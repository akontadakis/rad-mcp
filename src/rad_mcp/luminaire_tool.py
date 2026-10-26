"""`luminaire` -- import LM-63 photometry and place luminaires (docs/tool-surface.md §18, Phase 11a).

Plumbing only (CONTEXT.md): the LM-63 screen is `rad_core.photometry_file`, placement arithmetic
`rad_core.luminaire_layout`, and every spawn `rad_engine.ies`/`rad_engine.convert`. Spec:
docs/superpowers/specs/2026-09-13-phase-11a-luminaire-placement-design.md §4-§6.

**The order inside `import` is the security boundary.** `name` is validated before anything is
read; the caller's file is read once, under `IES_MAX_BYTES`, and screened by `screen_ies` before the
install is even looked for -- so a luminous-geometry header (whose `[LUMINOUSGEOMETRY]` value
`ies2rad` hands to a shell) is refused on a machine with or without Radiance, and no byte of a
refused file ever reaches a spawn. `rad_engine.ies.convert_ies` screens again as its own first line.
"""

from __future__ import annotations

import dataclasses
import logging
import math
import os
import stat
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Annotated, Any, ClassVar, Literal

from mcp_types import CallToolResult, ResourceLink
from pydantic import BaseModel, ConfigDict, Field, RootModel, StrictFloat

from rad_core.geometry import GeometryError, metres_per_unit
from rad_core.luminaire_layout import (
    LayoutError,
    array_line,
    cad_position,
    guard_islands,
    hand_line,
    island_seat,
    PLACEMENT_MAX_INSTANCES,
    instance_positions,
    is_compound,
    islands,
    EmitterInstance,
    SeatChange,
    marker_seat,
    refuse_intersecting_emitters,
    rotate,
    rotated_extent,
    rotation_flags,
    rotation_matrix,
    seat_change,
)
from rad_core.manifest import FileRef, InputRecord, ProvenanceValue, ToolCall
from rad_core.material_map import MATERIAL_MAP_MAX_BYTES, parse_material_map
from rad_core.obj import OBJ_MAX_BYTES, parse_obj
from rad_core.photometry_file import (
    IES_MAX_BYTES,
    IesHeaderError,
    IesUnsupportedError,
    normalised_colour,
    screen_ies,
)
from rad_engine import ProjectPaths, RadianceNotFound, bytes_digest, discover, open_project, register_input, save_manifest
from rad_engine.convert import (
    GeometryConversionError,
    ScratchOutsideRootsError,
    scratch_directory,
    transform_points,
    xform_argv,
)
from rad_engine.ies import (
    SOURCE_CAL_BYTES,
    SOURCE_CAL_DIGEST,
    IesConversionError,
    PhotometryUnverifiedError,
    convert_ies,
    ies2rad_argv,
    modifier_collisions,
    source_cal_state,
    verify_photometry,
)
from rad_engine.octree import SceneInlineCommandError, refuse_inline_commands_in_text
from rad_engine.project import LoadedProject, ProjectError
from rad_engine.run import RadianceError, RadianceTimeoutError
from rad_engine.safe_io import (
    FileTooLargeError,
    NotARegularFileError,
    open_regular_file,
    open_staging_file,
    read_capped,
    refuse_existing_non_regular_destination,
    resolved_parent_within,
)
from rad_mcp.envelope import Advisory, RadResult, Refusal, emit, one_of_schema, refuse, refuse_missing
from rad_mcp.geometry_tool import _ASSEMBLY_TIMEOUT_S, _cap_for, drift
from rad_mcp.geometry_tool import _INPUT_PREFIX as _GEOMETRY_PREFIX
from rad_mcp.materials_tool import InputStillBuiltFrom, refuse_if_built_from, replay_materials
from rad_mcp.names import is_legal_name, name_refusal_reason
from rad_mcp.paths import PathOutsideRoot, RootsNotConfigured, contain
from rad_mcp.runtime import Runtime, current, refuse_engine_error

__all__ = ["DESTRUCTIVE_ACTIONS", "LuminaireResult", "luminaire"]

logger = logging.getLogger(__name__)

Action = Literal["import", "place", "list", "remove"]
DESTRUCTIVE_ACTIONS = ("import", "place", "remove")
TYPE_PREFIX = "luminaire_type_"
PLACEMENT_PREFIX = "luminaire_placement_"
SUPPORT_ID = "luminaire_support_source_cal"
#: The project-relative directory a placement's `!xform` line names its type file under -- the
#: manifest kind prefix (`TYPE_PREFIX`) is a different string and is never written into scene text.
TYPES_PREFIX = "12_luminaires/types"


class LuminaireRefusal(ValueError):
    """A refusal this module decides. **Every raise passes `severity=` as a literal** (R497):
    `tests/test_advisories.py` reads a code's severity only from the call site, and a default here is
    invisible to it."""

    def __init__(self, message: str, *, code: str, field: str | None, severity: str = "error") -> None:
        super().__init__(message)
        self.code, self.field, self.severity = code, field, severity


class _Nested(BaseModel):
    model_config = ConfigDict(extra="forbid")


#: `light_loss_factor`'s `{value, provenance}` is the server's one provenance-wrapped shape,
#: `rad_core.manifest.ProvenanceValue`, not a local narrowing of it: `tests/test_server.py` check 2.9
#: requires every published provenance-wrapped object to constrain `provenance` to all three values
#: and, since that admits `default`, to publish `source`. The value here is always `assumed`.
ValueProvenanceView = ProvenanceValue


class ColourView(_Nested):
    given: list[float] | None
    normalised: list[float] | None
    provenance: Literal["assumed", "default"]


class Field2View(_Nested):
    value: float
    meaning: Literal["factor", "reserved", "file_generation_type"]


class FootprintView(_Nested):
    x: float
    y: float
    z: float


class TypeView(_Nested):
    name: str
    format: str
    photometric_type: Literal["C"]
    tilt: Literal["NONE", "INCLUDE"]
    lamps: int
    lumens: float | None
    photometry: Literal["relative", "absolute"]
    candela_multiplier: float
    ballast_factor: float
    field2: Field2View
    watts: float | None
    shape_class: str | None
    footprint_m: FootprintView | None
    colour: ColourView
    light_loss_factor: ValueProvenanceView | None
    files: list[str]


class SourceRefView(_Nested):
    path: str
    digest: str


class PlacementView(_Nested):
    name: str
    type_name: str
    route: Literal["hand", "array", "geometry"]
    instances: int
    positions: list[list[float]]
    rot: list[float] | None
    files: list[str]
    sources: list[SourceRefView] | None = None


class SupportView(_Nested):
    package_digest: str
    matches_package: bool


class RemovedView(_Nested):
    kind: Literal["type", "placement"]
    name: str
    files: list[str]


class LuminaireBase(RadResult):
    project: str
    root: str


class LuminaireImported(LuminaireBase):
    OMIT_WHEN_NONE: ClassVar[frozenset[str]] = frozenset({"advisories", "dry_run", "argv"})
    action: Literal["import"]
    type: TypeView
    dry_run: Literal[True] | None = None
    argv: list[list[str]] | None = None


class LuminairePlaced(LuminaireBase):
    OMIT_WHEN_NONE: ClassVar[frozenset[str]] = frozenset({"advisories", "dry_run", "argv"})
    action: Literal["place"]
    placements: list[PlacementView]
    dry_run: Literal[True] | None = None
    argv: list[list[str]] | None = None


class LuminaireListed(LuminaireBase):
    action: Literal["list"]
    types: list[TypeView]
    placements: list[PlacementView]
    support: SupportView | None


class LuminaireRemoved(LuminaireBase):
    OMIT_WHEN_NONE: ClassVar[frozenset[str]] = frozenset({"advisories", "dry_run"})
    action: Literal["remove"]
    removed: RemovedView
    dry_run: Literal[True] | None = None


LuminairePayload = LuminaireImported | LuminairePlaced | LuminaireListed | LuminaireRemoved | Refusal


