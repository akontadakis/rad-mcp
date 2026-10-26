"""Judgement only: bands, levels, statements, and the four-axis EN 17037 object.

Pure: no Radiance binaries, no subprocesses, no file IO -- see CONTEXT.md, Layers. This module
does no arithmetic over matrices; it consumes the tallies and percentages `rad_core.metrics`
computes and the denominators `rad_core.periods` derives, and says what they mean against the
bands and tables the standards themselves print.

**Three things this module is structurally incapable of saying:**

1. **No `pass`/`fail` verdict, anywhere.** A band or a level-met is a classification against a
   printed threshold, not a verdict -- `sda_band`, `ase_band` and `en17037_provision` all return
   named bands or `None`, never a boolean framed as passing.
2. **`overall` is a literal `None`.** `en17037_object` builds it with no parameter, no branch and
   no code path that could set it from any of the four axes. EN 17037 states four independent
   recommendation levels with no composite score (ch03:9-11) -- a caller synthesising one from
   `daylight_provision` alone would be reporting a false pass built from one of four axes, because
   `view_out`, `sunlight_exposure` and `glare` are not assessed by this phase at all.
3. **The wording is "meets"/"does not meet the ... recommendation level", never "passes".**
   EN 17037 Annex A is informative, not normative (ch03: "**Status of values**: Clause 5 is
   normative; Annex A is informative"), and `CONTEXT.md` states the required phrasing directly:
   "Report 'meets the medium recommendation level', not 'passes'."

**EN 12464-1 and EN 17037 Method 1 are both out of scope here, deliberately.** Both are deferred
to a later phase; this module builds nothing reachable for either, because an unreachable stub is
worse than no stub at all.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Final, Literal

from rad_core.apertures import Aperture
from rad_core.lm83_edition import DEFAULT_LM83_EDITION, LM83_22, Lm83Edition

__all__ = [
    "LM83_CALIBRATION_LATITUDE_MIN",
    "LM83_CALIBRATION_LATITUDE_MAX",
    "LM83_22_CALIBRATION_LATITUDE_MIN",
    "LM83_22_CALIBRATION_LATITUDE_MAX",
    "LM83_22_ASE_CONCERN_PCT",
    "lm83_latitude_band",
    "lm83_latitude_in_calibration_band",
    "sda_band",
    "ase_band",
    "EN17037_ABOVE_PLANE_TOLERANCE_M",
    "OpeningClass",
    "en17037_opening_class",
    "en17037_opening_is_horizontal",
    "en17037_thresholds",
    "en17037_provision",
    "en17037_object",
]

#: ch04:14 -- the supporting research ran 37 N-48 N in North America.
LM83_CALIBRATION_LATITUDE_MIN: Final[float] = 37.0
LM83_CALIBRATION_LATITUDE_MAX: Final[float] = 48.0

#: R864: LM-83-22 section 1.3, in ANSI/IES LM-83-22 -- "The studied
#: spaces were located in the continental United States, and thus are most applicable to similar
#: latitudes (35 to 55 degrees)". Signed (north-positive), as LM-83-12's band is: that is this
#: server's choice, not the standard's.
LM83_22_CALIBRATION_LATITUDE_MIN: Final[float] = 35.0
LM83_22_CALIBRATION_LATITUDE_MAX: Final[float] = 55.0

#: R865: LM-83-22 section 3.2 -- "an ASE_net of 20% or higher has been determined to be a level of
#: concern". Not yet checked against the printed standard; cited from ANSI/IES
#: LM-83-22.
LM83_22_ASE_CONCERN_PCT: Final[float] = 20.0


def lm83_latitude_band(edition: Lm83Edition = DEFAULT_LM83_EDITION) -> tuple[float, float]:
    """The (low, high) edges, in signed degrees, of `edition`'s latitude band: 37 to 48 for
    LM-83-12, where its supporting research was conducted (ch04:14), and 35 to 55 for LM-83-22,
    whose section 1.3 prints the figure -- the studied spaces "are most applicable to similar
    latitudes (35 to 55 degrees)" (R864). Signed, under both editions, by this server's choice."""
    if edition == LM83_22:
        return LM83_22_CALIBRATION_LATITUDE_MIN, LM83_22_CALIBRATION_LATITUDE_MAX
    return LM83_CALIBRATION_LATITUDE_MIN, LM83_CALIBRATION_LATITUDE_MAX


