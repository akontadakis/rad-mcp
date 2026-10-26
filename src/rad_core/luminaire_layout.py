"""Luminaire placement arithmetic: the hcl_mcp `rot` remap, `!xform` lines, instance positions, CAD islands.

Pure (CONTEXT.md). Spec: docs/superpowers/specs/2026-09-13-phase-11a-luminaire-placement-design.md
§5.2, §6, rulings R462, R475, R476, R489, R492, R497, R498, R502, R511, R512, R741, R742.

**R462, recorded where it acts.** `rot` is hcl_mcp's Y-up Euler triple, remapped `rotX = rx, rotY = rz,
rotZ = -ry` whatever the map's up axis. Spec §1.6 measured that the remap mirrors single-axis Y and Z
rotations against this repository's Y-up -> Z-up convention; the owner accepted that to keep one map
file meaning the same lamps in both tools. This module copies the remap and does not correct it.
"""

from __future__ import annotations

import itertools
import math
from dataclasses import dataclass
from typing import Any, Final, Literal, Sequence

from rad_core.geometry import Point3, area_vector
from rad_core.photometry_file import FLAT_SHAPES

__all__ = [
    "AREA_TIE_RATIO",
    "EXTENT_CHECK_MIN_FOOTPRINT_M",
    "FLAT_TOLERANCE_M",
    "OVERLAP_TOLERANCE_M",
    "PLACEMENT_MAX_INSTANCES",
    "ROT_HINT_ASPECT",
    "RUN_MAX_INSTANCES",
    "SEAT_RULES",
    "SPANS_SEVERAL_RATIO",
    "WELD_TOLERANCE_M",
    "GuardFinding",
    "Island",
    "LayoutError",
    "Rot",
    "SeatChange",
    "array_line",
    "cad_position",
    "guard_islands",
    "hand_line",
    "instance_positions",
    "is_compound",
    "island_seat",
    "islands",
    "marker_seat",
    "near_field_count",
    "rotate",
    "rotated_extent",
    "rotation_flags",
    "rotation_matrix",
    "seat_change",
    "EmitterInstance",
    "refuse_intersecting_emitters",
]

Rot = tuple[float, float, float]
Matrix = tuple[Point3, Point3, Point3]

#: R489. Unsourced (docs/tool-surface.md `## Unsourced values`).
PLACEMENT_MAX_INSTANCES: Final[int] = 400
RUN_MAX_INSTANCES: Final[int] = 2000


class LayoutError(ValueError):
    def __init__(self, message: str, *, code: str, field: str, severity: str = "error") -> None:
        super().__init__(message)
        self.code = code
        self.field = field
        self.severity = severity


def _fmt(value: float) -> str:
    return format(value + 0.0, ".10g")


def rotation_flags(rot: Rot | None) -> list[str]:
    if rot is None:
        return []
    rx, ry, rz = rot
    return ["-rx", _fmt(rx), "-ry", _fmt(rz), "-rz", _fmt(-ry)]


def _axis(axis: int, degrees: float) -> Matrix:
    c, s = math.cos(math.radians(degrees)), math.sin(math.radians(degrees))
    if axis == 0:
        return ((1.0, 0.0, 0.0), (0.0, c, -s), (0.0, s, c))
    if axis == 1:
        return ((c, 0.0, s), (0.0, 1.0, 0.0), (-s, 0.0, c))
    return ((c, -s, 0.0), (s, c, 0.0), (0.0, 0.0, 1.0))


def _multiply(a: Matrix, b: Matrix) -> Matrix:
    return tuple(tuple(sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)) for i in range(3))  # type: ignore[return-value]


def rotation_matrix(rot: Rot | None) -> Matrix:
    """The matrix `xform` applies for `rotation_flags(rot)`: `-rx`, then `-ry`, then `-rz`."""
    if rot is None:
        return ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))
    rx, ry, rz = rot
    return _multiply(_axis(2, -ry), _multiply(_axis(1, rz), _axis(0, rx)))


def rotate(matrix: Matrix, point: Point3) -> Point3:
    return tuple(sum(matrix[i][k] * point[k] for k in range(3)) for i in range(3))  # type: ignore[return-value]


def is_compound(rot: Rot | None) -> bool:
    return rot is not None and sum(1 for v in rot if v != 0) > 1


def hand_line(type_rad: str, position: Point3, rot: Rot | None) -> str:
    return " ".join(["!xform", *rotation_flags(rot), "-t", *(_fmt(v) for v in position), type_rad])


