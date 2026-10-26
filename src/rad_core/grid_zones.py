"""EN 12464-1:2011 zoned calculation grids -- Phase 11b design §4.

Task areas are axis-aligned rectangles gathered into groups; each group has one surround (licht.de
p.8, Fig. 03). The default surround is EN's band (deck: "at least 0.5m around the task area"), and
the rest of the room less the wall strip is the background; `"rest_of_room"` is ASR A3.4's geometry
(R619). `task_layout="unknown"` is one task area, `task_whole` (licht.de a.2, R614).

Pure: no IO, no Radiance. Refusals are `GridError`s, warnings `ArtifactWarning`s, as in `rad_core.grids`.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final, Literal

from rad_core.grids import (
    MATRIX_MAX_DECLARED_POINTS,
    GridEmptyError,
    GridError,
    GridPoint,
    GridSpec,
    PolygonError,
    Standard,
    _as_points,
    _bounds,
    _ensure_cell_aspect_in_range,
    _polygon_is_simple,
    _resolve_height,
    plan_grid,
    polygon_floor_z,
)
from rad_core.manifest import ArtifactWarning, ProvenanceValue
from rad_core.planar import (
    Point,
    Rect,
    _point_in_polygon,
    _segments_intersect,
    nearest_point_on_rect,
    point_rect_distance,
    point_segment_distance,
    rect_inside_polygon,
    segment_inside_polygon,
)

__all__ = [
    "WALL_STRIP_M",
    "DEFAULT_SURROUND_WIDTH_M",
    "MIN_SURROUND_WIDTH_M",
    "BACKGROUND_MIN_WIDTH_M",
    "MAX_TASK_ZONES",
    "SurroundSpec",
    "ZoneArgumentError",
    "TaskZone",
    "validated_room",
    "groups_in_order",
    "validate_task_zones",
    "Membership",
    "formula_spacing",
    "band_narrowest",
    "Zone",
    "ZonePlan",
    "plan_zones",
    "background_narrow_message",
]

#: licht.de p.17: "A 0.5m wide strip along the walls is excluded from the calculation area."
WALL_STRIP_M: Final[float] = 0.5
#: Deck: "Immediate surround – width at least 0.5m around the task area" (R601's default band).
DEFAULT_SURROUND_WIDTH_M: Final[float] = 0.5
MIN_SURROUND_WIDTH_M: Final[float] = 0.5
#: licht.de p.8: the background "should be seen as a strip at least 3.0 m wide."
BACKGROUND_MIN_WIDTH_M: Final[float] = 3.0
#: R617 -- unsourced; bounds the O(n^2) overlap check and the per-point membership work.
MAX_TASK_ZONES: Final[int] = 64

SurroundSpec = float | Literal["rest_of_room"]


class ZoneArgumentError(GridError):
    """A zone argument refused on its face (R14): `ARGUMENT_OUT_OF_RANGE` with the argument's name."""

    code = "ARGUMENT_OUT_OF_RANGE"

    def __init__(self, message: str, *, field: str) -> None:
        super().__init__(message)
        self.field = field


@dataclass(frozen=True)
class TaskZone:
    rect: Rect
    group: str = "1"


def validated_room(polygon: Sequence[Sequence[float]]) -> list[Point]:
    """The room outline as points, refused as `rad_core.grids.place_sensor_grid` refuses it."""
    room = _as_points(polygon)
    if not _polygon_is_simple(room):
        raise PolygonError("polygon self-intersects -- it has no well-defined interior to zone.")
    x_min, x_max, y_min, y_max = _bounds(room)
    if x_max <= x_min or y_max <= y_min:
        raise PolygonError("polygon has a degenerate circumscribing rectangle -- no interior to zone.")
    return room


def _finite_number(value: object) -> bool:
    return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)


def _overlap(a: Rect, b: Rect) -> bool:
    """Open interiors overlap; touching edges or corners do not."""
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def groups_in_order(tasks: Sequence[TaskZone]) -> list[str]:
    seen: list[str] = []
    for task in tasks:
        if task.group not in seen:
            seen.append(task.group)
    return seen


