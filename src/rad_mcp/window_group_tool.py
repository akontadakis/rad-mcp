"""The `window_group` tool -- docs/tool-surface.md#6-window_group. Builds `define`, `list`,
`assign_bsdf`, `bind_schedule` and `remove`; `make_secondary_source` is deferred (ruling R64).

Phase 25 C2a: `define` records the `automated` mark (R869); `bind_schedule` copies an automated
group's hourly open/closed CSV into `05_bsdf/automated_schedules/` and registers it as an
`automated_schedule` input (R870); `assign_bsdf` reads `states={"closed": "lm83_default"}` as
LM-83's default 5 % diffuse closed blind composed from the group's sealed open state (R871,
`_assign_lm83_default`).

Thin, in the sense CONTEXT.md's Layers table means it: this module contains and validates
arguments, picks the engine call, maps findings onto the wire and shapes the envelope. **No number
reported here is computed here.** `rad_core.apertures` owns the plane, the outward normal, the
Klems up-vector, coplanarity and the azimuth/obstruction spread statistics; `rad_engine.klems`
renders the `kbin(...)` expression and casts the sky-visibility rays. This module plumbs.

## `make_secondary_source` is not here

Deferred whole to Phase 9 by ruling R64 (docs/plan.md): it would register an `illum:<group>`
artifact, and `illum` is not a member of `rad_core.manifest.ArtifactKind` -- shipping it needs a
schema bump for a consumer (`run_render`/`run_point_in_time`) that does not exist yet.
`illum_directions`/`illum_samples` are therefore not on this module's signature either; adding them
now would publish a schema this phase cannot honour.

## `assign_bsdf` binds a pairing, and refuses two different mismatches per role

A basis mismatch is not a property of a file but of a **pairing** (ruling R60): a tensor-tree XML is
perfectly valid and becomes wrong only when handed to a chain it does not belong to. So this is the
layer that refuses, and `bsdf` -- which inspects files and never binds one -- keeps every basis code
allocated and unemitted. Which pairing is being checked is `role` (Task 10a, owner-approved -- ruling
R63's deferred half, closed the moment the term it was waiting on existed): `role="matrix"` (the
default, unchanged since Phase 6a) is the 3-phase `T` term, and `role="direct_sun"` (new here) is the
five-phase method's `Cds` term. Both arms compare the same two published booleans --
`suitable_for.three_phase` and `suitable_for.five_phase_direct`, exact complements of each other
(Phase 5) -- rather than inferring anything, and each mismatch earns its own register row so a
caller greps the true condition rather than a borrowed one (the defect docs/tool-surface.md SS9
records as *"a code whose definition is untrue of its condition is worse than no code"*):

- **`BSDF_TENSOR_NOT_FOR_MATRIX`** -- *"a tensor tree was handed to a 3-phase chain"* (SS9's own
  row). Computed from `suitable_for.three_phase`. This is check 5.4 clause 1, checked only at
  `role="matrix"`, and ruling R63 closes by naming this code for it.
- **`BSDF_BASIS_MISMATCH`** -- *"the assigned XML's basis is not the 145-bin Klems full basis the V
  and D matrices were binned into"* (SS6's own row). A *Klems* file off the full basis, checked only
  at `role="matrix"`: a half or quarter basis is a valid BSDF and a mismatch only against this chain.
  Keyed on the declared basis **name** and not on the bin count alone, because a file can name itself
  `LBNL/Klems Half` while still summing to 145 blocks -- measured directly on the renamed fixture --
  and the count alone would let it through.
- **`BSDF_KLEMS_NOT_FOR_DIRECT_SUN`** -- the reverse of the first row, checked only at
  `role="direct_sun"`: a Klems file handed to the five-phase direct-sun term. Computed from
  `suitable_for.five_phase_direct`, `three_phase`'s exact complement, so this is the identical
  comparison as clause 1 with the two booleans swapped. This is check 5.4 clause 2 -- the half R63
  named as *"deferred to Phase 7"* rather than refused outright, because the term it needed
  (`Cds`) did not exist yet. Phase 7a builds it, so the argument that names a state's destination is
  required rather than invented (owner-approved shape: `role="matrix" | "direct_sun"`).

**`role="direct_sun"` is a second binding on the state, never a replacement.** A 5-phase state needs
both files -- the Klems XML for `T`, the tensor tree for `Cds` -- so each role writes its own
`bsdf_*` input id (`bsdf_<group>_<state>` for `matrix`, `bsdf_<group>_<state>_direct_sun` for
`direct_sun`), and only `role="matrix"` ever seals or overwrites a `tmx` artifact. **The two ids are
not disjoint by construction** -- `_` is legal in a state name, so a state named `<x>_direct_sun`
bound at `role="matrix"` writes the same string `role="direct_sun"` writes for state `<x>` -- so a
call whose id would land on a binding the *other* role already owns is refused
(`_refuse_bsdf_input_id_collision`, `ARGUMENT_OUT_OF_RANGE`) rather than allowed to overwrite it.
Short of that collision, binding one role never touches, reseals or removes what the other role
already bound -- each is validated and committed against the manifest as it stands, independent of
whichever call came first. A state carrying only one of the two is a state some chain cannot build;
raising *that* refusal belongs to the tool that reads both roles back (Task 10), not to this one,
which only records what it was asked to bind.

**The tensor tree is registered as a plain `bsdf` input, not copied or sealed.** `role="matrix"`
seals a `tmx` copy (ruling R62, below) because the bound Klems XML *is* the `T` matrix `dctimestep`
multiplies directly. The tensor tree bound at `role="direct_sun"` is not multiplied directly -- it is
one build **input** to Task 13's `cdsmx` artifact, the same relationship a materials XML has to a
`dmx` build -- so it is registered and left exactly where the caller put it, the same way `geometry`
and `materials` inputs are.

## The `tmx` artifact is a copy, not an alias (ruling R62) -- and only `role="matrix"` writes one

`register_artifact` hard-refuses a path not on disk -- *"An artifact is committed only when it is on
disk with a digest; registering it first would publish a matrix that does not exist as reusable"* --
so sealing a `tmx` needs a file, and R62 settles which. Pointing it at the XML already sitting in
`05_bsdf/` was rejected: the same bytes would be both a `bsdf` **input** and a `tmx` **artifact**,
the artifact would sit outside the directory its own kind declares (`ARTIFACT_DIRECTORY["tmx"] ==
"07_matrices"`), and the record would carry nothing its input edge does not already carry. So the
bound XML is copied under the artifact's own name and the copy is sealed. The property this buys is
the reason for the cost: **an edit to the source XML after binding becomes detectable drift rather
than a silent substitution**, which is what ruling R7's `content_digest` exists to provide and which
the alias option would have discarded. The cost is a file copy per state, and the tensor-tree
fixture is 4.4 MB -- real, and accepted by that ruling.

`REQUIRED_EDGES["tmx"]` names `bsdf` and `window_group` as `input_kinds`, so binding also registers
the source XML as a `bsdf` input: the glazing BSDF and the aperture geometry each invalidate T, and
an edge that cannot move with the file it claims to describe would never fire that invalidation.

## The fallback ladder does not fire from this action, and the reason is the signature

`NO_BSDF_FOR_SHADING_DEVICE` and `rad_core.apertures.substitute_shading` are built and tested
(Task 2), and **no call site here can reach them**. `substitute_shading` needs a `device` key plus
`vlt`/`openness`/`reflectance` to match one of LM-83 SS2.2.7's seven rows, and SS6's published
signature carries none of them: `states` is `dict[str, str]`, a state name mapped to an XML *path*,
so there is no argument in which a caller can say "this state is a white louvre at 85 % reflectance".
Firing the ladder anyway would mean picking a row on the caller's behalf -- deciding that every
BSDF-less state is a fabric shade of unknown VLT -- which is a guess about their building and the
class of invention rulings R60 and R63 both refuse. Reported and ruled rather than quietly skipped:
a check that changes phase silently is indistinguishable from a check nobody ran.

## Registering a group is registering an edge, not a new file

A window group owns no bytes of its own -- it names polygons that already live in geometry this
project has registered. So `register_input(kind="window_group", ...)` (called through
`register_input_and_set_role`, ruling R133 -- see that function's own docstring for why `_define`
does not call `register_input` directly) is called with **the exact same `files` the owning
geometry input already carries**: same absolute paths, same bytes, same content digest. That is
what makes the manifest edge real rather than decorative -- `REQUIRED_EDGES["vmx"/"dmx"/"tmx"]` name
`window_group` as an `input_kinds` member precisely because aperture geometry invalidates V, D and
T, and an edge whose digest cannot move with the geometry it claims to describe would never fire
that invalidation. `register_input` refuses an input with no files at all (`rad_core.manifest`'s "an
input with nothing to digest contributes a constant to every digest downstream of it"), which is the
same reasoning applied one level up here: a `window_group` input pointing at nothing would be
exactly that constant.

## The refusal that matters most, and where it sits

*"a group pointing at a modifier that does not exist produces an all-zero V matrix, and an all-zero
matrix multiplies to a plausible zero"* (docs/tool-surface.md SS6). `aperture` names are resolved
against every polygon modifier the project's registered `geometry` inputs actually use -- read back
through `rad_engine.scene.read_scene` over each geometry input's own files, the same route
`rad_mcp.geometry_tool._assemble` uses to get `Polygon.modifier` values, because there is no
"declares" accessor at that layer, only "uses" (task-7b-report.md, cited in that module). A name
that resolves to zero polygons is refused with `ARGUMENT_OUT_OF_RANGE`, `field="aperture"` --
**above** the `dry_run` branch. Ruling R39: *"validation a caller can only trigger by paying for a
trace is not validation"* -- this check costs nothing (no octree, no spawn) and must not be gated
behind a flag that lets a caller skip paying for it once and then be surprised that the group they
built points at nothing.

## The obstruction check needs an exterior octree this tool does not own

`WINDOW_GROUP_OBSTRUCTION_VARIES` samples sky visibility in *the exterior octree* -- the D matrix's
own scene, not the interior one `rad_engine.klems`' own module docstring draws the same split
against. Nothing in this tool's signature takes an octree path, so it is found the way
`rad_mcp.geometry_tool._ground_modifiers` finds the project's ground materials: scanning
`loaded.manifest.inputs` for a `geometry` input whose `registered_by.arguments["role"]` is
`"exterior"`. Where none is registered, the check is skipped rather than guessed -- the same posture
`rad_mcp.weather_tool` states for `EPW_SITE_MISMATCH`: *"there is no project-declared location to
compare against... emitting it would require this module to invent"*. A scratch octree is built at
`_window_group_obstruction.oct` in the project root (the convention `rad_mcp.point_in_time_tool`
followed for `_point_in_time.oct` until R730, 2026-09-27, moved that octree to
`06_octrees/point_in_time.oct` and registered it; `rad_mcp.glare_tool`'s `_run_glare.oct` still
follows it) and is never registered: it is this tool's own working file, not an artifact anything
downstream depends on.

## The two-member obstruction case (carried from Task 5's review)

`rad_core.apertures.obstruction_outliers` on exactly two members always names **both** -- they sit
equidistant from the pairwise median by construction, not because both are unusual. The advisory
message below states the measured spread and both members' own fractions rather than singling one
out as "the outlier", which would be a false claim about geometry the check cannot actually tell
apart at n=2.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from pathlib import Path
from typing import Annotated, Any, Final, Literal

from mcp_types import CallToolResult
from pydantic import BaseModel, ConfigDict, Field, RootModel

from rad_core.apertures import (
    ApertureError,
    ApertureNormalAmbiguousError,
    ApertureNotCoplanarError,
    OBSTRUCTION_SPREAD_THRESHOLD,
    Aperture,
    azimuth_spread_deg,
    derive,
    is_horizontal,
    is_tilt_verified,
    obstruction_outliers,
    obstruction_spread,
)
from rad_core.blinds import (
    CLOSING_ROLES,
    MAX_AUTOMATED_SCHEDULE_BYTES,
    AutomatedScheduleError,
    parse_automated_schedule,
)
from rad_core.bsdf import (
    KLEMS_FULL_BINS,
    LM83_DEFAULT_DIFFUSE_FRACTION,
    BSDFError,
    BSDFFile,
    BSDFParseError,
    Basis,
    lambertian_klems_text,
    parse_bsdf,
)
from rad_core.geometry import Polygon
from rad_core.manifest import FileRef, ToolCall
from rad_engine import (
    LoadedProject,
    ProjectError,
    ProjectPaths,
    RadianceEnv,
    RadianceNotFound,
    SceneInlineCommandError,
    discover,
    open_project,
    read_scene,
    refuse_unscaled_geometry,
    register_input,
    register_input_and_set_role,
    save_manifest,
)
from rad_engine.bsdf import MAX_BSDF_BYTES
from rad_engine.chain import (
    AUTOMATED_SCHEDULE_INPUT_PREFIX,
    WINDOW_GROUP_INPUT_PREFIX,
    automated_schedule_file,
    is_automated as _is_automated,
)
from rad_engine.klems import bin_expression, sample_sky_visibility
from rad_engine.octree import build_octree
from rad_engine.project import bytes_digest, register_artifact
from rad_engine.run import RadianceError
from rad_engine.safe_io import (
    NotARegularFileError,
    open_regular_file,
    refuse_existing_non_regular_destination,
    resolved_parent_within,
)
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

__all__ = ["DESTRUCTIVE_ACTIONS", "LM83_DEFAULT", "WindowGroupResult", "window_group"]

Action = Literal[
    "define", "list", "assign_bsdf", "bind_schedule", "remove", "make_secondary_source"
]

#: R870: the input id `bind_schedule` registers an automated group's hourly schedule under, one per
#: group (`rad_engine.chain`'s constant, the one definition its readers share), and where the copy
#: of the caller's CSV is written (`05_bsdf/automated_schedules/`).
_SCHEDULE_PREFIX = AUTOMATED_SCHEDULE_INPUT_PREFIX
_SCHEDULE_DIRECTORY = "automated_schedules"

#: R871: the reserved `states` value `assign_bsdf` reads as "compose LM-83's default closed blind
#: from this group's sealed open state" rather than as a path, legal only as
#: `states={"closed": LM83_DEFAULT}` at `role="matrix"`, alone in its call. The composed XML is
#: server-written into `05_bsdf/lm83_default/` and sealed like any other matrix binding.
LM83_DEFAULT = "lm83_default"
_LM83_DEFAULT_DIRECTORY = "lm83_default"

#: `assign_bsdf`'s own argument (Task 10a, owner-approved shape). `"matrix"` is the default and is
#: today's behaviour, unchanged: the Klems XML for the 3-phase `T` term, sealed as a `tmx` copy.
#: `"direct_sun"` is new: the tensor-tree XML for the five-phase method's `Cds` term, registered as a
#: plain `bsdf` input and never sealed. See the module docstring's "`assign_bsdf` binds a pairing"
#: section for why each earns its own basis check and its own register row.
BsdfRole = Literal["matrix", "direct_sun"]

#: LM-83's two-value closing-class vocabulary that `closing_role` (below) validates against.
#: Lives in `rad_core.blinds`, not here (ruling R135): Task 2's `closing_order` needs the identical
#: set to rank a group by its declared class, and `rad_core` may not import from `rad_mcp` to reach
#: a copy defined at this layer. Imported rather than retyped -- see that module's own comment on
#: the constant for the full reasoning, including why the wire argument is `closing_role` and not
#: `role`.

#: `remove` deregisters a window-group input. Named in the tool description, the same rule
#: `sensor_grid`'s own constant states: *"any tool with a destructive action is annotated
#: destructive and its description names which actions those are"*
#: (docs/tool-surface.md#annotations-are-coarser-than-the-surface). `tests/test_protocol.py` reads
#: this constant rather than a list of its own.
DESTRUCTIVE_ACTIONS: tuple[str, ...] = ("remove",)

#: Which registered input ids this tool owns -- one per `window_group(name=...)`, so a project
#: holding two groups gets two records rather than one overwriting the other. Same convention as
#: `bsdf_tool._INPUT_PREFIX` and `geometry_tool._INPUT_PREFIX`. Defined once in `rad_engine.chain`,
#: whose manifest readers strip it too (ruling R30), and aliased here.
_INPUT_PREFIX = WINDOW_GROUP_INPUT_PREFIX

#: A window-group name's legal charset -- **one shared rule, `rad_mcp.names`**, aliased rather than
#: recompiled (ruling R61; `tests/test_names.py` fails a fourth copy of this regex).
_NAME_PATTERN = NAME_PATTERN

#: `geometry(role="exterior")`'s own role string, as `_assemble` in `geometry_tool.py` records it
#: under `registered_by.arguments["role"]`. Not a `MaterialRole` member -- `geometry`'s five-value
#: role vocabulary is a different, assembly-time classification (ruling R32) -- so this is matched
#: as a plain string against the recorded tool call, the same way `geometry_tool._ground_modifiers`
#: reads a materials input's own recorded role.
_EXTERIOR_ROLE = "exterior"

#: Where the scratch octree `sample_sky_visibility` traces against is written, inside the project
#: root -- never registered, never an artifact. The convention `rad_mcp.point_in_time_tool`'s
#: `_point_in_time.oct` followed until R730 (2026-09-27) moved it to `06_octrees/` and registered
#: it: a fixed literal name this tool owns and overwrites on every call that needs it.
_OBSTRUCTION_OCTREE_NAME = "_window_group_obstruction.oct"

#: How long the scratch octree build and the sky-visibility rtrace calls may run before this tool
#: gives up. `WINDOW_GROUP_OBSTRUCTION_VARIES` is a `warn`-severity finding, not the reason the
#: caller made this call, so it must not be allowed to block `define` indefinitely -- 60 s is far
#: above what a handful of `rtrace -oL` first-intersection queries cost (`sample_sky_visibility`'s
#: own docstring: no ambient calculation at all) and far below the 300 s bound the geometry-import
#: path uses for a full scene assembly.
_OBSTRUCTION_TIMEOUT_S = 60.0

#: LM-83's grouping tolerance -- see `rad_core.apertures.azimuth_spread_deg`'s own docstring for why
#: the module returns a number and the tool layer decides. Not sourced further than the register row
#: itself (docs/tool-surface.md's `WINDOW_GROUP_AZIMUTH_SPREAD` entry: "beyond LM-83's grouping
#: tolerance").
_AZIMUTH_SPREAD_THRESHOLD_DEG = 7.5


# --- nested shapes -------------------------------------------------------------------------------


class _Nested(BaseModel):
    model_config = ConfigDict(extra="forbid")


# The module docstring says why a `role="direct_sun"` binding is not sealed; the pointer to it left
# the published description under R809 (item 38). R810 moved out the ruling (R62: the sealed copy)
# and the task (10a: the direct-sun role), and added `artifact_id`'s form and the `null` basis
# (`_assign_bsdf`: `artifact_id = f"tmx:{name}:{state}"`, `basis=parsed.basis_name`).
class StateView(_Nested):
    """One BSDF state bound by `window_group(action="assign_bsdf")`.

    `path`, `basis` and `artifact_id` are a `role="matrix"` binding: `path` is the **sealed copy**
    in `07_matrices/`, not your source XML, so a later edit to the source is detectable drift, not
    a silent substitution. `artifact_id` is the sealed `tmx:<group>:<state>` artifact.

    `direct_sun_path`, `direct_sun_basis` and `direct_sun_input_id` are a `role="direct_sun"`
    binding, a second and independent binding on the same state; a 5-phase state needs both.
    `direct_sun_path` is your own source path: nothing is copied or sealed for this role.

    `basis` and `direct_sun_basis` are the file's declared basis name (`AngleBasisName`), `null`
    when it declares none. Each call reports only the role it bound: the other three fields are
    `null` even when an earlier call bound that role on the same state.

    `open_diffuse_transmittance` and `diffuse_transmittance` are set only on a
    `states={"closed": "lm83_default"}` binding: the open state's visible diffuse-diffuse
    transmittance and the composed closed state's, 5 % of it. `null` on every other binding.
    """

    path: str | None = None
    basis: str | None = None
    artifact_id: str | None = None
    direct_sun_path: str | None = None
    direct_sun_basis: str | None = None
    direct_sun_input_id: str | None = None
    open_diffuse_transmittance: float | None = None
    diffuse_transmittance: float | None = None


class WindowGroupInputView(_Nested):
    """One defined window group in `window_group(action="list")`, from the project record.

    `id` is the group's input id, `window_group_<name>`. `files` are every geometry file registered
    when the group was defined, not only those holding its polygons.
    `closing_role` is the role `define` declared, `null` when none was. `automated` is whether
    `define` marked the group's blinds automated (`false` for a group defined without the mark);
    `schedule` is the project's copy of the hourly schedule `bind_schedule` bound, `null` when none
    is bound.
    """

    id: str
    files: list[str]
    closing_role: str | None = None
    automated: bool = False
    schedule: str | None = None


# --- the result branches --------------------------------------------------------------------------


class WindowGroupBase(RadResult):
    """What every window-group-shaped answer carries. Also the union schema's shared base."""

    project: str
    root: str


