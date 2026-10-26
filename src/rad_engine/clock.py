"""The per-column clock shift that puts LM-83's analysis window on local clock time (ruling R860).

LM-83-12 section 2.2.1 counts 08:00-18:00 "local clock time, accounting for daylight savings time",
and ANSI/IES LM-83-22 keeps it at section 4.4 ("accounting for daylight
saving time if it applies at the building location"). An EPW's hours are local **standard** time at
a fixed UTC offset (`EPWLocation.time_zone_hours`), and it records no daylight-saving rule: the
offset alone cannot decide one (Arizona and Colorado share an offset; the southern hemisphere shifts
in the other half of the year). So the rule comes from a named IANA zone, resolved with `zoneinfo`,
and this module turns it into what `rad_core.periods.lm83_window(..., clock_shift_h=...)` takes: the
hours to add to each column's standard-time midpoint to read it on the clock.

**`REFERENCE_YEAR` is 2024, and it is chosen, not sourced** (an Unsourced values row in
`docs/tool-surface.md`). An EPW row carries month, day and hour and no year -- a TMY splices each
month from a different real year -- but the changeover Sundays depend on the year. 2024 is used
because it is a leap year, so a leap-year EPW's 29 February has a date to land on.

**`.dst()` is never called.** Its sign depends on how a database build encodes a zone: this
machine's Europe/Dublin gives 0 in January and 1 h in July, while the upstream source encodes
Dublin's winter as the negative offset. The shift here is `utcoffset - time_zone_hours`, the
difference between the zone's wall clock and the EPW's own standard time, which every encoding
agrees on.

**No database is not "no daylight saving".** When neither the system database nor the `tzdata`
package is present, every call raises `ClockZoneError` with `code` `"database_missing"` rather than
shifting nothing, because a silent zero shift is exactly the defect R860 fixes.
"""

from __future__ import annotations

import datetime as _dt
from typing import Final, Literal, Sequence
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError, available_timezones

__all__ = ["REFERENCE_YEAR", "ClockZoneError", "check_clock_zone", "clock_shifts"]

REFERENCE_YEAR: Final[int] = 2024


class ClockZoneError(ValueError):
    """A clock zone that cannot be applied. `code` says why: `"unknown"` (not a zone in the
    database), `"offset_mismatch"` (the zone's standard offset is not the EPW's), or
    `"database_missing"` (no time zone database on this machine).

    **These are reasons, not advisory codes**: the caller that loads a weather file maps each to
    its registered refusal code. The constructor takes them as `reason=` rather than `code=` because
    `tests/test_advisories.py` reads every `code="..."` literal in `src/` as an advisory code that
    must be in `docs/tool-surface.md`'s register, and these three are not.
    """

    def __init__(
        self, message: str, *, reason: Literal["unknown", "offset_mismatch", "database_missing"]
    ):
        super().__init__(message)
        self.code = reason


def _available_zones() -> frozenset[str]:
    return frozenset(available_timezones())


def _zone(name: str) -> ZoneInfo:
    """The zone `name` names, **spelled exactly as `available_timezones()` lists it**.

    `ZoneInfo` alone is not the test: it opens a file under the database directory, so on a
    case-insensitive filesystem (macOS APFS) `ZoneInfo("europe/athens")` loads, and a manifest
    recording that spelling reads back unknown on Linux -- `LM83_CLOCK_ZONE_UNRESOLVED` on every
    read. A name in another letter case is refused `unknown`, naming the listed spelling. So is
    `posixrules`, which `ZoneInfo` loads (a file in the database directory, a copy of some other
    zone's rules) but `available_timezones()` does not list as a zone.
    """
    zones = _available_zones()
    if not zones:
        raise ClockZoneError(
            "no time zone database is available on this machine (neither the system database "
            "nor the tzdata package), so a clock zone cannot be applied", reason="database_missing")
    if name not in zones:
        folded = sorted(zone for zone in zones if zone.casefold() == name.casefold())
        spelled = (
            f"; the database spells it {folded[0]!r}, and a zone is matched exactly, letter case "
            "included"
            if folded else ""
        )
        raise ClockZoneError(
            f"{name!r} is not a zone in the time zone database{spelled}", reason="unknown"
        )
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ClockZoneError(
            f"{name!r} is not a zone in the time zone database", reason="unknown"
        ) from exc


