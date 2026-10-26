# Patterns & Techniques

## The Five-Step Daylight Factor Chain
**When to use**: any point-grid DF study.
**How**:
1. `gensky` (with `-c` and calibrated `-b`) → `sky.rad`
2. `oconv materials geometry sky.rad sky_geometry.rad` → `scene.oct`
3. Generate `x y z dx dy dz` records → `calc_points.txt`
4. `rtrace -I -h -oov … | rcalc` → lux
5. `rcalc` divide by exterior illuminance × 100 → DF%

**Trade-offs**: each step is cheap except 4, which carries the whole ambient
cost. Validate steps 1–3 before committing to a long trace.

---

## Zenith Brightness Calibration
**When to use**: whenever DF must be reproducible and comparable.
**How**: `B_zenith = (9/7) × E_horizontal / (179 × π)`; pass as `gensky -b`.
10,000 lux → 22.86 W/m²/sr.
**Trade-offs**: none technically — the alternative (default brightness) leaves
the DF denominator unknown. The same `E` must be reused in step 5 and in the
`falsecolor` multiplier.

---

## Sky as Two Files
**When to use**: every daylight simulation without exception.
**How**: `gensky` produces distribution/materials (`skyfunc`); you additionally
author `sky_geometry.rad` with `glow` + `source` primitives for the sky dome
(`0 0 1 180`) and ground (`0 0 -1 180`).
**Trade-offs**: the extra file is boilerplate, but omitting it produces a
silently unlit scene rather than an error — the single highest-value habit in
the workflow.

---

## Neutral Glow Colour
**When to use**: authoring sky/ground `glow` materials.
**How**: use **`1 1 1`**. `skyfunc` already carries the absolute magnitude from
`gensky -b`/`-B`; the glow RGB is only a multiplier on it.
**Trade-offs**: any value below 1 dims a sky you just calibrated. Measured on a
10,000 lux sky: `1 1 1` → 9982 lux, but `0.9 0.95 1.05` / `1.0 0.9 0.8` → 9416
lux (−5.7%), which biases every DF high because the divisor stays 10,000.
Do **not** apply the `0.265R+0.670G+0.065B = 1` photopic weighting here — that is
the lux conversion, and using it as a "normalisation" is what causes the dimming.

---

## Photometric Conversion (RGB → lux)
**When to use**: converting any `rtrace` radiometric output to illuminance.
**How**: `E = 179 × (0.265R + 0.670G + 0.065B)`. With `-oov`, RGB are fields **4,5,6**.
**Trade-offs**: field positions shift with the `-o` spec — each of `o`/`d`/`v`
adds 3 fields. Re-check whenever you edit either side.

---

## Ambient Parameter Tuning
**When to use**: before trusting any interreflected result.
**How**: raise `-ab` until results stop changing (convergence, not taste); raise
`-ad`/`-as` against splotchiness; size `-ar` so `scene_size / ar` is smaller
than the finest detail; lower `-aa` last, it is the most expensive.
**Trade-offs**: runtime grows steeply. Diagnose the symptom first — darkness is
`-ab`, noise is `-ad`. Never substitute `-av`.

**How to actually check convergence** (the step "raise until it stops changing"
leaves implicit):
1. Pick a **fixed subset** of sensor points — a dozen spanning bright to dark.
   The darkest, deepest points converge last, so they set the verdict.
2. Run at setting A, then at the next tier up (B). Save both result files.
3. Compare **per point**, not on the mean: `rlam a.txt b.txt | rcalc -e
   '$1=100*($2-$1)/$1'` gives per-point % change. Converged means the **largest**
   change is inside your tolerance (2–5% is typical for design work), not that
   the average looks stable — an average hides one point moving 40%.
4. **Radiance is stochastic**, so two runs at *identical* settings already differ.
   Establish that noise floor first: run A twice with different `-ad` seeds or
   simply two invocations, and measure the spread. A difference between A and B
   that is inside A's own run-to-run spread is noise, not a trend — raising the
   parameter further buys nothing.
5. Delete the `.amb` between runs (Ch 2), or you are comparing partly cached
   results from the *old* settings.

