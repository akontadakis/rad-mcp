"""Where a sky is: the site its `sky` dict states, or the one the project's weather file records
(ruling R762, owner decisions D2 and D3 of 2026-10-02).

Every tool that generates a sky from a `sky` dict -- `run_point_in_time`, `run_render`,
`run_glare` -- calls `resolve_sky_inputs` **above its `dry_run` return**, so a dry run refuses what
the real run refuses (check 3.9). `run_glare` calls it on its render path only: with `image=` it
generates no sky. Until R762 a sky with no site ran at `gensky`'s own default site
in California, and a sky with `longitude` took it west-positive while the `weather` tool and
`run_view_and_sunlight` speak east-positive (design F5).

The rules, in order:

1. A dict that is not a sky this server knows, or no dict at all, reads no site: the type refusal,
   the `.rad`-path refusal and the daylight-factor substitution own those cases.
2. All three of `latitude`, `longitude_east`, `meridian_east` given: that is the site. Each must be
   a finite number of degrees in range; a bool is not a number here.
3. Some but not all: refused. Half a site from the call and half from the weather file describe
   one place only if they happen to agree.
4. None: the project's weather inputs. None registered -> `SkySiteRequired`. More than one that
   describe different sites -> refused (`run_view_and_sunlight`'s rule, now shared). One site ->
   that site, reported by `SKY_LOCATION_FROM_EPW`.

A Perez sky is read first by `_perez_weather_route` (R765, docs/tool-surface.md §10's route 1).
When it names `weather`, or names no source and gives no site in a project with exactly one
weather input, the site and `gendaylit -W`'s `(dni, dhi)` both come from that file, reported by
`SKY_LOCATION_FROM_EPW` and `SKY_IRRADIANCE_FROM_EPW`. Otherwise the rules above apply.

`read_registered_sites` is `run_view_and_sunlight`'s site reader, moved here unchanged in
behaviour: it re-contains each EPW path against the allowed roots before it reads it, checks the
bytes against the registered digest in the same read (R707), and refuses a LOCATION line that is
not a place on Earth.

No physics: the east-positive site and the meridian derived from a time zone are
`rad_engine.sky.site_from_epw_location`'s.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final, Literal

from rad_core.weather import EPWError, EPWFile, EPWLocation, epw_record_at
from rad_engine.project import LoadedProject
from rad_engine.sky import (
    CIE_SKY_TYPES,
    SkyError,
    SkySite,
    SkySiteRequired,
    site_from_epw_location,
    sky_integer_token,
    sky_irradiance_from_epw_advisory,
    sky_location_from_epw_advisory,
)
from rad_engine.weather import WeatherError, read_epw_with_digest
from rad_mcp.advisories import to_wire
from rad_mcp.envelope import Advisory
from rad_mcp.paths import contain
from rad_mcp.runtime import Runtime
from rad_mcp.unknown_arguments import UnknownKeysRefused
from rad_mcp.weather_tool import _INPUT_PREFIX as _WEATHER_PREFIX
from rad_mcp.weather_tool import _files_of

__all__ = [
    "SKY_SITE_KEYS",
    "RegisteredSite",
    "RegisteredSiteError",
    "SkyInputs",
    "SkySiteKeysRefused",
    "read_registered_sites",
    "resolve_sky_inputs",
    "site_from_keys",
]

#: The three keys a `sky` dict states its site with (R762). Degrees, east-positive.
SKY_SITE_KEYS: Final = ("latitude", "longitude_east", "meridian_east")

#: Each key's magnitude bound, degrees.
_SITE_BOUNDS: Final = {"latitude": 90.0, "longitude_east": 180.0, "meridian_east": 180.0}

#: Every sky type whose generator reads a site.
_SITED_TYPES: Final = frozenset(CIE_SKY_TYPES) | {"perez"}


@dataclass(frozen=True)
class RegisteredSite:
    """One committed weather input's id and its parsed LOCATION line."""

    weather: str
    location: EPWLocation


