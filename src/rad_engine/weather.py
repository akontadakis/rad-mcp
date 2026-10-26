"""The disk half of EPW/`.wea` handling: read a weather file off disk, write a `.wea` to disk.

**This module is deliberately thin, and that is its whole design.** `rad_core.weather` owns every
EPW field index, every missing-value sentinel, the east/west longitude sign flip, the
time-zone-to-meridian derivation, the IWEC albedo rule, and the `.wea` text itself -- all of it
pure, all of it testable on a machine with no Radiance installed (CONTEXT.md, Layers; check 1.4,
`tests/test_purity.py`). What that layer *cannot* do is open a file, which is the one thing this
module does. `read_epw` reads bytes and hands text to `rad_core.weather.parse_epw`;
`read_epw_with_digest` does the same and returns the digest of the very bytes it parsed, so a
caller checking a recorded `FileRef.digest` before trusting the content checks the bytes it then
uses rather than a second, later read of the path (the swap window ruling R97's own drift check
otherwise leaves open); `write_wea` takes `rad_core.weather.to_wea`'s text and puts it on disk.
Nothing else -- if a future edit finds itself splitting a comma here, the field belongs one layer
down.

**No `epw2wea` subprocess, and this is a decision rather than an omission.** docs/tool-surface.md
§8's Layers line says "`rad_engine.weather` runs `epw2wea` and `gendaymtx`". The `gendaymtx` half is
`rad_engine.skymatrix`'s; the `epw2wea` half is answered by `rad_core.weather`'s own module
docstring, which records why the `.wea` is generated as text instead: the conversion is pure
arithmetic over parsed fields, and a pure implementation runs in CI where a DAYSIM binary does not.
That text is verified byte-for-byte against real `epw2wea` output for the committed Athens fixture
(`tests/test_weather.py::TestToWeaDataLines::
test_every_body_line_matches_the_real_epw2wea_output_exactly`, all 8760 rows), and
`tests/test_engine_weather.py::TestRoundTripFromDisk` re-proves the same equality end to end
through *this* module's read-and-write path. So there is no subprocess here at all, and
`tests/test_engine_weather.py::TestTheModuleIsThin` asserts there never quietly becomes one.

**Ruling R38: the decode must not be able to raise.** `read_epw` reads **bytes** and decodes with
``errors="surrogateescape"``, exactly as `rad_engine.octree._decode_scene_bytes` does and for a
related reason. In the scene path a strict UTF-8 decode was an RCE (a file that failed to decode was
skipped *unscanned* while `oconv` executed its `!` lines anyway) and, in `read_scene`, an uncoded
`UnicodeDecodeError` on the wire -- the transport failure check 0.4 forbids. An EPW is user-supplied
data in the same sense, and the failure mode here is the second one: real TMY/IWEC files carry
non-UTF-8 bytes in their free-text header records (`COMMENTS 1`, a source or city name in Latin-1
is the ordinary case), and a strict decode would refuse an entire year of valid numeric data over a
byte in a record this project never reads. Surrogate-escaping cannot raise, is byte-preserving, and
leaves every ASCII byte -- which is every byte of every field this module's caller actually reads,
the commas included -- as itself.

**The two failure kinds are kept distinguishable by type, on purpose.** `WeatherFileUnreadableError`
means *the filesystem would not give us the bytes* (missing path, a directory, a permission the
server does not hold). `rad_core.weather.EPWParseError` means *the bytes are not an EPW* and
propagates from `parse_epw` untouched. They are not interchangeable: the first is recovered by
fixing the environment, the second by supplying a different file, and collapsing them would send a
client to the wrong recovery -- the same reasoning that keeps `PROJECT_NOT_EMPTY` and
`PROJECT_TREE_UNRECOGNISED` apart (`rad_engine.project`).

**Advisory-code gap, reported rather than filled (docs/tool-surface.md#advisory-code-register).**
The register allocates four weather codes -- `EPW_ALBEDO_ZERO` (warn), `EPW_MISSING_DATA` (warn),
`EPW_SITE_MISMATCH` (warn), `WEA_LONGITUDE_SIGN` (info) -- and every one of them is a statement
about a file that *was* read. None is true of a file that never opened. Codes are allocated once and
never renamed, so `WeatherFileUnreadableError.code` is `None` and the gap is reported to the owner,
rather than reusing a code whose own definition is untrue of the failure. `ARGUMENT_OUT_OF_RANGE`
was considered and rejected on `rad_engine.scene.SceneFileNotFoundError`'s reasoning: the argument
was well-formed, and what failed was the filesystem, not the call. `src/rad_mcp/project_tool.py`
(the `OSError` branch of `project`) already ships this exact "no advisory, deliberately" posture for
a filesystem refusal, so `rad_mcp` has a working precedent for shaping a code-less refusal.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Literal

from rad_core.weather import EPWFile, parse_epw, to_wea

from .project import bytes_digest
from .safe_io import NotARegularFileError, open_regular_file

__all__ = [
    "EPW_DECODE_ERRORS",
    "MAX_EPW_BYTES",
    "WEA_ENCODING",
    "WeatherError",
    "WeatherFileUnreadableError",
    "read_epw",
    "read_epw_with_digest",
    "write_wea",
]

logger = logging.getLogger(__name__)

#: How EPW bytes are decoded. **Not** a strict decode: ruling R38 (see the module docstring) and
#: `rad_engine.octree._decode_scene_bytes`, whose reasoning this mirrors -- the property being bought
#: is that this decode *cannot raise*, so no valid weather file is ever refused over a byte in a
#: header record nobody reads, and no `UnicodeDecodeError` can escape as an uncoded transport
#: failure. Byte-preserving and ASCII-transparent, which is every character an EPW's numeric fields
#: and comma separators are made of.
EPW_DECODE_ERRORS: Literal["surrogateescape"] = "surrogateescape"

#: `.wea` is written UTF-8. Radiance reads it as bytes and every character `rad_core.weather.to_wea`
#: emits is ASCII except the `place` token, which is derived from the EPW's own city/country fields
#: and may therefore carry a surrogate from `EPW_DECODE_ERRORS` above. `errors="surrogateescape"` on
#: the *encode* side turns such a surrogate back into the original byte rather than raising
#: `UnicodeEncodeError` mid-write -- which would leave a truncated `.wea` on disk that later reads as
#: a valid but short weather file. Round-tripping the byte keeps the write total and hands Radiance
#: the same `place` bytes the EPW carried.
WEA_ENCODING = "utf-8"

#: Ceiling on an EPW's size on disk, measured on the open descriptor before the bytes are read. A
#: complete hourly year is about 1.5 MB, and a leap year at 10-minute resolution -- the largest thing
#: `rad_core.weather.MAX_EPW_RECORDS` will parse -- is about 9 MB at real row widths, so 16 MB
#: admits every real file and refuses what was never an EPW.
#:
#: **It was 64 MB until 2026-08-08.** The two caps are a pair and were never checked against each
#: other: at ~66 bytes for a minimal-width row, 64 MB admits far more rows than the record cap was
#: meant to allow, so the byte cap could not bound the memory and the record cap was doing all the
#: work alone. Both were re-derived from one measurement (~1 370 bytes of peak RSS per record) so
#: that neither can be satisfied while the other is defeated. See `MAX_EPW_RECORDS` for the
#: arithmetic.
#:
#: The read is not incremental -- the bytes come in whole, the decode copies them, and `parse_epw`
#: then builds one `EPWHourlyRecord` per data row -- and MCP runs this synchronous tool on `anyio`'s
#: 40-slot thread pool, so an unbounded read exhausts memory for the **whole server** rather than
#: for one request.
MAX_EPW_BYTES: int = 16 * 1_048_576


class WeatherError(RuntimeError):
    """Something this module refuses to do with a weather file on disk.

    Mirrors `rad_engine.project.ProjectError` and `rad_engine.sky.SkyError` (docs/tool-surface.md
    #refusals): on the wire a refusal is `{"refused": true, "reason": ..., "advisories": [...]}` and
    never an exception, but that shape is `rad_mcp`'s -- building it here would put the wire contract
    in the engine. So this carries the allocated `code` (and `severity`, and `field` where the
    refusal is about one client argument) and `rad_mcp.runtime.refuse_engine_error` shapes the
    envelope.

    `code` is `None` where the register has none for the condition. Read `severity` from the class
    rather than hardcoding it at a call site: a code allocated at `warn` would otherwise be emitted
    as an `error` by a caller that never asked (`ProjectError.severity`'s own docstring).
    """

    code: str | None = None
    severity: Literal["info", "warn", "error"] = "error"
    field: str | None = None

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        if code is not None:
            self.code = code


class WeatherFileUnreadableError(WeatherError):
    """The EPW file's bytes could not be read: no such path, a directory, or an OS refusal.

    **Not** a malformed EPW -- that is `rad_core.weather.EPWParseError`, raised one layer down over
    bytes this module successfully read, and propagated from `read_epw` unchanged. The distinction is
    load-bearing (module docstring): this one is fixed by fixing the environment, that one by
    supplying a different file.

    Raised in place of the stdlib `FileNotFoundError`/`IsADirectoryError`/`PermissionError` for the
    reason `rad_engine.scene.SceneFileNotFoundError` was: a bare `OSError` is not a member of this
    codebase's error vocabulary, so nothing above catches it and it reaches the wire as an uncoded
    transport failure (check 0.4). `OSError` is caught as the base class rather than by enumerating
    subclasses, because the set is platform-dependent -- `Path.read_bytes()` on a directory raises
    `IsADirectoryError` on POSIX and `PermissionError` on Windows, and an enumeration would leak the
    Windows case uncaught.

    **`code` is `None`, deliberately -- see the module docstring's register section.** The four
    allocated weather codes are all statements about a file that was read; none is true of one that
    never opened, and codes are allocated once and never renamed, so the gap is reported to the owner
    rather than filled by reusing a code whose definition is untrue of this failure.
    `field = "epw"` still names which client argument the refusal is about, which is what makes a
    code-less refusal actionable at the client -- `refuse_engine_error` threads `field` through
    independently of `code`.
    """

    code = None
    field = "epw"

    def __init__(self, message: str, *, path: Path) -> None:
        super().__init__(message)
        self.path = path
        """The path that could not be read, kept as data so a caller does not have to recover it by
        parsing the message."""


def read_epw(path: Path | str) -> EPWFile:
    """Read an EPW file off disk and parse it via `rad_core.weather.parse_epw`.

    The only work done here is the read and the decode (`EPW_DECODE_ERRORS` -- ruling R38, a decode
    that cannot raise). Every field index, sentinel and derivation belongs to the pure layer and is
    not duplicated here; `parse_epw`'s own `EPWParseError` propagates unchanged, because a file whose
    *content* is not an EPW is that layer's judgement to make, not this one's.

    Raises `WeatherFileUnreadableError` -- never a bare `OSError` -- when the path does not exist, is
    a directory, or the OS otherwise refuses the read. Accepts `Path | str` for the same reason
    `rad_engine.materials.write_materials` and `rad_engine.octree.build_octree` do: a caller one
    layer up is holding a manifest-derived string.

    A caller that must *also* check the file's bytes against a recorded digest wants
    `read_epw_with_digest` below, not this function plus a separate `file_digest` -- see that
    function's docstring for the swap window two opens leave.
    """
    return parse_epw(_read_epw_bytes(Path(path)).decode("utf-8", errors=EPW_DECODE_ERRORS))


def read_epw_with_digest(path: Path | str) -> tuple[str, EPWFile]:
    """`(digest, parsed)` from **one** read: the `sha256:` digest of the bytes this call read, and
    the `EPWFile` parsed from those same bytes.

    **The bytes that satisfy a drift check must be the bytes that are parsed.** `file_digest(path)`
    opens, hashes and closes; `read_epw(path)` then opens the path again. A local writer inside an
    allowed root who swaps the file between the two opens passes the gate and has another site's
    weather parsed -- which is precisely the silent substitution ruling R97's drift check exists to
    catch, so a check-then-open implementation of it catches nothing an attacker with write access
    to the root cannot step around. `read_epw`'s own body already records fixing the sibling
    check-then-open (`stat` then `read_bytes`); this closes the other one. Found by the Phase 7b
    whole-branch review, security lens.

    The digest string is byte-identical in format to `rad_engine.project.file_digest`'s, so it
    compares directly against a recorded `FileRef.digest`. The read is bounded exactly as
    `read_epw`'s is (`MAX_EPW_BYTES`, measured on the descriptor), and every refusal is the same
    `WeatherFileUnreadableError`.
    """
    raw = _read_epw_bytes(Path(path))
    return bytes_digest(raw), parse_epw(raw.decode("utf-8", errors=EPW_DECODE_ERRORS))


def _read_epw_bytes(path: Path) -> bytes:
    """The bounded, `open_regular_file`-guarded read both `read_epw` and `read_epw_with_digest`
    consume. Factored out so there is exactly one place that decides how an EPW is opened, sized
    and bounded -- two copies of this loop is how the digest and the parse came to read the file
    twice in the first place."""
    try:
        # **Opened first, then interrogated through the descriptor.** This was `path.stat()` followed
        # by `path.read_bytes()` -- check-then-open, which is a TOCTOU: a regular file that passes
        # the `stat` can be swapped for a FIFO before the read, and `read_bytes()` on a writer-less
        # FIFO then blocks **forever**. Reproduced by replaying that exact sequence. `contain()`
        # accepts a FIFO inside an allowed root, so nothing else stopped it, and MCP dispatches this
        # synchronous tool on `anyio`'s thread pool (default limiter 40) -- so forty such paths stall
        # every tool call the server has, not merely weather ones. `open_regular_file` closes the
        # type window (`S_ISREG` on the fd), the blocking-open window (`O_NONBLOCK`, cleared) and
        # the final-component symlink question in one call.
        with open_regular_file(path, "rb") as handle:
            size = os.fstat(handle.fileno()).st_size
            if size > MAX_EPW_BYTES:
                # A full year of hourly data is ~1.5 MB, so this ceiling refuses only files that
                # were never weather. Bounded *before* the read, because the read is what commits
                # the memory: the decode copies it and `parse_epw` then builds one model per data
                # row, which measured 21-25x the input size in peak RSS. Measured on the descriptor
                # rather than the path, so the file that is sized is the file that is read.
                raise WeatherFileUnreadableError(
                    f"the weather file at {path} is {size / 1_048_576:.1f} MB, over the "
                    f"{MAX_EPW_BYTES // 1_048_576} MB limit for an EPW. A complete hourly year is "
                    "about 1.5 MB, so a file this large is not weather data. Nothing about its "
                    "content has been judged.",
                    path=path,
                )
            # Read one byte past the cap rather than to EOF: a file that grew between the `fstat`
            # and the read cannot exceed the bound by more than that byte, which the check below
            # then refuses. `read_bytes()` would have committed the whole of it.
            raw = handle.read(MAX_EPW_BYTES + 1)
        if len(raw) > MAX_EPW_BYTES:
            raise WeatherFileUnreadableError(
                f"the weather file at {path} exceeds the {MAX_EPW_BYTES // 1_048_576} MB limit for "
                "an EPW; it grew while it was being read. Nothing about its content has been "
                "judged.",
                path=path,
            )
    except NotARegularFileError as exc:
        # Raised by `open_regular_file` for a FIFO, a device, or a directory. Re-shaped into this
        # module's own class so the tool layer keeps one refusal type to map, with the message
        # naming what an EPW must be rather than what a "declared stream" is.
        logger.info("read_epw refused %s: %s", path, exc)
        raise WeatherFileUnreadableError(
            f"the weather file at {path} is not a regular file. An EPW must be a file on disk: "
            "a FIFO or a device would block or stream without end rather than being read. "
            "Nothing about its content has been judged.",
            path=path,
        ) from exc
    except OSError as exc:
        # Base-class catch: see `WeatherFileUnreadableError`'s docstring on why enumerating
        # `FileNotFoundError`/`IsADirectoryError` would leak the Windows `PermissionError` case.
        logger.info("read_epw refused %s: %s", path, exc)
        raise WeatherFileUnreadableError(
            f"the weather file at {path} could not be read: {exc}. Nothing about its content has "
            "been judged -- the filesystem refused the read, so check the path exists, is a file "
            "rather than a directory, and is readable by this server.",
            path=path,
        ) from exc
    return raw


def write_wea(epw: EPWFile, path: Path | str) -> Path:
    """Render `epw` as `.wea` text (`rad_core.weather.to_wea`) and write it to `path`.

    Returns the **resolved** path, so a caller recording the artifact in a manifest stores one
    canonical absolute path rather than whatever relative or `..`-bearing form it happened to pass
    in -- two spellings of one file is how a reuse check comes to disagree with itself.

    `path`'s parent directories are created if missing, matching
    `rad_engine.materials.write_materials` and `rad_engine.octree.build_octree`/`run_to_file`, which
    already own that convention for an artifact's own output path.

    **`to_wea` runs before the directory is created**, which is the ordering and not an accident:
    that call refuses a record-less `EPWFile` (a header-only parse -- `gendaymtx` cannot build a sky
    matrix from a location alone), and doing it first means a refused write leaves nothing at all
    behind. The alternative order creates a directory, then fails, and leaves a tree that looks like
    a started build.
    """
    text = to_wea(epw)  # raises EPWError on a record-less file, before anything touches the disk
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # **Not** `path.write_text`, which follows a symlink at the final component. A symlink planted
    # at `<project>/04_skies/<name>.wea` made this an arbitrary-file **overwrite** with
    # attacker-influenced content (the `.wea` header carries `place`, from the EPW's own city
    # field), and the tool-layer `contain()` ran *after* the write -- so the refusal reported a
    # completed breach. `open_regular_file` carries `O_NOFOLLOW`, and its `O_NONBLOCK`-then-clear
    # also stops `os.mkfifo` at this path from wedging the calling thread forever, which
    # `write_text` did in both directions. Containment of the *parent* chain is the caller's, above:
    # `O_NOFOLLOW` does not see a directory symlink partway up.
    with open_regular_file(path, "wb") as handle:
        handle.write(text.encode(WEA_ENCODING, errors=EPW_DECODE_ERRORS))
    resolved = path.resolve()
    logger.debug("wrote .wea with %d hourly records to %s", len(epw.records), resolved)
    return resolved
