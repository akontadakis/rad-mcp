"""`rad_engine.render` -- the general-purpose `rpict` argv builder Phase 9's `run_render` needs,
and the single-spawn wrapper every `rpict` invocation in this phase goes through.

Where `rad_engine.rpict` (Phase 8) hardcodes one hemispherical fisheye view at one of two
DGP-oriented quality presets for `run_glare`, this module builds the general case: any of the six
sourced view types (`rad_core.views.VIEW_TYPES`), the ambient presets `rad_core.quality.
AMBIENT_PRESETS` publishes (or a caller override, resolved to a `QualityParameters` before this
module ever sees it), and an optional `-i` for an illuminance render. It owns no sky, no octree
compilation and no post-processing -- `pfilt`/`falsecolor`/`pcond` are `rad_engine.postprocess`'s
job (Phase 9 Task 8), not this module's.

**The supersample multipliers are sourced, but not from where the task brief that named this
module said they were (ruling R315).** The brief's three quoted phrases -- "a modest degree of
antialiasing", "aliasing artifacts should be negligible", "some aliasing may be apparent" -- are
NOT in `docs/specs/radiance-engine-overview.md`. Measured at 7fec46f::

    $ grep -c "modest degree of antialiasing" docs/specs/radiance-engine-overview.md
    0
    $ grep -c "aliasing may be apparent" docs/specs/radiance-engine-overview.md
    0

and a grep for both phrases across every file in `docs/specs/` returns 0 hits as well. Both
phrases are real text, but they live in **`docs/tool-surface.md#12-run_render`** (the paragraph
"Rendering at the output resolution is not how Radiance anti-aliases"), which itself cites
`radiance-engine-overview.md` §rad for them -- a citation this project could not verify at 7fec46f
(§rad does not use this wording; nothing in `docs/specs/` does). So this module cites that
paragraph directly, not the spec its citation names, and records here that the spec citation
itself is unverified. **The multipliers stand regardless**: `docs/tool-surface.md`
is this project's own published tool surface and the numbers it states are the real `rad` QUALITY
model (Medium computes at 2x the final size, High at 3x, Low at 1x) -- so `SUPERSAMPLE_BY_PRESET`
below takes `draft=1` / `standard=2` / `high=3` from that document as published, not invented.

**`SUPERSAMPLE_BY_PRESET["reference"] = 3` is this project's own inference, not the source's.**
That paragraph names three quality levels (Low/Medium/High); `rad_core.quality.
AMBIENT_PRESETS` publishes four (`draft`/`standard`/`high`/`reference`). `reference` is the most
accurate ambient preset -- the one with `-aa 0.0`, ambient interpolation disabled entirely -- so
giving it the *smallest* supersample factor of the four (matching `draft`, the cheapest preset)
would contradict what the preset is for: a ground-truth render antialiased no better than the
draft one. Reusing `high`'s multiplier, the largest sourced value, is the reading that does not
undercut `reference`'s own purpose. The source names no fourth level at all, so this is this
project's inference, flagged as one rather than presented as sourced.

**`SUPERSAMPLE_BY_PRESET` publishes four keys though `docs/tool-surface.md` §12's `quality`
parameter states only a three-member `Literal` (`draft`/`standard`/`high`).** `reference` is
reachable through that same parameter's other form -- `quality: Literal[...] | dict` -- not
through the Literal, the same split `rad_core.quality.AMBIENT_PRESETS` already carries (four keys,
even though callers that only ever pass the Literal only ever see three of them). Dropping the
fourth key here would silently strand a `quality={"base": "reference"}` caller: `rad_core.quality.
AMBIENT_PRESETS["reference"]` resolves happily, but this module would have no supersample factor to
look up for it. Keeping all four is what makes the two tables agree.

**The spawn wrapper is the second half of the `pfilt` boundary** (Task 8 is the first). Every
process this module starts goes through `spawn_render`, and `spawn_render` refuses any argv whose
program is not `rpict` -- see `_ALLOWED_PROGRAMS` and `ProgramNotAllowed` below.

**The allow-list check runs before `env` is ever touched.** `spawn_render`'s first two statements
read `argv` alone; `env` is not read until the `run_to_file(env, argv, ...)` call several lines
later, which is reached only once the program name has already been accepted. That ordering is
what makes `spawn_render(None, ["pfilt", ...], ...)` raise `ProgramNotAllowed` rather than
`AttributeError` on `None.path` -- a caller proving the allow-list works never needs a real
`RadianceEnv` to do it.

**`RenderedImage.rendered_resolution` and `.final_resolution` are equal here, and `.supersample`
is always `1`.** This module never downsamples anything -- that is `rad_engine.postprocess`'s
`pfilt` step (Task 8), which this module does not import (see the `pfilt` boundary above).
`spawn_render` reads `-x`/`-y` back out of the argv it was actually given -- the same numbers
`rpict` rendered at -- and reports them as both fields, because as far as this function knows
nothing downstream has changed the picture's size yet. A caller that runs `pfilt` afterwards
(Task 8) builds its own updated `RenderedImage` with `dataclasses.replace(image,
final_resolution=..., supersample=...)` rather than this function inventing a final size it never
computed.

**Warnings are carried, verbatim and in order, on `RenderedImage.warnings`** -- the same contract
`rad_engine.rpict.RenderedFisheye.warnings`, `Octree.warnings`, `ReadScene.warnings` and
`ImageInfo.warnings` already keep: a Radiance process that exits 0 having written to stderr is this
project's single most common route to a confidently wrong answer, and never merged, reworded or
truncated.

**A failed render leaves no file behind, and does not truncate one that was already there**
(whole-branch fix wave, R336). `rpict` writes to its stdout, so a descriptor has to be open before
the process starts, and `run_to_file` opens it `"wb"` -- `O_WRONLY|O_CREAT|O_TRUNC`. Opening
`out_path` directly therefore destroyed a pre-existing image *before* the render had produced
anything, and the old `except BaseException: out_path.unlink()` handler then deleted the remains:
a timed-out re-render cost the caller the good image from the previous run, silently.
`spawn_render` now takes `spawn_postprocess`/`spawn_ra_tiff`'s pattern instead -- lstat-refuse a
non-regular destination, render into a `mkstemp` file in the destination's own directory, unlink
*that* on any `BaseException` and re-raise unchanged, `os.replace` onto `out_path` only on
success. `rad_engine.rpict.render_fisheye` has no handler of its own and needs none; see
`spawn_render`'s docstring.

**Every spawn goes through `rad_engine.run.run_to_file`, never a bare `subprocess` call**, and
`spawn_render` is the only function in this module that calls it -- the structural guarantee the
`pfilt` boundary depends on. `run_to_file` writes `rpict`'s stdout (the HDR image) to `out_path`
and keeps stderr separate in memory; merging them (`> file 2>&1`) would corrupt the image with
warning text.
"""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from rad_core.quality import QualityParameters
from rad_core.views import View, vt_flag

