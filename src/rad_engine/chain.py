"""The matrix chains `run_annual` submits, built as `Step` lists -- 2-phase and 3-phase.

**Why this module exists, and why it is `rad_engine` rather than `rad_mcp`.** CONTEXT.md's Layers
table gives `rad_engine` *"Builds commands, runs binaries"* and `rad_mcp` *"No physics"*.
Until Phase 7a every `Step(` in this repo was constructed in `src/rad_mcp/annual_tool.py`, which had grown past
3 600 lines carrying the 2-phase and 3-phase chains, their edge sets, their sampling constants and
the reuse decision alongside the argument refusals and the wire envelope. Phase 7a adds five more
matrices and three more `dctimestep` invocations; written where 3-phase sat, that is a
protocol-layer file past 5 000 lines and a physics leak the layer rule already forbids.

Nothing here is new. Every function, constant and docstring below was moved out of
`rad_mcp.annual_tool` unchanged -- the task that created this module was a pure refactor whose proof
was that the existing suite stayed green -- so the reasoning each one carries is the reasoning it
carried at the call site it was written for, and the phase and ruling references in it point at the
tasks that decided them.

## What is here and what deliberately is not

Here: the two step lists, the artifact edge sets they seal, the 3-phase target resolution and shape
precondition, the reuse decision, and the sampling constants all of those read.

**Not here: any file write.** `two_phase_steps` and `three_phase_steps` used to write their own
`points.pts`, `receiver.rad` and `aperture_sample.rad` through `rad_mcp.annual_tool._write_contained`
-- ruling R48's containment primitive, which raises `rad_mcp.paths.PathOutsideRoot`, a wire-layer
type this module may not import. So the protocol layer writes those files and hands this module the
paths (`ChainRequest.points_path`, `ChainRequest.sample_rad_paths`), which is what
`ChainRequest`'s field list already assumed. The write order at the call site is unchanged.

**Not here either: the wire refusals.** `rad_mcp.annual_tool` keeps `_validate_arguments`, every
`_refuse_*` function, every advisory, `_dispatch` and the submit-time octree build. What this module
raises instead is `ThreePhaseNotBuildableError`, a `rad_engine.project.ProjectError`, which the
protocol layer's existing handler chain shapes into a refusal exactly as it shaped it before.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from math import ceil, isfinite
from pathlib import Path
from typing import Any, Final

from rad_core.apertures import Aperture, grid_behind_aperture
from rad_core.blinds import ClosingOrder, GroupFacts, closing_order
from rad_core.bsdf import parse_bsdf
from rad_core.manifest import (
    DIGEST_EXCLUDED_PARAMETERS,
    GROUND_REFLECTANCE_INPUT_ID,
    FileRef,
    ProvenanceValue,
    normalise_parameters,
)
from rad_core.grids import LM83_GRID_POINT_DENSITY_MIN, lm83_grid_point_density
from rad_core.lm83_edition import DEFAULT_LM83_EDITION, LM83_22, LM83_EDITIONS, Lm83Edition, section
from rad_core.metrics import LM83_ASE_THRESHOLD_LUX, LM83_BLIND_AREA_GENERAL_SF
from rad_engine.annual import (
    DcMatrixHeader,
    DcMatrixSpec,
    MatrixFileUnreadableError,
    MatrixShapeError,
    build_dctimestep_argv,
    build_direct_lux_argv,
    build_five_phase_combine_argv,
    build_rcollate_final_argv,
    build_rcollate_leg_argv,
    build_rfluxmtx_argv,
    build_rlam_argv,
    build_rmtxop_sum_argv,
    build_three_phase_argv,
    check_three_phase_shapes,
    dc_receiver_filename,
    read_matrix_header,
)
from rad_engine.blend import SCHED_DIGEST_OPERAND_KEY
from rad_engine.bsdf import MAX_BSDF_BYTES
from rad_engine.jobs import HELPER_SENTINEL, ArtifactSpec, AseOcclusionProbe, OperandDigest, Step
from rad_engine.klems import KLEMS_BINS
from rad_engine.matrices import (
    build_ase_direct_argv,
    build_cds_argv,
    build_d_argv,
    build_genklemsamp_argv,
    build_v_argv,
    reinhart_bins,
)
from rad_engine.octree import dash_safe_operand, refuse_shell_unsafe_paths
from rad_engine.project import (
    NORTH_INPUT_ID,
    LoadedProject,
    ProjectError,
    is_reusable,
)
from rad_engine.rtrace import QualityParameters, parameters_dict
from rad_engine.safe_io import open_regular_file
from rad_engine.schedule import LM83_EDITION_PARAMETER, hours_per_pass, sealed_lm83_edition
from rad_engine.skymatrix import (
    SOLAR_DISK_DEGREES,
    SkyMatrixSpec,
    build_gendaymtx_argv,
    reinhart_row_count,
)
from rad_engine.suns import SUNS_MODIFIER, SunPosition

logger = logging.getLogger(__name__)

__all__ = [
    "ANNUAL_TIMESTEPS",
    "BSDF_PROXY_MODIFIER",
    "GLOW_MODIFIER",
    "SAMPLE_MODIFIER",
    "SKY_MODIFIER",
    "LM83_BLIND_CONTROL",
    "AUTOMATED_SCHEDULE_INPUT_PREFIX",
    "AutomatedSchedule",
    "AutomatedScheduleRecordError",
    "ChainRequest",
    "ThreePhaseGroup",
    "ThreePhaseNotBuildableError",
    "SparseLm83GridError",
    "ThreePhaseTarget",
    "WINDOW_GROUP_INPUT_PREFIX",
    "WindowGroup",
    "automated_group_names",
    "automated_schedule_file",
    "automated_schedules",
    "is_automated",
    "blend_ill_id",
    "blend_ill_path",
    "blend_steps",
    "blind_schedule_id",
    "blind_schedule_path",
    "blind_schedule_steps",
    "direct_ill_id",
    "direct_ill_net_id",
    "direct_ill_net_path",
    "direct_ill_path",
    "direct_sun_groups",
    "direct_sun_steps",
    "five_phase_shape_check",
    "five_phase_steps",
    "group_state_ill_id",
    "group_state_ill_path",
    "refuse_colliding_outputs",
    "reuse_decision",
    "reused_ids",
    "sample_rad_paths",
    "states_by_group",
    "three_phase_shape_check",
    "three_phase_steps",
    "three_phase_target",
    "two_phase_steps",
]

#: Hours in the annual result `run_annual` produces -- the column count of the `.ill`, and the number
#: check 4.7 asserts the shape against (`n_points x 8760`).
#:
#: **Used only to state the expected shape in the plan and to size the estimate, never to bound a
#: read.** Ruling R47 is the reason that distinction is written down here: `weather`'s `timesteps`
#: was a hardcoded `8760` beside a comment saying "8760 for a complete year", and a 24-hour file, a
#: truncated file and an **8784-hour leap year** all reported a complete common year through it. The
#: real column count of a real `.ill` is whatever `gendaymtx` wrote from the `.wea` actually loaded,
#: and `results` (Task 3) reads it from the matrix header rather than from this constant. Nothing
#: here compares a measured count against it.
#:
#: Re-exported by `rad_mcp.annual_tool`, which published it first. **`AnnualResult.timesteps` moved
#: off this constant in Phase 7a Task 14's review round 1** (Important 5): that field now publishes
#: `state.weather.n_hours`, the real measurement, once `annual_tool._weather` had one to publish --
#: reporting this constant instead would have stated `8760` against a matrix a shorter or longer
#: `.wea` is about to build a different column count for, which is the exact failure ruling R47
#: closed for `weather`'s own `timesteps` field. This constant remains `three_phase_shape_check`'s
#: own prediction of S's column count (still true for every project this server has ever run against
#: in practice, since nothing before Task 14 measured a real hour count to disagree with it) and
#: `ChainRequest.n_hours`'s default for the callers that never set it (`two_phase_steps`/
#: `three_phase_steps`, which never read the field at all).
ANNUAL_TIMESTEPS = 8760

#: `-c` for the D matrix's `genklemsamp`/`rcontrib` pipe: rays sampled per Klems incident direction,
#: passed as the **same variable** to `build_genklemsamp_argv` and `build_d_argv` (both docstrings:
#: `man genklemsamp`'s and `man rcontrib`'s own worked examples reuse one `-c` on both sides, and a
#: mismatch is a silent error rather than a loud one).
#:
#: **1000, and the number is a stated engineering choice rather than a sourced constant** -- no spec
#: in this repo names a count. `tests/test_matrices.py` uses 20 and says why: it asserts the pipe's
#: *shape* (145 rows x 438 values), for which any count works, and a larger one only costs wall
#: clock. A real D matrix is a different problem: each of the 145 rows is a Monte Carlo estimate of
#: what one Klems incident direction sees of the sky, and 20 rays per direction is far too few for
#: the numbers to mean anything. 1000 is the order `man genklemsamp`'s own worked example uses
#: (`-c 500`) rounded up one step, and it puts this pipe's cost in the same range as the V matrix's
#: `rcontrib` trace rather than making it the free step in a chain whose other steps are minutes.
#:
#: Named once here rather than at the two call sites, which is what makes the "one variable, not two
#: typed literals" discipline structural instead of a thing a reader has to check.
_KLEMS_SAMPLE_COUNT: int = 1000

#: `-lw` for the V and D matrix traces -- `rad_engine.matrices`' own explicit keyword rather than a
#: field on `QualityParameters`, for the reason that module's docstring gives: `QualityParameters`
#: *"deliberately never populates `-lw` or `-dj`"*, and adding a field for it would change a frozen
#: dataclass three shipped tools already resolve.
#:
#: **1e-4, taken from check 6.5's own discriminating argv** (`rad_engine.matrices`' module docstring:
#: "the cheapest setting at which the correct arm is measurably nonzero"). That is the one value in
#: this repo that was derived by measurement against a real V matrix rather than chosen, so it is the
#: one to reuse; check 6.4's `-lw 0.1` is explicitly a shape-only setting that cannot even tell a
#: correct Klems basis from a reversed one.
_MATRIX_LIMIT_WEIGHT: float = 1e-4


def matrix_limit_weight(parameters: QualityParameters) -> float:
    """The `-lw` a matrix trace runs at: `_MATRIX_LIMIT_WEIGHT`, or `1/ad` where that is stricter.

    **Engine invariant 4 -- `lw` tracks `ad` -- and the whole-branch review's Minor 5.** `-lw` was
    the one flag in this module that a caller's `quality` could not move, while `-ad` came straight
    from it. At every shipped preset the fixed `1e-4` is the *stricter* of the two (`standard` is
    `-ad 1024`, so `1/ad = 9.8e-4`), so the invariant held -- but by accident, not by construction.
    Past `-ad 10000` the fixed value becomes the **looser** one and rays start terminating before
    the sampling the caller paid for can matter. That regime is reachable today: `quality={"ad":
    65536}` is a legal override, and it is precisely where [five-phase-method.md](../../docs/specs/
    five-phase-method.md) §4.3 points for `Cds` (`-ad 65536 -lw 1.52e-5`, and `1/65536 = 1.526e-5`).
    `AMBIENT_BOUNCES_INEFFECTIVE` would be the advisory for it and has no emitter, so nothing warned
    either. `rad_engine.rtrace.parameters_dict`'s own crossover note describes the same crossover
    from the other side.

    **`min`, not `1/ad`.** Taking `1/ad` outright would *loosen* every shipped preset -- `standard`
    would go from `1e-4` to `9.8e-4` -- which moves every matrix this server has ever built and
    every reference value measured against them. This function only ever tightens: below the
    crossover it returns exactly the `1e-4` that has always been used, so no preset's bytes move,
    and above it the caller gets the `-lw` their own `-ad` implies.
    """
    return min(_MATRIX_LIMIT_WEIGHT, 1.0 / parameters.ambient_divisions)

#: The modifier the aperture's `glow` primitive is written under for the V matrix, and the `-m` value
#: `build_v_argv` restricts that trace to. Formatted with the group name so two groups in one project
#: get two distinct, traceable surfaces rather than one primitive standing in for both -- the same
#: reason `rad_engine.klems.glow_surface` names one primitive per aperture polygon.
#:
#: Public, because the protocol layer reads it too: `run_annual` writes the glow `.rad` this modifier
#: heads into the interior octree at submit time, and `build_v_argv` below restricts the trace to the
#: same name. One constant, so the surface in the octree and the `-m` in the argv cannot drift.
GLOW_MODIFIER = "wg_{group}_glow"

#: The modifier the aperture's plain `polygon` primitive is written under for `genklemsamp` to sample
#: over. **A separate name from `GLOW_MODIFIER`, and separate on purpose**: `rad_engine.klems.
#: polygon_surface`'s docstring states that the V and D matrices are separate octrees and its text is
#: never combined with `glow_surface`'s. Two names make a scene that accidentally contained both
#: diagnosable rather than silently ambiguous to `-m`.
#:
#: Public for `GLOW_MODIFIER`'s reason: the protocol layer writes the sampled `.rad` (the write
#: itself needs ruling R48's containment primitive, which lives above this layer), and step 2's argv
#: below reads that same file.
SAMPLE_MODIFIER = "wg_{group}_sample"

#: The `-m` modifier of the exterior octree's own "white sky" glow, which `build_d_argv` requires:
#: `rcontrib` with no `-m`/`-M` at all exits 1 with `fatal - missing required modifier argument`
#: (measured, `rad_engine.matrices`' docstring). Its primitive is written by
#: `rad_engine.klems.sky_dome_surface`, which takes this name as its `modifier`.
#:
#: **A name, not a primitive.** The `.rad` text this modifier heads used to live in `rad_mcp` as a
#: module constant -- the only Radiance scene syntax anywhere in `src/rad_mcp/`, and a layer-rule
#: violation: CONTEXT.md gives `rad_engine` *"Builds commands, runs binaries"* and `rad_mcp`
#: *"No physics"*, and (as this project designs it) a primitive's argument-line arity is Radiance
#: knowledge (the `rad_engine.klems`
#: module docstring records that the spec's three-real `4 1 1 0` form is a fatal `oconv` error,
#: measured).
#: It now sits beside `glow_surface` and `polygon_surface`, where the sibling reasoning already
#: lived.
SKY_MODIFIER = "annual_sky_glow"

#: The modifier the aperture's tensor-tree `BSDF` proxy surface is written under in the suns octree
#: (`rad_engine.klems.bsdf_proxy_surface`, `docs/specs/five-phase-method.md` 1.1/4.2). Formatted with
#: the group name for `GLOW_MODIFIER`'s reason: one named, traceable surface per group rather than one
#: primitive standing in for several.
#:
#: **Unlike its three siblings above, no argv in this module reads it, and that is a fact about the
#: method rather than an oversight.** `Cds` is `rcontrib ... -m solar` (`rad_engine.suns.
#: SUNS_MODIFIER`, `build_cds_argv`): it gathers contribution from the *suns*, and the proxy is the
#: surface those rays pass **through** on the way, never a modifier the trace names. The constant
#: lives here anyway, beside the other three, because it is read at exactly the same call site they
#: are -- `rad_mcp.annual_tool`'s submit-time scene writes -- and a fourth modifier name invented at
#: that call site is how a scene comes to declare one identifier and reference another.
BSDF_PROXY_MODIFIER = "wg_{group}_bsdf_proxy"

#: The one `blind_control` value under which anything chooses between a window group's bound BSDF
#: states -- LM-83 section 2.2.6's hourly schedule, derived from a zero-bounce direct-sun run.
#:
#: **Named here, in the layer that takes the refusal, and re-exported by `rad_mcp.annual_tool`
#: rather than spelled a second time there.** `three_phase_target` resolves a multi-state group only
#: for this value and `_dispatch` plans the direct-sun leg only for this value; those are one
#: decision, and two independent `"lm83"` literals are two statements of it that can drift apart
#: (ruling R30). The protocol layer published the constant first (`LM83_BLIND_CONTROL`), which now
#: reads this one.
LM83_BLIND_CONTROL: Final[str] = "lm83"


@dataclass(frozen=True)
class AutomatedSchedule:
    """An automated group's bound hourly schedule, as the blind-schedule step names it (R870).

    `window_group(action="bind_schedule")` registers it as input `automated_schedule_<g>`, one file
    under `05_bsdf/automated_schedules/` with its digest. `input_id` is the `sched:<grid>` edge,
    `path` the helper's `--automated-schedule` operand and `digest` the value the helper compares
    the bytes it reads against before it parses them. The row count is not carried: the helper
    reads the file against its own `--n-hours`, and `run_annual`'s plan-time read checks it against
    the run's weather without keeping it (final review: the field was written and never read).
    """

    input_id: str
    path: Path
    digest: str


class WindowGroup:
    """One `window_group` input of a project, as the annual chains need to see it.

    **Three facts, from three different places, and the split is the point.** The name and the
    aperture `.rad` files are *stored* on the `InputRecord`; the states are *derived* by scanning
    artifact ids for `tmx:<group>:<state>`; the geometry is *re-derived* by running the same
    `rad_core.apertures.derive` that `window_group(action="define")` ran. `InputRecord` has no field
    for either of the last two and this phase does not add one -- it is a persisted schema, and a
    bump for a fact that is recoverable at read time buys nothing.

    **Re-derivation is not a workaround; it is the stronger guarantee.** A normal cached at `define`
    time goes stale the moment the `.rad` file behind it is edited, and nothing in the manifest
    would notice: the input's digest would move, but the cached vector would not. A normal
    re-derived from the file on this call cannot disagree with the file on this call. The cost is
    one `read_scene` and one pure derivation per group per `run_annual`, both of which that tool
    already pays for the octree it builds a few lines later.

    `aperture` carries the whole `Aperture`, not just the normal: Task 9's `annual_dgp`-on-a-
    horizontal-opening refusal read `rad_core.apertures.is_horizontal(aperture)` until R815 withdrew
    the metric, and Task 6's
    3-phase step list needs the same object the Klems binning was computed from. Carried once here
    rather than re-derived at each consumer, which is how two call sites come to disagree.

    Built by `rad_mcp.annual_tool._window_groups`, which is where the manifest is read; it lives
    here because both sides of the seam name the type -- the protocol layer's fenestration refusals
    and this module's `three_phase_target` -- and `rad_engine` may not import the layer above it.
    """

    def __init__(
        self,
        *,
        name: str,
        input_id: str,
        files: tuple[str, ...],
        states: tuple[str, ...],
        aperture: Aperture,
        closing_role: str | None = None,
        automated: bool = False,
        automated_schedule: AutomatedSchedule | None = None,
    ) -> None:
        self.name = name
        self.input_id = input_id
        self.files = files
        self.states = states
        self.aperture = aperture
        # LM-83 section 2.2.6's DISCUSSION advises closing "view" window groups first and
        # "clerestory" groups last, and nothing in the geometry tells the two apart -- so this is
        # the caller's own declaration, carried straight from `InputRecord.role`
        # (`rad_mcp.window_group_tool`'s `closing_role` argument, ruling R133), not derived here.
        # Named `closing_role` rather than `role`, deliberately: a bare `role` beside `states` (BSDF
        # states) reads as the BSDF-chain role `window_group(action="assign_bsdf")` already owns,
        # and this is a different vocabulary the two tools' shared name would otherwise collide on.
        self.closing_role = closing_role
        # `define`'s automated mark (R869) and the schedule `bind_schedule` bound for it (R870),
        # `None` when none was bound -- the group is then held closed every hour. An automated
        # group leaves `blind_schedule_steps`' closing order; its states are sealed, not scanned.
        self.automated = automated
        self.automated_schedule = automated_schedule


@dataclass(frozen=True)
class ChainRequest:
    """Everything a chain builder needs, with no `rad_mcp` type in it.

    `rad_mcp.annual_tool._ValidatedProject` is a protocol-layer aggregate; `rad_engine` importing it
    would invert the layer rule CONTEXT.md sets. The protocol layer builds one of these instead
    (`annual_tool._chain_request`), which is the whole of the seam.

    **The three job-local paths are given rather than derived**, because the protocol layer has to
    write two of them before the steps exist: ruling R48's containment primitive raises
    `rad_mcp.paths.PathOutsideRoot`, which this layer may not name. `points_path` and
    `sample_rad_paths` are therefore resolved once, by the writer, and read here; `job_dir` is carried
    for the one job-local file nothing writes at submit -- `genklemsamp`'s rays, which step 2 creates
    as its `stdout_path` and step 3 consumes as its `stdin_path`.
    """

    loaded: LoadedProject
    """The manifest as it stands at submit. Read for the geometry, materials and weather input ids
    the edge sets name, and by `reuse_decision` for the stored artifacts."""
    root: Path
    """The project root -- `cwd` for every step, and where the 2-phase receiver file lands."""
    grid: str
    """The sensor grid's handle. Half of every artifact id and matrix filename this chain writes."""
    grid_input_id: str
    """The grid's *manifest id*, which is what `built_from` names (`annual_tool._grid_input_id`)."""
    scene_files: list[str]
    """The registered scene `.rad` files, as `build_rfluxmtx_argv` takes them."""
    octree_id: str
    octree_path: Path
    exterior_octree_id: str
    exterior_octree_path: Path
    points_path: Path
    """The job-local sensor-points file, already written by the caller (R43: `rfluxmtx` and
    `rcontrib -I+` take rays only on stdin)."""
    sample_rad_paths: Mapping[str, Path]
    """The job-local plain-`polygon` aperture geometry `genklemsamp` samples over, **one file per
    window group**, keyed by group name. Already written by the caller. Empty for the 2-phase chain,
    which samples nothing.

    **A mapping rather than one path, and that is a defect fix rather than a generalisation (Phase
    7d-1 Task 7, review round 1's Important 1).** It was a single `Path`, written from one group's
    aperture, and `_genklemsamp_step` passed it for **every** group -- so a non-first group's rays
    were sampled over another facade's polygons and its D matrix carried another facade's view of
    the sky. That is the `GLOW_MODIFIER` hazard one step over: `genklemsamp` reads whatever geometry
    it is given, `rcontrib` bins the rays it is handed, the matrix is the right shape, and
    `check_three_phase_shapes` compares dimensions that all still agree. A valid file, a plausible
    number, no error anywhere.

    Keyed by **name**, so `_genklemsamp_step` can only ever ask for the group it was handed, and a
    group the writer did not write for is a loud `ThreePhaseNotBuildableError` at plan time rather
    than a silent substitution -- see `_sample_rad_for`."""
    job_dir: Path
    n_sensors: int
    parameters: QualityParameters
    weather_input_id: str
    weather_path: Path
    ground_reflectance: ProvenanceValue
    north: float
    mf: int
    """`gendaymtx -m`, after the LM-83 floor was applied -- the subdivision this run resolved."""
    matrices_dir: Path
    results_dir: Path
    target: ThreePhaseGroup | None = None
    """The resolved 3-phase window group, state and sealed T -- `Vd` and `Dd` (Task 9) share V and
    D's own aperture, glow modifier and sample geometry exactly as
    `docs/specs/five-phase-method.md` 3.2-3.3 do ("Similar to V, but..." / "...using the
    model_allblack.oct"), so `five_phase_steps` needs the same `ThreePhaseGroup` `three_phase_steps`
    already takes as an explicit second argument.

    **A `ThreePhaseGroup`, not the whole `ThreePhaseTarget` (ruling R126).** `three_phase_target`
    now resolves every group a project registers; this field is the *one* group the chain reached
    from here multiplies, and the caller narrows to it with `ThreePhaseTarget.single`, which is
    where the multi-group refusal now lives.

    **A field here rather than a second parameter on `five_phase_steps`, and that is a deliberate
    departure from `three_phase_steps`'s own two-argument shape.** Every task snippet in
    `docs/superpowers/plans/2026-08-17-phase-7a-five-phase-chain.md` that calls
    `five_phase_steps` -- Tasks 9, 11, 12, 15, 16 -- calls it with the bare `request` and nothing
    else, and Task 12's own snippet threads *more* state through the same value
    (`five_phase_steps(replace(request, n_hours=3))`). That is a plan-wide pattern, not one task's
    typo, and it reads as this dataclass's own docstring's promise -- *"everything a chain builder
    needs"* -- taken literally for the chain that needs the most. `two_phase_steps` and
    `three_phase_steps` are unchanged and never read this field.

    `None` for every caller that does not build the 5-phase chain. **`_chain_request` (the production
    seam in `rad_mcp.annual_tool`) sets it as of Phase 7a Task 14b** -- from the same
    `three_phase_target` call `_dispatch` already made for both methods -- so it is `None` only for a
    2-phase run, where no window group was resolved at all. `two_phase_steps`/`three_phase_steps`
    still never read this field (the latter takes its target as an explicit second argument), so
    their construction is untouched either way. `five_phase_steps` raises immediately if it is still
    `None` when called, rather than reading through it into an `AttributeError` on a `None`."""
    grid_points: tuple[tuple[float, float, float], ...] = ()
    """The grid's sensor positions (R846), for the facing `_v_step` records on each V matrix as
    `grid_behind_aperture`. `annual_tool._chain_request` passes them; empty, nothing is recorded."""
    processes: int | None = None
    """`-n` for every `rcontrib`/`rfluxmtx` step (R781): `annual_tool._chain_request` passes
    `rad_engine.cores.worker_cores()`. Recorded as `n` in each such artifact's `parameters`, which
    `DIGEST_EXCLUDED_PARAMETERS` keeps out of the digest and `_parameters_match` out of reuse."""
    black_octree_id: str | None = None
    black_octree_path: Path | None = None
    """`build_black_octree`'s (Task 8) own artifact id and path -- the all-black octree
    `docs/specs/five-phase-method.md` 3.1 states is shared by both `Vd` and `Dd` ("This octree will
    be used for both Vd and Dd"). `None` for the 2-phase and 3-phase chains, which never build it;
    see `target`'s docstring immediately above for why this is a field on the request rather than a
    third positional argument.

    **Populated in production as of Phase 7a Task 14b.** `rad_mcp.annual_tool._dispatch` compiles it
    at submit time -- above the `dry_run` branch (R39), through Task 8's guarded entry point
    (R44(3)) -- from the registered scene blackened by `xform -m black`, plus the `black` definition
    itself (`rad_engine.octree.black_material`) and the same V-matrix glow and D-matrix sky-dome
    surfaces the 3-phase octrees carry, which 3.1's own `oconv` line adds unblackened."""
    suns_octree_id: str | None = None
    suns_octree_path: Path | None = None
    """`build_suns_octree`'s (Task 8) own artifact id and path -- the suns octree
    `docs/specs/five-phase-method.md` 4.2 builds for `Cds` alone: the same all-black opaque shell
    `black_octree_path` uses, plus the unblackened fenestration geometry, the tensor-tree BSDF proxy
    surface and the named suns scene (`rad_engine.suns.build_suns_argv`). A field here rather than a
    third octree `five_phase_steps` builds itself, for `black_octree_id`'s own reason immediately
    above: this function has no file-write access (module docstring, "Not here: any file write"), and
    `Cds`'s octree needs a BSDF-proxy `.rad` and a suns-scene `.rad` written before `oconv` ever runs
    -- writes this module may not perform. `None` for every chain that does not build `Cds`, which is
    every chain but this one today.

    **Populated in production as of Phase 7a Task 14b**, by the same submit-time block that builds
    the black octree: `rad_mcp.annual_tool._dispatch` writes the proxy
    (`rad_engine.klems.bsdf_proxy_surface`, over the tensor tree the manifest records at
    `bsdf_<group>_<state>_direct_sun`) and the suns scene (`suns_scene_text`, at
    `suns_scene_filename(mf)`), both through ruling R48's containment primitive, then compiles them
    with `build_suns_octree`."""
    ase_octree_id: str | None = None
    ase_octree_path: Path | None = None
    """`rad_engine.octree.build_ase_octree`'s (Phase 7c Task 9) own artifact id and path -- the
    octree LM-83's annual sunlight exposure is traced on (sec 3.3): **the registered scene, whole,
    plus the suns scene.** `None` for every chain but `direct_sun_steps`, which raises rather than
    reading through a `None`.

    **A third octree pair on this dataclass rather than an argument, for `black_octree_id`'s own
    reason immediately above**: this module performs no file write (module docstring, *"Not here:
    any file write"*), and the ASE octree needs the suns scene `.rad` written before `oconv` runs.
    Task 11's submit-time block compiles it, above the `dry_run` branch, through R44(3)'s guarded
    entry point -- the same place the black and suns octrees are already built.

    **Not `suns_octree_path`, and the difference is not a detail.** `build_suns_octree`'s output is
    the all-black opaque shell plus a tensor-tree BSDF proxy, built for `Cds` at `-ab 1`; this one
    is the registered scene with its own materials and its own fixed shading, traced at `-ab 0`.
    Substituting one for the other compiles, runs, and publishes a plausible ASE -- see
    `build_ase_octree`'s own docstring for each difference and why it is required rather than
    chosen."""
    n_hours: int = ANNUAL_TIMESTEPS
    """The real column count of the weather this run loaded -- what `five_phase_steps`' own final
    `rcollate -hi -fa1 -ic n_hours` reshapes the combined result into (`docs/specs/five-phase-method.md`
    sec 5; `rad_engine.annual.build_rcollate_final_argv`'s own docstring for the full defect this
    guards against). **Never read as `ANNUAL_TIMESTEPS`'s own docstring already forbids** -- that
    constant states plainly it is *"never to bound a read"* -- so the default here exists only so
    `two_phase_steps`/`three_phase_steps` (which never read this field) and every fixture built before
    this field existed stay unaffected by its addition, never as a claim that a real project has 8760
    hours.

    **Wired as of Phase 7a Task 14 -- this docstring's own instruction, closed rather than left
    stale.** `annual_tool._chain_request` now passes `n_hours=state.weather.n_hours` on its one
    production call site, and `state.weather.n_hours` is a real measurement
    (`annual_tool._measured_n_hours`: the registered `.epw` when it can be read, the registered
    `.wea`'s own line count when it cannot -- review round 1's Important 4 found the first cut's
    unconditional `.epw` re-read made every annual call depend on an external file this server never
    owns, and the fallback closes that without weakening the measurement). `two_phase_steps`/
    `three_phase_steps` still never read this field, so the default above still matters for them and
    for every fixture built before this field existed.

    **The consumer arrived at Phase 7a Task 14b, and this paragraph is the record of it rather than
    of the gap it used to describe.** `_chain_request` builds a `ChainRequest` carrying the real
    `n_hours` regardless of method; until 14b nothing called `five_phase_steps` with it, because
    `_dispatch` refused every 5-phase-inferred call before `ChainRequest` was even constructed (the
    two octrees `Vd`/`Dd`/`Cds` trace against had no production builder). 14b built that caller, so a
    real `run_annual(method='5phase')` now reaches this field's real value and the final `rcollate
    -hi -fa1 -ic n_hours` reshapes into the column count of the weather this run actually loaded
    (`-t` was dropped by ruling R98, Phase 7b Task 1 -- the legs' own `-oc 1` already leaves the
    stream sensor-major, and transposing it again is what made the five-phase `.ill` unreadable).
    `tests/test_five_phase.py` and `tests/test_chain.py` still set it directly via
    `dataclasses.replace` (Task 15's own micro scene, 3 hours), which is how a chain test moves it
    without a weather record; `tests/test_annual_tool.py::TestNHoursIsWiredFromTheRealWeather` is
    still what pins the seam itself."""
    ase_octree_ids: Mapping[str, str] = field(default_factory=dict)
    ase_octree_paths: Mapping[str, Path] = field(default_factory=dict)
    """The **per-window-group** ASE octrees (Phase 7d-1 Task 8), keyed by group name: the registered
    scene with every *other* group's aperture polygons blacked (`rad_engine.scene.build_ase_scene`),
    compiled by `rad_mcp.annual_tool` at submit time exactly where the four octrees before it are.

    Empty for every run that is not `blind_control="lm83"` (`direct_sun_groups`) and for a 2-phase
    run, which resolves no window group at all. A five-phase `lm83` run fills them though it plans
    no schedule, and `direct_sun_steps` traces none of them there (Phase 25 final review A2).
    `ase_octree_id`/`ase_octree_path` above are a **different** octree and are still required: they
    are the whole-grid ASE run, every aperture open, which is the pre-blind quantity LM-83 sec 3.3
    defines ASE over. Substituting one for the other compiles, runs, and answers a different
    question. A run with a window group marked automated has one octree more, `ase_net_octree_id`
    below (R869): the whole grid with the automated groups' apertures blacked and every other
    aperture open, which is neither of these.

    **Two mappings rather than one of pairs**, matching `sample_rad_paths`' own shape one leg over,
    and keyed by name for its reason: `_ase_octree_for` can only ask for the group it was handed, and
    a group the caller compiled no octree for is a loud `ThreePhaseNotBuildableError` at plan time
    rather than a silent trace against another facade's scene."""
    ase_suns: tuple[SunPosition, ...] | None = None
    """The Reinhart patch centres this run's ASE octrees carry a `source` at --
    `rad_engine.suns.sun_directions(env, mf)`, the **whole** dome, read back out of the same suns
    scene the octrees compile (ruling R144).

    Carried to each per-group `asemx` so `rad_engine.project.register_artifact`'s sun-geometry guard
    can run: that guard was built by Task 18 and was **unreachable**, because it needs an aperture
    and `asemx:<grid>` is grid-scoped. It refuses a filtered list (`len(suns) == sun_count(mf)`) and
    refuses an empty one, so this is the full dome or `None`.

    Also the directions every `asemx`'s occlusion probe casts along (ruling R831,
    `rad_engine.jobs.AseOcclusionProbe`), so `rad_mcp.annual_tool` sets it on every run that plans
    the leg, not only on one that fans out -- a two-phase run included, whose whole-grid probe
    strips every glazing surface (ruling R856).

    **The frame is the scene's, not true north's** (`SunPosition`'s own docstring). `north` rotates
    the *sky* relative to the model and these directions know nothing about it, which is a live
    limitation rather than a resolved one -- see this module's `direct_sun_steps` for what it costs.
    """
    ase_suns_scene: Path | None = None
    """The suns scene the whole-grid ASE octree was compiled with (`rad_mcp.annual_tool` writes
    it). The occlusion probe recompiles that octree from the files as they are at commit (R848)."""
    ase_scene_digest: str | None = None
    """The whole-grid ASE octree's submit-time body digest (R822's snapshot). The occlusion probe
    gives no verdict when the recompiled octree's differs (R848)."""
    ase_net_octree_id: str | None = None
    """The manifest id of the ASE_net octree (ruling R869): the registered scene with every
    aperture polygon of the window groups marked automated blacked
    (`rad_engine.scene.build_ase_net_scene`) and every other aperture open, compiled with the same
    suns scene as the whole-grid ASE octree. `None` on a run with no automated group, which plans
    no net leg; `direct_sun_steps` refuses a run that names automated groups (`ase_net_groups`)
    without it."""
    ase_net_octree_path: Path | None = None
    """The path of the ASE_net octree `ase_net_octree_id` names, which the net leg's `rcontrib`
    traces. `None` exactly where that id is; `direct_sun_steps` refuses a run that names automated
    groups without it."""
    ase_net_groups: tuple[str, ...] = ()
    """The names of the window groups marked automated (R869), whose apertures the ASE_net octree
    blacks. Empty plans no net leg, so an unmarked project plans what it always planned."""
    ase_net_apertures: tuple[Aperture, ...] = ()
    """The automated groups' apertures, in `ase_net_groups` order: what the ASE_net matrix's
    occlusion probe closes (`AseOcclusionProbe.closed`)."""


