"""The `image` tool -- docs/tool-surface.md#15-image--inspect-falsecolor-tonemap-and-convert-built.

(The heading is `### 15. `image` -- *inspect, falsecolor, tonemap and convert built*`, and GitHub's
slug carries the whole of it, the em dash leaving the double hyphen.
`test_the_module_docstring_anchor_resolves` derives the slug from the heading actually in the file,
so the two move together -- this is the third wording the heading has carried:
`*inspect built, other actions in Phase 9*` (through Phase 8) claimed `compare` for a phase it was
never assigned to and ruling R300 struck that clause; `*inspect built*` (Phase 8 through Phase 9
Task 10) was accurate only while `inspect` was the sole live action; this task's own three make it
stale in the other direction, so it is reworded again, to name what is live now rather than what
still is not.)

Thin, in the sense CONTEXT.md's Layers table means it: this module contains and validates
arguments, picks the engine call, maps findings onto the wire and shapes the envelope. **No number
reported here is computed here.** `rad_engine.image.read_image` owns `getinfo`, `pextrem` and
`pvalue`; `rad_engine.postprocess` owns `falsecolor`, `pcond` and the LDR converters, and `rad_core.
views.choose_scale` owns the falsecolor scale arithmetic; `rad_core.glare.check_glare_input` owns
the fisheye/resolution preconditions and decides `suitable_for_evalglare`. This module plumbs those
together and adds the one thing none of them can: containment of a caller-supplied absolute path
against this server's configured roots (`rad_mcp.paths`, ruling R48) -- both the image `path` this
tool reads and, for the three actions that write one, the `out` path it writes.

## Ruling R258 (Phase 8) and Phase 9 Task 11: four of five actions built, `compare` stays unowned

The published signature (docs/tool-surface.md #15) names five actions -- `inspect`, `falsecolor`,
`tonemap`, `convert`, `compare`. Phase 8 (ruling R258) built `inspect`. **This task builds the other
three docs/plan.md's Phase 8 section assigns to Phase 9 by name** -- `falsecolor` (`falsecolor`),
`tonemap` (`pcond`) and `convert` (the LDR converters: `ra_bmp`/`ra_tiff`/`ra_ppm`, and `ra_png`
when this install carries it). `compare` (a per-pixel difference between two images) is reached by
no check in any phase and is assigned to none: docs/plan.md's Phase 9 section delivers `run_render`,
`quality` and `export`, and its Phase 8 section names only `pfilt` supersampling, `falsecolor`,
`pcond` and the LDR converters as what Phase 9 adds here -- `compare` is not among them.
**`compare` is therefore specified and unowned, not deferred** (ruling R304), which is what its
refusal says. `IMAGE_ACTIONS` is still the whole published set, unchanged -- shrinking it would be a
wire-contract change, and that decision belongs to the doc, not to this task. `LIVE_IMAGE_ACTIONS`
now names four of the five; `compare` is the one action outside it, and it refuses with
`ARGUMENT_OUT_OF_RANGE` (`field="action"`) rather than being silently accepted and mishandled or
hidden from the signature.

## `format` widens to five members (ruling R307), so `convert`'s PNG refusal is reachable

`format` published `Literal["bmp", "tiff", "tiff_grey"]` through Phase 8; a caller could never have
named `"ppm"` or `"png"` at all, so a PNG request could never reach `convert`'s own runtime refusal
-- it would fail pydantic's schema validation first, as a different, uncoded kind of error. This
task widens the Literal to all five `rad_engine.postprocess.LDR_CONVERTERS` keys
(`"bmp"`/`"tiff"`/`"tiff_grey"`/`"ppm"`/`"png"`), matching what that module already probed for
regardless of what this tool's own signature accepted -- **a published wire-surface change**, and
Task 16 (`docs/tool-surface.md`'s own change log) records it.

## `tonemap` and `convert` -- what each postprocess binary actually accepts, measured

`falsecolor`, `pfilt`, `ra_bmp` and `ra_ppm` all read a picture from stdin and write one to stdout
when given no positional filenames -- the shape `rad_engine.postprocess.spawn_postprocess` assumes,
and the shape `action="falsecolor"` and `action="convert"` with `format` in `{"bmp", "ppm"}` use.
Two binaries do not fit that shape, measured directly on this machine (Radiance 6.1a) rather than
assumed from a man page:

- **`pcond` takes its input as a required positional filename, never from stdin** -- see
  `rad_engine.postprocess`'s own module docstring, and `pcond -h+ <input> > out.hdr` (`docs/specs/
  radiance-engine-overview.md` line 934) shows the *output* still goes to stdout when no `outpic`
  is given. Measured: `pcond -h+ high_presp_view.hdr` with `outpic` omitted exits 0 and writes the
  adapted picture to stdout, byte-identical in shape to a `falsecolor`/`pfilt` invocation. So
  `action="tonemap"` still goes through `spawn_postprocess` unmodified: `argv = [*build_pcond_argv(
  human=True), str(resolved_path)]`, `stdin_path=None` (pcond never reads it), `out_path` captures
  pcond's own stdout exactly the way it captures `falsecolor`'s.
- **`ra_tiff` cannot write to stdout at all.** Its usage line is `{in.hdr|-} out.tif` -- `outpic` is
  not bracketed, and measured: `ra_tiff - - < in.hdr > out.tif` exits 0 but writes a **0-byte**
  file, while `ra_tiff in.hdr out.tif` (a real second positional) writes the real image and prints
  nothing to stdout or stderr. `spawn_postprocess`'s whole contract is capturing the child's stdout
  into `out_path`; that is the wrong shape for a binary that writes its own named file and leaves
  stdout empty. `format in {"tiff", "tiff_grey"}` therefore calls `rad_engine.postprocess.
  spawn_ra_tiff`, not `rad_engine.run.run` directly the way this task first shipped it -- a local
  `_spawn_ra_tiff` here called `rad_engine.run.run` with `out_path` as `ra_tiff`'s own positional
  argument, and `ra_tiff`'s own `fopen` of that name, unguarded, was a symlink escape (ruling R330,
  Phase 9 Task 11 fix round 1: `_spawn_ra_tiff` was deleted from this module and its guarded
  replacement built in the engine layer instead; see `spawn_ra_tiff`'s own docstring for the
  measured escape and the fix). **As of that fix, `ra_tiff` never opens `out_path` by that name at
  all**: `spawn_ra_tiff` gives it a fresh temp file in `out_path`'s own directory and only moves
  that temp file onto `out_path` with `os.replace` after the spawn has already succeeded, having
  first refused (via a read-only `os.lstat`, never an `open()`) a symlink, FIFO, device or
  directory already sitting at `out_path`. **This is not a narrower write guarantee than the rest
  of this module any more** -- it was, through fix round 2, while `spawn_postprocess` still opened
  its own `out_path` directly; ruling R333 (fix round 3) moved `spawn_postprocess` onto the
  identical temp-file-plus-`os.replace` pattern (see that function's own docstring), so every write
  this module makes now gets the same final-component symlink guard from the same mechanism --
  this project's own `lstat` check and atomic rename, not any one binary's own `fopen`. `tiff_grey`
  is the same binary with `-b` (greyscale) added -- `rad_engine.postprocess.LDR_CONVERTERS` maps
  both to `"ra_tiff"` and leaves the flag to whichever caller builds the full argv, which is this
  module.

## R326: `choose_scale` and `clipped_fraction` are not composed here, and here is why not

Task 9 landed `rad_core.views.choose_scale`/`clipped_fraction`/`FalsecolorScale` with `choose_scale`
always setting `clipped_fraction=None`. `action="falsecolor"` is a live caller of `choose_scale`
(the falsecolor scale this action reports is exactly what that function computes from `rad_engine.
image.read_image`'s own `ImageStatistics`), but it does **not** call `clipped_fraction` to fill that
field in: `clipped_fraction` takes the raw per-pixel sequence, and `ImageStatistics` exposes only
the aggregate `minimum`/`maximum`/`mean` `pextrem -o`/`pvalue` already computed -- composing the two
here would mean re-reading every pixel a second way (`pvalue`'s full output) to build a Python list
Radiance itself never needed, duplicating physics CONTEXT.md's Layers table reserves for
`rad_engine`. `rad_mcp.render_tool` (Task 10) reached the identical conclusion for the identical
reason, in the first live caller of these three names; this is the second, and `scale.
clipped_fraction` is `None` on this action's wire result for the same reason `RenderScalePayload.
clipped_fraction` is. `FALSECOLOR_SCALE_CLIPPED` therefore stays allocated and unreached from this
module, same as it is from `run_render`.

## Why this tool takes no `project`

Every other read that opens a project-registered file (`bsdf(action="inspect", path=...)`,
`weather(action="describe")`) goes through a `LoadedProject` and inherits its `runtime`. `image` has
no project argument at all -- it inspects *any* HDR the caller names, rendered by this server or by
hand outside it entirely, which is the whole point of a tool that exists so a caller can check an
image before spending a glare run on it. That means `rad_mcp.paths.contain` against
`runtime.current().roots` is the *only* thing standing between `path` and the filesystem here, with
no manifest record to have validated it first -- see `image`'s own `contain(path, ...)` call below.

## `suitable_for_evalglare` is a read, not a refusal

`check_glare_input` returns every fault it finds (view type, view angles, resolution, pixel aspect)
in a fixed order; `image(action="inspect")` runs it and folds the result down to one boolean rather
than refusing the call. An image that fails those preconditions is not a bad *argument* to
`image` -- it is a fact about the picture, and the caller asked to be told that fact, not to be
stopped from learning it. `run_glare` (Task 6) is the tool that turns the same preconditions into a
refusal, because there the picture is an input to a computation this project would otherwise waste
time and Radiance licence-free CPU on.

## Advisories this task emits, and the ones it deliberately does not

`IMAGE_NO_VIEW_HEADER` (warn) fires when `read_image` found no `VIEW=` line at all -- fisheye
validity cannot be confirmed from a file that carries no view. `IMAGE_NOT_FISHEYE` (info) fires when
a view line *is* present but its type is neither `h` nor `a` -- a fact about a perfectly good
perspective render, not a defect. Both fire only from `action="inspect"`, unchanged by this task.
`FALSECOLOR_SCALE_LINEAR_WIDE_RANGE` (info) fires from `action="falsecolor"` when `choose_scale`'s
own `decades` exceeds `rad_core.views.WIDE_RANGE_DECADES` -- the same threshold and the same
computed number `rad_mcp.render_tool` reports under the identical code, worded without that
module's `scale_log` suggestion because this tool's published signature has no such argument to
suggest. **R329 (fix round 1): the message states whether `decades` is a real measurement or an
artefact of `choose_scale`'s own clamp** on a non-positive image minimum -- see
`_wide_range_advisory`'s own docstring; `decades` itself is unchanged either way (still `choose_
scale`'s own number), only the prose differs. All three are read off `docs/tool-surface.md`'s
advisory register rather than invented (Global Constraints: codes are allocated once, never
renamed).

`IMAGE_PIXEL_OVERFLOW`, `LDR_FORMAT_SUBSTITUTED` and `FALSECOLOR_SCALE_CLIPPED` are also allocated
to the `image`/rendering family and are **not** emitted here.

- `IMAGE_PIXEL_OVERFLOW` has no trigger `read_image`'s `ImageInfo` currently exposes (it is not one
  of `pextrem`/`pvalue`'s own stderr classifications -- `rad_engine.advisories.RULES` carries none
  of the four image codes -- and no fixture this task was given produces the condition); reported
  here as a gap rather than guessed at with an invented threshold, the same posture
  `rad_mcp.bsdf_tool` documents for its own three code gaps. Unaffected by this task.
- `LDR_FORMAT_SUBSTITUTED` names `ra_png` being absent from this install and a substitute format
  being used *in its place*. **`action="convert"` is built by this task and still never emits it**:
  `format` defaults to `"bmp"` (`rad_engine.postprocess.DEFAULT_LDR_FORMAT`, always present on this
  install) and a caller who names a format explicitly either gets it or is refused
  (`ARGUMENT_OUT_OF_RANGE`, `field="format"`) -- this action never silently writes a different
  format than the one asked for, so nothing is ever *substituted* here. `rad_mcp.render_tool`
  (Task 10) is this code's one live emitter, where the LDR step takes no `format` argument at all
  and always writes `DEFAULT_LDR_FORMAT`, reporting the code when `"png"` is unavailable.
- `FALSECOLOR_SCALE_CLIPPED` needs `rad_core.views.clipped_fraction`, which this module does not
  call -- see this docstring's R326 section above.

Every stderr line the four spawns behind `read_image` wrote (`getinfo`, `getinfo -d`, `pextrem -o`,
`pvalue`) is still surfaced: `ImageInfo.warnings` -- added because Task 2 originally shipped without
it and a review graded that Critical -- is classified through `rad_engine.advisories.classify` and
mapped onto the wire with `rad_mcp.advisories.to_wire_all`, the same two-step every other tool that
surfaces a `Result.warnings` list already takes. The stderr from every spawn behind
`action="falsecolor"`/`"tonemap"`/`"convert"` (`falsecolor`/`pcond`/the LDR converter itself) is
classified and surfaced the identical way.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Annotated, Any, Literal

from mcp_types import CallToolResult
from pydantic import BaseModel, ConfigDict, Field, RootModel, StrictInt, StrictFloat

from rad_engine import (
    RadianceError,
    RadianceNotFound,
    classify,
    discover,
    read_image,
)
from rad_engine.image import ImageInfo
from rad_engine.postprocess import (
    LDR_CONVERTERS,
    available_ldr_formats,
    build_falsecolor_argv,
    build_pcond_argv,
    build_ra_tiff_argv,
    spawn_postprocess,
    spawn_ra_tiff,
)
from rad_engine.env import RadianceEnv
from rad_engine.safe_io import NotARegularFileError
from rad_core.glare import EVALGLARE_VIEW_TYPES, ViewHeader, check_glare_input
from rad_core.views import WIDE_RANGE_DECADES, choose_scale

from rad_mcp.advisories import to_wire_all
from rad_mcp.envelope import (
    Advisory,
    RadResult,
    Refusal,
    emit,
    one_of_schema,
    refuse,
    refuse_missing,
)
from rad_mcp.paths import PathOutsideRoot, RootsNotConfigured, contain
from rad_mcp.runtime import current, refuse_engine_error

logger = logging.getLogger(__name__)

__all__ = [
    "DESTRUCTIVE_ACTIONS", "IMAGE_ACTIONS", "LIVE_IMAGE_ACTIONS", "ImageToolResult", "image",
]

#: R802: `falsecolor`, `tonemap` and `convert` each replace a regular file already at the caller's
#: `out`, whether or not this server wrote it; `inspect` only reads. `tests/test_protocol.py::
#: TestCheck07StaticToolList.test_annotations_are_stated_and_match_the_surface` checks `image`'s
#: published `destructiveHint`/`readOnlyHint` against this tuple, as it does for every other tool.
DESTRUCTIVE_ACTIONS: tuple[str, ...] = ("falsecolor", "tonemap", "convert")

#: The whole published action set (docs/tool-surface.md #15). Unchanged by this task -- see the
#: module docstring's ruling R258 section.
IMAGE_ACTIONS: tuple[str, ...] = ("inspect", "falsecolor", "tonemap", "convert", "compare")

#: The subset this task actually builds. `compare` is the one member of `IMAGE_ACTIONS` outside
#: this tuple, and `image_tool.image` refuses it with `ARGUMENT_OUT_OF_RANGE`, `field="action"`,
#: rather than accepting and mishandling it or hiding it from the published signature.
LIVE_IMAGE_ACTIONS: tuple[str, ...] = ("inspect", "falsecolor", "tonemap", "convert")

#: Every LDR format this tool's own `format` Literal publishes that `convert` can actually be asked
#: for -- ruling R307, the same five keys `rad_engine.postprocess.LDR_CONVERTERS` already carries.
IMAGE_CONVERT_FORMATS: tuple[str, ...] = tuple(LDR_CONVERTERS)

#: Bound, in seconds, on each spawn this module makes: the four behind `action="inspect"`
#: (`getinfo`, `getinfo -d`, `pextrem -o`, `pvalue`), and the one spawn each behind
#: `action="falsecolor"`/`"tonemap"`/`"convert"` (`falsecolor`/`pcond`/the LDR converter). Per
#: spawn, not for however many one call makes.
#:
#: **Not sourced -- a judgement call, stated plainly, and it exists because there was none at all.**
#: `read_image(...)` was called with no `timeout=`, so every one of the four was unbounded and one
#: `image(action="inspect")` on a large picture held an `anyio` worker slot for as long as Radiance
#: wanted it; measured here, one `read_image` over a 3000x3000 synthetic HDR took **7.85 s** (Phase 8
#: whole-branch review, finding I4, which measured 12.43 s on its own synthetic -- the figure moves
#: with the picture's content, since `pvalue` streams every pixel). Every other spawn this module
#: makes, this task's three included, is bounded by the same constant: none of `falsecolor`/`pcond`/
#: the LDR converters is measured to cost more than a `pvalue` scan of the same picture, since each
#: reads the image once and writes one output once, doing less work per pixel than `pvalue -d -h -H
#: -o` streaming every channel as text.
#:
#: 60 s is well above that measurement and above any picture a caller plausibly inspects, while
#: still failing rather than hanging on a pathological one -- the same shape, the same value and the
#: same reasoning as `rad_mcp.materials_tool._USAGE_TIMEOUT_S` and
#: `rad_mcp.window_group_tool._OBSTRUCTION_TIMEOUT_S`, the two sibling module-level bounds on a
#: read-shaped spawn. `image` takes no timeout argument of its own: unlike `run_glare`, it has no
#: `inline_timeout_s` on the wire, and minting one would be a signature change
#: docs/tool-surface.md #15 does not publish.
_IMAGE_SPAWN_TIMEOUT_S = 60.0


# --- nested shapes ------------------------------------------------------------------------------


class _Nested(BaseModel):
    model_config = ConfigDict(extra="forbid")


# R810 moved this from the published text: the line is read by `rad_engine.image.parse_view_line`,
# and the null-not-default rule is `rad_core.glare.ViewHeader`'s own, carried through unchanged.
class ImageViewPayload(_Nested):
    """The image header's first `VIEW=` line.

    `vh`, `vv`, `position` and `direction` are `null`, never a substituted default, when the line
    does not state `-vh`, `-vv`, `-vp` or `-vd`, so "not stated" is never read as a value. `type`
    is the `-vt` letter, `""` when the line has none.
    """

    type: str
    vh: float | None = None
    vv: float | None = None
    position: list[float] | None = None
    direction: list[float] | None = None


# The precision note dates from the Phase 8 whole-branch review; the final review of Phase 18
# Branch B (FW-3) corrected it for both scopes, and that history left the published description
# under R809 (item 38). R810 moved out the rest: the fields are `rad_engine.image.ImageStatistics`,
# `min`/`max` renamed to match §15; the `image_circle` scope is R764's; the worked example
# (`tests/fixtures/hdr/high_fish_view.hdr`: `pextrem` prints the maximum as `1.22e+01` per channel,
# `rad_core.photometry.to_lux` gives `2183.8`, while docs/tool-surface.md's Verified engine fact 26,
# a `pvalue` scan, records `2187.38` -- a rounding of the input, not of the arithmetic); and why
# the extrema are not recomputed from `pvalue` (it would disagree with `pextrem` for the same file,
# and no number this module publishes is computed here; documented in docs/tool-surface.md §15).
# Task 7 fix round 1 corrected this task's first line, "Luminance statistics of the image, in
# cd/m^2": `rad_engine.image` applies `rad_core.photometry.to_lux` (179 x weighted RGB) to every
# picture, so an `rpict -i` picture (`run_render(quantity="illuminance")`) gives lux, and
# `read_image` parses only `VIEW=`, `EXPOSURE=` and the resolution, nothing that records `-i`.
class ImageStatisticsPayload(_Nested):
    """Photometric statistics of the image, from its original pixel values (`exposure` undone):
    179 x the weighted RGB of each pixel, which is luminance in cd/m^2 for an ordinary (radiance)
    picture and illuminance in lux for an irradiance picture rendered with `-i` (for example
    `run_render(quantity="illuminance")`). The tool does not read which one the image is; the
    caller knows it from how the image was rendered.

    `scope` says which pixels they cover: `"whole_image"`, every pixel; `"image_circle"`, only the
    pixels wholly inside a 180-degree `h` or `a` fisheye's image circle (a shifted or lifted view is
    reported whole). `image(action="falsecolor")` still scales over the whole image, because it
    colours every pixel.

    Precision depends on `scope`, and it is the Radiance tools': with `"whole_image"`, `min` and
    `max` are `pextrem`'s, printed to three significant figures before conversion, so `max` can sit
    measurably off the true brightest pixel; with `"image_circle"`, `min`, `max` and `mean` carry
    `pvalue`'s four significant figures. `mean`, in either scope, is summed over `pvalue`'s
    four-figure pixels and not rounded further. Compare `max` against a threshold at that precision.
    """

    min: float
    max: float
    mean: float
    scope: Literal["image_circle", "whole_image"]


# Why `clipped_fraction` is not composed here is the module docstring's R326 section; the pointer to
# it left the published description under R809 (item 38). R810 moved out the rest: this is
# `rad_core.views.FalsecolorScale`, the same shape `render_tool.RenderScalePayload` publishes, and
# composing `rad_core.views.clipped_fraction` would be a second, Python-side pass over every pixel
# `pvalue` already streamed, which this module's contract reserves for `rad_engine`. The `decades`
# floor is `choose_scale`'s clamp on a non-positive minimum (R329).
class ImageScalePayload(_Nested):
    """The falsecolor scale `image(action="falsecolor")` used.

    `maximum` is the `falsecolor -s` value: the whole image's own maximum (`chosen: true`) when
    `scale_max` was not given, else `scale_max` (`chosen: false`). `decades` is log10 of the
    image's maximum over its minimum, whatever `scale_max` was; when the minimum is zero or less it
    is taken against a floor of a millionth of the maximum (6 decades), not a measured range, and
    `FALSECOLOR_SCALE_LINEAR_WIDE_RANGE` says so. It is `0` for an all-black image.

    `clipped_fraction` is always `null`: the fraction of pixels above the scale is not measured, so
    `null` does not mean no pixel was clipped.
    """

    maximum: float
    chosen: bool
    decades: float
    clipped_fraction: float | None


# --- the result branches -------------------------------------------------------------------------


class ImageBase(RadResult):
    """What every `image`-shaped answer carries. Also the union schema's shared base."""


