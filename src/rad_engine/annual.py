"""The 2-phase daylight-coefficient chain: `rfluxmtx` builds a sky-to-sensor daylight coefficient
(DC) matrix, `genskyvec` turns a sky description into a Reinhart sky vector, and `dctimestep`
multiplies the two to produce a result -- one RGB irradiance triple per sensor per sky.

**Scope** (docs/plan.md Phase 4 Task 4, check 4.6; Phase 7a Task 5 for the receiver's `mf`
parameter): this module owns the `rfluxmtx` argv, the sky+ground receiver text every DC matrix in
this project is built against, `genskyvec` argv
construction, matrix-header parsing for both `rfluxmtx` and `dctimestep` output, and the shape check
that must run before either matrix is trusted (engine invariant 3). It does **not** run `gendaymtx`
or parse its header (`rad_engine.skymatrix`, this module's Phase-4 sibling for the *annual* sky
matrix -- this module works with a single-column sky *vector*, one timestep, not an 8760-column
annual matrix), does **not** build the wire envelope or decide method inference (`rad_core.method`,
`rad_mcp`), and does **not** convert RGB to lux (`rad_core.photometry.to_lux`, imported by the
caller -- see the note below on why that boundary matters here specifically).

**This module was de-risked by hand before any test was written.** docs/plan.md names check 4.6 as
the one risk that could force Phase 4 to re-plan: "If the DC chain and `rtrace` disagree by more
than an order of magnitude, the fault is in the sender/receiver setup or the bin count." The chain
below was built and run against Radiance 6.1a on the office fixture first, by hand, specifically to
find that fault before committing to an API shape. It did not disagree by more than an order of
magnitude -- the ratios trend from ~0.36 to ~1.1 across the three check-3.1 points, which is within
the first-commit tolerance docs/plan.md's check 4.6 states (a) same sign and order of magnitude,
(b) monotonic falloff preserved -- so this module proceeds as designed rather than being bisected.
See `tests/fixtures/README.md`'s Phase 4 Task 4 entry for the measured ratios, the repeat count, and
the settings they were measured at.

**The dimensional trap, found during that hand-run and now structural rather than incidental.** A
receiver declaring only the sky hemisphere (`h=r{mf}`) gives a DC matrix with `NCOLS=144*mf^2+1`
against a `genskyvec -m mf` sky vector's `NROWS=144*mf^2+2` -- the ground patch that
`rad_engine.skymatrix`'s own module docstring names as the reason check 4.3 exists, one dimension
up: `144*mf^2 + 1` sky patches is the *wrong* count for a Reinhart matrix's column dimension in
exactly the same way it is the wrong row count there. `dc_receiver_text(mf)` below declares **both**
hemispheres -- `h=r{mf}` for the sky, `h=u` (uniform, one bin) for the ground -- for every `mf` it
accepts, so no caller of this module can build a one-hemisphere receiver by omission the way the
hand-run's first attempt did.

**Phase 7a Task 5: the receiver is parameterised by `mf`, closing ruling R44(1)'s deferral.** Until
this task `DC_RECEIVER_TEXT` was a fixed module constant hardcoding `h=r4`, and R44(1) (docs/plan.md)
refused every `sky_subdivision != 4` on the 2-phase path rather than mis-build a receiver at another
subdivision -- *"parameterising the receiver by `mf` is the real fix and is deferred to Phase 7,
where `-m 6` is actually needed for the sun matrix."* `dc_receiver_text(mf)` is that fix: one
function, checked against the same four measured subdivisions docs/tool-surface.md's verified fact 3
records (`Nrbins` = 146 / 578 / 2306 / 5186 for MF 1 / 2 / 4 / 6), rather than a formula this module
invented and hoped agreed with `gendaymtx`.

**The receiver's *content* became `mf`-dependent; its *filename* had to follow, and a second review
round is why it does.** The first version of this parameterisation left `DC_RECEIVER_FILENAME` a
fixed name (`"receiver.rad"`), shared by every call regardless of `mf` -- safe only while the
content was fixed too. Review found the reopened defect: two ordinary sequential `run_annual` calls
at different subdivisions racing that one shared, submit-time write (which sits above the `dry_run`
return) could leave one call's queued job reading the *other* call's receiver, sealing a manifest
`"mf"` that no longer described the matrix actually built. `dc_receiver_filename(mf)` closes it by
construction -- `receiver_mf4.rad` and `receiver_mf6.rad` never collide -- and `rad_mcp.annual_tool.
_shape_check` additionally reads the receiver's own declared `mf` back off disk before the job is
queued, an independent second source rather than a repeated calculation.

**`dctimestep` fails loudly on this mismatch, and that is worth stating precisely because it bounds
what `check_matrix_shape` below is actually for.** Measured directly against Radiance 6.1a: feeding
`dctimestep` a `2305`-column DC matrix against a `2306`-row sky vector exits 1 with `fatal -
unexpected column count in header` on stderr, and `rad_engine.run.run`'s own `check=True` default
turns that into a `RadianceError` -- so *this specific* mismatch can never silently produce a wrong
number. That is not a reason to skip shape validation, though, and engine invariant 3
(docs/tool-surface.md) says why: "a transposed `.ill` is a valid file that yields a plausible wrong
answer." A DC matrix and a sky vector with their row/column roles swapped relative to each other
(`NROWS`/`NCOLS` both changed so the multiplication still "works" dimensionally under some other
pairing) is a shape `dctimestep` may accept without complaint while computing something other than
what the caller intended. `check_matrix_shape` is therefore built as a first-class check the caller
runs *before* invoking `dctimestep` at all, not as a decorative restatement of a failure Radiance
already reports loudly on its own -- the two catch different things, and only the explicit check
catches the plausible-wrong-answer case invariant 3 is actually about.

**Why RGB-to-lux conversion is not here.** `dctimestep`'s own output is one RGB irradiance triple
per sensor per sky column, not one scalar -- the trap the hand-run hit on its first pass, mis-parsed
into "243 000 lux out of header digits." `parse_dctimestep_output` below returns the RGB tuples and
stops there; converting them is `rad_core.photometry.to_lux`'s job, exactly as
`rad_engine.rtrace.parse_irradiance` leaves it to a caller one layer up for `rtrace -I`'s identical
RGB-per-sensor shape. Ruling R30 (docs/plan.md) already made this call for the analogous reflectance
arithmetic: physics lives in `rad_core`, `rad_engine` consumes it, "so there is one arithmetic path
by construction rather than by discipline" -- reimplementing `179*(0.265R+0.670G+0.065B)` a second
time here is exactly the drift that ruling exists to prevent.

**Never compare across sampling regimes (ruling R37).** A Monte Carlo result has its own converged
mean per setting. Every ratio this module's own module-level fixture measurement records is
measured at one stated `-ab`/`-ad`/`-as` setting against Phase 3's `rtrace` values measured at their
own separately-recorded setting -- the two are never assumed comparable merely because both use
`-ab 3`; see `tests/fixtures/README.md`'s Phase 4 Task 4 entry for exactly which settings were used
and why re-measuring both sides is required before changing either.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Final, Literal

from .env import RadianceEnv
from .octree import refuse_inline_commands_in_files, refuse_shell_unsafe_paths
from .rtrace import QualityParameters
from .run import Result, run
from .safe_io import open_regular_file

__all__ = [
    "MATRIX_DECODE_ERRORS",
    "DcMatrixHeader",
    "DcMatrixSpec",
    "MatrixFileUnreadableError",
    "MatrixShapeError",
    "build_dctimestep_argv",
    "build_direct_lux_argv",
    "build_five_phase_combine_argv",
    "build_genskyvec_argv",
    "build_rcollate_final_argv",
    "build_rcollate_leg_argv",
    "build_rfluxmtx_argv",
    "build_rlam_argv",
    "build_rmtxop_sum_argv",
    "build_three_phase_argv",
    "check_five_phase_shapes",
    "check_matrix_shape",
    "check_three_phase_shapes",
    "dc_receiver_filename",
    "dc_receiver_text",
    "parse_dc_matrix_header",
    "parse_dctimestep_output",
    "parse_receiver_subdivision",
    "read_ill",
    "read_matrix_header",
    "read_receiver_subdivision",
    "run_dctimestep",
    "run_genskyvec",
    "run_rfluxmtx",
    "SUPPORTED_SUBDIVISIONS",
]

logger = logging.getLogger(__name__)

#: How a matrix file's bytes are decoded on the way in from disk (`read_ill`, `read_matrix_header`).
#: **Not** a strict decode -- ruling R38, and `rad_engine.weather.EPW_DECODE_ERRORS` /
#: `rad_engine.octree._decode_scene_bytes`, whose reasoning this mirrors exactly: the property being
#: bought is that this decode *cannot raise*. A Radiance matrix header echoes its own command line
#: verbatim, so a non-ASCII byte anywhere in a project path -- an ordinary accented directory name --
#: lands in the header of every matrix built under it, and a strict decode would refuse an entire
#: annual result over a byte in a line nothing reads. Byte-preserving and ASCII-transparent, which is
#: every character of every `NROWS=`/`NCOLS=`/`NCOMP=` field and every numeric token below them.
MATRIX_DECODE_ERRORS: Literal["surrogateescape"] = "surrogateescape"

#: How many bytes `read_matrix_header` pulls per read while hunting for the header's terminating
#: blank line. Sized to hold a whole Radiance matrix header in one read in the ordinary case -- the
#: measured `dctimestep` and `rfluxmtx` headers on this machine are ~200 and ~350 bytes -- so the
#: loop below almost always runs once. **It is the granularity, not the bound** -- `_MAX_HEADER_BYTES`
#: is what stops the scan, and until Phase 7a's whole-branch review there was nothing at all.
_HEADER_CHUNK_BYTES = 8192

#: How far `read_matrix_header` scans for the header's terminating blank line before refusing.
#:
#: **The same correctness bound `rad_engine.results._MAX_HEADER_BYTES` carries, for the same reason,
#: at the same value** -- that constant holds the measurement (60.76 s and a 2.9 GB `maxrss` over a
#: 49.5 MB headerless file, called directly). This function has the identical loop over the identical
#: question, so it gets the identical ceiling rather than being left as the second instance of a
#: defect the first was fixed for. Restated here rather than imported because `rad_engine.results`
#: imports *this* module and the reverse edge would be a cycle.
_MAX_HEADER_BYTES = 256 * 1024

#: How much of the scanned text `parse_dc_matrix_header` may quote back when a field is missing.
#:
#: **An error message must never be able to carry a matrix.** The message embedded `header_block`
#: verbatim, and `header_block` is `text.split("\n\n", 1)[0]` -- which for a headerless file is the
#: *whole file*. Measured on this machine 2026-08-18 over a 49.5 MB headerless five-phase `.ill`:
#: a 55 923 937-character `ValueError`, 1.08x the input (the `!r` repr escapes every tab), which
#: `rad_mcp.results_tool` then interpolated into both a refusal string and a `logger.info` call.
#: 2 000 characters is far more header than any real one has and cannot carry a matrix under any
#: input; the total length is reported alongside so the truncation is visible rather than silent.
_HEADER_TEXT_IN_MESSAGE_LIMIT = 2000

#: The Reinhart subdivisions `dc_receiver_text` has a row-count formula for -- docs/tool-surface.md
#: verified fact 3 records `Nrbins` = 146 / 578 / 2306 / 5186 for MF 1 / 2 / 4 / 6 from `gendaymtx`
#: and `rcalc -f reinhart.cal` directly (`rad_engine.skymatrix.reinhart_row_count` reproduces the
#: same four values from `144*mf^2+2`, and `rad_engine.matrices._REINHART_BINS` the same four
#: `Nrbins` for the 3-phase D matrix's own use of `reinhart.cal`). **That fact is about `gendaymtx`
#: and `reinhart.cal`, not about this module's own `rfluxmtx` receiver** -- `reinhart.cal` itself
#: would compute a bin count for any `mf`, including ones outside this set. The receiver text below
#: has been run through real `rfluxmtx` on this machine at `mf=4` (the original hand-run) and `mf=6`
#: (Phase 7a Task 5's own end-to-end test, `tests/test_annual_tool.py::TestEndToEnd::
#: test_the_receiver_and_sky_matrix_agree_at_a_non_default_subdivision`); `mf=1` and `mf=2` are
#: covered by `dc_receiver_text`'s own text-construction tests and the same code path proven at the
#: other two, not by an independent `rfluxmtx` spawn at those two values specifically. This set is
#: the four subdivisions this project is willing to vouch for on that basis -- `dc_receiver_text`
#: refuses anything outside it rather than emitting a receiver text this project has not exercised.
SUPPORTED_SUBDIVISIONS: Final[frozenset[int]] = frozenset({1, 2, 4, 6})


def dc_receiver_filename(mf: int) -> str:
    """The `rfluxmtx` receiver filename for one daylight-coefficient run at Reinhart subdivision
    `mf`: ``receiver_mf{mf}.rad``.

    **Derived from `mf` rather than a fixed constant, and that is a correctness fix, not styling.**
    Before this function existed, `DC_RECEIVER_FILENAME` was one fixed name (`"receiver.rad"`)
    written to the shared project root by every call regardless of `mf` -- safe only while
    `DC_RECEIVER_TEXT` was itself a fixed constant, so every writer wrote identical bytes. The moment
    the receiver's *content* became `mf`-dependent (`dc_receiver_text`, this module, Phase 7a Task
    5), two `run_annual` calls at different subdivisions racing on that one shared path could leave
    one call's queued job reading the *other* call's receiver -- silently, since nothing at build
    time re-reads what actually landed on disk, and the write happens above the `dry_run` return, so
    even a call documented as "preview without doing work" could clobber another call's live input.
    Deriving the filename from `mf` removes the collision by construction: `run_annual(sky_subdivision
    =4)` and `run_annual(sky_subdivision=6)` write to two different paths, `receiver_mf4.rad` and
    `receiver_mf6.rad`, so there is no shared file for either to overwrite. Two calls at the *same*
    `mf` still share a path, exactly as every call did before this function existed -- benign for the
    identical reason: `dc_receiver_text` is pure, so both writers write identical bytes.

    `build_rfluxmtx_argv` calls this directly from `spec.sky_subdivision` -- no new parameter on that
    function's signature, because the value it needs was already there.
    """
    return f"receiver_mf{mf}.rad"


def dc_receiver_text(mf: int) -> str:
    """The sky+ground receiver for one daylight-coefficient run at Reinhart subdivision `mf`.

    Replaces `DC_RECEIVER_TEXT`, the module constant this function's own history hardcoded `h=r4`
    into and which forced ruling R44(1) to refuse every other `sky_subdivision` on the 2-phase path
    rather than mis-build one -- *"parameterising the receiver by `mf` is the real fix and is
    deferred to Phase 7, where `-m 6` is actually needed for the sun matrix"* (docs/plan.md). This is
    that fix.

    **The ground hemisphere is written FIRST, and the order is the whole correctness of the file.**
    `rfluxmtx` numbers a receiver's bins in the order its `#@rfluxmtx` blocks appear, and
    `dctimestep` pairs matrix column `i` with sky-matrix row `i` by position and nothing else. Both
    `gendaymtx` and `genskyvec` put the ground patch in **row 0**, so the ground block must come
    first here or the two are off by one.

    **This file listed the sky first until the R94 receiver fix (Phase 7a, not 7b), and every 2-phase annual result
    before it was computed misaligned.** The ground's radiance was applied to the first sky patch's
    coefficient, every sky patch's radiance landed on the previous patch's coefficient, and the
    ground's own coefficient received the *zenith* patch's radiance. Nothing detected it:
    `check_matrix_shape` compares 2306 against 2306 and is satisfied, which is
    [engine invariant 3](../../docs/tool-surface.md#engine-invariants)'s hazard occurring in the one
    place the invariant cannot look. Measured on the office fixture at the three check-3.1 points,
    the correction moves them **-7.2%, +2.1%, -11.2%** -- a few percent rather than an order of
    magnitude, because neighbouring sky patches carry similar radiance, which is precisely why it
    survived every gate.

    Both indices are measured, not assumed, by
    `tests/test_annual.py::TestReceiverAgainstRealRfluxmtx::
    test_the_ground_bin_sits_where_gendaymtx_puts_the_ground_row`: a down-facing sensor in an
    otherwise empty scene finds the receiver's ground bin, a `-g` sweep finds `gendaymtx`'s ground
    row, and the test asserts the two **indices are equal**. Two separate assertions -- "the
    receiver's ground bin is last", "gendaymtx's ground row is 0" -- would each have passed on the
    defect; the invariant is the equality, so the equality is what is written down.

    **Both hemispheres, always** (module docstring's "dimensional trap"): `h=r{mf}` gives the
    `144*mf^2 + 1` sky patches `rad_engine.skymatrix.reinhart_row_count(mf) - 1` counts, `h=u`
    (uniform, one bin) gives the single ground patch, and the sum is exactly what a
    `gendaymtx -m {mf}` matrix has rows -- `reinhart_row_count(mf)` itself. A caller cannot build a
    one-hemisphere receiver by omission the way the hand-run's first attempt did (module docstring),
    because there is no second code path that assembles this text a different way: this function is
    the only place `#@rfluxmtx h=` lines are written in this module. **The sky hemisphere carries `u=+Y` and the ground `u=+Z`, and the difference
    is not cosmetic.** `u=` gives `rfluxmtx` the reference direction for *azimuth*, so it must be
    **perpendicular to the hemisphere's own axis**. `skyglow source sky` points along `0 0 1`, so
    `u=+Z` there is parallel to the axis and leaves azimuth undefined -- every azimuth in an altitude
    ring collapses into one bin. The ground block is `h=u`, a single uniform bin with no azimuthal
    subdivision to lose, so its `u=+Z` is harmless and is left alone.

    **This file said `u=+Z` on the sky block until the R95 azimuth fix (Phase 7a, not 7b), and the two-phase
    chain had no azimuthal sky resolution at all.** The original reasoning -- that `u=+Z` keeps the
    receiver *"consistent with the sensor points' own `+Z`-up direction vector"* -- conflates the
    **sensor's** up direction with the **receiver hemisphere's** azimuth reference; they are
    unrelated, and only the second one `u=` describes. The fingerprint was arithmetic: an
    unobstructed sensor gave **exactly 29 nonzero bins of 2306**, at strides of 120/96/72/48/24 (the
    Reinhart patch counts per ring), because 29 is the number of altitude rings at `MF:4`. Measured
    invariant to `-ad 1024 -> 65536`, `-c 1 -> 10000` and `-aa 0.2` vs `0`: no amount of sampling
    recovers a coordinate the binning function does not have.

    **Nothing could have caught it by conservation**: the bin total was always exactly `pi`, so flux
    was conserved and only direction was destroyed -- which is where the sun is. Measured on an
    open-sky up-facing sensor against `rtrace` on the identical sky: **6 953 lux vs 83 965**, a 12x
    deficit, becoming **91 690** with the up-vector perpendicular. The residual 9% is the sun spread
    over three patches, ordinary discretisation.

    Raises `ValueError` for an `mf` outside `SUPPORTED_SUBDIVISIONS` -- **not because `reinhart.cal`
    would refuse it** (it computes a bin count for any `mf`), but because this project has not run
    real `rfluxmtx` (or, for `mf=1`/`mf=2`, the same code path proven at the other two subdivisions)
    against a receiver at that value (see `SUPPORTED_SUBDIVISIONS`'s own comment for exactly what
    was measured where). Returning a receiver text for an unmeasured subdivision would be exactly the
    "formula nobody checked against the real binary" gap `rad_engine.matrices.reinhart_bins`'s own
    docstring describes avoiding for the analogous 3-phase D-matrix lookup, applied here to the
    2-phase receiver.

    Verified against real `rfluxmtx` on this machine (Radiance 6.1a) at `mf=4`: this text, as
    `receiver.rad`, against the office fixture's `officeA_base.rad` and the three check-3.1 points,
    gives `NCOLS=2306` -- matching a `-m 4` sky vector's `NROWS=2306` exactly. The single-hemisphere
    version (sky only) gives `NCOLS=2305` and `dctimestep` then fails loudly (module docstring).
    """
    if mf not in SUPPORTED_SUBDIVISIONS:
        raise ValueError(
            f"sky_subdivision={mf} is not one of {sorted(SUPPORTED_SUBDIVISIONS)}, the "
            "subdivisions this project has run its rfluxmtx receiver against (see "
            "rad_engine.annual.SUPPORTED_SUBDIVISIONS); reinhart.cal itself would compute a bin "
            "count for other values, but this server has not measured a receiver built at one"
        )
    return (
        "#@rfluxmtx h=u u=+Z\n"
        "void glow groundglow\n"
        "0\n"
        "0\n"
        "4 1 1 1 0\n"
        "groundglow source ground\n"
        "0\n"
        "0\n"
        "4 0 0 -1 180\n"
        "\n"
        f"#@rfluxmtx h=r{mf} u=+Y\n"
        "void glow skyglow\n"
        "0\n"
        "0\n"
        "4 1 1 1 0\n"
        "skyglow source sky\n"
        "0\n"
        "0\n"
        "4 0 0 1 180\n"
    )


#: `dc_receiver_text`'s first line, as a compiled pattern -- `parse_receiver_subdivision` reads `mf`
#: back out of exactly this token, never out of anything the sky/ground glow primitives below it
#: contain. `re.match` anchors at the string start, so a receiver whose sky hemisphere line has been
#: altered or is missing entirely fails to match rather than matching some other line by accident.
_RECEIVER_SUBDIVISION_RE: Final[re.Pattern[str]] = re.compile(r"#@rfluxmtx h=r(\d+) u=\+Y")


def parse_receiver_subdivision(text: str) -> int:
    """Read the Reinhart subdivision `mf` back out of a receiver file's own bytes.

    **The second, independent source `_shape_check` (`rad_mcp.annual_tool`) needs.** Comparing a
    freshly-computed `dc_receiver_text(mf)` against itself proves nothing -- both sides are the
    identical expression evaluated twice, so the comparison cannot go red for any input (the
    tautology `rad_mcp.annual_tool._shape_check`'s own docstring names). What can go red is
    comparing the `mf` a caller *intends* against the `mf` the receiver file *on disk* actually
    declares, read back independently of whatever this call believes it wrote -- catching a stale or
    mismatched file left by a previous write to the same path, not merely a coding error in one
    in-memory computation.

    Reads only the sky hemisphere's `#@rfluxmtx h=r{mf} u=+Y` line and ignores the rest of the file,
    exactly as `parse_dc_matrix_header` reads only a matrix's header block and ignores its data rows:
    this function answers one question (which `mf` is this receiver built at) and nothing else.

    **The `u=+Y` in that pattern is not cosmetic, and this docstring named `u=+Z` for two commits
    after the code stopped writing it.** `u=` is `rfluxmtx`'s reference direction for *azimuth*, so
    it must be perpendicular to the hemisphere's own axis; `u=+Z` is parallel to `skyglow source
    sky` and collapsed every azimuth in a ring into one bin (ruling R95). A caller who acted on the
    old refusal message -- which told them the file must contain `u=+Z` -- would have edited the
    receiver straight back into that defect. Both the prose and the message are corrected here on
    the whole-branch review; the regex itself was right throughout.

    **It scans for that line rather than reading the first one, and that changed with the R94/R95
    receiver fixes (Phase 7a, not 7b -- see `dc_receiver_text`).** The sky block used to be first; the ground block is now, because `rfluxmtx`
    numbers bins in block order and `gendaymtx`/`genskyvec` put the ground in row 0 (see
    `dc_receiver_text`). Only the `h=r{mf}` line carries a subdivision -- `h=u` is a single uniform
    bin at every `mf` -- so scanning finds exactly the same line the anchored read used to, and one
    file cannot contain two of them: `dc_receiver_text` is the only writer of `#@rfluxmtx h=` lines
    in this module and writes one sky hemisphere. Scanning is therefore not a loosening of what is
    accepted, only a removal of a positional assumption that the fix invalidated.

    Raises `ValueError` if no line matches that pattern -- a receiver file this project did not write
    (or one truncated, corrupted, or hand-edited) is not silently read as "some default `mf`"; the
    caller must decide what a receiver it cannot identify means, the same discipline
    `parse_dc_matrix_header` holds for a header missing `NROWS`/`NCOLS`/`NCOMP`.
    """
    for line in text.splitlines():
        match = _RECEIVER_SUBDIVISION_RE.match(line)
        if match is not None:
            return int(match.group(1))
    raise ValueError(
        "receiver text declares no Reinhart sky hemisphere in the form "
        f"'#@rfluxmtx h=r<mf> u=+Y': {text[:120]!r}. This is not a receiver dc_receiver_text "
        "wrote, or it has been altered since."
    )


def read_receiver_subdivision(path: Path) -> int:
    """Read a receiver file off disk and parse its `mf` via `parse_receiver_subdivision`.

    **The file-side entry point `parse_receiver_subdivision` does not have, and why a caller needs
    one** -- `read_ill`'s own docstring gives the identical reasoning for the identical shape: a
    caller verifying what is actually on disk (`rad_mcp.annual_tool._shape_check`, this module's own
    "second source" the receiver-filename collision finding asked for) needs a path-in, not a
    text-in, entry point. This is that read and nothing more; the parse rules live in
    `parse_receiver_subdivision` alone (ruling R30).

    Raises `MatrixFileUnreadableError` -- never a bare `OSError` -- when the path does not exist, is
    a directory, or the OS otherwise refuses the read (`read_ill`'s own `open_regular_file` guard
    against a FIFO blocking forever, applied here to a much smaller file for the same reason: R48 is
    "in either direction"); and `ValueError` (from the parser, unchanged) when the bytes were read
    but do not declare a Reinhart sky hemisphere.
    """
    path = Path(path)
    try:
        with open_regular_file(path, "rb") as handle:
            raw = handle.read()
    except OSError as exc:
        logger.info("read_receiver_subdivision refused %s: %s", path, exc)
        raise MatrixFileUnreadableError(
            f"the receiver file at {path} could not be read: {exc}. Nothing about its content has "
            "been judged -- the filesystem refused the read, so check the path exists, is a file "
            "rather than a directory, and is readable by this server.",
            path=path,
        ) from exc
    return parse_receiver_subdivision(raw.decode("utf-8", errors=MATRIX_DECODE_ERRORS))


@dataclass(frozen=True)
class DcMatrixSpec:
    """Everything `build_rfluxmtx_argv` needs to build one `rfluxmtx` daylight-coefficient run.

    Mirrors `rad_engine.rtrace.RtraceRequest`'s shape -- `parameters` is the identical
    `QualityParameters` a point-in-time run uses, reused rather than reimplemented, since `rfluxmtx`
    passes the same five ambient flags straight through to `rcontrib` (module docstring; the man
    page: "All other supported options are passed on to rcontrib(1)").
    """

    scene_files: list[str]
    """The scene `.rad` files `officeA_base.rad`-style, resolved relative to the working directory
    the caller spawns `rfluxmtx` from -- the same convention `rad_engine.octree.build_octree` uses
    for its own `scene_files`. These follow the receiver file on the command line (the man page's
    `system.rad` position); `dc_receiver_filename(mf)` (and the `dc_receiver_text(mf)` written to it)
    are not part of this list, they are built in by `build_rfluxmtx_argv` itself."""
    n_sensors: int
    """`rfluxmtx -y` -- the sensor count, matching `.pts` line count exactly
    (`rad_engine.sensors.PointsFile.count`). Required, not inferred: `rfluxmtx`'s pass-through mode
    (a bare `-` sender, points on stdin) needs `-y` stated explicitly or it does not know how many
    rows to expect on stdin, unlike a real sender surface where the sample count has its own
    default."""
    parameters: QualityParameters
    """The five ambient flags -- reused from `rad_engine.rtrace`, not reimplemented, per the task
    brief's explicit instruction and matching `SkyMatrixSpec`'s own reuse of `sky.
    north_rotation_flag`."""
    sky_subdivision: int
    """`build_rfluxmtx_argv` **does** read this now, unlike before Phase 7a's fix for the finding
    that `dc_receiver_filename`'s docstring records: it is what `dc_receiver_filename(mf)` derives
    the receiver's own filename from, so the argv names the file the caller was told to write
    `dc_receiver_text(mf)` to (`genskyvec -m`/`gendaymtx -m` reuse this same value for the matching
    sky call). Load-bearing, not bookkeeping: a `DcMatrixSpec` built with the wrong `sky_subdivision`
    now names the wrong receiver file on the command line, not merely a caller's private mismatch."""
    processes: int | None = None
    """`-n`, immediately before the `-` sender: `rfluxmtx` passes `-n` to `rcontrib`
    (rfluxmtx.1: "All other supported options are passed on to rcontrib(1)"; `-n` is not among
    the six it controls; measured with `-v`: `rcontrib -fo+ -ab 0 -n 2 -I+ ...`);
    `None` passes nothing (R781)."""


def build_rfluxmtx_argv(spec: DcMatrixSpec) -> list[str]:
    """The `rfluxmtx` argv for one daylight-coefficient run, in the order verified against real
    Radiance on this machine (module docstring's hand-run): ``rfluxmtx -I+ -y n_sensors -ab AB -ad
    AD -as AS -ar AR -aa AA [-dt DT -dc DC] -lw 1e-4 [-n N] - receiver_mf{mf}.rad
    scene_files...``.

    `-I+` selects irradiance mode (`rcontrib`'s own flag, passed through by `rfluxmtx`) -- the same
    non-negotiable role `-I` plays in `rad_engine.rtrace.build_rtrace_argv`, and for the identical
    reason: it is the whole point of this tool, so it is not part of `QualityParameters` and cannot
    be overridden away.

    `-lw 1e-4` is fixed here rather than left unset, matching the value the hand-run's chain used
    and `rtrace`'s own measured binary default (`rad_engine.rtrace`'s module docstring, ruling R41).
    Unlike `rtrace`, `rfluxmtx`/`rcontrib` has no equivalent binary default recorded in this
    project's own measurements, so leaving it unset here would be a fresh, unverified assumption
    about a different binary rather than the same reused one.

    `-n spec.processes` goes immediately before the `-` sender when it is not `None` (R781);
    `rfluxmtx` passes it to `rcontrib`. Each scene operand is spelled by the caller
    (`rad_engine.chain._relative_to_cwd`, which prefixes `./` to a name beginning with `-`, R782).

    Returns a plain argv **leading with the bare binary name**, matching every other `build_*_argv`
    in this codebase -- `rad_engine.run.run` substitutes `env.path("rfluxmtx")` for that first
    token. The `-` sender means points arrive on stdin, exactly as
    `rad_engine.rtrace.build_rtrace_argv` documents for its own `.pts` contract; this function does
    not read or know about the points file itself.

    **The receiver filename is `dc_receiver_filename(spec.sky_subdivision)`, not a fixed literal.**
    A caller must still write `dc_receiver_text(spec.sky_subdivision)` to that exact path before
    spawning this argv (`run_rfluxmtx`'s own docstring) -- this function only names the file on the
    command line, it does not write it -- but the name it names now carries `mf`, which is what
    keeps two `DcMatrixSpec`s at different subdivisions from ever naming the same file.
    """
    p = spec.parameters
    argv: list[str] = [
        "rfluxmtx", "-I+",
        "-y", str(int(spec.n_sensors)),
        "-ab", str(p.ambient_bounces),
        "-ad", str(p.ambient_divisions),
        "-as", str(p.ambient_super_samples),
        "-ar", str(p.ambient_resolution),
        "-aa", repr(float(p.ambient_accuracy)),
    ]
    if p.direct_threshold is not None:
        argv.extend(["-dt", repr(float(p.direct_threshold))])
    if p.direct_certainty is not None:
        argv.extend(["-dc", repr(float(p.direct_certainty))])
    argv.extend(["-lw", "1e-4"])
    if spec.processes is not None:
        argv.extend(["-n", str(int(spec.processes))])  # R781: before the sender
    argv.extend(["-", dc_receiver_filename(spec.sky_subdivision)])
    argv.extend(spec.scene_files)
    return argv


def build_genskyvec_argv(sky_subdivision: int) -> list[str]:
    """The `genskyvec` argv for one Reinhart sky vector: ``genskyvec -m sky_subdivision``.

    No positional file argument -- `genskyvec` reads the sky description (a completed `gensky`/
    `gendaylit` `.rad` text, `rad_engine.sky.sky_description_text`'s own output shape, or the bare
    `gensky`/`gendaylit` stdout the corpus's `genskyvec` examples pipe in directly) from stdin and
    writes the vector to stdout, matching `rad_engine.rtrace.build_rtrace_argv`'s own stdin
    convention for points rather than `gendaymtx`'s positional `.wea` path.

    Returns a plain argv **leading with the bare binary name**, the same contract every
    `build_*_argv` in this codebase holds.
    """
    return ["genskyvec", "-m", str(int(sky_subdivision))]


def build_dctimestep_argv(dc_matrix_path: Path, sky_vector_path: Path) -> list[str]:
    """The `dctimestep` argv for one matrix multiplication: ``dctimestep <DC matrix> <sky vector>``.

    Order is the arithmetic, not a convention: `dctimestep` dots each *row* of the first argument
    against the second (`check_matrix_shape`'s own docstring), so swapping the two is not a syntax
    error -- it is a different, and wrong, computation that Radiance may well perform without
    complaint. That is the same "valid file, plausible wrong answer" shape engine invariant 3 names,
    which is why the pairing lives in one builder rather than at each call site.

    **`dctimestep` needs no stdin at all, and that is what makes it expressible as a job step.**
    Measured on Radiance 6.1a on this machine: ``dctimestep A.mtx GA.vec`` with **stdin closed**
    exits 0, and its data rows are byte-identical to the piped form ``dctimestep A.mtx < GA.vec``
    (117 bytes of data, `cmp`-clean, over the office fixture's three points). Both inputs are file
    arguments; the sky vector's file position is optional on the command line only because the
    binary will fall back to stdin for it, never because it requires it. So this argv can be a
    `rad_engine.jobs.Step` with `stdin_path=None` -- which the worker turns into `DEVNULL` -- and
    the 2-phase chain's last step needs none of the stdin plumbing ruling R43 had to add for
    `rfluxmtx`'s sensor points.

    Two things about that measurement are **not** claimed, because they are not true. The *whole*
    outputs of the two forms are not identical: a Radiance header echoes its own command line, so
    line 2 reads ``dctimestep A.mtx GA.vec`` in one and ``dctimestep A.mtx`` in the other, and the
    `CAPDATE=`/`GMT=` stamps differ between any two runs however invoked (two successive runs of the
    *identical* argv also differ byte-for-byte for that reason alone). The claim this module rests
    on is about the numbers, and it is the numbers that were compared.

    Returns a plain argv **leading with the bare binary name**, the same contract every
    `build_*_argv` in this codebase holds -- `rad_engine.run.run` substitutes
    `env.path("dctimestep")` for that first token. Resolving it here would need a `RadianceEnv`
    this builder has no other use for, and `run` would resolve it a second time regardless.
    """
    return ["dctimestep", str(dc_matrix_path), str(sky_vector_path)]


def build_three_phase_argv(v: Path, t: Path, d: Path, s: Path) -> list[str]:
    """The `dctimestep` argv for one 3-phase matrix multiplication: ``dctimestep V T D S``.

    `dctimestep -h` on this machine publishes two usage forms, and this builder is the second one::

        Usage: dctimestep [-n nsteps][-o ospec][-x xr][-y yr][-i{f|d|h}][-o{f|d|c}] DCspec [skyf]
           or: dctimestep [-n nsteps][-o ospec][-x xr][-y yr][-i{f|d|h}][-o{f|d|c}] Vspec Tbsdf
               Dmat.dat [skyf]

    `Vspec Tbsdf Dmat.dat [skyf]`, in that order -- ``V`` (the view/Klems matrix), ``T`` (the BSDF
    transmission matrix), ``D`` (the Klems-to-Reinhart daylight matrix), then the sky vector last,
    exactly as `build_dctimestep_argv`'s own two-argument form is the first usage line's
    `DCspec [skyf]`; that function's docstring is about *that* form only, not this one.

    **Order is the arithmetic, not a convention** (`build_dctimestep_argv`'s own words) -- and with
    four operands the risk is larger than with two, because `dctimestep V T D S` composes as
    `((V @ T) @ D) @ S` (`check_three_phase_shapes`'s own docstring), and three of the four wrong
    orderings of four file arguments still name a *runnable* command on shapes that happen to be
    close in size: they fail loudly only if the mismatched dimensions actually disagree, and are
    otherwise a plausible wrong answer computed without complaint (engine invariant 3).

    **Measured directly against Radiance 6.1a on this machine**, over synthetic matrices in the
    exact 3-phase shape (V 2x145 of 0.01, T 145x145 of 0.001, D 145x146 of 0.01, S 146x4 of 1000)::

        $ dctimestep V.mtx T.mtx D.mtx S.mtx
        exit=0, stderr empty
        NROWS=2  NCOLS=4  NCOMP=3
        3.069650e+02 ...

    and `0.01 * 0.001 * 0.01 * 1000 * 145 * 145 * 146 = 306.965` -- the output is the exact matrix
    product, confirming this argv order performs `((V @ T) @ D) @ S` as intended and not some other
    pairing of the same four files.

    Returns a plain argv **leading with the bare binary name**, the same contract every
    `build_*_argv` in this codebase holds -- `rad_engine.run.run` substitutes
    `env.path("dctimestep")` for that first token.
    """
    return ["dctimestep", str(v), str(t), str(d), str(s)]


def build_rmtxop_sum_argv(*, inputs: Sequence[Path]) -> tuple[str, ...]:
    """``rmtxop A + B + ...`` -- the element-wise sum of two or more annual results.

    **Here rather than in `rad_engine.matrices` (ruling R119).** `matrices.py` holds the `rcontrib`
    and `genklemsamp` builders that make a coefficient matrix; every `rmtxop`/`dctimestep`/
    `rcollate` builder -- the ones that *combine* matrices already built -- lives in this module,
    beside `build_three_phase_argv` and `build_dctimestep_argv`.

    **The three-phase result is linear in the window groups**, so summing the per-group `.ill` files
    **is** the multi-group result rather than an approximation of it. That decomposition is this
    repo's own inference from the linearity of light transport and from `rad_engine.chain.
    GLOW_MODIFIER` being formatted with each group's own name -- one `rcontrib` modifier per group,
    so group `g`'s V captures group `g`'s aperture and no other. The LBNL three-phase guide never
    states a sum over window groups and is not cited for one (ruling R132).

    **Measured on Radiance 6.1a on this machine**, over 2x3 ascii matrices: `A + B` is the
    element-wise sum, exit 0, **empty stderr**, with `NROWS`/`NCOLS`/`NCOMP`/`FORMAT` preserved on
    the output; it chains over three or more operands; and it fails **loudly** (exit 1) on a
    dimension or `NCOMP` mismatch rather than producing a plausible wrong shape.

    **Refuses fewer than two inputs**: `rmtxop A` with one operand is a copy, and a copy sealed
    under an id that says "sum" would report one facade's fenestration as the whole story -- the
    exact substitution `rad_engine.chain.ThreePhaseTarget.single`'s own refusal names. The chain's
    single-group runs never reach here at all: they seal the multiply's own output as `ill:<grid>`
    (see `three_phase_steps`), which is a real result and not a copy of one.

    Returns a `tuple`, not a `list`, unlike its siblings above: `Step.argv` is a tuple and every
    call site of those wraps them in `tuple(...)`. Nothing else in this module returns one; stated
    here so the asymmetry reads as deliberate.
    """
    if len(inputs) < 2:
        raise ValueError(
            f"the window-group sum needs at least two operands, not {len(inputs)}. One operand "
            "makes this a copy sealed under an id that says it is a sum."
        )
    argv: list[str] = ["rmtxop", str(inputs[0])]
    for path in inputs[1:]:
        argv += ["+", str(path)]
    return tuple(argv)


def build_rcollate_leg_argv() -> list[str]:
    """The `rcollate` argv every one of the five-phase method's three terms is piped through after
    its own `dctimestep`: ``rcollate -ho -oc 1`` (`docs/specs/five-phase-method.md` sec 5, steps
    2.6, 3.6 and 4.5).

    **`-ho`, not the spec's `-h`, and the difference is measured rather than preferred** (Phase 7a
    Task 13, found on the first end-to-end run of this chain -- Task 12 built the combination and
    every test of it was an argv comparison, so no test had ever fed a real `dctimestep` stream
    through this step). `man rcollate`, verified against Radiance 6.1a on this machine: *"By
    default, the file is assumed to include an information header... The -hi option may be used
    to turn off the expectation of a header on input. The -ho option turns off header output, and
    -h by itself turns off both input and output headers."* And `man dctimestep`: *"A header will
    normally be produced on the output, unless the -h option is given."*

    So the spec's own pipe is internally inconsistent: its `dctimestep` carries no `-h`, therefore
    writes a header, and its `rcollate -h` then declares that header to be data. Reproduced exactly,
    on a three-line fixture and again on a real 456-sensor leg::

        printf '#?RADIANCE\\nNROWS=2\\nNCOLS=3\\nNCOMP=3\\nFORMAT=ascii\\n\\n1 1 1 2 2 2 3 3 3\\n..'
        rcollate -h  -oc 1 m.mtx   ->  exit 1, "Incomplete record / unexpected EOF"
        rcollate -ho -oc 1 m.mtx   ->  exit 0, six single-triplet rows, sensor-major

    `-ho` is the fix that stays in this function. The alternative -- adding `-h` to the legs'
    `dctimestep` so the spec's `rcollate -h` becomes correct -- would have to be threaded through
    `build_three_phase_argv` and `build_dctimestep_argv`, both of which are **shared with the
    3-phase and 2-phase chains**, whose final step writes the published `.ill` and whose header is
    what `parse_dc_matrix_header` reads a column count out of. One token here, in the function that
    exists for these three legs alone, against a `header=` parameter on two builders used by three
    chains: the same "smaller, non-colliding diff" reasoning, and it does not put a published
    result's header at the mercy of a five-phase-only flag.

    **A consequence worth naming rather than discovering: the five-phase `.ill` used to carry no
    header.** Before Phase 7b Task 1 (ruling R98), the final step ran `rcollate -h -fa1 -ic n -t`
    (`build_rcollate_final_argv`). Its `-h` correctly suppressed the *input* header -- its input is
    `rcalc`'s stdout, which carries none once these legs stop emitting one -- but `-h` suppresses the
    *output* header too, so a five-phase result was a bare ASCII matrix while a 3-phase one (written
    straight by `dctimestep`) has a `#?RADIANCE` block. Anything that reads a column count back out
    of the file (`parse_dc_matrix_header`) could not read a five-phase `.ill`. Measured, not
    inferred: an 8760x456 headerless file.

    **That gap had two halves, and Phase 7b Task 1 closed both** (Task 13 review round 1 measured the
    header half first, on the same pipe -- recorded here so Tasks 15/16 did not have to re-derive
    it). The *header* half was cheap: `rcollate -hi` (expect an input header, emit an output one) on
    the identical final step produces a full block::

        rcollate -hi -fa1 -ic 2 -t lam.txt
        #?RADIANCE / rcollate -hi -fa1 -ic 2 -t / NCOMP=1 / NROWS=2 / NCOLS=3 / FORMAT=ascii

    -- exactly the fields `parse_dc_matrix_header` reads. The *shape* half was not a flag at all: with
    the `-t` still present (as in the measurement just above), a five-phase `.ill` would publish
    hours x sensors of **scalar photopic lux** (`NCOMP=1`, because `build_five_phase_combine_argv`'s
    `rcalc` has already applied the 179*(.265r+.670g+.065b) conversion), while a 3-phase `.ill` is
    sensors x hours of **RGB**. A header would then *report* a shape no reader of the 3-phase `.ill`
    expects, which is worse than no header only if nobody notices. R98 decided the contract rather
    than leaving it deferred: `build_rcollate_final_argv` now drops `-t` entirely, so the incoming
    sensor-major stream (this function's own `-oc 1`) is published as-is -- sensors x hours, the same
    layout the 2- and 3-phase chains write -- and the quantity (`NCOMP=1` scalar lux instead of RGB)
    is the one thing that still tells a five-phase `.ill` apart from a three-phase one.

    **And the hedge is now a measurement.** Sec 5 states this step's output is *"assumed to be...
    likely in a sensor-major format"*. Measured on the fixture above: a 2x3 input emerges as
    `(r0c0, r0c1, r0c2, r1c0, r1c1, r1c2)`, one triplet per line -- rows are sensors and columns are
    timesteps in a `dctimestep` result, so that is all hours for sensor 1, then all hours for sensor
    2. Sensor-major, as the spec guessed. Check 7.4 (Task 15) is still what proves the *arithmetic*
    that ordering feeds; this only settles the shape.

    Takes no arguments: unlike `build_dctimestep_argv`/`build_three_phase_argv`, every operand this
    step touches is a `Step.stdin_path`/`stdout_path`, never an argv token (R43's own convention,
    reused rather than reinvented for a step whose sole job is to reshape a stream already flowing
    through the job).

    Returns a plain argv **leading with the bare binary name** -- `rad_engine.run.run` substitutes
    `env.path("rcollate")` for that first token.
    """
    return ["rcollate", "-ho", "-oc", "1"]


def build_rlam_argv(leg_paths: Sequence[Path]) -> list[str]:
    """`rlam <path> <path> <path>` -- pastes the three legs' sensor-major records side by side so
    `rcalc` sees nine fields per output record, three RGB triples in a row
    (`docs/specs/five-phase-method.md` sec 5: *"rlam: Pastes records from the three files"*).

    **Order is the arithmetic, the same discipline `build_three_phase_argv`'s own docstring holds
    for its own four operands.** `build_five_phase_combine_argv`'s `r=$1-$4+$7` addresses fields 1-3
    as the *first* path's RGB, 4-6 as the *second*'s, 7-9 as the *third*'s -- so `leg_paths` must be
    `(i_3ph, i_ds3ph, i_ds5ph)`, in that exact order, for the combination's `-`/`+` signs to land on
    the term each was written for. Three of the six orderings of three file arguments still name a
    runnable command that emits nine columns and computes *some* combination without complaint --
    engine invariant 3's "plausible wrong answer" shape, applied to file order rather than matrix
    shape.

    **Exactly three, refused otherwise -- ruling R138's sweep, taken when `build_rmtxop_sum_argv`
    gained its own operand-count guard (Phase 7d-1 Task 7).** These two are this module's only
    builders that take a variable-length operand sequence, and this one's count is not free: its
    single consumer, `build_five_phase_combine_argv`, addresses fields `$1`-`$9` by position, so
    two legs leave `$7`-`$9` unbound and four silently drop the fourth leg's contribution from the
    combination while `rlam` and `rcalc` both exit 0. Not reachable today -- `chain.five_phase_steps`
    passes a three-element literal -- and stated as a guarantee this module holds rather than one
    its only caller happens to satisfy.

    Returns a plain argv **leading with the bare binary name** -- `rad_engine.run.run` substitutes
    `env.path("rlam")` for that first token.
    """
    if len(leg_paths) != 3:
        raise ValueError(
            f"the five-phase combination pastes exactly three legs, not {len(leg_paths)}. "
            "build_five_phase_combine_argv reads fields $1-$9 by position, so any other count "
            "either leaves a term unbound or drops one from the sum without an error."
        )
    return ["rlam", *(str(path) for path in leg_paths)]


def build_five_phase_combine_argv() -> list[str]:
    """The `rcalc` argv that turns `rlam`'s nine-field stream into one lux value per record:
    ``I_5ph = V*T*D*S - Vd*T*Dd*Sds + Cds*Ssun``, per RGB channel, converted to photopic illuminance
    (`docs/specs/five-phase-method.md` sec 5)::

        rcalc -e r=$1-$4+$7 -e g=$2-$5+$8 -e b=$3-$6+$9 -e $1=179*(.265*r+.670*g+.065*b)

    **The sign of each term is the whole physics, and there is nothing else in this repo that can
    catch it flipped before a real run does.** `$1-$4+$7` takes the 3-phase answer (fields 1-3, the
    first leg `rlam` pasted), subtracts its own smeared sun (4-6, the second leg), and adds back the
    accurately-traced sun (7-9, the third leg) -- exactly `I_5ph = I_3ph - I_ds3ph + I_ds5ph`, restated
    per channel. `$1+$4-$7` still runs, still emits one lux column, and is simply wrong (engine
    invariant 3: a valid file, a plausible wrong answer). The mutation this function's own test
    exercises (flipping this sign) proves only that the *argv* changed -- it is check 7.4
    (`docs/superpowers/plans/2026-08-17-phase-7a-five-phase-chain.md` Task 15), a sunless run whose
    `I_5ph` must equal `I_3ph` exactly, that proves the *physics*; see
    `rad_engine.chain.five_phase_steps`'s own docstring for why that distinction is stated rather
    than assumed.

    **Which sign check 7.4 actually proves, now that it exists and has been mutated (Task 15).** It
    proves the sign of `$1` -- swapping the first two `rlam` operands makes it publish `-1167.36876`
    where `+1167.36876` is right, and it goes red with that message. It does **not** prove the signs
    of `$4` and `$7`: under a sunless sky both of those terms are multiplied by a zero sky matrix, so
    `-e "r=$1-$4-$7"` leaves check 7.4 green (measured).

    **What its negative control adds, restated after Phase 7a Task 15b.** Until 15b this docstring
    said `Cds` was *"an identically zero matrix on every project this server can build, because the
    suns octree blackens the glazing polygon sitting behind the BSDF proxy"*, and concluded that
    `+$7` was unfalsifiable for that reason. **The premise is now false**: 15b removes the aperture
    from the suns octree instead of blackening it, and `Cds` carries signal on every scene measured
    (`tests/fixtures/README.md`, finding 4; pinned by `tests/test_five_phase.py::
    TestFivePhaseIsAdmitted::test_cds_is_not_identically_zero_through_the_sealed_suns_octree`).

    **The conclusion survives, for a weaker and entirely different reason**: no test in this repo
    compares a sunlit five-phase result against an independent expectation. The gate multiplies `$7`
    by a zero `Ssun`; the negative control (`tests/test_five_phase.py::TestCheck7_4TheGate::
    test_check_7_4_does_not_pass_for_a_sky_that_has_a_sun`) asserts only that the five-phase and
    three-phase results *differ*, which the `-$4` term alone already guarantees -- measured after the
    repair, with `Cds` carrying signal: flipping all three signs to `-e "r=$1-$4-$7"` (and `g`, `b`)
    still leaves that control green. So `+$7`'s sign is still unfalsified by any run, but it is no
    longer *unfalsifiable*: the term now contributes, and a check that predicted a sunlit value
    rather than merely a difference could close it. Stated here rather than left as an implication of
    the sentence before it, and stated as what it is -- a gap in the checks, not a zero in the
    matrix.

    **Four separate `-e` flags, not one semicolon-joined clause, even though `docs/specs/
    five-phase-method.md`'s own sec 5 line writes the first three together**
    (``'r=$1-$4+$7; g=$2-$5+$8; b=$3-$6+$9'``). `man rcalc` treats repeated `-e` options as additional
    expressions evaluated in the order given -- the same effect as one semicolon-joined clause -- and
    four independent tokens is what lets a test assert each assignment on its own
    (``"r=$1-$4+$7" in argv``) rather than parsing a compound string apart to tell a flipped sign in
    `r` from one in `g` or `b`.

    The second `-e`, ``$1=179*(.265*r+.670*g+.065*b)``, is the identical photopic weighting
    `rad_core.photometry.to_lux` computes (`CIE_PHOTOPIC_WEIGHTS = (0.265, 0.670, 0.065)`, the same
    179 lm/W efficacy) -- reproduced as a literal here rather than imported, which is the one
    deliberate exception to ruling R30's "one arithmetic path" in this module: `to_lux` is a Python
    function over an already-read RGB tuple, and this expression runs *inside* an `rcalc` subprocess
    over a stream this server never reads into Python at all, so there is no in-process value to hand
    it. Two independently-typed statements of the same conversion is exactly how they drift apart
    (`rad_core.photometry`'s own module docstring); this one is checked against `to_lux`'s published
    weights rather than invented fresh.

    Returns a plain argv **leading with the bare binary name** -- `rad_engine.run.run` substitutes
    `env.path("rcalc")` for that first token.
    """
    return [
        "rcalc",
        "-e", "r=$1-$4+$7",
        "-e", "g=$2-$5+$8",
        "-e", "b=$3-$6+$9",
        "-e", "$1=179*(.265*r+.670*g+.065*b)",
    ]


def build_direct_lux_argv() -> list[str]:
    """The `rcalc` argv that converts the ASE direct-sun leg's RGB stream to one photopic lux value
    per record: ``rcalc -e '$1=179*(.265*$1+.670*$2+.065*$3)'``.

    **The single-term form of `build_five_phase_combine_argv`.** That function reads nine fields --
    three legs of three channels -- and subtracts before it converts (`$1-$4+$7` per channel). This
    one has a single operand: `dctimestep` over the ASE direct-sun matrix and its sun vector emits
    one RGB triple per record, and there is nothing to add or subtract from it, so the channels are
    weighted straight out of `$1`/`$2`/`$3` with no intermediate `r`/`g`/`b` assignments. No
    subtraction is not an omission to check against the five-phase form -- it is what one operand
    means.

    **The R30 exception, and it is the same one.** Read `build_five_phase_combine_argv`'s own
    paragraph on this literal in full: `rad_core.photometry.to_lux` states this conversion once for
    the whole project (`CIE_PHOTOPIC_WEIGHTS = (0.265, 0.670, 0.065)`, 179 lm/W), and ruling R30
    requires one arithmetic path. The single sanctioned exception is exactly this case -- an
    expression evaluated *inside* an `rcalc` subprocess, over a stream this server never reads into
    Python, so there is no in-process value to hand `to_lux`. The literal is checked against
    `to_lux`'s published weights rather than invented fresh, and `tests/test_annual.py::
    test_the_rcalc_weights_are_to_lux_s_own` is that check.

    **Never `rmtxop -c 47.4 119.9 11.6`,** which is the other way this project could have converted
    an off-Python matrix and is refused by name. `rad_core.photometry`'s own module docstring names
    that exact command as the second of *"two independently-typed constants for the same physical
    conversion... exactly how they drift apart"* -- and its digits are a **third** set, rounded, next
    to `to_lux`'s pinned `47.435 / 119.93 / 11.635`. `179*.265 = 47.435`, not `47.4`; the shortest
    statement of the drift is that the two forms disagree in the fourth significant figure before
    anyone has edited either. The `rcalc` form carries the weights themselves, so there is one place
    the numbers live and `to_lux` is it.

    Returns a plain argv, leading with the bare binary *name* the way every builder in this module
    does -- `rad_engine.run.run` splits `binary, *args = argv` and substitutes `env.path("rcalc")`
    for that first token.
    """
    return ["rcalc", "-e", "$1=179*(.265*$1+.670*$2+.065*$3)"]


def build_rcollate_final_argv(n_hours: int) -> list[str]:
    """The final `rcollate` argv that reshapes `rcalc`'s one-lux-value-per-record stream into the
    published `.ill` shape -- sensors as rows, hours as columns: ``rcollate -hi -fa1 -ic n_hours``.

    **`n_hours` is a parameter, never the literal `8760`.** Sec 5's own line hardcodes `-ic 8760` and
    says so itself -- *"Assumes input is organizable into blocks of 8760 columns"*. Baking that
    literal into this function would reshape any result computed from a weather file with a different
    hour count -- a leap year's 8784 hours, or the 3-hour micro scene `rad_engine.chain.
    five_phase_steps`'s own docstring names as the case that exercises this -- into a wrong shape
    with no error from `rcollate` or `dctimestep`: the row/column split it is told to reshape into is
    exactly what it reshapes into, whether or not that split describes the file actually being read.
    Ruling R47 named this class of defect once already, for `weather`'s own hardcoded `timesteps`;
    this function exists so the identical mistake cannot recur here by construction -- the caller
    supplies the real count and nothing here substitutes a guess for it.

    **`-hi`, not `-h`.** `-h` suppresses the header on both sides. This step's stdin is `rcalc`'s
    stdout, which carries none, so only the input suppression was ever needed -- and the output
    suppression is what left the five-phase `.ill` as the one matrix in this project that does not
    declare its own shape. Measured 2026-08-20: identical data rows, `NCOMP=1 / NROWS / NCOLS /
    FORMAT=ascii` gained.

    **No `-t`.** The incoming stream is already sensor-major (`build_rcollate_leg_argv`'s `-oc 1`), so
    `-ic n_hours` alone yields one row per sensor and one column per hour -- the same layout the two-
    and three-phase chains write. The `-t` transposed it into hour-major, which is why `results` could
    not read it: `rad_engine.results.AnnualMatrix` maps `points = NROWS`, and under `-t` that field
    named the hour count.

    Returns a plain argv **leading with the bare binary name** -- `rad_engine.run.run` substitutes
    `env.path("rcollate")` for that first token.
    """
    return ["rcollate", "-hi", "-fa1", "-ic", str(int(n_hours))]


@dataclass(frozen=True)
class DcMatrixHeader:
    """The parsed `#?RADIANCE` header a Radiance matrix file (`rfluxmtx`, `genskyvec`, or
    `dctimestep` output) writes before its data rows -- the fields `parse_dc_matrix_header` reads,
    nothing more. Mirrors `rad_engine.skymatrix.SkyMatrixHeader`'s shape deliberately: both are "the
    handful of fields every Radiance matrix header carries", but this module keeps its own dataclass
    rather than importing that one, since the two headers describe different matrices (an annual sky
    matrix there, a DC matrix / sky vector / dctimestep result here) and this module does not import
    `rad_engine.skymatrix` for the same reason `rad_engine.sky` and `rad_engine.rtrace` do not import
    each other's result types -- each module's header belongs to the chain it parses.
    """

    nrows: int
    ncols: int
    ncomp: int
    format: str
    """The data encoding (``ascii``, ``float``, or ``double``), carried through as text -- this
    module does not itself read matrix data of any encoding other than the ASCII rows
    `parse_dctimestep_output` parses."""
    extra: Mapping[str, str] = field(default_factory=dict, compare=False)
    """Every other ``KEY=VALUE`` line the header carried, verbatim and unparsed.

    **Added by Phase 7d-1 Task 10 (ruling R129), because a header this server writes itself now
    carries a field Radiance has no name for.** `rad_engine.blend` declares `SCHEDULE_DIGEST=` so
    the blended matrix says which blind schedule decided it, and `rad_engine.blend
    .compare_schedule_digest` is the function that compares it against the schedule the project
    holds (ruling R191). Dropping it here would make the helper's own self-check decorative: the
    value would be written and nothing could ever read it back.

    **No production caller runs that comparison yet** -- Phase 7d-1 Task 15a wires it into
    `rad_mcp.results_tool`. Carrying the field through this type is what makes the wiring possible;
    it is not itself the check.

    **This corrects a sentence that shipped false, twice.** It first said Task 12's seal compares
    the field -- Task 12 landed and compared nothing. Task 15b then replaced that with a present
    tense of its own (*"compares that against the schedule the project holds when the blend is
    read"*), which was the same defect forward-dated.

    Real Radiance headers populate this too -- `CAPDATE=`, `GMT=`, `SOFTWARE=` -- and that is the
    point of taking whatever is there rather than an allowlist: a reader that knew only the names
    this repo writes would silently be a second definition of "a header field".

    **`compare=False`**, for two reasons that both matter. A `Mapping` is unhashable, and a frozen
    dataclass derives `__hash__` from its comparison fields, so including it would make every
    `DcMatrixHeader` unhashable -- a change to this type's protocol in return for nothing. And a
    header's *identity* is its shape: two matrices of the same shape are the same shape whatever
    provenance lines their producers stamped on them. The consequence is stated so nobody relies on
    the wrong thing: **an equality assertion is not a digest check.** A test that means to check
    `SCHEDULE_DIGEST` asserts `header.extra["SCHEDULE_DIGEST"]` directly, and
    `tests/test_blend.py::TestTheHeaderItDeclares` does.
    """


#: The header fields `DcMatrixHeader` names in its own right. Everything else a header carries goes
#: to `extra` -- **read off the dataclass rather than retyped**, so a field added to the type cannot
#: end up named *and* duplicated inside `extra`. A hand-typed set beside the class is two
#: definitions that agree until one of them moves, which is the trap `rad_core.blinds._BAND_*` is
#: built the same way to avoid.
_NAMED_HEADER_FIELDS: Final[frozenset[str]] = frozenset(
    f.name.upper() for f in fields(DcMatrixHeader) if f.name != "extra"
)
assert _NAMED_HEADER_FIELDS == {"NROWS", "NCOLS", "NCOMP", "FORMAT"}, (
    "the header fields this parser names have moved; see `extra` above"
)


def _truncate_for_message(text: str) -> str:
    """`text` if it is short, otherwise its first `_HEADER_TEXT_IN_MESSAGE_LIMIT` characters plus a
    marker saying how much was dropped.

    Returns the text **unchanged** below the limit, so every existing message over a real header is
    byte-for-byte what it was; the ellipsis only ever appears on input no header could produce.
    """
    if len(text) <= _HEADER_TEXT_IN_MESSAGE_LIMIT:
        return text
    dropped = len(text) - _HEADER_TEXT_IN_MESSAGE_LIMIT
    return f"{text[:_HEADER_TEXT_IN_MESSAGE_LIMIT]}... [{dropped} more characters not shown]"


def parse_dc_matrix_header(text: str) -> DcMatrixHeader:
    """Parse a Radiance matrix header (`rfluxmtx`, `genskyvec`, or `dctimestep`) from captured
    stdout text.

    Reads only the lines before the header's terminating blank line, matching
    `rad_engine.skymatrix.parse_gendaymtx_header`'s own boundary -- a DC matrix over a real scene
    can be large, and nothing here requires materialising the data rows just to learn the shape.

    Raises `ValueError` if `NROWS`, `NCOLS`, or `NCOMP` is absent -- a header this parser cannot read
    a full shape from is not a header a caller should trust silently as some default that might read
    as a real, if empty, matrix (the same discipline `parse_gendaymtx_header` holds).
    """
    header_block = text.split("\n\n", 1)[0]
    # Named `found`, not `fields`: `dataclasses.fields` is imported above for `_NAMED_HEADER_FIELDS`
    # and a local of that name would shadow it inside this function.
    found: dict[str, str] = {}
    for line in header_block.splitlines():
        if "=" in line:
            key, _, value = line.partition("=")
            found[key.strip()] = value.strip()

    for required in ("NROWS", "NCOLS", "NCOMP"):
        if required not in found:
            # **Truncated, and that is a correctness fix rather than a cosmetic one** -- see
            # `_HEADER_TEXT_IN_MESSAGE_LIMIT` for the 55.9-million-character message this produced
            # over a headerless file. The full length is stated so a reader can tell a real header
            # from a matrix that arrived here by mistake.
            raise ValueError(
                f"Radiance matrix header has no {required} field; cannot determine the matrix "
                f"shape. Header text was ({len(header_block)} chars): "
                f"{_truncate_for_message(header_block)!r}"
            )

    return DcMatrixHeader(
        nrows=int(found["NROWS"]),
        ncols=int(found["NCOLS"]),
        ncomp=int(found["NCOMP"]),
        format=found.get("FORMAT", ""),
        extra={k: v for k, v in found.items() if k not in _NAMED_HEADER_FIELDS},
    )


class MatrixShapeError(ValueError):
    """A DC matrix's column count does not match a sky vector's row count.

    Raised by `check_matrix_shape`, which a caller runs **before** invoking `dctimestep` at all
    (engine invariant 3, module docstring) -- not merely a restatement of the loud failure
    `dctimestep` itself already produces on this exact mismatch (`fatal - unexpected column count in
    header`, exit 1). Not a registered advisory code: this is a guard against a caller assembling
    the wrong two files together, the same class of defensive exception
    `rad_engine.rtrace.RtraceOutputError` and `rad_engine.sensors.PointsFileEmptyError` are for their
    own modules, not a client-facing refusal with a code in the register.
    """


class MatrixFileUnreadableError(RuntimeError):
    """A matrix file's bytes could not be read off disk: no such path, a directory, or an OS refusal.

    **Not** a malformed matrix -- that is the `ValueError` `parse_dc_matrix_header` /
    `parse_dctimestep_output` raise one layer down over bytes that *were* read, and it propagates
    from `read_ill`/`read_matrix_header` unchanged. The distinction is the one
    `rad_engine.weather.WeatherFileUnreadableError`'s docstring makes and is load-bearing for the
    same reason: this one is recovered by fixing the environment (a job step that never wrote its
    `stdout_path`, a stale artifact path in a manifest), that one by producing a different file.
    Collapsing them sends a caller to the wrong recovery.

    Raised in place of the stdlib `FileNotFoundError`/`IsADirectoryError`/`PermissionError` because a
    bare `OSError` is not a member of this codebase's error vocabulary, so nothing above catches it
    and it reaches the wire as an uncoded transport failure (check 0.4) -- `rad_engine.scene.
    SceneFileNotFoundError`'s own reasoning. `OSError` is caught as the base class rather than by
    enumerating subclasses, because the set is platform-dependent: opening a directory raises
    `IsADirectoryError` on POSIX and `PermissionError` on Windows, and an enumeration would leak the
    Windows case uncaught.

    **No advisory code, deliberately, and the gap is reported rather than filled.** The register's
    nearest codes are all statements about a file that *was* read -- `MATRIX_SHAPE_MISMATCH` is about
    two headers that disagree, `RESULT_TRUNCATED` about a result that exists and was cut short,
    `SCENE_FILE_NOT_FOUND` about a scene input rather than a matrix artifact. None is true of a
    matrix that never opened. Codes are allocated once and never renamed (docs/tool-surface.md
    #advisory-code-register), so reusing one whose definition is untrue of this failure would be
    worse than the gap. `rad_engine.weather.WeatherFileUnreadableError` already ships this exact
    "code is None, reported to the owner" posture for the identical filesystem refusal one module
    over, and `rad_mcp.runtime.refuse_engine_error` shapes a code-less refusal without complaint.
    """

    def __init__(self, message: str, *, path: Path) -> None:
        super().__init__(message)
        self.path = path
        """The path that could not be read, kept as data so a caller does not have to recover it by
        parsing the message -- `WeatherFileUnreadableError.path`'s own contract."""


def check_matrix_shape(dc_header: DcMatrixHeader, sky_header: DcMatrixHeader) -> None:
    """Refuse a DC matrix and sky vector whose shapes cannot be multiplied together correctly.

    `dctimestep <DC matrix> <sky vector>` computes, per output sensor, the dot product of that
    sensor's DC matrix row against the sky vector -- so the DC matrix's column count must equal the
    sky vector's row count. `dc_header.ncols == sky_header.nrows` is the one check this function
    makes; nothing else about either header is inspected, because that is the only relationship
    `dctimestep`'s own arithmetic actually depends on.

    **This is engine invariant 3's own example, checked structurally rather than left to
    `dctimestep`'s loud failure.** That failure exists and is real (module docstring: exit 1,
    `fatal - unexpected column count in header`, confirmed live against Radiance 6.1a) -- but engine
    invariant 3 is stated as "a transposed `.ill` is a valid file that yields a plausible wrong
    answer," which is a claim about a *different* failure shape: two headers whose numbers still
    satisfy some multiplication (because both were transposed relative to what the caller intended)
    would not trip `dctimestep`'s own column-count guard at all. Running this check first, as a
    condition of this module's own contract rather than as a hope that Radiance will refuse a bad
    pairing, is what catches that case too -- a caller that always calls this before `dctimestep`
    never depends on which of the two failure modes a given mismatch happens to produce.

    Raises `MatrixShapeError` naming both numbers when they disagree; returns `None` (does nothing
    else) when they agree.
    """
    if dc_header.ncols != sky_header.nrows:
        raise MatrixShapeError(
            f"DC matrix NCOLS={dc_header.ncols} does not match sky vector NROWS={sky_header.nrows}. "
            "dctimestep multiplies each DC matrix row against the sky vector, so these must agree "
            "exactly; a receiver missing a hemisphere (see rad_engine.annual.dc_receiver_text) is "
            "the most common cause -- one column/row short is the ground patch."
        )


def check_three_phase_shapes(
    v: DcMatrixHeader, t: DcMatrixHeader, d: DcMatrixHeader, s: DcMatrixHeader
) -> None:
    """Refuse a `dctimestep V T D S` chain whose four matrices cannot be multiplied together in
    that order (check 6.7 clause 2) -- the three-phase sibling of `check_matrix_shape`, for the
    four-operand chain rather than the two-matrix DC-matrix/sky-vector form.

    `dctimestep V T D S` composes as `((V @ T) @ D) @ S`, so it has three join points, each the
    same "this dimension must equal that dimension" relationship `check_matrix_shape` checks for
    its own pair, extended across four operands instead of two::

        v.ncols == t.nrows        # V's Klems columns feed T's rows
        t.ncols == d.nrows        # T's columns feed D's rows
        d.ncols == s.nrows        # D's Reinhart columns feed S's rows

    **This measurement is why the function exists.** Feeding `dctimestep` a deliberately
    mismatched T matrix (measured directly against Radiance 6.1a on this machine, a 73-bin T
    against a 145/146-bin V/D/S chain) exits 1 with `fatal - unexpected row count in header` on
    stderr -- loud, but from *inside* the job, after V and D have already been paid for. Running
    this check first, as a submit-time precondition, is what turns that into a refusal before
    either of those matrices is spent.

    **Not a fourth check on absolute bin counts, deliberately, the same discipline
    `check_matrix_shape`'s own docstring holds for the two-matrix form.** Asserting `== 145` or
    `== 146` anywhere here would hardcode the Klems/Reinhart basis into a shape check; the chain is
    correct whenever the operands *agree* with each other, and the basis itself is pinned
    elsewhere (checks 6.4 and 6.6). This function checks composability only.

    **Every message opens with a `"{producer} ({its own count} columns) x {consumer} ({its own
    count} rows): "` naming clause -- added under ruling R74, which this function's own history is
    half the evidence for.** `v.ncols == t.nrows` and `t.ncols == d.nrows` both once compared
    `target.klems_bins` to itself (fix round 1, this module's own git history) and, even fixed, both
    joins can legitimately share the same two digits (a `klems_bins` of 73 makes both "145 vs 73"),
    so `MatrixShapeError` plus a digit does not say which of the three fired -- only the message's
    opening clause, naming the operand pair, does. `check_five_phase_shapes` (this module, Task 11)
    adds a fourth join under the identical exception type and calls this function for its first
    three rather than re-deriving them, so the naming clause is what lets a caller -- or a test --
    tell all four apart regardless of which function raised.

    Raises `MatrixShapeError` naming both numbers and which of the three relationships disagreed,
    the first time one is found (V-T, then T-D, then D-S) -- a caller told only "shape mismatch"
    has to guess which of three; returns `None` (does nothing else) when all three agree.
    """
    if v.ncols != t.nrows:
        raise MatrixShapeError(
            f"V ({v.ncols} columns) x T ({t.nrows} rows): V matrix NCOLS={v.ncols} does not match "
            f"T matrix NROWS={t.nrows}. dctimestep V T D S multiplies V's Klems columns against T's "
            "rows first, so these must agree exactly; a T matrix built at the wrong Klems "
            "subdivision (see rad_engine.matrices) is the most common cause."
        )
    if t.ncols != d.nrows:
        raise MatrixShapeError(
            f"T ({t.ncols} columns) x D ({d.nrows} rows): T matrix NCOLS={t.ncols} does not match "
            f"D matrix NROWS={d.nrows}. dctimestep V T D S multiplies T's columns against D's rows, "
            "so these must agree exactly; a T matrix built at the wrong Klems subdivision (see "
            "rad_engine.matrices) is the most common cause."
        )
    if d.ncols != s.nrows:
        raise MatrixShapeError(
            f"D ({d.ncols} columns) x S ({s.nrows} rows): D matrix NCOLS={d.ncols} does not match "
            f"sky vector S NROWS={s.nrows}. dctimestep V T D S multiplies D's Reinhart columns "
            "against S's rows last, so these must agree exactly; a receiver missing a hemisphere "
            "(see rad_engine.annual.dc_receiver_text) is the most common cause -- one column/row "
            "short is the ground patch."
        )


def check_five_phase_shapes(
    v: DcMatrixHeader, t: DcMatrixHeader, d: DcMatrixHeader, s: DcMatrixHeader,
    cds: DcMatrixHeader, ssun: DcMatrixHeader,
) -> None:
    """Refuse a 5-phase chain whose operands cannot be multiplied together in the shapes
    `I_5ph = V*T*D*S - Vd*T*Dd*Sds + Cds*Ssun` requires -- check 7.3, the five-phase sibling of
    `check_three_phase_shapes`.

    **Four joins, not three, and the first three are *delegated*, not re-derived.** `V*T*D*S`'s own
    V-T, T-D, D-S relationships are `check_three_phase_shapes`' own three checks verbatim -- this
    function calls it rather than reimplementing them, so there is one arithmetic path for those
    three joins, not two that could silently drift apart (ruling R30). That delegation is not merely
    tidy: `check_three_phase_shapes` and this function used to carry two independently-worded
    messages for the identical V-T/T-D/D-S mismatches (one with the R74 naming clause, one without),
    which is exactly the "two copies, one of them stale" risk R30 exists to rule out; see that
    function's own docstring for the naming clause both now share. `Vd`/`Dd`/`Sds` need no separate
    joins here: `docs/superpowers/specs/2026-08-17-phase-7-five-phase-design.md`'s own table states
    they are built at the identical Klems/Reinhart basis as `V`/`D`/`S`, so a caller that has already
    proven `V*T*D*S` composable has proven `Vd*T*Dd*Sds` composable too, and a fifth/sixth/seventh
    join checking the same numbers under new names would not catch anything the delegated three
    already catch.

    The fourth join is new and is this function's own: `Cds`'s Reinhart sun-source columns against
    `Ssun`'s rows -- the `Cds*Ssun` leg, checked only after the delegated three have passed.

    ## Ruling R74, and where this function's own risk actually lives

    **Every join compares two *different* parameters, by construction, because this function takes
    six independent arguments and derives no operand from another.** `v`, `t`, `d`, `s`, `cds` and
    `ssun` have no relationship to each other except what a caller supplies, so `cds.ncols !=
    ssun.nrows` genuinely compares two independent facts *as far as this function itself is
    concerned*. That is necessary but **not sufficient** for the check to be able to fire in
    practice -- see `rad_engine.chain.five_phase_shape_check`'s own docstring for a case where it
    is not: predicting both `cds` and `ssun` from the same `mf` through two functions that are proven
    to agree at every reachable value reintroduces R74's defect at the *caller*, in a form this
    function's own six-parameter signature cannot see or prevent. This function is correct; it does
    not by itself guarantee the check it expresses can ever go red for a given caller.

    **Every message names its own join**, via the naming clause `check_three_phase_shapes` now also
    carries -- see that function's own docstring for why (`MatrixShapeError` is the one exception
    type every branch across both functions raises, and the digits can repeat across joins, so the
    opening clause is the only thing that tells a caller, or a test, which of the four fired).

    ## Check 7.3's own substitution

    **`cds.ncols != ssun.nrows` is check 7.3, written as the specific substitution it exists to
    prevent rather than as a generic mismatch it happens to catch** (`docs/superpowers/specs/
    2026-08-17-phase-7-five-phase-design.md`, "Two numbers that legitimately differ: 5185 and
    5186"). `five-phase-method.md` 4.3 and `workflows-point-in-time-to-annual.md` 3.2-3.3 both write
    a literal `-bn 5185` -- one short, because `5185 = 1 + 144*mf^2` is the *sun-source* count
    (`rad_engine.suns.sun_count`), not `Nrbins`, the ground-patch-inclusive Reinhart *bin* count
    `-bn` actually needs to bin into (`rad_engine.matrices.reinhart_bins`, the same "one short" gap
    that module's own docstring records for `-bn 145` at MF:1).

    **This function is not, today, what stops that substitution from reaching a running job** -- see
    `rad_engine.chain.five_phase_shape_check`'s own docstring for the honest account of what does
    (`rad_engine.matrices.build_cds_argv` passing the bare symbol `-bn Nrbins`, pinned by
    `tests/test_matrices.py::TestBuildCdsArgv::
    test_cds_argv_carries_the_direct_calculation_flags_and_the_symbol_bn`) and why this function's
    own fourth join is not wired to run at submit time yet. This join is correct and becomes
    load-bearing the moment a real `Cds` or `Ssun` header is available to compare against -- a
    reused artifact read back off disk, or a post-build verification -- neither of which exists
    before Task 12 gives `Cds`/`Ssun` their own artifact kinds.

    **Deliberately not a fifth check on absolute bin counts**, the same discipline
    `check_three_phase_shapes` holds for `== 145`/`== 146`: this function accepts any `cds`/`ssun`
    pair whose columns and rows agree, whatever basis produced them. `5185` and `5186` are the
    numbers a caller sees when the substitution actually happens, not a constant this function pins.

    Raises `MatrixShapeError` naming both numbers and which of the four relationships disagreed, the
    first time one is found (V-T, then T-D, then D-S -- all three via `check_three_phase_shapes` --
    then Cds-Ssun); returns `None` when all four agree.
    """
    check_three_phase_shapes(v, t, d, s)
    if cds.ncols != ssun.nrows:
        raise MatrixShapeError(
            f"Cds ({cds.ncols} columns) x Ssun ({ssun.nrows} rows): Cds matrix NCOLS={cds.ncols} "
            f"does not match sun matrix Ssun NROWS={ssun.nrows}. The Cds*Ssun leg of "
            "I_5ph = V*T*D*S - Vd*T*Dd*Sds + Cds*Ssun multiplies Cds's Reinhart sun-source columns "
            "against Ssun's rows, so these must agree exactly; a Cds matrix built with a literal "
            "-bn 5185 in place of the symbol -bn Nrbins (see rad_engine.matrices.build_cds_argv) is "
            "the most common cause -- 5185 counts sun sources, 5186 is Nrbins, the "
            "ground-patch-inclusive bin count both Cds and Ssun actually bin into at MF:6."
        )


def parse_dctimestep_output(text: str) -> list[tuple[float, float, float]]:
    """`dctimestep` stdout -> one `(R, G, B)` irradiance triple per sensor, in grid order.

    **The trap this function exists to make impossible.** `dctimestep` emits exactly one RGB triple
    per output row (`NCOMP=3`), not one scalar per sensor -- mis-parsed on the hand-run that de-risked
    this module before any code existed here, producing "243 000 lux out of header digits" when
    header text was accidentally swept into the numeric stream. This function reads only the data
    rows after the header's terminating blank line and returns RGB tuples, leaving the RGB -> lux
    conversion to whatever caller has `rad_core.photometry.to_lux` in scope (module docstring) --
    the same split `rad_engine.rtrace.parse_irradiance` holds for `rtrace -I`'s identical shape.

    Raises `ValueError` if a data line does not have exactly three whitespace-separated numeric
    tokens, or if `NROWS`/`NCOLS`/`NCOMP` claim a shape parsing the data would contradict -- both
    read as a wrong-but-plausible-looking result otherwise, exactly the class of failure
    `rad_engine.rtrace.parse_irradiance` refuses to let through for its own text.
    """
    header = parse_dc_matrix_header(text)
    if header.ncomp != 3:
        raise ValueError(
            f"dctimestep output declares NCOMP={header.ncomp}, not 3 -- this parser only reads "
            "RGB triples (R, G, B irradiance per sensor)."
        )
    data_block = text.split("\n\n", 1)[1] if "\n\n" in text else ""
    lines = [ln for ln in data_block.splitlines() if ln.strip()]
    if len(lines) != header.nrows:
        raise ValueError(
            f"dctimestep output header declares NROWS={header.nrows}, but {len(lines)} data "
            "line(s) were found. The output may be truncated or the header may not match the data."
        )
    triples: list[tuple[float, float, float]] = []
    for line in lines:
        tokens = line.split()
        if len(tokens) != 3:
            raise ValueError(
                f"dctimestep data line has {len(tokens)} numeric token(s), not 3: {line!r}. "
                "Expected one (R, G, B) irradiance triple per line."
            )
        try:
            r, g, b = (float(tok) for tok in tokens)
        except ValueError as exc:
            raise ValueError(
                f"dctimestep data line contained a non-numeric token: {line!r} ({exc})"
            ) from exc
        triples.append((r, g, b))
    return triples


def read_ill(path: Path) -> list[tuple[float, float, float]]:
    """Read a `.ill` matrix file off disk and parse it via `parse_dctimestep_output`.

    **The file-side entry point `parse_dctimestep_output` does not have, and why it is needed.**
    That function takes *text*, and until now the only text was `Result.stdout`. The moment
    `dctimestep` runs as a `rad_engine.jobs.Step` with `stdout_path` set, that text does not exist:
    `run_to_file` sends stdout straight to the file handle and returns `Result.stdout == ""` by
    construction (`run_to_file`'s docstring -- "``Result.stdout`` is empty; the payload is the
    file"), which is the correct design for a matrix payload and precisely why the matrix must be
    re-read from disk afterwards. This function is that read and nothing more.

    It does **not** re-implement the parse. Every rule about what a `.ill`'s rows mean -- one RGB
    triple per sensor and never one scalar, `NCOMP` must be 3, the row count must match `NROWS` --
    lives in `parse_dctimestep_output` and is reached through it, so there is one parser by
    construction rather than by discipline (ruling R30, "a second copy is a second thing to drift").
    It also does not convert RGB to lux: that is `rad_core.photometry.to_lux`'s job, called by a
    caller one layer up, exactly as it is for `parse_dctimestep_output`'s own return value (module
    docstring).

    **The decode cannot raise (ruling R38).** Bytes in, `MATRIX_DECODE_ERRORS` out -- see that
    constant for why a strict decode would refuse a valid annual result over a byte in the header's
    command-line echo. The *parse* still refuses loudly: a surrogate inside a numeric token is not a
    number, and `parse_dctimestep_output` says so rather than coercing it. R38 buys an unskippable
    read, not a permissive parse.

    Raises `MatrixFileUnreadableError` -- never a bare `OSError` -- when the path does not exist, is
    a directory, or the OS otherwise refuses the read; and `ValueError` (from the parser, unchanged)
    when the bytes were read but are not a well-formed RGB matrix. Accepts `Path | str` for
    `read_epw`'s stated reason: a caller one layer up is holding a manifest-derived string.
    """
    path = Path(path)
    try:
        # `open_regular_file`, not `read_bytes`: R48's rule is *"in either direction"*, and a FIFO
        # at a matrix path blocks the reader forever rather than returning short. Adopted
        # 2026-08-10. `NotARegularFileError` is an `OSError`, so it is already carried by the
        # clause below and reaches callers as `MatrixFileUnreadableError` with the path named --
        # no handler anywhere had to change for this.
        with open_regular_file(path, "rb") as handle:
            raw = handle.read()
    except OSError as exc:
        # Base-class catch: see `MatrixFileUnreadableError`'s docstring on why enumerating
        # `FileNotFoundError`/`IsADirectoryError` would leak the Windows `PermissionError` case.
        logger.info("read_ill refused %s: %s", path, exc)
        raise MatrixFileUnreadableError(
            f"the matrix file at {path} could not be read: {exc}. Nothing about its content has "
            "been judged -- the filesystem refused the read, so check the path exists, is a file "
            "rather than a directory, and is readable by this server. A job step that exited "
            "before writing its stdout_path leaves exactly this.",
            path=path,
        ) from exc
    return parse_dctimestep_output(raw.decode("utf-8", errors=MATRIX_DECODE_ERRORS))


def read_matrix_header(path: Path) -> DcMatrixHeader:
    """Read **only** the header of a matrix file off disk, without loading its data rows.

    **The primitive for shape-checking a large matrix cheaply.** `check_matrix_shape` needs three
    integers -- `NROWS`, `NCOLS`, `NCOMP` -- and nothing else, but the only way to get them from a
    file was `read_ill`, which goes through `parse_dctimestep_output` and materialises every row
    into a list of tuples. An annual `.ill` is n_points x 8760: paying for the whole matrix in memory
    to learn its shape is the wrong trade for a check whose entire purpose is to run *before* the
    expensive thing. This function reads in `_HEADER_CHUNK_BYTES` chunks and stops at the header's
    terminating blank line.

    Delegates the actual field parsing to `parse_dc_matrix_header`, so the rules about which fields
    are required and what a missing one means are stated once (ruling R30 again). What this adds is
    only *where the text came from* and *how little of it was read*.

    **It does not validate the body, and that is the point rather than an omission.** A header
    claiming `NROWS=100000` over three actual rows parses cleanly here and reports 100000 --
    `parse_dctimestep_output` would refuse the same file, correctly, because it is answering a
    different question. Reading the shape and reading the data are separate, and a caller that wants
    both wants `read_ill`.

    A header that ends at EOF with no blank line is accepted if its fields are complete: the shape is
    knowable, and a matrix whose data rows are missing is a body problem this function has no opinion
    on. An incomplete header raises `ValueError` from `parse_dc_matrix_header` exactly as parsing the
    same text would.

    **The scan stops at `_MAX_HEADER_BYTES` and the join is outside the loop.** A file with no blank
    line anywhere had neither bound: the loop re-joined every chunk read so far on every iteration,
    so it consumed the whole file at O(n^2). The EOF case above is unchanged -- a file shorter than
    the ceiling is still read to its end and accepted if its fields are complete -- so the bound only
    changes what happens to files this function could never have finished reading usefully.

    Raises `MatrixFileUnreadableError` -- never a bare `OSError` -- on a filesystem refusal, and
    decodes with `MATRIX_DECODE_ERRORS` so the decode cannot raise (ruling R38).
    """
    path = Path(path)
    chunks: list[bytes] = []
    scanned = 0
    try:
        # `open_regular_file` for `read_ill`'s stated reason, and more sharply here: this loop
        # reads until it sees a `\n\n` terminator, so a FIFO or a character device feeds it
        # forever rather than merely blocking once.
        with open_regular_file(path, "rb") as handle:
            # One byte of overlap is all a two-byte terminator straddling a chunk boundary needs.
            # The join this replaces was inside the loop and was the quadratic half of the defect.
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
                    raise ValueError(
                        f"the matrix file at {path} has no header: the first {scanned} bytes "
                        "contain no blank line terminating a `#?RADIANCE` block, so there is no "
                        "NROWS/NCOLS/NCOMP to read a shape from. The scan stopped there rather "
                        "than reading the rest of the file."
                    )
                carry = chunk[-1:]
    except OSError as exc:
        # Base-class catch, for `read_ill`'s stated reason.
        logger.info("read_matrix_header refused %s: %s", path, exc)
        raise MatrixFileUnreadableError(
            f"the matrix file at {path} could not be read: {exc}. Nothing about its content has "
            "been judged -- the filesystem refused the read, so check the path exists, is a file "
            "rather than a directory, and is readable by this server.",
            path=path,
        ) from exc
    header_bytes = b"".join(chunks).split(b"\n\n", 1)[0]
    return parse_dc_matrix_header(header_bytes.decode("utf-8", errors=MATRIX_DECODE_ERRORS))


def run_rfluxmtx(
    env: RadianceEnv,
    spec: DcMatrixSpec,
    *,
    points_text: str,
    cwd: Path | str,
    timeout: float | None = None,
) -> Result:
    """Spawn `rfluxmtx` over `points_text` (a `.pts` file's own text, `rad_engine.sensors`'s
    contract) and return the raw `Result`.

    The caller is responsible for writing `dc_receiver_text(spec.sky_subdivision)` to
    `dc_receiver_filename(spec.sky_subdivision)` inside `cwd` before calling this -- this function
    only spawns the process and does not create files, matching `rad_engine.rtrace.run_rtrace`'s own
    division of labour (it does not write the `.pts` file either). `check` is left at `run`'s own
    default (`True`): an `rfluxmtx` that exits non-zero produced no usable DC matrix, and
    `RadianceError` already carries the stderr that explains why.

    Returns the raw `Result` rather than a parsed header, so a caller that also wants
    `result.warnings` for `rad_engine.advisories.classify` does not have to re-invoke `rfluxmtx` to
    get it -- the same reasoning `run_rtrace`'s own docstring gives.
    """
    # R754: `rfluxmtx` compiles `spec.scene_files` with `oconv`, which runs every `!` line through
    # `/bin/sh`. No caller in `src/` reaches this function (`rad_engine.chain.two_phase_steps`
    # queues the same argv as a job step); the scan is here so a future caller cannot forget it
    # (`tests/test_scene_guard_ratchet.py`).
    # The names as well as the contents: rfluxmtx builds `rcontrib ... "!oconv -f <file> ..."`,
    # single-quoting a name only when it holds some characters, so `>`, `#` and a leading `~`
    # reach the shell bare and a `'` closes the quote it adds (measured, R754 fix round 1 and the
    # final review).
    refuse_shell_unsafe_paths(spec.scene_files, field="project", program="rfluxmtx")
    root = Path(cwd)
    refuse_inline_commands_in_files(list(spec.scene_files), cwd=root, raypath=env.raypath_dirs(root))
    return run(
        env, build_rfluxmtx_argv(spec), cwd=cwd, stdin=points_text, timeout=timeout
    )


def run_genskyvec(
    env: RadianceEnv,
    sky_subdivision: int,
    *,
    sky_text: str,
    cwd: Path | str,
    timeout: float | None = None,
) -> Result:
    """Spawn `genskyvec -m sky_subdivision` over `sky_text` and return the raw `Result`.

    **`sky_text` must be a completed sky description -- `rad_engine.sky.sky_description_text`'s
    output, not bare `gensky`/`gendaylit` stdout -- and this docstring asserted the opposite until
    the R95 sky fix (Phase 7a, not 7b).** The old wording claimed `genskyvec` *"reads the `skyfunc` brightness
    description directly and does not need the glow/source pair"*. Measured on Radiance 6.1a, the two
    inputs produce **different vectors at every one of 2306 patches**, and the ground patch differs
    by **5x**: `[28.70, 30.02, 33.43]` from bare `gensky` against `[5.98, 5.98, 5.98]` from the
    completed description, whose value is exactly `0.2 x` the first -- the project's ground
    reflectance. With no `groundglow` primitive to read, `genskyvec` models a **white,
    sky-coloured** ground; with one, it uses the reflectance that primitive declares. Confirmed by
    sweeping the reflectance `0.2 -> 0.8`: exactly one row moves, row 0, by exactly 4.000.

    *(Appended 2026-10-03, Phase 18 Branch B, ruling R761: the completed description's ground glow is
    now white, so the ground patch it gives is the generator's own ground brightness -- measured 29.90
    grey against 5.98 under the old `0.2` glow (plan M-B1b) -- and the bare form differs from it only
    in colour (28.70 / 30.02 / 33.43, `genskyvec`'s default sky colour applied to the ground). The
    completed form is still the one to pass: it is what the `rtrace` side of any comparison
    compiles.)*

    The man page's own example pipes bare `gensky`, so that usage is not wrong in general -- it is
    wrong whenever the scene being compared against models a ground of any reflectance other than
    1.0, which is every scene this server builds. A caller passing bare `gensky` output gets a
    plausible vector describing a ground nobody modelled.

    `check` is left at `run`'s own default (`True`), matching every other spawn in this module.
    """
    return run(
        env, build_genskyvec_argv(sky_subdivision), cwd=cwd, stdin=sky_text, timeout=timeout
    )


def run_dctimestep(
    env: RadianceEnv,
    *,
    dc_matrix_path: Path | str,
    sky_vector_path: Path | str,
    cwd: Path | str,
    timeout: float | None = None,
) -> Result:
    """Spawn ``dctimestep dc_matrix_path sky_vector_path`` and return the raw `Result`.

    **Callers should run `check_matrix_shape` on the two files' parsed headers before calling this**
    (engine invariant 3, module docstring) -- this function does not do that check itself, because it
    has no header to compare against until the files already exist on disk, and re-reading both
    files a second time here merely to re-derive what the caller already parsed would be the second
    arithmetic path ruling R30 warns against, applied to a shape check instead of a physical
    constant. `check` is left at `run`'s own default (`True`): the mismatch case this module
    otherwise guards against is not the only way `dctimestep` can fail, and every other failure is
    exactly as informative reported as a plain `RadianceError` as any other Radiance crash in this
    codebase.

    Takes file paths, not text, unlike this module's other two spawn helpers -- `dctimestep` takes
    two *positional file arguments*, not stdin, so there is no text this function could pass through
    `run`'s `stdin` parameter even if a caller had it in memory rather than on disk. That claim is
    measured, not assumed: see `build_dctimestep_argv`, which owns the argv this function spawns and
    records the stdin-closed run it was verified with.

    The argv is built by `build_dctimestep_argv` rather than listed here, so this module has one
    definition of what a `dctimestep` command line is and not two (ruling R30, "a second copy is a
    second thing to drift") -- and so a caller assembling the same command as a `rad_engine.jobs.
    Step` gets the identical tokens without spawning anything.
    """
    return run(
        env,
        build_dctimestep_argv(Path(dc_matrix_path), Path(sky_vector_path)),
        cwd=cwd,
        timeout=timeout,
    )