def _processes_parameter(request: ChainRequest) -> dict[str, int]:
    """`{"n": count}` for an artifact whose step passed `-n`, else `{}` (R781). One function, so the
    recorded key and the argv cannot disagree about whether a count was passed."""
    return {} if request.processes is None else {"n": int(request.processes)}


# --- the artifact edge sets ---------------------------------------------------------------------


def _dc_edges(request: ChainRequest, octree_id: str) -> tuple[str, ...]:
    """R44(2)'s edge set for the `dsmx`, **named in full**.

    Geometry, materials, sensor grid, octree artifact. `REQUIRED_EDGES` has no `dsmx` row, so
    `check_edges` returns early after proving each named edge resolves -- and *"`check_edges`
    returning early is not permission to under-record"*: the table's own preamble warns that an
    artifact sealed with an incomplete edge set is "reported valid and reused forever". A DC matrix
    genuinely depends on all four -- change a wall's reflectance, move a sensor, or recompile the
    scene, and every coefficient in it is stale -- so all four are recorded whether or not anything
    currently enforces it.

    No `dsmx` row is added to `REQUIRED_EDGES`: that table is derived from the spec's invalidation
    table, and a row not in that table is a deviation needing its own sourcing (R44(2)).

    The weather input is deliberately **not** an edge of the DC matrix: a daylight-coefficient
    matrix is sky-independent by construction -- that is the entire reason the method is worth
    running -- so a new EPW invalidates the sky matrix and the `.ill`, never the DC matrix. Binding
    it here would discard the one reuse 2-phase offers.
    """
    materials = [
        record.id for record in request.loaded.manifest.inputs if record.kind == "materials"
    ]
    geometry = [
        record.id for record in request.loaded.manifest.inputs if record.kind == "geometry"
    ]
    return (*geometry, *materials, request.grid_input_id, octree_id)


def _smx_edges(request: ChainRequest) -> tuple[str, ...]:
    """The annual sky matrix's edge set: the weather input, the project ground reflectance, and
    north where the project declares it.

    **This row is enforced**, unlike `dsmx`'s -- `REQUIRED_EDGES["smx"]` names all three and
    `check_edges` refuses a set missing any of them. That refusal fired on the first end-to-end run
    of `run_annual`, which is the value of checking edges at submit time rather than at seal time: it
    cost a validation error rather than an hour of matrix building.

    `north` is `ids_if_present`, so it is named only when the project actually declares the scalar
    -- *"it is synthetic and a project with no north has nothing to name"*. Naming it
    unconditionally would be a dangling edge, which `check_edges` refuses from the other direction.

    **Read off `input_digests()`, not `manifest.inputs` (R734).** North is a synthetic scalar: it
    reaches the graph only through `Manifest.input_digests()` (`_synthetic_input_digests`), and
    `register_input` refuses an id in the reserved `project:` namespace, so no `InputRecord` can
    ever carry it. A test of `NORTH_INPUT_ID` against the input records' own ids can therefore never
    hold, on any project -- every `smx`/`smxd`/`smxsun` built from a project declaring north came
    back missing the edge and refused by `check_edges` at `ARTIFACT_EDGES_INCOMPLETE`, synchronously
    at `JobStore.submit`, before any Radiance process spawned (measured through `run_annual` on a
    project declaring `north=90.0`; docs/plan.md R734 carries the refusal verbatim). `check_edges` itself already reads `input_digests()` for this same reason
    (`rad_engine.project.check_edges`); this function is the one builder that had drifted from it.
    """
    edges = [request.weather_input_id, GROUND_REFLECTANCE_INPUT_ID]
    if NORTH_INPUT_ID in request.loaded.manifest.input_digests():
        edges.append(NORTH_INPUT_ID)
    return tuple(edges)


def states_by_group(loaded: LoadedProject) -> dict[str, tuple[str, ...]]:
    """Which states each group has, scanned off the `tmx:<group>:<state>` artifact ids.

    **Derived, because nothing stores it.** `window_group(action='assign_bsdf')` seals one `tmx`
    artifact per bound state and records the state name in the id's third component; the group's own
    `InputRecord` gains nothing from that call. The id is the reliable key rather than the sealed
    filename, which joins the same two components with `_` -- a character legal inside both -- and
    is therefore not injective (`window_group_tool._refuse_filename_collision` refuses the
    collision for exactly this reason). `:` is outside the name charset, so a three-way split of the
    id cannot be ambiguous.

    Sorted, so the tuple order is a property of the project rather than of manifest insertion order.
    `infer`'s rules 2-4 read only membership and length, but a caller comparing two runs should not
    see a state list reorder itself.

    **It lives here, in `rad_engine`, because it has two readers in two `rad_mcp` modules and
    `rad_mcp` has no seam they can share.** It was `annual_tool._states_by_group` while `run_annual`
    was the only caller; owner ruling R242's sibling gave `results_tool` a reason to ask the same
    question -- *"does any group of this project bind more than one BSDF state?"*, which is what
    decides whether an unscheduled re-run is a call that can succeed -- and `annual_tool` already
    imports `results_tool`, so importing back would have made a one-way edge a cycle
    (`tests/test_purity.py`, and the whole-branch composition review measured that edge as one-way).
    Copying five lines into the second reader is the alternative ruling R30 exists to refuse. This
    module already owns the convention being scanned: `WindowGroup`'s own docstring says the states
    are derived by scanning `tmx:<group>:<state>`, and `three_phase_target` is what mints those ids.
    """
    states: dict[str, list[str]] = {}
    for artifact in loaded.manifest.artifacts:
        if artifact.kind != "tmx":
            continue
        parts = artifact.id.split(":")
        if len(parts) != 3 or parts[0] != "tmx":
            continue
        states.setdefault(parts[1], []).append(parts[2])
    return {group: tuple(sorted(names)) for group, names in states.items()}


#: The id prefixes `window_group` registers a group (`define`) and its bound schedule
#: (`bind_schedule`, R870) under. **Defined once, here** (ruling R30): `rad_mcp.window_group_tool`
#: (its `_INPUT_PREFIX` and `_SCHEDULE_PREFIX`) and `rad_mcp.annual_tool` import these, because
#: `rad_mcp` may import `rad_engine` and not the other way round.
WINDOW_GROUP_INPUT_PREFIX: Final[str] = "window_group_"
AUTOMATED_SCHEDULE_INPUT_PREFIX: Final[str] = "automated_schedule_"


class AutomatedScheduleRecordError(ValueError):
    """An `automated_schedule_<g>` record that does not name exactly one file (R870).

    `bind_schedule` always writes exactly one, so this is a hand-edited or damaged manifest (R50:
    untrusted). It is raised rather than read as "no schedule bound", which would hold the group
    closed every hour with no message about the schedule the caller bound.
    """


def _strip_prefix(record_id: str, prefix: str) -> str:
    return record_id[len(prefix):] if record_id.startswith(prefix) else record_id


def is_automated(record: Any) -> bool:
    """Whether a `window_group` input record carries `define`'s automated mark (R869).

    The mark is `registered_by.arguments["automated"]`, and only the value `True` is a mark: a
    record written before the key existed, or one carrying anything else (`"yes"`, `1`), reads as
    unmarked. The one definition of the mark: `automated_group_names` reads it here, and
    `rad_mcp.window_group_tool` imports it (final review, T-M7).
    """
    return record.registered_by is not None and record.registered_by.arguments.get(
        "automated"
    ) is True


def automated_group_names(loaded: LoadedProject) -> tuple[str, ...]:
    """The window groups `define` marked automated (R869, `is_automated`), sorted, without the
    input-id prefix."""
    return tuple(sorted(
        _strip_prefix(record.id, WINDOW_GROUP_INPUT_PREFIX)
        for record in loaded.manifest.inputs
        if record.kind == "window_group" and is_automated(record)
    ))


def automated_schedule_file(record: Any) -> FileRef | None:
    """The one file an `automated_schedule_<g>` record names, or `None` when it does not name
    exactly one (R870). The single rule both readers share: `automated_schedules` refuses the
    `None`, and `window_group(action="list")` shows it as no schedule."""
    return record.files[0] if len(record.files) == 1 else None


def automated_schedules(loaded: LoadedProject) -> dict[str, AutomatedSchedule]:
    """`{group: AutomatedSchedule}`, one per `automated_schedule_<g>` input (R870).

    Read off the manifest alone: the path and digest are the record's own file; nothing here reads
    the file. A record that does not name exactly one file raises
    `AutomatedScheduleRecordError` -- it is never read as "no schedule bound".
    """
    schedules: dict[str, AutomatedSchedule] = {}
    for record in loaded.manifest.inputs:
        if record.kind != "automated_schedule":
            continue
        ref = automated_schedule_file(record)
        if ref is None:
            raise AutomatedScheduleRecordError(
                f"the manifest's record {record.id!r} names {len(record.files)} file(s); "
                "window_group(action='bind_schedule') registers exactly one, the copy of the "
                "group's hourly schedule. The record was edited or damaged after it was written, so "
                "which hours that group's blinds are closed cannot be read from it."
            )
        schedules[_strip_prefix(record.id, AUTOMATED_SCHEDULE_INPUT_PREFIX)] = AutomatedSchedule(
            input_id=record.id, path=Path(ref.path), digest=ref.digest,
        )
    return schedules


def refuse_colliding_outputs(steps: Sequence[Step], loaded: LoadedProject) -> None:
    """Refuse a plan in which two steps write one file, **or in which a step writes a file another
    artifact already owns** -- review round 1's Important 2 and fix round 2's item 3.

    **Public as of Phase 7d-1 Task 8, and the reason is this docstring's own promise.** It said the
    check *"stays true when a later task mints a path this reasoning was never applied to"* -- and
    then that task arrived and the check could not see it. `three_phase_steps` calls this over its
    own list; the direct-sun leg is appended to that list afterwards by `rad_mcp.annual_tool`, and a
    group named `north` with a state named `direct` puts `ill:<grid>:north:direct` and
    `illdir:<grid>:north` at one path -- `08_results/annual_<grid>_north_direct.ill` -- in **one
    job**, two `dctimestep`s deep. Two ids, two manifest records, one file. So the composed plan is
    checked where it is composed, which needs this name outside the module.

    **The artifact ids are unambiguous and the filenames are not, and that asymmetry is the whole
    bug.** `ill:<grid>:<group>:<state>` joins its components with `:`, which is outside the name
    charset, so no two bindings share an id. The file it is written to joins the same components
    with `_`, which **is** legal in both a group name and a state name -- so `ill:grid:north:open_x`
    and `ill:grid:north_open:x` both resolve to `annual_grid_north_open_x.ill`. Two artifact ids,
    two manifest records, one file: whichever `dctimestep` runs second wins, and the other id
    describes bytes that are not its own. Measured, on this codebase, before this guard existed.

    **`rad_mcp.window_group_tool._refuse_filename_collision` is the same refusal, already in this
    repo**, for the same `_`-join over the same two components on the way to `tmx:<group>:<state>`'s
    sealed filename -- *"the id-to-filename mapping is not injective"*, in its own words. It could
    not be reused: it is a wire-layer function returning a `CallToolResult`, and this layer raises
    (`ChainRequest`'s own docstring on why the two layers are split). So its reasoning is matched
    rather than its code, which is ruling R138's shape applied to path construction.

    **Checked over every step's `stdout_path`, not over the `.ill` names alone**, so it is a
    property of the plan rather than an argument about today's format strings -- it stays true when
    a later task mints a path this reasoning was never applied to.

    ## Two collisions, and the first version of this guard could only see one (fix round 2, item 3)

    **Within one plan**, `<grid>` is one value, so `vmx_<group>_<grid>.mtx` is injective in the
    group and only the `.ill`'s `<group>_<state>` pair can collide. That argument was used to
    conclude the `.ill` was the only exposed path, and it is **wrong the moment a second run
    happens**: `07_matrices/` and `08_results/` are *project*-level, `vmx` is deliberately reused
    across runs, and `<grid>` therefore varies between the files sitting in them. Measured:
    `vmx:b_c:a` and `vmx:b:c_a` both resolve to `vmx_b_c_a.mtx`, and the second run would
    **overwrite a matrix the first run's manifest still describes**, with `MATRIX_REUSED` available
    to serve it afterwards.

    So the second half compares each artifact-producing step's path against the paths the manifest
    already records under a **different** id. That is the same two-directional shape
    `rad_mcp.window_group_tool._refuse_filename_collision` has -- within this call, and against what
    is already owned -- carried across rather than reinvented (R138).

    **A refusal rather than a rename, deliberately.** Making the filenames injective (a digest
    suffix, an escaped separator) would move every stored artifact's path and invalidate every
    matrix in every existing project, to close a collision that needs two specific names chosen to
    collide. Refusing costs a caller with two such names one rename and costs everyone else nothing.

    An artifact rebuilding at its **own** recorded path is not a collision -- that is what a rebuild
    is -- so the comparison is `existing != artifact_id`, exactly as the precedent's is. A manifest
    that recorded a relative path where this run resolves an absolute one simply does not match,
    which is a missed refusal and never a false one.
    """
    seen: dict[Path, str] = {}
    for step in steps:
        if step.stdout_path is None:
            continue
        previous = seen.get(step.stdout_path)
        if previous is not None:
            raise ThreePhaseNotBuildableError(
                f"two steps of this chain would both write {step.stdout_path.name}: "
                f"{previous!r} and {step.phase!r}, so two outputs of this plan name one file even "
                "though their artifact ids differ. Group and state names are joined with '_' to "
                "form result filenames and '_' is legal in both, so a (group, state) pair can name "
                "the file of another pair, of a window group's direct-sun result "
                "('annual_<grid>_<group>_direct.ill') or of the ASE_net leg's "
                "('annual_<grid>_direct_net.ill'). The second writer would overwrite the first, "
                "leaving one id describing the other's bytes. Rename one of the groups or states "
                "involved."
            )
        seen[step.stdout_path] = step.phase

    owned = {
        Path(artifact.path): artifact.id for artifact in loaded.manifest.artifacts
    }
    for step in steps:
        if step.artifact is None:
            continue
        existing = owned.get(step.artifact.path)
        if existing is not None and existing != step.artifact.artifact_id:
            raise ThreePhaseNotBuildableError(
                f"this run would seal {step.artifact.artifact_id!r} to "
                f"{step.artifact.path.name}, which artifact {existing!r} already owns. Group, "
                "grid and state names are joined with '_' to form matrix and result filenames and "
                "'_' is legal in all three, so two different bindings can name one file even "
                "though their ids differ -- and 07_matrices/ and 08_results/ are project-level, so "
                "the two need not be in the same run. Writing it would replace another artifact's "
                "matrix with bytes that are not its own while its manifest record still describes "
                "it. Rename one of the groups, grids or states involved."
            )


def sample_rad_paths(job_dir: Path, group_names: Iterable[str]) -> dict[str, Path]:
    """The job-local plain-`polygon` sampling file for each named window group -- **the naming
    convention, in one place** (ruling R30).

    Public, and it is the one thing about these files both layers have to agree on. The protocol
    layer writes them (`rad_engine.chain` performs no file write -- module docstring) and hands the
    result over as `ChainRequest.sample_rad_paths`; `_sample_rad_for` looks up in that same dict. A
    caller that spelled the filename itself would be a second derivation of one convention, which is
    how the writer and the chain come to name two different files and the step opens one nobody
    wrote.

    One variable in the name, so the `_`-join collision `refuse_colliding_outputs` exists for cannot
    arise here: two group names produce two filenames if and only if they differ, and Task 6's
    duplicate-name refusal already guarantees they do.
    """
    return {name: job_dir / f"aperture_sample_{name}.rad" for name in group_names}


def _sample_rad_for(request: ChainRequest, group: WindowGroup) -> Path:
    """The plain-`polygon` aperture file `genklemsamp` samples **this group** over.

    **One lookup, by name, and it raises rather than falling back** -- review round 1's Important 1.
    `ChainRequest.sample_rad_paths` used to be a single `Path` that every group's `genklemsamp` was
    handed, so a second group sampled the first group's polygons and its D matrix described the
    wrong facade's view of the sky. Nothing downstream can see that: the ray count is the same, the
    Klems binning is the same, the matrix is the right shape, and `check_three_phase_shapes` joins
    dimensions that all still agree.

    A missing key is a **caller** defect -- the protocol layer writes these files and hands over the
    paths (`ChainRequest`'s own docstring), so a group with no file means the writer and the chain
    disagree about which groups this job is for. Refused loudly at plan time, before an octree is
    compiled, instead of being answered with whichever path happened to be there.
    """
    try:
        return request.sample_rad_paths[group.name]
    except KeyError:
        raise ThreePhaseNotBuildableError(
            f"no aperture sampling geometry was written for window group {group.name!r}: this job "
            f"has files for {sorted(request.sample_rad_paths) or 'no groups at all'}. genklemsamp "
            "samples the polygons it is given, so running this group's daylight matrix against "
            "another group's file would trace the wrong facade and report a plausible number for "
            "it. The caller that writes the sampling geometry and the chain that names it must "
            "cover the same groups."
        ) from None


def _sun_disk_sky_step(request: ChainRequest) -> Step:
    """The `gendaymtx -m mf -5 0.533 -d` step that seals `smxsun:<weather>` -- **one definition, two
    callers.**

    `five_phase_steps` builds this as leg 3's `Ssun`; `direct_sun_steps` multiplies the very same sun
    matrix against `asemx`. Sealed under one artifact id, they must be one command: a second,
    independently written `SkyMatrixSpec` that disagreed about `mf`, the ground reflectance or north
    would produce a *different* matrix under an id the other chain then happily reuses, and nothing
    downstream could tell which of the two wrote the bytes on disk.

    **Why `direct_sun_steps` plans it at all, against what the phase plan said.** The plan has the
    direct leg merely consuming an `smxsun` *"already sealed by `five_phase_steps`"*. That is false
    for the path this phase needs: Task 11 plans the direct leg whenever `"ase"` is among the metrics
    or `blind_control == "lm83"`, **including on a three-phase run**, which never calls
    `five_phase_steps` and therefore seals no sun matrix at all. A leg that could not be built on a
    three-phase project would be unreachable for most of the runs that need ASE.

    **No second reuse mechanism, and none is needed.** Planning the step is not the same as running
    it: `reuse_decision` compares this spec's parameters against the stored artifact's
    (`_parameters_match`) and asks `is_reusable`, and a matrix that is already current comes back
    with `Step.reuse` set so `JobStore._run` declines to spawn it (ruling R65). That is the shipped
    mechanism for exactly this case and it is left to do its job.

    **A caller that concatenates both legs into one job gets this step twice**, once from each
    chain, and must drop the duplicate -- two `Step`s sealing one artifact id in a single plan is
    not a wrong answer (both are the identical command writing the identical path) but it is a
    duplicated 45 s and a plan row a caller cannot make sense of. The de-duplication is the
    assembling caller's, since only it knows which legs it combined; it is a filter on
    `step.artifact.artifact_id` over the concatenated list. **Task 11 wrote it:
    `rad_mcp.annual_tool._append_leg`**, which drops an identical duplicate and *refuses* two
    non-identical steps sealing one id -- that second case would be two different matrices under one
    name, a wrong answer rather than a wasted spawn.

    **The phase string names the matrix, not a method.** It read "the five-phase direct-sun matrix
    (Ssun)" until Phase 7c Task 11; this step is planned on three-phase runs too, and a client
    polling `job(action="status")` on one of those would have read a phase naming a method the run
    does not use.
    """
    spec = SkyMatrixSpec(
        weather_path=request.weather_path,
        sky_subdivision=request.mf,
        ground_reflectance=request.ground_reflectance,
        north=request.north,
        mode="sun_disk",
    )
    return Step(
        phase="building the sun-disk sky matrix (Ssun)",
        argv=tuple(build_gendaymtx_argv(spec)),
        cwd=request.root,
        stdout_path=request.matrices_dir / f"smxsun_{request.weather_input_id}.mtx",
        stdin_path=None,  # DEVNULL: gendaymtx takes the .wea as a positional argument.
        artifact=ArtifactSpec(
            artifact_id=f"smxsun:{request.weather_input_id}",
            kind="smxsun",
            path=request.matrices_dir / f"smxsun_{request.weather_input_id}.mtx",
            built_from=_smx_edges(request),
            parameters={
                "mf": request.mf,
                "north_east_of_north": request.north,
                "ground_reflectance": request.ground_reflectance.value,
                "mode": "sun_disk",
                "solar_disk_degrees": SOLAR_DISK_DEGREES,
            },
        ),
        estimate_s=45.0,
    )


# --- the 2-phase chain ---------------------------------------------------------------------------


def _relative_to_cwd(files: Sequence[str], cwd: Path) -> list[str]:
    """Each of `files` as the step will be handed it: relative to `cwd` when it lies under it,
    otherwise unchanged (ruling R754, final review).

    Lexical, not resolved: the manifest records absolute paths under the project root it was
    registered in, and `cwd` is that root, so a file under it shares its spelling. A relative
    entry is already relative to `cwd` and is returned as it is.

    A name that would begin with `-` is handed over as `./<name>` (`rad_engine.octree.
    dash_safe_operand`, ruling R782): `rfluxmtx` passes these names to `oconv`, which read a first
    `-f.rad` as its `-f` switch and dropped the file.
    """
    names: list[str] = []
    for item in files:
        path = Path(item)
        if path.is_absolute():
            try:
                names.append(dash_safe_operand(path.relative_to(cwd).as_posix()))
                continue
            except ValueError:
                pass
        names.append(dash_safe_operand(str(item)))
    return names


def two_phase_steps(request: ChainRequest) -> list[Step]:
    """The three `Step`s of the 2-phase chain, in order, each with its stdin declared.

    `phase` strings follow the spec's own `"<what> (<n>/<total>)"` shape so `job(action="status")`
    reports progress a reader can place in the chain -- here `"building annual sky matrix (2/3)"`,
    over this chain's own three steps. **The denominator is this chain's step count and nothing
    else**: this line cited the spec's own six-step example (`"building D matrix (2/6)"`) until Phase
    7a's whole-branch review, which is a count no `Step` below has ever carried.

    Estimates are **declared, not calibrated**, and `JobStore.submit` attaches
    `RUNTIME_ESTIMATE_UNCALIBRATED` to say so -- so no number here is published as a
    measurement. **It attaches it to the jobs whose steps declare an `estimate_s`, not to every job
    it accepts**: `submit` guards that append with `if record.estimate_s is not None:`, and its
    own `estimate_s=(...)` makes the record's own estimate `None` whenever any step has none. Every
    `Step` this module builds carries one (measured: 31 of 31 `Step(...)` constructions in this file
    pass `estimate_s`, and `Step(` is constructed nowhere else under `src/`), so every job submitted
    from this chain does get it. They exist to order the plan and to give the queue something
    to report.

    **The two files this chain needs at submit -- the job's `points.pts` and the project root's
    `receiver_mf{mf}.rad` -- are written by the caller**, in that order, immediately before this
    call. They used to be written here through ruling R48's containment primitive, which raises a
    wire type this layer may not name; `build_rfluxmtx_argv` still names
    `dc_receiver_filename(spec.sky_subdivision)` as a bare relative filename resolved against `cwd`,
    and `rad_engine.annual.run_rfluxmtx`'s docstring still states the contract that somebody must
    write it (*"this function only spawns the process and does not create files"*). Omitting it fails
    the whole job at step 1 with `rfluxmtx: cannot load 'receiver_mf{mf}.rad'`, which is exactly what
    the first end-to-end run did (before the filename carried `mf`).
    """
    # Ruling R754, fix round 1: the job worker spawns step 1's `rfluxmtx` later, and rfluxmtx
    # builds `rcontrib ... "!oconv -f <file> ..."` from these names, single-quoting one only when
    # it holds some characters: `>`, `#` and a leading `~` reach the shell bare, and a `'` closes
    # the quote it adds (measured). Refused here, where the step is built at submit, so no job is
    # queued over them.
    # Final review: each file under the step's cwd is passed relative to it, so the project root's
    # own name never reaches the shell line; the check runs on the strings actually passed (the
    # receiver's bare name included), and a file outside the cwd keeps its absolute path.
    scene_names = _relative_to_cwd(request.scene_files, request.root)
    refuse_shell_unsafe_paths(
        [dc_receiver_filename(request.mf), *scene_names], field="project", program="rfluxmtx"
    )
    grid = request.grid
    parameters = request.parameters
    sky_subdivision = request.mf
    north = request.north
    weather_input_id = request.weather_input_id

    matrices = request.matrices_dir
    dc_path = matrices / f"dsmx_{grid}.mtx"
    sky_path = matrices / f"smx_{weather_input_id}.mtx"
    ill_path = request.results_dir / f"annual_{grid}.ill"
    points_path = request.points_path

    dc_spec = DcMatrixSpec(
        scene_files=scene_names,
        n_sensors=request.n_sensors,
        parameters=parameters,
        sky_subdivision=sky_subdivision,
        processes=request.processes,  # R781
    )
    sky_spec = SkyMatrixSpec(
        weather_path=request.weather_path,
        sky_subdivision=sky_subdivision,
        ground_reflectance=request.ground_reflectance,
        north=north,
    )

    return [
        Step(
            phase="building daylight-coefficient matrix (1/3)",
            argv=tuple(build_rfluxmtx_argv(dc_spec)),
            cwd=request.root,
            # R755: rfluxmtx reads the receiver itself and compiles the scene files with oconv when
            # the step starts, and runs a `!` line in either (measured); the worker scans them then.
            # The receiver name is the argv's own operand, from the same `dc_spec`.
            scene_inputs=(
                dc_receiver_filename(dc_spec.sky_subdivision),
                *dc_spec.scene_files,
            ),
            stdout_path=dc_path,
            # R43: `rfluxmtx` takes sensor rays **only** on stdin, and `stdin_path` is a path rather
            # than bytes so the payload never lands in the durable record. Written into the job
            # directory, which `run_annual` owns, rather than beside the grid's own `.pts`: the file
            # the worker opens must be the file this submit resolved (R45 opens it with `O_NOFOLLOW`
            # and an `S_ISREG` check on the descriptor), and a job-local copy cannot be swapped by a
            # later `sensor_grid` call mid-run.
            stdin_path=points_path,
            artifact=ArtifactSpec(
                artifact_id=f"dsmx:{grid}",
                # R44(2): `dsmx`, not `vmx` -- a 2-phase DC matrix has no window group and
                # `REQUIRED_EDGES["vmx"]` demands one, so `vmx` is unsatisfiable by construction.
                kind="dsmx",
                path=dc_path,
                built_from=_dc_edges(request, request.octree_id),
                parameters={
                    **parameters_dict(parameters),
                    "mf": sky_subdivision,
                    "receiver": dc_receiver_filename(sky_subdivision),
                    "n_sensors": request.n_sensors,
                    **_processes_parameter(request),  # R781
                },
            ),
            estimate_s=120.0,
        ),
        Step(
            phase="building annual sky matrix (2/3)",
            argv=tuple(build_gendaymtx_argv(sky_spec)),
            cwd=request.root,
            stdout_path=sky_path,
            stdin_path=None,  # DEVNULL: gendaymtx takes the .wea as a positional argument.
            artifact=ArtifactSpec(
                artifact_id=f"smx:{weather_input_id}",
                kind="smx",
                path=sky_path,
                # **`smx` DOES have a `REQUIRED_EDGES` row, unlike `dsmx`** -- the EPW, the project
                # ground reflectance, and north where the project declares it, each of which
                # invalidates S on its own. `check_edges` refuses an incomplete set here rather than
                # returning early, and it did exactly that the first time this was written with the
                # weather input alone. The ground edge is the other half of the `dmx` materials
                # pair: the same physical quantity arriving by a different route, and "the asymmetry
                # is only real if both are here".
                built_from=_smx_edges(request),
                parameters={
                    "mf": sky_subdivision,
                    "north_east_of_north": north,
                    "ground_reflectance": request.ground_reflectance.value,
                    # **`mode` recorded, on the whole-branch review's Minor 4.** `smxd` records
                    # `"direct"` and `smxsun` records `"sun_disk"`; this one recorded nothing, and
                    # it is the id under which two physically different matrices could be sealed --
                    # a total sky and a `gendaymtx -d` sky are both `smx:<weather>` and both the
                    # same shape, so `_parameters_match` had no field to tell them apart. No
                    # production caller builds a direct-only `smx` today; the field is what keeps
                    # that from becoming a silent substitution the day one does. The two new sky
                    # kinds got this right and the older one was the outlier.
                    #
                    # It invalidates every stored `smx`: one rebuild, the same trade
                    # `_DAYLIGHT_MATRIX_REVISION` already took for `dmx`, and cheaper here
                    # (`gendaymtx` is one spawn of about 45 s, not a traced matrix).
                    "mode": "total",
                },
            ),
            estimate_s=45.0,
        ),
        Step(
            phase="multiplying matrices into the annual result (3/3)",
            argv=tuple(build_dctimestep_argv(dc_path, sky_path)),
            cwd=request.root,
            stdout_path=ill_path,
            # R43: `dctimestep` needs no stdin at all -- both inputs are file arguments, measured
            # with stdin closed (exit 0, data rows byte-identical to the piped form).
            stdin_path=None,
            artifact=ArtifactSpec(
                artifact_id=f"ill:{grid}",
                kind="ill",
                path=ill_path,
                built_from=(f"dsmx:{grid}", f"smx:{weather_input_id}"),
                parameters={"mf": sky_subdivision, "n_points": request.n_sensors},
            ),
            estimate_s=30.0,
        ),
    ]


# --- the 3-phase chain (Phase 6b Task 6) ---------------------------------------------------------


class ThreePhaseNotBuildableError(ProjectError):
    """A 3-phase run whose project state cannot supply one of the chain's four terms.

    **The refusals that are about the project rather than the arguments**, gathered on one exception
    so `run_annual`'s existing handler chain shapes them uniformly with the inference context
    attached. Six conditions reach it from `three_phase_target`, and every one is something the
    *caller* can repair:

    - **no window group at all.** The inference only answers 3-phase because a group with a BSDF
      state exists, so this is unreachable from `method="auto"`; it is reachable from an explicit
      `method="3phase"` on a project with no fenestration declared, where there is no aperture for
      V to trace and no aperture for `genklemsamp` to sample.
    - **two window groups registered under one name.** Every artifact id, matrix filename and
      per-group mapping key this chain builds is derived from the group's name, so a repeat is one
      identity to all of them and the pair would be summed twice under it -- more light than either
      facade admits. Ruling R138's fourth sibling, after `rad_core.blinds`' `closing_order`,
      `group_schedule` and `blend_row`; this is the first function here that ever sees more than one
      group, so it is the first place the hole could bite.
    - **a group with no `tmx` sealed.** T is not a step (R62): it is the copied XML
      `window_group(action="assign_bsdf")` sealed, and the chain consumes it. A group with no bound
      state has no T to consume.
    - **a group with more than one state, under a `blind_control` that chooses between none of
      them.** Choosing which state an hour uses is `blind_control="lm83"`'s business -- LM-83
      section 2.2.6's hourly schedule, derived from a zero-bounce direct-sun run. Under `"none"` and
      `"fixed"` nothing chooses, so multiplying through one of the bound BSDFs would publish an
      annual result as though the chosen state were the whole story. **Phase 7d-1 narrowed this from
      "more than one state" to "more than one state and nothing will choose"**: under `"lm83"` the
      group resolves and the blend consumes every state.
    - **a group whose states are sealed on two different Klems bases** -- new with that lift. The
      blend sums the states' results at the same sensors, and two bases are two different sets of
      directions summed as though they were one.
    - **more than one window group, at the point a one-group chain narrows to it**
      (`ThreePhaseTarget.single`). The chain builders multiply **one** V, T and D; a project with
      two groups needs one chain per group summed into a single `.ill`
      (`I = sum_g V_g T_g D_g S`), with its own artifact ids and its own reuse story. **Phase 7d-1
      moved this refusal off `three_phase_target`**, which now resolves every group a project
      registers, and onto the narrowing itself -- so it fires where a facade would actually be
      substituted for the building rather than forbidding the resolution outright.

    **`blind_schedule_steps` raises it too, for three more conditions**, and the enumeration above
    is `three_phase_target`'s rather than this exception's whole surface: an analysis area that
    cannot select a band, a single hour of the transpose that does not fit, and -- owner ruling
    R242 -- a grid materially sparser than LM-83's own placement over the polygon that selected the
    band, below 1 000 sf. See that function's own docstring for each.

    No code is invented: this carries none, exactly as `WeatherNotLoadedError` and
    `GridNotFoundError` do for the same reason -- the register allocates codes for protocol handles
    and for conditions a client branches on, and these carry their reason as prose naming the call
    that fixes them (`weather_tool._unknown_name`'s posture).
    """

    code = None


class SparseLm83GridError(ThreePhaseNotBuildableError):
    """R242's refusal of a grid materially sparser than LM-83's own placement below 1 000 sf
    (`blind_schedule_steps`), split out so the layer that holds the project's records spells its
    remedy whole (Phase 25 C2a final fix F, fix round 5, P3).

    This module holds no project name, weather name, north or quality, so its own message ends
    with the remedy in words, naming no call; `finding` is the message without it, which
    `rad_mcp.annual_tool` ends with the `sensor_grid` and `run_annual` calls spelled from the grid's
    record and the run's own arguments. `grid` is the grid refused."""

    def __init__(self, finding: str, remedy: str, *, grid: str) -> None:
        super().__init__(f"{finding} {remedy}")
        self.finding = finding
        self.grid = grid


