"""Invoking Radiance binaries.

One rule, and it is the reason this module exists: **stderr is never merged into stdout.**

Radiance writes data to stdout and warnings to stderr. Merging them corrupts the data stream, and
if the target is a file it corrupts the artifact. Most of the engine traps in CONTEXT.md are only
detectable by reading stderr on a process that exited 0.

**And stdin is declared on every spawn, including the ones that take none.** `subprocess.run(...,
input=None)` does not close the child's stdin -- it leaves it *inherited*, which on stdio is the
JSON-RPC request stream. Verified empirically on 2026-08-01: with no ``stdin`` argument the child
read the parent's own stdin, line for line. So every spawn below declares ``input=`` -- bytes, and
``b""`` when there is nothing to send -- rather than leaving stdin to be inherited: an empty input
is an empty write followed by a close, which a Radiance binary reading stdin sees as EOF, and
``input=`` is a keyword check 0.2's AST guard can see. Same failure as the stdout rule, one file
descriptor over, arriving as a hang instead of as corruption.

**And the third stream, the one `run_to_file` opens itself: its sink goes through
`safe_io.open_regular_file`, never `out_path.open("wb")`** (ruling R48). That open was unguarded
until 2026-08-09 and the module was not the reason -- `run_to_file` was then byte-identical to the
version that had shipped on `master`, where its only caller sat *below* a `dry_run` branch and was
unreachable without a submitted job. `run_annual` hoists `build_octree` **above** its `dry_run`
branch, correctly and deliberately (R44(3)/R39, so octree refusals are reachable on a dry run), and
that hoist made this open reachable on a call that submits nothing and returns success. Two measured
consequences, both on the octree destination `annual_<grid>.oct` whose `<grid>` the caller supplies
-- so the attacker chooses *which* file in that directory is written, the same targetability that
ranked `sensors.write_points` first:

- A symlink at the destination overwrote a file outside every allowed root with
  `#?RADIANCE / oconv -f s.rad / FORMAT=Radiance_octree`, while this function returned
  `returncode: 0`.
- A FIFO at the destination -- planted *inside* an allowed root, so containment legitimately passes
  and only `O_NONBLOCK`/`S_ISREG` can refuse it -- blocked in `open()` forever. Measured: `SIGALRM`
  in the main thread could not interrupt an `anyio` worker parked there, and the process needed
  `kill -9`. The worker pool's limiter is 40, so forty such paths stall every tool the server offers.

Containment of the *parent chain* is not this function's to do -- it has no roots and inventing a
path policy here would be settling an open owner decision. That guard belongs to the caller, and
`rad_mcp.annual_tool` applies `resolved_parent_within` before the `mkdir`, matching how R48 left
`write_wea`/`write_points`: the engine owns the final-component and file-type guards, the tool layer
owns the parent chain, because only it holds the roots.

**And every spawn here runs in its own session** (ruling R750), through
`rad_engine.process_group.run_in_own_session`: a multi-process `rcontrib` that fails or is
signalled kills its whole process group, which was the server's own (design M1, M5c); and a
timeout now kills the child's whole tree, where `subprocess.run` killed only the direct child.
"""

from __future__ import annotations

import io
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import IO

from .env import RadianceEnv
from .process_group import SpawnStopped, run_in_own_session
from .safe_io import open_regular_file

__all__ = [
    "Result",
    "RadianceError",
    "RadianceStoppedError",
    "RadianceTimeoutError",
    "run",
    "run_to_file",
    "run_to_sink",
]


class RadianceError(RuntimeError):
    """A Radiance binary failed. Carries stderr, which is where Radiance explains itself."""

    def __init__(self, argv: list[str], returncode: int, stderr: str):
        self.argv = argv
        self.returncode = returncode
        self.stderr = stderr
        detail = stderr.strip() or "(no stderr)"
        super().__init__(f"{argv[0]} exited {returncode}: {detail}")


class RadianceTimeoutError(RadianceError):
    """A Radiance binary exceeded the caller's `timeout` and was killed.

    **This type exists so the timeout stays behind the layer boundary.** `Popen.communicate`, inside
    `rad_engine.process_group.run_in_own_session` (R750), signals an expiry with
    `subprocess.TimeoutExpired`, and a tool in `rad_mcp` that wanted to shape that into
    a coded refusal would have to `import subprocess` to name it -- which
    `tests/test_purity.py::TestTheProtocolLayerSpawnsNothing` refuses, correctly: *"rad_mcp is thin
    -- no subprocess calls. Running a binary is rad_engine's job."* (CONTEXT.md, Layers). Spawning
    is this layer's knowledge, so the vocabulary for a spawn that ran too long is this layer's too.

    A subclass of `RadianceError` rather than a sibling, because every existing caller that already
    handles "the binary failed" should keep handling this without being edited -- a timeout *is* a
    failed invocation. A caller that wants to tell them apart catches this first, which is exactly
    what `run_annual` does to name the bound in its refusal.

    `returncode` is `-1`: the process was killed rather than exiting, so there is no status to
    report, and a real status is never negative.
    """

    def __init__(self, argv: list[str], timeout: float, stderr: str = ""):
        self.timeout = timeout
        super().__init__(argv, -1, stderr)
        detail = f" Radiance said: {stderr.strip()}" if stderr.strip() else ""
        self.args = (
            f"{argv[0]} exceeded its {timeout:g} s bound and was terminated.{detail}",
        )


