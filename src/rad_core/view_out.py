"""EN 17037 5.2.2's plan-view geometry: azimuth intervals and the total horizontal sight angle.

Pure: no Radiance, no `rad_engine`, no `rad_mcp`. Consumes `rad_core.geometry.Point3` and nothing
else -- `rad_engine.rtrace.ESCAPED_MODIFIER` (Task 1) belongs to the caller that classifies a ray as
escaped or blocked (Task 3), not to this module, which only turns directions into angles.

Azimuth here is degrees **east of north**: `atan2(x, y)`, matching `rad_core.apertures.
azimuth_spread_deg` and `rad_engine.suns.SunPosition.direction`. `atan2(y, x)` would rotate every
angle by a quarter turn -- ruling R68's defect class, a whole branch that shipped 180 degrees wrong
because two modules were each internally consistent on their own.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final, Literal

from rad_core.geometry import Point3, area_vector
from rad_core.planar import _point_in_polygon

__all__ = [
    "LAYER_AREA_FRACTION",
    "LEVEL_RANK",
    "SAMPLES_PER_OPENING",
    "DistanceSpread",
    "Layer",
    "Level",
    "SightAngleSpread",
    "azimuth_deg",
    "distance_level",
    "distance_spread",
    "layer_area_fractions",
    "layer_of",
    "layers_level",
    "overall_level",
    "sample_opening",
    "sight_angle_deg",
    "sight_angle_level",
    "sight_angle_spread",
    "union_measure_deg",
]

#: EN 17037's three view-out layers (5.2.1). Defined once, here -- Task 7's sunlight bands import
#: `Level` from this module rather than duplicating either type.
Layer = Literal["sky", "landscape", "ground"]

#: The three, in one place, so `layer_area_fractions` and any future reader iterate the same list
#: the `Layer` alias names rather than a second copy of it.
_LAYERS: Final[tuple[Layer, ...]] = ("sky", "landscape", "ground")

#: EN 17037 Annex A's three recommendation levels. Defined once, here -- Task 7 imports this same
#: `Level` for the sunlight-exposure bands rather than defining a second copy.
Level = Literal["minimum", "medium", "high"]

#: Rank for C.1's "lowest rated criterion" comparison. `None` -- assessed and below minimum --
#: ranks below `"minimum"` itself, so a single unassessed-as-failing axis pulls `overall_level`
#: down to `None` rather than being skipped.
LEVEL_RANK: Final[dict[Level | None, int]] = {None: 0, "minimum": 1, "medium": 2, "high": 3}


def azimuth_deg(direction: Point3) -> float:
    """Degrees east of north in `[0, 360)`: `atan2(x, y)`, NOT `atan2(y, x)`.

    The angle is measured **from north towards east**, which is the convention
    `rad_core.apertures` and `rad_engine.suns` already use. The z component is discarded because
    the sight angle is a plan-view measure (5.2.2, "total horizontal sight angle").
    """
    return math.degrees(math.atan2(direction[0], direction[1])) % 360.0


def union_measure_deg(intervals: Sequence[tuple[float, float]]) -> float:
    """The total angular measure covered by `intervals`, counting overlap once.

    Each interval is `(start, end)` in degrees east of north and may **wrap through north**, i.e.
    `start > end` means the interval runs up through 360 and on past 0. Wrapping intervals are
    split at north before merging, so the merge itself never has to reason about the seam.
    """
    flat: list[tuple[float, float]] = []
    for start, end in intervals:
        s, e = start % 360.0, end % 360.0
        if s <= e:
            flat.append((s, e))
        else:
            flat.extend([(s, 360.0), (0.0, e)])
    total, current_end = 0.0, None
    for s, e in sorted(flat):
        if current_end is None or s > current_end:
            total += e - s
            current_end = e
        elif e > current_end:
            total += e - current_end
            current_end = e
    return total


#: How many **samples** one view opening is laid out with, for one reference point (design
#: section 5, "Rays per opening"). **Measured down a ladder, not chosen** (Phase 12 Task 13) -- and
#: it still carries an `Unsourced values` row in `docs/tool-surface.md`, because what the ladder
#: was read against is itself a choice (below).
#:
#: **What was measured, and why not a spread.** Design section 5 asks for the cheapest count that
#: resolves Table A.5's 14 / 28 / 54 degree steps "above the run-to-run spread" (ruling R91's
#: procedure). There is no spread: `sample_opening` lays out a fixed lattice and `rtrace -ab 0`
#: returns first hits, so repeats are bit-identical (measured: 5 repeats, 1 distinct payload, at
#: each of four counts on two fixtures). What the count changes is the **bias** --
#: `sight_angle_deg` under-reports by about half a cell at each edge and converges upward -- so
#: the requirement is read as: on a room just above an edge, the level at this count equals the
#: level at a count far up the ladder (ruling T13.1).
#:
#: **The ladder**, walked on `tests/conftest.py::office_near_28_deg` (geometric worst-point span
#: 29,288 degrees, 1,29 above the 28-degree `"medium"` edge), eye 1,2 m, 3 repeats per rung, in
#: REALISED samples per opening (ruling T13.2); rays per reference point are twice that:
#:
#:     request   lattice (rows x columns = realised)   worst point, deg   level
#:          4       1 x   4 =     4                         22,126        minimum
#:          8       1 x   6 =     6                         24,531        minimum
#:         16       2 x   8 =    16                         25,727        minimum
#:         32       3 x  11 =    33                         26,702        minimum
#:         48       3 x  14 =    42                         27,258        minimum
#:         64       4 x  16 =    64                         27,513        minimum
#:         96       5 x  20 =   100                         27,869        minimum
#:        128       6 x  23 =   138                         28,054        medium
#:        192       7 x  28 =   196                         28,275        medium
#:        256       8 x  32 =   256                         28,402        medium   <- shipped
#:        384      10 x  39 =   390                         28,561        medium
#:        512      11 x  45 =   495                         28,658        medium
#:        768      14 x  55 =   770                         28,773        medium
#:       1024      16 x  64 =  1024                         28,845        medium
#:       2048      23 x  91 =  2093                         28,977        medium
#:       4096      32 x 128 =  4096                         29,067        medium
#:       8192      45 x 181 =  8145                         29,132        medium
#:      16384      64 x 256 = 16384                         29,178        medium   (reference)
#:
#: `tests/fixtures/README.md` carries the same rungs with their wall-clock.
#:
#: **Why 256 and not 138, the cheapest rung that agrees.** 138 clears the edge by 0,05 degrees, so
#: a room of this shape about a twentieth of a degree nearer the edge would disagree. 256 is the
#: cheapest rung on this ladder whose half-budget rung also agrees -- the margin is one halving,
#: and 0,40 degrees above the edge on this fixture. Wall-clock here, for 54 reference points,
#: 3 calls per rung: 0,23-0,47 s per call at 256 against 0,08-0,15 s at 64. Below about 1024 a
#: per-call cost that does not scale (0,07 s at the 4-sample rung) is a large part of that; from
#: about 1024 up the time is roughly linear in the count.
#:
#: **What this does not establish.** The bias depends on the geometry, not only on the count.
#: At this count the worst point here reads 3,0 % under its geometric span (2,7 % under the
#: reference rung), so a room of this shape whose span is less than about that far above an edge
#: can be rated one level low -- never high: the bias only ever under-reports. A reference point
#: beside the window, seeing it at a grazing angle, is biased further (measured on a 6 x 4 m room
#: with a 4 m window, worst point at the front corner: 34,871 at a realised 261 against 38,162 at
#: 16380, 8,6 % under that rung and 9,8 % under the 38,660-degree geometric span).
#:
#: **Only columns resolve the sight angle of a vertical opening** (two samples in one column differ
#: in elevation, not azimuth), so near-square cells spend most of the count on rows the sight angle
#: does not read. Measured on the same fixture, one row of N columns reads what this lattice
#: reads with N columns (to the three decimals printed, at 16, 32, 64, 128 and 256 columns), and on
#: this 4,8 x 1,2 m window the lattice needs a count of about N squared over 4 to lay out N
#: columns -- 64 columns in one row read 28,845, the 1024-sample rung's value. **Not adopted**
#: (ruling T13.3): the layers and the distance criteria read the rows, and changing the allocation
#: reopens the lattice Task 10's review closed its Critical on (order invariance, mirror symmetry,
#: upward convergence).
#:
#: **Samples, not rays, and the name says so** (ruling T10.15). Every sample costs **two** rays --
#: the eye ray that tests whether the opening is visible from that reference point and the outward
#: ray that gives the layer and the view distance (`rad_mcp.view_sunlight_tool._build_rays`) -- so
#: a realised lattice of N samples costs 2N rays per opening per reference point. It was called
#: `RAYS_PER_OPENING`, and the published result field `rays_per_opening` with it, until the review
#: of Task 10: a caller who read that field and never reached its docstring priced a run at half
#: its real cost, which is this repo's silent-wrong-number class. Anything that prices this
#: constant must say which unit it is quoting, or be out by exactly two.
#:
#: **And this number is a request, not the count that was used.** `sample_opening` rounds the
#: row/column split and drops cells outside the loops, so the realised lattice is usually a
#: different number -- measured on Task 10's own fixtures, for this request of 256: 264 for a
#: 5,0 x 1,2 m window (8 x 33), 253 for a 2,5 x 1,2 m half (11 x 23) and 228 for a 5,0 x 0,4 m
#: band (4 x 57). The result publishes what was realised, per opening, in `samples_per_opening`;
#: pricing a run off this constant instead is wrong by whatever the rounding did.
#:
#: **Guarded once, and checked for its comment once.** In `tests/test_view_sunlight_tool.py`,
#: `test_the_shipped_budget_gives_the_level_the_converged_sampling_gives` compares this count with
#: 4096 on that fixture; every rung the ladder measured below a request of 128 reads `"minimum"`
#: there, so each would turn it red (run live at 64 and 96). In `tests/test_view_out.py`,
#: `test_the_budget_constant_carries_a_ladder_comment` reads this module's source for
#: `SAMPLES_PER_OPENING` and the word "ladder" -- a presence check that no value of this constant
#: turns red. (That test was `test_the_budget_is_the_measured_rung_not_a_round_number`, and this
#: comment said "Guarded twice", until the final whole-branch review.) The plan spells the
#: constant's name `RAYS_PER_OPENING`, and is stale there (ruling T10.15).
SAMPLES_PER_OPENING: Final[int] = 256


#: The in-plane horizontal axis for an opening so flat that +Z has no projection onto its own
#: plane. **Sourced, not chosen**: it is the same vector `rad_core.apertures._FLAT_APERTURE_UP`
#: takes from Radiance's own skylight alias `kbinD = kbin(0,0,-1, 0,1,0)` in `klems_full.cal:54`.
#: A rooflight has no meaningful "horizontal sight angle" anyway (5.2.2 is a plan-view measure of a
#: facade), so this exists to give the lattice a frame rather than to claim a reading.
_FLAT_OPENING_AXIS: Final[Point3] = (0.0, 1.0, 0.0)

#: Below this, +Z's projection onto the opening's plane has no direction left to normalise.
#: `rad_core.apertures._PARALLEL_TO_Z_EPS`'s number and its reason, but **applied to a different
#: quantity and therefore a tighter test**: that one compares `abs(dot(normal, Z)) >= 1 - eps`,
#: this one compares the projection's own magnitude, `sqrt(1 - dot^2) <= eps`. At the same 1e-9
#: the second is reached only about 4,5e4 times nearer to parallel than the first. The consequence
#: is confined to an opening within about 1e-9 radian (6e-8 degrees) of parallel to +Z, where this
#: frame's in-plane axes
#: become float noise while still spanning the plane -- the lattice then covers a rotated bounding
#: rectangle of the same polygon, so it samples the same opening with a few more cells dropped.
#: Not aligned with the sibling, because doing so is a behaviour change and this is a comment.
_PARALLEL_TO_Z_EPS: Final[float] = 1e-9


def _normalise(vector: Point3) -> Point3:
    length = math.sqrt(vector[0] ** 2 + vector[1] ** 2 + vector[2] ** 2)
    return (vector[0] / length, vector[1] / length, vector[2] / length)


def _dot(a: Point3, b: Point3) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a: Point3, b: Point3) -> Point3:
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def _opening_frame(loops: Sequence[Sequence[Point3]]) -> tuple[Point3, Point3, Point3] | None:
    """An orthonormal `(normal, up, across)` frame for the opening, **derived from its plane, and
    from the vertex order only through one sign** -- never from which vertex a `.rad` file happens
    to list first. A reversed winding flips `normal` and `across` together and leaves `up` alone;
    that sign is the whole of the order dependence, and `sample_opening`'s lattice is invariant
    under it (see its docstring).

    `up` is +Z projected onto the opening's plane, falling back to `_FLAT_OPENING_AXIS` where that
    projection vanishes; `across` completes the right-handed frame. This is deliberately the same
    construction `rad_core.apertures._up_vector` uses, for the same reason: it is perpendicular to
    the normal at every tilt, by construction rather than by luck.

    **`up` does not change when the polygon's winding is reversed.** A reversed loop flips the
    Newell normal's sign, and `up = Z - (Z.n)n` is quadratic in `n`, so it is unchanged; `across`
    flips with `n`, and `sample_opening`'s lattice is invariant under that flip (see its docstring).

    `None` when the loops have no consistent normal at all -- no area, or windings that cancel.
    `rad_core.apertures.derive` is the function that *refuses* such an aperture; this one only
    declines to sample it, because refusing the same condition twice in two modules is how the two
    answers come to disagree.
    """
    total = (0.0, 0.0, 0.0)
    for loop in loops:
        part = area_vector(loop)
        total = (total[0] + part[0], total[1] + part[1], total[2] + part[2])
    if math.sqrt(total[0] ** 2 + total[1] ** 2 + total[2] ** 2) <= 0.0:
        return None
    normal = _normalise(total)
    along_z = normal[2]
    up = (-along_z * normal[0], -along_z * normal[1], 1.0 - along_z * normal[2])
    if math.sqrt(up[0] ** 2 + up[1] ** 2 + up[2] ** 2) <= _PARALLEL_TO_Z_EPS:
        up = _FLAT_OPENING_AXIS
    up = _normalise(up)
    return normal, up, _cross(normal, up)


def sample_opening(
    loops: Sequence[Sequence[Point3]], count: int
) -> dict[tuple[int, int], Point3]:
    """About `count` points over a view opening, on a **lattice that remembers which samples are
    neighbours** -- keyed by `(row, column)` in the opening's own plane.

    The adjacency is not decoration. `sight_angle_deg` measures the azimuth union by bridging
    *neighbouring* samples, so it needs to know which two samples have nothing between them; a
    flat list of points cannot say.

    ## Construction, and why it is this one

    The opening's plane gives an orthonormal frame (`_opening_frame`); every vertex is projected
    into it; the lattice is the cell **centres** of a regular grid over that projection's bounding
    rectangle, with the row and column counts chosen to make the cells as near square as the
    rectangle allows; and a cell is kept when its centre lies inside one of the loops
    (`rad_core.planar._point_in_polygon`, whose boundary rule is "on the edge is outside" -- a
    sample on the frame is not a sample through the glass).

    **This replaced a fan triangulation from `loop[0]`, which was measured wrong** (Phase 12 Task
    10 review, Critical C1). That construction made the sample density depend on which vertex the
    `.rad` file listed first, and on the office fixture's own 5,0 x 1,2 m window it moved the
    reported sight angle from 44,15 to 74,67 degrees -- across Table A.5's 54-degree `"high"` edge
    -- under nothing but a rotation of the vertex list. The cause is structural, not a shortage of
    samples: when two of a fan triangle's vertices share the frame's horizontal coordinate -- which
    every fan triangle of an axis-aligned rectangle does -- the root map's two lattice axes collapse
    onto one in azimuth, and a whole row of samples shares a single azimuth. Measured up the count,
    the left/right asymmetry on a mirror-symmetric window was 30,5 deg at 64 samples and 26,3 deg
    at 4096, so no sampling budget closes it. This construction has none of that: the bounding
    rectangle and the lattice are functions of the vertex *set*, and the frame is one too up to the
    sign a reversed winding flips.

    ## What the caller may rely on

    - **Order invariance.** Reordering or reversing a loop's vertices returns the same point set,
      to within float rounding -- measured over eight orderings in `tests/test_view_out.py`. It was
      bit-identical at a request of 64; at 128, 256, 1024 and 4096 some coordinates differ between
      orderings by under 1e-15 m, which moved a measured sight angle by under 1e-13 degrees (Task
      13). The frame's `up` is unchanged by a winding flip and its `across` merely reverses, which
      maps column `j` to column `n - 1 - j`. That relabelling maps neighbouring pairs onto
      neighbouring pairs, so `sight_angle_deg`'s interval set is unchanged; every other number
      downstream is read off the sample *positions* and their survival, never off the keys.
    - **Mirror symmetry.** A lattice of cell centres over a bounding rectangle is carried onto
      itself by a reflection about that rectangle's own centre line.
    - **The realised count is about `count`, not `count`** -- the row/column split rounds, and
      cells outside the loops are dropped. Read the returned mapping, never the argument.

    `{}` for a `count` of zero or less, and for an opening with no consistent normal, no area, or a
    bounding rectangle with no width in one direction.
    """
    frame = _opening_frame(loops)
    if frame is None or count <= 0:
        return {}
    normal, up, across = frame
    offset = _dot(loops[0][0], normal)
    projected = [[(_dot(v, up), _dot(v, across)) for v in loop] for loop in loops]
    flat = [point for loop in projected for point in loop]
    up_min, up_max = min(p[0] for p in flat), max(p[0] for p in flat)
    across_min, across_max = min(p[1] for p in flat), max(p[1] for p in flat)
    up_span, across_span = up_max - up_min, across_max - across_min
    if up_span <= 0.0 or across_span <= 0.0:
        return {}
    columns = max(1, round(math.sqrt(count * across_span / up_span)))
    rows = max(1, round(count / columns))
    cells: dict[tuple[int, int], Point3] = {}
    for row in range(rows):
        u = up_min + (row + 0.5) * up_span / rows
        for column in range(columns):
            a = across_min + (column + 0.5) * across_span / columns
            if not any(_point_in_polygon((u, a), loop) for loop in projected):
                continue
            cells[(row, column)] = (
                u * up[0] + a * across[0] + offset * normal[0],
                u * up[1] + a * across[1] + offset * normal[1],
                u * up[2] + a * across[2] + offset * normal[2],
            )
    return cells


def _shorter_arc(start: float, end: float) -> tuple[float, float]:
    """The azimuth interval between two neighbouring samples, taken the short way round.

    What this needs to be right is only that neighbouring samples are **less than 180 degrees**
    apart in azimuth, which holds whenever a cell subtends less than a half-turn from the eye --
    true of any opening a reference point is not inside. It is **not** a claim that the step is
    small: measured on the `office` window from a point 0,5 m in front of its end, 16 columns cover
    84,3 degrees, so the realised step there is about 5 degrees and the far-end steps are a
    hundredth of that. Written as an ordered `(start, end)` pair that may wrap through north, which
    is exactly what `union_measure_deg` consumes.
    """
    forward = (end - start) % 360.0
    return (start, end) if forward <= 180.0 else (end, start)


def sight_angle_deg(per_opening: Sequence[Mapping[tuple[int, int], Point3]]) -> float:
    """The total horizontal sight angle (5.2.2) at ONE reference point, in degrees.

    Each element of `per_opening` is one view opening's **visible** samples: the lattice keys
    `sample_opening` produced, mapped to the direction from the eye to that sample, and carrying
    only the samples whose ray actually reached that opening. The union is taken across every
    opening at once, so two openings overlapping in plan are counted once (5.2.2's *"view
    opening(s)"*).

    **The measure is the union of the azimuth intervals between *neighbouring* visible samples**,
    never a fixed bucket around each one. Two samples that are lattice neighbours have no sampled
    gap between them, so the opening subtends every azimuth in between; two samples with a blocked
    sample between them do not, and nothing bridges them. That is what makes a partition standing
    in front of part of the window remove the azimuths behind it, instead of having them smeared
    over by a bucket wide enough to reach across the hole.

    **This replaced a fixed-width bucket per direction** (Phase 12 Task 10 review, Critical C1).
    No single width can work: the azimuth subtended per metre of glass varies by an order of
    magnitude across one window seen from one point, so a width wide enough to bridge the far end's
    gaps overshoots the near end's, and a width narrow enough for the near end leaves holes at the
    far end. The review measured the consequence at 30 degrees of error across Table A.5's
    `"high"` edge. Bridging neighbours needs no width at all, which is also why this function takes
    no `resolution_deg` and cannot acquire a default for one.

    **It under-reports, and converges upward.** The union runs from the first visible sample's
    azimuth to the last's, so it misses half a cell at each edge of the opening; that shortfall
    shrinks as the count rises. Under-reporting makes a Table A.5 level harder to meet, never
    easier.

    `0.0` for no openings, for an opening with nothing visible, and for a single visible sample --
    one sample has measured no angle, and inventing one would publish an angle nothing sampled.
    """
    intervals: list[tuple[float, float]] = []
    for visible in per_opening:
        for (row, column), direction in visible.items():
            here = azimuth_deg(direction)
            for neighbour in ((row + 1, column), (row, column + 1)):
                beside = visible.get(neighbour)
                if beside is not None:
                    intervals.append(_shorter_arc(here, azimuth_deg(beside)))
    return union_measure_deg(intervals)


def layer_of(direction: Point3, *, escaped: bool) -> Layer:
    """Which of EN 17037's three layers (5.2.1) this ray sees.

    A ray that reaches nothing sees **sky**, and that is tested first: a downward ray that escapes
    has seen sky past the edge of the modelled world, and calling it ground would invent a surface
    the scene does not contain. `escaped` is the caller's own bool -- converted from
    `rad_engine.rtrace.ESCAPED_MODIFIER` before this module ever sees it, since `rad_core` imports
    nothing from `rad_engine`.

    Otherwise the split is the **ray's own elevation at the eye**: below the horizontal is
    **ground**, at or above it is **landscape**. This is C.4.1's no-ground-line construction
    expressed as an angle. It deliberately does not test the hit surface's orientation, which is
    unreliable on imported geometry, and does not ask the caller to name a ground material, which
    an OBJ import usually does not have.

    **Which way it errs:** a flat roof below eye level classifies as ground when it is plainly
    landscape. That makes the layer count *harder* to satisfy at the medium and high levels, never
    easier, because landscape is the layer every level requires.
    """
    if escaped:
        return "sky"
    return "ground" if direction[2] < 0.0 else "landscape"


#: Table A.5 (chapters/ch03-en-17037-daylight.md:142-148), horizontal sight angle. Checked
#: high-to-minimum so the first threshold met is the highest one -- both bounds are inclusive
#: (">= 14 deg" etc, never "> 14 deg").
_TABLE_A5_SIGHT_ANGLE_DEG: Final[dict[Level, float]] = {
    "high": 54.0,
    "medium": 28.0,
    "minimum": 14.0,
}

#: Table A.5, outside distance of view, in metres. Same high-to-minimum, inclusive convention.
_TABLE_A5_DISTANCE_M: Final[dict[Level, float]] = {
    "high": 50.0,
    "medium": 20.0,
    "minimum": 6.0,
}

#: Table A.5's layers column header: "Layers seen from >= 75 % of utilized area". Sourced from
#: that header, not chosen -- so it carries no `Unsourced values` row.
LAYER_AREA_FRACTION: Final[float] = 0.75


def sight_angle_level(deg: float) -> Level | None:
    """Table A.5's horizontal-sight-angle row for a total sight angle of `deg` (5.2.2's own
    measure, `sight_angle_deg`'s return value). `None` means assessed and below the minimum
    14 deg -- never "not assessed".
    """
    for level, threshold in _TABLE_A5_SIGHT_ANGLE_DEG.items():
        if deg >= threshold:
            return level
    return None


def distance_level(metres: float) -> Level | None:
    """Table A.5's outside-distance-of-view row for a view distance of `metres` (3.12: measured
    from the opening's inner surface, not from the eye). `None` means assessed and below the
    minimum 6,0 m -- never "not assessed".
    """
    for level, threshold in _TABLE_A5_DISTANCE_M.items():
        if metres >= threshold:
            return level
    return None


def _opening_layers_level(layers: frozenset[Layer]) -> Level | None:
    """The best Table A.5 layers-row a single view opening supports on its own: all three layers
    for high, landscape plus one more for medium, landscape alone for minimum -- each row
    requires the layers **in that one opening** (chapters/ch03-en-17037-daylight.md:147-148), so
    this never looks past `layers` itself.
    """
    if {"sky", "landscape", "ground"} <= layers:
        return "high"
    if "landscape" in layers and len(layers) >= 2:
        return "medium"
    if "landscape" in layers:
        return "minimum"
    return None


def layers_level(per_point: Sequence[Mapping[str, frozenset[Layer]]]) -> Level | None:
    """Table A.5's layers criterion, aggregated over `per_point` -- one mapping per reference
    point, from view-opening name to the frozenset of layers seen through *that* opening.

    Each point's own level is the best row any **single** opening supports (`_opening_layers_level`):
    layers are never pooled across openings, so landscape through one window and sky through
    another does not make a medium point. The result is then the highest level whose point
    fraction is `>= LAYER_AREA_FRACTION` -- Table A.5's own "seen from >= 75 % of utilized area",
    attached to the layers column alone. `None` means assessed and below minimum, whether because
    no point reaches even the landscape layer or because too few points do.
    """
    point_levels = [
        max((_opening_layers_level(layers) for layers in openings.values()), key=LEVEL_RANK.get, default=None)
        for openings in per_point
    ]
    if not point_levels:
        return None
    for level in ("high", "medium", "minimum"):
        met = sum(1 for pl in point_levels if LEVEL_RANK[pl] >= LEVEL_RANK[level])
        if met / len(point_levels) >= LAYER_AREA_FRACTION:
            return level
    return None


@dataclass(frozen=True)
class SightAngleSpread:
    """Table A.5's horizontal-sight-angle criterion over a whole reference grid.

    `worst_deg` decides `level`, and that is C.3's own framing (design section 4.1): the medium
    rule there is written against *the distance from the facade to the most remote part of the
    utilized area*, i.e. the worst case made explicit. Table A.5 attaches its *"from at least 75 %
    of utilized area"* to the **layers** column alone, so extending an area fraction to this
    criterion would be an addition to the table rather than a reading of it. `median_deg` and
    `max_deg` are published beside the verdict so a caller can see whether one awkward corner is
    carrying it.
    """

    level: Level | None
    worst_deg: float
    median_deg: float
    max_deg: float


def sight_angle_spread(angles: Sequence[float]) -> SightAngleSpread:
    """`SightAngleSpread` over one angle per reference point (`sight_angle_deg` each).

    An empty `angles` gives zeros and `level=None` -- assessed, below minimum. That is not a
    special case worth refusing: a grid with no reference points is already refused upstream
    (`GridEmptyOnDiskError`), and a grid whose every point sees nothing genuinely has a total
    sight angle of zero.
    """
    if not angles:
        return SightAngleSpread(level=None, worst_deg=0.0, median_deg=0.0, max_deg=0.0)
    worst = min(angles)
    return SightAngleSpread(
        level=sight_angle_level(worst),
        worst_deg=worst,
        median_deg=statistics.median_low(angles),
        max_deg=max(angles),
    )


@dataclass(frozen=True)
class DistanceSpread:
    """Table A.5's outside-distance-of-view criterion over one view opening's rays.

    **`None` in the three distances means unbounded, not unknown**: the ray escaped the model and
    met no obstruction at all. `n_rays` is what tells the two apart from the outside -- all three
    `None` with `n_rays > 0` is an unobstructed view, all three `None` with `n_rays == 0` is an
    opening nothing reached.

    `level` comes from the **median** (design section 4.1, and its own `Unsourced values` row):
    *"major"* obstruction is better served by what most of the view meets than by a worst case,
    where one balcony post would fail a view open for fifty metres. `statistics.median_low` and not
    `median`: on an even sample the lower of the two middle rays is taken rather than their mean,
    so the published number is a distance a ray actually measured, and it errs shorter -- harder to
    meet a level, never easier.
    """

    level: Level | None
    min_m: float | None
    median_m: float | None
    max_m: float | None
    unobstructed_fraction: float
    n_rays: int


def distance_spread(distances: Sequence[float | None]) -> DistanceSpread:
    """`DistanceSpread` over one opening's outward rays; `None` in `distances` is an escape.

    **Escapes stay in the sample**, sorted last as `math.inf`. An unobstructed ray is a measurement
    of the view, not a missing one, and dropping it would pull the median down to the obstructed
    subset's -- reporting a blocked view for an opening that mostly is not.
    """
    if not distances:
        return DistanceSpread(
            level=None, min_m=None, median_m=None, max_m=None,
            unobstructed_fraction=0.0, n_rays=0,
        )
    ordered = sorted(math.inf if metres is None else metres for metres in distances)
    median = statistics.median_low(ordered)
    bounded = lambda metres: None if math.isinf(metres) else metres  # noqa: E731
    return DistanceSpread(
        level=distance_level(median),
        min_m=bounded(ordered[0]),
        median_m=bounded(median),
        max_m=bounded(ordered[-1]),
        unobstructed_fraction=sum(1 for d in ordered if math.isinf(d)) / len(ordered),
        n_rays=len(ordered),
    )


def layer_area_fractions(
    per_point: Sequence[Mapping[str, frozenset[Layer]]]
) -> dict[str, float]:
    """The fraction of reference points that see each of the three layers through **at least one**
    opening. `per_point` is `layers_level`'s own argument, so the two readings cannot drift.

    **Diagnostic, and deliberately not what decides the layers level.** `layers_level` requires the
    combination within a *single* opening, so landscape through one window and sky through another
    is not a medium point -- a verdict read off these three numbers would disagree with the one
    published beside them.
    """
    if not per_point:
        return {layer: 0.0 for layer in _LAYERS}
    return {
        layer: sum(
            1 for openings in per_point if any(layer in seen for seen in openings.values())
        ) / len(per_point)
        for layer in _LAYERS
    }


def overall_level(levels: Sequence[Level | None]) -> Level | None:
    """C.1's aggregation rule (chapters/ch03-en-17037-daylight.md:185-186): "the overall achieved
    level for view out should be ranked according to the lowest rated criterion." This is a
    minimum by rank, never an average or a best-of -- and `None` (assessed, below minimum) ranks
    below `"minimum"`, so one failing axis pulls the whole result to `None`.

    **Raises `ValueError` on an empty `levels`.** There are always three view-out criteria --
    sight angle, distance, layers -- so an empty sequence is a caller bug, not a legitimate
    "nothing assessed" state; inventing a return value for it (`None`, or the vacuous-minimum
    `"high"`) would manufacture a verdict from no assessment at all.
    """
    if not levels:
        raise ValueError(
            "overall_level received no criteria -- there are always three: sight angle, "
            "distance, layers."
        )
    return min(levels, key=LEVEL_RANK.get)
