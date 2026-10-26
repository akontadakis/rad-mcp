"""`rad_core.glare` -- validity preconditions, interpretation bands, and the EN 17037 glare
moment block for daylight glare probability (DGP).

Pure: no Radiance binaries, no subprocesses, no file IO -- see CONTEXT.md, Layers. It consumes
a `ViewHeader` and a pixel resolution and says whether they are valid inputs for a DGP calculation,
and it consumes a DGP *value* and says what band it falls in and what EN 17037 can and cannot
conclude from it.

**What it knows about `evalglare` and about HDR files, and what that knowledge is.** This docstring
used to claim the module "knows nothing about `evalglare`, HDR files, or `rpict`", which is refuted
fifty lines below by `EVALGLARE_VIEW_TYPES` (*"The two `-vt` letters evalglare accepts"*), by
`ViewHeader`'s own *"the subset of an HDR view header"*, and by refusal text reading *"mandatory for
evalglare"*. The design is right and the sentence was wrong (Phase 8 whole-branch review, finding
I9): this module holds `evalglare`'s two legal view-type letters, the 180-degree fisheye convention
and the resolution band **as inert data**, and it *runs* nothing, *spawns* nothing and *parses* no
file. Two flag letters in a `frozenset` are not a subprocess, and knowing that a `-vh` value comes
off an HDR view header is not knowing how to read one -- `rad_engine.image.parse_view_line` does
that and hands the result here already destructured. The layer rule this module keeps is the one
CONTEXT.md actually states: no I/O and no process, not ignorance of the vocabulary its own callers
speak.

**Two threshold tables live here, and they are deliberately not merged.**

1. `interpretation_band` classifies a DGP value against the four-band scale from
   docs/specs/workflows-point-in-time-to-annual.md:417-423 ("DGP < 0.35: Imperceptible glare
   (Good)" through "DGP >=0.45: Intolerable glare (Unacceptable)"). That source writes every
   boundary as a strict `<` on the lower band, so a boundary value lands in the *upper* band. This
   table is descriptive: it names what a human perceives, with no reference to any standard's
   compliance criterion.
2. `en17037_glare_moment` compares against `EN17037_GLARE_THRESHOLDS`, EN 17037 Table A.7
   (skills/lighting-standards-compliance/chapters/ch03-en-17037-daylight.md:212-218: Minimum 0.45,
   Medium 0.40, High 0.35). That source's own words are "DGP must not exceed the threshold"
   (same file, line 194) -- "not exceed" is `<=`, not `<`, so a DGP exactly at a threshold counts
   as *below* it. This table is a compliance criterion, and it is evaluated over **usage time**,
   not an instant -- see the next paragraph.

The two tables answer different questions (what does a person perceive vs. what does the standard
require) with different operators (`<` vs `<=`) and are not interchangeable; merging them would
silently pick one operator for both questions and get one of them wrong.

**`en17037_glare_moment` cannot return a non-null `level_met`, by construction.** EN 17037's
glare criterion is that DGP must not exceed the threshold for more than 5 % of *usage time*
(ch03-en-17037-daylight.md:194) -- a fraction of a year, not a value at an instant. This function
takes a single `dgp: float` and has no parameter through which a usage-time fraction, or any other
annual quantity, could ever reach it. There is no branch, no default, and no code path in this
function that assigns anything but the literal `None` to `level_met`: the field exists to name
what a single moment cannot answer, not to compute it.
"""

from __future__ import annotations

from dataclasses import dataclass

from rad_core.views import VIEW_TYPES

__all__ = [
    "EN17037_GLARE_THRESHOLDS",
    "EVALGLARE_VIEW_TYPES",
    "FISHEYE_ANGLE_DEG",
    "MAX_RESOLUTION_PX",
    "MIN_RESOLUTION_PX",
    "PIXEL_ASPECT_TOLERANCE",
    "RECOMMENDED_RESOLUTION_PX",
    "SLOW_RESOLUTION_PX",
    "VIEW_ANGLE_TOLERANCE_DEG",
    "GlareRefusal",
    "ViewHeader",
    "check_glare_input",
    "check_render_resolution",
    "en17037_glare_moment",
    "interpretation_band",
    "resolution_notes",
]

#: The two `-vt` letters evalglare accepts, named from `rad_core.views.VIEW_TYPES` rather than
#: restated -- both are fisheye projections, and the primary_use column of each says so.
EVALGLARE_VIEW_TYPES = frozenset(
    letter for letter, spec in VIEW_TYPES.items() if "Fisheye" in spec.name
)

