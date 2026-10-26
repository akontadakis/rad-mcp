"""EPW parsing and the `.wea` conversion -- pure, runs with no Radiance installed.

Pure: no Radiance, no subprocesses, no file IO. This module never opens the EPW file itself --
`rad_engine.weather` (or whichever layer above reads the disk) hands it the file's *text*, and
`rad_core.weather` returns structured values. `tests/test_purity.py` enforces this by scanning
`src/rad_core/` for a forbidden import or a file-IO call; see that file's own docstring.

**Why this module exists rather than a real `epw2wea` subprocess call, docs/tool-surface.md's own
Layers line notwithstanding.** §8 of that document ("`rad_engine.weather` runs `epw2wea` and
`gendaymtx`") describes the *engine* layer's eventual real conversion via the DAYSIM binary. This
module is the check-4.1/4.2 *pure* layer: parsing the LOCATION line and the per-hour data columns
needs no Radiance at all, and having that logic testable without Radiance installed (docs/plan.md
check 1.4, `tests/test_purity.py`) is worth more than deferring it to a subprocess call that cannot
run in CI without a Radiance install. `to_wea` here produces the same six-line header `epw2wea`
does -- verified field-for-field against the committed `tests/fixtures/weather/athens.wea` -- and
callers that need the byte-identical body `epw2wea` would produce (rounding, any solar-time
correction it applies) go through the real binary in `rad_engine`; this module's `to_wea` is a
straight, documented passthrough of each hour's own DNI/DHI columns, not a reverse-engineering of
`epw2wea`'s internal rounding.

**The LOCATION line, field by field**
(`LOCATION,ATHENS,-,GRC,IWEC Data,167160,37.90,23.73,2.0,15.0`, EPW spec, always line 1):

1. `LOCATION` -- the record marker.
2. City -- `ATHENS`.
3. State/province -- `-` if absent.
4. Country -- `GRC`.
5. Source -- `IWEC Data`.
6. WMO station number -- `167160`.
7. Latitude, degrees, **north-positive** -- `37.90`. Radiance's `.wea` `latitude` is the same
   convention, unchanged.
8. Longitude, degrees, **east-positive** -- `23.73`. This is the field the sign flip applies to;
   see `EPWLocation.longitude_west_deg`.
9. Time zone, hours **east of UTC** -- `2.0`. See `EPWLocation.time_zone_meridian_deg` for the
   degrees conversion the `.wea` header actually uses.
10. Elevation, metres -- `15.0`. Unchanged; the `.wea` `site_elevation` line is the same value.

**The sign flip is the point (check 4.1).** EPW longitude is east-positive, the ordinary
geographic convention; Radiance's `.wea` `longitude` line is **west-positive**
(tests/fixtures/README.md, verified against `epw2wea`'s own output on this fixture: 23.73 degrees E
becomes -23.73 in `athens.wea` -- the flip is pure negation, which is why the two agree to the
digit; a line reading "23.74 E becomes -23.73" is self-contradicting and was corrected here on
2026-08-08, `23.74 E / 37.96 N` being the *`skies/` `gensky` fixture's* Athens, a different site).
`EPWLocation` carries both `longitude_east_deg` (the EPW field,
unflipped) and `longitude_west_deg` (`-longitude_east_deg`, what `to_wea` writes) under
distinguishable names, so a caller reading this model cannot pick up the wrong one by accident the
way a single `longitude` field invites -- this project has already shipped that exact bug once
(ruling history, docs/tool-surface.md #8's "sign convention is reported, not silently handled").
`rad_mcp`'s tool layer is expected to attach `WEA_LONGITUDE_SIGN` at `info` stating both numbers;
this module only computes them.

**The time-zone-to-meridian conversion is derived, not hardcoded.** The EPW's time zone is hours
east of UTC (`2.0` for Athens); the `.wea` header's `time_zone` line is a **meridian in degrees,
west-positive** (`-30` for Athens). One hour of time zone is 15 degrees of longitude
(360 degrees / 24 hours), and the sign flips for the same west-positive reason longitude does:
``time_zone_meridian_deg = -time_zone_hours * 15.0``. `2.0 * 15.0 = 30.0`, negated is `-30.0`,
matching the committed fixture exactly. A hardcoded `-30.0` would silently be wrong for every EPW
that is not Athens; deriving it is what makes the conversion actually general.

**This deliberately diverges from `epw2wea` on half-hour time zones, and the divergence is ours to
keep.** `epw2wea` truncates the time zone to a whole hour before multiplying by 15, so a `+5.5`
file gets meridian `-75` where this module computes `-82.5`; the same applies to `+5.75`, `-3.5` and
`+9.5` -- India, Nepal, Iran, Newfoundland, central Australia. **Ours is the correct one**: Indian
Standard Time is *defined* on the 82.5 degrees E meridian, so true solar time at Delhi (77.2 E) at
12:00 IST is `12 + (77.2 - 82.5)/15 = 11.65` h, which `-82.5` reproduces and `-75` misses by half an
hour. The consequence is not cosmetic -- at Delhi on 21 June, `gensky` at noon gives solar altitude
84.6 degrees under `-75` and 82.8 degrees under `-82.5`, and `gendaymtx` puts the sun in different
patches. Recorded here because the module docstring's *"verified against `epw2wea`'s own output"*
claim is true only for **whole-hour zones**, and a future reader who "fixes" the mismatch to match
`epw2wea` would introduce a real half-hour error for every fractional zone.
`tests/test_weather.py::TestFractionalTimeZone` pins the fractional case so the divergence cannot be
undone silently.

**The albedo quirk (check 4.2).** EPW field 33 (1-indexed) of each hourly data record is albedo;
the documented EPW "missing value" sentinel for it is the literal `999.0` (EnergyPlus Auxiliary
Programs documentation, Weather Converter chapter, missing-value table). IWEC-sourced files
(verified on the committed Athens fixture, all 8760 hours) write a literal `0.000` instead of that
sentinel -- so a naive `albedo != 999.0` check concludes the field is present and reads a perfectly
black ground, silently changing every interreflected daylight calculation the ground plane
contributes to.

**What "present" means here, stated precisely because a future reader must be able to tell this
rule from an accident.** `EPWDataQuality.albedo_present` is `True` only if *at least one* hourly
record carries a plausible, non-sentinel, **non-zero** albedo value. The reasoning:

- A single non-zero hour is enough. One real measurement is evidence the column is a genuine data
  column and not a placeholder that happens to parse as a float -- the alternative (requiring
  *most* or *all* hours to be non-zero) would fail files where snow-free ground legitimately reads
  a small, stable albedo for most of the year, which is a real and common case this module must not
  misclassify as "absent".
- **All-zero across every hour is treated as absent, not as a genuine albedo of zero.** A ground
  albedo of exactly 0.0 for all 8760 hours of a full year is not a physically plausible measurement
  -- real ground (even fresh asphalt) reflects some non-zero fraction of incident light, and no
  natural surface's reflectance is invariant at exactly zero across every season, sun angle and
  weather condition a TMY/IWEC file spans. Indistinguishable, in other words, from the column never
  having been populated. This is deliberately a stronger rule than "not exactly the `999.0`
  sentinel": it catches both the documented sentinel *and* this file's undocumented one, because
  the rule is stated in terms of what a real value looks like rather than in terms of one specific
  placeholder.
- A file with some hours at `999.0` and others with real readings would have those `999.0` hours
  excluded from `albedo` (reported as `None`, mirroring `missing_direct_normal`/`missing_diffuse`)
  but `albedo_present` would still be `True`, because the non-sentinel hours are genuine evidence.

`EPWDataQuality` is what `rad_mcp`'s tool layer reads to decide whether to emit `EPW_ALBEDO_ZERO`
at `warn` and fall back to the project's ground reflectance (`rad_core.manifest.ProvenanceValue`,
`default_ground_reflectance`) -- this module states the fact; the fallback and its provenance are
one layer up.

**Missing direct-normal / diffuse-horizontal irradiance** (EPW fields 15 and 16) use the EPW spec's
documented sentinel, `9999`, unchanged -- unlike albedo, that sentinel is not known to be
misreported by any source fixture this project has, so no analogous "all-zero means absent" rule is
applied to it. A missing hour's `direct_normal_wh_m2`/`diffuse_horizontal_wh_m2` is `None`, and the
count of each feeds `EPWDataQuality.missing_direct_normal`/`missing_diffuse`, which the tool layer
maps to `EPW_MISSING_DATA` at `warn`.

**Only whole-hour records.** Sub-hourly time steps are explicitly deferred (docs/plan.md Phase 4,
"Defers": "Sub-hourly time steps"); `EPWHourlyRecord` carries `month`/`day`/`hour` (EPW's own
1..24 hour-of-day field) and nothing finer. The annual daylight-coefficient path (check 4.7) reads
whole `.ill` columns per hour, which is exactly what this shape supports.

**Robustness.** An EPW is user-supplied data (docs/plan.md check 0.4: every refusal is typed).
`parse_epw` tolerates a BOM, CRLF or LF line endings, and trailing blank lines; it refuses -- with
`EPWParseError`, never a bare `ValueError`/`IndexError` escaping this module -- a missing or
malformed `LOCATION` line, a `LOCATION` line not on the first record, or a data row with the wrong
field count or a non-numeric required field. A file with a well-formed header and zero data rows is
tolerated (`records == ()`), since a header-only read (e.g. a site-mismatch check against
`EPWLocation` alone) has no need of the timeseries.
"""

