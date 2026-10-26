"""`run_ugr` -- docs/superpowers/specs/2026-09-20-phase-11c-ugr-design.md §5-§6.

Unified Glare Rating (CIE 117), measured off the project's placed luminaires as Radiance has
already built them -- no photometry is re-derived here (design §6's rewrite). Plumbing only: the
pure arithmetic is `rad_core.ugr`, the glarefile format and the `rtrace`/`glarendx` spawns are
`rad_engine.glarendx`, and this module validates arguments, builds advisories and shapes the
result. No verdict is published (R608): there is no `ugr_met` and no `maintenance_factor` -- UGR is
not scale-invariant, so applying one would report a dirtier installation as a *better* glare
rating (design §5).

**The order of the checks is a contract** (design §6, R569): the arguments (`_refuse_vectors` and
`run_point_in_time`'s own `quality`/`inline_timeout_s` clauses, before the project is opened) ->
the lamps (`verify_lit_files`) -> the geometry and the `!` scan -> `dry_run` stops. `_prepare`
walks every one of those for both a dry run and a real one, spawning nothing and writing nothing,
so the two cannot disagree about a refusal or about an advisory knowable before the spawn.

**A ray belongs to an instance by where it lands, not by the modifier it reports** (P1). There is
one `.rad` file per luminaire *type* and every placement is a bare `!xform` of it, so the `light`
modifier names the type and is shared by every instance of it; the emitters handed to
`rad_engine.glarendx.sample_emitters` are therefore keyed by a synthetic instance key
(`"<placement>#<index>"`) carrying that instance's own absolute emitter box.
"""

from __future__ import annotations

import logging
import math
import os
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, ClassVar, Final, Literal

from mcp_types import CallToolResult
from pydantic import BaseModel, ConfigDict, Field, RootModel, StrictFloat

from rad_core.luminaire_layout import rotated_extent
from rad_core.manifest import InputRecord
from rad_core.quality import QualityParameters
from rad_core.ugr import (
    AT_THE_EYE,
    MAX_TOTAL_RAYS,
    MIN_HITS,
    OUTSIDE_CUT,
    WRAPS_THE_EYE,
    BackgroundNotPositive,
    Instance,
    RayBudgetExceeded,
    SourceRow,
    Vec,
    background_luminance,
    cone_for,
    cull,
    indirect_illuminance_lux,
    rays_per_emitter,
    reduce_samples,
)
from rad_engine import ProjectError, ProjectPaths, RadianceError, RadianceNotFound, discover
from rad_engine.glarendx import (
    DEFAULT_RAYS_PER_EMITTER,
    EmitterSample,
    GlarendxValueNotFinite,
    RtraceOutputMismatch,
    run_ugr_index,
    sample_emitters,
    vertical_illuminance_at,
    write_glarefile,
)
from rad_engine.ies import PhotometryUnverifiedError
from rad_engine.octree import (
    EmptyOctreeError,
    OctreeSetOverflowError,
    OctreeTooManyFilesError,
    SceneInlineCommandError,
    build_octree,
)
from rad_engine.project import LoadedProject
from rad_engine.rtrace import (
    ambient_interpolation_radius_m,
    direct_exact_forced,
    parameters_dict,
    resolve_quality,
)
from rad_engine.safe_io import NotARegularFileError, refuse_linked_components
from rad_mcp.advisories import to_wire_all
from rad_mcp.envelope import (
    Advisory,
    RadResult,
    Refusal,
    emit,
    one_of_schema,
    refuse,
    refuse_missing,
)
from rad_mcp.lit_scene import (
    LitSceneRefusal,
    _header,
    _points,
    _rot,
    _type_of,
    _types_by_name,
    commit_lit_support,
    lit_placements_view,
    placement_records,
    verify_lit_files,
)
from rad_mcp.paths import CodedWriteRefusal, PathOutsideRoot, RootsNotConfigured, contain_as_named, write_failure
from rad_mcp.point_in_time_tool import (
    _METADATA_TIMEOUT_S,
    DEFAULT_INLINE_TIMEOUT_S,
    NoGeometryRegisteredError,
    QualityValue,
    _refuse_argument,
    _scan_scene_for_inline_commands,
    _scene_files,
)
from rad_mcp.point_in_time_tool import (
    _validate_arguments as _validate_point_in_time_arguments,
)
from rad_mcp.runtime import Runtime, current, refuse_engine_error

__all__ = [
    "GLAREFILE_NAME",
    "LIMIT_BASIS_APPLICATION",
    "UGR_DESTRUCTIVE_ACTIONS",
    "UGR_SAMPLE_SEED",
    "ExcludedView",
    "FormulaView",
    "SourceView",
    "UgrDryRunOk",
    "UgrLimit",
    "UgrPayload",
    "UgrResponse",
    "UgrResult",
    "run_ugr",
]

logger = logging.getLogger(__name__)

#: `run_ugr` takes no `action`, deregisters no input and deletes no file a caller registered, so it
#: has no destructive action -- the same property `ELECTRIC_DESTRUCTIVE_ACTIONS` records for
#: `run_electric` (`tests/test_protocol.py`'s map is pinned to the tools).
UGR_DESTRUCTIVE_ACTIONS: frozenset[str] = frozenset()

#: design §5: "a NEW string" -- not `rad_core.en12464.LIMIT_BASIS_TASK`
#: (`"per_activity_not_in_corpus"`), whose documented meaning is the task-area `U_o` basis. One
#: constant for two quantities is the defect R103/R112 already ruled against. The corpus *does*
#: carry licht.de's representative maxima (ch01:210-218); what is absent is the full
#: per-application table, and this string says that and no more.
LIMIT_BASIS_APPLICATION: Final[str] = "per_application_table_not_in_corpus"

#: F8 (design §4/§6): `glarendx` exits 1 on a `view_up` parallel to `view_direction` ("view up
#: parallel to view direction"), with no result to parse -- refused here as `ARGUMENT_OUT_OF_RANGE`
#: rather than surfacing as a `RadianceError` from a spawn that never had a chance to succeed.
_PARALLEL_TOLERANCE: Final[float] = 1e-9

#: The seed `rad_core.ugr.ray_directions` draws this tool's cone samples from. Fixed, so two calls
#: with the same arguments against the same scene sample the same directions and return the same
#: rating -- a UGR that moved between identical calls would be indistinguishable from one that
#: moved because something in the project changed. Not a physical quantity and not sourced.
UGR_SAMPLE_SEED: Final[int] = 20260920

#: This run's *published* glarefile, under the project's results directory. One name per project,
#: overwritten on every real call -- the "report of the last run, not an accumulating history"
#: posture R348 already states for `run_point_in_time`'s octree and values file. **It is an
#: artefact of the run, never an input to it:** the copy `glarendx` reads is `mkstemp`ed beside it
#: and deleted in a `finally`, because a fixed name another call can rewrite between this call's
#: write and its spawn publishes that call's rating under this call's observer (whole-branch
#: review, Critical 1).
GLAREFILE_NAME: Final[str] = "ugr.glr"

#: `tempfile.mkstemp`'s prefix for both of this tool's temporaries: the octree (`dir=paths.root`,
#: `suffix=".oct"`) and the submitted glarefile copy (`dir=glarefile.parent`, `suffix=".glr"`).
#: `rad_engine.project._UGR_OCTREE_TEMP_NAME` exempts exactly the `.oct` shape from the orphan
#: scan, on `_ELECTRIC_OCTREE_TEMP_NAME`'s terms (C4) -- `rad_engine` may not import this module,
#: so the two spellings are held together by
#: `tests/test_project.py::test_a_ugr_temp_octree_is_not_an_orphan`, which reads this constant.
#: The `.glr` copy lives under `paths.results`, not `paths.root`; `_orphan_entries_against` only
#: scans `paths.root`'s own top-level entries, so a leftover there is outside the orphan scan's
#: reach already and needs no pattern (measured, R706).
UGR_OCTREE_PREFIX: Final[str] = ".ugr-"

