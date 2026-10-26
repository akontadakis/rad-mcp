"""The `export` tool -- docs/tool-surface.md#17-export, Phase 9 Task 13
(docs/plan.md, "Phase 9 — rendering, quality, export").

**`out=` is a caller-named write path, and that is the hazard this task exists to close.** Phase
8's whole-branch review found an arbitrary write outside every allowed root that no per-task gate
saw, and this branch hit the same shape again at Task 11 -- a symlink at the *final component* of a
caller-supplied `out` escaped a parent-chain-only check while a sibling write correctly refused it.
**`out` itself is resolved through `rad_mcp.paths.contain(candidate, roots)` before anything is
opened**, and `contain` is the stronger primitive on purpose: it calls `Path.resolve()` on the
*whole* candidate, so a symlink at the final component is followed and judged exactly like one three
levels up -- not merely checked against its parent's own resolved chain, which is what let Task 11's
escape through. `PathOutsideRoot`/`RootsNotConfigured` become a refusal
(`rad_mcp.runtime.refuse_engine_error`), never a traceback, and nothing is opened before that check
runs. See `tests/test_export_tool.py::TestOutIsContainedInAnAllowedRoot` for the proof, including the
directory-symlink shape this docstring names.

**Fix round 1, ruling R349 -- containing `out` is not the whole hazard.** `"artifacts"` and
`"manifest"` both mint a destination filename from a manifest-controlled string
(`Artifact.id`/`InputRecord.id`), which is free text this project's own registration machinery does
not constrain to a single path component end to end (`register_input`/`register_artifact` accept
whatever a caller's `name=`/`artifact_id=` argument supplies, and `rad_core.manifest`'s `InputRecord`
/`Artifact` carry no pattern or validator on `id`). A `contain()`-resolved `out_dir`
does not save a caller from an id **containing** its own traversal (`../../../outside_all_roots/
PWNED`): `out_dir / f"{record.id}_{name}"` is plain path-join arithmetic, and `Path.__truediv__`
honours a `..` or an embedded absolute path in either operand -- no symlink required, and `contain`
is never consulted on the result. **Every destination filename this module mints from a
manifest-controlled string is now sanitised (`_sanitize_component`, alnum plus `-_.` only, the same
posture `rad_engine.export._local_name` established in Task 12) and the resulting path is
independently re-contained (`_contained_dest`)** -- belt and braces, because sanitising alone would
leave the module's own stated posture resting on a regex with no second check behind it. **The same
fix round also contains every *source* read** (`Artifact.path`, `InputRecord.files[].path`) before
`open_regular_file` ever sees it: `open_regular_file`'s `O_NOFOLLOW`/`S_ISREG` says nothing about
*where* a path is, only what kind of thing sits at its final component, so a manifest naming a path
outside every root was read from there unchecked before this fix (a source that fails containment is
folded into the same `EXPORT_PARTIAL`-excluded bucket as a missing file, not a hard refusal --
nothing about the request is wrong, the referenced file just cannot be produced).

## R308 -- `out=` is a directory, not a file

Section 17 publishes `-> { out: str, files: [...] }`: a single `out` beside a plural `files` is a
directory and its contents. Three of this tool's four actions write more than one file into it
(`"scripts"` is the exception -- one script, `commands_for_job`'s whole ancestor chain bundled by
Task 12's `script_for_job`); `files` is the list of paths actually written inside `out`, and a
caller executes `result["files"][<the script>]`, never `result["out"]` directly (Task 14's own
contract). `out` defaults to `<project>/12_export/` when omitted -- a plain subdirectory, not a
layout directory `rad_engine.project.ProjectPaths` knows about, because registering one is a
`rad_engine.project` change and this task's file list does not include that module. It is still
resolved through `contain` exactly like a caller-supplied `out`, with no exemption for being the
tool's own default.

## `job_id` names a `rad_engine.jobs.JobRecord`, not an `Artifact` -- the translation this layer owns

`rad_engine.export.script_for_job`/`rad_core.report.assemble` both take a `Manifest` and an artifact
id (see each module's own "job_id names an artifact, not a JobRecord" section) -- a bare `Manifest`
has no other id to resolve. This tool's own `job_id` is the wire's real handle, a
`rad_engine.jobs.JobRecord` id, and `_job_target` is the translation: it reads `runtime.store.
status(job_id)` and takes the **last** artifact that job committed (`artifacts` on a succeeded job,
`partial_artifacts` on any other state -- `rad_engine.jobs.status_payload`'s own two names for the
one list a job actually sealed) as the target this call narrates. `job_id` is required for
`"scripts"`, `"report"` and `"artifacts"` -- each needs one chain to narrate -- and optional for
`"manifest"`, which exports the whole project's provenance when none is named.

**Ruling R351 -- "last committed" is a real, disclosed heuristic, not a specified rule, and the
result now names it.** Section 17 says nothing about how `job_id` maps to an artifact; `chain.py`
appends `CommittedArtifact` entries in step-completion order (`jobs.JobStore._commit`), and a
multi-branch run (an ASE-carrying or multi-group `run_annual`, whose own `chain.py` commits
`asemx:{grid}{scope}`, `direct_ill_id`, `blend_ill_id` and `ill:{grid}` from different branches of
one job) records nothing that marks any one of them "the result". `committed[-1]` is this module's
own choice among several equally defensible ones, and the choice is now visible rather than silent:
every non-refusal result carries `artifact_id`, the exact id this call narrated, so a caller can see
what was picked instead of trusting it blindly (`tests/test_export_tool.py::TestJobTarget`'s
two-artifact fixture pins the current choice from the outside).

**Ruling R350 -- a stale artifact reference is a refusal, not a `KeyError`.** `rad_core.report.
assemble` and `rad_engine.export.script_for_job`/`commands_for_job` each raise a bare `KeyError` when
`job_id` (at their layer, an artifact id) is not in the manifest they were handed -- both modules'
own docstrings say so. That happens here whenever the *job record*'s snapshot of what it committed
disagrees with the *manifest*'s current content: an out-of-band edit to `manifest.json`, a restored
backup, or a `project(action="update")`/manual edit that has since dropped the artifact. All three
call sites are wrapped and refused (`ARGUMENT_OUT_OF_RANGE`, `field="project"`) rather than left to
escape as an uncaught exception, which `rad_mcp.runtime.refuse_engine_error`'s own docstring names as
exactly the failure check 0.4 forbids. R746: when the missing id is not one the job record lists,
the reason names it as an input of the job's chain instead.

**Ruling R352 -- an assumption with no clause is surfaced loudly, not published silently.**
`rad_core.report.assemble`'s own `Assumption(clause=record.source or "")` can publish an empty
string when a `provenance="default"` input was registered with no `source` -- an assumption labelled
with nothing, which is the exact failure check 9.11 exists to prevent. That data gap is Task 12's;
this module's own coverage of it is not -- `EXPORT_CONTAINS_DEFAULTS`'s message now names every
assumption id whose `clause` is empty, so the gap is visible on the wire rather than published as an
ordinary, fully-labelled assumption.

**Ruling R729 (Phase 14 Branch D) -- `artifact_id` is the alternative to `job_id` for a call with no
`JobRecord` to translate.** Every action above required `job_id` because a bare `Manifest` has no
other id to resolve (this section's own opening paragraph) -- but a caller may already hold the
target artifact id directly, straight from a `run_*` tool's own payload (`run_point_in_time`'s,
since R730), and `_artifact_target` resolves that id with no call to `runtime.store` and no
`JobRecord` in the picture at all. `job_id` and `_job_target` are unchanged by this: `artifact_id` is
a second, parallel way to name the target `Artifact`, refused if it names nothing in the manifest or
if `rad_core.manifest.evaluate` reports it stale. Exactly one of `job_id`/`artifact_id` is required
for `"scripts"`, `"report"` and `"artifacts"`; both are optional for `"manifest"`; both together are
refused for every action.

## Ruling R345 -- superseded by R348 (Task 14): the manifest-registration half is now closed

Task 14's reproduction check names `run_point_in_time` as "the Phase 3 job" it will replay. This
ruling originally declined to widen `run_point_in_time` to register the octree it builds, on the
ground that doing so was a different tool's published-surface change, out of this task's file list,
and reviewable on its own. **R348 (also Task 14) overturned that decision and did the widening**:
`src/rad_mcp/point_in_time_tool.py`'s `_dispatch` now calls `_register_point_in_time_octree`
(renamed `_register_point_in_time_artifacts` by R730, 2026-09-27, when it began registering the sky
and the result too) after every real (non-`dry_run`) run, sealing the octree `_run_pipeline` already
builds under the stable id
`octree:point_in_time`, matching `annual_tool._register_octree`'s shape -- `parameters` carries
`freeze: True` plus a `sky_digest` hash (the sky is compiled *into* this octree and is not itself a
registered input, so its content has to move the artifact's digest some other way, or a sky change
would silently read as "nothing changed" to `rad_core.manifest.evaluate`). **`built_from` names the
project's registered materials inputs, then its geometry inputs (ruling R356, fix round 1)** -- not
geometry alone, corrected from this paragraph's own first version, which said "geometry inputs" and
went stale the moment R356 landed in the same fix round without this sentence being re-read against
it. `tests/test_point_in_time_tool.py::TestPointInTimeOctreeRegistration` proves `evaluate`/
`_changed_edges`/`_own_build_changed` read the new artifact correctly: a fresh registration is
valid; two different skies over identical geometry disagree on digest; an edited geometry input
invalidates it with `invalidated_by == ["geometry_office"]`; a registered materials input appears in
`built_from` before geometry (`test_built_from_names_materials_before_geometry`); and an edited
materials input invalidates it with `invalidated_by == ["materials_office"]`
(`test_a_changed_materials_input_invalidates_the_registered_octree`).

**What this does NOT close, measured directly and still true after R348:** `run_point_in_time` never
calls `runtime.store.submit()` -- every call runs inline (see this module's own "KNOWN LIMITATION"
docstring, which names the reason: `rtrace`'s sensor points reach it only on stdin, and the job
worker never wires a step's stdin, "a gap in `rad_engine.jobs`, outside this tool's ownership"). Its
`job_id` is a synthesised `f"inline-{...}"` string that `runtime.store` has never heard of. `_job_
target` below calls `runtime.store.status(job_id)` *before* it ever reads a manifest artifact, so
`export(action="scripts", job_id=<a run_point_in_time job_id>, ...)` is refused `JOB_NOT_FOUND`
regardless of R348's registration -- measured directly, both before and after this ruling landed,
with `runtime.store.status()` raising `JobNotFoundError` either way. Closing *that* gap means giving
`run_point_in_time` a real `JobRecord`, which is the stdin-wiring gap this tool's own docstring
already disclaims owning -- a materially bigger change than "register the octree", and Task 14's own
report is where this is recorded as the finding it is, per the owner's explicit authorisation that
this check may legitimately fail rather than be forced green.

**2026-09-27, ruling R729 -- closed from a different angle than this paragraph anticipated.** The
gap above is still exactly what it was: `job_id=<a run_point_in_time job_id>` is refused
`JOB_NOT_FOUND` today precisely as measured, and closing *that* route still means giving
`run_point_in_time` a real `JobRecord`. What changed is that `export` no longer needs that route to
reach a point-in-time result at all -- `run_point_in_time`'s payload carries its own `artifact_id`
(R730), and a caller passes that to `export(artifact_id=...)` instead of `job_id`, which
`_artifact_target` resolves with no call to `runtime.store` and so never asks whether a `JobRecord`
exists. `artifact_id` is the route around the job-store gap, not through it.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any, Literal

from mcp_types import CallToolResult
from pydantic import BaseModel, ConfigDict, Field, RootModel

from rad_core.manifest import Artifact, ArtifactStatus, InputRecord, ManifestError
from rad_core.report import Report, assemble
from rad_engine import evaluate_loaded
from rad_engine.export import (
    BatPathUnquotableError,
    UnsupportedArtifactKindError,
    exported_octree_differences,
    script_for_job,
)
from rad_engine.jobs import JobError
from rad_engine.project import MANIFEST_LOCK_NAME, MANIFEST_NAME, LoadedProject, ProjectError
from rad_engine.safe_io import (
    NotARegularFileError,
    open_regular_file,
    refuse_existing_non_regular_destination,
    refuse_linked_components,
    replace_file_atomically,
)
from rad_mcp.envelope import Advisory, RadResult, Refusal, emit, one_of_schema, refuse, refuse_missing
from rad_mcp.paths import (
    CodedWriteRefusal,
    PathOutsideRoot,
    RootsNotConfigured,
    contain,
    contain_as_named,
    expand_user,
    write_failure,
)
from rad_mcp.runtime import Runtime, current, refuse_engine_error

logger = logging.getLogger(__name__)

__all__ = ["ExportResult", "export", "stale_detail"]

Action = Literal["scripts", "report", "artifacts", "manifest"]
Shell = Literal["sh", "bat"]

# R804 (item 36): every action writes into a caller-named `out` through `open_regular_file(path,
# "wb")` (`O_TRUNC`), so each replaces a regular file of the same name already there, whoever wrote
# it -- the first half of R804's boundary, the half `image`'s writes meet (R802).
DESTRUCTIVE_ACTIONS: tuple[str, ...] = ("scripts", "report", "artifacts", "manifest")

#: The subdirectory `out` defaults to when the caller names none. A plain directory name, not a
#: `rad_engine.project.ProjectPaths` layout entry -- see the module docstring's R308 section.
_DEFAULT_EXPORT_DIRNAME = "12_export"


class _JobBelongsToAnotherProject(ValueError):
    """`job_id` resolves to a real job, but not one this `project` argument names."""


class _NoCommittedArtifact(ValueError):
    """The named job has not committed any artifact yet -- nothing to narrate."""


class _ArtifactNotInManifest(ValueError):
    """`artifact_id` (ruling R729) names no artifact in this project's manifest.

    The alternative to `job_id` for a caller that already holds a manifest artifact id directly --
    `run_point_in_time`'s own payload, since R730 -- and so has nothing for `_job_target` to
    translate at all.
    """


class _ArtifactStale(ValueError):
    """`artifact_id` names a real artifact, but `rad_core.manifest.evaluate`'s status for it is not
    `valid` -- exporting it would regenerate a script, report or copy that silently disagrees with
    the project's current registered inputs (M1, measured before this ruling: no existing route
    refused this case for an `artifact_id`)."""


class _ExportFilenameCollision(ValueError):
    """Two input files would be copied to one destination filename inside `out`.

    **Whole-branch fix wave, B3.** `_export_manifest` mints each copy's name as
    `f"{_sanitize_component(record.id)}_{_sanitize_component(src.name)}"`, which is **not
    injective**: `_` is legal inside a `rad_mcp.names.NAME_PATTERN` input id *and* inside a
    filename, so the join has no unambiguous separator. Measured against the real function with
    real files, before this guard:

        geo_a -> <root>/wall.rad          (both ids legal under NAME_PATTERN)
        geo   -> <root>/sub/a_wall.rad
        files the tool REPORTED writing:  .../12_export/geo_a_wall.rad, twice, the same path
        manifest.json listed BOTH inputs; advisories: None
        on disk: geo_a_wall.rad = "### INPUT TWO: a completely different wall"

    A silent substitution reported as success, in an **audit artefact** -- this project's own
    worst-outcome class, and exactly the shape `rad_mcp.window_group_tool._refuse_filename_collision`
    already exists to refuse for `tmx` seals ("`NAME_PATTERN` permits `_` in both a group name and a
    state name... the second would overwrite the first silently"). Phase 9 reintroduced the shape in
    a new tool with no guard.

    Refused rather than disambiguated: renaming one copy would make the export's own filenames
    disagree with the ids `manifest.json` records beside them, and an audit bundle whose file names
    do not match its manifest is a different silent wrongness. The caller renames an input.

    Shaped by `export`'s own handler chain into `ARGUMENT_OUT_OF_RANGE` with `field="project"` --
    computed from project state, not from an argument refused on its face (R14).
    """


# --- nested wire shapes ------------------------------------------------------------------------


class _Nested(BaseModel):
    model_config = ConfigDict(extra="forbid")


# R810 moved check 9.11 (every default labelled with its citation) here. `clause` is
# `rad_core.report.assemble`'s `record.source or ""`; R352 names the empty ones in
# `EXPORT_CONTAINS_DEFAULTS` (`_export_report`).
class AssumptionPayload(_Nested):
    """One input or project value the narrated build chain rests on whose provenance is
    `"default"`: an assumed value, not a measured one.

    `clause` is the source recorded with it, the citation the assumption rests on: for the ground
    reflectance, `gensky -defaults`, the source of the 0.20 a project gets when `project` is never
    given a `ground_reflectance`. `""` when none was recorded, and `EXPORT_CONTAINS_DEFAULTS` then
    names its `id`. `value` and `note` are `null` for a file input; for the project's ground
    reflectance (`project:ground_reflectance`, listed when the chain was built from it) `value` is
    the reflectance and `note` its recorded note, if any.
    """

    id: str
    clause: str
    value: float | None = None
    note: str | None = None


class InputProvenancePayload(_Nested):
    id: str
    kind: str
    provenance: str | None = None
    source: str | None = None
    units: str | None = None


class MatrixAgePayload(_Nested):
    id: str
    kind: str
    built_at: datetime | None = None
    wall_time_s: float | None = None
    radiance_version: str


# `rad_core.report.Report.advisories`: the `warnings` of every artifact in the chain, ancestors
# first. R810 corrected the old "raised during the run this report narrates": an ancestor matrix a
# later run reused carries the warnings of the run that built it.
class ReportAdvisoryPayload(_Nested):
    """One advisory recorded with an artifact of the narrated build chain, the target included,
    when that artifact was built, which may have been an earlier run than the target's.

    Not the result's top-level `advisories`, which are about the export itself.
    """

    code: str
    severity: str
    message: str


class ReportPayload(_Nested):
    """`standard_edition` names the LM-83 edition(s) this chain's LM-83 inputs recorded, IES
    LM-83-12 when none recorded one, and BS EN 17037:2018, this server's fixed EN 17037 edition.
    Each LM-83 judgement from `results` names its own `lm83_edition`."""

    assumptions: list[AssumptionPayload]
    inputs: list[InputProvenancePayload]
    method: str | None
    matrices: list[MatrixAgePayload]
    advisories: list[ReportAdvisoryPayload]
    radiance_version: str
    standard_edition: str


# --- result branches ----------------------------------------------------------------------------


class ExportBase(RadResult):
    action: Action
    out: str
    files: list[str]
    artifact_id: str | None = None
    """Which artifact this call narrated -- `None` only for `action="manifest"` with no `job_id`.
    Published so `_job_target`'s "last committed artifact" choice (ruling R351) is visible to the
    caller rather than picked silently: a multi-branch job (an ASE-carrying or multi-group
    `run_annual`) can commit several artifacts with nothing else marking which one is "the result"."""


class ExportScripts(ExportBase):
    action: Literal["scripts"] = "scripts"


class ExportReport(ExportBase):
    action: Literal["report"] = "report"
    report: ReportPayload


class ExportArtifacts(ExportBase):
    action: Literal["artifacts"] = "artifacts"


class ExportManifest(ExportBase):
    action: Literal["manifest"] = "manifest"


ExportPayload = ExportScripts | ExportReport | ExportArtifacts | ExportManifest | Refusal


# `"scripts"`, `"artifacts"` and `"manifest"` share one shape (`{action, out, files,
# advisories?}`) and are told apart in the schema only by their own `action` literal -- without it
# they would be indistinguishable branches and `oneOf` would never hold (`envelope.one_of_schema`'s
# "exactly one" requirement). R810 lifted `artifact_id`'s meaning from `ExportBase.artifact_id`'s
# field text (R351's "last committed" choice) onto the wire. Fix round 1 trimmed the per-action
# file names and the `EXPORT_PARTIAL` sentence (R42 "no": `files` and the advisory speak for
# themselves) and narrowed the multi-artifact case to several branch results.
class ExportResult(RootModel[ExportPayload]):
    """What `export` returns: the files one action wrote, or a refusal.

    `out` is the directory written into and `files` the absolute path of every file written there.
    `"artifacts"` copies each artifact file in the target's build chain, the target included.
    `"manifest"` writes `manifest.json` (the project record and its registered inputs, or only
    those of the target's chain, and no artifacts) plus a copy of each of those inputs' files.

    `artifact_id` is the artifact narrated. Given a `job_id`, it is the last artifact that job
    committed. A job whose branches committed several results (an annual run with ASE, or with
    several window groups) marks none of them as the result, and the last one's build chain need
    not contain the others: check it, and pass `artifact_id` instead to choose. `null` only for
    `"manifest"` called with neither `job_id` nor `artifact_id`, which covers the whole project.
    """

    @classmethod
    def model_json_schema(cls, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return one_of_schema(super().model_json_schema(*args, **kwargs), ExportBase)


# --- small helpers -------------------------------------------------------------------------------


def _write_bytes(path: Path, data: bytes) -> None:
    """Every write this tool makes. `path`'s directory is `out`, already `contain`-resolved before
    this is ever called.

    *(Amended 2026-10-05, R851, registers N-112 and N-114.)* The name is `lstat`-checked
    (`refuse_existing_non_regular_destination`: anything not a regular file there is refused,
    e.g. a symlink, FIFO, device or directory), and the bytes are staged beside it and renamed over it (`replace_file_atomically`,
    R526's pattern) instead of opened `"wb"`, so a hardlink at the name is replaced, not written
    through: the `"wb"` open rewrote a hardlinked file outside every root. This said R48's
    `O_NOFOLLOW` and `S_ISREG` were the guard; the staging open is still `open_regular_file`'s
    `"xb"`.

    *(Amended 2026-10-05, R855.)* A write the disk or the permissions refuse is `PROJECT_WRITE_FAILED`
    with `field: "out"`, naming a read-only destination as the cause (`write_failure`); until then
    it was a reason with no code."""
    refuse_existing_non_regular_destination(path)
    try:
        replace_file_atomically(path, lambda handle: handle.write(data))
    except NotARegularFileError:
        raise
    except OSError as exc:
        raise write_failure(path, exc, what="the export file", field="out", outcome=(
            "A file already there is unchanged.")) from None


def _same_file(path: Path, identity: os.stat_result) -> bool:
    """Whether ``path`` names the file ``identity`` was taken from (`st_dev`, `st_ino`), following
    links; a path that does not exist, or cannot be `stat`-ed, is not it."""
    try:
        found = os.stat(path)
    except OSError:
        return False
    return (found.st_dev, found.st_ino) == (identity.st_dev, identity.st_ino)


def _refuse_linked_out(candidate: Path, *, project_root: Path, resolved: Path) -> None:
    """Refuse (`NotARegularFileError`) an `out` that resolves inside the project through a symbolic
    link below the project root (register N-113, ruling R851, fix rounds 3 and 4).

    **Decided on the resolved path, walked on the named one.** The fix-round-2 guard asked whether
    ``candidate`` was *lexically* under the project root, which is held resolved; an `out` spelled
    through a linked allowed root (`home -> real_home`, as `/tmp` and `/var` are on macOS) is not,
    so the walk was skipped and `contain` followed a planted `12_export` link (measured: the
    victim overwritten with the manifest JSON). Now "inside the project" is ``resolved``'s answer,
    and the walk starts below the highest named ancestor of ``candidate`` that is the project
    root -- the root itself, or any spelling of it through a link above it, which is the caller's
    and is not refused. An `out` inside the project with no such ancestor reached it through a
    link, and is refused too. ``candidate`` holds no `..` (refused before this).

    **The project root is found by file identity, not by spelling** (fix round 4). Fix round 3
    compared ``resolved``'s ancestors and ``candidate``'s with `Path` equality, which is
    case-sensitive; on a case-insensitive volume (APFS's default) `Path.resolve()` keeps the
    caller's case on a component that is not a link, so `out=<root>/EX2/12_export` for the project
    `ex2`, with `12_export` a *relative* link to `elsewhere`, resolved to `.../EX2/elsewhere`, was
    not judged inside `.../ex2`, and the victim's `manifest.json` was overwritten (measured). Each
    ancestor is now `stat`-ed and compared with the project root's `st_dev`/`st_ino`. And
    ``candidate`` is walked with `~` expanded, as `contain` expands it: walked unexpanded, a
    `~/...` `out` had no ancestor that was the project root and was refused as reaching it through
    a link (measured)."""
    root = project_root.resolve()
    named = expand_user(candidate)
    identity = _project_root_identity(root)
    if not _resolves_inside(resolved, identity):
        return
    for ancestor in [*reversed(named.parents), named]:
        if _same_file(ancestor, identity):
            refuse_linked_components(named, below=ancestor)
            return
    raise NotARegularFileError(
        f"{str(candidate)!r} reaches the project at {str(root)!r} through a symbolic link, so this "
        "server will not write through it. Nothing has been written. Name the export directory "
        "without the link and re-run."
    )


def _project_root_identity(root: Path) -> os.stat_result:
    """`os.stat` of the project root, or `PathOutsideRoot` when it cannot be taken: whether `out` is
    inside the project is then unknown, and a guard that returned would let the write through
    unchecked (final review of 2026-10-05: this failed open)."""
    try:
        return os.stat(root)
    except OSError as exc:
        raise PathOutsideRoot(
            f"the project root {str(root)!r} could not be examined ({exc.strerror or exc}), so whether "
            "`out` lies inside it cannot be told. Nothing has been written."
        ) from None


def _resolves_inside(resolved: Path, identity: os.stat_result) -> bool:
    """Whether `resolved` or one of its parents is the project root, by file identity."""
    return any(_same_file(ancestor, identity) for ancestor in [resolved, *resolved.parents])


def _refuse_project_root_out(call: str, out_dir: Path) -> CallToolResult | None:
    """R805 (item 36): a project root holds both `manifest.json` and `.manifest.lock`
    (`create_project` writes both; this tool writes `manifest.json` and never `.manifest.lock`).
    `export(action="manifest")` into one replaced that project's live manifest with a copy that
    carries no `artifacts`, so every action refuses it, before anything is written."""
    if not (os.path.lexists(out_dir / MANIFEST_NAME) and os.path.lexists(out_dir / MANIFEST_LOCK_NAME)):
        return None
    reason = (
        f"{call}: out={str(out_dir)!r} is a project root: it holds {MANIFEST_NAME} and "
        f"{MANIFEST_LOCK_NAME}. export refuses a project root as out for every action, because "
        f"action='manifest' would replace that project's {MANIFEST_NAME}. Name a "
        "directory that is not a project root, or omit out to write into <project>/12_export/."
    )
    return refuse(reason, [Advisory(
        code="ARGUMENT_OUT_OF_RANGE", severity="error", field="out", message=reason,
    )])


def _sanitize_component(value: str) -> str:
    """A filesystem-safe local name -- alnum plus `-_.` only, everything else (including `/`)
    replaced -- the same sanitisation `rad_engine.export._local_name` established in Task 12.

    Ruling R349: `Artifact.id`/`InputRecord.id` are free text this project's registration
    machinery does not constrain to one path component end to end, and treating either as a
    trusted filename component let an id containing `../../../outside_all_roots/PWNED` escape a
    fully `contain()`-resolved `out_dir` through plain `Path.__truediv__` arithmetic -- no symlink
    involved, no `contain` call anywhere near the write. Sanitising removes every character that
    could form a traversal or an absolute path; `_contained_dest` below is the second, independent
    check on the result, because sanitising alone would leave this module's stated posture resting
    on an un-verified regex.

    `.` is a kept character (a `.oct`/`.rad` suffix is meaningful), which means a bare `value` of
    `"."` or `".."` would otherwise sanitise to itself -- still no `/`, so it cannot cross into a
    *different* directory, but `out_dir / ".."` is a valid path one level above `out_dir`, which is
    a real escape from the directory this call is scoped to write into even though every call site
    appends a non-empty suffix or prefix today. Guarded here rather than left to depend on every
    future call site continuing to do that.
    """
    raw = "".join(c if c.isalnum() or c in "-_." else "_" for c in value) or "_"
    return "_" if raw in (".", "..") else raw


def _refuse_colliding_copies(
    candidates: list[tuple[str, str]], *, what: str, why: str
) -> None:
    """Raise `_ExportFilenameCollision` if two of `candidates` claim one destination filename.

    `candidates` is `(destination filename, a human description of what claims it)`, in the order
    the export would write them, so the message can name the pair. Shared by `"manifest"` and
    `"artifacts"` because both mint a destination from an id that is not constrained to be
    injective under `_sanitize_component`; `what`/`why` carry the part that genuinely differs.

    Checked over the whole set **before** the first copy, so a refusal never leaves a bundle that
    is half an audit trail.
    """
    claimed: dict[str, str] = {}
    for name, owner in candidates:
        if (previous := claimed.get(name)) is not None:
            raise _ExportFilenameCollision(
                f"two {what} would both be copied to {name!r} inside out: {previous} and {owner}. "
                f"{why} Left unchecked, the second copy would silently replace the first while "
                "manifest.json still listed both, and the export would report success."
            )
        claimed[name] = owner


def _copy_name(record_id: str, source_name: str) -> str:
    """The filename `_export_manifest` gives one input file's copy inside `out`.

    **One function, so the collision guard and the write cannot drift** (B3). The guard has to
    compute exactly the name the write will use; two copies of the same f-string is how a guard
    silently stops guarding, and this project has the precedent (`_refuse_filename_collision` was
    written after a `tmx` seal did the same thing).

    Not injective, and cannot be made so without changing the published bundle layout: `_` is
    legal in a `rad_mcp.names.NAME_PATTERN` id and in a filename, so `("geo_a", "wall.rad")` and
    `("geo", "a_wall.rad")` both give `"geo_a_wall.rad"`. `_export_manifest` refuses the collision
    rather than renaming around it -- see `_ExportFilenameCollision`.
    """
    return f"{_sanitize_component(record_id)}_{_sanitize_component(source_name)}"


def _safe_artifact_filename(artifact: Artifact) -> str:
    """A script-relative-safe filename for a copy of `artifact`'s own file."""
    safe = _sanitize_component(artifact.id)
    suffix = Path(artifact.path).suffix
    return safe + suffix if suffix else safe


def _contained_dest(out_dir: Path, name: str, roots: tuple[Path, ...]) -> Path:
    """`out_dir / name`, re-`contain`-ed -- the belt beside `_sanitize_component`'s braces (ruling
    R349). `name` is already sanitised by the time this is called, so a traversal or absolute-path
    *character sequence* never reaches this join -- but sanitising cannot see a symlink already
    sitting on disk at the sanitised name: an attacker who plants one there before this call gets a
    perfectly ordinary-looking destination that `Path.resolve()` still follows to wherever the link
    points. Fix round 2 reproduced exactly that shape and confirmed this is where it is caught: this
    call refuses it, in practice, before `_write_bytes`/`open_regular_file` ever opens anything at
    `name`.

    **Returned as named, not resolved** (register N-112, ruling R851; `rad_mcp.paths.contain_as_named`).
    The paragraph above holds for a link aimed *outside* every root. One aimed at another file
    *inside* a root passed `contain`, whose resolved return then handed `_write_bytes` the target, so
    that file was overwritten. As named, `_write_bytes`' `lstat` check
    (`refuse_existing_non_regular_destination`) refuses the link. *(Corrected 2026-10-05, R851 fix
    round 3: this said "As named, `open_regular_file`'s `O_NOFOLLOW` refuses the link"; since fix
    round 2 `_write_bytes` stages and renames, and the `lstat` is what refuses it.)*"""
    return contain_as_named(out_dir / name, roots)


def _job_target(runtime: Runtime, loaded: LoadedProject, job_id: str) -> tuple[str, str, frozenset[str]]:
    """`(artifact_id, state, recorded)` for `job_id` -- the last artifact it committed, its own state,
    and the ids of every artifact the job record lists, which `export`'s `KeyError` handler compares
    a missing id with (R746).

    Raises `JobError` exactly as `runtime.store.status` does for an unknown or expired id;
    `_JobBelongsToAnotherProject` if `job_id` names a job under a different project root than
    `loaded`'s, whatever the two projects' names;
    `_NoCommittedArtifact` if the job has sealed nothing yet -- a real `JobRecord` (`run_annual` --
    the only tool that submits one; `run_render` runs inline, measured 2026-09-13: no `store.submit(`
    in `render_tool.py`) that is still `queued`/`running` with no step committed. **Not**
    `run_point_in_time`: that tool's `job_id` is never in `runtime.store` at all (no `submit()` call
    anywhere in `point_in_time_tool.py` -- see the module docstring's R345 section), so a
    `run_point_in_time` job_id raises the `JobError` above, one line into this function, and never
    reaches this branch.
    """
    status = runtime.store.status(job_id)
    # By root, not by name (register N-38): two allowed roots may each hold a project of the same
    # name, and the job record's `project_root` is the resolved root it was submitted against (and
    # the resolved root it was recovered from, `JobStore.recover`), so it is the comparison that
    # tells them apart. The message keeps the names a host knows the projects by.
    if status["project_root"] != str(loaded.paths.root.resolve()):
        raise _JobBelongsToAnotherProject(
            f"job_id={job_id!r} belongs to project {status['project']!r} at "
            f"{status['project_root']}, not to {loaded.paths.name!r} at {loaded.paths.root.resolve()}. "
            "Pass the job_id a run on this project returned."
        )
    state = status["state"]
    committed = status.get("artifacts") or status.get("partial_artifacts") or []
    if not committed:
        raise _NoCommittedArtifact(
            f"job_id={job_id!r} (state={state!r}) has not committed any artifact yet -- nothing "
            "for this export to narrate."
        )
    return committed[-1]["id"], state, frozenset(entry["id"] for entry in committed)


def _artifact_target(loaded: LoadedProject, artifact_id: str) -> str:
    """`artifact_id`, checked and returned unchanged -- the parallel resolution `_job_target` above
    has no way to reach: `run_point_in_time` never calls `runtime.store.submit()`, so its result
    has no `JobRecord` at all, only the manifest artifact R730 registers (ruling R729).

    Raises `_ArtifactNotInManifest` if `loaded.manifest.artifact(artifact_id)` finds nothing;
    `_ArtifactStale` if `rad_core.manifest.evaluate`'s status for it is not `valid` -- named after
    M1's own measurement that no existing route refused this case: `rad_engine.export.
    script_for_job`/`rad_core.report.assemble` reconstruct a stale artifact's chain exactly as
    readily as a fresh one's digest alone says nothing about whether it is current.

    Evaluated through `rad_engine.evaluate_loaded`, not the raw `rad_core.manifest.
    evaluate`: `evaluate` resolves **every** artifact in the manifest, so one hand-edited record
    anywhere naming an unknown id raised `DanglingEdgeError` -- a `ManifestError`, which `export`
    did not catch at the time -- while this call exported a different, healthy artifact (an uncoded
    error, check 0.4; final review, Ruling 13). The wrapper recodes it as `EdgesIncompleteError`
    (`ARTIFACT_EDGES_INCOMPLETE`), a `ProjectError` `export`'s handler already shapes, so the same
    manifest refuses here exactly as `describe`/`update_project` refuse it -- before `out` exists.
    A `built_from` cycle or a repeated id, which `evaluate_loaded` leaves a bare `ManifestError`
    (R790), is refused `MANIFEST_UNREADABLE` by `export`'s `ManifestError` clause, also before `out`
    exists. *(Corrected 2026-10-12, R868: this said "a `ManifestError`, none of `export`'s caught
    types"; `export` catches `ManifestError` now.)*
    """
    try:
        loaded.manifest.artifact(artifact_id)
    except KeyError:
        raise _ArtifactNotInManifest(
            f"artifact_id={artifact_id!r} is not in this project's manifest. "
            'project(action="describe") lists the artifacts it holds.'
        ) from None

    status = evaluate_loaded(loaded.manifest)[artifact_id]
    if not status.valid:
        raise _ArtifactStale(
            f"artifact_id={artifact_id!r} is stale ({stale_detail(status)}). Re-run the tool that "
            "built it, then export the fresh artifact it registers."
        )
    return artifact_id


def stale_detail(status: ArtifactStatus) -> str:
    """What made an invalid artifact stale, in the words `export` refuses it with (R729) and
    `results` refuses a stored result with (R785): the edges that moved (`invalidated_by`), its own
    parameters, or the kind rule's `stale_reason` (R777). `rad_core.manifest.ArtifactStatus` says
    an invalid artifact always carries one of the three; the last clause covers a status that does
    not."""
    clauses = []
    if status.invalidated_by:
        clauses.append(f"invalidated_by={status.invalidated_by}")
    if status.parameters_changed:
        clauses.append("its own parameters changed since it was built")
    if status.stale_reason:
        clauses.append(status.stale_reason)
    return "; ".join(clauses) if clauses else "its recomputed digest no longer matches"


def _version_advisory(loaded: LoadedProject, target_id: str, installed: str | None) -> Advisory | None:
    """R738: `RADIANCE_VERSION_CHANGED` (warn) when an artifact in `target_id`'s own chain was
    built by a Radiance other than the installed one. A version change flags and never invalidates
    (`rad_core.manifest.ArtifactStatus`), so nothing is refused and nothing is rebuilt; the report
    names each artifact's own version. `None` when the installed version is unknown, since there is
    then nothing to compare against."""
    if installed is None:
        return None
    by_id = {artifact.id: artifact for artifact in loaded.manifest.artifacts}
    chain: list[Artifact] = []
    seen: set[str] = set()

    def visit(node_id: str) -> None:
        # Artifacts only, ancestors first (`rad_core.report.assemble`'s order). An input id, a
        # `project:*` scalar or a dangling edge carries no build version and is skipped, so this
        # warning never refuses a call (R738): each action body reads what it needs and refuses on
        # its own when that is missing.
        artifact = by_id.get(node_id)
        if artifact is None or node_id in seen:
            return
        seen.add(node_id)
        for edge in artifact.built_from:
            visit(edge)
        chain.append(artifact)

    visit(target_id)
    others = [artifact for artifact in chain if artifact.radiance_version != installed]
    if not others:
        return None
    built = ", ".join(f"{a.id} ({a.radiance_version})" for a in others)
    return Advisory(
        code="RADIANCE_VERSION_CHANGED", severity="warn",
        message=(
            f"Radiance is now {installed!r}. {len(others)} artifact(s) in this export's chain were "
            f"built by another version: {built}. The export describes them as they were built. "
            "A version change flags an artifact and does not invalidate it: run_point_in_time "
            'rebuilds its artifacts on every run and window_group(action="assign_bsdf") '
            "re-registers a tmx matrix on every call, but run_annual reuses a valid matrix "
            "unless its rebuild argument names it."
        ),
    )


def _with(result: ExportBase, advisory: Advisory | None) -> ExportBase:
    if advisory is None:
        return result
    return result.model_copy(update={"advisories": [*(result.advisories or []), advisory]})


# --- action bodies -------------------------------------------------------------------------------


def _report_payload(report: Report) -> ReportPayload:
    return ReportPayload(
        assumptions=[
            AssumptionPayload(id=a.id, clause=a.clause, value=a.value, note=a.note)
            for a in report.assumptions
        ],
        inputs=[
            InputProvenancePayload(
                id=i.id, kind=i.kind, provenance=i.provenance, source=i.source, units=i.units,
            )
            for i in report.inputs
        ],
        method=report.method,
        matrices=[
            MatrixAgePayload(
                id=m.id, kind=m.kind, built_at=m.built_at, wall_time_s=m.wall_time_s,
                radiance_version=m.radiance_version,
            )
            for m in report.matrices
        ],
        advisories=[
            ReportAdvisoryPayload(code=w.code, severity=w.severity, message=w.message)
            for w in report.advisories
        ],
        radiance_version=report.radiance_version,
        standard_edition=report.standard_edition,
    )


def _report_markdown(report: Report, payload: ReportPayload) -> str:
    lines = [
        "# Export report", "",
        f"Radiance version: {report.radiance_version}",
        # W2 (final review, controller ruling, round 2): what each half of the value is, true of every
        # chain -- one with no LM-83 ancestor, or none that recorded an edition, included; nothing
        # records the EN 17037 edition, and a judgement's LM-83 edition is per `results` call.
        f"Standard edition: {report.standard_edition}. The LM-83 edition is what this chain's "
        "LM-83 inputs recorded (IES LM-83-12 when none recorded one); BS EN 17037:2018 is this "
        "server's fixed EN 17037 edition; each LM-83 judgement from results names its own "
        "lm83_edition.",
        f"Method: {report.method or '(not recorded for this artifact kind)'}",
        "", "## Assumptions",
    ]
    if payload.assumptions:
        for a in payload.assumptions:
            value = f" = {a.value}" if a.value is not None else ""
            lines.append(f"- {a.id}{value} ({a.clause})")
    else:
        lines.append("(none -- every input this result depends on carries measured provenance)")
    lines += ["", "## Inputs"]
    for i in payload.inputs:
        lines.append(f"- {i.id} ({i.kind}, {i.provenance or 'unknown'} provenance)")
    lines += ["", "## Matrices"]
    for m in payload.matrices:
        lines.append(f"- {m.id} ({m.kind}), built {m.built_at or 'unknown time'} by {m.radiance_version}")
    lines += ["", "## Advisories raised during the run"]
    if payload.advisories:
        for w in payload.advisories:
            lines.append(f"- [{w.severity}] {w.code}: {w.message}")
    else:
        lines.append("(none)")
    return "\n".join(lines) + "\n"


def _export_report(loaded: LoadedProject, target_id: str, out_dir: Path) -> ExportReport:
    report = assemble(loaded.manifest, target_id)
    payload = _report_payload(report)
    files: list[str] = []

    report_json = out_dir / "report.json"
    _write_bytes(report_json, (json.dumps(payload.model_dump(mode="json"), indent=2) + "\n").encode())
    files.append(str(report_json))

    report_md = out_dir / "report.md"
    _write_bytes(report_md, _report_markdown(report, payload).encode())
    files.append(str(report_md))

    advisories = None
    if payload.assumptions:
        message = (
            f"{len(payload.assumptions)} default-provenance value(s) are recorded in this "
            "report's Assumptions section -- verify each is the right assumption for this "
            "project rather than reading it as a measured input."
        )
        # Ruling R352: `rad_core.report.assemble` can publish `clause=""` when the input's own
        # `source` was never set -- an assumption labelled with nothing, the exact failure check
        # 9.11 exists to prevent. Named here rather than left to pass silently as an ordinary,
        # fully-labelled assumption; the underlying gap is `rad_core.report`'s (Task 12's), not
        # fixed here, but not hidden here either.
        unlabelled = [a.id for a in payload.assumptions if not a.clause]
        if unlabelled:
            message += (
                f" {len(unlabelled)} of these carry NO clause citation at all ({unlabelled}) -- "
                "an unlabelled default, which check 9.11 exists to prevent publishing silently."
            )
        advisories = [Advisory(code="EXPORT_CONTAINS_DEFAULTS", severity="warn", message=message)]
    return ExportReport(
        out=str(out_dir), files=files, report=payload, artifact_id=target_id, advisories=advisories,
    )


def _export_scripts(loaded: LoadedProject, target_id: str, shell: Shell, out_dir: Path) -> ExportScripts:
    text = script_for_job(loaded.manifest, target_id, shell=shell, project_root=loaded.paths.root)
    script_path = out_dir / f"export.{shell}"
    _write_bytes(script_path, text.encode())
    advisories = [Advisory(
        code="EXPORT_PATHS_ABSOLUTE", severity="info",
        message=(
            "This script changes into the project folder (its first argument, or the folder it "
            "was exported from) and reads every input inside that folder by a project-relative "
            "path (R731). A registered input outside the project folder -- the office fixture's "
            "own geometry, registered in place, is the measured example -- is read by its absolute "
            "path on this machine instead, and the script will not run on a different machine or "
            "directory layout without editing that path."
        ),
    )]
    differs = _octree_differs_advisory(loaded, target_id)
    if differs is not None:
        advisories.append(differs)
    return ExportScripts(
        out=str(out_dir), files=[str(script_path)], artifact_id=target_id, advisories=advisories,
    )


def _octree_differs_advisory(loaded: LoadedProject, target_id: str) -> Advisory | None:
    """R849: `EXPORT_OCTREE_DIFFERS` (warn) when an octree in the chain will not be rebuilt as the
    run compiled it: a file its build compiled is gone or holds other bytes now (a materials edit
    after the run is one), or the octree was registered without the list of files it compiled
    (`rad_engine.export.exported_octree_differences`). The script is still written."""
    differences = exported_octree_differences(
        loaded.manifest, target_id, project_root=loaded.paths.root
    )
    if not differences:
        return None
    causes = "; ".join(f"{octree_id}: {cause}" for octree_id, cause in differences)
    return Advisory(
        code="EXPORT_OCTREE_DIFFERS", severity="warn",
        message=(
            "This script will not rebuild the octree the run compiled, so its octree and every "
            f"result traced on it can differ from the run's. {causes}. Re-run the tool that built "
            "the octree, then export again."
        ),
    )


def _copy_artifact_file(artifact: Artifact, out_dir: Path, roots: tuple[Path, ...]) -> str | None:
    """Copy `artifact`'s own file into `out_dir`; `None` (rather than raising) if it is missing, not
    a regular file, or -- ruling R349 -- outside every allowed root, which `open_regular_file`'s
    `O_NOFOLLOW`/`S_ISREG` says nothing about. The caller folds every `None` into `EXPORT_PARTIAL`
    instead of failing the whole export for one stale or out-of-root reference."""
    src = Path(artifact.path)
    try:
        contained_src = contain(src, roots)
        with open_regular_file(contained_src, "rb") as handle:
            data = handle.read()
    except (FileNotFoundError, NotARegularFileError, PathOutsideRoot, RootsNotConfigured):
        return None
    dest = _contained_dest(out_dir, _safe_artifact_filename(artifact), roots)
    _write_bytes(dest, data)
    return str(dest)


def _export_artifacts(
    loaded: LoadedProject, target_id: str, out_dir: Path, roots: tuple[Path, ...]
) -> ExportArtifacts:
    report = assemble(loaded.manifest, target_id)
    # B3's second half. `_safe_artifact_filename` has the same non-injectivity -- two artifact ids
    # differing only in a character `_sanitize_component` folds to `_` give one filename -- and the
    # fix wave found no reachable pair, `rad_engine.project.refuse_colliding_outputs` blocking the
    # candidates upstream. Guarded anyway, and for one line at this call site, because "no pair is
    # constructible today" is a property of a *different* module's guard: the two are free to move
    # apart, and this is the one that would report success while overwriting an audit artefact.
    _refuse_colliding_copies(
        [(_safe_artifact_filename(a), a.id) for a in report.matrices],
        what="artifacts",
        why=(
            "An artifact's destination name is its id with every character outside [A-Za-z0-9-_.] "
            "folded to '_', plus the source suffix, so two ids differing only in such a character "
            "name one file. Rename one of these artifacts."
        ),
    )
    files: list[str] = []
    missing: list[str] = []
    for artifact in report.matrices:
        written = _copy_artifact_file(artifact, out_dir, roots)
        if written is None:
            missing.append(artifact.id)
        else:
            files.append(written)

    advisories = None
    if missing:
        advisories = [Advisory(
            code="EXPORT_PARTIAL", severity="warn",
            message=(
                f"{len(missing)} artifact(s) could not be copied -- their file is no longer on "
                f"disk, is not a regular file, or is outside every allowed root -- and were "
                f"excluded from this export: {missing}."
            ),
        )]
    return ExportArtifacts(
        out=str(out_dir), files=files, artifact_id=target_id, advisories=advisories,
    )


def _export_manifest(
    loaded: LoadedProject, target_id: str | None, out_dir: Path, roots: tuple[Path, ...]
) -> ExportManifest:
    """`manifest.json` plus a copy of every registered input file it references -- the "ingredients"
    that complement `"artifacts"`'s "built things". Scoped to `target_id`'s own ancestor chain when
    a job is named, or the whole project's registered inputs when it is not."""
    inputs: list[InputRecord]
    if target_id is not None:
        report = assemble(loaded.manifest, target_id)
        inputs = list(report.inputs)
    else:
        inputs = list(loaded.manifest.inputs)

    manifest_payload = {
        "project": loaded.manifest.project.model_dump(mode="json"),
        "inputs": [record.model_dump(mode="json") for record in inputs],
    }
    files: list[str] = []
    manifest_json = out_dir / "manifest.json"
    _write_bytes(manifest_json, (json.dumps(manifest_payload, indent=2) + "\n").encode())
    files.append(str(manifest_json))

    # **B3: refuse a collision before the first byte is written, not after the second overwrite.**
    # `_refuse_filename_collision`'s posture, one tool over: the whole set is checked up front, so
    # a refusal never leaves a half-written bundle behind. `manifest.json` above is already
    # written by the time this runs and is not one of the colliding names (nothing mints
    # `"manifest.json"`), so a refusal costs at most that one file in a directory the caller named
    # for this export.
    #
    # Checked over every referenced file, including ones the copy loop below will skip as missing
    # or out-of-root. Deliberately conservative: whether a collision is "real" would otherwise
    # depend on which of the two files happened to be readable at this instant, which is not a
    # property a caller can reason about or reproduce.
    _refuse_colliding_copies(
        [
            (_copy_name(record.id, Path(file_ref.path).name), f"{record.id} -> {file_ref.path}")
            for record in inputs
            for file_ref in record.files
        ],
        what="registered inputs",
        why=(
            "An input id and a source filename are joined with '_' to form this name, and '_' is "
            "legal in both, so two different inputs can name one file. Rename one of them "
            "(project(action='describe') lists them)."
        ),
    )

    missing: list[str] = []
    for record in inputs:
        for file_ref in record.files:
            src = Path(file_ref.path)
            try:
                contained_src = contain(src, roots)
                with open_regular_file(contained_src, "rb") as handle:
                    data = handle.read()
            except (FileNotFoundError, NotARegularFileError, PathOutsideRoot, RootsNotConfigured):
                if record.id not in missing:
                    missing.append(record.id)
                continue
            # Ruling R349: `record.id` is free text a caller's `name=`/`input_id=` argument
            # supplies with no path-component pattern enforced end to end -- sanitised, then
            # re-contained, exactly like `_copy_artifact_file`'s own destination.
            dest = _contained_dest(out_dir, _copy_name(record.id, src.name), roots)
            _write_bytes(dest, data)
            files.append(str(dest))

    advisories = None
    if missing:
        advisories = [Advisory(
            code="EXPORT_PARTIAL", severity="warn",
            message=(
                f"{len(missing)} input file(s) could not be copied -- missing, not a regular "
                f"file, or outside every allowed root -- and were excluded from this export: "
                f"{missing}."
            ),
        )]
    return ExportManifest(
        out=str(out_dir), files=files, artifact_id=target_id, advisories=advisories,
    )


# --- the tool ------------------------------------------------------------------------------------


def export(
    action: Annotated[
        Action,
        Field(
            description=(
                "which operation: \"scripts\", \"report\", \"artifacts\" or \"manifest\". The tool "
                "description says what each one does and which of `job_id`/`artifact_id` it takes."
            )
        ),
    ],
    project: Annotated[
        str | None,
        Field(description="the project handle."),
    ] = None,
    job_id: Annotated[
        str | None,
        Field(
            description=(
                "the handle a run_* tool returned when it submitted a job. Refused if it names a "
                "job under a different project, or one that has not committed any artifact yet."
            )
        ),
    ] = None,
    artifact_id: Annotated[
        str | None,
        Field(
            description=(
                "the id of an artifact in this project's manifest -- the `artifact_id` a tool "
                "returned. Use it instead of `job_id` for a tool that submits no job but registers "
                "an artifact (`run_point_in_time`; `window_group(action=\"assign_bsdf\")`, whose "
                "`tmx` artifacts \"report\" and \"artifacts\" accept and \"scripts\" refuses); "
                "`run_annual` is the one tool that submits a job, and `run_render`, `run_glare`, "
                "`run_electric`, `run_view_and_sunlight` and `run_ugr` register no artifact. "
                "Refused if the manifest holds no such artifact, or if it is stale "
                "(`rad_core.manifest.evaluate`)."
            )
        ),
    ] = None,
    shell: Annotated[
        Shell,
        Field(description="\"sh\" or \"bat\", for \"scripts\" only. Unused otherwise."),
    ] = "sh",
    out: Annotated[
        str | None,
        Field(
            description=(
                "absolute path to a **directory** this call writes into -- not a file. Defaults to "
                "`<project>/12_export/` when omitted. Resolved through `rad_mcp.paths.contain` "
                "before anything is opened; a path outside every allowed root is refused, nothing "
                "is written. A project's root (a directory holding manifest.json and .manifest.lock) is "
                "refused, nothing is written."
            )
        ),
    ] = None,
) -> ExportResult:
    """Export what produced a result -- reproducibility and audit (docs/tool-surface.md#17-export).

    Use it to hand on the commands, the provenance or the files behind an artifact in a project's
    manifest -- a run's result or a `window_group(action="assign_bsdf")` binding -- or, with
    "manifest", behind the whole project.

    `"scripts"` writes the exact command sequence that built the target's own result, regenerated
    from the manifest rather than read back from anything a job recorded verbatim
    (`rad_engine.export`) -- `oconv -f`, an explicit `RAYPATH` computed inside the script itself, and
    per-step `2>` redirections that never merge into stdout. `"report"` writes a provenance
    narrative: inputs with their provenance, the matrices this result rests on, every advisory raised
    while building them, the Radiance version that built the target and each matrix, and the
    standard edition, with every `default`-provenance input labelled in an Assumptions section.
    `"artifacts"` copies the built matrices/octrees themselves into `out`. `"manifest"` writes the
    manifest record plus copies of the registered input files it references.

    DESTRUCTIVE ACTIONS: `scripts`, `report`, `artifacts` and `manifest` each replace a regular file
    of the same name already in `out`, whoever wrote it: `export.sh` or `export.bat`, `report.json`
    and `report.md`, an artifact's copy, `manifest.json` and each input's copy. `export` registers
    nothing in the project. `out` may not be a project's root.

    "scripts", "report" and "artifacts" each narrate one target's own build chain and require
    exactly one of `job_id`/`artifact_id`. "manifest" narrates the whole project when neither is
    given, or one target's own chain when one is. Exactly one of `job_id` and `artifact_id` for
    "scripts", "report" and "artifacts"; both are refused together for every action.
    """
    runtime = current()
    call = f"export(action={action!r})"

    if (refusal := refuse_missing(call, project=project)) is not None:
        return refusal
    assert project is not None

    if job_id is not None and artifact_id is not None:
        reason = (
            f"{call} was given both job_id={job_id!r} and artifact_id={artifact_id!r}: give "
            "exactly one, not both."
        )
        return refuse(reason, [Advisory(
            code="ARGUMENT_OUT_OF_RANGE", severity="error", field="artifact_id", message=reason,
        )])

    if action != "manifest" and job_id is None and artifact_id is None:
        reason = (
            f"{call} requires exactly one of job_id or artifact_id. job_id is the handle a run_* "
            "tool returned when it submitted a job; artifact_id is the id of an artifact already in "
            "this project's manifest, for a tool that submits no job but registers an artifact "
            "(run_point_in_time; window_group's assign_bsdf); run_annual is the one tool that "
            "submits a job, and run_render, run_glare, run_electric, run_view_and_sunlight and "
            "run_ugr register no artifact. "
            '"manifest" is the one action that requires neither.'
        )
        return refuse(reason, [Advisory(
            code="ARGUMENT_OUT_OF_RANGE", severity="error", field="job_id", message=reason,
        )])

    # R746: the ids the job's record lists, so the `KeyError` handler can tell an artifact this job
    # recorded from an input of its chain. Stays empty on the `artifact_id` route, which raises no
    # `KeyError` into that handler (Phase 16 Branch B plan, P6).
    recorded: frozenset[str] = frozenset()
    try:
        loaded = runtime.open(project)
        roots = runtime.roots

        # R729: an `artifact_id` refusal (missing from the manifest, or stale) happens before `out`
        # is created, exactly like every other refusal in this call -- so this is resolved before
        # `out_dir` is even computed, not folded into the job_id-based resolution below, which (for
        # `job_id` only, unchanged from before this ruling) still runs after `out_dir.mkdir`.
        artifact_target: str | None = None
        if artifact_id is not None:
            artifact_target = _artifact_target(loaded, artifact_id)

        candidate = Path(out) if out is not None else (loaded.paths.root / _DEFAULT_EXPORT_DIRNAME)
        out_dir = contain(candidate, roots)
        # R851 fix round 3 (N-113): a `..` component in an `out` that resolves inside the project is
        # refused on its face, so the linked-component walk below can never stop at a directory that
        # does not exist and let `contain` resolve past it (measured: `<project>/nothere/../12_export`
        # with `12_export` a planted link wrote the manifest into the link's target). An `out` that
        # resolves outside the project is the caller's directory and is not walked, so a `..` there is
        # not refused (final review of 2026-10-05: it was, though the reason is about the project).
        if ".." in candidate.parts and _resolves_inside(out_dir, _project_root_identity(loaded.paths.root.resolve())):
            message = (
                f"out={out!r} has a '..' component and resolves inside the project. Name the export "
                "directory without one: this server checks each directory of `out` inside the project "
                "for a symbolic link, and a '..' would let the path step past that check. Nothing has "
                "been written."
            )
            return refuse(message, [Advisory(
                code="ARGUMENT_OUT_OF_RANGE", severity="error", field="out", message=message,
            )])
        # N-113 (R851): an `out` that resolves inside the project is walked for a symbolic link at
        # any component below the project root, which is found among `out`'s directories by file
        # identity (so a case variant or a `~` spelling is walked too), before anything is created
        # or written (`_refuse_linked_out`). An `out` outside the project is the caller's directory.
        _refuse_linked_out(candidate, project_root=loaded.paths.root, resolved=out_dir)
        if (refusal := _refuse_project_root_out(call, out_dir)) is not None:
            return refusal
        out_dir.mkdir(parents=True, exist_ok=True)

        if action == "manifest":
            target_id: str | None = artifact_target
            if job_id is not None:
                target_id, _state, recorded = _job_target(runtime, loaded, job_id)
            advisory = (
                _version_advisory(loaded, target_id, runtime.launcher.version)
                if target_id is not None else None
            )
            return emit(_with(_export_manifest(loaded, target_id, out_dir, roots), advisory))

        state: str | None = None
        if artifact_target is not None:
            target_id = artifact_target
        else:
            assert job_id is not None
            target_id, state, recorded = _job_target(runtime, loaded, job_id)

        if action == "report":
            if state == "failed":
                reason = (
                    f"{call} refuses job_id={job_id!r}: it failed, and a partial run's numbers in "
                    "a report-shaped document will be read as results."
                )
                return refuse(reason, [Advisory(
                    code="ARGUMENT_OUT_OF_RANGE", severity="error", field="project", message=reason,
                )])
            # Each advisory is computed before its action body writes anything.
            advisory = _version_advisory(loaded, target_id, runtime.launcher.version)
            return emit(_with(_export_report(loaded, target_id, out_dir), advisory))

        if action == "scripts":
            advisory = _version_advisory(loaded, target_id, runtime.launcher.version)
            return emit(_with(_export_scripts(loaded, target_id, shell, out_dir), advisory))

        assert action == "artifacts"
        advisory = _version_advisory(loaded, target_id, runtime.launcher.version)
        return emit(_with(_export_artifacts(loaded, target_id, out_dir, roots), advisory))

    except (PathOutsideRoot, RootsNotConfigured, ProjectError, JobError, CodedWriteRefusal) as exc:
        logger.info("%s refused: %s", call, exc)
        return refuse_engine_error(exc)
    except (_ArtifactNotInManifest, _ArtifactStale) as exc:
        logger.info("%s refused: %s", call, exc)
        return refuse(str(exc), [Advisory(
            code="ARGUMENT_OUT_OF_RANGE", severity="error", field="artifact_id", message=str(exc),
        )])
    except BatPathUnquotableError as exc:
        logger.info("%s refused: %s", call, exc)
        return refuse(str(exc), [Advisory(
            code="ARGUMENT_OUT_OF_RANGE", severity="error", field="shell", message=str(exc),
        )])
    except (
        _JobBelongsToAnotherProject,
        _NoCommittedArtifact,
        _ExportFilenameCollision,
        UnsupportedArtifactKindError,
    ) as exc:
        logger.info("%s refused: %s", call, exc)
        return refuse(str(exc), [Advisory(
            code="ARGUMENT_OUT_OF_RANGE", severity="error", field="project", message=str(exc),
        )])
    except ManifestError as exc:
        # R868: `rad_core.report.assemble` raises `ManifestError` when an LM-83 ancestor of the chain
        # records an `lm83_edition` that is not an edition, refusing it rather than reading it as
        # either; `report`, `artifacts` and `manifest` with a target all assemble the chain before
        # writing any file. The same clause codes the evaluate path: `_artifact_target`'s
        # `evaluate_loaded` leaves a `built_from` cycle or a repeated id a bare `ManifestError`
        # (R790), raised before `out` is created. Shaped as `results` shapes a manifest it cannot
        # evaluate (R790): `MANIFEST_UNREADABLE`, `field="project"` because it is project state
        # (R14). Those texts end without a period and the edition's with one, so the join
        # normalises it.
        logger.info("%s refused: %s", call, exc)
        reason = (
            f"{call}: manifest.json could not be read for this export: {str(exc).rstrip('.')}. No run "
            "of this server writes such a manifest; repair or restore manifest.json. No export file "
            "has been written."
        )
        return refuse(reason, [Advisory(
            code="MANIFEST_UNREADABLE", severity="error", field="project", message=reason,
        )])
    except KeyError as exc:
        # Ruling R350: `rad_core.report.assemble`/`rad_engine.export.script_for_job` each raise a
        # bare `KeyError` when the artifact id they were handed is not in the manifest -- both
        # modules' own docstrings say so. Reachable when the *job record*'s committed-artifact
        # snapshot disagrees with the *manifest*'s current content (an out-of-band edit to
        # `manifest.json`, a restored backup) -- computed from project state, not an argument
        # refused on its face, so `field="project"` (R14).
        # R746: the reason names an input when the missing id is not one the job record lists.
        logger.info("%s refused: %s", call, exc)
        missing = exc.args[0] if exc.args else None
        if missing in recorded:
            what = f"the project's manifest no longer has the artifact this job recorded ({exc})"
        else:
            # R746: an input edge of the chain, or an ancestor artifact the job did not record --
            # `rad_core.report.assemble` reads any id outside the manifest's artifacts as an input.
            what = f"an input of this job's chain, {exc}, is no longer in the project's manifest"
        reason = f"{call}: {what}. manifest.json may have been edited or restored since this job ran."
        return refuse(reason, [Advisory(
            code="ARGUMENT_OUT_OF_RANGE", severity="error", field="project", message=reason,
        )])
    except NotARegularFileError as exc:
        # R851 (registers N-112 to N-114): coded, as every other tool codes this condition; `field`
        # is `out`, the argument whose destination it is. Until then it was a reason with no code.
        logger.info("%s refused: %s", call, exc)
        return refuse(str(exc), [Advisory(
            code="PROJECT_PATH_NOT_A_REGULAR_FILE", severity="error", field="out", message=str(exc),
        )])
    except OSError as exc:
        # The filesystem itself refused, not an argument: a read-only mount, a permission this
        # server does not hold, `out` already existing as a plain file. No advisory: a write of one
        # of `export`'s files is refused `PROJECT_WRITE_FAILED` by `_write_bytes` (R855), and what
        # reaches this clause is any other step (creating `out`, reading a source), whose failing
        # file this clause cannot name (`rad_mcp.weather_tool`'s posture).
        logger.warning("%s failed on the filesystem: %s", call, exc)
        return refuse(
            f"{call} could not complete: {exc}. Nothing about the arguments is wrong -- the "
            "filesystem refused the operation."
        )