class ThreePhaseGroup:
    """One window group of a 3-phase run, with **every** sealed T it has.

    Built by `three_phase_target`, which is where every refusal about the project's fenestration
    lives, so the chain builders receive a state that is already proven buildable and contain no
    branches of their own.

    `states` is sorted, so the order is a property of the project rather than of the order the
    manifest scan happened to produce the ids in. `tmx_ids` and `tmx_paths` are keyed by state and
    cover `states` exactly.

    **`state`, `tmx_id` and `tmx_path` are the one-state *view*, and they raise rather than index.**
    Every chain this server ships today multiplies one T (`three_phase_steps`, `five_phase_steps`),
    and under `blind_control="lm83"` a group may now carry several -- so the narrowing is stated
    here, once, instead of `states[0]` appearing at each consumer. Picking a BSDF silently is the
    substitution the multi-state refusal existed for, and a `[0]` at a call site is that
    substitution wearing an index.
    """

    def __init__(self, *, group: WindowGroup, states: tuple[str, ...],
                 tmx_ids: Mapping[str, str], tmx_paths: Mapping[str, Path],
                 klems_bins: int) -> None:
        self.group = group
        self.states = states
        self.tmx_ids = tmx_ids
        self.tmx_paths = tmx_paths
        self.klems_bins = klems_bins
        """T's own bin count, **read from the sealed XML** rather than assumed to be 145.

        `window_group(action='assign_bsdf')` already refuses any Klems file off the 145-direction
        full basis (`BSDF_BASIS_MISMATCH`) and any tensor tree (`BSDF_TENSOR_NOT_FOR_MATRIX`), so
        this is 145 for every T this server can seal today. It is read anyway because the shape
        precondition is only meaningful if T's dimensions come from T: a precondition that asserted
        `145` against a constant would be checking this module's memory of the basis, not the file
        the job is about to multiply.

        **One number for the whole group, and Phase 7d is what makes that a claim rather than a
        restatement.** With several states bound, `three_phase_target` refuses the group outright
        unless every state's sealed file declares the same basis -- see its own refusal -- so this
        field describes all of `states` and not merely the first of them.

        **This is the only value in `three_phase_shape_check` that comes from the sealed file, and
        fix round 1 is what made that true.** Until then `v.ncols` and `d.nrows` were derived from
        this field too, so the V-T and T-D joins compared it with itself and could never fail. They
        now come from `rad_engine.klems.KLEMS_BINS` -- what `-bn Nkbins` and `genklemsamp` will
        actually produce -- which is what turns the V-T join into a comparison of two independent
        facts."""

    @property
    def state(self) -> str:
        """The one bound state, for the chains that multiply one T.

        Raises rather than returning `states[0]`: under `blind_control="lm83"` a group legitimately
        carries several, and a chain that has no blend in it yet must say so rather than publish an
        annual result computed through whichever BSDF sorted first.

        ## Only `five_phase_steps` reaches this, and the message now says so

        **The sentence this replaces was half-false and named no chain.** It said *"it is not yet
        what this chain multiplies through"* -- true of the five-phase chain, and false of the
        three-phase one as of Phase 7d-1 Task 19, which wired `blind_schedule_steps` and
        `blend_steps` into the submitted plan. Measured over `src/rad_engine/chain.py` at
        `4d60d58`: `three_phase_steps` (the whole function) contains no use of `.state`,
        `.tmx_id` or `.tmx_path` outside comments, and every live use is inside `five_phase_steps`.
        `tests/test_chain.py::TestOnlyTheFivePhaseChainNarrowsToOneState` pins that structurally,
        so a future three-phase consumer reddens rather than silently making this sentence false
        again.

        ## And it closed a loop on this phase's headline workflow

        LM-83 sec 2.4 makes sDA and ASE a mandatory pair, and `ase` drives `rad_core.method.infer`
        rule 1 to `5phase` unconditionally. On a blend-capable project, measured at `4d60d58` at
        the tool boundary with the direct-sun tensor tree bound:

            run_annual(metrics=['sda','ase'])                       -> refused by
              `three_phase_target`: "...Run with blind_control='lm83', or bind a single state."
            run_annual(metrics=['sda','ase'], blind_control='lm83') -> refused HERE, and the old
              message answered "Bind a single state" -- naming back the argument the first refusal
              had just sent the caller here with.

        Neither named the route that works, which was written down only in a test docstring:
        drop `ase` so the inference chooses the three-phase chain, run `metrics=['da'],
        blind_control='lm83'` -- that run plans the direct-sun leg too, so it seals `illdir:<grid>`
        as well as the blend -- and then read **both** metrics off
        `results(action='compliance', standard='lm83', blind_control='lm83')`, whose `lm83` block
        carries `sda` (from `illblend:<grid>`) and `ase` (from `illdir:<grid>`) with a band for
        each. That route is now named here, and
        `tests/test_annual_tool.py::TestTheAseWithBlindsLoopHasAnExit` **makes those calls** rather
        than asserting this wording (ruling R189, and R239's technique).
        """
        if len(self.states) != 1:
            raise ThreePhaseNotBuildableError(
                f"window group {self.group.name!r} has {len(self.states)} BSDF states bound "
                f"({', '.join(self.states)}), and the FIVE-PHASE chain multiplies one T. Choosing "
                "which state an hour uses, and summing the states' results against that choice, "
                "is the LM-83 blind-schedule blend -- it is what blind_control='lm83' resolves the "
                "states for, and the three-phase chain does multiply through it, while this one "
                "does not. So blind_control='lm83' is not the answer here, "
                "and re-passing it will return to this same refusal. Two routes run instead. "
                "(1) Keep the blend and drop 'ase' from metrics: metric 'ase' is what forces the "
                "five-phase inference, so run_annual(project=..., grid=..., metrics=['da'], "
                "blind_control='lm83', epw=..., north=...) plans the three-phase blend AND the "
                "direct-sun leg, and results(action='compliance', standard='lm83', "
                "blind_control='lm83') then reports sDA off the blend and ASE off the direct-sun "
                "matrix, in one payload, with a band for each -- which is the sDA/ASE pair LM-83 "
                "section 2.4 requires. (2) Keep the five-phase chain and bind a single state to "
                "this group, which multiplies that one BSDF and chooses between none."
            )
        return self.states[0]

    @property
    def tmx_id(self) -> str:
        """The one bound state's sealed `tmx` artifact id. See `state` for why it is not `[0]`."""
        return self.tmx_ids[self.state]

    @property
    def tmx_path(self) -> Path:
        """The one bound state's sealed `tmx` file. See `state` for why it is not `[0]`."""
        return self.tmx_paths[self.state]


class ThreePhaseTarget:
    """Every window group a 3-phase run multiplies through, with their sealed Ts resolved.

    `groups` is sorted by group name, so the chain's step order and every artifact id derived from
    it are properties of the project rather than of the order `annual_tool` happened to read the
    manifest in. Ruling R30's posture: one arithmetic path by construction.

    `blind_control` is carried rather than re-read because it is what decided which projects this
    object can exist for at all -- a multi-state group resolves only under `"lm83"` -- and the blend
    that consumes the states has to know the same value the refusal was taken against.
    """

    def __init__(self, *, groups: tuple[ThreePhaseGroup, ...], blind_control: str) -> None:
        self.groups = groups
        self.blind_control = blind_control

    @property
    def single(self) -> ThreePhaseGroup:
        """The one group, for callers that are only defined over one.

        **This is the multi-group refusal, moved from the resolver to the point of consumption.**
        `three_phase_target` no longer refuses two groups -- `I = sum_g V_g*T_g*D_g*S` is what
        Phase 7d builds -- but the chain builders reached from here are still one-group, so the
        refusal fires where the narrowing actually happens instead of forbidding the whole
        resolution. It raises rather than returning the first: publishing one facade's fenestration
        as though it were the whole story is the exact substitution the old message named.

        A `ThreePhaseNotBuildableError`, not a `ValueError`, and the distinction is the one
        `five_phase_steps`' own `None`-field guard draws. That guard is a `ValueError` because
        reaching it means this module's caller has drifted, which no client can act on. This one is
        reachable from the wire by a caller who registered two window groups, and the remedy is
        theirs -- so it goes through `run_annual`'s refusal envelope with the fix instruction
        attached, exactly as it did when it lived in `three_phase_target`.
        """
        if len(self.groups) != 1:
            names = ", ".join(group.group.name for group in self.groups)
            raise ThreePhaseNotBuildableError(
                f"this run resolved {len(self.groups)} window groups ({names}), and this chain "
                "builds for exactly one. A multi-group run is not one chain with a bigger matrix: "
                "it is one V, T and D per group summed into a single result "
                "(I = sum over groups of V_g*T_g*D_g*S), which needs per-group artifact ids and "
                "its own reuse accounting. Refused rather than run against whichever group "
                "happened to sort first -- that would publish an annual result computed with one "
                "facade's fenestration standing in for all of them. Remove the groups this run is "
                "not about with window_group(action='remove', name=...), or run one project per "
                "facade."
            )
        return self.groups[0]


def _klems_bins_of_sealed_tmx(group: WindowGroup, tmx_path: Path) -> int:
    """T's Klems direction count, read from the sealed copy `tmx_path` names.

    **Moved out of `three_phase_target` by Phase 7d-1 Task 6, not rewritten**: the body below is the
    code that stood inline at the end of that function, byte for byte apart from this header and its
    indentation, because the measured facts its comments record (the 875 MB peak, the shared
    ceiling, R30 and R62) are the reason each line is the way it is. It is a function now because a
    group may bind several states and each one's sealed file has to be read.

    Takes the `WindowGroup` rather than its name so the four refusal messages below are the
    identical f-strings they were, with nothing re-worded on the way past.
    """
    # **T's bin count is read off the sealed copy, not off the source XML and not from a constant.**
    # R62's whole point is that the `tmx` is a *copy*, so a later edit to the caller's own file is
    # detectable drift rather than a silent substitution -- reading the source here would discard
    # that guarantee at the one place it matters, which is the matrix actually being multiplied.
    # **Bounded before the parse, with the same ceiling `assign_bsdf` binds with** -- one constant,
    # imported, not a second number that could drift from it (ruling R30: one arithmetic path by
    # construction rather than by discipline). `open_regular_file` closes the streaming case, so a
    # FIFO or character device cannot be read forever here; what is left is an ordinary regular file
    # of arbitrary size, and this read held it in memory three times over -- once as bytes, once
    # decoded, once through `parse_bsdf`. Measured on a 315 MB file: 546 MB RSS after read and
    # decode, 875 MB peak through the parse, on an `anyio` worker whose pool is 40.
    #
    # A file over the bound cannot arrive through `assign_bsdf`, which refuses it at the same
    # ceiling -- literally the same object, `rad_engine.bsdf.MAX_BSDF_BYTES` -- so reaching this
    # refusal means the sealed copy grew after it was bound, which is drift worth reporting rather
    # than absorbing.
    try:
        with open_regular_file(tmx_path, "rb") as handle:
            raw_bytes = handle.read(MAX_BSDF_BYTES + 1)
        if len(raw_bytes) > MAX_BSDF_BYTES:
            raise ThreePhaseNotBuildableError(
                f"the sealed BSDF for window group {group.name!r} at {tmx_path} is larger than "
                f"{MAX_BSDF_BYTES >> 20} MB. A BSDF XML describes one device's optical behaviour; "
                "a file this size is not one, and window_group(action='assign_bsdf') refuses the "
                "same ceiling on the way in -- so the sealed copy has grown since it was bound. "
                "Re-bind the state with window_group(action='assign_bsdf')."
            )
        parsed = parse_bsdf(raw_bytes.decode("utf-8", errors="replace"))
    except OSError as exc:
        raise ThreePhaseNotBuildableError(
            f"the sealed BSDF for window group {group.name!r} could not be read at {tmx_path}: "
            f"{exc.strerror or exc}. The 3-phase chain multiplies through this file, so the run is "
            "refused rather than submitted to fail inside the job. Re-bind the state with "
            "window_group(action='assign_bsdf')."
        ) from exc
    if parsed.bins is None:
        raise ThreePhaseNotBuildableError(
            f"the sealed BSDF for window group {group.name!r} at {tmx_path} declares no Klems angle "
            "basis, so it has no bin count for the V*T join to be checked against. A tensor tree "
            "does not work in the matrix formulation at all "
            "(BSDF_TENSOR_NOT_FOR_MATRIX). Re-bind a full-Klems XML."
        )
    return parsed.bins


def _refuse_repeated_name(names: Sequence[str], *, subject: str, consequence: str) -> None:
    """Raise if `names` repeats, naming **which** names repeat and what the repeat would do.

    **Ruling R138, and the shape is `rad_core.blinds._refuse_repeated_group_name`'s deliberately.**
    That helper is what `closing_order`, `group_schedule` and `blend_row` share; this one is its
    sibling for the two sequences `three_phase_target` resolves -- the project's window group names,
    and one group's bound state names. One check, written once, with each caller naming its own
    subject and its own consequence, because a repeat means something different on each axis: a
    repeated *group* is one facade's V, T and D summed twice under one set of artifact ids; a
    repeated *state* is one sealed T carried twice in `states` while `tmx_ids` and `tmx_paths` carry
    it once, so the blend sums one state's contribution twice. Both over-report; neither message
    would be true of the other's condition, which is why the consequence is the caller's.

    Not `rad_core.blinds._refuse_repeated_group_name` itself: that one raises `ValueError`, which
    `run_annual` does not shape into a refusal envelope, and it is private to a module in another
    package. What the four functions share is the rule, not the call.
    """
    if len(set(names)) == len(names):
        return
    # `> 1`, not `> 0`: every name is in `names` at least once, so `> 0` would report every name as
    # repeated and the message would be true only by accident on a two-element sequence where both
    # elements are the repeat. Pinned by a three-element fixture with one unique member.
    repeated = sorted({name for name in names if list(names).count(name) > 1})
    raise ThreePhaseNotBuildableError(f"{subject} {repeated!r} more than once. {consequence}")


def three_phase_target(
    loaded: LoadedProject, groups: list[WindowGroup], *, blind_control: str
) -> ThreePhaseTarget:
    """Every window group and sealed T this run's V, T and D are about.

    Every project-state refusal of the 3-phase chain is here, above the `dry_run` branch by
    construction: `_dispatch` calls this before it branches, exactly as `_validate_project` is.
    See `ThreePhaseNotBuildableError` for the conditions and why each is refused rather than
    guessed past.

    **Two of the four refusals narrowed in Phase 7d-1 and two did not.** Multi-group is no longer
    refused here at all: `I = sum_g V_g*T_g*D_g*S` is what this phase builds, and until the chain
    builders do, `ThreePhaseTarget.single` is what refuses -- at the point of narrowing rather than
    at resolution, so a caller is told the same thing by the same exception type. Multi-state
    resolves **only under `blind_control="lm83"`**, where `rad_core.blinds.group_schedule` chooses
    between the states hour by hour; under `"none"` and `"fixed"` nothing chooses, and picking one
    BSDF silently is the substitution the refusal exists for. A group with no sealed T and a project
    with no group are refused unchanged.

    **A new refusal arrives with the lift**: two states of one group whose sealed files declare
    different Klems bases. The blend sums the states' results at the same sensors, and two bases are
    two different sets of directions summed as though they were one.

    The sealed read is bounded by `rad_engine.bsdf.MAX_BSDF_BYTES` -- **the same object
    `rad_mcp.window_group_tool` applies to `assign_bsdf`'s own read**, imported rather than passed in
    (see `_klems_bins_of_sealed_tmx`, which is that read, moved). That constant's own docstring
    records why: threading it down as an argument left the binding at the production call site
    unasserted, so `max_bsdf_bytes=1 << 40` there re-opened the measured 875 MB peak with the whole
    suite green. Read from the module, there is no call site to mis-bind.
    """
    if not groups:
        raise ThreePhaseNotBuildableError(
            "the 3-phase chain multiplies V*T*D*S through a window group's Klems basis, and this "
            "project has no window group registered. Define one with window_group(action='define', "
            "name=..., aperture=[...]) naming the modifiers your glazing polygons use, then bind "
            "its BSDF with window_group(action='assign_bsdf'). With no fenestration declared there "
            "is no aperture for the V matrix to trace or for genklemsamp to sample, so there is "
            "nothing this method could compute."
        )

    # **A repeated group name is refused before anything is resolved (ruling R138).** This function
    # is the fourth in this repo to take a sequence of window groups, and the first three --
    # `rad_core.blinds.closing_order`, `group_schedule` and `blend_row` -- all grew this same guard,
    # the last two of them in a fix round after two clean reviews had passed over the hole. Here the
    # consequence is its own: two groups sharing a name resolve to the same `tmx:<group>:<state>`
    # ids, the same `vmx_<group>_<grid>.mtx` and `dmx_<group>.mtx` filenames, and the same key in
    # every per-group mapping the blend builds -- so the pair is summed twice under one identity,
    # which overstates the row with light one facade never admitted.
    #
    # Written here rather than delegated to `rad_core.blinds._refuse_repeated_group_name`, and the
    # reason is the exception rather than the check: that helper raises `ValueError`, which
    # `run_annual` does not shape into a refusal envelope, and it is private to a module in another
    # package. What is shared between the four is the *rule*, which is why this comment names them.
    #
    # Unreachable from the wire today -- `annual_tool._window_groups` derives each name from a
    # distinct `window_group_<name>` input id -- for the same reason and with the same answer as the
    # two-Klems-bases refusal below: an upstream uniqueness is not a precondition anyone here holds.
    _refuse_repeated_name(
        [group.name for group in groups],
        subject="this project registers window group(s)",
        consequence=(
            "Each group's V, T and D are named after it (vmx:<group>:<grid>, tmx:<group>:<state>, "
            "dmx:<group>), so two groups sharing a name are one identity to every artifact id, "
            "every matrix filename and every per-group term the blend sums -- their contributions "
            "would be summed twice under that one name, publishing more light than either facade "
            "admits. Rename or remove one with window_group(action='remove', name=...)."
        ),
    )

    resolved: list[ThreePhaseGroup] = []
    # **Sorted by name, and the key is the group's own name rather than its position.** Step order,
    # every matrix filename and every artifact id below are derived from it, so they are properties
    # of the project rather than of the order `annual_tool` happened to read the manifest in -- and
    # a project whose inputs were registered in a different order therefore reuses the same
    # artifacts rather than rebuilding them under different ids.
    for group in sorted(groups, key=lambda candidate: candidate.name):
        if not group.states:
            raise ThreePhaseNotBuildableError(
                f"window group {group.name!r} has no BSDF bound, so the 3-phase chain has no T "
                "matrix to multiply through. T is not a step this server runs: it is the BSDF XML "
                "that window_group(action='assign_bsdf') copied and sealed, and the chain consumes "
                f"it. Bind one with window_group(action='assign_bsdf', name={group.name!r}, "
                "states={'<state>': '/path/to/klems.xml'})."
            )
        # **The same rule one loop deeper, on the state axis -- fix round 1.** `states` below is a
        # `tuple`, while `tmx_ids` and `tmx_paths` are dicts keyed by state, so a repeat makes
        # `len(states)` exceed both and breaks `ThreePhaseGroup`'s own documented invariant that the
        # two mappings "cover `states` exactly". Under Tasks 7 and 8 it is the group-name defect one
        # axis over: one state's contribution summed twice under one identity.
        #
        # Taken **before** the `blind_control` check below, deliberately: ('closed', 'closed') is a
        # malformed group, not a policy question, and answering it with "2 BSDF states bound
        # (closed, closed) and blind_control='none' chooses between them with nothing" would send a
        # caller to bind a schedule for a second state they never bound.
        #
        # Unreachable from the wire today, and more strongly than the group-name case:
        # `rad_core.manifest` refuses a duplicate artifact id at load (`duplicate artifact id`), and
        # `rad_engine.project.register_artifact` replaces by id rather than appending, so
        # `states_by_group` above cannot scan the same `tmx:<group>:<state>` twice.
        _refuse_repeated_name(
            group.states,
            subject=f"window group {group.name!r} binds state(s)",
            consequence=(
                "A state names its own sealed T (tmx:<group>:<state>) and is the key the LM-83 "
                "hourly schedule selects by, so one state bound twice is one T carried twice: "
                "`states` would count it twice while its tmx id and path are stored once, and the "
                "blend would sum that state's contribution twice under one identity, publishing "
                "more light than the group ever admits. Re-bind the group with "
                "window_group(action='assign_bsdf')."
            ),
        )
        if len(group.states) > 1 and blind_control != LM83_BLIND_CONTROL:
            raise ThreePhaseNotBuildableError(
                f"window group {group.name!r} has {len(group.states)} BSDF states bound "
                f"({', '.join(sorted(group.states))}), and blind_control={blind_control!r} chooses "
                "between them with nothing. Choosing which state an hour uses is "
                "blind_control='lm83's business: it derives LM-83's hourly blind schedule "
                "from a zero-bounce direct-sun run and blends the states against it. Run with "
                "blind_control='lm83', or bind a single state."
            )

        states = tuple(sorted(group.states))
        tmx_ids: dict[str, str] = {}
        tmx_paths: dict[str, Path] = {}
        klems_bins: int | None = None
        for state in states:
            tmx_id = f"tmx:{group.name}:{state}"
            record = next((a for a in loaded.manifest.artifacts if a.id == tmx_id), None)
            if record is None:  # pragma: no cover -- `states` is derived from these very ids
                raise ThreePhaseNotBuildableError(
                    f"window group {group.name!r} reports state {state!r} but no {tmx_id!r} "
                    "artifact is registered. Re-bind the state with "
                    "window_group(action='assign_bsdf')."
                )
            tmx_ids[state] = tmx_id
            # **`Path(record.path)`, not `loaded.root / record.path` (ruling R127).**
            # `rad_engine.project.register_artifact` stores `Path(path).resolve()`, so every stored
            # path is already absolute: joining a root onto it is a no-op at best, and on any
            # relative path that ever reached the manifest it would silently relocate the read to a
            # file that is not the sealed copy.
            path = Path(record.path)
            tmx_paths[state] = path
            bins = _klems_bins_of_sealed_tmx(group, path)
            # **Compared against the basis an *earlier state* declared, never against a constant
            # and never against itself.** `klems_bins` is `None` until the first state has been
            # read, which is what keeps this a join between two files rather than the `x == f(x)`
            # shape ruling R74 rejects: with one state bound there is nothing to disagree with and
            # an off-basis T is left to `three_phase_shape_check`'s V-T join, which is where a
            # single T's basis is checked against what `-bn Nkbins` will actually produce.
            #
            # **Unreachable from the wire today, and that is not a reason to leave it unwritten** --
            # the identical posture `three_phase_shape_check`'s own docstring takes for its V-T
            # join. `window_group(action='assign_bsdf')` refuses any Klems file off the
            # 145-direction full basis at `role="matrix"` (`BSDF_BASIS_MISMATCH`, keyed on the
            # declared basis *name*), so no project this server can seal binds two states on two
            # bases. That upstream refusal is the real defence; this one is what keeps the blend's
            # own precondition a guarantee somebody holds rather than one nobody does.
            if klems_bins is not None and bins != klems_bins:
                raise ThreePhaseNotBuildableError(
                    f"window group {group.name!r} binds state {state!r} on a {bins}-direction "
                    f"Klems basis and an earlier state on a {klems_bins}-direction one. The blend "
                    "sums the two states' results at the same sensors, and two bases are two "
                    "different sets of directions summed as though they were one. Re-bind the "
                    "states on one basis with window_group(action='assign_bsdf')."
                )
            klems_bins = bins

        # `klems_bins` cannot be `None` here: `states` is non-empty (the refusal above) and the loop
        # assigns it on its first pass. `assert` rather than `or 0`, which would publish a
        # zero-direction basis as though it had been read from a file.
        assert klems_bins is not None
        resolved.append(ThreePhaseGroup(
            group=group, states=states, tmx_ids=tmx_ids, tmx_paths=tmx_paths,
            klems_bins=klems_bins,
        ))

    return ThreePhaseTarget(groups=tuple(resolved), blind_control=blind_control)


def three_phase_shape_check(
    target: ThreePhaseGroup, *, n_points: int, sky_subdivision: int, matrices: Path,
    weather_input_id: str, n_hours: int,
) -> None:
    """Run `check_three_phase_shapes` once, at submit, over whatever V/T/D/S exist on disk now.

    **Ruling 1 of Phase 6b Task 6, and it overrides the brief's own "again in full before step 5".**
    That second placement is unimplementable and is not attempted: owner ruling R44 states the
    governing fact -- the job worker *"spawns processes and offers no per-step Python hook, so
    anything that is not a subprocess has no home inside a job"* -- and `JobStore._run`'s loop is
    spawn, classify stderr, commit artifact, with no seam a Python check could occupy. Verified in
    the code rather than assumed.

    So the precondition sees, on a **cold** run, only T and S; on a run where V and D already exist
    from an earlier submit, it sees those too. **That is not a gap**, and the reason is specific: a
    mismatch between a V and a D this very call is about to build would be an *engine* defect, not a
    caller error -- both come from `rad_engine.matrices`' own builders at one `mf`, out of one
    aperture, so the only way they can disagree is if those builders disagree with each other, which
    is `tests/test_matrices.py`'s subject and not something a caller can cause or repair.
    `dctimestep`'s own loud exit 1 (`fatal - unexpected row count in header`) remains the backstop,
    exactly as R44 already accepted for the 2-phase chain's identical case.

    What it *does* catch is the case worth catching before paying for anything: a **T** whose bin
    count does not match the basis V and D are binned into, and an **S** at a subdivision the D
    matrix will not be built at. Both are caller-caused, both are known at submit, and both would
    otherwise surface as an opaque Radiance fatal from inside a job after V and D had been paid for.

    ## Every operand is predicted from what its own producer will emit -- fix round 1

    **The first two joins were tautologies and could never go red.** `v`, `t` and `d` were all
    synthesised from the single variable `target.klems_bins`, so `v.ncols == t.nrows` and
    `t.ncols == d.nrows` each compared a value to itself. Only `d.ncols == s.nrows` was a real
    check, while this docstring claimed the function catches a T whose basis does not match the
    chain -- which it could not. Verified by execution: a `klems_bins` of 73 passed.

    **The rule that fixes it: predict each operand from the thing that will actually produce it,
    never from another operand.**

    - `v.ncols` and `d.nrows` come from `rad_engine.klems.KLEMS_BINS`. That is not a restatement of
      T: `build_v_argv` emits `-bn Nkbins` and `build_genklemsamp_argv` samples the directions
      `klems_full.cal` defines, and that `.cal` file resolves `Nkbins` to 145 (measured) **whatever
      BSDF the chain multiplies through**. V's column count is a property of the basis, not of the
      bound file.
    - `t.nrows`/`t.ncols` come from `parse_bsdf` over the sealed `tmx` -- the file's own statement
      about itself, and now the *only* term read from it.
    - `d.ncols` and `s.nrows` are both `reinhart_*` of the one `mf` this run resolved.

    So `v.ncols == t.nrows` is now a genuine comparison of two independent facts -- the basis the
    argv bins into against the basis the sealed XML declares -- and a 73-bin T is refused here
    instead of failing inside the job with `fatal - unexpected row count in header` after V and D
    have both been paid for.

    **`reinhart_bins` and `reinhart_row_count` are two functions and the join between them is
    real too.** `rad_engine.matrices.reinhart_bins` is what `build_d_argv` bins into;
    `rad_engine.skymatrix.reinhart_row_count` is what `gendaymtx -m` produces. They agree at every
    measured subdivision, and asserting that agreement here is what would catch them drifting apart.

    **This is currently unreachable from the wire, and that is not a reason to have left it
    broken.** `window_group(action='assign_bsdf')` refuses any Klems file off the 145-direction full
    basis (`BSDF_BASIS_MISMATCH`) and any tensor tree (`BSDF_TENSOR_NOT_FOR_MATRIX`), so no project
    this server can build has a `tmx` at any other bin count. The upstream refusal is the real
    defence; a precondition that cannot fail is still a guarantee nobody holds, and this docstring
    was stating one.

    **Nothing here checks V's or D's own row counts, and nothing can.** `rcontrib` streams, so
    neither matrix carries `NROWS` in its header at all (measured); `check_three_phase_shapes` reads
    only the three join dimensions, none of which is a row count of V or D.
    """
    v, t, d, s = _predict_v_t_d_s(
        target, n_points=n_points, sky_subdivision=sky_subdivision, matrices=matrices,
        weather_input_id=weather_input_id, n_hours=n_hours,
    )
    check_three_phase_shapes(v, t, d, s)


def _predict_v_t_d_s(
    target: ThreePhaseGroup, *, n_points: int, sky_subdivision: int, matrices: Path,
    weather_input_id: str, n_hours: int,
) -> tuple[DcMatrixHeader, DcMatrixHeader, DcMatrixHeader, DcMatrixHeader]:
    """The V/T/D/S prediction `three_phase_shape_check` and `five_phase_shape_check` both need,
    factored out so there is one arithmetic path rather than two (ruling R30) -- `five_phase_steps`'s
    own `V*T*D*S` leg is built from the identical basis as the 3-phase chain's own four matrices
    (`five_phase_shape_check`'s own docstring), so predicting it a second time under a different name
    would not catch anything this prediction does not already catch, and *would* be a second place
    for the two functions to silently disagree.

    Every rule for how each of the four is predicted -- T read from the sealed file and nothing else,
    V and D's Klems side from `KLEMS_BINS`, D and S's Reinhart side from `reinhart_bins`/
    `reinhart_row_count`, S overridden by a real on-disk header when one exists -- lives here, once,
    and is documented in full on `three_phase_shape_check`'s own docstring rather than restated.
    """
    # T -- the **only** operand read from the sealed file, which is what makes the V-T join below a
    # comparison of two independent facts rather than of one value with itself.
    t = DcMatrixHeader(
        nrows=target.klems_bins, ncols=target.klems_bins, ncomp=3, format="ascii"
    )
    # S's shape is predicted from `gendaymtx -m`, the same way `annual_tool._shape_check` predicts
    # the 2-phase pair, and for the same reason: the file does not exist yet on a cold run and the
    # number is not a guess -- `reinhart_row_count` is the one function both the sky and the receiver
    # read.
    # **`n_hours`, not `ANNUAL_TIMESTEPS`** -- the whole-branch review's Important 1, second half.
    # A hardcoded 8760 here is the same defect class as the `-ic 8760` that `build_rcollate_final_
    # argv` was parameterised to avoid: it predicts a common year for weather that may be shorter,
    # and then the prediction agrees with itself no matter what the caller loaded.
    s = DcMatrixHeader(
        nrows=reinhart_row_count(sky_subdivision), ncols=n_hours, ncomp=3, format="ascii"
    )
    # D's columns are the Reinhart bins `build_d_argv` will bin into at this same `mf`, via
    # `rad_engine.matrices.reinhart_bins` -- the measured, ground-patch-inclusive count, not
    # `144*mf^2`. Its **rows** are `KLEMS_BINS`, because `genklemsamp` samples the Klems directions
    # `klems_full.cal` defines -- not the count the bound BSDF happens to carry, which is the whole
    # point of the fix above.
    d = DcMatrixHeader(
        nrows=KLEMS_BINS, ncols=reinhart_bins(sky_subdivision), ncomp=3, format="ascii"
    )
    # V's columns are `-bn Nkbins`, resolved by `klems_full.cal` to `KLEMS_BINS` regardless of T.
    v = DcMatrixHeader(nrows=n_points, ncols=KLEMS_BINS, ncomp=3, format="ascii")

    # **Where a real file exists, it wins over the prediction.** On a re-run the sky matrix for this
    # weather input is already on disk, and reading it is what turns this from "the two numbers this
    # module derived agree with each other" into a statement about the artefact the job will
    # actually multiply. A file that cannot be read or parsed is left to the prediction rather than
    # refused: an unreadable *existing* S is the job's problem to report, and refusing here would
    # turn a stale byte into "this project cannot run".
    sky_path = matrices / f"smx_{weather_input_id}.mtx"
    if sky_path.exists():
        try:
            s = read_matrix_header(sky_path)
        except (MatrixFileUnreadableError, ValueError):
            logger.info("shape check: %s exists but is unreadable; using the predicted shape",
                        sky_path)
        else:
            # **A genuine two-source join, and the reason `n_hours` is threaded down here at all.**
            # `n_hours` is measured from the project's own `.wea` (`annual_tool._measured_n_hours`);
            # `s.ncols` is read off the sky matrix that `gendaymtx` actually produced from that
            # `.wea` on an earlier run. They are two independent measurements of one quantity, so
            # comparing them can fail -- unlike comparing the prediction with itself, which is the
            # `x == f(x)` shape ruling R74 rejects.
            #
            # What it catches: an `smx` left behind by *different* weather, and any future path
            # that lets the two drift. The consequence if it does not fire is not an error but a
            # silently reshaped result -- `rcollate -h -fa1 -ic N -t` was measured to exit 0 with a
            # valid, wrong shape whenever `N` divides the stream -- which is engine invariant 3's
            # own hazard ("a transposed `.ill` yields a plausible sDA").
            if s.ncols != n_hours:
                raise MatrixShapeError(
                    f"the sky matrix on disk ({sky_path}) has {s.ncols} columns, but the weather "
                    f"this run loaded measures {n_hours} hours. dctimestep pairs a coefficient "
                    "column with a sky row by position, and the final rcollate reshapes the "
                    "result with -ic on the second number, so multiplying them would either "
                    "publish a silently reshaped .ill or fail at the last step of the chain. The "
                    "sky matrix was built from different weather than this run is using: remove "
                    f"{sky_path.name} to rebuild it, or run against the weather input it was "
                    "built from."
                )

    return v, t, d, s


def five_phase_shape_check(
    target: ThreePhaseGroup, *, n_points: int, sky_subdivision: int, matrices: Path,
    weather_input_id: str, n_hours: int,
) -> None:
    """Run the `V*T*D*S` leg's shape precondition once, at submit -- the identical placement and
    reasoning `three_phase_shape_check`'s own docstring gives (ruling R71: the job worker offers no
    per-step Python hook, so a submit-time precondition is the only home this check can have).

    **Deliberately checks only three joins, not `check_five_phase_shapes`'s four -- review round 1's
    own finding, kept here as the record of why.** A first version of this function also built `cds`
    and `ssun` headers and called `check_five_phase_shapes` with all six operands:

    ```
    cds  = DcMatrixHeader(ncols=reinhart_bins(sky_subdivision), ...)
    ssun = DcMatrixHeader(nrows=reinhart_row_count(sky_subdivision), ...)
    check_five_phase_shapes(v, t, d, s, cds, ssun)
    ```

    `reinhart_bins` (`rad_engine.matrices`, the measured table `{1:146, 2:578, 4:2306, 6:5186}`) and
    `reinhart_row_count` (`rad_engine.skymatrix`, `144*mf*mf+2`) are two independently-coded routes to
    the *same measured fact*, and they agree exactly for every `mf` either accepts; outside
    `{1, 2, 4, 6}`, `reinhart_bins` raises before `cds` is even constructed. So `cds.ncols !=
    ssun.nrows` **could not evaluate `True` for any input this path could reach** -- not textually
    `x == x`, but `x == f(x)` where `f` is a guaranteed identity over the whole reachable domain,
    which is the runtime effect ruling R74 forbids. Worse than merely inert: reviewed and found to
    protect nothing at submit time while reading, in its own docstring, as though it did -- exactly
    the shape `docs/superpowers/plans/2026-08-17-phase-7a-five-phase-chain.md`'s own R74 history
    warns is worse than no check, because a reader trusts a check that cannot fail.

    **What actually guards against a `-bn 5185` regression today: the argv-level assertion that
    `build_cds_argv` emits the bare symbol `-bn Nrbins`, never a literal digit** -- `rad_engine.
    matrices.build_cds_argv`'s own docstring and code, pinned by `tests/test_matrices.py::
    TestBuildCdsArgv::test_cds_argv_carries_the_direct_calculation_flags_and_the_symbol_bn`
    (`assert argv[argv.index("-bn") + 1] == "Nrbins"`). That is a real, load-bearing test today; this
    function's own docstring should not imply a second, submit-time guard exists alongside it when it
    does not.

    **`check_five_phase_shapes`'s own fourth join is correct and is kept, in `rad_engine.annual`, for
    when a real header exists to check it against** -- a reused `Cds`/`Ssun` artifact read back off
    disk (the way `_predict_v_t_d_s` already reads a real `S` when one exists), or a post-build
    verification once `Cds`/`Ssun` have artifact kinds of their own (Task 12). Wiring it here now,
    against two predictions of the same `mf`, would only restore the unfalsifiable version review
    round 1 removed.

    Everything else is unchanged from `three_phase_shape_check`: V/T/D/S come from `_predict_v_t_d_s`,
    the same shared prediction, so this function and that one cannot silently disagree about what a
    correct `V*T*D*S` chain looks like (ruling R30).
    """
    v, t, d, s = _predict_v_t_d_s(
        target, n_points=n_points, sky_subdivision=sky_subdivision, matrices=matrices,
        weather_input_id=weather_input_id, n_hours=n_hours,
    )
    check_three_phase_shapes(v, t, d, s)


