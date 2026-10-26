# Chapter 6: Daylight Factor Workflow (canonical)

## Core Idea
A Daylight Factor study fixes the exterior illuminance at a known reference
(conventionally 10,000 lux under a CIE overcast sky) so that interior results
become a ratio independent of absolute sky brightness. The whole workflow is
five programs, and the only subtle part is calibrating the sky.

## Frameworks Introduced

- **Two ways to calibrate the same 10,000 lux sky** — the single most confusable
  point across Radiance references.
  - `gensky <M> <D> <H> -c -B 55.866` — `-B` sets **horizontal diffuse
    irradiance** (W/m²).
    Derivation: `10000 lux ÷ 179 lm/W ≈ 55.866 W/m²`.
  - `gensky <M> <D> <H> -c -b 22.863` — `-b` sets **zenith radiance** (W/m²/sr).
    Derivation: `(9/7) × 10000 / (179 × π) ≈ 22.863`.
  - These describe the **same sky**: `55.866 / 22.863 = 2.4435 = (7/9)·π`.
  - **Trap**: passing a `-B` value to `-b` (or vice versa) over- or
    under-brightens the sky by 2.44× with no error message.
  - When to use: prefer `-B` — the derivation is one division and harder to get
    wrong. Use `-b` when following older scripts or references that assume it.

- **The DF pipeline**: `gensky` **+ hand-authored `sky_geometry.rad`** → `oconv`
  → (grid generation) → `rtrace` → `rcalc`.
  - The sky-geometry step is not optional and is the most commonly dropped one
    — see Ch 3. Omitting it returns 0 lux everywhere with only a warning.
  - When to use: any DF compliance study.
  - How: see Worked Example. `-c` (overcast) is mandatory — DF is *defined*
    against an overcast sky, so a sun would make the result meaningless.

- **Fold the DF ratio into the `rcalc` expression** — one pass, not two.
  ```
  rcalc -e '$1=100 * (179*($1*0.265+$2*0.670+$3*0.065)) / 10000'
  ```
  - Why: keeps the reference illuminance adjacent to the conversion that uses
    it, so the two can't drift apart in a long script.

## Key Concepts
- **Daylight Factor (DF)** — interior illuminance ÷ simultaneous exterior horizontal illuminance × 100, under CIE overcast.
- **`-B`** — `gensky` flag setting horizontal **diffuse** irradiance (W/m²). Under `-c` (overcast) all light is diffuse, so this is the exterior horizontal value.
- **`-b`** — `gensky` flag setting zenith radiance (W/m²/sr).
- **`gengrid`** — optional utility generating the sensor point grid.
- **Reference illuminance** — the fixed exterior value; 10,000 lux by convention.

## Reference Tables

### Sky calibration for a target exterior illuminance E (lux)
| Flag | Quantity set | Formula | E = 10,000 |
|---|---|---|---|
| `-B` | Horizontal **diffuse** irradiance (W/m²) | `E / 179` | **55.866** |
| `-b` | Zenith radiance (W/m²/sr) | `(9/7)·E / (179·π)` | **22.863** |

Conversion between them: `-B = -b × (7/9)·π = -b × 2.4435`.

### Program roles
| Program | Role |
|---|---|
| `gensky` | Generate the CIE overcast sky with a calibrated reference level |
| `oconv` | Compile materials + geometry + sky into the octree |
| `gengrid` | (Optional) generate the sensor grid |
| `rtrace` | Compute irradiance at each sensor point |
| `rcalc` | Convert RGB irradiance → lux, and lux → DF% |

## Worked Example — DF at 10,000 lux, end to end