# `states`/`default_state` were published before any action bound a BSDF (before Task 7 shipped),
# because a client reading this tool's schema early must already see the shape it will fill in -- an
# output schema that grows a field later is the same "state-dependent shape" problem
# `envelope.one_of_schema` exists to avoid. `closing_role` (Phase 7d-1 Task 1) is the same rule
# applied to that task's own new argument, published before Task 2's schedule read it. This history
# left the published description under R809 (item 38). R810 moved out the §6 pointer and the reason
# `closing_role` is echoed (confirm it stuck without a second `list`), and added what the geometry
# fields hold (`_define`: `derive(members)`, `klems_bins=145`) and that `dry_run=True` returns this
# shape with nothing registered (`if not dry_run:` guards `register_input_and_set_role`).
class WindowGroupDefined(WindowGroupBase):
    """The aperture `window_group(action="define")` derived from the named polygons.

    `polygons` counts the polygons whose modifier `aperture` named, `area_m2` is their total area,
    and `outward_normal`, `azimuth_deg` and `tilt_deg` describe their common plane.
    `klems_bin_expression` is the `kbin(...)` expression the V and D matrices are binned by;
    `klems_bins` is always 145. With `dry_run=true` the same result is returned but nothing is
    registered: `input_id` is the id the group would be registered under.

    `states` is always empty and `default_state` always `null` here: a BSDF is bound later, by
    `action="assign_bsdf"`. `closing_role` echoes what the call declared, `null` if nothing; the
    blinds schedule reads it to decide which groups close first. `automated` echoes the mark this
    call recorded: a redefine that omits `automated` records and reports `false`.
    """

    name: str
    polygons: int
    area_m2: float
    outward_normal: list[float]
    azimuth_deg: float
    tilt_deg: float
    klems_bin_expression: str
    klems_bins: int
    states: dict[str, StateView] = Field(default_factory=dict)
    default_state: str | None = None
    input_id: str
    closing_role: str | None = None
    automated: bool = False


class WindowGroupListed(WindowGroupBase):
    """`window_group(action="list")`: every window group defined in the project."""

    window_groups: list[WindowGroupInputView]


class WindowGroupRemoved(WindowGroupBase):
    """`window_group(action="remove")`: the group's record is deregistered.

    `removed` lists the input ids removed, empty when no group had that name (not refused). The
    geometry the group pointed at is untouched. Removing a group an artifact was built from (such as
    the `tmx` a bound BSDF state sealed) is refused (`ARTIFACT_EDGES_INCOMPLETE`), and nothing is
    changed.
    """

    removed: list[str]


# §6's `states`/`default_state` block, now populated. A group with one state is static fenestration;
# two or more is dynamic and triggers the schedule machinery in `run_annual` (§6, citing
# sda-ase-lm83.md §"Applying the Schedule"); this action records that shape and does not act on it.
# R810 moved that out (the `states` argument's description says it) and added that both fields are
# this call's own and that `default_state` is not recorded: nothing outside this module reads it,
# and `_assign_bsdf` registers no argument naming it (`rad_engine.chain.states_by_group` derives a
# group's states from its `tmx` artifact ids).
class WindowGroupAssigned(WindowGroupBase):
    """The BSDF states `window_group(action="assign_bsdf")` bound to group `name`.

    `states` holds only the states this call bound, under its `role`, not every state the group
    has. `default_state` is the `default_state` given, else the one state when this call bound
    only one, else `null`. It is reported for this call only: the project does not record it.
    """

    name: str
    states: dict[str, StateView]
    default_state: str | None


class WindowGroupScheduleBound(WindowGroupBase):
    """`window_group(action="bind_schedule")`: an automated group's hourly blind schedule, bound.

    `path` is the project's own copy of your CSV; a later edit to your file is not read. `input_id`
    is the record it is registered under, `automated_schedule_<name>`. `hours` counts its rows (8760
    or 8784), `hours_closed` those reading `closed`, and `header` whether its first line was
    `state`. Binding again replaces the copy and the record.
    """

    name: str
    input_id: str
    path: str
    hours: int
    hours_closed: int
    header: bool


WindowGroupPayload = (
    WindowGroupDefined
    | WindowGroupListed
    | WindowGroupRemoved
    | WindowGroupAssigned
    | WindowGroupScheduleBound
    | Refusal
)