#: Bumped when a change moves what the three-phase daylight matrix's **bytes** mean without moving
#: any flag a caller can see. Recorded in every `dmx` artifact's own `parameters`, so a matrix built
#: before the change cannot satisfy `_parameters_match` for a run after it.
#:
#: **1 -> 2 (ruling R92, the daylight-matrix fix -- Phase 7a, not 7b).** `build_d_argv` hardcoded `-ab 0`, so `D` carried
#: no ground- or obstruction-reflected light and the shipped three-phase result was low by ~30% on
#: the office fixture. Every `dmx` sealed before that fix is wrong in a way its own record cannot
#: express -- it names the `-ab` the caller *asked* for, which is exactly what a post-fix run asks
#: for again. Revision 1 is never written by this code; it is the absent key every pre-fix record
#: has, and absence is what makes them fail to match.
_DAYLIGHT_MATRIX_REVISION: Final[int] = 2

#: Bumped when a change moves what the five-phase `.ill`'s **bytes** mean without moving any digest
#: input a caller can see. Recorded in the `ill5` artifact's own `parameters`, so a matrix built
#: before the change cannot satisfy `_parameters_match` for a run after it.
#:
#: **1 -> 2 (Phase 7b Task 2, following Task 1's `rcollate` fix).** Task 1 rewrote the final
#: `rcollate` from headerless hour-major to headered sensor-major by changing one flag -- and the
#: argv is not a digest input, `_parameters_match` does not compare it, and `is_reusable` re-digests
#: the file already on disk, so nothing else could tell a pre-change artifact from a post-change one.
#: A key absent from every pre-fix record is the only separator. Same reasoning as
#: `_DAYLIGHT_MATRIX_REVISION`; see its comment for the R92 case that earned it.
_FIVE_PHASE_RESULT_REVISION: Final[int] = 2

#: Bumped when a change moves what the ASE direct-sun result's **bytes** mean without moving any
#: digest input a caller can see. Recorded in the `illdir` artifact's own `parameters`, so a matrix
#: built before the change cannot satisfy `_parameters_match` for a run after it.
#:
#: **Why this leg gets one on the day it ships, rather than when it is first corrected.** The two
#: constants above were each added *after* a defect had already sealed artifacts nothing could tell
#: apart -- R92's `-ab 0` and R98's `rcollate` flag -- and in both cases revision 1 is the absent
#: key every pre-fix record carries, which works only because the fix and the constant arrived
#: together. This leg's own most likely correction has the same signature: the four stages between
#: `asemx` and the sealed `.ill` are argv, and `argv` is not a digest input -- `_parameters_match`
#: does not compare it and `is_reusable` re-digests the file already on disk. Drop the `rcalc`
#: stage, or reshape with the wrong flag, and the sealed file is irradiance (or hour-major) wearing
#: a lux reader's shape, with a parameter dict identical to the correct one's. Starting at 1 costs
#: nothing and means the *next* correction has a key to move.
_ASE_DIRECT_REVISION: Final[int] = 1


def _v_step(request: ChainRequest, target: ThreePhaseGroup, v_path: Path) -> Step:
    """One window group's V matrix -- step 1 of the 3-phase chain, moved out of
    `three_phase_steps` unchanged (Phase 7d-1 Task 7) so the fan-out can call it once per group.

    **`GLOW_MODIFIER.format(group=target.group.name)` is evaluated here, from the group this call
    is about, and that is the single fact the whole per-group decomposition rests on.** `rcontrib`
    attributes contribution by **modifier name** and sums every surface in the octree carrying it,
    so one modifier shared across the groups would make every group's V capture every group's
    aperture: the summed result would be inflated by up to N x, from valid matrices, with no error
    anywhere. Formatting it inside this function -- which takes one `ThreePhaseGroup` and cannot see
    the others -- is what makes the hoist unexpressible rather than merely avoided.
    """
    grid = request.grid
    parameters = request.parameters
    points_path = request.points_path

    # The V matrix's own edge set. `REQUIRED_EDGES["vmx"]` **is** enforced -- geometry, materials,
    # sensor_grid and window_group, all four required -- unlike `dsmx`'s empty row, so this set is
    # checked by `check_edges` at submit rather than merely recorded. The interior octree is named
    # too, for `_dc_edges`' own reason: recompiling the scene invalidates every coefficient in V.
    v_edges = (*_dc_edges(request, request.octree_id), target.group.input_id)

    return Step(
        phase="building view matrix (1/5)",
        argv=tuple(build_v_argv(
            octree=request.octree_path,
            points=points_path,
            aperture=target.group.aperture,
            glow_modifier=GLOW_MODIFIER.format(group=target.group.name),
            parameters=parameters,
            limit_weight=matrix_limit_weight(parameters),
            # **The `#?RADIANCE` header is required, not cosmetic.** Measured against Radiance
            # 6.1a: `dctimestep V T D S` over a headerless V exits 1 with `fatal - unexpected
            # EOF in header`, because the header is where `NCOLS`/`NCOMP` live and an ASCII
            # matrix carries its shape nowhere else. `build_v_argv` defaults `header=False` for
            # the stdout-parsing callers it was written for; this matrix is a file `dctimestep`
            # reads, so it says so. It is also what lets `read_matrix_header` report a shape
            # for this artifact at all.
            header=True,
            processes=request.processes,  # R781
        )),
        cwd=request.root,
        stdout_path=v_path,
        # R43 again: `rcontrib -I+` takes its sensor rays on stdin exactly as `rfluxmtx` does.
        stdin_path=points_path,
        artifact=ArtifactSpec(
            artifact_id=f"vmx:{target.group.name}:{grid}",
            kind="vmx",
            path=v_path,
            built_from=v_edges,
            parameters={
                **parameters_dict(parameters),
                "lw": matrix_limit_weight(parameters),
                "klems_bins": target.klems_bins,
                "n_sensors": request.n_sensors,
                **_processes_parameter(request),  # R781
                # R846: whether every grid point is behind this group's room-facing plane, read by
                # `results` beside an all-zero V. Digest-excluded (`DIGEST_EXCLUDED_PARAMETERS`).
                **({"grid_behind_aperture": grid_behind_aperture(
                    target.group.aperture, request.grid_points)} if request.grid_points else {}),
            },
        ),
        estimate_s=240.0,
    )


def _genklemsamp_operand(sample_rad: Path) -> tuple[Path, str]:
    """`(cwd, operand)` for a `genklemsamp` step over `sample_rad`: its own directory and its bare
    name, the name refused unless it is inside `SHELL_SAFE_PATH_CHARACTERS` (ruling R754, final
    review).

    genklemsamp runs `system "xform -m void @ARGV | oconv - > $td/surf.oct"`, the file operand
    unquoted (read, Radiance 6.1a). The sampling file is job-local and its name is built here from a
    legal group name, but the directories above it are the caller's project root, which may hold a
    space or a quote. Running the step in that directory keeps them out of the shell line; the check
    is on the exact string the argv carries.
    """
    operand = sample_rad.name
    refuse_shell_unsafe_paths([operand], field="project", program="genklemsamp")
    return sample_rad.parent, operand


def _genklemsamp_step(request: ChainRequest, target: ThreePhaseGroup, rays_path: Path) -> Step:
    """One window group's Klems ray sample -- step 2, moved out of `three_phase_steps` unchanged.

    **`rays_path` is passed in rather than derived here**, because step 3 reads the identical path
    as its `stdin_path` and the guarantee that the two are one path is what makes the pipe a pipe
    (ruling R30). `three_phase_steps` names it once, per group, and hands it to both.

    **`window_rad` is this group's own sampling geometry** (`_sample_rad_for`), not the job's one
    file -- review round 1's Important 1. It was `request.sample_rad_path`, a single file written
    from one group's aperture and handed to every group's `genklemsamp`, so every group but the one
    it was written from sampled the wrong facade's polygons and carried them into its D matrix.
    """
    sample_dir, sample_name = _genklemsamp_operand(_sample_rad_for(request, target.group))

    return Step(
        phase="sampling Klems directions (2/5)",
        argv=tuple(build_genklemsamp_argv(
            aperture=target.group.aperture,
            count=_KLEMS_SAMPLE_COUNT,
            window_rad=Path(sample_name),
        )),
        cwd=sample_dir,
        scene_inputs=(sample_name,),  # R755: scanned by the worker before the spawn
        # The pipe's first half: rays to a job-local file, read by step 3's `stdin_path`. This
        # step commits **no artifact** -- the rays are an intermediate of one job, not something
        # a later run could reuse, and sealing them would put a file in the manifest whose only
        # consumer is the step immediately after it.
        stdout_path=rays_path,
        stdin_path=None,  # DEVNULL: genklemsamp reads the .rad as a positional argument.
        artifact=None,
        estimate_s=20.0,
    )


def _d_step(
    request: ChainRequest, target: ThreePhaseGroup, d_path: Path, rays_path: Path
) -> Step:
    """One window group's D matrix -- step 3, moved out of `three_phase_steps` unchanged.

    `rays_path` is `_genklemsamp_step`'s own `stdout_path`, handed in by the caller for the reason
    that function's docstring gives.
    """
    parameters = request.parameters
    sky_subdivision = request.mf

    # The D matrix's: `REQUIRED_EDGES["dmx"]` requires geometry, materials **with role="ground"**
    # (ruling R9, the strong form) and window_group. The exterior octree is the artifact edge.
    d_edges = (*_dc_edges(request, request.exterior_octree_id), target.group.input_id)

    return Step(
        phase="building daylight matrix (3/5)",
        argv=tuple(build_d_argv(
            octree=request.exterior_octree_path,
            aperture=target.group.aperture,
            mf=sky_subdivision,
            parameters=parameters,
            count=_KLEMS_SAMPLE_COUNT,
            sky_modifier=SKY_MODIFIER,
            header=True,  # same measurement as step 1's -- dctimestep reads D from a file
            processes=request.processes,  # R781
        )),
        cwd=request.root,
        stdout_path=d_path,
        # The pipe's second half. The same path step 2 wrote, named here rather than a copy of
        # it: two spawns, one file, no shell.
        stdin_path=rays_path,
        artifact=ArtifactSpec(
            artifact_id=f"dmx:{target.group.name}",
            kind="dmx",
            path=d_path,
            built_from=d_edges,
            parameters={
                **parameters_dict(parameters),
                "mf": sky_subdivision,
                "count": _KLEMS_SAMPLE_COUNT,
                "sky_modifier": SKY_MODIFIER,
                # **The one key here that describes no flag, and it exists to force a rebuild.**
                # Before R92's daylight-matrix fix this step recorded the caller's
                # requested `-ab` while `build_d_argv` hardcoded `0` -- so a stored `dmx` built
                # at zero bounces claims, in the manifest, the very bounce count a post-fix run
                # asks for. `_parameters_match` would find them equal, `is_reusable` would find
                # the artifact self-consistent, and every existing project would keep serving the
                # under-built matrix forever, with `MATRIX_REUSED` and no error: R75's defect
                # reappearing through a door R75 could not have closed, because nothing about the
                # *stored* record is wrong -- what moved is the meaning of the bytes it names.
                # A key absent from every pre-fix record is the only thing that separates them.
                # `ddmx` deliberately gets none: it was `-ab 0` before and after, its bytes did
                # not move, and invalidating it would force a needless retrace.
                "daylight_matrix_revision": _DAYLIGHT_MATRIX_REVISION,
                **_processes_parameter(request),  # R781
            },
        ),
        estimate_s=180.0,
    )


def _sky_matrix_step(request: ChainRequest, sky_path: Path) -> Step:
    """The run's annual sky matrix -- step 4, moved out of `three_phase_steps` unchanged.

    **Takes no window group, and that is the point of extracting it.** S is a property of the
    weather, the subdivision, the ground and north; every group's multiply names the same file and
    it depends on none of them, so it is built **once** per run however many groups the run fans
    out over. A sky matrix built per group would be N identical spawns of `gendaymtx` sealed under
    one id -- or, worse, N ids for one matrix.
    """
    sky_subdivision = request.mf
    north = request.north
    weather_input_id = request.weather_input_id

    sky_spec = SkyMatrixSpec(
        weather_path=request.weather_path,
        sky_subdivision=sky_subdivision,
        ground_reflectance=request.ground_reflectance,
        north=north,
    )

    return Step(
        phase="building annual sky matrix (4/5)",
        argv=tuple(build_gendaymtx_argv(sky_spec)),
        cwd=request.root,
        stdout_path=sky_path,
        stdin_path=None,  # DEVNULL: gendaymtx takes the .wea as a positional argument.
        artifact=ArtifactSpec(
            artifact_id=f"smx:{weather_input_id}",
            kind="smx",
            path=sky_path,
            built_from=_smx_edges(request),
            parameters={
                "mf": sky_subdivision,
                "north_east_of_north": north,
                "ground_reflectance": request.ground_reflectance.value,
                # **`mode` recorded, on the whole-branch review's Minor 4.** `smxd` records
                # `"direct"` and `smxsun` records `"sun_disk"`; this one recorded nothing, and
                # it is the id under which two physically different matrices could be sealed --
                # a total sky and a `gendaymtx -d` sky are both `smx:<weather>` and both the
                # same shape, so `_parameters_match` had no field to tell them apart. No
                # production caller builds a direct-only `smx` today; the field is what keeps
                # that from becoming a silent substitution the day one does. The two new sky
                # kinds got this right and the older one was the outlier.
                #
                # It invalidates every stored `smx`: one rebuild, the same trade
                # `_DAYLIGHT_MATRIX_REVISION` already took for `dmx`, and cheaper here
                # (`gendaymtx` is one spawn of about 45 s, not a traced matrix).
                "mode": "total",
            },
        ),
        estimate_s=45.0,
    )


def group_state_ill_id(grid: str, group_name: str, state: str) -> str:
    """`ill:<grid>:<g>:<s>` -- one window group's annual result in one blind state.

    **One spelling, two readers, and Phase 7d-1 Task 12 is the second reader** --
    `direct_ill_id`'s reason exactly, one leg over. `three_phase_steps` mints this id when it fans
    out per (group, state) and `blend_steps` names every one of them as an edge of
    `illblend:<grid>`; a second, hand-typed `f"ill:{grid}:{name}:{state}"` in the blend is two
    statements of one name that agree until one of them moves, and the failure is not a crash. It
    is `check_edges` refusing a blend whose operand it cannot resolve -- or worse, resolving one
    that is not the matrix the multiply actually wrote.

    `kind` is **`"ill"`**, never a kind of its own: this is an annual illuminance matrix and what
    differs is only which id it occupies (`ArtifactKind`'s own note on `illdir`). The *blend* is
    `kind="illblend"`, and that is a genuine difference -- see `blend_steps`.
    """
    return f"ill:{grid}:{group_name}:{state}"


def group_state_ill_path(results_dir: Path, grid: str, group_name: str, state: str) -> Path:
    """Where `group_state_ill_id`'s matrix is written -- `08_results/annual_<grid>_<g>_<s>.ill`.

    The file half of the same name, for `direct_ill_path`'s reason: deriving the operand paths from
    the ids instead (`results_dir / id.split(":", 1)[1]`) spells `ill:<grid>:<g>:<s>` as
    `08_results/<grid>:<g>:<s>`, a file nothing writes -- ruling R120, which this task would have
    repeated on the blend's own argv had the two halves not come from one place.
    """
    return results_dir / f"annual_{grid}_{group_name}_{state}.ill"


def three_phase_steps(request: ChainRequest, target: ThreePhaseTarget) -> list[Step]:
    """The 3-phase chain's `Step`s: one V and one D per window group, one T per state, one S for
    the run, and one `dctimestep` per (group, state).

    ======  ==========================================  ======================  ==================
    Step    `phase`                                     Produces                argv from
    ======  ==========================================  ======================  ==================
    1       building view matrix (1/5)                  `vmx:<group>:<grid>`    `matrices.build_v_argv`
    2       sampling Klems directions (2/5)             rays file               `build_genklemsamp_argv`
    3       building daylight matrix (3/5)              `dmx:<group>`           `matrices.build_d_argv`
    ..      *steps 1-3 again, per further group*        ..                      ..
    4       building annual sky matrix (4/5)            `smx:<epw>`             `build_gendaymtx_argv`
    5       multiplying V*T*D*S                         `ill:<grid>[:<g>:<s>]`  `build_three_phase_argv`
    ..      *step 5 again, per further (group, state)*  ..                      ..
    6       summing the window groups                   `ill:<grid>`            `build_rmtxop_sum_argv`
    ======  ==========================================  ======================  ==================

    **The `(n/5)` labels are the single-group chain's, kept verbatim** -- one group with one state
    is still every run `rad_mcp.annual_tool` submits today, and for that run this list is the same
    five steps in the same order it has always been, with the same `phase` strings.

    **The fan-out is on the multiply alone** (Phase 7d-1 Task 7). `I = sum_g V_g*T_g*D_g*S` is
    linear in the groups, so V and D are built once per group and shared across that group's states,
    and S is built once for the run -- which is why the blend costs N groups of chain work plus S
    cheap multiplies, rather than one chain per blind combination. **The decomposition is this
    repo's own inference**, from the linearity of light transport and from `GLOW_MODIFIER` being
    formatted with each group's own name inside `_v_step` (one `rcontrib` modifier per group, so
    group `g`'s V captures group `g`'s aperture and no other). The LBNL three-phase guide never
    states a sum over window groups and is not cited for one (ruling R132).

    **Under `blind_control="lm83"` the summed `ill:<grid>` is not emitted.** The per-group results
    are the blend's operands and `rad_engine.blend` writes `illblend:<grid>` from them. Two ids, so
    a `blind_control="none"` re-run cannot rebuild in place over a scheduled result -- ruling R107's
    third blocker, closed by construction rather than by teaching `_parameters_match` about a new
    field.

    **A one-group run with no blend seals `ill:<grid>` from its own multiply and emits no sum, and
    that is forced rather than chosen.** `build_rmtxop_sum_argv` refuses a single operand, because
    `rmtxop A` is a copy and a copy sealed under an id that says "sum" reports one facade as the
    whole story. So there are only two ways a lone group can reach the id every 7c reader resolves
    (`results`, `metrics`, `sda`): pay for a full copy of an annual matrix on every run this server
    currently submits, or let the multiply write it directly -- which is what it already did before
    this task, at the same path, with the same argv and the same parameter dict. The cost of that
    choice is one rebuild: a single-group project that later switches to `lm83` builds its `.ill`
    again under the per-group id instead of reusing it. A multi-group project pays nothing, because
    it fans out under both settings.

    **T is not a step, and that is what makes it the only term a BSDF change invalidates** (ruling
    R62). It is the XML `window_group(action='assign_bsdf')` copied into `07_matrices/` and sealed
    as `tmx:<group>:<state>`; this chain names that file as the multiply's second operand and as an
    edge of the `.ill`, and never rebuilds it. **Under `lm83` a group carries several**, and each is
    multiplied on its own -- `tp_group.tmx_paths[state]`, never `.tmx_path`, which is the one-state
    narrowing and raises here rather than picking whichever state sorted first.

    **Steps 2 and 3 are one pipe expressed as two spawns, never a shell pipe.**
    `rad_engine.matrices`' module docstring forbids the shell form -- *"it is what keeps each stage's
    stderr separately attributable"* -- so step 2 writes `genklemsamp`'s rays to a job-local file via
    `stdout_path` and step 3 reads that same path via `stdin_path`, the existing R43 mechanism with
    no new job-layer capability. `_KLEMS_SAMPLE_COUNT` is passed to **both** builders as the same
    variable, which is the by-construction guarantee both their docstrings describe: a mismatched
    `-c` is a silent wrong answer, not an error. **The rays file is named per group** (`klems_rays_
    <group>.txt`): one filename for every group would have the second group's `genklemsamp`
    overwrite the first group's rays, which is harmless only for as long as the worker runs steps
    strictly in order -- a property `rad_engine.jobs` holds today and states nowhere as a guarantee
    this chain may rely on.

    **Two octrees, not one, and they are genuinely different scenes.** The V matrix traces the
    interior with the aperture turned into a `glow` light source (`klems.glow_surface`); the D
    matrix traces the exterior against a white sky dome, and `genklemsamp` samples the aperture as
    **plain geometry** (`klems.polygon_surface`) -- a `glow` primitive there would make it sample a
    light-emitting material instead of a surface. `rad_engine.klems`'s own docstring states the
    split; `run_annual` is the caller that honours it when it compiles the two octrees.

    Estimates are **declared, not calibrated**, exactly as `two_phase_steps`' are, and
    `JobStore.submit` attaches `RUNTIME_ESTIMATE_UNCALIBRATED` to say so -- to the jobs whose steps
    declare an `estimate_s`, which every step this module builds does; see `two_phase_steps`'
    docstring for the guard and its measurement.

    **The two files this chain needs at submit -- the job's `points.pts` and its
    `aperture_sample.rad` -- are written by the caller**, in that order, immediately before this
    call, for the reason `ChainRequest` records: the containment primitive those writes must go
    through (R48/finding C1) raises a wire-layer exception this module may not name. Both land in the
    **job directory** rather than the project root, which is a property of the paths handed in: a
    job-local copy cannot be swapped by a concurrent `window_group` or `sensor_grid` call between
    submit and the step that reads it (R45).

    **Takes the whole `ThreePhaseTarget`, not one `ThreePhaseGroup` (ruling R126).** Task 6 left the
    narrowing at the call site deliberately, because Task 6 did not fan out; this task is what fans
    out. `ChainRequest.target` stays a `ThreePhaseGroup`, because it feeds `five_phase_steps`, which
    is single-group by construction.
    """
    grid = request.grid
    sky_subdivision = request.mf
    weather_input_id = request.weather_input_id

    matrices = request.matrices_dir
    sky_path = matrices / f"smx_{weather_input_id}.mtx"

    # **The per-group ids are minted whenever a blend will consume them, or whenever there is more
    # than one group to sum.** The remaining case -- one group, no blend -- is the run every caller
    # submits today, and it seals `ill:<grid>` straight off its own multiply; see the docstring for
    # why a one-operand `rmtxop` is not the alternative it looks like.
    fan_out = target.blind_control == LM83_BLIND_CONTROL or len(target.groups) > 1

    # **A target with no groups is refused here, not left to produce a one-step plan.**
    # `three_phase_target` already refuses a project with no window group, so nothing reachable from
    # the wire arrives here empty -- but `ThreePhaseTarget` is public and constructible, and the
    # loops below simply do not execute for an empty one: the plan would be the sky matrix alone,
    # the job would exit 0 having built no `.ill` at all, and the failure would surface as a missing
    # artifact somewhere downstream. Same posture, and the same reason, as
    # `ThreePhaseGroup.state`'s own `!= 1` refusal.
    # **A chain that does not fan out multiplies exactly one T, so it is handed exactly one state
    # -- review round 1's Minor.** `not fan_out` means one group and no blend, and the loop below
    # then mints `ill:<grid>` once per state: a two-state group under `blind_control="none"` sealed
    # that one id **twice, at one path**, the second multiply overwriting the first. Unreachable
    # from the wire (`three_phase_target` resolves several states only under `lm83`) and refused
    # anyway, for the reason its empty sibling below is refused: `ThreePhaseTarget` is public and
    # constructible, and the two cases were inconsistent -- one refused, one silently planned a
    # self-overwriting job.
    if not fan_out and target.groups and len(target.groups[0].states) != 1:
        group = target.groups[0]
        raise ThreePhaseNotBuildableError(
            f"window group {group.group.name!r} has {len(group.states)} BSDF states bound "
            f"({', '.join(group.states)}), and an unscheduled run has exactly one result to "
            "seal per grid -- so every state would be sealed under one unscheduled result id, at "
            "one path, each multiply overwriting the last. Choosing "
            "which state an hour uses is the LM-83 blind-schedule blend, which is what "
            "blind_control='lm83' resolves the states for; under it every state gets a result of "
            "its own. Bind a single state to run unscheduled."
        )

    if not target.groups:
        raise ThreePhaseNotBuildableError(
            "the 3-phase chain was handed a target with no window groups, so there is nothing to "
            "multiply a T through. Resolve the target with three_phase_target, which refuses a "
            "project with no window group registered and says how to define one."
        )

    steps: list[Step] = []
    # **Each group's two matrix paths are derived once, here, and carried to the multiply** rather
    # than spelled a second time in the loop below: two derivations of one path is how a
    # `dctimestep` comes to name a file no step in the same job wrote (ruling R30).
    prepared: list[tuple[ThreePhaseGroup, Path, Path]] = []
    for tp_group in target.groups:
        group_name = tp_group.group.name
        v_path = matrices / f"vmx_{group_name}_{grid}.mtx"
        d_path = matrices / f"dmx_{group_name}.mtx"
        # One path, named once and handed to both halves of the pipe (R30). Per group, for the
        # reason the docstring gives.
        rays_path = request.job_dir / f"klems_rays_{group_name}.txt"
        steps.append(_v_step(request, tp_group, v_path))
        steps.append(_genklemsamp_step(request, tp_group, rays_path))
        steps.append(_d_step(request, tp_group, d_path, rays_path))
        prepared.append((tp_group, v_path, d_path))

    # **S after the groups' matrices and before every multiply**, which is where it has always sat
    # for a single-group run (step 4 of 5) and is what keeps that run's plan the identical five
    # steps in the identical order. It names no group and no group names it.
    steps.append(_sky_matrix_step(request, sky_path))

    # **The ids and the paths are collected in the same loop, and the sum is handed the paths.**
    # Deriving the operand paths from the ids (`results_dir / id.split(":", 1)[1]`) spells
    # `ill:<grid>:<g>:<s>` as `08_results/<grid>:<g>:<s>`, a file nothing writes -- ruling R120.
    per_group_ill_ids: list[str] = []
    per_group_ill_paths: list[Path] = []
    for tp_group, v_path, d_path in prepared:
        group_name = tp_group.group.name
        for state in tp_group.states:
            if fan_out:
                # **Through `group_state_ill_id`/`group_state_ill_path`, not spelled here** (Task
                # 12): `blend_steps` names every one of these ids as an edge and every one of these
                # paths as a `--contribution`, so the id and the file are derived in one place for
                # `direct_ill_id`'s reason.
                ill_id = group_state_ill_id(grid, group_name, state)
                ill_path = group_state_ill_path(request.results_dir, grid, group_name, state)
                phase = f"multiplying V*T*D*S for {group_name}/{state}"
            else:
                ill_id = f"ill:{grid}"
                ill_path = request.results_dir / f"annual_{grid}.ill"
                phase = "multiplying V*T*D*S (5/5)"
            per_group_ill_ids.append(ill_id)
            per_group_ill_paths.append(ill_path)
            steps.append(Step(
                phase=phase,
                # **Order is the arithmetic** (`build_three_phase_argv`'s own docstring): V, T, D, S,
                # composing as ((V @ T) @ D) @ S. Three of the four wrong orderings of four file
                # arguments still name a runnable command, so the pairing lives in the builder rather
                # than at this call site.
                argv=tuple(build_three_phase_argv(
                    v_path, tp_group.tmx_paths[state], d_path, sky_path,
                )),
                cwd=request.root,
                stdout_path=ill_path,
                # `dctimestep` needs no stdin: every operand is a file argument (R43, measured).
                stdin_path=None,
                artifact=ArtifactSpec(
                    artifact_id=ill_id,
                    kind="ill",
                    path=ill_path,
                    # **All four terms named, T included.** T is not a step, so nothing else in this
                    # job would record that the result depends on the bound BSDF -- and a BSDF change
                    # invalidating the `.ill` while leaving V, D and S valid is exactly the row
                    # docs/tool-surface.md's invalidation table gives it.
                    built_from=(
                        f"vmx:{group_name}:{grid}",
                        tp_group.tmx_ids[state],
                        f"dmx:{group_name}",
                        f"smx:{weather_input_id}",
                    ),
                    parameters={
                        "mf": sky_subdivision,
                        "n_points": request.n_sensors,
                        "method": "3phase",
                        "window_group": group_name,
                        "state": state,
                    },
                ),
                estimate_s=60.0,
            ))

    if fan_out and target.blind_control != LM83_BLIND_CONTROL:
        summed_path = request.results_dir / f"annual_{grid}.ill"
        steps.append(Step(
            phase="summing the window groups (I = sum_g V_g*T_g*D_g*S)",
            argv=build_rmtxop_sum_argv(inputs=per_group_ill_paths),
            cwd=request.root,
            # `rmtxop` takes every operand as a file argument and writes the sum to stdout.
            stdout_path=summed_path,
            stdin_path=None,
            artifact=ArtifactSpec(
                artifact_id=f"ill:{grid}",
                kind="ill",
                path=summed_path,
                built_from=tuple(per_group_ill_ids),
                parameters={
                    "mf": sky_subdivision,
                    "n_points": request.n_sensors,
                    "method": "3phase",
                    # **The operand set, named as a parameter, and it is not redundant with
                    # `built_from`.** `reuse_decision` poisons a step whose upstream rebuilds in
                    # *this* job, so a group added or renamed is caught: its `.ill` has never been
                    # sealed, it rebuilds, and this sum rebuilds with it. A group **removed** is
                    # caught by nothing there -- the survivors are unchanged and reusable, and
                    # `is_reusable` asks only whether the stored artifact is self-consistent with
                    # its own recorded edges. Without this key the three-group sum would be served
                    # to a two-group run with the removed facade's light still in it, flagged
                    # `MATRIX_REUSED`. `_parameters_match` compares the whole dict, so one string
                    # closes it.
                    "window_groups": ",".join(
                        tp_group.group.name for tp_group in target.groups
                    ),
                },
            ),
            estimate_s=30.0,
        ))

    # **Last, over the finished plan** (`refuse_colliding_outputs`): the `_`-join that turns two
    # distinct artifact ids into one filename is a property of the names this run resolved, not of
    # any one step, so it is checked once the paths all exist.
    refuse_colliding_outputs(steps, request.loaded)
    return steps


# --- the 5-phase chain (Phase 7a) ----------------------------------------------------------------


#: The five `ChainRequest` fields `five_phase_steps` cannot build without, each with the phrase its
#: own refusal names it by. A tuple rather than five `or`-ed conditions so the message can say
#: **which** field is missing -- see the loop that reads it.
_FIVE_PHASE_REQUIRED_FIELDS: Final[tuple[tuple[str, str], ...]] = (
    ("target", "the resolved ThreePhaseGroup and its sealed T, from three_phase_target.single"),
    ("black_octree_id", "the manifest id of the black octree (build_black_octree's output)"),
    ("black_octree_path", "the path of the black octree (build_black_octree's output)"),
    ("suns_octree_id", "the manifest id of the suns octree (build_suns_octree's output)"),
    ("suns_octree_path", "the path of the suns octree (build_suns_octree's output)"),
)

#: `direct_sun_steps`' own two, in `_FIVE_PHASE_REQUIRED_FIELDS`' shape and named one at a time for
#: R74's reason: one message covering both tells a caller *which two could be wrong*, not which one
#: is. The pair is never partially populated by anything this repo ships -- Task 11's submit-time
#: block fills both from one branch -- so a `None` here means a caller has drifted.
_DIRECT_SUN_REQUIRED_FIELDS: Final[tuple[tuple[str, str], ...]] = (
    ("ase_octree_id", "the manifest id of the ASE octree (build_ase_octree's output)"),
    ("ase_octree_path", "the path of the ASE octree (build_ase_octree's output)"),
)