from __future__ import annotations

import math
from typing import Final

from pydantic import BaseModel, ConfigDict, Field, model_validator

__all__ = [
    "ALBEDO_FIELD_INDEX",
    "DEGREES_PER_HOUR",
    "DIRECT_NORMAL_FIELD_INDEX",
    "DIFFUSE_HORIZONTAL_FIELD_INDEX",
    "DIFFUSE_HORIZONTAL_ILLUMINANCE_FIELD_INDEX",
    "EPW_ALBEDO_SENTINEL",
    "EPW_ILLUMINANCE_SENTINEL_FLOOR",
    "EPW_IRRADIANCE_SENTINEL",
    "EPW_MIN_DATA_FIELDS",
    "EPWDataQuality",
    "EPWError",
    "EPWFile",
    "EPWHourlyRecord",
    "EPWLocation",
    "EPWParseError",
    "WEA_HEADER_LINES",
    "epw_record_at",
    "parse_epw",
    "to_wea",
    "wea_hour_count",
]

#: EPW hourly data record, 1-indexed field positions this module reads. Fields not listed
#: (dry-bulb temperature, wind speed, and the rest of the 35-field row) are outside this phase's
#: scope -- the module docstring's "don't over-build" note.
#:
#: **Measured directly against the committed fixture, not taken from memory of the EPW spec's field
#: table.** Field 14 is Global Horizontal Radiation, *not* an irradiance this module reads at all --
#: an earlier version of this module read fields 14/15 as direct-normal/diffuse-horizontal, which
#: silently wrote global-horizontal into the `.wea` DNI column and true direct-normal into the DHI
#: column. Every value looked physically plausible (both are non-negative irradiances of a similar
#: magnitude), so nothing about the numbers themselves signalled the swap -- only a body-line
#: comparison against real `epw2wea` output caught it
#: (`tests/test_weather.py::TestToWeaDataLines::test_body_dni_dhi_matches_the_real_epw2wea_output`).
#: Athens EPW row for 1 Jan, hour 10 (line 8 + 10 -- the header is 8 lines): field 14 = 111 (global
#: horizontal, unused), field
#: 15 = 8 (direct normal), field 16 = 109 (diffuse horizontal); the real `epw2wea`-generated
#: `athens.wea` line for that hour is `1 1 9.500 8 109`, matching fields 15/16, not 14/15.
DIRECT_NORMAL_FIELD_INDEX = 15
DIFFUSE_HORIZONTAL_FIELD_INDEX = 16
DIFFUSE_HORIZONTAL_ILLUMINANCE_FIELD_INDEX = 19
ALBEDO_FIELD_INDEX = 33

