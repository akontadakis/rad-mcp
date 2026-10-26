"""The `geometry` tool -- docs/tool-surface.md#4-geometry.

Thin: this module contains and validates arguments, picks the engine call, maps findings onto the
wire and shapes the envelope. `rad_core.geometry` owns the enclosure test, the coplanar-duplicate
scan, the degenerate-polygon test, the bounding-box arithmetic and the unit factors;
`rad_engine.scene`/`rad_engine.octree` own `xform`, `oconv -f`, `getinfo -d` and the parsing of their
output. **No number reported here is computed here.**

## Ruling R32: `geometry`'s `role` is not `InputRecord.role`

They share a name and they are different things, and this is the trap the module has to state where
someone writing the call is looking.

- **`geometry(role=...)`** takes one of `interior`, `exterior`, `context`, `shading`, `aperture`. It
  is an *assembly-time* classification: which polygon soup `NOT_ENCLOSED` is computed over, and which
  octree a matrix method later assembles from it.
- **`rad_core.materials.MaterialRole`** -- what `rad_engine.project.register_input(role=...)` takes --
  has seven entirely different members (`floor`, `wall`, `ceiling`, `furniture`, `ground`,
  `exterior_vertical`, `trees`) and says which material a *modifier* plays. Only `materials` writes it.

**None of geometry's five values is a `MaterialRole` member**, so passing one to `register_input`
raises `InvalidRoleError` (`ARGUMENT_OUT_OF_RANGE`, `field="role"`). This module therefore **never**
passes its own `role` to `register_input` -- the call below passes no `role` at all, and the value is
carried on the result and in the manifest's record of the call that registered the input. See
docs/tool-surface.md §4 and "What it records → Per input", where 7a documented the distinction at
both sites.

## Materials come first, and not by convention

`scene_files` is built by `rad_engine.materials.scene_files_with_materials`, never assembled here.
`oconv`/`xform` read their arguments strictly in order and a modifier must be defined before it is
referenced, with no flag that changes either; materials after geometry means `UNDEFINED_MODIFIER` at
`error` (docs/specs/artificial-lighting.md §5.2). That function is the one place the argument list is
built with every materials path ahead of every geometry path, **and** it is the one place two
materials files defining the same modifier can be caught -- `oconv` takes the last definition, exits 0
and reports nothing. A hand-built list gets neither guarantee, which is why one is not built here.

## The OBJ path, and why it parses before it discovers

`.obj` import (`rad_engine.convert` in docs/tool-surface.md §4's Layers row) is built: one `.obj` per
call, with an optional JSON material map beside it. `_dispatch` reads and parses both **before**
`discover()` -- `rad_core.obj` and `rad_core.material_map` are pure, and putting them ahead of the
install lookup is what makes every hostile-input refusal answerable with no Radiance at all and
provable to have run no converter. Moving a parse after `discover`, or a containment check after a
read, is what this order exists to prevent, not a matter of taste.

## Each refused format carries its own reason

glTF, gbXML, `.rif` and IFC all carry the same `ARGUMENT_OUT_OF_RANGE`/`field="files"` code an
argument rejected on its face gets (ruling R14), and each carries its own *reason*, because the
recoveries differ. glTF and gbXML are *sourced and deliberately not built*
(docs/tool-surface.md#sourced-and-deliberately-not-built) -- no spec in docs/specs/ sources either
format and the 6.1a install has no converter for either. IFC has a converter nowhere in this install
and no citation in docs/specs/ either. A `.rif` is different in kind: it is `rad`'s control file,
naming scene files and render options, and holds no geometry to import at all. **It was in
`SUPPORTED_SUFFIXES` from Phase 2 and nothing here ever parsed one**, so `oconv` read its `SCENE=`
lines as if they were primitives; Phase 10 refuses it instead. No refusal promises a future version,
and every one of them names the supported set.
"""

from __future__ import annotations

import logging
import os
import stat
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated, Any, ClassVar, Final, Literal, NamedTuple

from mcp_types import CallToolResult
from pydantic import BaseModel, ConfigDict, Field, RootModel

from rad_core.geometry import (
    GeometryError,
    Polygon,
    UndefinedModifierError,
    area_by_modifier,
    area_removed,
    bounding_box,
    coincident_pairs,
    coincident_surfaces,
    degenerate_polygon_ids,
    far_from_origin_warning,
    inventory,
    metres_per_unit,
    non_finite_polygon_ids,
    non_planar_warning,
    scale_warning,
    triangulation_warning,
    window_warning,
    windows_without_opening,
)
from rad_core.manifest import ArtifactWarning, FileRef, ToolCall, Units
from rad_core.material_map import (
    MATERIAL_MAP_MAX_BYTES,
    MaterialMap,
    parse_material_map,
)
from rad_core.materials import MaterialRole, MaterialsError
from rad_core.obj import OBJ_MAX_BYTES, NonFiniteCoordinatesError, ObjModel, parse_obj
from rad_core.photometry_file import IES_MAX_BYTES
from rad_engine import (
    ConvertedObj,
    EmittedMaterial,
    EngineAdvisory,
    FileTooLargeError,
    GeometryConversionError,
    LoadedProject,
    MaterialsEmissionError,
    MaterialsFile,
    OctreeSetOverflowError,
    OctreeTooManyFilesError,
    ProjectError,
    ProjectPaths,
    RadianceEnv,
    RadianceError,
    RadianceNotFound,
    RadianceTimeoutError,
    SceneAssembly,
    SceneAssemblyError,
    SceneFileNotFoundError,
    SceneInlineCommandError,
    ScratchOutsideRootsError,
    assemble_scene,
    bytes_digest,
    classify,
    convert_obj,
    discover,
    file_refs,
    flatten_scene,
    glass_modifiers,
    non_polygon_surfaces,
    open_project,
    parse_polygons,
    read_capped,
    refuse_inline_commands_in_text,
    register_input,
    rules_text,
    save_manifest,
    scale_scene_text,
    scene_files_with_materials,
    scratch_directory,
    triangulate_obj,
    unscaled_geometry_records,
    write_materials,
)
from rad_engine.safe_io import (
    NotARegularFileError,
    handle_digest,
    open_regular_file,
    open_staging_file,
    refuse_existing_non_regular_destination,
    resolved_parent_within,
)
from rad_mcp.advisories import to_wire_all, warnings_to_wire
from rad_mcp.envelope import (
    Advisory,
    RadResult,
    Refusal,
    emit,
    one_of_schema,
    refuse,
    refuse_missing,
)
from rad_mcp.materials_tool import (
    AssignmentRefused,
    InputStillBuiltFrom,
    commit_materials_locked,
    ground_finding,
    refuse_if_built_from,
    replay_materials,
    set_assignments_for_import,
)
from rad_mcp.names import NAME_PATTERN
from rad_mcp.paths import (
    PathOutsideRoot,
    RootsNotConfigured,
    contain,
    contain_octree_destination,
)
from rad_mcp.runtime import Runtime, current, refuse_engine_error

logger = logging.getLogger(__name__)

__all__ = ["DESTRUCTIVE_ACTIONS", "GeometryResult", "drift", "geometry"]

Action = Literal["add", "list", "remove", "check"]

#: Named in the tool description, because annotations are per-tool and destructiveness here is
#: per-action (docs/tool-surface.md#annotations-are-coarser-than-the-surface): *"`geometry` cannot be
#: marked non-destructive, because `action="remove"` is."* `tests/test_protocol.py` reads this
#: constant rather than a list of its own.
#:
#: **`add` joined `remove` in Phase 10.** Before it, `add` only registered files the caller
#: already owned and changed nothing on disk. An `.obj` import -- and a `.rad` import declared in
#: any unit but metres -- writes `01_geometry/<id>.rad`
#: itself and, with a material map, replaces the mapped modifiers' values in the project's `set`
#: materials file and may withdraw them from the LM-83 defaults file -- both of which overwrite
#: state a previous call put there. A host that prompts on `destructiveHint` has to be told that
#: before the call, not after it.
DESTRUCTIVE_ACTIONS: tuple[str, ...] = ("add", "remove")

#: The formats this tool accepts. `.rad` is what `oconv`/`xform` read directly; `.obj` goes through
#: `rad_engine.convert` first (Phase 10). Read by `tests/test_readme_formats.py`, which asserts the
#: README's geometry bullet names this exact set -- a published promise and the code that keeps it
#: cannot be two lists maintained apart.
#:
#: **`.rif` left this tuple in Phase 10.** It was accepted from Phase 2 and nothing in this server
#: ever parsed one: a `.rif` is `rad`'s control file, holding `SCENE=`/`ZONE=`/`view=` lines and
#: render options, and handing it to `oconv` as geometry compiles those lines as if they were
#: primitives. It is refused below, for that reason, rather than accepted and misread.
SUPPORTED_SUFFIXES: tuple[str, ...] = (".rad", ".obj")

#: Refused outright, each with the reason the refusal states and no promise of a future version.
#: glTF and gbXML are recorded under docs/tool-surface.md#sourced-and-deliberately-not-built; `.rif`
#: and IFC joined them in Phase 10, and their reasons are different in kind, which is why this is a
#: mapping to a sentence rather than a set sharing one: a `.rif` holds no geometry to import at all,
#: while IFC holds plenty and has no converter here to read it with. The recoveries differ, so the
#: refusals do.
_REFUSED_SUFFIXES: dict[str, str] = {
    ".gltf": "glTF, which no spec in docs/specs/ sources and the Radiance 6.1a install has no converter for",
    ".glb": "glTF, which no spec in docs/specs/ sources and the Radiance 6.1a install has no converter for",
    ".gbxml": "gbXML, which no spec in docs/specs/ sources and the Radiance 6.1a install has no converter for",
    ".rif": "a .rif control file, which names scene files and render options rather than holding geometry",
    ".ifc": "IFC, for which no converter is installed or cited",
}

#: The capability whose binaries every `add`/`check` needs. `xform` is in it (Task 5 added it), and
#: `rad_engine.scene.read_scene` calls `xform` to flatten a scene's `!xform` chain before `oconv`
#: ever runs, so an install missing this capability cannot import geometry at all.
_IMPORT_CAPABILITY = "geometry_import"

#: Which registered input ids this tool owns. One per `geometry(name=...)`; `name` defaults to the
#: role so that a project importing an interior and a context scene gets two records rather than one
#: overwriting the other.
_INPUT_PREFIX = "geometry_"

#: How long scene assembly may spend inside `xform` and `oconv` before the call is abandoned.
#:
#: `rad_engine.run.run` defaults `timeout` to `None`, which is *no bound at all* -- and this tool and
#: `materials_tool._geometry_usage` were the only two call sites that took the default, so `xform`
#: and `oconv` ran unbounded on the request thread while every other spawn in the codebase
#: (`octree._METADATA_TIMEOUT_S`, `env._probe_version`) already declared one. A scene compile is not
#: a metadata read, so it gets a longer bound than `octree.py`'s 30 s rather than sharing it:
#: `test_room` assembles in well under a second, and the office fixture likewise, so five minutes is
#: three orders of magnitude of slack for a genuinely large import while still bounding the hang.
#:
#: **A bound, not a performance budget.** Exceeding it means something is wrong -- an `!xform` chain
#: that will not terminate, a filesystem that has stopped answering -- not that the scene was merely
#: big. Simulation work belongs on the job model (CONTEXT.md) and never on this path, which is why a
#: value this generous is still safe to state here. Sits at the tool layer, beside `job_tool`'s own
#: `MAX_WAIT_S`, because it is a policy about how long a *tool call* may block, not Radiance
#: knowledge -- `rad_engine` correctly declines to pick a number for its callers.
#:
#: **It bounds each Radiance spawn, not the call** (ruling R453, the owner). It is passed to every
#: converter and octree subprocess, separately. The pure-Python stages -- `parse_obj`, `_health`,
#: `_checks` -- have no bound, by the owner's decision, and one `check` of a 134 MB OBJ ran
#: 348.97 s. So 300 s is never a promise about how long an import takes.
_ASSEMBLY_TIMEOUT_S: Final[float] = 300.0

#: What marks an advisory as being about the **un-triangulated** soup rather than the registered one
#: (`_import_obj`, design §5.7). One home for the string, because it is the only thing on the wire
#: that distinguishes two findings whose `code`, `severity` and `field` are all identical: the
#: registered scene's findings carry no prefix, so a marker on the other side is the whole
#: distinction. Published prose -- clients match on it -- so it is a constant here rather than an
#: f-string at the one call site.
_BEFORE_TRIANGULATION: Final = "before triangulation: "


# --- nested shapes -----------------------------------------------------------------------------


class _Nested(BaseModel):
    model_config = ConfigDict(extra="forbid")


# R810 moved this from the published text: Phase 10 scales the geometry itself (`xform -s` before
# `oconv` reads it, on both import paths), so the octree `getinfo -d` measures is built from metre
# coordinates. Until Phase 10 Task 11 a non-metre `.rad` reported `min`/`max` scaled in Python
# (`rad_core.geometry.convert_to_metres`) beside the *unscaled* octree's cube, so the two keys
# differed by the unit factor. `min`/`max` are `bounding_box` of the parsed scene polygons, not
# read off the octree; the old text's "both come off the same compiled octree" was loose.
class BBoxView(_Nested):
    """The scene's extents (`min`, `max`) and the octree's bounding cube (`octree_cube`, as
    `[x, y, z, size]`).

    Both are in metres whenever `units` was declared: the geometry is scaled before the octree is
    built, and `GEOMETRY_UNITS_CONVERTED` gives the factor. On a `check` without `units` nothing is
    scaled, and both are in the file's own units.
    """

    min: list[float]
    max: list[float]
    octree_cube: list[float]


# R810 corrected this from the published text, which said the digest is "never a second read of
# the path". True of an `.obj` import (`bytes_digest(obj.data)`, the bytes `_read_obj_input`
# parsed); false of a scaled `.rad` import, whose `sources` are `file_refs(...)`, a fresh
# `file_digest` of each file after the flatten read it. `geometry(action="list")` reads
# `record.sources` back through this same class.
class SourceView(_Nested):
    """One file an import was computed from, and its content digest at import.

    `geometry(action="list")` compares the digest with the file as it is now, and reports
    `GEOMETRY_SOURCE_CHANGED` when they differ.
    """

    path: str
    digest: str


# R810 moved this from the published text: filled by `_import_obj` since Task 13, and declared
# one task early so the wire shape did not change when `triangulate` started being honoured. The
# area threshold is `rad_core.geometry.TRIANGULATION_AREA_TOLERANCE_M2` (1e-6); losses only, as
# `area_removed` argues.
class TriangulatedView(_Nested):
    """What `triangulate=True` did to an `.obj` import: `robjutil +T`, then `obj2rad` again.

    `polygons_before` and `polygons_after` count the converted scene's polygons on each side, not
    OBJ faces. `area_removed_m2_by_material` names each modifier whose total area fell by more
    than 0.000001 m², the subject of `TRIANGULATION_REMOVED_SURFACE`. It is ordinarily empty, and a
    modifier that gained area, as a warped face does when split, is not listed.
    """

    polygons_before: int
    polygons_after: int
    area_removed_m2_by_material: dict[str, float]


# R810 moved this from the published text: each count answers its own question, which is why they
# are published separately rather than as one "faces lost" figure.
class FacesView(_Nested):
    """How an `.obj`'s faces became the scene's polygons.

    `obj_faces` counts the faces in your `.obj`, `luminaire_faces_omitted` those left out as
    luminaire rows, `split_faces` the warped faces `obj2rad` split into triangles, and `polygons`
    the polygons in the stored scene. They do not reconcile by subtraction: a split face becomes
    several polygons. With `triangulate=True`, `polygons` counts the triangulated scene while
    `obj_faces` and `split_faces` describe your `.obj`; `triangulated` is `null` otherwise.
    """

    obj_faces: int
    luminaire_faces_omitted: int
    polygons: int
    split_faces: int
    triangulated: TriangulatedView | None = None


# An earlier version of this docstring said a skipped check is "never a silent omission", which the
# code does not keep; the `role == "context"` skip in `_health` is the counter-example. A totality
# claim over a field this narrow was the wrong sentence. The history left the published
# description under R809 (item 38).
#
# R810 moved this from the published text: the rows are `_ABSOLUTE_CHECKS`. Design §5.10 scopes
# `skipped` to `check` and to the units precondition; the `role` skip applies to `add` too, so
# listing it here would make the field mean two things.
class SkippedView(_Nested):
    """One check a `check` without `units` did not run, and why (`reason`).

    A check with an absolute tolerance (1 mm coincidence, 5 mm planarity) means nothing over
    coordinates of unknown unit, so it is listed here rather than reported as passed. A check
    skipped for another reason has no row: with `role="context"` the window check is skipped
    silently.
    """

    check: str
    reason: str


# R810 corrected this from the published text, which said "plus the modifiers nothing defined":
# `_checks` always builds `undefined_modifiers=[]`, because `assemble_scene` raises
# `UndefinedModifierError` instead, refused as `UNDEFINED_MODIFIER`. The key stays because
# docs/tool-surface.md §4 names it. `enclosed: false` on an empty soup would be a verdict nothing
# established; the field docstring below gives the tolerances behind the two `null` counts.
class ChecksView(_Nested):
    """The scene checks.

    `enclosed` is `null` when the scene has no polygons. `degenerate_polygons` and
    `coincident_pairs` are `null` on a `check` without `units`, since both rest on an absolute
    tolerance (`skipped` lists them); `0` means checked and none found. `undefined_modifiers` is
    always `[]`: a scene using a modifier nothing defines is refused with `UNDEFINED_MODIFIER`.
    """

    octree_non_empty: bool
    enclosed: bool | None = None
    undefined_modifiers: list[str]
    degenerate_polygons: int | None
    coincident_pairs: int | None
    """`null` when the check was skipped, which is a different statement from `0`.

    Both carry an absolute tolerance -- `_DEGENERATE_AREA_EPS` on the square of an area, in m^4,
    `COINCIDENCE_TOLERANCE_M` at 1 mm -- so a `check` whose units nobody declared cannot run either
    of them over coordinates that might be millimetres or feet. `skipped` names them and says why;
    `0` here would be a count of nothing found, which nothing established.
    """


# R810 moved this from the published text: `server_written` and `sources` are
# `rad_core.manifest.InputRecord.server_written` and `.sources`; what `remove` deletes is
# `_removed`'s. Always present rather than omitted: an in-place registration is a positive
# statement about where the bytes live.
class GeometryInputView(_Nested):
    """One registered geometry input, as `geometry(action="list")` reports it.

    `server_written` is `true` when `files` is this server's metre copy in `01_geometry/`, not a
    file you passed; `sources` then names the files it was converted from (the `.obj` and its map,
    or the `.rad` and its includes), with their digests. Both are always present: `false` and `[]`
    for files registered in place. `remove` deletes the `01_geometry/` copy, never a file you
    supplied or a `sources` file.
    """

    id: str
    files: list[str]
    units: str | None = None
    role: str | None = None
    server_written: bool = False
    sources: list[SourceView] = []


# --- the result branches -----------------------------------------------------------------------


class GeometryBase(RadResult):
    """What every geometry-shaped answer carries. Also the union schema's shared base."""

    project: str
    root: str


# R810 moved this from the published text: one branch for both actions, because they compute the
# same thing and differ only in whether the result is registered. R810 lifted the omitted-keys
# rule from the `OMIT_WHEN_NONE` comment below into the published text: it reached no host before.
class GeometryAssembled(GeometryBase):
    """`action="add"` and `action="check"`: the same import and checks.

    `registered` says whether the input was registered (`add`); `input_id` is `null` on `check`.
    A key the import did not answer is omitted, not `null`: `faces` is on `.obj` imports only;
    `materials_written`, `materials_replaced` and `materials_withdrawn` on an `.obj` `add` with a
    `material_map`, and `unused_map_rows` on any `.obj` import with one; `sources` is absent when
    a `.rad` was not scaled; `skipped` is only on a `check` without `units`. An empty list is an
    answer: `unused_map_rows: []` means every map row was used. `materials_written` is the map's
    own rows, not what the materials file now holds; the file also keeps earlier recorded
    assignments.
    """

    action: Literal["add", "check"]
    role: str | None = None
    files: list[str]
    polygon_count: int
    modifiers: list[str]
    bbox: BBoxView
    checks: ChecksView
    octree: str
    input_id: str | None = None
    registered: bool

    # --- the import's own report (Phase 10). Each key is present only where the call actually
    # answered its question: `sources` is the files an import was computed *from* as opposed to the
    # files it was *given* (an `.obj` and its map, or a scaled `.rad` and its `!xform` includes --
    # never a metre `.rad`, which is registered in place), `faces` is the OBJ-to-polygon accounting
    # and is absent on every `.rad`, the three `materials_*` lists are what a material map did to the
    # project's materials and so are `.obj`-only, and `skipped` is `check`'s record of the questions
    # it could not ask, which either import can carry. Every one of them is in `OMIT_WHEN_NONE`
    # below, so a key that was never answered is missing rather than null.
    sources: list[SourceView] | None = None
    faces: FacesView | None = None
    materials_written: list[str] | None = None
    materials_replaced: list[str] | None = None
    materials_withdrawn: list[str] | None = None
    unused_map_rows: list[str] | None = None
    skipped: list[SkippedView] | None = None

    #: **Omitted, not `null`.** The distinction these seven share with `advisories` and not with
    #: `enclosed`: `enclosed: null` is a verdict that was not reached about a soup that exists,
    #: while `faces: null` on a `.rad` import would be an answer to a question the call never asked.
    #: An empty list is still emitted -- `unused_map_rows: []` says the map had no unused row, which
    #: is a finding, and is why the office-room check asserts `[]` rather than absence.
    OMIT_WHEN_NONE: ClassVar[frozenset[str]] = RadResult.OMIT_WHEN_NONE | {
        "sources",
        "faces",
        "materials_written",
        "materials_replaced",
        "materials_withdrawn",
        "unused_map_rows",
        "skipped",
    }


