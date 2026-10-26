"""The tallies: DA, sDA, ASE, EN 17037's F_plane, and LM-83's blind-trigger predicate.

Pure: no Radiance, no subprocesses, no file formats, no I/O. Runs green with no binaries
installed. Denominators (which columns are "in" a period, and how many) come from
`rad_core.periods` -- never a literal here, per R47.

**The comparison strictness is the entire content of this module**, and it is the phase's whole
point. Three standards, three different inclusive/exclusive rules on the same shape of number (an
hour count judged against a threshold), and mixing them up moves every published percentage:

- LM-83's sDA (ch04:38-40): illuminance `>= 300 lux`, hour count `>= 1 825` -- **both inclusive**.
  1 825 h is 50 % of the fixed 3 650 h/yr analysis window (10 h/day, 08:00-18:00 local clock).
- LM-83's ASE: illuminance `>= 1 000 lux`, **inclusive**, and hour count `> 250`, **strict**
  (R861). LM-83-12 defines direct sunlight as "1,000 lux or more" (section 2.2.6's definition,
  section 3.2.2 and the glossary's *Direct sunlight*); section 3.1.2's "more than 1000 lux" is the
  minority wording. The hours are "more than 250 hours" in section 3.1.2 and in section 3.1 of the
  ANSI/IES LM-83-22. A cell at exactly 1 000.0 lux counts; a point at
  exactly 250 hours does not. *(Corrected 2026-10-12, R861: this said "illuminance `> 1 000 lux`,
  hour count `> 250` -- **both strict**, unlike sDA's. A point at exactly 250 hours, or exactly
  1 000.0 lux, does not count.")*
- LM-83's blind trigger (ch04:91-100): `> 2 %` of analysis points is the general rule; the three
  small-area rows are its *stated exceptions* ("small-area corrections", ch04:92), not a fourth
  tier. Every comparison in the table is strict -- see `blinds_close`.
- UDI (R106): illuminance in the band `>= UDI_BAND_LOWER_LUX` **and** `< UDI_BAND_UPPER_LUX` --
  100 lux exactly is useful, 2 000 lux exactly is *exceeded*, so the two edges carry opposite
  operators and a band written with two `>=` or two `<` silently reclassifies both boundaries.
  **UDI has a second threshold, and it is what makes `spatial_udi` structurally an sDA and not a
  per-point average**: after the band, a point must be in band for `>= UDI_SPATIAL_TIME_FRACTION`
  (50 %) of the analysis hours before it counts toward the spatial figure. A grid in band for
  exactly 40 % of the hours meets the band and misses the time threshold, so its spatial UDI is
  `0.0` -- not 40, and not 100.

**Not every function here returns the same unit, and that split is deliberate.**
`daylight_autonomy`, `spatial_daylight_autonomy`, `annual_sunlight_exposure`,
`useful_daylight_illuminance` and `spatial_udi` return percentages (`0-100`, matching §14's
`unit: "%"`). `f_plane` returns an EN 17037 **fraction** (`0-1`, matching
`rad_core.compliance.en17037_provision`'s own `0.50`/`0.95` comparisons and docs/tool-surface.md's
own `f_plane_at_target: 0.61` example) -- **the only fraction in this module**, and see `f_plane`'s
own docstring for the measured consequence of getting this wrong. Do not "fix" the two to agree;
each already matches its own standard's published surface, and making them match each other would
break both. UDI joins the percentages rather than the fraction: a number of the right shape in the
wrong units is what passed seven green tests on the previous branch.

**Two scopes, one traversal.** `PointTally.observe(hour, lux)` updates whole-year statistics
(`count`, `total`, `minimum`, `maximum`, `negatives`) unconditionally, and updates the
period-scoped threshold counters only when `hour` falls inside the `window` the tally was
constructed with. A driver that pre-filtered its input to the period before calling `observe`
would report the wrong whole-year negative count and minimum -- the two indices (hour, lux) and
the two scopes (period-scoped, whole-year) are why `observe` takes both and `PointTally` keeps
both sets of counters.

**Negatives are counted, never clamped.** A negative lux is below every threshold this module
applies, so counting it unclamped moves no period-scoped tally -- clamping would only corrupt the
`mean` and `minimum` a whole-year summary publishes, turning them into numbers describing a matrix
nobody computed. `max(0, ...)` does not appear anywhere in this file, on purpose.

**An absent denominator raises. It does not answer.** `daylight_autonomy`, `spatial_daylight_autonomy`
and `f_plane` each divide "hours/points that qualified" by "hours/points there were to qualify
out of". When that denominator is zero -- an empty `window`/`retained`
(`rad_core.periods.lm83_window`'s own docstring names the 3-hour micro-scene as the case that
produces one) or an empty `tallies` -- there is no proportion to report, and returning `0.0` is not
a fix for that, only a quieter version of the same mistake this module's first review round found
when the fall-through instead produced `100.0`: **`0.0` is just as publishable a compliance figure
as `100.0` is.** A caller reading `sDA = 0 %` back cannot tell "the space failed completely" from
"the period contained no hours" or "there were no points to measure" -- and reporting the second as
the first is exactly the substituted-default-as-measurement `CONTEXT.md`'s reporting rules forbid
("never report a substituted default as a measured value"). So every empty-denominator case here
raises `ValueError`, naming which quantity was empty -- the third shape of refusal alongside
`_at_or_above`'s `KeyError` on an undeclared threshold and `_ensure_window_matches`'s
`ValueError` on a mismatched window, both below.

**A `window`/`retained` argument is verified, not trusted -- and checked before the denominator is
judged empty.** `PointTally`'s threshold counters are accumulated against the window it was built
with, not against whatever window a caller later passes to a metric function -- so
`spatial_daylight_autonomy`, `annual_sunlight_exposure` and `f_plane` all check every tally's own
`.window` against the `window`/`retained` argument first, and raise on a mismatch before ever
asking whether that argument is empty. Checking the mismatch second would let an empty
`window`/`retained` silently swallow a real mismatch -- a tally built against a completely
different, non-empty period would report "no hours" instead of "wrong window", which is a
different lie than the one being refused.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Final

from rad_core.lm83_edition import DEFAULT_LM83_EDITION, LM83_22, Lm83Edition
from rad_core.periods import LM83_DAYLIT_FRACTION

__all__ = [
    "LM83_SDA_THRESHOLD_LUX",
    "LM83_ASE_THRESHOLD_LUX",
    "UDI_BAND_LOWER_LUX",
    "UDI_BAND_UPPER_LUX",
    "UDI_SPATIAL_TIME_FRACTION",
    "LM83_ASE_THRESHOLD_HOURS",
    "LM83_BLIND_AREA_GENERAL_SF",
    "LM83_BLIND_AREA_MID_SF",
    "LM83_BLIND_AREA_SMALL_SF",
    "PointTally",
    "TallyResult",
    "daylight_autonomy",
    "sda_hours_required",
    "spatial_daylight_autonomy",
    "useful_daylight_illuminance",
    "udi_hours_required",
    "spatial_udi",
    "annual_sunlight_exposure",
    "f_plane",
    "blinds_close",
]

#: ch04:38-40 -- sDA's illuminance threshold, folded into `PointTally.observe`'s `>=` (inclusive)
#: convention and `spatial_daylight_autonomy`'s own default. Named so a caller wanting "the sDA
#: threshold this module uses" imports the one value its comparisons are checked against, rather
#: than restating `300.0` a second place with nothing pinning the two equal.
LM83_SDA_THRESHOLD_LUX: Final[float] = 300.0

#: ASE's illuminance threshold and LM-83's blind trigger's, LM-83-12 section 2.2.6's "1,000 lux or
#: more" (R861): read through `PointTally.observe`'s `>=` (inclusive) counter and
#: `annual_sunlight_exposure`'s own default. Same reason as `LM83_SDA_THRESHOLD_LUX` above.
LM83_ASE_THRESHOLD_LUX: Final[float] = 1000.0

#: R106 -- UDI's lower band edge. **Inclusive**: `PointTally.observe` counts a column when
#: `lower <= value`, so exactly 100 lux is useful daylight, not "just below useful".
UDI_BAND_LOWER_LUX: Final[float] = 100.0

#: R106 -- UDI's upper band edge. **Exclusive**, the opposite operator to the lower edge:
#: `observe` counts a column when `value < upper`, so exactly 2 000 lux is *exceeded*, not useful.
#: The two edges disagreeing on strictness is the whole content of the band, which is why they are
#: named separately here rather than left as a `(100.0, 2000.0)` literal at each call site.
UDI_BAND_UPPER_LUX: Final[float] = 2000.0

#: R106 -- UDI's **second** threshold, the one that makes `spatial_udi` an sDA rather than an
#: average: the fraction of the analysis period a point must be in band for before it counts
#: toward the spatial figure. Read only through `udi_hours_required`, so the number enforced and
#: the number published cannot drift -- `sda_hours_required`'s lesson, applied before it can
#: repeat here.
UDI_SPATIAL_TIME_FRACTION: Final[float] = 0.5

#: ch04:91-100 -- the three area edges of LM-83's blind-trigger table, in square feet. Named
#: because `blinds_close` is not the only reader: `rad_core.blinds` labels each schedule with the
#: band that decided it, and a second copy of 1 000/500/200 typed there could disagree with the
#: predicate's own branch with nothing pinning the two equal. **Every edge is inclusive at its own
#: lower bound**: exactly 1 000 sf takes the general 2 % rule, and 999.99 sf takes the row below --
#: which is the boundary `blinds_close`'s `>=` chain encodes and `tests/test_blinds.py`'s
#: `test_the_band_boundaries_themselves` pins.
#:
#: The point-count thresholds those rows compare against (`> 5`, `> 3`, `> 1`) and the general
#: rule's `> 0.02` are deliberately **not** named: they are read at exactly one site each, inside
#: `blinds_close`, and naming a number that is never imported adds a name without removing a
#: duplicate. The area edges are named because they genuinely have two readers.
#: ch04:52-54 -- ASE_1000,250h's **hour** threshold, the companion to `LM83_ASE_THRESHOLD_LUX` one
#: constant up. Named for the reason the lux threshold is: it was a bare `hours: int = 250` default
#: in `annual_sunlight_exposure`'s signature, beside a named lux constant, with **four** prose
#: restatements of the digits elsewhere (`rad_core/periods.py`, and three in
#: `rad_mcp/results_tool.py`) -- a number a reader has to trust five places agree about.
#:
#: **Absolute, and that is why it matters.** ASE counts hours against 250 and never against a
#: fraction of the period, so a matrix shorter than 251 hours can never reach it and
#: `ase_band(0.0)` returns `clearly_acceptable` -- a favourable verdict guaranteed by the period
#: rather than earned by the space, which is what `is_whole_year` exists to refuse.
LM83_ASE_THRESHOLD_HOURS: Final[int] = 250

LM83_BLIND_AREA_GENERAL_SF: Final[float] = 1000.0
LM83_BLIND_AREA_MID_SF: Final[float] = 500.0
LM83_BLIND_AREA_SMALL_SF: Final[float] = 200.0


@dataclass(frozen=True)
class TallyResult:
    """A snapshot of one point's accumulator.

    `hours_at_or_above` and `hours_above` are keyed by the thresholds the owning `PointTally` was
    constructed with, and counted only within its `window`. Everything else here is whole-year and
    unfiltered -- `count`, `total`, `mean`, `minimum`, `maximum`, `negatives` see every column
    `observe` was ever called with, regardless of the period.
    """

    count: int
    total: float
    mean: float | None
    """`None` when `count == 0` -- the same "no value was folded in" convention `minimum` and
    `maximum` already use. Substituting `0.0` for an empty mean was exactly the CONTEXT.md-forbidden
    pattern this module's own docstring names (an absent denominator raises; it does not answer with
    a number nobody measured) -- fixed at the whole-branch review, F4. No wire consumer existed to
    make this anything but a type widening: `grep -rn "\\.result\\(\\)\\." src/` finds no reader of
    `.mean`/`.minimum`/`.maximum` off a `TallyResult`."""
    minimum: float | None
    maximum: float | None
    negatives: int
    hours_at_or_above: dict[float, int]
    """Per declared threshold: count of window hours `>= threshold` -- sDA's convention (ch04:38-40)
    and, since R861, ASE's too (LM-83-12 section 2.2.6, "1,000 lux or more")."""
    hours_above: dict[float, int]
    """Per declared threshold: count of window hours `> threshold`. No LM-83 metric reads it since
    R861 moved ASE to `hours_at_or_above`; `results(action="points", threshold_lux=...)` publishes
    it as `hours_above_threshold`, a caller-set threshold documented as `>`."""
    hours_in_band: dict[tuple[float, float], int]
    """Per declared `(lower, upper)` band: count of window hours `>= lower AND < upper` (R106's UDI
    convention -- exactly `lower` is inside the band, exactly `upper` is outside it)."""