#: The lowest field index this module reads (`ALBEDO_FIELD_INDEX`), so a data row shorter than
#: this cannot possibly carry every value the row model needs -- refused before any field lookup
#: rather than raising `IndexError` out of a `row[32]`.
EPW_MIN_DATA_FIELDS = ALBEDO_FIELD_INDEX

#: Ceiling on how many data records `parse_epw` will build before refusing. A complete hourly year
#: is 8760 (8784 in a leap year), and 52 704 is a **leap year at 10-minute resolution** -- six times
#: finer than the finest sub-hourly step this server has any use for, and 6x the largest real file.
#:
#: This is the pure layer's half of a two-part bound with `rad_engine.weather.MAX_EPW_BYTES`: that
#: one bounds the bytes on disk, this one bounds the rows parsed out of them, and a small file can
#: declare far more rows than a year.
#:
#: **It was 600 000 until 2026-08-08, and the two caps did not compose.** A security pass found that
#: a minimal-width row is ~66 bytes, so 599 999 rows is only ~45 MB -- under the 64 MB byte cap --
#: and both limits were satisfied by a single call that measured **~800 MB peak RSS**. Across
#: `anyio`'s 40-slot thread pool that is ~30 GB, i.e. server-wide memory exhaustion from a
#: well-formed file breaking no declared limit. The cost is per **record**, not per byte, and it was
#: re-measured directly rather than carried forward: **~1 370 bytes of peak RSS per record**, flat
#: from 8 760 to 599 999 rows. At 52 704 that is ~69 MB per call and ~2.7 GB across a saturated
#: pool, which is a bound the byte cap can no longer defeat. (The older note here cited "21-25x the
#: input size"; that ratio is real but is the wrong variable to bound on, because it moves with row
#: width while bytes-per-record does not.)
#:
#: Checked inside the loop rather than against `len(lines)` up front so the refusal names the line
#: where parsing stopped, and so no work is done past the limit.
#: *Since R773 a sub-hourly file is refused at its second row for an hour; the ceiling still bounds
#: the rows built before that refusal.*
MAX_EPW_RECORDS = 52_704

#: EPW spec's documented missing-value sentinel for direct normal / diffuse horizontal irradiance.
EPW_IRRADIANCE_SENTINEL = 9999.0

#: EPW spec's documented missing-value sentinel for albedo. IWEC files write a literal `0.000`
#: instead -- see the module docstring's "what present means" section for why this module does not
#: rely on this sentinel alone.
EPW_ALBEDO_SENTINEL = 999.0

#: EPW marks the illuminance columns missing with a **floor**, not with the exact sentinel the
#: irradiance columns use: anything at or above 999900 is absent data. `_sentinel_or`'s exact
#: equality is therefore wrong here -- 999950 is not 999999, and admitting it would put a value
#: three orders of magnitude above any real sky at the top of EN 17037's rank ordering.
EPW_ILLUMINANCE_SENTINEL_FLOOR: Final[float] = 999900.0

#: 360 degrees / 24 hours. What `EPWLocation.time_zone_meridian_deg` derives the `.wea` `time_zone`
#: line from, rather than a value hardcoded per location.
DEGREES_PER_HOUR = 15.0

#: `place`'s bound, in **characters** of the Python `str` -- not bytes. Chosen, not measured: every
#: `<CITY>_<COUNTRY>` token across this repo's own fixtures is `ATHENS_GRC`, 10 characters, so 64
#: clears every place token in this repo's fixtures by a wide margin (Unsourced row,
#: docs/tool-surface.md). City and country are free-text EPW fields nothing bounds upstream
#: (Ruling R708, Phase 13 Branch C, C7): `_wea_place` truncates to this many characters of
#: `f"{city}_{country}"` rather than writing an unbounded caller-controlled string into the `.wea`
#: header and every place `EPWLocation.place` is echoed from there (a published result field). It
#: errs toward truncating a genuine long place name (cosmetic; a long city can push part or all
#: of the country past the cut) over leaving the string unbounded.
MAX_PLACE_CHARS: Final[int] = 64


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EPWError(ValueError):
    """Something this module refuses to parse or convert.

    Mirrors `rad_core.grids.GridError`'s shape (a class-attribute `code`, read by whichever layer
    eventually builds the wire envelope) -- `rad_core` may not import `rad_mcp`, so this is as
    close to a coded refusal as this layer can get on its own. `code` is `None` where the register
    (docs/tool-surface.md) has no code for the condition; every malformed-EPW case this module
    refuses is a parse failure the client can only fix by supplying a different file, which is
    `ARGUMENT_OUT_OF_RANGE`-shaped territory the tool layer assigns, not this one.
    """

    code: str | None = None
    field: str | None = None

    def __init__(self, message: str, *, code: str | None = None, field: str | None = None) -> None:
        super().__init__(message)
        if code is not None:
            self.code = code
        if field is not None:
            self.field = field


