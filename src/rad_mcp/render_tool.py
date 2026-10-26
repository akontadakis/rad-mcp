"""The `run_render` tool -- docs/tool-surface.md#12-run_render.

Photorealistic HDR and falsecolor luminance/illuminance maps. Thin, in the sense CONTEXT.md's
Layers table means it: this module validates arguments, resolves and contains paths, picks the
engine calls and shapes the envelope. **No number reported here is computed here.**
`rad_core.views` validates view geometry (`View`, `vt_flag`, `ViewTypeError`) and computes the
falsecolor scale from an image's own statistics (`choose_scale`); `rad_engine.render` builds the
`rpict` argv and spawns it (`build_rpict_argv`, `spawn_render`, the `pfilt` boundary's structural
half); `rad_engine.postprocess` builds and spawns `falsecolor`/`pfilt`/the LDR converters. This
module composes the four.

## What this task builds, and what it refuses as unbuilt (ruling R258's pattern, one axis over)

The published `mode` is a four-member `Literal` (`hdr`/`falsecolor`/`contour`/`tonemapped`), and
this task builds exactly two of them -- `LIVE_RENDER_MODES`. `contour` and `tonemapped` are
refused with `ARGUMENT_OUT_OF_RANGE`, `field="mode"`, naming the two that are live, rather than
silently doing nothing or mishandling a mode this task never designed a pipeline for -- the same
posture `rad_mcp.image_tool` documents for `IMAGE_ACTIONS` vs. `LIVE_IMAGE_ACTIONS`. `mode` stays
in the published signature at its full four-member domain; shrinking it would be a wire-contract
change this task's file list does not authorise (ruling R302, the same reasoning that keeps
`use_illum` published while permanently refused below).

## `mode="falsecolor"` with `quantity="illuminance"` is refused, unconditionally, this task

docs/plan.md check 9.2's second sentence: *"`quantity='illuminance'` without an `-i` render is
refused -- the two produce numerically different images that look the same."* This task's own
falsecolor pipeline only builds the single-image shape `tests/fixtures/scripts/falsecolor.sh`
validates for a **luminance** picture (`falsecolor -n 10 -s 500 -e`, piped straight from a plain
`rpict` render). It also ships a daylight-factor variant (`-m 1.79 -l "DF %"`), and **ruling R390 is
what gives that variant a wire caller again**: `run_render(mode="falsecolor",
quantity="daylight_factor")` with **no** `sky` is an accepted call shape, and `_dispatch`'s
`mode == "falsecolor"` branch passes `quantity` straight into `build_falsecolor_argv`, whose
`if quantity == "daylight_factor": argv += ["-m", _DAYLIGHT_FACTOR_MULTIPLIER]` then fires. Between
R388/R389 and R390 that branch had **no wire caller at all**: those two closed every daylight-factor
call shape on this tool, and the builder's only other call site is
`rad_mcp.image_tool`, whose own `quantity` is typed
`Literal["luminance", "illuminance"]` (`image_tool.image`) and cannot name it. `mode="hdr"` with
`quantity="daylight_factor"` stays refused (R388), and a *caller-supplied* sky stays refused (R389);
what R390 added is the omitted-sky route, on which the CIE reference overcast sky is forced and
`DF_SKY_FORCED` reports it.
The two-render contour-overlay shape the same check's other validated line
describes -- `falsecolor -i illum.hdr -p lum.hdr -cl -s 2000 -n 10 -l Lux`, an illuminance render
overlaid as contour lines on a *separately rendered* luminance backdrop -- needs two renders of the
same view, one with `-i` and one without, and this task does not build it. Refusing the combination
outright is therefore correct for what this task ships: every `mode="falsecolor"` +
`quantity="illuminance"` call from this tool would otherwise pipe a plain (non-`-i`) render into
`falsecolor` under an illuminance label, which is exactly the "looks the same, wrong number" defect
the check names. `mode="hdr"` with `quantity="illuminance"` is unaffected -- it renders with `-i`
and reports the raw irradiance-proportional HDR directly, with no falsecolor step to mislabel.

## `use_illum=True` stays published and is always refused (ruling R64)

`window_group(action="make_secondary_source")` -- the `mkillum` route that would produce an illum
artifact standing in for a window group's own glazing -- is deferred by ruling R64 and unbuilt.
Nothing in this server produces that artifact, so `use_illum=True` can only ever be a lie (silently
rendering the aperture's own glazing material and calling it the illum's) or a refusal. It is
refused, unconditionally, naming the ruling and the unbuilt producer. `RENDER_ILLUM_USED` (the
advisory that would announce a real substitution) stays allocated and **unraised** by this module,
by construction -- the register's own three-state scheme (built-and-live, allocated-ahead, retired)
has a name for exactly this state, and `tests/test_advisories.py`'s `_ALLOCATED_WITHOUT_AN_EMITTER`
still carries it after this task.

## `-av` is refused everywhere except `mode="hdr"` (engine invariant 5), and IS wired there (R327)

`docs/tool-surface.md`'s engine invariants table: *"A `quality` dict override setting `-av` on an
illuminance, DF, glare or matrix run is refused (`AMBIENT_VALUE_SET`, `error`). It is permitted on
`run_render` in `hdr` mode, where it is a presentation choice."* This module refuses an `-av` key
in the `quality` dict whenever `mode != "hdr"`, matching `rad_mcp.point_in_time_tool`'s own
`AMBIENT_VALUE_SET` refusal (each module keeps its own copy of this check -- ruling R30's own
convention -- because each tool's *permitted* condition differs: point-in-time never permits it,
this tool permits it in exactly one mode).

**A published argument that is "permitted" has to reach the render, or the wire docstring is a
false statement (ruling R327, fix round 1).** An `-av` key that survives `_refuse_av_override`
(i.e. `mode == "hdr"`) is stripped from the dict before `rad_engine.rtrace.resolve_quality` sees it
(that function has no `-av` field and refuses an unrecognised key), and then applied to the
`QualityParameters` `resolve_quality` returns via `dataclasses.replace` --
`_apply_av_override` is the one function that does this, and it is the only place in this project
that ever constructs a `QualityParameters` with a non-`None` `ambient_value`
(`rad_core.quality.QualityParameters.ambient_value`'s own docstring). `rad_engine.render.
build_rpict_argv` then emits `-av v v v` (a grey triple -- `rpict`'s own `-av red grn blu`,
docs/specs/radiance-practical-guide.md:1705) whenever that field is not `None`. Both the
`QualityParameters` field and the argv emission are new in this fix round, in `rad_core/quality.py`
(Task 2's module) and `rad_engine/render.py` (Task 6's module) -- the two modules the invariant
itself names, plus the argv assembly already in the second.

## `AMBIENT_ARTIFACT_LIKELY` and `FALSECOLOR_SCALE_CLIPPED` are allocated and not emitted this task

`AMBIENT_ARTIFACT_LIKELY` ("`-ad` below the preset floor for the requested `-aa`") names no sourced
threshold in any spec this project has read -- inventing one would be exactly what CONTEXT.md's
"do not invent a number" rule forbids. `FALSECOLOR_SCALE_CLIPPED` needs the fraction of *pixels*
above `scale_max` (`rad_core.views.clipped_fraction`, which takes a raw pixel sequence), and
`rad_engine.image.read_image`'s `ImageInfo` exposes only aggregate statistics
(`pextrem -o`'s min/max, `pvalue`'s mean, and for a 180 degree fisheye the in-circle statistics,
R764), never the pixel stream itself -- composing
`clipped_fraction` here would mean this module re-reading every pixel a second way
(`pvalue`'s full output) duplicating physics CONTEXT.md's Layers table reserves for `rad_engine`.
Both are reported gaps, the same posture `rad_mcp.image_tool` documents for its own
`IMAGE_PIXEL_OVERFLOW`. `FALSECOLOR_SCALE_LINEAR_WIDE_RANGE` **is** emitted -- it needs only the
image's aggregate statistics, which `choose_scale` already turns into `decades` for free.

## R326: `choose_scale` is wired into a live path here for the first time

Task 9 landed `rad_core.views.choose_scale`/`clipped_fraction`/`FalsecolorScale` with zero callers
outside their own tests. This module is the first live caller (every non-`dry_run` result computes
a falsecolor scale via `choose_scale`, regardless of `mode`, since the published shape carries
`scale` unconditionally). A reachable defect in `choose_scale` was found and fixed in the same
commit as this wiring, not merely tested around: an all-black rendered image (`stats.maximum ==
0.0`) together with an explicit **positive** `scale_max` divided `0.0 / 0.0` computing `decades`,
raising `ZeroDivisionError` -- see `rad_core.views.choose_scale`'s own docstring and
`tests/test_views.py::TestAnAllBlackImageWithAnExplicitScaleMax`. An all-black render (a view facing an unlit wall, an empty room with no sky visible) is realistic
input a caller can trigger by picking a bad view, not a synthetic edge case.

## Every argument-shape refusal is checked before any project is opened, and the project before `dry_run`

`mode`, `quantity`+`mode`, `use_illum`, the `-av` override, `sky`'s own type, `scale_max`'s sign,
`quality` (`rad_engine.rtrace.resolve_quality`) and `view` (`rad_core.views.vt_flag`/`View`) are all
knowable from the call's own arguments -- none of them need this project's geometry, its sky, or a
compiled octree to answer, so they are refused before the project is opened. `dry_run=True` then
opens the project and refuses a pre-Phase-10 unscaled geometry record exactly as a live render does
(ruling R452, reversing R434), and returns before any sky, octree or render is built. That matches `RENDER_SUPERSAMPLED`'s and `RENDER_QUALITY_DRAFT`'s own advisories being
pure arithmetic over `resolution`/`supersample`/`quality`'s *name* -- both are attached to
`RenderDryRunOk` exactly as they would be to a live `RenderResult`, so a caller planning a call can
see them without paying for a render.
"""

