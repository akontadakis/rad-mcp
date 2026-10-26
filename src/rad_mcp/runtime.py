"""Server-level state: the job store, the allowed roots, and how a handle becomes a directory.

`rad_mcp` is thin, but "thin" is not "stateless". Three things have to exist once per server and be
reachable from every tool, and each of them is here because putting it anywhere else would break a
rule stated somewhere else:

- **One `JobStore`.** *"The job store is one store. A task id and a job id name the same record"*
  (docs/tool-surface.md#long-running-work-job-is-the-fallback-not-the-interface). Phase 1's `job`
  tool and Phase 1's Tasks extension are two front doors onto this object and neither keeps job
  state of its own. A store constructed per call would be two stores.
- **The allowed roots, and the containment that uses them.** `rad_engine.project` takes an
  already-validated absolute path and must not import `rad_mcp` (CONTEXT.md, Layers), so ruling R1's
  containment happens here, on the way in.
- **The retention policy.** `JobStore(retention_s=...)` deliberately has no default: check 1.11
  requires the number to be stated in the tool descriptions, so it belongs where a description can
  quote it. `RETENTION_S` below is that number, and `tests/test_job_tool.py` asserts the descriptions
  and the configured value cannot drift.

**Recovery is wired here, and it is the difference between a durable handle and a broken one.**
`rad_engine.jobs` will not guess where projects live -- that is docs/plan.md#still-open item 1 -- so
it never scans the disk for a project directory it was not told about. `rad_mcp` therefore calls
`store.recover(project_root)` in two places: at startup across every configured root, and again on
every project open. Without both, a valid job id whose project this process has not touched answers
`JOB_NOT_FOUND`, which is a *wrong* answer about a record that is sitting on disk -- the durability
the whole job store was built for, lost at the last step (docs/plan.md, "Phase 1 — manifest, jobs,
purity guard", which lists this module as "the recovery that makes a job id survive a restart").
"""

from __future__ import annotations

import logging
import os
import threading
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from mcp_types import CallToolResult

from rad_core.grids import GridError
from rad_engine import (
    JobError,
    JobStore,
    Launcher,
    LoadedProject,
    ProjectError,
    ProjectNotFoundError,
    ProjectPaths,
    RadianceLauncher,
    RadianceNotFound,
    discover,
    open_project,
)
from rad_engine.project import MANIFEST_NAME
from rad_engine.project import manifest_lock as file_manifest_lock

from rad_mcp.advisories import to_wire
from rad_mcp.envelope import Advisory, refuse
from rad_mcp.paths import (
    CodedWriteRefusal,
    DEFAULT_ROOT_ENV_VAR,
    DefaultRootNotConfigured,
    PathOutsideRoot,
    RootsNotConfigured,
    contain,
    default_root_from_env,
    expanded_root,
    roots_from_env,
)

logger = logging.getLogger(__name__)

__all__ = [
    "RETENTION_S",
    "Runtime",
    "current",
    "refuse_engine_error",
    "set_current",
]

#: How long a **job record** survives after the job reaches a terminal state, in seconds. Seven days.
#:
#: The number is a policy, not a measurement, and picking it is this task's job: `JobStore` refuses
#: to invent one because *"the server's retention policy should be stated in the creation tool's
#: description ... so the model can see it when deciding to create state"*
#: (docs/tool-surface.md#handles-project-and-job_id), and a number invented in the engine would be
#: settled in a place no description can see.
#:
#: Seven days rather than hours or months: the record is forensics for a run that may have taken an
#: hour, and *"a 5-phase run is an expensive thing to discover has been garbage-collected"*. A week
#: covers a user who starts a run on Friday and reads it on Monday, and bounds `10_jobs/` for a
#: project that is built nightly. It expires the **evidence**, never the matrices: committed
#: artifacts live in the manifest and `purge_expired` does not touch them.
#:
#: **Stated verbatim in both tool descriptions.** `tests/test_job_tool.py` reads this constant and
#: requires the phrase to appear in the registered description text, so changing one without the
#: other goes red (check 1.11).
RETENTION_S: float = 7 * 24 * 60 * 60


