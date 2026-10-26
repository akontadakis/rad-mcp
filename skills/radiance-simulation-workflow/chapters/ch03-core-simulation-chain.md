# Part III: Core Simulation Steps

## Core Idea
A daylight factor run is a five-step pipeline: build the sky, compile the
octree, generate sensor points, trace illuminance, divide by the exterior
reference. Each step's output is the next step's input, and two of the steps
have silent failure modes that produce a *wrong number* rather than an error.

## Frameworks Introduced

- **The five-step DF chain**:
  1. `gensky` → sky description (`sky.rad`)
  2. `oconv` → octree (`scene.oct`)
  3. point generation → `calc_points.txt`
  4. `rtrace -I` + `rcalc` → illuminance in lux
  5. `rcalc` → daylight factor %
  - When to use: any point-grid daylight factor study.
  - How: see the worked example — each step below.

- **The sky is two files, not one** — `gensky` output alone emits no light.
  - When to use: every daylight simulation. This is the guide's most
    consequential warning.
  - How: `gensky` defines the *distribution and materials* (`skyfunc`, `solar`)
    but **not the hemispherical surfaces**. You must separately author a
    `sky_geometry.rad` containing `glow` + `source` primitives for the sky dome
    and ground plane.
  - Failure mode: skip it and the scene is lit by nothing. Radiance does not
    complain; you get zeros or near-zeros.

- **`oconv` argument order is semantic, not cosmetic**:
  `materials → geometry → gensky output → sky geometry`.
  - When to use: every octree build.
  - Why: a modifier must be *defined before* it is referenced. `sky_geometry.rad`
    uses `skyfunc`, which `sky.rad` defines — so `sky.rad` must precede it.

- **The photometric conversion**: Radiance traces radiometric RGB; lux comes
  from the luminous efficacy weighting.
  - How: `illuminance = 179 × (0.265·R + 0.670·G + 0.065·B)`
  - The weights sum to 1.0, so 179 is the lm/W efficacy constant.

- **The `-o` field-offset rule** — the single most common way to get a wrong
  number out of a correct pipeline. **Each letter in `-o` is one output item**,
  and each contributes its own field count:
  | Letter | Meaning | Fields |
  |---|---|---|
  | `o` | Ray origin | 3 |
  | `d` | Ray direction (normalized) | 3 |
  | `v` | **Value (radiance/irradiance RGB)** | 3 |

  | `rtrace` invocation | Total fields | RGB in | `rcalc` expression |
  |---|---|---|---|
  | `rtrace -I -h` (no `-o`) | 3 | `$1 $2 $3` | `-e '$1=179*($1*0.265+$2*0.670+$3*0.065)'` |
  | `rtrace -I -h -oov` | **6** | **`$4 $5 $6`** | `-e '$1=179*($4*0.265+$5*0.670+$6*0.065)'` |
  | `rtrace -I -h -oodv` | 9 | `$7 $8 $9` | `-e '$1=179*($7*0.265+$8*0.670+$9*0.065)'` |

  - `-oov` is `o` + `o` + `v`? **No** — it is `-o` (the flag) followed by the
    spec `ov`: origin then value. Six fields, RGB in 4–6. Direction is **not**
    included; that would require `d`.
  - **Verified empirically**: `rtrace -I -h -oov -ab 1 sky.oct` on a 10,000 lux
    calibrated sky returns 6 fields, and `$4 $5 $6` yields 9985 lux — matching
    the no-`-o` form exactly. Using `$7 $8 $9` returns **0**, silently.
  - Failure mode: the wrong index set either reads past the record (→ 0) or
    weights coordinates instead of radiometry. Count the letters, count the fields.

