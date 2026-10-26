"""Filesystem opens that refuse anything which is not a regular file.

**Why this module exists rather than a helper inside one caller.** The guard below was written for
`rad_engine.jobs` (ruling R45), for a declared step stream. A Phase 4 security pass then found the
identical hazard one module over, on the *write* side: `rad_engine.weather.write_wea` used
`Path.write_text`, which follows a symlink and blocks forever on a FIFO -- so a symlink planted at a
project's own `04_skies/<name>.wea` overwrote an arbitrary file outside every allowed root, and the
tool reported `refused: true` **about damage already done**.

R45 and ruling R46 each hardened a *read* path and each was correct about that path. What both
missed is that the write path has the same threat model and had none of the same guards. So the rule
this module encodes is deliberately broader than either ruling: **every filesystem open in
`rad_engine`, in either direction, goes through this function, and containment is checked before the
syscall rather than after.** A second copy of the logic is what ruling R30 exists to forbid, and this
particular logic guards an arbitrary-file overwrite -- the worst thing to hold two versions of.

`rad_core` never calls this: that layer is pure and touches no filesystem at all.
"""

from __future__ import annotations

import errno
import hashlib
import logging
import os
import secrets
import shutil
import stat
import sys
from collections.abc import Callable
from pathlib import Path
from typing import IO, Literal

__all__ = [
    "DIGEST_BLOCK",
    "FileTooLargeError",
    "NotARegularFileError",
    "handle_digest",
    "open_regular_file",
    "open_staging_file",
    "open_unique_staging_file",
    "read_capped",
    "refuse_existing_non_regular_destination",
    "refuse_linked_components",
    "replace_file_atomically",
    "resolved_parent_within",
    "rmtree_logging_errors",
]

#: How many bytes are hashed per `read()` by `handle_digest`. A BSDF XML, an octree or an annual
#: `.ill` is not a thing to hold in memory to hash.
#:
#: **Public because `rad_engine.octree` streams its body digest in the same blocks** (it imports this
#: name rather than a private one); `handle_digest` here reads it too. It was moved here from
#: `rad_engine.project`, whose `file_digest` now delegates to `handle_digest` (ruling R30, one
#: arithmetic path). `grep -rn "DIGEST_BLOCK" src/ tests/` is the list of its readers.
DIGEST_BLOCK = 1 << 20


class NotARegularFileError(OSError):
    """A declared path is not a regular file.

    Its own class so a caller's stderr triage can tell it from a missing file: the fix for one is
    "supply the file", and for the other "that path is a device or a pipe, not data".

    Named `StepStreamNotRegularFileError` while it lived in `rad_engine.jobs` and served only step
    streams; that name is kept there as an alias, because the job supervisor's messages and tests
    speak in steps and a weather file is not one.
    """


