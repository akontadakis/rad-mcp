"""`rad_engine.postprocess` -- `falsecolor`, `pfilt`, `pcond` and the LDR converters.

**This module exists so the glare path is structurally unable to reach any of them.**
`rad_engine.rpict` (the fisheye renderer `run_glare` builds on) never imports this module, and
`rad_engine.render.spawn_render` refuses to spawn anything but `rpict` (see its own
`_ALLOWED_PROGRAMS`/`ProgramNotAllowed`). Those two facts are the two halves of the same boundary:
`rad_core.glare.MIN_RESOLUTION_PX`'s 800x800 floor exists because a glare source can hide inside a
single pixel, and `pfilt`'s own job is averaging neighbouring pixels together -- exactly how a real
bright source would then go undetected. Nothing in this module checks for that at runtime, because
nothing needs to: the glare path simply has no route to it.

**The two argv shapes `build_falsecolor_argv` builds are the ones `tests/fixtures/scripts/
falsecolor.sh` validates**, and only those two: `falsecolor -n 10 -s 500 -e` (piped straight from a
luminance `rpict` render, no `-i`) and `falsecolor -i i_view.hdr -p lum_view.hdr -cl -s 2000 -n 10
-l Lux` (an illuminance render overlaid as contour lines on a separate luminance backdrop). `-s 500`
and `-s 2000` are that fixture scene's own numbers -- `docs/tool-surface.md` #12 says so
explicitly -- not defaults this module invents; Task 9 computes the scale a real caller passes.
`quantity="daylight_factor"` is a third, unvalidated-by-fixture shape this module also builds,
sourced from falsecolor's own man page and `docs/specs/radiance-practical-guide.md`: *"The default
multiplier is 179 ... A different multiplier can be given with -m to get daylight factors."*
`1.79 = 179 / 10_000 * 100` -- see `_DAYLIGHT_FACTOR_MULTIPLIER`.

**`LDR_CONVERTERS` is the probe set: a `format -> binary` mapping, where the published `format`
parameter is a bare five-member `Literal`.** The two are different *objects* for that reason alone,
but as of this commit they name the identical five formats -- through Phase 8, `docs/tool-surface.
md` #15's `image` tool published only three (`Literal["bmp", "tiff", "tiff_grey"]`), narrower than
this module's own five keys, and **Phase 9 Task 11 widened it to match** (ruling R307), adding
`ppm`/`png` so check 9.4's PNG refusal is reachable at runtime rather than failing pydantic's
schema validation first -- that widening happened in `rad_mcp.image_tool`
(`IMAGE_CONVERT_FORMATS = tuple(LDR_CONVERTERS)` ties the two together by construction going
forward), not this module. This module's own `LDR_CONVERTERS` already carried all five keys before
that widening landed, because `available_ldr_formats` has to answer for `tiff_grey` regardless of
which format names the tool surface accepts: `tiff_grey` shares `tiff`'s own binary, `ra_tiff`, and
its greyscale flag is `-b` -- see `build_ra_tiff_argv` below, which now builds that argv (ruling
R330, Phase 9 Task 11 fix round 1; this module used to publish only which converter a format uses,
and building the full `ra_tiff` argv, plus the whole spawn shape a program that cannot write to
stdout needs, lived in `rad_mcp.image_tool` until that ruling moved it here -- see `spawn_ra_tiff`'s
own docstring for why).

**The probe measures the install; it does not know which format is missing.** `available_ldr_
formats` reads `env.has(binary)` -- the same accessor `RadianceEnv.capabilities()` already uses --
for every entry in `LDR_CONVERTERS`, so an install missing any converter (not only `ra_png`) is
reported correctly. `ra_bmp`/`ra_tiff`/`ra_ppm` are registered under the existing `"image"`
capability group and `ra_png` under a new one-binary `"png_export"` group in `rad_engine.env`,
specifically so a missing `ra_png` cannot flip `"image"` false for every other format -- see that
module for the reasoning and the task report for what this does to the previously-published
nine-capability-group count.

**`pcond` does not read from standard input, and `ra_tiff` cannot write to it.** Measured on this
machine (Radiance 6.1a): `pcond -h < picture.hdr > out.hdr` exits 1 with pcond's own usage line;
`pcond`'s synopsis is `pcond [options] inpic [outpic]` and the input is a required positional
argument (`outpic` is optional and still goes to stdout, the ordinary shape, when omitted).
`falsecolor`, `pfilt` and `ra_bmp`/`ra_ppm`/`ra_png` all read a picture from stdin with no
positional argument at all when none is given -- the shape `spawn_postprocess` below assumes.
`build_pcond_argv` therefore returns only `pcond`'s flags, the same way `build_falsecolor_argv`'s
`source`/`backdrop` keep `-i`/`-p` as the caller's own choice rather than a fixed shape; a caller
driving `pcond` appends its own positional input path to what `build_pcond_argv` returns instead
of routing it through `spawn_postprocess`'s stdin pipe -- `outpic` is left unnamed, so the
adapted picture is captured off stdout exactly like every other converter here. `ra_tiff` is the
opposite shape: `ra_tiff - - < in.hdr > out.tif` exits 0 but writes a **0-byte** file (measured),
so it needs its own spawn entirely -- see `spawn_ra_tiff` below.

**`spawn_postprocess` pipes bytes, not text, and never merges stdout with stderr** -- the same two
rules `rad_engine.run.run_to_file` keeps for `rpict`, restated here rather than reused because
`run_to_file`'s own `stdin: str | None` parameter encodes as UTF-8 text and a rendered picture is
arbitrary binary data that routinely is not valid UTF-8. The read side goes through
`rad_engine.safe_io.open_regular_file("rb")` for `stdin_path` -- a symlinked or FIFO'd input is
refused rather than followed or blocked on forever, the same guard `run_to_file` applies to its
own output side (ruling R48).

**Rule 2, stated once, truthfully, for every write this module makes (`spawn_postprocess` and
`spawn_ra_tiff` both -- ruling R333, Phase 9 Task 11 fix round 3, rewriting what this paragraph
said before that fix and `spawn_ra_tiff`'s own docstring already said correctly): a failed or
timed-out spawn never creates or corrupts a file at `out_path`.** If `out_path` did not exist
before the call, none exists after a failure. If it already existed, it survives byte-identical --
this function does not open, truncate, or otherwise touch it until a temp file, written in
`out_path`'s own directory, has been confirmed to hold a complete, successful result and is
atomically moved onto `out_path` with `os.replace`.

**Two consequences of that pattern, both deliberate and both measured (Phase 9 Task 11 fix round
4).**

First, the temp file is created by `tempfile.mkstemp(dir=out_path.parent)` -- in the *destination's
own directory*, never a system temp dir -- so `os.replace` is always a same-filesystem rename and
`EXDEV` cannot arise.

Second, **the file that ends up at `out_path` is the temp file, and `mkstemp` creates it at mode
`0600`.** `rename(2)` moves a directory entry and carries the inode's own mode with it; it does
**not** preserve the destination's mode. So an output reads `0600` rather than the process umask's
default, **and rather than whatever was at `out_path` before, in either direction**. Measured under
`umask 022`, on this exact mechanism: a destination at `0644` reads `0600` after (a narrowing) and
a destination at `0400` also reads `0600` (a **widening** -- owner-write added to a file the caller
had deliberately made read-only). *"Always a narrowing, never a widening"* stood here through fix
round 4 and was false for exactly that case; a totality word is a count of one, and `0400`
falsifies it. `0600` is simply the outcome, so a caller needing any other mode must `chmod` after
the call rather than set it beforehand.

Both of this module's write paths build the temp file the same way, so this holds for
`spawn_postprocess` and `spawn_ra_tiff` alike; measured at the wire under `umask 022` on
`falsecolor`, `tonemap`, and `convert` in `bmp`/`ppm`/`tiff`, and pinned by
`tests/test_image_tool.py::TestWhatTheWriteActionsDoToOutAtTheWire` in both directions. Recorded
here as a chosen property rather than left as an unremarked side effect of the R333 rewrite.
`spawn_ra_tiff` has behaved this way since fix round 2; `spawn_postprocess` joined it at R333,
where it previously inherited `open_regular_file`'s own `0o644` creation mode.

**This paragraph used to say "leaves no file
at `out_path`" as if that meant the same thing** -- true for a `out_path` that did not exist
before the call (the ordinary case, and the only one this project's own tests exercised until
Phase 9 Task 11 fix round 2 found the gap), false for one that already did: **each function opened
`out_path` for writing before its own spawn's success was known, independently, at two different
points in this module's history** -- `spawn_ra_tiff` until fix round 2 (Critical A) found it there
first, `spawn_postprocess` until fix round 3 (ruling R333) found the identical shape here, one
round later, once the two functions' failure behaviour was compared at the wire and found to
disagree. Either way the pre-existing file was truncated (or, for `spawn_postprocess`
specifically, truncated and then unlinked on failure) regardless of what happened next -- the same
class of damage `rad_engine.render.spawn_render`'s own guarantee describes for a truncated render,
applied here to a caller's own pre-existing file rather than to this call's own output.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

from .env import RadianceEnv
from .process_group import SpawnStopped, run_in_own_session
from .run import RadianceError, RadianceStoppedError, RadianceTimeoutError
from .safe_io import (
    NotARegularFileError,
    open_regular_file,
    refuse_existing_non_regular_destination,
)

__all__ = [
    "DAYLIGHT_FACTOR_LUX_PER_PERCENT",
    "DEFAULT_LDR_FORMAT",
    "LDR_CONVERTERS",
    "available_ldr_formats",
    "build_falsecolor_argv",
    "build_pcond_argv",
    "build_pfilt_argv",
    "build_ra_tiff_argv",
    "spawn_postprocess",
    "spawn_ra_tiff",
]

#: `docs/specs/radiance-practical-guide.md`, falsecolor section (and falsecolor's own man page,
#: word for word): "The default multiplier is 179 ... A different multiplier can be given with -m
#: to get daylight factors." Formatted with `:g` so `17.900000000000002`-style float noise never
#: reaches an argv -- `179 / 10_000 * 100` is exact in double precision, so this is cosmetic, not
#: load-bearing, but it matches every other `_number`-style formatter in this package (ruling R30).
_DAYLIGHT_FACTOR_MULTIPLIER = f"{179 / 10_000 * 100:g}"

#: Ruling R812: the lux that make one DF % on the daylight-factor falsecolor map. falsecolor reads
#: `-s` in the unit its `-m` multiplier produces (its man page: the default multiplier, 179, gives
#: luminance or illuminance). `rad_engine.image.read_image` reports 179 x the weighted RGB, lux on an
#: `-i` render, while `-m 1.79` makes falsecolor read the same pixels as 1.79 x the weighted RGB, so a
#: `read_image` value is divided by 179 / 1.79 = 100 to be in the legend's unit, DF % against the
#: 10 000 lux reference sky. Written `10_000 / 100` for the same reason as the multiplier above.
DAYLIGHT_FACTOR_LUX_PER_PERCENT = 10_000 / 100

#: The label a daylight-factor falsecolor image gets when the caller does not name one -- "percent"
#: is the daylight-factor convention this project's own tools already publish elsewhere; falsecolor
#: itself has no opinion on the string, only on `-m`.
_DAYLIGHT_FACTOR_LABEL = "DF %"


def build_falsecolor_argv(
    *,
    quantity: str,
    scale_max: float,
    levels: int,
    legend_label: str | None,
    contour: bool,
    source: str | Path | None = None,
    backdrop: str | Path | None = None,
) -> list[str]:
    """The `falsecolor` argv for one image, matching `tests/fixtures/scripts/falsecolor.sh`'s two
    validated shapes (see the module docstring) plus the daylight-factor variant those fixtures do
    not cover.

    `quantity` is `"luminance"`, `"illuminance"` or `"daylight_factor"` -- a label this function
    reads, not one falsecolor itself understands. `"luminance"` gets `-e` (extrema printed on the
    brightest/darkest pixel, falsecolor's own default-input shape); `"illuminance"` and
    `"daylight_factor"` do not, since both are built from a separately-rendered `-i` image or a
    daylight-factor-scaled one, neither of which is the bare `rpict | falsecolor` pipe `-e`'s shape
    describes. `"daylight_factor"` adds `-m 1.79` (see `_DAYLIGHT_FACTOR_MULTIPLIER`) and, absent an
    explicit `legend_label`, `-l "DF %"`.

    `source`/`backdrop` become `-i`/`-p` when given (illuminance contour: the illuminance render and
    the luminance picture to draw contour lines over) and are omitted entirely otherwise, which is
    what leaves the luminance shape reading its picture from standard input -- see
    `spawn_postprocess`.

    `contour=True` adds `-cl`. `legend_label`, when not `None`, becomes `-l`.
    """
    argv: list[str] = ["falsecolor"]
    if source is not None:
        argv += ["-i", str(source)]
    if backdrop is not None:
        argv += ["-p", str(backdrop)]
    if contour:
        argv.append("-cl")
    if quantity == "daylight_factor":
        argv += ["-m", _DAYLIGHT_FACTOR_MULTIPLIER]
    argv += ["-s", str(scale_max)]
    argv += ["-n", str(levels)]

    label = legend_label
    if label is None and quantity == "daylight_factor":
        label = _DAYLIGHT_FACTOR_LABEL
    if label is not None:
        argv += ["-l", label]

    if quantity == "luminance":
        argv.append("-e")

    return argv


def build_pfilt_argv(*, supersample: int) -> list[str]:
    """The `pfilt` argv that brings a supersampled render back down to its final size, dividing
    both axes by `supersample` (`-x /N -y /N`) -- the pairing `rad_engine.render.rendered_size`'s
    own docstring names as `pfilt`'s job, not that module's.

    `supersample=1` returns an empty list: dividing by 1 is a spawn that changes nothing but the
    file's encoding, and every extra Radiance pass this project runs costs a little precision for
    no return. A caller checks for this empty list before deciding whether there is a `pfilt` step
    to run at all -- this function does not spawn anything itself.
    """
    if supersample == 1:
        return []
    return ["pfilt", "-x", f"/{supersample}", "-y", f"/{supersample}"]


def build_pcond_argv(*, human: bool = True) -> list[str]:
    """`pcond`'s own `-h` switch: `-h+` mimics human visual response (a bundle of `-a`/`-v`/`-s`/
    `-c`, per `pcond`'s man page), `-h-` turns it off. See the module docstring for why this
    function returns only the flag -- `pcond` takes its input as a required positional argument,
    never on stdin, so the caller appends that path (and, optionally, an output path) itself rather
    than routing `pcond` through `spawn_postprocess`'s stdin pipe.
    """
    return ["pcond", "-h+" if human else "-h-"]


#: The probe set (ruling R307): every LDR format this module knows how to reach, mapped to the
#: converter binary that reaches it. **Not** the published `image` tool's `format` Literal, which
#: is narrower today (`docs/tool-surface.md` #15: `Literal["bmp", "tiff", "tiff_grey"]`) until Task
#: 11 widens it to these same five names. `tiff` and `tiff_grey` share `ra_tiff` -- the brief this
#: task was handed omitted `tiff_grey` entirely, which would have made `available_ldr_formats`
#: silently unable to answer for it; fixed here.
LDR_CONVERTERS: dict[str, str] = {
    "bmp": "ra_bmp",
    "tiff": "ra_tiff",
    "tiff_grey": "ra_tiff",
    "ppm": "ra_ppm",
    "png": "ra_png",
}

#: `ra_bmp` is present on every Radiance install this project has measured, including the one
#: `ra_png` is absent from -- `bmp` is the one format `available_ldr_formats` never has to fall
#: back away from.
DEFAULT_LDR_FORMAT = "bmp"


def available_ldr_formats(env: RadianceEnv) -> tuple[str, ...]:
    """Which of `LDR_CONVERTERS`' formats this install can actually produce.

    Probes with `env.has(binary)` -- the identical accessor `RadianceEnv.capabilities()` already
    uses to decide a capability group -- so this is a measurement of `env`, not a constant: an
    install missing `ra_bmp`, or one that happens to carry `ra_png`, is reported correctly either
    way. Order matches `LDR_CONVERTERS`' own insertion order.
    """
    return tuple(fmt for fmt, binary in LDR_CONVERTERS.items() if env.has(binary))


#: The shared `out_path` guard, **moved to `rad_engine.safe_io` by the Phase 9 whole-branch fix
#: wave** and aliased back here under its original private name so this module's two spawners --
#: and `docs/tool-surface.md`, `tests/test_postprocess.py` and `tests/test_image_tool.py`, which
#: all name `rad_engine.postprocess._refuse_existing_non_regular_destination` -- keep resolving to
#: the one function.
#:
#: R333 consolidated `spawn_postprocess`'s and `spawn_ra_tiff`'s two copies into one; the fix wave
#: gave that one a third caller, `rad_engine.render.spawn_render`, which may not import this module
#: (`tests/test_pfilt_boundary.py::test_no_module_reachable_from_run_glare_imports_postprocess`).
#: See `safe_io.refuse_existing_non_regular_destination` for the guard's own reasoning.
_refuse_existing_non_regular_destination = refuse_existing_non_regular_destination


def spawn_postprocess(
    env: RadianceEnv,
    argv: list[str],
    *,
    stdin_path: Path | str | None,
    out_path: Path | str,
    cwd: Path | str,
    timeout: float | None,
) -> tuple[Path, tuple[str, ...]]:
    """Run one post-processing argv (`falsecolor`, `pfilt`, or an `ra_*` converter), piping
    `stdin_path`'s bytes into the process's own stdin and writing its stdout to `out_path`.

    See the module docstring for why `stdin_path` is a file rather than the text `rad_engine.run.
    run_to_file` accepts, and why `pcond` is not run through this function. `stdin_path=None` sends
    an empty write followed by a close (`input=b""`) -- the same "declared, never inherited"
    contract `rad_engine.run`'s own spawns keep, for a caller whose argv already names its input
    via a flag (`-i`/`-p`) rather than stdin, such as the illuminance-contour falsecolor shape this
    module's docstring describes.

    **Writes through a temp file plus `os.replace`, not a direct open of `out_path` (ruling R333,
    Phase 9 Task 11 fix round 3).** This function used to open `out_path` itself with
    `open_regular_file(out_path, "wb")` before the spawn ran, exactly the shape `spawn_ra_tiff`'s
    own round 1 guard used and round 2 replaced -- `"wb"` is `O_CREAT|O_TRUNC`, unconditional on
    open, so a pre-existing `out_path` was truncated the moment this function started, and a
    failed spawn then unlinked it, leaving nothing. Measured at the wire on the same garbage input
    across the four `LDR_CONVERTERS` formats this install can actually produce -- four is a fact
    about this install, not about the mapping, which holds five: `png` needs `ra_png`, absent here.
    **What happens to the fifth is the caller's choice, not this module's** (fix round 5, Minor 7):
    `rad_mcp.image_tool`'s `convert` refuses an unavailable format before any write path is
    reached, while `rad_mcp.render_tool`'s LDR step substitutes `DEFAULT_LDR_FORMAT` and reports
    `LDR_FORMAT_SUBSTITUTED` instead -- two live callers of this function, disagreeing deliberately
    (check 9.4), so this docstring states neither as *the* behaviour. Before this fix:
    `tiff`/`tiff_grey` (already
    on `spawn_ra_tiff`'s round 2 fix) left a pre-existing `out` byte-identical; `bmp`/`ppm` (still
    on this function) left it **gone**. A caller cannot reason about a published `format` Literal
    whose members differ in whether they destroy the file on a failed conversion, and this
    function also drives `falsecolor` and `tonemap`'s own postprocess step, so the same defect
    reached those paths too. Now: `_refuse_existing_non_regular_destination` refuses (read-only,
    `lstat`) a symlink/FIFO/device/directory already at `out_path` before anything opens; the
    process writes to a fresh temp file in `out_path`'s own directory; and only **on success** is
    that temp file atomically moved onto `out_path` with `os.replace` (`rename(2)`, which replaces
    whatever directory entry is there without ever dereferencing it -- the same property
    `spawn_ra_tiff`'s own docstring derives at length).

    Returns `(out_path.resolve(), warnings)`, `warnings` being every non-blank stderr line, stripped
    and in call order -- the same "never merged, reworded or truncated" contract `RenderedImage.
    warnings` and `rad_engine.run.Result.warnings` already keep, since a Radiance process that
    exits 0 having written a warning is this project's single most common route to a confidently
    wrong answer.

    Raises `rad_engine.env.RadianceNotFound` (via `env.path`) for an unregistered or absent binary,
    `rad_engine.run.RadianceError` if the process exits non-zero, `rad_engine.run.
    RadianceTimeoutError` if it exceeds `timeout`, and `rad_engine.run.RadianceStoppedError` if the
    server's stop had begun when the spawn read the stop flag (R750). In every one of those cases
    the temp file is unlinked and `out_path` itself is never touched -- not opened, not created, not
    truncated -- which is what makes a pre-existing `out_path` survive a failed spawn
    byte-identical, and a non-existent one stay non-existent.
    """
    cwd = Path(cwd)
    if not cwd.is_dir():
        raise NotADirectoryError(f"cwd for Radiance must be an existing directory: {cwd}")

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    _refuse_existing_non_regular_destination(out_path)

    # Resolved -- and any `RadianceNotFound` raised -- before the temp file exists, matching
    # `spawn_ra_tiff`'s own ordering: nothing on disk to leak if this raises.
    binary, *args = argv
    resolved = [str(env.path(binary)), *args]

    fd, tmp_name = tempfile.mkstemp(
        dir=str(out_path.parent), prefix=".rad-postprocess-", suffix=".tmp"
    )
    tmp_path = Path(tmp_name)

    try:
        with os.fdopen(fd, "wb") as sink:
            if stdin_path is not None:
                with open_regular_file(Path(stdin_path), "rb") as source:
                    returncode, _, raw_err = run_in_own_session(
                        resolved, cwd=cwd, env=env.environ(cwd), stdout=sink,
                        timeout=timeout, stdin=source,  # separate stderr, always -- see the module docstring
                    )
            else:
                returncode, _, raw_err = run_in_own_session(
                    resolved, cwd=cwd, env=env.environ(cwd), stdout=sink, timeout=timeout,
                    input=b"",  # declared EOF, never the inherited fd -- see run.py's own note
                )
    except subprocess.TimeoutExpired as exc:
        stderr = exc.stderr or b""
        if isinstance(stderr, bytes):
            stderr = stderr.decode(errors="replace")
        tmp_path.unlink(missing_ok=True)
        raise RadianceTimeoutError(resolved, timeout or 0.0, stderr) from exc
    except SpawnStopped as exc:
        tmp_path.unlink(missing_ok=True)
        raise RadianceStoppedError(resolved, str(exc)) from exc
    except BaseException:
        # `BaseException`, matching `rad_engine.render.spawn_render`'s own handler: a
        # `KeyboardInterrupt` or a worker-pool cancellation leaves the same truncated file a
        # timeout does -- now the temp file, never `out_path`.
        tmp_path.unlink(missing_ok=True)
        raise

    stderr_text = raw_err.decode(errors="replace")
    if returncode != 0:
        tmp_path.unlink(missing_ok=True)
        raise RadianceError(resolved, returncode, stderr_text)

    os.replace(tmp_path, out_path)
    warnings = tuple(line.strip() for line in stderr_text.splitlines() if line.strip())
    return out_path.resolve(), warnings


def build_ra_tiff_argv(*, greyscale: bool) -> list[str]:
    """`ra_tiff`'s own flags. `greyscale=True` adds `-b`, for the `tiff_grey` format
    `LDR_CONVERTERS` maps onto the same binary as `tiff`.

    Returns only the flags, not the two positional filenames: `spawn_ra_tiff` appends those
    itself, because -- unlike every argv `spawn_postprocess` drives -- `ra_tiff` needs a real
    destination path and cannot take one from a caller-supplied string safely (see that
    function's own docstring for why).
    """
    return ["ra_tiff", *(["-b"] if greyscale else [])]


def spawn_ra_tiff(
    env: RadianceEnv,
    argv: list[str],
    *,
    source: Path | str,
    out_path: Path | str,
    cwd: Path | str,
    timeout: float | None,
) -> tuple[Path, tuple[str, ...]]:
    """Run `ra_tiff` (`build_ra_tiff_argv`'s argv), which takes both its input and its output as
    real positional filenames and cannot write to stdout at all.

    **Moved here from `rad_mcp.image_tool` (ruling R330, Phase 9 Task 11 fix round 1).** That
    module had put a Radiance program name, a Radiance flag and this whole spawn shape in the
    plumbing layer -- against CONTEXT.md's own Layers rule, which reserves running a binary for
    `rad_engine` -- and, measured directly, that placement is what lost this write's guard: it
    called `rad_engine.run.run` with `out_path` as `ra_tiff`'s own second positional argument, so
    `ra_tiff`'s own `fopen` opened it, with no `O_NOFOLLOW`, and a symlink planted at `out_path`
    (inside an allowed root, pointing outside every one) was followed -- an arbitrary write outside
    every configured root. Measured before this fix: `ra_tiff <src> <symlinked out_path>` wrote
    389 170 bytes to the symlink's target, entirely outside the contained tree.

    **The guard: `ra_tiff` never opens `out_path` by that name at all, and neither does this
    function until the spawn has already succeeded.** Two steps:

    1. `_refuse_existing_non_regular_destination(out_path)` -- `os.lstat`, never an `open()`, so
       nothing is created or truncated by the check itself. See that function's own docstring for
       why the refusal message names no specific type.
    2. `ra_tiff` is given a **temp file in `out_path`'s own directory** as its output, never
       `out_path` itself, and only **on success** is that temp file atomically moved onto
       `out_path` with `os.replace` (`rename(2)`). `rename` replaces whatever directory entry
       currently sits at the destination -- symlink included -- **without ever dereferencing it**;
       this is what closes the race the `lstat` check alone would only narrow (a symlink planted
       in the instant between the check and `ra_tiff`'s own run): even if that race is won,
       `os.replace` still cannot be tricked into writing through the pre-existing directory entry,
       symlink or not. `out_path` is **never opened** by this function on the failure path -- there
       is no step 1 side effect left to clean up, because step 1 no longer has one.

    **`spawn_postprocess` now uses this identical two-step pattern for its own `out_path` (ruling
    R333, Phase 9 Task 11 fix round 3) -- it did not, when this function was first written, and
    that gap was itself a defect.** Measured before R333, at the wire, on the same garbage input:
    `tiff`/`tiff_grey` (already on this function's own fix round 2) left a pre-existing `out`
    byte-identical after a forced failure; `bmp`/`ppm` (still on `spawn_postprocess`'s pre-R333
    shape, `open_regular_file(out_path, "wb")` opened and truncated unconditionally before the
    spawn could even fail) left it **gone**. A caller cannot reason about a published `format`
    Literal whose members differ in whether they destroy the file on failure, so `spawn_postprocess`
    was moved onto the identical pattern rather than this function's guarantee staying stronger
    than a sibling function serving the same published argument. See `spawn_postprocess`'s own
    docstring for that history. **The `open_regular_file`-versus-`lstat` exception-type contrast
    this function's own fix round 2 worked out no longer applies to either function**: neither
    calls `open_regular_file` on its own `out_path` any more, so there is nothing left to contrast
    -- both raise `NotARegularFileError` uniformly, via the shared
    `_refuse_existing_non_regular_destination`.

    **Why not the same trick `run_to_file` uses (an already-open, `O_NOFOLLOW`-verified descriptor
    handed to the child)?** Measured: passing `ra_tiff` `/dev/fd/<n>` as its output positional
    argument, with that descriptor kept open across the spawn (`pass_fds`), fails outright on this
    machine -- libtiff's own `TIFFOpen` refuses `/dev/fd/3` with `Permission denied`, even though a
    plain shell redirect through the identical `/dev/fd` entry works. That mechanism is therefore
    not available for this specific binary, and the temp-file-plus-rename pattern above is the
    substitute that reaches the same property (this program never performs its own lookup of an
    attacker-influenced path) through a route that actually works against `ra_tiff`.

    On failure or a timeout, the temp file is unlinked and `out_path` is left completely
    untouched -- never opened, never created, never truncated.
    """
    cwd = Path(cwd)
    if not cwd.is_dir():
        raise NotADirectoryError(f"cwd for Radiance must be an existing directory: {cwd}")

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    _refuse_existing_non_regular_destination(out_path)

    # Resolved -- and any `RadianceNotFound` raised -- before the temp file exists, matching
    # `spawn_postprocess`'s own ordering: nothing on disk to leak if this raises.
    binary, *flags = argv
    resolved_binary = str(env.path(binary))

    fd, tmp_name = tempfile.mkstemp(
        dir=str(out_path.parent), prefix=".ra_tiff-", suffix=".tmp"
    )
    os.close(fd)
    tmp_path = Path(tmp_name)
    resolved = [resolved_binary, *flags, str(source), str(tmp_path)]

    try:
        returncode, _, raw_err = run_in_own_session(
            resolved, cwd=cwd, env=env.environ(cwd), stdout=subprocess.PIPE, timeout=timeout,
            input=b"",  # ra_tiff takes its input as a positional path, not stdin -- see the module
                        # docstring; declared EOF regardless, matching every other spawn here.
        )
    except subprocess.TimeoutExpired as exc:
        stderr = exc.stderr or b""
        if isinstance(stderr, bytes):
            stderr = stderr.decode(errors="replace")
        tmp_path.unlink(missing_ok=True)
        raise RadianceTimeoutError(resolved, timeout or 0.0, stderr) from exc
    except SpawnStopped as exc:
        tmp_path.unlink(missing_ok=True)
        raise RadianceStoppedError(resolved, str(exc)) from exc
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise

    stderr_text = raw_err.decode(errors="replace")
    if returncode != 0:
        tmp_path.unlink(missing_ok=True)
        raise RadianceError(resolved, returncode, stderr_text)

    os.replace(tmp_path, out_path)
    warnings = tuple(line.strip() for line in stderr_text.splitlines() if line.strip())
    return out_path.resolve(), warnings
