"""The `weather` tool -- docs/tool-surface.md#8-weather.

Thin, in the sense CONTEXT.md's Layers table means it: this module validates and contains arguments,
picks the engine call, maps findings onto the wire and shapes the envelope. **No number reported
here is computed here.** `rad_core.weather` owns every EPW field index, every missing-value
sentinel, the east/west sign flip, the time-zone-to-meridian derivation, the IWEC albedo rule and
the `.wea` text; `rad_engine.weather` owns the read and the write. This module plumbs.

**Refuses nothing about an EPW's content, and that is the spec's own rule**: *"An EPW with problems
is still the EPW the user has; the job is to say what is wrong with it"*
(docs/tool-surface.md#8-weather). A zero albedo column, thousands of sentinel hours, a site nowhere
near the project -- every one of those loads, writes its `.wea`, and is *named* in `advisories`. The
refusals below are all argument-level and none of them is a judgement about weather data: a missing
`project`, a path outside the allowed roots, a name that cannot be a filename, a file the filesystem
would not open, bytes that are not an EPW at all, a name nothing matches.

**Both longitude conventions are on the result, under distinguishable names.** `site.longitude_east`
is the EPW's own field and `site.longitude_west` is what the `.wea` carries; `WEA_LONGITUDE_SIGN`
states the conversion at `info`. A single `longitude` key is the bug this project has already
shipped once (`rad_core.weather`'s module docstring), and it is not reintroduced by a result shape
that forces the reader to guess which convention a number is in.

## Where the `.wea` goes

`04_skies/`. There is no weather directory in `rad_engine.project.LAYOUT_DIRECTORIES` and adding one
would be a third deviation from the spec's §1.1 layout, which already carries exactly two and
records them (`rad_engine.project`, `LAYOUT_DIRECTORIES`). `04_skies/` is where the sky matrices'
own inputs live, and a `.wea` is one; `_LAYOUT_TOP_LEVEL` is what `delete_project` checks a project
root against, so a file written anywhere else in the root would read as a foreign entry.

## Two advisory-code gaps, reported and not filled

Codes are allocated once and never renamed, so a gap is reported rather than papered over with a
code whose own definition is untrue of the condition
(docs/tool-surface.md#advisory-code-register).

- **`EPW_SITE_MISMATCH` (warn) is allocated and is not emitted by this tool.** Its register row
  reads *"the EPW site is more than 100 km from any project-declared location"* -- and there is no
  project-declared location to compare against: `rad_core.manifest.Project` records `name`, `units`,
  `radiance_version`, `ground_reflectance`, `scalars` (`north`) and `description`, and no latitude or
  longitude anywhere. Emitting it would require this module to invent the project's site, which is
  the failure the whole provenance model exists to prevent. The code stays allocated for the phase
  that adds a project site; this tool emits the other three.
- **A file the filesystem would not open has no code.** `rad_engine.weather.WeatherFileUnreadableError`
  carries `code = None` for the reason its own docstring gives: all four weather codes are statements
  about a file that *was* read. The refusal carries its reason as prose and `field="epw"`, which is
  what keeps a code-less refusal actionable -- the posture `project`'s own `OSError` branch already
  ships.

## What is not built here

`gendaymtx` -- the sky matrix. §8's Layers row puts the sky-matrix argv and header parsing in
`rad_engine.skymatrix`, which is a different tool's plumbing; this one converts and describes. The
`.wea` written here is the input that path takes.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Annotated, Any, Literal

from mcp_types import CallToolResult
from pydantic import BaseModel, ConfigDict, Field, RootModel

from rad_core.manifest import ProvenanceValue, ToolCall
from rad_core.weather import EPWError, EPWFile
from rad_engine import (
    LoadedProject,
    ProjectError,
    ProjectPaths,
    open_project,
    register_input,
    save_manifest,
)
from rad_engine.clock import ClockZoneError, check_clock_zone
from rad_engine.safe_io import resolved_parent_within
from rad_engine.weather import WeatherError, read_epw, write_wea
from rad_mcp.envelope import (
    Advisory,
    RadResult,
    Refusal,
    emit,
    one_of_schema,
    refuse,
    refuse_missing,
)
from rad_mcp.materials_tool import InputStillBuiltFrom, refuse_if_built_from
from rad_mcp.names import NAME_PATTERN, is_legal_name, name_refusal_reason
from rad_mcp.paths import PathOutsideRoot, RootsNotConfigured, contain
from rad_mcp.runtime import Runtime, current, refuse_engine_error

logger = logging.getLogger(__name__)

__all__ = ["DESTRUCTIVE_ACTIONS", "WeatherResult", "weather"]

Action = Literal["load", "describe", "list", "remove"]

#: Named in the tool description, because annotations are per-tool and destructiveness here is
#: per-action (docs/tool-surface.md#annotations-are-coarser-than-the-surface). `tests/test_protocol.py`
#: check 0.7 reads this constant rather than a list of its own.
DESTRUCTIVE_ACTIONS: tuple[str, ...] = ("remove",)

#: Which registered input ids this tool owns. One per `weather(name=...)`, so a project holding a
#: TMY and an actual-year file gets two records rather than one overwriting the other.
_INPUT_PREFIX = "weather_"

#: The default handle when the caller names none: the EPW's own filename stem, or this.
_DEFAULT_NAME = "weather"

#: A weather name's legal charset -- **now one shared rule, `rad_mcp.names`**, hoisted there by
#: [R61](../../docs/plan.md#settled) on 2026-08-12. The name does two jobs: it becomes a manifest
#: input id (`weather_<name>`) *and* a filename (`04_skies/<name>.wea`), which is the same pair of
#: jobs the sensor-grid and BSDF names do -- so it was the same regex, copied. It now also refuses a
#: leading `-` or `.`; see that module for what each half of the rule defends.
_NAME_PATTERN = NAME_PATTERN


# --- nested shapes -----------------------------------------------------------------------------
# Plain models: only a top-level result carries `advisories`.


class _Nested(BaseModel):
    model_config = ConfigDict(extra="forbid")


# Two longitudes and two names, deliberately: a single `longitude` key would leave a reader to guess
# which one they had, the confusion `WEA_LONGITUDE_SIGN` and this shape exist together to remove
# (docs/tool-surface.md#8-weather). `time_zone` comes from
# `rad_core.weather.EPWLocation.time_zone_meridian_deg` and is never recomputed here.
class SiteView(_Nested):
    """The EPW's LOCATION line, and the two Radiance-convention values the `.wea` header carries.

    `longitude_east` is the EPW's own field, east-positive; `longitude_west` is its negation, the
    west-positive value the `.wea` carries and Radiance reads. Athens is `23.73` and `-23.73`.
    `time_zone` is the `.wea` header's value, a meridian in degrees, west-positive (`-30` for
    Athens, from the EPW's 2 hours east of UTC), not the EPW's hours field.
    """

    place: str
    latitude: float
    longitude_east: float
    longitude_west: float
    time_zone: float
    elevation_m: float


# `rad_core.weather.EPWDataQuality`, verbatim; the rule is stated in terms of what a real value
# looks like rather than one specific placeholder (`rad_core.weather`, "what present means").
class DataQualityView(_Nested):
    """Missing data in the EPW's ground-albedo and solar-irradiance columns.

    `albedo_present` is `true` when at least one hour carries a ground albedo that is neither the
    `999.0` missing-value sentinel nor `0`; `false` when every hour is one of those (a literal
    `0.000` is the IWEC file quirk) or the file has no hourly records, and `EPW_ALBEDO_ZERO` then
    says so. `missing_direct_normal` and `missing_diffuse` count hours, not fractions, carrying the
    `9999` sentinel; the `.wea` writes `0` for them, the same value a night hour gets.
    """

    albedo_present: bool
    missing_direct_normal: int
    missing_diffuse: int


# Deliberately shallow: listing a project's weather should not read and parse every EPW in it, and
# a row that reported `timesteps` would have to. `_listed` reports an out-of-root path as `null`
# (R46's reasoning) rather than dropping the row, so the caller can still `remove` it.
class WeatherRow(_Nested):
    """One row of `action="list"`: what the project recorded at registration. No file is opened,
    so a row says nothing about whether its files still exist; `describe` reads the EPW.

    `epw` or `wea` is `null` when the record names no such file or its path is now outside every
    allowed root. `clock_zone` is the zone the last `load` named, or `null` when it named none.
    """

    name: str
    input_id: str
    epw: str | None = None
    """Nullable for the same reason `wea` is: `_files_of` tells the two files apart by suffix, and a
    hand-edited record carrying neither is reported as `null` rather than crashing the listing."""
    wea: str | None = None
    clock_zone: str | None = None
    """`InputRecord.clock_zone`, as recorded (R860)."""


# --- the result branches -----------------------------------------------------------------------


class WeatherBase(RadResult):
    """What every weather-shaped answer carries. Also the union schema's shared base."""

    project: str
    root: str