def array_line(type_rad: str, position: Point3, rot: Rot | None, *, count_x: int, spacing_x: float, count_y: int, spacing_y: float) -> str:
    """Rotation, the two array steps, `-i 1`, then the origin (spec §1.4: without `-i 1` the origin joins the last step)."""
    return " ".join([
        "!xform", *rotation_flags(rot),
        "-a", str(count_x), "-t", _fmt(spacing_x), "0", "0",
        "-a", str(count_y), "-t", "0", _fmt(spacing_y), "0",
        "-i", "1", "-t", *(_fmt(v) for v in position), type_rad,
    ])


def instance_positions(position: Point3, *, count_x: int = 1, spacing_x: float = 0.0, count_y: int = 1, spacing_y: float = 0.0) -> list[Point3]:
    if count_x * count_y > PLACEMENT_MAX_INSTANCES:
        raise LayoutError(
            f"{count_x} x {count_y} = {count_x * count_y} instances exceeds the {PLACEMENT_MAX_INSTANCES}-instance bound on one placement (R489).",
            code="ARGUMENT_OUT_OF_RANGE", severity="error", field="array",
        )
    x, y, z = position
    return [(x + i * spacing_x, y + j * spacing_y, z) for i in range(count_x) for j in range(count_y)]


def rotated_extent(emitter_min: Point3, emitter_max: Point3, rot: Rot | None) -> tuple[Point3, Point3]:
    """The axis-aligned extent of the emitter's box after `rot`."""
    matrix = rotation_matrix(rot)
    corners = [rotate(matrix, (x, y, z)) for x in (emitter_min[0], emitter_max[0]) for y in (emitter_min[1], emitter_max[1]) for z in (emitter_min[2], emitter_max[2])]
    return (
        tuple(min(c[i] for c in corners) for i in range(3)),  # type: ignore[return-value]
        tuple(max(c[i] for c in corners) for i in range(3)),  # type: ignore[return-value]
    )


#: The axis names a refusal prints, in the order `Point3` stores them.
_AXIS_NAMES: Final[tuple[str, str, str]] = ("x", "y", "z")


@dataclass(frozen=True)
class EmitterInstance:
    """One placed instance's luminous opening, as the overlap check sees it (R673, R675).

    `emitter_min`/`emitter_max` are the type's **unrotated** extent from
    `rad_core.photometry_file.ConvertedType`; `rot` is the placement's rotation and `position` its
    origin. The box is carried in this form rather than as a world-aligned extent because the
    axis-aligned bounding box of a rotated emitter is **not** the emitter -- a 0.312 x 1.55 m panel
    turned 45 degrees has a 1.3166 m bounding span -- and refusing on the bounding box refuses
    layouts that are provably disjoint (R675).
    """

    placement: str
    index: int
    position: Point3
    rot: Rot | None
    emitter_min: Point3
    emitter_max: Point3

    @property
    def half_extents(self) -> Point3:
        return tuple((b - a) / 2 for a, b in zip(self.emitter_min, self.emitter_max))  # type: ignore[return-value]

    @property
    def axes(self) -> tuple[Point3, Point3, Point3]:
        """The box's own three axes in world space: the COLUMNS of its rotation matrix, since
        `rotate(m, p)` is `m @ p` and the image of local unit vector `e_i` is column `i`."""
        m = rotation_matrix(self.rot)
        return tuple(tuple(m[row][col] for row in range(3)) for col in range(3))  # type: ignore[return-value]

    @property
    def centre(self) -> Point3:
        local = tuple((a + b) / 2 for a, b in zip(self.emitter_min, self.emitter_max))
        turned = rotate(rotation_matrix(self.rot), local)  # type: ignore[arg-type]
        return tuple(p + t for p, t in zip(self.position, turned))  # type: ignore[return-value]

    @property
    def world_extent(self) -> tuple[Point3, Point3]:
        """The axis-aligned bounding box, used ONLY as the cheap first filter."""
        low, high = rotated_extent(self.emitter_min, self.emitter_max, self.rot)
        return (tuple(p + v for p, v in zip(self.position, low)),  # type: ignore[return-value]
                tuple(p + v for p, v in zip(self.position, high)))  # type: ignore[return-value]