def five_phase_steps(request: ChainRequest) -> list[Step]:
    """The 5-phase chain's `Step`s, complete: three terms and the combination that turns them into
    one `.ill` -- ``I_5ph = V*T*D*S - Vd*T*Dd*Sds + Cds*Ssun`` (`docs/specs/five-phase-method.md`).

    ======  =====================================================  ==========  =====================
    Step    `phase`                                                Produces    argv from
    ======  =====================================================  ==========  =====================
    1-4     `three_phase_steps`' own vmx/rays/dmx/smx, reused       vmx/dmx/smx  `three_phase_steps`
    5       multiplying V*T*D*S for I_3ph                          --          `build_three_phase_argv`
    6       collating I_3ph sensor-major (i_3ph)                   --          `build_rcollate_leg_argv`
    7       building direct view matrix (Vd)                       vdmx        `matrices.build_v_argv`
    8       sampling Klems directions for the direct pair (Dd)     --          `build_genklemsamp_argv`
    9       building direct daylight matrix (Dd)                   ddmx        `matrices.build_d_argv`
    10      building direct-sun sky matrix (Sds)                   smxd        `build_gendaymtx_argv`
    11      multiplying Vd*T*Dd*Sds for I_ds3ph                    --          `build_three_phase_argv`
    12      collating I_ds3ph sensor-major (i_ds3ph)                --          `build_rcollate_leg_argv`
    13      building direct-sun coefficient matrix (Cds)            cdsmx       `matrices.build_cds_argv`
    14      building the sun-disk sky matrix (Ssun)                 smxsun      `build_gendaymtx_argv`
    15      multiplying Cds*Ssun for I_ds5ph                        --          `build_dctimestep_argv`
    16      collating I_ds5ph sensor-major (i_ds5ph)                --          `build_rcollate_leg_argv`
    17      pasting the three terms together (rlam)                 --          `build_rlam_argv`
    18      combining the three terms into illuminance (combine)    --          `build_five_phase_combine_argv`
    19      reshaping the annual result into sensors x hours (ill)  ill         `build_rcollate_final_argv`
    ======  =====================================================  ==========  =====================

    **Step 19 seals `ill5:<grid>` at `08_results/annual_<grid>_5ph.ill`, not the 2-/3-phase chains'
    `ill:<grid>` at `08_results/annual_<grid>.ill`.** See the `ill_path` assignment's own comment for
    the collision that closes and for the option not taken.

    Artifact-producing steps, in the order they appear above: `vmx`, `dmx`, `smx`, `vdmx`, `ddmx`,
    `smxd`, `cdsmx`, `smxsun`, `ill` -- what `[s.artifact.kind for s in five_phase_steps(request) if
    s.artifact]` returns, and what `tests/test_chain.py::
    test_the_five_phase_step_list_is_ordered_and_complete` pins. **No `tmx` in that list, and this is
    a deliberate departure from an earlier draft of this task's own test snippet, not an oversight.**
    `three_phase_steps`' own docstring states ruling R62 plainly -- *"T is not a step, and that is
    what makes it the only term a BSDF change invalidates"* -- and every task through Task 11 that
    touched this chain held that line without exception: T is the XML `window_group(action=
    'assign_bsdf')` already sealed as `tmx:<group>:<state>`, consumed as a file argument, never
    rebuilt. Adding a step that produces a *second* `tmx`-kind artifact here would either collide with
    that sealed id or invent a new one backed by no real computation -- pure risk for a chain that
    already carries T correctly. Instead, `target.tmx_id` is named once, directly, in the final `ill`
    step's own `built_from` below (the same place `three_phase_steps`' own `ill` step names it) --
    which is what the brief's own "T is shared" comment was gesturing at: T is not rebuilt for the
    second term because it is one file, referenced twice, never a second artifact.

    ## Reuse, not re-derivation (ruling R30)

    **Steps 1-4 are `three_phase_steps(request, target)`'s own first four `Step`s, unpacked and used
    as-is.** `V*T*D*S` is the identical multiplication a standalone 3-phase run performs -- same
    octree, same aperture, same Klems basis, same sky matrix -- so building it a second way here
    would be exactly the second arithmetic path ruling R30 forbids, and the two could silently drift
    apart (argv order, `-lw`, header flags) the way `check_three_phase_shapes`'/`check_five_phase_
    shapes`' own history already shows happening to *shape checks* written twice. Only
    `three_phase_steps`' own fifth step -- the `dctimestep V T D S` writing straight to `ill_path` and
    sealing it as the published result -- is not reused: that step's whole meaning (*this is the
    answer*) is wrong here, where `V*T*D*S` is one of three terms still to be combined. Its `dctimestep`
    *argv* is reused (`build_three_phase_argv`, called again with the same four paths this function
    reads off the reused steps' own `stdout_path`), but it writes to a job-local intermediate and
    carries no `ArtifactSpec` -- rebuilt as step 5 above, immediately followed by the `rcollate` that
    turns it into the `i_3ph` leg `rlam` will read.

    **`build_three_phase_argv` is reused a second time, for `Vd*T*Dd*Sds`, rather than writing a
    second four-operand `dctimestep` builder.** The shape is identical -- view matrix, T, daylight
    matrix, sky vector, in that order -- and `Vd`/`Dd`/`Sds` are simply a different view/daylight/sky
    triple than `V`/`D`/`S`, not a different equation. `Cds*Ssun` (step 15) is the two-operand form
    instead, and reuses `build_dctimestep_argv` for the identical reason: `dctimestep DCspec [skyf]`
    is exactly this pipe's own shape (`docs/specs/five-phase-method.md` 4.5: `dctimestep -n 8760 -if
    matrices/directsun.dsmx matrices/OakLand_direct_m6.smx`), already built for the 2-phase chain.

    ## Sds, Ssun and the combination -- this task's own additions

    **`Sds` and `Ssun` are both `gendaymtx`, at `request.mf`, distinguished only by
    `SkyMatrixSpec.mode`** -- `"direct"` for `Sds` (`Vd*T*Dd*Sds`'s own sky-side operand, the same
    subdivision `D`/`Dd` are binned into) and `"sun_disk"` for `Ssun` (`Cds*Ssun`'s own, the same
    subdivision `Cds`'s `-bn Nrbins` bins into, `build_cds_argv`'s own `mf=request.mf`). **Both use
    `request.mf`, not a literal `6`**, matching `Cds`'s own already-committed choice (Task 10): the
    spec's worked example happens to run every direct-sun term at `MF:6`, but nothing in this chain
    requires that specific number -- only that `Cds`'s own Reinhart bin count and `Ssun`'s own row
    count agree, which holding both to the one `request.mf` guarantees by construction rather than by
    two callers separately remembering to type the same digit.

    **The `rcollate -h -oc 1` after every leg is the spec's own hedge, carried forward rather than
    resolved.** `docs/specs/five-phase-method.md` sec 5 states its own uncertainty about this step --
    *"likely making it sensor-major"* -- and this function does not pretend otherwise:
    `build_rcollate_leg_argv`'s own docstring repeats the caveat, and check 7.4
    (`docs/superpowers/plans/2026-08-17-phase-7a-five-phase-chain.md` Task 15, a sunless run whose
    `I_5ph` must equal `I_3ph` exactly) is the first thing in this repo that turns it into a
    measurement rather than an assumption. If that check fails, this ordering is where to look first.

    **The combination is `rlam` then `rcalc` then `rcollate`, three spawns, never a shell pipe** --
    the same "two spawns, not a shell pipe" discipline `rad_engine.matrices`' own module docstring
    states for the D-matrix pipe, extended to three stages: `rlam`'s stdout is a job-local file
    (`Step.stdout_path`), which `rcalc` reads on stdin (`Step.stdin_path`), whose own stdout is a
    second job-local file the final `rcollate` reads. `build_five_phase_combine_argv`'s own docstring
    carries the sign discipline in full -- `$1-$4+$7` per channel, why it is four separate `-e` flags
    rather than the spec's own one semicolon-joined clause, and why a flipped sign is a plausible wrong
    answer rather than an error (engine invariant 3).

    **This chain's reuse story is narrower than `three_phase_steps`' own, and that is worth stating
    rather than discovering by surprise.** `reuse_decision` (below) sets `Step.reuse` only for a step
    that carries an `ArtifactSpec` -- *"a step with no artifact is never reused"*, that function's own
    docstring, because there is no artifact id to ask `is_reusable` about. Ten of this function's
    nineteen steps have none: every `dctimestep`/`rcollate` leg step, `rlam`, and the final `rcalc`.
    So even a fully-cached re-run -- every one of `vmx`/`dmx`/`smx`/`vdmx`/`ddmx`/`smxd`/`cdsmx`/
    `smxsun` reused, nothing rebuilt -- still re-spawns all three `dctimestep` multiplications, all
    three per-leg `rcollate`s, `rlam`, `rcalc` and the final `rcollate`: nine subprocess spawns with
    no matrices to rebuild behind them. This is not a correctness defect -- every one of those spawns
    reads the same cached matrix files a rebuild would have produced, so the *result* is identical --
    but it means the 5-phase method's reuse savings stop at the eight matrices and never reach the
    combination pipeline itself, unlike a plain 3-phase re-run, where a fully-cached case reuses its
    one `ill`-producing step too. Sealing the combination's own intermediates as artifacts would close
    this, at the cost of exactly the "artifact with no real reuse consumer" problem `three_phase_steps`'
    own `genklemsamp` step already avoids by carrying no `ArtifactSpec` -- left as a known, named
    limitation rather than solved here.

    **The final `rcollate -hi -fa1 -ic n_hours` reads `request.n_hours`, never `ANNUAL_TIMESTEPS` and never
    the literal `8760`.** `build_rcollate_final_argv`'s own docstring gives the defect this avoids in
    full: a hardcoded `8760` would reshape a result computed over a shorter (or longer) weather file
    into nonsense with no error from either `rcollate` or `dctimestep` -- silently, which
    `ChainRequest.n_hours`'s own docstring names as this repo's worst failure mode (ruling R47's own
    history). `n_hours` is wired to a real weather record as of Phase 7a Task 14
    (`annual_tool._chain_request` passes `state.weather.n_hours` on its one production call site --
    see `ChainRequest.n_hours`'s own docstring for what is and is not exercised by a real call yet).
    `dataclasses.replace(request, n_hours=...)` is still how `tests/test_chain.py` and Task 15's
    micro-scene test move it off the default directly, without going through `_chain_request` at all.

    **The final `ill` step's `built_from` names all nine upstream terms plus T, not merely the three
    this function itself built new.** `docs/tool-surface.md`'s invalidation table is why: a BSDF
    change must invalidate this result exactly as it does `three_phase_steps`' own `ill` (T is named
    once, per the "no `tmx` step" note above), and a change to any of the eight matrices --
    `vmx`/`dmx`/`smx` from the reused leg, `vdmx`/`ddmx`/`smxd` from the second, `cdsmx`/`smxsun` from
    the third -- must invalidate it too, since every one of them is multiplied into the published
    number. **`REQUIRED_EDGES` still has no `ill` row, and Task 13 deliberately did not add one** --
    not an omission, and not the "Task 13 adds the edge-table entries this method needs" this
    paragraph used to promise. That table's own preamble gives the reason, and a second annual method
    does not change it: `ill` is reachable from a point-in-time run that names none of these nine
    matrices, so there is no edge an `ill` always has, and a row asserting otherwise would refuse a
    correct build. These nine edges are therefore recorded whether or not anything enforces them, the
    same posture `_dc_edges`'s own docstring holds for `dsmx`. What *does* stop a stale `.ill` being
    served is the other half of the machinery: `reuse_decision` poisons every step whose `built_from`
    names an artifact this same job is about to rebuild, transitively, so an `.ill` naming all nine
    cannot be reused while any one of them is being rebuilt.

    ## What this function deliberately does not add a parameter for

    **No `sky_mode` argument, and no parameter for forcing the blind state open.** Both are named as
    requirements Task 15/16 depend on (`docs/superpowers/sdd/2026-08-17-phase-7a-five-phase-chain/
    task-12-brief.md`), and the controller's own pre-flight ruling for this branch is explicit that
    check 7.4 does *not* reach this function through a new keyword: *"Check 7.4 builds both chains
    through `rad_engine.chain` with a `sky_only` `SkyMatrixSpec` and compares the two `.ill` files
    directly"* (`progress.md`). Nothing here forecloses that: `SkyMatrixSpec.mode` already accepts
    `"sky_only"` (Task 6), `Step`/`ArtifactSpec` are ordinary public dataclasses, and every builder
    this function calls is public too -- so a caller that needs a variant chain composes one from the
    same pieces, the way this function itself composes `three_phase_steps`' pieces above, rather than
    this function growing a keyword for every future variant and risking two chains that seal the
    same artifact id (`smx:<weather_input_id>`) under two different physical matrices depending on
    which mode a caller happened to pass -- a collision Task 13's edge/kind work did **not** resolve
    and could not have: the new kinds separate `Sds` (`smxd:<weather>`) and `Ssun`
    (`smxsun:<weather>`) from `S` and from each other, but `sky_only` is a *third* mode of the same
    `smx` kind and id, and giving it one would mean a fourth sky kind for a variant no production
    caller builds. Named here so the gap is a known one rather than an assumed closure.
    Forcing the blind state open needs even less: `request.target` is caller-supplied data
    (`ChainRequest.target`'s own docstring), so a caller wanting the "open" state's `ThreePhaseGroup`
    simply attaches one via `dataclasses.replace(request, target=open_target)` -- nothing in this
    function inspects `target.state` to refuse that.

    **`Vd` and `Dd` share one octree, and the octree and the bounce count are one decision, not
    two** (five-phase-method.md 3.1: *"This octree will be used for both Vd and Dd"*; 3.2: *"Uses
    the all-black model to avoid unwanted specular reflections even with -ab 1"*). `-ab 1` on a
    normally-materialled octree would be a plausible-looking one-bounce **interior** number wearing
    a direct calculation's name -- not an error, just wrong -- so both are built from
    `request.black_octree_path` (Task 8's `build_black_octree`) and nothing here accepts a caller's
    choice of octree the way `three_phase_steps`' V and D take two genuinely different ones.

    **`Vd`'s `-ab` is forced to 1, and `Dd`'s to 0, regardless of `request.parameters`.** Both
    argv builders read `-ab` off `parameters`, and the 3-phase `V` and `D` steps' whole point is to
    honour whatever ambient bounce count the caller's quality preset asks for. The direct pair is not
    that: five-phase-method.md 3.2 states `-ab 1` for `Vd` and 3.3 states `-ab 0` for `Dd` as those
    terms' own definitions, not quality choices, so `dataclasses.replace(request.parameters,
    ambient_bounces=...)` is what reaches each builder -- one bounce and zero bounces, whatever the
    rest of the run is sampled at.

    **`Dd`'s override used to be `build_d_argv`'s own hardcoded `0`, and that is the defect the
    R92 daylight-matrix fix closed.** A builder that fixes a flag fixes it for *every* caller,
    so the 3-phase `D` -- which must be `-ab 2` per five-phase-method.md 2.3 -- silently received the
    direct matrix's bounce count and carried no ground- or obstruction-reflected light. `D` **was**
    `Dd` apart from the octree. See `build_d_argv`'s own docstring for the measurement (+30.4% on
    the office fixture's three-phase mean over lit hours).

    **Both matrices are built with `header=True`.** `build_v_argv` and `build_d_argv` both default to
    `header=False` (`-h`, suppressing the `#?RADIANCE` header) for a caller that parses their stdout
    directly -- exactly wrong for a matrix written to a file a later `dctimestep` will read: measured
    against Radiance 6.1a, `dctimestep` over a headerless operand exits 1 with `fatal - unexpected
    EOF in header`, because the header is where `NCOLS`/`NCOMP` live and an ASCII matrix carries the
    shape nowhere else (see `build_v_argv`'s own docstring, and `three_phase_steps`' V and D steps,
    which set the same argument for the identical reason).

    **`Dd` gets its own `genklemsamp` pipe, not `D`'s.** `genklemsamp`'s rays depend only on the
    aperture and `-c`, never on the receiving octree, so `D`'s and `Dd`'s rays are the same bytes in
    principle -- but this function has no visibility into whether a caller's step list also contains
    the 3-phase legs (Task 9's scope is `Vd`/`Dd` alone), so reusing `D`'s job-local rays file here
    would be reaching for a step this function does not itself build. A second, self-contained pipe
    costs one more `genklemsamp` spawn (`_KLEMS_SAMPLE_COUNT` sensor rays, the same order of cost
    Task 9's own estimate assigns the 3-phase pipe) and is correct regardless of what Task 12
    eventually assembles around it; de-duplicating the two rays files, if it is ever worth doing, is
    that task's call to make with the whole step list in view.

    **Edges follow `vmx`/`dmx`'s own shape, via the same `_dc_edges` helper**, with the black
    octree's artifact id standing in for the interior/exterior one and `target.group.input_id`
    appended exactly as `three_phase_steps` appends it to `v_edges`/`d_edges`. `REQUIRED_EDGES` now
    carries a `vdmx` and a `ddmx` row (Task 13, ruling R82), so `check_edges` enforces these edge
    sets rather than returning early -- including, for `ddmx`, `dmx`'s own strong `role="ground"`
    requirement, which `_dc_edges` satisfies because it names every materials input.

    ## What each term records as its parameters, and why it is the *effective* set

    **`Artifact.parameters` is the flag set the step's argv actually carries, never the flag set the
    caller asked for.** Three of this chain's steps pin an `-ab` the caller's quality does not
    choose: `Vd` at 1 and `Dd` at 0 (both this function's own `dataclasses.replace`, passed to
    builders that read the value rather than fixing it) and `Cds` at 1 (`build_cds_argv`'s own
    internal literal, which ignores `parameters.ambient_bounces` entirely). All three record
    `ambient_bounces` at the value their argv carries, via `vd_parameters`/`dd_parameters`/
    `cds_parameters` below.

    The recorded dict is not decoration: `rad_core.manifest.compute_digest` hashes it, and
    `_parameters_match` compares it against what *this* run is asking for before offering the
    artifact as a cache hit. Recording a *requested* `-ab 4` on a matrix whose argv said `-ab 0`
    therefore states something false about the artifact **and** forces a rebuild whenever the caller
    moves a flag that provably cannot move the bytes. The direction is conservative -- a needless
    rebuild, never a false reuse -- which is why this was a Minor carried from Task 10's review
    rather than a defect, and it is fixed here because Task 13 is the task that seals these kinds.

    **The 3-phase `dmx` step carried the identical slip, and the daylight-matrix fix closed it from
    the other end.** It records `parameters_dict(parameters)` -- the caller's requested `-ab` -- while
    `build_d_argv` fixed its own `-ab 0`, so the recorded set stated something the argv did not
    carry. Task 13 left it alone deliberately: `dmx` had shipped since Phase 6b, its `parameters`
    dict is inside `compute_digest`, and moving it would invalidate every existing project's
    D-matrix. **That reasoning was sound and its premise was the defect.** The mismatch was not a
    recording slip to be papered over; it was the *record* telling the truth about what the caller
    asked for while the *argv* quietly did something else -- and what it quietly did was drop every
    ground bounce out of the daylight matrix. Making the argv honour `parameters` makes the existing
    record correct without touching it.

    The migration Task 13 declined still happens, in the direction that matters: `D`'s **bytes** move
    for every project, because the matrix is now built with the bounces it always should have had.
    Stored `dmx` artifacts are stale in the only sense that counts and must be rebuilt; a digest that
    did not move would have been the worse outcome, since it would have offered the under-built
    matrix as a cache hit forever.

    ## `Cds`, Task 10's own addition

    **`Cds` reads `request.suns_octree_path`, not `black_octree_path`.** `Vd`/`Dd` trace the all-black
    interior against the standard Klems fenestration; `Cds` traces the all-suns octree
    (`ChainRequest.suns_octree_path`'s own docstring, Task 8's `build_suns_octree`) whose fenestration
    is the high-resolution tensor-tree BSDF proxy, not the Klems glazing `Vd`/`Dd` see -- two
    genuinely different scenes, so this is a second octree field rather than a second use of
    `black_octree_path`.

    **`Cds`'s own `rcontrib` half takes sensor rays on stdin, like `Vd`'s, unlike `Dd`'s.** `build_cds_argv`
    is `-I` over real sensor points (`docs/specs/five-phase-method.md` 4.3's own `< data/
    photocells.pts`), not a `genklemsamp`-piped sample the way the Klems-basis `D`/`Dd` are -- so
    `Cds` needs no second `genklemsamp` spawn and no job-local rays file of its own, and reuses
    `points_path` directly (R43: `rcontrib -I` takes sensor rays on stdin only, `build_v_argv`'s own
    convention).

    **`Cds`'s edges add the direct-sun BSDF binding, on top of `Vd`/`Dd`'s own four-plus-group set.**
    The suns octree's own tensor-tree BSDF proxy is baked from the file `window_group(action=
    'assign_bsdf', role='direct_sun')` registers (Task 10a) as `bsdf_<group>_<state>_direct_sun` --
    `rad_mcp.window_group_tool._assign_bsdf`'s own naming, reproduced here as a literal format rather
    than imported, because this module may import nothing from `rad_mcp` (module docstring's layer
    rule). `REQUIRED_EDGES["cdsmx"]` (Task 13) now requires an input of kind `bsdf` among these
    edges, so a `Cds` sealed with no BSDF edge at all is refused rather than reported valid and
    reused across a glazing swap. That row is only at *kind* strength -- `InputRecord.role` is
    materials-only, so nothing in the edge table can tell this tensor tree from the Klems file bound
    at `role="matrix"`. What tells them apart, and what re-reads the tensor tree's bytes, is
    `rad_mcp.annual_tool`'s own pre-flight (`_refuse_drifted_direct_sun_bsdf`), which is in the
    layer that may read `registered_by.arguments["role"]` and digest a file.

    Raises `ValueError` if `request.target`/`black_octree_id`/`black_octree_path`/`suns_octree_id`/
    `suns_octree_path` are still `None` -- see `ChainRequest.target`'s own docstring for why they are
    fields on the request rather than extra positional arguments.

    **In production that guard should now never fire, and that is the point of Phase 7a Task 14b.**
    `rad_mcp.annual_tool._dispatch` is the only production caller of this function, and it reaches
    this call having already resolved `target` (`three_phase_target`, above its own `dry_run` branch)
    and compiled both octrees (`build_black_octree`/`build_suns_octree`, R44(3), also above it) --
    all five fields are populated on the one `ChainRequest` it constructs, or none of them is,
    because the same branch decides all five. The guard is kept as the contract for the *other*
    callers this signature invites -- `tests/test_five_phase.py`/`tests/test_chain.py` compose their
    own requests with `dataclasses.replace` -- and as the loud failure a future dispatch that
    populated four of five would get, instead of an `AttributeError` on a `None` several steps deeper.
    A `ValueError` rather than a coded refusal for that reason: reaching it from the wire would mean
    this module's own caller has drifted, which is not something a client can act on.
    """
    # **Named one field at a time, and that is R74's class rather than a style preference.** One
    # message covering five fields tells a caller *which five could be wrong*, not which one is --
    # this branch was bitten by exactly that three times (rulings R60/R63/R66/R67 are one defect four
    # times over). The tuple is the field name beside its own value, so a name that drifts from what
    # it reads is a `getattr` away rather than a string comparison nothing runs.
    for field_name, description in _FIVE_PHASE_REQUIRED_FIELDS:
        if getattr(request, field_name) is None:
            raise ValueError(
                f"five_phase_steps requires request.{field_name} ({description}) and it is None. "
                "None of two_phase_steps, three_phase_steps or _chain_request populate the five "
                "fields this chain needs (target, black_octree_id, black_octree_path, "
                "suns_octree_id, suns_octree_path) -- a caller must attach them all first, e.g. "
                "dataclasses.replace(request, target=..., black_octree_id=..., "
                "black_octree_path=..., suns_octree_id=..., suns_octree_path=...)."
            )

    target = request.target
    grid = request.grid
    weather_input_id = request.weather_input_id
    sky_subdivision = request.mf

    matrices = request.matrices_dir
    vd_path = matrices / f"vdmx_{target.group.name}_{grid}.mtx"
    dd_path = matrices / f"ddmx_{target.group.name}.mtx"
    cds_path = matrices / f"cdsmx_{target.group.name}_{grid}.mtx"
    sds_path = matrices / f"smxd_{weather_input_id}.mtx"
    direct_rays_path = request.job_dir / "klems_rays_direct.txt"

    # Job-local intermediates the combination pipeline reads and writes -- never sealed as
    # artifacts, for the identical reason the `genklemsamp` rays files never are (`three_phase_
    # steps`' own docstring): each is consumed by the very next step in this same job and nothing
    # later could reuse it on its own.
    i3ph_raw_path = request.job_dir / "i_3ph_raw.mtx"
    i3ph_path = request.job_dir / "i_3ph.txt"
    ids3ph_raw_path = request.job_dir / "i_ds3ph_raw.mtx"
    ids3ph_path = request.job_dir / "i_ds3ph.txt"
    ids5ph_raw_path = request.job_dir / "i_ds5ph_raw.mtx"
    ids5ph_path = request.job_dir / "i_ds5ph.txt"
    rlam_path = request.job_dir / "rlam_combined.txt"
    lux_path = request.job_dir / "illuminance_lux.txt"
    # **`annual_<grid>_5ph.ill` under `ill5:<grid>`, and never the 2-/3-phase chain's own
    # `annual_<grid>.ill` under `ill:<grid>`** -- the whole-branch review's Critical, part 3.
    #
    # Both older chains seal that id at that path (`two_phase_steps`, `three_phase_steps`). This one
    # used them too, and `reuse_decision` cannot save the older result from it: the `ill` artifact's
    # parameters record `"method"`, so a 5-phase run never reuses a 3-phase `ill` and always
    # *rebuilds over it*. The consequence was measured, not reasoned about: a project where
    # `results(action="summary")` answered before the call could not answer after it, because the
    # bytes at that path are now a **headered, sensor-major, scalar-lux** matrix
    # (`build_rcollate_final_argv`'s own docstring carries the measurement). Phase 7b Task 1
    # (ruling R98) aligned that shape with the 2-/3-phase chains' own -- sensors as rows, hours as
    # columns, a `#?RADIANCE` block declaring it -- so only the quantity (`NCOMP=1` scalar lux
    # instead of RGB) still sets a five-phase `.ill` apart. `rad_engine.results` does not read it
    # yet; that is a later Phase 7b task's own work, not this shape decision's.
    #
    # Two options were on the table and this is the one taken: a separate id and path, so the
    # 5-phase result destroys nothing and the 3-phase one it would have overwritten stays readable
    # with its provenance intact. The alternative -- keeping one id and emitting an advisory saying
    # the readable result had just been replaced -- reports the data loss instead of preventing it.
    # `rad_mcp.annual_tool` emitted `FIVE_PHASE_RESULT_NOT_YET_READABLE` on every 5-phase submit from
    # that review through Phase 7b Task 3, because a caller who could not tell that `results(grid=
    # ...)` was answering from an *older* 3-phase matrix would be the silent wrong answer this split
    # exists to avoid. Task 4 retired both the emit site and the constant once `rad_engine.results.
    # iter_annual_lux_rows` could read the file `ill5:<grid>` names, so the advisory no longer fires
    # -- there is no longer a stale answer left for it to be warning about.
    ill_path = request.results_dir / f"annual_{grid}_5ph.ill"

    points_path = request.points_path
    # This group's own sampling geometry, by name (`_sample_rad_for`) -- one group here, since this
    # chain is single-group by construction, but read through the same accessor the 3-phase chain
    # reads it through so there is one lookup rather than two (R30).
    sample_dir, sample_name = _genklemsamp_operand(_sample_rad_for(request, target.group))

    # `Vd`'s and `Dd`'s shared edge set: the same shape `three_phase_steps` gives `vmx`/`dmx`
    # (`_dc_edges`'s geometry/materials/sensor-grid/octree four, plus the window group), with the
    # black octree standing in for the interior/exterior one both terms are traced against here.
    direct_edges = (*_dc_edges(request, request.black_octree_id), target.group.input_id)

    # `Cds`'s own edge set: the same four-plus-group shape, but keyed on the *suns* octree -- a
    # different scene from `Vd`/`Dd`'s all-black one -- and with the direct-sun BSDF binding
    # appended, since that file is what the suns octree's own tensor-tree proxy is baked from. See
    # this function's own docstring for why the id is a reproduced literal rather than an import.
    direct_sun_input_id = f"bsdf_{target.group.name}_{target.state}_direct_sun"
    suns_edges = (
        *_dc_edges(request, request.suns_octree_id), target.group.input_id, direct_sun_input_id,
    )

    # **The three direct terms' *effective* ambient parameters -- what their argv actually carries,
    # which is what each one records.** See this function's own docstring ("What each term records
    # as its parameters") for the convention and why it is the effective set rather than the
    # requested one.
    #
    # `Vd`: `request.parameters` with the bounce count forced to 1, because `build_v_argv` reads
    # `-ab` off `parameters` and five-phase-method.md 3.2 states `-ab 1` as the term's own
    # definition, not a quality choice. `Dd` and `Cds` do not need the override to *build* -- their
    # own argv builders fix `-ab 0`/`-ab 1` internally and ignore what `parameters` says -- but they
    # do need it to *record*, which is the whole of Task 13's carried-in fix.
    vd_parameters = replace(request.parameters, ambient_bounces=1)
    #: `build_d_argv`'s own fixed `-ab 0` (that function's docstring: *"`-ab 0` is fixed, not read
    #: from `parameters`"*), made explicit so the recorded dict is the argv's.
    dd_parameters = replace(request.parameters, ambient_bounces=0)
    #: `build_cds_argv`'s own fixed direct-calculation flags, *all* of them -- `-ab 1 ... -dc 1
    #: -dt 0 -dj 0 -st 1 -ss 0` (*"the term's own fixed definition ... never read from
    #: `parameters`"*).
    #:
    #: **`direct_threshold`/`direct_certainty` were added on the whole-branch review, and the gap
    #: was this function's own stated rule breaking on its last three flags**: *"`Artifact.
    #: parameters` is the flag set the step's argv actually carries, never the flag set the caller
    #: asked for."* Only `-ab` was fixed up, so `cdsmx` recorded `dt: None`/`dc: None` -- which
    #: `parameters_dict`'s own docstring defines as *"left at rtrace's binary default, unstated by
    #: this tool"* -- while the argv stated `-dt 0 -dc 1` explicitly. `vmx`/`vdmx` and `dmx`/`ddmx`
    #: are accurate as they stand: `build_v_argv` and `build_d_argv` emit no `-dt`/`-dc` at all, so
    #: their `None` is true. `-dj`, `-st` and `-ss` have no `QualityParameters` field to carry them;
    #: `dj` is overridden in the artifact dict below beside `lw`, and `-st`/`-ss` are recorded
    #: nowhere, which is stated here rather than left to be discovered.
    #:
    #: No stored matrix is invalidated in practice: `cdsmx` is new on this branch and has never
    #: shipped, so nothing on disk carries the old dict for `_parameters_match` to compare against.
    cds_parameters = replace(
        request.parameters, ambient_bounces=1, direct_threshold=0.0, direct_certainty=1.0
    )

    # --- leg 1: I_3ph = V*T*D*S -- steps 1-4 are `three_phase_steps`' own, reused rather than
    # re-derived (ruling R30, this function's own docstring). Its fifth step (the `dctimestep`
    # sealing `ill:<grid>` directly) is discarded: that meaning is wrong here, where V*T*D*S is one
    # term of three, not the published result -- steps 5-6 below rebuild the multiplication into a
    # job-local file and collate it into the `i_3ph` leg instead.
    #
    # **Selected by `artifact.kind` (and the rays step by its own `argv`), never by position.**
    # `three_phase_steps` returns a `list[Step]`, not a named tuple, and its own docstring makes no
    # promise about index order beyond the table it documents today. Unpacking
    # `v_step, v_rays_step, d_step, s_step, _ = three_phase_steps(...)` would bind these names to
    # whatever sits at each index -- if that function's own step order ever changed (even in a way
    # that left the *artifact-kind* order this module's own tests assert unchanged), `d_step` could
    # silently bind to the rays step instead of the real D matrix, and leg 1 would multiply
    # `V*T*rays*S` rather than `V*T*D*S` with no test here catching it, since the kinds-list
    # assertion only inspects artifact-producing steps. Reading the four Steps back out by the fact
    # that actually identifies each one -- its artifact kind, or (for the one step with none) its own
    # `genklemsamp` argv -- cannot silently point at the wrong Step even if the source order moves.
    #
    # **Wrapped in a one-group `ThreePhaseTarget` since Phase 7d-1 Task 7 widened that signature
    # (ruling R126).** `request.target` stays a `ThreePhaseGroup` -- R126 again -- because this
    # chain is single-group by construction: `annual_tool` narrows through `ThreePhaseTarget.single`
    # before it ever fills the field. `blind_control` is `"none"` because that is what this chain is:
    # it multiplies one T and has no schedule to choose between states with (`ThreePhaseGroup.state`
    # refuses a multi-state group for exactly that reason, and `_dispatch` has already forced it).
    # The value reaches only the fifth step's own id, and the fifth step is the one discarded here.
    reused_leg = three_phase_steps(
        request, ThreePhaseTarget(groups=(target,), blind_control="none")
    )
    v_step = next(s for s in reused_leg if s.artifact is not None and s.artifact.kind == "vmx")
    d_step = next(s for s in reused_leg if s.artifact is not None and s.artifact.kind == "dmx")
    s_step = next(s for s in reused_leg if s.artifact is not None and s.artifact.kind == "smx")
    v_rays_step = next(
        s for s in reused_leg if s.artifact is None and s.argv[:1] == ("genklemsamp",)
    )
    v_path = v_step.stdout_path
    d_path = d_step.stdout_path
    sky_path = s_step.stdout_path

    i3ph_dctimestep = Step(
        phase="multiplying V*T*D*S for I_3ph (i_3ph_matrix)",
        argv=tuple(build_three_phase_argv(v_path, target.tmx_path, d_path, sky_path)),
        cwd=request.root,
        stdout_path=i3ph_raw_path,
        stdin_path=None,  # dctimestep takes every operand as a file argument (R43, measured).
        artifact=None,
        estimate_s=60.0,
    )
    i3ph_rcollate = Step(
        phase="collating I_3ph sensor-major (i_3ph)",
        argv=tuple(build_rcollate_leg_argv()),
        cwd=request.root,
        stdout_path=i3ph_path,
        stdin_path=i3ph_raw_path,
        artifact=None,
        estimate_s=15.0,
    )

    # --- leg 2: I_ds3ph = Vd*T*Dd*Sds ---
    sds_spec = SkyMatrixSpec(
        weather_path=request.weather_path,
        sky_subdivision=sky_subdivision,
        ground_reflectance=request.ground_reflectance,
        north=request.north,
        mode="direct",
    )
    sds_step = Step(
        phase="building direct-sun sky matrix (Sds)",
        argv=tuple(build_gendaymtx_argv(sds_spec)),
        cwd=request.root,
        stdout_path=sds_path,
        stdin_path=None,  # DEVNULL: gendaymtx takes the .wea as a positional argument.
        artifact=ArtifactSpec(
            artifact_id=f"smxd:{weather_input_id}",
            kind="smxd",
            path=sds_path,
            # The same weather/ground/north edges `smx` itself carries (`_smx_edges`, reused rather
            # than re-derived) -- `Sds` is the identical weather file with `-d` added, invalidated by
            # exactly the same changes.
            built_from=_smx_edges(request),
            parameters={
                "mf": sky_subdivision,
                "north_east_of_north": request.north,
                "ground_reflectance": request.ground_reflectance.value,
                "mode": "direct",
            },
        ),
        estimate_s=45.0,
    )
    ids3ph_dctimestep = Step(
        phase="multiplying Vd*T*Dd*Sds for I_ds3ph (i_ds3ph_matrix)",
        # Same four-operand builder as leg 1's step 5 -- `Vd`/`Dd`/`Sds` is the identical shape as
        # `V`/`D`/`S`, not a different equation (this function's own docstring).
        argv=tuple(build_three_phase_argv(vd_path, target.tmx_path, dd_path, sds_path)),
        cwd=request.root,
        stdout_path=ids3ph_raw_path,
        stdin_path=None,
        artifact=None,
        estimate_s=60.0,
    )
    ids3ph_rcollate = Step(
        phase="collating I_ds3ph sensor-major (i_ds3ph)",
        argv=tuple(build_rcollate_leg_argv()),
        cwd=request.root,
        stdout_path=ids3ph_path,
        stdin_path=ids3ph_raw_path,
        artifact=None,
        estimate_s=15.0,
    )

    # --- leg 3: I_ds5ph = Cds*Ssun ---
    #
    # **Built by `_sun_disk_sky_step`, shared with `direct_sun_steps` (Phase 7c Task 10), rather
    # than spelled out here.** Both chains seal `smxsun:<weather>`, and two independently written
    # `SkyMatrixSpec`s under one artifact id is the drift ruling R30 names -- see that helper's own
    # docstring. `ssun_path` is read back off the step rather than re-derived beside it, so the
    # filename this leg multiplies and the filename that step writes are one expression.
    ssun_step = _sun_disk_sky_step(request)
    ssun_path = ssun_step.stdout_path
    ids5ph_dctimestep = Step(
        phase="multiplying Cds*Ssun for I_ds5ph (i_ds5ph_matrix)",
        # Two-operand form, matching `docs/specs/five-phase-method.md` 4.5's own `dctimestep
        # directsun.dsmx OakLand_direct_m6.smx` -- reused from the 2-phase chain rather than
        # re-derived (this function's own docstring).
        argv=tuple(build_dctimestep_argv(cds_path, ssun_path)),
        cwd=request.root,
        stdout_path=ids5ph_raw_path,
        stdin_path=None,
        artifact=None,
        estimate_s=30.0,
    )
    ids5ph_rcollate = Step(
        phase="collating I_ds5ph sensor-major (i_ds5ph)",
        argv=tuple(build_rcollate_leg_argv()),
        cwd=request.root,
        stdout_path=ids5ph_path,
        stdin_path=ids5ph_raw_path,
        artifact=None,
        estimate_s=15.0,
    )

    # --- the combination: rlam, then rcalc, then the final rcollate -- three spawns, never a shell
    # pipe (this function's own docstring) ---
    rlam_step = Step(
        phase="pasting the three terms together (rlam)",
        # Order is the arithmetic: (i_3ph, i_ds3ph, i_ds5ph) is what makes rcalc's $1-3/$4-6/$7-9
        # address the term each field was written for (`build_rlam_argv`'s own docstring).
        argv=tuple(build_rlam_argv((i3ph_path, ids3ph_path, ids5ph_path))),
        cwd=request.root,
        stdout_path=rlam_path,
        stdin_path=None,  # rlam takes every operand as a file argument, like dctimestep's own.
        artifact=None,
        estimate_s=15.0,
    )
    combine_step = Step(
        phase="combining V*T*D*S - Vd*T*Dd*Sds + Cds*Ssun into illuminance (combine)",
        argv=tuple(build_five_phase_combine_argv()),
        cwd=request.root,
        stdout_path=lux_path,
        stdin_path=rlam_path,
        artifact=None,
        estimate_s=15.0,
    )
    final_step = Step(
        phase="reshaping the annual result into sensors x hours (ill)",
        argv=tuple(build_rcollate_final_argv(request.n_hours)),
        cwd=request.root,
        stdout_path=ill_path,
        stdin_path=lux_path,
        artifact=ArtifactSpec(
            # `ill5:`, not `ill:` -- see `ill_path`'s own comment above for the collision this
            # closes. `kind` stays `"ill"`: it is still an annual illuminance matrix, and the
            # invalidation table keys on kind. What differs is which id it occupies.
            artifact_id=f"ill5:{grid}",
            kind="ill",
            # All nine upstream matrices plus T -- see this function's own docstring for why T is
            # named once here rather than through a `tmx`-kind step.
            #
            # **Hand-typed literals, not read back off the eight `Step`s' own `artifact_id`s --
            # recorded rather than fixed.** Each id here reproduces the exact f-string its own
            # producing `Step` above already built (e.g. `vdmx_step.artifact.artifact_id`), so a
            # typo here is a second, independent statement of a string this function already knows
            # correctly in eight other places. Left as-is rather than reading each id back off its
            # own step (`vd_step.artifact.artifact_id`, etc.) because a mismatch is not silent: a
            # `built_from` id that names an artifact that does not exist fails loudly at seal time
            # (`register_artifact`/`resolve_built_from_digests`, `rad_core.manifest`), not the class
            # of defect this branch treats as worth a structural fix on its own -- unlike, say,
            # `sun_count`/`reinhart_bins` disagreeing silently inside an argv.
            path=ill_path,
            built_from=(
                f"vmx:{target.group.name}:{grid}",
                target.tmx_id,
                f"dmx:{target.group.name}",
                f"smx:{weather_input_id}",
                f"vdmx:{target.group.name}:{grid}",
                f"ddmx:{target.group.name}",
                f"smxd:{weather_input_id}",
                f"cdsmx:{target.group.name}:{grid}",
                f"smxsun:{weather_input_id}",
            ),
            parameters={
                "mf": sky_subdivision,
                "n_points": request.n_sensors,
                "method": "5phase",
                "window_group": target.group.name,
                "state": target.state,
                "n_hours": request.n_hours,
                "five_phase_result_revision": _FIVE_PHASE_RESULT_REVISION,
            },
        ),
        estimate_s=30.0,
    )

    return [
        v_step,
        v_rays_step,
        d_step,
        s_step,
        i3ph_dctimestep,
        i3ph_rcollate,
        Step(
            phase="building direct view matrix (Vd)",
            argv=tuple(build_v_argv(
                octree=request.black_octree_path,
                points=points_path,
                aperture=target.group.aperture,
                glow_modifier=GLOW_MODIFIER.format(group=target.group.name),
                parameters=vd_parameters,
                limit_weight=matrix_limit_weight(vd_parameters),
                # Required, not cosmetic -- see this function's own docstring: a headerless matrix
                # `dctimestep` reads from a file exits 1 with `fatal - unexpected EOF in header`.
                header=True,
                processes=request.processes,  # R781
            )),
            cwd=request.root,
            stdout_path=vd_path,
            # R43 again: `rcontrib -I+` takes its sensor rays on stdin, exactly as the 3-phase V
            # step's does.
            stdin_path=points_path,
            artifact=ArtifactSpec(
                artifact_id=f"vdmx:{target.group.name}:{grid}",
                kind="vdmx",
                path=vd_path,
                built_from=direct_edges,
                parameters={
                    **parameters_dict(vd_parameters),
                    "lw": matrix_limit_weight(vd_parameters),
                    "klems_bins": target.klems_bins,
                    "n_sensors": request.n_sensors,
                    **_processes_parameter(request),  # R781
                },
            ),
            estimate_s=180.0,
        ),
        Step(
            phase="sampling Klems directions for the direct pair (Dd)",
            argv=tuple(build_genklemsamp_argv(
                aperture=target.group.aperture,
                count=_KLEMS_SAMPLE_COUNT,
                window_rad=Path(sample_name),
            )),
            cwd=sample_dir,  # R754 final review: the bare name, in its own directory
            scene_inputs=(sample_name,),  # R755: scanned by the worker before the spawn
            # The pipe's first half, into its own job-local file -- see this function's own
            # docstring for why it is not `D`'s rays file even though the bytes would be identical.
            stdout_path=direct_rays_path,
            stdin_path=None,  # DEVNULL: genklemsamp reads the .rad as a positional argument.
            # No artifact, for the identical reason the 3-phase chain's own genklemsamp step has
            # none: these rays are a job-local intermediate of the pipe immediately after it, not
            # something a later run could reuse on its own.
            artifact=None,
            estimate_s=20.0,
        ),
        Step(
            phase="building direct daylight matrix (Dd)",
            argv=tuple(build_d_argv(
                octree=request.black_octree_path,
                aperture=target.group.aperture,
                mf=request.mf,
                # `dd_parameters`, not `request.parameters`: `-ab 0` is this term's own definition
                # (five-phase-method.md 3.3) and is stated here, at the call site, exactly as `Vd`
                # states its own `-ab 1`. `build_d_argv` used to hardcode the zero for every caller,
                # which silently gave the *three-phase* `D` the direct matrix's bounce count -- see
                # that function's own docstring for the measurement.
                parameters=dd_parameters,
                count=_KLEMS_SAMPLE_COUNT,
                sky_modifier=SKY_MODIFIER,
                # Same measurement as Vd's -- dctimestep reads D from a file.
                header=True,
                processes=request.processes,  # R781
            )),
            cwd=request.root,
            stdout_path=dd_path,
            # The pipe's second half: the job-local file the previous step just wrote.
            stdin_path=direct_rays_path,
            artifact=ArtifactSpec(
                artifact_id=f"ddmx:{target.group.name}",
                kind="ddmx",
                path=dd_path,
                built_from=direct_edges,
                parameters={
                    # The *effective* set (`dd_parameters`), not `request.parameters`: this step's
                    # `-ab` is the `0` the call site above pins, whatever the caller's quality asked
                    # for. Recorded from the same object the argv was built from, so the two cannot
                    # drift.
                    **parameters_dict(dd_parameters),
                    "mf": request.mf,
                    "count": _KLEMS_SAMPLE_COUNT,
                    "sky_modifier": SKY_MODIFIER,
                    **_processes_parameter(request),  # R781
                },
            ),
            estimate_s=120.0,
        ),
        sds_step,
        ids3ph_dctimestep,
        ids3ph_rcollate,
        Step(
            phase="building direct-sun coefficient matrix (Cds)",
            argv=tuple(build_cds_argv(
                octree=request.suns_octree_path,
                points=points_path,
                mf=request.mf,
                parameters=request.parameters,
                limit_weight=matrix_limit_weight(request.parameters),
                suns_modifier=SUNS_MODIFIER,
                # build_cds_argv's own default -- stated explicitly anyway, matching Vd/Dd's own
                # explicit header=True: a matrix written to a file dctimestep will read.
                header=True,
                processes=request.processes,  # R781
            )),
            cwd=request.root,
            stdout_path=cds_path,
            # R43 again: rcontrib -I takes its sensor rays on stdin, exactly as Vd's -I+ step does
            # -- and unlike Dd, Cds needs no genklemsamp pipe of its own (this function's own
            # docstring).
            stdin_path=points_path,
            artifact=ArtifactSpec(
                artifact_id=f"cdsmx:{target.group.name}:{grid}",
                kind="cdsmx",
                path=cds_path,
                built_from=suns_edges,
                parameters={
                    # The *effective* set (`cds_parameters`), not `request.parameters`: this step's
                    # `-ab` is `build_cds_argv`'s fixed `1`, the term's own definition.
                    **parameters_dict(cds_parameters),
                    "lw": matrix_limit_weight(request.parameters),
                    # `-dj 0` is in the argv and has no `QualityParameters` field, so it is stated
                    # here the same way `lw` is. `-st 1`/`-ss 0` are in the argv too and are
                    # recorded nowhere -- see `cds_parameters` above.
                    "dj": 0.0,
                    "mf": request.mf,
                    "n_sensors": request.n_sensors,
                    "suns_modifier": SUNS_MODIFIER,
                    **_processes_parameter(request),  # R781
                },
            ),
            estimate_s=300.0,
        ),
        ssun_step,
        ids5ph_dctimestep,
        ids5ph_rcollate,
        rlam_step,
        combine_step,
        final_step,
    ]


