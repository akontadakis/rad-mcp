"""UGR geometry, pure. Phase 11c design §6.

No IO, no Radiance file formats, no imports from `rad_engine` or `rad_mcp`. The cull exists
because `glarendx` silently discards any source at or beyond 90 degrees from the view direction
(design §4, F7): a row outside it would be written, published in `sources`, and contribute
nothing.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import Final

__all__ = [
    "Instance",
    "CullResult",
    "cull",
    "OUTSIDE_CUT",
    "AT_THE_EYE",
    "WRAPS_THE_EYE",
    "Cone",
    "cone_for",
    "ray_directions",
    "DEFAULT_CONE_MARGIN",
    "SourceRow",
    "reduce_samples",
    "MIN_HITS",
    "MAX_TOTAL_RAYS",
    "MIN_RAYS_PER_EMITTER",
    "RayBudgetExceeded",
    "rays_per_emitter",
    "BackgroundNotPositive",
    "direct_illuminance_lux",
    "indirect_illuminance_lux",
    "background_luminance",
    "Vec",
]

OUTSIDE_CUT: Final[str] = "at or beyond 90 degrees from the view direction"
AT_THE_EYE: Final[str] = "coincident with the observer"
WRAPS_THE_EYE: Final[str] = "wraps more than 90 degrees about its own centre direction"

Vec = tuple[float, float, float]


@dataclass(frozen=True)
class Instance:
    """One placed luminaire instance, with its world-space emitter box.

    `bounds_min`/`bounds_max` are the type's emitter box after the placement's `rot`, plus the
    instance position, so the placement's rotation is already in them and nothing here applies one.
    `ugr_tool._placed` computes them from the **current** type record's `emitter_min`/`emitter_max`
    with `rotated_extent`, not from the placement record's `rotated_min`/`rotated_max` snapshot: the
    snapshot is recorded (and refreshed by a re-import, R724), but a manifest re-imported before
    R724, or edited by hand, can carry a stale one, so no computation reads it (R726).
    """

    placement: str
    type_name: str
    index: int
    bounds_min: Vec
    bounds_max: Vec

    @property
    def centre(self) -> Vec:
        return tuple((self.bounds_min[i] + self.bounds_max[i]) / 2 for i in range(3))  # type: ignore[return-value]


@dataclass(frozen=True)
class CullResult:
    kept: list[Instance]
    excluded: list[tuple[Instance, str]]


def _normalise(v: Vec) -> Vec:
    n = math.sqrt(sum(c * c for c in v))
    if n == 0.0:
        raise ValueError("the view direction is a zero vector")
    return (v[0] / n, v[1] / n, v[2] / n)


def _emitter_axis(eye: Vec, bounds_min: Vec, bounds_max: Vec) -> Vec:
    """The unit direction from `eye` to the emitter box's centre."""
    centre = tuple((bounds_min[i] + bounds_max[i]) / 2 for i in range(3))
    to_centre = tuple(centre[i] - eye[i] for i in range(3))
    if math.sqrt(sum(c * c for c in to_centre)) == 0.0:
        raise ValueError("the emitter's bounds are centred exactly at the eye, so no cone axis is defined")
    return _normalise(to_centre)


def _widest_corner_angle(eye: Vec, bounds_min: Vec, bounds_max: Vec, axis: Vec) -> float:
    """The largest angle any of the box's eight corners makes with `axis`, seen from `eye`.

    `cull` and `cone_for` both need it -- one to decide whether a forward cone exists at all, the
    other to size that cone -- and they must not answer it two different ways.
    """
    widest = 0.0
    for cx in (bounds_min[0], bounds_max[0]):
        for cy in (bounds_min[1], bounds_max[1]):
            for cz in (bounds_min[2], bounds_max[2]):
                d = tuple((cx, cy, cz)[i] - eye[i] for i in range(3))
                n = math.sqrt(sum(c * c for c in d))
                if n == 0.0:
                    continue
                cosang = max(-1.0, min(1.0, sum(d[i] * axis[i] for i in range(3)) / n))
                widest = max(widest, math.acos(cosang))
    return widest


