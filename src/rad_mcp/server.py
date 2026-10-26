"""MCP server.

Thin by design: argument plumbing, docstrings, and result-side advisories. No physics lives here,
and no subprocess is spawned here -- that is `rad_engine`'s job. See CONTEXT.md.

Speaks MCP revision **2026-07-28** on the official SDK (`mcp` >= 2.0), per the build-server guide.
Two protocol rules bind everything in this module, both in docs/tool-surface.md#protocol-conformance:

- **Nothing writes to stdout.** On stdio, stdout *is* the JSON-RPC transport: *"Never write to
  stdout. Writing to stdout will corrupt the JSON-RPC messages and break your server."* Logging
  goes to stderr through the standard library. This is a different rule from `rad_engine`'s
  "stderr is never merged into stdout", and both fail the same way -- silently, then completely.
- **Results are built in one place.** Every tool returns through `envelope.emit`, which carries the
  declared output schema and sets `isError`.
"""

from __future__ import annotations

import errno
import importlib.metadata
import inspect
import logging
import os
import re
import secrets
import stat
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Annotated, Any, Final, Literal

from mcp.server import MCPServer
from mcp_types import CallToolResult, ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field, RootModel, StrictFloat

from rad_core.grids import (
    GRID_VERY_LARGE_POINTS,
    GridError,
    GridPoint,
    GridSpec,
    Standard,
    place_sensor_grid,
    plan_grid,
    refuse_extra_ordinates,
)
from rad_core.geometry import far_from_origin_warning
from rad_core.grid_zones import TaskZone, plan_zones
from rad_core.lm83_edition import DEFAULT_LM83_EDITION, Lm83Edition
from rad_core.manifest import ProvenanceValue, ToolCall, ZoneRecord
from rad_engine import (
    ProjectError,
    ProjectPaths,
    RadianceNotFound,
    discover,
    open_project,
    register_input,
    save_manifest,
    stage_points,
)
# The same route `rad_mcp.weather_tool` and `rad_mcp.annual_tool` already take for ruling R48's
# primitive -- an established boundary crossing, not a new one.
from rad_engine.safe_io import (
    NotARegularFileError,
    open_regular_file,
    open_staging_file,
    refuse_existing_non_regular_destination,
    resolved_parent_within,
)
from rad_mcp import runtime, stop_signals
from rad_mcp.advisories import warnings_to_wire
from rad_mcp.envelope import (
    Advisory,
    RadResult,
    Refusal,
    emit,
    one_of_schema,
    refuse,
    refuse_missing,
)
from rad_mcp.annual_tool import run_annual
from rad_mcp.geometry_tool import geometry
from rad_mcp.job_tool import job
from rad_mcp.materials_tool import InputStillBuiltFrom, materials, refuse_if_built_from
from rad_mcp.names import (
    SENSOR_GRID_INPUT_PREFIX,
    NAME_PATTERN,
    is_legal_name,
    name_refusal_reason,
)
from rad_mcp.paths import (
    CodedWriteRefusal,
    PathOutsideRoot,
    ProjectWriteFailed,
    RootsNotConfigured,
    SensorGridFilesReplaced,
    write_failure,
)
from rad_mcp.point_in_time_tool import (
    RETIRED_ARGUMENTS as POINT_IN_TIME_RETIRED_ARGUMENTS,
    run_point_in_time,
)
from rad_mcp.project_tool import project
from rad_mcp.results_tool import results
from rad_mcp.tasks import Tasks
from rad_mcp.unknown_arguments import RetiredMessage, refuse_unknown_arguments
from rad_mcp.bsdf_tool import bsdf
from rad_mcp.weather_tool import weather
from rad_mcp.window_group_tool import window_group
from rad_mcp.image_tool import image
from rad_mcp.glare_tool import run_glare
from rad_mcp.quality_tool import quality
from rad_mcp.render_tool import run_render
from rad_mcp.export_tool import export
from rad_mcp.luminaire_tool import luminaire
from rad_mcp.electric_tool import run_electric
from rad_mcp.ugr_tool import run_ugr
from rad_mcp.view_sunlight_tool import (
    RETIRED_ARGUMENTS as VIEW_SUNLIGHT_RETIRED_ARGUMENTS,
    run_view_and_sunlight,
)

logger = logging.getLogger(__name__)

SERVER_TITLE: Final = "Radiance simulation"
_UNKNOWN_VERSION: Final = "0+unknown"


def _distribution_version() -> str:
    """The installed `rad-mcp` distribution's version, which is `pyproject.toml`'s (R793).

    Read from the installed metadata rather than restated here, so `serverInfo.version` cannot
    drift from the package. A run with no installed `rad-mcp` metadata reports `0+unknown`, never
    the SDK's empty default, and never refuses to start.
    """
    try:
        return importlib.metadata.version("rad-mcp")
    except importlib.metadata.PackageNotFoundError:
        logger.warning(
            "rad-mcp is not installed as a distribution; serverInfo.version is %s", _UNKNOWN_VERSION
        )
        return _UNKNOWN_VERSION

INSTRUCTIONS = """\
Radiance simulation for daylight and artificial lighting studies.

Start with `radiance_status` -- it reports whether the Radiance install can support the workflow
you need, and there is no point building a scene it cannot run.

Then `project(action="create")`. Every other tool operates against a named project, because matrix
reuse only works if provenance is tracked: change a glazing BSDF and the T matrix is invalidated
while V and D survive, which is the entire value of the 3-phase method.
`project(action="describe")` shows what is still valid and what will be rebuilt.

`run_annual` is asynchronous: it returns a job id and `job(action="status")` polls it; a 5-phase
annual run outlives any client timeout. Every other tool returns its result, or a refusal, from the
call itself; a `job_id` such a result carries (`inline-...`) names no job, and `job` has no record
of it. `job(action="logs")` reads a `run_annual` job's stderr by default, because that is where
Radiance explains itself -- a Radiance process that exits 0 having written warnings is the most
common way to get a confidently wrong answer.

Three rules when reporting results from these tools:

- Report the metric the relevant standard names. UGR is for electric lighting indoors, GR for
  outdoors, DGP for daylight. EN 17037 forbids substituting UGR for DGP.
- Never present a substituted default as a measured value. Where a tool reports `provenance`,
  a `default` is an assumption, not a property of the building.
- Simulated illuminance is *initial*, not maintained. EN 12464-1's `E_m` is a maintained value, so
  comparing a raw result to it compares a brand-new installation against a maintained threshold;
  a maintenance factor (typically 0.67-0.80) has to be applied and reported first.

EN 17037 Annex A is informative: say "meets the medium recommendation level", not "passes".
"""

# `extensions=[Tasks()]` is the whole of the Tasks wiring, and it is a **protocol** capability, not
# a tool: the extension serves `tasks/get`, `tasks/result`, `tasks/cancel` and `tasks/list` over the
# one job store and contributes no tool, so the registered set below is unchanged and check 0.7's
# *"the tool list does not move"* still holds. `Extension.tools()` exists and is deliberately not
# used (docs/tool-surface.md#long-running-work-job-is-the-fallback-not-the-interface,
# `rad_mcp.tasks`).
#: Retired arguments per tool: a name a tool no longer takes, and the message that says what replaced
#: it (ruling R716). Consulted by `RadMCPServer.call_tool` before the generic unknown-argument message.
RETIRED_ARGUMENTS: dict[str, Mapping[str, RetiredMessage]] = {
    "run_view_and_sunlight": VIEW_SUNLIGHT_RETIRED_ARGUMENTS,
    "run_point_in_time": POINT_IN_TIME_RETIRED_ARGUMENTS,
}


class RadMCPServer(MCPServer):
    """The SDK server, with one addition: an argument a tool does not name is refused (R713).

    The SDK's argument model ignores extra keys (pydantic's default), so a misspelt optional
    argument silently ran with its default. `call_tool` is the one method both the wire path
    (`_handle_call_tool`) and in-process callers go through, so the check lives there and nowhere
    else. The SDK exposes no public accessor for a registered `Tool`; `_tool_manager` is pinned by
    `tests/test_unknown_arguments.py::test_the_sdk_attribute_both_overrides_use_still_exists`.
    """

    def add_tool(self, fn: Any, name: str | None = None, **kwargs: Any) -> None:
        # R797: publish the docstring without its code indentation: 4,249 characters of whitespace
        # (indentation and each docstring's trailing newline), measured on the snapshot.
        # Everything else, `title` and `annotations` included, passes through unchanged.
        if kwargs.get("description") is None and fn.__doc__:
            kwargs["description"] = inspect.cleandoc(fn.__doc__)
        super().add_tool(fn, name=name, **kwargs)
        tool = self._tool_manager.get_tool(name or fn.__name__)
        tool.parameters = {**tool.parameters, "additionalProperties": False}

    async def call_tool(
        self, name: str, arguments: dict[str, Any], context: Any = None
    ) -> Any:
        tool = self._tool_manager.get_tool(name)
        if tool is not None:
            refusal = refuse_unknown_arguments(
                name,
                arguments or {},
                tool.parameters.get("properties", {}).keys(),
                RETIRED_ARGUMENTS.get(name),
            )
            if refusal is not None:
                return refusal
        return await super().call_tool(name, arguments, context)


mcp = RadMCPServer(
    "rad-mcp",
    title=SERVER_TITLE,
    version=_distribution_version(),
    instructions=INSTRUCTIONS,
    extensions=[Tasks()],
    lifespan=stop_signals.lifespan,
)


# --- result shapes -------------------------------------------------------------------------
# These are the tools' `outputSchema`s. A `dict[str, Any]` return would publish a schema that
# accepts anything, which tells a client nothing and can never fail.


class RadianceStatusResult(RadResult):
    """What `radiance_status` returns.

    `found: false` means no Radiance installation was found: `error` says why, `version`,
    `bin_dir`, `lib_dir` and `capabilities` are absent, and the result has `isError: true`
    (`RADIANCE_NOT_FOUND`). With `found: true`, `capabilities` maps each workflow to whether all
    its binaries are present; `version` is absent when it could not be read, and `lib_dir` is
    absent when the library directory was not found (`RADIANCE_LIB_NOT_FOUND`).
    """

    found: bool
    version: str | None = None
    bin_dir: str | None = None
    lib_dir: str | None = None
    capabilities: dict[str, bool] | None = None
    error: str | None = None

    OMIT_WHEN_NONE = frozenset(
        {"advisories", "version", "bin_dir", "lib_dir", "capabilities", "error"}
    )


