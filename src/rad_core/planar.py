"""Two-dimensional point, segment and polygon predicates.

Moved out of `rad_core.grids` in Phase 10 (design §3) because `rad_core.geometry`'s coincidence check
needs the same predicates, and a second copy of a boundary rule is the drift ruling R30 forbids.
Pure: no IO, no Radiance.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

__all__ = [
    "Point",
    "Rect",
    "_GEOM_EPS",
    "_on_segment",
    "_orientation",
    "_point_in_polygon",
    "_point_on_boundary",
    "_segments_intersect",
    "nearest_point_on_rect",
    "point_rect_distance",
    "point_segment_distance",
    "rect_inside_polygon",
    "segment_inside_polygon",
]

#: Slack for the polygon geometry below -- segment/endpoint/boundary coincidence, not a relaxation
#: of "self-intersecting" or "inside".
_GEOM_EPS = 1e-9

Point = tuple[float, float]


def _orientation(a: Point, b: Point, c: Point) -> float:
    """> 0 for a CCW turn a->b->c, < 0 for CW, 0 for collinear (twice the signed triangle area)."""
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def _on_segment(a: Point, b: Point, p: Point) -> bool:
    """`p`, already known collinear with `a`-`b`, actually lies within that segment's span."""
    return (
        min(a[0], b[0]) - _GEOM_EPS <= p[0] <= max(a[0], b[0]) + _GEOM_EPS
        and min(a[1], b[1]) - _GEOM_EPS <= p[1] <= max(a[1], b[1]) + _GEOM_EPS
    )


def _segments_intersect(p1: Point, p2: Point, p3: Point, p4: Point) -> bool:
    """Standard orientation-based proper/collinear segment intersection test."""
    d1, d2 = _orientation(p3, p4, p1), _orientation(p3, p4, p2)
    d3, d4 = _orientation(p1, p2, p3), _orientation(p1, p2, p4)

    if ((d1 > 0) != (d2 > 0)) and (d1 != 0 and d2 != 0) and ((d3 > 0) != (d4 > 0)) and (d3 != 0 and d4 != 0):
        return True
    if d1 == 0 and _on_segment(p3, p4, p1):
        return True
    if d2 == 0 and _on_segment(p3, p4, p2):
        return True
    if d3 == 0 and _on_segment(p1, p2, p3):
        return True
    if d4 == 0 and _on_segment(p1, p2, p4):
        return True
    return False


def _point_on_boundary(point: Point, polygon: Sequence[Point]) -> bool:
    """True if `point` lies exactly on one of `polygon`'s edges (within `_GEOM_EPS`).

    The explicit half of `_point_in_polygon`'s boundary rule -- see that function's docstring for
    why this exists as its own check rather than being left to the interior test's fall-through
    behaviour (fix round 1, finding I5).
    """
    n = len(polygon)
    for i in range(n):
        a, b = polygon[i], polygon[(i + 1) % n]
        if abs(_orientation(a, b, point)) <= _GEOM_EPS and _on_segment(a, b, point):
            return True
    return False


def _point_in_polygon(point: Point, polygon: Sequence[Point]) -> bool:
    """Point-in-polygon test, with an explicit, symmetric boundary rule: **a point exactly on the
    polygon boundary is outside.**

    The interior half is the standard even-odd (crossing-number) rule. Left uncorrected, that rule's
    boundary behaviour is an implementation artifact rather than a decision: whether a point on a
    given edge classifies inside or outside depends on what lies beyond that edge along the
    ray-casting direction, which is not something a caller can predict from the edge alone (the
    conventional "half-open" tie-break most crossing-number and winding-number reference
    implementations share, because it is what makes adjacent polygons tile the plane with no
    double-counted or missed seam, at the cost of exactly this unpredictability). That asymmetry is
    invisible until a grid point lands exactly on an edge -- which happens
    (`TestNotchOnAGridColumn` in tests/test_grids.py reproduces it on an EN 12464 12x15 m grid
    whose 1.2 m column spacing puts a cell-centre exactly on a notch edge moved to x=3 -- verified by
    hand before that fixture was written that x=3 is the orientation where the uncorrected rule
    actually disagrees, and that the more obvious x=9 does not) -- and a caller has no way to predict
    which side of that asymmetry a given edge falls on.

    "Outside" rather than "inside" for a sensor grid specifically: a point sitting exactly on a wall
    or a re-entrant notch edge is architecturally ambiguous (is it in the room, or in the wall?), and
    the conservative choice for a lighting calculation is to drop it rather than risk reporting a
    point that is arguably not receiving room daylight/light at all. `_point_on_boundary` is checked
    first and is what makes the rule symmetric -- **both** edges of the office fixture (or any
    polygon) drop a point sitting exactly on them, not just one.
    """
    if _point_on_boundary(point, polygon):
        return False

    x, y = point
    n = len(polygon)
    inside = False
    x_j, y_j = polygon[-1]
    for x_i, y_i in polygon:
        if (y_i > y) != (y_j > y):
            x_cross = (x_j - x_i) * (y - y_i) / (y_j - y_i) + x_i
            if x < x_cross:
                inside = not inside
        x_j, y_j = x_i, y_i
    return inside


Rect = tuple[float, float, float, float]
"""An axis-aligned rectangle `(x0, y0, x1, y1)` with `x0 < x1` and `y0 < y1` (Phase 11b design §4)."""


