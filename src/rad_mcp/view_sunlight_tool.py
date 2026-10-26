"""`run_view_and_sunlight` -- docs/superpowers/specs/2026-09-21-phase-12-view-out-and-sunlight-design.md.

EN 17037's two axes view out (5.2, Table A.5, Annex C) and exposure to sunlight (5.3, Table A.6,
Annex D), over a frozen octree. Task 9 built the **argument surface and its refusals** (design §3,
§7): the published signature, the per-axis required-argument checks (one code and one `field` per
missing argument -- R14), the date window, and the rejection of an argument that belongs to the
other axis. **Task 10 built the `view_out` branch**, end to end: the named grid and the project's
window groups read, one `rtrace` spawn cast over them, and `level_met` published from
`rad_core.view_out`. **Task 11 built the `sunlight_exposure` branch**: the committed weather's
site read, the reference day walked in True Solar Time, one shadow-ray `rtrace` spawn for every
aperture's surviving steps, and the apertures' intervals unioned into Table A.6's level. With both
branches built nothing refuses as "not implemented" any more, and the `_refuse_unbuilt` seam Task 9
left for them is gone.

**Task 12 attached the advisories** (design section 8, rulings T12.1-T12.3): every code design
section 8 attaches to every call of a branch is attached to every call of it, `EN17037_SINGLE_AXIS`
to both; `EN17037_SUNLIGHT_REVEAL_NOT_MODELLED`, the design's own conditional code, fires on its
detection (ruling T12.2); and the codes ruling T12.1 added -- `EN17037_VIEW_NO_LANDSCAPE_LAYER`,
`EN17037_VIEW_SIGHT_ANGLE_HORIZONTAL_OPENING`, `EN17037_SUNLIGHT_BLOCKED_BY_GLAZING` -- fire only
on the condition that would otherwise leave a published number silent about its cause. Which of
them a `dry_run` carries is decided in `_view_out_advisories` and `_sunlight_advisories`: every
code knowable before a ray is cast, and none that needs one.

**Layering (design §6): no geometry and no physics live in this module.** Every view-out angle,
level and aggregation is *computed* by `rad_core.view_out`, and every solar position, True Solar
Time, point P, interval union and sunlight level by `rad_core.sunlight`; the `rtrace` spawn and its
parsing are `rad_engine.visibility`'s, and the sun's facing test is `rad_engine.suns`'. What lives
here is argument validation, the project-state checks, the grouping that decides which values go
to which of those functions, and the result shape. One rule
the standard does not state -- how two openings combine into the distance criterion's single level
-- is chosen here and argued in `ViewDistanceCriterion`.

**There is no `quality` argument, and the omission is deliberate** (design §3): `-ab`, `-ad`, `-as`
and `-ar` govern the ambient calculation, and both axes read only the first intersection's point and
modifier from an `-ab 0` cast, so no quality setting could move the answer.

**`inline_timeout_s` keeps `run_point_in_time`'s 20 s default, not `run_ugr`'s 60 s** (design §3):
R671's exception for `run_ugr` is the `rtrace -I` behind `E_total`, whose cost grows with the
scene's luminaire count; this tool casts no `-I` ray and runs no ambient calculation, so the shared
default stands.

**`axis` is a bare `str`, not `Literal["view_out", "sunlight_exposure"]`, on the wire.** Measured
against `run_point_in_time`'s own `quantity: Quantity` parameter: a `Literal` mismatch is rejected
by pydantic's argument-model validation *before* the tool function ever runs, as an uncoded
`ToolError` with no `structuredContent` -- exactly the failure design §7's "axis absent or unknown"
row requires this module to answer with a coded `ARGUMENT_OUT_OF_RANGE` refusal instead. So `axis`
(and the six other arguments this task refuses on their face) stay loosely typed and are validated
here, the same posture `run_ugr.quality` already takes for the identical reason.
"""

from __future__ import annotations

import logging
import math
import os
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Any, Final, Literal

from mcp_types import CallToolResult
from pydantic import BaseModel, ConfigDict, Field, RootModel, StrictFloat

# `_HORIZONTAL_TILT_DEG` is private to `rad_core.apertures`, and read here only so that
# `EN17037_VIEW_SIGHT_ANGLE_HORIZONTAL_OPENING`'s message states the band `is_horizontal` actually
# applies rather than a copy of it that could drift (Task 12 fix round 1, M4).
from rad_core.apertures import _HORIZONTAL_TILT_DEG, Aperture, ApertureError, is_horizontal
from rad_core.geometry import Point3
from rad_core.sunlight import (
    SUNLIGHT_STEP_MINUTES,
    PointPUnreachable,
    SunStep,
    model_azimuth_deg,
    opening_reach_m,
    point_p,
    reference_day,
    reveal_is_modelled,
    reveal_probe_directions,
    step_runs,
    sunlight_level,
    union_intervals,
)
from rad_core.view_out import (
    LAYER_AREA_FRACTION,
    LEVEL_RANK,
    SAMPLES_PER_OPENING,
    Layer,
    Level,
    distance_spread,
    layer_area_fractions,
    layer_of,
    layers_level,
    overall_level,
    sample_opening,
    sight_angle_deg,
    sight_angle_spread,
)
from rad_engine import (
    ProjectError,
    ProjectPaths,
    RadianceError,
    RadianceNotFound,
    SceneInlineCommandError,
    build_octree,
    discover,
)
from rad_engine.env import RadianceEnv
from rad_engine.octree import EmptyOctreeError, OctreeSetOverflowError, OctreeTooManyFilesError
from rad_engine.project import LoadedProject
from rad_engine.rtrace import RtraceOutputError
from rad_engine.safe_io import NotARegularFileError
from rad_engine.suns import SunPosition, exterior_normal, suns_in_hemisphere
from rad_engine.visibility import Sighting, cast
from rad_mcp.envelope import Advisory, RadResult, Refusal, emit, one_of_schema, refuse, refuse_missing
from rad_mcp.paths import PathOutsideRoot, RootsNotConfigured
from rad_mcp.timeouts import refuse_inline_timeout
from rad_mcp.unknown_arguments import RetiredMessage
# `_project_apertures` is read from `glare_tool` rather than copied: it is the one place this
# server turns a manifest's `window_group` records into `rad_core.apertures.Aperture`s, and its
# docstring records decisions (which groups are skipped, which errors propagate) that a second copy
# would drift from -- R30's "one arithmetic path by construction" applied to plumbing. It is a
# *glare* module only by where it happens to live, which is the same complaint design section 6
# makes about `parse_rtrace_hits` living in `rad_engine.glarendx`; moving it to a shared home is
# the same behaviour-preserving refactor and is not this task's to make.
from rad_mcp.glare_tool import _project_apertures
from rad_mcp.point_in_time_tool import (
    _METADATA_TIMEOUT_S,
    DEFAULT_INLINE_TIMEOUT_S,
    _scan_scene_for_inline_commands,
    _scene_files,
    _validate_scene,
)
from rad_mcp.runtime import Runtime, current, refuse_engine_error
from rad_mcp.sky_site import RegisteredSite as _Site
from rad_mcp.sky_site import RegisteredSiteError, read_registered_sites

logger = logging.getLogger(__name__)

__all__ = [
    "LayersCriterion",
    "SunlightAperture",
    "SunlightExposureResult",
    "SunlightSite",
    "ViewDistanceDistribution",
    "SightAngleCriterion",
    "ViewDistanceCriterion",
    "ViewOutCriteria",
    "ViewOutResult",
    "ViewSunlightBase",
    "ViewSunlightDryRunOk",
    "ViewSunlightPayload",
    "ViewSunlightResponse",
    "run_view_and_sunlight",
    "RETIRED_ARGUMENTS",
]

#: `run_view_and_sunlight` takes no `action` argument, so -- like `run_ugr`/`run_electric` before it
#: -- it has no destructive action of its own; `tests/test_protocol.py`'s per-tool map is pinned to
#: the tools it names and this constant is what a future entry there would read (Files, not this
#: task's own: task-9-brief.md names only this module, `server.py` and this task's own test file).
VIEW_SUNLIGHT_DESTRUCTIVE_ACTIONS: frozenset[str] = frozenset()

#: design §3: the two axes EN 17037's four-axis shape allows this tool to assess in one call.
_AXES: Final[frozenset[str]] = frozenset({"view_out", "sunlight_exposure"})

#: design §3: the arguments only `axis="view_out"` requires, with no default, and that
#: `axis="sunlight_exposure"` therefore refuses rather than silently ignores. `floor_height_m` is
#: in neither tuple: since R698 (owner decision 2) it is required on BOTH axes -- the floor point P
#: is measured up from on one, and the floor the eye is measured up from on the other.
_VIEW_OUT_ONLY: Final[tuple[str, ...]] = ("grid", "eye_above_floor_m")

#: The mirror image: the arguments only `axis="sunlight_exposure"` requires, refused under
#: `axis="view_out"`. `floor_height_m`, required by both, is not here (R698).
_SUNLIGHT_ONLY: Final[tuple[str, ...]] = ("reference_date", "min_solar_altitude_deg")

#: design §3/§7: `reference_date` must fall in 1 February .. 21 March inclusive -- Table D.1's
#: solar altitudes are conditioned on 21 March and the 1,5 h level, and NOTE 1 names the CEN
#: capitals' evaluation window. Compared as `(month, day)` so the check does not care which year the
#: caller wrote, and both tuples are themselves inside the window: a strict `<`/`>` comparison would
#: refuse the two dates the standard itself names (`test_the_boundary_dates_are_inside_the_window`).
_WINDOW_START: Final[tuple[int, int]] = (2, 1)
_WINDOW_END: Final[tuple[int, int]] = (3, 21)


def _refuse_argument(message: str, *, field: str) -> CallToolResult:
    """`ARGUMENT_OUT_OF_RANGE` with the argument's own name in `field` -- ruling R14's code for an
    argument refused on its face. The same helper `point_in_time_tool`/`image_tool` each define for
    themselves rather than share (`ugr_tool`'s own module docstring records the reason: the code,
    the field and the sentence are this tool's own and are not `run_point_in_time`'s to drift from)."""
    return refuse(
        message,
        [Advisory(code="ARGUMENT_OUT_OF_RANGE", severity="error", field=field, message=message)],
    )


def _refuse_axis(axis: str | None) -> CallToolResult | None:
    """design §7, row 1: `axis` absent or not one of the two literals it defines. See the module
    docstring for why `axis` is typed as a bare `str | None` rather than a `Literal` that pydantic
    would reject before this function ever ran."""
    if axis is None:
        return _refuse_argument(
            "run_view_and_sunlight requires axis, one of 'view_out' or 'sunlight_exposure'. It has "
            "no default: the two axes take different arguments, and a substituted axis would "
            "publish a level for a criterion nobody asked to assess "
            "(see docs/tool-surface.md#required-with-no-default).",
            field="axis",
        )
    if axis not in _AXES:
        return _refuse_argument(
            f"axis={axis!r} is not one of 'view_out' or 'sunlight_exposure'. EN 17037 assesses four "
            "independent axes with no composite score; this tool computes these two and no other "
            "string names a third.",
            field="axis",
        )
    return None


def _refuse_other_axis_arguments(axis: str, **candidates: Any) -> CallToolResult | None:
    """design's domain point: *"An argument belonging to the other axis is REFUSED, not ignored."*

    Every present argument that belongs to the axis **not** selected is refused together, one
    `Advisory` per argument (R14's shape, the same one `refuse_missing` gives a bundle of absent
    arguments) rather than only the first found -- so a caller who passed two wrong-axis arguments
    at once learns about both in one round trip rather than fixing them one refusal at a time.
    """
    present = sorted(name for name, value in candidates.items() if value is not None)
    if not present:
        return None
    other = "sunlight_exposure" if axis == "view_out" else "view_out"
    reason = (
        f"{', '.join(present)} belong to axis={other!r}, not axis={axis!r}. "
        "run_view_and_sunlight refuses an argument that belongs to the other axis rather than "
        "silently ignoring it -- silently ignoring a value is how a caller comes to believe it was "
        "used."
    )
    return refuse(
        reason,
        [
            Advisory(code="ARGUMENT_OUT_OF_RANGE", severity="error", field=name, message=reason)
            for name in present
        ],
    )


def _refuse_required_for_axis(
    axis: str, *, grid: str | None, eye_above_floor_m: float | None, reference_date: str | None,
    min_solar_altitude_deg: float | None, floor_height_m: float | None,
) -> CallToolResult | None:
    """design §7, rows 2-3: the per-axis required arguments, one `ARGUMENT_OUT_OF_RANGE` `field` per
    missing one (R14) -- then the cross-axis rejection for whichever axis was **not** selected.
    `floor_height_m` is required by both axes since R698 and so is never the other axis's."""
    if axis == "view_out":
        if (refusal := refuse_missing(
            "run_view_and_sunlight", grid=grid, eye_above_floor_m=eye_above_floor_m,
            floor_height_m=floor_height_m,
        )) is not None:
            return refusal
        return _refuse_other_axis_arguments(
            axis, reference_date=reference_date, min_solar_altitude_deg=min_solar_altitude_deg,
        )
    # axis == "sunlight_exposure" -- `_refuse_axis` above has already ruled out every other value.
    if (refusal := refuse_missing(
        "run_view_and_sunlight", reference_date=reference_date,
        min_solar_altitude_deg=min_solar_altitude_deg, floor_height_m=floor_height_m,
    )) is not None:
        return refusal
    return _refuse_other_axis_arguments(axis, grid=grid, eye_above_floor_m=eye_above_floor_m)


def _refuse_date_window(reference_date: str) -> CallToolResult | None:
    """design §7, row 4: `reference_date` outside 1 February .. 21 March, naming the window and that
    the date is the caller's to choose within it. A malformed ISO string is refused the same way --
    there is no code for a parse failure distinct from an out-of-range one, and both are the caller's
    argument to fix."""
    try:
        parsed = date.fromisoformat(reference_date)
    except (TypeError, ValueError):
        return _refuse_argument(
            f"reference_date={reference_date!r} is not a valid ISO date (YYYY-MM-DD).",
            field="reference_date",
        )
    if not (_WINDOW_START <= (parsed.month, parsed.day) <= _WINDOW_END):
        return _refuse_argument(
            f"reference_date={reference_date!r} is outside 1 February .. 21 March, the window Table "
            "D.1's minimum criteria and NOTE 1's True Solar Time evaluation are stated for. Choose a "
            "date inside that window -- both 1 February and 21 March are themselves inside it.",
            field="reference_date",
        )
    return None