- **Glow colour is a multiplier on `skyfunc` — keep it neutral `1 1 1`.**
  `skyfunc` already carries the absolute magnitude set by `gensky -b`/`-B`, so
  the glow RGB only scales it. Anything below 1 dims the calibrated sky.
  - **Measured**: on a sky calibrated to 10,000 lux, glow `1 1 1` returns
    **9982 lux**; the tinted values `0.9 0.95 1.05` (sky) / `1.0 0.9 0.8`
    (ground) return **9416 lux** — a **5.7% shortfall** that silently biases
    every DF built on it, because the denominator stays the nominal 10,000.
  - Use a tint only when you deliberately want one (e.g. a coloured ground),
    and then re-derive the exterior reference rather than assuming it.
  - **Do not "normalise" the glow with `0.265R + 0.670G + 0.065B = 1`** — that
    is the photopic weighting for converting radiance to lux (above), and it
    does not belong here. Applying it produces exactly the dimming measured.

## Key Concepts
- **Octree (`.oct`)** — binary spatial structure `oconv` compiles for efficient ray tracing.
- **`skyfunc`** — the sky luminance-distribution pattern emitted by `gensky`.
- **`source`** — a primitive describing a distant/infinite emitter by direction and subtended angle.
- **Sensor record** — one line of `x y z dx dy dz`; direction `0 0 1` = upward-facing horizontal sensor.
- **`-I`** — `rtrace` flag computing irradiance for a sensor at a point/orientation.
- **`-oov`** — output format: Origin, view Direction, then Value (RGB).

## Reference Tables

### `rtrace` flags for illuminance
| Flag | Effect |
|---|---|
| `-I` | Irradiance at (x,y,z) for sensor along (dx,dy,dz) — required for illuminance |
| `-h` | Suppress rtrace's own header |
| `-oov` | Output spec `ov`: Origin (3) + Value (3) = 6 fields; **RGB in 4,5,6** |

### `oconv` ordering
| Order | File | Why |
|---|---|---|
| 1 | `materials.mat` | Modifiers must exist before use |
| 2 | `room.rad` | Geometry references those materials |
| 3 | `sky.rad` (gensky) | Defines `skyfunc` |
| 4 | `sky_geometry.rad` | *Uses* `skyfunc` — must come after |

## Code Examples

**Step 1 — sky description**
```bash
gensky $MONTH $DAY $TIME -c -b $ZENITH_BRIGHTNESS \
    -a $LATITUDE -o $LONGITUDE -m $MERIDIAN > $SKY_FILE
if [ $? -ne 0 ]; then echo "Error generating sky. Exiting."; exit 1; fi
```

**Step 1b — the sky geometry you must write yourself**
```
# sky_geometry.rad
# 'skyfunc' must be defined by gensky (sky.rad) before oconv reads this file.

skyfunc glow sky_glow_material
0
0
4  1 1 1  0              # neutral multiplier on skyfunc; 4th arg = maxrad

sky_glow_material source sky_dome_surface
0
0
4  0 0 1  180            # Dx Dy Dz + subtended angle: zenith vector, full hemisphere

skyfunc glow ground_glow_material
0
0
4  1 1 1  0

ground_glow_material source ground_plane_surface
0
0
4  0 0 -1  180           # nadir vector
```
- **What it demonstrates**: the sky dome and ground are ordinary `source`
  primitives; without them the octree contains a light *distribution* with no
  surface to emit from.

**Step 2 — octree**
```bash
oconv $MATERIAL_FILE $GEOMETRY_FILE $SKY_FILE $SKY_GEOMETRY_FILE > $OCTREE_FILE
```

**Step 3 — sensor grid** (conceptual `cnt` + `rcalc` form)
```bash
cnt $(($X_POINTS * $Y_POINTS)) | \
rcalc -e "COLX=1+(recno-1)%$X_POINTS;COLY=1+floor((recno-1)/$X_POINTS);" \
      -e "x=$X_START+(COLX-1)*$X_STEP; y=$Y_START+(COLY-1)*$Y_STEP; z=$WP_HEIGHT;" \
      -e "dx=0; dy=0; dz=1;" \
      -o "\$x \$y \$z \$dx \$dy \$dz" > calc_points.txt
```

**Grid placement is a compliance question, not a free choice.** The generator
above places points *at* `X_START + n·step` — i.e. at grid origins. EN 12464 and
EN 17037 require points at **cell centres**, with a perimeter band excluded, so
for compliance work `X_START` must be `perimeter + step/2`, not `0`. Spacing
itself is formula-driven and differs per standard. Get the geometry from the
`lighting-standards-compliance` skill (patterns.md) before generating; this
chapter only shows the mechanism.