def _dot(a: Point3, b: Point3) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a: Point3, b: Point3) -> Point3:
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _separating_axes(a: EmitterInstance, b: EmitterInstance) -> list[Point3]:
    """The 15 candidate separating axes for two oriented boxes: each box's own three, and the nine
    cross products of one box's axes with the other's.

    A cross product of two parallel axes is the zero vector and is dropped -- it is not an axis at
    all. The exact `!= 0` test is right here because these vectors come from orthonormal rotation
    matrices: parallel axes give a bit-exact zero, and anything else is a real direction.

    **The set is over-complete, and deliberately left so.** Two boxes sharing a rotation lose only
    the three self-crosses; the other six cross products are each a frame's own third axis rebuilt
    from its first two, so the list is twelve rather than six. Measured 2026-09-21: dropping each
    box's third *own* axis changes no verdict in the suite, because those crosses put it straight
    back. That redundancy is a property of the algorithm, not slack in the tests -- a mutation that
    genuinely removes an axis from the decision, such as ignoring a box's third half-extent in
    `_projection`, is caught by
    `tests/test_luminaire_layout.py::test_two_instances_at_one_plan_point_overlapping_in_z_are_refused`.
    """
    axes = [*a.axes, *b.axes]
    for u in a.axes:
        for v in b.axes:
            c = _cross(u, v)
            if c != (0.0, 0.0, 0.0):
                axes.append(c)
    return axes


def _projection(box: EmitterInstance, axis: Point3) -> tuple[float, float, float]:
    """`(centre, radius, is_flat)` of one box projected on `axis` -- `radius` is the half-width of
    the shadow it casts, and `is_flat` says the shadow is a single point."""
    half = box.half_extents
    radius = sum(abs(_dot(u, axis)) * h for u, h in zip(box.axes, half))
    return _dot(box.centre, axis), radius, radius == 0.0


def emitters_intersect(a: EmitterInstance, b: EmitterInstance) -> bool:
    """Whether two placed emitters occupy common space (R673).

    **Two stages.** The axis-aligned bounding boxes are compared first, because that is cheap and
    two luminaires in different parts of a room fail it immediately. Only a pair that survives is
    put through the exact separating-axis test on the boxes' own orientations. The filter can only
    ever say "maybe"; it never decides a refusal on its own, which is the whole point -- a rotated
    emitter's bounding box is much larger than the emitter (R675).

    **The rule on each axis.** With `overlap` the length the two shadows share, the axis leaves the
    boxes apart when `overlap < 0`; it is a proper overlap when `overlap > 0`; and `overlap == 0`
    is a touch. A touch does **not** count as an intersection, so two fittings butted end to end
    are legal -- **except** where either box's shadow on that axis is a single point, which is what
    a flat luminous opening casts along its own normal. There, coincidence is the only kind of
    overlap available, and two coincident flat emitters are one luminous surface counted twice.

    `ies2rad` gives a flat opening an exactly zero extent, so "a single point" is an exact test and
    needs no tolerance: this is the whole of R673's criterion, measured off the emitter, with no
    chosen distance anywhere in it.
    """
    (a_low, a_high), (b_low, b_high) = a.world_extent, b.world_extent
    for i in range(3):
        if max(a_low[i], b_low[i]) > min(a_high[i], b_high[i]):
            return False
    for axis in _separating_axes(a, b):
        a_centre, a_radius, a_flat = _projection(a, axis)
        b_centre, b_radius, b_flat = _projection(b, axis)
        overlap = (a_radius + b_radius) - abs(a_centre - b_centre)
        if overlap < 0.0 or (overlap == 0.0 and not (a_flat or b_flat)):
            return False
    return True


