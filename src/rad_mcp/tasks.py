"""The MCP Tasks extension (`io.modelcontextprotocol/tasks`) over the one job store.

docs/tool-surface.md#long-running-work-job-is-the-fallback-not-the-interface, check 1.12.

**What this module is.** MCP 2026-07-28 keeps Tasks as an *extension* rather than core protocol:
`mcp_types` models the whole vocabulary -- `CreateTaskResult`, `Task`, `TaskStatus`,
`GetTaskRequest`, `CancelTaskRequest`, `TaskStatusNotification` -- and `mcp.server` serves **none**
of it. There is no `tasks/*` handler, no task store and no capability advertisement anywhere in the
server package; the only mention is a docstring in `mcp.server.extension` naming `tasks/get` as an
example of a method an extension would serve. So the handlers are ours, built on that framework
(SEP-2133), over the job store Task 4 delivered. `tests/test_tasks.py` asserts that SDK state
directly (check 0.6) so the reason for writing this by hand cannot quietly outlive itself.

**One store, two front doors, and this is the hard rule.** *"The job store is one store. A task id
and a job id name the same record"*; *"two stores that could disagree about whether a matrix was
committed is the same class of defect as two ground reflectances."* Nothing here keeps task state.
Every method below reads `rad_mcp.runtime.current().store`, and the per-state projection is
`rad_engine.jobs.status_payload` -- the same function `job(action="status")` returns. A task id
**is** a job id: `tasks/get` answers for any job this server knows about, and cancelling through
`tasks/cancel` is visible through `job(action="status")` on the same id, because there is only one
record to change.

**Never hand a task to a client that did not ask for one.** *"Before returning a
`CreateTaskResult`, verify that the client included the extension in its per-request capabilities.
Never return a task to a client that did not declare support."* On 2026-07-28 those capabilities
ride every request's `_meta` (`io.modelcontextprotocol/clientCapabilities`) and the SDK records them
on the connection, so `intercept_tool_call` can read them per call. A client that did not declare
would get, through `handoff`, the unchanged job-id shape plus `TASK_UNSUPPORTED_BY_CLIENT` at
`info`, which is what would let a user comparing two hosts see *why* they behave differently. No
tool calls `handoff` today, so that advisory is not emitted (below).

The negotiation is deliberately an **interceptor** rather than something each `run_*` tool does for
itself. There will be six or seven of them; a rule implemented six times is a rule that will be
implemented five times after the next tool is added. `handoff` below is the only thing a `run_*`
tool has to call, and it is honest on its own -- a server built *without* this extension still
returns the job-id shape carrying `TASK_UNSUPPORTED_BY_CLIENT`, because that statement is true.

**No tool calls `handoff` today** (R779, measured on the wire): `run_annual` returns its own
`AnnualResult`, with a `job_id` and no hand-off marker, so a client that declared the extension
gets that result, and a client that declared nothing gets the same one. `tasks/get`,
`tasks/list` and `tasks/cancel` were each measured answering for the job id `run_annual` returned,
from a declaring and from a non-declaring client. `tasks/result` was measured for one job each: for
the declaring client's job, which had failed, it returned a `CallToolResult` (the measurement
printed its class, not its `isError` or content); for the plain client's job, while it was running,
it raised an error naming the state.
*(Corrected 2026-10-04, Phase 19, R786: this said "A succeeded job's `tasks/result` was not
measured.")* Measured 2026-10-04 (Phase 19): a succeeded `run_annual` job's `tasks/result` is the
job's own `result`, `{"artifacts": [...]}`, not `run_annual`'s `AnnualResult`, from a declaring and
from a non-declaring client. R786 (owner decision F5) keeps `handoff` uncalled and documented as
such; `tests/test_tasks.py::TestNoToolCallsHandoff` fails the day a module under `src/` calls it.
*(Corrected 2026-10-04, Phase 19, R788: it said "a `CallToolResult` carrying the failure (`isError`)";
the measurement printed `declaring result -> CallToolResult` and nothing more.)*

**`job` keeps the forensics.** `action="list"`, `action="logs"` and `partial_artifacts` have no home
in a `Task`. A `failed` task says *failed* and points at `job`; the stderr tail, the classified
advisories and the artifacts committed before the failure stay reachable there, and that is the
whole point of the failure design -- a 5-phase run that dies in Part 3 has already spent an hour
building V, D and the 3-phase result.

**`ttlMs` and `pollIntervalMs` are numbers we mostly do not have.** `pollInterval` is a fixed
conservative constant, stated as such below, not a fabricated per-method estimate: until the
estimator is calibrated against recorded `wall_time_s`, any per-tool number would be invention
(docs/tool-surface.md#unsourced-values). `ttl` is derived from the store's own retention rather than
guessed, and is `null` exactly while the record provably cannot expire.

**Elicitation and `input_required` are not adopted.** `TaskStatus` includes `input_required` and
this module never produces it. It is optional for clients, so the refusal path has to exist anyway
and would become a second implementation of the same rule; and a round trip that asks the *model*
for a maintenance factor is a fabricated default with extra steps
(docs/tool-surface.md#elicitation-is-not-a-way-to-fill-in-a-required-argument).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from datetime import datetime
from typing import Any, Final, Literal

from mcp.server.context import CallNext, HandlerResult, ServerRequestContext
from mcp.server.extension import Extension, MethodBinding
from mcp.shared.exceptions import MCPError
from mcp_types import (
    INVALID_PARAMS,
    CallToolRequestParams,
    CallToolResult,
    CancelTaskRequestParams,
    CancelTaskResult,
    ContentBlock,
    GetTaskPayloadRequestParams,
    GetTaskRequestParams,
    GetTaskResult,
    ListTasksResult,
    PaginatedRequestParams,
    ServerTasksCapability,
    ServerTasksRequestsCapability,
    Task,
    TasksCallCapability,
    TasksCancelCapability,
    TasksListCapability,
    TaskStatus,
    TasksToolsCapability,
    TextContent,
)
from mcp_types import CreateTaskResult as _CreateTaskResult
from pydantic import TypeAdapter

from rad_engine import AdvisoryRecord, JobError, JobRecord, JobState, JobStore
from rad_engine.jobs import TERMINAL_STATES
from rad_mcp.advisories import to_wire_all
from rad_mcp.envelope import Advisory, emit
from rad_mcp.job_tool import JobBase
from rad_mcp.runtime import Runtime, current

logger = logging.getLogger(__name__)

__all__ = [
    "HANDOFF_META_KEY",
    "JOB_META_KEY",
    "POLL_INTERVAL_MS",
    "STATUS_BY_STATE",
    "TASKS_EXTENSION_ID",
    "JobHandoff",
    "TaskCreated",
    "Tasks",
    "handoff",
    "task_of",
]

#: The extension's reverse-DNS identifier, as the client declares it and as the server advertises it
#: under `ServerCapabilities.extensions`.
TASKS_EXTENSION_ID: Final = "io.modelcontextprotocol/tasks"

#: `Task.pollInterval`, milliseconds. **A fixed conservative value, and stated as one.**
#:
#: The spec invites a per-request estimate. We do not have one: *"until the estimator is calibrated
#: from recorded `wall_time_s`, `pollIntervalMs` is a fixed conservative value, stated as such, not
#: a fabricated per-method estimate"* (docs/tool-surface.md, the four adopted rules). A number
#: derived from `Step.estimate_s` would look calibrated and would not be -- which is why every
#: runtime estimate in this project carries `RUNTIME_ESTIMATE_UNCALIBRATED`, `JobStore.submit`
#: included.
#:
#: Five seconds is chosen against the *cost of polling*, which is a fact we do have: `tasks/get`
#: reads an in-memory record under the store's lock and touches no disk, so the load it imposes is
#: negligible, while a 5-phase annual run is measured in hours. Polling faster wastes a round trip;
#: polling much slower would make a 30 s render feel asynchronous when it is not. It is a hint
#: either way -- the client decides, and `job(action="wait")` remains the way to block.
POLL_INTERVAL_MS: Final = 5_000

#: Result `_meta` key marking a `run_*` result as a job hand-off, so `intercept_tool_call` can find
#: the job id without guessing from the payload.
#:
#: A marker rather than sniffing `structured_content` for a `job_id`: `job(action="status")` on a
#: running job *also* returns `job_id` and `state`, and converting a poll into a task would be a
#: nonsense the client could not undo. The interceptor strips the key before the result reaches the
#: wire -- it is a seam between this module and the tool, not part of any tool's published contract.
HANDOFF_META_KEY: Final = "rad-mcp/jobHandoff"

#: Result `_meta` key carrying a running job's status metadata alongside a `Task`.
#:
#: The state mapping requires **three** fields to *"become status metadata"* -- `phase`, `elapsed_s`
#: and `progress` -- and `Task` has exactly one free-text field, so they need somewhere structured to
#: go. `_meta` is that somewhere, and `JOB_META_FIELDS` is the whole of it.
#:
#: **It is deliberately not the whole `status_payload`.** Shipping the projection wholesale put
#: `result`, `artifacts`, `partial_artifacts`, `stderr_tail` and `returncode` on the task -- which
#: contradicts *"Tasks carries the handle; `job` carries the evidence"*
#: (docs/tool-surface.md#long-running-work-job-is-the-fallback-not-the-interface), contradicts this
#: module's own `failed` status message telling the client to read those through `job`, and
#: contradicts `rad_mcp.envelope.emit`'s rule that `_meta` is *"not a second place to put results"* --
#: a client could read `result` from here and never call `tasks/result` at all. It also re-shipped
#: the whole record on every poll. Three fields, named by the mapping, and nothing else.
JOB_META_KEY: Final = "rad-mcp/job"

#: The three fields `JOB_META_KEY` carries, and the whole of what the mapping calls status metadata.
#: They exist only on a `running` payload, so a task in any other state carries no `_meta` block.
JOB_META_FIELDS: Final = ("phase", "elapsed_s", "progress")

#: **The state mapping, written down once**
#: (docs/tool-surface.md#long-running-work-job-is-the-fallback-not-the-interface).
#:
#: `queued -> working` is the interesting row: Tasks has no queued state, so a client holding only a
#: task handle cannot otherwise tell "waiting behind a 5-phase run" from "running and silent". Our
#: concurrency-1 FIFO makes `position` and `blocked_on` the whole answer to *"why is nothing
#: happening"*, so they go in `statusMessage` -- see `_status_message`.
#:
#: `input_required` is absent by design and is not an oversight; see the module docstring.
STATUS_BY_STATE: Final[dict[JobState, TaskStatus]] = {
    "queued": "working",
    "running": "working",
    "succeeded": "completed",
    "failed": "failed",
    "cancelled": "cancelled",
}


class TaskCreated(_CreateTaskResult):
    """`CreateTaskResult` with the wire tag the extension reserves.

    `mcp_types.CreateTaskResult` inherits `Result`, which declares no `result_type`, so a bare one
    serialises without `resultType` and the SDK's outbound pass then stamps it `"complete"` -- a
    task announced as a finished result. `ResultType` is an open union and *"the tasks extension
    reserves 'task'"* (`mcp_types._types.ResultType`), so the tag is declared here.

    It also has to be a distinct class for the client half: `mcp.client.extension.ResultClaim`
    requires the claimed model's `result_type` to be `Literal["task"]`, which is how a client that
    declared the extension parses this shape instead of failing core `CallToolResult` validation.
    """

    result_type: Literal["task"] = "task"


class JobHandoff(JobBase):
    """The async shape a `run_*` tool returns once it has handed work to the job store.

    *"`run_annual` **always** returns a job id ... `{state: "running", job_id: ...}`"*
    (docs/tool-surface.md#3-job). The five shared fields come from `JobBase`, the same base the
    `job` tool's union publishes -- a hand-off and a poll of one job agree on `job_id`, `project`,
    `project_root`, `tool` and `created_at` because they are literally the same five fields.

    **`state` is the record's real state, not the literal `"running"` the spec sketches.** `submit`
    returns with the job `queued`, and the worker may or may not have picked it up by the time this
    result is built. Reporting `"running"` regardless would be the two front doors disagreeing about
    one record in the first sentence they speak, which is the defect this whole module exists to
    prevent. A client that needs `running` waits for it.
    """

    state: Literal["queued", "running", "succeeded", "failed", "cancelled"]


# --- projection ---------------------------------------------------------------------------------


#: Serialises a `datetime` the way pydantic does, which is the way the `job` tool does.
#:
#: Not `datetime.isoformat()`. `job`'s payload goes through `model_dump(mode="json")`, which renders
#: an aware UTC instant as `...Z`, while `isoformat()` renders it `...+00:00`. The same moment, two
#: spellings -- and two front doors that disagree in print about one record are exactly what this
#: module is built to prevent, even when the disagreement is only cosmetic. **Every** timestamp this
#: module emits goes through here: `Task.createdAt` and `lastUpdatedAt` as well as anything in a
#: payload, because a response whose two halves spell one moment differently is the same defect
#: arrived at from inside instead of across.
_DATETIME = TypeAdapter(datetime)


def _iso(moment: datetime) -> str:
    """`Task.createdAt` / `lastUpdatedAt`: *"ISO 8601 timestamp"*, both required strings.

    Job timestamps are timezone-aware UTC (`rad_engine.jobs`), so this is a format change and never
    a clock reading -- nothing here invents a time for a record that does not carry one. The
    spelling is pydantic's, for the reason on `_DATETIME`.
    """
    return _DATETIME.dump_python(moment, mode="json")


def _status_message(payload: dict[str, Any]) -> str | None:
    """The human sentence a `Task` carries, per the state mapping.

    Built from `payload`, which is `status_payload`'s own output and the only thing this module ever
    reads a job through. What each state contributes is that state's row of the mapping table:

    - `queued` -> *"Tasks has no queued state, so `position` and `blocked_on` go in the status
      message"*. Without them a client holding only a task handle sees `working` forever and
      concludes the server hung, when the true answer is "third in a FIFO with one worker".
    - `running` -> the phase and elapsed time. `progress` is null for a single opaque Radiance
      process and is reported only when the record has it, because a fabricated rate would be worse
      than an honest silence.
    - `succeeded` -> where the result is. `tasks/result` returns the job's `result` as
      `job(action="status")` reports it: for a tool that submits with `result_from` and hands off,
      what the call would have returned; for `run_annual`, which calls no `handoff`, the store's
      `{"artifacts": [...]}` (measured 2026-10-04). Until R786 this said "what the call would have
      returned synchronously", which `run_annual`'s job did not.
    - `failed` -> where the evidence is. The task has nowhere to put `partial_artifacts`,
      `stderr_tail` or the classified advisories, so it says so and names the tool that has them.
    - `cancelled` -> the supervisor's own `message`, which is what distinguishes a client's cancel
      from a server that stopped.
    """
    state = payload["state"]
    if state == "queued":
        position = payload.get("position")
        blocked_on = payload.get("blocked_on") or []
        where = "waiting to start" if position is None else f"queued at position {position}"
        blocked = f"; blocked on {', '.join(blocked_on)}" if blocked_on else ""
        return (
            f"{where} (one job runs at a time){blocked}. Tasks has no queued status, so this "
            "reports as working."
        )
    if state == "running":
        parts = [f"running {payload.get('phase') or 'a step'}"]
        elapsed = payload.get("elapsed_s")
        if elapsed is not None:
            parts.append(f"{elapsed:.0f}s elapsed")
        progress = payload.get("progress")
        if progress is not None:
            parts.append(f"{progress:.0%} of steps done")
        return ", ".join(parts) + "."
    if state == "succeeded":
        return (
            "finished; tasks/result returns the job's result, as job(action='status') reports it."
        )
    if state == "failed":
        return (
            f"{payload.get('message') or 'the run failed.'} The task cannot carry "
            f"partial_artifacts, stderr or the classified advisories: read them with "
            f"job(action='status', job_id={payload['job_id']!r}) and job(action='logs')."
        )
    return payload.get("message") or "cancelled."


def _ttl_ms(payload: dict[str, Any], retention_s: float | None) -> int | None:
    """`Task.ttl`: *"Actual retention duration from creation in milliseconds, null for unlimited."*

    Derived from the store's own retention, never guessed, and both `None` cases are *true* rather
    than convenient:

    - No retention configured -- `purge_expired` returns immediately, so nothing expires.
    - The job is not terminal -- `purge_expired` walks terminal records only
      (`rad_engine.jobs.JobStore.purge_expired`), so a queued or running record provably cannot be
      purged and its retention clock has not started. There is no finite number to state yet, and
      stating one anyway would tell a client to drop the handle to a live 5-phase run.

    Once terminal the number is exact rather than an estimate: retention runs from `finished_at`,
    `ttl` is measured from `created_at`, so it is the distance between those two plus the window.
    """
    finished_at = payload.get("finished_at")
    if retention_s is None or payload["state"] not in TERMINAL_STATES or finished_at is None:
        return None
    lifetime = (finished_at.timestamp() + retention_s) - payload["created_at"].timestamp()
    return int(lifetime * 1000)


def task_of(payload: dict[str, Any], *, retention_s: float | None) -> Task:
    """One job as a `Task`, **from the store's own projection and nothing else**.

    `payload` is `rad_engine.jobs.status_payload`'s dict -- the same function `job(action="status")`
    returns -- and it is the only input. Taking the `JobRecord` as well was the earlier shape and it
    was a second read of a live object: a transition landing between the two reads produced one
    response whose halves disagreed. Everything a `Task` needs is in the projection, so there is one
    read, and the two front doors cannot describe different moments.
    """
    return Task(
        # The identity claim of this whole module: the task id **is** the job id.
        task_id=payload["job_id"],
        status=STATUS_BY_STATE[payload["state"]],
        status_message=_status_message(payload),
        created_at=_iso(payload["created_at"]),
        # The record is rewritten at every transition, so "last updated" is when it last moved.
        last_updated_at=_iso(
            payload.get("finished_at") or payload.get("started_at") or payload["created_at"]
        ),
        ttl=_ttl_ms(payload, retention_s),
        poll_interval=POLL_INTERVAL_MS,
    )


def _task_wire(task: Task) -> dict[str, Any]:
    """A `Task` as the wire dict, with `ttl` present even when it is null.

    **This exists because of an asymmetry in the SDK, verified on `mcp` 2.0.0.** `ServerRunner`
    dumps a `BaseModel` handler result with `exclude_none=True`, but `Task.ttl` is *required and
    nullable* -- so a `Task` returned as a model loses `ttl` on the way out and the SDK's own client
    then rejects the result with `ttl: Field required`. A handler returning a **dict** is passed
    through untouched, so every handler below builds its dict here and the models are used to
    validate it (`_validated`) rather than to serialise it.
    """
    dumped = task.model_dump(by_alias=True, mode="json", exclude_none=True)
    dumped["ttl"] = task.ttl  # required on the wire; None must survive as an explicit null
    return dumped


def _validated(model: type[Any], wire: dict[str, Any]) -> dict[str, Any]:
    """Check a hand-built wire dict against the SDK model it claims to be, then ship the dict.

    The dict is what goes out -- see `_task_wire` for why it cannot be a model -- and this is what
    type-checks it: pydantic rejects a **missing or misnamed** field here, at the handler, rather
    than at whichever client validates it first.

    It does **not** catch an extra key. `MCPModel` does not forbid extras, so `model_validate`
    silently ignores anything the model does not declare; a stray field would round-trip to nothing
    on the client rather than raising here. That is a real limit of this guard and it is stated
    rather than implied -- the class of error it does catch is the one that breaks a client.
    """
    model.model_validate(wire)
    return wire


def _meta_field(payload: dict[str, Any]) -> dict[str, Any]:
    """`{"_meta": {...}}` carrying the three status-metadata fields, or `{}`. See `JOB_META_KEY`.

    Only a `running` payload has those fields, so every other state carries no `_meta` at all --
    omitted rather than sent as an empty object, because a key that is always present and usually
    empty reads as a place results might one day appear.
    """
    metadata = {name: payload[name] for name in JOB_META_FIELDS if name in payload}
    return {"_meta": {JOB_META_KEY: metadata}} if metadata else {}


def _json_safe(payload: dict[str, Any]) -> dict[str, Any]:
    """`status_payload`'s dict with its `datetime`s and `AdvisoryRecord`s in JSON form.

    Used by `tasks/result`, which carries a **payload** rather than a task: the store's projection is
    Python-typed because `job`'s pydantic models serialise it on the way out, and here there is no
    model in the way. The conversion goes through the same serialisers, so the two doors emit the
    same characters for the same record. The advisories go through `rad_mcp.advisories`, the one
    place that knows both advisory types, rather than being dumped raw: an unmapped `AdvisoryRecord`
    reaching the wire is exactly the leak that module exists to prevent.
    """
    out: dict[str, Any] = {}
    for key, value in payload.items():
        if key == "advisories":
            out[key] = [
                wire.model_dump(mode="json")
                for wire in to_wire_all(
                    AdvisoryRecord.model_validate(entry).engine() for entry in value or []
                )
            ]
        elif isinstance(value, datetime):
            out[key] = _DATETIME.dump_python(value, mode="json")
        else:
            out[key] = value
    return out


def _text(payload: Any) -> list[ContentBlock]:
    """The mirrored text block. *"A tool that returns structured content SHOULD also return the
    serialized JSON in a TextContent block"* -- the same rule `rad_mcp.envelope.emit` follows."""
    return [TextContent(type="text", text=json.dumps(payload, indent=2, default=str))]


# --- the hand-off a `run_*` tool returns --------------------------------------------------------

#: What `TASK_UNSUPPORTED_BY_CLIENT` says. Allocated at `info` in
#: docs/tool-surface.md#advisory-code-register: *"the client did not declare the Tasks extension, so
#: this run is polled through `job`"*.
_UNSUPPORTED_MESSAGE = (
    "This client did not declare the io.modelcontextprotocol/tasks extension, so this run is "
    "polled through job(action='status', job_id=...) rather than tasks/get. The job is unaffected; "
    "only the handle differs."
)


def handoff(record: JobRecord) -> CallToolResult:
    """The result a `run_*` tool would return once it has submitted a job. **No tool in `src/` calls it**
    (R779): it is the negotiation seam, exercised by `tests/test_tasks.py`'s stand-in.

    Returns the job-id shape carrying `TASK_UNSUPPORTED_BY_CLIENT` at `info`, marked with
    `HANDOFF_META_KEY` so `Tasks.intercept_tool_call` can convert it for a client that *did* declare
    the extension -- and only then.

    **The advisory is present by default and removed by the conversion, not added by its absence.**
    A tool cannot know what the client declared; the interceptor is the only thing that can. Making
    "polled through `job`" the default statement means it is true whenever it is emitted, including
    on a server assembled without this extension registered at all -- the case where a
    conditionally-added advisory would silently be missing.

    The record must already be durable, and is: `JobStore.submit` fsyncs `job.json` before it
    returns, because *"the task must be durably created before sending the response"*.
    """
    return emit(
        JobHandoff(
            job_id=record.job_id,
            project=record.project,
            project_root=record.project_root,
            tool=record.tool,
            state=record.state,
            created_at=record.created_at,
            advisories=[
                Advisory(
                    code="TASK_UNSUPPORTED_BY_CLIENT",
                    severity="info",
                    field="job_id",
                    message=_UNSUPPORTED_MESSAGE,
                )
            ],
        ),
        meta={HANDOFF_META_KEY: {"jobId": record.job_id}},
    )


# --- the extension ------------------------------------------------------------------------------


def _store(runtime: Runtime | None = None) -> JobStore:
    """The one store, with the retention policy applied on the way in.

    `job` applies retention on entry for the same reason (`rad_mcp.job_tool.job`): the policy is
    only ever observable through a front door, so applying it at *every* door is what stops the two
    from disagreeing about whether an id has aged out. A task reporting `working` for a record `job`
    calls expired would be the two-stores defect arrived at through one store.
    """
    store = (runtime or current()).store
    store.purge_expired()
    return store


def _read(store: JobStore, task_id: str) -> dict[str, Any]:
    """One job's status projection, or a JSON-RPC error. **One read, taken once.**

    `store.status` holds the store lock across the whole projection, so what comes back describes a
    single moment. Reading the record separately alongside it would not: a transition landing between
    the two calls yields one response whose halves disagree, which is the two-front-doors defect in
    miniature, inside one door.
    """
    try:
        return store.status(task_id)
    except JobError as exc:
        logger.info("tasks/* on %r refused: %s", task_id, exc)
        raise _refuse(exc) from exc


def _refuse(exc: JobError) -> MCPError:
    """An engine refusal as a JSON-RPC error.

    `tasks/*` are protocol methods, not tool calls, so there is no `isError` result to carry a
    refusal envelope -- *"a refusal is a tool execution error"* is a rule about `tools/call`
    (`rad_mcp.envelope`). The allocated code still travels, in `data`, so `JOB_NOT_FOUND` and
    `JOB_RECORD_EXPIRED` stay as distinguishable here as they are through `job`: one is recovered by
    checking the id, the other by re-running the work.
    """
    advisory = exc.advisory
    return MCPError(
        code=INVALID_PARAMS,
        message=str(exc),
        data={"code": advisory.code} if advisory is not None else None,
    )


def _client_declared(ctx: ServerRequestContext[Any, Any]) -> bool:
    """Whether *this request* declared the Tasks extension.

    On 2026-07-28 capabilities are per-request: the client stamps them into `_meta` under
    `io.modelcontextprotocol/clientCapabilities` and the SDK records them on the connection, which is
    the route `mcp.server.apps.client_supports_apps` takes for the Apps extension. Absent, malformed
    and empty all read as "did not declare" -- the rule is *"never return a task to a client that did
    not declare support"*, so anything short of an explicit declaration is a no.
    """
    capabilities = ctx.session.client_capabilities
    extensions = capabilities.extensions if capabilities is not None else None
    return bool(extensions) and TASKS_EXTENSION_ID in extensions


class Tasks(Extension):
    """`io.modelcontextprotocol/tasks`, served over `rad_engine.jobs.JobStore`.

    Holds no state: every method reads `rad_mcp.runtime.current().store`. Registered on the server
    at construction (`rad_mcp.server`), which is what advertises it under
    `ServerCapabilities.extensions` and what routes `tasks/*` to the handlers below.

    **It contributes no tools.** An extension may (`Extension.tools`) and this one deliberately does
    not: Tasks is a protocol capability, the registered tool set *"MUST NOT vary per-connection or
    as a side effect of other requests"*, and check 0.7 asserts the list does not move.
    """

    identifier = TASKS_EXTENSION_ID

    def settings(self) -> dict[str, Any]:
        """What this server supports, advertised at `capabilities.extensions[identifier]`.

        The shape is `ServerTasksCapability` -- the spec's own vocabulary for *which requests can be
        task-augmented* -- built from the SDK's models and dumped, rather than a hand-written dict
        that would be a second, drifting statement of what `methods()` below actually serves.
        """
        return ServerTasksCapability(
            list=TasksListCapability(),
            cancel=TasksCancelCapability(),
            requests=ServerTasksRequestsCapability(
                tools=TasksToolsCapability(call=TasksCallCapability())
            ),
        ).model_dump(by_alias=True, exclude_none=True)

    def methods(self) -> Sequence[MethodBinding]:
        """The four `tasks/*` methods. All additive -- none of them is a spec method.

        `tasks/get` and `tasks/result` are the polling path and are not optional: handing a client a
        task it cannot poll is worse than handing it a job id. `tasks/cancel` is what makes the
        `cancelled` row of the state mapping reachable at all. `tasks/list` is served because a
        client that speaks only Tasks has no other way to enumerate the handles it holds -- and it
        is **lossy** next to `job(action="list")`, which is the front door that carries `project`,
        `tool` and `expired`.
        """
        return (
            MethodBinding(method="tasks/get", params_type=GetTaskRequestParams, handler=self._get),
            MethodBinding(
                method="tasks/result",
                params_type=GetTaskPayloadRequestParams,
                handler=self._result,
            ),
            MethodBinding(
                method="tasks/cancel", params_type=CancelTaskRequestParams, handler=self._cancel
            ),
            MethodBinding(
                method="tasks/list", params_type=PaginatedRequestParams, handler=self._list
            ),
        )

    # -- handlers -----------------------------------------------------------------------------

    async def _get(
        self, ctx: ServerRequestContext[Any, Any], params: GetTaskRequestParams
    ) -> HandlerResult:
        """`tasks/get`: the same record `job(action="status")` reads, projected as a `Task`."""
        store = _store()
        payload = _read(store, params.task_id)
        task = task_of(payload, retention_s=store.retention_s)
        return _validated(GetTaskResult, {**_task_wire(task), **_meta_field(payload)})

    async def _result(
        self, ctx: ServerRequestContext[Any, Any], params: GetTaskPayloadRequestParams
    ) -> HandlerResult:
        """`tasks/result`: *"what the original request would have returned synchronously."*

        The return type is the original request's -- a `CallToolResult`, not `GetTaskPayloadResult`,
        which the SDK documents as unusable for this (*"validate the response into the original
        request's result type"*).

        Two shapes, matching the state mapping:

        - `succeeded` -> the job's `result`. No tool that submits a job has an inline shape for it
          to match: `run_annual` is the only one, and always returns a job id
          (docs/tool-surface.md §3). A job that declared no result projection has the store's
          default `{"artifacts": [...]}` as its `result` (`rad_engine.jobs`; measured 2026-10-04),
          so the status-payload fallback below is for a record with no `result` at all.
          *(Corrected 2026-10-04, Phase 19, R786: this said "A job that declared no result
          projection has no result, and the status payload is returned instead of a fabricated
          one.")*
        - `failed` -> `isError: true` with the reason and the classified advisories. The mapping says
          *"the task carries a JSON-RPC `error`"*; expressed at this layer that is a **tool**
          execution error, because this method's result type *is* a tool result and *"clients SHOULD
          provide tool execution errors to language models to enable self-correction"*
          (`rad_mcp.envelope`). The `failed` status on the `Task` is the protocol-level half of the
          same fact; a client polling `tasks/get` then `tasks/result` gets both.

        A non-terminal task has no payload at all. That is a protocol error rather than a result:
        there is nothing to return, and returning the current state under a name that means "the
        answer" is how a client comes to report a half-built matrix as a result.
        """
        store = _store()
        payload = _read(store, params.task_id)
        state = payload["state"]

        if state not in TERMINAL_STATES:
            raise MCPError(
                code=INVALID_PARAMS,
                message=(
                    f"task {params.task_id!r} is {state!r} and has no result yet. Poll "
                    "tasks/get until it reports a terminal status."
                ),
            )
        safe = _json_safe(payload)
        if state == "succeeded":
            body = safe["result"] if safe.get("result") is not None else safe
            return CallToolResult(content=_text(body), structured_content=body)
        # `cancelled` lands here too: nothing was produced, and the reason is the supervisor's.
        reason = payload.get("message") or f"the run ended {state}."
        body = {"refused": True, "reason": reason, "advisories": safe.get("advisories") or []}
        return CallToolResult(content=_text(body), structured_content=body, is_error=True)

    async def _cancel(
        self, ctx: ServerRequestContext[Any, Any], params: CancelTaskRequestParams
    ) -> HandlerResult:
        """`tasks/cancel`.

        *"Cancellation is cooperative -- the server acknowledges the intent but is not obligated to
        stop the work"*, which is also true of killing an `rcontrib` process tree. `JobStore.cancel`
        signals the group and reconciles the record; a cancel that arrives after the job finished is
        `JOB_CANCEL_LATE` at `info` rather than an error.

        **That advisory is appended to `statusMessage`, not carried as data.** `Task` has one
        free-text field and no advisory channel, and `_meta` is for the three status-metadata fields
        the mapping names -- not a general advisory bus. What `JOB_CANCEL_LATE` says is a fact about
        *this call* ("nothing was stopped") rather than evidence about the run, so the sentence is
        the honest home for it; the machine-readable copy stays where the code is allocated, on
        `job(action="cancel")`.

        `store.cancel` returns the projection itself, so this reads the record exactly once -- and
        the same cancellation is visible through `job(action="status")` on the same id, because
        there is one record and this changed it.
        """
        store = _store()
        try:
            payload, advisories = store.cancel(params.task_id)
        except JobError as exc:
            logger.info("tasks/cancel on %r refused: %s", params.task_id, exc)
            raise _refuse(exc) from exc
        task = task_of(payload, retention_s=store.retention_s)
        if advisories:
            note = " ".join(a.message for a in advisories)
            task = task.model_copy(
                update={"status_message": f"{task.status_message or ''} {note}".strip()}
            )
        return _validated(CancelTaskResult, {**_task_wire(task), **_meta_field(payload)})

    async def _list(
        self, ctx: ServerRequestContext[Any, Any], params: PaginatedRequestParams
    ) -> HandlerResult:
        """`tasks/list`: every live job this server knows about, as tasks.

        Unpaginated, in one page: the store holds one process's worth of records, and a cursor over
        an in-memory dict would be a pagination contract this server could not honour across a
        restart. Expired records are skipped rather than reported -- `Task` has no `expired` field,
        and `job(action="list")` is the front door where a handle that aged out is still visible as
        one.

        **No `_meta` on the elements.** `ListTasksResult.tasks` is typed `list[Task]`, and `Task` has
        no `_meta` field, so a client validating the result drops anything extra silently -- an
        annotation nobody can read. Status metadata belongs on `tasks/get`, which is the request that
        asks about one job.
        """
        store = _store()
        tasks: list[dict[str, Any]] = []
        for row in store.list_jobs():
            if row["expired"]:
                continue
            try:
                payload = store.status(row["job_id"])
            except JobError:  # pragma: no cover - raced with a purge between the list and the read
                continue
            tasks.append(_task_wire(task_of(payload, retention_s=store.retention_s)))
        return _validated(ListTasksResult, {"tasks": tasks})

    # -- negotiation --------------------------------------------------------------------------

    async def intercept_tool_call(
        self,
        params: CallToolRequestParams,
        ctx: ServerRequestContext[Any, Any],
        call_next: CallNext,
    ) -> HandlerResult:
        """Turn a `run_*` hand-off into a task -- **only** for a client that declared the extension.

        Runs for every `tools/call` and does nothing to a result that carries no `HANDOFF_META_KEY`,
        so `radiance_status`, `sensor_grid`, `project` and `job` pass through untouched.
        """
        result = await call_next(ctx)
        if not isinstance(result, CallToolResult):
            return result
        meta = dict(result.meta or {})
        marker = meta.pop(HANDOFF_META_KEY, None)
        if marker is None:
            return result
        # The marker is a seam between `handoff` and this method, not a published key: strip it on
        # both branches so it never reaches a client as an undocumented result field.
        result = result.model_copy(update={"meta": meta or None})

        if not _client_declared(ctx):
            # The job-id shape, unchanged, still carrying `TASK_UNSUPPORTED_BY_CLIENT` at `info` --
            # which `handoff` put there precisely so this branch has nothing to add.
            return result

        store = _store()
        try:
            payload = store.status(marker["jobId"])
        except JobError as exc:  # pragma: no cover - submit fsyncs the record before the id exists
            logger.warning("task hand-off for %s could not read its own record: %s", marker, exc)
            return result
        task = task_of(payload, retention_s=store.retention_s)
        return _validated(
            TaskCreated,
            {"resultType": "task", "task": _task_wire(task), **_meta_field(payload)},
        )