class PointTally:
    """One analysis point's accumulator across a year of matrix columns.

    `window` is the set of columns that count toward the period-scoped threshold counters --
    `rad_core.periods.lm83_window(...)` for LM-83's sDA/ASE, or a `DaylightHours.columns` for
    EN 17037's F_plane/F_time. `observe` filters against it, so the same class serves either
    period; a caller building both an LM-83 tally and an EN 17037 tally for the same point
    constructs two `PointTally` instances, one per window.

    `thresholds` are the illuminance levels this tally tracks, declared up front so `observe` can
    maintain running counters in one pass rather than storing a year of raw values per point. A
    threshold not given at construction cannot be queried later: `daylight_autonomy`,
    `spatial_daylight_autonomy`, `annual_sunlight_exposure` and `f_plane` all raise `KeyError` if
    asked for one that was not declared.

    `bands` are `(lower, upper)` UDI-style ranges, declared for the same reason as `thresholds` --
    so `observe` can maintain a running band counter in the same one-pass traversal rather than
    storing a year of raw values per point. A band not given at construction cannot be queried
    later, the same posture as an undeclared threshold. The band counter is maintained directly by
    its own loop in `observe`, never derived from the threshold counters by subtraction: a
    subtraction would be correct only under today's exact `>=`/`<` operators, which makes it a fact
    about this implementation rather than a definition of the band.
    """

    def __init__(
        self,
        window: frozenset[int],
        thresholds: Iterable[float] = (),
        bands: Iterable[tuple[float, float]] = (),
    ) -> None:
        self._window = frozenset(window)
        self._thresholds: tuple[float, ...] = tuple(dict.fromkeys(float(t) for t in thresholds))
        self._hours_at_or_above: dict[float, int] = {t: 0 for t in self._thresholds}
        self._hours_above: dict[float, int] = {t: 0 for t in self._thresholds}
        self._bands: tuple[tuple[float, float], ...] = tuple(
            dict.fromkeys((float(lo), float(hi)) for lo, hi in bands)
        )
        self._hours_in_band: dict[tuple[float, float], int] = {b: 0 for b in self._bands}
        self._count = 0
        self._total = 0.0
        self._negatives = 0
        self._minimum: float | None = None
        self._maximum: float | None = None

    @property
    def window(self) -> frozenset[int]:
        return self._window

    def observe(self, hour: int, lux: float) -> None:
        """Fold one column's reading in.

        Updates the whole-year statistics unconditionally. Updates the period-scoped threshold
        counters only when `hour` is in `window`. Never clamps: a negative `lux` is recorded as
        given, counted in `negatives`, and eligible to set `minimum`.

        **Not idempotent per hour.** Each call adds one more observation regardless of whether
        `hour` was already seen -- calling this twice for the same column double-counts it in
        every counter it touches. A driver owes this class exactly one `observe` call per column
        per point; nothing here detects or rejects a repeat.

        **Refuses a non-finite `lux`** -- `nan`, `+inf` and `-inf` alike (defect 2b, ruling
        R255-10). `_UDI_BINS` spans `[-inf, +inf)`, so every value this method accepts lands in
        exactly one band and the three UDI percentages sum to the window by construction, which is
        what `docs/tool-surface.md` and `UdiPointView` already publish about them. A `nan` or a
        `+inf` cell satisfies no `lower <= value < upper`, so every band counter stayed at zero
        while the denominator stayed a real 2 600 hours: three measured-looking percentages
        summing to 0.0 for a point nothing was measured at, and -- for `+inf`, which *does*
        satisfy every threshold -- those three zeros published beside a `da_300` of 100.0 in the
        same payload. The failure is continuous, not a corner: half `nan` and half 500 lux
        measured `[0.0, 50.0, 0.0]`, sum 50.0.

        **`-inf` is refused too, and it used to band.** `-inf <= -inf < 100.0` is True, so it
        counted in the lowest bin and the sum survived. Refused anyway: `-inf` is not an
        illuminance and `rad_engine.results` already refuses the whole matrix for it; the lowest
        bin's `-inf` **edge** exists for the negative *finite* cells the five-phase combination
        subtracts into being (2 336-3 113 in the runs recorded so far), not for the infinity itself; and banding
        `-inf` while refusing `+inf` and `nan` would be two arithmetic paths through one
        predicate, which ruling R30 forbids.

        **A second guard, deliberately.** `rad_engine.results` already refuses a non-finite cell
        before any row is yielded -- a non-finite *token* at `_parse_row`/`_parse_lux_row`, and a
        lux value that overflows in conversion at `_stream_lux_from`, which is the second of the
        two and was added in fix round 2 of Phase 7d-2 (`to_lux` multiplies by 179 lm/W, so three
        finite components can convert to an infinity that the token check cannot see). All of
        `results`' tally call sites read through those readers -- so no wire payload changes
        today. This settles the
        question at the source rather than depending on that reader for reachability, the same
        posture `rad_core.blinds` takes on the same kind of matrix cell. A bare `ValueError`, like
        this module's empty-denominator guards: `rad_mcp` shapes it, `rad_core` stays pure.

        **Above every counter**, so a refused value cannot poison `total`, `mean`, `minimum` or
        `maximum` on its way out -- `min(nan, x)` is `nan`, and one admitted cell would make the
        whole year's statistics unreadable as well as emptying the bins.
        """
        value = float(lux)
        if not math.isfinite(value):
            raise ValueError(
                f"PointTally.observe was given {value!r} for hour {hour}, which is not a finite "
                "illuminance. Every comparison against a band edge is False for nan and for "
                "+inf, so the UDI bins would each stay at zero against a real denominator and "
                "publish three percentages summing to 0 for a point nothing was measured at; "
                "+inf satisfies every threshold at the same time, so the same payload would "
                "carry a full daylight autonomy beside them. -inf is refused with them rather "
                "than banded, because one predicate is one arithmetic path. Refuse the matrix "
                "that produced this cell and re-run the chain behind it."
            )
        self._count += 1
        self._total += value
        if value < 0:
            self._negatives += 1
        self._minimum = value if self._minimum is None else min(self._minimum, value)
        self._maximum = value if self._maximum is None else max(self._maximum, value)

        if hour in self._window:
            for t in self._thresholds:
                if value >= t:  # sDA's and (R861) ASE's inclusive convention
                    self._hours_at_or_above[t] += 1
                if value > t:  # strict; read by `results(action="points")` only
                    self._hours_above[t] += 1
            for lower, upper in self._bands:
                if lower <= value < upper:   # R106: `>= lower`, `< upper`. Exactly `lower` is inside;
                    self._hours_in_band[(lower, upper)] += 1   # exactly `upper` is outside.

    def result(self) -> TallyResult:
        count = self._count
        return TallyResult(
            count=count,
            total=self._total,
            mean=(self._total / count) if count else None,
            minimum=self._minimum,
            maximum=self._maximum,
            negatives=self._negatives,
            hours_at_or_above=dict(self._hours_at_or_above),
            hours_above=dict(self._hours_above),
            hours_in_band=dict(self._hours_in_band),
        )