from __future__ import annotations

import dataclasses
import logging
import math
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any, ClassVar, Final, Literal

from mcp_types import CallToolResult
from pydantic import BaseModel, ConfigDict, Field, RootModel, StrictInt, StrictFloat

from rad_core.quality import QualityParameters
from rad_core.views import View, ViewTypeError, WIDE_RANGE_DECADES, choose_scale, parse_view_file, vt_flag
from rad_engine import (
    EmptyOctreeError,
    LoadedProject,
    OctreeSetOverflowError,
    OctreeTooManyFilesError,
    ProjectError,
    ProjectPaths,
    RadianceError,
    RadianceNotFound,
    SceneFileNotFoundError,
    SceneInlineCommandError,
    build_octree,
    classify,
    discover,
    read_image,
    refuse_unscaled_geometry,
)
from rad_engine.advisories import EngineAdvisory
from rad_engine.env import RadianceEnv
from rad_engine.ies import PhotometryUnverifiedError
from rad_engine.postprocess import (
    DAYLIGHT_FACTOR_LUX_PER_PERCENT,
    DEFAULT_LDR_FORMAT,
    LDR_CONVERTERS,
    available_ldr_formats,
    build_falsecolor_argv,
    build_pfilt_argv,
    spawn_postprocess,
)
from rad_engine.render import SUPERSAMPLE_BY_PRESET, build_rpict_argv, rendered_size, spawn_render
from rad_engine.rtrace import ambient_interpolation_radius_m, direct_exact_forced, resolve_quality
from rad_engine.safe_io import NotARegularFileError, open_regular_file
from rad_engine.sky import (
    SkyError,
    SkySpec,
    generate_sky,
    sky_description_text,
    validate_sky_dates,
)

from rad_mcp.advisories import to_wire, to_wire_all
from rad_mcp.envelope import (
    Advisory,
    RadResult,
    Refusal,
    emit,
    one_of_schema,
    refuse,
    refuse_missing,
)
from rad_mcp.lit_scene import (
    DAYLIGHT_FACTOR_WITH_LUMINAIRES_MESSAGE,
    LitPlacementView,
    LitSceneRefusal,
    commit_lit_support,
    excluded_advisory,
    lit_advisories,
    lit_placements_view,
    verify_lit_files,
)
from rad_mcp.paths import PathOutsideRoot, RootsNotConfigured, contain
from rad_mcp.point_in_time_tool import (
    _project_north,
    _resolve_sky,
    _scan_scene_for_inline_commands,
    _scene_files,
    sky_key_advisories,
)
from rad_mcp.sky_site import resolve_sky_inputs
from rad_mcp.unknown_arguments import UnknownKeysRefused, unknown_key_advisories
from rad_mcp.scene_warnings import bsdf_not_applied_advisory, light_redirecting_advisory
from rad_mcp.runtime import Runtime, current, refuse_engine_error
from rad_mcp.timeouts import refuse_inline_timeout

logger = logging.getLogger(__name__)

__all__ = ["DESTRUCTIVE_ACTIONS", "RenderResponse", "RenderResult", "run_render"]

#: Empty, and that is the claim -- the same property `rad_mcp.glare_tool.DESTRUCTIVE_ACTIONS`
#: exports for check 0.7's sweep. `run_render` takes no `action` argument, deregisters no manifest
#: input and deletes no user file: it writes a rendered HDR and a converted LDR into this project's
#: `09_images/hdr/`/`09_images/ldr/` (`rad_engine.ProjectPaths.images_hdr`/`.images_ldr`), both of
#: which it owns.
DESTRUCTIVE_ACTIONS: tuple[str, ...] = ()

#: The subset of the published `mode` Literal this task actually builds. See the module docstring's
#: "What this task builds" section.
LIVE_RENDER_MODES: tuple[str, ...] = ("hdr", "falsecolor")


# --- nested shapes -------------------------------------------------------------------------------


