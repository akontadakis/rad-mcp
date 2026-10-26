"""Project directories and manifest IO -- the on-disk half of the provenance graph.

`rad_core.manifest` owns the schema, the digests and the invalidation graph, and is pure: it never
reads a file. This module is the half that touches the disk -- the directory layout, the content
digest of an actual file, and reading and writing `manifest.json`. Nothing here re-derives a rule
that lives there: `combine_file_digests`, `resolve_ground_reflectance`, `resolve_built_from_digests`
and `recompute_digests` are called, never reimplemented (a second file-folding rule is the defect
Task 1's review caught).

**A project directory is not a valid path.** `create_project` and `open_project` take an
**already-validated, already-resolved absolute path**. Containment against the allowed roots is
`rad_mcp.paths.contain`'s job (docs/tool-surface.md#input-validation-and-the-path-boundary), and
`rad_mcp` sits *above* this layer (CONTEXT.md, Layers), so this module must not import it and does
not re-check it. What it does own is the *name*: a project name becomes a directory name, and
"a `project` argument of `../../etc` is the same bug as an unbounded absolute path"
(docs/tool-surface.md#handles-project-and-job_id), so names are validated as single path components
here as well as contained above.

**Atomic commit.** An artifact is committed only when it is on disk *and* in the manifest with a
digest, and a half-written `manifest.json` must never be readable as a whole one. Every write goes
to a temporary file in the same directory, is flushed and fsynced, and is then `os.replace`d over
the target -- an atomic rename within one filesystem. A write that fails rolls the in-memory
manifest back, so "committed means on disk" holds in both directions rather than only one.
`register_artifact` refuses an artifact whose file is not on disk, digests its bytes into
`content_digest`, and persists the manifest as part of registering, so a job that dies after
building V and D leaves both committed and reusable
(docs/tool-surface.md#what-job-returns-at-each-state).

**Atomicity is not exclusion, and `manifest_lock` is the other half.** `os.replace` guarantees no
reader sees a torn document; it guarantees nothing at all about two *writers*. A manifest is read at
t0 and written back whole at t1, so anything written in between is erased by a document that never
knew about it -- and the write being atomic is exactly what makes the erasure clean and silent.
`rad_engine.jobs.JobStore.manifest_lock` closed that window between the job worker thread and the
tool calls in its own process. `manifest_lock` here closes it between *processes*: two `rad-mcp`
servers on one project directory, or a server and a CLI. It is the same failure the whole provenance
design exists to prevent -- two stores disagreeing about whether a matrix was committed -- and it is
the same class as the two ground reflectances that motivated this project's engine layer.

**Ground reflectance has exactly one home.** It is a project field, set by `create_project` and
`update_project` and nowhere else. No input or artifact registration in this module accepts one,
because "a rule stating that a value is project-level, with no argument anywhere that sets it, is a
rule that gets implemented as three scattered defaults again"
(docs/tool-surface.md#ground-reflectance-is-one-project-value-passed-to-every-sky-generator).

**Advisories** are emitted as `rad_engine.advisories.EngineAdvisory`, the same frozen dataclass the
stderr table produces, so `rad_mcp` has one type to map at the wire boundary and not two. Their
`lines` tuple is empty: these findings are derived from the manifest, not from a Radiance stream,
and inventing a "line" for them would misrepresent where they came from. Consumers read `message`;
`EngineAdvisory.line` is only meaningful for stderr-derived advisories.

Codes come from the register in docs/tool-surface.md, which is the complete allocated list. Ruling
R6 allocated the five refusals this module needed -- `PROJECT_ALREADY_EXISTS`, `PROJECT_JOB_RUNNING`,
`PROJECT_NOT_FOUND`, `MANIFEST_UNREADABLE`, `ARTIFACT_EDGES_INCOMPLETE` -- because the recoveries
differ and a client that cannot tell two refusals apart cannot recover from either. What remains at
`code=None` is deliberate: a builder violating an internal invariant, where there is nothing a client
could have passed differently.
"""

from __future__ import annotations

import errno
import hashlib
import json
import math
import os
import re
import shutil
import stat
import tempfile
import threading
import time
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from typing import Any, Final

from pydantic import ValidationError

from rad_core.manifest import (
    GROUND_REFLECTANCE_INPUT_ID,
    SCALAR_INPUT_PREFIX,
    SCHEMA_VERSION,
    Artifact,
    ArtifactKind,
    ArtifactStatus,
    ArtifactWarning,
    DanglingEdgeError,
    FileRef,
    InputKind,
    InputRecord,
    Manifest,
    ManifestError,
    Project,
    Provenance,
    ProvenanceValue,
    ReuseSummary,
    ToolCall,
    Units,
    ZoneRecord,
    combine_file_digests,
    evaluate,
    normalise_parameters,
    recompute_digests,
    resolve_built_from_digests,
    resolve_ground_reflectance,
    reuse_summary,
)
from rad_core.apertures import Aperture
from rad_core.materials import InvalidRoleError, MaterialRole

from .advisories import EngineAdvisory, Severity
from .results import coefficient_matrix_is_all_zero
from .suns import SunPosition, exterior_elevation_deg, exterior_normal, sun_count, suns_in_hemisphere
from .safe_io import NotARegularFileError, handle_digest, open_regular_file

#: `fcntl` is POSIX-only. Absent, the cross-process half of `manifest_lock` cannot exist and the
#: in-process half still does -- see that function's docstring for what that costs on Windows.
_fcntl: ModuleType | None
try:
    import fcntl as _fcntl
except ImportError:  # pragma: no cover - exercised only on a platform without fcntl
    _fcntl = None

__all__ = [
    "ARTIFACT_DIRECTORY",
    "LAYOUT_DIRECTORIES",
    "MANIFEST_LOCK_NAME",
    "MANIFEST_LOCK_TIMEOUT_S",
    "MANIFEST_NAME",
    "NORTH_INPUT_ID",
    "REQUIRED_EDGES",
    "TRANSMITTING_MATERIAL_WHERE",
    "Description",
    "EdgeRequirement",
    "EdgesIncompleteError",
    "GeometryUnitsNotAppliedError",
    "GroundReflectanceRangeError",
    "LoadedProject",
    "ManifestLockTimeout",
    "ManifestUnreadableError",
    "ProjectError",
    "ProjectExistsError",
    "ProjectNameError",
    "ProjectNotEmptyError",
    "ProjectNotFoundError",
    "ProjectPaths",
    "ProjectSummary",
    "ProjectTreeUnrecognisedError",
    "ProjectUnitsUnsetError",
    "ProvenanceError",
    "RunningJobError",
    "ScalarNotFiniteError",
    "ScalarNotNumberError",
    "UpdateResult",
    "archive_project",
    "bytes_digest",
    "check_edges",
    "create_project",
    "delete_project",
    "describe",
    "drifted_inputs",
    "evaluate_loaded",
    "file_digest",
    "file_refs",
    "is_reusable",
    "list_projects",
    "manifest_lock",
    "open_project",
    "orphan_entries",
    "refuse_unscaled_geometry",
    "register_artifact",
    "register_input",
    "rescan_inputs",
    "reuse_advisory",
    "save_manifest",
    "unscaled_geometry_records",
    "update_project",
]

MANIFEST_NAME: Final = "manifest.json"

#: The file `manifest_lock` flocks. **Not `manifest.json` itself**: every write replaces that path's
#: inode, so a process holding a lock on the old inode and a process opening the new one would each
#: believe they held the same lock and neither would be wrong. A separate file is never renamed, so
#: its inode is the stable thing to serialise on.
#:
#: Dot-prefixed for the reason `save_manifest`'s temp files are: a crash must not leave something in
#: the project root that reads as part of the layout.
MANIFEST_LOCK_NAME: Final = ".manifest.lock"

#: How long `manifest_lock` waits for another process before refusing.
#:
#: Bounded rather than infinite because "a tool call that blocks forever is indistinguishable from a
#: hung server" (docs/tool-surface.md#concurrency, the rule that bounds `job(action="wait")`). The
#: critical section is one small JSON document plus, at worst, the content digest of one matrix, so
#: thirty seconds is far longer than any honest holder needs and short enough that a caller learns
#: something is wrong while it still has a client attached.
MANIFEST_LOCK_TIMEOUT_S: Final = 30.0

#: Poll bounds for the wait. `flock` has no timed form, so the wait is a non-blocking retry: start
#: fine so an uncontended-but-just-missed lock is picked up almost immediately, back off so thirty
#: seconds of waiting is not thirty seconds of spinning.
_LOCK_POLL_MIN_S: Final = 0.002
_LOCK_POLL_MAX_S: Final = 0.05

#: The layout of docs/specs/workflows-point-in-time-to-annual.md §1.1 with the **three deviations**
#: docs/tool-surface.md#2-project states: the spec's `07_scripts/` is replaced by `10_jobs/<job_id>/`
#: (nothing here is run by hand from a scripts directory, and conflating "the commands that ran" with
#: "the scripts you could run" hides which one produced a result), `11_sensors/` is added for `.pts`
#: files, and `12_luminaires/{types,placements}/` is added for imported IES types and placement
#: files (Phase 11a, Amendment 2). All three are recorded in the manifest by `Project.schema_version`
#: -- the layout is fixed per schema version, so a manifest states which layout it was written
#: against.
LAYOUT_DIRECTORIES: Final[tuple[str, ...]] = (
    "01_geometry",
    "02_materials",
    "03_views",
    "04_skies",
    "05_bsdf",
    "06_octrees",
    "07_matrices",
    "08_results",
    "09_images/hdr",
    "09_images/ldr",
    "10_jobs",
    "11_sensors",
    "12_luminaires/types",
    "12_luminaires/placements",
)

#: Where each artifact kind is written. `amb` has no directory of its own in §1.1 and adding one
#: would be a third deviation, so the ambient cache sits beside the octree it is keyed to -- it is a
#: cache of that octree's interreflection and is invalidated with it
#: (docs/tool-surface.md#what-it-records, "The ambient cache is an artifact, not a temp file").
ARTIFACT_DIRECTORY: Final[Mapping[str, str]] = {
    "octree": "06_octrees",
    "amb": "06_octrees",
    "vmx": "07_matrices",
    "dmx": "07_matrices",
    "tmx": "07_matrices",
    "smx": "07_matrices",
    "dsmx": "07_matrices",
    # Phase 7a Task 13's five-phase matrices. All five are matrices and all five live where every
    # other matrix does; the entries exist because `test_every_artifact_kind_has_a_home` requires
    # this mapping and `ArtifactKind` to be the same set, so a kind added to one without the other
    # is refused rather than written wherever the caller felt like.
    "vdmx": "07_matrices",
    "ddmx": "07_matrices",
    "cdsmx": "07_matrices",
    "smxd": "07_matrices",
    "smxsun": "07_matrices",
    # Phase 7c's ASE coefficient matrix. A matrix, so it lives where every other matrix does; the
    # entry exists in the same commit as its `ArtifactKind` member for the reason stated there.
    "asemx": "07_matrices",
    "ill": "08_results",
    # Phase 7d-1's blind schedule. **A result, not a matrix**: it is what the run decided about the
    # blinds over the analysis period, it is read back beside the `.ill` files it selected between,
    # and `rad_engine.blend` is handed its path from the same directory. The entry exists in the
    # same commit as its `ArtifactKind` member and its `REQUIRED_EDGES` row, for the reason stated
    # on that member.
    "sched": "08_results",
    # Phase 7d-1 Task 12's blended annual result. A result, beside the per-state results it sums
    # and beside the schedule that selected between them; the entry exists in the same commit as
    # its `ArtifactKind` member and its `REQUIRED_EDGES` row, for the reason stated on that member.
    "illblend": "08_results",
    # Phase 14d (R730): `run_point_in_time`'s sky, in the layout's own sky folder, and its result,
    # beside every other result. Each entry exists in the same commit as its `ArtifactKind` member
    # and its `REQUIRED_EDGES` row, for the reason stated on that member.
    "sky": "04_skies",
    "point_in_time": "08_results",
    "hdr": "09_images/hdr",
    "pts": "11_sensors",
}

#: A project name, a job id and an artifact filename all become path components. Anchored, no
#: separators, no leading dot: `..` and `a/b` and `.hidden` are all refused, and so is the empty
#: string. 64 characters is a directory name, not a description.
_COMPONENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")

#: The other synthetic project scalar the invalidation table gives a row of its own. `rad_core`
#: exports the ground one as a constant because it is a `Project` field; `north` lives in the
#: untyped `scalars` dict, so its id is spelled here rather than left as a literal in the table.
NORTH_INPUT_ID: Final = SCALAR_INPUT_PREFIX + "north"


class _Unset:
    """Sentinel for "argument not given", distinct from an explicit `None`.

    `update_project(ground_reflectance=None)` means *reset to the labelled default*, which is a
    different instruction from *leave it alone*.
    """

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "<unset>"


_UNSET: Final = _Unset()


# --------------------------------------------------------------------------------------------
# Errors
# --------------------------------------------------------------------------------------------


class ProjectError(RuntimeError):
    """Something this module refuses to do.

    On the wire a refusal is `{"refused": true, "reason": ..., "advisories": [...]}` and never an
    exception (docs/tool-surface.md#refusals) -- but that shape is the *protocol* layer's, and
    building it here would put the wire contract in the adapter. So the engine raises, carrying the
    allocated advisory `code` where the register has one, and `rad_mcp` shapes the envelope. This is
    how `rad_mcp.paths.PathOutsideRoot` already works.

    `code` is `None` where the register has no code for the condition. Codes are allocated once and
    never renamed, so a gap is reported rather than filled by invention -- ruling R6 allocated the
    five this module needed (docs/tool-surface.md#advisory-code-register).
    """

    code: str | None = None
    severity: Severity = "error"
    """Read from the class rather than hardcoded at the call site: a code allocated at `warn` would
    otherwise be emitted as an `error` by a property that never asked."""
    field: str | None = None
    """Which **client argument** the refusal is about, where the refusal is about one.

    `None` for the refusals that are about a state rather than an argument -- a running job, a
    manifest that will not parse. It exists because `ARGUMENT_OUT_OF_RANGE` is one code for the whole
    class of arguments refused on their face, and *"the `field` key already says which argument"*
    (docs/tool-surface.md#advisory-code-register): a code without it would be unactionable in a tool
    that takes six arguments. `rad_mcp.runtime.refuse_engine_error` reads it -- the engine still does
    not build the envelope.

    Fixed per class (`ProjectNameError.field = "name"`) for every refusal about one named argument.
    `ScalarNotFiniteError` and `ScalarNotNumberError` are the exceptions: `scalars` is a
    `Mapping[str, float]`, one refusal can be about any of its keys, and a class attribute cannot
    say which -- so it is passed at construction instead, the same choice
    `rad_engine.materials.MaterialsEmissionError` already made for the same reason (a
    `MaterialArgumentError` names whichever of `reflectance`/`transmittance` is wrong)."""

    def __init__(self, message: str, *, code: str | None = None, field: str | None = None) -> None:
        super().__init__(message)
        if code is not None:
            self.code = code
        if field is not None:
            self.field = field

    @property
    def advisory(self) -> EngineAdvisory | None:
        """The advisory `rad_mcp` puts in the refusal, or `None` when no code is allocated."""
        if self.code is None:
            return None
        return _advisory(self.code, self.severity, str(self))


class ProjectExistsError(ProjectError):
    """A project is already where one is about to be put.

    Refused, not adopted: "silently adopting an existing directory is how you get a manifest that
    describes a different scene than the files" (docs/tool-surface.md#2-project). Distinct from
    `PROJECT_NOT_EMPTY`, which is the `delete`-without-`force` case: the recoveries differ.
    """

    code = "PROJECT_ALREADY_EXISTS"


class ProjectNotFoundError(ProjectError):
    """No project directory, or one with no manifest in it."""

    code = "PROJECT_NOT_FOUND"


class ProjectNameError(ProjectError):
    """A project name, job id or filename that is not a single safe path component.

    **This one is client-facing, which is why it carries a code.** The module docstring reserves
    `code=None` for *"a builder violating an internal invariant, where there is nothing a client
    could have passed differently"* -- and a project **name** is the most client-supplied argument
    on the tool. `project(action="describe", name="../etc")` is the path-traversal rejection a
    client most wants to branch on, and it used to return a refusal with no `advisories` key at all.

    `ARGUMENT_OUT_OF_RANGE` rather than a new code: ruling R14 allocated it for *"an argument
    rejected on its face, before any work was attempted"* and the same tool already uses it for
    `confirm` and for missing arguments. `field` says which argument, which is what lets one code
    cover the class.
    """

    code = "ARGUMENT_OUT_OF_RANGE"
    field = "name"


class ProjectNotEmptyError(ProjectError):
    """`delete` without `force` on a project that has registered inputs or built artifacts."""

    code = "PROJECT_NOT_EMPTY"


class GroundReflectanceRangeError(ProjectError):
    """`ground_reflectance` is not a finite fraction in [0, 1].

    Allocated no code of its own: ruling R14's `ARGUMENT_OUT_OF_RANGE` is *"an argument rejected on
    its face, before any work was attempted"*, and `field` is what keeps one code actionable across
    many arguments -- the same choice `ProjectNameError` already made.

    Two separate refusals share this class, because both are the same argument rejected on its face:

    - **Non-finite.** `NaN` and `Infinity` are accepted by pydantic-core's JSON parser for a `float`
      field, and until 2026-08-31 they were *stored*: `update_project` committed before `describe`
      ran, the serializer wrote JSON `null` where the model declares a non-nullable `float`, and the
      project became unreadable through `describe`, `update` **and** `delete` -- permanently, since
      `delete_project` loads the manifest as its evidence that the tree is the project. Graded
      Critical by the whole-branch security review; this guard is the boundary half of the fix and
      `ProvenanceValue._the_value_must_be_finite` is the structural half.
    - **Outside [0, 1].** `-5.0` and `100.0` were stored without comment and reached `gensky -g` and
      `gendaymtx -g` verbatim (`sky.build_gensky_argv`, `build_gendaylit_argv`,
      `skymatrix.build_gendaymtx_argv`). A reflectance is the
      fraction of incident light a surface returns; outside [0, 1] it is not a wrong *setting*, it
      is not a reflectance, and `-g 100` puts a hundred times the incident light back into the sky
      matrix's ground patch with no error anywhere. Both ends are refused rather than clamped: a
      clamp would silently simulate a number the caller did not ask for, and CONTEXT.md's own
      ground-reflectance trap is about exactly this value disagreeing with itself in silence.
    """

    code = "ARGUMENT_OUT_OF_RANGE"
    field = "ground_reflectance"


class ScalarNotFiniteError(ProjectError):
    """A `scalars` value `create_project` was asked to store is not a finite number.

    **The `scalars` sibling of `GroundReflectanceRangeError`'s non-finite half, and the same
    Critical shape.** Measured 2026-09-24: `create_project(parent, name=..., units="m",
    radiance_version="x", scalars={"north": float("nan")})` (and the same with `float("inf")`) was
    accepted -- it laid out the project directories and wrote `"north": null` into `manifest.json`
    (Python's own `json` module writes `NaN`/`Infinity`/`-Infinity`, and the manifest writer's
    serialisation turns that into `null`). `open_project` then raised `ManifestUnreadableError`
    ("project.scalars.north ... Input should be a valid number"): the project was unopenable from
    birth, through every later call on it. Not silently unrotated -- unreadable.

    `ground_reflectance` already had this guard, because it is a `Project` field of its own,
    wrapped in `ProvenanceValue`, whose `_the_value_must_be_finite` validator refuses a non-finite
    `value` at construction. `scalars` reaches `Project.scalars: dict[str, float]` directly, with no
    wrapper and no boundary check of its own -- so the same class of value that
    `_validated_ground_reflectance` already refuses for one project field reached disk unrefused
    through this one.

    Reuses `ARGUMENT_OUT_OF_RANGE` (ruling R14: an argument rejected on its face, before any work is
    attempted) -- the same code `GroundReflectanceRangeError` reuses for the sibling project scalar.
    `field` names the specific key (`"scalars.north"`), not the whole dict, the same choice
    `MaterialArgumentError` makes for `reflectance`/`transmittance` in `rad_engine.materials`.

    Also raised for an `int` too large for any double (`10**400`), which `math.isfinite` met with a
    bare `OverflowError` (measured 2026-09-24): no finite float can store it.
    """

    code = "ARGUMENT_OUT_OF_RANGE"


class ScalarNotNumberError(ProjectError):
    """A `scalars` value `create_project` was asked to store is not a number at all.

    **`ScalarNotFiniteError`'s sibling, for the values that never reach its test** (R710). Measured
    2026-09-24: `scalars={"north": "abc"}` (or a list, or a dict) raised a bare
    `TypeError: must be real number, not str` from `math.isfinite` -- an uncoded exception, no
    `field`. `scalars={"north": True}` passed, because `math.isfinite(True)` is true, and
    `Project.scalars: dict[str, float]` then stored it as `1.0`: a project silently rotated by one
    degree, indistinguishable on disk from one that declared `north: 1.0`. A `bool` is refused
    although Python counts it an `int`, for that reason.

    A sibling rather than a widened `ScalarNotFiniteError`, because that name says the value is a
    number and not a finite one, which is false of `"abc"`. Same code, same per-key `field`, same
    point in `create_project` -- before anything is written.
    """

    code = "ARGUMENT_OUT_OF_RANGE"


class ProjectTreeUnrecognisedError(ProjectError):
    """The directory is not (only) the project its manifest claims, so it is not ours to remove.

    Allocated `PROJECT_TREE_UNRECOGNISED` 2026-08-01, after an audit reproduced a well-formed
    `create` + `delete` pair destroying a pre-existing user folder. Distinct from
    `PROJECT_NOT_EMPTY` in the one way that matters: **`force` does not override this one.**
    `PROJECT_NOT_EMPTY` means *"this project has work in it, confirm you meant that"* and is
    recovered by passing `force`; this means *"this tree holds things this project never put here"*
    and is recovered by moving them out, or by removing the directory outside this server.
    Reporting the two alike would send a client to a recovery that deletes the files it was warning
    about (ruling R6's reasoning, applied to a refusal R6 did not foresee).
    """

    code = "PROJECT_TREE_UNRECOGNISED"


class RunningJobError(ProjectError):
    """`delete` or `archive` without `force` while a job is writing into the directory."""

    code = "PROJECT_JOB_RUNNING"


class ManifestUnreadableError(ProjectError):
    """`manifest.json` is present but is not JSON, or is of an unknown schema.

    Deliberately not the same as `PROJECT_NOT_FOUND`: one is recovered by creating a project, the
    other by repairing or restoring one, and reporting them alike sends the client to the wrong
    remedy (ruling R6).
    """

    code = "MANIFEST_UNREADABLE"


class ProjectUnitsUnsetError(ManifestUnreadableError):
    """A manifest with no project `units`.

    Not defaulted and not guessed: "a scene modelled in feet and read as metres is wrong by 3.28x
    with no error" (docs/tool-surface.md#required-with-no-default). The register allocates
    `PROJECT_UNITS_UNSET` at `error` for exactly this, "on any tool if a project was created before
    this field existed".
    """

    code = "PROJECT_UNITS_UNSET"


class ManifestLockTimeout(ProjectError):
    """Another writer held the manifest lock longer than this caller was willing to wait.

    Ruling R12 allocated `MANIFEST_LOCK_BUSY` for it, the fourth register gap found and ruled on
    this branch. It carries a code rather than refusing as prose because **this refusal is
    retryable**, and a retryable refusal is exactly where a machine-readable code earns its keep: a
    client that can read the code backs off and retries, where one reading a sentence can only
    surface it. The condition is transient by construction -- the only ways to hold this lock for
    thirty seconds are a pathologically large content digest or a bug.
    """

    code = "MANIFEST_LOCK_BUSY"


class ProvenanceError(ProjectError):
    """An artifact that cannot be committed as described.

    Its file is not on disk, or its path is outside the project. Internal invariants violated by a
    *builder*, not by a user argument -- which is why they carry no advisory code: there is nothing
    a client could have passed differently.
    """


class EdgesIncompleteError(ProvenanceError):
    """`built_from` omits an edge this artifact's kind requires, or names something unknown.

    This one *does* carry a code, unlike its parent, and the reason is the false-refusal case:
    `REQUIRED_EDGES` is a floor derived from the spec's invalidation table, and the day it refuses a
    legitimate scene it has not caught up with, the user needs something machine-readable to act on
    rather than an internal error (ruling R6, `ARTIFACT_EDGES_INCOMPLETE`).

    Raised in two places for the "names something unknown" half: `check_edges`, before a build ever
    starts, and `evaluate_loaded`, which recodes `rad_core.manifest.DanglingEdgeError` when a
    manifest already on disk (hand-edited, or corrupted) is found to carry the same dangling edge on
    load. Both are the same fact about the manifest caught at different times, so both carry this one
    code rather than two.
    """

    code = "ARTIFACT_EDGES_INCOMPLETE"


class AseDirectMatrixEmptyError(ProvenanceError):
    """Every cell of an `asemx:<grid>` coefficient matrix is zero, so the ASE leg produced nothing.

    **The most important single refusal in Phase 7c.** Nothing in `window_group(action="define")`
    requires an aperture polygon to carry a transmitting material: in a three-phase model the
    transmission **is** the T matrix and the polygon can be a placeholder. An opaque one kills every
    shadow ray in the `-ab 0` direct-sun trace, `asemx` comes back identically zero, `illdir:<grid>`
    multiplies to zero, `rad_core.metrics.annual_sunlight_exposure` returns `0.0` and
    `rad_core.compliance.ase_band(0.0)` returns `"clearly_acceptable"` -- the single most favourable
    verdict LM-83 has, from a run that produced nothing at all. This repo has shipped that exact
    silent zero once already: `rad_engine.octree.build_suns_octree`'s own docstring records `Cds`
    coming back identically zero with `oconv` exiting 0 and *"nothing anywhere reports it"*.

    **It keys on `asemx`, the coefficient matrix, and never on `illdir`, the lux matrix multiplied
    out of it** -- a reversal of the design, forced by Phase 7c Task 1's measurement and settled by
    owner ruling. On the micro fixture with its committed weather, a transmitting `void glass`
    aperture, a valid octree, a non-zero `asemx` (465 of 2 306 bins for sensor 0) and a non-zero
    `smxsun` (3 non-zero rows) multiplied to an **identically zero** `illdir`, five runs out of five,
    for correct physics: a 2 m deep room with an 0.8 m window admits at most 48.8 deg above the
    workplane and Athens' three June suns sit at 48.9 deg and 74.2 deg, so the two non-zero bin sets
    do not intersect (`tests/fixtures/README.md`, "Step 3"). Refusing an all-zero `illdir` would
    refuse a legitimate `ASE = 0 %` on any deep room or high-latitude winter study. `asemx` carries
    no weather at all: it is all-zero only when no sensor has a line of sight to any sky direction
    through anything that transmits.

    **It reads the MATRIX, not the material.** A material-name check would be a second definition of
    "transmits" living in this codebase, and it would miss an aperture occluded by an interior
    partition, a mis-oriented overhang, or a `-m` naming a modifier the octree does not carry. The
    matrix is what decides the verdict, so the matrix is what the guard reads.

    **One non-zero cell passes.** The condition is *"nothing at all"*, never *"not much"*: a
    genuinely dark space is a real result, and rulings R35/R37's rule against moving a threshold to
    suit a test applies to a guard's own boundary too.

    Unlike its parent it carries a code, for `EdgesIncompleteError`'s reason one axis over: this is
    not a builder violating an internal invariant, it is a **scene** the caller can repair -- model
    the glazing and re-run -- so the refusal has to be machine-readable rather than an internal
    error.
    """

    code = "ASE_DIRECT_MATRIX_EMPTY"
    severity: Severity = "error"