def cull(eye: Vec, view_direction: Vec, instances: list[Instance]) -> CullResult:
    u = _normalise(view_direction)
    kept: list[Instance] = []
    excluded: list[tuple[Instance, str]] = []
    for inst in instances:
        d = tuple(inst.centre[i] - eye[i] for i in range(3))
        if math.sqrt(sum(c * c for c in d)) == 0.0:
            excluded.append((inst, AT_THE_EYE))
            continue
        if sum(d[i] * u[i] for i in range(3)) > 0.0:
            # Its centre is in front, but that does not mean a forward cone contains the whole
            # box: the axis points at the centre, so a box the eye lies beside can reach past 90
            # degrees from it. `cone_for` used to clamp and return a truncated cone, losing those
            # corners' rays and understating `omega_sr` with nothing said. R670.
            axis = _emitter_axis(eye, inst.bounds_min, inst.bounds_max)
            if _widest_corner_angle(eye, inst.bounds_min, inst.bounds_max, axis) > math.pi / 2:
                excluded.append((inst, WRAPS_THE_EYE))
            else:
                kept.append(inst)
        else:
            excluded.append((inst, OUTSIDE_CUT))
    return CullResult(kept=kept, excluded=excluded)


DEFAULT_CONE_MARGIN: Final[float] = 1.5


@dataclass(frozen=True)
class Cone:
    axis: Vec
    half_angle_rad: float
    omega_sr: float


def cone_for(eye: Vec, bounds_min: Vec, bounds_max: Vec, *,
             margin: float = DEFAULT_CONE_MARGIN) -> Cone:
    """A cone from `eye` that contains the axis-aligned emitter box, widened by `margin`.

    The bounds are the instance's rotated emitter box (`Instance`, R726), so the rotation is
    already in them and this function never applies one.
    """
    axis = _emitter_axis(eye, bounds_min, bounds_max)
    widest = _widest_corner_angle(eye, bounds_min, bounds_max, axis)
    # **No forward cone contains this emitter, so none is returned** (R670). A cone is a cone about
    # one axis, and its half-angle cannot exceed pi/2 and still point forward. This function used
    # to clamp to pi/2 and return a cone anyway: the corners past 90 degrees fell outside it, their
    # rays were never cast, and the hit fraction -- and so `omega_sr` -- came back silently low. A
    # wrong number wearing a result's shape is what R665 refuses to publish, so this raises and
    # `cull` excludes the instance under `WRAPS_THE_EYE` before the pipeline ever gets here.
    #
    # The earlier note called the case degenerate, reachable only with the eye inside or touching
    # the box. **Both halves of that were measured false on 2026-09-21.** The axis points at the
    # box centre, not along any separating direction, so a box the eye lies *beside* reaches past
    # 90 degrees while the eye stays well outside it. It takes an emitter box longer than about
    # 5.25 m at a 1.4 m mounting height above the eye -- a continuous run declared as one luminous
    # opening. Over 500 000 random placements at ordinary luminaire dimensions the widest corner
    # angle never passed 90 degrees; its worst was 88.25 degrees.
    if widest > math.pi / 2:
        raise ValueError(
            f"the emitter's corners reach {math.degrees(widest):.2f} degrees from the direction to "
            "its own centre, which is more than 90 degrees, so no forward cone contains it and no "
            "sampling cone is defined")
    # `widest` is at most pi/2 here, so this clamp only ever trims the margin: the emitter itself
    # is still inside the cone, and nothing is lost.
    half = min(widest * margin, math.pi / 2)
    return Cone(axis=axis, half_angle_rad=half, omega_sr=2 * math.pi * (1 - math.cos(half)))


def ray_directions(cone: Cone, n: int, seed: int) -> list[Vec]:
    """`n` directions uniform in solid angle within `cone`, deterministic in `seed`."""
    rng = random.Random(seed)
    u = cone.axis
    a = (1.0, 0.0, 0.0) if abs(u[0]) < 0.9 else (0.0, 1.0, 0.0)
    e1 = _normalise((a[1] * u[2] - a[2] * u[1], a[2] * u[0] - a[0] * u[2], a[0] * u[1] - a[1] * u[0]))
    e2 = (u[1] * e1[2] - u[2] * e1[1], u[2] * e1[0] - u[0] * e1[2], u[0] * e1[1] - u[1] * e1[0])
    cos_min = math.cos(cone.half_angle_rad)
    out: list[Vec] = []
    for _ in range(n):
        ct = 1 - rng.random() * (1 - cos_min)
        st = math.sqrt(max(0.0, 1 - ct * ct))
        ph = rng.random() * 2 * math.pi
        cp, sp = math.cos(ph), math.sin(ph)
        out.append(tuple(u[i] * ct + (e1[i] * cp + e2[i] * sp) * st for i in range(3)))
    return out