def refuse_intersecting_emitters(instances: Sequence[EmitterInstance], *, field: str) -> None:
    """R673: refuse the first pair of placed emitters that intersect.

    The sequence is every instance in the project, not one placement's, because `rad_core.ugr`'s
    `E_direct` sums over source rows and does not care which `place` call produced a row (R675).

    `field` is the caller's own argument to change and is supplied by the route rather than fixed
    here: an array whose own spacing is too tight is fixed through `array`, a hand placement that
    lands on an existing one through `position`, and a CAD placement through `geometry` -- which
    carries no `position` argument at all, so a hardcoded `"position"` would name an argument that
    call never had.

    Raises `LayoutError` with `LUMINAIRE_EMITTER_BOXES_INTERSECT`, naming both instances, each
    world axis's centre separation against both emitters' own extents, and the two recoveries. The
    separation quoted is along the world axes, which is what a caller types as `position` and
    `array`; the *verdict* above is taken on the boxes' own axes, and the message says so rather
    than implying the printed numbers are what decided it.

    **The pair loop is O(n^2) and is left that way deliberately, on a measurement.** Its worst case
    is a *legal* layout, where no pair is rejected early and every one is examined. Measured
    2026-09-21 on this machine: 400 instances (`PLACEMENT_MAX_INSTANCES`, the most ONE call may
    place) in 0.019 s and 2 000 in 0.475 s, against file writes and a project lock. **No bound caps
    a project's total instances at placement** -- `RUN_MAX_INSTANCES` is enforced in
    `rad_mcp.lit_scene`, at run time -- so a project built from many calls can exceed those counts
    and this check grows quadratically with it. That is stated rather than implied by quoting a
    ceiling this route does not enforce.
    """
    for index, a in enumerate(instances):
        for b in instances[index + 1:]:
            if not emitters_intersect(a, b):
                continue
            a_low, a_high = a.world_extent
            b_low, b_high = b.world_extent
            axes = "; ".join(
                f"{_AXIS_NAMES[i]}: centres {abs(((a_low[i] + a_high[i]) - (b_low[i] + b_high[i])) / 2):.3f} m "
                f"apart, emitter extents {a_high[i] - a_low[i]:.3f} and {b_high[i] - b_low[i]:.3f} m"
                for i in range(3)
            )
            # Two records carrying one recorded `name` (a hand-edited manifest) would read
            # "'desk' instance 0 and 'desk' instance 0" (final review of 2026-10-05).
            twins = (
                f" (two placements are both named {a.placement!r}: the manifest was edited by hand)"
                if (a.placement, a.index) == (b.placement, b.index) else ""
            )
            raise LayoutError(
                f"{a.placement!r} instance {a.index} and {b.placement!r} instance {b.index}{twins} have "
                f"emitter boxes that intersect ({axes}; measured on the emitters' own axes, not "
                "these world ones). Two luminous openings cannot occupy one volume, and a rating "
                "built over them counts the same emitter's light once per overlapping instance -- "
                "R672 measured indirect_illuminance_lux = -15186.145183 lux for four such "
                "instances. Move one placement until the two emitters clear each other, or declare "
                "a single luminaire type for the fitting.",
                code="LUMINAIRE_EMITTER_BOXES_INTERSECT", field=field, severity="error",
            )


# --------------------------------------------------------------------------------------------
# CAD islands, the overlap/spans-several guard, positions by shape class, near field (Task 5)
# --------------------------------------------------------------------------------------------

#: R475/R476. Unsourced design values (docs/tool-surface.md `## Unsourced values`).
WELD_TOLERANCE_M: Final[float] = 1e-6
FLAT_TOLERANCE_M: Final[float] = 1e-6
#: N-116. Unsourced design value (docs/tool-surface.md `## Unsourced values`): two face areas within this
#: fraction of the largest tie for it.
AREA_TIE_RATIO: Final[float] = 1e-9
OVERLAP_TOLERANCE_M: Final[float] = 1e-3
SPANS_SEVERAL_RATIO: Final[float] = 1.9
EXTENT_CHECK_MIN_FOOTPRINT_M: Final[float] = 0.05
ROT_HINT_ASPECT: Final[float] = 1.5