def _at_or_above(tally: PointTally, threshold_lux: float) -> int:
    hours = tally.result().hours_at_or_above
    if threshold_lux not in hours:
        raise KeyError(
            f"PointTally was not constructed to track threshold {threshold_lux!r} -- declared "
            f"thresholds are {sorted(hours)}. Thresholds are fixed at construction; a caller "
            "wanting a different one must build a new PointTally with it declared."
        )
    return hours[threshold_lux]


def _ensure_window_matches(tallies: Sequence[PointTally], window: frozenset[int], *, caller: str) -> None:
    """Raise `ValueError` if any `tally.window` differs from `window`.

    A `PointTally`'s threshold counters are accumulated only against the window it was built with
    (`observe`'s doc) -- a `window`/`retained` argument that disagrees with that does not change
    what was counted, only what `caller` reports it over, which is silent and wrong rather than
    loud and wrong. `f_plane` first carried this check inline; `spatial_daylight_autonomy` and
    `annual_sunlight_exposure` now share it rather than each repeating (or, as before that fix,
    omitting) their own copy. Every caller checks this *before* asking whether `window` is empty --
    see the module docstring's ordering note -- so a genuine mismatch is reported as a mismatch
    even when `window` also happens to be empty.
    """
    expected = frozenset(window)
    for tally in tallies:
        if tally.window != expected:
            raise ValueError(
                f"{caller} received a PointTally whose window does not match the `window` "
                "argument -- every tally passed here must be built against the same period, or "
                "the percentage reported divides by the wrong denominator."
            )