class _DeferredRadianceLauncher:
    """A `Launcher` that discovers Radiance the first time a step actually needs it.

    The store is a server-level object and is built at import time; a Radiance install is a property
    of the machine and may be absent, arrive later, or be pointed at explicitly through
    `radiance_status(bin_dir=...)`. Discovering eagerly would make constructing the server fail on a
    machine with no Radiance -- and every one of Phase 1's checks runs there by design
    (docs/plan.md check 1.4).

    It resolves nothing itself: `RadianceLauncher` is still the thing that sets `RAYPATH` explicitly
    with `.` first, because an inherited `RAYPATH` missing `.` compiles an empty octree and exits 0
    (CONTEXT.md).
    """

    def __init__(self) -> None:
        self._delegate: RadianceLauncher | None = None
        self._guard = threading.Lock()

    def _discovered(self) -> RadianceLauncher | None:
        """The delegate, or `None` while no install can be found. Success is cached; failure is not
        -- an install that appears after the server started should be usable without a restart."""
        with self._guard:
            if self._delegate is None:
                try:
                    self._delegate = RadianceLauncher(discover())
                except RadianceNotFound as exc:
                    logger.info("no Radiance installation found: %s", exc)
                    return None
            return self._delegate

    def resolve(self, argv: Sequence[str], *, cwd: Path) -> tuple[list[str], dict[str, str]]:
        delegate = self._discovered()
        if delegate is None:
            raise JobError(
                "no Radiance installation was found, so no step can be launched. Run "
                "radiance_status to see what this machine has, or pass bin_dir."
            )
        return delegate.resolve(argv, cwd=cwd)

    @property
    def version(self) -> str | None:
        delegate = self._discovered()
        return delegate.version if delegate is not None else None


