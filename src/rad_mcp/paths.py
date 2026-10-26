"""Where the server is allowed to read and write.

MCP 2026-07-28, Tools, Security Considerations: *"Servers MUST: Validate all tool inputs ...
Implement proper access controls"*. Every path this server takes is absolute
(docs/tool-surface.md#paths) and is handed to binaries that read and write, so "absolute" without
"contained" is an arbitrary-file API with a lighting vocabulary.

**The policy was settled 2026-08-10 (ruling R57): client-chosen, with a configured default root.**
A project lives wherever the caller says, bounded by the union of the roots the client declares and
the configured default. This module holds both halves, and they are deliberately different
questions:

- ``roots_from_env`` answers *what may be touched at all* -- the containment set, still
  ``RAD_MCP_ROOTS``, still refuse-everything when empty.
- ``default_root_from_env`` answers *where a project lands when the caller names no ``root``* --
  ``RAD_MCP_DEFAULT_ROOT``, and it is a **member** of the containment set rather than a widening of
  it. Setting it does not grant access; it picks one already-allowed directory as the landing site.

That split is the whole ruling. Conflating them would make the default a back door: a server with
``RAD_MCP_ROOTS`` unset and a default configured would otherwise accept writes to a directory no
containment set names. `default_root_from_env` therefore returns a path and `contain` still judges
it, every call, with no exemption.

A Radiance **install** directory is not a data path. `radiance_status(bin_dir=...)` names an
executable location, which is legitimately outside every data root -- confining it would refuse
`/usr/local/radiance/bin`. Only paths the server reads scene data from or writes artifacts to pass
through `contain`.
"""

from __future__ import annotations

import os
import stat
from collections.abc import Sequence
from pathlib import Path

from rad_engine.safe_io import resolved_parent_within

__all__ = [
    "DEFAULT_ROOT_ENV_VAR",
    "DefaultRootNotConfigured",
    "PathOutsideRoot",
    "ROOTS_ENV_VAR",
    "RootsNotConfigured",
    "contain",
    "contain_octree_destination",
    "default_root_from_env",
    "roots_from_env",
    "contain_as_named",
    "expand_user",
    "expanded_root",
    "CodedWriteRefusal",
    "ProjectWriteFailed",
    "SensorGridFilesReplaced",
    "write_failure",
]

ROOTS_ENV_VAR = "RAD_MCP_ROOTS"
DEFAULT_ROOT_ENV_VAR = "RAD_MCP_DEFAULT_ROOT"


class PathOutsideRoot(ValueError):
    """A path argument resolved outside every allowed root."""


class CodedWriteRefusal(ValueError):
    """A write inside the project that did not complete, coded by its class rather than by
    containment: `rad_mcp.runtime.refuse_engine_error` puts `code`, `severity` and `field` on the
    wire. Not a `PathOutsideRoot`: nothing here is outside anything (R852, R855)."""

    code: str
    severity: str = "error"
    field: str | None = "project"


class ProjectWriteFailed(CodedWriteRefusal):
    """The disk or the permissions refused a write (R855): a read-only file, a full disk, a directory
    the server user may not write. `field` is `project` for a project file, and `out` for `export`'s
    destination directory. Built by `write_failure`, whose message says what is unchanged and the one
    remedy the cause gives."""

    code = "PROJECT_WRITE_FAILED"

    def __init__(self, message: str, *, field: str = "project") -> None:
        super().__init__(message)
        self.field = field


def write_failure(path: Path, exc: OSError, *, what: str, outcome: str, field: str = "project") -> ProjectWriteFailed:
    """R855: the refusal for a write the disk or the permissions refused at `path`. A read-only
    regular file there is named as the cause, with "make it writable or remove it" -- the remedy that
    works; naming the directory and the disk instead sent a caller to fix what was not broken (final
    review, measured). Anything else names the directory and the disk."""
    try:
        existing = os.lstat(path)
        read_only = stat.S_ISREG(existing.st_mode) and not os.access(path, os.W_OK, follow_symlinks=False)
    except OSError:
        read_only = False
    remedy = (f"{path} is read-only: make it writable or remove it, and re-run." if read_only else
              f"Check that {path.parent} is writable and its disk has space, and re-run.")
    return ProjectWriteFailed(f"{what} could not be written at {path}: {exc.strerror or exc}. {outcome} {remedy}",
                              field=field)