#: "For fisheye images, this should be 180 degrees" -- for both -vh and -vv (same source).
FISHEYE_ANGLE_DEG = 180.0

#: UNSOURCED. No spec gives a tolerance on the 180 degree requirement. Half a degree is a
#: stated conservative default: wide enough for a header written as 179.999999 by a float
#: round-trip, far too narrow to admit any real alternative field of view.
#: In docs/tool-surface.md's Unsourced values table (Phase 8 Task 8).
VIEW_ANGLE_TOLERANCE_DEG = 0.5

#: "The recommended image size is 1000x1000 pixels, with a minimum of 800x800" (same source).
MIN_RESOLUTION_PX = 800
RECOMMENDED_RESOLUTION_PX = 1000

#: "should not exceed 1500x1500 pixels for runtime efficiency" -- runtime, NOT validity, so it
#: is a warning and never a refusal.
SLOW_RESOLUTION_PX = 1500

#: UNSOURCED. The ceiling above which a *requested* render resolution is refused rather than
#: warned. No spec states one: the sourced figure above is an efficiency limit and is deliberately
#: only a warning, and nothing in the corpus says at what size a fisheye render stops being a
#: study and becomes a denial of the server's own worker pool.
#:
#: **It decides refuse-versus-run and never a computed value** -- the same class of number as
#: `rad_engine.suns._GRAZING_DOT`, and the reason a chosen bound is acceptable here where a chosen
#: reflectance would not be. 4000 is 2.67x the sourced 1500 efficiency limit, so a caller who
#: deliberately wants more than the source recommends still has the whole 1500-4000 band (with
#: `GLARE_RESOLUTION_SLOW` on every step of it); at 16 megapixels it is roughly seven times the
#: pixel count the source itself calls the point where runtime suffers. Measured here, the case it
#: exists for: the office fixture at `resolution=20000, quality="maximum"` (400 megapixels) was
#: killed cleanly by its bound with no orphaned `rpict`, leaving a partial `.hdr` whose size is
#: simply how far the render got -- 563 bytes at a 3 s bound, 2 682 at 20 s, 7 880 at 60 s. (The
#: review reported 2 805 343 bytes; re-measured that figure does not reproduce, because it is a
#: function of the timeout and the scene rather than of the resolution. The defect is unchanged: a
#: file with a valid Radiance header and a truncated pixel body is left on disk either way.)
#:
#: **The two directions of error are not symmetric.** Too low and a legitimate high-resolution
#: study is refused, which the caller can see and argue with. Too high and the server accepts a
#: render it cannot finish, holds one of its worker slots for the whole bound, and leaves a
#: truncated HDR that looks like an output. In docs/tool-surface.md's Unsourced values table.
MAX_RESOLUTION_PX = 4000

#: UNSOURCED. No spec gives a tolerance on the square-pixel assumption.
#: In docs/tool-surface.md's Unsourced values table (Phase 8 Task 8).
PIXEL_ASPECT_TOLERANCE = 0.01

#: EN 17037 Table A.7, read from
#: skills/lighting-standards-compliance/chapters/ch03-en-17037-daylight.md:212-218.
EN17037_GLARE_THRESHOLDS = {"high": 0.35, "medium": 0.40, "minimum": 0.45}


@dataclass(frozen=True)
class ViewHeader:
    """The subset of an HDR view header `check_glare_input` needs. `position` and `direction`
    are carried through for later tasks (they are not examined by this module) but are part of
    the fixed signature later dispatches quote."""

    view_type: str
    vh_deg: float | None
    vv_deg: float | None
    position: tuple[float, float, float] | None
    direction: tuple[float, float, float] | None
    shift: float = 0.0
    """`-vs`, the view's horizontal shift in image widths; Radiance's default 0 when the line states
    none (R771). Read so `rad_engine.image` can tell a centred fisheye from a shifted one."""
    lift: float = 0.0
    """`-vl`, the vertical lift in image heights; default 0 (R771)."""


@dataclass(frozen=True)
class GlareRefusal:
    """One reason a glare calculation cannot proceed. `field` is a stable machine-readable key;
    `reason` is the human-readable sentence."""

    field: str
    reason: str


