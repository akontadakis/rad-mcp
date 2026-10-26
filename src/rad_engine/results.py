"""Reading an **annual** `.ill` matrix back off disk, one sensor row at a time.

**Why this module exists rather than a function in `rad_engine.annual`.** That module owns the
*building* of the 2-phase chain -- the `rfluxmtx`/`gendaymtx`/`dctimestep` argv, the receiver text,
the shape check that must run before the multiplication. This module owns the opposite direction:
turning the committed artifact back into numbers, days or weeks later, in a different process, with
no Radiance binary involved at all. `results` (docs/tool-surface.md §14) is the tool it serves, and
Phase 7b's sDA/ASE/UDI tallies (`results(action="compliance")`) read the same matrix through the same
seam.

**This module reads the five-phase `.ill` too, under `ill5:<grid>`.** Through Phase 7a the shape
depended on which method built it: the five-phase combination's final `rcollate` wrote a
**headerless**, **hour-major**, **scalar photopic lux** matrix, where the two-/three-phase chains'
`dctimestep` writes a headered, sensor-major, RGB one (`rad_engine.annual.build_rcollate_final_argv`'s
own docstring carries the measurement), and this paragraph said this reader could not open it.
Phase 7b Task 1 (ruling R98) made the five-phase `.ill` headered and sensor-major, and this module
reads it: `iter_annual_lux_rows` and `iter_verified_annual_lux_rows` take scalar lux (`NCOMP=1`, the
five-phase chain's output) and RGB irradiance (`NCOMP=3`) behind one contract. The five-phase chain
still seals its result under its own artifact id (`ill5:<grid>`), so a five-phase run cannot
overwrite a three-phase result.

## `read_ill` is the single-timestep reader and this is not a widening of it

`rad_engine.annual.read_ill` parses via `parse_dctimestep_output`, which requires **exactly three
numeric tokens per data line** -- one RGB triple per sensor, for one sky. That is correct and
complete for check 4.6's chain, where the sky is a `genskyvec` vector (`NCOLS=1`) and the result is
one moment. It is **not** what an annual `.ill` looks like, and pointing it at one raises rather than
mis-reading, which is the good failure:

    ValueError: dctimestep data line has 15 numeric token(s), not 3: '3.000000e+00 ...'

Measured against the real binary (Radiance 6.1a on this machine) rather than assumed: `dctimestep`
over a `3 x 4` DC matrix and a `4 x 5` sky matrix writes `NROWS=3 NCOLS=5 NCOMP=3` and **one line per
sensor carrying five tab-separated RGB triples** -- `3 * NCOLS` numeric tokens on every row. At
`NCOLS=8760` that is 26 280 tokens per line.

`read_ill` is deliberately left alone. Widening it to accept both shapes would give one function two
return types over two different matrix layouts, and its existing callers -- check 4.6's DC-vs-rtrace
comparison -- want the single-timestep shape specifically. A second caller with a different shape is
what would break it, so the second shape gets its own function.

## Streaming is a correctness requirement here, not an optimisation

An annual matrix is `n_points x 8760`. Materialising it as `list[list[tuple[float, float, float]]]`
costs, measured by object count:

| Grid | Triples | As Python objects |
|---|---|---|
| 456 points (the fixture `run_annual` really produces) | 3 994 560 | ~607 MB |
| 20 000 points (`GRID_VERY_LARGE`, the inline bound's own threshold) | 175 200 000 | tens of GB |

So an eager reader would exhaust memory **before** `results` could emit the `RESULT_TRUNCATED`
advisory that says the result was too large -- defeating docs/plan.md check 4.8's bounded half rather
than serving it. `iter_annual_rows` therefore yields one sensor row at a time and never holds two,
and `results(action="summary")` accumulates across all 8 760 hours without the matrix ever existing
in memory. One row is 8 760 triples (~1.3 MB), which is the real bound on this module's footprint.

**RGB stays RGB in `iter_annual_rows`.** That function's own contract is unchanged by Phase 7b:
converting to lux is a caller's job, the same split `parse_dctimestep_output` and
`rad_engine.rtrace.parse_irradiance` already hold. `iter_annual_lux_rows` (Phase 7b) is the
exception, by design rather than by drift -- it is the one reader every metric and every `results`
action goes through, so it owns the conversion for the three-component case, delegating to
`rad_core.photometry.to_lux` -- which stays the only place those weights are written, per ruling
R30 -- and passes a one-component file's values through untouched, because `rcalc` already applied
that same conversion inside the five-phase combination.

## Every open goes through `safe_io` (ruling R48)

`iter_annual_rows` takes a path that came out of a **manifest**, which ruling R50 established is
untrusted on every action that touches it. So the open is `safe_io.open_regular_file`, with
containment checked by the caller before the syscall, and never `Path.open`/`read_bytes`.

**`O_NOFOLLOW` surfaces a symlink as `ELOOP` before there is any descriptor to `fstat`**, so
`NotARegularFileError` never fires for that case -- measured on this machine:

    link.ill  -> OSError errno=62 ELOOP : [Errno 62] Too many levels of symbolic links
    fifo.ill  -> NotARegularFileError: 'fifo.ill' is not a regular file...

Both are caught here and re-raised as `MatrixFileUnreadableError`, so a raw `OSError` never reaches
a caller equipped only to shape this module's own exception types -- see that class's docstring,
*"a bare `OSError` ... is not a member of this codebase's error vocabulary"*. (**The citation of
check 0.4 that stood here until Task 14's fix round 1 was wrong**: 0.4 is *"a refusal is an error on
the wire"* -- `isError: true` -- and says nothing about paths in messages. Corrected in place rather
than deleted, because the same embellishment was copied into two other docstrings in this file.) `NotARegularFileError` is itself an `OSError`
subclass, so the single base-class `except` covers both -- and the base-class catch is also what
keeps the Windows case (`PermissionError` for a directory, where POSIX raises `IsADirectoryError`)
from leaking, exactly as `read_ill`'s own docstring reasons.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from math import isfinite
from pathlib import Path
from typing import IO

from rad_core.photometry import to_lux

from .advisories import EngineAdvisory, Severity
from .annual import (
    MATRIX_DECODE_ERRORS,
    DcMatrixHeader,
    # Re-exported rather than redefined: a caller catching "this matrix would not read" should not
    # have to know whether the read came through `annual.read_ill` or through this module. One
    # exception type for one failure mode (ruling R30).
    MatrixFileUnreadableError,
    parse_dc_matrix_header,
)
from .safe_io import handle_digest, open_regular_file

__all__ = [
    "AnnualMatrix",
    "CoefficientMatrixUnreadableError",
    "MatrixContentDriftedError",
    "MatrixDigestUnreadableError",
    "MatrixFileUnreadableError",
    "NonFiniteMatrixCellError",
    "coefficient_matrix_is_all_zero",
    "iter_annual_lux_rows",
    "iter_annual_rows",
    "iter_verified_annual_lux_rows",
    "read_annual_header",
]

logger = logging.getLogger(__name__)

#: How many bytes are pulled per read while scanning for the header's terminating blank line. The
#: same size and the same reason as `rad_engine.annual._HEADER_CHUNK_BYTES`: a real `dctimestep`
#: header is ~200-350 bytes, so this is one read in the ordinary case.
#:
#: **The chunk size alone bounds nothing** -- it says how much is read per syscall, not how much is
#: read in total, and the loop below used to have no other stop condition at all. `_MAX_HEADER_BYTES`
#: is the bound; this is only the granularity.
_HEADER_CHUNK_BYTES = 8192

#: How far into a file this module will scan for the header's terminating blank line before refusing.
#:
#: **A correctness bound, not a tuning knob, and the measurement is why.** A file with no blank line
#: anywhere -- a headerless five-phase `.ill` is exactly one -- has no terminator to find, so an
#: unbounded scan reads the whole matrix. Measured on this machine 2026-08-18, calling
#: `read_annual_header` directly (not through pytest) on headerless hour-major files of the shape
#: `rcollate -h -fa1 -ic 8760 -t` writes, one process per row so `maxrss` is the read's own peak:
#:
#: | file | wall | `tracemalloc` peak | `maxrss` | `ValueError` message |
#: |---|---|---|---|---|
#: | 1.0 MB | 0.02 s | 5.2 MB | 41 MB | 1 130 065 chars |
#: | 4.2 MB | 0.39 s | 21.7 MB | 298 MB | 4 743 409 chars |
#: | 16.8 MB | 6.77 s | 86.6 MB | 1 130 MB | 18 960 577 chars |
#: | 49.5 MB (an 8760 x 456 five-phase result) | **60.76 s** | **255.5 MB** | **2 894 MB** | **55 923 937 chars** |
#:
#: Quadratic in wall clock, linear in memory, and the message was 1.08x the file. At `GRID_VERY_LARGE`
#: (20 000 points, ~2.3 GB) that is hours of CPU before the process runs out of memory. Two things
#: caused it and both are fixed below: the join was inside the loop, and the loop had no ceiling.
#:
#: 256 KiB is three orders of magnitude above any real header -- this repo's own captured `dctimestep`
#: and `rfluxmtx` headers are 60-350 bytes, and the largest thing a Radiance header carries is the
#: `CMD` line holding the producing argv -- and two orders below the smallest file the bound exists to
#: stop. A file whose first 256 KiB contain no blank line is not a matrix this module can read, and
#: saying so costs one read rather than the whole file.
_MAX_HEADER_BYTES = 256 * 1024


@dataclass(frozen=True)
class AnnualMatrix:
    """An annual `.ill`'s shape, read from its own header -- never from a constant.

    **The distinction this type exists to keep.** `rad_mcp.annual_tool.ANNUAL_TIMESTEPS` is 8 760:
    the column count `run_annual` *intends* to produce from a full-year `.wea`. `hours` below is the
    count actually written. Ruling R47 is why they are kept apart -- `weather`'s hardcoded `8760`
    let a 24-hour file, a truncated file and an **8 784-hour leap year** all report a complete common
    year -- so every number `results` publishes about shape comes from here.
    """

    points: int
    """`NROWS` -- one row per sensor, in the grid's own point order."""
    hours: int
    """`NCOLS` -- one column per `.wea` timestep. **The real count**, not `ANNUAL_TIMESTEPS`."""
    ncomp: int
    """Components per value. Always 3 for a `dctimestep` result; `iter_annual_rows` refuses others."""
    extra: Mapping[str, str] = field(default_factory=dict, compare=False)
    """Every other ``KEY=VALUE`` line the header carried -- carried straight through from
    `rad_engine.annual.DcMatrixHeader.extra`, which holds the full reasoning including why it is
    `compare=False`.

    **The field this type gained in Phase 7d-1 (ruling R129).** `rad_engine.blend` writes
    `SCHEDULE_DIGEST=` into the header of the blended matrix so it says which blind schedule decided
    it; without somewhere to read it back, that declaration would be a string nothing could ever
    check, and the helper's own self-check would be decorative. `rad_engine.blend
    .compare_schedule_digest` is the reader: `header.extra[blend.SCHEDULE_DIGEST_KEY]` against the
    digest of the `sched:<grid>` the project holds now (ruling R191).

    **Called on every `results` action except `blind_schedule`, and has been since Phase 7d-1
    Task 15a.** `rad_mcp.results_tool._stale_blend_advisories` is called from `_resolve`,
    unconditionally -- and `_resolve` is what `blind_schedule` alone does not reach: its dispatch
    branch returns above the `_resolve` call, as `_resolve`'s own docstring says in bold, because
    the schedule is derived from `illdir:<grid>` and no chain result. Nothing is left undefended by
    that -- a `blind_schedule` call reads no blend -- but *"every action"* was wrong by one, and
    the phrasing came from the whole-branch review's own findings file (fix round 2, N5). It
    calls `blend.compare_schedule_digest(header, sealed_path)`, which reads this field. A blend
    older than its schedule is caught, not served silently, and this field is read in production,
    not by tests alone.

    **This corrects a sentence that shipped false, THREE times, not two.** It first said Task 12's
    *seal* reads the field -- Task 12 landed and read nothing. Task 15b then replaced that with *"so
    a blend older than its schedule is caught rather than served silently"*, which was the same
    defect forward-dated: the comparator existed and no caller ran it. **A third version --
    "Nothing calls it yet... until then a blend older than its schedule is still served silently,
    and this field is still read by tests alone" -- then survived past the wiring landing**, pointed
    out by the whole-branch review (F8): the sentence was pre-existing (`git log -S` finds it at
    `cb72641`, not in this branch's diff) and misstated a live defence in the module Task 1 rewrote.
    A weaker form of the identical claim sat in `blend`'s module docstring and is corrected there too.

    An equality assertion does **not** check this field -- assert `extra[...]` directly."""

    @property
    def triples(self) -> int:
        """`points * hours` -- the total RGB triples the matrix holds, and the quantity
        `results` measures its inline bound against."""
        return self.points * self.hours