class LuminaireResult(RootModel[LuminairePayload]):
    @classmethod
    def model_json_schema(cls, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return one_of_schema(super().model_json_schema(*args, **kwargs), LuminaireBase)


def _refuse_argument(message: str, *, field: str) -> CallToolResult:
    return refuse(message, [Advisory(code="ARGUMENT_OUT_OF_RANGE", severity="error", field=field, message=message)])


def _emit_with_links(result: RadResult, files: Sequence[str]) -> CallToolResult:
    emitted = emit(result)
    links = [ResourceLink(uri=Path(f).as_uri(), name=Path(f).name, description="luminaire file", mimeType="text/plain") for f in files]
    return CallToolResult(content=[*emitted.content, *links], structured_content=emitted.structured_content, is_error=False)


_ARRAY_KEYS = frozenset({"count_x", "spacing_x", "count_y", "spacing_y"})


def _is_number(value: Any) -> bool:
    """A finite `int` or `float` that is not a `bool`. An `int` past ~1.8e308 is not one:
    `math.isfinite` raises `OverflowError` on it rather than answering (as in
    `rad_engine.export._recorded_real`, R737). It checks caller arguments and material-map rows as
    well as recorded ones; a manifest holding one is refused when it loads (R743)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _is_triple(value: Any) -> bool:
    return isinstance(value, list) and len(value) == 3 and all(_is_number(v) for v in value)


def _refuse_hand_arguments(*, name: str | None, type_name: str | None, position: list[float] | None,
                           rot: list[float] | None, array: dict[str, Any] | None) -> CallToolResult | None:
    """`place`'s hand/array argument shapes, refused on their face. `name`'s **legality** is already
    checked once in `_dispatch` (R523), before any open, for every action but `list` -- re-spelling
    `is_legal_name` here would duplicate that check under a second message (R531); this only checks
    that `name` and `type_name` were given at all."""
    missing = refuse_missing("luminaire", name=name, type_name=type_name)
    if missing is not None:
        return missing
    if not _is_triple(position):
        return _refuse_argument(f"position must be three finite numbers in metres, got {position!r}.", field="position")
    if array is not None:
        if set(array) != _ARRAY_KEYS:
            return _refuse_argument(f"array must have exactly {sorted(_ARRAY_KEYS)}, got {sorted(array)}.", field="array")
        counts_ok = all(isinstance(array[k], int) and not isinstance(array[k], bool) and array[k] >= 1 for k in ("count_x", "count_y"))
        spacings_ok = all(_is_number(array[k]) and array[k] > 0 for k in ("spacing_x", "spacing_y"))
        if not (counts_ok and spacings_ok):
            return _refuse_argument("array counts must be whole numbers >= 1 and spacings finite numbers > 0.", field="array")
    return None


def luminaire(
    action: Annotated[
        Action,
        Field(
            description=(
                'Which operation to run: "import" (LM-63 file to a luminaire type), "place" (put copies of a type in the project), "list" (report records), or "remove" (delete a record). Required.'
            )
        ),
    ],
    project: Annotated[
        str | None,
        Field(
            description=(
                'The project handle (its name). Required by every action, `list` included.'
            )
        ),
    ] = None,
    name: Annotated[
        str | None,
        Field(
            description=(
                "Record name. Required by `import` (the type's name), by hand/array `place` (the placement's name) and by `remove`. Must be a legal name; on `import` and `place` a name differing from an existing record of the same kind only by case is refused, while the identical name replaces that record. Refused with `place` + `geometry` (the material map's rows name the placements); silently ignored by `list`."
            )
        ),
    ] = None,
    kind: Annotated[
        Literal["type", "placement"] | None,
        Field(
            description=(
                '`remove` only, and required there: which record `name` refers to, "type" or "placement". Silently ignored by `import`, `place` and `list`.'
            )
        ),
    ] = None,
    ies: Annotated[
        str | None,
        Field(
            description=(
                '`import` only, and required there: absolute path to the LM-63 (IES) file, inside the allowed roots. A missing or unreadable file is refused with ARGUMENT_OUT_OF_RANGE (field ies). Silently ignored by `place`, `list` and `remove`.'
            )
        ),
    ] = None,
    light_loss_factor: Annotated[
        StrictFloat | None,
        Field(
            description=(
                '`import` only, optional: maintenance/light-loss factor in (0, 1], applied as `ies2rad -m` and recorded as assumed. Outside (0, 1] is refused. Omitted means no factor is applied. Silently ignored by `place`, `list` and `remove`.'
            )
        ),
    ] = None,
    colour: Annotated[
        list[StrictFloat] | None,
        Field(
            description=(
                "`import` only, optional: lamp colour as three finite numbers >= 0 (r, g, b) with non-zero photopic weight; scaled to photopic weight 1 before `ies2rad -c`. Omitted means ies2rad's default white, with a DEFAULTS_SUBSTITUTED warning. Silently ignored by `place`, `list` and `remove`."
            )
        ),
    ] = None,
    type_name: Annotated[
        str | None,
        Field(
            description=(
                '`place` hand and array routes only, and required there: the name of an imported luminaire type to copy (unknown is LUMINAIRE_NOT_FOUND). Refused with `geometry`, whose map rows pick the types; silently ignored by `import`, `list` and `remove`.'
            )
        ),
    ] = None,
    position: Annotated[
        list[StrictFloat] | None,
        Field(
            description=(
                "`place` hand and array routes: three finite numbers [x, y, z] in metres; required there (it selects the route, and for an array it is the first instance's position). Refused with `geometry`; silently ignored by `import`, `list` and `remove`."
            )
        ),
    ] = None,
    rot: Annotated[
        list[StrictFloat] | None,
        Field(
            description=(
                "`place` hand and array routes, optional: three finite angles in degrees, read as hcl_mcp's Y-up [rx, ry, rz] and emitted as `-rx rx -ry rz -rz -ry`: the first turns about X, the second about this repository's vertical Z with its sign reversed, the third about Y (R462; a non-zero value adds LUMINAIRE_ROT_REMAPPED, `warn` when more than one is non-zero). Omitted means no rotation. Refused with `geometry` (each map row carries its own rot); silently ignored by `import`, `list` and `remove`."
            )
        ),
    ] = None,
    array: Annotated[
        dict[str, Any] | None,
        Field(
            description=(
                '`place` array route, optional, only together with `position`: exactly the keys count_x, spacing_x, count_y, spacing_y; counts are whole numbers >= 1, spacings finite numbers > 0 in metres, along world X and Y. At most 400 instances per placement. Refused with `geometry`; silently ignored by `import`, `list` and `remove`.'
            )
        ),
    ] = None,
    geometry: Annotated[
        str | None,
        Field(
            description=(
                '`place` CAD route: the name of a geometry record imported from an .obj with a material map; one placement is made per luminaire row of the map. Use it instead of `position`, never with it (exactly one route). Silently ignored by `import`, `list` and `remove`.'
            )
        ),
    ] = None,
    row_types: Annotated[
        dict[str, str] | None,
        Field(
            description=(
                "`place` with `geometry` only, optional: {material-map row: imported type name}, to pick the type when a row's IES file matches several. Any other call that passes it is refused, not ignored."
            )
        ),
    ] = None,
    placement_names: Annotated[
        dict[str, str] | None,
        Field(
            description=(
                '`place` with `geometry` only, optional: {material-map row: placement name}, replacing the default `<geometry>__<row>`. Names must be legal and unique case-folded. Any other call that passes it is refused, not ignored.'
            )
        ),
    ] = None,
    confirm: Annotated[
        bool,
        Field(
            description=(
                '`remove` only: must be true, or the call is refused (field confirm); the check comes after `name` and `kind`. Default false. Silently ignored by `import`, `place` and `list`.'
            )
        ),
    ] = False,
    dry_run: Annotated[
        bool,
        Field(
            description=(
                'Default false. `import`, `place` and `remove` with true report what they would do (the parsed type, positions and argv, or the deletion set) and write nothing; `import` and hand/array `place` spawn nothing. `list` silently ignores it.'
            )
        ),
    ] = False,
) -> LuminaireResult:
    """Import LM-63 (IES) photometry and place luminaires in a project.

    Use it to bring luminaires into a project: `run_electric` and `run_ugr` read every placement,
    and `run_point_in_time`, `run_render` and `run_glare` read them with `luminaires=true`.

    `import` screens the file (luminous-geometry files, external tilt, types A/B are refused before any
    spawn), converts it with `ies2rad -dm -t default` and writes rad_mcp's photometric correction file
    `source.cal` to the project root. `place` puts copies of a type by hand (`position`, `rot`), as an
    array (`array`), or at the luminaire faces of an OBJ import (`geometry`). `list` reports records and
    changed files. `remove` deletes a record (`kind`, `confirm=true`).

    DESTRUCTIVE ACTIONS: `import` and `place` replace a record of the same name; `remove` deletes a type
    or placement and its files.
    """
    runtime = current()
    try:
        return _dispatch(runtime, action=action, project=project, name=name, kind=kind, ies=ies,
                         light_loss_factor=light_loss_factor, colour=colour, type_name=type_name, position=position,
                         rot=rot, array=array, geometry=geometry, row_types=row_types,
                         placement_names=placement_names, confirm=confirm, dry_run=dry_run)  # type: ignore[return-value]
    except (IesHeaderError, IesUnsupportedError, IesConversionError, PhotometryUnverifiedError, LayoutError,
            GeometryConversionError, ScratchOutsideRootsError, InputStillBuiltFrom, LuminaireRefusal,
            # Ruling R33, the same `refuse_inline_commands_in_text` scan `geometry_tool.py` and
            # `materials_tool.py` already catch this from: a placement's `!xform` line is text this
            # module wrote (never a scene file on disk), but `_placed_by_hand_or_array` still runs it
            # through the R33 guard before anything is staged, and Task 13's CAD route will call the
            # same scan -- so the handler belongs here, in the tool's whole handler chain, not wrapped
            # only around the hand/array route.
            SceneInlineCommandError,
            # Task 13: the CAD route re-parses a geometry record's OBJ and map. Their digests match
            # what `geometry(action="add")` accepted, so a parse refusal is not expected -- but a
            # `rad_core.obj`/`rad_core.material_map` error carries its own code and field, and an
            # unhandled one would escape as a bare `ToolError` with no code at all.
            GeometryError) as exc:
        return refuse(str(exc), [Advisory(code=exc.code, severity=exc.severity, field=getattr(exc, "field", None), message=str(exc))])  # type: ignore[return-value]
    except RadianceNotFound as exc:
        # R508: no install at all is `run_render`'s code (render_tool.py); a present install without
        # the `photometry` capability is `CAPABILITY_UNAVAILABLE`, raised in `_imported`.
        return refuse(str(exc), [Advisory(code="RADIANCE_NOT_FOUND", severity="error", message=str(exc))])  # type: ignore[return-value]
    except RadianceTimeoutError as exc:
        message = f"a Radiance step exceeded its {_ASSEMBLY_TIMEOUT_S:g} s bound: {exc}"
        return refuse(message, [Advisory(code="IES_CONVERSION_FAILED", severity="error", field="ies", message=message)])  # type: ignore[return-value]
    except RadianceError as exc:
        message = f"a Radiance step failed while importing the luminaire: {exc}"
        return refuse(message, [Advisory(code="IES_CONVERSION_FAILED", severity="error", field="ies", message=message)])  # type: ignore[return-value]
    except (ProjectError, PathOutsideRoot, RootsNotConfigured) as exc:
        return refuse_engine_error(exc)  # type: ignore[return-value]
    except NotARegularFileError as exc:
        return refuse(str(exc), [Advisory(code="PROJECT_PATH_NOT_A_REGULAR_FILE", severity="error", field="project", message=str(exc))])  # type: ignore[return-value]
    except OSError as exc:
        logger.warning("luminaire(action=%s) failed on the filesystem: %s", action, exc)
        return refuse(  # type: ignore[return-value]
            f"luminaire(action={action!r}) could not complete: {exc}. Nothing about the arguments is "
            "wrong -- the filesystem refused the operation."
        )


def _dispatch(runtime: Runtime, *, action: str, project: str | None, name: str | None, kind: str | None, ies: str | None,
              light_loss_factor: float | None, colour: list[float] | None, type_name: str | None,
              position: list[float] | None, rot: list[float] | None, array: dict[str, Any] | None,
              geometry: str | None, row_types: dict[str, str] | None, placement_names: dict[str, str] | None,
              confirm: bool, dry_run: bool) -> CallToolResult:
    missing = refuse_missing("luminaire", project=project)
    if missing is not None:
        return missing
    if action == "remove":
        missing = refuse_missing("luminaire", name=name, kind=kind)
        if missing is not None:
            return missing
    if action == "import":
        missing = refuse_missing("luminaire", name=name, ies=ies)
        if missing is not None:
            return missing
    # The edge for `name` (parked from Task 8): `rad_engine.ies.convert_ies` joins it into paths and
    # confines nothing, so it is checked here, before any read, write or spawn, on every action that
    # takes one.
    if action != "list" and name is not None and not is_legal_name(name):
        return _refuse_argument(name_refusal_reason(name, kind="luminaire", examples="erco, ll2722"), field="name")
    if action == "remove" and not confirm:
        return _refuse_argument("remove deletes a luminaire record and its files; pass confirm=true.", field="confirm")
    if action == "import":
        if light_loss_factor is not None and not (0.0 < light_loss_factor <= 1.0):
            return _refuse_argument(f"light_loss_factor must be in (0, 1], got {light_loss_factor!r}.", field="light_loss_factor")
        if colour is not None:
            try:
                normalised_colour(colour)
            except ValueError as exc:
                return _refuse_argument(str(exc), field="colour")
    # R721: the two row-keyed arguments are read only on the geometry route (`_placed_from_geometry`).
    for stray, value in (("row_types", row_types), ("placement_names", placement_names)):
        if value is not None and not (action == "place" and geometry is not None):
            return _refuse_argument(
                f"{stray} is read only by luminaire(action='place', geometry=...); this call would ignore it.",
                field=stray,
            )
    if action == "place":
        # `name` is required for hand and array, and refused (below) with `geometry`: substituting a
        # truthy placeholder when `geometry` is given keeps this one `refuse_missing` call honest for
        # both routes without a second name check.
        missing = refuse_missing("luminaire", name=name if geometry is None else "cad")
        if missing is not None:
            return missing
        routes = [route for route, given in (("position", position is not None), ("geometry", geometry is not None)) if given]
        if len(routes) != 1:
            return _refuse_argument("place takes exactly one of position (with an optional array) or geometry.",
                                    field="geometry" if geometry is not None else "position")
        if geometry is not None and (name is not None or type_name is not None or array is not None):
            field = "name" if name is not None else ("type_name" if type_name is not None else "array")
            return _refuse_argument(f"{field} is not used with geometry: the material map's rows name the types and placements.", field=field)
        if geometry is None:
            refusal = _refuse_hand_arguments(name=name, type_name=type_name, position=position, rot=rot, array=array)
            if refusal is not None:
                return refusal
        elif rot is not None:
            # R538: each material-map row carries its own `rot`; a top-level one would be silently ignored.
            return _refuse_argument("rot is not used with geometry: each luminaire row of the material map carries its own rot.", field="rot")
        if rot is not None and not _is_triple(rot):
            return _refuse_argument(f"rot must be three finite numbers, got {rot!r}.", field="rot")
    loaded = runtime.open(project)  # type: ignore[arg-type]
    if action == "import":
        return _imported(runtime, loaded, name=name, ies=ies, light_loss_factor=light_loss_factor, colour=colour, dry_run=dry_run)  # type: ignore[arg-type]
    if action == "list":
        return _listed(runtime, loaded)
    if action == "place" and geometry is None:
        return _placed_by_hand_or_array(runtime, loaded, name=name, type_name=type_name,  # type: ignore[arg-type]
                                        position=position, rot=rot, array=array, dry_run=dry_run)  # type: ignore[arg-type]
    if action == "place":
        return _placed_from_geometry(runtime, loaded, geometry=geometry, row_types=row_types,  # type: ignore[arg-type]
                                     placement_names=placement_names, dry_run=dry_run)
    return _removed(runtime, loaded, name=name, kind=kind, dry_run=dry_run)  # type: ignore[arg-type]


def _type_records(manifest: Any) -> list[InputRecord]:
    return [r for r in manifest.inputs if r.kind == "luminaire_type"]


def _placement_records(manifest: Any) -> list[InputRecord]:
    return [r for r in manifest.inputs if r.kind == "luminaire_placement"]


def _material_modifiers(loaded: LoadedProject) -> frozenset[str]:
    return frozenset(m.modifier for r in loaded.manifest.inputs if r.kind == "materials" for m in replay_materials(r))


def _refuse_collisions(modifiers: Sequence[str], project: LoadedProject) -> None:
    """A type's modifier that is already a project material is refused. Run twice on import (R529):
    on the pre-lock read, and on the manifest re-read inside the lock before anything is staged."""
    collisions = modifier_collisions(modifiers, _material_modifiers(project))
    if collisions:
        raise LuminaireRefusal(f"the type's modifiers {collisions} are already project materials; import it under another name.",
                               code="LUMINAIRE_MODIFIER_COLLISION", severity="error", field="name")


def _refuse_case_collisions(manifest: Any, *, kind: str, prefix: str, names: Sequence[str], field: str) -> None:
    """R543: luminaire names are unique case-folded within a kind. This filesystem is case-insensitive
    (APFS): `Desk.rad` and `desk.rad` are one file and one `.partial`, so a second record differing only
    by case would land its lamps in the first's file. A same-cased name is a replacement and passes.
    Run on the pre-lock read and again on the in-lock manifest (`_write_and_register`'s `recheck`)."""
    existing = [r.id.removeprefix(prefix) for r in manifest.inputs if r.kind == kind and r.id.startswith(prefix)]
    for name in names:
        clashes = sorted(e for e in existing if e != name and e.casefold() == name.casefold())
        if clashes:
            raise LuminaireRefusal(
                f"{name!r} differs only by case from the existing {kind} {clashes[0]!r}; on a case-insensitive "
                f"filesystem both name one file. Use {clashes[0]!r} to replace it, or choose another name.",
                code="ARGUMENT_OUT_OF_RANGE", severity="error", field=field)


def _holds_exactly(path: Path, data: bytes) -> bool:
    """Whether `path` is a regular file holding exactly `data` -- `lstat` first, so a symlink, FIFO or
    directory is never opened, and the size before any read, so a large file is never read."""
    try:
        st = os.lstat(path)
        if not stat.S_ISREG(st.st_mode) or st.st_size != len(data):
            return False
        return read_capped(path, len(data)) == data
    except OSError:  # `FileTooLargeError` and `NotARegularFileError` are both `OSError`s
        return False


def _refuse_unrecorded_destinations(manifest: Any, paths: ProjectPaths, writes: Mapping[Path, bytes], *, field: str) -> None:
    """L-90/R847: a write into `12_luminaires/types` or `12_luminaires/placements` that would land on a
    file no record names -- the same name, or a case variant found by listing the directory (one
    file on a case-insensitive filesystem; refused on any, as R543 compares names case-folded) -- is
    refused before anything is staged, **unless that file already holds exactly the bytes about to
    be written** (R847): that is this call's own output left unrecorded by an earlier identical call
    whose save failed (R528's "Repeat the same call"), and replacing it loses nothing. A recorded
    file is this server's own and is replaced as before. Run on the pre-lock read for the writes
    whose bytes are known then, and again inside the lock on every write (`_write_and_register`).
    The message says only that no record names the file -- never who wrote it."""
    recorded = {f.path for r in manifest.inputs for f in r.files}
    directories = {paths.luminaire_types, paths.luminaire_placements}
    listings: dict[Path, list[str]] = {}
    for target, data in writes.items():
        if target.parent not in directories:
            continue
        if target.parent not in listings:
            try:
                listings[target.parent] = sorted(os.listdir(target.parent))
            except FileNotFoundError:
                listings[target.parent] = []
        resolved = target.parent.resolve()
        for entry in listings[target.parent]:
            if entry.casefold() != target.name.casefold() or str(resolved / entry) in recorded:
                continue
            if _holds_exactly(target.parent / entry, data):
                continue
            raise LuminaireRefusal(
                f"{str(target.parent / entry)!r} exists, no record in this project names it, and it does not hold "
                f"the bytes this call would write to {target.name!r}, so it is not overwritten; nothing was "
                "written. Move that file out of the directory, or choose another name.",
                code="ARGUMENT_OUT_OF_RANGE", severity="error", field=field)


def _refuse_types_changed(project: LoadedProject, originals: Sequence[InputRecord], *, field: str) -> None:
    """R547: every type a placement was prepared against still exists, with the `registered_by` it was
    read with, on the manifest re-read inside the lock -- `remove` (Task 14) or a re-import between the
    pre-lock read and the lock would otherwise leave a placement naming a missing or different type."""
    current = {r.id: r for r in project.manifest.inputs if r.kind == "luminaire_type"}
    for original in originals:
        now = current.get(original.id)
        if now is None or now.registered_by != original.registered_by:
            raise LuminaireRefusal(
                f"luminaire type {original.id.removeprefix(TYPE_PREFIX)!r} was removed or re-imported while this "
                "placement was being prepared; nothing was written. Repeat the call.",
                code="LUMINAIRE_NOT_FOUND", severity="error", field=field)


def _support_digests(manifest: Any) -> tuple[str | None, set[str]]:
    """R503: the support record's `package_digest` and `previous_package_digests`, or `(None, set())`.

    The digest history lives on the record (spec R485), never in a module constant: it is what lets a
    later package recognise a `source.cal` an earlier one wrote."""
    record = next((r for r in manifest.inputs if r.id == SUPPORT_ID), None)
    if record is None or record.registered_by is None:
        return None, set()
    arguments = record.registered_by.arguments
    package = arguments.get("package_digest")
    earlier = arguments.get("previous_package_digests") or []
    return (package if isinstance(package, str) else None), {d for d in earlier if isinstance(d, str)}


def _known_digests(manifest: Any) -> set[str]:
    """Every digest rad_mcp recorded writing to the root `source.cal`, less the current package's own
    (R503) -- the `previous_digests` `source_cal_state` compares against."""
    package, earlier = _support_digests(manifest)
    return (({package} if package is not None else set()) | earlier) - {SOURCE_CAL_DIGEST}


def _register_support(project: LoadedProject, paths: ProjectPaths, *, tool: str, action: str | None) -> None:
    """The one spelling of the support record's registration (R485/R503/R551). `package_digest`
    becomes the current package's; every digest the record named before -- its `package_digest` and
    its history -- stays in `previous_package_digests`, less the current one, so a replaced earlier
    package's digest is kept. Called by `import` and by a lit run that rewrites an earlier package's
    root `source.cal` (`rad_mcp.lit_scene`); the caller holds the lock and saves.

    `tool`/`action` have no default (R567): `registered_by` names the call that actually wrote the
    file -- `luminaire`/`import`, or the lit tool (`run_point_in_time` or `run_render`, R585) whose run
    rewrote it or healed its record."""
    history = sorted(_known_digests(project.manifest))
    register_input(project, input_id=SUPPORT_ID, kind="luminaire_support", files=[paths.source_cal],
                   registered_by=ToolCall(tool=tool, action=action,
                                          arguments={"package_digest": SOURCE_CAL_DIGEST, "previous_package_digests": history}),
                   server_written=True, save=False)


def _partial(path: Path) -> Path:
    return path.with_name(path.name + ".partial")


def _refuse_non_regular(path: Path) -> None:
    """`safe_io.refuse_existing_non_regular_destination`, re-worded for this tool, which has no `out`.

    `lstat`, never `open`: a symlink (live or dangling), directory or FIFO at a staging or final path
    is refused before anything is created, so nothing is ever written *through* one."""
    try:
        refuse_existing_non_regular_destination(path)
    except NotARegularFileError as exc:
        raise NotARegularFileError(
            f"{str(path)!r} exists and is not a regular file, so this server cannot write it. "
            "Remove whatever occupies that path and re-run."
        ) from exc


def _write_and_register(runtime: Runtime, paths: ProjectPaths, writes: dict[Path, bytes],
                        register: Callable[[LoadedProject], list[Path]], *, field: str,
                        recheck: Callable[[LoadedProject], None] | None = None) -> LoadedProject:
    """Stage every file beside its target, rename all in, register, save once; then delete files the
    replaced records no longer name (spec §5.1 step 6). Returns the project whose manifest was saved.

    `recheck` runs against the manifest re-read inside the lock, before anything is staged (R529), and
    may add or drop entries in `writes` (import's root `source.cal`, code review M1): a
    refusal decided on the pre-lock read can be invalidated by a registration in between.

    Every target and staging path is checked -- containment of its resolved parent, and that nothing
    but a regular file sits there -- **before the first staging file is created**, so a refusal at
    the last target (the root `source.cal`, say) leaves nothing behind at the first. So is every
    target in the luminaire directories against an unrecorded file there (L-90), refused under
    `field` -- the argument that named the file.

    **Once any file is renamed in, every exception is `LUMINAIRE_STORED_FILES_REPLACED`** (R528): the
    files-landed-record-unsaved state holds whatever raised. **Once `save_manifest` returns, the call
    has committed** (R525): the obsolete-file unlink after it is best-effort -- logged, never raised --
    and the caller builds its result from the returned project, not from a second read after the lock."""
    with runtime.modifying(paths):
        current_project = open_project(paths.root, radiance_version=runtime.launcher.version)
        if recheck is not None:
            recheck(current_project)
        for directory in (paths.luminaire_types, paths.luminaire_placements):
            if not resolved_parent_within(directory / "probe", runtime.roots):
                raise PathOutsideRoot(f"{directory} resolves outside every allowed root; nothing was written.")
            directory.mkdir(parents=True, exist_ok=True)
        _refuse_unrecorded_destinations(current_project.manifest, paths, writes, field=field)
        for path in writes:
            if not resolved_parent_within(path, runtime.roots):
                raise PathOutsideRoot(f"{path} resolves outside every allowed root; nothing was written.")
            _refuse_non_regular(path)
            _refuse_non_regular(_partial(path))
        staged: list[tuple[Path, Path]] = []
        renamed: list[Path] = []
        try:
            for path, data in writes.items():
                partial = _partial(path)
                staged.append((partial, path))
                # R526/S1: `open_staging_file` unlinks a stale regular name (never the inode) and then
                # creates the file with O_EXCL, so a hardlink planted at the staging name -- before the
                # call or re-planted after the unlink -- is never written through.
                with open_staging_file(partial) as handle:
                    handle.write(data)
            for partial, final in staged:
                os.replace(partial, final)
                renamed.append(final)
            obsolete = register(current_project)
            save_manifest(paths, current_project.manifest)
        except Exception as exc:
            if renamed:
                raise LuminaireRefusal(
                    f"{', '.join(str(p) for p in renamed)} were written and the manifest could not be saved "
                    f"({type(exc).__name__}: {exc}); the files on disk are newer than the record. "
                    "Repeat the same call to record them.",
                    code="LUMINAIRE_STORED_FILES_REPLACED", severity="error", field="project",
                ) from exc
            raise
        finally:
            for partial, _final in staged:
                if _is_regular(partial):
                    partial.unlink()
        _unlink_obsolete(paths, obsolete)
    return current_project


def _unlink_obsolete(paths: ProjectPaths, obsolete: list[Path]) -> None:
    """Delete the replaced record's files the new record does not name -- after the save, so
    best-effort (R525): the manifest already names only the new files, and a file that cannot be
    removed is logged and left, never turned into a refusal of a committed call. Only a regular file
    directly in `12_luminaires/types` is ever unlinked (R522)."""
    for path in obsolete:
        _unlink_if_in(path, [paths.luminaire_types])


def _unlink_if_in(path: Path, allowed_dirs: Sequence[Path]) -> None:
    """The one containment-checked unlink after a committed save (L-92; R522/R525/R541): `path` is
    deleted only when its parent is one of `allowed_dirs` (each resolved) and it is lstat-regular.
    Anything else is left in place and logged, and so is an `OSError` from the unlink (L-86: the
    only failure `unlink` raises) -- the call has already committed, so none of these is a refusal."""
    if path.parent not in {d.resolve() for d in allowed_dirs}:
        logger.warning("luminaire call committed; %s is outside the directories this server writes luminaire "
                       "files under, so it was left in place.", path)
        return
    if not os.path.lexists(path):
        return
    if not _is_regular(path):
        logger.warning("luminaire call committed; %s is not a regular file, so it was left in place.", path)
        return
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        logger.warning("luminaire call committed; %s was not removed: %s", path, exc)


def _is_regular(path: Path) -> bool:
    try:
        return stat.S_ISREG(os.lstat(path).st_mode)
    except FileNotFoundError:
        return False


def _unreadable_record(record: InputRecord, exc: Exception) -> LuminaireRefusal:
    """N-111: a hand-edited type or placement record `list` cannot describe -- a key missing, a value
    of the wrong shape, no registration at all -- is `LUMINAIRE_FILES_CHANGED`, the code the lit
    tools give the same record, never an uncoded exception."""
    remedy = "re-import that type" if record.kind == "luminaire_type" else "re-place that placement"
    return LuminaireRefusal(
        f"luminaire record {record.id!r} carries no readable registration ({type(exc).__name__}: {exc}); {remedy}.",
        code="LUMINAIRE_FILES_CHANGED", severity="error", field=None)


#: What reading a hand-edited record can raise: `AttributeError` for a record with no `registered_by`,
#: `KeyError` for a missing key, `TypeError`/`IndexError` for a value of the wrong shape, and
#: `ValueError` (pydantic's `ValidationError` among them) for one the view model refuses.
_UNREADABLE_RECORD = (AttributeError, KeyError, TypeError, IndexError, ValueError)


def _recorded_name(record: InputRecord) -> str:
    """A record's recorded `name`, or `LUMINAIRE_FILES_CHANGED` for a hand-edited record with none
    (N-111's remainder, R844) -- never an uncoded `KeyError` out of `remove`'s scan of the placements
    that name a type."""
    try:
        return str(record.registered_by.arguments["name"])  # type: ignore[union-attr]
    except _UNREADABLE_RECORD as exc:
        raise _unreadable_record(record, exc) from exc


def _type_view(record: InputRecord) -> TypeView:
    try:
        return _type_view_of(record)
    except _UNREADABLE_RECORD as exc:
        raise _unreadable_record(record, exc) from exc


def _type_view_of(record: InputRecord) -> TypeView:
    a = record.registered_by.arguments  # type: ignore[union-attr]
    h = a["header"]
    footprint = None
    if a.get("emitter_min") is not None:
        footprint = FootprintView(**{axis: hi - lo for axis, lo, hi in zip("xyz", a["emitter_min"], a["emitter_max"])})
    return TypeView(
        name=a["name"], format=h["format"], photometric_type="C", tilt=h["tilt"], lamps=h["lamps"],
        lumens=h["lumens_per_lamp"], photometry="absolute" if h["lumens_per_lamp"] is None else "relative",
        candela_multiplier=h["candela_multiplier"], ballast_factor=h["ballast_factor"],
        field2=Field2View(value=h["field2"], meaning=h["field2_meaning"]), watts=h["watts"],
        shape_class=a.get("shape_class"), footprint_m=footprint,
        colour=ColourView(given=a["colour_given"], normalised=a["colour"], provenance="default" if a["colour_given"] is None else "assumed"),
        light_loss_factor=None if a["light_loss_factor"] is None else ValueProvenanceView(value=a["light_loss_factor"], provenance="assumed"),
        files=[f.path for f in record.files],
    )


def _import_advisories(header: Any, *, colour: list[float] | None, light_loss_factor: float | None) -> list[Advisory]:
    advisories = [Advisory(code="IES_MANUFACTURER_DATA", severity="info", field="ies", message=(
        "Photometry in an IES file may be the manufacturer's and may not match the specific luminaire sample; "
        "nothing in an LM-63 header proves independent lab data."))]
    if colour is None:
        advisories.append(Advisory(code="DEFAULTS_SUBSTITUTED", severity="warn", field="colour", message=(
            "colour was not given: the light colour is ies2rad's default white (provenance default), not a property of the lamp.")))
    for label, value in header.applied_factors:
        advisories.append(Advisory(code="IES_FACTOR_APPLIED", severity="warn", field="ies", message=(
            f"the header's {label} {value:g} is multiplied into the luminaire's output by ies2rad.")))
    if header.field2_meaning == "reserved" and header.field2 != 1.0:
        advisories.append(Advisory(code="IES_FIELD_IGNORED", severity="warn", field="ies", message=(
            f"header field 2 is {header.field2:g}; {header.format} reserves it, and ies2rad ignores it.")))
    if light_loss_factor is not None:
        also = f"; the header's {', '.join(label for label, _ in header.applied_factors)} are applied as well" if header.applied_factors else ""
        advisories.append(Advisory(code="LLF_APPLIED", severity="info", field="light_loss_factor", message=(
            f"light_loss_factor {light_loss_factor:g} is applied as ies2rad -m. It is for a physically justifiable "
            f"loss, not a factor to force agreement with a measurement{also}.")))
    return advisories


def _replaced_in_use_advisory(name: str, placements: Sequence[tuple[str, int, str]], *, dry_run: bool) -> Advisory:
    """R724: `LUMINAIRE_TYPE_REPLACED_IN_USE`, naming every placement of the re-imported type and its
    instance count. The dry run cannot know the new extent -- it runs no `ies2rad` -- so it says the
    overlap check is the real import's. A `geometry`-route placement's seat is judged by the real
    import, which refuses a re-import that would move it (R741); the dry run says so."""
    listing = ", ".join(f"{p!r} ({n} instance{'s' if n != 1 else ''})" for p, n, _ in placements)
    if dry_run:
        message = (f"luminaire type {name!r} is used by placements: {listing}. A real import replaces its photometry for "
                   "all of them; the new emitter extent is known only once ies2rad runs, so the overlap check runs on "
                   "the real import, not on this dry run.")
    else:
        message = (f"luminaire type {name!r} was replaced while placements use it: {listing}. Their emitter boxes were "
                   "refreshed to the new photometry; the scene's light changed with it, so a later lit result "
                   "may differ from one computed before this import.")
    geo = [p for p, _, route in placements if route == "geometry"]
    if geo and dry_run:
        message += (f" Placement(s) {', '.join(repr(p) for p in geo)} were seated from CAD geometry; a real "
                    "import refuses (LUMINAIRE_SEAT_WOULD_MOVE) any change it cannot show leaves their "
                    "seats in place, and then writes nothing.")
    return Advisory(code="LUMINAIRE_TYPE_REPLACED_IN_USE", severity="warn", field="name", message=message)


def _in_use_listing(pairs: Sequence[tuple[InputRecord, dict[str, Any]]]) -> list[tuple[str, int, str]]:
    return [(a["name"], len(a["positions"]), a["route"]) for _, a in pairs]


def _read_ies(ies: str, roots: tuple[Path, ...]) -> tuple[Path, bytes]:
    """The caller's LM-63 file, contained, read once under `IES_MAX_BYTES` (R504).

    Every way the read fails is refused on `ies`'s face, before anything is spawned -- the precedent
    is `geometry_tool._capped`. A symlink loop is caught at `contain` itself: `Path.resolve` raises
    `RuntimeError` for one on this Python (3.11), before `read_capped` is reached."""
    try:
        source = contain(ies, roots)
    except (PathOutsideRoot, RootsNotConfigured):
        raise
    except (RuntimeError, OSError) as exc:
        raise LuminaireRefusal(f"ies {ies!r} cannot be read as a regular file: {exc}.",
                               code="ARGUMENT_OUT_OF_RANGE", severity="error", field="ies") from None
    try:
        return source, read_capped(source, IES_MAX_BYTES)
    except FileTooLargeError as exc:
        raise LuminaireRefusal(f"ies {ies!r} is {exc.size} bytes, over the {IES_MAX_BYTES}-byte cap for an LM-63 file.",
                               code="IES_HEADER_INVALID", severity="error", field="ies") from None
    except FileNotFoundError:
        raise LuminaireRefusal(f"ies {ies!r} does not exist.",
                               code="ARGUMENT_OUT_OF_RANGE", severity="error", field="ies") from None
    except OSError as exc:
        raise LuminaireRefusal(f"ies {ies!r} cannot be read as a regular file: {exc}.",
                               code="ARGUMENT_OUT_OF_RANGE", severity="error", field="ies") from None


def _imported(runtime: Runtime, loaded: LoadedProject, *, name: str, ies: str, light_loss_factor: float | None,
              colour: list[float] | None, dry_run: bool) -> CallToolResult:
    paths = loaded.paths
    _refuse_case_collisions(loaded.manifest, kind="luminaire_type", prefix=TYPE_PREFIX, names=[name], field="name")
    source, data = _read_ies(ies, runtime.roots)
    # R847: only the `.ies`'s bytes are known before the conversion; the `.rad` and data files are
    # checked inside the lock, against the bytes `ies2rad` wrote.
    _refuse_unrecorded_destinations(loaded.manifest, paths, {paths.luminaire_type_file(name, ".ies"): data}, field="name")
    header = screen_ies(data)  # before the install is looked for, and so before any spawn
    normal = normalised_colour(colour) if colour is not None else None
    argv = ies2rad_argv(name=name, light_loss_factor=light_loss_factor, colour=normal)
    advisories = _import_advisories(header, colour=colour, light_loss_factor=light_loss_factor)
    arguments: dict[str, Any] = {
        "name": name, "ies": str(source), "light_loss_factor": light_loss_factor, "colour_given": colour,
        "colour": list(normal) if normal is not None else None, "header": dataclasses.asdict(header), "argv": argv,
        "shape_class": None, "emitter_min": None, "emitter_max": None, "modifiers": [],
    }
    if dry_run:
        users = [(a["name"], len(a["positions"]), a["route"])
                 for a in (r.registered_by.arguments for r in _placement_records(loaded.manifest) if r.registered_by is not None)
                 if a.get("type_name") == name]  # L-123: a record without it names no type
        if users:
            advisories.append(_replaced_in_use_advisory(name, users, dry_run=True))
        view = _type_view(InputRecord(id=TYPE_PREFIX + name, kind="luminaire_type", files=[],
                                      registered_by=ToolCall(tool="luminaire", action="import", arguments=arguments)))
        return emit(LuminaireImported(project=paths.name, root=str(paths.root), action="import", type=view, dry_run=True, argv=[argv], advisories=advisories))
    env = discover()
    if not env.capabilities().get("photometry"):
        raise LuminaireRefusal(
            "this Radiance install lacks the photometry capability (ies2rad and xform), so no IES file can be imported.",
            code="CAPABILITY_UNAVAILABLE", severity="error", field="ies",
        )
    with scratch_directory(paths.jobs, runtime.roots) as scratch:
        try:
            verify_photometry(env, scratch=scratch, timeout=_ASSEMBLY_TIMEOUT_S)
        except (RadianceError, IesConversionError) as exc:  # RadianceTimeoutError is a RadianceError
            # R527: the probe converts rad_mcp's own synthetic files, so its failure is the install's,
            # not the caller's IES -- never IES_CONVERSION_FAILED.
            raise LuminaireRefusal(
                f"this install's photometric probe did not run ({type(exc).__name__}: {exc}); rad_mcp's "
                "source.cal is unverified on it, so no IES file is imported.",
                code="LUMINAIRE_PHOTOMETRY_UNVERIFIED", severity="error", field="project",
            ) from exc
        converted = convert_ies(env, data, name=name, light_loss_factor=light_loss_factor, colour=normal, scratch=scratch, timeout=_ASSEMBLY_TIMEOUT_S)
    modifiers = converted.converted.modifiers
    _refuse_collisions(modifiers, loaded)
    arguments.update(shape_class=converted.converted.shape_class, emitter_min=list(converted.converted.emitter_min),
                     emitter_max=list(converted.converted.emitter_max), modifiers=list(converted.converted.modifiers))
    refreshed: list[tuple[InputRecord, dict[str, Any]]] = []
    writes: dict[Path, bytes] = {paths.luminaire_type_file(name, ".ies"): data, paths.luminaire_type_file(name, ".rad"): converted.rad_bytes}
    writes.update({paths.root / relative: body for relative, body in converted.data_files.items()})
    # R503: a root `source.cal` that is anything but the current package's bytes -- missing, an
    # earlier rad_mcp package's, or foreign -- is staged for rewrite; `_write_and_register` refuses a
    # non-regular one (a symlink, a directory) before anything lands. **Decided in `recheck`, under
    # the lock, against the manifest and file re-read there** (code review M1, whole-branch fix wave):
    # decided here, before the lock, a concurrent `remove` of the last type deleted `source.cal` and
    # the support record in between, and this import then registered a support record naming a file
    # it had not written -- `LUMINAIRE_STORED_FILES_REPLACED`, with the type files landed unrecorded.

    def register(project: LoadedProject) -> list[Path]:
        previous = next((r for r in project.manifest.inputs if r.id == TYPE_PREFIX + name), None)
        new_files = [p for p in writes if p != paths.source_cal]
        register_input(project, input_id=TYPE_PREFIX + name, kind="luminaire_type", files=new_files,
                       registered_by=ToolCall(tool="luminaire", action="import", arguments=arguments),
                       sources=[FileRef(path=str(source), digest=bytes_digest(data))], server_written=True, save=False)
        for record, new_arguments in refreshed:
            record.registered_by = record.registered_by.model_copy(update={"arguments": new_arguments})  # type: ignore[union-attr]
        _register_support(project, paths, tool="luminaire", action="import")
        kept = {str(Path(p).resolve()) for p in new_files}
        return [Path(f.path) for f in (previous.files if previous else []) if f.path not in kept]

    def recheck(project: LoadedProject) -> None:
        _refuse_case_collisions(project.manifest, kind="luminaire_type", prefix=TYPE_PREFIX, names=[name], field="name")
        _refuse_collisions(modifiers, project)
        refreshed[:] = _refreshed_placements(project.manifest, type_name=name, emitter_min=arguments["emitter_min"],
                                             emitter_max=arguments["emitter_max"], shape_class=arguments["shape_class"])
        if refreshed:
            try:
                _refuse_emitter_overlap(project.manifest, [], field="ies",
                                        standing_in=(name, arguments["emitter_min"], arguments["emitter_max"]))
            except LayoutError as exc:
                raise LuminaireRefusal(
                    f"importing {source} as luminaire type {name!r} replaces the emitter of placement(s) "
                    f"{', '.join(repr(a['name']) for _, a in refreshed)}, and the project-wide overlap check, run with the new "
                    f"emitter standing in, found: {exc} Nothing was "
                    "written and the previous type stays in force; to compare the two fittings, import this file "
                    "under a new type name.",
                    code="LUMINAIRE_EMITTER_BOXES_INTERSECT", severity="error", field="ies",
                ) from exc
        if refreshed:
            _refuse_moved_seats(project.manifest, refreshed, name=name, source=source,
                                new_shape=arguments["shape_class"], new_min=arguments["emitter_min"],
                                new_max=arguments["emitter_max"])
        # M1: `_write_and_register` calls this before it checks or stages any path in `writes`.
        if source_cal_state(paths.source_cal, previous_digests=_known_digests(project.manifest)) != "current":
            writes[paths.source_cal] = SOURCE_CAL_BYTES
        else:
            writes.pop(paths.source_cal, None)

    saved = _write_and_register(runtime, paths, writes, register, field="name", recheck=recheck)
    record = next(r for r in saved.manifest.inputs if r.id == TYPE_PREFIX + name)
    if refreshed:
        advisories.append(_replaced_in_use_advisory(name, _in_use_listing(refreshed), dry_run=False))
    result = LuminaireImported(project=paths.name, root=str(paths.root), action="import", type=_type_view(record), advisories=advisories)
    return _emit_with_links(result, [f.path for f in record.files])


def _placement_view(record: InputRecord) -> PlacementView:
    try:
        return _placement_view_of(record)
    except _UNREADABLE_RECORD as exc:
        raise _unreadable_record(record, exc) from exc


def _placement_view_of(record: InputRecord) -> PlacementView:
    a = record.registered_by.arguments  # type: ignore[union-attr]
    return PlacementView(
        name=a["name"], type_name=a["type_name"], route=a["route"], instances=len(a["positions"]),
        positions=[list(p) for p in a["positions"]], rot=a["rot"], files=[f.path for f in record.files],
        sources=[SourceRefView(path=s.path, digest=s.digest) for s in record.sources] or None,
    )


def _rot_advisories(rot: list[float] | None, *, where: str) -> list[Advisory]:
    """`LUMINAIRE_ROT_REMAPPED`: `info` for no more than one non-zero axis, `warn` for a compound
    rotation, whose R462 remap the docstring on `rad_core.luminaire_layout` records the caveat for.
    No advisory at all for `rot=None` or an all-zero triple, which emits no rotation flags."""
    if rot is None or not any(rot):
        return []
    flags = " ".join(rotation_flags(tuple(rot)))  # type: ignore[arg-type]
    if is_compound(tuple(rot)):  # type: ignore[arg-type]
        return [Advisory(code="LUMINAIRE_ROT_REMAPPED", severity="warn", field="rot", message=(
            f"{where}: rot {rot} is read as hcl_mcp's Y-up Euler triple and emitted as {flags}. With more than one "
            "non-zero angle that remap differs from the true rotation, and single-axis Y and Z turns are mirrored "
            "against this repository's up-axis convention (R462)."))]
    return [Advisory(code="LUMINAIRE_ROT_REMAPPED", severity="info", field="rot", message=(
        f"{where}: rot {rot} is read as hcl_mcp's Y-up Euler triple and emitted as {flags} (R462)."))]


def _find_type(manifest: Any, type_name: str, *, field: str) -> InputRecord:
    record = next((r for r in _type_records(manifest) if r.id == TYPE_PREFIX + type_name), None)
    if record is None:
        raise LuminaireRefusal(f"no luminaire type {type_name!r} is imported in this project.",
                               code="LUMINAIRE_NOT_FOUND", field=field, severity="error")
    return _readable_type(record)


def _readable_type(record: InputRecord) -> InputRecord:
    """`record`, once the keys a placement reads from it are readable: `name`, `shape_class`, and
    `emitter_min`/`emitter_max` as three numbers each. A hand-edited type missing one, or holding one
    of the wrong shape, is `LUMINAIRE_FILES_CHANGED` (N-111's remainder, R844), never an uncoded
    exception out of `place` or the emitter-overlap check."""
    try:
        a = record.registered_by.arguments  # type: ignore[union-attr]
        str(a["name"])
        a["shape_class"]
        for key in ("emitter_min", "emitter_max"):
            if len(a[key]) != 3:
                raise ValueError(f"{key} {a[key]!r} is not three numbers")
            [float(v) for v in a[key]]
    except _UNREADABLE_RECORD as exc:
        raise _unreadable_record(record, exc) from exc
    return record


def _placement_arguments(*, name: str, type_record: InputRecord, route: str, rot: list[float] | None,
                         array: dict[str, Any] | None, positions: list[tuple[float, float, float]],
                         geometry: str | None = None, seats: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """A placement's recorded arguments. `rotated_min`/`rotated_max` are the type's emitter box
    under `rot`, a snapshot recorded here and refreshed by a re-import of the type (R724). No
    computation reads it: the lit readers compute the box from the type record, as the overlap
    check does (R675), so a manifest written before R724 or edited by hand cannot carry a stale box
    into a result (R726). `seats` is how each `geometry`-route instance was seated, one per
    position, recorded for a re-import to judge (R741, R742); `None` on the hand and array routes."""
    a = type_record.registered_by.arguments  # type: ignore[union-attr]
    low, high = rotated_extent(tuple(a["emitter_min"]), tuple(a["emitter_max"]), tuple(rot) if rot is not None else None)  # type: ignore[arg-type]
    return {"name": name, "type_name": a["name"], "route": route, "rot": rot, "array": array, "geometry": geometry,
            "positions": [list(p) for p in positions], "rotated_min": list(low), "rotated_max": list(high),
            "shape_class": a["shape_class"], "seats": seats}


def _instance_boxes(manifest: Any, arguments: dict[str, Any], *,
                    standing_in: tuple[str, Sequence[float], Sequence[float]] | None = None) -> list[EmitterInstance]:
    """One placement's instances as placed emitters: the **current** type record's unrotated
    extent, the placement's own `rot`, and each recorded position.

    **The type is read as it stands now, not as it stood at placement time** (R675). The placement
    record carries a `rotated_min`/`rotated_max` snapshot, and a re-import of the type refreshes that
    snapshot in the same save (R724); reading the type record keeps this check correct on any
    manifest written before R724, or edited by hand. Judging on the snapshot would mix
    current geometry with stale and make the published claim to cover "any two instances in the
    project" false after any re-import.

    **The extent is carried unrotated, with `rot` beside it**, because the axis-aligned bounding
    box of a rotated emitter is not the emitter; see `EmitterInstance`.

    **`standing_in` is a re-import's extent for one type name** (R724): `(type_name, emitter_min,
    emitter_max)`. A placement of that type is judged on it instead of on the type record, which
    the import has not yet replaced, so the re-import's check is this one and not a second
    implementation.
    """
    if standing_in is not None and arguments["type_name"] == standing_in[0]:
        emitter_min, emitter_max = tuple(standing_in[1]), tuple(standing_in[2])
    else:
        type_record = _find_type(manifest, arguments["type_name"], field="type_name")
        a = type_record.registered_by.arguments  # type: ignore[union-attr]
        emitter_min, emitter_max = tuple(a["emitter_min"]), tuple(a["emitter_max"])
    rot = arguments["rot"]
    return [
        EmitterInstance(arguments["name"], index, tuple(position),
                        tuple(rot) if rot is not None else None, emitter_min, emitter_max)
        for index, position in enumerate(arguments["positions"])
    ]


def _refuse_emitter_overlap(manifest: Any, new: Sequence[dict[str, Any]], *, field: str,
                            standing_in: tuple[str, Sequence[float], Sequence[float]] | None = None) -> None:
    """R673/R675: refuse when any two instances in the project would have intersecting emitters --
    the new placement's against each other, and against every instance already placed.

    **Project-wide, not per call.** `rad_core.ugr`'s `E_direct` sums over source rows and does not
    care which `place` call produced a row, so a check scoped to one call would leave R672's
    measured defect reachable by making two calls instead of one array.

    **A placement being replaced is not compared against its own previous self.** `place` replaces
    a record of the same name, so the old instances are about to stop existing; comparing against
    them would refuse every re-place of an unchanged layout.

    The new instances are listed first so the refusal names the placement the caller just asked
    for before the one already on disk.

    `standing_in` is passed through to `_instance_boxes`; with `new` empty, the check covers every
    placement in the project (a re-import, R724).
    """
    # Fix round 3 of R844: a record about to be replaced is skipped by its id, before any of its keys
    # is read, so a damaged placement can be re-placed under its own name -- the remedy every
    # refusal below names ("re-place that placement").
    replacing = {PLACEMENT_PREFIX + arguments["name"] for arguments in new}
    boxes = [box for arguments in new for box in _instance_boxes(manifest, arguments, standing_in=standing_in)]
    for record in _placement_records(manifest):
        if record.registered_by is None or record.id in replacing:
            continue
        recorded = record.registered_by.arguments
        # N-111's remainder (R844): a hand-edited placement already in the project is read through
        # one guard, so a missing or malformed key is `LUMINAIRE_FILES_CHANGED` naming it. The shapes
        # are checked before any use (fix round 2): `refuse_intersecting_emitters` below runs outside
        # this guard and must only ever see a checked `rot` and checked positions.
        try:
            _check_placement_shapes(recorded)
            if (standing_in is None or recorded["type_name"] != standing_in[0]) and not any(
                    r.id == TYPE_PREFIX + recorded["type_name"] for r in _type_records(manifest)):
                raise LuminaireRefusal(
                    f"luminaire placement record {record.id!r} names type {recorded['type_name']!r}, which has no "
                    "record in this project; re-import that type or re-place the placement.",
                    code="LUMINAIRE_FILES_CHANGED", field=None, severity="error")
            boxes.extend(_instance_boxes(manifest, recorded, standing_in=standing_in))
        except LuminaireRefusal:
            # Already coded, about the record it names: the missing-type refusal just above, or
            # `_readable_type`'s refusal of the placement's type (reached through `_find_type`, whose
            # own `LUMINAIRE_NOT_FOUND` cannot fire here: the type's existence is checked first, or
            # `standing_in` replaces the lookup). `LuminaireRefusal` subclasses `ValueError`, so
            # without this clause the guard below re-blamed this placement for it.
            raise
        except _UNREADABLE_RECORD as exc:
            raise _unreadable_record(record, exc) from exc
    refuse_intersecting_emitters(boxes, field=field)


def _is_point(value: Any) -> bool:
    """Three finite numbers, as a list or a tuple (a record read back from JSON holds lists; one
    built in this process may hold tuples)."""
    return isinstance(value, (list, tuple)) and len(value) == 3 and all(_is_number(v) for v in value)


def _check_placement_shapes(arguments: dict[str, Any]) -> None:
    """Fix round 2 of R844: a recorded placement's `name` and `type_name` are strings, its `rot` is
    null or three finite numbers, and its `positions` a list of three finite numbers each. A missing
    key raises `KeyError`; a wrong shape raises `ValueError` naming the key -- both `_UNREADABLE_RECORD`,
    so the caller refuses `LUMINAIRE_FILES_CHANGED` naming the record."""
    for key in ("name", "type_name"):
        if not isinstance(arguments[key], str):
            raise ValueError(f"{key!r} is {arguments[key]!r}, not a string")
    rot = arguments["rot"]
    if rot is not None and not _is_point(rot):
        raise ValueError(f"'rot' is {rot!r}, not null or three finite numbers")
    positions = arguments["positions"]
    if not isinstance(positions, (list, tuple)) or not all(_is_point(p) for p in positions):
        raise ValueError(f"'positions' is {positions!r}, not a list of three finite numbers each")


def _refreshed_placements(manifest: Any, *, type_name: str, emitter_min: Sequence[float],
                          emitter_max: Sequence[float], shape_class: str | None) -> list[tuple[InputRecord, dict[str, Any]]]:
    """R724: every placement of `type_name`, with the arguments a re-import of that type gives it.

    The emitter box is the new extent under the placement's **own** `rot`, through
    `rotated_extent`, the function `_placement_arguments` uses at placement time, so a refreshed
    `hand` or `array` record equals the one a fresh `place` of the new type would write. Only
    `rotated_min`, `rotated_max` and `shape_class` change; `positions` and `seats` are kept: a
    `geometry`-route placement's refreshed record is written only when `_refuse_moved_seats`, run
    on this function's result, found that no seat moves (R741). The recorded arguments are copied,
    never mutated: `recheck` computes this and `register` writes it.

    **The refreshed snapshot is a record, not an input** (R726): the lit readers
    (`ugr_tool._placed`, `lit_scene.near_field_advisory`) compute each box from the type record, as
    the overlap check does (R675), so a manifest written before R724 or edited by hand cannot carry
    a stale box into a result.
    """
    out: list[tuple[InputRecord, dict[str, Any]]] = []
    for record in _placement_records(manifest):
        if record.registered_by is None:
            continue
        a = record.registered_by.arguments
        if a.get("type_name") != type_name:
            continue
        try:  # N-111's remainder (R844): a hand-edited placement's `rot`, read through one guard
            _check_placement_shapes(a)
            rot = a["rot"]
            low, high = rotated_extent(tuple(emitter_min), tuple(emitter_max), tuple(rot) if rot is not None else None)  # type: ignore[arg-type]
        except _UNREADABLE_RECORD as exc:
            raise _unreadable_record(record, exc) from exc
        out.append((record, {**a, "rotated_min": list(low), "rotated_max": list(high), "shape_class": shape_class}))
    return out


def _emitter_of(record: InputRecord | None) -> tuple[str, tuple[float, ...], tuple[float, ...]] | None:
    """A type record's `(shape_class, emitter_min, emitter_max)`, or `None` when the record is
    missing or does not hold a string shape class and three finite numbers for each extent -- read
    as "cannot judge", never raised."""
    if record is None or record.registered_by is None:
        return None
    a = record.registered_by.arguments
    low, high, shape = a.get("emitter_min"), a.get("emitter_max"), a.get("shape_class")
    if not isinstance(shape, str) or not (_is_triple(low) and _is_triple(high)):
        return None
    return shape, tuple(low), tuple(high)


def _seat_phrase(change: SeatChange | None) -> str:
    """One instance's reason, as the refusal prints it. `None` is a previous type this server
    cannot read. Offsets are `repr` of each float, so no display precision is chosen."""
    if change is None:
        return "its seat cannot be judged: the previous type's emitter is not readable from the record"
    if change.reason == "no legal seat":
        return ("no legal seat: a flat drawn face cannot seat a type with luminous height "
                "(what place refuses as LUMINAIRE_HEIGHT_NEEDS_SOLID)")
    if change.reason == "not recorded":
        return ("its seat cannot be judged: the placement records no seat (it was placed before seats "
                "were recorded, or its record was edited) and the type changes between flat and having "
                "luminous height, which changes the seat rule on a solid island")
    x, y, z = (repr(v + 0.0) for v in change.offset)  # type: ignore[union-attr]
    return f"its seat moves by ({x}, {y}, {z}) m"


def _refuse_moved_seats(manifest: Any, refreshed: Sequence[tuple[InputRecord, dict[str, Any]]], *,
                        name: str, source: Path, new_shape: str, new_min: Sequence[float],
                        new_max: Sequence[float]) -> None:
    """R741: refuse a re-import that would move any `geometry`-route seat of type `name`.

    Each instance is judged by `rad_core.luminaire_layout.seat_change` from its recorded seat
    (R742), exactly; where the previous type's emitter cannot be read from its record, no instance
    can be judged and each is named as such. Hand and array placements are the caller's positions
    and are not judged.
    Runs in `recheck`, inside the lock, after the R724 overlap re-check and before anything is
    staged, so a refusal writes nothing and the previous type stays in force."""
    old = _emitter_of(next((r for r in _type_records(manifest) if r.id == TYPE_PREFIX + name), None))
    groups: dict[tuple[str, str], list[int]] = {}
    totals: dict[str, int] = {}
    for _record, a in refreshed:
        if a.get("route") != "geometry":
            continue
        positions, seats, rot = a["positions"], a.get("seats"), a.get("rot")
        totals[a["name"]] = len(positions)
        if not isinstance(seats, list) or len(seats) != len(positions):
            seats = [None] * len(positions)
        for index, seat in enumerate(seats):
            if old is None:
                phrase = _seat_phrase(None)
            else:
                change = seat_change(seat, rot=tuple(rot) if _is_triple(rot) else None,
                                     old_shape=old[0], old_min=old[1], old_max=old[2],  # type: ignore[arg-type]
                                     new_shape=new_shape, new_min=tuple(new_min), new_max=tuple(new_max))  # type: ignore[arg-type]
                if change is None:
                    continue
                phrase = _seat_phrase(change)
            groups.setdefault((a["name"], phrase), []).append(index)
    if not groups:
        return
    parts = []
    for (placement, phrase), indices in groups.items():
        total = totals[placement]
        if total > 1 and len(indices) == total:
            which = f"all {total} instances"
        else:
            which = ("instance " if len(indices) == 1 else "instances ") + ", ".join(map(str, indices))
        parts.append(f"{placement!r} {which}: {phrase}")
    raise LuminaireRefusal(
        f"importing {source} as luminaire type {name!r} would move, or cannot show that it keeps, the "
        f"seat of placements seated from CAD geometry: {'; '.join(parts)}. Instances are counted from 0, "
        "in the order of the placement's positions. Nothing was written, and the "
        "previous type stays in force. To go on: remove these placements, import the file, point the "
        "material map's luminaire row at it, re-import the geometry and place from it again (a row that "
        "still names the previous file matches any other type imported from that file; failing that it "
        "follows the type of the geometry's remaining placements made from that file, R748; and it is "
        "LUMINAIRE_IES_UNMATCHED when neither exists); or import the file under a new type name, which leaves these "
        "placements as they are; or remove these placements, import the file (under this name or a new "
        "one), and place the fittings by hand with position and rot.",
        code="LUMINAIRE_SEAT_WOULD_MOVE", severity="error", field="ies",
    )


def _placed_by_hand_or_array(runtime: Runtime, loaded: LoadedProject, *, name: str, type_name: str, position: list[float],
                             rot: list[float] | None, array: dict[str, Any] | None, dry_run: bool) -> CallToolResult:
    paths = loaded.paths
    type_record = _find_type(loaded.manifest, type_name, field="type_name")
    _refuse_case_collisions(loaded.manifest, kind="luminaire_placement", prefix=PLACEMENT_PREFIX, names=[name], field="name")
    type_rad = f"{TYPES_PREFIX}/{type_name}.rad"
    rot_t = tuple(rot) if rot is not None else None
    origin = tuple(position)
    if array is None:
        positions = [origin]
        line, route = hand_line(type_rad, origin, rot_t), "hand"  # type: ignore[arg-type]
    else:
        positions = instance_positions(origin, **array)  # type: ignore[arg-type]
        line, route = array_line(type_rad, origin, rot_t, **array), "array"  # type: ignore[arg-type]
    target = paths.luminaire_placement_file(name)
    text = f"# luminaire placement {name} (rad_mcp)\n{line}\n"
    _refuse_unrecorded_destinations(loaded.manifest, paths, {target: text.encode("utf-8")}, field="name")
    refuse_inline_commands_in_text(text, label=target, cwd=paths.root)
    arguments = _placement_arguments(name=name, type_record=type_record, route=route, rot=rot, array=array, positions=positions)  # type: ignore[arg-type]
    advisories = _rot_advisories(rot, where=f"placement {name!r}")
    _refuse_emitter_overlap(loaded.manifest, [arguments], field="array" if array is not None else "position")
    if dry_run:
        view = PlacementView(name=name, type_name=type_name, route=route, instances=len(positions), positions=arguments["positions"], rot=rot, files=[])  # type: ignore[arg-type]
        return emit(LuminairePlaced(project=paths.name, root=str(paths.root), action="place", placements=[view], dry_run=True, argv=[["xform", *line.split()[1:]]], advisories=advisories or None))

    def register(project: LoadedProject) -> list[Path]:
        register_input(project, input_id=PLACEMENT_PREFIX + name, kind="luminaire_placement", files=[target],
                       registered_by=ToolCall(tool="luminaire", action="place", arguments=arguments), server_written=True, save=False)
        return []

    # R530: `_write_and_register` is the call that commits (R525) -- once `save_manifest` inside it
    # returns, the result is built from the `LoadedProject` it returns, never from a second
    # `open_project` after the lock has been released.
    def recheck(project: LoadedProject) -> None:
        _refuse_case_collisions(project.manifest, kind="luminaire_placement", prefix=PLACEMENT_PREFIX, names=[name], field="name")
        _refuse_types_changed(project, [type_record], field="type_name")
        _refuse_emitter_overlap(project.manifest, [arguments], field="array" if array is not None else "position")

    saved = _write_and_register(runtime, paths, {target: text.encode("utf-8")}, register, field="name", recheck=recheck)
    record = next(r for r in saved.manifest.inputs if r.id == PLACEMENT_PREFIX + name)
    result = LuminairePlaced(project=paths.name, root=str(paths.root), action="place", placements=[_placement_view(record)], advisories=advisories or None)
    return _emit_with_links(result, [f.path for f in record.files])


def _read_recorded_source(ref: FileRef, cap: int, roots: tuple[Path, ...]) -> bytes:
    """One read of a geometry record's recorded source, refused at error when it no longer holds its
    bytes (spec §6 step 2, R464): missing, unreadable, too large, out of the roots, or changed."""
    try:
        path = contain(ref.path, roots)
        data = read_capped(path, cap)
    except (PathOutsideRoot, RootsNotConfigured):
        reason = "no longer inside the allowed roots"
    except FileTooLargeError:
        reason = f"larger than its {cap}-byte cap"
    except FileNotFoundError:
        reason = "missing"
    except (RuntimeError, OSError):  # `contain`'s symlink loop (R518), a directory, a FIFO, no permission
        reason = "unreadable (not a regular file, a symlink loop, or not readable by this server)"
    else:
        if bytes_digest(data) == ref.digest:
            return data
        reason = "changed"
    raise LuminaireRefusal(f"the geometry record's source {ref.path} is {reason}; re-import the geometry first.",
                           code="GEOMETRY_SOURCE_CHANGED", severity="error", field="geometry")


def _read_row_ies(row: str, value: Any, map_path: str, roots: tuple[Path, ...]) -> tuple[Path, bytes]:
    """A luminaire row's `ies`: absolute, or relative to the map's directory, contained, read once
    under `IES_MAX_BYTES` (spec §6 step 3). R536: every way the read fails is
    `LUMINAIRE_IES_UNMATCHED` naming which; only containment is re-raised, as `PATH_OUTSIDE_ROOT`."""
    if not isinstance(value, str) or not value:
        raise LuminaireRefusal(f"row {row!r} names no ies file.",
                               code="LUMINAIRE_IES_UNMATCHED", severity="error", field="geometry")
    candidate = Path(value) if Path(value).is_absolute() else Path(map_path).parent / value
    try:
        path = contain(candidate, roots)
    except (PathOutsideRoot, RootsNotConfigured):
        raise
    except (RuntimeError, OSError) as exc:
        raise LuminaireRefusal(f"row {row!r}: its ies file {candidate} is unreadable ({exc}).",
                               code="LUMINAIRE_IES_UNMATCHED", severity="error", field="geometry") from None
    try:
        return path, read_capped(path, IES_MAX_BYTES)
    except FileTooLargeError as exc:
        raise LuminaireRefusal(f"row {row!r}: its ies file {path} is too large ({exc.size} bytes, over the {IES_MAX_BYTES}-byte cap).",
                               code="LUMINAIRE_IES_UNMATCHED", severity="error", field="geometry") from None
    except FileNotFoundError:
        raise LuminaireRefusal(f"row {row!r}: its ies file {path} is missing.",
                               code="LUMINAIRE_IES_UNMATCHED", severity="error", field="geometry") from None
    except OSError as exc:
        raise LuminaireRefusal(f"row {row!r}: its ies file {path} is unreadable (not a regular file, or not readable by this server: {exc}).",
                               code="LUMINAIRE_IES_UNMATCHED", severity="error", field="geometry") from None


def _refuse_row_keyed(value: Any, *, field: str, rows: frozenset[str]) -> dict[str, str]:
    """R538: `row_types` and `placement_names` are str -> str maps keyed by luminaire rows of the map."""
    if value is None:
        return {}
    if not isinstance(value, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in value.items()):
        raise LuminaireRefusal(f"{field} must map material-map row names to strings, got {value!r}.",
                               code="ARGUMENT_OUT_OF_RANGE", severity="error", field=field)
    unknown = sorted(set(value) - rows)
    if unknown:
        raise LuminaireRefusal(f"{field} names {unknown}, which are not luminaire rows of the material map; its luminaire rows are {sorted(rows)}.",
                               code="ARGUMENT_OUT_OF_RANGE", severity="error", field=field)
    return value


@dataclasses.dataclass(frozen=True)
class _CadRow:
    row: str
    placement: str
    type_record: InputRecord
    ies: FileRef
    rot: list[float] | None
    faces: list[tuple[str | None, list[tuple[float, float, float]]]]
    markers: list[tuple[float, float, float]]


def _cad_positions(row: _CadRow, faces: list[tuple[str | None, list[tuple[float, float, float]]]],
                   markers: list[tuple[float, float, float]],
                   advisories: list[Advisory]) -> tuple[list[tuple[float, float, float]], list[dict[str, Any]]]:
    """One row's instance positions from its converted (metres, Z-up) vertices: `p` markers when any
    exist, else welded islands through the R475 guard and R476's shape-class seat. Returns the
    positions and each instance's seat (R742)."""
    a = row.type_record.registered_by.arguments  # type: ignore[union-attr]
    emitter_min, emitter_max = tuple(a["emitter_min"]), tuple(a["emitter_max"])
    rot_t = tuple(row.rot) if row.rot is not None else None
    if markers:
        centre = rotate(rotation_matrix(rot_t), tuple((lo + hi) / 2 for lo, hi in zip(emitter_min, emitter_max)))  # type: ignore[arg-type]
        return [tuple(m - c for m, c in zip(marker, centre)) for marker in markers], [marker_seat() for _ in markers]  # type: ignore[misc]
    found = islands(faces)
    if len(found) > PLACEMENT_MAX_INSTANCES:
        raise LuminaireRefusal(
            f"luminaire row {row.row!r} has {len(found)} islands, over the {PLACEMENT_MAX_INSTANCES}-instance bound "
            "on one placement (R489). Split the row across materials.",
            code="ARGUMENT_OUT_OF_RANGE", severity="error", field="geometry")
    for finding in guard_islands(found, row=row.row, emitter_min=emitter_min, emitter_max=emitter_max, rot=rot_t):  # type: ignore[arg-type]
        # R534: the guard raises `LayoutError` for its errors and returns only this one finding, so
        # the advisory is spelled as literals the register scan can read; any other code is a defect.
        if finding.code != "LUMINAIRE_ISLAND_EXTENT_UNCHECKED":
            raise TypeError(f"guard_islands returned an unexpected finding {finding.code!r}")
        advisories.append(Advisory(code="LUMINAIRE_ISLAND_EXTENT_UNCHECKED", severity="info", field="geometry", message=finding.message))
    positions: list[tuple[float, float, float]] = []
    seats: list[dict[str, Any]] = []
    assumed_bottom = False
    for island in found:
        position, note = cad_position(island, shape_class=a["shape_class"], emitter_min=emitter_min,  # type: ignore[arg-type]
                                      emitter_max=emitter_max, rot=rot_t)  # type: ignore[arg-type]
        assumed_bottom = assumed_bottom or note == "LUMINAIRE_OPENING_ASSUMED_BOTTOM"
        positions.append(position)
        seats.append(island_seat(island))
    if assumed_bottom:
        advisories.append(Advisory(code="LUMINAIRE_OPENING_ASSUMED_BOTTOM", severity="info", field="geometry", message=(
            f"row {row.row!r}: the fitting is drawn as a solid, so its luminous opening is assumed to be its lowest point.")))
    return positions, seats


def _followed_types(manifest: Any, types: list[InputRecord], *, base: str, digest: str) -> list[InputRecord]:
    """R748: the types a CAD row follows when its `.ies` bytes match no imported type -- those of the
    `geometry`-route placements of geometry `base` with an `.ies` source of these bytes, where the
    type is still imported. The map row is not recorded (Phase 16 design M15), so the link is the
    base name and the digest (Phase 16 design M20)."""
    named = {r.registered_by.arguments.get("type_name") for r in _placement_records(manifest)
             if r.registered_by is not None
             and isinstance(r.registered_by.arguments.get("type_name"), str)
             and r.registered_by.arguments.get("route") == "geometry"
             and r.registered_by.arguments.get("geometry") == base
             and any(s.digest == digest and s.path.lower().endswith(".ies") for s in r.sources)}
    return [t for t in types if t.id.removeprefix(TYPE_PREFIX) in named]


def _type_names(types: list[InputRecord]) -> list[str]:
    return [t.id.removeprefix(TYPE_PREFIX) for t in types]


def _followed_advisory(row: str, ies_path: Path, type_record: InputRecord, *, base: str) -> Advisory:
    name = type_record.id.removeprefix(TYPE_PREFIX)
    current = next((s.path for s in type_record.sources if s.path.lower().endswith(".ies")), "not recorded")
    return Advisory(
        code="LUMINAIRE_ROW_FOLLOWED_REIMPORT", severity="warn", field="geometry",
        message=(f"row {row!r}: its ies file {ies_path} matches no imported luminaire type, but geometry {base!r} "
                 f"has placements of type {name!r} made from a file with these bytes, so this place uses {name!r}, "
                 f"whose recorded .ies source is {current}. The map still names {ies_path}; point the row at that "
                 "source to make the map and the manifest agree."))


def _retyped_advisories(manifest: Any, cad_rows: list[_CadRow]) -> list[Advisory]:
    """R748: one warning per existing placement this `place` gives a different type (Phase 16 design
    M19). A record whose `type_name` is not a string is reported as having no recorded type."""
    existing = {r.id: r for r in _placement_records(manifest)}
    advisories = []
    for cad in cad_rows:
        record = existing.get(PLACEMENT_PREFIX + cad.placement)
        if record is None or record.registered_by is None:
            continue
        old = record.registered_by.arguments.get("type_name")
        new = cad.type_record.id.removeprefix(TYPE_PREFIX)
        if old != new:
            was = f"was of type {old!r}" if isinstance(old, str) else "had no recorded type"
            advisories.append(Advisory(
                code="LUMINAIRE_PLACEMENT_RETYPED", severity="warn", field="geometry",
                message=f"placement {cad.placement!r} {was}; this place gives it type {new!r} (row {cad.row!r})."))
    return advisories


def _placed_from_geometry(runtime: Runtime, loaded: LoadedProject, *, geometry: str, row_types: dict[str, str] | None,
                          placement_names: dict[str, str] | None, dry_run: bool) -> CallToolResult:
    """`place` from a CAD import (spec §6). Everything that can refuse on the caller's arguments or
    sources -- the record, both source digests, every row's `ies` digest and type, `row_types`,
    `placement_names`, names and duplicates, rows with nothing drawn -- is decided before the one
    `xform` spawn (R477); every row commits or none."""
    paths = loaded.paths
    record = next((r for r in loaded.manifest.inputs
                   if r.kind == "geometry" and r.id in {geometry, _GEOMETRY_PREFIX + geometry}), None)
    if record is None:
        raise LuminaireRefusal(f"no geometry input {geometry!r} is registered.",
                               code="LUMINAIRE_NOT_FOUND", severity="error", field="geometry")
    arguments = record.registered_by.arguments if record.registered_by else {}
    obj_ref = next((s for s in record.sources if s.path.lower().endswith(".obj")), None)
    # R548: the map is the source whose path geometry_tool recorded as `material_map`, not a `.json` guess.
    map_path = arguments.get("material_map")
    map_ref = next((s for s in record.sources if s.path == map_path), None) if isinstance(map_path, str) else None
    if obj_ref is None or map_ref is None:
        raise LuminaireRefusal(f"geometry input {geometry!r} was not imported from an .obj with a material map.",
                               code="ARGUMENT_OUT_OF_RANGE", severity="error", field="geometry")
    obj_bytes = _read_recorded_source(obj_ref, OBJ_MAX_BYTES, runtime.roots)
    map_bytes = _read_recorded_source(map_ref, MATERIAL_MAP_MAX_BYTES, runtime.roots)
    mapping = parse_material_map(map_bytes, source=map_ref.path)
    document = mapping.luminaire_rows
    model = parse_obj(obj_bytes, mapped=True)
    base = record.id.removeprefix(_GEOMETRY_PREFIX)
    factor = metres_per_unit(arguments.get("units") or record.units or "m")
    up_axis = arguments.get("up_axis") or "Z"
    rows = mapping.luminaire_names
    if not rows:
        raise LuminaireRefusal(f"the material map {map_ref.path} has no luminaire rows, so there is nothing to place.",
                               code="ARGUMENT_OUT_OF_RANGE", severity="error", field="geometry")
    chosen_types = _refuse_row_keyed(row_types, field="row_types", rows=rows)
    names = _refuse_row_keyed(placement_names, field="placement_names", rows=rows)
    types = _type_records(loaded.manifest)
    advisories: list[Advisory] = []
    spd_rows = sorted(row for row in rows if document[row].get("spd") is not None)
    if spd_rows:
        advisories.append(Advisory(code="LUMINAIRE_SPD_IGNORED", severity="info", field="geometry",
                                   message=f"rows {spd_rows} carry spd, which rad_mcp does not read."))
    cad_rows: list[_CadRow] = []
    for row in sorted(rows):
        entry = document[row]
        rot = entry.get("rot")
        if rot is not None and not _is_triple(rot):
            raise LuminaireRefusal(f"row {row!r}: rot must be three finite numbers, got {rot!r}.",
                                   code="ARGUMENT_OUT_OF_RANGE", severity="error", field="geometry")
        # R537: the row's ies is read and digested whether or not row_types names its type.
        ies_path, ies_bytes = _read_row_ies(row, entry.get("ies"), map_ref.path, runtime.roots)
        digest = bytes_digest(ies_bytes)
        matches = [t for t in types if any(s.digest == digest and s.path.lower().endswith(".ies") for s in t.sources)]
        # R748: no imported type's recorded `.ies` source has these bytes, so follow the type this
        # geometry's placements were made with from them -- a re-import from another file must not
        # strand the row.
        followed = not matches
        if followed:
            matches = _followed_types(loaded.manifest, types, base=base, digest=digest)
        if not matches:
            raise LuminaireRefusal(f"row {row!r}: {ies_path} matches no imported luminaire type; import it first.",
                                   code="LUMINAIRE_IES_UNMATCHED", severity="error", field="geometry")
        if row in chosen_types:
            type_record = _find_type(loaded.manifest, chosen_types[row], field="row_types")
            if type_record not in matches:
                if followed:
                    raise LuminaireRefusal(
                        f"row {row!r}: row_types names {chosen_types[row]!r}, which is not among the types geometry "
                        f"{base!r} placed from a file with the bytes of {ies_path} (R748): {_type_names(matches)}.",
                        code="ARGUMENT_OUT_OF_RANGE", severity="error", field="row_types")
                raise LuminaireRefusal(
                    f"row {row!r}: row_types names {chosen_types[row]!r}, which was not imported from {ies_path}; "
                    f"that file matches {_type_names(matches)}.",
                    code="ARGUMENT_OUT_OF_RANGE", severity="error", field="row_types")
        elif len(matches) > 1:
            if followed:
                raise LuminaireRefusal(
                    f"row {row!r}: {ies_path} matches no imported luminaire type, and geometry {base!r} has placements "
                    f"of types {_type_names(matches)} made from a file with these bytes (R748); choose one with row_types.",
                    code="LUMINAIRE_IES_AMBIGUOUS", severity="error", field="row_types")
            raise LuminaireRefusal(f"row {row!r}: {ies_path} matches types {_type_names(matches)}; choose one with row_types.",
                                   code="LUMINAIRE_IES_AMBIGUOUS", severity="error", field="row_types")
        else:
            type_record = matches[0]
        _readable_type(type_record)  # N-111's remainder (R844): before the `xform` spawn, as R477 asks
        if followed:
            advisories.append(_followed_advisory(row, ies_path, type_record, base=base))
        markers = [model.vertices[p.vertex - 1] for p in model.points if row in model.point_names(p)]
        faces = [] if markers else [(face.obj, list(model.face_vertices(face))) for face in model.faces if row in model.face_names(face)]
        if not markers and not faces:
            raise LuminaireRefusal(f"luminaire row {row!r} has no faces and no p markers in the OBJ, so it would place no lamp.",
                                   code="ARGUMENT_OUT_OF_RANGE", severity="error", field="geometry")
        if len(markers) > PLACEMENT_MAX_INSTANCES:
            raise LuminaireRefusal(
                f"luminaire row {row!r} has {len(markers)} p markers, over the {PLACEMENT_MAX_INSTANCES}-instance bound "
                "on one placement (R489). Split the row across materials.",
                code="ARGUMENT_OUT_OF_RANGE", severity="error", field="geometry")
        placement = names.get(row, f"{base}__{row}")
        if not is_legal_name(placement):
            raise LuminaireRefusal(f"row {row!r}: the placement name {placement!r} is not legal; give one in placement_names.",
                                   code="ARGUMENT_OUT_OF_RANGE", severity="error", field="placement_names")
        cad_rows.append(_CadRow(row=row, placement=placement, type_record=type_record,
                                ies=FileRef(path=str(ies_path), digest=digest), rot=rot, faces=faces, markers=markers))
    by_name: dict[str, list[_CadRow]] = {}
    for cad in cad_rows:
        by_name.setdefault(cad.placement.casefold(), []).append(cad)
    shared = [[(cad.row, cad.placement) for cad in group] for group in by_name.values() if len(group) > 1]
    if shared:
        raise LuminaireRefusal(
            f"rows would share placement names, compared without regard to case (R543): {shared}; give each row "
            "its own name in placement_names.",
            code="ARGUMENT_OUT_OF_RANGE", severity="error", field="placement_names")
    placement_list = [cad.placement for cad in cad_rows]
    _refuse_case_collisions(loaded.manifest, kind="luminaire_placement", prefix=PLACEMENT_PREFIX, names=placement_list, field="placement_names")
    advisories.extend(_retyped_advisories(loaded.manifest, cad_rows))

    # Faces then markers, row by row: the consumption loop below reads `converted` in exactly this order.
    flat_points = [point for cad in cad_rows for point in [*(p for _obj, face in cad.faces for p in face), *cad.markers]]
    env = discover()
    with scratch_directory(paths.jobs, runtime.roots) as scratch:
        converted = iter(transform_points(env, flat_points, factor=factor, up_axis=up_axis, scratch=scratch, timeout=_ASSEMBLY_TIMEOUT_S))
    writes: dict[Path, bytes] = {}
    registrations: list[tuple[_CadRow, dict[str, Any]]] = []
    views: list[PlacementView] = []
    for cad in cad_rows:
        converted_faces = [(obj_name, [next(converted) for _ in face]) for obj_name, face in cad.faces]
        converted_markers = [next(converted) for _ in cad.markers]
        positions, seats = _cad_positions(cad, converted_faces, converted_markers, advisories)
        a = cad.type_record.registered_by.arguments  # type: ignore[union-attr]
        rot_t = tuple(cad.rot) if cad.rot is not None else None
        type_rad = f"{TYPES_PREFIX}/{a['name']}.rad"
        text = f"# luminaire placement {cad.placement} from geometry {base} row {cad.row} (rad_mcp)\n" + "".join(
            hand_line(type_rad, p, rot_t) + "\n" for p in positions)  # type: ignore[arg-type]
        target = paths.luminaire_placement_file(cad.placement)
        refuse_inline_commands_in_text(text, label=target, cwd=paths.root)
        placement_arguments = _placement_arguments(name=cad.placement, type_record=cad.type_record, route="geometry",
                                                   rot=cad.rot, array=None, positions=positions, geometry=base, seats=seats)
        advisories.extend(_rot_advisories(cad.rot, where=f"row {cad.row!r}"))
        writes[target] = text.encode("utf-8")
        registrations.append((cad, placement_arguments))
        views.append(PlacementView(name=cad.placement, type_name=a["name"], route="geometry", instances=len(positions),
                                   positions=[list(p) for p in positions], rot=cad.rot, files=[]))
    _refuse_emitter_overlap(loaded.manifest, [a for _cad, a in registrations], field="geometry")
    _refuse_unrecorded_destinations(loaded.manifest, paths, writes, field="placement_names")
    if dry_run:
        return emit(LuminairePlaced(project=paths.name, root=str(paths.root), action="place", placements=views, dry_run=True,
                                    argv=[xform_argv(factor=factor, up_axis=up_axis)], advisories=advisories or None))

    def register(project: LoadedProject) -> list[Path]:
        for cad, placement_arguments in registrations:
            register_input(project, input_id=PLACEMENT_PREFIX + cad.placement, kind="luminaire_placement",
                           files=[paths.luminaire_placement_file(cad.placement)],
                           registered_by=ToolCall(tool="luminaire", action="place", arguments=placement_arguments),
                           sources=[FileRef(path=obj_ref.path, digest=obj_ref.digest),
                                    FileRef(path=map_ref.path, digest=map_ref.digest), cad.ies],
                           server_written=True, save=False)
        return []

    # R535 (R525/R530): the result is built from the project `_write_and_register` saved.
    def recheck(project: LoadedProject) -> None:
        _refuse_case_collisions(project.manifest, kind="luminaire_placement", prefix=PLACEMENT_PREFIX, names=placement_list, field="placement_names")
        _refuse_types_changed(project, [cad.type_record for cad in cad_rows], field="geometry")
        _refuse_emitter_overlap(project.manifest, [a for _cad, a in registrations], field="geometry")

    saved = _write_and_register(runtime, paths, writes, register, field="placement_names", recheck=recheck)
    ids = [PLACEMENT_PREFIX + cad.placement for cad, _ in registrations]
    by_id = {r.id: r for r in saved.manifest.inputs}
    placed = [_placement_view(by_id[i]) for i in ids]
    result = LuminairePlaced(project=paths.name, root=str(paths.root), action="place", placements=placed, advisories=advisories or None)
    return _emit_with_links(result, [f.path for i in ids for f in by_id[i].files])


def _listed(runtime: Runtime, loaded: LoadedProject) -> CallToolResult:
    manifest = loaded.manifest
    advisories: list[Advisory] = []
    for record in [*_type_records(manifest), *_placement_records(manifest), *[r for r in manifest.inputs if r.id == SUPPORT_ID]]:
        for label, refs, capped in (("stored", record.files, False), ("source", record.sources, True)):
            for ref in refs:
                # R506: one suffix-cap map, `geometry_tool._cap_for`, which carries `.ies` too.
                reason = drift(ref, cap=_cap_for(ref.path) if capped else None, roots=runtime.roots)
                if reason is not None:
                    advisories.append(Advisory(code="GEOMETRY_SOURCE_CHANGED", severity="warn", field="files",
                                               message=f"luminaire record {record.id!r}: {label} file {ref.path} is {reason}."))
    support = None
    if any(r.id == SUPPORT_ID for r in manifest.inputs):
        state = source_cal_state(loaded.paths.source_cal, previous_digests=_known_digests(manifest))
        support = SupportView(package_digest=SOURCE_CAL_DIGEST, matches_package=state == "current")
    return emit(LuminaireListed(
        project=loaded.paths.name, root=str(loaded.paths.root), action="list",
        types=[_type_view(r) for r in _type_records(manifest)],
        placements=[_placement_view(r) for r in _placement_records(manifest)],
        support=support, advisories=advisories or None,
    ))


def _unlink_removed(path: Path, paths: ProjectPaths) -> None:
    """Delete one file a just-removed record named, best-effort (R541 = R505 + R525 + R522).

    Called only after `save_manifest` has already committed the manifest without the removed
    record: the post-condition the caller asked for (the record is gone) already holds, so a
    delete that fails or is declined here is logged, never turned into a refusal of a call that
    has already succeeded.

    **Only a path whose resolved parent is `12_luminaires/types`, `12_luminaires/placements`, or --
    for `source.cal` only -- the project root is ever unlinked**, and only when it is lstat-regular.
    A hand-edited manifest whose `files` names some other path in the project (or outside it) must
    not turn `remove` into a delete of an arbitrary path; that path is left in place and only the
    manifest entry is gone, which `test_a_files_entry_outside_the_luminaire_directories_is_never_
    deleted` pins.

    **If `12_luminaires` has become a symlink since the record was written, the comparison fails
    closed**: the recorded path's parent is the old resolved directory and the allowed directories are
    resolved afresh, so they differ, and the file is left in place and logged. A link that existed
    before the import is resolved into the recorded paths (`file_refs`), so those files are deleted
    as usual. The failure direction is the safe one (nothing else is deleted).
    """
    allowed = [paths.luminaire_types, paths.luminaire_placements]
    if path.name == "source.cal":
        allowed.append(paths.root)
    _unlink_if_in(path, allowed)


def _removed(runtime: Runtime, loaded: LoadedProject, *, name: str, kind: str, dry_run: bool) -> CallToolResult:
    """`remove` (spec Sec5.3): deregister one luminaire type or placement and delete the files it
    named. `name`, `kind` and `confirm=true` are already checked in `_dispatch`, before the project
    is opened (R451/R59, R542).

    Refused, in order, once the project is open: unknown record -> `LUMINAIRE_NOT_FOUND`; a type
    a placement still names -> `LUMINAIRE_TYPE_IN_USE`; any record about to be removed (the support
    record included, with the last type) named in a sealed artifact's `built_from` ->
    `ARTIFACT_EDGES_INCOMPLETE`, via `refuse_if_built_from` -- nothing is deleted and the project
    still opens on a later call.

    **Manifest first, files second (R541 = R505 + R525 + R522).** `save_manifest` is the point this
    commits; every unlink after it runs through `_unlink_removed`, which is best-effort and where
    the containment check lives -- see its docstring.
    """
    paths = loaded.paths
    record_id = (TYPE_PREFIX if kind == "type" else PLACEMENT_PREFIX) + name
    with runtime.modifying(paths):
        current_project = open_project(paths.root, radiance_version=runtime.launcher.version)
        manifest = current_project.manifest
        record = next((r for r in manifest.inputs if r.id == record_id), None)
        if record is None:
            raise LuminaireRefusal(f"no luminaire {kind} {name!r} is registered.",
                                   code="LUMINAIRE_NOT_FOUND", field="name", severity="error")
        removed_ids = [record_id]
        if kind == "type":
            users = sorted(_recorded_name(r) for r in _placement_records(manifest)
                           if r.registered_by and r.registered_by.arguments.get("type_name") == name)
            if users:
                raise LuminaireRefusal(f"luminaire type {name!r} is placed by {users}; remove those placements first.",
                                       code="LUMINAIRE_TYPE_IN_USE", field="name", severity="error")
            if len(_type_records(manifest)) == 1:
                removed_ids += [r.id for r in manifest.inputs if r.id == SUPPORT_ID]
        refuse_if_built_from(manifest, removed_ids)
        doomed = [Path(f.path) for r in manifest.inputs if r.id in removed_ids for f in r.files]
        view = RemovedView(kind=kind, name=name, files=[str(p) for p in doomed])
        if dry_run:
            return emit(LuminaireRemoved(project=paths.name, root=str(paths.root), action="remove", removed=view, dry_run=True))
        manifest.inputs[:] = [r for r in manifest.inputs if r.id not in removed_ids]
        save_manifest(paths, manifest)
        for path in doomed:
            _unlink_removed(path, paths)
    return emit(LuminaireRemoved(project=paths.name, root=str(paths.root), action="remove", removed=view))