# --- LM-83's ASE direct-sun leg (Phase 7c Task 10) -----------------------------------------------


#: The chains `direct_sun_steps` is planned beside, as `rad_core.method` names them.
_DIRECT_SUN_CHAINS: Final[frozenset[str]] = frozenset({"2phase", "3phase", "5phase"})


def direct_sun_steps(
    request: ChainRequest, target: ThreePhaseTarget | None, *, chain: str
) -> list[Step]:
    """The six `Step`s that turn a pre-blind, zero-bounce direct-sun run into `illdir:<grid>` -- the
    annual **direct-beam-only** illuminance LM-83 sec 3.3 defines annual sunlight exposure over.

    ======  ===========================================================  ==============  ===========
    Step    `phase`                                                      Produces        argv from
    ======  ===========================================================  ==============  ===========
    1       building the ASE direct-sun coefficient matrix (asemx)       `asemx:<grid>`  `build_ase_direct_argv`
    2       building the sun-disk sky matrix (Ssun)                      `smxsun:<epw>`  `_sun_disk_sky_step`
    3       multiplying asemx*Ssun for the direct-sun result             --              `build_dctimestep_argv`
    4       collating the direct-sun result sensor-major (i_dir)         --              `build_rcollate_leg_argv`
    5       converting the direct-sun result to lux (illuminance_direct) --              `build_direct_lux_argv`
    6       reshaping the direct-sun result into sensors x hours (illdir) `illdir:<grid>` `build_rcollate_final_argv`
    ======  ===========================================================  ==============  ===========

    ## Why the whole-grid run stays when the leg fans out per group (ruling R146)

    Under `blind_control="lm83"` on the three-phase chain (`chain`; Phase 25 final review) this
    leg runs again, once per window group, over an octree in which
    every **other** group's aperture is blacked (`direct_sun_groups`, `rad_engine.scene.
    build_ase_scene`). Those per-group runs answer sec 2.2.6's residual and they do **not** replace
    the run below: sec 3.3's ASE is the pre-blind *whole-building* quantity, every aperture open, and
    the sum of the per-group runs is **neither an upper nor a lower bound** on it. Measured by the
    Task 8 reviewer on a two-group light-well with this leg's own flags
    (`rcontrib -I+ -ab 0 -dc 1 -dt 0 -dj 0 -st 1 -ss 0 -m solar`):

    - two apertures **in series**: whole-grid `5.281e-05`, **both** per-group runs `0.000e+00` --
      each blacks the other group's glazing and kills the path, so the sum **under**-counts;
    - an opening belonging to **no** window group: whole-grid `6.797e-05`, **each** per-group run
      `6.797e-05` -- nothing blacks it anywhere, so the sum **over**-counts it once per group.

    So `illdir:<grid>` is computed, not derived, and `rad_mcp.results_tool` keeps tallying ASE from
    it. The cost is stated where it is paid: see `direct_sun_groups` and
    `rad_mcp.annual_tool._build_per_group_ase_octrees`.

    ## The ASE_net leg (ruling R869)

    When `request.ase_net_groups` names window groups marked automated, one more whole-grid leg is
    planned on **any** chain: five steps, `asemxnet:<grid>` to `illdirnet:<grid>`, traced against
    `request.ase_net_octree_id` -- the scene with those groups' apertures blacked
    (`rad_engine.scene.build_ase_net_scene`) -- and sharing this leg's sun matrix. It is appended
    **after** every step above, so the steps an unmarked project plans keep their positions and
    their bytes; with no group marked nothing is appended. Its `asemx` probe opens every group's
    aperture as the whole-grid probe does (or strips every glazing surface on a two-phase run) and
    closes the automated groups' (`AseOcclusionProbe.closed`), so an all-zero ASE_net matrix seals
    only where no sun reaches the grid through the openings that remain. Refused (`ValueError`)
    when the net octree's id or path is missing or `ase_net_apertures` does not carry one aperture
    per group.

    ## Why ASE is its own run and not a column of an existing one

    LM-83 sec 3.3 is the direct beam from the solar orb alone -- sky excluded, interreflections
    excluded -- so it is *not* read off a total-illuminance annual result
    (`skills/radiance-simulation-workflow` ch11). It is also evaluated **pre-blind**: ASE is the
    quantity that justifies the shading device, so a run with the device deployed cannot measure it.
    Both exclusions are properties of the octree and of `-ab 0`, neither is a caller's setting, and
    `build_ase_direct_argv` holds both as literals for the reason R92 taught -- a builder that
    fixes a flag fixes it for every caller.

    ## The four-stage shape, which is the whole of why `illdir` is readable

    Steps 3-6 are the four stages ruling R98 rewrote the five-phase result into: `dctimestep`, the
    leg `rcollate -ho -oc 1`, `rcalc`'s lux form, then `rcollate -hi -fa1 -ic n_hours`. That is not
    a stylistic match -- it is what lets `rad_engine.results.iter_annual_lux_rows` read this file
    with **no new branch**: sensors as rows, hours as columns, a `#?RADIANCE` block declaring the
    shape, one scalar photopic lux per cell.

    **The `rcalc` stage is the one a reader cannot check for you.** Drop it and the pipeline still
    produces a file with the right row and column counts and a valid header -- it is irradiance
    wearing a lux reader's shape, wrong by ~179, and ASE counts hours over a hard 1000 lux
    threshold. `build_direct_lux_argv` is the single-term form of the five-phase combination, and
    **never `rmtxop -c 47.4 119.9 11.6`**, whose rounded digits disagree with
    `rad_core.photometry.to_lux`'s in the fourth significant figure before anyone has edited either.

    ## `illdir:<grid>`, and never `ill:` or `ill5:`

    Those two are the annual **results** a caller reads; this is one **term** of one. The Phase 7a
    whole-branch review's Critical was exactly this collision one axis over: the five-phase chain
    sealed `ill:<grid>` over the three-phase result, and because the `ill` artifact's parameters
    record `"method"`, `reuse_decision` could not save the older matrix -- it always rebuilt over
    it, and a project that could answer `results(action="summary")` before the call could not after
    it. A direct-sun run writing to either id or either *path* would do the same. Both are asserted:
    `tests/test_chain.py::TestTheDirectSunLeg` pins the ids and the paths separately, because two
    distinct ids pointing at one path lose the data just as thoroughly and record the loss under a
    different name.

    `kind` is `"ill"`, not a new one: it is still an annual illuminance matrix and the invalidation
    table keys on kind. What differs is which id it occupies -- the identical posture `ill5:` holds.
    Only `asemx` is a genuinely new kind.

    ## What each artifact records as its parameters

    **The effective flag set, never the requested one** -- the slip Phase 7a Task 13 fixed for
    `Vd`/`Dd`/`Cds`. `build_ase_direct_argv` states `-ab 0 -dc 1 -dt 0 -dj 0 -st 1 -ss 0` as
    literals whatever the caller's `quality` says, so `ase_parameters` below is `request.parameters`
    with those overridden, and it is the object *both* the argv and the record are built from, so
    the two cannot drift. `-dj`, `-st` and `-ss` have no `QualityParameters` field; `dj` is stated
    in the artifact dict beside `lw`, and `-st`/`-ss` are recorded nowhere, which is said here
    rather than left to be discovered.

    `illdir` records `ase_direct_revision`, R98's device: the four stages above are argv, `argv` is
    not a digest input, and `is_reusable` re-digests the file already on disk -- so a correction to
    this pipeline moves nothing a stored artifact could be told apart by. A key absent from every
    pre-correction record is the only separator. See `_ASE_DIRECT_REVISION`.

    ## The sun matrix is planned here, and that is a correction to the phase plan

    The plan said this leg merely consumes an `smxsun:<weather>` *"already sealed by
    `five_phase_steps`"*. False for the path the phase needs: Task 11 plans this leg on **three-
    phase** runs too, which never call `five_phase_steps`. Step 2 is therefore
    `_sun_disk_sky_step(request)` -- the same helper `five_phase_steps` now calls, so the two cannot
    build different matrices under one id -- and eliding an already-current one is left to
    `reuse_decision`, the shipped mechanism, rather than to a second one invented here. A caller
    that concatenates this list with `five_phase_steps`' must drop the duplicate step; see that
    helper's docstring.

    ## Edges

    `asemx` carries `_dc_edges`' four and only those: every geometry input, every materials input,
    the sensor grid, and the ASE octree. `REQUIRED_EDGES["asemx"]` enforces all four kinds, and
    `check_edges` runs **before** the spawn (the per-step call in `JobStore._run`;
    the submit-time call is in `JobStore.submit`), so an incomplete set costs a
    validation error rather than an `rcontrib`. The materials edge is the load-bearing one that
    `cdsmx`'s row does not have: this matrix holds no BSDF at all, the direct beam crosses the
    aperture's own glass primitive, and LM-83 measures the beam *after* glazing transmittance --
    which is declared in the registered materials and nowhere else.

    **No window group, and that changed at Phase 7c Task 11.** Task 10 named every registered group
    here because `REQUIRED_EDGES["asemx"]` demanded one; the row was wrong and the edge went with
    it. Nothing in this leg reads a group: `build_ase_octree` is `oconv -f <scene_files...>
    <suns_scene>` with no aperture strip and no BSDF proxy, and `build_ase_direct_argv` bins by
    **sun** (`-m solar`), never by aperture. A group also owns no bytes of its own -- it is
    registered with the *exact files* every geometry input already carries -- so its digest moves if
    and only if geometry moves, which the geometry edges already carry, while *redefining* a group's
    aperture list leaves the files untouched and the digest identical. Zero invalidation power, at
    the cost of a hard refusal on any project that legitimately has no group. That is R44(2)'s
    situation one kind over: the 2-phase DC matrix commits as `dsmx` rather than `vmx` precisely
    because `REQUIRED_EDGES["vmx"]` demands a window group a 2-phase run cannot acquire.

    `illdir` names its two operands, `asemx:<grid>` and `smxsun:<weather>`, exactly as `ill5:` names
    its nine: the digest graph carries geometry, materials and weather down through them, and an
    edge set that restated them would be a second, independently maintained statement of the same
    dependency. `REQUIRED_EDGES` has no `ill` row, so `check_edges` returns after proving each named
    edge resolves -- and returning early is not permission to under-record, which is why both
    operands are named rather than only the matrix.

    Raises `ValueError` if `request.ase_octree_id`/`ase_octree_path` are still `None`, one field at
    a time (R74): a caller that populated one of two learns which, rather than reading through a
    `None` into an `AttributeError` several steps deeper.
    """
    # Final review A1: `chain` names the chain this leg is planned beside. It is carried on every
    # `asemx` spec (`ArtifactSpec.ase_chain`) because what a refused matrix leaves readable differs
    # by chain, and it must agree with `target`: a two-phase run resolves no window group.
    if chain not in _DIRECT_SUN_CHAINS:
        raise ValueError(
            f"direct_sun_steps was given chain={chain!r}; it is one of "
            f"{sorted(_DIRECT_SUN_CHAINS)}, the chain this leg is planned beside."
        )
    if (target is None) != (chain == "2phase"):
        raise ValueError(
            f"direct_sun_steps was given chain={chain!r} with "
            f"{'no' if target is None else 'a'} window-group target: a two-phase run resolves no "
            "window group and every three- or five-phase run resolves one."
        )
    for field_name, description in _DIRECT_SUN_REQUIRED_FIELDS:
        if getattr(request, field_name) is None:
            raise ValueError(
                f"direct_sun_steps requires request.{field_name} ({description}) and it is None. "
                "Neither two_phase_steps, three_phase_steps, five_phase_steps nor _chain_request "
                "populates the ASE octree fields this leg needs (ase_octree_id, ase_octree_path) "
                "-- a caller must attach both first, e.g. dataclasses.replace(request, "
                "ase_octree_id=..., ase_octree_path=...). It is build_ase_octree's output, not "
                "build_suns_octree's: see ChainRequest.ase_octree_path for why substituting the "
                "one for the other runs cleanly and answers wrongly."
            )
    if request.ase_net_groups:
        # R869: a net leg names its own octree; tracing the whole-grid one under 'asemxnet:'
        # would publish ASE_gross as ASE_net.
        for field_name in ("ase_net_octree_id", "ase_net_octree_path"):
            if getattr(request, field_name) is None:
                raise ValueError(
                    f"direct_sun_steps was given automated window groups "
                    f"{list(request.ase_net_groups)} (request.ase_net_groups) and "
                    f"request.{field_name} is None. The ASE_net leg is traced against its own "
                    "octree, the scene with those groups' apertures blacked "
                    "(rad_engine.scene.build_ase_net_scene); attach ase_net_octree_id and "
                    "ase_net_octree_path, or no automated groups."
                )
        if len(request.ase_net_apertures) != len(request.ase_net_groups):
            raise ValueError(
                f"direct_sun_steps was given {len(request.ase_net_groups)} automated window "
                f"group(s) and {len(request.ase_net_apertures)} aperture(s) for them; "
                "request.ase_net_apertures carries one aperture per group, in ase_net_groups "
                "order, and is what the ASE_net matrix's occlusion probe closes."
            )

    # **S after the matrix and before the multiply**, which is where it has always sat (step 2 of
    # 6), and **built once for every group**: it carries the weather, not the aperture, and two
    # steps sealing one id at one path is a race whose loser is whichever half of the answer
    # finished second. `_sun_disk_sky_step` is the same helper `five_phase_steps` calls, so the two
    # chains cannot build different matrices under one id.
    ssun_step = _sun_disk_sky_step(request)
    whole_grid = _direct_sun_leg(
        request,
        octree_id=request.ase_octree_id,
        octree_path=request.ase_octree_path,
        sun_matrix_path=ssun_step.stdout_path,
        group=None,
        # R831: every window group's opening, since this run has every aperture open. `()` on a
        # two-phase run, whose whole-grid probe strips every glazing surface instead (R856).
        probe_groups=target.groups if target is not None else (),
        chain=chain,
    )
    steps = [whole_grid[0], ssun_step, *whole_grid[1:]]
    # Final review (controller ruling 2): the per-group legs are planned beside the three-phase
    # chain only. Their one reader is the schedule (`blind_schedule_steps`) and through it the
    # blend, both steps of the three-phase chain alone (`rad_mcp.annual_tool._plans_blend`); on a
    # five-phase lm83 run they were an `rcontrib` per group whose matrices nothing read (measured
    # through the real tool: results, compliance, blind_schedule and the job's export named none).
    for tp_group in direct_sun_groups(target) if chain == "3phase" else ():
        octree_id, octree_path = _ase_octree_for(request, tp_group.group.name)
        steps.extend(_direct_sun_leg(
            request,
            octree_id=octree_id,
            octree_path=octree_path,
            sun_matrix_path=ssun_step.stdout_path,
            group=tp_group,
            # R848: every group's opening is still opened, as the whole-grid probe -- one that left
            # the others in place read a beam through two groups in series as blocked. R858: the
            # probe now also closes this group (`_direct_sun_leg` passes it as the probe's `group`)
            # and refuses only where closing it dims some ray.
            probe_groups=target.groups if target is not None else (),
            chain=chain,
        ))
    # R869: the ASE_net leg, appended AFTER every step above so an unmarked plan (no
    # `ase_net_groups`) is the list it always was and a marked one only grows at its end.
    if request.ase_net_groups:
        steps.extend(_direct_sun_leg(
            request,
            octree_id=request.ase_net_octree_id,
            octree_path=request.ase_net_octree_path,
            sun_matrix_path=ssun_step.stdout_path,
            group=None,
            # Every group's opening opened, as the whole-grid probe opens them; the probe then
            # closes the automated groups' (`AseOcclusionProbe.closed`). A two-phase run's probe
            # strips every glazing surface instead, and still closes them.
            probe_groups=target.groups if target is not None else (),
            chain=chain,
            net_groups=request.ase_net_groups,
        ))
    return steps


def direct_sun_groups(target: ThreePhaseTarget | None) -> tuple[ThreePhaseGroup, ...]:
    """The window groups the direct-sun leg runs **once each** for -- one definition, two layers.

    `rad_engine.chain` plans one `asemx`/`illdir` pair per group in this tuple;
    `rad_mcp.annual_tool` compiles one blacked ASE octree per group in the same tuple. Two
    independently written conditions is how the chain comes to name an octree nothing built, or a
    submit comes to pay for an octree no step traces -- ruling R30's own situation, so the
    condition is written once and imported.

    **`lm83` and nothing else.** Only the LM-83 sec 2.2.6 schedule consumes a per-group direct-sun
    result (Task 11 seals `sched:<grid>` under that setting alone), and the per-group leg costs a
    second `rcontrib` per group -- the most expensive step in the leg. Under `"none"` and
    `"fixed"` the leg is the six steps it has always been.

    **At G = 1 -- every run this server submits today -- that second `rcontrib` traces a scene
    byte-identical to the whole-grid one**, because a lone group has no other aperture to black. The
    escape, and the three things that have to be built before it can be taken, are written out at
    `rad_mcp.annual_tool._build_per_group_ase_octrees`. It is an owner decision, not this function's.

    **Not `three_phase_steps`' own `fan_out`, which is `lm83` *or* more than one group.** That one
    fans out because a multi-group `.ill` has to be summed; this one has nothing to sum -- LM-83
    sec 3.3's ASE is the whole-building pre-blind quantity and `illdir:<grid>` already measures it
    with every aperture open. A multi-group run with no schedule needs no per-group direct term.

    `None` for a run that resolved no window group at all, which is every 2-phase run: this leg is
    planned whenever `"ase" in metrics`, and nothing in it needs an aperture.

    **`direct_sun_steps` plans these groups' legs beside the three-phase chain only** (Phase 25
    final review, controller ruling 2): their readers, `blind_schedule_steps` and `blend_steps`,
    are three-phase steps, and on a five-phase `lm83` run the legs were matrices nothing read.
    `rad_mcp.annual_tool` still compiles these groups' octrees on a five-phase run, because
    `BLIND_SCHEDULE_UNCLAIMED_APERTURE` is read off them at submit.
    """
    if target is None or target.blind_control != LM83_BLIND_CONTROL:
        return ()
    return target.groups


def direct_ill_id(grid: str, group_name: str | None) -> str:
    """`illdir:<grid>` for the whole-grid run, `illdir:<grid>:<g>` for one window group's.

    **One spelling, two readers, and the second reader is why this is a function** (Phase 7d-1
    Task 11). `_direct_sun_leg` mints the id and `blind_schedule_steps` names it as an edge; a
    second, hand-typed `f"illdir:{grid}:{name}"` in the planner is two statements of one name that
    agree until one of them moves -- and the failure is not a crash, it is `check_edges` refusing a
    schedule whose operand it cannot resolve, or worse, resolving one that is not the matrix the
    step actually wrote. The Task 11 brief itself got this name wrong (it asked for a kind called
    `illdir` and a file under `07_matrices/`), which is the evidence that it is worth deriving
    rather than restating.

    `kind` is **`"ill"`** for both, never a kind of its own -- see `ArtifactKind`'s own note: this
    is still an annual illuminance matrix and what differs is only which id it occupies.
    """
    return f"illdir:{grid}" if group_name is None else f"illdir:{grid}:{group_name}"


def direct_ill_path(results_dir: Path, grid: str, group_name: str | None) -> Path:
    """Where `direct_ill_id`'s matrix is written -- `08_results/annual_<grid>[_<g>]_direct.ill`.

    The file half of the same name, for `direct_ill_id`'s reason. Two distinct ids pointing at one
    path lose the data just as thoroughly as one id does and record the loss under a different name
    (`direct_sun_steps`' own "## `illdir:<grid>`, and never `ill:` or `ill5:`"), so the id and the
    path are derived from one `group_name` argument in one place.
    """
    slug = "" if group_name is None else f"_{group_name}"
    return results_dir / f"annual_{grid}{slug}_direct.ill"


def direct_ill_net_id(grid: str) -> str:
    """`illdirnet:<grid>`: the ASE_net direct-sun result, the whole grid traced with the window
    groups marked automated blacked (ruling R869). Derived here, beside `direct_ill_id`, for that
    function's reason: the chain mints it and a later reader names it."""
    return f"illdirnet:{grid}"


def direct_ill_net_path(results_dir: Path, grid: str) -> Path:
    """Where `direct_ill_net_id`'s matrix is written -- `08_results/annual_<grid>_direct_net.ill`.

    A name no window group can produce through `direct_ill_path`, whose per-group form always ends
    `_direct.ill`."""
    return results_dir / f"annual_{grid}_direct_net.ill"


def _ase_octree_for(request: ChainRequest, group_name: str) -> tuple[str, Path]:
    """The blacked ASE octree compiled for **this** window group: its artifact id and its path.

    **One lookup, by name, and it raises rather than falling back** -- `_sample_rad_for`'s reason,
    one leg over, and the same defect class Task 7's review found twice. Each group's octree is the
    registered scene with *every other group's aperture blacked*; running group B's `rcontrib`
    against group A's octree measures the beam through A's window and seals it under B's id. The
    matrix is the right shape, the column count is the same, `dctimestep` multiplies it happily,
    and the number is wrong for a reason nothing downstream can see.

    A missing key is a **caller** defect -- `rad_mcp.annual_tool` compiles these octrees at submit
    time and hands the ids and paths over on the `ChainRequest` -- so a group with no octree means
    the compiler and the chain disagree about which groups this job is for. Refused at plan time,
    before an `rcontrib` is spawned.
    """
    try:
        return request.ase_octree_ids[group_name], request.ase_octree_paths[group_name]
    except KeyError:
        raise ThreePhaseNotBuildableError(
            f"no per-group ASE octree was compiled for window group {group_name!r}: this job has "
            f"octrees for {sorted(request.ase_octree_ids) or 'no groups at all'}. Each group's "
            "direct-sun run is traced against a scene with every OTHER group's aperture blacked, "
            "so running this group against another group's octree would measure the beam through "
            "the wrong window and seal it under this group's id. The caller that compiles the "
            "blacked octrees and the chain that names them must cover the same groups."
        ) from None


