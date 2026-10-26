"""One inline Radiance child in its own session, and how its group is killed (Phase 17, R750).

**Why a session.** `rcontrib` with `-n` > 1, when it fails or is sent SIGINT, SIGHUP or SIGTERM
after forking its workers, calls `end_children(1)` in `rt/rc3.c`, which runs
`kill(kidpr[nchild].pid, SIGKILL)` -- an index one past its last worker, whose pid is 0, so the
kill reaches **every process in its process group** (design M2a; measured M1, M2b, M2c). A child
spawned in the server's own group takes the server with it (M5c). In its own session the same kill
stays inside the child's group. It does not contain the `kill(-1, ...)` case M2a also reads: a pipe
or fork failure while starting a worker leaves the pid at -1, and that kill reaches every process
the user owns.

**Why a group kill on a timeout.** `subprocess.run(timeout=...)` kills only its direct child, so
genBSDF's `rfluxmtx` and `rcontrib`, or falsecolor's `pcomb`, ran on after the call had returned
(M4a, M6). A new session alone changes nothing there (M4b); `kill_group` on the session does (M4c).

**The registry and the stop flag** let `rad_mcp.stop_signals` kill every live inline child when the
server is stopped: the child shares no group with the server any more, so the client's own
`killpg` of the server's group no longer reaches it (M7). The helper records a child **before** it
reads the flag, and `begin_stop_sweep` sets the flag **before** it snapshots the record, so one of
the two always sees a child spawned during a stop. Nothing here takes a lock: a signal handler that
waits on a lock its own thread holds never returns (design P7).

Standard library only: `rad_engine.run` imports this module, and this module must not import
`rad_engine.run` (or anything that reaches pydantic) back.
"""

from __future__ import annotations

import logging
import os
import signal
import subprocess
import threading
import time
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import IO, Any, Final

__all__ = [
    "ChildCollector",
    "DEFAULT_KILL_GRACE_S",
    "SpawnStopped",
    "begin_stop_sweep",
    "collecting_children",
    "kill_group",
    "recorded_groups",
    "reset_for_tests",
    "run_in_own_session",
    "stop_has_begun",
]

_log = logging.getLogger(__name__)

#: SIGTERM first, then SIGKILL after this **again** -- so the worst case is *two* grace windows, not
#: one, and check 1.7's 2 s budget has to hold at 2 x grace plus the bookkeeping. At 1.0 s that
#: arithmetic came out at exactly the budget with nothing left over, which is a check that passes
#: until the machine is busy. 0.5 s puts the worst case at 1.0 s. It is not a knob for letting a
#: long render finish: a Radiance binary either dies on SIGTERM at once or does not die on it.
DEFAULT_KILL_GRACE_S: Final = 0.5

def kill_group(
    pgid: int, *, proc: subprocess.Popen[bytes] | None = None, grace_s: float
) -> bool:
    """SIGTERM a process group, then SIGKILL it. POSIX only. Returns whether it was signalled.

    The return value exists so recovery can say *"still running, and I could not stop it"* rather
    than claim a kill it did not perform: `killpg` can return `EPERM` for a group this process no
    longer has the right to signal, and a message asserting the orphan is gone would be worse than
    no message at all.

    Separated from `_terminate_tree` because recovery needs it too: a server killed hard leaves a
    live group behind with no `Popen` object anywhere to wait on (see `JobStore._reap_orphan`).

    **The direct child exiting says nothing about its children**, and a grandchild that ignored
    SIGTERM is exactly the orphan case check 1.7 forbids -- so the group is swept with SIGKILL even
    when the child went quietly. The sweep is guarded by `killpg(pgid, 0)`, which asks whether the
    group still has members: once `proc.wait()` has reaped the leader, the pid is free to be reused,
    and signalling it unconditionally is signalling whatever got there next.

    **Only that final SIGKILL sweep is guarded.** The first SIGTERM goes to `pgid` with no
    membership check, and since ruling R757 `rad_engine.jobs._terminate_tree` calls this after it
    has reaped the leader, so that SIGTERM reaches whatever group then holds the id (`killpg`
    reaches a pgid only while some process is a member of that group; R757 final review).
    """
    signalled = False
    for sig, wait_s in ((signal.SIGTERM, grace_s), (signal.SIGKILL, grace_s)):
        try:
            os.killpg(pgid, sig)
            signalled = True
        except ProcessLookupError:
            return signalled
        except PermissionError:  # pragma: no cover - the group outlived our right to signal it
            _log.warning("not permitted to signal process group %s", pgid)
            return signalled
        if proc is None:
            time.sleep(wait_s)
            continue
        try:
            proc.wait(timeout=wait_s)
            break
        except subprocess.TimeoutExpired:
            continue

    try:
        os.killpg(pgid, 0)  # does the group still hold anyone? raises if not
    except (ProcessLookupError, PermissionError):
        return signalled
    try:
        os.killpg(pgid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):  # pragma: no cover - lost between the two calls
        pass
    return signalled


