"""The `job` tool -- docs/tool-surface.md#3-job.

Thin: this module holds no job state of its own. *"The job store is one store. A task id and a job
id name the same record"* and *"two stores that could disagree about whether a matrix was committed
is the same class of defect as two ground reflectances"*
(docs/tool-surface.md#long-running-work-job-is-the-fallback-not-the-interface). Everything here goes
through `rad_engine.jobs.JobStore`, and the per-state projection is the store's own
`status_payload`, so this front door and the Tasks extension project the same record through the
same function.

**The output schema is a `oneOf` discriminated on `state`.** *"`job` returns a shape that varies by
state, and one tool gets one schema ... not a permissive object that would let a `running` payload
validate as `succeeded`"*
(docs/tool-surface.md#results-are-structured-and-a-structured-result-has-a-schema). Five state
shapes, two more by `action`, and the refusal, each with `additionalProperties: false`.

**Three payload facts the spec's per-state table does not carry**, all pinned against live
`store.status` payloads by `STATE_FIELDS` in `tests/test_jobs.py`:

- `returncode` is **nullable** in the `failed` shape. A job abandoned by a server restart is
  reconciled to `failed` and has no exit code, because no exit ever happened.
- `cancelled` carries `advisories`. Ruling R10 makes a cancelled job carry `JOB_TERMINATED` at
  `error`, and an advisory a front door cannot read is an advisory that was not emitted. The spec's
  table was amended to match.
- `project_root` and `message` are additions to that table. A `project` handle is a name and a name
  cannot be reopened while where projects live is undecided, so the record carries the path; and
  `message` is the honest carrier for *why* the supervisor ended a run.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Annotated, Any, Final, Literal

from mcp_types import CallToolResult
from pydantic import BaseModel, ConfigDict, Field, RootModel, StrictInt, StrictFloat

from rad_engine import AdvisoryRecord, JobError
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
from rad_mcp.runtime import Runtime, current, refuse_engine_error

logger = logging.getLogger(__name__)

__all__ = ["MAX_WAIT_S", "JobResult", "job", "JobBase"]

Action = Literal["status", "wait", "list", "cancel", "logs"]
Stream = Literal["stdout", "stderr", "both"]

#: The server maximum for `job(action="wait")`, in seconds.
#:
#: *"`wait` with `timeout_s` above the server maximum, or below zero. Reason: a tool call that
#: blocks forever is indistinguishable from a hung server"* (docs/tool-surface.md#3-job's "Refuses"
#: list). **The "or below zero" clause was inside the quotation marks and missing until the
#: whole-branch fix wave (R373)** -- a whole refusal condition dropped from a quoted list, which
#: reads as the document not having it. The number is a policy and
#: the spec does not set it, so the reasoning is: the server cannot see the client's own request
#: timeout, and a wait that runs to a bound *at* that timeout races it -- the client gives up on a
#: call that was about to answer, and the user sees a hang rather than a state. Thirty seconds sits
#: comfortably inside every client request timeout this revision's hosts ship with, and a caller who
#: wants longer polls again, which is the whole point of the async design. It bounds nothing else:
#: the job keeps running, and `timeout_s=None` still returns the current state immediately.
MAX_WAIT_S: Final[float] = 30.0

#: Actions that stop work. `cancel` kills a running process tree
#: (docs/tool-surface.md#annotations-are-coarser-than-the-surface).
DESTRUCTIVE_ACTIONS: tuple[str, ...] = ("cancel",)


# --- nested shapes -----------------------------------------------------------------------------


class _Nested(BaseModel):
    model_config = ConfigDict(extra="forbid")


# A snapshot, not a second source of truth (`rad_engine.jobs.CommittedArtifact`). It is what makes
# `partial_artifacts` on a failed job answerable without reopening the project --
# *"`partial_artifacts` on failure is the point of the design"* (docs/tool-surface.md#3-job).
class CommittedArtifactView(_Nested):
    """An artifact this job committed to the project manifest, as recorded at the moment it did.

    A snapshot, not a current status: it is not re-checked when the job is read, and
    `project(action="describe")` says whether the artifact is still valid.
    """

    id: str
    kind: str
    path: str
    digest: str
    content_digest: str | None = None
    built_from: list[str]


# An expired row is listed rather than hidden: a handle that vanishes from the list reads as one
# that never existed, which is the distinction `JOB_RECORD_EXPIRED` exists to keep.
class JobRow(_Nested):
    """One row of `action="list"`.

    `expired` is `true` when the job's record was purged after its retention period: the id still
    answers, with `JOB_RECORD_EXPIRED`, but its logs, timings and stderr are gone; the artifacts it
    committed stay in the project manifest. `position` is `null` unless the job is queued.
    """

    job_id: str
    project: str
    tool: str
    state: str
    created_at: datetime
    position: int | None = None
    expired: bool


# --- the result branches -----------------------------------------------------------------------


class JobBase(RadResult):
    """*"Every state returns `job_id`, `project`, `project_root`, `tool`, `state`, `created_at`"*
    (docs/tool-surface.md#3-job). Also the union schema's shared base.

    **Corrected by the whole-branch fix wave (R373).** This quotation used to drop `project_root`
    from the middle of the document's own list, with no ellipsis, and then add *"plus
    `project_root`"* after it -- asserting that §3 omits a field it publishes third, and making
    this module look like the place the field was introduced."""

    job_id: str
    project: str
    project_root: str
    tool: str
    state: str
    created_at: datetime


# So a client that submitted quick work behind a 5-phase run can see why nothing is happening rather
# than concluding the server hung (docs/tool-surface.md#concurrency). `_queue_view` computes both.
class JobQueued(JobBase):
    """A job waiting to start.

    `position` is the number of jobs ahead of it, a running job included. `blocked_on` lists the
    artifact ids this job would build that a job ahead of it, running or queued, also builds; `[]`
    when the wait is only the queue.
    """

    state: Literal["queued"]
    position: int | None = None
    blocked_on: list[str]


# A fabricated rate would be worse than an honest null (`rad_engine.jobs.JobRecord.progress`).
class JobRunning(JobBase):
    """A job in progress.

    `progress` is the fraction of the job's steps finished (a step skipped because its artifact
    was reused counts as finished), not a time estimate; `null` for a job of fewer than two steps,
    because one Radiance process reports nothing about how far through it is.
    `partial_artifacts` are those committed so far.
    """

    state: Literal["running"]
    phase: str | None = None
    started_at: datetime | None = None
    elapsed_s: float | None = None
    estimate_s: float | None = None
    progress: float | None = None
    partial_artifacts: list[CommittedArtifactView]


class JobSucceeded(JobBase):
    state: Literal["succeeded"]
    finished_at: datetime | None = None
    wall_time_s: float | None = None
    artifacts: list[CommittedArtifactView]
    result: dict[str, Any] | None = None


# R810 corrected the old wire text, which tied a null `returncode` to the abandoned case alone:
# `JobStore._work`/`_run` also fail a job with no returncode on a refusal or an exception, and on a
# stop before the next step spawned; a step killed at a server stop or its time bound keeps its exit
# status, `0` included (the abandoned-after-exit branch of `_run`, fix round 1). `message` is set by
# every `_end` and by the exception path, and is left `None` by the non-zero-exit `_finish`.
class JobFailed(JobBase):
    """A job that ended without succeeding.

    `returncode` is the exit status of the step the failure came from, a step killed at its time
    bound or at a server stop included. It can be `0`, when the server stopped after a step exited
    0 and before its output was committed. It is `null` when the job did not end on a step's exit: a
    refusal or error in the server, a server stop before the next step started, or a server
    restart that found the job still queued or running (it is not resumed). `message` says why the
    server ended the job; `null` when a step exited non-zero on its own, whose evidence is
    `returncode`, `stderr_tail` and `advisories`. `partial_artifacts` were committed to the
    project manifest before the failure.
    """

    state: Literal["failed"]
    finished_at: datetime | None = None
    failed_phase: str | None = None
    returncode: int | None = None
    stderr_tail: list[str]
    partial_artifacts: list[CommittedArtifactView]
    message: str | None = None


class JobCancelled(JobBase):
    state: Literal["cancelled"]
    finished_at: datetime | None = None
    cancelled_at_phase: str | None = None
    partial_artifacts: list[CommittedArtifactView]
    message: str | None = None


class JobListed(RadResult):
    """`action="list"`: one row per job this server has a record of, expired ones included,
    filtered to `project` when it is given."""

    jobs: list[JobRow]


class JobLogs(RadResult):
    """`action="logs"`: the last `tail` lines of the captured stream, or all of it when `tail` is 0
    or less.

    With `stream="both"`, `lines` holds stdout under a `--- stdout ---` line, then stderr under
    `--- stderr ---`, each cut to `tail` lines on its own, never interleaved. `truncated` is `true`,
    with a `RESULT_TRUNCATED` advisory, when lines were cut.
    """

    job_id: str
    stream: Stream
    lines: list[str]
    truncated: bool


JobPayload = (
    JobQueued | JobRunning | JobSucceeded | JobFailed | JobCancelled | JobListed | JobLogs | Refusal
)


# Declared as the tool's return annotation so the SDK validates every payload against it before it
# reaches the wire. The five state branches are told apart by a `const` on `state`; the two action
# branches and the refusal by which keys they require. With `additionalProperties: false` on each,
# a `running` payload relabelled `succeeded` matches no branch at all -- the failure the union
# exists to produce and check 1.11 asserts.
class JobResult(RootModel[JobPayload]):
    """What `job` returns: for `"status"`, `"wait"` and `"cancel"`, the job's shape for its
    current `state` (`queued`, `running`, `succeeded`, `failed` or `cancelled`), each carrying only
    the fields that mean something in that state; `jobs` for `"list"`; `lines` for `"logs"`; or a
    refusal."""

    @classmethod
    def model_json_schema(cls, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return one_of_schema(super().model_json_schema(*args, **kwargs), JobBase)


_STATE_MODELS: Final[dict[str, type[JobBase]]] = {
    "queued": JobQueued,
    "running": JobRunning,
    "succeeded": JobSucceeded,
    "failed": JobFailed,
    "cancelled": JobCancelled,
}


# --- mapping ------------------------------------------------------------------------------------


def _state_result(
    payload: dict[str, Any], extra: list[Advisory] | None = None
) -> CallToolResult:
    """`store.status`'s dict -> the branch of the union that matches its state.

    The dict is the store's own projection and is not rebuilt here; what is mapped is the
    `advisories` list, because the store carries `AdvisoryRecord` (durable, keeps every raw stderr
    line) and the wire carries `Advisory` (four fields, `extra="forbid"`). That mapping is
    `rad_mcp.advisories`, and it is the only thing that knows both types.
    """
    payload = dict(payload)
    advisories = _wire_advisories(payload)
    advisories.extend(extra or [])
    model = _STATE_MODELS[payload["state"]]
    return emit(model(**payload, advisories=advisories or None))


def _wire_advisories(payload: dict[str, Any]) -> list[Advisory]:
    """Take `advisories` out of a store payload and return it in the wire's shape.

    Removed from the dict rather than left in it, so the branch model receives exactly the keys it
    declares and an unmapped `AdvisoryRecord` can never reach the wire by accident.
    """
    raw = payload.pop("advisories", None) or []
    return to_wire_all(AdvisoryRecord.model_validate(entry).engine() for entry in raw)


def _truncation_advisory(stream: str) -> Advisory:
    return Advisory(
        code="RESULT_TRUNCATED",
        severity="warn",
        field="lines",
        message=(
            f"Only the last lines of {stream} are returned; the log holds more. The full stream is "
            "on disk in the job directory. Raise tail, or pass tail=0 for the whole stream."
        ),
    )


# --- the tool -------------------------------------------------------------------------------


def job(
    action: Annotated[
        Action,
        Field(
            description=(
                "which operation: \"status\", \"wait\", \"list\", \"cancel\" or \"logs\". The tool "
                "description says what each one does."
            )
        ),
    ],
    job_id: Annotated[
        str | None,
        Field(description="required for \"status\", \"wait\", \"cancel\" and \"logs\"."),
    ] = None,
    project: Annotated[
        str | None,
        Field(description="on \"list\", filter to one project name."),
    ] = None,
    timeout_s: Annotated[
        StrictFloat | None,
        Field(
            description=(
                "on \"wait\", how long to block. None returns the current state immediately. There "
                "is a server maximum, because a tool call that blocks forever is indistinguishable "
                "from a hung server; above it the call is refused rather than silently shortened."
            )
        ),
    ] = None,
    stream: Annotated[
        Stream,
        Field(
            description=(
                "on \"logs\", which stream. Defaults to \"stderr\" because that is where Radiance "
                "explains itself -- an exit status of 0 with warnings on stderr is the most common "
                "way to get a confidently wrong answer. \"both\" returns the two labelled and in "
                "order, never interleaved."
            )
        ),
    ] = "stderr",
    tail: Annotated[
        StrictInt,
        Field(description="on \"logs\", how many lines from the end. 0 or less returns the whole stream."),
    ] = 50,
) -> JobResult:
    """Poll, wait for, cancel, and read the logs of an async simulation.

    Use it with the `job_id` `run_annual` returns.

    `run_annual` returns a job id; a 5-phase annual run outlives any MCP client timeout. Every
    other tool returns its result from the call and submits no job, so `job` has no record of the
    `inline-...` `job_id` some of them return. The shape of a successful answer depends on the
    job's state -- queued, running, succeeded, failed or cancelled -- and each state carries only
    the fields that mean something in it.

    DESTRUCTIVE ACTION: `cancel` kills the running process tree. Work in flight is lost; artifacts
    already committed are not.

    RETENTION: a job record is kept for **7 days** after the job reaches a terminal state. After
    that the id still resolves, and says so -- `JOB_RECORD_EXPIRED`, which is a different answer
    from `JOB_NOT_FOUND` because the recoveries differ -- but the logs, timings and stderr are gone.
    Artifacts the job committed are never expired: they live in the project manifest and stay
    reusable. Job ids also survive a server restart.

    "status" returns the state-dependent shape. "wait" blocks until the job is terminal or
    `timeout_s` elapses, then returns the same shape. "list" reports every job this server knows
    about. "cancel" stops one. "logs" returns captured output.
    """
    runtime = current()
    try:
        # Retention is applied here rather than on a timer. A background thread would be one more
        # thing to shut down, and it would expire records while nobody is listening; the policy is
        # only ever observable through this tool, so applying it on entry makes "expired" become
        # true exactly when somebody asks. `purge_expired` walks terminal records only and leaves
        # committed artifacts alone (`rad_engine.jobs.JobStore.purge_expired`).
        #
        # **Inside the `try`, not before it.** Outside, a purge that failed -- an expired record
        # whose `10_jobs/` had been deleted used to reach `mkstemp` with no directory to write in --
        # came out of the *first* `job(...)` call of the day as a bare `ToolError` instead of a
        # refusal, whatever action the caller had asked for. `purge_expired` no longer raises for
        # that case; this is the second half of the same fix, and it is the half that holds if a
        # third failure mode is found.
        runtime.store.purge_expired()
        return _dispatch(
            runtime,
            action,
            job_id=job_id,
            project=project,
            timeout_s=timeout_s,
            stream=stream,
            tail=tail,
        )
    except JobError as exc:
        logger.info("job(action=%s) refused: %s", action, exc)
        return refuse_engine_error(exc)


def _dispatch(
    runtime: Runtime,
    action: Action,
    *,
    job_id: str | None,
    project: str | None,
    timeout_s: float | None,
    stream: Stream,
    tail: int,
) -> CallToolResult:
    if action == "list":
        return emit(JobListed(jobs=[JobRow(**row) for row in runtime.store.list_jobs(project=project)]))

    # `ARGUMENT_OUT_OF_RANGE` with `field="job_id"` (ruling R14, extended to missing arguments):
    # the same code and the same recovery shape as an argument supplied with a bad value.
    if (refusal := refuse_missing(
        f"job(action={action!r})",
        hint=(
            "It is the handle run_annual returned; job(action='list') shows the ids this server "
            "knows about."
        ),
        job_id=job_id,
    )) is not None:
        return refusal

    if action == "status":
        return _state_result(runtime.store.status(job_id))

    if action == "wait":
        if timeout_s is not None and not 0 <= timeout_s <= MAX_WAIT_S:
            reason = (
                f"job(action='wait', timeout_s={timeout_s}) is outside the server maximum of "
                f"{MAX_WAIT_S:g}s. A tool call that blocks forever is indistinguishable from a hung "
                "server. Wait again, or poll with action='status' -- the job keeps running either "
                "way."
            )
            # Ruling R14 (docs/tool-surface.md#advisory-code-register): one code for an argument
            # refused on its face, with `field` naming which one.
            return refuse(
                reason,
                [
                    Advisory(
                        code="ARGUMENT_OUT_OF_RANGE",
                        severity="error",
                        field="timeout_s",
                        message=reason,
                    )
                ],
            )
        return _state_result(runtime.store.wait(job_id, timeout_s=timeout_s))

    if action == "cancel":
        payload, advisories = runtime.store.cancel(job_id)
        return _state_result(payload, to_wire_all(advisories))

    logs = runtime.store.logs(job_id, stream=stream, tail=tail)
    return emit(
        JobLogs(
            **logs,
            advisories=[_truncation_advisory(stream)] if logs["truncated"] else None,
        )
    )