@dataclass(frozen=True)
class Island:
    """One welded island of an OBJ luminaire row's faces -- the vertices `islands` leaves after
    welding coordinates within `WELD_TOLERANCE_M` of each other, inside one OBJ object (R475).

    `faces` keeps each welded face as its own polygon loop (R511): `vertices` is an unordered set of
    welded cluster representatives, in first-seen order, and carries no loop a normal can be computed
    from -- a plane fitted to it (the pre-R511 reading) is a plane fitted to a *set*, and whether that
    set of points reads as flat can depend on the order the faces happened to be welded in. `faces` is
    what `is_flat` reads a real plane from instead.

    **Equality and hash include `faces`**, because this is a frozen dataclass; no caller compares or
    hashes islands, so nothing depends on it.
    """

    vertices: tuple[Point3, ...]
    faces: tuple[tuple[Point3, ...], ...]

    @property
    def low(self) -> Point3:
        return tuple(min(v[i] for v in self.vertices) for i in range(3))  # type: ignore[return-value]

    @property
    def high(self) -> Point3:
        return tuple(max(v[i] for v in self.vertices) for i in range(3))  # type: ignore[return-value]

    @property
    def centroid(self) -> Point3:
        """The area-weighted centroid of the island's faces (L-68), so an extra collinear vertex (a
        T-junction) does not move a flat seat. Each face is fanned from its first vertex into
        triangles weighted by their signed area along the face's own normal, so a non-convex face
        weighs correctly. The vertex mean when every face is degenerate (zero area). Note that the
        centroid of a non-convex island can fall outside it."""
        total = 0.0
        acc = [0.0, 0.0, 0.0]
        for face in self.faces:
            normal = area_vector(face)
            size = math.sqrt(sum(c * c for c in normal))
            if size == 0.0:
                continue
            unit = tuple(c / size for c in normal)
            a = face[0]
            for b, c in itertools.pairwise(face[1:]):
                cross = (
                    (b[1] - a[1]) * (c[2] - a[2]) - (b[2] - a[2]) * (c[1] - a[1]),
                    (b[2] - a[2]) * (c[0] - a[0]) - (b[0] - a[0]) * (c[2] - a[2]),
                    (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0]),
                )
                weight = sum(u * x for u, x in zip(unit, cross))
                total += weight
                for i in range(3):
                    acc[i] += weight * (a[i] + b[i] + c[i]) / 3
        if total <= 0.0:
            n = len(self.vertices)
            return tuple(sum(v[i] for v in self.vertices) / n for i in range(3))  # type: ignore[return-value]
        return tuple(x / total for x in acc)  # type: ignore[return-value]

    @property
    def is_flat(self) -> bool:
        """True when every vertex lies within `FLAT_TOLERANCE_M` of the plane of the island's own
        largest-area face (R511) -- chosen by area rather than by first-in-loop order, so the answer
        does not depend on which order `islands` happened to weld this island's faces in. When several
        faces tie for largest (N-116) the vertices must lie within `FLAT_TOLERANCE_M` of every tied plane. If every
        face is degenerate (zero area), the plane is unreadable and the degenerate answer is `True`,
        matching this property's behaviour before R511.
        """
        areas = [math.sqrt(sum(c * c for c in area_vector(face))) for face in self.faces]
        best_area = max(areas, default=0.0)
        if best_area == 0.0:
            return True
        # N-116: faces whose areas tie with the largest (to `AREA_TIE_RATIO`, which absorbs rounding)
        # all name a plane, and the answer must not depend on which of them the welding listed first:
        # the island is flat only if every one of those planes holds all its vertices. An island with
        # one largest face has one such plane, the one this property always read.
        for face, area in zip(self.faces, areas):
            if area < best_area * (1.0 - AREA_TIE_RATIO):
                continue
            unit = tuple(c / area for c in area_vector(face))
            origin = tuple(sum(v[i] for v in face) / len(face) for i in range(3))
            if not all(
                abs(sum(u * (v[i] - origin[i]) for i, u in enumerate(unit))) <= FLAT_TOLERANCE_M
                for v in self.vertices
            ):
                return False
        return True


def _cell(point: Point3) -> tuple[int, int, int]:
    return tuple(math.floor(c / WELD_TOLERANCE_M) for c in point)  # type: ignore[return-value]


def _distance(a: Point3, b: Point3) -> float:
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)))