class RegisteredSiteError(ValueError):
    """A committed weather input whose site cannot be read: no EPW file, unreadable, drifted from
    its registered digest, or not a place on Earth. The message is the refusal's reason.

    ``code`` is set where the register allocates one: a drifted file is `WEATHER_INPUT_DRIFTED`
    (R830, register N-79), with ``severity``; every other reason keeps `None` and its prose
    refusal. `resolve_sky_inputs` passes both to its `SkyError` with `field="project"`, and
    `run_view_and_sunlight` attaches them the same way."""

    code: str | None = None
    severity: Literal["info", "warn", "error"] = "error"

    def __init__(
        self, message: str, *, code: str | None = None,
        severity: Literal["info", "warn", "error"] = "error",
    ) -> None:
        super().__init__(message)
        self.code = code
        self.severity = severity


def _coded_sky_error(message: str, exc: Exception) -> SkyError:
    """A `SkyError` carrying ``exc``'s register code, when it is a coded `RegisteredSiteError`,
    with `field="project"`: the refusal is about a file the project holds, not an argument."""
    code = exc.code if isinstance(exc, RegisteredSiteError) else None
    error = SkyError(message, code=code)
    if isinstance(exc, RegisteredSiteError) and code is not None:
        error.field = "project"
        error.severity = exc.severity
    return error


class SkySiteKeysRefused(UnknownKeysRefused):
    """A `sky` dict whose site keys are refused as a set -- part of a site, or a value that is not
    a place (R762). A subclass, so every handler that shapes `UnknownKeysRefused` shapes this."""


@dataclass(frozen=True)
class SkyInputs:
    """What `resolve_sky_inputs` read for one `sky` argument, above `dry_run`."""

    site: SkySite | None
    advisories: list[Advisory] = field(default_factory=list)
    epw_irradiance: tuple[float, float] | None = None
    """`(dni, dhi)` read from a weather file for a Perez sky (R765, `_perez_weather_route`);
    `None` otherwise."""


def _refusal(message: str) -> SkySiteKeysRefused:
    return SkySiteKeysRefused(
        [Advisory(code="ARGUMENT_OUT_OF_RANGE", severity="error", field="sky", message=message)]
    )


def read_registered_sites(runtime: Runtime, loaded: LoadedProject) -> list[RegisteredSite]:
    """Every committed weather input's site, in manifest order; `[]` when there is none.

    Moved from `rad_mcp.view_sunlight_tool._resolve_site` (R762) with its messages unchanged, so
    that tool's refusals read as before. Raises `RegisteredSiteError` for the first input whose
    site cannot be trusted; `contain`'s own refusals propagate as they did there.
    """
    sites: list[RegisteredSite] = []
    for record in loaded.manifest.inputs:
        if record.kind != "weather":
            continue
        site, _parsed = _read_one_site(runtime, record)
        sites.append(site)
    return sites


def _read_one_site(runtime: Runtime, record: Any) -> tuple[RegisteredSite, EPWFile]:
    """One committed weather input's site and its parsed EPW, read and checked as
    `read_registered_sites` describes (R707's digest, R762's place check). Raises
    `RegisteredSiteError`; `contain`'s own refusals propagate. Split out by R765 so the Perez
    weather route reads the same checked bytes the site comes from."""
    epw, _wea = _files_of(record)
    if epw is None:
        raise RegisteredSiteError(
            f"the weather input {record.id!r} records no EPW file, so there is no LOCATION "
            "line to read the site from. Re-register it with weather(action='load', epw=...)."
        )
    file_ref = next((f for f in record.files if Path(f.path) == epw), None)
    if file_ref is None:
        raise RegisteredSiteError(
            f"the EPW of the weather input {record.id!r} is not one of that input's own registered files."
        )
    try:
        current_digest, parsed = read_epw_with_digest(contain(epw, runtime.roots))
    except (WeatherError, EPWError) as exc:
        raise RegisteredSiteError(
            f"the weather input {record.id!r} could not be read for its site: {exc} "
            "Re-load it with weather(action='load', epw=...)."
        ) from exc
    if current_digest != file_ref.digest:
        raise RegisteredSiteError(
            f"the weather input {record.id!r} has drifted from its registered digest "
            f"(expected {file_ref.digest}, found {current_digest}): its bytes on disk no "
            "longer match what was registered, so this call cannot trust the site it would "
            "read. Re-load it with weather(action='load', epw=...).",
            code="WEATHER_INPUT_DRIFTED", severity="error",
        )
    location = parsed.location
    if not (-90.0 <= location.latitude <= 90.0
            and -180.0 <= location.longitude_east_deg <= 180.0):
        raise RegisteredSiteError(
            f"the weather input {record.id!r} gives its site as latitude {location.latitude!r}, "
            f"longitude {location.longitude_east_deg!r} on its LOCATION line, which is not a "
            "place on Earth: latitude must lie in -90 .. 90 degrees and longitude in "
            "-180 .. 180. Re-load a weather file for this building with "
            "weather(action='load', epw=...)."
        )
    return RegisteredSite(weather=record.id, location=location), parsed