def check_glare_input(
    view: ViewHeader | None, resolution: tuple[int, int]
) -> list[GlareRefusal]:
    """Validity preconditions for a DGP calculation. Returns every fault found, in the fixed
    order `view_type`, `view_angles`, `resolution`, `pixel_aspect` -- never just the first, so a
    caller who fixes one fault learns every other fault still standing rather than calling again
    to discover it one at a time.

    A missing `view` is a `view_type` refusal, never an assumed fisheye: silently substituting a
    default view type would validate a header that was never actually inspected. A missing angle
    is likewise a `view_angles` refusal, never defaulted to 180: a caller who omitted the angle
    gets told to supply it, not told their (unstated) input passed.
    """

    refusals: list[GlareRefusal] = []

    if view is None:
        refusals.append(
            GlareRefusal(
                field="view_type",
                reason=(
                    "No view header was supplied. A hemispherical ('h') or angular ('a') "
                    "fisheye view is mandatory for evalglare."
                ),
            )
        )
        # Every remaining check reads fields off `view`; with no view there is nothing more to
        # say about it, and no view angles or pixel aspect to check against.
        x, y = resolution
        if x < MIN_RESOLUTION_PX or y < MIN_RESOLUTION_PX:
            refusals.append(
                GlareRefusal(
                    field="resolution",
                    reason=(
                        f"Resolution {x}x{y} is below the minimum {MIN_RESOLUTION_PX}x"
                        f"{MIN_RESOLUTION_PX} evalglare requires."
                    ),
                )
            )
        return refusals

    if view.view_type not in EVALGLARE_VIEW_TYPES:
        refusals.append(
            GlareRefusal(
                field="view_type",
                reason=(
                    f"View type '{view.view_type}' is not a fisheye view. A hemispherical "
                    "('h') or angular ('a') fisheye view is mandatory for evalglare."
                ),
            )
        )

    vh, vv = view.vh_deg, view.vv_deg
    angles_present = vh is not None and vv is not None
    angles_valid = (
        angles_present
        and abs(vh - FISHEYE_ANGLE_DEG) <= VIEW_ANGLE_TOLERANCE_DEG
        and abs(vv - FISHEYE_ANGLE_DEG) <= VIEW_ANGLE_TOLERANCE_DEG
    )
    if not angles_valid:
        vh_str = "missing" if vh is None else str(vh)
        vv_str = "missing" if vv is None else str(vv)
        refusals.append(
            GlareRefusal(
                field="view_angles",
                reason=(
                    f"View angles -vh {vh_str} -vv {vv_str} must both be "
                    f"{FISHEYE_ANGLE_DEG:g} degrees for a fisheye view."
                ),
            )
        )

    x, y = resolution
    if x < MIN_RESOLUTION_PX or y < MIN_RESOLUTION_PX:
        refusals.append(
            GlareRefusal(
                field="resolution",
                reason=(
                    f"Resolution {x}x{y} is below the minimum {MIN_RESOLUTION_PX}x"
                    f"{MIN_RESOLUTION_PX} evalglare requires."
                ),
            )
        )

    # Only evaluated once the angles are themselves valid: the ratio this check forms
    # (vh_deg / x vs. vv_deg / y) is a secondary, finer-grained check on top of the fisheye
    # angle precondition above, and re-derives the same fault from mismatched angles alone if
    # run against angles that have already been refused as `view_angles`.
    if angles_valid:
        x_px, y_px = resolution
        if x_px > 0 and y_px > 0:
            ratio_a = vh / x_px
            ratio_b = vv / y_px
            if abs(ratio_a - ratio_b) > PIXEL_ASPECT_TOLERANCE:
                refusals.append(
                    GlareRefusal(
                        field="pixel_aspect",
                        reason=(
                            f"Image {x_px}x{y_px} is not square-pixel for a "
                            f"{vh:g}x{vv:g} degree fisheye view; the equidistant fisheye "
                            "projection assumes square pixels."
                        ),
                    )
                )

    return refusals