class SensorGridFilesReplaced(CodedWriteRefusal):
    """`sensor_grid` replaced some of a grid's `.pts` files and could not finish, or could not put the
    earlier ones back (R852). The message names each file and where the earlier bytes are kept."""

    code = "SENSOR_GRID_FILES_REPLACED"


class RootsNotConfigured(RuntimeError):
    """No allowed roots are configured, so no data path can be accepted."""


class DefaultRootNotConfigured(RuntimeError):
    """A project was created with no ``root`` and no default root is configured.

    Its own class, not `RootsNotConfigured`: the two have different repairs. That one means the
    server may touch nothing at all; this one means it may touch plenty but has not been told where
    an unqualified `create` should land. Collapsing them would tell a user with a perfectly good
    `RAD_MCP_ROOTS` to go and set `RAD_MCP_ROOTS`.
    """


def expand_user(text: str | Path) -> Path:
    """`Path(text).expanduser()`, or `PathOutsideRoot` when a leading `~user` names no account on
    this machine: `expanduser` raises `RuntimeError` there, which reached the wire as an uncoded
    tool error (final review of 2026-10-05, measured with `project(create, root="~nosuchuser_zz/x")`)."""
    try:
        return Path(text).expanduser()
    except RuntimeError:
        raise PathOutsideRoot(f"{str(text)!r}: '~user' names no home directory on this machine.") from None


def expanded_root(root: Path) -> Path | None:
    """A configured root, `~` expanded and resolved, or `None` when its `~user` names no account:
    such a root can contain nothing, and it must not stop every other root being judged."""
    try:
        return Path(root).expanduser().resolve()
    except RuntimeError:
        return None


def roots_from_env(environ: dict[str, str] | os._Environ[str] | None = None) -> tuple[Path, ...]:
    """Allowed roots from ``RAD_MCP_ROOTS``, a ``os.pathsep``-separated list of absolute paths.

    Returns an empty tuple when unset. Empty is *not* "allow everything" -- `contain` refuses.
    There is deliberately no built-in default: picking one would settle the open path-policy
    decision by omission.
    """
    raw = (os.environ if environ is None else environ).get(ROOTS_ENV_VAR, "")
    roots = []
    for part in raw.split(os.pathsep):
        if part.strip():
            try:
                roots.append(Path(part).expanduser())
            except RuntimeError:  # a `~user` with no account: kept as written; it contains nothing
                roots.append(Path(part))
    return tuple(roots)


def default_root_from_env(
    environ: dict[str, str] | os._Environ[str] | None = None,
) -> Path | None:
    """Where a project lands when `create` names no ``root``. ``None`` when unset.

    **Not a root in the containment sense.** The value returned here is a *landing site*, and it is
    still passed through `contain` against `roots_from_env()` before anything is created -- see this
    module's docstring for why that check is load-bearing rather than belt-and-braces. Ruling R57
    settled the policy as "client-chosen with a configured default"; the default being contained is
    what keeps the second half from silently undoing the first.

    Returned unresolved for the same reason `roots_from_env` does: resolution belongs to `contain`,
    which does it once, after `expanduser`, and is the only place the symlink semantics are stated.
    """
    raw = (os.environ if environ is None else environ).get(DEFAULT_ROOT_ENV_VAR, "").strip()
    return expand_user(raw) if raw else None


def contain(candidate: str | Path, roots: Sequence[Path]) -> Path:
    """Resolve ``candidate`` and return it, or raise if it lands outside every root.

    Containment is checked **after** resolution, so a symlink inside a root that points outside it
    is refused. That is the case this function exists for: a textual prefix check passes it.
    """
    if not roots:
        raise RootsNotConfigured(
            f"No allowed roots are configured. Set {ROOTS_ENV_VAR} to the directories this server "
            "may read and write."
        )

    text = str(candidate)
    if "\0" in text:
        raise PathOutsideRoot("Path contains a null byte.")

    path = expand_user(text)
    if not path.is_absolute():
        raise PathOutsideRoot(f"Paths must be absolute; got {text!r}.")

    resolved = path.resolve()
    for root in roots:
        root_resolved = expanded_root(root)
        if root_resolved is not None and (resolved == root_resolved or root_resolved in resolved.parents):
            return resolved

    named = ", ".join(str(expanded_root(r) or r) for r in roots)
    raise PathOutsideRoot(f"{resolved} is outside every allowed root ({named}).")