def islands(faces: Sequence[tuple[str | None, Sequence[Point3]]]) -> tuple[Island, ...]:
    """Faces joined into connected islands, welding coordinates within `WELD_TOLERANCE_M` of each
    other **by Euclidean distance** -- not by rounding each coordinate into a bin, which can split
    two points a nanometre apart that straddle a bin edge (R502) -- **inside one OBJ object only**
    (R475, Amendment 10): two separately drawn fittings butted end to end share edge coordinates, and
    welding across objects would merge them into one lamp.

    A spatial hash keyed on `(object name, floor(c / WELD_TOLERANCE_M) per axis)` finds the points a
    new one might weld to, checking the 27 neighbouring cells so a pair straddling a cell boundary is
    still compared. One union-find welds points; a second welds faces, whenever two faces both touch
    a shared welded cluster. Each island's `vertices` are one representative point per welded cluster
    its faces touch, and its `faces` are its own member faces' loops, each substituted with welded
    representatives but otherwise kept as a proper polygon loop (R511) -- `Island.is_flat` needs one
    to compute a plane from.

    **Welding is transitive**: a chain of vertices each within `WELD_TOLERANCE_M` of the next joins
    across more than the tolerance (a 0.9 micrometre chain welded across 17 micrometres); the drift is
    micrometres against millimetre tolerances. **A face with no vertices contributes nothing and is
    skipped**: it carries no geometry to place.
    """
    points: list[Point3] = []
    point_face: list[int] = []
    point_parent: list[int] = []
    grid: dict[tuple[str | None, tuple[int, int, int]], list[int]] = {}

    def point_find(i: int) -> int:
        while point_parent[i] != i:
            point_parent[i] = point_parent[point_parent[i]]
            i = point_parent[i]
        return i

    for face_index, (name, face) in enumerate(faces):
        for point in face:
            this_index = len(points)
            points.append(point)
            point_face.append(face_index)
            point_parent.append(this_index)
            cx, cy, cz = _cell(point)
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    for dz in (-1, 0, 1):
                        for other_index in grid.get((name, (cx + dx, cy + dy, cz + dz)), ()):
                            if _distance(point, points[other_index]) <= WELD_TOLERANCE_M:
                                ri, rj = point_find(this_index), point_find(other_index)
                                if ri != rj:
                                    point_parent[ri] = rj
            grid.setdefault((name, (cx, cy, cz)), []).append(this_index)

    face_parent = list(range(len(faces)))

    def face_find(i: int) -> int:
        while face_parent[i] != i:
            face_parent[i] = face_parent[face_parent[i]]
            i = face_parent[i]
        return i

    cluster_first_face: dict[int, int] = {}
    cluster_representative: dict[int, Point3] = {}
    for point_index, point in enumerate(points):
        root = point_find(point_index)
        cluster_representative.setdefault(root, point)
        face_index = point_face[point_index]
        if root in cluster_first_face:
            ri, rj = face_find(face_index), face_find(cluster_first_face[root])
            if ri != rj:
                face_parent[ri] = rj
        else:
            cluster_first_face[root] = face_index

    # Each face's own loop, substituted with its points' welded representatives, in the face's own
    # vertex order (R511) -- `Island.is_flat` reads a plane off one of these, so the substitution has
    # to preserve the loop rather than collapsing it into an unordered set of points.
    substituted_faces: list[tuple[Point3, ...]] = []
    cursor = 0
    for face in faces:
        loop = [cluster_representative[point_find(cursor + offset)] for offset in range(len(face[1]))]
        substituted_faces.append(tuple(loop))
        cursor += len(face[1])

    groups_vertices: dict[int, dict[int, Point3]] = {}
    groups_faces: dict[int, list[tuple[Point3, ...]]] = {}
    for point_index, point in enumerate(points):
        group_root = face_find(point_face[point_index])
        vertex_group = groups_vertices.setdefault(group_root, {})
        root = point_find(point_index)
        vertex_group[root] = cluster_representative[root]
    for face_index in range(len(faces)):
        group_root = face_find(face_index)
        groups_faces.setdefault(group_root, []).append(substituted_faces[face_index])

    return tuple(
        Island(tuple(groups_vertices[root].values()), tuple(groups_faces[root]))
        for root in sorted(groups_vertices)
    )


@dataclass(frozen=True)
class GuardFinding:
    code: str
    severity: str
    message: str


def guard_islands(
    found: Sequence[Island], *, row: str, emitter_min: Point3, emitter_max: Point3, rot: Rot | None
) -> list[GuardFinding]:
    """The CAD placement guard (R475/R476): refuses two unwelded islands of one row that overlap in
    plan, and a single island whose footprint is several fittings' worth of the rotated luminous
    opening. Otherwise reports one `info` finding when the opening is too small in both axes to
    compare a drawn extent against at all."""
    for a_index, a in enumerate(found):
        for b in found[a_index + 1:]:
            px = min(a.high[0], b.high[0]) - max(a.low[0], b.low[0])
            py = min(a.high[1], b.high[1]) - max(a.low[1], b.low[1])
            if px > OVERLAP_TOLERANCE_M and py > OVERLAP_TOLERANCE_M:
                raise LayoutError(
                    f"row {row!r}: two separately drawn pieces overlap by {px * 1000:.1f} x "
                    f"{py * 1000:.1f} mm -- one fitting drawn as unwelded parts would become several "
                    "lamps. Weld the fitting or place it by hand.",
                    code="LUMINAIRE_ISLANDS_OVERLAP", field="geometry", severity="error",
                )
    low, high = rotated_extent(emitter_min, emitter_max, rot)
    fx, fy = high[0] - low[0], high[1] - low[1]
    if fx < EXTENT_CHECK_MIN_FOOTPRINT_M and fy < EXTENT_CHECK_MIN_FOOTPRINT_M:
        # Keywords, not positions: tests/test_advisories.py::emitted_codes reads `code=`/`severity=`.
        return [GuardFinding(
            code="LUMINAIRE_ISLAND_EXTENT_UNCHECKED", severity="info",
            message=f"row {row!r}: the luminous opening is {fx:.3f} x {fy:.3f} m, under "
                    f"{EXTENT_CHECK_MIN_FOOTPRINT_M} m, so the drawn extent was not compared with it.",
        )]
    for island in found:
        ix, iy = island.high[0] - island.low[0], island.high[1] - island.low[1]
        # Per axis, not longest-against-longest: a fitting drawn across its rot has the same long
        # dimension as its opening, and only the axis-by-axis comparison sees it (Amendment 10).
        over_x = fx > 0 and ix >= SPANS_SEVERAL_RATIO * fx
        over_y = fy > 0 and iy >= SPANS_SEVERAL_RATIO * fy
        if over_x or over_y:
            # R498: the tests assert on the hint's own exact phrase (present/absent), not on the bare
            # substring "rot" -- so the base sentence is free to say "rotated" here.
            hint = ""
            aspect = max(fx, fy) / max(min(fx, fy), 1e-12)
            if aspect >= ROT_HINT_ASPECT and (ix >= iy) != (fx >= fy):
                hint = (
                    " The drawn long axis crosses the rotated luminous opening's short axis, so the "
                    "row's rot is the likely cause."
                )
            raise LayoutError(
                f"row {row!r}: a drawn piece is {ix:.3f} x {iy:.3f} m against a {fx:.3f} x {fy:.3f} m "
                f"rotated luminous opening (ratio >= {SPANS_SEVERAL_RATIO} on an axis), so it looks "
                f"like several fittings welded into one.{hint}",
                code="LUMINAIRE_ISLAND_SPANS_SEVERAL", field="geometry", severity="error",
            )
    return []


