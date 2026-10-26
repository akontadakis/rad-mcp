"""Scene geometry, the pure checks.

Pure: no Radiance, no subprocesses, no file IO, no file formats. Runs green with no binaries
installed. `rad_engine` parses a `.rad` file (or any other importer -- an OBJ, a future gbXML/IFC
route) into the `Polygon` soup this module takes as its only input; `rad_core` never reads a file
(docs/tool-surface.md#4-geometry: *"rad_core.geometry holds the pure checks: enclosure test on a
polygon soup, coplanar duplicate detection, degenerate-polygon detection, bounding box arithmetic,
unit conversion."*). Phase 10 adds four more pure checks over the same soup, all scaled to metres:
`coincident_pairs`/`coincident_surfaces` (rewritten -- true in-plane overlap on a coplanar pair, not
only an identical vertex set), `non_planar_polygons`/`non_planar_warning` (a stored polygon's
deviation from its own Newell plane), `windows_without_opening`/`window_warning` (a glass face with
no cut opaque face behind it), and `non_finite_polygon_ids` (a polygon carrying a NaN/inf coordinate,
so every check above can skip it instead of raising).

**The polygon soup is the contract with `rad_engine`.** A `Polygon` is a Radiance `polygon`
primitive already parsed into coordinates: an id (the primitive's own name, e.g. `"f_2_0"`, for
actionable error messages), a modifier name, and a vertex loop. Nothing here knows about `.rad`
syntax, `!xform` chains, or `oconv` -- that parsing is Task 5's job, in `rad_engine`.

**How advisories leave this layer**, exactly the split `rad_core.grids` uses (see that module's
docstring):

- A **refusal** (`error` severity, the operation did not happen) is a raised exception.
  `GeometryError` and its subclasses carry a class-attribute `code`, the same shape
  `rad_core.grids.GridError` and `rad_engine.project.ProjectError` use, for the same reason: the wire
  envelope is the protocol layer's job, and this layer only needs to carry *which* allocated code
  applies.
- A **finding that does not stop the operation** (`info`/`warn`, the result is still usable) is
  `rad_core.manifest.ArtifactWarning` -- "one classified advisory, kept with the artifact" -- reused
  rather than a second code/severity/message shape invented here.

**Check 2.4 is a negative assertion and it is the point of this module.** Radiance polygons are
two-sided (CONTEXT.md), so a face normal pointing "the wrong way" is not a defect. Nothing in this
module computes or reports a normal or a winding direction for any surface -- there is no function
here that could. Normal direction *is* computed for the aperture, where it matters for Klems binning,
but that is `rad_core.apertures` in a later phase and explicitly not this module's concern.

**`NOT_ENCLOSED` stays at `warn`, correctly.** Fix round 1 of code review found the false-positive
rate reported in this module's first submission was a defect in `free_edge_count` (then named
`unmatched_edge_count`), not evidence the register's `warn` severity was wrong: it conflated free
edges (`count == 1`, a genuine hole in the volume) with non-manifold junctions (`count > 2`, e.g. a
window frame's mullions -- a legitimate junction, not a hole). Once fixed, every scene fixture's own
room (ground excluded) reports zero free edges, including `space_a.rad`'s 246-polygon interior, which
the conflated rule had wrongly flagged at 24 (task-2-report.md's fix-round-1 section has the full
re-measurement). `enclosure_warning` takes a `ground_modifiers` argument so a caller states the
soup's documented precondition ("the interior polygon soup", per the register) explicitly rather than
this module hedging about it in the message -- see that function's docstring.
"""

from __future__ import annotations

import bisect
import itertools
import math
from collections import Counter
from collections.abc import Collection, Iterable, Sequence
from dataclasses import dataclass
from typing import Final

from rad_core.manifest import ArtifactWarning, Units
from rad_core.planar import Point, _GEOM_EPS, _orientation, _point_in_polygon

__all__ = [
    "COINCIDENCE_MAX_REPORTED_PAIRS",
    "COINCIDENCE_TOLERANCE_M",
    "END_CAP_MAX_AREA_M2",
    "NON_PLANAR_TOLERANCE_M",
    "OCONV_DEFAULT_MAXRES",
    "SITE_SCALE_BOUND_M",
    "TRIANGULATION_AREA_TOLERANCE_M2",
    "BoundingBox",
    "GeometryError",
    "GeometryInventory",
    "Polygon",
    "UndefinedModifierError",
    "area_by_modifier",
    "area_removed",
    "area_vector",
    "bounding_box",
    "coincident_pairs",
    "coincident_surfaces",
    "convert_to_metres",
    "degenerate_polygon_ids",
    "enclosure_warning",
    "far_from_origin",
    "far_from_origin_warning",
    "free_edge_count",
    "inventory",
    "metres_per_unit",
    "non_finite_polygon_ids",
    "non_planar_polygons",
    "non_planar_warning",
    "octree_resolution_ratio",
    "scale_warning",
    "triangulation_warning",
    "window_warning",
    "windows_without_opening",
    "Point3",
]

Point3 = tuple[float, float, float]

#: Coordinate quantisation for the hash-based comparisons below (edge matching, vertex-set
#: equality): 1e-6 m, one micron. Not sourced from a spec -- a judgement call, justified by the
#: fixtures themselves: every `.rad` file under tests/fixtures/scenes writes vertices to exactly six
#: decimal places, so 1e-6 m is below the precision any of them actually carry and well below any
#: real construction tolerance, while still merging float noise from upstream unit conversion.
_QUANTIZE_DECIMALS = 6

#: Squared-magnitude threshold on a polygon's Newell area vector, below which it is degenerate
#: (collinear or coincident vertices -- no interior). `area_vector`'s magnitude equals the
#: polygon's true area (verified against a unit square: area vector `(0, 0, 1)`, magnitude 1), so
#: this is a threshold on area *squared* -- same order of magnitude as `rad_core.grids`'
#: `_GEOM_EPS` (1e-9), but applied to squared area rather than a linear coincidence tolerance: a
#: polygon has to fall under `sqrt(1e-9)` ~= 3.16e-5 m^2 (about 31.6 square millimetres, verified
#: computationally, not estimated) of true area to trip this. That does not mean no real building
#: surface can: a finely-tessellated curved surface's individual facet can be smaller than this
#: while genuinely non-degenerate in area but still carry an edge that is noise, not detail -- see
#: `_MIN_EDGE_LENGTH_M`'s docstring and `TestOctreeResolutionRatio`'s many-sided-polygon fixture,
#: which sits just above this boundary on purpose.
_DEGENERATE_AREA_EPS = 1e-9

#: `oconv -r maxres`'s own default when `-r` is not given (docs/tool-surface.md's `OCTREE_RESOLUTION_LOW`
#: row). Exported so `rad_engine` does not have to re-cite this number to compare it against
#: `octree_resolution_ratio`'s return value -- this module computes the ratio, `rad_engine` raises
#: `-r` to meet it (see that row and this module's own docstring above).
OCONV_DEFAULT_MAXRES = 16384