def _offset_h(zone: ZoneInfo, when: _dt.datetime) -> float:
    return when.replace(tzinfo=zone).utcoffset().total_seconds() / 3600.0


def check_clock_zone(name: str, time_zone_hours: float) -> None:
    """Refuse a zone that cannot serve an EPW at `time_zone_hours`.

    The zone must resolve, and the **smaller** of its UTC offsets at noon on 15 January and on
    15 July of `REFERENCE_YEAR` must equal the EPW's offset. An EPW is in standard time, and where
    daylight saving moves a clock forward for a season the smaller of a winter and a summer offset
    is the standard one in either hemisphere: Australia/Sydney at +10, Europe/Dublin at 0 and
    Australia/Adelaide at +9.5 pass. *(Corrected 2026-10-12, Phase 25 Branch B final review,
    R860: it said "daylight saving only ever moves a clock forward, so the smaller of a winter and
    a summer offset is the standard one in either hemisphere". False: Africa/Casablanca and
    Africa/El_Aaiun are at +1 on both noon samples and move their clocks **back** an hour for
    Ramadan -- `clock_shifts` is -1.0 on 7 April 2024 at +1 -- so the database's standard time
    for them is +0. For such a zone the minimum is the offset the clock keeps outside the backward
    season: an EPW at +0 is refused `offset_mismatch` (conservative; the remedy is to omit
    `clock_zone`), and one at +1 is accepted, its window placed on the zone's clock, which on the
    days the clock goes back is an hour later in the EPW's standard time.)* The refusal states the
    two samples and the rule, and nothing more: two instants cannot show that a zone keeps one
    offset all year.

    Accepting *either* offset would let Europe/Athens pass at +3, its summer offset, and
    `clock_shifts` would then return -1.0 on every winter row and move the winter window an hour
    later without a word. The minimum also holds for Europe/Dublin, whose database encodes winter
    as the negative-DST season: its `utcoffset` is still 0 in January and +1 in July, and `.dst()`
    is not read.
    """
    zone = _zone(name)
    january, july = (
        _offset_h(zone, _dt.datetime(REFERENCE_YEAR, month, 15, 12)) for month in (1, 7)
    )
    standard = min(january, july)
    if float(time_zone_hours) != standard:
        raise ClockZoneError(
            f"{name!r} is at UTC{january:+g} at noon on 15 January {REFERENCE_YEAR} and "
            f"UTC{july:+g} at noon on 15 July {REFERENCE_YEAR}; the smaller, UTC{standard:+g}, is "
            f"taken as its standard time, and the EPW's standard time is UTC{time_zone_hours:+g}",
            reason="offset_mismatch")


def clock_shifts(
    rows: Sequence[tuple[int, int, int]], *, zone: str, time_zone_hours: float
) -> tuple[float, ...]:
    """Hours to add to standard time to read each row on `zone`'s clock, one per row.

    `rows` are EPW `(month, day, hour)` with `hour` 1..24 hour-ending, placed in `REFERENCE_YEAR`.
    Each row is judged at its midpoint (`hour - 0.5`), the convention `rad_core.periods.HourIndex`
    documents, at the EPW's fixed offset, converted to the zone. The result is the zone's offset
    at that instant minus `time_zone_hours`: the zone's daylight-saving amount on a daylight-saving
    hour (1.0 almost everywhere; 0.5 at Australia/Lord_Howe), `0.0` otherwise -- and negative
    where a zone moves its clock back from the offset `check_clock_zone` took as standard (-1.0 on
    Africa/Casablanca's Ramadan days at +1).
    """
    tz = _zone(zone)
    fixed = _dt.timezone(_dt.timedelta(hours=time_zone_hours))
    out = []
    for month, day, hour in rows:
        midnight = _dt.datetime(REFERENCE_YEAR, month, day, tzinfo=fixed)
        standard = midnight + _dt.timedelta(hours=hour - 0.5)
        out.append(standard.astimezone(tz).utcoffset().total_seconds() / 3600.0 - time_zone_hours)
    return tuple(out)