def _open_matrix(path: Path) -> IO[bytes]:
    """`safe_io.open_regular_file`, with every `OSError` shaped as `MatrixFileUnreadableError`.

    The one place this module opens a file, so the `ELOOP`-vs-`NotARegularFileError` distinction in
    the module docstring is handled once rather than at each call site.
    """
    try:
        return open_regular_file(path, "rb")
    except OSError as exc:
        # Base-class catch, covering `NotARegularFileError` (a FIFO or device, caught on the
        # descriptor) and the bare `ELOOP` a symlink raises before any descriptor exists -- plus
        # the platform-dependent directory case. A raw OSError here would reach a caller equipped
        # only to shape this module's own exception types (`MatrixFileUnreadableError`'s docstring:
        # a bare `OSError` "is not a member of this codebase's error vocabulary"). This cited check
        # 0.4 until Task 14's fix round 1; 0.4 is about `isError: true`, not about paths.
        logger.info("annual matrix refused %s: %s", path, exc)
        raise MatrixFileUnreadableError(
            f"the annual matrix at {path} could not be read: {exc}. Nothing about its content has "
            "been judged -- the filesystem refused the read. Check the path exists, is a regular "
            "file rather than a symlink, directory, FIFO or device, and is readable by this "
            "server. A job step that exited before writing its stdout_path leaves exactly this.",
            path=path,
        ) from exc