def _direct_sun_leg(
    request: ChainRequest,
    *,
    octree_id: str,
    octree_path: Path,
    sun_matrix_path: Path,
    group: ThreePhaseGroup | None,
    probe_groups: tuple[ThreePhaseGroup, ...] = (),
    chain: str,
    net_groups: tuple[str, ...] = (),
) -> list[Step]:
    """The five steps of **one** direct-sun run: `asemx`, the multiply, the two reshapes, the lux
    conversion and `illdir`. The sun matrix is the caller's, because every run shares it.

    `group` is `None` for the whole-grid run -- every aperture open, which is the ASE term LM-83
    sec 3.3 defines (`illdir:<grid>`) -- and a `ThreePhaseGroup` for a per-group run, whose octree
    has every *other* group's aperture blacked and which answers sec 2.2.6's residual
    (`illdir:<grid>:<g>`).

    **Every per-group input is per group**, and that is the whole of what this function is careful
    about: the octree, the matrix file, all three job-local intermediates and the sealed result all
    carry the group's name. Task 7 shipped this defect three times in one task -- a shared
    `genklemsamp` sample file, a glow surface written once from one group, a modifier that happened
    to be per group -- and each was a valid file with a plausible, wrong number. Two groups sharing
    `i_direct_raw.mtx` is two steps writing one path, and the answer that survives is whichever
    finished second.

    **Moved here by Phase 7d-1 Task 8, not rewritten.** Every argv builder call, `stdout_path`,
    `stdin_path`, edge set, `estimate_s` and `parameters` entry below stood in `direct_sun_steps`
    and is carried over unchanged; what this task added is the `slug`/`scope` the per-group names
    are built with, the group's own octree in place of the request's single one, and the sun
    geometry the seal-time guard needs. A rewritten `-dj 0` or a dropped `-ab 0` would be a valid
    file with a wrong answer, so the bodies were moved rather than retyped.

    **`net_groups` (ruling R869)** makes this the ASE_net run: the whole grid (`group` is `None`)
    traced against the octree with those automated groups' apertures blacked. Its names are its
    own -- `asemxnet:<grid>` at `asemxnet_<grid>.mtx`, the job-local `i_directnet_raw.mtx`,
    `i_directnet.txt` and `illuminance_directnet_lux.txt` (a per-group slug always follows
    `i_direct`, `i_direct_raw` or `illuminance_direct_lux` with `_`, so no group name produces
    them), and `illdirnet:<grid>` at `direct_ill_net_path` -- and both artifacts record
    `"ase_net_blacked"`, the blacked groups, so `_parameters_match` cannot reuse a matrix blacked
    for another set. Its probe closes `request.ase_net_apertures` (`AseOcclusionProbe.closed`).
    """
    if net_groups and group is not None:
        raise ValueError(
            "_direct_sun_leg was given a window group and automated groups to black: the ASE_net "
            "run is a whole-grid run, and a per-group run blacks every other group already."
        )
    grid = request.grid
    matrices = request.matrices_dir
    #: `""` for the whole-grid run and `"_<group>"`/`":<group>"` for a per-group one -- the file
    #: and id halves of one name (R122: `illdir:` keeps `kind="ill"` and **extends** the existing
    #: name rather than inventing a second scheme). One variable each, so a per-group file and a
    #: per-group id cannot disagree about which group they are for.
    group_name = group.group.name if group is not None else None
    slug = "" if group_name is None else f"_{group_name}"
    scope = "" if group_name is None else f":{group_name}"
    label = "" if group_name is None else f" for {group_name}"
    #: Recorded on both artifacts of a per-group run, and on neither of the whole-grid run's --
    #: which is not about one group and must not claim to be. `_parameters_match` compares the
    #: whole dict, so this is also what keeps a group's matrix from being reused for another's.
    group_parameters: dict[str, Any] = {} if group_name is None else {"window_group": group_name}
    #: R869: the ASE_net run's own names, apart from the whole-grid run's and every group's (see
    #: this function's docstring), and its record of which groups it blacked.
    net = bool(net_groups)
    matrix_name = "asemxnet" if net else "asemx"
    scratch = "directnet" if net else "direct"
    if net:
        label = " with the automated groups blacked"
        group_parameters = {"ase_net_blacked": list(net_groups)}
    #: Ruling R144's pair, and **both or neither**: `register_artifact` raises `ValueError` on half
    #: of it, and an empty `ase_suns` is refused there too -- 0 suns in a hemisphere means "nothing
    #: was supplied", which is not the same fact as "no sun is in front of this aperture". The
    #: whole-grid run supplies neither because it has every aperture open and no single one to
    #: reason from; the guard then fails closed there exactly as it did before this pair existed.
    sun_geometry = bool(group is not None and request.ase_suns)
    ase_path = matrices / f"{matrix_name}_{grid}{slug}.mtx"
    #: Rulings R831/R848's occlusion probe: `probe_groups` are the window groups whose apertures it
    #: opens -- every group, for the whole-grid and every per-group run alike (R848). A run with no
    #: group to open (a two-phase run) gets ruling R856's probe on its whole-grid matrix instead,
    #: which strips every glazing surface of the scene (`strip_glazing`) and casts every dome
    #: direction. None without the dome. R858: a per-group run's probe also names this group
    #: (`group`), whose aperture `probe_ase_occlusion` closes for its second trace; the whole-grid
    #: and strip-mode probes name none.
    ase_probe: AseOcclusionProbe | None = None
    if request.ase_suns and (probe_groups or group is None):
        ase_probe = AseOcclusionProbe(
            scene_files=tuple(request.scene_files),
            modifiers=tuple(sorted({
                polygon.modifier for tp in probe_groups for polygon in tp.group.aperture.polygons
            })),
            apertures=tuple(tp.group.aperture for tp in probe_groups),
            suns=tuple(request.ase_suns),
            points_path=request.points_path,
            cwd=request.root,
            scratch_dir=request.job_dir,
            suns_scene=request.ase_suns_scene,
            scene_digest=request.ase_scene_digest,
            strip_glazing=not probe_groups,
            group=group.group.aperture if group is not None and probe_groups else None,
            # R869: the ASE_net probe closes the automated groups' apertures, every one of them,
            # and asks whether any sun still reaches the grid.
            closed=tuple(request.ase_net_apertures) if net else (),
        )

    # Job-local intermediates, never sealed, for the reason `five_phase_steps`' own scratch files
    # are not: each is consumed by the very next step of this same job and nothing later could
    # reuse it on its own. **Named apart from that chain's own scratch files** (`i_ds5ph_raw.mtx`,
    # `illuminance_lux.txt`) rather than reusing them -- a five-phase run whose metrics name `ase`
    # submits both legs into one `job_dir`, and two steps writing one path is a race whose loser is
    # whichever half of the answer finished second.
    raw_path = request.job_dir / f"i_{scratch}_raw{slug}.mtx"
    leg_path = request.job_dir / f"i_{scratch}{slug}.txt"
    lux_path = request.job_dir / f"illuminance_{scratch}_lux{slug}.txt"
    #: **Through `direct_ill_path`/`direct_ill_id`, not spelled here** (Task 11): the schedule step
    #: names this same matrix as an operand, and a second copy of either name in the planner is two
    #: statements of one fact that agree until one moves.
    ill_path = (
        direct_ill_net_path(request.results_dir, grid) if net
        else direct_ill_path(request.results_dir, grid, group_name)
    )

    # `_dc_edges`' four -- every geometry input, every materials input, the sensor grid, and the
    # ASE octree -- and **no window group**, which is a decision rather than an omission. See the
    # "## Edges" section of this function's docstring, and `REQUIRED_EDGES["asemx"]`'s own third
    # deliberate absence.
    ase_edges = _dc_edges(request, octree_id)

    #: `build_ase_direct_argv`'s own fixed direct-calculation flags, *all* of them -- `-ab 0 ...
    #: -dc 1 -dt 0 -dj 0 -st 1 -ss 0`. The `-ab` is the term's definition (LM-83 sec 3.3), not a
    #: quality choice, so a caller at the `high` preset still records `0`; `-dt`/`-dc` are
    #: overridden for the reason the whole-branch review added them to `cdsmx` -- left reading off
    #: `parameters` they record `None`, which `parameters_dict` defines as *"left at rtrace's binary
    #: default, unstated by this tool"*, while the argv states them explicitly.
    ase_parameters = replace(
        request.parameters, ambient_bounces=0, direct_threshold=0.0, direct_certainty=1.0
    )

    ase_step = Step(
        phase=f"building the ASE direct-sun coefficient matrix (asemx){label}",
        argv=tuple(build_ase_direct_argv(
            octree=octree_path,
            points=request.points_path,
            mf=request.mf,
            # `request.parameters`, not `ase_parameters`: the builder ignores what `parameters`
            # says about `-ab`/`-dt`/`-dc` entirely and reads only `-ad`/`-as`/`-ar`/`-aa` off it.
            # Handing it the overridden object would state the override twice and imply the builder
            # honours it, which is the misreading R92 was about.
            parameters=request.parameters,
            limit_weight=matrix_limit_weight(request.parameters),
            suns_modifier=SUNS_MODIFIER,
            # The builder's own default, stated anyway as `Vd`/`Dd`/`Cds` state theirs: this matrix
            # is written to a file `dctimestep` reads, and a headerless operand exits 1 with
            # `fatal - unexpected EOF in header`.
            header=True,
            processes=request.processes,  # R781
        )),
        cwd=request.root,
        stdout_path=ase_path,
        # R43: `rcontrib -I` takes its sensor rays on stdin only -- never as an argv token, which is
        # why `build_ase_direct_argv` carries `points` and does not emit it.
        stdin_path=request.points_path,
        artifact=ArtifactSpec(
            # The literal at the keyword, where `tests/test_advisories.py` reads the sealed prefixes.
            artifact_id=f"asemx:{grid}{scope}" if not net else f"asemxnet:{grid}",
            kind="asemx",
            path=ase_path,
            built_from=ase_edges,
            parameters={
                **parameters_dict(ase_parameters),
                "lw": matrix_limit_weight(request.parameters),
                # In the argv with no `QualityParameters` field to carry it, so stated here the same
                # way `lw` is. `-st 1`/`-ss 0` are in the argv too and are recorded nowhere.
                "dj": 0.0,
                "mf": request.mf,
                "n_sensors": request.n_sensors,
                "suns_modifier": SUNS_MODIFIER,
                **group_parameters,
                **_processes_parameter(request),  # R781
            },
            ase_aperture=group.group.aperture if sun_geometry else None,
            ase_suns=request.ase_suns if sun_geometry else None,
            ase_probe=ase_probe,
            ase_chain=chain,
        ),
        estimate_s=300.0,
    )

    multiply_step = Step(
        phase=f"multiplying asemx*Ssun for the direct-sun result (i_direct_matrix){label}",
        # Two-operand form, the same one leg 3 of the five-phase chain uses for `Cds*Ssun`. Order is
        # the arithmetic: `dctimestep` dots each row of the first operand against the second.
        argv=tuple(build_dctimestep_argv(ase_path, sun_matrix_path)),
        cwd=request.root,
        stdout_path=raw_path,
        stdin_path=None,  # dctimestep takes every operand as a file argument (R43, measured).
        artifact=None,
        estimate_s=30.0,
    )
    collate_step = Step(
        phase=f"collating the direct-sun result sensor-major (i_direct){label}",
        argv=tuple(build_rcollate_leg_argv()),
        cwd=request.root,
        stdout_path=leg_path,
        stdin_path=raw_path,
        artifact=None,
        estimate_s=15.0,
    )
    lux_step = Step(
        phase=f"converting the direct-sun result to illuminance (illuminance_direct){label}",
        argv=tuple(build_direct_lux_argv()),
        cwd=request.root,
        stdout_path=lux_path,
        stdin_path=leg_path,
        artifact=None,
        estimate_s=15.0,
    )
    final_step = Step(
        phase=f"reshaping the direct-sun result into sensors x hours (illdir){label}",
        argv=tuple(build_rcollate_final_argv(request.n_hours)),
        cwd=request.root,
        stdout_path=ill_path,
        stdin_path=lux_path,
        artifact=ArtifactSpec(
            artifact_id=direct_ill_net_id(grid) if net else direct_ill_id(grid, group_name),
            kind="ill",
            path=ill_path,
            built_from=(ase_step.artifact.artifact_id, f"smxsun:{request.weather_input_id}"),
            parameters={
                "mf": request.mf,
                "n_points": request.n_sensors,
                "n_hours": request.n_hours,
                # **What this matrix *is*, recorded rather than left to the id's spelling.** `kind`
                # is `"ill"` and `ill:`/`ill5:` record `"method"`; a reader that saw only the kind
                # and the parameters could not otherwise tell a direct-beam-only term from a total-
                # illuminance result, and mistaking one for the other is a factor-of-several answer
                # against a hard 1000 lux threshold.
                "term": "ase_direct_net" if net else "ase_direct",
                "ase_direct_revision": _ASE_DIRECT_REVISION,
                **group_parameters,
            },
        ),
        estimate_s=30.0,
    )

    return [ase_step, multiply_step, collate_step, lux_step, final_step]


#: The `sched:<grid>` schedule's own revision, `_ASE_DIRECT_REVISION`'s device one artifact over.
#:
#: The scan is `rad_core.blinds.group_schedule`'s and the order is `closing_order`'s; neither is an
#: argv token, `argv` is not a digest input, and `is_reusable` re-digests the file already on disk
#: -- so a correction to *how the schedule is derived* moves nothing a stored `sched:<grid>` could
#: be told apart by, and every project would keep reusing a schedule derived by the old rule. A key
#: absent from every pre-correction record is the only separator. Bump it when the scan, the
#: substituted ordering key or the window this is derived over changes.
#:
#: **2 since R861**: the scan's per-point in-sun test moved from `> 1 000 lux` to `>= 1 000 lux`
#: (LM-83-12 section 2.2.6, "1,000 lux or more"), so a schedule sealed at 1 is stale and is
#: re-derived on the next run. `illblend:<grid>` names `sched:<grid>` in `built_from`, so
#: `reuse_decision` rebuilds the blend whenever it rebuilds the schedule, and `_BLEND_REVISION` does
#: not move with it.
_BLIND_SCHEDULE_REVISION: Final[int] = 2

#: Seconds declared for **one pass** of the schedule derivation, multiplied by the pass count the
#: planner reads off `hours_per_pass` (ruling R162). `rad_engine.schedule` holds a block of hours of
#: the transpose at once and re-reads every per-group direct matrix once per block, so the passes
#: are the multiplier -- see `blind_schedule_steps`' own `estimate_s` section. 30 s is the figure
#: that was declared for the whole step while it was flat, so a one-pass run -- every ordinary
#: LM-83 grid -- publishes exactly what it published before.
_BLIND_SCHEDULE_ESTIMATE_S_PER_PASS: Final[float] = 30.0

#: **How far past its own declared estimate a helper step may run before the queue takes the worker
#: back**, and the two constants below are the only `timeout_s` in this repo.
#:
#: Until 2026-08-31 no `Step` anywhere set one: `grep -rn "timeout_s=" src/` found none over 32
#: `Step(` constructions, so `jobs.py`'s `process.wait(timeout=step.timeout_s)` waited forever on
#: every step of every job, on a queue whose own docstring says *"Concurrency 1. One worker thread
#: runs one job at a time"*. The whole-branch security review graded that Important, paired with the
#: absent `quality` ceiling now in `rad_engine.rtrace._INTEGER_OVERRIDE_CEILINGS`.
#:
#: **Only the two helper steps get one, and the omission elsewhere is the same decision as before.**
#: `Step.timeout_s`'s own docstring says *"a timeout guessed at is a cancellation of correct work"*,
#: and for an `rcontrib` matrix build that is exactly right: its wall time is a property of the
#: scene, the host and the ambient parameters, and there is no number this module can derive. The
#: two helpers are different in the way that matters -- they are **this repository's own Python**,
#: their cost is a traversal count this planner computes (`estimate_s` above and below, both scaled
#: from the operands rather than declared flat), and neither reads anything whose size the planner
#: has not already bounded. A helper still running at twenty times a cost we derived ourselves is
#: not slow work, it is stuck work.
#:
#: **Twenty times, and the factor is doing the honest thing rather than the precise one.** The
#: estimates are `RUNTIME_ESTIMATE_UNCALIBRATED` -- declared, never measured on this machine -- so a
#: tight multiple would kill correct work on a slow host, which is the failure `Step.timeout_s`
#: warns about. Twenty is wide enough that no correct run reaches it and narrow enough that the
#: queue is not held for a day. The floor keeps a tiny job (a one-pass schedule over nine sensors)
#: from inheriting a timeout measured in seconds, where interpreter start-up alone is a
#: material fraction of the budget.
_HELPER_TIMEOUT_FACTOR: Final[float] = 20.0
_HELPER_TIMEOUT_FLOOR_S: Final[float] = 300.0


def _helper_timeout_s(estimate_s: float) -> float:
    """`timeout_s` for a helper step declaring `estimate_s`. See `_HELPER_TIMEOUT_FACTOR`."""
    return max(_HELPER_TIMEOUT_FLOOR_S, _HELPER_TIMEOUT_FACTOR * estimate_s)


def blind_schedule_id(grid: str) -> str:
    """`sched:<grid>` -- LM-83 section 2.2.6's hourly blind schedule for one sensor grid.

    **Two readers since Task 12, which is why it is a function** (`direct_ill_id`'s rule):
    `blind_schedule_steps` seals it and `blend_steps` names it as the edge that carries the
    schedule's invalidation into the blended result.
    """
    return f"sched:{grid}"


def blind_schedule_path(results_dir: Path, grid: str) -> Path:
    """Where `blind_schedule_id`'s JSON is written -- `08_results/blind_schedule_<grid>.json`.

    The file half of the same name. `blind_schedule_steps` passes it to the schedule helper's
    `--out` and `blend_steps` passes it to the blend helper's `--sched`, so a rename that moved one
    and not the other would surface as a helper finding no schedule **after** the annual run had
    been paid for -- the same late failure `direct_ill_path` exists to prevent.
    """
    return results_dir / f"blind_schedule_{grid}.json"


def blind_schedule_steps(
    request: ChainRequest, target: ThreePhaseTarget | None, *, area_sf: float,
    points_expected: int | None, edition: Lm83Edition = DEFAULT_LM83_EDITION,
) -> list[Step]:
    """The one `Step` that derives and seals LM-83's per-group blind schedule, under `edition`.

    **`edition` (R864)** chooses the closing rule: LM-83-12 section 2.2.6's four-row area table,
    the default, or LM-83-22 section 4.6's one 2 % rule at every area. Under LM-83-22 the sealed
    `parameters` gain `lm83_edition: "LM-83-22"` (and the argv `--lm83-edition`, rendered from
    that parameter through `rad_engine.schedule.sealed_lm83_edition`, so the two cannot disagree);
    under LM-83-12 neither appears, so every schedule sealed before R864 keeps its digest and its
    reuse. Under LM-83-22 the density refusal below is not applied: it protects LM-83-12's
    small-area rows, which are absolute point counts, and LM-83-22 has no area rows -- its 2 % rule
    is a ratio in which the count cancels, the case R156 measured as one that cannot flatter sDA.
    `area_sf` is then the LM-83-22 glossary's points times spacing (`results_tool._analysis_area`
    under the same edition), recorded and not decided on. An edition outside `LM83_EDITIONS` is
    refused rather than read as LM-83-12.

    Emitted only where `direct_sun_groups` names groups -- `blind_control="lm83"` and nothing else,
    the same condition and the **same function** that decides which per-group direct-sun runs
    exist. Not a second condition spelled the same way: the operands of this step are exactly those
    runs' outputs, so a planner that decided independently which groups the schedule covers is how
    a schedule comes to name an `illdir` nothing built. Ruling R30's own situation, and the reason
    `direct_sun_groups` is a function rather than an `if` in two places.

    Its output is `sched:<grid>` at `08_results/blind_schedule_<grid>.json`, which
    `rad_engine.blend` reads at run time (Task 12).

    **Why it is sealed here and was not in 7c** (ruling R107): *"7c derives the schedule at READ
    time and seals nothing -- it is a pure function of the direct matrix ... and nothing in 7c
    consumes it. Sealing a derived value with no consumer would be storage for its own sake; in 7d,
    when a number is computed against a schedule, the schedule must be sealed with it."* Task 12
    computes a number against it.

    ## The resolved order rides in `parameters`, and that is the one deliberate asymmetry in the phase

    `InputRecord.role` is **excluded from `content_digest()`** -- it is a function of kind, file
    digests and units, and nothing else -- so relabelling an input does not invalidate what was
    built from its bytes. That is right for V and D: neither reads a role, and retagging is a
    metadata correction that moves no photon (`is_reusable`'s own note on ruling R26).

    It is **wrong for the schedule.** `window_group(action="define")`'s `closing_role` (ruling R133)
    lands on that same field, `closing_order` ranks by it, and the rank decides which prefix of
    groups the scan closes -- so relabelling one group from `"view"` to `"clerestory"` changes which
    blinds close in which hours without moving a single input digest. Recording the resolved order
    on this artifact's `parameters` is what makes `_parameters_match` deny reuse when the order
    moves, while `vmx:<g>:<grid>` and `dmx:<g>`, whose parameter dicts carry no order at all, keep
    reusing across the same relabelling. That asymmetry is the point: one fact is a digest input on
    one artifact and not on another, deliberately, because only one of them reads it.

    `order_substituted` rides beside it for a second reason: it is not merely a label. A substituted
    order ranks by sill height and a declared one ranks by LM-83's two classes, and the two can
    produce the *same* `names` tuple from different rules -- so a project that declares roles which
    happen to agree with its sill heights has a schedule derived by a different rule from one that
    declared nothing, and the two must not share a digest.

    ## The operands, and why `illdir` is not a kind

    `built_from` is every `illdir:<grid>:<g>` and, when a group is automated, each bound schedule's
    input (R870, below). The schedule is a pure function of
    the per-group **pre-blind** direct beam, the analysis area and the threshold; no chain result
    enters it, so no chain result is named -- whichever chain sealed the total illuminance, this
    number would be the same. Both the id and the path come from `direct_ill_id`/`direct_ill_path`,
    which `_direct_sun_leg` also mints from, so the edge and the file cannot disagree about which
    matrix this is. **The Task 11 brief was wrong about both**: it asked for `REQUIRED_EDGES
    ["sched"] = ("illdir",)` over a kind called `illdir` at `07_matrices/illdir_<grid>_<g>.ill`.
    There is no such kind -- these are `kind="ill"` (`ArtifactKind`'s own note) -- and Task 8 writes
    them under `08_results/`. Ruling R122.

    ## One enumeration, so the order and the operands cannot name different sets

    `order.names` and the `--group` arguments are produced by **one loop** over `order.names`, and
    the sequence of `--group` names is therefore literally `parameters["order"]`. A planner that
    built the two from two loops -- one over the target's groups and one over the resolved order --
    could emit a group in the argv that the order does not name, which `group_schedule` refuses as a
    ghost, or a name in the order with no matrix, which it refuses as a missing group. Both
    refusals close the hole from below; nothing above them prevented the caller creating it, which
    is Task 3's review carried into this task.

    ## Automated groups (R870)

    A group `define` marked automated (`WindowGroup.automated`) is **not** in `order` and not a
    `--group`: the scan never decides it. It reaches the helper as `--automated-group NAME:PATH`
    (its own `illdir:<grid>:<g>`, an edge and a `--group-digest` operand like a manual group's),
    and its bound schedule, when one is bound, as `--automated-schedule NAME:PATH` with
    `--automated-schedule-digest` rendered from `parameters["automated_schedules"]`; the bound
    schedule's input id joins `built_from` after the `illdir` ids. `parameters` gain
    `automated_groups` and `automated_schedules` (`"none"` for a group held closed) **only when a
    group is automated**, so an unmarked project plans the step it always planned
    (`tests/test_chain.py::test_an_unmarked_project_plans_what_master_planned`).

    ## The area is an argument, not a `ChainRequest` field

    It is derived from the sensor grid's registered room polygon, which lives in the manifest and is
    read by `rad_mcp.results_tool._analysis_area`; `ChainRequest` carries no polygon and giving it
    an `area_sf` with a default would let a run whose area never arrived silently take LM-83's
    strictest band -- `blinds_close` falls through to *"more than 1 point in sun"* for anything
    below 200 sf, and 0.0 is below 200. Required, keyword-only, no default.

    ### The caller's obligation: one source for this number and the point count (ruling R172)

    **`area_sf` must be `_analysis_area(loaded, grid=request.grid, edition=edition).area_sf` --
    under LM-83-12 the shoelace area of the polygon registered for `request.grid`, and nothing
    else; under LM-83-22 the LM-83-22 glossary's points times spacing of that grid's own `.pts`
    (R864), which is recorded and decides no hour.** The rest of this paragraph is LM-83-12's:
    section 2.2.6 selects a row of its table from this number and then applies that row's own
    criterion to a **point count** that comes
    from the per-group direct matrices, so an `area_sf` measured over a different region than the
    grid was placed in is two bases for one rule. Below `LM83_BLIND_AREA_GENERAL_SF` the row's
    criterion is an absolute count (`> 5`, `> 3`, `> 1`), and a count taken over fewer points than
    the area implies **under-closes**: blinds stay open in hours the standard shuts them, and the
    sDA the blend then computes against this schedule is **OVERSTATED**. That is the flattering
    direction, which is why `results(action="blind_schedule")` refuses its read-time equivalent
    (`BLIND_TRIGGER_MIXED_BASIS`, Phase 7d-1 Task 16) rather than labelling it.

    Concretely, three things this argument may **not** be, each of which type-checks and none of
    which is refused by the finiteness guard below: a *bounding-box* area rather than the shoelace
    area; the area of a **different** grid's polygon; a caller-supplied figure that never came from
    the manifest at all. This function cannot detect any of them -- it is handed a float, and every
    wrong float in the list is a perfectly good positive one -- so the rule is stated here, where
    the parameter is declared, and pinned by `tests/test_chain.py`.

    **Measured at Phase 7d-1 Task 19: this function has exactly one production caller**, and it
    honours the rule above -- `rad_mcp.annual_tool._dispatch` passes
    `_analysis_area(state.loaded, grid=grid, edition=edition).area_sf` (R864 added `edition`), the
    same function
    `results(action="blind_schedule")` derives its own area from. (Task 16 recorded the opposite --
    that every occurrence of this name in `src/` was a docstring -- which was true when it was
    written and is the datedness class this branch has already been bitten by; Task 19 is the
    wiring, so the sentence is corrected here in the same commit that made it false.)
    `tests/test_chain.py::TestTheAreaAndThePointCountShareOneSource` AST-scans `src/` for every
    `area_sf=` argument this function is given and requires the expression to end in `.area_sf`, so
    a second caller cannot land on a bare float. (The class name was wrapped mid-word across two
    lines here and greppable as neither half -- the Task 19 review's Minor 3.)

    ### `points_expected` -- the other half of the same one source (owner ruling R242)

    **It must come off the *same* `_AnalysisArea` object as `area_sf`, in one call**, and the AST
    ratchet above extends to it: two calls to `_analysis_area` would re-read the polygon and
    re-grid it, which is the second derivation the "one source" rule exists to forbid. `None` means
    the polygon could not be gridded, and an unmeasured density neither refuses nor advises -- the
    same reading `results_tool._lm83_expected_points_or_none` already gives it.

    What it buys is the density refusal below -- the last of the four, and the third when this
    sentence was written, before R255-22 added the point-count one above the area guard. Before
    R242, this function had **no density check at all** while `results(action="blind_schedule")`
    refused on one: the rule guarded the schedule a caller could ask to see, and not the schedule a
    run seals or the sDA the blend then computes from it. Measured at `4d60d58`: an 8 x 8 m room
    gridded the documented EN 17037 way (16 points at 2.0 m, 689 sf) planned `sched:coarse` and
    `illblend:coarse` with advisories
    `['METHOD_INFERRED', 'NORTH_CONVENTION', 'BLIND_SCHEDULE_DERIVED']` and no signal, while the
    read side refused the identical basis at `error`.

    ## Four refusals, all at plan time, all because the alternative is refusing after the run

    Raises `ThreePhaseNotBuildableError` when:

    - **the point count is not positive** -- `<= 0`, exactly the set `rad_engine.schedule` refuses
      on `--n-points` (owner ruling R255-22). Section 2.2.6's rule divides by it, so a zero
      denominator makes every hour's trigger False: a year of blinds that never close, and an sDA
      computed against that schedule that is OVERSTATED. **Before R255-22 the two halves of that
      set failed differently, and neither failed here.** A NEGATIVE count planned a step and was
      refused by the helper's own `--n-points` guard at run time, after every `rcontrib` had been
      paid for. ZERO never reached the helper at all: `hours_per_pass` divides by `_cells_per_hour`,
      which is `0` at `n_points=0`, so it raised `ZeroDivisionError` out of this planner at plan
      time, unshaped. The guard's own comment carries that measurement and is the fuller account;
    - **the area cannot select a band** -- not finite, or at or below zero. `blinds_close` falls
      through to LM-83's strictest row for anything under 200 sf, so a missing area does not look
      wrong: it closes blinds early, all year. It also makes `repr(area_sf)` safe to put on an
      argv, which `-inf` is not (argparse reads that token as an option string);
    - **a single hour of the transpose does not fit** under `rad_engine.schedule.
      MAX_SCHEDULE_CELLS`. Only that, since ruling R152: anything larger costs the helper passes
      rather than a refusal, and one hour is the smallest block there is. The check is
      `hours_per_pass` -- the helper's **own** function, rather than a predicate here that agrees
      with it today;
    - **the grid is materially sparser than LM-83's own placement over the polygon that selected
      the band, and the area is below `LM83_BLIND_AREA_GENERAL_SF`** (ruling R242). Below 1 000 sf
      section 2.2.6's criterion is an absolute count (`> 5`, `> 3`, `> 1`) against an unchanged
      threshold, so a sparse grid puts fewer points in sun, **under**-closes, and the sDA the blend
      computes against the resulting schedule is **OVERSTATED**. R198 measures that as a threefold
      inflation of the stated share, entirely in the flattering direction.

      **Refused below 1 000 sf only, and the asymmetry is R156's measurement rather than an
      omission.** At and above it the criterion is `> 2 %` of points -- a ratio in which the count
      cancels -- and the residual row-selection error **over**-closes, understating sDA. Refusing a
      correct-and-conservative schedule for a mismatch that cannot flatter the answer is the wrong
      trade, and it is the trade `results(action="blind_schedule")` already declined to make one
      surface over. That case is `warn` on the read side
      (`results_tool._mixed_basis_blend_advisories`) and plans without comment here.

    **Three of the four -- the point count, the area and the transpose -- have a helper copy that
    fires after every `rcontrib` has been paid for** (whole-branch review, m4: this list grew to
    four refusals across two rulings and the sentence still said "Both", true only when it named
    two). `rad_engine.schedule` refuses `args.n_points <= 0` and
    `not isfinite(args.area_sf) or args.area_sf <= 0` -- both in `main`, quoted rather than
    numbered because an earlier line-number citation of the second had already drifted onto
    the `--n-hours` guard, a real but different refusal from the one this sentence names -- and
    `refuse_oversized_transpose`'s own docstring names both of its call sites. **The density
    refusal has no helper copy**: below
    `LM83_BLIND_AREA_GENERAL_SF` it plans without comment and there is nothing at the helper to
    duplicate it, by the asymmetry R156 measured two paragraphs above. The plan-time refusal is
    still the one that runs first in every case; fix round 1 found the "fires early" principle
    applied to the transpose and not to the area, before the point count and the density rows
    existed.

    ## `estimate_s` counts the helper's passes (ruling R162)

    `hours_per_pass` is called once and its answer is used twice: for the refusal above, and for
    `ceil(n_hours / per_pass)` -- the number of times `rad_engine.schedule` re-reads **every**
    per-group direct matrix, which is what that module's own cost note is written in terms of (an
    ordinary run is 1 pass, a 2 000-point two-group floorplate over a year is 2, and a
    `GRID_VERY_LARGE` grid at one group over a year is 6). Flat, this step declared the same
    seconds for all three, and `rad_mcp.annual_tool`'s `ReuseView.saved_estimate_s` publishes the
    figure to a caller as the time their reuse saved. Still declared rather than calibrated
    (`RUNTIME_ESTIMATE_UNCALIBRATED`); what changed is that it now has the right *shape*.
    """
    groups = direct_sun_groups(target)
    if not groups:
        return []

    # `GroupFacts` is `rad_core`'s plain shape, mapped field by field: that module may not import
    # `rad_engine` (CONTEXT.md, Layers), so the mapping happens here.
    #
    # **`sill_z` is the lowest vertex Z of the group's aperture, not the centroid Z** (ruling R136).
    # The design spec's section 3.1 says centroid and is deliberately overruled: `closing_order`'s
    # substituted branch reads the field as a *sill height* -- "the lowest aperture first, on the
    # reading that a view window sits below a clerestory" -- and a centroid ranks a tall window
    # above a short one that starts lower, which is the opposite of what the reading says.
    #
    # **Automated groups leave the closing order** (R870): their states are their bound schedule,
    # or closed every hour when none is bound, and the 2 % scan never decides them. Only the
    # manual groups are ranked, so `parameters["order"]` and the `--group` names stay the manual
    # order exactly as before; with no automated group `manual` is `groups` and nothing below
    # differs from the step planned before the mark existed.
    manual = [tp_group for tp_group in groups if not tp_group.group.automated]
    automated = sorted(tp_group.group.name for tp_group in groups if tp_group.group.automated)
    order = closing_order([
        GroupFacts(
            name=tp_group.group.name,
            # `closing_role`, never `role`: `window_group` already publishes a `role` for the BSDF
            # chain (`assign_bsdf`'s `BsdfRole`, ruling R63), and the two are different
            # vocabularies. Ruling R133.
            role=tp_group.group.closing_role,
            sill_z=min(
                vertex[2]
                for polygon in tp_group.group.aperture.polygons
                for vertex in polygon.vertices
            ),
        )
        for tp_group in manual
    ]) if manual else ClosingOrder(names=(), substituted=False)

    # **The point count, refused here and shaped, rather than left to fail somewhere downstream**
    # (owner ruling R255-22). `rad_engine.schedule` refuses `--n-points <= 0` and always will -- it
    # is handed an argv by anything, not only by this function -- and until R255-22 this planner
    # refused nothing. The two halves of that set then failed differently: a NEGATIVE count planned
    # a step and was refused by the helper's guard at run time, after every `rcontrib` had been paid
    # for, while ZERO never got that far -- this comment's own `ZeroDivisionError` paragraph records
    # what it did instead. That is the gap the density guard below used to record rather than close.
    #
    # **`<= 0`, the same set the helper refuses**, so plan time and run time cannot disagree about
    # which counts are buildable: a planner that accepted a count its own helper rejects is a run
    # that dies in its last step. Zero is the substantive edge -- `n_points` is section 2.2.6's
    # divisor, and a zero denominator makes every hour's trigger False, which is a year of blinds
    # that never close and an sDA computed against that schedule that is OVERSTATED.
    #
    # **And zero did not even reach the helper: it crashed this planner unshaped.** Measured with
    # this guard removed -- `hours_per_pass` below divides `MAX_SCHEDULE_CELLS` by
    # `_cells_per_hour`, which is `0` at `n_points=0`, and `rad_engine.schedule` raised
    # `ZeroDivisionError`. That is an `ArithmeticError`, so the `except ValueError` around that
    # call did **not** shape it and it left this function raw -- and `run_annual`'s handler chain
    # catches no `ArithmeticError` and no bare `Exception` (`rad_mcp.annual_tool`), so it reaches
    # the wire the way the bare `ValueError` the density note below is about would: `is_error=True`
    # with no `structured_content`, check 0.4's malformed envelope, which ruling R38 graded a
    # violation and which `sensor_grid`'s own `except ArithmeticError` clause exists for. The gap
    # was recorded as being about the negative count; the zero was the worse half of it.
    #
    # **Above the area guard, and therefore above every step this function builds.** Unreachable
    # from the wire (`--n-points` renders `ChainRequest.n_sensors`, which comes from a grid that
    # cannot be empty), but `ChainRequest` is a public constructible dataclass, so this is the same
    # posture the area guard one paragraph down already takes.
    #
    # **The wording deliberately shares no fragment with the helper's refusal or with the density
    # refusal below** -- whole-branch test finding F4: two guards raising the same string is what
    # let a `<= 0` -> `< 0` mutation survive in `rad_engine.schedule`, because the test could only
    # assert the shared text and the shared text was raised either way.
    if edition not in LM83_EDITIONS:
        raise ThreePhaseNotBuildableError(
            f"lm83_edition={edition!r} is not an LM-83 edition; the editions are exactly "
            f"{' and '.join(repr(e) for e in LM83_EDITIONS)}, letter case included. It is refused "
            "rather than read as either, because it chooses the closing rule for every hour."
        )
    if request.n_sensors <= 0:
        raise ThreePhaseNotBuildableError(
            f"grid {request.grid!r} would be scheduled over {request.n_sensors} sensor point(s). "
            f"The blind trigger of {section('blind_trigger', edition)} is a count of grid points "
            "in sun, taken over that "
            "grid and divided by its size: at or below zero there is no grid to count over, every "
            "hour's trigger is False, and the schedule would hold the blinds open all year -- so "
            "the sDA computed against it would be OVERSTATED, the flattering direction. Re-create "
            "the grid so it carries at least one point: sensor_grid(action='create', "
            f"name={request.grid!r}, polygon=[[x, y], ...])."
        )

    # **The area, refused here rather than three hundred `rcontrib`s later** (fix round 1,
    # Important 3). It selects which row of LM-83 section 2.2.6's table decides every hour, and
    # `blinds_close` falls through to the strictest row -- "more than 1 point in sun" -- for
    # anything below 200 sf, so `0.0`, `-1.0` and `nan` do not look wrong: they close blinds early,
    # all year, on every project whose area failed to arrive. The helper guards it too and always
    # will (it is handed an argv by anything, not only by this function); what this call adds is
    # that the refusal arrives *before* the run is paid for, which is the same principle the
    # transpose bound below already honoured and which was applied inconsistently one argument
    # over. It also means `repr(area_sf)` on the next line can never be `nan`, `inf` or `-inf` --
    # and `-inf` is a token argparse reads as an option string rather than a value.
    if not isfinite(area_sf) or area_sf <= 0:
        # LM-83-22's area selects no row (section 4.6's 2 % rule holds at every area), but the
        # schedule records it and the helper refuses it all the same, so it is refused here too,
        # with a sentence that is true of that edition.
        raise ThreePhaseNotBuildableError(
            f"the analysis area for grid {request.grid!r} is {area_sf!r}, "
            + (
                "which is not an area to record: LM-83-22 section 4.6 closes the blinds on more "
                "than 2 % of the points at every area, and this schedule records the LM-83-22 "
                "glossary's points times spacing beside it. Re-create the grid with "
                "sensor_grid(action='create', standard='lm83', ...) so it carries points to count."
                if edition == LM83_22 else
                "which cannot select a "
                "row of LM-83-12 section 2.2.6's blind-trigger table. The rows span an order of "
                "magnitude -- from 'more than 1 point in sun' below 200 sf to 'more than 2 % of "
                "points' at 1 000 sf and above -- and every area at or below zero silently takes "
                "the strictest of them. The area is the shoelace area of the polygon "
                "sensor_grid(action='create') recorded, so re-create the grid with a polygon that "
                "encloses the space."
            )
        )

    # **The density, refused here for the same reason the area above is -- owner ruling R242.**
    # Immediately below the area guard and above every step this function builds, so a grid that
    # cannot carry section 2.2.6's small-area rows is refused before the first `rcontrib` rather
    # than after the whole chain has been paid for and an overstated sDA sealed.
    #
    # **The guard is on the DENSITY, and `grid_polygon_coverage` is a different measure** (ruling
    # R171, and R173-withdrawn/R192 record at length why the two cannot stand in for each other).
    # Coverage is an *area* -- the floor the grid's cells sample over the polygon's shoelace area
    # -- and the Phase 7c ruling that under-coverage **advises and never refuses** is preserved
    # here, not reversed: a correctly-inset LM-83 grid covers less than its polygon by
    # construction. This is a *count*, against the count the row was calibrated on.
    #
    # **`request.n_sensors`, which is the number the schedule helper will actually divide.** It is
    # the same field `--n-points` is rendered from four lines below, so the count this refusal is
    # taken on and the count the derivation uses are one local rather than two reads that agree
    # today -- the property `results_tool._blind_schedule` records for its own `points_total`.
    # **Bound once and read twice, never called twice.** The number in the refusal's message and
    # the number the comparison is taken on are one expression; a second call would be a second
    # derivation of exactly the quantity this rule is about.
    #
    # **`n_sensors >= 0` guards the CALL, not the plan.** `lm83_grid_point_density` raises
    # `ValueError` on a negative count -- correctly, a density cannot be negative -- and a bare
    # `ValueError` out of a planner is not one of the exceptions `run_annual`'s handler chain
    # shapes, so it would reach the wire as `is_error=True` with no `structured_content`: check
    # 0.4's violation, which ruling R38 already graded as one.
    #
    # **The plan is now guarded too, and by the point-count refusal above rather than by this
    # clause** (owner ruling R255-22). Until R255-22 this comment recorded the opposite -- that a
    # negative count *"plans exactly as it did before this guard existed"*, a gap left open on the
    # reading that the refusal which belongs there is about the point count and not about the
    # density. That reading was right about *whose* refusal it is and wrong to leave it unwritten;
    # R255-22 wrote it, above the area guard, where it refuses `<= 0` for the same reason
    # `rad_engine.schedule` does.
    #
    # This screen is therefore unreachable today and is **kept deliberately**, not left behind: it
    # is the second line of defence that keeps a bare `ValueError` off the wire if the guard above
    # is ever narrowed or moved below this point. It costs one comparison and it is the only thing
    # standing between `lm83_grid_point_density` and a caller who edits that guard.
    #
    # **Not applied under LM-83-22** (R864): it protects LM-83-12's small-area rows, and LM-83-22
    # has none -- section 4.6's 2 % rule is a ratio in which the count cancels, the direction R156
    # measured as unable to flatter sDA. `None` is the "no density claim" this guard already reads.
    density = (
        None if points_expected is None or request.n_sensors < 0 or edition == LM83_22
        else lm83_grid_point_density(request.n_sensors, points_expected)
    )
    if (
        density is not None
        and density < LM83_GRID_POINT_DENSITY_MIN
        and area_sf < LM83_BLIND_AREA_GENERAL_SF
    ):
        raise SparseLm83GridError(
            f"the sensor grid {request.grid!r} carries {request.n_sensors} point(s), and LM-83's "
            f"own 0.6 m placement over the polygon this run took its {area_sf:.0f} sf analysis "
            f"area from puts {points_expected} inside it -- a density of {density:.0%}, below "
            f"{LM83_GRID_POINT_DENSITY_MIN:.0%}. LM-83-12 section 2.2.6 selects a row of its "
            "blind-trigger "
            "table from the AREA and then applies that row's own POINT COUNT, and below "
            f"{LM83_BLIND_AREA_GENERAL_SF:.0f} sf that row is an absolute count ('more than 5', "
            "'more than 3', 'more than 1' points in sun) rather than a percentage. A grid this "
            "sparse counts fewer points in sun against an unchanged threshold, so the blinds would "
            "stay open in hours the standard closes them, more daylight would be retained than the "
            "standard allows, and the sDA computed against the resulting schedule would be "
            "OVERSTATED -- the flattering direction, which is why this is refused rather than "
            "planned and labelled.",
            # Phase 25 C2a final fix F, fix round 5 (P3): the calls were elided here
            # (`polygon=[[x, y], ...]`, `run_annual(project=..., epw=..., north=...)`); this module
            # cannot spell them, so it names none, and `rad_mcp.annual_tool` spells both.
            f"Re-create grid {request.grid!r} over the same polygon with sensor_grid's "
            "standard='lm83', which places LM-83's own points, and run again. If the polygon is "
            "the number that is wrong, re-create the grid with the outline the space actually has "
            "instead.",
            grid=request.grid,
        )

    # **The same arithmetic the helper will run, called once for its refusal *and* for the
    # estimate** (rulings R152 and R162). `hours_per_pass` raises only when a single hour of the
    # transpose does not fit; everything larger costs passes rather than a refusal. Calling the
    # helper's own function rather than a predicate that agrees with it is what keeps the planner
    # and the helper from disagreeing about which runs are buildable -- and the planner's answer is
    # the one a caller sees.
    #
    # The answer used to be discarded, which left the declared cost flat while the real one scaled
    # with the block count. It is bound now, so the seconds this step declares and the refusal it
    # can raise come from one call rather than from two that agree today.
    try:
        # Every scheduled group, automated ones included: the helper transposes each group's
        # matrix, so the block size is taken over all of them (R870).
        per_pass = hours_per_pass(
            n_groups=len(groups), n_points=request.n_sensors, n_hours=request.n_hours
        )
    except ValueError as error:
        raise ThreePhaseNotBuildableError(str(error)) from None
    # `per_pass` is at least 1 (`refuse_oversized_transpose` has already rejected the case where a
    # single hour does not fit) and `n_hours` at least 1 (`hours_per_pass` refuses anything else),
    # so this neither divides by zero nor rounds down to a costless step.
    passes = ceil(request.n_hours / per_pass)

    by_name = {tp_group.group.name: tp_group for tp_group in groups}
    # Through `blind_schedule_path`, not spelled here: `blend_steps` passes the same file to the
    # blend helper's `--sched` (Task 12), so the two are one derivation.
    out = blind_schedule_path(request.results_dir, request.grid)
    # **The sealed parameters first, and the argv's edition read back off them** (R864): the
    # helper's `--lm83-edition` is rendered from `parameters` through the same reader every later
    # reader of this record uses, so the edition the schedule is derived under and the edition the
    # manifest says it was derived under are one value. The key is written only under LM-83-22.
    parameters: dict[str, Any] = {
        "order": list(order.names),
        "order_substituted": order.substituted,
        # The same constant `rad_engine.schedule` derives with, imported by both, so the
        # number the schedule was derived at and the number the manifest says it was
        # derived at are one definition rather than two that agree today.
        "threshold_lux": LM83_ASE_THRESHOLD_LUX,
        "area_sf": area_sf,
        "n_points": request.n_sensors,
        "n_hours": request.n_hours,
        "blind_schedule_revision": _BLIND_SCHEDULE_REVISION,
    }
    if edition == LM83_22:
        parameters[LM83_EDITION_PARAMETER] = edition
    # **Written only when a group is automated** (R870), so an unmarked project's parameters, and
    # therefore its digest and its reuse, are exactly what they were. Marking or unmarking a group,
    # binding or rebinding its schedule, each moves these and denies reuse of the old schedule.
    # `"none"` is a group held closed: no digest is `"none"`, which is always `sha256:<hex>`.
    if automated:
        parameters["automated_groups"] = list(automated)
        parameters["automated_schedules"] = {
            name: (
                "none" if by_name[name].group.automated_schedule is None
                else by_name[name].group.automated_schedule.digest
            )
            for name in automated
        }
    sealed_edition = sealed_lm83_edition(parameters)
    argv = [
        HELPER_SENTINEL, "-m", "rad_engine.schedule",
        "--out", str(out),
        "--n-points", str(request.n_sensors),
        "--n-hours", str(request.n_hours),
        "--area-sf", repr(area_sf),
    ]
    if sealed_edition == LM83_22:
        argv += ["--lm83-edition", sealed_edition]
    if order.substituted:
        argv.append("--order-substituted")
    edges: list[str] = []
    operand_digests: list[OperandDigest] = []
    # **The one loop.** Every `--group` argument, every edge, every operand digest and
    # `parameters["order"]` come out of this single iteration over `order.names`, in that order --
    # see this function's own "One enumeration" section.
    for name in order.names:
        if name not in by_name:  # pragma: no cover - `closing_order` maps the names it was given
            raise ThreePhaseNotBuildableError(
                f"the closing order names window group {name!r}, for which this run planned no "
                "per-group direct-sun matrix. The order and the operands must cover the same "
                "groups: a name with no matrix leaves that group contributing zero to the "
                "residual, which opens a blind that should have closed."
            )
        argv += ["--group", f"{name}:{direct_ill_path(request.results_dir, request.grid, name)}"]
        operand_id = direct_ill_id(request.grid, name)
        edges.append(operand_id)
        # **Declared, not written into argv here.** The digest this operand should be checked
        # against is the manifest's, and it is the right one only if this job does not rebuild the
        # matrix -- which `reuse_decision` decides, later and in step order. See
        # `rad_engine.jobs.OperandDigest`. Emitted from the same one loop as the argv and the edge,
        # so the three cannot name three different sets.
        operand_digests.append(
            OperandDigest(artifact_id=operand_id, flag="--group-digest", key=name)
        )
    # **The automated groups, in sorted order** (R870). Each one's direct matrix is still an
    # operand: under LM-83-12 its open hours are in the trigger's residual (`group_schedule`), and
    # the helper validates it under either edition. Its digest is checked like a manual group's.
    for name in automated:
        argv += [
            "--automated-group",
            f"{name}:{direct_ill_path(request.results_dir, request.grid, name)}",
        ]
        operand_id = direct_ill_id(request.grid, name)
        edges.append(operand_id)
        operand_digests.append(
            OperandDigest(artifact_id=operand_id, flag="--group-digest", key=name)
        )
    # **Each bound schedule, rendered from `parameters["automated_schedules"]`** -- the
    # `sealed_lm83_edition` device: the digest the helper checks the file against and the digest
    # the manifest says the schedule was derived from are one value, and so cannot disagree. The
    # schedule's input id is an edge after every `illdir` one, so rebinding it is an input change.
    for name, digest in parameters.get("automated_schedules", {}).items():
        bound = by_name[name].group.automated_schedule
        if digest == "none" or bound is None:
            continue
        edges.append(bound.input_id)
        argv += [
            "--automated-schedule", f"{name}:{bound.path}",
            "--automated-schedule-digest", f"{name}:{digest}",
        ]

    return [Step(
        phase=f"deriving the blind schedule ({section('blind_trigger', sealed_edition)})",
        argv=tuple(argv),
        cwd=request.root,
        # The schedule goes to `--out`; stdout stays the job log, exactly as `rad_engine.blend`'s
        # matrix does. `stdin_path=None` is DEVNULL.
        stdout_path=None,
        stdin_path=None,
        operand_digests=tuple(operand_digests),
        artifact=ArtifactSpec(
            artifact_id=blind_schedule_id(request.grid),
            kind="sched",
            path=out,
            built_from=tuple(edges),
            parameters=parameters,
        ),
        estimate_s=_BLIND_SCHEDULE_ESTIMATE_S_PER_PASS * passes,
        # Derived from the line above rather than declared beside it, so the bound moves with the
        # work whenever the estimate does. See `_HELPER_TIMEOUT_FACTOR`.
        timeout_s=_helper_timeout_s(_BLIND_SCHEDULE_ESTIMATE_S_PER_PASS * passes),
    )]