def check_render_resolution(resolution: int) -> list[GlareRefusal]:
    """Validity preconditions for a resolution a caller asked this server to *render at*, as
    against `check_glare_input`'s question about an image it has already read.

    The two are different questions and this is deliberately not folded into that function.
    `check_glare_input` inspects a picture that exists: it has a view header, two axes, and a pixel
    aspect ratio to check. This one is answered from a single integer argument, before anything is
    rendered, and it is the only place the **ceiling** applies -- an existing image above
    `MAX_RESOLUTION_PX` costs nothing to read and is accepted, while asking this server to *produce*
    one is asking it to spend a worker slot on a render it may not finish.

    Returns `[]` or one `GlareRefusal` on `field="resolution"`, in the same shape
    `check_glare_input` returns, so the tool layer folds both through one refusal builder rather
    than growing a second.

    **Ordering, and the defect this closes** (Phase 8 whole-branch review, finding I2): `run_glare`
    used to reach `check_glare_input` only *after* rendering, so `resolution=500` paid for `gensky`,
    `oconv` and a full `-ab 5 -ad 512` fisheye and was then refused for a fact knowable from the
    argument alone -- while `rad_engine.rpict` published the opposite as fact, that the floor "is
    enforced at the tool layer before this function is ever called."
    """
    if resolution < MIN_RESOLUTION_PX:
        return [
            GlareRefusal(
                field="resolution",
                reason=(
                    f"resolution={resolution} is below the minimum {MIN_RESOLUTION_PX}x"
                    f"{MIN_RESOLUTION_PX} evalglare requires. Refused before rendering rather "
                    "than after: the render would be discarded by the same floor."
                ),
            )
        ]
    if resolution > MAX_RESOLUTION_PX:
        return [
            GlareRefusal(
                field="resolution",
                reason=(
                    f"resolution={resolution} is above the {MAX_RESOLUTION_PX}x"
                    f"{MAX_RESOLUTION_PX} ceiling this server will render a fisheye at. That "
                    "ceiling is a chosen bound, not a sourced one (docs/tool-surface.md, Unsourced "
                    f"values): the sourced figure is {SLOW_RESOLUTION_PX}x{SLOW_RESOLUTION_PX}, "
                    "above which runtime suffers, and everything between that and this ceiling is "
                    "accepted with GLARE_RESOLUTION_SLOW. A render this large holds a worker slot "
                    "for its whole timeout and is likely to be killed part-written."
                ),
            )
        ]
    return []


def resolution_notes(resolution: tuple[int, int]) -> list[tuple[str, str, str]]:
    """Runtime (not validity) notes about `resolution`. Returns `[]` at or below
    `SLOW_RESOLUTION_PX` on both axes; above it on either axis, one
    `("GLARE_RESOLUTION_SLOW", "warn", message)` triple. This is deliberately never a refusal:
    the sourced 1500x1500 figure is about runtime efficiency, not validity.
    """

    x, y = resolution
    if x <= SLOW_RESOLUTION_PX and y <= SLOW_RESOLUTION_PX:
        return []
    return [
        (
            "GLARE_RESOLUTION_SLOW",
            "warn",
            (
                f"Resolution {x}x{y} exceeds {SLOW_RESOLUTION_PX}x{SLOW_RESOLUTION_PX}, "
                "the sourced limit above which evalglare's runtime suffers. This is a "
                "runtime concern, not a validity refusal."
            ),
        )
    ]


def interpretation_band(dgp: float) -> str:
    """Classify `dgp` against the four-band descriptive scale
    (docs/specs/workflows-point-in-time-to-annual.md:417-423). Every boundary uses strict `<`
    on the lower band, so a boundary value (0.35, 0.40, 0.45) lands in the *upper* band, matching
    the source's own `<` / `<=` mix written out band by band."""

    if dgp < 0.35:
        return "imperceptible"
    if dgp < 0.40:
        return "perceptible"
    if dgp < 0.45:
        return "disturbing"
    return "intolerable"


def en17037_glare_moment(dgp: float) -> dict:
    """What EN 17037 Table A.7 can and cannot say about a single DGP value `dgp`.

    `level_met` is always `None` -- see the module docstring: EN 17037's criterion is an
    annual, 5 %-of-usage-time exceedance test, and this function has no way to receive a usage
    fraction. `moment_below` names every threshold this one instant is under, built with
    `dgp <= threshold` ("must not exceed" -- ch03-en-17037-daylight.md:194), ordered `high`,
    `medium`, `minimum`.
    """

    moment_below = [
        level
        for level in ("high", "medium", "minimum")
        if dgp <= EN17037_GLARE_THRESHOLDS[level]
    ]

    return {
        "level_met": None,
        "moment_below": moment_below,
        "thresholds": dict(EN17037_GLARE_THRESHOLDS),
        "statement": (
            f"DGP at this moment is {dgp:g}. EN 17037's glare criterion requires DGP to not "
            "exceed the threshold for more than 5 % of usage time over a full year; a single "
            "image cannot establish or refute that. Only an annual usage-time series of DGP can "
            "determine which recommendation level, if any, this space meets, and this value is "
            "not one."
        ),
    }