def _no_header_within_bound(path: Path, scanned: int) -> ValueError:
    """The refusal `read_annual_header` raises when the scan hits `_MAX_HEADER_BYTES`.

    A `ValueError`, deliberately, and not `MatrixFileUnreadableError`: nothing was refused by the
    filesystem here. The bytes arrived and they are not a matrix header, which is the same question
    `parse_dc_matrix_header`'s own `ValueError` answers -- so `rad_mcp.results_tool`'s existing
    `ValueError` clause shapes both into one refusal rather than two.

    **The message carries the size and not the bytes.** A headerless five-phase `.ill` is the file
    this most often lands on, and quoting what was scanned would put a matrix inside an error string
    -- the second half of the same defect (`rad_engine.annual.parse_dc_matrix_header`, and
    `_HEADER_TEXT_IN_MESSAGE_LIMIT` beside it).

    **Re-worded in the Phase 7b whole-branch review: the old text was false twice over.** It told
    the caller a five-phase result *is* "headerless, hour-major" and that "reading one back is
    Phase 7b's" -- but Phase 7b Task 1 made the five-phase `.ill` headered and sensor-major, and
    Phase 7b reads it. This is the exact message a caller hits on an `ill5:` sealed *before* that
    change, so it now names that stale file as what it is and says what to do about it, rather
    than telling them the feature they are already using is not built.
    """
    return ValueError(
        f"the matrix at {path} has no header: the first {scanned} bytes contain no blank line "
        "terminating a `#?RADIANCE` block, so there is no NROWS/NCOLS/NCOMP to read a shape from. "
        "The scan stopped there rather than reading the rest of the file. Every chain this server "
        "runs seals a headered, sensor-major matrix, the five-phase one included -- so a file of "
        "this shape is a result sealed by an older build of this server, before the five-phase "
        "`.ill` gained its header. Re-run run_annual for this grid to seal a readable one."
    )


def read_annual_header(path: Path) -> AnnualMatrix:
    """The `.ill`'s shape, read from its own header without touching the data rows.

    Reads in `_HEADER_CHUNK_BYTES` chunks and stops at the header's terminating blank line, so
    learning the shape of an `n_points x 8760` matrix costs one read rather than the whole file --
    the same contract `rad_engine.annual.read_matrix_header` holds, over `safe_io` (R48) and
    returning this module's `AnnualMatrix` rather than a `DcMatrixHeader`.

    **It does not validate the body, and that is deliberate.** A header claiming `NROWS=100000` over
    three real rows parses cleanly here and reports 100000; `iter_annual_rows` is what refuses that
    file, because reading the shape and reading the data are separate questions. A caller that wants
    both wants both functions -- and `results` calls this one first precisely so it can decide
    whether to inline *before* paying to read anything.

    **The scan is bounded at `_MAX_HEADER_BYTES` and the join is outside the loop** -- see that
    constant for the measurement that forced both. A file whose first `_MAX_HEADER_BYTES` carry no
    blank line raises `ValueError` having read exactly that much, rather than consuming the whole
    matrix at O(n^2) to reach the same conclusion. A file *shorter* than the bound is still accepted
    whole if its fields are complete, which is the unterminated-at-EOF case `read_matrix_header`
    documents: the ceiling changes what happens to files it could never finish reading, and nothing
    about the files it already read to the end.

    Raises `MatrixFileUnreadableError` on a filesystem refusal (never a bare `OSError`) and
    `ValueError` -- from the bound here, or from `parse_dc_matrix_header` when the header is
    incomplete.
    """
    path = Path(path)
    with _open_matrix(path) as handle:
        return _read_header_from(handle, path=path)


def _read_header_from(handle: IO[bytes], *, path: Path) -> AnnualMatrix:
    """`read_annual_header`'s scan, over a descriptor the caller opened and still holds.

    Split out so the path form and `iter_verified_annual_lux_rows` share **one** parse rather than
    two that agree today (ruling R30). `path` is carried for the refusal messages only -- nothing
    here re-opens it.

    **Reads from the handle's current position**, which the caller owns: `read_annual_header` hands
    over a freshly opened descriptor, and `iter_verified_annual_lux_rows` rewinds to 0 after its
    digest pass. A caller that has already consumed bytes and does not seek back gets a header
    parsed from the middle of the file, which is a refusal, not a wrong number.
    """
    chunks: list[bytes] = []
    scanned = 0
    try:
        # `carry` is the single byte that could hold the first half of a `\n\n` straddling a
        # chunk boundary -- the reason the old loop re-joined every chunk read so far, which is
        # what made this scan quadratic. One byte of overlap answers the same question in O(1).
        carry = b""
        while True:
            chunk = handle.read(_HEADER_CHUNK_BYTES)
            if not chunk:
                break
            chunks.append(chunk)
            scanned += len(chunk)
            if b"\n\n" in carry + chunk:
                break
            if scanned >= _MAX_HEADER_BYTES:
                raise _no_header_within_bound(path, scanned)
            carry = chunk[-1:]
    except MatrixFileUnreadableError:
        raise
    except OSError as exc:
        logger.info("annual matrix header refused %s: %s", path, exc)
        raise MatrixFileUnreadableError(
            f"the annual matrix at {path} could not be read: {exc}.", path=path
        ) from exc
    header_bytes = b"".join(chunks).split(b"\n\n", 1)[0]
    return _as_annual(parse_dc_matrix_header(
        header_bytes.decode("utf-8", errors=MATRIX_DECODE_ERRORS)
    ))


def _as_annual(header: DcMatrixHeader) -> AnnualMatrix:
    return AnnualMatrix(
        points=header.nrows, hours=header.ncols, ncomp=header.ncomp, extra=header.extra
    )


class NonFiniteMatrixCellError(ValueError):
    """A cell of an annual illuminance matrix is not a finite illuminance -- either the token the
    file carries is `NaN` or an infinity, or the lux it converts to is.

    **Raised from the row parsers, which is the one seam every metric and every `results` action
    goes through** -- `iter_annual_lux_rows`' own docstring's words -- **and, since fix round 2 of
    Phase 7d-2, from the conversion as well.** Three raise sites, all in this module, enumerated by
    `grep -n "^ *raise _refuse_" src/rad_engine/results.py`: `_parse_row` (`_refuse_non_finite`),
    `_parse_lux_row` (`_refuse_non_finite`) and `_stream_lux_from`
    (`_refuse_overflowed_lux`) -- the third on the **converted** value, which is the one
    place a triple of finite components can become a non-finite lux. The alternative considered and
    rejected was a check in each consumer: `rad_mcp.results_tool` holds **four** unrelated
    cell-level readers (`_compliance_lm83`'s tally, `_metrics`' tally, `_in_sun_counts_by_column`
    and `_Accumulator`), and a guard placed in one of them covers one of four. CONTEXT.md gives
    `rad_engine` *"parses their output"*, and *"a cell of a Radiance
    illuminance matrix must be a finite number"* is a fact about the file format, not about any one
    metric.

    **Why it cannot be left to the caller to notice.** `float("nan")` parses, so the non-numeric
    token check cannot see it, and every comparison against it is false:

    - `nan >= 1000.0` and `-inf >= 1000.0` are `False`, so every threshold tally counts zero. ASE
      publishes 0.0 % in band `clearly_acceptable` -- the most favourable verdict LM-83 has -- and
      an hourly blind schedule is `"open"` for every hour of the year. Both measured on the wire.
    - `inf >= 1000.0` is `True` for every threshold, which is the same defect in the other
      direction.
    - `min`/`max` accumulators keep their own initial sentinels, because `nan < inf` is false too.
      Measured over stdio at `6abd6ce`: `results(action="summary")` published
      `statistics: {min: null, max: null, mean: null}` with `isError: false`, the sentinels
      laundered into `null` by the JSON serializer -- CONTEXT.md's *"never report a substituted
      default as a measured value"*, silently.

    **A `ValueError`, deliberately**, exactly as its sibling `CoefficientMatrixUnreadableError` is:
    `rad_mcp.results_tool`'s dispatch already carries a `ValueError` clause of last resort, so a
    caller that has not been taught this name still produces a refusal rather than letting an
    exception escape a tool (check 0.4). Callers that *have* been taught it shape it into an
    allocated advisory code instead.

    **Refused, never reported as an empty or a dark matrix.** `050e256` took that distinction one
    matrix upstream for `asemx:`: `ASE_DIRECT_MATRIX_EMPTY` says *every cell is zero*, which is
    untrue of a `NaN` matrix, and an advisory whose definition is untrue of its condition is a
    defect in its own right (docs/tool-surface.md section 9). Whether this matrix carries signal is
    not a question that can be answered about it.
    """