class EPWParseError(EPWError):
    """The EPW text could not be parsed: missing/malformed `LOCATION` line, a data row with too
    few fields, or a required field that is not numeric.

    Never raised as a bare `IndexError`/`ValueError` escaping this module (docs/plan.md check
    0.4) -- every malformed-input branch in `parse_epw` raises this, with the offending line
    number and a description of what was expected, so a caller debugging a real user-supplied file
    gets a message that names the problem rather than a Python traceback.
    """


class EPWLocation(_Model):
    """The parsed EPW `LOCATION` line, plus the two Radiance-convention values `.wea` needs.

    `latitude` and `longitude_east_deg` are the EPW's own fields, unchanged. `longitude_west_deg`
    and `time_zone_meridian_deg` are *derived* -- never independently settable -- so the two
    conventions can never silently disagree with each other. See the module docstring's "sign flip"
    and "time-zone-to-meridian" sections for the derivations.
    """

    place: str
    """`<CITY>_<COUNTRY>`, upper-cased and space-stripped -- the `.wea` `place` token
    (`ATHENS_GRC` for the Athens fixture). Radiance's `.wea` format has no quoting for `place`, so
    this is not the raw EPW city string when that string contains a space or a character `.wea`
    cannot carry unescaped; see `_wea_place` for the exact rule."""

    latitude: float
    """Degrees, north-positive. EPW field 7, unchanged -- the `.wea` `latitude` line is the same
    convention."""

    longitude_east_deg: float
    """Degrees, **east-positive** -- EPW field 8, exactly as the file states it. This is the
    ordinary geographic convention (most-used maps and GPS), not what `.wea` writes."""

    time_zone_hours: float
    """Hours east of UTC -- EPW field 9, unchanged."""

    elevation_m: float
    """Metres above sea level -- EPW field 10, unchanged."""

    @property
    def longitude_west_deg(self) -> float:
        """Degrees, **west-positive** -- what the `.wea` `longitude` line actually carries.

        `-longitude_east_deg`, always. Athens: `23.73` east becomes `-23.73` here, matching the
        committed `athens.wea` fixture exactly (check 4.1).
        """
        return -self.longitude_east_deg

    @property
    def time_zone_meridian_deg(self) -> float:
        """Degrees, west-positive -- what the `.wea` `time_zone` line actually carries.

        `-time_zone_hours * DEGREES_PER_HOUR`. Athens: `2.0` hours east of UTC is `30.0` degrees of
        longitude, negated for the west-positive convention: `-30.0`, matching the committed
        `athens.wea` fixture exactly (check 4.1).
        """
        return -self.time_zone_hours * DEGREES_PER_HOUR


class EPWHourlyRecord(_Model):
    """One hourly row of EPW data, restricted to what this phase's callers need.

    `month`/`day`/`hour` are EPW's own fields (`hour` is 1..24, hour-of-day, EPW's "hour ending"
    convention) -- sub-hourly files are refused by `parse_epw` (R773).
    """

    month: int
    day: int
    hour: int
    """1..24, EPW's hour-of-day field, unchanged."""

    direct_normal_wh_m2: float | None
    """EPW field 15 (`DIRECT_NORMAL_FIELD_INDEX`), Wh/m^2 -- **not** field 14, which is Global
    Horizontal Radiation. `None` when the field carries the documented `9999` missing-value
    sentinel (`EPW_IRRADIANCE_SENTINEL`)."""

    diffuse_horizontal_wh_m2: float | None
    """EPW field 16 (`DIFFUSE_HORIZONTAL_FIELD_INDEX`), Wh/m^2. `None` when the field carries
    `EPW_IRRADIANCE_SENTINEL`."""

    albedo: float | None
    """EPW field 33. `None` when the field carries the documented `999.0` sentinel
    (`EPW_ALBEDO_SENTINEL`) -- **not** `None` merely for reading `0.000`, since a genuine ground
    reading of zero and the IWEC quirk are indistinguishable at the level of one hour. See
    `EPWDataQuality.albedo_present` for the whole-file rule that does tell them apart."""

    diffuse_horizontal_lux: float | None
    """EPW field 19 (`DIFFUSE_HORIZONTAL_ILLUMINANCE_FIELD_INDEX`), lux. `None` when the field is
    at or above the documented missing-value **floor** (`EPW_ILLUMINANCE_SENTINEL_FLOOR`,
    `999900`) -- not the exact-equality sentinel the irradiance fields use, because EPW's
    illuminance columns are documented as "missing if >= 999900", not "missing if == 999999". EN
    17037 B.3.3's daylight-provision denominator is the only consumer of this field this phase
    has."""