def validate_task_zones(room: list[Point], tasks: Sequence[TaskZone], *, surround: SurroundSpec | None) -> None:
    """Every geometric refusal of design §4's Arguments and Containment paragraphs."""
    if not tasks:
        # Fix round 1, M3: with no task areas, `surround="rest_of_room"` indexes an empty
        # `groups_in_order(tasks)` (a bare `IndexError`) and a band silently yields nothing but a
        # background -- both are `task_layout="task_zones"` with nothing to zone, refused the same
        # way as any other argument shape this layout cannot use.
        raise ZoneArgumentError(
            "task_layout='task_zones' with no task areas has nothing to zone; pass at least one "
            "task area, or task_layout='unknown' for the whole room as one task area.",
            field="task_zones")
    if len(tasks) > MAX_TASK_ZONES:
        raise ZoneArgumentError(
            f"{len(tasks)} task areas exceed the {MAX_TASK_ZONES} one zoned grid holds (R617); "
            "split the room into two grids.", field="task_zones")
    for index, task in enumerate(tasks, start=1):
        rect = task.rect
        if len(rect) != 4 or not all(_finite_number(v) for v in rect) or not (rect[0] < rect[2] and rect[1] < rect[3]):
            raise ZoneArgumentError(
                f"task area {index} is {list(rect)!r}: a rect is four finite numbers [x0, y0, x1, y1] "
                "with x0 < x1 and y0 < y1.", field="task_zones")
        if not rect_inside_polygon(rect, room):
            raise ZoneArgumentError(
                f"task area {index} {list(rect)!r} is not inside the room: a corner is outside, or a "
                "wall runs through it. A task area may touch a wall but not cross one.", field="task_zones")
    for i, first in enumerate(tasks):
        for j in range(i + 1, len(tasks)):
            if _overlap(first.rect, tasks[j].rect):
                raise ZoneArgumentError(
                    f"task areas {i + 1} and {j + 1} overlap; task areas may touch but not overlap.",
                    field="task_zones")
    if surround == "rest_of_room":
        if len(groups_in_order(tasks)) > 1:
            raise ZoneArgumentError(
                "surround='rest_of_room' is ASR A3.4's single surround over the rest of the room, so all "
                "task areas must share one group (R619).", field="surround")
    elif surround is not None:
        if not _finite_number(surround) or surround < MIN_SURROUND_WIDTH_M:
            raise ZoneArgumentError(
                f"surround width {surround!r} is not a finite number of at least {MIN_SURROUND_WIDTH_M} m "
                "(deck: 'at least 0.5m around the task area').", field="surround")


class Membership:
    """Which zone a candidate point belongs to (design §4's zone table). `surround` is a band width,
    `"rest_of_room"`, or `None` for `task_layout="unknown"`."""

    def __init__(self, room: list[Point], tasks: Sequence[TaskZone], *, surround: SurroundSpec | None, wall_strip: bool) -> None:
        self.room = room
        self.tasks = list(tasks)
        self.surround = surround
        self.wall_strip = wall_strip
        self._edges = [(room[i], room[(i + 1) % len(room)]) for i in range(len(room))]

    def in_room(self, p: Point) -> bool:
        return _point_in_polygon(p, self.room)

    def in_strip(self, p: Point) -> bool:
        if not self.wall_strip:
            return False
        return min(point_segment_distance(p, a, b) for a, b in self._edges) < WALL_STRIP_M

    def in_task(self, p: Point, index: int) -> bool:
        x0, y0, x1, y1 = self.tasks[index].rect
        return x0 <= p[0] <= x1 and y0 <= p[1] <= y1

    def in_any_task(self, p: Point) -> bool:
        return any(self.in_task(p, i) for i in range(len(self.tasks)))

    def nearest_in_group(self, p: Point, group: str) -> tuple[float, Point]:
        """Distance to the group's nearest rectangle and the nearest point on it; ties go to the
        lowest-index rectangle -- the first encountered, in argument order -- then the smallest
        `(x, y)` (design §4, R618). Distinct task rectangles always have distinct indices, so a
        strict `d < best[0]` alone implements "lowest index wins": iterating in argument order and
        only replacing `best` on a *strict* improvement means an equal-distance later rectangle
        never displaces an earlier one (fix round 1, M1 -- `q < best[1]` used to let a smaller
        `(x, y)` win a tie even from a *later* rectangle, against the lowest-index rule)."""
        best: tuple[float, Point] | None = None
        for task in self.tasks:
            if task.group != group:
                continue
            q = nearest_point_on_rect(p, task.rect)
            d = point_rect_distance(p, task.rect)
            if best is None or d < best[0]:
                best = (d, q)
        if best is None:
            raise KeyError(group)
        return best

    def in_band(self, p: Point, group: str) -> bool:
        assert isinstance(self.surround, float | int)
        distance, nearest = self.nearest_in_group(p, group)
        return distance <= self.surround and segment_inside_polygon(p, nearest, self.room)

    def in_zone(self, p: Point, zone: str) -> bool:
        if not self.in_room(p):
            return False
        if zone.startswith("task_") and zone != "task_whole":
            return self.in_task(p, int(zone.removeprefix("task_")) - 1)
        if zone == "task_whole":
            return not self.in_strip(p)
        if self.in_any_task(p) or self.in_strip(p):
            return False
        if zone == "surround":
            return self.surround == "rest_of_room"
        if zone.startswith("surround_"):
            return self.in_band(p, zone.removeprefix("surround_"))
        if zone == "background":
            return not any(self.in_band(p, g) for g in groups_in_order(self.tasks))
        raise KeyError(zone)