def lm83_latitude_in_calibration_band(
    latitude: float, *, edition: Lm83Edition = DEFAULT_LM83_EDITION
) -> bool:
    """Whether `latitude` (degrees, north-positive) falls inside `edition`'s latitude band -- the
    37-48 N its supporting research was conducted in under LM-83-12 (ch04:14), the default, and
    under LM-83-22 the "similar latitudes (35 to 55 degrees)" its section 1.3 says the studied
    spaces "are most applicable to" (R864), signed by this server's choice. `lm83_latitude_band`
    returns the edges. **Both boundaries are inside** -- there is no source text narrowing either
    band to an open interval, and a caller outside it should attach
    `LM83_LATITUDE_OUTSIDE_CALIBRATION`: ch04's own anti-pattern list (ch04:193) names *"omitting
    the 37-48 N caveat"* explicitly, so reporting it outside the band is an obligation this
    predicate exists to make checkable in one place rather than re-derived at each call site.
    """
    low, high = lm83_latitude_band(edition)
    return low <= latitude <= high


def sda_band(value: float) -> str:
    """sDA_300/50%'s two judgement bands, both **inclusive** (ch04:34-35):

    | sDA_300/50% | Criterion |
    |---|---|
    | >= 75 % | Preferred |
    | >= 55 % | Nominally accepted |

    Anything below 55 % carries no named band in the source, so this returns `"below"` rather
    than inventing one.
    """
    if value >= 75.0:
        return "preferred"
    if value >= 55.0:
        return "nominally_accepted"
    return "below"


def ase_band(value: float, *, edition: Lm83Edition = DEFAULT_LM83_EDITION) -> str:
    """ASE's judgement bands under `edition`.

    **Under LM-83-22** (R865) there is one line: LM-83-22 section 3.2 says *"an ASE_net of 20% or
    higher has been determined to be a level of concern"*, so `value >= 20.0` returns
    `"level_of_concern"` and anything below returns `"below_level_of_concern"` -- inclusive at 20,
    and the 20 % is a value not yet checked against the printed standard. LM-83-22 prints no 3 % or 7 % band, so none
    of the LM-83-12 labels below is ever returned under it.

    **Under LM-83-12, the default**, ASE_1000,250h's three judgement bands, printed in LM-83-12
    itself (ch04:69-71):

    | ASE_1000,250h | Judgement (LM-83's own words) | returned |
    |---|---|---|
    | < 3 % | clearly acceptable | `"clearly_acceptable"` |
    | < 7 % | neutral / nominally acceptable | `"neutral"` |
    | > 10 % | unsatisfactory visual comfort | `"unsatisfactory"` |

    **The middle column is the standard's phrasing and the right one is this function's**, and the
    two are not the same string on any row: LM-83 prints a judgement, this returns an identifier
    formed from that judgement's head. Naming them side by side rather than editing the quotation
    is what stops a reader transplanting *"nominally acceptable"* -- which is a **different
    standard clause**, sDA's own `nominally_accepted` band (`sda_band`) -- onto an ASE result the
    server never labels that way.

    **The 7-10 % region is genuinely unnamed in the source.** `7.0` is not `< 7` and `10.0` is not
    `> 10` -- both fall in the gap the three printed rows leave, and the gap is closed at *both*
    ends: `7.0 <= value <= 10.0` returns `"unclassified"`. This never interpolates a threshold the
    source does not state -- these bands are LM-83's own supporting-research judgement, not a
    normative pass/fail line, so naming the gap would be inventing what LM-83 declined to.
    """
    if edition == LM83_22:
        # R865: LM-83-22 section 3.2, "an ASE_net of 20% or higher has been determined to be a
        # level of concern" -- inclusive, a value not yet checked against the printed standard. LM-83-22 drops the
        # 3 % and 7 % bands and their labels.
        return "level_of_concern" if value >= LM83_22_ASE_CONCERN_PCT else "below_level_of_concern"
    if value < 3.0:
        return "clearly_acceptable"
    if value < 7.0:
        return "neutral"
    if value > 10.0:
        return "unsatisfactory"
    return "unclassified"


#: Table A.1 -- vertical and inclined openings: (target lux, target fraction, minimum lux,
#: minimum fraction). Both conditions must hold (ch03:19-25).
_TABLE_A1: Final[dict[str, tuple[float, float, float, float]]] = {
    "minimum": (300.0, 0.50, 100.0, 0.95),
    "medium": (500.0, 0.50, 300.0, 0.95),
    "high": (750.0, 0.50, 500.0, 0.95),
}