#: Metres per unit, `rad_core.manifest.Units`' four non-metre members. Exact SI/imperial definitions
#: (1 ft = 0.3048 m, 1 in = 0.0254 m by international agreement), not a substituted default -- these
#: do not carry `ProvenanceValue` for the same reason `rad_core.grids`' LM-83 constants do not: they
#: are literal conversion facts, not a value this module chose on the caller's behalf.
_METRES_PER_UNIT: dict[Units, float] = {
    "m": 1.0,
    "cm": 0.01,
    "mm": 0.001,
    "ft": 0.3048,
    "in": 0.0254,
}

#: `GEOMETRY_SCALE_SUSPECT`'s own bound (docs/tool-surface.md: "Bounding box implies a room over
#: 200 m or under 0.5 m in a plan dimension. Usually a units error."). Checked against the x/y
#: (plan) extent only -- a very tall or very short storey is not the units mistake this code names.
_SCALE_MIN_M = 0.5
_SCALE_MAX_M = 200.0

#: Below this, an edge is tessellation/import noise, not modelled detail, and is excluded from
#: `octree_resolution_ratio`'s smallest-edge scan. docs/tool-surface.md's own example calls the
#: detail this ratio protects "millimetre-scale mullion detail" -- an edge under half a millimetre is
#: not on that scale. Verified against a real case, not chosen blind: `space_a.rad`'s `f_241_0` (a
#: curved mirror's tessellation facet, modifier `hel_lightshelve_mirrorsurf_mat`) has three edges at
#: 0.096-0.395 mm, CAD curve-fitting artifacts of the facet's own tessellation, sitting almost an
#: order of magnitude below the next-smallest genuine edge anywhere in that fixture (5 mm, a real
#: reveal on `f_227_0`, modifier `hel_lightshelve_mat`). Without this floor,
#: `octree_resolution_ratio` on the whole `space_a` soup (ground included) is 379166.7, driven
#: entirely by the 0.096 mm sliver; with it, 7280.0, driven by the 5 mm reveal -- both computed from
#: the same real bounding box, so the floor is the entire difference (task-2-report.md).
#:
#: **A conservative default, not a measurement -- recorded in docs/tool-surface.md's "Unsourced
#: values" table, not just here.** The two directions of error are not symmetric: too low lets
#: tessellation noise through and over-inflates the ratio (`rad_engine` sets a needlessly high `-r`
#: -- wasteful but safe); too high silently excludes genuine fine detail and understates the ratio,
#: which is the `OCTREE_RESOLUTION_LOW` failure mode itself ("the octree under-resolves fine
#: geometry with no error"). 0.5 mm was set from an observed decade of separation in real geometry,
#: not picked to be merely "small" -- see the table entry for what would replace the guess.
_MIN_EDGE_LENGTH_M = 0.0005


# --------------------------------------------------------------------------------------------
# The polygon soup -- the contract with rad_engine
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Polygon:
    """One Radiance `polygon` primitive, already parsed into coordinates.

    `id` is the primitive's own name (e.g. `"f_2_0"` in the office fixture) -- carried through so
    every advisory and refusal below can name the offending surface rather than just its position in
    a list. `modifier` is the material name the primitive references. `vertices` is the loop in
    whatever units the caller states (see `convert_to_metres`); every function below except that one
    assumes metres.
    """

    id: str
    modifier: str
    vertices: tuple[Point3, ...]


# --------------------------------------------------------------------------------------------
# Refusals
# --------------------------------------------------------------------------------------------


class GeometryError(ValueError):
    """Something this module refuses to compute.

    Mirrors `rad_core.grids.GridError`'s shape -- see that class and this module's own docstring for
    why an exception, not `ArtifactWarning`, is the right shape for an `error`-severity finding at
    this layer.
    """

    code: str | None = None
    severity: str = "error"
    field: str | None = None


class UndefinedModifierError(GeometryError):
    """A polygon's modifier is not among the materials `oconv` will have read.

    docs/plan.md check 2.3 / docs/tool-surface.md: *"Geometry references a material `oconv` had not
    read. Materials must precede the geometry that uses them."* `field` is left `None` rather than
    naming one of `inventory`'s two arguments: neither `polygons` nor `known_modifiers` is wrong on
    its own -- it is the *mismatch* between them that this refuses, the same way a `KeyError` does
    not blame the dict or the key alone.

    `undefined_modifiers` carries the offending set as data, not only as prose inside the message:
    `geometry(action="check")` (docs/tool-surface.md#4-geometry) "reports without registering the
    geometry" -- a non-refusing path that still has to fill `checks.undefined_modifiers: [str]`
    -- and a caller
    that could only get that list by parsing this exception's message string is exactly the failure
    this attribute exists to avoid.
    """

    code = "UNDEFINED_MODIFIER"

    def __init__(self, message: str, *, undefined_modifiers: frozenset[str]) -> None:
        super().__init__(message)
        self.undefined_modifiers = undefined_modifiers


# --------------------------------------------------------------------------------------------
# Shared geometry helpers
# --------------------------------------------------------------------------------------------


def _quantize(point: Point3) -> Point3:
    return (
        round(point[0], _QUANTIZE_DECIMALS),
        round(point[1], _QUANTIZE_DECIMALS),
        round(point[2], _QUANTIZE_DECIMALS),
    )


def area_vector(vertices: Sequence[Point3]) -> Point3:
    """Newell's method: twice the polygon's signed area, as a vector along its normal.

    Works for any planar (or near-planar) loop without needing the plane's normal in advance --
    exactly the situation a Radiance `polygon` primitive is in, since Radiance itself infers the
    plane from the vertex loop the same way.

    Public, and shared with `rad_core.apertures` (promoted there in Phase 6a), which is the one
    place in this codebase that needs this vector's *direction* rather than only its magnitude:
    this module uses it solely to test degeneracy (`_is_degenerate`) and, per the module docstring,
    deliberately reports no normal or winding direction of its own for a scene surface. Promoting
    the arithmetic itself -- rather than letting `apertures.py` reimplement or reach across a
    private boundary -- keeps both uses on one arithmetic path by construction (R30's own
    reasoning), so a future fix to Newell's method here cannot silently diverge from the aperture
    normal it also feeds.
    """
    nx = ny = nz = 0.0
    n = len(vertices)
    for i in range(n):
        x1, y1, z1 = vertices[i]
        x2, y2, z2 = vertices[(i + 1) % n]
        nx += (y1 - y2) * (z1 + z2)
        ny += (z1 - z2) * (x1 + x2)
        nz += (x1 - x2) * (y1 + y2)
    return (nx / 2.0, ny / 2.0, nz / 2.0)


def _is_degenerate(polygon: Polygon) -> bool:
    if len(polygon.vertices) < 3:
        return True
    ax, ay, az = area_vector(polygon.vertices)
    return (ax * ax + ay * ay + az * az) < _DEGENERATE_AREA_EPS