def daylight_autonomy(tally: PointTally, *, threshold_lux: float, window: frozenset[int]) -> float:
    """DA_threshold for one point: percent of `window`'s hours at or above `threshold_lux`
    (ch04:38-40's inclusive convention -- the per-point quantity sDA aggregates across points, and
    reads the same threshold `spatial_daylight_autonomy` does).

    Raises `ValueError` on an empty `window` (`rad_core.periods.lm83_window`'s own micro-scene
    case) rather than returning `0.0` -- see the module docstring's "absent denominator" note.
    `0.0` would be exactly as publishable and exactly as wrong as the `100.0` a vacuous comparison
    would give: a caller cannot tell "zero daylit hours out of a real period" from "no period to
    have been daylit over at all". This function wrote that guard in this same task, so it is not
    inherited behaviour to preserve -- it changes here along with the other two.
    """
    n = len(window)
    if n == 0:
        raise ValueError(
            "daylight_autonomy's window contains no hours -- the analysis period has no columns "
            "to compute a percentage over, so there is no denominator and therefore no proportion "
            "to report."
        )
    return 100.0 * _at_or_above(tally, threshold_lux) / n


def sda_hours_required(
    window: frozenset[int], *, fraction: float = LM83_DAYLIT_FRACTION
) -> float:
    """The exact hour count `spatial_daylight_autonomy` compares each point's tally against:
    `fraction * len(window)`, **untruncated** (ch04:38-40 -- 1 825 h is 50 % of LM-83's 3 650 h
    window, and the comparison below is `>=`, inclusive).

    **One definition, two readers -- and that is the entire reason this function exists.**
    `spatial_daylight_autonomy` enforces this number and `rad_mcp.results_tool` publishes it as
    `analysis.hours_required`; until this function existed, each computed `fraction * len(window)`
    itself. They agreed only because both operands happened to be passed explicitly at the one
    call site, which is the shape that produced two of this branch's own defects (a bare `0.5`
    default that merely matched `LM83_DAYLIT_FRACTION`, and an `int()` that truncated the
    published number a whole hour below the enforced one on an odd window). A published
    requirement that is not the enforced requirement is a false statement about the verdict beside
    it, so there is now nothing left to drift.

    Returns a `float` even for a whole-number result -- `0.5 * 3650` is `1825.0`, not `1825` --
    because the comparison is against the real number and a caller publishing `int()` of it
    understates the requirement by up to 1 whenever `len(window)` is odd.
    """
    return fraction * len(window)


