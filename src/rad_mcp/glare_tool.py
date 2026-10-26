"""The `run_glare` tool -- docs/tool-surface.md#13-run_glare.

Owns daylight glare probability (DGP) via a fisheye HDR and `evalglare` (docs/plan.md Phase 8).
Thin, in the sense CONTEXT.md's Layers table means it: this module validates arguments, resolves
and contains paths, reads the project's apertures and the image header, picks the engine calls and
shapes the envelope. **No number reported here is computed here.** `rad_core.glare` owns the
validity preconditions (view type, view angles, resolution, pixel aspect) and the EN 17037 glare
moment arithmetic; `rad_engine.rpict` renders; `rad_engine.evalglare` runs `evalglare` and parses
its output and its notices; `rad_core.apertures` derives an aperture's tilt and decides whether it
is horizontal. This module plumbs the four together.

## There is no `fast` quality, and it is deliberate

`quality` accepts only `"accurate"` and `"maximum"` -- `rad_engine.rpict.GLARE_QUALITY_PRESETS`'s
whole domain, rulings R262 (which sourced column each value binds to) and R299 (which name each
value carries). Any other string is refused, naming why: *"A low-quality ambient calculation
produces a confident, plausible, wrong DGP. This is the metric where cheap parameters do the most
damage"* (docs/tool-surface.md #13, quoting
[patterns.md](../../skills/lighting-standards-compliance/patterns.md)). DGP's formulation folds in
the vertical illuminance at the eye and the background luminance, both of which come out of the
ambient calculation -- a cheap `-ab`/`-ad` does not merely blur the picture, it changes the number.
The sourced DGP table's `Fast / preview` column is deliberately not offered, and there is no
`"draft"`/`"standard"` alias from the ambient family that would quietly downgrade to one either.

## `en17037.level_met` is always `null` from this tool, and it cannot be otherwise

EN 17037's glare criterion is that DGP must not exceed a threshold for more than **5 % of usage
time** over a full year (ch03 §5.4) -- a fraction of a year, not a value at an instant. One fisheye
render answers "what is DGP right now", never "does this space meet a recommendation level".
`rad_core.glare.en17037_glare_moment` has no parameter through which an annual usage-time fraction
could ever reach it, by construction (see that function's own docstring): the field exists to name
what a single moment cannot answer. `moment_below` names which thresholds this one instant happens
to sit under, and `GLARE_SINGLE_MOMENT` (info) says so.

**No path in this build populates a glare `level_met`, so this docstring names none.** It used to
name `run_annual(metrics=["annual_dgp"])`; annual DGP is not built (R815 withdrew `annual_dgp`
from `run_annual`'s metrics, and a call naming it is refused as a withdrawn metric), and
`results(action="compliance", standard="en17037", axis="glare")` is refused too
(`results_tool._refuse_en17037_glare_not_built`). The only code in this repository that assigns a
non-null `level_met` is `rad_core.compliance.en17037_provision`, in both its vertical and its
horizontal branch, reached through
`results(action="compliance", standard="en17037", axis="provision")` off
`run_annual(metrics=["en17037_provision"])` -- **EN 17037 daylight provision, a different criterion
from a different metric, not the glare answer this field is about.**

## `ugr` is returned, and it is not a daylight glare rating

`evalglare` prints a `ugr` (Unified Glare Rating) field alongside `dgp` on every run. UGR is an
electric-lighting metric, and EN 17037 §5.4.2 forbids substituting one for DGP as a daylight glare
assessment. `ugr` is still reported, in the raw block, because it costs nothing to report and a
caller who wants it for some other purpose should not have to re-run `evalglare` by hand -- but it
carries `UGR_NOT_FOR_DAYLIGHT` at **`error`** severity on every successful call, and it is
structurally absent from `en17037` (never merely omitted from a bigger dict -- the `en17037` object
this module builds never has a `"ugr"` key to begin with). `error` on a call that is not a refusal
is unusual and is the one deliberate exception in this phase: `is_error` describes the call,
`error`-severity describes a value inside it (`rad_mcp.envelope`'s own distinction), and this is the
value this project judges as carrying that severity even when the call itself succeeded.

## Order of operations, and why check 8.3 pins it

validate arguments (`quality`, `image` xor `view_position`+`view_direction`, `task_area`'s length)
-> resolve and contain `image` if given -> open the project, refuse a pre-Phase-10 unscaled-in-place
geometry record (`rad_engine.project.refuse_unscaled_geometry`, design §5.11 amendment 4) and
resolve the DGP's scope, which refuses a project carrying any horizontal window group
(`rad_core.apertures.is_horizontal`) unless
`dgp_window_group` names a vertical one, and refuses that argument itself if it is unregistered,
horizontal or outside the fisheye (`_resolve_dgp_scope`) -> refuse the render branch's own two arguments,
`sky` and `resolution`, if this call is going to render -> (`dry_run` stops here) -> read the image
header, or render one -> `rad_core.glare.check_glare_input` -> **only then** spawn `evalglare`. `evalglare` itself
does not refuse a perspective image -- measured, `evalglare high_presp_view.hdr` returns rc 0 and
`dgp 0.220343` with only a stderr warning (`rad_engine.evalglare`'s own module docstring) -- so the
refusal for a bad view type, bad view angles, a resolution below the floor, or a non-square pixel
aspect is this tool's, and it must precede the spawn rather than follow a wasted one.

**The render branch's own two arguments moved up to satisfy that same sentence** (Phase 8
whole-branch review, findings I2 and I7). `resolution` and `sky` are knowable from the call alone,
and both used to be discovered only after `gensky`, `oconv` and a full `-ab 5 -ad 512` fisheye had
already run: `resolution=500` paid for the whole render and was then refused by `check_glare_input`
reading the header of the image it had just made -- while `rad_engine.rpict` published the opposite
as fact, that the floor is enforced *"at the tool layer before this function is ever called"* -- and
a missing `sky` surfaced as `point_in_time_tool._resolve_sky`'s message naming a `quantity` argument
this tool does not have. Both are now refused below the horizontal-opening check and above `dry_run`, so
`dry_run=True` reports them too and nothing is ever rendered for a call they refuse. They sit below
the horizontal-opening refusal rather than with the other argument checks deliberately: a rooflight
project can never produce a DGP whatever its `sky` says, so naming a fixable argument first would
send a caller to fix one thing and be refused again for a permanent one -- the ordering argument
`annual_tool`'s DGP-on-a-horizontal-opening refusal made for its own placement until R815 withdrew
`annual_dgp`. `check_glare_input` still runs after the render, unchanged: it is
the only check that can answer for a caller-supplied `image=`, it covers three faults `resolution`
alone cannot (view type, view angles, pixel aspect), and the header of a rendered file is still
worth reading rather than assumed.

## The nine glare-family advisory codes, and the two this task cannot emit

Six of the register's nine glare codes have a real trigger here and are emitted:
`GLARE_RESOLUTION_SLOW` (`rad_core.glare.resolution_notes`), `GLARE_LOW_LIGHT_CORRECTION_APPLIED`
(always "not applied" -- ruling R257, `rad_engine.evalglare` never emits `-C`),
`GLARE_NO_TASK_AREA` (no `task_area`, and a `threshold_factor` that is absent or at most
`rad_engine.evalglare.THRESHOLD_FACTOR_ABSOLUTE_BOUNDARY` -- above 100 the flag is an absolute
cd/m2 threshold and the missing task area does not scale anything, so the advisory is correctly
silent there; §13's register row already stated the `<= 100` condition and only these docstrings
had drifted),
`GLARE_SINGLE_MOMENT` and `GLARE_VIEW_NOT_WORST_CASE` (always true of a point-in-time single-view
run) and `UGR_NOT_FOR_DAYLIGHT` (always true, at `error`, on a successful result). A seventh,
`GLARE_LOW_LIGHT_SCENE`, already has an emitter -- `rad_engine.advisories.RULES` classifies
`evalglare`'s own "Low brightness scene" notices, and this module reaches it by running every
`EvalglareResult.notices` line through `classify`/`to_wire_all`, the same two-step every other tool
that surfaces a `Result.warnings` list already takes.

`GLARE_NO_SOURCES_FOUND` and `GLARE_PIXEL_OVERFLOW` are **not** emitted this task, and that is a
reported gap rather than a guessed-at threshold, the same posture `rad_mcp.image_tool` documents
for `IMAGE_PIXEL_OVERFLOW`. Both are conditions `evalglare` would have to say something about on its
own stderr (a genuine "no glare source found" notice, a genuine overflow notice), and
`rad_engine.advisories.RULES` carries no rule for either string -- adding one is engine-layer work
this task's file list does not include (Files: this module, `server.py`, its own test file and
`tests/conftest.py`). Reported here rather than invented: this module has no other signal to derive
either condition from without duplicating `evalglare`'s own detection logic in Python, which would
be exactly the physics duplication CONTEXT.md's Layers table forbids at this level.

## Reading the project's apertures

`_project_apertures` mirrors `rad_mcp.annual_tool._window_groups`'s own route for the identical
reason that module's docstring gives: the member modifiers are read back off each `window_group`
input's own recorded `registered_by.arguments["aperture"]` and resolved against every polygon any
registered `geometry` input uses (`rad_engine.scene.read_scene`), never against the window group's
own files -- a narrower file set would find a different member list for the same group name, and an
aperture derived from a subset of its own members has a different tilt. This is plumbing (reading
a manifest, resolving modifier names, calling `rad_core.apertures.derive`), not the arithmetic
itself. `run_glare` has no `metrics` list to drop a member of the way `run_annual` does, so its
refusal names deregistering every horizontal window group in this project, or running against
one whose window groups are all vertical -- and, since check 9.12, passing `dgp_window_group` to
declare which aperture the DGP is for. See "The escape" below.

**The predicate is ANY horizontal group, not "all of them".** `_refuse_horizontal_project` refuses
a side-lit room that also has one skylight, and that is deliberate: it is the same test
`run_annual` applied to `annual_dgp` until R815 withdrew that metric -- both built the horizontal
subset with `rad_core.apertures.is_horizontal` and refused when that subset was non-empty --
shipped since commit `b0844d0`, and loosening it is the direction that returns a confident number
for a metric EN 17037 rules out. Ruling R278 settled this against the wire docstring, which read
*"its **only** registered window group(s)"* until the Phase 8 whole-branch fix wave: the predicate
stood and the sentence was the defect.

## The escape: `dgp_window_group` (check 9.12, ruling R302)

R278 left the asymmetry the owner then scoped into Phase 9: `run_annual` let a caller drop
`annual_dgp` from `metrics` (withdrawn since R815), and `run_glare` had nothing to drop, so a side-lit room with one
skylight could not use this tool at all -- for a metric that is well defined on its window.
`dgp_window_group` is the matching escape, and `_resolve_dgp_scope` is where it lives.

**It is a declaration of scope plus a facing check, and never a claim that the number excludes
the project's horizontal openings, however many it has.** Nothing is removed from what the
180-degree fisheye renders; a rooflight's light is in the vertical illuminance and the background luminance
`evalglare` integrates, whatever the caller names. What the argument changes is what the *result
says the number is for* -- `dgp_aperture` -- and `DGP_APERTURE_SCOPED` (warn), which names each
horizontal window group `_project_apertures` resolves, states the rest plainly on every result this
argument produces from a project that has at least one. Physically excluding a horizontal
opening, by swapping its material to opaque before the render, was considered at design and
**rejected**: it returns a number for a building that does not exist.

**The refusal is still the default.** Passing nothing reaches `_refuse_horizontal_project`, whose
predicate R278 settled and this task did not touch. Its *message* did change: a refusal that still
said no argument could drop the offending group would be false the moment this escape shipped, so
it names `dgp_window_group` instead. The escape is explicit rather than a flag that quietly widens
the predicate, and naming the horizontal opening is refused rather than honoured.

**`en17037.level_met` is unaffected and stays structurally null.** One image is one instant and EN
17037's glare criterion is a 5 %-of-usage-time test; scoping a DGP to an aperture narrows what the
number is about and does not turn an instant into a year.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Collection, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any, Final, Literal

from mcp_types import CallToolResult
from pydantic import BaseModel, ConfigDict, Field, RootModel, StrictInt, StrictFloat

from rad_core.apertures import ApertureError, derive, is_horizontal
from rad_core.geometry import BoundingBox, Polygon, bounding_box
from rad_core.glare import (
    FISHEYE_ANGLE_DEG,
    GlareRefusal,
    check_glare_input,
    check_render_resolution,
    en17037_glare_moment,
    interpretation_band,
    resolution_notes,
)
from rad_core.quality import QualityParameters
from rad_core.views import angle_between_deg
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
    read_scene,
    refuse_unscaled_geometry,
)
import rad_engine.evalglare as _evalglare_module
from rad_engine.advisories import EngineAdvisory
from rad_engine.env import RadianceEnv
from rad_engine.evalglare import THRESHOLD_FACTOR_ABSOLUTE_BOUNDARY
from rad_engine.rpict import (
    GLARE_QUALITY_PRESETS,
    dgp_ambient_parameters,
    pixel_sampling_jitter,
    render_fisheye,
)
from rad_engine.ies import PhotometryUnverifiedError
from rad_engine.rtrace import ambient_interpolation_radius_m
from rad_engine.safe_io import NotARegularFileError, open_regular_file
from rad_engine.sky import SkyError, SkySpec, generate_sky, sky_description_text, validate_sky_dates

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
from rad_mcp.lit_scene import LitSceneRefusal, commit_lit_support, excluded_advisory, lit_advisories, verify_lit_files
from rad_mcp.paths import PathOutsideRoot, RootsNotConfigured, contain
from rad_mcp.point_in_time_tool import (
    _project_north,
    _resolve_sky,
    _scan_scene_for_inline_commands,
    _scene_files,
    sky_key_advisories,
)
from rad_mcp.sky_site import SkyInputs, resolve_sky_inputs
from rad_mcp.unknown_arguments import UnknownKeysRefused
from rad_mcp.scene_warnings import bsdf_not_applied_advisory, light_redirecting_advisory
from rad_mcp.runtime import Runtime, current, refuse_engine_error
from rad_mcp.timeouts import refuse_inline_timeout

logger = logging.getLogger(__name__)

__all__ = ["DESTRUCTIVE_ACTIONS", "GlareResponse", "GlareResult", "run_glare"]

#: Empty, and that is the claim -- the same property `rad_mcp.annual_tool.DESTRUCTIVE_ACTIONS` and
#: `rad_mcp.point_in_time_tool.DESTRUCTIVE_ACTIONS` export for check 0.7's sweep. `run_glare` takes
#: no `action` argument, deregisters no manifest input and deletes no user file: it writes a
#: rendered HDR and (by default) a check image into this project's `09_images/hdr/`
#: (`rad_engine.ProjectPaths.images_hdr`), both of which it owns.
DESTRUCTIVE_ACTIONS: tuple[str, ...] = ()


# --- nested shapes -------------------------------------------------------------------------------


class _Nested(BaseModel):
    model_config = ConfigDict(extra="forbid")


# R810 moved this from the published text: physically excluding a horizontal opening (swapping its
# material to opaque) was considered at design and rejected, because it returns a number for a
# building that does not exist; `tilt_deg` is `rad_core.apertures.Aperture.tilt_deg`.
class DgpAperture(_Nested):
    """The window group `dgp_window_group` declared this DGP to be for.

    A declaration of scope, not a claim about what the image contains: the fisheye still sees every
    opening in the room, each horizontal opening included, and their light is still in the DGP;
    `DGP_APERTURE_SCOPED` (warn) says so whenever the project has a horizontal opening. `name` is
    the `window_group` input's id, the handle `dgp_window_group` takes. `tilt_deg` is the opening's
    angle from horizontal (0 for a flat skylight, 90 for a vertical window), so a caller can check
    the DGP was scoped to the opening they meant.
    """

    name: str
    tilt_deg: float


# --- the result branches ---------------------------------------------------------------------------


class GlareBase(RadResult):
    """What every `run_glare`-shaped answer carries. Also the union schema's shared base.

    `dgp_aperture` sits here rather than on `GlareResult` alone because `dry_run=True` validates
    the escape and must be able to report what it resolved -- a dry run that silently dropped the
    scope could not tell a caller whether the group they named was the one the tool accepted.
    """

    dgp_aperture: DgpAperture | None = None
    """The aperture `dgp_window_group` named, or `null` when the argument was not given.
    **Structurally always present**, never an absent key, so a caller reads one key either way --
    the same posture `en17037.level_met` takes for its own always-null field."""


# The module docstring's "`en17037.level_met` is always `null`" section is the full argument; the
# pointer to it left the published description under R809 (item 38). R810 moved out the shape note
# (docs/tool-surface.md §13) and why `en17037` is a plain `dict`: built by hand rather than as a
# model whose extra fields pydantic could reshape, so `"ugr" not in payload["en17037"]`, asserted
# over the serialised payload, is a fact about the code and not only about a test. The radius
# sentence is lifted from the field note below, which never reached the wire.
class GlareResult(GlareBase):
    """A successful `run_glare` call: `evalglare`'s values for one fisheye image, one moment.

    `ugr` is `evalglare`'s UGR, an electric-lighting rating that is not a daylight glare
    assessment: `UGR_NOT_FOR_DAYLIGHT` (error) is attached to every result. `en17037` is an object
    whose `level_met` key is always present and always `null`, because EN 17037's glare criterion
    is a fraction of a year's usage time, which one image cannot answer; `moment_below` lists the
    levels whose DGP threshold this one value does not exceed, which is not compliance. `en17037`
    never carries a `"ugr"` key.

    `image` is the fisheye evaluated: the `image` given, or the one this call rendered.
    `check_image` is `evalglare`'s check image when `check_image=true`, else `null`.
    `evalglare_notices` are the lines `evalglare` wrote to stderr. `ambient_interpolation_radius_m`
    is in metres: `quality`'s `-aa` over `-ar` times the rendered octree's bounding-cube edge, or,
    when `image` was given, the project geometry's largest extent (`0` with no geometry), so it
    does not describe how a supplied image was rendered.
    """

    dgp: float
    dgi: float
    ugr: float
    vcp: float
    cgi: float
    lveil: float
    vertical_illuminance_lux: float
    interpretation: str
    en17037: dict[str, Any]
    image: str
    check_image: str | None
    evalglare_notices: list[str]
    ambient_interpolation_radius_m: float
    """``Rmin = maxSize x aa / ar`` (`rad_engine.rtrace.ambient_interpolation_radius_m`) at `quality`
    -- docs/tool-surface.md#16-quality's *"every `run_*` result reports
    `ambient_interpolation_radius_m`"*, mirroring `PointInTimeResult`'s own field
    (`point_in_time_tool.PointInTimeResult.ambient_interpolation_radius_m`). On a render,
    `maxSize` is the octree this call actually traced against; on an `image=` call (nothing
    rendered this call), it is this project's own geometry bounding box's largest edge -- see
    `_room_bounding_box` and `_dispatch`. `AMBIENT_RADIUS_COARSE` (warn) is attached to
    `advisories` when this exceeds one twentieth of the project's smallest room dimension."""