def _refuse_non_finite(
    *, path: Path, seen: int, column: int, value: float
) -> NonFiniteMatrixCellError:
    """The one message, built once. Names the row **and** the hour column: "somewhere in this file"
    is not actionable over a matrix with thousands of rows, which is why every other refusal in
    this module names its row too."""
    return NonFiniteMatrixCellError(
        f"annual matrix at {path} row {seen} column {column} carries {value!r}, which is not a "
        "finite illuminance. Every comparison against a threshold is false for NaN and for -inf "
        "(so every tally would count zero and publish the favourable verdict off cells that "
        "measured nothing), and true for +inf (so every tally would count all of them); a "
        "min/max accumulator keeps its own initial sentinels for the same reason. The matrix is "
        "refused rather than read: whether it carries the light it claims to is not a question "
        "that can be answered about it. Re-run the chain that produced it."
    )


def _refuse_overflowed_lux(
    *, path: Path, seen: int, column: int, triple: tuple[float, float, float], value: float
) -> NonFiniteMatrixCellError:
    """The three-component leg's refusal, for a cell whose components are finite and whose lux is
    not.

    **A different sentence from `_refuse_non_finite`'s, because it is a different fact.** That one
    tells the operator the file carries a cell that is not a number; here every token in the file
    is a number, and the infinity was produced by the conversion. Reporting this under that
    message would name a cause the file does not have -- the condition-under-another-definition
    defect docs/tool-surface.md section 9 names -- and it is what the accumulator's own raise did
    say, in the words *"a cell rad_engine.results should already have refused"*, about a cell
    `rad_engine.results` had read correctly (fix round 2, N1).

    The **same class**, though, and deliberately: `rad_mcp.results_tool._lux_rows` shapes
    `NonFiniteMatrixCellError` into `ANNUAL_MATRIX_UNREADABLE` (or `DIRECT_MATRIX_UNREADABLE`), and
    the operator's repair is the same one -- the matrix cannot be aggregated over and the chain
    behind it has to be re-run. One condition, one code, per that method's own reasoning.
    """
    return NonFiniteMatrixCellError(
        f"annual matrix at {path} row {seen} column {column} carries the RGB triple {triple!r}, "
        f"whose components are all finite but whose photopic conversion is {value!r}. "
        "rad_core.photometry.to_lux applies the 179 lm/W efficacy to the weighted sum of the "
        "three components, and that product is not representable as a double, so the value the "
        "reader would yield is an infinity rather than an illuminance. Every threshold comparison "
        "against +inf is true, so every tally would count every hour; a min/max accumulator would "
        "publish an infinity or its own sentinel. The matrix is refused rather than read: these "
        "are not irradiances a chain produces, so the file is not the result it claims to be. "
        "Re-run the chain that produced it."
    )


def iter_annual_rows(path: Path) -> Iterator[list[tuple[float, float, float]]]:
    """Yield one sensor row at a time: `hours` RGB triples per row, in the grid's own point order.

    **The generator never holds two rows**, which is the property the whole module exists for (see
    the docstring's sizing table). A caller that wants an aggregate per point consumes each row and
    discards it; a caller that wants specific points filters by index as it goes. Nothing here
    accumulates.

    The header is read first, by `read_annual_header`, and every row is then validated against it --
    so the shape a caller is told and the rows it receives cannot disagree without raising. The file
    is opened twice (once for the header, once for the body) rather than held open across the
    generator's lifetime, which is deliberate: a generator a caller abandons half-way would otherwise
    hold a descriptor until garbage collection, and the row checks below already catch a file that
    changed between the two opens -- a body that no longer matches its header raises rather than
    being aggregated.

    **Every row is checked against the header**, and a mismatch raises rather than being yielded.
    An annual result whose rows disagree with its own header is exactly engine invariant 3's
    "valid file, plausible wrong answer": the numbers would parse, and a caller aggregating them
    would publish a mean over the wrong number of hours with nothing to indicate it. Specifically:

    - `NCOMP != 3` is refused outright -- this reads RGB triples and nothing else.
    - a row whose token count is not `3 * hours` raises, naming the row and both counts.
    - a row count that does not reach `NROWS` raises at the end (a truncated matrix -- the shape a
      job killed mid-write leaves), and one that exceeds it raises as soon as it does.

    Raises `MatrixFileUnreadableError` on a filesystem refusal and `ValueError` on any of the shape
    violations above. Yields nothing before the header has validated, so a caller in a `for` loop
    sees the refusal on the first `next()` rather than part-way through.
    """
    path = Path(path)
    header = read_annual_header(path)
    if header.ncomp != 3:
        raise ValueError(
            f"annual matrix at {path} declares NCOMP={header.ncomp}, not 3 -- this reader only "
            "reads RGB triples (R, G, B irradiance per sensor per hour)."
        )
    return _stream(path, header=header)


def _stream(
    path: Path, *, header: AnnualMatrix
) -> Iterator[list[tuple[float, float, float]]]:
    """The lazy half: open, skip the header, and hand the data lines to `_rows` one at a time.

    **The file is iterated line by line and never read whole.** At `NCOLS=8760` a single row is
    ~340 kB of text and a 20 000-point matrix is **~6.8 GB on disk** -- so `handle.read()` here
    would reintroduce, in bytes, exactly the memory blow-up this module exists to avoid. Iterating
    the handle reads a buffer at a time and holds one line.

    Kept separate from `iter_annual_rows` so that function's header checks run **eagerly**: a
    generator body does not execute until the first `next()`, so a caller asking only for the shape
    would otherwise never see the `NCOMP` refusal.
    """
    expected_tokens = 3 * header.hours
    seen = 0
    try:
        with _open_matrix(path) as handle:
            in_body = False
            for raw_line in handle:
                line = raw_line.decode("utf-8", errors=MATRIX_DECODE_ERRORS)
                if not in_body:
                    # The header's terminating blank line: everything after it is data. The header
                    # itself was already parsed by `read_annual_header`, so it is only skipped here.
                    if not line.strip():
                        in_body = True
                    continue
                if not line.strip():
                    continue
                seen += 1
                yield _parse_row(line, seen=seen, header=header, path=path,
                                 expected_tokens=expected_tokens)
    except MatrixFileUnreadableError:
        raise
    except OSError as exc:
        logger.info("annual matrix refused %s: %s", path, exc)
        raise MatrixFileUnreadableError(
            f"the annual matrix at {path} could not be read: {exc}.", path=path
        ) from exc
    if seen != header.points:
        raise ValueError(
            f"annual matrix at {path} declares NROWS={header.points} but carries {seen} data "
            "row(s). The matrix is truncated -- a job step killed mid-write leaves exactly this "
            "-- and an aggregate over it would silently be over fewer points than the grid has."
        )