#: The tail of `MIN_HITS`, in the same units: an instance whose hit count is under this many is
#: named by `UGR_SOURCE_UNDERSAMPLED` even when it clears the floor and is published. **Unsourced**
#: (docs/tool-surface.md `## Unsourced values`, Task 12's row): nothing states where "near the
#: floor" ends. Twice the floor is chosen because `MIN_HITS`' own docstring puts the fractional
#: standard error of a solid angle at about `1/sqrt(hits)` -- about 7 % at the floor and about 5 %
#: at this bound -- so this advisory covers the band where that error is still the largest term in
#: the published `solid_angle_sr`, and says so rather than implying a sourced limit.
_UNDERSAMPLED_NEAR: Final[int] = 2 * MIN_HITS

#: `glarendx`'s own attribution, published as `formula.implementation` (design §5).
_IMPLEMENTATION: Final[str] = "glarendx -t ugr (Greg Ward, Raphael Compagnon)"

#: The opener of `verify_lit_files`' refusals for this tool (R574): `run_ugr` has no `luminaires`
#: argument, so "luminaires=true" would name one it does not take.
_LABEL: Final[str] = "run_ugr rates the glare from every placement"

_NO_GEOMETRY_RECOVERY: Final[str] = (
    "Call geometry(action='add') with at least one scene before run_ugr -- there is nothing to "
    "build an octree from, and a luminaire in an empty scene has no room to be glaring in."
)

#: `_validate_point_in_time_arguments` takes a `grid` because its own caller has one. `run_ugr` has
#: none, so a legal literal is passed and that clause cannot fire; the `quality` and
#: `inline_timeout_s` clauses -- the two arguments this tool refuses exactly as `run_point_in_time`
#: does -- are what the call is for. Same posture as `electric_tool._validate_arguments`, which reuses
#: the same validator for the same two arguments rather than copying their messages.
_NO_GRID_OF_ITS_OWN: Final[str] = "ugr"

#: **This tool's own inline bound, 60 s, not `run_point_in_time`'s 20 s (R671).** Sized from the
#: measurement, not chosen for comfort: with the two `rtrace` spawns instrumented separately on this
#: machine, 2026-09-21, the `rtrace -I` behind `E_total` took 8.2 s at 4 kept instances, 25.0 s at
#: 20 and 25.9 s at 21, while the budgeted emitter sampler finished in 0.5-4.4 s at every count. At
#: 20 s an ordinary twenty-luminaire room was refused on a first call -- and refused *silently* as
#: to the cause, because advisories do not survive a refusal and the message names the binary
#: (`rtrace`) rather than which spawn or the recovery. 60 s clears the measured 25.9 s with room for
#: a slower machine without becoming a bound that lets a genuinely stuck spawn sit for minutes.
#: Unsourced, like every figure in this tool's budget; see tool-surface.md's `Unsourced values`.
UGR_DEFAULT_INLINE_TIMEOUT_S: Final[float] = 60.0

def _undersampled_reason(n_rays: int) -> str:
    """Why an instance was dropped, for `sources_excluded.reason`.

    `OUTSIDE_CUT` and `AT_THE_EYE` are `rad_core.ugr`'s own strings; this third one is decided here
    because `reduce_samples` answers `None` and says nothing about how that should read on the wire.

    A function rather than the constant it was, because it takes **the run's own ray count**:
    `DEFAULT_RAYS_PER_EMITTER` is no longer always what was cast (`rays_per_emitter` scales it down
    past 20 kept instances -- whole-branch review, Critical 2), and a reason naming 20 000 when
    8000 were cast would misdescribe the count the floor was applied to.
    """
    return f"under-resolved: fewer than {MIN_HITS} of {n_rays} sampling rays reached it"


def _unit(v: list[float]) -> tuple[float, float, float]:
    """`v` normalised to length 1, or the zero vector back when `v` already is one.

    The zero-length case is not reachable for `view_direction` (refused earlier in
    `_refuse_vectors`), but `view_up` has no such guard, and a zero `view_up` should read as
    "the roll of the view is undefined" -- the same refusal a parallel one gets -- rather than
    raising `ZeroDivisionError` out of a helper with no code of its own.
    """
    length = math.sqrt(sum(c * c for c in v))
    if length == 0.0:
        return (0.0, 0.0, 0.0)
    return (v[0] / length, v[1] / length, v[2] / length)


# --- step 1: the arguments, before the project is opened --------------------------------------------
#
# **Every `step N` below is the PUBLISHED order -- `docs/tool-surface.md` §20's *How a call runs*,
# which is the order this module actually executes in and the one a caller observes.** Design §6
# numbers several of the same steps differently, because it puts the octree build ahead of the
# cull and this code does not; where the two disagree, these comments follow the published list
# and the design is cited by name rather than by number.


def _refuse_vectors(
    view_position: list[float], view_direction: list[float], view_up: list[float]
) -> CallToolResult | None:
    """Step 1: every vector finite, `view_direction` non-zero, `view_up` not parallel to
    it. Nothing is spawned by any clause here. (Design §6 numbers this one the same.)"""
    for name, v in (
        ("view_position", view_position),
        ("view_direction", view_direction),
        ("view_up", view_up),
    ):
        # `not isinstance(c, bool)`: `bool` is a subclass of `int`, so `[True, False, False]`
        # passed this check and was normalised to a unit vector along x (whole-branch review,
        # minor). A caller who typed a boolean into a coordinate has made a mistake that a
        # silently-accepted 1.0 hides.
        if len(v) != 3 or not all(
            isinstance(c, (int, float)) and not isinstance(c, bool) and math.isfinite(c) for c in v
        ):
            return _refuse_argument(
                f"{name} must be three finite numbers in project coordinates; got {v!r}.",
                field=name,
            )
    if math.sqrt(sum(c * c for c in view_direction)) == 0.0:
        return _refuse_argument(
            "view_direction is a zero vector, so there is no line of sight to rate a UGR against.",
            field="view_direction",
        )
    d = _unit(view_direction)
    u = _unit(view_up)
    cross = (
        d[1] * u[2] - d[2] * u[1],
        d[2] * u[0] - d[0] * u[2],
        d[0] * u[1] - d[1] * u[0],
    )
    if math.sqrt(sum(c * c for c in cross)) <= _PARALLEL_TOLERANCE:
        return _refuse_argument(
            "view_up is parallel to view_direction, which leaves the roll of the view undefined. "
            "glarendx exits 1 on this ('view up parallel to view direction'), so it is refused here "
            "rather than surfacing as a Radiance error. Pass an up vector across the line of sight, "
            "for example [0, 0, 1] for a horizontal view.",
            field="view_up",
        )
    return None


def _refuse_arguments(
    view_position: list[float], view_direction: list[float], view_up: list[float],
    quality: str | dict[str, Any], inline_timeout_s: float,
) -> CallToolResult | None:
    """Step 1, in full: this tool's three vectors, then `quality` and `inline_timeout_s`
    exactly as `run_point_in_time` refuses them.

    **The second half is Task 10's, and it closes a check 0.4 hole rather than adding a feature.**
    `quality` is this tool's own documented argument ("as run_point_in_time") and Task 10 is the
    first code to resolve it: `rad_engine.rtrace.resolve_quality` signals every rejection by
    raising a bare `ValueError` -- `"ultra"`, `{"zzz": 1}`, `{"ab": 3.5}` -- which is not a
    `ProjectError`, a `RadianceError` or anything else this module's handler chain catches, so it
    would have reached the wire as a raw `ToolError` with `structuredContent: null` and no advisory
    code. Before this task nothing resolved `quality` at all, so `run_ugr(quality="ultra")`
    returned a *successful* dry run. `point_in_time_tool._validate_arguments` is called rather than
    copied for the reason `electric_tool` gives for the same call: the code, the field and the
    sentence are `run_point_in_time`'s own and cannot drift from it.
    """
    if (refusal := _refuse_vectors(view_position, view_direction, view_up)) is not None:
        return refusal
    return _validate_point_in_time_arguments(
        quantity="illuminance", sky=None, window_group_states=None,
        luminaires=True, quality=quality, inline_timeout_s=inline_timeout_s,
        grid=_NO_GRID_OF_ITS_OWN,
    )


# --- the published models (design §5) --------------------------------------------------------------


class _Nested(BaseModel):
    model_config = ConfigDict(extra="forbid")


