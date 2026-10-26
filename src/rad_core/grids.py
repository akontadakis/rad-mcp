"""Calculation grids, per standard.

Pure: no Radiance, no subprocesses, no file IO. Runs green with no binaries installed.

The standard is always an explicit argument. EN 12464 and EN 17037 use the same formula shape with
a different coefficient, so a default here would silently produce a grid of the wrong density --
see CONTEXT.md.

`max_cell_size` and `plan_grid` size a grid over a bare rectangle and return counts and spacings,
not coordinates -- the right pure boundary for a caller that has no room outline yet
(`sensor_grid(action="plan")`). `place_sensor_grid` is the caller that *does* have one
(`sensor_grid(action="create")`): it turns a spacing plan into actual sensor coordinates, resolves
each standard's reference-plane height with provenance, and clips a rectangle-sized grid down to a
non-rectangular polygon's real interior.

**`plan_grid` and `place_sensor_grid` size LM-83 through one path, not two.** LM-83's own rule
(spacing <= 24in, offset 12-24in from walls, "preferably centred") is not the cell-centre
construction the two formula standards use -- see `_lm83_axis`'s docstring for why cell-centre
placement alone under-shoots the 12in floor by 4.8mm on the office fixture. Both `plan_grid` and
`place_sensor_grid` call `_lm83_grid_spec`/`_lm83_axis` for LM-83, so a "plan" preview and a
"create" placement of the same rectangle can never disagree -- `TestLM83PlanAndCreateAgree` in
tests/test_grids.py pins this so it cannot drift back apart.

**How advisories work at this layer.** `rad_core` cannot import `rad_mcp` -- CONTEXT.md's layer
rule -- so nothing here can construct a wire `Advisory`. Two shapes cover everything this module
needs to say, both borrowed from `rad_core.manifest` rather than reinvented:

- **A refusal** (`error` severity, the operation did not happen) is a raised exception. `GridError`
  and its subclasses carry a class-attribute `code`, exactly the shape
  `rad_engine.project.ProjectError` uses and for the same reason given there: the wire envelope is
  the protocol layer's job, and an engine/core exception only needs to carry *which* allocated code
  applies, if any.
- **A finding that does not stop the operation** (`info`/`warn`, the result is still usable) is
  reused as data on the result: `rad_core.manifest.ArtifactWarning` is already exactly "one
  classified advisory, kept with the artifact" -- `GRID_POINTS_CLIPPED` reuses it rather than a
  third code/severity/message triple invented here.

`GRID_ASPECT_SWAPPED` and `GRID_VERY_LARGE` are unaffected by this: both are *built* from
`GridSpec.aspect_swapped` / `GridSpec.n_points` in `rad_mcp/server.py`, one layer up, and stay there.
The **number** `GRID_VERY_LARGE` is judged against does not -- `GRID_VERY_LARGE_POINTS` is below,
with every other band this module holds, because `rad_mcp` carries no thresholds at all (CONTEXT.md,
Layers). Constructing an advisory from a value is the protocol layer's job; deciding the value is
not.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Final

from rad_core.geometry import COINCIDENCE_TOLERANCE_M
from rad_core.lm83_edition import DEFAULT_LM83_EDITION, LM83_22, Lm83Edition
from rad_core.manifest import ArtifactWarning, Provenance, ProvenanceValue
from rad_core.planar import (
    _GEOM_EPS,
    Point,
    _on_segment,
    _orientation,
    _point_in_polygon,
    _point_on_boundary,
    _segments_intersect,
)

__all__ = [
    "EN17037_REFERENCE_HEIGHT_M",
    "EN17037_REFERENCE_HEIGHT_SOURCE",
    "GRID_POLYGON_COVERAGE_MIN",
    "GRID_VERY_LARGE_POINTS",
    "MATRIX_MAX_DECLARED_POINTS",
    "LM83_GRID_POINT_DENSITY_MIN",
    "LM83_22_REFERENCE_HEIGHT_M",
    "LM83_22_REFERENCE_HEIGHT_SOURCE",
    "LM83_REFERENCE_HEIGHT_M",
    "LM83_REFERENCE_HEIGHT_SOURCE",
    "LM83_WALL_OFFSET_MAX_M",
    "LM83_WALL_OFFSET_MIN_M",
    "M2_TO_SQFT",
    "ArgumentOutOfRangeGridError",
    "CellAspectOutOfRangeError",
    "FloorNotLevelError",
    "GridEmptyError",
    "GridError",
    "GridPoint",
    "GridSpec",
    "HeightOutOfRangeError",
    "PlacedGrid",
    "PolygonError",
    "Standard",
    "TaskHeightRequiredError",
    "grid_polygon_coverage",
    "grid_spacing_m",
    "lm83_grid_point_count",
    "lm83_grid_point_density",
    "lm83_points_area_sf",
    "lm83_reference_height",
    "max_cell_size",
    "place_sensor_grid",
    "plan_grid",
    "polygon_area_m2",
    "polygon_floor_z",
    "recorded_floor_mismatch",
    "refuse_extra_ordinates",
]


class Standard(str, Enum):
    """Which standard's grid rule to apply."""

    EN_12464 = "en12464"      # -1 indoor and -2 outdoor share the rule
    EN_17037 = "en17037"
    LM_83 = "lm83"


#: Coefficient in ``p = C * 5 ** log10(d)``. The whole point of this module.
_COEFFICIENT = {Standard.EN_12464: 0.2, Standard.EN_17037: 0.5}

#: Both formula-based standards cap the cell size. EN 12464-2 states it explicitly
#: (clause 4.3.3); EN 17037 states ``P <= 10 m`` under Formula (B.1).
_MAX_CELL_M = 10.0

#: LM-83 fixes spacing rather than deriving it, and states the bound two ways: ch04
#: (ies-lm-83-sda-ase.md) both gives the imperial figure "no greater than 24in on center" *and*
#: names the metric bound directly ("maximum 2 ft (0.6 m)"), and separately lists "using a grid
#: coarser than 0.6 m" as its own anti-pattern. 24 in is exactly 0.6096 m, so the two readings are
#: not the same number -- 0.6 m is the *stricter* of the two (0.6 <= 0.6096), and owner ruling R22
#: is to use it: a pitch of 0.6 m satisfies both the imperial and the metric statement of the bound
#: simultaneously, where 0.6096 m satisfies only the imperial one and lands on the wrong side of the
#: chapter's own "coarser than 0.6 m" pitfall. Do not "correct" this back to 0.6096 -- that direction
#: has already been tried and reverted (ruling R22).
_LM83_MAX_SPACING_M = 0.6

#: 12 in / 24 in wall offset LM-83 requires of its analysis points (ch04-ies-lm-83-sda-ase.md:
#: "offset 12-24 in from walls"). Independent of `_LM83_MAX_SPACING_M`: this pair is the wall-offset
#: band ch04 states in inches, 12 and 24, and 24 in is 0.6096 m exactly regardless of what pitch the
#: grid itself is built at. It no longer equals the spacing cap now that the cap is 0.6 m (ruling
#: R22) -- the two constants were only ever equal by the imperial-reading coincidence, not by any
#: rule tying them together, and `_lm83_axis`'s proof below is re-derived to depend on that, not on
#: the two happening to match.
LM83_WALL_OFFSET_MIN_M = 0.3048
LM83_WALL_OFFSET_MAX_M = 0.6096

#: EN 17037 Annex B.2's reference plane, cited alongside the grid formula it shares this module
#: with (ch03-en-17037-daylight.md: "Reference plane 0.85 m.").
EN17037_REFERENCE_HEIGHT_M = 0.85
EN17037_REFERENCE_HEIGHT_SOURCE = "EN 17037 Annex B.2 -- reference plane 0.85 m above floor"

#: LM-83's sDA workplane (ch04-ies-lm-83-sda-ase.md: "horizontal workplane at 30 in / 0.8 m above
#: finished floor").
LM83_REFERENCE_HEIGHT_M = 0.8
LM83_REFERENCE_HEIGHT_SOURCE = (
    "IES LM-83-12 sDA_300/50% criterion -- horizontal workplane at 30 in / 0.8 m above finished floor"
)

#: R864: LM-83-22's default point height, from ANSI/IES LM-83-22.
LM83_22_REFERENCE_HEIGHT_M: Final[float] = 0.76
LM83_22_REFERENCE_HEIGHT_SOURCE: Final[str] = (
    "ANSI/IES LM-83-22 sections 4.1.2 and 4.2.1 -- points \"typically placed at a height of "
    "0.76 m (30 in.) above finished floor\""
)


def lm83_reference_height(edition: Lm83Edition) -> tuple[float, str]:
    """`edition`'s default analysis-point height in metres, and the source text that states it:
    0.8 m under LM-83-12, 0.76 m (30 in.) under LM-83-22 (sections 4.1.2 and 4.2.1, R864)."""
    if edition == LM83_22:
        return LM83_22_REFERENCE_HEIGHT_M, LM83_22_REFERENCE_HEIGHT_SOURCE
    return LM83_REFERENCE_HEIGHT_M, LM83_REFERENCE_HEIGHT_SOURCE


#: Above this many sensor points, `sensor_grid` reports `GRID_VERY_LARGE` (warn) --
#: *"N sensor points will make annual simulation slow."*
#:
#: **Unsourced, and a reporting threshold rather than a physical one.** No standard gives a point
#: count at which a grid becomes impractical: EN 12464-1, EN 17037 Annex B.2 and LM-83 all specify a
#: *spacing*, and the count that follows is whatever the room's area divides into. It changes no
#: number this module computes and refuses nothing -- it only decides whether the result *mentions*
#: that an annual run over this grid will be slow -- which is exactly why it is recorded here as a
#: guess rather than embedded confidently, the same treatment `rad_core.geometry._MIN_EDGE_LENGTH_M`
#: gets for its own tessellation-noise floor.
#:
#: Chosen against the failure mode, not measured: 20 000 points is roughly a 60 x 60 m floor plate at
#: LM-83's 0.6 m pitch, well past the single room this project's fixtures describe, and an annual
#: 3-phase run scales linearly in sensors. Erring high is cheap (a slow run nobody was warned about);
#: erring low is worse than cheap in a different way (a warning on every ordinary office, which users
#: learn to ignore -- docs/plan.md's own Phase 2 risk note about `NOT_ENCLOSED` becoming noise).
#: What would establish it is observed wall time per sensor on this project's own recorded
#: `wall_time_s`, which is the same self-calibration the runtime estimates are waiting on.
#:
#: It lives here, beside the other grid bands, and **not** in the layer that emits the advisory.
#: Task 7b left it in `rad_mcp.server` arguing that a reporting threshold is not a physical one; fix
#: round 2 ruled it down anyway, and the ruling is right for a reason worth keeping: with no
#: automated guard over `rad_mcp` (`tests/test_purity.py` cannot detect a threshold -- see
#: `TestTheProtocolLayerSpawnsNothing`), "no thresholds in the wire layer" only holds if it is
#: applied without exceptions to argue about.
GRID_VERY_LARGE_POINTS = 20000

#: The point count above which a **manifest-declared** matrix shape is refused outright, rather
#: than warned about. A different quantity from `GRID_VERY_LARGE_POINTS` above and deliberately not
#: the same number: that one is a *reporting* threshold on a grid this server is about to place,
#: this one is a *magnitude bound* on an integer read out of a manifest, which ruling R50 holds
#: untrusted, and which every caller sizes an allocation from.
#:
#: **The bound exists because the shape cross-check cannot be it.** `_ensure_header_matches_manifest`
#: compares the `.ill` header against the manifest record -- and a hand-edited manifest can edit
#: both sides, which is exactly what R50 says. Measured on this machine at `6abd6ce`:
#: `results(action="metrics")` against a record declaring `NROWS=400 000` over a 3-row file built
#: 400 000 `PointTally` objects and grew RSS 43 -> 343 MB in 0.94 s *before* the truncation refusal
#: two lines below caught it. At the ~1.4 kB per declared point that measures out to,
#: `NROWS=2000000000` is ~2.8 TB: it OOM-kills the process, taking every project and every running
#: job with it.
#:
#: **Ten times `GRID_VERY_LARGE_POINTS`**, and the factor is the argument: a refusal set at the
#: warning threshold would refuse a grid this server itself will place and merely warn about, while
#: an order of magnitude above it is beyond anything the placers can produce -- LM-83's 0.6 m
#: spacing over 200 000 points is a 72 000 m2 floor plate -- and 200 000 x 1.4 kB is ~280 MB, which
#: a process survives and can report on.
MATRIX_MAX_DECLARED_POINTS: Final[int] = 10 * GRID_VERY_LARGE_POINTS