#: Below this a ray's cross product with a segment's direction counts as zero: the ray runs parallel
#: to the segment (`_ray_hit`) or to a capsule's offset lines (`_segment_capsule_entry`), and has no
#: single crossing to report. `_ray_hit`'s is a cross product of a unit direction and a segment (metres);
#: `_segment_capsule_entry`'s `w1` is a dot product of a unit direction and a unit normal (dimensionless).
_PARALLEL_EPS: Final[float] = 1e-9
#: The slack on a value sitting exactly at a bound: a ray's distance and its position along a segment,
#: a point against a closed rectangle or a capsule's radius, a free width against the 0.5 m floor, a
#: spacing against its cap (and the cell count the cap implies). It makes a touch count as a touch --
#: zero free width, the cap not exceeded -- rather than a miss by one ulp. In metres everywhere except
#: two dimensionless uses: `_ray_hit`'s `u` (a fraction of a segment) and `_capped`'s `length / cap`
#: (a cell count, which the slack keeps from rounding up past an exact multiple).
_TOUCH_EPS: Final[float] = 1e-9
#: The smallest length that counts as a length: a segment shorter than this has no direction to offset
#: a capsule's side lines by (only its end caps are used), and a rectangle overlap narrower than this
#: is no overlap. In metres.
_LENGTH_EPS: Final[float] = 1e-9


def formula_spacing(d: float) -> float:
    """EN 12464-1:2011's maximum grid size `p = 0,2 x 5^log10(d)` (deck)."""
    return 0.2 * 5.0 ** math.log10(d)


def _ray_hit(origin: Point, direction: Point, a: Point, b: Point) -> float:
    """Distance along the unit ray to segment `a`-`b`, or `inf`."""
    sx, sy = b[0] - a[0], b[1] - a[1]
    denom = direction[0] * sy - direction[1] * sx
    if abs(denom) <= _PARALLEL_EPS:
        return math.inf
    qx, qy = a[0] - origin[0], a[1] - origin[1]
    t = (qx * sy - qy * sx) / denom
    u = (qx * direction[1] - qy * direction[0]) / denom
    return t if t > _TOUCH_EPS and -_TOUCH_EPS <= u <= 1 + _TOUCH_EPS else math.inf


def _rect_edges(rect: Rect) -> list[tuple[Point, Point]]:
    x0, y0, x1, y1 = rect
    return [((x0, y0), (x1, y0)), ((x1, y0), (x1, y1)), ((x1, y1), (x0, y1)), ((x0, y1), (x0, y0))]


def _sides(rect: Rect) -> list[tuple[tuple[Point, Point, Point], Point]]:
    """Each side's two ends and midpoint, with the side's outward unit normal."""
    x0, y0, x1, y1 = rect
    xm, ym = (x0 + x1) / 2.0, (y0 + y1) / 2.0
    return [
        (((x0, y0), (xm, y0), (x1, y0)), (0.0, -1.0)),
        (((x1, y0), (x1, ym), (x1, y1)), (1.0, 0.0)),
        (((x0, y1), (xm, y1), (x1, y1)), (0.0, 1.0)),
        (((x0, y0), (x0, ym), (x0, y1)), (-1.0, 0.0)),
    ]


def _point_in_closed_rect(p: Point, rect: Rect) -> bool:
    x0, y0, x1, y1 = rect
    return x0 - _TOUCH_EPS <= p[0] <= x1 + _TOUCH_EPS and y0 - _TOUCH_EPS <= p[1] <= y1 + _TOUCH_EPS


