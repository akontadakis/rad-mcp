"""The `project` tool -- docs/tool-surface.md#2-project.

Thin, in the sense CONTEXT.md's Layers table means it: this module validates and contains arguments,
picks the engine call, maps `EngineAdvisory` onto the wire, and shapes the envelope. It knows no
Radiance and spawns nothing. `rad_core.manifest` owns the schema, the digests and the invalidation
graph; `rad_engine.project` owns the directory layout and the manifest file.

**Ruling R1** puts `root` here with no default and no invented fallback, contained through
`rad_mcp.paths.contain` before `rad_engine.project` ever sees it -- that module takes an
already-validated absolute path because it must not import `rad_mcp`.

**Ruling R2** adds `action="update"`, the setter the invalidation table's project-ground row needs;
the reasoning is in docs/tool-surface.md#2-project and is deliberately not restated here.

**One output schema, several shapes.** `project` returns a different object per action, so its
`outputSchema` is a `oneOf` over the branches rather than a permissive object -- the rule
docs/tool-surface.md#results-are-structured-and-a-structured-result-has-a-schema states for `job`
and extends to `action=` in the same paragraph. The branches are told apart by which keys they
require and `extra="forbid"` on each, so `describe`'s payload cannot validate as `create`'s.
"""

from __future__ import annotations

import logging
from contextlib import nullcontext
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any, Literal

from mcp_types import CallToolResult
from pydantic import BaseModel, ConfigDict, Field, RootModel, StrictFloat

from rad_core.manifest import (
    ArtifactStatus,
    InputRecord,
    Manifest,
    ProvenanceValue,
    ReuseSummary,
    Units,
)
from rad_engine import (
    Description,
    LoadedProject,
    ProjectError,
    ProjectNotFoundError,
    ProjectPaths,
    ProjectSummary,
    archive_project,
    create_project,
    delete_project,
    describe,
    list_projects,
    open_project,
    update_project,
)
from rad_engine.export import COMPILED_PARAMETER
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
from rad_mcp.paths import (
    DefaultRootNotConfigured,
    PathOutsideRoot,
    RootsNotConfigured,
    contain,
)
from rad_mcp.runtime import Runtime, current, refuse_engine_error

logger = logging.getLogger(__name__)

__all__ = ["ProjectResult", "project"]

Action = Literal["create", "list", "describe", "update", "delete", "archive"]

#: Actions that remove or move a directory. Named in the tool description, because annotations are
#: per-tool and destructiveness here is per-action
#: (docs/tool-surface.md#annotations-are-coarser-than-the-surface).
DESTRUCTIVE_ACTIONS: tuple[str, ...] = ("delete", "archive")


# --- nested shapes -----------------------------------------------------------------------------
# Plain models: only a top-level result carries `advisories`.