@dataclass
class Runtime:
    """Everything one running server owns. Constructed once; replaced wholesale in tests.

    A dataclass rather than module-level globals so a test can bind a store and a set of roots to a
    `tmp_path` without reaching into private module state, and so two tests cannot share a job queue.
    """

    roots: tuple[Path, ...]
    store: JobStore
    default_root: Path | None = None
    """Where `project(action="create")` lands a project that names no `root`. **Ruling R57.**

    Separate from `roots` because it answers a different question -- `roots` is what may be touched,
    this is where an unqualified create goes -- and because a default that *widened* `roots` would
    be a back door: a server with `RAD_MCP_ROOTS` unset would accept writes to a directory no
    containment set names. `default_parent` below contains it on every use, so it grants nothing.

    Sits after `store` because it carries a default and `store` does not; the dataclass field order
    is what makes that mandatory, not a preference.
    """
    launcher: Launcher = field(default_factory=lambda: _DeferredRadianceLauncher())
    """The same object the store was built with. Held here as well because the *tools* need the
    live install's version string -- `open_project(radiance_version=...)` is what makes
    `RADIANCE_VERSION_CHANGED` detectable -- and reading it off the store would mean reading a
    private attribute of another layer's object."""

    _recovered: set[Path] = field(default_factory=set, repr=False)
    """Project roots this runtime has already handed to `store.recover`.

    `recover` is idempotent -- it skips an id it already holds -- but it lists a directory and parses
    a JSON document per job to find that out, and a `job(action="status")` poll loop would pay that
    on every call. What must not be missed is the *first* open of a project, which is the call that
    makes an id from a previous process resolve at all."""

    @classmethod
    def from_env(cls, environ: dict[str, str] | os._Environ[str] | None = None) -> Runtime:
        launcher = _DeferredRadianceLauncher()
        return cls(
            roots=roots_from_env(environ),
            store=JobStore(launcher=launcher, retention_s=RETENTION_S),
            launcher=launcher,
            default_root=default_root_from_env(environ),
        )

    def default_parent(self) -> Path:
        """The contained parent directory an unqualified `create` lands in. **Ruling R57.**

        Raises `DefaultRootNotConfigured` when none is set, and `PathOutsideRoot` when one is set
        but falls outside `roots` -- the check that stops the default being a way to reach a
        directory the containment set does not name. Both are the operator's misconfiguration
        rather than the caller's argument, and they are told apart because their repairs differ
        (`rad_mcp.paths.DefaultRootNotConfigured`).

        Contained on **every** call rather than once at construction: `roots` and the default are
        both read from the environment, but the directory either names can be replaced by a symlink
        while the server runs, and `contain` resolves at the moment of use.
        """
        if self.default_root is None:
            raise DefaultRootNotConfigured(
                f"project(action='create') was called with no root and no default is configured. "
                f"Set {DEFAULT_ROOT_ENV_VAR} to the directory unqualified projects should be "
                f"created in, or pass root explicitly."
            )
        return contain(self.default_root, self.roots)

    @property
    def retention_s(self) -> float | None:
        """The retention the store **actually enforces**, never a second copy of the number.

        A field holding `RETENTION_S` was the obvious spelling and it was wrong: it read as the
        server's policy while `purge_expired` consulted the store's, so the two could disagree
        without anything noticing. Derived, they cannot -- and check 1.11's drift guard now has one
        object to watch instead of choosing between three.
        """
        return self.store.retention_s

    # -- handles ------------------------------------------------------------------------------

    def locate(self, name: str, root: str | None) -> Path:
        """The project directory `name` refers to, contained and resolved. Raises otherwise.

        *"A project name is a name, not a capability. It is checked against the root policy on every
        call, never concatenated into a path unchecked -- a `project` argument of `../../etc` is the
        same bug as an unbounded absolute path"* (docs/tool-surface.md#handles-project-and-job_id).
        Two independent checks do that here: `ProjectPaths.for_project` refuses a name that is not a
        single safe path component, and `contain` refuses the result if it resolves outside every
        allowed root -- which is the check that catches a *symlinked* project directory pointing out
        of the workspace, since containment is tested after resolution.

        `root` given is the parent directory to look in (the `create` sense of the argument).
        Omitted, the configured roots are searched, which is what makes `name` alone a usable handle
        for a project this server did not create in this process.
        """
        if root is not None:
            parent = contain(root, self.roots)
            return contain(ProjectPaths.for_project(parent, name).root, self.roots)

        for parent in self.roots:
            expanded = expanded_root(parent)
            if expanded is None:  # a `~user` root with no account contains nothing
                continue
            candidate = ProjectPaths.for_project(expanded, name).root
            # `lexists`, not `is_file`: a directory or FIFO at `manifest.json` is found here, so the
            # open names what stands there (`MANIFEST_UNREADABLE`) instead of "no project named"
            # (final review of 2026-10-05).
            if os.path.lexists(candidate / MANIFEST_NAME):
                return contain(candidate, self.roots)

        if not self.roots:
            raise RootsNotConfigured(
                "No allowed roots are configured, so no project can be located by name. Set "
                "RAD_MCP_ROOTS, or pass root explicitly."
            )
        raise ProjectNotFoundError(
            f"no project named {name!r} under any configured root "
            f"({', '.join(str(r) for r in self.roots)}). Pass root to say where it lives, or "
            "create it."
        )

    def open(self, name: str, root: str | None = None) -> LoadedProject:
        """Open a project by handle, **and adopt its jobs**.

        The `recover` call is not incidental: it is the seam that makes a job id survive a restart,
        and this is one of the two places a front door has to make it (the module docstring says
        which two).
        """
        project_root = self.locate(name, root)
        loaded = open_project(project_root, radiance_version=self.launcher.version)
        self.adopt(loaded.paths.root)
        return loaded

    def adopt(self, project_root: Path) -> None:
        """Hand a project's `10_jobs/` to the store, once per runtime. See `_recovered`."""
        key = Path(project_root).resolve()
        if key in self._recovered:
            return
        # Marked *after* the call, not before: a recovery that raised would otherwise leave the root
        # recorded as done and never be retried, which is the failure this whole seam exists to
        # prevent, arrived at by bookkeeping.
        adopted = self.store.recover(key)
        self._recovered.add(key)
        if adopted:
            logger.info("adopted %d job record(s) from %s", len(adopted), key)

    # -- the two locks, in the one legal order -------------------------------------------------

    @contextmanager
    def modifying(self, paths: ProjectPaths) -> Iterator[None]:
        """Hold both manifest locks around an open-modify-save of one project.

        `save_manifest` writes the whole document with no compare-against-disk, so what loses an
        update is the *interval* between the read at t0 and the write at t1. Two writers have to be
        excluded across that whole span, and there are two of them for two different reasons:

        - `store.manifest_lock` excludes the **job worker thread** in this process. Concurrency is 1
          for jobs; tool calls are not serialised against the worker at all, so without it a
          `project(action="update")` issued mid-build is erased by the build's next step commit.
        - `rad_engine.project.manifest_lock` excludes **another process** -- a second server, or a
          CLI -- with an `flock` the kernel releases even if the holder is killed.

        **The order is thread lock first, then the file lock, and it is not a preference.**
        `JobStore._commit` takes them in that order; taking them the other way round anywhere
        completes a cycle and deadlocks. The rule is stated on
        `rad_engine.project.manifest_lock` and asserted by
        `tests/test_project.py::test_the_lock_ordering_that_prevents_deadlock_is_taken_thread_first`.
        """
        with self.store.manifest_lock, file_manifest_lock(paths):
            yield

    # -- lifecycle ----------------------------------------------------------------------------

    def startup(self) -> None:
        """Adopt every project under every configured root, then apply the retention policy.

        This is the half of recovery that `open` cannot do: a client holding a job id from before
        the restart has no reason to open the project first, and asking it to would make the handle
        conditional on knowing something the handle was supposed to encode.

        The scan is by directory name and the presence of `manifest.json` -- it deliberately does not
        parse manifests. Startup must not fail because one project of forty has a corrupt manifest,
        and `MANIFEST_UNREADABLE` is a thing to report when that project is *used*, not a reason to
        refuse to start.
        """
        for parent in self.roots:
            expanded = expanded_root(parent)
            if expanded is None:
                logger.warning("configured root names no home directory, skipping: %s", parent)
                continue
            resolved = Path(parent).expanduser()
            if not resolved.is_dir():
                logger.warning("configured root does not exist, skipping: %s", resolved)
                continue
            for child in sorted(resolved.iterdir()):
                if (child / MANIFEST_NAME).is_file():
                    self.adopt(child)
        expired = self.store.purge_expired()
        if expired:
            logger.info("purged %d expired job record(s)", len(expired))