def _parse_row(
    line: str, *, seen: int, header: AnnualMatrix, path: Path, expected_tokens: int
) -> list[tuple[float, float, float]]:
    """One data line -> `hours` RGB triples, with both shape checks the header makes possible.

    Split out so `_stream` stays a readable loop; every refusal here names the row number, because
    "some row is wrong" is not actionable over a matrix with thousands of them.
    """
    if seen > header.points:
        raise ValueError(
            f"annual matrix at {path} declares NROWS={header.points} but carries more data rows "
            "than that. The header and the body disagree, so no aggregate over this file would be "
            "over the number of points it claims."
        )
    tokens = line.split()
    if len(tokens) != expected_tokens:
        raise ValueError(
            f"annual matrix at {path} row {seen} has {len(tokens)} numeric token(s), not "
            f"{expected_tokens} (NCOLS={header.hours} hours x 3 components). An annual row is one "
            "RGB triple per hour; a row of exactly 3 tokens means this is a single-timestep "
            "result, which rad_engine.annual.read_ill reads instead."
        )
    try:
        values = [float(tok) for tok in tokens]
    except ValueError as exc:
        raise ValueError(
            f"annual matrix at {path} row {seen} contained a non-numeric token ({exc})."
        ) from exc
    # **Checked on the parsed tokens, before conversion, and before the triples are built.**
    # `to_lux` of a non-finite component is non-finite, so this guard is about the FILE: a token
    # this function accepts is a number the file really carries.
    #
    # **It is not the only guard, and the reason it is not was a false published sentence until fix
    # round 2 of Phase 7d-2 (N1).** The converse does not hold: `to_lux` multiplies a weighted sum
    # by 179 lm/W, so three *finite* components can convert to an infinite lux -- measured,
    # `to_lux((1e308, 1e308, 1e308))` is `inf`, and this loop passes every token of that row.
    # `_stream_lux_from`'s three-component leg therefore carries a **second** check, on the
    # converted value. Two guards, deliberately, because they are about two different things: this
    # one about what the file carries, that one about what the reader is about to yield.
    #
    # The hour column is `1 + index // 3` because a three-component row is one RGB triple per
    # hour.
    for index, value in enumerate(values):
        if not isfinite(value):
            raise _refuse_non_finite(
                path=path, seen=seen, column=1 + index // 3, value=value
            )
    return [(values[i], values[i + 1], values[i + 2]) for i in range(0, expected_tokens, 3)]


def iter_annual_lux_rows(
    path: Path, *, expected_points: int | None = None
) -> Iterator[list[float]]:
    """Yield one sensor row at a time as photopic lux, for a 1- or 3-component annual matrix.

    **The UNVERIFIED reader, kept for tests and for a caller with no sealed digest to check against
    -- every production read goes through `iter_verified_annual_lux_rows` instead** (whole-branch
    review, F6). This docstring called itself *"the one reader every metric and every `results`
    action goes through"* after this branch repointed all three production callers
    (`results_tool`, `blend`, `schedule`) at the verified reader; measured,
    `grep -rn "iter_annual_lux_rows(" src/` returns one hit, this `def`, and
    `rad_mcp.results_tool`'s module docstring names `iter_verified_annual_lux_rows` as the one reader.
    Twenty-eight test call sites keep this function alive deliberately -- it is still the shared
    engine under both, sharing `_stream_lux_from` and `_refuse_unreadable_annual_header` -- but a
    future `rad_engine` helper reaching for "the" reader by this docstring's old claim would get no
    digest verification, silently. A three-component file
    is `dctimestep` RGB irradiance and is converted by `rad_core.photometry.to_lux`; a
    one-component file is the five-phase chain's output, where `rcalc` already applied that same
    conversion inside the combination -- converting again would apply the 179 lm/W factor twice.

    `expected_points` is the manifest's own `parameters["n_points"]`. When given, `NROWS` must equal
    it. The header alone cannot detect a file that belongs to a different grid: two integers are
    consistent with any story about them. The manifest's count is the independent third statement
    that makes the check able to fail.

    Streams exactly like `iter_annual_rows` and never holds two rows; see that function for the
    sizing table and for the per-row shape checks, which are shared.
    """
    path = Path(path)
    header = read_annual_header(path)
    _refuse_unreadable_annual_header(header, path=path, expected_points=expected_points)
    return _stream_lux(path, header=header)


def _refuse_unreadable_annual_header(
    header: AnnualMatrix, *, path: Path, expected_points: int | None
) -> None:
    """The two header refusals both lux readers make, in **one** place.

    `iter_annual_lux_rows` and `iter_verified_annual_lux_rows` restated these verbatim until fix
    round 1 of Phase 7d-2 Task 1 -- two paths that agreed *today*, which ruling R30 exists to
    forbid and which ruling R255-2 confirmed binds here over the plan text that transcribed them
    twice. The failure it leaves open is not hypothetical: adding `NCOMP=2` to the accepted set in
    one copy would make the `results` read path and the blend/schedule path disagree about which
    files are readable, and only one copy has tests over it.

    Raises `ValueError` in both cases, which is what every caller already shapes.
    """
    if header.ncomp not in (1, 3):
        raise ValueError(
            f"annual matrix at {path} declares NCOMP={header.ncomp}; this reader reads scalar lux "
            "(NCOMP=1, the five-phase chain's output) or RGB irradiance (NCOMP=3, every other "
            "chain's) and nothing else."
        )
    if expected_points is not None and header.points != expected_points:
        raise ValueError(
            f"annual matrix at {path} declares NROWS={header.points} but the manifest records "
            f"{expected_points} point(s) for this grid. The file and the grid have drifted apart, "
            "so every row would be labelled with a sensor it is not."
        )


class MatrixDigestUnreadableError(MatrixFileUnreadableError):
    """The bytes of a matrix could not be **read in order to hash them**.

    A subclass of `MatrixFileUnreadableError`, so every caller that already shapes "this matrix
    would not read" keeps working unchanged and no handler has to learn a new base type. What the
    subclass adds is the one distinction a handler cannot make for itself: *which phase* failed.

    **Why the phase matters, measured (fix round 1, Critical 1).**
    `iter_verified_annual_lux_rows` reads the same descriptor three times -- hash, header, rows --
    and all three raise `MatrixFileUnreadableError` on a bad block. A handler that re-coded the
    whole read as "the bytes no longer hash to the sealed digest" therefore said so about an
    `EIO` on the last row of a file whose digest had **already matched**: the operator was told to
    re-run the build or restore the file, when the file was fine and the disk was not. Raised only
    for the open-and-hash phase; a failure after the digest succeeds keeps the plain base class and
    the caller's pre-existing filesystem refusal.
    """