def spatial_daylight_autonomy(
    tallies: Sequence[PointTally],
    *,
    threshold_lux: float = LM83_SDA_THRESHOLD_LUX,
    window: frozenset[int],
    fraction: float = LM83_DAYLIT_FRACTION,
) -> float:
    """sDA_300/50%: percent of points meeting `threshold_lux` for at least `fraction` of `window`
    (ch04:38-40). 1 825 h is 50 % of LM-83's 3 650 h window, and **both** comparisons here are
    inclusive: `>=` on the illuminance (folded into the tally's `hours_at_or_above`, `observe`'s
    doc) and `>=` on the hour count below. One hour short, or one lux short, does not count -- but
    landing exactly on either boundary does.

    The hour count each tally is compared against is `sda_hours_required(window, fraction=...)`,
    which `rad_mcp.results_tool` also publishes as `analysis.hours_required` -- one definition, so
    the number enforced here and the number published beside the verdict cannot drift apart.

    Raises `ValueError` on an empty `tallies` (no analysis points) or an empty `window` (no hours
    in the analysis period) -- see the module docstring's "absent denominator" note. Without this,
    `sda_hours_required(window)` would be `0.0`, and every tally's `hours_at_or_above` count
    -- being `>= 0` always -- would vacuously satisfy `>= required`, which was this module's
    Critical defect in the first review round; returning `0.0` instead would only trade that
    publishable false-100 % for an equally publishable false-0 %.

    Every `tally` must also have been built against this same `window` -- `_ensure_window_matches`
    is checked *before* the empty-`window` guard, so a tally built against a genuinely different,
    non-empty period is still reported as a mismatch rather than as "no hours".
    """
    if not tallies:
        raise ValueError(
            "spatial_daylight_autonomy received no tallies -- there is no analysis area to report "
            "a percentage of, so there is no denominator and therefore no proportion to report."
        )
    _ensure_window_matches(tallies, window, caller="spatial_daylight_autonomy")
    if len(window) == 0:
        raise ValueError(
            "spatial_daylight_autonomy's window contains no hours -- the analysis period has no "
            "columns, so there is no denominator and therefore no percentage to report."
        )
    required = sda_hours_required(window, fraction=fraction)
    qualifying = sum(1 for tally in tallies if _at_or_above(tally, threshold_lux) >= required)
    return 100.0 * qualifying / len(tallies)