#: Every standard here requires near-square cells (docs/tool-surface.md, CONTEXT.md). The tolerance
#: is float slack around the formula boundary, not a relaxation of the rule -- check 2.5 asserts the
#: coefficient ratio itself to 1e-9, so this module holds itself to the same precision.
_CELL_ASPECT_MIN = 0.5
_CELL_ASPECT_MAX = 2.0
_CELL_ASPECT_TOLERANCE = 1e-9

#: Square feet per square metre, and **the only place this repo writes it**.
#:
#: IES LM-83-12 section 2.2.6's blind-trigger bands are stated in square feet
#: (ch04:97-100 -- `>= 1 000 sf`, `500-999 sf`, `200-499 sf`, `< 200 sf`) while every polygon this
#: repo records is in metres (`sensor_grid(action="create")`'s own `polygon` argument: *"a list of
#: [x, y] vertices in metres"*; it has no `units` argument, and `InputRecord.units` is the scene
#: input's declared unit, never applied to the polygon). Exactly one conversion therefore stands
#: between a recorded room outline and which row of that table decides an hour.
#:
#: Written twice is how a band boundary moves by 8 % with nothing going red, which is why
#: `tests/test_grids.py::TestSquareFootConversion` greps `src/` for a second copy of the digits
#: rather than merely asserting this one.
M2_TO_SQFT: Final[float] = 10.7639

#: The fraction of its recorded polygon's **area** a sensor grid's own cells must sample before
#: `results` stops mentioning the discrepancy. **A substituted default, not a sourced value**
#: -- recorded in docs/tool-surface.md's Unsourced values table under this constant's own name.
#:
#: **It gates an `info` advisory and refuses nothing** (owner ruling, Phase 7c Task 15). LM-83
#: section 2.2.6 requires grids to *span the whole space*, so the polygon -- not the sensor cloud --
#: is the right quantity for the analysis area, and it is what gets published. But the standard also
#: asks for grids inset 12-24 in from every wall (ch04:47), so a correctly-built LM-83 grid always
#: covers *less* than its polygon, and how much less is a judgement about someone else's model.
#: Refusing on it would reject the very grids the standard asks for; the caller sees the discrepancy
#: and decides.
#:
#: **That ruling stands, and `LM83_GRID_POINT_DENSITY_MIN` below does not touch it** (Phase 7d-1
#: Task 16, ruling R171): this constant is an **area sampled** and that one is a **density**, they
#: are measured from different inputs, and only the density one gates a refusal. Nothing about
#: under-*coverage* refuses anything, here or anywhere -- a grid may read 0.495 against this floor
#: and 1.000 against that one, and this repo's own `place_sensor_grid` produces exactly that pair on
#: a 1.2 x 40 m corridor.
#:
#: **Why 0.5, re-derived for the measure that now exists** (Phase 7d-1 Task 17, ruling R160).
#: `grid_polygon_coverage` was a bounding box over a bounding box until that ruling, and this
#: paragraph derived 0.5 from `((4.310 - 1.219) / 4.310)**2 = 0.514` -- arithmetic that was correct
#: and is now about a function that no longer exists. The number does not move; the reason is
#: rebuilt from the measure that replaced it, which is a **fraction of floor area sampled**:
#: `len(points) * dx * dy / polygon_area_m2(polygon)`. Read directly, 0.5 is *"more than half the
#: room is unsampled"*.
#:
#: The size that matters is unchanged, and for the same reason: **200 sf**, the lowest edge of
#: section 2.2.6's table -- below it the `< 200 sf` row is unbounded downward, so a grid sampling
#: even less of the room still lands in the same row and the fraction decides nothing. Swept over
#: every rectangle of at least 200 sf at every aspect ratio, an LM-83 grid placed by this module's
#: own `place_sensor_grid` reads at least **0.476**, and at least **0.625** on any such room whose
#: narrow axis is 1.2096 m or wider -- `_LM83_MAX_SPACING_M + 2 * LM83_WALL_OFFSET_MIN_M`, the width
#: at which the standard's own placement admits a *second* column of sensors. Worked cases: the
#: office fixture (12 x 15 m) reads **0.912**, a square 200 sf room **0.949**, a 3 x 3 m room
#: **0.640**, a 2 x 2.5 m room **0.864**.
#:
#: **Those two figures read 0.484 and 0.659 when this paragraph was first written, and both were
#: false of the placer** (Task 17 fix wave, the third time this derivation has published a
#: measurement that is not true of the code beside it). They are the minima over rectangles of
#: *exactly* 200 sf, which is the domain the sweep behind them covered and is **not** the domain
#: this sentence claims: coverage is not monotone in area (see below), so "at least 200 sf" admits
#: lower readings than "exactly 200 sf" does. The binding rooms are just over the edge -- 1.2095 x
#: 15.6095 m (203 sf) reads 0.4767 and 1.8095 x 10.8095 m (211 sf) reads 0.6258. Both are approached
#: rather than attained: an axis of length `L` carries
#: `max(1, floor((L - 2 * 0.3048) / 0.6) + 1)` sensors -- `_lm83_axis` floors the count at one and
#: centres that single point, so an axis shorter than `2 * 0.3048 = 0.6096` m, where the two wall
#: offsets alone exceed it, still carries a sensor rather than none. **The floor is not a detail
#: outside this sentence's domain**: a 0.5 x 40 m room is 215 sf, inside the "at least 200 sf" this
#: paragraph claims, and it reads **1.000** -- while the unfloored formula, which stood here until
#: the Task 17 re-review measured it, gives zero sensors and a coverage of 0.000, contradicting the
#: "at least 0.476" three lines above. So the fraction `n * 0.6 / L` falls as `L` grows within a
#: band and jumps up at each band edge (the first edge, at 0.6096 m, is the one the floor absorbs:
#: the count is 1 on both sides of it and 2 only past 1.2096 m),
#: and the greatest lower bounds are `(0.6 / 1.2096) * (15.0 / 15.6096) = 0.47666` and
#: `(1.2 / 1.8096) * (10.2 / 10.8096) = 0.62573`, reached only in the limit. **The conclusion is
#: unchanged.** The load-bearing figure is the second one -- the first is below 0.5 under either
#: pair of digits, and that is the single-column family the next paragraph says fires correctly --
#: and 0.625 still clears 0.5 by a quarter of the scale.
#:
#: **What still fires, and it is true rather than a false positive.** Below 1.2096 m of width
#: LM-83's pitch and offset leave a single line of sensors, whose cells are 0.6 m wide; in a 1.2 m
#: corridor that samples half the floor and the measure says 0.495. The `info` is correct there. The
#: class R160 *closed* is the one where the old box-over-box ratio read a correctly-inset grid low
#: for reasons that were not about sampling at all: 0.360 on a 3 x 3 m room, 0.480 on a 2.4 x 20 m
#: room, and **exactly 0.0** on that same corridor -- a floor of zero being unreachable is why no
#: positive threshold could close it and why the measure, not the number, had to change.
#:
#: **What a grid that genuinely under-covers reads.** The failure section 2.2.6's "grids must span
#: the whole space" exists to prevent is a grid built over one part of an open-plan floor and
#: published against the whole floor's area. Measured on a 20 x 10 m floor with this module's own
#: placer: a grid over half of it (10 x 10 m) reads **0.461**, over a third (6.667 x 10 m)
#: **0.317**, over a quarter (5 x 10 m) **0.230**. (0.288 stood here for the middle case and was
#: not a third of the floor: it is a **6.6** x 10 m grid, and truncating 20/3 to 6.6 drops a whole
#: sensor column, 11 to 10.)
#:
#: **0.5 separates that family from every correctly-placed grid in the domain the two swept figures
#: cover** -- at least 200 sf with a narrow axis of 1.2096 m or wider, where the least any such grid
#: reads is 0.625, against 0.461 for the most generous member of the under-covering family. **It
#: does not separate it from every correctly-placed grid**, and that qualifier is load-bearing
#: rather than decorative: the guarantee is bounded by area, not by the band edge. Measured on this
#: module's own placer, a correctly-placed LM-83 grid reads **0.4398** in a 1.8095 x 1.8095 m room
#: (35 sf) and **0.4954** in a 1.8095 x 2.4095 m room (47 sf) -- both wider than 1.2096 m, both
#: below 0.5, both advised. Those rooms sit under the lowest edge of section 2.2.6's table, where
#: the `< 200 sf` row is unbounded downward and the fraction decides nothing, which is the same
#: reason 200 sf is the size that matters three paragraphs above; the advisory there costs a
#: mention and changes no verdict. (This sentence claimed the unqualified separation until the
#: Task 17 re-review measured those two rooms.)
#:
#: **The two directions of error are not symmetric**, the same asymmetry `GRID_VERY_LARGE_POINTS`
#: and `OBSTRUCTION_SPREAD_THRESHOLD` are recorded against. Too high a floor warns on ordinary
#: correctly-inset grids, which users learn to ignore. Too low a floor stays silent while a grid
#: covering a fraction of an open-plan floor is reported against the whole floor's area. It changes
#: no number the server computes and refuses nothing: it decides only whether the result *mentions*
#: the gap. (`LM83_GRID_POINT_DENSITY_MIN` below *does* gate a refusal, and it is a different
#: measure over different inputs -- this sentence is about this constant and stays true of it.)
GRID_POLYGON_COVERAGE_MIN: Final[float] = 0.5


#: The fraction of LM-83's **own point count** a sensor grid must supply over its recorded polygon
#: before `results(action="blind_schedule")` refuses to apply section 2.2.6's small-area rows to it.
#: **A substituted default, not a sourced value** -- recorded in docs/tool-surface.md's Unsourced
#: values table under this constant's own name.
#:
#: **A different question from `GRID_POLYGON_COVERAGE_MIN` above, and the two are not
#: interchangeable** (ruling R171, which withdrew a design that used that one for this job).
#:
#: - `grid_polygon_coverage` asks *"how much of the room's floor does this grid actually
#:   **sample**?"* -- the area of its own cells over the polygon's shoelace area.
#: - `lm83_grid_point_density` asks *"how many points does this grid have, against how many LM-83's
#:   own placement rule would put in the same polygon?"* -- a ratio of **counts**.
#:
#: Neither implies the other, and the disagreement is not academic -- it survived Task 17's
#: replacement of the coverage measure, on a different room. Measured against this repo's own
#: `place_sensor_grid`: a correctly-placed LM-83 grid in a 1.2 x 40 m corridor samples **0.495** of
#: its polygon (below the coverage floor -- one 0.6 m column in a 1.2 m width) at a density of
#: **1.000** (it is LM-83's own grid); a deliberately sparse 3 m grid over a 12 x 15 m room samples
#: **1.000** (clear of the coverage floor -- 20 cells of 9 m2 is the whole 180 m2) at a density of
#: **0.044** -- 20 points where LM-83 places 456. A gate on coverage would refuse the first and pass
#: the second, which is exactly backwards for the defect this number exists to catch.
#:
#: **Why 0.5, and it is not copied from the constant above** -- the two are equal by arithmetic
#: coincidence and are derived from different things. Section 2.2.6's three small-area rows are
#: absolute point counts (`> 5`, `> 3`, `> 1`, at `rad_core.metrics.blinds_close`), and ch04:93 says
#: outright that they are *stated exceptions to the 2 % general rule* rather than a fourth tier --
#: **and it names a pitch while doing so**: *"Small-area corrections, because a **2 ft grid**
#: under-samples small rooms -- the 2 % figure is the general rule and the three bands below are its
#: stated exceptions, not a fourth tier."* So the corpus itself ties these rows to a stated grid
#: spacing, which is the density reading below argued for from arithmetic alone; the arithmetic is
#: corroboration, not the only evidence.
#: Read that way they are the 2 % rule evaluated at **LM-83's own point density**: at 0.6 m pitch one
#: point covers 0.36 m2, i.e. 3.875 sf, so each band's upper edge carries
#: `1 000 / 3.875 = 258` points (2 % = 5.16, the row's `> 5`), `500 / 3.875 = 129` points
#: (2 % = 2.58, the row's `> 3`) and `200 / 3.875 = 51.6` points (2 % = 1.03, the row's `> 1`).
#: All three rows reproduce to the digit. The rows are therefore **a density statement**, and
#: applying them to a grid at some other density silently applies some other percentage: a grid at
#: density `r` fires its row at `T/r` of the points the standard meant, so the effective share is
#: inflated by exactly `1/r`.
#:
#: 0.5 is the largest step the table itself takes between neighbouring rows: the minimum closing
#: counts are 2, 4 and 6, and 2 -> 4 is a factor of two. At `r = 0.5` the derived trigger is off by
#: exactly one such step; below it the schedule is applying a share of the room that section 2.2.6
#: does not state at **any** area. So the floor is "the distortion has grown past the table's own
#: coarsest granularity", not a guess at how sparse is too sparse.
#:
#: **The two directions of error are not symmetric, and they are not symmetric the other way round
#: from `GRID_POLYGON_COVERAGE_MIN`'s.** Too **high** a floor refuses grids that are only slightly
#: coarser than LM-83's -- recoverable (re-place the grid at `standard="lm83"`) but a refusal on
#: work that was nearly right. Too **low** a floor ships the **flattering** direction: fewer points
#: against an unchanged absolute threshold under-closes the blinds, more daylight is retained than
#: the standard allows, and sDA comes out **overstated**. A number that is somewhat wrong here
#: therefore costs a recoverable refusal on one side and an overstated compliance verdict on the
#: other, which is why this one gates a refusal where the coverage floor gates an `info`.
#:
#: **Only below `rad_core.metrics.LM83_BLIND_AREA_GENERAL_SF`.** At and above 1 000 sf the criterion
#: is the ratio `points_in_sun / points_total > 2 %`, in which the density cancels arithmetically;
#: what does not cancel is the row *selection*, and that error over-closes, retains less daylight
#: and **understates** sDA -- the conservative direction, which is recorded and not refused
#: (`BLIND_TRIGGER_MIXED_BASIS` at `warn` rather than `error`).
#:
#: A grid **finer** than LM-83's (density above 1.0) is never refused: more points can only make the
#: count these rows are calibrated on more faithful, never less. That case is reachable -- EN 12464's
#: formula puts more points in the same room than LM-83's fixed 0.6 m pitch does on any reference
#: dimension below **about 6 m**, measured at 72 points against LM-83's 48 on a 4 x 5 m room, 49
#: against 16 on a 3 x 3 m one, 81 against 64 at 5 x 5 m, and equal at 81 apiece at 6 x 6 m. (It is
#: **coarser** on larger rooms -- 100 against 169 at 8 x 8 m, 120 against 456 on 12 x 15 m -- so
#: "EN 12464 is the fine one" is not a rule and is not claimed here; which standard lands on which
#: side depends on the room. **6 m is the crossover for the point COUNT**, which is what this
#: measure divides; 4.816 m is the crossover for the *spacing*, and the two are not the same
#: question -- an earlier draft of this note quoted the spacing figure for a count claim.)
LM83_GRID_POINT_DENSITY_MIN: Final[float] = 0.5