class MatrixContentDriftedError(ValueError):
    """The bytes at a matrix path do not hash to the digest a caller says was sealed for it.

    Raised **from the descriptor the rows are then read from**, so no `open()` happens between the
    hash and the read and the file cannot be *replaced* between them -- an fd binds the inode, so a
    rename or an unlink-and-recreate cannot change the bytes behind it.

    **This NARROWS the window; it does not close it.** A writer that modifies the same inode in
    place -- `pwrite`, `ftruncate` -- between the hash and the rewind still wins, and this class
    cannot detect that. The two-pass form it replaces lost to both.

    **And "replacement defeated / in-place not defeated" is not an exhaustive enumeration.** Two
    further residual classes are disclosed in full at the published surface (`docs/tool-surface.md`
    §14, *"Two more residual classes"*; whole-branch review, F7): the digest's **trust root** is
    the same unsigned `manifest.json` that an attacker able to perform the in-place write already
    owns, so the read reports as verified with no race at all; and a **directory symlink planted in
    the parent chain** substitutes the path upstream of both seeks, which fails closed with a
    digest present and open without one. Neither is fixed; both are named so the pair above stops
    reading as complete.
    """

    def __init__(self, message: str, *, path: Path, expected: str, actual: str) -> None:
        super().__init__(message)
        self.path = path
        self.expected = expected
        self.actual = actual


def iter_verified_annual_lux_rows(
    path: Path,
    *,
    expected_points: int | None = None,
    content_digest: str | None = None,
) -> Iterator[list[float]]:
    """`iter_annual_lux_rows`, with the sealed digest verified on the **same held descriptor** the
    rows are then read from.

    **One open, not one buffer.** The digest is taken in `safe_io.DIGEST_BLOCK` blocks and the rows
    are streamed one at a time, so peak memory is unchanged from `iter_annual_lux_rows` -- a block
    plus a row. The two-pass form's stated reason for not fixing this (*"holding one to hash it is
    the blow-up this whole module exists to avoid"*) was wrong: one open is not one buffer.

    **What this narrows, and what it does not.** Holding the descriptor defeats **replacement** --
    rename, unlink-and-recreate -- because an fd binds the inode. It does **not** defeat **in-place
    modification** of that inode between the hash and the rewind. The window is narrowed from
    "between two opens" to "between two seeks on one fd", and the residual is named rather than
    claimed closed.

    **Two seeks, never a second open.** The header scan and the row loop both start from 0, so the
    descriptor is rewound between them; `_open_matrix` is called exactly once and
    `tests/test_results.py::test_the_verified_reader_opens_the_matrix_exactly_once` asserts that
    count rather than inferring it, against a positive control that counts 2 over the two-pass
    reader.

    **A generator**, so the digest is taken when the first row is asked for rather than at
    construction. `iter_annual_lux_rows` is not one -- it validates its header eagerly and returns
    a generator -- so a caller that builds this and never iterates it has checked nothing. Every
    caller in this repo consumes it.

    **Silent when `content_digest` is None**, for `results_tool._lux_rows`' stated reason: an
    artifact sealed by something other than `register_artifact` records no digest, and refusing
    those would be a different decision taken by a guard asked to catch drift.
    """
    path = Path(path)
    try:
        handle = _open_matrix(path)
    except MatrixFileUnreadableError as exc:
        # **The open is part of the digest phase**, exactly as it was when `results_tool._lux_rows`
        # called `file_digest(path)` -- that function opened *and* hashed, and either failing was
        # one `ProvenanceError` shaped into one refusal. A file that will not open has bytes that
        # cannot be shown to be the sealed ones, so the distinction below is drawn here and not
        # after it. With no digest asked for there is nothing to show, and the filesystem refusal
        # propagates as it always did.
        if content_digest is None:
            raise
        raise MatrixDigestUnreadableError(str(exc), path=path) from exc
    with handle:
        if content_digest is not None:
            actual = _digest_of(handle, path=path)
            if actual != content_digest:
                raise MatrixContentDriftedError(
                    f"the annual matrix at {path} no longer matches the bytes that were sealed "
                    f"for it: the record holds {content_digest}, the file hashes to {actual}.",
                    path=path, expected=content_digest, actual=actual,
                )
            handle.seek(0)
        # **Everything below here is POST-digest**, and a read failure in it is a filesystem
        # failure, not a provenance one -- so it keeps `MatrixFileUnreadableError` and reaches the
        # caller's own handler unrelabelled. Fix round 1, Critical 1: the phases were previously
        # told apart in the *handler*, which could not tell them apart at all, and an `EIO` on any
        # block after the first was published as `MATRIX_CONTENT_DRIFTED` with a message saying the
        # bytes had moved. They had not; the disk had failed.
        header = _read_header_from(handle, path=path)
        _refuse_unreadable_annual_header(header, path=path, expected_points=expected_points)
        handle.seek(0)
        yield from _stream_lux_from(handle, path=path, header=header)


def _digest_of(handle: IO[bytes], *, path: Path) -> str:
    """`handle_digest`, with a read failure **during the hash** given its own exception type.

    The type is the whole point (fix round 1, Critical 1). `handle_digest` reads to EOF, and so do
    the header scan and the row loop that follow it on the same descriptor; all three raise the
    same `OSError` family on a bad block. Only a failure *here* means "these bytes cannot be shown
    to be the bytes that were sealed" -- a failure afterwards means the digest already matched and
    the read then broke, which is a filesystem fault and must not be published as drift.
    """
    try:
        return handle_digest(handle)
    except OSError as exc:
        logger.info("annual matrix digest refused %s: %s", path, exc)
        raise MatrixDigestUnreadableError(
            f"the annual matrix at {path} could not be hashed: {exc}. Nothing about its content "
            "has been judged -- the read failed part-way through the digest, so its bytes have "
            "neither been shown to match the sealed digest nor shown to differ from it.",
            path=path,
        ) from exc


def _stream_lux(path: Path, *, header: AnnualMatrix) -> Iterator[list[float]]:
    """The lazy half for `iter_annual_lux_rows`: open, skip the header, and hand each data line to
    the row parser matching the header's own `NCOMP`, converting to lux only where the file has not
    already been converted.

    Mirrors `_stream`'s loop structure and its correctness properties (streamed, one row held at a
    time, the header's terminating blank line skipped exactly once, the same truncated/overlong row
    counting at the end) rather than re-deriving them -- see `_stream` for the full reasoning. The
    one difference is the row parser: a three-component row is parsed by the shared `_parse_row` (so
    its refusals are not duplicated here) and each triple is converted through `to_lux`; a
    one-component row already carries lux -- `rcalc` applied `to_lux` inside the five-phase
    combination -- so its tokens are only floated, never re-weighted.
    """
    with _open_matrix(path) as handle:
        yield from _stream_lux_from(handle, path=path, header=header)