# Not a sourced threshold. Chosen so a resolved solid angle is not dominated by counting noise
# (a fractional standard error of about 1/sqrt(hits), so ~7 % at 200). F9 measured 49 hits for the
# ERCO disk over a fixed 25-degree cone, which is the case this floor exists to refuse. The observed
# counts and their repeat count are recorded in `tests/fixtures/README.md`; tighten there, not here.
MIN_HITS: Final[int] = 200


# --- the ray budget -------------------------------------------------------------------------------
#
# **What grows with the placement count is the memory, not the spawn count** (whole-branch review,
# Critical 2). `rad_engine.glarendx.sample_emitters` sends its per-instance ray count for *every*
# kept instance, assembled into one `lines` list, one `directions` list and one joined stdin blob
# before the spawn -- and `rad_engine.run` then encodes that to bytes (a copy) and hands it
# to `rad_engine.process_group.run_in_own_session` (R750; `subprocess.run(input=...,
# capture_output=True)` before it), whose `Popen.communicate` buffers the whole of stdout again.
# Measured on this machine at 20 000 rays for one instance of the test fixture's own emitter:
# 101 bytes of `lines` strings, 145 bytes of `directions` tuples and 45 bytes of joined blob per
# ray -- **291 bytes per ray, 5.82 MB per kept instance**, before `rtrace` starts.
#
# R489 permits 2000 instances on one lit run and `cull` keeps every instance in the forward
# half-space, so all 2000 can survive: 40 M rays and **11.6 GB** of Python-side allocation, roughly
# three times that once the subprocess's own copies are counted. `inline_timeout_s` does not bound
# any of it, because the lists are built before the spawn -- the class ruling R69's last paragraph
# already settled for `sample_sky_visibility` ("stdin payload is assembled in memory before the
# spawn and is therefore not bounded by `timeout`"), and which that ruling answered with an
# explicit range check. A legal project OOM-kills the server instead of refusing, so the bound is
# here, in the same posture.

#: The most rays one `run_ugr` call may cast in total, across every instance that survived the
#: cull. **Not a sourced figure and not a physical quantity** (docs/tool-surface.md
#: `## Unsourced values`): it is a memory bound, chosen at the measured 291 bytes per ray to hold
#: this process's pre-spawn payload near 120 MB, and roughly three times that with the
#: subprocess's encoded input and buffered stdout. It is 20 times `rad_engine.glarendx.
#: DEFAULT_RAYS_PER_EMITTER`, so up to 20 kept instances sample at the full per-emitter count and
#: nothing about the common case changes.
MAX_TOTAL_RAYS: Final[int] = 400_000

#: The fewest rays `rays_per_emitter` will scale an instance down to. Ten times `MIN_HITS`:
#: `MIN_HITS` hits out of this many is a hit fraction of 10 %, and the fixture this tool was
#: measured on resolves at about 8-9 %, so an instance sampled with fewer rays than this can
#: essentially only fail the floor and be excluded. Scaling below it would answer a legal call with
#: `UGR_ALL_SOURCES_UNDERSAMPLED` and `ugr` null, which is a refusal wearing a result's shape;
#: the caller is refused outright instead, and told so.
MIN_RAYS_PER_EMITTER: Final[int] = 10 * MIN_HITS


class RayBudgetExceeded(ValueError):
    """Too many instances survived the cull to sample any of them meaningfully.

    Carries no wire code of its own: `rad_core` publishes no advisory codes, and the caller
    (`rad_mcp.ugr_tool`) names one at its own call site, which is what `tests/test_advisories.py`'s
    register scan reads.
    """


