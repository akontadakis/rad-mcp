# Part II: Setting Up Simulation Parameters

## Core Idea
Three parameter families govern a daylight run: **where and when** the sky is
(`gensky` location/date/sky type), **where you measure** (the working-plane
grid), and **how hard Radiance works on interreflection** (the ambient
parameters). The ambient family is the one that trades accuracy against runtime
and needs tuning per scene.

## Frameworks Introduced

- **Zenith brightness calibration**: for a daylight factor study you want a
  *known* exterior horizontal illuminance, so you set the sky's zenith
  brightness to produce it rather than accepting whatever `gensky` defaults to.
  - When to use: any DF calculation, where the denominator must be a fixed,
    stated value.
  - How: `B_zenith (W/m²/sr) = (9/7) × E_horizontal(lux) / (179 × π)`, then pass
    it as `gensky -b <B_zenith>`. For 10,000 lux → **22.86**.
  - Why it works: DF is a *ratio*; fixing the denominator makes results
    comparable and independent of the absolute sky brightness.

- **The ambient parameter set** (`-ab -ad -as -ar -aa -lw -av`): the accuracy /
  speed dial for indirect light.
  - When to use: every `rtrace` and `rpict` run that includes interreflection.
  - How: see the reference table. Raise `-ab` until results stop changing;
    raise `-ad` to kill splotchiness; `-aa` lower = more accurate, much slower.
  - Failure mode: too low `-ab` makes shadowed areas dark and flat — a
    plausible-looking image that is quantitatively wrong.

- **Physically-honest ambient value**: set `-av 0 0 0` when you want the
  interreflection you actually computed.
  - When to use: any physically accurate simulation.
  - How: leave `-av` at zero and let `-ab` do the work. A non-zero `-av` is a
    non-physical "fill light" that fakes what `-ab` should compute.

## Key Concepts
- **Ambient bounces (`-ab`)** — max diffuse reflections traced for indirect light.
- **Ambient divisions (`-ad`)** — initial rays sampling the hemisphere at a point.
- **Ambient super-samples (`-as`)** — extra rays sent where `-ad` found high variance.
- **Ambient resolution (`-ar`)** — density of the cached indirect-value grid.
- **Ambient accuracy (`-aa`)** — error tolerance for interpolating cached values.
- **Limit weight (`-lw`)** — minimum ray contribution still worth tracing.
- **Ambient value (`-av`)** — global fill light; `0 0 0` for physical accuracy.
- **Irradiance cache** — the adaptive-sampling structure holding computed indirect values (`.amb` file), interpolated between rather than recomputed.
- **Meridian** — standard timezone meridian, `15 × offset_from_UTC` (0 = GMT, −75 = EST).
- **Working plane** — the horizontal measurement height, typically 0.75–0.85 m.
- **Backward ray tracing** — Radiance traces from the camera/sensor into the scene, not from sources outward.

## Mental Models
- Think of **`-ad` as smoothness and `-ab` as depth**: `-ad` too low gives
  splotches, `-ab` too low gives darkness. They fix different symptoms.
- Think of **`-ar` and `-aa` as the cache's resolution and tolerance** —
  Radiance stores indirect values sparsely and interpolates between them; these
  two decide how sparse and how loosely.
- Think of **`-ad` as how many "peeks" a point takes into its hemisphere**. Too
  few peeks and it misses a bright region (a sunlit floor patch), so its average
  brightness is wrong — which is exactly what a splotch *is*. The tell is that
  splotches are much larger than a pixel: that's an ambient-calculation problem,
  not a pixel-sampling one.
- Treat **`-av` as a debugging crutch, not a setting** — if the scene needs it
  to look right, `-ab` is too low.

## The `-aa` misconception (important)
`-aa` is **not** a general accuracy dial — it governs *interpolation* error only.
Trying to fix splotches by lowering `-aa` is wrong twice over: it drastically
increases runtime **and** can make the artifacts worse by sharpening the
splotch boundaries. Splotches are an `-ad` problem.

Use `-aa` for its actual symptom: smooth but incorrect gradients, unnaturally
sharp transitions in softly lit areas, and light "bleeding" around corners.
`-aa 0` disables interpolation entirely — prohibitively slow, reserved for
ground-truth reference results.

## The `-ab` / `-lw` / `-lr` interdependency
A high `-ab` alone accomplishes nothing. Rays terminate early when their weight
falls below `-lw`, so setting `-ab 50` while leaving `-lw` at its default means
the deep bounces never happen. To genuinely trace deep interreflection, raise
`-ab`, **lower `-lw`** (e.g. `1e-5`/`1e-6`), and raise `-lr` (total reflection
limit) together. `-ab 0` disables indirect entirely — useful for isolating the
direct component or fast previews.

## Reference Tables

