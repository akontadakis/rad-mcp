"""An aperture's plane, normal, up-vector and coplanarity -- pure, no Radiance, no subprocess.

Task 1 of Phase 6a (docs/plan.md, Phase 6's "Outcome — 6a, 2026-08-16"). `rad_core.geometry`
reserves this seam explicitly in its own module docstring: *"Normal direction is computed for the
aperture, where it matters for Klems binning, but that is `rad_core.apertures` in a later phase and
explicitly not this module's concern."* This is that later phase.

**Inputs and outputs are the same polygon soup `rad_core.geometry` takes and produces** --
`derive` consumes one or more `rad_core.geometry.Polygon`s (already parsed from a `.rad` file by
`rad_engine`) and returns one `Aperture`: the plane those polygons share, its normal, its area, its
Klems up-vector, and its orientation as azimuth/tilt. Task 3 renders `outward_normal`/`up_vector`
into a Klems `kbin(...)` expression; Task 6 plumbs `derive` into the `window_group` tool.

**Newell's method is shared, not duplicated, with `rad_core.geometry`.** `geometry._is_degenerate`
needs only the area vector's *magnitude* (is there an interior at all); this module needs its
*direction* (which way the aperture faces) and its magnitude (the area). Both now call the same
public `rad_core.geometry.area_vector`, promoted there in this task rather than reimplemented here
or imported as a private cross-module reach -- one arithmetic path by construction rather than by
discipline (R30's own reasoning), so a future fix to Newell's method cannot silently diverge between
the two call sites.

## The convention this module fixes, once, in writing

The corpus uses "outward" two ways: `plan.md` check 6.1 and `tool-surface.md`'s verified-facts table
both say *"outward normal of +Y, i.e. into the room"* for the office aperture, while elsewhere
"outward" means away from the room (the ordinary architectural sense). This module owns both the
`kbin` input and the aperture normal, so it fixes one meaning rather than inheriting the word's two
senses. **Measured on this machine: `kbin(0,1,0,0,0,1)` bins rays travelling in -Y** -- so the
vector fed to `kbin` is the **room-facing** one, and that is what `outward_normal` means here: room
-facing, not away-from-the-room, because that is the vector Klems binning actually needs and the
name the published tool surface (`tool-surface.md`) already uses for it.

## `up_vector`: +Z projected onto the aperture's own plane

The Klems basis needs an up direction **perpendicular to the normal** to complete its local frame,
which is what `kbin(...)`'s last three arguments are, and which `rad_engine.klems.
_orthonormal_tangent` states it *relies* on: it takes a plain cross product rather than a
Gram-Schmidt correction, then normalises. An `up` that is not perpendicular does not merely tilt the
frame -- it shortens that cross product before normalising, and the Klems basis comes out rotated
against the `.cal` file with no error anywhere.

So `up_vector` is +Z with its component along the normal removed: the steepest-ascent direction
within the aperture's own plane, perpendicular by construction at **every** tilt. Only an aperture
flat to within `_PARALLEL_TO_Z_EPS`, where that projection vanishes, falls through to a constant --
and that constant is **+Y**, sourced rather than chosen: `klems_full.cal:54` defines Radiance's own
skylight alias as `kbinD = kbin(0,0,-1, 0,1,0)`, whose last three arguments are the up-vector
(measured on this machine 2026-08-16).

**This replaced a pair of constants that was correct at exactly two orientations** (Phase 6b Task 0).
The original note here described a `+Z`-everywhere rule with a `+X` fallback inside a 5 degree band,
and recorded only that the `+X` disagreed with `kbinD`. Measuring it found the wider defect --
`dot(up, normal)`, which must be 0:

    tilt      0.0    2.0     4.9     10.0     45.0     89.0    90.0
    dot     0.000  -0.035  -0.085   0.985    0.707    0.017   0.000

Only exactly-flat and exactly-vertical were right, and the worst case was a **10 degree rooflight at
0.985 -- 80 degrees from perpendicular**, not the horizontal band. It reached the argv: a 45 degree
rooflight produced ``kbin(-0.707107,0,0.707107, 0,0,1)``. Nothing caught it because every asserted
aperture is vertical (where +Z happens to be right) or flat (where perpendicularity holds but the
*axis* was wrong, and no test asserted the axis) -- the shape ruling R68 records for `azimuth_deg`.

**`_PARALLEL_TO_Z_EPS` is deliberately not `_HORIZONTAL_TILT_DEG`.** They used to be the same number
and that is what forced the error. The 5 degree band answers a *reporting* question (which EN 17037
table applies, whether DGP is defined, `is_horizontal`); this epsilon answers only whether +Z still
has a projection to take. A 3 degree rooflight is horizontal for EN 17037 *and* has a well-defined
projected up-vector, and tying the two made the second answer pay for the first.

`is_tilt_verified` is the separate, honest statement that the orientations between flat and vertical
are arithmetically fine and **untested** -- see that function.

## `azimuth_spread_deg` returns a number and does not decide

The ±7.5 degree grouping rule (`WINDOW_GROUP_AZIMUTH_SPREAD`, `warn`) is the caller's job (Task 6,
the `window_group` tool) -- this module only measures the spread. LM-83 defines a window group as
*coplanar* windows, which is why non-coplanar members are refused outright (`error`,
`APERTURE_NOT_COPLANAR`) rather than warned about: a single Klems basis over non-coplanar surfaces
is not meaningful, unlike an azimuth spread, which is a judgement call about how much orientation
variance one basis should absorb.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

from rad_core.geometry import Point3, Polygon, area_vector

__all__ = [
    "OBSTRUCTION_SPREAD_THRESHOLD",
    "Aperture",
    "ApertureNormalAmbiguousError",
    "ApertureNotCoplanarError",
    "ShadingLadderExhaustedError",
    "ShadingSubstitution",
    "azimuth_spread_deg",
    "derive",
    "grid_behind_aperture",
    "is_horizontal",
    "is_tilt_verified",
    "obstruction_outliers",
    "obstruction_spread",
    "substitute_shading",
    "ApertureError",
]

#: `is_horizontal`'s own threshold (brief step 6): tilt under 5 degrees from horizontal. Carried
#: forward because EN 17037 uses Table A.2 (95 % of plane, no minimum-target column) for horizontal
#: openings and DGP does not apply to them (docs/tool-surface.md's `APERTURE_IS_HORIZONTAL` row).
_HORIZONTAL_TILT_DEG = 5.0

#: How far from 90 degrees an aperture's tilt may sit and still count as "vertical" for
#: `is_tilt_verified`. Mirrors `_HORIZONTAL_TILT_DEG`'s 5 degrees deliberately: the two bracket the
#: *asserted* orientations from opposite ends, and a real facade drawn to six decimal places
#: (`geometry._QUANTIZE_DECIMALS`) sits within float noise of 90, so this is a band for modelling
#: slop rather than a claim that 85 degrees is vertical. Unsourced -- a judgement call, stated
#: plainly in the same spirit as `_COPLANAR_ANGLE_TOLERANCE_DEG`.
_VERTICAL_TILT_TOLERANCE_DEG = 5.0

#: Squared-magnitude floor on a member's own Newell area vector below which it contributes no
#: consistent normal to derive from -- the same numeric shape as `rad_core.geometry`'s
#: `_DEGENERATE_AREA_EPS`, applied here rather than imported: this module's degeneracy question is
#: "does this soup have a normal to report", not geometry.py's "does this polygon have an
#: interior", and the two happen to share a threshold rather than a definition.
_ZERO_AREA_EPS = 1e-9

#: Coplanarity tolerance: how far (in metres, along the shared normal) a member's centroid may sit
#: from the reference plane through the first member's centroid before the soup is refused as not
#: coplanar. Not sourced from a spec -- a judgement call in the same spirit as
#: `rad_core.geometry._QUANTIZE_DECIMALS`: well below any real construction tolerance (a millimetre)
#: while comfortably above float noise from upstream unit conversion.
_COPLANAR_TOLERANCE_M = 1e-6

#: How far from parallel or antiparallel a member's own normal may sit, in degrees, before the
#: group is refused as not coplanar. Not sourced from a spec -- an unsourced judgement call, stated
#: plainly rather than hidden (this repo's own convention: see e.g. `geometry._MIN_EDGE_LENGTH_M`'s
#: docstring for the same posture). Deliberately tight: two coplanar `.rad` polygons drawn to six
#: decimal places (`geometry._QUANTIZE_DECIMALS`'s own observation about this codebase's fixtures)
#: disagree in direction by float noise only, so a member genuinely 1 degree off is already a
#: modelling error this check exists to catch, not a tolerance band to accommodate.
_COPLANAR_ANGLE_TOLERANCE_DEG = 0.5


class ApertureError(ValueError):
    """Something this module refuses to derive.

    Mirrors `rad_engine.project.ProjectError`'s shape (class-attribute `code`, `ValueError` base) --
    see that class and `rad_core.geometry.GeometryError`'s docstring for why an exception, not an
    advisory return value, is the right shape for an `error`-severity refusal at this layer.
    """

    code: str | None = None
    severity: str = "error"


class ApertureNotCoplanarError(ApertureError):
    """The polygons handed to `derive` do not share one plane.

    LM-83 defines a window group as **coplanar** windows (docs/tool-surface.md's
    `APERTURE_NOT_COPLANAR` row) -- a single Klems basis over non-coplanar members is not
    meaningful, which is why this is a refusal and not a warning.
    """

    code = "APERTURE_NOT_COPLANAR"


class ApertureNormalAmbiguousError(ApertureError):
    """The polygon soup has no consistent normal to report.

    Raised when the combined Newell area vector across every member is (numerically) zero --
    either because `derive` was given no polygons at all, or because every vertex loop given is
    degenerate (collinear or coincident vertices, no interior). docs/tool-surface.md's
    `APERTURE_NORMAL_AMBIGUOUS` row: *"Polygon winding gives no consistent normal."*
    """

    code = "APERTURE_NORMAL_AMBIGUOUS"


class ShadingLadderExhaustedError(ApertureError):
    """No LM-83 row matches the device and its parameters.

    Raised by `substitute_shading` when a device (e.g. "electrochromic_glazing") has no entry
    in the LM-83-12 §2.2.7 fallback ladder. A device matching no sourced row is refused rather
    than approximated -- inventing an eighth row is exactly how an unsourced number enters the
    codebase.
    """

    code = None  # No advisory code is allocated for this refusal; Task 7's tool layer handles it


@dataclass(frozen=True)
class Aperture:
    """One coplanar aperture, derived from one or more `rad_core.geometry.Polygon` members.

    `outward_normal` is unit length and **room-facing** -- see the module docstring's convention
    note; it is the vector `kbin(...)` (Task 3) takes directly. `up_vector` is unit length and is
    the Klems up-vector: global +Z for a non-horizontal aperture, global +X for a horizontal one
    (see the module docstring for why +Z cannot be used there). `area_m2` sums every member's own
    Newell-derived area. `azimuth_deg` is degrees east of north in `[0, 360)` (+Y = north/0 deg,
    +X = east/90 deg), matching `rad_engine.bsdf`'s convention -- and it is taken from the
    **exterior**-facing normal, `-outward_normal`, because an azimuth names which way the *facade*
    looks. **The two senses of "outward" do not agree and must not be conflated**: `outward_normal`
    points into the room (what `kbin` needs), `azimuth_deg` points out of the building (what a
    facade orientation means). Deriving the second from the first inverts it by 180 degrees, which
    is the defect the Phase 6a whole-branch review found here -- see `derive`'s own comment.
    `tilt_deg` is the angle from horizontal: 0 for a flat skylight, 90 for a vertical window.
    `centroid` is the unweighted mean of every vertex of every member.
    """

    polygons: tuple[Polygon, ...]
    outward_normal: Point3
    up_vector: Point3
    area_m2: float
    azimuth_deg: float
    tilt_deg: float
    centroid: Point3


@dataclass(frozen=True)
class ShadingSubstitution:
    """A fallback BSDF's transmittance components for a shading device with no measured data.

    `specular_transmittance` is the transmission of direct light (None for louvres, 0 for
    fabric-shade-with-VLT-only). `diffuse_transmittance` is the transmission of diffuse light.
    `clause` is the quoted text of the LM-83 row used for matching and audit. `provenance` is
    always the string "default" -- a substituted value standing in for a real BSDF is never
    reported as a measured value (see CONTEXT.md).
    """

    specular_transmittance: float | None
    diffuse_transmittance: float
    clause: str
    provenance: str


def _normalise(vector: Point3) -> Point3:
    """`vector` scaled to unit length. Callers only ever pass a vector already checked non-zero."""
    length = math.sqrt(sum(c * c for c in vector))
    return (vector[0] / length, vector[1] / length, vector[2] / length)


def _dot(a: Point3, b: Point3) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


#: How near-parallel to +Z a normal must be before +Z has no projection onto the aperture plane to
#: speak of. True numerical parallelism, not a band: at `1 - 1e-9` the projection's magnitude is
#: ~4.5e-5, still far above float noise, and the fallback below is reached only by an aperture that
#: is flat to within ~0.0026 degrees. **Deliberately not `_HORIZONTAL_TILT_DEG`** -- see `_up_vector`.
_PARALLEL_TO_Z_EPS: Final = 1e-9

#: The up-vector for an aperture so flat that +Z has no in-plane direction to project onto.
#: **Sourced, not chosen**: Radiance's own skylight alias fixes it. Measured on this machine
#: 2026-08-16, `/usr/local/radiance/lib/klems_full.cal:54`::
#:
#:     kbinD = kbin(0,0,-1, 0,1,0);	{ Skylight bins }
#:
#: The last three arguments are the up-vector: +Y. A flat rooflight given any other axis gets a
#: Klems frame rotated in azimuth against the `.cal` file it is binning into, which rotates the
#: basis against whatever BSDF is bound to it.
_FLAT_APERTURE_UP: Final[Point3] = (0.0, 1.0, 0.0)


def _up_vector(unit_normal: Point3) -> Point3:
    """The Klems up-vector for an aperture whose (room-facing) unit normal is `unit_normal`.

    **+Z projected onto the aperture's own plane**, which is perpendicular to `unit_normal` by
    construction, at every tilt. Only an aperture flat to within `_PARALLEL_TO_Z_EPS` -- where that
    projection vanishes -- falls through to the sourced `_FLAT_APERTURE_UP`.

    ## Why a projection and not a constant (Phase 6b Task 0)

    `rad_engine.klems._orthonormal_tangent` states that it *relies* on `up` being perpendicular to
    `normal`: it takes a plain cross product "rather than a Gram-Schmidt correction", and normalises
    the result. An `up` that is not perpendicular therefore does not merely tilt the frame, it
    shortens the cross product before normalising, and the Klems basis comes out rotated against the
    `.cal` file with no error anywhere.

    This function returned a bare `+Z` for every non-horizontal aperture and a bare `+X` inside a
    5 degree band, and **both were only correct at the two extremes**. Measured on this machine
    2026-08-16, `dot(up, normal)` -- which must be 0 -- across tilt from horizontal:

        tilt      0.0    2.0     4.9     10.0     45.0     89.0    90.0
        dot     0.000  -0.035  -0.085   0.985    0.707    0.017   0.000

    Only exactly-flat and exactly-vertical were right. **The worst case was a 10 degree rooflight at
    0.985 -- 80 degrees from perpendicular** -- not the horizontal band the original note recorded.
    It reached the argv: a 45 degree rooflight produced
    ``kbin(-0.707107,0,0.707107, 0,0,1)``, a normal and an "up" 45 degrees apart. Nothing caught it
    because every asserted aperture in the suite is vertical (where +Z happens to be right) or flat
    (where perpendicularity holds but the *axis* was wrong, and no test asserted the axis) -- the
    same shape as the `azimuth_deg` defect ruling R68 records, internally consistent and never
    spanned by an assertion.

    ## `_PARALLEL_TO_Z_EPS` is not `_HORIZONTAL_TILT_DEG`, and they answer different questions

    They used to be the same number and that is what forced the error. `_HORIZONTAL_TILT_DEG` (5
    degrees) decides `is_horizontal`, which is a **reporting** question -- which EN 17037 table
    applies, and whether DGP is defined. This threshold decides only whether +Z still has a
    projection to take, which is a question about arithmetic. A 3 degree rooflight is horizontal for
    EN 17037 *and* has a perfectly good projected up-vector; tying the two made the second answer
    pay for the first.
    """
    z_axis = (0.0, 0.0, 1.0)
    alignment = _dot(unit_normal, z_axis)
    if abs(alignment) >= 1.0 - _PARALLEL_TO_Z_EPS:
        return _FLAT_APERTURE_UP
    # +Z with its component along the normal removed: the steepest-ascent direction in the
    # aperture's own plane, and exactly perpendicular to `unit_normal` because that is what
    # subtracting the projection means. `unit_normal` is a unit vector, so no division is needed.
    projected = (
        z_axis[0] - alignment * unit_normal[0],
        z_axis[1] - alignment * unit_normal[1],
        z_axis[2] - alignment * unit_normal[2],
    )
    return _normalise(projected)


def derive(polygons: Sequence[Polygon]) -> Aperture:
    """Derive one `Aperture` from `polygons`: the room-facing normal, plane, up-vector, area,
    azimuth and tilt they share.

    Raises `ApertureNormalAmbiguousError` when `polygons` is empty or every member is degenerate
    (no consistent normal to report), and `ApertureNotCoplanarError` when the members do not lie in
    one plane. See the module docstring for both the outward-normal convention and the up-vector
    fallback.
    """
    combined = (0.0, 0.0, 0.0)
    member_vectors: list[tuple[Polygon, Point3]] = []
    for polygon in polygons:
        vector = area_vector(polygon.vertices)
        member_vectors.append((polygon, vector))
        combined = (
            combined[0] + vector[0],
            combined[1] + vector[1],
            combined[2] + vector[2],
        )

    if _dot(combined, combined) < _ZERO_AREA_EPS:
        raise ApertureNormalAmbiguousError(
            "no consistent normal: the combined Newell area vector across "
            f"{len(polygons)} polygon(s) is (numerically) zero. Either no polygons were given, or "
            "every member's vertex loop is degenerate (collinear or coincident vertices, no "
            "interior)."
        )

    outward_normal = _normalise(combined)

    _check_coplanar(polygons, member_vectors, outward_normal)

    area_m2 = sum(math.sqrt(_dot(vector, vector)) for _, vector in member_vectors)

    # **From the EXTERIOR normal, not `outward_normal`.** The two senses of "outward" are the trap
    # this module set out to disambiguate, and this expression had them crossed. `outward_normal` is
    # room-facing -- the vector `kbin` takes, fixed by this module's own convention -- while azimuth
    # "degrees east of north" describes the *facade*, which looks the other way. Taking it from the
    # room-facing vector inverted every reported azimuth by 180 degrees: the office fixture's
    # south-facing window read as north (0.0), while five docstrings on this branch and
    # `man genklemsamp`'s own "0 -1 0 for a south-facing window" all call it south.
    #
    # It is a published field, and it is published *precisely* to catch this class of error --
    # tool-surface.md SS6: "azimuth_deg and tilt_deg are reported so the user can check the geometry
    # matches their intent - a wrong normal is the one error here that produces a plausible number."
    # It also reaches `rad_engine.bsdf.build_xform_argv`, where the inversion selected the north row
    # of genblinds' sourced rotation table for a south facade: a well-formed BSDF of slats turned
    # 180 degrees from the device being modelled, which CONTEXT.md names as failing with no error
    # and no warning. `tests/test_apertures.py::TestAzimuthIsExteriorFacing` asserts both halves,
    # the second across both modules, since each was internally consistent on its own.
    exterior_normal = (-outward_normal[0], -outward_normal[1])
    azimuth_deg = math.degrees(math.atan2(exterior_normal[0], exterior_normal[1])) % 360.0
    # tilt_deg is the angle *between the normal and vertical (+Z)*, per the brief: 0 for a
    # horizontal aperture (normal parallel to +Z) and 90 for a vertical one (normal perpendicular
    # to +Z, i.e. horizontal itself). acos is clamped into [-1, 1] against float residue from
    # _normalise's division.
    cos_from_vertical = max(-1.0, min(1.0, abs(outward_normal[2])))
    tilt_deg = math.degrees(math.acos(cos_from_vertical))

    up_vector = _up_vector(outward_normal)

    all_vertices = [v for polygon in polygons for v in polygon.vertices]
    n = len(all_vertices)
    centroid = (
        sum(v[0] for v in all_vertices) / n,
        sum(v[1] for v in all_vertices) / n,
        sum(v[2] for v in all_vertices) / n,
    )

    return Aperture(
        polygons=tuple(polygons),
        outward_normal=outward_normal,
        up_vector=up_vector,
        area_m2=area_m2,
        azimuth_deg=azimuth_deg,
        tilt_deg=tilt_deg,
        centroid=centroid,
    )


def _check_coplanar(
    polygons: Sequence[Polygon],
    member_vectors: Sequence[tuple[Polygon, Point3]],
    outward_normal: Point3,
) -> None:
    """Refuse `polygons` unless every member's own plane matches the combined one.

    Two independent tests per member, either of which is sufficient to refuse:

    1. **Direction.** Its own normal must be genuinely **parallel or antiparallel** to the combined
       normal, within `_COPLANAR_ANGLE_TOLERANCE_DEG` -- not merely "in the same half-space"
       (`dot > 0`), which an obliquely- or perpendicularly-facing member can still satisfy. Fix
       round 1's review caught exactly this: two polygons in orthogonal planes sharing one edge
       point averaged to a combined normal that belonged to neither, and `dot(member, combined) <=
       0.0` refused nothing because the perpendicular member's dot product was exactly (not
       negative) zero -- the boundary case `<= 0` was never designed to catch a genuinely oblique
       member with `dot > 0`. **Antiparallel is accepted, not refused**: Radiance polygons are
       two-sided (CONTEXT.md) and winding direction is not a defect, so a member drawn the other way
       round on the *same* plane must pass this check, which is why the test is on the angle's
       distance from 0 *or* 180 degrees, not on the sign of the dot product alone.
    2. **Offset.** Its centroid must lie on the plane the first member's centroid defines (not
       merely parallel but offset, e.g. two parallel walls a metre apart).

    A single test would miss one of the two failure shapes: parallel-but-offset planes pass a
    direction-only check, and (before this fix) a member facing a different direction could still
    happen to have its centroid land on the reference plane by coincidence.
    """
    if len(polygons) < 2:
        return

    reference_centroid = _polygon_centroid(polygons[0])
    cos_tolerance = math.cos(math.radians(_COPLANAR_ANGLE_TOLERANCE_DEG))

    for polygon, vector in member_vectors:
        magnitude = math.sqrt(_dot(vector, vector))
        if magnitude < math.sqrt(_ZERO_AREA_EPS):
            continue  # a degenerate member contributes no plane to disagree with
        member_normal = _normalise(vector)
        # abs(): parallel (dot ~= +1) and antiparallel (dot ~= -1) both pass -- see the docstring
        # on why winding direction must not be refused.
        if abs(_dot(member_normal, outward_normal)) < cos_tolerance:
            raise ApertureNotCoplanarError(
                f"polygon {polygon.id!r} does not share the group's plane: its own normal is more "
                f"than {_COPLANAR_ANGLE_TOLERANCE_DEG:g} degrees from parallel (or antiparallel) to "
                "the combined normal, so this is not one coplanar aperture (LM-83 defines a window "
                "group as coplanar windows)."
            )
        centroid = _polygon_centroid(polygon)
        offset = tuple(c - r for c, r in zip(centroid, reference_centroid))
        distance_from_plane = abs(_dot(offset, outward_normal))
        if distance_from_plane > _COPLANAR_TOLERANCE_M:
            raise ApertureNotCoplanarError(
                f"polygon {polygon.id!r} is {distance_from_plane:g} m off the plane through "
                f"the centroid of {polygons[0].id!r} -- not coplanar with the rest of the group (LM-83 "
                "defines a window group as coplanar windows)."
            )


def _polygon_centroid(polygon: Polygon) -> Point3:
    n = len(polygon.vertices)
    return (
        sum(v[0] for v in polygon.vertices) / n,
        sum(v[1] for v in polygon.vertices) / n,
        sum(v[2] for v in polygon.vertices) / n,
    )


def grid_behind_aperture(aperture: Aperture, points: Sequence[Point3]) -> bool:
    """Whether every point lies on the far side of `aperture`'s plane from the room its room-facing
    `outward_normal` names: `dot(p - centroid, outward_normal) <= 0` for each point (R846).

    For a convex room every interior point is in front of a correctly wound pane, so `True` over a
    grid in that room is the geometry of a pane wound out of the room. A pane in another room of a
    convex building also has the grid in front of its plane, so it gives `False`. `False` for no
    points: nothing was tested.
    """
    if not points:
        return False
    return all(
        _dot((x - aperture.centroid[0], y - aperture.centroid[1], z - aperture.centroid[2]),
             aperture.outward_normal) <= 0.0
        for x, y, z in points
    )


def is_horizontal(aperture: Aperture) -> bool:
    """Whether `aperture`'s tilt is less than `_HORIZONTAL_TILT_DEG` (5 degrees) -- strictly: a
    tilt of exactly 5 is not horizontal.

    A plain predicate -- the caller decides what to do with it (`APERTURE_IS_HORIZONTAL` is `info`,
    docs/tool-surface.md), exactly as `azimuth_spread_deg` returns a number rather than raising.
    """
    return aperture.tilt_deg < _HORIZONTAL_TILT_DEG


def is_tilt_verified(aperture: Aperture) -> bool:
    """Whether `aperture`'s orientation is one the test suite actually asserts end to end.

    **A statement about coverage, not about geometry** (Phase 6b Task 0, owner decision 2026-08-16).
    Since `_up_vector` projects +Z onto the aperture plane, the Klems frame is correct by
    construction at every tilt -- there is no orientation this module computes wrongly. What varies
    is whether anything has *checked* it against a real BSDF, and only two orientations have:

    - **vertical** (within `_VERTICAL_TILT_TOLERANCE_DEG` of 90 degrees from horizontal) -- the
      office fixture, asserted by checks 6.1 and 6.3 through 6.6;
    - **flat** (within `_HORIZONTAL_TILT_DEG` of horizontal) -- asserted against `klems_full.cal`'s
      own `kbinD` alias by `tests/test_klems.py`'s cross-module seam test.

    Everything between is arithmetically fine and untested, which is what `APERTURE_TILT_UNVERIFIED`
    reports at `warn`. A predicate rather than a refusal because refusing would make correct geometry
    unusable -- a sawtooth rooflight is an ordinary building, not a modelling error -- while saying
    nothing would let an unasserted path look exactly as settled as the office window. The `warn`
    disappears the day a tilted fixture exists, and that is the intended way to retire it.

    Returns a plain `bool` and decides nothing, the same contract `is_horizontal` holds.
    """
    if is_horizontal(aperture):
        return True
    return abs(aperture.tilt_deg - 90.0) <= _VERTICAL_TILT_TOLERANCE_DEG


def azimuth_spread_deg(apertures: Sequence[Aperture]) -> float:
    """The largest pairwise azimuth difference across `apertures`, in degrees, wrapped correctly
    across the 0/360 seam (so two facades either side of due north read as close, not 350 apart).

    Returns 0.0 for zero or one aperture. Returns the number only -- the caller (`window_group`,
    Task 6) raises `WINDOW_GROUP_AZIMUTH_SPREAD` at `warn` above 7.5 degrees; this module does not
    decide (see the module docstring).
    """
    if len(apertures) < 2:
        return 0.0
    spreads = [
        _angular_difference_deg(a.azimuth_deg, b.azimuth_deg)
        for i, a in enumerate(apertures)
        for b in apertures[i + 1 :]
    ]
    return max(spreads)


def _angular_difference_deg(a_deg: float, b_deg: float) -> float:
    """The smaller of the two arcs between two azimuths, in `[0, 180]`."""
    diff = abs(a_deg - b_deg) % 360.0
    return diff if diff <= 180.0 else 360.0 - diff


#: How far apart the most- and least-visible members of a coplanar, co-azimuthal window group's own
#: sampled sky-visibility fractions (`rad_engine.klems.sample_sky_visibility`'s return value) may sit
#: before `WINDOW_GROUP_OBSTRUCTION_VARIES` (warn, Task 6's tool layer -- this module never raises
#: it, see `obstruction_spread`'s own docstring) is warranted.
#:
#: **Unsourced, stated plainly rather than hidden.** No standard in this project's corpus gives a
#: number for "materially different amounts of sky" between coplanar members of one window group --
#: `docs/specs/three-phase-method.md`'s appendix (`WINDOW_GROUP_OBSTRUCTION_VARIES`'s own citation,
#: docs/tool-surface.md) says *that* a significant local obstruction should split the group, never
#: *how much* spread counts as significant. `0.2` is a stated conservative default in the same spirit
#: as `_COPLANAR_ANGLE_TOLERANCE_DEG` above: a member seeing 20 percentage points less sky than
#: another is a large, qualitatively different obstruction (an overhang or a neighbouring building),
#: not sampling noise -- `rtrace`/`rcontrib` are Monte Carlo (see
#: `rad_engine.klems.sample_sky_visibility`'s own docstring for the measured run-to-run spread on
#: this machine), so the threshold must sit well clear of that noise floor or it would fire on two
#: identically-unobstructed members some fraction of the time.
#:
#: **This value reaches the tool's published output** (it gates a warn-severity advisory a client
#: reads), which is the same posture `docs/tool-surface.md`'s "Unsourced values" section already
#: holds every other number in that category to -- unlike a refusal-boundary constant such as
#: `_MIN_EDGE_LENGTH_M` that never flows into a result, this one is recorded there rather than only
#: in this docstring.
OBSTRUCTION_SPREAD_THRESHOLD: Final[float] = 0.2


def obstruction_spread(fractions: Sequence[float]) -> float:
    """The range (max minus min) across `fractions` -- the sky-visibility fraction
    `rad_engine.klems.sample_sky_visibility` measured for each member of a window group, in the same
    order.

    Returns `0.0` for zero or one member, matching `azimuth_spread_deg`'s own convention: a single
    member has nothing to spread against. Range, not variance or a pairwise-max angular wrap
    (`azimuth_spread_deg`'s own statistic) -- sky visibility has no wraparound seam the way an
    azimuth does, so the simplest statistic that answers "how far apart are the most- and
    least-visible members" is the one used.

    Returns a number only and does not decide anything -- `WINDOW_GROUP_OBSTRUCTION_VARIES` (warn,
    docs/tool-surface.md) is raised by the caller (`window_group`, Task 6) when this exceeds
    `OBSTRUCTION_SPREAD_THRESHOLD`, exactly the split `azimuth_spread_deg`'s own docstring states for
    `WINDOW_GROUP_AZIMUTH_SPREAD` and the module docstring's own R30/R34 reasoning: `rad_engine`
    casts the rays, `rad_core` judges the numbers, and only the tool layer decides to warn.
    """
    if len(fractions) < 2:
        return 0.0
    return max(fractions) - min(fractions)


def obstruction_outliers(fractions: Sequence[float]) -> tuple[int, ...]:
    """Indices into `fractions` whose sky-visibility fraction sits far from the group's own median --
    the members `WINDOW_GROUP_OBSTRUCTION_VARIES` (Task 6) names as the ones to consider splitting
    out, once the caller has decided `obstruction_spread(fractions) >= OBSTRUCTION_SPREAD_THRESHOLD`
    is worth warning about (this function does not check the threshold itself and returns indices
    regardless of whether the spread is material -- the caller applies that gate, same split as
    `obstruction_spread`).

    **Median, not mean.** A single heavily obstructed member among several unobstructed ones drags
    the mean toward itself, which would understate its own distance from "the rest of the group" and
    could even flag an unobstructed member as the outlier instead. The median of an odd-sized group
    sits at an actual member's value and is not moved by one extreme; distance from it is what
    separates "the obstructed member(s)" from "the group" the way a human reading the sampled
    fractions would.

    A member is an outlier when its distance from the median is at least half of
    `OBSTRUCTION_SPREAD_THRESHOLD` -- half, because the threshold is defined as the *spread* (max
    minus min, `obstruction_spread`) across the whole group, and a single outlier sitting that far
    below a tight cluster of the rest displaces the group's own range by roughly that much on its
    own. Returns `()` for zero or one member (nothing to compare against, matching
    `obstruction_spread`'s own convention) and indices in ascending order otherwise.
    """
    if len(fractions) < 2:
        return ()
    ordered = sorted(fractions)
    mid = len(ordered) // 2
    if len(ordered) % 2 == 1:
        median = ordered[mid]
    else:
        median = (ordered[mid - 1] + ordered[mid]) / 2.0
    half_threshold = OBSTRUCTION_SPREAD_THRESHOLD / 2.0
    return tuple(
        index
        for index, fraction in enumerate(fractions)
        if abs(fraction - median) >= half_threshold
    )


def substitute_shading(
    *, device: str, vlt: float | None = None, openness: float | None = None,
    reflectance: float | None = None,
) -> ShadingSubstitution:
    """Return a fallback BSDF for a shading device, matching LM-83-12 §2.2.7's seven-row ladder.

    `device` is a key like "fabric_shade" or "louvre"; optional numeric parameters refine the
    match within that device's rows. The ladder has a bottom: when no row matches the device and
    parameters, `ShadingLadderExhaustedError` is raised rather than returning a value. Inventing
    an eighth row is exactly how an unsourced number enters the codebase (brief, step 6).

    **Fabric shade rows** express results as absolute values (VLT, VLT−openness) or as a fixed
    absolute default (5%). **Louvre rows are read here as fractions of the device's VLT**: "20% VLT"
    is taken as 20% *of* the device's VLT, not an absolute 20%. That reading is this function's
    own, and neither text establishes it: row 22 of `docs/tool-surface.md`'s "LM-83-22: where this
    server's LM-83 differs" finds that LM-83-12 §2.2.7 and ANSI/IES
    LM-83-22 (§4.7, Table 4-1) both give the percentage as the blind's own diffuse VLT, not a
    fraction of some other VLT, and `docs/plan.md`'s Still open item 50 records the question. The
    function is unwired (R835), so no published number rests on it. Consequence of the reading: a
    louvre without a supplied VLT cannot match any row and raises `ShadingLadderExhaustedError`.
    *(Corrected 2026-10-12, Phase 25 Branch B final review, R863: it said "This distinction
    appears in the source: the "Shade VLT unknown" row reads "0 | 5%" unambiguously as absolute
    (cannot be a fraction of unknown VLT), while louvre rows write "20 % VLT" rather than bare
    "20%" precisely because they are fractional".)*

    **Reflectance banding for louvres:** The source gives four point values (reflectances for
    white, medium-light, medium-dark, black louvres). We snap continuous reflectance to the
    nearest sourced row by banding: [0, 0.4), [0.4, 0.6), [0.6, 0.8), [0.8, 1]. These band
    boundaries are a design choice, not cited in the source, so that a future reader cannot
    mistake an edge (0.4, 0.6, 0.8) for a point value the source gives.

    **The "lower limit" row:** LM-83 calls the fourth louvre row (reflectance → 10% VLT) a
    "lower limit" for louvre transmittance. We interpret this as: the result always floors at
    10% VLT, so `max(0.10 * vlt, band_result)` where band_result is already one of [0.20, 0.175,
    0.15, 0.10] times VLT. Since 0.10 is the smallest band value, this floors naturally without
    special logic.

    One fabric-shade row (VLT-only, no openness) always returns specular_transmittance=0.
    When openness is provided, specular_transmittance=openness and diffuse_transmittance=vlt-openness
    (splitting the VLT). The fallback for unknown VLT is 0.05 diffuse, sourced from
    LM-83-12's device-level default.
    """

    # The seven rows from LM-83-12 §2.2.7, sourced in docs/tool-surface.md, Task 7.
    # Rows: fabric shade (VLT+openness, VLT only, VLT unknown), then louvres (white→black).

    if device == "fabric_shade":
        # Fabric shade with VLT and openness: split the VLT
        if vlt is not None and openness is not None:
            return ShadingSubstitution(
                specular_transmittance=openness,
                diffuse_transmittance=vlt - openness,
                clause='"Fabric shade: VLT known and openness known, split VLT equally"',
                provenance="default",
            )
        # Fabric shade with VLT only (no openness): all diffuse
        elif vlt is not None:
            return ShadingSubstitution(
                specular_transmittance=0,
                diffuse_transmittance=vlt,
                clause='"Fabric shade: VLT known, openness unknown; transmit entirely as diffuse"',
                provenance="default",
            )
        # Fabric shade with neither VLT nor openness: use default 5%
        else:
            return ShadingSubstitution(
                specular_transmittance=None,
                diffuse_transmittance=0.05,
                clause='"Fabric shade: VLT unknown; use default diffuse transmittance"',
                provenance="default",
            )

    elif device == "louvre":
        # Louvre matching by reflectance and VLT. The four rows are band-matched by reflectance
        # and scaled by VLT (not absolute values).
        # Reflectance banding: [0, 0.4), [0.4, 0.6), [0.6, 0.8), [0.8, 1].
        # Louvre without VLT has no sourced row and is refused.
        if vlt is None:
            raise ShadingLadderExhaustedError(
                "louvre device requires vlt parameter to produce a transmittance fraction"
            )

        if reflectance is not None:
            # Select the band and compute the fractional result
            if reflectance >= 0.8:
                diffuse_t = 0.20 * vlt
                clause = '"Louvre: reflectance ≥ 80% (white); diffuse transmit 20% of VLT"'
            elif reflectance >= 0.6:
                diffuse_t = 0.175 * vlt
                clause = '"Louvre: reflectance 60–80%; diffuse transmit 17.5% of VLT"'
            elif reflectance >= 0.4:
                diffuse_t = 0.15 * vlt
                clause = '"Louvre: reflectance 40–60%; diffuse transmit 15% of VLT"'
            else:
                diffuse_t = 0.10 * vlt
                clause = '"Louvre: reflectance < 40% (black); diffuse transmit 10% of VLT (lower limit)"'

            return ShadingSubstitution(
                specular_transmittance=None,
                diffuse_transmittance=diffuse_t,
                clause=clause,
                provenance="default",
            )
        # Louvre with VLT but no reflectance: no sourced row matches
        else:
            raise ShadingLadderExhaustedError(
                "louvre device requires reflectance parameter to match an LM-83-12 row"
            )

    # Device not in the ladder
    else:
        raise ShadingLadderExhaustedError(
            f"device {device!r} has no entry in the LM-83-12 §2.2.7 shading fallback ladder"
        )
