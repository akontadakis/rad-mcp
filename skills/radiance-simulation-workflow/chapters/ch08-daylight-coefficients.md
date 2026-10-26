# Chapter 8: The Daylight Coefficient Method

## Core Idea
Running 8,760 individual simulations is infeasible. The Daylight Coefficient
method instead pre-computes *how much* each discrete sky patch contributes to
each sensor — once — then evaluates any hour of the year as a fast matrix
multiply. Every annual method in Radiance is a refinement of this idea.

## Frameworks Introduced

- **The DC equation**: `E = DC · S`
  - `E` — illuminance vector at the sensor points
  - `S` — sky luminance vector for a given time step
  - `DC` — the daylight coefficient matrix (sensor × sky patch)
  - When to use: any annual or multi-timestep study.
  - Why it works: the expensive part (light transport geometry) is independent
    of the sky, so it is computed once and reused for all 8,760 hours.

- **Sky discretization**: the sky dome is subdivided into patches, each a
  `source` with a unique modifier name, so `rcontrib` can attribute
  contributions per patch.
  - How: commonly the 145-patch Reinhart subdivision (MF:1); higher subdivision
    factors (MF:2, MF:4…) give finer angular resolution at higher cost.
  - A `.cal` function file maps ray directions to bin numbers.

- **`rcontrib` binning**: the mechanism that turns one trace into N per-patch
  coefficients.
  - How: `-f <cal>` supplies the direction→bin function, `-b <var>` names the
    bin variable, `-bn <N>` the bin count, `-m <modifier>` the modifier to
    attribute contributions to.

- **The two-phase limitation** — and why 3PM exists: the DC matrix bakes the
  *entire* path (sky → exterior → window → interior → sensor) into one set of
  coefficients. Changing the glazing means recomputing everything. The
  Three-Phase Method (Ch 9) exists precisely to break that coupling.

## Key Concepts
- **Daylight coefficient** — the transfer relationship between one sky patch and one sensor point.
- **Sky patch** — one discretized region of the sky dome, with its own modifier.
- **Reinhart subdivision (MF)** — sky discretization scheme; MF:1 = 145 patches.
- **`.smx` / sky matrix** — annual sky luminances, one column per timestep.
- **`.wea`** — Radiance-native weather format, converted from EPW.
- **Bin** — the index a traced ray is attributed to, per the `.cal` function.

## Reference Tables

### Annual sky generation chain
| Step | Program | Output |
|---|---|---|
| EPW → Radiance weather | `epw2wea in.epw out.wea` | `.wea` (2 positional args, no stdout) |
| Annual sky matrix | `gendaymtx` | `.smx` (per-patch luminance per hour) |
| Point-in-time sky vector | `gensky` | `.rad` / `.skv` |

`gendaymtx -m 1` → MF:1 (145 patches). Raise `-m` for finer discretization:
`1 + 144·MF²` patches — MF:1 = 145, MF:2 = 577, MF:4 = 2,305, MF:6 = 5,185
(plus one ground patch).

**Compliance floor.** MF:1 is the Radiance default but **not** always admissible:
per LM-83 §2.3.1–2.3.2, sDA/ASE work requires a minimum of **2,305 sky patches**
— i.e. **`-m 4`** — along with TMY hourly data, Perez skies, and solar position
within 5° of actual. Defaulting to `-m 1` for an LM-83 study is a compliance
failure that no Radiance error will flag. See the `lighting-standards-compliance`
skill (ch04) for the obligation; this chapter only covers the mechanism.

### Timestamps: the half-hour convention (and why it is already right)

Each `.wea` line becomes **one column** of the sky matrix, and the *standard
time* on that line is what fixes the solar position. LM-83 §2.2.1 requires the
**half hour** — TMY data is a band average, so "hour 9" covers 08:00–09:00 and
should be evaluated at **08:30**.

`epw2wea` already does this. A converted file begins:
```
place ATHENS_GRC
latitude 37.90
longitude -23.73          # negative = EAST (see Ch 2)
time_zone -30
weather_data_file_units 1
1 1 0.500 0 0             # Jan 1, 00:30  <- half-hour, not 0.000
1 1 1.500 0 0             # Jan 1, 01:30
```
The `0.500` / `1.500` column is the half-hour timestamp, so the standard
`epw2wea | gendaymtx` chain satisfies the requirement **without intervention**.

Two things can still break it:
- **Hand-built or re-timestamped `.wea` files** using whole hours (`0.000`,
  `1.000`) shift every solar position by 30 minutes across all 8,760 steps —
  which biases ASE most, since it is a direct-sun metric.
- `gendaymtx -i <minutes>` is **not** this correction; it is a capture-interval
  adjustment applied only near sunrise and sunset. Do not reach for it to "fix"
  timestamps.

Verify with `head` on the `.wea` before a compliance run — the fractional hour
should end in `.5`.

### `rcontrib` options for a DC matrix
| Option | Meaning |
|---|---|
| `-I+` | Compute **irradiance** contributions |
| `-h` | Suppress header |
| `-w` | Suppress warnings |
| `-f reinhart.cal` | Function file mapping directions → bins |
| `-b tbin` | Variable in the `.cal` holding the bin number |
| `-bn 145` | Number of bins (sky patches) |
| `-m sky_glow` | Modifier to attribute contributions to |

## Code Examples

Generate the annual sky matrix:
```bash
epw2wea weather.epw weather.wea      # two positional args — does NOT pipe
gendaymtx -m 1 weather.wea > sky.smx
```

Generate a DC matrix:
```bash
rcontrib -I+ -h -w -f reinhart.cal -b tbin -bn 145 -m sky_glow \
         [render_options] scene.oct < points.pts > daylight_coeffs.mtx
```

## Worked Example — why the matrix shape matters

For 100 sensor points and an MF:1 sky:

```
DC : 100 sensors × 145 patches
S  : 145 patches × 8760 hours
E  = DC · S  →  100 × 8760   (every point, every hour)
```

The `rcontrib` run costs the same whether you later evaluate one hour or all
8,760 — the sky matrix is what carries the time dimension. That asymmetry is
the whole economic argument for the method: pay once for geometry, then time is
nearly free.

Swap the glazing, though, and `DC` is invalid — the glazing sits *inside* the
path those coefficients encode. That is the limitation Ch 9 removes.

## Anti-patterns
- **Using DC/two-phase when comparing glazing options** — you recompute the
  expensive matrix per option; use 3PM instead.
- **Mismatched bin counts** — `-bn` must match the `.cal` subdivision and the
  `gendaymtx -m` factor, or contributions land in the wrong patches.
- **Forgetting `-I+`** — you get radiance, not irradiance, and lux conversion is
  then wrong.
- **Relying on DC for direct sun / ASE** — a sky patch smears the solar disc;
  use 5PM (Ch 10).

## Key Takeaways
1. `E = DC · S` — geometry once, sky per timestep.
2. `epw2wea in.epw out.wea` then `gendaymtx -m 1 out.wea` produces the annual
   `.smx` — `epw2wea` takes two positional filenames and cannot be piped.
3. `rcontrib` needs `-f`/`-b`/`-bn`/`-m` consistent with the sky subdivision.
4. Two-phase DC couples glazing into the matrix — the reason 3PM exists.
5. DC smears direct sun across a patch — the reason 5PM exists.

## Connects To
- **Ch 9**: Three-Phase splits this matrix into V·T·D.
- **Ch 10**: Five-Phase corrects the direct solar term.
- **Ch 11**: what to do with the resulting annual illuminance file.