# R810 moved this from the published text: `action="inspect"` was built at Phase 8 (ruling R258).
class ImageInspected(ImageBase):
    """`image(action="inspect")`: what the HDR image at `path` says about itself. It writes nothing.

    `resolution` is `[width, height]` in pixels. `view` is `null` when the header has no `VIEW=`
    line, and `IMAGE_NO_VIEW_HEADER` says so. `exposure` is the product of the header's `EXPOSURE=`
    values, `1.0` when it has none. `is_fisheye` says only that the view type is `h` or `a`;
    `suitable_for_evalglare` says whether `run_glare` would accept the image (fisheye type,
    180-degree angles, at least 800 x 800 pixels, square pixels), so a fisheye can still be
    unsuitable. `out` is always `null`.
    """

    action: Literal["inspect"]
    path: str
    resolution: list[int]
    view: ImageViewPayload | None = None
    statistics: ImageStatisticsPayload
    exposure: float
    is_fisheye: bool
    suitable_for_evalglare: bool
    out: str | None = None


class ImageFalsecolored(ImageBase):
    """`image(action="falsecolor")`: `falsecolor` run over `path`, written to `out`, with the
    `scale` it used."""

    action: Literal["falsecolor"]
    path: str
    out: str
    scale: ImageScalePayload


class ImageTonemapped(ImageBase):
    """`image(action="tonemap")`: `pcond -h+` (human visual response) over `path`, written to
    `out`."""

    action: Literal["tonemap"]
    path: str
    out: str