# One branch for both actions because they report the same facts about the same file and differ only
# in whether the `.wea` was (re)written and the input (re)registered. R810 lifted the `timesteps`,
# `daylight_hours` and `ground_reflectance` meanings from their field text, which never reached the
# wire.
class WeatherFile(WeatherBase):
    """`action="load"` and `action="describe"`: what the EPW contains.

    `load` writes the `.wea` and registers both files as one input. `describe` writes nothing, the
    `.wea` included, and reads the EPW again, so it reports the file as it is now, not as it was
    when loaded. `wea` is
    always a path on `load`; `null` on a `describe` whose record names no `.wea`.

    `timesteps` is the number of hourly records actually in the file: 8760 for a complete year,
    8784 for a leap year, fewer for a truncated file. `daylight_hours` counts the hours with
    positive direct-normal or diffuse-horizontal irradiance, read from the file, not computed from
    the sun's position; a missing-value hour counts as zero. `ground_reflectance` is the project's
    one ground reflectance with its provenance, set with `project`'s `ground_reflectance`: the value
    in use, never read from the EPW.

    `clock_zone` is the time zone the last `load` named, or `null` when it named none; LM-83's
    08:00-18:00 window is then in the EPW's standard time, with no daylight saving.
    """

    action: Literal["load", "describe"]
    name: str
    input_id: str
    epw: str
    wea: str | None = None
    """The `.wea` this tool wrote. Always a path on `load`, which writes it.

    `null` rather than `""` on a `describe` of a record that carries no `.wea` -- reachable only
    through a hand-edited manifest, but an empty string reads as a path that happens to be empty
    while `null` says there is none. `RadResult.payload` emits it as an explicit `null` (only
    `advisories` is omitted when empty), so the key is present either way and a client is never left
    to infer the difference from an absent field."""
    site: SiteView
    timesteps: int
    """Hourly records in the EPW. 8760 for a complete year; sub-hourly steps are out of scope
    (docs/plan.md Phase 4, "Defers").

    **Counted from the parsed records, never assumed.** This was a literal `8760` until 2026-08-08,
    which meant a leap-year file (8784) and a truncated one alike were reported as a complete year:
    a wrong number wearing a right one's clothes, with no advisory to qualify it, and every other
    field on this result derived from the same `parsed` object it ignored. It is load-bearing
    downstream -- check 4.7 asserts the `.ill` is `n_points x 8760`, so a leap year would publish a
    count that disagrees with its own annual result by 24 columns. Found by mutation testing: the
    hardcoded value survived the entire suite, because every test that touched it used the 8760-hour
    Athens fixture, where the constant and the truth coincide."""
    daylight_hours: int
    """Records carrying non-zero direct-normal **or** diffuse-horizontal irradiance.

    Defined here because §8's return block names the key and no layer below defines it, and it is
    stated rather than left to a reader's assumption: it is the count of hours the sun contributed
    *anything* at this site, which is the number that says how much of the year an annual run has
    daylight to work with. Not a solar-geometry calculation -- `rad_core.weather` computes no sun
    position, and a number derived from one would be a claim this server cannot support from the
    file's own columns. A sentinel hour counts as zero, which is why `EPW_MISSING_DATA` matters to
    reading it."""
    data_quality: DataQualityView
    clock_zone: str | None = None
    """`InputRecord.clock_zone` (R860): on `load` the zone this call named, which it records; on
    `describe` the zone the record carries, read from the manifest rather than the EPW, which has no
    zone. `null` when none was named, including a record written before the field existed."""
    ground_reflectance: ProvenanceValue
    """The project's ONE ground reflectance, with its provenance -- **an object, never a scalar**
    (`rad_core.manifest.ProvenanceValue`). Reported on this result because `EPW_ALBEDO_ZERO` says the
    file's own ground data is unusable, and a warning that does not say what is being used instead
    leaves the caller with a problem and no number. Read from the manifest, never chosen here: it is
    set in exactly one place, `project(ground_reflectance=...)`."""