def contain_as_named(candidate: str | Path, roots: Sequence[Path]) -> Path:
    """`contain`'s check, returning ``candidate`` **as named**, not resolved (register N-112,
    ruling R851): the path a server write must open.

    `contain` returns the resolved path, so a writer that opens what it returns follows a symlink at
    the final component before its no-follow open (`O_NOFOLLOW`, or `refuse_existing_non_regular_
    destination`'s `lstat`) can see one. Measured through `run_point_in_time`: a symlink at
    `08_results/point_in_time_<grid>.txt` aimed at another file inside the root was followed and
    that file overwritten. Opening the named path lets the final-component guard see the link.

    *(Corrected 2026-10-05, R851, register N-113: this said "a symlinked *directory* in the chain is
    still resolved by the open, and `contain` has checked where it leads", as though that were
    safe. It was not: with `08_results` a link to another in-root directory, `run_point_in_time`
    overwrote that directory's `point_in_time_<grid>.txt` and was refused only afterwards.)* This
    function does not refuse a linked directory; every writer that uses it also calls
    `rad_engine.safe_io.refuse_linked_components` below the project root, before any `mkdir` or
    write.
    """
    contain(candidate, roots)
    return expand_user(str(candidate))


def contain_octree_destination(octree_path: Path, *, roots: tuple[Path, ...]) -> None:
    """Refuse a submit-time octree destination that escapes `roots`, **before** anything is created.

    **The same shape as `_write_contained`, at the one write this tool does not perform itself.**
    `build_octree` -> `rad_engine.run.run_to_file` opens the octree destination, and that open was
    unguarded. It is a *branch* finding even though `run.py` was byte-identical to `master`: what
    changed is reachability. On `master` the only caller of `run_to_file` sits below
    `run_point_in_time`'s `dry_run` branch and cannot be reached without a submitted job. This tool
    hoists `build_octree` **above** its `dry_run` branch on purpose (R44(3)/R39, so an empty-octree
    or hostile-`!`-line refusal is reachable without paying for a trace), and that hoist put an
    arbitrary-file write on a path that submits no job, writes no job record, and returns success.

    Reproduced four ways before this guard existed, each with the victim under its own `mkdtemp` and
    therefore outside every allowed root -- a victim under `tmp_path` would prove nothing, because
    `tmp_path` *is* the root in these tests:

    - `dry_run=True` with a symlink at the destination: the victim was overwritten with
      `#?RADIANCE / oconv -f s.rad / FORMAT=Radiance_octree` and the call returned **success**.
    - `dry_run=False`, same plant: the victim was destroyed and the call *then* returned
      `refused: true` -- R48's exact signature, a refusal about damage already done.
    - `06_octrees` as a **directory symlink**: the unguarded `mkdir` walked out of the root and a
      1 910-byte `.oct` landed outside every root, with no symlink at the final component at all.
      This is why the check runs before `mkdir` and not merely before the open.
    - A FIFO at the destination, planted **inside** the root: containment passes legitimately and
      the open blocks forever. That one containment cannot fix and does not try to --
      `open_regular_file`'s `O_NONBLOCK`/`S_ISREG` in `run_to_file` is its only guard.

    **The destination is targetable, which is what settles the severity.** It is
    `annual_<grid>.oct` and `grid` is a caller-supplied argument, so an attacker chooses which
    filename in that directory is written rather than waiting for one -- the property that ranked
    `rad_engine.sensors.write_points` first among the seventeen sites.

    Nothing about this needs the open path-policy decision: `runtime.roots` is already in scope at
    the call site, and this asks the roots the server was launched with, exactly as `_write_contained`
    does. Raises `PathOutsideRoot`, which `run_annual`'s handler chain shapes into a
    `PATH_OUTSIDE_ROOT` refusal.
    """
    if not resolved_parent_within(octree_path, roots):
        raise PathOutsideRoot(
            f"the octree for this run would be written to {octree_path.parent}, which resolves "
            "outside every allowed root. Nothing has been written and no job has been submitted. A "
            "symlinked directory inside the project is the usual cause; the octree this server "
            "builds must stay inside the project it belongs to."
        )