def degenerate_polygon_ids(polygons: Sequence[Polygon]) -> frozenset[str]:
    """Ids of polygons with no real interior: fewer than three vertices, or vertices collinear or
    coincident enough that the Newell area vector is (numerically) zero.

    Returned as plain data, not an advisory -- by design, not a gap. `docs/tool-surface.md`'s
    section 4 `geometry` publishes this as a **count** inside the result's `checks` block, not as an
    advisory-producing finding, and nothing maps this function's output to a code: every caller in
    `src/` reads it either for that count or to *exclude* faces before another test runs, and those
    callers are listed at the end of this docstring.

    Earlier versions of this paragraph went on to survey where else in this repository degeneracy
    carries an allocated code. Four of them were false and the survey is deleted rather than
    narrowed a fifth time: that claim spans other modules, other tools and Radiance's own behaviour,
    and no leaf function here can close it. The claim above is closable from this module, and it is
    the one the "plain data, not an advisory" reading needs.

    The asymmetry with `coincident_surfaces` -- which builds one `ArtifactWarning` per pair up to
    `COINCIDENCE_MAX_REPORTED_PAIRS`, then one more carrying the total -- is deliberate: a duplicate
    surface leaks light at a seam and is worth a warning naming the two ids; a zero-area polygon is
    inert, contributes nothing to occlusion or reflection, and has no comparable failure mode to
    warn about, only a count to report.

    **Cited by section rather than by line, and the type is Phase 10's.** This paragraph used to
    cite `docs/tool-surface.md:1533` for `checks.degenerate_polygons: int`. Both halves expired in
    one commit: Phase 10 Task 15 rewrote section 4 above that point, and the same task published the
    field as `degenerate_polygons: int | null`, so the citation named a line that had become prose
    and a type the surface no longer declares. A line number into a document nine tasks of one phase
    edit is fragile by construction (ruling R372); a section is not.

    **The nullability is a caller's decision and is invisible here.** This function always returns a
    set. `rad_mcp.geometry_tool._checks` publishes `null` instead of a count when a `check` declared
    no units, because the degeneracy test rests on an absolute tolerance -- and that is a statement
    about a question not asked, not about a finding suppressed. It does not weaken the argument
    above: nullable or not, the surface still types this as a count.

    **All four callers in `src/`, which is what the claim above rests on.**
    `rad_mcp.geometry_tool._checks` fills the published count; `coincident_pairs` (and so
    `coincident_surfaces`), `windows_without_opening` and `_smallest_edge_length` (the edge-length
    scan behind `octree_resolution_ratio`) each drop degenerate faces before their own test runs.
    The set is closed as of this commit -- that is the point of it -- and it has grown once already,
    when Phase 10 added the window check, so a fifth caller belongs in this list on the commit that
    adds it.
    """
    return frozenset(p.id for p in polygons if _is_degenerate(p))


# --------------------------------------------------------------------------------------------
# Bounding box arithmetic
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class BoundingBox:
    """An axis-aligned bounding box, metres."""

    x_min: float
    x_max: float
    y_min: float
    y_max: float
    z_min: float
    z_max: float

    @property
    def size_x(self) -> float:
        return self.x_max - self.x_min

    @property
    def size_y(self) -> float:
        return self.y_max - self.y_min

    @property
    def size_z(self) -> float:
        return self.z_max - self.z_min


def bounding_box(polygons: Sequence[Polygon]) -> BoundingBox:
    """The axis-aligned box circumscribing every vertex of every polygon in the soup."""
    if not polygons:
        raise GeometryError("bounding_box() needs at least one polygon; got an empty soup.")
    xs = [v[0] for p in polygons for v in p.vertices]
    ys = [v[1] for p in polygons for v in p.vertices]
    zs = [v[2] for p in polygons for v in p.vertices]
    return BoundingBox(min(xs), max(xs), min(ys), max(ys), min(zs), max(zs))


def scale_warning(box: BoundingBox) -> ArtifactWarning | None:
    """`GEOMETRY_SCALE_SUSPECT` (warn) when the plan (x/y) extent implies a units mistake.

    docs/tool-surface.md: "Bounding box implies a room over 200 m or under 0.5 m in a plan dimension.
    Usually a `units` error." `z` (storey height) is deliberately not checked -- a very tall atrium
    or a very short crawl space is not the units confusion this code names.
    """
    suspect = [
        (axis, size)
        for axis, size in (("x", box.size_x), ("y", box.size_y))
        if size > _SCALE_MAX_M or size < _SCALE_MIN_M
    ]
    if not suspect:
        return None
    detail = ", ".join(f"{axis}={size:g} m" for axis, size in suspect)
    return ArtifactWarning(
        code="GEOMETRY_SCALE_SUSPECT",
        severity="warn",
        message=(
            f"plan extent {detail} is outside the {_SCALE_MIN_M:g}-{_SCALE_MAX_M:g} m band this "
            "geometry's bounding box implies is plausible for a room -- usually a units error "
            "(docs/tool-surface.md)."
        ),
    )


#: How far from the origin, on any axis, coordinates may reach before `far_from_origin` reports them
#: (ruling R827). **Chosen, not sourced**: a room or a building fits well inside 1 km of its own
#: origin, while a site or survey coordinate (a UTM easting is six digits of metres) does not. The
#: geometric tolerances these checks use are absolute -- `rad_core.planar._GEOM_EPS` on a quantity
#: quadratic in coordinate magnitude, the 1 mm `COINCIDENCE_TOLERANCE_M`, the 1 micron
#: `_QUANTIZE_DECIMALS` -- so the further out a model sits, the fewer of float64's digits are left for the room itself. Too low
#: warns on a large campus modelled in one frame; too high stays silent on a model placed at its
#: survey coordinates.
SITE_SCALE_BOUND_M: Final = 1000.0


def far_from_origin(points: Iterable[Point3]) -> float | None:
    """The largest `|x|`, `|y|` or `|z|` over `points` when it exceeds `SITE_SCALE_BOUND_M`, else
    `None`. Non-finite coordinates are skipped: they are refused on their own
    (`NON_FINITE_COORDINATES`) and are not a distance."""
    reach = max(
        (abs(c) for point in points for c in point if math.isfinite(c)),
        default=0.0,
    )
    return reach if reach > SITE_SCALE_BOUND_M else None


def far_from_origin_warning(points: Iterable[Point3], *, field: str | None = None) -> ArtifactWarning | None:
    """`COORDINATES_FAR_FROM_ORIGIN` (warn) when `far_from_origin(points)` reports a distance: one
    message for both callers, `geometry`'s imports and `sensor_grid(action="create")` (ruling R827)."""
    reach = far_from_origin(points)
    if reach is None:
        return None
    shown = f"{reach:.1f}".rstrip("0").rstrip(".")
    return ArtifactWarning(
        code="COORDINATES_FAR_FROM_ORIGIN",
        severity="warn",
        field=field,
        message=(
            f"coordinates reach {shown} m from the origin; many of this server's geometric "
            "tolerances are absolute, so recentre the model near the origin: translate it to a local "
            "site origin before import, and give sensor grids coordinates in that same frame. A model "
            f"kept at a true elevation above {SITE_SCALE_BOUND_M:g} m reaches this too."
        ),
    )