_CURRENT: Runtime | None = None
_CURRENT_GUARD = threading.Lock()


def current() -> Runtime:
    """The running server's `Runtime`, built from the environment on first use.

    Lazy rather than built at import so that importing `rad_mcp.server` -- which
    `tests/test_protocol.py` does to introspect the tool list -- neither reads `RAD_MCP_ROOTS` nor
    starts a worker thread. Tests replace it wholesale with `set_current`.
    """
    global _CURRENT
    with _CURRENT_GUARD:
        if _CURRENT is None:
            _CURRENT = Runtime.from_env()
        return _CURRENT


def set_current(runtime: Runtime | None) -> Runtime | None:
    """Install a runtime and return the one it replaced. `None` restores lazy construction."""
    global _CURRENT
    with _CURRENT_GUARD:
        previous, _CURRENT = _CURRENT, runtime
        return previous


def refuse_engine_error(exc: Exception) -> CallToolResult:
    """Turn an engine refusal into the wire's refusal shape.

    The engine raises and never builds an envelope: *"on the wire a refusal is `{"refused": true,
    ...}` -- but that shape is the protocol layer's, and building it here would put the wire contract
    in the adapter"* (`rad_engine.project.ProjectError`). This is the other end of that arrangement,
    and it is the only place the two shapes meet.

    `ProjectError.advisory` and `JobError.advisory` return `None` where the register has no code for
    the condition. That `None` is carried through as an advisory-free refusal rather than filled in:
    codes are allocated once and never renamed, so a gap is reported, not invented
    (docs/tool-surface.md#advisory-code-register).

    **`field` is threaded through where the engine names one.** `ARGUMENT_OUT_OF_RANGE` is one code
    for the whole class of arguments refused on their face, and *"the `field` key already says which
    argument"* is the reason one code is enough. This used to pass `field=None` unconditionally for
    every engine error, so `project(action="describe", name="../etc")` reached the wire carrying a
    code that could not say what to fix. `ProjectError.field` is a class attribute, or set per
    instance where one class covers several arguments (`ScalarNotFiniteError`), and is `None` for
    every refusal that is about a state rather than an argument, so nothing else moves.

    **`GridError` (`rad_core.grids`) is handled here too, and built directly rather than through
    `to_wire`.** `to_wire` maps `rad_engine.advisories.EngineAdvisory` -> the wire `Advisory`, and
    `rad_core` -- below `rad_engine` in the layer stack -- cannot construct an `EngineAdvisory` any
    more than it can construct the wire model itself (CONTEXT.md, Layers). `GridError` already
    carries exactly the three fields `to_wire` would otherwise copy across (`code`, `severity`,
    `field`), so this builds the `Advisory` from them directly instead of manufacturing an
    intermediate `EngineAdvisory` with no producer of its own. Before this branch existed,
    `sensor_grid(standard="lm83", ...)` calling `plan_grid` and hitting
    `CellAspectOutOfRangeError` propagated the raw exception out of the tool function: no code
    reached the client, and an uncaught exception escaping a tool is a JSON-RPC error rather than
    the tool-result-channel refusal check 0.4 requires
    (docs/tool-surface.md#a-refusal-is-a-tool-execution-error-not-a-successful-result) -- fix round 1
    of Task 1's review, finding I4.
    """
    if isinstance(exc, ProjectError | JobError):
        advisory = exc.advisory
        if advisory is None:
            return refuse(str(exc))
        return refuse(str(exc), [to_wire(advisory, field=getattr(exc, "field", None))])

    if isinstance(exc, GridError):
        if exc.code is None:
            return refuse(str(exc))
        return refuse(
            str(exc),
            [Advisory(code=exc.code, severity=exc.severity, field=exc.field, message=str(exc))],
        )

    if isinstance(exc, DefaultRootNotConfigured):
        # **Not `PATH_OUTSIDE_ROOT`.** Nothing was outside anything: no path was supplied at all, and
        # the server has no configured answer for where an unqualified `create` goes. Sharing the
        # containment code would tell an operator with a correct `RAD_MCP_ROOTS` that their path
        # escaped a root, which is the wrong repair -- the same overloading argument R56 made for
        # `OCTREE_BUILD_TIMEOUT`, applied before the second meaning is minted rather than after.
        # `field="root"` because `root` is the argument whose absence is being reported.
        return refuse(
            str(exc),
            [
                Advisory(
                    code="DEFAULT_ROOT_NOT_CONFIGURED",
                    severity="error",
                    field="root",
                    message=str(exc),
                )
            ],
        )

    if isinstance(exc, CodedWriteRefusal):
        # R852, R855: a write inside the project that did not complete. Its class carries the code;
        # it is not `PATH_OUTSIDE_ROOT`, because nothing escaped a root.
        return refuse(
            str(exc),
            [Advisory(code=exc.code, severity=exc.severity, field=exc.field, message=str(exc))],  # type: ignore[arg-type]
        )

    if isinstance(exc, RootsNotConfigured | PathOutsideRoot):
        # Both are `PATH_OUTSIDE_ROOT`, and the second case is not a stretch: with no roots
        # configured, every path is outside every allowed root -- `contain` refuses rather than
        # treating empty as "allow everything" (`rad_mcp.paths`). The messages differ, which is what
        # tells an operator to set RAD_MCP_ROOTS rather than to move a file.
        return refuse(
            str(exc),
            [
                Advisory(
                    code="PATH_OUTSIDE_ROOT",
                    severity="error",
                    field="root",
                    message=str(exc),
                )
            ],
        )
    raise exc  # pragma: no cover - not a refusal; let it surface as a tool error
