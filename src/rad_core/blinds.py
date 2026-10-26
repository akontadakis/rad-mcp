"""LM-83 section 2.2.6's hourly blind schedule, and LM-83-22 section 4.6's under that edition.

Pure: no Radiance binaries, no subprocesses, no file IO -- see CONTEXT.md, Layers. Runs green
with no binaries installed.

**This module re-implements no part of the trigger rule.** `rad_core.metrics.blinds_close` decides
whether one hour's in-sun count closes the blind; everything here applies that predicate once per
hour of the analysis window and reports what decided each one -- the band, the column order, the
count of closed hours, and the illuminance threshold the caller used to call a point "in sun".
The four area edges come from `metrics`' own `LM83_BLIND_AREA_*` constants, never a second copy.

**`derive_schedule` takes counts, not a matrix.** The trigger needs a per-*hour* count while
`rad_engine.results`' reader yields per-*sensor* rows, and a materialised annual matrix is ~607 MB
at the 456-point office grid and tens of gigabytes at `GRID_VERY_LARGE` -- the exact thing that
streaming reader exists to prevent. The caller accumulates one integer per column as it streams and
hands *that* across this seam. **`group_schedule` cannot take the same shortcut** (see its own
docstring): closing one group changes which points are lit, so the per-group scan needs the
per-point rows themselves, one `(group -> row)` mapping per column, streamed a column at a time
rather than pre-summed into a count.

**The in-sun test is `>=`, and the threshold is the caller's to name** (R861). LM-83-12 section
2.2.6 defines direct sunlight as "1,000 lux or more", which is also the illuminance ASE counts at
(`rad_core.metrics.annual_sunlight_exposure`), so a point at exactly 1 000.0 lux is in sun and the
trigger and ASE agree at the threshold they share. `threshold_lux` is a **required keyword with no
default**: the caller names it and it is carried on the result. Callers import
`rad_core.metrics.LM83_ASE_THRESHOLD_LUX` rather than retyping it. *(Corrected 2026-10-12, R861:
this said "**The in-sun test is strict, and it is the caller's to label.** LM-83 never states how
much light counts as "receiving direct sunlight"" and that ASE's comparison is "`> 1 000 lux`,
strict"; LM-83-12 section 2.2.6 states the threshold, as the definition of direct sunlight.)*

**A missing column is a refusal, not a zero** -- and an empty window raises. Both are the same
rule: a favourable state ("open", or a schedule in which the blinds never closed) must never be the
by-product of an hour nothing was measured for. `threshold_lux` is held to the same rule and
validated **here** rather than at the caller: see `derive_schedule` for why
(ruling R115, docs/plan.md).
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Callable, Final, Mapping, Sequence

from rad_core.lm83_edition import DEFAULT_LM83_EDITION, LM83_22, Lm83Edition
from rad_core.metrics import (
    LM83_BLIND_AREA_GENERAL_SF,
    LM83_BLIND_AREA_MID_SF,
    LM83_BLIND_AREA_SMALL_SF,
    blinds_close,
)

__all__ = [
    "AUTOMATED_SCHEDULE_HEADER",
    "AUTOMATED_SCHEDULE_HOURS",
    "BAND_FLAT",
    "BLIND_CLOSED",
    "BLIND_OPEN",
    "BLIND_STATES",
    "CLOSING_ROLES",
    "LM83_BLIND_ANALYSIS_AREA_MAX_SF",
    "MAX_AUTOMATED_SCHEDULE_BYTES",
    "AutomatedScheduleError",
    "AutomatedScheduleRows",
    "BlindSchedule",
    "ClosingOrder",
    "GroupFacts",
    "GroupSchedule",
    "blend_row",
    "closing_order",
    "derive_schedule",
    "group_schedule",
    "parse_automated_schedule",
    "same_closing_rule",
]

#: The two blind states this module writes, and the whole vocabulary of `BlindSchedule.states` and
#: `GroupSchedule.states`.
#:
#: **Named here because a reader needs the set and a writer needs the members**, and the two must
#: not be separate statements of one vocabulary. `rad_engine.schedule` seals these strings into
#: `sched:<grid>` and `rad_mcp.results_tool` reads that file back off a manifest path -- untrusted
#: on every action that touches it (ruling R50) -- so it validates each state against `BLIND_STATES`
#: rather than against a third copy of the two words. A state outside this set reaching a client
#: reads as **not** `"closed"`, which is the favourable direction: an unrecognised state
#: distributed over window groups leaves a blind open that the run closed.
BLIND_OPEN: Final[str] = "open"
BLIND_CLOSED: Final[str] = "closed"
BLIND_STATES: Final[frozenset[str]] = frozenset({BLIND_OPEN, BLIND_CLOSED})


#: LM-83 section 2.2.6's DISCUSSION advises a closing order -- view groups first, clerestory last --
#: and gives no way to tell the two apart from geometry. It is a declaration, so it is an argument:
#: `rad_mcp.window_group_tool.window_group`'s `closing_role` argument on `action="define"`, stored
#: on `InputRecord.role`, the field that already carries `"ground"` for materials.
#:
#: **Lives here, not in `window_group_tool.py` (ruling R135), because Task 2's `closing_order` needs
#: it too and this module may not import from `rad_mcp`.** `closing_order` (`rad_core/blinds.py`,
#: Task 2) ranks a group by its declared class with a `_ROLE_RANK` table keyed on these same two
#: strings; a second, hand-typed `{"view": 0, "clerestory": 1}` one layer down from a copy
#: `window_group_tool` owned would be the same two-definitions-that-agree-until-one-moves trap this
#: module's own `_BAND_*` labels are built to avoid, one paragraph below. `rad_mcp`, which may
#: import `rad_core`, imports this constant rather than retyping it.
#:
#: Named `closing_role` on the wire it is validated against, **not** `role`: `window_group` already
#: has a published, owner-ruled `role` argument (`assign_bsdf`'s `BsdfRole`, ruling R63,
#: docs/tool-surface.md#6-window_group) -- one MCP function, one name, and it is already spoken for.
#: A bare `role` there would sit in the same signature as `states` (which are BSDF states) and the
#: next reader would take it for a `BsdfRole` rather than for LM-83's view/clerestory vocabulary,
#: which is a different scheme on the same name -- exactly what ruling R30 exists to prevent. Do not
#: "fix" this back to `role`: the two are deliberately not the same argument, and unioning them onto
#: one name was considered and rejected for the identical reason.
CLOSING_ROLES: Final[frozenset[str]] = frozenset({"view", "clerestory"})

#: The DISCUSSION's own two classes, in the order it closes them (ch04 section 2.2.6 DISCUSSION:
#: view groups first, clerestory last). The rank is the index into that reading.
#:
#: **Built from `CLOSING_ROLES`, checked against it, and never allowed to say something different.**
#: A role with no rank here, or a rank for a role not in `CLOSING_ROLES`, is the "two schemes side
#: by side" defect ruling R30 exists to prevent -- the same trap `CLOSING_ROLES`' own docstring
#: names for `_BAND_*` two paragraphs below. `tests/test_blinds.py::TestTheClosingOrder` pins
#: `set(_ROLE_RANK) == CLOSING_ROLES` directly so the two cannot drift apart silently.
_ROLE_RANK: Final[dict[str, int]] = {"view": 0, "clerestory": 1}
assert set(_ROLE_RANK) == CLOSING_ROLES, (
    "_ROLE_RANK must rank exactly the roles CLOSING_ROLES names -- see the docstring above"
)

#: ch04:105 -- *"Analysis areas for blind scheduling are grouped by facade orientation and never
#: exceed 10 000 sf."* The **upper** bound on the region section 2.2.6's table is defined over, as
#: against `LM83_BLIND_AREA_*` above, which are the edges *within* it.
#:
#: **Not a refusal, and not enforced here.** `derive_schedule` answers over whatever area it is
#: given: the arithmetic is correct for the region described, and what is missing is the caller's
#: split by facade orientation, which no amount of reasoning over one grid can perform. So this is
#: a labelling threshold that `rad_mcp` attaches an advisory from, and it lives here for the reason
#: every other threshold does -- `rad_mcp` carries none (CONTEXT.md, Layers).
#:
#: **It became load-bearing on this branch, and that is why it exists now and did not before.**
#: While nothing consumed an analysis area, `docs/tool-surface.md` recording the cap among rules
#: *"the server states rather than enforces"* was honest. `results(action="blind_schedule")`
#: computes `area_sf` and **selects a section 2.2.6 band from it**, so a 40 000 sf floor plate now
#: takes the `>=1000sf` row and the schedule's own advisory asserts applicability, in prose, for an
#: area section 2.2.6 defines no schedule over. The earlier "downgraded on measurement" call on
#: this item is reversed -- by a change in the code, not a change of mind.
LM83_BLIND_ANALYSIS_AREA_MAX_SF: Final[float] = 10000.0


def _whole_sf(value: float) -> str:
    return f"{value:.0f}"


#: The four labels of LM-83's blind-trigger table (ch04:97-100), *formatted from the same edges
#: `blinds_close` branches on* rather than typed out -- a hand-typed "500-999sf" beside an imported
#: `LM83_BLIND_AREA_MID_SF` is two definitions that agree until one of them moves.
#:
#: The table's wording names the whole square foot below the next edge ("500-999"), so that figure
#: is computed as `edge - 1`. The band it labels is really the half-open interval
#: `[500, 1 000)`: 999.99 sf is in the "500-999sf" row, because every edge is inclusive at its own
#: lower bound. The label quotes the standard; the interval is what is enforced.
_BAND_GENERAL: Final[str] = f">={_whole_sf(LM83_BLIND_AREA_GENERAL_SF)}sf"
_BAND_MID: Final[str] = (
    f"{_whole_sf(LM83_BLIND_AREA_MID_SF)}-{_whole_sf(LM83_BLIND_AREA_GENERAL_SF - 1)}sf"
)
_BAND_SMALL: Final[str] = (
    f"{_whole_sf(LM83_BLIND_AREA_SMALL_SF)}-{_whole_sf(LM83_BLIND_AREA_MID_SF - 1)}sf"
)
_BAND_BELOW_SMALL: Final[str] = f"<{_whole_sf(LM83_BLIND_AREA_SMALL_SF)}sf"

#: LM-83-22 section 4.6's one rule -- more than 2 % of the analysis points in direct sunlight closes
#: the blind, at every area (R864). **A label this server chose, not a string the standard prints**:
#: LM-83-22 has no area table, so there is no row name to quote.
BAND_FLAT: Final[str] = "flat_2pct"


def _area_band(area_sf: float, *, edition: Lm83Edition = DEFAULT_LM83_EDITION) -> str:
    """Which rule of `edition`'s blind trigger `area_sf` falls under, as a label.

    **This decides nothing.** It selects the name of the rule `blinds_close` will take, off the
    same constants and in the same order, so a published schedule can say which rule produced it.
    The predicate is still the only thing that reads a count. Under LM-83-22 there is one rule at
    every area, `BAND_FLAT`; under LM-83-12, the default, it is the row of the four-row table.
    """
    if edition == LM83_22:
        return BAND_FLAT
    if area_sf >= LM83_BLIND_AREA_GENERAL_SF:
        return _BAND_GENERAL
    if area_sf >= LM83_BLIND_AREA_MID_SF:
        return _BAND_MID
    if area_sf >= LM83_BLIND_AREA_SMALL_SF:
        return _BAND_SMALL
    return _BAND_BELOW_SMALL


def same_closing_rule(band_a: str, band_b: str) -> bool:
    """Whether two band labels name the same closing rule.

    LM-83-12's `>=1000sf` row and LM-83-22's `BAND_FLAT` are one predicate under two names --
    more than 2 % of the analysis points, with no point-count exception; `blinds_close` takes both
    through the same private helper in `rad_core.metrics` -- so a schedule labelled
    with one agrees, hour for hour, with one recomputed under the other from the same counts.
    Every other pair of distinct labels names a different rule.
    """
    return band_a == band_b or {band_a, band_b} == {_BAND_GENERAL, BAND_FLAT}


@dataclass(frozen=True)
class BlindSchedule:
    """One blind state per hour of the analysis window, and what decided them.

    `states[i]` is `"closed"` or `"open"` for `columns[i]`, and `columns` is the window in sorted
    column order -- the two are parallel by construction and there are exactly as many states as
    the window has hours. `band` and `threshold_lux` are carried so a report can say which rule
    applied -- a row of LM-83-12's table, or `BAND_FLAT` under LM-83-22 -- and what illuminance
    was called "in sun", neither of which can be recovered from `states` alone.
    """

    states: tuple[str, ...]
    columns: tuple[int, ...]
    area_sf: float
    band: str
    threshold_lux: float
    hours_closed: int


def _validate_threshold_lux(threshold_lux: float) -> None:
    """Raise if `threshold_lux` cannot be trusted to decide a single hour.

    Shared by `derive_schedule` and `group_schedule` so the message is written once: two copies
    of the same validation are two messages that agree only until one of them is edited, which is
    the exact defect a fix-round review found between them (a reviewer's note, fix round 1).
    """
    if not isinstance(threshold_lux, (int, float)) or isinstance(threshold_lux, bool):
        raise ValueError(
            f"threshold_lux must be a number of lux, not {threshold_lux!r}. It is the illuminance "
            "at or above which a point counts as receiving direct sunlight, and it is carried on "
            "the result."
        )
    if not math.isfinite(threshold_lux) or threshold_lux <= 0:
        raise ValueError(
            f"threshold_lux must be finite and positive, not {threshold_lux!r}. Zero would call "
            "every measured cell in sun, infinity would call none, and NaN compares false against "
            "every value -- each of the three produces a well-formed schedule off a comparison "
            "that decided nothing, which is the failure this module refuses to make quiet."
        )


def _refuse_repeated_group_name(names: Sequence[str], *, subject: str, consequence: str) -> None:
    """Raise if `names` repeats a group.

    Shared by `closing_order`, `group_schedule` and `blend_row` -- all three take a sequence of
    group names, or a `GroupFacts`/`ClosingOrder` that carries them -- so the *check* for a
    repeated name is written once. `group_schedule` grew this guard in an earlier fix round;
    `blend_row` did not inherit it when Task 5 was written, and neither did `closing_order`, even
    though all three take the same kind of argument and a repeated name is the same hole in each
    -- a composition defect a fix-round review found (ruling R138, fix round 1), the same shape
    `_validate_threshold_lux`'s own docstring names between `derive_schedule` and `group_schedule`.

    `subject` and `consequence` still let each caller name its own field and its own failure mode
    in the message -- a repeated name means something different at each call site (a `states`
    entry folded and a residual double-subtracted in `group_schedule`; a row double-summed in
    `blend_row`; an order that is not actually total in `closing_order`) -- one generic message
    for all three would name the shared cause but drop the caller-specific consequence.
    """
    if len(set(names)) == len(names):
        return
    raise ValueError(f"{subject} has a repeated group: {tuple(names)!r}. {consequence}")


def derive_schedule(
    points_in_sun_by_column: Mapping[int, int],
    *,
    points_total: int,
    area_sf: float,
    window: frozenset[int],
    threshold_lux: float,
    edition: Lm83Edition = DEFAULT_LM83_EDITION,
) -> BlindSchedule:
    """Apply `rad_core.metrics.blinds_close` to every hour of `window`, under `edition`.

    `edition` (LM-83-12 by default) is passed to both `blinds_close` and the band label, so the
    schedule and the rule it names cannot come from different editions.

    `points_in_sun_by_column` maps a matrix column to how many of the `points_total` analysis
    points received direct sunlight in that hour -- one integer per column, accumulated by the
    caller as it streams; see the module docstring for why this seam does not take a matrix. A
    point counts as in sun when its illuminance is `>= threshold_lux` (R861, LM-83-12 section
    2.2.6's "1,000 lux or more"); the comparison belongs to whoever built these counts, and
    `threshold_lux` is carried on the result so a report can say what it was. Keys outside
    `window` are not part of the analysis period and are ignored.

    Raises `ValueError` if `threshold_lux` is not finite and positive, if `window` has no hours, or
    if any of its columns has no count.

    **`threshold_lux` is validated here, and Phase 7c Task 7 said to validate it at the publishing
    seam instead.** Ruling R115 (docs/plan.md), adjudicated in Task 16 against the seam that
    actually exists:
    `rad_mcp.results_tool._blind_schedule` hands this function the module constant
    `rad_core.metrics.LM83_ASE_THRESHOLD_LUX` and nothing else -- the trigger threshold is not
    caller-settable, because `results(threshold_lux=...)` is documented and implemented as
    `action="points"`' argument alone and admits **zero**, which this contract does not. A check at
    that seam therefore could not go red from any caller, which is the "assertion that cannot go
    red" defect this phase is organised against. The contract is documented in this module's own
    docstring, so the guard belongs in this module, where a direct test can drive `-5.0`, `NaN`,
    `inf`, `0.0`, `True` and a non-number and assert the raise.

    **Two branches, and the second is not redundant.** `isinstance(True, int)` is `True`,
    `math.isfinite(True)` is `True` and `True > 0` is `True`, so a `bool` passes every arithmetic
    check below and would be published as `threshold_lux: true` -- a trigger that closed the blinds
    above *true* lux, with nothing downstream wrong about it. The type check is the only thing that
    stops it, and `results`' own `threshold_lux` is annotated `Field(strict=True)` for exactly this
    reason at the schema boundary -- which this seam does not sit behind.

    Each rejected value fails **silently** rather than loudly if it is let through, which is why
    this is a raise and not an advisory: `value >= nan` is `False` for every cell, so a NaN
    threshold yields a schedule that is open every hour of the year with `nan` carried as the
    number that decided it; `0.0` calls every measured cell in sun; `inf` calls none. All three
    return a well-formed `BlindSchedule`, and only its `threshold_lux` field says anything is
    wrong.
    """
    _validate_threshold_lux(threshold_lux)

    # **Above the per-column loop.** A bad threshold and an incomplete column set are different
    # repairs, and a caller handed the column message first would fix the matrix and then meet this
    # one; the threshold is knowable without looking at a single count.
    columns = tuple(sorted(window))
    if not columns:
        raise ValueError(
            "the blind schedule's window contains no hours, so there is nothing to schedule. "
            "An empty schedule is not an answer here -- it would read as 'the blinds never "
            "closed', which is the favourable case and is not what an empty period measured."
        )

    states: list[str] = []
    for column in columns:
        if column not in points_in_sun_by_column:
            raise ValueError(
                f"column {column} is in the analysis window but has no in-sun count, so the "
                "matrix does not cover the period. A missing hour is not an hour with no sun: "
                "reading it as 'open' would put a favourable state on an hour nothing was "
                "measured for."
            )
        closed = blinds_close(
            points_in_sun=points_in_sun_by_column[column],
            points_total=points_total,
            area_sf=area_sf,
            edition=edition,
        )
        states.append(BLIND_CLOSED if closed else BLIND_OPEN)

    return BlindSchedule(
        states=tuple(states),
        columns=columns,
        area_sf=area_sf,
        band=_area_band(area_sf, edition=edition),
        threshold_lux=threshold_lux,
        hours_closed=states.count(BLIND_CLOSED),
    )


@dataclass(frozen=True)
class GroupFacts:
    """One window group, as `closing_order` needs to see it.

    Plain data, not `rad_engine.chain.WindowGroup` -- this module may not import `rad_engine`
    (CONTEXT.md, Layers), so a caller in that layer maps its own type onto this one field by field.

    `sill_z` is the lowest vertex Z of the group's aperture, in metres, in the project's own frame.
    It is the substituted ordering key and nothing else in this module reads it.

    `role` is `InputRecord.role` passed through **unchecked**: it may be `None` (undeclared), one
    of `CLOSING_ROLES`, or any other string a stale or hand-edited manifest happened to carry --
    `"ground"`, a material label, is a real value seen in this field for other purposes. See
    `closing_order` for why the last case is treated as undeclared rather than rejected.
    """

    name: str
    role: str | None
    sill_z: float


@dataclass(frozen=True)
class ClosingOrder:
    """The order LM-83 section 2.2.6's prefix scan closes groups in, and whether it was declared.

    `substituted` is `True` when **any** group's role was not usable to order by -- undeclared
    (`None`) or not one of `CLOSING_ROLES`. It is not per group, because an order is a property of
    the whole sequence: one declared role does not order the undeclared ones against it, and a
    half-declared order is exactly as substituted as an undeclared one.

    **A substituted order is not a measured value, and only the cardinality claim survives here
    too -- see R137 on `group_schedule`, which this paragraph originally got wrong the same way.**
    Task 3 scans groups in this order and stops at the first prefix that meets the criterion. Both
    the substituted order's scan and a correctly-ordered one close **at least as many** groups as a
    true minimum-cardinality search, for the reason `group_schedule` gives: each is constrained to
    one fixed sequence of prefixes, so neither can find a subset an unconstrained search would have
    missed. **That does not make either one's closed set a superset of the other's** -- two
    different orders scan two different sequences of prefixes, and the sets those prefixes settle
    on can be disjoint, exactly as `group_schedule`'s counterexample shows for a scan against the
    true minimum. Where a substituted order's closed set happens to nest around a
    correctly-ordered scan's, the extra closures it makes are real over-closures and it
    under-retains daylight, **understating sDA** -- the same direction ruling R116 measured. Where
    the two do not nest, the direction is not determined, and this docstring no longer claims
    otherwise. **Ruling R137 (fix round 1):** the earlier "superset, never overstates" claim here
    and in `group_schedule` was false -- a hand-derived counterexample -- and both were corrected
    together, in the same commit, once it surfaced.

    **The claim originated in the design spec's own section 3, and this docstring asserted for the
    whole branch that R137 "also corrects" it -- which was not true until the whole-branch fix
    wave.** The spec was byte-identical to `master` while every shipped surface carried the
    correction; a code comment claiming a document was corrected, about a document that was not, is
    the worst shape a spec-drift finding has. `docs/superpowers/specs/
    2026-08-23-phase-7d-blind-aware-sda-design.md` section 3 now carries the correction as a struck
    sentence with R137's reasoning beside it, and
    `tests/test_blinds.py::TestTheDesignSpecCarriesR137sCorrection` reads that file and goes red if
    the struck claim is restored or the correction removed -- so this sentence is checkable rather
    than merely asserted.
    """

    names: tuple[str, ...]
    substituted: bool


def closing_order(groups: Sequence[GroupFacts]) -> ClosingOrder:
    """The order the prefix scan closes window groups in.

    **Declared when every group's role is one of `CLOSING_ROLES`**, ordered by LM-83's two classes
    and broken by name so the order is total -- an order that depends on input sequence is an order
    that moves when a caller reorders their calls. The DISCUSSION for section 2.2.6 advises closing
    "view" groups first and "clerestory" groups last; nothing in the standard requires reading that
    advice as a *search* order rather than a scheduling preference -- doing so is this server's own
    reading, applied consistently by `_ROLE_RANK`.

    **Substituted when any group's role is not usable to order by**, ordered by `sill_z` ascending:
    the lowest aperture first, on the reading that a view window sits below a clerestory. A role
    that is `None` (undeclared) and a role that is some other string entirely (stale or hand-edited,
    e.g. `"ground"`) are treated identically here: both fail the same test, "is this one of the two
    classes the DISCUSSION names", and an unrecognised value is not evidence of anything more
    specific than "not declared". **This function never raises on an unrecognised role.**
    `InputRecord.role` is passed through unchecked by `annual_tool._window_groups`, so a `KeyError`
    on `_ROLE_RANK[g.role]` here would turn a stale manifest into a crash inside a pure function
    that has no way to report which group's role was at fault -- the substituted branch is a
    well-formed, honestly-labelled answer instead, at the cost documented on `ClosingOrder`.

    That reading is a substitution and `ClosingOrder.substituted` says so, because ordering *is*
    what decides which groups close.

    Raises `ValueError` on an empty sequence: an empty order would read as "no group ever closes",
    the favourable case, off a sequence nothing was measured for.

    A single group is never a substitution, whatever its role: a one-element order is total
    whatever decided it, and there is no second group to compare `sill_z` or rank against.

    Raises `ValueError` on a repeated group name too: two `GroupFacts` sharing a name would carry
    that repeat straight into `ClosingOrder.names`, which this docstring calls "total" and whose
    consumers -- `group_schedule`, `blend_row` -- both assume names a group at most once (ruling
    R138, fix round 1).
    """
    if not groups:
        raise ValueError(
            "the closing order was asked for with no window group. An empty order reads as "
            "'no group ever closes', which is the favourable case and is not what an empty "
            "project measured."
        )
    _refuse_repeated_group_name(
        [g.name for g in groups],
        subject="the window groups",
        consequence=(
            "Two groups cannot be ordered against each other by name if they share one, and the "
            "repeat would carry straight into `ClosingOrder.names`, which every consumer of this "
            "order assumes names a group at most once."
        ),
    )
    if len(groups) == 1:
        return ClosingOrder(names=(groups[0].name,), substituted=False)

    declared = all(g.role in CLOSING_ROLES for g in groups)
    if declared:
        ranked = sorted(groups, key=lambda g: (_ROLE_RANK[g.role], g.name))  # type: ignore[index]
    else:
        ranked = sorted(groups, key=lambda g: (g.sill_z, g.name))
    return ClosingOrder(
        names=tuple(g.name for g in ranked),
        substituted=not declared,
    )


#: R870: an automated group's bound schedule (`window_group(action="bind_schedule")`). One row per
#: weather hour in the EPW's own row order, `open` or `closed`, an optional first line `state`.
AUTOMATED_SCHEDULE_HEADER: Final[str] = "state"
AUTOMATED_SCHEDULE_HOURS: Final[tuple[int, int]] = (8760, 8784)
#: R870, an unsourced bound: 8 784 rows of "closed\r\n" and a header is 70 279 bytes; 256 KiB
#: admits that with room and refuses a file that is not a schedule. Unsourced values table.
MAX_AUTOMATED_SCHEDULE_BYTES: Final[int] = 262_144
_LINE_BREAK: Final[re.Pattern[str]] = re.compile(r"\r\n|\r|\n")


class AutomatedScheduleError(ValueError):
    """A bound automated schedule that is not one row per hour of `open` or `closed`."""


@dataclass(frozen=True)
class AutomatedScheduleRows:
    """An automated group's schedule as read (R870): one state per weather hour, in file order.

    `header` records whether the optional first line `state` was present; it is not a row.
    """

    states: tuple[str, ...]
    header: bool

    @property
    def hours(self) -> int:
        return len(self.states)

    @property
    def hours_closed(self) -> int:
        return sum(1 for state in self.states if state == BLIND_CLOSED)

    @property
    def hours_open(self) -> int:
        return self.hours - self.hours_closed


def parse_automated_schedule(data: bytes, *, hours: int | None = None) -> AutomatedScheduleRows:
    """Read an automated group's hourly schedule (R870) from its bytes.

    One row per hour of the weather file, in the weather file's own row order, each exactly `open`
    or `closed` after surrounding whitespace is stripped (LF, CRLF or CR ends a line, and a
    leading UTF-8 byte order mark is dropped); one optional first line
    `state`; 8 760 or 8 784 rows. Anything else raises `AutomatedScheduleError` naming the 1-based
    line number of the first bad row, or the size or row count that is wrong.

    `hours`, when given, replaces the two legal counts with exactly that one (R870, Task 3's build
    ruling): `rad_engine.schedule` reads a bound schedule against its own `--n-hours`, the column
    count of the direct matrices it schedules, so a year the run does not span is refused there
    rather than indexed past its end. `window_group(action="bind_schedule")` omits it: at bind time
    no run, and so no hour count, exists yet.
    """
    if len(data) > MAX_AUTOMATED_SCHEDULE_BYTES:
        raise AutomatedScheduleError(
            f"the schedule is {len(data)} bytes, over the {MAX_AUTOMATED_SCHEDULE_BYTES} a year "
            "of 'open' and 'closed' rows needs; it is not an hourly schedule."
        )
    try:
        # `utf-8-sig`: a spreadsheet's "CSV UTF-8" export writes a byte order mark first, which no
        # editor shows; read as text it would make line 1 a bad row the caller cannot see. Only
        # the leading one is dropped -- U+FEFF anywhere else stays in its row and is refused.
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise AutomatedScheduleError(f"the schedule is not UTF-8 text: {exc}") from exc
    # Only CR LF, CR and LF end a line, as in an editor: `str.splitlines` would also split on form
    # feed, the information separators, NEL and U+2028/9, and number the rows differently from the
    # file the caller is looking at. Exactly one final line break is dropped; an empty line anywhere
    # else is a bad row (the `("", 2)` case pins it). A file of nothing but whitespace has no rows
    # at all, and is refused by the row count below rather than as a blank "line 1".
    lines = _LINE_BREAK.split(text) if text.strip() else []
    if lines and lines[-1] == "":
        lines.pop()
    header = bool(lines) and lines[0].strip() == AUTOMATED_SCHEDULE_HEADER
    states: list[str] = []
    for number, raw in enumerate(lines[1:] if header else lines, start=2 if header else 1):
        value = raw.strip()
        if value not in BLIND_STATES:
            raise AutomatedScheduleError(
                f"line {number} of the schedule is {raw!r}; every row after the optional "
                f"'{AUTOMATED_SCHEDULE_HEADER}' header is 'open' or 'closed' (surrounding "
                "whitespace ignored), one per hour of the weather file, in its own row order."
            )
        states.append(value)
    if hours is not None:
        if len(states) != hours:
            raise AutomatedScheduleError(
                f"the schedule has {len(states)} hourly rows; it needs {hours}, one per hour of "
                "the run that reads it, in the weather file's own row order."
            )
    elif len(states) not in AUTOMATED_SCHEDULE_HOURS:
        raise AutomatedScheduleError(
            f"the schedule has {len(states)} hourly rows; it needs 8760 (8 760, a common year) or "
            "8784 (8 784, a leap year), one per row of the weather file it will be run with."
        )
    return AutomatedScheduleRows(states=tuple(states), header=header)


@dataclass(frozen=True)
class GroupSchedule:
    """One blind state per group per hour, and what decided them.

    `states[g][i]` is `"closed"` or `"open"` for group `g` at `columns[i]`. Every group named by
    `order` appears in `states`, and every list has exactly as many entries as `columns` -- parallel
    by construction, as `BlindSchedule`'s are.

    `hours_closed` counts **hours in which any group closed**, not group-closures. An hour that
    closes two groups is one hour. The whole-grid `BlindSchedule` counts the same quantity, so the
    two remain comparable. **It counts only closures the scan decided** (R870): an automated
    group's `"closed"` is an input the caller bound, not a decision of the 2 % trigger, so an hour
    in which only automated groups are closed is not counted.

    `automated` names the automated groups (R870), sorted; each also appears in `states` with its
    own bound state per column. Empty when the caller declared none.
    """

    states: Mapping[str, tuple[str, ...]]
    columns: tuple[int, ...]
    area_sf: float
    band: str
    threshold_lux: float
    hours_closed: int
    order: ClosingOrder
    automated: tuple[str, ...] = ()


def group_schedule(
    direct_by_column: Mapping[int, Mapping[str, Sequence[float]]],
    *,
    order: ClosingOrder,
    points_total: int,
    area_sf: float,
    window: frozenset[int],
    threshold_lux: float,
    edition: Lm83Edition = DEFAULT_LM83_EDITION,
    automated: Mapping[str, Sequence[str]] | None = None,
) -> GroupSchedule:
    """LM-83 section 2.2.6's blind schedule, per window group -- or LM-83-22 section 4.6's when
    `edition` is `"LM-83-22"`; `edition` is passed to both `blinds_close` and the band label.

    ## Automated groups (R870)

    `automated[g][column]` is automated group `g`'s own bound state (`"open"` or `"closed"`) at each
    column of `window`. An automated group is **out of the closing order**: the prefix scan over
    `order.names` never closes or opens it, and `states[g]` is its bound state. Whether its row
    counts toward the manual groups' residual depends on the edition:

    - **LM-83-22** (section 4.6): an automated shade "is excluded from the 2% sunlight trigger
      analysis", so its row is never in the residual, whatever its state.
    - **LM-83-12** (section 2.2.6, p. 5) allows "the manufacturers' blind control protocol" for
      motorized blinds and gives **no exclusion**: the room's direct sun is what the trigger
      counts, so the row is in the residual in exactly the hours its state is `"open"` (a closed
      automated blind admits no direct beam on this reading).

    `hours_closed` counts only hours in which the scan closed a **manual** group: an automated
    group's state is an input, not a decision of the trigger. With every group automated (empty
    `order.names`) no scan runs and `hours_closed` is 0. An empty `order.names` with no automated
    group is refused. With `automated` empty or `None` every path below is unchanged.

    `direct_by_column[h][g][p]` is the **direct-sun-only** illuminance at point `p` from group `g`
    in hour `h`, in lux -- one `illdir:<grid>:<g>` column per group.

    ## The precondition (ruling R147), which is stated nowhere else and enforced nowhere

    The scan below builds its residual as **the sum of every column** and removes a closed group by
    **subtracting that group's column**. Both halves rest on one thing:

    > **Every direct path from a sun to a sensor crosses exactly one window group's aperture.**

    It is a property of the *scene*, not of this arithmetic, and nothing in this module can check
    it. Where it fails, this function returns a schedule that is wrong about the room -- with no
    refusal, because every column is finite, non-negative and the right length.

    **Nothing published today depends on any of this.** This function has **no production caller**:
    `results(action="blind_schedule")` derives its schedule with `derive_schedule` over the
    whole-grid `illdir:<grid>` (`rad_mcp.results_tool`), and the per-group columns this function
    takes are sealed by `run_annual` and read by nothing yet. Task 11 seals `sched:<grid>` from them
    and Task 14 publishes it; everything below is written in that tense on purpose (ruling R149 --
    an earlier draft of this paragraph described the per-group residual as though it were already
    being computed on the wire).

    **Two ways it fails, and the arithmetic is the same one** (`tests/test_blinds.py::
    TestTheSubtractionPrecondition` runs every claim here against this function; the `rcontrib`
    figures are the Task 8 reviewer's, on a two-group light-well with the ASE leg's own flags):

    1. **An opening that belongs to no window group** -- a doorway, a rooflight, a neighbour's
       glazing left in the model. No per-group direct-sun run blacks it (each blacks only the
       *other groups'* apertures), so it is in **every** column: whole-scene `6.797e-05`, **each**
       per-group run `6.797e-05`. With `G` groups and an unclaimed contribution `D`, the scan's
       residual after closing `k` of them is `sum(open) + (G-k)*D` while the room's is
       `sum(open) + D`, so **the error is `(G-k-1)*D`** -- an over-count for `k <= G-2`, **exactly
       zero at `k = G-1`**, and an under-count of `D` at `k = G`. The consequence therefore depends
       on `G`, and **each shape below was measured against this function rather than derived from
       that line** (ruling R149; the direction was stated wrongly twice before it was measured per
       shape):

       - **`G = 1` -- the only shape `rad_mcp.annual_tool` can reach today**, because it still
         narrows every run to one window group (`chain.ThreePhaseTarget.single`). `k = 0` is
         `G-1`, so the one closure decision this scan makes is **exact**: measured over 20 000
         randomised draws against an honest scan that sees `sum(open) + D`, **0 disagreements**.
         What fails is what happens *after* the closure: the single subtraction removes `D` along
         with the group's own beam, so the residual reads **zero for a room still in sun**.
         Measured, `D = 6 000 lux` at every point: the group closes (correctly -- the room really
         is lit), the scan's residual is `[0, 0, 0]` and the room's is `[6000, 6000, 6000]`, three
         points of three still in direct sun. The scan terminates as though the criterion were met
         when **no combination of blinds can meet it**, because the light comes through something
         no blind covers.
       - **`G >= 2` -- reachable when Task 12 lifts that narrowing.** The prefixes with `k <= G-2`
         over-count, and the scan closes **at least as many** groups as an honest one: measured,
         **1 108 disagreements in 20 000 draws at `G = 2`** and **1 176 at `G = 3`**, and in
         **every** one of them the scan's closed set is a strict superset of the honest set -- never
         once the other way (`(more, fewer, other) = (1108, 0, 0)` and `(1176, 0, 0)`).
         *(Corrected 2026-10-12, R861: it said "**1 271 disagreements in 20 000 draws at `G = 2`**
         and **1 405 at `G = 3`**" and "`(1271, 0, 0)` and `(1405, 0, 0)`", measured under the
         strict `>` in-sun test; the same draws re-measured under `>=`.)* Over-closure
         retains less daylight, which **understates sDA**, the direction ruling R116 measured. The
         `k = G` under-count is case `G = 1`'s failure again, at the end of the scan.
       - **The control that makes those numbers mean something**: with `D = 0` -- the precondition
         holding -- the same comparison finds **0 disagreements at `G = 1, 2 and 3`**, so the
         oracle is a faithful model of this scan and the divergences above are the unclaimed
         opening's doing.

       **Only a *candidate* for this half is detectable from the scene text, and a modifier is not
       a path** (ruling R150). `rad_engine.scene.unclaimed_transmitting_modifiers` lists modifiers
       of a transmitting type that no group claims, and `run_annual` emits
       `BLIND_SCHEDULE_UNCLAIMED_APERTURE` naming them -- but whether any of them lies on a path
       from a sun to a sensor is exactly the question the text cannot answer. It fires on an
       interior glass partition and on a `dielectric` tabletop, which are correct scenes; and it is
       **silent** on a scene that has this defect with no unclaimed material at all -- **a hole**.
       An absent wall or a dropped `!xform` include admits sun crossing **zero** apertures, which is
       this same arithmetic with `D` supplied by nothing the detector can see.

    2. **Two group apertures in series on one path** -- a light well, a double-skin facade, an
       internal window onto a sunlit atrium. The beam crosses `a`'s glazing and then `b`'s, and it
       is killed in **both** per-group runs because each blacks the other's aperture: whole-scene
       `5.281e-05`, **both** per-group runs `0.000e+00`. Every column reads zero, the scan sees an
       unlit room and leaves every blind **open** -- **under**-closure, **sDA overstated**, the
       opposite direction from case 1 at `G >= 2`. Measured the same way.
       **This half is a named limitation, detected only where it zeroes a whole matrix.** When the
       series path is the only way sun reaches the grid, every cell of each per-group `asemx` is
       zero, and the seal refuses it with `ASE_DIRECT_MATRIX_EMPTY`: the occlusion probe opens
       every group's aperture and finds the beam through both (ruling R848), so the run fails
       loudly rather than deriving a schedule. When some other path also reaches the grid, the
       per-group matrix is not all zero, no probe runs, and the under-closure is undetected:
       deciding whether two apertures lie on one path is a question about occlusion between them,
       which needs a probe of the path between two apertures that this server does not build. No
       advisory claims to cover that case, and this paragraph is the record that it is open.

    **So the sum of the per-group columns is neither an upper nor a lower bound on the room's own
    direct beam, and neither is the schedule derived from it** -- which is why `illdir:<grid>` is
    computed by its own whole-scene run rather than summed from these (ruling R146), and why no
    single direction of error is claimed for the precondition as a whole. Per shape, measured, the
    directions are the ones above.

    **What this replaces.** Until fix round 2 this paragraph read *"the subtraction is licensed,
    the reconstruction is not"*. That distinction does not describe the code: the residual **is**
    the reconstruction, `lit` reads it before any closure, and *"each column is the beam admitted
    through that group"* is exactly what both cases falsify. Fix round 2 replaced it with *"for
    `k < G` it over-counts"*, which is false at `k = G-1` and therefore false at every `G = 1` run
    -- the only shape that can reach the advisory today. Both survived a review because they sounded
    like narrowings. This one is per shape and each shape is measured.

    **The scan is a prefix, and that is a reading of the standard.** Section 2.2.6 says groups close
    *"in any combination until the criterion is met"* and its DISCUSSION advises an order. Reading
    the order as the *search* order makes the scan O(N) rather than 2^N, and it is what the
    DISCUSSION is for.

    **Ruling R137 (owner, fix round 1): only the cardinality claim survives, and it corrects this
    docstring, the design spec's own §3 and the project owner's hand-off** -- all three stated the
    claim below as fact, and a hand-derived counterexample showed it false. The prefix scan closes
    **at least as many** groups as a true minimum-cardinality search: it is constrained to close
    groups in `order`, one prefix at a time, so any subset it settles on is also reachable by an
    unconstrained search, and the unconstrained minimum can therefore never exceed it. That
    cardinality bound is the only guarantee. **It does not follow that the prefix scan's closed set
    is a superset of the minimum one** -- the two closed sets can be disjoint, and where they are,
    neither retains more daylight than the other by construction; cardinality alone does not decide
    it. Counterexample, `area_sf=150.0` (the "> 1 point" band), `points_total=3`, order `(a, b, c)`,
    `threshold_lux=1000.0`:

        a = [900, 0, 400]
        b = [600, 400, 400]
        c = [900, 900, 900]

    The prefix scan closes `a` (residual `a+b+c` lights all 3 points, `> 1` triggers closure), then
    `b` (residual now `[1500, 1300, 1300]`, still lights all 3), and stops there: the residual is
    now `[900, 900, 900]` -- exactly `c` -- which lights 0 points, `> 1` no longer triggers. Closed
    set: `{a, b}`, cardinality 2. Closing `c` alone leaves residual `a+b = [1500, 400, 800]`, which
    lights exactly 1 point -- already at the "> 1 point" band's own boundary, so no further closure
    is required. Closed set: `{c}`, cardinality 1, the true minimum. `{a, b}` and `{c}` are
    **disjoint** -- neither contains the other -- so cardinality 2 >= 1 holds (the only claim that
    is safe), but which set retains more daylight depends on the groups' own areas, which
    cardinality says nothing about. **Where the scan's closed set does nest around the minimum
    one** (the minimum is a subset of the scan's), the scan's extra closures are real over-closures
    and it under-retains daylight, **understating sDA** -- the same direction ruling R116 measured,
    and the reason the direction is still named wherever it is knowable. **Where the two do not
    nest, the direction is not determined**, and this docstring no longer claims otherwise.

    Every refusal below exists because its favourable answer is silent. A missing group counts as
    zero and opens a blind that should have closed; a group present but not named by `order` is the
    mirror case and is refused for the mirror reason -- silently dropping it opens a blind that
    should have stayed factored in, which **overstates** sDA, the favourable direction; a
    non-finite or negative cell compares `False` against every threshold, or can cancel a real
    closure by pulling the residual back under it, and produces a year of `"open"` (measured on the
    wire in Phase 7c before the guard); a short row scans a prefix and reports it as the whole grid;
    a duplicate name in `order.names` folds `states` down to one entry per repeated group while
    `columns` keeps one entry per hour, and sums that group's row into the residual once per
    repetition, doubling light the grid never produced.
    """
    _validate_threshold_lux(threshold_lux)
    columns = tuple(sorted(window))
    if not columns:
        raise ValueError(
            "the blind schedule's window contains no hours. An empty schedule reads as 'the blinds "
            "never closed', which is the favourable case and is not what an empty period measured."
        )
    if points_total <= 0:
        raise ValueError(
            f"points_total must be positive, not {points_total}. A zero denominator makes the "
            "2 % rule return False for every hour, which is a year of open blinds off no grid."
        )
    _refuse_repeated_group_name(
        order.names,
        subject="order.names",
        consequence=(
            "A repeated name folds `states` down to one entry per repetition while `columns` "
            "keeps one entry per hour -- breaking 'parallel by construction' -- and would sum "
            "that group's row into the residual once per repetition, doubling light the grid "
            "never produced."
        ),
    )

    auto: Mapping[str, Sequence[str]] = automated or {}
    if not order.names and not auto:
        raise ValueError(
            "the closing order names no window group and no automated group was given, so there "
            "is no blind to schedule. An empty schedule reads as 'the blinds never closed', the "
            "favourable case, off no group at all."
        )
    both = sorted(set(order.names) & set(auto))
    if both:
        raise ValueError(
            f"window group(s) {both!r} are both in the closing order and automated. An automated "
            "group's state is its own bound schedule and the 2 % scan never decides it; a group "
            "in both would be scanned and bound at once, and the two states can disagree."
        )
    for name in sorted(auto):
        bound = auto[name]
        bad = sorted({state for state in bound if state not in BLIND_STATES}, key=repr)
        if bad:
            raise ValueError(
                f"automated group {name!r} has state(s) {bad!r}; a bound state is exactly "
                f"'{BLIND_OPEN}' or '{BLIND_CLOSED}'. An unrecognised state read as anything "
                "would decide whether its direct sun reaches the trigger."
            )
        missing = [column for column in columns if column >= len(bound) or column < 0]
        if missing:
            raise ValueError(
                f"automated group {name!r} has {len(bound)} bound state(s), with no entry for "
                f"column(s) {missing[:5]!r}{' ...' if len(missing) > 5 else ''} of the analysis "
                "window. A missing hour has no state to report and no defined contribution to "
                "the trigger."
            )

    order_names = set(order.names)
    known_names = order_names | set(auto)
    automated_names = tuple(sorted(auto))
    # LM-83-12 section 2.2.6 gives no exclusion; LM-83-22 section 4.6 excludes the automated shade
    # from the trigger analysis (R870).
    count_open_automated = edition != LM83_22
    states: dict[str, list[str]] = {name: [] for name in order.names}
    for name in automated_names:
        states[name] = []
    hours_closed = 0

    for column in columns:
        if column not in direct_by_column:
            raise ValueError(
                f"column {column} is in the analysis window but no direct-sun values were given "
                "for it. A missing column counted as zero is an hour of open blinds nothing "
                "measured."
            )
        by_group = direct_by_column[column]
        ghosts = set(by_group) - known_names
        if ghosts:
            raise ValueError(
                f"column {column} has direct-sun rows for {sorted(ghosts)!r}, which `order` does "
                "not name (nor `automated`). A group silently dropped from the scan is never "
                "subtracted from the residual, so the criterion sees less light than the grid "
                "received -- the favourable direction, which overstates sDA."
            )
        rows: dict[str, Sequence[float]] = {}
        for name in (*order.names, *automated_names):
            if name not in by_group:
                raise ValueError(
                    f"window group {name!r} has no direct-sun row in column {column} (only "
                    f"{sorted(by_group)!r} were given). A missing group contributes zero, which "
                    "opens a blind that should have closed."
                )
            row = by_group[name]
            if len(row) != points_total:
                raise ValueError(
                    f"window group {name!r} column {column} has {len(row)} values but the grid has "
                    f"{points_total} points. A short row scans a prefix and reports it as the "
                    "whole grid."
                )
            rows[name] = row

        # The running residual: total direct illuminance at each point from the groups still open.
        # Every automated row is validated; it is summed only under LM-83-12 and only when open.
        residual = [0.0] * points_total
        for name in (*order.names, *automated_names):
            is_automated = name in auto
            counts = not is_automated or (
                count_open_automated and auto[name][column] == BLIND_OPEN
            )
            row = rows[name]
            for index in range(points_total):
                value = row[index]
                if not math.isfinite(value):
                    raise ValueError(
                        f"window group {name!r} column {column} point {index} is {value!r}, which "
                        "is not finite (nan or inf). Every comparison against a threshold is False "
                        "for NaN, so this would publish a year of 'open' off a matrix nothing can "
                        "be read from."
                    )
                if value < 0:
                    raise ValueError(
                        f"window group {name!r} column {column} point {index} is {value!r}, which "
                        "is negative. Direct-sun illuminance is not physical below zero, and a "
                        "negative cell can cancel a real closure by pulling the residual back under "
                        "threshold_lux without any group actually blocking that light -- a "
                        "favourable answer nothing measured produced."
                    )
                if counts:
                    residual[index] += value

        closed: list[str] = []
        # `>=`: LM-83-12 section 2.2.6's "1,000 lux or more" (R861), here and in the recount below.
        lit = sum(1 for value in residual if value >= threshold_lux)
        for name in order.names:
            if not blinds_close(
                points_in_sun=lit, points_total=points_total, area_sf=area_sf, edition=edition
            ):
                break
            closed.append(name)
            row = rows[name]
            lit = 0
            for index in range(points_total):
                residual[index] -= row[index]
                if residual[index] >= threshold_lux:
                    lit += 1

        for name in order.names:
            states[name].append(BLIND_CLOSED if name in closed else BLIND_OPEN)
        for name in automated_names:
            states[name].append(auto[name][column])
        if closed:
            hours_closed += 1

    return GroupSchedule(
        states={name: tuple(values) for name, values in states.items()},
        columns=columns,
        area_sf=area_sf,
        band=_area_band(area_sf, edition=edition),
        threshold_lux=threshold_lux,
        hours_closed=hours_closed,
        order=order,
        automated=automated_names,
    )


def blend_row(
    contributions: Mapping[tuple[str, str], Sequence[float]],
    *,
    states_at: Callable[[str, int], str],
    order: Sequence[str],
    hours: int,
) -> list[float]:
    """One sensor's blended annual row: the sum over groups of the state the schedule selected.

    `contributions[(group, state)][h]` is that group's contribution to this sensor in hour `h` with
    that state deployed -- one row of `ill:<grid>:<group>:<state>`. The sum is exact rather than an
    approximation because the three-phase result is linear in the groups: `I = sum_g V_g T_g D_g S`,
    which the one-group refusal `chain.ThreePhaseTarget.single` still raises states in full (it was
    `chain.three_phase_target`'s until Phase 7d-1 Task 6 lifted the resolver's own group count).

    **Per row, because the caller streams.** A materialised annual matrix is ~607 MB at the
    456-point office grid; `rad_engine.results.iter_annual_lux_rows` yields one row at a time for
    that reason and this function keeps the same shape.

    Every refusal is a zero that would otherwise be silent: a missing `(group, state)` pair
    contributes nothing and dims the room, a short row blends a prefix and pads the rest with
    nothing, a non-finite value makes the whole row NaN and every downstream threshold compare
    False, a repeated name in `order` sums a group's contribution once per repetition (overstating
    the row, the dangerous direction), and a `contributions` entry for a group `order` does not
    name is dropped from the sum without a trace (understating the row, the opposite direction --
    ruling R138, fix round 1, the same two-sided hole `group_schedule`'s own duplicate and ghost
    guards close for the residual it scans).
    """
    if not order:
        raise ValueError(
            "the blend was asked for with no window group. A row of zeros is a dark room, which "
            "is a measured-looking answer to a question nothing was measured for."
        )
    _refuse_repeated_group_name(
        order,
        subject="order",
        consequence=(
            "A repeated name sums that group's contribution once per repetition instead of once, "
            "overstating the blended row with light the schedule never selected twice."
        ),
    )
    order_names = set(order)
    ghosts = {group for group, _state in contributions} - order_names
    if ghosts:
        raise ValueError(
            f"contributions has row(s) for window group(s) {sorted(ghosts)!r}, which `order` "
            "does not name. A group silently dropped from the blend is never summed into the "
            "row, so the room comes out darker than what was computed -- understating the row, "
            "the opposite direction from a missing `(group, state)` pair."
        )
    if hours <= 0:
        raise ValueError(
            f"hours must be positive, not {hours}. A non-positive value here would silently "
            "return `[]` -- a well-formed, empty blended row that reads as 'a window with no "
            "hours' rather than as the bad argument it actually is, and nothing downstream can "
            "tell the two apart."
        )

    blended = [0.0] * hours
    for hour in range(hours):
        for group in order:
            state = states_at(group, hour)
            key = (group, state)
            if key not in contributions:
                raise ValueError(
                    f"the schedule selects state {state!r} for window group {group!r} in hour "
                    f"{hour}, but no contribution was given for that pair. A missing contribution "
                    "sums as zero and publishes a darker room than was computed."
                )
            row = contributions[key]
            if len(row) != hours:
                raise ValueError(
                    f"contribution for {group!r}/{state!r} has {len(row)} hours but the blend is "
                    f"over {hours}. A short row blends a prefix and leaves the rest at zero; a "
                    "long row silently carries hours nothing downstream reads or checks."
                )
            value = row[hour]
            if not math.isfinite(value):
                raise ValueError(
                    f"contribution for {group!r}/{state!r} at hour {hour} is {value!r}, which is "
                    "not finite. One such cell makes the whole blended row NaN, and every "
                    "threshold downstream compares False against it."
                )
            blended[hour] += value
    return blended