def _refuse_min_solar_altitude(min_solar_altitude_deg: float) -> CallToolResult | None:
    """Ruling T12.4: a negative cut-off is refused, and so is a non-finite one. Ruling R701 (owner
    decision 6) adds the top: a cut-off above 90 is refused too.

    3.19 defines exposure to sunlight as time during which the sun is *above the horizon*. A cut-off
    below 0 admits steps with the sun below it, whose shadow rays point downward; on a model with
    no ground those rays escape and are counted as sunlight -- a silent wrong number rather than an
    error. 0 itself is accepted: it is the horizon, and a caller asking for no cut-off beyond it
    still has one. A non-finite value is refused with it (an extension of the ruling's words, not
    of its reason): NaN fails the altitude `>=` in `_build_sun_rays`, so every step is dropped and
    the call publishes 0 h for a window in full sun.

    Above 90 is the same failure mode from the other side: no sun is ever higher than the zenith, so
    every step is dropped there too, silently. 90 itself is accepted -- it is the zenith, not above
    it -- so the bound is `<= 90.0`, not `< 90.0`. A caller who reaches it anyway on a real day (no
    site on Earth sees the sun at 90 on every reference date) is warned by
    `EN17037_SUNLIGHT_CUTOFF_ABOVE_DAY_PEAK` instead of refused a second time.
    """
    if math.isfinite(min_solar_altitude_deg) and 0.0 <= min_solar_altitude_deg <= 90.0:
        return None
    return _refuse_argument(
        f"min_solar_altitude_deg={min_solar_altitude_deg!r} is not a finite number of degrees "
        "between 0 and 90 inclusive. EN 17037 3.19 defines exposure to sunlight as time with the "
        "sun above the horizon, so a cut-off below it would count steps with the sun below the "
        "horizon -- rays that point downward and, on a model with no ground, escape and read as "
        "sunlight. Pass 0 for the horizon itself, or the value Table D.1 gives for the site, and at "
        "or below 90, the zenith: no sun is ever higher.",
        field="min_solar_altitude_deg",
    )


def _eye_z_m(floor_height_m: float, eye_above_floor_m: float) -> float:
    """The eye's z in the model's frame: `floor_height_m + eye_above_floor_m`, added as decimals.

    **The one place the sum is formed (R711).** Every use reads it from here: the eye rays'
    origins, the published `eye_z_m`, the utilized-area advisory and the overflow refusal. Branch
    B formed it at four sites as a binary float sum, which can publish residue -- a 0,15 m floor
    and a 1,2 m eye gave `1.3499999999999999`, and the eye was placed there. Adding the two values'
    shortest reprs as `Decimal`s gives `1.35`, the number the caller wrote, with no rounding
    constant chosen; the eye is placed at that same value, so the echo and the geometry match.
    The sum is exact while it fits `decimal`'s default 28 significant digits -- any two heights
    within about ten orders of magnitude of each other; beyond that it is rounded to 28 digits
    before the conversion to `float`.

    A non-finite height keeps binary float arithmetic's answer (`inf`, NaN) rather than
    `decimal`'s `InvalidOperation` for `inf + -inf`, so no caller of this can have a refusal turned
    into an exception. `_refuse_non_finite_heights` refuses every non-finite height before any
    other caller is reached.

    **Near the largest double the decimal sum can stay finite where the binary one overflowed.**
    `1.7976931348623157e308 + 1e292` is `inf` in binary but rounds back to
    `1.7976931348623157e308` here (measured), so `_refuse_non_finite_heights` now accepts that pair,
    which it refused before R711. No practical effect: no model is 1e308 m tall. A pair whose
    decimal sum is itself past the largest double (`1e308 + 1e308`) still gives `inf` and is still
    refused.
    """
    if not (math.isfinite(floor_height_m) and math.isfinite(eye_above_floor_m)):
        return floor_height_m + eye_above_floor_m
    return float(Decimal(repr(floor_height_m)) + Decimal(repr(eye_above_floor_m)))


def _refuse_non_finite_heights(
    *, eye_above_floor_m: float | None, floor_height_m: float | None
) -> CallToolResult | None:
    """Final whole-branch review, I2 / S-M1: a non-finite `floor_height_m` or eye height is
    refused, as `ARGUMENT_OUT_OF_RANGE` with its own `field`, before the project is opened -- so a
    `dry_run` refuses it too. JSON's `1e999` parses to `inf`, so a wire caller can send one.

    **Since R698 (owner decision 2) the eye height checked is `eye_above_floor_m`**, which replaced
    the absolute `eye_height_m` the paragraph below was measured against; it is refused at or
    below 0 as well ("an eye at or below the floor"), because it is now a height above the floor
    and not a coordinate. `floor_height_m` is still a coordinate and still only has to be finite.

    Measured before this check, on the phase's fixtures: a NaN `floor_height_m` on `office_south`
    made point P NaN, and the real call escaped uncoded as `RtraceOutputError` ("rtrace returned 0
    rows for 610 rays") while its `dry_run` previewed success; a NaN or infinite `eye_height_m` on
    `office` escaped uncoded as "Out of range float values are not JSON compliant", raised when
    the result echoing it was serialised. (The security review reported the second as a published
    `level_met: null`; that is not what this measurement shows.) Only finiteness is checked: no
    range of heights is refused, because a floor below the model's origin or an eye below z = 0 is
    a legitimate coordinate (`eye_height_m` is an absolute model z, not a height above the floor).
    That paragraph describes the check as it stood before R698: since then the eye is range-checked
    too, as the paragraph above says, and "only finiteness" holds of `floor_height_m` alone.

    **Two finite heights can still sum to a non-finite eye z** (Phase 13 Branch B final fix wave,
    item 9), and the sum is what places the eye, so it is checked too, when both are given.
    Measured before this check on a scratch copy of the `office` room, built with the same builder
    and arguments as `office`: `floor_height_m=1e308, eye_above_floor_m=1e308` made the dry run
    preview success with its advisory stating the eye at "inf m", while the real call escaped
    uncoded as "Out of range float values are not JSON compliant: inf". It is refused under
    `eye_above_floor_m`, the argument that is the eye's own offset from the floor.
    """
    if eye_above_floor_m is not None and not (
        math.isfinite(eye_above_floor_m) and eye_above_floor_m > 0.0
    ):
        return _refuse_argument(
            f"eye_above_floor_m={eye_above_floor_m!r} is not a finite number of metres above 0. "
            "It is the eye's height above floor_height_m -- 1.2 seated or 1.7 standing (Annex "
            "C.4.1) -- and a value at or below 0 puts the eye at or below the floor.",
            field="eye_above_floor_m",
        )
    if floor_height_m is not None and not math.isfinite(floor_height_m):
        return _refuse_argument(
            f"floor_height_m={floor_height_m!r} is not a finite number of metres. It is a z "
            "coordinate in the model's own frame, and a non-finite one places no point anywhere.",
            field="floor_height_m",
        )
    if (eye_above_floor_m is not None and floor_height_m is not None
            and not math.isfinite(_eye_z_m(floor_height_m, eye_above_floor_m))):
        return _refuse_argument(
            f"floor_height_m + eye_above_floor_m = {floor_height_m!r} + {eye_above_floor_m!r} is "
            "not a finite number of metres. Each is finite, but their sum is the eye's z in the "
            "model's frame, and a sum that overflows places the eye nowhere.",
            field="eye_above_floor_m",
        )
    return None


def _refuse_inline_timeout(inline_timeout_s: float) -> CallToolResult | None:
    """`inline_timeout_s` goes through the one shared spawn-timeout check (Phase 13 Branch C, ruling
    R704 -- `rad_mcp.timeouts.refuse_inline_timeout`), which closes the gap this docstring used to
    document: a finite value too large for the spawn (`1e9`, `1e30`, `threading.TIMEOUT_MAX`) was
    **not** refused here before R704. **That gap was not "past the `dry_run` return" -- re-measured
    against a copy of the code before R704**: this tool's own `dry_run` preview already reads the
    window groups, and reading one shells out to `xform` -- above the `dry_run` return, not below
    it (`dry_run`'s own argument description says so: *"the preview opens the project, validates the
    named grid and reads the window groups -- and reading a group's aperture shells out to
    `xform`"*). So a too-large value fired on `dry_run=True` itself, on a project with a real
    window group, not only on a live call; the handler chain's `ArithmeticError` clause caught it
    there and turned it into a code-less refusal rather than an uncoded escape (confirmed by log:
    `run_view_and_sunlight failed in arithmetic: OverflowError('timeout is too large')`,
    `refused: true`, no advisory). `rad_mcp.timeouts`'s own docstring records the measured ceiling
    (the largest value a spawn on the measuring machine accepted) and the boundary either side of
    it, so the bound below is measured, not claimed.

    `run_point_in_time` refused a non-positive value in the same words before R704, and this
    function widened that to non-finite ones (final whole-branch review, S-I2 / I2) -- both are
    now this one function.

    **No `isinstance(bool)` exemption here, and there never was one.** `inline_timeout_s` is
    `StrictFloat`, so the MCP SDK refuses a JSON `true`/`false` before this function is ever called
    on the wire (R854; until then it coerced them to `1.0`/`0.0`), exactly as on
    `point_in_time_tool` (see `rad_mcp.timeouts`'s "Booleans" paragraph). Pinned by a test over
    MCP."""
    return refuse_inline_timeout(inline_timeout_s)


def _refuse_window_group_list(window_group: list[str] | str | None) -> CallToolResult | None:
    """Ruling F.5 (final whole-branch review, I3 and S-M2): an EMPTY `window_group` list and a list
    naming one group twice are both refused, before the project is opened.

    An empty list passed the no-opening refusal -- that refusal reads the project's groups, not the
    caller's selection -- and, read off the code, went on to a level over zero openings, a verdict
    about nothing. A duplicate id was read as a second opening. Measured on `office_south` with
    the group named twice: that aperture's published `sunlight_hours` went from 10,13 to 20,27 and
    its `steps_cast` from 608 to 1216, while the union stayed 10,13 h. Read off the code, the view
    axis casts a repeated opening's rays twice too, which the result cannot show
    (`samples_per_opening` is keyed by id). `None` -- every group the project defines -- and a
    single string are not lists and are not checked here.
    """
    if not isinstance(window_group, list):
        return None
    if not window_group:
        return _refuse_argument(
            "window_group=[] names no window group, so there would be no opening to assess and a "
            "level over none would be a verdict about nothing. Omit window_group to assess every "
            "group the project defines, or name at least one.",
            field="window_group",
        )
    repeated = sorted({name for name in window_group if window_group.count(name) > 1})
    if repeated:
        return _refuse_argument(
            f"window_group names {repeated} more than once. Each window group is one opening, and "
            "a second mention would be read as a second opening -- counted twice in the rays cast "
            "and, on the sunlight axis, in that aperture's published hours. Name each group once.",
            field="window_group",
        )
    return None


# --- the view_out branch's geometry and arithmetic (design §4.1) ---------------------------------

#: How far past a sample point on the opening the **outward** ray starts, in metres. This is not a
#: sampling number and not a threshold: it is the self-intersection guard any ray tracer needs, so
#: that a ray launched exactly on the surface a previous ray was traced to cannot re-hit that same
#: surface at distance zero. 1 mm sits four orders of magnitude above Radiance's own `FTINY`
#: (`1e-7`, read off this machine's `lib/rayinit.cal:89`) and more than three below Table A.5's
#: smallest distance row (6,0 m), so it can neither
#: vanish into float noise nor move a level. It carries no `Unsourced values` row for that reason
#: -- it is not one of design section 5's two sampling numbers, both of which are Task 13's.
_OUTWARD_OFFSET_M: Final[float] = 1e-3

#: Ruling F.6 (final whole-branch review, S-I1): the most rays one call may cast. A call whose
#: count exceeds it is refused **before a ray is built**, above the `dry_run` return, because much
#: of the cost it bounds is paid before any timeout applies: every ray is built as a Python tuple
#: in one list and a line of one `rtrace` stdin string before the spawn -- and its timeout --
#: begins, and each ray's stdout row is parsed into a `Sighting` after it ends. **Measured, not argued**
#: (Apple M2, 8 GB, Radiance 6.1a, 2026-09-24; one process per run, stderr empty): the view-out
#: pipeline this module runs -- `_lattices`, `_build_rays`, `cast`, `_surviving`,
#: `_view_out_result` -- over a 6 x 4 x 3 m room with one 4,0 x 1,2 m window and a wall 12 m
#: outside it, from a 45 x 45 grid, cast 1 057 050 rays in 8,90 and 9,03 s over two runs, 6,33 and
#: 6,49 s of it inside `cast`, at a peak resident size of 1 420 and 1 469 MB; a 30 x 30 grid,
#: 469 800 rays, took 3,69 and 3,74 s. So at this ceiling the cast takes about a third of the
#: default 20 s `inline_timeout_s` on that room, and the process peaks near 1,5 GB. **Not a sourced
#: number**, and it carries an `Unsourced values` row: it errs
#: toward refusal for a legitimately large grid (which must then be split), and a scene far more
#: complex than that room is slower per ray, where `inline_timeout_s` -- not this ceiling -- is what
#: stops it, as a refusal rather than a wrong number. The refusal is coded `EN17037_TOO_MANY_RAYS`
#: (owner decision 3, 2026-09-24, ruling R695).
MAX_RAYS_PER_CALL: Final[int] = 1_000_000


def _refuse_ray_ceiling(rays: int, *, how: str) -> CallToolResult | None:
    """Ruling F.6's refusal, shared by both axes. Coded `EN17037_TOO_MANY_RAYS`, `field` None, by
    owner decision 3 of 2026-09-24 -- every sibling resource-bound refusal is coded
    (`UGR_TOO_MANY_SOURCES_TO_SAMPLE`), and the recovery spans two arguments, so no single `field`
    owns it."""
    if rays <= MAX_RAYS_PER_CALL:
        return None
    message = (
        f"this call would cast {rays} rays ({how}), above run_view_and_sunlight's ceiling of "
        f"{MAX_RAYS_PER_CALL} rays per call, and no ray has been built or cast. The ceiling bounds "
        "the memory and time a call spends building and reading its rays, which inline_timeout_s "
        "does not bound. Split the work: a smaller "
        "sensor grid (sensor_grid(action='create', polygon=...)) or fewer openings named with "
        "window_group=[...], one call each."
    )
    return refuse(message, [
        Advisory(code="EN17037_TOO_MANY_RAYS", severity="error", field=None, message=message),
    ])


@dataclass(frozen=True)
class _Sample:
    """One (reference point, view opening, lattice cell, direction) tuple a ray pair was built for.

    `cell` is `rad_core.view_out.sample_opening`'s own lattice key and is carried all the way to
    `sight_angle_deg`, which measures the azimuth union by bridging *neighbouring* samples and
    therefore has to know which two samples have nothing between them (review Critical C1).
    """

    point_index: int
    opening: str
    cell: tuple[int, int]
    direction: Point3