# R810 corrected the old wire text, which said "nothing was run": the aperture read behind
# `_resolve_dgp_scope` spawns `xform` when the project has a window group, above `dry_run`. It also
# moved out why this is a distinct shape (no `dgp`, no `image`, so "validation passed" cannot be read
# as a DGP of zero; `point_in_time_tool.DryRunOk`'s reasoning) and added that a given `image` is only
# contained, not opened, before `dry_run` returns (`read_image`/`check_glare_input` run after it).
class GlareDryRunOk(GlareBase):
    """`dry_run=true` and nothing was refused: the arguments, paths and the project's apertures
    were checked and, without `image`, the sky and scene too. Nothing was rendered and `evalglare`
    was not run, so there is no `dgp`.

    A given `image` is not opened: whether it exists and is a fisheye `run_glare` accepts is
    checked only by the real call. `dgp_aperture` is the group `dgp_window_group` resolved to,
    `null` when it was not given.
    """

    dry_run: Literal[True] = True


GlarePayload = GlareResult | GlareDryRunOk | Refusal


class GlareResponse(RootModel[GlarePayload]):
    """`run_glare`'s result: a `GlareResult`, a `GlareDryRunOk` when `dry_run=true`, or a
    refusal."""

    @classmethod
    def model_json_schema(cls, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return one_of_schema(super().model_json_schema(*args, **kwargs), GlareBase)


# --- argument checks -------------------------------------------------------------------------------


def _refuse_argument(message: str, *, field: str) -> CallToolResult:
    """`ARGUMENT_OUT_OF_RANGE` with the argument's own name in `field` -- ruling R14's code for an
    argument refused on its face."""
    return refuse(
        message,
        [Advisory(code="ARGUMENT_OUT_OF_RANGE", severity="error", field=field, message=message)],
    )


def _refuse_no_fast_quality(quality: str) -> CallToolResult:
    message = (
        f"quality={quality!r} is not one of {sorted(GLARE_QUALITY_PRESETS)}. There is no `fast` "
        "quality for a glare render: a low-quality ambient calculation produces a confident, "
        "plausible, wrong DGP. This is the metric where cheap parameters do the most damage "
        "(docs/tool-surface.md #13)."
    )
    return _refuse_argument(message, field="quality")


def _refuse_missing_view(
    *, image: str | None, view_position: list[float] | None, view_direction: list[float] | None
) -> CallToolResult | None:
    """`image` xor (`view_position` and `view_direction`) -- DGP is a property of an observer, not
    a room (docs/tool-surface.md, `run_glare.view_position` + `.view_direction`)."""
    if image is not None:
        return None
    if view_position is None and view_direction is None:
        message = (
            "run_glare requires either image (an existing fisheye HDR) or both view_position and "
            "view_direction, to render one. DGP is a property of an observer, not a room "
            "(docs/tool-surface.md #13)."
        )
        return _refuse_argument(message, field="image")
    if view_direction is None:
        message = (
            "run_glare was given view_position but no view_direction. DGP is defined from an "
            "observer at a position looking in a specific direction; a position alone does not "
            "name one."
        )
        return _refuse_argument(message, field="view_direction")
    if view_position is None:
        message = (
            "run_glare was given view_direction but no view_position. DGP is defined from an "
            "observer at a position looking in a specific direction; a direction alone does not "
            "name one."
        )
        return _refuse_argument(message, field="view_position")
    return None


def _refuse_missing_sky(sky: dict[str, Any] | str | None) -> CallToolResult | None:
    """`sky` is required on the render branch, refused in `run_glare`'s own vocabulary.

    **Finding I7 of the Phase 8 whole-branch review.** Without this, a `run_glare` call with a view
    and no `sky` fell through to `point_in_time_tool._resolve_sky`, which `_render` reaches with a
    hardcoded `quantity="luminance"`, and its `SkyError` was re-raised verbatim:

        *"sky is required for quantity='illuminance'/'luminance' -- pass a sky dict
        (docs/tool-surface.md §10), or use quantity='daylight_factor' to let the server force the
        reference overcast sky."*

    **`run_glare` has no `quantity` argument.** A model reading that message retries into a schema
    rejection, and the refusal carried no code and no advisory either, unlike every other
    `run_glare` refusal. The message here names only arguments this tool accepts.

    A `str` `sky` is deliberately **not** refused here: `_resolve_sky`'s own answer for one -- that a
    caller-supplied `.rad` path is accepted on the wire and not built -- names no argument
    `run_glare` lacks, and duplicating it would be a second copy of a message that is already right.
    """
    if sky is None:
        message = (
            "run_glare needs a sky to render against: no image= was given, so this call has to "
            "render one, and a fisheye render needs a sky. Pass sky= as a CIE or Perez sky dict "
            "(docs/tool-surface.md §10), for example {'type': 'clear', 'month': 6, 'day': 21, "
            "'hour': 12.0} -- or pass image= with an existing fisheye HDR and no sky is needed."
        )
        return _refuse_argument(message, field="sky")
    if not isinstance(sky, dict | str):
        message = (
            f"sky must be a sky dict (docs/tool-surface.md §10); got {type(sky).__name__}. "
            "run_glare has no other way to place the sun and no default sky to substitute."
        )
        return _refuse_argument(message, field="sky")
    return None


# --- the two files this tool has written for it ------------------------------------------------------


def _run_discriminator() -> str:
    """The per-call suffix on both filenames this tool writes.

    **`int(time.monotonic() * 1000)` alone was not one** (Phase 8 whole-branch review). Its epoch is
    arbitrary and **resets at boot**, so two runs on either side of a restart can produce the same
    integer and the second silently overwrites the first's render -- and a caller reading the
    published `image` path of the earlier one is then looking at a different picture. It also could
    not be told from a real timestamp by anyone reading the filename.

    `rad_engine.jobs.new_job_id` already established the shape for a name that must be unique,
    sortable by creation and a legal path component: a UTC stamp plus eight hex characters of a
    UUID4. This is that shape, reused rather than a third convention -- with a `datetime` stamp,
    which is a real wall-clock time, in place of the monotonic counter.
    """
    return f"{datetime.now(UTC).strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex[:8]}"


def _contain_written_path(path: Path, *, roots: tuple[Path, ...], what: str) -> Path:
    """Refuse `path` before anything is created if its **parent chain** escapes `roots`.

    **Ruling R48's primitive, applied to this tool's two writes -- finding C1 of the Phase 8
    whole-branch review.** `run_glare` derives both of them from `ProjectPaths.images_hdr`
    (`<project>/09_images/hdr/`) and neither was contained: a **directory** symlink planted at
    `09_images/hdr` walked both files out of every allowed root while the call returned success and
    reported the contained-looking path it was asked for.

    This is `rad_mcp.annual_tool._write_contained`'s first guard, and only its first guard, for a
    reason: that function goes on to `open_regular_file(path, "wb")` because *it* does the writing.
    Neither file here is written by this process -- `rpict` writes the render through
    `rad_engine.run.run_to_file`, and `evalglare -c` opens the check image itself
    (`rad_engine.evalglare`'s own module docstring, and `_refuse_unsafe_check_image_destination`,
    which is that module's leaf-component half of the same question). So this is the guard
    `O_NOFOLLOW` on a leaf cannot provide, and it is the half that was missing; the leaf halves were
    already in place downstream.

    **Both halves are now taken here (whole-branch fix wave, B2).** This called
    `resolved_parent_within`, which judges the *parent* only -- the same shape
    `rad_mcp.export_tool`'s module docstring names as Task 11's escape. Measured on a leaf symlink
    inside a root pointing outside every root, `resolved_parent_within` answers `True` and does not
    refuse, while `rad_mcp.paths.contain` raises `PathOutsideRoot`. With the parent-only check the
    whole guarantee for the leaf rested on `O_NOFOLLOW` in `safe_io.open_regular_file`, one flag on
    one line whose removal left 165 tests green -- and for the check image it did not rest there at
    all, since `evalglare -c` opens that file itself. `contain` resolves the whole candidate, so
    both components are judged by the same rule.

    **The original `path` is returned, not `contain`'s resolved one**: `contain` supplies the
    verdict, and substituting the resolved path would change the destination this tool writes to
    and reports.

    Raises `PathOutsideRoot`, which `run_glare`'s handler chain already shapes into a
    `PATH_OUTSIDE_ROOT` refusal (`rad_mcp.runtime.refuse_engine_error`). Nothing is written on that
    path -- the refusal precedes the spawn rather than describing one that already ran.
    """
    try:
        contain(path, roots)
    except PathOutsideRoot as exc:
        raise PathOutsideRoot(
            f"the {what} would be written to {path}, which resolves outside every allowed "
            "root. Nothing has been written. A symlink -- at the file itself or at any directory "
            "in its chain -- inside the project is the usual cause; the files run_glare writes "
            "must stay inside the project they belong to."
        ) from exc
    return path


# --- reading the project's apertures (check 8.10) ---------------------------------------------------


def _project_apertures(
    loaded: LoadedProject, env: RadianceEnv, cwd: Path, *, timeout: float,
    names: Collection[str] | None = None,
) -> list[tuple[str, Any]]:
    """Every registered `window_group` input's derived `Aperture`, `(input id, Aperture)` pairs.

    `names`, when given, keeps only the groups whose recorded `registered_by.arguments["name"]` is
    in it (the key `window_group_tool._bsdf_input_group` reads); a record that carries no name is
    then left out. `results`' EN 17037 provision axis passes the groups whose V matrix to the grid
    it judges is not all zero, so a group behind opaque walls does not decide that grid's table.

    `timeout` is `run_glare`'s own `inline_timeout_s`, and it is a parameter rather than a module
    constant because that argument's published contract is *"bound, in seconds, on **every**
    Radiance spawn this call makes"* -- and this function's `read_scene` (which shells out to
    `xform`) is one of them. It used to run under a hardcoded `_APERTURE_READ_TIMEOUT_S = 60.0`, so
    a caller asking for `inline_timeout_s=5` could wait twelve times the bound they requested
    before anything else even started (Phase 8 whole-branch review, finding I6). The constant is
    gone rather than kept as a maximum: two bounds on one call is exactly the ambiguity the
    published sentence rules out.

    See the module docstring's "Reading the project's apertures" section. A group whose recorded
    modifiers resolve to no polygon at all is skipped -- this check has one question (is there a
    horizontal opening), which an unresolvable group cannot answer either way, and `run_glare` has
    no `metrics` list to name a member of the way `run_annual`'s own `StaleWindowGroupError` does
    for its refusal. **A group skipped here is not visible to `dgp_window_group` either** -- naming
    it is refused as unregistered, by `_resolve_dgp_scope`, which reads this same list; that is the
    honest answer, since the tool cannot say what tilt such a group has. A group whose members do
    not `derive` (`ApertureError`: not coplanar, or no consistent
    normal) is **not** swallowed the same way -- it propagates to `run_glare`'s own handler chain
    and is refused, the same posture `annual_tool._window_groups`'s re-derivation takes for edited
    geometry: a project whose geometry no longer derives is a real defect worth reporting, not a
    silently-skipped group that could hide a horizontal opening from this very check.
    """
    records = [
        record for record in loaded.manifest.inputs
        if record.kind == "window_group"
        and (
            names is None
            or (record.registered_by is not None
                and record.registered_by.arguments.get("name") in names)
        )
    ]
    if not records:
        return []

    geometry_files = [
        ref.path
        for record in loaded.manifest.inputs
        if record.kind == "geometry"
        for ref in record.files
    ]
    by_modifier: dict[str, list[Polygon]] = {}
    if geometry_files:
        scene = read_scene(env, geometry_files, cwd=cwd, timeout=timeout)
        for polygon in scene.polygons:
            by_modifier.setdefault(polygon.modifier, []).append(polygon)

    apertures: list[tuple[str, Any]] = []
    for record in records:
        call = record.registered_by
        names: tuple[str, ...] = ()
        if call is not None:
            raw = call.arguments.get("aperture")
            names = (raw,) if isinstance(raw, str) else tuple(raw or ())
        members: list[Polygon] = []
        for modifier in names:
            members.extend(by_modifier.get(modifier, []))
        if not members:
            continue
        apertures.append((record.id, derive(members)))
    return apertures


def _refuse_horizontal_project(apertures: list[tuple[str, Any]]) -> CallToolResult | None:
    """DGP against a rooflight -- see `docs/tool-surface.md`'s `annual_dgp`-on-a-horizontal-opening
    passage (withdrawn with the metric by R815), the precedent this read rather than reinvented.

    **Refuses on ANY horizontal group**, not only on a project whose groups are all horizontal --
    ruling R278, see the module docstring. A side-lit room with one skylight is refused outright
    **when `dgp_window_group` is not given**, which is the default and the only path that reaches
    this function (`_resolve_dgp_scope` calls it in that branch alone). Check 9.12's escape did not
    weaken the predicate: it added an argument a caller may pass, and passing nothing still lands
    here.

    Takes the already-derived `(input id, Aperture)` pairs rather than deriving them itself.
    `_project_apertures` shells out to `xform`, and `_resolve_dgp_scope` needs the same list for
    the escape's own checks -- deriving twice would double this call's aperture-read spawn for one
    question.

    `ARGUMENT_OUT_OF_RANGE`,
    `field="project"`: the register has no code of its own for this condition (the same reported
    gap `run_annual`'s DGP-on-a-horizontal-opening refusal recorded until R815 withdrew `annual_dgp`), and `field="project"` because
    the offending fact is project geometry, not an argument `run_glare` itself takes -- the same
    convention `run_annual`, `run_point_in_time` and `image` all use for a refusal computed from
    project state (`field="project"` throughout `rad_mcp`). The escape's own refusals blame
    `field="dgp_window_group"` instead, because those *are* about an argument the caller passed.
    """
    horizontal = [(name, aperture.tilt_deg) for name, aperture in apertures if is_horizontal(aperture)]
    if not horizontal:
        return None
    message = (
        f"this project's window group(s) "
        + ", ".join(f"{name!r} (tilt {tilt:.1f} degrees from horizontal)" for name, tilt in horizontal)
        + " are horizontal openings. DGP is not applicable to horizontal openings (EN 17037, "
        "ch03 §5.4): the metric is defined from a seated occupant's view direction toward a "
        "facade, and a rooflight overhead has no such view geometry, so a number computed for one "
        "would not be a DGP. Refused rather than computed. run_glare refuses a project that "
        "contains ANY horizontal window group, not only one whose groups are all horizontal: run "
        "it against a project whose window groups are all vertical openings, deregister every "
        "horizontal window group from this one, or pass dgp_window_group=<a vertical window "
        "group's id> to declare which aperture the DGP is for. That declaration does not remove "
        "the horizontal opening(s) named above from the fisheye -- their light is still in the "
        "number, and the result says so."
    )
    return _refuse_argument(message, field="project")


# --- check 9.12: the escape, `dgp_window_group` ------------------------------------------------

#: Half of `rad_core.glare.FISHEYE_ANGLE_DEG`, and **no number of this module's own**. EN 17037
#: §5.4 gives no angle at all; the bound here is the rendered image's own field of view. A fisheye
#: spans 180 degrees, so it sees exactly the hemisphere in front of the eye, and an aperture every
#: vertex of which lies at 90 degrees or more from `view_direction` is not in the picture the DGP is
#: computed from (the bound is a plane through the eye, so such an aperture lies wholly behind it).
#: Naming such an aperture is refused rather than honoured: the tool would otherwise report a DGP as
#: being "for" an opening the image does not contain. An aperture with a vertex in front of the eye
#: is at least partly in the image and is accepted (the fix-wave re-review: judged by the centroid,
#: a large window straddling the bound was refused though part of it is in the picture).
_FACING_BOUND_DEG = FISHEYE_ANGLE_DEG / 2.0


#: A vertex within this many degrees of `_FACING_BOUND_DEG` lies on the image's horizon: neither in
#: front of the eye nor behind it (an eye on a grid line under a skylight's edge puts that edge at
#: exactly 90 degrees). A chosen value: docs/tool-surface.md, Unsourced values.
_ON_BOUND_TOLERANCE_DEG: Final = 1e-9


def _in_front(angle: float) -> bool:
    return angle < _FACING_BOUND_DEG - _ON_BOUND_TOLERANCE_DEG


def _behind(angle: float) -> bool:
    return angle > _FACING_BOUND_DEG + _ON_BOUND_TOLERANCE_DEG


def _vertex_angles(opening: Any, position: Sequence[float], direction: Sequence[float]) -> list[float]:
    """The angle from `direction` to each vertex of `opening` seen from `position`, in degrees; a
    vertex the eye sits on says nothing and is left out (`angle_between_deg` raises on it)."""
    angles: list[float] = []
    for polygon in opening.polygons:
        for vertex in polygon.vertices:
            try:
                angles.append(angle_between_deg(direction, [c - p for c, p in zip(vertex, position)]))
            except ValueError:
                continue
    return angles


def _aperture_scoped_advisory(group: str, horizontal: list[tuple[str, float]],
                              outside: list[tuple[str, float]] | None = None,
                              partly: list[tuple[str, float]] | None = None) -> Advisory:
    """`DGP_APERTURE_SCOPED` (warn) -- the honesty clause of check 9.12's escape.

    Built only where the escape scoped a DGP **and** this project actually has a horizontal
    opening, because that is the condition the sentence is about: on an all-vertical project
    `dgp_window_group` declares a scope and there is nothing in the image the declaration is
    qualifying. `_resolve_dgp_scope`'s last line is the `if horizontal` that decides it.

    **It says the opening contributes, never that it was excluded.** The escape is a declaration of
    scope plus a facing check; it changes nothing about what `rpict` renders or what `evalglare`
    integrates.

    `horizontal` are the horizontal openings in the fisheye's field of view, `outside` those whose
    every vertex is `_FACING_BOUND_DEG` or more from `view_direction`, and `partly` those with
    vertices on both sides of that bound (final review of 2026-10-05: the message said the fisheye
    "still sees" every horizontal opening, though none was tested; a rooflight behind the eye is
    not in the image and reaches the DGP only through interreflection). The bound is a plane through
    the eye, so an opening whose every vertex is behind it lies wholly behind it. *(The re-review of
    the fix wave: the test was on the centroid, so a large rooflight straddling the bound was called
    outside the image; measured with `rpict -vth -vh 180 -vv 180`, a 2 x 3 m glow rooflight at
    z = 3 m whose centroid is 0.5 m behind an eye at z = 1.2 m lit 1 107 of 40 000 pixels, and only
    the part behind the eye lit none.)*
    """
    def named(openings: list[tuple[str, float]]) -> str:
        return ", ".join(f"{name!r} (tilt {tilt:.1f} degrees from horizontal)" for name, tilt in openings)

    parts = [f"this DGP is reported for the window group {group!r}. This project also has horizontal openings."]
    if horizontal:
        parts.append(
            f"In the {FISHEYE_ANGLE_DEG:.0f}-degree fisheye's field of view: {named(horizontal)}; their light "
            "is in the vertical illuminance and the background luminance this DGP is computed from, so "
            "it contributes to the number."
        )
    if partly:
        parts.append(
            f"Partly in the image (vertices on both sides of {_FACING_BOUND_DEG:.0f} degrees from "
            f"view_direction): {named(partly)}; the part in the image is in the vertical illuminance and "
            "the background luminance this DGP is computed from, and the part outside it reaches this "
            "DGP only through interreflection, so both contribute to the number."
        )
    if outside:
        parts.append(
            f"Outside the image (every vertex {_FACING_BOUND_DEG:.0f} degrees or more from view_direction): "
            f"{named(outside)}; their light reaches this DGP only through interreflection, so it still "
            "contributes to the number."
        )
    parts.append(
        "dgp_window_group declares which aperture the result is about (EN 17037 ch03 §5.4 defines "
        "DGP against a facade, not a rooflight); it does not remove anything from the field of view."
    )
    return Advisory(code="DGP_APERTURE_SCOPED", severity="warn", field="dgp_window_group", message=" ".join(parts))


def _resolve_dgp_scope(
    loaded: LoadedProject,
    env: RadianceEnv,
    cwd: Path,
    *,
    group: str | None,
    position: list[float] | None,
    direction: list[float] | None,
    timeout: float,
) -> tuple[CallToolResult | None, DgpAperture | None, Advisory | None]:
    """Check 9.12: `run_glare`'s horizontal-opening refusal, and the escape from it.

    Returns `(refusal, scoped aperture, advisory)` -- exactly one of the first two is ever
    non-`None`.

    **The order of the checks is the design, not an accident**, and it is the order the plan's
    check 9.12 states:

    1. `group is None` and this project has a horizontal opening -> `_refuse_horizontal_project`,
       ruling R278's default, unweakened. A caller who passes nothing gets exactly what they got
       before this task.
    2. `group` names nothing this project registers -> refused as an unknown handle, the way any
       unresolvable handle is. Before the horizontal check, so a *typo* is never reported as a
       physics refusal: the two messages send a caller to different fixes.
    3. `group` is registered and horizontal -> refused. **The escape must not become a way to ask
       for the number the standard does not define** -- EN 17037 ch03 §5.4 defines DGP against a
       facade, and naming a rooflight does not give it view geometry.
    4. The aperture's centroid and every vertex of it are at `_FACING_BOUND_DEG` or more from
       `view_direction` -> refused, citing the fisheye's own 180-degree field of view (an aperture
       with a vertex in front of the eye is partly in the image and is accepted). See that constant: the bound is the
       image's, not this module's. Two preconditions of that check refuse here as well, in this
       order and above the comparison itself: `view_position`/`view_direction` absent (legal on the
       `image=` branch, and the check cannot run without them), and a view `angle_between_deg`
       refuses to measure -- a zero-length vector, or a length mismatch.
    5. Otherwise the scope stands, and `DGP_APERTURE_SCOPED` is attached **only if** a horizontal
       opening is present.

    Checks 2 to 4 run whether or not this project has a horizontal opening. The argument is
    validated wherever it is given, not only where it rescues a refusal -- otherwise a typo would
    be silently honoured on exactly the projects that never needed the escape.
    """
    apertures = _project_apertures(loaded, env, cwd, timeout=timeout)
    horizontal = [(name, aperture.tilt_deg) for name, aperture in apertures if is_horizontal(aperture)]

    if group is None:
        return _refuse_horizontal_project(apertures), None, None

    by_id = dict(apertures)
    if group not in by_id:
        registered = ", ".join(repr(name) for name in by_id) or "none"
        message = (
            f"dgp_window_group={group!r} is not a registered window group in this project. This "
            f"project's window groups are: {registered}. dgp_window_group takes the input id a "
            "window_group(action='define') call registered, and a group whose recorded aperture "
            "modifiers resolve to no polygon in this project's geometry is not among them."
        )
        return _refuse_argument(message, field="dgp_window_group"), None, None

    aperture = by_id[group]
    if is_horizontal(aperture):
        message = (
            f"dgp_window_group={group!r} is a horizontal opening (tilt "
            f"{aperture.tilt_deg:.1f} degrees from horizontal). DGP is not applicable to "
            "horizontal openings (EN 17037, ch03 §5.4): the metric is defined from a seated "
            "occupant's view direction toward a facade, and a rooflight overhead has no such view "
            "geometry. Naming it does not make it applicable -- this argument declares which "
            "aperture an otherwise-valid DGP is for, and it is refused rather than honoured here. "
            "Name a vertical window group instead."
        )
        return _refuse_argument(message, field="dgp_window_group"), None, None

    if position is None or direction is None:
        message = (
            f"dgp_window_group={group!r} needs view_position and view_direction: the check it "
            "carries is that the named aperture is inside the rendered image, and that is an "
            "angle between the view direction and the direction from the eye to the aperture. "
            "Both are optional when image= supplies an already-rendered fisheye, so pass them "
            "alongside image= as well, or drop dgp_window_group."
        )
        return _refuse_argument(message, field="dgp_window_group"), None, None

    offset = [c - p for c, p in zip(aperture.centroid, position)]
    try:
        angle_deg = angle_between_deg(direction, offset)
    except ValueError as exc:
        # `angle_between_deg` raises on a zero-length vector (its own docstring says why: `nan < 90`
        # is False, so returning NaN would silently accept) and on a length mismatch, which is what
        # a two-element view_position reaches here as. Refused against the argument that made the
        # check run rather than left to `run_glare`'s own `except ValueError`, which exists for a
        # malformed Radiance picture and would blame the wrong thing.
        message = (
            f"dgp_window_group={group!r} could not be checked against the view: {exc} "
            "view_position and view_direction are [x, y, z], and the eye may not sit exactly on "
            f"the aperture's own centroid ({aperture.centroid})."
        )
        return _refuse_argument(message, field="dgp_window_group"), None, None

    vertex_angles = _vertex_angles(aperture, position, direction)
    if angle_deg >= _FACING_BOUND_DEG and not any(_in_front(a) for a in vertex_angles):
        nearest = f" (the nearest {min(vertex_angles):.1f})" if vertex_angles else ""
        message = (
            f"dgp_window_group={group!r} is {angle_deg:.1f} degrees from view_direction at its "
            f"centroid, and every vertex of it is {_FACING_BOUND_DEG:.0f} degrees or more from "
            f"view_direction{nearest}, so it is outside the rendered image. run_glare renders a {FISHEYE_ANGLE_DEG:.0f}-degree "
            f"fisheye (rad_core.glare.FISHEYE_ANGLE_DEG), and that field of view is the "
            "hemisphere in front of the eye: an aperture at "
            f"{_FACING_BOUND_DEG:.0f} degrees or more from the view "
            "direction is not in the picture at all, so a DGP cannot be reported as being for it. "
            "Point view_direction at the aperture, or name the group the view actually faces."
        )
        return _refuse_argument(message, field="dgp_window_group"), None, None

    scoped = DgpAperture(name=group, tilt_deg=aperture.tilt_deg)
    if not horizontal:
        return None, scoped, None
    # Final review: which horizontal openings the fisheye sees, by the facing test above applied to
    # each vertex (the fix-wave re-review: a centroid behind the eye says nothing of an edge in front).
    in_view: list[tuple[str, float]] = []
    straddling: list[tuple[str, float]] = []
    behind: list[tuple[str, float]] = []
    for name, opening in apertures:
        if not is_horizontal(opening):
            continue
        angles = _vertex_angles(opening, position, direction)
        front = any(_in_front(a) for a in angles)
        if angles and not front:  # every vertex behind the eye or on the image's horizon
            behind.append((name, opening.tilt_deg))
        elif front and any(_behind(a) for a in angles):
            straddling.append((name, opening.tilt_deg))
        else:  # in front, with any other vertex on the horizon; or none measurable: in view
            in_view.append((name, opening.tilt_deg))
    return None, scoped, _aperture_scoped_advisory(group, in_view, behind, straddling)


# --- check_glare_input (checks 8.3, 8.4, 8.5) --------------------------------------------------------


def _refuse_check_glare_input(refusals: list[GlareRefusal]) -> CallToolResult:
    """Every fault `check_glare_input` found, folded into one refusal -- never just the first, so
    a caller who fixes one fault learns every other fault still standing."""
    reason = " ".join(refusal.reason for refusal in refusals)
    advisories = [
        Advisory(code="ARGUMENT_OUT_OF_RANGE", severity="error", field=refusal.field, message=refusal.reason)
        for refusal in refusals
    ]
    return refuse(reason, advisories)


# --- rendering (no image= given) ---------------------------------------------------------------------


_NO_GEOMETRY_RECOVERY = (
    "Register some with geometry(action='add') first, or pass image= with an existing "
    "fisheye HDR instead of rendering one -- there is nothing to build an octree from."
)


def _render(
    loaded: LoadedProject,
    paths: ProjectPaths,
    env: RadianceEnv,
    *,
    sky_spec: SkySpec,
    position: tuple[float, float, float],
    direction: tuple[float, float, float],
    resolution: int,
    quality: str,
    luminaires: bool,
    inline_timeout_s: float,
    roots: tuple[Path, ...],
    scene_files: Sequence[str] | None = None,
) -> tuple[Path, tuple[str, ...], QualityParameters, tuple[float, float, float, float], list[Advisory]]:
    """Sky -> octree -> fisheye render, for the `image=None` path.

    `scene_files`, when given, is the geometry/materials/placement list `run_glare` already built
    and scanned; it is compiled as given so the scanned and rendered files cannot diverge. `None`
    (the default) builds it here from `_scene_files`, as before. `sky_spec` is the sky `run_glare`
    resolved and date-checked above `dry_run` (R762, R765; final review FW-2 moved `_resolve_sky`
    out of this function, where it ran below `dry_run` and the dry run accepted skies it refused,
    so it took the raw `sky` and the resolved `site`/`epw_irradiance` until then). Returns
    `(path, warnings, parameters, octree_cube, sky_advisories)`; `sky_advisories` is what the sky
    generator reported on stderr (final review FW-1, `field: "sky"`).

    **`parameters` and `octree_cube`, added Phase 9 Task 5.** `parameters` is `render_fisheye`'s own
    resolved `rad_core.quality.QualityParameters` for `quality` (`rad_engine.rpict.
    dgp_ambient_parameters`); `octree_cube` is this function's own `build_octree(...).bbox`,
    `(x, y, z, size)` from `getinfo -d`. Together they are exactly what `_dispatch` needs to call
    `ambient_interpolation_radius_m(parameters, octree_cube[3])` the way
    `rad_mcp.point_in_time_tool._dispatch` does off its own pipeline -- see that module's `_PipelineResult`.

    Mirrors `rad_mcp.point_in_time_tool`'s own sky -> octree pipeline (`_resolve_sky`/`rad_engine.sky.
    generate_sky`, reused rather than reimplemented -- R30) with `render_fisheye` in place of
    `run_rtrace`.

    **The scene is `point_in_time_tool._scene_files(loaded)`'s, not a hand-built geometry list.**
    This used to collect `kind == "geometry"` inputs only -- that module's geometry half with its
    materials half deleted -- so a project laid out the way docs/tool-surface.md documents
    (`geometry(action="add")` plus `materials(action="set")`, which stores the definitions as a
    separate `kind="materials"` input) compiled with geometry and sky and **no modifiers**, and
    `oconv` exited 1 with `fatal - undefined modifier`, handed raw to a caller the README defines as
    knowing lighting but not Radiance. `rad_engine.materials.scene_files_with_materials`'s own
    docstring names that failure -- *"A caller that instead hand-builds `scene_files` without
    calling this function is, of course, still free to get the order wrong"* -- and the shared
    helper also brings the duplicate-modifier refusal and the coded `NoGeometryRegisteredError`
    the hand-built copy had lost. The sky stays appended last, as it always was: it defines its own
    modifiers and references none of the project's.

    `roots` is `Runtime.roots`, threaded in for `_contain_written_path` -- see that function.
    """
    # **First, before the sky, the octree or any spawn at all.** Where this file lands is knowable
    # from the project alone, and a destination that escapes every root is not made acceptable by
    # the sky generator and the octree build that would otherwise both run before it is discovered
    # -- the same "must precede the spawn rather than follow a wasted one" ordering the module
    # docstring states for `check_glare_input`.
    out_path = _contain_written_path(
        paths.images_hdr / f"glare_{_run_discriminator()}.hdr",
        roots=roots,
        what="rendered fisheye HDR",
    )

    # Ruling R694: `generate_sky` spawns the generator and, for a project declaring a non-zero
    # north, pipes its stdout through a second spawned `xform -rz` -- neither `gensky` nor
    # `gendaylit`'s own `-r` rotates on this Radiance 6.1a.
    generator_advisories: list[EngineAdvisory] = []
    sky_text = sky_description_text(
        generate_sky(env, sky_spec, cwd=paths.root, timeout=inline_timeout_s,
                     advisories=generator_advisories),
    )
    sky_path = paths.root / "_run_glare_sky.rad"
    with open_regular_file(sky_path, "wb") as handle:
        handle.write(sky_text.encode("utf-8"))

    if scene_files is None:
        scene_files = _scene_files(
            loaded,
            luminaires=luminaires,
            no_geometry_recovery=_NO_GEOMETRY_RECOVERY,
        )
    scene_files = [*scene_files, str(sky_path)]
    octree_path = paths.root / "_run_glare.oct"
    octree = build_octree(env, scene_files, out_path=octree_path, cwd=paths.root, timeout=inline_timeout_s)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    rendered = render_fisheye(
        env, octree.path, out_path=out_path, position=position, direction=direction,
        resolution=resolution, quality=quality, cwd=paths.root, timeout=inline_timeout_s,
    )
    return (
        rendered.path, rendered.warnings, rendered.parameters, octree.bbox,
        [to_wire(a, field="sky") for a in generator_advisories],
    )


# --- always-true advisories (checks 8.6, 8.7, plus the docs table's own always-on rows) --------------


def _low_light_correction_advisory() -> Advisory:
    return Advisory(
        code="GLARE_LOW_LIGHT_CORRECTION_APPLIED",
        severity="info",
        message=(
            "evalglare's low-light correction (-C l+) was NOT applied to this DGP. Ruling R257, "
            "measured on this server's evalglare 3.06: the bare default is uncorrected (dgp "
            "0.211771 on the high_fish_view.hdr fixture; -C l+ gives 0.195696 on the same file). "
            "run_glare never passes -C."
        ),
    )


def _no_task_area_advisory() -> Advisory:
    return Advisory(
        code="GLARE_NO_TASK_AREA",
        severity="warn",
        field="task_area",
        message=(
            "No task_area was given. Without -t, a threshold factor of 100 or less multiplies the "
            "whole-picture average rather than a defined task area, which the man page calls "
            '"not recommended". (A threshold_factor above 100 is an absolute cd/m2 threshold '
            "instead and scales nothing, so this advisory is not emitted for one.)"
        ),
    )


def _single_moment_advisory() -> Advisory:
    return Advisory(
        code="GLARE_SINGLE_MOMENT",
        severity="info",
        message=(
            "This is one moment. EN 17037's glare criterion is that DGP must not exceed the "
            "threshold for more than 5 % of usage time over a full year (ch03 §5.4); a single "
            "image cannot establish or refute that. No path in this build answers it either: "
            "annual DGP is not built, and "
            "results(action='compliance', standard='en17037', axis='glare') is refused as not "
            "built. Read the DGP this call returns as this moment's value, not as a level met."
        ),
    )


def _view_not_worst_case_advisory() -> Advisory:
    return Advisory(
        code="GLARE_VIEW_NOT_WORST_CASE",
        severity="info",
        message=(
            "This is one view direction, not a search over the worst case. EN 17037's glare "
            'protection requirement applies "where occupants cannot choose their position or view '
            'direction" (ch03 §5.4); a different position or gaze within the same room could '
            "report a different DGP."
        ),
    )


def _image_provenance_advisory(luminaires_ignored: bool = False) -> Advisory:
    """`GLARE_IMAGE_PROVENANCE_UNKNOWN` (warn, Task 14, closes R459's open cost): on **every**
    `image=` call, whether or not this project has any luminaire placement -- the point is the
    image's own content, not this project's scene. `_render` never runs on this branch, so
    `LUMINAIRES_EXCLUDED` does not fire alongside it (R479's suppression stands, untouched): nothing
    was excluded, because nothing was rendered. Before this advisory, a lit `run_render` HDR passed
    back as `image=` gave a DGP that included the lamps with nothing on the result to say so.

    **`luminaires_ignored` names the argument that was thrown away** (whole-branch review, Important
    3). `luminaires`' own description says it is "Ignored when `image` is given", and nothing on the
    wire said so: `luminaires=True, image=...` returned an ordinary DGP, and a caller who passed
    both had every reason to read this advisory's "cannot know what light produced it" as generic
    boilerplate rather than as a statement that their switch did nothing. The combination is **not**
    refused -- it is harmless, and a caller may pass a project default alongside an image -- so the
    fix is to name it. This argument defaults to `False`, so the sentence only appears for a caller
    who actually asked for the lamps.
    """
    return Advisory(
        code="GLARE_IMAGE_PROVENANCE_UNKNOWN",
        severity="warn",
        field="image",
        message=(
            "image= was not rendered by this call, so run_glare cannot know what light produced "
            "it -- daylight, electric, or both. If it came from a lit run_render, any luminaire it "
            "shows is not flagged separately: LUMINAIRES_EXCLUDED does not fire on this branch "
            "(R479) because nothing here was excluded -- this call rendered nothing at all. This "
            "DGP reflects whatever light is actually in the image, not a fact this server measured."
            + (" luminaires=true was IGNORED: it lights the render branch's scene, and this call "
               "rendered nothing, so it neither added a lamp to this image nor confirmed that one "
               "is in it. Whether this DGP includes electric light is a property of the image you "
               "supplied. Render the fisheye here -- omit image= and give sky and view_position/"
               "view_direction -- if you need the lamps to be in it by construction."
               if luminaires_ignored else "")
        ),
    )


def _ugr_not_for_daylight_advisory() -> Advisory:
    return Advisory(
        code="UGR_NOT_FOR_DAYLIGHT",
        severity="error",
        field="ugr",
        message=(
            "ugr is evalglare's Unified Glare Rating, an electric-lighting metric. EN 17037 "
            "§5.4.2 forbids substituting an electric-lighting glare rating for DGP as a daylight "
            "glare assessment. ugr is returned in the raw block for reference only, and is "
            "excluded from en17037."
        ),
    )


def _pixel_sampling_conflict_advisory(dj: float, pj: float, ps: float) -> Advisory:
    """`PIXEL_SAMPLING_CONFLICT` (warn) -- docs/tool-surface.md #16: *"It is usually wise to turn
    off image sampling when using direct jitter by setting Pixel Sampling (-ps) to 1"*
    (`docs/specs/rpict-parameter-guide.md`, note A; `-pj` carries the same note, note B). Fires by
    construction on `run_glare`'s own default, `quality="accurate"` -- `-ps 4` with `-dj 0.7` and
    `-pj 0.9` (`rad_engine.rpict.GLARE_QUALITY_PRESETS`) -- so this is not a hypothetical the tests
    merely allow; every default-quality render carries it.
    """
    jittered = ", ".join(
        f"-{flag} {value:g}" for flag, value in (("dj", dj), ("pj", pj)) if value > 0
    )
    return Advisory(
        code="PIXEL_SAMPLING_CONFLICT",
        severity="warn",
        field="quality",
        message=(
            f"this quality preset sets {jittered} (direct/positional jitter above zero) together "
            f"with pixel sampling above one (-ps {ps:g}). \"It is usually wise to turn off image "
            "sampling when using direct jitter by setting Pixel Sampling (-ps) to 1\" "
            "(docs/specs/rpict-parameter-guide.md, note A/B) -- jittered shadow rays under adaptive "
            "pixel subdivision can produce speckle that reads as noise and is misdiagnosed as an "
            "ambient problem."
        ),
    )


def _build_advisories(
    *,
    resolution: tuple[int, int],
    notices: tuple[str, ...],
    render_warnings: tuple[str, ...],
    task_area: list[float] | None,
    threshold_factor: float | None,
    quality: str,
) -> list[Advisory]:
    # `resolution_notes` returns `(code, severity, message)` triples, but `code`/`severity` are
    # taken as *data* here, not as literals -- `tests/test_advisories.py`'s AST scan (the register
    # enforcement, docs/tool-surface.md#advisory-code-register) only trusts a code/severity it can
    # read as a literal string at the `Advisory(...)` call site, so the wire code is spelled out by
    # hand rather than piped through: `resolution_notes`'s own contract is "one `GLARE_RESOLUTION_
    # SLOW`/`warn` triple above 1500 px, `[]` at or below" (`rad_core.glare`'s own docstring), and
    # this is the code and severity that contract has always named.
    advisories: list[Advisory] = [
        Advisory(code="GLARE_RESOLUTION_SLOW", severity="warn", message=message)
        for _code, _severity, message in resolution_notes(resolution)
    ]
    advisories.extend(to_wire_all(classify(list(render_warnings))))
    advisories.extend(to_wire_all(classify(list(notices))))
    # `<=` and not `<`, because `evalglare -b`'s own boundary is `> 100` for the absolute reading:
    # exactly 100 is still a *factor* and still multiplies the whole-picture average when no `-t` is
    # given, which is the condition this advisory reports. The number is
    # `rad_engine.evalglare.THRESHOLD_FACTOR_ABSOLUTE_BOUNDARY`, which carries the man-page text it
    # comes from -- it was a bare, uncited `100` in this layer, which CONTEXT.md says is thin, with
    # no physics, until the Phase 8 whole-branch fix wave.
    if task_area is None and (
        threshold_factor is None or threshold_factor <= THRESHOLD_FACTOR_ABSOLUTE_BOUNDARY
    ):
        advisories.append(_no_task_area_advisory())
    advisories.append(_low_light_correction_advisory())
    advisories.append(_single_moment_advisory())
    advisories.append(_view_not_worst_case_advisory())
    advisories.append(_ugr_not_for_daylight_advisory())
    dj, pj, ps = pixel_sampling_jitter(quality)
    if (dj > 0 or pj > 0) and ps > 1:
        advisories.append(_pixel_sampling_conflict_advisory(dj, pj, ps))
    return advisories


# --- ambient_interpolation_radius_m and AMBIENT_RADIUS_COARSE (Phase 9 Task 5) -------------------


def _ambient_radius(parameters: QualityParameters, max_size: float) -> float:
    """`ambient_interpolation_radius_m(parameters, max_size)`, guarded against a real division by
    zero the ambient family never has to worry about.

    **`GLARE_QUALITY_PRESETS["maximum"]` sets `-aa 0` together with `-ar 0`.** Both say "ambient
    interpolation disabled" the way the *ambient* family's own `reference` preset does with `-aa
    0.0` alone -- `ambient_interpolation_radius_m`'s own docstring: "a zero-radius ambient sphere...
    is what disables ambient interpolation entirely". `reference`'s `-ar` is 512 (nonzero), so its
    division is `0.0 / 512 == 0.0` without ever dividing by zero; `maximum`'s `-ar 0` divides
    `0.0 / 0`, which Python raises as `ZeroDivisionError` even though the numerator is already zero.
    Measured: `ambient_interpolation_radius_m(dgp_ambient_parameters("maximum"), 42.0)` raises.
    Every `run_glare(quality="maximum")` call would therefore crash inside this tool with an uncoded
    exception -- this is a real, reachable defect Task 5's own new call site would otherwise
    introduce, not a hypothetical one. `ambient_accuracy == 0.0` is checked first and short-circuits
    to `0.0` directly -- the same answer the division gives everywhere it does not crash -- rather
    than widening `rad_core.quality.ambient_interpolation_radius_m` itself, which this task's file
    list does not include.
    """
    if parameters.ambient_accuracy == 0.0:
        return 0.0
    return ambient_interpolation_radius_m(parameters, max_size)


def _room_bounding_box(
    loaded: LoadedProject, env: RadianceEnv, cwd: Path, *, timeout: float
) -> BoundingBox | None:
    """This project's own registered geometry, as one axis-aligned box -- `AMBIENT_RADIUS_COARSE`'s
    comparison target (docs/tool-surface.md's Advisory code register) and the `image=` path's own
    fallback `max_size` for `ambient_interpolation_radius_m` (see `_dispatch`).

    **Never the octree's own bounding cube.** That cube is a single enclosing edge over the *whole*
    compiled scene, ground plane and sky included -- `rad_engine.rtrace.
    ambient_interpolation_radius_m`'s own docstring measures the office fixture's at ~42 m against a
    3 m-tall room -- so comparing an interior interpolation radius against it would essentially
    never fire. Task 5's brief gives this reasoning; `rad_mcp.annual_tool` carries its own copy of
    it for the identical advisory (R30: two tools, one reasoning, not one definition, because
    `rad_mcp` modules do not import each other's private helpers).

    Reads every registered `kind == "geometry"` file through `rad_engine.scene.read_scene`, the same
    route `_project_apertures` above already takes for aperture derivation -- a **second** `xform`
    spawn on a project that also has a window group registered (the two reads are not merged:
    `_project_apertures` returns early when no `window_group` input exists, while this runs on
    every call reaching it), bounded by `timeout` like every other spawn `run_glare` makes after the
    install probe (`discover()`, under `rad_engine.env`'s fixed 10 s).

    `None` only when this project has no registered `geometry` input at all, or the read yields no
    polygon -- the one case ruling R313 allows `AMBIENT_RADIUS_COARSE` to raise nothing for, rather
    than substitute the octree cube. Reachable here in a way it is not for `run_annual`: `image=`
    given with no `geometry` ever registered is a legal `run_glare` call (`_render`, which requires
    geometry, never runs on that path).
    """
    geometry_files = [
        ref.path
        for record in loaded.manifest.inputs
        if record.kind == "geometry"
        for ref in record.files
    ]
    if not geometry_files:
        return None
    scene = read_scene(env, geometry_files, cwd=cwd, timeout=timeout)
    if not scene.polygons:
        return None
    return bounding_box(scene.polygons)


def _ambient_radius_coarse_advisory(radius_m: float, smallest_dimension_m: float) -> Advisory:
    """`AMBIENT_RADIUS_COARSE` (warn) -- docs/tool-surface.md's Advisory code register: fires when
    the ambient interpolation radius exceeds one twentieth of this project's smallest room
    dimension. The threshold is computed once, at the call site, and passed in rather than
    recomputed here, so the message and the `if` that decided to build this advisory can never
    disagree about which number crossed which line.
    """
    return Advisory(
        code="AMBIENT_RADIUS_COARSE",
        severity="warn",
        field="quality",
        message=(
            f"the ambient interpolation radius ({radius_m:.4g} m) exceeds one twentieth of this "
            f"project's smallest room dimension ({smallest_dimension_m:.4g} m -> "
            f"{smallest_dimension_m / 20:.4g} m). Cached irradiance is being interpolated over a "
            "sphere large relative to the room, which can blur real gradients near walls and "
            "windows. Raise -ar (ambient resolution) or lower -aa (ambient accuracy) for this "
            "quality preset (docs/tool-surface.md #10)."
        ),
    )


# --- the tool ------------------------------------------------------------------------------------


def run_glare(
    project: Annotated[
        str | None,
        Field(
            description=(
                "the project handle. Required. Refused if **any** registered window group is a "
                "horizontal opening (a rooflight) -- DGP is not defined for one -- unless "
                "`dgp_window_group` names a vertical one to scope the result to. That refusal is "
                "the default; scoping is one way past it, and the refusal message names others -- "
                "deregistering every horizontal window group in this project is one of those."
            )
        ),
    ] = None,
    view_position: Annotated[
        list[StrictFloat] | None,
        Field(
            description=(
                "[x, y, z] metres, the eye position. REQUIRED unless `image` is given."
            )
        ),
    ] = None,
    view_direction: Annotated[
        list[StrictFloat] | None,
        Field(
            description=(
                "[x, y, z], the view direction (need not be normalised). REQUIRED unless `image` "
                "is given."
            )
        ),
    ] = None,
    sky: Annotated[
        dict[str, Any] | str | None,
        Field(
            description=(
                "a CIE/Perez sky dict (docs/tool-surface.md §10). REQUIRED when rendering (i.e. "
                "when `image` is not given) -- refused on the argument alone, before anything is "
                "rendered. Ignored when `image` is given. A sky dict's site is `latitude`, "
                "`longitude_east`, `meridian_east` (degrees, east-positive), all three or none; "
                "with none, the project's registered weather file supplies it "
                "(`SKY_LOCATION_FROM_EPW`), and with no weather file the call is refused "
                "(`SKY_SITE_REQUIRED`). The old `longitude`/`meridian` keys are refused: they were "
                "west-positive (R762)."
            )
        ),
    ] = None,
    image: Annotated[
        str | None,
        Field(
            description=(
                "an absolute path to an existing fisheye HDR, used instead of rendering one. Must "
                "already satisfy `rad_core.glare.check_glare_input` (hemispherical or angular "
                "fisheye, 180-degree view angles, at least 800x800, square pixels) -- an image "
                "that does not is refused before any `evalglare` process is spawned."
            )
        ),
    ] = None,
    resolution: Annotated[
        StrictInt,
        Field(
            description=(
                "pixel resolution for a render (`image` not given). Square; both `-x`/`-y`. "
                "Recommended 1000, minimum 800 (`rad_core.glare.MIN_RESOLUTION_PX`), maximum 4000 "
                "(`MAX_RESOLUTION_PX`, a chosen bound -- docs/tool-surface.md's Unsourced values). "
                "Both bounds are refused on the argument alone, before anything is rendered. "
                "Between 1500 and the ceiling a render is accepted and carries "
                "`GLARE_RESOLUTION_SLOW` (warn); the sourced 1500 figure is about runtime, not "
                "validity, and is never a refusal."
            )
        ),
    ] = 1000,
    quality: Annotated[
        Literal["accurate", "maximum"],
        Field(
            description=(
                "`\"accurate\"` or `\"maximum\"` -- "
                "`rad_engine.rpict.GLARE_QUALITY_PRESETS`'s whole domain, and the two columns of "
                "that name in "
                "`docs/specs/rpict-parameter-guide.md`'s DGP preset table (rulings R262, R299). No "
                "other value, ever: see the tool description."
            )
        ),
    ] = "accurate",
    task_area: Annotated[
        list[StrictFloat] | None,
        Field(
            description=(
                "[x, y, angle] -- `evalglare -t`, the task area for threshold scaling. Omitted, "
                "the whole-picture average is used instead, which the man page calls \"not "
                "recommended\" and which carries `GLARE_NO_TASK_AREA` (warn) -- but only when "
                "`threshold_factor` is absent or <= 100, since above 100 that flag is an absolute "
                "cd/m2 threshold and scales nothing."
            )
        ),
    ] = None,
    threshold_factor: Annotated[
        StrictFloat | None,
        Field(
            description=(
                "`evalglare -b`. Must be > 0 -- 0 and negatives are accepted by the binary and "
                "answer with a sentinel `ugr` of -99 at exit 0, so they are refused here. At or "
                "below 100 it multiplies an average luminance (the task area's, or the whole "
                "picture's if no `task_area` is given); above 100 it is an absolute cd/m2 "
                "threshold. Omitted, `evalglare`'s own default applies."
            )
        ),
    ] = None,
    check_image: Annotated[
        bool,
        Field(
            description=(
                "whether to also write a marked check image (`evalglare -c`) into this project's "
                "`09_images/hdr/` directory. Default `True`."
            )
        ),
    ] = True,
    background_mode: Annotated[
        Literal["cie", "average", "ev_over_pi"],
        Field(
            description=(
                "`\"cie\"` (default, `-q 0`), `\"average\"` (`-q 1`) or `\"ev_over_pi\"` (`-q 2`). "
                "Ruling R269, measured: this moves `dgi` and `ugr` only -- `dgp`, `vcp`, `cgi` and "
                "`lveil` are identical across all three."
            )
        ),
    ] = "cie",
    dgp_window_group: Annotated[
        str | None,
        Field(
            description=(
                "the `window_group` input id this DGP is declared for: a scope, not a filter, so the "
                "DGP still counts every opening in the image (`DGP_APERTURE_SCOPED`, warn, if the "
                "project has a horizontal one). It escapes `project`'s horizontal-opening refusal "
                "(ruling R302). Given, it is validated on any project: an unregistered id, a "
                "**horizontal** group and a group with no vertex less than 90 degrees from `view_direction` "
                "are refused. The result echoes the group as "
                "`dgp_aperture: {name, tilt_deg}`. Needs `view_position` and `view_direction`, "
                "which are otherwise optional on the `image=` branch."
            )
        ),
    ] = None,
    luminaires: Annotated[
        bool,
        Field(
            description=(
                "light the render branch's scene with every placement (`luminaire` tool), so the "
                "fisheye shows the lamps -- this is about what is in the picture, not a lamp-only "
                "render, so `sky` stays required either way. Ignored when `image` is given: "
                "nothing is rendered on that branch, and `GLARE_IMAGE_PROVENANCE_UNKNOWN` says so "
                "when both are passed. Default `False`: the fisheye compiles this project's "
                "geometry only, and any placement it left out carries `LUMINAIRES_EXCLUDED` (warn, "
                "field `\"luminaires\"`) when this project has one."
            )
        ),
    ] = False,
    dry_run: Annotated[
        bool,
        Field(
            description=(
                "validate every argument, path and this project's aperture geometry without "
                "rendering or spawning `evalglare`. `sky` and `resolution` are checked before the "
                "dry run stops, not after it, so `dry_run=True` with no `sky` and no `image` is "
                "refused (`ARGUMENT_OUT_OF_RANGE`, `field=\"sky\"`) rather than validating cleanly "
                "-- it reports every refusal a real run would, short only of the steps a dry run "
                "skips: a lit render's `source.cal` write, the render, `evalglare` itself and "
                "opening a given `image` (a missing or non-fisheye `image` passes the dry run)."
            )
        ),
    ] = False,
    inline_timeout_s: Annotated[
        StrictFloat,
        Field(
            description=(
                "bound, in seconds, on every Radiance spawn this call makes after the install "
                "probe (`discover()`'s `rpict -version`, then `rtrace -version` if needed, each "
                "under `rad_engine.env`'s fixed 10 s) -- the `xform` that reads this project's "
                "geometry to derive its apertures, the sky generator, the `xform` that rotates the "
                "sky when the project declares a non-zero north, the octree build, the fisheye "
                "render, the four spawns behind the image-header read, and `evalglare` itself. It "
                "bounds each spawn, not their total. This tool always completes inline; there is "
                "no job hand-off."
            )
        ),
    ] = 20,
) -> GlareResponse:
    """Daylight glare probability (DGP) from a fisheye HDR, via `evalglare`.

    Use it for the daylight glare of one view at one moment; for a picture of the scene use
    `run_render`, and for an electric-lighting glare rating (UGR) use `run_ugr`.

    It refuses a `fast` quality, a resolution below 800 and an `image` that is not a 180-degree
    fisheye, because DGP is the metric where cheap inputs do the most damage; an `image` it did not
    render (`GLARE_IMAGE_PROVENANCE_UNKNOWN`) and a coarse ambient radius (`AMBIENT_RADIUS_COARSE`)
    are accepted with a warning. There is **no `fast` quality** -- `quality` accepts only `"accurate"` and
    `"maximum"`; any other value is refused, because a low-quality ambient calculation produces a
    confident, plausible, wrong DGP (docs/tool-surface.md #13). `en17037.level_met` is **always
    null** from this tool: EN 17037's glare criterion is a 5 %-of-usage-time-over-a-year test, and
    one image is one instant, so this field cannot honestly say a space "meets" a level from a
    single call. **No path in this build populates a glare `level_met`**: annual DGP is not built,
    and `results(action="compliance", standard="en17037", axis="glare")` is refused, not built. The
    `level_met` values this server does fill are EN 17037 daylight *provision* --
    `results(action="compliance", standard="en17037", axis="provision")` off
    `run_annual(metrics=["en17037_provision"])` -- and view out and sunlight exposure
    (`run_view_and_sunlight`): different criteria, none of them a glare answer. `ugr`
    is returned (in the raw block, for reference) but it is an electric-lighting metric: it carries
    `UGR_NOT_FOR_DAYLIGHT` at `error` severity on every successful result, although the call
    succeeds, and it is structurally absent from
    `en17037` (EN 17037 §5.4.2 forbids substituting it for DGP).

    `dgp_window_group` scopes a DGP past the rooflight refusal described under `project`. It is a
    **declaration of scope, not a filter**: the fisheye still sees every opening in the fisheye's
    field of view and every horizontal one's light is still in the DGP, which
    `DGP_APERTURE_SCOPED` (warn) states on every result this argument produces from a project that
    has at least one.
    """
    runtime = current()
    try:
        return _dispatch(
            runtime,
            project=project,
            view_position=view_position,
            view_direction=view_direction,
            sky=sky,
            image=image,
            resolution=resolution,
            quality=quality,
            task_area=task_area,
            threshold_factor=threshold_factor,
            check_image=check_image,
            background_mode=background_mode,
            dgp_window_group=dgp_window_group,
            luminaires=luminaires,
            dry_run=dry_run,
            inline_timeout_s=inline_timeout_s,
        )
    except (LitSceneRefusal, PhotometryUnverifiedError) as exc:
        # Task 13 change 2: `PhotometryUnverifiedError` is a `RuntimeError` and, before this
        # handler, escaped `_dispatch` entirely -- every lit refusal `commit_lit_support` could
        # raise (a missing/foreign root source.cal, or a failed re-registration of its support
        # record) reached the wire uncoded. Matches `render_tool.run_render`'s, `point_in_time_tool.
        # run_point_in_time`'s and `electric_tool.run_electric`'s own handler for the same two exceptions -- the code,
        # severity and field are the exception's own.
        logger.info("run_glare refused: %s", exc)
        return refuse(str(exc), [Advisory(code=exc.code, severity=exc.severity, field=exc.field, message=str(exc))])
    except (ApertureError,) as exc:
        logger.info("run_glare refused: %s", exc)
        code = getattr(exc, "code", None)
        if code is None:
            return refuse(str(exc))
        return refuse(
            str(exc),
            [Advisory(code=code, severity=exc.severity, field="project", message=str(exc))],
        )
    except SceneInlineCommandError as exc:
        # Rulings R33/R753 (final review): a registered scene file carrying a hostile `!` line, or
        # an include tree too large to verify, is refused before anything reads it. Shaped with its
        # own allocated code, as `annual_tool` shapes it, rather than escaping as an uncoded
        # ToolError carrying an absolute path (check 0.4).
        logger.info("run_glare refused: %s", exc)
        return refuse(
            str(exc),
            [Advisory(
                code=getattr(exc, "code", "SCENE_INLINE_COMMAND"), severity="error",
                field="project", message=str(exc),
            )],
        )
    except SceneFileNotFoundError as exc:
        logger.info("run_glare refused: %s", exc)
        return refuse(
            str(exc),
            [Advisory(code=exc.code, severity=exc.severity, field="project", message=str(exc))],
        )
    except (EmptyOctreeError, OctreeSetOverflowError, OctreeTooManyFilesError) as exc:
        logger.info("run_glare refused: %s", exc)
        code = getattr(exc, "code", None)
        if code is None:
            return refuse(str(exc))
        return refuse(
            str(exc),
            [Advisory(code=code, severity=getattr(exc, "severity", "error"), message=str(exc))],
        )
    except UnknownKeysRefused as exc:
        logger.info("run_glare refused: %s", exc)
        return refuse(str(exc), exc.advisories)
    except SkyError as exc:
        logger.info("run_glare refused: %s", exc)
        if exc.code is not None:
            return refuse(
                str(exc),
                [Advisory(code=exc.code, severity=exc.severity, field=exc.field, message=str(exc))],
            )
        return refuse(str(exc))
    except RadianceNotFound as exc:
        logger.info("run_glare refused: %s", exc)
        return refuse(
            str(exc), [Advisory(code="RADIANCE_NOT_FOUND", severity="error", message=str(exc))]
        )
    except NotARegularFileError as exc:
        # **`field` is only `"image"` when the caller supplied one.** This handler used to name
        # `image` unconditionally, but `NotARegularFileError` also arrives from `_render`'s sky
        # write and from `rad_engine.evalglare`'s check-image destination guard -- neither of which
        # a caller who passed no `image` can fix by fixing `image` (Phase 8 whole-branch review).
        # `field="project"` for those, the convention `rad_mcp` uses throughout for a refusal about
        # project state rather than an argument, the same one `_refuse_horizontal_project` gives.
        logger.info("run_glare refused: %s", exc)
        field = "image" if image is not None else "project"
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
                    field=field,
                    message=message,
                )
            ],
        )
    except (ProjectError, PathOutsideRoot, RootsNotConfigured) as exc:
        logger.info("run_glare refused: %s", exc)
        return refuse_engine_error(exc)
    except ValueError as exc:
        # Not a well-formed Radiance picture (`read_image`'s own parsers) or an unsupported
        # background_mode -- neither of which the register has a code for, so this is reported
        # uncoded rather than reused onto a code that means something else (the same posture
        # `rad_mcp.image_tool` takes for the identical `read_image` parse failure). The "no
        # registered geometry" case is **not** here: since the Phase 8 whole-branch review
        # `_render` goes through `point_in_time_tool._scene_files`, whose
        # `NoGeometryRegisteredError` is a coded `ProjectError` caught above.
        logger.info("run_glare refused: %s", exc)
        return refuse(str(exc))
    except RadianceError as exc:
        logger.info("run_glare failed: %s", exc)
        return refuse(str(exc))
    except OSError as exc:
        logger.warning("run_glare failed on the filesystem: %s", exc)
        return refuse(
            f"run_glare could not complete: {exc}. Nothing about the arguments is wrong -- the "
            "filesystem refused the operation, so this is an environment problem (permissions, a "
            "read-only mount, or a file held open elsewhere)."
        )