def _ray_hit_rect(origin: Point, direction: Point, rect: Rect) -> float:
    """Distance along the unit ray to `rect`, or `0.0` if `origin` already sits on or inside it.

    The zero case is the fix (round 1, I1a): `_ray_hit`'s own `t > _TOUCH_EPS` filter is deliberate --
    it is what lets a ray leave a group rectangle's own side without immediately re-hitting that
    same edge at `t=0` -- but the same filter let a ray *touching* another group's desk skip that
    desk's near edge and report the far one instead (measured: `band_narrowest` on a 12x10 room
    with a group rectangle `(3,4,5,5)` touching another group's `(5,4,5.6,5)` along their shared
    edge `x=5` returned `0.6`, the far edge, rather than `0.0`). A touching desk has zero free
    width on that side, same as a touched wall -- checking containment first, before ray-casting,
    is what restores that."""
    if _point_in_closed_rect(origin, rect):
        return 0.0
    return min((_ray_hit(origin, direction, a, b) for a, b in _rect_edges(rect)), default=math.inf)


def _segment_capsule_entry(origin: Point, direction: Point, a: Point, b: Point, radius: float) -> float:
    """The smallest `t >= 0` at which `origin + t * direction` (unit `direction`) comes within
    `radius` of the closed segment `a`-`b` -- i.e. enters that segment's "capsule"/stadium of the
    given radius -- or `inf` if it never does.

    Solved by intersecting the ray with the capsule's two offset lines (parallel to `a`-`b`, at
    `radius` on each side) and its two end-cap circles (radius `radius`, centred on `a` and `b`),
    then verifying every algebraic candidate against `point_segment_distance` itself rather than
    trusting either branch's domain algebra -- point-segment distance is a convex function of `t`
    along a straight ray (distance to a convex set, composed with an affine path), so the interval
    `{t : distance(t) <= radius}` is a single interval and the smallest valid, verified root is its
    entry point.
    """
    if point_segment_distance(origin, a, b) <= radius + _TOUCH_EPS:
        return 0.0
    ux, uy = b[0] - a[0], b[1] - a[1]
    length = math.hypot(ux, uy)
    candidates: list[float] = []
    if length > _LENGTH_EPS:
        nx, ny = -uy / length, ux / length
        w1 = direction[0] * nx + direction[1] * ny
        if abs(w1) > _PARALLEL_EPS:
            w0 = (origin[0] - a[0]) * nx + (origin[1] - a[1]) * ny
            candidates.append((radius - w0) / w1)
            candidates.append((-radius - w0) / w1)
    for c in (a, b):
        ocx, ocy = origin[0] - c[0], origin[1] - c[1]
        bcoef = 2.0 * (direction[0] * ocx + direction[1] * ocy)
        ccoef = ocx * ocx + ocy * ocy - radius * radius
        disc = bcoef * bcoef - 4.0 * ccoef
        if disc >= 0.0:
            sq = math.sqrt(disc)
            candidates.append((-bcoef - sq) / 2.0)
            candidates.append((-bcoef + sq) / 2.0)
    best = math.inf
    for t in candidates:
        if t < -_TOUCH_EPS:
            continue
        t = max(t, 0.0)
        point = (origin[0] + t * direction[0], origin[1] + t * direction[1])
        if abs(point_segment_distance(point, a, b) - radius) <= 1e-6:
            best = min(best, t)
    return best


def _strip_entry_t(origin: Point, direction: Point, room_edges: Sequence[tuple[Point, Point]], radius: float) -> float:
    """The smallest `t >= 0` at which the ray from `origin` along unit `direction` first comes
    within `radius` of any room edge -- the geometric wall-strip boundary (fix round 1, I1b). At
    `radius=0` this reduces to a plain ray-segment hit, so it also replaces the wall term when
    `wall_strip=False`.

    Not `distance-to-the-wall-line minus radius`, which is only correct for a wall perpendicular
    to the ray: near a reflex vertex, or alongside a wall the ray runs parallel to, the ray can
    enter the strip well before (or without ever) hitting an edge square-on (measured: the L room
    `[[0,0],[10,0],[10,4],[4,4],[4,10],[0,10]]`, a desk `(1.5,1.5,3.8,3.0)`, `w=2`: the ray from the
    desk's corner `(3.8,3.0)` toward `+y` passes near the reflex vertex `(4,4)` and enters its 0.5 m
    strip at `t≈0.542`, well short of squarely hitting any wall)."""
    return min((_segment_capsule_entry(origin, direction, a, b, radius) for a, b in room_edges), default=math.inf)