@dataclass(frozen=True)
class _Seen:
    """One sample whose eye ray actually reached its own opening -- i.e. one surviving direction.

    `distance_m` is the **outward** ray's distance, measured from the sample point on the opening
    and not from the eye (3.12: *"from the inner surface of view opening"*), and is `None` exactly
    when that ray escaped -- an unobstructed view, never a distance derived from the `(0,0,0)` a
    miss prints (`rad_engine.visibility`'s F1).
    """

    point_index: int
    opening: str
    cell: tuple[int, int]
    direction: Point3
    layer: Layer
    distance_m: float | None


def _unit(origin: Point3, target: Point3) -> Point3 | None:
    """The unit direction from `origin` to `target`, or `None` when there is no direction to take.

    **This is where Task 8's carried deferred minor is closed.** `rad_engine.visibility.cast`
    validates neither non-zero nor unit-length direction vectors, and deliberately so: `rad_engine`
    owns no geometry, and the guard belongs with whatever builds the directions. That is this
    function, and it is the only place the `view_out` branch builds one: `_build_rays` calls it for
    the eye ray and reuses the result for the outward ray, and nothing below either constructs a
    direction of its own. Unit length is therefore true **by construction** rather than by a check
    after the fact. Two situations have no answer -- a reference point sitting exactly on the
    sample point, and a non-finite coordinate reaching here -- and both return `None`, so the
    caller drops the sample rather than handing `rtrace` a zero vector, which `rtrace` reads as a
    ray with no direction at all. A sample dropped this way
    is counted and published (`samples_without_direction`), so it is never a silent loss.
    """
    dx, dy, dz = target[0] - origin[0], target[1] - origin[1], target[2] - origin[2]
    length = math.sqrt(dx * dx + dy * dy + dz * dz)
    if not math.isfinite(length) or length <= 0.0:
        return None
    return (dx / length, dy / length, dz / length)


def _lattices(
    openings: Sequence[tuple[str, Aperture]]
) -> dict[str, dict[tuple[int, int], Point3]]:
    """Each opening's sample lattice, laid out once (`rad_core.view_out.sample_opening`). Split out
    of `_build_rays` so the ray-count ceiling (`_refuse_ray_ceiling`, ruling F.6) can be judged on
    the lattice actually realised, above the `dry_run` return, before a single ray is built."""
    return {
        name: sample_opening([p.vertices for p in aperture.polygons], SAMPLES_PER_OPENING)
        for name, aperture in openings
    }


def _build_rays(
    eyes: Sequence[Point3],
    openings: Sequence[tuple[str, Aperture]],
    targets: dict[str, dict[tuple[int, int], Point3]],
) -> tuple[list[tuple[Point3, Point3]], list[_Sample], dict[str, int], int]:
    """Every ray this branch casts, in **one** list; the sample each pair belongs to; the lattice
    size realised per opening; and how many (eye, sample) pairs had no direction to take.

    Two rays per sample, adjacent, in this order (design section 4.1):

    1. **the eye ray**, from the reference point to a sample on the opening. Its first hit decides
       whether that direction counts at all: only a hit on the opening's own modifier does, so a
       partition standing between the eye and the window removes the direction, which is exactly
       what 5.2.2's *"as seen from the reference point"* asks for.
    2. **the outward ray**, from that same sample point, `_OUTWARD_OFFSET_M` along the same
       direction, continuing outside. Its escape decides the layer and its distance is the view
       distance.

    **Both go in one list because the second does not depend on the first's answer.** Casting only
    the surviving directions outward would need the eye rays' results first and therefore a second
    `rtrace` spawn; casting both unconditionally and discarding the outward rays of directions that
    did not survive costs one extra ray each and keeps the spawn count at one, which is
    `rad_engine.visibility`'s own stated contract.

    The opening is sampled **once**, not once per reference point: the sample points are on the
    glass and do not move with the eye. This is also why the realised lattice size is returned as
    a plain count per opening rather than divided back out of that opening's ray total -- the
    floor division this replaced would have silently reported one sample fewer for an opening the
    moment `_unit` dropped a single one of its samples, for any one eye (review M4). `targets` is
    `_lattices(openings)`, computed by the caller so the ceiling and the cast read one lattice.
    """
    rays: list[tuple[Point3, Point3]] = []
    samples: list[_Sample] = []
    without_direction = 0
    for index, eye in enumerate(eyes):
        for name, _aperture in openings:
            for cell, target in targets[name].items():
                direction = _unit(eye, target)
                if direction is None:
                    without_direction += 1
                    continue
                samples.append(
                    _Sample(point_index=index, opening=name, cell=cell, direction=direction)
                )
                rays.append((eye, direction))
                rays.append((
                    (
                        target[0] + _OUTWARD_OFFSET_M * direction[0],
                        target[1] + _OUTWARD_OFFSET_M * direction[1],
                        target[2] + _OUTWARD_OFFSET_M * direction[2],
                    ),
                    direction,
                ))
    return rays, samples, {name: len(cells) for name, cells in targets.items()}, without_direction


def _surviving(
    samples: Sequence[_Sample],
    sightings: Sequence[Sighting],
    modifiers: dict[str, frozenset[str]],
) -> list[_Seen]:
    """The samples whose eye ray struck their own opening first, with their layer and distance.

    An eye ray that **escaped** is not a survivor either: it left the model without meeting the
    glass, so it never saw the opening it was aimed at.
    """
    seen: list[_Seen] = []
    for index, sample in enumerate(samples):
        eye_ray, outward = sightings[2 * index], sightings[2 * index + 1]
        if eye_ray.escaped or eye_ray.modifier not in modifiers[sample.opening]:
            continue
        seen.append(_Seen(
            point_index=sample.point_index,
            opening=sample.opening,
            cell=sample.cell,
            direction=sample.direction,
            layer=layer_of(sample.direction, escaped=outward.escaped),
            distance_m=outward.distance_m,
        ))
    return seen


def _outward_glazing_hits(
    samples: Sequence[_Sample],
    sightings: Sequence[Sighting],
    modifiers: dict[str, frozenset[str]],
    glazing: dict[str, list[str]],
) -> dict[str, int]:
    """The window group(s) whose glazing modifier stopped a surviving sample's outward ray, and how
    many rays each stopped -- `_view_blocked_by_glazing`'s input (owner decision 9, 2026-09-24).

    A **survivor** is exactly what `_surviving` keeps -- the same eye-ray-reached-its-own-opening
    test, repeated rather than shared, so that function's own contract (the seen samples, for the
    three Table A.5 criteria) does not also have to carry an unrelated count. `glazing` is
    `_resolve_openings`'s map from every window group's aperture modifier to its group(s) -- the
    same map the sunlight branch reads for `EN17037_SUNLIGHT_BLOCKED_BY_GLAZING` -- so a modifier
    shared by two groups counts the one ray under both, exactly as that branch's own `glass_groups`
    does.
    """
    by_group: dict[str, int] = {}
    for index, sample in enumerate(samples):
        eye_ray, outward = sightings[2 * index], sightings[2 * index + 1]
        if eye_ray.escaped or eye_ray.modifier not in modifiers[sample.opening]:
            continue
        if not outward.escaped and outward.modifier in glazing:
            for group in glazing[outward.modifier]:
                by_group[group] = by_group.get(group, 0) + 1
    return by_group


# --- the published models (design §8) ----------------------------------------------------------


class ViewSunlightBase(RadResult):
    """What every `run_view_and_sunlight`-shaped answer carries. Also the union schema's shared
    base. Carries no fields of its own: each axis's result shape (`ViewOutResult`,
    `SunlightExposureResult`) extends it with its own, the same incremental path
    `image_tool.ImageBase` took across its own phase's tasks."""


# R810 moved this from the published text: design section 8's `{dry_run: true, advisories}`. The
# old list of cast-dependent codes left out `EN17037_VIEW_BLOCKED_BY_GLAZING`, which `_run_view_out`
# adds after the cast and the dry run never carries; R810 named it.
class ViewSunlightDryRunOk(ViewSunlightBase):
    """`dry_run=True` and every refusal passed: no octree is built, no ray is cast and nothing is
    written.

    `advisories` holds every code the real call would carry that is decidable before casting.
    `EN17037_VIEW_NO_LANDSCAPE_LAYER`, `EN17037_VIEW_BLOCKED_BY_GLAZING`,
    `EN17037_SUNLIGHT_REVEAL_NOT_MODELLED` and `EN17037_SUNLIGHT_BLOCKED_BY_GLAZING` need the cast
    and are never here; their absence says nothing about the real call.
    """

    dry_run: Literal[True] = True


# R810 moved this from the published text: aggregation by the least favourable reference point is
# design section 4.1's (`rad_core.view_out.SightAngleSpread` argues it). There is no
# `resolution_deg` field: it published the width of a fixed azimuth bucket, which the Phase 12
# Task 10 review measured wrong (Critical C1); `rad_core.view_out.sight_angle_deg` bridges
# neighbouring samples and needs no width. `SAMPLES_PER_OPENING`'s comment records how the sample
# count was set.
class SightAngleCriterion(BaseModel):
    """Table A.5's horizontal sight angle (5.2.2), judged at the least favourable reference point.

    `level_met` is the level of `worst_point_deg`, `null` below the 14° minimum; `median_deg` and
    `max_deg` show whether one corner decides it. Each point's angle is the union, in plan, of the
    azimuths through which it sees any opening.
    """

    model_config = ConfigDict(extra="forbid")

    level_met: Level | None
    worst_point_deg: float
    median_deg: float
    max_deg: float


# R810 moved this from the published text: this is `rad_core.view_out.DistanceSpread` on the wire,
# and that class carries the reasoning for the median and for what `null` means. R810 added the
# median rule and the `n_rays == 0` values from `distance_spread`.
class ViewDistanceDistribution(BaseModel):
    """One view opening's outside distance of view, measured from the opening (3.12), over the
    outward rays of every eye ray that reached it, from all reference points.

    `level_met` rates the median ray (the lower middle one on an even count). A `null` distance
    means unbounded, not unknown: the ray escaped the model. So all three `null` with `n_rays` > 0
    is an unobstructed view (`unobstructed_fraction` 1). `n_rays` 0 means no eye ray reached this
    opening: the distances and `level_met` are `null`, `unobstructed_fraction` is 0, and the
    opening is in `openings_not_seen`.
    """

    model_config = ConfigDict(extra="forbid")

    level_met: Level | None
    min_m: float | None
    median_m: float | None
    max_m: float | None
    unobstructed_fraction: float
    n_rays: int


# R810 moved this from the published text. Per opening, not pooled: design section 4.1's table
# ("per opening; it does not aggregate across points at all", ruling T10.11); Task 10
# first shipped one pooled distribution. Unseen openings leave the ranking under ruling F.2 (final
# whole-branch review I4): before it, with the default `window_group`, one opening the grid
# cannot see (another room's window) made the criterion `null` whatever the openings it can see.
# Lowest versus best among the seen openings was R680's open question; the owner published
# `best_seen_opening_level` beside the lowest on 2026-09-24 (decision 1). The asymmetry with the
# layers criterion is ruling T10.13: each Table A.5 layers row is met within one opening
# (chapters/ch03-en-17037-daylight.md:147-148), a sourced best-of; the standard says nothing
# comparable for distance, so the conservative direction was chosen.
class ViewDistanceCriterion(BaseModel):
    """Table A.5's outside distance of view, rated per view opening in `by_opening`.

    The outward ray continues each eye ray's line of sight, so these distances depend on the
    reference grid (`conditioned_on`) and are not a property of the opening alone. `level_met` is
    the lowest level among the openings some eye ray reached: Table A.5 does not say how openings
    combine, and the lowest is the conservative choice. `best_seen_opening_level` is the highest
    among them; `level_met` is the one the overall view-out level ranks. An opening no eye ray
    reached stays in `by_opening` with `n_rays` 0, is named in `openings_not_seen` and is not
    ranked; when none was reached, both levels are `null`. The layers criterion instead takes each
    point's best opening, since each of its rows is met within one opening.
    """

    model_config = ConfigDict(extra="forbid")

    level_met: Level | None
    best_seen_opening_level: Level | None
    aggregation: Literal["lowest_rated_opening"] = "lowest_rated_opening"
    conditioned_on: Literal["reference_grid"] = "reference_grid"
    by_opening: dict[str, ViewDistanceDistribution]
    openings_not_seen: list[str]


# R810 moved this from the published text: the 75 % header attaching to the layers column alone is
# design section 4.1's reading; `rad_core.view_out.layers_level` and `layer_area_fractions` are
# the two readings, built from one argument so they cannot drift. R810 added the per-row layer
# rule from `_opening_layers_level`.
class LayersCriterion(BaseModel):
    """Table A.5's layers criterion (5.2.1): the highest level whose layers at least
    `required_area_fraction` (75 %) of reference points see through a single opening; `null` below
    minimum. Every level needs the landscape layer: landscape alone is minimum, landscape and one
    more is medium, all three is high.

    The fraction is of reference points, not of area; the two agree only for a uniformly spaced
    grid covering the utilized area, as `sensor_grid(action="create")` lays out
    (`EN17037_VIEW_UTILIZED_AREA_IS_THE_GRID`). `area_fraction` is, per layer, the fraction of
    points that see it through at least one opening. It is diagnostic and does not decide
    `level_met`: landscape through one window and sky through another is not a medium point.
    """

    model_config = ConfigDict(extra="forbid")

    level_met: Level | None
    area_fraction: dict[str, float]
    required_area_fraction: float


class ViewOutCriteria(BaseModel):
    """The three criteria of 5.2.2 that are assessed. The fourth, glazing quality, has no numeric
    threshold in the standard and is not assessed (`EN17037_VIEW_GLAZING_NOT_ASSESSED`)."""

    model_config = ConfigDict(extra="forbid")

    sight_angle: SightAngleCriterion
    view_distance: ViewDistanceCriterion
    layers: LayersCriterion


