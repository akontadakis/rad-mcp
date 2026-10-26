"""`rtrace` for view-out and sunlight-exposure: one spawn, casting every ray in a single call and
reporting per ray whether it hit something, what it hit, and how far away.

**Scope.** Both EN 17037 axes this phase builds -- view-out's obstruction angle and
sunlight-exposure's beam-visibility check -- reduce to the same primitive: cast a ray from a point
in a direction and ask whether it escapes or hits, and if it hits, at what distance. This module
owns that one primitive: the `rtrace` argv, the single spawn, and turning its stdout into a
`Sighting` per ray. It owns no geometry (which directions to cast, in `rad_core`) and no physics
(what an obstruction angle or a sun-visibility fraction means, also `rad_core`).

**One spawn per call, regardless of ray count** (mirrors `rad_engine.glarendx.sample_emitters`,
the closest existing model): every ray a caller wants -- a whole grid, a whole day's minutes --
goes on one `rtrace` stdin, in one call to `rad_engine.run.run`. The spawn count must not grow
with the ray count.

**The engine fact this module exists to respect, measured on this machine (F1).** A ray that hits
nothing prints modifier `*` (`rad_engine.rtrace.ESCAPED_MODIFIER`), **not** Radiance's `void`, and
its reported intersection point is `(0, 0, 0)` -- a finite, plausible-looking coordinate. A
distance computed from a miss's point would be a real number (the distance from the ray origin to
the world origin) and nothing downstream could tell it from an answer. So every `Sighting` tests
the modifier **before** any distance is computed: `distance_m` is `None` on an escape, never a
number derived from `(0, 0, 0)`.

**A `None` row from the parser is not a miss.** `rad_engine.rtrace.parse_rtrace_hits` returns
`None` for a row it could not read -- a truncated or malformed line, not a ray that reached the
sky. Converting the first into the second would report an unobstructed view where the truth is an
unreadable answer, so it raises `RtraceOutputError` instead.

No ambient bounces (`-ab 0`): both axes this module serves are pass/fail geometric queries against
the first surface a ray meets -- what it hit and how far -- and neither reads an ambient-lit
radiance value the way `rad_engine.glarendx.vertical_illuminance_at` does. `sample_emitters` makes
the identical choice for the identical reason (P10 in that module's docstring).
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from .cores import worker_cores
from .env import RadianceEnv
from .rtrace import ESCAPED_MODIFIER, RtraceOutputError, parse_rtrace_hits
from .run import run

#: A 3-tuple of floats -- a point or a direction, depending on context. Defined locally rather than
#: imported from a sibling module, matching `rad_engine.rtrace`'s own `Vec`: this module's purity
#: boundary is building argv and parsing output, and an import solely for a type alias would be
#: exactly the cross-module dependency that boundary watches for.
Vec = tuple[float, float, float]

__all__ = [
    "PROBE_LIMIT_REFLECTIONS",
    "PROBE_LIMIT_WEIGHT",
    "PROBE_SKY_MODIFIER",
    "PROBE_SKY_TEXT",
    "Sighting",
    "any_sun_reaches",
    "cast",
    "sun_ray_radiance",
]


@dataclass(frozen=True)
class Sighting:
    """One ray's outcome: escaped, or hit something at a known distance.

    `distance_m` is `None` exactly when `escaped` is `True`, and is never derived from an escape's
    reported `(0, 0, 0)` point (module docstring, F1). `modifier` is `rtrace`'s own token --
    `ESCAPED_MODIFIER` ("*") on an escape, the name of whatever was struck otherwise.
    """

    direction: Vec
    escaped: bool
    modifier: str
    distance_m: float | None


def cast(
    env: RadianceEnv,
    octree: Path,
    *,
    origins_and_directions: list[tuple[Vec, Vec]],
    cwd: Path | str,
    timeout_s: float,
) -> list[Sighting]:
    """Cast every `(origin, direction)` ray in one `rtrace` spawn; one `Sighting` per ray, in
    input order.

    `-h -faa -ovpm -ab 0`: `-h` keeps the header off stdout (as `rad_engine.rtrace` already
    relies on), `-faa` is the ASCII form of `-ovpm`'s three fields, and `-ovpm` prints the
    radiance value, the intersection point and the modifier per ray -- `parse_rtrace_hits`'s own
    contract. `-ab 0` is a cost cut with no bearing on what this module reports (module docstring).
    `-n` = `rad_engine.cores.worker_cores()` goes immediately before the octree (R780); at `-ab 0`
    there is no ambient cache for the workers not to share.
    """
    # **Origins at `.17g`, which round-trips any float exactly** (final whole-branch review, C1):
    # they were `{:g}`, six significant digits, which between 1 000 and 9 999 m moves an origin by
    # up to 5 mm -- more than the 1 mm self-intersection offset the callers start a ray outside the
    # glass by. Measured: a ray started at x = 1234,5621, 1 mm outside a pane at 1234,5611, was
    # written 1234,56, on the pane's room side, and met its own glass. At 4 512 345,67 m the
    # spelling was 4,51235e+06, 4,33 m off. Directions stay at `.9g`: they are unit vectors, where nine digits
    # are an angle of ~1e-9 rad.
    lines = [
        f"{ox:.17g} {oy:.17g} {oz:.17g} {dx:.9g} {dy:.9g} {dz:.9g}"
        for (ox, oy, oz), (dx, dy, dz) in origins_and_directions
    ]
    result = run(
        env, ["rtrace", "-h", "-faa", "-ovpm", "-ab", "0", "-n", str(worker_cores()), str(octree)],
        cwd=cwd, timeout=timeout_s, stdin="".join(f"{line}\n" for line in lines),
    )
    hits = parse_rtrace_hits(result.stdout)
    if len(hits) != len(origins_and_directions):
        raise RtraceOutputError(
            f"rtrace returned {len(hits)} rows for {len(origins_and_directions)} rays. The rows "
            "are matched to rays by index, so a short or long answer cannot be attributed to any "
            "particular ray; nothing was published from this call."
        )

    sightings: list[Sighting] = []
    for (origin, direction), hit in zip(origins_and_directions, hits):
        if hit is None:
            raise RtraceOutputError(
                f"rtrace printed a row for ray origin={origin} direction={direction} that did not "
                "parse -- fewer than 7 tab-separated fields, or a non-numeric one. This is an "
                "unreadable row, not a ray that reached the sky, and the two are never conflated."
            )
        point, modifier, _rgb = hit
        escaped = modifier == ESCAPED_MODIFIER
        distance_m = None if escaped else math.dist(origin, point)
        sightings.append(
            Sighting(direction=direction, escaped=escaped, modifier=modifier, distance_m=distance_m)
        )
    return sightings


#: The probe's sky (ruling R848): a unit `glow` covering the upper hemisphere (ruling R859), so a ray
#: that leaves the scene above the horizon returns radiance 1 times whatever it was transmitted
#: through, and one that leaves below it returns 0. Radius 0, so it is never a light source and
#: lights nothing at `-ab 0`. Named so it cannot collide with a scene modifier.
PROBE_SKY_MODIFIER = "rad_mcp_probe_sky"
#: R859: the upper hemisphere only (`4 0 0 1 180`). The sun is never below the horizon, so a probe
#: ray that leaves the scene downward is not sun; with the full sphere a ray bounced down by a mirror
#: and out through an opening counted as reaching (Phase 25, measured).
PROBE_SKY_TEXT = (
    f"void glow {PROBE_SKY_MODIFIER}\n0\n0\n4 1 1 1 0\n\n"
    f"{PROBE_SKY_MODIFIER} source rad_mcp_probe_sky_src\n0\n0\n4 0 0 1 180\n"
)

#: `-lw`/`-lr` for the probe trace: a path through several low-transmission panes must not be cut
#: off by rtrace's default weight limit and read as blocked. Chosen (docs/tool-surface.md,
#: Unsourced values); too high a weight limit errs towards "blocked", the favourable direction.
PROBE_LIMIT_WEIGHT = 1e-9
PROBE_LIMIT_REFLECTIONS = 64


def any_sun_reaches(
    env: RadianceEnv,
    octree: Path,
    origins: Sequence[Vec],
    directions: Sequence[Vec],
    *,
    cwd: Path | str,
    timeout_s: float,
) -> bool:
    """Whether any ray from any of `origins` along any of `directions` carries radiance out of
    `octree`, which must hold `PROBE_SKY_TEXT` -- the occlusion probe's question (rulings R831,
    R848), answered with **one** `rtrace` spawn.

    **Transmission-aware, not first-hit** (R848). `rtrace -ab 0 -av 0 0 0` follows a ray through
    every surface whose transmission a direct ray follows (`glass`, the specular part of `trans`,
    measured; a lone single-surface `dielectric` only when the ray meets it from its back side --
    from the front it enters the medium and, with no second surface to leave by, is absorbed, so
    the probe gives no verdict on `dielectric`, ruling R857)
    out to the unit glow sky, so a ray that leaves through an outer pane
    returns that pane's transmittance, and a ray that meets a scene's own sky or a light returns its
    radiance. Any channel above zero is "reaches". A first-hit test counted a transmitting pane, or a
    sky in the scene, as a blocker and sealed ASE 0 % through it (measured by the Task 12 review).

    Every origin is paired with every direction, so the spawn carries
    `len(origins) * len(directions)` rays; the rows are matched to rays by count.

    **An empty `origins` or `directions` is a `ValueError`, never `False`.** No ray cast and no ray
    reaching are the same `False` here, and the caller seals `ASE = 0 %` on `False` -- the
    empty-list trap `rad_engine.project._refuse_an_empty_ase_matrix` already refuses for `ase_suns`.
    A non-finite or unreadable row raises `RtraceOutputError` rather than reading as zero.

    Shares its trace with `sun_ray_radiance` (R858).
    """
    return any(
        any(v > 0.0 for v in channels)
        for channels in _probe_trace(env, octree, origins, directions, cwd=cwd, timeout_s=timeout_s)
    )


def sun_ray_radiance(
    env: RadianceEnv,
    octree: Path,
    origins: Sequence[Vec],
    directions: Sequence[Vec],
    *,
    cwd: Path | str,
    timeout_s: float,
) -> tuple[float, ...]:
    """Per ray, the radiance it carries out of `octree` (which must hold `PROBE_SKY_TEXT`), as the
    sum of its three channels: one entry per ray, origin-major (`for o in origins for d in
    directions`), from **one** `rtrace` spawn -- the values R858's difference trace compares
    between the all-open and the closed scene.

    A value, not a yes/no (Task 2 review): a ray that meets `glass` or `trans` splits into a
    transmitted and a reflected branch, and when one branch leaves through the closed group and
    another reaches the sky some other way the ray reaches both open and closed; only its radiance
    drops.

    The `rtrace` argv, the row-count check, the non-finite check and the `ValueError` on an empty
    `origins` or `directions` are `any_sun_reaches`' (whose docstring says why each exists).
    """
    return tuple(
        sum(channels)
        for channels in _probe_trace(env, octree, origins, directions, cwd=cwd, timeout_s=timeout_s)
    )


def _probe_trace(
    env: RadianceEnv,
    octree: Path,
    origins: Sequence[Vec],
    directions: Sequence[Vec],
    *,
    cwd: Path | str,
    timeout_s: float,
) -> list[tuple[float, float, float]]:
    """The probe's one `rtrace` spawn: per ray, origin-major, its three radiance channels."""
    if not origins or not directions:
        raise ValueError(
            f"the probe trace was given {len(origins)} origin(s) and {len(directions)} "
            "direction(s), so no ray would be cast. No ray cast is not the same fact as no ray "
            "reaching, and reading it as False would report that no sun reaches the grid."
        )
    lines = [
        f"{ox:.17g} {oy:.17g} {oz:.17g} {dx:.9g} {dy:.9g} {dz:.9g}\n"
        for (ox, oy, oz) in origins for (dx, dy, dz) in directions
    ]
    result = run(
        env,
        [
            "rtrace", "-h", "-faa", "-ov", "-ab", "0", "-av", "0", "0", "0",
            "-lw", f"{PROBE_LIMIT_WEIGHT:g}", "-lr", str(PROBE_LIMIT_REFLECTIONS),
            "-n", str(worker_cores()), str(octree),
        ],
        cwd=cwd, timeout=timeout_s, stdin="".join(lines),
    )
    rows = [line.split() for line in result.stdout.splitlines() if line.strip()]
    if len(rows) != len(lines):
        raise RtraceOutputError(
            f"rtrace returned {len(rows)} rows for {len(lines)} probe rays; the rows are matched "
            "to rays by count, so the answer cannot be read."
        )
    traced: list[tuple[float, float, float]] = []
    for row in rows:
        try:
            values = [float(token) for token in row[:3]]
        except ValueError:
            values = []
        if len(values) != 3 or not all(math.isfinite(v) for v in values):
            raise RtraceOutputError(
                f"rtrace printed a probe row {' '.join(row)!r} that is not three finite numbers. An "
                "unreadable row is not a ray that was blocked, and the two are never conflated."
            )
        traced.append((values[0], values[1], values[2]))
    return traced