# R810 moved this from the published text: the converter is one of
# `rad_engine.postprocess.LDR_CONVERTERS`.
class ImageConverted(ImageBase):
    """`image(action="convert")`: `path` written to `out` as an LDR image in `format`. A `format`
    this install cannot produce is refused, never substituted."""

    action: Literal["convert"]
    path: str
    out: str
    format: str


ImagePayload = ImageInspected | ImageFalsecolored | ImageTonemapped | ImageConverted | Refusal


class ImageToolResult(RootModel[ImagePayload]):
    """`image`'s result: one of the four action shapes, or a refusal."""

    @classmethod
    def model_json_schema(cls, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return one_of_schema(super().model_json_schema(*args, **kwargs), ImageBase)


# --- argument checks ------------------------------------------------------------------------------


def _refuse_argument(message: str, *, field: str) -> CallToolResult:
    """`ARGUMENT_OUT_OF_RANGE` with the argument's own name in `field` -- ruling R14's code for an
    argument refused on its face."""
    return refuse(
        message,
        [Advisory(code="ARGUMENT_OUT_OF_RANGE", severity="error", field=field, message=message)],
    )


def _refuse_unbuilt_action(action: str) -> CallToolResult:
    """R258/R304: `compare` is the one member of `IMAGE_ACTIONS` outside `LIVE_IMAGE_ACTIONS`.

    The message names the action as not built, with no pipeline, and names no phase:
    an earlier wording deferred it to one, which was false (rulings R284/R300/R303/R304, R806).
    Three tests in `tests/test_image_tool.py` pin the resulting substring `'compare' is not built` against a
    live call (`TestCompareIsTheOnlyUnbuiltAction`, `TestCompareIsStillUnbuiltAndStillUnowned`,
    `TestTheWireDocstringNeverPutsCompareInAPhase`).
    """
    live = ", ".join(repr(name) for name in LIVE_IMAGE_ACTIONS)
    message = (
        f"{action!r} is not built: image(action={action!r}) is specified "
        f"(docs/tool-surface.md #15) but has no pipeline. The live actions "
        f"are {live}."
    )
    return _refuse_argument(message, field="action")


def _refuse_bad_scale_max(scale_max: float) -> CallToolResult:
    message = (
        f"scale_max={scale_max!r} must be greater than 0. A falsecolor scale of zero or less has "
        "no useful meaning (rad_core.views.choose_scale). Omit scale_max to compute it from the "
        "image's own maximum."
    )
    return _refuse_argument(message, field="scale_max")


def _refuse_unavailable_format(fmt: str, env: RadianceEnv) -> CallToolResult:
    """Check 9.4: requesting a format this install cannot produce is refused, naming the formats
    that are actually available -- not silently substituted (`LDR_FORMAT_SUBSTITUTED` is a
    different action's story; see the module docstring)."""
    available = available_ldr_formats(env)
    named = ", ".join(repr(name) for name in available)
    message = (
        f"format={fmt!r} needs {LDR_CONVERTERS[fmt]!r}, which is absent from this Radiance install "
        f"(measured). Available formats on this install: {named}."
    )
    return _refuse_argument(message, field="format")


def _contain_written_path(path: Path, *, roots: tuple[Path, ...]) -> Path:
    """Refuse `out` before anything is written if it escapes `roots` -- ruling R48's primitive, the
    same guard `rad_mcp.glare_tool`/`rad_mcp.render_tool` apply to their own writes (each tool keeps
    its own copy, ruling R30's convention, since each write's own message differs).
    Raises `PathOutsideRoot`, shaped into a refusal by `rad_mcp.runtime.refuse_engine_error`.

    **`rad_mcp.paths.contain`, not `resolved_parent_within` (whole-branch fix wave, B2).** `out` is
    a *caller-named* path here, which makes the parent-only check the weakest of the three: a
    caller could name a symlink inside a root whose target is outside every root, and this function
    resolved only the parent and answered yes. Measured on exactly that shape,
    `resolved_parent_within` returns `True` while `contain` raises `PathOutsideRoot` -- the same
    escape `rad_mcp.export_tool`'s module docstring records for Task 11, which is why that tool
    already uses `contain` for its own `out`. `contain` resolves the whole candidate, so the leaf
    is judged like any other component.

    **The original `path` is returned, not `contain`'s resolved one**: `contain` supplies the
    verdict, and the path this tool writes to and publishes is unchanged.
    """
    try:
        contain(path, roots)
    except PathOutsideRoot as exc:
        raise PathOutsideRoot(
            f"the output image would be written to {path}, which resolves outside every "
            "allowed root. Nothing has been written."
        ) from exc
    return path


def _same_file(path: Path, out: Path) -> bool:
    """Is `out` the same file as `path` -- Critical 1, Phase 9 Task 11 fix round 1.

    Compares `st_dev`/`st_ino` on the resolved pair when both exist, so a hardlink or a
    differently-spelled path to the identical inode is caught even though the two strings differ
    -- a size check or a string comparison alone would miss both. Falls back to `os.path.realpath`
    when `out` does not exist yet (the ordinary case for every write this tool makes: `out` is a
    file this call is about to create): there is no inode yet to compare, but two paths whose
    fully resolved parent chains and final component are textually identical name the same
    eventual location. `path` failing to `stat` (a race, or a caller passing a `path` that will
    fail for its own reason downstream) is not this function's concern -- it returns `False` and
    lets the normal read-path error handling report why `path` could not be opened.
    """
    try:
        path_stat = os.stat(path)
    except OSError:
        return False
    try:
        out_stat = os.stat(out)
    except OSError:
        return os.path.realpath(str(path)) == os.path.realpath(str(out))
    return (path_stat.st_dev, path_stat.st_ino) == (out_stat.st_dev, out_stat.st_ino)


def _wide_range_advisory(decades: float, *, clamped: bool) -> Advisory:
    """R329 (Phase 9 Task 11 fix round 1): the message must say whether `decades` is a real
    measurement of the image or an artefact of `choose_scale`'s own floor.

    `choose_scale` clamps its denominator to `stats.maximum * 1e-6` whenever `stats.minimum` is
    not positive -- the ordinary case for a real HDR render, which has zero-valued pixels wherever
    a ray hit nothing (that function's own docstring). The clamp fires, `decades` is exactly
    `log10(1e6) == 6.0` regardless of the image's actual content, and the code fires alongside it
    (measured: `high_fish_view.hdr` and `falsecolor_fishview.hdr`, both with `pextrem` minimum
    `0.00e+00`, both publish `decades: 6.0`). Reporting *"this image spans 6 decades of dynamic
    range"* in that case is not a measurement, and a caller (or a model) reading it as one would be
    reasoning from a fabricated number. `clamped` says which sentence to build; the advisory
    **code** is unchanged either way -- both are still "a linear scale was used and might not be
    the right call," just for different reasons.
    """
    if clamped:
        message = (
            f"this image contains pixels at or below zero luminance, so the falsecolor scale's "
            f"own {decades:.2g}-decade figure is measured against a floor "
            "(rad_core.views.choose_scale's clamp on a non-positive minimum), not against this "
            "image's own minimum -- it is not a measurement of the image's true dynamic range. A "
            "linear falsecolor scale was used regardless of this figure."
        )
    else:
        message = (
            f"this image spans {decades:.2g} decades of dynamic range, above the "
            f"{WIDE_RANGE_DECADES:g}-decade threshold, and a linear falsecolor scale was used. A "
            "linear scale of a sunlit room is a picture of the sun patch with everything else at "
            "the bottom of the scale."
        )
    return Advisory(code="FALSECOLOR_SCALE_LINEAR_WIDE_RANGE", severity="info", message=message)


# --- the tool ---------------------------------------------------------------------------------


def image(
    action: Annotated[
        Literal["inspect", "falsecolor", "tonemap", "convert", "compare"],
        Field(
            description=(
                "which operation: \"inspect\", \"falsecolor\", \"tonemap\", \"convert\" or "
                "'compare'. The tool description says what each one does."
            )
        ),
    ],
    path: Annotated[
        str | None,
        Field(
            description=(
                "absolute path to an HDR image. REQUIRED on every live action. The file is read, "
                "never modified, and never registered in any project -- this tool takes no "
                "`project` argument at all."
            )
        ),
    ] = None,
    reference: Annotated[
        str | None,
        Field(description="unused; published for \"compare\", which is not built."),
    ] = None,
    quantity: Annotated[
        Literal["luminance", "illuminance"],
        Field(
            description=(
                "used by \"falsecolor\" only -- `\"luminance\"` (default) reports pixel extrema in the "
                "legend (`falsecolor -e`); `\"illuminance\"` omits that flag, matching an image the "
                "caller already rendered with `-i`. Unused otherwise."
            )
        ),
    ] = "luminance",
    scale_max: Annotated[
        StrictFloat | None,
        Field(
            description=(
                "used by \"falsecolor\" only. `None` (default) computes the `-s` value from the "
                "image's own maximum (`rad_core.views.choose_scale`) and reports the chosen value; "
                "a caller-supplied value must be positive. Unused otherwise."
            )
        ),
    ] = None,
    levels: Annotated[
        StrictInt,
        Field(description="used by \"falsecolor\" only -- `falsecolor -n`, the number of colour bands."),
    ] = 10,
    contour: Annotated[
        bool,
        Field(description="used by \"falsecolor\" only -- `falsecolor -cl`."),
    ] = False,
    legend_label: Annotated[
        str | None,
        Field(description="used by \"falsecolor\" only -- `falsecolor -l`."),
    ] = None,
    format: Annotated[
        Literal["bmp", "tiff", "tiff_grey", "ppm", "png"],
        Field(
            description=(
                "used by \"convert\" only. The default is `\"bmp\"`; a format this install "
                "cannot produce, `\"bmp\"` included (`available_ldr_formats`, measured at call "
                "time), is refused, naming the formats this install can actually produce, rather than being "
                "silently substituted. Unused otherwise."
            )
        ),
    ] = "bmp",
    out: Annotated[
        str | None,
        Field(
            description=(
                "absolute path to write. REQUIRED on \"falsecolor\", \"tonemap\" and \"convert\"; "
                "unused on \"inspect\" and \"compare\"."
            )
        ),
    ] = None,
) -> ImageToolResult:
    """Inspect, falsecolor, tone-map or convert an HDR image -- no re-rendering.

    Use it on an HDR image already on disk, to check it or post-process it without re-rendering; to
    render one, use `run_render`.

    DESTRUCTIVE ACTIONS: `falsecolor`, `tonemap` and `convert` replace a regular file already at
    `out`, whether or not this server wrote it. A symlink, FIFO, device or directory at `out`, or
    an `out` that is the input image, is refused.

    Four of the five published actions are live: "inspect", "falsecolor", "tonemap" and
    "convert". 'compare' is not
    built: calling it refuses with `ARGUMENT_OUT_OF_RANGE` naming the live actions rather than
    doing nothing or doing the wrong thing silently.

    "inspect" reads `path` and reports its resolution, `VIEW=` header (if any), exposure and
    luminance statistics, and whether the image is a valid fisheye view for a DGP calculation -- the
    same check `run_glare` uses, published here so a caller can check a hand-rendered image before
    spending a glare run on it. "falsecolor" pipes `path` through `falsecolor`, writing `out`.
    "tonemap" runs `pcond -h+` (human visual response) over `path`, writing `out`. "convert" writes
    `path` as an LDR `format` to `out`. 'compare' is not built.
    """
    if action not in LIVE_IMAGE_ACTIONS:
        return _refuse_unbuilt_action(action)

    call = f"image(action={action!r})"
    required: dict[str, Any] = {"path": path}
    if action != "inspect":
        required["out"] = out
    if (refusal := refuse_missing(call, **required)) is not None:
        return refusal
    assert path is not None

    if action == "falsecolor" and scale_max is not None and scale_max <= 0.0:
        return _refuse_bad_scale_max(scale_max)
    if action == "convert" and format not in IMAGE_CONVERT_FORMATS:
        # Defensive: `format`'s own `Literal` already refuses anything outside `LDR_CONVERTERS`'
        # keys at the schema boundary, before this function body ever runs.
        return _refuse_argument(f"format={format!r} is not a format this tool publishes.", field="format")

    roots = current().roots
    try:
        resolved = contain(path, roots)
    except (PathOutsideRoot, RootsNotConfigured) as exc:
        logger.info("image(action=%s) refused: %s", action, exc)
        return refuse_engine_error(exc)

    out_resolved: Path | None = None
    if action != "inspect":
        assert out is not None
        out_candidate = Path(out)
        if not out_candidate.is_absolute():
            return _refuse_argument(f"out must be an absolute path; got {out!r}.", field="out")
        try:
            out_resolved = _contain_written_path(out_candidate, roots=roots)
        except (PathOutsideRoot, RootsNotConfigured) as exc:
            logger.info("image(action=%s) refused: %s", action, exc)
            return refuse_engine_error(exc)
        if _same_file(resolved, out_resolved):
            # Critical 1 (Phase 9 Task 11 fix round 1). "Convert this in place" is a natural call
            # for a model to make, and every one of the three write actions pipes `path` in as the
            # spawn's own input. At the time this refusal was written, `spawn_postprocess`/
            # `spawn_ra_tiff` opened `out` "wb" (truncating) before or during the read, so
            # `out == path` truncated the caller's own input while it was still being read from,
            # and a subsequent failure then unlinked it entirely. **Ruling R333 (fix round 3)
            # changed that mechanism for both functions**: each now writes to a fresh temp file
            # and `os.replace`s it onto `out_path` only after a successful spawn, so a failure no
            # longer touches `out_path` at all -- `out == path` included. The refusal still stands
            # for the case that remains: on a *successful* conversion, `os.replace` would still
            # overwrite the caller's own input with the converted result computed from it, which is
            # exactly what naming the same file for both arguments would otherwise do, silently.
            # Checked by inode when both exist (`os.stat` `st_dev`/`st_ino` -- catches a hardlink or
            # a differently-spelled alias to the identical file, not merely identical strings) and
            # by `os.path.realpath` when `out` does not exist yet, which is the ordinary case for
            # every write this tool makes.
            message = (
                f"out={out!r} names the same file as path={path!r}. A successful {action} moves "
                "its finished result onto out, so the input would be overwritten by the result "
                "computed from it -- choose a different out path."
            )
            return _refuse_argument(message, field="out")

    try:
        env = discover()
        if action == "convert" and format not in available_ldr_formats(env):
            # Check 9.4: known from the argument and the install alone.
            return _refuse_unavailable_format(format, env)
        if action == "inspect":
            info = read_image(env, resolved, cwd=resolved.parent, timeout=_IMAGE_SPAWN_TIMEOUT_S)
            return emit(_inspected(resolved, info))
        assert out_resolved is not None
        if action == "falsecolor":
            return emit(
                _falsecolor(
                    env, resolved, out_resolved,
                    quantity=quantity, scale_max=scale_max, levels=levels,
                    contour=contour, legend_label=legend_label,
                )
            )
        if action == "tonemap":
            return emit(_tonemap(env, resolved, out_resolved))
        assert action == "convert"
        return emit(_convert(env, resolved, out_resolved, fmt=format))
    except RadianceNotFound as exc:
        logger.info("image(action=%s) refused: %s", action, exc)
        return refuse(
            str(exc),
            [Advisory(code="RADIANCE_NOT_FOUND", severity="error", message=str(exc))],
        )
    except NotARegularFileError as exc:
        # `path` is a FIFO or a device, read via `open_regular_file(..., "rb")` inside `read_image`/
        # `spawn_postprocess` -- **not** a symlink at its final component: measured directly (fix
        # round 2), `open_regular_file` raises this exception only once its own `open()` has
        # already succeeded and its `S_ISREG` check then fails, and `O_NOFOLLOW` makes a symlink's
        # `open()` fail first, as a plain `OSError` the generic handler below catches instead --
        # this remains true for `path`'s own read-only case, unaffected by ruling R333.
        #
        # `out` is a second, now-uniform case (ruling R333, fix round 3): every write this module
        # makes -- `falsecolor`/`tonemap`/`convert` in every format, `tiff`/`tiff_grey` included --
        # goes through the shared `rad_engine.postprocess._refuse_existing_non_regular_destination`
        # (a read-only `os.lstat`, never an `open()`) before anything is spawned, and raises this
        # exception uniformly for a symlink, FIFO, device *or directory* already at `out`. Before
        # R333 this was two different shapes -- `spawn_ra_tiff`'s own `lstat` check already raised
        # it uniformly for `tiff`/`tiff_grey`, while `spawn_postprocess`'s direct `open_regular_file
        # (out_path, "wb")` raised a plain `OSError` for a symlink or a FIFO on the other formats,
        # and this exception only for a device -- a mix this comment used to describe as a live
        # fact about `bmp`/`ppm`/`png`/`falsecolor`/`tonemap`; measured again after R333, it no
        # longer holds for any of them. Either way `out` reaches this same handler, still reported
        # as `field="path"` below, matching this handler's original scope rather than distinguishing
        # the two arguments. Ruling R58's `PROJECT_PATH_NOT_A_REGULAR_FILE` is the code allocated
        # for exactly this condition regardless of whether the path names project state.
        # `field="path"` because that is the argument this call most commonly took, unlike the
        # project-scoped callers that carry `field="project"` for the same code.
        logger.info("image(action=%s) refused: %s", action, exc)
        message = (
            f"{exc} Nothing about this argument is malformed -- remove or replace whatever is at "
            "that path."
        )
        return refuse(
            message,
            [
                Advisory(
                    code="PROJECT_PATH_NOT_A_REGULAR_FILE",
                    severity="error",
                    field="path",
                    message=message,
                )
            ],
        )
    except ValueError as exc:
        # Not a well-formed Radiance picture (`_read_header_block`'s bound, `parse_resolution`'s
        # by-name match, or `pextrem`'s expected two-line shape), or `rad_core.views.choose_scale`'s
        # own refusal for a non-positive falsecolor scale (an all-black image with no explicit
        # `scale_max` -- `_refuse_bad_scale_max` above only catches the caller-supplied case) --
        # discovered by opening and reading the file, not by inspecting the argument's own shape,
        # so this is reported uncoded rather than reused onto `ARGUMENT_OUT_OF_RANGE` (ruling R58's
        # own distinction between the two, applied the same way `rad_mcp.weather_tool` reports
        # `EPWError` uncoded).
        logger.info("image(action=%s) refused: %s", action, exc)
        return refuse(f"{path!r} could not be read as a Radiance picture: {exc}")
    except RadianceError as exc:
        logger.info("image(action=%s) failed: %s", action, exc)
        return refuse(str(exc))
    except OSError as exc:
        logger.warning("image(action=%s) failed on the filesystem: %s", action, exc)
        return refuse(
            f"{call} could not complete: {exc}. Nothing about the arguments is wrong -- the "
            "filesystem refused the operation, so this is an environment problem (permissions, a "
            "read-only mount, or a file held open elsewhere)."
        )


def _inspected(path: Path, info: ImageInfo) -> ImageInspected:
    """`ImageInfo` -> the wire shape, plus the `rad_core.glare` fisheye/suitability read.

    `is_fisheye` and `suitable_for_evalglare` are two different questions answered from the same
    two facts (`info.view`, `info.resolution`): the first is "does the header say this is a
    hemispherical/angular fisheye view", the second is "would `run_glare` accept this image at
    all" -- `check_glare_input` also checks the view angles, the resolution floor and the pixel
    aspect ratio, none of which `is_fisheye` looks at. A 512x512 `-vth` image is a fisheye
    (`is_fisheye=True`) and is still not suitable for evalglare, because 512 is below the
    800x800 floor (`rad_core.glare.MIN_RESOLUTION_PX`) -- the two fields deliberately disagree on
    that fixture.
    """
    view = info.view
    is_fisheye = view is not None and view.view_type in EVALGLARE_VIEW_TYPES
    suitable_for_evalglare = not check_glare_input(view, info.resolution)

    advisories = _view_advisories(view)
    advisories.extend(to_wire_all(classify(info.warnings)))

    circle = info.in_view_statistics
    stats = circle if circle is not None else info.statistics

    return ImageInspected(
        action="inspect",
        path=str(path),
        resolution=list(info.resolution),
        view=_view_payload(view),
        statistics=ImageStatisticsPayload(
            min=stats.minimum, max=stats.maximum, mean=stats.mean,
            scope="image_circle" if circle is not None else "whole_image",
        ),
        exposure=info.exposure,
        is_fisheye=is_fisheye,
        suitable_for_evalglare=suitable_for_evalglare,
        out=None,
        advisories=advisories or None,
    )


def _view_payload(view: ViewHeader | None) -> ImageViewPayload | None:
    if view is None:
        return None
    return ImageViewPayload(
        type=view.view_type,
        vh=view.vh_deg,
        vv=view.vv_deg,
        position=list(view.position) if view.position is not None else None,
        direction=list(view.direction) if view.direction is not None else None,
    )


def _view_advisories(view: ViewHeader | None) -> list[Advisory]:
    """`IMAGE_NO_VIEW_HEADER` / `IMAGE_NOT_FISHEYE` -- see the module docstring for both codes and
    the two this function deliberately never emits."""
    if view is None:
        message = (
            "This image carries no VIEW= header, so fisheye validity cannot be confirmed from the "
            "file alone."
        )
        return [Advisory(code="IMAGE_NO_VIEW_HEADER", severity="warn", field="view", message=message)]
    if view.view_type not in EVALGLARE_VIEW_TYPES:
        message = (
            f"View type {view.view_type!r} is not a fisheye view (hemispherical 'h' or angular "
            "'a')."
        )
        return [Advisory(code="IMAGE_NOT_FISHEYE", severity="info", field="view", message=message)]
    return []


# --- the three actions this task builds --------------------------------------------------------


def _falsecolor(
    env: RadianceEnv,
    path: Path,
    out: Path,
    *,
    quantity: str,
    scale_max: float | None,
    levels: int,
    contour: bool,
    legend_label: str | None,
) -> ImageFalsecolored:
    """`action="falsecolor"` -- `read_image` for the scale, then `falsecolor` piped over `path`.

    `choose_scale` can raise `ValueError` for a non-positive computed maximum (an all-black image
    with no explicit `scale_max` -- `image`'s own caller-supplied check catches the other case
    before this runs); that propagates to `image`'s own `except ValueError` handler, uncoded, the
    same way a malformed picture does. See the module docstring's R326 section for why `scale.
    clipped_fraction` is always `None` here.
    """
    info = read_image(env, path, cwd=path.parent, timeout=_IMAGE_SPAWN_TIMEOUT_S)
    scale = choose_scale(info.statistics, scale_max=scale_max)

    argv = build_falsecolor_argv(
        quantity=quantity, scale_max=scale.maximum, levels=levels,
        legend_label=legend_label, contour=contour,
    )
    written, warnings = spawn_postprocess(
        env, argv, stdin_path=path, out_path=out, cwd=path.parent,
        timeout=_IMAGE_SPAWN_TIMEOUT_S,
    )

    advisories = to_wire_all(classify(list(info.warnings) + list(warnings)))
    if scale.decades > WIDE_RANGE_DECADES:
        # R329: the same non-positive-minimum condition `choose_scale` itself clamps on -- see
        # `_wide_range_advisory`'s own docstring for why this matters and what was measured.
        clamped = info.statistics.minimum <= 0.0 and info.statistics.maximum > 0.0
        advisories.append(_wide_range_advisory(scale.decades, clamped=clamped))

    return ImageFalsecolored(
        action="falsecolor",
        path=str(path),
        out=str(written),
        scale=ImageScalePayload(
            maximum=scale.maximum, chosen=scale.chosen, decades=scale.decades,
            clipped_fraction=scale.clipped_fraction,
        ),
        advisories=advisories or None,
    )


def _tonemap(env: RadianceEnv, path: Path, out: Path) -> ImageTonemapped:
    """`action="tonemap"` -- `pcond -h+` over `path`, via `spawn_postprocess`.

    Measured (module docstring): `pcond` reads its input only as a positional filename, never from
    stdin, and writes to stdout when given no `outpic` -- so the argv carries `path` as `pcond`'s
    own positional input and `stdin_path=None` (pcond never reads it; `spawn_postprocess` sends a
    declared, immediately-closed empty write), while `out_path` captures pcond's stdout exactly the
    way it captures `falsecolor`'s.
    """
    argv = [*build_pcond_argv(human=True), str(path)]
    written, warnings = spawn_postprocess(
        env, argv, stdin_path=None, out_path=out, cwd=path.parent,
        timeout=_IMAGE_SPAWN_TIMEOUT_S,
    )
    advisories = to_wire_all(classify(list(warnings)))
    return ImageTonemapped(
        action="tonemap", path=str(path), out=str(written), advisories=advisories or None,
    )


def _convert(env: RadianceEnv, path: Path, out: Path, *, fmt: str) -> ImageConverted:
    """`action="convert"` -- one of `LDR_CONVERTERS[fmt]` over `path`, writing `out` as `fmt`.

    `ra_bmp`/`ra_ppm` (and `ra_png`, on an install that carries it) read stdin and write stdout with
    no positional filenames, the `spawn_postprocess` shape. `ra_tiff` (`fmt` in `{"tiff",
    "tiff_grey"}`) does not: measured (`rad_engine.postprocess`'s own module docstring), it cannot
    write to stdout at all, and needs its own two positional filenames -- `rad_engine.postprocess.
    spawn_ra_tiff` owns that spawn and its own write guard (ruling R330, Phase 9 Task 11 fix
    round 1: this used to be a local `_spawn_ra_tiff` here, calling `rad_engine.run.run` directly
    with `out` as `ra_tiff`'s own positional argument -- a Radiance program name, flag and spawn
    shape in the plumbing layer, and the reason `ra_tiff`'s write lost its symlink guard. Moved to
    the engine layer, where the guard could be restored; see that function's own docstring for the
    measured escape and the fix).
    """
    binary = LDR_CONVERTERS[fmt]
    if binary == "ra_tiff":
        argv = build_ra_tiff_argv(greyscale=(fmt == "tiff_grey"))
        written, warnings = spawn_ra_tiff(
            env, argv, source=path, out_path=out, cwd=path.parent,
            timeout=_IMAGE_SPAWN_TIMEOUT_S,
        )
    else:
        written, warnings = spawn_postprocess(
            env, [binary], stdin_path=path, out_path=out, cwd=path.parent,
            timeout=_IMAGE_SPAWN_TIMEOUT_S,
        )
    advisories = to_wire_all(classify(list(warnings)))
    return ImageConverted(
        action="convert", path=str(path), out=str(written), format=fmt,
        advisories=advisories or None,
    )