def useful_daylight_illuminance(
    tally: PointTally, *, band: tuple[float, float], window: frozenset[int]
) -> float:
    """UDI for one point: **percent** of `window`'s hours inside `band` (R106 -- `>= lower`,
    `< upper`, so exactly 100 lux is useful and exactly 2 000 lux is exceeded).

    A percentage (`0-100`), not a fraction, for the same reason `daylight_autonomy` is one: it is
    the per-point quantity `spatial_udi` aggregates, and `f_plane` is the only fraction this module
    publishes -- see the module docstring's unit paragraph. `f_plane`'s own docstring records what a
    metric returning the wrong one of the two costs.

    The band count comes from the tally's own `hours_in_band`, which `observe` maintains directly
    inside its window filter -- it is never re-derived here from the threshold counters, because a
    subtraction would be a fact about today's operators rather than a definition of the band.

    Raises `ValueError` on an empty `window` rather than returning `0.0`, the same guard and the
    same wording `daylight_autonomy` carries -- see the module docstring's "absent denominator"
    note. `0.0` is exactly as publishable a UDI figure as `100.0`, and a caller cannot tell "in
    band for none of a real period" from "no period at all".
    """
    n = len(window)
    if n == 0:
        raise ValueError(
            "useful_daylight_illuminance's window contains no hours -- the analysis period has no "
            "columns to compute a percentage over, so there is no denominator and therefore no "
            "proportion to report."
        )
    return 100.0 * tally.result().hours_in_band[band] / n


def udi_hours_required(
    window: frozenset[int], *, fraction: float = UDI_SPATIAL_TIME_FRACTION
) -> float:
    """The exact hour count `spatial_udi` compares each point's band count against:
    `fraction * len(window)`, **untruncated**. The comparison there is `>=`, inclusive, the same
    convention sDA states explicitly for its own hour count.

    **One definition, two readers**, exactly as `sda_hours_required` is -- and that function's
    docstring records why: a published requirement that is not the enforced requirement is a false
    statement about the verdict printed beside it. `spatial_udi` enforces this number and Task 18's
    wire layer publishes it; neither restates the arithmetic.

    Returns a `float` even for a whole-number result -- `0.5 * 2600` is `1300.0`, not `1300` --
    because an `int()` here would understate the requirement by up to 1 whenever `len(window)` is
    odd, which is the precise defect that reached `sda_hours_required`'s published number.
    """
    return fraction * len(window)