def cad_position(
    island: Island, *, shape_class: str, emitter_min: Point3, emitter_max: Point3, rot: Rot | None
) -> tuple[Point3, str | None]:
    """The placement translation for one island (R476). The second value is an advisory code or
    `None`: `LUMINAIRE_OPENING_ASSUMED_BOTTOM` when a flat-shape type is seated at a solid island's
    lowest point, because nothing in the drawing says which face is the luminous opening."""
    centre = tuple((a + b) / 2 for a, b in zip(emitter_min, emitter_max))
    rc = rotate(rotation_matrix(rot), centre)  # type: ignore[arg-type]
    flat_type = shape_class in FLAT_SHAPES
    if island.is_flat:
        if not flat_type:
            raise LayoutError(
                f"a {shape_class} type has luminous height and cannot be seated on a flat drawn face; "
                "draw the fitting as a solid or place it by hand.",
                code="LUMINAIRE_HEIGHT_NEEDS_SOLID", field="geometry", severity="error",
            )
        c = island.centroid
        return (c[0] - rc[0], c[1] - rc[1], c[2] - rc[2]), None
    low, high = island.low, island.high
    if flat_type:
        return (
            (low[0] + high[0]) / 2 - rc[0], (low[1] + high[1]) / 2 - rc[1], low[2] - rc[2],
        ), "LUMINAIRE_OPENING_ASSUMED_BOTTOM"
    return tuple((a + b) / 2 - r for a, b, r in zip(low, high, rc)), None  # type: ignore[return-value]


#: How a `geometry`-route instance was seated (R742): at a `p` marker, on a flat drawn face, or in a
#: solid drawn island. `place` records one per instance; a re-import reads it (R741).
SEAT_RULES: Final[tuple[str, str, str]] = ("marker", "flat_face", "solid")


def marker_seat() -> dict[str, Any]:
    """A `p` marker's seat: the marker minus the rotated emitter centre, whatever the shape class."""
    return {"rule": "marker"}


def island_seat(island: Island) -> dict[str, Any]:
    """The seat `cad_position` takes for this island. A flat island seats at its centroid, and only
    a flat type (R476); a solid island at its plan centre and, by the type's class, its lowest z or
    its middle -- so its z extent is recorded, and nothing else is needed to recompute either."""
    if island.is_flat:
        return {"rule": "flat_face"}
    return {"rule": "solid", "island_z": [island.low[2], island.high[2]]}


@dataclass(frozen=True)
class SeatChange:
    """Why a re-import would move one instance's seat (R741). `offset` is a fresh `place` of the new
    type minus the position kept, in world metres after `rot`; `None` where no legal seat exists, or
    where the placement records no seat and the type changes between flat and non-flat
    (`FLAT_SHAPES`)."""

    reason: Literal["moves", "no legal seat", "not recorded"]
    offset: Point3 | None