#: Table A.2 -- horizontal openings (ch03:27-33). **No minimum-target column**, and the target
#: fraction is 95 %, not 50 %. An opening counts as horizontal when its entire area is above the
#: reference plane (ch03) -- a position test, not a tilt test, and a different rule from
#: `window_group`'s APERTURE_IS_HORIZONTAL; `en17037_opening_class` applies it.
_TABLE_A2: Final[dict[str, tuple[float, float]]] = {
    "minimum": (300.0, 0.95),
    "medium": (500.0, 0.95),
    "high": (750.0, 0.95),
}


OpeningClass = Literal["vertical", "horizontal", "mixed", "undetermined"]

#: How far above the reference plane a vertex must sit to count as above it, 1 mm. A chosen value:
#: ch03 says "above" and gives no tolerance. 1 mm is `rad_core.geometry.COINCIDENCE_TOLERANCE_M`,
#: the band within which `rad_core.grids.polygon_floor_z` calls a floor level, so a sill drawn on
#: the plane to the precision a grid's own plane is placed at counts as on it, not above it
#: (docs/tool-surface.md, Unsourced values).
EN17037_ABOVE_PLANE_TOLERANCE_M: Final[float] = 0.001


def en17037_opening_is_horizontal(aperture: Aperture, *, plane_z: float) -> bool:
    """ch03's rule: "An opening counts as horizontal if its entire area is above the reference
    plane". True when every vertex of every polygon of `aperture` is more than
    `EN17037_ABOVE_PLANE_TOLERANCE_M` above `plane_z`. A polygon's area lies within its vertices'
    z range, so this is the whole area's test. The opening's tilt does not enter it: a vertical
    clerestory wholly above the plane is horizontal, and a window whose sill is below it is not."""
    vertices = [vertex for polygon in aperture.polygons for vertex in polygon.vertices]
    return bool(vertices) and all(
        vertex[2] > plane_z + EN17037_ABOVE_PLANE_TOLERANCE_M for vertex in vertices
    )


def en17037_opening_class(apertures: Sequence[Aperture], *, plane_z: float) -> OpeningClass:
    """Which EN 17037 table judges a reference plane at `plane_z` lit through `apertures`
    (ch03, Tables A.1/A.2): "horizontal" when every aperture is wholly above the plane
    (`en17037_opening_is_horizontal`), "vertical" when none is (Table A.1, vertical and inclined
    openings), "mixed" when both occur (the standard gives no rule for combining them),
    "undetermined" when there is no aperture to test."""
    if not apertures:
        return "undetermined"
    kinds = {en17037_opening_is_horizontal(aperture, plane_z=plane_z) for aperture in apertures}
    if kinds == {True}:
        return "horizontal"
    if kinds == {False}:
        return "vertical"
    return "mixed"


def en17037_thresholds(level: str, opening_class: str) -> tuple[float, float | None]:
    """The printed lux value(s) Table A.1 (ch03:19-25) / Table A.2 (ch03:27-33) test `level` at,
    for `opening_class`: `(target_lux, minimum_lux)` for `"vertical"` (Table A.1, both conditions
    apply), `(target_lux, None)` for `"horizontal"` (Table A.2, no minimum-target column).

    `en17037_provision` below takes the two F_plane *fractions* (0-1, `rad_core.metrics.f_plane`'s
    own return unit -- never a percentage; see that function's docstring) a caller already computed
    at these lux values, not raw lux -- so this function is the one place a caller reads which lux
    value(s) `rad_core.metrics.f_plane` must be evaluated at before calling it, rather than a
    second, independent copy of Table A.1/A.2 living in whichever module builds the `PointTally`
    thresholds (the two-copies-that-must-agree shape this phase has already found and removed
    twice).

    Raises `KeyError` if `level` is not one of `"minimum"`, `"medium"`, `"high"`, and `ValueError`
    if `opening_class` is not one of `"vertical"`, `"horizontal"` -- same as `en17037_provision`.
    """
    if opening_class == "vertical":
        target_lux, _target_fraction, minimum_lux, _minimum_fraction = _TABLE_A1[level]
        return target_lux, minimum_lux
    if opening_class == "horizontal":
        target_lux, _target_fraction = _TABLE_A2[level]
        return target_lux, None
    raise ValueError(
        f"en17037_thresholds received opening_class={opening_class!r} -- must be 'vertical' "
        "(Table A.1) or 'horizontal' (Table A.2)."
    )