def site_from_keys(sky_arg: dict[str, Any]) -> SkySite | None:
    """The site a `sky` dict states, `None` when it states none. A key whose value is `null` is
    absent. Refuses part of a site, and a value that is not a finite number of degrees in range
    (`SkySiteKeysRefused`, `ARGUMENT_OUT_OF_RANGE`, `field="sky"`)."""
    given = [key for key in SKY_SITE_KEYS if sky_arg.get(key) is not None]
    if not given:
        return None
    if len(given) < len(SKY_SITE_KEYS):
        missing = [key for key in SKY_SITE_KEYS if key not in given]
        raise _refusal(
            f"sky gives {', '.join(given)} but not {', '.join(missing)}. A site is all three "
            "(latitude, longitude_east, meridian_east: degrees, east-positive) or none; with none "
            "it is read from the project's registered weather file. Part of a site from this call "
            "and the rest from the weather file is refused, because the two describe one place "
            "only if they happen to agree. Give all three, or remove the ones given."
        )
    values: dict[str, float] = {}
    for key in SKY_SITE_KEYS:
        raw = sky_arg[key]
        bound = _SITE_BOUNDS[key]
        # No `math.isfinite`: it converts to float and raises OverflowError for a JSON integer
        # like 10**400. The bound comparison is exact for an int, and False for nan and inf.
        if (isinstance(raw, bool) or not isinstance(raw, (int, float))
                or not -bound <= raw <= bound):
            raise _refusal(
                f"sky.{key} must be a finite number of degrees between {-bound:g} and {bound:g} "
                f"(east-positive), but got {raw!r}."
            )
        values[key] = float(raw)
    return SkySite(**values)


def resolve_sky_inputs(runtime: Runtime, loaded: LoadedProject, sky_arg: object) -> SkyInputs:
    """The module docstring's four rules, after the Perez weather route. Call it after
    `point_in_time_tool.sky_key_advisories` has passed the dict's keys, and above `dry_run`."""
    sky_type = sky_arg.get("type") if isinstance(sky_arg, dict) else None
    if not isinstance(sky_type, str) or sky_type not in _SITED_TYPES:
        return SkyInputs(site=None)
    if sky_type == "perez":
        named = sky_arg.get("weather")
        measured = sky_arg.get("irradiance") is not None or sky_arg.get("illuminance") is not None
        if named is not None or not measured:
            route = _perez_weather_route(runtime, loaded, sky_arg, named)
            if route is not None:
                return route
    given = site_from_keys(sky_arg)
    if given is not None:
        return SkyInputs(site=given)
    try:
        registered = read_registered_sites(runtime, loaded)
    except RegisteredSiteError as exc:
        raise _coded_sky_error(str(exc), exc) from exc
    if not registered:
        raise SkySiteRequired(
            f"sky type {sky_arg.get('type')!r} needs a site, and this call gives none and project "
            f"{loaded.paths.name!r} has no registered weather file to read one from. gensky and "
            "gendaylit place the sun from the latitude, longitude and standard meridian; without "
            "them they use their own built-in site in California (37.8 N, 122.0 W) and say nothing "
            "(ruling R762). Pass latitude, longitude_east and meridian_east (degrees, "
            "east-positive: Athens is 37.97, 23.72, 30), or load this building's weather with "
            "weather(action='load', epw=..., name=...)."
        )
    distinct = {
        (s.location.latitude, s.location.longitude_east_deg, s.location.time_zone_hours)
        for s in registered
    }
    if len(distinct) > 1:
        listed = "; ".join(
            f"{s.weather}: latitude {s.location.latitude}, longitude "
            f"{s.location.longitude_east_deg} E, UTC{s.location.time_zone_hours:+g}"
            for s in registered
        )
        raise SkyError(
            "this project has more than one registered weather input and they describe different "
            f"sites ({listed}). A sky takes its site from the weather file only when there is one "
            "site to take. Pass latitude, longitude_east and meridian_east in the sky, or remove "
            "the weather inputs that are not this building's with weather(action='remove', "
            "name=...).",
            code=None,
        )
    chosen = registered[0]
    site = site_from_epw_location(chosen.location)
    return SkyInputs(
        site=site,
        advisories=[to_wire(sky_location_from_epw_advisory(site, weather=chosen.weather), field="sky")],
    )