# --------------------------------------------------------------------------------------------
# Refusals
# --------------------------------------------------------------------------------------------


class GridError(ValueError):
    """Something this module refuses to build.

    Mirrors `rad_engine.project.ProjectError`'s shape (a class-attribute `code`, read by whichever
    layer eventually builds the wire envelope) rather than `rad_core.manifest.ArtifactWarning`'s,
    because a refusal stops the operation and an `ArtifactWarning` is data attached to one that
    succeeded. `rad_core` may not import `rad_mcp`, so this is as close to that shape as this layer
    can get on its own -- see the module docstring. `rad_mcp.runtime.refuse_engine_error` is where
    this meets the wire, alongside `ProjectError`/`JobError`.

    `code` is `None` where the register has no code for the condition -- none of this module's
    refusals are in that position today, but the field exists for the same reason
    `ProjectError.code` does: a gap should read as a gap, not silently acquire a code invented at
    the call site.
    """

    code: str | None = None
    severity: str = "error"
    field: str | None = None
    """Which client argument the refusal is about -- `ProjectNameError`'s reasoning in
    `rad_engine.project` applies here too: `ARGUMENT_OUT_OF_RANGE` is one code for a whole class of
    refusals, and `field` is what keeps it actionable."""

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        if code is not None:
            self.code = code


class TaskHeightRequiredError(GridError):
    """EN 12464-1 has no default reference-plane height.

    Check 2.7 / ch01-en-12464-1-indoor.md: the task area "may be horizontal, vertical or inclined",
    so there is no one plane a default could name -- unlike EN 17037's 0.85 m or LM-83's 0.8 m
    (0.76 m under LM-83-22), which are both always-horizontal workplanes. Omitting `height` is
    refused, not substituted.
    """

    code = "GRID_TASK_HEIGHT_REQUIRED"
    field = "height"


class CellAspectOutOfRangeError(GridError):
    """A cell aspect ratio that cannot be brought inside 0.5-2 for the given reference surface.

    Kept as a defensive invariant guard on every `GridSpec` this module builds. It is not reachable
    through any of the three standards' *current* constructions -- see
    `_ensure_cell_aspect_in_range`'s docstring for the proof covering all three, including LM-83's
    (which used to be the one live path here, before `plan_grid`/`place_sensor_grid` were reconciled
    onto one LM-83 construction; see the module docstring). It stays because the invariant is still
    the contract every caller of `GridSpec.cell_aspect` relies on, and a guard that could never fire
    is cheaper than a caller assumption that silently stops being true.
    """

    code = "GRID_CELL_ASPECT_OUT_OF_RANGE"
    field = "polygon"


class PolygonError(GridError):
    """A polygon this module will not clip a grid against: too few vertices, self-intersecting, or
    degenerate (a circumscribing rectangle with zero width or depth -- collinear or coincident
    vertices, no interior at all).

    `ARGUMENT_OUT_OF_RANGE` rather than a dedicated code: the register (docs/tool-surface.md) allocates
    it for exactly "an argument rejected on its face, before any work was attempted", the same
    reasoning `rad_engine.project.ProjectNameError` already relies on for a bad project name. None of
    these polygons have a well-defined interior, so there is nothing downstream to attempt.
    """

    code = "ARGUMENT_OUT_OF_RANGE"
    field = "polygon"


class ArgumentOutOfRangeGridError(GridError):
    """A reference-surface dimension that is zero or negative.

    Code-less, for `envelope.py`'s stated rule -- *"a refusal the register has no code for carries
    its reason as prose rather than a fabricated code"* -- and the register allocates nothing for
    this condition. What matters is the **type**: `server.py` catches `GridError` so that a refusal
    reaches the client in the tool-result channel with its envelope intact (check 0.4), and a bare
    `ValueError` is `GridError`'s base rather than a member of the family, so it walked straight
    past that clause. Found by the check-0.4 sweep, which is the point of having one.
    """

    field = "width"


class HeightOutOfRangeError(GridError):
    """A reference-plane `height` below zero or not a finite number (ruling R783, owner decision F2
    of 2026-10-04).

    `height` is measured above the floor polygon (R763), so a negative one puts every point below
    the floor it names: measured before R783, `height=-0.5` over a polygon at z = 0 placed the
    points at -0.5 and nothing downstream noticed. Zero is a height (a floor-level grid), and is
    accepted. A non-finite height reached `ProvenanceValue`'s validator as a pydantic
    `ValidationError`, which is not a `GridError`, so the wire got an uncoded error (check 0.4).
    `ARGUMENT_OUT_OF_RANGE` with `field="height"`, `PolygonError`'s reasoning: a value refused on
    its face, before any work.
    """

    code = "ARGUMENT_OUT_OF_RANGE"
    field = "height"


class GridEmptyError(GridError):
    """No sensor points survived placement and clipping.

    Controller ruling, fix round 1 of Task 1's review: an empty grid is a refusal, not a degraded
    success. A `PlacedGrid` with `points=()` is an empty `.pts` file and a study over zero sensors;
    reporting `points_clipped == points_total` as an `info` advisory on an otherwise-`refused=false`
    result invites a caller to treat "clipped every candidate point" the same as "clipped a few
    stray ones", when the two need entirely different responses (a differently-shaped or larger
    polygon vs. nothing). `GRID_EMPTY` is allocated `error` for exactly this
    (docs/tool-surface.md#advisory-code-register).
    """

    code = "GRID_EMPTY"
    field = "polygon"


class FloorNotLevelError(GridError):
    """A room outline whose vertices do not share one z (ruling R763).

    `height` is a height **above the floor polygon** -- EN 17037 Annex B.2's "0.85 m above floor",
    and the label `place_sensor_grid` already put on the result -- so the polygon has to be one
    floor. A sloped or stepped outline has no single plane to measure from, and is refused whether
    or not `height` was given (owner decision D4 of 2026-10-02). Before R763 every vertex's z was
    dropped and the plane sat at `height` above z = 0: a room at z = 3.5 was gridded 2.65 m below
    its own floor (design F6).
    """

    code = "GRID_FLOOR_NOT_LEVEL"
    field = "polygon"


# --------------------------------------------------------------------------------------------
# Grid planning -- existing surface, extended
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class GridSpec:
    """A planned grid. Lengths in metres."""

    standard: Standard
    max_cell: float
    """Maximum permitted cell size ``p``."""
    spacing_x: float
    spacing_y: float
    n_x: int
    n_y: int
    governing_dimension: float
    """The ``d`` actually used -- the shorter side when the aspect ratio forced a swap."""
    aspect_swapped: bool
    """True when a reference-surface aspect ratio >= 2 moved ``d`` to the shorter side."""

    @property
    def n_points(self) -> int:
        return self.n_x * self.n_y

    @property
    def cell_aspect(self) -> float:
        """Cell length:width. Every standard here requires 0.5 <= this <= 2."""
        lo, hi = sorted((self.spacing_x, self.spacing_y))
        return hi / lo if lo else math.inf


def _ensure_cell_aspect_in_range(spec: GridSpec) -> None:
    """Refuse a `GridSpec` whose cell aspect ratio cannot be brought inside 0.5-2.

    **Why EN 12464 and EN 17037 can never actually hit this.** Write ``s`` for the spacing on the
    governing axis and ``r`` for ``other / s``, where ``other`` is the non-governing side. Because
    `n_other = ceil(other / s)`, the achieved `spacing_other` satisfies
    `spacing_other <= s` always, so `cell_aspect = ceil(r) / r`, which exceeds 2 only when `r < 0.5`.
    The aspect-ratio swap rule bounds `r` away from that: unswapped, `other > d/2 >= s/2` (ratio < 2
    is exactly "the short side is more than half the long side", and `s <= d`); swapped,
    `other >= 2*d >= 2*s`. Either way `r > 0.5` always, so `cell_aspect < 2` always.

    **Why LM-83 can no longer hit it either.** `_lm83_grid_spec` fixes `spacing_x == spacing_y ==`
    the standard's own cap on *every* call, regardless of `width`/`depth` -- see `_lm83_axis`'s
    docstring. `cell_aspect` is the ratio of those two, so it is exactly 1.0 always. This used to be
    the one reachable branch (an 0.2 m x 30 m corridor, sized by the *old* cell-centre-style LM-83
    construction `plan_grid` no longer uses, would have given a ~3.0 aspect); reconciling `plan_grid`
    and `place_sensor_grid` onto one LM-83 construction (fix round 1, finding I1) removed that branch
    along with the disagreement it was part of.

    **So this is now a pure invariant guard, reachable by no input through any of this module's
    current constructions**, kept because `GridSpec.cell_aspect` being in `[0.5, 2]` is a contract
    every caller relies on and a guard that cannot fire today is a cheap insurance policy against a
    future construction change reintroducing a violation -- not a claim that one exists now.
    `TestCellAspectGuardIsStructural` in tests/test_grids.py exercises it directly, against a
    hand-built out-of-range `GridSpec` no public call can produce, rather than against a fixture that
    no longer exists.
    """
    aspect = spec.cell_aspect
    if aspect > _CELL_ASPECT_MAX + _CELL_ASPECT_TOLERANCE or aspect < _CELL_ASPECT_MIN - _CELL_ASPECT_TOLERANCE:
        raise CellAspectOutOfRangeError(
            f"{spec.standard.value} grid over a {spec.governing_dimension:g} m reference dimension "
            f"gives spacing {spec.spacing_x:.4f} x {spec.spacing_y:.4f} m, a cell aspect ratio of "
            f"{aspect:.3f} -- outside the 0.5-2 every standard here requires. The reference surface "
            "is too elongated for this grid to be brought near-square; there is no smaller cell size "
            "that fixes it, since the ratio is between the two axis spacings, not against the cap."
        )


