"""`run_electric` -- docs/tool-surface.md §19, Phase 11b design §5-§9.

Lamp-only illuminance over a zoned EN 12464-1:2011 grid, one `rtrace` over every zone's points, and
per-zone initial and maintained illuminance and `U_o` beside the sourced limit, with no verdict
(R608). Plumbing only: zone arithmetic is `rad_core.en12464`, zoning `rad_core.grid_zones`, the lit
scene `rad_mcp.lit_scene`, the trace `point_in_time_tool._run_pipeline`. Runs inline, as
`run_point_in_time` does (R613), and so is not exportable (R611).

**The order of the checks is a contract** (design §6 steps 1-5, §9): arguments (`_validate_arguments`,
before the project is opened) -> the lamps (`verify_lit_files`) -> the double count (R602) -> geometry,
then the grid and its zone files -> the `!` scan -> the grid's floor (R776) -> `dry_run` stops.
`_prepare` walks steps 2-5 once for
both a dry run and a real one, spawning nothing and writing nothing, so the two cannot disagree about a
refusal or about an advisory knowable before the spawn.
"""

from __future__ import annotations

import json
import logging
import math
import os
import tempfile
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, ClassVar, Literal

from mcp_types import CallToolResult, ResourceLink, TextContent
from pydantic import BaseModel, ConfigDict, Field, RootModel, StrictFloat

from rad_core.en12464 import (
    ASR_SURROUND_UO,
    EDITION,
    SURROUND_UO_LIMIT,
    ZONE_MIN_POINTS,
    ZONE_UNLIT_FRACTION,
    evaluate,
)
from rad_core.grid_zones import (
    BACKGROUND_MIN_WIDTH_M,
    MAX_TASK_ZONES,
    WALL_STRIP_M,
    background_narrow_message,
)
from rad_core.grids import MATRIX_MAX_DECLARED_POINTS, GridPoint
from rad_core.manifest import InputRecord, ProvenanceValue, ZoneRecord
from rad_core.quality import QualityParameters
from rad_engine import ProjectError, ProjectPaths, RadianceError, RadianceNotFound, bytes_digest, discover
from rad_engine.ies import PhotometryUnverifiedError
from rad_engine.octree import (
    EmptyOctreeError,
    OctreeSetOverflowError,
    OctreeTooManyFilesError,
    SceneInlineCommandError,
)
from rad_engine.project import LoadedProject
from rad_engine.rtrace import RtraceOutputError, ambient_interpolation_radius_m, parameters_dict, resolve_quality
from rad_engine.safe_io import (
    NotARegularFileError,
    open_regular_file,
    refuse_existing_non_regular_destination,
    refuse_linked_components,
    replace_file_atomically,
)
from rad_mcp.envelope import Advisory, RadResult, Refusal, emit, one_of_schema, refuse, refuse_missing
from rad_mcp.lit_scene import (
    LitPlacementView,
    LitSceneRefusal,
    _header,
    _points,
    _recorded,
    _types_by_name,
    commit_lit_support,
    lit_advisories,
    lit_placements_view,
    near_field_advisory,
    placement_records,
    verify_lit_files,
)
from rad_mcp.names import SENSOR_GRID_INPUT_PREFIX, is_legal_name
from rad_mcp.paths import CodedWriteRefusal, PathOutsideRoot, RootsNotConfigured, contain, contain_as_named, write_failure
from rad_mcp.point_in_time_tool import (
    DEFAULT_INLINE_TIMEOUT_S,
    MAX_INLINE_VALUES,
    MAX_POINTS_BYTES,
    RESULT_MIME_TYPE,
    GridEmptyOnDiskError,
    GridNotFoundError,
    GridUnreadableError,
    NoGeometryRegisteredError,
    QualityValue,
    _parse_points,
    _refuse_argument,
    _run_pipeline,
    _scan_scene_for_inline_commands,
    _scene_files,
    _ValidatedScene,
    _grid_candidates,
    refuse_grid_off_recorded_floor,
)
# One validator is R30's rule, so a clause added to `run_point_in_time`'s validator applies here too;
# check both tools when adding one (L-110, R845).
from rad_mcp.point_in_time_tool import (
    _validate_arguments as _validate_point_in_time_arguments,
)
from rad_mcp.scene_warnings import light_redirecting_advisory
from rad_mcp.runtime import Runtime, current, refuse_engine_error

__all__ = [
    "ELECTRIC_DESTRUCTIVE_ACTIONS",
    "MAINTENANCE_NOTE_MAX_BYTES",
    "ElectricDryRunOk",
    "ElectricRefusal",
    "ElectricResponse",
    "ElectricResult",
    "ZoneLimit",
    "ZoneStats",
    "ZoneView",
    "run_electric",
]

logger = logging.getLogger(__name__)

#: Amendment 8: `run_electric` takes no `action`, deregisters no input and deletes no file a caller
#: registered, so it has no destructive action (`tests/test_protocol.py`'s map is pinned to the tools).
ELECTRIC_DESTRUCTIVE_ACTIONS: frozenset[str] = frozenset()
#: R617 -- unsourced; bounds a free-text note that is persisted in the result, measured in UTF-8 bytes
#: (the unit the machine stores), not in characters.
MAINTENANCE_NOTE_MAX_BYTES: int = 4096
#: The opener of `verify_lit_files`' refusals for this tool (R574): run_electric has no `luminaires`
#: argument, so "luminaires=true" would name one it does not take.
_LABEL = "run_electric lights the scene with every placement"
_NO_GEOMETRY_RECOVERY = (
    "Call geometry(action='add') with at least one scene before run_electric -- there is nothing to "
    "build an octree from."
)