class _Nested(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FileRefView(_Nested):
    path: str
    digest: str


class InputView(_Nested):
    """One registered input, as `describe` reports it, with each file's path and digest."""

    id: str
    kind: str
    files: list[FileRefView]
    units: str | None = None
    provenance: str | None = None
    source: str | None = None


# R810 moved this from the published text: the verdict is docs/tool-surface.md#2-project's, and
# `stale_reason` is R777's kind rule (corrected 2026-10-03: the text had said `parameters_changed`
# was the only other way an artifact's digest can move). A Radiance version change flags rather than
# invalidates (docs/tool-surface.md#how-provenance-invalidates-a-matrix).
class ArtifactView(_Nested):
    """One built artifact and whether it can still be reused.

    `valid` is judged from the recorded provenance; it does not re-read the artifact's file. An
    invalid artifact names its cause: `invalidated_by` lists exactly the inputs and artifacts it was
    built from whose digest has moved, `parameters_changed` is `true` when its own build parameters
    no longer reproduce its digest, or `stale_reason` says a rule makes it stale whatever its digest
    says. `radiance_version_changed` is `true` when the project's Radiance version differs from the
    build's; it flags the artifact and does not invalidate it.
    """

    id: str
    kind: str
    path: str
    valid: bool
    built_from: list[str]
    parameters: dict[str, Any]
    digest: str
    content_digest: str | None = None
    built_at: datetime | None = None
    wall_time_s: float | None = None
    radiance_version: str
    invalidated_by: list[str]
    parameters_changed: bool
    radiance_version_changed: bool
    stale_reason: str | None = None


# R810 moved this from the published text: `error` is surfaced rather than skipped because a
# project that silently vanishes from the list reads as deleted (`rad_engine.project.ProjectSummary`).
class ProjectRow(_Nested):
    """One project of `project(action="list")`; `name` is its handle, the directory name.

    `error` says why its manifest could not be read or evaluated; the project still exists, `units`
    and `last_job_at` are then `null`, and both artifact counts read 0 but were not counted.
    Otherwise `last_job_at` is `null` when the project has never started a job.
    """

    name: str
    root: str
    units: str | None = None
    artifacts_valid: int
    artifacts_invalid: int
    last_job_at: datetime | None = None
    error: str | None = None


# --- the result branches -----------------------------------------------------------------------


class ProjectBase(RadResult):
    """What every project-shaped answer carries. Also the union schema's shared base."""

    name: str
    root: str
    units: Units
    ground_reflectance: ProvenanceValue
    """An object, never a scalar: *"Provenance-wrapped values are objects, not scalars ... a code
    path that emits a bare `0.20` for the same field is a validation failure at the client, which is
    exactly the alarm we want"* (docs/tool-surface.md#results-are-structured-and-a-structured-result-has-a-schema).
    The model is `rad_core`'s own, so the wire shape and the manifest's cannot drift apart."""
    created_at: datetime | None = None
    radiance_version: str
    description: str | None = None


class ProjectCreated(ProjectBase):
    """`project(action="create")`: the new project.

    `ground_reflectance` is 0.20 labelled `default` when it was not given. `radiance_version` is
    `unknown` when no Radiance version could be read (`RADIANCE_VERSION_UNKNOWN`).
    """


# R810 moved this from the published text: `inputs`' keys are `rad_core.manifest.InputKind`
# verbatim. docs/tool-surface.md's illustrative block writes two of them as plurals; a
# pluralisation map would be a second name for a kind, the drift the register's naming rule
# exists to prevent.
class ProjectDescribed(ProjectBase):
    """`project(action="describe")`: every registered input and artifact, and what is reusable.

    `inputs` groups the inputs by kind, keyed by the kind's singular name (`sensor_grid`,
    `window_group`); a kind with no input has no key. `reuse_summary.rebuild_estimate_s` is `null`
    when any invalid artifact has no recorded build time.
    """

    inputs: dict[str, list[InputView]]
    artifacts: list[ArtifactView]
    reuse_summary: ReuseSummary


# R810 moved this from the published text: the action is ruling R2's, and the ground rows are
# docs/tool-surface.md#how-provenance-invalidates-a-matrix's. `newly_invalid` names the price of the
# change so a cheap one can be told from an expensive one.
class ProjectUpdated(ProjectBase):
    """`project(action="update")`: the project after the change.

    `changed_fields` names the fields whose stored value or label changed, empty when none did. `newly_invalid`
    lists the artifacts that were valid before and are not now: setting `ground_reflectance`
    invalidates the sky matrices, `run_point_in_time`'s sky and every artifact built from them, and
    leaves the daylight matrices alone.
    """

    changed_fields: list[str]
    newly_invalid: list[str]
    reuse_summary: ReuseSummary


class ProjectListed(RadResult):
    """`project(action="list")`: the projects directly under `root`, or under every allowed root.

    A root that does not exist is skipped, not refused.
    """

    projects: list[ProjectRow]


# R810 moved this from the published text: one branch for both actions, as docs/tool-surface.md's
# return block writes it (`removed: [paths]`, `archived_to: str | null`).
class ProjectRemoved(RadResult):
    """`project(action="delete")` or `project(action="archive")`; `root` is where the project was.

    After `delete`, `removed` lists what was deleted and `archived_to` is `null`. After `archive`,
    `removed` is empty and `archived_to` is the project's new directory.
    """

    name: str
    root: str
    removed: list[str]
    archived_to: str | None = None


ProjectPayload = (
    ProjectCreated | ProjectDescribed | ProjectUpdated | ProjectListed | ProjectRemoved | Refusal
)


# R810 moved this from the published text: declared as the tool's return annotation, so the SDK
# validates every payload against it before it reaches the wire ("Servers MUST provide structured
# results that conform to this schema").
class ProjectResult(RootModel[ProjectPayload]):
    """What `project` returns: exactly one of its five result shapes, or a refusal."""

    @classmethod
    def model_json_schema(cls, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return one_of_schema(super().model_json_schema(*args, **kwargs), ProjectBase)


# --- mapping ------------------------------------------------------------------------------------


def _input_view(record: InputRecord) -> InputView:
    return InputView(
        id=record.id,
        kind=record.kind,
        files=[FileRefView(path=f.path, digest=f.digest) for f in record.files],
        units=record.units,
        provenance=record.provenance,
        source=record.source,
    )


def _grouped_inputs(manifest: Manifest) -> dict[str, list[InputView]]:
    grouped: dict[str, list[InputView]] = {}
    for record in manifest.inputs:
        grouped.setdefault(record.kind, []).append(_input_view(record))
    return grouped


def _without_recorded_text(value: Any) -> Any:
    """`value` with each recorded file's `text` replaced by its size, `text_bytes`. R849's `compiled`
    record carries every per-call glow file's whole text so `export` can write it again; `describe`
    published it verbatim, uncapped, on every artifact (final review of 2026-10-05). The manifest
    keeps it; the published view keeps each file's path and digest."""
    if isinstance(value, dict):
        out = {key: _without_recorded_text(item) for key, item in value.items() if key != "text"}
        if isinstance(value.get("text"), str):
            out["text_bytes"] = len(value["text"].encode("utf-8"))
        return out
    if isinstance(value, list):
        return [_without_recorded_text(item) for item in value]
    return value


def _artifact_view(manifest: Manifest, status: ArtifactStatus) -> ArtifactView:
    artifact = manifest.artifact(status.id)
    parameters = dict(artifact.parameters)
    if COMPILED_PARAMETER in parameters:
        parameters[COMPILED_PARAMETER] = _without_recorded_text(parameters[COMPILED_PARAMETER])
    return ArtifactView(
        id=artifact.id,
        kind=artifact.kind,
        path=artifact.path,
        valid=status.valid,
        built_from=list(artifact.built_from),
        parameters=parameters,
        digest=artifact.digest,
        content_digest=artifact.content_digest,
        built_at=artifact.built_at,
        wall_time_s=artifact.wall_time_s,
        radiance_version=artifact.radiance_version,
        invalidated_by=list(status.invalidated_by),
        parameters_changed=status.parameters_changed,
        radiance_version_changed=status.radiance_version_changed,
        stale_reason=status.stale_reason,
    )


def _base_fields(loaded: LoadedProject) -> dict[str, Any]:
    project_record = loaded.manifest.project
    return {
        "name": project_record.name,
        "root": str(loaded.paths.root),
        "units": project_record.units,
        "ground_reflectance": project_record.ground_reflectance,
        "created_at": project_record.created_at,
        "radiance_version": project_record.radiance_version,
        "description": project_record.description,
    }


def _described(description: Description) -> dict[str, Any]:
    return {
        "artifacts": [
            _artifact_view(description.manifest, status)
            for status in description.statuses.values()
        ],
        "reuse_summary": description.reuse,
    }


def _row(summary: ProjectSummary) -> ProjectRow:
    return ProjectRow(
        name=summary.name,
        root=str(summary.root),
        units=summary.units,
        artifacts_valid=summary.artifacts_valid,
        artifacts_invalid=summary.artifacts_invalid,
        last_job_at=summary.last_job_at,
        error=summary.error,
    )


def _advisories(*groups: Any) -> list[Advisory] | None:
    """Flatten engine advisories onto the wire. `None` when empty, which is the omit-when-empty
    convention `RadResult.payload` implements."""
    wire: list[Advisory] = []
    for group in groups:
        wire.extend(to_wire_all(group))
    return wire or None


def _running_job_in(runtime: Runtime, name: str, root: Path) -> bool:
    """Whether a job is queued or running **against this directory**.

    Filtered by `project_root` and not only by the project name, because `JobStore` keys its rows by
    name and two roots may legitimately hold a project called `office`. Getting this wrong would
    refuse a delete because of an unrelated project's job, or -- worse -- allow one while a job is
    writing into the directory being removed.
    """
    for row in runtime.store.list_jobs(project=name):
        if row["state"] not in ("queued", "running"):
            continue
        if Path(runtime.store.record(row["job_id"]).project_root) == root:
            return True
    return False


def _missing(action: str, **arguments: Any) -> CallToolResult | None:
    """The refusal for a required argument this action has no default for, or `None`.

    Carries `ARGUMENT_OUT_OF_RANGE` with the argument's own name in `field` (ruling R14, extended to
    missing arguments): a client branches on the code and reads the field, rather than parsing which
    argument to supply out of a sentence.
    """
    return refuse_missing(f"project(action={action!r})", **arguments)


# --- the tool -------------------------------------------------------------------------------


def project(
    action: Annotated[
        Action,
        Field(
            description=(
                "which operation: \"create\", \"list\", \"describe\", \"update\", \"delete\" or "
                "\"archive\". The tool description says what each one does."
            )
        ),
    ],
    name: Annotated[
        str | None,
        Field(
            description=(
                "the project handle. Required except on \"list\". A single path component."
            )
        ),
    ] = None,
    root: Annotated[
        str | None,
        Field(
            description=(
                "on \"create\", the parent directory the project is made in -- absolute. Omitted, "
                "the project is created in the server's configured default root; if the operator "
                "configured none, the call is refused with DEFAULT_ROOT_NOT_CONFIGURED and you must "
                "pass this argument. Pass it whenever the project should live somewhere specific "
                "-- beside the CAD model it came from, say. On \"list\", the parent to list. On "
                "\"archive\", the destination parent to move the project into. Elsewhere, "
                "optional: it says which parent to look in when a name is ambiguous."
            )
        ),
    ] = None,
    units: Annotated[
        Units | None,
        Field(
            description=(
                "REQUIRED on \"create\", with no default. A scene modelled in feet and read as "
                "metres is wrong by 3.28x with no error anywhere."
            )
        ),
    ] = None,
    ground_reflectance: Annotated[
        StrictFloat | None,
        Field(
            description=(
                "the project's ONE ground reflectance, on \"create\" and \"update\". `gensky`, "
                "`gendaylit` and `gendaymtx` each default to 0.2 independently of each other and of "
                "the modelled ground polygon, so it is passed to each explicitly. Omitted on "
                "\"create\" it is stored as 0.20 labelled `default`, sourced to `gensky "
                "-defaults`; omitted on \"update\" it is left alone. It has a stated domain -- a "
                "**finite** fraction in [0, 1] -- and a value outside it is REFUSED "
                "(ARGUMENT_OUT_OF_RANGE, field=\"ground_reflectance\"), on \"create\" and "
                "\"update\" alike, before anything is written. Not clamped: 20 for \"20 %\" is "
                "refused."
            )
        ),
    ] = None,
    description: Annotated[
        str | None,
        Field(description="free text, on \"create\" and \"update\"."),
    ] = None,
    force: Annotated[
        bool,
        Field(
            description=(
                "on \"delete\", permission to remove a project that has recorded inputs or "
                "artifacts, and permission to delete one whose `manifest.json` will not validate "
                "(un-forced that is PROJECT_NOT_EMPTY, which names `force` as the recovery); on "
                "\"delete\" and \"archive\", permission to proceed while a job is writing into the "
                "directory. It is never permission to remove a directory that is not a project, "
                "and it never overrides PROJECT_TREE_UNRECOGNISED -- a tree holding things this "
                "project never put there is recovered by moving them out, not by forcing past the "
                "warning."
            )
        ),
    ] = False,
    confirm: Annotated[
        bool,
        Field(
            description=(
                "REQUIRED true on \"delete\". It defaults to false and is never derived from the "
                "other arguments -- it exists so a human is in the loop."
            )
        ),
    ] = False,
) -> ProjectResult:
    """Create and inspect the project directory and its manifest.

    Call it with "create" before any other tool that takes a `project` handle; "describe" shows
    which artifacts are still valid.

    Every other tool operates against a named project: matrix reuse only works if provenance is
    tracked, and the manifest is what tracks it.

    DESTRUCTIVE ACTIONS: `delete` removes a project directory and everything in it, and `archive`
    moves one. `delete` additionally requires `confirm=true` -- an untrusted annotation is a weak
    thing to hang a directory deletion on.

    RETENTION: a project directory is permanent. This server never deletes one, expires one, or
    garbage-collects one; it goes away only when you call `delete`. Job records inside it are the
    exception and are kept for **7 days** after the job finishes, after which the id still answers
    (`JOB_RECORD_EXPIRED`) but the logs and timings are gone. Committed artifacts are never expired.

    "create" lays out the directory and writes the first manifest. "list" reports the projects
    under a root, or under every configured root. "describe" is the provenance view: every
    artifact, what it was built from, and whether it is still valid. "update" sets
    `ground_reflectance` or `description` and reports which artifacts that invalidated. "delete"
    removes the directory. "archive" marks the project archived and moves it.
    """
    runtime = current()
    try:
        return _dispatch(
            runtime,
            action,
            name=name,
            root=root,
            units=units,
            ground_reflectance=ground_reflectance,
            description=description,
            force=force,
            confirm=confirm,
        )
    except (ProjectError, PathOutsideRoot, RootsNotConfigured, DefaultRootNotConfigured) as exc:
        logger.info("project(action=%s) refused: %s", action, exc)
        return refuse_engine_error(exc)
    except OSError as exc:
        # `delete` and `archive` move and remove trees, and the filesystem can refuse either half
        # way through -- a read-only mount, a permission the server does not hold, a file another
        # process has open on Windows. Unhandled, that left the wire with a bare `ToolError` and an
        # absolute path in it; here it is a refusal the client can read.
        #
        # **No advisory, deliberately.** `PROJECT_WRITE_FAILED` (R855) is a write of one named file
        # refused by the disk or the permissions; a `delete` or `archive` that fails half way through
        # moving or removing a tree is not that, and no other code's definition is true of it, so the
        # gap is reported rather than filled (docs/tool-surface.md#advisory-code-register). It is not
        # an argument the client can change, which is why it is also not `ARGUMENT_OUT_OF_RANGE`.
        logger.warning("project(action=%s) failed on the filesystem: %s", action, exc)
        return refuse(
            f"project(action={action!r}) could not complete: {exc}. Nothing about the arguments is "
            "wrong -- the filesystem refused the operation, so this is an environment problem "
            "(permissions, a read-only mount, or a file held open elsewhere)."
        )


def _dispatch(
    runtime: Runtime,
    action: Action,
    *,
    name: str | None,
    root: str | None,
    units: Units | None,
    ground_reflectance: float | None,
    description: str | None,
    force: bool,
    confirm: bool,
) -> CallToolResult:
    if action == "list":
        return _list(runtime, root)

    if (refusal := _missing(action, name=name)) is not None:
        return refusal

    if action == "create":
        return _create(
            runtime,
            name=name,
            root=root,
            units=units,
            ground_reflectance=ground_reflectance,
            description=description,
        )
    if action == "describe":
        return _describe(runtime, name=name, root=root)
    if action == "update":
        return _update(
            runtime,
            name=name,
            root=root,
            ground_reflectance=ground_reflectance,
            description=description,
        )
    if action == "delete":
        return _delete(runtime, name=name, root=root, force=force, confirm=confirm)
    return _archive(runtime, name=name, destination=root, force=force)


def _list(runtime: Runtime, root: str | None) -> CallToolResult:
    parents = [contain(root, runtime.roots)] if root is not None else list(runtime.roots)
    if not parents:
        raise RootsNotConfigured(
            "No allowed roots are configured, so there is nowhere to list. Set RAD_MCP_ROOTS, or "
            "pass root."
        )

    rows: list[ProjectRow] = []
    for parent in parents:
        try:
            rows.extend(_row(summary) for summary in list_projects(parent))
        except ProjectNotFoundError as exc:
            # A configured root that does not exist is an operator problem, not a reason to refuse
            # the whole listing -- and it is reported, because a silently empty list reads as "no
            # projects".
            logger.warning("skipping root %s: %s", parent, exc)
    return emit(ProjectListed(projects=rows))


def _create(
    runtime: Runtime,
    *,
    name: str,
    root: str | None,
    units: Units | None,
    ground_reflectance: float | None,
    description: str | None,
) -> CallToolResult:
    # `units` is still required with no default; `root` is not, as of ruling R57. The two are
    # deliberately no longer checked together: a substituted `units` would be a *physical* default
    # invented on the caller's behalf (the thing CONTEXT.md's Global Constraints forbid), while a
    # substituted `root` is an *operator* setting the human running the server chose in advance.
    if (refusal := _missing("create", units=units)) is not None:
        return refusal

    # **Ruling R57 — client-chosen, with a configured default root.** `root` given names the parent
    # to create in, as before. Omitted, the project lands in `RAD_MCP_DEFAULT_ROOT`, which is
    # contained against `roots` on every use by `default_parent` -- so the default is a landing
    # site, never a widening of what may be touched. With neither, the refusal is
    # `DEFAULT_ROOT_NOT_CONFIGURED` rather than `ARGUMENT_OUT_OF_RANGE`: the argument was not
    # rejected on its face, the server has no configured answer for where it should go.
    parent = contain(root, runtime.roots) if root is not None else runtime.default_parent()
    # **Contained BEFORE anything is written, not after.** `create_project` joins the name onto the
    # parent, and a *pre-existing symlink* at `<parent>/<name>` pointing out of the workspace is
    # followed by `mkdir(exist_ok=True)`: the twelve layout directories, `.manifest.lock` and
    # `manifest.json` all landed in the symlink's target, and only then did the check below refuse.
    # Reproduced 2026-08-01 against `<root>/office -> ~/Documents`. `contain` resolves and *then*
    # tests containment, which is what makes it see through the symlink -- the pattern
    # `Runtime.locate` already follows. `ProjectPaths.for_project` is what validates the name, so
    # `../etc` is refused here too, before the parent is even joined.
    contain(ProjectPaths.for_project(parent, name).root, runtime.roots)

    advisories: list[Advisory] = []
    version = runtime.launcher.version
    if version is None:
        # Allocated for exactly this: the project is created and usable, and every artifact built in
        # it will record a version string that is not a version. `info`, not a refusal -- laying out
        # a directory needs no Radiance.
        advisories.append(
            Advisory(
                code="RADIANCE_VERSION_UNKNOWN",
                severity="info",
                field="radiance_version",
                message=(
                    "No Radiance version could be read (no installation was found, or its "
                    "-version probe read none), so this project records its version as "
                    "'unknown'. Run radiance_status; artifacts built later will not be comparable "
                    "against this string."
                ),
            )
        )

    loaded = create_project(
        parent,
        name=name,
        units=units,
        radiance_version=version or "unknown",
        ground_reflectance=ground_reflectance,
        description=description,
    )
    # And again afterwards, cheaply: the check above is the one that prevents the writes, this one
    # catches a symlink planted in the window between them.
    contain(loaded.paths.root, runtime.roots)
    runtime.adopt(loaded.paths.root)

    return emit(ProjectCreated(**_base_fields(loaded), advisories=advisories or None))


def _describe(runtime: Runtime, *, name: str, root: str | None) -> CallToolResult:
    loaded = runtime.open(name, root)
    description = describe(loaded)
    return emit(
        ProjectDescribed(
            **_base_fields(loaded),
            inputs=_grouped_inputs(loaded.manifest),
            **_described(description),
            advisories=_advisories(description.advisories),
        )
    )


def _update(
    runtime: Runtime,
    *,
    name: str,
    root: str | None,
    ground_reflectance: float | None,
    description: str | None,
) -> CallToolResult:
    project_root = runtime.locate(name, root)
    runtime.adopt(project_root)
    paths = ProjectPaths(project_root)

    # Open *inside* both locks. What loses an update is the interval between the read and the write,
    # so a lock taken around only the save would serialise two writers who have already read the
    # same stale document (`Runtime.modifying`, `rad_engine.project.manifest_lock`).
    with runtime.modifying(paths):
        loaded = open_project(project_root, radiance_version=runtime.launcher.version)
        result = update_project(
            loaded,
            # Omitted means "leave it alone". `update_project` also accepts an explicit `None`
            # meaning "reset to the labelled default", which this signature cannot express -- a JSON
            # argument that is absent and one that is null arrive here identically.
            **(
                {"ground_reflectance": ground_reflectance}
                if ground_reflectance is not None
                else {}
            ),
            **({"description": description} if description is not None else {}),
        )

    return emit(
        ProjectUpdated(
            **_base_fields(loaded),
            changed_fields=list(result.changed_fields),
            newly_invalid=list(result.newly_invalid),
            reuse_summary=result.description.reuse,
            # `describe` already carries the load-time advisories, so they are not added twice.
            advisories=_advisories(result.description.advisories),
        )
    )


def _delete(
    runtime: Runtime, *, name: str, root: str | None, force: bool, confirm: bool
) -> CallToolResult:
    # **`confirm` is checked BEFORE `locate` -- ruling R59, 2026-08-10.** R14 defines
    # `ARGUMENT_OUT_OF_RANGE` as *an argument rejected on its face, before any work was attempted*,
    # and a lookup is work. The old order answered `PROJECT_NOT_FOUND` for an unconfirmed delete of
    # a nonexistent project, which is the more specific error but the wrong one: it makes the
    # refusal depend on state the consent question does not turn on, and it lets an unconfirmed
    # `delete` be used as an existence probe -- the caller learns whether a project exists by
    # sending a destructive call it never had permission to make.
    #
    # The message names the *handle* rather than the resolved directory, which is the reason this
    # was in `locate`'s shadow to begin with: the absolute path is not known yet, and printing it
    # would have meant resolving first. A handle is what the caller passed and what they will pass
    # again, so it identifies the call without leaking the server's layout.
    if not confirm:
        reason = (
            f"project(action='delete') on {name!r} requires confirm=true. It removes the "
            "directory and everything in it, including every matrix built so far. Nothing else "
            "about this call implies that consent."
        )
        # Ruling R14 allocated `ARGUMENT_OUT_OF_RANGE` for an argument refused on its face. `field`
        # says which one, which is why one code covers the class
        # (docs/tool-surface.md#advisory-code-register).
        return refuse(
            reason,
            [
                Advisory(
                    code="ARGUMENT_OUT_OF_RANGE",
                    severity="error",
                    field="confirm",
                    message=reason,
                )
            ],
        )

    project_root = runtime.locate(name, root)
    runtime.adopt(project_root)
    paths = ProjectPaths(project_root)
    running = _running_job_in(runtime, name, project_root)
    # The lock is only taken where there is a project to lock. Taking it first would create
    # `.manifest.lock` inside a directory this call is about to refuse to touch -- and *"force is
    # permission to delete a project, not permission to delete an arbitrary tree"* means a refused
    # delete must leave the directory exactly as it found it.
    with runtime.modifying(paths) if paths.manifest.is_file() else nullcontext():
        removed = delete_project(paths, force=force, has_running_job=running)

    return emit(
        ProjectRemoved(name=name, root=str(project_root), removed=[str(p) for p in removed])
    )


def _archive(
    runtime: Runtime, *, name: str, destination: str | None, force: bool
) -> CallToolResult:
    # `root` names the destination parent here, and it is required for the same reason it is on
    # `create`: an archive directory this server picked for itself would settle
    # docs/plan.md#still-open item 1 by omission. Keyed `root=` so the advisory's `field` is the
    # argument the client passes, not the name this function calls it internally.
    if (refusal := _missing("archive", root=destination)) is not None:
        return refusal

    project_root = runtime.locate(name, None)
    target_parent = contain(destination, runtime.roots)
    runtime.adopt(project_root)
    paths = ProjectPaths(project_root)
    running = _running_job_in(runtime, name, project_root)

    # The **thread** lock only, and deliberately not the cross-process file lock.
    #
    # `archive_project` moves the directory and then marks the manifest at its new location. The
    # lock file lives inside the directory, so it moves too -- same inode, new path -- and
    # `save_manifest`'s own acquisition at the new path opens a *second* file description onto the
    # inode this frame is already holding. `flock` is per open file description, so the process
    # blocks on itself for the full timeout and reports `MANIFEST_LOCK_BUSY` against a lock nobody
    # else ever took. Observed exactly that before this comment existed.
    #
    # Nothing is given up that could be held: no cross-process lock can span a rename of the
    # directory it lives in. `archive_project` takes the file lock at the destination, which is where
    # the only write happens.
    with runtime.store.manifest_lock:
        archived = archive_project(
            paths, destination=target_parent, force=force, has_running_job=running
        )

    return emit(
        ProjectRemoved(
            name=name,
            root=str(project_root),
            removed=[],
            archived_to=str(archived.paths.root),
        )
    )