# --------------------------------------------------------------------------------------------
# LM-83's own placement rule -- wall offset, not cell centres. Defined before plan_grid/
# place_sensor_grid because both now call into it for LM-83's sizing (fix round 1, finding I1).
# --------------------------------------------------------------------------------------------


def _lm83_axis(length: float) -> tuple[float, int, float]:
    """One axis of an LM-83 grid: ``(spacing, n_points, offset)``.

    ch04's rule is three numbers, not a formula: spacing <= 0.6 m (the stricter of ch04's two
    readings of its own bound -- see `_LM83_MAX_SPACING_M`), offset 12-24 in (0.3048-0.6096 m) from
    the walls, "grids preferably centred on the space". This picks the fixed pitch `PITCH`
    (never finer, satisfying "<=" at the boundary) and centres whatever margin is left over -- which
    the two bounds below prove always keeps that margin inside the wall-offset band, with no case
    analysis needed on `length`.

    Let ``PITCH = _LM83_MAX_SPACING_M`` (0.6), ``MIN = LM83_WALL_OFFSET_MIN_M`` (0.3048),
    ``WALL_MAX = LM83_WALL_OFFSET_MAX_M`` (0.6096) -- **three independent constants as of ruling
    R22**, not two, since `PITCH` stopped equaling `WALL_MAX` the day the pitch moved off the 24in
    reading. ``k = floor((length - 2 * MIN) / PITCH)``, ``remaining = length - k * PITCH``,
    ``offset = remaining / 2``.

    - **Lower bound.** By the definition of floor, ``k * PITCH <= length - 2 * MIN``, i.e.
      ``remaining >= 2 * MIN``, i.e. ``offset >= MIN``. Holds for any positive `PITCH` -- this half
      of the proof never depended on the relationship between `PITCH` and `WALL_MAX`.
    - **Upper bound.** Also by floor, ``length - 2 * MIN < (k + 1) * PITCH``, i.e.
      ``remaining < 2 * MIN + PITCH``, i.e. ``offset < MIN + PITCH / 2``. Unlike the old (0.6096 m)
      pitch, `MIN + PITCH / 2` is no longer *exactly* `WALL_MAX` -- it is
      ``0.3048 + 0.6 / 2 == 0.6048``, strictly less than `WALL_MAX` (0.6096) rather than equal to
      it. So ``offset < 0.6048 < WALL_MAX`` still holds, with room to spare rather than landing
      exactly on the boundary.

    So ``MIN <= offset < 0.6048 < WALL_MAX`` for every ``length > 2 * MIN`` -- true of any room
    LM-83 is applied to. (A room dimension at or below 24 in cannot carry the standard's own margin
    on both sides at once; that is a room too small for this rule, not a bug in it, and is out of
    this module's scope -- `k` floors at 0 rather than going negative, so the one point placed lands
    at the centre instead of raising.)

    Fixing `spacing` at `PITCH` on *every* call, rather than deriving it from `length` the way the
    two formula standards' cell-centre spacing is derived, is what keeps `plan_grid`'s LM-83 counts
    and `place_sensor_grid`'s LM-83 placement in agreement for the same rectangle: both go through
    this function for both axes, so there is exactly one LM-83 sizing rule rather than two that
    happen to both be spec-compliant while disagreeing with each other (fix round 1, finding I1).
    Cell-centre placement was tried first and rejected for LM-83 specifically because it does not
    keep this promise *and* under-shoots the 12in floor: on the 12x15 m office fixture, cell-centre
    spacing at the (then) 0.6096 m pitch comes out at exactly 0.6 m, an offset of 0.3 m -- 4.8mm
    under 0.3048 m.
    """
    k = max(0, math.floor((length - 2 * LM83_WALL_OFFSET_MIN_M) / _LM83_MAX_SPACING_M))
    remaining = length - k * _LM83_MAX_SPACING_M
    offset = remaining / 2.0
    return _LM83_MAX_SPACING_M, k + 1, offset


def _lm83_grid_spec(width: float, depth: float) -> GridSpec:
    """The `GridSpec` for an LM-83 grid over a `width` x `depth` rectangle.

    The single source of truth for LM-83 sizing: `plan_grid` calls this directly, and
    `_lm83_grid` (the placement path) calls it too, then separately asks `_lm83_axis` for the same
    two axes' offsets to build coordinates. Same inputs, same function, so the two paths cannot
    independently drift the way `plan_grid`'s old formula-derived LM-83 branch and this module's
    placement did before fix round 1.
    """
    spacing_x, n_x, _ = _lm83_axis(width)
    spacing_y, n_y, _ = _lm83_axis(depth)
    spec = GridSpec(
        standard=Standard.LM_83,
        max_cell=_LM83_MAX_SPACING_M,
        spacing_x=spacing_x,
        spacing_y=spacing_y,
        n_x=n_x,
        n_y=n_y,
        governing_dimension=max(width, depth),
        aspect_swapped=False,
    )
    _ensure_cell_aspect_in_range(spec)
    return spec


def max_cell_size(standard: Standard, width: float, depth: float) -> tuple[float, float, bool]:
    """Return ``(p, d, aspect_swapped)`` -- the maximum cell size for a reference surface.

    ``p = C * 5 ** log10(d)``, capped at 10 m.

    ``d`` is the longer side, *unless* the longer:shorter ratio is >= 2, in which case the
    standards switch to the shorter side. EN 12464-2 phrases it as "d is the longer dimension
    if the ratio is less than 2, otherwise the shorter"; EN 17037 B.2 phrases it as "if the ratio
    is 2 or more then d becomes the shorter dimension". Same rule.

    LM-83 does not derive a size -- it fixes spacing at <= 24in, so ``d`` is returned as the longer
    side purely for reporting. (`plan_grid` does not use this function's `p` for LM-83's own sizing
    -- see `_lm83_grid_spec` -- but still calls this first, for the width/depth validation below.)
    """
    if not math.isfinite(width) or not math.isfinite(depth):
        # **Checked before `<= 0`, because `nan <= 0` is `False` and the comparison below lets it
        # through.** `sensor_grid(action="plan", standard="en17037", width=1e999, depth=4)` needs no
        # project at all, and pydantic-core's JSON parser reads `1e999`, `Infinity` and `NaN` as
        # floats for a `float` field -- so this is the cheapest of the branch's four
        # `ArithmeticError` escapes to reach. Unguarded, `inf` reached `math.ceil(d / p)` and `nan`
        # reached `_lm83_axis`'s `math.floor`, both raising `OverflowError`/`ValueError` from
        # `int()`: `OverflowError` is an `ArithmeticError`, which is in **none** of the exception
        # families `rad_mcp.server.sensor_grid` catches, so it left the tool as
        # `isError=True` with no `structuredContent` -- the malformed envelope check 0.4 forbids and
        # ruling R38 already graded a violation. One wording for one rule: this is the same refusal
        # `lm83_grid_point_count` and `place_sensor_grid` make about a polygon's ordinates, made
        # here about the two arguments that name a rectangle directly.
        raise ArgumentOutOfRangeGridError(
            f"reference surface dimensions must be finite, got {width} x {depth}. There is no cell "
            "size to derive from a dimension that is inf or nan, and the derivation reaches "
            "math.ceil as an OverflowError rather than as a refusal."
        )

    if width <= 0 or depth <= 0:
        # **A `GridError`, not a bare `ValueError`.** Found by the check-0.4 sweep added in the
        # whole-branch fix wave, not by the wave's own findings: `sensor_grid(action="plan",
        # width=0.0, depth=0.0)` validates against the published `inputSchema` and then reached a
        # real client as `isError=True` with **no `structuredContent` at all** and this sentence as
        # a raw string -- an uncaught exception escaping a tool, which is the same violation
        # `place_sensor_grid`'s malformed vertices produced and which `server.py`'s
        # `except GridError` was written to prevent. It could not catch this one, because
        # `ValueError` is `GridError`'s *base*, not its subclass.
        #
        # **Code-less deliberately**: the register allocates nothing for "the reference surface has
        # no extent", and `envelope.py`'s rule is that a refusal the register has no code for
        # carries its reason as prose rather than a fabricated code. `field="width"` names the
        # first of the two arguments, which is what makes the prose actionable.
        raise ArgumentOutOfRangeGridError(
            f"reference surface must have positive extent, got {width} x {depth}"
        )

    shorter, longer = sorted((float(width), float(depth)))

    if standard is Standard.LM_83:
        # LM-83 fixes spacing at <= 24in and has NO aspect-ratio rule, so nothing is ever
        # "swapped". Reporting swapped=True here would contradict the longer-side d we return,
        # and the caller would emit an advisory claiming the longer side is the shorter one.
        return _LM83_MAX_SPACING_M, longer, False

    swapped = (longer / shorter) >= 2.0
    d = shorter if swapped else longer

    coefficient = _COEFFICIENT[standard]
    p = coefficient * 5.0 ** math.log10(d)
    return min(p, _MAX_CELL_M), d, swapped


def plan_grid(standard: Standard, width: float, depth: float) -> GridSpec:
    """Plan a compliant grid over a ``width`` x ``depth`` rectangle.

    Point counts come from the governing dimension, then the other axis is divided to match that
    spacing as closely as possible -- which is what keeps cells near-square, as all three standards
    require (aspect 0.5-2). Raises `CellAspectOutOfRangeError` on the reference surfaces where that
    cannot be achieved -- see `_ensure_cell_aspect_in_range`.

    LM-83 is sized by `_lm83_grid_spec` -- the same function `place_sensor_grid` calls for LM-83
    placement -- rather than by the cell-centre construction below, so a "plan" preview and a
    "create" placement of the same rectangle always agree (fix round 1, finding I1).

    Note this returns *counts and spacings*, not coordinates. EN 12464 puts points at cell centres;
    LM-83 offsets them 12-24in from the walls. `place_sensor_grid` is the caller that knows the room
    polygon and does the actual placement.
    """
    p, d, swapped = max_cell_size(standard, width, depth)  # also validates width, depth > 0

    if standard is Standard.LM_83:
        return _lm83_grid_spec(width, depth)

    # Points along the governing dimension: the next whole number >= d/p.
    n_governing = max(1, math.ceil(d / p))
    spacing = d / n_governing

    # Which axis is governing? After the aspect-ratio swap, d is whichever side the rule chose.
    # When width == depth the choice is arbitrary, so bias to width for determinism.
    governing_is_width = math.isclose(d, width)
    other = depth if governing_is_width else width

    # ceil, never round: rounding down would make the other axis' spacing exceed the standard's
    # maximum cell size, which is precisely the thing p is there to bound.
    n_other = max(1, math.ceil(other / spacing))
    spacing_other = other / n_other

    if governing_is_width:
        n_x, n_y = n_governing, n_other
        spacing_x, spacing_y = spacing, spacing_other
    else:
        n_x, n_y = n_other, n_governing
        spacing_x, spacing_y = spacing_other, spacing

    spec = GridSpec(
        standard=standard,
        max_cell=p,
        spacing_x=spacing_x,
        spacing_y=spacing_y,
        n_x=n_x,
        n_y=n_y,
        governing_dimension=d,
        aspect_swapped=swapped,
    )
    _ensure_cell_aspect_in_range(spec)
    return spec


# --------------------------------------------------------------------------------------------
# Reference-plane height, with provenance
# --------------------------------------------------------------------------------------------


