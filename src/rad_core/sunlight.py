"""EN 17037 Annex D's solar position formulae and True Solar Time.

Pure: no Radiance, no `rad_engine`, no `rad_mcp`. Later tasks walk a reference day minute by
minute through these functions and cast a shadow ray at each step, so an error here shifts every
reported sunlight interval.

Formulae of EN 17037:2018 Annex D (printed page 41), cited by equation number:

    (D.1)  TST = LT + (lambda - lambda_s)/15 + ET
           lambda   geographical longitude, East (+) or West (-) of Greenwich
           lambda_s longitude of standard meridian
           ET       equation of time
    (D.2)  ET(J)    = 0,0066 + 7,3525*cos(J' + 85,9) + 9,9359*cos(2J' + 108,9)
                       + 0,3387*cos(3J' + 105,2)
    (D.3)  delta(J) = 0,3948 - 23,2559*cos(J' + 9,1) - 0,3915*cos(2J' + 5,4)
                       - 0,1764*cos(3J' + 26,0)
           J = day number (1 Jan = 1, 31 Dec = 365; February is taken to have 28 days)
           J' = 360 * J / 365
    (D.4)  omega = (12,00 h - TST) * 15
    (D.5)  gamma_s = arcsin(cos(omega)*cos(phi)*cos(delta) + sin(phi)*sin(delta))
    (D.6)  alpha_s = 180 - arccos[(sin(gamma_s)*sin(phi) - sin(delta)) / (cos(gamma_s)*cos(phi))]
                     for TST <= 12:00
    (D.7)  alpha_s = 180 + arccos[(same quotient)]
                     for TST > 12:00
           alpha_s is measured from North, North = 0 deg.

Three inconsistencies in the source, recorded so an implementer does not "fix" one into a bug.

1. (D.4) as printed gives a *negative* hour angle in the afternoon, while the sentence under it
   says the angle is "positive towards the afternoon and negative towards the morning". Transcribed
   exactly as printed anyway: altitude (D.5) uses `cos(omega)`, which is even, and the azimuth
   branch (D.6)/(D.7) is on `TST` directly, not on the sign of `omega`. Neither result changes.

2. (D.5) writes `omega_h` where (D.4) defines `omega_eta`; they are the same quantity, so this is a
   typesetting slip, not a second formula. No result changes.

3. **(D.2)'s equation of time is in MINUTES; (D.1) adds it to an expression in HOURS.** Evaluated
   across a full year (`equation_of_time_minutes(1..365)`), ET ranges from -14.410 to +16.487 --
   the real equation of time is a few minutes, never a few hours, so a result in that range cannot
   be hours. (D.1)'s longitude term `(lambda - lambda_s)/15` is degrees divided by 15, i.e. hours,
   so the raw `ET` from (D.2) must be divided by 60 before it is added. Unlike the first two, this
   inconsistency **does** change every result: on Berlin's longitude (13.4 E, standard meridian
   15 E) at LT 12:00 on 21 March (J = 80, ET(80) = -7.3374), the correct TST is
   `12.0 + (13.4 - 15.0)/15 + (-7.3374)/60` = 11.7710 h; omitting the /60 gives 4.5559 h, which is
   not a plausible offset from local noon. `equation_of_time_minutes` keeps returning minutes,
   exactly as (D.2) prints and as its name says; `true_solar_time_hours` is the one place that
   divides by 60, so the unit conversion lives in exactly one function.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Final, Literal

from rad_core.geometry import Point3
from rad_core.view_out import Level

__all__ = [
    "SUNLIGHT_STEP_MINUTES",
    "SunStep",
    "day_number",
    "equation_of_time_minutes",
    "declination_deg",
    "true_solar_time_hours",
    "hour_angle_deg",
    "solar_altitude_deg",
    "solar_azimuth_deg",
    "model_azimuth_deg",
    "reference_day",
    "step_runs",
    "PointPUnreachable",
    "point_p",
    "union_intervals",
    "sunlight_level",
    "reveal_probe_directions",
    "opening_reach_m",
    "reveal_is_modelled",
]

#: D.2. Both clearances bind at once (`z = max(floor + FLOOR_CLEARANCE_M, sill +
#: SILL_CLEARANCE_M)`), never as alternatives.
FLOOR_CLEARANCE_M: Final[float] = 1.2
SILL_CLEARANCE_M: Final[float] = 0.3

#: The reference day is walked in steps of this many minutes (design section 5). **Not provisional,
#: and not one of the numbers Task 13 measures**: the design states it and states why. One minute
#: is 1,1 % of Table A.6's 1,5 h minimum, so the step can move a level only for a room whose
#: hours sit within a minute of a threshold. And a fixed step rather than bisection of the interval boundaries, because D.6 says
#: sunshine may be *"broken by several obstructions"* -- visibility is not monotone in time, and
#: bisection between two lit instants would step straight over a shadow between them. It divides
#: the 1440 minutes of a day exactly, which `reference_day` checks. It carries an `Unsourced values`
#: row in docs/tool-surface.md (Task 13): the design states the reason, and no source states the
#: number.
SUNLIGHT_STEP_MINUTES: Final[int] = 1

_DAYS_IN_MONTH_FEB28: tuple[int, ...] = (31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)


def day_number(d: date) -> int:
    """D.2/D.3's J: 1 January = 1, February fixed at 28 days regardless of leap year, so
    31 December is 365 in every year including leap years."""
    return sum(_DAYS_IN_MONTH_FEB28[: d.month - 1]) + d.day


def equation_of_time_minutes(j: int) -> float:
    """(D.2). Returns MINUTES, exactly as printed -- see inconsistency 3 above. Do not fold the
    /60 conversion in here; `true_solar_time_hours` is where it belongs."""
    jp = math.radians(360.0 * j / 365.0)
    return (
        0.0066
        + 7.3525 * math.cos(jp + math.radians(85.9))
        + 9.9359 * math.cos(2 * jp + math.radians(108.9))
        + 0.3387 * math.cos(3 * jp + math.radians(105.2))
    )


def declination_deg(j: int) -> float:
    """(D.3), degrees."""
    jp = math.radians(360.0 * j / 365.0)
    return (
        0.3948
        - 23.2559 * math.cos(jp + math.radians(9.1))
        - 0.3915 * math.cos(2 * jp + math.radians(5.4))
        - 0.1764 * math.cos(3 * jp + math.radians(26.0))
    )


def true_solar_time_hours(
    local_time_hours: float,
    *,
    longitude_east_deg: float,
    standard_meridian_deg: float,
    day: int,
) -> float:
    """(D.1). `ET` from (D.2) is minutes; divided by 60 here, and only here, to match the hours of
    the local-time and longitude terms -- see inconsistency 3 in the module docstring."""
    et_minutes = equation_of_time_minutes(day)
    return (
        local_time_hours
        + (longitude_east_deg - standard_meridian_deg) / 15.0
        + et_minutes / 60.0
    )


def hour_angle_deg(true_solar_hours: float) -> float:
    """(D.4), transcribed as printed -- see inconsistency 1 in the module docstring."""
    return (12.0 - true_solar_hours) * 15.0


def solar_altitude_deg(*, true_solar_hours: float, latitude_deg: float, day: int) -> float:
    """(D.5): gamma_s, the solar altitude above the horizon, degrees."""
    omega = math.radians(hour_angle_deg(true_solar_hours))
    phi = math.radians(latitude_deg)
    delta = math.radians(declination_deg(day))
    sin_gamma = math.cos(omega) * math.cos(phi) * math.cos(delta) + math.sin(phi) * math.sin(delta)
    return math.degrees(math.asin(sin_gamma))


def solar_azimuth_deg(*, true_solar_hours: float, latitude_deg: float, day: int) -> float:
    """(D.6)/(D.7): alpha_s, measured from North (North = 0 deg).

    `reference_day` steps a day at True Solar Time offset by (D.1)'s equation-of-time/longitude
    correction (`offset = true_solar_time_hours(0.0, ...)`), so the first or last step of a civil
    day can carry a `true_solar_hours` below 0 or at/above 24. `arccos_deg` below is exactly
    periodic in `true_solar_hours` with period 24 (it depends only on `cos(hour_angle_deg(...))`,
    itself periodic), but the raw `<= 12.0` branch is not, so outside [0, 24) it picked the wrong
    D.6/D.7 branch and mirrored the azimuth about due north/south (measured, ruling R709: at
    latitude -80 deg/21 Dec and +80 deg/21 Jun, TST -1, -0.5, 24.5 and 25 disagreed with an
    independent azimuth formula by 14-28 deg). Normalising with `% 24.0` here fixes the branch
    without changing `arccos_deg` itself. The result lies in [0, 24], not [0, 24): a tiny negative
    input gives exactly 24.0 (`-1e-17 % 24.0 == 24.0`), which takes the `> 12` branch -- still the
    right one, since at TST 0 the sun is on the meridian and both branches name one direction
    (180 + 180 = 360 where 180 - 180 = 0 is meant).

    At TST 0 and 24 the sun is on the meridian: `arccos_deg` is 0 where the solar-midnight azimuth
    is 180 (latitude -80 deg/21 Dec) and 180 where it is 0 (+80 deg/21 Jun, 52.47 deg N/21 Mar).
    In the 180 case the unnormalised `> 12` branch returned 360 at TST 24 rather than 0: the same
    direction spelled differently. TST 24 now normalises to 0 and returns 0."""
    gamma_s = math.radians(
        solar_altitude_deg(true_solar_hours=true_solar_hours, latitude_deg=latitude_deg, day=day)
    )
    phi = math.radians(latitude_deg)
    delta = math.radians(declination_deg(day))
    quotient = (math.sin(gamma_s) * math.sin(phi) - math.sin(delta)) / (
        math.cos(gamma_s) * math.cos(phi)
    )
    quotient = max(-1.0, min(1.0, quotient))  # clamp float noise at the +/-1 boundary (e.g. noon)
    arccos_deg = math.degrees(math.acos(quotient))
    tst_normalised = true_solar_hours % 24.0
    if tst_normalised <= 12.0:
        return 180.0 - arccos_deg
    return 180.0 + arccos_deg


def model_azimuth_deg(true_azimuth_deg: float, *, north_deg: float) -> float:
    """A sun's azimuth in the **model** frame, from its azimuth in the **true-north** frame.

    `(true + north) % 360`, where `north` is the project's `north` scalar in degrees east of north
    and both azimuths are degrees clockwise from their frame's north (3.17 Note 1 for the true one;
    `rad_core.apertures.Aperture.azimuth_deg`'s own convention, +Y = 0, for the model one).

    **This is the one place the sunlight branch crosses from one frame to the other**, and the
    sense is measured, not argued: `run_annual` applies the same scalar as the sky rotation
    `gendaymtx -r -north` (`rad_engine.sky.north_rotation_flag`). At 10:00 on 21 March, north 0,
    30, 90, -90 and 180, real `gendaymtx` put the sun's patch 1,19 degrees from this function's
    answer, and the sign-flipped `true - north` 45,37 degrees off at north 30 and 102,16 at +/-90
    (identical at 0 and 180, where the two signs agree). At 14:30 only the north 0 and 30 results
    were recorded (the run's output was cut to those lines): 1,21 degrees, and 42,03 for the flip
    at 30 (task-11-report.md;
    `tests/test_sunlight.py::test_the_north_conversion_agrees_with_the_sky_rotation_run_annual_emits`).
    """
    return (true_azimuth_deg + north_deg) % 360.0


@dataclass(frozen=True)
class SunStep:
    """One step of the reference day: its index, the True Solar Time at its midpoint, and the
    sun's altitude and TRUE-north azimuth there (degrees; the azimuth is 3.17 Note 1's, clockwise
    from due north, and is not yet in the model frame -- `model_azimuth_deg` does that)."""

    index: int
    tst_mid_h: float
    altitude_deg: float
    azimuth_true_deg: float


def reference_day(
    d: date,
    *,
    latitude_deg: float,
    longitude_east_deg: float,
    standard_meridian_east_deg: float,
    step_minutes: int = SUNLIGHT_STEP_MINUTES,
) -> tuple[float, list[SunStep]]:
    """The reference day `d`, stepped at `step_minutes` through its 24 hours of local standard
    time, each step placed at True Solar Time (D.1) and given the sun's position there.

    Returns `(tst_minus_local_h, steps)`. The offset is one number for the whole day, because
    (D.2)'s equation of time and (D.3)'s declination are functions of the day number alone; step
    `i` therefore covers TST `[offset + i*m/60, offset + (i+1)*m/60)` hours for a step of `m`
    minutes, and its position is taken at the midpoint. `standard_meridian_east_deg` is **east-positive**, as (D.1)'s
    `lambda_s` is -- not `rad_core.weather.EPWLocation.time_zone_meridian_deg`, which is the
    west-positive `.wea` convention.

    The day is stepped in local time rather than in TST so that it is the civil date the caller
    named. The two differ only by the offset, and either way all 24 hours are walked, so no sunlit
    step is lost: the choice only decides where a sunlit period spanning local midnight -- the
    southern polar summer has them in February -- would be split in two.
    """
    if step_minutes <= 0 or (24 * 60) % step_minutes != 0:
        raise ValueError(f"step_minutes={step_minutes} does not divide the 1440 minutes of a day")
    j = day_number(d)
    offset = true_solar_time_hours(
        0.0, longitude_east_deg=longitude_east_deg,
        standard_meridian_deg=standard_meridian_east_deg, day=j,
    )
    steps: list[SunStep] = []
    for index in range((24 * 60) // step_minutes):
        tst = offset + (index + 0.5) * step_minutes / 60.0
        steps.append(SunStep(
            index=index,
            tst_mid_h=tst,
            altitude_deg=solar_altitude_deg(true_solar_hours=tst, latitude_deg=latitude_deg, day=j),
            azimuth_true_deg=solar_azimuth_deg(
                true_solar_hours=tst, latitude_deg=latitude_deg, day=j
            ),
        ))
    return offset, steps


def step_runs(indices: Iterable[int]) -> list[tuple[int, int]]:
    """Step indices merged into half-open runs `[start, end)` of consecutive steps: `[3, 4, 5, 9]`
    is `[(3, 6), (9, 10)]`. In step units, so `union_intervals` over these runs is exact integer
    arithmetic and a level is never decided by float drift in a sum of hour differences."""
    runs: list[tuple[int, int]] = []
    for index in sorted(set(indices)):
        if runs and index == runs[-1][1]:
            runs[-1] = (runs[-1][0], index + 1)
        else:
            runs.append((index, index + 1))
    return runs


class PointPUnreachable(ValueError):
    """D.2's reference point P does not fit within the aperture's own opening.

    Mirrors `rad_core.geometry.GeometryError`'s shape (class-attribute `code`, `ValueError` base)
    for the same reason: an `error`-severity refusal at this layer is an exception, not a return
    value a caller could silently ignore.

    **`field` names this function's own parameter, not a tool argument** -- no caller of
    `run_view_and_sunlight` passes anything called `polygon_vertices`. The tool re-maps it from
    `binding` (ruling T11.3): `"sill"` means the opening is too short to hold its own 0,3 m sill
    clearance, which no floor height can fix -- judged first, so it wins even when the floor clause
    is the larger; `"floor"` means the sill clause fits under the head and only the floor
    clearance puts P above it, so a lower floor would fix it. `required_z_m` and `head_z_m` are the
    two numbers the design's refusal row says to name.
    """

    code = "EN17037_POINT_P_ABOVE_OPENING"
    field = "polygon_vertices"
    severity = "error"

    def __init__(
        self, message: str, *, binding: Literal["floor", "sill"], required_z_m: float,
        head_z_m: float,
    ) -> None:
        super().__init__(message)
        self.binding: Literal["floor", "sill"] = binding
        self.required_z_m = required_z_m
        self.head_z_m = head_z_m


def point_p(
    polygon_vertices: Sequence[Point3], *, floor_height_m: float, normal: Point3
) -> Point3:
    """D.2: the reference point P for sunlight exposure, "on the inner surface of the aperture ...
    at the centre of the opening width ... minimum 1,2 m above the floor and 0,3 m above the sill
    of the daylight opening, if present."

    Both clauses bind at once -- `z = max(floor + FLOOR_CLEARANCE_M, sill + SILL_CLEARANCE_M)` --
    not as alternatives: a high sill can push P above the plain floor clearance. The sill is the
    opening's own lowest vertex, so a floor-to-ceiling opening (sill at or below the floor) falls
    back to D.2's "without a sill" case, 1,2 m above the floor, automatically.

    `x, y` start at the centre of the opening's own bounding box, i.e. the centre of its width --
    not the average of its vertices, which differs from the width centre on a non-rectangular
    opening.

    **Then P is moved onto the aperture's plane** (ruling F.3, final whole-branch review I5), at
    the same height, along the horizontal projection of `normal` -- the plane's normal, either
    sense, since the move is the same line both ways; the tool passes the exterior one. D.2 puts P
    *"on the inner surface of the aperture"*. On a vertical opening the bounding-box centre is
    already on the plane and nothing moves. On a tilted one it is not: a 45 degree roof window
    rising from z = 2,0 to 3,0 over y = 0 to 1 had P at y = 0,5, height 2,3 m -- 0,200 m below the
    glass, measured vertically, on the room side of it, where the correctness review reported the
    pane blocking its own sun; on the plane at 2,3 m it is at y = 0,3. The plane
    is taken through the mean of the vertices, which a group of coplanar panes shares. A pane whose
    normal has no horizontal component is flat, and a flat pane never reaches the move: its lowest
    vertex is its highest, so the 0,3 m sill clearance has already refused it.

    Raises `PointPUnreachable` when the resulting `z` is above the opening's own highest vertex --
    the opening is too short to hold both clearances, so a point outside the opening would
    silently measure the wall instead.
    """
    xs = [v[0] for v in polygon_vertices]
    ys = [v[1] for v in polygon_vertices]
    zs = [v[2] for v in polygon_vertices]
    min_z, max_z = min(zs), max(zs)
    from_floor = floor_height_m + FLOOR_CLEARANCE_M
    from_sill = min_z + SILL_CLEARANCE_M
    z = max(from_floor, from_sill)
    if z > max_z:
        # The sill clause is judged first: when it alone is above the head, no floor height can
        # bring P inside the opening, so it is the bound that failed even where the floor clause
        # happens to be the larger of the two.
        binding: Literal["floor", "sill"] = "sill" if from_sill > max_z else "floor"
        raise PointPUnreachable(
            f"point P at {z:.3f} m is above the opening's own head at {max_z:.3f} m "
            f"(the {binding} clearance is the one that fails: floor {floor_height_m:.3f} + "
            f"{FLOOR_CLEARANCE_M} = {from_floor:.3f} m, sill {min_z:.3f} + {SILL_CLEARANCE_M} = "
            f"{from_sill:.3f} m)",
            binding=binding, required_z_m=z, head_z_m=max_z,
        )
    x = (min(xs) + max(xs)) / 2.0
    y = (min(ys) + max(ys)) / 2.0
    nx, ny, nz = normal
    horizontal_sq = nx * nx + ny * ny
    if horizontal_sq > 0.0:
        cx = sum(xs) / len(xs)
        cy = sum(ys) / len(ys)
        cz = sum(zs) / len(zs)
        offset = nx * (x - cx) + ny * (y - cy) + nz * (z - cz)
        x -= offset * nx / horizontal_sq
        y -= offset * ny / horizontal_sq
    return (x, y, z)


def union_intervals(
    per_aperture: Mapping[str, Sequence[tuple[float, float]]],
) -> tuple[list[tuple[float, float]], float]:
    """D.2's cross-facade cumulation ("cumulate the durations ... occurring at different times
    ... if not occurring at the same time") and D.6's within-facade summation of parts "broken by
    several obstructions" are the same operation: the union, over every aperture, of its sunlit
    intervals. Two apertures lit at once count that overlap once; a period broken into disjoint
    pieces sums its parts.

    Returns the merged, sorted, non-overlapping intervals and their total measure (same units as
    the interval bounds -- hours, for a reference day).
    """
    all_intervals = sorted(
        interval for intervals in per_aperture.values() for interval in intervals
    )
    merged: list[tuple[float, float]] = []
    for start, end in all_intervals:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    total = sum(end - start for start, end in merged)
    return merged, total


#: Table A.6, cumulative sunlight duration on the reference day. Checked high-to-minimum, same
#: inclusive convention as `rad_core.view_out`'s Table A.5 dicts (">= 4,0 h" etc, never "> 4,0 h").
_TABLE_A6_SUNLIGHT_HOURS: Final[dict[Level, float]] = {
    "high": 4.0,
    "medium": 3.0,
    "minimum": 1.5,
}


def sunlight_level(hours: float) -> Level | None:
    """Table A.6's sunlight-exposure row for a cumulated `hours` (`union_intervals`'s total
    measure, a UNION across apertures, never a sum). `None` means assessed and below the minimum
    1,5 h -- never "not assessed", and never collapsed into a string or an exception.

    `Level` is imported from `rad_core.view_out`, not redefined here -- see that module's own
    docstring for why it is the one place `Level` is defined.
    """
    for level, threshold in _TABLE_A6_SUNLIGHT_HOURS.items():
        if hours >= threshold:
            return level
    return None


# --- whether the opening's reveal exists as geometry (design section 4.3, ruling T12.2) ----------
#
# D.6's acceptance angle `alpha_a` -- *"Sunshine is determined if the reference point is insolated
# within the acceptance angle alpha_a in plan"* -- is narrowed by a deep reveal, a loggia or a
# self-shading facade. This tool applies it by casting the shadow ray: a ray the reveal stops is
# not counted. That works only where the reveal exists in the scene on the side the shadow ray
# travels through. The three functions below decide, from geometry alone, whether it does.
#
# **The test.** From the shadow rays' own start point -- point P moved a small offset outside the
# glass by the caller (`rad_mcp.view_sunlight_tool._shadow_origin`, 1 mm) -- cast a ray *along the
# opening's plane*, in plan, towards each side of the opening. A reveal on the exterior side of the glass meets that ray within the opening's own
# width; a zero-thickness wall, or glass flush with the wall's outer face, leaves it running along
# the facade with nothing to meet. "Within the opening's own width" is `opening_reach_m`: the
# distance from P to the opening's farthest vertex. That is a number the opening's geometry
# supplies, not one chosen here. No point of an opening -- nor of the hull of a group of panes --
# lies farther from P than its farthest vertex, so a reveal standing at the glass edge is always
# within reach. On a rectangular opening of non-zero height it is strictly within -- the farthest
# corner lies above or below P as well as beside it -- so a jamb exactly at the glass edge does not
# sit on a float boundary.
#
# **No threshold is minted.** The depth a reveal must have to be found is the shadow rays' own
# start offset: a reveal shallower than that is not met by the probe, and it is not met by any
# shadow ray either, so the probe and the computation it reports on agree by construction.
#
# **Which way each error goes, and why the test leans the way it does.** A false negative -- the
# warning withheld although the opening has no reveal -- lets over-counted hours pass in silence,
# which is the error that matters. It happens when some *other* surface stands within the
# opening's reach, just off the facade plane: a fin, a return wall at an inside corner, a
# neighbouring pane set perpendicular to this one. Such a surface stops grazing shadow rays on
# that side as a reveal would, though not necessarily as many. A false
# positive -- the warning given although the scene is fine -- costs a caller a sentence. It
# happens for a reveal on the ROOM side of the glass (glass flush with the wall's outer face), and
# that warning is true of the computation: P is on the glass, the shadow rays leave outward, and
# no shadow ray can meet a reveal behind its own start. It also happens on a genuinely open side
# of a corner window, where the missing jamb is real architecture; the message is worded so that
# it is true there too ("if the real opening has a reveal the model omits"). Every side must be
# bounded for the reveal to count as modelled, which leans the test towards warning.


def _probe_normalise(vector: Point3) -> Point3:
    length = math.sqrt(vector[0] ** 2 + vector[1] ** 2 + vector[2] ** 2)
    return (vector[0] / length, vector[1] / length, vector[2] / length)


def reveal_probe_directions(
    exterior_normal: Point3, up_vector: Point3, *, horizontal: bool
) -> tuple[Point3, ...]:
    """The in-plane directions a reveal probe is cast along, from the shadow rays' start point.

    `exterior_normal` points out of the building and `up_vector` is `rad_core.apertures.Aperture.
    up_vector` -- unit, in the aperture's plane, and never parallel to its normal (that module's
    `_up_vector` guarantees both). Their cross product is therefore in the plane and **horizontal**:
    for a facade window it runs along the wall, towards the two jambs, which are the sides that
    bound D.6's acceptance angle *in plan*. Its two senses are the first two probes.

    A horizontal opening (`horizontal=True`, `rad_core.apertures.is_horizontal`) has every side
    in plan, so `up_vector` -- in-plane; +Z projected onto the plane, or
    `rad_core.apertures._FLAT_APERTURE_UP` (+Y) for an opening flat enough that the projection
    vanishes -- and its opposite are probed too.
    Sill and head are not probed on a facade window: D.6 states the acceptance angle in plan, and a
    downward probe could meet the ground plane of a model that has one.
    """
    ex, ey, ez = exterior_normal
    ux, uy, uz = up_vector
    along = _probe_normalise((uy * ez - uz * ey, uz * ex - ux * ez, ux * ey - uy * ex))
    directions: list[Point3] = [along, (-along[0], -along[1], -along[2])]
    if horizontal:
        directions += [up_vector, (-ux, -uy, -uz)]
    return tuple(directions)


def opening_reach_m(polygon_vertices: Sequence[Point3], point: Point3) -> float:
    """The distance from `point` (D.2's P) to the opening's farthest vertex: how far a probe may
    travel and still be meeting the opening's own reveal rather than something beside it."""
    return max(math.dist(vertex, point) for vertex in polygon_vertices)


def reveal_is_modelled(distances: Sequence[float | None], *, reach_m: float) -> bool:
    """Whether every probe met a surface within `reach_m` -- `None` is a probe that escaped.

    **Every** side, not any: an opening bounded on one side only has nothing modelled on the
    other, and a shadow ray leaving by that side is not narrowed. An empty `distances` is not
    modelled -- nothing was probed, so nothing was found.
    """
    return bool(distances) and all(
        metres is not None and metres <= reach_m for metres in distances
    )