#: Seconds declared per full matrix traversal in the blend, and the multiplier is
#: `len(contributions) + 1` -- every operand read once, plus the blended matrix written once.
#:
#: **Not `blind_schedule_steps`' pass multiplier, and the difference was measured rather than
#: assumed.** `rad_engine.schedule` holds a block of hours of the transpose at once and re-reads
#: every matrix `ceil(n_hours / hours_per_pass)` times (`schedule.py`'s own "The cost is" note);
#: `rad_engine.blend` does not chunk at all -- `_open_contributions` opens one streaming reader per
#: operand and `_write_rows` advances every one of them in lockstep, sensor row by sensor row, so
#: each operand is traversed exactly once whatever `n_hours` is. A pass multiplier here would
#: declare a cost the helper does not pay.
#:
#: 20 s is anchored so the canonical blend -- one window group in two states, two operands and one
#: output -- keeps the 60 s that was declared for it before this scaled. Declared, not calibrated,
#: as `JobStore.submit`'s `RUNTIME_ESTIMATE_UNCALIBRATED` says of every estimate in this file.
_BLEND_ESTIMATE_S_PER_MATRIX: Final[float] = 20.0

#: The `illblend:<grid>` result's own derivation revision -- `_BLIND_SCHEDULE_REVISION`'s device one
#: artifact further down the chain, and `_DAYLIGHT_MATRIX_REVISION` / `_FIVE_PHASE_RESULT_REVISION` /
#: `_ASE_DIRECT_REVISION`'s three artifacts over.
#:
#: **`illblend` was the one derived artifact on this branch without one**, which the whole-branch
#: composition review raised: `blend_steps`' `ArtifactSpec` recorded `n_points`, `method`,
#: `blind_control` and `contributions`, and nothing that moves when the *derivation itself* is
#: corrected. The gap has the same shape as `sched`'s: the per-hour state selection lives in
#: `rad_engine.blend._write_rows`/`blend_row`, none of it is an argv token, `argv` is not a digest
#: input, and `is_reusable` re-digests the file already on disk -- so a correction to which state
#: each hour selects moves nothing a stored `illblend:<grid>` could be told apart by, and every
#: project would go on reusing a matrix blended by the old rule. A key absent from every
#: pre-correction record is the only separator.
#:
#: It is the most likely artifact on the branch to need one, which is the argument for adding it
#: before rather than after: `rad_engine.blend` is the newest code here and its per-hour selection
#: is the thing a correction would land on. Bump it when `blend_row`'s selection rule, the schedule
#: key it looks the state up under, or the header this seals changes.
_BLEND_REVISION: Final[int] = 1


def blend_ill_id(grid: str) -> str:
    """`illblend:<grid>` -- the schedule-weighted annual result for one sensor grid.

    **One spelling, two readers, and Phase 7d-1 Task 15 is the second reader** -- `blind_schedule_
    id`'s rule one artifact over. `blend_steps` seals this id and `rad_mcp.results_tool`'s resolver
    is what answers a `blind_control="lm83"` run from it; a second, hand-typed
    `f"illblend:{grid}"` in the resolver is two statements of one name that agree until one of them
    moves. The failure is not a crash: it is a resolver reporting *no annual result* for a project
    that holds one, after the whole per-(group, state) fan-out and the blend have been paid for.

    Never `ill:<grid>` -- that id is the **unscheduled** total, and `blend_steps`' own docstring
    says what writing the blend there would cost.
    """
    return f"illblend:{grid}"


def blend_ill_path(results_dir: Path, grid: str) -> Path:
    """Where `blend_ill_id`'s matrix is written -- `08_results/annual_blended_<grid>.ill`.

    The file half of the same name, for `blind_schedule_path`'s reason: `blend_steps` puts this on
    the blend helper's `--out` **and** on the `ArtifactSpec.path` it seals, so a rename that moved
    one and not the other would register a manifest entry pointing at a file nothing wrote. Ruling
    R120's own failure, and it surfaces only when a reader opens the path -- after the run.

    Not derived from the id (`results_dir / id.split(":", 1)[1]`), which spells `illblend:<grid>`
    as `08_results/<grid>`: `group_state_ill_path`'s note, one artifact over.
    """
    return results_dir / f"annual_blended_{grid}.ill"


def blend_steps(request: ChainRequest, target: ThreePhaseTarget | None) -> list[Step]:
    """The one `Step` that sums the per-group, per-state results the sealed schedule selected.

    Emitted only where `direct_sun_groups` names groups -- `blind_control="lm83"` and nothing else,
    the **same function** that decides which per-group direct-sun runs exist and which schedule is
    sealed. Not a second condition spelled the same way: this step's operands are exactly the states
    the schedule chooses between, so a planner that decided independently which runs get a blend is
    how a blend comes to read a schedule nothing sealed. Ruling R30's situation again, and
    `direct_sun_groups`' own reason for being a function rather than an `if` in three places.

    Its output is the `blend_ill_id`/`blend_ill_path` pair, which `rad_mcp.results_tool` reads
    (Task 15) -- the id and the file each spelled in exactly one place, and neither of them here.

    ## Every bound state is an operand, not only the selected ones

    **The schedule is a run-time artifact**: at submit nothing knows which hours close, so the step
    cannot name a subset. Passing all of them and letting `rad_engine.blend` index by the schedule
    is the only form in which the edge set is complete -- and a complete edge set is what stops a
    rebuilt `tmx` from leaving a stale blend reported valid (`ArtifactSpec`'s own note: an artifact
    sealed with an incomplete edge set is *"reported valid and reused forever"*).

    ## `illblend` is a kind of its own, and `illdir` is not

    The two look like the same decision taken twice differently, and they are not. `illdir:<grid>`
    is `kind="ill"` because nothing needs to tell it apart *by kind*: `REQUIRED_EDGES` has no `ill`
    row and can have none (an `ill` is reachable from a point-in-time run that names no matrix at
    all), so a kind for it would buy nothing. The blend is the opposite case -- it has an edge
    requirement that can be stated, `sched` **and** `ill`, and `REQUIRED_EDGES` is keyed by kind.
    Sealed as `kind="ill"` it would inherit the absent `ill` row, `check_edges` would return after
    proving each named edge resolves, and a blend could seal naming nothing at all. `ARTIFACT_
    DIRECTORY["illblend"]` is `08_results` all the same: it is a result, beside the results it sums.

    And it is `illblend:<grid>`, never `ill:<grid>`: that id is the **unscheduled** total, and a
    blend written to it would be handed to a later `blind_control="none"` run as though the blinds
    had never moved -- with `MATRIX_REUSED` and no warning, since the two runs' `ill:<grid>`
    parameters would agree on every key.

    ## What rides in `parameters`, and the one key that is not redundant with `built_from`

    `method="3phase"` is a hard requirement (ruling R158), not decoration: `rad_mcp.results_tool
    ._recorded_chain` reads exactly this key and **defaults a `method`-less artifact to
    `"2phase"`**, so a blend sealed without it makes Task 15's ambiguity refusal print a recovery
    line pointing the caller at the wrong artifact.

    `contributions` records the operand set, for the reason the `ill:<grid>` sum's own
    `window_groups` key records its group set: `reuse_decision` poisons a step whose upstream
    rebuilds in *this* job, which catches a state added or renamed, and catches nothing when one is
    **unbound**. Every surviving edge is untouched and still valid, `is_reusable` asks only whether
    the stored artifact is self-consistent with its own recorded edges, and the closing order does
    not move either -- so `sched:<grid>`'s parameters are identical and nothing poisons this step
    through it. Without this key the two-state blend is served to a one-state run with the unbound
    state's light still in it.

    `n_hours` is deliberately **not** a key. It is not covered by silence: it rides on
    `sched:<grid>`'s own parameters, `sched:<grid>` is an edge of this artifact, and a schedule
    derived over a different hour count therefore rebuilds and poisons this step through
    `reuse_decision`. Restating it here would be a second, independently maintained statement of
    one fact -- the posture the schedule's own row takes towards geometry and materials.
    """
    groups = direct_sun_groups(target)
    if not groups:
        return []

    out = blend_ill_path(request.results_dir, request.grid)
    argv = [
        HELPER_SENTINEL, "-m", "rad_engine.blend",
        # The schedule's own path, from the function that mints it for the step that writes it.
        "--sched", str(blind_schedule_path(request.results_dir, request.grid)),
        "--out", str(out),
        # The row and column counts the helper checks every operand's shape against. `NROWS` alone
        # cannot detect a matrix belonging to a different grid (`_open_contributions`' own note),
        # which is why the count the step was launched with is passed at all.
        "--n-points", str(request.n_sensors),
        "--hours", str(request.n_hours),
    ]
    # **The schedule is an operand, and until Phase 7d-2's fix wave it was the only declared edge of
    # this step that carried no digest** (ruling R255-31). It sits outside the loop below because
    # there is exactly one of it -- which is precisely how it was missed: the loop that emits an
    # `OperandDigest` beside every argv token runs over the contributions only, and a reader
    # checking that "every operand is covered" reads the loop and not the line above it.
    #
    # It is the operand that matters most. The contributions are the ingredients; the schedule is
    # the recipe -- it decides, for every hour and every window group, which contribution is summed.
    schedule_id = blind_schedule_id(request.grid)
    edges = [schedule_id]
    contributions: list[str] = []
    operand_digests: list[OperandDigest] = [
        OperandDigest(
            artifact_id=schedule_id, flag="--sched-digest", key=SCHED_DIGEST_OPERAND_KEY
        )
    ]
    # **One loop**, so the argv, the edge set and `parameters["contributions"]` cannot name three
    # different sets -- `blind_schedule_steps`' "one enumeration" section, applied to the operands
    # rather than to the order. Every state, not the selected ones: see this function's own section.
    for tp_group in groups:
        name = tp_group.group.name
        for state in tp_group.states:
            path = group_state_ill_path(request.results_dir, request.grid, name, state)
            argv += ["--contribution", f"{name}:{state}:{path}"]
            operand_id = group_state_ill_id(request.grid, name, state)
            edges.append(operand_id)
            contributions.append(f"{name}:{state}")
            # Same one loop, same reason as `blind_schedule_steps`: see
            # `rad_engine.jobs.OperandDigest` for why the digest itself is resolved by
            # `reuse_decision` rather than read from the manifest here.
            operand_digests.append(OperandDigest(
                artifact_id=operand_id, flag="--contribution-digest", key=f"{name}:{state}"
            ))

    return [Step(
        phase="blending the window groups per the LM-83 blind schedule",
        argv=tuple(argv),
        cwd=request.root,
        # The matrix goes to `--out`; stdout stays the job log, exactly as the schedule's JSON does.
        # `stdin_path=None` is DEVNULL.
        stdout_path=None,
        stdin_path=None,
        operand_digests=tuple(operand_digests),
        artifact=ArtifactSpec(
            artifact_id=blend_ill_id(request.grid),
            kind="illblend",
            path=out,
            built_from=tuple(edges),
            parameters={
                "n_points": request.n_sensors,
                # **Ruling R158.** `results_tool._recorded_chain` defaults a `method`-less artifact
                # to `"2phase"`; see this function's own `parameters` section for what that costs.
                "method": "3phase",
                "blind_control": LM83_BLIND_CONTROL,
                "contributions": ",".join(contributions),
                # See `_BLEND_REVISION`: without it, a correction to the per-hour state selection
                # leaves every stored blend reported valid and reused.
                "blend_revision": _BLEND_REVISION,
            },
        ),
        # **Scaled by the operands, not flat** (ruling R162). The blend streams every
        # `ill:<grid>:<g>:<s>` it was given once and writes one full `n_points x n_hours` matrix,
        # so its cost is `len(contributions) + 1` matrix traversals -- and `contributions` is the
        # list the loop above already built, rather than a second count of the same operands. A
        # flat figure declared the same seconds for a one-group blend and a six-group one, and
        # `rad_mcp.annual_tool`'s `ReuseView.saved_estimate_s` publishes that number as the time a
        # caller's reuse saved. `_BLEND_ESTIMATE_S_PER_MATRIX` carries the rest of the reasoning,
        # including why this is *not* the schedule's pass multiplier.
        estimate_s=_BLEND_ESTIMATE_S_PER_MATRIX * (len(contributions) + 1),
        # Off the same expression as the estimate, for the reason `_HELPER_TIMEOUT_FACTOR` gives:
        # two derivations of one cost would agree only until one of them was edited.
        timeout_s=_helper_timeout_s(_BLEND_ESTIMATE_S_PER_MATRIX * (len(contributions) + 1)),
    )]


# --- the reuse decision (Phase 6b Task 8) --------------------------------------------------------


def reuse_decision(
    loaded: LoadedProject, steps: list[Step], rebuild: list[str] | None
) -> list[Step]:
    """The same steps, with `Step.reuse` set for every artifact this project can prove is current.

    **Check 6.8, and the call site `rad_engine.project.is_reusable` has been waiting for since Phase
    2.** That predicate had zero production callers: the four conditions were built, tested at the
    engine layer and never consulted by anything that submits work. This is the function that
    consults it.

    **The decision is taken where the manifest is read**, which is why it takes a `LoadedProject`
    rather than reaching for one. `rad_engine.jobs`' `Step.reuse` docstring states the split
    explicitly -- *"nothing in this module ever sets it: the reuse decision is
    `rad_engine.project.is_reusable`'s, taken in the tool layer where the manifest is read. This is
    the mechanism, not the policy"* -- and `rad_engine.project.reuse_advisory`'s says the advisory
    belongs *"where a build is actually skipped, not where one could be"*. Neither is re-implemented
    here and neither is second-guessed: this function calls one and `run_annual` hands the set it
    produced to the other.

    **The step stays in the list.** Filtering a reused step out before `submit` would satisfy check
    6.8(d) -- no `rcontrib` spawn -- and break 6.8(b), which requires the reused artifact still to
    *appear* in the plan with `reuse: true`. `_plan` derives the plan from the steps actually
    submitted (ruling R30), so a step removed from the list is a step absent from the plan. Ruling
    R65 resolved that pull the other way round: the flag rides on the step, the step is submitted,
    and `JobStore._run` declines to spawn it. This function is the half of R65 that sets the flag.

    **A step with no artifact is never reused**, and step 2 of the 3-phase chain is the case that
    matters: `genklemsamp` writes a job-local rays file that step 3 reads on stdin and that nothing
    seals. There is no artifact id to ask `is_reusable` about, and skipping it would leave step 3
    reading a file this job never wrote -- from a *previous* job's directory, or from nothing at all.
    So the condition is `step.artifact is not None`, not "the step looks expensive".

    **`rebuild` wins over the predicate, never the other way round.** An id named there is not
    flagged whatever `is_reusable` answers, which is the whole point of the argument: a caller who
    suspects a matrix is stale for a reason the provenance graph cannot see (a Radiance upgrade,
    which flags rather than invalidates; a sampling parameter they now distrust) has no other way to
    force the build. The comparison is against the artifact **id**, the same string the plan
    publishes under `artifact`, so what a caller reads off one call is what they pass to the next.

    ## The requested parameters are compared here, because nothing below this function compares them

    **`is_reusable` cannot answer this and must not be changed to.** Its question is *"is the stored
    artifact self-consistent"*: the digest it checks comes from `recompute_digests` ->
    `compute_digest(parameters=artifact.parameters)` -- the **stored** flag set, from the original
    build, hashed against itself. That is exactly right for what `project(action='describe')` reports
    and for ruling R7's bytes-still-match guarantee, and it is structurally incapable of noticing
    that *this* call asked for something else, because the requested flag set is never handed to it.
    So a re-run at `-ab 5 -ad 2048` over a project built at `-ab 1 -ad 64` found every artifact
    "valid", reused four of five steps, and republished the old `.ill` byte-identical with
    `MATRIX_REUSED` and no warning -- measured end-to-end against Radiance. The same held for a
    changed `north`, which moves `smx`'s `north_east_of_north` and nothing else.

    **The caller owns "does it match what was asked for", and this is the caller.** Every
    `ArtifactSpec` on both chains already carries the parameter dict this run would build with --
    `parameters_dict(parameters)` plus the per-kind extras (`mf`, `klems_bins`, `n_sensors`,
    `north_east_of_north`, ...). The stored `Artifact.parameters` is the same dict, put through
    `normalise_parameters` by `register_artifact` on the way in. So the comparison is between two
    normalised dicts and needs no new plumbing: normalise the requested one the same way, and a
    difference in *any* published flag -- ambient parameters, `mf`, north, sensor count -- denies
    reuse and the step rebuilds.

    **Both chains, not only the new one.** `reuse_decision` is the single reuse site for the 2-phase
    and 3-phase step lists alike; before Phase 6b `_plan` hardcoded `reuse=False`, so the 2-phase
    path rebuilt everything and could not express this defect. It can now, and the same comparison
    covers it.

    This is what docs/tool-surface.md's provenance contract already says -- an artifact *"is reused
    only when its digest still matches"*, with the ambient parameter set named as part of what it is
    built from -- so the code was the defect and this restores the published behaviour.

    ## A step whose upstream rebuilds in *this* job rebuilds too

    The manifest's digest graph carries a change downstream, but only once the upstream artifact has
    actually been re-sealed -- and at plan time none of this job's steps has run. So `is_reusable`
    asked about the `.ill` reads a `dsmx` digest that has not moved yet and answers "valid", even
    though step 1 of the same list is about to rebuild the matrix the `.ill` is multiplied from.
    Measured directly: at a changed `quality` the DC matrix rebuilt and the `.ill` was still flagged
    for reuse, which is the docs' *"silently mixes cached irradiance from a scene that no longer
    exists into the current answer, and nothing reports it"* with the scene swapped for the regime.

    So the decision is taken in step order and a rebuilt artifact id poisons every later step that
    names it in `built_from`, transitively -- the same direction the digest graph propagates in,
    applied to the artifacts this job has not sealed yet. Nothing is *added* to the reuse set by
    this: it can only turn `True` into `False`.
    """
    forced = set(rebuild or ())
    rebuilding: set[str] = set()
    decided: list[Step] = []
    for step in steps:
        # **The operand digests are resolved for *every* step, including one with no artifact.**
        # A step's own reuse and the digests it hands its helper are independent questions: the
        # branch below is about "there is no artifact id to ask `is_reusable` about", which
        # says nothing about the operands the step *reads*. Resolving them only for artifact-
        # carrying steps left an artifact-less consumer reading its operands unverified, in
        # silence -- caught by `tests/test_chain.py::TestEveryOperandCarriesItsSealedDigest::
        # test_the_digest_the_planner_emits_is_the_one_the_helper_parses`, which is built on
        # exactly that shape. Both production consumers do carry an artifact, so this was latent
        # rather than live; it is fixed rather than documented because the next consumer would
        # inherit the silence.
        #
        # **Called once per step, not twice** (whole-branch review, m3). A step is never its own
        # operand -- `blind_schedule_steps` emits `direct_ill_id` against artifact
        # `blind_schedule_id`, `blend_steps` emits `group_state_ill_id` against `blend_ill_id` --
        # so a call made before this step's own `reuse` is decided and one made after always
        # returned the same tuple; the first was a discarded `O(operands x artifacts)` linear scan
        # (`Manifest.artifact`) on every artifact-carrying step, for a value nothing used.
        if step.artifact is None:
            decided.append(replace(
                step, argv=_with_operand_digests(loaded, step, rebuilding=rebuilding)
            ))
            continue
        artifact_id = step.artifact.artifact_id
        reuse = (
            artifact_id not in forced
            and not rebuilding.intersection(step.artifact.built_from)
            and _parameters_match(loaded, step.artifact)
            and is_reusable(loaded, artifact_id)
        )
        if not reuse:
            rebuilding.add(artifact_id)
        # **After this step's own classification**, not before: a step is never its own operand,
        # but `rebuilding` may have gained this step's id, and reading the set after the decision
        # is the ordering the "A step whose upstream rebuilds" section describes rather than one
        # line earlier.
        decided.append(replace(
            step, reuse=reuse, argv=_with_operand_digests(loaded, step, rebuilding=rebuilding)
        ))
    return decided


def _with_operand_digests(
    loaded: LoadedProject, step: Step, *, rebuilding: set[str]
) -> tuple[str, ...]:
    """`step.argv` plus one digest token per operand this job is **not** rebuilding.

    ## Why the digest is resolved here and nowhere earlier

    `rad_engine.schedule` and `rad_engine.blend` read matrices that earlier steps of this same job
    produce. At plan time none of those steps has run, so `Artifact.content_digest` for such an
    operand is the *previous* run's value. Passing it unconditionally would refuse every rebuild
    as drifted bytes -- an outage, not a missed check -- and passing it never would leave two
    `rad_engine` helpers reading sealed, digest-carrying artifacts with **no verification at all**,
    which is the defect this exists to close.

    Only this function can tell the two apart. `rebuilding` is built in step order and already
    carries the transitive poisoning this function's own "A step whose upstream rebuilds" section
    describes, and the operand steps come **before** the steps that read them -- so by the time a
    schedule or blend step is decided, every operand it names has already been classified. That
    ordering is the whole reason this is a post-pass rather than a builder's job, and
    `tests/test_chain.py` asserts it directly rather than inferring it from a green plan.

    **An operand with no `content_digest`, or one this job rebuilds, contributes no token**, and the
    helper then reads it with `content_digest=None` -- `iter_verified_annual_lux_rows`' documented
    silence. The alternative, refusing, would refuse every first run of every project.

    **The token is appended, never inserted**, so an existing argv is unchanged up to its old
    length: `StepRecord.argv` records what actually ran, and a reader diffing two jobs' plans sees
    the digests as an addition rather than as a reshuffle.
    """
    if not step.operand_digests:
        return step.argv
    extra: list[str] = []
    for operand in step.operand_digests:
        if operand.artifact_id in rebuilding:
            continue
        try:
            stored = loaded.manifest.artifact(operand.artifact_id)
        except KeyError:
            # Not sealed at all -- a first run. `is_reusable` would have said the same, and the
            # operand's own step is in `rebuilding` in every ordinary plan; this is the guard for
            # the case where it is not in this step list at all.
            continue
        if stored.content_digest is None:
            continue
        extra += [operand.flag, f"{operand.key}:{stored.content_digest}"]
    return step.argv + tuple(extra)


def _parameters_match(loaded: LoadedProject, spec: ArtifactSpec) -> bool:
    """Whether the stored artifact was built with the flag set `spec` is asking for now.

    Both sides go through `rad_core.manifest.normalise_parameters`, which is the same function
    `register_artifact` applies when it seals: `-ad` and `ad` are one key, `1024` and `1024.0` are
    one value, and key order is not a difference. Comparing raw dicts instead would deny reuse over
    spellings that hash identically, which would be the opposite error -- rebuilding a matrix that
    is genuinely current.

    **`DIGEST_EXCLUDED_PARAMETERS` is stripped from both sides after normalising, the same set
    `compute_digest` itself excludes.** `-n` is a core count that changes wall time, and a result
    only as a new random draw does, within the recorded bands (R780,
    docs/tool-surface.md#concurrency; until R780 this said "changes wall time, not results"), so
    comparing it here would deny reuse of a matrix moved to a machine with a different core count
    -- a difference the artifact's own digest already ignores, which this comparison exists to
    mirror, not to re-litigate.

    A missing artifact is `False`: there is nothing to compare against, and `is_reusable` will
    decline it a moment later for the same reason. Returning `True` here would make this function's
    answer depend on which of the two ran first.
    """

    def _comparable(parameters: Mapping[str, object]) -> dict[str, object]:
        return {
            key: value
            for key, value in normalise_parameters(parameters).items()
            if key not in DIGEST_EXCLUDED_PARAMETERS
        }

    try:
        stored = loaded.manifest.artifact(spec.artifact_id)
    except KeyError:
        return False
    return _comparable(stored.parameters) == _comparable(spec.parameters)


def reused_ids(steps: list[Step]) -> list[str]:
    """The artifact ids actually flagged, in step order -- what `MATRIX_REUSED` names."""
    return [
        step.artifact.artifact_id for step in steps if step.reuse and step.artifact is not None
    ]