def spatial_udi(
    tallies: Sequence[PointTally],
    *,
    band: tuple[float, float],
    window: frozenset[int],
    fraction: float = UDI_SPATIAL_TIME_FRACTION,
) -> float:
    """Spatial UDI: percent of points inside `band` for at least `fraction` of `window` (R106).

    **Two thresholds, structurally an sDA and not a per-point average.** The band is the first;
    `udi_hours_required(window, fraction=...)` is the second, and dropping it is what turns this
    function into the mean per-point percentage -- a real defect shape, and R103's in a new place.
    A grid whose every point is in band for exactly 40 % of the hours meets the band and misses the
    time threshold, so this returns `0.0`, not `40.0` and not `100.0`.

    Raises `ValueError` on an empty `tallies` (no analysis points) or an empty `window` (no hours
    in the analysis period) -- see the module docstring's "absent denominator" note. Without the
    second, `udi_hours_required(window)` would be `0.0` and every tally's band count -- being
    `>= 0` always -- would vacuously qualify, the same vacuous-100 % this module's first review
    round found in sDA.

    Every `tally` must also have been built against this same `window`, and
    `_ensure_window_matches` is checked *before* the empty-`window` guard -- the module docstring's
    ordering rule -- so a tally built against a genuinely different, non-empty period is reported
    as a mismatch rather than as "no hours".
    """
    if not tallies:
        raise ValueError(
            "spatial_udi received no tallies -- there is no analysis area to report a percentage "
            "of, so there is no denominator and therefore no proportion to report."
        )
    _ensure_window_matches(tallies, window, caller="spatial_udi")
    if len(window) == 0:
        raise ValueError(
            "spatial_udi's window contains no hours -- the analysis period has no columns, so "
            "there is no denominator and therefore no percentage to report."
        )
    required = udi_hours_required(window, fraction=fraction)
    qualifying = sum(1 for tally in tallies if tally.result().hours_in_band[band] >= required)
    return 100.0 * qualifying / len(tallies)


def annual_sunlight_exposure(
    tallies: Sequence[PointTally],
    *,
    threshold_lux: float = LM83_ASE_THRESHOLD_LUX,
    window: frozenset[int],
    hours: int = LM83_ASE_THRESHOLD_HOURS,
) -> float:
    """ASE_1000,250h: percent of points receiving direct sunlight -- `threshold_lux` or more -- for
    more than `hours` of `window` (R861). `>=` on the illuminance, read from the tally's
    `hours_at_or_above` (`observe`'s doc): LM-83-12 defines direct sunlight as "1,000 lux or more"
    in section 2.2.6's definition, section 3.2.2 and the glossary, and section 3.1.2's "more than
    1000 lux" is the minority wording. `>` on the hour count below: "more than 250 hours" in
    section 3.1.2 and in section 3.1 of ANSI/IES LM-83-22. A cell at
    exactly `threshold_lux` lux counts; a point at exactly `hours` hours does not.
    *(Corrected 2026-10-12, R861: this said "**Both** comparisons are strict, unlike sDA's: `>` on
    the illuminance (folded into the tally's `hours_above`, `observe`'s doc) and `>` on the hour
    count below. A point at exactly `hours` hours, or exactly `threshold_lux` lux, does not
    count.")*

    Raises `ValueError` on an empty `tallies` (no analysis points): "percent of zero points" has no
    more of a well-defined answer than "percent of zero hours" does -- see the module docstring's
    "absent denominator" note, applied here to the point count rather than the hour count.

    `window` bounds no arithmetic here -- ASE's own comparison is against the fixed `hours`
    threshold, not against the period's length, so an *empty* `window` is not itself a defect the
    way it is for `spatial_daylight_autonomy` (every tally's `hours_at_or_above` count would
    correctly come back `0`, and `0 > hours` is false, giving `0.0` -- the true answer, not a
    substituted one). Every `tally` must still have been built with the `window` given:
    `_ensure_window_matches` raises `ValueError` on a mismatch, since a `window` argument that
    disagrees with what a tally actually accumulated against would otherwise be accepted and
    silently ignored.
    """
    if not tallies:
        raise ValueError(
            "annual_sunlight_exposure received no tallies -- there is no analysis area to report "
            "a percentage of, so there is no denominator and therefore no proportion to report."
        )
    _ensure_window_matches(tallies, window, caller="annual_sunlight_exposure")
    qualifying = sum(1 for tally in tallies if _at_or_above(tally, threshold_lux) > hours)
    return 100.0 * qualifying / len(tallies)