class GeometryUnitsNotAppliedError(ProjectError):
    """A geometry input registered before Phase 10 with a non-metre unit, in place, so its coordinates
    were never scaled while every grid and view position is in metres (design §5.11)."""

    code = "GEOMETRY_UNITS_NOT_APPLIED"
    field = "project"


def unscaled_geometry_records(manifest: Manifest) -> tuple[InputRecord, ...]:
    return tuple(
        record for record in manifest.inputs
        if record.kind == "geometry" and record.units not in (None, "m") and not record.server_written
    )


def refuse_unscaled_geometry(project: LoadedProject) -> None:
    unscaled = unscaled_geometry_records(project.manifest)
    if unscaled:
        named = ", ".join(f"{record.id!r} ({record.units})" for record in unscaled)
        raise GeometryUnitsNotAppliedError(
            f"geometry input(s) {named} were registered before this server scaled imports: their "
            "coordinates are still in the declared unit while grids and views are in metres. Re-import "
            "each with geometry(action='add', ..., units=...) so it is stored in metres."
        )


# --------------------------------------------------------------------------------------------
# Layout
# --------------------------------------------------------------------------------------------


def _component(value: str, *, what: str) -> str:
    if not _COMPONENT.match(value or ""):
        raise ProjectNameError(
            f"{what} {value!r} is not a usable directory name. It must be 1-64 characters of "
            "letters, digits, '.', '_' or '-', start with a letter or digit, and contain no path "
            "separator -- a name is concatenated into a path, so '..' and 'a/b' are refused."
        )
    return value


@dataclass(frozen=True)
class ProjectPaths:
    """Every path in one project directory.

    `root` is the **project's own** directory, `<parent>/<name>`. The `project` tool's `root`
    argument is the *parent* ("create: parent dir for the project", docs/tool-surface.md#2-project);
    the two senses are one character apart in prose and are kept apart here by name.
    """

    root: Path

    @classmethod
    def for_project(cls, parent: Path | str, name: str) -> ProjectPaths:
        """The paths of project `name` under an already-validated, already-resolved `parent`."""
        return cls(Path(parent) / _component(name, what="project name"))

    @property
    def name(self) -> str:
        return self.root.name

    @property
    def manifest(self) -> Path:
        return self.root / MANIFEST_NAME

    @property
    def manifest_lock_file(self) -> Path:
        """The file `manifest_lock` flocks -- see `MANIFEST_LOCK_NAME` for why it is not the
        manifest. It is created on the first write and then never removed: unlinking a file other
        processes hold open drops the mutual exclusion without anyone noticing."""
        return self.root / MANIFEST_LOCK_NAME

    @property
    def geometry(self) -> Path:
        return self.root / "01_geometry"

    @property
    def materials(self) -> Path:
        return self.root / "02_materials"

    @property
    def views(self) -> Path:
        return self.root / "03_views"

    @property
    def skies(self) -> Path:
        return self.root / "04_skies"

    @property
    def bsdf(self) -> Path:
        return self.root / "05_bsdf"

    @property
    def octrees(self) -> Path:
        return self.root / "06_octrees"

    @property
    def matrices(self) -> Path:
        return self.root / "07_matrices"

    @property
    def results(self) -> Path:
        return self.root / "08_results"

    @property
    def images_hdr(self) -> Path:
        return self.root / "09_images" / "hdr"

    @property
    def images_ldr(self) -> Path:
        return self.root / "09_images" / "ldr"

    @property
    def jobs(self) -> Path:
        return self.root / "10_jobs"

    @property
    def sensors(self) -> Path:
        return self.root / "11_sensors"

    @property
    def luminaire_types(self) -> Path:
        return self.root / "12_luminaires" / "types"

    @property
    def luminaire_placements(self) -> Path:
        return self.root / "12_luminaires" / "placements"

    @property
    def source_cal(self) -> Path:
        return self.root / "source.cal"

    def luminaire_type_file(self, name: str, suffix: str) -> Path:
        return self.luminaire_types / f"{name}{suffix}"

    def luminaire_placement_file(self, name: str) -> Path:
        return self.luminaire_placements / f"{name}.rad"

    def directories(self) -> tuple[Path, ...]:
        """The layout, in order, as absolute paths."""
        return tuple(self.root / relative for relative in LAYOUT_DIRECTORIES)

    def job_dir(self, job_id: str) -> Path:
        """`10_jobs/<job_id>/` -- stdout, stderr, argv and timing for one run.

        The job id is validated as a path component for the same reason a project name is: it
        arrives from a client as a handle and is concatenated into a path
        (docs/tool-surface.md#handles-project-and-job_id).
        """
        return self.jobs / _component(job_id, what="job id")

    def geometry_file(self, input_id: str) -> Path:
        """`01_geometry/<input_id>.rad` -- the metre copy `geometry` writes (Phase 10 design §5.9).

        Not an `ARTIFACT_DIRECTORY` entry: geometry is an input, not an artifact kind, and
        `tests/test_project.py::test_every_artifact_kind_has_a_home` pins that mapping to `ArtifactKind`.
        The filename goes through `_component`, so `/`, `..` and names over 64 characters are refused.
        """
        return self.geometry / _component(f"{input_id}.rad", what="geometry filename")

    def artifact_path(self, kind: str, filename: str) -> Path:
        """Where an artifact of `kind` is written, by the layout rather than by the caller."""
        try:
            relative = ARTIFACT_DIRECTORY[kind]
        except KeyError:
            raise ProjectError(f"no directory is defined for artifact kind {kind!r}") from None
        return self.root / relative / _component(filename, what="artifact filename")


# --------------------------------------------------------------------------------------------
# File digests -- the half `rad_core` cannot do
# --------------------------------------------------------------------------------------------


def file_digest(path: Path | str) -> str:
    """`sha256:` over the file's **bytes**, and over nothing else.

    Not the size, not the mtime, not the path. Check 1.3's file half is exactly this: a file whose
    bytes change gets a different digest, and a file touched but unchanged gets the same one. A
    digest that read the mtime would rebuild every matrix after a `git checkout`; one that read the
    size would miss a one-character edit to a material.

    **The block loop itself lives in `safe_io.handle_digest`**, which this calls, so the path form
    and the held-descriptor form are one arithmetic path rather than two that agree today (ruling
    R30). `rad_engine.results.iter_verified_annual_lux_rows` is the held-descriptor caller: it
    hashes and then reads the rows without a second `open`, which this function's own shape --
    open, hash, close -- cannot do. See `handle_digest` for what that narrows and what it does not.
    """
    path = Path(path)
    try:
        # `open_regular_file`, not `path.open("rb")` -- R48's rule, adopted here 2026-08-10. This
        # is the last unguarded open in this module; `_load_manifest` adopted the primitive on
        # 2026-08-09. The hazard is the same one and worse in shape: this loop reads to EOF, so a
        # FIFO or `/dev/zero` at a registered input path feeds it forever while it hashes, and the
        # paths it walks come from the **manifest** -- strings a previous call wrote, re-opened
        # here with no re-containment.
        with open_regular_file(path, "rb") as handle:
            return handle_digest(handle)
    except IsADirectoryError:
        raise ProvenanceError(f"{path} is a directory, not an input file") from None
    except FileNotFoundError:
        raise ProvenanceError(f"input file does not exist: {path}") from None
    except NotARegularFileError as exc:
        # Translated into this module's own family rather than allowed to escape as a bare
        # `OSError`. Every other way this function can fail is already a `ProvenanceError` -- the
        # callers catch that and nothing else -- so letting the new guard raise a different type
        # would turn a refusal into an uncaught exception at every call site, which is the
        # check-0.4 class. `IsADirectoryError` above is the precedent: same shape of problem, same
        # translation.
        raise ProvenanceError(
            f"{path} is not a regular file, so it cannot be digested as a project input: {exc}"
        ) from None
    except OSError as exc:
        # **`ELOOP` is the same refusal, and it does not arrive as `NotARegularFileError`**
        # (whole-branch audit, 2026-08-10). `O_NOFOLLOW` fails the open before there is a descriptor
        # to `fstat`, so the clause above never fires for a symlink -- the very case the guard most
        # exists for. Measured before this clause: a directory and a FIFO raised `ProvenanceError`
        # and a symlink escaped as a bare `OSError` errno 62, so one function produced two refusal
        # shapes for one condition. Only `ELOOP` is translated; every other `OSError` (a full disk,
        # a permission the server lacks) is a different failure and is left to propagate rather than
        # be relabelled as provenance -- the mislabel this same audit found in `run_annual`.
        if exc.errno != errno.ELOOP:
            raise
        raise ProvenanceError(
            f"{path} is a symlink, so it cannot be digested as a project input: the server refuses "
            f"to read through one because the target may be any file it can reach ({exc})"
        ) from None


def bytes_digest(data: bytes) -> str:
    """`sha256:` over bytes **already in memory** -- the same string `file_digest` produces for a
    file holding exactly those bytes, in the same format, so the two are directly comparable
    against a recorded `FileRef.digest`.

    **Exists so a drift check and the parse it guards can be one read.** `file_digest(path)` opens,
    hashes and closes; a caller that then opens the path again to parse it has checked one set of
    bytes and used another, and a writer inside an allowed root that swaps the file between the two
    opens passes the gate. That is exactly the silent swap ruling R97's drift check exists to
    catch, so the fix is to read once and hand the same buffer to both -- this function is the half
    that hashes it. `tests/test_project.py` pins `bytes_digest(p.read_bytes()) == file_digest(p)`
    rather than leaving the two formats agreeing by inspection.

    Takes bytes, so a caller must already have bounded its own read: this function commits no
    memory of its own beyond the hash state, but it also imposes no ceiling.
    """
    return "sha256:" + hashlib.sha256(data).hexdigest()


def file_refs(paths: Sequence[Path | str]) -> list[FileRef]:
    """One `FileRef` per file, **in the order given**.

    Order is preserved all the way into the digest (`combine_file_digests` does not sort) because it
    is the order the files are handed to `oconv`, and the manifest has to be able to tell two
    orderings apart. All paths are stored absolute (docs/tool-surface.md#paths).
    """
    return [FileRef(path=str(Path(p).resolve()), digest=file_digest(p)) for p in paths]


# --------------------------------------------------------------------------------------------
# Advisories
# --------------------------------------------------------------------------------------------


def _advisory(
    code: str, severity: Severity, message: str, subject: str | None = None
) -> EngineAdvisory:
    """A manifest-derived advisory. `lines` is empty -- see the module docstring."""
    return EngineAdvisory(
        code=code, severity=severity, message=message, lines=(), subject=subject
    )


def reuse_advisory(artifact_ids: Sequence[str]) -> EngineAdvisory:
    """`MATRIX_REUSED` -- emitted where a build is actually *skipped*, not where one could be.

    A separate constructor rather than something `describe` emits, because "reusable" and "reused"
    are different claims and only the caller that declined to rebuild knows which one is true.
    """
    return _advisory(
        "MATRIX_REUSED",
        "info",
        f"Reused {len(artifact_ids)} artifact(s) whose provenance is unchanged: "
        + ", ".join(artifact_ids),
    )


# --------------------------------------------------------------------------------------------
# The cross-process manifest lock
# --------------------------------------------------------------------------------------------


@dataclass
class _LockState:
    """One project root's lock, as this process sees it.

    `guard` is what makes the lock re-entrant and thread-safe *inside* one process, and it has to
    exist: `flock` is held per open file description, so two threads that each opened their own
    descriptor would block on each other correctly but one thread nesting `save_manifest` inside a
    caller's `manifest_lock` would deadlock against itself. Holding `guard` first means only ever
    one descriptor per process, taken at depth 0 and released at depth 0.
    """

    guard: threading.RLock = field(default_factory=threading.RLock)
    fd: int | None = None
    depth: int = 0


#: Keyed by **resolved** root, so two spellings of one project are one lock. Entries are never
#: evicted: a second `_LockState` for a root some other thread is currently holding would be a
#: second `guard` for one lock, and the cost of keeping them is a dataclass per project this process
#: has ever written to.
_LOCK_STATES: Final[dict[Path, _LockState]] = {}
_LOCK_STATES_GUARD: Final = threading.Lock()


def _lock_state(root: Path) -> _LockState:
    key = root.resolve()
    with _LOCK_STATES_GUARD:
        state = _LOCK_STATES.get(key)
        if state is None:
            state = _LockState()
            _LOCK_STATES[key] = state
        return state


@contextmanager
def manifest_lock(
    paths: ProjectPaths, *, timeout_s: float = MANIFEST_LOCK_TIMEOUT_S
) -> Iterator[None]:
    """Serialise one project's **read-modify-write** of `manifest.json` across processes.

    Hold this around the whole span, not around the write:

        with manifest_lock(paths):
            project = open_project(paths.root)
            register_input(project, input_id="geo:room", kind="geometry", files=[...])

    Holding it around only the save is the bug, not the fix. `save_manifest` writes the whole
    document with no compare-against-disk, so what loses an update is the *interval* between the
    read at t0 and the write at t1; a lock that covers only t1 serialises two writers who have both
    already read the same stale document and changes nothing about which one survives.

    `save_manifest` takes this lock itself as well, and the nesting above is free because the lock is
    re-entrant per thread. That inner acquisition is a floor, not a substitute for the block above:
    it guarantees no writer's rename lands in the middle of another's critical section, which is
    what makes the disciplined caller's span actually exclusive.

    **A holder that dies never blocks anyone.** The mechanism is `flock(2)` on
    `.manifest.lock`, and the kernel releases a `flock` when the file description closes -- which
    happens on `close`, on a clean exit, and on a `SIGKILL`, a segfault or a power-off reboot alike.
    There is no stale lock to time out, no PID to test for liveness (a test that is wrong the moment
    the PID is reused), and no recovery path for an operator to run. That is the reason for `flock`
    over an `O_CREAT|O_EXCL` lock file: an exclusive-create lock survives its holder's death, and a
    lock that outlives its holder is a worse failure than the lost update it was meant to prevent.

    **Waiting is bounded** by `timeout_s`; exceeding it raises `ManifestLockTimeout`, which the
    register has no code for -- see that class.

    **Lock ordering, so composing with `rad_engine.jobs.JobStore.manifest_lock` cannot deadlock.**
    That one serialises threads within a server; this one serialises processes, and the job worker
    holds both. **The order is: thread lock first, then this one, never the reverse.**

    That is a **rule for callers holding both**, not a guarantee this module can enforce. What is
    guaranteed is only the narrow half: `rad_engine.project` does not import `rad_engine.jobs` and
    cannot -- `jobs` imports `project` -- so nothing *in this module* can block on the thread lock
    while holding this one. `jobs.py` and `rad_mcp` import both and can write either order, and
    writing `with manifest_lock(...): with store.manifest_lock:` anywhere would complete a cycle
    against `JobStore._commit`, which takes them the other way round. There is exactly one place
    that holds both today (`JobStore._commit`) and it takes the thread lock first; that ordering is
    asserted by `tests/test_project.py::test_the_lock_ordering_that_prevents_deadlock_is_taken_thread_first`
    rather than left to be re-derived. Anything that comes to hold both must join that order.

    **On a platform without `fcntl`** the cross-process half is absent and only `guard` applies: two
    servers on one project directory lose updates exactly as they did before this existed. Windows
    is not a supported platform for this server and is not tested; if it becomes one, this is the
    line that needs `msvcrt.locking` behind it.
    """
    if not paths.root.is_dir():
        raise ProjectNotFoundError(f"project directory does not exist: {paths.root}")

    deadline = time.monotonic() + timeout_s
    state = _lock_state(paths.root)
    if not state.guard.acquire(timeout=max(timeout_s, 0.0)):
        raise ManifestLockTimeout(
            f"another thread in this server held the manifest lock on {paths.root} for longer "
            f"than {timeout_s:g}s. Retry: nothing should hold it that long."
        )
    try:
        if state.depth == 0:
            state.fd = _acquire_flock(paths, deadline=deadline, timeout_s=timeout_s)
        state.depth += 1
    except BaseException:
        state.guard.release()
        raise

    try:
        yield
    finally:
        # `release()` in the outermost `finally`, so *nothing* on the way out can strand the guard.
        # An `os.close` that raises -- EBADF, or EIO on some filesystems -- would otherwise leave the
        # RLock held by a dead frame, and every later acquisition on this project in this server
        # would then refuse after the full timeout, for the life of the process. That is the
        # in-process form of exactly the stale lock `flock` was chosen to make impossible, so it
        # cannot be reintroduced here by an unguarded release path.
        try:
            state.depth -= 1
            if state.depth == 0 and state.fd is not None:
                fd, state.fd = state.fd, None
                try:
                    if _fcntl is not None:  # pragma: no branch - fd is None without fcntl
                        _fcntl.flock(fd, _fcntl.LOCK_UN)
                finally:
                    os.close(fd)
        finally:
            state.guard.release()


def _acquire_flock(paths: ProjectPaths, *, deadline: float, timeout_s: float) -> int | None:
    """Take the exclusive `flock`, or raise. `None` where the platform has no `fcntl`.

    Opened `O_RDONLY`: `flock` does not need write access, and requiring it would refuse the lock to
    a second user who can read the project but does not own the lock file.

    **The lock file is created on every platform**, `fcntl` or not (register N-96): it is half of
    how `rad_mcp.export_tool._refuse_project_root_out` (R805) recognises a project root, and
    `create_project` takes this lock, so every project it creates holds `.manifest.lock`. Without
    `fcntl` the file is created and closed and no lock is taken.
    """
    if _fcntl is None:
        os.close(os.open(paths.manifest_lock_file, os.O_RDONLY | os.O_CREAT, 0o644))
        return None

    fd = os.open(paths.manifest_lock_file, os.O_RDONLY | os.O_CREAT, 0o644)
    delay = _LOCK_POLL_MIN_S
    try:
        while True:
            try:
                _fcntl.flock(fd, _fcntl.LOCK_EX | _fcntl.LOCK_NB)
                return fd
            except OSError:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ManifestLockTimeout(
                        f"another process held the manifest lock on {paths.root} for longer than "
                        f"{timeout_s:g}s. It is a live process -- the kernel releases this lock "
                        "when its holder dies -- so something else is writing to this project. "
                        "Retry, or find the other server."
                    ) from None
                time.sleep(min(delay, remaining))
                delay = min(delay * 2, _LOCK_POLL_MAX_S)
    except BaseException:
        os.close(fd)
        raise


# --------------------------------------------------------------------------------------------
# Reading and writing the manifest
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class LoadedProject:
    """An open project: where it lives, what it records, and what loading it had to say."""

    paths: ProjectPaths
    manifest: Manifest
    advisories: tuple[EngineAdvisory, ...] = ()