class EPWDataQuality(_Model):
    """Data-quality facts about one parsed EPW file -- check 4.2's `albedo_present`, the
    daylight-provision denominator's own `illuminance_present`, plus the two documented-sentinel
    counts for solar irradiance. What `rad_mcp`'s `weather` tool reads to decide whether to emit
    `EPW_ALBEDO_ZERO`/`EPW_MISSING_DATA`, and what `rad_mcp.results_tool`'s EN 17037
    daylight-provision compliance action reads before ever ranking an hour; see the module
    docstring."""

    albedo_present: bool
    """`True` iff at least one hourly record's albedo is a non-sentinel, non-zero value. `False`
    for a file with zero records, every hour at the documented `999.0` sentinel, or -- the quirk
    check 4.2 exists for -- every hour at a literal `0.000`. See the module docstring's "what
    present means" section for the full reasoning."""

    illuminance_present: bool
    """`True` iff at least one hourly record's `diffuse_horizontal_lux` (field 19,
    `DIFFUSE_HORIZONTAL_ILLUMINANCE_FIELD_INDEX`) is a genuine positive reading -- not `None`
    (missing, or at/above `EPW_ILLUMINANCE_SENTINEL_FLOOR`) and not exactly `0.0`. `False` for a
    file with zero records, every hour missing, or every hour reading a genuine `0.0` --
    **the same "all-zero means absent" rule `albedo_present` already applies to a different field**
    (there, an IWEC-source quirk; here, EN 17037 B.3.3's own rank-ordering): a diffuse horizontal
    illuminance of exactly zero for every hour of a full year is not a physically plausible
    measurement, indistinguishable from the column never having been populated, and ranking it
    would let `rad_core.periods.daylight_hours`'s earliest-hour tie-break alone decide the entire
    retained daylight-hours set rather than any real ranking. `rad_mcp.results_tool`'s EN 17037
    daylight-provision action is this flag's one consumer this phase, checked before
    `daylight_hours` is ever called."""

    missing_direct_normal: int
    """Count of hourly records whose field 15 (`DIRECT_NORMAL_FIELD_INDEX`) carries
    `EPW_IRRADIANCE_SENTINEL` (`9999`)."""

    missing_diffuse: int
    """Count of hourly records whose field 16 (`DIFFUSE_HORIZONTAL_FIELD_INDEX`) carries
    `EPW_IRRADIANCE_SENTINEL` (`9999`)."""


class EPWFile(_Model):
    """A fully parsed EPW: the site location, the hourly timeseries, and the data-quality summary.

    `records` is empty for a header-only file (module docstring's robustness section) -- a caller
    that only needs `location` (e.g. an `EPW_SITE_MISMATCH` check) is not forced to also supply a
    complete year of data.
    """

    location: EPWLocation
    records: list[EPWHourlyRecord] = Field(default_factory=list)
    data_quality: EPWDataQuality


# --------------------------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------------------------


def _strip_bom(text: str) -> str:
    return text[1:] if text and text[0] == "﻿" else text


def _split_lines(text: str) -> list[str]:
    """CRLF, LF, or a mix, all tolerated -- `str.splitlines()` handles every EPW line ending this
    module has seen (the committed Athens fixture is itself CRLF) without a manual `\\r` strip that
    would leave a stray `\\r` on the last field of a line missing this module's own newline
    handling would otherwise mishandle."""
    return _strip_bom(text).splitlines()


def _parse_location_line(line: str) -> EPWLocation:
    fields = line.split(",")
    if fields[0].strip().upper() != "LOCATION":
        raise EPWParseError(
            f"expected the file to start with a LOCATION record, found {fields[0]!r} instead -- "
            "the EPW spec requires LOCATION on line 1.",
            field="epw",
        )
    if len(fields) < 10:
        raise EPWParseError(
            f"LOCATION line has {len(fields)} fields, need at least 10 "
            "(marker, city, state, country, source, WMO, latitude, longitude, time_zone, "
            "elevation).",
            field="epw",
        )
    city = fields[1].strip()
    country = fields[3].strip()
    latitude = _parse_float(fields[6], "LOCATION latitude (field 7)")
    longitude_east = _parse_float(fields[7], "LOCATION longitude (field 8)")
    time_zone_hours = _parse_float(fields[8], "LOCATION time zone (field 9)")
    elevation_m = _parse_float(fields[9], "LOCATION elevation (field 10)")
    return EPWLocation(
        place=_wea_place(city, country),
        latitude=latitude,
        longitude_east_deg=longitude_east,
        time_zone_hours=time_zone_hours,
        elevation_m=elevation_m,
    )


def _wea_place(city: str, country: str) -> str:
    """`<CITY>_<COUNTRY>`, matching `epw2wea`'s own `place` token on the Athens fixture exactly
    (`ATHENS_GRC`): upper-cased, each of city and country space-collapsed to nothing, joined by a
    single underscore. Any other whitespace inside either field is dropped for the same reason --
    `.wea`'s `place` line is one whitespace-delimited token, so a literal space would silently
    split it into two fields for whatever reads the file next.

    **Bounded at `MAX_PLACE_CHARS` characters -- of the Python `str`, not its UTF-8 byte length.**
    City and country are free-text EPW fields (fields 2 and 4 of the LOCATION line) that nothing
    upstream bounds, and this string was echoed downstream unbounded: into every `.wea` header
    `to_wea` writes, and into published result fields (`EPWLocation.place`, `weather`'s `site.place`,
    `run_view_and_sunlight`'s `location.place`). No refusal reason carries it. Truncated, not
    refused -- a long but genuine place name is a cosmetic loss, and refusing it would make every
    other field of an otherwise-valid LOCATION line unusable over one long string. The cut is on
    the joined token, so a long city can drop part or all of the country.
    """
    city_token = "".join(city.upper().split())
    country_token = "".join(country.upper().split())
    return f"{city_token}_{country_token}"[:MAX_PLACE_CHARS]


def _parse_float(raw: str, description: str) -> float:
    """Parse one EPW numeric field, refusing the values `float()` accepts but weather cannot be.

    **`float()` accepts `inf`, `-inf` and `nan`**, and every one of them parses out of a plain text
    field. Left in, they reach the `.wea` header: a `latitude nan` was observed written and reported
    as a clean success with no advisory, and `1e308` through `:.2f` produced a latitude line of some
    310 digits. That is engine invariant 3's own failure shape -- a file that parses, a result that
    looks like a result, and a number nothing downstream can be right about. `math.isfinite` is the
    whole check, and it belongs here because this is the single chokepoint every numeric EPW field
    passes through.
    """
    try:
        value = float(raw.strip())
    except ValueError as exc:
        raise EPWParseError(f"{description} is not numeric: {raw!r}", field="epw") from exc
    if not math.isfinite(value):
        raise EPWParseError(
            f"{description} is not a finite number: {raw!r}. `inf` and `nan` parse as floats but "
            "are not measurements -- written into a `.wea` header they would produce a sky matrix "
            "no downstream result could be right about.",
            field="epw",
        )
    return value