class SpawnStopped(RuntimeError):
    """A spawn refused because the server's stop had begun when `run_in_own_session` read the stop
    flag, just after recording its child: that child had already started, and was killed with its
    whole group before this was raised.

    `rad_engine.run` and `rad_engine.postprocess` translate it to
    `rad_engine.run.RadianceStoppedError`, a `RadianceError`, at each call site.
    """


#: Process-group ids of the inline children alive right now. A plain set: `add`, `discard` and
#: `.copy()` of it are each one C call under the GIL, which is all a signal handler may rely on.
#: (`tuple(...)` is not: it iterates, and a collection run by its allocation can let another thread
#: change the set's size mid-iteration -- read in CPython 3.11's source, not measured.)
_recorded: set[int] = set()

#: Set once by `begin_stop_sweep`; read by `run_in_own_session` after it records a child.
_stopping = False


def stop_has_begun() -> bool:
    """Whether `begin_stop_sweep` has run: the process is stopping (a stop signal arrived)."""
    return _stopping


def recorded_groups() -> tuple[int, ...]:
    """A snapshot of the live inline children's group ids."""
    return tuple(_recorded.copy())


def begin_stop_sweep() -> None:
    """Set the stop flag, then SIGKILL every recorded group -- once; later calls do nothing.

    Runs inside a signal handler: no lock, no sleep, no wait. Each `killpg` is guarded on its own,
    because on macOS a group whose only member is an unreaped zombie answers `EPERM` (design P8),
    and one failure must not stop the sweep.
    """
    global _stopping
    if _stopping:
        return
    _stopping = True
    for pgid in _recorded.copy():
        try:
            os.killpg(pgid, signal.SIGKILL)
        except OSError:
            pass


class ChildCollector:
    """The inline children one caller started, so that caller alone can be stopped (final review of
    2026-10-05): `JobStore`'s ASE occlusion probe spawns up to four children from a job's commit,
    each bounded only by its own 300 s timeout, and `cancel` reached none of them. A child spawned
    under `collecting_children(collector)` is recorded in `pids` **before** `stopping` is read, and
    `stop` sets `stopping` **before** it snapshots `pids` -- the same order as the server-wide stop
    above -- so a child is either killed by `stop` or refused at once. No lock, as above."""

    def __init__(self) -> None:
        self.pids: set[int] = set()
        self.stopping = False
        self.signalled = False
        """Whether a child of this collector was sent a kill because of a stop: by `stop`, by a
        spawn refused at once after it or after the server-wide stop, or -- read after the fact --
        a child that ended by a signal once `begin_stop_sweep` had run (Phase 24 final review: the
        job's end reason says whether the occlusion probe had a process to kill, as a step's
        does)."""

    def stop(self, *, grace_s: float = DEFAULT_KILL_GRACE_S) -> None:
        """Refuse every later spawn under this collector, and kill each recorded child's group."""
        self.stopping = True
        held = self.pids.copy()
        if held:
            self.signalled = True
        for pgid in held:
            kill_group(pgid, grace_s=grace_s)


#: The collectors the current thread spawns under (`collecting_children`), innermost last.
_local = threading.local()


@contextmanager
def collecting_children(collector: ChildCollector) -> Iterator[ChildCollector]:
    """Record every inline child this thread spawns inside the block in `collector`."""
    stack = getattr(_local, "collectors", None)
    if stack is None:
        stack = _local.collectors = []
    stack.append(collector)
    try:
        yield collector
    finally:
        stack.remove(collector)


def reset_for_tests() -> None:
    """Clear the registry and the stop flag. Tests only: a real server never un-stops."""
    global _stopping
    _stopping = False
    _recorded.clear()


def _kill_tree(proc: subprocess.Popen[bytes]) -> None:
    """Kill `proc`'s whole group (POSIX) or `proc` itself (Windows, as `subprocess.run` does).

    POSIX: `poll()` first, so a leader that already exited is reaped and an empty group answers
    `ProcessLookupError` rather than macOS's `EPERM`; then `kill_group` **whether or not the
    leader has exited** -- a grandchild holding a pipe outlives it (`jobs._terminate_tree`'s early
    return would skip it). The pgid is `proc.pid`, never `os.getpgid`, which races the reaper.
    """
    if os.name == "nt":  # pragma: no cover - no Windows here
        proc.kill()
        return
    proc.poll()
    try:
        kill_group(proc.pid, proc=proc, grace_s=DEFAULT_KILL_GRACE_S)
    except Exception:  # pragma: no cover - logged, never allowed to replace the caller's exception
        _log.exception("killing inline process group %s failed", proc.pid)