def open_regular_file(path: Path, mode: Literal["rb", "wb", "ab", "xb"] = "rb") -> IO[bytes]:
    """Open `path`, refusing anything that is not a regular file. **Never** `path.open(mode)`.

    **The blocking-open hazard.** `open()` on a FIFO **blocks until the other end is attached** -- in
    *both* directions, measured on this machine: `rb`, `wb` and `ab` each hang indefinitely on a FIFO
    with no peer. In the job supervisor that open runs before `_spawn`, so `Step.timeout_s` --
    documented as "a hard bound for a step that must not hang the queue" -- has not been armed yet
    and cannot bound it; and `JobStore._process` is assigned only *after* `_spawn` returns, so a job
    wedged here has no process for `cancel` to terminate: `cancel` finds `None`, kills nothing, and
    waits out its full grace window while the job stays `running`. With a single worker thread the
    whole queue is stranded behind it, permanently and unkillably. In a synchronous tool the same
    open wedges an `anyio` worker thread, and that pool's default limiter is 40 -- so forty such
    paths stall **every** tool the server offers, not merely the one that was called. `os.mkfifo`
    inside a project directory is enough to trigger either; no escape from an allowed root is needed.

    Three guards, and each closes a distinct hole:

    - `O_NONBLOCK` on the open, cleared immediately after. This is what makes the FIFO case
      *return* instead of hanging -- `ENXIO` for a write-side FIFO with no reader, and an
      immediately-readable fd for the read side. It is cleared before the handle is used because a
      non-blocking fd handed to a child changes how that child's own reads behave, which is not a
      property this function may quietly impose on Radiance.
    - `O_NOFOLLOW`, so the **final** component may not be a symlink. This is the guard whose absence
      on the write side was the Critical: `Path.write_text` follows one silently. See
      `resolved_parent_within` for the intermediate components, which `O_NOFOLLOW` does *not* cover.
    - `S_ISREG` on the **opened descriptor**, never on the path. Checking the path and then opening
      it is a TOCTOU: a file that passes `path.stat()` can be swapped for a FIFO before
      `read_bytes()` runs, and the read then blocks forever. `os.fstat` interrogates the object
      already open, so nothing can be swapped underneath it. This is also what rejects `/dev/zero`
      (an infinite byte stream a reader would consume forever) and `/dev/stdin` (on stdio, the
      JSON-RPC request stream -- the exact inheritance ruling R43 added `Step.stdin_path` to
      prevent, otherwise reachable straight *through* it).

    `"xb"` is the exclusive-create write (`O_CREAT | O_EXCL`, no `O_TRUNC`): it fails with
    `FileExistsError` if **anything** is at `path`, a hardlink to someone else's file included, so it
    can only ever write a file this call created. Staging files use it through `open_staging_file`.

    Raising is deliberate and preserves R43's posture: a declared stream that cannot be honoured
    **fails**. Falling back to `DEVNULL` would hide a bad path behind an empty matrix -- a wrong
    number instead of an error -- and falling back to an inherited fd would be the defect the field
    exists to avoid.
    """
    flags = os.O_NOFOLLOW | os.O_NONBLOCK
    flags |= os.O_RDONLY if mode == "rb" else os.O_WRONLY | os.O_CREAT
    flags |= {"ab": os.O_APPEND, "wb": os.O_TRUNC, "xb": os.O_EXCL}.get(mode, 0)
    fd = os.open(path, flags, 0o644)
    try:
        stat_result = os.fstat(fd)
        if not stat.S_ISREG(stat_result.st_mode):
            raise NotARegularFileError(
                f"{str(path)!r} is not a regular file. A declared stream must be a regular file on "
                "disk: a FIFO blocks before any timeout is armed, and a character device is an "
                "unbounded stream. Point it at a file on disk."
            )
        # Clear O_NONBLOCK: it exists only to stop the *open* from hanging, and a non-blocking
        # descriptor inherited by a Radiance binary would change that binary's own IO semantics.
        os.set_blocking(fd, True)
        # **`fdopen` is inside this `try` too** (whole-branch review, m1). It was outside until this
        # fix, so a raise from it -- `OSError`, `MemoryError` under pressure -- left `fd` open with
        # nothing left holding a reference to close it: every other exit from this function either
        # returns the wrapping file object (which owns the fd) or raises through this clause (which
        # closes it). No caller-controlled input was found that reaches this raise; it is defence in
        # depth on the one function every filesystem open in `rad_engine` goes through.
        return os.fdopen(fd, "wb" if mode == "xb" else mode)
    except BaseException:
        os.close(fd)
        raise


def open_staging_file(path: Path) -> IO[bytes]:
    """Create a staging file (`<name>.partial`) for writing -- **every** `.partial` write goes
    through this, never `open_regular_file(..., "wb")` (S1, whole-branch fix wave, Phase 11a).

    **The hazard.** `"wb"` is `O_CREAT | O_TRUNC`: a *regular* file at the staging name passes
    `O_NOFOLLOW` and the `S_ISREG` check, and if it is a **hardlink** to a file elsewhere -- outside
    every root, even -- the truncate and the write land on that file's inode, under all of its names.
    Measured by the security review on `luminaire_tool` (R526 unlinked first, but a re-plant between
    the unlink and the open still won) and on `blend`, `schedule` and `geometry_tool`, which did not
    unlink at all.

    **The fix is two steps, and each closes one half.** A stale *regular* file at `path` is unlinked
    first -- that removes this *name* only; a hardlinked inode keeps its bytes and its other names. The
    open is then `"xb"` (`O_CREAT | O_EXCL | O_NOFOLLOW`), which fails with `FileExistsError` if
    anything was planted there in between, so the only file this can ever write is one it created.

    Anything at `path` that is **not** a regular file (a symlink, live or dangling; a directory; a
    FIFO) is refused with `NotARegularFileError` and left exactly where it is: this function removes
    only what could be a stale staging file of ours, never something whose meaning it cannot know.
    """
    try:
        existing = path.lstat().st_mode
    except FileNotFoundError:
        pass
    else:
        if not stat.S_ISREG(existing):
            raise NotARegularFileError(
                f"{str(path)!r} exists and is not a regular file, so it cannot be used as a staging "
                "file. Remove whatever occupies that path and re-run."
            )
        path.unlink()
    return open_regular_file(path, "xb")


