# Cheatsheet — decisions, thresholds, tells

## Pick the method first
| Study | Method | Why |
|---|---|---|
| Daylight factor | `gensky -c` + `rtrace` (Ch 6) | Single overcast sky |
| Point-in-time illuminance | `rtrace` (Ch 3) | One sky condition |
| Glare / DGP | `rpict` fisheye + `evalglare` (Ch 7) | Needs 180° hemisphere |
| Annual sDA / UDI / DA | **Three-Phase** (Ch 9) | Diffuse-dominated; modular glazing |
| Annual **ASE** | **Direct-only** (`-ab 0` + sun sky); 5PM for CFS | LM-83: direct sun only |
| Comparing glazing options | **Three-Phase** (Ch 9) | Swap only `T` |
| Single fixed glazing, annual | Two-phase DC (Ch 8) | Simplest matrix route |

## Numbers to know
| Quantity | Value | Where it comes from |
|---|---|---|
| Luminous efficacy constant | **179** lm/W | lux = 179 × (0.265R+0.670G+0.065B) |
| Photometric weights | 0.265 / 0.670 / 0.065 | Sum to 1.0 |
| `rmtxop` photometric coefficients | **47.4 119.9 11.6** | = 179 × the weights |
| Sky @ 10,000 lux via `-B` | **55.866** W/m² | E/179 (horizontal irradiance) |
| Sky @ 10,000 lux via `-b` | **22.863** W/m²/sr | (9/7)·E/(179·π) (zenith radiance) |
| `-B` ÷ `-b` | **2.4435** | = (7/9)·π — same sky, different flag |
| Falsecolor multiplier for DF% @ 10,000 lux | **1.79** | 179×100/E |
| Reinhart patches, MF:1/2/4/6 | **145** / **577** / **2,305** / **5,185** | `1 + 144·MF²` (+1 ground patch) |
| Klems basis directions | **145** | `Nkbins` in `klems_int.cal` |
| Solar disc angular diameter | **0.533°** | Suns file |
| DGP thresholds | **0.35 / 0.40 / 0.45** | Imperceptible→intolerable |
| sDA criteria | **300 lux / 50%** | Of occupied hours |
| ASE criteria | **1000 lux / 250 h** | **Direct sun only** (LM-83: no sky, no interreflection) |
| UDI bins | **100 / 500 / 2000** lux | Fell short→exceeded |
| `evalglare` image size | **1000×1000** | Min 800, max 1500 |
| Transmissivity for Tn=0.88 | **≈0.96** | Note: tn > Tn |
| Glass default refractive index | 1.52 | Optional 4th arg |
| Working plane height | **standards fix this** | EN 17037 0.85 m · LM-83 0.8 m (30 in) · EN 12464-1 = the task plane. Don't pick 0.75 by habit — see lighting-standards-compliance |
| RGB fields in `rtrace -oov` output | **4, 5, 6** | `o`=3 fields, then `v`=3 |

## Diagnose by symptom
| Symptom | Likely cause | Fix |
|---|---|---|
| **All results ~0 lux** | `gensky` output used without sky geometry | Author `sky_geometry.rad` with `glow`+`source` |
| **All results ~0 lux** | Date passed non-positionally to `gensky` | `gensky MONTH DAY TIME` is positional |
| Shadowed areas dark and flat | `-ab` too low | Raise until results converge |
| Splotchy / blotchy patches | `-ad` too low | **Double `-ad`.** Never lower `-aa` — worsens it |
| **DF off by ~2.44×** | `-B`/`-b` values swapped | 55.866 is `-B`, 22.863 is `-b` |
| **Illuminance zero or wrong** | `-o` field offset | RGB = `$1$2$3` bare, **`$4$5$6`** with `-oov` |
| **Dark deep in the space** | `-ab` low **or** `-lw` high | Raise `-ab` ≥5 **and** lower `-lw` to 1e-5/1e-6 |
| **Annual run missing/inconsistent direct sun** | `rcontrib` defaults `-dj 0.9` | `-dj 0 -dc 1` (its `-dt` is already 0) |
| **DGP wrong** | Not a 180° fisheye, or low quality | `-vth -vh 180 -vv 180`; High-Fidelity settings |
| **No glare sources on a bright window** | Threshold too high | `-t` task area; inspect `-c` check image |
| **ASE implausible** | Computed from a total-illuminance result | ASE is direct-sun-only: `-ab 0` + sun-only sky (5PM for CFS) |
| **Glazing swap is slow** | Two-phase DC bakes in glazing | Move to 3PM; swap only `T` |
| Residual noise after raising `-ad` | Variance not super-sampled | Raise `-as` |
| Error on closely-spaced surfaces | `scene_size / -ar` too coarse | Raise `-ar` |
| Surface renders black | Modifier undefined or misspelled | Check name; check `oconv` order |
| Light leaks from a room | Outward normals | `genbox -i`; CCW from front |
| Interior too dark, glazing suspect | Entered Tn instead of tn | Convert: Tn=0.88 → tn≈0.96 |
| Window over-reflecting | `glass` modelled as two surfaces | Use one surface per pane |
| Render inexplicably slow | `mirror` material | Virtual light sources; avoid if possible |
| Image looks washed out / wrong in report | `.hdr` is linear, not gamma-corrected | `-gamma 2.2` or `normtiff` |
| Falsecolor map uniformly maxed | `-s` below actual range | `-s a` to auto-scale and learn the range, then pin a round value |
| Falsecolor misaligned with background | Two renders used different views | Same `.vf` for both |

