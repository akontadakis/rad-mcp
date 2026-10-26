"""`run_point_in_time` -- docs/tool-surface.md#10-run_point_in_time.

Phase 3's headline deliverable: the first tool that composes all three modules the phase built
(`rad_core.photometry`, `rad_engine.sky`, `rad_engine.rtrace`) into one shippable capability -- "a
user can get a standards-compliant grid of illuminance values from a scene." This module contains no
physics and builds no Radiance command line itself: it reads the project manifest for the registered
geometry, materials and sensor grid, resolves the sky through `rad_engine.sky`, builds the octree and
runs `rtrace` through `rad_engine.octree`/`rad_engine.rtrace`, and converts RGB irradiance to the
requested quantity through `rad_core.photometry`. `rad_mcp` plumbs and shapes the wire contract, per
docs/tool-surface.md §10's own "Layers" line.

**The inline/job split, and why this tool still runs inline.**
docs/tool-surface.md §3 once required `run_point_in_time` to return its result directly inside
`inline_timeout_s` and hand off to the job store otherwise; since R768 it says this tool, like every
tool but `run_annual`, runs inline and never hands off. When this module was written,
`rad_engine.jobs.Step` wired every step's stdin to `subprocess.DEVNULL` with no field to override it,
and `rtrace` reads its sensor points only from stdin (``rtrace [options] octree``, verified
against a live Radiance 6.1a man page), so the one invocation this tool exists to run could not be a
`jobs.Step`. **That gap in `rad_engine.jobs` is closed**: ruling R43 gave `Step` a `stdin_path` field
the worker opens and passes to the child, and `run_annual`'s chain uses it. This tool has not been
moved onto it. It still runs inline and **never submits a job**, so `job(action="status")` has no
point-in-time run to report and no hand-off (`rad_mcp.tasks.handoff`) is ever produced; `job_id` on
its result is synthesised, not a `JobStore` record.

Two ways of reaching the job store were rejected when the gap was open, and the reasons still say why
nothing here pretends to one: a `Step` running a Python wrapper that spawns `rtrace` would be `rad_mcp`
orchestrating Radiance processes outside `rad_engine.jobs` (the layer violation `tests/test_purity.py`
exists to catch), and running inline while calling it "the async path" would make an unreachable
hand-off branch look tested. Every check in docs/plan.md's Phase 3 table that exercises `rtrace` goes
through the inline path, using `rad_engine.sky`/`rad_engine.octree`/`rad_engine.rtrace` directly.

**What `export` reaches instead of a job (R729, R730).** Every real call registers three manifest
artifacts: `sky:point_in_time` (the sky file, `04_skies/point_in_time.rad`, with the argv that
regenerates it), `octree:point_in_time` (`06_octrees/point_in_time.oct`, naming the scene inputs and
that sky) and `point_in_time:<grid>` (the results file `08_results/point_in_time_<grid>.txt`, naming
the octree and the grid, with the flags `rtrace` was given). A lamp-only run has no sky, so it
registers no `sky:point_in_time` and its octree names none. The result carries `artifact_id`, and `export(artifact_id=...)` reconstructs the run from those
records.

**`window_group_states` is accepted honestly and refused unconditionally**, and **the reason it
gives changed at the Phase 7d-1 whole-branch fix wave.** It read *"no `window_group` tool exists yet
(Phase 6), so no BSDF can be bound to an aperture"* -- false since Phase 6a, and false on the wire:
`window_group` is a registered tool, `action="assign_bsdf"` binds a Klems XML into `07_matrices/`,
and the refusal fired on a project that had just done exactly that. A published sentence saying a
tool does not exist, about a tool a caller can list, is the mirror of a documented refusal that does
not exist -- the client stops rather than reading on. What is true is narrower and is about **this
tool**: `run_point_in_time` spawns `rtrace` over an octree, and this server has no route from a
bound BSDF state to that octree -- the matrix formulation is `run_annual`'s. So naming any key here
is refused rather than silently ignored, which would let a caller believe a blind state was honoured
when nothing changed. **Behaviour is unchanged; only the reason is.**

**`use_matrices` was withdrawn (R816, owner decision of 2026-10-05).** It was published and could
not be exercised: this tool has no reader for the V/T/D matrices `run_annual` seals, so `True` was
refused and `None` or `False` ran `rtrace`. The argument is gone from the input schema; a call that
still sends it is refused by `rad_mcp.server.RETIRED_ARGUMENTS` with `_use_matrices_retired`'s
message, which names the fix. `source` is always `"rtrace"`, and `POINT_IN_TIME_FROM_MATRICES` is
retired.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import math
import statistics as _statistics
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Annotated, Any, ClassVar, Final, Literal

from mcp_types import CallToolResult, ResourceLink, TextContent
from pydantic import BaseModel, ConfigDict, Field, RootModel, StrictFloat, StrictInt

from rad_core.grids import GRID_VERY_LARGE_POINTS, GridPoint, recorded_floor_mismatch
from rad_core.manifest import GROUND_REFLECTANCE_INPUT_ID, ProvenanceValue
from rad_core.photometry import daylight_factor, to_lux
from rad_engine import (
    NORTH_INPUT_ID,
    LoadedProject,
    ProjectError,
    ProjectPaths,
    RadianceError,
    RadianceNotFound,
    bytes_digest,
    discover,
    open_project,
    refuse_inline_commands_in_files,
    refuse_unscaled_geometry,
    register_artifact,
    worker_cores,
)
from rad_engine.env import RadianceEnv, locate
from rad_engine.materials import MaterialsFile, scene_files_with_materials
from rad_engine.octree import (
    EmptyOctreeError,
    OctreeSetOverflowError,
    OctreeTooManyFilesError,
    SceneInlineCommandError,
    build_octree,
)
from rad_engine.safe_io import (
    NotARegularFileError,
    open_regular_file,
    refuse_existing_non_regular_destination,
    refuse_linked_components,
    replace_file_atomically,
)
from rad_engine.rtrace import (
    QualityParameters,
    RtraceOutputError,
    RtraceRequest,
    ambient_bounces_low_advisory,
    ambient_interpolation_radius_m,
    build_rtrace_argv,
    direct_exact_forced,
    parameters_dict,
    resolve_quality,
    run_rtrace,
)
from rad_engine.sky import (
    CIE_SKY_TYPES,
    CIE_TYPES_WITH_SUN,
    EPW_DEFAULT_INTERVAL_MIN,
    GROUND_GLOW_RGB,
    DaylightFactorSkyRefused,
    SkyError,
    SkySite,
    SkySiteRequired,
    SkySpec,
    generate_sky,
    resolve_daylight_factor_sky,
    resolve_perez_inputs,
    sky_description_text,
    sky_generator_argv,
    sky_result_from_spec,
    validate_sky_dates,
)
from rad_engine.advisories import EngineAdvisory, classify
from rad_engine.ies import PhotometryUnverifiedError
from rad_mcp.advisories import to_wire, to_wire_all
from rad_mcp.lit_scene import (
    DAYLIGHT_FACTOR_WITH_LUMINAIRES_MESSAGE,
    LitPlacementView,
    LitSceneRefusal,
    commit_lit_support,
    excluded_advisory,
    lit_advisories,
    lit_placements_view,
    luminaire_edge_ids,
    near_field_advisory,
    placement_files,
    verify_lit_files,
)
from rad_mcp.names import SENSOR_GRID_INPUT_PREFIX, is_legal_name, name_refusal_reason
from rad_mcp.envelope import Advisory, RadResult, Refusal, emit, one_of_schema, refuse, refuse_missing
from rad_mcp.materials_tool import replay_materials
from rad_mcp.unknown_arguments import RetiredMessage, UnknownKeysRefused, unknown_key_advisories
from rad_mcp.sky_site import resolve_sky_inputs
from rad_mcp.paths import CodedWriteRefusal, PathOutsideRoot, RootsNotConfigured, contain_as_named, write_failure
from rad_mcp.scene_warnings import bsdf_not_applied_advisory, light_redirecting_advisory
from rad_mcp.runtime import Runtime, current, refuse_engine_error
from rad_mcp.timeouts import refuse_inline_timeout

logger = logging.getLogger(__name__)

__all__ = [
    "DEEP_POINT_THRESHOLD_M",
    "DESTRUCTIVE_ACTIONS",
    "GridFileChangedError",
    "SKY_ARTIFACT_ID",
    "PointInTimeResponse",
    "PointInTimeResult",
    "point_in_time_result_id",
    "run_point_in_time",
    "DEFAULT_INLINE_TIMEOUT_S",
    "GridEmptyOnDiskError",
    "GridNotFoundError",
    "GridRecordNameError",
    "GridUnreadableError",
    "MAX_INLINE_VALUES",
    "MAX_POINTS_BYTES",
    "NoGeometryRegisteredError",
    "QualityValue",
    "RESULT_MIME_TYPE",
    "RETIRED_ARGUMENTS",
    "refuse_grid_off_recorded_floor",
    "sky_key_advisories",
]

#: **Empty, and that is the claim.** Every other tool module exports this tuple so
#: `tests/test_protocol.py`'s check-0.7 sweep can derive `destructiveHint` from the surface itself
#: rather than from a list kept in the test -- *"a tool that grows one cannot satisfy this check by
#: the test not having heard about it."* `run_point_in_time` takes no `action` argument at all and
#: removes nothing (see `server.py`'s `_WRITES` comment), so its destructive-action set is empty,
#: which is exactly what makes `destructive_hint=False` derivable rather than asserted. Exported
#: rather than omitted so the sweep reads the same property off this module that it reads off the
#: other five.
DESTRUCTIVE_ACTIONS: tuple[str, ...] = ()

#: R801 (item 33, owner decision 2026-10-04): "luminance" was withdrawn from this enum rather than
#: built. A luminance in cd/m2 is `run_render`'s; this tool's `rtrace -I` measures irradiance. The
#: SDK's argument validation now refuses "luminance" like any other value outside the enum, before
#: `_validate_arguments` is reached, so this module has no refusal of its own for it.
Quantity = Literal["illuminance", "daylight_factor"]

#: The published default of `inline_timeout_s` (docs/tool-surface.md §10's signature). The spec
#: once said this tool hands off past it; since R768 it says the tool runs inline and never hands
#: off (§3), and `inline_timeout_s` is range-checked and bounds nothing here (§3's table). Every
#: call returns inline regardless of how long it takes -- see the module docstring.
DEFAULT_INLINE_TIMEOUT_S: float = 20.0

#: `quantity` -> the wire `unit` (docs/tool-surface.md §10's return block).
_UNIT: dict[str, str] = {"illuminance": "lux", "daylight_factor": "%"}

#: **The deep-point rule for check 3.6, and the judgement call this task owns.**
#:
#: docs/tool-surface.md §10 defines `DEEP_POINT_NOISE` as points "whose distance from the nearest
#: aperture puts them in the almost-entirely-inter-reflected regime, where repeat runs move +/-8 %
#: against +/-1 % near the window -- verified on the office fixture." The office fixture
#: (tests/fixtures/README.md) gives exactly three measured points and their observed half-spread:
#: 2.0 m from the window band reads +/-0.7 %, 7.5 m reads +/-4.5 %, 13.0 m reads +/-5.8 % (20 repeats,
#: `-ab 3 -ad 4096`, ruling R35). There is no fourth, intermediate measurement, so the boundary
#: between "near the window" and "almost entirely inter-reflected" cannot be read off measured data
#: with more precision than "somewhere between 7.5 m and 13.0 m."
#:
#: **What this module actually has to compute the rule from -- and what it therefore approximates.**
#: A point's true distance to the nearest *aperture* would need the aperture's own geometry (which
#: polygons are glazing), which this tool does not parse: geometry import and its role tagging are
#: `geometry`'s and `materials`' concern, and re-parsing the scene here to find glazing polygons
#: would duplicate work that task already owns for a different purpose. **The quantity computed here
#: is therefore not distance-to-nearest-aperture. It is a proxy, and it is reported as one** -- the
#: advisory message says so in the words a reader will see, not only here.
#:
#: The proxy: **a point's distance from the `y = y_min` edge of the room footprint, measured along
#: whichever horizontal axis the footprint is longer on.** Two properties of it are load-bearing and
#: both are assumptions, not facts this tool can check:
#:
#: 1. **It is one-sided.** Depth is measured from *one* edge, not from `min(near, far)`. A room has
#:    a façade and a back wall, and a point 2 m from the back wall is the *deepest* point in the
#:    room, not a shallow one. Measuring from the nearer of the two edges would score the office
#:    fixture's 13.0 m point at 2.0 m -- identical to its genuinely shallow 2.0 m point -- and would
#:    flag neither. (This is not hypothetical: the two-sided form was what this module shipped with
#:    and it is exactly why check 3.6 failed.)
#: 2. **It assumes the aperture is on the low edge of the deep axis.** For the office fixture the
#:    window band is on the `y = 0` façade and the back wall is at `y = 15`, so "distance from
#:    `y_min`" is the real depth-into-the-room. A scene whose glazing is on the *high* edge would be
#:    scored exactly backwards by this rule, and **this tool cannot today detect that it has been.**
#:    So would an interior grid lit from two sides, or a rooflight, for which "depth from a façade"
#:    is not a meaningful quantity at all. These are real false-positive and false-negative modes of
#:    the approximation and they are stated rather than hidden; closing them needs aperture polygons,
#:    which is a `geometry`/`materials` capability that does not exist yet.
#:
#: **The footprint is the room's, not the sensor cloud's, where the room's is knowable.** The grid's
#: own registration call records the `polygon` it was placed inside
#: (`InputRecord.registered_by.arguments["polygon"]`, written by `sensor_grid(action="create")`), and
#: that polygon is the room outline -- the thing whose low `y` edge is the façade. Falling back to
#: the sensor points' own bounding box would measure depth from the first *sensor row* instead, which
#: is inset from the wall by half a grid cell at best and by an arbitrary margin at worst; on the
#: office fixture's three canonical points that box starts at `y = 2.0`, which alone moves the 13 m
#: point to a computed depth of 11.0 m. The bounding box is still used when no polygon was recorded
#: (a `.pts` file registered by some route other than `sensor_grid`), because a slightly inset origin
#: is a better answer than no advisory at all -- and that fallback is why the threshold below has to
#: tolerate a metre or two of origin error.
#:
#: The threshold itself, in metres: set at **10.0 m**, strictly between the office fixture's own
#: 7.5 m point (measured half-spread +/-4.5 %, still inside check 3.1's own +/-6 % tolerance for that
#: depth, and not the "almost entirely inter-reflected" regime the register's prose describes) and
#: its 13.0 m point (+/-5.8 % measured, the deepest of the three and the one check 3.6 names by
#: number: "the 13 m point"). No fourth measured point exists to place the boundary more precisely
#: than "between 7.5 and 13"; 10.0 m is the midpoint of that gap, stated as a judgement call rather
#: than a second sourced fact. The midpoint is also the placement most tolerant of the bounding-box
#: fallback's origin error described above: it leaves 2.5 m of slack on either side.
DEEP_POINT_THRESHOLD_M: float = 10.0

#: A results file this tool commits gets this MIME type. **Reasoning stated because there is no
#: precedent for it anywhere in this codebase** -- no `resource_link` exists here before this task.
#: The file this tool writes is one numeric value per line, in `.pts` grid order: not a real
#: Radiance binary `.ill` matrix (that format and the manifest's `ill` artifact kind belong to
#: `run_annual`'s multi-column, multi-timestep matrix output, which this single-sky single-column
#: report does not attempt to be), and not a `.pts` sensor description either. It is a plain
#: newline-delimited text report a client can fetch and parse trivially, so `text/plain` is the
#: honest MIME type -- not `application/json` (the file is not JSON; the JSON half of the result is
#: `structuredContent`, a different thing) and not a Radiance-specific type (none is registered with
#: IANA, and inventing one would be exactly the kind of unsourced assertion this project avoids).
RESULT_MIME_TYPE = "text/plain"

#: How many sensor values may be inlined into the wire result before the list is truncated and
#: `RESULT_TRUNCATED` is attached.
#:
#: **Why a bound exists at all.** `values` was unbounded, and it is emitted into *both* a
#: `TextContent` JSON block and `structuredContent`, so the payload crosses the wire twice.
#: Measured: 25 000 points is ~0.43 MB per copy (~0.85 MB on the wire), and 1e6 points is ~17 MB
#: per copy (~34 MB on the wire). docs/tool-surface.md is explicit that this is not how bulk data
#: travels: *"Tool results have no pagination... bulk data is a resource link to the committed file
#: **plus a bounded summary** in `structuredContent`, and any tool result that drops rows says so
#: with `RESULT_TRUNCATED` at `warn`."* The `resource_link` this tool already emits exists precisely
#: to make inlining unnecessary, and was redundant with the inlined copy.
#:
#: **Why 20 000.** Taken from the spec's own `GRID_VERY_LARGE` threshold, which warns at 20 000
#: sensor points -- so the number at which this server already says "this grid is very large" is
#: the number at which it stops inlining every value. Choosing a second, unrelated constant would
#: put two different definitions of "large grid" in one tool. At the bound the inline copy is
#: ~0.35 MB per block, and every grid the spec considers ordinary is inlined complete.
#:
#: **The full data is never lost**: the committed `08_results/point_in_time_<grid>.txt` file holds
#: every value in grid order, the `resource_link` points at it, and `path` names it. `statistics`
#: are computed over the **full** set before truncation -- a mean over a truncated list would be a
#: wrong number, which is the class of defect this project exists to refuse.
MAX_INLINE_VALUES: int = 20_000

#: Metadata reads (`gensky`/`gendaylit` stdout, `getinfo -d`) get the same bound
#: `rad_engine.octree._METADATA_TIMEOUT_S` uses for the identical reason: they must never hang the
#: server. `rtrace` itself gets a longer, simulation-shaped bound below.
_METADATA_TIMEOUT_S = 30.0

#: How long the inline `rtrace` pass may run before this tool gives up rather than block a request
#: forever. Generous relative to the office fixture's own measured ~23 s *for the whole
#: three-point, 20-repeat fixture-verification run* (tests/fixtures/README.md, ruling R35) -- a
#: single-call `rtrace` invocation over one grid is far cheaper than that.
_RTRACE_TIMEOUT_S = 600.0

#: `-c`, `+s`, `-s`, `+i`, `-u` -- the five idealised CIE distributions, as `_resolve_sky` accepts
#: them on the wire. Duplicated from `rad_engine.sky.CIE_SKY_TYPES` as a literal tuple rather than
#: imported, because the message text below needs to name them in prose and importing a tuple only
#: to join it is no clearer than stating it once.
_CIE_SKY_TYPE_NAMES = ("clear", "clear_no_sun", "overcast", "intermediate", "uniform")

#: **The duplication above is justified; drifting from it is not.** `_CIE_SKY_TYPE_NAMES` is the
#: set `_resolve_sky` accepts, and `rad_engine.sky.CIE_SKY_TYPES` is the set the engine can build --
#: two copies of one fact, and a copy that can drift silently in the direction that matters. Dropping
#: `"uniform"` from the tuple above left **221 tests green** while a documented, Pydantic-accepted
#: sky type was refused at runtime: the wire schema still advertised it, `SkySpec` still validated
#: it, and only this tuple said no.
#:
#: Asserted at import rather than in a test, because the failure it guards against is one where the
#: module is *usable but wrong* -- an assertion here makes it unimportable instead, which is the
#: louder and earlier failure. `tests/test_advisories.py` uses the same discipline for the register.
assert set(_CIE_SKY_TYPE_NAMES) == set(CIE_SKY_TYPES), (
    "_CIE_SKY_TYPE_NAMES has drifted from rad_engine.sky.CIE_SKY_TYPES: "
    f"only here {sorted(set(_CIE_SKY_TYPE_NAMES) - set(CIE_SKY_TYPES))}, "
    f"only in the engine {sorted(set(CIE_SKY_TYPES) - set(_CIE_SKY_TYPE_NAMES))}. "
    "This tuple exists so the refusal message can name the types in prose (see above); it is not "
    "a second definition of which types are legal."
)

#: R719/R720: the keys `_resolve_sky` reads. Every type reads the first set; `perez` also reads the
#: second, and a CIE type ignores it -- so a perez-only key on a CIE sky is refused, not dropped.
#: R762: the site keys are `latitude`, `longitude_east`, `meridian_east` (east-positive), read by
#: `rad_mcp.sky_site.resolve_sky_inputs`.
_SKY_KEYS_EVERY_TYPE: Final = frozenset(
    {"type", "month", "day", "hour", "latitude", "longitude_east", "meridian_east"}
)
_SKY_KEYS_PEREZ_ONLY: Final = frozenset({"weather", "irradiance", "illuminance", "interval_min"})

#: R762: sky keys this server no longer reads, each with the message naming what replaced it. The
#: sign changed, so reading an old value under the new key would move the sun with no error.
_SKY_KEYS_RETIRED: Final = {
    "longitude": (
        "sky key 'longitude' is no longer accepted (ruling R762): it reached gensky -o unchanged, "
        "so it was WEST-positive (Athens was -23.72). Use 'longitude_east', which is east-positive "
        "like the weather tool's site.longitude_east (Athens 23.72) -- negate the value you were "
        "passing. Or leave the site out: with a registered weather file the sky reads it there."
    ),
    "meridian": (
        "sky key 'meridian' is no longer accepted (ruling R762): it reached gensky -m unchanged, "
        "so it was WEST-positive (Athens, UTC+2, was -30). Use 'meridian_east', which is "
        "east-positive, hours east of UTC times 15 (Athens 30) -- negate the value you were "
        "passing. Or leave the site out: with a registered weather file the sky reads it there."
    ),
}


def sky_key_advisories(sky_arg: Any) -> list[Advisory]:
    """R719/R720. `[]` for a non-dict (a `.rad` path is refused elsewhere) and for a `type` this
    server does not know -- the existing "not one of" refusal owns that, and a key list for an
    unknown type would be a guess."""
    if not isinstance(sky_arg, dict):
        return []
    sky_type = sky_arg.get("type")
    if sky_type != "perez" and sky_type not in _CIE_SKY_TYPE_NAMES:
        return []
    if sky_type == "perez":
        return unknown_key_advisories(
            "sky", sky_arg, _SKY_KEYS_EVERY_TYPE | _SKY_KEYS_PEREZ_ONLY, field="sky",
            explain=_SKY_KEYS_RETIRED,
        )
    explain = {
        **_SKY_KEYS_RETIRED,
        **{
            key: (f"sky key {key!r} is read only for type 'perez'; this sky is type {sky_type!r}, "
                  "so the key would be ignored. Remove it, or use type 'perez'.")
            for key in _SKY_KEYS_PEREZ_ONLY
        },
    }
    return unknown_key_advisories("sky", sky_arg, _SKY_KEYS_EVERY_TYPE, field="sky", explain=explain)


# --- nested wire shapes --------------------------------------------------------------------------


class _Nested(BaseModel):
    model_config = ConfigDict(extra="forbid")


# docs/tool-surface.md §10: `{min, max, mean, median, uniformity_u0}`. `uniformity_u0` is named
# differently from EN 12464-1's `U_o` on purpose, per the spec's own explicit warning. R810 moved
# the pointers out and added the unit, the full-set basis and the 0.0 at a zero mean
# (`_statistics_view`, called on `pipeline.values` before the inline bound).
# `results_tool.StatisticsView` shares the name.
class StatisticsView(_Nested):
    """Statistics over every sensor value of the run, in the result's `unit` (lux or %).

    Computed over the full grid, never over a truncated `values` list. `uniformity_u0` is `min`
    over `mean` for the whole grid, `0.0` when the mean is 0. It is **not** EN 12464-1's `U_o`
    for a task area or zone, and nothing here compares it with a threshold.
    """

    min: float
    max: float
    mean: float
    median: float
    uniformity_u0: float


# docs/tool-surface.md §10's `sky` block, copied from `rad_engine.sky.SkyResult` (built by
# `sky_result_from_spec`) rather than re-derived. R810 moved the pointers out and lifted what the
# fields hold: `resolved_from` from `_resolve_sky`, `dni_wm2`/`dhi_wm2` from `perez_irradiance`
# (`resolve_perez_inputs` returns irradiance or illuminance), and the `rotation_argv` field note
# (R694, `north_rotation_xform_argv`), which never reached the wire.
class SkyView(_Nested):
    """The sky this run generated.

    `resolved_from` is `"forced_overcast"` for `quantity="daylight_factor"`, `"epw"` for a perez sky
    whose irradiance was read from a registered weather file, and `"user"` otherwise. `dni_wm2` and
    `dhi_wm2` are the direct-normal and diffuse-horizontal irradiance a perez sky was given, `null`
    for any other sky and for a perez sky given as `illuminance`. `argv` is the exact sky-generator
    command line. `rotation_argv` is the `xform -rz` command the sky was piped through to apply the
    project's north, `null` when the project declares no north or north 0.
    """

    type: str
    resolved_from: Literal["epw", "user", "forced_overcast"]
    month: int
    day: int
    hour: float
    dni_wm2: float | None
    dhi_wm2: float | None
    ground_reflectance: ProvenanceValue
    argv: list[str]
    rotation_argv: list[str] | None
    """Ruling R694: the `xform -rz` argv the generated sky was piped through to apply the project's
    declared north, or `null` when the project declares no north or declares 0. See
    `rad_engine.sky.SkyResult.rotation_argv`, copied here rather than re-derived."""


# --- the result shape: the call's own result (this tool submits no job) --------------------------


# The module docstring's inline/job note says why this tool submits no job; the pointer to it left
# the published description under R809 (item 38). R810 moved out the history (the spec once named
# two delivery paths; since R768 §10 says this tool submits no job) and lifted the field notes on
# `values`, `sky`, `luminaires`, `path` and `artifact_id`, which never reached the wire. Added from
# `_dispatch`: `job_id = f"inline-..."`, `artifacts_used=None`, `wall_time_s` timed around
# `_run_pipeline` only (sky generation incl. any `xform -rz`, the sky-file write, octree build,
# rtrace and the photometric conversion; fix round 1 widened "octree build and trace"),
# `parameters` = `forced_parameters` after `direct_exact_forced`, and the overwrite
# (`_write_results_file`: one file per grid, by design).
class PointInTimeResult(RadResult):
    """One instant's sensor-grid result from `run_point_in_time`.

    **Always the call's own result:** this tool submits no job. `job_id` (`inline-<ms>`) is made up
    locally and names no job `job` can find; `artifact_id` (`point_in_time:<grid>`) is the handle
    `export(artifact_id=...)` takes to write a script that reproduces the run.

    `values` are in grid order and bounded at 20 000: `len(values) < n_points` means the list was
    cut, and `RESULT_TRUNCATED` says so. `statistics` cover every point, and `path` is the
    committed file holding every value, one per line, also linked as a `resource_link`; the next
    run on the same grid overwrites it.

    `sky` is `null` for a lamp-only run (`luminaires=true` with no `sky`); `luminaires` is absent
    unless `luminaires=true`. `parameters` are the `rtrace` settings traced, after any change
    `DIRECT_EXACT_FORCED` reports. `artifacts_used` is always `null`. `octree_cube` is the octree's
    bounding cube `[x, y, z, size]`, and `ambient_interpolation_radius_m` is size x `-aa` / `-ar`.
    `wall_time_s` is the run's wall-clock time in seconds: sky generation, octree build and trace;
    writing the results file and registering the run are not included.
    """

    quantity: Quantity
    unit: Literal["lux", "%"]
    grid: str
    n_points: int
    values: list[float]
    """The sensor values in grid order, **bounded** at `MAX_INLINE_VALUES`.

    `len(values) < n_points` means the list was truncated for the wire and `RESULT_TRUNCATED` is
    attached at `warn`. The complete data is always the committed file named by `path` and pointed
    at by the `resource_link`; `statistics` are always computed over the full set, never over this
    list. See `MAX_INLINE_VALUES` for the bound and why it is 20 000.
    """
    OMIT_WHEN_NONE: ClassVar[frozenset[str]] = frozenset({"advisories", "luminaires"})

    statistics: StatisticsView
    sky: SkyView | None
    """`null` for a lamp-only run (`luminaires=true` with no `sky`, Phase 11a spec §7, R458)."""
    luminaires: list[LitPlacementView] | None = None
    """Every placement a lit run included; absent from the payload when `luminaires=false`."""
    source: Literal["rtrace"]
    artifacts_used: list[str] | None = None
    parameters: dict[str, Any]
    ambient_interpolation_radius_m: float
    octree_cube: list[float]
    job_id: str
    wall_time_s: float
    path: str
    """The absolute path of the committed results file.

    **A field, not only a `resource_link` URI.** docs/tool-surface.md requires links *alongside*
    paths, not instead of them, because they are *"what an exported script needs and what a user
    pastes into `rvu`"* -- and a `file://` URI is neither. The path existed only inside the
    `resource_link`, while `_emit_with_resource_link`'s own docstring claimed it "stays inside
    `structuredContent` exactly as it already does on every other field this result carries." It did
    not: `PointInTimeResult.model_fields` had no path field at all.
    """
    artifact_id: str
    """R730: the manifest id this run's result is registered under, `point_in_time:<grid>` -- the
    handle `export(artifact_id=...)` takes to write a script that reproduces this run. It names the
    run's registered records rather than a job: this tool submits no job, and `job_id` above is
    synthesised, not a `JobStore` record."""


# A distinct shape from `PointInTimeResult` (no `values`, no `job_id`) so a caller cannot mistake
# "validation passed" for "here is a result carrying zero points". R810 corrected the old wire text,
# "every argument validated: nothing was run, nothing was refused": checks below the `dry_run`
# branch (octree build, the trace) are not made, and a real call adds advisories the dry run cannot
# (`DIRECT_EXACT_FORCED`, `RESULT_TRUNCATED`, in `_run_pipeline` and `_dispatch`). The checks above
# it spawn nothing (`_validate_scene`'s own contract). `annual_tool.DryRunOk` shares the name.
class DryRunOk(RadResult):
    """`run_point_in_time` with `dry_run=true`, and nothing was refused.

    The arguments, the project, the sky, the grid and the scene files were checked; no octree was
    built, nothing was traced, and there are no values. `advisories` holds those decided
    before the trace; a real call can add more, such as `DIRECT_EXACT_FORCED` and
    `RESULT_TRUNCATED`.
    """

    dry_run: Literal[True] = True


PointInTimePayload = PointInTimeResult | DryRunOk | Refusal


# The same `oneOf` pattern `ProjectResult`/`JobResult`/`SensorGridResponse` use. Declared as the
# tool's return annotation so the SDK validates every payload against it before it reaches the wire;
# the function body still returns a `CallToolResult` directly (via `emit` for the two plain shapes,
# and via `_emit_with_resource_link` for the real result, which needs an extra `resource_link`
# content block `emit` has no parameter for) -- the SDK validates `result.structured_content`
# against this model either way, per `FuncMetadata.convert_result`. R810 moved this out.
class PointInTimeResponse(RootModel[PointInTimePayload]):
    """`run_point_in_time`'s result: a `PointInTimeResult`, a `DryRunOk` when `dry_run=true`, or a
    refusal."""

    @classmethod
    def model_json_schema(cls, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return one_of_schema(super().model_json_schema(*args, **kwargs), PointInTimeResult)


# --- argument-shape refusals, shared by the real call and dry_run --------------------------------


def _refuse_argument(message: str, *, field: str) -> CallToolResult:
    return refuse(
        message,
        [Advisory(code="ARGUMENT_OUT_OF_RANGE", severity="error", field=field, message=message)],
    )


#: The published value type for a `quality` override dict.
#:
#: **Strict on purpose.** As `dict[str, float]` this accepted JSON ``true``: `bool` is an `int`
#: subclass and Pydantic's lax mode coerced it to ``1.0`` *at the schema boundary*, before any code
#: in this module ran, so ``{"ab": true}`` became ``-ab 1`` and no in-process guard could see it --
#: by then ``true`` and ``1`` were the same value. `_coerce_override` refuses `bool` for any
#: in-process caller and documents that it cannot for a wire one; this closes the wire half, which
#: is a change to the published contract rather than to any module's behaviour.
#:
#: ``StrictInt | StrictFloat`` rather than a single strict type because the flags are genuinely
#: mixed: ``ab``/``ad``/``as``/``ar`` are integers and ``aa`` is continuous. Measured against
#: Pydantic before it was written down -- ``{"ab": True}`` is refused, while ``{"ab": 4}``,
#: ``{"ad": 1024}`` and ``{"aa": 0.2}`` all still validate, and ``{"ab": 3.7}`` still reaches
#: `_coerce_override`'s own refusal rather than being rejected here as a type error, so a caller
#: asking for a fractional bounce count keeps the message that explains why it is refused. Note
#: ``int | float`` alone does **not** work: lax mode still admits ``true``.
QualityValue = StrictInt | StrictFloat


def _refuse_quality(quality: str | dict[str, float]) -> CallToolResult | None:
    """Every `quality` refusal, coded and shaped, **before anything is spawned**.

    `rad_engine.rtrace.resolve_quality` is the authority on what a legal `quality` is, and it
    signals every rejection by raising `ValueError` -- an unknown preset name, an unknown override
    key, a fractional value for an integer flag. Nothing caught that: a `ValueError` from an engine
    module is not a `ProjectError`, a `SkyError` or a `RadianceError`, so it escaped
    `run_point_in_time`'s handler chain entirely and reached the wire as a raw `ToolError` --
    `is_error=True` with `structured_content=None` and no advisory code, which **check 0.4
    forbids**. Reproduced through a real client session for `{"av": 5}`, `{"zzz": 1}`,
    `{"ab": 3.5}` and `"ultra"`, all four.

    Two refusals are made here rather than delegated, because `resolve_quality` cannot make them:

    1. **`-av` gets its own allocated code.** `AMBIENT_VALUE_SET` (`error`) is allocated in the
       register and named by docs/tool-surface.md engine invariant 5 -- *"A `quality` dict override
       setting `-av` on an illuminance, DF, glare or matrix run is refused (`AMBIENT_VALUE_SET`,
       `error`)"* -- and `rad_engine.rtrace` promises three separate times that the tool layer
       refuses it. Nothing raised it: `grep AMBIENT_VALUE_SET src/` found only docstrings. This is
       the raiser those three promises referred to. It is checked *before* `resolve_quality`, which
       would otherwise fold `av` into its generic unknown-key `ValueError` and lose the specific
       code the spec names.
    2. **`-ar 0` is refused here, not discovered later.** `ambient_interpolation_radius_m` divides
       by `-ar`, so `{"ar": 0}` raised a bare `ZeroDivisionError` -- and raised it *after* a full
       `rtrace` run had already been paid for (measured: 4.76 s on the office fixture at the default
       preset, and far longer at `high`). `resolve_quality` accepts 0 happily, so the check belongs
       at this layer, where it costs nothing and lands with the other argument refusals.

    Everything else is `resolve_quality`'s own judgement, re-raised here as a coded refusal carrying
    its message unchanged: the engine owns *what* is illegal, this layer owns *how a client is
    told*. `ARGUMENT_OUT_OF_RANGE` with `field="quality"` is the register's established code for a
    refused argument carried with its field name.

    **That delegation is what closes the all-zero-lux defect**, and it is worth naming here because
    the refusal is invisible in this function's own body. `rad_engine.rtrace._coerce_override` now
    refuses a non-positive or boolean value for the four integer flags (`rad_engine.rtrace.
    _ZERO_IS_LEGAL` carries the per-flag reasoning), and it does so by raising the same `ValueError`
    every other override refusal raises -- so it arrives here already shaped, with no second branch
    in this function to keep in sync. Measured at the wire before the fix: `{"ad": 0}`, `{"ad":
    -1024}` and `{"ab": -5}` each returned `is_error=False` with every sensor at `0.0` lux and a
    mean of `0.0`; all four now refuse with `ARGUMENT_OUT_OF_RANGE`. `-ar 0` keeps its own message
    above because it is refused for a different reason (it divides the interpolation radius, and the
    caller needs to be told *that*), and both refusals now agree on rejecting it.

    **`-ab 0` is deliberately still accepted**, and this is the one place the reasoning is visible
    from the tool layer: it returns `0.0` at every *interior* sensor, which looks exactly like the
    defect above but is correct physics (nothing reaches an interior point in zero bounces). The
    same flag on an unobstructed sensor returns a real number. `AMBIENT_BOUNCES_LOW` already warns
    on it. See `rad_engine.rtrace._ZERO_IS_LEGAL` for the measurements.

    JSON `true` never reaches this layer over MCP: `quality`'s override values are published as
    `StrictInt | StrictFloat` (`QualityValue`), so the SDK refuses a boolean at the schema. The
    `bool` refusal below serves an in-process Python caller only.
    """
    if isinstance(quality, dict):
        if "av" in quality:
            message = (
                f"quality override -av {quality['av']!r} is refused: the ambient value is a "
                "constant substituted for the indirect calculation, so a non-zero -av adds light "
                "that is not in the scene. docs/tool-surface.md engine invariant 5 requires -av to "
                "be zero on every quantitative run -- illuminance, daylight factor, glare and "
                "matrix work alike. Remove -av from the quality dict; it is reported as 0 in the "
                "result's `parameters`, which is what this run actually passes."
            )
            return refuse(
                message,
                [Advisory(
                    code="AMBIENT_VALUE_SET", severity="error", field="quality", message=message
                )],
            )
        if "ar" in quality:
            try:
                resolution = float(quality["ar"])
            except (TypeError, ValueError):
                resolution = None  # resolve_quality below owns the refusal for a non-numeric value
            if resolution == 0:
                message = (
                    "quality override -ar 0 is refused: the ambient resolution divides the ambient "
                    "interpolation radius (Rmin = maxSize x aa / ar, radiance-engine-overview.md), "
                    "so 0 has no meaning and the run cannot report "
                    "`ambient_interpolation_radius_m` at all. Pass a positive -ar (the presets use "
                    "128 to 1024), or use the 'reference' preset's -aa 0.0 if the intent is to "
                    "disable ambient interpolation."
                )
                return _refuse_argument(message, field="quality")

    try:
        resolve_quality(quality)
    except (ValueError, ArithmeticError) as exc:
        # **`ArithmeticError` beside `ValueError`, and the pairing is not symmetry for its own
        # sake.** `_coerce_override` narrows an integer flag with `int(value)`, and over an override
        # off the wire the two failure shapes differed: `int(nan)` raises `ValueError` and was
        # refused cleanly, while `int(inf)` raises `OverflowError` -- an `ArithmeticError`, which is
        # not a `ValueError` and was caught by nothing. `quality={"ab": 1e999}` therefore left
        # `run_point_in_time` as `isError=True` with **no `structuredContent`** (measured through
        # `mcp.call_tool`), the malformed envelope check 0.4 forbids. `_coerce_override` now refuses
        # a non-finite override before `int()` is reached, so this clause should not fire; it is
        # kept because the guard it backs is *"a promise held by a callee's current translation of
        # its own arithmetic"*, and `rad_core.grids._lm83_expected_points_or_none`'s docstring
        # already records why that is not a promise.
        return _refuse_argument(str(exc), field="quality")
    return None


def _use_matrices_retired(arguments: Mapping[str, Any]) -> str:
    return (
        "use_matrices was withdrawn: this tool always traces rays with rtrace, which is what "
        "use_matrices=false or null ran before. Omit use_matrices; for a result multiplied "
        "through the V, T and D matrices, use run_annual."
    )


#: Arguments this tool no longer takes (R816). Routed by `rad_mcp.server.RETIRED_ARGUMENTS`.
RETIRED_ARGUMENTS: dict[str, RetiredMessage] = {"use_matrices": _use_matrices_retired}


def _validate_arguments(
    *,
    # `str | None`, not `str`: `quantity` is optional on the wire (the SDK publishes it as such and
    # `refuse_missing` in `_dispatch` is what refuses its absence), so this function is genuinely
    # handed `None` for a call that omitted it. Typed honestly rather than narrowed, since every
    # comparison below is an equality test that `None` simply fails.
    quantity: str | None,
    sky: dict[str, Any] | str | None,
    window_group_states: dict[str, str] | None,
    luminaires: bool,
    quality: str | dict[str, float] = "standard",
    inline_timeout_s: float = DEFAULT_INLINE_TIMEOUT_S,
    grid: str | None = None,
) -> CallToolResult | None:
    """Every refusal `run_point_in_time` can produce **before a single Radiance process would be
    spawned**, in one function.

    This is what makes check 3.9 true by construction rather than by two implementations that happen
    to agree today: `dry_run=True` calls exactly this function (plus `_resolve_sky` inside
    `_dispatch`, the same call a real run makes) and stops before anything is spawned;
    `dry_run=False` calls the identical sequence and only proceeds to real work if none of it
    refused. There is one validation path, not a parallel copy that happens to agree today.

    `quality` and `inline_timeout_s` are validated **here** rather than deeper inside `_dispatch`,
    which is the ordering fix for two defects: a `quality` refusal used to escape as an uncaught
    `ValueError` (check 0.4), and `{"ar": 0}` used to be discovered by a `ZeroDivisionError` *after*
    a full `rtrace` run had been paid for. Both now refuse on the argument alone, identically under
    `dry_run`.
    """
    if window_group_states:
        # Refused honestly rather than silently ignored, which would let a caller believe a blind
        # state was applied when nothing changed. **The reason was measured false and corrected at
        # the whole-branch fix wave**: it said `window_group(action='assign_bsdf')` "is a Phase 6
        # deliverable that does not exist in this server", and it said so to callers whose project
        # had a group with a Klems file bound. The gap is in THIS tool, not in the binding tool.
        message = (
            f"window_group_states names {sorted(window_group_states)}, and this tool cannot honour "
            "a BSDF state: run_point_in_time traces rtrace over an octree, and a state bound by "
            "window_group(action='assign_bsdf') enters the T matrix of the three-phase multiply, "
            "which only run_annual builds. No scalar fallback is declared for one either. Omit "
            "window_group_states and run the scene as registered, or use run_annual for a study "
            "that multiplies through a bound state."
        )
        return _refuse_argument(message, field="window_group_states")

    if quantity == "daylight_factor" and sky is not None:
        # **Ruling R389 widened this from "a non-overcast dict" to "any sky at all".** It used to
        # read `isinstance(sky, dict) and sky.get("type") not in (None, "overcast")`, which let an
        # overcast dict of the caller's own through while `rad_core.photometry.daylight_factor` went
        # on dividing by its `reference_lux=10000.0` default -- this tool's one call site never
        # overrides it -- so the published percentage was a ratio between two different skies.
        # The test is now `sky is not None`, with no type test and no `isinstance`: a dict of any
        # type, a `.rad` path string and any other value are all refused on the same line, which is
        # what makes the claim in the `sky` parameter block below mechanical rather than a promise.
        #
        # ARGUMENT_OUT_OF_RANGE: a combination of arguments rejected on its face, before any work is
        # attempted -- the same class docs/tool-surface.md's register allocates the code for
        # (`project(action="delete")` without `confirm`, `job(action="wait")`'s timeout_s out of
        # bounds, an unknown `role` member). `field="sky"` names the argument that is actually
        # incompatible with the quantity requested: quantity says what the caller wants measured,
        # and it is the sky that cannot support it. No new code is allocated (R313): the engine's
        # `DaylightFactorSkyRefused` carries `code=None` and this layer supplies the register entry.
        message = (
            f"quantity='daylight_factor' was requested alongside sky={sky!r}. A daylight factor is "
            "defined as the ratio of the interior illuminance to the unobstructed exterior "
            "horizontal illuminance under the CIE standard overcast sky, conventionally 10 000 "
            "lux, and this tool divides by that 10 000 lux whichever sky is rendered "
            "(rad_core.photometry.daylight_factor's reference_lux default, never overridden here). "
            "A sky of your own is therefore measured against a reference it does not match: "
            "measured on this machine's Radiance, gensky 6 21 12.0 -c -g 0.2 is 1.82x the "
            "reference gensky 12 21 12.0 -c -g 0.2 -B 55.866 on both terms of its 3 2 row, so a "
            "200 lux sensor would publish 2.0 % where the definitional answer is 1.10 %. Omit sky "
            "entirely and the reference sky is substituted for you, reported by DF_SKY_FORCED; or "
            "keep your sky and ask for quantity='illuminance' instead."
        )
        return _refuse_argument(message, field="sky")

    if luminaires and quantity == "daylight_factor":
        # R457, spec §7's refusal order (R570): R388-R390's sky refusal just above, then this one --
        # all before the project is opened. (The luminance refusal that came first is gone: R801
        # withdrew "luminance" from `Quantity`, so the SDK refuses it before this function.) R580:
        # the message is one spelling shared with `run_render`'s own refusal
        # (`lit_scene.DAYLIGHT_FACTOR_WITH_LUMINAIRES_MESSAGE`); `code=`/`severity=` stay literal
        # here (R497).
        message = DAYLIGHT_FACTOR_WITH_LUMINAIRES_MESSAGE
        return refuse(message, [Advisory(code="DAYLIGHT_FACTOR_WITH_LUMINAIRES", severity="error", field="luminaires", message=message)])

    # **`inline_timeout_s` goes through the one shared spawn-timeout check** (Phase 13 Branch C,
    # ruling R704 -- `rad_mcp.timeouts.refuse_inline_timeout`), bounded by what the spawn's
    # timeout-to-C-int conversion can represent (`Popen.communicate`'s selector wait, which
    # `subprocess.run` made and `rad_engine.process_group.run_in_own_session` makes since R750), not merely "positive": before R704, a
    # caller-supplied `1e9`/`inf`/`nan` passed this clause's old `<= 0` test (false for all three)
    # and reached a live spawn (nothing here touches `inline_timeout_s` under `dry_run`, per the
    # KNOWN LIMITATION note below). `inf` and `1e9` there raised `OverflowError`, which
    # `run_point_in_time`'s own `except ArithmeticError` clause already caught and turned into a
    # code-less refusal; `nan` raised `ValueError`, which no clause in `run_point_in_time`'s
    # handler chain catches (measured), so `nan` escaped uncaught (`rad_mcp.timeouts`'s own
    # docstring records the measured boundary and this exact split). This also closes check 0.4
    # for the positive-but-unspawnable range: `run_electric` and `run_ugr` inherit the fix for
    # free, since both delegate to this function rather than checking `inline_timeout_s`
    # themselves.
    #
    # What this deliberately still does **not** do is claim every accepted value is honoured this
    # phase: every call still runs inline regardless (see `run_point_in_time`'s KNOWN LIMITATION and
    # the module docstring's inline/job note -- `rad_engine.jobs.Step.stdin_path` exists since R43,
    # and this tool has not been moved onto it).
    #
    # **`bool` is exempted here for an in-process Python caller, and that guard predates R704 --
    # but it is dead code on the wire.** `inline_timeout_s` is `StrictFloat`, so the MCP SDK refuses
    # a JSON `true`/`false` before this function is ever called (R854; until then it coerced them to
    # `1.0`/`0.0`). See `rad_mcp.timeouts`'s "Booleans" paragraph.
    if not isinstance(inline_timeout_s, bool):
        if (refusal := refuse_inline_timeout(inline_timeout_s)) is not None:
            return refusal

    if (refusal := _refuse_quality(quality)) is not None:
        return refusal

    # **`grid` is charset-validated here, before the project is opened** (Phase 11b final fix wave,
    # S1), `annual_tool`'s guard and reason: `grid` becomes the stem of `point_in_time_<grid>.txt`
    # (and `run_electric`'s `electric_<grid>.txt`, which calls this function too), and a hand-edited
    # record id such as `x/../../../notes` matched a `grid` of the same spelling and put the values
    # into `notes.txt` inside the roots. `is_legal_name` is the one rule (`rad_mcp.names`, R61)
    # `sensor_grid` names a grid by, so nothing it created is refused. `isinstance` first: a missing
    # `grid` is `refuse_missing`'s refusal, naming the absent argument.
    if isinstance(grid, str) and not is_legal_name(grid):
        return _refuse_argument(
            name_refusal_reason(grid, kind="sensor grid", examples="floor, level_2, open-plan"),
            field="grid",
        )
    return None


# --- reading the project's registered scene and grid ----------------------------------------------


class NoGeometryRegisteredError(ProjectError):
    """No `geometry` input is registered in this project. The caller -- `run_point_in_time`, or
    `run_glare`'s render branch, which shares `_scene_files` -- has nothing to build an octree from,
    distinguished from `OCTREE_EMPTY` in name only by when it is raised: here no
    file was ever named at all, so `rad_engine.octree.build_octree` is never even called. Reuses the
    same code (`OCTREE_EMPTY`, "bounding cube size 0") because the observable fact -- no compilable
    geometry -- is identical, and the register has no separate code for "no geometry was named."
    """

    code = "OCTREE_EMPTY"


class RegisteredSceneFileMissingError(ProjectError):
    """A geometry or materials file the manifest registers is not on disk (register N-107, ruling
    R851). `_scene_files` raises it, above every tool's `dry_run`: before it, a deleted geometry file
    passed `run_point_in_time(dry_run=True)` and the real call was refused only by `oconv`'s exit 2
    ("cannot open scene file"), with no code -- the advisory scan skips a file it cannot open, for
    the reason `rad_engine.octree.refuse_inline_commands_in_files` gives. `SCENE_FILE_NOT_FOUND` is
    the code `rad_engine.scene.SceneFileNotFoundError` carries for the same fact at registration."""

    code = "SCENE_FILE_NOT_FOUND"
    field = "project"


class GridNotFoundError(ProjectError):
    """No sensor grid named `grid` is registered. No register code: `JOB_NOT_FOUND`-style handle
    misses have codes because `job_id` and `project` are protocol handles docs/tool-surface.md names
    explicitly; a `grid` name is an ordinary tool argument with no code of its own allocated."""

    code = None


class GridZonedError(ProjectError):
    """A zoned EN 12464 grid given to a tool that traces one grid file (Phase 11b R610). Its zones are
    separate lattices with their own spacing; reading only the first would silently trace the first
    task area. `run_electric` is the tool that reads a zoned grid."""

    code = "GRID_ZONED"
    field = "grid"


class GridEmptyOnDiskError(ProjectError):
    """The registered grid's `.pts` file has no lines -- zero sensor points to trace.

    `GRID_EMPTY` (docs/tool-surface.md §10 refusals: "a grid with zero points inside the room
    polygon") is `rad_core.grids.GridEmptyError`'s own code, reused here at the identical severity
    for the identical claim -- reached by reading the file this tool was pointed at, rather than by
    re-running placement, since `sensor_grid(action='create')` already refuses an empty placement at
    write time. This is the read-time echo of that same fact for a `.pts` file that could in
    principle reach a project by a route other than `sensor_grid`.
    """

    code = "GRID_EMPTY"


class GridUnreadableError(ProjectError):
    """The registered grid's `.pts` file has lines, but one of them is not a sensor point.

    **Deliberately not `GRID_EMPTY`.** That code is defined (docs/tool-surface.md) as *"no sensor
    points survived placement and clipping"* -- a statement about a placement that produced nothing.
    A file whose lines are the wrong shape is a different fact: points were placed and written, and
    what is wrong is the *file*. Reporting the two under one code would describe one event by
    another's definition, which is the mistake ruling R5 exists to prevent, and it would send a
    caller to the wrong recovery (reshape the polygon, when the fix is to re-create the grid).

    **The register allocates no code for a malformed `.pts` file**, and none is invented here. The
    refusal therefore carries `ARGUMENT_OUT_OF_RANGE` with `field="grid"` -- the register's general
    code for a refused argument, naming the argument whose value cannot be used -- which is exactly
    how this module already refuses other unusable `grid` values. That gap is reported rather than
    filled with a code this project has not allocated.

    Before this existed, `_read_points`'s tuple unpacking raised a bare `ValueError` ("not enough
    values to unpack (expected 6, got 3)") that escaped `run_point_in_time`'s handler chain and
    reached the wire as `is_error=True` with `structured_content=None` -- a check-0.4 violation,
    reproduced through a real client session.
    """

    code = "ARGUMENT_OUT_OF_RANGE"


class GridFileChangedError(GridUnreadableError):
    """A registered grid's `.pts` no longer holds the bytes its record digested when `sensor_grid`
    wrote it -- edited or replaced outside the server (final review of 2026-10-05: a hand edit that
    moved points in x or y, keeping the floor, was traced and published silently; a z edit is caught
    by `GRID_NOT_ON_RECORDED_FLOOR`). `SENSOR_GRID_FILES_CHANGED`, `field: "grid"`, as `run_electric`
    refuses a zone file whose digest differs; the remedy is to re-create the grid. A subclass of
    `GridUnreadableError` so every reader's existing clause for an unusable `.pts` catches it."""

    code = "SENSOR_GRID_FILES_CHANGED"


class GridOffRecordedFloorError(ProjectError):
    """A registered grid whose points are not on the floor its record names (R776, owner decision E3
    of 2026-10-03): what a grid created before R763 over a floor above its height looks like, and,
    for a zoned grid, over any floor more than 1 mm from z = 0. An unzoned grid created before R763
    over a floor between 0 and its height, or below zero, is not detected: a record created before
    R784 carries no `height`. Still open item 29 is closed by R784 for grids created from then on:
    an unzoned record carries `height` and is judged at the floor plus it.
    `rad_core.grids.recorded_floor_mismatch` is the rule; `run_point_in_time`, `run_annual`,
    `run_electric` and `results` refuse through `refuse_grid_off_recorded_floor`."""

    code = "GRID_NOT_ON_RECORDED_FLOOR"
    field = "grid"


def refuse_grid_off_recorded_floor(
    grid: str, polygon: object, points: Sequence[GridPoint], *, height: object = None,
    outcome: str = "Nothing was traced.",
) -> None:
    """Raise `GridOffRecordedFloorError` when `recorded_floor_mismatch` names a reason (R776).

    `outcome` is the message's last sentence: the run tools trace nothing, and `results`, which
    traces nothing in any case, passes its own ("No stored value was read.")."""
    reason = recorded_floor_mismatch(polygon, [point.z for point in points], height=height)
    if reason is not None:
        raise GridOffRecordedFloorError(
            f"sensor grid {grid!r}: {reason} Every value traced from it is for points off the "
            f"reference plane its record names. Re-create it with sensor_grid(action='create', "
            f"name={grid!r}, ...) over the same polygon, with a height of zero or more. {outcome}"
        )


class GridRecordNameError(GridUnreadableError):
    """The matched sensor-grid record's id is not `sensor_grid_<legal name>` (Phase 11b final fix
    wave, S1). A hand-edited `record.id` such as `x/../../../notes` would otherwise become the stem of
    the results file this tool writes. Refused as the argument is (`ARGUMENT_OUT_OF_RANGE`,
    `field="grid"`, `GridUnreadableError`'s code and handler). A second, independent guard, so neither
    depends on the other being right -- and one that does fire: `_grid_points_file` matches a record by
    `grid` itself as well as by `sensor_grid_<grid>`, so a `grid` of `sensor_grid_.x`, which
    `is_legal_name` accepts, matches the record `sensor_grid_.x` and leaves the suffix `.x`, which it
    does not. (The re-review's B3: this docstring used to call the guard unreachable while the argument
    guard stands.)"""


#: What `_scene_files` tells a caller to do when the project has no geometry at all. A default
#: rather than a fixed string because `rad_mcp.glare_tool` shares this function and has a second
#: recovery its caller can take (`image=`), and a message that named only this tool's would be
#: wrong there -- Phase 8 whole-branch review, finding C2.
_NO_GEOMETRY_RECOVERY = (
    "Call geometry(action='add') with at least one scene before running a point-in-time study -- "
    "there is nothing to build an octree from."
)


def _scene_files(
    loaded: LoadedProject, *, luminaires: bool, no_geometry_recovery: str = _NO_GEOMETRY_RECOVERY
) -> list[str]:
    """Every registered geometry file, materials-first, in `oconv`/`xform`'s required order, then --
    only when `luminaires` -- every luminaire placement file (Phase 11a spec §7, R478).

    `luminaires` has no default (R478), and every caller states it. `run_point_in_time`,
    `run_render` and -- since Phase 11c Task 13 -- `run_glare` pass through their own caller's
    switch; `run_electric` and `run_ugr` pass `True` unconditionally, because both are lamp-only
    measurements with no unlit branch to offer; `run_annual` passes `False`. *(This paragraph said
    "only `run_point_in_time` and `run_render` pass `True`" until Phase 11c; `run_electric` and
    then `run_glare`'s new switch each falsified it, and it is now written off the caller list
    rather than off a pair of names.)* The placement files are checked by `rad_mcp.lit_scene.
    verify_lit_files` in the caller, which holds the runtime this function does not -- every caller
    that can pass `True` does so, `run_glare`'s lit branch included since that task's fix round.

    Reuses `rad_engine.materials.scene_files_with_materials` -- the one place this ordering
    guarantee is built -- rather than re-assembling the list here, exactly as `geometry_tool` does.
    Modifiers are re-derived from each materials input's own recorded registration call through
    `rad_mcp.materials_tool.replay_materials`, the same route `materials(action="list")` and
    `geometry` both already take, so this module does not need a second way to reconstruct what a
    materials input actually defines.

    **Shared with `rad_mcp.glare_tool._render`** since the Phase 8 whole-branch review: that
    function had this one's geometry half copied with its materials half deleted, so a project
    registered the documented way (`geometry(action="add")` plus `materials(action="set")`)
    compiled with no modifiers and `oconv` exited 1. `no_geometry_recovery` is the one sentence
    that legitimately differs between the two callers -- `run_glare` can also be handed an existing
    `image=` -- and it is a parameter rather than a second copy of the function for exactly the
    reason this docstring's first paragraph gives.
    """
    # Design §5.11, amendment 4: this is the shared reader `run_point_in_time`, `run_render`,
    # `run_annual` and `run_glare`'s render path all call before any octree or matrix is built or
    # reused, so it is where a pre-Phase-10 unscaled-in-place record refuses every run.
    refuse_unscaled_geometry(loaded)
    materials_files = [
        MaterialsFile(
            path=Path(ref.path),
            modifiers=frozenset(m.modifier for m in replay_materials(record)),
        )
        for record in loaded.manifest.inputs
        if record.kind == "materials"
        for ref in record.files
    ]
    geometry_files = [
        ref.path
        for record in loaded.manifest.inputs
        if record.kind == "geometry"
        for ref in record.files
    ]
    if not geometry_files:
        raise NoGeometryRegisteredError(
            f"no geometry is registered in this project. {no_geometry_recovery}"
        )
    # N-107 (R851): every top-level file the octree would be compiled from is on disk, or the call
    # is refused here, coded, rather than by `oconv` after `dry_run` said it would run. A path the
    # manifest records relative resolves against the project root, `oconv`'s working directory.
    for kind, path in [*(("materials", str(m.path)) for m in materials_files),
                       *(("geometry", path) for path in geometry_files)]:
        try:
            os.stat(loaded.paths.root / path)
        except (FileNotFoundError, NotADirectoryError):
            recovery = (
                "Restore the file, or remove the input with geometry(action='remove', ...) and add "
                "the geometry again."
                if kind == "geometry"
                else "Restore the file, or write the materials again with materials(action='set', ...)."
            )
            raise RegisteredSceneFileMissingError(
                f"the registered {kind} file {path} is not on disk, so the scene this project records "
                f"cannot be compiled. Nothing has been run. {recovery}"
            ) from None
    lamps = placement_files(loaded.manifest) if luminaires else []
    if not materials_files:
        return [*geometry_files, *lamps]
    return [*scene_files_with_materials(materials_files, geometry_files), *lamps]


def _grid_candidates(grid: str) -> set[str]:
    """The two record ids `grid` names: itself and `sensor_grid_<grid>` -- one definition, read by
    `_grid_points_file` and by `_validate_scene`'s lookup of the record id it matched."""
    return {grid, f"{SENSOR_GRID_INPUT_PREFIX}{grid}"}


def _grid_points_file(
    loaded: LoadedProject, grid: str
) -> tuple[Path, list[list[float]] | None, object, str]:
    """The `.pts` file registered under `grid`'s handle -- either `grid` or `sensor_grid_<grid>`,
    matching `sensor_grid`'s own two-name convention (`server.py`'s `_remove_grid`) -- and the room
    polygon that grid was placed inside, where its registration recorded one.

    The polygon comes back with the path rather than from a second lookup because it is read off the
    same `InputRecord`: two functions walking `manifest.inputs` for the same record could disagree
    about which record they found. `None` where the grid reached the project by a route that
    recorded no polygon -- see `DEEP_POINT_THRESHOLD_M` for what that costs.

    The third element is the `height` the record carries (R784: an unzoned grid records it since
    R784, a zoned one always did), or `None`, for `recorded_floor_mismatch`'s second rule. The
    fourth is the file's recorded digest, which `_read_points` compares with the bytes it reads.
    """
    candidates = _grid_candidates(grid)
    for record in loaded.manifest.inputs:
        if record.kind == "sensor_grid" and record.id in candidates:
            if not is_legal_name(record.id.removeprefix(SENSOR_GRID_INPUT_PREFIX)):
                raise GridRecordNameError(
                    f"sensor grid {grid!r} is registered under the id {record.id!r}, which is not "
                    f"'{SENSOR_GRID_INPUT_PREFIX}' followed by a legal name. This server never writes "
                    "such an id; the manifest has been edited. Nothing was read or written. Remove the "
                    "record and re-create the grid with sensor_grid(action='create')."
                )
            if record.zones is not None:
                raise GridZonedError(
                    f"sensor grid {grid!r} is a zoned EN 12464 grid; this tool traces one grid file. "
                    "Use run_electric for it, or create an unzoned grid with sensor_grid(action='create')."
                )
            if not record.files:
                raise GridEmptyOnDiskError(
                    f"sensor grid {grid!r} is registered with no files -- nothing to trace."
                )
            polygon = None
            height = None
            if record.registered_by is not None:
                polygon = record.registered_by.arguments.get("polygon")
                height = record.registered_by.arguments.get("height")
            return Path(record.files[0].path), polygon, height, record.files[0].digest
    raise GridNotFoundError(
        f"no sensor grid named {grid!r} is registered in this project. Call "
        f"sensor_grid(action='create', name={grid!r}, ...) first, or sensor_grid(action='list') to "
        "see what is registered."
    )


#: The ceiling on a registered `.pts`, in bytes. **`read_epw`'s `MAX_EPW_BYTES` posture, applied to
#: the other manifest-derived file this server re-opens** (ruling R50: a path out of the manifest is
#: untrusted on every action that touches it).
#:
#: Sized from `GRID_VERY_LARGE_POINTS`: a `.pts` line is six floats, ~48 bytes, so this repo's own
#: "very large" grid is ~1 MB. 16 MB is an order of magnitude clear of that, which means the bound
#: refuses only files that were never a sensor grid -- the same property that makes
#: `MAX_EPW_BYTES`'s 16 MB defensible against a ~1.5 MB hourly year.
MAX_POINTS_BYTES: int = 16 * 1_048_576


def _read_points(path: Path, recorded_digest: str | None = None, *,
                 grid: str | None = None) -> tuple[list[GridPoint], str]:
    """The `.pts` file's own points, parsed back into `GridPoint`s, and its raw text (what `rtrace`
    is actually fed on stdin) -- read once and reused for both, so the two can never disagree.

    Given the record's `recorded_digest`, the bytes read are digested and compared first (as
    `rad_mcp.electric_tool._read_zones` does for a zoned grid): a file edited or replaced since
    `sensor_grid` wrote it is `GridFileChangedError`, never traced or published (final review)."""
    try:
        # `open_regular_file` and not `read_text`: the registered `.pts` is a **manifest-derived**
        # path, contained when the grid was created and re-opened here with no re-containment.
        # `read_text` on a FIFO blocks in `open()` forever -- and containment cannot close that,
        # because a FIFO planted inside the project is legitimately contained. Only `O_NONBLOCK`
        # plus `S_ISREG` on the opened descriptor refuses it, and neither needs a `roots` argument.
        # Reproduced by hand-editing `manifest.json`: the `anyio` worker wedged and the process
        # needed `kill -9`.
        #
        # `NotARegularFileError` and `O_NOFOLLOW`'s `ELOOP` are both `OSError`s, so both land in the
        # existing handler below and become `GridUnreadableError` -- the coded refusal this site
        # already shapes, now reached instead of a hang. The decode moves here from `read_text`'s
        # keyword and keeps `errors` strict: a `.pts` is six floats per line, and a file that is not
        # decodable is not one, so `UnicodeDecodeError` remains the parse failure it was rather than
        # being surrogate-escaped into a bogus point.
        with open_regular_file(path, "rb") as handle:
            # **Bounded, and measured on the descriptor** -- `read_epw`'s posture under ruling R50,
            # applied to the other manifest-derived file this server re-opens. This was
            # `handle.read()`, unbounded, reading to EOF: a `.pts` path is a manifest record, R50
            # holds those untrusted, and the read is newly reachable from `blind_schedule` and
            # `compliance` (via `_analysis_area`) as well as from this tool. `open_regular_file`
            # already closes the FIFO window; it does not bound a large regular file.
            #
            # `MAX_POINTS_BYTES` is sized against `GRID_VERY_LARGE_POINTS` at ~48 bytes a line, an
            # order of magnitude clear of anything `place_sensor_grid` writes, so it refuses only
            # files that were never a sensor grid. Bounded **before** the read, because the read is
            # what commits the memory.
            size = os.fstat(handle.fileno()).st_size
            if size > MAX_POINTS_BYTES:
                raise GridUnreadableError(
                    f"the sensor grid file at {path} is {size / 1_048_576:.1f} MB, over the "
                    f"{MAX_POINTS_BYTES // 1_048_576} MB limit for a .pts. A "
                    f"{GRID_VERY_LARGE_POINTS}-point grid is under 1 MB, so a file this large is "
                    "not a sensor grid. Nothing about its content has been read. Re-create it "
                    "with sensor_grid(action='create')."
                )
            data = handle.read()
    except OSError as exc:
        # The registered `.pts` is not a readable file -- a directory stands where it should be, a
        # permission was withdrawn, the mount went away. `read_text` raises `OSError`, which no
        # handler in this module's chain catches, so it reached the wire as `is_error=True` with
        # `structured_content=None` and an absolute path in the message. Same check-0.4 violation
        # `GridUnreadableError` already exists for, one layer earlier: the file cannot be read at
        # all rather than cannot be parsed.
        raise GridUnreadableError(
            f"{path} cannot be read as a sensor grid: {exc.strerror or exc}. Re-create it with "
            "sensor_grid(action='create')."
        ) from None
    if recorded_digest is not None and bytes_digest(data) != recorded_digest:
        named = f"the file of sensor grid {grid!r}" if grid is not None else "the sensor grid file"
        raise GridFileChangedError(
            f"{named} ({path}) no longer holds the points sensor_grid wrote (its digest differs from the "
            "grid's record): it was edited or replaced outside this server. Nothing has been traced. "
            f"Re-create the grid with sensor_grid(action='create'{f', name={grid!r}' if grid else ''}, ...)."
        )
    text = data.decode("utf-8")
    return _parse_points(text, path), text


def _parse_points(text: str, path: Path) -> list[GridPoint]:
    """`text` -- a `.pts` file's decoded contents, already read -- parsed into `GridPoint`s, or the
    coded refusal for a line that is not a sensor point. Split out of `_read_points` so a caller that
    must digest the same bytes it parses (`rad_mcp.electric_tool._read_zones`, Phase 11b F13) opens
    the file once; `path` only names the file in a refusal."""
    points: list[GridPoint] = []
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        # A `.pts` line is six floats: three for the position, three for the direction. Anything
        # else is refused by name here rather than left to raise a bare `ValueError` out of the
        # tuple unpacking -- see `GridUnreadableError` for why that mattered and why this does not
        # claim `GRID_EMPTY`. `float()`'s own failure is caught for the same reason: a line of six
        # non-numeric tokens is as unusable as a line of three numeric ones.
        tokens = line.split()
        try:
            x, y, z, dx, dy, dz = (float(tok) for tok in tokens)
        except ValueError:
            raise GridUnreadableError(
                f"{path} line {number} is not a sensor point: a .pts line must be six numbers "
                f"(x y z dx dy dz), and this one is {line.strip()[:80]!r} ({len(tokens)} field(s)). "
                "The grid cannot be traced as written -- re-create it with "
                "sensor_grid(action='create')."
            ) from None
        # `float()` accepts "nan", "inf" and "-inf", so the parse above is not the finiteness check
        # it looks like. Left through, a NaN coordinate reaches `rtrace` and comes back as a NaN
        # value whose statistics raise `fmean requires at least one data point` -- a bare
        # `StatisticsError` on the wire with `structured_content=None`, after the trace was paid
        # for. Refused here, at the same boundary and under the same code as any other unusable
        # line, because a sensor at an undefined position is not a sensor.
        if not all(map(math.isfinite, (x, y, z, dx, dy, dz))):
            raise GridUnreadableError(
                f"{path} line {number} places a sensor at a non-finite coordinate "
                f"({line.strip()[:80]!r}). A point at nan or inf has no position to trace from -- "
                "re-create the grid with sensor_grid(action='create')."
            )
        points.append(GridPoint(x=x, y=y, z=z, dx=dx, dy=dy, dz=dz))
    if not points:
        raise GridEmptyOnDiskError(
            f"{path} has no sensor points -- a study over zero sensors traces nothing."
        )
    return points


# --- check 3.6: DEEP_POINT_NOISE -------------------------------------------------------------------


def _bounding_rectangle(
    xs: list[float], ys: list[float]
) -> tuple[float, float, float, float]:
    return min(xs), max(xs), min(ys), max(ys)


def _room_footprint(
    points: list[GridPoint], polygon: list[list[float]] | None
) -> tuple[float, float, float, float]:
    """The rectangle depth is measured inside: the room's own outline where `sensor_grid` recorded
    one, else the sensor cloud's bounding box. See `DEEP_POINT_THRESHOLD_M` for why the distinction
    is worth making and what the fallback costs."""
    if polygon:
        try:
            xs = [float(vertex[0]) for vertex in polygon]
            ys = [float(vertex[1]) for vertex in polygon]
        except (TypeError, ValueError, IndexError):
            # A malformed recorded polygon is not worth refusing a whole simulation over -- the
            # advisory degrades to the sensor-cloud fallback, which is the same answer this function
            # gives for a grid that never recorded one.
            logger.info("ignoring unparseable recorded grid polygon %r", polygon)
        else:
            if xs and ys:
                return _bounding_rectangle(xs, ys)
    return _bounding_rectangle([p.x for p in points], [p.y for p in points])


def deep_point_indices(
    points: list[GridPoint], polygon: list[list[float]] | None = None
) -> list[int]:
    """Indices of points whose distance-to-nearest-aperture *proxy* (`DEEP_POINT_THRESHOLD_M`'s own
    docstring) meets or exceeds `DEEP_POINT_THRESHOLD_M`. That constant carries the full reasoning
    and every assumption this makes; here is only its arithmetic.

    One-sided by construction: depth runs from the **low** edge of the footprint's longer horizontal
    axis, on the assumption that the aperture is on that edge. Measuring from the nearer of the two
    edges would call the deepest point in a room a shallow one.
    """
    if not points:
        return []
    x_min, x_max, y_min, y_max = _room_footprint(points, polygon)
    deep: list[int] = []
    for index, point in enumerate(points):
        if (y_max - y_min) >= (x_max - x_min):
            depth = point.y - y_min
        else:
            depth = point.x - x_min
        if depth >= DEEP_POINT_THRESHOLD_M:
            deep.append(index)
    return deep


def _deep_point_noise_advisory(
    points: list[GridPoint], polygon: list[list[float]] | None = None
) -> Advisory | None:
    indices = deep_point_indices(points, polygon)
    if not indices:
        return None
    message = (
        f"point(s) {indices} sit at or beyond {DEEP_POINT_THRESHOLD_M:g} m into the room along its "
        "longer horizontal axis, measured from that axis' low edge. **This is a proxy for distance "
        "to the nearest aperture, not that distance**: this tool does not parse which polygon is "
        "glazing, so it assumes the aperture is on that low edge -- a scene glazed on the opposite "
        "edge, lit from two sides, or lit from a rooflight would be scored wrongly and this tool "
        "cannot detect that it has been (see the DEEP_POINT_THRESHOLD_M docstring). Points this "
        "deep are almost entirely inter-reflected, where repeat runs move roughly +/-8 % against "
        "+/-1 % near the window (verified on the office fixture). Raise -ad/-ab rather than "
        "re-running."
    )
    return Advisory(code="DEEP_POINT_NOISE", severity="warn", field="values", message=message)


# --- RESULT_IS_INITIAL_ILLUMINANCE -----------------------------------------------------------------


def _initial_illuminance_advisory(quantity: str) -> Advisory | None:
    """`RESULT_IS_INITIAL_ILLUMINANCE` (info) -- docs/tool-surface.md §10: attached "whenever the
    number could be compared to an EN 12464-1 E_m."

    **Judgement call: fires for `quantity="illuminance"` and never for `luminance` or
    `daylight_factor`.** EN 12464-1's maintained illuminance `E_m` is stated in lux against a task
    area -- a luminance in cd/m2 is not the same physical quantity and is never compared to `E_m` at
    all, and a daylight factor is a percentage normalised against a fixed reference sky, compared
    against EN 17037's DF thresholds rather than EN 12464-1's `E_m` table. `illuminance` in lux is
    the one quantity this tool can produce that is literally the same unit and the same physical
    measurand `E_m` names, so it is the one quantity where a caller could put this number directly
    against that table with no unit conversion -- exactly the comparison the advisory exists to
    caveat as "initial, not maintained."
    """
    if quantity != "illuminance":
        return None
    return Advisory(
        code="RESULT_IS_INITIAL_ILLUMINANCE",
        severity="info",
        field="values",
        message=(
            "these are initial illuminance values, not maintained -- no maintenance factor has been "
            "applied. An EN 12464-1 E_m comparison against these numbers is a comparison against a "
            "brand-new installation, not one at the end of its maintenance cycle."
        ),
    )


# --- sky resolution ---------------------------------------------------------------------------------


def _supplied_sky_label(sky_arg: object) -> str:
    """How the wire `sky` value is named inside `DaylightFactorSkyRefused`'s message (ruling R389).

    The engine exception takes a *description*, not a sky type, precisely because the wire value is
    not always a sky with a type: `sky` is `dict | str | None` on the schema and this server also
    receives values that are neither. The three branches below are exhaustive over `object` -- a
    dict, a string, and everything else -- so every caller gets a label and none gets a `KeyError`
    or an `AttributeError` from a shape it did not anticipate.
    """
    if isinstance(sky_arg, dict):
        return f"sky.type={sky_arg.get('type')!r}"
    if isinstance(sky_arg, str):
        return f"sky={sky_arg!r}, a .rad path"
    return f"sky={sky_arg!r}"


def _project_north(loaded: LoadedProject) -> float | None:
    """The project's `north` scalar, as `run_point_in_time`, `run_render` and `run_glare` hand it to
    `_resolve_sky`; `None` when the project declares none.

    **A non-finite north is refused here (R694), the one check all three tools call.** A
    hand-edited manifest holding `NaN` or `Infinity` for north opens without complaint, and before
    this check `run_point_in_time` passed it to a spawned `xform -rz`, which exited 1 with "command
    line error at '-rz'" (measured for both values). The refusal is a code-less `SkyError`, which
    each tool's own handler turns into `refuse(reason)`; the wording
    mirrors `view_sunlight_tool._resolve_north`'s. Each tool calls this above its `dry_run` return
    on every call that generates a sky, so a dry run refuses what the real call would; a call that
    generates none (a lamp-only run, or `run_glare` given `image=`) never reads north.
    `rad_engine.sky.north_rotation_xform_argv` raises `ValueError` on the same value, as the
    engine's own backstop.
    """
    raw = loaded.manifest.project.scalars.get("north")
    if raw is None:
        return None
    north = float(raw)
    if not math.isfinite(north):
        raise SkyError(
            f"this project's north scalar is {raw!r}, which is not a finite number of degrees, so "
            "no sky can be turned into the model's frame. Set it again with a number.",
            code=None,
        )
    return north


def _resolve_sky(
    *,
    quantity: str,
    sky_arg: dict[str, Any] | str | None,
    ground_reflectance: ProvenanceValue,
    north: float | None,
    luminaires: bool = False,
    site: SkySite | None = None,
    epw_irradiance: tuple[float, float] | None = None,
) -> tuple[SkySpec | None, list[Advisory]]:
    """Everything docs/tool-surface.md §10 says about resolving `sky` into one `SkySpec`, with the
    Perez EPW route read upstream by `rad_mcp.sky_site.resolve_sky_inputs` and handed in as
    `epw_irradiance` (R765), and minus a `.rad` path (accepted on the wire, refused here -- see
    below).

    **`None` only for a lamp-only run** (Phase 11a spec §7, R458): `luminaires=True` and no `sky` --
    the sky is required unless the quantity is a daylight factor (handled first, below) or the
    luminaires are on. **Task 16**: this branch is reached by more than one `quantity` now.
    `run_point_in_time` only ever reaches it at `quantity="illuminance"` ("luminance" is not in its
    `Quantity` enum since R801, so the SDK refuses it before this function); `run_render`'s
    default `quantity="luminance"` reaches it too (R458 states the rule for `run_render` with no
    `quantity` qualifier at all), so the condition is not narrowed to one quantity. Every caller that
    passes no `luminaires` always gets a `SkySpec`.

    `site` is what `rad_mcp.sky_site.resolve_sky_inputs` resolved above `dry_run` (R762); a CIE or
    Perez sky reaching here without one raises `SkySiteRequired`, never a sky at gensky's own site.
    """
    advisories: list[Advisory] = []

    if quantity == "daylight_factor":
        # **Ruling R389: any supplied sky is refused here, for both callers.**
        #
        # The earlier shape built a `SkySpec` only for an overcast dict and let every other shape
        # fall through to `supplied = None`, which meant the forced reference sky silently replaced
        # it. That was measured to bite `rad_mcp.render_tool`, which imports this function and had
        # no guard of its own: `_resolve_sky(quantity="daylight_factor", sky_arg={"type": "clear"})`
        # returned `resolved_from="forced_overcast"` and raised nothing. Both callers now refuse
        # before reaching here -- `run_point_in_time`'s `_validate_arguments` at the
        # `quantity == "daylight_factor" and sky is not None` guard, `run_render`'s `_dispatch` at
        # its own guard before `dry_run` -- and this line is the engine-side backstop that makes
        # the refusal true of the *function* rather than of the two call sites that happen to check
        # today. `sky_arg is not None` is the whole test: no `isinstance`, no type comparison.
        if sky_arg is not None:
            raise DaylightFactorSkyRefused(_supplied_sky_label(sky_arg))
        spec, engine_advisory = resolve_daylight_factor_sky(
            None, month=None, day=None, hour=None,
            ground_reflectance=ground_reflectance, north=north,
        )
        if engine_advisory is not None:
            advisories.extend(to_wire_all([engine_advisory]))
        return spec, advisories

    if isinstance(sky_arg, str):
        raise SkyError(
            f"sky={sky_arg!r} names a .rad path directly. This is accepted on the wire "
            "(docs/tool-surface.md §10) and not built here: a caller-supplied .rad sky bypasses "
            "rad_engine.sky's argv construction entirely, and there is no argv or resolved_from to "
            "report for a file this server did not build. Pass a sky dict instead.",
            code=None,
        )
    if luminaires and sky_arg is None:
        return None, []
    if not isinstance(sky_arg, dict):
        raise SkyError(
            "sky is required for quantity='illuminance'/'luminance' -- pass a sky dict "
            "(docs/tool-surface.md §10), or use quantity='daylight_factor' to let the server force "
            "the reference overcast sky.",
            code=None,
        )

    if key_advisories := sky_key_advisories(sky_arg):
        raise UnknownKeysRefused(key_advisories)

    sky_type = sky_arg.get("type")
    month, day, hour = sky_arg.get("month"), sky_arg.get("day"), sky_arg.get("hour")

    if sky_type == "perez":
        resolved = resolve_perez_inputs(
            weather=sky_arg.get("weather"),
            irradiance=(tuple(sky_arg["irradiance"]) if sky_arg.get("irradiance") is not None else None),
            illuminance=(tuple(sky_arg["illuminance"]) if sky_arg.get("illuminance") is not None else None),
            interval_min=sky_arg.get("interval_min"),
            epw_irradiance=epw_irradiance,
        )
        perez_irradiance = resolved if resolved[0] is not None else None
        perez_illuminance = resolved[1] if resolved[0] is None else None
        if month is None or day is None or hour is None:
            raise SkyError(
                "a perez sky needs month, day and hour to place the sun "
                "(docs/tool-surface.md §10).",
                code=None,
            )
        if site is None:
            raise SkySiteRequired(
                f"sky type {sky_type!r} reached _resolve_sky with no site. Each sky tool resolves it "
                "above dry_run with rad_mcp.sky_site.resolve_sky_inputs (R762)."
            )
        spec = SkySpec(
            type="perez", month=month, day=day, hour=hour,
            ground_reflectance=ground_reflectance,
            site=site, north=north,
            perez_irradiance=perez_irradiance, perez_illuminance=perez_illuminance,
            interval_min=(
                sky_arg.get("interval_min") if sky_arg.get("interval_min") is not None
                else EPW_DEFAULT_INTERVAL_MIN if epw_irradiance is not None
                else None
            ),
            resolved_from="epw" if epw_irradiance is not None else "user",
        )
        return spec, advisories

    if sky_type not in _CIE_SKY_TYPE_NAMES:
        raise SkyError(
            f"sky.type={sky_type!r} is not one of {', '.join(_CIE_SKY_TYPE_NAMES)}, perez "
            "(docs/tool-surface.md §10).",
            code=None,
        )
    if month is None or day is None or hour is None:
        raise SkyError(
            f"sky.type={sky_type!r} needs month, day and hour (docs/tool-surface.md §10).",
            code=None,
        )
    if site is None:
        raise SkySiteRequired(
            f"sky type {sky_type!r} reached _resolve_sky with no site. Each sky tool resolves it "
            "above dry_run with rad_mcp.sky_site.resolve_sky_inputs (R762)."
        )
    spec = SkySpec(
        type=sky_type, month=month, day=day, hour=hour,
        ground_reflectance=ground_reflectance,
        site=site, north=north, resolved_from="user",
    )
    return spec, advisories


def _sky_view(spec: SkySpec) -> SkyView:
    result = sky_result_from_spec(spec, sky_generator_argv(spec))
    return SkyView(
        type=result.type, resolved_from=result.resolved_from,
        month=result.month, day=result.day, hour=result.hour,
        dni_wm2=result.dni_wm2, dhi_wm2=result.dhi_wm2,
        ground_reflectance=result.ground_reflectance, argv=result.argv,
        rotation_argv=result.rotation_argv,
    )


# --- running the pipeline, inline -----------------------------------------------------------------


def _statistics_view(values: list[float]) -> StatisticsView:
    mean = _statistics.fmean(values)
    return StatisticsView(
        min=min(values), max=max(values), mean=mean, median=_statistics.median(values),
        uniformity_u0=(min(values) / mean if mean != 0 else 0.0),
    )


def _quantity_values(rgb: list[tuple[float, float, float]], *, quantity: str) -> list[float]:
    """RGB **irradiance** (what `rtrace -I` reports) -> the requested quantity.

    Handles the two quantities `Quantity` holds, and nothing else. `luminance` never reaches here
    through the MCP server: R801 withdrew it from `Quantity`, so the SDK's argument validation
    refuses it (a luminance needs `rtrace` *without* `-I`; `run_render` gives one). Before a refusal
    existed, a luminance request fell through to `to_lux` and produced an illuminance in lux
    relabelled `cd/m2`; the raise below is the backstop against that.

    A quantity that is neither of the two is a bug upstream, not a caller error, so it raises
    rather than silently returning lux under another quantity's name.
    """
    lux = [to_lux(triple) for triple in rgb]
    if quantity == "daylight_factor":
        return [daylight_factor(value) for value in lux]
    if quantity != "illuminance":
        raise ValueError(
            f"_quantity_values cannot convert to {quantity!r}: this function converts the RGB "
            "irradiance `rtrace -I` reports, which supports 'illuminance' and 'daylight_factor' "
            "only. Reaching here with anything else means a quantity outside the tool's enum got "
            "through to a pipeline that cannot compute it."
        )
    return lux


class _PipelineResult:
    """Everything `_run_pipeline` produced, named rather than a bare tuple -- eight positional
    values is exactly the shape that invites a caller to swap two of them."""

    def __init__(
        self,
        *,
        values: list[float],
        stats: StatisticsView,
        sky_view: SkyView | None,
        parameters: QualityParameters,
        octree_cube: tuple[float, float, float, float],
        advisories: list[Advisory],
        octree_path: Path,
        sky_text: str,
        sky_path: Path | None = None,
        processes: int | None = None,
    ) -> None:
        self.values = values
        self.stats = stats
        self.sky_view = sky_view
        self.parameters = parameters
        self.octree_cube = octree_cube
        self.advisories = advisories
        self.octree_path = octree_path
        """Where `_run_pipeline` compiled this call's octree (R348) -- `_dispatch` registers it into
        the manifest after this returns, matching `annual_tool._register_octree`'s shape."""
        self.sky_text = sky_text
        """The exact sky primitive text `oconv` compiled into `octree_path` (`sky_description_text`
        output: the sky glow and R36's ground glow, white since R761). Its hash is the octree's
        `sky_digest`; the file it was written to is `sky_path`."""
        self.sky_path = sky_path
        """Where `_run_pipeline` wrote `sky_text` -- `04_skies/point_in_time.rad`, registered as
        `sky:point_in_time` (R730). `None` on a run with no sky (a lamp-only run, and `run_electric`,
        which never registers it)."""
        self.processes = processes
        """The `-n` `rtrace` was given (R780); `None` from a stand-in that ran nothing."""


def _scan_scene_for_inline_commands(scene_files: list[str], *, cwd: Path) -> None:
    """Run `rad_engine.octree`'s own `!`-line scan over `scene_files`, **above** the `dry_run`
    branch, raising `SceneInlineCommandError` exactly as `build_octree` would.

    **Why this exists at all, and why it is not a second scanner.** `build_octree` scans the scene
    immediately before spawning `oconv` -- the right place for it, because that is the spawn whose
    bytes it must match (ruling R33, commit 9a93e51). But `build_octree` is reached only from
    `_run_pipeline`, which is *below* `_dispatch`'s `dry_run` branch, so the refusal was unreachable
    by construction under `dry_run`. Measured at the wire on a scene registered clean and made
    hostile afterwards -- the TOCTOU window `build_octree`'s own docstring describes::

        [real run on hostile scene] is_error=True  codes=['SCENE_INLINE_COMMAND']
        [dry_run on hostile scene ] is_error=False codes=[]  refused=None

    A caller was told the run would succeed, and the real call then refused it. Commit `66f74f9`
    fixed one instance of this class by hand; this closes the structural cause for the scene scan.

    **It calls the engine's own scan rather than reimplementing it**:
    `rad_engine.refuse_inline_commands_in_files`, public since R747, is the loop `build_octree` runs,
    with its own fresh `seen` per call. One scanner, ruling R30's *"a second copy is a second thing to drift"*.

    Scanning twice per real call is accepted: it is a small read against the compile that follows,
    and `build_octree`'s scan is **not** removed. That one still runs against the bytes `oconv`
    actually reads, which is the guarantee R33 requires and which this earlier, advisory scan
    cannot provide -- the file may change between the two. This scan exists to make `dry_run` tell
    the truth, not to replace the security boundary.

    **The same RAYPATH as the real call** (R753): `rad_engine.env.locate` finds the install with
    the search `discover` uses and spawns nothing, so this scan searches the directories
    `build_octree`'s scan will, the Radiance library directory included, and `dry_run` refuses a
    hostile include that exists only there, as the real call does.

    **Inside the roots** (R823): this scan, unlike `build_octree`'s, knows the configured roots, so
    it also refuses `PATH_OUTSIDE_ROOT` a scene file or include resolving outside every root and the
    Radiance library directory (`refuse_inline_commands_in_files`' ``allowed_dirs``).
    """
    # R753 (owner decision of 2026-10-02): the RAYPATH the real call's spawns will get, read without
    # spawning. With no install the real call is refused `RADIANCE_NOT_FOUND` and nothing reads the
    # scene, so the working directory alone is searched.
    # R823 (owner decision of 2026-10-05, register N-41): a scene file or include resolving outside
    # every configured root is refused `PATH_OUTSIDE_ROOT`. The Radiance library directory is
    # allowed too, because the spawn's RAYPATH searches it and a scene may `!xform` from it.
    lib_dir: Path | None = None
    try:
        env = locate()
        raypath = env.raypath_dirs(cwd)
        lib_dir = env.lib_dir
    except RadianceNotFound:
        raypath = (Path(cwd).resolve(),)
    allowed_dirs = (*current().roots, *((lib_dir,) if lib_dir is not None else ()))
    refuse_inline_commands_in_files(
        scene_files, cwd=cwd, raypath=raypath, allowed_dirs=allowed_dirs
    )


class _ValidatedScene:
    """The project state `run_point_in_time` needs, proven readable **before** anything is spawned.

    Produced by `_validate_scene` and consumed by `_run_pipeline`, so the checks that decide whether
    a call *can* run and the code that then runs it read the same values rather than each resolving
    the manifest for themselves. See `_validate_scene` for why that matters to check 3.9.
    """

    def __init__(
        self,
        *,
        points: list[GridPoint],
        points_text: str,
        scene_files: list[str],
        polygon: list[list[float]] | None,
        grid_record_id: str | None = None,
        recorded_height: object = None,
    ) -> None:
        self.points = points
        self.points_text = points_text
        self.scene_files = scene_files
        self.polygon = polygon
        self.recorded_height = recorded_height
        """The `height` the grid's record carries (R784), or `None` for a grid created before
        R784 or assembled by another route; `recorded_floor_mismatch` reads it."""
        self.grid_record_id = grid_record_id
        """The id of the sensor-grid record `_grid_points_file` matched -- the edge
        `point_in_time:<grid>` names (R730). `None` where the scene was assembled by another route
        (`run_electric`'s zoned grid), which registers no point-in-time result."""


def _validate_scene(loaded: LoadedProject, *, grid: str, cwd: Path, luminaires: bool) -> _ValidatedScene:
    """Every project-state refusal `run_point_in_time` can produce, in one function, spawning
    nothing.

    This is the second half of what makes check 3.9 structural rather than coincidental.
    `_validate_arguments` covers refusals readable from the arguments alone; this covers the ones
    that need the manifest -- no geometry registered, no such grid, a grid whose `.pts` file is
    empty. Both run on the single path in `_dispatch` *before* the `dry_run` branch, so `dry_run`
    cannot reach a decision a real call would not have reached identically: there is no second copy
    of these checks for it to drift from.

    Ordering is deliberate and is a contract, not an accident. **Geometry is checked before the
    grid**, because "this project has no scene at all" is the more fundamental fact: a caller who
    has registered neither wants to be told to import geometry first, not sent to create a sensor
    grid for a scene that does not exist. `_scene_files` therefore runs first even though the grid
    lookup is cheaper.

    **The `!`-line scan runs here, and that placement is the whole of check 3.9's structural fix.**
    It is the one refusal in this tool that needed file *contents* rather than the manifest, which
    is why it originally lived in `build_octree` -- below the `dry_run` branch, and therefore
    unreachable under `dry_run` by construction. Hoisting it into this function, which both paths
    already call, means a `dry_run` cannot report success for a scene the real call refuses. It
    runs **last**, after the grid checks, so the cheaper manifest refusals still win the race to be
    reported and the ordering contract above is unchanged. See `_scan_scene_for_inline_commands`.
    """
    scene_files = _scene_files(loaded, luminaires=luminaires)
    points_path, polygon, recorded_height, recorded_digest = _grid_points_file(loaded, grid)
    try:
        points, points_text = _read_points(points_path, recorded_digest, grid=grid)
    except UnicodeDecodeError:
        # Final review, P1 (pre-existing): `_read_points` decodes strictly after the digest check,
        # so a digest-matching `.pts` that is not UTF-8 raised a bare `ValueError` no handler of
        # this tool catches -- an uncoded `ToolError`. It is a `.pts` that cannot be read, so it
        # takes that refusal's class, as `run_annual`'s `_validate_project` does.
        raise GridUnreadableError(
            f"{points_path} cannot be read as a sensor grid: it is not UTF-8 text. Re-create it "
            "with sensor_grid(action='create')."
        ) from None
    _scan_scene_for_inline_commands(scene_files, cwd=cwd)
    # The record `_grid_points_file` just matched, by the same candidates in the same manifest
    # order, so the result's `built_from` names the grid that was read (R730). It cannot miss:
    # `_grid_points_file` raised above if nothing matched.
    candidates = _grid_candidates(grid)
    grid_record_id = next(
        record.id for record in loaded.manifest.inputs
        if record.kind == "sensor_grid" and record.id in candidates
    )
    return _ValidatedScene(
        points=points, points_text=points_text, scene_files=scene_files, polygon=polygon,
        grid_record_id=grid_record_id,
        recorded_height=recorded_height,
    )


def _scene_advisories(scene: _ValidatedScene, *, quantity: str, lit: bool = False) -> list[Advisory]:
    """Every advisory that is knowable from the validated scene and the arguments alone -- i.e.
    **without** running Radiance.

    Exists so `dry_run` and the real call cannot disagree about them. Both call this one function;
    neither builds this list itself. `DEEP_POINT_NOISE` needs only the sensor points and the
    recorded room polygon, and `RESULT_IS_INITIAL_ILLUMINANCE` needs only `quantity` -- so a
    `dry_run` that omitted them was not "not yet known", it was silently dropped.

    The advisories that stay behind in `_run_pipeline` are exactly the ones that genuinely are not
    knowable here: `DIRECT_EXACT_FORCED` and `AMBIENT_BOUNCES_LOW` are decided against the
    *post-`direct_exact_forced`* parameters, and anything classified out of Radiance's own stderr
    obviously requires having run it. A `dry_run` cannot honestly claim those, and does not.
    """
    advisories: list[Advisory] = []
    deep = _deep_point_noise_advisory(scene.points, scene.polygon)
    if deep is not None:
        advisories.append(deep)
    # R493: on a lit run `rad_mcp.lit_scene.lit_advisories` decides `RESULT_IS_INITIAL_ILLUMINANCE`
    # (only when no placed type is dimmed), so this unconditional one would contradict it.
    initial = None if lit else _initial_illuminance_advisory(quantity)
    if initial is not None:
        advisories.append(initial)
    # R818: a mirror or prism in the files this run compiles -- read from the files, so knowable
    # here and reported by `dry_run` and the real call alike.
    redirecting = light_redirecting_advisory(scene.scene_files, tool="run_point_in_time")
    if redirecting is not None:
        advisories.append(redirecting)
    return advisories


def _run_pipeline(
    env: RadianceEnv,
    *,
    scene: _ValidatedScene,
    quantity: str,
    sky_spec: SkySpec | None,
    parameters: QualityParameters,
    cwd: Path,
    luminaires: bool,
    octree_path: Path | None = None,
    scene_advisories: bool = True,
    sky_path: Path | None = None,
) -> _PipelineResult:
    """Sky -> octree -> rtrace -> photometric conversion, run synchronously.

    Takes the already-validated scene rather than re-reading the manifest: `_validate_scene` has
    proven the geometry, the grid and its points are all readable, and re-resolving them here would
    be a second copy of exactly the checks check 3.9 requires be single.

    The only place this composition is written -- see the module docstring for why there is no
    second, job-worker version of it this phase.

    `octree_path` defaults to `06_octrees/point_in_time.oct` and `sky_path` to
    `04_skies/point_in_time.rad` (R730: the layout's own folders for the two kinds they are
    registered as, where they were `_point_in_time.oct` and `_point_in_time_sky.rad` at the root);
    `run_electric` passes its own R611 temporary octree instead (design §6 step 6) and has no sky,
    so it writes neither. Each default's folder is created when missing: a project created before
    its layout had the folder still runs. `scene_advisories`
    defaults to `True`, unchanged for `run_point_in_time`; `run_electric` passes `False` because
    `DEEP_POINT_NOISE` does not apply to its zoned grid and its own `lit_advisories` call already
    decides `RESULT_IS_INITIAL_ILLUMINANCE` (design §6 step 6, R607).
    """
    advisories: list[Advisory] = []
    # Only the points *text* is needed here now: the advisories that used the parsed points moved
    # to `_scene_advisories`, which both this path and `dry_run` call.
    points_text = scene.points_text

    if sky_spec is None:
        # A lamp-only run (Phase 11a spec §7): no sky file and no sky advisories, and the octree
        # record hashes an empty sky text.
        sky_text = ""
        scene_files = list(scene.scene_files)
        sky_has_sun = False
    else:
        # Ruling R694: `generate_sky` spawns the generator (`gendaylit` for perez, `gensky`
        # otherwise) and, when the project declares a non-zero north, pipes its stdout through a
        # second spawned `xform -rz` -- neither generator's own `-r` rotates on this Radiance 6.1a.
        # Ruling R761: the glow pair is constant (a white ground glow); the project's reflectance
        # reaches the sky through the generator's own `-g`.
        # Final review FW-1: the generator's own stderr reaches the payload, `field: "sky"`.
        generator_advisories: list[EngineAdvisory] = []
        sky_text = sky_description_text(
            generate_sky(env, sky_spec, cwd=cwd, timeout=_METADATA_TIMEOUT_S,
                         advisories=generator_advisories),
        )
        advisories.extend(to_wire(a, field="sky") for a in generator_advisories)
        if sky_path is None:
            sky_path = ProjectPaths(cwd).artifact_path("sky", "point_in_time.rad")
        sky_path.parent.mkdir(parents=True, exist_ok=True)
        # **Not** `write_text`. Adopted 2026-08-10 under R48's rule that every open in `src/` goes
        # through the primitive, in either direction.
        #
        # Lower severity than the sites ranked above it, and recorded as such rather than overstated:
        # `cwd` is `paths.root`, already contained by `Runtime.locate`, and the filename is a fixed
        # literal, so a caller cannot *aim* this write the way `<grid>.pts` or `annual_<grid>.oct` can
        # be aimed. What is left is the wait-for-it vector -- anything with write access to the project
        # directory plants a symlink or a FIFO at this one known name, and `write_text` would follow
        # the first and hang forever on the second, in an `anyio` worker whose pool is 40 wide.
        with open_regular_file(sky_path, "wb") as handle:
            handle.write(sky_text.encode("utf-8"))
        scene_files = scene.scene_files + [str(sky_path)]
        sky_has_sun = sky_spec.type == "perez" or sky_spec.type in CIE_TYPES_WITH_SUN

    if octree_path is None:
        octree_path = ProjectPaths(cwd).artifact_path("octree", "point_in_time.oct")
        octree_path.parent.mkdir(parents=True, exist_ok=True)
    octree = build_octree(
        env, scene_files, out_path=octree_path, cwd=cwd, timeout=_METADATA_TIMEOUT_S
    )

    forced_parameters, direct_advisory = direct_exact_forced(
        parameters, sky_has_sun=sky_has_sun, quantity=quantity, luminaires=luminaires
    )
    if direct_advisory is not None:
        advisories.extend(to_wire_all([direct_advisory]))
    bounces_advisory = ambient_bounces_low_advisory(forced_parameters)
    if bounces_advisory is not None:
        advisories.extend(to_wire_all([bounces_advisory]))

    processes = worker_cores()
    request = RtraceRequest(octree=octree.path, parameters=forced_parameters, processes=processes)
    rgb, run_result = run_rtrace(
        env, request, points_text=points_text, cwd=cwd, timeout=_RTRACE_TIMEOUT_S
    )
    # R488: a lit run classifies rtrace's stderr, so a lamp that warns reaches the result. R820: an
    # unlit run does too -- a warning that explains a wrong daylight number reaches the result. Measured
    # first: the office fixture's daylight runs (clear and overcast illuminance, the daylight factor,
    # `standard` and `draft`) write no stderr line, so no routine message class is dropped.
    advisories.extend(to_wire_all(classify(list(run_result.warnings))))

    values = _quantity_values(rgb, quantity=quantity)
    stats = _statistics_view(values)

    # The same list `dry_run` reports, built by the same function -- see `_scene_advisories`. Guarded
    # so `run_electric` can skip it (design §6 step 6): its zoned grid has no `DEEP_POINT_NOISE`
    # reading, and `RESULT_IS_INITIAL_ILLUMINANCE` is its own `lit_advisories` call's decision (R607).
    if scene_advisories:
        advisories.extend(_scene_advisories(scene, quantity=quantity, lit=luminaires))

    return _PipelineResult(
        values=values, stats=stats, sky_view=_sky_view(sky_spec) if sky_spec is not None else None,
        parameters=forced_parameters,
        octree_cube=octree.bbox, advisories=advisories,
        octree_path=octree_path, sky_text=sky_text,
        sky_path=sky_path if sky_spec is not None else None,
        processes=processes,
    )


#: Stable id this tool's own octree is registered under -- overwritten on every real call, the same
#: "report of the last run, not an accumulating history" posture `_write_results_file` already states
#: for the values file (ruling R348). One id per *project*, not per grid: the octree's own geometry
#: does not vary by `grid` (only the sensor points traced against it do), so keying by grid would add
#: a distinction the octree itself does not have.
_POINT_IN_TIME_OCTREE_ID = "octree:point_in_time"

#: R730: the id this tool's sky is registered under -- one per project, overwritten on every call
#: that has a sky, for `_POINT_IN_TIME_OCTREE_ID`'s reason: the octree it is compiled into is one per
#: project too.
SKY_ARTIFACT_ID = "sky:point_in_time"


def point_in_time_result_id(grid: str) -> str:
    """R730: the id `run_point_in_time`'s result for `grid` is registered under, and the
    `artifact_id` its payload carries for `export(artifact_id=...)`. One per grid, like the
    `08_results/point_in_time_<grid>.txt` file it names."""
    return f"point_in_time:{grid}"


def _register_point_in_time_artifacts(
    runtime: Runtime,
    paths: ProjectPaths,
    *,
    pipeline: _PipelineResult,
    sky_spec: SkySpec | None,
    grid: str,
    grid_record_id: str,
    quantity: str,
    luminaires: bool,
) -> None:
    """Seal this call's sky, octree and result into the manifest, in that order, under one lock
    (R730): each names the one before it, so each must exist when the next is registered.

    - `sky:point_in_time` (only when the run has a sky): `built_from` the project ground
      reflectance, plus north where the project declares it (`REQUIRED_EDGES["sky"]`, `smx`'s
      shape); `parameters` the generator argv, the north `xform` argv (`[]` when there is none) and
      the ground reflectance -- what regenerates the file.
    - `octree:point_in_time`: as below, then the sky's id when the run has one. "Has one" is
      *this call's* sky, never the manifest's: a lamp-only run after a sky run re-registers the
      octree without the sky edge, so the script `export(artifact_id=...)` renders for it has no
      sky step -- and the `sky:point_in_time` record the earlier run sealed **stays in the
      manifest**, named by nothing, until a later sky run overwrites it
      (`tests/test_export.py::TestTheScriptRunsFromACopy::
      test_a_lamp_only_run_after_a_sky_run_names_no_sky_and_exports_no_sky_step`).
    - `point_in_time:<grid>`: `built_from` the octree and the grid record `_validate_scene`
      matched; `parameters` the quantity and the flags `rtrace` was given -- `_run_pipeline`'s
      parameters *after* `direct_exact_forced`, with `-dt`/`-dc` only when they were set.

    The octree registration (ruling R348) is what first gave `export` a real artifact to narrate
    for a `run_point_in_time` run. Before this, `_run_pipeline` built an octree
    (`build_octree`) and this module called `register_artifact` nowhere -- measured independently
    three times; `_write_results_file`'s docstring already said as much about the *results* file, and
    the octree carried no registration either.

    Matches `rad_mcp.annual_tool._register_octree`'s shape exactly: both manifest locks in the one
    legal order (`Runtime.modifying`), a read-modify-write under them, `register_artifact` under
    them.

    **`built_from` names materials inputs as well as geometry (ruling R356), and `REQUIRED_EDGES`
    permits the superset.** `_scene_files` above compiles every registered `kind="materials"` input
    into the octree exactly as it compiles geometry (`scene_files_with_materials`) -- so a materials
    edit (repainting the interior) changes what `build_octree` compiles just as a geometry edit
    does. Naming geometry alone in `built_from` left a materials-only edit invisible to
    `rad_core.manifest.evaluate`: the artifact would report `valid=True` forever against a scene
    that no longer matches it, the exact silent stale-reads-fresh failure the sky-digest fix above
    exists to prevent, and materials are a real registered edge (not a scratch file like the sky),
    so there is no reason to leave them out. `REQUIRED_EDGES["octree"]` (`rad_engine/project.py`)
    requires only `input_kinds={"geometry"}` -- a **floor**, by that table's own docstring
    (the `#:` comment above `REQUIRED_EDGES`), not a ceiling -- and `REQUIRED_EDGES["vmx"]` already names
    materials for a different kind, so citing more than the floor is an established, permitted
    shape, not a new convention.

    **Materials precede geometry, matching `scene_files_with_materials`'s own ordering guarantee.**
    `oconv` requires a modifier to be defined before the geometry that references it
    (`rad_engine.materials.scene_files_with_materials`'s own *function* docstring, verbatim: *"all
    materials precede all geometry"* -- the module docstring's
    ordering-guarantee section states the identical fact in different words
    and points a reader at this function's own docstring for the guarantee itself) -- the real
    `_scene_files`/`build_octree` call this artifact narrates already gets that order for free from
    that function. This registration's own `built_from` list is built
    in the same order for the same reason, even though `built_from`'s *order* has no effect on
    `evaluate`/`compute_digest` (edges are hashed as a sorted `(id, digest)` list --
    `rad_core.manifest.compute_digest`) -- the only consumer order could matter to is
    `rad_engine.export._octree_command` (`_octree_step` since R731, 2026-09-27), which walks
    `built_from` positionally to build `oconv`'s argument list. That module is Task 12's own and
    this task does not change it (ruling R356: "do
    not fix export.py"); see Task 14's report for the measured order this call produces and whether
    it is correct for that reconstruction.

    **Why the sky text is hashed into `parameters` rather than left out.** This tool's octree is not
    like `run_annual`'s: the generated sky (`sky_description_text`, with its sky and ground glows)
    is compiled *into* the octree itself (`_run_pipeline` appends `sky_path` to `scene_files` before
    calling `build_octree`), so two calls with identical geometry but different skies produce
    genuinely different octree files. Until R730 the sky was a scratch file regenerated fresh every
    call and registered nowhere, so it could not be named in `built_from`, which may only name ids
    the manifest already has (R730 changed that; see the next paragraph). Left out of `parameters`
    entirely, two such octrees would compute the *identical*
    digest (`compute_digest` is a pure function of kind + edges + parameters + radiance_version, and
    none of those would differ between the two calls), so `rad_core.manifest.evaluate` would report
    the second registration as indistinguishable from the first -- a stale-vs-fresh signal that is
    wrong and silent, exactly the failure mode this ruling warns is the expensive one here. Folding
    `sha256(sky_text)` into `parameters` (excluded from nothing -- `DIGEST_EXCLUDED_PARAMETERS` only
    drops `n`) makes a sky change move the digest, which
    `tests/test_point_in_time_tool.py::TestPointInTimeOctreeRegistration` proves directly: two
    registrations over the same geometry but different sky text are asserted to disagree on both
    `digest` and `evaluate()`'s verdict for the first once the second has overwritten it.

    **The sky is now a registered artifact whose parameters regenerate it (R730).** Until R730 the
    sky was a scratch file at the project root, so `built_from` could not name it and `parameters`
    held only its hash: an export could detect that the sky had changed and could not rebuild it.
    `sky:point_in_time` records the generator argv, the north `xform` argv and the ground
    reflectance (and, since R761, the ground glow's colour, `ground_glow`), and this octree names
    it last in `built_from`, so a changed sky reaches the octree through an edge as well as through
    `sky_digest` -- which stays, because it hashes the text `oconv` actually compiled (the sky and
    ground glows included, the ground glow white since R761) rather than the argv that produced it.
    """
    sky_digest = "sha256:" + hashlib.sha256(pipeline.sky_text.encode("utf-8")).hexdigest()
    with runtime.modifying(paths):
        project = open_project(paths.root, radiance_version=runtime.launcher.version)
        has_sky = sky_spec is not None and pipeline.sky_path is not None
        if has_sky:
            assert sky_spec is not None and pipeline.sky_view is not None and pipeline.sky_path is not None
            sky_edges = [GROUND_REFLECTANCE_INPUT_ID]
            # North is an edge where the project declares it (`REQUIRED_EDGES["sky"]`'s
            # `ids_if_present`), read off `input_digests()`, which is where the synthetic `project:`
            # ids live. `manifest.inputs` never holds one (`Manifest.input_digests` refuses a record
            # in the reserved namespace), so a test of `NORTH_INPUT_ID` against the input records'
            # ids would never name north, and `check_edges` would refuse the sky for the missing
            # edge (measured by mutation, Phase 14d Task 2). `rad_engine.chain._smx_edges` used that
            # different test -- the input records' ids -- for `smx` until R734 (2026-09-27) moved it
            # to `input_digests()` too.
            if NORTH_INPUT_ID in project.manifest.input_digests():
                sky_edges.append(NORTH_INPUT_ID)
            register_artifact(
                project,
                artifact_id=SKY_ARTIFACT_ID,
                kind="sky",
                path=pipeline.sky_path,
                built_from=sky_edges,
                parameters={
                    "generator_argv": list(pipeline.sky_view.argv),
                    "rotation_argv": list(pipeline.sky_view.rotation_argv or []),
                    "ground_reflectance": float(sky_spec.ground_reflectance.value),
                    "ground_glow": GROUND_GLOW_RGB,
                },
                radiance_version=runtime.launcher.version,
                save=False,
            )
        register_artifact(
            project,
            artifact_id=_POINT_IN_TIME_OCTREE_ID,
            kind="octree",
            path=pipeline.octree_path,
            built_from=[
                *(record.id for record in project.manifest.inputs if record.kind == "materials"),
                *(record.id for record in project.manifest.inputs if record.kind == "geometry"),
                # R480: a lit octree names the support record, every placed type and every
                # placement, read here from the manifest re-opened inside the lock. An unlit run
                # re-registers this id without them, which is `luminaire(action="remove")`'s recovery.
                *(luminaire_edge_ids(project.manifest) if luminaires else []),
                *([SKY_ARTIFACT_ID] if has_sky else []),
            ],
            parameters={"freeze": True, "sky_digest": sky_digest},
            radiance_version=runtime.launcher.version,
            save=False,
        )
        flags = {
            key: value
            for key, value in parameters_dict(pipeline.parameters).items()
            if key in ("ab", "ad", "as", "ar", "aa", "dt", "dc")
            and not (key in ("dt", "dc") and value is None)
        }
        register_artifact(
            project,
            artifact_id=point_in_time_result_id(grid),
            kind="point_in_time",
            path=paths.results / f"point_in_time_{grid}.txt",
            built_from=[_POINT_IN_TIME_OCTREE_ID, grid_record_id],
            parameters={
                "quantity": quantity, **flags,
                **({"n": pipeline.processes} if pipeline.processes is not None else {}),
            },
            radiance_version=runtime.launcher.version,
        )


# --- committing the results file and the resource_link --------------------------------------------


def _write_results_file(
    paths: ProjectPaths, *, grid: str, values: list[float], roots: Sequence[Path]
) -> Path:
    """Commit the values as one number per line, in grid order, into `08_results/`.

    Not an `ill` artifact: that kind belongs to `run_annual`'s multi-column matrix output (Phase
    4+), and this is a single-sky, single-column report. `_dispatch` registers it, once written, as
    `point_in_time:<grid>` (R730, a kind of its own -- `rad_core.manifest.ArtifactKind` says why),
    so `export(artifact_id=...)` can reproduce the run; nothing reuses it. It is a real committed
    file with a stable path, which is what the resource_link points at.
    Overwrites on every call by design: this is a report of the *last* run against this grid, not an
    accumulating history, and giving each call its own filename would leave `08_results/` growing
    without bound for a tool meant to be called repeatedly while tuning a scene.

    **The write goes through `paths.contain`, like every read path in this server and unlike this
    one before.** `grid` is a caller-supplied string that becomes a filename here, and containment
    was the one control that was never applied to it. It failed closed *today* only by accident --
    a missing intermediate directory, plus the `point_in_time_` prefix absorbing one leading path
    segment -- and neither is a control. `sensor_grid(action="create")` now constrains the grid
    name charset as well (`server.py`), so this is the second of two independent guards rather than
    the only one; a name that reached a project by some other route still cannot escape here.

    `contain` raises `PathOutsideRoot`/`RootsNotConfigured`, both already in
    `run_point_in_time`'s handler chain and both already coded (`PATH_OUTSIDE_ROOT`) through
    `refuse_engine_error` -- so the failure is a coded refusal rather than the previous uncoded
    `ToolError`, which additionally leaked the server's absolute root into the error text.
    """
    # N-112 (R851): the path as named, so a symlink at the final component is refused below rather
    # than followed (`contain` returns the resolved path, which the open would never see as a link).
    out_path = contain_as_named(paths.results / f"point_in_time_{grid}.txt", roots)
    # N-113 (R851): a symbolic link at any component below the project root -- `08_results` aimed at
    # another directory -- is refused before the `mkdir`, which would traverse it, and before any
    # write; the project root itself may be reached through a link.
    refuse_linked_components(out_path, below=paths.root)
    # `mkdir` after containment, never before: creating the directory first would let a hostile
    # `grid` bring a directory tree into existence outside the roots before anything refused it.
    out_path.parent.mkdir(parents=True, exist_ok=True)
    # **Not** `write_text`, which follows a symlink at the final component and blocks forever on a
    # FIFO. Adopted 2026-08-10 under ruling R48's rule -- *every filesystem open, in either
    # direction, goes through this function*. Containment above is a different guard and does not
    # cover this: `06_results` may legitimately be inside the root while `point_in_time_<grid>.txt`
    # is a symlink aimed out of it, and `grid` is a caller-supplied argument, so that final
    # component is targetable rather than merely waitable-for.
    # *(Corrected 2026-10-05, N-112, R851: this said the final component was guarded; it was not,
    # because `contain` had already resolved it and the no-follow open was handed the link's target.)*
    # A symlink, FIFO or directory at the name is refused `PROJECT_PATH_NOT_A_REGULAR_FILE` by the
    # `lstat` check. N-114 (R851): the values are staged beside the name and renamed over it
    # (`replace_file_atomically`, R526's pattern), so a hardlink there is replaced, never written
    # through -- a `"wb"` open rewrote a hardlinked file outside every root -- and a link planted
    # after the check is replaced too.
    #
    # L-112 (R830): a write the disk refuses (a permission withdrawn, a full volume, a read-only
    # results file) is refused, coded, rather than escaping as an uncoded `OSError`; something not a
    # regular file at the name keeps its own code. `PROJECT_WRITE_FAILED` since R855 (it was
    # `PATH_OUTSIDE_ROOT`, the containment code), naming a read-only file as the cause.
    refuse_existing_non_regular_destination(out_path)
    data = ("\n".join(repr(v) for v in values) + "\n").encode("utf-8")
    try:
        replace_file_atomically(out_path, lambda handle: handle.write(data))
    except NotARegularFileError:
        raise
    except OSError as exc:
        raise write_failure(out_path, exc, what="the results file", outcome=(
            "The trace ran, but no result was registered; a results file already there is unchanged.")) from None
    # Resolved, as `contain` returned it before N-112: the file just written is a regular file at
    # the named path, so this is the spelling the result and the manifest have always carried.
    return out_path.resolve()


def _emit_with_resource_link(
    result: PointInTimeResult, *, path: Path, project: str, grid: str
) -> CallToolResult:
    """Wrap `result` the way `rad_mcp.envelope.emit` does, plus a `resource_link` content block for
    the committed results file -- docs/tool-surface.md#artifacts-are-resource-links-as-well-as-paths.

    Built directly rather than through `emit`, which has no parameter for an extra content block;
    both produce the identical `CallToolResult` shape otherwise (JSON mirrored into a `TextContent`
    block, `structuredContent` set, `is_error=False`).

    **The absolute path is carried by `PointInTimeResult.path`, and `resource_link` travels
    alongside it** -- the spec's "the paths stay" instruction, because a link is *"not what an
    exported script needs and not what a user pastes into `rvu`"*. This docstring previously claimed
    the path "stays inside `structuredContent` exactly as it already does on every other field this
    result carries", which was false: the result had no path field at all and the path existed only
    as this block's `file://` URI. The field now exists; `path` and `uri` are built from the same
    `path` argument, so they cannot name different files.
    """
    payload = result.payload()
    return CallToolResult(
        content=[
            TextContent(type="text", text=json.dumps(payload, indent=2)),
            ResourceLink(
                uri=path.as_uri(),
                name=f"{project}/{grid} point-in-time results",
                description=(
                    f"One {result.unit} value per line, in the {grid!r} sensor grid's own point "
                    "order -- the same order as `values` in this result."
                ),
                mimeType=RESULT_MIME_TYPE,
            ),
        ],
        structured_content=payload,
        is_error=False,
    )


# --- the tool -------------------------------------------------------------------------------------


def run_point_in_time(
    project: Annotated[
        str | None,
        Field(
            description=(
                "the project handle. Must have at least one registered `geometry` input and the "
                "named sensor grid already created (`sensor_grid(action=\"create\")`)."
            )
        ),
    ] = None,
    grid: Annotated[
        str | None, Field(description="the sensor grid's handle inside the project.")
    ] = None,
    quantity: Annotated[
        Quantity | None,
        Field(
            description=(
                "\"illuminance\" (lux) or \"daylight_factor\" (%), which **always normalises "
                "against 10 000 lux** (`rad_core.photometry.daylight_factor`'s `reference_lux` "
                "default, never overridden here). Because that divisor is fixed, "
                "**\"daylight_factor\" is available only with no `sky` of your own**: the CIE "
                "reference overcast sky is forced for you and `DF_SKY_FORCED` (info) says so. "
                "Passing any `sky` alongside it is refused (ARGUMENT_OUT_OF_RANGE, field=\"sky\"). "
                "To render a sky of your own, ask for quantity=\"illuminance\" and read lux. "
                "For a luminance in cd/m2, use `run_render`: `rtrace -I` gives irradiance."
            )
        ),
    ] = None,
    sky: Annotated[
        dict[str, Any] | str | None,
        Field(
            description=(
                "a sky dict (`{type, month, day, hour, ...}`) or a `.rad` path. Required for "
                "illuminance (unless luminaires=true); **refused for daylight_factor** -- omit it "
                "there and the forced reference overcast sky is substituted for you, reported by "
                "`DF_SKY_FORCED`. A `.rad` path is "
                "accepted on the wire and never rendered by this tool: it is refused outright. "
                "The old "
                "`longitude`/`meridian` keys are refused: they were west-positive (R762). Give "
                "`latitude`, `longitude_east` and `meridian_east` (degrees, east-positive) "
                "instead, all three or none."
            )
        ),
    ] = None,
    quality: Annotated[
        str | dict[str, QualityValue],
        Field(
            description=(
                "a preset name (draft/standard/high/reference) or a dict overriding individual "
                "ambient flags. See `rad_engine.rtrace.QUALITY_PRESETS` for the exact values. An "
                "override is bounded on both sides: every value must be finite, the integer flags "
                "are capped at 16x the 'high' preset (-ab 112, -ad 65536, -as 32768, -ar 16384) "
                "and -aa lies in [0, 1]. Outside those, ARGUMENT_OUT_OF_RANGE naming the flag."
            )
        ),
    ] = "standard",
    window_group_states: Annotated[
        dict[str, str] | None,
        Field(
            description=(
                "BSDF binding per aperture state. **This tool cannot honour one** -- it traces "
                "rtrace over an octree, while a state bound by window_group(action='assign_bsdf') "
                "enters the T matrix of the three-phase multiply, which only run_annual builds. "
                "Naming any state here is refused rather than ignored. (The `window_group` tool "
                "itself exists and works; the gap is this tool's.)"
            )
        ),
    ] = None,
    luminaires: Annotated[
        bool,
        Field(
            description=(
                "light the scene with every placement (`luminaire` tool); `sky` becomes optional "
                "for illuminance, and a lamp-only run returns `sky: null`. Off by default (R456); "
                "a project with placements then reports `LUMINAIRES_EXCLUDED`. Refused with "
                "daylight_factor (DAYLIGHT_FACTOR_WITH_LUMINAIRES). A lit run first checks, also "
                "under dry_run, that a luminaire is placed, the instance bound, the project's root "
                "source.cal and every placed file, and passes -dt 0 -dc 1."
            )
        ),
    ] = False,
    dry_run: Annotated[
        bool,
        Field(
            description=(
                "validate every argument and refuse exactly as a real call would, but spawn "
                "nothing -- not even a job. Used to check a call will succeed before paying for it. "
                "The one exception: with `luminaires=true` a real call first rewrites the "
                "project's `source.cal` when it holds an earlier package's bytes, or re-records "
                "it, and a failure there is refused only by the real call."
            )
        ),
    ] = False,
    inline_timeout_s: Annotated[
        StrictFloat,
        Field(
            description=(
                "published per docs/tool-surface.md §10; it does not yet bound how long a call may "
                "run (the tool description's KNOWN LIMITATION)."
            )
        ),
    ] = DEFAULT_INLINE_TIMEOUT_S,
) -> PointInTimeResponse:
    """Illuminance or daylight factor at a registered sensor grid, for one sky; for luminance, use run_render.

    Use it for one sky at one moment, or for the placed luminaires; for every hour of a year use
    `run_annual` and read it with `results`, for an image `run_render`, and for per-zone maintained
    illuminance and uniformity under the luminaires `run_electric`.

    Composes `rad_core.photometry`, `rad_engine.sky` and `rad_engine.rtrace` over a project's
    registered geometry, materials and sensor grid.

    KNOWN LIMITATION: every call runs inline (synchronously) and none is ever handed off to `job`.
    `rad_engine.jobs.Step.stdin_path` exists since R43 and could carry `rtrace`'s sensor points, but
    this tool has not been moved onto it and submits no job. `inline_timeout_s` is accepted and
    published for forward compatibility but does not yet bound how long a call may run. To reproduce
    a run as a script, pass the result's `artifact_id` to `export(artifact_id=...)` (R729): every real
    call registers its sky (when it has one), its octree and its result as manifest artifacts.

    A `sky` of your own is refused with "daylight_factor" because it would still be divided by
    10 000 lux, so the percentage would be a ratio between two different skies rather than a
    daylight factor.

    A sky dict's site is `latitude`, `longitude_east`, `meridian_east` (degrees, east-positive), all
    three or none; with none, the project's registered weather file supplies it
    (`SKY_LOCATION_FROM_EPW`), and with no weather file the call is refused (`SKY_SITE_REQUIRED`).
    """
    validation_refusal = _validate_arguments(
        quantity=quantity, sky=sky,
        window_group_states=window_group_states, luminaires=luminaires, quality=quality,
        inline_timeout_s=inline_timeout_s, grid=grid,
    )
    if validation_refusal is not None:
        return validation_refusal

    runtime = current()
    try:
        return _dispatch(
            runtime, project=project, grid=grid, quantity=quantity, sky=sky,
            quality=quality, dry_run=dry_run, luminaires=luminaires,
        )
    except (LitSceneRefusal, PhotometryUnverifiedError) as exc:
        # Phase 11a spec §7/R487: the code, severity and field are the exception's own (R556/R554).
        logger.info("run_point_in_time refused: %s", exc)
        return refuse(str(exc), [Advisory(code=exc.code, severity=exc.severity, field=exc.field, message=str(exc))])
    except DaylightFactorSkyRefused as exc:
        # `code=None` at the engine layer (rad_engine.sky owns no wire vocabulary); this layer
        # attaches ARGUMENT_OUT_OF_RANGE with field="sky" -- the same code and reasoning
        # `_validate_arguments` already applies for the shape of this refusal it can see without
        # calling the engine.
        #
        # **Under ruling R389 this clause is unreachable from `run_point_in_time` and is kept
        # anyway.** `_validate_arguments` now refuses on `quantity == "daylight_factor" and sky is
        # not None`, which is strictly wider than anything `_resolve_sky` can raise for, so no call
        # that reaches `_dispatch` can still be carrying a sky for this quantity. It is retained
        # because `rad_engine.sky.resolve_daylight_factor_sky` remains the authority on the refusal
        # and this tool must not turn the engine's exception into a traceback if that pre-check is
        # ever narrowed. Do not read its presence as evidence of a live path.
        logger.info("run_point_in_time(quantity=%s) refused: %s", quantity, exc)
        return _refuse_argument(str(exc), field="sky")
    except UnknownKeysRefused as exc:
        logger.info("run_point_in_time refused: %s", exc)
        return refuse(str(exc), exc.advisories)
    except SkyError as exc:
        logger.info("run_point_in_time refused: %s", exc)
        if exc.code is not None:
            return refuse(
                str(exc),
                [Advisory(code=exc.code, severity=exc.severity, field=exc.field, message=str(exc))],
            )
        return refuse(str(exc))
    except GridEmptyOnDiskError as exc:
        logger.info("run_point_in_time refused: %s", exc)
        return refuse(
            str(exc),
            [Advisory(code=exc.code, severity="error", field="grid", message=str(exc))],
        )
    except GridUnreadableError as exc:
        logger.info("run_point_in_time refused: %s", exc)
        return refuse(
            str(exc),
            [Advisory(code=exc.code, severity="error", field="grid", message=str(exc))],
        )
    except GridNotFoundError as exc:
        logger.info("run_point_in_time refused: %s", exc)
        return refuse(str(exc))
    except NoGeometryRegisteredError as exc:
        logger.info("run_point_in_time refused: %s", exc)
        return refuse(
            str(exc),
            [Advisory(code=exc.code, severity="error", field="project", message=str(exc))],
        )
    except SceneInlineCommandError as exc:
        # Ruling R33 / commit 9a93e51: a registered scene file carrying a hostile `!` line is
        # refused at the `oconv` boundary, where the content is actually read -- the security hole
        # is genuinely closed and the command never runs (verified: the marker file the probe
        # command would have written is never created). What was *not* closed is the wire shape.
        # This exception is a bare `RuntimeError`, disjoint from `ProjectError`/`RadianceError`, so
        # it escaped this chain and reached the client as a raw `ToolError`: `structured_content`
        # empty, no code on the wire, and the raw message -- which names an **absolute filesystem
        # path** -- spilled into the error text. That is the check-0.4 violation class, with a path
        # disclosure on top.
        #
        # Shaped here with the exception's own allocated `SCENE_INLINE_COMMAND` (`error`), via the
        # same `getattr` route `geometry_tool` and `materials_tool` already use for this exact
        # exception. `field="project"`: the offending file is registered project state, not an
        # argument of this call -- the caller passed a project handle, and the refusal is about
        # what that project contains.
        logger.info("run_point_in_time refused: %s", exc)
        return refuse(
            str(exc),
            [Advisory(
                code=getattr(exc, "code", "SCENE_INLINE_COMMAND"), severity="error",
                field="project", message=str(exc),
            )],
        )
    except (EmptyOctreeError, OctreeSetOverflowError, OctreeTooManyFilesError) as exc:
        logger.info("run_point_in_time refused: %s", exc)
        code = getattr(exc, "code", None)
        if code is None:
            return refuse(str(exc))
        return refuse(
            str(exc),
            [Advisory(code=code, severity=getattr(exc, "severity", "error"), message=str(exc))],
        )
    except NotARegularFileError as exc:
        # R48/R58, as `run_electric`: something not a regular file at the results file's name --
        # refused, never followed.
        logger.info("run_point_in_time refused: %s", exc)
        return refuse(str(exc), [Advisory(code="PROJECT_PATH_NOT_A_REGULAR_FILE", severity="error",
                                          field="project", message=str(exc))])
    except RtraceOutputError as exc:
        # N-74/L-114 (R830): `rtrace`'s output does not give one value per traced point -- the code
        # and reading `run_electric` gives it (`electric_tool._mismatch`).
        logger.info("run_point_in_time refused: %s", exc)
        message = (
            f"rtrace's output did not parse as one value per point: {exc}. The values cannot be "
            "attributed to the points. This is a fault inside the server, not something about the "
            "arguments; no results file was written, and the failure should be reported."
        )
        return refuse(message, [Advisory(code="RTRACE_OUTPUT_MISMATCH", severity="error", message=message)])
    except RadianceNotFound as exc:
        logger.info("run_point_in_time refused: %s", exc)
        return refuse(str(exc), [Advisory(code="RADIANCE_NOT_FOUND", severity="error", message=str(exc))])
    except RadianceError as exc:
        logger.info("run_point_in_time failed: %s", exc)
        return refuse(str(exc))
    except (ProjectError, PathOutsideRoot, RootsNotConfigured, CodedWriteRefusal) as exc:
        logger.info("run_point_in_time refused: %s", exc)
        return refuse_engine_error(exc)
    except ArithmeticError as exc:
        # The last resort for the family `ValueError` does not cover. Every *known* producer is
        # guarded at its source (`_coerce_override`'s finiteness check, `_validate_quality`'s own
        # clause above), so reaching here is a defect rather than a caller error -- but an uncaught
        # exception escaping a tool is a JSON-RPC error with no `structuredContent` at all, which is
        # the one outcome check 0.4 forbids outright. A coded-less refusal is a strictly better
        # failure than a malformed envelope; the register allocates nothing for "the server's own
        # arithmetic overflowed" and inventing a code would be worse than reporting the gap.
        logger.warning("run_point_in_time failed in arithmetic: %r", exc)
        return refuse(
            f"run_point_in_time could not complete: {type(exc).__name__}: {exc}. This is an "
            "arithmetic overflow inside the server rather than something about the arguments; if a "
            "quality override or a sky argument carried an extreme value, reducing it is the "
            "workaround, and the failure should be reported."
        )


def _dispatch(
    runtime: Runtime,
    *,
    project: str,
    grid: str,
    quantity: str,
    sky: dict[str, Any] | str | None,
    quality: str | dict[str, float],
    dry_run: bool,
    luminaires: bool,
) -> CallToolResult:
    if (
        refusal := refuse_missing("run_point_in_time", project=project, grid=grid, quantity=quantity)
    ) is not None:
        return refusal
    loaded = runtime.open(project)
    # Phase 11a spec §7's after-open refusals, also under `dry_run`, before the sky is resolved:
    # none placed, the R489 run bound, then R487's source.cal and file checks. They write nothing:
    # R551's rewrite of an earlier package's source.cal waits for `commit_lit_support`, below every
    # refusal (R569).
    if luminaires:
        verify_lit_files(runtime, loaded)
    ground_reflectance = loaded.manifest.project.ground_reflectance
    # A lamp-only run (luminaires, no sky) generates no sky, so its north is never read.
    north = None if luminaires and sky is None else _project_north(loaded)

    # R762: the sky's keys first -- a retired `longitude` names its successor before anything
    # reads a site -- then the site, from this call or the project's weather file, above `dry_run`.
    if key_advisories := sky_key_advisories(sky):
        raise UnknownKeysRefused(key_advisories)
    sky_inputs = resolve_sky_inputs(runtime, loaded, sky)
    sky_spec, sky_advisories = _resolve_sky(
        quantity=quantity, sky_arg=sky, ground_reflectance=ground_reflectance, north=north,
        luminaires=luminaires, site=sky_inputs.site, epw_irradiance=sky_inputs.epw_irradiance,
    )
    sky_advisories = sky_inputs.advisories + sky_advisories
    # The argv builders range-check `month`/`day` themselves, but they run inside `_run_pipeline`,
    # below the `dry_run` branch -- so `month=13` refused on a real call and returned success on a
    # dry run. Same class as the scene scan hoisted below, and the same fix: run the engine's own
    # validator here, where both paths reach it. A lamp-only run has no sky to validate.
    if sky_spec is not None:
        validate_sky_dates(sky_spec)
    parameters = resolve_quality(quality)
    # `paths` is resolved here rather than after the `dry_run` branch because `_validate_scene`'s
    # scene scan resolves `scene_files` relative to exactly the `cwd` `_run_pipeline` later hands
    # `build_octree`. Reading it from a different base would scan different files than the ones the
    # real call compiles, which is the drift this fix exists to remove.
    paths = ProjectPaths(loaded.paths.root)
    scene = _validate_scene(loaded, grid=grid, cwd=paths.root, luminaires=luminaires)
    refuse_grid_off_recorded_floor(  # R776; after the scene scan, above dry_run
        grid, scene.polygon, scene.points, height=scene.recorded_height,  # R784
    )

    # R501/R555: the switch's advisories are knowable without a spawn, so both the dry run and the
    # real run carry the same list. Lit: `lit_advisories` and R492's near field. Unlit with
    # placements: `LUMINAIRES_EXCLUDED` at warn, never for a daylight factor (R479).
    switch_advisories: list[Advisory] = []
    if luminaires:
        switch_advisories.extend(lit_advisories(loaded.manifest, quantity=quantity, result_field="values"))
        near = near_field_advisory(loaded.manifest, [(p.x, p.y, p.z) for p in scene.points])
        if near is not None:
            switch_advisories.append(near)
    elif quantity != "daylight_factor":
        excluded = excluded_advisory(loaded.manifest, severity="warn", tool="run_point_in_time")
        if excluded is not None:
            switch_advisories.append(excluded)
    # R819: a BSDF bound by window_group(action='assign_bsdf') is not what this tool traces; read
    # from the manifest, so the dry run and the real run carry it alike.
    bsdf_advisories = [
        a for a in (bsdf_not_applied_advisory(loaded, tool="run_point_in_time"),) if a is not None
    ]

    # Everything above this line is validation: reading the manifest, resolving the sky's shape and
    # the quality preset, and proving the geometry and the named grid are both there and readable.
    # Nothing has spawned a process yet. `dry_run` stops here, on the same path a real call has
    # already walked -- check 3.9's own requirement that this be the *same* validation code, not a
    # parallel copy that happens to agree today. A refusal raised by any of it leaves this function
    # by exception and is shaped identically for both, in `run_point_in_time`'s one handler chain.
    if dry_run:
        # **Every advisory a dry run can honestly know, not just the sky's.** This previously
        # emitted `sky_advisories` alone, which silently dropped `DEEP_POINT_NOISE` and
        # `RESULT_IS_INITIAL_ILLUMINANCE` -- both computable from the validated scene with nothing
        # spawned. The visible cost was that a caller previewing a DF study saw no `DF_SKY_FORCED`
        # companion warnings and got them only on the real run. `_scene_advisories` is the single
        # function both paths call, so this list cannot drift from the real call's by editing one.
        return emit(DryRunOk(
            advisories=(
                sky_advisories + switch_advisories + bsdf_advisories
                + _scene_advisories(scene, quantity=quantity, lit=luminaires)
            ) or None
        ))

    # R568/R569: a lit run's only project write -- rewriting an earlier package's source.cal, or
    # healing a support record an earlier rewrite never saved -- happens here, after every refusal
    # above and immediately before the first spawn, and never on a dry run.
    if luminaires:
        commit_lit_support(runtime, loaded, tool="run_point_in_time")
    env = discover()
    started = time.monotonic()

    pipeline = _run_pipeline(
        env, scene=scene, quantity=quantity, sky_spec=sky_spec,
        parameters=parameters, cwd=paths.root, luminaires=luminaires,
    )
    wall_time_s = time.monotonic() - started
    advisories = sky_advisories + switch_advisories + bsdf_advisories + pipeline.advisories

    ambient_radius = ambient_interpolation_radius_m(pipeline.parameters, pipeline.octree_cube[3])

    # No job is ever submitted (see the module docstring's inline/job note), so there is no real
    # JobStore record to name. A synthesised id says so rather than borrowing the shape of a real one.
    job_id = f"inline-{int(started * 1000)}"

    # **The committed file is written first, and it is written from the FULL set.** It is the
    # complete data the `resource_link` and the `path` field both point at, so it must not be
    # affected by the wire bound applied immediately below.
    committed_path = _write_results_file(
        paths, grid=grid, values=pipeline.values, roots=runtime.roots
    )

    # Rulings R348 and R730: seal the sky and the octree `_run_pipeline` built, and the result file
    # just written -- below the write, because an artifact is registered only once it is on disk --
    # so a caller has real artifacts to point `export` at. Timed outside `wall_time_s` above: that
    # field reports the physics run's own cost, and a manifest write is not part of it.
    assert scene.grid_record_id is not None  # `_validate_scene` always sets it
    _register_point_in_time_artifacts(
        runtime, paths, pipeline=pipeline, sky_spec=sky_spec, grid=grid,
        grid_record_id=scene.grid_record_id, quantity=quantity, luminaires=luminaires,
    )

    # `statistics` were computed by `_run_pipeline` over the full set, before this point -- a mean
    # over a truncated list would be a wrong number, which is the whole hazard class this project
    # refuses. Only the *inlined* list is bounded; `n_points` keeps reporting the real count, so
    # `len(values) < n_points` is what tells a caller the list was cut.
    inline_values = pipeline.values[:MAX_INLINE_VALUES]
    if len(inline_values) < len(pipeline.values):
        advisories.append(Advisory(
            code="RESULT_TRUNCATED",
            severity="warn",
            field="values",
            message=(
                f"`values` carries the first {len(inline_values)} of {len(pipeline.values)} sensor "
                f"points; the result is emitted into two content blocks, so inlining all of them "
                f"would cross the wire twice. `statistics` are computed over all "
                f"{len(pipeline.values)} values, not over this list. The complete grid, one value "
                f"per line in grid order, is the committed file at `path` ({committed_path}), also "
                "linked as a resource on this result."
            ),
        ))

    result = PointInTimeResult(
        quantity=quantity,  # type: ignore[arg-type]
        unit=_UNIT[quantity],  # type: ignore[arg-type]
        grid=grid,
        n_points=len(pipeline.values),
        values=inline_values,
        statistics=pipeline.stats,
        sky=pipeline.sky_view,
        luminaires=lit_placements_view(loaded.manifest) if luminaires else None,
        source="rtrace",
        artifacts_used=None,
        parameters=parameters_dict(pipeline.parameters),
        ambient_interpolation_radius_m=ambient_radius,
        octree_cube=list(pipeline.octree_cube),
        job_id=job_id,
        wall_time_s=wall_time_s,
        path=str(committed_path),
        artifact_id=point_in_time_result_id(grid),
        advisories=advisories or None,
    )

    return _emit_with_resource_link(result, path=committed_path, project=loaded.paths.name, grid=grid)