def open_unique_staging_file(destination: Path) -> tuple[Path, IO[bytes]]:
    """Create a staging file for `destination` under a name **this call alone** holds --
    `.<name>.<random>.partial` beside it -- and return the path with the open handle (R668).

    **When to use this rather than `open_staging_file`.** That one takes a fixed `<name>.partial`
    and first unlinks a stale regular file there. When two calls legitimately write the same
    destination at once, each sees the other's live staging file as stale and unlinks it: the loser
    raises `FileNotFoundError` from its `os.replace`, or `FileExistsError` from its exclusive open.
    Measured on `rad_engine.glarendx`'s one-per-project `ugr.glr`: eight concurrent writers, all
    eight raised. A name drawn per call cannot be shared, so nothing is unlinked first.

    **The S1 guarantee is unchanged, because it was always the open mode and never the name:**
    `"xb"` is `O_CREAT | O_EXCL | O_NOFOLLOW`, so whatever sits at the drawn name -- a hardlink
    included -- fails the open rather than being written through.

    **The cost** is the one a fixed name does not have: nothing cleans up after a call killed
    between this open and its rename, so a stray dot-file can be left beside `destination`. A caller
    whose remover owns its `.partial` by name (`rad_mcp.geometry_tool`) must not switch to this.
    """
    staged = destination.with_name(f".{destination.name}.{secrets.token_hex(8)}.partial")
    return staged, open_regular_file(staged, "xb")


def handle_digest(handle: IO[bytes]) -> str:
    """`sha256:` over the bytes from `handle`'s **current position** to EOF, in `DIGEST_BLOCK`
    blocks.

    **Exists so a drift check and the read it guards can share one descriptor.**
    `rad_engine.project.file_digest` opens, hashes and closes; a caller that then opens the path
    again has checked one set of bytes and used another, and a writer that swaps the file between
    the two opens passes the gate. That is `bytes_digest`'s reasoning applied to a file too large
    to hold: this hashes in blocks, so the caller pays one block of memory rather than the whole
    matrix, and still reads exactly the bytes it hashed by seeking back on the same descriptor.

    **This narrows that window; it does not close it.** A held descriptor binds the inode, so the
    file cannot be *replaced* -- renamed over, or unlinked and recreated -- between the hash and
    the caller's rewind. It does not stop an *in-place* modification of that same inode: a `pwrite`
    or an `ftruncate` in that interval still wins, and nothing here can detect it.

    **Does not seek, before or after.** The position is the caller's statement about which bytes it
    is asking about; moving it here would hash bytes the caller did not name.
    """
    digest = hashlib.sha256()
    while block := handle.read(DIGEST_BLOCK):
        digest.update(block)
    return "sha256:" + digest.hexdigest()