### Ambient parameters
| Flag | Name | Typical | Too low → | Too high → |
|---|---|---|---|---|
| `-ab` | Ambient bounces | 4 std / 7 high | Dark, flat shadowed areas | Runtime grows sharply |
| `-ad` | Ambient divisions | 1024 std / 4096 high | Splotchy artifacts | Slower, smoother |
| `-as` | Ambient super-samples | 512 std / 2048 high | Residual noise from `-ad` | Slower |
| `-ar` | Ambient resolution | 512 std / 1024 high | Error on surfaces closer than `scene_size / ar` | Denser cache, slower |
| `-aa` | Ambient accuracy | 0.2 std / 0.15 high | — (lower = more accurate) | Looser interpolation, faster |
| `-lw` | Limit weight | 0.001–0.0001 | Insignificant rays still traced | Rays culled too early |
| `-av` | Ambient value | `0 0 0` | — | Non-physical fill light |

### Recommended quality presets
| Quality Level | `-ab` | `-ad` | `-as` | `-ar` | `-aa` | Use case |
|---|---|---|---|---|---|---|
| **Draft / Preview** | 2 | 256 | 128 | 128 | 0.25 | View setup, massing. Expect splotches. |
| **Standard / Good** | 4 | 1024 | 512 | 512 | 0.2 | Most reports and illuminance analysis. **Good default.** |
| **High-Fidelity** | 7 | 4096 | 2048 | 1024 | 0.15 | Publication renderings, validation, complex scenes, **glare**. Long runtimes. |
| **Reference (no interpolation)** | 5 | 1024 | 512 | 512 | 0.0 | Ground-truth research data. Extremely slow. |

Rule of thumb: set `-as` to about **half** of `-ad` — which is exactly `rtrace`'s
own default ratio (1024 / 512). `rpict` defaults to a quarter (512 / 128), so
treat this as guidance, not a law.

### Verified program defaults

**The defaults differ per program.** Check with `<prog> -defaults` rather than
assuming; the values below were read from a local Radiance install.

| | `-ab` | `-ad` | `-as` | `-aa` | `-ar` | `-lw` | `-dj` | `-dt` | `-dc` |
|---|---|---|---|---|---|---|---|---|---|
| `rtrace` | 0 | 1024 | 512 | 0.10 | 256 | 1e-4 | 0 | 0.03 | 0.75 |
| `rpict` | 0 | 512 | 128 | 0.20 | 64 | 1e-4 | 0 | 0.05 | 0.50 |
| `rcontrib` | **1** | 350 | 0 | **0** | 256 | 2e-3 | **0.9** | **0** | 0.75 |

Three consequences worth internalising:
- **`-ab` defaults to 0** in `rtrace`/`rpict` — no interreflection at all unless
  you ask for it. A scene that looks flat and dark may simply be at the default.
- **`rcontrib` is the odd one out**: `-dj 0.9` (jittered sun — see Ch 10),
  `-ab 1`, `-aa 0`, and `-dt 0` already set.
- `-aa 0` in `rcontrib` means interpolation is off by default, which is correct
  for coefficient work and wrong for rendering.

### Parallelism (`-n`) and the ambient cache (`-af`)

Neither changes the physics, but on an 8,760-hour matrix run they decide whether
the job takes hours or days — and `-af` can silently serve you stale numbers.

**`-n nproc` — parallel processes** (`rtrace`, `rcontrib`; default **1**):
- *"Execute in parallel on nproc local processes."* There is **no benefit beyond
  the number of cores**, or beyond the `-x` setting, which forces a wait at each
  flush.
- Incompatible with `-P`/`-PP`, and **does not work properly with ray-tree
  output** (any `-o*t*` option).
- `rpict` has no `-n`; parallelise it with `rpiece` or by rendering separate
  timesteps concurrently.

**`-af fname` — the ambient file**:
- Indirect irradiance is normally kept **in memory and lost when the program
  exits**. `-af` persists it so later invocations reuse the work.
- It is also the documented mechanism for sharing between simultaneous
  processes — the same file may be used by several, and the network lock manager
  keeps access consistent. Sharing is supported, not a hack.
- **The real hazard is staleness, not corruption.** *"If any calculation
  parameters are changed or the scene is modified, the old ambient file should
  be removed so that the calculation can start over from scratch."* A stale
  `.amb` silently mixes results computed at different settings — precisely the
  kind of wrong-number-no-error failure this skill catalogues.
- The originating ambient parameters are stored in the file's header: check with
  `getinfo`, and inspect values with `lookamb`.

**Rule**: delete the `.amb` whenever you touch geometry, materials, or any
ambient parameter. Treat it as a cache keyed on inputs you must invalidate by
hand.