def _resolve_height(
    standard: Standard,
    height: float | None,
    *,
    provenance: Provenance,
    source: str | None,
    note: str | None,
    lm83_edition: Lm83Edition = DEFAULT_LM83_EDITION,
) -> ProvenanceValue:
    """The reference-plane height for `standard`, labelled.

    Under LM-83 an absent height is `lm83_reference_height(lm83_edition)` (R864): 0.8 m under
    LM-83-12, 0.76 m under LM-83-22. The edition never touches a height the caller gave. It is
    not validated here (`lm83_reference_height` reads any value but "LM-83-22" as LM-83-12), so a
    tool passes it only after its schema has refused anything outside `LM83_EDITIONS`.

    Mirrors `rad_core.manifest.resolve_ground_reflectance`: a value the caller gave is recorded
    under whatever provenance it states (default `assumed` -- "user gave it, unmeasured"), and only
    an *absent* value reaches for a standard's own default. EN 12464-1 has no default to reach for
    -- see `TaskHeightRequiredError`. A given height that is not finite or is below zero is refused,
    `HeightOutOfRangeError` (R783); zero is accepted.
    """
    if height is not None:
        value = float(height)
        if not math.isfinite(value) or value < 0.0:
            # Worded per case (final review C-M4): the below-the-floor sentence is false of nan/inf.
            why = (
                "A negative height puts every point below the floor its polygon names"
                if math.isfinite(value)
                else "It is not a finite number, so no point can be placed at it"
            )
            raise HeightOutOfRangeError(
                f"height={height!r} m is refused: a reference-plane height is measured above the "
                f"floor polygon and must be a finite number of zero or more. {why} (ruling R783)."
            )
        return ProvenanceValue(value=value, provenance=provenance, source=source, note=note)

    if standard is Standard.EN_12464:
        raise TaskHeightRequiredError(
            "EN 12464-1's task area may be horizontal, vertical or inclined (ch01), so there is no "
            "single reference-plane height a default could name -- pass height explicitly."
        )
    if standard is Standard.EN_17037:
        return ProvenanceValue(
            value=EN17037_REFERENCE_HEIGHT_M, provenance="default", source=EN17037_REFERENCE_HEIGHT_SOURCE
        )
    if standard is Standard.LM_83:
        value, cited = lm83_reference_height(lm83_edition)
        return ProvenanceValue(value=value, provenance="default", source=cited)
    raise AssertionError(f"unhandled standard {standard!r}")  # pragma: no cover -- Standard is exhaustive


# --------------------------------------------------------------------------------------------
# Polygon geometry -- bounds, self-intersection, point-in-polygon
# --------------------------------------------------------------------------------------------


def _bounds(polygon: Sequence[Point]) -> tuple[float, float, float, float]:
    xs = [p[0] for p in polygon]
    ys = [p[1] for p in polygon]
    return min(xs), max(xs), min(ys), max(ys)


def _polygon_is_simple(polygon: Sequence[Point]) -> bool:
    """False if any two non-adjacent edges cross. The production half of check 2.8's clipping --
    the *placement* code's own predicate. `tests/test_grids.py` re-verifies this module's clipping
    decisions with a second, differently-derived algorithm rather than calling this one, which is
    what makes that check mean something."""
    n = len(polygon)
    edges = [(polygon[i], polygon[(i + 1) % n]) for i in range(n)]
    for i in range(n):
        for j in range(i + 1, n):
            if j == i + 1 or (i == 0 and j == n - 1):
                continue  # adjacent edges share a vertex by construction -- not a crossing
            if _segments_intersect(*edges[i], *edges[j]):
                return False
    return True


def _as_points(polygon: Sequence[Sequence[float]]) -> list[Point]:
    """`polygon` as `(x, y)` floats, refusing anything that cannot bound a region.

    Used by `polygon_area_m2`, which `grid_polygon_coverage` in turn takes its denominator from, so
    the two can never disagree about which polygons they will answer for. That indirection is the
    stronger form of the guarantee this docstring used to claim from two direct callers: as of Phase
    7d-1 Task 17 there is one normaliser, reached once, and coverage divides by the very number the
    analysis area is published from rather than by a second reading of the same outline.

    **A malformed vertex is a `PolygonError` too, not a bare `ValueError`.** Both public functions
    document `PolygonError` as the refusal for a polygon this module will not answer for, and
    `float("a")` raises `ValueError` while `PolygonError` is a `ValueError` *subclass* -- so a caller
    catching the subclass, which is the documented contract, does not catch the base and the
    exception escapes its own error family. That is not hypothetical here: `results_tool` reads
    these polygons out of a **manifest**, which R50's posture holds untrusted on every action that
    touches it, so `[["a", "b"], ...]` is reachable by exactly the route the two tested refusals
    are. Every way indexing-then-`float` can fail over JSON-decoded data is normalised here:
    `ValueError` (not a number), `TypeError` (not a number's type), `IndexError` (a vertex with one
    ordinate) and `KeyError` (a vertex that is an object rather than a pair).

    **A non-finite ordinate is refused here too, and here is the only place it needs to be.**
    `1e999`, `Infinity` and `NaN` are all accepted by pydantic-core's JSON parser for a declared
    `list[list[float]]`, so every polygon that reaches this module -- from the wire or read back out
    of a manifest, which R50 holds untrusted -- can carry one. Before 2026-08-31 the check existed
    on `lm83_grid_point_count` alone, and its own docstring named the gap on `place_sensor_grid` and
    deferred it; the whole-branch security review then measured three distinct consequences of the
    gap, on three different functions:

    - `place_sensor_grid` with an `inf` ordinate reached `_lm83_axis`'s `math.floor` as an
      `OverflowError` -- an `ArithmeticError`, in **none** of the exception families
      `rad_mcp.server.sensor_grid` catches -- and left the tool as `isError=True` with no
      `structuredContent` at all, the malformed envelope check 0.4 forbids;
    - `place_sensor_grid` with a `NaN` vertex **silently succeeded**: `nan` compares false against
      every bound, so `_bounds` and `_point_in_polygon` ignored the vertex and 48 points were
      placed, 0 clipped, with zero advisories -- and the manifest then recorded the vertex as
      `[null, null]`, so the record of what the grid was placed over was not what it was placed
      over;
    - `polygon_area_m2` returned `nan`, which `rad_mcp.envelope.emit` published as `null` against a
      required, non-nullable `float` (ruling R200).

    Normalising it in the one place every polygon passes through is the same argument the paragraph
    above makes about malformed vertices: one wording for one rule, and no public entry point can
    be added later that forgets it. It does **not** subsume `polygon_area_m2`'s own finiteness check
    on its *result* -- four finite `1e308` coordinates overflow the shoelace sum to `nan` -- so that
    one stays where it is.
    """
    try:
        vertices = [(float(v[0]), float(v[1])) for v in polygon]
    except (TypeError, ValueError, IndexError, KeyError) as exc:
        raise PolygonError(
            "a room polygon must be a list of [x, y] vertices in metres, and this one is not: "
            f"{type(exc).__name__}: {exc}."
        ) from exc
    if len(vertices) < 3:
        raise PolygonError(
            f"a room polygon needs at least 3 vertices to enclose an area, got {len(vertices)}."
        )
    if not all(math.isfinite(ordinate) for vertex in vertices for ordinate in vertex):
        raise PolygonError(
            "a room polygon must be finite to grid, and this one has a coordinate that is not "
            "(inf, -inf or nan). There is no circumscribing rectangle to divide into cells, and an "
            "infinite span reaches `math.floor` as an OverflowError rather than as a refusal."
        )
    return vertices


def polygon_floor_z(polygon: Sequence[Sequence[float]]) -> float:
    """The z of a level room outline (ruling R763): each vertex's third ordinate, or 0.0 for a
    vertex given as `[x, y]`.

    Call it after `_as_points`, which owns the arity and the x/y checks. Refuses a z that is not a
    finite number (`PolygonError`, the same refusal `_as_points` gives a bad x or y) and a spread
    of more than `rad_core.geometry.COINCIDENCE_TOLERANCE_M` (`FloorNotLevelError`). Returns the
    lowest z; inside the 1 mm band the choice moves the plane by less than a millimetre.

    A polygon that gives z on some vertices and not others is judged as written: the bare
    vertices are at 0, so `[[0, 0], [4, 0, 3.5], ...]` is not level and is refused, rather than
    the missing z being taken from its neighbours.
    """
    zs: list[float] = []
    for vertex in polygon:
        if len(vertex) < 3:
            zs.append(0.0)
            continue
        try:
            z = float(vertex[2])
        except (TypeError, ValueError, KeyError, IndexError) as exc:
            raise PolygonError(
                f"a room polygon vertex's third ordinate is its z in metres, and {vertex[2]!r} is "
                "not a number."
            ) from exc
        if not math.isfinite(z):
            raise PolygonError(
                "a room polygon must be finite to grid, and a vertex's z is not (inf, -inf or nan)."
            )
        zs.append(z)
    low, high = min(zs), max(zs)
    if high - low > COINCIDENCE_TOLERANCE_M:
        raise FloorNotLevelError(
            f"the polygon's vertices are not level: their z runs from {low:g} to {high:g} m, "
            f"{high - low:g} m apart, more than {COINCIDENCE_TOLERANCE_M:g} m. A sensor grid's "
            "height is measured above the floor polygon, and a sloped or stepped outline has no "
            "one floor to measure it from. Grid each level as its own polygon with one z, or give "
            "[x, y] vertices for a floor at z = 0."
        )
    return low


def refuse_extra_ordinates(polygon: Sequence[Sequence[float]]) -> None:
    """Refuse a vertex with more than three numbers (ruling R772, owner decision E7 of 2026-10-03).

    A room polygon vertex is `[x, y]` or `[x, y, z]`: `_as_points` reads the first two and
    `polygon_floor_z` the third, and nothing reads a fourth, so before R772 one was dropped without
    a word. Called by `sensor_grid(action="create")` only, not inside `_as_points`, so a polygon
    already recorded in a manifest is not newly refused by the tools that read it. A vertex that is
    not a sequence is left to `_as_points`, which refuses it in its own words. A string or bytes
    vertex is refused here (R788): it has a length, so it was reported as "4 numbers" with its
    characters listed, and a digit string such as `"4000"` was read by `_as_points` digit by digit.
    """
    for index, vertex in enumerate(polygon):
        if isinstance(vertex, (str, bytes)):
            raise PolygonError(
                f"a room polygon vertex is [x, y] or [x, y, z] in metres, and vertex {index} is a "
                f"string, not a list of 2 or 3 numbers: {vertex!r}."
            )
        try:
            count = len(vertex)
        except TypeError:
            continue
        if count > 3:
            raise PolygonError(
                f"a room polygon vertex is [x, y] or [x, y, z] in metres, and vertex {index} has "
                f"{count} numbers: {list(vertex)!r}. Nothing reads a fourth number, so it is refused "
                "rather than dropped."
            )


def recorded_floor_mismatch(
    polygon: object, point_zs: Sequence[float], *, height: object = None
) -> str | None:
    """Why a registered grid's points are not on the floor its record names, or `None` (R776).

    Ruling R763 placed points at `height` above the polygon's own z; a grid created before it over
    a polygon at z = 3.5 kept them at `height` above z = 0, so every number from it is for points
    outside the room. The record holds the polygon (`registered_by.arguments["polygon"]`), so the
    mismatch is detectable without a version stamp:

    - any record, floor more than 1 mm above 0: refused when the lowest point is below the floor.
      A grid created between R763 and R783 with a negative `height` over such a floor is refused here
      too (`create` refuses a negative `height` since R783; until then it did not, measured
      2026-10-03);
    - a record that carries `height` (zoned grids, and unzoned grids created since R784):
      refused when the lowest point is not at `floor + height`, within the band, whatever the
      floor's z -- so a grid created before R763 over a floor below 0, or between 0 and its
      height, is refused too (final review, 2026-10-04).

    An unzoned record created before R784 carries no `height`, and a guessed one would refuse a
    correct grid created with another, so for it the first rule is all: a grid created before R763
    over a floor below 0, or over a floor between 0 and its height (its points then sit above that
    floor), is not detected. *(Corrected 2026-10-04, Phase 19, R784: it said "An unzoned record
    carries no `height`"; `sensor_grid(action="create")` records the resolved height since R784.)*

    The floor is the lowest vertex z (0 for `[x, y]`), not `polygon_floor_z`, so a non-level outline
    recorded before R763 is judged rather than refused. A polygon that cannot be read, or no points,
    is not judged: each reader handles a bad record its own way. The tolerance is
    `COINCIDENCE_TOLERANCE_M`, the level band.
    """
    if not polygon or not point_zs:
        return None
    try:
        zs = [float(vertex[2]) if len(vertex) >= 3 else 0.0 for vertex in polygon]  # type: ignore[union-attr]
    except (TypeError, ValueError, IndexError, KeyError):
        return None
    if not zs or not all(math.isfinite(z) for z in zs):
        return None
    floor = min(zs)
    lowest = min(point_zs)
    if floor > COINCIDENCE_TOLERANCE_M and lowest < floor - COINCIDENCE_TOLERANCE_M:
        return (
            f"its points sit as low as z = {lowest:g} m, below the floor its recorded polygon names "
            f"(z = {floor:g} m). A grid created before ruling R763 placed its points above z = 0 "
            "rather than above its polygon's own floor, and one created before R783 with a negative height sits "
            "below that floor too."
        )
    if (isinstance(height, (int, float)) and not isinstance(height, bool) and math.isfinite(height)
            and abs(lowest - (floor + height)) > COINCIDENCE_TOLERANCE_M):
        return (
            f"its lowest point sits at z = {lowest:g} m, not at its recorded height ({height:g} m) "
            f"above the floor its recorded polygon names (z = {floor:g} m, so z = "
            f"{floor + height:g} m). A grid created before ruling R763 placed its points above "
            "z = 0 rather than above its polygon's own floor."
        )
    return None