def _centre(low: Point3, high: Point3) -> Point3:
    return tuple((a + b) / 2 for a, b in zip(low, high))  # type: ignore[return-value]


def _finite_real(value: Any) -> bool:
    """A finite `int` or `float` that is not a `bool`. An `int` past ~1.8e308 is not one:
    `math.isfinite` raises `OverflowError` on it rather than answering (as in
    `rad_engine.export._recorded_real`, R737)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _valid_seat(seat: Any) -> dict[str, Any] | None:
    """A recorded seat, or `None` when it is absent or malformed -- a hand edit is read as "not
    recorded", never raised."""
    if not isinstance(seat, dict) or seat.get("rule") not in SEAT_RULES:
        return None
    if seat["rule"] != "solid":
        return seat
    z = seat.get("island_z")
    if isinstance(z, list) and len(z) == 2 and all(_finite_real(v) for v in z) and z[0] < z[1]:
        return seat
    return None


def seat_change(seat: Any, *, rot: Rot | None, old_shape: str, old_min: Point3, old_max: Point3,
                new_shape: str, new_min: Point3, new_max: Point3) -> SeatChange | None:
    """Whether replacing the old emitter with the new one moves this instance's seat (R741), or
    `None` when it does not. **Exact:** no tolerance anywhere (M20: the same file re-imported gives
    a `repr`-identical record).

    The offset is `(seat_new - seat_old) + (rc_old - rc_new)`, with `rc` the emitter centre turned
    by `rot`, which is `cad_position`'s arithmetic taken as a difference. Where the seat rule is the
    same for both types, `seat_new == seat_old` and the offset is the centre change alone; so a
    placement with no recorded seat is judged exactly unless the flat/non-flat class changes, which
    is the one case its missing seat leaves open (design section 5's rule)."""
    matrix = rotation_matrix(rot)
    rc_old, rc_new = rotate(matrix, _centre(old_min, old_max)), rotate(matrix, _centre(new_min, new_max))
    centre_offset: Point3 = tuple(o - n for o, n in zip(rc_old, rc_new))  # type: ignore[assignment]
    old_flat, new_flat = old_shape in FLAT_SHAPES, new_shape in FLAT_SHAPES
    known = _valid_seat(seat)
    if known is None and old_flat != new_flat:
        return SeatChange("not recorded", None)
    if known is not None and known["rule"] == "flat_face" and not new_flat:
        return SeatChange("no legal seat", None)
    if known is not None and known["rule"] == "solid" and old_flat != new_flat:
        low_z, high_z = known["island_z"]
        middle = (low_z + high_z) / 2
        rise = (low_z if new_flat else middle) - (low_z if old_flat else middle)
        return SeatChange("moves", (centre_offset[0], centre_offset[1], rise + centre_offset[2]))
    return None if rc_old == rc_new else SeatChange("moves", centre_offset)


def near_field_count(
    sensors: Sequence[Point3], instances: Sequence[tuple[Point3, Point3, Point3, float]]
) -> int:
    """Sensors closer to some instance's world-space rotated emitter box than that instance's own
    unrotated horizontal footprint (R492/R512).

    Each instance is `(position, rotated_min, rotated_max, footprint_m)`: `rotated_min`/`rotated_max`
    are the emitter box's extent after `rot`, local to `position` (as `rotated_extent` returns), and
    `footprint_m` is the *unrotated* emitter box's own `max(x extent, y extent)` -- the type's
    horizontal footprint, which a rotation does not change. R512 corrects an earlier reading that
    measured the near-field radius off the *rotated* 3-D box's own longest edge instead: that value
    shrinks under a compound rotation (measured on the office `2722` type at `rot=(0, 45, 0)`: 1.317 m
    against the type's true 1.55 m footprint) and can be far larger than the footprint for a tall,
    narrow box (a 0.1 x 0.1 x 0.8 m box's longest edge is its own 0.8 m height, not its 0.1 m
    footprint) -- both silently wrong bounds on a quantity that should not depend on rotation at all.
    The distance itself is still measured to the world-space rotated box; only the threshold changed.
    """
    count = 0
    for sensor in sensors:
        for position, low, high, footprint_m in instances:
            world_low = tuple(p + l for p, l in zip(position, low))
            world_high = tuple(p + h for p, h in zip(position, high))
            distance = math.sqrt(
                sum(max(l - s, 0.0, s - h) ** 2 for s, l, h in zip(sensor, world_low, world_high))
            )
            if distance < footprint_m:
                count += 1
                break
    return count
