"""EN 12464-1:2011 zone arithmetic -- Phase 11b design §5. Pure: no IO, no advisories.

`U_o = E_min / E_mean` per zone (licht.de p.7), rounded half-up to two decimals on its decimal value
(licht.de p.8, R616). Limits: surround 0.40 and background 0.10 (licht.de p.8); every task area
`None`, because the 2011 task uniformity is per activity and that table is not in the corpus. There is
no verdict (R608): a caller compares `u_o` with the limit beside it.

This module constructs no `Advisory`/`ArtifactWarning` (Amendment 2): `evaluate` reports `unlit` as a
plain flag on `ZoneResult`, and the caller (`rad_mcp.electric_tool`) is the one layer
allowed to turn a flag into an advisory.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

__all__ = [
    "ASR_SURROUND_UO",
    "BACKGROUND_UO_LIMIT",
    "EDITION",
    "LIMIT_BASIS_SOURCED",
    "LIMIT_BASIS_TASK",
    "ROUNDING_EPSILON",
    "SURROUND_UO_LIMIT",
    "ZONE_MIN_POINTS",
    "ZONE_UNLIT_FRACTION",
    "ZoneResult",
    "evaluate",
    "round_uo",
    "zone_limit",
]

EDITION: Final[str] = "EN 12464-1:2011"
SURROUND_UO_LIMIT: Final[float] = 0.40
#: ASR A3.4's surround uniformity, quoted beside `SURROUND_UO_LIMIT` in `SURROUND_IS_ASR_GEOMETRY` (R619, R652).
ASR_SURROUND_UO: Final[float] = 0.5
BACKGROUND_UO_LIMIT: Final[float] = 0.10
LIMIT_BASIS_SOURCED: Final[str] = "licht.de p.8"
LIMIT_BASIS_TASK: Final[str] = "per_activity_not_in_corpus"
#: Design §5 -- unsourced. A zone this dim against the brightest one is mostly not lit by the lamps.
ZONE_UNLIT_FRACTION: Final[float] = 0.01
#: Design §5 -- unsourced. Below this, `U_o` is an artefact of the count (one point gives 1.0).
ZONE_MIN_POINTS: Final[int] = 3
#: R616: added after the x100, so the tolerance on `u_o_raw` is 1e-11.
ROUNDING_EPSILON: Final[float] = 1e-9


def round_uo(raw: float) -> float:
    return math.floor(raw * 100.0 + 0.5 + ROUNDING_EPSILON) / 100.0


def zone_limit(zone: str) -> tuple[float | None, str]:
    if zone == "background":
        return BACKGROUND_UO_LIMIT, LIMIT_BASIS_SOURCED
    if zone == "surround" or zone.startswith("surround_"):
        return SURROUND_UO_LIMIT, LIMIT_BASIS_SOURCED
    return None, LIMIT_BASIS_TASK


@dataclass(frozen=True)
class ZoneResult:
    zone: str
    group: str | None
    n_points: int
    values: tuple[float, ...]
    e_initial_mean: float
    e_initial_min: float
    e_maintained_mean: float
    e_maintained_min: float
    u_o: float | None
    u_o_raw: float | None
    limit_value: float | None
    limit_basis: str
    unlit: bool


def evaluate(
    zones: Sequence[tuple[str, str | None, Sequence[float]]], maintenance_factor: float
) -> tuple[ZoneResult, ...]:
    if not (math.isfinite(maintenance_factor) and 0.0 < maintenance_factor <= 1.0):
        raise ValueError(f"maintenance_factor must be in (0, 1], got {maintenance_factor!r}")
    means = [math.fsum(v) / len(v) for _, _, v in zones if v]
    brightest = max(means, default=0.0)
    results: list[ZoneResult] = []
    for zone, group, values in zones:
        if not values:
            raise ValueError(f"zone {zone!r} has no values")
        mean = math.fsum(values) / len(values)
        low = min(values)
        raw = None if mean == 0.0 else low / mean
        limit, basis = zone_limit(zone)
        results.append(ZoneResult(
            zone=zone, group=group, n_points=len(values), values=tuple(values),
            e_initial_mean=mean, e_initial_min=low,
            e_maintained_mean=mean * maintenance_factor, e_maintained_min=low * maintenance_factor,
            u_o=None if raw is None else round_uo(raw), u_o_raw=raw,
            limit_value=limit, limit_basis=basis,
            unlit=mean == 0.0 or (brightest > 0.0 and mean < ZONE_UNLIT_FRACTION * brightest),
        ))
    return tuple(results)
