# Chapter 11: Annual Metrics — DA, sDA, UDI, ASE

## Core Idea
An annual run produces an `.ill` file of 8,760 illuminance values per sensor.
The metrics are all the same shape: **count hours past a threshold, then count
points past an hour-fraction**. The subtleties are which hours count (occupied
only) and which simulation method is valid for which metric.

## Frameworks Introduced

- **The two-stage threshold pattern**: every one of these metrics reduces an
  8760 × N matrix in two steps.
  1. Per point, count occupied hours meeting an **illuminance** criterion.
  2. Count points meeting a **temporal** criterion; express as % of floor area.
  - When to use: implementing or auditing any annual daylight metric.

- **sDA 300/50%** — Spatial Daylight Autonomy: % of floor area receiving
  ≥ **300 lux** for ≥ **50%** of occupied hours. Measures *sufficiency*.

- **ASE 1000,250** — Annual Sunlight Exposure: % of floor area receiving
  ≥ **1,000 lux** of **direct sunlight** for ≥ **250** occupied hours.
  Measures *excess*.
  - **Critical constraint**: per LM-83, ASE counts **direct sunlight only —
    sky and interreflections are excluded**. It is therefore *not* computed
    from an ordinary total-illuminance annual result. The standard route is a
    direct-only run (`-ab 0`, sun-only sky); with complex fenestration, the
    **Five-Phase** direct term (Ch 10) is the accurate way to obtain it.
  - Feeding a full 3PM total-illuminance `.ill` into the ASE formula is wrong
    twice over: it includes sky and interreflected light that LM-83 excludes,
    **and** 3PM smears the solar disc across a sky patch.

- **UDI bins** — Useful Daylight Illuminance: the % of occupied hours a point
  falls in each range. Unlike sDA/ASE, UDI is reported *per point across bins*,
  not as a single area percentage.

- **Occupied-hours masking**: all of these are defined over occupied hours, not
  all 8,760. Apply the schedule mask before any counting — including it changes
  every result.

## Key Concepts
- **`.ill` file** — annual illuminance, 8,760 rows × N sensor columns.
- **DA (Daylight Autonomy)** — per-point % of occupied hours above a threshold.
- **sDA** — the spatial aggregate of DA across the floor area.
- **ASE** — direct-sun overexposure metric.
- **UDI-f / -s / -a / -e** — fell short / supplementary / autonomous / exceeded.
- **Occupancy schedule** — which hours count; typically a `.csv` in `10_schedules/`.

## Reference Tables

### Metric definitions
| Metric | Illuminance criterion | Temporal criterion | Reports | Requires |
|---|---|---|---|---|
| **sDA 300/50%** | ≥ 300 lux | ≥ 50% of occupied hours | % floor area | 3PM sufficient |
| **ASE 1000,250** | ≥ 1000 lux **direct sun only** | ≥ 250 occupied hours | % floor area | **Direct-only run** (`-ab 0` + sun-only sky); 5PM for CFS |
| **UDI-f** | < 100 lux | — | % of hours, per point | 3PM sufficient |
| **UDI-s** | 100 – 500 lux | — | % of hours, per point | 3PM sufficient |
| **UDI-a** | 500 – 2000 lux | — | % of hours, per point | 3PM sufficient |
| **UDI-e** | ≥ 2000 lux | — | % of hours, per point | 3PM sufficient |

### UDI interpretation
| Bin | Range | Meaning |
|---|---|---|
| Fell short | < 100 lx | Too dark; electric light needed |
| Supplementary | 100–500 lx | Useful, may need supplement |
| Autonomous | 500–2000 lx | Ideal daylight |
| Exceeded | ≥ 2000 lx | Glare / heat gain risk |

## Code Examples