def f_plane(
    tallies: Sequence[PointTally],
    *,
    target_lux: float,
    retained: frozenset[int],
    required: int,
) -> float:
    """EN 17037's F_plane: the **fraction** (0-1, not a percentage) of the reference plane's
    points meeting `target_lux` for at least `required` of the `retained` daylight hours
    (ch03:15-16, "a target illuminance is met ... for at least half the daylight hours" --
    inclusive, the same "at least" convention sDA states explicitly for its own hour count).

    **A fraction, deliberately, not a percentage -- the one place this module's comparisons split
    by which standard is asking.** `daylight_autonomy`, `spatial_daylight_autonomy` and
    `annual_sunlight_exposure` above all return `100.0 * ...`: those are LM-83 figures, and §14 of
    docs/tool-surface.md publishes every one of them with `unit: "%"`. EN 17037's own Table A.1/A.2
    are written in 0-1 fractions (`_TABLE_A1`/`_TABLE_A2` in `rad_core.compliance`: `0.50`, `0.95`),
    `rad_core.compliance.en17037_provision` compares its two inputs against exactly those fractions
    and documents them as "fraction of the reference plane (0-1)", and
    the worked example in docs/tool-surface.md#14-results publishes `f_plane_at_target: 0.61` -- a
    fraction, not `61.0`. Before this fix, `f_plane` returned `100.0 * qualifying / len(tallies)`
    and `en17037_provision` compared that against `0.50`: a grid where 2 of 100 points met the
    target (a real 2 % fraction) published `f_target = 2.0`, which is `>= 0.50` -- **every level,
    "met" by a grid that missed nearly all of it.** Do not "harmonise" this back to a percentage;
    the split is principled, not an oversight -- each metric's unit matches its own standard and
    the surface already publishes.

    Raises `ValueError` on an empty `tallies` (no analysis points) or an empty `retained` (no
    daylight hours were ranked at all) -- see the module docstring's "absent denominator" note. A
    caller-supplied `required` derived from an empty `daylight_hours(...)` can itself be `0`, and
    'met the target for >= 0 of 0 hours' is vacuously true; returning `0.0` here would only trade
    that same false certainty for its mirror image.

    Every `tally` must have been built with `window == retained` -- `_ensure_window_matches` is
    checked *before* the empty-`retained` guard, so a `PointTally` built against LM-83's window and
    handed to `f_plane` by mistake is still reported as a mismatch rather than as "no hours",
    even when `retained` itself happens to be empty.
    """
    if not tallies:
        raise ValueError(
            "f_plane received no tallies -- there is no reference plane to report a fraction of "
            "points over, so there is no denominator and therefore no proportion to report."
        )
    _ensure_window_matches(tallies, retained, caller="f_plane")
    if len(retained) == 0:
        raise ValueError(
            "f_plane's retained daylight-hours window contains no hours -- there is no "
            "denominator and therefore no proportion of the plane to report."
        )
    qualifying = sum(1 for tally in tallies if _at_or_above(tally, target_lux) >= required)
    return qualifying / len(tallies)


def _more_than_two_percent_in_sun(points_in_sun: int, points_total: int) -> bool:
    """The one "> 2 % of analysis points" predicate: LM-83-12 section 2.2.6's general row and
    LM-83-22 section 4.6's rule at every area both call it, so `rad_core.blinds.same_closing_rule`
    equating the two labels is true by construction, not by two expressions that agree. Strict:
    exactly 2 % stays open. No points, no closure."""
    return points_total > 0 and (points_in_sun / points_total) > 0.02


def blinds_close(
    *,
    points_in_sun: int,
    points_total: int,
    area_sf: float,
    edition: Lm83Edition = DEFAULT_LM83_EDITION,
) -> bool:
    """The LM-83 blind trigger under `edition`.

    **Under LM-83-22** (R864) the blind closes when more than 2 % of the analysis points receive
    direct sunlight, at every area: LM-83-22 section 4.6 asks that "no more than 2% of the analysis
    points" do so "at every hour", and prints no small-area rows. `area_sf` decides nothing under
    it. Exactly 2 % stays open, as under the general rule below.

    **Under LM-83-12, the default**, LM-83-12 section 2.2.6's trigger.

    **> 2 % of analysis points is the general rule.** The three point-count rows below are its
    *stated exceptions* for small areas, not a fourth tier -- ch04:93 is explicit, and a 2 ft grid
    under-samples a small room, which is why they exist:

    | area            | closes when                    |
    |-----------------|--------------------------------|
    | >= 1 000 sf     | > 2 % of points (general rule) |
    | 500-999 sf      | > 5 points                     |
    | 200-499 sf      | > 3 points                     |
    | < 200 sf        | > 1 point                      |

    Every comparison is strict. The off-by-one is the point of the exceptions.

    The three area edges are `LM83_BLIND_AREA_GENERAL_SF`, `LM83_BLIND_AREA_MID_SF` and
    `LM83_BLIND_AREA_SMALL_SF`, read from there rather than typed here so `rad_core.blinds` can
    label a schedule with the band that decided it without restating them.
    """
    if edition == LM83_22:
        # R864: LM-83-22 section 4.6 -- "no more than 2% of the analysis points ... at every hour";
        # no small-area rows.
        return _more_than_two_percent_in_sun(points_in_sun, points_total)
    if area_sf >= LM83_BLIND_AREA_GENERAL_SF:
        return _more_than_two_percent_in_sun(points_in_sun, points_total)
    if area_sf >= LM83_BLIND_AREA_MID_SF:
        return points_in_sun > 5
    if area_sf >= LM83_BLIND_AREA_SMALL_SF:
        return points_in_sun > 3
    return points_in_sun > 1