class GeometryListed(GeometryBase):
    """`action="list"`: every registered geometry input.

    Each registered file and source is re-read; one that changed, is missing or cannot be read
    since its import gets a `GEOMETRY_SOURCE_CHANGED` advisory.
    """

    inputs: list[GeometryInputView]


# R810 moved this from the published text: `_removed` says what is deleted and carries the ruling
# candidate on publishing the deleted files, as `server.SensorGridRemoved.removed_files` does for
# `sensor_grid(action="remove")`. R810 added the not-registered and unsafe-delete cases from
# `_removed` and `_unlink_contained`, which logs a skipped delete and never raises.
class GeometryRemoved(GeometryBase):
    """`action="remove"`: the input is deregistered, and the file this server wrote for it in
    `01_geometry/` is deleted; a file you supplied is never deleted.

    `removed` names the input ids, `[]` when no such input was registered (not a refusal). It does
    not name deleted files, and a server-written file that cannot be deleted safely is left on disk
    with no advisory. Removing an input an artifact was built from is refused
    (`ARTIFACT_EDGES_INCOMPLETE`), and nothing is changed.
    """

    removed: list[str]


GeometryPayload = GeometryAssembled | GeometryListed | GeometryRemoved | Refusal


class GeometryResult(RootModel[GeometryPayload]):
    """What `geometry` returns: the `add`/`check`, `list` or `remove` result, or a refusal."""

    @classmethod
    def model_json_schema(cls, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return one_of_schema(super().model_json_schema(*args, **kwargs), GeometryBase)


# --- argument checks -----------------------------------------------------------------------------


def _refuse_argument(message: str, *, field: str) -> CallToolResult:
    return refuse(
        message,
        [Advisory(code="ARGUMENT_OUT_OF_RANGE", severity="error", field=field, message=message)],
    )


def _refuse_format(files: list[str]) -> CallToolResult | None:
    """Every suffix `_REFUSED_SUFFIXES` names, then anything else that is not importable. `None` when
    every file is.

    `ARGUMENT_OUT_OF_RANGE` on `files` throughout -- an argument rejected on its face, before any
    work was attempted (ruling R14) -- and the *reason* is what distinguishes the cases, because the
    recoveries do. **`_NOT_BUILT_SUFFIXES` and its branch are gone** (Phase 10 Task 11): the tuple
    was emptied when the OBJ path landed and the branch it guarded became unreachable, and a branch
    no input can reach is a claim no test can keep honest. The formats this task added -- `.rif` and
    IFC -- are refused with their own reasons from `_REFUSED_SUFFIXES` rather than from a second
    branch, which is what makes each refusal answerable from one table.

    The supported set is named from `SUPPORTED_SUFFIXES` and never spelled out here: the sentence is
    published to the caller, and a hand-written copy of a list the code already holds is the drift
    `tests/test_readme_formats.py` exists to catch one document further out.
    """
    named = ", ".join(SUPPORTED_SUFFIXES)
    for candidate in files:
        suffix = Path(candidate).suffix.lower()
        if (reason := _REFUSED_SUFFIXES.get(suffix)) is not None:
            return _refuse_argument(
                f"{candidate} is {reason}. Supported: {named}. Export or convert to Radiance "
                "geometry and pass that.",
                field="files",
            )
        if suffix not in SUPPORTED_SUFFIXES:
            return _refuse_argument(
                f"{candidate} is not a Radiance scene file. Supported: {named}.", field="files"
            )
    return None


def _identity(path: Path) -> tuple[int, int] | None:
    """`(st_dev, st_ino)` -- what the filesystem calls one file -- or `None` when it cannot be read.

    **`None` rather than a raise, because a path that does not exist yet is not this guard's
    refusal.** `geometry(files=[".../m.obj"])` on a missing file must reach its own "does not exist"
    answer (`_capped` -> `_ArgumentRefused`, `field="files"`), not die inside a comparison. `None` is
    never equal to a real identity, so a missing candidate falls through every check below.

    `stat` follows symlinks, deliberately: so does every read this server does, and so does the
    `resolve()` that `rad_mcp.paths.contain` already applied, so a symlink is compared by what it
    points at.

    **`ValueError` as well as `OSError`, and `_same_file` is why** (fix round 3's own defect, found in
    round 4). Every other caller hands this function a path that has already been through
    `rad_mcp.paths.contain` -- which refuses a NUL byte outright -- or through `Path.iterdir`, which
    cannot produce one. `_same_file` is the first caller that hands it a **manifest-supplied string**:
    `rad_core.manifest.FileRef.path` is a bare `str` with no NUL validation, and
    `Path("/tmp/a\x00b").stat()` raises `ValueError: embedded null byte`, not an `OSError`. So a
    corrupted or hand-edited `manifest.json` turned what string comparison used to answer plainly
    `False` into an uncaught `ValueError` on every `add` -- an uncoded transport failure where there
    had been none, which is check 0.4's class. Measured, then fixed.

    **An empty `FileRef.path` is not guarded, and that is deliberate.** `Path("")` is `Path(".")`, so
    it returns the **cwd's** identity rather than `None`. It cannot produce a wrong answer: the only
    caller that can be handed an empty string is `_same_file`, whose other operand is always
    `01_geometry/<id>.rad` -- a regular file or nothing -- and a directory's identity can never equal
    a regular file's, so the comparison falls through to `one == other`, which is `False`: the right
    answer for a path that names no file. The ancestor walk in `_refuse_server_written_input` cannot
    reach it at all, because every path it walks came from `contain`, which refuses a relative path.
    A branch for it would be a claim no input can reach and no test can keep honest, which is the
    shape `_refuse_format`'s docstring records having just removed.
    """
    try:
        info = path.stat()
    except (OSError, ValueError):
        return None
    return (info.st_dev, info.st_ino)


def _same_file(one: Path, other: Path) -> bool:
    """One file under two paths: `(st_dev, st_ino)` where both exist, path equality where they do not.

    **Fix round 3, Critical.** Two supported calls -- `geometry(action="add", name="scene")` then
    `name="Scene"` -- land on one file on a case-insensitive filesystem, because the destination is
    `geometry_<name>.rad` and `rad_mcp.names.NAME_PATTERN` allows uppercase. The `others` check
    compared `Path(f.path) == destination`, so the second import wrote new geometry over the first
    record's file and left it under the *previous* record's `FileRef.digest` -- Task 10's Critical 1
    class exactly, undetectable by `recompute_digests` (which works from recorded digests) or
    `is_reusable` (which re-digests the artifact, never its inputs). Measured: `01_geometry/` held
    only `geometry_scene.rad`, `geometry_Scene` matched the disk, `geometry_scene` did not, and the
    bytes described the second scene.

    The identity half is the fix; **the path-equality fallback is not decoration.** A record can name
    a file that no longer exists -- deleted outside the server, or on a project moved between
    machines -- and `_identity` answers `None` for it. Path equality is then the only comparison
    available and is what the check did before, so nothing that was refused stops being refused.

    Not a case-insensitive uniqueness rule on `name`: that is a policy change about the whole
    namespace, and it is not what closed the data loss.
    """
    if (identity := _identity(one)) is not None and identity == _identity(other):
        return True
    return one == other


def _input_id(name: str | None, role: str | None) -> str:
    """The manifest id one import registers under, derived once.

    `_INPUT_PREFIX + (name or role or "scene")` was spelled in `_import_obj` and again in
    `_import_rad`, and the two agreed only by coincidence -- this module's bar everywhere else is
    one spelling per question (`_refuse_non_regular`, `_staging_path`, `_same_file`,
    `omitted_faces`), because a hand-written second spelling let a defect return at a new site twice
    on this branch. `_removed` derives the same id from `name` alone, and a change here that did not
    reach it would leave the writer and the remover disagreeing about which file a name owns --
    which is exactly what `_staging_path`'s own docstring records happening once already.
    """
    return _INPUT_PREFIX + (name or role or "scene")


def _within_roots(path: Path, roots: tuple[Path, ...]) -> bool:
    """`contain`'s verdict as a predicate, with no second definition of "inside a root".

    `contain` raises and returns the resolved path; here the question is asked about a file this
    call did not receive as an argument, so what is wanted is the boolean and one refusal naming
    every offender rather than one exception per include. Asking through `contain` rather than
    re-deriving `.resolve()` and the `in .parents` test is the point -- the same discipline
    `rad_mcp.paths.contain_octree_destination` follows for the same reason.
    """
    try:
        contain(path, roots)
    except (PathOutsideRoot, RootsNotConfigured):
        return False
    return True


def _refuse_server_written_input(
    loaded: LoadedProject, contained: Sequence[Path]
) -> CallToolResult | None:
    """No file this server wrote into `01_geometry/` may be handed back in as an import.

    **Fix round 1, Critical 1: a silent wrong answer with no detector.** The destination a scaled
    import writes is `01_geometry/<input_id>.rad`, derived from `name`/`role` alone; nothing compared
    it against `files`. The `others` check in `_write_geometry_file` excludes only *other* records'
    files, `_refuse_stale_octree` compares only the materials note, and `resolved_parent_within` and
    both `_refuse_non_regular` calls all pass on a perfectly ordinary regular file. So a caller who
    passed the destination as the source got it overwritten by its own scaled output -- and repeating
    the identical call rescaled the already-scaled file. Measured: two identical `units="ft"` calls
    naming the destination in `files` stored a bounding extent of 1.8288 m and then 0.55741824 m,
    which is 1.8288 x 0.3048, with `# xform -c -s 0.3048` in the stored header twice.
    `register_input` then re-digested, so `recompute_digests` and `is_reusable` both stayed clean over
    a building a third of its real size. The precondition is not exotic: `geometry(action="list")`
    and the record's own `files` publish exactly that path after any scaled import.

    **Compared by file identity, not by path form, and fix round 2 is why.** The first version tested
    `directory in candidate.parents` over `resolve()`d paths, which closes exactly one spelling -- the
    one the tool itself publishes -- and nothing else. Measured on this filesystem (case-insensitive,
    APFS):

        canon.resolve() == upper.resolve()   False   <- resolve() does not case-fold
        canon.samefile(upper)                True    <- one file
        directory in upper.resolve().parents False   <- the path-form test cannot see it

    so `01_GEOMETRY/geometry_scene.rad` walked straight through, and so did `os.link` to the
    destination from anywhere in the roots -- both reproduced end to end, both corrupting the stored
    file and its digest together. `(st_dev, st_ino)` is what the filesystem itself calls one file, so
    it is blind to neither.

    Two comparisons, because the two bypasses are different shapes:

    1. **Every ancestor of the candidate against the directory's own inode.** A directory cannot be
       hard-linked, so the only way an ancestor shares `01_geometry`'s inode is by *being* it under
       some other spelling -- a case variant, a trailing-dot form, a symlinked path component. This
       subsumes the canonical case and needs no string comparison at all.
    2. **The candidate against the inode of every non-symlink entry in the directory.** A hard link
       names one file from a second directory, where no ancestor test can reach it; `st_ino` is the
       only thing that sees through it. Read once per call, before the loop, because the directory
       does not change while `_dispatch` is deciding. A **case variant never reaches this check** --
       it always has a matching ancestor and is answered by 1 -- which is why the refusal here names
       a hard link and nothing else (fix round 3, finding 3).

    **Deliberately local to this guard.** `rad_mcp.paths.contain` and the `others` check compare
    resolved path strings and are case-blind in the same way; that is the containment boundary of
    every tool in this server, and the owner ruled that rewriting it is not this task's call. It is
    recorded for the whole-branch review with the measurement above.

    **Scoped to the directory, not to this call's own destination**, so it holds for every `name` at
    once: `01_geometry/geometry_other.rad` is refused too, where the `others` check would catch it
    only if it happened to be registered. It also needs no `input_id`, so it does not have to reach
    for `_input_id` at all -- which is now the one spelling of that derivation (Minor 1 of the
    whole-branch review split the two copies that used to be inline here and in `_import_rad`).

    **Refused on `check` as well as on `add`, and that is one rule rather than an arm.** A `check`
    writes nothing and so cannot destroy anything -- but `01_geometry/` is the directory this server
    writes its own copies into, so a `check` there asks about this server's own output rather than
    about geometry being imported. A guard whose one arm fires only on `add` is the shape that lets a
    later task move a write under the other arm; see the report's ruling candidate.

    Placed in `_dispatch` immediately after `contain` and **before** `discover()` and before
    `_read_obj_input` opens anything, which is where this module already states path validation
    belongs: the refusal is then answerable with no Radiance install and provable to have read and
    spawned nothing. The alternative -- beside the `others` check inside `_write_geometry_file` --
    would refuse only after a converter had run and an octree had been compiled.
    """
    directory = ProjectPaths(loaded.paths.root).geometry.resolve()
    owned = _identity(directory)
    stored: dict[tuple[int, int], Path] = {}
    try:
        entries = sorted(directory.iterdir())
    except OSError:
        entries = []
    for entry in entries:
        # **A symlinked entry lends no identity, and that is fix round 3's finding 2.** `_identity`
        # follows symlinks, so a link planted in `01_geometry/` put *its target's* inode into this
        # map -- and importing the caller's own unrelated file was then refused as "the same file as
        # .../01_geometry/link.rad". No data loss, but a false refusal of a file the caller owns,
        # which is the failure class this project ranks second only to a silent wrong answer.
        #
        # Skipped rather than recorded under `lstat`'s own inode: this map answers "which files did
        # this server write into its own directory", and a symlink is neither -- it is a pointer to
        # someone else's file, and the link's own inode can never be a candidate here because
        # `rad_mcp.paths.contain` resolves every path argument before this guard sees it.
        if not entry.is_symlink() and (identity := _identity(entry)) is not None:
            stored.setdefault(identity, entry)

    for candidate in contained:
        if owned is not None and any(
            _identity(ancestor) == owned for ancestor in (candidate, *candidate.parents)
        ):
            return _refuse_argument(
                f"{candidate} is inside this project's own {directory}, the directory this server "
                "writes its own metre copies of imported geometry into. Importing it would overwrite "
                "the file the manifest's digest describes -- and on a non-metre import it would scale "
                "an already-scaled scene a second time, with nothing able to detect it. Pass the "
                "geometry you own, or use geometry(action='list') to see what is registered.",
                field="files",
            )
        identity = _identity(candidate)
        if identity is not None and (match := stored.get(identity)) is not None:
            return _refuse_argument(
                f"{candidate} is the same file as {match}, inside this project's own {directory} -- "
                "a hard link names one file from two directories, and the filesystem sees one inode. "
                "Importing it would overwrite the file the manifest's digest describes -- "
                "and on a non-metre import it would scale an already-scaled scene a second time, "
                "with nothing able to detect it. Pass the geometry you own, or use "
                "geometry(action='list') to see what is registered.",
                field="files",
            )
    return None


def _refuse_unhonoured(*, transform: str | None) -> CallToolResult | None:
    """The one argument docs/tool-surface.md §4 publishes and this server still does not honour.

    Refused when given rather than accepted and ignored. `transform` is an `xform` argument string
    applied on import: a caller who passes `transform="-s 0.001"` and gets a scene at its original
    scale has been given a *different building* with no error anywhere, which is the failure this
    whole project is built against. An argument the server does not honour has to say so.

    It stays on the signature rather than being deleted from it, because the signature is the
    published one and a tool whose schema silently loses a documented argument is harder to reason
    about than one that refuses it and says why.

    **`material_map` left this function in Phase 10, and `triangulate` left it in Task 13.** Both are
    honoured on an `.obj` -- `triangulate` is `robjutil +T`, and `_import_obj` runs it. With a `.rad`
    each is still refused on its own field, by the `.obj`-only rule in `_dispatch`: an OBJ-converter
    operation has nothing to do to a scene `oconv` reads directly. That is the same answer, from the
    one rule that now owns it rather than from a second copy here. `transform` is the remaining
    member because nothing anywhere applies it, on either path.

    A single-member loop rather than a straight `if`: the loop body is the refusal's wording, and
    `material_map` and `triangulate` each left this function by deleting a row from the dict above.
    The next one leaves the same way.
    """
    given = {
        "transform": transform,
    }
    for field, value in given.items():
        if value is None:
            continue
        return _refuse_argument(
            f"{field}={value!r} is not honoured by this server. It is an `xform` argument string that "
            "would be applied on import (docs/tool-surface.md#4-geometry), and nothing here applies "
            "one -- on either import path. Accepting it silently would import geometry at a scale "
            "the caller did not get, with no error anywhere. Apply the transform outside the server "
            "and pass the resulting .rad.",
            field=field,
        )
    return None


#: The longest `name` this tool accepts. **Derived, not chosen**: `name` becomes the input id
#: `geometry_<name>`, which becomes the file `01_geometry/geometry_<name>.rad` through
#: `ProjectPaths.geometry_file`, which puts it through `rad_engine.project._component` -- and that
#: refuses a component over 64 characters. `len("geometry_") + 51 + len(".rad") == 64`, so 51 is the
#: largest `name` whose file can exist, not a cap this module picked (design §4). The same
#: arithmetic already bounded today's octree filename `geometry_<name>.oct`, which fails above 51
#: too; this states the bound where the caller is refused instead of letting the failure surface as
#: a project-layout error several calls later.
#:
#: **Not an entry in docs/tool-surface.md's `Unsourced values`**, and `rad_mcp.names.NAME_PATTERN`
#: is the precedent: its own 64-character bound is not in that table either, because a bound read
#: off a rule that already exists is a derivation, not a default someone substituted for a
#: measurement. What would change this number is `_component`'s 64 or the `geometry_`/`.rad`
#: spelling changing -- both of them facts in this repository, neither of them a survey.
NAME_MAX_LENGTH: Final = 51

#: The health checks whose verdict rests on an **absolute** tolerance in metres, and which therefore
#: cannot be run over coordinates whose unit nobody declared (design §5.10). Named here in one place
#: because two things read the list: `_health`, which skips them, and the result's `skipped` block,
#: which reports each one by this exact name. Spelled in the register's lower-case form of each
#: code, so a client can map a skipped check back to the advisory it did not get.
_ABSOLUTE_CHECKS: Final = (
    "coincident_surfaces", "non_planar_face", "window_without_opening",
    "degenerate_polygons", "geometry_scale_suspect", "octree_resolution_low",
    "coordinates_far_from_origin",
)


class IncludeOutsideRootError(GeometryError):
    """An `!xform` include of a `.rad` import resolves outside every allowed root.

    **Whole-branch review Important 5.** `contain` judges only the top-level `files`. The R33
    recursion resolves and reads the includes, `xform` splices them, and Task 6's `flatten_scene`
    then makes the result *storable*: the spliced text is written as this server's own
    `01_geometry/<id>.rad`, and `file_refs` digests every include into `sources`.

    **Measured:** an out-of-root file's content read, **copied into `01_geometry/`**, its modifier
    names and bbox published in the result, and its absolute path plus sha256 written into
    `manifest.json` and re-published by `geometry(action="list")`.

    **Provenance, split honestly.** The *read* half is pre-existing and known --
    `rad_engine.octree._refuse_inline_commands` records it and leaves it as the owner's call, and
    that layer deliberately has no roots to check against. What is new on this branch is that the
    content now lands **inside the project as a stored copy** and the out-of-root path and digest
    now reach the manifest and the wire. That half is unambiguously this branch's to close.

    Closed by refusing the import rather than by dropping the include from `sources`: an import
    whose scene silently omitted part of itself would be a wrong number reported as success, which
    is worse than a refusal the caller can act on. It is refused on **every** `.rad` import, not
    only the scaled one that records `sources`, because the out-of-root content reaches `oconv` --
    and therefore the published `bbox`, `modifiers` and `checks` -- on both.

    `field="files"` rather than `"root"`: the include was reached through the caller's `files`
    argument, and moving the include (or widening `RAD_MCP_ROOTS`) is the repair.
    """

    code = "PATH_OUTSIDE_ROOT"
    severity = "error"
    field = "files"


class _ArgumentRefused(GeometryError):
    """An argument refused on its face, raised from inside the read-and-parse step.

    A refusal rather than a returned `CallToolResult` because `_read_obj_input` sits below the
    function that builds envelopes and has three sources of the same verdict to unify -- the OBJ
    parser, the material-map parser and this module's own agreement rules, two of which already
    raise. `GeometryError` puts it in `geometry`'s existing `except` tuple, so it reaches the wire
    through `_refuse_assembly` with the code its class carries and the `field` its instance does.
    """

    code = "ARGUMENT_OUT_OF_RANGE"

    def __init__(self, message: str, *, field: str) -> None:
        super().__init__(message)
        self.field = field


def _refuse_name(name: str | None) -> CallToolResult | None:
    """`name` against the one naming rule and the 51-character bound above. `None` when it is fine.

    `rad_mcp.names.NAME_PATTERN` rather than a fourth copy of the regex -- see that module for why
    one rule is shared. The length is checked separately because the pattern's own bound is 64 and
    this tool's file needs a tighter one.
    """
    if name is None or (NAME_PATTERN.match(name) and len(name) <= NAME_MAX_LENGTH):
        return None
    return _refuse_argument(
        f"name={name!r} must match {NAME_PATTERN.pattern} and be at most {NAME_MAX_LENGTH} characters: "
        "it becomes the file 01_geometry/geometry_<name>.rad.",
        field="name",
    )


def _agree(field: str, argument: str | None, from_map: str | None) -> str | None:
    """One value from the argument and the material map, or a refusal that they disagree.

    Neither wins. A precedence rule would mean a caller who passes `units="ft"` against a map that
    says `"m"` gets a building 3.28x the size they asked for, or 0.3x, depending on which side the
    rule happened to favour -- and no error anywhere either way. The only safe answer to two
    declarations of one fact is to make the caller settle it.
    """
    if argument is not None and from_map is not None and argument != from_map:
        raise _ArgumentRefused(
            f"{field}={argument!r} disagrees with the material map's {field} {from_map!r}. Pass one, or make them agree.",
            field=field,
        )
    return argument if argument is not None else from_map


def _capped(path: Path, limit: int, field: str) -> bytes:
    """`rad_engine.safe_io.read_capped`, with every way it can fail mapped onto `field`.

    The size is taken from the open descriptor before the read, so an oversized file is refused
    without being taken into memory -- which is the guard's point, and why the refusal states the
    size it measured rather than "too large".
    """
    try:
        return read_capped(path, limit)
    except FileTooLargeError as exc:
        raise _ArgumentRefused(f"{path} is {exc.size} bytes, over the {limit}-byte cap.", field=field) from None
    except FileNotFoundError:
        raise _ArgumentRefused(f"{path} does not exist.", field=field) from None
    except OSError as exc:
        raise _ArgumentRefused(f"{path} cannot be read as a regular file: {exc}.", field=field) from None


@dataclass(frozen=True)
class _ObjInput:
    """One `.obj` and its optional material map, read once and parsed, before any converter ran.

    `data` and `map_data` are the **only** copies of those bytes this call uses: every converter
    stage is fed from `data`, and the recorded source digests are taken over both. A second read of
    either path could see different content, and then the digest would describe a file nothing in
    this import ever parsed.
    """

    path: Path
    data: bytes
    model: ObjModel
    map_path: Path | None
    map_data: bytes | None
    mapping: MaterialMap | None
    units: Units | None
    up_axis: Literal["Z", "Y"]


def _read_obj_input(
    obj_path: Path, map_path: Path | None, *, units: Units | None, up_axis: str | None, action: str
) -> _ObjInput:
    """Design §5.2-5.3: each file read once, capped, parsed and validated. No converter has run."""
    data = _capped(obj_path, OBJ_MAX_BYTES, "files")
    map_data = _capped(map_path, MATERIAL_MAP_MAX_BYTES, "material_map") if map_path is not None else None
    model = parse_obj(data, mapped=map_path is not None)
    mapping = parse_material_map(map_data, source=str(map_path)) if map_data is not None else None
    resolved_units = _agree("units", units, mapping.units if mapping else None)
    resolved_axis = _agree("up_axis", up_axis, mapping.up_axis if mapping else None) or "Z"
    if action == "add" and resolved_units is None:
        raise _ArgumentRefused(
            "geometry(action='add') needs units, from the argument or the material map's 'units'. A model "
            "in feet read as metres is wrong by 3.28x with no error anywhere.",
            field="units",
        )
    if mapping is not None:
        mapping.refuse_unmapped(model.names)
    return _ObjInput(obj_path, data, model, map_path, map_data, mapping, resolved_units, resolved_axis)  # type: ignore[arg-type]


def _capability_refusal(capabilities: dict[str, bool]) -> CallToolResult | None:
    """`CAPABILITY_UNAVAILABLE` when this install cannot import geometry at all.

    *"Capability gating is a refusal at call time, not a hidden tool"*
    (docs/tool-surface.md#the-tool-list-is-static) -- the tool stays registered and says why it
    cannot run, so `radiance_status`'s report and this refusal name the same capability.

    The advisory is `warn` because that is the severity the register allocates
    `CAPABILITY_UNAVAILABLE` (docs/tool-surface.md#advisory-code-register, Install group), and
    `tests/test_advisories.py` checks every emission against it. The **call** is still an error:
    `is_error` describes the call and an advisory describes a value, and they are deliberately not
    derived from each other (`rad_mcp.envelope`).
    """
    if capabilities.get(_IMPORT_CAPABILITY, False):
        return None
    reason = (
        "this Radiance install cannot import geometry: the 'geometry_import' capability is "
        "unavailable, so obj2rad, robjutil or xform is missing. Every scene is flattened through "
        "xform before oconv sees it, so nothing can be imported without it. Run radiance_status to "
        "see which workflows this install supports, or pass bin_dir to point at a complete one."
    )
    return refuse(
        reason,
        [
            Advisory(
                code="CAPABILITY_UNAVAILABLE",
                severity="warn",
                field="files",
                message=reason,
            )
        ],
    )


# --- the tool -------------------------------------------------------------------------------


def geometry(
    action: Annotated[
        Action,
        Field(
            description=(
                "which operation: \"add\", \"check\", \"list\" or \"remove\". The tool description "
                "says what each one does."
            )
        ),
    ],
    project: Annotated[
        str | None, Field(description="the project handle. Required for every action.")
    ] = None,
    files: Annotated[
        list[str] | None,
        Field(
            description=(
                "absolute paths, required on \"add\" and \"check\". EITHER exactly one .obj -- "
                "imported alone, never alongside another file -- OR one or more Radiance scene "
                "files (.rad). glTF, gbXML, IFC and .rif are not imported, each refused with its "
                "own reason: no specification this server is built against sources glTF or gbXML "
                "and the Radiance install has no converter for either or for IFC, while a .rif is "
                "a control file naming scene files and render options rather than geometry."
            )
        ),
    ] = None,
    role: Annotated[
        Literal["interior", "exterior", "context", "shading", "aperture"] | None,
        Field(
            description=(
                "what this geometry IS in the scene -- interior, exterior, context, shading or "
                "aperture. The matrix methods need different octrees (the daylight matrix uses "
                "exterior context and no room interior; the view matrix uses the interior with "
                "the aperture as a glow source), and this is how they are assembled without asking "
                "you to think about octrees. It is NOT a material role: it never says which "
                "surface is the floor or the ground -- that is "
                "materials(assignments={...: {\"role\": ...}})."
            )
        ),
    ] = None,
    units: Annotated[
        Units | None,
        Field(
            description=(
                "what the file's coordinates are in. REQUIRED on \"add\", from this argument or "
                "from the material map's \"units\" -- a model in feet read as metres is wrong by "
                "3.28x with no error anywhere. Optional on \"check\", which registers nothing: "
                "without it the scale is unknown, so every check with an absolute tolerance is "
                "skipped and listed in \"skipped\" rather than answered. Given on both sides, the "
                "two must agree."
            )
        ),
    ] = None,
    material_map: Annotated[
        str | None,
        Field(
            description=(
                "absolute path to a JSON material map beside an .obj -- one row per usemtl/group "
                "name, setting that material's values through the same code path "
                "materials(action= \"set\") takes. .obj only; passing it with a .rad is refused."
            )
        ),
    ] = None,
    up_axis: Annotated[
        Literal["Z", "Y"] | None,
        Field(
            description=(
                "\"Z\" or \"Y\" -- which axis the model calls up. Taken from this argument, else "
                "the material map's \"up_axis\", else Z. .obj only; passing it with a .rad is "
                "refused."
            )
        ),
    ] = None,
    transform: Annotated[
        str | None,
        Field(
            description=(
                "an `xform` argument string to apply on import. **Not built here**, on either "
                "path, and passing it is refused rather than ignored -- geometry imported at a "
                "scale you did not ask for is a different building with no error anywhere."
            )
        ),
    ] = None,
    triangulate: Annotated[
        bool,
        Field(
            description=(
                "run robjutil +T over the .obj before converting it, and register the "
                "triangulated scene. The checks and the validation octree run on what is "
                "registered; the un-triangulated scene's findings are reported too, each prefixed "
                "\"before triangulation: \", because +T can delete a duplicate face and take a "
                "finding with it. Use action=\"check\" to see both lists without committing. "
                "Compare the two lists before acting on either. .obj only; passing it with a .rad "
                "is refused."
            )
        ),
    ] = False,
    name: Annotated[
        str | None,
        Field(
            description=(
                "the input's handle inside the project, at most 51 characters of the usual name "
                "charset (it becomes 01_geometry/geometry_<name>.rad). Defaults to the role, so an "
                "interior scene and a context scene do not overwrite one another."
            )
        ),
    ] = None,
    confirm: Annotated[
        bool,
        Field(
            description=(
                "REQUIRED true on \"remove\". It defaults to false and is never derived from the "
                "other arguments -- it exists so a human is in the loop before a file is deleted."
            )
        ),
    ] = False,
) -> GeometryResult:
    """Get real geometry in and prove it is not broken.

    Use it after `project(action="create")` and before `window_group`, which names apertures
    among the polygons registered here, and before any run that traces the scene.

    This is the tool that stands between you and the two silent octree failures: `oconv` exits 0
    when every `!xform` include fails, writing a well-formed nearly-empty octree, and it exits 0
    again having quietly dropped one input of eight. Every import here is validated by building a
    frozen octree and reading its own bounding cube.

    "add" imports and registers geometry. "check" does every check and registers nothing, which is
    the only way past a refused import. "list" reports what is registered. "remove" deregisters one
    input by name.

    DESTRUCTIVE ACTIONS: `add` of an .obj with a material_map replaces the values of the modifiers
    that map names in the project's set materials file, and may withdraw them from the LM-83 defaults
    file; every `add` that scales or converts overwrites this server's own 01_geometry/<id>.rad.
    `remove` deregisters a geometry input from the project **and deletes the file this server wrote
    for it**: 01_geometry/<id>.rad, plus a 01_geometry/<id>.rad.partial left there by an interrupted
    import. Neither ever deletes or changes a file you supplied -- an input registered in place stays
    exactly where it is, and so does every file an import was converted from, **including an !xform
    include the flatten pulled in**, which is refused as a destination the same way a top-level file
    is -- and a path inside the project's own 01_geometry/ is refused as an import rather than
    written over, so the only file any action here can delete or change is one this server wrote
    itself. `remove` additionally requires `confirm=true`, as `project(action="delete")` does.

    **Under `triangulate=true`, each finding of the un-triangulated scene is reported with the
    prefix "before triangulation: ".** **A prefixed error the registered (triangulated) scene does
    not repeat is reported at warn and does not refuse the add**; it says so, and it may still be a
    real defect in the file you handed in. The coplanar-overlap check compares face extents with a
    10 µm slack, so a wall/ceiling thickness return it excuses as a quad is still excused once split.
    """
    runtime = current()
    try:
        return _dispatch(
            runtime,
            action,
            project=project,
            files=files,
            role=role,
            units=units,
            material_map=material_map,
            up_axis=up_axis,
            transform=transform,
            triangulate=triangulate,
            name=name,
            confirm=confirm,
        )
    except RadianceTimeoutError as exc:
        # **Above the `RadianceError` clause below, which it subclasses.** Ordering is load-bearing
        # in exactly the way `annual_tool` records for its own copy of this bound: caught there, the
        # expiry is reported as an ordinary Radiance failure with no mention of a timeout, which is
        # how this tool behaved until 2026-08-10.
        #
        # `OCTREE_BUILD_TIMEOUT` was allocated 2026-08-09 by ruling R56 for `run_annual`, and the
        # Phase 4 close recorded that `geometry` carried *the same 300 s bound* without emitting it.
        # This closes that: two tools compile an octree under one bound, and one code names the
        # expiry for both. The alternative -- a second code meaning the same thing in a different
        # tool -- is what the register's "allocated once, never renamed" rule exists to prevent.
        #
        # `field="project"` matches `run_annual`'s spelling of the same refusal. It is **retryable
        # as-is** with nothing for the caller to repair, which is precisely what distinguishes it
        # from the symlink and file-type refusals nearby -- the overload ruling R58 separates.
        logger.info("geometry(action=%s) refused: %s", action, exc)
        message = (
            f"compiling the octree for this geometry exceeded its {_ASSEMBLY_TIMEOUT_S:g} s bound "
            "and was terminated. An octree build is seconds for normal geometry, so retry once -- "
            "and if it expires again the geometry is the problem, not the moment: either "
            "pathological geometry (hundreds of triangles at a shared vertex -- see "
            "OCTREE_SET_OVERFLOW), a non-terminating !xform chain, or a scene file that is a pipe "
            "or device rather than a regular file, which oconv blocks on. Nothing has been "
            "registered."
        )
        return refuse(
            message,
            [
                Advisory(
                    code="OCTREE_BUILD_TIMEOUT",
                    severity="error",
                    field="project",
                    message=message,
                )
            ],
        )
    except (
        SceneAssemblyError,
        UndefinedModifierError,
        OctreeSetOverflowError,
        OctreeTooManyFilesError,
        SceneFileNotFoundError,
        # Ruling R33: a scene file carrying an inline `!` command. Coded
        # `SCENE_INLINE_COMMAND`, mapped by the same `getattr` every other refusal here uses.
        SceneInlineCommandError,
        MaterialsEmissionError,
        # `GeometryError` covers `rad_core`'s whole family, which Phase 10 widened: `ObjError`,
        # `ObjFacesWithoutMaterialError`, `NonFiniteCoordinatesError`, `MaterialMapError`,
        # `MaterialUnmappedError` and this module's own `_ArgumentRefused` all descend from it and
        # all carry their own `code`/`field`, so `_refuse_assembly`'s `getattr` shapes each one.
        GeometryError,
        # A converter stage that failed, timed out, or produced a scene that does not account for
        # every kept OBJ face (`GEOMETRY_CONVERSION_FAILED`), and a scratch directory whose parent
        # chain resolves outside every root (`PATH_OUTSIDE_ROOT`). Both are `rad_engine.convert`'s.
        GeometryConversionError,
        ScratchOutsideRootsError,
        RadianceError,
        # A materials input whose recorded assignment no longer validates. Refused rather than
        # worked around: `known_modifiers` and the per-file modifier sets
        # `scene_files_with_materials` compares both come from replaying those assignments, so
        # proceeding with a set narrowed by a dropped input would import geometry against a
        # materials picture the server could not actually read (fix round 2, finding I3).
        #
        # **All three of the shapes that replay can raise.** `MaterialsEmissionError` is
        # `rad_engine`'s (primitive syntax); `MaterialsError` is `rad_core`'s (ranges, energy
        # balance, roles) and was missing here, so a recorded reflectance the current code refuses
        # escaped the tool function uncaught -- a JSON-RPC error with no payload, which is the
        # check-0.4 regression class. Found by the I3 test, not by reading this list.
        MaterialsError,
        AssignmentRefused,
        # Deregistering an input a sealed artifact is built from. Carries
        # `ARTIFACT_EDGES_INCOMPLETE`, mapped by the same `getattr` every other refusal here goes
        # through.
        InputStillBuiltFrom,
        # The project's materials moved between the validation octree and the commit, so the octree
        # this import was checked against is stale. Raised *before* anything is written, which is
        # what makes "Nothing has been written" in its own message true (`_commit_obj`).
        MaterialsChangedDuringImportError,
        # `oconv` compiled a scene materially larger than the polygon soup can account for --
        # whole-branch review Critical 4's outside check. Raised before anything is registered.
        OctreeUnexplainedError,
        # The rename replaced the stored copy and the manifest write then failed. **Above the
        # `except OSError` clause below by being in this tuple at all**, which is the point: that
        # clause's generic sentence is true and says nothing about the swap (Important 2).
        StoredGeometryReplacedError,
    ) as exc:
        logger.info("geometry(action=%s) refused: %s", action, exc)
        return _refuse_assembly(exc)
    except RadianceNotFound as exc:
        logger.info("geometry(action=%s) refused: %s", action, exc)
        return refuse(
            str(exc),
            [
                Advisory(
                    code="RADIANCE_NOT_FOUND",
                    severity="error",
                    message=(
                        f"No usable Radiance installation was found, so no octree can be built and "
                        f"no import can be validated: {exc}"
                    ),
                )
            ],
        )
    except (ProjectError, PathOutsideRoot, RootsNotConfigured) as exc:
        logger.info("geometry(action=%s) refused: %s", action, exc)
        return refuse_engine_error(exc)
    except NotARegularFileError as exc:
        # **Both a read and a write reach here, and the message says so.** Two paths raise this:
        # `_commit_obj`'s guard on the `01_geometry/<id>.rad` destination (a FIFO, a device, a
        # directory, or a symlink at the final component), and `rad_engine.scene.read_scene` ->
        # `read_scene_text` -> `open_regular_file` on a `.rad` in `files` that is not a regular file
        # -- which `rad_engine.scene` deliberately lets escape for this boundary to shape. Measured
        # with `os.mkfifo` (Task 10 fix round 1): before this wording a FIFO `.rad` was refused with
        # "the imported geometry could not be stored ... Nothing has been written", which is a
        # write-flavoured sentence about a read that never attempted one. The OBJ and material-map
        # reads do **not** reach here: `_capped` turns their `OSError` into `_ArgumentRefused`
        # (`ARGUMENT_OUT_OF_RANGE`, `field="files"`/`"material_map"`) before this clause can see it.
        # Coded
        # `PROJECT_PATH_NOT_A_REGULAR_FILE` under ruling R58, and this is the first time
        # `geometry` emits it.
        # **Neither a list nor a count of the other tools that carry it is written here.**
        # `grep -rln PROJECT_PATH_NOT_A_REGULAR_FILE src/` is the measurement; the sentence in
        # docs/tool-surface.md that did enumerate them went stale the moment this task added an
        # emitter, which is why it was replaced rather than extended.
        #
        # **Above the `OSError` clause, which it subclasses.** Below it this would be swallowed into
        # the generic filesystem refusal and lose its code -- the same ordering hazard the
        # `RadianceTimeoutError` clause above documents.
        logger.info("geometry(action=%s) refused: %s", action, exc)
        # **The exception's own repair sentence is not repeated here, and not overridden either.**
        # Each raiser states the repair for its own case -- `_commit_obj` re-words the destination
        # guard in this tool's vocabulary (see there; `safe_io`'s copy names `out`, which is
        # `run_glare`'s and `image`'s argument and does not exist on `geometry`), and
        # `open_regular_file` says "Point it at a file on disk" for a read. A second instruction in
        # the wrapper's own words told the caller the same thing twice, in one case impossibly.
        message = (
            f"the geometry could not be read or stored: {exc} Nothing has been written and nothing "
            "has been registered."
        )
        return refuse(
            message,
            [
                Advisory(
                    code="PROJECT_PATH_NOT_A_REGULAR_FILE",
                    severity="error",
                    field="project",
                    message=message,
                )
            ],
        )
    except OSError as exc:
        logger.warning("geometry(action=%s) failed on the filesystem: %s", action, exc)
        return refuse(
            f"geometry(action={action!r}) could not complete: {exc}. Nothing about the arguments is "
            "wrong -- the filesystem refused the operation."
        )


def _refuse_assembly(exc: Exception) -> CallToolResult:
    """One refusal envelope for every way an import can be proven not to be the scene requested.

    `SceneAssemblyError` is the one exception in this codebase that carries **several** advisories
    rather than one code: ruling R5 defines `OCTREE_EMPTY` and `OCTREE_INPUT_DROPPED` as two
    independently provable claims, and a run can satisfy both. They are relayed in the order the
    engine built them -- `OCTREE_EMPTY` first, then one `OCTREE_INPUT_DROPPED` per dropped input,
    never one per stderr line.

    Everything else carries a class-level `code`/`severity`/`field` in the shape `ProjectError`
    established, or none at all, in which case the reason is prose -- a gap is reported, not filled.
    `RadianceError` is the last of those: it is *"a Radiance binary failed"* with the binary's own
    stderr attached, which covers conditions the register has no code for, and the backstop matters
    more than the code -- an exception escaping a tool function is a JSON-RPC error, and check 0.4
    requires a refusal in the tool-result channel where the model can act on it.
    """
    if isinstance(exc, SceneAssemblyError):
        return refuse(str(exc), to_wire_all(exc.advisories))
    if isinstance(exc, UndefinedModifierError):
        return refuse(
            str(exc),
            [
                Advisory(
                    code="UNDEFINED_MODIFIER",
                    severity="error",
                    field="files",
                    message=str(exc),
                )
            ],
        )
    code = getattr(exc, "code", None)
    if code is None:
        return refuse(str(exc))
    return refuse(
        str(exc),
        [
            Advisory(
                code=code,
                severity=getattr(exc, "severity", "error"),
                field=getattr(exc, "field", None),
                message=str(exc),
            )
        ],
    )


def _dispatch(
    runtime: Runtime,
    action: Action,
    *,
    project: str | None,
    files: list[str] | None,
    role: str | None,
    units: Units | None,
    material_map: str | None,
    up_axis: Literal["Z", "Y"] | None,
    transform: str | None,
    triangulate: bool,
    name: str | None,
    confirm: bool,
) -> CallToolResult:
    if (refusal := refuse_missing(f"geometry(action={action!r})", project=project)) is not None:
        return refusal
    # `refuse_missing` returns `None` only when every argument it was given is non-`None`, which no
    # checker can see through the `**arguments` it collects them with. Restated here so the guarantee
    # is local to the call that relies on it.
    assert project is not None

    if action == "remove":
        if (refusal := refuse_missing(f"geometry(action={action!r})", name=name)) is not None:
            return refusal
        # **Ruling R451 (the owner), reversing R424: `remove` requires `confirm`, checked BEFORE the
        # project is opened** -- R59's order for `project(action="delete")`, for R59's reason: a
        # lookup is work, and an unconfirmed destructive call must not be usable as an existence
        # probe. The message names the caller's own handles, never a resolved path.
        if not confirm:
            reason = (
                f"geometry(action='remove') of {name!r} in project {project!r} requires "
                "confirm=true. It deregisters the input and deletes the file this server wrote for "
                "it. Nothing else about this call implies that consent."
            )
            return refuse(
                reason,
                [Advisory(code="ARGUMENT_OUT_OF_RANGE", severity="error", field="confirm", message=reason)],
            )

    loaded = runtime.open(project)

    if action == "list":
        return _listed(runtime, loaded)
    if action == "remove":
        assert name is not None
        return _removed(runtime, loaded, name=name)

    # Computed before `refuse_missing` because it decides what `refuse_missing` is given: `units`
    # is required on `add` for a `.rad`, and for an `.obj` it may come from the material map
    # instead, which only `_read_obj_input` can see. `files or []` so a missing `files` still
    # refuses on `files` below rather than raising here.
    obj_files = [f for f in (files or []) if Path(f).suffix.lower() == ".obj"]

    required: dict[str, Any] = {"files": files}
    if action == "add" and not obj_files:
        # `units` is required on `add` and deliberately not on `check`: `check` registers nothing,
        # so there is no record for a wrong unit to become permanent in, and refusing a diagnostic
        # call for a missing declaration would make the one action that can inspect a broken scene
        # harder to reach than the one that imports it.
        required["units"] = units
    if (refusal := refuse_missing(f"geometry(action={action!r})", **required)) is not None:
        return refusal
    if not files:
        return _refuse_argument(
            f"geometry(action={action!r}) was given an empty files list, so there is nothing to "
            "import. An input with no files contributes a constant to every digest downstream of it.",
            field="files",
        )
    if (refusal := _refuse_format(files)) is not None:
        return refusal
    if (refusal := _refuse_unhonoured(transform=transform)) is not None:
        return refusal
    if (refusal := _refuse_name(name)) is not None:
        return refusal

    # **One `.obj` per call, never with another file.** An OBJ is converted, scaled and stored as a
    # single server-written `.rad` under one input id (design §4); a call carrying two of them, or
    # one alongside a hand-written `.rad`, has no single answer for what that file is. Refused
    # rather than resolved by a rule the caller would have to know.
    if obj_files and len(files) != 1:
        return _refuse_argument(
            "an .obj is imported alone: one .obj per call, never with another file (design §4).", field="files"
        )
    if not obj_files:
        # `triangulate or None` so `False` -- the default -- is not a value the loop refuses.
        # `robjutil +T` is an OBJ-converter stage (`rad_engine.convert.triangulate_obj`) and there is
        # no `.rad` equivalent, so it joins the two arguments that were already `.obj`-only rather
        # than staying in `_refuse_unhonoured`, which now means *nothing honours this anywhere*.
        for field, value in (
            ("material_map", material_map), ("up_axis", up_axis), ("triangulate", triangulate or None),
        ):
            if value is not None:
                return _refuse_argument(f"{field} applies to an .obj import only.", field=field)

    # **Containment before discovery, and the order is not cosmetic.** *"Servers MUST: Validate all
    # tool inputs"* (docs/tool-surface.md#input-validation-and-the-path-boundary), and a path
    # argument is the untrusted input this whole boundary exists for -- deciding it before the server
    # goes looking for an install means a path outside every root is refused for the reason it is
    # actually wrong, on every machine, rather than shadowed by `RADIANCE_NOT_FOUND` on one that has
    # no Radiance. It also keeps the refusal answerable with no install at all, which is what lets
    # its test run in the no-Radiance suite.
    contained = [contain(candidate, runtime.roots) for candidate in files]

    if (refusal := _refuse_server_written_input(loaded, contained)) is not None:
        return refusal

    # **Read and parse before `discover()`, and this is the security property, not a style.**
    # `rad_core.obj` and `rad_core.material_map` are pure, so every rule they enforce -- a `!` at
    # the head of a name, a NUL, a trailing backslash, a nine-name `g` line, an unmapped material --
    # is decided here, with no install, no subprocess and nothing written. That is what lets the
    # hostile-input tests assert `no_converter == []` and mean it: there is no path from a refused
    # OBJ to a spawn, because the spawn is downstream of a step the refusal never leaves. Containment
    # above is likewise before the read, so nothing outside a root is ever opened.
    obj: _ObjInput | None = None
    if obj_files:
        map_path = contain(material_map, runtime.roots) if material_map else None
        obj = _read_obj_input(contained[0], map_path, units=units, up_axis=up_axis, action=action)

    env = discover()
    if (refusal := _capability_refusal(env.capabilities())) is not None:
        return refusal

    # `list` and `remove` returned above, so only `add` and `check` reach here. Asserted rather than
    # cast: if a fifth action is ever added to `Action` and falls through to here, this fails loudly
    # at the boundary instead of registering an input under an action `GeometryAssembled` cannot type.
    assert action in ("add", "check")
    if obj is not None:
        return _import_obj(
            runtime, loaded, env, action=action, obj=obj, role=role, name=name, triangulate=triangulate
        )
    # **`units=units`, never `units or "m"`.** `add` already required it above; on a `check` a
    # missing declaration is the *finding* -- factor 1, every absolute check skipped and listed --
    # and substituting metres here would answer six questions over coordinates nobody scaled.
    return _import_rad(
        runtime,
        loaded,
        env,
        action=action,
        files=contained,
        role=role,
        units=units,
        name=name,
    )


def _materials_files(loaded: LoadedProject) -> list[MaterialsFile]:
    """Every materials input this project has, as `rad_engine.materials.MaterialsFile`.

    The modifiers are re-derived through `rad_mcp.materials_tool.replay_materials` from the manifest's
    own record of the call that registered each input, which is the same route `materials(action=
    "list")` takes. Two consequences, both wanted: the set handed to `oconv` as `known_modifiers`
    is exactly the set the materials file defines, and `scene_files_with_materials` gets the per-file
    modifier sets it needs to refuse the same modifier defined twice across two files.
    """
    return [
        MaterialsFile(
            path=Path(ref.path),
            modifiers=frozenset(m.modifier for m in replay_materials(record)),
        )
        for record in loaded.manifest.inputs
        if record.kind == "materials"
        for ref in record.files
    ]


def _ground_modifiers(loaded: LoadedProject) -> frozenset[str]:
    """Modifiers the project's materials tagged `MaterialRole.GROUND`.

    Forwarded to `assemble_scene(ground_modifiers=...)`, which forwards it to
    `rad_core.geometry.enclosure_warning`: `NOT_ENCLOSED` is scoped to *the interior polygon soup*,
    and every real fixture's ground plane has a genuine free perimeter that says nothing about
    whether the room is closed. This is `MaterialRole`, written by `materials` -- never
    `geometry(role=...)`, which is a different vocabulary (ruling R32, module docstring).
    """
    ground: set[str] = set()
    for record in loaded.manifest.inputs:
        if record.kind != "materials" or record.role != MaterialRole.GROUND.value:
            continue
        ground |= {
            material.modifier
            for material in replay_materials(record)
            if material.role is MaterialRole.GROUND
        }
    return frozenset(ground)


def _undefined_modifier_error(
    polygons: Sequence[Polygon], registered: set[str], failure: RadianceError
) -> Exception:
    """Diagnose an `oconv` failure, purely, and without reading a word of its stderr.

    `oconv` refusing a scene is the *evidence*; naming the cause has to come from somewhere this
    layer is allowed to look. `rad_core.geometry.inventory` run against the **registered** materials
    alone answers exactly one question -- does this scene use a modifier the project never
    registered? -- and if the answer is yes while `oconv` has just refused the file, then nothing in
    the whole `oconv` argument list defined it and `UNDEFINED_MODIFIER` is true of the run. That is
    the code's own definition: *"geometry references a material `oconv` had not read"*.

    Deliberately **not** matched against `oconv`'s message text. Which sentences Radiance writes is
    `rad_engine.advisories`' knowledge, and reading them here would put a Radiance string table in
    the protocol layer (CONTEXT.md, Layers). The pure check is a stronger source anyway: it names
    *which* modifiers, where the stderr line names one.

    When the pure check finds nothing, the `oconv` failure was something else -- a syntax error, a
    primitive this build rejects -- and the original `RadianceError` is returned unchanged, so the
    refusal carries Radiance's own words as prose rather than a code that would be untrue of the run
    (docs/tool-surface.md#advisory-code-register: a gap is reported, not filled).
    """
    try:
        inventory(polygons, registered)
    except UndefinedModifierError as undefined:
        return UndefinedModifierError(
            f"{undefined} oconv refused the scene: {failure}",
            undefined_modifiers=undefined.undefined_modifiers,
        )
    return failure


# --- the OBJ import -------------------------------------------------------------------------


def omitted_faces(model: ObjModel, luminaires: frozenset[str]) -> frozenset[int]:
    """Face numbers left out of the scene because every name they carry is a luminaire row.

    A luminaire row's faces are left out entirely (owner decision 2): a housing modelled as opaque
    geometry would shade the light it is supposed to emit, and `luminaire(action="place", geometry=...)`
    places lamps at them instead. *Every* name, so a face named by both a luminaire and a surface row
    stays in.

    **`and names` is the guard against a vacuous truth, and it is not decoration.** `set() <= anything`
    is `True`, so a face carrying no name at all would otherwise be reported as an omitted luminaire
    face. Today no such face can reach here, and only because of a coupling across two modules that
    nothing in this expression states: `rad_core.obj.parse_obj(data, mapped=True)` refuses a file
    whose faces come before the first `usemtl`, and refuses a face with neither `usemtl` nor group --
    which are the only two shapes `ObjModel.face_names` returns `()` for -- while `luminaires` is
    empty whenever there is no map, which is the only case `parse_obj` is called with `mapped=False`.
    Both halves have to hold, in two modules, for the unguarded form to be correct.

    The guard is kept rather than replaced by a comment because the two failure modes are not
    symmetric. Vacuously omitted, a nameless face is **silently dropped**: it is excluded from
    `account`'s expected set, so the rule file deleting it goes unnoticed and the scene quietly
    loses a surface, with `LUMINAIRE_FACES_OMITTED` overcounting into the bargain. Guarded, the same
    face is expected in the output, `rad_engine.convert.account` does not find it, and the import is
    refused with `GEOMETRY_CONVERSION_FAILED` naming the face. A loud refusal over a silent wrong
    answer is this project's whole posture.

    **Task 13 called this function a second time rather than editing it.** A triangulated import
    converts two models -- the caller's and `robjutil +T`'s -- and each needs its own omitted set, so
    the question is asked twice through `_conversion_plan` and answered here once. Re-spelling the
    comprehension at the second site is precisely how the vacuous-truth guard above would have been
    lost: it is one `and names` in one expression, and a copy of it is a copy that can drop it.

    `luminaires and` is a **short-circuit, not a second guard**: with no luminaire rows,
    `names <= luminaires` is already `False` for every non-empty name set, and the empty set is
    excluded by `and names`. It is kept because the mapless import is the common case and it skips
    building a set per face; it is documented as redundant so nobody reads correctness into it, and
    so a test is not written claiming to pin it (measured: removing it changes no result).

    Split out of `_import_obj` so the `and names` guard can be reached by a test at all: no input `parse_obj`
    accepts can produce the empty-name case, so the only way to show the assertion red is to hand
    this function an `ObjModel` directly (`tests/test_geometry_import_tool.py`).
    """
    return frozenset(
        face.number
        for face in model.faces
        if luminaires and (names := set(model.face_names(face))) and names <= luminaires
    )


def _conversion_plan(model: ObjModel, mapping: MaterialMap | None) -> tuple[frozenset[int], str | None]:
    """What `rad_engine.convert.convert_obj` needs to know about one model: which faces are not
    expected in its output, and the `obj2rad -m` rule file that names the rest.

    **One home for the pair, because a triangulated import asks it twice** -- once about the caller's
    OBJ and once about `robjutil +T`'s. The two answers genuinely differ (`robjutil` renumbers every
    face and may delete one), so both have to be computed; a second hand-written spelling of them is
    what this module has twice paid for (`_refuse_non_regular`, `_staging_path`). Here the stakes are
    `omitted_faces`'s `and names` guard, which is one clause of one comprehension.

    `rules` is `None` with no map: no rule file is written, so `obj2rad` names each face by the
    OBJ's own `usemtl`; `luminaires` is then empty and nothing is omitted.
    """
    luminaires = mapping.luminaire_names if mapping else frozenset()
    rules = (
        rules_text([n for n in model.names if n not in luminaires], qualifier=model.qualifier)
        if mapping else None
    )
    return omitted_faces(model, luminaires), rules


def _registered_glass(loaded: LoadedProject) -> set[str]:
    """Modifiers the project's own materials made `glass`.

    `windows_without_opening` needs to know which faces are glazing, and an OBJ whose map declares no
    `glass` row can still reference a `glass` modifier the project registered earlier. Re-derived
    through `replay_materials`, the one derivation every reader of the recorded assignments shares.
    """
    return {
        material.modifier
        for record in loaded.manifest.inputs if record.kind == "materials"
        for material in replay_materials(record) if material.material_type == "glass"
    }


def _scale_warning(polygons: Sequence[Polygon]) -> ArtifactWarning | None:
    """`GEOMETRY_SCALE_SUSPECT` over the soup's extents, or `None` when there are no extents.

    `rad_core.geometry.bounding_box` raises `GeometryError` on an empty soup -- correctly: a box
    over nothing is not a number, and the caller that needs "this scene is empty" as a refusal has
    the octree's own bounding cube, which is the authoritative check. Guarded here because an empty
    soup reaches this function on both import paths and is *not* this check's refusal to raise. It
    was measured on the `.rad` path: a scene whose only content was a failed `!xform` include
    flattened to nothing, and the plausibility check on its extents raised
    `bounding_box() needs at least one polygon` over the top of `OCTREE_EMPTY` and
    `OCTREE_INPUT_DROPPED`, so the refusal the caller saw named neither. The same shape reaches it
    from an `.obj` whose every face was a luminaire row.
    """
    return scale_warning(bounding_box(polygons)) if polygons else None


def _health(
    polygons: Sequence[Polygon], *, text: str, glass: set[str], context: frozenset[str], role: str | None, units_known: bool
) -> tuple[list[ArtifactWarning], list[SkippedView]]:
    """Design §7 on the metre soup; every check with an absolute tolerance skipped without units (§5.10).

    **Run on the polygons that actually reached Radiance**, not on the OBJ faces (decision 15):
    `obj2rad` splits a warped quad into triangles of its own accord, and a face this server called
    non-planar which `obj2rad` had already triangulated would be a warning about geometry no run
    will ever trace.

    `role == "context"` skips the window check rather than reporting it: surrounding buildings are
    modelled as unbroken opaque slabs, so every glazed face in front of one would be "blocked" by a
    neighbour it is nowhere near. The map's own `context` rows are excluded from the opaque set for
    the same reason at a finer grain -- that is `excluded_modifiers`, and it is the donor's rule.
    """
    if not units_known:
        return [], [SkippedView(check=check, reason="units not declared") for check in _ABSOLUTE_CHECKS]
    warnings = list(coincident_surfaces(polygons))
    warnings += [w for w in (non_planar_warning(polygons), _scale_warning(polygons)) if w is not None]
    # Ruling R827: the 1 km bound is in metres, which is why this is one of `_ABSOLUTE_CHECKS`.
    site = far_from_origin_warning((v for p in polygons for v in p.vertices), field="files")
    if site is not None:
        warnings.append(site)
    if role != "context":
        blocked = windows_without_opening(polygons, glass_modifiers=glass | glass_modifiers(text), excluded_modifiers=context)
        if (warning := window_warning(blocked)) is not None:
            warnings.append(warning)
    return warnings, []


def _validation_materials(
    loaded: LoadedProject, mapping: MaterialMap | None, scratch: Path
) -> tuple[list[MaterialsFile], dict[str, Any] | None, list[str], list[EmittedMaterial]]:
    """Design §5.8: registered materials re-emitted into scratch copies with the map's modifiers
    withdrawn, plus a scratch file of the map's rows, so a re-import defines no modifier twice.

    The withdrawal is what makes `check` and `add` agree. `oconv` reads its arguments in order and
    takes the *last* definition of a modifier silently, exiting 0 -- so validating against the
    project's existing materials file **and** the map's rows would compile a scene at whichever
    values happened to come last, and `scene_files_with_materials` refuses the duplicate outright
    rather than let that happen. Re-importing the same OBJ is the ordinary case, not an edge one.

    Nothing here touches `02_materials/`: every file is written inside the caller's scratch
    directory, which is removed when the import returns.
    """
    taken = set(mapping.assignments) if mapping else set()
    files = []
    for record in (r for r in loaded.manifest.inputs if r.kind == "materials"):
        kept = [m for m in replay_materials(record) if m.modifier not in taken]
        if kept:
            files.append(write_materials(scratch / f"{record.id}.rad", kept))
    if mapping is None:
        return files, None, [], []
    merged, replaced, emitted = set_assignments_for_import(loaded, mapping.assignments)
    rows = [m for m in emitted if m.modifier in taken]
    if rows:
        files.append(write_materials(scratch / "material_map.rad", rows))
    return files, merged, replaced, emitted


class MaterialsChangedDuringImportError(RuntimeError):
    """The registered materials or the project ground reflectance changed between building the
    validation octree and the commit (design 5.9 step 1). Retryable as-is.

    Its own class rather than a `_ArgumentRefused`: nothing about the arguments is wrong, and the
    repair is to repeat the identical call -- the same distinction ruling R58 draws between a
    retryable expiry and a refusal the caller has to fix. Raised **before** anything is written, so
    its message's "Nothing has been written" is a property of where it sits in `_commit_obj`, not a
    hope: every write in that function is downstream of this check.
    """

    code = "MATERIALS_CHANGED_DURING_IMPORT"
    severity = "error"
    field = "project"


#: How far past the parsed soup's own longest extent `oconv`'s bounding cube may sit before the
#: octree is holding geometry this server never saw -- a relative term plus an absolute floor, so
#: the rule means the same thing on a 1 m model and a 100 m one.
#:
#: **Measured, and deliberately loose.** Over the 28 `.rad` files in this working tree that parse
#: to a non-empty polygon soup, carry no `!` line and compile -- **tracked and untracked both;
#: most of the 28 are in a private scene library that is not committed** -- `oconv` pads the
#: cube by exactly 2e-5 m in total (7.0 -> 7.00002,
#: 42.0 -> 42.00002, 2.8 -> 2.80002); the largest cube/extent ratio is **1.000007**. The bound below
#: is 1 % plus 1 mm -- more than a thousand times that pad. This is a detector for a whole
#: primitive appearing in the octree and nowhere in the soup, not a precision instrument, and the
#: injection it exists to catch grew a 1 m scene's cube to 40 m (a ratio of 40). Erring loose costs
#: only detection of an injection small enough to hide inside 1 %, which the two rules in
#: `rad_engine.octree` and `rad_core.obj` already refuse at their own boundary; erring tight would
#: refuse legal geometry over a float difference.
_OCTREE_CUBE_RELATIVE_TOLERANCE = 0.01
_OCTREE_CUBE_ABSOLUTE_TOLERANCE_M = 1e-3


class OctreeUnexplainedError(RuntimeError):
    """`oconv` compiled a scene materially larger than the polygons this server parsed.

    **The outside check for the buffer-seam injection class** (whole-branch review Critical 4). The
    two load-bearing rules -- `rad_engine.octree._refuse_inline_commands`' length limits (the word
    limit on every word, the line limit on every line carrying `#` or `!`, R824) and
    `rad_core.obj.OBJ_MAX_COMMENT_BYTES` -- refuse the text at the boundary. This one
    refuses the *result*, and so does not depend on having modelled the boundary correctly: however
    a primitive got into the octree without passing through `parse_polygons`, the cube says so.

    Measured on this branch: a trailing 2048-byte comment carrying no `!` at all put a polygon into
    the octree that the soup never saw, and the import registered with `refused=None`, `polys=1`,
    `bbox_max=[1.0, 1.0, 0.0]` and an `octree_cube` of 40.00002.
    """

    code = "GEOMETRY_OCTREE_UNEXPLAINED"
    severity = "error"
    field = "files"


def _refuse_unexplained_octree(assembly: SceneAssembly, *, text: str) -> None:
    """Refuse when `oconv`'s bounding cube is materially larger than the parsed soup can account for.

    **Three preconditions, each of which makes the comparison meaningless rather than merely
    imprecise, so each skips rather than refuses:**

    1. *An empty octree.* `assemble_scene` already raises `OCTREE_EMPTY` for it, and `bounding_box`
       has nothing to compare against.
    2. *An empty soup.* Same reason -- `bounding_box` raises on it.
    3. *A scene carrying a non-`polygon` surface.* `parse_polygons` returns polygons and nothing
       else, so a `sphere`, `cone`, `ring`, `mesh` or `instance` makes the soup a strict subset of
       what `oconv` compiled and the cube legitimately exceeds it. **Measured:** a one-polygon scene
       of extent 1.0 with `m sphere s 0 0 4 5 5 5 2` beside it compiles to a cube of **7.00002** --
       seven times the extent, with nothing wrong. Without this gate the check refuses that file,
       which is a data-refusal defect of its own. `rad_engine.non_polygon_surfaces` is the one
       spelling of the question, walked by the same tokeniser `parse_polygons` uses.

    ``text`` is the geometry scene text, not the whole `scene_files` list: the materials files
    define no surfaces, so they contribute nothing to the cube.

    Only the *larger* direction is refused. A cube smaller than the soup would mean `oconv` dropped
    geometry, which is `OCTREE_INPUT_DROPPED`'s claim, proven from stderr by the run that dropped
    it -- reporting one event under two definitions is the mistake ruling R5 exists to prevent.
    """
    if assembly.octree.is_empty or not assembly.polygons:
        return
    unaccounted = non_polygon_surfaces(text)
    if unaccounted:
        return
    box = bounding_box(assembly.polygons)
    extent = max(box.size_x, box.size_y, box.size_z)
    cube = assembly.octree.bbox[3]
    allowed = extent * (1.0 + _OCTREE_CUBE_RELATIVE_TOLERANCE) + _OCTREE_CUBE_ABSOLUTE_TOLERANCE_M
    if cube <= allowed:
        return
    raise OctreeUnexplainedError(
        f"the compiled octree's bounding cube is {cube:.9g} m across, but the "
        f"{len(assembly.polygons)} polygon(s) this server parsed span only {extent:.9g} m in their "
        "longest dimension. oconv "
        "read geometry that is not in the scene text this server can see -- the usual cause is a "
        "line long enough to overrun one of Radiance's own input buffers, whose tail the object "
        "loop then reads as scene text. Nothing has been registered. Shorten any over-long line or "
        "comment in the input and import again."
    )


def _materials_note(loaded: LoadedProject) -> tuple[dict[str, tuple[str, ...]], dict[str, Any]]:
    """What the validation octree was built against: every materials input id with its recorded
    digests, and the ground reflectance. A new input, a removed one or a changed file all differ.

    **Digests, not modifier names.** The octree an import is validated against is compiled from the
    project's materials as they were when `_validation_materials` replayed them; a modifier whose
    *value* moved in between compiles a different scene under the same name, and only the file
    digest sees that. The ground reflectance rides along because `ground_finding` is computed
    against it and `_ground_modifiers` scopes the enclosure check by it, so a project whose ground
    moved mid-import was checked against a verdict that no longer holds.
    """
    return (
        {r.id: tuple(f.digest for f in r.files) for r in loaded.manifest.inputs if r.kind == "materials"},
        loaded.manifest.project.ground_reflectance.model_dump(),
    )


@dataclass(frozen=True)
class _Staged:
    """One import -- an `.obj` converted, or a `.rad` flattened and scaled -- with nothing committed.

    Everything `add` needs is on here, and `check` returns from exactly the same value: the two
    actions differ in whether the result is registered, never in what was computed, which is the
    property `GeometryAssembled.registered` publishes rather than leaving it to be inferred from a
    shape. The commit reads `merged`, `replaced`, `emitted`, `ground` and `ground_refuses`; `check`
    reports none of them.

    **Both importers stage into this one shape** (Phase 10 Task 11), which is what lets
    `_write_geometry_file`, `_blocking` and `_assembled` have one copy each rather than a `.rad`
    twin of each. `converted` is a `ConvertedObj` on the `.rad` path too -- its `text` is the
    flattened, scaled scene and `split_faces` is empty -- so the shared write guard reads one
    attribute for both. **It is the soup that gets registered, which since Task 13 is the
    *triangulated* one whenever `triangulate=True`**: every reader of this field gets the scene that
    was stored without having to know whether `robjutil +T` ran, which is why nothing downstream of
    `_import_obj` mentions triangulation at all. Every field that carries a default is one only an `.obj` can fill; `obj` is
    `None` for a `.rad`, and `units` is carried here rather than read off `obj` for the same reason.
    """

    role: str | None
    input_id: str
    paths: ProjectPaths
    #: The unit the **caller declared**, or `None` on a `check` that declared none. Not the unit the
    #: staged text is in -- that is always metres once `factor` has been applied -- and the value
    #: `register_input(units=...)` records, so `unscaled_geometry_records` can read the pair.
    units: Units | None
    converted: ConvertedObj
    assembly: SceneAssembly
    advisories: list[Advisory]
    skipped: list[SkippedView]
    sources: list[SourceView]
    #: `_materials_note` as of the read this import was staged from -- compared inside the commit
    #: lock against the project as it is then, so a materials write that landed while the octree was
    #: compiling is caught rather than committed over.
    note: tuple[dict[str, tuple[str, ...]], dict[str, Any]]

    obj: _ObjInput | None = None
    merged: dict[str, Any] | None = None
    replaced: list[str] = field(default_factory=list)
    emitted: list[EmittedMaterial] = field(default_factory=list)
    ground: Advisory | None = None
    ground_refuses: bool = False


def _import_obj(
    runtime: Runtime,
    loaded: LoadedProject,
    env: RadianceEnv,
    *,
    action: Literal["add", "check"],
    obj: _ObjInput,
    role: str | None,
    name: str | None,
    triangulate: bool,
) -> CallToolResult:
    """Convert one `.obj` in a scratch directory, validate it against a real octree, and report.

    **Every stage that needs the scratch files happens inside the `with`.** The rule file, the
    converted `.rad` and the validation materials copies all live there and are removed on the way
    out; the octree does not need them afterwards, because `assemble_scene` freezes it (`oconv -f`)
    and `getinfo -d` reads the frozen file's own cube.

    **`triangulate=True` converts the model twice and registers the second one** (design §5.7). The
    caller's OBJ goes through `obj2rad` as `converted`, `robjutil +T` then produces a second OBJ which
    goes through `obj2rad` again as `stored`, and from there the scene file, the validation octree,
    §7's checks and the commit all read `stored`. `converted` stays alive for two reasons only: the
    area accounting needs both sides, and its own §7 findings are reported with a
    `before triangulation: ` prefix -- because `robjutil +T` can *delete* a face, and a coincident
    pair it resolved by deleting one member cannot be named by any finding about the stored scene.
    Without that, a caller who ran `check`, saw `COINCIDENT_SURFACES`, and then imported with
    `triangulate=True` would see the warning simply vanish, with nothing saying a surface went with it.
    """
    paths = ProjectPaths(loaded.paths.root)
    input_id = _input_id(name, role)
    # Taken **before** the octree is compiled, because that is what it is a note about: the
    # materials picture `_validation_materials` is about to replay. Read from `loaded`, the open
    # this call already has, so it costs no extra IO.
    note = _materials_note(loaded)
    factor = metres_per_unit(obj.units) if obj.units else 1.0
    units_known = obj.units is not None

    omitted, rules = _conversion_plan(obj.model, obj.mapping)
    # Derived once and passed to both `_health` calls below. Two calls are two calls; two *spellings*
    # of which modifiers count as glazing and which as donor context would be a second rule.
    glass = _registered_glass(loaded) | (obj.mapping.glass_names if obj.mapping else set())
    context = obj.mapping.context_names if obj.mapping else frozenset()

    with scratch_directory(paths.jobs, runtime.roots) as scratch:
        converted = convert_obj(
            env, obj.data, model=obj.model, rules=rules, omitted_faces=omitted, factor=factor,
            up_axis=obj.up_axis, scratch=scratch, timeout=_ASSEMBLY_TIMEOUT_S,
        )
        # What is registered, checked and committed. `converted` without `triangulate`, so every
        # statement below is about one soup on both paths.
        stored = converted
        triangulated_view: TriangulatedView | None = None
        before_findings: list[ArtifactWarning] = []
        lost: ArtifactWarning | None = None
        if triangulate:
            data = triangulate_obj(env, obj.data, scratch=scratch, timeout=_ASSEMBLY_TIMEOUT_S)
            # **The cap is asked before the parse, so an oversized output is not a converter fault.**
            # `parse_obj` enforces `OBJ_MAX_BYTES` itself, and `+T` grows the file -- 29 429 bytes to
            # 37 237 on `office_room.obj`, measured -- so a legal import can cross the cap only after
            # triangulation. Wrapped by the `except` below it would read
            # *"robjutil +T's output could not be read back as the same model: the OBJ is N bytes,
            # over the ... cap"*, blaming a converter for the size of the caller's own file and
            # offering no repair. Asked here instead, on `files`, with the two numbers and a way out
            # (fix round 1, item 7).
            if len(data) > OBJ_MAX_BYTES:
                raise _ArgumentRefused(
                    f"{obj.path} is {len(obj.data)} bytes, which robjutil +T turns into {len(data)} "
                    f"bytes -- over the {OBJ_MAX_BYTES}-byte cap (OBJ_MAX_BYTES). Triangulation "
                    "splits every face, so a file inside the cap can leave it. Import without "
                    "triangulate=True, or triangulate outside the server and pass the result.",
                    field="files",
                )
            try:
                model = parse_obj(data, mapped=obj.mapping is not None)
                # **`robjutil +T` can rename every surface in the model, and nothing noticed.**
                # Whole-branch review Critical 1, measured cold: `+T` injects
                # `usemtl DEFAULT_MATERIAL` into **every** face group that carried no material, so
                # on an OBJ with no `usemtl` at all the re-parse flips `uses_usemtl` False -> True,
                # which flips `ObjModel.qualifier` from `Group` to `Material`, and `face_names` then
                # returns `("DEFAULT_MATERIAL",)` for every face. The caller's two walls are gone
                # from `model.names`; `_conversion_plan` regenerates its rules from the new model
                # and `account` is satisfied, because it matches on `.N` only and every face is
                # still present.
                #
                # It shipped as a wrong number reported as success. The first attempt refused with
                # *"the material map has no row for 'DEFAULT_MATERIAL'"*, the caller did exactly
                # what that message said, and the `add` then **succeeded** with every wall at one
                # reflectance. So this check must come **before** `refuse_unmapped`: the old
                # refusal blamed the caller's map and its repair was the defect.
                #
                # Refusing here rather than refusing `triangulate=True` up front for a
                # `qualifier == "Group"` model, deliberately: `rules_text(qualifier="Group")` exists
                # to serve exactly that class, `robjutil` is entitled to leave it alone, and a
                # blanket refusal would take triangulation away from every group-qualified model
                # whether or not this version of `robjutil` does the renaming. The measurement is
                # about what came back, so the check is about what came back.
                #
                # **Scope, measured:** confined to models with no `usemtl` at all. A model whose
                # `usemtl` merely persists across `g` lines is safe, which is why
                # `office_room.obj` and all of Task 13's tests missed it.
                # `invented` is computed **before** the condition, not with a walrus inside it:
                # `or` short-circuits, so a qualifier change alone left the name unbound and the
                # refusal raised `UnboundLocalError` instead -- an uncoded transport failure. Caught
                # by the test below, which is what a test that goes through the wire is for.
                invented = sorted(set(model.names) - set(obj.model.names))
                if model.qualifier != obj.model.qualifier or invented:
                    raise GeometryConversionError(
                        f"robjutil +T renamed the surfaces of {obj.path}: this server reads the "
                        f"model it wrote as qualified by {model.qualifier!r} with "
                        f"{list(model.names)!r}, where the model you supplied is qualified by "
                        f"{obj.model.qualifier!r} with {list(obj.model.names)!r}"
                        + (f" (names robjutil invented: {invented!r})" if invented else "")
                        + ". robjutil injects `usemtl DEFAULT_MATERIAL` into every face group that "
                        "carried no material, which collapses every surface onto one modifier and "
                        "one reflectance. **Do not add a row for the invented name** -- that makes "
                        "the import succeed at the wrong physics. Import without triangulate=True, "
                        "or give the model a `usemtl` per surface and export again."
                    )
                if obj.mapping is not None:
                    obj.mapping.refuse_unmapped(model.names)
            except GeometryError as exc:
                # Re-raised as a conversion failure rather than relayed: the caller's OBJ already
                # parsed and already satisfied the map, so a refusal naming `files` or
                # `material_map` here would blame an input that is fine for a stage the caller
                # cannot see. `robjutil` wrote this OBJ, so this is a converter fault.
                raise GeometryConversionError(
                    f"robjutil +T's output could not be read back as the same model: {exc}"
                ) from exc
            omitted_after, rules_after = _conversion_plan(model, obj.mapping)
            # Its own directory: `convert_obj` writes `rules.map` and both stages' guarded output
            # under the scratch path it is given, and the first conversion's rule file is still
            # there.
            second = scratch / "triangulated"
            second.mkdir()
            stored = convert_obj(
                env, data, model=model, rules=rules_after, omitted_faces=omitted_after,
                factor=factor, up_axis=obj.up_axis, scratch=second, timeout=_ASSEMBLY_TIMEOUT_S,
            )
            before_findings, _ = _health(
                converted.polygons, text=converted.text, glass=glass, context=context, role=role,
                units_known=units_known,
            )
            before_areas, after_areas = area_by_modifier(converted.polygons), area_by_modifier(stored.polygons)
            removed = area_removed(converted.polygons, stored.polygons)
            lost = triangulation_warning(removed, before_areas, after_areas)
            triangulated_view = TriangulatedView(
                polygons_before=len(converted.polygons),
                polygons_after=len(stored.polygons),
                area_removed_m2_by_material=removed,
            )
        geometry_rad = scratch / "geometry.rad"
        with open_regular_file(geometry_rad, "wb") as handle:
            handle.write(stored.text.encode("utf-8", "surrogateescape"))

        health, skipped = _health(
            stored.polygons,
            text=stored.text,
            glass=glass,
            context=context,
            role=role,
            units_known=units_known,
        )
        files, merged, replaced, emitted = _validation_materials(loaded, obj.mapping, scratch)
        scene_files = scene_files_with_materials(files, [geometry_rad])
        registered_modifiers = {m for f in files for m in f.modifiers}
        known = registered_modifiers | {p.modifier for p in stored.polygons}
        # **Ruling R48's guard, at the one write in this tool that leaves the roots.**
        # `open_regular_file(out_path, "wb")` uses `O_CREAT|O_TRUNC|O_NOFOLLOW`, and `O_NOFOLLOW`
        # covers only the **final component**: a directory symlink at `06_octrees/` is never
        # resolved, and `_component` blocks `/` and `..` in the filename, so that symlink is the
        # whole vector. Measured by the whole-branch review: an 852-byte octree written into a
        # directory outside every configured root, **overwriting and truncating a pre-existing
        # regular file**, with the tool reporting success and publishing the escaped path in
        # `octree`. Reached from `check`, which registers nothing and is the documented
        # non-destructive action; `name` is caller-supplied, so `geometry_<name>.oct` is targetable.
        #
        # **Pre-existing at the merge base** (`git show 2d55222:src/rad_mcp/geometry_tool.py`, the
        # same unguarded call), fixed here rather than left because this branch rewrote both import
        # paths and now has two of them. The house guard is hoisted, not re-spelled: it moved from
        # `annual_tool` to `rad_mcp.paths` in this commit and annual's six call sites moved with it.
        octree_out = paths.artifact_path("octree", f"{input_id}.oct")
        contain_octree_destination(octree_out, roots=runtime.roots)
        try:
            assembly = assemble_scene(
                env,
                scene_files,
                known_modifiers=known,
                out_path=octree_out,
                cwd=paths.root,
                ground_modifiers=_ground_modifiers(loaded),
                timeout=_ASSEMBLY_TIMEOUT_S,
            )
        except RadianceError as exc:
            raise _undefined_modifier_error(stored.polygons, registered_modifiers, exc) from exc
        # Before anything is committed or registered: the octree the caller would be handed must be
        # the scene this server parsed. Inside the `scratch_directory` block, so the refusal takes
        # the same cleanup path every other refusal here does.
        _refuse_unexplained_octree(assembly, text=stored.text)

    advisories = _relayed(assembly.advisories, units_known=units_known)
    if factor != 1.0:
        advisories.append(Advisory(
            code="GEOMETRY_UNITS_CONVERTED",
            severity="info",
            message=f"scaled by {factor:g} ({obj.units} -> m) on import; the stored geometry is in metres.",
        ))
    if omitted:
        advisories.append(Advisory(
            code="LUMINAIRE_FACES_OMITTED",
            severity="warn",
            field="material_map",
            message=(
                f"{len(omitted)} face(s) of luminaire rows were left out of the scene (faces "
                f"{sorted(omitted)[:20]}); place lamps at them with luminaire(action='place', geometry=...)."
            ),
        ))
    if converted.split_faces:
        advisories.append(Advisory(
            code="OBJ_FACES_SPLIT",
            severity="info",
            field="files",
            message=(
                f"obj2rad split {len(converted.split_faces)} warped face(s) into triangles (faces "
                f"{list(converted.split_faces)[:20]})."
            ),
        ))
    advisories.extend(warnings_to_wire(health))
    # **Both soups' findings, and the prefix is what separates them.** The stored scene's findings are
    # published plainly above; the un-triangulated soup's follow, each re-made with
    # `_BEFORE_TRIANGULATION` on the front, because a caller reading two `COINCIDENT_SURFACES` rows
    # with no marker cannot tell which scene either is about. `TRIANGULATION_REMOVED_SURFACE` leads
    # them: it is the finding that explains why the two lists differ at all.
    advisories.extend(warnings_to_wire(
        ([lost] if lost is not None else []) + _before_triangulation(before_findings, health)
    ))

    ground: Advisory | None = None
    ground_refuses = False
    if obj.mapping:
        ground, ground_refuses = ground_finding(loaded, emitted)
        if ground is not None:
            advisories.append(ground)

    sources = [SourceView(path=str(obj.path), digest=bytes_digest(obj.data))]
    if obj.map_path is not None:
        assert obj.map_data is not None
        sources.append(SourceView(path=str(obj.map_path), digest=bytes_digest(obj.map_data)))

    staged = _Staged(
        obj=obj,
        role=role,
        input_id=input_id,
        paths=paths,
        units=obj.units,
        converted=stored,
        assembly=assembly,
        advisories=advisories,
        skipped=skipped,
        merged=merged,
        replaced=replaced,
        emitted=emitted,
        sources=sources,
        ground=ground,
        ground_refuses=ground_refuses,
        note=note,
    )
    faces = FacesView(
        obj_faces=len(obj.model.faces),
        luminaire_faces_omitted=len(omitted),
        polygons=len(stored.polygons),
        # **`converted`, not `stored`, and both of these are deliberate.** `obj_faces` and
        # `luminaire_faces_omitted` count the OBJ the *caller* wrote -- the file they can open --
        # and `split_faces` names the warped faces `obj2rad` split of its own accord in that same
        # file. Reading them off `robjutil`'s intermediate would answer a question about a model
        # nobody can see, and after `+T` there is no warped face left for `obj2rad` to split, so
        # `stored.split_faces` is empty and would report that the caller's model had none.
        split_faces=len(converted.split_faces),
        triangulated=triangulated_view,
    )
    if action == "check":
        return emit(_assembled(
            loaded, staged, action="check", registered=False, files=[str(obj.path)], faces=faces
        ))

    if (blocking := _blocking(staged)) is not None:
        return blocking
    withdrawn = _commit_obj(runtime, staged, obj, arguments={
        "files": [str(obj.path)],
        "role": role,
        "units": obj.units,
        "material_map": str(obj.map_path) if obj.map_path else None,
        "up_axis": obj.up_axis,
        # **Recorded because it changes the stored bytes**, which is the same reason `material_map`
        # and `up_axis` are here. The registered `.rad` is `robjutil +T`'s output, not `obj2rad`'s
        # reading of the file this record names, so a manifest that omitted this would describe a
        # call that reproduces different bytes -- and `geometry(action="list")`'s
        # `GEOMETRY_SOURCE_CHANGED` compares digests against exactly that claim. Fix round 1,
        # Important 1: it was absent while both its siblings were present.
        "triangulate": triangulate,
        "name": name,
    })
    return emit(_assembled(
        loaded, staged, action="add", registered=True, files=[str(obj.path)], faces=faces,
        # **One spelling of "the answer was none", not two** (whole-branch review, Minor 1).
        # `staged.replaced or None` and `withdrawn or None` omitted the key on an `add` that
        # replaced or withdrew nothing, while `unused_map_rows: []` two lines below is published
        # empty and docs/tool-surface.md calls that deliberate. All three now answer the same way:
        # `[]` when a map was given and the answer is none, and **absent** when there was no map at
        # all -- which is the question these keys are about, and the only case in which they answer
        # nothing.
        materials=(
            (sorted(obj.mapping.assignments), staged.replaced, withdrawn)
            if obj.mapping is not None
            else (None, None, None)
        ),
    ))


#: What R825 appends to a pre-triangulation `error` the registered scene does not repeat.
_PASSES_AFTER_TRIANGULATION: Final = (
    " Only the faces as written fail this check; the triangulated scene, which is what an add"
    " registers, passes it."
)


def _before_triangulation(
    before: Sequence[ArtifactWarning], stored: Sequence[ArtifactWarning]
) -> list[ArtifactWarning]:
    """The un-triangulated soup's findings, prefixed `_BEFORE_TRIANGULATION`, as they go on the wire.

    **Ruling R825 (register N-43), reversing R431.** A finding at `error` whose code the stored
    (triangulated) scene does not also raise at `error` is reported at `warn`, with
    `_PASSES_AFTER_TRIANGULATION` appended, so `_blocking` no longer refuses an `add` over a soup
    that is not the one registered. What is registered is the triangulated scene, and R432 measured
    these checks' verdicts moving under triangulation, so a pre-triangulation error can be a check
    artefact. One the stored scene repeats keeps `error`; the stored scene's own finding refuses the
    add either way. Every other finding keeps its severity.

    `COORDINATES_FAR_FROM_ORIGIN` is left out: `robjutil +T` splits faces and moves no vertex, so a
    prefixed copy would repeat the stored scene's finding word for word.
    """
    failing = {w.code for w in stored if w.severity == "error"}
    findings = []
    for warning in before:
        if warning.code == "COORDINATES_FAR_FROM_ORIGIN":
            continue
        update: dict[str, Any] = {"message": _BEFORE_TRIANGULATION + warning.message}
        if warning.severity == "error" and warning.code not in failing:
            # The finding keeps one closing full stop, and the note follows as its own sentence.
            message = update["message"].rstrip(".") + "." + _PASSES_AFTER_TRIANGULATION
            update = {"severity": "warn", "message": message}
        findings.append(warning.model_copy(update=update))
    return findings


def _relayed(advisories: Sequence[EngineAdvisory], *, units_known: bool) -> list[Advisory]:
    """Engine findings on their way to the wire, minus the one that needs a declared scale.

    `OCTREE_RESOLUTION_LOW` is a ratio of the bounding box to the smallest edge against `oconv -r`'s
    own default -- an absolute comparison, and one of `_ABSOLUTE_CHECKS`. Without units the ratio is
    over coordinates in nobody's units, so the finding is dropped here and reported in `skipped`
    instead of asserted over a scale nothing declared.

    One function because both importers relay engine findings and both owe the same suppression. Fix
    round 1, finding 5: the filter and this comment were a verbatim copy in `_import_obj` and
    `_import_rad`, in a change whose whole theme was one copy per rule -- and the `.rad` copy was the
    one new behaviour with no assertion that could go red for it, because the tests that covered it
    mutated `units_known` rather than the filter.
    """
    return to_wire_all(
        advisory
        for advisory in advisories
        if units_known or advisory.code != "OCTREE_RESOLUTION_LOW"
    )


def _blocking(staged: _Staged) -> CallToolResult | None:
    """**Every `error` finding blocks the commit, and `check` is the way past one.** `None` when none does.

    The one exception is `GROUND_REFLECTANCE_INCONSISTENT`, which `ground_finding` emits at `error`
    whether or not it refuses: `materials(action="set")` already treats a ground mismatch as
    advisory unless `_refuses_ground_mismatch` says otherwise, and `staged.ground_refuses` is that
    same verdict. Re-deriving the rule here instead would be a second ground policy drifting from
    the first.

    One function rather than a copy per importer (Phase 10 Task 11). The `.rad` path cannot produce
    a ground finding at all -- only a material map does, and a `.rad` may not carry one -- so a
    second copy there would have been the same rule with one arm that could never run, which is the
    shape a later edit keeps in step with nothing.

    **A `before triangulation: ` finding reaches this list at `error` only when the registered scene
    repeats it** (ruling R825, reversing R431). Until R825 one about a soup that was never registered
    refused the `add` while the scene that would have been stored was clean; `_before_triangulation`
    now reports such a finding at `warn`, because what is registered is the triangulated scene and
    R432 measured these verdicts moving under triangulation. `action="check"` still reports both
    lists and commits nothing.
    """
    blocking = [
        advisory
        for advisory in staged.advisories
        if advisory.severity == "error"
        and not (advisory.code == "GROUND_REFLECTANCE_INCONSISTENT" and not staged.ground_refuses)
    ]
    if not blocking:
        return None
    return refuse(
        "the import was checked and not registered: "
        + "; ".join(advisory.message for advisory in blocking),
        staged.advisories,
    )


def _refuse_non_regular(path: Path, *, what: str) -> None:
    """`rad_engine.safe_io.refuse_existing_non_regular_destination`, with this tool's own repair.

    **The wrapper exists so the re-word cannot be forgotten at a third site.** `safe_io`'s message
    ends *"Point `out` at a plain file, or remove whatever is there"* -- `out` is `run_glare`'s and
    `image`'s argument and `geometry` has none, so relaying it tells the caller to change something
    they cannot pass. Fix round 1 re-worded that at the destination guard by hand; fix round 2 found
    the identical sentence reaching the caller again from the staging guard the same fix had just
    added (finding 1). One function, called from both, is what stops the third.

    The class is preserved, so the boundary's `except NotARegularFileError` still maps it to
    `PROJECT_PATH_NOT_A_REGULAR_FILE`; only the prose is this tool's.

    **Creates nothing** -- `lstat`, never `open` -- which is why both callers run it *outside* the
    block whose `except` unlinks the staging file. Inside, a directory at that path made the cleanup
    raise `PermissionError` over this exception and the published refusal degraded to the generic
    filesystem prose (finding 2, measured).
    """
    try:
        refuse_existing_non_regular_destination(path)
    except NotARegularFileError as exc:
        raise NotARegularFileError(
            f"{str(path)!r} exists and is not a regular file, so this server cannot {what}. "
            "Remove whatever occupies that path and re-run."
        ) from exc


def _refuse_stale_octree(current: LoadedProject, staged: _Staged) -> None:
    """Design 5.9 step 1: refuse when the materials this import was validated against have moved.

    Called from inside each commit's lock, on the project as re-read there. Raised **before**
    anything is written, so the message's "Nothing has been written" is a property of where the call
    sits rather than a hope -- every write in both commits is downstream of it. One function so the
    two commits cannot drift on which comparison counts as stale.
    """
    if _materials_note(current) != staged.note:
        raise MaterialsChangedDuringImportError(
            "the project's materials or ground reflectance changed while this import was being "
            "validated, so the octree it was checked against is stale. Nothing has been written. "
            "Repeat the same call."
        )


def _staging_path(destination: Path) -> Path:
    """Where `_write_geometry_file` stages the bytes for `destination` before the atomic rename.

    **One home for the suffix, and fix round 1's finding 6 is why.** `_write_geometry_file` derived
    `<name>.partial` and `_removed` re-derived it, so the writer and the *remover* agreed only by
    coincidence: renaming the suffix at the writer would have left every test green while the orphan
    `_removed` exists to delete became permanently unowned -- which is the exact failure the
    `.partial` ownership decision was made to prevent. Same discipline as `_refuse_non_regular`,
    which is deliberately this module's only path to
    `rad_engine.safe_io.refuse_existing_non_regular_destination` for the same reason.

    Beside `destination`, never in a temp directory: `os.replace` is atomic only within one
    filesystem, and the rename is what makes a half-written import invisible.
    """
    return destination.with_name(destination.name + ".partial")


class _StoredGeometry(NamedTuple):
    """What `_write_geometry_file` hands its caller: where the bytes landed, and what that cost.

    `replaced` is `True` when a stored copy was already there before this call -- a re-import under
    an existing `name`, which is the documented destructive action. It exists so `_recording_the_import`
    can tell the caller *which* of two very different things a failure after the rename means.
    """

    path: Path
    replaced: bool


class StoredGeometryReplacedError(RuntimeError):
    """The rename succeeded and the manifest write did not, on a **re-import**.

    **Whole-branch review Important 2.** Spec sec 5.9's partial-failure paragraph covers *"an
    unregistered file"* and *"the materials file ahead of the manifest"*. Neither covers this case,
    where the rename does not *create* a file but **replaces** one the manifest already describes.

    Measured by injecting `OSError(ENOSPC)` at `save_manifest` on a second `add` under the same
    name: the stored file holds the new scene, the manifest records `sha256:f69dcf3f...`, the file
    digests `sha256:65ef3e19...`. `rad_core.manifest.recompute_digests` works from recorded digests
    and `rad_engine.project.is_reusable` re-digests the *artifact's* own bytes and never its inputs,
    so every artifact built from that input stays `valid: true` over different geometry -- Task 10's
    Critical-1 class, reached through partial failure instead of the code path that was fixed.
    `geometry(action="list")` does detect it, which is why this is Important and not Critical;
    nothing on the run path does.

    **The refusal text is the sharper half of the fix, and the reason this class exists.** Before
    it, the caller fell through to `geometry`'s generic `except OSError` -- *"could not complete ...
    the filesystem refused the operation"* -- which is true and says nothing about the registered
    geometry having been swapped underneath the manifest. The caller could not know to repeat the
    call, and repeating it is the repair.

    **Not the fix the finding preferred, and that is a decision rather than an omission.** Its first
    choice -- digest the staging file and pass a caller-supplied `FileRef` so `register_input` and
    `save_manifest` run *before* the rename -- needs a `file_refs=` parameter on
    `rad_engine.project.register_input`, which has no such form (`_commit_obj`'s own docstring says
    so). Counted by AST rather than by grep: **10 call sites in 7 modules** -- `rad_engine.project`
    (inside `register_input_and_set_role`), `bsdf_tool`, `geometry_tool`, `materials_tool`,
    `server`, `weather_tool` and `window_group_tool`. Its second -- restore the previous destination bytes
    in the `except` -- means holding a copy of a file that may be tens of megabytes for the duration
    of every commit, and a restore that can itself fail leaves a third state with no name. Both are
    larger than an Important on the last wave of a branch should be. This is the finding's third
    option, taken deliberately and reported.
    """

    code = "GEOMETRY_STORED_COPY_REPLACED"
    severity = "error"
    field = "project"


@contextmanager
def _recording_the_import(stored: _StoredGeometry | None) -> Iterator[None]:
    """`register_input` + `save_manifest`, with the one failure that is not merely a failure named.

    The window is *"one rename plus one digest read"* (`_commit_obj`'s docstring). Everything before
    the rename leaves `01_geometry/` as it was found; everything after it has already replaced the
    stored copy, and on a **re-import** that copy is one the manifest describes.

    One spelling for both commits. `_commit_obj` and `_commit_rad` each end with the identical
    `register_input(save=False)` / `save_manifest` pair, and a hand-written second guard is how a
    defect has already returned at a new site twice on this branch.

    `None` is the in-place metre `.rad` registration, which writes no file at all: there is no
    stored copy and nothing was replaced, so a failure there is an ordinary filesystem failure.
    """
    try:
        yield
    except OSError as exc:
        if stored is None or not stored.replaced:
            # A *first* import: the rename created the file and nothing described it before, so the
            # residue is an unregistered file -- exactly what spec sec 5.9 already covers, and
            # `geometry`'s generic filesystem refusal is the right report for it.
            raise
        raise StoredGeometryReplacedError(
            f"the imported geometry was stored as {stored.path}, replacing the copy that was there, "
            f"and then the project manifest could not be written: {exc}. **The manifest still "
            "records the previous file's digest, so it now describes bytes that are no longer "
            "there**, and every artifact built from this input will still report itself valid over "
            "the new geometry. Nothing about the arguments is wrong. Repeat the identical call once "
            "the filesystem problem is cleared; until then, treat results built from this input as "
            "untrustworthy, and geometry(action='list') will report the mismatch."
        ) from exc


@contextmanager
def _write_geometry_file(
    current: LoadedProject, runtime: Runtime, staged: _Staged
) -> Iterator[Path]:
    """Store `staged.converted.text` as `01_geometry/<id>.rad`: every guard, the staged write, the
    caller's own last refusable step, the atomic rename, and the cleanup on every other exit.

    **One copy, called by both importers** (Phase 10 Task 11), and the whole sequence rather than
    the write alone. The sequence *is* the guarantee: written straight to `destination`, a refusal
    from a step that still runs afterwards left the **new** geometry on disk under the **previous**
    record's `FileRef.digest` on a re-import under an existing `name`, which the `others` check
    below deliberately allows. Nothing in the system can notice that -- `rad_core.manifest
    .recompute_digests` works from the digests the manifest already records rather than from the
    files, and `rad_engine.project.is_reusable` re-digests the *artifact's* bytes and never its
    inputs -- so every artifact built from that input stays `valid: true` and reusable while
    pointing at different geometry. That was Task 10's fix round 1, Critical 1; extracting only the
    `open_regular_file` call would have re-opened it on the `.rad` path.

    A context manager rather than a function because the `.obj` path has a step that can still
    refuse *after* the bytes exist and *before* the rename -- `commit_materials_locked`, which may
    raise `InputStillBuiltFrom`, `PathOutsideRoot` or an `OSError` from `write_materials`. That step
    runs in the `with` body, inside the same cleanup, so the `.rad` path (which has no such step and
    passes an empty body) and the `.obj` path share one rename and one `except`. `os.replace` runs
    only when the body returns normally.

    **Both `_refuse_non_regular` calls are above the `try`, and that placement is Task 10 fix round
    2's finding 2.** Neither creates anything, so neither needs the cleanup; inside it, a *directory*
    at `staging` made the guard raise and then `staging.unlink()` raise `PermissionError` over it, so
    the caller got the generic filesystem prose instead of `PROJECT_PATH_NOT_A_REGULAR_FILE`. The
    `O_CREAT` open below is the first statement that can leave a file behind; it sits above the `try`
    as well (ruling R830), so a name its exclusive open refuses is never unlinked by this cleanup.

    `current` is the project as re-read **inside** the caller's lock, and it is read for one thing:
    the `others` check. Passing `staged.paths`'s own `LoadedProject` from before the lock would
    decide "is this destination someone else's file?" against a manifest that may have moved.

    **`geometry(action="remove")` owns both of the files this function can leave behind**, as of
    Task 12. `_removed` deletes `01_geometry/<id>.rad` when the record it deregisters is marked
    `server_written` and names exactly that path, and it deletes a `<id>.rad.partial` beside it in
    the same breath -- the orphan left by an exit the `except` below does not run on at all, an
    abrupt process kill being the reachable case. Both go through `_unlink_contained`, which holds the containment check, and
    a record the caller's own files were registered against is untouched. Its own docstring states
    all of that; the sentence you are reading is not the place that promises it.

    Until then the two lifecycles had **no owner at all**: `remove` edited the manifest only, so the
    only thing that ever replaced the destination was the next `add` under the same id, and the only
    thing that removed a `.partial` was the `except` below. Recorded because the gap was real, and
    kept here because this is the function that creates both paths.
    """
    destination = staged.paths.geometry_file(staged.input_id)
    # **Read before anything is written, because it is what the rename below will have done.** A
    # first import under a name *creates* the stored copy; a re-import *replaces* one the manifest
    # already describes, and only the second case can leave the manifest's recorded digest
    # describing bytes that are no longer there (Important 2). Carried out on the yield rather than
    # re-derived by the caller: `destination` has exactly one spelling and this question is about
    # that path, not about a second computation of it.
    replaced_an_existing_copy = destination.exists()

    # **Another input's file is never overwritten, and the id is the only thing that makes a
    # destination this call's own.** Re-importing under the same `name` replaces this server's own
    # metre copy, which is the documented destructive action; landing on the file a *different*
    # record names would silently change what that record's digest describes.
    #
    # `_same_file`, not `Path(f.path) == destination` (fix round 3): `name="scene"` and `name="Scene"`
    # are two ids whose destinations are one file on a case-insensitive filesystem, and path equality
    # cannot see it. See `_same_file` for the measurement.
    others = [
        record.id
        for record in current.manifest.inputs
        if record.id != staged.input_id
        and any(_same_file(Path(f.path), destination) for f in record.files)
    ]
    if others:
        raise _ArgumentRefused(
            f"{destination} is registered by {others!r}; this import would overwrite it. "
            "Choose another name.",
            field="name",
        )

    # **And never a file this very import read.** Whole-branch review Critical 2. Three categories
    # of caller file exist and only two were guarded: `_refuse_server_written_input` judges the
    # caller's **top-level** `files`, the `others` check above judges **other records'** files, and
    # Task 6's `flatten_scene` introduced a third -- the `!xform` includes the flatten pulls in,
    # which nothing compared against the destination (`grep -c "_same_file(destination" ` against
    # `sources` was 0).
    #
    # **Measured end to end by the review.** A caller's part file at
    # `<project>/01_geometry/geometry_ft.rad`, included by a `main.rad` at the project root,
    # imported with `units="ft", name="ft"`: `refused: None`, and the part file came back holding
    # `# /usr/local/radiance/bin/xform -c -s 0.3048` and the whole flattened, scaled scene. The
    # caller's original content is gone, unrecoverably. The record then names that path as **both**
    # `files[0]` and `sources[1]`, with the source digest taken *before* the overwrite, so every
    # later `geometry(action="list")` reports a permanent unrepairable `GEOMETRY_SOURCE_CHANGED`
    # against a file the server itself destroyed -- and `main.rad` still `!xform`s it, so the
    # caller's scene now recursively contains the whole flattened scene.
    #
    # Narrow -- it needs an include resolving to exactly `01_geometry/geometry_<name>.rad` -- and it
    # needs no hostility, only an unlucky `name`. Critical on consequence, not on likelihood.
    #
    # `_same_file`, the same spelling the `others` check uses two lines up, and for the same reason:
    # path equality cannot see two spellings of one file on a case-insensitive filesystem.
    read = [
        source.path
        for source in staged.sources
        if _same_file(Path(source.path), destination)
    ]
    if read:
        raise _ArgumentRefused(
            f"{destination} is one of the files this import was computed from ({read!r}); writing "
            "the converted scene there would destroy it. A `!xform` include of a scene being "
            "imported reaches this the same way a top-level file does. Choose another name, or "
            "move that file out of the project's own 01_geometry/ directory.",
            field="name",
        )

    # **The parent chain, resolved, before anything is created.** `O_NOFOLLOW` below guards only the
    # final component; a `01_geometry` that is itself a symlink to somewhere else leads the write out
    # of every root while the final component is not a symlink at all
    # (`rad_engine.safe_io.resolved_parent_within`).
    if not resolved_parent_within(destination, runtime.roots):
        raise PathOutsideRoot(
            f"{destination.parent} resolves outside every allowed root. Nothing has been written."
        )

    # Ruling R391's scan of the text about to be stored is not here: each caller runs
    # `_scan_text_to_store` before it takes `runtime.modifying` (ruling R753, final review).
    _refuse_non_regular(destination, what="store the imported geometry there")

    staging = _staging_path(destination)
    _refuse_non_regular(staging, what="stage the imported geometry there")

    # **The staging open sits outside the cleanup** (ruling R830), `blend._write_blend`'s shape: when
    # the exclusive open refuses a name planted after its unlink, nothing of this import's exists
    # there, and the `except` below must not unlink what someone else put there.
    handle = open_staging_file(staging)
    try:
        with handle:
            handle.write(staged.converted.text.encode("utf-8", "surrogateescape"))
        yield _StoredGeometry(destination, replaced_an_existing_copy)
        # **The last thing that can leave `01_geometry/` changed.** `os.replace` is atomic and does
        # not follow a symlink at `destination` -- it replaces the link itself -- so the `O_NOFOLLOW`
        # the old direct write relied on is not needed to keep the write inside the project.
        os.replace(staging, destination)
    except BaseException:
        # Every exit that is not the rename leaves `01_geometry/` exactly as it was found.
        staging.unlink(missing_ok=True)
        raise


def _scan_text_to_store(staged: _Staged) -> None:
    """Ruling R391 over text that is about to become a scene file: `xform -c` keeps a `!` line's
    text without executing it, so what is about to be stored is scanned before it is stored rather
    than after something reads it back.

    Called by both importers **before** `runtime.modifying` (ruling R753, final review): the scan
    follows `!xform` includes for up to its deadline, and that lock holds the job worker's manifest
    lock and the project's `flock`, so a slow scan inside it stalled every other writer. The text
    scanned is the in-memory `staged.converted.text` `_write_geometry_file` then writes, so nothing
    the lock protects is read here.
    """
    destination = staged.paths.geometry_file(staged.input_id)
    refuse_inline_commands_in_text(staged.converted.text, label=destination, cwd=staged.paths.root)


def _commit_obj(
    runtime: Runtime, staged: _Staged, obj: _ObjInput, *, arguments: dict[str, Any]
) -> list[str]:
    """Design 5.9, under one lock and one manifest save. Returns the modifiers withdrawn from the
    LM-83 defaults file.

    **Every refusal is above `commit_materials_locked`, and that is the whole shape of this
    function.** That call's `save=False` contract (see its docstring) is that by the time it
    returns the *filesystem* side is already final and unrollable -- `_withdraw_files` has unlinked
    or rewritten the LM-83 defaults file and `write_materials` has written this import's claim --
    and only the manifest mutation is deferred to the caller. So a refusal, an early return or a
    raise between that call and `save_manifest` would leave `02_materials/` and `manifest.json`
    permanently disagreeing. Everything that can say no -- the stale-octree check here, and the
    already-taken destination, the containment of the destination's parent and the two
    non-regular-file guards inside `_write_geometry_file`, and the inline-command scan
    (`_scan_text_to_store`, before the lock) -- runs before it, and nothing
    but the rename, `register_input` and the save runs after.

    **Two things inside that window still do IO, and neither is avoidable through these APIs.** The
    `os.replace` in `_write_geometry_file` is one rename, which cannot partially apply.
    `register_input` is the other: `rad_engine.project.file_refs` opens and digests `destination` to
    build the `FileRef`, so an `OSError` there lands between the point of no return and the save.
    There is no digest-supplied-by-the-caller form of `register_input`; a later task changing this
    sequence should know that the window is "one rename plus one digest read", not "nothing".

    **The geometry file is written to a sibling temporary and renamed into place last**, and the
    whole of that sequence is `_write_geometry_file`'s -- see there for what writing straight to the
    destination cost. `commit_materials_locked` runs inside it, which is the point: it is the step
    that can still refuse after the bytes exist.

    **The same `runtime` the tool is running under is what must be passed.**
    `commit_materials_locked` containment-checks its own target against `runtime.roots`, while
    `_withdraw_files` reaches for the module-level `current().roots`; the two agree only because
    every caller passes the runtime `current()` returned. `geometry` binds `runtime = current()` at
    its top and hands that same object down, so both containment checks consult one root set.

    The re-read inside the lock is `Runtime.modifying`'s own rule: `save_manifest` writes the whole
    document with no compare-against-disk, so the read the write is based on has to be taken inside
    the lock or the interval between them loses another writer's update.
    """
    paths = staged.paths
    _scan_text_to_store(staged)
    with runtime.modifying(paths):
        current = open_project(paths.root, radiance_version=runtime.launcher.version)
        _refuse_stale_octree(current, staged)

        withdrawn: list[str] = []
        target = current
        with _write_geometry_file(current, runtime, staged) as stored:
            if obj.mapping is not None:
                committed = commit_materials_locked(
                    runtime,
                    paths,
                    action="set",
                    materials_=staged.emitted,
                    arguments={"assignments": staged.merged},
                    save=False,
                )
                target, withdrawn = committed.current, committed.taken
        with _recording_the_import(stored):
            register_input(
                target,
                input_id=staged.input_id,
                kind="geometry",
                files=[stored.path],
                # The unit the **caller declared**, not the unit the stored file is in: the file is
                # always metres and `server_written=True` is what says so
                # (`InputRecord.server_written`). Recording "m" here for a model declared in feet
                # would erase the only record of what the source was, and
                # `unscaled_geometry_records` reads the pair, not `units` alone.
                units=obj.units,
                # **No `role=` here, and that is ruling R32.** `geometry`'s five role values are not
                # `MaterialRole` members, and `register_input` raises `InvalidRoleError` for
                # anything that is not one. The assembly-time role is carried on the result and in
                # the `ToolCall`, which is where a record of the call belongs.
                registered_by=ToolCall(tool="geometry", action="add", arguments=arguments),
                sources=[
                    FileRef(path=source.path, digest=source.digest) for source in staged.sources
                ],
                server_written=True,
                save=False,
            )
            save_manifest(paths, target.manifest)
    return withdrawn


def _assembled(
    loaded: LoadedProject,
    staged: _Staged,
    *,
    action: Literal["add", "check"],
    registered: bool,
    files: list[str],
    faces: FacesView | None,
    materials: tuple[list[str] | None, list[str] | None, list[str] | None] | None = None,
) -> GeometryAssembled:
    """One import's result shape -- `.obj` or `.rad` -- read off `staged` and nothing re-derived.

    `materials` is `(written, replaced, withdrawn)` and is **`None` on every `check`**, which is why
    it is a parameter rather than three more fields read off `staged`. `check` computes `merged`,
    `replaced` and `emitted` -- it needs them to build the validation octree -- and commits none of
    them, so reporting `materials_replaced` on a `check` would name modifiers whose values are
    untouched on disk. The three keys are in `OMIT_WHEN_NONE`, so a `check` result does not carry
    them at all rather than carrying them empty.

    **`files` and `faces` are parameters for the same reason** (Phase 10 Task 11). `files` is what
    the caller named -- the `.obj`, or the `.rad` list -- never the flattened or converted copy in
    the scratch directory that no longer exists by the time this runs; `check` stores nothing, so
    naming a path the caller could not open would be a lie about what is on disk, and the `.rad`
    path's *stored* file is named by `register_input` instead. `faces` is the OBJ-to-polygon
    accounting and is `None` for a `.rad`, which has no faces to account for: an import that never
    read an OBJ has no answer to "how many faces went in", and `OMIT_WHEN_NONE` leaves the key off
    rather than publishing a null answer to a question this call did not ask.

    `sources` is likewise `None` -- omitted -- when empty, which is the metre-`.rad` case: the
    caller's own files are registered in place, so there is no separate "what this was computed
    from" to report. `sources` on the scaled `.rad` and on every `.obj` is where the digests live.
    """
    assembly = staged.assembly
    obj = staged.obj
    box = bounding_box(assembly.polygons)
    return GeometryAssembled(
        project=loaded.paths.name,
        root=str(loaded.paths.root),
        action=action,
        role=staged.role,
        files=files,
        polygon_count=assembly.inventory.polygon_count,
        modifiers=sorted(assembly.inventory.modifiers),
        bbox=BBoxView(
            min=[box.x_min, box.y_min, box.z_min],
            max=[box.x_max, box.y_max, box.z_max],
            octree_cube=list(assembly.octree.bbox),
        ),
        checks=_checks(assembly, units_known=staged.units is not None),
        faces=faces,
        materials_written=materials[0] if materials else None,
        materials_replaced=materials[1] if materials else None,
        materials_withdrawn=materials[2] if materials else None,
        unused_map_rows=(
            list(obj.mapping.unused(obj.model.names)) if obj is not None and obj.mapping else None
        ),
        skipped=staged.skipped or None,
        sources=staged.sources or None,
        octree=str(assembly.octree.path),
        input_id=staged.input_id if registered else None,
        registered=registered,
        advisories=staged.advisories or None,
    )


def _once(paths: Sequence[Path]) -> list[Path]:
    """`paths` with repeats removed, keeping the first spelling of each and the order given.

    Fix round 1, finding 6. A caller may legitimately name a parent scene **and** one of its own
    `!xform` includes -- `oconv` reads both, and so does the flatten -- so `files` and
    `flattened.includes` can overlap. Undeduplicated, that path got two `sources` rows under one
    digest and contributed to `rad_core.manifest.combine_file_digests` twice, which changes the
    input's digest for a reason nothing in the scene changed. Compared on `Path.resolve()` because the
    two lists are built by different code: `rad_mcp.paths.contain` resolves, and the R391 include
    recursion records `(cwd / target).resolve()` (`rad_engine.octree`). **Both elements are therefore
    already resolved**, so what the first-seen rule preserves is the *resolved* spelling and the order
    `oconv` was given -- not the string the caller typed, which neither list still holds.
    """
    once: list[Path] = []
    seen: set[Path] = set()
    for candidate in paths:
        resolved = candidate.resolve()
        if resolved not in seen:
            seen.add(resolved)
            once.append(candidate)
    return once


def _refuse_rad_over_cap(files: Sequence[Path], includes: Sequence[Path]) -> None:
    """**Ruling R828 (register N-50).** Refuse a `.rad` import whose top-level `files` plus
    `includes` (the `!xform` includes `flatten_scene` read) hold more than `OBJ_MAX_BYTES` together,
    on `files` with the code `_capped` uses for an `.obj`.

    `OBJ_MAX_BYTES` because it is the bound already measured for one call's pure-Python parse and
    checks (`rad_core.obj`), and the flattened scene is parsed and checked the same way. Each file is
    counted once (`_once`), however many times the scene includes it; a file whose size cannot be
    read counts as 0, since the flatten that named it has just read it and a refusal here would
    blame the cap for a filesystem fault. `includes` is empty on the call made before the flatten,
    which refuses on the top-level files alone without reading them.
    """
    counted = _once([*files, *includes])
    total = 0
    for path in counted:
        try:
            total += path.stat().st_size
        except OSError:
            continue
    if total <= OBJ_MAX_BYTES:
        return
    included = len(counted) - len(_once(list(files)))
    what = f"{len(_once(list(files)))} scene file(s)" + (
        f" and the {included} `!xform` include(s) they read" if includes else ""
    )
    raise _ArgumentRefused(
        f"{what} hold {total} bytes, over the {OBJ_MAX_BYTES}-byte cap (OBJ_MAX_BYTES, the bound an "
        ".obj import has). Split the scene into smaller imports, or simplify it.",
        field="files",
    )


def _import_rad(
    runtime: Runtime,
    loaded: LoadedProject,
    env: RadianceEnv,
    *,
    # `Literal["add", "check"]`, not `str`: `list` and `remove` both return from `_dispatch` before
    # it reaches here, so those are the only two values that can arrive -- and `GeometryAssembled`
    # already types its own `action` field that narrowly. Declaring `str` here widened the value
    # between the two and left the mismatch to be discovered at the assignment instead of the call.
    action: Literal["add", "check"],
    files: list[Path],
    role: str | None,
    units: Units | None,
    name: str | None,
) -> CallToolResult:
    """Flatten one or more Radiance scene files, scale them if they are not in metres, validate the
    result against a real octree, and report (design §5.11). Three cases, one path:

    - **`units` given and not `"m"`** -- the flattened scene is scaled through `xform -s` and stored
      as `01_geometry/<id>.rad` on `add`, `server_written=True`, with `sources` naming the caller's
      files and every `!xform` include the flatten pulled in.
    - **`units == "m"`** -- nothing is stored and the caller's own files are registered in place,
      exactly as before Phase 10 (`server_written=False`, no `sources`). The scene is still
      flattened, checked and compiled.
    - **`check` without `units`** -- factor 1, and every check resting on an absolute tolerance in
      metres is skipped and listed in `skipped` rather than answered (design §5.10, and 10.13's rule
      applies here as it does on the OBJ path).

    **The octree is assembled from the flattened copy even when `units == "m"`**, so `oconv` compiles
    exactly the text the checks read: `_health` and `non_finite_polygon_ids` work on `flattened.text`
    and a second, independent expansion for the octree would be a second scene that merely ought to
    match. The file the caller keeps is untouched either way -- the copy lives in the scratch
    directory and is removed on the way out.

    **The non-finite scan runs on the flattened text and is not redundant with `xform`'s own
    refusal.** Measured on Radiance 6.1a: a literal `inf`/`nan` in a `polygon` makes `xform` exit 1
    (`bad polygon`), so it never reaches here -- but `1e400` makes `xform` exit **0** and write
    `inf nan nan` into the flattened scene, which is the silent wrong answer this scan exists for.
    """
    paths = ProjectPaths(loaded.paths.root)
    input_id = _input_id(name, role)
    # Taken **before** the octree is compiled, because that is what it is a note about: the materials
    # picture this import is validated against. Read from `loaded`, the open this call already has.
    note = _materials_note(loaded)
    units_known = units is not None
    factor = metres_per_unit(units) if units is not None else 1.0

    # Ruling R828: the top-level files alone, before the flatten reads them; then, below, the same
    # bound over them plus every include the flatten read.
    _refuse_rad_over_cap(files, ())
    flattened = flatten_scene(
        env, [str(f) for f in files], cwd=paths.root, timeout=_ASSEMBLY_TIMEOUT_S
    )
    # **`!xform` includes that could not be read are visible here and nowhere below.** `oconv` exits
    # 0 having silently omitted a failed include, and `assemble_scene` is what normally catches that
    # (ruling R5's `OCTREE_INPUT_DROPPED`, classified from `xform`'s and `oconv`'s own stderr) -- but
    # it is handed the already-flattened copy, whose `!` lines are gone, so it cannot see a drop that
    # happened before it ran. The flatten this import performed is the run that saw it.
    # Every file the flatten pulled in, contained before it is digested, recorded or compiled --
    # see `IncludeOutsideRootError`. Sorted so two runs over one scene name the same first offender.
    outside = [
        str(include)
        for include in sorted(flattened.includes)
        if not _within_roots(include, runtime.roots)
    ]
    if outside:
        raise IncludeOutsideRootError(
            f"{len(outside)} `!xform` include(s) of this scene resolve outside every allowed root: "
            f"{outside[:10]!r}. This server read them to flatten the scene and would have stored "
            "their content inside the project and recorded their paths and digests in the "
            "manifest. Nothing has been registered. Move them inside a configured root, or add "
            "their directory to the server's roots."
        )
    _refuse_rad_over_cap(files, sorted(flattened.includes))

    flatten_findings = classify(flattened.warnings)
    dropped = [a for a in flatten_findings if a.code == "OCTREE_INPUT_DROPPED"]
    # **Everything else `xform` said is relayed, not discarded** (fix round 1, finding 3).
    # `assemble_scene`'s contract is that every remaining stderr warning is *"never silently
    # dropped"*, and until Task 11 the caller's own files went into `assemble_scene`, so this
    # flatten's warnings were *its* warnings and reached `SceneAssembly.advisories`. They cannot any
    # more -- it is handed the flattened copy -- so they are carried forward here instead. Measured:
    # no `.rad` this tool accepts could be made to produce a warning in this set today (an unknown
    # object type and a literal `inf` both make `xform` exit 1, and a degenerate scene writes no
    # stderr at all), so the set is empty in practice -- which is a reason to keep the guarantee
    # rather than to rely on the set staying empty.
    kept = [a for a in flatten_findings if a.code != "OCTREE_INPUT_DROPPED"]

    bad = non_finite_polygon_ids(parse_polygons(flattened.text))
    if bad:
        raise NonFiniteCoordinatesError(
            f"{len(bad)} polygon(s) have a non-finite coordinate ({', '.join(map(repr, bad[:20]))}). "
            "Radiance cannot trace them. Repair the scene."
        )

    with scratch_directory(paths.jobs, runtime.roots) as scratch:
        text = flattened.text if factor == 1.0 else scale_scene_text(
            env, flattened.text, factor=factor, scratch=scratch, timeout=_ASSEMBLY_TIMEOUT_S
        )
        polygons = parse_polygons(text)
        health, skipped = _health(
            polygons,
            text=text,
            glass=_registered_glass(loaded),
            # No `context` rows: those come from a material map, which a `.rad` import may not carry.
            context=frozenset(),
            role=role,
            units_known=units_known,
        )
        geometry_file = scratch / "geometry.rad"
        with open_regular_file(geometry_file, "wb") as handle:
            handle.write(text.encode("utf-8", "surrogateescape"))

        materials_files = _materials_files(loaded)
        registered_modifiers = {m for f in materials_files for m in f.modifiers}
        # The one correct way to build this list -- never assembled here. See the module docstring.
        scene_files = scene_files_with_materials(materials_files, [geometry_file])
        # The same guard on the other import path -- see `_import_obj` for the reproduction and
        # the provenance. Two unguarded sites is what made this a branch finding rather than a
        # merge-base one.
        octree_out = paths.artifact_path("octree", f"{input_id}.oct")
        contain_octree_destination(octree_out, roots=runtime.roots)
        try:
            assembly = assemble_scene(
                env,
                scene_files,
                # **The registered materials plus every modifier the scene's own polygons use**, and
                # that is correct rather than a check switched off: `assemble_scene` runs `oconv`
                # first and `oconv` exits 1 on a genuinely undefined modifier, so by the time
                # `inventory` runs the authoritative check has already passed. Passing the registered
                # set alone refused every scene that declares its own materials and then uses them --
                # a completely valid `.rad`, and what this repository's own office fixture is.
                known_modifiers=registered_modifiers | {p.modifier for p in polygons},
                out_path=octree_out,
                cwd=paths.root,
                ground_modifiers=_ground_modifiers(loaded),
                timeout=_ASSEMBLY_TIMEOUT_S,
            )
        except SceneAssemblyError as exc:
            # Re-raised **only** when this flatten saw a drop `assemble_scene` could not: with
            # nothing to add, the engine's own refusal is the better one and is left untouched.
            if not dropped:
                raise
            raise _dropped_include_error(
                files, dropped, leading=exc.advisories, trailing=kept
            ) from exc
        except RadianceError as exc:
            raise _undefined_modifier_error(polygons, registered_modifiers, exc) from exc
        if dropped:
            # `kept` rides the refusal too (fix round 2, finding 3). It is the same guarantee the
            # success path keeps, and for the same reason: a refusal is still a report about a run,
            # and a warning `xform` wrote about this scene is no less true because the run is being
            # refused. Empty in practice today, exactly as on the success path -- which is why it is
            # carried rather than reasoned away.
            raise _dropped_include_error(files, dropped, trailing=kept)
        # See `_import_obj` for why this sits here: the same check, at the same point, on the other
        # import path. `text` is the geometry scene text -- scaled if this import scaled it -- which
        # is what `geometry_file` holds and what `oconv` read.
        _refuse_unexplained_octree(assembly, text=text)

    # The flatten ran first, so its findings lead. `assemble_scene` classified its own `xform` and
    # `oconv` stderr over the *flattened* copy, which carries no `!` line, so the two sets cannot
    # report the same include twice.
    advisories = _relayed([*kept, *assembly.advisories], units_known=units_known)
    if factor != 1.0:
        advisories.append(Advisory(
            code="GEOMETRY_UNITS_CONVERTED",
            severity="info",
            message=f"scaled by {factor:g} ({units} -> m) on import; the stored geometry is in metres.",
        ))
    advisories.extend(warnings_to_wire(health))

    # **`sources` is what a *stored* copy was computed from, so the metre case has none.** Registered
    # in place, the caller's files are already `files` on the record and their digests are the
    # record's own `FileRef`s; a `sources` list repeating them would claim this server converted
    # something it did not. The includes are sorted, so two runs over one scene record the same list.
    sources = (
        [
            SourceView(path=ref.path, digest=ref.digest)
            for ref in file_refs(_once([*files, *sorted(flattened.includes)]))
        ]
        if factor != 1.0
        else []
    )

    staged = _Staged(
        role=role,
        input_id=input_id,
        paths=paths,
        units=units,
        # `split_faces=()` -- `obj2rad`'s face splitting is an OBJ fact and nothing split anything
        # here. The shape is shared so `_write_geometry_file` reads one attribute for both importers.
        converted=ConvertedObj(text=text, polygons=polygons, split_faces=()),
        assembly=assembly,
        advisories=advisories,
        skipped=skipped,
        sources=sources,
        note=note,
    )
    named = [str(f) for f in files]
    if action == "check":
        return emit(_assembled(
            loaded, staged, action="check", registered=False, files=named, faces=None
        ))

    if (blocking := _blocking(staged)) is not None:
        return blocking
    _commit_rad(
        runtime,
        staged,
        files=files,
        units=units,
        arguments={"files": named, "role": role, "units": units, "name": name},
        server_written=factor != 1.0,
    )
    return emit(_assembled(
        loaded, staged, action="add", registered=True, files=named, faces=None
    ))


def _dropped_include_error(
    files: Sequence[Path],
    dropped: Sequence[EngineAdvisory],
    *,
    leading: Sequence[EngineAdvisory] = (),
    trailing: Sequence[EngineAdvisory] = (),
) -> SceneAssemblyError:
    """`assemble_scene`'s own refusal shape, raised for a drop it could not have seen.

    **The parameter names are the ordering rule.** Ruling R5's order is kept literally: `leading` is
    `OCTREE_EMPTY` -- the claim about the artifact, which only `assemble_scene` can prove -- and
    applies when the scene's only content was the failed include, so both claims hold at once; then
    one finding per dropped input; then `trailing`, the run's remaining stderr findings, which qualify
    the run rather than making a claim about the artifact.
    """
    advisories = [*leading, *dropped, *trailing]
    return SceneAssemblyError(
        f"the octree assembled from {[str(f) for f in files]} is not the scene requested: "
        + "; ".join(f"{a.code} ({a.subject or a.message})" for a in advisories),
        advisories=advisories,
    )


def _commit_rad(
    runtime: Runtime,
    staged: _Staged,
    *,
    files: list[Path],
    units: Units | None,
    arguments: dict[str, Any],
    server_written: bool,
) -> None:
    """Register one `.rad` import, under one lock and one manifest save.

    Two shapes, and `server_written` is the whole of the difference. A scaled import stores this
    server's own metre copy through `_write_geometry_file` -- the same guards, the same staging
    write and the same atomic rename the OBJ path takes, because it is the same function -- and
    registers *that* file with `sources` naming what it was built from. A metre import registers the
    caller's own files in place and writes nothing at all, which is what `server_written=False` and
    an empty `sources` say on the record.

    There is no materials half here: a `.rad` may not carry a `material_map` (`_dispatch` refuses
    one), so `_write_geometry_file`'s body is empty and the rename is the only thing that can leave
    `01_geometry/` changed. `register_input(save=False)` plus one `save_manifest` all the same, so
    both branches leave the manifest written exactly once.
    """
    paths = staged.paths
    if server_written:
        # The only branch that stores text (`_write_geometry_file`), scanned before the lock.
        _scan_text_to_store(staged)
    with runtime.modifying(paths):
        # Re-read inside the lock, for the reason `Runtime.modifying` states: `save_manifest` writes
        # the whole document with no compare-against-disk, so what loses an update is the interval
        # between the read and the write, and a lock that does not cover the read covers nothing.
        current = open_project(paths.root, radiance_version=runtime.launcher.version)
        _refuse_stale_octree(current, staged)

        registered: list[Path] = list(files)
        stored: _StoredGeometry | None = None
        # **The stored copy this registration is about to orphan, found before the record moves.**
        # Whole-branch review, Minor 4: re-registering an id **in place** (`units="m"` after a
        # scaled import under the same `name`) sets `server_written=False`, and the previous
        # `01_geometry/<id>.rad` is then named by nothing -- `remove` will not delete it, because
        # the record it deregisters is no longer marked `server_written`, and `orphan_entries` walks
        # only the project root. A permanent leak inside the project, with no owner.
        #
        # The same three conditions `_removed`'s `doomed` uses, and for the same reasons: the
        # `server_written` mark, the single-file test, and `_same_file` identity against the derived
        # path -- so the recorded path is a predicate and can never redirect the delete. Nothing is
        # unlinked when this import writes its own copy, because the rename replaces it.
        orphaned = [
            paths.geometry_file(record.id)
            for record in current.manifest.inputs
            if not server_written
            and record.id == staged.input_id
            and record.kind == "geometry"
            and record.server_written
            and len(record.files) == 1
            and _same_file(Path(record.files[0].path), paths.geometry_file(record.id))
        ]
        if server_written:
            with _write_geometry_file(current, runtime, staged) as stored:
                pass
            registered = [stored.path]

        with _recording_the_import(stored):
            register_input(
                current,
                input_id=staged.input_id,
                kind="geometry",
                files=registered,
                # The unit the **caller declared**, not the unit the stored file is in: a scaled
                # file is always metres and `server_written=True` is what says so
                # (`InputRecord.server_written`). Recording "m" for a scene declared in feet would
                # erase the only record of what the source was, and `unscaled_geometry_records`
                # reads the pair, not `units` alone.
                units=units,
                # **No `role=` here, and that is ruling R32.** `geometry`'s five role values are not
                # `MaterialRole` members, and `register_input` raises `InvalidRoleError` for
                # anything that is not one. The assembly-time role is carried on the result and in
                # the `ToolCall` below, which is where a record of the call belongs.
                registered_by=ToolCall(tool="geometry", action="add", arguments=arguments),
                sources=[FileRef(path=s.path, digest=s.digest) for s in staged.sources],
                server_written=server_written,
                save=False,
            )
            save_manifest(paths, current.manifest)
        # **Manifest first, file second**, the asymmetry `_removed`'s docstring states: a save that
        # succeeds and an unlink that then fails leaves an unregistered file, which the next `add`
        # under that id replaces; the other order leaves a record naming a file that is gone, which
        # nothing can repair. Still inside the lock. `_unlink_contained` holds the containment and
        # `S_ISREG` guards and never raises.
        for path in orphaned:
            _unlink_contained(path, runtime.roots)
            _unlink_contained(_staging_path(path), runtime.roots)


def _checks(assembly: SceneAssembly, *, units_known: bool = True) -> ChecksView:
    """The `checks` block, every field read off something already computed.

    `undefined_modifiers` is always empty on a *successful* assembly -- `assemble_scene` raises
    `UndefinedModifierError` rather than returning one, so a non-empty list here would mean the
    engine changed its mind about that. It is reported anyway because docs/tool-surface.md §4 names
    it in the return shape, and a key that is present and empty is a different statement from a key
    that is absent.

    `coincident_pairs` (the field) is `len(coincident_pairs(...))` (the function), never
    `len(coincident_surfaces(...))`: `coincident_surfaces` caps at
    `COINCIDENCE_MAX_REPORTED_PAIRS + 1` advisories once the pair count exceeds the cap (one per
    pair, then one carrying the total), so counting its own output would publish a capped number
    (21 for a 200-pair scene) under a field docs/tool-surface.md §4 and this class both type
    `int | null` and document as a count, not a display limit.

    `units_known=False` (a `check` that declared no units) reports both counts as `null` rather than
    computing them: each rests on an absolute tolerance in metres, and a number produced over
    coordinates whose unit nobody stated would be a count of the wrong thing reported as a fact.
    `_ABSOLUTE_CHECKS` names them, and the result's `skipped` block says they were not asked.
    """
    return ChecksView(
        octree_non_empty=not assembly.octree.is_empty,
        enclosed=assembly.enclosed if assembly.polygons else None,
        undefined_modifiers=[],
        degenerate_polygons=len(degenerate_polygon_ids(assembly.polygons)) if units_known else None,
        coincident_pairs=len(coincident_pairs(assembly.polygons)) if units_known else None,
    )


def _cap_for(path: str) -> int | None:
    """The read cap that applies to a recorded source, by suffix, or `None` for no cap.

    The capped kinds are the ones whose *whole contents* an import parses in memory:
    `rad_core.obj.OBJ_MAX_BYTES` for an `.obj`, `rad_core.material_map.MATERIAL_MAP_MAX_BYTES`
    for its JSON map, and `rad_core.photometry_file.IES_MAX_BYTES` for an LM-63 `.ies` file
    (Phase 11a, ruling R506 -- one suffix-cap map, shared by `luminaire(action="list")`, rather than
    a second one in `rad_mcp.luminaire_tool`). **A `.rad` source is capped at `OBJ_MAX_BYTES` too**
    (ruling R828): the import caps a `.rad` scene's top-level files and `!xform` includes together at
    that bound (`_refuse_rad_over_cap`), so a single source past it is one the importer would now
    refuse, and `list` reports it without reading it. A *registered* file is never capped here
    (`_listed` passes `cap=None` for those) and is hashed through a streaming `handle_digest`, which
    holds one block rather than the file -- the same split `rad_engine.project.file_digest` makes.

    **All three names are read out of this module's globals on every call**, not captured at import,
    so the bound this drift check honours is the same object the importer honours even where a test
    patches it (`monkeypatch.setattr(geometry_tool, "OBJ_MAX_BYTES", ...)`, or `"IES_MAX_BYTES"`). A
    module-level dict would have frozen the first value any name ever had.
    """
    suffix = Path(path).suffix.lower()
    return {
        ".obj": OBJ_MAX_BYTES, ".rad": OBJ_MAX_BYTES, ".json": MATERIAL_MAP_MAX_BYTES, ".ies": IES_MAX_BYTES,
    }.get(suffix)


def drift(ref: FileRef, *, cap: int | None, roots: tuple[Path, ...]) -> str | None:
    """Why `ref` no longer holds the bytes recorded, or `None`.

    Read-only: it opens, hashes and closes, and writes nothing anywhere. `rad_mcp.paths.contain`
    runs first, so a recorded path that resolves outside every allowed root is *reported* rather
    than read -- the manifest is a file on disk and a hand-edited or relocated one must not turn
    `list` into a reader of arbitrary paths.

    **Every way the read can fail is a finding, never a raise.** `list` is the action a caller
    reaches for when something is already wrong, so a source that is missing, is a FIFO, or has
    lost its read permission has to be *reported* alongside the rest of the inventory rather than
    refuse the whole inventory. Each `except` below is one such way; the last line is the ordinary
    comparison.

    A capped source **larger than its cap is reported without being read** (design §5.11):
    `read_capped` takes the size off the open descriptor and refuses before any byte is consumed,
    so a source that grew past what the importer would accept costs a `fstat` here, not a read.

    **Nothing hangs.** `read_capped` and `open_regular_file` both open with `O_NONBLOCK` and then
    refuse a non-regular file on the *descriptor*, which is what makes a FIFO planted at a recorded
    path return `unreadable` instead of wedging an `anyio` worker thread (see
    `rad_engine.safe_io.open_regular_file` for the measurement).

    **`contain` resolves, so the bytes hashed are the link target's where a recorded path is now a
    symlink** -- and the containment verdict is taken on the resolved path, which is the boundary
    every path argument in this server already crosses. `O_NOFOLLOW` therefore never fires on the
    ordinary path and is defence against a link planted between the resolve and the open.
    """
    try:
        path = contain(ref.path, roots)
    except (PathOutsideRoot, RootsNotConfigured):
        return "no longer inside the allowed roots"
    try:
        if cap is not None:
            digest = bytes_digest(read_capped(path, cap))
        else:
            with open_regular_file(path) as handle:
                digest = handle_digest(handle)
    except FileTooLargeError:
        return f"larger than its {cap}-byte cap, so it was not read"
    except FileNotFoundError:
        return "missing"
    except OSError:
        return "unreadable (not a regular file, or not readable by this server)"
    return None if digest == ref.digest else "changed"


def _listed(runtime: Runtime, loaded: LoadedProject) -> CallToolResult:
    """Every registered geometry input, and what has happened to the files each one names.

    **`list` re-reads the disk, and that is the whole point of the action.**
    `rad_engine.project.is_reusable` already compares sealed input digests back against the bytes on
    disk (ruling R7), but only for a record some artifact's `built_from` names, only as one
    `valid: false` verdict about that artifact, and **never for `sources`** -- which
    `InputRecord.content_digest` excludes by design, so no digest anywhere downstream covers them.
    So an `.obj` edited after its import leaves the project describing geometry that no longer
    exists, with nothing that would say so: the stored metre copy is still the old shape, and every
    metric computed from it is about the old shape too. `GEOMETRY_SOURCE_CHANGED` is that comparison
    made visible, at `warn`: the record is still internally coherent (`files` and their digests
    still agree), so nothing is refused, but it is no longer the geometry the caller has open.

    **Both lists are checked, and they are different statements.** A drifted `source` says the
    import is out of date with respect to what it was built from; a drifted **`registered file`**
    says the bytes Radiance will actually read no longer match the digest every artifact's
    `built_from` was taken over -- which is the more serious of the two and is why the label is on
    the wire rather than left to be inferred from the suffix.

    A `.json` map earns one extra sentence, because the edit a caller makes there does not show up
    in the geometry at all: `materials` was written from the map at import time, so a changed map
    means the *project's materials* are stale, and re-importing is what applies it.

    `GEOMETRY_UNITS_NOT_APPLIED` rides along at `error` for a pre-Phase-10 record registered in
    place with a non-metre unit (`rad_engine.project.unscaled_geometry_records`). `list` **reports**
    it and never raises it -- neither does `remove`, `materials(action="list")` or
    `window_group(action="list")`, the reading and repairing actions
    `tests/test_unscaled_geometry_refusal.py::test_reading_and_repairing_tools_do_not_refuse` pins.
    The **run** tools refuse instead: `run_point_in_time`, `run_render` and `run_annual` through
    `point_in_time_tool._scene_files`'s one shared call, plus `run_glare` and
    `window_group(action="define")` at their own entries, each raising
    `rad_engine.project.refuse_unscaled_geometry`'s `GeometryUnitsNotAppliedError`. `list`'s report
    is not the only way to learn which input needs re-importing -- `project(action="describe")`
    surfaces the same record and its `units` too -- but it is the one that explains, at `error`
    severity, why a non-metre record matters rather than leaving a caller to notice a bare `ft`
    string in an otherwise ordinary field.
    """
    geometry_records = [record for record in loaded.manifest.inputs if record.kind == "geometry"]
    inputs = [
        GeometryInputView(
            id=record.id,
            files=[ref.path for ref in record.files],
            units=record.units,
            role=(record.registered_by.arguments.get("role") if record.registered_by else None),
            server_written=record.server_written,
            sources=[SourceView(path=ref.path, digest=ref.digest) for ref in record.sources],
        )
        for record in geometry_records
    ]
    advisories: list[Advisory] = []
    for record in geometry_records:
        for label, refs, capped in (
            ("registered file", record.files, False),
            ("source", record.sources, True),
        ):
            for ref in refs:
                reason = drift(ref, cap=_cap_for(ref.path) if capped else None, roots=runtime.roots)
                if reason is None:
                    continue
                stale = (
                    " The material values set from it are stale; re-import to apply the edit."
                    if ref.path.endswith(".json")
                    else ""
                )
                advisories.append(
                    Advisory(
                        code="GEOMETRY_SOURCE_CHANGED",
                        severity="warn",
                        field="files",
                        message=f"geometry input {record.id!r}: {label} {ref.path} is {reason}.{stale}",
                    )
                )
    for record in unscaled_geometry_records(loaded.manifest):
        advisories.append(
            Advisory(
                code="GEOMETRY_UNITS_NOT_APPLIED",
                severity="error",
                field="project",
                message=(
                    f"geometry input {record.id!r} is in {record.units} and was never scaled to "
                    "metres; every run refuses until it is re-imported."
                ),
            )
        )
    return emit(
        GeometryListed(
            project=loaded.paths.name,
            root=str(loaded.paths.root),
            inputs=inputs,
            advisories=advisories or None,
        )
    )


def _unlink_contained(path: Path, roots: tuple[Path, ...]) -> None:
    """Unlink `path` if -- and only if -- it is a **regular file** that provably lies inside one of
    `roots`. Anything else is left alone and logged; nothing raises.

    **`remove`'s one delete, for the reason `_refuse_non_regular` is this module's one path to
    `refuse_existing_non_regular_destination`**: a hand-written second spelling of a safety check is
    what let an earlier defect return at a new site. (`_write_geometry_file` unlinks too -- its own
    staging file, on the way out of a failed write -- and that one needs no containment check,
    because the path it removes is the path it just created inside an already-contained parent.)

    **`unlink` is not guarded by `rad_engine.safe_io` and cannot be.** That module's rule is
    *"every filesystem open in `rad_engine`, in either direction, goes through
    `open_regular_file`"*, and `unlink` opens nothing, so `O_NOFOLLOW` and the `fstat` on the
    descriptor have nothing to act on. Ruling R48 was worded around *opening*, and this repo has
    shipped the consequence of that wording: `server._remove_grid` records the gap at its own
    unlink. **`materials_tool._withdraw_files` no longer does, and this sentence used to say it
    did** (whole-branch review, Minor 9): that unlink is guarded by
    `materials_tool._contain_materials_destination`, so citing it here
    named a hole that is closed. So containment is checked **here,
    explicitly, in this function's first `if`** -- `resolved_parent_within(path, roots)` -- against
    the roots the caller's runtime declares.

    **What that check is enough for.** `resolved_parent_within` resolves the *parent chain* and
    requires it inside a root, which is the guard `O_NOFOLLOW` cannot supply: a `01_geometry` that
    is itself a symlink to another directory leads this unlink out of every root while the final
    component is not a symlink at all. The final component needs no resolution because `unlink`
    never follows it -- it removes the link, not the target -- so a resolved-contained parent plus a
    name is an inode inside that directory and nothing else. Both call sites also derive `path` from
    `ProjectPaths.geometry_file` (one of them through `_staging_path`), and
    `rad_engine.project._component` refuses `/` and `..` there, so the name cannot walk out of the
    directory it was resolved against either.

    **`S_ISREG` on the `lstat`, and fix round 1's finding 5 is why this is not `is_symlink()`.**
    The question this asks is *"is this a file this server wrote?"*, and the first version asked only
    about a symlink -- so a FIFO, a socket or a device node planted at `01_geometry/<id>.rad` was
    **unlinked** while being just as much not this server's file, and a directory there reached
    `unlink` and raised. A server-written copy arrives by `os.replace` of a regular staging file and
    is therefore always `S_ISREG`; everything else is somebody else's object and is declined.
    `lstat`, not `stat`, so a symlink is judged as a symlink rather than as whatever it points at --
    which is also what makes this strictly stronger than the `is_symlink()` it replaces.

    **It is a predicate, and `refuse_existing_non_regular_destination` is a refusal, which is why
    this is not a second spelling of that guard.** That one answers "may I *create* here?" and
    raises, before anything is written; this one answers "may I *delete* this?" after the manifest is
    already committed, where a raise would report a completed deregistration as a failure (below).
    Opposite direction, opposite failure mode, and one home each.

    **A missing path is silent, at either syscall.** `lstat` failing with `FileNotFoundError` means
    there is nothing to unlink, which is the ordinary case for the `.partial` on every healthy
    `remove` -- logging it would put a warning in the operator's log on every successful call. The
    `unlink` raising the same thing is the narrow race where an outside actor removed the file in
    between, and it is silent for the same reason (fix round 2, item 3): the post-condition holds, and
    "could not be unlinked" would misdescribe a file that is gone. Every *other* `OSError` from the
    `unlink` is logged.

    **An `OSError` from the `unlink` itself is logged, not raised, and fix round 1's finding 7 is
    why.** By the time this runs, `save_manifest` has committed the deregistration. A raise here
    escapes to `geometry`'s `except OSError` and publishes `refused: true` -- telling the caller the
    call failed about work that succeeded, and skipping the `.partial` unlink on the way out. The
    deregistration is the post-condition the caller asked for; a file left behind is a smaller,
    recoverable fact, and it belongs in the log rather than in a refusal.

    **A decline is logged, not published.** `GeometryRemoved` has no field for the files that went
    (see `_removed`), so the log is the only channel.
    """
    if not resolved_parent_within(path, roots):
        logger.warning(
            "not unlinking %s: its parent does not resolve inside any of %s. The input record is "
            "deregistered and the file is left.",
            path,
            roots,
        )
        return
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        return
    except (OSError, ValueError) as exc:
        logger.warning("not unlinking %s: it could not be stat'ed (%s).", path, exc)
        return
    if not stat.S_ISREG(mode):
        logger.warning(
            "not unlinking %s: it is not a regular file, so it is not a file this server wrote. "
            "The input record is deregistered and the file is left.",
            path,
        )
        return
    try:
        path.unlink()
    except FileNotFoundError:
        # Something outside this server removed it between the `lstat` above and here. The
        # post-condition holds, so this is silent for the same reason the absent case above is: a log
        # line saying a file "could not be unlinked" would misdescribe a file that is gone.
        return
    except OSError as exc:
        # The deregistration is already saved. See the docstring: this is reported to the operator,
        # never to the caller as a refusal.
        logger.warning("the input record is deregistered but %s could not be unlinked (%s).", path, exc)


def _removed(runtime: Runtime, loaded: LoadedProject, *, name: str) -> CallToolResult:
    """Deregister one geometry input **and delete the file this server wrote for it**.

    The manifest is edited here and written by `rad_engine.project.save_manifest`, under both locks:
    `rad_engine.project` has a `register_input` and no `unregister_input`, and the interval between
    the read and the write is what loses an update, so the whole span is held (`Runtime.modifying`).

    Removing an input that is not there is **not** refused: the post-condition the caller asked for
    already holds, and `removed: []` says so. A refusal would make a retry after a timeout report a
    failure for work that succeeded.

    **What this does to files on disk**, stated here because until Task 12 this function's own
    docstring said nothing about files at all while `_write_geometry_file`'s cited it as though it
    had: a record marked `server_written` whose one file *is* `01_geometry/<id>.rad` (by file
    identity -- see the comment on `doomed`) has that file deleted, along with the `_staging_path`
    beside it, orphaned there by an abrupt kill. **Every other
    file is left exactly where it is** -- an in-place `.rad` registered in metres
    (`server_written=False`) is the caller's own file and is never deleted, and neither is anything
    in `sources`, which names the caller's `.obj`, `.json` and includes. `_unlink_contained` is this
    function's only path to a delete, and it is where the containment check lives.

    **Manifest first, file second, and the asymmetry is deliberate.** A save that succeeds and an
    unlink that then fails leaves an unregistered file on disk: recoverable -- the next `add` under
    that id replaces it, and a user can delete it. The other order would leave the record naming a
    file that is gone, which is the state `is_reusable` reports as an invalid artifact rather than a
    missing input, and which nothing can repair. `server._remove_grid` makes the same trade for the
    same reason.

    **A `doomed` path this server may not safely delete is skipped, not refused**, which is where
    this diverges from `_remove_grid` (which raises `PathOutsideRoot` and removes nothing). A
    geometry record is load-bearing in a way a sensor grid is not -- now that
    `refuse_unscaled_geometry` is wired into every run tool, an unscaled one makes every run refuse
    except a `run_render(dry_run=True)`, which returns before the project is ever opened and so
    never reaches the gate (amendment 4 names three call sites on the reasoning that the shared
    reader is reached before any reuse decision, and a dry run makes none) -- so deregistration
    must not be blocked by a directory whose shape this server cannot vouch for. The same reasoning
    covers an `unlink` that raises *after* the save: `_unlink_contained` logs it rather than letting
    it escape, because the caller asked for a deregistration and got one. Both are logged rather
    than silent, and the ruling candidate in this task's report proposes publishing them on the
    wire.
    """
    paths = ProjectPaths(loaded.paths.root)
    candidates = {name, _INPUT_PREFIX + name}
    with runtime.modifying(paths):
        # Re-read inside the lock, for the reason `Runtime.modifying` states: `save_manifest` writes
        # the whole document with no compare-against-disk, so what loses an update is the interval
        # between the read and the write, and a lock that does not cover the read covers nothing.
        current = open_project(paths.root, radiance_version=runtime.launcher.version)
        removed = [
            record.id
            for record in current.manifest.inputs
            if record.kind == "geometry" and record.id in candidates
        ]
        if removed:
            # Refused before the record is touched. An artifact whose `built_from` names a removed
            # input makes the **whole manifest** unevaluable, not just that artifact -- every later
            # call that opens the project fails. This hole predates the fix round that found it and
            # is closed here because the guard is shared with `materials`' own withdrawal, which had
            # the identical shape (fix round 3, finding N2).
            refuse_if_built_from(current.manifest, removed)
            # **Collected before the filter below, because the filter is what drops the records
            # this reads.** Three conditions, and each rules out a different record: the
            # `server_written` mark says this server wrote the bytes; the single-file test says the
            # record is nothing but that copy (a multi-file record is the caller's scene set, even
            # if one entry happens to land here); and the identity test says the bytes it names are
            # the ones at `01_geometry/<id>.rad`. What is unlinked is always the **derived** path,
            # never the recorded one, so the recorded path is a predicate and cannot redirect the
            # delete anywhere.
            #
            # **`_same_file`, not `f.path == str(...)` -- fix round 1's finding 3.** The first
            # version compared path strings and justified it as "both sides are absolute and
            # resolved". That was **false**: `Runtime.locate` concatenates the caller's own spelling
            # of `project` and `Path.resolve()` does not case-fold, so on a case-insensitive
            # filesystem `remove(project="P")` against a project created as `"p"` produced two
            # spellings of one path, matched nothing, and left the stored copy on disk while
            # reporting success -- with no log line, because this loop never reached
            # `_unlink_contained` to write one. `_same_file` compares `(st_dev, st_ino)` and is the
            # spelling Task 11 fix round 3 added for exactly this blindness; it is strictly safe
            # here, because identity-equal means the record names that inode.
            doomed = [
                paths.geometry_file(record.id)
                for record in current.manifest.inputs
                if record.id in removed
                and record.server_written
                and len(record.files) == 1
                and _same_file(Path(record.files[0].path), paths.geometry_file(record.id))
            ]
            current.manifest.inputs = [
                record for record in current.manifest.inputs if record.id not in removed
            ]
            save_manifest(paths, current.manifest)
            for path in doomed:
                # Still inside the lock, so no `add` can be staging a `.partial` for this id while
                # the orphan of an earlier one is being removed.
                _unlink_contained(path, runtime.roots)
                _unlink_contained(_staging_path(path), runtime.roots)
        else:
            # **The orphan of an interrupted *first* import, which has no record at all.**
            # Whole-branch review Important 6. `.partial` cleanup lived only inside `if removed:`,
            # so it reached the case where a record exists and missed the one where it does not --
            # and "no record" is the **most reachable** abrupt-kill case, because a first import
            # under a name is exactly when the destination does not yet exist. Measured: with a
            # record present the `.partial` is deleted; with a `.partial` and no record, `remove`
            # returned `removed []` and the file survived. `orphan_entries` walks only the project
            # root, so `project(action="describe")` never reported it either, while this tool's own
            # description promises `remove` deletes it.
            #
            # The published sentence is kept and made true rather than narrowed: `remove` is the
            # only thing that owns this path's lifecycle, and narrowing the promise would leave the
            # orphan with no owner at all -- the state Task 12's `.partial` ownership decision was
            # made to end.
            #
            # Safe with no record to authorise it because nothing here is derived from the manifest:
            # the path is `_staging_path(paths.geometry_file(_INPUT_PREFIX + name))`, built from the
            # validated `name` alone, and `_unlink_contained` holds the containment and `S_ISREG`
            # guards. Still inside the lock, for the same reason the branch above is.
            #
            # `removed` stays `[]`: the caller asked which *inputs* were deregistered and none was.
            _unlink_contained(
                _staging_path(paths.geometry_file(_INPUT_PREFIX + name)), runtime.roots
            )
    return emit(
        GeometryRemoved(
            project=loaded.paths.name, root=str(loaded.paths.root), removed=removed
        )
    )