def polygon_area_m2(polygon: Sequence[Sequence[float]]) -> float:
    """The area a recorded room polygon encloses, in **square metres**, by the shoelace formula.

    This is the quantity IES LM-83-12 section 2.2.6's blind-trigger bands are keyed on -- once
    through `M2_TO_SQFT`, since the table is in square feet and every polygon this repo records is
    in metres. `sensor_grid(action="create")` writes that polygon into the grid's `InputRecord`
    (`registered_by.arguments["polygon"]`) and **no manifest field records a grid's spacing**, so
    the polygon is the only thing an analysis area can be derived from at read time. Section 2.2.6
    wants the area of the *space* in any case, not of the sensor cloud: it requires grids to span
    the whole space, and LM-83's own 12-24 in wall offset (ch04:47) guarantees the cloud is smaller.
    `grid_polygon_coverage` below is how the gap between the two is reported.

    **Absolute, and that is load-bearing.** A signed shoelace sum is negative for a clockwise
    polygon, so a signed area would make which band applies -- and therefore whether the blinds
    close in a given hour -- depend on which way round the room happened to be drawn. Both winding
    orders are asserted in `tests/test_grids.py::TestPolygonArea`. Non-convex outlines are handled
    by the same sum, which is the whole reason this is a shoelace rather than the bounding box
    `point_in_time_tool._room_footprint` computes for a different purpose (depth-from-facade); that
    box is 16 m^2 where this is 12 m^2 on the L-shaped case, and the two are not interchangeable.

    Raises `PolygonError` for fewer than 3 vertices, and for a polygon that encloses no area at all
    (collinear or coincident vertices -- which have a perfectly good bounding box). Returning 0.0
    for the latter would put every such grid in the strictest `< 200 sf` row off a number that
    measured nothing, and the whole point of refusing an unknown area is that a wrong band moves
    the trigger by a factor of ten.
    """
    vertices = _as_points(polygon)

    twice_signed = 0.0
    for index, (x0, y0) in enumerate(vertices):
        x1, y1 = vertices[(index + 1) % len(vertices)]
        twice_signed += x0 * y1 - x1 * y0
    signed_area = twice_signed / 2.0

    # **The sum can be non-finite even though every ordinate is.** `_as_points` refuses `inf` and
    # `nan` on the way in, and that is not enough: four *finite* coordinates of order `1e308`
    # overflow `x0 * y1` to `inf`, and `inf - inf` is `nan` -- measured on
    # `[[0,0],[1e308,1e308],[-1e308,1e308],[1e308,-1e308]]`, which returned `nan` from a polygon
    # nothing on the way in could fault. That `nan` is ruling R200: `rad_mcp.envelope.emit` handed
    # it to `structuredContent` as `null` against `area_m2`, declared a **required, non-nullable**
    # `float`, while the mirrored text carried the literal token `NaN`, which RFC 8259 does not
    # define -- so the two carriers `emit` promises are the same JSON were not. It also chose a
    # band: `nan < 1000` is `False`, so a `nan` area silently took LM-83's `>= 1000 sf` row.
    #
    # Refused rather than clamped, for the same reason the degenerate case below is: a band decided
    # off a number that measured nothing moves the blind trigger by a factor of ten.
    if not math.isfinite(signed_area):
        raise PolygonError(
            f"the room polygon's shoelace sum is {signed_area}, not a finite area -- its "
            "coordinates are finite but large enough to overflow the products the formula takes. "
            "There is no analysis area to derive from it, and publishing a non-finite number would "
            "reach the wire as JSON null against a field declared a required float."
        )

    # A magnitude test, deliberately blind to sign: it must refuse a degenerate polygon of *either*
    # winding without also being the `abs` the returned value depends on. Those are two different
    # jobs and folding them into one call would make the winding-order assertion below unfalsifiable
    # -- drop the `abs` on the return and a clockwise room would raise here instead of coming back
    # negative, which is a refusal where the test is looking for a wrong number.
    if abs(signed_area) <= _GEOM_EPS:
        raise PolygonError(
            "the room polygon encloses no area -- its vertices are collinear or coincident. "
            "There is no analysis area to derive from it, and reporting 0 sf would put the grid "
            "in LM-83's strictest band off a number that measured nothing."
        )

    return abs(signed_area)


def _observed_pitch(values: Sequence[float]) -> float | None:
    """The spacing between adjacent sensor positions along one axis, or `None` for a single one.

    `grid_polygon_coverage` needs a cell size and is given only points, so the pitch is read back
    off the points themselves: the mean gap between adjacent **distinct** positions,
    `(last - first) / (count - 1)`. For a grid placed on a lattice -- which is every grid this
    module places, and the shape `.pts` files carry -- every gap equals the pitch, so the mean is
    the pitch exactly.

    **For a hand-built cloud it is the span over the count, and that is a weaker claim than "the
    cell size".** It is the mean of the gaps, not the modal or the smallest one, so a single stray
    sensor stretches it: `_observed_pitch([0, 1, 2, 3, 100])` is **25.0**, not the 1.0 four of the
    five points agree on. That direction is optimistic -- a larger cell reads as more floor sampled
    -- which is the posture `grid_polygon_coverage` documents and caps at 1.0, and it is the safe
    direction for a measure whose entire defect history is false positives. Pinned in
    `tests/test_grids.py::TestObservedPitch` so the choice of statistic is a decision rather than an
    accident.

    **Distinct with a tolerance, not by `set`.** Positions arrive as floats out of a `.pts` file or
    a JSON manifest, and two sensors nominally in the same column can differ in the last bit. On
    `set` equality that column would count as *two*, the mean gap would come out at ~1e-16, and the
    grid would read as covering essentially nothing -- a zero-coverage answer manufactured by
    floating point. `_GEOM_EPS` is the same slack the polygon geometry above uses.
    """
    ordered = sorted(float(v) for v in values)
    distinct: list[float] = []
    for value in ordered:
        if not distinct or value - distinct[-1] > _GEOM_EPS:
            distinct.append(value)
    if len(distinct) < 2:
        return None
    return (distinct[-1] - distinct[0]) / (len(distinct) - 1)


def grid_spacing_m(points: Sequence[Sequence[float]]) -> tuple[float, float]:
    """`(dx, dy)`, the grid's row and column spacings in metres, read back off its points.

    `_observed_pitch` on each axis; a single-axis grid takes a **square** cell from the axis that
    has a pitch (see `grid_polygon_coverage` for why that cell is derived, not assumed). The one
    reader of a grid's cell size, shared by `grid_polygon_coverage` and by the LM-83-22 glossary's
    "points times the spacing" analysis area (R864, `lm83_points_area_sf`), so the two cannot read
    two different cells off one grid.

    Raises `ValueError` for an empty point list and for points that occupy a single position on
    both axes, which support no spacing at all.
    """
    placed = [(float(p[0]), float(p[1])) for p in points]
    if not placed:
        raise ValueError("the grid has no sensor points, so it has no spacing to read.")
    pitch_x = _observed_pitch([p[0] for p in placed])
    pitch_y = _observed_pitch([p[1] for p in placed])
    if pitch_x is None:
        pitch_x = pitch_y
    if pitch_y is None:
        pitch_y = pitch_x
    if pitch_x is None or pitch_y is None:
        raise ValueError(
            f"the grid's {len(placed)} sensor point(s) occupy a single position, so there is no "
            "spacing to read a cell size from."
        )
    return pitch_x, pitch_y


def grid_polygon_coverage(
    points: Sequence[Sequence[float]], polygon: Sequence[Sequence[float]]
) -> float:
    """What fraction of `polygon`'s **area** the placed sensor `points` actually sample.

    Each sensor stands for one cell of the grid it was placed on -- `dx` by `dy`, centred on the
    point -- so the floor area a grid samples is `len(points) * dx * dy`, and this is that over the
    polygon's own shoelace area (`polygon_area_m2`, the same call and therefore the same number the
    analysis area is published from). The cell size is read back off the points by
    `_observed_pitch`, because points are all this function is given.

    Judged against `GRID_POLYGON_COVERAGE_MIN`, which carries the reasoning and the ruling: a grid
    covering materially less than its polygon **advises, never refuses**. That is still true, and it is a statement about *this* measure:
    `lm83_grid_point_density` below is a different measure over different inputs -- **area sampled**
    here, **point count** there -- and it is that one, never this one, that gates
    `results(action="blind_schedule")`'s refusal (ruling R171). A grid can read 0.495 here and
    1.000 there.

    **Neither side is a bounding box, and that is the change** (Phase 7d-1 Task 17, ruling R160).
    This was a box-over-box ratio until then, and its lowest observable value was **0.0**: a grid
    whose points share one x has a zero-area box, so a single column of sensors running the length
    of a corridor read as covering none of it. No positive floor can be set under a measure that
    bottoms out at zero for a grid that is doing its job, and every correctly-inset LM-83 grid in a
    small room read low for the same reason (a 3 x 3 m room measured 0.360, a 2.4 x 20 m room
    0.480). Those read 0.640 and 0.743 here.

    **And it is not a box over an *area* either**, which is the form this docstring used to argue
    against and the argument still holds: the grid's bounding box spans an L-shaped room's notch
    while the shoelace area does not, so box-over-area reads such a grid as covering *over* 100 %.
    A sum of placed cells cannot, because a cell only exists where a point was placed, and
    `place_sensor_grid` clips points to the polygon -- the L-shaped room in
    `tests/test_grids.py::TestGridPolygonCoverage` reads 0.810 with the notch counted on both sides
    of the ratio.

    **A single-axis grid is measured with a square cell, and that is derived, not assumed.** When
    every point shares one x (or one y) there is no gap on that axis to read a pitch from, so the
    pitch observed on the *other* axis is used for both. Every placement rule in this module makes
    that the right cell: `_lm83_grid_spec` fixes `spacing_x == spacing_y` on every call, so an
    LM-83 cell is exactly square, and `_ensure_cell_aspect_in_range` holds every other standard's
    cell between 0.5 and 2. So the 1.2 x 40 m corridor -- 66 sensors in one column, which is what
    LM-83's own 0.6 m pitch and 12-24 in offset leave in a room that narrow -- reads 0.495 rather
    than 0.0: one 0.6 m-wide line of cells down a 1.2 m-wide corridor samples very close to half
    its floor, which is the true statement the old 0.0 was not.

    **Capped at 1.0, because the cap is what makes this an upper bound rather than a guess.** The
    quantity a caller wants is the area of the cells' *intersection with the room*; summing whole
    cells over-counts wherever a cell overhangs a wall, and `min(1.0, ...)` bounds that over-count
    by the only thing that certainly bounds it -- the room's own area. The published number is
    therefore an upper bound on the fraction actually sampled, so the advisory fires only when even
    the optimistic reading is below the floor, which is the safe direction for a measure whose
    entire defect history is false positives. The cap is inert for grids this module places on a
    rectangle: LM-83's smallest wall offset (0.3048 m) exceeds half its pitch (0.3 m), so no cell of
    a placed grid crosses a wall.

    **A non-finite side of the ratio is a non-answer, not a 1.0.** `min(1.0, x / nan)` is **1.0** in
    Python -- the comparison is False and `min` keeps its first argument -- so a `nan` denominator
    would publish "fully covered", the most flattering reading there is, off an input that measured
    nothing; the same polygon read 0.01 under the bounding-box measure this replaced, which was
    obviously wrong rather than silently wrong. Both sides are therefore checked for finiteness and
    a `ValueError` raised, which is the same route to a published `null` the single-position grid
    below takes.

    **The area half of that check is now defence rather than a live path, and is kept deliberately.**
    Ruling R200 was closed on 2026-08-31: `_as_points` refuses a non-finite ordinate and
    `polygon_area_m2` refuses a shoelace sum that overflows to `nan`, so the only two ways an area
    came back non-finite are both refusals upstream now. It stays because this function's contract
    is about *its* ratio -- it takes an `area` from a call it does not own, and a guard deleted on
    the strength of a callee's current behaviour is the shape `_lm83_expected_points_or_none`'s
    docstring already names ("a promise held by a callee's current translation of its own
    arithmetic" is not a promise). The **cell-sum** half below is live and always was: it is
    computed here from the points.

    Raises `PolygonError` (via `polygon_area_m2`) for a polygon that encloses no area to divide by,
    and `ValueError` for an empty point list -- an empty grid is `GridEmptyError`'s refusal at
    placement time, not a coverage of zero -- for a grid whose points occupy a **single** position
    on both axes, which supports no cell size at all, and for a non-finite area or cell sum. Those
    are not zero coverage either: they are unknowns, and
    `rad_mcp.results_tool._grid_polygon_coverage_or_none` publishes `null` for each of them.
    """
    area = polygon_area_m2(polygon)
    if not math.isfinite(area):
        raise ValueError(
            f"the polygon's area came out as {area}, not a finite number of square metres, so no "
            "fraction of it can be reported. That is an unknown coverage, not a full one -- and a "
            "full one is what a NaN denominator would otherwise produce, since min(1.0, x / nan) "
            "is 1.0."
        )

    placed = [(float(p[0]), float(p[1])) for p in points]
    if not placed:
        raise ValueError(
            "the grid has no sensor points, so it covers nothing to measure. An empty grid is a "
            "refusal at placement time (GridEmptyError), not a coverage of zero."
        )

    # The single-axis cell is SQUARE, and `grid_spacing_m`'s fallback is that and nothing else:
    # the pitch comes from the axis that has one, never from a constant. Write `pitch_x = 0.6`
    # there and it reads identically on every LM-83 fixture (pitch 0.6), which is why
    # `TestGridPolygonCoverage::test_a_single_column_cloud_takes_its_cell_from_the_axis_that_has_one`
    # measures a hand-built column at a 1.0 m pitch, where the two forms differ by 40 %.
    # The square cell is derived, not chosen: `_lm83_grid_spec` sets `spacing_x` and `spacing_y`
    # from the same `_LM83_MAX_SPACING_M` on every call, so an LM-83 cell is exactly square, and
    # `_ensure_cell_aspect_in_range` holds every other standard's cell between 0.5 and 2.
    #
    # `grid_spacing_m` holds the fallback (R864 made it the one reader of a grid's cell size, so the
    # LM-83-22 points-times-spacing area and this measure read one cell). Its `ValueError` for a
    # single-position grid is re-raised in this function's own terms.
    try:
        pitch_x, pitch_y = grid_spacing_m(placed)
    except ValueError:
        # Only reachable when *both* pitches were None -- the single-axis case is covered in each
        # direction by `grid_spacing_m` -- so this is the single-position grid and nothing else.
        raise ValueError(
            f"the grid's {len(placed)} sensor point(s) occupy a single position, so there is no "
            "spacing to read a cell size from and no area they can be said to sample. That is an "
            "unknown coverage, not a coverage of zero."
        ) from None

    sampled_m2 = len(placed) * pitch_x * pitch_y
    if not math.isfinite(sampled_m2):
        raise ValueError(
            f"the grid's {len(placed)} sensor point(s) give a cell sum of {sampled_m2} m2, not a "
            "finite area, so there is no fraction to report. That is an unknown coverage, not a "
            "full one."
        )
    return min(1.0, sampled_m2 / area)