# The field was called `rays_per_opening` until ruling T10.15, and the name was the defect. It has
# always counted samples; a caller who read `rays_per_opening: 64` and never reached this docstring
# priced the run at half its real cost, which is the silent-wrong-number class this repo keeps
# paying for. Renaming it -- and `rad_core.view_out.SAMPLES_PER_OPENING` with it -- before Task 13
# priced the constant and before Task 14 wrote it into the published tool surface was ruled cheaper
# than after either. The samples-and-rays note is kept rather than retired with the rename: the name
# now states the unit, and the note is what gives the conversion factor. This history left the
# published description under R809 (item 38); what an old payload's name meant stays there.
#
# R810 moved this from the published text: `null` is design section 8's "assessed and below
# minimum"; "not assessed" is `rad_core.compliance.en17037_object`'s `assessed: false`. The two
# rays per sample are `_build_rays`'. `eye_z_m` replaced `eye_height_m` under R698 (owner
# decision 2) and is summed as decimals by `_eye_z_m` (R711). The old example "4752 for 18
# reference points at 264 samples" left with the rest: a realised lattice is about
# `SAMPLES_PER_OPENING`, not exactly it.
class ViewOutResult(ViewSunlightBase):
    """`axis="view_out"`: EN 17037 5.2's view-out level over the reference grid `grid`.

    `level_met` is the lowest of the three criteria's levels (`aggregation`); `null` means
    assessed and below minimum, never "not assessed". The eye is at `eye_z_m` =
    `floor_height_m` + `eye_above_floor_m`, a model z; the grid's own height is not used.

    `samples_per_opening` counts samples on each opening, and each sample costs two rays per
    reference point: an eye ray and an outward ray. `criteria.view_distance.by_opening[...].n_rays`
    is a different count: the eye rays that reached that opening, pooled over all reference points.
    `samples_without_direction` counts (reference point, sample) pairs dropped because no direction
    could be taken (the eye exactly on the sample point, or a non-finite coordinate); normally 0. An older
    payload's `rays_per_opening` was this count of samples, and its `eye_height_m` was `eye_z_m`.
    """

    axis: Literal["view_out"] = "view_out"
    grid: str
    floor_height_m: float
    eye_above_floor_m: float
    eye_z_m: float
    level_met: Level | None
    aggregation: Literal["lowest_rated_criterion"] = "lowest_rated_criterion"
    criteria: ViewOutCriteria
    openings: list[str]
    n_reference_points: int
    samples_per_opening: dict[str, int]
    samples_without_direction: int


# R810 moved this from the published text: design section 10 names the site as the input a wrong
# file silently shifts. `standard_meridian_east_deg` is `-EPWLocation.time_zone_meridian_deg`,
# east-positive as D.1's `lambda_s` is written.
class SunlightSite(BaseModel):
    """The site the sun was computed for, read from the committed weather file's LOCATION line: a
    wrong weather file moves the sun with no other sign.

    `longitude_east_deg` and `standard_meridian_east_deg` are east-positive (the `.wea` meridian
    convention is west-positive).
    """

    model_config = ConfigDict(extra="forbid")

    weather: str
    place: str
    latitude_deg: float
    longitude_east_deg: float
    standard_meridian_east_deg: float


# R810 moved this from the published text: design section 4.2 ("The result publishes each
# aperture's own intervals and hours as well as the union"). `azimuth_deg` is
# `rad_core.apertures.Aperture.azimuth_deg` unchanged; the 1 mm start offset is ruling T11.6's
# (`_OUTWARD_OFFSET_M`, `_shadow_origin`).
class SunlightAperture(BaseModel):
    """One aperture's own sunlight, before the union across apertures.

    `azimuth_deg` is the aperture's facing in the model frame, degrees clockwise from model +Y,
    from its exterior normal: the number `window_group(action="define")` reported. `point_p` is
    D.2's point P in model coordinates; the shadow rays start 1 mm outside it, and that offset is
    not in `point_p`. `steps_cast` counts the steps with the sun at or above
    `min_solar_altitude_deg` and in front of the aperture, each cast as one shadow ray;
    `sunlight_hours` is at most `steps_cast * step_minutes / 60`. `intervals_tst_h` are True Solar
    Time hours.
    """

    model_config = ConfigDict(extra="forbid")

    azimuth_deg: float
    point_p: tuple[float, float, float]
    steps_cast: int
    sunlight_hours: float
    intervals_tst_h: list[tuple[float, float]]


# R810 moved this from the published text: design sections 4.2 and 8; "not assessed" is
# `rad_core.compliance.en17037_object`'s `assessed: false`. An absent north is 0 as in
# `run_glare`, `run_point_in_time` and `run_render`, and since ruling R694 a declared north turns
# their sky in the same sense (`rad_engine.sky.north_rotation_xform_argv`). R810 added Table A.6's
# thresholds from `rad_core.sunlight._TABLE_A6_SUNLIGHT_HOURS`.
class SunlightExposureResult(ViewSunlightBase):
    """`axis="sunlight_exposure"`: EN 17037 5.3's sunlight-exposure level on `reference_date`.

    `level_met` is Table A.6's level for `sunlight_hours` (at least 1.5, 3 and 4 h for minimum,
    medium and high); `null` means assessed and below minimum, never "not assessed".
    `sunlight_hours` and `intervals_tst_h` are the union over all apertures (`cumulation`), never a
    sum: D.2 cumulates apertures only "if not occurring at the same time". Each aperture's own
    share is in `by_aperture`.

    Times are True Solar Time hours (Table D.1 NOTE 1). The day walked is the local-standard-time
    day `reference_date` names; subtract `tst_minus_local_standard_time_h` from a published time to
    read local standard time (no daylight saving is applied). `north_deg` is the project's `north`,
    degrees east of north: a sun at true azimuth A is cast at model azimuth (A + `north_deg`) mod
    360, clockwise from model +Y. `north_declared` is `false` when the project declares none and 0
    was used.
    """

    axis: Literal["sunlight_exposure"] = "sunlight_exposure"
    reference_date: str
    min_solar_altitude_deg: float
    floor_height_m: float
    level_met: Level | None
    sunlight_hours: float
    cumulation: Literal["union"] = "union"
    intervals_tst_h: list[tuple[float, float]]
    true_solar_time: Literal[True] = True
    tst_minus_local_standard_time_h: float
    step_minutes: int
    location: SunlightSite
    north_deg: float
    north_declared: bool
    by_aperture: dict[str, SunlightAperture]


ViewSunlightPayload = ViewOutResult | SunlightExposureResult | ViewSunlightDryRunOk | Refusal


# --- the advisories (design section 8, rulings T12.1-T12.3) --------------------------------------
#
# Every code below is built with a literal `code=` and a literal `severity=`, which is what
# `tests/test_advisories.py`'s AST scan reads to check each against docs/tool-surface.md's register.
# Each message is a statement about what THIS code did, and each is checked against the function
# that does it, not against the design's wording where the two differ (`EN17037_VIEW_DISTANCE_
# STATISTIC` is the case: rulings T10.7, T10.11 and T10.13 changed what runs).


def _single_axis(axis: str) -> Advisory:
    return Advisory(
        code="EN17037_SINGLE_AXIS", severity="info",
        message=(
            f"This result assesses one of EN 17037's four independent axes, axis={axis!r}. The "
            "standard assesses daylight provision, view out, exposure to sunlight and glare "
            "separately and gives no composite score; nothing here combines the axes, and this "
            "result is not one."
        ),
    )


def _view_out_advisories(
    *, grid: str, eye_z_m: float, openings: Sequence[tuple[str, Aperture]]
) -> list[Advisory]:
    """Every view-out advisory decidable before a ray is cast -- so the `dry_run` return and the
    real call carry the same ones. The one that needs the cast, `EN17037_VIEW_NO_LANDSCAPE_LAYER`,
    is `_view_out_result`'s."""
    names = [name for name, _aperture in openings]
    advisories = [
        _single_axis("view_out"),
        Advisory(
            code="EN17037_VIEW_UTILIZED_AREA_IS_THE_GRID", severity="info",
            message=(
                f"The sensor grid {grid!r} stands in for the utilized area (3.20: 'fraction of the "
                "space intended to be occupied'): its points' x and y are the reference positions, "
                "and its own height is replaced by floor_height_m + eye_above_floor_m = "
                f"{eye_z_m:g} m, the eye's z in the model's own frame. The layers "
                "criterion's 75 % is applied as a fraction of those grid POINTS, which equals a "
                "fraction of area only for a uniformly spaced grid; an unevenly spaced grid weights "
                "every point equally whatever area it stands for. A grid covering the whole room is "
                "larger than the occupied area wherever part of the room is not occupied; a narrower "
                "area is a grid of that shape, sensor_grid(action='create', polygon=...)."
            ),
        ),
        Advisory(
            code="EN17037_VIEW_OPENING_IS_WINDOW_GROUP", severity="info",
            message=(
                f"Each window group read is one view opening: {names}. A.3 lets several openings "
                "'with little distance between them' be regarded as one, and does not define "
                "'little distance', so no groups were merged. A view opening (3.23) is any area of "
                "the envelope admitting a view, which is not the same set as the daylight openings "
                "a window group is defined for: an opening with no window group was not read."
            ),
        ),
        Advisory(
            code="EN17037_VIEW_GLAZING_NOT_ASSESSED", severity="warn",
            message=(
                "5.2.2 sets four criteria for view out and this level covers three: the horizontal "
                "sight angle, the outside distance of view and the layers. The fourth -- that the "
                "glazing should give a view perceived to be clear, undistorted and neutrally "
                "coloured -- has no numeric threshold in Table A.5 and is not assessed, so this "
                "level says nothing about the glazing actually installed."
            ),
        ),
        Advisory(
            code="EN17037_VIEW_METHOD", severity="info",
            message=(
                "Assessed by C.4.2's advanced method, implemented by casting rays -- from each "
                "reference point's eye to samples on each opening, and on outward from the opening "
                "-- rather than by drawing the fish-eye projection C.4.2 describes. C.4.1's "
                "simplified section-cut construction is not implemented. The two methods do not "
                "always agree (C.4.1 assumes the opening width and view distance already satisfy "
                "C.3), so a C.4.1 construction of this room may reach a different level."
            ),
        ),
        Advisory(
            code="EN17037_VIEW_DISTANCE_STATISTIC", severity="info",
            message=(
                "Each opening's outside distance of view is rated on the lower median of its "
                "outward rays (statistics.median_low: a distance a ray measured, never the mean of "
                "two), pooled over every eye ray, from every reference point, that reached that "
                "opening, with an outward ray that escaped counted as unbounded. The median is a "
                "choice -- 3.12's 'major obstructions' read as what most of the view meets -- so "
                "one near obstruction does "
                "not set the level; each opening's minimum, median, maximum and unobstructed "
                "fraction are published beside it, and they depend on the reference grid "
                "(conditioned_on). The criterion's level is the LOWEST of the levels of the "
                "openings some eye ray reached, also a choice: Table A.5 does not say how openings "
                "combine. An opening no eye ray reached is not seen from any reference point; it "
                "is named in openings_not_seen and not ranked. The layers criterion "
                "runs the other way, taking each point's best opening, because each of its rows is "
                "met within one opening."
            ),
        ),
    ]
    horizontal = [name for name, aperture in openings if is_horizontal(aperture)]
    if horizontal:
        advisories.append(Advisory(
            code="EN17037_VIEW_SIGHT_ANGLE_HORIZONTAL_OPENING", severity="warn",
            message=(
                f"Window group(s) {horizontal} lie within {_HORIZONTAL_TILT_DEG:g} degrees of "
                "horizontal (rad_core.apertures.is_horizontal). The horizontal sight angle here "
                "is the union, in plan, of the azimuths of the directions that reach each opening, and the "
                "directions from an eye to an opening overhead can point to any azimuth -- all of "
                "them, from an eye beneath it -- so the plan angle this opening contributes is not "
                "the horizontal sight angle 5.2.2 means, and the sight-angle level may be "
                "overstated. Table A.5 does apply to horizontal openings (A.3), and this opening is "
                "counted in all three criteria. Naming only the facade openings with "
                "window_group=[...] reads the sight angle without it -- and drops it from the "
                "distance and layers criteria as well, so that call assesses the facade openings "
                "alone."
            ),
        ))
    return advisories


def _no_landscape(layers: LayersCriterion) -> Advisory | None:
    """Ruling T12.1: `EN17037_VIEW_NO_LANDSCAPE_LAYER`, only when no reference point saw the
    landscape layer through any opening -- the condition under which every Table A.5 layers row
    fails and C.1 pulls the overall level to `null` with nothing else on the result saying why.
    Read off the published `area_fraction`, so the advisory and the number cannot disagree."""
    if layers.area_fraction.get("landscape", 0.0) > 0.0:
        return None
    return Advisory(
        code="EN17037_VIEW_NO_LANDSCAPE_LAYER", severity="warn",
        message=(
            "No reference point saw the landscape layer through any opening. Every row of Table "
            "A.5's layers column requires landscape, so the layers criterion is below minimum and, "
            "by C.1's lowest-rated-criterion rule, so is the overall view-out level. A ray reads "
            "as landscape only when it leaves the eye at or above the horizontal and meets a "
            "surface beyond the opening; in a model with nothing outside the window every such ray "
            "escapes and reads as sky, and a surface below the eye's horizontal -- a ground plane -- "
            "reads as ground. If the surroundings above eye level -- neighbouring buildings, "
            "terrain -- are not modelled, this level says only that the model holds nothing there "
            "to see; model them for the criterion to be assessed."
        ),
    )


def _view_blocked_by_glazing(by_group: dict[str, int]) -> Advisory:
    """Owner decision 9, 2026-09-24: view rays keep blocking at glass, same as decision 5's shadow
    rays, and this code says so. `by_group` is `_outward_glazing_hits`'s output -- window group id
    to the count of surviving samples whose outward ray's first hit carried that group's aperture
    modifier -- and is never empty when this is called.

    The ruling was first taken the other way in this same session ("pass through glass") and
    reversed: the EN 17037 chapter this repo distils (ch03) says nothing about a view seen through
    a second pane, so there is no sourced reading either way, and the owner chose the reading
    consistent with decision 5 rather than a mixed one where sun blocks at glass but sight does
    not."""
    total = sum(by_group.values())
    return Advisory(
        code="EN17037_VIEW_BLOCKED_BY_GLAZING", severity="warn",
        message=(
            f"{total} outward view ray(s) first met a surface carrying the aperture modifier of "
            f"window group(s) {sorted(by_group)} -- by group, {dict(sorted(by_group.items()))} -- "
            "and were read as an obstruction at that distance: landscape or ground, not sky. This "
            "tool treats glass outside the view opening as an obstruction, so a separately "
            "modelled outer pane or a double-skin facade shortens the view distance and removes "
            "the sky layer. EN 17037 as distilled here says nothing on view through a second pane "
            "(owner decision 9, 2026-09-24)."
        ),
    )