from .env import RadianceEnv
from .run import run_to_file
from .safe_io import refuse_existing_non_regular_destination

__all__ = [
    "ProgramNotAllowed",
    "RenderedImage",
    "SUPERSAMPLE_BY_PRESET",
    "build_rpict_argv",
    "rendered_size",
    "spawn_render",
]


#: docs/tool-surface.md#12-run_render, the anti-aliasing paragraph (ruling R315 -- see the module
#: docstring for why this is cited instead of the spec that document itself names).
#: `"draft"`/`"standard"`/`"high"` are the sourced Low/Medium/High multipliers (1/2/3);
#: `"reference"` is this project's own inference, not the source's -- also documented above.
SUPERSAMPLE_BY_PRESET: dict[str, int] = {
    "draft": 1,
    "standard": 2,
    "high": 3,
    "reference": 3,
}


def rendered_size(width: int, height: int, supersample: int) -> tuple[int, int]:
    """The size `rpict` should actually render at: both axes multiplied by `supersample`.

    `supersample=1` is the identity -- `docs/tool-surface.md`'s `LOW` behaviour, computing the
    picture at exactly the final size. The caller (not `build_rpict_argv`, see that function's own
    docstring) is responsible for calling this before building the argv; `pfilt -x /N -y /N`
    (Task 8) is what brings the result back down to `(width, height)` afterwards.
    """
    return width * supersample, height * supersample