class WeatherListed(WeatherBase):
    """`action="list"`: one row per weather input registered in the project."""

    weather: list[WeatherRow]


# The `.wea` is left because it is a derived file inside the project, and removing it while an
# artifact built from it still points at it would break what `refuse_if_built_from` protects.
class WeatherRemoved(WeatherBase):
    """`action="remove"`: the input is deregistered and no file is deleted, neither your EPW nor
    the `.wea` this server wrote.

    `removed` names the input ids, `[]` when no such input was registered (not a refusal).
    Removing an input an artifact was built from is refused (`ARTIFACT_EDGES_INCOMPLETE`), and
    nothing is changed.
    """

    removed: list[str]


WeatherPayload = WeatherFile | WeatherListed | WeatherRemoved | Refusal


class WeatherResult(RootModel[WeatherPayload]):
    """What `weather` returns: the `load`/`describe`, `list` or `remove` result, or a refusal."""

    @classmethod
    def model_json_schema(cls, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return one_of_schema(super().model_json_schema(*args, **kwargs), WeatherBase)


# --- argument checks -----------------------------------------------------------------------------


def _refuse_argument(message: str, *, field: str) -> CallToolResult:
    """`ARGUMENT_OUT_OF_RANGE` with the argument's own name in `field` -- ruling R14's code for an
    argument refused on its face, and `field` is what keeps one code actionable across many
    arguments."""
    return refuse(
        message,
        [Advisory(code="ARGUMENT_OUT_OF_RANGE", severity="error", field=field, message=message)],
    )


def _refuse_name(name: str) -> CallToolResult | None:
    """Refuse a weather name that is not a conservative identifier, or `None` if it is fine.

    Checked on `remove` as well as `load`: the name is matched against manifest ids there, and a name
    this server would never have written is one it should say so about rather than report as simply
    absent (`rad_mcp.server._refuse_grid_name`, same reasoning).
    """
    if is_legal_name(name):
        return None
    return _refuse_argument(
        name_refusal_reason(name, kind="weather", examples="'athens', 'tmy_2020', 'actual-2024'"),
        field="name",
    )


# --- the tool -------------------------------------------------------------------------------


def weather(
    action: Annotated[
        Action,
        Field(
            description=(
                "which operation: \"load\", \"describe\", \"list\" or \"remove\". The tool "
                "description says what each one does."
            )
        ),
    ],
    project: Annotated[
        str | None,
        Field(
            description=(
                "the project handle. Required for every action -- a `.wea` has to be written into "
                "something, and the ground reflectance reported alongside it is the project's."
            )
        ),
    ] = None,
    epw: Annotated[
        str | None,
        Field(
            description=(
                "absolute path to the EPW file. REQUIRED on \"load\". It is read, never moved or "
                "modified."
            )
        ),
    ] = None,
    name: Annotated[
        str | None,
        Field(
            description=(
                "the weather file's handle inside the project, and the `.wea`'s filename. REQUIRED "
                "on \"describe\" and \"remove\"; on \"load\" it defaults to the EPW's own filename "
                "stem. 1-64 characters of ASCII letters, digits, underscore, hyphen and dot."
            )
        ),
    ] = None,
    clock_zone: Annotated[
        str | None,
        Field(
            description=(
                "\"load\" only: an IANA time zone such as \"Europe/Athens\". Used only to "
                "place LM-83's 08:00-18:00 window in local clock time, with the zone's daylight "
                "saving. Its standard-time offset must equal the EPW's. Omitted, the window is in "
                "the EPW's standard time."
            )
        ),
    ] = None,
) -> WeatherResult:
    """Register an EPW, convert it to `.wea`, and describe what it actually contains.

    Use it before any study that needs the site's weather or location: an annual sky matrix, sunlight
    exposure, or a sky or site read from the EPW.

    An EPW is the one input a whole annual study rests on, and the ways it goes wrong are silent:
    Radiance's `.wea` states longitude **west-positive**, so an unflipped sign puts the building on
    the wrong side of the planet with no error anywhere, and IWEC-sourced files write a literal
    `0.000` ground albedo instead of the documented missing-value sentinel, which reads as perfectly
    black ground. Both are reported here rather than handled quietly. Nothing about an EPW's content
    is refused: a file with problems is still the file you have, and this says what is wrong with it.

    DESTRUCTIVE ACTIONS: `remove` deregisters a weather input from the project. It never deletes the
    EPW itself -- that is yours.

    "load" reads the EPW, writes the project's `.wea` and registers the input a sky matrix is built
    from. "describe" re-reads a registered file and reports what it contains, writing nothing.
    "list" reports the project's weather inputs. "remove" deregisters one.
    """
    runtime = current()
    try:
        return _dispatch(
            runtime, action, project=project, epw=epw, name=name, clock_zone=clock_zone
        )
    except ClockZoneError as exc:
        logger.info("weather(action=%s) refused: %s", action, exc)
        return _refuse_clock_zone(exc)
    except (EPWError, WeatherError) as exc:
        # `EPWParseError` (the bytes are not an EPW) and `WeatherFileUnreadableError` (the filesystem
        # would not give us the bytes) are deliberately distinguishable by type one layer down, and
        # they stay distinguishable here: `_refuse_weather` reads each one's own `code`/`field`
        # rather than collapsing them onto one message.
        logger.info("weather(action=%s) refused: %s", action, exc)
        return _refuse_weather(exc)
    except InputStillBuiltFrom as exc:
        logger.info("weather(action=%s) refused: %s", action, exc)
        return refuse(
            str(exc),
            [
                Advisory(
                    code="ARTIFACT_EDGES_INCOMPLETE",
                    severity="error",
                    field="name",
                    message=str(exc),
                )
            ],
        )
    except (ProjectError, PathOutsideRoot, RootsNotConfigured) as exc:
        logger.info("weather(action=%s) refused: %s", action, exc)
        return refuse_engine_error(exc)
    except OSError as exc:
        # The `.wea` write and the manifest save both touch the disk and the filesystem can refuse
        # either -- a read-only mount, a permission the server does not hold. Unhandled, that leaves
        # the wire with a bare `ToolError`; here it is a refusal the client can read. **No advisory,
        # deliberately**: this clause cannot tell the `.wea` write from the manifest save, so it cannot
        # name the file `PROJECT_WRITE_FAILED` (R855) would name, and it is not an argument the client
        # can change, which is why it is also not `ARGUMENT_OUT_OF_RANGE` (`rad_mcp.project_tool`, the
        # same branch).
        logger.warning("weather(action=%s) failed on the filesystem: %s", action, exc)
        return refuse(
            f"weather(action={action!r}) could not complete: {exc}. Nothing about the arguments is "
            "wrong -- the filesystem refused the operation, so this is an environment problem "
            "(permissions, a read-only mount, or a file held open elsewhere)."
        )


def _refuse_clock_zone(exc: ClockZoneError) -> CallToolResult:
    """Shape a `rad_engine.clock.ClockZoneError` raised by `_load`, before anything was written.

    The reason is the clock module's own message, unchanged. Each `Advisory` is spelt out with a
    literal code, rather than looked up, so `tests/test_advisories.py`'s emitter scan reads all
    three.
    """
    message = (
        f"clock_zone was refused: {exc}. Nothing was written: no .wea, no change to the project's "
        "weather record."
    )
    if exc.code == "unknown":
        advisory = Advisory(
            code="WEATHER_CLOCK_ZONE_UNKNOWN", severity="error", field="clock_zone", message=message
        )
    elif exc.code == "offset_mismatch":
        advisory = Advisory(
            code="WEATHER_CLOCK_ZONE_OFFSET_MISMATCH",
            severity="error",
            field="clock_zone",
            message=message,
        )
    else:
        advisory = Advisory(
            code="WEATHER_CLOCK_ZONE_DATABASE_MISSING",
            severity="error",
            field="clock_zone",
            message=message,
        )
    return refuse(message, [advisory])


def _refuse_weather(exc: Exception) -> CallToolResult:
    """Shape a `rad_core.weather` / `rad_engine.weather` refusal.

    Both layers carry a class-level `code`/`severity`/`field` in the shape `ProjectError`
    established. `code` is `None` for `WeatherFileUnreadableError` -- the register allocates nothing
    for "the file would not open" and the gap is reported rather than filled -- so that refusal
    carries its reason as prose, with `field="epw"` still naming the argument it is about.

    A parse failure is different and *does* get a code: the bytes are not an EPW, which is an
    argument this server rejects on its face, and `ARGUMENT_OUT_OF_RANGE` is exactly the class ruling
    R14 allocated. `rad_core.weather.EPWError` declines to assign it because that layer states the
    fact and this layer assigns the class (`rad_core.weather.EPWError`'s own docstring says so).
    """
    field = getattr(exc, "field", None)
    code = getattr(exc, "code", None)
    if code is None and isinstance(exc, EPWError):
        # Every malformed-EPW branch: `parse_epw`'s refusals, and `to_wea`'s record-less file.
        return _refuse_argument(
            f"{exc} This file cannot be read as an EPW, so nothing about its weather data has been "
            "judged -- supply a different file.",
            field=field or "epw",
        )
    if code is None:
        # `WeatherFileUnreadableError`: the register has no code for a file that will not open to be
        # read (`PROJECT_WRITE_FAILED` is a refused write), so
        # the reason is prose. `field` is not lost -- it is on the exception and named in the
        # message -- but an advisory needs a code, and inventing one is what the register forbids.
        return refuse(str(exc))
    return refuse(
        str(exc),
        [
            Advisory(
                code=code,
                severity=getattr(exc, "severity", "error"),
                field=field,
                message=str(exc),
            )
        ],
    )


def _dispatch(
    runtime: Runtime,
    action: Action,
    *,
    project: str | None,
    epw: str | None,
    name: str | None,
    clock_zone: str | None,
) -> CallToolResult:
    call = f"weather(action={action!r})"
    if (refusal := refuse_missing(call, project=project)) is not None:
        return refusal
    if clock_zone is not None and action != "load":
        # Refused rather than ignored: only `load` records a zone, and a zone dropped silently by
        # `describe` would leave the caller believing it had been set.
        return _refuse_argument(
            f"clock_zone is read only by weather(action='load'); {call} would ignore it. Load the "
            "EPW again with clock_zone=... to record a zone.",
            field="clock_zone",
        )
    # `refuse_missing` returns `None` only when every argument it was given is non-`None`, which no
    # checker can see through the `**arguments` it collects them with (`geometry_tool._dispatch`).
    assert project is not None

    loaded = runtime.open(project)

    if action == "list":
        return _listed(runtime, loaded)

    if action == "load":
        if (refusal := refuse_missing(call, epw=epw)) is not None:
            return refusal
        assert epw is not None
        # **Contained before the file is read**, which is the whole point of the boundary: *"Servers
        # MUST: Validate all tool inputs"*, and a path argument is the untrusted input it exists for
        # (docs/tool-surface.md#input-validation-and-the-path-boundary). `contain` resolves and
        # *then* tests containment, so a symlink inside a root pointing out of it is refused too.
        contained = contain(epw, runtime.roots)
        handle = name if name is not None else _default_name(contained)
        if (refusal := _refuse_name(handle)) is not None:
            return refusal
        return _load(runtime, loaded, epw=contained, name=handle, clock_zone=clock_zone)

    if (refusal := refuse_missing(call, name=name)) is not None:
        return refusal
    assert name is not None
    if (refusal := _refuse_name(name)) is not None:
        return refusal
    if action == "describe":
        return _describe(runtime, loaded, name=name)
    return _removed(runtime, loaded, name=name)


def _default_name(epw: Path) -> str:
    """The EPW's own filename stem, or `weather` if the stem is empty.

    Not sanitised into legality here: a stem outside the charset is *refused* by `_refuse_name` like
    any other name, rather than silently rewritten into something the caller did not choose and
    cannot predict. `GRC_Athens.167160_IWEC.epw` is already legal and becomes
    `GRC_Athens.167160_IWEC`.
    """
    return epw.stem or _DEFAULT_NAME


# --- the actions -------------------------------------------------------------------------------


def _load(
    runtime: Runtime, loaded: LoadedProject, *, epw: Path, name: str, clock_zone: str | None
) -> CallToolResult:
    """Read the EPW, write the `.wea`, register the input.

    **A `clock_zone` is checked against the EPW's own UTC offset before anything is written**
    (R860): `check_clock_zone` raises `ClockZoneError` between the read and the `.wea` write, so a
    refused zone leaves the `.wea` and the manifest as they were. It is then set on the fresh record
    inside the one manifest write, as `register_input_and_set_role` sets `role`; a load that names
    none records `None`, because the record is replaced whole.

    The `.wea` is written **before** the input is registered, and the registration digests both
    files: an input record pointing at a file that does not exist yet would digest nothing and then
    never invalidate (`rad_mcp.server._create`, the same ordering for the same reason).

    **Both files are registered under one id.** The EPW is the source and the `.wea` is what
    `gendaymtx` actually reads, and a sky matrix built from this input is invalidated by a change to
    either -- which is only true if both are in the record's digest.
    """
    parsed = read_epw(epw)
    if clock_zone is not None:
        check_clock_zone(clock_zone, parsed.location.time_zone_hours)
    paths = ProjectPaths(loaded.paths.root)
    target = paths.skies / f"{name}.wea"

    # **Contained BEFORE the write, and this ordering is the whole guard.** It used to run after,
    # with a comment calling it "the second of two independent guards ... this cannot fail today".
    # It could: `paths.skies` being derived from a contained root and `name` being charset-checked
    # says nothing about what is *at* the target path. A symlink planted at `04_skies/<name>.wea`
    # made `write_wea` overwrite an arbitrary file outside every root -- and because `contain` ran
    # afterwards, the tool returned `refused: true` describing a breach that had already completed.
    # A check downstream of the syscall it is meant to prevent is a report, not a guard.
    #
    # `resolved_parent_within` is the half `O_NOFOLLOW` cannot cover: that flag refuses a symlink at
    # the final component only, so a *directory* symlink at `04_skies` itself would still place the
    # file outside the root with no symlink in the final position at all. Checked before `mkdir`,
    # because `mkdir(parents=True)` would otherwise traverse it.
    if not resolved_parent_within(target, runtime.roots):
        raise PathOutsideRoot(
            f"the project's sky directory {target.parent} resolves outside every allowed root, so "
            f"the .wea for {name!r} was not written. Nothing has been changed on disk."
        )
    wea = write_wea(parsed, target)
    # Re-contained after the write too, now that it is genuinely a second independent guard rather
    # than the only one: `write_wea` returns the *resolved* path, so this is the check that the file
    # actually created is the file that was authorised.
    contain(wea, runtime.roots)

    input_id = _INPUT_PREFIX + name
    # Opened *inside* both locks: `register_input` writes the whole manifest with no
    # compare-against-disk, so what loses an update is the interval between the read and the write,
    # and a lock that does not cover the read covers nothing (`Runtime.modifying`).
    with runtime.modifying(paths):
        fresh = open_project(paths.root, radiance_version=runtime.launcher.version)
        arguments = {"epw": str(epw), "name": name}
        if clock_zone is not None:
            arguments["clock_zone"] = clock_zone
        record = register_input(
            fresh,
            input_id=input_id,
            kind="weather",
            files=[epw, wea],
            registered_by=ToolCall(tool="weather", action="load", arguments=arguments),
            save=False,
        )
        record.clock_zone = clock_zone
        save_manifest(paths, fresh.manifest)

    return _file_result(
        loaded,
        action="load",
        name=name,
        input_id=input_id,
        epw=epw,
        wea=wea,
        parsed=parsed,
        clock_zone=clock_zone,
    )


def _describe(runtime: Runtime, loaded: LoadedProject, *, name: str) -> CallToolResult:
    """Re-read a registered EPW and report what it contains. Writes nothing.

    The EPW is read again rather than answered from the manifest: the manifest records paths and
    digests, and the question this action asks is what the *file* contains. A record whose EPW has
    since been edited on disk therefore reports the current file, which is the honest answer.

    **The manifest path is re-contained before the read, which is why this takes `runtime`.** It
    would be easy to argue the check is redundant -- `load` contained the same path when it
    registered it -- but "it was inside a root when it was written" is not the same claim as "it is
    inside a root now", and the gap between them is reachable three ways without any hand-editing:
    a project created while `RAD_MCP_ROOTS` was wider, a project directory copied between machines,
    and a path whose parent became a symlink after registration. This function's own docstring
    already treats a hand-edited manifest as in-scope for the suffix rule, so a manifest-supplied
    path is untrusted input by this module's own standard. Without the check, `describe` reads a
    file outside every allowed root and publishes its LOCATION line -- the city string plus four
    numbers -- which is a small exfiltration primitive and a file-existence oracle. `_load` states
    the general rule at its own `contain` call; this is the same rule applied to the read path
    rather than the write one.
    """
    record = _record(loaded, name)
    if record is None:
        return _unknown_name(loaded, name, action="describe")
    epw, wea = _files_of(record)
    if epw is None:
        # A weather record whose files are all `.wea`, or which has none at all -- only reachable by
        # editing the manifest by hand. Refused rather than answered from the `.wea`: that file is
        # this server's own derived output, so parsing it as the source would report the conversion
        # back to the caller as if it were the measurement.
        return _refuse_argument(
            f"the weather input {record.id!r} records no EPW file, so there is nothing to describe. "
            "Its manifest record carries no file with an EPW suffix. Re-register it with "
            "weather(action='load', epw=...).",
            field="name",
        )
    # The *contained* path is both read and published. Publishing the raw manifest string while
    # reading the resolved one would report a path that was never the one validated -- harmless
    # today, since the read is contained either way, but it makes the result a claim about a
    # different file from the one opened.
    contained = contain(epw, runtime.roots)
    parsed = read_epw(contained)
    return _file_result(
        loaded,
        action="describe",
        name=name,
        input_id=record.id,
        epw=contained,
        wea=wea,
        parsed=parsed,
        clock_zone=record.clock_zone,
    )


def _publishable(path: Path | None, runtime: Runtime) -> str | None:
    """`str(path)` if it is inside an allowed root, else `None`.

    Swallows the refusal rather than raising, because `list` must keep answering: one record whose
    path drifted out of root is not a reason to refuse the whole listing. `describe` on that same
    record still refuses loudly, which is where the caller learns why the path is `null`.
    """
    if path is None:
        return None
    try:
        return str(contain(path, runtime.roots))
    except (PathOutsideRoot, RootsNotConfigured):
        return None


def _listed(runtime: Runtime, loaded: LoadedProject) -> CallToolResult:
    """Registration facts only -- no file is opened, parsed, or validated.

    **Out-of-root paths are reported as `null`, not echoed.** `describe` re-contains its manifest
    path before reading it (R46), on the reasoning that a registered path is not a currently-valid
    one -- three ways to reach that state are listed there, none needing a hand-edited manifest.
    `list` did no containment at all and published `str(epw)` straight from the manifest, which
    leaks an absolute path outside every allowed root. Strictly weaker than the `describe` case,
    since nothing is read, but it is the same claim about the same untrusted string and the same
    rationale covers it.

    Reported as `null` rather than filtered out: the row still exists and the caller needs to see it
    to `remove` it. A missing row would say "there is no such input", which is false.
    """
    rows = []
    for record in loaded.manifest.inputs:
        if record.kind != "weather":
            continue
        epw, wea = _files_of(record)
        rows.append(
            WeatherRow(
                name=_name_of(record.id),
                input_id=record.id,
                epw=_publishable(epw, runtime),
                wea=_publishable(wea, runtime),
                clock_zone=record.clock_zone,
            )
        )
    return emit(
        WeatherListed(
            project=loaded.paths.name, root=str(loaded.paths.root), weather=rows
        )
    )


def _removed(runtime: Runtime, loaded: LoadedProject, *, name: str) -> CallToolResult:
    """Deregister one weather input.

    The manifest is edited here and written by `rad_engine.project.save_manifest`, under both locks:
    `rad_engine.project` has a `register_input` and no `unregister_input`, and the interval between
    the read and the write is what loses an update (`rad_mcp.geometry_tool._removed`).

    Removing an input that is not there is **not** refused -- the post-condition the caller asked for
    already holds, and `removed: []` says so; a refusal would make a retry after a timeout report a
    failure for work that succeeded. An input a sealed artifact is still built from *is* refused,
    before the record is touched: an artifact whose `built_from` names a removed input makes the
    whole manifest unevaluable, not just that artifact.
    """
    paths = ProjectPaths(loaded.paths.root)
    candidates = {name, _INPUT_PREFIX + name}
    with runtime.modifying(paths):
        fresh = open_project(paths.root, radiance_version=runtime.launcher.version)
        removed = [
            record.id
            for record in fresh.manifest.inputs
            if record.kind == "weather" and record.id in candidates
        ]
        if removed:
            refuse_if_built_from(fresh.manifest, removed)
            fresh.manifest.inputs = [
                record for record in fresh.manifest.inputs if record.id not in removed
            ]
            save_manifest(paths, fresh.manifest)
    return emit(
        WeatherRemoved(
            project=loaded.paths.name, root=str(loaded.paths.root), removed=removed
        )
    )


# --- shared shaping ------------------------------------------------------------------------------


def _name_of(input_id: str) -> str:
    return input_id[len(_INPUT_PREFIX) :] if input_id.startswith(_INPUT_PREFIX) else input_id


def _record(loaded: LoadedProject, name: str) -> Any | None:
    """The weather input `name` refers to, or `None`. Matched on the bare name and the prefixed id
    alike, the same two candidates `remove` accepts."""
    candidates = {name, _INPUT_PREFIX + name}
    for record in loaded.manifest.inputs:
        if record.kind == "weather" and record.id in candidates:
            return record
    return None


def _files_of(record: Any) -> tuple[Path | None, Path | None]:
    """The record's EPW and its `.wea`, told apart by suffix rather than by position.

    By suffix because position is not a guarantee the schema makes: `InputRecord.files` is a list and
    nothing in `rad_core.manifest` orders it, so reading `files[0]` as the EPW would be an assumption
    about this tool's own write order that a hand-edited or re-registered manifest can break.

    **Both halves are optional, and neither absence raises.** `register_input` refuses an empty file
    list and `load` always writes both, so a record carrying neither is unreachable through this
    tool -- but a hand-edited manifest is exactly what this function's suffix rule exists to survive,
    and an earlier version fell back to `record.files[0]`, which is an `IndexError` on a record with
    no files at all. An exception escaping a tool function is a JSON-RPC error rather than the
    tool-result-channel refusal check 0.4 requires, so the absence is returned as data and the
    callers shape it: `describe` refuses (`_unreadable_record`), `list` reports `null`.
    """
    epw = next(
        (Path(f.path) for f in record.files if Path(f.path).suffix.lower() != ".wea"), None
    )
    wea = next((Path(f.path) for f in record.files if Path(f.path).suffix.lower() == ".wea"), None)
    return epw, wea


def _unknown_name(loaded: LoadedProject, name: str, *, action: str) -> CallToolResult:
    """A name no weather input in this project matches.

    An argument-level refusal and not a statement about weather data: the caller named something
    that is not there, and the recovery is to name one that is -- which is why the known names are
    in the message.
    """
    known = sorted(
        _name_of(record.id) for record in loaded.manifest.inputs if record.kind == "weather"
    )
    return _refuse_argument(
        f"weather(action={action!r}) found no weather input named {name!r} in project "
        f"{loaded.paths.name!r}. "
        + (f"Registered: {', '.join(known)}." if known else "This project has no weather loaded.")
        + " Load one with weather(action='load', epw=...).",
        field="name",
    )


def _file_result(
    loaded: LoadedProject,
    *,
    action: Literal["load", "describe"],
    name: str,
    input_id: str,
    epw: Path,
    wea: Path | None,
    parsed: EPWFile,
    clock_zone: str | None,
) -> CallToolResult:
    location = parsed.location
    quality = parsed.data_quality
    reflectance = loaded.manifest.project.ground_reflectance
    return emit(
        WeatherFile(
            project=loaded.paths.name,
            root=str(loaded.paths.root),
            action=action,
            name=name,
            input_id=input_id,
            epw=str(epw),
            wea=str(wea) if wea is not None else None,
            site=SiteView(
                place=location.place,
                latitude=location.latitude,
                longitude_east=location.longitude_east_deg,
                longitude_west=location.longitude_west_deg,
                time_zone=location.time_zone_meridian_deg,
                elevation_m=location.elevation_m,
            ),
            timesteps=len(parsed.records),
            daylight_hours=_daylight_hours(parsed),
            data_quality=DataQualityView(
                albedo_present=quality.albedo_present,
                missing_direct_normal=quality.missing_direct_normal,
                missing_diffuse=quality.missing_diffuse,
            ),
            clock_zone=clock_zone,
            ground_reflectance=reflectance,
            advisories=_advisories(parsed, reflectance) or None,
        )
    )


def _daylight_hours(parsed: EPWFile) -> int:
    """Hours carrying non-zero direct-normal or diffuse-horizontal irradiance. See
    `WeatherFile.daylight_hours` for why this and not a solar-geometry calculation."""
    return sum(
        1
        for record in parsed.records
        if (record.direct_normal_wh_m2 or 0.0) > 0.0
        or (record.diffuse_horizontal_wh_m2 or 0.0) > 0.0
    )


def _advisories(parsed: EPWFile, reflectance: ProvenanceValue) -> list[Advisory]:
    """The three findings this tool can make about a weather file. Order is stable: the sign
    convention first, because it is true of every file, then the two data-quality warnings.

    `EPW_SITE_MISMATCH` is not among them, and the omission is deliberate and reported -- see the
    module docstring: there is no project-declared location in the manifest to measure a distance
    against, and inventing one is the failure the provenance model exists to prevent.
    """
    location = parsed.location
    advisories = [
        Advisory(
            code="WEA_LONGITUDE_SIGN",
            severity="info",
            field="site",
            message=(
                f"Longitude is reported in both conventions: {location.longitude_east_deg:g} "
                f"degrees east (the EPW's own field) and {location.longitude_west_deg:g} "
                "west-positive, which is what the .wea header carries and what Radiance reads. The "
                "time_zone reported is likewise the .wea's west-positive meridian in degrees "
                f"({location.time_zone_meridian_deg:g}), derived from the EPW's "
                f"{location.time_zone_hours:g} hours east of UTC -- not the hours field itself."
            ),
        )
    ]

    quality = parsed.data_quality
    if not quality.albedo_present:
        advisories.append(
            Advisory(
                code="EPW_ALBEDO_ZERO",
                severity="warn",
                field="data_quality",
                message=(
                    "This EPW carries no usable ground albedo: every hour is either the documented "
                    "999.0 missing-value sentinel or a literal 0.000, which is the IWEC file quirk "
                    "and is indistinguishable from a perfectly black ground. Nothing from the file "
                    "is used for it. The ground reflectance in effect is the project's own: "
                    f"{reflectance.value} ({reflectance.provenance}"
                    + (f", {reflectance.source}" if reflectance.source else "")
                    + "). Set it with project(action='update', ground_reflectance=...) if that is "
                    "not what this site's ground looks like -- it reaches gensky -g, gendaymtx -g "
                    "and the modelled ground material, all three."
                ),
            )
        )

    if quality.missing_direct_normal or quality.missing_diffuse:
        advisories.append(
            Advisory(
                code="EPW_MISSING_DATA",
                severity="warn",
                field="data_quality",
                message=(
                    f"{quality.missing_direct_normal} hour(s) carry the 9999 missing-value sentinel "
                    f"for direct-normal irradiance and {quality.missing_diffuse} for "
                    "diffuse-horizontal, out of "
                    f"{len(parsed.records)}. The .wea writes 0 for each of them, which is the same "
                    "value a genuine zero-irradiance night hour gets -- the file itself cannot tell "
                    "you which is which, so this count is the only place that distinction survives. "
                    "An annual result computed from it under-reports those hours rather than "
                    "failing."
                ),
            )
        )
    return advisories