def _overlaps_side(side: tuple[Point, Point, Point], rect: Rect) -> bool:
    """Whether `rect` covers the side (from its first to its last sample) over a positive length.

    A rect that meets the side at one point, a corner contact, does not (N-117); one that shares any
    stretch of it, an abutting edge, does (R651).
    """
    (ax, ay), (bx, by) = side[0], side[2]
    x0, y0, x1, y1 = rect
    if ax == bx:   # a vertical side at x = ax, spanning y
        return x0 - _TOUCH_EPS <= ax <= x1 + _TOUCH_EPS and min(max(ay, by), y1) - max(min(ay, by), y0) > _TOUCH_EPS
    return y0 - _TOUCH_EPS <= ay <= y1 + _TOUCH_EPS and min(max(ax, bx), x1) - max(min(ax, bx), x0) > _TOUCH_EPS


def band_narrowest(room: list[Point], group_rects: Sequence[Rect], other_rects: Sequence[Rect], *, width: float, wall_strip: bool) -> float:
    """The band's narrowest free width (design §4, R615): the minimum over the group's rectangle
    sides of the distance along the outward normal -- from both ends and the midpoint -- to the
    first of the wall strip (the wall, less 0.5 m), a room edge, or another task area -- another
    group's rectangle, or one of this group's own others (final fix wave, P1: a 0.6 m gap between two
    desks of one group is a 0.6 m-wide ribbon, and ignoring it left the spacing coarser than p.36
    allows, against R609's "never coarser") -- capped at `width`. This is
    the design's operationalisation of licht.de p.36's "narrowest point"; for a wall not
    perpendicular to the side, or near a reflex vertex, the strip boundary is found geometrically
    (`_strip_entry_t`), not by subtracting a fixed offset from a straight wall hit.

    **Only samples at least `MIN_SURROUND_WIDTH_M` (0.5 m) wide are counted** (B2, owner ruling
    R651). p.36 halves the band "at its narrowest point *if that is 0.5 m or more*", so a free width
    under 0.5 m is not a narrowest point the rule speaks about -- it is a sample the rule excludes,
    not a sample that switches the rule off. Taking the minimum over every sample and *then* asking
    whether it clears 0.5 m let a gap under 0.5 m -- between two desks of one group since P1, or of
    another group before it -- become the minimum and hide a real pinch of 0.5 m or more: the band
    was then left unclamped and coarser than p.36 allows, against R609 (measured: a 0.6 m wall pinch
    plus a same-group 0.1 m gap gave spacing (0.3148, 0.3158) unclamped, where the same pinch alone
    gives 0.30). A side with no free width at all -- touching a wall or a desk -- is excluded by the
    same test, as it was before. When no sample qualifies, the return is the cap `width` itself and
    there is no half-narrowest condition: the formula cap alone stands (`plan_zones`).

    **A task area that meets a side only at a point is no obstacle to the sample there** (N-117): a
    corner contact gives the band the width a desk a millimetre away, or no desk, gives, never a
    coarser one (R609). **One that covers the side over a positive length still closes it** (an
    abutting edge, R651) at each sample it touches, as a touched wall does: such a sample has no free
    width and is left out of the minimum. A sample of the same side the area does not touch is
    measured as any other (room 5.9 m wide, group desk (3,4,5,5), another desk (5,4.6,5.6,5) covering
    the top 0.4 m of its right side: 0.9 m).
    """
    room_edges = [(room[i], room[(i + 1) % len(room)]) for i in range(len(room))]
    strip = WALL_STRIP_M if wall_strip else 0.0
    narrowest = width
    for index, rect in enumerate(group_rects):
        # Every task area but the origin's own rectangle (P1).
        obstacles = [*other_rects, *(r for j, r in enumerate(group_rects) if j != index)]
        for side in _sides(rect):
            samples, normal = side
            for origin in samples:
                to_wall = _strip_entry_t(origin, normal, room_edges, strip)
                # N-117: a task area the sample sits on or in blocks the ray only if it covers the side
                # over a positive length (an abutting edge, R651). One that meets the side at the sample's
                # own corner point is no obstacle to it, so a corner contact is never coarser than no contact.
                to_desk = min((_ray_hit_rect(origin, normal, other) for other in obstacles
                               if not _point_in_closed_rect(origin, other) or _overlaps_side(samples, other)),
                              default=math.inf)
                free = min(width, to_wall, to_desk)
                if free >= MIN_SURROUND_WIDTH_M - _TOUCH_EPS:
                    narrowest = min(narrowest, free)
    return narrowest