class ElectricRefusal(ValueError):
    """A refusal this module decides. **Every raise passes `code=` and `severity=` as literals**
    (`LitSceneRefusal`'s shape, R497/R554): `tests/test_advisories.py` reads a code's severity only
    from the call site."""

    def __init__(self, message: str, *, code: str, severity: str, field: str | None) -> None:
        super().__init__(message)
        self.code, self.severity, self.field = code, severity, field


# --- the published models (design §5) --------------------------------------------------------------


class _Nested(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ZoneStats(_Nested):
    mean: float
    min: float


class ZoneLimit(_Nested):
    value: float | None
    basis: Literal["licht.de p.8", "per_activity_not_in_corpus"]


class ZoneView(_Nested):
    zone: str
    group: str | None
    n_points: int
    values_initial_lux: list[float]
    values_truncated: bool
    e_initial_lux: ZoneStats
    e_maintained_lux: ZoneStats
    u_o: float | None
    u_o_raw: float | None
    u_o_limit: ZoneLimit


# Amendment 9: `edition` is typed `str` and `applicable_limits` a nullable dict, not Literals, so
# that no schema guard treats either field as a single-valued constant.
class ElectricResult(RadResult):
    """EN 12464-1 illuminance and uniformity per zone, from the placed luminaires only, no daylight.

    There is no pass or fail: compare each zone's `u_o` with its `u_o_limit` (surround 0.40 and
    background 0.10, sourced; every other zone's `value` is `null`, its basis
    `per_activity_not_in_corpus`). `applicable_limits` is always `null`. `u_o` is E_min/E_mean
    rounded half-up to two decimals, `u_o_raw` unrounded; both are `null` when the zone's mean is
    0 lux. `e_maintained_lux` is the initial value times `maintenance_factor`, whose provenance is
    always `assumed`. `values_initial_lux` is initial lux, at most 20 000 values across the zones in
    zone order (`values_truncated`, `RESULT_TRUNCATED`); each zone's statistics use all its points,
    and the file at `path` holds every value. `zones_absent` names recorded zones with no
    calculation point.
    """

    OMIT_WHEN_NONE: ClassVar[frozenset[str]] = frozenset({"advisories"})

    edition: str
    layout: Literal["task_zones", "unknown"]
    surround_mode: Literal["band", "rest_of_room"] | None
    zones: list[ZoneView]
    zones_absent: list[str]
    maintenance_factor: ProvenanceValue
    applicable_limits: dict[str, float] | None = None
    luminaires: list[LitPlacementView]
    grid: str
    path: str
    parameters: dict[str, Any]
    ambient_interpolation_radius_m: float
    octree_cube: list[float]
    job_id: str
    wall_time_s: float


class ElectricDryRunOk(RadResult):
    """`dry_run=True` and every check passed: nothing was spawned or written.

    `advisories` holds those decided before the trace; a real call can add more, such as `ZONE_UNLIT`
    and `RESULT_TRUNCATED`.
    """

    dry_run: Literal[True] = True


ElectricPayload = ElectricResult | ElectricDryRunOk | Refusal


class ElectricResponse(RootModel[ElectricPayload]):
    @classmethod
    def model_json_schema(cls, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return one_of_schema(super().model_json_schema(*args, **kwargs), ElectricResult)


# --- step 1: the arguments, before the project is opened --------------------------------------------


def _validate_arguments(maintenance_factor: float, maintenance_note: str | None,
                        quality: str | dict[str, Any], inline_timeout_s: float,
                        grid: str) -> CallToolResult | None:
    """Design §6 step 1. A JSON `true` for `maintenance_factor` never reaches here: the argument is
    `StrictFloat`, so the SDK refuses it before the tool runs, as for `luminaire.light_loss_factor`
    (R854; Amendment 6's coercion to `1.0` no longer happens)."""
    if not (math.isfinite(maintenance_factor) and 0.0 < maintenance_factor <= 1.0):
        return _refuse_argument(
            f"maintenance_factor={maintenance_factor!r} is not in (0, 1]. EN 12464-1 compares maintained "
            "illuminance, E_m = MF x initial, and the design must state MF; give the product of the "
            "LLMF, LSF, LMF and RSMF your maintenance schedule implies.", field="maintenance_factor")
    if maintenance_note is not None:
        size = len(maintenance_note.encode("utf-8"))
        if size > MAINTENANCE_NOTE_MAX_BYTES:
            return _refuse_argument(
                f"maintenance_note is {size} bytes in UTF-8; the limit is {MAINTENANCE_NOTE_MAX_BYTES} "
                "bytes (R617). Shorten it to the factor breakdown and the schedule.", field="maintenance_note")
    # `quality`, `inline_timeout_s` and `grid`'s charset (S1, final fix wave) exactly as
    # `run_point_in_time` refuses them (design §6 step 1): its own validator, called with the one
    # combination of its other arguments under which only those clauses can fire -- a lamp-only
    # illuminance run (no sky, no matrices, no BSDF states) -- so the code, field and sentence are its
    # own, never a copy that could drift.
    return _validate_point_in_time_arguments(
        quantity="illuminance", sky=None, window_group_states=None, luminaires=True,
        quality=quality, inline_timeout_s=inline_timeout_s, grid=grid)


# --- steps 2-5: the pre-spawn half ------------------------------------------------------------------


@dataclass
class _Prepared:
    """What `_prepare` proved before any spawn, for Task 10's `_run` to consume unchanged."""

    loaded: LoadedProject
    paths: ProjectPaths
    record: InputRecord
    #: Present zones only, in file order: the record, its points, and the file's text.
    zones: list[tuple[ZoneRecord, list[GridPoint], str]]
    scene_files: list[str]
    parameters: QualityParameters
    #: Every advisory knowable before the spawn -- the dry run's list and the real run's first part.
    advisories: list[Advisory]


def _double_counted(manifest: Any) -> list[str]:
    """R602: every placed type carrying a `light_loss_factor`, 1.0 included -- read through
    `lit_placements_view`, the one reader of that field on a lit run."""
    return sorted({view.type_name for view in lit_placements_view(manifest, None) if view.light_loss_factor is not None})


def _grid_record(loaded: LoadedProject, grid: str) -> InputRecord:
    """The zoned grid registered under `grid` or `sensor_grid_<grid>` (`_grid_points_file`'s two-name
    convention); an unknown name is refused as `run_point_in_time` refuses it."""
    candidates = _grid_candidates(grid)
    for record in loaded.manifest.inputs:
        if record.kind == "sensor_grid" and record.id in candidates:
            # S1 (final fix wave): the record's own id, too -- a second, independent guard, so
            # neither depends on the other being right. It fires where `grid` is itself spelled with
            # the prefix: `grid="sensor_grid_.x"` passes `is_legal_name` in `_validate_arguments`,
            # matches the record `sensor_grid_.x` through the first of the two candidates above, and
            # leaves the suffix `.x`, which is not a legal name. (The re-review's B3: this comment
            # used to call the guard unreachable while the argument guard stands.)
            if not is_legal_name(record.id.removeprefix(SENSOR_GRID_INPUT_PREFIX)):
                raise ElectricRefusal(
                    f"sensor grid {grid!r} is registered under the id {record.id!r}, which is not "
                    f"'{SENSOR_GRID_INPUT_PREFIX}' followed by a legal name. This server never writes such "
                    "an id; the manifest has been edited. Nothing was read or written. Remove the record "
                    "and re-create the grid with sensor_grid(action='create').",
                    code="ARGUMENT_OUT_OF_RANGE", severity="error", field="grid")
            if record.zones is None:
                raise ElectricRefusal(
                    f"sensor grid {grid!r} has no task-area zones, so there is no U_o to report: EN 12464-1 "
                    "computes uniformity per zone, never over the whole room. Create it with "
                    "sensor_grid(action='create', standard='en12464', task_zones=[...]) or "
                    "task_layout='unknown'.",
                    code="UNIFORMITY_ZONE_UNDEFINED", severity="error", field="grid")
            return record
    raise GridNotFoundError(
        f"no sensor grid named {grid!r} is registered in this project. Call "
        f"sensor_grid(action='create', name={grid!r}, standard='en12464', task_zones=[...]) first, or "
        "sensor_grid(action='list') to see what is registered."
    )


def _inconsistent(zone: str, message: str) -> ElectricRefusal:
    return ElectricRefusal(
        f"zone {zone!r}: {message} Re-create the grid with sensor_grid(action='create').",
        code="GRID_ZONES_INCONSISTENT", severity="error", field="grid")


#: S4 (final fix wave): a zoned grid holds at most one zone per task area, one surround per group and
#: one background -- `2 * MAX_TASK_ZONES + 1` records -- derived from R617's bound, not a new number.
_MAX_ZONE_RECORDS = 2 * MAX_TASK_ZONES + 1


def _refuse_zone_record_shape(record: InputRecord) -> None:
    """S4 (final fix wave): the record's shape, bounded **before any file is opened**. `ZoneRecord`s
    come out of a manifest (R50: untrusted), and `_read_zones` reads one file per present zone -- so
    129 records, or thousands sharing one `file_index`, re-read one 4 MB file thousands of times
    (measured: 80 records pointing at one file, +1.5 GB and 78 s on a dry run). Refused whole, as
    `GRID_ZONES_INCONSISTENT` with `field="grid"`: the record, not any one zone, is what is wrong."""
    assert record.zones is not None

    def refuse(message: str) -> ElectricRefusal:
        return ElectricRefusal(
            f"sensor grid record {record.id!r}: {message} This server never writes such a record; "
            "re-create the grid with sensor_grid(action='create').",
            code="GRID_ZONES_INCONSISTENT", severity="error", field="grid")

    if len(record.zones) > _MAX_ZONE_RECORDS:
        raise refuse(f"it records {len(record.zones)} zones, over the {_MAX_ZONE_RECORDS} a zoned grid can "
                     f"hold ({MAX_TASK_ZONES} task areas, a surround each, one background).")
    indices = [zone.file_index for zone in record.zones if zone.file_index is not None]
    if len(set(indices)) != len(indices):
        raise refuse("two of its zones name the same file; each zone has its own file.")
    if len(indices) != len(record.files):
        raise refuse(f"{len(indices)} of its zones hold points and it records {len(record.files)} file(s); "
                     "they must be equal.")
    declared = sum(zone.points for zone in record.zones)
    if declared > MATRIX_MAX_DECLARED_POINTS:
        raise refuse(f"its zones declare {declared} points, over the {MATRIX_MAX_DECLARED_POINTS} this server "
                     "sizes an allocation from (R199).")


def _read_zones(record: InputRecord, roots: tuple[Path, ...]) -> list[tuple[ZoneRecord, list[GridPoint], str]]:
    """Each present zone file, contained, opened once, and checked against its record (design §6
    step 4): its name against the zone's, its bytes against `FileRef.digest`, then its parsed count
    against `ZoneRecord.points`. A same-count edit would otherwise attribute values to the wrong
    sensors, and a count the record does not hold would split `rtrace`'s output at the wrong line.

    F13: the bytes that are digested are the bytes that are parsed -- one `open_regular_file` per
    zone, never a second open for the digest, so the file cannot change between the two. `contain`
    runs first: a zone file swapped for a link out of the roots is refused as a path, unread.

    S4 (final fix wave): the record's shape is bounded first (`_refuse_zone_record_shape`), and one
    **running byte budget** of `MAX_POINTS_BYTES` spans every zone -- the bound an unzoned grid's one
    file has, applied to the one grid a zoned record describes -- with each read capped at the
    budget left plus one byte, so a file that grows after its `fstat` is still refused. Bytes that
    are not UTF-8 are the record's inconsistency, never an uncoded `UnicodeDecodeError`.
    """
    assert record.zones is not None
    _refuse_zone_record_shape(record)
    budget = MAX_POINTS_BYTES
    read: list[tuple[ZoneRecord, list[GridPoint], str]] = []
    for zone in record.zones:
        if zone.file_index is None:
            continue
        if not 0 <= zone.file_index < len(record.files):
            raise _inconsistent(zone.zone, f"its record names file {zone.file_index}, and the grid "
                                           f"records {len(record.files)} file(s).")
        ref = record.files[zone.file_index]
        # C2a (final fix wave): a zone's file is `<zone>.pts` -- two zones with equal counts and their
        # indices swapped would otherwise pass the digest and the count and be graded on each
        # other's points (a task area against a surround's limit).
        if Path(ref.path).name != f"{zone.zone}.pts":
            raise _inconsistent(zone.zone, f"its record names the file {Path(ref.path).name!r}, and a zone's "
                                           f"file is {zone.zone + '.pts'!r}.")
        path = contain(ref.path, roots)
        try:
            with open_regular_file(path, "rb") as handle:
                # `_read_points`' bound, on the descriptor and before the read (R50), against what is
                # left of the grid's budget.
                size = os.fstat(handle.fileno()).st_size
                if size > budget:
                    raise _inconsistent(zone.zone, f"its file {path} is {size} bytes, over the "
                                                   f"{MAX_POINTS_BYTES}-byte bound on a grid's .pts files "
                                                   f"({budget} left); nothing was read.")
                data = handle.read(budget + 1)
        except OSError as exc:
            raise GridUnreadableError(
                f"{path} cannot be read as a sensor grid zone: {exc.strerror or exc}. Re-create it with "
                "sensor_grid(action='create')."
            ) from None
        if len(data) > budget:
            raise _inconsistent(zone.zone, f"its file {path} grew past the {MAX_POINTS_BYTES}-byte bound on a "
                                           "grid's .pts files while it was read.")
        budget -= len(data)
        digest_matches = bytes_digest(data) == ref.digest
        if not digest_matches:
            raise _inconsistent(zone.zone, f"its file {path} no longer holds the bytes the grid recorded "
                                           "(digest differs).")
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            raise _inconsistent(zone.zone, f"its file {path} is not UTF-8 text.") from None
        points = _parse_points(text, path)
        if len(points) != zone.points:
            raise _inconsistent(zone.zone, f"its file {path} holds {len(points)} points and its record "
                                           f"{zone.points} (digest matches).")
        read.append((zone, points, text))
    return read


def _knowable_advisories(loaded: LoadedProject, record: InputRecord,
                         zones: list[tuple[ZoneRecord, list[GridPoint], str]], *,
                         maintenance_factor: float, maintenance_note: str | None) -> list[Advisory]:
    """Every advisory knowable before the spawn (design §6 step 5) and nothing else -- the dry run's
    list and the real run's first part, built once."""
    manifest = loaded.manifest
    found: list[Advisory] = [
        Advisory(code="EN12464_EDITION", severity="warn", field=None, message=(
            f"reported against {EDITION}. Its task-area uniformity is per activity and not in this "
            "server's corpus, so task-area limits are null; 2011 is superseded by EN 12464-1:2021, which "
            "is not in the corpus either.")),
        Advisory(code="EN12464_ASSESSMENT_INCOMPLETE", severity="warn", field=None, message=(
            "not assessed: the zones' illuminance requirements (per activity), cylindrical illuminance "
            "E_z and the modelling index, wall and ceiling illuminance and uniformity, and circulation "
            "areas. U_o is reported beside its sourced limit; there is no pass or fail (R608).")),
        Advisory(code="MAINTENANCE_FACTOR_APPLIED", severity="info", field="maintenance_factor", message=(
            f"maintained = {maintenance_factor:g} x initial; provenance assumed"
            + ("; note given." if maintenance_note else
               "; no note -- EN 12464-1 asks the design to state the LLMF, LSF, LMF and RSMF behind it "
               "and the maintenance schedule."))),
    ]
    # R607: no RESULT_IS_INITIAL_ILLUMINANCE. `field=None` (R574) reaches `lit_scene._header` too: a
    # hand-edited type record whose `header` is unreadable is refused there, with no field, as
    # `LUMINAIRE_FILES_CHANGED` -- through `lit_advisories`, not `verify_lit_files`, which never reads
    # the header (F9).
    found.extend(lit_advisories(manifest, quantity="illuminance", field=None, initial_notice=False))
    # R602: the header's factors stay applied by ies2rad and are restated, once per placed type, in
    # `IesHeader.applied_factors` -- the one spelling `luminaire(action="import")` announces.
    types = _types_by_name(manifest, None)
    # Not reachable with an unreadable `type_name`: `lit_advisories` above already ran
    # `lit_placements_view`, whose `_type_of` refuses (`LUMINAIRE_FILES_CHANGED`) every placement whose
    # `type_name` is missing, unhashable, or names no type record, so each value here is a registered
    # type's name. Read through `_recorded` anyway, so a reordering cannot make it an uncoded error.
    placed = sorted({_recorded(p, "type_name", None) for p in placement_records(manifest) if p.registered_by})
    for name in placed:
        for label, value in _header(types[name], None).applied_factors:
            found.append(Advisory(code="IES_FACTOR_APPLIED", severity="warn", field=None, message=(
                f"type {name!r}: the header's {label} {value:g} is applied by ies2rad; it is an operating "
                "condition, not a maintenance loss, and is not part of maintenance_factor (R602).")))

    assert record.zones is not None
    points_of = {zone.zone: points for zone, points, _text in zones}
    for zone in record.zones:
        if zone.file_index is None and zone.zone.startswith("surround"):
            found.append(Advisory(code="GRID_ZONE_ABSENT", severity="warn", field="grid", message=(
                f"zone {zone.zone!r} holds no calculation point, so its task area(s) have no evaluated "
                "surround.")))
        elif zone.file_index is None and zone.zone == "background":
            found.append(Advisory(code="GRID_ZONE_ABSENT", severity="info", field="grid", message=(
                f"zone {zone.zone!r} holds no calculation point: the surrounds and the wall strip cover "
                "the rest of the room.")))
        if zone.task_in_strip:
            found.append(Advisory(code="TASK_IN_WALL_STRIP", severity="info", field="grid", message=(
                f"{zone.zone} reaches into the {WALL_STRIP_M:g} m wall strip; its points there are kept (licht.de p.17).")))
        if zone.zone == "background" and zone.file_index is not None and zone.circumscribing_m is not None:
            x0, y0, x1, y1 = zone.circumscribing_m
            if min(x1 - x0, y1 - y0) < BACKGROUND_MIN_WIDTH_M:
                found.append(Advisory(code="BACKGROUND_NARROW", severity="info", field="grid", message=background_narrow_message()))
        if zone.zone in points_of and len(points_of[zone.zone]) < ZONE_MIN_POINTS:
            found.append(Advisory(code="ZONE_TOO_FEW_POINTS", severity="warn", field="grid", message=(
                f"{zone.zone} has {len(points_of[zone.zone])} point(s), fewer than {ZONE_MIN_POINTS}; its "
                "U_o is an artefact of the count.")))
    arguments = record.registered_by.arguments if record.registered_by else {}
    if arguments.get("surround") == "rest_of_room":
        found.append(Advisory(code="SURROUND_IS_ASR_GEOMETRY", severity="info", field="grid", message=(
            "this grid's surround is the rest of the room less the wall strip: the guides' reading for an "
            "office with one known work station (licht.de p.11, a.1; ZVEI a.), and also ASR A3.4's geometry "
            f"(licht.de p.8). It is graded here against EN 12464-1's {SURROUND_UO_LIMIT:.2f}, where ASR asks "
            f"{ASR_SURROUND_UO:g} (R619, R652).")))

    # R621, licht.de p.17: the plane is the sensors' own height, read from the files just verified.
    sensors = [(p.x, p.y, p.z) for _zone, points, _text in zones for p in points]
    # N-111's remainder (R844): each position through the guarded accessor, so a hand-edited
    # `positions` that is not a list of triples is `LUMINAIRE_FILES_CHANGED`, not an uncoded error.
    lamps = [position[2] for p in placement_records(manifest) if p.registered_by
             for position in _recorded(p, "positions", None, read=_points)]
    if sensors and lamps:
        mounting = min(lamps) - max(z for _x, _y, z in sensors)
        wide = [(zone.zone, max(zone.spacing_m)) for zone, _points, _text in zones
                if zone.spacing_m and max(zone.spacing_m) >= mounting]
        if wide and mounting <= 0:
            # L-109 (R830): a height at or below zero is not a mounting height to quote.
            found.append(Advisory(code="GRID_SPACING_EXCEEDS_MOUNTING_HEIGHT", severity="warn", field="grid", message=(
                "the lowest luminaire is at or below the task plane, so no grid spacing can be finer than "
                f"its mounting height ({', '.join(f'{name} {spacing:.3f} m' for name, spacing in wide)}); "
                "licht.de p.17 asks for a grid finer than the mounting height.")))
        elif wide:
            found.append(Advisory(code="GRID_SPACING_EXCEEDS_MOUNTING_HEIGHT", severity="warn", field="grid", message=(
                f"zone spacing is not less than the lowest luminaire's {mounting:.3f} m above the task "
                f"plane in {', '.join(f'{name} ({spacing:.3f} m)' for name, spacing in wide)}; licht.de "
                "p.17 asks for a grid finer than the mounting height.")))
    near = near_field_advisory(manifest, sensors, None)
    if near is not None:
        found.append(near)
    return found


def _prepare(runtime: Runtime, *, project: str, grid: str, maintenance_factor: float,
             maintenance_note: str | None, quality: str | dict[str, Any]) -> _Prepared:
    """Design §6 steps 2-5, in the contract's order, spawning nothing and writing nothing."""
    loaded = runtime.open(project)
    # Step 2: the lamps, first (R574: no `luminaires` argument, so no field).
    verify_lit_files(runtime, loaded, field=None, label=_LABEL)
    # Step 3: the double count (R602).
    doubled = _double_counted(loaded.manifest)
    if doubled:
        raise ElectricRefusal(
            f"luminaire types {', '.join(repr(name) for name in doubled)} carry a light_loss_factor, and "
            "maintenance_factor is the only home of the maintenance loss (R602). A light_loss_factor may "
            "also bundle losses that are not maintenance; those belong in the IES file or in "
            "maintenance_note, not in both. Re-import these types without light_loss_factor.",
            code="MAINTENANCE_FACTOR_DOUBLE_COUNT", severity="error", field="maintenance_factor")
    # Step 4: geometry, then the grid (Amendment 3: `_validate_scene`'s parts, around this tool's own
    # zone read). `paths` is the base the scan and, in Task 10, the spawn both resolve against.
    paths = ProjectPaths(loaded.paths.root)
    scene_files = _scene_files(loaded, luminaires=True, no_geometry_recovery=_NO_GEOMETRY_RECOVERY)
    record = _grid_record(loaded, grid)
    assert record.zones is not None
    if all(zone.file_index is None for zone in record.zones):
        # C2b (final fix wave): here, not in `_run`, so a dry run refuses what the real run refuses. A
        # zoned record whose every zone is absent has no sensor to trace: refused as
        # `run_point_in_time` refuses an empty grid, before any file is read.
        raise GridEmptyOnDiskError(
            f"sensor grid {grid!r} records no zone with a calculation point, so there is nothing to "
            "trace. Re-create it with sensor_grid(action='create', standard='en12464', task_zones=[...])."
        )
    zones = _read_zones(record, runtime.roots)
    # Step 5: the `!` scan (check 3.9), then the grid's floor.
    _scan_scene_for_inline_commands(scene_files, cwd=paths.root)
    # R776: a zoned record carries `height`, so its points are judged at the floor plus that height
    # on any floor. After the scan, as in `run_point_in_time` and `run_annual`: the last of steps
    # 2-5. Not the last refusal before a spawn: `_knowable_advisories` below can still refuse an
    # unreadable recorded IES header, `LUMINAIRE_FILES_CHANGED`.
    arguments = record.registered_by.arguments if record.registered_by is not None else {}
    refuse_grid_off_recorded_floor(
        grid, arguments.get("polygon"), [point for _zone, points, _text in zones for point in points],
        height=arguments.get("height"),
    )
    return _Prepared(
        loaded=loaded, paths=paths, record=record, zones=zones, scene_files=scene_files,
        parameters=resolve_quality(quality),
        advisories=[
            *_knowable_advisories(loaded, record, zones, maintenance_factor=maintenance_factor,
                                  maintenance_note=maintenance_note),
            # R818: a mirror or prism in the files this run compiles, lamps included.
            *filter(None, [light_redirecting_advisory(scene_files, tool="run_electric")]),
        ],
    )


# --- steps 6-8: the trace, the zone maths, the results file -----------------------------------------


def _write_results(paths: ProjectPaths, *, grid: str, zone_values: list[tuple[str, list[float]]],
                   roots: Sequence[Path]) -> Path:
    """Every value, `<zone>\t<value>` per line in zone order, staged and renamed in (R612): a failed
    write leaves the previous file, and a hardlink at the name is replaced, never written through.

    Contained before the `mkdir`, as `point_in_time_tool._write_results_file` is, and written as it
    is, through `replace_file_atomically` (R855): a read-only file is refused, a writable one keeps
    its permission bits.
    """
    # N-112 (R851): the path as named. `contain` returns the resolved path, so the check below was
    # handed a symlink's target and `os.replace` overwrote that file (measured: an in-root
    # `victim.txt` linked from `electric_<grid>.txt` was replaced with the results).
    out_path = contain_as_named(paths.results / f"electric_{grid}.txt", roots)
    # N-113 (R851): a link at any component below the project root is refused before the `mkdir`
    # and any write; measured, `08_results` aimed at another in-root directory took the results.
    refuse_linked_components(out_path, below=paths.root)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    # A directory, FIFO or symlink at the name is refused (`PROJECT_PATH_NOT_A_REGULAR_FILE`) rather
    # than left for `os.replace` to fail on uncoded or to swap out from under whoever made it.
    # *(Corrected 2026-10-05, N-112, R851: until then the symlink half of this did not hold.)*
    refuse_existing_non_regular_destination(out_path)
    # R855 (Task 16's deferred item): staged under a name drawn per call and renamed over the file
    # (`replace_file_atomically`, as `run_point_in_time` writes), so a read-only results file is
    # refused rather than replaced and a writable one keeps its permission bits; the fixed
    # `<name>.partial` and its planted-name race are gone. A write the disk or the permissions
    # refuse is `PROJECT_WRITE_FAILED` (it was `PATH_OUTSIDE_ROOT`, L-112).
    data = "".join(f"{zone}\t{value!r}\n" for zone, values in zone_values for value in values).encode("utf-8")
    try:
        replace_file_atomically(out_path, lambda handle: handle.write(data))
    except NotARegularFileError:
        raise
    except OSError as exc:
        raise write_failure(out_path, exc, what="the results file", outcome=(
            "The trace ran, but no result was registered; a results file already there is unchanged.")) from None
    return out_path.resolve()  # the spelling `contain` gave before N-112


def _mismatch(message: str) -> ElectricRefusal:
    """Design §6 step 6: `rtrace`'s output does not give one value per traced point. A server fault,
    never a caller's; refused, coded, so the zones are never split at the wrong line."""
    return ElectricRefusal(
        f"{message} The values cannot be attributed to the zones. This is a fault inside the server, "
        "not something about the arguments; nothing was written, and the failure should be reported.",
        code="RTRACE_OUTPUT_MISMATCH", severity="error", field=None)


def _run(runtime: Runtime, prepared: _Prepared, *, grid: str, maintenance_factor: float,
         maintenance_note: str | None) -> CallToolResult:
    """Design §6 steps 6-8: one trace over every zone's points, the split, the maths, the file."""
    # `_prepare` refused a record with no present zone (C2b), and `_read_zones` a present zone whose
    # file holds no point (`_parse_points`), so there is at least one point to trace.
    points = [p for _zone, zone_points, _text in prepared.zones for p in zone_points]
    # R568/R569: the lit run's one project write before the spawn, after every refusal above.
    commit_lit_support(runtime, prepared.loaded, tool="run_electric")
    env = discover()
    # One stdin, in zone order: each file's text, each ending in a newline so two files never join.
    text = "".join(t if t.endswith("\n") else t + "\n" for _zone, _points, t in prepared.zones)
    scene = _ValidatedScene(points=points, points_text=text, scene_files=prepared.scene_files, polygon=None)
    # R611: a temporary octree at the project root, never `run_point_in_time`'s octree
    # (`06_octrees/point_in_time.oct` since R730; `_point_in_time.oct` at the root before), gone
    # whatever happens.
    try:
        handle, name = tempfile.mkstemp(dir=prepared.paths.root, prefix=".electric-", suffix=".oct")
    except OSError as exc:
        # L-112 (R830): the project root does not take the temporary octree (a permission withdrawn,
        # a full volume) -- refused, coded `PROJECT_WRITE_FAILED` since R855 (it was
        # `PATH_OUTSIDE_ROOT`, the containment code).
        raise write_failure(prepared.paths.root / ".electric-<random>.oct", exc, what="the temporary octree",
                            outcome="Nothing has been traced and no results file was written.") from None
    os.close(handle)
    octree_path = Path(name)
    started = time.monotonic()
    try:
        pipeline = _run_pipeline(env, scene=scene, quantity="illuminance", sky_spec=None,
                                 parameters=prepared.parameters, cwd=prepared.paths.root, luminaires=True,
                                 octree_path=octree_path, scene_advisories=False)
    except RtraceOutputError as exc:
        raise _mismatch(f"rtrace's output did not parse as one value per point: {exc}.") from None
    finally:
        octree_path.unlink(missing_ok=True)
    wall_time_s = time.monotonic() - started
    if len(pipeline.values) != len(points):
        raise _mismatch(f"rtrace returned {len(pipeline.values)} values for {len(points)} points.")

    zone_values: list[tuple[str, list[float]]] = []
    offset = 0
    for zone, zone_points, _text in prepared.zones:
        zone_values.append((zone.zone, pipeline.values[offset:offset + len(zone_points)]))
        offset += len(zone_points)
    results = evaluate([(zone.zone, zone.group, values)
                        for (zone, _points, _text), (_name, values) in zip(prepared.zones, zone_values)],
                       maintenance_factor)
    committed = _write_results(prepared.paths, grid=grid, zone_values=zone_values, roots=runtime.roots)

    # `ZONE_TOO_FEW_POINTS` is `_knowable_advisories`' (knowable before the spawn); `evaluate` adds none.
    advisories = list(prepared.advisories) + list(pipeline.advisories)
    for result in results:
        if result.unlit:
            advisories.append(Advisory(code="ZONE_UNLIT", severity="warn", field="zones", message=(
                f"zone {result.zone!r}: its mean is {result.e_initial_mean:.3g} lux, 0 or under "
                f"{ZONE_UNLIT_FRACTION * 100:g} % of "
                "the brightest zone's mean; its U_o compares values the lamps barely reach.")))
    # R612: the inline bound spans the zones, filled in zone order; statistics are over every value.
    # Read at call time, so the module global (and a test's monkeypatch of it) is the one in force.
    bound = MAX_INLINE_VALUES
    budget = bound
    views: list[ZoneView] = []
    for result in results:
        inline = list(result.values[:max(budget, 0)])
        budget -= len(inline)
        views.append(ZoneView(
            zone=result.zone, group=result.group, n_points=result.n_points, values_initial_lux=inline,
            values_truncated=len(inline) < result.n_points,
            e_initial_lux=ZoneStats(mean=result.e_initial_mean, min=result.e_initial_min),
            e_maintained_lux=ZoneStats(mean=result.e_maintained_mean, min=result.e_maintained_min),
            u_o=result.u_o, u_o_raw=result.u_o_raw,
            u_o_limit=ZoneLimit(value=result.limit_value, basis=result.limit_basis)))  # type: ignore[arg-type]
    if any(view.values_truncated for view in views):
        advisories.append(Advisory(code="RESULT_TRUNCATED", severity="warn", field="zones", message=(
            f"values_initial_lux carries the first {bound} of {len(points)} values across the zones, "
            "filled in zone order; each zone's statistics are over all of its values, not over this "
            f"list. The complete set, one `<zone>\t<lux>` line per point, is the committed file at "
            f"`path` ({committed}), also linked as a resource on this result.")))

    record = prepared.record
    assert record.zones is not None
    arguments = record.registered_by.arguments if record.registered_by else {}
    unknown = arguments.get("task_layout") == "unknown"
    payload = ElectricResult(
        edition=EDITION,
        layout="unknown" if unknown else "task_zones",
        surround_mode=None if unknown else ("rest_of_room" if arguments.get("surround") == "rest_of_room" else "band"),
        zones=views,
        zones_absent=[zone.zone for zone in record.zones if zone.file_index is None],
        maintenance_factor=ProvenanceValue(value=maintenance_factor, provenance="assumed", source=None,
                                           note=maintenance_note),
        applicable_limits=None,
        luminaires=lit_placements_view(prepared.loaded.manifest, None),
        grid=grid,
        path=str(committed),
        parameters=parameters_dict(pipeline.parameters),
        ambient_interpolation_radius_m=ambient_interpolation_radius_m(pipeline.parameters, pipeline.octree_cube[3]),
        octree_cube=list(pipeline.octree_cube),
        # No job is submitted (R613): a synthesised id, `run_point_in_time`'s spelling.
        job_id=f"inline-{int(started * 1000)}",
        wall_time_s=wall_time_s,
        advisories=advisories or None,
    ).payload()
    return CallToolResult(
        content=[
            TextContent(type="text", text=json.dumps(payload, indent=2)),
            ResourceLink(uri=committed.as_uri(), name=f"{prepared.loaded.paths.name}/{grid} electric results",
                         description=("One `<zone>\t<lux>` line per point, in zone order: initial "
                                      "illuminance, the values `zones` summarises."),
                         mimeType=RESULT_MIME_TYPE),
        ],
        structured_content=payload,
        is_error=False,
    )


def run_electric(
    project: Annotated[
        str | None,
        Field(description="the project handle, with geometry, a placed luminaire and a zoned grid."),
    ] = None,
    grid: Annotated[
        str | None,
        Field(
            description=(
                "a zoned en12464 grid -- sensor_grid(action='create', standard='en12464', "
                "task_zones=[...]) or task_layout='unknown'. An unzoned grid is refused "
                "(UNIFORMITY_ZONE_UNDEFINED)."
            )
        ),
    ] = None,
    maintenance_factor: Annotated[
        StrictFloat | None,
        Field(
            description=(
                "REQUIRED, in (0, 1], no default. The only home of the maintenance loss: a placed "
                "type imported with light_loss_factor is refused (MAINTENANCE_FACTOR_DOUBLE_COUNT). "
                "Maintained = MF x initial; U_o does not depend on it."
            )
        ),
    ] = None,
    maintenance_note: Annotated[
        str | None,
        Field(
            description=(
                "the LLMF / LSF / LMF / RSMF breakdown and schedule, at most 4096 bytes UTF-8; "
                "published as maintenance_factor.note."
            )
        ),
    ] = None,
    quality: Annotated[
        str | dict[str, QualityValue],
        Field(description="as run_point_in_time."),
    ] = "standard",
    dry_run: Annotated[
        bool,
        Field(
            description=(
                "every refusal and every advisory knowable before a spawn; spawns and writes "
                "nothing."
            )
        ),
    ] = False,
    inline_timeout_s: Annotated[
        StrictFloat,
        Field(description="as run_point_in_time -- every call runs inline."),
    ] = DEFAULT_INLINE_TIMEOUT_S,
) -> ElectricResponse:
    """EN 12464-1:2011 illuminance and per-zone U_o from the placed luminaires, lamps only.

    Use it for the electric-lighting illuminance and uniformity of a placed luminaire layout, per zone
    of a zoned grid.

    Returns per zone the initial and maintained mean and min, U_o rounded to two decimals, and the
    sourced limit beside it (surround 0.40, background 0.10; task areas null, per activity). There is
    no pass or fail. Not exportable: the run is inline and registers no artifact.
    """
    if (refusal := refuse_missing("run_electric", project=project, grid=grid,
                                  maintenance_factor=maintenance_factor)) is not None:
        return refusal  # type: ignore[return-value]
    assert project is not None and grid is not None and maintenance_factor is not None
    if (refusal := _validate_arguments(maintenance_factor, maintenance_note, quality, inline_timeout_s,
                                       grid)) is not None:
        return refusal  # type: ignore[return-value]
    runtime = current()
    try:
        prepared = _prepare(runtime, project=project, grid=grid, maintenance_factor=maintenance_factor,
                            maintenance_note=maintenance_note, quality=quality)
        if dry_run:
            return emit(ElectricDryRunOk(advisories=prepared.advisories or None))  # type: ignore[return-value]
        return _run(runtime, prepared, grid=grid, maintenance_factor=maintenance_factor,  # type: ignore[no-any-return]
                    maintenance_note=maintenance_note)
    except (LitSceneRefusal, PhotometryUnverifiedError, ElectricRefusal) as exc:
        # The code, severity and field are the exception's own (R556/R554).
        logger.info("run_electric refused: %s", exc)
        return refuse(str(exc), [Advisory(code=exc.code, severity=exc.severity, field=exc.field,  # type: ignore[return-value]
                                          message=str(exc))])
    # From here, `run_point_in_time`'s own clauses, in its order and shape (point_in_time_tool.py).
    except GridEmptyOnDiskError as exc:
        logger.info("run_electric refused: %s", exc)
        return refuse(str(exc), [Advisory(code=exc.code, severity="error", field="grid",  # type: ignore[return-value]
                                          message=str(exc))])
    except GridUnreadableError as exc:
        logger.info("run_electric refused: %s", exc)
        return refuse(str(exc), [Advisory(code=exc.code, severity="error", field="grid",  # type: ignore[return-value]
                                          message=str(exc))])
    except GridNotFoundError as exc:
        logger.info("run_electric refused: %s", exc)
        return refuse(str(exc))  # type: ignore[return-value]
    except NoGeometryRegisteredError as exc:
        logger.info("run_electric refused: %s", exc)
        return refuse(str(exc), [Advisory(code=exc.code, severity="error", field="project",  # type: ignore[return-value]
                                          message=str(exc))])
    except SceneInlineCommandError as exc:
        logger.info("run_electric refused: %s", exc)
        return refuse(str(exc), [Advisory(  # type: ignore[return-value]
            code=getattr(exc, "code", "SCENE_INLINE_COMMAND"), severity="error", field="project",
            message=str(exc))])
    except (EmptyOctreeError, OctreeSetOverflowError, OctreeTooManyFilesError) as exc:
        logger.info("run_electric refused: %s", exc)
        code = getattr(exc, "code", None)
        if code is None:
            return refuse(str(exc))  # type: ignore[return-value]
        return refuse(str(exc), [Advisory(code=code, severity=getattr(exc, "severity", "error"),  # type: ignore[return-value]
                                          message=str(exc))])
    except NotARegularFileError as exc:
        # R48/R58: something not a regular file at a name this run writes (the results file's
        # staging name, a planted octree destination) -- refused, never followed.
        logger.info("run_electric refused: %s", exc)
        return refuse(str(exc), [Advisory(code="PROJECT_PATH_NOT_A_REGULAR_FILE", severity="error",  # type: ignore[return-value]
                                          field="project", message=str(exc))])
    except RadianceNotFound as exc:
        logger.info("run_electric refused: %s", exc)
        return refuse(str(exc), [Advisory(code="RADIANCE_NOT_FOUND", severity="error",  # type: ignore[return-value]
                                          message=str(exc))])
    except RadianceError as exc:
        logger.info("run_electric failed: %s", exc)
        return refuse(str(exc))  # type: ignore[return-value]
    except (ProjectError, PathOutsideRoot, RootsNotConfigured, CodedWriteRefusal) as exc:
        logger.info("run_electric refused: %s", exc)
        return refuse_engine_error(exc)  # type: ignore[return-value]
    except ArithmeticError as exc:
        # `run_point_in_time`'s last resort: an uncaught exception is a JSON-RPC error with no
        # `structuredContent` (check 0.4); a code-less refusal is the better failure.
        logger.warning("run_electric failed in arithmetic: %r", exc)
        return refuse(  # type: ignore[return-value]
            f"run_electric could not complete: {type(exc).__name__}: {exc}. This is an arithmetic "
            "overflow inside the server rather than something about the arguments; the failure should be "
            "reported.")