class _Nested(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RenderViewPayload(_Nested):
    """The view this call actually rendered against, echoed back on the wire."""

    type: str
    position: list[float]
    direction: list[float]
    up: list[float]
    vh: float
    vv: float


# Why `clipped_fraction` is not composed here is the module docstring's "allocated and not emitted"
# section; the pointer to it left the published description under R809 (item 38). R810 moved out
# the name `rad_core.views.FalsecolorScale` and added what the other three fields hold
# (`choose_scale`, called on every live render whatever `mode`; the floor is its clamp, R331).
class RenderScalePayload(_Nested):
    """The falsecolor scale for the rendered image, reported in every `mode`.

    `maximum` is the `falsecolor -s` value, in the legend's unit: the render's own maximum
    (`chosen: true`) when `scale_max` was not given, else `scale_max` (`chosen: false`); with
    `mode="hdr"` no falsecolor image is made from it. The render's own maximum is in cd/m^2 for
    `quantity="luminance"` and lux for `"illuminance"`; for `"daylight_factor"` it is that lux
    maximum over 100, the largest DF % in the picture. `decades` is log10 of the render's maximum
    over its minimum, whatever `scale_max` was; when the minimum is zero or less it is taken against
    a floor of a millionth of the maximum (6 decades), not a measured range, and
    `FALSECOLOR_SCALE_LINEAR_WIDE_RANGE` says so unless `scale_log` was given. It is `0` for an
    all-black render.

    `clipped_fraction` is always `null`: the fraction of pixels above the scale is not measured, so
    `null` does not mean no pixel was clipped.
    """

    maximum: float
    chosen: bool
    decades: float
    clipped_fraction: float | None


# --- the result branches ---------------------------------------------------------------------------


class RenderBase(RadResult):
    """What every `run_render`-shaped answer carries. Also the union schema's shared base."""


# R810 moved this from the published text: the shape is docs/tool-surface.md §12's. The radius and
# `luminaires` sentences are lifted from the two field notes below, which never reached the wire.
class RenderResult(RenderBase):
    """A successful `run_render` call.

    `hdr` is the final HDR picture: the falsecolor image when `mode="falsecolor"`, otherwise the
    render after any supersampling filter. `ldr` is that picture converted to BMP. Both are files
    this call wrote under the project's `09_images/`. `ambient_interpolation_radius_m` is in
    metres: the compiled octree's bounding-cube edge times `-aa` over `-ar` at the resolved
    `quality`. `luminaires` lists the placements a lit render included, and is absent unless
    `luminaires=true`.
    """

    hdr: str
    ldr: str | None
    view: RenderViewPayload
    scale: RenderScalePayload
    ambient_interpolation_radius_m: float
    """``Rmin = maxSize x aa / ar`` (`rad_engine.rtrace.ambient_interpolation_radius_m`) at the
    resolved `quality`, `maxSize` being the octree this call actually compiled and rendered
    against (`getinfo -d`'s bounding-cube edge) -- docs/tool-surface.md#16-quality's *"every `run_*`
    result reports `ambient_interpolation_radius_m`"*, mirroring `PointInTimeResult`'s and
    `GlareResult`'s own field."""

    luminaires: list[LitPlacementView] | None = None
    """Every placement a lit render included, the same shape `PointInTimeResult.luminaires` carries
    (Phase 11a spec §7's Result paragraph binds both lit tools, R578); absent from the payload when
    `luminaires=false` (`OMIT_WHEN_NONE` below)."""

    OMIT_WHEN_NONE: ClassVar[frozenset[str]] = frozenset({"advisories", "luminaires"})


# The module docstring's dry-run section says why the two advisories can be attached without a
# render; the pointer to it left the published description under R809 (item 38). R810 widened
# "every argument-shape check": since R452 the dry run also opens the project, and the scene scan
# and sky resolution run above `dry_run` (R753 D10, R762, FW-2).
class RenderDryRunOk(RenderBase):
    """`dry_run=true` and nothing was refused: the arguments, the project, its scene files and any
    sky were checked, and nothing was rendered.

    `advisories` are those known before rendering, the same a live call attaches:
    `RENDER_SUPERSAMPLED` on every call, `RENDER_QUALITY_DRAFT` for a `draft` quality, and the
    sky's and luminaires' own. Advisories that need a live call, such as
    `FALSECOLOR_SCALE_LINEAR_WIDE_RANGE` or `LDR_FORMAT_SUBSTITUTED`, are not here.
    """

    dry_run: Literal[True] = True


RenderPayload = RenderResult | RenderDryRunOk | Refusal


class RenderResponse(RootModel[RenderPayload]):
    """`run_render`'s result: a `RenderResult`, a `RenderDryRunOk` when `dry_run=true`, or a
    refusal."""

    @classmethod
    def model_json_schema(cls, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return one_of_schema(super().model_json_schema(*args, **kwargs), RenderBase)


# --- argument checks -------------------------------------------------------------------------------


def _refuse_argument(message: str, *, field: str) -> CallToolResult:
    """`ARGUMENT_OUT_OF_RANGE` with the argument's own name in `field` -- ruling R14's code for an
    argument refused on its face."""
    return refuse(
        message,
        [Advisory(code="ARGUMENT_OUT_OF_RANGE", severity="error", field=field, message=message)],
    )


def _refuse_unbuilt_mode(mode: str) -> CallToolResult:
    message = (
        f"run_render(mode={mode!r}) is specified (docs/tool-surface.md #12) but not built. "
        f"run_render builds {sorted(LIVE_RENDER_MODES)}; 'contour' and 'tonemapped' are published "
        "Literal members with no pipeline. Use mode='hdr' for a photorealistic render or "
        "mode='falsecolor' for a luminance falsecolor map."
    )
    return _refuse_argument(message, field="mode")


def _refuse_illuminance_falsecolor() -> CallToolResult:
    message = (
        "run_render(mode='falsecolor', quantity='illuminance') is refused: an illuminance render "
        "(-i) and a luminance render produce numerically different images that can look identical "
        "without -i. run_render's falsecolor pipeline builds only the single-image luminance "
        "shape, not the two-render contour-overlay shape "
        "(falsecolor -i illum.hdr -p lum.hdr -cl) that a real illuminance falsecolor needs, so "
        "this combination would otherwise pipe a plain (non-i) render into falsecolor under an "
        "illuminance label. Use mode='hdr' with quantity='illuminance' for a raw -i render, or "
        "mode='falsecolor' with quantity='luminance' (the default)."
    )
    return _refuse_argument(message, field="quantity")


def _refuse_hdr_daylight_factor() -> CallToolResult:
    """Ruling R388: `mode="hdr"` with `quantity="daylight_factor"` is refused on its face.

    **What it published before.** The `-m 1.79` divisor that turns an irradiance-proportional render
    into a percentage is emitted by `rad_engine.postprocess.build_falsecolor_argv`, gated by
    its own `if quantity == "daylight_factor":` -- and that builder is reached only from
    the `mode == "falsecolor"` branch of `_dispatch` below. So `mode="hdr"` emitted **no multiplier
    at all** and handed back a raw HDR labelled a daylight factor.

    **Why refusing beats applying the multiplier in HDR too**, which was the other option on the
    owner's desk: refusing changes no existing number. Applying `-m 1.79` to the HDR path would
    silently restate every pixel of every `mode="hdr"` daylight-factor image any existing caller has
    already taken, and there is no advisory code for "the numbers you had before were wrong."

    `ARGUMENT_OUT_OF_RANGE` with `field="quantity"` (R14: an argument refused on its face), matching
    `_refuse_illuminance_falsecolor` next door, which refuses the mirror-image combination.
    """
    message = (
        "run_render(mode='hdr', quantity='daylight_factor') is refused: a daylight factor is a "
        "percentage of a 10 000 lux reference, and the divisor that produces it -- falsecolor's "
        "-m 1.79 (= 179 / 10 000 x 100) -- is emitted only on the falsecolor path "
        "(rad_engine.postprocess.build_falsecolor_argv). A mode='hdr' render therefore carries no "
        "multiplier at all, so the image would be a raw irradiance-proportional HDR labelled a "
        "daylight factor: every pixel wrong by that factor, with nothing on the wire saying so. "
        "For a daylight-factor image from this tool use mode='falsecolor' with no sky at all, "
        "which forces the CIE reference overcast sky, reports DF_SKY_FORCED and does emit -m 1.79. "
        "For the number at a grid of points use run_point_in_time(quantity='daylight_factor'), "
        "also with no sky. For a picture in this mode use quantity='luminance' (the default) or "
        "quantity='illuminance'."
    )
    return _refuse_argument(message, field="quantity")


def _refuse_daylight_factor_sky(sky: dict[str, Any] | str) -> CallToolResult:
    """Ruling R389: a caller-supplied `sky` alongside `quantity="daylight_factor"` is refused.

    **Omitting `sky` is the accepted shape, ruling R390.** `sky` is required on this tool for every
    quantity *except* `daylight_factor` -- `_dispatch`'s `required` dict, which drops `sky` from
    `refuse_missing`'s arguments exactly when `quantity == "daylight_factor"`, is what makes that
    conditional -- so there IS an omit-the-sky route here now, the same one `run_point_in_time` has:
    the CIE reference overcast sky is forced and `DF_SKY_FORCED` (info) reports the substitution.
    Until R390 there was none, and R389 left `quantity="daylight_factor"` with no accepted call
    shape on `run_render` at all; the owner took R390 once that side effect was measured at the
    wire. What R389 still refuses is unchanged: a sky the caller *supplied*, of any shape, because
    the falsecolor route divides by a 10 000 lux reference that sky is not. `mode="hdr"` is refused
    one check earlier by `_refuse_hdr_daylight_factor` for emitting no divisor at all.

    Refused **here**, before `dry_run` and before `runtime.open`, rather than left to
    `point_in_time_tool._resolve_sky`'s own R389 backstop: that call happens after the project is
    opened, so relying on it alone would let `dry_run=True` return success for a call the live run
    refuses. Every other check in `_dispatch` precedes `if dry_run:` for the same reason.
    """
    label = f"sky.type={sky.get('type')!r}" if isinstance(sky, dict) else f"sky={sky!r}"
    message = (
        f"run_render(quantity='daylight_factor') was called with a sky of your own ({label}), and "
        "that is refused. A daylight factor is defined as the ratio of the interior illuminance to "
        "the unobstructed exterior horizontal illuminance under the CIE standard overcast sky, "
        "conventionally 10 000 lux; the falsecolor multiplier this server applies, -m 1.79, is that "
        "10 000 lux and nothing else, so it divides by the reference sky whichever sky was actually "
        "rendered. Measured on this machine's Radiance, gensky 6 21 12.0 -c -g 0.2 is 1.82x the "
        "reference gensky 12 21 12.0 -c -g 0.2 -B 55.866 on both terms of its 3 2 row: an image "
        "rendered under it would read 2.0 % where the definitional answer is 1.10 %. Omit sky "
        "entirely and the reference sky is substituted for you, reported by DF_SKY_FORCED "
        "(mode='falsecolor' only -- mode='hdr' emits no multiplier and is refused separately); or "
        "keep your sky here and render quantity='luminance' or 'illuminance'."
    )
    return _refuse_argument(message, field="sky")


def _refuse_use_illum() -> CallToolResult:
    message = (
        "use_illum=True is refused: window_group(action='make_secondary_source') "
        "(the mkillum route) is not built, and this server builds no illum artifact for any "
        "window group. "
        "use_illum can only ever be a lie (silently rendering the aperture's own glazing material "
        "and calling it the illum's) or a refusal -- it is refused. Leave use_illum at its default "
        "(False)."
    )
    return _refuse_argument(message, field="use_illum")


def _refuse_av_override(quality: str | dict[str, Any], mode: str) -> CallToolResult | None:
    """`AMBIENT_VALUE_SET` (error) -- engine invariant 5, applied the way §12 states it for this
    tool alone: refused on every mode except `hdr`. See the module docstring's `-av` section for
    what `mode="hdr"` does and does not do with a permitted override."""
    if not isinstance(quality, dict) or "av" not in quality:
        return None
    if mode == "hdr":
        return None
    message = (
        f"quality override -av {quality['av']!r} is refused on mode={mode!r}: the ambient value is "
        "a constant substituted for the indirect calculation, so a non-zero -av adds light that is "
        "not in the scene and would corrupt a falsecolor/contour/tonemapped map the same way it "
        "would corrupt any other quantitative run. docs/tool-surface.md's engine invariant 5 "
        "permits -av only on run_render's mode='hdr', where it is a presentation choice rather "
        "than a reported number. Remove -av from the quality dict, or use mode='hdr'."
    )
    return refuse(
        message,
        [Advisory(code="AMBIENT_VALUE_SET", severity="error", field="quality", message=message)],
    )


def _apply_av_override(
    parameters: QualityParameters, quality: str | dict[str, Any], mode: str
) -> QualityParameters:
    """`-av`, actually applied to `parameters` when `mode == "hdr"` and `quality` carries one
    (ruling R327). Reached only after `_refuse_av_override` has already refused every other mode --
    this function never refuses anything itself; it only ever adds a value `resolve_quality` was
    never given (that function has no `av` key in its own domain at all, so the caller strips it
    from the dict before resolving and reapplies it here with the resolved `QualityParameters` in
    hand).

    `quality["av"]` becomes `parameters.ambient_value` unchanged (a single number -- the same shape
    every other overridable ambient flag this dict accepts already has); `rad_engine.render.
    build_rpict_argv` is what turns one number into `-av`'s three-channel grey triple. `quality`
    that is not a dict, a dict with no `"av"` key, or `mode != "hdr"` all return `parameters`
    unchanged -- the identical `QualityParameters` object `resolve_quality` returned, not a copy,
    so a caller comparing by identity for the common "no override" case sees that nothing happened.
    """
    if mode != "hdr" or not isinstance(quality, dict) or "av" not in quality:
        return parameters
    return dataclasses.replace(parameters, ambient_value=float(quality["av"]))


#: `_resolve_view`'s six published keys -- shared between the unknown-key check and the
#: required-key check so the two lists cannot drift apart (R720).
_VIEW_KEYS: Final = ("type", "position", "direction", "up", "vh", "vv")


def _resolve_view(view: dict[str, Any] | str, *, roots: tuple[Path, ...]) -> View:
    """`view` off the wire -> a validated `rad_core.views.View`.

    A `str` is a `.vf` path, contained against `roots` and parsed with `rad_core.views.
    parse_view_file` (Task 1). A `dict` is checked for its six required keys (`type`, `position`,
    `direction`, `up`, `vh`, `vv`) and its `type` is validated through `vt_flag`, which raises
    `ViewTypeError` for an unpublished letter and never suggests a substitute (docs/plan.md check
    9.1) -- callers of this function rely on that: the refusal this raises is never allowed to
    mention `-vtv`. A key `_VIEW_KEYS` does not name is refused (`UnknownKeysRefused`), and
    `position`/`direction`/`up` must each be three finite numbers -- a two- or four-element list
    was previously an `IndexError` or a silent truncation, and a `NaN`/bool/string component was
    silently accepted (ruling R720).
    """
    if isinstance(view, str):
        resolved = contain(view, roots)
        with open_regular_file(resolved, "rb") as handle:
            text = handle.read().decode("utf-8")
        return parse_view_file(text)
    if not isinstance(view, dict):
        raise ViewTypeError(
            f"view must be a dict ({{'type', 'position', 'direction', 'up', 'vh', 'vv'}}) or a "
            f".vf path string; got {type(view).__name__}."
        )
    if key_advisories := unknown_key_advisories("run_render's view", view, _VIEW_KEYS, field="view"):
        raise UnknownKeysRefused(key_advisories)
    missing = [key for key in _VIEW_KEYS if key not in view]
    if missing:
        raise ViewTypeError(f"view is missing {', '.join(missing)}, and none is assumed.")
    for key in ("position", "direction", "up"):
        value = view[key]
        if not (
            isinstance(value, list | tuple) and len(value) == 3
            and all(isinstance(v, int | float) and not isinstance(v, bool) and math.isfinite(v) for v in value)
        ):
            raise ViewTypeError(f"view {key} must be three finite numbers; got {value!r}.")
    vt_flag(view["type"])  # raises ViewTypeError for an unpublished letter; never a substitute
    return View(
        view_type=view["type"],
        position=(view["position"][0], view["position"][1], view["position"][2]),
        direction=(view["direction"][0], view["direction"][1], view["direction"][2]),
        up=(view["up"][0], view["up"][1], view["up"][2]),
        vh=float(view["vh"]),
        vv=float(view["vv"]),
    )


# --- the two written destinations, contained (ruling R48's primitive) -----------------------------


def _run_discriminator() -> str:
    """The per-call suffix on every filename this tool writes -- a UTC stamp plus eight hex
    characters of a UUID4, the same shape `rad_mcp.glare_tool._run_discriminator` and
    `rad_engine.jobs.new_job_id` already use (R30: one shape, reused rather than a third
    convention, with its own module-local copy per the established per-module-copy pattern)."""
    return f"{datetime.now(UTC).strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex[:8]}"


def _contain_written_path(path: Path, *, roots: tuple[Path, ...], what: str) -> Path:
    """Refuse `path` before anything is created if it escapes `roots` -- ruling R48's primitive,
    the same guard `rad_mcp.glare_tool._contain_written_path` applies for its own two writes.
    Raises `PathOutsideRoot`, shaped into a refusal by `rad_mcp.runtime.refuse_engine_error` in
    this module's own handler chain.

    **`rad_mcp.paths.contain`, not `resolved_parent_within` (whole-branch fix wave, B2).** The
    parent-chain check this used to make is exactly the shape `rad_mcp.export_tool`'s own module
    docstring names as Task 11's escape: it resolves the *parent* and judges that, so a symlink
    at the **final component** -- the file this call is about to write -- was never resolved and
    never judged. Measured on a leaf symlink inside a root pointing outside every root:

        resolved_parent_within(link, roots) -> True     (does NOT refuse)
        contain(link, roots)                -> PathOutsideRoot

    The whole guarantee then rested on `O_NOFOLLOW` in `safe_io.open_regular_file`, one flag on
    one line, with 165 tests staying green when it was removed. `contain` resolves the *whole*
    candidate, so a symlinked leaf is judged exactly like a symlinked directory three levels up,
    and the refusal is this tool's own rather than an `ELOOP` from the open.

    **The original `path` is returned, not `contain`'s resolved one.** `contain` is used for its
    verdict; substituting the resolved path would change what this tool writes to and what it
    publishes, which is not what this fix is for. A leaf symlink that resolves *inside* a root is
    still permitted here, exactly as before, and still refused one layer down by the destination's
    own `lstat`/`S_ISREG` guard (`safe_io.refuse_existing_non_regular_destination`, which both
    `spawn_render` and `spawn_postprocess` call on `out_path` before they spawn) -- what changed is
    that one resolving outside is refused here instead of relying on a single flag further down.
    """
    try:
        contain(path, roots)
    except PathOutsideRoot as exc:
        raise PathOutsideRoot(
            f"the {what} would be written to {path}, which resolves outside every allowed "
            "root. Nothing has been written. A symlink -- at the file itself or at any directory "
            "in its chain -- inside the project is the usual cause; the files run_render writes "
            "must stay inside the project they belong to."
        ) from exc
    return path


# --- advisories that need no rendering (dry_run and live alike) -----------------------------------


def _supersample_advisory(
    rendered_w: int, rendered_h: int, supersample: int, final_w: int, final_h: int
) -> Advisory:
    return Advisory(
        code="RENDER_SUPERSAMPLED",
        severity="info",
        message=(
            f"rendered at {rendered_w}x{rendered_h} px (supersample factor {supersample}), then "
            f"pfilt -x /{supersample} -y /{supersample} down to the final {final_w}x{final_h} px. "
            "Radiance's own quality model computes the picture larger than the final size and "
            "filters it down for antialiasing (docs/tool-surface.md #12); a supersample of 1 "
            "renders at the final size with no filtering step."
        ),
    )


def _quality_draft_advisory() -> Advisory:
    return Advisory(
        code="RENDER_QUALITY_DRAFT",
        severity="info",
        message=(
            "quality='draft' is the cheapest ambient preset: expect visual artifacts like "
            "splotches in indirect light. This image is not suitable as the source of any "
            "reported number -- it is for quick composition checks only."
        ),
    )


def _wide_range_advisory(decades: float, *, clamped: bool) -> Advisory:
    """R331 (Phase 9 Task 11 fix round 2): the identical fabricated-span defect R329 fixed in
    `rad_mcp.image_tool._wide_range_advisory` also lived here, on `choose_scale`'s other live
    caller. `decades` reads exactly `log10(1e6) == 6.0` whenever the rendered image's own minimum
    is not positive -- an ordinary case for a real render (a view facing an unlit wall, a room with
    no sky visible), not an edge one -- regardless of the image's actual content; `clamped` says
    which sentence to build, and the advisory **code** is unchanged either way.

    **`field` changes with `clamped` too (fix round 3, a Minor the review caught): a caller that
    branches on `field` rather than parsing `message` must not still be pointed at `scale_log`
    when the message itself no longer suggests it.** Measured before this fix: both branches
    returned `field="scale_log"` unconditionally, so a caller reading only the field on a real,
    clamped render still saw `scale_log` nominated as the fix, which the message text
    contradicted. `field=None` on the clamped branch, since no argument this tool publishes fixes
    a fabricated figure; `field="scale_log"` stays on the unclamped branch, where it is a genuine
    suggestion.
    """
    if clamped:
        message = (
            f"this image contains pixels at or below zero luminance, so the falsecolor scale's "
            f"own {decades:.2g}-decade figure is measured against a floor "
            "(rad_core.views.choose_scale's clamp on a non-positive minimum), not against this "
            "image's own minimum -- it is not a measurement of the image's true dynamic range. A "
            "linear falsecolor scale was used regardless of this figure."
        )
        field = None
    else:
        message = (
            f"this image spans {decades:.2g} decades of dynamic range, above the "
            f"{WIDE_RANGE_DECADES:g}-decade threshold, and a linear falsecolor scale was used. A "
            "linear scale of a sunlit room is a picture of the sun patch with everything else at "
            "the bottom of the scale -- pass scale_log to map falsecolor -log decades instead."
        )
        field = "scale_log"
    return Advisory(code="FALSECOLOR_SCALE_LINEAR_WIDE_RANGE", severity="info",
                     field=field, message=message)


def _ldr_format_substituted_advisory() -> Advisory:
    return Advisory(
        code="LDR_FORMAT_SUBSTITUTED",
        severity="info",
        message=(
            f"ra_png is absent from this Radiance install (measured). run_render writes its LDR "
            f"image with {LDR_CONVERTERS[DEFAULT_LDR_FORMAT]!r} ({DEFAULT_LDR_FORMAT!r}) on every "
            "install, whatever converters it carries."
        ),
    )


# --- the render pipeline (no dry_run) ---------------------------------------------------------------


def _write_sky(
    env: RadianceEnv, paths: ProjectPaths, sky_spec: SkySpec, *, disc: str, timeout: float
) -> tuple[Path, list[Advisory]]:
    """Write this call's sky description and return the file `build_octree` will compile, with
    what the sky generator reported on stderr (final review FW-1, `field: "sky"`).

    **`disc` is B6's second site, and it is not in the fix wave's own list -- re-derived here**
    (CONSTRAINTS: "a site list handed to you is a starting point, not an authority"). The finding
    named `run_render`'s octree path; this file has the identical shape one call earlier and is a
    *build input to that octree*. It is written with `open_regular_file(..., "wb")`
    (`O_WRONLY | O_CREAT | O_TRUNC`, no lock), `run_render` is a plain `def` that FastMCP runs in a
    worker thread, and under a fixed name two overlapping calls on one project share it: call B
    rewrites the sky while call A's `oconv` is reading it, and a truncated or half-written sky
    description is a *wrong rendered image*, not a crash. Discriminating the octree alone would
    have left that open.
    """
    # Ruling R694: `generate_sky` spawns the generator and, for a project declaring a non-zero
    # north, pipes its stdout through a second spawned `xform -rz` -- neither `gensky` nor
    # `gendaylit`'s own `-r` rotates on this Radiance 6.1a.
    generator_advisories: list[EngineAdvisory] = []
    sky_text = sky_description_text(
        generate_sky(env, sky_spec, cwd=paths.root, timeout=timeout, advisories=generator_advisories),
    )
    sky_path = paths.root / f"_run_render_sky_{disc}.rad"
    with open_regular_file(sky_path, "wb") as handle:
        handle.write(sky_text.encode("utf-8"))
    return sky_path, [to_wire(a, field="sky") for a in generator_advisories]


# --- the tool ------------------------------------------------------------------------------------


def run_render(
    project: Annotated[
        str | None,
        Field(description="the project handle. Required."),
    ] = None,
    view: Annotated[
        dict[str, Any] | str | None,
        Field(
            description=(
                "`{type: v|l|c|h|a|s, position, direction, up, vh, vv}` or a `.vf` path string. "
                "An unpublished `type` letter is refused (`field=\"view\"`) rather than defaulted "
                "to perspective (docs/plan.md check 9.1)."
            )
        ),
    ] = None,
    sky: Annotated[
        dict[str, Any] | str | None,
        Field(
            description=(
                "a CIE/Perez sky dict, or a `.rad` path string. "
                "**Required for `quantity=\"luminance\"` and `quantity=\"illuminance\"` unless "
                "`luminaires=true`**, and rendered as given there. With "
                "`quantity=\"daylight_factor\"` any `sky` is refused (`field=\"sky\"`, ruling R389): "
                "omit it and the CIE reference overcast sky (`gensky -c -B 55.866`, 10 000 lux) is "
                "forced, reported by `DF_SKY_FORCED` (info, R390); `quantity` says why. The old `longitude`/`meridian` "
                "keys are refused: they were west-positive (R762). Give `latitude`, "
                "`longitude_east` and `meridian_east` (degrees, east-positive) instead, all three "
                "or none."
            )
        ),
    ] = None,
    mode: Annotated[
        Literal["hdr", "falsecolor", "contour", "tonemapped"],
        Field(
            description=(
                "`\"hdr\"` (default, the raw render) or `\"falsecolor\"` (a colour-mapped "
                "luminance image). `\"contour\"` and `\"tonemapped\"` are published and refused "
                "(`ARGUMENT_OUT_OF_RANGE`): neither has a pipeline built."
            )
        ),
    ] = "hdr",
    quantity: Annotated[
        Literal["luminance", "illuminance", "daylight_factor"],
        Field(
            description=(
                "`\"luminance\"` (default) or `\"illuminance\"` (`-i`). `\"daylight_factor\"` has "
                "**exactly one accepted call shape: `mode=\"falsecolor\"` with no `sky`** (ruling "
                "R390). The others are refused because each makes the published percentage wrong: "
                "the `-m 1.79` divisor (179 / 10 000 x 100) is emitted only by the falsecolor "
                "builder, so `mode=\"hdr\"` would publish a raw HDR under a daylight-factor label "
                "(ruling R388), and it divides by the 10 000 lux reference sky whichever sky you "
                "supplied (ruling R389). On the accepted shape the reference sky is forced for you "
                "and `DF_SKY_FORCED` (info) reports it."
            )
        ),
    ] = "luminance",
    resolution: Annotated[
        list[StrictInt] | None,
        Field(description="`[width, height]` at the FINAL size. Defaults to `[1280, 720]`."),
    ] = None,
    quality: Annotated[
        Literal["draft", "standard", "high"] | dict[str, Any],
        Field(
            description=(
                "`\"draft\"`/`\"standard\"`/`\"high\"` (`rad_core.quality.AMBIENT_PRESETS`) or a "
                "dict override (`rad_engine.rtrace.resolve_quality`) -- a bare dict overrides "
                "`standard`; `{\"base\": \"reference\"}` reaches the fourth preset the Literal "
                "does not name. `-av` in an override dict is refused unless `mode=\"hdr\"` "
                "(`AMBIENT_VALUE_SET`); on `mode=\"hdr\"` it IS applied to the render (`-av`'s "
                "grey triple, `rad_engine.render.build_rpict_argv`)."
            )
        ),
    ] = "standard",
    scale_max: Annotated[
        StrictFloat | None,
        Field(
            description=(
                "the falsecolor `-s` value. `None` (default) computes it from the rendered "
                "image's own maximum (`rad_core.views.choose_scale`) and reports the chosen "
                "value; a caller-supplied value must be positive. It is in the legend's unit: DF % "
                "for `quantity=\"daylight_factor\"` (the computed value is the image's lux "
                "maximum over 100), otherwise the image's own cd/m^2 or lux."
            )
        ),
    ] = None,
    scale_log: Annotated[
        StrictInt | None,
        Field(
            description=(
                "`falsecolor -log decades`, for a scene spanning several decades of dynamic "
                "range. `None` (default) uses a linear scale."
            )
        ),
    ] = None,
    levels: Annotated[
        StrictInt,
        Field(description="`falsecolor -n`, the number of colour bands."),
    ] = 10,
    legend_label: Annotated[
        str | None,
        Field(
            description=(
                "`falsecolor -l`. Omitted unless you pass one -- except on "
                "`quantity=\"daylight_factor\"`, where `build_falsecolor_argv` defaults it to "
                "`\"DF %\"`; pass your own to override that."
            )
        ),
    ] = None,
    supersample: Annotated[
        StrictInt | None,
        Field(
            description=(
                "`None` (default) resolves from the `quality` preset's own name "
                "(`SUPERSAMPLE_BY_PRESET`); an explicit value overrides it."
            )
        ),
    ] = None,
    use_illum: Annotated[
        bool,
        Field(
            description=(
                "**`use_illum` is published and always refused.** "
                "`window_group(action=\"make_secondary_source\")` is not built, so nothing in this "
                "server can "
                "produce the illum artifact `use_illum=True` would stand in for the aperture's own "
                "glazing. Default `False`."
            )
        ),
    ] = False,
    luminaires: Annotated[
        bool,
        Field(
            description=(
                "light the scene with every placement (`luminaire` tool). `sky` becomes optional "
                "-- for every `quantity` but `daylight_factor`, which `luminaires=true` refuses "
                "outright (`DAYLIGHT_FACTOR_WITH_LUMINAIRES`, R457) -- and a lamp-only render "
                "carries no sky file. Off by default (R456); a project with placements then "
                "reports `LUMINAIRES_EXCLUDED` (warn). A lit render first checks, also under "
                "`dry_run`, that a luminaire is placed, the R489 instance bound and every "
                "luminaire file, and passes `-dt 0 -dc 1` to `rpict` so no lamp in an array is "
                "culled (R492, R494)."
            )
        ),
    ] = False,
    dry_run: Annotated[
        bool,
        Field(
            description=(
                "validate every argument and open the project, without compiling an octree or "
                "rendering anything. A project whose geometry was never scaled to metres is "
                "refused here, as a live render refuses it. Reports the same "
                "`RENDER_SUPERSAMPLED`/ `RENDER_QUALITY_DRAFT` advisories a live call would."
            )
        ),
    ] = False,
    inline_timeout_s: Annotated[
        StrictFloat,
        Field(
            description=(
                "bound, in seconds, on every Radiance spawn this call makes after the install "
                "probe (`discover()`'s `rpict -version`, then `rtrace -version` if needed, each "
                "under `rad_engine.env`'s fixed 10 s) -- the sky generator, the `xform` that "
                "rotates the sky when the project declares a non-zero north, the octree build, "
                "`rpict`, `pfilt` (if supersampled), `falsecolor` (if `mode=\"falsecolor\"`) and "
                "the LDR converter. Bounds each spawn, not their total. This tool always "
                "completes inline; there is no job hand-off."
            )
        ),
    ] = 20,
) -> RenderResponse:
    """Photorealistic HDR and falsecolor luminance/illuminance maps, via `rpict`/`falsecolor`/`pfilt`.

    Use it for a picture of the scene. For numbers at sensor points use `run_point_in_time`; for
    glare (DGP) use `run_glare`; to falsecolor, tonemap or convert an HDR you already have use
    `image`. For the **number** at a grid of points rather than an image, use
    `run_point_in_time(quantity="daylight_factor")`, also with **no** `sky`.

    **`resolution` is the FINAL size; the render itself happens larger.** Radiance's own quality
    model computes the picture at a multiple of the final size and filters it down with `pfilt` for
    antialiasing (docs/tool-surface.md #12) -- `supersample` defaults to 1/2/3 for draft/standard/
    high (`rad_engine.render.SUPERSAMPLE_BY_PRESET`), `rpict` runs at `resolution x supersample`,
    and the result's `RENDER_SUPERSAMPLED` advisory states the rendered, filter and final sizes.

    **`quantity="illuminance"` requires an `-i` render, and the two look identical without it.** A
    plain luminance render and an illuminance render (`rpict -i`) can produce indistinguishable
    pixel values while meaning cd/m2 in one case and a value that must be read as lux (via the 179
    factor) in the other -- a picture that looks right and reports the wrong physical quantity.
    `mode="hdr"` with `quantity="illuminance"` renders with `-i` and reports the raw
    irradiance-proportional HDR directly. `mode="falsecolor"` with `quantity="illuminance"` is
    refused: this tool's falsecolor reads one plain (non-`-i`) render, so it would label a luminance
    picture as illuminance.

    A sky dict's site is `latitude`, `longitude_east`, `meridian_east` (degrees, east-positive),
    all three or none; with none, the project's registered weather file supplies it
    (`SKY_LOCATION_FROM_EPW`), and with no weather file the call is refused (`SKY_SITE_REQUIRED`).
    The old `longitude`/`meridian` keys are refused: they were west-positive (R762).

    **LDR output is always BMP.** Every live call also converts the final HDR to an LDR via
    `ra_bmp`, whatever converters the install carries, since this tool publishes no `format`
    argument to ask for anything else (ruling R307); `LDR_FORMAT_SUBSTITUTED` (info) is reported
    when the install lacks `ra_png`.
    """
    runtime = current()
    try:
        return _dispatch(
            runtime,
            project=project,
            view=view,
            sky=sky,
            mode=mode,
            quantity=quantity,
            resolution=resolution,
            quality=quality,
            scale_max=scale_max,
            scale_log=scale_log,
            levels=levels,
            legend_label=legend_label,
            supersample=supersample,
            use_illum=use_illum,
            luminaires=luminaires,
            dry_run=dry_run,
            inline_timeout_s=inline_timeout_s,
        )
    except (LitSceneRefusal, PhotometryUnverifiedError) as exc:
        # Phase 11a spec §7/R487: the code, severity and field are the exception's own (R497/R554),
        # matching `run_point_in_time`'s own handler for the same two exceptions.
        logger.info("run_render refused: %s", exc)
        return refuse(str(exc), [Advisory(code=exc.code, severity=exc.severity, field=exc.field, message=str(exc))])
    except (EmptyOctreeError, OctreeSetOverflowError, OctreeTooManyFilesError) as exc:
        logger.info("run_render refused: %s", exc)
        code = getattr(exc, "code", None)
        if code is None:
            return refuse(str(exc))
        return refuse(
            str(exc),
            [Advisory(code=code, severity=getattr(exc, "severity", "error"), message=str(exc))],
        )
    except SceneInlineCommandError as exc:
        # Rulings R33/R753 (final review): a registered scene file carrying a hostile `!` line, or
        # an include tree too large to verify, is refused before anything reads it. Shaped with its
        # own allocated code, as `annual_tool` shapes it, rather than escaping as an uncoded
        # ToolError carrying an absolute path (check 0.4).
        logger.info("run_render refused: %s", exc)
        return refuse(
            str(exc),
            [Advisory(
                code=getattr(exc, "code", "SCENE_INLINE_COMMAND"), severity="error",
                field="project", message=str(exc),
            )],
        )
    except SceneFileNotFoundError as exc:
        logger.info("run_render refused: %s", exc)
        return refuse(
            str(exc),
            [Advisory(code=exc.code, severity=exc.severity, field="project", message=str(exc))],
        )
    except UnknownKeysRefused as exc:
        logger.info("run_render refused: %s", exc)
        return refuse(str(exc), exc.advisories)
    except SkyError as exc:
        logger.info("run_render refused: %s", exc)
        if exc.code is not None:
            return refuse(
                str(exc),
                [Advisory(code=exc.code, severity=exc.severity, field=exc.field, message=str(exc))],
            )
        return refuse(str(exc))
    except RadianceNotFound as exc:
        logger.info("run_render refused: %s", exc)
        return refuse(
            str(exc), [Advisory(code="RADIANCE_NOT_FOUND", severity="error", message=str(exc))]
        )
    except NotARegularFileError as exc:
        logger.info("run_render refused: %s", exc)
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
                    field="project",
                    message=message,
                )
            ],
        )
    except (ProjectError, PathOutsideRoot, RootsNotConfigured) as exc:
        logger.info("run_render refused: %s", exc)
        return refuse_engine_error(exc)
    except ViewTypeError as exc:
        # Reached only if `view` raises past `_dispatch`'s own guard (defensive: `_dispatch`
        # already catches this at the call site and refuses with `field="view"`).
        logger.info("run_render refused: %s", exc)
        return _refuse_argument(str(exc), field="view")
    except ValueError as exc:
        # `rad_core.views.choose_scale`'s own refusal for a non-positive falsecolor scale (an
        # all-black render with no explicit scale_max -- see the module docstring's R326 section),
        # or an unsupported background/value this module has no code for. Reported uncoded rather
        # than reused onto a code that means something else, the same posture `rad_mcp.image_tool`
        # and `rad_mcp.glare_tool` take for the identical class of failure.
        logger.info("run_render refused: %s", exc)
        return refuse(str(exc))
    except RadianceError as exc:
        logger.info("run_render failed: %s", exc)
        return refuse(str(exc))
    except OSError as exc:
        logger.warning("run_render failed on the filesystem: %s", exc)
        return refuse(
            f"run_render could not complete: {exc}. Nothing about the arguments is wrong -- the "
            "filesystem refused the operation, so this is an environment problem (permissions, a "
            "read-only mount, or a file held open elsewhere)."
        )