def _stream_lux_from(
    handle: IO[bytes], *, path: Path, header: AnnualMatrix
) -> Iterator[list[float]]:
    """`_stream_lux`'s loop, over a descriptor the caller opened and still holds.

    **The caller must have positioned it at 0.** The loop skips the header itself, by the same
    `in_body` rule `_stream_lux` has always used, so a caller that has already read the header from
    this descriptor must seek back before calling -- two seeks on one descriptor, never a second
    open.
    """
    expected_tokens = header.ncomp * header.hours
    seen = 0
    try:
        in_body = False
        for raw_line in handle:
            line = raw_line.decode("utf-8", errors=MATRIX_DECODE_ERRORS)
            if not in_body:
                # The header's terminating blank line: everything after it is data.
                if not line.strip():
                    in_body = True
                continue
            if not line.strip():
                continue
            seen += 1
            if header.ncomp == 3:
                triples = _parse_row(line, seen=seen, header=header, path=path,
                                      expected_tokens=expected_tokens)
                lux = [to_lux(triple) for triple in triples]
                # **The second finiteness guard, on the CONVERTED value** (fix round 2, N1).
                # `_parse_row` has already established that every token in this row is finite --
                # that is a fact about the file. This is a fact about what is about to be yielded:
                # `to_lux` multiplies by 179 lm/W, so a row of finite components can convert to an
                # infinite lux, and every token guard upstream passes it. Measured before this
                # guard existed: a `NCOMP=3` matrix of `1e308` components yielded `[[inf]]`, which
                # `_summary` fed straight to `_Accumulator.add` -- and the five published sentences
                # saying that raise is unreachable through `results` were false (ruling R255-7:
                # a branch recorded as unreachable must actually be unreachable). Placed here
                # rather than in `_parse_row` because `_parse_row` returns triples and is shared
                # with `iter_annual_rows`, which does not convert; the one-component leg needs no
                # copy, since `_parse_lux_row` floats already-converted tokens and re-weights
                # nothing.
                for index, value in enumerate(lux):
                    if not isfinite(value):
                        raise _refuse_overflowed_lux(
                            path=path, seen=seen, column=index + 1,
                            triple=triples[index], value=value,
                        )
                yield lux
            else:
                yield _parse_lux_row(line, seen=seen, header=header, path=path,
                                      expected_tokens=expected_tokens)
    except MatrixFileUnreadableError:
        raise
    except OSError as exc:
        logger.info("annual matrix refused %s: %s", path, exc)
        raise MatrixFileUnreadableError(
            f"the annual matrix at {path} could not be read: {exc}.", path=path
        ) from exc
    if seen != header.points:
        raise ValueError(
            f"annual matrix at {path} declares NROWS={header.points} but carries {seen} data "
            "row(s). The matrix is truncated -- a job step killed mid-write leaves exactly this "
            "-- and an aggregate over it would silently be over fewer points than the grid has."
        )


def _parse_lux_row(
    line: str, *, seen: int, header: AnnualMatrix, path: Path, expected_tokens: int
) -> list[float]:
    """One one-component data line -> `hours` lux values, already converted.

    `rcalc` applied `to_lux` inside the five-phase combination that produced this file, so these
    tokens are floated and yielded untouched -- converting them again would apply the 179 lm/W
    factor a second time. Gives the one-component case its own token-count check with the same
    message shape `_parse_row` uses for the three-component case, rather than routing a scalar row
    through a function whose refusal names "x 3 components".
    """
    if seen > header.points:
        raise ValueError(
            f"annual matrix at {path} declares NROWS={header.points} but carries more data rows "
            "than that. The header and the body disagree, so no aggregate over this file would be "
            "over the number of points it claims."
        )
    tokens = line.split()
    if len(tokens) != expected_tokens:
        raise ValueError(
            f"annual matrix at {path} row {seen} has {len(tokens)} numeric token(s), not "
            f"{expected_tokens} (NCOLS={header.hours} hours x 1 component). An annual row is one "
            "lux value per hour for a five-phase result."
        )
    try:
        values = [float(tok) for tok in tokens]
    except ValueError as exc:
        raise ValueError(
            f"annual matrix at {path} row {seen} contained a non-numeric token ({exc})."
        ) from exc
    # The one-component leg's own copy of the finiteness guard, for the reason this function has
    # its own token-count check: routing a scalar row through `_parse_row` would give it a refusal
    # that names "x 3 components". The condition and the message are shared
    # (`_refuse_non_finite`); only the column arithmetic differs, and here a token *is* an hour.
    for index, value in enumerate(values):
        if not isfinite(value):
            raise _refuse_non_finite(path=path, seen=seen, column=index + 1, value=value)
    return values


# --------------------------------------------------------------------------------------------
# The `rcontrib` coefficient matrix, and the one question the ASE guard asks of it
# --------------------------------------------------------------------------------------------


class CoefficientMatrixUnreadableError(ValueError):
    """The bytes at an `asemx:<grid>` path are not the ASCII `rcontrib` matrix that kind requires.

    Four conditions, one fact: a `FORMAT` other than `ascii`, a `#?RADIANCE` block no blank line
    terminates, a token that will not parse as a number, and a cell that parses to `NaN` or an
    infinity. They are one exception rather than four because they have one repair -- produce the
    matrix again -- and because a caller cannot act differently on them.

    **A `ValueError`, deliberately**, so every `except ValueError` already standing around a matrix
    read keeps catching it. This is `_no_header_within_bound`'s own reasoning: nothing was refused
    by the filesystem here, the bytes arrived and are not a matrix, which is a different fact from
    `MatrixFileUnreadableError`'s.

    **It carries `COEFFICIENT_MATRIX_UNREADABLE`, allocated Phase 7c Task 15 fix round 1.** The
    allocation was *sequenced* rather than declined when this type was introduced (Task 14, fix
    round 1): docs/tool-surface.md was owned by another task at the time, and a code emitted from
    `src/` that the register does not carry is itself a defect. Until it landed, this exception
    reached `jobs.JobStore._work`'s `except BaseException`, which reads
    `getattr(exc, "advisory", None)`, found none, and finished the job `failed` with
    `record.message` set to `"CoefficientMatrixUnreadableError: ..."` and an **empty** `advisories`
    list -- a failure that was loud, attributable to a named step and named the path and the row,
    but was not machine-readable, which is exactly what `MatrixFileUnreadableError`'s own docstring
    names as the defect (*"reaches the wire as an uncoded transport failure"*). Having one type
    over four raise sites is what made closing it a single `advisory` property here rather than a
    hunt.

    **`advisory` is built here and not through `project._advisory`.** `rad_engine.project` imports
    this module, so importing its helper back would be circular; `EngineAdvisory` is the shared
    vocabulary both sides speak and is constructed directly. `lines` is empty for the reason that
    helper's own docstring gives -- there is no Radiance stderr behind this, only bytes on disk.
    """

    #: See the class docstring. Read by `jobs.JobStore._work` through the `advisory` property below,
    #: the same route `rad_engine.project.ProjectError` already takes to the wire.
    code: str = "COEFFICIENT_MATRIX_UNREADABLE"
    severity: Severity = "error"

    @property
    def advisory(self) -> EngineAdvisory:
        """The coded advisory a failed job carries, so the refusal is machine-readable."""
        return EngineAdvisory(
            code=self.code, severity=self.severity, message=str(self), lines=(), subject=None
        )