## Decision rules
- **If the study is a daylight factor** → overcast sky (`gensky -c`), calibrated
  `-b`, and a fixed exterior illuminance reused in *all three* places (sky, DF
  divisor, falsecolor multiplier).
- **If you need a point-in-time result** → omit `-c` so `gensky` includes a sun.
- **If the scene looks too dark** → raise `-ab`; do **not** reach for `-av`.
  `-av` non-zero means you have stopped simulating interreflection.
- **If you want physical accuracy** → `-av 0 0 0`, always.
- **If you're about to run a long trace** → `objview` first. Thirty seconds of
  preview beats a three-hour trace of a scene with flipped normals.
- **If you need an image at final quality** → compose in `rvu`, save the `.vf`,
  render with `rpict`. Never re-aim the camera by hand twice.
- **If you're making a quantitative map** → two renders, same `.vf`, one with
  `-i`, one without.
- **If a value looks wrong at one location** → `rvu` `t`+click, or `ximage`
  lowercase `l` over an area, before changing any parameter.
- **If comparing scenarios** → name the sky and octree for the condition;
  never reuse a generic `scene.oct`.

## `oconv` ordering — non-negotiable
```
oconv materials.mat  geometry.rad  sky.rad(gensky)  sky_geometry.rad  > scene.oct
        1                2               3                 4
```
A modifier must be defined before it is used. `sky_geometry.rad` references
`skyfunc`, which `sky.rad` defines — so 3 precedes 4, always.

## Ambient parameter presets
| Quality | `-ab` | `-ad` | `-as` | `-ar` | `-aa` | Use |
|---|---|---|---|---|---|---|
| Draft | 2 | 256 | 128 | 128 | 0.25 | Previews; expect splotches |
| **Standard** | **4** | **1024** | **512** | **512** | **0.2** | **Default for reports** |
| High-Fidelity | 7 | 4096 | 2048 | 1024 | 0.15 | Publication, validation, **glare** |
| Reference | 5 | 1024 | 512 | 512 | 0.0 | Ground truth; extremely slow |

Set `-as` ≈ half of `-ad`. Tune `-ab` → `-ad` → `-as` → `-ar` → `-aa`: cheapest
diagnostic first, most expensive last. `-av` stays `0 0 0`.

**Deep-space rule**: raising `-ab` alone does nothing — rays die at `-lw` first.
Raise `-ab`, lower `-lw` (1e-5/1e-6), raise `-lr`, together.

## Matrix methods at a glance
| | 3PM term | 5PM adds |
|---|---|---|
| Equation | `i = V·T·D·s` | `− Vd·T·Dd·Sds + Cds·Ssun` |
| BSDF | Klems (`genBSDF`) | **Tensor tree** for `Cds` |
| Sky | `gendaymtx -of` | `-of -d` (Sds), `-5 -d -m 6 -of` (Ssun) |
| Octree | `model_3ph.oct` | `model_allblack.oct`, `model_suns.oct` |
| `-ab` | V 10–12, D 2 | Vd **1**, Dd **0**, Cds **1** |

**`dctimestep` order is always `V T D S`.** A `gendaymtx` `.smx` has a header
with its own timestep count; `-n` is only needed for **headerless** sky input.

**Matrix-method tells:**
- `rcontrib -c` left at its default 1 while `genklemsamp -c` is 1000 → record
  boundaries misalign, D matrix silently wrong.
- Glow window normal facing outward → near-zero V matrix. Check with
  `rvu -ab 2 -vf views/back.vf model_vmx.oct`.
- `kbinS` on a non-south window → every direction misbinned.
- One BSDF used for all three 5PM terms → term 3 is not actually accurate.
- `bsdf2klems` used instead of a second `genBSDF` run → specular handled poorly.

## Direct sun parameters (`rcontrib`, 5PM)
`-dj 0 -dt 0 -dc 1 -st 1 -ss 0` — `-dj 0` aims at the solar disc centre for
consistency; `-dt 0 -dc 1` disable statistical shortcuts. Default `-dj 0.9` is the
classic cause of "annual simulation missing direct sun".

## Sky type by study
| Study | Sky | Flag |
|---|---|---|
| Daylight factor | CIE overcast | `-c` + calibrated `-b` |
| Point-in-time / sun studies | Sunny with sun | omit `-c` |

## Tells & smells
- A `.rad` file with no matching `.mat` in the `oconv` line → black surfaces coming.
- A `scene.oct` newer than its inputs but a script that didn't check `$?` → possibly stale.
- Results that changed after "only" a sky edit, with the DF divisor untouched → the ratio is now wrong.
- An exterior illuminance that appears as three different literals in one script → one of them is stale.
- A falsecolor legend in lux labelled "%" → the multiplier wasn't applied.