def refuse_existing_non_regular_destination(out_path: Path) -> None:
    """Refuse (`NotARegularFileError`) if `out_path` already exists and is not a plain regular
    file.

    `os.lstat`, never `open()`: nothing is created if `out_path` is absent, and a pre-existing
    regular file is never touched by the check itself -- only read. `stat.S_ISREG` on the `lstat`
    result does **not** follow a final-component symlink, so a symlink there is refused rather
    than followed regardless of what it points at.

    **The message names no specific type.** Measured (Task 11 fix round 3): a *directory* at
    `out_path` also fails this check, and a message that enumerated "a symlink, a FIFO, or a
    device" would be naming three things that did not fire for the real case a test can produce
    most easily -- fixed by not enumerating at all.

    **Why not enumerate a longer list instead.** `S_ISREG` is a *negative* test, so what it refuses
    is not a set this docstring can close by counting: measured (fix round 4), a unix socket and a
    character device land here too, as would any other non-regular type. The old enumeration was
    wrong in kind, not merely short by one -- which is why the fix round 3 version of this
    paragraph, which replaced "three" with "four", still carried the same mistake. The caller
    cannot act differently on any of them anyway; every one is answered the same way: point `out`
    at a plain file, or remove what is there.

    **Why it lives here and not in `rad_engine.postprocess`, where it was written (ruling R333's
    reasoning, one module further out).** R333 consolidated `spawn_postprocess`'s and
    `spawn_ra_tiff`'s two copies into one function; the Phase 9 whole-branch fix wave gave the
    same guard a third caller, `rad_engine.render.spawn_render`. That module may not import
    `rad_engine.postprocess` -- `tests/test_pfilt_boundary.py::
    test_no_module_reachable_from_run_glare_imports_postprocess` asserts the whole import closure
    of `run_glare` is clear of it, and `spawn_render` is on that closure -- so the shared guard
    moved to the module every filesystem open in `rad_engine` already goes through.
    `rad_engine.postprocess._refuse_existing_non_regular_destination` is kept as an import alias,
    so that name still resolves to this function and the two spawners' own docstrings stay true.
    """
    try:
        existing_mode = out_path.lstat().st_mode
    except FileNotFoundError:
        return
    if not stat.S_ISREG(existing_mode):
        raise NotARegularFileError(
            f"{str(out_path)!r} exists and is not a regular file. Point `out` at a plain file, "
            "or remove whatever is there."
        )


class FileTooLargeError(OSError):
    """A file larger than the cap its reader declared. The caller maps it to its own field."""

    def __init__(self, path: Path, size: int, limit: int) -> None:
        super().__init__(f"{str(path)!r} is {size} bytes, over the {limit}-byte cap.")
        self.size = size
        self.limit = limit


def read_capped(path: Path, limit: int) -> bytes:
    """Every byte of a regular file, refusing one larger than `limit` before reading it.

    Opened through `open_regular_file` (no FIFO hang, no final-component symlink). The size is taken
    from the open descriptor, and at most `limit + 1` bytes are read, so a file that grows between the
    `fstat` and the read is still refused."""
    with open_regular_file(path) as handle:
        size = os.fstat(handle.fileno()).st_size
        if size > limit:
            raise FileTooLargeError(path, size, limit)
        data = handle.read(limit + 1)
    if len(data) > limit:
        raise FileTooLargeError(path, len(data), limit)
    return data


def resolved_parent_within(path: Path, roots: tuple[Path, ...]) -> bool:
    """Is `path`'s parent chain, fully resolved, inside one of `roots`?

    **`O_NOFOLLOW` guards only the final component**, which is not the whole question for a write.
    With `O_CREAT`, a *directory* symlink anywhere in the parent chain still leads the write out of
    the root -- measured: `04_skies -> /somewhere/else` places a newly created file entirely outside
    every root while the final component is not a symlink at all and `O_NOFOLLOW` never fires.

    So the caller resolves the parent and checks *that*, before creating anything. This is a
    predicate rather than a refusal because the two callers phrase their refusals differently: the
    engine raises, and the tool layer returns a `PATH_OUTSIDE_ROOT` advisory. What both must not do
    is check **after** the write, which is what made the original defect a report of a completed
    breach rather than a guard.

    An empty `roots` means "refuse everything", matching `rad_mcp.paths.contain`'s posture that no
    configured root is not permission to write anywhere.
    """
    if not roots:
        return False
    parent = path.parent.resolve()
    return any(parent == root or root in parent.parents for root in (r.resolve() for r in roots))