# --------------------------------------------------------------------------------------------
# Geometry inventory -- the pure half of check 2.3
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class GeometryInventory:
    """Polygon count and the modifier set actually used. `bounding_box` is a separate function
    (docs/tool-surface.md#4-geometry lists it as its own pure check) rather than a field here."""

    polygon_count: int
    modifiers: frozenset[str]


def inventory(polygons: Sequence[Polygon], known_modifiers: Collection[str]) -> GeometryInventory:
    """Polygon count and modifier set, refusing (`UndefinedModifierError`) if any polygon references
    a modifier outside `known_modifiers` -- the materials `rad_engine`/Task 5 has already registered.

    `known_modifiers` is always an explicit argument, never inferred from the soup itself: the whole
    point of `UNDEFINED_MODIFIER` is to catch a polygon whose modifier `oconv` never read, and a soup
    cannot report that about itself.
    """
    modifiers = frozenset(p.modifier for p in polygons)
    undefined = modifiers - frozenset(known_modifiers)
    if undefined:
        raise UndefinedModifierError(
            f"{len(polygons)} polygon(s) reference undefined modifier(s) {sorted(undefined)!r} -- "
            "materials must be registered before the geometry that uses them (oconv reads materials "
            "first; docs/tool-surface.md#4-geometry).",
            undefined_modifiers=undefined,
        )
    return GeometryInventory(polygon_count=len(polygons), modifiers=modifiers)


# --------------------------------------------------------------------------------------------
# Coplanar overlap detection
# --------------------------------------------------------------------------------------------

#: Two faces closer than this along their shared normal are one plane. **A default, not a
#: measurement** (donor value, design §7.5). Too low misses a copy standing 1-2 mm proud; too high
#: flags a thin reveal.
COINCIDENCE_TOLERANCE_M: Final = 0.001

#: The largest face the donor's end-cap gate may excuse. **A default** (donor value, sized above
#: office_room's largest wall-thickness return, ~1.10 m²). Too low: false positives where box walls
#: meet (8 on office_room with a stricter gate, design §7.6). Too high: the known misses grow.
END_CAP_MAX_AREA_M2: Final = 2.0

#: Peak-to-peak distance from the Newell plane above which a stored polygon is non-planar. **A
#: default** (donor value). Too low warns on tessellation noise; too high lets a visible warp through.
NON_PLANAR_TOLERANCE_M: Final = 0.005

#: Edge directions within this many radians of each other, once folded onto a quarter turn, are one
#: direction when `_end_cap_frames` groups the two faces' edges (ruling R850). **Chosen, not
#: sourced**: 1e-3 rad is a 1 mm drift along a 1 m edge, wide enough for a modeller's rounding. A
#: triangle's diagonal falls inside it only when the triangle is thinner than about 1:1000, and the
#: frame is then the group's length-weighted mean direction, turned from the legs by at most the
#: diagonal's share of that small angle. Too low splits one drawn direction into several; too high
#: merges genuinely different directions.
SHARED_DIRECTION_TOLERANCE_RAD: Final = 1e-3

#: Pairs named one per advisory before the rest are counted in one. **A default**: the total is
#: always reported, so too low only names fewer pairs; too high floods the result.
COINCIDENCE_MAX_REPORTED_PAIRS: Final = 20

PlaneKey = tuple[int, int, int, int]


def _finite(polygon: Polygon) -> bool:
    return all(math.isfinite(c) for vertex in polygon.vertices for c in vertex)


def _unit_normal(polygon: Polygon) -> Point3 | None:
    ax, ay, az = area_vector(polygon.vertices)
    length = math.sqrt(ax * ax + ay * ay + az * az)
    if length == 0.0:
        return None
    return (ax / length, ay / length, az / length)


def _plane_key(polygon: Polygon, tolerance: float) -> PlaneKey | None:
    normal = _unit_normal(polygon)
    if normal is None:
        return None
    if normal < tuple(-c for c in normal):
        normal = (-normal[0], -normal[1], -normal[2])
    x, y, z = polygon.vertices[0]
    offset = normal[0] * x + normal[1] * y + normal[2] * z
    return (round(normal[0] / tolerance), round(normal[1] / tolerance),
            round(normal[2] / tolerance), round(offset / tolerance))


def _dominant_axis(normal: Point3) -> int:
    return max(range(3), key=lambda k: abs(normal[k]))


def _project(vertices: Sequence[Point3], axis: int) -> tuple[Point, ...]:
    keep = [k for k in range(3) if k != axis]
    return tuple((v[keep[0]], v[keep[1]]) for v in vertices)


def _area(polygon: Polygon) -> float:
    ax, ay, az = area_vector(polygon.vertices)
    return math.sqrt(ax * ax + ay * ay + az * az)


def _interior_samples(outline: Sequence[Point]) -> list[Point]:
    """Centroids of the fan triangles that lie strictly inside `outline` (a concave outline's fan
    has triangles outside it, which are dropped)."""
    samples = []
    for i in range(1, len(outline) - 1):
        a, b, c = outline[0], outline[i], outline[i + 1]
        point = ((a[0] + b[0] + c[0]) / 3.0, (a[1] + b[1] + c[1]) / 3.0)
        if _point_in_polygon(point, outline):
            samples.append(point)
    return samples


def _properly_cross(p1: Point, p2: Point, p3: Point, p4: Point) -> bool:
    d1, d2 = _orientation(p3, p4, p1), _orientation(p3, p4, p2)
    d3, d4 = _orientation(p1, p2, p3), _orientation(p1, p2, p4)
    return (d1 * d2 < 0 and abs(d1) > _GEOM_EPS and abs(d2) > _GEOM_EPS
            and d3 * d4 < 0 and abs(d3) > _GEOM_EPS and abs(d4) > _GEOM_EPS)


def _outlines_overlap(a: Sequence[Point], b: Sequence[Point]) -> bool:
    """True in-plane overlap: a proper edge crossing, or an interior sample of one strictly inside
    the other. Touching edges and shared corners are not overlap (boundary counts as outside)."""
    for i in range(len(a)):
        for j in range(len(b)):
            if _properly_cross(a[i], a[(i + 1) % len(a)], b[j], b[(j + 1) % len(b)]):
                return True
    return any(_point_in_polygon(s, b) for s in _interior_samples(a)) or any(
        _point_in_polygon(s, a) for s in _interior_samples(b)
    )


def _dot(u: Point3, v: Point3) -> float:
    return u[0] * v[0] + u[1] * v[1] + u[2] * v[2]


def _cross(u: Point3, v: Point3) -> Point3:
    return (u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2], u[0] * v[1] - u[1] * v[0])