def _number(value: float) -> str:
    """A float as `rpict`'s argv sees it: `%g`, so `180.0` becomes `180` and `2.0` becomes `2`.
    Same convention as `rad_engine.rpict`/`rad_engine.evalglare`/`rad_engine.matrices`/
    `rad_engine.bsdf`/`rad_engine.klems`'s own `_number` (ruling R30: one formatting rule, not a
    further copy of it) -- kept as a module-local one-liner rather than imported, matching how each
    of those modules already keeps its own copy of this exact expression."""
    return f"{value:g}"


def build_rpict_argv(
    view: View,
    parameters: QualityParameters,
    *,
    width: int,
    height: int,
    irradiance: bool = False,
    octree: Path,
) -> list[str]:
    """The `rpict` argv for one general-purpose render, `octree` always last.

    `view.view_type` selects the `-vt` flag via `rad_core.views.vt_flag`, which raises
    `rad_core.views.ViewTypeError` for anything not one of the six sourced letters -- this function
    does not itself validate the letter a second time, matching `vt_flag`'s own "an unknown one is
    refused rather than defaulted to perspective" contract.

    **`width`/`height` become `-x`/`-y` unchanged -- this function does not multiply anything by a
    supersample factor.** `rendered_size()` is the caller's tool for computing the size to pass
    here; if this function applied the multiplier itself as well, a caller that already called
    `rendered_size()` would double-apply it. Pass `rendered_size(final_w, final_h, supersample)`'s
    result as `width`/`height` to get a supersampled render; pass the final size directly (or
    `supersample=1`) to get `docs/tool-surface.md`'s `LOW` behaviour.

    `irradiance=True` adds `-i`, the flag that makes the difference between a luminance and an
    illuminance image -- the two look identical without it.

    The five ambient flags (`-ab`/`-ad`/`-as`/`-ar`/`-aa`) always appear, taken from `parameters`;
    `-dt`/`-dc`/`-av` appear only when `parameters.direct_threshold`/`.direct_certainty`/
    `.ambient_value` are not `None` -- the same "`None` means no flag, not an assumed default"
    contract `rad_engine.rtrace.build_rtrace_argv` already keeps, and the same formatting
    (`str(int(...))` for the four integer ambient flags, `repr(float(...))` for `-aa`/`-dt`/`-dc`/
    `-av`).

    **`-av` is a grey triple, `red grn blu` all equal to `parameters.ambient_value`** (ruling R327:
    `run_render(mode="hdr")` is the one call in this project that ever sets it, as a presentation
    choice -- see `rad_core.quality.QualityParameters.ambient_value`'s own docstring for the
    citation). `rpict`'s `-av` takes three numbers
    (docs/specs/radiance-practical-guide.md:1705, `"-av red grn blu"`); this module has no separate
    per-channel input to carry, so all three are the same value.

    No specular flags are built here at all, so `-sj` (ruling R271 -- the source table's own
    "Specular jitter" row is not a real `rpict` flag) cannot appear; this argv builder does not
    read the DGP-oriented table `-sj`/`-ss` live in, only the ambient family.

    Returns a plain argv **leading with the bare binary name** (`"rpict"`), matching
    `build_octree`'s, `build_rtrace_argv`'s and `rad_engine.rpict.build_rpict_argv`'s own contract:
    `rad_engine.run.run_to_file` resolves `"rpict"` via `env.path("rpict")` and substitutes it for
    `argv[0]` -- it does not prepend an extra element, so the returned argv is never longer than
    what this function built.
    """
    px, py, pz = view.position
    dx, dy, dz = view.direction
    ux, uy, uz = view.up

    argv: list[str] = [
        "rpict",
        vt_flag(view.view_type),
        "-vp", _number(px), _number(py), _number(pz),
        "-vd", _number(dx), _number(dy), _number(dz),
        "-vu", _number(ux), _number(uy), _number(uz),
        "-vh", _number(view.vh),
        "-vv", _number(view.vv),
        "-x", str(int(width)),
        "-y", str(int(height)),
    ]
    if irradiance:
        argv.append("-i")

    argv += [
        "-ab", str(parameters.ambient_bounces),
        "-ad", str(parameters.ambient_divisions),
        "-as", str(parameters.ambient_super_samples),
        "-ar", str(parameters.ambient_resolution),
        "-aa", repr(float(parameters.ambient_accuracy)),
    ]
    if parameters.direct_threshold is not None:
        argv += ["-dt", repr(float(parameters.direct_threshold))]
    if parameters.direct_certainty is not None:
        argv += ["-dc", repr(float(parameters.direct_certainty))]
    if parameters.ambient_value is not None:
        av = repr(float(parameters.ambient_value))
        argv += ["-av", av, av, av]

    argv.append(str(octree))
    return argv