def _close_pipes(proc: subprocess.Popen[bytes]) -> None:
    for stream in (proc.stdin, proc.stdout, proc.stderr):
        if stream is not None:
            try:
                stream.close()
            except OSError:  # pragma: no cover - a pipe already closed by communicate
                pass


def run_in_own_session(
    argv: Sequence[str],
    *,
    cwd: Path | str,
    env: Mapping[str, str],
    stdout: Any,
    timeout: float | None,
    input: bytes | None = None,
    stdin: IO[bytes] | None = None,
) -> tuple[int, bytes, bytes]:
    """Run `argv` in its own session; return `(returncode, stdout_bytes, stderr_bytes)`.

    Exactly one of `input` (bytes; `b""` is a declared EOF) and `stdin` (an open binary file) must
    be given: stdin on stdio is the JSON-RPC request stream, and `None` would inherit it. `stdout` is
    `subprocess.PIPE` or a caller-owned file (then `stdout_bytes` is `b""`); stderr is always piped.
    Bytes only: CPython decodes text output after it has reaped the child (design P10), so a
    decoding error would land inside the kill window after a normal exit.

    **The kill window** runs from `Popen` returning until `communicate` returns or raises. On
    `TimeoutExpired` or any other exception the child's whole group is killed (`_kill_tree`) and
    the same exception object is re-raised; a `TimeoutExpired` carries what `communicate` had
    collected. After a normal exit, zero or not, nothing is signalled. `communicate(timeout=...)` is
    the call `subprocess.run` makes, so `rad_mcp.timeouts`' measured boundary (R704) is unchanged.

    Raises `SpawnStopped` if the server's stop had begun when this call read the stop flag, just
    after recording its child; the child is killed with its group first.

    Raises `ValueError`, before any spawn, if `stdout` is `None`: that would inherit the server's
    stdout, which on stdio is the JSON-RPC response stream.
    """
    if (input is None) == (stdin is None):
        raise ValueError(
            "run_in_own_session needs exactly one of input (bytes) or stdin (an open file): stdin on "
            "stdio is the JSON-RPC request stream, and leaving both out would inherit it."
        )
    if stdout is None:
        raise ValueError(
            "run_in_own_session needs stdout as subprocess.PIPE or an open file: stdout on stdio "
            "is the JSON-RPC response stream, and None would inherit it."
        )
    posix = os.name != "nt"  # Windows keeps today's behaviour: no new group, kill the child only
    collectors = tuple(getattr(_local, "collectors", ()))
    proc = subprocess.Popen(
        list(argv),
        cwd=str(cwd),
        env=dict(env),
        stdin=subprocess.PIPE if input is not None else stdin,
        stdout=stdout,
        stderr=subprocess.PIPE,
        start_new_session=posix,
    )
    completed = False
    try:
        if posix:
            _recorded.add(proc.pid)  # record FIRST, then read the flag: see the module docstring
            for collector in collectors:
                collector.pids.add(proc.pid)  # the same order for a caller's own stop
            if _stopping:
                for collector in collectors:
                    collector.signalled = True
                _kill_tree(proc)
                _close_pipes(proc)
                raise SpawnStopped(f"{argv[0]} was stopped at once: the server is stopping.")
            stopped_by = [collector for collector in collectors if collector.stopping]
            if stopped_by:
                for collector in stopped_by:
                    collector.signalled = True
                _kill_tree(proc)
                _close_pipes(proc)
                raise SpawnStopped(f"{argv[0]} was stopped at once: the job that started it was cancelled.")
        try:
            out, err = proc.communicate(input, timeout=timeout)
            completed = True
            if _stopping and proc.returncode is not None and proc.returncode < 0:
                # Ended by a signal while the server's stop was in progress: the sweep's kill, as
                # far as this process can tell. A caller's collector words its stop from this.
                for collector in collectors:
                    collector.signalled = True
        except subprocess.TimeoutExpired as exc:
            _kill_tree(proc)
            if not posix:  # pragma: no cover - subprocess.run collects it this way on Windows
                exc.stdout, exc.stderr = proc.communicate()
            raise
        except BaseException:
            _kill_tree(proc)
            raise
    finally:
        _recorded.discard(proc.pid)  # only after any kill above has returned
        for collector in collectors:
            collector.pids.discard(proc.pid)
        if not completed:
            _close_pipes(proc)
            proc.wait()  # unbounded, as in subprocess.run's own exit path
    return proc.returncode, out or b"", err or b""