```python
# Mask to occupied hours FIRST — every metric is defined over these only
occupied_illuminance = annual_illuminance[occupied_mask, :]

# sDA 300/50%
hours_above = np.sum(occupied_illuminance >= 300, axis=0)
fraction_above = hours_above / occupied_illuminance.shape[0]
sDA = np.sum(fraction_above >= 0.5) / num_points * 100

# ASE 1000,250 — the .ill here MUST be direct-sun-only (LM-83 excludes
# sky and interreflections), not a total-illuminance annual result.
hours_above_ase = np.sum(occupied_illuminance >= 1000, axis=0)
ASE = np.sum(hours_above_ase >= 250) / num_points * 100

# UDI bins (per point, % of occupied hours)
udi_f = np.mean(occupied_illuminance < 100, axis=0) * 100
udi_s = np.mean((occupied_illuminance >= 100) & (occupied_illuminance < 500), axis=0) * 100
udi_a = np.mean((occupied_illuminance >= 500) & (occupied_illuminance < 2000), axis=0) * 100
udi_e = np.mean(occupied_illuminance >= 2000, axis=0) * 100
```

## Worked Example — sDA on a 100-point grid

Given `occupied_illuminance` of shape `(3650 occupied hours, 100 points)`:

1. **Per point**, count hours ≥ 300 lux → e.g. point 42 has 2,100 hours.
2. **Fraction**: `2100 / 3650 = 0.575` → 57.5% of occupied hours.
3. `0.575 ≥ 0.50`, so point 42 **passes**.
4. Repeat for all 100 points; suppose 68 pass.
5. `sDA = 68 / 100 × 100 = 68%`.

Note the two thresholds do different jobs: 300 lux decides whether an *hour*
counts, 50% decides whether a *point* counts. Confusing them — e.g. averaging
illuminance over the year and comparing to 300 — gives a different and wrong
number, because a point can average well above 300 while failing the temporal
criterion.

## Anti-patterns
- **Computing ASE from a total-illuminance result** — LM-83 counts direct sun
  only, so sky and interreflected light must be excluded from the input, not
  just filtered afterwards. A 3PM total `.ill` fails on both counts (it includes
  diffuse light *and* smears the solar disc).
- **Using all 8,760 hours instead of occupied hours** — inflates or deflates
  every metric; mask first.
- **Averaging annual illuminance and comparing to the threshold** — the metrics
  are hour-counting, not mean-based.
- **Reporting sDA without the 300/50 qualifiers, or ASE without 1000/250** —
  the thresholds are part of the metric's name for a reason.
- **Applying UDI bins as an area percentage** — UDI is per point across bins.

## Key Takeaways
1. Mask to occupied hours before any counting.
2. sDA 300/50%: ≥300 lux for ≥50% of occupied hours, as % of floor area.
3. ASE 1000,250: ≥1000 lux for ≥250 hours from **direct sun only** — LM-83
   excludes sky and interreflections, so use a direct-only run (`-ab 0`,
   sun-only sky), or 5PM's direct term with complex fenestration.
4. UDI bins: <100 / 100–500 / 500–2000 / ≥2000 lux.
5. Two-stage pattern: illuminance criterion per hour, temporal criterion per point.
6. Always state the thresholds alongside the metric.

## Connects To
- **Ch 9**: 3PM is adequate for sDA and UDI.
- **Ch 10**: 5PM's direct term is the accurate ASE route for complex
  fenestration; a plain `-ab 0` sun-only run suffices for simple glazing.
- **lighting-standards-compliance** (`skills/lighting-standards-compliance`):
  the authority on the compliance obligation — this chapter only documents the
  computation. Go there for anything a **report** must satisfy, including
  details this chapter does not carry:
  - ASE is **pre-blind and zero-bounce** (direct beam from the solar orb only,
    after glazing transmittance) — which is *why* the direct-only run below is
    the correct method, not merely a faster one.
  - The 10 h/day analysis period, max **2 ft (0.6 m)** point spacing, and
    small-area point-count corrections.
  - Blind operation rules (close at >2% of points in direct sun) and the
    mandatory **5%** dirt-depreciation factor on vertical glazing.
  - Pass/fail bands (ASE <3% good, >10% unsatisfactory).

  Note LM-83 §2.3.3 does **not** mandate a parameter set — the `-ab 6 / -ad
  1000 / -dt 0` table often quoted is a documented floor, not a requirement.
  Ch 2's presets and Ch 9/10's matrix values are engineering guidance, not
  compliance thresholds.