def _dispatch(
    runtime: Runtime,
    *,
    project: str | None,
    view: dict[str, Any] | str | None,
    sky: dict[str, Any] | str | None,
    mode: str,
    quantity: str,
    resolution: list[int] | None,
    quality: str | dict[str, Any],
    scale_max: float | None,
    scale_log: int | None,
    levels: int,
    legend_label: str | None,
    supersample: int | None,
    use_illum: bool,
    luminaires: bool,
    dry_run: bool,
    inline_timeout_s: float,
) -> CallToolResult:
    call = "run_render"
    # **Ruling R390 (the owner): `sky` is optional for `quantity="daylight_factor"` and required for
    # every other quantity.** The required set is assembled here rather than split into two
    # `refuse_missing` calls so that a call missing both `project` and `sky` still returns ONE
    # refusal carrying one advisory per absent argument -- the shape `rad_mcp.envelope.
    # refuse_missing`'s own docstring publishes ("One advisory per absent argument, each carrying
    # its own `field`"), which two sequential calls would have collapsed to whichever fired first.
    #
    # An absent `sky` on a daylight-factor call is not "no sky": it reaches
    # `rad_mcp.point_in_time_tool._resolve_sky` below, whose `quantity == "daylight_factor"` branch
    # calls `rad_engine.sky.resolve_daylight_factor_sky(None, ...)` and gets back the forced
    # reference overcast sky (`resolved_from="forced_overcast"`, `gensky -c -B 55.866`) together
    # with `DF_SKY_FORCED` at `info`. That is `run_point_in_time`'s own mechanism, reached through
    # the same function this module already imports -- not a second one invented here.
    required: dict[str, Any] = {"project": project, "view": view}
    if quantity != "daylight_factor" and not luminaires:
        required["sky"] = sky
    if (refusal := refuse_missing(call, **required)) is not None:
        return refusal
    assert project is not None and view is not None

    # **`inline_timeout_s` goes through the one shared spawn-timeout check** (Phase 13 Branch C,
    # ruling R704 -- `rad_mcp.timeouts.refuse_inline_timeout`). Before R704 `run_render` checked
    # `inline_timeout_s` nowhere at all: every one of `nan`, `inf`, `1e9`, `0`, `-1` and JSON
    # `true` reached a clean `dry_run` success (measured, `rad_mcp.timeouts`'s own docstring), and
    # a live call with one of the non-positive or non-finite values would have passed it straight
    # to `subprocess.run(..., timeout=...)` at the first spawn -- the sky generator, the `xform`
    # that rotates the sky for a non-zero north, the octree build, `rpict`, `pfilt`, `falsecolor`
    # or the LDR converter, whichever ran first. No `isinstance(bool)` exemption: this is new
    # validation, not a tool with an existing boolean behaviour to preserve, so `true`/`false` get
    # the plain numeric read like any other value.
    if (refusal := refuse_inline_timeout(inline_timeout_s)) is not None:
        return refusal

    if mode not in LIVE_RENDER_MODES:
        return _refuse_unbuilt_mode(mode)

    if mode == "falsecolor" and quantity == "illuminance":
        return _refuse_illuminance_falsecolor()

    # Ruling R388. Ordered **before** the `sky` refusal below deliberately, and the reason changed
    # shape at R390 rather than going away: while `sky` was required for every quantity, an
    # R389-first ordering swallowed this combination entirely and left `_refuse_hdr_daylight_factor`
    # unreachable. Now that an omitted `sky` is legal for `daylight_factor`, R389-first would leave
    # it reachable only through `mode="hdr"` + no sky, and a caller who passed a sky as well -- the
    # commonest way to write the call, since `sky` is required for everything else -- would be told
    # about the sky when the actual defect is the mode. With this order, `mode="hdr"` always gets
    # the multiplier explanation and `mode="falsecolor"` gets the sky one.
    if mode == "hdr" and quantity == "daylight_factor":
        return _refuse_hdr_daylight_factor()

    if use_illum:
        return _refuse_use_illum()

    if (refusal := _refuse_av_override(quality, mode)) is not None:
        return refusal

    # `sky is not None` is R390's guard, not a widening of the shape check: for every quantity but
    # `daylight_factor` the `refuse_missing` above has already refused `None`, so the only call that
    # reaches this line with `sky is None` is the daylight-factor one R390 made legal. Without it,
    # the very shape R390 exists to accept would be refused here as "got NoneType".
    if sky is not None and not isinstance(sky, dict | str):
        message = (
            f"sky must be a sky dict (docs/tool-surface.md §10) or a .rad path string; got "
            f"{type(sky).__name__}."
        )
        return _refuse_argument(message, field="sky")

    # Ruling R389, placed after the shape check above so `_refuse_daylight_factor_sky` is handed a
    # `dict | str` and its `isinstance(sky, dict)` branch is the only distinction it has to make.
    # **`and sky is not None` is R390**: R389 refuses a sky the caller *supplied*, and an omitted
    # one is now the accepted shape rather than a fourth thing to refuse.
    if quantity == "daylight_factor" and sky is not None:
        return _refuse_daylight_factor_sky(sky)

    if luminaires and quantity == "daylight_factor":
        # R457, spec §7's refusal order (R570): the luminance/R388/R389 checks above, then this one
        # -- both before the project is opened. R580: the message is one spelling shared with
        # `run_point_in_time`'s own refusal (`lit_scene.DAYLIGHT_FACTOR_WITH_LUMINAIRES_MESSAGE`);
        # `code=`/`severity=` stay literal here (R497).
        message = DAYLIGHT_FACTOR_WITH_LUMINAIRES_MESSAGE
        return refuse(message, [Advisory(code="DAYLIGHT_FACTOR_WITH_LUMINAIRES", severity="error", field="luminaires", message=message)])

    if scale_max is not None and scale_max <= 0.0:
        message = (
            f"scale_max={scale_max!r} must be greater than 0. A falsecolor scale of zero or less "
            "has no useful meaning (rad_core.views.choose_scale). Omit scale_max to compute it "
            "from the rendered image's own maximum."
        )
        return _refuse_argument(message, field="scale_max")

    quality_for_resolve: str | dict[str, Any] = quality
    if isinstance(quality, dict) and "av" in quality:
        # Permitted only in mode="hdr" (checked above), and stripped here because resolve_quality
        # does not accept an "av" key at all -- _apply_av_override reapplies it below, onto the
        # QualityParameters resolve_quality actually returns (ruling R327).
        quality_for_resolve = {key: val for key, val in quality.items() if key != "av"}
    try:
        parameters = resolve_quality(quality_for_resolve)
    except (ValueError, ArithmeticError) as exc:
        return _refuse_argument(str(exc), field="quality")
    parameters = _apply_av_override(parameters, quality, mode)

    preset_name = quality.get("base", "standard") if isinstance(quality, dict) else quality

    try:
        resolved_view = _resolve_view(view, roots=runtime.roots)
    except ViewTypeError as exc:
        return _refuse_argument(str(exc), field="view")
    except UnknownKeysRefused as exc:
        return refuse(str(exc), exc.advisories)

    resolution_values = resolution if resolution is not None else [1280, 720]
    if len(resolution_values) != 2:
        message = f"resolution must be [width, height] (2 values); got {resolution_values!r}."
        return _refuse_argument(message, field="resolution")
    try:
        width, height = int(resolution_values[0]), int(resolution_values[1])
    except (TypeError, ValueError):
        message = f"resolution must be two numbers [width, height]; got {resolution_values!r}."
        return _refuse_argument(message, field="resolution")
    if width <= 0 or height <= 0:
        message = f"resolution must both be positive; got {resolution_values!r}."
        return _refuse_argument(message, field="resolution")

    resolved_supersample = (
        supersample if supersample is not None else SUPERSAMPLE_BY_PRESET.get(preset_name, 1)
    )
    if resolved_supersample <= 0:
        message = f"supersample must be a positive integer; got {resolved_supersample!r}."
        return _refuse_argument(message, field="supersample")
    rendered_w, rendered_h = rendered_size(width, height, resolved_supersample)

    advisories: list[Advisory] = [
        _supersample_advisory(rendered_w, rendered_h, resolved_supersample, width, height)
    ]
    if preset_name == "draft":
        advisories.append(_quality_draft_advisory())

    # **Ruling R452 (the owner), reversing R434: the project is opened above `if dry_run:`.** Until
    # then a dry run returned before it could consult the manifest, and so reported success on a
    # pre-Phase-10 unscaled project every live render refuses -- the one run tool whose preview
    # disagreed with its real call. Every argument-shape check still precedes this line.
    loaded = runtime.open(project)
    refuse_unscaled_geometry(loaded)

    # Phase 11a spec §7's after-open refusals, also under `dry_run`, before the sky is resolved:
    # none placed, the R489 run bound, then R487's source.cal and file checks -- `verify_lit_files`
    # writes nothing (R569); the rewrite it defers to is `commit_lit_support`, below, never on a dry
    # run. Lit: `lit_advisories` (R578 -- `DEFAULTS_SUBSTITUTED`/`RESULT_IS_INITIAL_ILLUMINANCE`, the
    # same function `run_point_in_time` calls for its own `switch_advisories`). Unlit with
    # placements: `LUMINAIRES_EXCLUDED` at warn, never for a daylight factor (R479) -- both branches
    # feed the one `advisories` list `dry_run` and the real run share.
    if luminaires:
        verify_lit_files(runtime, loaded)
        # R821: `RenderResult` has no `values`; the notice is about the image, `hdr`.
        advisories.extend(lit_advisories(loaded.manifest, quantity=quantity, result_field="hdr"))
    elif quantity != "daylight_factor":
        excluded = excluded_advisory(loaded.manifest, severity="warn", tool="run_render")
        if excluded is not None:
            advisories.append(excluded)

    # **I2 (whole-branch fix wave): the scene list is read above `if dry_run:`**, as
    # `run_point_in_time`'s `_validate_scene` is. It used to be read after `commit_lit_support`, so a
    # project with placements and no geometry passed its dry run, and the real call rewrote
    # `source.cal` and re-registered the support record before refusing `OCTREE_EMPTY`. Reading it
    # here spawns nothing and writes nothing; the sky file is appended to it below, once written.
    geometry_scene_files = _scene_files(
        loaded,
        luminaires=luminaires,
        no_geometry_recovery=(
            "Register some with geometry(action='add') first -- there is nothing to build an "
            "octree from."
        ),
    )

    # R694: a non-finite north on a hand-edited manifest is refused here, above `dry_run`, by the
    # one check all three sky tools share. A lamp-only run (luminaires, no sky) generates no sky, so
    # its north is never read.
    north = None if luminaires and sky is None else _project_north(loaded)

    # R719: the sky's keys are checked above `dry_run`; `_resolve_sky` runs after it (also above
    # `dry_run` since the final review's FW-2) and keeps the same check as its backstop, as R389 did
    # for the daylight-factor sky.
    if key_advisories := sky_key_advisories(sky):
        return refuse(" ".join(a.message for a in key_advisories), key_advisories)

    # R753 final review (D10): ruling R33's scan over the files the real call compiles -- geometry,
    # materials and, when lit, the placements -- above `dry_run`, so a dry run refuses a scene the
    # real call refuses. `build_octree` scans again against the bytes `oconv` reads; the sky file,
    # written below, is this server's own.
    _scan_scene_for_inline_commands(geometry_scene_files, cwd=loaded.paths.root)
    # R818/R819: what the scene holds that this render does not model -- a mirror or prism in the
    # files it compiles, and a BSDF bound to a window group -- read here, above `dry_run`, so the
    # preview and the render carry the same warnings.
    advisories.extend(a for a in (
        light_redirecting_advisory(geometry_scene_files, tool="run_render"),
        bsdf_not_applied_advisory(loaded, tool="run_render"),
    ) if a is not None)

    # R762: the site is resolved above `dry_run`, after the key check and the scan above, so a dry
    # run refuses a siteless sky exactly as the real run does (check 3.9's rule), and a hostile
    # scene is reported ahead of it.
    sky_inputs = resolve_sky_inputs(runtime, loaded, sky)
    advisories.extend(sky_inputs.advisories)

    # Final review FW-2: the sky is resolved and its dates checked above `dry_run`, so a dry run
    # refuses the Perez skies the real run refuses (a site of its own and no source, both measured
    # pairs). `_resolve_sky` builds a `SkySpec` and spawns nothing; its result is reused below.
    ground_reflectance = loaded.manifest.project.ground_reflectance
    sky_spec, sky_advisories = _resolve_sky(
        quantity=quantity, sky_arg=sky, ground_reflectance=ground_reflectance, north=north,
        luminaires=luminaires, site=sky_inputs.site, epw_irradiance=sky_inputs.epw_irradiance,
    )
    # A lamp-only run (Phase 11a spec §7, R458) has no sky to validate or write -- `sky_spec` is
    # `None` only when `luminaires=true` left `sky` omitted.
    if sky_spec is not None:
        validate_sky_dates(sky_spec)
    advisories.extend(sky_advisories)

    if dry_run:
        return emit(RenderDryRunOk(advisories=advisories or None))

    paths = ProjectPaths(loaded.paths.root)
    env = discover()

    irradiance = quantity in ("illuminance", "daylight_factor")

    # R568/R569/R577: a lit run's only project write -- rewriting an earlier package's source.cal,
    # or healing a support record an earlier rewrite never saved -- happens here, after every
    # refusal above and immediately before the first spawn (the sky generator just below, or the
    # octree build when this is a lamp-only run with no sky to generate), and never on a dry run.
    if luminaires:
        commit_lit_support(runtime, loaded, tool="run_render")

    disc = _run_discriminator()
    sky_path = None
    if sky_spec is not None:
        sky_path, generator_advisories = _write_sky(env, paths, sky_spec, disc=disc, timeout=inline_timeout_s)
        advisories.extend(generator_advisories)

    scene_files = [*geometry_scene_files, *([str(sky_path)] if sky_path else [])]
    # **Discriminated, not a fixed `_run_render.oct` (whole-branch fix wave, B6).** `build_octree`
    # writes through `rad_engine.run.run_to_file`, which opens its destination with
    # `safe_io.open_regular_file(..., "wb")` -- `O_WRONLY | O_CREAT | O_TRUNC`, and no lock. This
    # tool is a plain `def`, so FastMCP runs it in a worker thread and two `run_render` calls on
    # one project can overlap: with one filename between them, call B truncated and rewrote the
    # octree call A's `rpict` was still reading. The discriminator was already separating every
    # image this call writes; the octree and the sky description were the two files left sharing a
    # name between calls.
    #
    # **Reasoned from the code, not reproduced** -- the window is the interval between B's truncate
    # and A's last read, and a test that lost that race would prove nothing either way. The fix is
    # cheap and removes the shared name rather than timing it.
    #
    # The cost is one octree file per call in `paths.root` where there used to be one in total.
    # That is the same posture every other output of this call already has (`render_{disc}.hdr` and
    # its `_filtered`/`_falsecolor` siblings, `render_{disc}.<ldr>`), and it is deliberate: an
    # octree deleted after the render would be gone from exactly the audit trail this phase's
    # `export` action exists to reconstruct.
    #
    # **The two inherited sites are untouched.** `rad_mcp.glare_tool`'s `_run_glare.oct` and
    # `rad_mcp.point_in_time_tool`'s `_point_in_time.oct` carry the same shape and predate this
    # phase, and `_point_in_time.oct` is cited twice in `rad_mcp.window_group_tool` (the module
    # docstring and `_OBSTRUCTION_OCTREE_NAME`'s own comment) as the convention that tool's scratch
    # octree follows -- "a fixed literal name this tool owns and overwrites on every call that needs
    # it". Changing it is a decision about that convention, not a fix to this tool.
    # (2026-09-27, R730: `run_point_in_time`'s octree is no longer at the root -- it is registered
    # at `06_octrees/point_in_time.oct` -- so `_run_glare.oct` is the one inherited site left.)
    octree_path = paths.root / f"_run_render_{disc}.oct"
    octree = build_octree(env, scene_files, out_path=octree_path, cwd=paths.root, timeout=inline_timeout_s)

    hdr_out = _contain_written_path(
        paths.images_hdr / f"render_{disc}.hdr", roots=runtime.roots, what="rendered HDR"
    )
    hdr_out.parent.mkdir(parents=True, exist_ok=True)

    # Phase 11a spec §7/R494: a lit render passes `-dt 0 -dc 1` to `rpict`, the same forcing
    # `run_point_in_time` applies to `rtrace`, so no lamp in an array is culled by the statistical
    # shadow-testing approximation (R492). `sky_has_sun=False`: this module has no sun-detection of
    # its own (R557 -- no existing `direct_exact_forced` call here to widen), so only the
    # `luminaires` branch of that function can ever fire from this call site.
    parameters, direct = direct_exact_forced(
        parameters, sky_has_sun=False, quantity=quantity, luminaires=luminaires
    )
    if direct is not None:
        advisories.extend(to_wire_all([direct]))

    argv = build_rpict_argv(
        resolved_view, parameters, width=rendered_w, height=rendered_h,
        irradiance=irradiance, octree=octree.path,
    )
    rendered = spawn_render(env, argv, out_path=hdr_out, cwd=paths.root, timeout=inline_timeout_s)
    advisories.extend(to_wire_all(classify(list(rendered.warnings))))

    current_path = rendered.path
    pfilt_argv = build_pfilt_argv(supersample=resolved_supersample)
    if pfilt_argv:
        pfilt_out = paths.images_hdr / f"render_{disc}_filtered.hdr"
        current_path, pfilt_warnings = spawn_postprocess(
            env, pfilt_argv, stdin_path=current_path, out_path=pfilt_out,
            cwd=paths.root, timeout=inline_timeout_s,
        )
        advisories.extend(to_wire_all(classify(list(pfilt_warnings))))

    info = read_image(env, current_path, cwd=paths.root, timeout=inline_timeout_s)
    scale = choose_scale(info.statistics, scale_max=scale_max)
    if quantity == "daylight_factor" and scale_max is None:
        # Ruling R812: `build_falsecolor_argv` adds `-m 1.79` here, so falsecolor reads `-s` in DF %,
        # while `read_image`'s maximum is lux. The chosen maximum is converted into the legend's
        # unit (see `DAYLIGHT_FACTOR_LUX_PER_PERCENT`); a caller's `scale_max` is already in it.
        # `decades` is a ratio of the image's own extrema and does not change.
        scale = dataclasses.replace(
            scale, maximum=scale.maximum / DAYLIGHT_FACTOR_LUX_PER_PERCENT,
        )
    if scale.decades > WIDE_RANGE_DECADES and scale_log is None:
        # R331: the same non-positive-minimum condition `choose_scale` itself clamps on -- see
        # `_wide_range_advisory`'s own docstring, and `rad_mcp.image_tool`'s identical fix (R329).
        clamped = info.statistics.minimum <= 0.0 and info.statistics.maximum > 0.0
        advisories.append(_wide_range_advisory(scale.decades, clamped=clamped))

    if mode == "falsecolor":
        fc_argv = build_falsecolor_argv(
            quantity=quantity, scale_max=scale.maximum, levels=levels,
            legend_label=legend_label, contour=False,
        )
        if scale_log is not None:
            fc_argv = [*fc_argv, "-log", str(int(scale_log))]
        fc_out = paths.images_hdr / f"render_{disc}_falsecolor.hdr"
        current_path, fc_warnings = spawn_postprocess(
            env, fc_argv, stdin_path=current_path, out_path=fc_out,
            cwd=paths.root, timeout=inline_timeout_s,
        )
        advisories.extend(to_wire_all(classify(list(fc_warnings))))

    if "png" not in available_ldr_formats(env):
        advisories.append(_ldr_format_substituted_advisory())
    ldr_out = _contain_written_path(
        paths.images_ldr / f"render_{disc}.{DEFAULT_LDR_FORMAT}",
        roots=runtime.roots, what="converted LDR image",
    )
    ldr_out.parent.mkdir(parents=True, exist_ok=True)
    ldr_path, ldr_warnings = spawn_postprocess(
        env, [LDR_CONVERTERS[DEFAULT_LDR_FORMAT]], stdin_path=current_path, out_path=ldr_out,
        cwd=paths.root, timeout=inline_timeout_s,
    )
    advisories.extend(to_wire_all(classify(list(ldr_warnings))))

    ambient_radius = ambient_interpolation_radius_m(parameters, octree.bbox[3])

    result = RenderResult(
        hdr=str(current_path),
        ldr=str(ldr_path),
        view=RenderViewPayload(
            type=resolved_view.view_type,
            position=list(resolved_view.position),
            direction=list(resolved_view.direction),
            up=list(resolved_view.up),
            vh=resolved_view.vh,
            vv=resolved_view.vv,
        ),
        scale=RenderScalePayload(
            maximum=scale.maximum, chosen=scale.chosen, decades=scale.decades,
            clipped_fraction=scale.clipped_fraction,
        ),
        ambient_interpolation_radius_m=ambient_radius,
        luminaires=lit_placements_view(loaded.manifest) if luminaires else None,
        advisories=advisories or None,
    )
    return emit(result)