def _extent(polygon: Polygon, direction: Point3) -> tuple[float, float]:
    values = [_dot(vertex, direction) for vertex in polygon.vertices]
    return min(values), max(values)


def _normal_extents_meet(a: Polygon, b: Polygon, normal: Point3, tolerance: float) -> bool:
    """The two faces' extents along `normal` (the first face's unit normal) come within `tolerance`
    of each other: the "one plane" test, in the plane's own frame rather than along the world axis
    nearest the normal, so it says the same of a tilted pair as of a level one (ruling R850)."""
    half = tolerance / 2.0
    (a0, a1), (b0, b1) = _extent(a, normal), _extent(b, normal)
    return max(a0, b0) - half <= min(a1, b1) + half


#: Two group weights within this relative difference are a tie (ruling R850): float rounding of
#: edge lengths after a rotation must not decide between two directions of one length. **Chosen,
#: not sourced**: far above float64 rounding of a sum of a few lengths (~1e-15 relative), far below
#: any difference a modeller draws.
_WEIGHT_TIE_RELATIVE: Final = 1e-9


def _end_cap_frames(a: Polygon, b: Polygon, normal: Point3) -> list[tuple[Point3, Point3]]:
    """The in-plane frames the end-cap gate must pass in, each two orthonormal directions (ruling R850).

    The two faces' edges, projected into the plane, are grouped by direction, directions a quarter
    turn apart counted as one (within `SHARED_DIRECTION_TOLERANCE_RAD`), each group weighed by its
    total edge length. **When one group is the heaviest, with no tie, and holds edges of both
    faces**, that is the one frame: its length-weighted mean direction and the perpendicular in the
    plane. For faces drawn square to the axes that is the world axes the donor's gate measured
    along; turned, it turns with them; a triangle of a split rectangle carries its two legs in the
    rectangle's directions, which together outweigh its diagonal.

    **Otherwise -- the faces share no direction, or two directions tie -- every edge's own direction
    is a frame**, and the gate must pass in all of them. A single frame chosen there would be one
    face's or the other's, by weight or by rounding, so the verdict would change with orientation
    and could drop a true overlap the other frame reports; every frame together rotates with the
    faces, and excuses only what each face's own frame excuses."""
    edges: list[tuple[Point3, float]] = []
    owner: list[int] = []
    for side, polygon in enumerate((a, b)):
        vertices = polygon.vertices
        for i, start in enumerate(vertices):
            end = vertices[(i + 1) % len(vertices)]
            step = (end[0] - start[0], end[1] - start[1], end[2] - start[2])
            along = _dot(step, normal)
            step = (step[0] - along * normal[0], step[1] - along * normal[1], step[2] - along * normal[2])
            length = math.sqrt(_dot(step, step))
            if length > 0.0:
                edges.append(((step[0] / length, step[1] / length, step[2] / length), length))
                owner.append(side)
    reference = edges[0][0]
    across = _cross(normal, reference)
    quarter = math.pi / 2.0
    angles = [math.atan2(_dot(d, across), _dot(d, reference)) % quarter for d, _ in edges]

    def offset(i: int, j: int) -> float:
        """`angles[j] - angles[i]`, folded into (-quarter/2, quarter/2]."""
        apart = (angles[j] - angles[i]) % quarter
        return apart - quarter if apart > quarter / 2.0 else apart

    def frame(angle: float) -> tuple[Point3, Point3]:
        c, s = math.cos(angle), math.sin(angle)
        u = tuple(c * reference[k] + s * across[k] for k in range(3))
        return u, _cross(normal, u)

    # Each edge's group weight, by bisection over the angles sorted and repeated a quarter turn
    # either side (the fold wraps), so the grouping is O(E log E), not O(E^2).
    tol = SHARED_DIRECTION_TOLERANCE_RAD
    spread = sorted(
        (angle + shift, edges[j][1])
        for j, angle in enumerate(angles) for shift in (-quarter, 0.0, quarter)
    )
    keys = [angle for angle, _ in spread]
    running = list(itertools.accumulate((length for _, length in spread), initial=0.0))
    weight = [
        running[bisect.bisect_right(keys, angle + tol)] - running[bisect.bisect_left(keys, angle - tol)]
        for angle in angles
    ]
    heaviest = max(weight)
    tied = [i for i in range(len(edges)) if weight[i] >= heaviest * (1.0 - _WEIGHT_TIE_RELATIVE)]
    seed = tied[0]
    members = [j for j in range(len(edges)) if abs(offset(seed, j)) <= tol]
    one_direction = all(abs(offset(seed, i)) <= tol for i in tied)
    if one_direction and {owner[j] for j in members} == {0, 1}:
        mean = sum(edges[j][1] * offset(seed, j) for j in members) / sum(edges[j][1] for j in members)
        return [frame(angles[seed] + mean)]
    return [frame(angle) for angle in angles]


def _end_cap_gated(a: Polygon, b: Polygon, normal: Point3, tolerance: float) -> bool:
    """The donor's gate, quoted in design §7.1 from hcl-mcp `geometry_health.py:202-221`: the smaller
    face is at most END_CAP_MAX_AREA_M2, and along one of the two in-plane directions the two extents
    are not equal and one is a subset of the other, in either direction.

    **The directions are the plane's own** (`_end_cap_frames`, ruling R850), where the donor took the
    two world axes other than the one nearest the normal. Square to the axes the two are the same;
    turned off them, the world boxes of a wall top and the wall it meets at a corner stop nesting,
    and `office_room.obj` tiled 4 x 4 and turned 17 degrees reported 64 pairs against 0 square. A
    pair sharing no edge direction is excused only when every frame `_end_cap_frames` returns
    excuses it."""
    if min(_area(a), _area(b)) > END_CAP_MAX_AREA_M2:
        return False
    # Ruling R449 (the owner): 10 µm at the default tolerance, not 1 µm. A triangle loses one of its
    # quad's extreme corners, and `office_room`'s 1 µm coordinate asymmetry then un-nests a junction
    # the quad excused; `2.150001 - 2.15` is 1.0000000001398e-06 in float64, so 1e-6 would pass
    # that by rounding luck. The survey's genuine duplicate is reported at every slack up to 1e-3.
    eps = max(tolerance * 1e-2, 1e-12)

    def nests(direction: Point3) -> bool:
        (a0, a1), (b0, b1) = _extent(a, direction), _extent(b, direction)
        equal = abs(a0 - b0) <= eps and abs(a1 - b1) <= eps
        a_in_b = a0 >= b0 - eps and a1 <= b1 + eps
        b_in_a = b0 >= a0 - eps and b1 <= a1 + eps
        return not equal and (a_in_b or b_in_a)

    return all(nests(u) or nests(v) for u, v in _end_cap_frames(a, b, normal))


#: The 3x3x3x3 neighbourhood of a `PlaneKey`: each quantised normal component and the offset one
#: bucket either way (ruling R826).
_KEY_NEIGHBOURS: Final = tuple(itertools.product((-1, 0, 1), repeat=4))


