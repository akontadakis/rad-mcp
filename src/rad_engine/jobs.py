"""Job supervision -- the async spine every simulation tool runs through.

CONTEXT.md requires this to be designed in rather than retrofitted: *"a 5-phase annual run outlives
any MCP client timeout"*, and retrofitting it later means rewriting every simulation tool. So this
module is the single record for a run, and the `job` tool (Task 5) and the MCP Tasks extension
(Task 6) are two front doors onto it -- **one store**. A task id and a job id name the same record
(docs/tool-surface.md#long-running-work-job-is-the-fallback-not-the-interface); two stores that
could disagree about whether a matrix was committed is the same class of defect as two ground
reflectances.

**Three stream rules meet here, and this module is where each would be lost.**

- *Nothing writes to stdout, ever.* On stdio, stdout **is** the JSON-RPC transport, so a Radiance
  binary spawned with inherited stdout writes octree bytes into the protocol stream and kills the
  session (docs/plan.md check 0.2 names the job supervisor as exactly where this regresses). Every
  spawn below passes an explicit `stdout=`.
- *Nothing reads stdin either.* The same argument one file descriptor over: on stdio, stdin is the
  request stream, and a binary that inherits it consumes client requests. Every spawn passes
  `stdin=DEVNULL`.
- *Radiance stderr is never merged into stdout* (CONTEXT.md). The job directory holds `stdout` and
  `stderr` as two files, always, and a step whose payload is binary sends its stdout to the artifact
  file while its stderr still goes to the job's stderr log. Merging corrupts the data stream, and
  when the target is a file it corrupts the artifact.

**A process that exits 0 having written warnings is the most common way to get a confidently wrong
answer.** Every step's stderr is classified through `rad_engine.advisories.classify`, never
discarded, never passed through raw, and never re-implemented here.

**Durable before the response.** *"The task must be durably created before sending the response"*
(Tasks). `submit` writes `10_jobs/<job_id>/job.json` with an fsync and an atomic rename **before**
the job is queued and before the id is returned, so an id handed to a client survives a restart.
That is the manifest's existing rule for artifacts applied to jobs.

**`partial_artifacts` is the point of the failure design.** A 5-phase run that dies in Part 3 has
already spent an hour building V, D and the 3-phase result. Each step commits its artifact through
`rad_engine.project.register_artifact` the moment that step succeeds, so a later failure leaves
those committed with valid digests and a re-run reuses them.

**The pre-build edge check is called here.** `check_edges` is public precisely so a builder can run
it before spawning anything: the seal-time check inside `register_artifact` cannot be skipped, but
by then the matrix is already written and a refusal discards an hour of work
(docs/tool-surface.md#how-provenance-invalidates-a-matrix).

**Queue.** A single FIFO queue, concurrency 1, and the running job gets the machine
(docs/tool-surface.md#concurrency). Priorities and preemption are deferred: a quick render submitted
behind a 5-phase run waits, and `position` is how the user finds that out.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from collections import deque
from collections.abc import Callable, Mapping, Sequence
from contextlib import ExitStack
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Final, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from rad_core.apertures import Aperture
from rad_core.manifest import ArtifactKind

from .advisories import EngineAdvisory, Severity, classify
from .cores import worker_cores  # re-exported; moved by R780
from .env import RadianceEnv, raypath_dirs_of
from .octree import (
    SceneInlineCommandError,
    build_ase_octree,
    build_octree,
    octree_body_digest,
    refuse_inline_commands_in_files,
    scene_include_files,
)
from .process_group import (
    DEFAULT_KILL_GRACE_S,
    ChildCollector,
    collecting_children,
    kill_group,
    stop_has_begun,
)
from .results import coefficient_matrix_is_all_zero
from .safe_io import NotARegularFileError, handle_digest, open_regular_file
from .scene import (
    parse_polygons,
    probe_unfollowed_materials,
    scene_without_modifiers,
    two_phase_probe_glazing,
)
from .suns import SunPosition, suns_in_hemisphere
from .visibility import PROBE_SKY_TEXT, any_sun_reaches, sun_ray_radiance
from .project import (
    LoadedProject,
    ProjectNameError,
    ProjectPaths,
    check_edges,
    manifest_lock,
    open_project,
    register_artifact,
)

__all__ = [
    "DEFAULT_KILL_GRACE_S",
    "LEGAL_TRANSITIONS",
    "MAX_QUEUED_JOBS",
    "RECORD_NAME",
    "STDERR_NAME",
    "STDOUT_NAME",
    "TERMINAL_STATES",
    "TOMBSTONE_SUFFIX",
    "AdvisoryRecord",
    "ASE_OCCLUSION_PROBE_TIMEOUT_S",
    "ArtifactSpec",
    "AseOcclusionProbe",
    "CommittedArtifact",
    "HELPER_SENTINEL",
    "JobError",
    "JobLogsUnavailableError",
    "JobNotFoundError",
    "JobRecord",
    "JobRecordExpiredError",
    "JobState",
    "JobStore",
    "Launcher",
    "OperandDigest",
    "PROBE_CLOSED_MODIFIER",
    "QueueFullError",
    "RadianceLauncher",
    "Step",
    "StepInputChangedError",
    "StepSceneRefusedError",
    "StepRecord",
    "SystemLauncher",
    "is_same_process",
    "kill_group",
    "new_job_id",
    "parse_elapsed",
    "ProbeVerdict",
    "probe_ase_occlusion",
    "started_when_recorded",
    "status_payload",
    "worker_cores",
]

_log = logging.getLogger(__name__)
"""Logging goes to stderr, never stdout -- see the module docstring."""

JobState = Literal["queued", "running", "succeeded", "failed", "cancelled"]

#: States from which nothing moves. `cancel` on one of these is late rather than wrong, and the
#: register allocates `JOB_CANCEL_LATE` at `info` to say so.
TERMINAL_STATES: Final[frozenset[str]] = frozenset({"succeeded", "failed", "cancelled"})

#: **The state machine, written down once.** Every transition goes through `_finish`, which refuses
#: anything not in this table.
#:
#: It is a table rather than a comment because the alternative was four call sites each re-deriving
#: legality for themselves, and the first thing that cost was two of them disagreeing about what
#: "the server stopped" means -- `shutdown` recording `cancelled` for a job nobody cancelled while
#: `recover` argued, correctly, that nobody had. A five-state machine whose only explicit artefact
#: is the terminal set is a machine that will drift again.
#:
#: `queued -> failed` is legal and is not dead: a job abandoned before it ever started is `failed`,
#: because it will never run and nobody cancelled it.
LEGAL_TRANSITIONS: Final[Mapping[str, frozenset[str]]] = {
    "queued": frozenset({"running", "cancelled", "failed"}),
    "running": frozenset({"succeeded", "failed", "cancelled"}),
    "succeeded": frozenset(),
    "failed": frozenset(),
    "cancelled": frozenset(),
}

#: The record file inside `10_jobs/<job_id>/`. `stdout` and `stderr` sit beside it, and the argv and
#: timing the layout promises (docs/tool-surface.md#2-project) live in this file rather than in a
#: fourth one, so there is one record per run and not two that can disagree.
RECORD_NAME: Final = "job.json"
STDOUT_NAME: Final = "stdout"
STDERR_NAME: Final = "stderr"

#: A purged job leaves this behind so "aged out" and "never existed" stay distinguishable --
#: `JOB_RECORD_EXPIRED` versus `JOB_NOT_FOUND`. Without the tombstone a purge would silently demote
#: every expired handle to "unknown", which sends the client to the wrong recovery
#: (docs/tool-surface.md#handles-project-and-job_id).
TOMBSTONE_SUFFIX: Final = ".expired.json"

#: How many stderr lines a record carries. Matches `job(action="logs", tail=50)`; the full stream
#: stays on disk, so this is a summary and never the only copy.
STDERR_TAIL_LINES: Final = 50

#: How many jobs may wait in the queue at once (owner decision D8 of 2026-10-02, ruling R756). A
#: running job does not count. A chosen value, not a measurement: no source gives a queue bound,
#: and each queued job holds its `Step` tuple in memory and a directory under `10_jobs/` from the
#: moment it is submitted (docs/tool-surface.md#unsourced-values). `submit` refuses the next job
#: with `QUEUE_FULL`.
MAX_QUEUED_JOBS: Final = 32


def new_job_id(*, now: datetime | None = None) -> str:
    """A job id that is unique, sortable by creation, and a legal path component.

    It becomes a directory name under `10_jobs/`, so it must satisfy the same component rule a
    project name does (docs/tool-surface.md#handles-project-and-job_id). The timestamp prefix makes
    `sorted(ids)` creation order, which is what `list` reports and what FIFO means.
    """
    stamp = (now or datetime.now(UTC)).strftime("%Y%m%dT%H%M%S")
    return f"{stamp}-{uuid.uuid4().hex[:8]}"


# --------------------------------------------------------------------------------------------
# Errors -- refusals, in the engine's shape
# --------------------------------------------------------------------------------------------


class JobError(RuntimeError):
    """Something the job store refuses to do.

    Same convention as `rad_engine.project.ProjectError`: on the wire a refusal is
    `{"refused": true, "reason": ..., "advisories": [...]}` (docs/tool-surface.md#refusals), but
    that shape belongs to `rad_mcp`, and building it here would put the wire contract in the
    adapter. The engine raises carrying the allocated code; `rad_mcp` shapes the envelope.
    """

    code: str | None = None
    severity: Severity = "error"

    @property
    def advisory(self) -> EngineAdvisory | None:
        if self.code is None:
            return None
        return EngineAdvisory(code=self.code, severity=self.severity, message=str(self), lines=())


class JobNotFoundError(JobError):
    """No record of this id anywhere the store has been told to look."""

    code = "JOB_NOT_FOUND"


class JobRecordExpiredError(JobError):
    """The id was valid and its record aged out.

    Deliberately a different message from `JOB_NOT_FOUND`: *"a call against an expired or unknown
    handle should return a tool execution error that says so, so the model can recover"*, and the
    recoveries differ -- one is re-run the work, the other is check the id
    (docs/tool-surface.md#handles-project-and-job_id).
    """

    code = "JOB_RECORD_EXPIRED"


class JobLogsUnavailableError(JobError):
    """The record is live and answering, and its captured output is not on disk any more.

    Allocated `JOB_LOGS_UNAVAILABLE` 2026-08-01. The reachable case is
    `project(action="delete", force=True, confirm=True)`: the `10_jobs/` tree goes with the project
    while the in-memory record survives, so `status` keeps answering normally and `logs` used to
    raise a bare exception with an absolute path in it. Check 1.11 requires *"a call against an
    expired or unknown handle should return a tool execution error that says so"*, and this handle
    is neither expired nor unknown -- it never self-heals either, because nothing purges a record
    whose project was removed underneath it.

    **Not folded into `JOB_RECORD_EXPIRED`**, whose message is *"finished at ... and its record was
    purged"*: that sentence would be untrue of a job whose record is right here, and the recoveries
    differ -- one is re-run the work, this one is "the evidence went with the project; `status`,
    `list` and the committed artifacts are still good". Not `JOB_NOT_FOUND` either, for the same
    reason ruling R6 kept `PROJECT_NOT_FOUND` and `MANIFEST_UNREADABLE` apart.
    """

    code = "JOB_LOGS_UNAVAILABLE"


class QueueFullError(JobError):
    """`MAX_QUEUED_JOBS` jobs are already waiting, so `submit` refused this one (ruling R756).

    Raised before anything reaches disk: no job directory, no record, no id a client could poll. A
    running job does not count against the bound. The recovery is the caller's -- cancel a queued
    job, or wait for one to start -- so the message says both.
    """

    code = "QUEUE_FULL"


class StepSceneRefusedError(JobError):
    """A queued step's scene inputs carry a `!` line ruling R33 refuses, so the worker did not spawn
    the step (ruling R755). Built inside `JobStore._run`, which records the job `failed` with this
    code on the record and `failed_phase` naming the step, and logs a warning: an expected refusal,
    not a supervision failure, so it does not reach `_work`'s traceback handler. Distinct from the
    tool's submit-time refusal: here the file changed after it was scanned at submit, or was never
    scanned."""

    code = "SCENE_INLINE_COMMAND"


class StepInputChangedError(JobError):
    """A file a queued step reads changed after the job was submitted, so the worker did not spawn
    the step (ruling R822, extended). `run_annual` builds its octrees at submit, at one path per
    grid, and records each one's digest on the steps that read it (`Step.input_digests`); another
    call at the same grid, a `dry_run` included, rewrites that file in place, and the queued step
    would otherwise trace the other call's scene and seal a wrong matrix with no error. Finished
    inside `JobStore._run` the way `StepSceneRefusedError` is: the job is `failed` with this code
    and `failed_phase` naming the step."""

    code = "OCTREE_REBUILT_SINCE_SUBMIT"


# --------------------------------------------------------------------------------------------
# What a job is made of
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class OperandDigest:
    """A digest argument a step should be given **only if** the operand it names is not rebuilt.

    ## Why this is declared rather than written into `argv` at plan time

    `rad_engine.schedule` and `rad_engine.blend` read matrices that earlier steps of the *same job*
    produce. At plan time none of those steps has run (`chain.reuse_decision`'s own words), so the
    manifest's `content_digest` for such an operand describes the **previous** run's bytes:

    - if the producing step is **reused**, the file on disk is untouched and that digest is exactly
      the right thing to verify against -- and verifying it in the helper closes the window between
      the planner's check and the helper's read, which is the whole point of this task;
    - if the producing step **rebuilds**, the file is about to be overwritten and the stored digest
      is stale **by design**. Passing it would refuse every rebuild as drifted bytes -- a total
      outage, not a missed check.

    Only `reuse_decision` knows which of the two happened, because only it runs `is_reusable` and
    the requested-parameter comparison, in step order, with the poisoning of downstream steps
    already applied. So the builders declare the *intent* here and that function turns it into
    argv. A builder that guessed from `is_reusable` alone would get the parameters half wrong and
    pass a stale digest, which is the outage above.

    ## What the helper's check narrows, and what it does not

    **The clause above is about one window only -- planner-check to helper-read -- and closing it
    is not closing the defect.** What the helper then does is
    `rad_engine.results.iter_verified_annual_lux_rows`: hash from a held descriptor, seek, read the
    rows. That defeats **replacement** of the file between the hash and the read (a rename, an
    unlink-and-recreate) because an fd binds the inode. It does **not** defeat an **in-place
    rewrite of that same inode** -- a `pwrite` or an `ftruncate` in that interval still wins and is
    undetected. Said here as well as in `blend._open_contributions`, that reader and
    docs/tool-surface.md, because one docstring that says "closes" with no residual beside it is
    how the next reader concludes the defect is shut.

    **And "replacement defeated / in-place not defeated" is not an exhaustive enumeration.** Two
    further residual classes are disclosed in full at the published surface (`docs/tool-surface.md`
    §14, *"Two more residual classes"*; whole-branch review, F7): the digest's **trust root** is
    the same unsigned `manifest.json` that an attacker able to perform the in-place write already
    owns, so the read reports as verified with no race at all; and a **directory symlink planted in
    the parent chain** substitutes the path upstream of both seeks, which fails closed with a
    digest present and open without one. Neither is fixed; both are named so the pair above stops
    reading as complete.

    **And an operand this job rebuilds carries no digest at all**, by the reasoning above, so its
    matrix is read unverified. That is correct -- no current sealed digest for it exists -- and it
    is genuinely partial coverage rather than a complete check. **What follows and is easy to
    miss** (whole-branch review, m13): on a fresh `run_annual(blind_control="lm83")` every group
    matrix, every per-state contribution and the schedule itself are produced by earlier steps of
    that same job, so every one of them is `rebuilding` and *no digest token is emitted at all*.
    This check covers the **reuse path** -- a re-run that reuses those artifacts. That is the path
    the defect this task closes lived on, and it is not every path.

    `flag` and `key` are the helper's own wire form (`--group-digest` + `NAME`,
    `--contribution-digest` + `GROUP:STATE`); the digest itself is read from the manifest at
    decision time and appended as `f"{key}:{digest}"`. See
    `rad_engine.blend.parse_operand_digests` for why the digest is the untouched remainder of that
    token rather than a capped split.
    """

    artifact_id: str
    """The operand this digest verifies -- a `built_from` edge of the step that carries it."""
    flag: str
    """The helper's repeatable flag, e.g. `"--contribution-digest"`."""
    key: str
    """The operand's key in that helper's own namespace, e.g. `"north:closed"`."""


#: The bound on each of the occlusion probe's spawns (two `oconv`, one `xform` -- two in a
#: two-phase run's strip mode, which flattens and then strips -- and one `rtrace`), in seconds;
#: each `oconv` is followed by a `getinfo -d` read of its bounding cube, bounded by
#: `rad_engine.octree`'s own metadata timeout instead. `rad_mcp.annual_tool.OCTREE_BUILD_TIMEOUT_S`,
#: the bound the same scene's submit-time octree builds take, restated because this layer may not
#: import `rad_mcp`. A chosen value (docs/tool-surface.md, Unsourced values); a probe that exceeds
#: it gives no verdict, and the matrix is then refused as it was before the probe existed.
ASE_OCCLUSION_PROBE_TIMEOUT_S: Final[float] = 300.0


@dataclass(frozen=True)
class AseOcclusionProbe:
    """What `probe_ase_occlusion` traces for one `asemx` (rulings R831, R848): the registered scene
    with `modifiers` removed, plus a unit glow sky, and from every point of `points_path` one ray
    along each of `suns` that lies in front of any of `apertures` -- or, in strip mode
    (`strip_glazing`, ruling R856), the scene with every glazing surface removed and one ray along
    every one of `suns`.

    Set by `rad_engine.chain._direct_sun_leg` on the whole-grid `asemx:<grid>` and on every
    per-group `asemx:<grid>:<g>`, always with **every** window group's apertures and modifiers
    (R848: a per-group probe that left another group's opening in place sealed the series case),
    whenever the run resolved a window group and carries the dome. A two-phase run resolves none;
    its probe sets `strip_glazing` instead (ruling R856). What is opened is always every group's
    apertures; a per-group probe (`group` set, ruling R858) also traces the scene again with its
    own group's apertures closed, and answers from the difference.

    The ASE_net matrix's probe (`closed` set, ruling R869) is the whole-grid probe -- or, on a
    two-phase run, the strip-mode one -- with the automated groups' aperture polygons closed by an
    opaque black surface in its one trace: it asks whether any sun still reaches the grid through
    the openings that remain.
    """

    scene_files: tuple[str, ...]
    """The registered scene, as the ASE octree compiles it (`ChainRequest.scene_files`)."""
    modifiers: tuple[str, ...]
    """Every window group's aperture modifiers, whose primitives are removed so the openings are
    open."""
    apertures: tuple[Aperture, ...]
    """A sun direction is cast when it lies in front of any of these (`suns_in_hemisphere`)."""
    suns: tuple[SunPosition, ...]
    """The Reinhart dome the matrix was built over (`ChainRequest.ase_suns`)."""
    points_path: Path
    """The job's sensor-points file; the first three numbers of each line are a ray origin."""
    cwd: Path
    """The project root, against which `scene_files` resolve."""
    scratch_dir: Path
    """The job directory, where the probe's scene and octrees are written and then removed."""
    suns_scene: Path | None = None
    """The suns scene the whole-grid ASE octree was compiled with (`ChainRequest.ase_suns_scene`)."""
    scene_digest: str | None = None
    """That ASE octree's submit-time body digest (R822's snapshot, `ChainRequest.ase_scene_digest`).
    The probe recompiles the same octree from the files as they are now and gives no verdict when
    the digests differ: it must judge the scene the matrix was traced against."""
    strip_glazing: bool = False
    """A two-phase run's probe (ruling R856): no window group to open, so every glazing surface of
    the flattened scene is removed (`rad_engine.scene.two_phase_probe_glazing`) and every dome
    direction is cast. `modifiers` and `apertures` are then empty."""
    group: Aperture | None = None
    """The window group whose per-group matrix this probe judges (ruling R858), or `None` for the
    whole-grid matrix and in strip mode. With it set the probe traces every ray twice, with every
    group's aperture removed and again with this group's aperture polygons closed by an opaque
    black surface, and answers `True` when some ray carries less radiance in the second than in
    the first: sun reaches the grid through this group's opening. A group shaded by independent
    massing beside a lit group is then sealed, and a group in series with another still refused."""
    closed: tuple[Aperture, ...] = ()
    """The apertures of the window groups marked automated, set on the ASE_net matrix's probe alone
    (ruling R869), and empty on every other. With it set the probe adds these apertures' polygons
    to the stripped scene as an opaque black surface (`_closed_group_text`) and traces once with
    `any_sun_reaches`, over the directions the probe without it casts (every dome direction in
    strip mode): `True` when some ray still carries radiance out, that is sun reaches the grid with
    the automated groups closed. With `group` also set it gives no verdict (two specs mixed)."""


@dataclass(frozen=True)
class ProbeVerdict:
    """The occlusion probe's answer: `reaches` is `True`/`False`, or `None` with `reason` saying why
    the probe gave no verdict (R848). No verdict refuses the matrix exactly as before."""

    reaches: bool | None
    reason: str | None = None
    stripped_glazing: tuple[str, ...] = ()
    """The glazing modifiers a two-phase probe removed (R856), named by the seal's notice."""


class _ProbeStopped(Exception):
    """The job was cancelled, abandoned by a `shutdown`, or caught by a stop signal's sweep, while
    (or before) its occlusion probe ran (Phase 24 final review): whatever the probe answered, a
    killed child included, is not a verdict on the matrix. `JobStore._run` ends the job as a stop
    during the step ends it, and the matrix is neither sealed nor refused. `signalled` says whether
    a probe child was sent a kill."""

    def __init__(self, *, signalled: bool) -> None:
        super().__init__("the job was stopped while its occlusion probe ran")
        self.signalled = signalled


#: The opaque black material R858's closed trace puts over a group's aperture polygons. Named so it
#: cannot collide with a scene modifier, as `rad_engine.visibility.PROBE_SKY_MODIFIER` is.
PROBE_CLOSED_MODIFIER = "rad_mcp_probe_closed"


def _closed_group_text(*apertures: Aperture) -> str:
    """Every polygon of `apertures` as an opaque black surface (R858's closed trace closes one
    group; R869's ASE_net probe closes every automated group). One material and one running index
    across all of them, so no two polygons share an identifier."""
    lines = [f"void plastic {PROBE_CLOSED_MODIFIER}\n0\n0\n5 0 0 0 0 0\n"]
    polygons = [polygon for aperture in apertures for polygon in aperture.polygons]
    for index, polygon in enumerate(polygons):
        coords = " ".join(f"{c:.17g}" for vertex in polygon.vertices for c in vertex)
        lines.append(f"{PROBE_CLOSED_MODIFIER} polygon {PROBE_CLOSED_MODIFIER}_{index}\n0\n0\n"
                     f"{3 * len(polygon.vertices)} {coords}\n")
    return "\n".join(lines)


#: The coordinate tolerance, in scene units, within which `_coincident` takes two vertices for
#: one: the flattened scene re-prints the registered coordinates, and a reprint is not a move.
_SAME_VERTEX_TOLERANCE: Final[float] = 1e-6


def _coincident(a: Any, b: Any) -> bool:
    """Whether polygons `a` and `b` lie exactly on one another: the same vertices to within
    `_SAME_VERTEX_TOLERANCE`, as a cyclic sequence read in either direction from any start.
    Geometric only -- the identifiers are ignored (fix round 3): Radiance allows a surface any
    identifier, and two surfaces at one place are coincident whatever they are called."""
    first, second = list(a.vertices), list(b.vertices)
    if len(first) != len(second) or not first:
        return False

    def _same(u: Any, v: Any) -> bool:
        return all(abs(p - q) <= _SAME_VERTEX_TOLERANCE for p, q in zip(u, v))

    n = len(first)
    for candidate in (second, second[::-1]):
        for start in range(n):
            if all(_same(first[k], candidate[(start + k) % n]) for k in range(n)):
                return True
    return False


def _probe_origins(points_path: Path) -> list[tuple[float, float, float]]:
    """The grid points' positions: the first three numbers of each non-blank line."""
    with open_regular_file(points_path, "rb") as handle:
        text = handle.read().decode("utf-8")
    origins: list[tuple[float, float, float]] = []
    for line in text.splitlines():
        tokens = line.split()
        if tokens:
            x, y, z = (float(token) for token in tokens[:3])
            origins.append((x, y, z))
    return origins


def probe_ase_occlusion(env: RadianceEnv, probe: AseOcclusionProbe) -> ProbeVerdict:
    """Whether any sun direction reaches the grid with every window group's aperture opened
    (rulings R831, R848) -- for a per-group probe (`probe.group`, ruling R858), whether it reaches
    through that group's opening -- or, for a two-phase run's probe (`probe.strip_glazing`, ruling
    R856), with every glazing surface removed.

    A per-group probe first gives no verdict in strip mode, or when the group's polygons are not
    among `apertures`' (by identifier). Then, in order, each giving **no verdict** (the matrix is
    then refused as before) when it fails:

    1. some dome direction lies in front of some aperture. In strip mode: `modifiers` and
       `apertures` are empty (the probe chooses what to strip itself), the job carries a dome, and
       **every** dome direction is cast -- a two-phase run has no aperture to choose "in front"
       directions from, and an unglazed opening has no polygon a filter could be built from;
    2. the scene is the one the matrix was traced against: `oconv -f` of the scene files and the
       suns scene, as `build_ase_octree` compiles them, has the ASE octree's submit-time body digest;
    3. the strip removed **every** aperture polygon (by identifier) -- one left in the scene is a
       closed opening. In strip mode the scene is flattened, every glazing surface modifier of it
       (`rad_engine.scene.two_phase_probe_glazing`: never one leading to `antimatter`, whose removal
       would fill the hole it cuts) is stripped, and the stripped scene must hold no glazing
       surface left;
    4. every material a surface of the stripped scene uses is one whose transmission a direct
       `-ab 0` ray follows (`rad_engine.scene.probe_unfollowed_materials`; it lists `antimatter`,
       so a scene that cuts an opening with it gives no verdict, and `dielectric`, ruling R857, so
       a lone pane facing the grid, which absorbs every probe ray, gives none either);

    then the stripped scene plus `rad_engine.visibility.PROBE_SKY_TEXT` is compiled. For the
    whole-grid and two-phase probes it is traced with `any_sun_reaches`: `True` if any ray carries
    radiance out, `False` if none does.

    For the ASE_net matrix's probe (`probe.closed`, ruling R869) the scene compiled is the stripped
    scene plus every polygon of `closed` as an opaque black surface (`_closed_group_text`), traced
    once the same way over the same directions (every dome direction in strip mode): `True` when
    sun reaches the grid with the automated groups closed. It gives no verdict with `group` also
    set (two specs mixed), outside strip mode when a polygon of `closed` is not among
    `apertures`' (by identifier), and when a surface still in the stripped scene lies exactly on a
    polygon of `closed` (`_coincident`: the same vertices in either direction from any start,
    whatever its identifier; in strip mode, for example, an automated group's own polygon that is
    not glazing): which of two coincident surfaces a ray hits is undefined.

    For a per-group probe (the difference trace, R858) each ray's radiance is traced instead
    (`rad_engine.visibility.sun_ray_radiance`) in the stripped scene, and again in the stripped
    scene plus the group's aperture polygons as an opaque black surface (`PROBE_CLOSED_MODIFIER`):
    `True` when some ray carries strictly less radiance closed than open -- sun reaches the grid
    through this group's opening -- and `False` otherwise. Radiance, not reaches-or-not: a ray split
    by `glass` can leave through the group on one branch and reach the sky another way on the
    other, reaching either way. A black surface can only block, so a ray carrying more closed than
    open is a probe fault and gives no verdict.

    Steps 2 and 3 read the scene files separately (`oconv` for the check, `xform` for the strip --
    twice in strip mode, to flatten and then to strip), so the bytes of every scene file and every
    file its `!xform` lines reach are digested before the check and again after the strip; a file
    that changed in between gives no verdict (final review of 2026-10-05: the strip read the files a
    few seconds after the check, unchecked).

    In strip mode every verdict reached after the glazing is read carries it as `stripped_glazing`.

    Every file is written under a per-call name in the job directory and removed afterwards; the
    octrees are frozen (`build_octree`'s default) and cast from before their scene file goes.
    """
    if probe.closed and probe.group is not None:
        # R869 with R858: a net probe closes the automated groups and a per-group probe closes its
        # own; a probe given both is two specs mixed, and either trace would answer the other's
        # question. Worded without "window group" or "aperture" (D-M7).
        return ProbeVerdict(None, "the probe was given both groups to close for a net matrix and "
                            "a group to close for a per-group matrix")
    if probe.strip_glazing:
        if probe.modifiers or probe.apertures:
            # Final review (D-M7, re-review M1): worded without "window group" or "aperture", since
            # this reason is interpolated into a two-phase refusal, which names neither (R856).
            return ProbeVerdict(None, "the two-phase probe strips every glazing surface itself and "
                                "was also given surfaces to open or modifiers to remove")
        if probe.group is not None:
            # R858: worded without "window group" or "aperture", as the reason above (D-M7).
            return ProbeVerdict(None, "the two-phase probe strips every glazing surface itself and "
                                "was also given a group to close")
        directions = tuple(sun.direction for sun in probe.suns)
        if not directions:
            return ProbeVerdict(None, "the job carries no Reinhart dome")
    else:
        if probe.group is not None and not (
            {polygon.id for polygon in probe.group.polygons}
            <= {polygon.id for aperture in probe.apertures for polygon in aperture.polygons}
        ):
            return ProbeVerdict(None, "the group to close is not among the openings the probe "
                                "opens")
        if probe.closed and not (
            {polygon.id for aperture in probe.closed for polygon in aperture.polygons}
            <= {polygon.id for aperture in probe.apertures for polygon in aperture.polygons}
        ):
            # R869: an automated group the probe did not open would be closed over a scene in which
            # it is already closed, which answers nothing about the openings that remain.
            return ProbeVerdict(None, "an automated group to close is not among the openings the "
                                "probe opens")
        directions = tuple(
            sun.direction for sun in probe.suns
            if any(suns_in_hemisphere(aperture, (sun,)) for aperture in probe.apertures)
        )
        if not directions:
            return ProbeVerdict(None, "no Reinhart dome direction lies in front of any window "
                                "group's aperture")
    glazing: tuple[str, ...] = ()
    stem = probe.scratch_dir / f"ase_probe_{uuid.uuid4().hex[:12]}"
    check_path = stem.with_name(stem.name + "_scene.oct")
    scene_path, sky_path = stem.with_suffix(".rad"), stem.with_name(stem.name + "_sky.rad")
    octree_path = stem.with_suffix(".oct")
    closed_scene_path = stem.with_name(stem.name + "_closed.rad")
    closed_octree_path = stem.with_name(stem.name + "_closed.oct")
    try:
        if probe.suns_scene is None or probe.scene_digest is None:
            return ProbeVerdict(None, "the job carries no submit-time snapshot of the scene the "
                                "matrix was traced against")
        before = _scene_bytes_digests(env, probe)
        build_ase_octree(
            env, list(probe.scene_files), str(probe.suns_scene), out_path=check_path,
            cwd=probe.cwd, timeout=ASE_OCCLUSION_PROBE_TIMEOUT_S,
        )
        if octree_body_digest(check_path) != probe.scene_digest:
            return ProbeVerdict(None, "the registered scene changed after this job was submitted, "
                                "so the probe cannot judge the scene this matrix was traced "
                                "against")
        if probe.strip_glazing:
            flat = scene_without_modifiers(
                env, list(probe.scene_files), modifiers=(), cwd=probe.cwd,
                timeout=ASE_OCCLUSION_PROBE_TIMEOUT_S,
            )
            glazing = two_phase_probe_glazing(flat.text)
            modifiers = glazing
        else:
            modifiers = probe.modifiers
        stripped = scene_without_modifiers(
            env, list(probe.scene_files), modifiers=modifiers, cwd=probe.cwd,
            timeout=ASE_OCCLUSION_PROBE_TIMEOUT_S,
        )
        if _scene_bytes_digests(env, probe) != before:
            return ProbeVerdict(None, "a scene file changed while the probe ran, so the scene it "
                                "stripped may not be the one it checked", stripped_glazing=glazing)
        if probe.strip_glazing:
            glazing_left = two_phase_probe_glazing(stripped.text)
            if glazing_left:
                return ProbeVerdict(None, "removing every glazing surface left glazing surface(s) "
                                    f"{', '.join(glazing_left)} in the scene, so an opening would "
                                    "have stayed closed", stripped_glazing=glazing)
        else:
            left = sorted(
                {polygon.id for aperture in probe.apertures for polygon in aperture.polygons}
                - set(stripped.removed)
            )
            if left:
                return ProbeVerdict(None, "removing the window groups' aperture modifiers left "
                                    f"aperture polygon(s) {', '.join(left)} in the scene, so the "
                                    "opening would have stayed closed")
        unfollowed = probe_unfollowed_materials(stripped.text)
        if unfollowed:
            removed = (
                f"with every glazing surface removed ({', '.join(glazing) or 'none found'}), "
                if probe.strip_glazing else ""
            )
            return ProbeVerdict(None, f"{removed}the scene uses material(s) whose transmission the "
                                "probe's direct ray does not follow: "
                                f"{', '.join(unfollowed)} (only glass and trans with no diffuse "
                                "transmission are followed)", stripped_glazing=glazing)
        if probe.closed:
            # Task 4 review, Minor 2: a closed polygon still in the stripped scene (in strip mode,
            # one that is not glazing) would have the black polygon laid exactly on it, and which
            # of two coincident surfaces a ray hits is undefined. Worded without "window group" or
            # "aperture": this reason reaches a two-phase refusal (D-M7). Fix round 3: the test is
            # geometric (`_coincident`), so a surface at that place is found whatever its
            # identifier and whichever way round its vertices run, and the reason names the
            # surface in the scene by its own identifier.
            closed_polygons = [
                polygon for aperture in probe.closed for polygon in aperture.polygons
            ]
            coincident = sorted({
                kept.id for kept in parse_polygons(stripped.text)
                if any(_coincident(kept, closed) for closed in closed_polygons)
            })
            if coincident:
                one = len(coincident) == 1
                return ProbeVerdict(None, f"{'a surface' if one else 'surfaces'} still in the "
                                    f"scene after the strip, {', '.join(coincident)}, "
                                    f"{'lies' if one else 'lie'} exactly where the probe would "
                                    "lay the surface that closes a group marked automated, and "
                                    "which of two coincident surfaces a ray hits is undefined",
                                    stripped_glazing=glazing)
        traced_text = stripped.text
        if probe.closed:
            # R869: the automated groups closed in the one trace the net probe makes.
            traced_text += "\n" + _closed_group_text(*probe.closed)
        with open_regular_file(scene_path, "xb") as handle:
            handle.write(traced_text.encode("utf-8"))
        with open_regular_file(sky_path, "xb") as handle:
            handle.write(PROBE_SKY_TEXT.encode("utf-8"))
        octree = build_octree(
            env, [str(scene_path), str(sky_path)], out_path=octree_path, cwd=probe.cwd,
            timeout=ASE_OCCLUSION_PROBE_TIMEOUT_S,
        )
        if probe.group is None:
            return ProbeVerdict(any_sun_reaches(
                env, octree.path, _probe_origins(probe.points_path), directions, cwd=probe.cwd,
                timeout_s=ASE_OCCLUSION_PROBE_TIMEOUT_S,
            ), stripped_glazing=glazing)
        origins = _probe_origins(probe.points_path)
        opened = sun_ray_radiance(env, octree.path, origins, directions, cwd=probe.cwd,
                                  timeout_s=ASE_OCCLUSION_PROBE_TIMEOUT_S)
        with open_regular_file(closed_scene_path, "xb") as handle:
            handle.write((stripped.text + "\n" + _closed_group_text(probe.group)).encode("utf-8"))
        closed_octree = build_octree(
            env, [str(closed_scene_path), str(sky_path)], out_path=closed_octree_path,
            cwd=probe.cwd, timeout=ASE_OCCLUSION_PROBE_TIMEOUT_S,
        )
        closed = sun_ray_radiance(env, closed_octree.path, origins, directions, cwd=probe.cwd,
                                  timeout_s=ASE_OCCLUSION_PROBE_TIMEOUT_S)
        # Strict, with no tolerance: any noise the extra polygon adds errs towards refusal, as a
        # ray brighter closed is no verdict and a ray dimmer closed is sun through the group.
        if any(c > o for o, c in zip(opened, closed)):
            return ProbeVerdict(None, "a ray carried more light with this group's opening closed, "
                                "which an opaque surface cannot cause, so the probe's trace "
                                "cannot be trusted")
        return ProbeVerdict(any(c < o for o, c in zip(opened, closed)))
    finally:
        for path in (check_path, scene_path, sky_path, octree_path, closed_scene_path,
                     closed_octree_path):
            path.unlink(missing_ok=True)


def _scene_bytes_digests(env: RadianceEnv, probe: AseOcclusionProbe) -> dict[str, str | None]:
    """`{path: sha256 of its bytes, or None if unreadable}` for every scene file of `probe` and every
    file the `!xform` include search reaches from them (`scene_include_files`, the build's
    RAYPATH)."""
    files = [probe.cwd / name for name in probe.scene_files]
    files += list(scene_include_files(list(probe.scene_files), cwd=probe.cwd, raypath=env.raypath_dirs(probe.cwd)))
    digests: dict[str, str | None] = {}
    for path in files:
        try:
            with open_regular_file(Path(path), "rb") as handle:
                digests[str(path)] = handle_digest(handle)
        except OSError:
            digests[str(path)] = None
    return digests


def _probe_env(launcher: Launcher) -> RadianceEnv | None:
    """The Radiance install the probe spawns through: a `RadianceLauncher`'s, or none."""
    return launcher.env if isinstance(launcher, RadianceLauncher) else None


@dataclass(frozen=True)
class ArtifactSpec:
    """What a step commits when it succeeds.

    `built_from` is asserted **twice**: by `check_edges` before the step spawns, and again by
    `register_artifact` at seal time. The early call is the one this module exists to make.
    """

    artifact_id: str
    kind: ArtifactKind
    path: Path
    built_from: tuple[str, ...]
    parameters: Mapping[str, Any] = field(default_factory=dict)
    ase_aperture: Aperture | None = None
    ase_suns: tuple[SunPosition, ...] | None = None
    """The sun geometry `rad_engine.project.register_artifact` tests an all-zero `asemx` against
    before it concludes the aperture does not transmit -- **both or neither** (that function raises
    `ValueError` on half of the pair, and on an empty `ase_suns`).

    Set by `chain._direct_sun_leg` on a **per-group** `asemx:<grid>:<g>` alone (ruling R144): the
    guard needs one aperture, and the whole-grid `asemx:<grid>` is the run with every aperture open.
    Carried on the spec rather than looked up at commit time because the aperture is the *chain's*
    knowledge -- it is the group whose octree this matrix was traced against, and `_commit` has only
    the manifest, in which two groups' apertures look alike.

    Every other kind leaves both `None`, which is what every caller before Phase 7d-1 Task 8 did:
    the guard then fails closed and refuses an all-zero matrix, exactly as it did then."""
    ase_probe: AseOcclusionProbe | None = None
    """The occlusion probe `JobStore._commit` runs before sealing an `asemx` that would otherwise be
    refused (rulings R831, R848; R856 gives a two-phase run, which has no window group, a probe of
    its own that strips every glazing surface, R858 names the group a per-group probe closes, and
    R869 the automated groups the ASE_net matrix's probe closes, `AseOcclusionProbe.closed`);
    `None` for every other kind and for an `asemx` planned without the run's sun directions
    (`chain._direct_sun_leg`), and then the guard refuses an all-zero matrix exactly as it did
    before the probe."""
    ase_chain: str | None = None
    """The chain the run that plans this `asemx` builds (`"2phase"`, `"3phase"` or `"5phase"`),
    set by `chain._direct_sun_leg` on every `asemx` it plans and `None` for every other kind. It is
    passed through to `rad_engine.project.register_artifact`, whose refusal of an all-zero matrix
    says what the failed run leaves readable, and that differs by chain (final review A1:
    a three-phase `lm83` run seals no `ill:<grid>`, so a refusal there leaves no annual result)."""


@dataclass(frozen=True)
class Step:
    """One process in a job, and what it produces.

    `phase` is the human string `job(action="status")` reports while this step runs -- the spec's
    example is `"building D matrix (2/6)"`. The builder writes it because only the builder knows how
    many phases there are.
    """

    phase: str
    argv: tuple[str, ...]
    cwd: Path
    stdout_path: Path | None = None
    """Where this step's stdout goes. `None` sends it to the job's own `stdout` log; a path sends it
    to that file, which is how a binary payload (an octree, an HDR, a matrix) is captured. Either
    way it is declared, and stderr goes somewhere else -- never merged."""
    stdin_path: Path | None = None
    """Where this step's stdin comes from. `None` means `DEVNULL`, which stays the default.

    Added by ruling R43, because `rfluxmtx` takes its sensor points **only** on stdin and the
    2-phase chain cannot otherwise be expressed as a job. Measured on Radiance 6.1a before the field
    was added: a points file in the sender slot exits 1 with `-i, -I supported for pass-through
    only`, and with `-I+` removed it exits 1 with `no sender surface!` -- a double lock, and `-I+`
    is the whole purpose of a daylight-coefficient matrix. `rtrace` and `rcontrib` are the same.

    **A path, never inline bytes.** A sensor-points payload carried on `Step` would land in the
    durable record, which is fsynced before the job id is returned; the file is already on disk and
    already the artifact's own provenance. The worker opens it and passes the handle, so `stdin=` is
    still declared explicitly at the spawn and check 0.2's guard reads the same keyword it always
    did -- "every subprocess declares its stdin" stays literally true rather than gaining an
    exception. A declared path that cannot be opened **fails the step**: falling back to the default
    would be the one outcome worth preventing, since an inherited fd 0 on stdio is the JSON-RPC
    request stream (CONTEXT.md)."""
    artifact: ArtifactSpec | None = None
    estimate_s: float | None = None
    timeout_s: float | None = None
    """A hard bound for a step that must not hang the queue. `None` means unbounded, which is the
    right default for a matrix build: a timeout guessed at is a cancellation of correct work."""
    operand_digests: tuple[OperandDigest, ...] = ()
    """Digest arguments `chain.reuse_decision` appends to `argv` for the operands this job does not
    rebuild -- see `OperandDigest` for why the decision cannot be taken at plan time.

    **Declarative, like `reuse` beside it.** Nothing in this module reads it: the worker spawns the
    argv it is given, and by then `reuse_decision` has already turned these into tokens. R44's
    property that the supervisor offers no per-step Python hook is untouched, for `reuse`'s own
    stated reason -- a declarative field is not a callback."""
    reuse: bool = False
    """Whether this step's artifact is already built, so the worker must **not** spawn it.

    Added by ruling R65. Reuse across runs is the whole value of the 3-phase method -- change a
    glazing BSDF and only T rebuilds while V and D survive -- and check 6.8 asserts that in two
    halves that pull against each other: **(b)** the job plan's `reuse: true` set is exactly
    {S, V, D}, so a reused artifact still *appears* in the plan; **(d)** no `rcontrib` process is
    spawned in the second run, which the check itself calls the real assertion because *"wall time
    is machine-dependent, process spawning is not."*

    Filtering reused steps out of `steps` before `submit` satisfies (d) and **breaks (b)**: the plan
    is derived from the submitted steps *by design*, since a plan authored separately from the steps
    can describe a job the worker is not running -- the drift ruling R30 names. So the flag rides on
    the step, the step stays in the list, and the skip happens where the spawn is (`JobStore._run`).

    **The default is `False`, and every 2-phase call site keeps its meaning unchanged.** Nothing in
    this module ever *sets* it: the reuse decision is `rad_engine.project.is_reusable`'s, taken in
    the tool layer where the manifest is read. This is the mechanism, not the policy -- R44's
    property that the worker offers no per-step Python hook still holds, because a declarative flag
    is not a callback."""
    scene_inputs: tuple[str, ...] = ()
    """Files this step's spawn reads as Radiance scene text, relative to `cwd` or absolute (ruling
    R755). The worker re-runs ruling R33's `!`-line scan over them immediately before the spawn,
    with the RAYPATH of the environment the launcher resolved for this step, and fails the job with
    `SCENE_INLINE_COMMAND` instead of spawning. A queued step reads its files when it starts, which
    can be long after the tool scanned them at submit. Declarative, like `reuse`: the worker still
    offers no per-step Python hook (R44). Not in `StepRecord`: a recovered job never restarts."""
    input_digests: tuple[tuple[str, str], ...] = ()
    """`(path, octree_body_digest)` pairs for files this step's spawn reads that another call can rewrite
    in place between submit and spawn -- `run_annual`'s octrees, built at submit at one path per
    grid (ruling R822, extended). The worker re-hashes each file once, immediately before the spawn,
    and fails the job with `OCTREE_REBUILT_SINCE_SUBMIT` instead of spawning on any mismatch or
    unreadable file. Declarative, like `scene_inputs`; not in `StepRecord`."""


# --------------------------------------------------------------------------------------------
# Launching -- the seam between "a Radiance binary" and "a process"
# --------------------------------------------------------------------------------------------


#: `argv[0]` of a step that runs this server's own Python helper rather than a Radiance binary.
#:
#: **Resolved at spawn, never stored resolved.** `Step` is frozen and lands in the durable record,
#: which is fsynced before the job id is returned; an interpreter path baked in there is a machine
#: fact stored as a job fact, and a record replayed elsewhere would spawn an interpreter that may not
#: exist. The sentinel goes through `Launcher.resolve` exactly as `rcontrib` does, which is what
#: keeps that seam's own guarantee literally true: *"no code path can reach a bare name and let the
#: inherited PATH decide which binary runs."* A bare `"python"` is not special-cased and is still
#: refused as an unknown Radiance binary.
#:
#: Angle brackets because no Radiance binary is named that and no filesystem path starts with one,
#: so the sentinel cannot collide with a real argv[0].
HELPER_SENTINEL: Final[str] = "<python>"

#: The directory `rad_engine` itself was imported from, put on the helper's `PYTHONPATH`.
#:
#: **Measured at Phase 7d-1 Task 19, the first task to run a helper step through a real job.** The
#: schedule step spawns `sys.executable -m rad_engine.schedule`, and `RadianceEnv.environ` inherits
#: `os.environ` -- which carries whatever `PYTHONPATH` the *server's* environment happened to set,
#: and nothing else. A parent whose `rad_engine` reached `sys.path` by any other route hands the
#: child an interpreter that cannot import it, and the job dies at its last step with
#: `ModuleNotFoundError: No module named 'rad_engine'` **after** every matrix has been built.
#: Reproduced end to end on this machine: `pytest` puts `src/` on `sys.path` through its own
#: `pythonpath` ini key (`pyproject.toml`), which is invisible to a subprocess, so every blended
#: run failed at `deriving the blind schedule (LM-83 2.2.6)` with a returncode of 1.
#:
#: **This is the launcher's own rule one import system over.** `Launcher` exists so *"no code path
#: can reach a bare name and let the inherited PATH decide which binary runs"*; a helper resolved to
#: `sys.executable` and then left to find `rad_engine` on the inherited `PYTHONPATH` lets the
#: environment decide **which copy of this package** runs -- a different version, an older install,
#: or none at all. Pinning it to the directory this module was actually imported from makes the
#: helper the same code as its parent by construction.
#:
#: Prepended rather than replacing: an inherited `PYTHONPATH` may carry a caller's own additions,
#: and this only guarantees that ours is found first. Both halves of that sentence -- *prepended*
#: and *a caller's own additions survive* -- are pinned by
#: `tests/test_jobs.py::TestTheHelperInterpreter`, which presets `PYTHONPATH` in the launcher's own
#: environment. They were unasserted until the Task 19 review measured it: `PYTHONPATH` is absent
#: from `os.environ` under `pytest`, so the `else` arm below never executed and appending instead
#: of prepending left the whole fast gate green.
#:
#: **Prepending `PYTHONPATH` is not by itself enough, and `PYTHONSAFEPATH` is what closes the gap**
#: (the Task 19 review's Minor 1). Both helper steps are `<python> -m rad_engine.{schedule,blend}`
#: spawned with `cwd=request.root`, and under `-m` CPython puts the **cwd ahead of `PYTHONPATH`**
#: on `sys.path` -- so a directory named `rad_engine` under a project root would win, and the
#: sentence above ("the same code as its parent by construction") would be false of exactly the
#: case it is written for. Measured on this machine before the fix: a `/tmp` directory holding
#: `rad_engine/__init__.py` shadowed this tree's copy under `PYTHONPATH=<repo>/src python -m
#: rad_engine.schedule`. `PYTHONSAFEPATH=1` (CPython >= 3.11, and `pyproject.toml` requires 3.11)
#: removes that cwd entry, leaving the pinned `PYTHONPATH` as the first place the helper looks.
#:
#: It was never exploitable through this server's tool surface -- no tool writes a `.py` file
#: anywhere, and the one caller-named directory (`project(action="create", name=...)`) is guarded
#: by `_COMPONENT`, so the most a caller could land is an empty `rad_engine/`, which the finder
#: records as a namespace portion and passes over. That is why this is hardening rather than a
#: patched hole; the reason to take it is that the comment above should be true as written.
_HELPER_PACKAGE_PARENT: Final[str] = str(Path(__file__).resolve().parent.parent)


class Launcher(Protocol):
    """Resolves a step's argv and environment.

    A seam rather than a hard-wired `RadianceEnv` for two reasons. The state machine and the queue
    must be testable with a fake command so their logic is green on a machine with no Radiance
    (docs/plan.md, Phase 1), and later phases will run helper processes that are not Radiance
    binaries. Both go through one resolver, so no code path can reach a bare name and let the
    inherited `PATH` decide which binary runs.
    """

    def resolve(self, argv: Sequence[str], *, cwd: Path) -> tuple[list[str], dict[str, str]]: ...

    @property
    def version(self) -> str | None: ...


@dataclass(frozen=True)
class RadianceLauncher:
    """Resolves through a discovered Radiance install.

    `env.environ(cwd)` sets `RAYPATH` explicitly with `.` first and never inherits it -- an
    inherited value missing `.` produces an empty octree with a zero exit status (CONTEXT.md).
    """

    env: RadianceEnv

    def resolve(self, argv: Sequence[str], *, cwd: Path) -> tuple[list[str], dict[str, str]]:
        binary, *rest = argv
        if binary == HELPER_SENTINEL:
            # Honoured only here, at argv[0] -- a sentinel in an argument position is a filename a
            # step happens to pass, not an interpreter request. `self.env.environ(cwd)`, the same
            # call the Radiance branch below makes, not a second RAYPATH-setting path of its own:
            # every key it sets, RAYPATH and PATH included, is identical on both branches.
            #
            # **Two keys are added, and only for the helper** -- `PYTHONPATH`, led by the
            # directory this package was imported from, and `PYTHONSAFEPATH`, which keeps the
            # spawn directory from getting in front of it. See `_HELPER_PACKAGE_PARENT` for both
            # measurements and for why this is the launcher's own "the environment must not choose
            # what runs" rule applied to the import system. A Radiance binary imports no Python and
            # gets neither key, so this is a helper-only addition rather than a divergence in how
            # the two environments are built.
            environ = self.env.environ(cwd)
            inherited = environ.get("PYTHONPATH")
            environ["PYTHONPATH"] = (
                _HELPER_PACKAGE_PARENT if not inherited
                else os.pathsep.join([_HELPER_PACKAGE_PARENT, inherited])
            )
            # `-P`'s environment form. Set here rather than as an argv flag because `rest` is the
            # step's own argv and a launcher that edited it would be deciding what the step runs,
            # not where it imports from.
            environ["PYTHONSAFEPATH"] = "1"
            return [sys.executable, *rest], environ
        return [str(self.env.path(binary)), *rest], self.env.environ(cwd)

    @property
    def version(self) -> str | None:
        return self.env.version


@dataclass(frozen=True)
class SystemLauncher:
    """Runs an explicitly named executable that is not a Radiance binary.

    `argv[0]` **must** be an absolute path to an existing file. A bare name would resolve through
    whatever `PATH` the server inherited, which is the same class of mistake as an inherited
    `RAYPATH`: the command that runs would be chosen by the environment rather than by the caller.
    """

    environ: Mapping[str, str] = field(default_factory=dict)
    version: str | None = None

    def resolve(self, argv: Sequence[str], *, cwd: Path) -> tuple[list[str], dict[str, str]]:
        binary = Path(argv[0])
        if not binary.is_absolute() or not binary.is_file():
            raise JobError(
                f"{argv[0]!r} is not an absolute path to an existing executable. A bare name is "
                "resolved by the inherited PATH, which chooses the binary from outside the caller."
            )
        return [str(binary), *argv[1:]], dict(self.environ)


# --------------------------------------------------------------------------------------------
# The durable record
# --------------------------------------------------------------------------------------------


class _Record(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AdvisoryRecord(_Record):
    """`EngineAdvisory` as it is stored.

    The engine's advisory is a frozen dataclass rather than a pydantic model -- deliberately, so the
    wire model does not leak downward -- and this record has to survive a restart, so it is mapped
    here rather than embedded. `lines` is kept whole: a classified advisory's evidence is the raw
    Radiance text, and a record that dropped it could not be re-read later.
    """

    code: str
    severity: Severity
    message: str
    lines: list[str] = Field(default_factory=list)
    subject: str | None = None

    @classmethod
    def of(cls, advisory: EngineAdvisory) -> AdvisoryRecord:
        return cls(
            code=advisory.code,
            severity=advisory.severity,
            message=advisory.message,
            lines=list(advisory.lines),
            subject=advisory.subject,
        )

    def engine(self) -> EngineAdvisory:
        return EngineAdvisory(
            code=self.code,
            severity=self.severity,
            message=self.message,
            lines=tuple(self.lines),
            subject=self.subject,
        )


class CommittedArtifact(_Record):
    """A snapshot of an artifact this job sealed.

    A snapshot, **not** a second source of truth: the manifest decides whether an artifact is still
    valid and `is_reusable` re-reads its bytes. What this carries is the identity, the edges and the
    digests as of the moment the job committed them, so a `failed` job can report exactly what
    survived without the caller reopening the project.
    """

    id: str
    kind: str
    path: str
    digest: str
    content_digest: str | None = None
    built_from: list[str] = Field(default_factory=list)


class StepRecord(_Record):
    """One step, as it ran -- the argv and timing the layout promises the job directory records."""

    phase: str
    argv: list[str]
    cwd: str
    stdout_path: str | None = None
    stdin_path: str | None = None
    """What this step read on stdin, recorded for the same reason `stdout_path` is: a DC matrix that
    came out wrong is diagnosed from the points that went into it (ruling R43)."""
    started_at: datetime | None = None
    finished_at: datetime | None = None
    returncode: int | None = None
    artifact_id: str | None = None
    estimate_s: float | None = None
    skipped: bool = False
    """Whether this step was skipped because its artifact was already built (`Step.reuse`, R65).

    A separate field rather than an inference from `returncode is None`, which is **ambiguous**: a
    step cancelled before it started has no returncode either, and so does a step whose job died
    mid-flight. A record has to be able to say which of its steps never ran and why, or the
    difference between "reused" and "never got there" is lost the moment the job is read back.

    Defaulted, so a `job.json` written before this field existed still loads -- `_load_record` goes
    through `JobRecord.model_validate_json`, and pydantic fills a defaulted field from an absent key.
    (`extra="forbid"` runs the other way: it rejects keys the model does *not* have, which is why
    adding one is safe and removing one would not be.)"""

    resolved_argv: list[str] = Field(default_factory=list)
    """What was actually executed, after the launcher resolved `argv[0]` to an absolute binary.

    Forensics, and the identity check `JobStore._reap_orphan` needs: it is what a live process's own
    command line is compared against before anything signals it."""
    pid: int | None = None
    """The child's pid, written to disk **before** the process is waited on.

    `start_new_session=True` deliberately detaches the child from the parent's fate, which is what
    makes a group kill possible and also means a hard-killed server leaves a live `rcontrib` holding
    every core with nothing recorded that could find it. A record saying `failed` with a process
    still behind it is as wrong as one saying `running` with none, and it is worse in one way: a
    re-run then spawns a *second writer to the same artifact path*."""
    pid_started_at: datetime | None = None
    """When this server spawned that pid. A bare pid is ambiguous after wraparound, so recovery
    never acts on the pid alone -- see `JobStore._reap_orphan` for what it actually checks."""


class JobRecord(_Record):
    """Everything known about one run, durable on disk from before its id was returned."""

    job_id: str
    project: str
    project_root: str
    """Absolute. The `project` handle is a name (docs/tool-surface.md#handles-project-and-job_id)
    and a name alone cannot be reopened -- where projects live is still open (docs/plan.md, item 1),
    so the record stores the path it was submitted against rather than deriving one."""
    tool: str
    state: JobState
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None

    steps: list[StepRecord] = Field(default_factory=list)
    step_index: int = 0
    phase: str | None = None
    estimate_s: float | None = None

    artifacts: list[CommittedArtifact] = Field(default_factory=list)
    advisories: list[AdvisoryRecord] = Field(default_factory=list)
    result: dict[str, Any] | None = None

    returncode: int | None = None
    failed_phase: str | None = None
    cancelled_at_phase: str | None = None
    stderr_tail: list[str] = Field(default_factory=list)

    message: str | None = None
    """Why the supervisor ended the job the way it did, when the reason is **not** a Radiance stderr
    line -- a step that outran its bound, a server that stopped mid-run.

    A plain string rather than an advisory because the register allocates no code for "the
    supervisor ended this run", and codes are allocated once and never renamed
    (docs/tool-surface.md#advisory-code-register). Reusing `JOB_STDERR_WARNINGS`, whose own
    definition is *"the run exited 0 but wrote warnings"*, would emit a code whose meaning is untrue
    of the run. The gap is reported rather than filled by invention."""

    expired_at: datetime | None = None

    @property
    def wall_time_s(self) -> float | None:
        if self.started_at is None or self.finished_at is None:
            return None
        return (self.finished_at - self.started_at).total_seconds()

    def elapsed_s(self, *, now: datetime | None = None) -> float | None:
        if self.started_at is None:
            return None
        end = self.finished_at or (now or datetime.now(UTC))
        return (end - self.started_at).total_seconds()

    @property
    def progress(self) -> float | None:
        """Fraction of this job's **steps** that are done, or `None`.

        `None` for a single-step job: one opaque Radiance process reports nothing about how far
        through it is, and the spec makes the field nullable for exactly that. For a multi-step job
        the completed-step count is a fact rather than an estimate, so it is reported -- and it is
        deliberately not blended with elapsed time inside the current step, which would be a
        fabricated rate wearing a real number's clothes.

        **A skipped step counts as done** (`Step.reuse`, R65). It has no returncode -- nothing ran
        -- but the job is genuinely past it and its artifact is in the manifest, so excluding it
        would make a 3-phase run that reused V and D report a fraction that can never reach 1.0.
        Counting it is the same fact the rest of this property reports: how many of these steps the
        job no longer has ahead of it.
        """
        if len(self.steps) < 2:
            return None
        done = sum(
            1
            for s in self.steps
            if s.finished_at is not None and (s.skipped or s.returncode == 0)
        )
        return done / len(self.steps)


def status_payload(
    record: JobRecord,
    *,
    position: int | None = None,
    blocked_on: Sequence[str] = (),
    now: datetime | None = None,
) -> dict[str, Any]:
    """The state-dependent dict `job(action="status")` returns.

    The shape is state-dependent **by design** (docs/tool-surface.md#3-job): every state carries
    `job_id`, `project`, `tool`, `state` and `created_at`, then the fields that mean something in
    that state and no others. Built here rather than in `rad_mcp` so the `job` tool and the Tasks
    extension project the same record through the same function -- one store deserves one
    projection, or the two front doors can still disagree.

    `partial_artifacts` and `artifacts` are the same list under two names, which is what the table
    says: while a job runs or after it fails, what is committed is partial; when it succeeds, it is
    the result.
    """
    payload: dict[str, Any] = {
        "job_id": record.job_id,
        "project": record.project,
        "project_root": record.project_root,
        "tool": record.tool,
        "state": record.state,
        "created_at": record.created_at,
    }
    committed = [a.model_dump() for a in record.artifacts]
    advisories = [a.model_dump() for a in record.advisories]

    if record.state == "queued":
        payload["position"] = position
        payload["blocked_on"] = list(blocked_on)
    elif record.state == "running":
        payload.update(
            phase=record.phase,
            started_at=record.started_at,
            elapsed_s=record.elapsed_s(now=now),
            estimate_s=record.estimate_s,
            progress=record.progress,
            partial_artifacts=committed,
        )
    elif record.state == "succeeded":
        payload.update(
            finished_at=record.finished_at,
            wall_time_s=record.wall_time_s,
            artifacts=committed,
            result=record.result,
            advisories=advisories,
        )
    elif record.state == "failed":
        payload.update(
            finished_at=record.finished_at,
            failed_phase=record.failed_phase,
            returncode=record.returncode,
            stderr_tail=list(record.stderr_tail),
            advisories=advisories,
            partial_artifacts=committed,
            message=record.message,
        )
    elif record.state == "cancelled":
        payload.update(
            finished_at=record.finished_at,
            cancelled_at_phase=record.cancelled_at_phase,
            partial_artifacts=committed,
            message=record.message,
            # Not in the spec's table, and required by ruling R10: a cancelled job carries
            # `JOB_TERMINATED` at `error`, and an advisory a front door cannot read is an advisory
            # that was not emitted.
            advisories=advisories,
        )
    return payload


# --------------------------------------------------------------------------------------------
# Durable IO
# --------------------------------------------------------------------------------------------


def _atomic_write(path: Path, payload: str) -> None:
    """Write `payload` so a reader sees the whole document or the previous one, never a prefix.

    The rule `save_manifest` applies to the manifest, for the same reason: a half-written job record
    must never be readable as a whole one, and *"durably created before sending the response"* is
    not satisfied by a write still sitting in a buffer.
    """
    handle_fd, temp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}-", suffix=".tmp")
    temp_path = Path(temp_name)
    try:
        with os.fdopen(handle_fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp_path, 0o644)
        os.replace(temp_path, path)
    except BaseException:
        temp_path.unlink(missing_ok=True)
        raise
    _fsync_directory(path.parent)


def _fsync_directory(directory: Path) -> None:
    """Persist a directory entry -- the half `register_artifact` does not cover.

    `register_artifact` fsyncs the artifact's **bytes** before the manifest records it. Nothing
    fsyncs the directory entry that gives those bytes a name, so a power loss between the two can
    still leave a manifest referencing a file with no name. Best effort: Windows cannot open a
    directory for reading, and a failure here is not a reason to fail a completed build.
    """
    try:
        fd = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def _read_from(path: Path, offset: int) -> str:
    """The bytes one step appended to a shared log, without re-reading the whole job's stderr.

    Opened through the primitive (R48, adopted 2026-08-10) for the same reason the *write* side of
    these logs already is: this module opens both ends of a job's stderr, and guarding only the end
    that writes leaves the other one able to wedge the supervisor thread on a FIFO.
    """
    with open_regular_file(path, "rb") as handle:
        handle.seek(offset)
        return handle.read().decode("utf-8", errors="replace")


def _tail(path: Path, count: int) -> list[str]:
    # `open_regular_file`, not `read_text` -- R48's rule in the read direction.
    with open_regular_file(path, "rb") as handle:
        text = handle.read().decode("utf-8", errors="replace")
    return text.splitlines()[-count:]


#: The guard itself now lives in `rad_engine.safe_io`, because a Phase 4 security pass found the
#: identical hazard on `rad_engine.weather`'s *write* path -- a symlink-followed `write_text` that
#: overwrote a file outside every allowed root. Ruling R30 forbids the second copy, and this
#: particular logic guards an arbitrary-file overwrite, which is the worst thing to hold two
#: versions of. The name below is kept as an alias: this module's messages and tests speak in
#: *steps*, and `safe_io` is deliberately ignorant of what a step is.
StepStreamNotRegularFileError = NotARegularFileError

#: Read as "open a declared step stream". Kept as a module-level name rather than inlined at the two
#: call sites so the AST guard in check 0.2 -- which resolves spawner and stream names per module --
#: keeps seeing one symbol here.
_open_regular_file = open_regular_file


# --------------------------------------------------------------------------------------------
# Process supervision
# --------------------------------------------------------------------------------------------


def _spawn(
    argv: Sequence[str],
    *,
    cwd: Path,
    environ: Mapping[str, str],
    stdout: Any,
    stderr: Any,
    stdin: Any = subprocess.DEVNULL,
) -> subprocess.Popen[bytes]:
    """Start one process in its **own process group**, with every stream declared.

    `stdin=DEVNULL` is not tidiness, and it stays the **default** rather than becoming a choice. On
    stdio, stdin is the JSON-RPC request stream, and a binary that inherits it consumes client
    requests -- the same failure as inherited stdout, one file descriptor over. A caller may pass an
    open file handle instead (ruling R43: `rfluxmtx` takes sensor points only on stdin), and that is
    still an explicit declaration -- what is never passed is `None`, which inherits.
    `stdout` and `stderr` are always passed explicitly and never merged (CONTEXT.md).

    The new process group is what makes cancellation reach the **tree**: `oconv` runs a shell for
    every `!xform` include and `rad` spawns the ray tracer, so killing the direct child alone leaves
    work running that nobody is waiting for. See `_terminate_tree` for what that costs on Windows.
    """
    # Passed as named arguments rather than splatted from a `dict[str, Any]`: an `Any` splat erases
    # the `text`/`encoding` arguments from `Popen`'s overload resolution, which then infers
    # `Popen[str]` -- wrong, since every stream here is binary (`_run_step` opens its sinks "wb"/"ab").
    # `CREATE_NEW_PROCESS_GROUP` is a Windows-only attribute, so it stays inside the branch: a
    # ternary would evaluate it on POSIX too and raise AttributeError. 0 is the Windows default.
    creationflags = 0
    if os.name == "nt":  # pragma: no cover - no Windows here; see _terminate_tree
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP
    return subprocess.Popen(
        list(argv),
        cwd=str(cwd),
        env=dict(environ),
        stdin=stdin,
        stdout=stdout,
        stderr=stderr,
        start_new_session=os.name != "nt",
        creationflags=creationflags,
    )


def _pid_is_alive(pid: int) -> bool:
    """Whether `pid` names a live process. Signal 0 checks existence without delivering anything."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:  # pragma: no cover - exists, owned by someone else
        return True
    return True


def _command_line(pid: int) -> str | None:
    """One live process's own command line, or `None` if it cannot be read.

    Half of the identity check `_reap_orphan` makes, and the half the record itself supplies the
    other side of -- see `started_when_recorded` for the half it does not.
    """
    return _ps(pid, "args=")


#: How far a live process's own start time may sit from the `pid_started_at` a record claims before
#: recovery treats that pid as somebody else's.
#:
#: `ps -o etime=` resolves to one second and the record's timestamp is taken microseconds after the
#: spawn, so an honest gap is under a second. Ten seconds leaves room for a loaded machine without
#: leaving room for a coincidence: a pid that has been alive for hours cannot match a record written
#: minutes ago, which is the wraparound case, and an unrelated process picked out of a forged record
#: has no reason to have started when the record says it did.
_PID_START_TOLERANCE_S: Final = 10.0


def _ps(pid: int, field: str) -> str | None:
    """One field of one live process, read from `ps`, or `None` if it cannot be read.

    `ps` rather than a dependency: `psutil` is not in this project's dependency set, and adding one
    to identify a process would be a large decision taken for a small reason.
    """
    try:
        probe = subprocess.run(
            ["/bin/ps", "-o", field, "-p", str(pid)],
            capture_output=True,  # never inherit: stdout is the transport
            stdin=subprocess.DEVNULL,  # nor stdin: on stdio it is the request stream (CONTEXT.md)
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):  # pragma: no cover - no ps on this platform
        return None
    return probe.stdout.strip() or None


def parse_elapsed(text: str) -> float | None:
    """`ps -o etime=`'s `[[dd-]hh:]mm:ss` as seconds, or `None` if it is not that.

    That format is what POSIX specifies for `etime`, which is why it is the field read rather than
    `lstart` (a locale-dependent date) or `etimes` (procps only -- `ps: etimes: keyword not found`
    on this machine, 2026-08-01).
    """
    text = text.strip()
    days = 0
    if "-" in text:
        head, _, text = text.partition("-")
        try:
            days = int(head)
        except ValueError:
            return None
    parts = text.split(":")
    if not 2 <= len(parts) <= 3:
        return None
    try:
        numbers = [int(part) for part in parts]
    except ValueError:
        return None
    hours, minutes, seconds = ([0] + numbers) if len(numbers) == 2 else numbers
    return days * 86400.0 + hours * 3600.0 + minutes * 60.0 + seconds


def started_when_recorded(pid: int, recorded: datetime | None, *, now: datetime | None = None) -> bool:
    """Whether `pid`'s **own** start time agrees with the `pid_started_at` a record claims.

    The corroboration `is_same_process` cannot provide. That function compares the live command line
    against `resolved_argv`, and both of those come out of the same document -- so a job record is
    able to state both sides of its own identity check, which is how a forged `10_jobs/*/job.json`
    got an unrelated `sleep` killed at server startup (reproduced 2026-08-01, returncode -15 before
    the first request). A process's start time is read from the kernel and is not in the record's
    gift; agreeing with it is evidence the record did not manufacture.

    `False` whenever it cannot be established -- no recorded time, no readable `etime`, or a drift
    past `_PID_START_TOLERANCE_S`. The asymmetry is `_reap_orphan`'s: failing to reap leaves a
    process we then report, signalling the wrong pid kills a stranger's work.
    """
    if recorded is None:
        return False
    elapsed = _ps(pid, "etime=")
    if elapsed is None:
        return False
    uptime = parse_elapsed(elapsed)
    if uptime is None:  # pragma: no cover - a platform whose ps does not speak POSIX etime
        return False
    # A record written by hand may carry a naive datetime; read it as UTC rather than raising.
    if recorded.tzinfo is None:  # pragma: no cover - defensive
        recorded = recorded.replace(tzinfo=UTC)
    started = (now or datetime.now(UTC)) - timedelta(seconds=uptime)
    return abs((started - recorded).total_seconds()) <= _PID_START_TOLERANCE_S


def is_same_process(recorded_argv: Sequence[str], running_argv: str) -> bool:
    """Whether a live process's command line is the one a job record says it spawned.

    **Deliberately conservative, because the two mistakes are not symmetric.** Failing to reap an
    orphan leaves a process we then report; signalling the wrong pid kills a stranger's work. So
    this returns `False` whenever it cannot be sure, and `_reap_orphan` reports instead of killing.

    Matching is on the **basename** of `argv[0]` plus **every** remaining argument, not on the
    absolute `argv[0]`. `ps` reports the binary the kernel actually executed, which is not always
    the path we passed -- a venv Python shows as the framework binary it re-execs, verified on this
    machine 2026-07-31. Radiance binaries do not do that, but a rule that happens to hold for one
    family of programs is not a rule. The remaining arguments carry the discrimination anyway: a
    real step names absolute project paths, and those are what make a pid recognisably *this* run's.

    An argv with nothing after `argv[0]` is treated as unidentifiable rather than as a basename
    match, for the same asymmetry.
    """
    if len(recorded_argv) < 2:
        return False
    if Path(recorded_argv[0]).name not in running_argv:
        return False
    return all(token in running_argv for token in recorded_argv[1:])


def _terminate_tree(proc: subprocess.Popen[bytes], *, grace_s: float) -> None:
    """Kill the process **and everything it started**: SIGTERM to the group, then SIGKILL.

    **This is the platform-specific part of the async spine, and it is not symmetric.** On POSIX the
    child is a session and process-group leader (`start_new_session=True`), so its pgid equals its
    pid and `killpg` reaches every descendant that has not deliberately left the group. That is the
    documented fallback in docs/plan.md, Phase 1: *"a subprocess-group approach with a documented
    Windows limitation rather than pretend it is portable."*

    On Windows there is no process group with POSIX semantics. `CREATE_NEW_PROCESS_GROUP` plus
    `CTRL_BREAK_EVENT` reaches console children only, so the tree kill goes through `taskkill /T /F`
    where it exists and degrades to killing the direct child where it does not. **That degraded path
    can leave a grandchild running.** Closing it properly needs a Windows Job Object, which needs
    `pywin32` and a Windows machine to verify on; neither is available here, so the limitation is
    stated rather than claimed away.

    **The group is swept even when the leader has already exited** (R757): a step that left a
    grandchild in its group would otherwise outlive a cancel, timeout or shutdown that arrived
    after the step's own process ended.
    """
    if os.name == "nt":  # pragma: no cover - see the docstring
        if proc.poll() is not None:
            return
        killer = shutil.which("taskkill")
        if killer is not None:
            subprocess.run(
                [killer, "/T", "/F", "/PID", str(proc.pid)],
                capture_output=True,  # never inherit: stdout is the transport
                stdin=subprocess.DEVNULL,  # nor stdin: on stdio it is the request stream
                timeout=grace_s + 5.0,
                check=False,
            )
        else:
            proc.kill()
        try:
            proc.wait(timeout=grace_s + 5.0)
        except subprocess.TimeoutExpired:
            _log.warning("job process %s did not exit after taskkill", proc.pid)
        return

    # R757: the group is killed **whether or not the leader has exited**. This returned early on
    # `proc.poll() is not None` until Phase 18, which left a grandchild running in the step's
    # group -- the case `rad_engine.process_group._kill_tree` already handled for inline spawns
    # (R750). `poll()` first reaps an exited leader, so an empty group answers `ProcessLookupError`
    # rather than macOS's `EPERM` for a zombie-only group (Phase 17 design P8).
    # pgid == pid because the child was made a session leader. Reading it back with `os.getpgid`
    # would race the reaper: once the process has been waited on its pid is gone and getpgid raises.
    proc.poll()
    kill_group(proc.pid, proc=proc, grace_s=grace_s)


# --------------------------------------------------------------------------------------------
# The store
# --------------------------------------------------------------------------------------------


def _changed_input(input_digests: Sequence[tuple[str, str]]) -> str | None:
    """The first path in `input_digests` whose bytes no longer hash to the recorded digest, or that
    can no longer be read; `None` when every one matches (ruling R822, extended). One hash per file
    per step, before the spawn."""
    for path, recorded in input_digests:
        try:
            if octree_body_digest(path) != recorded:
                return path
        except OSError:
            return path
    return None


class JobStore:
    """The one record for every run, and the FIFO queue that executes them.

    **One store, two front doors.** `rad_mcp`'s `job` tool and the Tasks extension both call this
    object and neither keeps state of its own. What a second front door needs is exactly `submit`,
    `record`, `status`, `list_jobs`, `wait`, `cancel`, `logs`, `recover` and `purge_expired` --
    nothing else reads or writes a job.

    **Concurrency 1.** One worker thread runs one job at a time and it gets the machine
    (docs/tool-surface.md#concurrency). The lock is held for record mutations and never across a
    `proc.wait()`, which is what lets `status` on a queued job answer while a matrix build saturates
    the rest of the cores -- check 1.9.
    """

    def __init__(
        self,
        *,
        launcher: Launcher,
        retention_s: float | None = None,
        kill_grace_s: float = DEFAULT_KILL_GRACE_S,
    ) -> None:
        """`retention_s` has **no default**.

        How long a job record survives is a policy the `job` tool has to state in its description --
        *"the server's retention policy should be stated in the creation tool's description"* -- and
        check 1.11 asserts the description and the configured value cannot drift. Inventing a number
        here would settle that by omission, in a place the description cannot see. `None` keeps
        records until something calls `purge_expired`.
        """
        self._launcher = launcher
        self._retention_s = retention_s
        self._kill_grace_s = kill_grace_s

        self._lock = threading.RLock()
        self._changed = threading.Condition(self._lock)
        self._records: dict[str, JobRecord] = {}
        self._steps: dict[str, tuple[Step, ...]] = {}
        """The submitted `Step` objects, which hold what the durable record cannot: the artifact
        spec's `built_from` tuple and the step's timeout. Live state only -- a job recovered from
        disk has none, and `recover` never restarts one."""
        self._results: dict[str, Callable[[JobRecord], dict[str, Any]]] = {}
        self._pending: deque[str] = deque()
        self._reserved = 0
        """Queue slots held by a `submit` between its bound check and its append (R756), so two
        submits racing for the last slot cannot both pass the check."""
        self._running_id: str | None = None
        self._cancelled: set[str] = set()
        self._abandoned: set[str] = set()
        """Jobs whose process was killed by `shutdown`, **not** by a client.

        Kept apart from `_cancelled` because the two mean different things to whoever reads the
        record afterwards, and conflating them is how `shutdown` came to record `cancelled` for a
        job nobody cancelled -- contradicting `recover`, which argues the opposite for the same
        event. One event, one state: `failed`."""
        self._signalled: set[str] = set()
        """Jobs whose step's process group was sent a kill (R775), so an end reason can say whether
        one was. Marked, before the kill, wherever `_terminate_tree` is called on a step: `shutdown`
        and `cancel` whenever they hold the step's process, `_execute`'s post-spawn kill and its
        timeout kill. Cleared before each step's spawn, so a mark is always about the current
        step's group. No `poll()` decides it: `_terminate_tree` sweeps the group even after the
        leader has exited (R757), so a step whose process is held is sent a kill either way. A job
        not in this set had no process held when it was stopped, and none was signalled."""
        #: One occlusion-probe verdict per matrix of a job, keyed `(job_id, artifact_id)`. Until
        #: R858 every `asemx` of a job carried the same probe and one verdict per job sufficed
        #: (R848); since R858 a per-group probe also closes its own group, so two groups' matrices
        #: in one job can differ -- one shaded by massing, one lit -- and a per-job verdict handed
        #: the first group's seal to the second. Dropped when the job ends (`_finish`).
        self._probe_verdicts: dict[tuple[str, str], ProbeVerdict] = {}
        # The children a running job's ASE occlusion probe has started (`_sun_reaches_grid`), so
        # `cancel` and `shutdown` can stop them: the probe runs in the commit, after the step, with
        # no step process for `_terminate_tree` to reach (final review of 2026-10-05).
        self._probe_collectors: dict[str, ChildCollector] = {}
        self._process: subprocess.Popen[bytes] | None = None
        self._worker: threading.Thread | None = None
        self._stopping = False
        self._manifest_lock = threading.RLock()

    @property
    def retention_s(self) -> float | None:
        """Read by `rad_mcp` so the retention stated in the tool description is this number and not
        a second one written by hand (check 1.11)."""
        return self._retention_s

    @property
    def manifest_lock(self) -> threading.RLock:
        """Serialises read-modify-write of a project manifest against the worker thread.

        `save_manifest` is a blind whole-document write with no compare-against-disk, so a manifest
        read at t0 and written back at t1 silently erases everything written in between. The worker
        re-reads the manifest immediately before every commit, which shrinks that window from the
        length of a 5-phase run to the length of one `register_artifact` -- but shrinking a race is
        not closing one.

        **Front doors must hold this around `register_input`, `rescan_inputs` and `update_project`**
        (and around any other open-modify-save pair) whenever a job may be running. Concurrency is 1
        for *jobs*; tool calls are not serialised against the worker, so without it a
        `project(action="update")` issued mid-build is erased by the build's next step commit.
        """
        return self._manifest_lock

    # -- submission ---------------------------------------------------------------------------

    def submit(
        self,
        project: LoadedProject,
        *,
        tool: str,
        steps: Sequence[Step],
        result_from: Callable[[JobRecord], dict[str, Any]] | None = None,
        job_id: str | None = None,
        now: datetime | None = None,
    ) -> JobRecord:
        """Create a job, make it durable, then queue it. Returns the record, already on disk.

        The order is the requirement: *"the task must be durably created before sending the
        response."* The record reaches disk with an fsync before this returns and before the worker
        can touch it, so an id a client holds is an id that survives a restart.

        **Edges are checked here where they can be**, before anything is queued, let alone spawned.
        A step whose `built_from` names only ids the manifest already has is checked now, so a wrong
        edge set is refused immediately rather than after an hour of matrix building. A step naming
        an artifact an *earlier step of this same job* will build cannot be checked yet -- that edge
        does not exist until the job runs -- so it is checked immediately before it spawns instead.
        Both paths raise `EdgesIncompleteError`, carrying `ARTIFACT_EDGES_INCOMPLETE` at `error`.
        """
        if not steps:
            raise JobError("a job with no steps has nothing to supervise")

        created = now or datetime.now(UTC)
        job_id = job_id or new_job_id(now=created)
        job_dir = project.paths.job_dir(job_id)  # validates the id as a path component

        planned = {s.artifact.artifact_id for s in steps if s.artifact is not None}
        for step in steps:
            spec = step.artifact
            if spec is None:
                continue
            if any(edge in planned for edge in spec.built_from):
                continue  # checked at run time, once the earlier step has committed it
            check_edges(project, spec.kind, list(spec.built_from))

        estimates = [s.estimate_s for s in steps]
        record = JobRecord(
            job_id=job_id,
            project=project.manifest.project.name,
            project_root=str(project.paths.root.resolve()),
            tool=tool,
            state="queued",
            created_at=created,
            # A partial sum reported as a total is an under-estimate that reads as fact, so one step
            # without an estimate makes the whole job's estimate null -- the rule
            # `ReuseSummary.rebuild_estimate_s` already follows.
            estimate_s=(
                None
                if any(e is None for e in estimates)
                else sum(e for e in estimates if e is not None)
            ),
            steps=[
                StepRecord(
                    phase=s.phase,
                    argv=list(s.argv),
                    cwd=str(Path(s.cwd).resolve()),
                    stdout_path=str(Path(s.stdout_path).resolve()) if s.stdout_path else None,
                    stdin_path=str(Path(s.stdin_path).resolve()) if s.stdin_path else None,
                    artifact_id=s.artifact.artifact_id if s.artifact else None,
                    estimate_s=s.estimate_s,
                )
                for s in steps
            ],
        )
        if record.estimate_s is not None:
            record.advisories.append(
                AdvisoryRecord(
                    code="RUNTIME_ESTIMATE_UNCALIBRATED",
                    severity="info",
                    message=(
                        f"The {record.estimate_s:.0f} s estimate is the caller's declared figure, "
                        "not a calibrated one. Every runtime estimate in this project is "
                        "uncalibrated until the estimator is fitted to recorded wall_time_s "
                        "(docs/tool-surface.md#unsourced-values)."
                    ),
                )
            )

        # R756: the queue bound, checked before anything reaches disk. The slot is reserved under the
        # lock and released when the job is appended (or the write fails), so two submits racing
        # for the last slot cannot both pass.
        with self._lock:
            waiting = len(self._pending) + self._reserved
            if waiting >= MAX_QUEUED_JOBS:
                raise QueueFullError(
                    f"{waiting} jobs are already waiting in the queue, which holds at most "
                    f"{MAX_QUEUED_JOBS} (a running job does not count), so this job was not created "
                    "and nothing was written. Cancel a queued job with job(action=\"cancel\"), or "
                    "wait for one to start, then submit again."
                )
            self._reserved += 1
        reserved = True
        try:
            job_dir.mkdir(parents=True, exist_ok=True)
            # Both logs exist from the moment the job does, and they are two files. A step that
            # writes its payload to an artifact still leaves an empty `stdout` beside a populated
            # `stderr`, which is the shape check 1.5 asserts.
            (job_dir / STDOUT_NAME).touch()
            (job_dir / STDERR_NAME).touch()
            self._write(record)

            with self._lock:
                self._reserved -= 1
                reserved = False
                self._records[job_id] = record
                self._steps[job_id] = tuple(steps)
                if result_from is not None:
                    self._results[job_id] = result_from
                self._pending.append(job_id)
                self._ensure_worker()
                self._changed.notify_all()
        finally:
            if reserved:
                with self._lock:
                    self._reserved -= 1
        return record

    # -- reading ------------------------------------------------------------------------------

    def record(self, job_id: str) -> JobRecord:
        """The raw record.

        Raises rather than returning `None`: an unknown handle is a refusal with a code, not an
        absent value every caller has to re-interpret the same way.
        """
        with self._lock:
            found = self._records.get(job_id)
            if found is None:
                raise JobNotFoundError(
                    f"no job {job_id!r}. Job ids are returned by run_annual, the only tool that "
                    "submits a job (an 'inline-...' job_id from another tool names no job); a "
                    "project this server has not opened since it started is not searched for one."
                )
            if found.expired_at is not None:
                raise JobRecordExpiredError(
                    f"job {job_id!r} finished at {found.finished_at} and its record was purged. "
                    "The id was valid; the evidence is gone. Re-run the work for a new id."
                )
            return found

    def status(self, job_id: str, *, now: datetime | None = None) -> dict[str, Any]:
        """The state-dependent payload for one job, with `position` and `blocked_on` filled in."""
        with self._lock:
            found = self.record(job_id)
            position, blocked_on = self._queue_view(job_id)
            return status_payload(found, position=position, blocked_on=blocked_on, now=now)

    def list_jobs(self, *, project: str | None = None) -> list[dict[str, Any]]:
        """`action="list"`: one row per job in creation order, optionally filtered by project name.

        Expired records are listed rather than hidden. A handle that vanishes from the list reads as
        one that never existed, which is the distinction `JOB_RECORD_EXPIRED` exists to keep.
        """
        with self._lock:
            rows: list[dict[str, Any]] = []
            for job_id in sorted(self._records):
                found = self._records[job_id]
                if project is not None and found.project != project:
                    continue
                position, _ = self._queue_view(job_id)
                rows.append(
                    {
                        "job_id": found.job_id,
                        "project": found.project,
                        "tool": found.tool,
                        "state": found.state,
                        "created_at": found.created_at,
                        "position": position,
                        "expired": found.expired_at is not None,
                    }
                )
            return rows

    def logs(
        self,
        job_id: str,
        *,
        stream: Literal["stdout", "stderr", "both"] = "stderr",
        tail: int = 50,
    ) -> dict[str, Any]:
        """`action="logs"`. `stderr` is the default because that is where Radiance explains itself.

        `both` returns the two streams **labelled and in that order**, never interleaved: the point
        of keeping them apart on disk is lost the moment they are spliced back together on
        timestamps nobody recorded.
        """
        found = self.record(job_id)
        job_dir = ProjectPaths(Path(found.project_root)).job_dir(job_id)
        names = (STDOUT_NAME, STDERR_NAME) if stream == "both" else (stream,)

        lines: list[str] = []
        truncated = False
        for name in names:
            try:
                # `open_regular_file` (R48, adopted 2026-08-10). Already inside a `try` that
                # catches `OSError`, and `NotARegularFileError` is one -- so a FIFO planted at a
                # job's `stderr` now reports as unavailable logs on the existing path instead of
                # wedging the tool thread. No handler needed changing.
                with open_regular_file(job_dir / name, "rb") as handle:
                    raw = handle.read().decode("utf-8", errors="replace").splitlines()
            except OSError as exc:
                # The record is live -- `record()` above answered -- and its evidence is not on
                # disk. Reachable with no race at all: `project(action="delete", force=True)` takes
                # `10_jobs/` with it and leaves the in-memory record answering `status` normally
                # forever. A refusal with an allocated code, not a bare exception with a path in it.
                raise JobLogsUnavailableError(
                    f"job {job_id!r} is still on record, but its {name} log is no longer readable "
                    f"at {job_dir / name}: {exc.strerror or exc}. This is not an expired record and "
                    "not an unknown id -- the captured output is gone while the job is not, which "
                    "is what happens when the project directory is deleted underneath it. "
                    "action='status' and action='list' still answer, and any artifact this job "
                    "committed is in the project manifest."
                ) from exc
            kept = raw[-tail:] if tail >= 0 else raw
            truncated = truncated or len(kept) < len(raw)
            if stream == "both":
                lines.append(f"--- {name} ---")
            lines.extend(kept)
        return {"job_id": job_id, "stream": stream, "lines": lines, "truncated": truncated}

    def wait(self, job_id: str, *, timeout_s: float | None = None) -> dict[str, Any]:
        """Block until the job is terminal or `timeout_s` elapses, then return its status.

        `timeout_s=None` returns immediately with the current state, which is what the tool
        signature means by *"None = return immediately"*. A bound above the server maximum is
        refused by `rad_mcp`, not here: *"a tool call that blocks forever is indistinguishable from
        a hung server"* is a wire-contract rule and the engine has no wire.
        """
        deadline = None if timeout_s is None else time.monotonic() + timeout_s
        with self._changed:
            while True:
                found = self.record(job_id)
                if found.state in TERMINAL_STATES or deadline is None:
                    return self.status(job_id)
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return self.status(job_id)
                self._changed.wait(remaining)

    # -- cancellation -------------------------------------------------------------------------

    def cancel(self, job_id: str) -> tuple[dict[str, Any], list[EngineAdvisory]]:
        """Cancel a job. Returns its status and any advisory the cancellation itself produced.

        A queued job is cancelled in place and never runs. A running job has its **process tree**
        killed -- see `_terminate_tree`, including what that costs on Windows. A job that has
        already finished is not an error: *"cancellation is cooperative"*, the race is real, and
        `JOB_CANCEL_LATE` at `info` says which side of it the call landed on.
        """
        with self._lock:
            found = self.record(job_id)
            if found.state in TERMINAL_STATES:
                return status_payload(found), [
                    EngineAdvisory(
                        code="JOB_CANCEL_LATE",
                        severity="info",
                        message=(
                            f"job {job_id!r} was already {found.state} when the cancel arrived; "
                            "nothing was stopped."
                        ),
                        lines=(),
                    )
                ]
            self._cancelled.add(job_id)
            if found.state == "queued":
                self._pending.remove(job_id)
                self._end(
                    found,
                    "cancelled",
                    reason="cancelled while queued; it never started.",
                    cancelled_at_phase=None,
                )
                return status_payload(found), []
            process = self._process if self._running_id == job_id else None
            if process is not None:
                self._signalled.add(job_id)
            probe = self._probe_collectors.get(job_id)

        # Outside the lock: killing a tree waits on the process, and holding the lock across that
        # would block `status` on the queued job behind it for the whole grace window -- the exact
        # responsiveness check 1.9 asserts.
        if process is not None:
            _terminate_tree(process, grace_s=self._kill_grace_s)
        self._stop_probe(probe)
        with self._changed:
            self._changed.wait_for(
                lambda: self._records[job_id].state in TERMINAL_STATES, timeout=10.0
            )
            return self.status(job_id), []

    # -- retention ----------------------------------------------------------------------------

    def purge_expired(self, *, now: datetime | None = None) -> list[str]:
        """Delete the job directories of terminal jobs older than `retention_s`; return their ids.

        A purge leaves a tombstone rather than nothing, so the id keeps answering
        `JOB_RECORD_EXPIRED` instead of degrading to `JOB_NOT_FOUND`. **Committed artifacts are
        untouched** -- they live in the manifest, and expiring the evidence of a run must never
        expire the matrices that run built.

        **One bad record must not take the server with it.** Every `job` call enters through here
        and so does `Runtime.startup`, so anything this raises is raised before `mcp.run()` -- a
        recovered record whose id `job_dir` refuses put `ProjectNameError` out of `startup`, out of
        `main`, and made the server permanently unstartable, repeated at every restart and
        reproduced at the production retention of 604 800 s. `_load` already states the discipline
        this now shares: *"one corrupt job directory must not make an entire project unopenable."*
        A victim that cannot be expired is logged and skipped, and its `expired_at` is set **after**
        its tombstone is on disk -- marking first and then raising left a record the store believed
        expired with nothing on disk to say so.

        **The IO happens outside the lock.** Removing a job tree and fsyncing a tombstone are slow,
        every `job` call passes through here, and holding the store lock across them blocks
        `status` on the running job -- the responsiveness property check 1.9 exists to protect. The
        lock picks the victims; the disk work is done without it.
        """
        if self._retention_s is None:
            return []
        moment = now or datetime.now(UTC)
        cutoff = moment.timestamp() - self._retention_s
        with self._lock:
            victims = [
                found
                for found in self._records.values()
                if found.state in TERMINAL_STATES
                and found.expired_at is None
                and found.finished_at is not None
                and found.finished_at.timestamp() <= cutoff
            ]

        purged: list[str] = []
        for found in victims:
            paths = ProjectPaths(Path(found.project_root))
            try:
                job_dir = paths.job_dir(found.job_id)
            except ProjectNameError as exc:
                # `recover` refuses such a record now, so this is a record submitted in-process with
                # an id no longer legal -- unreachable today and kept because the consequence of
                # being wrong about that is a server that cannot start.
                _log.warning("cannot expire job %r: %s", found.job_id, exc)
                continue
            if not paths.jobs.is_dir():
                # The project directory went with a `project(action="delete", force=True)`. There is
                # nothing to remove and nowhere to leave a tombstone, and recreating `10_jobs/` would
                # resurrect a tree a client deliberately deleted. The record still expires, which is
                # what the retention policy is about.
                with self._lock:
                    found.expired_at = moment
                purged.append(found.job_id)
                continue
            # The tombstone has to *say* expired -- it is what makes the id keep answering
            # `JOB_RECORD_EXPIRED` after a restart -- while the live record is only marked once that
            # document is on disk. A copy is what lets both be true: mark-then-write left a record
            # the store believed expired with nothing on disk to agree, every time the write failed.
            sealed = found.model_copy(deep=True)
            sealed.expired_at = moment
            try:
                shutil.rmtree(job_dir, ignore_errors=True)
                _atomic_write(
                    paths.jobs / f"{found.job_id}{TOMBSTONE_SUFFIX}",
                    sealed.model_dump_json(indent=2),
                )
            except OSError as exc:
                _log.warning("could not expire job %r: %s", found.job_id, exc)
                continue
            with self._lock:
                found.expired_at = moment
            purged.append(found.job_id)
        return purged

    # -- recovery -----------------------------------------------------------------------------

    def recover(self, project_root: Path | str) -> list[str]:
        """Read back every job record under `<project_root>/10_jobs/`; return the ids adopted.

        This is what makes a job id survive a restart, and it is the call a front door has to make:
        `rad_mcp` recovers a project when it opens one, so a `job(job_id=...)` naming a project this
        process has never opened answers `JOB_NOT_FOUND` rather than searching the disk for a
        directory it was never told about.

        **A record left `queued` or `running` by a dead server is reconciled to `failed`.** It is
        not resumed: auto-resuming work a client may have given up on is a policy no document here
        takes, and taking it by default would spawn an hour of matrix building as a side effect of
        opening a project. It is not `cancelled` either -- nobody cancelled it. Its partial
        artifacts are already committed in the manifest and stay reusable, which is the whole point
        of committing per step.

        **And the process may still be alive.** `start_new_session=True` detaches the child from the
        parent's fate on purpose, so a SIGKILLed server leaves its `rcontrib` running, holding every
        core and still writing into an artifact path -- and a re-run would then put a *second* writer
        on the same file. `_reap_orphan` kills it before the record is reconciled.

        **Everything under `10_jobs/` is untrusted input.** These documents are not this process's
        memory: they are files on disk, in a directory a user can write to, parsed straight into a
        model whose fields then reach `os.killpg` and `shutil.rmtree`. A record used to be able to
        name any `job_id`, any `project_root` and any pid, and recovery believed all three -- an
        unrelated `sleep` in its own session died with returncode -15 at `Runtime.startup()`, before
        the first request (reproduced 2026-08-01). So `_load` is given the directory the record was
        actually found in and the root actually being recovered, and reconciles the document against
        both before it is adopted; nothing downstream re-derives a path from a field the file
        supplied.
        """
        paths = ProjectPaths(Path(project_root).resolve())
        if not paths.jobs.is_dir():
            return []

        adopted: list[str] = []
        for entry in sorted(paths.jobs.iterdir()):
            if entry.is_file() and entry.name.endswith(TOMBSTONE_SUFFIX):
                found = self._load(
                    entry, job_id=entry.name[: -len(TOMBSTONE_SUFFIX)], root=paths.root
                )
            elif entry.is_dir() and (entry / RECORD_NAME).is_file():
                found = self._load(entry / RECORD_NAME, job_id=entry.name, root=paths.root)
            else:
                continue
            if found is None:
                continue
            with self._lock:
                if found.job_id in self._records:
                    continue  # a job this process knows about is newer than anything on disk
                if found.expired_at is None and found.state in ("queued", "running"):
                    reaped = self._reap_orphan(found)
                    found.message = (
                        f"the server stopped while this job was {found.state}. It is not resumed; "
                        "artifacts committed before the stop are in the manifest and remain "
                        "reusable." + (f" {reaped}" if reaped else "")
                    )
                    self._records[found.job_id] = found
                    self._end(
                        found,
                        "failed",
                        reason=found.message,
                        failed_phase=found.phase,
                        returncode=None,
                    )
                else:
                    self._records[found.job_id] = found
                adopted.append(found.job_id)
        return adopted

    def _reap_orphan(self, record: JobRecord) -> str | None:
        """Kill a process this record's job left running, if it is still that process. POSIX only.

        **The pid alone is never enough.** Pids wrap, and the gap between a server dying and a
        client reopening the project can be days, so signalling a recorded pid on trust is
        signalling whatever inherited that number.

        **And neither is the record alone.** The command-line check is the live process's own argv
        against `StepRecord.resolved_argv`, which for any real job contains the absolute project
        paths this run was given -- a strong discriminator, and the code that performs it
        (`is_same_process`) is conservative and correct. What was wrong was the *provenance of its
        inputs*: the pid and the argv it is compared against come out of the same `job.json`, so a
        document was able to supply both sides of its own identity check and have the answer come
        back "yes". An unrelated `sleep 9999` in its own session was killed with returncode -15 at
        `Runtime.startup()`, before the first request (reproduced 2026-08-01).

        So identity is now **two** things that must both hold, one of them outside the record's
        gift: the argv match above, and `started_when_recorded` -- the kernel's own start time for
        that pid, against the `pid_started_at` the record claims. A forged record can name any pid
        and any argv; it cannot make a stranger's process have started when it says it did.
        `_load` is the other half of this, and this method is only ever reached through it.

        Returns a sentence for the record's `message`, or `None` when there was nothing to reap.
        On Windows this does nothing and says so: see `_terminate_tree` for why the tree kill there
        is not equivalent.
        """
        step = next(
            (
                s
                for s in reversed(record.steps)
                if s.pid is not None and s.finished_at is None
            ),
            None,
        )
        if step is None or step.pid is None:
            return None
        if os.name == "nt":  # pragma: no cover - see _terminate_tree
            return (
                f"A process (pid {step.pid}) may still be running from this job; this platform "
                "cannot verify or reap it."
            )
        if not _pid_is_alive(step.pid):
            return None

        running_argv = _command_line(step.pid)
        if running_argv is None or not is_same_process(step.resolved_argv, running_argv):
            # Alive, but not identifiably ours. Killing it would risk killing a stranger, and that
            # is the worse of the two mistakes -- so it is reported, not signalled.
            return (
                f"pid {step.pid} is alive but its command line is not identifiably this job's; it "
                f"was recorded at {step.pid_started_at} and the pid may since have been reused. "
                "Nothing was signalled."
            )
        if not started_when_recorded(step.pid, step.pid_started_at):
            # The argv matched -- and both sides of that comparison came out of this record. The
            # kernel's own start time for the pid did not, and it disagrees.
            return (
                f"pid {step.pid} runs a matching command line, but the process has not been alive "
                f"since {step.pid_started_at}, which is when this record says this job spawned it. "
                "The record cannot corroborate itself, so nothing was signalled. Check the process "
                "by hand before re-running, or two processes may write the same artifact."
            )

        if not kill_group(step.pid, grace_s=self._kill_grace_s):  # pragma: no cover - EPERM only
            return (
                f"Its process (pid {step.pid}, spawned {step.pid_started_at}) is still running and "
                "could not be signalled. Kill it before re-running, or two processes will write the "
                "same artifact."
            )
        return (
            f"Its process (pid {step.pid}, spawned {step.pid_started_at}) was still running and has "
            "been killed, so a re-run cannot end up with two writers on the same artifact."
        )

    def _load(self, path: Path, *, job_id: str, root: Path) -> JobRecord | None:
        """Parse one on-disk record **as untrusted input**, or report and skip it.

        Three reconciliations, and each one closes a field the document could otherwise choose:

        - the id must be a legal path component. `ProjectPaths.job_dir` refuses `"../../../../etc"`
          by raising, and the raise is the problem -- it propagated out of `purge_expired`, out of
          `Runtime.startup` and out of `main`, so one planted file made the server permanently
          unstartable. Refusing the *record* keeps the refusal local;
        - the id must equal the directory (or tombstone) the record was found in, so a document
          cannot answer for a job that lives somewhere else;
        - `project_root` is **overwritten** with the root actually being recovered, never read from
          the file. It is what `logs` reads a file through and what `purge_expired` removes a tree
          from, and a field that chooses its own rmtree target is not a field, it is an argument.

        A record we cannot parse is reported and skipped rather than raised: one corrupt job
        directory must not make an entire project unopenable. The same is now true of one that
        parses and lies.
        """
        try:
            # `open_regular_file` (R48, adopted 2026-08-10). The `except` below already catches
            # `OSError`, of which `NotARegularFileError` is one, so a job record that is a FIFO is
            # logged and skipped exactly like an unparseable one -- which is this function's whole
            # posture: *one corrupt job directory must not make an entire project unopenable*.
            #
            # Bytes are handed straight to `model_validate_json`, which accepts them, rather than
            # decoded here first: `read_text` decoded strictly and a `UnicodeDecodeError` is a
            # `ValueError`, so it was already caught below -- passing bytes keeps that behaviour
            # without this line having to restate the encoding.
            with open_regular_file(path, "rb") as handle:
                found = JobRecord.model_validate_json(handle.read())
        except (OSError, ValidationError, ValueError) as exc:
            _log.warning("ignoring unreadable job record %s: %s", path, exc)
            return None

        try:
            ProjectPaths(root).job_dir(found.job_id)  # validates the id as a path component
        except ProjectNameError as exc:
            _log.warning("ignoring job record %s: %s", path, exc)
            return None
        if found.job_id != job_id:
            _log.warning(
                "ignoring job record %s: it calls itself %r but was found under %r",
                path,
                found.job_id,
                job_id,
            )
            return None

        found.project_root = str(root)
        return found

    # -- shutdown -----------------------------------------------------------------------------

    def shutdown(self, *, cancel_running: bool = True, timeout_s: float = 15.0) -> None:
        """Stop the worker; with `cancel_running`, kill the live process tree first.

        Without the kill, stopping the server leaves an `rcontrib` holding every core with nothing
        left that could report on it or stop it.

        **The job it kills is recorded `failed`, not `cancelled`.** A graceful shutdown is the same
        event `recover` reasons about -- the server stopped -- and `recover` is right that nobody
        cancelled it. `cancelled` is the state a client's `cancel` produces and nothing else; making
        shutdown produce it too would mean a user reading `cancelled` could not tell whether their
        cancel had landed or the server had simply gone away.
        """
        with self._lock:
            self._stopping = True
            running = self._running_id
            process = self._process
            worker = self._worker
            # R775 (owner decision E4): the mark and the process read are one lock, so a worker
            # between its pre-spawn check and its spawn either sees the mark after spawning (and
            # kills the step itself) or has already published the process read here.
            if cancel_running and running is not None:
                # Marked whether or not a process is up (ruling R755, final review): a job between
                # steps, or inside the pre-spawn scan, has none yet, and `_run` and `_execute` read
                # this mark to refuse or kill the spawn that would otherwise follow the stop.
                self._abandoned.add(running)
                if process is not None:
                    self._signalled.add(running)
            probe = self._probe_collectors.get(running) if cancel_running and running is not None else None
            self._changed.notify_all()
        if cancel_running and process is not None:
            _terminate_tree(process, grace_s=self._kill_grace_s)
        self._stop_probe(probe)
        if worker is not None:
            worker.join(timeout=timeout_s)

    # -- internals ----------------------------------------------------------------------------

    def _write(self, record: JobRecord) -> None:
        job_dir = ProjectPaths(Path(record.project_root)).job_dir(record.job_id)
        job_dir.mkdir(parents=True, exist_ok=True)
        _atomic_write(job_dir / RECORD_NAME, record.model_dump_json(indent=2))

    def _ensure_worker(self) -> None:
        if self._worker is not None and self._worker.is_alive():
            return
        self._stopping = False
        self._worker = threading.Thread(target=self._work, name="rad-jobs", daemon=True)
        self._worker.start()

    def _queue_view(self, job_id: str) -> tuple[int | None, list[str]]:
        """`position` and `blocked_on` for a queued job; `(None, [])` for anything else.

        `position` counts the running job as 0, so the first job waiting behind it reports 1 -- the
        number check 1.9 asserts. `blocked_on` is the artifact ids this job would build that a job
        ahead of it is already building: the honest answer to "why is nothing happening" when the
        wait is a data dependency and not only a queue.
        """
        found = self._records.get(job_id)
        if found is None or found.state != "queued":
            return None, []
        queued = list(self._pending)
        if job_id not in queued:  # pragma: no cover - queued means pending
            return None, []
        ahead = queued[: queued.index(job_id)]
        position = len(ahead) + (1 if self._running_id is not None else 0)

        mine = {s.artifact_id for s in found.steps if s.artifact_id}
        blocking: set[str] = set()
        for other_id in ([self._running_id] if self._running_id else []) + ahead:
            other = self._records.get(other_id)
            if other is not None:
                blocking |= {s.artifact_id for s in other.steps if s.artifact_id} & mine
        return position, sorted(blocking)

    def _finish(
        self,
        record: JobRecord,
        state: JobState,
        *,
        failed_phase: str | None = None,
        cancelled_at_phase: str | None = None,
        returncode: int | None = None,
    ) -> None:
        """The **only** place a job changes state, and the only place legality is decided.

        Every caller used to check for itself whether a transition was allowed, and two of them
        disagreed about the same event. `LEGAL_TRANSITIONS` is now the single answer, enforced here,
        so a new caller cannot reintroduce that by omission.
        """
        with self._changed:
            if state not in LEGAL_TRANSITIONS[record.state]:
                raise JobError(
                    f"job {record.job_id!r} cannot move {record.state!r} -> {state!r}. Legal from "
                    f"{record.state!r}: {sorted(LEGAL_TRANSITIONS[record.state]) or 'nothing'}."
                )
            record.state = state
            record.finished_at = datetime.now(UTC)
            record.phase = None
            if state == "failed":
                record.failed_phase = failed_phase
                record.returncode = returncode
            if state == "cancelled":
                record.cancelled_at_phase = cancelled_at_phase
            # The job's occlusion-probe verdicts serve only its own later commits, and a finished
            # job makes none; kept, they grew by one entry per probed matrix for the server's life.
            for key in [key for key in self._probe_verdicts if key[0] == record.job_id]:
                del self._probe_verdicts[key]
            self._write(record)
            self._changed.notify_all()

    def _end(
        self,
        record: JobRecord,
        state: JobState,
        *,
        reason: str,
        **kwargs: Any,
    ) -> None:
        """Finish a job **the supervisor ended**, carrying `JOB_TERMINATED` and the reason.

        Ruling R10 allocates one `error` code for all three of them -- killed by a cancel, killed
        for exceeding a bound, abandoned by a server that stopped -- with the specific reason in
        `message`. A `failed` or `cancelled` state must not be reported through a `warn` advisory:
        `warn` means "usable, check an assumption", and a killed job produced nothing usable.

        A step that failed on its own merits does **not** come through here. Its evidence is its
        returncode, its `stderr_tail` and the classified advisories from its own stderr, and
        labelling that "terminated by the supervisor" would be untrue of the run.
        """
        with self._changed:
            record.message = reason
            record.advisories.append(
                AdvisoryRecord(code="JOB_TERMINATED", severity="error", message=reason)
            )
            self._finish(record, state, **kwargs)

    def _work(self) -> None:
        while True:
            with self._changed:
                while not self._pending and not self._stopping:
                    self._changed.wait()
                if self._stopping:
                    return
                job_id = self._pending.popleft()
                record = self._records[job_id]
                self._running_id = job_id
                record.state = "running"
                record.started_at = datetime.now(UTC)
                record.phase = record.steps[0].phase if record.steps else None
                self._write(record)
                self._changed.notify_all()
            try:
                self._run(record)
            except BaseException as exc:  # noqa: BLE001 - a dying worker takes the queue with it
                _log.exception("job %s supervision failed", job_id)
                with self._lock:
                    if record.state not in TERMINAL_STATES:
                        record.message = f"{type(exc).__name__}: {exc}"
                        # An engine refusal already carries its allocated code and severity --
                        # `EdgesIncompleteError` is `ARTIFACT_EDGES_INCOMPLETE` at `error`, which
                        # the brief requires a pre-build edge refusal to carry. Flattening it to a
                        # message string would drop a code that *is* allocated, leaving the failed
                        # payload's `advisories` empty for the one failure a client can act on.
                        advisory = getattr(exc, "advisory", None)
                        if isinstance(advisory, EngineAdvisory):
                            record.advisories.append(AdvisoryRecord.of(advisory))
                        self._finish(record, "failed", failed_phase=record.phase)
            finally:
                with self._lock:
                    self._running_id = None
                    self._process = None
                    self._cancelled.discard(job_id)
                    self._abandoned.discard(job_id)
                    self._signalled.discard(job_id)
                    self._results.pop(job_id, None)
                    self._steps.pop(job_id, None)
                with self._changed:
                    self._changed.notify_all()

    def _run(self, record: JobRecord) -> None:
        """Execute one job's steps in order, committing each artifact as its step succeeds."""
        paths = ProjectPaths(Path(record.project_root))
        job_dir = paths.job_dir(record.job_id)
        stdout_log = job_dir / STDOUT_NAME
        stderr_log = job_dir / STDERR_NAME
        with self._lock:
            steps = self._steps.get(record.job_id, ())

        for index, step in enumerate(steps):
            step_record = record.steps[index]
            if self._is_cancelled(record.job_id):
                self._end(
                    record,
                    "cancelled",
                    reason=f"cancelled before {step_record.phase!r} started.",
                    cancelled_at_phase=step_record.phase,
                )
                return

            with self._lock:
                record.step_index = index
                record.phase = step_record.phase
                step_record.started_at = datetime.now(UTC)
                self._write(record)

            if step.reuse:
                # **Ruling R65.** The artifact is already built, so there is nothing to spawn --
                # and check 6.8(d)'s real assertion is exactly that no process appears, because
                # *"wall time is machine-dependent, process spawning is not."* The step keeps its
                # place in `record.steps`, which is what makes 6.8(b) -- the plan's `reuse: true`
                # set -- assertable at all; only the work is skipped.
                #
                # **Placed after the cancellation check, deliberately.** A job cancelled while a
                # skipped step is current must still name that phase in `cancelled_at_phase`;
                # skipping first would run past it and report the next one.
                #
                # **`check_edges` is skipped with the spawn, not left standing.** It exists to
                # refuse a build before paying for it and there is no build -- and more than that,
                # `rad_engine.project.is_reusable`'s fourth condition (R26) *is* `check_edges`, so
                # re-running it here would risk refusing a reuse the predicate the caller consulted
                # has just approved. Two answers to one question is the drift, not the safety.
                #
                # `returncode` stays `None`: no process ran, and writing 0 would claim an exit
                # status nothing produced. `skipped` is the field that says which of the two
                # `returncode is None` cases this is.
                with self._lock:
                    step_record.skipped = True
                    step_record.finished_at = datetime.now(UTC)
                    self._write(record)
                continue

            if step.artifact is not None:
                # Read the manifest **now**, not once at job start. `save_manifest` is a blind
                # whole-document write, so a snapshot taken at t0 and written back an hour later
                # erases everything a tool call wrote in between -- and tool calls are not
                # serialised against this thread. Re-reading per step costs one small JSON read
                # against a build measured in minutes.
                #
                # Deliberately **not** under `project.manifest_lock`, unlike `_commit`: this reads
                # and never writes, and `os.replace` means a read is always of one whole document.
                # A read that lands just before another server's write sees a slightly older graph
                # and may start a build the seal-time `check_edges` then refuses -- which is the
                # cost of a wasted build, against making every step wait on another process's
                # writer. Nothing can be *erased* here, because nothing is written.
                with self._manifest_lock:
                    project = open_project(paths.root, radiance_version=self._launcher.version)
                    # Before the spawn, not after the matrix is written. `register_artifact` checks
                    # again at seal time and that check cannot be skipped -- but by then the build
                    # has cost what it costs
                    # (docs/tool-surface.md#how-provenance-invalidates-a-matrix).
                    check_edges(project, step.artifact.kind, list(step.artifact.built_from))

            argv, environ = self._launcher.resolve(step.argv, cwd=Path(step.cwd))
            if step.scene_inputs:
                # R755: the scan immediately before this spawn, against the bytes it will read and
                # the RAYPATH the environment resolved for it carries.
                try:
                    refuse_inline_commands_in_files(
                        list(step.scene_inputs),
                        cwd=Path(step.cwd),
                        raypath=raypath_dirs_of(environ, cwd=Path(step.cwd)),
                    )
                except SceneInlineCommandError as exc:
                    refusal = StepSceneRefusedError(
                        f"step {step_record.phase!r} was not started: {exc}"
                    )
                    # Finished here rather than raised to `_work`, whose handler logs a traceback
                    # as "supervision failed": this is the guard working, not the supervisor dying.
                    _log.warning("job %s: %s", record.job_id, refusal)
                    with self._changed:
                        record.message = str(refusal)
                        if refusal.advisory is not None:
                            record.advisories.append(AdvisoryRecord.of(refusal.advisory))
                        step_record.finished_at = datetime.now(UTC)
                        self._finish(record, "failed", failed_phase=step_record.phase)
                    return
            changed = _changed_input(step.input_digests)
            if changed is not None:
                # R822 (extended): the octree this step traces is not the one the job was submitted
                # against. Finished here, as the R755 refusal above is, not raised to `_work`.
                refusal = StepInputChangedError(
                    f"step {step_record.phase!r} was not started: {Path(changed).name} was "
                    "rebuilt with a different scene, or removed, after this job was submitted (a "
                    "run_annual at the same grid, a dry run included, rewrites it at the same "
                    "path after the geometry or a window group changed), so this step would not "
                    "have traced the scene this job was submitted against. Re-run run_annual."
                )
                _log.warning("job %s: %s", record.job_id, refusal)
                with self._changed:
                    record.message = str(refusal)
                    if refusal.advisory is not None:
                        record.advisories.append(AdvisoryRecord.of(refusal.advisory))
                    step_record.finished_at = datetime.now(UTC)
                    self._finish(record, "failed", failed_phase=step_record.phase)
                return
            # R755 final review: the scan can take seconds, and a cancel or a shutdown that landed
            # during it found no process to kill. Checked under the lock, after the scan and
            # before the spawn; `_execute` checks again once the process exists.
            with self._lock:
                cancelled = record.job_id in self._cancelled
                stopped = record.job_id in self._abandoned or self._stopping
            if cancelled:
                self._end(
                    record,
                    "cancelled",
                    reason=f"cancelled before {step_record.phase!r} started.",
                    cancelled_at_phase=step_record.phase,
                )
                return
            if stopped:
                self._end(
                    record,
                    "failed",
                    reason=(
                        f"the server stopped before the step started: {step_record.phase!r} "
                        "was never spawned. Artifacts committed before the stop remain in the "
                        "manifest."
                    ),
                    failed_phase=step_record.phase,
                )
                return
            with self._lock:
                step_record.resolved_argv = list(argv)
            stderr_offset = stderr_log.stat().st_size
            returncode, timed_out = self._execute(record, argv, environ, step, stdout_log, stderr_log)

            step_stderr = _read_from(stderr_log, stderr_offset)
            advisories = classify(step_stderr.splitlines())
            with self._lock:
                step_record.returncode = returncode
                step_record.finished_at = datetime.now(UTC)
                record.advisories.extend(AdvisoryRecord.of(a) for a in advisories)
                record.stderr_tail = _tail(stderr_log, STDERR_TAIL_LINES)

            if self._is_cancelled(record.job_id):
                self._end(
                    record,
                    "cancelled",
                    reason=(
                        f"cancelled during {step_record.phase!r}; its process group was sent a "
                        "kill."
                        if self._was_signalled(record.job_id)
                        else f"cancelled after {step_record.phase!r} had exited (returncode "
                        f"{returncode}) and before its output was committed; no process was "
                        "signalled."
                    ),
                    cancelled_at_phase=step_record.phase,
                )
                return

            if self._is_abandoned(record.job_id):
                # The server stopped. Same event `recover` reasons about, and the same state:
                # `failed`, because nobody cancelled it.
                self._end(
                    record,
                    "failed",
                    reason=(
                        f"the server stopped during {step_record.phase!r} and sent its process "
                        "group a kill. Artifacts committed before the stop remain in the manifest."
                        if self._was_signalled(record.job_id)
                        else f"the server stopped after {step_record.phase!r} had exited "
                        f"(returncode {returncode}) and before its output was committed; no "
                        "process was signalled. Artifacts committed before the stop remain in the "
                        "manifest."
                    ),
                    failed_phase=step_record.phase,
                    returncode=returncode,
                )
                return

            if returncode != 0:
                if timed_out:
                    self._end(
                        record,
                        "failed",
                        reason=(
                            f"step {step_record.phase!r} exceeded its {step.timeout_s} s bound and "
                            "its process group was sent a kill."
                        ),
                        failed_phase=step_record.phase,
                        returncode=returncode,
                    )
                else:
                    # Failed on its own merits: the evidence is the returncode, the stderr tail and
                    # the advisories classified from its own stderr. Not a supervisor termination.
                    self._finish(
                        record, "failed", failed_phase=step_record.phase, returncode=returncode
                    )
                return

            if step.artifact is not None:
                try:
                    self._commit(record, step.artifact, step_record, advisories)
                except _ProbeStopped as stop:
                    self._end_in_probe(
                        record, step_record.phase, signalled=stop.signalled, returncode=returncode
                    )
                    return

        with self._lock:
            builder = self._results.get(record.job_id)
        # The callable is in-memory; its **output** is what becomes durable, which is what matters
        # after a restart -- a succeeded job must answer with its result without re-running anything.
        built = builder(record) if builder is not None else None
        with self._lock:
            record.result = (
                built
                if built is not None
                else {"artifacts": [a.model_dump() for a in record.artifacts]}
            )
            record.step_index = len(record.steps)
            self._finish(record, "succeeded")

    def _execute(
        self,
        record: JobRecord,
        argv: list[str],
        environ: Mapping[str, str],
        step: Step,
        stdout_log: Path,
        stderr_log: Path,
    ) -> tuple[int, bool]:
        """Spawn one step and wait for it. Returns `(returncode, timed_out)`.

        The wait happens **outside** the store lock. A `proc.wait()` under the lock would block
        every `status` call for the length of a matrix build, which is the opposite of what
        reserving a core is for.
        """
        sink_path = Path(step.stdout_path) if step.stdout_path else stdout_log
        if step.stdout_path:
            sink_path.parent.mkdir(parents=True, exist_ok=True)
        # An artifact sink is truncated (it is this step's whole payload); the job log is appended
        # to, because several steps share it and a later step must not erase an earlier one's output.
        mode = "wb" if step.stdout_path else "ab"

        # A declared stdin is opened here and **not** guarded with a fallback. If this raises, the
        # step fails with the OSError -- which is the point: falling back to `DEVNULL` would hide a
        # missing points file behind an empty matrix, and falling back to an inherited fd 0 would
        # feed the child the JSON-RPC request stream (ruling R43, CONTEXT.md).
        with ExitStack() as streams:
            sink = streams.enter_context(_open_regular_file(sink_path, mode))
            errsink = streams.enter_context(_open_regular_file(stderr_log, "ab"))
            source: Any = subprocess.DEVNULL
            if step.stdin_path is not None:
                source = streams.enter_context(_open_regular_file(Path(step.stdin_path)))
            # A mark is about this step's process group. One left by an earlier step (a timeout
            # kill whose leader exited 0 at the deadline, so the job went on) must not word a stop
            # that lands on this one. Nothing can mark this step before `_process` is set below.
            with self._lock:
                self._signalled.discard(record.job_id)
            process = _spawn(
                argv,
                cwd=Path(step.cwd),
                environ=environ,
                stdout=sink,  # explicit, never inherited: stdout is the JSON-RPC transport
                stderr=errsink,  # a separate file, never merged into the one above
                stdin=source,  # DEVNULL unless this step declared a file to read
            )
            with self._lock:
                self._process = process
                # The pid reaches the durable record **before** anything waits on the process. A
                # server SIGKILLed a millisecond later leaves a live child; without this the record
                # would say `failed` with a process still behind it and nothing able to find it.
                step_record = record.steps[record.step_index]
                step_record.pid = process.pid
                step_record.pid_started_at = datetime.now(UTC)
                self._write(record)
            # Cancelled between the check in `_run` and the spawn. Kill it now: a job that was
            # already cancelled must not be allowed to run to completion because it won a race.
            # Abandoned too (R755 final review): a `shutdown` that read `_process` before this
            # spawn set it marked the job and killed nothing.
            if self._is_cancelled(record.job_id) or self._is_abandoned(record.job_id):
                with self._lock:
                    self._signalled.add(record.job_id)
                _terminate_tree(process, grace_s=self._kill_grace_s)
            try:
                return process.wait(timeout=step.timeout_s), False
            except subprocess.TimeoutExpired:
                with self._lock:
                    self._signalled.add(record.job_id)
                _terminate_tree(process, grace_s=self._kill_grace_s)
                code = process.poll()
                return (code if code is not None else -1), True
            finally:
                with self._lock:
                    self._process = None

    def _commit(
        self,
        record: JobRecord,
        spec: ArtifactSpec,
        step_record: StepRecord,
        advisories: Sequence[EngineAdvisory],
    ) -> None:
        """Seal one step's artifact into the manifest, durably, before the next step starts.

        Per step rather than per job: a 5-phase run that dies in Part 3 must leave V, D and the
        3-phase result committed and reusable. `register_artifact` fsyncs the artifact's bytes and
        writes the manifest atomically; the directory entry that names those bytes is flushed here,
        because nothing else does it and a manifest referencing a file with no name is the one
        inconsistency the digest cannot detect.

        **The manifest is re-read here, under both locks, and not carried from job start.**
        `save_manifest` writes the whole document with no compare-against-disk, so an hour-old
        snapshot written back at the end of a step erases every input registered, rescanned or
        updated while the job ran. Concurrency 1 serialises *jobs*, not tool calls against the
        worker. See the `manifest_lock` property for the caller's half of this.

        **Both locks, because the erasure has two sources.** `self._manifest_lock` excludes the tool
        calls in this server; `project.manifest_lock` excludes a *second server or a CLI* on the same
        directory, which the thread lock cannot see at all. Re-reading the manifest here shrinks the
        window from the length of a 5-phase run to the length of one `register_artifact` -- but
        shrinking a race is not closing one, and this is the only read-modify-write in the repo that
        runs on a hot path, so it is the one place where leaving it open would make ruling R11
        documented rather than achieved.

        **The order is thread lock first, then the file lock**, matching the order every other path
        already takes: `register_artifact` below reaches `save_manifest`, which acquires the file
        lock while this thread lock is held, so acquiring the file lock the other way round *here*
        would put the two orders in one program and complete a deadlock cycle. See
        `project.manifest_lock` for why the ordering is a rule for callers rather than something the
        lock can enforce.

        The wait is bounded like every other acquisition. A worker blocked forever is a job that
        never terminates and never reports, which is worse than one that fails with a reason -- and
        the artifact's bytes are already on disk and fsynced, so a re-run re-seals them rather than
        rebuilding from nothing.

        The classified stderr travels **with the artifact**: a matrix built from an octree that
        dropped an include is reusable forever, so the evidence has to be reusable with it.
        """
        _fsync_directory(Path(spec.path).parent)
        # Outside both locks: the probe spawns, and the locks bound a manifest write, not a trace.
        verdict = self._sun_reaches_grid(record.job_id, spec)
        wall = (
            (step_record.finished_at - step_record.started_at).total_seconds()
            if step_record.finished_at and step_record.started_at
            else None
        )
        with self._manifest_lock, manifest_lock(ProjectPaths(Path(record.project_root))):
            project = open_project(
                Path(record.project_root), radiance_version=self._launcher.version
            )
            artifact = register_artifact(
                project,
                artifact_id=spec.artifact_id,
                kind=spec.kind,
                path=spec.path,
                built_from=list(spec.built_from),
                parameters=dict(spec.parameters),
                radiance_version=self._launcher.version,
                warnings=advisories,
                wall_time_s=wall,
                # Ruling R144's pair, threaded straight through: the chain knows which window
                # group's octree this matrix was traced against, and `register_artifact` is where
                # the all-zero `asemx` guard consults it. `None` for every other kind and for the
                # whole-grid `asemx`, which is the run with every aperture open -- the guard then
                # fails closed and refuses, exactly as it did before the pair existed.
                ase_aperture=spec.ase_aperture,
                ase_suns=spec.ase_suns,
                # R831/R848: the occlusion probe's verdict and, where it gave none, its reason;
                # both `None` where no probe ran. R856: the glazing a two-phase probe removed,
                # which the seal's notice and refusal name; `()` for a window-group probe, for a
                # two-phase scene with no glazing, and for a two-phase probe that gave no verdict
                # before it read the glazing (a misuse, no dome, no snapshot, a scene changed since
                # submit).
                ase_sun_reaches_grid=None if verdict is None else verdict.reaches,
                ase_probe_no_verdict=None if verdict is None else verdict.reason,
                ase_probe_stripped_glazing=() if verdict is None else verdict.stripped_glazing,
                # The probe's mode from the spec, never from the names: a two-phase probe that
                # found no glazing (an unglazed opening) removes nothing.
                ase_probe_two_phase=spec.ase_probe is not None and spec.ase_probe.strip_glazing,
                # R858: a per-group probe's verdict is a difference trace, worded as one.
                ase_probe_group=spec.ase_probe is not None and spec.ase_probe.group is not None,
                # R869: the ASE_net matrix -- its probe closes the automated groups, and its
                # parameters name them, so a net matrix planned without the dome (no probe) is
                # still worded as one.
                ase_probe_net=(spec.ase_probe is not None and bool(spec.ase_probe.closed))
                or bool(spec.parameters.get("ase_net_blacked")),
                # Final review A1: what a refusal leaves readable depends on the chain.
                ase_chain=spec.ase_chain,
            )
        with self._lock:
            record.artifacts.append(
                CommittedArtifact(
                    id=artifact.id,
                    kind=artifact.kind,
                    path=artifact.path,
                    digest=artifact.digest,
                    content_digest=artifact.content_digest,
                    built_from=list(artifact.built_from),
                )
            )
            self._write(record)

    def _sun_reaches_grid(self, job_id: str, spec: ArtifactSpec) -> ProbeVerdict | None:
        """The occlusion probe's verdict for `spec`, run **only** where the seal would otherwise
        refuse (rulings R831, R848): an `asemx` carrying a probe, every cell zero, and -- where an
        aperture is given -- a dome direction in front of it, since with none the seal's no-sun
        branch answers without a trace. A matrix with signal, the ordinary case, costs one scan that
        stops at its first non-zero cell and no spawn.

        `None` -- no probe ran -- with no probe on the spec or no Radiance install to spawn through.
        A `ProbeVerdict` whose `reaches` is `None` -- the probe ran and gave no verdict -- carries
        its reason, including a failure of the probe itself; both refuse the matrix as before. A
        failed scan returns `None` and `register_artifact` raises the scan's own error again.

        Raises `_ProbeStopped` when the job was cancelled or abandoned, or a stop signal began the
        process's stop (`process_group.stop_has_begun`), by the time the probe returned: a stop is
        not a verdict, and the job ends as a stop (`_end_in_probe`).
        """
        probe = spec.ase_probe
        env = _probe_env(self._launcher)
        if probe is None or spec.kind != "asemx" or env is None:
            return None
        try:
            if not coefficient_matrix_is_all_zero(Path(spec.path)):
                return None
        except Exception:  # noqa: BLE001 -- register_artifact raises the scan's own error
            return None
        if (
            spec.ase_aperture is not None and spec.ase_suns is not None
            and suns_in_hemisphere(spec.ase_aperture, spec.ase_suns) == 0
        ):
            return None
        with self._lock:  # `_finish` prunes this dict from whichever thread ends a job
            cached = self._probe_verdicts.get((job_id, spec.artifact_id))
        if cached is not None:
            return cached
        collector = ChildCollector()
        with self._lock:
            # A cancel or a shutdown that landed before the probe: every spawn is refused at once.
            collector.stopping = job_id in self._cancelled or job_id in self._abandoned
            self._probe_collectors[job_id] = collector
        try:
            with collecting_children(collector):
                verdict = probe_ase_occlusion(env, probe)
        except Exception as exc:  # noqa: BLE001 -- no verdict is the fail-closed answer
            _log.warning(
                "occlusion probe for %s gave no verdict; the all-zero guard refuses as before",
                spec.artifact_id, exc_info=True,
            )
            verdict = ProbeVerdict(
                None, f"the probe failed ({type(exc).__name__}: {str(exc)[:300]})"
            )
        finally:
            with self._lock:
                self._probe_collectors.pop(job_id, None)
                stopped = job_id in self._cancelled or job_id in self._abandoned
        if stopped or stop_has_begun():
            # A cancel or a shutdown landed while (or before) the probe ran. A killed child makes
            # the probe raise, and that "no verdict" refused the matrix with a cause and a repair
            # that were false for it and never named the stop (Phase 24 final review). Not cached:
            # the job ends here. A stop signal counts too: `process_group.begin_stop_sweep` kills
            # the probe's children and marks nothing in this store, so its stop is read from
            # `stop_has_begun` (final fix round 2, measured), and the job ends as a shutdown's.
            raise _ProbeStopped(signalled=collector.signalled)
        # Per matrix, not per job: since R858 each per-group probe closes its own group.
        with self._lock:
            self._probe_verdicts[(job_id, spec.artifact_id)] = verdict
        return verdict

    def _end_in_probe(
        self, record: JobRecord, phase: str, *, signalled: bool, returncode: int | None
    ) -> None:
        """End a job whose cancel or shutdown landed in its commit's occlusion probe, worded and
        stated as `_run` words a stop during a step: `cancelled` for a client's cancel, `failed`
        for a shutdown, `JOB_TERMINATED` either way, and whether a process was sent a kill.
        `returncode` is the step's own exit status (0: only a step that exited 0 commits), recorded
        on a `failed` end as `_run`'s server-stopped path records the stopped step's
        (re-review M2)."""
        during = f"{phase!r}, while the occlusion probe judged its all-zero ASE matrix"
        if self._is_cancelled(record.job_id):
            self._end(
                record,
                "cancelled",
                reason=(
                    f"cancelled during {during}; the probe's process group was sent a kill, and "
                    "the matrix was neither sealed nor refused."
                    if signalled
                    else f"cancelled during {during} and before the matrix was committed; no "
                    "process was signalled, and the matrix was neither sealed nor refused."
                ),
                cancelled_at_phase=phase,
            )
            return
        self._end(
            record,
            "failed",
            reason=(
                f"the server stopped during {during}, and sent the probe's process group a kill; "
                "the matrix was neither sealed nor refused. Artifacts committed before the stop "
                "remain in the manifest."
                if signalled
                else f"the server stopped during {during} and before the matrix was committed; no "
                "process was signalled, and the matrix was neither sealed nor refused. Artifacts "
                "committed before the stop remain in the manifest."
            ),
            failed_phase=phase,
            returncode=returncode,
        )

    def _stop_probe(self, collector: ChildCollector | None) -> None:
        """Kill the occlusion probe's running child, and refuse its later spawns (`cancel`,
        `shutdown`). Called outside the lock: the kill waits up to two grace windows."""
        if collector is not None:
            collector.stop(grace_s=self._kill_grace_s)

    def _is_cancelled(self, job_id: str) -> bool:
        with self._lock:
            return job_id in self._cancelled

    def _is_abandoned(self, job_id: str) -> bool:
        """Killed by `shutdown`, which is not the same event as a client's `cancel`."""
        with self._lock:
            return job_id in self._abandoned

    def _was_signalled(self, job_id: str) -> bool:
        """Whether this job's step process group was sent a kill (R775); see `_signalled`."""
        with self._lock:
            return job_id in self._signalled