def _sunlight_advisories(
    *, min_solar_altitude_deg: float, reachable: bool, peak_deg: float,
) -> list[Advisory]:
    """Every sunlight advisory decidable before a ray is cast. The two that need the cast,
    `EN17037_SUNLIGHT_REVEAL_NOT_MODELLED` and `EN17037_SUNLIGHT_BLOCKED_BY_GLAZING`, are
    `_run_sunlight`'s.

    `reachable` and `peak_deg` come from `_reference_day_steps`, which `_dispatch` calls and passes
    the result of in here. `_run_sunlight` calls the same helper again to cast: one shared helper,
    so the two cannot disagree about which cut-off the reference day reaches (ruling R701, owner
    decision 6)."""
    return [
        _single_axis("sunlight_exposure"),
        Advisory(
            code="EN17037_SUNLIGHT_TRUE_SOLAR_TIME", severity="info",
            message=(
                "Every time a sunlight_exposure result publishes is True Solar Time (Table D.1 "
                "NOTE 1), computed from the committed weather file's longitude and standard "
                "meridian -- not local clock time. A computed result carries "
                "tst_minus_local_standard_time_h, the offset between the two on reference_date: "
                "subtract it from a published time to read local standard time. No daylight saving "
                "is applied."
            ),
        ),
        Advisory(
            code="EN17037_SUNLIGHT_ALTITUDE_CUTOFF", severity="info",
            message=(
                f"Sun positions below min_solar_altitude_deg = {min_solar_altitude_deg:g} degrees "
                "are not counted (Table D.1 NOTE 2). The cut-off is the caller's: Table D.1's "
                "minimum solar altitudes are stated for 21 March and for the 1,5 h minimum level "
                "(its own title), so a value read off it is conditioned on both. This tool looks "
                "nothing up and substitutes no default."
            ),
        ),
        *([] if reachable else [
            Advisory(
                code="EN17037_SUNLIGHT_CUTOFF_ABOVE_DAY_PEAK", severity="warn",
                message=(
                    "No step of the reference day has the sun at or above "
                    f"min_solar_altitude_deg = {min_solar_altitude_deg:g} degrees; the sun's "
                    f"highest altitude that day at this site is {peak_deg:.2f} degrees. Every "
                    "step is dropped, so the result is 0 h and a null level whatever the "
                    "geometry. Check the cut-off against Table D.1."
                ),
            ),
        ]),
    ]


def _reveal_not_modelled(unbounded: Sequence[str]) -> Advisory:
    """Ruling T12.2's detection is `rad_core.sunlight`'s (`reveal_probe_directions`,
    `opening_reach_m`, `reveal_is_modelled`, and the comment above them, which argues which way
    each error goes); this is only its sentence."""
    return Advisory(
        code="EN17037_SUNLIGHT_REVEAL_NOT_MODELLED", severity="warn",
        message=(
            f"Window group(s) {list(unbounded)}: a ray cast along the opening's plane, in plan, "
            "from the shadow rays' own start point just outside the glass, met no surface within "
            "the opening's own reach on at least one side. So nothing modelled bounds the opening "
            "on that side where the shadow rays leave -- as when the glass sits in a "
            "zero-thickness wall, or flush with the wall's outer face with any reveal behind it, "
            "where no shadow ray can meet it. D.6's acceptance angle, which a reveal, loggia or "
            "self-shading facade narrows, is applied here only by shadow rays meeting modelled "
            "geometry, and is not applied separately. If the real opening has a reveal the model "
            "omits, these hours are over-counted; model the reveal outside the glass and the "
            "shadow rays apply it."
        ),
    )


def _blocked_by_glazing(
    blocked: dict[str, int], glazing_hit: Sequence[str]
) -> Advisory:
    """Ruling T12.1 and Task 11's deferred M8: reports the rays glass stopped; glass blocks by owner
    decision (R702). The owner decided on 2026-09-24
    (decision 5) that glass keeps blocking: ch03 is silent on sun through a second pane. The
    ruling was first taken the other way in the same session ("pass through glass") and reversed
    for the reading consistent with decision 9's view rays -- there is no sourced answer either
    way, so the two are decided the same way rather than left to disagree."""
    total = sum(blocked.values())
    return Advisory(
        code="EN17037_SUNLIGHT_BLOCKED_BY_GLAZING", severity="warn",
        message=(
            f"{total} shadow ray(s) -- by aperture, {dict(blocked)} -- first met a surface carrying "
            f"the aperture modifier of window group(s) {list(glazing_hit)}, and were counted as "
            "blocked. This tool counts any surface a shadow ray meets as an obstruction, glass "
            "included, so a separately modelled outer pane, a double-skin facade or another "
            "opening's glass in the sun's path removes those steps from that aperture's hours. "
            "Read the hours as sun reaching point P with nothing modelled, glass included, in its "
            "path. The owner ruled that glass blocks (2026-09-24); model the outer pane without a "
            "window-group modifier only if it should not be read as glazing."
        ),
    )