class RadianceStoppedError(RadianceError):
    """A Radiance step refused because the server is stopping (R750); a child it had started was
    killed with its whole group. Translated from `rad_engine.process_group.SpawnStopped`, which
    cannot name this class without importing this module."""

    def __init__(self, argv: list[str], reason: str | None = None):
        super().__init__(argv, -1, "")
        # The `SpawnStopped` message when one is passed: a job's own stop (`ChildCollector`) says
        # its job was cancelled, not that the server is stopping.
        self.args = (reason or f"{argv[0]} was stopped at once: the server is stopping.",)


def _text_encoding() -> str:
    """The encoding `subprocess.Popen(text=True)` chooses for its pipes in this process: a
    `TextIOWrapper` given no encoding (the locale's preferred encoding, or UTF-8 in UTF-8 mode)."""
    return io.TextIOWrapper(io.BytesIO()).encoding


def _decode_text(data: bytes, encoding: str) -> str:
    """Exactly what Popen's text mode does to captured output: strict decode, then `\\r\\n` and `\\r`
    to `\\n` (`subprocess.Popen._translate_newlines`)."""
    return data.decode(encoding, "strict").replace("\r\n", "\n").replace("\r", "\n")


@dataclass(frozen=True)
class Result:
    """Outcome of one Radiance invocation."""

    argv: list[str]
    returncode: int
    stdout: str
    stderr: str
    cwd: Path

    @property
    def warnings(self) -> list[str]:
        """Non-fatal lines Radiance wrote to stderr.

        A Radiance process that exits 0 having written warnings is the single most common way to
        get a confidently wrong answer -- an octree whose every ``!xform`` include failed still
        exits 0. Callers should surface these, not discard them.
        """
        return [ln.strip() for ln in self.stderr.splitlines() if ln.strip()]


def run(
    env: RadianceEnv,
    argv: list[str],
    *,
    cwd: Path | str,
    stdin: str | None = None,
    stdin_bytes: bytes | None = None,
    timeout: float | None = None,
    check: bool = True,
) -> Result:
    """Run a Radiance binary and capture stdout and stderr **separately**.

    ``cwd`` is required, not optional. Radiance resolves relative scene paths and ``!xform``
    includes against the process working directory, and a non-frozen octree stores relative paths
    to its sources -- so the caller must always be explicit about where the command runs. Defaulting
    to the server's own cwd is how you end up reading the wrong file.

    ``stdin_bytes`` feeds the child raw bytes rather than a `str` -- for `rad_engine.convert`, where
    a converter must read exactly the bytes the server digested (Phase 10 design §5.2), not a
    re-encoding of them. Passing both ``stdin`` and ``stdin_bytes`` is a programming error, refused
    before any process is spawned. With ``stdin_bytes``, stdout is decoded UTF-8 with
    `surrogateescape` -- so a non-UTF-8 byte a converter's output carries round-trips losslessly --
    and stderr with `replace`, because stderr is prose read by a human or a classifier, never
    round-tripped byte for byte.
    """
    if stdin is not None and stdin_bytes is not None:
        raise ValueError("pass stdin or stdin_bytes, not both")

    cwd = Path(cwd)
    if not cwd.is_dir():
        raise NotADirectoryError(f"cwd for Radiance must be an existing directory: {cwd}")

    binary, *args = argv
    resolved = [str(env.path(binary)), *args]

    # `TimeoutExpired` is translated here, at the spawn, so a timeout is reported in this layer's
    # vocabulary (`RadianceTimeoutError`) rather than `subprocess`'s. A tool in `rad_mcp` cannot
    # name `subprocess.TimeoutExpired` without importing `subprocess`, which the purity guard
    # refuses -- and correctly, since spawning is this layer's knowledge.
    try:
        if stdin_bytes is not None:
            proc_returncode, raw_out, raw_err = run_in_own_session(
                resolved, cwd=cwd, env=env.environ(cwd), stdout=subprocess.PIPE,
                timeout=timeout, input=stdin_bytes,
            )
            proc_stdout = raw_out.decode("utf-8", "surrogateescape")
            proc_stderr = raw_err.decode("utf-8", "replace")
        else:
            # Text in and out, as `subprocess.run(text=True)` did, but decoded *after* the helper
            # returns: CPython decodes after reaping the child, so a decode error inside the helper
            # would fall in its kill window after a normal exit (design P10). "" and not None: None
            # inherits the parent's stdin -- see the module docstring.
            encoding = _text_encoding()
            payload = (stdin if stdin is not None else "").encode(encoding)
            proc_returncode, raw_out, raw_err = run_in_own_session(
                resolved, cwd=cwd, env=env.environ(cwd), stdout=subprocess.PIPE,
                timeout=timeout, input=payload,
            )
            proc_stdout = _decode_text(raw_out, encoding)
            proc_stderr = _decode_text(raw_err, encoding)
    except subprocess.TimeoutExpired as exc:
        # `exc.stderr` is whatever the binary managed to write before it was killed -- often the
        # warning that explains the hang, so it is carried rather than dropped.
        stderr = exc.stderr or ""
        if isinstance(stderr, bytes):
            stderr = stderr.decode(errors="replace")
        raise RadianceTimeoutError(resolved, timeout or 0.0, stderr) from exc
    except SpawnStopped as exc:
        raise RadianceStoppedError(resolved, str(exc)) from exc
    result = Result(
        argv=resolved, returncode=proc_returncode, stdout=proc_stdout,
        stderr=proc_stderr, cwd=cwd,
    )
    if check and proc_returncode != 0:
        raise RadianceError(resolved, proc_returncode, proc_stderr)
    return result