def en17037_provision(
    f_target: float,
    f_minimum: float | None,
    *,
    level: str,
    opening_class: str,
) -> dict:
    """Judge EN 17037 daylight provision (5.1, A.2) against one recommendation `level`.

    `f_target` and `f_minimum` are the fraction of the reference plane (0-1) that met the level's
    target and minimum illuminance for at least half the daylight hours -- `rad_core.metrics.f_plane`
    computed twice, at the target lux and at the minimum lux; this function does no matrix
    arithmetic of its own, only the comparison against the printed fraction.

    **Vertical and inclined openings (Table A.1, ch03:19-25) require both conditions**: `f_target`
    at or above the level's target fraction (50 %) *and* `f_minimum` at or above its minimum
    fraction (95 %). **Horizontal openings (Table A.2, ch03:27-33) have no minimum-target
    condition at all** -- only `f_target` at or above 95 %, and `f_minimum` is not consulted, so a
    horizontal result carries no `f_plane_at_minimum` key. Using Table A.1's 50 % target fraction
    for a horizontal opening would accept a plane that met the target over half its area instead
    of the 95 % Table A.2 actually requires. A horizontal caller passes `f_minimum=None`.

    Raises `KeyError` if `level` is not one of `"minimum"`, `"medium"`, `"high"`, and `ValueError`
    if `opening_class` is not one of `"vertical"`, `"horizontal"`, or if it is `"vertical"` and
    `f_minimum` is `None`.
    """
    if opening_class == "vertical":
        if f_minimum is None:
            raise ValueError(
                "Table A.1 needs f_minimum: the fraction of the plane at the minimum target "
                "illuminance."
            )
        target_lux, target_fraction, minimum_lux, minimum_fraction = _TABLE_A1[level]
        meets = f_target >= target_fraction and f_minimum >= minimum_fraction
        result = {
            # **Carried minor, Phase 7b Task 12 review.** The worked example in
            # docs/tool-surface.md#14-results publishes `"method": "2"` inside
            # `daylight_provision` -- this function's result dict omitted it. Only Method 2 (the
            # climate-based route, this whole module's only built path) is
            # reachable through `rad_mcp.results_tool`, so the value is the literal `"2"` rather
            # than a parameter this function would otherwise have no other use for.
            "method": "2",
            "opening_class": opening_class,
            "level": level,
            "target_lux": target_lux,
            "f_plane_at_target": f_target,
            "f_plane_at_target_required": target_fraction,
            "minimum_lux": minimum_lux,
            "f_plane_at_minimum": f_minimum,
            "f_plane_at_minimum_required": minimum_fraction,
            "meets_level": meets,
            "level_met": level if meets else None,
            "statement": (
                f"meets the {level} recommendation level"
                if meets
                else f"does not meet the {level} recommendation level"
            ),
        }
        return result
    if opening_class == "horizontal":
        target_lux, target_fraction = _TABLE_A2[level]
        meets = f_target >= target_fraction
        result = {
            "method": "2",  # see the vertical branch's comment above for why this is a literal.
            "opening_class": opening_class,
            "level": level,
            "target_lux": target_lux,
            "f_plane_at_target": f_target,
            "f_plane_at_target_required": target_fraction,
            "meets_level": meets,
            "level_met": level if meets else None,
            "statement": (
                f"meets the {level} recommendation level"
                if meets
                else f"does not meet the {level} recommendation level"
            ),
        }
        return result
    raise ValueError(
        f"en17037_provision received opening_class={opening_class!r} -- must be 'vertical' "
        "(Table A.1) or 'horizontal' (Table A.2); en17037_opening_class decides which from the "
        "apertures' position against the reference plane."
    )


def en17037_object(provision: dict) -> dict:
    """Assemble EN 17037's four independent axes (ch03:9-11): daylight provision, view out,
    sunlight exposure, glare. There is no single composite score in the standard, so `overall` is
    a literal `None` -- no parameter, no branch, no code path sets it from `provision` or from any
    axis. `view_out`, `sunlight_exposure` and `glare` are not computed by this phase; each is
    reported unassessed (`"assessed": False`) rather than silently omitted, so a caller can see
    that only one of the four axes was actually judged.
    """
    return {
        "daylight_provision": provision,
        "view_out": {"assessed": False},
        "sunlight_exposure": {"assessed": False},
        "glare": {"assessed": False},
        "overall": None,
    }