def _sentinel_or(value: float, sentinel: float) -> float | None:
    return None if value == sentinel else value


def _parse_data_line(line: str, line_number: int) -> EPWHourlyRecord:
    fields = line.split(",")
    if len(fields) < EPW_MIN_DATA_FIELDS:
        raise EPWParseError(
            f"line {line_number}: data row has {len(fields)} fields, need at least "
            f"{EPW_MIN_DATA_FIELDS} to reach the albedo field (field {ALBEDO_FIELD_INDEX}).",
            field="epw",
        )
    month = _parse_int(fields[1], f"line {line_number}: month (field 2)")
    day = _parse_int(fields[2], f"line {line_number}: day (field 3)")
    hour = _parse_int(fields[3], f"line {line_number}: hour (field 4)")
    direct_normal = _parse_float(
        fields[DIRECT_NORMAL_FIELD_INDEX - 1],
        f"line {line_number}: direct normal irradiance (field {DIRECT_NORMAL_FIELD_INDEX})",
    )
    diffuse_horizontal = _parse_float(
        fields[DIFFUSE_HORIZONTAL_FIELD_INDEX - 1],
        f"line {line_number}: diffuse horizontal irradiance "
        f"(field {DIFFUSE_HORIZONTAL_FIELD_INDEX})",
    )
    albedo = _parse_float(
        fields[ALBEDO_FIELD_INDEX - 1], f"line {line_number}: albedo (field {ALBEDO_FIELD_INDEX})"
    )
    raw_lux = _parse_float(
        fields[DIFFUSE_HORIZONTAL_ILLUMINANCE_FIELD_INDEX - 1],
        f"line {line_number}: diffuse horizontal illuminance "
        f"(field {DIFFUSE_HORIZONTAL_ILLUMINANCE_FIELD_INDEX})",
    )
    diffuse_horizontal_lux = None if raw_lux >= EPW_ILLUMINANCE_SENTINEL_FLOOR else raw_lux
    return EPWHourlyRecord(
        month=month,
        day=day,
        hour=hour,
        direct_normal_wh_m2=_sentinel_or(direct_normal, EPW_IRRADIANCE_SENTINEL),
        diffuse_horizontal_wh_m2=_sentinel_or(diffuse_horizontal, EPW_IRRADIANCE_SENTINEL),
        albedo=_sentinel_or(albedo, EPW_ALBEDO_SENTINEL),
        diffuse_horizontal_lux=diffuse_horizontal_lux,
    )


def _parse_int(raw: str, description: str) -> int:
    try:
        return int(raw.strip())
    except ValueError as exc:
        raise EPWParseError(f"{description} is not an integer: {raw!r}", field="epw") from exc


#: The eight EPW header record markers, in file order, that precede the first data row (EPW spec).
#: Used only to know how many leading lines to skip once LOCATION is confirmed on line 1 -- this
#: module does not parse the other seven records, so it does not validate their content or order,
#: only that eight header lines are present before data begins.
_EPW_HEADER_LINE_COUNT = 8


def _albedo_present(records: list[EPWHourlyRecord]) -> bool:
    """See the module docstring's "what present means" section. `True` iff at least one record's
    albedo is non-`None` (not the documented sentinel) and non-zero."""
    return any(r.albedo is not None and r.albedo != 0.0 for r in records)


def _illuminance_present(records: list[EPWHourlyRecord]) -> bool:
    """See `EPWDataQuality.illuminance_present`'s own docstring -- `_albedo_present`'s "all-zero
    means absent" reasoning, applied to `diffuse_horizontal_lux` instead of `albedo`. `True` iff
    at least one record's diffuse horizontal illuminance is non-`None` and strictly positive.
    Moved here from `rad_mcp.results_tool` (coordinator review, Task 13): an EPW data-quality rule
    belongs beside its sibling `_albedo_present`, not re-derived one layer up."""
    return any(
        r.diffuse_horizontal_lux is not None and r.diffuse_horizontal_lux > 0.0 for r in records
    )