**Step 4 — illuminance**
```bash
cat $POINTS_FILE | rtrace -I -h -oov \
    -ab $AB_BOUNCES -ad $AD_SAMPLES -as $AS_SAMPLES -ar $AR_RESOLUTION -aa $AA_ACCURACY \
    $OCTREE_FILE | \
    rcalc -e '$1=179*($4*0.265 + $5*0.670 + $6*0.065)' > $ILLUMINANCE_VALUES_FILE
```

**Step 5 — daylight factor**
```bash
cat $ILLUMINANCE_VALUES_FILE | \
    rcalc -e "\$1 = \$1 / $EXTERNAL_HORIZONTAL_ILLUMINANCE * 100" > $DAYLIGHT_FACTORS_FILE

# Pair points with results:
paste $POINTS_FILE $DAYLIGHT_FACTORS_FILE | \
  awk '{printf "Point (%.2f, %.2f, %.2f): DF = %.2f%%\n", $1, $2, $3, $7}'
```

## Worked Example — one point, end to end

`rtrace -I -h -oov` returns **6 fields** per sensor — origin then RGB:
```
0.000000e+00  0.000000e+00  1.000110e+00   0.612  0.598  0.571
└──────── origin ($1 $2 $3) ────────┘   └── RGB ($4 $5 $6) ──┘
```
The irradiance is in `$4 $5 $6` (W/m²).

**Convert to lux:**
```
E = 179 × (0.265×0.612 + 0.670×0.598 + 0.065×0.571)
  = 179 × (0.1622 + 0.4007 + 0.0371)
  = 179 × 0.5999 ≈ 107.4 lux
```

**Convert to daylight factor**, against the 10,000 lux exterior fixed in Ch 2:
```
DF = 107.4 / 10000 × 100 = 1.07 %
```

Note the exterior reference (10,000) must be the *same* number that produced
`gensky -b 22.86`. If someone re-runs the sky at a different brightness but
leaves the divisor at 10,000, every DF in the report is wrong by that ratio —
and nothing in the pipeline signals it.

## Anti-patterns
- **Trusting `gensky` output alone to light the scene** — it is a modifier, not
  a light source. Results come back near zero with no error. *(This and the
  positional date argument are the two classic silent zero-lux traps.)*
- **Reordering `oconv` arguments** — geometry before materials, or
  `sky_geometry.rad` before `sky.rad`, breaks modifier resolution.
- **Reading RGB from the wrong fields** — with `-oov` the values are fields
  **4,5,6** (origin is 3 fields, then value). `$7 $8 $9` reads past the end of
  the record and returns 0 with no error.
- **Letting the DF denominator drift from the sky calibration** — see above.
- **Accepting the placeholder `sky_geometry.rad`** — the script's auto-generated
  stub exists so the pipeline *runs*, not so it is *correct*.
- **Omitting `-I`** — without it `rtrace` returns radiance along a ray, not
  irradiance at a sensor; the number looks plausible and means something else.

## Key Takeaways
1. `gensky` output is a modifier; you must author sky/ground `source` geometry
   separately or the scene is unlit.
2. `oconv` order is materials → geometry → sky → sky geometry.
3. `rtrace -I -h -oov` gives 6 fields; RGB are **4,5,6**. Count the `-o` letters:
   each of `o`/`d`/`v` contributes 3 fields.
4. lux = `179 × (0.265R + 0.670G + 0.065B)`.
5. DF = interior lux ÷ exterior lux × 100, with the exterior value the same one
   used to calibrate the sky.
6. Check each step's exit status — a failed `oconv` otherwise feeds a stale
   octree into a run that appears to succeed.

## Connects To
- **Ch 2**: supplies the zenith brightness and ambient parameters used here.
- **Ch 1**: `glow` / `source` primitives follow the standard grammar.
- **Ch 4**: the same octree feeds `rpict` and `falsecolor` mapping.