@dataclass(frozen=True)
class Zone:
    name: str
    kind: Literal["task", "surround", "background"]
    group: str | None
    circumscribing: Rect | None
    spacing: tuple[float, float] | None
    formula_spacing: tuple[float, float] | None
    counts: tuple[int, int] | None
    ribbon_clamped: bool
    aspect_swapped: bool
    candidates: int
    points: tuple[GridPoint, ...]
    task_in_strip: bool = False

    @property
    def absent(self) -> bool:
        return not self.points


@dataclass(frozen=True)
class ZonePlan:
    layout: Literal["task_zones", "unknown"]
    surround_mode: Literal["band", "rest_of_room"] | None
    surround_width: float | None
    wall_strip: bool
    height: ProvenanceValue
    zones: tuple[Zone, ...]
    warnings: tuple[ArtifactWarning, ...]
    floor_z: float = 0.0
    """The room polygon's z (`rad_core.grids.polygon_floor_z`, R763); points sit at
    `floor_z + height.value`."""

    @property
    def points_total(self) -> int:
        return sum(len(zone.points) for zone in self.zones)


def _intersect(a: Rect, b: Rect) -> Rect | None:
    r = (max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3]))
    return r if r[2] - r[0] > _LENGTH_EPS and r[3] - r[1] > _LENGTH_EPS else None


def _capped(length: float, spacing: float, count: int, cap: float) -> tuple[float, int, bool]:
    """Take the cap on an axis whose spacing exceeds it; keep `plan_grid`'s count otherwise, so float
    drift cannot add a column to an axis the cap does not touch (round 4, M2)."""
    if spacing <= cap + _TOUCH_EPS:
        return spacing, count, False
    n = math.ceil(length / cap - _TOUCH_EPS)
    return length / n, n, True


def _segment_rect_distance(a: Point, b: Point, rect: Rect) -> float:
    """Minimum distance between the closed segment `a`-`b` and the closed (filled) axis-aligned
    `rect`: 0 if they meet (an endpoint inside/on the rectangle, or the segment crossing one of its
    four edges), else the minimum of each segment endpoint's distance to the rectangle and each
    rectangle corner's distance to the segment -- the standard minimum-distance construction for
    two convex shapes, one of them (the segment) degenerate: for two disjoint convex shapes the
    closest pair always includes a vertex of at least one of them (fix round 1, M2 -- the R606
    wall-strip test needs the true geometric minimum, not a handful of sampled points, or a
    reflex-vertex apex closer to an edge's *interior* than to any sampled point is missed)."""
    x0, y0, x1, y1 = rect
    if (x0 <= a[0] <= x1 and y0 <= a[1] <= y1) or (x0 <= b[0] <= x1 and y0 <= b[1] <= y1):
        return 0.0
    corners = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    for edge_a, edge_b in _rect_edges(rect):
        if _segments_intersect(a, b, edge_a, edge_b):
            return 0.0
    return min(
        point_rect_distance(a, rect),
        point_rect_distance(b, rect),
        min(point_segment_distance(corner, a, b) for corner in corners),
    )


def _task_in_strip(membership: Membership, rect: Rect) -> bool:
    """Whether any point of the room boundary comes within the wall strip's width of `rect`
    (R606): the true minimum distance from every room edge to the rectangle, not a fixed set of
    eight sampled points on its boundary -- a reflex vertex can sit closer to an edge's *interior*
    than to any corner or midpoint sample (fix round 1, M2)."""
    if not membership.wall_strip:
        return False
    return any(_segment_rect_distance(a, b, rect) < WALL_STRIP_M for a, b in membership._edges)