class WindowGroupResult(RootModel[WindowGroupPayload]):
    """`window_group`'s result: one shape per `action` (`define`, `list`, `remove`,
    `assign_bsdf`, `bind_schedule`), or a refusal."""

    @classmethod
    def model_json_schema(cls, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return one_of_schema(super().model_json_schema(*args, **kwargs), WindowGroupBase)


# --- argument checks -----------------------------------------------------------------------------


def _refuse_argument(message: str, *, field: str) -> CallToolResult:
    """`ARGUMENT_OUT_OF_RANGE` with the argument's own name in `field` -- ruling R14's code for an
    argument refused on its face."""
    return refuse(
        message,
        [Advisory(code="ARGUMENT_OUT_OF_RANGE", severity="error", field=field, message=message)],
    )


def _refuse_name(name: str) -> CallToolResult | None:
    if is_legal_name(name):
        return None
    return _refuse_argument(
        name_refusal_reason(
            name, kind="window group", examples="'main', 'south-facade', 'skylight.1'"
        ),
        field="name",
    )


def _refuse_closing_role(closing_role: str) -> CallToolResult | None:
    """`closing_role must be 'view' or 'clerestory'` -- checked above `dry_run` and above every
    Radiance call `_define` makes (ruling R39, the same posture `_refuse_name` and the unresolved-
    aperture check both take): a third name would be ordered against nothing, and that is knowable
    from the string alone, so nothing about resolving `aperture` or deriving geometry has to run
    first to find it out."""
    if closing_role in CLOSING_ROLES:
        return None
    return _refuse_argument(
        f"closing_role must be 'view' or 'clerestory', not {closing_role!r}. These are LM-83 "
        "section 2.2.6's two closing classes: the DISCUSSION says it is advisable to close view "
        "groups first and clerestory last. A third name would be ordered against nothing.",
        field="closing_role",
    )


# --- the tool --------------------------------------------------------------------------------------


def window_group(
    action: Annotated[
        Action,
        Field(
            description=(
                "\"define\" derives one aperture from `aperture`'s polygons and registers it. "
                "\"assign_bsdf\" binds BSDF XMLs to that group's states. \"list\" reports a "
                "project's registered groups. \"remove\" deregisters one by name. "
                "\"bind_schedule\" binds an automated group's hourly schedule."
            )
        ),
    ],
    project: Annotated[
        str | None, Field(description="the project handle. Required for every action.")
    ] = None,
    name: Annotated[
        str | None,
        Field(
            description=(
                "the group's handle inside the project. REQUIRED on \"define\", \"assign_bsdf\", "
                "\"bind_schedule\" and \"remove\". 1-64 characters of ASCII letters, digits, "
                "underscore, hyphen and dot."
            )
        ),
    ] = None,
    aperture: Annotated[
        list[str] | str | None,
        Field(
            description=(
                "REQUIRED on \"define\" -- modifier names whose polygons, in the project's already "
                "registered geometry, form this group. Every name must resolve to at least one "
                "polygon actually used by a registered `geometry` input: a name that resolves to "
                "nothing would build a V matrix of all zeros that multiplies to a "
                "plausible-looking zero answer, so this is refused rather than silently accepted, "
                "and checked before dry_run so paying for a trace is never required to find out."
            )
        ),
    ] = None,
    closing_role: Annotated[
        str | None,
        Field(
            description=(
                "which closing class this group belongs to for LM-83 section 2.2.6's blind "
                "schedule -- \"view\" or \"clerestory\", checked only on \"define\". Omit it and "
                "it stores None -- and an undeclared group is NOT inert. LM-83's blind schedule "
                "closes groups in a closing order, and with two or more groups, if ANY group's "
                "closing_role is neither class the WHOLE order is substituted by sill height, "
                "lowest aperture first. Ordering is what decides which groups close, and which "
                "groups close decides the blind-aware sDA, so declare it on every group of a project "
                "whose sDA will be read with blind_control='lm83'."
            )
        ),
    ] = None,
    states: Annotated[
        dict[str, str] | None,
        Field(
            description=(
                "REQUIRED on \"assign_bsdf\" -- state name -> BSDF XML path. One state is static "
                "fenestration; two or more is dynamic and triggers the schedule machinery in "
                "run_annual. Which basis a bound XML must be on depends on `role`, below. At "
                "`role=\"matrix\"` each XML is **copied** into 07_matrices/ and sealed as a tmx "
                "artifact, so a later edit to your file is detectable drift, not a silent "
                "substitution; at `role=\"direct_sun\"` it is registered where it sits. "
                "`{\"closed\": \"lm83_default\"}` composes LM-83's default 5 % diffuse blind "
                "behind the group's bound open state, which must be bound first."
            )
        ),
    ] = None,
    default_state: Annotated[
        str | None,
        Field(
            description=(
                "read only by \"assign_bsdf\", which echoes it as `default_state` in its "
                "result: the project does not record it and no run reads it, so it does not "
                "choose the state any run uses. Must name one of `states`. Omitted, the result "
                "reports the one state this call bound, or null when it bound two or more -- "
                "which state is \"normal\" is a fact about the building."
            )
        ),
    ] = None,
    role: Annotated[
        BsdfRole,
        Field(
            description=(
                "which chain `states` is being bound for on \"assign_bsdf\". \"matrix\" (the "
                "default) is the 3-phase `T` term: each XML must be on the 145-direction full "
                "Klems basis the V and D matrices are binned into -- a tensor-tree file is refused "
                "(it does not work in the matrix formulation at all) and so is a Klems file on any "
                "other basis. \"direct_sun\" is the five-phase method's `Cds` term: each XML must "
                "be a tensor tree -- a Klems file is refused, the reverse of the \"matrix\" "
                "refusal. Binding one role never replaces what the other role already bound on "
                "the same state; a 5-phase state needs both."
            )
        ),
    ] = "matrix",
    automated: Annotated[
        bool,
        Field(
            description=(
                "read only on \"define\": true marks this group's blinds as automated. Its "
                "sunlight then leaves ASE_net (one extra sun trace with its apertures blacked) and "
                "the LM-83 blind schedule takes its hourly state from a schedule bound with "
                "\"bind_schedule\", or holds it closed when none is bound. A redefine that omits "
                "it sets it false; the result says which."
            )
        ),
    ] = False,
    schedule: Annotated[
        str | None,
        Field(
            description=(
                "REQUIRED on \"bind_schedule\": a CSV inside an allowed root, one row per hour "
                "of the weather file in its own row order, each open or closed (surrounding "
                "whitespace ignored), an optional first line state; 8 760 or 8 784 rows. The "
                "group must be marked automated. The file is copied into the project, so a later "
                "edit to yours is not read."
            )
        ),
    ] = None,
    dry_run: Annotated[
        bool,
        Field(
            description=(
                "report what \"define\" would compute -- the derived aperture, its advisories, and "
                "every refusal that does not require registering anything -- without writing to "
                "the manifest. `aperture`'s registered-geometry refusal fires identically with "
                "dry_run=True or False. \"assign_bsdf\", \"bind_schedule\" and \"remove\" have no "
                "dry run: each refuses dry_run=True once every check of its own has passed, so a "
                "dry run gets the refusal the real call would. \"list\", and a \"remove\" that "
                "matches nothing, write nothing either way."
            )
        ),
    ] = False,
) -> WindowGroupResult:
    """Declare which surfaces are apertures, and group them the way LM-83 groups a window group.

    Use it after `geometry` has registered the scene, before any run that reads window groups
    (the 3- and 5-phase chains, `run_view_and_sunlight`, `run_glare`'s `dgp_window_group`); check
    a BSDF file with `bsdf` before "assign_bsdf" binds it.

    This is the tool the 3-phase method exists for: `rad_core.apertures` derives the outward
    normal, the plane and the Klems up-vector from the polygons you name, and `rad_engine.klems`
    turns that into the `kbin(Nx,Ny,Nz,Ux,Uy,Uz)` expression the V and D matrices are binned by --
    always the explicit six-argument form, never a cardinal alias (`kbinS`/`kbinN`/...), because a
    façade at an arbitrary azimuth has no correct alias and picking the nearest one silently rotates
    the Klems basis against whatever BSDF is bound to it later.

    This build supports "define", "list", "assign_bsdf", "bind_schedule" and "remove".
    "make_secondary_source" (the `mkillum` route) is not built and is refused with a reason that
    says so. "define" with `automated=true` marks a group's blinds automated (LM-83 ASE_net and its
    blind schedule read the mark), "bind_schedule" binds such a group's hourly open/closed CSV, and
    "assign_bsdf" with `states={"closed": "lm83_default"}` composes LM-83's default 5 % diffuse
    closed state from the group's bound open state.

    DESTRUCTIVE ACTIONS: `remove` deregisters a window-group input from a project. It never touches
    the geometry files the group pointed at.

    **If any group's `closing_role` is not "view" or "clerestory", LM-83's whole closing order is
    substituted by sill height.** That substitution is published -- `order` and
    `order_substituted` on `results(action='blind_schedule')` -- and `BLIND_ORDER_SUBSTITUTED`
    (warn) names the repair: declare each group's closing_role and re-run run_annual. One group
    alone is never a substitution, whatever its role: a one-element order is total whatever decided
    it. LM-83 section 2.2.6's DISCUSSION says it is advisable to close view groups first and
    clerestory last, and nothing in the geometry distinguishes them, so `closing_role` is a caller
    declaration rather than an inference. `closing_role` is not the same argument as `role`: that
    is `assign_bsdf`'s own, owner-ruled BSDF-chain selector (ruling R63), and this is a different
    vocabulary the two happen to share a tool with -- see `rad_core.blinds.CLOSING_ROLES`'s own
    comment for why the two are not merged onto one name.
    """
    runtime = current()
    try:
        return _dispatch(
            runtime,
            action,
            project=project,
            name=name,
            aperture=aperture,
            closing_role=closing_role,
            states=states,
            default_state=default_state,
            role=role,
            automated=automated,
            schedule=schedule,
            dry_run=dry_run,
        )
    except BSDFError as exc:
        # A file that is not a well-formed BSDF, or one whose own statements about its basis
        # disagree. No allocated code covers "this XML is not parseable as a BSDF" -- §9 records
        # the same gap for a file `checkBSDF` rejects, and the posture there is this one: a
        # condition with no allocated code carries its reason as prose rather than borrowing a
        # code that means something else.
        logger.info("window_group(action=%s) refused: %s", action, exc)
        return refuse(str(exc))
    except ApertureError as exc:
        logger.info("window_group(action=%s) refused: %s", action, exc)
        code = exc.code
        if code is None:
            return refuse(str(exc))
        return refuse(
            str(exc),
            [Advisory(code=code, severity=exc.severity, field="aperture", message=str(exc))],
        )
    except RadianceNotFound as exc:
        logger.info("window_group(action=%s) refused: %s", action, exc)
        return refuse(
            str(exc),
            [Advisory(code="RADIANCE_NOT_FOUND", severity="error", message=str(exc))],
        )
    except SceneInlineCommandError as exc:
        # Rulings R33/R753 (final review): a registered scene file carrying a hostile `!` line, or
        # an include tree too large to verify, is refused before anything reads it. Shaped with its
        # own allocated code, as `annual_tool` shapes it, rather than escaping as an uncoded
        # ToolError carrying an absolute path (check 0.4).
        logger.info("window_group(action=%s) refused: %s", action, exc)
        return refuse(
            str(exc),
            [Advisory(
                code=getattr(exc, "code", "SCENE_INLINE_COMMAND"), severity="error",
                field="project", message=str(exc),
            )],
        )
    except RadianceError as exc:
        # A binary ran and failed while resolving the exterior octree for the obstruction check.
        # No allocated code for "a Radiance binary failed" -- the register has none, and this is the
        # posture `rad_mcp.bsdf_tool` and `rad_engine.weather.WeatherFileUnreadableError` both take:
        # the failure is the binary's own stderr rather than a condition this server classified.
        logger.info("window_group(action=%s) failed: %s", action, exc)
        return refuse(str(exc))
    except InputStillBuiltFrom as exc:
        # `remove` on a group a sealed `tmx` still names in `built_from` (Phase 6b Task 6, ruling 6
        # -- see `_removed`). `ARTIFACT_EDGES_INCOMPLETE` is the exception's own allocated code
        # (ruling R6), carried by the class rather than chosen here, and `field="name"` names the
        # argument that selects the group -- `weather_tool`'s identical clause takes both decisions
        # the same way for the same exception.
        #
        # Placed **above** the `ProjectError` clause deliberately, and the ordering is not merely
        # stylistic: `InputStillBuiltFrom` is a `ValueError`, disjoint from `ProjectError`, so
        # without its own clause it would fall through every clause here to the bare `OSError` one
        # (which it is not) and escape as an uncoded `ToolError` -- check 0.4 and 0.5 together, the
        # shape this module's `SceneInlineCommandError` handling already exists to prevent
        # elsewhere.
        logger.info("window_group(action=%s) refused: %s", action, exc)
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
        logger.info("window_group(action=%s) refused: %s", action, exc)
        return refuse_engine_error(exc)
    except OSError as exc:
        logger.warning("window_group(action=%s) failed on the filesystem: %s", action, exc)
        return refuse(
            f"window_group(action={action!r}) could not complete: {exc}. Nothing about the "
            "arguments is wrong -- the filesystem refused the operation."
        )


def _refuse_no_dry_run(action: str, effect: str, after: str = "") -> CallToolResult:
    """`dry_run=True` on "assign_bsdf", "bind_schedule" or "remove", which have no dry run
    (final review W-M5; final fix D3 and its fix rounds 1 and 2). Each used to ignore `dry_run`,
    so a dry run wrote or removed.

    **Called at the last point before the action's first write**, inside its lock and after every
    refusal the action makes of its own (fix round 2): so a dry run gets the refusal the real call
    would, `effect` can say what "this call would" do -- the ids it would register or deregister --
    and dropping `dry_run`, the remedy, runs. A state that writes nothing (a `remove` that
    matches nothing) never reaches here: it is answered, not refused. `after`, when given,
    continues the remedy's sentence in place of its full stop.
    """
    return _refuse_argument(
        f"dry_run is read only on action='define'; action={action!r} has no dry run: {effect} "
        f"Drop dry_run from this call to run it{after or '.'}",
        field="dry_run",
    )


def _ids_in_prose(ids: Sequence[str]) -> str:
    """`a`, `a and b`, `a, b and c` -- the ids a `_refuse_no_dry_run` effect names."""
    return ids[0] if len(ids) == 1 else f"{', '.join(ids[:-1])} and {ids[-1]}"


def _matrices_outside_root(target: Path, state: str) -> PathOutsideRoot:
    return PathOutsideRoot(
        f"the project's matrices directory {target.parent} resolves outside every allowed root, "
        f"so the BSDF copy for state {state!r} was not written."
    )


def _refuse_occupied(path: Path, *, what: str) -> CallToolResult | None:
    """A refusal when something other than a regular file is at `path`, a name this call is about
    to write (`refuse_existing_non_regular_destination`'s `lstat`, which opens nothing), else
    `None`. Final fix D, fix round 3: run for every destination **before the first write**, so a
    binding refused for its last state leaves the project as it was, rather than at the write,
    after earlier states were committed. Carries no code, as `window_group`'s filesystem
    refusals do not (docs/tool-surface.md, `PROJECT_PATH_NOT_A_REGULAR_FILE`)."""
    try:
        refuse_existing_non_regular_destination(path)
    except NotARegularFileError:
        return refuse(
            f"{what} cannot be written at {path}: something that is not a regular file is there "
            "(a directory, a symbolic link or a FIFO, for example), and this server will not "
            "write through it. Nothing was written. Remove whatever occupies that path and re-run."
        )
    return None


def _dispatch(
    runtime: Runtime,
    action: Action,
    *,
    project: str | None,
    name: str | None,
    aperture: list[str] | str | None,
    closing_role: str | None,
    states: dict[str, str] | None,
    default_state: str | None,
    role: BsdfRole,
    automated: bool,
    schedule: str | None,
    dry_run: bool,
) -> CallToolResult:
    call = f"window_group(action={action!r})"
    if (refusal := refuse_missing(call, project=project)) is not None:
        return refusal
    assert project is not None

    if action == "make_secondary_source":
        return _refuse_not_built(
            action,
            "make_secondary_source is not built: it would register an illum:<group> artifact, and "
            "'illum' is not a member of this server's ArtifactKind, so building it needs a "
            "manifest schema change and a consumer (run_render, run_point_in_time) that reads it; "
            "neither exists.",
        )

    loaded = runtime.open(project)

    # R869/R870: a mark or a schedule sent with an action that does not read it is a caller who
    # believes it took effect, so it is refused rather than ignored (stricter than `closing_role`,
    # which other actions ignore). `automated=False` is the default and cannot be told apart from
    # an omitted argument, so only `True` is refused. Checked after the project opens so the
    # redefine the refusal spells is read from the group's own record.
    # Fix round 3: a call holding several faults this function can see is told about each of them
    # in its first refusal: a wrong `schedule` beside a wrong `automated`, `dry_run=True` on an
    # action that has no dry run (which, once every check of its own passes, refuses it), and
    # (fix round 4, review N1) a `name` that is no defined group, on the two actions that refuse
    # one. It cannot see every refusal the action makes of its own (a `remove` of a group a sealed
    # `tmx` is built from, for one), so it claims only that each named fault is a fault, not that
    # the call with all of them dropped runs.
    schedule_wrong = schedule is not None and action != "bind_schedule"
    drop_dry_run = (
        f" Drop dry_run too: action={action!r} has no dry run."
        if dry_run and action in ("assign_bsdf", "bind_schedule", "remove")
        else ""
    )

    def undefined(record: Any) -> str:
        """The undefined-group sentence, where `action` refuses a group that is not defined."""
        if name is None or record is not None or action not in ("assign_bsdf", "bind_schedule"):
            return ""
        # Final fix E5 (part D round-5 re-review N2): `_refuse_name` runs after this refusal, so a
        # define spelled with an illegal name would be refused by `define` for that name. Name
        # the name's own fault instead, and spell no define for it.
        if not is_legal_name(name):
            return " The name is refused too: " + name_refusal_reason(
                name, kind="window group", examples="'main', 'south-facade', 'skylight.1'"
            )
        known = sorted(
            _group_name(each) for each in loaded.manifest.inputs if each.kind == "window_group"
        )
        # Fix round 5 (review M4): `bind_schedule` binds only to a group marked automated, so its
        # define call carries the mark; one without it would be refused as unmarked.
        define = (
            f"window_group(action='define', project={project!r}, name={name!r}, "
            "aperture=[<its glazing modifiers>], automated=True)"
            if action == "bind_schedule"
            else "window_group(action='define', ...)"
        )
        return (
            f" No window group named {name!r} is defined in this project, and action={action!r} "
            f"refuses a group that is not: define it first with {define}. "
            + (f"Defined groups: {', '.join(known)}." if known else "This project has none yet.")
        )

    if automated and action != "define":
        record = _group_record(loaded, name) if name is not None else None
        drop_schedule = (
            " Drop schedule too: schedule is read only on action='bind_schedule'."
            if schedule_wrong
            else ""
        )
        redefine = (
            f" To mark group {name!r} automated, redefine it: "
            f"{_redefine_call(project, name, record, automated=True)}."
            if name is not None and record is not None and not _is_automated(record)
            else ""
        )
        return _refuse_argument(
            f"automated is read only on action='define'; this call is action={action!r}, which "
            f"would not record it. Drop automated from this call.{drop_schedule}{drop_dry_run}"
            f"{undefined(record)}{redefine}",
            field="automated",
        )
    if schedule_wrong:
        record = _group_record(loaded, name) if name is not None else None
        retry = (
            f" To bind it, call window_group(action='bind_schedule', project={project!r}, "
            f"name={name!r}, schedule={schedule!r})."
            if record is not None and _is_automated(record)
            else ""
        )
        return _refuse_argument(
            f"schedule is read only on action='bind_schedule'; this call is action={action!r}, "
            f"which would not read it. Drop schedule from this call.{drop_dry_run}"
            f"{undefined(record)}{retry}",
            field="schedule",
        )

    if action == "list":
        return _listed(loaded)

    if action == "assign_bsdf":
        if (refusal := refuse_missing(call, name=name, states=states)) is not None:
            return refusal
        assert name is not None and states is not None
        if (refusal := _refuse_name(name)) is not None:
            return refusal
        return _assign_bsdf(
            runtime, loaded, name=name, states=states, default_state=default_state, role=role,
            dry_run=dry_run,
        )

    if action == "bind_schedule":
        if (refusal := refuse_missing(call, name=name, schedule=schedule)) is not None:
            return refusal
        assert name is not None and schedule is not None
        if (refusal := _refuse_name(name)) is not None:
            return refusal
        return _bind_schedule(
            runtime, loaded, project=project, name=name, schedule=schedule, dry_run=dry_run
        )

    if action == "remove":
        if (refusal := refuse_missing(call, name=name)) is not None:
            return refusal
        assert name is not None
        return _removed(runtime, loaded, project=project, name=name, dry_run=dry_run)

    assert action == "define"
    if (refusal := refuse_missing(call, name=name, aperture=aperture)) is not None:
        return refusal
    assert name is not None and aperture is not None

    if (refusal := _refuse_name(name)) is not None:
        return refusal

    if closing_role is not None and (refusal := _refuse_closing_role(closing_role)) is not None:
        return refusal

    return _define(
        runtime, loaded, name=name, aperture=aperture, closing_role=closing_role,
        automated=automated, dry_run=dry_run,
    )


def _recorded_aperture(record: Any) -> list[str] | None:
    """The aperture modifiers group `record`'s own `define` call recorded, as a list, or `None`
    when the record carries none this server reads: no recorded call, or an `aperture` that is
    neither a modifier name nor a non-empty list of them (a hand-edited manifest).

    `define` records `list(names)`, a list even when the caller passed one name (`define`
    accepts `list[str] | str`; `_aperture_names` makes a one-item tuple of it), and every version
    of this module since `73903c5`, its first, has recorded it that way (final fix D4, measured
    over the module's history). So no manifest this server wrote holds a string here: the string
    branch guards a hand-edited manifest only, and makes its name a one-item list, never a list of
    its characters."""
    if record is None or record.registered_by is None:
        return None
    recorded = record.registered_by.arguments.get("aperture")
    if isinstance(recorded, str) and recorded:
        return [recorded]
    if (
        isinstance(recorded, list) and recorded
        and all(isinstance(name, str) and name for name in recorded)
    ):
        return list(recorded)
    return None


#: `_redefine_call`'s default for `closing_role`: keep the role the record declares.
_RECORDED_ROLE: Final[object] = object()


def _redefine_call(
    project: str, name: str, record: Any, *, automated: bool,
    aperture: Sequence[str] | None = None, closing_role: object = _RECORDED_ROLE,
) -> str:
    """The whole `define` call that re-registers group `name` from its own record with the given
    mark: `aperture` from the recorded arguments and `closing_role` from `InputRecord.role`, the
    latter omitted only when the record has none -- a redefine that omits it clears it (R133).
    `aperture`, when given, replaces the recorded modifiers (`run_annual`'s shared-modifier
    refusal spells a group's redefine with the shared one left out); `closing_role`, when given,
    is the role the call declares instead of the recorded one (`results`' `BLIND_ORDER_SUBSTITUTED`
    spells a role for a group that declares none).

    **A record carrying no aperture this server reads spells `aperture=...`** (final fix A, fix
    round 3), never `aperture=[]`, which `define` refuses as an empty list, and the call is
    followed by a parenthesis saying what to pass there, so every caller's remedy says it."""
    if aperture is None:
        recorded = _recorded_aperture(record)
        spelled = "..." if recorded is None else repr(recorded)
    else:
        spelled = repr(list(aperture))
    parts = [
        "action='define'", f"project={project!r}", f"name={name!r}", f"aperture={spelled}"
    ]
    role = getattr(record, "role", None) if closing_role is _RECORDED_ROLE else closing_role
    if role is not None:
        parts.append(f"closing_role={role!r}")
    parts.append(f"automated={automated!r}")
    call = f"window_group({', '.join(parts)})"
    if spelled == "...":
        call += (
            f" (the record of window group {name!r} carries no aperture this server reads, so "
            "pass as aperture the group's aperture modifiers)"
        )
    return call


def _refuse_not_built(action: str, reason: str) -> CallToolResult:
    return refuse(f"window_group(action={action!r}) is not built by this server. {reason}")


# --- define ------------------------------------------------------------------------------------


def _aperture_names(aperture: list[str] | str) -> tuple[str, ...]:
    """`aperture` as a tuple of modifier names.

    `aperture` also accepts a `.rad` file path per §6's signature (`list[str] | str`), but nothing
    in this phase resolves a bare file path against a modifier -- a `.rad` file names its own
    geometry directly and is not itself a modifier name to look up. That branch of the signature is
    reported rather than guessed at, the same posture this module takes for
    `assign_bsdf`/`make_secondary_source`: refusing plainly rather than inventing a resolution rule
    with no spec behind it.
    """
    return (aperture,) if isinstance(aperture, str) else tuple(aperture)


def _geometry_inputs(loaded: LoadedProject) -> list[Any]:
    return [record for record in loaded.manifest.inputs if record.kind == "geometry"]


def _registered_polygons(
    loaded: LoadedProject, env: RadianceEnv, cwd: Path
) -> tuple[Polygon, ...]:
    """Every polygon any registered `geometry` input actually uses, read back through
    `rad_engine.scene.read_scene`.

    The same route `rad_mcp.geometry_tool._assemble` takes for `Polygon.modifier` values: there is
    no "what does this file declare" accessor at the engine layer, only "what do its polygons use".
    Reads every geometry input's files together in one `read_scene` call -- an aperture's members
    can legitimately come from more than one registered geometry input (an interior scene and a
    separate shading scene, say), and a name resolved against only one input at a time would refuse
    a legitimate cross-input group.
    """
    files: list[str] = []
    for record in _geometry_inputs(loaded):
        files.extend(ref.path for ref in record.files)
    if not files:
        return ()
    parsed = read_scene(env, files, cwd=cwd, timeout=_OBSTRUCTION_TIMEOUT_S)
    return parsed.polygons


def _exterior_geometry_files(loaded: LoadedProject) -> list[str]:
    """Files of every `geometry` input registered with `role="exterior"` (`geometry_tool`'s own
    assembly-time vocabulary, ruling R32 -- not a `MaterialRole`). Empty when none is registered,
    which is the caller's signal to skip the obstruction check rather than guess at a scene."""
    files: list[str] = []
    for record in _geometry_inputs(loaded):
        call = record.registered_by
        if call is not None and call.arguments.get("role") == _EXTERIOR_ROLE:
            files.extend(ref.path for ref in record.files)
    return files


def _define(
    runtime: Runtime,
    loaded: LoadedProject,
    *,
    name: str,
    aperture: list[str] | str,
    closing_role: str | None,
    automated: bool,
    dry_run: bool,
) -> CallToolResult:
    paths = ProjectPaths(loaded.paths.root)
    names = _aperture_names(aperture)
    if not names:
        return _refuse_argument(
            "window_group(action='define') was given an empty aperture list, so there is no "
            "modifier to resolve against the project's registered geometry.",
            field="aperture",
        )

    # Design §5.11, amendment 4: `define` resolves aperture names against this project's registered
    # geometry before anything else, so a pre-Phase-10 unscaled-in-place record refuses here rather
    # than binding a window group to coordinates that were never scaled to metres.
    refuse_unscaled_geometry(loaded)
    env = discover()
    all_polygons = _registered_polygons(loaded, env, paths.root)
    by_modifier: dict[str, list[Polygon]] = {}
    for polygon in all_polygons:
        by_modifier.setdefault(polygon.modifier, []).append(polygon)

    members: list[Polygon] = []
    source_files: list[str] = []
    unresolved: list[str] = []
    seen_files: set[str] = set()
    for modifier in names:
        matches = by_modifier.get(modifier, [])
        if not matches:
            unresolved.append(modifier)
            continue
        members.extend(matches)

    if unresolved:
        # **The refusal that matters most (docs/tool-surface.md SS6, "Refuses").** Above dry_run,
        # by construction: this branch returns before dry_run is ever consulted below, so the check
        # is not something a caller can pay a trace to skip past (ruling R39).
        known = sorted(by_modifier)
        reason = (
            f"aperture names {unresolved!r}, which resolve to no polygon in this project's "
            "registered geometry. A group pointing at a modifier that does not exist produces an "
            "all-zero V matrix, and an all-zero matrix multiplies to a plausible zero -- so this is "
            "refused rather than registered. "
            + (f"Registered modifiers: {', '.join(known)}." if known else
               "No geometry is registered in this project yet.")
        )
        return _refuse_argument(reason, field="aperture")

    # `source_files`: every file of every registered geometry input used to resolve `aperture`'s
    # names (`_registered_polygons` reads them all together in one `read_scene` call, so a matched
    # polygon cannot be attributed to one file without re-parsing per file). Conservative rather
    # than exact -- it may include a file that did not itself contain a matched modifier -- but it
    # is still exactly the bytes this group's derivation depended on: the digest still moves
    # whenever any of them changes, which is the property the edge exists for.
    for record in _geometry_inputs(loaded):
        for ref in record.files:
            if ref.path not in seen_files:
                seen_files.add(ref.path)
                source_files.append(ref.path)

    # `ApertureNotCoplanarError`/`ApertureNormalAmbiguousError` propagate to `window_group`'s own
    # `except ApertureError` handler, which maps each error's own `.code` onto the wire.
    derived: Aperture = derive(members)

    advisories = _definition_advisories(derived, group_name=name)

    exterior_files = _exterior_geometry_files(loaded)
    if len(members) >= 2 and exterior_files:
        obstruction_advisory = _obstruction_advisory(
            env, paths, aperture=derived, exterior_files=exterior_files
        )
        if obstruction_advisory is not None:
            advisories.append(obstruction_advisory)

    bin_expr = bin_expression(derived)

    input_id = _INPUT_PREFIX + name
    if not dry_run:
        with runtime.modifying(paths):
            fresh = open_project(paths.root, radiance_version=runtime.launcher.version)
            # `closing_role` is not a `MaterialRole` member, so it cannot go through
            # `register_input`'s own `role=` parameter (typed to that enum, ruling R9's channel) --
            # `register_input_and_set_role` is the general escape hatch for a second role vocabulary
            # on the same `InputRecord.role` field (ruling R133); see that function's own docstring.
            register_input_and_set_role(
                fresh,
                input_id=input_id,
                kind="window_group",
                files=source_files,
                registered_by=ToolCall(
                    tool="window_group",
                    action="define",
                    arguments={
                        "name": name,
                        "aperture": list(names),
                        "closing_role": closing_role,
                        # R869: always written, `False` included, so a redefine that omits the
                        # mark clears it rather than leaving a stale `True` for `.get` to read.
                        "automated": automated,
                    },
                ),
                role=closing_role,
            )

    return emit(
        WindowGroupDefined(
            project=loaded.paths.name,
            root=str(loaded.paths.root),
            name=name,
            polygons=len(derived.polygons),
            area_m2=derived.area_m2,
            outward_normal=list(derived.outward_normal),
            azimuth_deg=derived.azimuth_deg,
            tilt_deg=derived.tilt_deg,
            klems_bin_expression=bin_expr,
            klems_bins=145,
            states={},
            default_state=None,
            input_id=input_id,
            closing_role=closing_role,
            automated=automated,
            advisories=advisories or None,
        )
    )


def _definition_advisories(aperture: Aperture, *, group_name: str) -> list[Advisory]:
    """Findings computed from one `define` call's own derived aperture: horizontality, always
    reachable, and azimuth spread, which is not.

    **`WINDOW_GROUP_AZIMUTH_SPREAD` is allocated and wired here, and cannot fire from this call.**
    `_define` derives exactly one `Aperture` from every polygon `aperture` resolves to, through one
    `rad_core.apertures.derive` call -- and `derive`'s own coplanarity check
    (`_COPLANAR_ANGLE_TOLERANCE_DEG = 0.5`) already refuses any member whose own normal is more
    than 0.5 degrees from the combined one, `APERTURE_NOT_COPLANAR`, before this function ever
    runs. So every aperture reaching here is a *single* derived normal -- `azimuth_spread_deg` over
    a one-element list is `0.0` by that function's own convention -- and the 7.5 degree warn
    threshold can never be crossed by construction. `azimuth_spread_deg`'s real caller is a
    comparison **across** window groups (its own test suite exercises exactly that: two separately
    `derive()`-d apertures, not two members of one), and this tool's signature has no action that
    receives more than one group at a time to compare. The call stays wired rather than deleted --
    the code is allocated, the module that computes the number is a real dependency of this tool,
    and a future action comparing groups (or a coplanarity tolerance loosened past 7.5 degrees)
    would need exactly this call site. Reported here, in writing, rather than silently unreachable:
    the same posture `rad_mcp.bsdf_tool` takes for `BSDF_BASIS_MISMATCH` staying allocated and
    unemitted (docs/tool-surface.md#advisory-code-register: a gap is reported, not invented).
    """
    advisories: list[Advisory] = []

    if is_horizontal(aperture):
        advisories.append(
            Advisory(
                code="APERTURE_IS_HORIZONTAL",
                severity="info",
                field="aperture",
                message=(
                    f"Group {group_name!r} is within 5 degrees of horizontal (tilt_deg="
                    f"{aperture.tilt_deg:.2f}). EN 17037 uses Table A.2 (95% of plane, no minimum-"
                    "target column) for horizontal openings, and DGP does not apply to them."
                ),
            )
        )

    if not is_tilt_verified(aperture):
        # Phase 6b Task 0, owner decision 2026-08-16: `warn`, not a refusal. The Klems frame is
        # correct by construction at this tilt -- `_up_vector` projects +Z onto the aperture plane,
        # so `up` is perpendicular to the normal at every orientation. What is missing is coverage:
        # no fixture asserts a tilted aperture end to end against a real BSDF, and only the vertical
        # (office fixture) and flat (`kbinD` seam test) cases are checked. Refusing would make
        # correct geometry unusable -- a sawtooth rooflight is an ordinary building -- while saying
        # nothing would let an unasserted path look exactly as settled as the office window.
        advisories.append(
            Advisory(
                code="APERTURE_TILT_UNVERIFIED",
                severity="warn",
                field="aperture",
                message=(
                    f"Group {group_name!r} is neither vertical nor flat (tilt_deg="
                    f"{aperture.tilt_deg:.2f}). Its Klems basis is computed the same way as any "
                    "other orientation and is correct by construction, but no fixture in this "
                    "project asserts a tilted aperture end to end against a real BSDF, so the "
                    "basis is unverified at this tilt. The group is defined and its expression "
                    "returned; check the reported outward_normal and tilt_deg against your intent."
                ),
            )
        )

    spread = azimuth_spread_deg([aperture])
    if spread > _AZIMUTH_SPREAD_THRESHOLD_DEG:
        advisories.append(
            Advisory(
                code="WINDOW_GROUP_AZIMUTH_SPREAD",
                severity="warn",
                field="aperture",
                message=(
                    f"The members of group {group_name!r} span {spread:.2f} degrees of azimuth, beyond "
                    f"LM-83's +/-{_AZIMUTH_SPREAD_THRESHOLD_DEG:g} degree grouping tolerance. "
                    "Consider splitting this group by facade."
                ),
            )
        )

    return advisories


def _obstruction_advisory(
    env: RadianceEnv,
    paths: ProjectPaths,
    *,
    aperture: Aperture,
    exterior_files: list[str],
) -> Advisory | None:
    """`WINDOW_GROUP_OBSTRUCTION_VARIES`, sampled from each member polygon's own sky visibility.

    One `Aperture` per member polygon (rather than the single group-wide `Aperture` `_define`
    derives) is what `sample_sky_visibility` and `obstruction_spread`/`obstruction_outliers` are
    built to compare -- `aperture.polygons` are this group's own members, and each is re-derived
    alone so its own centroid, not the group's combined one, is what gets sampled. A group of one
    polygon has nothing to spread against (`obstruction_spread`'s own convention) and is skipped
    before any octree is built.
    """
    if len(aperture.polygons) < 2:
        return None

    per_member = tuple(derive([polygon]) for polygon in aperture.polygons)

    octree_path = paths.root / _OBSTRUCTION_OCTREE_NAME
    octree = build_octree(
        env, exterior_files, out_path=octree_path, cwd=paths.root,
        timeout=_OBSTRUCTION_TIMEOUT_S,
    )
    fractions = sample_sky_visibility(
        env=env, octree=octree.path, apertures=per_member, cwd=paths.root,
        timeout=_OBSTRUCTION_TIMEOUT_S,
    )

    spread = obstruction_spread(fractions)
    if spread < OBSTRUCTION_SPREAD_THRESHOLD:
        return None

    outliers = obstruction_outliers(fractions)
    ids = [aperture.polygons[i].id for i in range(len(aperture.polygons))]
    fraction_text = ", ".join(f"{ids[i]!r}: {fractions[i]:.3f}" for i in range(len(ids)))

    if len(fractions) == 2:
        # Carried from Task 5's review: at exactly two members, both sit equidistant from the
        # pairwise median and obstruction_outliers always names both -- that is mathematically
        # forced, not a finding that both are unusual. State the spread and each member's own
        # fraction rather than singling one out as "the outlier".
        message = (
            f"Group's two members see sky visibility fractions differing by {spread:.3f} "
            f"({fraction_text}), at or above the {OBSTRUCTION_SPREAD_THRESHOLD:g} threshold. With "
            "only two members, both sit equidistant from their own median by construction, so "
            "neither is singled out as the outlier here -- consider whether one is under a "
            "local obstruction (an overhang, a neighbouring building) the other is not, and split "
            "the group if so."
        )
    else:
        outlier_ids = [ids[i] for i in outliers]
        message = (
            f"Group's members see sky visibility fractions spreading by {spread:.3f} "
            f"({fraction_text}), at or above the {OBSTRUCTION_SPREAD_THRESHOLD:g} threshold. "
            f"Furthest from the group's median: {outlier_ids!r}. A single D matrix built for the "
            "whole group is fine only where there is no significant local obstruction "
            "(three-phase-method.md appendix) -- consider splitting the named member(s) into "
            "their own group."
        )

    return Advisory(
        code="WINDOW_GROUP_OBSTRUCTION_VARIES", severity="warn", field="aperture", message=message
    )


# --- assign_bsdf ---------------------------------------------------------------------------------


#: The basis name the V and D matrices are binned into. `rad_engine.matrices` builds both against
#: `klems_full.cal` with `-bn Nkbins`, so a bound XML declaring any other Klems basis describes a
#: different set of directions than the matrices it would be multiplied with. Matched
#: case-insensitively as a substring, the same way `rad_core.bsdf._klems_bins` tests the file's own
#: self-consistency, so "LBNL/Klems Full" and "Klems full" both read as the full basis.
_KLEMS_FULL_BASIS_NAME = "klems full"

#: Ceiling on a bound BSDF XML, applied to the validation read before the parse -- **imported from
#: `rad_engine.bsdf`, where the constant now lives, rather than defined here.**
#:
#: It was defined in this module until Phase 7a Task 4's review. The reason it moved is that this is
#: no longer the only read it bounds: `rad_engine.chain.three_phase_target` reads the sealed `tmx`
#: copy of the same file class, and that module cannot import this one (CONTEXT.md, Layers). See
#: `rad_engine.bsdf.MAX_BSDF_BYTES` for the measurement behind the number and for why passing it down
#: as an argument was the wrong shape. Two ceilings that could drift apart would mean a file
#: `assign_bsdf` accepted and `run_annual` refused, or the reverse; ruling R30's one-path rule makes
#: that unexpressible, and it is one **object** here rather than one value.


def _refuse_filename_collision(
    loaded: LoadedProject,
    *,
    name: str,
    resolved: dict[str, tuple[Path, BSDFFile, Path]],
) -> CallToolResult | None:
    """Refuse a sealed filename another `tmx` artifact already owns, or that two states in this
    same call would share.

    The artifact **id** (`tmx:<group>:<state>`) is unambiguous, because `:` is outside the name
    charset. The **filename** is not: it joins the same two components with `_`, which the charset
    permits, so the id-to-filename mapping is not injective. Both directions are checked -- against
    the manifest's existing artifacts, and within this call's own map -- because a single call
    binding two colliding states would otherwise overwrite its own first write before any manifest
    record existed to compare against.
    """
    within: dict[str, str] = {}
    for state, (_source, _parsed, target) in resolved.items():
        if (previous := within.get(target.name)) is not None:
            return _refuse_argument(
                f"states {previous!r} and {state!r} of group {name!r} would both be sealed to "
                f"{target.name}. Group and state names are joined with '_' to form this filename, "
                f"and '_' is legal in both, so {name}/{previous} and {name}/{state} collide. "
                "Rename one of them.",
                field="states",
            )
        within[target.name] = state

    owned = {
        Path(artifact.path).name: artifact.id
        for artifact in loaded.manifest.artifacts
        if artifact.kind == "tmx"
    }
    for state, (_source, _parsed, target) in resolved.items():
        artifact_id = f"tmx:{name}:{state}"
        existing = owned.get(target.name)
        if existing is not None and existing != artifact_id:
            return _refuse_argument(
                f"state {state!r} of group {name!r} would be sealed to {target.name}, which "
                f"artifact {existing!r} already owns. Group and state names are joined with '_' to "
                "form this filename, and '_' is legal in both, so two different bindings can name "
                "one file -- writing it would replace another group's sealed BSDF with bytes it "
                "never bound, and re-digest afterwards so the manifest reported clean. Rename this "
                "group or this state.",
                field="states",
            )
    return None


def _bsdf_input_role(record: Any) -> str:
    """Which `role` wrote a `bsdf`-kind input -- defaulting to `"matrix"` when the record predates
    the `role` argument entirely.

    Every `bsdf` input registered before this task's `role` argument existed (`role="matrix"`'s own
    commit path, unchanged since Phase 6a) has a `registered_by.arguments` with no `"role"` key at
    all -- the key did not exist yet to record, so `.get("role")` on such a record returns `None`,
    which equals neither `"matrix"` nor `"direct_sun"`. `role="matrix"` is the **only** role that
    ever wrote a `window_group` bsdf input before this task, so a missing key is read as that role
    rather than as "unowned". **Do not read the missing key as unowned, and do not drop this
    default as dead code.** Reading it as unowned excludes every pre-existing record from both
    directions of `_refuse_bsdf_input_id_collision`'s check silently -- caught in review round 2,
    where a project bound before this task shipped kept the full original defect: a colliding
    `role="direct_sun"` call would still silently invalidate its sealed `tmx`.
    """
    if record.registered_by is None:
        return "matrix"
    return record.registered_by.arguments.get("role", "matrix")


def _bsdf_input_group(record: Any) -> str | None:
    """Which window group wrote a `bsdf`-kind input, or `None` when the record cannot say.

    `_assign_bsdf` records the group under `registered_by.arguments["name"]` on both roles, and
    every pre-`role` record carries it too -- the `name`/`state`/`path` triple predates this
    branch. `None` therefore means the record carries no provenance at all (no `registered_by`, or
    arguments without a `name`), which `_refuse_bsdf_input_id_collision` treats as *not this call's
    own*: an id whose owner cannot be established must not be silently overwritten. That is the
    conservative direction on purpose, and it is the opposite default from `_bsdf_input_role`'s,
    because the two answer different questions -- "which role, historically" has exactly one right
    answer for a legacy record, and "which group" has none.
    """
    if record.registered_by is None:
        return None
    return record.registered_by.arguments.get("name")


def _refuse_bsdf_input_id_collision(
    loaded: LoadedProject,
    *,
    name: str,
    role: BsdfRole,
    resolved: dict[str, tuple[Path, BSDFFile, Path | None]],
) -> CallToolResult | None:
    """Refuse an input id this call does not own -- `_refuse_filename_collision`'s sibling, on the
    other `_`-joined axis.

    **Ownership is (role, group), not role alone.** The guard originally collected only the ids
    held by the *other* role, which left the same-role, cross-group collision open, and the
    whole-branch review demonstrated it end to end: group `a_b` state `c` and group `a` state
    `b_c` both write `bsdf_a_b_c_direct_sun`, the second call returned `is_error: False`, and
    `_refuse_drifted_direct_sun_bsdf` then re-digested against the *new* record and reported
    clean -- so a later `run_annual(method="5phase")` for group `a_b` traced the other group's
    tensor tree with correct-looking provenance. `role="matrix"` was covered on that axis only by
    accident, and not by this function: `_refuse_filename_collision` keys on the sealed `tmx`
    filename, which joins the same two components the same way. `role="direct_sun"` seals no
    file, so it had no guard at all on that axis.

    `role="matrix"` writes `bsdf_<group>_<state>`; `role="direct_sun"` writes
    `bsdf_<group>_<state>_direct_sun` (`_assign_bsdf`, below). `NAME_PATTERN`
    (`rad_mcp.names.NAME_PATTERN`) permits `_` in a state name, so a state legally named
    `<x>_direct_sun` bound at `role="matrix"` writes the identical string
    `role="direct_sun"` writes for state `<x>` -- `bsdf_<group>_<x>_direct_sun` either way.
    `register_input` replaces the whole record by id (`rad_engine.project.register_input`), so
    whichever of the two calls lands second would silently overwrite the first role's record:
    invalidating a sealed `tmx` if `role="matrix"` bound first, or substituting a Klems file into
    the `Cds` binding if `role="direct_sun"` bound first -- both with `is_error: False`, no
    advisory, no message, until this guard.

    Ownership is read by `_bsdf_input_role`, which reads `registered_by.arguments["role"]` where
    both arms of `_assign_bsdf` set it, and defaults to `"matrix"` for a record written before that
    key existed (see that function's own docstring -- this is a migration default, not a fallback
    to tidy away). Not derived from artifact presence: a fresh `role="matrix"` call has sealed no
    `tmx` yet at this point in its own validation, so "does a `tmx` reference this id" would read
    that call's own, not-yet-written id as unowned rather than as its own -- the wrong answer for
    the collision this guard exists to catch on someone *else's* prior call.
    """
    owners = {
        record.id: (_bsdf_input_role(record), _bsdf_input_group(record))
        for record in loaded.manifest.inputs
        if record.kind == "bsdf"
    }
    for state in resolved:
        candidate = (
            f"bsdf_{name}_{state}_direct_sun" if role == "direct_sun" else f"bsdf_{name}_{state}"
        )
        owner = owners.get(candidate)
        if owner is None:
            continue
        owner_role, owner_group = owner
        if owner_role == role and owner_group == name:
            # This group re-binding its own state at its own role: ordinary traffic, and the one
            # case `register_input`'s replace-by-id is *for*. Compared on both axes because either
            # alone over-refuses -- role alone would refuse a second group, group alone would
            # refuse the other role on the same group.
            continue
        held_by = (
            f"a role={owner_role!r} binding of group {owner_group!r}"
            if owner_group is not None
            else f"a role={owner_role!r} binding whose own group the manifest does not record"
        )
        return _refuse_argument(
            f"state {state!r} of group {name!r} at role={role!r} would write input id "
            f"{candidate!r}, which {held_by} already owns. Group and state names are joined "
            "with '_' to form this id, and '_' is legal in both, so two different bindings can "
            f"name one id: group 'a_b' state 'c' and group 'a' state 'b_c' both write "
            "'bsdf_a_b_c_direct_sun', and a state ending in '_direct_sun' bound at role='matrix' "
            "writes the same id role='direct_sun' writes for the state named without that "
            "suffix. Writing it would replace that binding's record -- sealed tmx bytes "
            "included, if role='matrix' is the one that already owns it, and silently repointing "
            "the other binding's direct-sun XML if it is not. Rename this group or this state.",
            field="states",
        )
    return None


def _refuse_bsdf(message: str, code: str) -> CallToolResult:
    """A basis refusal. `field="states"` on both: the argument the caller can act on is the state
    map, not the group's name or the project."""
    return refuse(
        message, [Advisory(code=code, severity="error", field="states", message=message)]
    )


def _basis_refusal(
    state: str, source: Path, parsed: BSDFFile, *, role: BsdfRole
) -> CallToolResult | None:
    """Which allocated basis code this pairing earns for `role`, or `None` if it binds cleanly.

    `role="matrix"`'s order matters and is not arbitrary: a tensor tree carries no angle-basis
    blocks at all, so `bins` is `None` and a bin-count test would read as "unknown" rather than
    "wrong". The tensor test is the structural one and runs first; the full-basis test then applies
    only to files that really are Klems. `role="direct_sun"` has no analogous second test: a tensor
    tree carries no basis *name* to be off, so once `suitable_for.five_phase_direct` holds there is
    nothing further to check.
    """
    if role == "matrix":
        if not parsed.suitable_for.three_phase:
            # `BSDF_TENSOR_NOT_FOR_MATRIX` -- §9's row, and ruling R63's named code for check 5.4
            # clause 1. `suitable_for.three_phase` is Phase 5's published fact, derived there as the
            # exact complement of `five_phase_direct`; this layer compares, it does not re-derive.
            return _refuse_bsdf(
                f"state {state!r} binds {source.name}, which is a {parsed.basis.value} file. The "
                "tensor-tree representation does not work in the matrix formulation of the "
                "daylight coefficient method (genBSDF man page, NOTES), so it cannot be bound to a "
                "3-phase state at role='matrix'. Bind a Klems-basis XML here; the tensor tree is "
                "the right file for the five-phase direct-sun term -- bind it with "
                "role='direct_sun' instead.",
                "BSDF_TENSOR_NOT_FOR_MATRIX",
            )

        if parsed.basis is Basis.KLEMS:
            declared = (parsed.basis_name or "").lower()
            if _KLEMS_FULL_BASIS_NAME not in declared:
                # `BSDF_BASIS_MISMATCH` -- §6's row. Keyed on the declared **name**, not on `bins`:
                # a file naming itself a half basis while still summing to 145 blocks is exactly
                # the case a count test would wave through, and it was measured on the renamed
                # fixture.
                return _refuse_bsdf(
                    f"state {state!r} binds {source.name}, which declares its basis as "
                    f"{parsed.basis_name!r}. The V and D matrices this server builds are binned "
                    f"into the {KLEMS_FULL_BINS}-direction full Klems basis, so a file on any "
                    "other basis describes a different set of directions than the matrices it "
                    "would multiply with. Bind a full-Klems XML, or rebuild the matrices against "
                    "this file's basis.",
                    "BSDF_BASIS_MISMATCH",
                )

        return None

    # role == "direct_sun"
    if not parsed.suitable_for.five_phase_direct:
        # `BSDF_KLEMS_NOT_FOR_DIRECT_SUN` -- §9's row, and this task's named code for check 5.4
        # clause 2, the reverse of the check above. `suitable_for.five_phase_direct` is
        # `three_phase`'s exact complement (Phase 5), so this compares the identical pair of
        # published booleans the `role="matrix"` clause above compares, the other way round.
        return _refuse_bsdf(
            f"state {state!r} binds {source.name}, which is a {parsed.basis.value} file, at "
            "role='direct_sun'. The five-phase method's direct-sun term (Cds) needs the "
            "tensor-tree representation genBSDF produces for a peaked BSDF -- a Klems file "
            "describes the same device on a coarse angular grid that cannot resolve a sun disc, "
            "which is exactly what the tensor tree exists for. Bind a tensor-tree XML here; "
            f"{source.name}'s Klems basis makes role='matrix' the chain it belongs to -- though "
            "role='matrix' applies its own basis check to it, and a Klems file off the full basis "
            "is refused there too, with BSDF_BASIS_MISMATCH.",
            "BSDF_KLEMS_NOT_FOR_DIRECT_SUN",
        )

    return None


def _group_record(loaded: LoadedProject, name: str) -> Any | None:
    candidates = {name, _INPUT_PREFIX + name}
    for record in loaded.manifest.inputs:
        if record.kind == "window_group" and record.id in candidates:
            return record
    return None


def _copy_file(source: Path, target: Path) -> None:
    """Copy `source` to `target` through `rad_engine.safe_io.open_regular_file` on both ends.

    Not `shutil.copy`: ruling R48, extended on 2026-08-10 to every filesystem *mutation*, requires
    the guarded primitive, and both ends need it here. The read end is a caller-supplied path, and
    the write end lands inside the project -- a FIFO or a symlink at either would otherwise be
    followed. Copied in blocks rather than read whole: the tensor-tree fixture is 4.4 MB and a BSDF
    XML is not a thing to hold in memory twice.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    with open_regular_file(source, "rb") as reader, open_regular_file(target, "wb") as writer:
        while chunk := reader.read(1 << 20):
            writer.write(chunk)


def _assign_bsdf(
    runtime: Runtime,
    loaded: LoadedProject,
    *,
    name: str,
    states: dict[str, str],
    default_state: str | None,
    role: BsdfRole,
    dry_run: bool = False,
) -> CallToolResult:
    """Bind BSDF states to a defined window group, one role per call.

    `role="matrix"` (the default, unchanged since Phase 6a) seals one `tmx` copy per state (ruling
    R62) -- the bound Klems XML *is* the `T` matrix `dctimestep` multiplies directly. `role="direct_sun"`
    (Task 10a) registers the tensor tree bound for the `Cds` term as a plain `bsdf` input, pointing at
    the caller's own file: it is a build **input** to Task 13's `cdsmx` artifact rather than a matrix
    multiplied directly, so nothing is copied or sealed. Each role writes its own `bsdf_*` input id,
    and -- once `_refuse_bsdf_input_id_collision` has cleared it -- never touches what the other
    role already bound on the same state; a 5-phase state needs both, and binding one is not a
    replacement for the other (see the module docstring).

    **Every refusal below sits above the first write** (ruling R39's shape, applied to a mutation
    rather than to `dry_run`): the group must exist, every state's XML must be readable, and every
    basis must pair with `role`'s own chain, all before any file is copied/registered or any
    artifact sealed. A partial binding -- two states committed and the third refused -- would leave
    the group in a state no caller asked for and none can name, so the whole map is validated first
    and then committed.
    """
    paths = ProjectPaths(loaded.paths.root)

    if not states:
        return _refuse_argument(
            "window_group(action='assign_bsdf') was given an empty states map, so there is no "
            "BSDF to bind. Name at least one state, e.g. states={'open': '/path/to/glazing.xml'}.",
            field="states",
        )

    if _group_record(loaded, name) is None:
        return _refuse_undefined_group(
            loaded,
            name,
            purpose="bind a BSDF to",
            why=(
                "A tmx sealed against a group that does not exist would carry a window_group edge "
                "naming nothing, and the aperture geometry could then never invalidate it. "
            ),
        )

    if default_state is not None and default_state not in states:
        return _refuse_argument(
            f"default_state={default_state!r} names no state in this call's states map "
            f"({', '.join(sorted(states))}), so this call's result would report as the default "
            "a state it did not bind. Name one of them, or omit default_state.",
            field="default_state",
        )

    if LM83_DEFAULT in states.values():
        if states != {"closed": LM83_DEFAULT} or role != "matrix":
            return _refuse_argument(
                f"states={states!r} at role={role!r} names {LM83_DEFAULT!r}, which is legal only "
                f"as states={{'closed': {LM83_DEFAULT!r}}} at role='matrix', alone in its call: it "
                "composes LM-83's default closed blind from the group's bound open state, so it "
                "has no meaning for any other state or role. Bind 'open' in its own call first, "
                f"then call window_group(action='assign_bsdf', project={loaded.paths.name!r}, "
                f"name={name!r}, states={{'closed': {LM83_DEFAULT!r}}}).",
                field="states",
            )
        return _assign_lm83_default(
            runtime, loaded, name=name, default_state=default_state, dry_run=dry_run
        )

    # --- validate every state before committing any of them ---------------------------------
    resolved: dict[str, tuple[Path, BSDFFile, Path | None]] = {}
    for state, raw in sorted(states.items()):
        if not is_legal_name(state):
            return _refuse_argument(
                name_refusal_reason(
                    state, kind="BSDF state", examples="'open', 'closed', 'blind-45'"
                ),
                field="states",
            )
        source = contain(raw, runtime.roots)
        if not source.is_file():
            return _refuse_argument(
                f"state {state!r} names {source}, which is not a file on disk. A tmx is sealed "
                "from the bytes that were bound, so there is nothing here to bind.",
                field="states",
            )
        # **Bounded before the parse.** `open_regular_file` already refuses a FIFO or a character
        # device (`S_ISREG` on the opened descriptor), so the unbounded-stream case is closed --
        # what is left is an ordinary regular file of arbitrary size, which `read()` with no
        # argument would slurp whole and `.decode()` would copy again. `_copy_file` below already
        # streams in blocks for exactly this reason; the validation read inherits the same care.
        with open_regular_file(source, "rb") as handle:
            raw_bytes = handle.read(MAX_BSDF_BYTES + 1)
        if len(raw_bytes) > MAX_BSDF_BYTES:
            return _refuse_argument(
                f"state {state!r} names a file larger than {MAX_BSDF_BYTES >> 20} MB. A BSDF XML "
                "describes one device's optical behaviour; a file this size is not one.",
                field="states",
            )
        parsed = parse_bsdf(raw_bytes.decode("utf-8", errors="replace"))
        if (refusal := _basis_refusal(state, source, parsed, role=role)) is not None:
            return refusal

        target: Path | None = None
        if role == "matrix":
            # **The sealed filename is built here, not in the commit loop, and that placement is
            # the guarantee.** Since final fix D's fix round 3 the commit loop saves the manifest
            # once, after the last state, so no record changes until every state is copied; but
            # the copies themselves are written state by state, so a refusal reachable from the
            # loop would leave earlier states' copies in `07_matrices/` -- over a rebind, copies
            # that no longer match the records still naming the old files (which `run_annual`
            # then refuses as `BSDF_MATRIX_DRIFTED`, final fix D, fix round 4) -- breaking this
            # function's own all-or-nothing contract. `artifact_path` refuses a component over 64
            # characters, and `name` and `state` are each independently allowed 64, so
            # `f"{name}_{state}.xml"` is reachably over the limit with both halves legal. Refused
            # here, where a refusal costs nothing. Only `role="matrix"` seals a copy, so only
            # this role needs a target.
            try:
                target = paths.artifact_path("tmx", f"{name}_{state}.xml")
            except ProjectError as exc:
                return _refuse_argument(
                    f"state {state!r} of group {name!r} cannot be sealed: {exc}", field="states"
                )
        resolved[state] = (source, parsed, target)

    # **The two roles' ids can collide on a `_`-joined state name, the same way two groups' sealed
    # filenames can (below).** Checked for both roles, against the manifest as it stands -- a
    # single call only ever binds one role, so no within-call pass is needed here the way the
    # filename guard needs one for `role="matrix"`'s own multi-state map.
    if (
        refusal := _refuse_bsdf_input_id_collision(loaded, name=name, role=role, resolved=resolved)
    ) is not None:
        return refusal

    if role == "matrix":
        # **Two legal bindings can name one sealed file, and the second would overwrite the first
        # silently.** `NAME_PATTERN` permits `_` in both a group name and a state name, and the two
        # are joined with `_` -- so `south_facade`/`open` and `south`/`facade_open` both resolve to
        # `south_facade_open.xml`. `_copy_file` opens the target `"wb"`, and `register_artifact`
        # re-digests **after** the write, so the first group's manifest would report a
        # `content_digest` matching bytes it never bound: R62's guarantee exactly inverted, with
        # the drift check reporting clean over a substitution. Refused rather than resolved,
        # because renaming one of them on the caller's behalf would be this server picking which
        # group's handle is real. Only reachable here: `role="direct_sun"` writes no file to
        # collide over.
        matrix_resolved: dict[str, tuple[Path, BSDFFile, Path]] = {
            state: (source, parsed, target)
            for state, (source, parsed, target) in resolved.items()
            if target is not None
        }
        if (
            refusal := _refuse_filename_collision(loaded, name=name, resolved=matrix_resolved)
        ) is not None:
            return refusal

    # --- commit ------------------------------------------------------------------------------
    views: dict[str, StateView] = {}
    with runtime.modifying(paths):
        fresh = open_project(paths.root, radiance_version=runtime.launcher.version)
        # Fix round 3: every state's destination is checked before the first write, so a refusal
        # of the last state commits none of them (the containment check used to sit in the commit
        # loop, after earlier states were registered and copied). The registrations below are
        # saved once, after the last of them.
        ids: list[str] = []
        for state, (_source, _parsed, target) in resolved.items():
            if target is not None:
                # Contained before the write, the ordering `rad_mcp.bsdf_tool` records as "the
                # whole guard": `resolved_parent_within` covers the intermediate components
                # `O_NOFOLLOW` cannot, so a directory symlink at `07_matrices` is refused before
                # mkdir traverses it.
                if not resolved_parent_within(target, runtime.roots):
                    raise _matrices_outside_root(target, state)
                occupied = _refuse_occupied(
                    target, what=f"the sealed copy of state {state!r} of group {name!r}"
                )
                if occupied is not None:
                    return occupied
                ids += [f"bsdf_{name}_{state}", f"tmx:{name}:{state}"]
            else:
                ids.append(f"bsdf_{name}_{state}_direct_sun")
        if dry_run:
            # Fix round 2: the last point before the first write, after every refusal above.
            copying = ", copying each XML into the project" if role == "matrix" else ""
            return _refuse_no_dry_run(
                "assign_bsdf",
                f"this call would register {_ids_in_prose(ids)}{copying}. Nothing was written.",
            )
        for state, (source, parsed, target) in resolved.items():
            if role == "matrix":
                assert target is not None
                bsdf_input_id = f"bsdf_{name}_{state}"
                register_input(
                    fresh,
                    input_id=bsdf_input_id,
                    kind="bsdf",
                    files=[source],
                    registered_by=ToolCall(
                        tool="window_group",
                        action="assign_bsdf",
                        arguments={"name": name, "state": state, "path": str(source), "role": role},
                    ),
                    save=False,
                )

                artifact_id = f"tmx:{name}:{state}"
                _copy_file(source, target)

                register_artifact(
                    fresh,
                    artifact_id=artifact_id,
                    kind="tmx",
                    path=target,
                    built_from=[bsdf_input_id, _INPUT_PREFIX + name],
                    parameters={"state": state, "basis": parsed.basis.value},
                    radiance_version=runtime.launcher.version,
                    save=False,
                )
                views[state] = StateView(
                    path=str(target), basis=parsed.basis_name, artifact_id=artifact_id
                )
            else:
                # `role="direct_sun"`: registered where the caller put it, never copied or sealed
                # (module docstring) -- this is Task 13's `cdsmx` build **input**, not a matrix
                # multiplied directly, the same relationship a materials XML has to a `dmx` build.
                # `_refuse_bsdf_input_id_collision`, above, has already refused this id if a
                # `role="matrix"` binding owns it (a state named `<this state>_direct_sun`), so by
                # the time execution reaches here this binding is additive: it does not overwrite,
                # and is not overwritten by, the matrix binding already made -- or later made -- on
                # the same state.
                bsdf_input_id = f"bsdf_{name}_{state}_direct_sun"
                register_input(
                    fresh,
                    input_id=bsdf_input_id,
                    kind="bsdf",
                    files=[source],
                    registered_by=ToolCall(
                        tool="window_group",
                        action="assign_bsdf",
                        arguments={"name": name, "state": state, "path": str(source), "role": role},
                    ),
                    save=False,
                )
                views[state] = StateView(
                    direct_sun_path=str(source),
                    direct_sun_basis=parsed.basis_name,
                    direct_sun_input_id=bsdf_input_id,
                )
        save_manifest(paths, fresh.manifest)

    return emit(
        WindowGroupAssigned(
            project=loaded.paths.name,
            root=str(loaded.paths.root),
            name=name,
            states=views,
            # With no explicit default, a single-state group's own state is the default: a static
            # fenestration group has exactly one thing a run could mean. With two or more and no
            # explicit choice, this reports None rather than picking -- which state is "normal" is
            # the caller's fact about their building, not one this server can read off the files.
            default_state=default_state or (next(iter(views)) if len(views) == 1 else None),
        )
    )


def _assign_lm83_default(
    runtime: Runtime, loaded: LoadedProject, *, name: str, default_state: str | None,
    dry_run: bool = False,
) -> CallToolResult:
    """`states={"closed": "lm83_default"}` (R871): LM-83's default closed blind, composed.

    Read from the group's **sealed** `tmx:<name>:open` copy, never from the caller's source XML:
    the copy is what the 3-phase chain multiplies, and its bytes are checked against the record's
    `content_digest` in the same read that feeds the composition (`bytes_digest`, one buffer), so a
    drifted copy is refused rather than composed. `rad_core.bsdf.lambertian_klems_text` replaces
    every transmission block with a uniform one of 5 % of that block's own diffuse-diffuse
    transmittance. The composed file is server-written into `05_bsdf/lm83_default/`, registered as
    `bsdf_<name>_closed`, then copied and sealed as `tmx:<name>:closed` exactly as a file binding is
    -- with `bsdf_<name>_open` added to `built_from`, so rebinding the open state makes the closed
    state stale rather than leaving a 5 % of a glazing the group no longer has.
    """
    paths = ProjectPaths(loaded.paths.root)
    open_id = f"tmx:{name}:open"
    open_input_id = f"bsdf_{name}_open"
    retry_open = (
        f"window_group(action='assign_bsdf', project={loaded.paths.name!r}, name={name!r}, "
        "states={'open': <path to a full-Klems BSDF XML>})"
    )
    sealed = next((a for a in loaded.manifest.artifacts if a.id == open_id), None)
    if sealed is None:
        return _refuse_argument(
            f"group {name!r} has no sealed open state ({open_id}), so there is nothing to compose "
            f"LM-83's default closed blind from. Bind it first, in its own call: {retry_open}; "
            f"then send states={{'closed': {LM83_DEFAULT!r}}} again.",
            field="states",
        )
    # The `bsdf_<name>_open` record's own source path, when it names one, makes the rebind remedy a
    # call that pastes as written; without it the template above is all that can be said.
    open_record = next((r for r in loaded.manifest.inputs if r.id == open_input_id), None)
    recorded = (
        open_record.registered_by.arguments.get("path")
        if open_record is not None and open_record.registered_by is not None
        else None
    )
    rebind = (
        f"window_group(action='assign_bsdf', project={loaded.paths.name!r}, name={name!r}, "
        f"states={{'open': {recorded!r}}})"
        if isinstance(recorded, str)
        else retry_open
    )
    remedy = (
        f"Bind the open state again: {rebind}; then send "
        f"states={{'closed': {LM83_DEFAULT!r}}} again."
    )
    open_path = Path(sealed.path)
    try:
        with open_regular_file(open_path, "rb") as handle:
            data = handle.read(MAX_BSDF_BYTES + 1)
    except OSError as exc:
        # Missing, a directory, a FIFO or a symlink where the sealed copy should be: the same
        # condition as drift for the caller -- the bytes the open state was sealed with are gone.
        return _refuse_argument(
            f"the sealed open state {open_path} could not be read as a regular file ({exc}), so "
            f"there is nothing to compose LM-83's default closed blind from. {remedy}",
            field="states",
        )
    if len(data) > MAX_BSDF_BYTES:
        return _refuse_argument(
            f"the sealed open state {open_path} is larger than {MAX_BSDF_BYTES >> 20} MB, so it is "
            f"not composed. {remedy}",
            field="states",
        )
    if sealed.content_digest is None:
        return _refuse_argument(
            f"{open_id} records no content digest, so its sealed copy {open_path} cannot be "
            "checked against the bytes it was sealed with and is not composed. "
            f"{remedy}",
            field="states",
        )
    if bytes_digest(data) != sealed.content_digest:
        return _refuse_argument(
            f"the sealed open state {open_path} no longer holds the bytes {open_id} was sealed "
            "with, so a closed state composed from it would describe a glazing the project never "
            f"bound. {remedy}",
            field="states",
        )
    text = data.decode("utf-8", errors="replace")
    parsed_open = parse_bsdf(text)
    if (
        parsed_open.basis is not Basis.KLEMS
        or _KLEMS_FULL_BASIS_NAME not in (parsed_open.basis_name or "").lower()
    ):
        return _refuse_argument(
            f"the sealed open state of group {name!r} is not a full Klems file, so LM-83's default "
            f"closed blind cannot be composed from it. {remedy}",
            field="states",
        )
    try:
        composed, tau_open, tau_closed = lambertian_klems_text(
            text, fraction=LM83_DEFAULT_DIFFUSE_FRACTION
        )
    except BSDFParseError as exc:
        return _refuse_argument(
            "LM-83's default closed blind cannot be composed from the open state of group "
            f"{name!r}: {exc}",
            field="states",
        )
    composed_bytes = composed.encode("utf-8")
    parsed = parse_bsdf(composed)

    state = "closed"
    written = paths.bsdf / _LM83_DEFAULT_DIRECTORY / f"{name}_{state}.xml"
    try:
        target = paths.artifact_path("tmx", f"{name}_{state}.xml")
    except ProjectError as exc:
        return _refuse_argument(
            f"state {state!r} of group {name!r} cannot be sealed: {exc}", field="states"
        )
    resolved: dict[str, tuple[Path, BSDFFile, Path]] = {state: (written, parsed, target)}
    if (
        refusal := _refuse_bsdf_input_id_collision(
            loaded, name=name, role="matrix", resolved=dict(resolved)
        )
    ) is not None:
        return refusal
    if (refusal := _refuse_filename_collision(loaded, name=name, resolved=resolved)) is not None:
        return refusal

    bsdf_input_id = f"bsdf_{name}_{state}"
    artifact_id = f"tmx:{name}:{state}"
    with runtime.modifying(paths):
        fresh = open_project(paths.root, radiance_version=runtime.launcher.version)
        # Re-read under the lock: an open state rebound between the read above and this write
        # would otherwise be sealed as this closed state's source while it composed another's.
        current_open = next((a for a in fresh.manifest.artifacts if a.id == open_id), None)
        if current_open is None or current_open.content_digest != sealed.content_digest:
            return _refuse_argument(
                f"the open state of group {name!r} changed while the closed state was being "
                "composed, so nothing was written. Send "
                f"states={{'closed': {LM83_DEFAULT!r}}} again.",
                field="states",
            )
        for path in (written, target):
            if not resolved_parent_within(path, runtime.roots):
                raise PathOutsideRoot(
                    f"the project directory {path.parent} resolves outside every allowed root, "
                    f"so LM-83's default closed state for group {name!r} was not written."
                )
        # Fix round 3: both destinations are checked before the first write, and the two
        # registrations are saved once, so a refusal at the sealed copy commits nothing.
        for path, what in (
            (written, f"LM-83's default closed state of group {name!r}"),
            (target, f"the sealed copy of state {state!r} of group {name!r}"),
        ):
            if (occupied := _refuse_occupied(path, what=what)) is not None:
                return occupied
        if dry_run:  # fix round 2: after every refusal, before the first write
            return _refuse_no_dry_run(
                "assign_bsdf",
                f"this call would compose LM-83's default closed state from {open_id}, write it "
                f"into the project and register {bsdf_input_id} and {artifact_id}. Nothing was "
                "written.",
            )
        written.parent.mkdir(parents=True, exist_ok=True)
        with open_regular_file(written, "wb") as writer:
            writer.write(composed_bytes)
        register_input(
            fresh,
            input_id=bsdf_input_id,
            kind="bsdf",
            files=[written],
            registered_by=ToolCall(
                tool="window_group",
                action="assign_bsdf",
                arguments={
                    "name": name,
                    "state": state,
                    "path": str(written),
                    "role": "matrix",
                    "lm83_default": True,
                    "open_input": open_input_id,
                },
            ),
            sources=[FileRef(path=str(open_path), digest=bytes_digest(data))],
            server_written=True,
            save=False,
        )
        _copy_file(written, target)
        register_artifact(
            fresh,
            artifact_id=artifact_id,
            kind="tmx",
            path=target,
            built_from=[bsdf_input_id, _INPUT_PREFIX + name, open_input_id],
            parameters={
                "state": state,
                "basis": parsed.basis.value,
                "lm83_default": True,
                "lm83_default_fraction": LM83_DEFAULT_DIFFUSE_FRACTION,
                "open_diffuse_transmittance": tau_open,
                "diffuse_transmittance": tau_closed,
            },
            radiance_version=runtime.launcher.version,
            save=False,
        )
        save_manifest(paths, fresh.manifest)

    return emit(
        WindowGroupAssigned(
            project=loaded.paths.name,
            root=str(loaded.paths.root),
            name=name,
            states={
                state: StateView(
                    path=str(target),
                    basis=parsed.basis_name,
                    artifact_id=artifact_id,
                    open_diffuse_transmittance=tau_open,
                    diffuse_transmittance=tau_closed,
                )
            },
            default_state=default_state or state,
        )
    )


# --- bind_schedule (R870) ------------------------------------------------------------------------


def _refuse_undefined_group(
    loaded: LoadedProject, name: str, *, purpose: str, why: str = "", project: str | None = None
) -> CallToolResult:
    """`name` names no defined group. `why`, when given, is a whole sentence (with its trailing
    space) saying what binding to a missing group would break.

    With `project` given (final fix E3, `bind_schedule`'s caller) the define call is spelled whole
    with `automated=True`: `bind_schedule` binds only to a marked group, so a define without the
    mark would be refused as unmarked on the retry. `name` is legal here, since `_dispatch` runs
    `_refuse_name` before either caller."""
    known = sorted(
        _group_name(record) for record in loaded.manifest.inputs if record.kind == "window_group"
    )
    define = (
        "window_group(action='define', ...)"
        if project is None
        else f"window_group(action='define', project={project!r}, name={name!r}, "
        "aperture=[<its glazing modifiers>], automated=True)"
    )
    return _refuse_argument(
        f"no window group named {name!r} is defined in this project, so there is nothing to "
        f"{purpose}. {why}Define the group first with {define}. "
        + (f"Defined groups: {', '.join(known)}." if known else "This project has none yet."),
        field="name",
    )


def _refuse_unmarked_group(project: str, name: str, record: Any) -> CallToolResult:
    return _refuse_argument(
        f"window group {name!r} is not marked automated, so no run would read an hourly schedule "
        "bound to it: its blinds follow the LM-83 blind schedule. Mark it first by redefining it "
        f"with everything it holds: {_redefine_call(project, name, record, automated=True)}. "
        "Then bind the schedule again.",
        field="name",
    )


def _bind_schedule(
    runtime: Runtime, loaded: LoadedProject, *, project: str, name: str, schedule: str,
    dry_run: bool = False,
) -> CallToolResult:
    """Copy one automated group's hourly schedule into the project and register it (R870).

    Every refusal sits above the first write: the group defined and marked automated, the file
    contained and a regular file, read **once** (bounded at `MAX_AUTOMATED_SCHEDULE_BYTES + 1`, so
    the parse sees an oversized file as oversized) and parsed. The copy is written from that same
    validated buffer -- never a second read of the caller's path, which a writer could have swapped
    between the parse and the copy. Rebinding replaces the record and the copy.
    """
    paths = ProjectPaths(loaded.paths.root)
    record = _group_record(loaded, name)
    if record is None:
        return _refuse_undefined_group(
            loaded, name, purpose="bind a schedule to", project=project
        )
    if not _is_automated(record):
        return _refuse_unmarked_group(project, name, record)

    source = contain(schedule, runtime.roots)
    try:
        with open_regular_file(source, "rb") as handle:
            data = handle.read(MAX_AUTOMATED_SCHEDULE_BYTES + 1)
    except OSError as exc:
        # A directory, a FIFO, a symlink at the final component or a missing file: none is a
        # schedule, and the argument that named it is `schedule`.
        return _refuse_argument(
            f"schedule names {source}, which could not be read as a regular file: {exc}.",
            field="schedule",
        )
    try:
        rows = parse_automated_schedule(data)
    except AutomatedScheduleError as exc:
        return _refuse_argument(str(exc), field="schedule")

    input_id = _SCHEDULE_PREFIX + name
    target = paths.bsdf / _SCHEDULE_DIRECTORY / f"{name}.csv"
    with runtime.modifying(paths):
        fresh = open_project(paths.root, radiance_version=runtime.launcher.version)
        # Re-read under the lock: a concurrent redefine without the mark, or a remove, between the
        # check above and this write would otherwise bind a schedule no run reads.
        current_record = _group_record(fresh, name)
        if current_record is None:
            return _refuse_undefined_group(
                fresh, name, purpose="bind a schedule to", project=project
            )
        if not _is_automated(current_record):
            return _refuse_unmarked_group(project, name, current_record)
        if not resolved_parent_within(target, runtime.roots):
            raise PathOutsideRoot(
                f"the project's schedule directory {target.parent} resolves outside every allowed "
                "root, so the schedule was not written."
            )
        # Fix round 4 (review N2): a directory, symlink or FIFO where the copy goes is refused here,
        # before the dry_run refusal, so a dry run gets the refusal the real call makes.
        occupied = _refuse_occupied(target, what=f"the schedule copy of window group {name!r}")
        if occupied is not None:
            return occupied
        if dry_run:  # fix round 2: after every refusal, before the first write
            return _refuse_no_dry_run(
                "bind_schedule",
                f"this call would copy the schedule into the project and register {input_id}. "
                "Nothing was written.",
            )
        target.parent.mkdir(parents=True, exist_ok=True)
        with open_regular_file(target, "wb") as writer:
            writer.write(data)
        register_input(
            fresh,
            input_id=input_id,
            kind="automated_schedule",
            files=[target],
            registered_by=ToolCall(
                tool="window_group",
                action="bind_schedule",
                arguments={"name": name, "schedule": str(source)},
            ),
            sources=[FileRef(path=str(source), digest=bytes_digest(data))],
            server_written=True,
            save=False,
        )
        save_manifest(paths, fresh.manifest)

    return emit(
        WindowGroupScheduleBound(
            project=loaded.paths.name,
            root=str(loaded.paths.root),
            name=name,
            input_id=input_id,
            path=str(target.resolve()),
            hours=rows.hours,
            hours_closed=rows.hours_closed,
            header=rows.header,
        )
    )


# --- list / remove -------------------------------------------------------------------------------


def _group_name(record: Any) -> str:
    return record.id[len(_INPUT_PREFIX):] if record.id.startswith(_INPUT_PREFIX) else record.id


def _listed(loaded: LoadedProject) -> CallToolResult:
    # `automated_schedule_file` is the rule `rad_engine.chain.automated_schedules` reads by; a
    # record that names no one file is listed as no schedule here and refused by a run.
    schedules = {
        record.id: ref.path
        for record in loaded.manifest.inputs
        if record.kind == "automated_schedule"
        and (ref := automated_schedule_file(record)) is not None
    }
    rows = [
        WindowGroupInputView(
            id=record.id,
            files=[ref.path for ref in record.files],
            closing_role=record.role,
            automated=_is_automated(record),
            schedule=schedules.get(_SCHEDULE_PREFIX + _group_name(record)),
        )
        for record in loaded.manifest.inputs
        if record.kind == "window_group"
    ]
    return emit(
        WindowGroupListed(
            project=loaded.paths.name, root=str(loaded.paths.root), window_groups=rows
        )
    )


def _removed(
    runtime: Runtime, loaded: LoadedProject, *, project: str, name: str, dry_run: bool = False
) -> CallToolResult:
    """Deregister one window-group input. Never refused when nothing matches `name` -- the
    post-condition the caller asked for already holds (`geometry_tool._removed`'s own reasoning,
    same shape here).

    **Refused when a sealed artifact still names the input -- Phase 6b Task 6, ruling 6.** This
    function filtered `manifest.inputs` and never consulted `manifest.artifacts`, which made it the
    **sole outlier** among this server's four removal paths: `geometry_tool._removed` (:927),
    `materials_tool`'s withdrawal (:816) and `weather_tool`'s own remove (:654) each call
    `refuse_if_built_from` before touching a record, and each of them closed the identical hole
    (finding N2). `assign_bsdf` seals `tmx:<group>:<state>` with `built_from=[bsdf_input_id,
    window_group_<group>]` (R62 and `REQUIRED_EDGES["tmx"]`, which names `window_group` as a
    required `input_kinds` member), so the edge this guard protects is one this tool writes itself.

    **What removing it anyway produced**, measured on this branch: `define -> assign_bsdf -> remove
    -> define` (same name, no BSDF this time) left the `tmx` artifacts standing over a group that
    had been redefined without them. `rad_engine.chain.states_by_group` derives a group's states by
    scanning artifact ids -- nothing stores the list -- so the project reported a BSDF state that no
    longer existed and the run inferred 3-phase off fenestration the caller had withdrawn. The
    graver reading is `InputStillBuiltFrom`'s own: `rad_core.manifest` walks `built_from` on every
    read, so an artifact naming a removed input makes the **whole document** unevaluable and every
    later call that opens the project fails. One guard covers both, and it is the existing one.

    Nothing new is allocated: `ARTIFACT_EDGES_INCOMPLETE` is carried by `InputStillBuiltFrom`
    itself, allocated by ruling R6 for exactly *"an artifact ... reported valid and reused
    forever"*. The refusal is raised **inside** the lock but **before** `manifest.inputs` is
    filtered, so a refused removal has changed nothing -- a guard downstream of the write it
    prevents is a report, not a guard (R48's own distinction).

    **The group's bound schedule goes with it (R870).** `automated_schedule_<name>` is removed in
    the same write and listed in `removed`, and `refuse_if_built_from` checks both ids. The copied
    CSV is left on disk, as the geometry files are.
    """
    paths = ProjectPaths(loaded.paths.root)
    candidates = {name, _INPUT_PREFIX + name}
    with runtime.modifying(paths):
        fresh = open_project(paths.root, radiance_version=runtime.launcher.version)
        removed = [
            record.id
            for record in fresh.manifest.inputs
            if record.kind == "window_group" and record.id in candidates
        ]
        # R870: the group's bound schedule goes with it, in the same write; no run reads a
        # schedule whose group is gone.
        removed += [
            record.id
            for record in fresh.manifest.inputs
            if record.kind == "automated_schedule" and record.id == _SCHEDULE_PREFIX + name
        ]
        if removed:
            refuse_if_built_from(fresh.manifest, removed)
            if dry_run:
                # Fix round 2: after the `built_from` refusal, before the write. With nothing
                # matched there is no write, and the empty `removed` is answered unrefused.
                return _refuse_no_dry_run(
                    "remove",
                    f"this call would deregister {_ids_in_prose(removed)}. Nothing was removed.",
                    f"; window_group(action='list', project={project!r}) reports what is "
                    "registered and changes nothing.",
                )
            fresh.manifest.inputs = [
                record for record in fresh.manifest.inputs if record.id not in removed
            ]
            save_manifest(paths, fresh.manifest)
    return emit(
        WindowGroupRemoved(project=loaded.paths.name, root=str(loaded.paths.root), removed=removed)
    )
