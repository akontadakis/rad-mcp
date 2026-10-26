"""The analysis denominators, and the mapping from a matrix column to a clock hour.

Pure: no Radiance, no file formats, no I/O. Three standards count over three different periods and
this module is the only place any of them is defined -- `docs/tool-surface.md` section 14's periods
table is the specification, and the three are not interchangeable (defect row D8, ruling R98).

**Nothing here is a constant read off a standard.** LM-83's "3 650 hours" and EN 17037's "4 380" are
both *derived* -- ten columns of each day the matrix actually has, and half of the hours it actually
has. The one exception is a column set, not a count: a leap-year matrix of 8 784 columns leaves out
29 February's 24 columns (`LEAP_DAY_COLUMNS`), because both LM-83 editions count 10 hours a day over
365 days (R867), so a whole year counts 3 650 either way. R47 exists because a hardcoded 8 760 let
a leap year report a complete common year, and this module is where that mistake would recur if it
were going to. The same holds for LM-83's clock: its window is 08:00-18:00 *local clock time*
(LM-83-12 section 2.2.1), and a matrix column is local *standard* time, so the daylight-saving
shift is not a constant either -- `lm83_window` takes it per column from its caller
(`rad_engine.clock.clock_shifts`, R860) and assumes none when given none.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date as _date
from datetime import timedelta as _timedelta
from typing import Final, Sequence

__all__ = [
    "LM83_START_HOUR",
    "LM83_END_HOUR",
    "LM83_HOURS_PER_DAY",
    "LM83_DAYLIT_FRACTION",
    "LEAP_DAY_COLUMNS",
    "MAX_DECLARED_HOURS",
    "WHOLE_YEAR_HOURS",
    "is_whole_year",
    "HourIndex",
    "lm83_window",
    "DaylightHours",
    "daylight_hours",
    "OCCUPIED_REFERENCE_YEAR",
    "OCCUPIED_START_HOUR",
    "OCCUPIED_END_HOUR",
    "occupied_hours",
]

LM83_START_HOUR: Final[float] = 8.0
LM83_END_HOUR: Final[float] = 18.0
LM83_HOURS_PER_DAY: Final[int] = 10
LM83_DAYLIT_FRACTION: Final[float] = 0.5

#: R867: 29 February's 24 columns in a whole-year matrix of 8 784 columns whose column 0 is
#: 1 January 00:00. Both editions say 3,650 hours (LM-83-12 section 2.2.1; LM-83-22 section 4.4).
LEAP_DAY_COLUMNS: Final[range] = range(59 * 24, 60 * 24)
_LEAP_YEAR_HOURS: Final[int] = 8784

#: The two hourly column counts that are a whole year: 8 760 for a common year, 8 784 for a leap
#: year. **Not a denominator** -- nothing here divides by either, and R47's rule that no period is
#: a hardcoded constant still holds for every count `lm83_window`/`daylight_hours` derive. This is
#: the *domain* of the annual metrics, not one of their denominators: it answers "is this matrix a
#: year at all", which is a different question from "how many of its columns count".
WHOLE_YEAR_HOURS: Final[frozenset[int]] = frozenset({8760, 8784})

#: The column count above which a **manifest-declared** matrix shape is refused outright. The
#: hours-axis half of `rad_core.grids.MATRIX_MAX_DECLARED_POINTS`, and there for the same reason:
#: `lm83_window(n_hours)` builds a frozenset over `range(n_hours)`, and in `results_tool` it was
#: built one line *above* the `is_whole_year` gate that would have refused it -- measured,
#: `lm83_window(8_000_000)` costs 0.64 s and ~342 MB and then `is_whole_year` returns `False`.
#: The gate order is fixed too, but an ordering fix guards one call site and a magnitude bound
#: guards every caller that sizes anything from this integer.
#:
#: **Not `max(WHOLE_YEAR_HOURS)`.** Several `results` actions read a matrix that is legitimately
#: *not* a whole year -- `summary`, `points`, `timeseries` and `export` all answer over one -- so a
#: bound at 8 784 would refuse files this server seals and reads correctly today. 527 040 is a leap
#: year at one-minute resolution (366 x 24 x 60), which is finer than anything `gendaymtx` produces
#: and therefore above every column count a real annual matrix can carry, while
#: `lm83_window(527_040)` is ~220 000 entries -- a bounded cost rather than an unbounded one.
MAX_DECLARED_HOURS: Final[int] = 366 * 24 * 60


def is_whole_year(n_hours: int) -> bool:
    """Whether a matrix of `n_hours` hourly columns spans a whole year (8 760, or 8 784 in a leap
    year).

    **Both LM-83's and EN 17037's thresholds are annual by construction, and neither scales.**
    ASE_1000,250h counts hours against an absolute `250` (ch04:52-54) -- a matrix of 48 hours can
    never reach 251, so `ase` is structurally `0.0` and `rad_core.compliance.ase_band` returns
    `"clearly_acceptable"` for it, a *guaranteed* favourable band from a period that never had the
    hours to earn an unfavourable one. EN 17037 mirrors it: `daylight_hours` retains half of
    whatever it is given, so a 48-record EPW yields `count=24, required=12` and a real-looking
    *"meets the medium recommendation level"* over two days.

    So a band or a level statement computed over a sub-annual matrix is **not a weaker answer, it
    is a different quantity wearing the same name** -- which is why `rad_mcp.results_tool` refuses
    on this predicate rather than caveating. The other two `rad_core` modules stay unchanged: the
    arithmetic over a short window is arithmetically correct, and it is the *judgement* published
    from it that is not; this predicate is what lets the caller refuse to publish one.
    """
    return n_hours in WHOLE_YEAR_HOURS


@dataclass(frozen=True)
class HourIndex:
    """Matrix column -> local standard time, on LM-83's hour-midpoint convention.

    Column `c` is the band **ending** at hour `c + 1` -- EPW's own hour-ending convention, which
    `EPWHourlyRecord.hour` documents -- so the time to judge it at is its midpoint, `c + 0.5`. ch04
    section 2.2.1 says so directly: hourly TMY data is a band average, "hour 9" covers 08:00-09:00,
    and the solar position to use is 08:30. Reading a column as its opening or closing edge shifts
    every period-scoped tally by one hour.

    **The hour is standard time, not clock time.** EPW hours are local standard time at the file's
    fixed UTC offset, and so is every column built from them. LM-83 judges at local clock time,
    daylight saving included (LM-83-12 section 2.2.1), so on a daylight-saving day this hour is one
    hour behind the clock. `lm83_window`'s `clock_shift_h` adds the difference per column (R860);
    this class does not, because the shift depends on the date and the zone, not on the column
    index.

    **`column 0` is assumed to be the hour ending 01:00 on 1 January**, which is what a `.wea`
    written from a full EPW carries and what `gendaymtx` emits a column per. Nothing here checks
    it -- a matrix built from a `.wea` that starts mid-day would have every column mislabelled by
    the offset, and this class would not notice. Carried, not fixed: the check belongs where the
    `.wea`'s own first line is readable (`rad_engine`), not in a pure mapping that is handed an
    integer.

    **This class carries no fields.** It held an `n_hours` that nothing ever read -- neither
    `clock_hour` nor any caller -- so no test could distinguish a right value from a wrong one,
    and a field whose correctness is undefined is worse than no field (whole-branch review,
    test-quality lens). `clock_hour` depends only on the column index; the convention, not the
    matrix length, is what this type names.
    """

    def clock_hour(self, column: int) -> float:
        return (column % 24) + 0.5


def lm83_window(
    n_hours: int, *, clock_shift_h: Sequence[float] | None = None
) -> frozenset[int]:
    """The columns inside LM-83's fixed 08:00-18:00 window, 10 per whole day.

    Every whole year counts 3 650 (R867): a common year by construction, and a leap year of 8 784
    columns because 29 February's 24 columns (`LEAP_DAY_COLUMNS`) are left out -- both editions
    say 3,650 hours (LM-83-12 section 2.2.1; LM-83-22 section 4.4). It still counts the matrix
    rather than assuming one: a matrix of any other length keeps every day it has, and a matrix
    shorter than one day yields whatever columns fall inside the window, which is how a 3-hour
    micro-scene result comes back as an empty window rather than as a year with no daylight.

    **`clock_shift_h`** is the hours to add to each column's standard-time midpoint to read it on
    the local clock -- the zone's daylight-saving amount on a daylight-saving day (1.0 almost
    everywhere; 0.5 at Australia/Lord_Howe), `0.0` otherwise, and negative on the days a zone
    moves its clock back (Africa/Casablanca's Ramadan) -- one entry per column
    (R860). Column `c` is in the window when `8.0 <= (c % 24) + 0.5 + clock_shift_h[c] < 18.0`.
    `None` judges every column at standard time, which is today's window exactly. A sequence whose
    length is not `n_hours` raises `ValueError`: a shift aligned to a different calendar would move
    the wrong columns without any sign of it. On a leap year the shift has 8 784 entries, one per
    matrix column, and is applied before 29 February leaves, so every kept column is still read on
    the clock.
    """
    index = HourIndex()
    if clock_shift_h is None:
        window = frozenset(
            c for c in range(n_hours) if LM83_START_HOUR <= index.clock_hour(c) < LM83_END_HOUR
        )
    else:
        if len(clock_shift_h) != n_hours:
            raise ValueError(
                f"clock_shift_h has {len(clock_shift_h)} entries for a matrix of {n_hours} "
                "columns; the shift is per column, so the two must be the same length"
            )
        window = frozenset(
            c for c in range(n_hours)
            if LM83_START_HOUR <= index.clock_hour(c) + clock_shift_h[c] < LM83_END_HOUR
        )
    if n_hours == _LEAP_YEAR_HOURS:
        window = window.difference(LEAP_DAY_COLUMNS)
    return window


@dataclass(frozen=True)
class DaylightHours:
    """EN 17037's F_time denominator: which columns count, how many, and how the cut was decided."""

    columns: frozenset[int]
    count: int
    required: int
    """`count // 2` -- F_time is 50 % of daylight hours (ch03 B.4)."""
    cut_value: float
    tied_at_cut: int
    """How many hours carry `cut_value`. Above 1, the retained set was decided by the tie-break
    rather than by the ranking, and the caller must say so."""


def daylight_hours(diffuse_lux: Sequence[float | None]) -> DaylightHours:
    """The brightest half of the year's hourly diffuse horizontal illuminances (ch03 B.3.3, B.4).

    **Not sun-up hours.** ch03's own anti-pattern list names that substitution as the mistake, and
    it gives a different answer. The retained set may contain zeros and the discarded set non-zeros;
    the standard says this is expected and does not affect the outcome.

    **The count is half of what it is given**, so a leap-year EPW yields 4 392 and a truncated
    matrix yields half of itself. 4 380 is what a common year happens to produce, not a constant.

    **Ties are broken earliest-hour-first**, because ranking alone does not determine the set: on
    the Athens fixture 23 hours sit exactly on the cut and 15 of them are kept. The rule is
    arbitrary and is therefore reported rather than hidden -- `tied_at_cut` is what the caller
    surfaces.

    Raises `ValueError` on a `None`, which means the EPW did not record that hour: substituting 0
    would rank it as the darkest hour of the year and silently change the retained set.
    """
    if any(v is None for v in diffuse_lux):
        missing = sum(1 for v in diffuse_lux if v is None)
        raise ValueError(
            f"{missing} of {len(diffuse_lux)} hours carry no diffuse horizontal illuminance. "
            "EN 17037 B.3.3 ranks every hour of the year, so a gap cannot be filled with zero -- "
            "that would rank the missing hours as the darkest and change which half is retained."
        )
    values = [float(v) for v in diffuse_lux]  # type: ignore[arg-type]
    count = len(values) // 2
    order = sorted(range(len(values)), key=lambda c: (-values[c], c))
    retained = frozenset(order[:count])
    cut_value = values[order[count - 1]] if count else 0.0
    return DaylightHours(
        columns=retained,
        count=count,
        required=count // 2,
        cut_value=cut_value,
        tied_at_cut=sum(1 for v in values if v == cut_value) if count else 0,
    )


OCCUPIED_REFERENCE_YEAR: Final[int] = 2023
OCCUPIED_START_HOUR: Final[int] = 8
OCCUPIED_END_HOUR: Final[int] = 17


def occupied_hours(n_hours: int) -> frozenset[int]:
    """UDI's analysis period: the corpus code's own occupied mask, 08:00-17:59 Monday to Friday.

    **The weekdays come from a synthetic reference year, not from the weather, and that is what the
    source does.** `docs/specs/radiance-practical-guide.md:794-796` builds
    `pd.date_range(start='2023-01-01', ...)` and masks against *that* -- it never touches the
    weather file. It could not here in any case: `rad_core.weather.EPWHourlyRecord` carries month,
    day and hour and no year, and a TMY year is spliced from a different real year per month, so
    its weekday sequence jumps at every month boundary.

    **A substitution, and labelled as one.** The source's own comment reads "e.g., Weekdays, 8 AM
    to 6 PM" -- an illustrative schedule, not a UDI definition. No source in this corpus states a
    period for UDI at all, so callers attach `UDI_PERIOD_ASSUMED` and publish
    `provenance: "default"`.

    **The count is derived** (R47): 2 600 for a common year is what 260 weekdays x 10 h happens to
    produce, never a constant. A leap-year matrix and a truncated one each answer for themselves.
    """
    start = _date(OCCUPIED_REFERENCE_YEAR, 1, 1)
    return frozenset(
        c for c in range(n_hours)
        if OCCUPIED_START_HOUR <= c % 24 <= OCCUPIED_END_HOUR
        and (start + _timedelta(days=c // 24)).weekday() < 5
    )
