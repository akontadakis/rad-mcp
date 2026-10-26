"""Derive LM-83's per-group blind schedule and seal it as `sched:<grid>`.

**Under either edition** (R864): LM-83-12 section 2.2.6's four-row area table, the default, or
LM-83-22 section 4.6's one 2 % rule, chosen by `--lm83-edition`. `rad_engine.chain.
blind_schedule_steps` writes that flag only when the sealed `parameters` carry `lm83_edition`, and
`sealed_lm83_edition` is the one reader of that key, here and for every later reader of the record.

**A process, not a hook**, for `rad_engine.blend`'s reason verbatim: `Step` is one spawned program
and `Step.reuse`'s docstring records that the worker offers no per-step Python hook (ruling R44).
This module has an argv, a DEVNULL stdin, no declared stdout and a declared artifact, exactly as
`rcontrib` does.

**It owns no arithmetic.** `rad_core.blinds.group_schedule` decides every state, `closing_order`
decided the order (at plan time -- see below), and `rad_core.metrics.blinds_close` decides each
hour; this module opens files, transposes them and writes JSON. Every refusal `group_schedule`
makes -- a missing column, a ghost group, a group with no row, a short row, a non-finite or
negative cell, a repeated name in the order -- is reached by calling it and letting it raise, never
by a second copy here.

**The order arrives on the argv and is not re-derived.** `closing_order` needs each group's
declared closing role and its sill height, which are `rad_engine.chain`'s knowledge and not this
process's: it is handed matrices and paths. So `chain.blind_schedule_steps` resolves the order
once, records it in `sched:<grid>`'s `parameters`, and passes it here as the **order of the
`--group` arguments themselves**. One enumeration, so `order.names` and the per-column group map
cannot name different sets -- which is the mismatch `group_schedule`'s ghost-group and
missing-group refusals close from below and which nothing above them prevented (Task 3's review).

**Why this transposes instead of streaming, and why it is chunked** (ruling R152). `rcollate`
leaves each `illdir:<grid>:<g>` **sensor-major**: one row per sensor, one column per hour. Section
2.2.6's prefix scan needs the opposite -- every sensor of *one hour*, for every group at once,
because the criterion is a count over the grid. A column is therefore not a contiguous read, and
there are only two ways to get one: hold the transpose, or re-scan every file once per hour. The
second is `n_hours` full passes -- 8 760 parses of every group's matrix -- so this holds the
transpose, in `array('d')` rather than lists of Python floats.

**But it holds only as many hours as fit, and re-reads the matrices for the next block.**
`MAX_SCHEDULE_CELLS` bounds the *peak*, `hours_per_pass` turns that bound into a block size, and
the loop in `main` calls `group_schedule` once per block over that block's own `window`. The first
revision of this module refused instead of chunking, and ruling R152 rejected that: at two groups
over a whole year the bound refused **1 826 sensors per group** -- an ordinary LM-83 floorplate,
and an order of magnitude below this server's own `GRID_VERY_LARGE` 20 000-point warning. A
refusal a real run trips is a defect however good its message is.

**Chunking is exact, and that is a property of `group_schedule`, not an approximation this module
makes.** That function decides each column independently: it builds `residual` from scratch inside
the per-column loop, runs the prefix scan on it, and carries nothing between columns. `states` is
appended per column, `hours_closed` counts columns in which any group closed, and `columns` is the
sorted window. So concatenating the per-block results in block order reproduces the single-pass
answer cell for cell -- asserted, not asserted-in-prose, by
`tests/test_blend.py::TestTheScheduleIsChunkedAndTheChunkingIsExact`, which runs the same case at
several block sizes and compares against one pass.

The cost is `ceil(n_hours / hours_per_pass)` re-reads of every matrix, and the ruling's own figures
are the ones to judge it by: an ordinary run is **1** pass, a 2 000-point two-group floorplate over
a year is **2**, and `GRID_VERY_LARGE` at one group over a year is **6** -- not the 8 760 that
re-scanning per hour would cost, which is what makes the block the right unit.

**The window is every column of the matrix, not `lm83_window`'s 3 650.** `rad_engine.blend` refuses
a schedule whose per-group lists are not exactly `--hours` long, and `--hours` is the annual
matrices' own `NCOLS`; a schedule covering only the 08:00-18:00 window would be refused there as
"the two files were built over different analysis windows". It is also the right quantity: blinds
are a *state of the building* in every hour, and an hour with no direct sun closes nothing --
`blinds_close(points_in_sun=0, ...)` is `False` in all four of LM-83's area bands, so the
out-of-window hours come back `"open"` because nothing was in sun, not because they were skipped.
The 08:00-18:00 restriction is a property of the **metric's** tally, and it is applied where sDA is
tallied, not here. `GroupSchedule.hours_closed` is therefore a count over the whole matrix and the
JSON says which columns it was taken over.

**That is not the `hours_closed` `results(action="blind_schedule")` publishes, and the difference is
not only the denominator.** Denominator: this counts columns of the whole matrix, that counts
columns of LM-83's 08:00-18:00 window. **Subject**: this is per window group -- an hour counts when
*any* group closed, over per-group runs whose octrees black every other group's aperture -- while
that one derives from the whole-grid `illdir:<grid>` with every aperture open and publishes
`groups: null` (ruling R116). The two are not comparable in either direction, and Task 14, which
publishes this artifact, must not present them as though they were.

**Atomic.** The JSON is written to a sibling temporary and renamed only after the last byte is on
disk, for `blend._write_blend`'s reason: a crash mid-write must not leave a partial file under the
final name, because the seal registers whatever is at `--out` and `blend` would then hash and parse
half a schedule.

**Nothing goes to stdout.** The schedule goes to `--out`; stdout stays the job log, and on stdio it
is the JSON-RPC transport. Refusals go to stderr.

**Automated groups** (R870). `--automated-group NAME:PATH` names a group whose blinds follow their
own control, out of the closing order: `group_schedule` takes its states from
`--automated-schedule NAME:PATH` -- read once, bounded, its digest compared with
`--automated-schedule-digest` **before** it is parsed, and its rows required to equal `--n-hours`
-- or holds it closed every hour when no schedule is bound. Its direct matrix is still read: under
LM-83-12 its open hours count toward the trigger. With an automated group, zero `--group` is
accepted, and the JSON gains `automated` and `automated_sources`; with none, it is unchanged.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from array import array
from collections.abc import Mapping, Sequence
from math import isfinite
from pathlib import Path
from typing import Final

from rad_core.blinds import (
    BLIND_CLOSED,
    MAX_AUTOMATED_SCHEDULE_BYTES,
    AutomatedScheduleError,
    ClosingOrder,
    GroupSchedule,
    group_schedule,
    parse_automated_schedule,
)
from rad_core.lm83_edition import DEFAULT_LM83_EDITION, LM83_22, LM83_EDITIONS, Lm83Edition, section
from rad_core.metrics import LM83_ASE_THRESHOLD_LUX

from .annual import MatrixFileUnreadableError
from .blend import HelperArgumentParser, refuse_unmatched_digests, parse_operand_digests
from .project import bytes_digest, fsync_directory
from .results import iter_verified_annual_lux_rows, read_annual_header
from .safe_io import open_regular_file, open_staging_file

__all__ = [
    "LM83_EDITION_PARAMETER",
    "MAX_SCHEDULE_CELLS",
    "hours_per_pass",
    "main",
    "refuse_oversized_transpose",
    "sealed_lm83_edition",
]

#: The `sched:<grid>` parameter that records the LM-83 edition the schedule was derived under
#: (R864). **Written only under LM-83-22**: a schedule sealed before this key existed, or under the
#: default, has no such key, so no existing record changes digest or goes stale.
LM83_EDITION_PARAMETER: Final[str] = "lm83_edition"


def sealed_lm83_edition(parameters: Mapping[str, object]) -> Lm83Edition:
    """The LM-83 edition a `sched:<grid>` record (or the argv built from it) names.

    The key's absence reads as `DEFAULT_LM83_EDITION`, which is what every schedule sealed before
    R864 and every one sealed under LM-83-12 means. **A value that is present and is not exactly one
    of `LM83_EDITIONS` raises `ValueError`, and is never read as either edition**: a manifest is
    untrusted (R50), and the `rad_core` edition branches read anything that is not `"LM-83-22"` as
    LM-83-12, so a hand-edited `"lm-83-22"` would otherwise derive or judge every hour by the area
    table its author did not ask for.
    """
    value = parameters.get(LM83_EDITION_PARAMETER, DEFAULT_LM83_EDITION)
    if not isinstance(value, str) or value not in LM83_EDITIONS:
        raise ValueError(
            f"the blind schedule names lm83_edition={value!r}, which is not an LM-83 edition: the "
            f"editions are exactly {' and '.join(repr(e) for e in LM83_EDITIONS)}, letter case "
            "included. It is refused rather than read as either, because the edition decides "
            "which closing rule derived every hour of the schedule. Re-run run_annual(..., "
            "blind_control='lm83') to seal the schedule again."
        )
    return value  # type: ignore[return-value]

#: How many `(group, sensor, hour)` cells this helper holds transposed **at one time**.
#:
#: **A ceiling on peak memory, not on which runs are buildable** (ruling R152). A per-hour count
#: over the grid cannot be read off a sensor-major file without either the transpose or a re-scan,
#: so this bounds the transpose and `hours_per_pass` turns it into a block size; only a run whose
#: **single hour** does not fit is refused. The first revision refused whenever the *whole year*
#: did not fit, which at two groups turned into a 1 826-sensor-per-group ceiling on an ordinary
#: LM-83 floorplate.
#:
#: **The cost per cell is measured, not derived from `sizeof(double)`.** One process per shape,
#: nothing else allocated, `resource.getrusage(RUSAGE_SELF).ru_maxrss` (bytes on darwin) before and
#: after building exactly `{hour: {group: array("d", n_points)}}` -- reproduced independently, fix
#: round 2, `python3 <script computing the before/after RSS delta above> <groups> <points> <hours>`:
#:
#: | groups | points | hours | cells | bytes | B/cell |
#: |---|---|---|---|---|---|
#: | 1 | 456 | 8 760 | 3 994 560 | 39 157 760 | 9.80 |
#: | 2 | 456 | 8 760 | 7 989 120 | 75 874 304 | 9.50 |
#: | 1 | 4 000 | 4 000 | 16 000 000 | 133 152 768 | 8.32 |
#: | 3 | 500 | 8 760 | 13 140 000 | 142 835 712 | 10.87 |
#: | 1 | 20 000 | 1 600 | 32 000 000 | 263 913 472 | 8.25 |
#: | 2 | 1 826 | 8 760 | 31 991 520 | 291 995 648 | 9.13 |
#: | 12 | 300 | 8 760 | 31 536 000 | 364 199 936 | 11.55 |
#: | 30 | 122 | 8 760 | 32 061 600 | 402 178 048 | 12.54 |
#:
#: So **8.25-12.54 bytes a cell, not 8**: the buffer is 8, and the rest is one `array` object and
#: one dict entry per `(group, hour)`, which is why the per-cell figure rises as `n_points` falls
#: and as `n_groups` rises.
#:
#: **The true worst case at this bound is ~400 MB, not ~348 MB.** A first revision of this comment
#: took the table's worst *row* (3 groups, 10.87 B/cell) for the worst *shape* -- it is not.
#: Extending the same trend the comment itself names -- more groups, fewer points, held at the
#: bound -- reaches **30 groups x 122 points x 8 760 hours = 32 061 600 cells, measured at 12.54
#: B/cell, 402 178 048 bytes (~402 MB)** (fix round 2, `task-11-rereview.md`'s finding n2, same
#: measurement method as the table above). So the number to hold in mind is **around 400 MB**,
#: still under half a gigabyte, and it is refused before any of it is allocated whenever a single
#: hour alone would not fit.
#:
#: 32 000 000 is kept where ruling R152 put it. With chunking it costs passes rather than
#: refusals: the 456-point office grid over a whole year is 1 pass at any group count this server
#: has submitted, and `GRID_VERY_LARGE`'s 20 000 points over a whole year is 6.
MAX_SCHEDULE_CELLS: Final[int] = 32_000_000


def _cells_per_hour(*, n_groups: int, n_points: int) -> int:
    """One hour of the transpose: every sensor of the grid, for **every** window group.

    **One expression, because the group factor is the half that is easy to drop** (fix round 1,
    Important 2). `n_points * n_hours` in place of `n_groups * n_points * n_hours` survived all 468
    tests of `test_chain` + `test_blend` + `test_project` in the first revision, and the docstring's
    own "eight groups fit" was the case it broke -- 2.2 GB admitted. Both the refusal and the block
    size read the count from here, so a mutant on this line reddens both.
    """
    return n_groups * n_points


def refuse_oversized_transpose(*, n_groups: int, n_points: int) -> None:
    """Raise when a **single hour** of the transpose is already above `MAX_SCHEDULE_CELLS`.

    That is the only shape chunking cannot rescue: `hours_per_pass` would come back `0` and the
    loop would make no progress. Everything else costs passes.

    **Two call sites, one implementation** (ruling R138's sweep): `main` below, where the memory
    would actually be allocated, and `rad_engine.chain.blind_schedule_steps`, where a run can still
    be refused for free. A second, hand-written copy of the arithmetic in the planner is how the
    two come to disagree about which runs are buildable, and the planner's copy is the one a caller
    sees.

    **`n_hours` is deliberately not an argument.** It was one before ruling R152, when the whole
    year had to fit at once; now it decides nothing here, and an argument that does not affect a
    decision is a trap for the next reader.
    """
    per_hour = _cells_per_hour(n_groups=n_groups, n_points=n_points)
    if per_hour > MAX_SCHEDULE_CELLS:
        raise ValueError(
            f"one hour of this blind schedule needs {per_hour} transposed cells ({n_groups} "
            f"window group(s) x {n_points} sensor(s)), above the {MAX_SCHEDULE_CELLS} this server "
            "holds at once. LM-83's blind trigger counts points in sun in one hour, "
            "across the whole grid, for every group at once -- so one hour is the smallest block "
            "there is, and a run whose single hour does not fit cannot be split into smaller "
            "passes. Reduce the grid, or run with blind_control='none', which plans no schedule "
            "at all."
        )


def hours_per_pass(*, n_groups: int, n_points: int, n_hours: int) -> int:
    """How many hours of the transpose are held at once -- the block size ruling R152 introduced.

    `MAX_SCHEDULE_CELLS // cells_per_hour`, capped at `n_hours` so a short run reports one pass
    rather than a block wider than the matrix it is reading. Raises through
    `refuse_oversized_transpose` when even one hour does not fit.

    Callers get the number rather than a boolean so the planner runs **exactly** the arithmetic the
    helper will run, which is a stronger guarantee than a separate predicate that agrees today.
    `chain.blind_schedule_steps` calls this **once and binds the answer**: it raises the planner's
    refusal when a single hour does not fit, and `ceil(n_hours / per_pass)` off the same number is
    the step's declared `estimate_s` (ruling R162). It discarded the result until Phase 7d-1 Task
    15b, and this sentence still said so afterwards -- corrected here rather than deleted, because
    the discarded form is the thing the ruling changed.
    """
    if n_hours <= 0:
        raise ValueError(
            f"--n-hours must be positive, not {n_hours}. An empty window reads as 'the blinds "
            "never closed', which is the favourable case and is not what a period nothing was "
            "measured in produced."
        )
    refuse_oversized_transpose(n_groups=n_groups, n_points=n_points)
    return min(n_hours, MAX_SCHEDULE_CELLS // _cells_per_hour(
        n_groups=n_groups, n_points=n_points
    ))


def _parse(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = HelperArgumentParser(
        prog="rad_engine.schedule",
        description="Derive LM-83's per-window-group hourly blind schedule.",
    )
    parser.add_argument("--out", required=True, type=Path, help="where the schedule JSON is written")
    parser.add_argument("--n-points", required=True, type=int, help="sensors in the grid")
    parser.add_argument("--n-hours", required=True, type=int, help="columns in each direct matrix")
    parser.add_argument(
        "--area-sf", required=True, type=float, help="the analysis area, in square feet"
    )
    parser.add_argument(
        "--group",
        action="append",
        default=[],
        metavar="NAME:PATH",
        help=(
            "one window group's direct-sun matrix, repeatable. **The order of these arguments is "
            "the closing order** -- see the module docstring"
        ),
    )
    parser.add_argument(
        "--group-digest",
        action="append",
        default=[],
        metavar="NAME:sha256:<hex>",
        help=(
            "the digest the manifest sealed for one --group matrix, checked on the same descriptor "
            "its rows are read from; repeatable. Omitted for an operand this job rebuilds, whose "
            "sealed digest is stale by design -- see rad_engine.blend.parse_operand_digests"
        ),
    )
    parser.add_argument(
        "--lm83-edition",
        default=DEFAULT_LM83_EDITION,
        help=(
            "the LM-83 edition whose closing rule derives the schedule; omitted means "
            f"{DEFAULT_LM83_EDITION}. Checked by sealed_lm83_edition, never read as a default"
        ),
    )
    parser.add_argument(
        "--automated-group",
        action="append",
        default=[],
        metavar="NAME:PATH",
        help=(
            "one automated window group's direct-sun matrix, repeatable (R870). Out of the closing "
            "order: its states are its --automated-schedule, or closed every hour without one"
        ),
    )
    parser.add_argument(
        "--automated-schedule",
        action="append",
        default=[],
        metavar="NAME:PATH",
        help="the hourly open/closed schedule bound to one --automated-group, repeatable (R870)",
    )
    parser.add_argument(
        "--automated-schedule-digest",
        action="append",
        default=[],
        metavar="NAME:sha256:<hex>",
        help=(
            "the digest sealed for one --automated-schedule, compared with the bytes read before "
            "they are parsed; required for every --automated-schedule (R870)"
        ),
    )
    parser.add_argument(
        "--order-substituted",
        action="store_true",
        help=(
            "the closing order was not declared by every group's role and was substituted by sill "
            "height; carried onto the schedule so the published result says so"
        ),
    )
    return parser.parse_args(argv)


def _parse_groups(
    specs: Sequence[str], *, automated: bool = False, schedules: bool = False
) -> list[tuple[str, Path]]:
    """`[(name, path), ...]` in argv order, which **is** the closing order for `--group`.

    Also reads `--automated-group` (`automated=True`, R870), whose argv order means nothing (the
    group is out of the order and never in the residual twice), and `--automated-schedule`
    (`schedules=True`), whose path is an hourly schedule rather than a matrix -- so each has its
    own repeat refusal, and the schedule its own malformed one.

    A repeated name is refused rather than overwritten, for `blend._open_contributions`' reason one
    file over: a `dict` would take the last path silently, so which matrix decided a group's
    schedule would depend on argv order and nothing anywhere would record that the other existed.
    It is also the repeat `closing_order` itself refuses (ruling R138, fix round 1) -- a name twice
    in the order sums that group's beam into the residual twice, doubling light the grid never
    produced -- so refusing it here means the two layers agree rather than one of them being the
    only guard.
    """
    groups: list[tuple[str, Path]] = []
    seen: set[str] = set()
    for spec in specs:
        name, separator, raw_path = spec.partition(":")
        if schedules and not (separator and name and raw_path):
            raise ValueError(
                f"automated schedule argument {spec!r} is not NAME:PATH. Both parts are required: "
                "without the name, nothing says which automated group's hours this file decides."
            )
        if schedules and name in seen:
            raise ValueError(
                f"an automated schedule for {name!r} was given twice. Only one could decide that "
                "group's hours, and which one would depend on the order they were passed in."
            )
        if automated and separator and name and raw_path and name in seen:
            raise ValueError(
                f"automated group {name!r} was given twice. Only one of the two matrices could be "
                "its direct sun, and which one would depend on the order they were passed in."
            )
        if not (separator and name and raw_path):
            raise ValueError(
                f"window group argument {spec!r} is not NAME:PATH. Both parts are required: "
                "without the name, nothing says which group's aperture this matrix was traced "
                "with every other one blacked."
            )
        if name in seen:
            raise ValueError(
                f"window group {name!r} was given twice. Only one of the two matrices could "
                "decide its schedule, and which one would depend on the order they were passed "
                "in -- and a repeated name in the closing order sums that group's direct beam "
                "into the residual twice, closing blinds against light the grid never produced."
            )
        seen.add(name)
        groups.append((name, Path(raw_path)))
    return groups


def _read_automated_states(
    schedules: Sequence[tuple[str, Path]], *, digests: Mapping[str, str], n_hours: int
) -> dict[str, tuple[str, ...]]:
    """`{group: states}` for each bound `--automated-schedule`, read and checked (R870).

    One bounded read through `open_regular_file` (a FIFO or a symlink is refused, not waited on),
    then the digest compared with `--automated-schedule-digest` **before** the bytes are parsed,
    so a file that moved since it was bound is refused as moved whatever it now holds. The rows
    must equal `--n-hours`: the schedule is indexed by the direct matrices' own columns.
    """
    states: dict[str, tuple[str, ...]] = {}
    for name, path in schedules:
        with open_regular_file(path) as handle:
            data = handle.read(MAX_AUTOMATED_SCHEDULE_BYTES + 1)
        actual = bytes_digest(data)
        if actual != digests[name]:
            raise ValueError(
                f"the automated schedule for {name!r} at {path} reads as {actual}, which does not "
                f"match the {digests[name]} sealed when it was bound. The file changed after "
                "window_group(action='bind_schedule') copied it, so the hours it now holds are not "
                "the ones this schedule was planned from, and running again plans the same sealed "
                "digest. Bind the schedule again from your blind controller's own file, then run "
                "again: window_group(action='bind_schedule', project=..., "
                f"name={name!r}, schedule=...)."
            )
        try:
            states[name] = parse_automated_schedule(data, hours=n_hours).states
        except AutomatedScheduleError as error:
            raise ValueError(f"the automated schedule for {name!r} at {path}: {error}") from None
    return states


def _transpose(
    groups: Sequence[tuple[str, Path]],
    *,
    n_points: int,
    n_hours: int,
    first: int,
    last: int,
    digests: Mapping[str, str] | None = None,
) -> dict[int, dict[str, array]]:
    """`{hour: {group: one array('d') of every sensor's direct lux}}` for hours `[first, last)`.

    **One block, not the whole year** (ruling R152). Every row of every matrix is still read -- a
    sensor's value for hour `h` is only reachable by parsing that sensor's row -- but only the
    columns inside the block are kept, so peak memory is `n_groups * n_points * (last - first)`
    cells and `main` chooses `last - first` through `hours_per_pass`. The cost is that the next
    block re-reads the same files; `ceil(n_hours / hours_per_pass)` passes in total, which the
    module docstring gives the ruling's own figures for.

    The reader is `rad_engine.results.iter_verified_annual_lux_rows` (whole-branch review, F6: this
    sentence named `iter_annual_lux_rows`, the unverified reader this function stopped calling when
    the digest check below was added -- the docstring survived the fix that changed the call three
    lines below it), with `expected_points` supplied: `NROWS` alone cannot detect a matrix belonging to a
    different grid, because two integers are consistent with any story about them, and the count
    this process was launched with is the independent third statement that makes the check able to
    fail. `NCOLS` is checked against `--n-hours` for the same reason and **before** any row is read,
    off `read_annual_header`, so a matrix built over a different analysis period is refused rather
    than transposed into columns nothing will index. Both checks run on **every** pass rather than
    only the first: a matrix replaced underneath a running helper between two passes is exactly the
    case a first-pass-only check would miss, and a header read is cheap beside the body it guards.

    **Two of the branches below are second lines and are unreachable in a correct build.** Said
    here for both, because fix round 1 found the short-body one documented and the overlong one not
    -- and an unreachable branch whose neighbour is labelled and which is not reads as reachable.

    - the **overlong** branch (`sensor >= n_points`): `iter_verified_annual_lux_rows` refuses a body
      longer than its own header, and `expected_points` has already tied that header to `--n-points`, so
      the reader raises before this loop can see row `n_points`. It is kept because a loop that
      stopped at a count would never ask for the row that proves the overrun --
      `blend._refuse_unconsumed`'s finding -- and this loop drains the iterator precisely so the
      reader's own check fires; the branch is what would name *this file and this schedule* if a
      reader ever stopped holding that contract.
    - the **short-body** branch (`sensor + 1 != n_points`): the mirror case, unreachable for the
      mirror reason, kept for `blend._next_row`'s. The alternative is a schedule silently derived
      over a prefix of the grid with the missing sensors reading zero, which counts them as not in
      sun and opens a blind that should have closed.

    Neither is asserted on its own wording. `tests/test_blend.py::TestTheScheduleWriter::
    test_a_short_matrix_is_refused_rather_than_scheduled_over_the_sensors_it_has` and
    `::test_an_overlong_matrix_is_refused_by_the_reader_before_this_module_sees_it` both assert the
    refusal that **actually** arrives, so neither is an assertion that cannot go red.
    """
    digests = digests or {}
    columns: dict[int, dict[str, array]] = {hour: {} for hour in range(first, last)}
    width = last - first
    for name, path in groups:
        # **A separate open, and it stays one.** The hours cross-check below needs the header
        # before any row is read, and its refusal names the *window group* -- which the engine's
        # generic reader cannot. This is the same split `rad_mcp.results_tool` lives with, where
        # `_contained_matrix` reads a header through its own open before `_lux_rows` runs: the
        # one-descriptor property is the row reader's, not the whole function's. Stated rather
        # than implied.
        header = read_annual_header(path)
        if header.hours != n_hours:
            raise ValueError(
                f"the direct-sun matrix for window group {name!r} at {path} declares "
                f"NCOLS={header.hours} but this schedule is over {n_hours} hour(s). The matrix and "
                "the analysis period have drifted apart, so every hour after the shorter of the "
                "two would be scheduled against a column that is not it."
            )
        per_group = [array("d", bytes(8 * n_points)) for _ in range(width)]
        for offset in range(width):
            columns[first + offset][name] = per_group[offset]
        # **`digests.get(name)` is `None` for an operand this job rebuilds, and that matrix is
        # then read unverified.** Not an oversight: `rad_engine.chain.reuse_decision` emits a
        # `--group-digest` only for an operand it has *not* classified as rebuilding, because the
        # manifest's digest for a matrix a step earlier in the same job is about to overwrite
        # describes the previous run's bytes -- passing it would refuse every rebuild as drifted.
        # So the coverage here is genuinely partial, and it is said rather than left to the reader
        # to infer from a `.get`. `blend._open_contributions` carries the same note.
        #
        # What the check does buy, where it is passed: the digest is taken from the descriptor the
        # rows are then read from, which defeats **replacement** of the file between the hash and
        # the read but **not** an in-place rewrite of the same inode -- narrowed, not closed.
        #
        # **And "replacement defeated / in-place not defeated" is not an exhaustive enumeration.**
        # Two further residual classes are disclosed in full at the published surface
        # (`docs/tool-surface.md` §14, *"Two more residual classes"*; whole-branch review, F7): the
        # digest's **trust root** is the same unsigned `manifest.json` that an attacker able to
        # perform the in-place write already owns, so the read reports as verified with no race at
        # all; and a **directory symlink planted in the parent chain** substitutes the path
        # upstream of both seeks, which fails closed with a digest present and open without one.
        # Neither is fixed; both are named so the pair above stops reading as complete.
        #
        # **The digest is re-taken once per pass, not once per schedule** (whole-branch review,
        # m12): `_derive` calls `_transpose` once per `hours_per_pass`-sized block, and this
        # function is what actually opens the matrix, so an ordinary 2-group x 456-point floorplate
        # (one pass) pays it once, but `GRID_VERY_LARGE` (2 groups x 20 000 points, 800 h/pass, 11
        # passes over a year) hashes every group matrix eleven times -- roughly doubling the I/O
        # this module already pays for the per-pass header re-scan, whose own reason
        # (`_transpose`'s docstring, above: "a matrix replaced underneath a running helper between
        # two passes is exactly the case a first-pass-only check would miss") applies identically
        # to the digest. Not hoisted to the first pass without deciding that question, since the
        # header's own rationale argues the other way.
        rows = iter_verified_annual_lux_rows(
            path, expected_points=n_points, content_digest=digests.get(name)
        )
        sensor = -1
        for sensor, row in enumerate(rows):
            if sensor >= n_points:
                raise ValueError(
                    f"the direct-sun matrix for window group {name!r} at {path} carries more than "
                    f"the {n_points} sensor row(s) this schedule was launched for. The matrix and "
                    "the grid have drifted apart, so the extra rows belong to sensors the "
                    "criterion would never count."
                )
            block = row[first:last]
            for offset in range(width):
                per_group[offset][sensor] = block[offset]
        if sensor + 1 != n_points:
            raise ValueError(
                f"the direct-sun matrix for window group {name!r} at {path} carries "
                f"{sensor + 1} sensor row(s) but this schedule is over {n_points}. A short matrix "
                "leaves the missing sensors reading zero, which counts them as not in sun and "
                "opens a blind that should have closed."
            )
    return columns


def _derive(
    groups: Sequence[tuple[str, Path]],
    *,
    order: ClosingOrder,
    n_points: int,
    n_hours: int,
    area_sf: float,
    digests: Mapping[str, str] | None = None,
    edition: Lm83Edition = DEFAULT_LM83_EDITION,
    automated: Mapping[str, Sequence[str]] | None = None,
) -> dict[str, object]:
    """The whole schedule, derived one block of hours at a time and concatenated (ruling R152).

    `groups` is every matrix the helper was given, manual and automated; `automated` maps each
    automated group to its state in every hour of the run (R870), and is passed whole to every
    block's `group_schedule`, which indexes it by the block's own columns. Each automated group's
    states follow the manual ones in `states`, sorted, and the payload gains `automated`; with no
    automated group the payload is exactly what it was.

    **The concatenation is exact and the reason is `group_schedule`'s own shape**, not an
    approximation made here. That function builds `residual` from scratch inside its per-column
    loop and carries nothing between columns: `states[g]` is appended once per column in `window`
    order, `hours_closed` counts columns in which any group closed, and `columns` is the sorted
    window. So the concatenation below -- states extended in block order, `hours_closed` summed,
    `columns` extended -- reproduces the single-pass answer cell for cell. Asserted at several
    block sizes against one pass by
    `tests/test_blend.py::TestTheScheduleIsChunkedAndTheChunkingIsExact`, because "this is exact"
    is the sort of claim that is true until someone gives the scan a carry.

    `band`, `area_sf`, `threshold_lux` and `order` are properties of the run rather than of a
    block, so they are read off the **first** block and every later block must agree. The
    `assert`-free equality check below is deliberate: an inconsistency here would mean
    `group_schedule` had become column-dependent in exactly the way the paragraph above says it is
    not, and that is a refusal, not an assertion to be stripped by `-O`.
    """
    # Every matrix is transposed, automated ones included, so the block is sized over all of them.
    per_pass = hours_per_pass(n_groups=len(groups), n_points=n_points, n_hours=n_hours)
    automated_names = tuple(sorted(automated or {}))
    states: dict[str, list[str]] = {name: [] for name in (*order.names, *automated_names)}
    columns: list[int] = []
    hours_closed = 0
    run: GroupSchedule | None = None
    for first in range(0, n_hours, per_pass):
        last = min(first + per_pass, n_hours)
        block = group_schedule(
            _transpose(
                groups, n_points=n_points, n_hours=n_hours, first=first, last=last,
                digests=digests,
            ),
            order=order,
            points_total=n_points,
            area_sf=area_sf,
            window=frozenset(range(first, last)),
            # Imported, never retyped: `chain.blind_schedule_steps` records this same constant in
            # `sched:<grid>`'s parameters, so the number the schedule was derived with and the
            # number the manifest says it was derived with are one definition.
            threshold_lux=LM83_ASE_THRESHOLD_LUX,
            edition=edition,
            automated=automated,
        )
        if run is None:
            run = block
        elif (block.band, block.area_sf, block.threshold_lux) != (
            run.band, run.area_sf, run.threshold_lux
        ):  # pragma: no cover - group_schedule decides these from its arguments, which do not move
            raise ValueError(
                f"the blind schedule's own trigger moved between hour blocks: block "
                f"{first}-{last} reports band {block.band!r} at {block.threshold_lux} lux over "
                f"{block.area_sf} sf, and the first block reported {run.band!r} at "
                f"{run.threshold_lux} lux over {run.area_sf} sf. The blocks are a memory device "
                "and must not be visible in the answer."
            )
        for name in states:
            states[name].extend(block.states[name])
        columns.extend(block.columns)
        hours_closed += block.hours_closed
    if run is None:  # pragma: no cover - hours_per_pass refuses n_hours <= 0 above
        raise ValueError("the blind schedule covered no hours at all.")
    payload: dict[str, object] = {
        # **In closing order, which is data-dependent, and that is deliberate.**
        # `rad_engine.blend` takes `tuple(sorted(schedule))` precisely because this order varies
        # with the sill heights and roles of the project; the sum over groups is commutative, so
        # sorting there is what makes the same operands produce the same bytes twice without this
        # writer having to give up saying what the scan did. `states` is keyed off `order.names`
        # above, so the block loop cannot reorder it either. Automated groups follow, sorted.
        "states": {name: states[name] for name in (*order.names, *automated_names)},
        "columns": columns,
        "area_sf": run.area_sf,
        "band": run.band,
        "threshold_lux": run.threshold_lux,
        "hours_closed": hours_closed,
        "order": list(order.names),
        "order_substituted": order.substituted,
    }
    if automated_names:
        payload["automated"] = list(automated_names)
    return payload


def _write_schedule(payload: dict[str, object], *, out: Path) -> None:
    """Write `payload` to a sibling temporary and rename it only once it is whole.

    `blend._write_blend`'s structure and its reasons, unchanged: `os.replace` is the last thing that
    happens, the temporary is a **sibling** so the rename is within one filesystem, the parent
    directory is fsynced after the rename because `os.replace` being atomic says nothing about the
    *name* having reached disk, the open sits outside the cleanup so a refusal never unlinks a file
    this process did not create, and the cleanup catches `BaseException` because a
    `KeyboardInterrupt` between two writes leaves the same partial file behind as a `ValueError`
    does.

    It is written here rather than imported from `blend` because that function writes a *matrix*
    row by row from a stream of readers; this writes one already-built JSON document. Sharing the
    body would mean a parameter for every difference and a function that is two functions.
    """
    partial = out.with_name(out.name + ".partial")
    data = json.dumps(payload).encode("utf-8")
    handle = open_staging_file(partial)
    try:
        with handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(partial, out)
        fsync_directory(out.parent)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise


def main(argv: Sequence[str] | None = None) -> int:
    """Derive and write the schedule, or refuse. Returns 0 only when a whole JSON is under `--out`.

    The `except` clause names `MatrixFileUnreadableError` explicitly for `blend.main`'s reason: it
    is a `RuntimeError` and **not** an `OSError` (`rad_engine.annual` keeps "the bytes could not be
    read" apart from "the bytes are not a matrix"), so a non-regular or unparseable matrix would
    otherwise escape as a traceback while a missing one, which fails in `open_regular_file` as a
    plain `FileNotFoundError`, returned 1. Two shapes of the same failure must not exit two
    different ways.
    """
    try:
        # **Inside the `try`, so a malformed argv exits 1 like every other refusal.**
        # `blend.HelperArgumentParser.error` raises `ValueError` rather than calling
        # `parser.exit(2, ...)`; see that class for the case that forced it (`--area-sf -inf`, fix
        # round 1, Important 3) and for why it is shared with `rad_engine.blend` rather than
        # written twice.
        args = _parse(argv)
        # The edition first: the messages below name its clause, and an unknown value is refused
        # here rather than read as LM-83-12 by `rad_core`'s edition branches.
        edition = sealed_lm83_edition({LM83_EDITION_PARAMETER: args.lm83_edition})
        if args.n_points <= 0:
            raise ValueError(
                f"--n-points must be positive, not {args.n_points}. "
                f"{section('blind_trigger', edition)} counts the points in sun against the grid's "
                "point count, and a zero denominator makes every hour's trigger False -- a year of "
                "open blinds off no grid."
            )
        if args.n_hours <= 0:
            # **The wording differs from `hours_per_pass`' guard on purpose, and the difference is
            # whole-branch test finding F4.** Both guards refuse `--n-hours <= 0` and both used to
            # raise the *same string*, so mutating this one to `< 0` left
            # `TestTheScheduleWriter::test_it_refuses_and_writes_nothing` green -- and that test
            # *does* assert a message fragment, which is exactly why the duplication made it
            # invisible: verified by capture, the stderr was byte-identical either way.
            #
            # Behaviourally the duplication was harmless, since `hours_per_pass` catches what this
            # one misses. What it cost was the ability to say **which check fired**, which is the
            # recorded "same exception and same digits" shape and is the duplication
            # `rad_core.blinds._validate_threshold_lux`'s docstring exists to forbid: *"two copies
            # of the same validation are two messages that agree only until one of them is edited."*
            # Naming the argv is the honest distinction -- this guard is about the **flag** this
            # program was launched with, `hours_per_pass`' is about the **argument** a caller in
            # this process passed it.
            raise ValueError(
                f"--n-hours must be positive, not {args.n_hours}, and this is the argv this "
                "program was launched with. An empty window reads as 'the blinds never closed', "
                "which is the favourable case and is not what a period nothing was measured in "
                "produced."
            )
        # **The area selects the band, so a nonsense area is a nonsense trigger, silently.**
        # `blinds_close` falls through to the `< 200 sf` row -- "more than 1 point in sun" -- for
        # any area below 200, and 0.0 and -1.0 are both below 200. That is the strictest row in the
        # table, so the substitution would not even look wrong: it would close blinds early, all
        # year, on every project whose area failed to arrive.
        if not isfinite(args.area_sf) or args.area_sf <= 0:
            raise ValueError(
                f"--area-sf must be finite and positive, not {args.area_sf!r}. "
                + (
                    "It is the analysis area this schedule records (the LM-83-22 glossary's points "
                    "times spacing); LM-83-22 section 4.6 closes on more than 2 % of the points at "
                    "every area, but an area that measured nothing is not one to record."
                    if edition == LM83_22 else
                    "It selects which row of LM-83-12 section 2.2.6's table decides every hour, "
                    "and the rows span an order of magnitude -- from 'more than 1 point in sun' "
                    "below 200 sf to 'more than 2 % of points' at 1 000 sf and above."
                )
            )
        groups = _parse_groups(args.group)
        automated_groups = _parse_groups(args.automated_group, automated=True)
        if not groups and not automated_groups:
            raise ValueError(
                "no --group and no --automated-group was given. A schedule over no window group is "
                "a year in which no blind ever closed, which is the favourable case and is not "
                "what an empty argument list measured."
            )
        manual_names = {name for name, _path in groups}
        automated_names = {name for name, _path in automated_groups}
        both = sorted(manual_names & automated_names)
        if both:
            raise ValueError(
                f"window group(s) {both!r} were given as both a --group and an --automated-group. "
                "An automated group's blinds follow its own schedule and the closing order never "
                "decides them; a group in both would be scanned and bound at once."
            )
        # **Parsed with `rad_engine.blend`'s parser, not a second one** (ruling R30): the digest's
        # own colon is what makes this fiddly, and two answers to "where does the digest start"
        # agree only until one is edited. See `parse_operand_digests` for the three readings that
        # were weighed and why the digest is the untouched remainder.
        group_digests = parse_operand_digests(
            args.group_digest, flag="--group-digest", fields=1
        )
        refuse_unmatched_digests(
            group_digests,
            keys=[name for name, _path in (*groups, *automated_groups)],
            flag="--group-digest",
            operand_flag="--group",
        )
        # R870: each bound schedule names an automated group and carries its sealed digest.
        schedules = _parse_groups(args.automated_schedule, schedules=True)
        orphans = sorted({name for name, _path in schedules} - automated_names)
        if orphans:
            raise ValueError(
                f"--automated-schedule named {orphans}, for which no --automated-group was given. "
                "A schedule binds an automated group's states; with no such group it would decide "
                "nothing, and the group it was meant for would be scheduled without it."
            )
        schedule_digests = parse_operand_digests(
            args.automated_schedule_digest, flag="--automated-schedule-digest", fields=1
        )
        refuse_unmatched_digests(
            schedule_digests,
            keys=[name for name, _path in schedules],
            flag="--automated-schedule-digest",
            operand_flag="--automated-schedule",
        )
        undigested = sorted({name for name, _path in schedules} - set(schedule_digests))
        if undigested:
            raise ValueError(
                f"the automated schedule(s) for {undigested} were given with no "
                "--automated-schedule-digest. A bound schedule is read only against the digest "
                "sealed when it was bound, so a file changed since then cannot decide the hours."
            )
        bound = _read_automated_states(
            schedules, digests=schedule_digests, n_hours=args.n_hours
        )
        automated = {
            name: bound.get(name, (BLIND_CLOSED,) * args.n_hours)
            for name in sorted(automated_names)
        }
        # **One enumeration, and this is the line that makes it one.** `order.names` and the
        # per-column group map are both built from `groups`, so a group cannot be in one and not
        # the other -- the mismatch `group_schedule`'s ghost-group and missing-group refusals catch
        # from below, and which nothing above them prevented (Task 3's review).
        order = ClosingOrder(
            names=tuple(name for name, _path in groups), substituted=args.order_substituted
        )
        payload = _derive(
            [*groups, *automated_groups],
            order=order,
            n_points=args.n_points,
            n_hours=args.n_hours,
            area_sf=args.area_sf,
            digests=group_digests,
            edition=edition,
            automated=automated,
        )
        if automated:
            payload["automated_sources"] = {
                name: "bound_schedule" if name in bound else "held_closed" for name in automated
            }
        _write_schedule(payload, out=args.out)
    except (ValueError, OSError, MatrixFileUnreadableError) as error:
        # `sys.stderr.write`, not `print(..., file=sys.stderr)`: check 0.2
        # (`tests/test_protocol.py::TestCheck02NoPrintNoInheritedStdout`) walks every module under
        # `src/` and fails on any call to `print`, without looking at its `file=` keyword.
        sys.stderr.write(f"rad_engine.schedule: {error}\n")
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised as a spawned process, not in-process
    raise SystemExit(main())