def coincident_pairs(
    polygons: Sequence[Polygon], *, tolerance: float = COINCIDENCE_TOLERANCE_M
) -> tuple[tuple[str, str], ...]:
    """Pairs of faces in one plane within `tolerance` that truly overlap, minus the end-cap gate
    (design §7.1), in index order. Degenerate and non-finite polygons are skipped.

    **Neighbouring buckets are compared, in all four key components** (ruling R826). Each face is
    registered once under its own `_plane_key`, and every bucket is compared with each bucket at
    most one step away in each of the three quantised normal components and the offset, so a pair whose
    normals or offsets round either side of a `round(v / tolerance)` boundary is still compared --
    and with the same neighbourhood around its **negated** key, because `_plane_key`'s sign rule
    flips a normal whose leading component is float noise of either sign, which gives one plane two
    opposite keys. Each pair is tested once, with no record of the pairs tested: `i < j` keeps the
    lower index's visit, and no bucket lies in the neighbourhood of both a key and its negation
    (that needs every component of twice the key within 2, which a quantised unit normal cannot
    be). Measured before it was built, over every `.rad` and `.obj` under tests/fixtures/ and every
    `.rad` in a private scene library: total 249.84 -> 150.31 ms, the slowest file 55.78 -> 30.83 ms
    (docs/plan.md, R826)."""
    degenerate = degenerate_polygon_ids(polygons)
    buckets: dict[PlaneKey, list[int]] = {}
    for index, polygon in enumerate(polygons):
        if polygon.id in degenerate or not _finite(polygon):
            continue
        key = _plane_key(polygon, tolerance)
        if key is None:
            continue
        buckets.setdefault(key, []).append(index)
    found: list[tuple[int, int]] = []
    outlines: dict[tuple[int, int], tuple[Point, ...]] = {}

    def outline(index: int, axis: int) -> tuple[Point, ...]:
        if (index, axis) not in outlines:
            outlines[(index, axis)] = _project(polygons[index].vertices, axis)
        return outlines[(index, axis)]

    for key, members in buckets.items():
        for base in (key, (-key[0], -key[1], -key[2], -key[3])):
            for d in _KEY_NEIGHBOURS:
                others = buckets.get((base[0] + d[0], base[1] + d[1], base[2] + d[2], base[3] + d[3]))
                if not others:
                    continue
                for i in members:
                    for j in others:
                        if i >= j:  # each unordered pair once, from the lower index's bucket
                            continue
                        a, b = polygons[i], polygons[j]
                        normal = _unit_normal(a)
                        assert normal is not None
                        axis = _dominant_axis(normal)
                        if (_normal_extents_meet(a, b, normal, tolerance)
                                and _outlines_overlap(outline(i, axis), outline(j, axis))
                                and not _end_cap_gated(a, b, normal, tolerance)):
                            found.append((i, j))
    found.sort()
    return tuple((polygons[i].id, polygons[j].id) for i, j in found)


def coincident_surfaces(
    polygons: Sequence[Polygon], *, tolerance: float = COINCIDENCE_TOLERANCE_M
) -> tuple[ArtifactWarning, ...]:
    """`COINCIDENT_SURFACES` (warn): one per pair up to `COINCIDENCE_MAX_REPORTED_PAIRS`, then one
    carrying the total."""
    pairs = coincident_pairs(polygons, tolerance=tolerance)
    warnings = [
        ArtifactWarning(
            code="COINCIDENT_SURFACES",
            severity="warn",
            message=(
                f"polygon {a!r} and polygon {b!r} lie in one plane within {tolerance * 1000:g} mm and "
                "overlap. Radiance picks between coplanar surfaces per ray, so results speckle and "
                "drift between runs. Delete one, or offset it by more than the tolerance."
            ),
        )
        for a, b in pairs[:COINCIDENCE_MAX_REPORTED_PAIRS]
    ]
    if len(pairs) > COINCIDENCE_MAX_REPORTED_PAIRS:
        warnings.append(ArtifactWarning(
            code="COINCIDENT_SURFACES",
            severity="warn",
            message=f"{len(pairs)} coincident pairs in total; the first {COINCIDENCE_MAX_REPORTED_PAIRS} are named above.",
        ))
    return tuple(warnings)


def non_planar_polygons(
    polygons: Sequence[Polygon], *, tolerance: float = NON_PLANAR_TOLERANCE_M
) -> tuple[tuple[str, float], ...]:
    """`(id, peak-to-peak deviation)` for polygons of 4+ vertices deviating more than `tolerance`
    from their Newell plane (donor rule, design §7.2). Run on the stored polygons (decision 15)."""
    found = []
    for polygon in polygons:
        if len(polygon.vertices) < 4 or not _finite(polygon):
            continue
        normal = _unit_normal(polygon)
        if normal is None:
            continue
        n = len(polygon.vertices)
        centre = tuple(sum(v[k] for v in polygon.vertices) / n for k in range(3))
        signed = [sum(normal[k] * (v[k] - centre[k]) for k in range(3)) for v in polygon.vertices]
        deviation = max(signed) - min(signed)
        if deviation > tolerance:
            found.append((polygon.id, deviation))
    return tuple(found)


def non_planar_warning(polygons: Sequence[Polygon]) -> ArtifactWarning | None:
    found = non_planar_polygons(polygons)
    if not found:
        return None
    worst = max(deviation for _, deviation in found)
    named = ", ".join(repr(pid) for pid, _ in found[:COINCIDENCE_MAX_REPORTED_PAIRS])
    return ArtifactWarning(
        code="NON_PLANAR_FACE",
        severity="warn",
        message=(
            f"{len(found)} polygon(s) deviate from their plane by up to {worst * 1000:.1f} mm, over "
            f"{NON_PLANAR_TOLERANCE_M * 1000:g} mm ({named}). A Radiance polygon must be planar; a "
            "warped one shades undefined. Triangulate or flatten these faces in the model."
        ),
    )


def windows_without_opening(
    polygons: Sequence[Polygon],
    *,
    glass_modifiers: Collection[str],
    excluded_modifiers: Collection[str] = (),
    tolerance: float = COINCIDENCE_TOLERANCE_M,
) -> tuple[str, ...]:
    """Ids of glass faces whose vertex centroid lies strictly inside an opaque face in the same
    plane (donor centroid probe, design §7.3). `glass` only; faces of `excluded_modifiers` are not
    opaque."""
    glass_set, excluded = set(glass_modifiers), set(excluded_modifiers)
    degenerate = degenerate_polygon_ids(polygons)
    usable = [p for p in polygons if p.id not in degenerate and _finite(p)]
    opaque: dict[PlaneKey, list[Polygon]] = {}
    for polygon in usable:
        if polygon.modifier in glass_set or polygon.modifier in excluded:
            continue
        key = _plane_key(polygon, tolerance)
        if key is not None:
            opaque.setdefault(key, []).append(polygon)
            opaque.setdefault((key[0], key[1], key[2], key[3] + 1), []).append(polygon)
    blocked = []
    for glass in (p for p in usable if p.modifier in glass_set):
        key = _plane_key(glass, tolerance)
        if key is None:
            continue
        n = len(glass.vertices)
        centroid = tuple(sum(v[k] for v in glass.vertices) / n for k in range(3))
        candidates = opaque.get(key, []) + opaque.get((key[0], key[1], key[2], key[3] + 1), [])
        for wall in candidates:
            normal = _unit_normal(wall)
            assert normal is not None
            axis = _dominant_axis(normal)
            if _point_in_polygon(_project((centroid,), axis)[0], _project(wall.vertices, axis)):
                blocked.append(glass.id)
                break
    return tuple(blocked)


