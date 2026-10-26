"""The `.pts` sensor file: six numeric columns per line, one line per sensor point.

A file format, so it lives here and not in `rad_core` (CONTEXT.md, Layers: `rad_core` holds "no file
formats") and not in `rad_mcp` (which "plumbs" -- docs/tool-surface.md#7-sensor_grid states the split
in one sentence: *"`rad_core.grids` owns the formulas, the aspect-ratio rule and point placement --
pure ... `rad_engine` only writes the six-column `x y z dx dy dz` file. `rad_mcp` plumbs."*).

`rad_core.grids.GridPoint` already owns the six numbers and their order; this module owns only the
bytes. It spawns nothing -- `rtrace -I` reads this file on stdin later, and that is `run.py`'s job,
not this one's.

**Why the count is refused rather than written.** An empty `.pts` is a study over zero sensors that
every downstream binary accepts without complaint: `rtrace` reads no lines, writes no lines, exits 0.
`rad_core.grids.place_sensor_grid` already refuses that upstream (`GridEmptyError`, ruling R21), and
this refuses it again at the byte boundary for the same reason `oconv -f` is not left to convention:
the caller that eventually writes points from somewhere other than `place_sensor_grid` gets the same
guarantee.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from rad_core.grids import GridPoint

from .safe_io import open_regular_file, open_staging_file

__all__ = [
    "PTS_COLUMNS", "PointsFile", "PointsFileEmptyError", "render_points_text", "stage_points", "write_points",
]

#: `x y z dx dy dz`. The number is asserted by check 2.6 ("exactly 6 numeric columns per line") and
#: is fixed by `rad_core.grids.GridPoint.as_tuple`, which is the only thing that produces a row.
PTS_COLUMNS = 6


class PointsFileEmptyError(RuntimeError):
    """No points to write. See the module docstring.

    `code` is `None`: `GRID_EMPTY` is allocated for the *placement* that produced nothing
    (`rad_core.grids.GridEmptyError`, ruling R21), and claiming it here would report a placement
    refusal for a write that was never given anything to place. This exception is a guard against a
    caller, not a client-facing refusal -- `place_sensor_grid` raises the coded one first.
    """


@dataclass(frozen=True)
class PointsFile:
    """One `.pts` file `write_points` wrote, and how many sensors are in it.

    `count` is the number of **lines**, which is the number a caller reports as `points.total` for a
    grid that was not clipped and as `points_inside` for one that was -- this module does not know
    which, and deliberately reports only what it wrote.
    """

    path: Path
    count: int


def _format_real(value: float) -> str:
    """One column, exactly. `repr`, not a fixed-precision format -- the same reasoning
    `rad_engine.materials._format_real` states for primitive arguments: truncating a correct value's
    precision on the way to disk is a wrong number with fewer digits, and Radiance's number parser
    accepts `repr`'s shape (`0.0`, `0.6000000000000001`) exactly as it accepts a shorter one.
    """
    return repr(float(value))


def render_points_text(points: Sequence[GridPoint]) -> str:
    """The file's bytes, as text: `PTS_COLUMNS` space-separated numbers per line, in `GridPoint`'s
    own order, one line per point, every line terminated.

    Order is the order `points` arrives in and is **not** sorted here: a `.pts` file's line order is
    the sensor order of every downstream `.ill` column, so re-ordering it would silently permute
    results against the point list the caller reported. (This is the one place this project's
    sort-for-determinism habit -- `render_materials_text` -- must not be copied.)
    """
    if not points:
        raise PointsFileEmptyError(
            "a .pts file with no lines is a study over zero sensors: rtrace reads no lines, writes "
            "no lines and exits 0. Nothing downstream would report a problem."
        )
    return "".join(
        " ".join(_format_real(column) for column in point.as_tuple()) + "\n" for point in points
    )


def write_points(path: Path | str, points: Sequence[GridPoint]) -> PointsFile:
    """Write `points` to `path` as a `.pts` file and report where it went and how many lines it has.

    `path`'s parent directories are created if missing -- the convention
    `rad_engine.materials.write_materials` and `rad_engine.octree.build_octree` already follow for an
    output path.

    The write goes through `rad_engine.safe_io.open_regular_file` (ruling R48), so a symlink or a
    FIFO at `path` is refused rather than followed. Raises `NotARegularFileError` for a FIFO or a
    device, and a raw `OSError` with `errno.ELOOP` for a symlink -- `O_NOFOLLOW` fails the open
    *before* there is a descriptor to `fstat`, so the regular-file branch is never reached for the
    case the guard exists to stop. A caller that shapes these into a refusal must catch both.
    """
    path = Path(path)
    text = render_points_text(points)  # raises before anything is created
    path.parent.mkdir(parents=True, exist_ok=True)
    # **Not** `path.write_text`, which follows a symlink at the final component. This site was
    # ranked first of seventeen unguarded writes for one reason: it is the only one where a caller
    # both **influences the filename** (`sensor_grid`'s `name` becomes `<name>.pts`) and no
    # containment is applied here, so a planted symlink can be *aimed at* rather than merely waited
    # for. Reproduced before the fix: a symlink at `grid.pts` pointing outside every allowed root
    # had its target overwritten with sensor coordinates, and this function returned a `PointsFile`
    # reporting success. `open_regular_file` carries `O_NOFOLLOW`, and its `O_NONBLOCK`-then-clear
    # also stops `os.mkfifo` at this path from wedging the calling thread forever -- which
    # `write_text` did, in an `anyio` worker whose pool is 40 wide.
    #
    # **Containment of the parent chain stays the caller's**, exactly as ruling R48 left
    # `rad_engine.weather.write_wea`: `O_NOFOLLOW` does not see a *directory* symlink partway up,
    # and this layer has no roots to check against -- `resolved_parent_within` needs them, and
    # inventing a policy for what they are would settle open decision 1 by omission.
    # `sensor_grid` no longer calls this: since Phase 11b's final fix wave it stages through
    # `stage_points`, and `rad_mcp.server._commit_grid_files` runs `resolved_parent_within` first.
    with open_regular_file(path, "wb") as handle:
        handle.write(text.encode("utf-8"))
    return PointsFile(path=path, count=len(points))


def stage_points(partial: Path | str, points: Sequence[GridPoint]) -> PointsFile:
    """Write `points` to the **staging** file `partial` (`<name>.pts.partial`) and report it; the
    caller renames it over the real name with `os.replace` once every file of the grid is staged.

    **Why a staged write, and not `write_points`** (Phase 11b final fix wave, S3 and C1).
    `write_points` opens `"wb"`, which is `O_TRUNC` on whatever regular file is at the name -- and a
    *hardlink* planted at `<grid>.pts` or at a zone file's name is a regular file, so the sensor
    coordinates landed on its inode, outside every root. `open_staging_file` creates the file
    exclusively (`O_EXCL`), so the only inode this can write is one it created, and `os.replace`
    then swaps the *name*, leaving a hardlinked inode's bytes and other names untouched. Staging is
    also what lets a multi-file grid be refused whole: nothing replaces a real file until every file
    is written.

    Neither creates `partial`'s parent nor checks it: containment of the parent chain is the
    caller's, as for `write_points`. Raises `NotARegularFileError` for anything at `partial` that is
    not a regular file, and `FileExistsError` if something was planted there after the unlink.
    """
    partial = Path(partial)
    text = render_points_text(points)  # raises before anything is created
    # The open outside the cleanup (`rad_engine.blend`'s reason): a refused open created nothing of
    # ours, so there is nothing of ours to unlink; a failed write removes the file it created.
    handle = open_staging_file(partial)
    try:
        with handle:
            handle.write(text.encode("utf-8"))
    except BaseException:
        partial.unlink(missing_ok=True)
        raise
    return PointsFile(path=partial, count=len(points))