class UgrLimit(_Nested):
    value: float | None = None
    basis: str


class SourceView(_Nested):
    placement: str
    type_name: str
    instance: int
    direction: list[float]
    distance_m: float
    solid_angle_sr: float
    luminance_cd_m2: float
    #: What `UGR_SOURCE_UNDERSAMPLED` (Task 10) reports against -- additive over the design §5
    #: sketch (P4).
    hits: int


class ExcludedView(_Nested):
    placement: str
    #: What `UGR_INCLUDES_LIGHT_LOSS_FACTOR` (Task 10) names -- additive over the design §5 sketch
    #: (P4).
    type_name: str
    instance: int
    reason: str


class FormulaView(_Nested):
    basis: Literal["radiance_glarendx"] = "radiance_glarendx"
    implementation: str
    radiance_version: str
    standard_verified: bool = False


# Design §5. `ugr` is nullable rather than the `0.0` glarendx returns for a source at or beyond 90
# degrees (design §4, F7). `background_luminance_cd_m2` and `indirect_illuminance_lux` are nullable
# for the same reason: on a null exit there is no background, and a required float there would be the
# schema violation `envelope.py` names (P4). Phase 22 (R810): the wire text named only the empty cull;
# it now names all three null exits `_run` has (fix round 1 item 1 and R670 added the other two).
class UgrResult(RadResult):
    """The Unified Glare Rating at one eye point and view direction, from the placed luminaires.

    `ugr` is `null`, never 0.0, when no instance can be rated: none survives the 90 degree cull
    (`UGR_NO_SOURCES_IN_VIEW`, or `UGR_ALL_SOURCES_WRAP_THE_EYE` when some were too close to the eye
    to sample), or every survivor resolved below the hit floor (`UGR_ALL_SOURCES_UNDERSAMPLED`).
    Then `background_luminance_cd_m2`, `indirect_illuminance_lux` and `glarefile` are `null` and
    `sources` is empty. An instance in `sources_excluded` is not rated as a source; its `reason` says
    why. `ugr_limit.value` is always `null`: there is no pass or fail. `glarefile` names the file of
    source rows glarendx rated; a later run that reaches glarendx rewrites it. `view_direction` and
    `view_up` are the normalised vectors the run used.
    """

    OMIT_WHEN_NONE: ClassVar[frozenset[str]] = frozenset({"advisories"})

    ugr: float | None
    background_luminance_cd_m2: float | None
    indirect_illuminance_lux: float | None
    sources: list[SourceView]
    sources_excluded: list[ExcludedView]
    formula: FormulaView
    ugr_limit: UgrLimit
    #: The glarefile this call submitted to `glarendx`, absolute. **Null on either exit where no
    #: file was written** -- nothing survived the cull, or everything that did resolved below the
    #: hit floor -- because no source row was ever assembled, so nothing was written and no
    #: `glarendx` ran. Fix round 1, item 5: this was the empty string, which is a sentinel a
    #: consumer resolves against its own working directory, producing a path that exists and is
    #: wrong; `null` cannot be mistaken for one. P4 settled this model's nullability before the
    #: second null exit existed, so this widens that ruling rather than overturning it.
    glarefile: str | None
    view_position: list[float]
    #: Normalised -- "the line of sight; normalised by the server" (design §5). This is the vector
    #: the cull, the `VIEW=` line and `E_direct` all used, not the one the caller typed.
    view_direction: list[float]
    view_up: list[float]
    parameters: dict[str, Any]
    ambient_interpolation_radius_m: float
    job_id: str
    wall_time_s: float


class UgrDryRunOk(RadResult):
    """`dry_run=True` and every check passed: nothing was spawned or written.

    `advisories` holds those decided from the project's manifest alone. A real call can add the
    view's cull and sampling codes (`UGR_SOURCE_OUTSIDE_CUT`, `UGR_NO_SOURCES_IN_VIEW` and others)
    and `UGR_FORMULA_NOT_STANDARD_VERIFIED`.
    """

    dry_run: Literal[True] = True


UgrPayload = UgrResult | UgrDryRunOk | Refusal