def window_warning(ids: Sequence[str]) -> ArtifactWarning | None:
    if not ids:
        return None
    return ArtifactWarning(
        code="WINDOW_WITHOUT_OPENING",
        severity="error",
        message=(
            f"{len(ids)} glass face(s) sit inside an unbroken opaque face in the same plane "
            f"({', '.join(repr(i) for i in ids[:COINCIDENCE_MAX_REPORTED_PAIRS])}). Radiance traces "
            "the wall, so the room goes dark while the run completes. Cut the opening in the wall."
        ),
    )


def non_finite_polygon_ids(polygons: Sequence[Polygon]) -> tuple[str, ...]:
    return tuple(polygon.id for polygon in polygons if not _finite(polygon))


# --------------------------------------------------------------------------------------------
# Triangulation area accounting -- what `robjutil +T` deleted (design 5.7)
# --------------------------------------------------------------------------------------------

#: Area a material may lose to `robjutil +T` before `TRIANGULATION_REMOVED_SURFACE` fires: 1 mm².
#: **A default, not a measurement.** Too low lets float noise raise the warning; too high hides a
#: removed face smaller than it.
TRIANGULATION_AREA_TOLERANCE_M2: Final = 1e-6


def area_by_modifier(polygons: Sequence[Polygon]) -> dict[str, float]:
    """Total surface area per modifier, over `_area` -- the one area function in this module.

    A non-finite polygon is left out, and the reason is that a `nan` would be **silent**: it
    propagates through the sum, and `nan > tolerance` is `False`, so one unparseable polygon would
    make `area_removed` report nothing lost for that modifier no matter how much surface was
    deleted. `non_finite_polygon_ids` is what reports the polygon itself.
    """
    totals: dict[str, float] = {}
    for polygon in polygons:
        if _finite(polygon):
            totals[polygon.modifier] = totals.get(polygon.modifier, 0.0) + _area(polygon)
    return totals


def area_removed(
    before: Sequence[Polygon], after: Sequence[Polygon], *, tolerance: float = TRIANGULATION_AREA_TOLERANCE_M2
) -> dict[str, float]:
    """Per modifier, the area `after` is **missing** relative to `before`, above `tolerance`.

    **One-sided, deliberately, and `abs()` here would be a defect.** Triangulating a warped quad
    *gains* a little true area: the two triangles together cover slightly more than the planar quad
    the warp was projected onto, so `after > before` is the ordinary outcome for every warped face in
    a CAD export. A symmetric difference would report all of them as lost surface and bury the one
    case this exists for -- a face `robjutil +T` deleted outright because it reused another face's
    vertex indices. A modifier that only gained is absent from the result, which is why the result is
    a dict of losses rather than of differences.

    Keyed on `before`'s modifiers: a modifier that exists only in `after` has lost nothing, and one
    that has vanished entirely is reported at its whole area (`b.get(m, 0.0)`).
    """
    a, b = area_by_modifier(before), area_by_modifier(after)
    return {m: a[m] - b.get(m, 0.0) for m in sorted(a) if a[m] - b.get(m, 0.0) > tolerance}


def triangulation_warning(
    removed: dict[str, float], before: dict[str, float], after: dict[str, float]
) -> ArtifactWarning | None:
    """`TRIANGULATION_REMOVED_SURFACE` naming each modifier that lost area, with both totals.

    `None` when nothing was lost, which is the ordinary case: `robjutil +T` splits faces and deletes
    none. `field="triangulate"` because the argument that caused it is the caller's.

    **The message names one known cause and stops short of asserting it, and that is the fix rather
    than the wording.** It used to state flatly that robjutil *"deletes a face that reuses another
    face's vertex indices"*. This function is handed two area dictionaries; it has no face
    identities, no vertex indices and no robjutil stderr, so it cannot establish that. The
    whole-branch review measured it firing on a model where nothing was deleted at all --
    `polygons_before=2, polygons_after=4` -- and sending the caller to hunt duplicate faces in a
    model that has none. A diagnosis a check cannot prove is worse than no diagnosis: it is a
    wrong instruction carrying the authority of a measurement.
    """
    if not removed:
        return None
    detail = "; ".join(
        f"{m}: {before.get(m, 0.0):.6g} m² before, {after.get(m, 0.0):.6g} m² after" for m in removed
    )
    return ArtifactWarning(
        code="TRIANGULATION_REMOVED_SURFACE", severity="warn", field="triangulate",
        message=(
            f"robjutil +T removed surface area ({detail}). The registered scene is the "
            "triangulated one. One known cause is a face that reuses another face's vertex "
            "indices, which robjutil deletes while keeping the later material -- but this "
            "advisory is computed from area alone and cannot tell you that is what happened."
        ),
    )


# --------------------------------------------------------------------------------------------
# Enclosure test
# --------------------------------------------------------------------------------------------