def rmtree_logging_errors(directory: Path, *, log: logging.Logger) -> None:
    """Remove `directory` and everything under it; an entry `shutil.rmtree` cannot remove is logged
    on `log`, with its path, `directory` and the error, and the removal carries on with the rest.
    Nothing is raised for it: the callers remove their own scratch after the work, and a leftover is
    a thing to see in the log, not a reason to fail a finished call. Moved here from
    `rad_engine.bsdf` so `rad_engine.convert.scratch_directory` uses the same remover (ruling R830)."""

    def logged(function, path, error) -> None:
        detail = error[1] if isinstance(error, tuple) else error
        log.warning("could not remove %s while removing %s: %s", path, directory, detail)

    # `onexc` from Python 3.12, where `onerror` is deprecated; `onerror` (an exc_info triple) on 3.11.
    if sys.version_info >= (3, 12):  # pragma: no cover - exercised on 3.12+ only
        shutil.rmtree(directory, onexc=logged)
    else:
        shutil.rmtree(directory, onerror=logged)


def refuse_linked_components(path: Path, *, below: Path) -> None:
    """Refuse (`NotARegularFileError`) when any component of ``path`` below ``below`` is a symbolic
    link (register N-113, ruling R851).

    ``path`` must be ``below`` joined with relative parts, both as named; only those parts are
    `lstat`-ed, never ``below`` or anything above it. So a project root that is itself reached
    through a link (an allowed root under a symlinked home) passes, and a link *inside* the project
    -- `08_results` aimed at another directory -- is refused before anything is written through it.
    Measured through `run_point_in_time` before this check: with `08_results` a link to another
    in-root directory, the results were written into that directory and the call was refused only
    afterwards, uncoded. A component that does not exist yet ends the walk: whatever `mkdir` makes
    there is a directory, not a link. The final component is checked too; what a non-link there
    must be is `refuse_existing_non_regular_destination`'s question.

    Not covered: a link swapped in after this check and before the open (the TOCTOU every
    path-based check has); the final component's open is still `O_NOFOLLOW` or a rename.
    """
    current = below
    for part in path.relative_to(below).parts:
        current = current / part
        try:
            mode = os.lstat(current).st_mode
        except FileNotFoundError:
            return
        if stat.S_ISLNK(mode):
            raise NotARegularFileError(
                f"{str(current)!r} is a symbolic link inside the project, so this server will not "
                f"write {str(path)!r} through it. Nothing has been written. Replace the link with a "
                "real directory or file and re-run."
            )


def replace_file_atomically(path: Path, write: Callable[[IO[bytes]], None]) -> None:
    """Write ``path`` by staging beside it (`open_unique_staging_file`) and `os.replace`-ing the stage
    over it -- R526's pattern -- so a **hardlink** at ``path`` is replaced, not written through, and
    its other names keep their bytes (register N-114, ruling R851). Measured before: a `"wb"` open of
    `point_in_time_<grid>.txt` hardlinked to a file outside every root truncated and rewrote that
    file. ``write`` receives the open binary handle. A failure unlinks the stage and re-raises.

    Chosen over refusing `st_nlink > 1`: a refusal needs a check before the open, which a link made
    after it defeats, and it would refuse a destination its owner hardlinked on purpose; a rename
    never writes into the old inode, whatever its link count when the rename happens.

    **The destination's mode is kept, and a read-only one is refused** (R851). An existing regular
    destination the server user may not write raises `PermissionError` (`EACCES`) before anything
    is staged, as the `"wb"` open this replaced did; an existing destination's permission bits,
    masked to `0o777` (no setuid, setgid or sticky bit), are put on the stage before the rename, so
    a `chmod +x`'d `export.sh` keeps its execute bits. Owner and group are not carried: the stage
    is the server user's."""
    keep: int | None = None
    try:
        existing = os.lstat(path)
    except FileNotFoundError:
        existing = None
    if existing is not None and stat.S_ISREG(existing.st_mode):
        if not os.access(path, os.W_OK, follow_symlinks=False):
            raise PermissionError(errno.EACCES, os.strerror(errno.EACCES), str(path))
        keep = existing.st_mode & 0o777
    partial, handle = open_unique_staging_file(path)
    try:
        with handle:
            write(handle)
        if keep is not None:
            os.chmod(partial, keep)
        os.replace(partial, path)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise
