"""Sum the per-group, per-state annual results the blind schedule selected.

**A process, not a hook.** `Step` is one spawned program and `Step.reuse`'s docstring records that
the worker offers no per-step Python hook (ruling R44). This module is a program: it has an argv, a
DEVNULL stdin, a declared stdout and a declared artifact, exactly as `rcontrib` does. R44's property
is unchanged -- nothing here is a callback and nothing is carried on `Step` the durable record
cannot hold.

**It owns no arithmetic.** `rad_core.blinds.blend_row` decides every value; this module opens files,
streams rows and writes them. The split is the layer rule (CONTEXT.md): the thing that could be
wrong about lighting lives where it can be tested with no binaries installed. Every refusal
`blend_row` makes -- a missing `(group, state)` pair, a row of the wrong length, a non-finite cell,
a duplicated group, a contribution for a group the schedule does not name -- is reached by calling
it and letting it raise, never by a second copy here.

**Per row, because the caller streams.** A materialised annual matrix is ~607 MB at the 456-point
office grid and tens of gigabytes at `GRID_VERY_LARGE`, which is the exact thing
`rad_engine.results.iter_annual_lux_rows` exists to prevent. Every contribution is advanced in
lockstep, one row at a time, and at most one row per contribution is ever held.

**Atomic.** The matrix is written to a sibling temporary file and renamed only after the last row
and after every contribution has been checked to be exhausted. A crash mid-write must not leave a
partial matrix under the final name, because the seal would register it and every reader after that
would stream a truncated year as a whole one.

**Its own failure mode, which is the one this module is built against.** A helper that exits 0
having written a well-formed matrix with a wrong answer is engine invariant 3 with a new author --
and the author is new: everything else this repo spawns is a Radiance binary, and this is the first
spawned program we wrote ourselves. So the header declares `NROWS`, `NCOLS`, `NCOMP=1`,
`FORMAT=ascii` and the schedule's digest.

**What actually checks those declarations, corrected Phase 7d-1 Task 15b.** This paragraph used to
say *"Task 12's seal refuses a matrix whose declared shape disagrees with its operands"*, and that
is false: `rad_engine.project.register_artifact` does containment, `path.is_file()`, `check_edges`
and `_refuse_an_empty_ase_matrix` -- whose own docstring says it is keyed on `kind == "asemx"` and
on nothing else -- and it never reads the matrix's **header**, for this kind or any other.

**It does open the file, and the first version of this correction said otherwise** (Task 15b fix
round 2). `content_digest=file_digest(path)` hashes every byte on every seal, and
`_refuse_an_empty_ase_matrix` scans the cells through `coefficient_matrix_is_all_zero`. Neither
parses a header: nothing at seal time reads `NROWS`, `NCOLS`, `NCOMP`, `FORMAT` or
`SCHEDULE_DIGEST`, so nothing there can notice a declaration disagreeing with anything. The
conclusion is unchanged; the reason it holds is *no header parse*, not *no read*.

The shape is checked one layer up at **read** time (`rad_mcp.results_tool`, `NROWS` against the
`n_points` the manifest *recorded*, and an upper bound on `NCOLS`), which is a comparison against a
record rather than against the operands, and it happens when a caller asks rather than when the
step seals. The digest half is checked by `compare_schedule_digest` below, called from
`rad_mcp.results_tool._stale_blend_advisories`, reached from `_resolve` on **every `results` action
except `blind_schedule`** -- which returns above the `_resolve` call because it reads `illdir:` and
no chain result, so it opens no blend to compare. (Fix round 2, N5: this said *"every"* action,
which is wrong by that one, and the phrasing was inherited verbatim from the review's own findings
file. Whole-branch review, F8: before that, it said the opposite -- *"which has no production
caller until Phase 7d-1 Task 15a wires it"* -- but that wiring landed and the sentence did not
follow it. A blend older than its schedule is caught, not served silently.) So the honest statement
of this module's defence is that the declarations are what *make* those later checks possible, not
that a seal enforces them: the checks this helper itself performs -- the operand shapes, the exhaustion of
every reader, the finiteness of every sum, and the write-to-sibling-then-rename -- are the ones
that run at the moment the matrix is produced.

`NCOMP=1` because `blend_row` sums **lux**, which `iter_annual_lux_rows` has already converted;
writing three components would invite a second application of the 179 lm/W factor downstream.
`ill5:` has been `NCOMP=1` since Phase 7b Task 1, and the reader this matrix is published through --
`iter_verified_annual_lux_rows`, which `rad_mcp.results_tool` actually imports (whole-branch review,
F6: this sentence named `iter_annual_lux_rows`, which `results_tool` has zero code references to;
the premise was wrong, the conclusion below survives because both readers share
`_refuse_unreadable_annual_header`) -- reads `NCOMP in (1, 3)` -- so this introduces no new
component regime.

**Nothing writes to stdout.** The matrix goes to `--out`; stdout stays the job log, and on stdio it
is the JSON-RPC transport. Refusals go to stderr, where the job supervisor classifies them and
carries the tail on the job record.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from math import isfinite
from pathlib import Path
from typing import IO, Final

from rad_core.blinds import blend_row

from .annual import MatrixFileUnreadableError
from .project import bytes_digest, fsync_directory
from .results import AnnualMatrix, iter_verified_annual_lux_rows
from .safe_io import open_regular_file, open_staging_file

__all__ = [
    "HelperArgumentParser",
    "SCHEDULE_DIGEST_KEY",
    "SCHED_DIGEST_OPERAND_KEY",
    "ScheduleDigestCheck",
    "compare_schedule_digest",
    "main",
    "parse_operand_digests",
    "refuse_unmatched_digests",
]

#: A sealed digest as it travels on argv and as `register_artifact` records it: the literal
#: `sha256:` prefix followed by 64 lowercase hex characters. **The prefix is carried, never
#: stripped and re-added** -- see `parse_operand_digests` for the measurement that decided it.
_DIGEST_TOKEN = re.compile(r"sha256:[0-9a-f]{64}\Z")


class HelperArgumentParser(argparse.ArgumentParser):
    r"""An `ArgumentParser` for a spawned Python helper: its refusals **raise** rather than exit 2.

    **`main`'s own docstring states the rule this class exists to make true of the argv too**:
    *"Two shapes of the same failure must not exit two different ways."* That paragraph was written
    about a missing contribution versus a missing schedule, and measured on this machine
    (Phase 7d-1 Task 11, fix round 1) the argv itself broke it:

    | argv | exit | message |
    |---|---|---|
    | `--n-points -1` | **1** | `rad_engine.blend: --n-points must be positive, not -1` |
    | `--n-points abc` | **2** | argparse's usage block |
    | `--no-such-flag` | **2** | argparse's usage block |

    Same defect -- an argument that cannot be used -- and the job supervisor was handed two exit
    codes and two message shapes for it. `rad_engine.schedule` has the sharper version: `--area-sf
    0.0` exits 1 naming the LM-83 band it would have selected, while `--area-sf -inf` exits 2,
    because argparse's negative-number matcher is `^-\d+$|^-\d*\.\d+$` and `-inf` does not match
    it, so the token is read as an option string.

    Raising `ValueError` puts every malformed argv through each helper's single `except` clause,
    which is what prefixes the message with the module's own name and returns 1. `--help` is
    untouched: that goes through `parser.exit(0)`, not through `error`, and `SystemExit` is not a
    `ValueError`.

    **Shared, and it lives here rather than in `rad_engine.schedule`, because this is the module
    whose docstring the rule comes from.** Two spawned helpers now hold the property, from one
    class, rather than one of them holding it and the other being cited as the authority for it
    (ruling R138's sweep).
    """

    def error(self, message: str) -> None:  # type: ignore[override]
        # `format_usage()` already returns a string that begins with "usage: " (and ends with a
        # newline) -- prefixing it with another "usage: " here produced `(usage: usage: ...)`,
        # fixed in Phase 7d-1 Task 11's fix round 2.
        raise ValueError(f"{message} ({self.format_usage().strip()})")

#: How many bytes of schedule JSON this helper will read before refusing.
#:
#: **A ceiling, not a tuning knob**, and the same reason `rad_engine.results._MAX_HEADER_BYTES` is
#: one: the schedule is read whole into memory (it has to be -- it is JSON, and it is hashed and
#: parsed from the same buffer), so an unbounded read at a path this process was merely *told* about
#: is a `/dev/zero` away from consuming the machine. `safe_io.open_regular_file` refuses the
#: character device itself, but not a very large regular file.
#:
#: The arithmetic for the bound: one hour of one group costs `"closed", ` -- ten bytes at the
#: widest state name LM-83 uses. A full year is 8 760 hours, so ~88 KB per window group, and 8 MiB
#: is ~95 groups of a full annual schedule. Two orders of magnitude above any schedule this server
#: produces, and far below the size at which reading one costs anything.
_MAX_SCHEDULE_BYTES: Final[int] = 8 * 1024 * 1024

#: The header this module writes. `SCHEDULE_DIGEST` is the one field Radiance itself never writes,
#: and it is what makes the matrix say which schedule decided it -- `rad_engine.results
#: .AnnualMatrix.extra` is what reads it back, and `compare_schedule_digest` below is the function
#: that compares it against the schedule a project currently holds -- **with no production caller
#: until Phase 7d-1 Task 15a wires one**, so on this commit the field is written, readable and
#: compared by nothing.
_NCOMP: Final[int] = 1
_FORMAT: Final[str] = "ascii"

#: The header key the digest is stamped under. **One spelling, two readers** (`chain.blind_schedule_
#: id`'s rule, applied to a header field): `_write_blend` writes it and `compare_schedule_digest`
#: reads it back, and a rename that moved one and not the other would not crash -- it would make
#: every comparison read `None` and report "this blend declares no schedule", forever and silently.
SCHEDULE_DIGEST_KEY: Final[str] = "SCHEDULE_DIGEST"

#: The key half of the `--sched-digest KEY:sha256:<hex>` token. **One spelling, two writers**, the
#: same rule `SCHEDULE_DIGEST_KEY` states for the header field: `rad_engine.chain.blend_steps` mints
#: the token with it and `main` looks the digest up under it, and a rename that moved one and not
#: the other would make `refuse_unmatched_digests` refuse every reuse run -- loudly, which is the
#: right direction, and is why this is a shared constant rather than two string literals.
#:
#: There is exactly one schedule operand, so the key carries no information a caller varies; it
#: exists because `parse_operand_digests` is shared with `--group-digest` and `--contribution-
#: digest` (R30), and a keyless fourth spelling of "where does the digest start" is the thing that
#: parser was written to prevent.
SCHED_DIGEST_OPERAND_KEY: Final[str] = "sched"


def _parse(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = HelperArgumentParser(
        prog="rad_engine.blend",
        description="Sum the per-group annual contributions the blind schedule selected.",
    )
    parser.add_argument("--sched", required=True, type=Path, help="the schedule JSON to read")
    parser.add_argument(
        "--sched-digest",
        action="append",
        default=[],
        metavar="sched:sha256:<hex>",
        help=(
            "the digest the manifest sealed for --sched, checked against the one buffer the "
            "schedule is parsed from; at most once. Omitted for a schedule this job rebuilds, "
            "whose sealed digest is stale by design -- see parse_operand_digests"
        ),
    )
    parser.add_argument("--out", required=True, type=Path, help="where the blended matrix is written")
    parser.add_argument("--n-points", required=True, type=int, help="sensors in the grid (NROWS)")
    parser.add_argument("--hours", required=True, type=int, help="hours in the window (NCOLS)")
    parser.add_argument(
        "--contribution",
        action="append",
        default=[],
        metavar="GROUP:STATE:PATH",
        help="one annual matrix for one window group in one blind state; repeatable",
    )
    parser.add_argument(
        "--contribution-digest",
        action="append",
        default=[],
        metavar="GROUP:STATE:sha256:<hex>",
        help=(
            "the digest the manifest sealed for one --contribution, checked on the same descriptor "
            "its rows are read from; repeatable. Omitted for an operand this job rebuilds, whose "
            "sealed digest is stale by design -- see parse_operand_digests"
        ),
    )
    return parser.parse_args(argv)


def _read_bounded(path: Path) -> bytes:
    """The whole file, or a refusal -- through `safe_io` (R48) and under `_MAX_SCHEDULE_BYTES`.

    One byte past the ceiling is read on purpose: a read of exactly the ceiling cannot tell a file
    that fits from one that was cut off at it.
    """
    with open_regular_file(path, "rb") as handle:
        data = handle.read(_MAX_SCHEDULE_BYTES + 1)
    if len(data) > _MAX_SCHEDULE_BYTES:
        raise ValueError(
            f"the schedule at {path} is larger than {_MAX_SCHEDULE_BYTES} bytes, which is about "
            "two orders of magnitude more than a full-year schedule for every window group a "
            "project can hold. This is not a schedule this helper can read."
        )
    return data


def _read_schedule(
    path: Path, *, hours: int, expected_digest: str | None
) -> tuple[dict[str, tuple[str, ...]], str]:
    """The per-group hourly states, and the `sha256:` digest of the bytes they were read from.

    **One read, hashed and parsed from the same buffer.** `file_digest(path)` followed by a second
    open to parse would hash one set of bytes and use another, and a writer that swaps the file
    between the two opens passes the gate -- ruling R97's own reasoning, and the reason
    `rad_engine.project.bytes_digest` exists. The digest goes into the header verbatim, in the same
    `sha256:<hex>` format the manifest records for every input, so `compare_schedule_digest` is a
    string comparison against a digest derived the same way rather than two formats reconciled at
    the point of use.

    ## The sealed digest, and why stamping one is not checking one (ruling R255-31)

    `expected_digest` is the `sha256:` string the manifest sealed for `sched:<grid>`, or `None` for
    a schedule this job is about to rebuild -- the same silence `_open_contributions` takes for a
    rebuilt contribution. **It is compared before the buffer is parsed**, so a schedule whose bytes
    drifted decides no hour of the blend at all.

    **Until Phase 7d-2's fix wave this function hashed and never compared.** The digest below was
    stamped into the blended matrix's header and read back later by `compare_schedule_digest` --
    which compares the header's value against the schedule **on disk now**, so a schedule edited
    *before* the blend ran was stamped with its own tampered digest and agreed with itself forever.
    Verifying every contribution and not the file that selects among them verifies the ingredients
    and not the recipe: `_read_schedule` decides, for every hour and every window group, **which**
    contribution is summed.

    **This comparison is TOCTOU-proof, unlike the matrix readers', and for a structural reason.**
    `_read_bounded` returns the whole schedule in one buffer; the digest, the comparison and the
    parse are all over that same `bytes` object, so there is no interval for a writer to act in --
    not even the narrowed hash-then-rewind window `iter_verified_annual_lux_rows` still carries.
    The bound that makes it possible is `_MAX_SCHEDULE_BYTES`, which a matrix has no equivalent of.

    **The sentence this replaces said Task 12's seal compares the two strings.** It shipped false:
    Task 12 landed and compared nothing, and the field had no production reader at all until
    ruling R191.

    **The shape is Task 11's, not the Task 10 brief's.** The brief's own test wrote the group map at
    the top level; the plan pins Task 11's writer to `payload["states"]` beside `columns` and
    `hours_closed`, and Task 11 is the writer. A reader built against the brief's shape would have
    left Task 12 wiring two files that do not fit.

    Every refusal here is a schedule that would otherwise decide an hour by accident: a missing
    `states` map would blend nothing, a short one would raise `IndexError` past `main`'s own
    handler, and a long one means the schedule and the matrices were built over different windows.
    """
    data = _read_bounded(path)
    digest = bytes_digest(data)
    if expected_digest is not None and digest != expected_digest:
        raise ValueError(
            f"the schedule at {path} no longer matches the bytes that were sealed for it: the "
            f"record holds {expected_digest}, the file hashes to {digest}. This schedule decides "
            "which window-group contribution is summed in every hour, so blending it would "
            "publish a matrix nobody authored the blind states for."
        )
    payload = json.loads(data.decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(
            f"the schedule at {path} is a {type(payload).__name__}, not a JSON object. A schedule "
            "is the object rad_engine.schedule writes, carrying its per-group states."
        )
    states = payload.get("states")
    if not isinstance(states, dict):
        raise ValueError(
            f"the schedule at {path} has no `states` object (found {type(states).__name__}). "
            "Without it no window group has an hourly state and the blend would sum nothing, "
            "which publishes a dark room rather than a refusal."
        )
    schedule: dict[str, tuple[str, ...]] = {}
    for group, row in states.items():
        if not isinstance(group, str) or not group:
            raise ValueError(
                f"the schedule at {path} names a window group as {group!r}, which is not a name."
            )
        if isinstance(row, str) or not isinstance(row, Sequence):
            raise ValueError(
                f"the schedule at {path} gives window group {group!r} a "
                f"{type(row).__name__}, not a list of hourly states."
            )
        if len(row) != hours:
            raise ValueError(
                f"the schedule at {path} covers {len(row)} hour(s) for window group {group!r} but "
                f"the blend is over {hours}. A short schedule is indexed past its end for the "
                "hours it does not cover, and a long one silently drops the hours beyond the "
                "blend -- the two files were built over different analysis windows."
            )
        for hour, state in enumerate(row):
            if not isinstance(state, str) or not state:
                raise ValueError(
                    f"the schedule at {path} gives window group {group!r} the state {state!r} in "
                    f"hour {hour}, which is not a blind state name."
                )
        schedule[group] = tuple(row)
    return schedule, digest


@dataclass(frozen=True)
class ScheduleDigestCheck:
    """What `compare_schedule_digest` found: the two digests, and why one of them is missing.

    `declared` is the `SCHEDULE_DIGEST=` the blended matrix's own header carries -- the digest of
    the schedule bytes that actually decided every hour of it. `sealed` is the digest of the
    schedule file the project holds **now**. `reason` is set, and one of the two is `None`, when
    the comparison could not be made at all.
    """

    declared: str | None
    """The blend's own header field, or `None` if it carries none."""

    sealed: str | None
    """The digest of the schedule now on disk, or `None` if it could not be read."""

    reason: str | None = None
    """Why the comparison could not be made, or `None` when both digests are in hand."""

    @property
    def mismatched(self) -> bool:
        """**True only when both digests are known and they differ** -- never on an unknown.

        The polarity is the point, and it is the opposite of `not agree`. A caller writes
        `if check.mismatched: advise(...)`, so an unreadable schedule or a header with no
        `SCHEDULE_DIGEST=` reports *nothing* rather than reporting a stale blend it has no
        evidence for -- which is the substituted-default-reported-as-a-measurement failure
        (docs/tool-surface.md, contract 5) in the shape an advisory can take. A caller that wants
        to say something about the unknown case reads `reason`, which names it.
        """
        return (
            self.declared is not None
            and self.sealed is not None
            and self.declared != self.sealed
        )


def compare_schedule_digest(header: AnnualMatrix, schedule_path: Path) -> ScheduleDigestCheck:
    """Does the blend at `header` still match the schedule at `schedule_path`?

    **The one on-disk means of catching a stale blend** (ruling R191, owner decision). `illblend:
    <grid>` names `sched:<grid>` in `built_from`, so a *rebuilt* schedule poisons the blend through
    `chain.reuse_decision` and the blend is rebuilt with it -- inside a job. Nothing covers the
    other order: a schedule re-derived by a later run, or edited, or restored from elsewhere,
    leaves a blend on disk whose every hour was decided by bytes the project no longer holds, and
    `is_reusable` cannot see it because the *artifact's* recorded digests are all still consistent
    with each other. The blend's header is the only record of which schedule it was, and this is
    the function that reads it back.

    **Advise, never refuse.** A mismatch means the blend is older than the schedule, not that it is
    unreadable: every row of it is a real sum of real matrices and its shape is intact. The caller
    turns `mismatched` into an advisory and reports the number anyway; that split is why this
    function returns a finding rather than raising.

    **Its caller is `rad_mcp.results_tool._stale_blend_advisories`** (corrected 2026-10-03, R768: this
    said "There is no such caller yet"). This function, its type and its tests ship in Phase 7d-1
    Task 15b; **Task 15a** wired it into `rad_mcp.results_tool` -- resolution happens there, and the
    advisory code is that layer's to allocate. Until it landed, a blend older than the schedule that
    decided it was served with no advisory, and `docs/tool-surface.md` said so in the same words.
    `tests/test_blend.py::TestCompareScheduleDigest::test_the_surface_says_the_check_is_unwired_
    for_exactly_as_long_as_it_is` goes red in both directions, so neither the hedge nor the wiring
    can outlive the other.

    ## Why the seam is here and not in `rad_core`

    `rad_core` is pure -- no file IO, no subprocess: text in, values out (CONTEXT.md, Layers). Both
    halves of this comparison are read off disk: the left-hand side is a `#?RADIANCE` header
    field and the right-hand side is the bytes of a JSON file, hashed. Neither could be reached
    from `rad_core` without giving it a reader, and the *policy* half -- which advisory code, what
    the message says -- is `rad_mcp`'s, which may not be imported downward. `rad_engine.blend` is
    where the digest is defined: `_write_blend` stamps it, `_read_schedule` derives it, and both
    are in this module, so a comparator anywhere else would be a second definition of what "the
    schedule's digest" means.

    ## What each argument is, and what the caller still owes

    `header` is the parsed matrix header -- `rad_engine.results.read_annual_header`'s
    `AnnualMatrix`, whose `extra` carries every `KEY=VALUE` line the header held. Passing the
    header rather than the matrix path is deliberate: a caller that has already opened the blend
    holds this object, and re-reading the file to get it back would hash one set of bytes and
    report on another.

    `schedule_path` is the sealed `sched:<grid>`'s file. **This function does not contain it.** It
    takes a bare path and owns the final-component and file-type guards only -- `open_regular_file`
    refuses a symlink, a FIFO and every other non-regular object -- exactly the split ruling R48
    left `rad_engine.sensors.write_points` and `rad_engine.run.run_to_file` in: the layer that
    holds the roots owns the parent chain, and this module is handed paths and has no roots to
    check against. The caller contains the path first.

    The schedule is read under `_MAX_SCHEDULE_BYTES` and hashed with `project.bytes_digest`, the
    same ceiling and the same function `_read_schedule` uses when the blend is written, so the two
    sides of the comparison are produced by one definition rather than by two that agree today.

    ## The three findings

    - both digests in hand and equal -- `mismatched` is `False`, `reason` is `None`;
    - both in hand and different -- `mismatched` is `True`. This is the advisory case;
    - one of them missing -- `mismatched` is `False` and `reason` names which. A header with no
      `SCHEDULE_DIGEST=` is not a blend this server wrote (or is one written before the field
      existed); an unreadable schedule is a project whose `sched:<grid>` has been removed or
      replaced by something that is not a file. Neither is evidence of staleness, and neither is
      reported as though it were.
    """
    declared = header.extra.get(SCHEDULE_DIGEST_KEY)
    if declared is None:
        return ScheduleDigestCheck(
            declared=None,
            sealed=None,
            reason=(
                f"the blended matrix header carries no {SCHEDULE_DIGEST_KEY}=, so it does not say "
                "which blind schedule decided it"
            ),
        )
    try:
        sealed = bytes_digest(_read_bounded(schedule_path))
    except (OSError, ValueError) as error:
        # `_read_bounded` raises `OSError` through `safe_io.open_regular_file` (a symlink, a FIFO,
        # a missing file, a directory) and `ValueError` past the ceiling. Both are "the schedule
        # could not be read", and neither says the blend is stale -- so both become a `reason`
        # rather than an exception the caller has to translate into one.
        return ScheduleDigestCheck(
            declared=declared,
            sealed=None,
            reason=f"the sealed schedule at {schedule_path} could not be read: {error}",
        )
    return ScheduleDigestCheck(declared=declared, sealed=sealed, reason=None)


def parse_operand_digests(
    specs: Sequence[str], *, flag: str, fields: int
) -> dict[str, str]:
    """`{"<key>": "sha256:<hex>"}` for a repeatable `--<flag> KEY:DIGEST` argument.

    Shared by this module's `--contribution-digest` (`fields=2`, key `GROUP:STATE`) and
    `rad_engine.schedule`'s `--group-digest` (`fields=1`, key `NAME`), for ruling R30's reason:
    two hand-written colon parsers are two answers to *"where does the digest start"* that agree
    only until one of them is edited.

    ## Why the digest is the REMAINDER, and not a capped split from the left

    **A digest contains a colon.** `sha256:<64 hex>` is what `register_artifact` seals and what
    `rad_engine.project.file_digest` returns, and both existing operand formats
    (`--contribution GROUP:STATE:PATH`, `--group NAME:PATH`) are colon-separated. Three readings
    were considered:

    - **Digest appended last** (`GROUP:STATE:PATH:sha256:abc...`). Rejected: the *path* is already
      the unsplit remainder in both existing formats -- `_open_contributions` and
      `schedule._parse_groups` both use `partition(":")`, so a path containing a colon works
      today. Appending after it would require a split capped from the left, and a capped split
      cannot tell `a:b:/tmp/x:y:sha256:...` (path `/tmp/x:y`) from a path `/tmp/x` with a stray
      field. That is a silent mis-read of a *path*, which is the worst thing on this argv to get
      wrong.
    - **Hex without the `sha256:` prefix, re-added here.** Rejected: it makes the wire form and the
      manifest form different strings, so a comparison against `Artifact.content_digest` needs a
      re-add step that can be forgotten in one of the two helpers -- and a forgotten re-add
      compares `abc...` against `sha256:abc...`, which never matches and refuses *every* read. The
      failure mode is a total outage dressed as drift.
    - **A separate paired flag whose value ends in the digest** -- this one. The leading `fields`
      key parts are partitioned off one at a time and **everything after them is the digest,
      untouched**, exactly the way the path is the remainder in the flags this parallels. The
      digest's own colon is therefore never a separator, no existing format changes, and a path is
      never re-parsed.

    **The prefix is required, not tolerated.** A value that does not match `sha256:<64 lowercase
    hex>` is refused here rather than passed down to fail as a mismatch, so "the planner sent the
    hex without its prefix" is distinguishable from "the bytes drifted" -- the two have opposite
    fixes, and a refusal that cannot tell them apart sends an operator to re-run a build over a
    typo in an argv builder.

    A repeated key is refused for `_open_contributions`' reason: a `dict` would take the last one
    silently, so which digest a matrix was checked against would depend on argv order.
    """
    digests: dict[str, str] = {}
    for spec in specs:
        parts: list[str] = []
        rest = spec
        for _ in range(fields):
            head, separator, rest = rest.partition(":")
            if not (separator and head):
                rest = ""
                break
            parts.append(head)
        key = ":".join(parts)
        if len(parts) != fields or not rest:
            raise ValueError(
                f"{flag} argument {spec!r} is not "
                f"{':'.join(['KEY'] * fields)}:sha256:<hex>. Every part is required: without the "
                "key nothing says which operand this digest was sealed for, and without the digest "
                "there is nothing to check the bytes against."
            )
        if not _DIGEST_TOKEN.fullmatch(rest):
            raise ValueError(
                f"{flag} argument {spec!r} carries {rest!r}, which is not a sealed digest. A "
                "digest is the literal 'sha256:' followed by 64 lowercase hex characters -- the "
                "exact string register_artifact records, carried whole rather than stripped and "
                "reassembled. A value that merely fails to match the file would be reported as "
                "drifted bytes, which is a different defect with a different fix."
            )
        if key in digests:
            raise ValueError(
                f"{flag} was given twice for {key!r}. Only one of the two could be checked "
                "against, and which one would depend on the order they were passed in."
            )
        digests[key] = rest
    return digests


def refuse_unmatched_digests(
    digests: Mapping[str, str], *, keys: Sequence[str], flag: str, operand_flag: str
) -> None:
    """Refuse a digest whose key names no operand this program was given.

    **A planner that disagrees with itself, not a caller mistake.** Both digest flags are emitted
    by `rad_engine.chain` from the same loop that emits the operand they pair with, so a digest
    with no operand means the two loops have come apart -- and the failure it would otherwise
    cause is silence: the digest is simply never consulted, and the matrix it was meant to verify
    is read unchecked. Refusing is the only outcome that cannot be mistaken for a verified read.

    The converse is **not** refused: an operand with no digest reads with `content_digest=None`,
    which is `iter_verified_annual_lux_rows`' documented silence for an artifact this job is about
    to rebuild (so the manifest's digest is stale by design) or one sealed before digests were
    recorded.
    """
    unmatched = sorted(set(digests) - set(keys))
    if unmatched:
        raise ValueError(
            f"{flag} named {unmatched}, for which no {operand_flag} was given. A digest with no "
            "operand verifies nothing, and the matrix it was meant to check would have been read "
            f"unverified. The {operand_flag} and {flag} arguments are emitted by one loop in "
            "rad_engine.chain and must name the same operands."
        )


def _open_contributions(
    specs: Sequence[str], *, expected_points: int, digests: Mapping[str, str] | None = None
) -> dict[tuple[str, str], Iterator[list[float]]]:
    """One streaming reader per `GROUP:STATE:PATH`, keyed the way `blend_row` indexes them.

    `expected_points` is passed to every reader: `NROWS` alone cannot detect a matrix belonging to a
    different grid, because two integers are consistent with any story about them, and the count the
    step was launched with is the independent third statement that makes the check able to fail.

    A repeated `(group, state)` is refused rather than overwritten. A `dict` would take the last
    path silently, so which file was summed would depend on argv order and nothing anywhere would
    record that the other one existed.

    ## The sealed digest, per contribution

    `digests` maps `"GROUP:STATE"` to the `sha256:` string the manifest recorded for that operand,
    and the reader is `iter_verified_annual_lux_rows` -- which hashes from the descriptor it then
    streams the rows from. Until Phase 7d-2 this module read every contribution through
    `iter_annual_lux_rows` with **no digest check at all**: not a two-pass check, none. A
    per-(group, state) matrix edited on disk after its run was summed into `illblend:<grid>` and
    published as measured, and the only thing that ever noticed was `results`' own check, one
    artifact downstream, on a file whose numbers had already absorbed it.

    **Narrowed, not closed.** Holding the descriptor defeats *replacement* of the file between the
    hash and the read -- a rename, an unlink-and-recreate -- because an fd binds the inode. It does
    **not** defeat an *in-place* rewrite of that same inode between the hash and the rewind. See
    `iter_verified_annual_lux_rows`.

    **And "replacement defeated / in-place not defeated" is not an exhaustive enumeration.** Two
    further residual classes are disclosed in full at the published surface (`docs/tool-surface.md`
    §14, *"Two more residual classes"*; whole-branch review, F7): the digest's **trust root** is
    the same unsigned `manifest.json` that an attacker able to perform the in-place write already
    owns, so the read reports as verified with no race at all; and a **directory symlink planted in
    the parent chain** substitutes the path upstream of both seeks, which fails closed with a
    digest present and open without one. Neither is fixed; both are named so the pair above stops
    reading as complete.

    **A contribution with no entry in `digests` is read with `content_digest=None`**, the same
    documented silence `results_tool._lux_rows` takes: `rad_engine.chain` emits a digest only for
    an operand this job will not rebuild, because the manifest's digest for an operand a step
    earlier in the same job is about to overwrite is stale by design. **What follows** (whole-branch
    review, m13): on a fresh `run_annual(blind_control="lm83")` every contribution is produced by an
    earlier step of that same job, so `digests` is empty and no contribution is verified at all --
    this check covers the **reuse path** only.
    """
    digests = digests or {}
    opened: dict[tuple[str, str], Iterator[list[float]]] = {}
    for spec in specs:
        group, group_sep, rest = spec.partition(":")
        state, state_sep, raw_path = rest.partition(":")
        if not (group_sep and state_sep and group and state and raw_path):
            raise ValueError(
                f"contribution {spec!r} is not GROUP:STATE:PATH. All three parts are required: "
                "without the state, nothing says which blind position this matrix was computed for."
            )
        key = (group, state)
        if key in opened:
            raise ValueError(
                f"two contributions were given for window group {group!r} in state {state!r}. "
                "Only one of them could be summed, and which one would depend on the order they "
                "were passed in."
            )
        opened[key] = iter_verified_annual_lux_rows(
            Path(raw_path),
            expected_points=expected_points,
            content_digest=digests.get(f"{group}:{state}"),
        )
    if not opened:
        raise ValueError(
            "no --contribution was given. A blend with no operand writes a matrix of zeros, which "
            "reads downstream as a room that measured no light rather than as a run that summed "
            "nothing."
        )
    return opened


_EXHAUSTED: Final[object] = object()


def _refuse_unconsumed(
    contributions: Mapping[tuple[str, str], Iterator[list[float]]], *, n_points: int
) -> None:
    """Refuse a contribution that still has rows left after `n_points` of them were blended.

    **The loop's own stopping condition hides the reader's overlong-body check.** `iter_annual_lux_
    rows` refuses a matrix carrying more data rows than its header declares -- but only when it is
    asked for the row that proves it, and a loop that stops at `n_points` never asks. A matrix with
    a fourth sensor's data in it would then be blended as a three-sensor grid and sealed as one.
    """
    for (group, state), rows in contributions.items():
        if next(rows, _EXHAUSTED) is not _EXHAUSTED:
            raise ValueError(
                f"the contribution for window group {group!r} in state {state!r} carries more "
                f"than the {n_points} sensor row(s) the blend was launched for. The matrix and "
                "the grid have drifted apart, so the extra rows belong to sensors this blend has "
                "no schedule for."
            )


def _write_rows(
    handle: IO[bytes],
    *,
    contributions: Mapping[tuple[str, str], Iterator[list[float]]],
    schedule: Mapping[str, Sequence[str]],
    n_points: int,
    hours: int,
) -> None:
    """Blend and write every sensor row, advancing all contributions in lockstep.

    `order` is the schedule's own groups, sorted -- a deterministic sequence of the names to sum,
    not LM-83's closing order. The closing order decided *which state each group is in*, and that
    decision is already baked into `schedule`; the sum over groups is commutative, and sorting is
    what makes the same operands produce the same bytes twice. Passing it also gives `blend_row`
    both halves of its two-sided check for free: a group in the schedule with no contribution is a
    missing pair, and a contribution for a group the schedule does not name is a ghost.
    """
    order = tuple(sorted(schedule))

    def states_at(group: str, hour: int) -> str:
        return schedule[group][hour]

    for sensor in range(n_points):
        # One row from **every** contribution before any of them is advanced twice. This
        # comprehension is the lockstep: a loop that drained one reader before starting the next
        # would materialise a whole matrix, and one that advanced a subset would blend sensor `n`
        # of one file against sensor `n + 1` of another.
        row_by_key = {
            key: _next_row(reader, key=key, sensor=sensor)
            for key, reader in contributions.items()
        }
        blended = blend_row(row_by_key, states_at=states_at, order=order, hours=hours)
        for hour, value in enumerate(blended):
            if not isfinite(value):
                # `blend_row` refuses non-finite *inputs*; nothing there looks at the sum, and two
                # finite cells can overflow. Writing `inf` would be a well-formed matrix that its
                # own reader then refuses -- an error moved from the writer, which knows what
                # happened, to every reader after it, which does not.
                raise ValueError(
                    f"the blended value for sensor {sensor} in hour {hour} is {value!r}, which is "
                    "not finite. The contributions were each finite, so the sum overflowed: this "
                    "is not an illuminance and every threshold downstream would compare False "
                    "against it."
                )
        handle.write((" ".join(repr(value) for value in blended) + "\n").encode("ascii"))


def _next_row(rows: Iterator[list[float]], *, key: tuple[str, str], sensor: int) -> list[float]:
    """One sensor row, or a refusal naming which contribution ran out.

    `iter_annual_lux_rows` raises its own truncation refusal when a body is shorter than its header
    claims, so this is the case where a reader stops without either refusing or yielding. It is
    reachable only from a reader that does not hold that contract; a bare `StopIteration` here would
    escape `main`'s handler as a traceback rather than as a refusal.
    """
    try:
        return next(rows)
    except StopIteration:
        group, state = key
        raise ValueError(
            f"the contribution for window group {group!r} in state {state!r} ran out at sensor "
            f"{sensor}. The blend was launched for more sensors than that matrix holds."
        ) from None


def _write_blend(
    *,
    out: Path,
    contributions: Mapping[tuple[str, str], Iterator[list[float]]],
    schedule: Mapping[str, Sequence[str]],
    n_points: int,
    hours: int,
    sched_digest: str,
) -> None:
    """Write the blended matrix to a sibling temporary and rename it only once it is whole.

    **`os.replace` is the last thing that happens**, after the final row, after every contribution
    has been checked exhausted and after the bytes are on disk. A rename any earlier publishes a
    matrix that is still being written, and the seal registers whatever is at `out`: every reader
    after that streams a truncated year as a whole one. On any failure the temporary is removed and
    whatever was already at `out` -- a previous, complete result -- is untouched.

    The temporary is a *sibling* so the rename is within one filesystem, which is what makes it
    atomic; `tempfile.gettempdir()` is frequently a different mount and `os.replace` across mounts
    is a copy with a window in the middle.

    **Two paths are written here, not one, and Task 12 owns both.** This function creates (`O_EXCL`,
    through `safe_io.open_staging_file`, which first unlinks a stale regular `<out>.partial` so a
    hardlink planted there is never written through -- S1, Phase 11a fix wave) and on failure
    `unlink`s `<out>.partial` as well as
    replacing `out`. The split is the one ruling R48 left `rad_engine.sensors.write_points` and
    `rad_engine.run.run_to_file` in: the engine takes a bare path and owns the final-component and
    file-type guards -- `O_NOFOLLOW` and `S_ISREG` on both -- and the layer that holds the roots
    owns the parent chain, because this process is handed an argv and has no roots to check
    against. So the `--out` Task 12 builds must be contained before the step is submitted, and the
    containment has to cover the sibling name too: a rule written about `out` alone would say
    nothing about the file this actually creates first.

    **That containment now exists; until 2026-08-31 the paragraph above named a caller that did not
    do it.** The whole-branch security review reproduced the gap -- a directory symlink planted at
    `08_results` put a schedule file outside every allowed root, with no symlink at the final
    component, so `O_NOFOLLOW` never fired -- and graded the *docstring* the part that could not
    merge, on CONTEXT.md's own standard that a rule written in prose beside code that does not obey
    it is worse than either. The caller is `rad_mcp.annual_tool._contain_chain_directory`, and it
    contains the **directory** rather than the filename, which is how it covers the `.partial`
    sibling this function creates first: every path either helper writes is `08_results / <name>`,
    so one check answers all of them and keeps answering when the next artifact is added.
    """
    partial = out.with_name(out.name + ".partial")
    header = (
        "#?RADIANCE\n"
        f"NROWS={n_points}\n"
        f"NCOLS={hours}\n"
        f"NCOMP={_NCOMP}\n"
        f"FORMAT={_FORMAT}\n"
        f"{SCHEDULE_DIGEST_KEY}={sched_digest}\n"
        "\n"
    )
    try:
        # **The open is outside the cleanup, and that is load-bearing.** `open_staging_file` refuses
        # a symlink, a FIFO or any other non-regular object at the name (`lstat`, before anything is
        # created) and a name re-planted after its unlink (`O_EXCL`) -- and in every one of those
        # cases nothing of ours was created, because `O_CREAT | O_EXCL` on a path that is free makes
        # a regular file and none of those checks fire. Unlinking inside that failure would delete a file
        # this process did not create as a side effect of refusing to write to it.
        handle = open_staging_file(partial)
        try:
            with handle:
                handle.write(header.encode("ascii"))
                _write_rows(
                    handle,
                    contributions=contributions,
                    schedule=schedule,
                    n_points=n_points,
                    hours=hours,
                )
                _refuse_unconsumed(contributions, n_points=n_points)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(partial, out)
            # And the directory entry after the rename. `os.replace` being atomic says nothing
            # about the **name** having reached disk, so a crash here can leave the manifest --
            # whose own write *is* fsynced (`project.save_manifest`) -- naming a file that is
            # absent or is the previous run's.
            #
            # **Re-measured 2026-09-18 (whole-branch review docs commit): ten call sites, not
            # four.** The plain `grep -rn "os\.replace(" src/` this comment used to cite returns
            # eleven lines, because it also matches `render.py`'s own docstring describing its
            # call in prose; `grep -rnE '^\s*os\.replace\(' src/` excludes that line and gives
            # exactly ten: this one, `render.spawn_render`, `postprocess.spawn_postprocess` and
            # `postprocess.spawn_ra_tiff`, `jobs._atomic_write`, `project.save_manifest`,
            # `schedule._write_schedule`, `ies.write_source_cal` (added by the whole-branch fix
            # wave's code M4), `luminaire_tool._write_and_register` and
            # `geometry_tool._write_geometry_file`. This one, `_atomic_write`, `save_manifest`,
            # `_write_schedule` and `write_source_cal` fsync their parent directory immediately
            # after the rename; the other five are not re-verified here. **History of the original
            # four** (`this one, jobs.py, project.py, schedule.py`): three of them already fsynced
            # when this call was added, which is why this was then the only rename in the tree that
            # did not -- `git grep -n "os\.replace(" 5cf3d17 -- src/` returns three lines and
            # no `schedule.py` -- and `_write_schedule` was modelled on this function and has
            # fsynced from its first commit. (An earlier revision of this comment said "jobs at
            # both of its rename sites" and was wrong: `jobs.JobStore._commit`
            # fsyncs a directory after a *spawned binary wrote the file directly*, which is not a
            # rename at all. `jobs` has one `os.replace`, not two.)
            fsync_directory(out.parent)
        except BaseException:
            # `BaseException`, not `Exception`: a `KeyboardInterrupt` between two rows leaves the
            # same partial file behind as a `ValueError` does, and the job supervisor's own
            # cancellation path is exactly that signal.
            partial.unlink(missing_ok=True)
            raise
    finally:
        for rows in contributions.values():
            close = getattr(rows, "close", None)
            if close is not None:
                close()


def main(argv: Sequence[str] | None = None) -> int:
    """Blend, or refuse. Returns 0 only when a whole matrix is under `--out`.

    The `except` clause names `MatrixFileUnreadableError` explicitly: it is a `RuntimeError` and
    **not** an `OSError` (that separation is deliberate -- `rad_engine.annual` keeps "the bytes
    could not be read" apart from "the bytes are not a matrix"), so a missing or non-regular
    *contribution* would otherwise escape as a traceback while a missing *schedule*, which fails in
    `open_regular_file` as a plain `FileNotFoundError`, returned 1. Two shapes of the same failure
    must not exit two different ways.
    """
    try:
        # **Inside the `try`, so a malformed argv exits 1 like every other refusal here** --
        # `HelperArgumentParser.error` raises instead of exiting 2. See that class for the three
        # measured cases that were inconsistent before it existed.
        args = _parse(argv)
        if args.n_points <= 0:
            raise ValueError(
                f"--n-points must be positive, not {args.n_points}. A matrix declaring NROWS=0 is "
                "a well-formed file that reads as a grid nothing was measured for."
            )
        if args.hours <= 0:
            raise ValueError(
                f"--hours must be positive, not {args.hours}. A matrix declaring NCOLS=0 is a "
                "well-formed file that reads as a year nothing was measured in."
            )
        # **Parsed and matched above the read, not after it** (ruling R255-31). A `--sched-digest`
        # whose key names no operand means the planner disagrees with itself, and the failure it
        # would otherwise cause is silence -- the schedule read unverified while an unconsulted
        # digest sat in argv. Same parser as the two operand flags for R30's reason.
        sched_digests = parse_operand_digests(
            args.sched_digest, flag="--sched-digest", fields=1
        )
        refuse_unmatched_digests(
            sched_digests,
            keys=[SCHED_DIGEST_OPERAND_KEY],
            flag="--sched-digest",
            operand_flag="--sched",
        )
        schedule, sched_digest = _read_schedule(
            args.sched, hours=args.hours, expected_digest=sched_digests.get(SCHED_DIGEST_OPERAND_KEY)
        )
        contribution_digests = parse_operand_digests(
            args.contribution_digest, flag="--contribution-digest", fields=2
        )
        contributions = _open_contributions(
            args.contribution, expected_points=args.n_points, digests=contribution_digests
        )
        refuse_unmatched_digests(
            contribution_digests,
            keys=[f"{group}:{state}" for group, state in contributions],
            flag="--contribution-digest",
            operand_flag="--contribution",
        )
        _write_blend(
            out=args.out,
            contributions=contributions,
            schedule=schedule,
            n_points=args.n_points,
            hours=args.hours,
            sched_digest=sched_digest,
        )
    except (ValueError, OSError, MatrixFileUnreadableError) as error:
        # `sys.stderr.write`, not `print(..., file=sys.stderr)`: check 0.2
        # (`tests/test_protocol.py::TestCheck02NoPrintNoInheritedStdout`) walks every module under
        # `src/` and fails on any call to `print`, without looking at its `file=` keyword.
        sys.stderr.write(f"rad_engine.blend: {error}\n")
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised as a spawned process, not in-process
    raise SystemExit(main())
