"""The six sourced `rpict`/`rvu` view types, and the geometry that goes with them.

Pure: this module runs no binary and opens no file. `rad_engine.render` builds argv from what is
here; `rad_core.glare` takes its fisheye subset from `VIEW_TYPES` rather than restating it.

**The table is transcribed, not invented.** `docs/specs/workflows-point-in-time-to-annual.md`
lines 218-223 publish six `-vt` letters with their names, descriptions and primary use cases
(ruling R319: the caption is line 214, the header row 216, the separator 217, and the six data
rows are 218-223 -- not 219-224). All six are here and nothing else is: `-vt` takes exactly one
letter and an unknown one is refused rather than defaulted to perspective, because a caller who
mistypes a view type and silently gets a rectilinear image has a picture of a fraction of the
visual field with no indication of it (docs/plan.md check 9.1).

Also carries the falsecolor scale arithmetic (`FalsecolorScale`, `choose_scale`,
`clipped_fraction`) -- pure for the same reason the view table is, so it lives here rather than in
`rad_engine` (docs/plan.md check 9.3).
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

__all__ = [
    "FalsecolorScale",
    "VIEW_TYPES",
    "View",
    "ViewTypeError",
    "ViewTypeSpec",
    "WIDE_RANGE_DECADES",
    "angle_between_deg",
    "choose_scale",
    "clipped_fraction",
    "fisheye_row_spans",
    "parse_view_file",
    "vt_flag",
]


class ViewTypeError(ValueError):
    """A `-vt` letter this project does not publish."""


@dataclass(frozen=True)
class ViewTypeSpec:
    """One row of the sourced table."""

    letter: str
    name: str
    description: str
    primary_use: str


#: The six rows of workflows-point-in-time-to-annual.md's view-type table (lines 218-223),
#: transcribed. Keyed by the `-vt` letter.
VIEW_TYPES: dict[str, ViewTypeSpec] = {
    "v": ViewTypeSpec("v", "Perspective", "Standard rectilinear projection.",
                      "Architectural visualization, realistic views."),
    "l": ViewTypeSpec("l", "Parallel", "Orthographic projection with no perspective.",
                      "Elevations, plans, axonometric views."),
    "c": ViewTypeSpec("c", "Cylindrical Panorama",
                      "Perspective projection vertically, cylindrical horizontally.",
                      "Creating 360° panoramic images."),
    "h": ViewTypeSpec("h", "Hemispherical Fisheye",
                      "Equidistant projection of a 180° hemisphere onto a circle.",
                      "Required for evalglare analysis."),
    "a": ViewTypeSpec("a", "Angular Fisheye",
                      "Distance from image center is proportional to view angle.",
                      "Can capture a full 360° view; also suitable for glare analysis."),
    "s": ViewTypeSpec("s", "Planisphere", "Stereographic projection that preserves angles.",
                      "Sun-path diagrams and other astronomical visualizations."),
}


def vt_flag(view_type: str) -> str:
    """`"h"` -> `"-vth"`. Anything not in `VIEW_TYPES` raises, and the message never names a
    substitute -- a refusal that suggests `-vtv` is one a caller can act on by accident."""
    if view_type not in VIEW_TYPES:
        published = ", ".join(f"'{k}' ({v.name})" for k, v in sorted(VIEW_TYPES.items()))
        raise ViewTypeError(
            f"View type {view_type!r} is not one this server publishes. The six sourced types "
            f"(docs/specs/workflows-point-in-time-to-annual.md) are: {published}."
        )
    return f"-vt{view_type}"


@dataclass(frozen=True)
class View:
    """A complete view: type, position, direction, up vector and both angles."""

    view_type: str
    position: tuple[float, float, float]
    direction: tuple[float, float, float]
    up: tuple[float, float, float]
    vh: float
    vv: float


def angle_between_deg(a: Sequence[float], b: Sequence[float]) -> float:
    """The angle between two direction vectors, in degrees, in [0, 180].

    Magnitude is divided out, so a unit vector and a 7-unit vector in the same direction give 0.
    A zero-length vector **raises** rather than returning NaN: Task 15's facing check compares the
    result against 90, and `nan < 90` is False, which would silently refuse a legal call.
    """
    na = math.sqrt(sum(c * c for c in a))
    nb = math.sqrt(sum(c * c for c in b))
    if na == 0.0 or nb == 0.0:
        raise ValueError("A zero-length vector has no direction, so no angle is defined for it.")
    cosine = sum(x * y for x, y in zip(a, b, strict=True)) / (na * nb)
    return math.degrees(math.acos(max(-1.0, min(1.0, cosine))))


def parse_view_file(text: str) -> View:
    """Parse one Radiance view line -- the `-vt? -vp x y z -vd x y z -vu x y z -vh a -vv a` shape
    `rvu`/`rpict` accept and a `.vf` file holds.

    Unknown tokens (`-vo`, `-va`, `-vs`, `-vl` and others a real `.vf` line carries) are skipped
    rather than rejected -- what makes that safe is the required-field check below: a line missing
    any of `-vp`, `-vd`, `-vu`, `-vh`, `-vv` raises rather than returning a partially-filled
    `View`.
    """
    tokens = text.split()
    view_type: str | None = None
    vectors: dict[str, tuple[float, float, float]] = {}
    angles: dict[str, float] = {}
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if token.startswith("-vt") and len(token) == 4:
            view_type = token[3]
            i += 1
        elif token in {"-vp", "-vd", "-vu"}:
            vectors[token] = (float(tokens[i + 1]), float(tokens[i + 2]), float(tokens[i + 3]))
            i += 4
        elif token in {"-vh", "-vv"}:
            angles[token] = float(tokens[i + 1])
            i += 2
        else:
            i += 1
    if view_type is None:
        raise ViewTypeError("This view line states no -vt view type, and none is assumed.")
    vt_flag(view_type)  # raises on an unpublished letter
    missing = [k for k in ("-vp", "-vd", "-vu") if k not in vectors] + [
        k for k in ("-vh", "-vv") if k not in angles
    ]
    if missing:
        raise ViewTypeError(f"This view line is missing {', '.join(missing)}, and none is assumed.")
    return View(
        view_type=view_type,
        position=vectors["-vp"],
        direction=vectors["-vd"],
        up=vectors["-vu"],
        vh=angles["-vh"],
        vv=angles["-vv"],
    )


#: FALSECOLOR_SCALE_LINEAR_WIDE_RANGE fires above this many decades of dynamic range in the
#: image's own statistics ("the image spans more than two decades and a linear scale was used",
#: docs/tool-surface.md section 12). Interior daylight commonly spans three to four decades between
#: a sunlit patch and the back of a room.
WIDE_RANGE_DECADES = 2.0

#: `choose_scale`'s clamp for a zero (or negative) `stats.minimum`, expressed as a fraction of
#: `stats.maximum` rather than as an absolute luminance -- see `choose_scale`'s docstring for why.
_MIN_CLAMP_FRACTION = 1e-6


@dataclass(frozen=True)
class FalsecolorScale:
    """The `-s` value a falsecolor render is built with, and what it costs.

    A falsecolor map whose scale was silently clipped is a picture of the scale, not the scene
    (docs/tool-surface.md section 12) -- that is why `clipped_fraction` exists alongside `maximum`
    rather than a caller being left to infer clipping from `maximum` and the image alone.
    """

    maximum: float
    chosen: bool
    decades: float
    clipped_fraction: float | None


def choose_scale(stats, *, scale_max: float | None) -> FalsecolorScale:
    """Pick (or accept) the `-s` value for a falsecolor render, from the image's own statistics.

    `stats` is data, not a type: anything exposing `.minimum`, `.maximum` and `.mean` as floats
    (the shape of `rad_engine.image.ImageStatistics`) satisfies this. `rad_core` never imports
    `rad_engine` (see this module's own docstring and `tests/test_purity.py`), so this function
    cannot name the real class and duck-types on those three attributes instead.

    With `scale_max=None`, `maximum` is `stats.maximum` and `chosen` is `True` -- the value this
    task exists to compute, in place of a round number pulled from the air (docs/tool-surface.md
    section 12: `-s 500`/`-s 2000` in the fixture scripts are that scene's numbers, not defaults).
    A caller-supplied `scale_max` is reported as `chosen=False`.

    **A zero (or negative) `maximum` raises rather than being accepted.** `falsecolor -s 0` has no
    useful meaning, and silently substituting `1.0` would publish a scale that describes nothing --
    this applies whether the zero came from an all-black image's own maximum or from the caller.

    **`decades` describes the image, not the chosen scale**, and is always `log10(stats.maximum /
    effective_minimum)` regardless of `scale_max` -- FALSECOLOR_SCALE_LINEAR_WIDE_RANGE ("the image
    spans more than two decades") is a fact about the rendered scene, and must not change just
    because a caller picked a smaller `-s`.

    **A zero (or negative) `stats.minimum` does not make `decades` infinite.** A real HDR render
    has zero-valued pixels wherever a ray hit nothing, and `log10(x / 0)` is infinite. The brief for
    this task allows clamping to "the smallest positive value present" among the raw pixels, but
    this function is given only the image's aggregate statistics (`minimum`/`maximum`/`mean`), not
    the pixel sequence itself, so that alternative is not available here -- the denominator is
    always clamped to `stats.maximum * 1e-6` when `stats.minimum` is not positive. `_MIN_CLAMP_
    FRACTION` is an unsourced choice with its own row in docs/tool-surface.md's Unsourced values
    table (added Phase 9 Task 11 fix round 1, R329(c)) -- **this sentence used to say the opposite,
    that nothing published `decades` and so no row was needed. That was true when Task 9 wrote it
    and became false at Phase 9 Task 10, uncorrected since**: `rad_mcp.render_tool.
    RenderScalePayload.decades` has published this field since that task, and `rad_mcp.image_tool.
    ImageScalePayload.decades` (Task 11) now does too. Both publish this clamp's fabricated
    `log10(1e6) == 6.0` verbatim whenever an image's minimum is not positive -- an ordinary case
    for a real HDR render, not an edge one -- and each caller is responsible for not presenting
    that figure as a measurement when the clamp fired. Both do, as of Phase 9 Task 11 fix round 2
    (R331): `rad_mcp.image_tool._wide_range_advisory` (R329) and `rad_mcp.render_tool.
    _wide_range_advisory` (R331) each check the identical condition this function clamps on and
    build a different message when it fired, rather than reporting `decades` as a measurement.

    **An all-black image (`stats.maximum == 0.0`) together with an explicit positive `scale_max`
    does not raise `ZeroDivisionError` (ruling R326, Phase 9 Task 10).** `maximum` above is
    `scale_max` in that case, not `stats.maximum`, so the `maximum <= 0.0` guard above does not
    catch it -- the clamp below used to divide `image_maximum / effective_minimum` as `0.0 /
    (0.0 * 1e-6)`, i.e. `0.0 / 0.0`, which Python raises rather than returning `nan` for. Measured:
    `choose_scale(_Stats(0.0, 0.0, 0.0), scale_max=500.0)` raised before this fix. An all-black
    render (a view facing an unlit wall, an interior room with no sky visible) is realistic input a
    caller can trigger by picking a bad view, not a synthetic edge case, and `run_render` (Phase 9
    Task 10) is the first live caller of this function. `decades=0.0` for an all-black image is the
    honest answer regardless of what clamp is used for the (moot) minimum: an image with no light
    in it has no dynamic range to report, and `0.0` cannot be mistaken for
    `FALSECOLOR_SCALE_LINEAR_WIDE_RANGE`'s "more than two decades" condition the way a fabricated
    large number could.
    """
    if scale_max is None:
        maximum = float(stats.maximum)
        chosen = True
    else:
        maximum = float(scale_max)
        chosen = False
    if maximum <= 0.0:
        raise ValueError(
            "A falsecolor scale of zero (or less) has no useful meaning -- `falsecolor -s 0` "
            "would publish a scale that describes nothing, whether the zero came from an "
            "all-black image's own maximum or from a caller-supplied scale_max."
        )
    image_maximum = float(stats.maximum)
    image_minimum = float(stats.minimum)
    if image_maximum <= 0.0:
        # An all-black image: `decades` has nothing to divide, whatever `scale_max` says. See this
        # function's own docstring, R326 paragraph.
        decades = 0.0
    else:
        effective_minimum = (
            image_minimum if image_minimum > 0.0 else image_maximum * _MIN_CLAMP_FRACTION
        )
        decades = math.log10(image_maximum / effective_minimum)
    return FalsecolorScale(maximum=maximum, chosen=chosen, decades=decades, clipped_fraction=None)


def clipped_fraction(values: Sequence[float], scale_max: float) -> float:
    """The fraction of `values` strictly greater than `scale_max`.

    `falsecolor -s N` maps `N` to the top of the scale, which is displayed, not lost -- the
    comparison is strict (`>`), not `>=`, so a pixel exactly at the scale is not counted as clipped.

    Reports the **fraction**, not the bare fact that clipping happened: a scale that clips 0.1% of
    an image's pixels and one that clips 40% of them both "have clipping", but only the second
    makes the falsecolor map a picture of the scale rather than the scene (docs/tool-surface.md
    section 12) -- FALSECOLOR_SCALE_CLIPPED is built to carry this number, not a boolean.

    Raises on an empty sequence rather than dividing by zero.
    """
    if not values:
        raise ValueError("clipped_fraction of an empty image is not defined.")
    clipped = sum(1 for value in values if value > scale_max)
    return clipped / len(values)


def fisheye_row_spans(width: int, height: int) -> tuple[tuple[int, int], ...]:
    """For each scanline, top first, the half-open column range `[first, stop)` of the pixels
    lying **wholly** inside the ellipse inscribed in a `width` x `height` picture (ruling R764).
    `(0, 0)` for a row that holds none.

    For a 180-degree hemispherical or angular fisheye the inscribed circle is the stated field;
    outside it `rpict` writes zero (`-vth`) or directions beyond 90 degrees (`-vta`). A pixel is
    inside when all four of its corners are, which keeps out the rim pixels whose centre is inside
    and whose value is zero (measured: 125 of them on `high_fish_view.hdr`, plan M-B4).

    Exact integer arithmetic: in doubled coordinates a corner is `(X, Y) = (2c - W, H - 2r)`, and
    the ellipse `(X/W)^2 + (Y/H)^2 <= 1` becomes `X^2 H^2 + Y^2 W^2 <= W^2 H^2`, so a corner on the
    circle is never decided by rounding. Checked against the four-corner predicate on every size
    1..59 x 1..59 (plan M-B4b); a floating `sqrt` form disagreed by one pixel on 800 x 800.
    """
    if width <= 0 or height <= 0:
        raise ValueError(f"a picture of {width} x {height} pixels has no pixels to mask")
    spans: list[tuple[int, int]] = []
    for row in range(height):
        y = max(abs(height - 2 * row), abs(height - 2 * row - 2))
        x_max = math.isqrt(width * width * (height * height - y * y)) // height
        first = -(-(width - x_max) // 2)
        stop = (x_max + width - 2) // 2 + 1
        spans.append((first, stop) if first < stop else (0, 0))
    return tuple(spans)