class UgrResponse(RootModel[UgrPayload]):
    @classmethod
    def model_json_schema(cls, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return one_of_schema(super().model_json_schema(*args, **kwargs), UgrResult)


# --- steps 2-4: the pre-spawn half ------------------------------------------------------------------


@dataclass(frozen=True)
class _Placed:
    """One placed instance and the `light` modifier its type's emitter carries.

    The two are kept together, and keyed by `key`, because of P1: the modifier names the *type*,
    not the instance -- one `.rad` per type, every placement a bare `!xform` of it with no `-m` --
    so four ceiling lamps of one type report one modifier between them. `key` is the identity the
    sampler and `sources`/`sources_excluded` are both built on.
    """

    instance: Instance
    modifier: str

    @property
    def key(self) -> str:
        return f"{self.instance.placement}#{self.instance.index}"


@dataclass
class _Prepared:
    """What `_prepare` proved before any spawn, for `_run` to consume unchanged."""

    loaded: LoadedProject
    paths: ProjectPaths
    scene_files: list[str]
    placed: list[_Placed]
    parameters: QualityParameters
    #: Every advisory knowable before the spawn -- the dry run's list and the real run's first part.
    advisories: list[Advisory]


def _emitter_modifier(type_record: InputRecord, name: str) -> str:
    """The `light` modifier of a converted type's emitter surfaces, from its own record.

    `ies2rad` writes the emitter as `void brightdata <name>_dist`, then `<name>_dist light
    <name>_light`, then the surfaces modified by `<name>_light`; `rad_core.photometry_file.
    read_converted` records exactly those non-surface identifiers, in file order, as `modifiers`.
    Radiance requires a modifier to be defined before it is used, so the surfaces' own modifier is
    necessarily the **last** of them -- which is what this reads, rather than matching on a
    `_light` suffix (a naming convention this server does not own) or re-parsing the `.rad` (a
    Radiance file format, which `rad_mcp` may not learn).

    A record with no modifiers at all is a hand-edited manifest, refused the way `lit_scene`
    refuses every other unreadable type record rather than raising `IndexError` from a helper.
    """
    modifiers = list((type_record.registered_by.arguments if type_record.registered_by else {}).get("modifiers") or [])
    if not modifiers:
        raise LitSceneRefusal(
            f"luminaire type {name!r} records no modifiers, so the emitter its placements build "
            "cannot be identified in the traced scene; re-import that type.",
            code="LUMINAIRE_FILES_CHANGED", severity="error", field=None)
    return str(modifiers[-1])


def _placed(loaded: LoadedProject) -> list[_Placed]:
    """Every placed instance, with its emitter box computed from the **current** type record (R726).

    The box is `rotated_extent(emitter_min, emitter_max, rot)` of the placement's type record and
    the placement's own `rot` -- the type's emitter box **after the placement's `rot` and relative
    to the placement position**, which is what `luminaire_tool._placement_arguments` records as
    `rotated_min`/`rotated_max`; `rotated_extent` rotates about the type's origin and translates
    nothing. `lit_scene.near_field_advisory` computes the same pair and hands it to
    `near_field_count`, which adds the position to both to get the world-space box, so adding
    `position` here is that same reading and not a second one.

    **The recorded `rotated_min`/`rotated_max` snapshot is not read** (R726). It is recorded at
    placement time and refreshed by a re-import of the type (R724), but a manifest in which the type
    was re-imported before R724, or one edited by hand, can carry a stale one, and reading it
    published a UGR for an emitter the scene no longer holds (R724's measured -8.397846). The box is computed from the type record, as the
    overlap check does (R675), so a stale snapshot cannot reach a result.
    """
    types = _types_by_name(loaded.manifest, None)
    out: list[_Placed] = []
    for record in placement_records(loaded.manifest):
        if record.registered_by is None:
            # `verify_lit_files` has already refused this; the guard is here so the reader is not
            # left wondering, and so this function is safe in isolation.
            continue
        a = record.registered_by.arguments
        type_record = _type_of(types, record, None)
        # **Every read of `a` below is inside this guard** (whole-branch review, minor). A
        # hand-edited manifest missing `rotated_min`, `rotated_max` or `positions` raised
        # `KeyError`; one holding a short or non-numeric position raised `IndexError` or
        # `TypeError`; none of the three is in `run_ugr`'s handler chain, so each escaped as an
        # uncoded `ToolError` with `structuredContent: null` (check 0.4). It is the same condition
        # `_emitter_modifier` just above and `lit_scene._header` already answer, so it is answered
        # the same way and with the same code, rather than with a new one. Since R726 the box is
        # computed from the type record's `emitter_min`/`emitter_max` and the placement's `rot`, and
        # those reads are inside the guard too, so a record missing or mangling one is answered alike.
        try:
            type_name = a["type_name"]
            modifier = _emitter_modifier(type_record, type_name)
            t = type_record.registered_by.arguments  # type: ignore[union-attr]
            # Fix round 2 of R844: `rot` and `positions` are shape-checked (three numbers each) before
            # any use, naming the key in the `ValueError` the clause below words as this record's
            # fault; a four-number position was read as its first three.
            rot = a["rot"]
            try:
                rot = _rot(rot)
            except (TypeError, IndexError, ValueError) as exc:
                raise ValueError(f"'rot' is {rot!r}, not null or three numbers") from exc
            low, high = rotated_extent(tuple(t["emitter_min"]), tuple(t["emitter_max"]),  # type: ignore[arg-type]
                                       tuple(rot) if rot is not None else None)
            name = a["name"]
            positions = a["positions"]
            try:
                positions = _points(positions)
            except (TypeError, IndexError, ValueError) as exc:
                raise ValueError(f"'positions' is {positions!r}, not a list of three numbers each") from exc
            instances = [
                Instance(
                    placement=name, type_name=type_name, index=index,
                    bounds_min=tuple(float(low[k]) + float(position[k]) for k in range(3)),  # type: ignore[arg-type]
                    bounds_max=tuple(float(high[k]) + float(position[k]) for k in range(3)),  # type: ignore[arg-type]
                )
                for index, position in enumerate(positions)
            ]
        except LitSceneRefusal:
            # Already coded about the record it names (`_emitter_modifier`'s refusal of the type):
            # `LitSceneRefusal` subclasses `ValueError`, so the clause below would re-blame this
            # placement for it.
            raise
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise LitSceneRefusal(
                f"luminaire placement record {record.id!r} does not record a readable emitter box "
                f"and position list ({type(exc).__name__}: {exc}); re-create that placement, or re-import its type "
                "if the type record is the damaged one.",
                code="LUMINAIRE_FILES_CHANGED", severity="error", field=None) from exc
        out += [_Placed(instance=instance, modifier=modifier) for instance in instances]
    return out


def _prepare(runtime: Runtime, *, project: str, quality: str | dict[str, Any]) -> _Prepared:
    """Steps 2-4, in the contract's order, spawning nothing and writing nothing: the lamps, the
    geometry and the `!` scan, then the advisories knowable from the manifest alone -- which is
    where a `dry_run` stops, in the caller below.

    `verify_lit_files` first (R569): `LUMINAIRES_NONE_PLACED` is refused **above** the `dry_run`
    exit, so a dry run cannot report success for a project a real call refuses. The geometry and
    the `!` scan follow for the same reason -- both are refusals a real call makes, and check 3.9
    requires `dry_run` to reach them too.
    """
    loaded = runtime.open(project)
    verify_lit_files(runtime, loaded, field=None, label=_LABEL)
    paths = ProjectPaths(loaded.paths.root)
    scene_files = _scene_files(loaded, luminaires=True, no_geometry_recovery=_NO_GEOMETRY_RECOVERY)
    _scan_scene_for_inline_commands(scene_files, cwd=paths.root)
    placed = _placed(loaded)
    return _Prepared(
        loaded=loaded, paths=paths, scene_files=scene_files, placed=placed,
        parameters=resolve_quality(quality),
        advisories=_knowable_advisories(loaded),
    )


# --- the advisories (design §8) ---------------------------------------------------------------------
#
# One function per code, in the table's order. `code=` and `severity=` stay literal at every
# `Advisory(...)` (R497/R554): `tests/test_advisories.py`'s register scan reads a code's severity
# only from a literal at the call site.


def _position_specific_advisory() -> Advisory:
    return Advisory(
        code="UGR_IS_POSITION_SPECIFIC", severity="warn", field="view_position",
        message=("this UGR is for this eye point and this view direction in this installation. It is "
                 "not the catalogue reference-room value UGR_R, which characterises the luminaire "
                 "rather than the installation, and the two are not interchangeable."))


def _formula_advisory(version: str) -> Advisory:
    return Advisory(
        code="UGR_FORMULA_NOT_STANDARD_VERIFIED", severity="warn", field="formula",
        message=("the rating comes from Radiance's glarendx -t ugr. Its arithmetic was verified "
                 "against CIE 117's formula by measurement and its position index is the published "
                 "Guth/Levin (1975) fit, but CIE 117's applicability bounds are not in this "
                 "server's corpus, so nothing here states for which source sizes and geometries "
                 f"the rating holds ({version})."))


def _no_sources_advisory() -> Advisory:
    return Advisory(
        code="UGR_NO_SOURCES_IN_VIEW", severity="warn", field="sources",
        message=("no placed luminaire instance is in front of this eye point along this view "
                 "direction, so there is nothing to rate and ugr is null. It is deliberately not "
                 "0.0: glarendx returns 0.000000 for a source at or beyond exactly 90 degrees, at "
                 "exit 0, and 0.0 reads as an excellent rating. Move the observer, turn the view, "
                 "or check the placements."))


def _all_undersampled_advisory(count: int, n_rays: int) -> Advisory:
    """The second null exit's own code, and **not** `UGR_NO_SOURCES_IN_VIEW` (fix round 1, item 1).

    Instances are in front of this eye point here -- that code's sentence says they are not, which
    would be false, and its recovery ("move the observer, turn the view, or check the placements")
    is not this condition's. The register's basis for allocate-once is recovery-distinctness, and
    these two recoveries differ: nothing is in view against everything in view resolved below the
    hit floor. Reusing the first code here would be exactly the defect design §8 cites for refusing
    to reuse `GLARE_NO_SOURCES_FOUND`.
    """
    return Advisory(
        code="UGR_ALL_SOURCES_UNDERSAMPLED", severity="warn", field="sources",
        message=(f"all {count} luminaire instance(s) in view resolved below the {MIN_HITS}-hit "
                 f"floor out of {n_rays} rays each, so there is no source row to "
                 "rate and ugr is null. It is deliberately not 0.0, which reads as an excellent "
                 "rating. They are in front of this eye point but too small, too distant or too "
                 "far occluded to resolve from here: check what stands between the eye and them, "
                 "move the observer, or rate a view where they are larger."))


def _outside_cut_advisory(placed: _Placed, angle_deg: float) -> Advisory:
    return Advisory(
        code="UGR_SOURCE_OUTSIDE_CUT", severity="info", field="sources",
        message=(f"{placed.key} (type {placed.instance.type_name!r}) is {angle_deg:.1f} degrees from "
                 "the view direction, at or beyond the 90 degree cut glarendx applies silently. It "
                 "contributes nothing to this rating and is listed in sources_excluded, never in "
                 "sources."))


def _wraps_the_eye_advisory(placed: _Placed) -> Advisory:
    """An instance in front of the eye that no forward cone contains, so it cannot be sampled.

    It names the direction of the error for the same reason `_undersampled_advisory` does: nothing
    subtracts the dropped instance's light from `E_total`, so it lands in the background term,
    raises `L_b` and lowers the rating of whatever survived.
    """
    return Advisory(
        code="UGR_SOURCE_WRAPS_THE_EYE", severity="warn", field="sources",
        message=(f"{placed.key} (type {placed.instance.type_name!r}) is close enough to the eye "
                 "that its own corners reach more than 90 degrees from the direction to its "
                 "centre, so no forward sampling cone contains it and it cannot be rated from "
                 "here. It is listed in sources_excluded, never in sources. Its light is still in "
                 "the measured vertical illuminance, so it lands in the background term and lowers "
                 "the rating of the instances that survived. Move the observer away from it."))


def _all_wrap_advisory(count: int) -> Advisory:
    """The third null exit's own code, and **not** `UGR_NO_SOURCES_IN_VIEW` (R670).

    That code's sentence says no instance is in front of this eye point. Here one or more is, and
    it is the nearest thing in the room to the observer's eye -- the same falsehood fix round 1,
    item 1 removed for the all-undersampled exit. The recoveries differ too, which is the
    register's basis for allocating a code: move the observer away from the luminaire, rather than
    turn the view or check the placements.
    """
    return Advisory(
        code="UGR_ALL_SOURCES_WRAP_THE_EYE", severity="warn", field="sources",
        message=(f"all {count} luminaire instance(s) in front of this eye point are close enough "
                 "to it that no forward sampling cone contains them, so there is no source row to "
                 "rate and ugr is null. It is deliberately not 0.0, which would read as an "
                 "excellent rating. Move the observer away from the luminaires, or rate a view "
                 "from a position CIE 117 would assess."))


def _undersampled_advisory(placed: _Placed, hits: int, n_rays: int) -> Advisory:
    """**The under-floor branch names the direction of the error** (whole-branch review, Important
    2). An excluded instance is dropped from `sources`, and nothing subtracts its light from
    `E_total` -- `vertical_illuminance_at` measures every photon arriving at the eye, this one's
    included -- so it lands in `E_indirect`, raises `L_b`, and *lowers* the rating of the sources
    that survived. The old sentence said only that "the rating beside it does not include it",
    which reads as a neutral omission; a caller must be able to tell which way the error runs."""
    published = hits >= MIN_HITS
    return Advisory(
        code="UGR_SOURCE_UNDERSAMPLED", severity="warn", field="sources",
        message=(f"{placed.key} (type {placed.instance.type_name!r}) was hit by {hits} of "
                 f"{n_rays} sampling rays, "
                 + (f"near the {MIN_HITS}-hit floor: its solid angle and luminance carry a "
                    "counting error large enough to matter to the rating. "
                    if published else
                    f"under the {MIN_HITS}-hit floor: it is excluded from sources rather than "
                    "published from counting noise. Its light is NOT excluded from the rating "
                    "beside it: E_total is measured at the eye and includes this instance, so "
                    "what was dropped from sources is counted as background instead, which "
                    "raises the background luminance and makes the published ugr lower -- more "
                    "flattering -- than one that resolved this instance as a source. ")
                 + "A source this small or this far off the line of sight is what the floor exists "
                   "for; raise the observer's view onto it, or place it closer to the line of sight, "
                   "if it should count."))


def _rays_reduced_advisory(kept: int, n_rays: int) -> Advisory:
    """`UGR_RAY_BUDGET_REDUCED` -- the sampler cast fewer rays per instance than its default.

    Whole-branch review, Critical 2. The reduction is not free and this says what it costs: a
    solid angle's fractional counting error goes as `1/sqrt(hits)`, so it is larger by
    `sqrt(DEFAULT_RAYS_PER_EMITTER / n_rays)` than a full-budget sample of the same instance, and
    an instance that would have cleared `MIN_HITS` at the full count can fall under it at the
    reduced one and be excluded (`UGR_SOURCE_UNDERSAMPLED` then names it, as it does any other
    under-resolved instance). A reduction with nothing on the result to say so would publish
    exactly that as an ordinary rating.
    """
    factor = math.sqrt(DEFAULT_RAYS_PER_EMITTER / n_rays)
    return Advisory(
        code="UGR_RAY_BUDGET_REDUCED", severity="warn", field="sources",
        message=(f"{kept} luminaire instances survived the 90 degree cull, so each was sampled "
                 f"with {n_rays} rays rather than the usual {DEFAULT_RAYS_PER_EMITTER}: one call "
                 f"casts at most {MAX_TOTAL_RAYS} rays in total, and the whole payload is built in "
                 "memory before rtrace starts, so no timeout bounds it. A solid angle's counting "
                 f"error goes as 1/sqrt(hits), so every solid_angle_sr and luminance_cd_m2 here "
                 f"carries about {factor:.1f}x the sampling error of a full-budget run, and an "
                 f"instance that would have cleared the {MIN_HITS}-hit floor at the full count may "
                 "fall under it here and be excluded. Rate a view with fewer instances in front of "
                 "the eye for the full budget."))


def _light_loss_factor_advisory(
    type_name: str, factor: float | None, header_factors: tuple[tuple[str, float], ...]
) -> Advisory:
    """Every loss already multiplied into the emitter this run sampled, named.

    **The code name stays and the trigger widens** (whole-branch review, Important 4). This fired
    on `light_loss_factor` alone, and an IES header's own `applied_factors` -- a ballast factor, a
    ballast-lamp factor -- dims the built emitter in exactly the same way and through exactly the
    same `ies2rad` argument. `rad_mcp.lit_scene.lit_advisories` already treats the pair together
    for `RESULT_IS_INITIAL_ILLUMINANCE` (R493), and before this a header-dimmed type reached the
    published luminances with **no disclosure at all**. Codes are allocated once and never renamed,
    so `UGR_INCLUDES_LIGHT_LOSS_FACTOR` keeps its name; the register row and this message carry the
    meaning.
    """
    named = ", ".join(f"{label} {value:g}" for label, value in header_factors)
    if factor is not None and header_factors:
        what = f"was imported with light_loss_factor {factor:g}, and its IES header applies {named}"
    elif factor is not None:
        what = f"was imported with light_loss_factor {factor:g}"
    else:
        what = f"has an IES header that applies {named}"
    return Advisory(
        code="UGR_INCLUDES_LIGHT_LOSS_FACTOR", severity="info", field="sources",
        message=(f"luminaire type {type_name!r} {what}, so "
                 "ies2rad -m has already dimmed the emitter this run sampled: the luminances in "
                 "sources, and this ugr, are as built rather than as catalogued. Nothing here "
                 "corrects for it, and run_ugr applies no maintenance factor of its own -- UGR is "
                 "not scale-invariant, and scaling it would rate a dimmer installation as better."))


def _knowable_advisories(loaded: LoadedProject) -> list[Advisory]:
    """Every advisory this tool can decide from the manifest alone -- the dry run's whole list, and
    the first part of a real run's.

    `UGR_FORMULA_NOT_STANDARD_VERIFIED` is **not** here, and the omission is deliberate: its
    sentence names the Radiance version that produced the rating, and resolving the install to read
    that version spawns a process (`rad_engine.env.discover` probes it). A dry run that spawned to
    fill in an advisory would break the one promise `dry_run` makes.
    """
    advisories = [_position_specific_advisory()]
    types = _types_by_name(loaded.manifest, None)  # every view's type exists: `lit_placements_view` refused otherwise
    seen: dict[str, tuple[float | None, tuple[tuple[str, float], ...]]] = {}
    for view in lit_placements_view(loaded.manifest, None):
        factor = None if view.light_loss_factor is None else view.light_loss_factor.value
        # `field=None`: `run_ugr` has no `luminaires` argument for a refusal to name (R574).
        header_factors = _header(types[view.type_name], None).applied_factors
        if factor is not None or header_factors:
            seen.setdefault(view.type_name, (factor, header_factors))
    advisories += [_light_loss_factor_advisory(name, *seen[name]) for name in sorted(seen)]
    return advisories


# --- steps 5-11: the lit write, the cull, the octree, the samples, the glarefile and the rating ------


def _angle_deg(eye: Vec, view_direction: Vec, centre: Vec) -> float:
    """The angle between the view direction and the line from the eye to `centre`, in degrees.

    Trigonometry for a sentence, not photometry: it names a number in `UGR_SOURCE_OUTSIDE_CUT`'s
    message and enters no published quantity. The cull itself is `rad_core.ugr.cull`'s decision and
    is not re-made here -- this function is never asked whether an instance is in or out.
    """
    d = tuple(centre[i] - eye[i] for i in range(3))
    length = math.sqrt(sum(c * c for c in d))
    if length == 0.0:
        return 0.0
    cosine = sum(d[i] * view_direction[i] for i in range(3)) / length
    return math.degrees(math.acos(max(-1.0, min(1.0, cosine))))


def _source_view(placed: _Placed, row: SourceRow, eye: Vec) -> SourceView:
    centre = placed.instance.centre
    distance = math.sqrt(sum((centre[i] - eye[i]) ** 2 for i in range(3)))
    return SourceView(
        placement=placed.instance.placement, type_name=placed.instance.type_name,
        instance=placed.instance.index, direction=list(row.direction), distance_m=distance,
        solid_angle_sr=row.omega_sr, luminance_cd_m2=row.luminance_cd_m2, hits=row.hits,
    )


def _excluded_view(placed: _Placed, reason: str) -> ExcludedView:
    return ExcludedView(
        placement=placed.instance.placement, type_name=placed.instance.type_name,
        instance=placed.instance.index, reason=reason,
    )


def _run(runtime: Runtime, prepared: _Prepared, *, eye: Vec, view_direction: Vec, view_up: Vec,
         timeout_s: float) -> CallToolResult:
    """Steps 5-11: the lit support write and the install probe, the cull, the lit octree with no
    sky, the samples, `E_total`, the background, the glarefile, `glarendx`, and the result.

    **The numbering is the published one** (`docs/tool-surface.md` §20). Design §6 numbers the
    octree 4 and the cull 5; this code culls first, so the published list -- and these comments --
    put the cull at 6 and the octree at 7. The code is what a caller observes, so the documents
    follow it rather than the other way round.

    The order is the contract's. In particular the cull runs **before** any sampling and its
    dropped instances never reach `sources`, and a run with nothing left after it returns `ugr`
    null without writing a glarefile or spawning `glarendx` at all -- F7's `0.000000` for a source
    at exactly 90 degrees is why there is no number to publish rather than a flattering one.
    """
    loaded, paths = prepared.loaded, prepared.paths
    # Step 5: the lit support write, then the install probe.
    # R568/R569: the lit run's one project write, after every refusal above and before the first
    # spawn. `tool="run_ugr"` is what `registered_by` should name -- the call that actually rewrote
    # `source.cal` -- and `tests/test_protocol.py`'s own annotation for this tool already says
    # run_ugr writes it through here. `commit_lit_support`'s `tool` Literal carries this tool's
    # name as of fix round 1, so this is an ordinary call with nothing suppressed.
    commit_lit_support(runtime, loaded, tool="run_ugr")
    env = discover()
    version = env.version or "radiance version unknown"
    advisories = list(prepared.advisories) + [_formula_advisory(version)]

    # Step 6, before anything is spawned for it: the cull is pure geometry off the manifest. It
    # runs BEFORE the octree is built (step 7 below), which is where the published order and design
    # §6's own numbering part company.
    culled = cull(eye, view_direction, [p.instance for p in prepared.placed])
    # Keyed by the `Instance` itself (a frozen dataclass, so hashable) rather than by a second
    # spelling of `_Placed.key`: `cull` hands back the very objects it was given, and two spellings
    # of one identity are two things to drift.
    placed_of = {p.instance: p for p in prepared.placed}
    kept = [placed_of[instance] for instance in culled.kept]
    excluded: list[ExcludedView] = []
    wrapped = 0
    for instance, reason in culled.excluded:
        dropped = placed_of[instance]
        excluded.append(_excluded_view(dropped, reason))
        if reason == OUTSIDE_CUT:
            advisories.append(
                _outside_cut_advisory(dropped, _angle_deg(eye, view_direction, instance.centre)))
        elif reason == WRAPS_THE_EYE:
            wrapped += 1
            advisories.append(_wraps_the_eye_advisory(dropped))

    # Step 6, second half: the ray budget the kept count implies, decided here -- before the octree
    # is built, since nothing below it can make a refused call affordable (whole-branch review,
    # Critical 2). `sample_emitters` builds its whole stdin payload in memory before the spawn, so
    # `timeout_s` does not bound it; `rays_per_emitter` is the bound, and a reduction is disclosed
    # because it costs precision.
    n_rays = rays_per_emitter(len(kept), DEFAULT_RAYS_PER_EMITTER)
    if n_rays != DEFAULT_RAYS_PER_EMITTER:
        advisories.append(_rays_reduced_advisory(len(kept), n_rays))

    # R611's shape: a temporary octree at the project root, gone whatever happens. Both `rtrace`
    # spawns read it, so it outlives the first one -- which is why it is not built inside a helper.
    handle, name = tempfile.mkstemp(dir=paths.root, prefix=UGR_OCTREE_PREFIX, suffix=".oct")
    os.close(handle)
    octree_path = Path(name)
    started = time.monotonic()
    try:
        # Step 7: the lit scene with no sky, as `run_electric` builds it. Bounded by
        # `_METADATA_TIMEOUT_S`, not by `timeout_s` -- the caller's `inline_timeout_s` bounds the
        # three spawns below, never this one.
        octree = build_octree(env, prepared.scene_files, out_path=octree_path, cwd=paths.root,
                              timeout=_METADATA_TIMEOUT_S)
        # R588: every lit run traces every lamp exactly (-dt 0 -dc 1) rather than culling sources
        # statistically. It is the `E_total` measurement that this changes -- `sample_emitters`
        # fixes its own argv at `-ab 0` and takes no parameters (P10).
        parameters, direct_advisory = direct_exact_forced(
            prepared.parameters, sky_has_sun=False, quantity="illuminance", luminaires=True)
        if direct_advisory is not None:
            advisories.extend(to_wire_all([direct_advisory]))
        radius = ambient_interpolation_radius_m(parameters, octree.bbox[3])

        if not kept:
            # Which null exit this is depends on *why* nothing is left. An instance that wraps the
            # eye is in front of it, so `UGR_NO_SOURCES_IN_VIEW`'s sentence would be false (R670).
            advisories.append(_all_wrap_advisory(wrapped) if wrapped else _no_sources_advisory())
            return _emit(
                ugr=None, background=None, indirect_lux=None, sources=[], excluded=excluded,
                glarefile=None, version=version, eye=eye, view_direction=view_direction,
                view_up=view_up, parameters=parameters, radius=radius, started=started,
                wall_time_s=time.monotonic() - started, advisories=advisories)

        # Step 8: one aimed cone per surviving instance, one `rtrace` for all of them, and the
        # hits assigned by modifier *and* box so two instances of one type cannot merge (P1).
        emitters = {
            p.key: EmitterSample(
                cone_for(eye, p.instance.bounds_min, p.instance.bounds_max),
                p.modifier, p.instance.bounds_min, p.instance.bounds_max)
            for p in kept
        }
        samples = sample_emitters(env, octree.path, eye=eye, emitters=emitters,
                                  n_rays=n_rays, seed=UGR_SAMPLE_SEED,
                                  cwd=paths.root, timeout_s=timeout_s)
        rows: list[tuple[_Placed, SourceRow]] = []
        for p in kept:
            hits = samples[p.key]
            row = reduce_samples(emitters[p.key].cone, hits, n_rays)
            if row is None:
                excluded.append(_excluded_view(p, _undersampled_reason(n_rays)))
            else:
                rows.append((p, row))
            if len(hits) < _UNDERSAMPLED_NEAR:
                advisories.append(_undersampled_advisory(p, len(hits), n_rays))
        if not rows:
            # Every surviving instance was under-resolved. There is no row to write and no
            # background to subtract one from, so `ugr` is null here exactly as it is on the empty
            # cull -- but under its **own** code (fix round 1, item 1): these instances are in
            # view, and `UGR_NO_SOURCES_IN_VIEW`'s published sentence says the opposite.
            advisories.append(_all_undersampled_advisory(len(kept), n_rays))
            return _emit(
                ugr=None, background=None, indirect_lux=None, sources=[], excluded=excluded,
                glarefile=None, version=version, eye=eye, view_direction=view_direction,
                view_up=view_up, parameters=parameters, radius=radius, started=started,
                wall_time_s=time.monotonic() - started, advisories=advisories)

        # Step 9: `E_total` from the same model, and the background the rows leave behind.
        # `background_luminance` raises `BackgroundNotPositive` when they leave none.
        e_total = vertical_illuminance_at(env, octree.path, eye=eye, view_direction=view_direction,
                                          parameters=parameters, cwd=paths.root, timeout_s=timeout_s)
        # Both quantities come from `rad_core.ugr`, and neither is derived from the other here
        # (fix round 1, item 4): publishing `background * pi` was background-luminance arithmetic
        # in a layer that may do none, and it round-tripped that module's own `/pi` besides.
        # `background_luminance` is defined in terms of `indirect_illuminance_lux` there, so the
        # two cannot disagree about what was subtracted or about when it is refused.
        source_rows = [row for _p, row in rows]
        indirect_lux = indirect_illuminance_lux(e_total, view_direction, source_rows)
        background = background_luminance(e_total, view_direction, source_rows)

        # Steps 10-11: the glarefile, then one `glarendx`, whose angle-0 row `parse_ugr` reads.
        #
        # **Two files, and the split closes the whole-branch review's Critical 1.** The stable name
        # is a fixed-name project file, one per project; the file `glarendx` reads is an **input to
        # the published number** -- it is written, and a *separate* spawn reads it back. Two
        # `run_ugr` calls on one project (a second server, or a CLI on the same root -- the threat
        # `rad_engine.project.manifest_lock` exists for) interleaving between the write and the
        # spawn would make call A publish **call B's rating** beside A's own `sources`,
        # `view_position` and `view_up`, at exit 0 with empty stderr and no advisory. So the
        # submitted copy is `mkstemp`ed, exactly as the octree above already is, and no other call
        # can reach it; the stable name is published as an artefact of this run and nothing reads
        # it back.
        #
        # The published copy goes first, so a directory, a FIFO or a symlink at that name is
        # refused (`NotARegularFileError` -> `PROJECT_PATH_NOT_A_REGULAR_FILE`) before a `glarendx`
        # is paid for, and so a hardlink there is replaced rather than written through
        # (`write_glarefile` stages and renames; R526/R612/R640/R646).
        # N-112 (R851): the path as named, so `write_glarefile`'s `lstat` check sees a symlink at
        # the name; `contain`'s resolved path let one aimed inside the root be replaced through.
        glarefile = contain_as_named(paths.results / GLAREFILE_NAME, runtime.roots)
        # N-113 (R851): a link at any component below the project root is refused before the write.
        refuse_linked_components(glarefile, below=paths.root)
        # R855: a write the disk or the permissions refuse (a read-only `ugr.glr`, a full disk) is
        # `PROJECT_WRITE_FAILED`, naming a read-only file as the cause; until then it escaped uncoded.
        try:
            write_glarefile(glarefile, view_position=eye, view_direction=view_direction,
                            view_up=view_up, rows=source_rows, indirect_lux=indirect_lux)
        except NotARegularFileError:
            raise
        except OSError as exc:
            raise write_failure(glarefile, exc, what="the glarefile", outcome=(
                "Nothing has been rated; a glarefile already there is unchanged.")) from None
        # The glarefile is written from here on, so a failure of the copy `glarendx` reads says so
        # (the fix-wave re-review: one message covered both, "a glarefile already there is unchanged").
        written = f"The glarefile was written at {glarefile}; nothing has been rated."
        copy = "the temporary copy of the glarefile that glarendx reads"
        try:
            submitted_handle, submitted_name = tempfile.mkstemp(
                dir=glarefile.parent, prefix=UGR_OCTREE_PREFIX, suffix=".glr")
        except OSError as exc:
            raise write_failure(glarefile.parent / f"{UGR_OCTREE_PREFIX}<random>.glr", exc, what=copy,
                                outcome=written) from None
        os.close(submitted_handle)
        submitted = Path(submitted_name)
        try:
            try:
                write_glarefile(submitted, view_position=eye, view_direction=view_direction,
                                view_up=view_up, rows=source_rows, indirect_lux=indirect_lux)
            except NotARegularFileError:
                raise
            except OSError as exc:
                raise write_failure(submitted, exc, what=copy, outcome=written) from None
            ugr = run_ugr_index(env, submitted, cwd=paths.root, timeout_s=timeout_s)
        finally:
            submitted.unlink(missing_ok=True)
    finally:
        octree_path.unlink(missing_ok=True)

    return _emit(
        ugr=ugr, background=background, indirect_lux=indirect_lux,
        sources=[_source_view(p, row, eye) for p, row in rows], excluded=excluded,
        glarefile=str(glarefile.resolve()), version=version, eye=eye, view_direction=view_direction,
        view_up=view_up, parameters=parameters, radius=radius, started=started,
        wall_time_s=time.monotonic() - started, advisories=advisories)


def _emit(*, ugr: float | None, background: float | None, indirect_lux: float | None,
          sources: list[SourceView], excluded: list[ExcludedView], glarefile: str | None, version: str,
          eye: Vec, view_direction: Vec, view_up: Vec, parameters: QualityParameters,
          radius: float, started: float, wall_time_s: float,
          advisories: list[Advisory]) -> CallToolResult:
    """The payload, assembled once after the published order's last step (design §6 numbered
    this assembly 11). Called from all three of `_run`'s exits, so the
    no-sources shape and the full shape cannot drift into disagreeing about a field."""
    return emit(UgrResult(
        ugr=ugr,
        background_luminance_cd_m2=background,
        indirect_illuminance_lux=indirect_lux,
        sources=sources,
        sources_excluded=excluded,
        formula=FormulaView(implementation=_IMPLEMENTATION, radiance_version=version),
        ugr_limit=UgrLimit(value=None, basis=LIMIT_BASIS_APPLICATION),
        glarefile=glarefile,
        view_position=list(eye),
        view_direction=list(view_direction),
        view_up=list(view_up),
        parameters=parameters_dict(parameters),
        ambient_interpolation_radius_m=radius,
        # No job is submitted: a synthesised id, `run_point_in_time`'s and `run_electric`'s spelling.
        job_id=f"inline-{int(started * 1000)}",
        wall_time_s=wall_time_s,
        advisories=advisories or None,
    ))


# --- the tool ----------------------------------------------------------------------------------------


def run_ugr(
    project: Annotated[
        str | None,
        Field(description="the project handle, with geometry and at least one placed luminaire."),
    ] = None,
    view_position: Annotated[
        list[StrictFloat] | None,
        Field(description="the observer's eye, [x, y, z] metres, project coordinates."),
    ] = None,
    view_direction: Annotated[
        list[StrictFloat] | None,
        Field(description="the line of sight; normalised by the server. Must be non-zero."),
    ] = None,
    view_up: Annotated[
        list[StrictFloat] | None,
        Field(
            description=(
                "REQUIRED -- the position index depends on it, measured at up to 8.59 UGR units "
                "for one source. Must not be parallel to view_direction: glarendx exits 1 on that "
                "(\"view up parallel to view direction\"), so it is refused here "
                "(ARGUMENT_OUT_OF_RANGE) rather than surfacing as a Radiance error."
            )
        ),
    ] = None,
    quality: Annotated[
        str | dict[str, QualityValue],
        Field(
            description=(
                "as run_point_in_time -- drives the rtrace behind the background illuminance at "
                "the eye. The emitter sampler runs at -ab 0 whatever this says, because it "
                "measures what fraction of a cone's rays land on an emitter and what radiance that "
                "emitter returns on a primary hit, and no ambient bounce changes either; its "
                "precision knob is its ray count."
            )
        ),
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
        Field(
            description=(
                "seconds, passed to the run as `timeout_s`: it bounds the emitter `rtrace`, the "
                "`E_total` `rtrace` and `glarendx`, each on its own, and no other spawn. Default "
                "60 s (R671). Must be finite, above 0 and at most "
                "2147483.647 s (`rad_mcp.timeouts.SPAWN_TIMEOUT_CEILING_S`), refused otherwise "
                "before the project is opened, dry run included. Every call runs inline. The bound is what the rtrace behind "
                "E_total needs on an ordinary room: measured 25.0 s at 20 kept instances on this "
                "machine, where the emitter sampler took 4.4 s."
            )
        ),
    ] = UGR_DEFAULT_INLINE_TIMEOUT_S,
) -> UgrResponse:
    """Unified Glare Rating (CIE 117) from a project's placed luminaires, measured off the built
    scene rather than re-derived from photometry (design §4-§6).

    Use it for the glare of the luminaires `luminaire` placed, seen from one observer position; for
    daylight glare probability (DGP) use `run_glare`.

    Returns `ugr` (null, never 0.0, when no placed instance can be rated, and then
    `UGR_NO_SOURCES_IN_VIEW`, `UGR_ALL_SOURCES_WRAP_THE_EYE` or `UGR_ALL_SOURCES_UNDERSAMPLED` says
    why), the background luminance and indirect illuminance measured off the same rays, one row per
    surviving source and one per excluded instance, and the sourced (always-null) application
    limit. There is no pass, no fail and no maintenance factor (R608; design §5): UGR is not
    scale-invariant, and applying one would report a dirtier installation as a better glare rating.

    A real call leaves behind this project's `source.cal` (the photometric correction every lit
    run needs, rewritten only when it holds an earlier package's bytes) and the `manifest.json`
    entry that records it; a run that reaches `glarendx` also rewrites the glarefile
    `08_results/ugr.glr`, while a null `ugr`, a dry run and a refusal before that write leave an
    earlier run's file as it was. It also writes and
    removes the octree, the copy `glarendx` actually reads, and a staging file per atomic write;
    docs/tool-surface.md's section "20. `run_ugr`" names each of them. The submitted glarefile is separate from the published one because the
    published name is one per project, and a fixed name that a second call can rewrite between this
    call's write and its spawn is a rating published under the wrong observer; the two hold the
    same bytes, written from the same arguments. Not exportable: the run is inline and registers no
    artifact.
    """
    if (
        refusal := refuse_missing(
            "run_ugr",
            project=project,
            view_position=view_position,
            view_direction=view_direction,
            view_up=view_up,
        )
    ) is not None:
        return refusal  # type: ignore[return-value]
    assert project is not None
    assert view_position is not None and view_direction is not None and view_up is not None
    if (refusal := _refuse_arguments(view_position, view_direction, view_up, quality,
                                     inline_timeout_s)) is not None:
        return refusal  # type: ignore[return-value]
    runtime = current()
    try:
        prepared = _prepare(runtime, project=project, quality=quality)
        if dry_run:
            return emit(UgrDryRunOk(advisories=prepared.advisories or None))  # type: ignore[return-value]
        return _run(runtime, prepared, eye=tuple(float(c) for c in view_position),  # type: ignore[no-any-return,arg-type]
                    view_direction=_unit(view_direction), view_up=_unit(view_up),
                    timeout_s=inline_timeout_s)
    except (BackgroundNotPositive, GlarendxValueNotFinite) as exc:
        # Design §9's two code-less refusals, and the posture `envelope.refuse`'s docstring sets: the
        # register has no code for either, both are conditions of this server's own arithmetic
        # rather than of the caller's arguments, and a caller cannot act on either by changing an
        # argument. Prose, not a fabricated code.
        logger.info("run_ugr refused: %s", exc)
        return refuse(str(exc))  # type: ignore[return-value]
    except RtraceOutputMismatch as exc:
        # Whole-branch review, Important 1. `RTRACE_OUTPUT_MISMATCH` is reused, not re-allocated:
        # R638 allocated it for `run_electric`'s "one `rtrace`, a value count other than the points
        # traced", which is this condition one tool over, with the same recovery -- report the
        # fault -- and recovery-distinctness is the register's own basis for allocating once.
        # `field=None`, as there: it names a server fault, not an argument.
        logger.info("run_ugr refused: %s", exc)
        return refuse(str(exc), [Advisory(code="RTRACE_OUTPUT_MISMATCH", severity="error",  # type: ignore[return-value]
                                          field=None, message=str(exc))])
    except RayBudgetExceeded as exc:
        # Whole-branch review, Critical 2. `field=None`, deliberately: the condition is how many
        # placed instances stand in front of this eye point, which is a fact about the project's
        # placements *and* about the view, and no single argument owns it -- the same reason
        # `LUMINAIRES_NONE_PLACED` reaches this tool with a null field (R574). The message names
        # both recoveries instead.
        logger.info("run_ugr refused: %s", exc)
        return refuse(str(exc), [Advisory(code="UGR_TOO_MANY_SOURCES_TO_SAMPLE",  # type: ignore[return-value]
                                          severity="error", field=None, message=str(exc))])
    except (LitSceneRefusal, PhotometryUnverifiedError) as exc:
        # The code, severity and field are the exception's own (R556/R554).
        logger.info("run_ugr refused: %s", exc)
        return refuse(str(exc), [Advisory(code=exc.code, severity=exc.severity,  # type: ignore[return-value]
                                          field=getattr(exc, "field", None), message=str(exc))])
    # From here, `run_electric`'s own clauses, in its order and shape (electric_tool.py).
    except NoGeometryRegisteredError as exc:
        logger.info("run_ugr refused: %s", exc)
        return refuse(str(exc), [Advisory(code=exc.code, severity="error", field="project",  # type: ignore[return-value]
                                          message=str(exc))])
    except SceneInlineCommandError as exc:
        logger.info("run_ugr refused: %s", exc)
        return refuse(str(exc), [Advisory(  # type: ignore[return-value]
            code=getattr(exc, "code", "SCENE_INLINE_COMMAND"), severity="error", field="project",
            message=str(exc))])
    except (EmptyOctreeError, OctreeSetOverflowError, OctreeTooManyFilesError) as exc:
        logger.info("run_ugr refused: %s", exc)
        code = getattr(exc, "code", None)
        if code is None:
            return refuse(str(exc))  # type: ignore[return-value]
        return refuse(str(exc), [Advisory(code=code, severity=getattr(exc, "severity", "error"),  # type: ignore[return-value]
                                          message=str(exc))])
    except NotARegularFileError as exc:
        # R48/R58: something not a regular file at a name this run writes (the glarefile, the
        # temporary octree) -- refused, never followed.
        #
        # **This clause only became true of the glarefile in the whole-branch fix wave.** Until
        # then `write_glarefile` opened the destination `"wb"` through `open_regular_file`, which
        # raises `NotARegularFileError` for a *symlink* but hands a **directory** back as
        # `IsADirectoryError [Errno 21]` and a **FIFO** as `OSError [Errno 6] ENXIO` -- neither of
        # them this class, so neither was caught here, and both reached the wire as an uncoded
        # `ToolError` with `structuredContent: null` (check 0.4). `write_glarefile` now calls
        # `refuse_existing_non_regular_destination` first, which answers every non-regular type in
        # this one class, and a hardlink at the name is replaced rather than written through.
        logger.info("run_ugr refused: %s", exc)
        return refuse(str(exc), [Advisory(code="PROJECT_PATH_NOT_A_REGULAR_FILE", severity="error",  # type: ignore[return-value]
                                          field="project", message=str(exc))])
    except RadianceNotFound as exc:
        logger.info("run_ugr refused: %s", exc)
        return refuse(str(exc), [Advisory(code="RADIANCE_NOT_FOUND", severity="error",  # type: ignore[return-value]
                                          message=str(exc))])
    except RadianceError as exc:
        # Design §9: `glarendx` exiting non-zero, or printing something `parse_ugr` cannot read as a
        # row, arrives here and names the glarefile in its own message.
        logger.info("run_ugr failed: %s", exc)
        return refuse(str(exc))  # type: ignore[return-value]
    except (ProjectError, PathOutsideRoot, RootsNotConfigured, CodedWriteRefusal) as exc:
        logger.info("run_ugr refused: %s", exc)
        return refuse_engine_error(exc)  # type: ignore[return-value]
    except ArithmeticError as exc:
        # `run_point_in_time`'s and `run_electric`'s last resort: an uncaught exception is a
        # JSON-RPC error with no `structuredContent` (check 0.4); a code-less refusal is the better
        # failure.
        logger.warning("run_ugr failed in arithmetic: %r", exc)
        return refuse(  # type: ignore[return-value]
            f"run_ugr could not complete: {type(exc).__name__}: {exc}. This is an arithmetic "
            "overflow inside the server rather than something about the arguments; the failure "
            "should be reported.")