def coefficient_matrix_is_all_zero(path: Path) -> bool:
    """Whether every cell of an `rcontrib` coefficient matrix is exactly zero.

    **The one question `ASE_DIRECT_MATRIX_EMPTY` asks of `asemx:<grid>`**, and the reason it needs a
    function of its own rather than a pass over `iter_annual_lux_rows`: **an `rcontrib` matrix has
    no `NROWS`.** Measured on this machine 2026-08-22 (Radiance 6.1a, `6.1.39b9966033`), running
    `rad_engine.matrices.build_ase_direct_argv`'s own flag set against
    `tests/fixtures/scenes/micro`, the header is

        #?RADIANCE
        oconv -f micro_room.rad suns_mf1.rad
        rcontrib -I -ab 0 ... -b rbin -bn Nrbins -m solar
        SOFTWARE= RADIANCE 6.1a 2026-05-05 LBNL (6.1.39b9966033)
        CAPDATE= ...
        GMT= ...
        NCOMP=3
        NCOLS=146
        FORMAT=ascii

    -- `NCOMP`, `NCOLS`, `FORMAT`, and no row count, because `rcontrib -I` streams its sensor rays
    from stdin and cannot know how many there will be. `rad_engine.annual.parse_dc_matrix_header`
    raises `ValueError` when `NROWS` is absent, so `read_annual_header`, `iter_annual_rows` and
    `iter_annual_lux_rows` all refuse this file. Nothing here reads a declared shape at all: the
    question is about the *cells*, and every one of them is on disk.

    **One non-zero cell anywhere returns `False`, and the scan stops there.** The guard is
    *"nothing at all"*, never *"not much"* -- a genuinely dark space is a real result, and the
    boundary of a guard is a threshold like any other (rulings R35/R37). The passing case therefore
    costs a few bytes; the refusing case costs the whole file, measured at **0.33 s** for a
    synthetic all-zero office-shaped matrix (456 sensors x 2 306 bins x 3 components, 41 MB ASCII).
    Its one production caller, `rad_engine.project.register_artifact`, already streams the same
    bytes for `file_digest`.

    **Not a lux reader, and never converted.** These are contribution coefficients, not
    illuminance: `to_lux` has no meaning here and is not applied. Zero-ness is the only property
    read, and it survives any positive weighting, which is what makes this the right question to
    ask of a coefficient matrix rather than of the lux matrix multiplied out of it.

    The header is optional. `build_ase_direct_argv(header=False)` is a legal call, and a reader
    that assumed a `#?RADIANCE` block would silently consume the first *sensor row* as header text
    and then report a matrix that is one row shorter than it is. The block is skipped only when the
    file actually opens with `#?`, bounded by `_MAX_HEADER_BYTES` for the reason that constant
    records.

    **A non-finite cell is refused, not counted as signal** -- Task 14's fix round 1, and the
    decision is recorded here because the alternative is a silent one. `float("nan") != 0.0` is
    `True`, so the first cut read an all-`NaN` matrix as *"has signal"* and sealed it; `illdir`
    would then multiply to `NaN`, `PointTally.observe` would find every `nan >= 1000.0` false, and
    ASE would publish `0.0` in the band `clearly_acceptable`. **That is the exact silent-favourable-
    verdict failure this whole guard exists to stop, arriving through a different door**, so
    passing it was not defensible.

    Refusing it as `ASE_DIRECT_MATRIX_EMPTY` was not defensible either: that code's register row
    says *every cell is zero*, which is untrue of a `NaN` matrix, and an advisory whose definition
    is untrue of its condition is a defect in its own right (docs/tool-surface.md section 9). So a
    non-finite cell takes the same exception as a token this reader cannot parse at all -- it is
    the same fact about the file, that it is not the matrix its kind requires -- and the caller
    fails the seal without a claim being made about *why* it is not a matrix.
    **Not reachable from `rcontrib` as this server invokes it**, and no run in this repo has ever
    produced one; this is a guard against a broken producer, not a reported condition.

    Raises `CoefficientMatrixUnreadableError` (a `ValueError`) for every way these bytes can fail
    to be an ASCII `rcontrib` matrix: a `FORMAT` other than `ascii`, a `#?RADIANCE` block no blank
    line terminates, a token that will not parse as a number, and a cell that parses to `NaN` or an
    infinity. A guard that cannot read its input must say so rather than return "no signal here",
    which is precisely the silent zero the caller exists to refuse.

    Raises `MatrixFileUnreadableError` on any filesystem refusal, so a raw `OSError` never reaches
    a caller that is only equipped to shape this module's own exception types
    (`MatrixFileUnreadableError`'s docstring: a bare `OSError` *"is not a member of this codebase's
    error vocabulary"*).

    Every open is `safe_io.open_regular_file` (ruling R48), via `_open_matrix`.
    """
    path = Path(path)
    with _open_matrix(path) as handle:
        first = handle.readline()
        if first.startswith(b"#?"):
            _skip_coefficient_header(handle, path, scanned=len(first))
            line = handle.readline()
        else:
            line = first
        seen = 0
        while line:
            seen += 1
            try:
                values = [float(token) for token in line.split()]
            except ValueError as exc:
                raise CoefficientMatrixUnreadableError(
                    f"the coefficient matrix at {path} row {seen} contained a non-numeric token "
                    f"({exc}). This reader reads the ASCII form `rcontrib` writes with no `-ff`/"
                    "`-fd`; a binary body cannot be scanned for signal."
                ) from exc
            # Checked before the zero test, not after: `nan != 0.0` is `True`, so a non-finite cell
            # would otherwise short-circuit the whole scan as "has signal" on its first appearance
            # and never be looked at again. See the docstring for why this is refused rather than
            # counted as signal or reported as an empty matrix.
            if not all(isfinite(value) for value in values):
                raise CoefficientMatrixUnreadableError(
                    f"the coefficient matrix at {path} row {seen} contains a value that is not "
                    "finite (NaN or an infinity), so whether this matrix carries signal is not a "
                    "question that can be answered about it. It is refused rather than sealed: "
                    "`nan != 0.0` is true, so counting it as signal would seal a broken matrix, "
                    "and ASE would then publish 0.0% in the band 'clearly_acceptable' because "
                    "every `nan >= 1000` comparison is false. It is not reported as an empty "
                    "matrix either, because it is not one."
                )
            if any(value != 0.0 for value in values):
                return False
            line = handle.readline()
    return True


def _skip_coefficient_header(handle: IO[bytes], path: Path, *, scanned: int) -> None:
    """Consume a `#?RADIANCE` block up to its terminating blank line, checking `FORMAT` on the way.

    Bounded at `_MAX_HEADER_BYTES` for the reason `read_annual_header` is: a file with no blank line
    anywhere has no terminator to find, and an unbounded scan reads the whole matrix to discover it.
    The bound is three orders of magnitude above any real Radiance header.
    """
    while True:
        line = handle.readline()
        if not line:
            raise CoefficientMatrixUnreadableError(
                f"the coefficient matrix at {path} opens a `#?RADIANCE` header block that no blank "
                f"line terminates within {scanned} bytes, so the data rows cannot be located. "
                "An `rcontrib` matrix written with a header always terminates it."
            )
        scanned += len(line)
        if not line.strip():
            return
        key, separator, value = line.partition(b"=")
        if separator and key.strip() == b"FORMAT":
            declared = value.strip().decode("utf-8", errors=MATRIX_DECODE_ERRORS)
            if declared != "ascii":
                raise CoefficientMatrixUnreadableError(
                    f"the coefficient matrix at {path} declares FORMAT={declared!r}; this reader "
                    "scans the ASCII form only. Nothing this server builds emits another: "
                    "`build_ase_direct_argv` passes no `-ff`/`-fd`. Refused rather than reported "
                    "as carrying no signal, which is the silent zero the caller exists to catch."
                )
        if scanned >= _MAX_HEADER_BYTES:
            raise CoefficientMatrixUnreadableError(
                f"the coefficient matrix at {path} has no blank line terminating its "
                f"`#?RADIANCE` block within the first {scanned} bytes. The scan stopped there "
                "rather than reading the rest of the file."
            )