def lm83_grid_point_count(polygon: Sequence[Sequence[float]]) -> int:
    """How many sensor points LM-83's own placement rule puts **inside** `polygon`.

    The count `place_sensor_grid(Standard.LM_83, polygon)` produces, clipping included -- it is that
    call, not a formula that agrees with it today. Section 2.2.6's three small-area rows are absolute
    point counts calibrated on this density (see `LM83_GRID_POINT_DENSITY_MIN`), so it is the
    denominator any statement about "enough points" has to be made against.

    **Defined next to `grid_polygon_coverage` and answering a different question.** That one is a
    ratio of areas and says how much of the room's floor a grid *samples*; this one is a count and
    says how many points the standard's own placement would have put there. Neither implies the
    other: a grid can sample 49.5 % of its polygon at exactly LM-83's density (a correctly-inset
    single column in a 1.2 m corridor) or sample all of it at a twenty-fifth of that density (a 3 m
    grid over a large room).

    Forward-references `place_sensor_grid`, which is defined below with the rest of the placement
    code: this belongs beside the coverage measure it is contrasted with, and Python resolves the
    name at call time.

    Raises `PolygonError` for a polygon this module will not answer for -- fewer than three
    vertices, malformed vertices, a self-intersecting outline, a degenerate circumscribing rectangle
    -- and, additionally, for one whose circumscribing rectangle would take more than
    `MATRIX_MAX_DECLARED_POINTS` LM-83 points to grid. **That bound is not defensive habit.** Callers
    reach this with a polygon read out of a manifest, which ruling R50 holds untrusted on every
    action that touches it, and the count grows as the *product* of two spans: a 1 km square outline
    is 2.8e9 candidate points, and `place_sensor_grid` materialises a `GridPoint` for every candidate
    before clipping. The bound is checked off `n_x * n_y` -- two integers, no allocation -- so the
    refusal arrives before the allocation rather than instead of it. **`place_sensor_grid` now
    carries the same bound off the same constant** (ruling R199, 2026-08-31): until then the read
    path was bounded and the write path -- the one that allocates -- was not.

    Raises `PolygonError` for a **non-finite** coordinate, and that guard is not tidiness: an
    infinite span reaches `_lm83_axis`'s `math.floor` and raises `OverflowError`, which is an
    `ArithmeticError` and therefore in **none** of the exception families this module's callers
    catch -- `rad_mcp.results_tool._lm83_expected_points_or_none` caught `GridError`, `ValueError`,
    `TypeError`, `IndexError` and `KeyError` and the exception went straight past all five, out of
    `results(action="blind_schedule")` as an uncaught crash with no advisory (fix round 1, C1).
    `1e999` is valid JSON and Python parses it to `inf`, and every polygon this reaches is manifest
    content, which ruling R50 holds untrusted on every action that touches it. `nan` is refused by
    the same check: it reached `place_sensor_grid` and came back as `GridEmptyError` -- caught, but
    reported as *"LM-83 would place no points here"*, which is a statement about the room rather than
    about the numbers, so both non-finite cases now carry one wording for one rule.

    (The check itself now lives in `_as_points`, so `place_sensor_grid` -- which
    `sensor_grid(action="create")` calls with the caller's polygon directly -- makes the same
    refusal in the same words. The hole this paragraph used to defer was measured open on that write
    path and closed on 2026-08-31.)

    Raises `GridEmptyError` when every candidate point falls outside the polygon, which is
    `place_sensor_grid`'s own refusal: "LM-83 would place no points here" is not a count of zero.
    """
    vertices = _as_points(polygon)
    x_min, x_max, y_min, y_max = _bounds(vertices)
    width, depth = x_max - x_min, y_max - y_min
    if width > 0 and depth > 0:
        candidates = _lm83_grid_spec(width, depth).n_points
        if candidates > MATRIX_MAX_DECLARED_POINTS:
            raise PolygonError(
                f"an LM-83 grid over this polygon's {width:g} x {depth:g} m circumscribing "
                f"rectangle would be {candidates} candidate points, above the "
                f"{MATRIX_MAX_DECLARED_POINTS} this repo will size an allocation from. There is no "
                "point count to compare a grid against without building one that large first."
            )
    # A degenerate rectangle falls through deliberately: `place_sensor_grid` refuses it with the
    # `PolygonError` message written for that case, and one wording per rule is the point.
    return len(place_sensor_grid(Standard.LM_83, polygon).points)


def lm83_grid_point_density(points: int, expected_points: int) -> float:
    """What fraction of LM-83's own point count a grid of `points` sensors supplies.

    ``points / expected_points``, where `expected_points` is `lm83_grid_point_count(polygon)`'s
    answer. Judged against `LM83_GRID_POINT_DENSITY_MIN`, which carries the derivation and the
    ruling: a grid **materially sparser** than LM-83's own placement cannot be handed section
    2.2.6's small-area rows, because those rows are absolute point counts calibrated on that
    density, and applying them to a sparser grid under-closes the blinds and **overstates** sDA.

    **The expected count is passed in and not re-derived here, and that is the point of the
    signature.** This function used to take the polygon and grid it itself, which read well and was
    wrong for exactly the reason this whole measure exists: the caller that has an expected count has
    already taken it off the polygon it published the analysis area from, and re-deriving it here
    would be a **second** derivation of the one number the "one source" rule is about -- two calls
    that agree only as long as nothing between them changes, over a polygon that comes out of a
    manifest. `rad_mcp.results_tool._analysis_area` reads the polygon once, publishes both the area
    and the count off that single read, and hands the count here (fix round 1, I1: as first shipped,
    this function had no production caller at all while `_blind_schedule`'s own docstring named it as
    the measure the guard used).

    **Above 1.0 is a real answer and is never a fault.** A finer grid supplies more of the count the
    rows were calibrated on, not less, so the threshold is a floor only. It is reachable rather than
    theoretical: EN 12464's formula puts **more points** in the same room than LM-83's fixed 0.6 m
    pitch does on any reference dimension below about 6 m -- 72 against 48 on a 4 x 5 m room, 81
    against 64 at 5 x 5 m, equal at 81 apiece at 6 x 6 m, and coarser above that (100 against 169 at
    8 x 8 m). The **spacing** crossover is 4.816 m and the **count** crossover is ~6 m; this measure
    counts, so 6 m is the figure that applies to it.

    **Not `grid_polygon_coverage`.** That measures the *floor area sampled* (a ratio of areas -- the
    grid's own cells over the polygon's shoelace area); this measures *count*.
    `LM83_GRID_POINT_DENSITY_MIN` lists the measured pair of rooms on which the two disagree in both
    directions.

    `points` is the count the criterion will actually be applied to -- for
    `results(action="blind_schedule")` that is the direct matrix's own `NROWS`, which is the number
    `blinds_close` divides and compares, and not the `.pts` file's line count. The whole defect this
    measure exists for is those two being allowed to differ from the polygon that chose the row.

    Raises `ValueError` for a negative `points` -- a count cannot be negative, and returning a
    negative density would read as "sparser than nothing" -- and for an `expected_points` at or below
    zero. `lm83_grid_point_count` can never return one (`place_sensor_grid` raises `GridEmptyError`
    rather than returning an empty grid), so that second guard is about a caller that computed the
    denominator some other way, which is the caller this signature now admits. A `points` of zero is
    allowed and returns 0.0: a matrix declaring no points is a real, and maximally sparse, state.
    """
    if points < 0:
        raise ValueError(
            f"a grid cannot have {points} sensor points, so there is no density to measure."
        )
    if expected_points <= 0:
        raise ValueError(
            f"LM-83's own placement over this polygon is said to be {expected_points} point(s), "
            "which cannot be a denominator. lm83_grid_point_count never returns one -- an empty "
            "grid is GridEmptyError's refusal at placement time, not a count of zero."
        )
    return points / expected_points


def lm83_points_area_sf(n_points: int, dx_m: float, dy_m: float) -> float:
    """The analysis area LM-83-22's glossary defines -- "the number of analysis points times the
    spacing distances for the rows and columns" -- in square feet, by `M2_TO_SQFT` (R864).

    `dx_m` and `dy_m` are the grid's row and column spacings in metres. This is a count of cells,
    not the polygon's area: the two differ by whatever the wall offsets and partial cells leave out.
    """
    return n_points * dx_m * dy_m * M2_TO_SQFT

# --------------------------------------------------------------------------------------------
# Placement -- the caller that knows the room polygon
# --------------------------------------------------------------------------------------------


def _lm83_grid(width: float, depth: float) -> tuple[GridSpec, list[float], list[float]]:
    """`(spec, xs, ys)` for an LM-83 grid over a `width` x `depth` rectangle, `xs`/`ys` relative to
    the rectangle's own corner (add the polygon's bounding-box minimum to place them absolutely).

    `spec` comes from `_lm83_grid_spec` -- the same call `plan_grid` makes for LM-83 -- so this
    function only adds the offsets `_lm83_grid_spec` does not itself return.
    """
    spec = _lm83_grid_spec(width, depth)
    _, _, offset_x = _lm83_axis(width)
    _, _, offset_y = _lm83_axis(depth)
    xs = [offset_x + i * spec.spacing_x for i in range(spec.n_x)]
    ys = [offset_y + j * spec.spacing_y for j in range(spec.n_y)]
    return spec, xs, ys


@dataclass(frozen=True)
class GridPoint:
    """One sensor: position and outward direction, metres. `.pts` is six numbers per line, in
    exactly this order -- `rad_engine` writes the file, this module owns the values."""

    x: float
    y: float
    z: float
    dx: float
    dy: float
    dz: float

    def as_tuple(self) -> tuple[float, float, float, float, float, float]:
        return (self.x, self.y, self.z, self.dx, self.dy, self.dz)