### Direct calculation parameters
| Flag | Name | Effect |
|---|---|---|
| `-dj` | Direct jittering | `0` = shadow rays to fixed points (sharp, cheap, unrealistic). Positive (e.g. `0.7`) distributes rays across an area source for true penumbrae. **For `rcontrib` direct sun, use `-dj 0`** so the ray always hits the solar disc centre — consistency matters more than softness there. |
| `-ds` | Direct sampling ratio | Adaptive subdivision of large area sources; avoids artifacts near big sources. |
| `-dt` | Direct thresholding | Halts shadow testing for sources whose contribution falls below a fraction; remainder approximated statistically. |
| `-dc` | Direct certainty | `1` guarantees all shadow lines above `-dt` contrast are computed explicitly. |

**High-accuracy direct sun**: set `-dt 0 -dc 1` to disable statistical
approximation entirely and force full shadow testing for all sources.

The "typical" column above is the **Standard / High-Fidelity** pair from the
preset table below — the two are deliberately the same numbers, so quoting
either is safe.

**`-ar` rule of thumb**: error increases on surfaces spaced closer together than
`scene_size / ar_value`. Size `-ar` against the smallest detail that matters.

### `gensky` inputs
| Variable | Meaning |
|---|---|
| `MONTH DAY TIME` | Date and local standard time (positional) |
| `-a` latitude | Site latitude, degrees **north** (negative for south) |
| `-o` longitude | Site longitude, **degrees WEST** — negative for east |
| `-m` meridian | Standard meridian, **degrees WEST** — negative for east; `15 × UTC offset` |
| `-c` | **Overcast** sky — the standard for daylight factor |
| *(omit `-c`)* | Sunny sky; `gensky` then includes a sun |
| `-b` | Zenith brightness (see calibration above) |

**The `-o`/`-m` sign trap.** The man page is explicit: *"The site longitude is
lon degrees **west**. (Use negative angle for east longitude.)"* — and the same
for `-m`. Everything east of Greenwich therefore takes **negative** values.

| Site | Longitude | Correct flags |
|---|---|---|
| Athens | 23.7° **E**, UTC+2 | `-o -23.7 -m -30` |
| New York | 74.0° **W**, UTC−5 | `-o 74.0 -m 75` |

Measured cost of getting it wrong (Athens, 21 Jun 12:00): the correct form gives
azimuth **−23.2°**, the naive `-o 23.7 -m 30` gives **+21.0°** — the sun moved
**44°**. Shadows, glare and any point-in-time result are wrong, with no error.
`-m` is still `15 × UTC offset` in magnitude; the sign convention is what bites.

### Working-plane grid
| Variable | Meaning |
|---|---|
| `X_POINTS`, `Y_POINTS` | Grid counts in each direction |
| `X_START`, `Y_START` | Coordinates of the first point |
| `X_MAX_ROOM`, `Y_MAX_ROOM` | Used to derive step size across the area |
| `WP_HEIGHT` | Working plane Z height. **Standards fix this** — EN 17037 0.85 m, LM-83 0.8 m, EN 12464-1 the task plane. See `lighting-standards-compliance`. |

## Worked Example — calibrating an overcast sky to 10,000 lux

Target: a standard overcast sky delivering **E_horizontal = 10,000 lux**, so
daylight factors divide by a known number.

```
B_zenith = (9/7) × 10000 / (179 × π)
         = (9/7) × 10000 / 562.36
         ≈ 22.86 W/m²/sr
```

(Source: *3_Radiance Tutorial*, p. 24; *Radiance Cookbook*, p. 39.)

That value goes straight into the sky generation:

```bash
gensky 6 21 12 -c -b 22.86 -a 51.5 -o 0 -m 0 > sky.rad
```

The `-c` makes it overcast (no sun), which is what daylight factor assumes. The
same 10,000 lux then reappears twice downstream — as the DF denominator in
Ch 3 Step 5, and inside the `falsecolor` multiplier in Ch 4 — so it must be
carried consistently or the three disagree.

## Anti-patterns
- **Leaving `gensky` at its default zenith brightness for a DF study** — the
  denominator becomes unknown and results aren't comparable.
- **Using a sunny sky for daylight factor** — DF is defined against an overcast
  sky; omit `-c` only for point-in-time studies.
- **Propping up a dark scene with `-av`** — masks an insufficient `-ab`.
- **Copying one project's ambient parameters into another** — `-ar` in
  particular is scene-size dependent.

## Key Takeaways
1. Fix the exterior illuminance deliberately via `gensky -b`; 10,000 lux → 22.86.
2. `-c` (overcast) is the standard sky for daylight factor.
3. `-ab` cures darkness, `-ad`/`-as` cure noise — diagnose before turning dials.
4. `-ar` is relative to scene size: error rises below `scene_size / ar`.
5. `-av 0 0 0` for physical accuracy; anything else is fill light.
6. Meridian is `15 × UTC offset`, not longitude.

## Connects To
- **Ch 3**: these parameters are consumed by `gensky`, `oconv` and `rtrace`.
- **Ch 4**: the same ambient parameters drive `rpict` image quality.
- **EN 12464-1 / EN 17037**: external standards governing grid spacing and
  working-plane height for compliance work.