```bash
# 1. CIE overcast sky calibrated to 10,000 lux exterior horizontal
#    10000 lux / 179 lm/W = 55.866 W/m2
gensky 6 21 12 -c -B 55.866 > sky.rad     # date is POSITIONAL and required

# 1b. The sky GEOMETRY — gensky alone emits no light (Ch 3). Without this file
#     every result below is exactly 0.0, with only a "no light sources" warning.
cat > sky_geometry.rad <<'EOF'
skyfunc glow sky_glow
0
0
4 1 1 1 0

sky_glow source sky
0
0
4 0 0 1 180

skyfunc glow ground_glow
0
0
4 1 1 1 0

ground_glow source ground
0
0
4 0 0 -1 180
EOF

# 2. Octree (materials -> geometry -> sky -> sky geometry; order is semantic)
oconv materials.rad geometry.rad sky.rad sky_geometry.rad > scene.oct

# 3. Trace and convert to DF% in one pipeline
rtrace -I -h -w -ab 4 -ad 1024 -as 512 -ar 512 -aa 0.2 \
       scene.oct < points.pts | \
  rcalc -e '$1=100 * (179*($1*0.265+$2*0.670+$3*0.065)) / 10000' \
  > results_df.txt
```

Note the field indices are `$1 $2 $3` here because `-oov` is **not** used — see
the field-offset rule in Ch 3. If you add `-oov`, they become `$4 $5 $6`.

Sanity check: an interior point reading 107 lux under this sky yields
`107 / 10000 × 100 = 1.07 %` DF — a typical value well inside a room.

**Self-test before trusting a model.** Run the pipeline above against a scene
containing *only* `sky.rad` + `sky_geometry.rad` (no room), with one sensor at
`0 0 1 0 0 1`. An unobstructed exterior point must return **≈100 % DF** —
verified here at **99.84 %**. Any other answer means the sky, the calibration or
the `rcalc` expression is wrong, and you have found it in seconds rather than
after modelling a building. If it returns exactly `0`, you omitted step 1b.

## If this is an EN 17037 compliance study, read this first

The 10,000 lux convention above makes DF **reproducible and comparable** — it is
a modelling convention, not a compliance target. EN 17037 Method 1 adds
requirements this chapter's generic recipe does not carry:

- **The sky is prescribed.** Daylight factors *"shall be calculated by any
  reliable method based on the **ISO 15469:2004 standard overcast sky (TYPE 1 or
  TYPE 16)**"* (B.3.2). `gensky -c` is the CIE overcast distribution, which is
  what that refers to — but the obligation is the standard's, not this chapter's.
- **The target is city-specific.** `D_T` derives from that city's median external
  diffuse illuminance, so there is no universal "good DF". Athens needs
  **1.5 %** to exceed 300 lx; Reykjavik needs **2.6 %**. Applying one city's
  target elsewhere is a silent compliance error.
- **Two different tables** (A.3 vertical/inclined vs A.4 horizontal diffusing
  openings) are keyed on different quantities and must not be mixed.

Get the target and the conditions from the `lighting-standards-compliance` skill
(ch03) **before** interpreting any number this chapter produces.

## Anti-patterns
- **Mixing `-B` and `-b` values** — 2.44× error, silent.
- **Treating a DF value as pass/fail without a city-specific target** — see above.
- **Omitting `-c`** — a sunny sky makes DF undefined.
- **Hard-coding 10,000 in two places that can drift** — fold it into one `rcalc`
  expression, or set it once as a shell variable.
- **Using Draft ambient settings for a DF report** — systematically
  underestimates interreflected light, biasing DF *low* everywhere.
- **Reporting DF without stating the reference illuminance and sky type** —
  the number is not interpretable without them.

## Key Takeaways
1. `-B 55.866` and `-b 22.863` are the same 10,000 lux sky; never interchange
   the values.
2. `-c` is mandatory — DF is defined under CIE overcast.
3. Prefer `-B`: `E / 179`, one division.
4. Fold the ÷10,000 ×100 into the same `rcalc` as the lux conversion.
5. Standard/Good ambient settings (`-ab 4 -ad 1024 -as 512 -ar 512 -aa 0.2`)
   are normally sufficient for DF.

## Connects To
- **Ch 2**: the ambient parameter set used here.
- **Ch 3**: the `-oov` field-offset rule and the photometric conversion.
- **Ch 7**: shares the octree; different sky and analysis.
- **lighting-standards-compliance** (ch03): the EN 17037 obligation — Method 1
  vs Method 2, the ISO 15469 overcast sky, city-specific `D_T`/`D_TM` targets
  from Tables A.3/A.4, and the daylight-hours definition. That skill decides
  what the number must be; this chapter only produces it.