Never compare across sampling regimes without this: each setting has its own
converged mean, and a tolerance narrower than the run-to-run spread will chase
noise forever.

---

## Preview Before Octree
**When to use**: right after authoring geometry/materials.
**How**: `objline geom.rad | x11meta` for wireframe; `objview mats.mat geom.rad`
for interactive shaded check.
**Trade-offs**: `objview` adds its own default lighting, so it validates
*geometry and material assignment*, never brightness.

---

## rvu → rpict View Handoff
**When to use**: producing any final image.
**How**: compose in `rvu`, save with `view my_view.vf`, then
`rpict -vf my_view.vf -x W -y H <ambient params> scene.oct > out.hdr`.
**Trade-offs**: `rvu` at low ambient settings is fast but not representative;
re-check quality settings for the batch render.

---

## Two-Image Falsecolor Mapping
**When to use**: any quantitative illuminance or DF map.
**How**: render twice from the **same** `.vf` — once with `-i` (data), once
without (background) — then:
`falsecolor -i data.hdr -p background.hdr -l "Daylight Factor (%)" -m 1.79 -s 10 -n 10 -cl`
**Trade-offs**: `-s` must be chosen against the actual range or the map
saturates; `-cl` (contours) reads better in print than solid fill.

---

## Falsecolor Multiplier Derivation
**When to use**: labelling a map in DF% rather than lux.
**How**: `M = 179 × 100 / E_external` → 1.79 for 10,000 lux.
**Why it works**: `falsecolor`'s **default multiplier is already 179** (the man
page: *"converts from radiance or irradiance to luminance or illuminance"*), and
`-m` **replaces** it rather than stacking on top. So supplying `179×100/E` maps
irradiance straight to DF%: a pixel at 0.6 W/m² → 107.4 lux → **1.074%**, which
is what `0.6 × 1.79` gives.
**Trade-offs**: must match the exterior illuminance used in the DF calculation,
or image and numbers disagree. Set `-l` accordingly — the default label is
`cd/m2`, which is wrong for any `-i`-derived map.

---

## Ray Probing for Debugging
**When to use**: a surface renders black, too bright, or the wrong colour.
**How**: in `rvu`, press `t` and click the pixel — prints intersection point,
material and radiance to the console (the command is `trace`; `t` abbreviates
it). In `ximage`, lowercase **`l`** over an area reports its
photometrically-weighted luminance.
**Trade-offs**: point evidence only; confirms *which* material is at fault, not
why the whole scene is off.

---

## Exit-Status Guarding
**When to use**: any scripted chain.
**How**: `if [ $? -ne 0 ]; then echo "Error …"; exit 1; fi` after each stage.
**Trade-offs**: verbose, but without it a failed `oconv` leaves a stale octree
in place and the run continues against yesterday's scene.

---

## Stage-Mirrored Project Layout
**When to use**: any project past a single test.
**How**: `materials/ geometry/ skies/ views/ octrees/ images/{raw_hdr,final_png}/ results/ scripts/`
plus a README recording ambient parameters, sky date/time and exterior illuminance.
**Trade-offs**: slightly longer paths in scripts; in exchange, derived artifacts
are obviously regenerable and scenarios stay separable.

---

## Annual Matrix Simulation (Daylight Coefficients)
**When to use**: any study spanning many timesteps; 8,760 individual runs are infeasible.
**How**: `E = DC · S`. Build the sky matrix once (`epw2wea in.epw out.wea`
then `gendaymtx -m 1 out.wea`),
build the coefficient matrix once (`rcontrib -I+ -f <cal> -b <var> -bn <N> -m <mod>`),
then evaluate any timestep as a matrix multiply.
**Trade-offs**: expensive up front, nearly free per timestep. Two-phase DC bakes
the glazing into the matrix, so comparing fenestration means recomputing — use 3PM instead.

---

## Three-Phase Modularity
**When to use**: comparing fenestration options, or modelling complex fenestration (blinds, frit, film).
**How**: `I = V·T·D·S`. Compute `V` (interior→window) and `D` (window→sky) once
with `rfluxmtx`; swap `T` (the BSDF XML) per option; combine with `dctimestep`
and convert with `rmtxop -fa -t -c 47.4 119.9 11.6`.
**Trade-offs**: cannot resolve direct sun accurately — the solar disc is smeared
across a sky patch. Fine for sDA/UDI, invalid for ASE.