def plan_zones(
    polygon: Sequence[Sequence[float]],
    *,
    height: float | None,
    tasks: Sequence[TaskZone],
    layout: Literal["task_zones", "unknown"],
    surround: SurroundSpec | None,
    wall_strip: bool,
    normal: Sequence[float] = (0.0, 0.0, 1.0),
) -> ZonePlan:
    """Design §4, whole: arguments, zones, spacing, lattices and warnings, before any IO."""
    room = validated_room(polygon)
    floor_z = polygon_floor_z(polygon)
    resolved_height = _resolve_height(Standard.EN_12464, height, provenance="assumed", source=None, note=None)
    if layout == "task_zones":
        width_or_rest: SurroundSpec = DEFAULT_SURROUND_WIDTH_M if surround is None else surround
        validate_task_zones(room, tasks, surround=width_or_rest)
    else:
        width_or_rest = None  # type: ignore[assignment]
    mode: Literal["band", "rest_of_room"] | None = (
        None if layout == "unknown" else "rest_of_room" if width_or_rest == "rest_of_room" else "band"
    )
    band_width = float(width_or_rest) if mode == "band" else None
    membership = Membership(room, tasks, surround=band_width if mode == "band" else width_or_rest, wall_strip=wall_strip)

    x_min, x_max, y_min, y_max = _bounds(room)
    inset = WALL_STRIP_M if wall_strip else 0.0
    inset_rect = _intersect((x_min + inset, y_min + inset, x_max - inset, y_max - inset),
                            (x_min + inset, y_min + inset, x_max - inset, y_max - inset))

    # (name, kind, group, rectangle, band group rects or None)
    layout_rows: list[tuple[str, Literal["task", "surround", "background"], str | None, Rect | None]] = []
    if layout == "unknown":
        layout_rows.append(("task_whole", "task", None, inset_rect))
    else:
        for index, task in enumerate(tasks, start=1):
            layout_rows.append((f"task_{index}", "task", task.group, task.rect))
        if mode == "rest_of_room":
            layout_rows.append(("surround", "surround", groups_in_order(tasks)[0], inset_rect))
        else:
            assert band_width is not None
            for group in groups_in_order(tasks):
                rects = [t.rect for t in tasks if t.group == group]
                grown = (min(r[0] for r in rects) - band_width, min(r[1] for r in rects) - band_width,
                         max(r[2] for r in rects) + band_width, max(r[3] for r in rects) + band_width)
                layout_rows.append((f"surround_{group}", "surround", group,
                                    _intersect(grown, inset_rect) if inset_rect else None))
            layout_rows.append(("background", "background", None, inset_rect))

    sized: list[tuple[str, str, str | None, Rect | None, GridSpec | None, tuple[float, float] | None, tuple[int, int] | None, bool]] = []
    total = 0
    for name, kind, group, rect in layout_rows:
        if rect is None:
            sized.append((name, kind, group, None, None, None, None, False))
            continue
        spec = plan_grid(Standard.EN_12464, rect[2] - rect[0], rect[3] - rect[1])
        sx, sy, nx, ny = spec.spacing_x, spec.spacing_y, spec.n_x, spec.n_y
        clamped = False
        if kind == "surround" and mode == "band":
            assert band_width is not None and group is not None
            group_rects = [t.rect for t in tasks if t.group == group]
            other_rects = [t.rect for t in tasks if t.group != group]
            narrow = band_narrowest(room, group_rects, other_rects, width=band_width, wall_strip=wall_strip)
            cap = formula_spacing(band_width)
            half_binds = narrow >= MIN_SURROUND_WIDTH_M - _TOUCH_EPS and narrow / 2.0 < cap
            if half_binds:
                cap = narrow / 2.0
            sx, nx, changed_x = _capped(rect[2] - rect[0], sx, nx, cap)
            sy, ny, changed_y = _capped(rect[3] - rect[1], sy, ny, cap)
            clamped = half_binds and (changed_x or changed_y)
            _ensure_cell_aspect_in_range(GridSpec(standard=Standard.EN_12464, max_cell=cap, spacing_x=sx,
                                                  spacing_y=sy, n_x=nx, n_y=ny,
                                                  governing_dimension=spec.governing_dimension,
                                                  aspect_swapped=spec.aspect_swapped))
        total += nx * ny
        sized.append((name, kind, group, rect, spec, (sx, sy), (nx, ny), clamped))
    if total > MATRIX_MAX_DECLARED_POINTS:
        raise PolygonError(
            f"this zoned grid would be {total} candidate points over all its zones, above the "
            f"{MATRIX_MAX_DECLARED_POINTS} this repo will size an allocation from (R199, R617). Zone a "
            "smaller room, or fewer and closer task areas.")

    dx, dy, dz = (float(v) for v in normal)
    z = floor_z + resolved_height.value
    zones: list[Zone] = []
    warnings: list[ArtifactWarning] = []
    for name, kind, group, rect, spec, spacing, counts, clamped in sized:
        if rect is None or spec is None or spacing is None or counts is None:
            zone = Zone(name=name, kind=kind, group=group, circumscribing=None, spacing=None,  # type: ignore[arg-type]
                        formula_spacing=None, counts=None, ribbon_clamped=False, aspect_swapped=False,
                        candidates=0, points=())
        else:
            xs = [rect[0] + (i + 0.5) * spacing[0] for i in range(counts[0])]
            ys = [rect[1] + (j + 0.5) * spacing[1] for j in range(counts[1])]
            kept = tuple(GridPoint(x=x, y=y, z=z, dx=dx, dy=dy, dz=dz)
                         for y in ys for x in xs if membership.in_zone((x, y), name))
            in_strip = kind == "task" and name != "task_whole" and _task_in_strip(membership, rect)
            zone = Zone(name=name, kind=kind, group=group, circumscribing=rect, spacing=spacing,  # type: ignore[arg-type]
                        formula_spacing=(spec.spacing_x, spec.spacing_y), counts=counts,
                        ribbon_clamped=clamped, aspect_swapped=spec.aspect_swapped,
                        candidates=counts[0] * counts[1], points=kept, task_in_strip=in_strip)
        zones.append(zone)
        warnings.extend(_zone_warnings(zone))
    if layout == "unknown" and zones[0].absent:
        raise GridEmptyError(
            f"the whole room less the {WALL_STRIP_M:g} m wall strip holds no calculation point -- the room is too "
            "small to zone. Use wall_strip=false, or a larger polygon.")
    return ZonePlan(layout=layout, surround_mode=mode, surround_width=band_width, wall_strip=wall_strip,
                    height=resolved_height, zones=tuple(zones), warnings=tuple(warnings), floor_z=floor_z)