def parse_epw(text: str) -> EPWFile:
    """Parse EPW text into structured location, hourly records, and data-quality facts.

    Tolerates a BOM, CRLF/LF line endings, and trailing blank lines. Refuses -- with
    `EPWParseError`, always -- a missing or malformed `LOCATION` line (must be line 1), fewer than
    the eight EPW header lines, a data row that is too short or has a non-numeric field this
    module reads, or a second row for a (month, day, hour) already read (R773). A well-formed
    header with zero data rows is accepted (`records == []`); see the
    module docstring.
    """
    lines = _split_lines(text)
    if not lines:
        raise EPWParseError("the file is empty -- no LOCATION line to parse.", field="epw")

    location = _parse_location_line(lines[0])

    if len(lines) < _EPW_HEADER_LINE_COUNT:
        # Header-only-but-incomplete: fewer than the eight EPW header lines exist at all, so there
        # is no well-defined boundary between "header" and "data". Tolerated as location-only --
        # not refused -- because every field this module needed (LOCATION) already parsed
        # successfully, and a caller reading only `.location` should not be refused a file that
        # gave it everything it asked for.
        return EPWFile(
            location=location,
            records=[],
            data_quality=EPWDataQuality(
                albedo_present=False, illuminance_present=False, missing_direct_normal=0,
                missing_diffuse=0,
            ),
        )

    records: list[EPWHourlyRecord] = []
    seen: set[tuple[int, int, int]] = set()
    for offset, line in enumerate(lines[_EPW_HEADER_LINE_COUNT:]):
        if not line.strip():
            continue  # trailing blank lines, tolerated
        line_number = _EPW_HEADER_LINE_COUNT + offset + 1
        if len(records) >= MAX_EPW_RECORDS:
            raise EPWParseError(
                f"line {line_number}: this file carries more than {MAX_EPW_RECORDS} data records, "
                "which is not a weather year. A complete hourly year is 8760 records (8784 in a "
                "leap year), and even minute-resolution data is 525 600. Parsing stopped here "
                "rather than building a model per row for a file that is not weather data.",
                field="epw",
            )
        record = _parse_data_line(line, line_number)
        key = (record.month, record.day, record.hour)
        if key in seen:
            # R773 (owner decision E7 of 2026-10-03): one row per hour. A sub-hourly file gave a
            # Perez sky the hour's first row and the annual `.wea` one line per row.
            raise EPWParseError(
                f"line {line_number}: more than one row for month {record.month}, day "
                f"{record.day}, hour {record.hour}. This server reads hourly EPW files only: a "
                "sub-hourly file, or one with a duplicated row, would have a sky read the hour's "
                "first row and the annual sky matrix read every row as an hour of its own. Supply "
                "an hourly EPW.",
                field="epw",
            )
        seen.add(key)
        records.append(record)

    missing_direct_normal = sum(1 for r in records if r.direct_normal_wh_m2 is None)
    missing_diffuse = sum(1 for r in records if r.diffuse_horizontal_wh_m2 is None)

    return EPWFile(
        location=location,
        records=records,
        data_quality=EPWDataQuality(
            albedo_present=_albedo_present(records),
            illuminance_present=_illuminance_present(records),
            missing_direct_normal=missing_direct_normal,
            missing_diffuse=missing_diffuse,
        ),
    )


def epw_record_at(epw: EPWFile, *, month: int, day: int, hour: float) -> EPWHourlyRecord:
    """The hourly record a Perez sky at `month`/`day`/`hour` reads (ruling R765): the one whose
    `.wea` mid-hour -- its EPW hour minus 0.5, the time `to_wea` writes and `gendaymtx` reads --
    equals `hour`. EPW rows are hourly means ending at their hour, so 12.5 is the row ending at
    13:00. Any other hour is **refused, never rounded** (owner decision of 2026-10-02): a different
    hour is a different sun. The refusal names the two nearest valid `hour` values in the caller's
    own convention -- the sky dict's decimal hour, one of 0.5, 1.5, ... 23.5 -- so near midnight
    both are still real rows (`hour=0.2` names 0.5 and 1.5).

    Raises `EPWError(field="sky")` for an hour that matches no row, and for a `month`/`day` the
    file holds no row for.
    """
    if not math.isfinite(hour) or hour % 1.0 != 0.5 or not 0.5 <= hour <= 23.5:
        valid = [h + 0.5 for h in range(24)]
        nearest = sorted(sorted(valid, key=lambda h: (abs(h - hour), h))[:2])
        raise EPWError(
            f"hour={hour:g} matches no row of the weather file and is not rounded: EPW rows are "
            "hourly means, and a Perez sky reads the row at its middle (EPW hour - 0.5, the .wea "
            f"convention). The nearest valid values are hour={nearest[0]:g} or "
            f"hour={nearest[1]:g}.",
            field="sky",
        )
    for record in epw.records:
        if record.month == month and record.day == day and record.hour - 0.5 == hour:
            return record
    raise EPWError(
        f"the weather file has no row for {month}/{day} at {hour:g}: no such day, or a file "
        "that does not cover it.",
        field="sky",
    )


# --------------------------------------------------------------------------------------------
# .wea conversion
# --------------------------------------------------------------------------------------------

#: `weather_data_file_units`: `1` means the two data columns are direct normal and diffuse
#: horizontal irradiance (W/m^2-equivalent, hourly) -- the only mode this module ever writes, and
#: the mode the committed `athens.wea` fixture uses. Radiance's `.wea` format also defines `2`
#: (illuminance) which no path in this phase produces.
_WEA_UNITS_IRRADIANCE = 1


def _format_lat_lon(value: float) -> str:
    """Two decimal places -- matches `latitude 37.90` / `longitude -23.73` in the committed
    `athens.wea` fixture exactly, and avoids float repr artefacts (`23.729999999999997`) reaching
    the file."""
    return f"{value:.2f}"


def _format_elevation(value: float) -> str:
    """One decimal place -- matches `site_elevation 15.0` in the committed `athens.wea` fixture.
    A different precision from `_format_lat_lon`: the fixture itself uses two different formats for
    the two kinds of field, and matching each verbatim (rather than picking one shared precision
    that happens to agree with only one of them) is what check 4.1's exact-match assertion needs."""
    return f"{value:.1f}"


def _format_time_zone(value: float) -> str:
    """`.wea`'s `time_zone` line is an integer degree meridian on the committed fixture (`-30`,
    never `-30.0`) -- an integral meridian formats with no decimal point, matching it exactly.

    **Not every EPW time zone is a whole hour**, so this is not refused for a fractional result:
    India Standard Time is UTC+5:30 (5.5 hours), which is `-82.5` degrees of meridian, a real value
    a genuine EPW can carry in field 9. Refusing it would refuse a valid, if less common, input for
    no physical reason -- the committed fixture only ever exercises the whole-hour case, so this
    branch is untested against a real file, but it is not speculative: it is the direct consequence
    of `DEGREES_PER_HOUR * time_zone_hours` for a `time_zone_hours` this module does not and should
    not constrain to whole hours.
    """
    return str(int(value)) if value == int(value) else f"{value:g}"