# Ruling R717 (Phase 13 Branch D). Before Branch D this tool could not refuse, so its schema
# rejected `refused`/`reason` and the unknown-argument refusal would have failed its own tool's
# schema. Kept out of the docstring because pydantic publishes the docstring as the output
# schema's `description`, which clients read.
class RadianceStatusResponse(RootModel[RadianceStatusResult | Refusal]):
    """What `radiance_status` returns: the status, or a refusal of an argument it does not name."""

    @classmethod
    def model_json_schema(cls, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return one_of_schema(super().model_json_schema(*args, **kwargs), RadianceStatusResult)


class GridSpacing(BaseModel):
    # Nested shapes are plain models: only a top-level result carries `advisories`.
    model_config = ConfigDict(extra="forbid")

    x: float
    y: float


class GridPoints(BaseModel):
    model_config = ConfigDict(extra="forbid")

    x: int
    y: int
    total: int


# R810 moved this from the published text. Deliberately unchanged by the `create` upgrade, down to
# the key set: check 0.8 compares this payload byte for byte against the golden captured before the
# SDK port ("a protocol port that quietly changes a grid spacing is the failure this exists to make
# impossible"). `create`'s extra keys live on `SensorGridCreated` rather than as optional fields
# here, so a `plan` result cannot acquire a `height_m` and a `create` result cannot omit one.
class SensorGridResult(RadResult):
    """`sensor_grid(action="plan")`: the grid a `width` by `depth` rectangle gets under `standard`.

    Nothing is written. `rule` is the spacing rule applied. Under EN 12464-1 and EN 17037,
    `governing_dimension_m` is the `d` the rule used: the shorter side when `GRID_ASPECT_SWAPPED` is
    raised. LM-83's fixed spacing uses no `d`; the field then reports the longer side.
    """

    standard: str
    max_cell_size_m: float
    spacing_m: GridSpacing
    points: GridPoints
    governing_dimension_m: float
    cell_aspect_ratio: float
    rule: str


# R810 moved this from the published text: `height_m` as a provenance object is check 2.7's wire
# half; the rectangular case of `points_inside == points.total` is what check 2.6 asserts on; the
# floor-z rule is R763.
class SensorGridCreated(RadResult):
    """`sensor_grid(action="create")`: the grid placed in `polygon` and written to `pts_file`.

    `height_m` is the reference-plane height above the floor. Omitted, it is the standard's own
    (EN 17037 0.85 m, LM-83 0.8 m or 0.76 m under LM-83-22), labelled `default` with the clause that
    states it; a height given is labelled `assumed`. EN 12464-1 has no default, so omitting `height`
    under it is refused (`GRID_TASK_HEIGHT_REQUIRED`). `floor_z_m` is the floor polygon's z; every
    point sits at `floor_z_m + height_m.value`.

    `points.total` is the circumscribing-rectangle grid before clipping to the polygon and
    `points_inside` what survived it; the `.pts` file has `points_inside` lines.
    """

    standard: str
    max_cell_size_m: float
    spacing_m: GridSpacing
    points: GridPoints
    governing_dimension_m: float
    cell_aspect_ratio: float
    rule: str
    height_m: ProvenanceValue
    floor_z_m: float
    points_inside: int
    points_clipped: int
    pts_file: str
    project: str
    root: str
    name: str
    input_id: str


# R810 moved this from the published text: the manifest records an absent zone with
# `file_index: null` (Phase 11b design §4, *Record*).
class ZoneRow(BaseModel):
    """One zone of a zoned EN 12464 grid.

    `pts_file` is `null` for an absent zone, one that holds no point (`points` is 0): no file is
    written for it. `spacing_m` is the spacing placed and `formula_spacing_m` the EN 12464-1
    formula's for the zone's rectangle; both are `null` when the zone has no rectangle.
    `ribbon_clamped` is `true` when a band surround's spacing was capped at half the band's
    narrowest width.
    """

    model_config = ConfigDict(extra="forbid")

    zone: str
    group: str | None
    points: int
    spacing_m: GridSpacing | None
    formula_spacing_m: GridSpacing | None
    ribbon_clamped: bool
    pts_file: str | None


# A separate model rather than optional fields on `SensorGridCreated` is the argument
# `SensorGridResult`'s comment makes for `plan` against `create`, one level further out: an unzoned
# `create` keeps its schema and its payload byte for byte (Phase 11b design §4). That pointer left
# the published description under R809 (item 38). R810 moved the rest: `zoned` is a plain `bool`
# rather than `Literal[True]` because a single-valued constant would read to a schema guard as a
# discriminator (`tests/test_protocol.py::test_no_inlined_base_carries_a_single_valued_discriminator`);
# the floor-z rule is R763.
class SensorGridZonedCreated(RadResult):
    """`sensor_grid(action="create")` with `task_zones` or `task_layout`: an EN 12464-1 zoned grid.

    One `.pts` file is written per zone that holds points. `zones` lists every zone, absent ones
    included; `points_total` is the sum over zones. `zoned` is always `true`. `surround_mode` is
    `null` when `task_layout="unknown"`. `floor_z_m` is the floor polygon's z; every point sits at
    `floor_z_m + height_m.value`.
    """

    project: str
    root: str
    input_id: str
    standard: str
    name: str
    zoned: bool
    layout: Literal["task_zones", "unknown"]
    surround_mode: Literal["band", "rest_of_room"] | None
    spacing_rule: str
    wall_strip: bool
    zones: list[ZoneRow]
    points_total: int
    height_m: ProvenanceValue
    floor_z_m: float


class SensorGridRow(BaseModel):
    """One registered sensor grid: `id` is `sensor_grid_<name>`, `files` its `.pts` files."""

    model_config = ConfigDict(extra="forbid")

    id: str
    files: list[str]


class SensorGridListed(RadResult):
    """`sensor_grid(action="list")`: the project's registered sensor grids, empty when it has none."""

    project: str
    root: str
    grids: list[SensorGridRow]


class SensorGridRemoved(RadResult):
    """`sensor_grid(action="remove")`: the grid is deregistered and its `.pts` files deleted.

    `removed` lists the input ids removed, and is empty when no grid had that name (not a refusal).
    `removed_files` lists the files deleted. A grid something was built from is refused, and
    nothing is changed.
    """

    project: str
    root: str
    removed: list[str]
    removed_files: list[str] = []
    """The files unlinked with the records, absolute. Empty when nothing matched.

    Published rather than implied: until 2026-08-31 this action deregistered the input and left the
    `.pts` on disk (ruling R199's disk half), and a caller reading `removed` alone could not tell
    which of the two behaviours they had. Naming the files is what makes the change observable from
    the wire instead of only from the filesystem."""


#: `sensor_grid`'s shapes: the plan it built, the grid it placed, the project's grids, a removal, or
#: a refusal. Before this existed, `sensor_grid`'s
#: return annotation was `SensorGridResult` alone -- a single, non-union shape -- so a `GridError`
#: refusal (however correctly `refuse_engine_error` built it) failed the SDK's own validation against
#: that one shape and surfaced *worse* than the uncaught exception it was meant to replace: a
#: `ToolError` wrapping a nine-field pydantic validation dump, rather than either a clean refusal or
#: a clean exception message (fix round 1 of Task 1's code review, finding I4 -- caught only by
#: actually exercising the fix end to end through a real tool call, not by reading the `except`
#: block). `project`/`job` already solved exactly this with `one_of_schema` over a `RootModel` union
#: that includes `Refusal`; `SensorGridResponse` below is the same pattern.
SensorGridPayload = (
    SensorGridResult
    | SensorGridCreated
    | SensorGridZonedCreated
    | SensorGridListed
    | SensorGridRemoved
    | Refusal
)


# R810 moved this from the published text: declared as the tool's return annotation, so the SDK
# validates every payload against it before it reaches the wire. `ProjectResult`/`JobResult` in
# `project_tool.py`/`job_tool.py` are built the same way; `one_of_schema` in `envelope.py` does the
# schema rewrite.
class SensorGridResponse(RootModel[SensorGridPayload]):
    """What `sensor_grid` returns: a plan, a grid or zoned grid placed, the grids listed, a removal, or a refusal."""

    @classmethod
    def model_json_schema(cls, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return one_of_schema(super().model_json_schema(*args, **kwargs), SensorGridResult)


# --- tools ---------------------------------------------------------------------------------


def radiance_status(
    bin_dir: Annotated[
        str | None,
        Field(
            description=(
                "optional explicit path to the Radiance `bin` directory. Otherwise $RADIANCE_BIN, "
                "then PATH, then the usual install prefixes are tried."
            )
        ),
    ] = None,
) -> RadianceStatusResponse:
    """Report the Radiance installation and which workflows it can support.

    Run this first. Each capability maps to binaries that must all be present; a missing one
    disables that workflow rather than the whole server.
    """
    try:
        env = discover(bin_dir)
    except RadianceNotFound as exc:
        logger.info("no Radiance installation found: %s", exc)
        return emit(
            RadianceStatusResult(
                found=False,
                error=str(exc),
                advisories=[
                    Advisory(
                        code="RADIANCE_NOT_FOUND",
                        severity="error",
                        message=(
                            "No Radiance installation found. Install Radiance, or pass bin_dir."
                        ),
                    )
                ],
            ),
            # The call could not do what its name says. Actionable: the model can retry with a
            # bin_dir, which is exactly the self-correction `isError` exists to enable.
            is_error=True,
        )

    capabilities = env.capabilities()
    missing = [name for name, ok in capabilities.items() if not ok]

    advisories: list[Advisory] = []
    if env.lib_dir is None:
        advisories.append(
            Advisory(
                code="RADIANCE_LIB_NOT_FOUND",
                severity="error",
                message=(
                    "Could not locate the Radiance library directory (no rayinit.cal). Ray tracing "
                    "will fail with 'cannot find function file'. Set RAYPATH or reinstall."
                ),
            )
        )
    if missing:
        advisories.append(
            Advisory(
                code="CAPABILITY_UNAVAILABLE",
                severity="warn",
                field="capabilities",
                message=f"Unavailable workflows: {', '.join(sorted(missing))}.",
            )
        )

    # Not `is_error`: the install was found and described. The advisories qualify the install,
    # not the call.
    return emit(
        RadianceStatusResult(
            found=True,
            version=env.version,
            bin_dir=str(env.bin_dir),
            lib_dir=str(env.lib_dir) if env.lib_dir else None,
            capabilities=capabilities,
            advisories=advisories or None,
        )
    )


#: Named in the tool description: `sensor_grid` gained `action="remove"` in Phase 2, and
#: *"any tool with a destructive action is annotated destructive and its description names which
#: actions those are"* (docs/tool-surface.md#annotations-are-coarser-than-the-surface).
SENSOR_GRID_DESTRUCTIVE_ACTIONS: tuple[str, ...] = ("remove",)


#: `p = ...` for each standard, verbatim from the formulas in CONTEXT.md's own summary. Reported so
#: a caller can see which of the two coefficients was applied -- the whole reason `standard` has no
#: default.
_RULES: dict[str, str] = {
    "en12464": "p = 0,2 x 5^log10(d), points at cell centres",
    "en17037": "p = 0,5 x 5^log10(d) (Annex B.2)",
    # 0.6 m, not 24in/0.6096m: ch04 states the bound both ways (24 in, and directly as
    # 0.6 m) and 0.6 m is the stricter of the two (owner ruling R22,
    # rad_core.grids._LM83_MAX_SPACING_M). The wall offset is a separate, independent
    # band -- 12-24in is unaffected by which spacing reading governs the pitch.
    "lm83": "fixed spacing <= 0.6 m on centre, 12-24in wall offset",
}


def _grid_advisories(spec: GridSpec, n_points: int) -> list[Advisory]:
    """The two findings a grid's own shape produces, shared by `plan` and `create`.

    Shared rather than written twice: a `create` call that reported neither would leave a caller who
    used the placing action blind to exactly the two things the planning action tells them.
    """
    advisories: list[Advisory] = []
    if spec.aspect_swapped:
        advisories.append(
            Advisory(
                code="GRID_ASPECT_SWAPPED",
                severity="info",
                field="governing_dimension_m",
                message=(
                    f"Surface aspect ratio is 2 or more, so d is the shorter side "
                    f"({spec.governing_dimension} m), not the longer one. This makes the grid finer."
                ),
            )
        )
    if n_points > GRID_VERY_LARGE_POINTS:
        advisories.append(
            Advisory(
                code="GRID_VERY_LARGE",
                severity="warn",
                field="points",
                message=f"{n_points} sensor points will make annual simulation slow.",
            )
        )
    return advisories


def _site_advisory(points: Sequence[GridPoint]) -> list[Advisory]:
    """Ruling R827: `COORDINATES_FAR_FROM_ORIGIN` over the points a `create` placed, the finding
    `geometry` reports over a scene, on `polygon` -- the argument the points came from."""
    warning = far_from_origin_warning(((p.x, p.y, p.z) for p in points), field="polygon")
    return warnings_to_wire([warning]) if warning is not None else []


def sensor_grid(
    action: Annotated[
        Literal["plan", "create", "list", "remove"],
        Field(
            description=(
                "\"plan\" sizes a grid over a bare rectangle and returns spacings and counts, no "
                "coordinates and no file. \"create\" places the points inside a room polygon, "
                "writes the `.pts` file and registers it. \"list\" reports a project's grids; "
                "\"remove\" deregisters one **and unlinks its `.pts`**, publishing the absolute "
                "paths it deleted as `removed_files` so the change is visible on the wire and not "
                "only on disk. A grid any artifact was built from is refused instead "
                "(ARTIFACT_EDGES_INCOMPLETE): deregistering it would leave that artifact's "
                "provenance naming an input that no longer exists."
            )
        ),
    ],
    standard: Annotated[
        Literal["en12464", "en17037", "lm83"] | None,
        Field(
            description=(
                "REQUIRED on \"plan\" and \"create\". **Pick \"lm83\" for any grid an LM-83 blind "
                "schedule will be read off.** Under LM-83-12, section 2.2.6's small-area rows are "
                "point counts calibrated on LM-83's 0.6 m grid, so below a 1 000 sf analysis area "
                "`run_annual(..., blind_control=\"lm83\")` **refuses to plan the blend** on a "
                "materially sparser grid, `results(action=\"blind_schedule\")` refuses to derive "
                "a schedule over it, and every read of a sealed blend carries "
                "`BLIND_TRIGGER_MIXED_BASIS` at `error`. Under LM-83-22 (no area rows) none of "
                "this applies, except on a blend with no schedule record."
            )
        ),
    ] = None,
    project: Annotated[
        str | None,
        Field(
            description=(
                "REQUIRED except on \"plan\" -- a grid has to be written into something."
            )
        ),
    ] = None,
    name: Annotated[
        str | None,
        Field(
            description=(
                "the grid's handle inside the project. REQUIRED on \"remove\"; defaults to "
                "\"grid\" on \"create\"."
            )
        ),
    ] = None,
    width: Annotated[
        StrictFloat | None,
        Field(
            description=(
                "extent of the reference surface in metres, for \"plan\". Both must be finite "
                "and positive; neither refusal needs a project."
            )
        ),
    ] = None,
    depth: Annotated[
        StrictFloat | None,
        Field(
            description=(
                "extent of the reference surface in metres, for \"plan\". Both must be finite "
                "and positive; neither refusal needs a project."
            )
        ),
    ] = None,
    polygon: Annotated[
        list[list[StrictFloat]] | None,
        Field(
            description=(
                "the room outline for \"create\" -- a list of [x, y] or [x, y, z] vertices in "
                "metres. The vertices must be level (one z, within 1 mm; GRID_FLOOR_NOT_LEVEL "
                "otherwise), and `height` is measured above that z ([x, y] vertices are at z = "
                "0). Points that fall inside the circumscribing rectangle but outside this outline "
                "are dropped, not reported: they see unobstructed sky and read systematically "
                "bright. Every ordinate must be finite, and a polygon whose circumscribing "
                "rectangle would need more candidate points than this server will size a matrix "
                "for is refused rather than placed."
            )
        ),
    ] = None,
    height: Annotated[
        StrictFloat | None,
        Field(
            description=(
                "reference-plane height in metres. Measured above the polygon's floor, not as an "
                "absolute z. Omitted, EN 17037 uses 0.85 m and LM-83 0.8 m, or 0.76 m with "
                "`lm83_edition=\"LM-83-22\"`, each labelled `default` with the clause that states "
                "it. EN 12464-1's task area may be "
                "horizontal, vertical or inclined, so it has no default and omitting `height` "
                "there is refused rather than substituted. It must be finite and zero or more "
                "(ARGUMENT_OUT_OF_RANGE, `field: \"height\"`, R783); zero is a floor-level plane."
            )
        ),
    ] = None,
    normal: Annotated[
        list[StrictFloat] | None,
        Field(
            description=(
                "sensor direction [dx, dy, dz] for every point. Defaults to [0, 0, 1], straight "
                "up. A zoned grid lies on one horizontal plane, so there it must be omitted or "
                "[0, 0, 1]."
            )
        ),
    ] = None,
    task_zones: Annotated[
        list[dict[str, Any]] | None,
        Field(
            description=(
                "EN 12464 only (\"create\", standard=\"en12464\"): task areas as "
                "`{\"rect\": [x0, y0, x1, y1], \"group\": name}`, at most 64, each inside the room "
                "and none overlapping another; `group` defaults to \"1\". Each group gets one "
                "surround. The grid is written as one `.pts` file per zone under "
                "`11_sensors/<name>.zones/`, and a zoned grid is read by `run_electric`, not by "
                "`run_point_in_time` or `run_annual`."
            )
        ),
    ] = None,
    task_layout: Annotated[
        Literal["unknown"] | None,
        Field(
            description=(
                "\"unknown\": the whole room less the 0.5 m wall strip is one task area, "
                "`task_whole` (licht.de a.2). Give task_zones or task_layout, not both."
            )
        ),
    ] = None,
    surround: Annotated[
        dict[str, Any] | Literal["rest_of_room"] | None,
        Field(
            description=(
                "`{\"width\": w}` with w >= 0.5 (default 0.5, EN 12464-1's band around the "
                "group's task areas; the rest of the room less the strip is the background), or "
                "\"rest_of_room\", ASR A3.4's geometry (one group only; no background). Valid only "
                "with task_zones."
            )
        ),
    ] = None,
    wall_strip: Annotated[
        bool,
        Field(
            description=(
                "exclude the 0.5 m strip along the walls from surrounds and the background (never "
                "from a task area). Zoned grids only; ignored on an unzoned create."
            )
        ),
    ] = True,
    lm83_edition: Annotated[
        Lm83Edition | None,
        Field(
            description=(
                "lm83: the LM-83 edition the default height comes from, \"LM-83-12\" (default, "
                "0.8 m) or \"LM-83-22\" (0.76 m, sections 4.1.2 and 4.2.1). Read only by "
                "\"create\"; refused for any other action or standard. An explicit `height` wins."
            )
        ),
    ] = None,
) -> SensorGridResponse:
    """Plan a standards-compliant calculation grid, and write its `.pts` file.

    Use "plan" to size a grid before a room exists, "create" to write the grid a run reads --
    `run_point_in_time`, `run_annual` (whose result `results` reads by the same grid name) and
    `run_view_and_sunlight`'s view-out axis; a zoned EN 12464 grid is read by `run_electric` -- and
    "list" to see a project's grids.

    `standard` is required and has no default on purpose. EN 12464 and EN 17037 use the same
    formula with different coefficients (0,2 and 0,5), so the wrong one yields a plausible grid of
    the wrong density with no error anywhere.

    DESTRUCTIVE ACTIONS: `remove` deregisters a sensor grid from a project **and deletes the
    `.pts` file it wrote**. It refuses first, with ARTIFACT_EDGES_INCOMPLETE, if any artifact is
    built from the grid.

    Does NOT convert between standards. sDA and EN 17037 daylight provision both use 300 lux and
    50 % of the time, and they are not convertible: the analysis periods, blind assumptions and
    spatial fractions all differ.
    """
    try:
        return _sensor_grid_dispatch(
            action,
            standard=standard,
            project=project,
            name=name,
            width=width,
            depth=depth,
            polygon=polygon,
            height=height,
            normal=normal,
            task_zones=task_zones,
            task_layout=task_layout,
            surround=surround,
            wall_strip=wall_strip,
            lm83_edition=lm83_edition,
        )
    except GridError as exc:
        # Every `rad_core.grids` refusal reaches the wire here: `TaskHeightRequiredError`
        # (`GRID_TASK_HEIGHT_REQUIRED`, check 2.7), `GridEmptyError` (`GRID_EMPTY`, ruling R21),
        # `PolygonError` and `CellAspectOutOfRangeError`. See the note in `_plan` for why the guard
        # predates a reachable input.
        logger.info("sensor_grid(action=%s, standard=%s) refused: %s", action, standard, exc)
        return runtime.refuse_engine_error(exc)
    except (ProjectError, PathOutsideRoot, RootsNotConfigured, CodedWriteRefusal) as exc:
        logger.info("sensor_grid(action=%s) refused: %s", action, exc)
        return runtime.refuse_engine_error(exc)
    except OSError as exc:
        logger.warning("sensor_grid(action=%s) failed on the filesystem: %s", action, exc)
        return refuse(
            f"sensor_grid(action={action!r}) could not complete: {exc}. Nothing about the arguments "
            "is wrong -- the filesystem refused the operation."
        )
    except InputStillBuiltFrom as exc:
        # `action="remove"` now unlinks the grid's `.pts`, so the guard `geometry(action="remove")`
        # already carried against making the manifest unevaluable is carried here too -- and it is
        # shaped exactly as `weather_tool` shapes the same exception, one code and one field, rather
        # than as a second vocabulary for one condition.
        logger.info("sensor_grid(action=%s) refused: %s", action, exc)
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
    except ArithmeticError as exc:
        # **The family none of the four clauses above covers, and it was reachable.** `polygon`
        # arrives as a declared `list[list[float]]`, and pydantic-core's JSON parser accepts
        # `1e999`, `Infinity` and `NaN` for a `float` -- so
        # `sensor_grid(action="create", polygon=[[0,0],[1e999,0],[1e999,1e999],[0,1e999]])` reached
        # `_lm83_axis`'s `math.floor` and raised `OverflowError`, which is an `ArithmeticError` and
        # is neither a `GridError` nor a `ProjectError` nor an `OSError`. Measured through
        # `mcp.call_tool`: `isError=True` with **no `structuredContent` at all**, the malformed
        # envelope check 0.4 forbids and ruling R38 already graded a violation.
        #
        # `rad_core.grids._as_points` and `max_cell_size` now refuse a non-finite ordinate and a
        # non-finite dimension as `PolygonError`/`ArgumentOutOfRangeGridError`, so the known route
        # is closed one layer down and this clause should not fire. It is kept for the reason
        # `_lm83_expected_points_or_none`'s docstring gives about the sibling case: a guard removed
        # on the strength of a callee's current translation of its own arithmetic is not a guard.
        logger.warning("sensor_grid(action=%s) failed in arithmetic: %r", action, exc)
        return refuse(
            f"sensor_grid(action={action!r}) could not complete: {type(exc).__name__}: {exc}. This "
            "is an arithmetic overflow inside the server rather than something about the "
            "arguments; if a coordinate or a dimension carried an extreme value, reducing it is "
            "the workaround, and the failure should be reported."
        )


#: A grid name's legal charset: ASCII letters, digits, underscore, hyphen and dot, 1-64 characters,
#: and never a name that is only dots.
#:
#: **Why a grid name needs a charset at all.** It is not just a label: it becomes a manifest input
#: id (`sensor_grid_<name>`) *and* a `.pts` filename, and `run_point_in_time` builds
#: `08_results/point_in_time_<name>.txt` from it. Measured before this guard: `name='../../ESCAPE'`,
#: `name='..'`, `name='a/b'` and `name='semi;colon'` were all accepted with `isError=False`.
#:
#: No escape was achievable today -- the write failed closed by accident, on a missing intermediate
#: directory and the `point_in_time_` prefix absorbing one leading path segment -- but neither of
#: those is a control, and "fails closed by accident" is the state a later refactor silently ends.
#: This is the first of two independent guards; `point_in_time_tool._write_results_file` now also
#: routes its write through `paths.contain`, so neither depends on the other being right.
#:
#: **Hoisted to `rad_mcp.names` by [R61](../../docs/plan.md#settled), 2026-08-12.** This was one of
#: three character-for-character copies of the same regex, each citing the others in prose as "the
#: same rule and the same reasoning" -- an arrangement that made the leading-hyphen weakness a
#: standing open decision, because tightening one copy would have left three subtly different rules
#: for one job. The alias is kept so the name still reads at this call site; the rule itself now has
#: exactly one definition, and it now also refuses a leading `-` or `.`.
_GRID_NAME_PATTERN = NAME_PATTERN


def _refuse_grid_name(name: str) -> CallToolResult | None:
    """Refuse a grid name that is not a conservative identifier, or `None` if it is fine.

    `ARGUMENT_OUT_OF_RANGE` with `field="name"` -- the register's established code for an argument
    refused on its face, carried with the argument's name. No new code is allocated: the observable
    fact is simply that this value of this argument is not one this server accepts.
    """
    if is_legal_name(name):
        return None
    message = name_refusal_reason(
        name, kind="sensor grid", examples="'grid', 'task_area', 'desk-row-2'"
    )
    return refuse(
        message,
        [Advisory(
            code="ARGUMENT_OUT_OF_RANGE", severity="error", field="name", message=message
        )],
    )


def _sensor_grid_dispatch(
    action: str,
    *,
    standard: str | None,
    project: str | None,
    name: str | None,
    width: float | None,
    depth: float | None,
    polygon: list[list[float]] | None,
    height: float | None,
    normal: list[float] | None,
    task_zones: list[dict[str, Any]] | None = None,
    task_layout: str | None = None,
    surround: dict[str, Any] | str | None = None,
    wall_strip: bool = True,
    lm83_edition: Lm83Edition | None = None,
) -> CallToolResult:
    call = f"sensor_grid(action={action!r})"
    if (refusal := _refuse_unread_edition(action, standard, lm83_edition)) is not None:
        return refusal
    if (refusal := _refuse_zone_arguments(
            action, standard, task_zones=task_zones, task_layout=task_layout, surround=surround,
            normal=normal)) is not None:
        return refusal
    if action == "plan":
        if (refusal := refuse_missing(call, standard=standard, width=width, depth=depth)) is not None:
            return refusal
        return _plan(standard, width, depth)

    if (refusal := refuse_missing(call, project=project)) is not None:
        return refusal
    loaded = runtime.current().open(project)

    if action == "list":
        return emit(
            SensorGridListed(
                project=loaded.paths.name,
                root=str(loaded.paths.root),
                grids=[
                    SensorGridRow(id=record.id, files=[ref.path for ref in record.files])
                    for record in loaded.manifest.inputs
                    if record.kind == "sensor_grid"
                ],
            )
        )
    if action == "remove":
        if (refusal := refuse_missing(call, name=name)) is not None:
            return refusal
        # Checked on `remove` as well as `create`: the name is matched against manifest ids here,
        # and a name this server would never have written is a name it should say so about rather
        # than report as simply absent.
        if (refusal := _refuse_grid_name(name)) is not None:
            return refusal
        return _remove_grid(loaded, name)

    if (refusal := refuse_missing(call, standard=standard, polygon=polygon)) is not None:
        return refusal
    refuse_extra_ordinates(polygon)  # R772: a GridError, shaped by sensor_grid's own handler
    grid_name = name or "grid"
    if (refusal := _refuse_grid_name(grid_name)) is not None:
        return refusal
    # S5c (final fix wave; R628's casefold rule one level out, to grid names): on a case-insensitive
    # filesystem `G.zones` and `g.zones` -- and `G.pts` and `g.pts` -- are one path, so a grid whose
    # name differs from a registered one only in case would write into, and later unlink, the other
    # grid's files. Refused before anything is created, the zone directory included.
    clash = sorted({
        record.id.removeprefix(SENSOR_GRID_INPUT_PREFIX) for record in loaded.manifest.inputs
        if record.kind == "sensor_grid"
        and record.id.removeprefix(SENSOR_GRID_INPUT_PREFIX).casefold() == grid_name.casefold()
        and record.id.removeprefix(SENSOR_GRID_INPUT_PREFIX) != grid_name
    })
    if clash:
        return _zone_refusal(
            f"sensor grid name {grid_name!r} differs only in case from the registered grid "
            f"{clash[0]!r}; on a case-insensitive filesystem the two would share files. Use "
            f"{clash[0]!r} to re-create that grid, or a name that differs in more than case.",
            field="name")
    if task_zones is not None or task_layout is not None:
        return _create_zoned(
            loaded, polygon, height=height, name=grid_name, task_zones=task_zones,
            task_layout=task_layout, surround=surround, wall_strip=wall_strip,
        )
    return _create(loaded, standard, polygon, height=height, normal=normal, name=grid_name,
                   lm83_edition=lm83_edition)


def _refuse_unread_edition(
    action: str, standard: str | None, lm83_edition: Lm83Edition | None
) -> CallToolResult | None:
    """R864 (Review Focus 4): `lm83_edition` chooses an LM-83 grid's default height, so only an
    unzoned `create` under `standard="lm83"` reads it. Sent anywhere else it would do nothing, and
    is refused before the project is opened rather than ignored. A missing `standard` is left to
    `refuse_missing`. The value itself is checked by the input schema's `Literal`."""
    if lm83_edition is None:
        return None
    if action != "create":
        message = (
            f"lm83_edition chooses the default height of an LM-83 grid and is read only by "
            f"action='create'; action={action!r} reads no height, so it is refused rather than "
            "ignored. Leave it out."
        )
    elif standard is not None and standard != "lm83":
        message = (
            f"lm83_edition chooses the default height of an LM-83 grid and is read only with "
            f"standard='lm83'; standard={standard!r} is not LM-83, so it is refused rather than "
            "ignored. Leave it out."
        )
    else:
        return None
    return refuse(
        message,
        [Advisory(code="ARGUMENT_OUT_OF_RANGE", severity="error", field="lm83_edition",
                  message=message)],
    )


def _refuse_zone_arguments(
    action: str,
    standard: str | None,
    *,
    task_zones: list[dict[str, Any]] | None,
    task_layout: str | None,
    surround: dict[str, Any] | str | None,
    normal: list[float] | None,
) -> CallToolResult | None:
    """Design §4's *Arguments* paragraph: every zone refusal readable from the arguments alone,
    before the project is opened. Geometry -- containment, overlap, the 64-area bound, the surround
    width, one group for `rest_of_room` -- is `rad_core.grid_zones`', and reaches the wire through
    `sensor_grid`'s `except GridError` (`ZoneArgumentError` carries its `field`).

    `wall_strip` is not checked here: it is read only when a zoned grid is made, and ignored on an
    unzoned create (plan Amendment 10)."""
    zoned = task_zones is not None or task_layout is not None
    if surround is not None and task_zones is None:
        return _zone_refusal("surround is valid only with task_zones.", field="surround")
    if not zoned:
        return None
    if action != "create" or standard != "en12464":
        field = "task_zones" if task_zones is not None else "task_layout"
        return _zone_refusal(
            f"{field} zones an EN 12464-1 grid and is valid only with action='create', "
            "standard='en12464'.", field=field)
    if task_zones is not None and task_layout is not None:
        return _zone_refusal(
            "give task_zones or task_layout='unknown', not both.", field="task_layout")
    if task_zones is not None:
        if not task_zones:
            return _zone_refusal(
                "task_zones is empty; give at least one task area, or task_layout='unknown'.",
                field="task_zones")
        for index, entry in enumerate(task_zones, start=1):
            if not isinstance(entry, dict) or set(entry) - {"rect", "group"} or "rect" not in entry:
                return _zone_refusal(
                    f"task area {index} must be {{'rect': [x0, y0, x1, y1], 'group': name}}.",
                    field="task_zones")
            rect = entry["rect"]
            if not isinstance(rect, list) or len(rect) != 4 or not all(
                    not isinstance(v, bool) and isinstance(v, (int, float)) for v in rect):
                return _zone_refusal(
                    f"task area {index}'s rect must be four numbers [x0, y0, x1, y1].",
                    field="task_zones")
            group = entry.get("group")
            if group is not None and (not isinstance(group, str) or not is_legal_name(group)):
                return _zone_refusal(
                    f"task area {index}'s group {group!r} is not a legal name (it becomes a file "
                    "name, surround_<group>.pts).", field="task_zones")
        # I2: a group label becomes a file name, and on a case-insensitive filesystem "A" and "a"
        # are one `surround_A.pts` -- one group's surround would be graded on the other's points.
        spellings: dict[str, set[str]] = {}
        for entry in task_zones:
            label = entry.get("group") or "1"
            spellings.setdefault(label.casefold(), set()).add(label)
        if clash := next((sorted(v) for v in spellings.values() if len(v) > 1), None):
            return _zone_refusal(
                f"group labels {clash!r} differ only in case; they would share one zone file on a "
                "case-insensitive filesystem. Use labels that differ in more than case.",
                field="task_zones")
    if isinstance(surround, dict):
        value = surround.get("width")
        if set(surround) != {"width"} or isinstance(value, bool) or not isinstance(value, (int, float)):
            return _zone_refusal(
                "surround must be {'width': metres} or 'rest_of_room'.", field="surround")
    if normal is not None and [float(v) for v in normal] != [0.0, 0.0, 1.0]:
        return _zone_refusal(
            "a zoned grid lies on one horizontal plane: normal must be omitted or [0, 0, 1].",
            field="normal")
    return None


def _zone_refusal(message: str, *, field: str) -> CallToolResult:
    return refuse(
        message,
        [Advisory(code="ARGUMENT_OUT_OF_RANGE", severity="error", field=field, message=message)],
    )


def _plan(standard: str, width: float, depth: float) -> CallToolResult:
    try:
        spec = plan_grid(Standard(standard), width, depth)
    except GridError as exc:
        # `plan_grid` can only raise `CellAspectOutOfRangeError` (`GRID_CELL_ASPECT_OUT_OF_RANGE`),
        # and as of fix round 1's other change (finding I1, reconciling `plan_grid` and
        # `place_sensor_grid` onto one LM-83 construction) that is no longer reachable through any
        # real width/depth/standard combination this tool's bare-rectangle form can pass --
        # `rad_core.grids._ensure_cell_aspect_in_range`'s docstring has the proof. This `except`
        # stays anyway: it is what Task 7's `polygon`-taking `action="create"` will need the moment
        # it is wired to `place_sensor_grid`, whose refusals (`TaskHeightRequiredError`,
        # `PolygonError`, `GridEmptyError`) are all reachable today.
        #
        # Before this existed, a `GridError` propagated out of the tool function uncaught: no code
        # reached the client, and an exception escaping a tool is a JSON-RPC error rather than the
        # tool-result-channel refusal check 0.4 requires. Routed through the same
        # `refuse_engine_error` `project` and `job` already use, which is where
        # `rad_core.grids.GridError` and `rad_engine`'s `ProjectError`/`JobError` meet the wire
        # (fix round 1 of Task 1's code review, finding I4). Making the refusal actually reach the
        # wire also required `SensorGridResponse` above -- `SensorGridResult` alone had no shape a
        # `Refusal` payload could validate against.
        logger.info("sensor_grid(standard=%s) refused: %s", standard, exc)
        return runtime.refuse_engine_error(exc)

    return emit(
        SensorGridResult(
            standard=spec.standard.value,
            max_cell_size_m=round(spec.max_cell, 4),
            spacing_m=GridSpacing(x=round(spec.spacing_x, 4), y=round(spec.spacing_y, 4)),
            points=GridPoints(x=spec.n_x, y=spec.n_y, total=spec.n_points),
            governing_dimension_m=spec.governing_dimension,
            cell_aspect_ratio=round(spec.cell_aspect, 3),
            advisories=_grid_advisories(spec, spec.n_points) or None,
            rule=_RULES[spec.standard.value],
        )
    )


def _commit_grid_files(planned: Sequence[tuple[Path, Sequence[GridPoint]]],
                       asides: dict[Path, Path | None] | None = None) -> list[Path]:
    """Write a grid's `.pts` file(s): every file staged as `<file>.partial`, then all renamed in --
    one path for the unzoned and the zoned create (Phase 11b final fix wave, S3 and C1).

    **Three phases, and a refusal in the first two leaves the disk as it was.**

    1. *Every destination checked, before anything is created.* `resolved_parent_within` resolves
       the **parent chain** (a directory symlink at `11_sensors` would walk a new file out of the
       root with no symlink at the leaf; checked before the `mkdir`, which would otherwise create the
       escape itself). `refuse_existing_non_regular_destination` refuses anything not a regular file (a
       symlink, a directory or a FIFO, say) at a destination name -- so an obstacle at a *later* zone's name refuses the call before
       an earlier zone's file has been replaced (C1: the re-create used to overwrite `task_1.pts` in
       place and then refuse at `task_3.pts`, leaving the unchanged record naming bytes it no longer
       digested).
    2. *Every file staged.* `rad_engine.sensors.stage_points` creates `<file>.partial` exclusively
       (`open_staging_file`), so a **hardlink** planted at a destination is never written through
       (S3: `"wb"` truncated the linked inode, outside every root). A failure unlinks the partials
       this call created -- never a planted one, which `open_staging_file` refuses and leaves.
    3. *Every file renamed in* with `os.replace`, which swaps the name, not the inode. Each earlier file
       is first hard-linked to a fresh aside name (`_link_aside`), so the grid's file is never missing
       while it is swapped and the caller can put the earlier bytes back if the record cannot be saved
       (N-118, R852). A failure here (only a race after phase 1 can cause one) puts back what was
       already replaced and is refused `SENSOR_GRID_FILES_REPLACED` (`SensorGridFilesReplaced`),
       naming what happened, never as "nothing written".

    The ELOOP/ENXIO translation is kept from `_write_grid_points`, which this replaces: an `OSError`
    reaching `sensor_grid`'s generic handler would interpolate the absolute path of a symlink's
    target; a failure in phase 1 is `PathOutsideRoot`, shaped into `PATH_OUTSIDE_ROOT` (field `root`),
    and a staging write in phase 2 that fails is `PROJECT_WRITE_FAILED` (`write_failure`, R855).
    """
    roots = runtime.current().roots
    for path, _points in planned:
        if not resolved_parent_within(path, roots):
            raise PathOutsideRoot(
                f"the sensor grid's .pts file would be written to {path.parent}, which resolves outside "
                "every allowed root. Nothing has been written. A symlinked directory inside the project "
                "is the usual cause; the file this server writes must stay inside the project it "
                "belongs to."
            )
        try:
            refuse_existing_non_regular_destination(path)
        except NotARegularFileError:
            raise PathOutsideRoot(
                f"{path} exists and is not a regular file (anything else is refused: a symlink, a "
                "directory or a FIFO, for example), so this server will not replace it with the "
                "grid's points. Nothing has been written and the "
                "grid's record is unchanged. Remove whatever occupies that path and re-run."
            ) from None
    staged: list[Path] = []
    try:
        for path, points in planned:
            path.parent.mkdir(parents=True, exist_ok=True)
            partial = path.with_name(path.name + ".partial")
            stage_points(partial, points)
            staged.append(partial)
    except OSError as exc:
        for partial in staged:
            partial.unlink(missing_ok=True)
        # R855: a write failure, not containment -- `PROJECT_WRITE_FAILED` (it was `PATH_OUTSIDE_ROOT`).
        # Something not a regular file at the `.partial` name (`open_staging_file` refuses to write
        # through one) is named as the cause; anything else is the disk or the permissions.
        partial_name = path.with_name(path.name + ".partial")
        if isinstance(exc, NotARegularFileError):
            raise ProjectWriteFailed(
                f"the sensor grid's staging file could not be written at {partial_name}: something not a "
                "regular file occupies that name, and this server will not write through it. Nothing has "
                "been written and the grid's record is unchanged. Remove it and re-run.") from None
        raise write_failure(partial_name, exc, what="the sensor grid's staging file", outcome=(
            "Nothing has been written and the grid's record is unchanged.")) from None
    except BaseException:
        for partial in staged:
            partial.unlink(missing_ok=True)
        raise
    # Each earlier file is kept under a fresh aside name before the new one replaces it, so the caller
    # can put it back if the record cannot be saved (N-118); a link or a rename needs no disk space.
    kept: dict[Path, Path | None] = asides if asides is not None else {}
    replaced = 0
    try:
        for (path, _points), partial in zip(planned, staged):
            kept[path] = _link_aside(path) if os.path.lexists(path) else None
            os.replace(partial, path)
            replaced += 1
    except OSError as exc:
        for partial in staged[replaced:]:
            partial.unlink(missing_ok=True)
        failed = _rollback_grid_files(kept)
        kept_as = ", ".join(str(kept[p]) for p in failed if kept.get(p) is not None)
        raise SensorGridFilesReplaced(
            f"the sensor grid's file {planned[replaced][0]} could not be put in place: "
            f"{exc.strerror or exc}. "
            + ("The files already replaced were put back, and the grid's record is unchanged."
               if not failed else
               f"{len(failed)} of them could not be put back ({', '.join(str(p) for p in failed)}), so the grid's "
               "record, which is unchanged, may no longer match its files"
               + (f" (the earlier files are kept as {kept_as})" if kept_as else "")
               + ": re-create the grid.")
        ) from None
    return [path for path, _points in planned]


#: The aside name `_commit_grid_files` keeps an earlier grid file under while the new one is swapped
#: in (R852): `.<file name>.<8 hex digits>.aside`, beside the file. Created with `os.link`, which fails
#: rather than replace an existing name, so nothing of a user's is overwritten; a crash between the
#: link and the save leaves one, which the grid's next create or remove deletes
#: (`_remove_orphan_asides`). A user's own file of exactly this shape cannot be told from such an
#: orphan and is deleted as one.
_ASIDE_NAME: Final[re.Pattern[str]] = re.compile(r"\A\.(?P<file>.+)\.[0-9a-f]{8}\.aside\Z")

#: `os.link` errors that mean the filesystem holds no hard link here; `_link_aside` then renames.
_NO_HARD_LINK: Final[frozenset[int]] = frozenset(
    {errno.EPERM, errno.ENOTSUP, errno.EOPNOTSUPP, errno.EMLINK, errno.ENOSYS}
)


def _link_aside(path: Path) -> Path:
    """Keep `path`'s current bytes under a fresh `_ASIDE_NAME` and return that name. A hard link, so
    `path` stays in place until `os.replace` swaps the new file in (one atomic rename, as before
    N-118). An occupied name is never overwritten: `os.link` refuses it and another token is drawn.
    Where the filesystem has no hard links the file is renamed to the fresh name instead, which
    leaves `path` missing until the swap -- the behaviour before R852."""
    for _attempt in range(16):
        aside = path.with_name(f".{path.name}.{secrets.token_hex(4)}.aside")
        try:
            os.link(path, aside, follow_symlinks=False)
            return aside
        except FileExistsError:
            continue
        except OSError as exc:
            if exc.errno not in _NO_HARD_LINK:
                raise
        if os.path.lexists(aside):
            continue
        os.rename(path, aside)
        return aside
    raise FileExistsError(errno.EEXIST, "no free aside name beside the grid file", str(path))


def _remove_orphan_asides(paths: ProjectPaths, name: str) -> None:
    """R852: delete the asides an interrupted create of grid `name` left -- regular files of
    `_ASIDE_NAME`'s shape for `11_sensors/<name>.pts` and for any `.pts` in `11_sensors/<name>.zones/`,
    the only files that grid writes. Anything else of that shape (another grid's, a directory, a
    symlink) is left; a failure is logged, never raised."""
    if not is_legal_name(name):
        return
    roots = runtime.current().roots
    places = [(paths.sensors, lambda file: file == f"{name}.pts"),
              (_zones_dir(paths, name), lambda file: file.endswith(".pts"))]
    for directory, owns in places:
        if directory.is_symlink() or not directory.is_dir() or not resolved_parent_within(directory / "x", roots):
            continue
        for entry in directory.iterdir():
            match = _ASIDE_NAME.match(entry.name)
            if match is None or not owns(match.group("file")):
                continue
            try:
                if stat.S_ISREG(os.lstat(entry).st_mode):
                    entry.unlink()
            except OSError as exc:
                logger.warning("sensor_grid: could not remove the leftover %s: %s", entry, exc)


def _rollback_grid_files(asides: dict[Path, Path | None]) -> list[Path]:
    """Undo `_commit_grid_files` by renaming: each earlier file comes back from beside it, and each
    file that did not exist before is removed. A rename needs no disk space, so it can succeed where
    a copy of the old bytes could not (N-118). Returns the paths it could not put back."""
    failed: list[Path] = []
    for path, aside in asides.items():
        try:
            if aside is not None and os.path.lexists(path) and os.path.samefile(path, aside):
                aside.unlink()  # never swapped: the link is the file itself (`rename` would do nothing)
            elif aside is not None:
                os.replace(aside, path)
            else:
                path.unlink(missing_ok=True)
        except OSError as exc:
            logger.warning("sensor_grid: could not put %s back after a failed save: %s", path, exc)
            failed.append(path)
    return failed


def _discard_asides(asides: dict[Path, Path | None]) -> None:
    """The record is saved: the earlier files kept beside the new ones are no longer needed."""
    for aside in asides.values():
        if aside is not None:
            try:
                aside.unlink(missing_ok=True)
            except OSError as exc:
                logger.warning("sensor_grid: could not remove %s: %s", aside, exc)


def _undo_after_failed_save(asides: dict[Path, Path | None], cause: BaseException) -> None:
    """N-118: the files were renamed in and the record could not be saved, so the unchanged record
    must not be left naming the new bytes. Put the earlier files back; if that cannot be done for some
    file, refuse (`SENSOR_GRID_FILES_REPLACED`, R852) naming it and where its earlier bytes are kept,
    rather than the generic filesystem refusal."""
    failed = _rollback_grid_files(asides)
    if failed and isinstance(cause, Exception):
        names = ", ".join(str(p) for p in failed)
        kept = ", ".join(str(a) for p, a in asides.items() if p in failed and a is not None)
        raise SensorGridFilesReplaced(
            f"the grid's record could not be saved ({cause}) and the earlier files could not all be put back: "
            f"{names} still hold the new points while the record, which is unchanged, describes the earlier ones"
            + (f" (the earlier files are kept as {kept})" if kept else "")
            + ". Re-create the grid."
        ) from cause


def _create(
    loaded: Any,
    standard: str,
    polygon: list[list[float]],
    *,
    height: float | None,
    normal: list[float] | None,
    name: str,
    lm83_edition: Lm83Edition | None = None,
) -> CallToolResult:
    """Place the points, write the `.pts` file, register it.

    `place_sensor_grid` owns every number here -- the spacing, the wall offsets, the clipping and the
    reference-plane height with its provenance. This function chooses no value and converts nothing.
    A height the caller gave is recorded `assumed` ("user gave it, unmeasured"), which is
    `place_sensor_grid`'s own default for `height_provenance`; an absent one resolves to the
    standard's own labelled default, or is refused for EN 12464-1, which has none.

    The file is written **before** the input is registered, and the registration digests it: an input
    record pointing at a file that does not exist yet would digest nothing and then never invalidate.
    """
    placed = place_sensor_grid(
        Standard(standard),
        polygon,
        height=height,
        normal=tuple(normal) if normal is not None else (0.0, 0.0, 1.0),
        lm83_edition=lm83_edition if lm83_edition is not None else DEFAULT_LM83_EDITION,
    )
    # R784 (owner decision F3): the resolved height, so a reader judges the points at the floor plus
    # it, as it judges a zoned grid. `placed.height.value`, not the argument: a defaulted call
    # passes `None`.
    arguments: dict[str, Any] = {"standard": standard, "name": name, "polygon": polygon,
                                 "height": placed.height.value}
    # Final fix E, fix round 2 (review O1): the normal the points were placed facing, resolved, so a
    # remedy that spells this record's create call re-creates the same points. Recorded on every
    # create from now on (an older record has no key, which therefore means "not recorded", not
    # "facing up"). `registered_by` is in no digest (`rad_core.manifest.input_digest` reads the
    # kind, the file digests and the units), so no stored result or planned step moves.
    arguments["normal"] = [float(v) for v in (normal if normal is not None else (0.0, 0.0, 1.0))]
    if lm83_edition is not None:
        # R864: recorded only when sent, so a grid created before this argument existed and one
        # created without it carry the same record; a reader of the record takes it as the edition
        # the grid was created under.
        arguments["lm83_edition"] = lm83_edition

    paths = ProjectPaths(loaded.paths.root)
    target = paths.sensors / f"{name}.pts"
    input_id = f"{SENSOR_GRID_INPUT_PREFIX}{name}"
    # Opened inside both locks: `register_input` writes the whole manifest with no
    # compare-against-disk, so a lock that does not cover the read covers nothing
    # (`Runtime.modifying`; `rad_mcp.materials_tool._write` says the same thing at more length).
    with runtime.current().modifying(paths):
        current = open_project(paths.root, radiance_version=runtime.current().launcher.version)
        # A re-create over a *zoned* grid of the same name (Phase 11b design §4, *Files*). Order,
        # all inside the lock (F20, fix round 2 N1): every refusal first -- the old record's stale
        # files proven contained -- then the write, then the save, then the unlink. A refusal
        # therefore leaves the disk exactly as it found it, including the file the unchanged
        # record still names and digests.
        stale = _stale_files(paths, name, _previous_files(current, input_id), [target])
        _remove_orphan_asides(paths, name)
        asides: dict[Path, Path | None] = {}
        [written] = _commit_grid_files([(target, placed.points)], asides)
        try:
            register_input(
                current,
                input_id=input_id,
                kind="sensor_grid",
                files=[written],
                registered_by=ToolCall(
                    tool="sensor_grid",
                    action="create",
                    arguments=arguments,
                ),
            )
        except BaseException as exc:
            _undo_after_failed_save(asides, exc)  # N-118: the unchanged record must still match its file
            raise
        _discard_asides(asides)
        _replace_files(paths, name, stale, [written])

    spec = placed.spec
    return emit(
        SensorGridCreated(
            standard=spec.standard.value,
            max_cell_size_m=round(spec.max_cell, 4),
            spacing_m=GridSpacing(x=round(spec.spacing_x, 4), y=round(spec.spacing_y, 4)),
            points=GridPoints(x=spec.n_x, y=spec.n_y, total=placed.points_total),
            governing_dimension_m=spec.governing_dimension,
            cell_aspect_ratio=round(spec.cell_aspect, 3),
            rule=_RULES[spec.standard.value],
            height_m=placed.height,
            floor_z_m=placed.floor_z,
            points_inside=placed.points_inside,
            points_clipped=placed.points_clipped,
            pts_file=str(written),
            project=loaded.paths.name,
            root=str(loaded.paths.root),
            name=name,
            input_id=input_id,
            advisories=(
                _grid_advisories(spec, placed.points_inside)
                + warnings_to_wire(placed.warnings)
                + _site_advisory(placed.points)
            )
            or None,
        )
    )


#: `SensorGridZonedCreated.spacing_rule`: every zone is sized by the en12464 rule with the guides'
#: aspect flip over its own circumscribing rectangle (R609), and a band surround is then capped by
#: licht.de p.36's ribbon rule (R615).
_SPACING_RULE = "guides_aspect_flip+licht.de_p36_ribbon"


def _zones_dir(paths: ProjectPaths, name: str) -> Path:
    """A zoned grid's directory, computed from its name (design §4, *Files*)."""
    return paths.sensors / f"{name}.zones"


def _previous_files(loaded: Any, input_id: str) -> list[str]:
    """The files the grid's current record names, before a re-create replaces it."""
    return [ref.path for record in loaded.manifest.inputs if record.id == input_id
            for ref in record.files]


def _rmdir_zone_dir(paths: ProjectPaths, name: str) -> None:
    """`rmdir` the computed zone directory if it exists, is a real directory and is empty.

    Never a recursive delete, and never a directory taken from a recorded path: the directory is
    `_zones_dir(paths, name)`, and a name this server would not have written computes nothing. A
    symlink is left alone (`rmdir` on one fails anyway, but the guard does not lean on that), and
    the parent chain is proven inside a root first -- R48's rule reaches `rmdir` as it reaches
    `unlink`. A directory holding a file this server did not write stays, and so does the file.
    """
    if not is_legal_name(name):
        return
    zone_dir = _zones_dir(paths, name)
    if not (zone_dir.is_dir() and not zone_dir.is_symlink()
            and resolved_parent_within(zone_dir, runtime.current().roots)):
        return
    try:
        zone_dir.rmdir()
    except OSError as exc:
        if exc.errno not in (errno.ENOTEMPTY, errno.EEXIST):
            # The grid is already saved; a directory that will not go is reported, not raised.
            logger.warning("sensor_grid: could not remove the empty zone directory %s: %s",
                           zone_dir, exc.strerror or exc)


def _same_file(a: str, b: str) -> bool:
    """File identity, not spelling: the same inode reached through a symlinked directory, or under
    another case on a case-insensitive filesystem, is one file. A path that does not exist is the
    same as nothing."""
    try:
        return os.path.samefile(a, b)
    except OSError:
        return False


def _grid_owns(paths: ProjectPaths, name: str, target: Path) -> bool:
    """Whether `target` is a file grid `name`'s own create writes -- `11_sensors/<name>.pts`, or a
    `.pts` directly inside `11_sensors/<name>.zones/` -- judged on the **resolved parent**, so a
    `..`, a symlinked zone directory or a symlinked `11_sensors` does not pass (Phase 11b final fix
    wave, S2).

    **Why containment alone was not enough.** `resolved_parent_within` asks "inside some root?", and
    a hand-edited record naming `manifest.json`, or another project's file, is inside the roots:
    the routine re-create (or `remove`) unlinked it. A recorded path is untrusted (R50); the only
    files this server may unlink on a grid's behalf are the ones that grid's name computes.

    **The file name is not itself asked to be a legal name** (B1, owner ruling after the re-review).
    `NAME_PATTERN` allows 64 characters, but the file is `<name>.pts`, 65--68 characters for a legal
    61--64 character grid name: the clause refused a grid this server had itself written, and
    `remove` and a zoned re-create both failed with `PATH_OUTSIDE_ROOT` and "the manifest has been
    edited". It bought nothing either: `name` is already checked legal, and the name is then pinned
    by the `f"{name}.pts"` equality (the `11_sensors` branch) or by the required parent -- the grid's
    own `<name>.zones` directory, which only this server's create writes into -- plus the `.pts`
    suffix (the zone branch).
    """
    if not (is_legal_name(name) and target.suffix == ".pts"):
        return False
    sensors = paths.sensors.resolve()
    parent = target.parent.resolve()
    return parent == sensors / f"{name}.zones" or (parent == sensors and target.name == f"{name}.pts")


def _refuse_foreign_file(target: Path, name: str, *, removing: bool) -> PathOutsideRoot:
    outcome = ("Nothing has been removed and the grid is still registered." if removing else
               "Nothing has been written or re-registered and the grid's record is unchanged.")
    return PathOutsideRoot(
        f"the grid's recorded file {target} is not one sensor grid {name!r} writes (11_sensors/"
        f"{name}.pts, or a .pts file in 11_sensors/{name}.zones/), so this server will not unlink "
        f"it. {outcome} The manifest has been edited; remove the record by hand."
    )


def _unlink_if_still_owned(paths: ProjectPaths, name: str, target: Path) -> bool:
    """S5a: containment and ownership **again**, immediately before the unlink -- the checks above
    ran before the writes and the save, and a directory swapped for a symlink in that window would
    otherwise lead the unlink out of the root. The grid is already saved, so a failure here is
    logged and the file left, never raised."""
    if not (resolved_parent_within(target, runtime.current().roots) and _grid_owns(paths, name, target)):
        logger.warning("sensor_grid: %s no longer resolves to grid %r's own files; left in place.", target, name)
        return False
    target.unlink(missing_ok=True)
    return True


def _record_grid_name(record: Any) -> str:
    """The grid name a record's files are computed from: the recorded `name` argument, else the id
    less its prefix -- `_remove_grid`'s rule for the zone directory, one definition."""
    recorded = record.registered_by.arguments.get("name") if record.registered_by is not None else None
    return recorded if isinstance(recorded, str) else record.id.removeprefix(SENSOR_GRID_INPUT_PREFIX)


def _stale_files(paths: ProjectPaths, name: str, old: list[str], planned: Sequence[Path]) -> list[Path]:
    """The old record's files the new record will not name, **each proven contained**, before
    anything is written (fix round 1: C1, M3; fix round 2: N1).

    *Names* is decided by identity. `old` holds `FileRef.path`, which `register_input` stores
    resolved (`rad_engine.project.file_refs`); `planned` is the write path, which is not. Compared
    by string, a symlinked `11_sensors` made every file the call wrote "stale" and the clean-up
    unlinked it after the save (C1). So the comparison is against the resolved spelling the new
    record will store, and a stale path that `os.path.samefile`s a planned file already on disk is
    kept -- the second half catches a case-only rename on a case-insensitive filesystem, where
    `resolve()` does not normalise case. `_replace_files` checks identity once more after the writes.

    Containment is R48's primitive, checked here -- inside the lock, before any write and before
    `register_input` -- and refused as `_remove_grid` refuses it (M3): a record naming a file
    outside every root stops the create with nothing written, nothing registered and nothing
    unlinked (N1).
    """
    new = [str(Path(p).resolve()) for p in planned]
    roots = runtime.current().roots
    stale: list[Path] = []
    for path in sorted(set(old) - set(new)):
        if any(_same_file(path, other) for other in new):
            continue
        target = Path(path)
        if not resolved_parent_within(target, roots):
            raise PathOutsideRoot(
                f"the grid's recorded file {target} resolves outside every allowed root, so this "
                "server will not unlink it. Nothing has been re-registered and the grid's record "
                "is unchanged. A symlinked directory inside the project is the usual cause."
            )
        # S2 (final fix wave): inside the roots is not enough -- only the grid's own files.
        if not _grid_owns(paths, name, target):
            raise _refuse_foreign_file(target, name, removing=False)
        stale.append(target)
    return stale


def _replace_files(paths: ProjectPaths, name: str, stale: list[Path], written: Sequence[Path]) -> None:
    """After the save, inside the same `modifying` block (F20): unlink the files `_stale_files`
    proved stale and contained before anything was written, then `_rmdir_zone_dir` -- which takes
    the zone directory only if that emptied it (design §4, *Files*: "zoned <-> unzoned re-creates
    leave no orphan").

    Identity is checked again here, against the files now on disk: `_stale_files` ran before the
    writes, so an old path that did not exist then (a recorded file already deleted) cannot have
    been matched by `samefile` -- and on a case-insensitive filesystem it may now name a file this
    call has just written under another case."""
    for target in stale:
        if any(_same_file(str(target), str(new)) for new in written):
            continue
        _unlink_if_still_owned(paths, name, target)
    _rmdir_zone_dir(paths, name)


def _create_zoned(
    loaded: Any,
    polygon: list[list[float]],
    *,
    height: float | None,
    name: str,
    task_zones: list[dict[str, Any]] | None,
    task_layout: str | None,
    surround: dict[str, Any] | str | None,
    wall_strip: bool,
) -> CallToolResult:
    """The zoned EN 12464 create (Phase 11b design §4): plan every zone, write one `.pts` per
    present zone under `11_sensors/<name>.zones/`, register one `sensor_grid` input whose `files`
    are those files in zone order and whose `zones` records every zone, absent ones included with
    `file_index: None`.

    `rad_core.grid_zones.plan_zones` owns every number and every geometric refusal, and it runs
    before anything touches the disk. The directory is contained before `mkdir` (a symlinked
    `11_sensors` would otherwise have `mkdir` create it outside the root) and refused after it if it
    is not a real directory (a symlinked `.zones` inside the root passes containment); each file is
    then contained again, staged and renamed in by `_commit_grid_files`.
    """
    tasks = [
        TaskZone(rect=tuple(float(v) for v in entry["rect"]),  # type: ignore[arg-type]
                 group=entry.get("group") or "1")
        for entry in (task_zones or [])
    ]
    spec: float | str | None = (
        "rest_of_room" if surround == "rest_of_room"
        else float(surround["width"]) if isinstance(surround, dict) else None
    )
    plan = plan_zones(
        polygon, height=height, tasks=tasks,
        layout="unknown" if task_layout == "unknown" else "task_zones",
        surround=spec, wall_strip=wall_strip,  # type: ignore[arg-type]
    )

    paths = ProjectPaths(loaded.paths.root)
    roots = runtime.current().roots
    zone_dir = _zones_dir(paths, name)
    if not resolved_parent_within(zone_dir / "probe.pts", roots):
        raise PathOutsideRoot(
            f"the zone directory {zone_dir} resolves outside every allowed root. Nothing has been "
            "written. A symlinked directory inside the project is the usual cause.")
    created_dir = not (zone_dir.exists() or zone_dir.is_symlink())
    try:
        zone_dir.mkdir(parents=True, exist_ok=True)
    except FileExistsError:
        pass  # a file (or a dangling link) at that name: refused just below
    if zone_dir.is_symlink() or not zone_dir.is_dir():
        raise PathOutsideRoot(
            f"{zone_dir} is not a plain directory (a symlink is the usual cause), so this server "
            "will not write a zoned grid into it. Nothing has been written.")

    # The files are planned here and written only inside the lock, after every refusal (N1).
    planned: list[tuple[Path, Sequence[GridPoint]]] = []
    records: list[ZoneRecord] = []
    for zone in plan.zones:
        index: int | None = None
        if not zone.absent:
            planned.append((zone_dir / f"{zone.name}.pts", zone.points))
            index = len(planned) - 1
        records.append(ZoneRecord(
            zone=zone.name, group=zone.group, file_index=index, points=len(zone.points),
            spacing_m=list(zone.spacing) if zone.spacing else None,
            formula_spacing_m=list(zone.formula_spacing) if zone.formula_spacing else None,
            circumscribing_m=list(zone.circumscribing) if zone.circumscribing else None,
            ribbon_clamped=zone.ribbon_clamped, task_in_strip=zone.task_in_strip,
        ))

    input_id = f"{SENSOR_GRID_INPUT_PREFIX}{name}"
    arguments = {
        "standard": "en12464", "name": name, "polygon": polygon, "height": height,
        "task_zones": ([{"rect": list(t.rect), "group": t.group} for t in tasks]
                       if task_zones is not None else None),
        "task_layout": task_layout, "surround": surround, "wall_strip": wall_strip,
    }
    with runtime.current().modifying(paths):
        # Every refusal, then every file staged and renamed in (`_commit_grid_files`, C1), then the
        # save, then the unlink (fix round 2, N1).
        try:
            current = open_project(paths.root, radiance_version=runtime.current().launcher.version)
            stale = _stale_files(paths, name, _previous_files(current, input_id), [path for path, _ in planned])
            _remove_orphan_asides(paths, name)
            asides: dict[Path, Path | None] = {}
            written = _commit_grid_files(planned, asides)
        except BaseException:
            if created_dir:  # the directory this call made, and only if still empty
                _rmdir_zone_dir(paths, name)
            raise
        try:
            registered = register_input(
                current, input_id=input_id, kind="sensor_grid", files=written,
                registered_by=ToolCall(tool="sensor_grid", action="create", arguments=arguments),
                zones=records,
            )
        except BaseException as exc:
            try:
                _undo_after_failed_save(asides, exc)  # N-118: the unchanged record must still match its files
            finally:
                if created_dir:
                    _rmdir_zone_dir(paths, name)
            raise
        _discard_asides(asides)
        _replace_files(paths, name, stale, written)
    # The recorded (resolved) spelling, so `pts_file` names what `files` names.
    new_files = [ref.path for ref in registered.files]

    advisories = warnings_to_wire(list(plan.warnings))
    if plan.surround_mode == "rest_of_room":
        advisories.append(Advisory(code="SURROUND_IS_ASR_GEOMETRY", severity="info", field="surround", message=(
            "surround='rest_of_room' is the rest of the room less the wall strip: the guides' reading for an "
            "office with one known work station (licht.de p.11, a.1; ZVEI a.), and also ASR A3.4's geometry "
            "(licht.de p.8). It is graded here against EN 12464-1's 0.40, where ASR asks 0.5 (R619, R652).")))
    if plan.points_total > GRID_VERY_LARGE_POINTS:
        advisories.append(Advisory(code="GRID_VERY_LARGE", severity="warn", field="points", message=(
            f"{plan.points_total} sensor points over all zones will make the run slow.")))
    advisories += _site_advisory([point for zone in plan.zones for point in zone.points])

    def _spacing(pair: tuple[float, float] | None) -> GridSpacing | None:
        return GridSpacing(x=round(pair[0], 4), y=round(pair[1], 4)) if pair else None

    rows = [
        ZoneRow(
            zone=zone.name, group=zone.group, points=len(zone.points),
            spacing_m=_spacing(zone.spacing), formula_spacing_m=_spacing(zone.formula_spacing),
            ribbon_clamped=zone.ribbon_clamped,
            pts_file=new_files[record.file_index] if record.file_index is not None else None,
        )
        for zone, record in zip(plan.zones, records)
    ]
    return emit(SensorGridZonedCreated(
        project=loaded.paths.name, root=str(loaded.paths.root), input_id=input_id,
        standard="en12464", name=name, zoned=True, layout=plan.layout,
        surround_mode=plan.surround_mode, spacing_rule=_SPACING_RULE, wall_strip=plan.wall_strip,
        zones=rows, points_total=plan.points_total, height_m=plan.height,
        floor_z_m=plan.floor_z,
        advisories=advisories or None,
    ))


def _remove_grid(loaded: Any, name: str) -> CallToolResult:
    """Deregister one sensor grid **and unlink the `.pts` it recorded**.

    See `rad_mcp.geometry_tool._removed` for why the manifest is edited here and why removing
    something absent is not a refusal.

    **The unlink is new as of 2026-08-31 and closes half of ruling R199.** This action used to edit
    the manifest only -- measured: 18 021 B of `.pts` still on disk after a successful
    `removed: ['sensor_grid_g']` -- and the file it left behind was then accounted for by nothing:
    not by the manifest, which no longer names it, and not by any tool, since `create` writes
    `<name>.pts` and only overwrites the same name. R199's other half is a *bound* on how large one
    grid may be (`rad_core.grids.place_sensor_grid`); this is the half that stops the bounded
    amount accumulating without limit across calls. `SensorGridRemoved.removed_files` publishes what
    went, because a destructive action that reports only what it deregistered is describing half of
    what it did.

    **Refused first if anything was built from the grid**, which is `geometry_tool._removed`'s own
    guard and is what makes the unlink safe to add. An artifact whose `built_from` names a removed
    input makes the *whole* manifest unevaluable; and now that the file goes too, the same call
    would also destroy the sensor positions the artifact's digest is taken over. One guard covers
    both, and it is the established one rather than a second spelling of it.

    **Contained before the unlink, not after** -- R48's rule was worded as *"every filesystem
    open"* and `unlink` opens nothing, which is exactly the gap `materials_tool._withdraw_files`
    records at its own unlink. A directory symlink in the parent chain is invisible to
    `O_NOFOLLOW`, so an unguarded unlink here deletes an arbitrary file outside every root and only
    *then* refuses. The path is also caller-influenced (`<name>.pts`), which is the property that
    ranked this write first among seventeen in the first place.
    """
    paths = ProjectPaths(loaded.paths.root)
    candidates = {name, f"{SENSOR_GRID_INPUT_PREFIX}{name}"}
    removed_files: list[str] = []
    with runtime.current().modifying(paths):
        current = open_project(paths.root, radiance_version=runtime.current().launcher.version)
        matched = [
            record
            for record in current.manifest.inputs
            if record.kind == "sensor_grid" and record.id in candidates
        ]
        removed = [record.id for record in matched]
        if removed:
            # Before anything is touched, on either the manifest or the disk.
            refuse_if_built_from(current.manifest, removed)
            targets = [(_record_grid_name(record), Path(ref.path)) for record in matched for ref in record.files]
            for grid_name, target in targets:
                if not resolved_parent_within(target, runtime.current().roots):
                    raise PathOutsideRoot(
                        f"the grid's recorded file {target} resolves outside every allowed root, so "
                        "this server will not unlink it. Nothing has been removed and the grid is "
                        "still registered. A symlinked directory inside the project is the usual "
                        "cause."
                    )
                # S2 (final fix wave): only the files the grid's own name computes.
                if not _grid_owns(paths, grid_name, target):
                    raise _refuse_foreign_file(target, grid_name, removing=True)
            current.manifest.inputs = [
                record for record in current.manifest.inputs if record.id not in removed
            ]
            # The manifest first: a file unlinked before the record is saved leaves, on a crash in
            # between, a manifest naming a file that is gone -- which is the state `is_reusable`
            # reports as an invalid artifact rather than as a missing input, and is strictly harder
            # to explain than a record already removed and a file still present.
            save_manifest(paths, current.manifest)
            for grid_name, target in targets:
                if _unlink_if_still_owned(paths, grid_name, target):
                    removed_files.append(str(target))
            # A zoned grid's directory, computed from the grid's name and never from a recorded
            # file's parent, taken only if the unlinks above emptied it (Phase 11b design §4).
            for record in matched:
                _remove_orphan_asides(paths, _record_grid_name(record))
                _rmdir_zone_dir(paths, _record_grid_name(record))
    return emit(
        SensorGridRemoved(
            project=loaded.paths.name,
            root=str(loaded.paths.root),
            removed=removed,
            removed_files=removed_files,
        )
    )


# Annotations are hints a host may use to decide what needs confirming -- clients "MUST consider
# tool annotations to be untrusted unless they come from trusted servers", so they never substitute
# for a refusal. `destructiveHint` defaults to **true** on the wire, so saying nothing is the
# cautious answer rather than the neutral one, and both annotations below are stated in full.
_READ_ONLY = ToolAnnotations(
    read_only_hint=True,
    destructive_hint=False,
    idempotent_hint=True,
    open_world_hint=False,
)

# Annotations are per-tool and destructiveness here is per-action: `project` holds both `describe`
# and `delete`, `job` holds both `status` and `cancel`. That is a real cost of collapsing CRUD into
# an `action` argument and it is paid explicitly -- the tool is annotated destructive and its
# description names which actions those are
# (docs/tool-surface.md#annotations-are-coarser-than-the-surface). `project(action="delete")` also
# takes a required `confirm`, because an untrusted hint is a weak thing to hang a directory deletion
# on and *"there SHOULD always be a human in the loop with the ability to deny tool invocations"*.
_DESTRUCTIVE = ToolAnnotations(
    read_only_hint=False,
    destructive_hint=True,
    idempotent_hint=False,
    open_world_hint=False,
)

# `_WRITES` -- read-only false, destructive false -- was defined here for `materials` and was removed
# with it: fix round 3 established that `materials(action="set"/"apply_defaults")` deregisters an
# input and deletes a file when it takes the last modifier out of the other action's file, so
# `destructiveHint: false` was under-declaring what a host asks the user to approve (ruling R17: the
# host prompt is the real consent gate). It was not to be re-added speculatively -- *"a genuinely
# write-but-never-remove tool can reintroduce it, with a test."*
#
# Phase 3's `run_point_in_time` is that tool, and this is that reintroduction. It writes exactly
# four paths. Three it owns outright, and none of the three is any other tool's or any user's:
# `08_results/point_in_time_<grid>.txt`, and the `04_skies/point_in_time.rad` /
# `06_octrees/point_in_time.oct` it compiles each run from (R730 moved both from the project root and
# registers all three). **The fourth, `manifest.json`, it does not own** -- that file is
# the whole project's shared manifest, and other tools write to it too (`project`, `geometry`,
# `materials`, `sensor_grid`, `weather`, `window_group`, `bsdf` and `run_annual` among them -- no
# quantifier over that list is measured here, so none is claimed). This tool's own write to it
# (since ruling R348, Phase 9 Task 14: every real call now seals its own octree,
# `register_artifact` -> `save_manifest`, so `export` has a real artifact to narrate) is one more
# writer among many, not a fourth exclusively-owned path. What carries the non-destructive claim for
# that fourth path is narrower and still holds: `register_artifact` only ever appends or replaces
# the three artifact ids this tool seals (`sky:point_in_time`, `octree:point_in_time` and
# `point_in_time:<grid>`; R730 added the first and the third) -- it deregisters no input, deletes
# no user file, and removes nothing any other tool or any caller put there. (This comment said
# "exactly three paths, all of which it owns" before R348 and did not name `manifest.json` at all;
# that drift was caught and corrected in R348's own fix round -- see
# `tests/test_point_in_time_tool.py::TestToolAnnotation::
# test_a_real_run_writes_exactly_the_paths_the_annotation_names` below -- and the "all of which it
# owns" clause was corrected a second time, in fix round 2, once it was read again against the
# fourth path the first correction had just added.) `tests/test_point_in_time_tool.py::
# TestToolAnnotation` asserts all three halves: the annotation as registered, that a real run leaves
# the manifest's *input* list untouched, and that the *set of files a real run touches on disk* is
# exactly this comment's own four names -- the third is what an artifact-registering change needs
# and the first two never covered, which is exactly how "exactly three paths" went stale silently
# the first time.
#
# R804 (item 36) states the boundary: an action is destructive when it can replace a regular file
# at a path the caller names, or a file registered as a project input. Replacing a file the tool
# itself names, at a fixed place inside the project it was called on, is not, which is why this
# tool's overwrite of its own previous results file leaves it `_WRITES`. `idempotentHint: true`:
# calling twice with the same arguments leaves the project in the state one call would have.
_WRITES = ToolAnnotations(
    read_only_hint=False,
    destructive_hint=False,
    idempotent_hint=True,
    open_world_hint=False,
)

# The registered surface is static: it never varies per connection or as a side effect of another
# call, so capability gating is a refusal at call time and never a hidden tool. Phase 2 adds
# `geometry` and `materials`; Phase 3 adds `run_point_in_time`; Phase 4 adds `weather`,
# `run_annual` and `results`; Phase 5 adds `bsdf`; Phase 6 adds `window_group`; Phase 8 adds
# `image` and `run_glare`; Phase 9 adds `quality`. The order is the wire order and is
# deterministic -- append order by build phase, not docs/tool-surface.md's own numbered list
# (which is topic order: `window_group` is tool 6 of nineteen there, but Phase 6 is the sixth
# *phase* to ship, after bsdf/Phase 5, so it is appended last here). `image` lands with only
# `action="inspect"` built (ruling R258, Task 4) -- the tool is registered now rather than held
# back, because the surface does not vary by which actions of a registered tool happen to work yet
# (the same posture `bsdf`/`geometry` take for their own not-yet-built actions). `run_glare`
# (Phase 8 Task 6) is appended after `image` -- Task 4 shipped first within the same phase.
# `quality` (Phase 9 Task 4) is appended last: all four of its actions are built in the same
# commit, so there is no live/specified split to note the way `image`'s had. `run_render` (Phase 9
# Task 10) is appended after it -- Task 4 shipped first within the same phase. `export` (Phase 9
# Task 13) is appended last of all: it is the phase's final tool and its own four actions
# (`"scripts"`, `"report"`, `"artifacts"`, `"manifest"`) are all built in this one commit, the same
# no-live/specified-split shape `quality` and `run_render` already have.
_TOOL_FUNCS = (
    radiance_status,
    sensor_grid,
    geometry,
    materials,
    project,
    job,
    run_point_in_time,
    weather,
    run_annual,
    results,
    bsdf,
    window_group,
    image,
    run_glare,
    quality,
    run_render,
    export,
    # Phase 11a
    luminaire,
    # Phase 11b
    run_electric,
    # Phase 11c
    run_ugr,
    # Phase 12 Task 9
    run_view_and_sunlight,
)

_ANNOTATIONS = {
    radiance_status: _READ_ONLY,
    # `sensor_grid` was read-only through Phase 1 and is not any more: Phase 2's upgrade gives it
    # `action="remove"`, and the rule is *"any tool with a destructive action is annotated destructive
    # and its description names which actions those are."* docs/tool-surface.md's own
    # [Annotations](#annotations-are-coarser-than-the-surface) section used to contradict that rule by
    # listing `sensor_grid` in the "genuinely read-only surface" sentence, which was written before §7
    # grew `remove`. **Resolved in the spec on 2026-08-08** in this code's favour, with the stale list
    # corrected and the reasoning recorded there; this comment is kept because the *why* is still
    # load-bearing. `destructiveHint` defaults to **true** on the wire, so claiming read-only for a
    # tool that deregisters a project input is the one direction a host cannot recover from.
    sensor_grid: _DESTRUCTIVE,
    geometry: _DESTRUCTIVE,
    materials: _DESTRUCTIVE,
    project: _DESTRUCTIVE,
    job: _DESTRUCTIVE,
    # See `_WRITES` above for why this tool is the one that earns that annotation back.
    run_point_in_time: _WRITES,
    # `weather` holds `action="remove"`, which deregisters a project input -- the same property that
    # took `sensor_grid` and `geometry` out of the read-only set. `destructiveHint` defaults to true
    # on the wire, so this is also the cautious answer for a tool whose `load` writes a `.wea` into
    # the project and replaces an input record.
    weather: _DESTRUCTIVE,
    # `run_annual` stays `_WRITES` under R804's boundary (item 36): it takes no caller-named path and
    # replaces only files it names itself inside the project it was called on -- its job directory,
    # the octree, the two matrices and the `.ill`. It deregisters no manifest input and deletes no
    # user file (`DESTRUCTIVE_ACTIONS` is empty).
    run_annual: _WRITES,
    # `results` stays `_WRITES` under R804's boundary (item 36): `action="export"` writes
    # `08_results/annual_<grid>.csv`, a name it fixes inside the project it was called on, and takes
    # no caller-named path. It deregisters no manifest input and deletes nothing
    # (`DESTRUCTIVE_ACTIONS` is empty; `tests/test_results_tool.py::TestToolAnnotation`). Not
    # `_READ_ONLY`: `readOnlyHint: true` over an action that writes a file is the one direction a
    # host cannot recover from.
    results: _WRITES,
    # `bsdf` earns `_DESTRUCTIVE` since R802 (item 34): `DESTRUCTIVE_ACTIONS` is `("generate",)`
    # (`rad_mcp.bsdf_tool`), because `generate` naming an existing BSDF replaces its XML in
    # `05_bsdf/` and registers it again, the precedent being `geometry`'s `add`; `inspect`,
    # `validate` and `list` write nothing. Not `_READ_ONLY`, and the annotation is per-tool rather
    # than per-action (docs/tool-surface.md#annotations-are-coarser-than-the-surface), so the tool's
    # description names the destructive action.
    bsdf: _DESTRUCTIVE,
    # `window_group` holds `action="remove"` (`window_group_tool.DESTRUCTIVE_ACTIONS`), which
    # deregisters a window-group input -- the same property that took `sensor_grid`, `geometry` and
    # `weather` out of the read-only set. `destructiveHint` defaults to true on the wire, so this is
    # also the cautious answer for a tool whose `define` writes a new manifest input.
    window_group: _DESTRUCTIVE,
    # `image` earns `_DESTRUCTIVE` since R802 (item 34): `action="falsecolor"`, `"tonemap"` and
    # `"convert"` each replace a regular file already at the caller's `out`, whether or not this
    # server wrote it (`image_tool.DESTRUCTIVE_ACTIONS`); `action="inspect"` only reads `path`.
    # `image` takes no `project` argument and registers nothing. Not `_READ_ONLY`: `readOnlyHint:
    # true` over three actions that write a file is the one direction a host cannot recover from.
    image: _DESTRUCTIVE,
    # `run_glare` writes and never removes -- the property `_WRITES` above requires. It renders a
    # fisheye HDR into this project's `09_images/hdr/` (`ProjectPaths.images_hdr`; unless `image=`
    # names an existing one), and writes a marked check image into the same directory when
    # `check_image=True` (the default). It takes no `action` argument, deregisters no manifest
    # input and deletes no user file, so it is not `_DESTRUCTIVE` -- the same property that earns
    # `run_annual` and `results` their own `_WRITES` rather than the coarser annotation. Not
    # `_READ_ONLY`: `readOnlyHint: true` over a tool that writes two files is the one direction a
    # host cannot recover from.
    #
    # **`idempotentHint` is what `_WRITES` says it is, and this comment used to overclaim it.**
    # Re-running against the same view does NOT replace that view's rendered HDR and check image:
    # both filenames carry a per-call discriminator (`glare_<stamp>-<uuid>.hdr`), so a second run
    # adds two more files and overwrites nothing. That is still the safe direction for the hint --
    # nothing a previous call produced is destroyed -- but the reason stated here was false, and a
    # caller reading it would expect one file per view. Corrected at the Phase 8 whole-branch fix
    # wave; `04_images_hdr/` above, which is not a directory this server has ever created, was
    # corrected in the same pass (finding I3).
    run_glare: _WRITES,
    # `quality` (Phase 9 Task 4) spawns no binary, writes no file and registers nothing in any
    # project on any of its four actions. `action="estimate"` takes a `project` argument -- part
    # of the published signature -- but this phase does not read it: no project in this codebase
    # yet records enough `wall_time_s` samples for a per-project read to mean anything, so every
    # estimate is `basis="uncalibrated"` regardless. Unlike `image`'s Phase 8 landing, this
    # annotation is not phase-scoped: all four actions are built now, and none of them ever
    # writes.
    quality: _READ_ONLY,
    # `run_render` writes and never removes -- the property `_WRITES` above requires. It writes a
    # rendered (and, for mode="falsecolor", colour-mapped) HDR into this project's
    # `09_images/hdr/` (`ProjectPaths.images_hdr`) and a converted LDR into `09_images/ldr/`
    # (`ProjectPaths.images_ldr`), both of which it owns. It takes no `action` argument
    # (`DESTRUCTIVE_ACTIONS` is empty), deregisters no manifest input and deletes no user file, so
    # it is not `_DESTRUCTIVE` -- the same property that earns `run_glare`/`run_annual`/`results`
    # their own `_WRITES` rather than the coarser annotation. Not `_READ_ONLY`: `readOnlyHint:
    # true` over a tool that writes two files is the one direction a host cannot recover from.
    # `idempotentHint: true` is what `_WRITES` says it is: re-running against the same view does
    # not replace a previous call's own HDR/LDR -- both filenames carry a per-call discriminator
    # (`render_<stamp>-<uuid>.hdr`), so a second run adds files and overwrites nothing.
    run_render: _WRITES,
    # `export` earns `_DESTRUCTIVE` since R804 (item 36): each of its four actions writes into a
    # caller-named `out` with `open_regular_file(path, "wb")`, so it replaces a regular file of the
    # same name there whoever wrote it -- the half of R804's boundary `image` meets (R802). Since
    # R805 it refuses an `out` that is a project root. Not `_READ_ONLY`: every action writes.
    export: _DESTRUCTIVE,
    # `luminaire` (Phase 11a): `remove` deletes a record and its files; `import` and `place` replace
    # by name (spec §5).
    luminaire: _DESTRUCTIVE,
    # `run_electric` (Phase 11b): writes `source.cal` (via `commit_lit_support`) and
    # `08_results/electric_<grid>.txt`; deletes nothing a caller registered -- `_WRITES`, as
    # `run_point_in_time` (design §12).
    run_electric: _WRITES,
    # `run_ugr` (Phase 11c): writes the glarefile under the project (design §6 step 8) and, through
    # `commit_lit_support`, `source.cal`; deletes nothing a caller registered -- `_WRITES`, as
    # `run_electric` (design §5). `UGR_DESTRUCTIVE_ACTIONS` is empty: it takes no `action` argument.
    run_ugr: _WRITES,
    # `run_view_and_sunlight` (Phase 12): `_READ_ONLY` until Task 10, and that annotation went
    # false the moment the `view_out` branch landed -- ruling T10.10, review I1. The branch writes
    # a temporary octree into the caller's own project directory
    # (`tempfile.mkstemp(dir=paths.root, ...)` in
    # `view_sunlight_tool._cast_in_temporary_octree`) and spawns
    # `xform`, `oconv` and `rtrace` for the work itself, on top of the version probe `discover()`
    # runs -- so `readOnlyHint: true` was telling a host that a tool which writes under the project
    # and runs binaries does neither. **It is the write that earns `_WRITES`, not the spawn**:
    # `radiance_status` spawns too (`discover()` probes `rpict -version`, falling back to `rtrace`)
    # and is read-only, correctly,
    # because it writes nothing. The siblings that write under a project already carry `_WRITES` --
    # `run_ugr`, `run_electric`, `run_glare`, `run_render`.
    #
    # `_WRITES` and not `_DESTRUCTIVE`: the octree is unlinked in a `finally` whatever happens and
    # nothing a caller registered is ever removed, which is the property `results`/`run_render`
    # earn their own `_WRITES` on. `VIEW_SUNLIGHT_DESTRUCTIVE_ACTIONS` is empty for the same reason
    # `UGR_DESTRUCTIVE_ACTIONS` is: this tool takes no `action` argument at all.
    # `idempotentHint: true` is what `_WRITES` says it is -- the temporary octree's name is fresh
    # per call and gone by the time the result is emitted, so a second call with the same arguments
    # leaves the project exactly as the first did.
    run_view_and_sunlight: _WRITES,
}

# What a host shows a person (R794). Plain English for a person. One per tool;
# tests/test_identity.py::TestToolTitles asserts the keys equal `_TOOL_FUNCS`.
_TITLES: dict[Callable[..., Any], str] = {
    radiance_status: "Radiance install check",
    project: "Project",
    job: "Background job",
    geometry: "Scene geometry",
    materials: "Materials",
    window_group: "Window group",
    sensor_grid: "Sensor grid",
    weather: "Weather file",
    bsdf: "BSDF (glazing and shading)",
    run_point_in_time: "Point-in-time illuminance",
    run_annual: "Annual daylight simulation",
    run_render: "Rendering",
    run_glare: "Daylight glare (DGP)",
    results: "Results and compliance",
    image: "Image processing",
    quality: "Simulation quality",
    export: "Export",
    luminaire: "Luminaires",
    run_electric: "Electric lighting illuminance",
    run_ugr: "Electric lighting glare (UGR)",
    run_view_and_sunlight: "View out and sunlight exposure",
}

for _tool in _TOOL_FUNCS:
    mcp.add_tool(_tool, title=_TITLES[_tool], annotations=_ANNOTATIONS[_tool])


def main() -> None:
    # stderr, explicitly: stdout is the transport.
    logging.basicConfig(stream=sys.stderr, level=logging.INFO)
    # Adopt the job records already on disk **before** the first request is served. `rad_engine.jobs`
    # does not know where projects live and will not scan for them, so without this a job id issued
    # by the previous process answers `JOB_NOT_FOUND` -- a wrong answer about a record that exists
    # (`rad_mcp.runtime`'s module docstring).
    runtime.current().startup()
    # R750: inline Radiance children run in their own session, so the client's group kill no
    # longer reaches them; the stop handlers do. Only here, never for an in-process client.
    stop_signals.enable()
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