def _perez_weather_route(
    runtime: Runtime, loaded: LoadedProject, sky_arg: dict[str, Any], named: object
) -> SkyInputs | None:
    """docs/tool-surface.md §10's route 1 (R765): `weather=<name>`, or no source at all in a project
    with exactly one weather input. The site and `(dni, dhi)` both come from that file's checked
    bytes; the row is `rad_core.weather.epw_record_at`'s (the `.wea` mid-hour, any other hour
    refused, never rounded).

    `None` -> the module docstring's site rules apply and no irradiance is read. That happens when
    the sky names no source and the project does not hold exactly one weather input, and when it
    names no source but gives its own site: a site from the call is never replaced by the file's,
    so that sky has no irradiance source and `resolve_perez_inputs` refuses it as "none was given".
    """
    site_given = any(sky_arg.get(key) is not None for key in SKY_SITE_KEYS)
    if named is not None:
        if site_given:
            raise _refusal(
                f"sky names weather={named!r} and gives a site too. With weather the site is that "
                "file's LOCATION line; give one of the two."
            )
        if sky_arg.get("irradiance") is not None or sky_arg.get("illuminance") is not None:
            raise _refusal(
                f"sky names weather={named!r} and a measured irradiance or illuminance pair; both "
                "are gendaylit's input. Give one source."
            )
    elif site_given:
        return None
    records = [r for r in loaded.manifest.inputs if r.kind == "weather"]
    if named is None:
        if len(records) != 1:
            return None
        record = records[0]
    else:
        record = next((r for r in records if r.id in {str(named), f"{_WEATHER_PREFIX}{named}"}), None)
        if record is None:
            known = sorted(r.id.removeprefix(_WEATHER_PREFIX) for r in records)
            raise SkyError(
                f"sky.weather={named!r} names no weather input in project {loaded.paths.name!r}. "
                + (f"Registered: {', '.join(known)}." if known else "It has none.")
                + " Load one with weather(action='load', epw=..., name=...).",
                code=None,
            )
    month, day, hour = sky_arg.get("month"), sky_arg.get("day"), sky_arg.get("hour")
    if month is None or day is None or hour is None:
        raise SkyError("a perez sky needs month, day and hour to place the sun "
                       "(docs/tool-surface.md §10).", code=None)
    # The engine's own month/day range check (`SkyError`, uncaught here); `hour` is checked by
    # `epw_record_at`. `OverflowError` is a JSON integer too large for a float.
    month_n = int(sky_integer_token(month, field="month", low=1, high=12))
    day_n = int(sky_integer_token(day, field="day", low=1, high=31))
    try:
        hour_f = float(hour)
        site, parsed = _read_one_site(runtime, record)
        row = epw_record_at(parsed, month=month_n, day=day_n, hour=hour_f)
    except (RegisteredSiteError, EPWError, TypeError, ValueError, OverflowError) as exc:
        raise _coded_sky_error(
            f"the weather input {record.id!r} could not supply this perez sky's irradiance: {exc}",
            exc,
        ) from exc
    if row.direct_normal_wh_m2 is None or row.diffuse_horizontal_wh_m2 is None:
        raise SkyError(
            f"the weather input {record.id!r} marks DNI or DHI missing (9999) at "
            f"{month_n}/{day_n} {hour_f:g}; gendaylit cannot be driven from it. Pass "
            "irradiance=[dni, dhi] instead.",
            code=None,
        )
    sky_site = site_from_epw_location(site.location)
    dni, dhi = row.direct_normal_wh_m2, row.diffuse_horizontal_wh_m2
    return SkyInputs(
        site=sky_site,
        advisories=[
            to_wire(sky_location_from_epw_advisory(sky_site, weather=record.id), field="sky"),
            to_wire(sky_irradiance_from_epw_advisory(dni, dhi, month=month_n, day=day_n,
                                                     hour=hour_f), field="sky"),
        ],
        epw_irradiance=(dni, dhi),
    )