def background_narrow_message() -> str:
    """The one text of `BACKGROUND_NARROW`, shared by `plan_zones` and `electric_tool` (L-117)."""
    return (
        f"the background's circumscribing rectangle is under {BACKGROUND_MIN_WIDTH_M:g} m on its "
        f"shorter side; licht.de p.8 describes a background at least {BACKGROUND_MIN_WIDTH_M:.1f} m wide.")


def _zone_warnings(zone: Zone) -> list[ArtifactWarning]:
    """Every literal code/severity pair on its own line, for the register scan."""
    found: list[ArtifactWarning] = []
    if zone.absent and zone.kind == "surround":
        found.append(ArtifactWarning(code="GRID_ZONE_ABSENT", severity="warn", field="task_zones", message=(
            f"zone {zone.name!r} holds no calculation point, so its task area(s) have no evaluated "
            "surround; the room, the wall strip or the band width leaves no room for one.")))
    elif zone.absent and zone.kind == "background":
        found.append(ArtifactWarning(code="GRID_ZONE_ABSENT", severity="info", field="task_zones", message=(
            f"zone {zone.name!r} holds no calculation point: the surrounds and the wall strip cover the "
            "rest of the room.")))
    if zone.task_in_strip:
        found.append(ArtifactWarning(code="TASK_IN_WALL_STRIP", severity="info", field="task_zones", message=(
            f"{zone.name} reaches into the {WALL_STRIP_M:g} m wall strip; its points there are kept (licht.de p.17), "
            "and its surround there is not sampled.")))
    if zone.ribbon_clamped:
        found.append(ArtifactWarning(code="GRID_RIBBON_CLAMPED", severity="info", field="surround", message=(
            f"{zone.name} was sized from its widest point and clamped to half its narrowest "
            f"({zone.spacing[0]:.4f} x {zone.spacing[1]:.4f} m; licht.de p.36).")))  # type: ignore[index]
    if zone.kind == "background" and not zone.absent and zone.circumscribing is not None:
        x0, y0, x1, y1 = zone.circumscribing
        if min(x1 - x0, y1 - y0) < BACKGROUND_MIN_WIDTH_M:
            found.append(ArtifactWarning(code="BACKGROUND_NARROW", severity="info", field="task_zones", message=background_narrow_message()))
    if zone.aspect_swapped:
        found.append(ArtifactWarning(code="GRID_ASPECT_SWAPPED", severity="info", field="zones", message=(
            f"{zone.name}: aspect ratio 2 or more, so d is the shorter side (the guides' rule, R609).")))
    if zone.candidates and len(zone.points) < zone.candidates:
        found.append(ArtifactWarning(code="GRID_POINTS_CLIPPED", severity="info", field="zones", message=(
            f"{zone.name}: {zone.candidates - len(zone.points)} of {zone.candidates} candidate points "
            "fell outside the zone and were dropped.")))
    return found