def rays_per_emitter(kept: int, default: int) -> int:
    """How many rays each of `kept` instances may be sampled with, given `MAX_TOTAL_RAYS`.

    `default` when the full budget fits (every call with 20 kept instances or fewer, at
    `DEFAULT_RAYS_PER_EMITTER`), the scaled-down share when it does not, and `RayBudgetExceeded`
    when even the scaled share would fall under `MIN_RAYS_PER_EMITTER`. `kept` of zero or fewer
    returns `default`: no rays are cast at all on that exit, and there is nothing to divide.

    The caller must tell the caller of *its* own that a reduction happened -- a smaller sample is a
    larger counting error on every published `solid_angle_sr` (about `1/sqrt(hits)`), and an
    instance that would have cleared `MIN_HITS` at the full count can fall under it at the reduced
    one and be excluded. A silent reduction would publish exactly that as an ordinary rating.
    """
    if kept <= 0:
        return default
    if kept * default <= MAX_TOTAL_RAYS:
        return default
    scaled = MAX_TOTAL_RAYS // kept
    if scaled < MIN_RAYS_PER_EMITTER:
        raise RayBudgetExceeded(
            f"{kept} luminaire instances are in front of this eye point, and sampling them would "
            f"cost more than this call's {MAX_TOTAL_RAYS}-ray budget even at the "
            f"{MIN_RAYS_PER_EMITTER}-ray minimum per instance ({MAX_TOTAL_RAYS // MIN_RAYS_PER_EMITTER} "
            "instances is the most that can be sampled in one call). The whole payload is built in "
            "memory before rtrace starts, so no timeout bounds it. Rate a view with fewer "
            "instances in front of the eye, or split the installation into the positions UGR is "
            "actually assessed at."
        )
    return scaled


@dataclass(frozen=True)
class SourceRow:
    direction: Vec
    omega_sr: float
    luminance_cd_m2: float
    hits: int


def reduce_samples(cone: Cone, samples: list[tuple[Vec, float]], n_rays: int) -> SourceRow | None:
    """`samples` are the (direction, luminance) pairs whose ray hit this instance's own emitter.

    Returns `None` when the instance is under-resolved, so the caller refuses rather than
    publishing a solid angle built from counting noise. A zero-luminance row is never returned.
    """
    if len(samples) < MIN_HITS:
        return None
    omega = len(samples) / n_rays * cone.omega_sr
    mean = sum(lum for _d, lum in samples) / len(samples)
    return SourceRow(direction=cone.axis, omega_sr=omega, luminance_cd_m2=mean, hits=len(samples))


class BackgroundNotPositive(ValueError):
    """`E_total` minus the rows' own direct part is zero or negative.

    `glarendx` answers `inf` for zero and `nan` for a negative, both at exit 0 with empty stderr
    (design §4, F12), and `inf` serialises to `Infinity`, which is not valid JSON. Refusing here is
    the only place this is visible.
    """


def direct_illuminance_lux(view_direction: Vec, rows: list[SourceRow]) -> float:
    u = _normalise(view_direction)
    total = 0.0
    for row in rows:
        d = _normalise(row.direction)
        total += row.luminance_cd_m2 * row.omega_sr * sum(d[i] * u[i] for i in range(3))
    return total


def indirect_illuminance_lux(e_total_lux: float, view_direction: Vec, rows: list[SourceRow]) -> float:
    """`E_total - E_direct`: the vertical illuminance at the eye that the sampled sources do not
    account for. This is the quantity the glarefile's indirect-illuminance row carries and the one
    design §5 publishes as `indirect_illuminance_lux`.

    It exists as its own function (fix round 1, item 4) because the caller needs *both* it and
    `L_b`, and computing one from the other outside this module meant `rad_mcp` multiplying a
    background luminance by pi -- arithmetic about background luminance, in the layer that may not
    do any, and a round trip through `/pi` besides. `background_luminance` is defined in terms of
    this function rather than beside it, so the published pair cannot disagree about what was
    subtracted or about when the subtraction is refused.
    """
    indirect = e_total_lux - direct_illuminance_lux(view_direction, rows)
    if indirect <= 0.0:
        raise BackgroundNotPositive(
            f"the background illuminance at the eye came out {indirect:.6f} lux, which is not positive: "
            "the sampled luminaires account for all of the measured vertical illuminance or more. "
            "glarendx answers inf or nan for this at exit 0, so no rating is published."
        )
    return indirect


def background_luminance(e_total_lux: float, view_direction: Vec, rows: list[SourceRow]) -> float:
    """`L_b = (E_total - E_direct) / pi` -- the basis `glarendx -t ugr` uses (design §4, F2)."""
    return indirect_illuminance_lux(e_total_lux, view_direction, rows) / math.pi