@dataclass(frozen=True)
class PlacedGrid:
    """The result of `place_sensor_grid`. Never has an empty `points` -- see `GridEmptyError`."""

    standard: Standard
    spec: GridSpec
    height: ProvenanceValue
    points: tuple[GridPoint, ...]
    """The *retained* points only -- already clipped to the real polygon, and never empty. What
    `rad_engine` writes to the `.pts` file, one line per point (check 2.6's file-level assertion is
    Task 7's; this is the point list that produces it)."""
    points_total: int
    """Size of the circumscribing-rectangle grid before clipping: `spec.n_points`."""
    points_inside: int
    """`len(points)`."""
    points_clipped: int
    """`points_total - points_inside`."""
    warnings: tuple[ArtifactWarning, ...] = ()
    floor_z: float = 0.0
    """The floor polygon's z (`polygon_floor_z`, ruling R763). Every point sits at
    `floor_z + height.value`; `height` stays the height above this floor."""


def place_sensor_grid(
    standard: Standard,
    polygon: Sequence[Sequence[float]],
    *,
    height: float | None = None,
    height_provenance: Provenance = "assumed",
    height_source: str | None = None,
    height_note: str | None = None,
    normal: Sequence[float] = (0.0, 0.0, 1.0),
    lm83_edition: Lm83Edition = DEFAULT_LM83_EDITION,
) -> PlacedGrid:
    """Place a standards-compliant sensor grid over `polygon` (metres, `[x, y]` per vertex).

    Sizes the grid from `polygon`'s circumscribing rectangle (`plan_grid` for EN 12464 and
    EN 17037, `_lm83_grid` for LM-83's own wall-offset rule), places points across the full
    rectangle -- cell centres for the formula standards, the offset grid for LM-83 -- and then keeps
    only the points that fall inside the real polygon. Points in the circumscribed-but-outside
    region "are not just wrong, they are systematically bright -- they see unobstructed sky"
    (patterns.md), so they are dropped rather than reported, and `GRID_POINTS_CLIPPED` says so when
    any were. A point exactly on the polygon's boundary is treated as outside -- see
    `_point_in_polygon`.

    `height` unset resolves to the standard's own default with provenance -- EN 17037 0.85 m,
    LM-83 0.8 m, or 0.76 m with `lm83_edition="LM-83-22"` (R864) -- except EN 12464, which has none
    and refuses (`TaskHeightRequiredError`). `lm83_edition` is read for no other purpose.
    `height` given is recorded under `height_provenance` (default `assumed`, matching
    `resolve_ground_reflectance`'s own default: a value the caller supplied, unmeasured unless it
    says otherwise). `height` is measured above the polygon's own z (`polygon_floor_z`, R763).

    `normal` is the sensor direction for every point, `(dx, dy, dz)`, upward-facing by default.

    Refuses (all `GridError` subclasses, `rad_mcp.runtime.refuse_engine_error` maps each to the
    wire): a polygon with fewer than 3 vertices, one that self-intersects, or one whose
    circumscribing rectangle is degenerate, one with a non-finite ordinate, or one whose
    circumscribing rectangle would take more than `MATRIX_MAX_DECLARED_POINTS` candidate points to
    grid (`PolygonError`); EN 12464 with no `height` (`TaskHeightRequiredError`); a grid whose points
    all end up clipped, leaving none (`GridEmptyError`); a polygon whose vertices are not level
    within 1 mm (`FloorNotLevelError`, R763).
    """
    # **`_as_points`, not a second copy of its comprehension.** This was
    # `[(float(v[0]), float(v[1])) for v in polygon]`, unguarded, 200 lines below the helper
    # written on this same branch to normalise exactly these failures -- so `[[0.0],[1,1],[2,2]]`,
    # `[[],[],[]]`, `[[0,0,9],[1.0],[2,2]]` and `[[0,0],[1.0]]` all validate against the published
    # `list[list[float]]` schema and then reached the wire as an uncaught `IndexError`: measured
    # through a real client session, `isError=True` with **no `structuredContent` at all** and the
    # body `"Error executing tool sensor_grid: list index out of range"`. Check 0.4 requires the
    # advisory intact, and ruling R38 already graded this shape a violation.
    #
    # `_as_points` also carries the `< 3` arity check, which is why it is not repeated here: on
    # `[[0,0],[1.0]]` the arity refusal was the *correct* one and was unreachable, because the
    # comprehension crashed above it. Its message says "room polygon" rather than "sensor grid
    # polygon"; one wording for one rule is the point of having one definition.
    points_polygon: list[Point] = _as_points(polygon)
    floor_z = polygon_floor_z(polygon)
    # **The write path's half of `lm83_grid_point_count`'s finiteness guard, which that function's
    # own docstring named and deferred as "an owner item".** The owner ruled it in on 2026-08-31,
    # and the whole-branch security review had by then measured three separate consequences of it
    # being open, all reachable through `sensor_grid(action="create")` with no manifest editing:
    #
    # - `polygon=[[0,0],[1e999,0],[1e999,1e999],[0,1e999]]` reached `_lm83_axis`'s `math.floor` as
    #   an `OverflowError` -- an `ArithmeticError`, in none of `sensor_grid`'s except clauses -- and
    #   left the tool as `isError=True` with **no `structuredContent`**;
    # - `polygon=[[0,0],[4,0],[4,5],[NaN,NaN],[0,5]]` was **silently accepted**: 48 points placed,
    #   0 clipped, zero advisories, and the grid registered. `nan` compares false against every
    #   bound, so `_bounds` and `_point_in_polygon` simply ignored the vertex;
    # - the polygon then stored in the manifest was `[[0,0],[4,0],[4,5],[null,null],[0,5]]`, because
    #   the serializer writes a non-finite float as `null`. The record of what the grid was placed
    #   over was not what it was placed over -- the provenance the manifest exists to carry, wrong
    #   in silence. `polygon_area_m2` then reads that polygon back and returns `nan` for the
    #   finite-`1e200` variant, which reaches the wire as `null` against a required, non-nullable
    #   `float` (ruling R200).
    #
    # One wording for one rule: the check itself lives in `_as_points`, which every polygon in this
    # module passes through, so it is made once and no entry point can be added that forgets it.
    if not _polygon_is_simple(points_polygon):
        raise PolygonError(
            "polygon self-intersects -- it has no well-defined interior to clip a grid against. "
            "Check the vertex order for a crossed edge."
        )

    x_min, x_max, y_min, y_max = _bounds(points_polygon)
    width, depth = x_max - x_min, y_max - y_min
    if width <= 0 or depth <= 0:
        # Collinear or coincident vertices, caught here as the `PolygonError` they actually are
        # (fix round 1, finding I3).
        #
        # **The sentence that stood here was false, and the half that made it load-bearing is the
        # half that went.** It said `max_cell_size` "would raise a bare, code-less `ValueError` for
        # this ... invisible to a caller catching `GridError` to build the wire envelope". True when
        # I3 was fixed; false since the commit that gave `max_cell_size` an
        # `ArgumentOutOfRangeGridError` -- which **is** a `GridError` (`GridError` subclasses
        # `ValueError`, not the reverse), so it reaches the envelope like any other. Measured on
        # this tree: `plan_grid(Standard.EN_12464, 2.0, 0.0)` raises `ArgumentOutOfRangeGridError`,
        # `isinstance(exc, GridError)` is True, `code` is None, `field` is "width". Only
        # *code-less* survived from the old account, and code-less is deliberate there --
        # `max_cell_size`'s own comment says the register allocates nothing for "the reference
        # surface has no extent".
        #
        # **The reason to catch it here is the other one, which was always the stronger.** That
        # refusal names `field="width"` and reads "reference surface must have positive extent" --
        # a statement about two arguments nobody passed on this path. The polygon is what is wrong,
        # so the polygon is what the refusal names.
        raise PolygonError(
            f"polygon has a degenerate circumscribing rectangle ({width:g} x {depth:g} m) -- its "
            "vertices are collinear or coincident, so it has no interior to place a grid over."
        )

    resolved_height = _resolve_height(
        standard, height, provenance=height_provenance, source=height_source, note=height_note,
        lm83_edition=lm83_edition,
    )

    if standard is Standard.LM_83:
        spec, xs, ys = _lm83_grid(width, depth)
    else:
        spec = plan_grid(standard, width, depth)
        xs = [(i + 0.5) * spec.spacing_x for i in range(spec.n_x)]
        ys = [(j + 0.5) * spec.spacing_y for j in range(spec.n_y)]

    # **Ruling R199 -- the bound the read path had and the write path did not.**
    # `lm83_grid_point_count` has checked `n_x * n_y` against `MATRIX_MAX_DECLARED_POINTS` since
    # Task 16, off two integers and before any allocation; this function -- the one that actually
    # allocates -- carried none, and its own sibling's docstring said so and deferred it. The
    # whole-branch security review re-measured the cost with a fresh interpreter per row (LM-83, the
    # worst case, since the two EN standards cap spacing at 10 m): a clean linear **210 B per
    # candidate** in memory and **41 B per point on disk**, 3.1 us per candidate. A 1 km square is
    # 2 775 556 points -> 0.92 GB RSS and a 114 MB `.pts`; a 10 km square is 277 755 556 points ->
    # **92 GB RSS, an 11.4 GB `.pts`, ~22 minutes**, from one `sensor_grid(action="create")` call
    # whose only response today is the *advisory* `GRID_VERY_LARGE`, emitted afterwards.
    #
    # Checked off `spec.n_x * spec.n_y`, two integers, so the refusal arrives **before** the
    # comprehension rather than instead of it -- `xs` and `ys` are `n_x + n_y` floats, not their
    # product, so nothing large has been built at this line. `GridPoint` allocation is the term that
    # grows as the product, and it is the next statement.
    #
    # The same constant as the read path, deliberately: one number for "the point count this repo
    # will size an allocation from", so a grid this server refuses to place is exactly a grid it
    # refuses to read back. `GRID_VERY_LARGE_POINTS` stays what it is -- a *reporting* threshold ten
    # times lower, on grids that are legitimate and merely expensive.
    candidates = spec.n_x * spec.n_y
    if candidates > MATRIX_MAX_DECLARED_POINTS:
        raise PolygonError(
            f"a {standard.value} grid over this polygon's {width:g} x {depth:g} m circumscribing "
            f"rectangle would be {candidates} candidate points, above the "
            f"{MATRIX_MAX_DECLARED_POINTS} this repo will size an allocation from. Placing it would "
            f"allocate roughly {candidates * 210 // 1_000_000} MB before a single point was clipped, "
            f"and write roughly {candidates * 41 // 1_000_000} MB to disk. Grid a smaller polygon, "
            "or a sub-area of this one."
        )

    dx, dy, dz = (float(v) for v in normal)
    all_points = [
        GridPoint(x=x_min + x_off, y=y_min + y_off, z=floor_z + resolved_height.value, dx=dx, dy=dy, dz=dz)
        for y_off in ys
        for x_off in xs
    ]
    kept = tuple(p for p in all_points if _point_in_polygon((p.x, p.y), points_polygon))

    points_total = len(all_points)
    points_inside = len(kept)
    points_clipped = points_total - points_inside

    if points_inside == 0:
        raise GridEmptyError(
            f"every one of {points_total} candidate grid point(s) fell outside the polygon (or "
            "exactly on its boundary) and was dropped -- a grid with no retained points is a study "
            "over zero sensors, not a usable result. The polygon may be smaller than one grid cell, "
            "degenerate in shape, or wound so the circumscribing rectangle barely overlaps it."
        )

    warnings: list[ArtifactWarning] = []
    if points_clipped > 0:
        warnings.append(
            ArtifactWarning(
                code="GRID_POINTS_CLIPPED",
                severity="info",
                message=(
                    f"{points_clipped} of {points_total} grid point(s) fell inside the "
                    f"circumscribing rectangle but outside the polygon and were dropped; "
                    f"{points_inside} retained. Points outside the real footprint see "
                    "unobstructed sky and read systematically bright (patterns.md)."
                ),
            )
        )

    return PlacedGrid(
        standard=standard,
        spec=spec,
        height=resolved_height,
        points=kept,
        points_total=points_total,
        points_inside=points_inside,
        points_clipped=points_clipped,
        warnings=tuple(warnings),
        floor_z=floor_z,
    )