#: The one program this module is permitted to start. Named as a basename set rather than as a
#: string comparison, so `/usr/local/radiance/bin/pfilt` is refused as surely as `pfilt`.
_ALLOWED_PROGRAMS = frozenset({"rpict"})


class ProgramNotAllowed(RuntimeError):
    """An argv whose program this module is not permitted to spawn.

    **This is the structural half of the `pfilt` boundary**, and it is a runtime guard rather than
    a comment because Phase 9's `pfilt` is a *second spawn piped after* `rpict`: an argv-substring
    assertion over one builder cannot see it. `run_glare` reaches `rpict` through
    `rad_engine.rpict`, not through this module, so this guard's own stake is `run_render`'s path:
    a `pfilt`/`falsecolor` call added anywhere downstream of this module raises here rather than
    silently running. `rad_core.glare.MIN_RESOLUTION_PX`'s 800x800 floor exists because a glare
    source can hide inside one pixel; down-filtering is precisely how it then goes undetected --
    the reason `rad_engine.rpict` (the glare path) never imports this module or `pfilt` at all.
    """


def spawn_render(
    env: RadianceEnv | None,
    argv: list[str],
    *,
    out_path: Path | str,
    cwd: Path | str,
    timeout: float | None,
) -> RenderedImage:
    """Run one `rpict` argv, writing stdout to `out_path`. The only place this module starts a
    process.

    **Checks the program name before touching `env` at all** (see the module docstring): an empty
    `argv` or a disallowed program raises `ProgramNotAllowed` from the argv alone, so `env=None` is
    a legal way to prove the allow-list without a real Radiance install.

    `rendered_resolution`/`final_resolution`/`supersample` are read back from `argv`'s own `-x`/
    `-y` (see the module docstring for why this task's own spawn never has a different rendered vs.
    final size). A malformed `argv` missing `-x`/`-y` raises `ValueError` from that lookup -- this
    function does not build the argv itself, so no other build_rpict_argv-conformant input is
    expected to be missing them.

    Raises `rad_engine.run.RadianceError` (via `run_to_file`'s own `check=True` default) if `rpict`
    exits non-zero, or `rad_engine.run.RadianceTimeoutError` if it exceeds `timeout`.

    **A failed render leaves no file behind, and it no longer destroys one either (whole-branch
    fix wave, R336; R284 is the same site).** `rpict` writes to its stdout, so the destination has
    to be an open descriptor before the process starts -- and `run_to_file` opens it through
    `safe_io.open_regular_file(out_path, "wb")`, where `"wb"` is `O_WRONLY|O_CREAT|O_TRUNC`. That
    is **two** destructive acts, not one: a pre-existing file at `out_path` was truncated to zero
    the instant the spawn began, and the old `except BaseException: unlink()` handler then removed
    what was left of it. A caller whose render timed out lost the good image from the previous run
    as well as the new one, and nothing reported it.

    So this function now uses the pattern its two siblings already use --
    `rad_engine.postprocess.spawn_postprocess` and `spawn_ra_tiff`:

    1. `safe_io.refuse_existing_non_regular_destination(out_path)` -- `os.lstat`, never an
       `open()`, so an absent destination stays absent and an existing regular file is read and
       not touched. A symlink, directory, FIFO or device at `out_path` is refused here rather
       than followed or truncated.
    2. `rpict` renders into a `tempfile.mkstemp` file **in `out_path`'s own directory**, so the
       final step is a same-filesystem rename. `run_to_file` opens that temp path with the same
       `O_NOFOLLOW`/`S_ISREG` guards it always did; only the path it opens has changed.
    3. `os.replace(tmp, out_path)` on success. `rename(2)` never dereferences the destination
       entry, so it cannot follow a symlink planted at `out_path` between (1) and here -- see
       `spawn_postprocess`'s own note, whose reasoning applies unchanged.

    **One honest difference from the siblings.** They write through the descriptor `mkstemp`
    returns; this function closes it and lets `run_to_file` open the temp path by name, because
    `run_to_file` owns the spawn and takes a path, not an fd. So there is a window between the
    close and that open in which the temp *name* could be swapped. `mkstemp` names are
    unpredictable and the directory is inside a project root, and `open_regular_file`'s
    `O_NOFOLLOW` plus `S_ISREG`-on-the-descriptor still refuse a symlink or a non-regular file put
    there -- but the window is real and is recorded here rather than described away.

    Every failure path -- non-zero exit, timeout, `KeyboardInterrupt`, a worker-pool cancellation
    -- unlinks the **temp** file and re-raises unchanged. A pre-existing `out_path` is left exactly
    as it was, byte for byte, because nothing ever opened it.

    `except BaseException` rather than `except Exception` for the same reason `spawn_postprocess`
    gives: a `KeyboardInterrupt` or a cancellation through the anyio worker pool leaves the same
    half-written file a timeout does. (Until this fix that clause justified itself as *"matching
    `rad_engine.rpict.render_fisheye`'s own handler"*, and `render_fisheye`'s comment justified
    having no handler by pointing here -- each citing the other, and only one of them existed.
    `render_fisheye` still has no handler of its own and still does not need one; the sentence
    that claimed one has been corrected there too.)
    """
    if not argv:
        raise ProgramNotAllowed("An empty argv names no program, and none is assumed.")
    program = Path(argv[0]).name
    if program not in _ALLOWED_PROGRAMS:
        raise ProgramNotAllowed(
            f"rad_engine.render spawns only {sorted(_ALLOWED_PROGRAMS)}, and this argv names "
            f"{program!r}. Post-processing belongs in rad_engine.postprocess, which the glare path "
            f"does not import."
        )

    rendered_resolution = (
        int(argv[argv.index("-x") + 1]),
        int(argv[argv.index("-y") + 1]),
    )

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    refuse_existing_non_regular_destination(out_path)

    fd, tmp_name = tempfile.mkstemp(dir=str(out_path.parent), prefix=".rad-render-", suffix=".tmp")
    # Closed immediately: `mkstemp` is used for the atomic, unpredictable *name* in the
    # destination directory, and `run_to_file` does its own guarded open of that path. Holding
    # this descriptor would leak it for the whole render.
    os.close(fd)
    tmp_path = Path(tmp_name)

    try:
        result = run_to_file(env, argv, out_path=tmp_path, cwd=cwd, timeout=timeout)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise

    os.replace(tmp_path, out_path)

    return RenderedImage(
        path=out_path.resolve(),
        rendered_resolution=rendered_resolution,
        final_resolution=rendered_resolution,
        supersample=1,
        warnings=tuple(result.warnings),
    )


@dataclass(frozen=True)
class RenderedImage:
    """`spawn_render`'s outcome: the file it wrote, the size `rpict` actually rendered at, the
    size this task reports as final (equal, here -- see the module docstring), and the `rpict`
    stderr carried through.

    `warnings` is `rad_engine.run.Result.warnings`, verbatim and in call order -- see the module
    docstring for why this is not a bare `Path`.
    """

    path: Path
    rendered_resolution: tuple[int, int]
    final_resolution: tuple[int, int]
    supersample: int
    warnings: tuple[str, ...]