def point_segment_distance(p: Point, a: Point, b: Point) -> float:
    """Euclidean distance from `p` to the closed segment `a`-`b`."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    length2 = dx * dx + dy * dy
    if length2 == 0.0:
        return math.hypot(p[0] - a[0], p[1] - a[1])
    t = max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / length2))
    return math.hypot(p[0] - (a[0] + t * dx), p[1] - (a[1] + t * dy))


def nearest_point_on_rect(p: Point, rect: Rect) -> Point:
    """The point of the closed rectangle nearest `p` -- `p` itself when inside."""
    x0, y0, x1, y1 = rect
    return (min(max(p[0], x0), x1), min(max(p[1], y0), y1))


def point_rect_distance(p: Point, rect: Rect) -> float:
    """Distance from `p` to the closed rectangle; 0 inside. Rounded corners outside (design §4)."""
    q = nearest_point_on_rect(p, rect)
    return math.hypot(p[0] - q[0], p[1] - q[1])


def _segment_meets_open_rect(a: Point, b: Point, rect: Rect) -> bool:
    """True if a positive-length piece of segment `a`-`b` lies in the rectangle's **open** interior.

    Liang-Barsky with strict slab bounds. A segment parallel to a slab (`p == 0`) meets the open
    interior only if it lies strictly between that slab's two lines; one lying on a rectangle edge
    does not. A segment touching the rectangle at one point gives `t0 == t1` and does not count.
    """
    x0, y0, x1, y1 = rect
    dx, dy = b[0] - a[0], b[1] - a[1]
    t0, t1 = 0.0, 1.0
    for p, lower, upper in ((dx, a[0] - x0, x1 - a[0]), (dy, a[1] - y0, y1 - a[1])):
        if p == 0.0:
            if lower <= _GEOM_EPS or upper <= _GEOM_EPS:
                return False
            continue
        lo, hi = -lower / p, upper / p
        if p < 0.0:
            lo, hi = hi, lo
        t0, t1 = max(t0, lo), min(t1, hi)
        if t1 - t0 <= 0.0:
            return False
    length = math.hypot(dx, dy)
    return (t1 - t0) * length > _GEOM_EPS


def rect_inside_polygon(rect: Rect, polygon: Sequence[Point]) -> bool:
    """The rectangle lies inside the simple polygon, touching its boundary allowed (design §4).

    Necessary and sufficient for a simple polygon: the open interior is connected, so if no polygon
    edge meets it, it lies wholly inside or wholly outside, and its centre decides which. A centre
    exactly on the boundary implies an edge meets the open interior, so that case is caught too.
    """
    x0, y0, x1, y1 = rect
    centre = ((x0 + x1) / 2.0, (y0 + y1) / 2.0)
    if not _point_in_polygon(centre, polygon):
        return False
    n = len(polygon)
    return not any(
        _segment_meets_open_rect(polygon[i], polygon[(i + 1) % n], rect) for i in range(n)
    )


def _cross(ax: float, ay: float, bx: float, by: float) -> float:
    return ax * by - ay * bx


def segment_inside_polygon(p: Point, q: Point, polygon: Sequence[Point]) -> bool:
    """The closed segment `p`-`q` stays inside the polygon or on its boundary (design R618).

    Refused on any **proper** crossing of an edge's interior. Otherwise the segment is cut at every
    contact with the boundary (edge endpoints and collinear overlaps included), and the midpoint of
    each piece must be inside or on the boundary -- which catches a segment that leaves the room
    through two reflex vertices while touching nothing but those vertices.
    """
    rx, ry = q[0] - p[0], q[1] - p[1]
    cuts = [0.0, 1.0]
    n = len(polygon)
    for i in range(n):
        a, b = polygon[i], polygon[(i + 1) % n]
        sx, sy = b[0] - a[0], b[1] - a[1]
        denom = _cross(rx, ry, sx, sy)
        qpx, qpy = a[0] - p[0], a[1] - p[1]
        if abs(denom) > _GEOM_EPS:
            t = _cross(qpx, qpy, sx, sy) / denom
            u = _cross(qpx, qpy, rx, ry) / denom
            if -_GEOM_EPS <= t <= 1 + _GEOM_EPS and -_GEOM_EPS <= u <= 1 + _GEOM_EPS:
                if _GEOM_EPS < t < 1 - _GEOM_EPS and _GEOM_EPS < u < 1 - _GEOM_EPS:
                    return False
                cuts.append(min(1.0, max(0.0, t)))
        elif abs(_cross(qpx, qpy, rx, ry)) <= _GEOM_EPS:
            length2 = rx * rx + ry * ry
            if length2 > 0.0:
                for end in (a, b):
                    t = ((end[0] - p[0]) * rx + (end[1] - p[1]) * ry) / length2
                    if 0.0 <= t <= 1.0:
                        cuts.append(t)
    cuts.sort()
    for t_start, t_end in zip(cuts, cuts[1:]):
        if t_end - t_start <= _GEOM_EPS:
            continue
        mid_t = (t_start + t_end) / 2.0
        mid = (p[0] + mid_t * rx, p[1] + mid_t * ry)
        if not (_point_in_polygon(mid, polygon) or _point_on_boundary(mid, polygon)):
            return False
    return True