def _dispatch(
    runtime: Runtime,
    *,
    project: str | None,
    view_position: list[float] | None,
    view_direction: list[float] | None,
    sky: dict[str, Any] | str | None,
    image: str | None,
    resolution: int,
    quality: str,
    task_area: list[float] | None,
    threshold_factor: float | None,
    check_image: bool,
    background_mode: str,
    dgp_window_group: str | None,
    luminaires: bool,
    dry_run: bool,
    inline_timeout_s: float,
) -> CallToolResult:
    call = "run_glare"
    if (refusal := refuse_missing(call, project=project)) is not None:
        return refusal
    assert project is not None

    if quality not in GLARE_QUALITY_PRESETS:
        return _refuse_no_fast_quality(quality)

    if (refusal := _refuse_missing_view(
        image=image, view_position=view_position, view_direction=view_direction
    )) is not None:
        return refusal

    if task_area is not None and len(task_area) != 3:
        return _refuse_argument(
            f"task_area must be [x, y, angle] (3 values); got {len(task_area)}.",
            field="task_area",
        )

    if threshold_factor is not None and threshold_factor <= 0:
        # **Measured, not assumed** (Phase 8 whole-branch review): `evalglare -b -5` and
        # `evalglare -b 0` on `tests/fixtures/hdr/high_fish_view.hdr` both exit **0** and print
        # `dgp 0.190808 ... ugr -99.000000 ... vcp 2.303815 ... Lveil 1342.409058` against the
        # baseline's `0.211771 / 18.194143 / 42.333836 / 27.245396`. `-99` is a sentinel, not a
        # rating, and nothing on stderr says so -- so without this refusal `run_glare` returns a
        # degenerate DGP and a sentinel UGR as an ordinary unflagged result. This is not an
        # injection (the argv is built list-form and `-5` is never re-parsed as a flag); it is a
        # value the binary accepts and answers nonsense for.
        message = (
            f"threshold_factor={threshold_factor!r} must be greater than 0. Measured on this "
            "server's evalglare 3.06: -b 0 and -b -5 both exit 0 with no stderr notice and return "
            "dgp 0.190808 with ugr -99.000000 -- a sentinel, not a rating -- where the same image "
            "gives dgp 0.211771 and ugr 18.194143 at the default. A number this tool cannot tell "
            "from a real result is refused rather than reported. evalglare -b is a threshold "
            "factor: at or below 100 it multiplies an average luminance, above 100 it is an "
            "absolute cd/m2 threshold."
        )
        return _refuse_argument(message, field="threshold_factor")

    # **`inline_timeout_s` goes through the one shared spawn-timeout check** (Phase 13 Branch C,
    # ruling R704 -- `rad_mcp.timeouts.refuse_inline_timeout`). Before R704 `run_glare` checked
    # `inline_timeout_s` nowhere at all: every one of `nan`, `inf`, `1e9`, `0`, `-1` and JSON
    # `true` reached a clean `dry_run` success on a project with no window group (measured,
    # `rad_mcp.timeouts`'s own docstring); on one with a window group, `_resolve_dgp_scope`'s
    # `xform` read above `dry_run` raised an uncaught `OverflowError` for `1e9`. A live call would
    # have passed any of those straight to the first `subprocess.run(..., timeout=...)` spawn --
    # the aperture `xform` read below, `gensky`, `oconv` or `evalglare`. No
    # `isinstance(bool)` exemption: this is new validation, not a tool with an existing boolean
    # behaviour to preserve.
    if (refusal := refuse_inline_timeout(inline_timeout_s)) is not None:
        return refusal

    resolved_image: Path | None = None
    if image is not None:
        resolved_image = contain(image, runtime.roots)

    loaded = runtime.open(project)
    # Design §5.11, amendment 4: a second, earlier gate than `_render`'s own
    # `point_in_time_tool._scene_files` call. The reason it is load-bearing, not merely doubled, is
    # unconditional: `dry_run=True` returns below without ever calling `_render`, and the `image=`
    # branch skips `_render` too -- but that branch still reads this project's own geometry, through
    # `_room_bounding_box` (below), to compute `max_size`'s bounding-box fallback for the ambient
    # radius. An unscaled record's coordinates would feed straight into both if this gate did not
    # sit above the branch that reads them. Neither branch reaches the shared reader by any route,
    # so this is their only gate.
    # `_resolve_dgp_scope` below reads this project's geometry too, through `_project_apertures`,
    # but only when a `window_group` is registered (`if not records: return []`) -- a real reason on
    # some projects, never the one this gate depends on.
    refuse_unscaled_geometry(loaded)
    paths = ProjectPaths(loaded.paths.root)
    env = discover()

    refusal, dgp_aperture, scope_advisory = _resolve_dgp_scope(
        loaded, env, paths.root,
        group=dgp_window_group, position=view_position, direction=view_direction,
        timeout=inline_timeout_s,
    )
    if refusal is not None:
        return refusal

    # **The render branch's own two arguments, refused before entering that branch** -- findings I2
    # and I7. Both are knowable from the call alone and both used to be discovered only once
    # `gensky`, `oconv` and a full fisheye had been paid for.
    #
    # **Below `_resolve_dgp_scope`, not above it, and that ordering is a choice.** A project whose
    # window groups include a horizontal opening can never produce a DGP from this tool unless
    # `dgp_window_group` scopes one, whatever `sky` and `resolution` say, so reporting a fixable
    # argument first would send a caller to fix one thing and be refused again for a condition
    # `sky` and `resolution` cannot address. That was the stated reasoning of `annual_tool`'s
    # DGP-on-a-horizontal-opening refusal for its placement (withdrawn with `annual_dgp` by R815) --
    # *"a phase-calendar fact invites a caller to come back and only then be told DGP was never
    # defined for their rooflight"* -- applied one axis over. The cost is `discover()` and the one
    # `xform` the aperture read spawns, which every `run_glare` call already pays; no sky, no
    # octree and no render is ever spawned for a call refused here.
    #
    # **`refuse_unscaled_geometry`, above even `_resolve_dgp_scope`, already reports the identical
    # kind of fact ahead of these two** -- a pre-Phase-10 unscaled record is a project condition
    # neither `sky` nor `resolution` can repair, so it joined this same ordering rather than being
    # left to surface only once one of them was validated.
    #
    # **Above `dry_run`**, so `dry_run=True` reports both, which is what *"validate every argument,
    # path and this project's aperture geometry"* promises.
    lit_advisories_list: list[Advisory] = []
    scene_warnings: list[Advisory] = []
    if image is None:
        if (refusal := _refuse_missing_sky(sky)) is not None:
            return refusal
        if refusals := check_render_resolution(resolution):
            return _refuse_check_glare_input(refusals)
        # R694: a non-finite north on a hand-edited manifest is refused here, above `dry_run`, by
        # the one check all three sky tools share; the sky resolution below reads it again.
        _project_north(loaded)

        # Fix round 1 (Critical, reviewer-measured): before this, `luminaires=True` read placement
        # files straight into `_scene_files`/`_render` with nothing checking them -- a project with
        # no placement returned an ordinary DGP at exit 0 (no `LUMINAIRES_NONE_PLACED`), a tampered
        # `12_luminaires/*.rad` returned a DGP silently (no `LUMINAIRE_FILES_CHANGED`), and the R489
        # instance bound and `LUMINAIRE_MODIFIER_COLLISION` were skipped the same way. It also broke
        # `point_in_time_tool._scene_files`'s own stated contract -- *"The placement files are
        # checked by `rad_mcp.lit_scene.verify_lit_files` in the caller"*. `verify_lit_files` writes
        # nothing (R569), so it runs here, above `dry_run`, exactly where `render_tool._dispatch`
        # runs its own -- and it checks `LUMINAIRES_NONE_PLACED` before it ever reads `source.cal`
        # state, so a project with neither a placement nor a `source.cal` is refused for the reason
        # that actually applies (no placement), not `LUMINAIRE_PHOTOMETRY_UNVERIFIED`.
        if luminaires:
            verify_lit_files(runtime, loaded)
            # R821: a lit DGP carries `RESULT_IS_INITIAL_ILLUMINANCE` on `dgp`, with the DGP message.
            lit_advisories_list = lit_advisories(loaded.manifest, quantity="dgp", result_field="dgp")
        # R753 final review (D10): ruling R33's scan over the files `_render` will compile --
        # the same `_scene_files` call, geometry, materials and (when lit) placements -- above
        # `dry_run`, so a dry run refuses a scene the real call refuses. `build_octree` scans again
        # against the bytes `oconv` reads; the sky file it adds is this server's own.
        geometry_scene_files = _scene_files(
            loaded,
            luminaires=luminaires,
            no_geometry_recovery=_NO_GEOMETRY_RECOVERY,
        )
        _scan_scene_for_inline_commands(geometry_scene_files, cwd=loaded.paths.root)
        # R818/R819: what the scene holds that this render does not model -- a mirror or prism in
        # the files it compiles, and a BSDF bound to a window group. Render path only: with
        # `image=` nothing is traced. Above `dry_run`, so the preview carries them too.
        scene_warnings = [a for a in (
            light_redirecting_advisory(geometry_scene_files, tool="run_glare"),
            bsdf_not_applied_advisory(loaded, tool="run_glare"),
        ) if a is not None]

    # `LUMINAIRES_EXCLUDED` (warn, spec §7/R479, Task 13 change 4): `image is None and not luminaires`
    # means `_render` below is about to compile and light this project's own scene while leaving any
    # placement out of it -- `_render`'s own `_scene_files(..., luminaires=luminaires)` call appends
    # no placement file when `luminaires` is false. `luminaires=True` includes them, so nothing was
    # excluded and no advisory is due. When `image=` is given instead, `_render` never runs and
    # nothing this call does excludes anything, so R479 says the advisory is not emitted at all
    # rather than reported against a render that never happened. `severity="warn"` unconditionally
    # (never "info"): `run_glare` now takes a `luminaires` argument, so `field="luminaires"` always
    # names it -- the `info`/`field=None` branch is `run_annual`'s alone (R574, retired for this
    # tool). Computed once here, above `dry_run`, so the hand-off preview below and the real
    # `GlareResult` further down read the identical advisory rather than two call sites that could
    # drift.
    excluded = (
        excluded_advisory(loaded.manifest, severity="warn", tool="run_glare")
        if image is None and not luminaires else None
    )

    # `GLARE_IMAGE_PROVENANCE_UNKNOWN` (warn, Task 14, closes R459's open cost): on every `image=`
    # call, whether or not this project has a luminaire placement -- computed once here, above
    # `dry_run`, for the same reason `excluded` is: the hand-off preview below and the real
    # `GlareResult` further down must read the identical advisory rather than risk two call sites
    # that could drift.
    image_provenance = _image_provenance_advisory(luminaires) if image is not None else None

    # R719: the sky's keys are checked above `dry_run`; `_resolve_sky` runs after it (also above
    # `dry_run` since the final review's FW-2) and keeps the same check as its backstop, as R389 did
    # for the daylight-factor sky.
    sky_inputs = SkyInputs(site=None)
    sky_spec: SkySpec | None = None
    if key_advisories := sky_key_advisories(sky):
        return refuse(" ".join(a.message for a in key_advisories), key_advisories)
    # R762: resolved above `dry_run` on the render path, after the scene scan and the key check;
    # `image=` generates no sky.
    if image is None:
        sky_inputs = resolve_sky_inputs(runtime, loaded, sky)
        # Final review FW-2: the sky is resolved and its dates checked here, above `dry_run`, so a
        # dry run refuses the Perez skies the real run refuses (a site of its own and no source, both
        # measured pairs). `_resolve_sky` builds a `SkySpec` and spawns nothing; `_render` reuses it.
        sky_spec, _sky_advisories = _resolve_sky(
            quantity="luminance", sky_arg=sky,
            ground_reflectance=loaded.manifest.project.ground_reflectance,
            north=_project_north(loaded), site=sky_inputs.site,
            epw_irradiance=sky_inputs.epw_irradiance,
        )
        validate_sky_dates(sky_spec)

    if dry_run:
        # The scope is reported on the dry run too, advisory included: *"validate every argument,
        # path and this project's aperture geometry"* is what `dry_run` promises, and the escape is
        # all three. A dry run that dropped `dgp_aperture` could not tell a caller which group the
        # tool resolved theirs to.
        return emit(GlareDryRunOk(
            dgp_aperture=dgp_aperture,
            advisories=(
                [a for a in (scope_advisory, excluded, image_provenance) if a is not None]
                + lit_advisories_list + scene_warnings + sky_inputs.advisories
            ) or None,
        ))

    render_warnings: tuple[str, ...] = ()
    sky_generator_advisories: list[Advisory] = []
    if resolved_image is not None:
        image_path = resolved_image
        # **`image=` given: nothing is rendered this call, so there is no `RenderedFisheye` to read
        # a resolved preset or an octree cube off.** `quality` is still a real, validated argument
        # (refused above if not one of `GLARE_QUALITY_PRESETS`), so its ambient parameters are used;
        # `max_size` falls back to this project's own geometry bounding box's largest edge -- the
        # best honest proxy for "the compiled scene's bounding cube" available when no octree was
        # actually built this call. `_room_bounding_box` returning `None` (no geometry registered at
        # all, a legal `run_glare(image=...)` call) is the one genuinely-unavailable case ruling
        # R313 describes: `max_size` is `0.0`, which is not a guess -- `_ambient_radius` already
        # treats a zero `max_size` as "no scene to interpolate over", the same degenerate answer a
        # zero-size octree would give.
        ambient_parameters = dgp_ambient_parameters(quality)
        room_box = _room_bounding_box(loaded, env, paths.root, timeout=inline_timeout_s)
        max_size = max(room_box.size_x, room_box.size_y, room_box.size_z) if room_box is not None else 0.0
    else:
        assert view_position is not None and view_direction is not None
        # R485/R551/R568/R569: a real lit run's only project write, at the last point before the
        # *render's* first spawn (`_render`'s own sky generator, just below) and never on a dry run
        # -- this branch is only reached once `dry_run` has already returned above. Not the call's
        # first spawn overall: `discover()` and `_resolve_dgp_scope`'s `xform` (aperture read) both
        # already ran earlier in `_dispatch` (fix round 1, item 3 -- the wording used to claim
        # otherwise). Raises `PhotometryUnverifiedError`/`LitSceneRefusal`, caught by `run_glare`'s
        # own handler (Task 13 change 2).
        if luminaires:
            commit_lit_support(runtime, loaded, tool="run_glare")
        assert sky_spec is not None  # resolved above `dry_run` on this path
        image_path, render_warnings, ambient_parameters, octree_cube, sky_generator_advisories = _render(
            loaded, paths, env, sky_spec=sky_spec,
            position=(view_position[0], view_position[1], view_position[2]),
            direction=(view_direction[0], view_direction[1], view_direction[2]),
            resolution=resolution, quality=quality, luminaires=luminaires,
            inline_timeout_s=inline_timeout_s,
            roots=runtime.roots,
            scene_files=geometry_scene_files,
        )
        max_size = octree_cube[3]
        # Read purely for `AMBIENT_RADIUS_COARSE`'s comparison target (never for `max_size` -- see
        # `_room_bounding_box`), so a project with no geometry left after a render (impossible: the
        # render just proved geometry compiles) is not a case worth branching on here.
        room_box = _room_bounding_box(loaded, env, paths.root, timeout=inline_timeout_s)

    ambient_radius = _ambient_radius(ambient_parameters, max_size)

    info = read_image(env, image_path, cwd=paths.root, timeout=inline_timeout_s)

    refusals = check_glare_input(info.view, info.resolution)
    if refusals:
        return _refuse_check_glare_input(refusals)

    check_image_path: Path | None = None
    if check_image:
        check_image_path = _contain_written_path(
            paths.images_hdr / f"glare_check_{_run_discriminator()}.hdr",
            roots=runtime.roots,
            what="evalglare check image",
        )
        check_image_path.parent.mkdir(parents=True, exist_ok=True)

    eg = _evalglare_module.run_evalglare(
        env, image_path, cwd=paths.root, check_image=check_image_path,
        task_area=(
            (task_area[0], task_area[1], task_area[2]) if task_area is not None else None
        ),
        threshold_factor=threshold_factor, background_mode=background_mode,
        timeout=inline_timeout_s,
    )

    advisories = _build_advisories(
        resolution=info.resolution, notices=eg.notices, render_warnings=render_warnings,
        task_area=task_area, threshold_factor=threshold_factor, quality=quality,
    )
    if room_box is not None:
        smallest_dimension = min(room_box.size_x, room_box.size_y, room_box.size_z)
        if smallest_dimension > 0 and ambient_radius > smallest_dimension / 20:
            advisories.append(_ambient_radius_coarse_advisory(ambient_radius, smallest_dimension))
    if scope_advisory is not None:
        advisories.append(scope_advisory)
    if excluded is not None:
        advisories.append(excluded)
    if image_provenance is not None:
        advisories.append(image_provenance)
    advisories.extend(lit_advisories_list)
    advisories.extend(scene_warnings)
    advisories.extend(sky_inputs.advisories)
    advisories.extend(sky_generator_advisories)

    moment = en17037_glare_moment(eg.values.dgp)

    result = GlareResult(
        dgp=eg.values.dgp,
        dgi=eg.values.dgi,
        ugr=eg.values.ugr,
        vcp=eg.values.vcp,
        cgi=eg.values.cgi,
        lveil=eg.values.lveil,
        vertical_illuminance_lux=eg.values.vertical_illuminance_lux,
        interpretation=interpretation_band(eg.values.dgp),
        en17037={
            "level_met": moment["level_met"],
            "moment_below": moment["moment_below"],
            "thresholds": moment["thresholds"],
            "statement": moment["statement"],
        },
        image=str(image_path),
        check_image=str(eg.check_image) if eg.check_image is not None else None,
        evalglare_notices=list(eg.notices),
        ambient_interpolation_radius_m=ambient_radius,
        dgp_aperture=dgp_aperture,
        advisories=advisories or None,
    )
    return emit(result)