def to_wea(epw: EPWFile) -> str:
    """Render an `EPWFile` as `.wea` text: the six-line header, then one `month day hour DNI DHI`
    line per hourly record, in file order.

    The header is verified field-for-field against the committed `tests/fixtures/weather/
    athens.wea` (check 4.1): `place`, `latitude` and `site_elevation` unchanged from the EPW;
    `longitude` and `time_zone` sign-flipped into Radiance's west-positive convention (see
    `EPWLocation.longitude_west_deg`/`time_zone_meridian_deg`); `weather_data_file_units` is always
    `1` (irradiance), the only mode this module produces.

    Each data line's `hour` is the record's own EPW hour-of-day minus 0.5, matching the solar
    mid-hour convention `.wea` data lines use (`1 1 0.500 ...` for EPW hour 1, `.5` for hour 2, and
    so on through `23.500` for hour 24) -- **not** an independent recomputation of solar time, just
    the documented half-hour offset from the EPW's own hour-ending convention. DNI/DHI are each
    record's own field, unconverted; a record with a missing-sentinel (`None`) value writes `0`,
    the same value `.wea` uses for a genuine zero-irradiance night hour -- `EPWDataQuality` is
    where a caller checks whether any hour was actually missing before trusting a `0` in this
    output as "no direct sun" rather than "sentinel, treated as none".

    Refuses (`EPWError`) an `EPWFile` with zero records -- an empty `.wea` timeseries is not a
    weather file `gendaymtx` can build a sky matrix from, and the failure is cheaper to see here
    than inside a Radiance subprocess several layers away.
    """
    if not epw.records:
        raise EPWError(
            "cannot render .wea: this EPWFile has no hourly records (a header-only parse). "
            "gendaymtx needs a full timeseries, not just a location.",
            field="records",
        )

    location = epw.location
    header = [
        f"place {location.place}",
        f"latitude {_format_lat_lon(location.latitude)}",
        f"longitude {_format_lat_lon(location.longitude_west_deg)}",
        f"time_zone {_format_time_zone(location.time_zone_meridian_deg)}",
        f"site_elevation {_format_elevation(location.elevation_m)}",
        f"weather_data_file_units {_WEA_UNITS_IRRADIANCE}",
    ]

    data_lines = [
        f"{record.month} {record.day} {record.hour - 0.5:.3f} "
        f"{_wea_component(record.direct_normal_wh_m2)} "
        f"{_wea_component(record.diffuse_horizontal_wh_m2)}"
        for record in epw.records
    ]

    return "\n".join(header + data_lines) + "\n"


#: The fixed header length `to_wea` writes -- `place`, `latitude`, `longitude`, `time_zone`,
#: `site_elevation`, `weather_data_file_units`, in that order, one line each, unconditionally.
#: `wea_hour_count` subtracts this from a `.wea` file's own line count to recover the record count
#: `to_wea` was given, without re-parsing the data lines themselves.
WEA_HEADER_LINES: Final[int] = 6


def wea_hour_count(text: str) -> int:
    """The number of hourly records a `.wea` file's own text represents -- `to_wea`'s inverse on the
    one fact this module's other reader (`rad_engine.weather.read_epw`, over the *source* `.epw`)
    cannot answer once the source file is gone: how many data lines are actually in the `.wea` this
    project already wrote and already validated exists.

    **Exists for exactly one caller.** `rad_mcp.annual_tool._weather` measures `ChainRequest.n_hours`
    from the registered `.epw` when it can, because that is the file `to_wea` derived the row count
    from in the first place (one arithmetic path, ruling R30) -- but the `.epw` is the caller's own
    external file (`weather_tool._load`'s own docstring: "the EPW is the user's file and is never
    deleted by this server"), and it can move, be tidied away, or sit on removable media, in which
    case re-reading it is a request this server cannot always satisfy. The `.wea` this project wrote
    into `04_skies/` is never any of those things -- it is a server-owned copy inside the project root
    -- so this function is the fallback that keeps `n_hours` measured rather than either defaulted
    silently to `ANNUAL_TIMESTEPS` or turned into a hard failure of every subsequent annual run over
    an external file nothing else in this server's own reuse story depends on.

    **Pure, and deliberately not a second EPW-record reader.** It knows nothing about EPW fields,
    sentinels, or units -- only `to_wea`'s own fixed six-line header (`WEA_HEADER_LINES`) and the
    fact that `to_wea` writes exactly one data line per hourly record, verified byte-for-byte against
    a real `epw2wea` run (`tests/test_weather.py::TestToWeaDataLines`). Splitting on `"\n"` and
    dropping a trailing empty element (the file's own trailing newline, `to_wea`'s own `+ "\n"`)
    counts lines the same way a human reading the file would, not by trusting a record separator this
    function invents.

    Returns `0` for text shorter than the header (a corrupt or header-only file), never negative --
    a `.wea` with no data lines is a fact worth reporting as zero hours, not raising over, since the
    caller already knows the file opened and read successfully to reach this function at all.
    """
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines = lines[:-1]
    return max(0, len(lines) - WEA_HEADER_LINES)


def _wea_component(value: float | None) -> str:
    """A `.wea` DNI/DHI column: the record's own value, or `0` for a missing-sentinel hour --
    see `to_wea`'s docstring on why this is not the same claim as a measured zero."""
    magnitude = 0.0 if value is None else value
    return str(int(magnitude)) if magnitude == int(magnitude) else f"{magnitude:g}"