---

## Window-as-Glow-Source (View Matrix)
**When to use**: generating any V matrix.
**How**: redefine the window with a `glow` material (4th arg `0` — coefficients
carry magnitude) whose normal faces **into** the room; build a dedicated
`model_vmx.oct`; verify with `rvu -ab 2 -vf views/back.vf model_vmx.oct`.
**Trade-offs**: an extra octree and a normal-direction check. Skipping the check
is how you get a silently near-zero matrix.

---

## Secondary Reference Geometry (Stencil)
**When to use**: you want an illuminance map of a specific surface (a virtual
work-plane, a band of a facade) rather than a whole view.
**How**: build a stencil octree from a `glow` plane; trace view rays through it
with `rtrace -opv`; filter with
`rcalc -e '$1=$1;$2=$2;$3=$3;$4=0;$5=0;$6=if($6-0.5,1,0)'`; feed to
`rcontrib -I`, which skips rays whose last component is 0.
**Trade-offs**: an extra octree and a pipeline stage; in exchange the V matrix
is confined to exactly the area of interest, and the output HDRs are illuminance
maps usable directly in `dctimestep`.

---

## Two-BSDF Discipline (5PM)
**When to use**: any five-phase run.
**How**: `genBSDF` twice from the same prepared geometry — a tensor-tree XML for
`Cds`, a Klems-basis XML for the 3PM terms. Prepare geometry with `xform` so it
lies parallel to XY in the **negative Z half-space** first.
**Trade-offs**: two generation runs. Deriving the Klems version with
`bsdf2klems` is faster but handles specular components poorly.

---

## Five-Phase Direct Sun Correction
**When to use**: ASE, light-redirecting facades, any direct-sun-critical annual study.
**How**: `I_5ph = V·T·D·S − Vd·T·Dd·Sds + Cds·Ssun` — total, minus the 3PM's
wrong direct term, plus an accurate one from thousands of discrete suns
(MF:6 → 5,185 at 0.533°). Isolate direct paths with all-black models.
**Trade-offs**: substantially more complex and slower. The accurate route to ASE
**with complex fenestration**; simple glazing needs only a direct-only run.
Requires `-dj 0 -dc 1` (rcontrib's `-dt` is already 0) and a tensor-tree BSDF
(`genBSDF -t4 N`).

---

## All-Black Model Isolation
**When to use**: separating a direct-only component from total transport.
**How**: rebuild the scene with non-fenestration materials black, then per term:
**`-ab 1` for Vd** (one bounce; the black model, not `-ab 0`, is what suppresses
interreflection), **`-ab 0` for Dd** (direct sky component only), and
**`-ab 1` for Cds** (BSDF sampling needs one bounce).
**Trade-offs**: an extra octree per isolated term; without it the subtraction in
5PM removes the wrong quantity.

---

## Two-Step Annual Metric Reduction
**When to use**: computing sDA, ASE, UDI or DA from an `.ill` file.
**How**: mask to occupied hours first; then (1) per point count hours meeting an
illuminance criterion, (2) count points meeting a temporal criterion.
**Trade-offs**: none — but averaging illuminance instead of counting hours is a
different and wrong metric.

---

## Fisheye Glare Evaluation
**When to use**: any DGP assessment.
**How**: `rpict -vth -vh 180 -vv 180` at ~1000×1000 with High-Fidelity ambient
settings, then `evalglare -d -c check.hdr view.hdr`.
**Trade-offs**: high render cost, but DGP depends on background luminance so it
cannot be economised. Always inspect the `-c` check image.

---

## HDR Export Pipeline
**When to use**: putting a render into a report.
**How**: `pfilt -x /2 -y /2` to filter/resize (**not** `-e 1` — that is a ×1
multiplier, not auto-exposure), then `normtiff` (gamma 2.2)
or `convert input.hdr -gamma 2.2 output.png`.
**Trade-offs**: exports are lossy and tone-mapped — always keep the linear
`.hdr` as the archival copy.