def free_edge_count(polygons: Sequence[Polygon], *, ground_modifiers: Collection[str] = ()) -> int:
    """The enclosure test: how many edges in the soup belong to exactly **one** polygon-edge instance
    -- a genuine hole in the volume, and the necessary (not sufficient) condition for "this soup does
    not bound a closed volume".

    Edges shared by three or more surfaces (`count > 2`) are **not** counted here. An earlier version
    of this function conflated the two (`count != 2` covered both `count == 1` and `count > 2`), and a
    real fixture proved the conflation wrong: three or more surfaces meeting along one line is a
    legitimate junction -- a window frame's mullions, a partition wall meeting an existing corner, a
    lightshelf bracket meeting a wall -- not a hole. `tests/fixtures/scenes/space_a.rad` minus its
    ground plane has **zero** free edges (a genuinely sealed 246-polygon volume) but 24 edges at
    `count == 3`, every one of them a `window_frame_mat`/glazing/`int_wall_mat` seam; the conflated
    rule reported a closed building as open. See task-2-report.md's fix-round-1 section for the full
    measurement.

    Counted per undirected, quantised edge, with no per-polygon deduplication: a Radiance "keyhole"
    polygon (a single primitive describing a wall with a window hole, cutting in to the hole and back
    out along the same segment -- `tests/fixtures/scenes/test_room/objects/OutsideFacade.rad`'s
    `f_6_0` is exactly this) legally traverses one physical edge twice, once each direction, from the
    *same* polygon. Counting by distinct owning polygon would misclassify that legitimate idiom as a
    free edge; counting raw occurrences (this function) nets it to zero, matching the surface's real
    shape in 3D.

    `ground_modifiers`, if given, excludes every polygon whose modifier is in it before the scan
    runs. `NOT_ENCLOSED`'s register entry scopes the code to *"the interior polygon soup"*
    (docs/tool-surface.md) -- this is how that precondition is enforced at the call site instead of
    assumed silently. Every real fixture measured has a ground plane whose own outer perimeter is a
    genuine free edge (office and test_room: 5 each; space_a: 5) that has nothing to do with whether
    the modelled room itself is closed -- pass the modifier(s) `materials`/`InputRecord.role` (ruling
    R9, `docs/tool-surface.md`'s `geometry(role=...)` argument) identifies as ground or exterior
    context to get the room's own answer instead of the whole scene's.
    """
    interior = (
        polygons if not ground_modifiers else tuple(p for p in polygons if p.modifier not in ground_modifiers)
    )
    counts: Counter[frozenset[Point3]] = Counter()
    for polygon in interior:
        n = len(polygon.vertices)
        for i in range(n):
            a, b = _quantize(polygon.vertices[i]), _quantize(polygon.vertices[(i + 1) % n])
            if a == b:
                continue  # a zero-length edge contributes no boundary
            counts[frozenset((a, b))] += 1
    return sum(1 for count in counts.values() if count == 1)


def enclosure_warning(
    polygons: Sequence[Polygon], *, ground_modifiers: Collection[str] = ()
) -> ArtifactWarning | None:
    """`NOT_ENCLOSED` (warn) when `free_edge_count` is nonzero. `ground_modifiers` is forwarded to it
    unchanged -- see that function's docstring for what it excludes and why.

    The message states the free-edge count plainly, with no hedge about what might account for it.
    An earlier version speculated in the message itself ("a ground plane ... commonly accounts for
    this") rather than giving the caller a way to state the soup's precondition -- exactly the kind
    of warning that ships with its own excuse and becomes "noise users learn to ignore"
    (docs/plan.md's Phase 2 risk section). `docs/tool-surface.md`'s `geometry(role=...)` argument and
    ruling R9's `InputRecord.role` (landing in this phase) mean a caller can already name the interior
    soup before reaching this function; a nonzero count here means either a genuinely unenclosed room
    or a soup nobody filtered, and the number is the finding either way.
    """
    count = free_edge_count(polygons, ground_modifiers=ground_modifiers)
    if count == 0:
        return None
    return ArtifactWarning(
        code="NOT_ENCLOSED",
        severity="warn",
        message=(
            f"{count} free edge(s) in this polygon soup have no matching surface -- the soup does "
            "not bound a closed volume. An interior illuminance result from an unenclosed room is "
            "meaningless (docs/tool-surface.md). Pass ground_modifiers to exclude a ground plane or "
            "other non-interior context before this check runs, if the soup includes one."
        ),
    )


# --------------------------------------------------------------------------------------------
# Unit conversion
# --------------------------------------------------------------------------------------------


def metres_per_unit(units: Units) -> float:
    """Metres per one of `units` -- the same table `convert_to_metres` scales by.

    Exposed because Phase 10 does the scaling with `xform -s <factor>` rather than in Python: the
    stored geometry is what `obj2rad` and `xform` produced, so the factor has to leave this module
    as a number instead of being applied to a soup here. One table, two readers.
    """
    return _METRES_PER_UNIT[units]


def convert_to_metres(
    polygons: Sequence[Polygon], units: Units
) -> tuple[tuple[Polygon, ...], ArtifactWarning | None]:
    """Scale every vertex in the soup to metres, per `units`. `GEOMETRY_UNITS_CONVERTED` (info)
    states the factor when `units != "m"`; `units == "m"` returns the soup unchanged and no advisory.
    """
    factor = _METRES_PER_UNIT[units]
    if units == "m":
        return tuple(polygons), None
    converted = tuple(
        Polygon(
            id=p.id,
            modifier=p.modifier,
            vertices=tuple((x * factor, y * factor, z * factor) for x, y, z in p.vertices),
        )
        for p in polygons
    )
    warning = ArtifactWarning(
        code="GEOMETRY_UNITS_CONVERTED",
        severity="info",
        message=f"scaled by {factor:g} ({units} -> m) on import.",
    )
    return converted, warning


# --------------------------------------------------------------------------------------------
# The oconv -r maxres ratio
# --------------------------------------------------------------------------------------------


def _smallest_edge_length(polygons: Sequence[Polygon]) -> float:
    degenerate = degenerate_polygon_ids(polygons)
    lengths = [
        ((x1 - x2) ** 2 + (y1 - y2) ** 2 + (z1 - z2) ** 2) ** 0.5
        for p in polygons
        if p.id not in degenerate
        for (x1, y1, z1), (x2, y2, z2) in zip(p.vertices, p.vertices[1:] + p.vertices[:1])
    ]
    lengths = [length for length in lengths if length > _MIN_EDGE_LENGTH_M]
    if not lengths:
        raise GeometryError(
            "no edge at or above the tessellation-noise floor "
            f"({_MIN_EDGE_LENGTH_M * 1000:g} mm) survives -- every non-degenerate polygon's edges "
            "are all noise-scale (see degenerate_polygon_ids and _MIN_EDGE_LENGTH_M)."
        )
    return min(lengths)


def octree_resolution_ratio(polygons: Sequence[Polygon]) -> float:
    """The ratio `oconv -r maxres` should be greater than or equal to: the largest bounding-box
    dimension divided by the smallest non-degenerate polygon edge (docs/tool-surface.md's
    `OCTREE_RESOLUTION_LOW` row).

    "Non-degenerate polygon edge" is read here as two filters, not one: an edge belonging to a
    degenerate polygon (`degenerate_polygon_ids`) is excluded, **and** an edge shorter than
    `_MIN_EDGE_LENGTH_M` is excluded even when its own polygon is otherwise real -- a tessellated
    curved surface can have genuine area and still carry a curve-fitting sliver edge three orders of
    magnitude smaller than its neighbours (see that constant's docstring for the real fixture that
    forced this reading). Neither filter alone is sufficient: the polygon-level filter cannot see a
    sliver edge inside an otherwise-real polygon, and a bare length floor with no polygon-level check
    would not exclude a polygon that is *entirely* degenerate.

    Returned as a plain ratio -- `rad_engine` compares it against whichever `-r` it plans to use
    (`OCONV_DEFAULT_MAXRES` unless raised) and decides whether to raise `-r` or emit the advisory;
    this module does not build that decision or an `oconv` command string.
    """
    box = bounding_box(polygons)
    largest = max(box.size_x, box.size_y, box.size_z)
    return largest / _smallest_edge_length(polygons)