def run_to_sink(
    env: RadianceEnv,
    argv: list[str],
    *,
    stdout: IO[bytes],
    cwd: Path | str,
    stdin: str | None = None,
    timeout: float | None = None,
    check: bool = True,
) -> Result:
    """Run a Radiance binary with stdout going to `stdout`, a binary file the caller opened and
    closes, and stderr kept in memory. `run_to_file`'s spawn, for a caller whose output file is not
    opened with `open_regular_file(..., "wb")` -- a staging file, which S1 requires be opened with
    `safe_io.open_unique_staging_file` (`rad_engine.bsdf.generate_bsdf`). Check 0.2's guard sees a
    call to this function only inside this module (`run_to_file`'s); it does not see
    `generate_bsdf`'s call in `rad_engine.bsdf`, whose `stdout=` is declared by hand. stdin cannot
    be inherited through this function, which always passes `input=`; and `run_in_own_session`
    refuses `stdout=None` at run time."""
    cwd = Path(cwd)
    if not cwd.is_dir():
        raise NotADirectoryError(f"cwd for Radiance must be an existing directory: {cwd}")

    binary, *args = argv
    resolved = [str(env.path(binary)), *args]

    try:
        returncode, _, raw_err = run_in_own_session(
            resolved, cwd=cwd, env=env.environ(cwd), stdout=stdout, timeout=timeout,
            # b"" and not None: None inherits the parent's stdin -- see the module docstring.
            # `surrogateescape`: the inverse of `run(stdin_bytes=...)`'s decode (ruling R450).
            input=stdin.encode("utf-8", "surrogateescape") if stdin is not None else b"",
        )
    except subprocess.TimeoutExpired as exc:
        stderr = exc.stderr or b""
        if isinstance(stderr, bytes):
            stderr = stderr.decode(errors="replace")
        raise RadianceTimeoutError(resolved, timeout or 0.0, stderr) from exc
    except SpawnStopped as exc:
        raise RadianceStoppedError(resolved, str(exc)) from exc
    result = Result(
        argv=resolved, returncode=returncode, stdout="",
        stderr=raw_err.decode(errors="replace"), cwd=cwd,
    )
    if check and returncode != 0:
        raise RadianceError(resolved, returncode, result.stderr)
    return result


def run_to_file(
    env: RadianceEnv,
    argv: list[str],
    *,
    out_path: Path | str,
    cwd: Path | str,
    stdin: str | None = None,
    timeout: float | None = None,
    check: bool = True,
) -> Result:
    """Run a Radiance binary, writing stdout to ``out_path`` and keeping stderr in memory.

    Use this for binary output (octrees, HDR images, matrices). ``Result.stdout`` is empty; the
    payload is the file. stderr still comes back so warnings remain visible -- writing
    ``> file 2>&1`` is what corrupts an octree with a warning message.

    ``out_path`` is opened with `safe_io.open_regular_file`, which refuses a symlink at the final
    component (`O_NOFOLLOW`) and anything that is not a regular file (`S_ISREG` on the **opened
    descriptor**, so a FIFO swapped in after a `stat` cannot wedge the worker). See the module
    docstring for the two measured breaches that guard closes, and for why the parent chain is the
    caller's containment to check rather than this function's.
    """
    cwd = Path(cwd)
    if not cwd.is_dir():
        raise NotADirectoryError(f"cwd for Radiance must be an existing directory: {cwd}")

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    env.path(argv[0])  # an absent binary is refused before the output file is opened, as before

    with open_regular_file(out_path, "wb") as sink:
        return run_to_sink(
            env, argv, stdout=sink, cwd=cwd, stdin=stdin, timeout=timeout, check=check
        )