def save_manifest(paths: ProjectPaths, manifest: Manifest) -> Path:
    """Write `manifest.json` **atomically**: temp file in the same directory, then `os.replace`.

    A half-written manifest must never be readable as a whole one. `os.replace` is atomic within a
    filesystem, so a reader sees either the previous manifest or the new one and never a truncated
    JSON document; the temp file is created in the project directory rather than in `/tmp` so the
    rename cannot cross a filesystem boundary and degrade to a copy.

    The temp name is dot-prefixed so that a crash between `mkstemp` and `replace` leaves something
    `list_projects` and the layout ignore rather than something that looks like a manifest.

    **Atomic is not exclusive.** The rename is taken under `manifest_lock` so it cannot land inside
    another process's read-modify-write, but that acquisition alone does not make a caller safe: the
    document this writes was read somewhere above, and the lock has to cover that read too. A caller
    that modifies a manifest it loaded earlier holds `manifest_lock` across the whole span. The two
    worked examples are in this repo and both open the project *inside* the lock:
    `rad_engine.jobs.JobStore._commit`, which re-reads and seals one step's artifact, and
    `create_project`, which checks for an existing manifest and writes the first one. The nesting is
    free; the lock is re-entrant.
    """
    if not paths.root.is_dir():
        raise ProjectNotFoundError(f"project directory does not exist: {paths.root}")

    payload = manifest.model_dump_json(indent=2) + "\n"
    with manifest_lock(paths):
        handle_fd, temp_name = tempfile.mkstemp(dir=paths.root, prefix=".manifest-", suffix=".tmp")
        temp_path = Path(temp_name)
        try:
            with os.fdopen(handle_fd, "w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                # fsync before the rename: the rename being atomic says nothing about the
                # *contents* having reached the disk before the crash that made atomicity matter.
                os.fsync(handle.fileno())
            # `mkstemp` creates at 0600 and the mode survives the rename, so without this the
            # manifest ends up more private than the artifacts beside it -- and unreadable to a
            # second user who can read the project directory. 0644 rather than a umask dance,
            # which is not thread-safe.
            os.chmod(temp_path, 0o644)
            os.replace(temp_path, paths.manifest)
        except BaseException:
            temp_path.unlink(missing_ok=True)
            raise
        fsync_directory(paths.root)
    return paths.manifest


def fsync_directory(directory: Path) -> None:
    """Persist the rename itself. Best effort: Windows cannot open a directory for reading.

    **Public, and named without the underscore, because it has a caller outside this module.**
    `rad_engine.blend` renames a matrix into place and owes the directory entry the same durability
    this module's own `os.replace` does; importing this rather than writing a fourth copy of six
    lines keeps one definition of "persist a rename" for the engine (`bytes_digest`'s precedent,
    imported by that module for the same reason). `rad_engine.jobs` keeps its own private copy --
    checked, and deliberately not merged in a fix round: it sits on the job supervisor's durability
    path, and the duplicate predates this change.
    """
    _fsync_path(directory)


def _fsync_path(path: Path) -> None:
    """Flush one path's own metadata and data to disk, where the platform allows it."""
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def _commit(project: LoadedProject, snapshot: Manifest) -> None:
    """Persist the manifest, or put the in-memory manifest back the way it was.

    Without the rollback, a write that fails on a full disk leaves `project.manifest` holding a
    change that is not on disk: `is_reusable` would report an uncommitted artifact as reusable, and
    the *next* successful save would silently persist a change that was supposed to have failed.
    "Committed means on disk" has to be true in both directions or it is not a rule.
    """
    try:
        save_manifest(project.paths, project.manifest)
    except BaseException:
        # Restore in place rather than rebinding, so every caller still holding this `Manifest`
        # sees the rollback too.
        project.manifest.project = snapshot.project
        project.manifest.inputs = snapshot.inputs
        project.manifest.artifacts = snapshot.artifacts
        raise


#: The oldest `schema_version` this server will migrate. Fixed at 3, not derived from
#: `SCHEMA_VERSION`: it is a historical fact, not a moving floor. `git log -S` on this module found
#: versions 1 and 2 only in Phase 1 *development* commits (14024f7, a13695a) -- Phase 1 merged to
#: master at 637027d already carrying 3. So no document a released version of this server ever wrote
#: can be older than this, and one that claims to be is not a real old project to migrate; it is
#: indistinguishable from a corrupted or hand-edited file (review round 2, finding I3). An **absent**
#: `schema_version` is refused by the same floor, not treated as "even older and thus in scope
#: anyway": every version of `Project` has always carried the field, with a default, and
#: `save_manifest` never excludes defaults from what it writes -- so a document with no key at all is
#: not a real product of any version of this server either, absent tampering. It is not "the same
#: thing as one that says 1"; it is a *different* kind of suspect, and both land on the same refusal
#: for the same underlying reason: this server has no record of ever producing either shape.
_OLDEST_MIGRATABLE_SCHEMA_VERSION: Final = 3


def _migrate_3_to_4(raw: dict[str, Any]) -> dict[str, Any]:
    """v3 -> v4 (ruling R9): `InputRecord.role` is the only field a v3 document can be missing, so
    this is the whole step. Every input already in the document gets an explicit `"role": null`
    written in (a role a newer caller already wrote -- there should not be one on a document this
    old, but this is not the place to assume it -- passes through unchanged). `null` here is a
    **decision**, not `InputRecord.role`'s own field default read through unmodified: a v3 project's
    inputs have a ground-ness that genuinely is not recoverable from anything the old document
    recorded, and guessing it from an existing `dmx`'s `built_from` would be exactly the false
    confidence check 1.1's ground pair exists to catch -- the old, weaker `REQUIRED_EDGES["dmx"]`
    accepted *any* materials input there, including one that provably was not the ground (the hole
    ruling R9 closes). So this step refuses to guess, and `null` means "not yet told", never
    "confirmed not ground".

    Stamps `schema_version` to `4`, this step's own literal -- not `SCHEMA_VERSION`, which a later
    step inserted between this one and the next must not be able to claim credit for by proxy. Only
    `_migrate_manifest_document`'s dispatch loop below decides when a document has reached
    `SCHEMA_VERSION`.
    """
    migrated = dict(raw)
    project_raw = raw.get("project")
    migrated["project"] = {
        **(project_raw if isinstance(project_raw, dict) else {}),
        "schema_version": 4,
    }

    inputs = raw.get("inputs")
    if isinstance(inputs, list):
        migrated["inputs"] = [
            {**item, "role": item.get("role")} if isinstance(item, dict) else item
            for item in inputs
        ]
    return migrated


def _migrate_4_to_5(raw: dict[str, Any]) -> dict[str, Any]:
    """Stamps `schema_version` to 5 and changes nothing else (R482): schema 5 only adds the luminaire
    input kinds and `12_luminaires/`, which no version-4 record can hold."""
    migrated = dict(raw)
    migrated["project"] = {**raw["project"], "schema_version": 5}
    return migrated


#: One entry per schema version this server can step *from*, to the version immediately after it.
#: `_migrate_manifest_document` walks a document's own stored version up to `SCHEMA_VERSION` one step
#: at a time through this table, rather than applying one generic transform to "anything old" -- the
#: defect review round 2 (finding I3) found in the single-step version of this function: it could not
#: tell a v3 document (needing exactly the role backfill) from a hypothetical v1 or v2 one (needing
#: fields this step never touches), and would have silently stamped either one `SCHEMA_VERSION`
#: regardless. Per-version dispatch makes that impossible by construction: `SCHEMA_VERSION` bumping
#: to 5 without a `4: ...` entry here does not silently reuse this step for a transition it was never
#: written for -- `_migrate_manifest_document` raises instead, on the next document that needs it.
_SCHEMA_MIGRATIONS: Final[Mapping[int, Callable[[dict[str, Any]], dict[str, Any]]]] = {
    3: _migrate_3_to_4,
    4: _migrate_4_to_5,
}


def _migrate_manifest_document(raw: dict[str, Any], *, stored_schema: int) -> dict[str, Any]:
    """A raw document at `stored_schema` (`>= _OLDEST_MIGRATABLE_SCHEMA_VERSION`, `<
    SCHEMA_VERSION` -- both enforced by the caller, `_load_manifest`), migrated to the current shape
    one version at a time through `_SCHEMA_MIGRATIONS`. Ruling R16, and the seam constraint 1 of
    task-4-brief.md names: this module reads and writes the file a migration acts on, so the act of
    migrating is here, even though `SCHEMA_VERSION` and the shapes each step produces belong to
    `rad_core.manifest`.

    Raises (not a `ManifestUnreadableError` -- this is a defect in the server, not in the document)
    if `SCHEMA_VERSION` names a version this table has no step to reach: the safety net that makes
    "the next bump is forced to add its own step" true rather than aspirational.
    """
    version = stored_schema
    while version < SCHEMA_VERSION:
        step = _SCHEMA_MIGRATIONS.get(version)
        if step is None:
            raise AssertionError(
                f"SCHEMA_VERSION is {SCHEMA_VERSION} but _SCHEMA_MIGRATIONS has no step from "
                f"{version} -- a schema bump shipped without the migration step it needs. Add "
                f"`{version}: _migrate_{version}_to_{version + 1}` before this can be read."
            )
        raw = step(raw)
        version += 1
    return raw


#: One note per migration step, keyed by the version stepped *from* -- `_schema_old_message` joins
#: the notes for every step a stored document still has to take, so a schema-3 document names both
#: the role backfill and the luminaire addition, and a schema-4 document names only the latter.
_SCHEMA_STEP_NOTES: Final[Mapping[int, str]] = {
    3: ("Inputs registered before schema 4 carry no role, and a role genuinely absent from the old document is "
        "not guessed at -- so a dmx built from this project's materials will refuse until the ground input's "
        "role is (re-)registered (docs/plan.md, ruling R9)."),
    4: ("Schema 5 adds the luminaire record kinds and the 12_luminaires/ directory; no recorded input or "
        "artifact changes."),
}


def _schema_old_message(stored_schema: int) -> str:
    notes = " ".join(_SCHEMA_STEP_NOTES[v] for v in range(stored_schema, SCHEMA_VERSION))
    return (
        f"This manifest was written against schema version {stored_schema}; it has been migrated in memory to "
        f"the current version {SCHEMA_VERSION}. {notes} The file on disk is rewritten at the current schema "
        "the next time anything registers a change to this project."
    )


#: R743: the deepest a manifest may nest, counting the document object as level 1. The deepest
#: manifest measured from this server's own writes is 8 levels (Phase 16 design M1). Beyond it the MCP
#: SDK client cannot read a reply once about 195 lists nest in a parameter (about 199 levels), and
#: pydantic-core's serialisation limit fails uncoded at 255 (259 levels) (M3, M4). A chosen value; see
#: tool-surface.md, Unsourced values.
MANIFEST_MAX_DEPTH = 64


def _first_load_fault(
    node: object, keys: tuple[str | int, ...] = (), level: int = 1
) -> tuple[tuple[str | int, ...], str, object] | None:
    """The first fault under `node`, depth first in document order: the keys leading to it, its
    kind, and its value; `None` when there is none. A key is a `str` for a JSON object member and
    an `int` for a list index. The kinds:

    - ``"non_finite"`` (R737): a NaN or infinite float. `json.loads` reads `NaN`, `Infinity` and
      `-Infinity` as floats, and an out-of-range literal such as `1e999` as `inf`.
    - ``"huge_int"`` (R743): an `int`, not a `bool`, that no float can hold. JSON integers are
      unbounded and every typed float field already refuses one at validation; the free-form
      `registered_by.arguments` and `parameters` dicts did not, and the value crashed `float()` and
      `math` uncoded at their readers (design M7, M8) or was exported as written (M9).
    - ``"too_deep"`` (R743): a list or object at a level past `MANIFEST_MAX_DEPTH`; its value is
      `None`. The walk never iterates a container past that level, so its deepest call is one level
      past the limit."""
    if isinstance(node, float):
        return None if math.isfinite(node) else (keys, "non_finite", node)
    if isinstance(node, int):  # a `bool` too; `float(True)` converts, so it is never refused
        try:
            float(node)
        except OverflowError:
            return (keys, "huge_int", node)
        return None
    if isinstance(node, dict):
        children = node.items()
    elif isinstance(node, list):
        children = enumerate(node)
    else:
        return None
    if level > MANIFEST_MAX_DEPTH:
        return (keys, "too_deep", None)
    for key, value in children:
        found = _first_load_fault(value, (*keys, key), level + 1)
        if found is not None:
            return found
    return None


def _json_path(keys: tuple[str | int, ...]) -> str:
    """`keys` as a readable path: `artifacts[0].parameters.ab`. A key that is empty or holds `.`,
    `[`, `]` or `"` is written `["…"]` in JSON string quoting, so it reads as one key and never as
    nesting or an index (R744): `assignments["glass.001"]`, `[""]`, `["artifacts[5]"]`."""
    path = ""
    for key in keys:
        if isinstance(key, int):
            path += f"[{key}]"
        elif key and not any(c in key for c in '.[]"'):
            path += f".{key}" if path else key
        else:
            path += f"[{json.dumps(key)}]"
    return path


def _manifest_obstacle(paths: ProjectPaths) -> str | None:
    """What stands at `manifest.json` when it is there but is no regular file (a directory, a FIFO,
    a link that leads to no file), as a phrase -- or `None` when nothing is there or it is a file.
    The final review of 2026-10-05 measured `delete` calling such a directory one with "no
    manifest.json" and `create` calling it "not a directory", both false."""
    try:
        mode = os.lstat(paths.manifest).st_mode
    except OSError:
        return None
    if paths.manifest.is_file():
        return None
    if stat.S_ISDIR(mode):
        return "a directory"
    if stat.S_ISFIFO(mode):
        return "a FIFO"
    if stat.S_ISLNK(mode):
        return "a symbolic link that leads to no regular file"
    return "not a regular file"


def _refuse_manifest_obstacle(paths: ProjectPaths, obstacle: str) -> ManifestUnreadableError:
    return ManifestUnreadableError(
        f"{paths.manifest} is {obstacle}, not a manifest file, so {paths.root} cannot be read as a "
        f"project. Remove what is at {MANIFEST_NAME} and restore the project's manifest, or remove "
        "the directory outside the server."
    )


def _load_manifest(paths: ProjectPaths, *, radiance_version: str | None) -> LoadedProject:
    if not paths.manifest.is_file():
        if (obstacle := _manifest_obstacle(paths)) is not None:
            raise _refuse_manifest_obstacle(paths, obstacle)
        raise ProjectNotFoundError(
            f"no {MANIFEST_NAME} in {paths.root} -- that directory is not a project."
        )
    # **`open_regular_file`, not `read_text` -- and the `is_file()` above is not the guard.**
    # Until 2026-08-09 this was `paths.manifest.read_text(encoding="utf-8")` behind that
    # `is_file()` pre-check, which is precisely the **check-then-open** pattern `safe_io`'s own
    # docstring forbids and that ruling **R50** already corrected once, in `read_epw`: `is_file()`
    # answers about the path at the moment it is asked, and `read_text` re-resolves it at the moment
    # it opens. Swap the file for a FIFO between the two and the open blocks forever, with no escape
    # from an allowed root needed -- `os.mkfifo` inside the project's own directory is enough.
    #
    # That matters more here than at any other site this rule covers: **this is the hottest path in
    # the server.** `_load_manifest` runs on every project open, so every tool call that names a
    # project passes through this open. Wedged, it takes an `anyio` worker thread with it, and that
    # pool's default limiter is 40 -- forty of them stall every tool the server offers, not merely
    # the one that was called. Check 0.9 passed green with this site unguarded because its receiver
    # test walked only bare `Name` nodes and `paths` is annotated `ProjectPaths`; the scan now
    # resolves attribute receivers, and this was the one live site it found.
    #
    # `open_regular_file` carries `O_NOFOLLOW|O_NONBLOCK` and `S_ISREG`-checks the descriptor, so
    # the decision is made about the file that was actually opened rather than about a path that has
    # since changed. It yields bytes, hence the explicit `.decode("utf-8")` -- `json.loads` accepts
    # bytes, but decoding here keeps a malformed-encoding failure a `ValueError` reported against
    # this file rather than a `UnicodeDecodeError` from inside the parser.
    #
    # The `except` clause is unchanged and already correct for both new failure shapes:
    # `NotARegularFileError` is an `OSError` subclass, and **`O_NOFOLLOW` surfaces a symlink as a
    # raw `OSError` errno 62 (ELOOP) before any descriptor exists**, so `NotARegularFileError` never
    # fires for that case -- both land on `OSError` and become `ManifestUnreadableError`, which is
    # the coded refusal a caller already handles.
    try:
        with open_regular_file(paths.manifest) as handle:
            raw = json.loads(handle.read().decode("utf-8"))
    except RecursionError as exc:
        # R743: `json.loads` recurses once per level and raises `RecursionError`, a `RuntimeError`,
        # at about the interpreter's recursion limit (design M3: 978 lists nested in a parameter, 982
        # levels, through open_project at the default 1000; the depth moves with the caller's stack).
        raise ManifestUnreadableError(
            f"{paths.manifest} is nested too deeply to read ({exc}). Restore the manifest from a "
            "backup, or remove the project directory outside this server: "
            'project(action="delete", confirm=true, force=true) must re-read the document for the '
            "project's name, and refuses when it cannot."
        ) from exc
    except OSError as exc:
        # R830 (register L-127, fix round 1): the file did not open (a permission, a symlink to a
        # regular file, a disk error; a directory, FIFO or dangling symlink is refused
        # `PROJECT_NOT_FOUND` by the `is_file()` pre-check above and reaches here only through a race
        # after it), so the document may be sound and a backup is not the remedy.
        # `_delete_evidence_without_a_manifest` opens it the same way, so while it will not open a
        # force delete cannot read its project name either.
        raise ManifestUnreadableError(
            f"{paths.manifest} could not be read: {exc}. Make it a regular file this server can "
            "read (check its permissions, and that it is not a symlink), or remove the project "
            "directory outside this server; while it cannot be opened, a force delete cannot read "
            "this document's project name either."
        ) from exc
    except ValueError as exc:
        # R830 (register L-127): the bytes opened and did not parse (an integer of more than 4300
        # digits among them). `_delete_evidence_without_a_manifest` re-reads them with the same
        # `json.loads`, so force delete gets no project name either; the message says so.
        raise ManifestUnreadableError(
            f"{paths.manifest} could not be read: {exc}. Restore the manifest from a backup, or "
            "remove the project directory outside this server; a force delete cannot read this "
            "document's project name."
        ) from exc
    if not isinstance(raw, dict) or not isinstance(raw.get("project"), dict):
        raise ManifestUnreadableError(f"{paths.manifest} is not a manifest document.")

    # R737, R743: JSON has no NaN or infinity and this server never writes one (pydantic writes a
    # non-finite float as `null`); every typed float field refuses an integer a float cannot hold
    # (design M8), though a typed `int` field such as `ZoneRecord.points` accepts one; and the deepest
    # server-written manifest measured nests 8 levels (design M1, the deepest seen, not a proof). A
    # sweep of tool arguments on 2026-09-29 found server writes that record such an integer only in
    # `bsdf generate`: a Klems run's unused `resolution` (measured with genBSDF; now refused), and a
    # huge `samples`, given or computed from a tensor4 `resolution` of 511 or more, recorded only if
    # genBSDF exits 0, which it did not in the real runs measured. Measured at `daa4d7c` and
    # `2f39354`: each fault made `describe`, `update`, `export`, `luminaire` or a run tool escape
    # uncoded, or was exported as written. Refused here, once, before anything reads or writes the
    # document.
    found = _first_load_fault(raw)
    if found is not None:
        keys, kind, value = found
        owner = ""
        # The keys actually walked, not the path text: a member literally named "artifacts[5]" is a
        # `str` key here, never an index into the real list (R737, Task 3 review, fix rounds 1 and 2).
        if len(keys) >= 2 and keys[0] in ("artifacts", "inputs") and isinstance(keys[1], int):
            record = raw[keys[0]][keys[1]]
            if isinstance(record, dict) and isinstance(record.get("id"), str):
                owner = f" (record {record['id']!r})"
        where = _json_path(keys)
        # R830 (register L-128): force delete still refuses a document naming another directory,
        # or a top level holding anything beyond the layout (`_delete_evidence_without_a_manifest`).
        force_delete = (
            'project(action="delete", confirm=true, force=true) can still remove the project '
            "when the directory's name matches the document's and the directory holds only the "
            "project layout"
        )
        recovery = f"or restore the manifest from a backup; {force_delete}."
        if kind == "non_finite":
            message = (
                f"{paths.manifest} holds a non-finite number ({value!r}) at {where}{owner}. JSON has "
                "no such number and this server never writes one, so it came from an edit outside "
                f"the server. Replace it with a finite number {recovery}"
            )
        elif kind == "huge_int":
            message = (
                f"{paths.manifest} holds an integer of {value.bit_length()} bits "  # type: ignore[attr-defined]
                f"at {where}{owner}, "
                f"too large for a float. Replace it with a number a float can hold {recovery}"
            )
        else:
            message = (
                f"{paths.manifest} nests more than {MANIFEST_MAX_DEPTH} levels deep at "
                f"{where}{owner}. Remove the nesting or restore the manifest from a backup; "
                f"{force_delete}, "
                "unless the document is nested so deep that it cannot read it either, and then it says so."
            )
        raise ManifestUnreadableError(message)

    project_raw: dict[str, Any] = raw["project"]
    advisories: list[EngineAdvisory] = []

    # Read the schema version from the *raw* document: `Project.schema_version` has a default, so a
    # manifest that omits it would validate as current and the advisory would never fire.
    stored_schema = project_raw.get("schema_version")
    if not isinstance(stored_schema, int) or isinstance(stored_schema, bool):
        stored_schema = None

    if stored_schema is not None and stored_schema > SCHEMA_VERSION:
        raise ManifestUnreadableError(
            f"{paths.manifest} was written against schema version {stored_schema}, newer than this "
            f"server's {SCHEMA_VERSION}. Reading it would discard whatever that version added."
        )
    if stored_schema is None or stored_schema < _OLDEST_MIGRATABLE_SCHEMA_VERSION:
        # Review round 2, finding I3: a single-step migration could not tell a real old project from
        # a document no released version of this server ever produced, and stamped either one
        # SCHEMA_VERSION regardless -- see `_OLDEST_MIGRATABLE_SCHEMA_VERSION`'s own comment for why
        # both an absent field and an explicit 1 or 2 land here rather than in the migrate branch
        # below.
        raise ManifestUnreadableError(
            f"{paths.manifest} declares schema version "
            f"{stored_schema if stored_schema is not None else 'absent'}, older than "
            f"{_OLDEST_MIGRATABLE_SCHEMA_VERSION}, the oldest version this server has ever written to "
            "disk. This is not a legitimately old project to migrate -- "
            "migrating it would guess at a shape nothing produced -- so it is refused rather than "
            "silently stamped current. Restore it from backup, or start a new project."
        )
    if stored_schema < SCHEMA_VERSION:
        # Migrate the *raw* document before anything validates it, so `manifest.project.schema_version`
        # below is this function's own literal and never `Project.schema_version`'s pydantic default
        # filling in silently -- the write-back gap ruling R16 exists to close (see
        # `_migrate_manifest_document` and task-4-report.md for what was measured). This does not
        # itself write `paths.manifest` back to disk: the migrated document lives only in the
        # `Manifest` this call returns until something else -- `register_input`, `register_artifact`,
        # `update_project`, `archive_project` -- next commits a change to this project, at which point
        # `save_manifest` writes the now-current shape as an ordinary side effect of that write. A
        # project only ever `describe`d or `list`ed keeps re-migrating in memory and re-warning on
        # every load, which is honest: its file on disk genuinely is still the old shape.
        raw = _migrate_manifest_document(raw, stored_schema=stored_schema)
        project_raw = raw["project"]
        advisories.append(
            _advisory("MANIFEST_SCHEMA_OLD", "warn", _schema_old_message(stored_schema))
        )

    if not project_raw.get("units"):
        raise ProjectUnitsUnsetError(
            f"{paths.manifest} records no project units. Every length in the project is read "
            "through them, and a scene modelled in feet and read as metres is wrong by 3.28x with "
            "no error anywhere, so this is refused rather than defaulted."
        )

    try:
        manifest = Manifest.model_validate(raw)
    except ValidationError as exc:
        raise ManifestUnreadableError(f"{paths.manifest} does not validate: {exc}") from exc

    if radiance_version is not None and radiance_version != manifest.project.radiance_version:
        stale = [a.id for a in manifest.artifacts if a.radiance_version != radiance_version]
        advisories.append(
            _advisory(
                "RADIANCE_VERSION_CHANGED",
                "warn",
                f"Radiance is now {radiance_version!r}; this project last recorded "
                f"{manifest.project.radiance_version!r}. "
                + (
                    f"{len(stale)} artifact(s) predate it and are flagged, not invalidated: "
                    + ", ".join(stale)
                    if stale
                    else "No artifact predates it."
                ),
            )
        )
        # The field means "the install's *current* version" (rad_core.manifest.Project), and
        # `evaluate` compares each artifact against it. Setting it here is what makes the flag land
        # on the artifacts rather than on nothing. It reaches disk only when something else writes.
        manifest.project.radiance_version = radiance_version

    return LoadedProject(paths=paths, manifest=manifest, advisories=tuple(advisories))


def open_project(root: Path | str, *, radiance_version: str | None = None) -> LoadedProject:
    """Open the project whose directory is `root` (already validated and resolved -- see module doc).

    `radiance_version` is the *live* install's version string, from `RadianceEnv.version`. Passing
    it is what makes `RADIANCE_VERSION_CHANGED` detectable; omitting it evaluates the project
    against the version it last recorded. A version change flags and never invalidates, because
    recomputing every matrix on an upgrade is hours for a 5-phase project
    (docs/tool-surface.md#how-provenance-invalidates-a-matrix).
    """
    return _load_manifest(ProjectPaths(Path(root)), radiance_version=radiance_version)


# --------------------------------------------------------------------------------------------
# create / list / delete / archive
# --------------------------------------------------------------------------------------------


def _validated_ground_reflectance(value: float | None) -> float | None:
    """Refuse a `ground_reflectance` no project should store, **before** anything is written.

    Called from `create_project` and `update_project` -- the two entry points that persist the
    value -- and deliberately not from `resolve_ground_reflectance`, which lives in `rad_core` and
    cannot raise a `ProjectError` without inverting the layer rule (CONTEXT.md, Layers).

    `None` passes through untouched: it is the distinct instruction *reset to the labelled default*,
    not a value.
    """
    if value is None:
        return None
    if not math.isfinite(value):
        raise GroundReflectanceRangeError(
            f"ground_reflectance={value!r} is not a finite number. It is written into the manifest "
            "as a JSON number and read back as one, and a non-finite float serialises as null -- "
            "which this server cannot read, so storing it would leave the project unopenable. "
            "Pass a fraction between 0 and 1."
        )
    if not 0.0 <= value <= 1.0:
        raise GroundReflectanceRangeError(
            f"ground_reflectance={value!r} is outside [0, 1]. Ground reflectance is the fraction of "
            "incident light the ground returns, and this value is passed verbatim to gensky -g and "
            "gendaymtx -g, where it scales the sky matrix's ground patch. A value outside [0, 1] is "
            "not a reflectance and would be simulated without complaint. Pass a fraction between 0 "
            "and 1 (0.20 is gensky's own default; LM-83 uses 0.10)."
        )
    return value


def _validated_scalars(scalars: Mapping[str, float] | None) -> dict[str, float]:
    """Refuse any `scalars` value a manifest cannot carry, **before** anything is written.

    `_validated_ground_reflectance`'s sibling, for the reasons `ScalarNotNumberError` and
    `ScalarNotFiniteError` state: that guard covers one project field of its own, `scalars` reaches
    `Project.scalars: dict[str, float]` with no wrapper and had none. Called from `create_project`
    only -- `update_project` has no `scalars` argument to validate, because nothing sets one after
    creation (module docstring, "Ground reflectance has exactly one home"; `scalars` has never had a
    second write path either).
    """
    if not scalars:
        return {}
    validated: dict[str, float] = {}
    for scalar_name, value in scalars.items():
        # Before `math.isfinite`, which raises a bare `TypeError` on a str and passes a `bool`.
        # See `ScalarNotNumberError`.
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ScalarNotNumberError(
                f"scalars[{scalar_name!r}]={value!r} is not a number. It is written into the "
                "manifest as a JSON number and reaches a Radiance command line as one"
                + (
                    "; a boolean would be stored as 1.0 or 0.0 without comment"
                    if isinstance(value, bool)
                    else ""
                )
                + ". Pass an int or a float.",
                field=f"scalars.{scalar_name}",
            )
        try:
            finite = math.isfinite(value)
        except OverflowError:
            # An `int` no double can hold (`10**400`): `math.isfinite` converts it first and
            # raised a bare `OverflowError`. Its size, not its `repr`, goes in the message: past
            # 4300 digits `repr` of an `int` raises `ValueError` itself.
            raise ScalarNotFiniteError(
                f"scalars[{scalar_name!r}] is an integer of {value.bit_length()} bits, too large "
                "for a float: `Project.scalars` holds floats, and no finite float represents "
                "it. Pass a finite number.",
                field=f"scalars.{scalar_name}",
            ) from None
        if not finite:
            raise ScalarNotFiniteError(
                f"scalars[{scalar_name!r}]={value!r} is not a finite number. It is written into "
                "the manifest as a JSON number and read back as one, and a non-finite float "
                "serialises as null -- which this server cannot read, so storing it would leave "
                "the project unopenable from the moment it was created. Pass a finite number.",
                field=f"scalars.{scalar_name}",
            )
        validated[scalar_name] = value
    return validated


def create_project(
    parent: Path | str,
    *,
    name: str,
    units: Units,
    radiance_version: str,
    ground_reflectance: float | None = None,
    ground_reflectance_provenance: Provenance = "assumed",
    ground_reflectance_source: str | None = None,
    description: str | None = None,
    scalars: Mapping[str, float] | None = None,
    created_at: datetime | None = None,
) -> LoadedProject:
    """Lay out `<parent>/<name>/` and write its first manifest.

    `parent` is the already-validated, already-resolved absolute path the `project` tool's `root`
    argument produced; it is **required and has no default** -- there is no server workspace to fall
    back to, and inventing one would settle the still-open question of where projects live
    (docs/plan.md#still-open, item 1) by omission.

    `units` is required with no default for the reason in
    docs/tool-surface.md#required-with-no-default.

    `ground_reflectance` unset stores `0.20` labelled `default` with source `gensky -defaults` --
    the binary default made explicit rather than inherited silently, and never an absent field
    (check 1.10).
    """
    parent = Path(parent)
    if not parent.is_dir():
        raise ProjectNotFoundError(
            f"the parent directory for a new project must already exist: {parent}"
        )

    # Refused here, before the `mkdir` below and before any manifest is written: a value the
    # manifest cannot carry must never reach the point where it has been half-written. See
    # `GroundReflectanceRangeError`.
    ground_reflectance = _validated_ground_reflectance(ground_reflectance)
    # Same reason, same place: `scalars` reaches `Project.scalars` with no `ProvenanceValue`
    # wrapper of its own, and had no boundary guard either. See `ScalarNotFiniteError`.
    scalars = _validated_scalars(scalars)

    paths = ProjectPaths.for_project(parent, name)
    # **An occupied directory is refused BEFORE the mkdir, and the ordering is the whole point.**
    # `mkdir(exist_ok=True)` adopts whatever is already at `<parent>/<name>`, and the manifest check
    # below it cannot catch that: an ordinary user folder has no `manifest.json`, so
    # `ProjectExistsError` never fired, the twelve layout directories were created *inside* the
    # user's files, and the manifest written beside them recorded zero inputs and zero artifacts --
    # which is exactly what satisfies `delete_project`'s no-`force` guard. Two well-formed tool
    # calls, no symlink and no attacker then removed a folder holding ten thousand files.
    # Reproduced 2026-08-01 against a directory containing `cad/tower.dwg` and `invoices.xlsx`.
    #
    # `ProjectExistsError` rather than a code of its own, because this is materially the refusal
    # already written down: *"silently adopting an existing directory is how you get a manifest
    # that describes a different scene than the files"* (docs/tool-surface.md#2-project). An
    # existing **empty** directory is still adopted -- that is the retry after a create that failed
    # on its manifest write, and it discards nothing.
    if paths.root.is_dir() and not paths.manifest.exists():
        # A directory that *does* hold a manifest falls through to the check under the lock, which
        # is the same refusal with the more useful sentence -- and which is the one that is also
        # race-free against a second server.
        occupants = sorted(entry.name for entry in paths.root.iterdir())
        if occupants:
            raise ProjectExistsError(
                f"{paths.root} already exists and is not empty -- it holds {len(occupants)} "
                f"entry/entries ({', '.join(occupants[:5])}"
                f"{', ...' if len(occupants) > 5 else ''}) and no {MANIFEST_NAME}. Laying a project "
                "out inside it would give it a manifest describing a scene those files are not, and "
                "the directory would then be deletable through this tool. Choose another name, or "
                "open the project if one is already there."
            )
    elif paths.root.is_dir() and (obstacle := _manifest_obstacle(paths)) is not None:
        raise ProjectExistsError(
            f"{paths.root} already exists, and its {MANIFEST_NAME} is {obstacle}, not a manifest file. "
            f"Remove what is at {paths.manifest} if this directory should become a project, or choose "
            "another name."
        )
    elif not paths.root.is_dir() and (paths.root.exists() or paths.root.is_symlink()):
        raise ProjectExistsError(
            f"{paths.root} already exists and is not a directory. A project is a directory with a "
            f"{MANIFEST_NAME} in it; this is something else, and creating over it would destroy it."
        )
    # The directory before the *manifest* check, because that check has to happen **under the lock**
    # and the lock file lives inside the directory. Two servers creating one project both passed the
    # old unlocked check and both wrote; one empty manifest won, the loser's ground reflectance and
    # description were discarded, and the loser was handed a `LoadedProject` that did not match
    # disk. `mkdir(exist_ok=True)` creates nothing where a project already exists, so this reorder
    # adds no directory that the old order would not also have left behind on a failed write.
    paths.root.mkdir(exist_ok=True)
    with manifest_lock(paths):
        if paths.manifest.exists():
            raise ProjectExistsError(
                f"{paths.manifest} already exists. Creating over it would leave a manifest "
                "describing a different scene than the files beside it; open the project instead, "
                "or choose another name."
            )

        for directory in paths.directories():
            directory.mkdir(parents=True, exist_ok=True)

        manifest = Manifest(
            project=Project(
                name=paths.name,
                units=units,
                radiance_version=radiance_version,
                created_at=created_at or datetime.now(UTC),
                description=description,
                ground_reflectance=resolve_ground_reflectance(
                    ground_reflectance,
                    provenance=ground_reflectance_provenance,
                    source=ground_reflectance_source,
                ),
                scalars=scalars,
            )
        )
        save_manifest(paths, manifest)
    return LoadedProject(paths=paths, manifest=manifest)


@dataclass(frozen=True)
class ProjectSummary:
    """One row of `project(action="list")`."""

    name: str
    root: Path
    units: str | None = None
    artifacts_valid: int = 0
    artifacts_invalid: int = 0
    last_job_at: datetime | None = None
    error: str | None = None
    """Why this project could not be read. Surfaced rather than skipped: a project that silently
    vanishes from the list reads as deleted."""


def list_projects(parent: Path | str) -> list[ProjectSummary]:
    """Every project directly under `parent`, by name.

    `last_job_at` is the modification time of the newest entry in `10_jobs/`. Read from the
    directory rather than from a job record because `rad_engine.jobs` owns that format and this
    module must not assume it; a job directory is created when the job starts, which is what the
    field means.
    """
    parent = Path(parent)
    if not parent.is_dir():
        raise ProjectNotFoundError(f"not a directory: {parent}")

    summaries: list[ProjectSummary] = []
    for child in sorted(parent.iterdir()):
        if not child.is_dir() or not (child / MANIFEST_NAME).is_file():
            continue
        paths = ProjectPaths(child)
        try:
            loaded = _load_manifest(paths, radiance_version=None)
        except ProjectError as exc:
            summaries.append(ProjectSummary(name=child.name, root=child, error=str(exc)))
            continue
        try:
            statuses = evaluate(loaded.manifest)
        except ManifestError as exc:
            summaries.append(ProjectSummary(name=child.name, root=child, error=str(exc)))
            continue
        summaries.append(
            ProjectSummary(
                # **The directory name, not the manifest's.** This list is a list of *handles*, and
                # the handle is the directory name: `describe(name=...)` resolves `<root>/<name>`
                # and never reads a manifest to find one. Reporting `manifest.project.name` here
                # listed a hand-renamed directory under a name no other action could open -- the
                # error rows already used `child.name`, so the two halves of one list disagreed
                # about what a row was called.
                name=child.name,
                root=child,
                units=loaded.manifest.project.units,
                artifacts_valid=sum(1 for s in statuses.values() if s.valid),
                artifacts_invalid=sum(1 for s in statuses.values() if not s.valid),
                last_job_at=_last_job_at(paths),
            )
        )
    return summaries


def _last_job_at(paths: ProjectPaths) -> datetime | None:
    if not paths.jobs.is_dir():
        return None
    times = [entry.stat().st_mtime for entry in paths.jobs.iterdir() if entry.is_dir()]
    return datetime.fromtimestamp(max(times), UTC) if times else None


#: The top-level names a project directory is laid out with. `09_images/hdr` contributes
#: `09_images`, because what `delete_project` inspects is the root's own entries.
_LAYOUT_TOP_LEVEL: Final[frozenset[str]] = frozenset(
    relative.split("/", 1)[0] for relative in LAYOUT_DIRECTORIES
)


#: The exact shape `save_manifest`'s `tempfile.mkstemp(dir=root, prefix=".manifest-", suffix=".tmp")`
#: produces, and nothing else. **One pattern rather than two clauses joined by `and`**, and the
#: reason is the whole-branch test review's finding F7: as
#: `startswith(".manifest-") and endswith(".tmp")`, widening the `and` to an `or` left the entire
#: 5 301-test gate green *and* turned `delete_project(force=True)` on a root holding a user's
#: `notes.tmp` from a refusal into a deletion of it. A pattern has no boolean operator between its
#: halves to widen, so that specific mutation cannot be expressed here at all -- and it is tighter on
#: its own terms: `.manifest-` alone, and `foo.tmp` alone, are both foreign names and are now both
#: orphans. `mkstemp`'s random component is 8 characters from `[a-z0-9_]`; the class is widened to
#: include upper case and the length left open so a future CPython that changes either does not turn
#: this server's own crash leftovers into a refusal to delete.
_MANIFEST_TEMP_NAME: Final[re.Pattern[str]] = re.compile(r"\A\.manifest-[A-Za-z0-9_]+\.tmp\Z")


#: `run_electric`'s temporary octree (Phase 11b R611): `tempfile.mkstemp(dir=root, prefix=".electric-",
#: suffix=".oct")`, deleted in a `finally`. A crash leftover must not turn `delete_project` into a
#: refusal. One pattern, not two joined clauses, for `_MANIFEST_TEMP_NAME`'s reason (finding F7).
#: **Unlike `_MANIFEST_TEMP_NAME`, the random component is `mkstemp`'s exact shape**, eight characters
#: of `[a-z0-9_]` (`tempfile._RandomNameSequence`), so a user's own dot-named file whose middle is
#: not exactly eight `[a-z0-9_]` characters (such as `.electric-notes.oct`) is an orphan that
#: `delete_project` refuses over (register N-97). One of `mkstemp`'s exact shape
#: (`.electric-mynotes1.oct`) cannot be told from a crash leftover and is deleted as one. `tests/test_project.py::test_an_mkstemp_name_is_an_orphan_temp` makes real
#: `mkstemp` names under each prefix, so a CPython that changed the shape goes red there.
_ELECTRIC_OCTREE_TEMP_NAME: Final[re.Pattern[str]] = re.compile(r"\A\.electric-[a-z0-9_]{8}\.oct\Z")


#: `run_view_and_sunlight`'s temporary octrees (Phase 12 final fix wave, security review S-M3):
#: `tempfile.mkstemp(dir=root, prefix=".view-out-" or ".sunlight-", suffix=".oct")`, deleted in a
#: `finally` -- `rad_mcp.view_sunlight_tool.VIEW_OUT_OCTREE_PREFIX` and `SUNLIGHT_OCTREE_PREFIX`.
#: Exempt on `_ELECTRIC_OCTREE_TEMP_NAME`'s terms, and only as a regular file (`_is_temp_leftover`).
_VISIBILITY_OCTREE_TEMP_NAME: Final[re.Pattern[str]] = re.compile(
    r"\A\.(?:view-out|sunlight)-[a-z0-9_]{8}\.oct\Z"
)


#: `run_ugr`'s temporary octree (Phase 13 Branch C, C4): `tempfile.mkstemp(dir=paths.root,
#: prefix=".ugr-", suffix=".oct")` -- `rad_mcp.ugr_tool.UGR_OCTREE_PREFIX`, deleted in a
#: `finally`. A crash leftover must not turn `delete_project` into a refusal. Exempt on
#: `_ELECTRIC_OCTREE_TEMP_NAME`'s terms. `run_ugr`'s second temporary, the submitted glarefile
#: copy (`suffix=".glr"`), is `mkstemp`ed under `paths.results`, not `paths.root`; the scan below
#: only reads `paths.root`'s own entries, so that one is already outside its reach and gets no
#: pattern (measured, R706).
_UGR_OCTREE_TEMP_NAME: Final[re.Pattern[str]] = re.compile(r"\A\.ugr-[a-z0-9_]{8}\.oct\Z")


def _orphan_entries_against(paths: ProjectPaths, recorded: frozenset[str]) -> list[str]:
    """`orphan_entries`' body, with the manifest-recorded names supplied rather than derived.

    Split out for `delete_project`'s unreadable-manifest recovery, which has no parsed manifest to
    derive them from and so passes the empty set -- strictly the most conservative reading, since
    every top-level file then counts as an orphan.
    """
    allowed = _LAYOUT_TOP_LEVEL | {MANIFEST_NAME, MANIFEST_LOCK_NAME} | recorded
    return sorted(
        entry.name
        for entry in paths.root.iterdir()
        if entry.name not in allowed
        and not (_is_temp_leftover(entry)
                 and (_MANIFEST_TEMP_NAME.fullmatch(entry.name)
                      or _ELECTRIC_OCTREE_TEMP_NAME.fullmatch(entry.name)
                      or _VISIBILITY_OCTREE_TEMP_NAME.fullmatch(entry.name)
                      or _UGR_OCTREE_TEMP_NAME.fullmatch(entry.name)))
    )


def _is_temp_leftover(entry: Path) -> bool:
    """Phase 11b final fix wave, S5b: a temp-file name is exempt only as what this server leaves there
    -- a **regular file, not a symlink** (`lstat`, never following). `mkstemp` makes nothing else, so a
    directory named `.electric-x.oct` holding a user's work, or a symlink at a `.manifest-*.tmp` name,
    is a foreign entry and stays an orphan: `delete_project(force=True)` used to delete it."""
    try:
        return stat.S_ISREG(entry.lstat().st_mode)
    except OSError:
        return False


def orphan_entries(paths: ProjectPaths, manifest: Manifest) -> list[str]:
    """Top-level names in `paths.root` that neither the layout nor `manifest` accounts for.

    The evidence `delete_project` refuses on. Everything this module ever writes into a project root
    is one of: a layout directory, `manifest.json`, `.manifest.lock`, or a `save_manifest` temp file
    that a crash left behind. An artifact or an input file *may* sit at the top level if a caller
    put it there, so the manifest's own paths count as accounted for too -- which is what keeps this
    a check on *foreign* files rather than a check on tidiness.

    **`Path(ref.path).parent == root` is load-bearing and is tested as such.** A manifest path that
    is *nested* (`03_grids/floor.pts`) contributes nothing to `recorded`: its basename is not a
    top-level name, and admitting it would let a foreign top-level file called `floor.pts` stop
    being an orphan on the strength of a record about a different file. Both comparisons survived
    mutation to `!=` against 346 tests before 2026-08-31; the pins are in `tests/test_project.py`.
    """
    root = paths.root.resolve()
    recorded = frozenset(
        {
            Path(ref.path).name
            for record in manifest.inputs
            for ref in record.files
            if Path(ref.path).parent == root
        }
        | {Path(a.path).name for a in manifest.artifacts if Path(a.path).parent == root}
    )
    return _orphan_entries_against(paths, recorded)


def _delete_evidence_without_a_manifest(
    paths: ProjectPaths, *, force: bool, cause: ManifestUnreadableError
) -> None:
    """Stand in for `delete_project`'s parsed-manifest evidence when the document will not validate.

    Returns nothing and raises on every refusal: the caller proceeds only if this returns.

    The ordinary path proves the tree is the project with two facts from the parsed manifest -- it
    names *this* directory, and the root holds nothing the layout and the manifest cannot account
    for. Both are re-established here from weaker material, and the substitutes are **strictly more
    conservative than the originals**, which is the only basis on which `force` may proceed:

    - the raw JSON must still parse far enough to yield `project.name`, and it must equal the
      directory's own name. `json.loads` needs none of the pydantic model, so the `null`
      `ground_reflectance` that produced the Critical -- and a bad enum, a missing field, an
      unmigratable `schema_version` -- all still answer it. A document too damaged to yield even
      that answers nothing, and the original `MANIFEST_UNREADABLE` is re-raised;
    - `orphan_entries` is evaluated with **no** recorded names at all, so a top-level artifact or
      input file that the ordinary path would have accounted for counts as an orphan here and
      refuses the delete. The unparsed manifest cannot vouch for such a file, and guessing that it
      would have is exactly the guess this function exists not to make.

    `force` is required. Without it the ordinary path reports *how much work* the delete discards
    (`PROJECT_NOT_EMPTY`, counting inputs and artifacts), and that count comes from the document
    that will not parse -- so an un-forced delete here would be consent given against an unstated
    price. The refusal says so and names `force` as the answer.
    """
    root_name = paths.root.name
    try:
        with open_regular_file(paths.manifest) as handle:
            raw = json.loads(handle.read().decode("utf-8"))
        declared = raw["project"]["name"]
    # R743: a document too deep for `json.loads` yields no project name either.
    except (OSError, ValueError, LookupError, TypeError, RecursionError) as exc:
        raise ManifestUnreadableError(
            f"{paths.manifest} could not be read, and it does not even yield a project name, so "
            "there is nothing to check this directory against. force is permission to delete a "
            "project, not permission to delete an arbitrary tree, so it does not override this. "
            f"Remove the directory outside this server. (Underlying failure: {cause})"
        ) from exc

    if declared != root_name:
        raise ProjectTreeUnrecognisedError(
            f"{paths.root} holds an unreadable manifest that names a project {declared!r}, not "
            f"{root_name!r}. A manifest that describes a different project is not evidence that "
            "this directory is one, and it is no better evidence for being unreadable. Rename the "
            "directory back, or remove it outside this server."
        )

    if not force:
        raise ProjectNotEmptyError(
            f"{paths.manifest} does not validate, so this delete cannot report what it would "
            "discard -- the inventory of inputs and artifacts is in the document that will not "
            "parse. Deleting anyway removes the directory and everything in it; pass force to "
            f"confirm you meant that. (Underlying failure: {cause})"
        )

    orphans = _orphan_entries_against(paths, frozenset())
    if orphans:
        raise ProjectTreeUnrecognisedError(
            f"{paths.root} holds {len(orphans)} top-level entry/entries "
            f"({', '.join(orphans[:5])}{', ...' if len(orphans) > 5 else ''}), and its manifest "
            "does not validate, so nothing can say whether this project put them there. force is "
            "permission to delete a project, not permission to delete an arbitrary tree, so this "
            "refusal is not overridden by it. Move them out first, or remove the directory outside "
            "this server."
        )


def delete_project(
    paths: ProjectPaths, *, force: bool = False, has_running_job: bool = False
) -> list[Path]:
    """Remove a project directory. Returns what was removed, top level first.

    Refuses without `force` on a project that has anything recorded in it, and on one with a
    running job -- "the job writes into the directory being removed"
    (docs/tool-surface.md#2-project). `has_running_job` is passed in rather than read from
    `rad_engine.jobs`: the queue is the *job* module's state, and a directory-layout module that
    reached into it would make the two depend on each other in both directions.

    It also refuses, `force` or not, to remove a directory with no `manifest.json` in it. **`force`
    is permission to delete a project, not permission to delete an arbitrary tree** -- and the
    presence of a manifest is not, on its own, proof that the tree *is* the project. Two more
    checks make it proof, and `force` overrides neither:

    - the manifest must describe **this** directory (`project.name == root.name`). A manifest copied
      or moved into a directory says nothing about what else is in it;
    - the root must hold nothing the layout and the manifest cannot account for (`orphan_entries`).

    Without those, `create` adopting a pre-existing user folder -- which it no longer does, see
    `create_project` -- left a manifest recording zero inputs and zero artifacts beside ten thousand
    of the user's files, and that manifest satisfied the `force`-free guard below all by itself.
    Both halves were reproduced end to end on 2026-08-01; either one alone leaves the pair open.

    **A manifest that will not validate is recoverable with `force`, and only with it.** See
    `_delete_evidence_without_a_manifest` for what stands in for the parsed document and why the
    substitute is strictly more conservative than the ordinary path.
    """
    if not paths.manifest.is_file():
        if (obstacle := _manifest_obstacle(paths)) is not None:
            raise _refuse_manifest_obstacle(paths, obstacle)
        raise ProjectNotFoundError(
            f"{paths.root} has no {MANIFEST_NAME} and is not a project. Refusing to remove it."
        )

    # Loaded unconditionally, `force` or not: this is the evidence that the tree is the project,
    # and `force` is permission to delete a project.
    try:
        loaded = _load_manifest(paths, radiance_version=None)
    except ManifestUnreadableError as exc:
        # **The one place `force` reaches past a `MANIFEST_UNREADABLE`, and it does not reach past
        # the *evidence*.** Until 2026-08-31 this raised straight through, on the reasoning that
        # *"a manifest that will not parse cannot supply the evidence, so `MANIFEST_UNREADABLE`
        # surfaces here rather than being bypassed by `force`"*. That reasoning is right about
        # what the evidence is for and wrong about there being no other way to get it, and the
        # cost was the Critical the whole-branch security review found: a project whose manifest
        # this server had itself written un-readable could not be `describe`d, `update`d **or**
        # `delete`d, and the only recovery was hand-editing `manifest.json` outside the server.
        # A destructive tool that can create a state it cannot clear is not a safe tool, whatever
        # else is true of it.
        #
        # The writing half of that Critical is closed at the boundary
        # (`GroundReflectanceRangeError`, `ProvenanceValue._the_value_must_be_finite`), so this
        # path is for the projects already on disk and for whatever corrupts one next -- a truncated
        # write, an editor, a restore from a partial backup.
        _delete_evidence_without_a_manifest(paths, force=force, cause=exc)
        if has_running_job and not force:  # pragma: no cover -- `force` is required above.
            raise RunningJobError(
                f"{paths.name} has a running job writing into {paths.root}. Cancel it, or pass force."
            ) from exc
        removed = [paths.root, *sorted(paths.root.iterdir())]
        shutil.rmtree(paths.root)
        return removed

    if loaded.manifest.project.name != paths.root.name:
        raise ProjectTreeUnrecognisedError(
            f"{paths.root} holds a manifest for a project named "
            f"{loaded.manifest.project.name!r}, not {paths.root.name!r}. A manifest that describes "
            "a different project is not evidence that this directory is one, and removing the tree "
            "on the strength of it would remove whatever else is here. Rename the directory back, "
            "or remove it outside this server."
        )
    orphans = orphan_entries(paths, loaded.manifest)
    if orphans:
        raise ProjectTreeUnrecognisedError(
            f"{paths.root} holds {len(orphans)} top-level entry/entries this project does not "
            f"account for ({', '.join(orphans[:5])}{', ...' if len(orphans) > 5 else ''}). force is "
            "permission to delete a project, not permission to delete an arbitrary tree, so this "
            "refusal is not overridden by it. Move them out first, or remove the directory outside "
            "this server."
        )

    if has_running_job and not force:
        raise RunningJobError(
            f"{paths.name} has a running job writing into {paths.root}. Cancel it, or pass force."
        )
    if not force:
        recorded = len(loaded.manifest.inputs) + len(loaded.manifest.artifacts)
        if recorded:
            raise ProjectNotEmptyError(
                f"{paths.name} records {len(loaded.manifest.inputs)} input(s) and "
                f"{len(loaded.manifest.artifacts)} artifact(s). Deleting discards every matrix "
                "built so far; pass force to confirm."
            )

    removed = [paths.root, *sorted(paths.root.iterdir())]
    shutil.rmtree(paths.root)
    return removed


def archive_project(
    paths: ProjectPaths,
    *,
    destination: Path | str,
    force: bool = False,
    has_running_job: bool = False,
    archived_at: datetime | None = None,
) -> LoadedProject:
    """Mark the project archived and move it under `destination`, returning it at its new home.

    Phase 1 archiving "just **marks** and moves" (docs/plan.md, Phase 1 Defers); ruling R8 added
    `Project.archived_at` so the marking has somewhere to go. Without it, an archived project can
    only be recognised by where it sits, which stops being true the moment anyone moves it again.

    Order: move, then mark at the new location. The other way round leaves a manifest claiming to be
    archived in a directory that never went anywhere, which is the more misleading of the two
    failures -- this way a failed mark leaves a moved but unmarked project, exactly the state Phase 1
    archiving had before R8.
    """
    if not paths.manifest.is_file():
        if (obstacle := _manifest_obstacle(paths)) is not None:
            raise _refuse_manifest_obstacle(paths, obstacle)
        raise ProjectNotFoundError(f"{paths.root} has no {MANIFEST_NAME} and is not a project.")
    if has_running_job and not force:
        raise RunningJobError(
            f"{paths.name} has a running job writing into {paths.root}. Cancel it, or pass force."
        )
    destination = Path(destination)
    if not destination.is_dir():
        raise ProjectNotFoundError(f"archive destination does not exist: {destination}")

    target = destination / paths.name
    if target.exists():
        raise ProjectExistsError(
            f"{target} already exists; archiving onto it would merge two projects' directories."
        )
    shutil.move(str(paths.root), str(target))

    archived = _load_manifest(ProjectPaths(target), radiance_version=None)
    archived.manifest.project.archived_at = archived_at or datetime.now(UTC)
    save_manifest(archived.paths, archived.manifest)
    return archived


# --------------------------------------------------------------------------------------------
# Registering inputs
# --------------------------------------------------------------------------------------------


def register_input(
    project: LoadedProject,
    *,
    input_id: str,
    kind: InputKind,
    files: Sequence[Path | str],
    units: Units | None = None,
    provenance: Provenance | None = None,
    source: str | None = None,
    registered_by: ToolCall | None = None,
    role: MaterialRole | None = None,
    sources: Sequence[FileRef] = (),
    server_written: bool = False,
    zones: Sequence[ZoneRecord] | None = None,
    save: bool = True,
) -> InputRecord:
    """Digest `files` and record them as one input, replacing any input of the same id.

    Re-registering an id in place is how "change the sensor grid" and "swap the glazing BSDF"
    happen: the id is the graph's edge and must not move when its content does. This replaces the
    **whole** record, `role` included: re-registering a ground materials input's files without also
    re-passing `role=MaterialRole.GROUND` drops the role, exactly as it already drops `units` or
    `provenance` if they are not re-supplied -- one behaviour, not a special case for the new field.

    `sources` and `server_written` are Phase 10's import provenance (`InputRecord.sources` and
    `.server_written`): `sources` names the files an imported input was converted from, and
    `server_written` marks that this server wrote `files` itself, in metres. Both default to "not an
    import" and, like every other field here, are dropped on a re-registration that does not re-pass
    them. `zones` is Phase 11b's zone record for an EN 12464 zoned grid and is dropped the same way.

    There is deliberately **no ground-reflectance argument here**. It is a project field with one
    setter (docs/tool-surface.md#ground-reflectance-is-one-project-value-passed-to-every-sky-generator);
    a second place to set it is the failure that rule exists to prevent.

    `role`, where given, must be a genuine `MaterialRole` member -- ruling R9's channel for "this
    input is the modelled ground" (or any of the LM-83 table's other six roles), read back by
    `REQUIRED_EDGES["dmx"]`. Typed against `rad_core.materials.MaterialRole` rather than a bare
    string, and stored as that member's own `.value`: `rad_core.manifest.InputRecord.role` is a plain
    string (that module may not import this one -- see its own docstring), so this is the one place a
    `MaterialRole` is converted into the value the schema actually carries. A plain string equal to a
    role's value (`"ground"` for `MaterialRole.GROUND`) is refused (`InvalidRoleError`,
    `ARGUMENT_OUT_OF_RANGE`, `field="role"`) rather than accepted by the str/Enum coincidence --
    `apply_defaults`'s own `InvalidRoleError` (`rad_core.materials`) is reused rather than duplicated,
    for the identical reason and at the identical code, distinguished only by `field`.
    """
    if input_id.startswith(SCALAR_INPUT_PREFIX):
        raise ProvenanceError(
            f"input id {input_id!r} uses the reserved {SCALAR_INPUT_PREFIX!r} namespace, which "
            "belongs to the project's synthetic scalar inputs. A file input there would shadow one."
        )
    if not files:
        raise ProvenanceError(
            f"input {input_id!r} has no files. An input with nothing to digest contributes a "
            "constant to every digest downstream of it, so a change to it could never be detected."
        )
    if role is not None and not isinstance(role, MaterialRole):
        raise InvalidRoleError(
            f"role={role!r} is not a MaterialRole member (got {type(role).__name__}). Pass one of "
            f"{[r.value for r in MaterialRole]!r}.",
            field="role",
        )

    record = InputRecord(
        id=input_id,
        kind=kind,
        files=file_refs(files),
        units=units,
        provenance=provenance,
        source=source,
        registered_by=registered_by,
        role=role.value if role is not None else None,
        sources=list(sources),
        server_written=server_written,
        zones=list(zones) if zones is not None else None,
    )

    manifest = project.manifest
    snapshot = manifest.model_copy(deep=True)
    for index, existing in enumerate(manifest.inputs):
        if existing.id == input_id:
            manifest.inputs[index] = record
            break
    else:
        manifest.inputs.append(record)

    if save:
        _commit(project, snapshot)
    return record


def register_input_and_set_role(
    project: LoadedProject,
    *,
    input_id: str,
    kind: InputKind,
    files: Sequence[Path | str],
    registered_by: ToolCall | None = None,
    role: str | None,
) -> InputRecord:
    """`register_input`, followed by stamping `InputRecord.role` with a value that is not a
    `MaterialRole` member -- because `register_input`'s own `role=` parameter is typed to that enum
    and raises `InvalidRoleError` for anything else (ruling R9's channel, reserved for the LM-83
    default-table's seven materials rows).

    **Exists because a second vocabulary now writes this same field (ruling R133, Phase 7d-1
    Task 1).** `rad_mcp.window_group_tool`'s `closing_role` argument records LM-83 section 2.2.6's
    two-value closing class (`"view"`/`"clerestory"`) on `window_group` inputs, and that vocabulary
    has nothing to do with materials -- passing it through `register_input`'s typed parameter would
    raise on every non-`None` value. `InputRecord.role` itself has no type gate of its own (a
    deliberately generic `str | None`, `rad_core/manifest.py`'s own docstring), so the fix is not to
    widen `register_input`'s parameter -- that would let a materials caller's typo through by the
    same `str`/`MaterialRole` coincidence `InvalidRoleError` exists to refuse -- but to give a
    second, differently-typed caller its own entry point that goes around the gate on purpose.

    **`save=False` inside, one write outside.** `register_input(..., save=False)` builds a *fresh*
    `InputRecord` and splices it into `project.manifest.inputs` (replacing any existing record of
    the same id, exactly as the default path does) without touching disk; `role` is then set
    directly on that same object, and `save_manifest` performs the single write this call was
    always going to make. Re-registering an id with `role=None` therefore clears whatever role a
    previous registration set, the same "replaces the whole record" rule `register_input`'s own
    docstring states for every other field -- and it does so unconditionally, because the fresh
    record `register_input` hands back already starts at `role=None` (this function never passes
    its own `role=` keyword through to that call, by construction). What *would* break the
    guarantee is not guarding this function's own assignment -- that turns out to be inert, since
    there is nothing stale on the fresh object to guard against -- but skipping the whole
    `register_input`/`save_manifest` sequence when `role is None` (an "optimisation" that reads
    "nothing to set" as "nothing to do" and returns the old record untouched): that leaves the
    previous call's `role` (and `files`, and `registered_by`) exactly where they were.
    `tests/test_project.py::TestRegistration::test_register_input_and_set_role_clears_a_role_on_redefine`
    pins the observable guarantee and is written to go red under that mutation; its own docstring
    records why the more literal `if role is not None: record.role = role` guard does not move it.
    """
    record = register_input(
        project, input_id=input_id, kind=kind, files=files, registered_by=registered_by, save=False,
    )
    record.role = role
    save_manifest(project.paths, project.manifest)
    return record


def rescan_inputs(project: LoadedProject, *, save: bool = True) -> list[str]:
    """Re-digest every registered input's files; return the ids whose content moved.

    The manifest cannot notice a `.mat` edited in a text editor unless something re-reads the bytes,
    and "change the modelled ground material" is exactly that
    (docs/tool-surface.md#how-provenance-invalidates-a-matrix). Comparison is by
    `combine_file_digests` -- the one file-folding rule, called rather than re-derived, so "did this
    input change" is decided by the same fold the graph walks.

    A file that has disappeared raises rather than being absorbed as a change: an input whose file
    is gone is a broken project, and reporting it as "changed" would invite a rebuild that cannot
    run.
    """
    snapshot = project.manifest.model_copy(deep=True)
    changed: list[str] = []
    for record in project.manifest.inputs:
        fresh = file_refs([f.path for f in record.files])
        if _folds_differently(fresh, record.files):
            record.files = fresh
            changed.append(record.id)
    if changed and save:
        _commit(project, snapshot)
    return changed


def _folds_differently(fresh: Sequence[FileRef], recorded: Sequence[FileRef]) -> bool:
    """Whether two `FileRef` lists fold to different digests, by `combine_file_digests`.

    One fold, one definition (R30): `rescan_inputs` and `drifted_inputs` ask the *same* question
    about the same bytes and differ only in what they do with the answer, so the comparison itself is
    written once. `combine_file_digests` is the graph's own fold -- order-sensitive, because the
    order is the order the files reach `oconv` -- and calling it here rather than comparing the two
    lists element-wise is what makes "did this input change" mean the same thing to a rescan, to a
    drift refusal and to `evaluate`'s walk.
    """
    return combine_file_digests([f.digest for f in fresh]) != combine_file_digests(
        [f.digest for f in recorded]
    )


def drifted_inputs(project: LoadedProject, input_ids: Sequence[str]) -> dict[str, str]:
    """`rescan_inputs`' question, asked **read-only**, about a named subset: which of `input_ids`
    no longer folds to the digest the manifest recorded, and why.

    Returns `{input_id: reason}` for the drifted ones only -- empty when every named input's bytes
    are still the bytes that were registered. An id the manifest does not carry is simply absent
    from the answer: "is this input's file still what we digested" is unanswerable about an input
    that was never registered, and the callers that need *that* fact
    (`rad_mcp.annual_tool._refuse_missing_direct_sun_binding`) already ask it separately and refuse
    with their own code.

    **Why this exists, and why it is not `rescan_inputs(save=False)`.** `rescan_inputs` mutates
    `record.files` in place before deciding whether to commit, so calling it with `save=False` to
    "just look" leaves the in-memory manifest holding fresh digests that were never written -- and
    every reuse decision taken against that `LoadedProject` afterwards would compare against bytes
    the manifest on disk does not claim. That is a silently wrong reuse answer produced by the act of
    checking, which is the opposite of what a check is for. This function reads and returns; it
    writes nothing, in memory or on disk.

    **What it closes.** `is_reusable` re-digests an *artifact's* own sealed bytes (ruling R7), and
    `evaluate` walks *recorded* input digests -- neither re-reads an input's file. So an input whose
    file is edited after registration moves nothing in the graph: every artifact built from it stays
    valid, and a build that reads it reads bytes nothing ever validated. That is tolerable for inputs
    this server copies and seals (the `role="matrix"` BSDF becomes a `tmx` artifact, whose copy
    `rad_mcp.annual_tool._refuse_drifted_sealed_tmx` re-digests before a run reads it -- not
    `is_reusable`, which is never asked about a `tmx`, T not being a step; final fix D, fix round
    4, corrected the claim that it was) and is *not* tolerable for one it points at in place --
    `role="direct_sun"`'s tensor tree, which `Cds` reads from the caller's own path
    (docs/tool-surface.md#6-window_group). Verifying at the point of use is what makes that binding
    as safe as the sealed one, and it is the check `rescan_inputs`' own docstring implied existed
    somewhere while having no caller in `src/` at all.

    A file this function **cannot read at all** -- gone, replaced by a directory, `chmod 000`, a
    symlink the server refuses to follow -- is reported as drift rather than raised, which is the
    opposite of `rescan_inputs`' choice and deliberately so: that function is asked to *record* a new
    digest and cannot record one for a file it cannot read, while this one is asked whether the
    recorded bytes are still there -- and "I cannot read it" is the same "no" as "the bytes changed",
    with the same recovery. Raising instead would hand the caller an exception where it wanted a
    refusal with a code. Both exception families are caught, for the reason given at the `except`
    below; `file_digest`'s own narrower translation is untouched.
    """
    recorded = {record.id: record for record in project.manifest.inputs}
    drifted: dict[str, str] = {}
    for input_id in input_ids:
        record = recorded.get(input_id)
        if record is None:
            continue
        try:
            fresh = file_refs([f.path for f in record.files])
        except (ProvenanceError, OSError) as exc:
            # **Both families, and the pair is not belt-and-braces.** `file_digest` translates
            # *some* read failures into `ProvenanceError` -- a missing file, a directory, a FIFO or
            # device, an `ELOOP` symlink chain -- and deliberately re-raises every other `OSError`
            # untouched, because *"a full disk, a permission the server lacks ... is a different
            # failure and is left to propagate rather than be relabelled as provenance"* (that
            # function's own clause, and the mislabel a whole-branch audit found in `run_annual`).
            # That split is right for the function that is asked to *record* a digest; it is not
            # right for the one asked whether recorded bytes are still there. A tensor tree at
            # `chmod 000` is unreadable, so this function cannot prove its bytes are the bound ones,
            # and the honest answer is the same "no" a deleted file gets -- with the same recovery
            # (re-bind, or restore what you bound). Catching only `ProvenanceError` let that case
            # escape as a bare `OSError` into the generic handler, which answers with a code whose
            # recovery is a different one (review round 1, Minor 1). `file_digest` keeps its own
            # narrower contract unchanged; the widening is here, where the question is different.
            drifted[input_id] = f"its file could not be read ({exc})"
            continue
        if _folds_differently(fresh, record.files):
            drifted[input_id] = "its bytes are not the bytes that were registered"
    return drifted


# --------------------------------------------------------------------------------------------
# Registering artifacts -- and the edges they must carry
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class EdgeRequirement:
    """Edges an artifact of some kind must name, and the row of the spec that says so."""

    why: str
    input_kinds: frozenset[str] = frozenset()
    """At least one input of each kind. The weakest of the four forms -- see `REQUIRED_EDGES`."""
    artifact_kinds: frozenset[str] = frozenset()
    ids: frozenset[str] = frozenset()
    """These exact ids. Strong: it names the edge, not a kind that might supply it."""
    ids_if_present: frozenset[str] = frozenset()
    """These exact ids, required only when the manifest actually has them.

    For the synthetic project scalars. `project:north` exists only in a project that declares a
    `north` scalar, so requiring it unconditionally would refuse a sky matrix in a project that has
    no north to depend on. Where the scalar *does* exist, an `smx` that does not name it will never
    invalidate when north changes -- and a 90-degrees-wrong north "produces a fully plausible annual
    result" (docs/tool-surface.md#how-provenance-invalidates-a-matrix)."""
    roles: frozenset[str] = frozenset()
    """At least one **materials** input among `built_from` whose `InputRecord.role` is one of these.
    Strong, like `ids`: it names *which* input of a kind satisfies the row by what that input was
    declared to be, not merely that an input of the right kind exists somewhere in the edge set
    (ruling R9). This is the fourth form, added for `dmx`'s ground requirement: `ids`/`ids_if_present`
    cannot express it because the ground materials input has no fixed id the manifest could pin, and
    `input_kinds` cannot express it because kind alone cannot tell the ground materials input apart
    from any other one -- "ground" is a role a materials input plays, not a kind of input, and until
    `InputRecord` carried one there was no field to check it against.

    Matched only against `kind == "materials"` edges (`check_edges`), never a bare role lookup across
    every input regardless of kind: `role` is a general-purpose label on `InputRecord`, and as of
    Phase 7d-1 Task 1 (ruling R133) it carries two disjoint vocabularies -- `MaterialRole`'s seven
    materials values, and `window_group(action="define")`'s two-value LM-83 closing class -- but
    every `EdgeRequirement.roles` requirement that exists today is over the materials vocabulary
    specifically, and a `roles` requirement is always paired with an `input_kinds` (or equivalent)
    requirement that a materials input exists at all -- so a `dmx` cannot be satisfied by tagging a
    *geometry* file `role="ground"` while no materials input is named at all, which is exactly what
    a role match unconstrained by kind would have allowed (review round 2, finding I1). A `roles`
    check against the closing-class vocabulary would need its own `input_kinds={"window_group"}`
    pairing for the identical reason; nothing on this branch defines one."""


#: **The edges are the untested half of the graph.** `rad_core.manifest` proves invalidation is
#: correct *given correct edges*, and derives it with no branch on artifact kind -- which is right,
#: because a per-kind rule table would pass the eleven rows and be wrong on the twelfth. But edge
#: *assignment* is unavoidably per-kind knowledge (which matrix depends on which input is a Radiance
#: fact), it lives in this layer, and it is unchecked anywhere: a `dmx` registered without
#: `mat:ground` in its `built_from`, or an `smx` without `project:ground_reflectance`, passes every
#: one of those eleven rows and then never invalidates. Silently, and forever.
#:
#: So this is a floor, one requirement per row of the table in
#: docs/tool-surface.md#how-provenance-invalidates-a-matrix, enforced both before a build starts
#: (`check_edges`) and again when the artifact is sealed. It is deliberately only as strong as that
#: table: kinds the table does not cover (`dsmx`, `hdr`, `pts`, `ill`) are absent rather than
#: guessed, because a wrong requirement refuses a correct build and would be worse than none. `ill`
#: in particular is reachable both from matrices and from a direct point-in-time run, so there is no
#: edge it always has.
#:
#: **How strong each row actually is.** `ids`, `ids_if_present` and `roles` each name *which* edge
#: satisfies the row -- by its id, or by what `InputRecord.role` says it is -- so all three close the
#: hole they are aimed at. `input_kinds` requires only that *an* input of that kind is named, which
#: is weaker: it cannot tell that input apart from any other of the same kind. Where a row is only as
#: strong as `input_kinds`, the `why` string says so rather than leaving the strength of the check to
#: be inferred from the fact that a check exists.
#:
#: **Ruling R82 (Phase 7a Task 13): a derived row must name the table row it derives from.** The
#: invalidation table is written for the 3-phase five (T/V/D/S/ill); the five-phase matrices are not
#: in it by name. Their rows below are therefore *derived* -- Vd is V against a different scene, Sds
#: is S with `-d` -- and each `why` names the table rows it stands on rather than asserting a shape.
#: A kind whose invalidation could not be sourced would get **no row at all** and would rebuild every
#: run, which costs wall clock and is honest; sealing it as reusable on an unsourced requirement is
#: ruling R75's defect (a byte-identical result returned for a run that asked for something else)
#: with a new name. `ill` stays absent for the 5-phase method too, for the reason above: it is
#: reachable from a point-in-time run that names none of these nine matrices, so there is still no
#: edge it always has.
REQUIRED_EDGES: Final[Mapping[str, EdgeRequirement]] = {
    "octree": EdgeRequirement(
        why="an octree with no geometry is the empty-octree failure recorded as a manifest entry",
        input_kinds=frozenset({"geometry"}),
    ),
    "vmx": EdgeRequirement(
        why=(
            "rows: interior partition/reflectance, sensor grid, and aperture geometry each "
            "invalidate V"
        ),
        input_kinds=frozenset({"geometry", "materials", "sensor_grid", "window_group"}),
    ),
    "dmx": EdgeRequirement(
        why=(
            "rows: exterior context, the modelled ground *material*, and aperture geometry each "
            "invalidate D. Two requirements, not one: an input of kind materials must be named at "
            "all (weak -- vmx's own row needs the same general guarantee), AND built_from must name "
            "a materials input whose InputRecord.role is "
            f"{MaterialRole.GROUND.value!r} (strong, ruling R9) -- so a dmx built from an interior "
            "materials input, or from no materials input at all, is refused rather than silently "
            "accepted. This closes the hole an earlier version of this row left open and said so: "
            "kind alone cannot tell the ground materials input apart from any other one, and now "
            "nothing here has to."
        ),
        input_kinds=frozenset({"geometry", "materials", "window_group"}),
        roles=frozenset({MaterialRole.GROUND.value}),
    ),
    "tmx": EdgeRequirement(
        why="rows: the glazing/blind BSDF and the aperture geometry each invalidate T",
        input_kinds=frozenset({"bsdf", "window_group"}),
    ),
    "smx": EdgeRequirement(
        why=(
            "rows: the EPW, north, and project ground_reflectance each invalidate S. All three are "
            "required by exact id or kind, so this row is closed. The ground edge is the other half "
            "of the pair -- the same physical quantity as the dmx materials edge, arriving by a "
            "different route, and the asymmetry is only real if both are here. north is required "
            "only where the project declares the scalar, since it is synthetic and a project with "
            "no north has nothing to name"
        ),
        input_kinds=frozenset({"weather"}),
        ids=frozenset({GROUND_REFLECTANCE_INPUT_ID}),
        ids_if_present=frozenset({NORTH_INPUT_ID}),
    ),
    "amb": EdgeRequirement(
        why=(
            "'built_from its octree ... and the ambient parameter set' -- a stale .amb silently "
            "mixes cached irradiance from a scene that no longer exists into the current answer"
        ),
        artifact_kinds=frozenset({"octree"}),
    ),
    # --- the five-phase matrices (Phase 7a Task 13, ruling R82) ---------------------------------
    #
    # **Every row below derives its `why` from the invalidation table above, not from the table's
    # shape**, and each says which of the table's rows it is standing on. Two invalidators the
    # design spec lists for these kinds -- `mf` and the solar disk size -- are *parameters*, not
    # inputs, so they get no edge here at all and are not silently folded into one: they are
    # recorded on the artifact's own `parameters` dict, which `rad_core.manifest.compute_digest`
    # hashes and which `rad_engine.chain._parameters_match` compares against what *this* run asked
    # for before offering reuse. That is the mechanism ruling R75 exists for, and it is a different
    # mechanism from these rows -- stated here so a reader does not read the absence of an `mf` edge
    # as an unenforced invalidator.
    #
    # Where a row is imprecise it is imprecise in the **conservative** direction (a needless
    # rebuild, never a false reuse), and the `why` says which clause that is.
    "vdmx": EdgeRequirement(
        why=(
            "as vmx -- interior partition/reflectance, sensor grid and aperture geometry each "
            "invalidate a view matrix -- plus the all-black octree, because Vd is the same trace "
            "against a different scene and a Vd built against the lit octree is a one-bounce "
            "interior result wearing a direct result's name. The octree half is enforced at "
            "artifact-kind strength only: kind alone cannot tell the all-black octree from the lit "
            "one, and octrees carry no InputRecord.role for a strong form to key on -- but an "
            "absent requirement would let a Vd be sealed naming no octree at all, which a "
            "recompiled scene would then never invalidate. Vd is traced at -ab 1 on a blackened "
            "shell, so the interior-reflectance clause is conservative for this kind (a materials "
            "edit forces a rebuild it may not need); the fenestration keeps its own material there, "
            "so it is not idle either"
        ),
        input_kinds=frozenset({"geometry", "materials", "sensor_grid", "window_group"}),
        artifact_kinds=frozenset({"octree"}),
    ),
    "ddmx": EdgeRequirement(
        why=(
            "as dmx -- exterior context, the modelled ground *material* and aperture geometry each "
            "invalidate a daylight matrix -- plus the all-black octree, for vdmx's reason above "
            "(five-phase-method.md 3.1: 'This octree will be used for both Vd and Dd'). Both halves "
            "of dmx's own pair are carried over unchanged: an input of kind materials must be named "
            "(weak), AND one whose InputRecord.role is "
            f"{MaterialRole.GROUND.value!r} (strong, ruling R9). Dd is traced on the blackened "
            "shell, so the ground *material* clause is the conservative one for this kind, and it "
            "is kept rather than dropped because dropping it would make ddmx the one daylight "
            "matrix in this table a re-tagged ground could not invalidate -- ruling R26's own hole, "
            "reopened by omission"
        ),
        input_kinds=frozenset({"geometry", "materials", "window_group"}),
        artifact_kinds=frozenset({"octree"}),
        roles=frozenset({MaterialRole.GROUND.value}),
    ),
    "cdsmx": EdgeRequirement(
        why=(
            "rows: the glazing/blind BSDF and aperture geometry each invalidate the term traced "
            "*through* the fenestration, and the sensor grid invalidates anything binned per sensor "
            "point -- Cds is rcontrib -I over the grid's own .pts through the tensor-tree BSDF "
            "proxy baked into the suns octree, so all three apply to it exactly as the table "
            "applies them to T and V. The bsdf edge is the load-bearing one and it is the reason "
            "this row exists rather than being left absent: docs/tool-surface.md#6-window_group "
            "states the role='direct_sun' tensor tree is 'a build input to a later cdsmx artifact', "
            "and without this row a Cds could be sealed naming no BSDF at all -- reported valid and "
            "reused forever across a glazing swap, which is the first row of the invalidation table "
            "failing on the one matrix that has no other route to notice it. Enforced at input-kind "
            "strength: kind alone cannot tell the tensor tree bound at role='direct_sun' from the "
            "Klems file bound at role='matrix', because InputRecord.role is materials-only "
            "(EdgeRequirement.roles) and both are kind 'bsdf'. What closes *that* gap is not an edge "
            "at all -- rad_mcp.annual_tool re-digests the direct-sun input against its recorded "
            "FileRef.digest before the run is admitted, so an edited tensor tree refuses instead of "
            "being traced. Plus the suns octree, at artifact-kind strength, for vdmx's reason"
        ),
        input_kinds=frozenset({"geometry", "materials", "sensor_grid", "window_group", "bsdf"}),
        artifact_kinds=frozenset({"octree"}),
    ),
    # --- LM-83's ASE coefficient matrix (Phase 7c, ruling R82's posture, not cdsmx's argument) ----
    # History kept out of the shipped `why`: the window_group edge was dropped at Phase 7c Task 11,
    # having shipped for one commit with Task 9's row.
    "asemx": EdgeRequirement(
        why=(
            "rows: interior partition/reflectance, the sensor grid and aperture geometry each "
            "invalidate a matrix binned per sensor point and traced through the fenestration. "
            "cdsmx's kinds minus the bsdf **and minus the window group**, and **the reason is not "
            "cdsmx's reason.** "
            "That row hangs on its bsdf edge because Cds reads the fenestration's optics from a "
            "tensor-tree proxy and has no other route to notice a glazing swap. This matrix has no "
            "BSDF in it at all -- the ASE octree is the registered scene plus the suns scene, so "
            "the direct beam crosses the aperture's own glass primitive -- which moves the "
            "load-bearing edge onto **materials**: LM-83 measures the direct beam *after* glazing "
            "transmittance (sec 3.3), and that transmittance is declared in the registered "
            "materials, nowhere else. Without this row a glazing retinted from VLT 0.74 to 0.40 "
            "would leave every sealed asemx reported valid, and ASE counts hours over a hard 1000 "
            "lux threshold, so the hours move and nothing says why. **The geometry edge is the "
            "second load-bearing one, and it is load-bearing here in a way it is not for cdsmx**: "
            "sec 3.2.6 leaves 'any fixed or static shading devices, such as overhangs or light "
            "shelves' in the ASE model, and adding an overhang is the commonest thing a study does "
            "*in response to* an ASE result -- an edge that could not carry that edit would hand "
            "back the pre-overhang answer for the post-overhang model. Enforced at input-kind "
            "strength throughout: kind alone cannot tell the shading geometry from any other "
            "geometry input, which is conservative in the safe direction (a needless rebuild). "
            "Plus the ASE octree, at artifact-kind strength, for vdmx's reason -- an absent "
            "requirement would let an asemx be sealed naming no octree at all, which a recompiled "
            "scene would then never invalidate.\n"
            "\n"
            "**Three absences, all deliberate.** No bsdf edge: requiring one would refuse every "
            "correct build, and it would be wrong in the other direction too -- ASE is evaluated "
            "**pre-blind** (sec 3.3: it is the quantity that justifies the shading device, so a run "
            "with the device deployed cannot measure it), so a blind BSDF swap *must not* "
            "invalidate this matrix. Requiring the edge would make the correct answer look stale. "
            "No ground role either, unlike dmx and ddmx: the term is traced at -ab 0, so no "
            "ground-reflected light reaches a sensor and a re-tagged ground cannot change a single "
            "number here.\n"
            "\n"
            "**And no window_group edge.** vmx, dmx, tmx and cdsmx all carry it because each is "
            "binned or traced *through* one named aperture; this matrix is binned by **sun** "
            "(rcontrib -m solar, -b rbin -bn Nrbins) against the registered scene whole, so no "
            "window group reaches build_ase_octree or build_ase_direct_argv at all. Nor could the "
            "edge carry an invalidation even in principle: window_group(action='define') registers "
            "with *the exact files every geometry input already carries* "
            "(rad_mcp.window_group_tool), and InputRecord.content_digest is a function of kind, "
            "those file digests and units -- so the group's digest moves if and only if geometry "
            "moves, which the geometry edge above already carries, while redefining a group's "
            "aperture list leaves the files untouched and the digest identical. Zero invalidation "
            "power, and in exchange a hard ARTIFACT_EDGES_INCOMPLETE on every project that "
            "legitimately has no group -- and ASE is exactly that kind of project: pre-blind, "
            "zero-bounce, direct beam, no fenestration matrix anywhere in it. This repo has already "
            "ruled on the same situation one kind over: R44(2) commits the 2-phase DC matrix as "
            "dsmx rather than vmx precisely because REQUIRED_EDGES['vmx'] demands a window group a "
            "2-phase run does not have and cannot acquire. mf and the quality parameters are "
            "parameters rather than inputs and are enforced through the artifact's own recorded "
            "parameters, as smxsun's row explains"
        ),
        input_kinds=frozenset({"geometry", "materials", "sensor_grid"}),
        artifact_kinds=frozenset({"octree"}),
    ),
    "smxd": EdgeRequirement(
        why=(
            "as smx, exactly -- the EPW, north and project ground_reflectance each invalidate a sky "
            "matrix, and Sds is the identical weather file with gendaymtx -d added. All three are "
            "required by exact id or kind, so this row is closed, and north is ids_if_present for "
            "smx's own reason: it is synthetic and a project with no north has nothing to name. The "
            "subdivision (mf) is a parameter rather than an input and is enforced through the "
            "artifact's recorded parameters, not here"
        ),
        input_kinds=frozenset({"weather"}),
        ids=frozenset({GROUND_REFLECTANCE_INPUT_ID}),
        ids_if_present=frozenset({NORTH_INPUT_ID}),
    ),
    "smxsun": EdgeRequirement(
        why=(
            "as smx, exactly -- the EPW, north and project ground_reflectance each invalidate a sky "
            "matrix, and Ssun is the identical weather file with gendaymtx -5 added, so this row is "
            "closed on its input side for smxd's reason above. The design spec lists two further "
            "invalidators for this kind, mf and the solar disk size, and **neither is an input**: "
            "both are recorded on the artifact's own parameters (mf, solar_disk_degrees) where "
            "compute_digest hashes them and _parameters_match compares them against what this run "
            "asked for. They are named here so their absence from this row reads as 'enforced "
            "elsewhere' rather than as 'not enforced' -- which is the distinction ruling R82 was "
            "written to force"
        ),
        input_kinds=frozenset({"weather"}),
        ids=frozenset({GROUND_REFLECTANCE_INPUT_ID}),
        ids_if_present=frozenset({NORTH_INPUT_ID}),
    ),
    # --- LM-83 section 2.2.6's blind schedule (Phase 7d-1 Task 11, ruling R107) ------------------
    "sched": EdgeRequirement(
        why=(
            "the schedule is a pure function of the per-group **pre-blind direct-sun** matrices, "
            "the analysis area and the in-sun threshold, and of nothing else -- so it names every "
            "illdir:<grid>:<g> and no chain result at all. Not the total-illuminance ill: or ill5: "
            "either: the schedule would be the same number whichever chain sealed the total, "
            "because section 2.2.6's trigger counts points over LM-83's own direct-beam threshold "
            "and a total-illuminance matrix cannot answer it. Without this row a schedule could be "
            "sealed naming nothing at all, and a re-run of the direct-sun leg -- a retinted "
            "glazing, an added overhang, a moved grid, all of which reach asemx through its own "
            "row -- would leave every stored schedule reported valid, so the blinds would keep "
            "closing on the hours the old scene put in sun. **Enforced at artifact-kind strength "
            "and no stronger, and this row says so rather than leaving it to be inferred**: the "
            "per-group direct results are kind 'ill' (there is no 'illdir' kind -- ruling R122), "
            "which is the same kind the annual results carry, "
            "so kind alone cannot tell an illdir: operand from an ill: one and this row would "
            "accept the wrong one. What closes that gap is not an edge: rad_engine.chain."
            "blind_schedule_steps mints every operand id through direct_ill_id, the same function "
            "_direct_sun_leg mints the artifact with, so the two cannot disagree about the name. "
            "The area, the threshold, the resolved closing order and the derivation revision are "
            "**parameters**, not inputs, and are enforced through the artifact's own recorded "
            "parameters where compute_digest hashes them and chain._parameters_match compares them "
            "-- the order in particular, which is what makes a relabelled closing_role invalidate "
            "this artifact while the view and daylight matrices keep reusing"
        ),
        artifact_kinds=frozenset({"ill"}),
    ),
    # --- LM-83 section 2.2.6's blended result (Phase 7d-1 Task 12) -------------------------------
    "illblend": EdgeRequirement(
        why=(
            "the blend is the schedule applied to the per-group, per-state annual results, and it "
            "is a pure function of exactly those two things -- so it names the schedule and every "
            "ill:<grid>:<g>:<s> operand, and nothing else. Each of the two edges carries an "
            "invalidation nothing else can carry. The **sched** edge carries every reason an hour "
            "changes which state it is in: a retinted glazing or an added overhang moves the "
            "per-group direct-sun matrices the schedule is derived from, a relabelled closing_role "
            "or a corrected analysis area moves the schedule's own parameters, and in every one of "
            "those cases the same per-state matrices are summed in a different combination -- so "
            "without this edge a stored blend stays valid while the blinds it was blended against "
            "have moved. The **ill** edges carry every reason a summand changes without the "
            "schedule changing at all: a rebuilt T for one state, a moved sensor grid, a new sky "
            "matrix. Neither edge implies the other, which is why both are rows. **Enforced at "
            "artifact-kind strength and no stronger, and this row says so rather than leaving it "
            "to be inferred**: kind ill is also what illdir: and the unscheduled ill: carry (there "
            "is no illdir kind -- ruling R122), so kind alone cannot tell a per-state operand from "
            "either of them and this row would accept the wrong one. What closes that gap is not "
            "an edge: rad_engine.chain.blend_steps mints every operand id through "
            "group_state_ill_id, the same function three_phase_steps mints the artifacts with, and "
            "the schedule edge through blind_schedule_id, the same function blind_schedule_steps "
            "seals with -- so the two cannot disagree about the name. The **operand set** itself "
            "is a parameter rather than an edge (contributions), because an operand that stops "
            "existing leaves no edge behind to be missing: unbinding one of a group's two states "
            "leaves every surviving edge valid and the schedule's parameters unmoved, and only a "
            "recorded operand set makes chain._parameters_match deny the two-state blend to a "
            "one-state run"
        ),
        artifact_kinds=frozenset({"sched", "ill"}),
    ),
    # --- run_point_in_time's sky and result (Phase 14d, R730) ------------------------------------
    "sky": EdgeRequirement(
        why=(
            "as smx on its non-weather side -- the project ground_reflectance is passed to the "
            "generator's -g (since R761 the ground glow is white and carries no reflectance of its "
            "own), and a declared north turns the sky "
            "through xform -rz. A sky without the ground edge stays valid after the ground "
            "reflectance changes, so an export regenerates the old -g; one without north stays "
            "valid after north changes, and every sun it holds is rotated the old way. north is "
            "ids_if_present for smx's reason: it is synthetic and a project with no north has "
            "nothing to name. The sky's type, date, time and site are parameters (generator_argv), "
            "not inputs, and are enforced through the artifact's recorded parameters"
        ),
        ids=frozenset({GROUND_REFLECTANCE_INPUT_ID}),
        ids_if_present=frozenset({NORTH_INPUT_ID}),
    ),
    "point_in_time": EdgeRequirement(
        why=(
            "the result is rtrace over one octree at one grid's points, and a pure function of "
            "those two and the recorded flags. Without the octree edge a result stays valid after "
            "the scene, the materials, the luminaires or the sky change; without the sensor_grid "
            "edge it stays valid after the grid is re-created, and its values are read against "
            "points they were never traced at. **Enforced at kind strength and no stronger, and "
            "this row says so**: any octree and any sensor grid satisfy it. What closes that gap is "
            "not an edge: rad_mcp.point_in_time_tool registers the result naming its own "
            "octree:point_in_time id and the grid record _validate_scene matched"
        ),
        input_kinds=frozenset({"sensor_grid"}),
        artifact_kinds=frozenset({"octree"}),
    ),
}


def check_edges(project: LoadedProject, kind: str, built_from: Sequence[str]) -> None:
    """Refuse an edge set that cannot invalidate correctly. **Call this before you build.**

    `register_artifact` calls it too, and that is the call that cannot be skipped -- but it happens
    *after* the build, so a refusal there discards a V matrix that already cost an hour. The check
    reads only the manifest and the intended edge list, so a builder can run it first, at zero cost,
    and fail before spawning anything.

    Two ways to fail, both `ARTIFACT_EDGES_INCOMPLETE`:

    - an edge names neither a registered input nor an existing artifact. Caught here rather than
      left to `rad_core`'s dangling-edge error at seal time, so it is refused before the build too;
    - the edge set is missing something `REQUIRED_EDGES` requires for this kind.
    """
    manifest = project.manifest
    input_digests = manifest.input_digests()
    artifact_kinds = {a.id: a.kind for a in manifest.artifacts}

    unknown = [e for e in built_from if e not in input_digests and e not in artifact_kinds]
    if unknown:
        raise EdgesIncompleteError(
            f"artifact of kind {kind!r} names {sorted(unknown)} in built_from, which is neither a "
            "registered input nor an existing artifact. An edge that resolves to nothing cannot "
            "carry a change, so the artifact would be permanently valid."
        )

    requirement = REQUIRED_EDGES.get(kind)
    if requirement is None:
        return

    input_kinds = {r.id: r.kind for r in manifest.inputs}
    input_roles = {r.id: r.role for r in manifest.inputs}
    present_inputs = {input_kinds[e] for e in built_from if e in input_kinds}
    present_artifacts = {artifact_kinds[e] for e in built_from if e in artifact_kinds}
    # Materials-only (`EdgeRequirement.roles`'s own docstring, review round 2 finding I1): a role
    # tagged onto a geometry or window_group input must not be able to stand in for the materials
    # input the row actually needs.
    present_roles = {
        input_roles[e]
        for e in built_from
        if input_kinds.get(e) == "materials" and input_roles.get(e) is not None
    }

    missing = [
        f"an input of kind {k!r}" for k in sorted(requirement.input_kinds - present_inputs)
    ]
    missing += [
        f"an artifact of kind {k!r}"
        for k in sorted(requirement.artifact_kinds - present_artifacts)
    ]
    missing += [f"the edge {i!r}" for i in sorted(requirement.ids - set(built_from))]
    missing += [
        f"the edge {i!r}, which this project declares"
        for i in sorted((requirement.ids_if_present & set(input_digests)) - set(built_from))
    ]
    missing += [
        f"an input whose role is {r!r}" for r in sorted(requirement.roles - present_roles)
    ]
    if missing:
        raise EdgesIncompleteError(
            f"artifact of kind {kind!r} is missing " + ", ".join(missing) + " in built_from "
            f"{sorted(built_from)}. docs/tool-surface.md#how-provenance-invalidates-a-matrix: "
            f"{requirement.why}. An artifact sealed with an incomplete edge set is reported valid "
            "and reused forever, which is worse than one that was never built."
        )


def register_artifact(
    project: LoadedProject,
    *,
    artifact_id: str,
    kind: ArtifactKind,
    path: Path | str,
    built_from: Sequence[str],
    parameters: Mapping[str, Any] | None = None,
    radiance_version: str | None = None,
    warnings: Iterable[EngineAdvisory] = (),
    wall_time_s: float | None = None,
    built_at: datetime | None = None,
    save: bool = True,
    ase_aperture: Aperture | None = None,
    ase_suns: Sequence[SunPosition] | None = None,
    ase_sun_reaches_grid: bool | None = None,
    ase_probe_no_verdict: str | None = None,
    ase_probe_stripped_glazing: tuple[str, ...] = (),
    ase_probe_two_phase: bool = False,
    ase_probe_group: bool = False,
    ase_chain: str | None = None,
    ase_probe_net: bool = False,
) -> Artifact:
    """Seal a freshly built artifact into the manifest, and persist it.

    Sealing is three things, and leaving out any one of them breaks a claim the manifest makes:

    - the file must be **on disk, inside the project**, and its bytes are digested into
      `content_digest` (ruling R7). "Committed" means on disk with a digest; registering a path that
      does not exist would publish a reusable matrix that is not there, and recording no content
      digest would let a matrix truncated by a full disk stay valid forever.
    - `built_from_digests` is filled from `resolve_built_from_digests`, a snapshot of what each edge
      hashed to *at build time*. Without it `invalidated_by` degrades to a shortlist and an artifact
      invalidated by its own raised `-ab` reports a set of unchanged inputs as its cause
      (docs/tool-surface.md#what-it-records).
    - `built_from` must carry the edges its kind requires -- see `REQUIRED_EDGES`.

    `parameters` are normalised on the way in, so the flag set on disk is the flag set that was
    digested; two artifacts cannot share a digest and disagree about what ran.

    `warnings` are the classified advisories from the build's stderr, from
    `rad_engine.advisories.classify(result.warnings)`. They are kept *with the artifact*: a matrix
    built from an octree that dropped an include is reusable forever, so the evidence has to travel
    with it. This module never re-implements that classification.

    `ase_aperture`/`ase_suns` are the sun geometry `_refuse_an_empty_ase_matrix` consults **before**
    it concludes that an all-zero `asemx` means the aperture does not transmit -- see that
    function. **Both or neither**, and an empty `ase_suns` is refused rather than read as "no sun in
    the hemisphere": both are `ValueError`, because either is a caller bug whose silent form would
    publish `ASE = 0 %` from a missing argument. Every caller that supplies neither gets exactly the
    refusal it got before this pair existed.

    `ase_sun_reaches_grid` is the occlusion probe's verdict (rulings R831, R848), passed through to
    `_refuse_an_empty_ase_matrix` as `sun_reaches_grid`: `False` when no sun direction reaches the
    grid with every window group's aperture opened (for a per-group probe, ruling R858: when no ray
    carries less light with this group's aperture closed than with every aperture opened; for a
    two-phase probe, ruling R856: with every glazing surface removed), `True` when one does, `None`
    when the probe gave no verdict or did not run -- and `None` keeps the refusal exactly as it
    was.
    `ase_probe_no_verdict` is the probe's reason for giving none, which the refusal names; `None`
    there with a `None` verdict means no probe ran. `ase_probe_two_phase` says the matrix's probe
    was a two-phase run's (ruling R856: `AseOcclusionProbe.strip_glazing`), passed through as
    `two_phase_probe`: the notice and the refusal then say the probe ran with every glazing surface
    removed rather than with every window group's aperture opened. It is read from the spec, not
    inferred from `ase_probe_stripped_glazing`, which is only the names that probe removed (passed
    through as `stripped_glazing`) and is empty when the scene has no glazing. `ase_probe_group`
    says the probe was a per-group one (ruling R858: `AseOcclusionProbe.group` set), passed through
    as `group_probe`: its verdict is whether closing this group's aperture dims some ray, and the
    notice and the refusal say so rather than speaking of every opening. `ase_probe_net` says the
    probe was the ASE_net matrix's (ruling R869: `AseOcclusionProbe.closed` set), passed through as
    `net_probe`: it ran with the window groups marked automated closed, and the notice and the
    refusal speak of ASE_net and of the openings that remain. `JobStore._commit` also sets it for
    an ASE_net matrix planned without the dome, which carries no probe, from the
    `"ase_net_blacked"` its parameters record. `ase_chain` is the chain
    the run builds (`ArtifactSpec.ase_chain`), passed through as `chain`: a refusal says what the
    failed run leaves readable, and that differs by chain; `None` states the three- and
    five-phase cases (a two-phase probe's refusal has its own paragraph, chosen on
    `ase_probe_two_phase`).
    """
    manifest = project.manifest
    path = Path(path).resolve()
    root = project.paths.root.resolve()
    if root != path and root not in path.parents:
        raise ProvenanceError(
            f"artifact {artifact_id!r} would be recorded at {path}, outside its project {root}. "
            "Artifacts are written by the layout, not by the caller."
        )
    if not path.is_file():
        raise ProvenanceError(
            f"artifact {artifact_id!r} is not on disk at {path}. An artifact is committed only "
            "when it is on disk with a digest; registering it first would publish a matrix that "
            "does not exist as reusable."
        )

    check_edges(project, kind, built_from)
    sealed_over = _refuse_an_empty_ase_matrix(
        artifact_id,
        kind,
        path,
        aperture=ase_aperture,
        suns=ase_suns,
        sun_reaches_grid=ase_sun_reaches_grid,
        probe_no_verdict=ase_probe_no_verdict,
        stripped_glazing=ase_probe_stripped_glazing,
        two_phase_probe=ase_probe_two_phase,
        group_probe=ase_probe_group,
        net_probe=ase_probe_net,
        chain=ase_chain,
        # From the artifact's own parameters, not from a second caller argument: `chain.py`'s
        # `asemx` spec already records `mf`, and asking for it twice is how two numbers that must
        # agree stop agreeing.
        mf=(parameters or {}).get("mf"),
    )
    if sealed_over is not None:
        # Kept **with the artifact**, for the reason `warnings` itself is: an `asemx` sealed over an
        # all-zero matrix is reusable for ever, so the reason it was allowed through has to be
        # reusable with it. Appended rather than prepended -- the build's own stderr findings came
        # first in time.
        warnings = [*warnings, sealed_over]

    # Flush the artifact itself before the manifest records it. Otherwise a power loss between the
    # two leaves a manifest that survived referencing bytes that did not, which is the one direction
    # of inconsistency the digest cannot detect -- the entry looks valid and the file is short.
    _fsync_path(path)

    artifact = Artifact(
        id=artifact_id,
        kind=kind,
        path=str(path),
        built_from=list(built_from),
        parameters=normalise_parameters(parameters or {}),
        digest="",  # replaced below by the digest the graph itself resolves
        content_digest=file_digest(path),
        radiance_version=radiance_version or manifest.project.radiance_version,
        built_at=built_at or datetime.now(UTC),
        wall_time_s=wall_time_s,
        warnings=[_artifact_warning(w) for w in warnings],
    )

    snapshot = manifest.model_copy(deep=True)
    replaced_at = next(
        (i for i, existing in enumerate(manifest.artifacts) if existing.id == artifact_id), None
    )
    previous = manifest.artifacts[replaced_at] if replaced_at is not None else None
    if replaced_at is None:
        manifest.artifacts.append(artifact)
    else:
        manifest.artifacts[replaced_at] = artifact

    try:
        artifact.built_from_digests = resolve_built_from_digests(manifest)[artifact_id]
        artifact.digest = recompute_digests(manifest)[artifact_id]
    except ManifestError:
        # A dangling edge or a cycle. Roll the append back: an in-memory manifest left holding an
        # artifact that cannot be evaluated would fail every later operation on this project, at a
        # point far from the call that caused it.
        # Branch on `replaced_at`, not on `previous`: the two are set together above, and testing the
        # index that this line also *uses* is what keeps "we appended" and "we overwrote" from
        # drifting apart. Rolling an overwrite back with `pop()` would discard the artifact that was
        # already in the manifest -- a data loss the digest could not detect afterwards.
        if replaced_at is None:
            manifest.artifacts.pop()
        else:
            assert previous is not None  # set together with replaced_at above
            manifest.artifacts[replaced_at] = previous
        raise

    if save:
        _commit(project, snapshot)
    return artifact


#: Final review A1: what a refused whole-grid `asemx` leaves readable, by chain. The guard sits at
#: seal time and `_append_leg` appends the direct leg AFTER the chain, each artifact committed as
#: its step succeeds. Each sentence was measured through the real tool on a run refused at
#: `asemx:<grid>` (blind_control='lm83', an opaque aperture): on three-phase every annual read and
#: blind_schedule were refused (no 'ill:<grid>' is sealed under lm83, `chain.three_phase_steps`),
#: as having no annual result on a fresh project and as stale after an earlier run whose glazing
#: the refused run changed (final review A2, item 5). Round A3: blind_schedule's refusals are its
#: own -- `BLIND_SCHEDULE_NO_DIRECT_MATRIX` where no 'illdir:<grid>' is registered, and stale
#: where `_blind_schedule`'s `_refuse_stale_results` finds 'illdir:<grid>' or 'sched:<grid>'
#: stale; `test_annual_tool.TestWhatARefusedAseRunLeavesReadable` drives both states. The
#: earlier-current branch is kept although rare: `chain.reuse_decision` also rebuilds an
#: `asemx:<grid>` whose requested parameters (quality, mf) differ, and a failed step commits
#: nothing, so the earlier 'illdir:<grid>' stays current -- e.g. one sealed all-zero by the probe
#: at one mf, re-run at another mf whose probe refuses.
#: On five-phase summary, points, timeseries, export and metrics read, compliance(standard='lm83')
#: answered with ASE_NOT_FROM_DIRECT_ONLY_RUN, and blind_schedule was refused (on a fresh project;
#: it reads 'illdir:<grid>' and nothing chain-specific, so an earlier current one answers as on
#: three-phase). `None` is a caller
#: that did not say which chain (`ArtifactSpec.ase_chain` is set on every planned `asemx`), and
#: its text states both. The two-phase paragraph above carries its own.
_THREE_PHASE_LOSS = (
    "on a three-phase chain the direct-sun leg is planned only under blind_control='lm83', and "
    "that run seals no 'ill:<grid>' -- its annual result is the blend 'illblend:<grid>', sealed "
    "after the schedule this leg feeds -- so this run publishes no annual result and no "
    "'illdir:<grid>': every annual read of this grid (summary, points, timeseries, export, "
    "metrics and compliance) answers only from an earlier run's result that is still registered "
    "and current, and is refused otherwise -- as having no annual result where none was sealed, "
    "and as stale where an input changed since -- and blind_schedule answers only from an "
    "earlier run's 'illdir:<grid>' that is still registered and current, and is refused "
    "otherwise -- for example with BLIND_SCHEDULE_NO_DIRECT_MATRIX where none was sealed, or as "
    "stale where an input of it, or of an earlier 'sched:<grid>' beside it, changed since"
)
_FIVE_PHASE_LOSS = (
    "on a five-phase chain 'ill5:<grid>' is already sealed when this fires, so summary, points, "
    "timeseries, export and metrics(da) all still read and compliance(standard='lm83') still "
    "answers, with its ASE not from the direct-only run (ASE_NOT_FROM_DIRECT_ONLY_RUN), while "
    "this run never seals 'illdir:<grid>', so blind_schedule answers only from an earlier run's "
    "'illdir:<grid>' that is still registered and current, and is refused otherwise"
)
_WHOLE_GRID_LOSS_BY_CHAIN: Final[dict[str | None, str]] = {
    "3phase": (
        "What is lost was measured: `_append_leg` appends the direct leg AFTER the chain and each "
        f"artifact is committed as its step succeeds, but {_THREE_PHASE_LOSS}. The guard fails "
        "closed: it costs availability, and it never publishes a wrong number."
    ),
    "5phase": (
        "What is lost is bounded and was measured: `_append_leg` appends the direct leg AFTER the "
        f"chain and each artifact is committed as its step succeeds: {_FIVE_PHASE_LOSS}. The "
        "guard was weighed against that cost and kept: it fails closed, it costs availability, "
        "and it never publishes a wrong number."
    ),
    None: (
        "What is lost was measured, and it depends on the chain: `_append_leg` appends the direct "
        f"leg AFTER the chain and each artifact is committed as its step succeeds: "
        f"{_FIVE_PHASE_LOSS}; but {_THREE_PHASE_LOSS}. The guard fails closed: it costs "
        "availability, and it never publishes a wrong number."
    ),
}


#: R869: what an ASE_net matrix's refusal leaves, by chain. The net leg is planned after every
#: other direct-sun run of the job (`chain.direct_sun_steps`), and `rad_mcp.annual_tool` appends
#: the direct-sun leg after the chain and the schedule and blend after the leg, so the whole-grid
#: ASE matrix and 'illdir:<grid>' are sealed (or reused) before this matrix and nothing after it
#: is. Stated per chain only where the chain decides it.
_NET_SEALED_BEFORE = (
    "What was and was not sealed: the ASE_net leg runs after every other direct-sun run of this "
    "job and each artifact is committed as its step succeeds, so ASE_gross's whole-grid matrix "
    "'asemx:<grid>' and its result 'illdir:<grid>' were sealed or reused by this run before "
    "this matrix, while 'illdirnet:<grid>', the ASE_net result, is not sealed and this run "
    "publishes no ASE_net. "
)
_NET_GUARD_COST = (
    "The guard fails closed: it costs availability, and it never publishes a wrong number."
)
_NET_LOSS_BY_CHAIN: Final[dict[str | None, str]] = {
    "3phase": (
        _NET_SEALED_BEFORE + "On a three-phase run that plans the blind schedule "
        "(blind_control='lm83'), each window group's 'illdir:<grid>:<g>' was sealed or reused "
        "before this matrix too, and the schedule 'sched:<grid>' and the blend 'illblend:<grid>' "
        "-- that run's annual result -- are planned after it and are not sealed. "
        + _NET_GUARD_COST
    ),
    "5phase": (
        _NET_SEALED_BEFORE + "On this five-phase chain 'ill5:<grid>' was sealed before the "
        "direct-sun leg. " + _NET_GUARD_COST
    ),
    "2phase": (
        _NET_SEALED_BEFORE + "On this two-phase chain 'ill:<grid>' was sealed before the "
        "direct-sun leg. " + _NET_GUARD_COST
    ),
    None: _NET_SEALED_BEFORE + _NET_GUARD_COST,
}


#: Where an aperture polygon's transmitting material goes, and the re-run (final fix E7, part A
#: round-5 re-review N4). It said "in the registered materials, re-register it, and re-run";
#: measured false where the geometry file defines the modifier itself: `scene_files_with_materials`
#: puts the materials ahead of the geometry, so the geometry file's own definition is the one its
#: polygons read, and `materials(action='set')` over the same name left the re-run refused again.
#: Re-adding the geometry with the modifier redefined as glass made the re-run seal.
#:
#: Fix round 1 (review I4): "the registered geometry file" became "the registered geometry, or a file
#: it includes": a definition in an `!xform` include is read after the registered materials too, and
#: measured, `materials(action='set')` over it left the re-run refused again. Split in two so
#: `results` gives the `where` half after its own lead (review M3).
TRANSMITTING_MATERIAL_WHERE: Final[str] = (
    "where the registered geometry, or a file it includes with !xform, defines the modifier "
    "itself, that definition is the one its polygons read, ahead of the registered materials, so "
    "redefine it in that file and add the geometry again with geometry(action='add', ...); "
    "otherwise set it in the registered materials with materials(action='set', ...), passing "
    "'reflectance': None where it records one, since glass takes none and that call keeps every "
    "field it is not given."
)
TRANSMITTING_MATERIAL_REPAIR: Final[str] = (
    f"for example glass, and re-run run_annual for this grid: {TRANSMITTING_MATERIAL_WHERE} "
)


def _refuse_an_empty_ase_matrix(
    artifact_id: str,
    kind: str,
    path: Path,
    *,
    aperture: Aperture | None = None,
    suns: Sequence[SunPosition] | None = None,
    mf: int | None = None,
    sun_reaches_grid: bool | None = None,
    probe_no_verdict: str | None = None,
    stripped_glazing: tuple[str, ...] = (),
    two_phase_probe: bool = False,
    group_probe: bool = False,
    chain: str | None = None,
    net_probe: bool = False,
) -> EngineAdvisory | None:
    """`ASE_DIRECT_MATRIX_EMPTY`, at seal time, keyed on `kind == "asemx"` and on nothing else --
    **and, since Phase 7d-1 Task 18, only after the sun geometry says the matrix ought to have had
    something in it.**

    ## The premise this guard was measured from, and the premise it was applied under

    Two runs one material line apart (Phase 7c, ruling R114): `void glass` gave 141 of 876 non-zero
    bins, `void plastic` gave **0 of 876**, both exiting 0 with empty stderr. **That is a premise
    about transmittance** -- an aperture that does not transmit produces a valid, empty, wrong
    matrix, and an ASE of 0 % read off it is the most favourable verdict LM-83 has.

    It was then applied as though it were a premise about **geometry**: every all-zero `asemx` was
    read as a non-transmitting aperture. It is not the same claim, and Phase 7d's per-group runs
    make the difference matter once per group rather than once per run.

    So the order is: **no sun in the set this matrix was built over lies in the aperture's own
    exterior hemisphere** -> all-zero is the expected answer, the matrix seals, and this function
    returns `ASE_DIRECT_ALL_ZERO_NO_SUN` at `info` for `register_artifact` to keep with the
    artifact. Sun available and the matrix still all-zero -> the aperture does not transmit, and
    that refuses exactly as before. A non-zero matrix returns `None` before either test runs: there
    is no all-zero to explain.

    ## `suns` is the sun DOME, not a weather file's sun path -- ruling R143

    **`asemx` carries no weather at all.** `rad_engine.octree.build_ase_octree` compiles
    `rad_engine.suns.suns_scene_text(mf)`, which is `1 + 144*mf^2` `source` primitives at the
    Reinhart **patch centres**, and `rad_engine.matrices.build_ase_direct_argv` bins every one of
    them (`-b rbin -bn Nrbins -m solar`). The matrix is a coefficient per sensor per sky direction;
    which of those directions the sun actually visits, and when, arrives later as `smxsun`. So the
    only set this guard may test against is **the dome the matrix was built over**, and `suns` must
    be those patch directions. Testing a weather file's sun positions here would be a claim about a
    quantity this artifact does not contain.

    **What that costs, derived and then measured.** A dome direction at altitude `a`, azimuth
    `psi` lies in front of an exterior normal at elevation `-b`, azimuth `phi` exactly when
    `cos(psi - phi) > tan(a) * tan(b)` (for `0 < b < 90`; it is the dot product divided through by
    `cos(a) cos(b)`). **`a + b < 90` is that condition's azimuth-ALIGNED reduction** -- `psi == phi`
    makes the cosine 1 -- and off-axis the cosine is smaller, so a direction can sit behind an
    aperture that the aligned form still places it in front of. The branch
    therefore needs `b` at, or a little under, `90 - a_min`, where `a_min` is the dome's **lowest
    ring**. That is not "below the horizon" -- it is **within `a_min` degrees of straight down**,
    and `a_min` moves with `mf`. Measured directly off `rad_engine.suns.suns_scene_text` on this
    machine, 2026-08-23:

        mf         1        2        4        6
        sources  145      577     2305     5185
        a_min   6.00 deg 3.10 deg 1.58 deg 1.06 deg

    **`90 - a_min` is the stricter end of a range; it is not the reachable set.** Bisected over the
    real `mf=1` dome while the aperture azimuth sweeps one lowest-ring patch spacing, the boundary
    runs 83.9672 degrees (azimuth halfway between two patches) to 84.0000 degrees (azimuth aligned
    with one), a spread of 0.033 degrees. So apertures shallower than -84 degrees do seal at some
    azimuths -- measured, 11 of 49 sampled azimuths at `b = 83.98` -- and `-84 degrees`
    **understates** the sealing set rather than overstating it, which is the safe direction for a
    caller reading it. What holds at every azimuth: a 45-degree downward-facing aperture is
    refused, and so is every wall at every orientation.
    `tests/test_project.py` asserts a 3-degrees-off-horizontal fixture seals and
    `tests/test_suns.py` measures the table against the real binary. **Note that `6/mf` is exact
    only at `mf=1`** -- 3.10 not 3.00, 1.58 not 1.50, 1.06 not 1.00 -- so nothing here derives
    `a_min` from a formula; the message reads it off the set it was actually given.

    **`tilt_deg` cannot describe this branch and is deliberately not used.** `apertures.derive` is
    `acos(abs(outward_normal[2]))`, and the `abs()` is sign-blind: a down-facing floor light and an
    up-facing skylight both report `tilt_deg == 0.0` and `azimuth_deg == 180.0` (the latter an
    `atan2(-0.0, -0.0)` artifact), while having 0 and all dome directions in hemisphere
    respectively. The message publishes `rad_engine.suns.exterior_elevation_deg` and the exterior
    normal's own signed vertical component instead -- the quantity the branch actually turns on.

    **This does NOT narrow the courtyard/lightwell false-refusal class** (ruling R143, and R137 on
    not claiming otherwise). A sun-facing aperture with a wall in the way has dome suns in its
    hemisphere by construction, so it takes the refusal exactly as it did before this branch
    existed. The design claim that this mechanism narrows that class is refuted: closing it is a
    question about **occlusion**, which no dot product over a dome can answer, and which needs a
    probe this server did not then build. What the branch does buy is that the guard no longer
    concludes "does not transmit" in a case where the geometry makes all-zero the only possible
    answer.

    ## The occlusion probe -- rulings R831 and R848

    The occlusion probe R143 found this class needs is now built. `sun_reaches_grid` is its
    verdict, computed by `rad_engine.jobs.probe_ase_occlusion` at the `asemx` commit, and only on
    the path that would otherwise refuse: the matrix all zero and, where an aperture is given, a
    dome direction in front of it. From every grid point one ray is traced along each dome
    direction in front of any window group's aperture, through the registered scene with **every**
    window group's aperture polygons removed and a unit glow sky added, at `-ab 0 -av 0 0 0`
    (`rad_engine.visibility.any_sun_reaches`; a per-group probe, below, traces each ray's radiance
    twice with `rad_engine.visibility.sun_ray_radiance` instead), so a ray is followed through
    glass and the other transmitters a direct ray follows, and one that meets a sky or a light in
    the scene carries radiance too. **None carries any** (`False`) -> no sun reaches the grid
    through any opening whatever the openings' own materials: the matrix seals with
    `ASE_DIRECT_ALL_ZERO_OCCLUDED` at `info`. **Any does** (`True`) -> light from the sky reaches
    the grid along a sun direction with the openings open while the matrix saw none: directly,
    through an aperture that does not transmit, or by a path the probe follows and the matrix does
    not (for example a specular reflection or a scene light). Refused as before, and the refusal
    names both kinds of explanation, giving those as examples, rather than concluding the first.
    **The probe gave no verdict** (`None` with
    `probe_no_verdict`, its reason: no dome direction in front of any aperture, a scene changed
    since submit, an aperture polygon the strip left in place, a glazing surface a two-phase strip
    left in place, a material whose transmission the probe's ray does not follow -- `dielectric`
    among them since ruling R857 -- or a failure) -> refused as before, naming the reason. **No
    probe ran** (`None` without a reason: no Radiance install, or no sun directions on the run)
    -> refused as before (R856: a two-phase run now carries a probe of its own, below).

    **A two-phase run** (ruling R856) resolves no window group, so its whole-grid probe removes
    every glazing surface of the scene instead of opening apertures, and casts every dome
    direction. `two_phase_probe` says this probe ran, and every wording that differs between the
    two probes keys on it, never on `stripped_glazing`: that names what the probe removed and is
    empty for a scene with no glazing (an unglazed opening), where the text says "the scene has
    none". On a two-phase probe neither the notice nor the refusal names a window group.

    **A per-group probe** (ruling R858, `group_probe`) traces every ray with every group's aperture
    removed and again with this group's aperture closed, and finds sun through this group's
    opening where closing it dims some ray. Its notice says no sun reaches the grid through this
    group's opening (not through any opening: another group may be lit); its refusal says light
    reaches the grid through this group's opening, names the blind schedule (not ASE) as what an
    all-zero per-group matrix would mislead, says a group in series with another gives that answer
    with correct glazing, since this group's matrix is traced with every other group's aperture
    blacked, and then that this run cannot build the matrix the schedule needs; the aperture cause
    and repair follow, conditional on the light not passing another group. A two-phase probe opens
    no window group, so `two_phase_probe` and `group_probe` together are a `ValueError`.

    **The ASE_net matrix's probe** (ruling R869, `net_probe`) is the whole-grid probe -- or, on a
    two-phase run, the strip-mode one, so `net_probe` with `two_phase_probe` is legal -- with the
    aperture polygons of the window groups marked automated closed by an opaque black surface.
    Its notice says ASE_net is 0 % because no sun reaches the grid through any opening once those
    groups are closed, and that ASE_gross is tallied from the whole-grid matrix; its refusal says
    ASE_net would be 0.0 % from a run that measured nothing through the openings that remain --
    "while the probe found sun reaching the grid" only where it did -- then gives the run's
    chain's cause and repair, and says what was sealed before this matrix (the whole-grid
    matrix and 'illdir:<grid>' among it) and what was not. A two-phase probe that found no glazing
    opened nothing, so its net lead does not say "through any opening" (W-M5). The cause and repair
    are a two-phase run's on `two_phase_probe` or `chain == "2phase"`, for every matrix, so a
    two-phase run whose probe did not run is not given the three-phase placeholder cause. Every
    wording keys on the flags or the chain, never on `stripped_glazing` (R856). A per-group matrix
    is not an ASE_net one, so `net_probe` with `group_probe` is a `ValueError`.

    It is honoured with or without an aperture, because the probe is the stronger evidence: the
    hemisphere test asks whether a sun *could* be in front of the opening, the probe whether one
    gets through the scene to it. The no-sun branch above is still answered first, because its
    reason is arithmetic and needs no trace. On a per-group matrix the notice says only that no
    sun reaches the grid through any opening (through this group's opening, under a per-group
    probe); ASE is tallied from the whole-grid matrix, whose
    own notice is the one that says ASE is 0 %.

    **What the probe cannot see, and the direction it errs.** Removing a polygon, a light in the
    scene and a specular reflection to the sky (a mirror, a slightly specular `plastic` wall, a
    glazed skin on a wall: measured by the Task 12 re-review) can only make more rays carry
    radiance, so each refuses more, never seals more. A `dielectric` surface gives no verdict
    (ruling R857): a lone single surface whose normal faces the grid, or an inverted slab, traps
    every probe ray, so following it could have sealed a lit room.
    Two wrong seals are known: a surface outside every window group that should transmit but was
    given an opaque material blocks the probe exactly as it blocks the matrix, and a path whose
    transmitted weight is below `rad_engine.visibility.PROBE_LIMIT_WEIGHT` (1e-9 of the sky's
    radiance) is cut off as blocked. The notice states the first.

    **Why sealing is the correct answer where it does fire.** No dome direction in front of the
    aperture means no `source` in the ASE octree can reach a sensor through it, whatever the
    aperture's material -- so the all-zero matrix is arithmetically forced, and the `ASE = 0 %` it
    leads to is the number a correctly modelled aperture would also have produced. The guard exists
    to stop a favourable verdict arriving from a run that produced nothing; there, the favourable
    verdict is the only one available.

    ## The set is CHECKED, not merely documented (fix round 2)

    A message caveat cannot make an unsound seal sound. If a caller hands a weather file's sun path
    instead of the dome, this branch seals a vertical north wall -- publishing `ASE = 0 %` from an
    opaque aperture, which is the exact failure the guard exists to prevent, arriving through it.
    So the set is checked: when `aperture`/`suns` are supplied for an `asemx`, `mf` is required and
    `len(suns)` must equal `rad_engine.suns.sun_count(mf)`.

    `mf` is not a new input -- it is already in the artifact's own `parameters` (`chain.py`'s
    `asemx` `ArtifactSpec` records it), so `register_artifact` reads it from there rather than
    asking a caller to state it twice.

    **The count is a necessary condition and is not claimed to be more.** It does not prove the
    directions are the dome's; it makes the wrong-set failure loud at the cheapest possible price,
    and it is the only check available that does not re-derive the Reinhart table inside this
    function -- a second definition of a quantity `rad_engine.suns` already owns.

    ## Arguments, and the two absences that are refused rather than read

    `aperture` and `suns` are **both or neither** (`ValueError`), and an **empty** `suns` is a
    `ValueError` too. `rad_engine.suns.suns_in_hemisphere(aperture, ())` is arithmetically 0, and
    0 there means *nothing was supplied* -- indistinguishable, at this seam, from *nothing is in the
    hemisphere*. Sealing on it would publish the most favourable verdict LM-83 has because a caller
    forgot an argument, which is the exact failure mode this guard exists for, arriving through the
    guard instead of past it. Both checks run before the `kind` test, so a caller that pairs them
    wrongly finds out on **every** seal rather than only on the rare all-zero `asemx`.

    Supplying neither is the ordinary case and is not an error: the guard then has no geometry to
    reason from and **fails closed**, refusing exactly as it did before this pair existed.

    **Here rather than at read time, and that placement is a ruling.** Three reasons, all of which
    survived contact with the code:

    - Radiance knowledge belongs in `rad_engine`, and an `rcontrib` coefficient matrix's header is
      Radiance knowledge -- it carries no `NROWS`, so `rad_engine.results.read_annual_header`
      refuses it outright (`coefficient_matrix_is_all_zero`'s own docstring carries the measurement).
    - The bytes are already being streamed at this exact moment: `file_digest(path)` two statements
      below reads the whole file regardless, so the pass is proportionate rather than additional.
      Measured worst case for the scan alone, on a synthetic all-zero office-shaped matrix
      (456 x 2 306 x 3, 41 MB ASCII): 0.33 s. The passing case stops at the first non-zero cell.
    - **An opaque aperture must fail the run, not every later read.** Refusing here fails the job
      at the step that built the matrix, with `ASE_DIRECT_MATRIX_EMPTY` on the failed record
      (`jobs.JobStore._work` reads `ProjectError.advisory` rather than flattening the refusal), so
      `illdir:<grid>` is never multiplied and never sealed **on the refusing path**. That
      qualification is load-bearing since the sun-geometry branch below: an all-zero `asemx` inside
      the cone that branch describes *is* sealed, and `illdir:` is multiplied from it -- correctly,
      to an all-zero result carrying `ASE_DIRECT_MATRIX_ALL_ZERO`. The unqualified sentence stood
      here through fix round 1 and is the third site of the same false claim in this repo.
      A read-time check would instead pay ~3.1 M floats on **every**
      `results(action="compliance")` call to re-derive a fact the run already knew, and would
      report the failure at a call that did nothing wrong.

    **Beside `check_edges`, deliberately.** That is the other kind-keyed refusal this function
    already makes, and it establishes the precedent: sealing is where an artifact's own kind gets
    its last word. Above `_fsync_path`, the digest and every mutation of the manifest, so a refused
    matrix leaves no entry behind -- an entry for a matrix this server just refused would be
    reported valid and reused for ever by `is_reusable`.

    **No other kind is scanned.** `ASE_DIRECT_MATRIX_EMPTY`'s register row is about ASE's
    coefficient matrix, and an advisory whose definition is untrue of its condition is a defect in
    its own right (docs/tool-surface.md section 9). An all-zero `dsmx`, `vmx` or `.ill` is a
    different fact with a different repair and no allocated code here.
    """
    if net_probe and group_probe:
        # R869 with R858: the ASE_net probe closes the automated groups on a whole-grid matrix and
        # a per-group probe closes one group on that group's matrix; both is two specs mixed.
        raise ValueError(
            f"artifact {artifact_id!r} was given both an ASE_net probe (ase_probe_net) and a "
            "per-group probe (ase_probe_group). The ASE_net probe closes the window groups marked "
            "automated on a whole-grid matrix, and a per-group probe closes one group on that "
            "group's matrix; at most one of the two is set."
        )
    if two_phase_probe and group_probe:
        # R858 with R856: a two-phase probe opens no window group, so it closes none either; a
        # caller setting both has mixed two specs, and the wording would follow whichever flag a
        # branch happens to test first.
        raise ValueError(
            f"artifact {artifact_id!r} was given both a two-phase probe (ase_probe_two_phase) and "
            "a per-group probe (ase_probe_group). A two-phase probe strips every glazing surface "
            "and opens no window group, so it cannot close one; at most one of the two is set."
        )
    if chain not in (None, "2phase", "3phase", "5phase"):
        raise ValueError(
            f"artifact {artifact_id!r} was given ase_chain={chain!r}; it is '2phase', '3phase', "
            "'5phase' or None."
        )
    if (two_phase_probe and chain not in (None, "2phase")) or (
        group_probe and chain not in (None, "3phase")
    ):
        # Final review A1: a two-phase probe is a two-phase run's, and a per-group matrix is
        # planned beside the three-phase chain only (`chain.direct_sun_steps`); a disagreeing
        # chain is two specs mixed, and the "what is lost" text would follow the wrong one.
        raise ValueError(
            f"artifact {artifact_id!r} was given ase_chain={chain!r} with a "
            f"{'two-phase' if two_phase_probe else 'per-group'} probe. A two-phase probe is a "
            "two-phase run's, and a per-group matrix is planned beside the three-phase chain only."
        )
    if (aperture is None) != (suns is None):
        raise ValueError(
            "register_artifact's ase_aperture and ase_suns are supplied together or not at all: "
            f"got ase_aperture={'set' if aperture is not None else 'None'}, "
            f"ase_suns={'set' if suns is not None else 'None'}. The sun-geometry test needs both, "
            "and half of it would silently degrade to the transmittance-only guard."
        )
    if suns is not None and len(suns) == 0:
        raise ValueError(
            "register_artifact was given an empty ase_suns. suns_in_hemisphere(aperture, ()) is "
            "arithmetically 0, and 0 there means 'nothing was supplied', which is not the same "
            "fact as 'no sun lies in this aperture's exterior hemisphere'. Sealing an all-zero "
            "asemx on it would publish ASE = 0 % -- the most favourable verdict LM-83 has -- "
            "because a caller passed an empty list. Pass the Reinhart patch directions this asemx "
            "was built over, or pass neither argument and take the transmittance-only refusal."
        )
    if kind != "asemx":
        return None
    if aperture is not None and suns is not None:
        # **The contract check, above the matrix read.** A caller that hands the wrong set finds
        # out on every `asemx` seal, not only on the rare all-zero one -- the same reasoning the
        # two argument refusals above are placed by.
        if not isinstance(mf, int):
            raise ValueError(
                f"artifact {artifact_id!r} was given ase_aperture/ase_suns but its parameters "
                f"carry no integer 'mf' (got {mf!r}). The sun set this guard may reason from is "
                "the Reinhart dome the matrix was built over, and its size is a function of mf "
                "alone; with no mf there is nothing to check the set against, and an unchecked "
                "set is how a five-hour sun path would be mistaken for a 145-direction dome."
            )
        if len(suns) != sun_count(mf):
            raise ValueError(
                f"artifact {artifact_id!r} was given {len(suns)} sun direction(s) but its "
                f"asemx was built at mf={mf}, whose suns scene carries exactly {sun_count(mf)} "
                "(rad_engine.suns.sun_count). This guard's conclusion is only sound over the set "
                "the MATRIX was built over -- build_ase_octree compiles suns_scene_text(mf) and "
                "build_ase_direct_argv bins every one of those sources -- so a different set is "
                "a claim about a quantity this artifact does not contain. A weather file's sun "
                "path is the mistake this refuses: it would seal an opaque north wall, publishing "
                "ASE = 0 % from a matrix that measured nothing. **The count is a necessary "
                "condition, not a proof the directions are right**, and it is not claimed as one; "
                "it is the cheapest check that makes the wrong-set failure loud."
            )
    if not coefficient_matrix_is_all_zero(path):
        return None
    if aperture is not None and suns is not None and suns_in_hemisphere(aperture, suns) == 0:
        elevation = exterior_elevation_deg(aperture)
        lowest_ring = min(sun.altitude_deg for sun in suns)
        return EngineAdvisory(
            code="ASE_DIRECT_ALL_ZERO_NO_SUN",
            severity="info",
            message=(
                # Reachable only for a per-group matrix: `chain._direct_sun_leg` supplies
                # `ase_aperture` only with a window group, so the lead names the group's matrix,
                # as the per-group `ASE_DIRECT_ALL_ZERO_OCCLUDED` lead does.
                f"every cell of {artifact_id!r}, this window group's ASE direct-sun coefficient "
                "matrix, is zero, "
                f"and none of the {len(suns)} sun direction(s) this matrix was built over lies in "
                "the aperture's exterior hemisphere. Those directions are the Reinhart sky-patch "
                f"centres the ASE octree's sun sources sit at (mf={mf}, so {sun_count(mf)} of "
                f"them, the lowest ring at {lowest_ring:.2f} degrees altitude) -- this matrix "
                "carries no weather; which of those directions the sun visits, and when, arrives "
                "later as smxsun. This aperture's exterior normal has a vertical component of "
                f"{exterior_normal(aperture)[2]:+.4f}, i.e. it points {abs(elevation):.1f} degrees "
                f"{'below' if elevation < 0 else 'above'} horizontal. With no dome direction in "
                "front of the aperture, no sun source can reach a sensor through it whatever its "
                "material, so all zero is arithmetically forced and the matrix is sealed rather "
                "than refused. "
                "**The set of orientations that can reach this branch is one small cone, and it "
                f"depends on mf.** A dome direction at altitude a is in front of an exterior "
                "normal at elevation -b whenever a + b < 90, so the branch needs "
                f"b >= 90 - {lowest_ring:.2f} = {90.0 - lowest_ring:.2f} degrees below horizontal "
                f"-- within {lowest_ring:.2f} degrees of straight down, which is this dome's own "
                "lowest ring and therefore moves with mf (measured: 6.00 degrees at mf 1, 3.10 at "
                "mf 2, 1.58 at mf 4, 1.06 at mf 6). A 45-degree downward-facing aperture is NOT in "
                "it and is refused. "
                "**That bound is the aligned case and is not exact to the last decimal.** It holds "
                "when the aperture's azimuth coincides with a lowest-ring patch; an aperture "
                "azimuth halfway between two of them seals very slightly sooner, because the "
                "nearest patch is off to one side. Measured over the mf 1 dome, the boundary runs "
                "from 83.9672 to 84.0000 degrees, a spread of 0.033 degrees, and the figure quoted "
                "above is the STRICTER end -- it understates the sealing set rather than "
                "overstating it. "
                "**What was checked about this set, exactly.** Its CARDINALITY was checked against "
                f"rad_engine.suns.sun_count({mf}); the directions themselves were not, because "
                "verifying them would mean re-deriving the Reinhart table inside the guard. So "
                "this advisory reports what it measured over the set it was handed, and the claim "
                "that the set IS the dome rests on the count and on the caller, not on a proof. "
                "This is NOT a narrowing of the courtyard/lightwell false-refusal class: "
                "a sun-facing aperture that is fully occluded -- a courtyard or lightwell -- "
                "produces the identical all-zero matrix and does not reach this branch, because "
                "the Reinhart dome spans every azimuth and every altitude ring, so any aperture "
                "outside that cone has dome directions in front of it. Such a matrix is sealed "
                "with ASE_DIRECT_ALL_ZERO_OCCLUDED when the occlusion probe finds that no sun "
                "reaches the grid, and refused with ASE_DIRECT_MATRIX_EMPTY otherwise."
            ),
            lines=(),
        )
    #: R856: what a two-phase probe removed, as the notice and the refusal both name it.
    glazing_removed = (
        f"every glazing surface removed ({', '.join(stripped_glazing) or 'the scene has none'})"
    )
    #: R869: what the ASE_net probe opened and cast along, as its notice and refusal name it.
    net_opened = (
        f"every sun direction this matrix was built over, through the registered scene with "
        f"{glazing_removed}"
        if two_phase_probe
        # A two-phase net matrix whose probe did not run: what the probe does on that chain.
        else "every sun direction this matrix was built over, through the registered scene with "
        "every glazing surface removed"
        if chain == "2phase"
        else "each sun direction this matrix was built over that lies in front of any window "
        "group's aperture, through the registered scene with every window group's aperture "
        "polygons removed"
    )
    if sun_reaches_grid is False:
        if net_probe:
            # R869: the whole-grid (or strip-mode) probe with the automated groups closed. It says
            # nothing about the automated groups' own openings, which the ASE_net run blacks.
            probe_text = (
                f"The probe traced one ray from every grid point along {net_opened}, the aperture "
                "polygons of the window groups marked automated then closed by an opaque black "
                "surface, and a unit sky added, following each ray through glass and the other "
                "transmitters a direct ray follows; none carried any light out. "
                # Fix round 2, Minor B: with no glazing found the lead names the cases; a claim
                # about transmitting glazing would contradict it.
                + (
                    ""
                    if two_phase_probe and not stripped_glazing
                    else "A courtyard, a lightwell or a window that is not automated facing a wall "
                    "that rises above every sun direction gives this answer with correctly "
                    "modelled, transmitting glazing. "
                )
                + "What the probe cannot see: a surface outside every window group that should "
                "transmit but was given an opaque material blocks the probe as it blocks the "
                "matrix"
                + (
                    "; a window or skylight drawn as a surface with an opaque material is not "
                    "glazing to the probe"
                    if two_phase_probe
                    else ""
                )
                + ". Before reporting this result, check that no glazed surface on the sun's path "
                "to this grid was given an opaque material."
            )
        elif group_probe:
            # R858: a per-group probe's difference trace -- every ray traced with every group's
            # aperture removed, then again with this group's closed. It says nothing about the
            # other groups, which may well be lit.
            probe_text = (
                "The probe traced one ray from every grid point along each sun direction this "
                "matrix was built over that lies in front of any window group's aperture, through "
                "the registered scene with every window group's aperture polygons removed and a "
                "unit sky added, following each ray through glass and the other transmitters a "
                "direct ray follows, and again with this window group's aperture polygons closed "
                "by an opaque black surface; no ray carried less light with this group's aperture "
                "closed than with it open, so no sun reaches the grid through this group's "
                "opening. Massing that shades this group's window while another group's is lit "
                "gives this answer with a correctly modelled, transmitting aperture. What the "
                "probe cannot see: a surface outside every window group that should transmit but "
                "was given an opaque material blocks the probe as it blocks the matrix. Before "
                "reporting this result, check that no glazed surface on the sun's path to this "
                "grid was given an opaque material."
            )
        elif two_phase_probe:
            # R856: a two-phase run's probe, which removed every glazing surface instead of
            # opening window groups' apertures, and cast every dome direction.
            probe_text = (
                "The probe traced one ray from every grid point along every sun direction this "
                f"matrix was built over, through the registered scene with {glazing_removed} and "
                "a unit sky added; none carried any light out. A courtyard, a lightwell or a "
                "window facing a wall that rises above every sun direction gives this answer. "
                "What the probe cannot see: a window or skylight drawn as a surface with an "
                "opaque material is not glazing to the probe, and blocks it as it blocks the "
                "matrix. Before reporting this result, check the sun's path to this grid for a "
                "glazed surface given an opaque material."
            )
        else:
            probe_text = (
                "The probe traced one ray from every grid point along each sun direction this "
                "matrix was built over that lies in front of any window group's aperture, through "
                "the registered scene with every window group's aperture polygons removed and a "
                "unit sky added, following each ray through glass and the other transmitters a "
                "direct ray follows; none carried any light out. A courtyard, a lightwell or a "
                "window facing a wall that rises above every sun direction gives this answer with "
                "a correctly modelled, transmitting aperture. What the probe cannot see: a surface "
                "outside every window group that should transmit but was given an opaque material "
                "blocks the probe as it blocks the matrix. Before reporting this result, check "
                "that no glazed surface on the sun's path to this grid was given an opaque "
                "material."
            )
        if net_probe and two_phase_probe and not stripped_glazing:
            # Task 4 review, Minor 2: W-M5 for ASE_net. With no glazing found the probe opened
            # nothing, so "through any opening" would describe an opening it never opened.
            lead = (
                f"every cell of {artifact_id!r}, the ASE_net direct-sun coefficient matrix (the "
                "window groups marked automated blacked), is zero, and the occlusion probe found "
                "that no sun reaches the grid with those groups' apertures closed, so the matrix "
                "is sealed rather than refused and ASE_net is 0 %. ASE_gross is tallied from the "
                "whole-grid matrix with every aperture open. The scene has no glazing surface, so "
                "the probe traced it as it stands with those groups' apertures closed: an "
                "unglazed opening facing massing that occludes every sun direction and a grid "
                "enclosed by opaque surfaces give this same answer. "
            )
        elif net_probe:
            lead = (
                f"every cell of {artifact_id!r}, the ASE_net direct-sun coefficient matrix (the "
                "window groups marked automated blacked), is zero, and the occlusion probe found "
                "that no sun reaches the grid through any opening once those groups' apertures "
                "are closed, so the matrix is sealed rather than refused and ASE_net is 0 %. "
                "ASE_gross is tallied from the whole-grid matrix with every aperture open. "
            )
        elif group_probe:
            lead = (
                f"every cell of {artifact_id!r}, this window group's ASE direct-sun coefficient "
                "matrix, is zero, and the occlusion probe found that no sun reaches the grid "
                "through this group's opening, so the matrix is sealed rather than refused. It is "
                "the per-group matrix the blind schedule reads; ASE is tallied from the whole-grid "
                "matrix, and this notice says nothing about the published ASE. "
            )
        elif aperture is None and two_phase_probe and not stripped_glazing:
            # Final review (W-M5): a two-phase probe that found no glazing opened nothing, so
            # "through any opening" would describe an opening the scene may not have; an unglazed
            # opening facing occluding massing and a grid enclosed by opaque surfaces (a window
            # drawn with an opaque material, the caveat below) give this same answer.
            lead = (
                f"every cell of {artifact_id!r}, the ASE direct-sun coefficient matrix, is zero, "
                "and the occlusion probe found that no sun reaches the grid, so the matrix is "
                "sealed rather than refused and ASE is 0 % because the geometry blocks every sun "
                "direction. The scene has no glazing surface, so the probe traced it as it "
                "stands: an unglazed opening facing massing that occludes every sun direction and "
                "a grid enclosed by opaque surfaces give this same answer. "
            )
        elif aperture is None:
            lead = (
                f"every cell of {artifact_id!r}, the ASE direct-sun coefficient matrix, is zero, "
                "and the occlusion probe found that no sun reaches the grid through any opening, "
                "so the matrix is sealed rather than refused and ASE is 0 % because the geometry "
                "blocks every sun direction. "
            )
        else:
            lead = (
                f"every cell of {artifact_id!r}, this window group's ASE direct-sun coefficient "
                "matrix, is zero, and the occlusion probe found that no sun reaches the grid "
                "through any opening, so the matrix is sealed rather than refused. It is the "
                "per-group matrix the blind schedule reads; ASE is tallied from the whole-grid "
                "matrix, and this notice says nothing about the published ASE. "
            )
        return EngineAdvisory(
            code="ASE_DIRECT_ALL_ZERO_OCCLUDED", severity="info", message=lead + probe_text,
            lines=(),
        )
    raise AseDirectMatrixEmptyError(
        f"artifact {artifact_id!r} is an ASE direct-sun coefficient matrix in which every cell is "
        f"zero, at {path}. "
        + (
            # R858: a per-group matrix is not what ASE is tallied from; what it feeds is the blind
            # schedule, so that is the consequence named.
            "No sensor in this grid received any contribution from any sun direction through this "
            "window group's opening, so the per-group direct-sun result multiplied from it would "
            # Final review (W-M2): "never close this group's blinds" was false under the ordered
            # prefix scan, which closes a group on any hour an earlier group's closing does not
            # meet the criterion; what the schedule loses is this group's sun in its count.
            "be identically zero and the blind schedule, which reads it, would count no sun "
            "through this group -- from a run that produced nothing at all. ASE is tallied from "
            "the whole-grid matrix, not from this one. This is refused rather than sealed. "
            if group_probe
            # R869: the ASE_net matrix. "While the probe found sun" is said only where it did.
            else "No sensor in this grid received any contribution from any sun direction with the "
            "window groups marked automated blacked, while the probe found sun reaching the grid "
            "with those groups closed, so ASE_net would be 0.0 % from a run that measured nothing "
            "through the openings that remain. This is refused rather than sealed. "
            if net_probe and sun_reaches_grid
            else "No sensor in this grid received any contribution from any sun direction with the "
            "window groups marked automated blacked, so ASE_net would be 0.0 % from a run that may "
            "have measured nothing through the openings that remain. This is refused rather than "
            "sealed. "
            if net_probe
            else "No sensor in this grid received any contribution from any sun direction, "
            "so the direct-sun result 'illdir:' multiplied from it would be identically zero, "
            "annual sunlight exposure would be 0.0 % and its band 'clearly_acceptable' -- the most "
            "favourable verdict LM-83 has, published from a run that produced nothing at all. This "
            "is refused rather than sealed. "
        )
        + (
            # R858: a per-group probe that found light gives its cause and repair inside its own
            # sentence below, conditioned on whether the light passes another window group.
            ""
            if group_probe and sun_reaches_grid
            # R856: a two-phase run has no aperture polygon and no BSDF, so its cause and repair
            # name the glazing; the window-group text is unchanged.
            else "The usual cause on a two-phase run is a glazing surface whose material the "
            "matrix's direct ray does not pass. The repair is to give it a transmitting material "
            f"the direct ray follows, {TRANSMITTING_MATERIAL_REPAIR}"
            # Task 4 review, Minor 1: a two-phase chain whose probe did not run is still a
            # two-phase run, and has no aperture polygon and no BSDF either.
            if (two_phase_probe or chain == "2phase")
            and not (sun_reaches_grid and not stripped_glazing)
            # A two-phase probe that removed nothing and found light traced the matrix's scene
            # with a unit sky for the sun sources: no glazing material can be the cause.
            else ""
            if two_phase_probe or chain == "2phase"
            else "The usual cause is an aperture polygon whose Radiance modifier "
            "does not transmit: in a three-phase model the transmission IS the BSDF and the "
            "polygon is often a placeholder, so nothing before this point requires it to be "
            "glazing. The repair is to model the glazing -- give the aperture polygon a "
            f"transmitting material, {TRANSMITTING_MATERIAL_REPAIR}"
        )
        + (
            # R869: the ASE_net probe found light with the automated groups closed.
            "The occlusion probe ran and found that light from the sky reaches the grid along a "
            f"sun direction with {glazing_removed} and the aperture polygons of the window groups "
            "marked automated closed, while every cell of this matrix is zero. That path may be "
            "direct, through a glazing surface whose material the matrix's direct ray does not "
            "pass, or it may be one the probe follows and this matrix does not, for example a "
            "specular reflection off a scene surface or the light of a lamp in the scene; the "
            "probe does not tell these apart, so check the glazing's material first. "
            if sun_reaches_grid and net_probe and two_phase_probe and stripped_glazing
            else "The occlusion probe ran and found that light from the sky reaches the grid along "
            f"a sun direction with {glazing_removed} and the aperture polygons of the window "
            "groups marked automated closed, while every cell of this matrix is zero. With no "
            "glazing removed, the probe traced this matrix's scene with those groups' apertures "
            "closed and a unit sky in place of the sun sources, so the path is one the probe "
            "follows and this matrix does not, for example a specular reflection off a scene "
            "surface or the light of a lamp in the scene. "
            if sun_reaches_grid and net_probe and two_phase_probe
            else "The occlusion probe ran and found that light from the sky reaches the grid along "
            "a sun direction with every window group's aperture opened and then the aperture "
            "polygons of the window groups marked automated closed, while every cell of this "
            "matrix is zero. That path may be direct, through an aperture that does not transmit, "
            "or it may be one the probe follows and this matrix does not, for example a specular "
            "reflection off a scene surface or the light of a lamp in the scene; the probe does "
            "not tell these apart, so check the aperture's material first. "
            if sun_reaches_grid and net_probe
            # R856: a two-phase probe removed glazing rather than opening apertures, so its
            # sentence names the glazing; the window-group sentence is unchanged.
            else "The occlusion probe ran and found that light from the sky reaches the grid "
            f"along a sun direction with {glazing_removed}, while every cell of this matrix is "
            "zero. That "
            "path may be direct, through a glazing surface whose material the matrix's direct ray "
            "does not pass, or it may be one the probe follows and this matrix does not, for "
            "example a specular reflection off a scene surface or the light of a lamp in the "
            "scene; the probe does not tell these apart, so check the glazing's material first. "
            if sun_reaches_grid and two_phase_probe and stripped_glazing
            else "The occlusion probe ran and found that light from the sky reaches the grid "
            f"along a sun direction with {glazing_removed}, while every cell of this matrix is "
            "zero. With nothing removed, the probe traced this matrix's scene with a unit sky in "
            "place of the sun sources, so the path is one the probe follows and this matrix does "
            "not, for example a specular reflection off a scene surface or the light of a lamp in "
            "the scene. "
            if sun_reaches_grid and two_phase_probe
            # R858: a per-group probe found a ray that closing this group dims. A group in series
            # with another gives that with correct glazing, since this group's matrix is traced
            # with every other group's aperture blacked; the cause above must not be the only one
            # offered.
            else "The occlusion probe ran and found that light from the sky reaches the grid "
            "through this window group's opening: a ray that reaches the grid with every window "
            "group's aperture opened carries less light when this group's aperture alone is "
            "closed, while every cell of this matrix is zero. A group in series with another (a "
            "light well, an outer skin) gives this answer with correct glazing, because this "
            "matrix is traced with every other group's aperture blacked, so light that passes "
            "another group on its way through this one is not in it. If this group's light passes "
            "another window group, this run cannot build the per-group matrix the blind schedule "
            "needs, since the schedule needs every direct path from a sun to a sensor to cross "
            # Final review (W-I1, D-M8): what a per-group refusal loses is stated once, measured,
            # in the paragraph below; here only that this grid's per-group schedule cannot be had.
            "exactly one window group's aperture and this path crosses two, so this grid's "
            "per-group blind schedule cannot be sealed. If this group's light reaches the grid "
            "without passing another window group, "
            "the usual cause is an aperture polygon whose Radiance modifier does not transmit: in "
            "a three-phase model the transmission IS the BSDF and the polygon is often a "
            "placeholder, so nothing before this point requires it to be glazing. The repair is "
            "then to model the glazing -- give the aperture polygon a transmitting material, "
            f"{TRANSMITTING_MATERIAL_REPAIR}The "
            "path may also be one the probe follows and this matrix does not, for example a "
            "specular reflection off a scene surface or the light of a lamp in the scene; the "
            "probe does not tell these apart. "
            if sun_reaches_grid and group_probe
            else "The occlusion probe ran and found that light from the sky reaches the grid "
            "along a sun direction with every window group's aperture opened, while every cell of "
            "this matrix is zero. That path may be direct, through an aperture that does not "
            "transmit, or it may be one the probe follows and this matrix does not, for example a "
            "specular reflection off a scene surface or the light of a lamp in the scene; the "
            "probe does not tell these apart, so check the aperture's material first. "
            if sun_reaches_grid
            else f"The occlusion probe ran and gave no verdict, because {probe_no_verdict}; the "
            "probe also gives none when the scene changed "
            "after the job was submitted, when removing every glazing surface leaves one in the "
            "scene, when the scene uses a material whose transmission a direct ray does not "
            "follow, and when it fails. "
            if probe_no_verdict and two_phase_probe
            else f"The occlusion probe ran and gave no verdict, because {probe_no_verdict}; the "
            "probe also gives none when no dome direction lies in front of any window group's "
            "aperture, when the scene changed after the job was submitted, when removing the "
            "aperture modifiers leaves an aperture polygon in the scene, when the scene uses a "
            "material whose transmission a direct ray does not follow, and when it fails. "
            + (
                # R858: the difference trace's own no-verdict case.
                "For a per-group matrix it also gives none when a ray carries more light with "
                "this group's aperture closed than with it open. "
                if group_probe
                # R869: the net probe's own no-verdict case outside strip mode.
                else "For an ASE_net matrix it also gives none when an automated group's aperture "
                "is not among the openings it opens. "
                if net_probe
                else ""
            )
            if probe_no_verdict
            else "No occlusion probe ran for this matrix: the probe does not run without a "
            "Radiance install, or without the run's sun directions. "
        )
        + "This guard reads the coefficient matrix, which carries "
        "no weather: an all-zero 'illdir:' alone is NOT refused, because a deep room or a "
        "high-latitude winter can produce one from correct physics.\n"
        + (
            # R869: the ASE_net matrix's paragraph -- what its probe separates, and what was and
            # was not sealed before it, since the net leg runs after every other direct-sun run.
            "**One known false-refusal class, closed where the occlusion probe seals and still "
            "open where it gives no verdict or does not run.** This matrix is all-zero whenever "
            "no sensor sees the sun through an opening that remains once the window groups "
            "marked automated are blacked -- which a room whose remaining openings all face "
            "massing that occludes every sun direction satisfies with correct geometry and "
            "transmitting glazing. The occlusion probe is what separates the two: from every "
            f"grid point it traces one ray along {net_opened}, the automated groups' aperture "
            "polygons closed by an opaque black surface and a unit sky added, and an all-zero "
            "ASE_net matrix none of whose rays carries light out is sealed with "
            "ASE_DIRECT_ALL_ZERO_OCCLUDED instead of refused. The guard sits at seal time, so a "
            "refused run fails at the step that built this matrix. "
            + _NET_LOSS_BY_CHAIN[chain]
            if net_probe
            # R856: a two-phase run has no aperture for the sun-geometry branch to test, so its
            # paragraph names only the probe; the window-group paragraph is unchanged.
            else "**One known false-refusal class, closed where the occlusion probe seals and "
            "still open where it gives no verdict or does not run.** This "
            "matrix is all-zero whenever no sensor sees the sun from any sampled direction -- "
            "which a **courtyard or lightwell** space can satisfy with entirely correct geometry "
            "and perfectly transmitting glazing, because the surrounding massing occludes every "
            "sun direction rather than because anything is modelled wrong. "
            "The occlusion probe is what separates the two: from every grid point it traces one "
            "ray along every dome direction, through the registered scene with every glazing "
            "surface removed and a unit sky added, and an all-zero matrix none of whose rays "
            "carries light out is sealed with ASE_DIRECT_ALL_ZERO_OCCLUDED instead of refused. "
            "The guard sits at seal time, so a refused run fails at the step that built this "
            "matrix. What is actually lost is bounded and was measured: `_append_leg` appends the "
            "direct leg AFTER the chain and each artifact is committed as its step succeeds, so "
            "'ill:' is already sealed when this fires -- summary, points, timeseries, export and "
            "metrics(da) all still read. Lost are blind_schedule, and "
            "compliance(standard='lm83') on this two-phase chain. The guard was weighed against "
            "that cost and kept: it fails closed, it costs availability, and it never publishes "
            "a wrong number."
            # A two-phase run whose probe did not run (no sun directions) is still a two-phase
            # run: the chain says so (final review A1, `ArtifactSpec.ase_chain`).
            if two_phase_probe or chain == "2phase"
            else "**One known false-refusal class, closed where the occlusion probe runs and still "
            "open where it does not.** This matrix is all-zero whenever no sensor sees the sun in "
            "its own upper hemisphere from any sampled direction -- which a **courtyard or "
            "lightwell** space can satisfy with entirely correct geometry and a perfectly "
            "transmitting aperture, because the surrounding massing occludes every sun direction "
            "rather than because anything is modelled wrong. The sun-geometry branch added on "
            "2026-08-23 (ASE_DIRECT_ALL_ZERO_NO_SUN) does NOT reach it: that branch tests the "
            "aperture against the Reinhart sun DOME this matrix was built over, and the dome "
            "spans every azimuth and every altitude ring, so an occluded sun-facing aperture has "
            "dome directions in front of it. The seal is reachable only inside one small cone -- "
            "an exterior normal within the dome's own lowest-ring altitude of straight DOWN, "
            "measured 6.00 degrees at mf 1, 3.10 at mf 2, 1.58 at mf 4 and 1.06 at mf 6 -- so a "
            "45-degree downward-facing aperture is refused here too, and so is every wall at every "
            "orientation. The occlusion probe is what separates the two: from every grid point it "
            "traces one ray along each dome direction in front of any window group's aperture, "
            "through the registered scene with every window group's aperture polygons removed and "
            "a unit sky added, "
            + (
                # Final review (W-M3, D-M7): a per-group matrix is sealed by R858's rule alone, so
                # the whole-grid rule is not stated first.
                "and again with this window group's aperture polygons closed, and this group's "
                "all-zero matrix is sealed with ASE_DIRECT_ALL_ZERO_OCCLUDED instead of refused "
                "only where no ray carries less light closed than open. "
                if group_probe
                else "and an all-zero matrix none of whose rays carries light out is "
                "sealed with ASE_DIRECT_ALL_ZERO_OCCLUDED instead of refused. "
            )
            + "The guard sits at seal time, so a refused run fails at the step that built this "
            "matrix. "
            + (
                # Final review (W-I1, D-M8), measured through the real tool on a three-phase
                # blind_control='lm83' run refused at a per-group matrix: every annual read was
                # refused as having no annual result, and blind_schedule answered with the
                # whole-grid schedule only. A per-group matrix is planned beside the three-phase
                # chain alone, under lm83 (`chain.direct_sun_steps`, `chain.direct_sun_groups`);
                # that chain then seals no 'ill:<grid>' (`chain.three_phase_steps`), and its blend
                # is sealed after the schedule.
                # Final review A2 (item 5), measured: with no earlier result every annual read
                # was refused; after an earlier successful run on one group, a second group added
                # and refused here, every read answered from the earlier blend and schedule.
                # Final fix A, fix round 4, measured: since `results` compares the group set a
                # sealed schedule was sealed for (round 2, G1), compliance under lm83, metrics 'da'
                # and blind_schedule refuse it BLIND_SCHEDULE_NOT_CURRENT, and (final fix E1) every
                # other read refuses it WINDOW_GROUPS_NOT_CURRENT; until E1 summary, points,
                # timeseries, export, metrics without 'da' and compliance under en17037 answered
                # (round 5, N1, measured).
                # Round A3, measured: after a scene edit that changed the earlier group's direct
                # matrix, every read was refused as stale, and blind_schedule too -- this run's
                # 'illdir:<grid>' is current, but `_blind_schedule` hands a registered
                # 'sched:<grid>' to `_refuse_stale_results` (TestWhatARefusedAseRunLeavesReadable).
                "What is lost was measured: a per-group matrix is built only by a three-phase "
                "run_annual(..., blind_control='lm83') run, and that run seals no 'ill:<grid>' -- "
                "its annual result is the blend 'illblend:<grid>', sealed after the per-group "
                "schedule -- so this run publishes no annual result. The annual reads of this "
                "grid (summary, points, timeseries, export, metrics and compliance) answer only "
                "from an earlier run's result that is still registered and current -- a blend "
                "sealed before a window group was added answers none of them: "
                "compliance(standard='lm83') and metrics reporting DA_300 refuse it as "
                "BLIND_SCHEDULE_NOT_CURRENT, since the schedule that decided its hours has no "
                "column for the added group, and every other read as WINDOW_GROUPS_NOT_CURRENT, "
                "since no light through the added group's glazing is in it -- and are refused "
                "otherwise -- as having no annual result where none was sealed, and as stale "
                "where an input changed since. results(action='blind_schedule') can still answer "
                "with the whole-grid schedule derived from the current 'illdir:<grid>' (sealed or "
                "reused by this run before this matrix), and takes per-group states only from an "
                "earlier run's 'sched:<grid>' "
                "that is still registered and current ('groups' is null where none is "
                "registered); where that earlier 'sched:<grid>' is registered but stale -- an "
                "input changed since -- the whole call is refused as stale, and where it was "
                "sealed before a window group was added, as BLIND_SCHEDULE_NOT_CURRENT. The guard "
                "fails closed: it costs availability, and it never publishes a wrong number."
                if group_probe
                else _WHOLE_GRID_LOSS_BY_CHAIN[chain]
            )
        )
    )


def _artifact_warning(advisory: EngineAdvisory) -> ArtifactWarning:
    """`EngineAdvisory` -> the manifest's own record of it.

    A separate model rather than a shared one because `rad_core` may not import this layer; the
    mapping is one line and it belongs on the side that knows both.

    `ArtifactWarning` gained an optional `field` (ruling R24), but `EngineAdvisory` has none to carry
    across -- its own docstring is explicit that a stderr line qualifies the whole run rather than one
    result field, `rad_core.materials`'s `ArtifactWarning`-emitting findings are the ones `field`
    exists for (`_plausibility_warning`), and they never pass through here. If `EngineAdvisory` ever
    grows one, this mapping must carry it too.
    """
    return ArtifactWarning(
        code=advisory.code, severity=advisory.severity, message=advisory.message
    )


def is_reusable(project: LoadedProject, artifact_id: str) -> bool:
    """Whether `artifact_id` can be reused: four conditions, and `rad_core` can only answer one.

    The provenance digest must be unchanged, the file must **exist**, and its bytes must still be
    the bytes that were sealed (ruling R7). An artifact whose digest matches but whose file was
    deleted is not a matrix, it is a manifest entry; one whose file was truncated by a full disk or
    edited by hand is worse, because it is a matrix of the wrong numbers that nothing reports.

    An artifact with no `content_digest` is **not** reusable: it was sealed by something other than
    `register_artifact`, so its bytes cannot be proved current, and "cannot prove" is not "fine".

    **Ruling R26: the stored `built_from` must still satisfy `REQUIRED_EDGES`.** `role` sits outside
    every digest by design (`InputRecord.role`'s own docstring; a digest that included it would
    false-invalidate every project ruling R16's migration ever touches, since retagging is a metadata
    correction that moves no photon) -- so retagging *which* materials input is the ground moves no
    digest, and `valid` above stays `True` for an artifact built from an edge set this server would
    now refuse to build from. Without this check, `is_reusable` would say "yes" to exactly the edge
    set `check_edges`/`register_artifact` would say "no" to -- a contradiction, not a corner case,
    and Phase 4 finding the cached artifact reusable is the wrong answer arriving silently. `valid`
    itself is untouched: it still means only "the digest matches", the meaning `evaluate` and
    `ArtifactStatus` give it, and this function layers the edges check on top rather than folding it
    in. This generalises beyond `role`: any future tightening of `REQUIRED_EDGES` now correctly stops
    an artifact sealed under the old, looser table from being reused under the new one.

    Returns only `bool`, matching the contract the first three checks already had -- a caller that
    gets `False` and wants to know whether the cause was this new edges check (rather than a digest
    or file mismatch) re-runs `check_edges(project, artifact.kind, artifact.built_from)` directly on
    the same `(kind, built_from)` this function reads from the stored artifact; it raises
    `EdgesIncompleteError` (`ARTIFACT_EDGES_INCOMPLETE`) with a message naming the specific missing
    edge. That is the one existing, already-coded channel for the cause, so this does not invent a
    second, parallel one that could drift from what `check_edges` itself reports.

    ## Deliberately NOT converted to a held-descriptor read (Phase 7d-2 Task 1, ruling R255-1)

    That task narrowed every *artifact reader* in this repo from "hash the path, then open it again
    to read" to one held descriptor -- `rad_engine.results.iter_verified_annual_lux_rows`, and
    through it `rad_mcp.results_tool._lux_rows`, `rad_engine.blend` and `rad_engine.schedule`. The
    `file_digest` call below looks like the same shape and **is not**: this function digests the
    artifact and then re-reads **nothing**. There is no window between a check and a use here,
    because there is no use. Converting it would buy no property and would couple a predicate to a
    streaming reader.

    (The plan for that task calls this `verify_artifact`, citing a line number. No function of that
    name exists in this tree; the line it named was inside this function's body. The tree wins, and
    the note is left here so the next reader looking for it finds the answer rather than the name.)
    """
    try:
        artifact = project.manifest.artifact(artifact_id)
    except KeyError:
        return False
    if not evaluate(project.manifest)[artifact_id].valid:
        return False
    if artifact.content_digest is None or not Path(artifact.path).is_file():
        return False
    if file_digest(artifact.path) != artifact.content_digest:
        return False
    try:
        check_edges(project, artifact.kind, artifact.built_from)
    except EdgesIncompleteError:
        return False
    return True


# --------------------------------------------------------------------------------------------
# describe and update
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Description:
    """`project(action="describe")`'s provenance view, before `rad_mcp` shapes it for the wire."""

    manifest: Manifest
    statuses: dict[str, ArtifactStatus]
    reuse: ReuseSummary
    advisories: tuple[EngineAdvisory, ...] = ()


def evaluate_loaded(manifest: Manifest) -> dict[str, ArtifactStatus]:
    """`evaluate`, with `rad_core`'s `DanglingEdgeError` recoded as `EdgesIncompleteError`.

    `rad_core.manifest._walk` raises `DanglingEdgeError` -- a `ManifestError`, a `ValueError`,
    disjoint from `ProjectError` -- when an artifact's `built_from` names an id no input or artifact
    carries (that module's own comment: *"a dangling edge is a corrupt manifest, not a valid
    artifact"*). That can only reach here on a manifest **loaded from disk**: every artifact this
    server itself seals passes `check_edges` first, which already refuses an unknown edge before the
    build even starts. A manifest hand-edited (or corrupted) between loads can still carry one, and
    until this wrapper existed `evaluate(project.manifest)` let it propagate raw out of `describe` and
    `update_project` -- an uncaught exception is `isError: true` with `structured_content: None` and
    no advisory, which check 0.4 forbids
    (docs/tool-surface.md#a-refusal-is-a-tool-execution-error-not-a-successful-result).

    Recoded as `EdgesIncompleteError` (`ARTIFACT_EDGES_INCOMPLETE`) rather than left uncoded or given
    a new code: that class's own docstring already names this exact event -- `built_from` "omits an
    edge this artifact's kind requires, **or names something unknown**" -- and `check_edges` raises
    the identical code for the same condition caught earlier, before a build. A dangling edge found
    on load and a dangling edge found before a build are the same fact about the manifest, discovered
    at two different times; giving the later discovery a second code would let two codes describe one
    condition, which is the drift ruling R6 exists to prevent. Caught **by type**, not by parsing the
    message, which is why `DanglingEdgeError` exists as its own `rad_core` class rather than being
    told apart here by string-matching `ManifestError`'s text -- a message edit must not silently stop
    this from firing. The cycle and duplicate-id branches of `ManifestError` are not translated: those
    are not `EdgesIncompleteError` by that class's own definition, so they are left to propagate from
    here. (Amended 2026-10-04, R790: this said the register has no code for them;
    `MANIFEST_UNREADABLE` covers a manifest "present but corrupt", and `results` refuses them with
    it.) A non-finite number in a manifest `_load_manifest` read never reaches here:
    `_load_manifest` refuses the document first (R737).

    `describe` and `update_project` in this module, `rad_mcp.export_tool._artifact_target` for
    `export`, and `rad_mcp.results_tool._refuse_stale_results` for `results` (R785), evaluate a
    manifest loaded from disk through this rather than the raw import, so the same manifest refuses
    the same way in each. The one difference: `_refuse_stale_results` refuses the cycle and
    duplicate-id `ManifestError` this leaves untranslated as `MANIFEST_UNREADABLE` (R790), where
    `_artifact_target` still lets it propagate. `list_projects` calls `evaluate` directly and
    reports a `ManifestError` as that project's `error` row instead. `is_reusable`'s own `evaluate`
    call and `register_artifact`'s seal-time digest walk (`resolve_built_from_digests`,
    `recompute_digests`) do not go through this either: both build or check a manifest under this
    server's own control in the same call, not one that arrived from disk since the last load, so a
    dangling edge there is this server's own bug and should surface as one.
    """
    try:
        return evaluate(manifest)
    except DanglingEdgeError as exc:
        raise EdgesIncompleteError(str(exc)) from exc


def describe(project: LoadedProject) -> Description:
    """Evaluate every artifact and say what is reusable, and why not where it is not."""
    statuses = evaluate_loaded(project.manifest)
    advisories = list(project.advisories)

    invalid = [s.id for s in statuses.values() if not s.valid]
    if invalid:
        advisories.append(
            _advisory(
                "ARTIFACTS_INVALID",
                "info",
                f"{len(invalid)} of {len(statuses)} artifact(s) are invalid and will be rebuilt: "
                + ", ".join(invalid),
            )
        )
    return Description(
        manifest=project.manifest,
        statuses=statuses,
        reuse=reuse_summary(statuses, project.manifest),
        advisories=tuple(advisories),
    )


@dataclass(frozen=True)
class UpdateResult:
    """What `project(action="update")` changed, and what it cost."""

    changed_fields: tuple[str, ...]
    newly_invalid: tuple[str, ...]
    """Artifacts that were valid before the update and are not after -- the price of the change,
    named. The two ground rows are the reason this is reported: setting `ground_reflectance`
    invalidates the sky matrices and leaves the daylight matrices alone, and a user who cannot see
    which is which cannot tell a cheap change from an expensive one."""
    description: Description


def update_project(
    project: LoadedProject,
    *,
    ground_reflectance: float | None | _Unset = _UNSET,
    ground_reflectance_provenance: Provenance = "assumed",
    ground_reflectance_source: str | None = None,
    description: str | None | _Unset = _UNSET,
    save: bool = True,
) -> UpdateResult:
    """Set the two mutable project fields and report what that invalidated.

    Ruling R2: the `project` tool gains `action="update"`, and this is the engine operation behind
    it. Only `ground_reflectance` and `description` are mutable -- `units` and `name` are not,
    because every digest in the project was taken under them.

    `ground_reflectance=None` is a distinct instruction from omitting it: `None` resets the field to
    the labelled `0.20` default, omitting it leaves the field alone. A value the user gives is
    `assumed` unless they state it was measured
    (docs/tool-surface.md#provenance-a-default-is-never-a-measurement).
    """
    # **Validated before `before` is even computed, and long before `_commit`.** The ordering is the
    # defect: this function committed the manifest and *then* called `describe`, so a `NaN` reached
    # disk as JSON `null` and the exception that followed left the project unreadable by every
    # later call, `delete` included. See `GroundReflectanceRangeError`.
    if not isinstance(ground_reflectance, _Unset):
        ground_reflectance = _validated_ground_reflectance(ground_reflectance)

    before = {artifact_id for artifact_id, s in evaluate_loaded(project.manifest).items() if s.valid}
    snapshot = project.manifest.model_copy(deep=True)
    changed: list[str] = []

    if not isinstance(ground_reflectance, _Unset):
        resolved: ProvenanceValue = resolve_ground_reflectance(
            ground_reflectance,
            provenance=ground_reflectance_provenance,
            source=ground_reflectance_source,
        )
        if resolved != project.manifest.project.ground_reflectance:
            project.manifest.project.ground_reflectance = resolved
            changed.append("ground_reflectance")

    if not isinstance(description, _Unset):
        if description != project.manifest.project.description:
            project.manifest.project.description = description
            changed.append("description")

    if changed and save:
        _commit(project, snapshot)

    described = describe(project)
    newly_invalid = tuple(
        sorted(
            artifact_id
            for artifact_id, status in described.statuses.items()
            if not status.valid and artifact_id in before
        )
    )
    return UpdateResult(
        changed_fields=tuple(changed), newly_invalid=newly_invalid, description=described
    )