class ViewSunlightResponse(RootModel[ViewSunlightPayload]):
    @classmethod
    def model_json_schema(cls, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return one_of_schema(super().model_json_schema(*args, **kwargs), ViewSunlightBase)


# --- the view_out branch, end to end (design §4.1, §7, §8) ---------------------------------------


def _resolve_openings(
    loaded: LoadedProject,
    env: RadianceEnv,
    cwd: Path,
    *,
    window_group: list[str] | str | None,
    timeout: float,
    axis: str,
) -> tuple[list[tuple[str, Aperture]], dict[str, list[str]], CallToolResult | None]:
    """This project's view openings, every window group's glazing, or the refusal that says why
    there are no openings to read.

    The middle element maps each aperture modifier of **every** window group `_project_apertures`
    reads -- not only the ones `window_group` selected -- to the group id(s) carrying it. (A group
    whose modifiers resolve to no polygon is not read, and has no glass in the scene to meet.) The sunlight
    branch reads it to recognise a shadow ray stopped by glazing (ruling T12.1,
    `EN17037_SUNLIGHT_BLOCKED_BY_GLAZING`), and another group's glass blocks a ray whether or
    not that group was selected. It is taken from the same `_project_apertures` list as the
    openings, so the two cannot disagree about which polygons are glazing.

    Design section 3: *"Apertures come from `window_group`... Each group is one view opening."*
    `window_group` names **input ids**, exactly as `run_glare`'s `dgp_window_group` already does
    (`_resolve_dgp_scope`) -- one convention for naming a registered group on the wire, not a
    second one for this tool. Default: every group the project defines -- which the sunlight
    branch of `_dispatch` then refuses when that is more than one group (ruling R699).

    Design section 7's fifth row is the empty case, and it is refused rather than answered: a view
    -out level computed over no view opening is not a low level, it is no assessment at all.
    """
    apertures = _project_apertures(loaded, env, cwd, timeout=timeout)
    if not apertures:
        what = (
            "no view opening to assess. EN 17037 5.2's criteria are all measured through one"
            if axis == "view_out" else
            "no aperture to assess. EN 17037 5.3's sunlight is measured at point P on an "
            "aperture (D.2)"
        )
        return [], {}, refuse(
            f"this project defines no window group, so run_view_and_sunlight has {what}; a level "
            "computed over no opening would be a verdict about nothing. Call "
            "window_group(action='define') first. (A group whose recorded aperture modifiers "
            "resolve to no polygon in this project's registered geometry is not among the groups "
            "read.)"
        )
    glazing: dict[str, list[str]] = {}
    for name, aperture in apertures:
        for modifier in sorted({polygon.modifier for polygon in aperture.polygons}):
            glazing.setdefault(modifier, []).append(name)
    if window_group is None:
        return apertures, glazing, None
    wanted = [window_group] if isinstance(window_group, str) else list(window_group)
    by_id = dict(apertures)
    missing = [name for name in wanted if name not in by_id]
    if missing:
        registered = ", ".join(repr(name) for name in by_id) or "none"
        message = (
            f"window_group={missing!r} is not a registered window group in this project. This "
            f"project's window groups are: {registered}. window_group takes the input id a "
            "window_group(action='define') call registered, and a group whose recorded aperture "
            "modifiers resolve to no polygon in this project's geometry is not among them."
        )
        return [], {}, _refuse_argument(message, field="window_group")
    return [(name, by_id[name]) for name in wanted], glazing, None


def _view_out_result(
    seen: Sequence[_Seen],
    *,
    grid: str,
    floor_height_m: float,
    eye_above_floor_m: float,
    n_points: int,
    openings: Sequence[str],
    samples_per_opening: dict[str, int],
    samples_without_direction: int,
    advisories: Sequence[Advisory],
) -> ViewOutResult:
    """The three criteria, each aggregated its own way (design section 4.1's table), and C.1's
    lowest-rated-criterion overall.

    **No arithmetic that decides a number lives here** (ruling T10.8): the sample layout, the
    azimuth union, the worst-point rule, the distance statistic and the layer fractions are all
    `rad_core.view_out`'s, where `tests/test_view_out.py` can reach them without a Radiance
    install. What this function does is group one flat list of surviving rays three ways -- by
    reference point and opening for the sight angle, by opening for the distance, by reference
    point and opening for the layers -- and hand each group to the function that judges it. That
    split is the whole of review Critical C1's second half: the rule that was wrong was wrong in
    `rad_mcp`, where no pure test could reach it, and every one of the ten mutations `55c189f`
    shipped with sat in the plumbing around it rather than on it.

    **`overall_level` is called twice here, with different-sized sequences.** Once over the three
    view-out criteria -- never empty, because there are always three, which is the case that
    function deliberately raises on -- and once over the levels of the openings some eye ray
    reached, which is guarded by `if rated else None`: when no opening was seen there is nothing to
    rank, and the criterion is `null` (ruling F.2).
    """
    # Sight angle: one angle per reference point, over that point's visible samples grouped by
    # opening and keyed by their lattice cell -- `sight_angle_deg` bridges neighbours, so the keys
    # are load-bearing and a flat direction list would not do.
    visible: list[dict[str, dict[tuple[int, int], Point3]]] = [{} for _ in range(n_points)]
    for item in seen:
        visible[item.point_index].setdefault(item.opening, {})[item.cell] = item.direction
    angles = [sight_angle_deg(list(per_opening.values())) for per_opening in visible]
    spread = sight_angle_spread(angles)
    sight_angle = SightAngleCriterion(
        level_met=spread.level,
        worst_point_deg=spread.worst_deg,
        median_deg=spread.median_deg,
        max_deg=spread.max_deg,
    )

    # View distance: per opening (T10.11). An opening no ray reached still appears, with n_rays 0
    # and a null level -- dropping it would hide the fact that it was assessed and saw nothing --
    # but it is left out of the lowest-opening rule and named in `openings_not_seen` (ruling F.2).
    by_opening: dict[str, list[float | None]] = {name: [] for name in openings}
    for item in seen:
        by_opening[item.opening].append(item.distance_m)
    distributions = {
        name: distance_spread(distances) for name, distances in by_opening.items()
    }
    rated = [d.level for d in distributions.values() if d.n_rays > 0]
    best = max(rated, key=LEVEL_RANK.get) if rated else None
    view_distance = ViewDistanceCriterion(
        level_met=overall_level(rated) if rated else None,
        best_seen_opening_level=best,
        openings_not_seen=[name for name, d in distributions.items() if d.n_rays == 0],
        by_opening={
            name: ViewDistanceDistribution(
                level_met=d.level, min_m=d.min_m, median_m=d.median_m, max_m=d.max_m,
                unobstructed_fraction=d.unobstructed_fraction, n_rays=d.n_rays,
            )
            for name, d in distributions.items()
        },
    )

    # Layers: one mapping per reference point, opening name -> the layers seen through THAT
    # opening. Never pooled across openings -- `layers_level`'s own contract.
    grouped: list[dict[str, set[Layer]]] = [{} for _ in range(n_points)]
    for item in seen:
        grouped[item.point_index].setdefault(item.opening, set()).add(item.layer)
    per_point: list[dict[str, frozenset[Layer]]] = [
        {name: frozenset(layers) for name, layers in point.items()} for point in grouped
    ]
    layers = LayersCriterion(
        level_met=layers_level(per_point),
        area_fraction=layer_area_fractions(per_point),
        required_area_fraction=LAYER_AREA_FRACTION,
    )
    no_landscape = _no_landscape(layers)

    return ViewOutResult(
        advisories=[*advisories, *([no_landscape] if no_landscape is not None else [])],
        grid=grid,
        floor_height_m=floor_height_m,
        eye_above_floor_m=eye_above_floor_m,
        eye_z_m=_eye_z_m(floor_height_m, eye_above_floor_m),
        level_met=overall_level(
            [sight_angle.level_met, view_distance.level_met, layers.level_met]
        ),
        criteria=ViewOutCriteria(
            sight_angle=sight_angle, view_distance=view_distance, layers=layers
        ),
        openings=list(openings),
        n_reference_points=n_points,
        samples_per_opening=samples_per_opening,
        samples_without_direction=samples_without_direction,
    )


#: The `tempfile.mkstemp` prefixes of the two branches' temporary octrees (suffix `.oct`).
#: `rad_engine.project._VISIBILITY_OCTREE_TEMP_NAME` exempts exactly these names from the orphan
#: scan, so a crash leftover does not block `project(action="delete")`; `rad_engine` may not import
#: this module, so the two spellings are held together by
#: `tests/test_project.py::test_a_view_out_or_sunlight_temp_octree_is_not_an_orphan`, which reads
#: these constants.
VIEW_OUT_OCTREE_PREFIX: Final[str] = ".view-out-"
SUNLIGHT_OCTREE_PREFIX: Final[str] = ".sunlight-"


def _cast_in_temporary_octree(
    env: RadianceEnv,
    *,
    paths: ProjectPaths,
    scene_files: Sequence[str],
    rays: list[tuple[Point3, Point3]],
    timeout_s: float,
    prefix: str,
) -> list[Sighting]:
    """Compile `scene_files` into a temporary octree at the project root, cast `rays` over it in
    one `rtrace` spawn, and remove the octree whatever happens. Both axes' casts go through here,
    so the octree's lifetime and timeout are decided once.

    The octree is R611's shape and is not registered in the manifest; `run_ugr` and `run_electric`
    already do exactly this: it is an implementation detail of one visibility cast, not an
    artifact this tool publishes a `resource_link` to.
    """
    handle, temporary = tempfile.mkstemp(dir=paths.root, prefix=prefix, suffix=".oct")
    os.close(handle)
    octree_path = Path(temporary)
    try:
        # `_METADATA_TIMEOUT_S` and not `timeout_s`: `inline_timeout_s`'s published sentence bounds
        # the physics spawn, and `run_ugr` already compiles its own temporary octree under the
        # metadata bound for the same reason.
        octree = build_octree(
            env, list(scene_files), out_path=octree_path, cwd=paths.root,
            timeout=_METADATA_TIMEOUT_S,
        )
        return cast(
            env, octree.path, origins_and_directions=rays, cwd=paths.root, timeout_s=timeout_s,
        )
    finally:
        octree_path.unlink(missing_ok=True)


def _run_view_out(
    env: RadianceEnv,
    *,
    paths: ProjectPaths,
    scene: Any,
    openings: Sequence[tuple[str, Aperture]],
    targets: dict[str, dict[tuple[int, int], Point3]],
    grid: str,
    floor_height_m: float,
    eye_above_floor_m: float,
    timeout_s: float,
    glazing: dict[str, list[str]],
    advisories: Sequence[Advisory],
) -> CallToolResult:
    """Compile the scene, cast every ray in one spawn, and publish the level.

    **The grid's own plane height is discarded** and replaced by `floor_height_m +
    eye_above_floor_m` (design section 3; R698): EN 17037's grid sits at 0,85 m, the
    daylight-provision reference plane, and the view-out eye heights are 1,2 m seated and 1,7 m
    standing above the floor. Only the points' x/y are the reference positions.

    **`luminaires=False`, and that is a stated limitation rather than an oversight** (review M2):
    the octree carries the project's registered geometry and materials and **not** its luminaire
    placements, so a pendant hanging between a reference point and the window does not block the
    view it really would block. EN 17037 5.2 is a question about the building envelope and the
    obstructions outside it, and every other view-out input (the opening, the reveal, the facing
    building) is geometry; a luminaire that matters to a sight angle is a modelling situation this
    tool does not cover, and a caller who has one should say so. `run_annual` passes `False` too
    -- for its own reason, not this one -- and `run_point_in_time` passes its caller's own
    switch.

    The octree's lifetime is `_cast_in_temporary_octree`'s, which the sunlight branch shares.

    **`glazing` is `_resolve_openings`'s map from every window group's aperture modifier to its
    group(s)** -- the same map the sunlight branch reads for `EN17037_SUNLIGHT_BLOCKED_BY_GLAZING`
    -- read here, after the cast, for `EN17037_VIEW_BLOCKED_BY_GLAZING` (owner decision 9,
    2026-09-24): view rays keep blocking at glass, same as the sunlight axis's shadow rays.
    """
    eye_z_m = _eye_z_m(floor_height_m, eye_above_floor_m)
    eyes: list[Point3] = [(point.x, point.y, eye_z_m) for point in scene.points]
    rays, samples, samples_per_opening, without_direction = _build_rays(eyes, openings, targets)
    modifiers = {
        name: frozenset(polygon.modifier for polygon in aperture.polygons)
        for name, aperture in openings
    }

    sightings = _cast_in_temporary_octree(
        env, paths=paths, scene_files=scene.scene_files, rays=rays, timeout_s=timeout_s,
        prefix=VIEW_OUT_OCTREE_PREFIX,
    )

    by_group = _outward_glazing_hits(samples, sightings, modifiers, glazing)
    return emit(_view_out_result(
        _surviving(samples, sightings, modifiers),
        grid=grid,
        floor_height_m=floor_height_m,
        eye_above_floor_m=eye_above_floor_m,
        n_points=len(eyes),
        openings=[name for name, _aperture in openings],
        samples_per_opening=samples_per_opening,
        samples_without_direction=without_direction,
        advisories=[*advisories, *([_view_blocked_by_glazing(by_group)] if by_group else [])],
    ))


# --- the sunlight_exposure branch (design §4.2, §7, §8) ------------------------------------------
#
# **Frames, stated once for the whole branch (ruling T11.4).** Two frames meet here and exactly one
# function converts between them:
#
# - TRUE-NORTH frame: `rad_core.sunlight`'s solar azimuth (3.17 Note 1, clockwise from due north).
#   Altitude is the same in both frames.
# - MODEL frame (+X, +Y, +Z of the registered geometry; azimuth clockwise from model +Y): every
#   `Aperture` field, `exterior_normal`, point P, every ray origin and every ray direction.
# - The ONE conversion is `rad_core.sunlight.model_azimuth_deg`, called in `_build_sun_rays` and
#   nowhere else. `SunPosition` is then built from the model azimuth, so its `direction` is a
#   model-frame vector -- which is the frame its own docstring says it is in.
#
# The aperture's two normals are the second trap: `Aperture.outward_normal` points INTO the room.
# The behind-the-plane test and the shadow ray's offset both use `rad_engine.suns.exterior_normal`
# (reached through `suns_in_hemisphere` for the first), never `outward_normal` and never a sign
# written here.


def _resolve_site(runtime: Runtime, loaded: LoadedProject) -> tuple[_Site | None, CallToolResult | None]:
    """The site this project's committed weather describes, or the refusal that says why there is
    none (design section 7: *"`sunlight_exposure` with no committed weather: refusal naming
    `weather`"*).

    **Only the LOCATION line is used** -- latitude for the sun's path, longitude and time zone for
    True Solar Time. None of the file's hourly data matters: the standard's sunlight duration
    assumes cloudless conditions (D.6).

    **More than one weather input is refused unless they agree on the site.** The published
    signature has no argument to choose between them, and picking one silently would compute the
    sun for a place the caller may not have meant. Two inputs for the same site (two years of one
    station, say) give the same sun, so they are not refused. This refusal is not one of design
    section 7's rows; it is the case that row does not cover.

    The EPW's path comes from the manifest and is **re-contained against the allowed roots before
    it is read**, the posture `weather_tool._describe` takes for the same path and its reason: a
    path that was inside a root when it was registered is not thereby inside one now.

    The per-input reading moved to `rad_mcp.sky_site.read_registered_sites` (R762), which the sky
    tools share; the messages are unchanged.
    """
    records = [record for record in loaded.manifest.inputs if record.kind == "weather"]
    if not records:
        return None, refuse(
            "this project has no committed weather, so run_view_and_sunlight has no site to compute "
            "the sun for: True Solar Time needs the site's longitude and time-zone meridian and the "
            "sun's path needs its latitude, and the weather file's LOCATION line is where this "
            "project records them. Load one with weather(action='load', epw=..., name=...) first. "
            "(Only its location is read; sunlight duration assumes a cloudless day, D.6.)"
        )
    try:
        sites = read_registered_sites(runtime, loaded)
    except RegisteredSiteError as exc:
        if exc.code is not None:
            # N-79 (R830): a drifted weather input is coded; the file is the project's.
            return None, refuse(str(exc), [Advisory(code=exc.code, severity=exc.severity, field="project",
                                                    message=str(exc))])
        return None, refuse(str(exc))
    distinct = {
        (s.location.latitude, s.location.longitude_east_deg, s.location.time_zone_hours)
        for s in sites
    }
    if len(distinct) > 1:
        listed = "; ".join(
            f"{s.weather}: latitude {s.location.latitude}, longitude {s.location.longitude_east_deg} "
            f"E, UTC{s.location.time_zone_hours:+g}"
            for s in sites
        )
        return None, refuse(
            "this project has more than one committed weather input and they describe different "
            f"sites ({listed}). run_view_and_sunlight computes the sun for one site and takes no "
            "argument to choose between them, so it will not pick one silently. Remove the ones "
            "that are not this building's with weather(action='remove', name=...)."
        )
    return sites[0], None


def _resolve_north(loaded: LoadedProject) -> tuple[float, bool, CallToolResult | None]:
    """`(north_deg, declared, refusal)`: the project's `north` scalar, degrees east of north, read
    from where `glare_tool`, `point_in_time_tool` and `render_tool` read it: those three read it
    through `point_in_time_tool._project_north`, which does the lookup
    `loaded.manifest.project.scalars.get("north")` and then refuses a non-finite north (R694);
    this function does the same lookup itself and refuses the same values in its own words.

    Absent is 0 and `declared` is False (ruling T11.5): those three tools treat an absent north as
    no rotation -- and, since ruling R694, rotate their sky by a declared one, in the same sense
    this tool turns its sun -- and this tool refusing alone would make one project's sun sit in two places
    depending on which tool was asked. A non-finite value -- reachable only by hand-editing the
    manifest -- is refused: it would turn every sun direction into NaN, and `cast` does not check
    directions.
    """
    raw = loaded.manifest.project.scalars.get("north")
    if raw is None:
        return 0.0, False, None
    north = float(raw)
    if not math.isfinite(north):
        return 0.0, True, refuse(
            f"this project's north scalar is {raw!r}, which is not a finite number of degrees, so "
            "no sun direction can be turned into the model's frame. Set it again with a number."
        )
    return north, True, None


def _refuse_point_p(
    opening: str, exc: PointPUnreachable, *, only_group: bool = False
) -> CallToolResult:
    """Design section 7's point-P row, with its `field` re-mapped to an argument the caller passed
    (ruling T11.3): `rad_core.sunlight.PointPUnreachable.field` is `polygon_vertices`, that
    function's own parameter, which no caller of this tool ever supplied.

    `exc.binding` says which of D.2's two clearances failed, and so which argument can fix it: the
    floor clearance -> `floor_height_m` (only that clause is above the head, and a lower floor
    lowers P); the sill clearance -> `window_group`, because an opening too short to hold its own
    0,3 m sill clearance is too short at every floor height, and leaving it out of `window_group`
    is the only argument change that removes the refusal.

    `only_group` (ruling R700, owner decision 8): when the failing opening is the only window group
    read from the project -- a project whose one opening is a flat rooflight, say -- that advice
    cannot be taken, since leaving it out leaves nothing to assess. The refusal stays and its
    `field` stays `window_group`, but the remedy says no group can be assessed instead. It defaults
    to False so a caller that does not know the project's group count gets the unchanged message.
    """
    if exc.binding == "floor":
        field = "floor_height_m"
        remedy = (
            "D.2's floor clearance is the bound that failed: with the floor at floor_height_m, P "
            "must sit 1,2 m above it. Check floor_height_m is this room's floor level."
        )
    elif only_group:
        field = "window_group"
        remedy = (
            "D.2's 0,3 m sill clearance is the bound that failed, and no floor height changes it. "
            "This is the only window group read from this project, so this project has no window "
            "group this axis can assess; EN 17037 as distilled here gives no point P for such an "
            "opening (ch03 is silent on a rooflight's P)."
        )
    else:
        field = "window_group"
        remedy = (
            "D.2's 0,3 m sill clearance is the bound that failed, and no floor height changes it: "
            "this opening is too short to hold point P. Leave it out with window_group=[...]."
        )
    message = (
        f"window group {opening!r}: {exc}. D.2 puts point P inside the opening, and a point above "
        f"its head would measure the wall instead. {remedy}"
    )
    return refuse(message, [
        Advisory(code=exc.code, severity=exc.severity, field=field, message=message),
    ])


def _resolve_points(
    openings: Sequence[tuple[str, Aperture]], *, floor_height_m: float, only_group: bool = False
) -> tuple[dict[str, Point3], CallToolResult | None]:
    """D.2's point P for every opening, in model coordinates, or the first opening's refusal.

    Every member polygon's vertices are passed together, so a group of several panes has one P at
    the centre of the group's own width -- the group is one aperture here, as it is one view
    opening on the other axis. The exterior normal goes with them, so `point_p` can put P on the
    aperture's own plane (ruling F.3): on a tilted aperture the bounding-box centre is off it.

    `only_group` is passed through to `_refuse_point_p` (ruling R700): whether exactly one window
    group was read from the project (`_resolve_openings` reads every group whose modifiers resolve
    to a polygon), which decides whether "leave it out" is advice the caller can take. It defaults
    to False, as `_refuse_point_p`'s does; `_dispatch` always passes it."""
    points: dict[str, Point3] = {}
    for name, aperture in openings:
        vertices = [vertex for polygon in aperture.polygons for vertex in polygon.vertices]
        try:
            points[name] = point_p(
                vertices, floor_height_m=floor_height_m, normal=exterior_normal(aperture),
            )
        except PointPUnreachable as exc:
            return {}, _refuse_point_p(name, exc, only_group=only_group)
    return points, None


def _shadow_origin(point: Point3, aperture: Aperture) -> Point3:
    """Where every ray cast for this aperture starts: point P moved `_OUTWARD_OFFSET_M` along the
    EXTERIOR normal (ruling T11.6; `_build_sun_rays` says why). One function, so the shadow rays
    and the reveal probes (`_build_reveal_probes`) cannot start from two different places -- the
    probe's whole claim is about what a shadow ray from here can meet."""
    px, py, pz = point
    ex, ey, ez = exterior_normal(aperture)
    return (px + _OUTWARD_OFFSET_M * ex, py + _OUTWARD_OFFSET_M * ey, pz + _OUTWARD_OFFSET_M * ez)


def _build_reveal_probes(
    openings: Sequence[tuple[str, Aperture]], points: dict[str, Point3]
) -> tuple[list[tuple[Point3, Point3]], list[str], dict[str, float]]:
    """Ruling T12.2's probe rays, the opening each answers for, and each opening's reach.

    Two per facade opening, four per horizontal one (`rad_core.sunlight.reveal_probe_directions`),
    from `_shadow_origin`. They go in the **same** `rtrace` spawn as the shadow rays -- design
    section 4.2's one spawn per call is a contract `test_two_apertures_cumulate_as_a_union_in_one_
    cast` asserts -- and they are kept out of `steps_cast` and out of every hour.
    """
    rays: list[tuple[Point3, Point3]] = []
    keys: list[str] = []
    reach: dict[str, float] = {}
    for name, aperture in openings:
        origin = _shadow_origin(points[name], aperture)
        for direction in reveal_probe_directions(
            exterior_normal(aperture), aperture.up_vector, horizontal=is_horizontal(aperture),
        ):
            rays.append((origin, direction))
            keys.append(name)
        vertices = [vertex for polygon in aperture.polygons for vertex in polygon.vertices]
        reach[name] = opening_reach_m(vertices, points[name])
    return rays, keys, reach


def _build_sun_rays(
    openings: Sequence[tuple[str, Aperture]],
    points: dict[str, Point3],
    steps: Sequence[SunStep],
    *,
    north_deg: float,
    min_solar_altitude_deg: float,
) -> tuple[list[tuple[Point3, Point3]], list[tuple[str, int]]]:
    """Every shadow ray the day needs, for every opening, in **one** list, and the
    `(opening, step index)` each ray answers for.

    A step is dropped, not cast, when the sun is below `min_solar_altitude_deg` (Table D.1 NOTE 2:
    *"Direct sunlight cannot be counted below minimum sun altitude"*) or when it is not in front of
    the opening's plane. The second test is `rad_engine.suns.suns_in_hemisphere`, called on the one
    sun: it is `exterior_normal` and `_GRAZING_DOT` already, so the convention is reused rather
    than restated, and a sun grazing the plane is out exactly as it is for `run_annual`'s ASE guard.

    **Directions are unit and non-zero by construction.** `SunPosition.direction` is
    `(cos a sin z, cos a cos z, sin a)`, whose length is 1 for any finite `a` and `z`; `a` comes
    from `asin` and `z` from `acos` plus a finite north (`_resolve_north` refuses any other), so
    neither can be non-finite, and a NaN altitude would in any case fail the `>=` above first.

    **The ray starts 1 mm outside the glass, not at P** (ruling T11.6), along the EXTERIOR normal,
    by `_OUTWARD_OFFSET_M` -- the view-out branch's self-intersection guard, reused. The ruling's
    stated mechanism, that a ray from P would meet the aperture's own polygon, was **measured
    false** for a P exactly on the glass plane: `rtrace` does not report a hit at the ray's own
    origin, and with the offset removed the hours of `office_south`, `office_southeast_and
    _southwest` and `office_south_shaded_to_the_east` were measured unchanged. The offset is kept
    for a P that is not exactly on the plane -- float residue on a non-axis-aligned wall, or a
    group whose members sit up to `rad_core.apertures`' coplanarity tolerance apart -- where a
    start on the room side of the glass would cross it and read as blocked. It is a guard: no
    fixture Task 11 built makes it fail.
    """
    rays: list[tuple[Point3, Point3]] = []
    keys: list[tuple[str, int]] = []
    for name, aperture in openings:
        origin = _shadow_origin(points[name], aperture)
        for step in steps:
            if not step.altitude_deg >= min_solar_altitude_deg:
                continue
            sun = SunPosition(
                altitude_deg=step.altitude_deg,
                azimuth_deg=model_azimuth_deg(step.azimuth_true_deg, north_deg=north_deg),
            )
            if suns_in_hemisphere(aperture, [sun]) != 1:
                continue
            rays.append((origin, sun.direction))
            keys.append((name, step.index))
    return rays, keys


def _reference_day_steps(site: _Site, reference_date: str) -> tuple[float, list[SunStep]]:
    """The reference day's True Solar Time offset and steps, called by both `_dispatch` (to decide
    `EN17037_SUNLIGHT_CUTOFF_ABOVE_DAY_PEAK`, ruling R701) and `_run_sunlight` (to cast). Each calls
    it itself; it is one shared helper, so the two cannot disagree about which steps the day has --
    a dry run that said the cut-off was reachable and a real call that then dropped every step
    would be the same false-preview failure check 3.9 already guards against for refusals."""
    location = site.location
    return reference_day(
        date.fromisoformat(reference_date),
        latitude_deg=location.latitude,
        longitude_east_deg=location.longitude_east_deg,
        standard_meridian_east_deg=-location.time_zone_meridian_deg,
        step_minutes=SUNLIGHT_STEP_MINUTES,
    )


def _run_sunlight(
    env: RadianceEnv,
    *,
    paths: ProjectPaths,
    scene_files: Sequence[str],
    openings: Sequence[tuple[str, Aperture]],
    points: dict[str, Point3],
    site: _Site,
    north_deg: float,
    north_declared: bool,
    reference_date: str,
    min_solar_altitude_deg: float,
    floor_height_m: float,
    timeout_s: float,
    glazing: dict[str, list[str]],
    advisories: Sequence[Advisory],
) -> CallToolResult:
    """Walk the reference day, cast every surviving step for every opening in one spawn, and
    publish the union.

    A step counts for an opening when its shadow ray **escaped**: anything it met first --
    another building, a reveal, the opening's own frame -- is the *"actual horizon ... limited by
    permanent obstructions"* 3.19 defines exposure against. Intervals are unioned in step units,
    as integers, and turned into hours only for publication, so a level at exactly 1,5 h is not
    decided by float drift in a sum of hour differences.

    `luminaires=False` for the reason `_run_view_out` gives: the question is about the envelope and
    what stands outside it.

    **The same spawn carries the reveal probes** (`_build_reveal_probes`), appended after the
    shadow rays, so the cast always happens -- even for a window no sun step survives the filters
    for -- and a call still costs one `rtrace`. `glazing` is `_resolve_openings`' map from every
    window group's aperture modifier to its group(s); a shadow ray whose first hit carries one is
    counted for `EN17037_SUNLIGHT_BLOCKED_BY_GLAZING` and, as before, as blocked.
    """
    location = site.location
    meridian_east = -location.time_zone_meridian_deg
    offset, steps = _reference_day_steps(site, reference_date)
    rays, keys = _build_sun_rays(
        openings, points, steps, north_deg=north_deg,
        min_solar_altitude_deg=min_solar_altitude_deg,
    )
    probes, probe_keys, reach = _build_reveal_probes(openings, points)
    # `probes` is never empty, so the cast always runs: every opening gets at least two, and there
    # is always an opening -- `_resolve_openings` refuses a project with no window group, and
    # `_refuse_window_group_list` refuses `window_group=[]` (ruling F.5). **Until the final fix
    # wave this comment said the first refusal alone was enough, and it was false**: an empty
    # list passed it, since it reads the project's groups, not the caller's selection.
    sightings = _cast_in_temporary_octree(
        env, paths=paths, scene_files=scene_files, rays=[*rays, *probes], timeout_s=timeout_s,
        prefix=SUNLIGHT_OCTREE_PREFIX,
    )
    shadow, probed = sightings[:len(rays)], sightings[len(rays):]

    lit: dict[str, list[int]] = {name: [] for name, _aperture in openings}
    cast_count: dict[str, int] = {name: 0 for name, _aperture in openings}
    by_glass: dict[str, int] = {}
    glass_groups: set[str] = set()
    for (name, index), sighting in zip(keys, shadow, strict=True):
        cast_count[name] += 1
        if sighting.escaped:
            lit[name].append(index)
        elif sighting.modifier in glazing:
            by_glass[name] = by_glass.get(name, 0) + 1
            glass_groups.update(glazing[sighting.modifier])

    probe_distances: dict[str, list[float | None]] = {name: [] for name, _aperture in openings}
    for name, sighting in zip(probe_keys, probed, strict=True):
        probe_distances[name].append(sighting.distance_m)
    unbounded = [
        name for name, _aperture in openings
        if not reveal_is_modelled(probe_distances[name], reach_m=reach[name])
    ]
    conditional = [
        *([_reveal_not_modelled(unbounded)] if unbounded else []),
        *([_blocked_by_glazing(by_glass, sorted(glass_groups))] if by_glass else []),
    ]
    runs = {name: step_runs(indices) for name, indices in lit.items()}
    merged, total_steps = union_intervals(runs)

    step_h = SUNLIGHT_STEP_MINUTES / 60.0

    def hours(intervals: Sequence[tuple[float, float]]) -> list[tuple[float, float]]:
        return [(offset + start * step_h, offset + end * step_h) for start, end in intervals]

    sunlight_hours = total_steps * step_h
    return emit(SunlightExposureResult(
        advisories=[*advisories, *conditional],
        reference_date=reference_date,
        min_solar_altitude_deg=min_solar_altitude_deg,
        floor_height_m=floor_height_m,
        level_met=sunlight_level(sunlight_hours),
        sunlight_hours=sunlight_hours,
        intervals_tst_h=hours(merged),
        tst_minus_local_standard_time_h=offset,
        step_minutes=SUNLIGHT_STEP_MINUTES,
        location=SunlightSite(
            weather=site.weather,
            place=location.place,
            latitude_deg=location.latitude,
            longitude_east_deg=location.longitude_east_deg,
            standard_meridian_east_deg=meridian_east,
        ),
        north_deg=north_deg,
        north_declared=north_declared,
        by_aperture={
            name: SunlightAperture(
                azimuth_deg=aperture.azimuth_deg,
                point_p=points[name],
                steps_cast=cast_count[name],
                sunlight_hours=len(lit[name]) * step_h,
                intervals_tst_h=hours(runs[name]),
            )
            for name, aperture in openings
        },
    ))


def _dispatch(
    runtime: Runtime,
    *,
    project: str,
    axis: str,
    window_group: list[str] | str | None,
    grid: str | None,
    eye_above_floor_m: float | None,
    reference_date: str | None,
    min_solar_altitude_deg: float | None,
    floor_height_m: float | None,
    dry_run: bool,
    timeout_s: float,
) -> CallToolResult:
    """Everything below the argument refusals: the project-state checks, then the computation.

    **Every project-state refusal fires above the `dry_run` return**, on both axes, which is design
    section 7's own sentence (*"every refusal above fires before that return -- a dry run that
    previewed a call the real one would refuse is a false preview"*) and this repo's check 3.9: one
    validation path that both a preview and a real call walk, not a second copy that happens to
    agree today. So a dry run opens the project and reads the window groups -- which is why it
    needs a Radiance install to resolve the aperture modifiers, exactly as `run_glare`'s own
    aperture validation above its `dry_run` already does.

    `discover()` sits **below** the manifest refusals on both axes: a caller whose project has no
    geometry, names a grid that is not registered, or has no committed weather is told that -- not
    that Radiance is missing -- and none of those refusals needs an install to fire.
    """
    loaded = runtime.open(project)
    paths = ProjectPaths(loaded.paths.root)

    if axis == "view_out":
        # `_refuse_required_for_axis` proved all three.
        assert grid is not None and eye_above_floor_m is not None and floor_height_m is not None
        scene = _validate_scene(loaded, grid=grid, cwd=paths.root, luminaires=False)
        env = discover()
        openings, glazing, refusal = _resolve_openings(
            loaded, env, paths.root, window_group=window_group, timeout=timeout_s, axis=axis,
        )
        if refusal is not None:
            return refusal
        targets = _lattices(openings)
        samples = sum(len(cells) for cells in targets.values())
        if (refusal := _refuse_ray_ceiling(
            len(scene.points) * samples * 2,
            how=(
                f"{len(scene.points)} reference points x {samples} samples over "
                f"{len(openings)} opening(s) x 2 rays per sample"
            ),
        )) is not None:
            return refusal
        view_advisories = _view_out_advisories(
            grid=grid, eye_z_m=_eye_z_m(floor_height_m, eye_above_floor_m), openings=openings,
        )
        if dry_run:
            return emit(ViewSunlightDryRunOk(advisories=view_advisories))
        return _run_view_out(
            env, paths=paths, scene=scene, openings=openings, targets=targets, grid=grid,
            floor_height_m=floor_height_m, eye_above_floor_m=eye_above_floor_m,
            timeout_s=timeout_s, glazing=glazing, advisories=view_advisories,
        )

    # axis == "sunlight_exposure" -- `_refuse_axis` has already ruled out every other value.
    assert (
        reference_date is not None and min_solar_altitude_deg is not None
        and floor_height_m is not None
    )  # `_refuse_required_for_axis` proved all three
    # No grid on this axis, so `_validate_scene`'s grid lookup and points read do not apply. Its
    # geometry check and its `!`-line scan do, in its order -- no geometry is refused first, and the
    # scan runs above the `dry_run` return so a preview cannot pass a scene the real call refuses
    # (check 3.9).
    scene_files = _scene_files(
        loaded, luminaires=False,
        no_geometry_recovery=(
            "Call geometry(action='add') with at least one scene before assessing sunlight "
            "exposure -- there is nothing for a shadow ray to meet, and no aperture to cast from."
        ),
    )
    _scan_scene_for_inline_commands(scene_files, cwd=paths.root)
    site, refusal = _resolve_site(runtime, loaded)
    if refusal is not None:
        return refusal
    assert site is not None
    north_deg, north_declared, refusal = _resolve_north(loaded)
    if refusal is not None:
        return refusal
    env = discover()
    openings, glazing, refusal = _resolve_openings(
        loaded, env, paths.root, window_group=window_group, timeout=timeout_s, axis=axis,
    )
    if refusal is not None:
        return refusal
    # Ruling R699 (owner decision 7): D.2 places point P in each opening of the space, and the
    # union across groups is this room's only if every group is this room's -- which nothing in the
    # manifest says. With one group there is nothing to choose; with more, the caller chooses.
    # Above the `dry_run` return, like every project-state refusal.
    if window_group is None and len(openings) > 1:
        names = ", ".join(repr(name) for name, _aperture in openings)
        return _refuse_argument(
            f"this project has {len(openings)} readable window groups ({names}), and "
            "axis='sunlight_exposure' needs window_group to say which belong to the room being "
            "assessed. D.2 places point P in each opening of the space, and the union across "
            "groups is only this room's if every group is this room's. Pass "
            "window_group=[...] naming this room's groups (owner decision 7, 2026-09-24).",
            field="window_group",
        )
    # Ruling R700 (owner decision 8): counted over every group read, not the selected ones.
    only_group = len({group for groups in glazing.values() for group in groups}) == 1
    points, refusal = _resolve_points(
        openings, floor_height_m=floor_height_m, only_group=only_group,
    )
    if refusal is not None:
        return refusal
    # Ruling F.6's ceiling, on an upper bound: every step of the day for every opening (the
    # altitude and facing filters only ever drop steps), plus each opening's reveal probes.
    steps_per_day = (24 * 60) // SUNLIGHT_STEP_MINUTES
    probes = sum(
        len(reveal_probe_directions(
            exterior_normal(aperture), aperture.up_vector, horizontal=is_horizontal(aperture),
        ))
        for _name, aperture in openings
    )
    if (refusal := _refuse_ray_ceiling(
        len(openings) * steps_per_day + probes,
        how=(
            f"at most {steps_per_day} steps of the reference day x {len(openings)} opening(s), "
            f"plus {probes} reveal probes"
        ),
    )) is not None:
        return refusal
    # Ruling R701 (owner decision 6): the same day `_run_sunlight` will walk -- it calls the helper
    # again itself; one shared helper, so a dry run's advisory and the real call's hours cannot
    # disagree about which cut-off the reference day reaches. `>=` is `_build_sun_rays`'s own
    # comparison for dropping a step -- reused, not restated, so "reachable" means exactly what
    # casting will do with it.
    _offset, steps = _reference_day_steps(site, reference_date)
    reachable = any(step.altitude_deg >= min_solar_altitude_deg for step in steps)
    peak_deg = max(step.altitude_deg for step in steps)
    sunlight_advisories = _sunlight_advisories(
        min_solar_altitude_deg=min_solar_altitude_deg, reachable=reachable, peak_deg=peak_deg,
    )
    if dry_run:
        return emit(ViewSunlightDryRunOk(advisories=sunlight_advisories))
    return _run_sunlight(
        env, paths=paths, scene_files=scene_files, openings=openings, points=points, site=site,
        north_deg=north_deg, north_declared=north_declared, reference_date=reference_date,
        min_solar_altitude_deg=min_solar_altitude_deg, floor_height_m=floor_height_m,
        timeout_s=timeout_s, glazing=glazing, advisories=sunlight_advisories,
    )


# --- the tool -------------------------------------------------------------------------------------


def _eye_height_m_retired(arguments: Mapping[str, Any]) -> str:
    """The message for the retired `eye_height_m` (R691, R698; moved here by R716).

    Read from the call's unvalidated arguments, so `axis` may be any JSON value: anything but a
    string gets both fixes. The value itself is not echoed; it used to be a validated float.
    """
    axis = arguments.get("axis")
    view_fix = (
        " For axis='view_out', pass floor_height_m (this room's floor z in the model's frame) "
        "and eye_above_floor_m (1.2 seated, 1.7 standing, Annex C.4.1) instead; the eye is "
        "placed at their sum."
    )
    sunlight_fix = (
        " For axis='sunlight_exposure', leave eye_height_m out: that axis never used it, and "
        "floor_height_m is already its own argument."
    )
    fix = {"view_out": view_fix, "sunlight_exposure": sunlight_fix}.get(
        axis if isinstance(axis, str) else "", view_fix + sunlight_fix,
    )
    return (
        "eye_height_m is no longer accepted: it was the ABSOLUTE model z of the eye, and an "
        f"upper-storey caller passing 1.2 put the eye in the floor slab (ruling R691).{fix}"
    )


#: Arguments this tool no longer takes, and what replaced them. Routed by
#: `rad_mcp.server.RETIRED_ARGUMENTS` (R716).
RETIRED_ARGUMENTS: dict[str, RetiredMessage] = {"eye_height_m": _eye_height_m_retired}


def run_view_and_sunlight(
    project: Annotated[str | None, Field(description="the project handle.")] = None,
    axis: Annotated[
        str | None,
        Field(
            description=(
                "REQUIRED, no default -- 'view_out' or 'sunlight_exposure'. One axis per call: "
                "EN 17037 has no composite score, and a payload carrying both would read as one."
            )
        ),
    ] = None,
    window_group: Annotated[
        list[str] | str | None,
        Field(
            description=(
                "which window group(s) to read as view openings/apertures. Common to both axes. "
                "Default on 'view_out': every defined group. On 'sunlight_exposure': required "
                "when more than one window group is read from the project (R699, owner decision "
                "7) -- the union across groups is only this room's if every group is -- and a "
                "one-group project needs none. A group whose aperture modifiers resolve to no "
                "polygon is not read. An empty list, or one naming a group twice, is refused."
            )
        ),
    ] = None,
    grid: Annotated[
        str | None,
        Field(
            description=(
                "REQUIRED for axis='view_out', no default. The sensor grid whose polygon stands in "
                "for the utilized area and whose points' x/y become the reference positions "
                "(design §3). Refused if given under axis='sunlight_exposure'."
            )
        ),
    ] = None,
    eye_above_floor_m: Annotated[
        StrictFloat | None,
        Field(
            description=(
                "REQUIRED for axis='view_out', no default. The eye's height above floor_height_m: "
                "1.2 seated or 1.7 standing (Annex C.4.1's two heights, \"unless otherwise "
                "specified\"). The eye is placed at floor_height_m + eye_above_floor_m. A layer "
                "count is not reproducible without it and the two answers differ, so no default "
                "is substituted. Must be finite and above 0, and its sum with floor_height_m must "
                "be finite. Refused if given under axis='sunlight_exposure'."
            )
        ),
    ] = None,
    reference_date: Annotated[
        str | None,
        Field(
            description=(
                "REQUIRED for axis='sunlight_exposure', no default. ISO date (YYYY-MM-DD), 1 "
                "February .. 21 March inclusive -- Table D.1's minimum criteria are conditioned "
                "on 21 March and the 1,5 h level. Refused if given under axis='view_out'."
            )
        ),
    ] = None,
    min_solar_altitude_deg: Annotated[
        StrictFloat | None,
        Field(
            description=(
                "REQUIRED for axis='sunlight_exposure', no default. Table D.1's values are "
                "conditioned on 21 March and the 1,5 h level, so nothing here mints a threshold. "
                "Must be finite, from 0 to 90 inclusive: exposure is time with the sun above the "
                "horizon (3.19), so a negative cut-off is refused, and no sun stands above the "
                "zenith, so one above 90 is refused too (R701). A cut-off no step of the reference "
                "day reaches is not refused; the call warns "
                "EN17037_SUNLIGHT_CUTOFF_ABOVE_DAY_PEAK, dry run included, and the real call "
                "publishes 0 h. Refused if given under axis='view_out'."
            )
        ),
    ] = None,
    floor_height_m: Annotated[
        StrictFloat | None,
        Field(
            description=(
                "REQUIRED on both axes, no default. The floor's z in the model's frame; must be "
                "finite. On axis='sunlight_exposure', point P sits a minimum height above it "
                "(D.2); the sill is derivable from the aperture but the floor is not, and "
                "guessing it moves P and the hours it sees. On axis='view_out' the eye sits "
                "eye_above_floor_m above it (R698) -- one floor z per call, so a sloped or "
                "stepped floor is assessed one level at a time."
            )
        ),
    ] = None,
    dry_run: Annotated[
        bool,
        Field(
            description=(
                "every refusal knowable without computing the level. **On axis='view_out' this "
                "is not a spawn-free preview**: the preview opens the project, validates the "
                "named grid and reads the window groups -- and reading a group's aperture shells "
                "out to `xform`. What it does not do is build the octree, cast a ray or write "
                "anything under the project. The same holds on axis='sunlight_exposure', whose "
                "preview also reads the committed weather's location and computes point P, so a "
                "refused weather file or an unreachable P is refused here too."
            )
        ),
    ] = False,
    inline_timeout_s: Annotated[
        StrictFloat,
        Field(
            description=(
                "bound, in seconds, on each `xform` read of the window groups' apertures and on "
                "the `rtrace` cast, each spawn separately, not their total (default 20 s, not "
                "run_ugr's 60 s); the temporary octree is built under a fixed 30 s. This tool casts no "
                "`-I` ray and runs no ambient calculation, so R671's exception for run_ugr does "
                "not apply. Must be finite, above 0 and at most 2147483.647 s "
                "(`rad_mcp.timeouts.SPAWN_TIMEOUT_CEILING_S`). A call that would cast more than "
                "MAX_RAYS_PER_CALL (1 000 000) rays is refused before any is built, dry run "
                "included: split the grid or name fewer window groups."
            )
        ),
    ] = DEFAULT_INLINE_TIMEOUT_S,
) -> ViewSunlightResponse:
    """EN 17037's view-out (5.2) or sunlight-exposure (5.3) axis, over a frozen octree -- one axis
    per call, each assessed by casting rays through the project's window groups.

    Use it for EN 17037's view-out and sunlight-exposure axes; for daylight provision use
    `results(action='compliance', standard='en17037')`, and for glare `run_glare`.

    There is no `quality` argument: no ambient parameter changes a visibility-only, `-ab 0` cast.

    axis='view_out' returns a level for each of 5.2.2's three assessable criteria -- horizontal
    sight angle, outside distance of view and layers -- and C.1's lowest of the three as
    `level_met`, which is `null` for **assessed and below minimum**. The utilized area is the named
    grid: its points' x/y are the reference positions and floor_height_m + eye_above_floor_m
    (published as `eye_z_m`) replaces its own plane height. Each window group is one view opening.

    axis='sunlight_exposure' returns Table A.6's level for the hours of direct sun reaching point
    P (D.2) of every window group on reference_date, walked minute by minute in True Solar Time
    for the committed weather file's site, above min_solar_altitude_deg and in front of each
    aperture, and not blocked by the scene's geometry. `level_met` is `null` for **assessed and
    below minimum**. Apertures are cumulated as a union, never a sum; each one's own intervals and
    hours are published beside the union. The sun is turned into the model's frame by the
    project's `north` (0 if the project declares none, and the result says which).

    **Dry run.** A clean preview returns `{dry_run: true, advisories}`: every advisory the real
    call carries that is decidable before a ray is cast. The ones that need the cast are not
    previewed.
    """
    if (refusal := refuse_missing("run_view_and_sunlight", project=project)) is not None:
        return refusal  # type: ignore[return-value]
    if (refusal := _refuse_axis(axis)) is not None:
        return refusal  # type: ignore[return-value]
    assert axis is not None and axis in _AXES
    if (refusal := _refuse_required_for_axis(
        axis, grid=grid, eye_above_floor_m=eye_above_floor_m, reference_date=reference_date,
        min_solar_altitude_deg=min_solar_altitude_deg, floor_height_m=floor_height_m,
    )) is not None:
        return refusal  # type: ignore[return-value]
    if axis == "sunlight_exposure":
        assert reference_date is not None and min_solar_altitude_deg is not None
        if (refusal := _refuse_date_window(reference_date)) is not None:
            return refusal  # type: ignore[return-value]
        if (refusal := _refuse_min_solar_altitude(min_solar_altitude_deg)) is not None:
            return refusal  # type: ignore[return-value]
    # The final whole-branch review's argument refusals, all before the project is opened and so
    # all above the `dry_run` return: the heights, the window-group list (ruling F.5), the timeout.
    if (refusal := _refuse_non_finite_heights(
        eye_above_floor_m=eye_above_floor_m, floor_height_m=floor_height_m,
    )) is not None:
        return refusal  # type: ignore[return-value]
    if (refusal := _refuse_window_group_list(window_group)) is not None:
        return refusal  # type: ignore[return-value]
    if (refusal := _refuse_inline_timeout(inline_timeout_s)) is not None:
        return refusal  # type: ignore[return-value]
    try:
        return _dispatch(  # type: ignore[return-value]
            current(), project=project, axis=axis, window_group=window_group, grid=grid,
            eye_above_floor_m=eye_above_floor_m, reference_date=reference_date,
            min_solar_altitude_deg=min_solar_altitude_deg, floor_height_m=floor_height_m,
            dry_run=dry_run, timeout_s=inline_timeout_s,
        )
    except ApertureError as exc:
        # A window group whose members are not coplanar, or give no consistent normal. Propagated
        # by `_project_apertures` rather than swallowed (its own docstring says why), and shaped
        # here with the exception's own allocated code -- `field="project"`, the convention every
        # tool in this package uses for a refusal computed from project state rather than from an
        # argument this call takes.
        logger.info("run_view_and_sunlight refused: %s", exc)
        if exc.code is None:
            return refuse(str(exc))  # type: ignore[return-value]
        return refuse(  # type: ignore[return-value]
            str(exc),
            [Advisory(code=exc.code, severity=exc.severity, field="project", message=str(exc))],
        )
    except SceneInlineCommandError as exc:
        # Ruling R33: a registered scene file carrying a hostile `!` line, refused at the boundary
        # where the content is read. A bare `RuntimeError`, disjoint from `ProjectError`, so it
        # needs its own clause or it escapes as an uncoded `ToolError` with no `structuredContent`
        # -- check 0.4's one forbidden outcome, with an absolute path disclosed in the message on
        # top. Shaped through the same `getattr` route `point_in_time_tool` uses for it.
        logger.info("run_view_and_sunlight refused: %s", exc)
        return refuse(  # type: ignore[return-value]
            str(exc),
            [Advisory(
                code=getattr(exc, "code", "SCENE_INLINE_COMMAND"), severity="error",
                field="project", message=str(exc),
            )],
        )
    # From here to `RadianceNotFound`, `run_ugr`'s and `run_electric`'s own clauses, in their
    # shapes and with their codes (final whole-branch review, I1 / S-I3 / Task 14's F1-F2): until
    # the final fix wave none of these was caught here, and each reached the wire as an uncoded
    # `ToolError` with no `structuredContent` -- check 0.4's one forbidden outcome.
    except (EmptyOctreeError, OctreeSetOverflowError, OctreeTooManyFilesError) as exc:
        logger.info("run_view_and_sunlight refused: %s", exc)
        code = getattr(exc, "code", None)
        if code is None:
            return refuse(str(exc))  # type: ignore[return-value]
        return refuse(str(exc), [Advisory(code=code, severity=getattr(exc, "severity", "error"),  # type: ignore[return-value]
                                          message=str(exc))])
    except RtraceOutputError as exc:
        # `rad_engine.visibility.cast` raises it for a row count other than the ray count or a row
        # that does not parse. `RTRACE_OUTPUT_MISMATCH` is reused, not re-allocated -- R638 allocated
        # it for `run_electric`'s identical condition and R666 reused it for `run_ugr`'s -- with no
        # `field`, as there: it names a server fault, not an argument.
        logger.info("run_view_and_sunlight refused: %s", exc)
        message = (
            f"{exc} This is a fault inside the server, not something about the arguments; "
            "nothing was published, and the failure should be reported."
        )
        return refuse(message, [Advisory(code="RTRACE_OUTPUT_MISMATCH", severity="error",  # type: ignore[return-value]
                                         field=None, message=message)])
    except NotARegularFileError as exc:
        # R48/R58: something not a regular file at a name this call reads or writes, refused,
        # never followed -- `run_ugr`'s and `run_electric`'s clause and code.
        logger.info("run_view_and_sunlight refused: %s", exc)
        return refuse(str(exc), [Advisory(code="PROJECT_PATH_NOT_A_REGULAR_FILE", severity="error",  # type: ignore[return-value]
                                          field="project", message=str(exc))])
    except RadianceNotFound as exc:
        logger.info("run_view_and_sunlight refused: %s", exc)
        return refuse(  # type: ignore[return-value]
            str(exc), [Advisory(code="RADIANCE_NOT_FOUND", severity="error", message=str(exc))]
        )
    except RadianceError as exc:
        logger.info("run_view_and_sunlight failed: %s", exc)
        return refuse(str(exc))  # type: ignore[return-value]
    except (ProjectError, PathOutsideRoot, RootsNotConfigured) as exc:
        # Every grid refusal this branch can raise is a `ProjectError` subclass
        # (`GridNotFoundError`, `GridEmptyOnDiskError`, `GridUnreadableError`, `GridZonedError`,
        # `NoGeometryRegisteredError`), so design section 7's last row -- *"a named `window_group`
        # or `grid` does not exist: the existing not-found refusal, with its field"* -- is honoured
        # by reusing that shaping rather than by restating any of those messages here.
        logger.info("run_view_and_sunlight refused: %s", exc)
        return refuse_engine_error(exc)  # type: ignore[return-value]
    except OSError as exc:
        # Any other operating-system error -- the temporary octree's `mkstemp` in a read-only
        # project root, say. No register code names it, and neither `run_ugr`'s nor
        # `run_electric`'s handler chain has a clause for it, so it is a code-less refusal here
        # rather than an uncoded escape (check 0.4).
        logger.warning("run_view_and_sunlight failed on the file system: %r", exc)
        return refuse(  # type: ignore[return-value]
            f"run_view_and_sunlight could not complete: {type(exc).__name__}: {exc}. A file this "
            "call reads or writes under the project could not be used; nothing was published."
        )
    except ArithmeticError as exc:
        # `run_point_in_time`'s, `run_electric`'s and `run_ugr`'s last resort. Measured before
        # R704, an `inline_timeout_s` of 1e9, 9,2e9 or 1e30 overflowed the spawn and landed here;
        # `_refuse_inline_timeout` now refuses every value above the measured spawn ceiling, with
        # a code, before the project is opened. The one way a timeout could still reach this
        # clause is a platform whose spawn ceiling is lower than the measured one (docs/
        # tool-surface.md, Unsourced values, SPAWN_TIMEOUT_CEILING_S), so the message keeps that
        # as a possibility rather than as the known cause.
        logger.warning("run_view_and_sunlight failed in arithmetic: %r", exc)
        return refuse(  # type: ignore[return-value]
            f"run_view_and_sunlight could not complete: {type(exc).__name__}: {exc}. This is an "
            "arithmetic overflow inside the server. An inline_timeout_s above the measured "
            "spawn ceiling is refused before the project is opened, so this is a defect to "
            "report -- or, on a platform whose spawn accepts less than that ceiling, a timeout "
            "that platform cannot represent; a smaller inline_timeout_s would show which.")
