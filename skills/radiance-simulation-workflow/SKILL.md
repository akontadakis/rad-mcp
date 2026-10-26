---
name: radiance-simulation-workflow
description: "Raw Radiance CLI reference, merged from four workflow guides including the LBNL three-phase and five-phase tutorials. Use when writing or debugging Radiance commands and scene files directly — gensky, genskyvec, oconv, rtrace, rpict, rvu, falsecolor, evalglare, rcontrib, rfluxmtx, genklemsamp, genBSDF, gendaymtx, dctimestep, rmtxop, rcollate; .rad/.mat primitive syntax; ambient and direct parameters (-ab/-ad/-as/-ar/-aa/-dj/-dt/-dc); three-phase and five-phase matrix methods; Klems and tensor-tree BSDFs; daylight factor, DGP glare, and annual sDA/UDI/ASE computation. For running studies through the rad-mcp server's tools, call those tools directly; this skill covers the Radiance CLI substrate underneath."
---

<!-- argument-hint: [command name, parameter, method, or chapter number] -->

# Radiance Simulation Workflows
**Scope**: raw Radiance CLI, point-in-time through annual matrix methods
**Chapters**: 11 | **Sources**: 4 guides | **Updated**: 2026-08-07

## How to Use This Skill

- **Without arguments** — load the core workflow and conventions below
- **With a topic** — ask about `three-phase`, `DGP`, `ambient parameters`, `ASE`
- **With a chapter** — ask for `ch09` to load the Three-Phase Method
- **Debugging** — [troubleshooting.md](troubleshooting.md) is symptom-first
- **Quick lookup** — [cheatsheet.md](cheatsheet.md) has the numbers and decision rules

---

## Core Frameworks & Mental Models

### Pick the method before anything else
| Study | Method |
|---|---|
| Daylight factor | `gensky -c` + `rtrace` (ch06) |
| Point-in-time illuminance | `rtrace` (ch03) |
| Glare / DGP | `rpict` fisheye + `evalglare` (ch07) |
| Annual sDA / UDI | Three-Phase (ch09) |
| Annual **ASE** | **Direct-sun-only** run (`-ab 0` + sun-only sky); Five-Phase (ch10) for CFS |
| Comparing glazing | Three-Phase — swap only `T` (ch09) |

### The pipeline
```
.rad/.mat → oconv → .oct → rtrace (numbers) → rcalc → lux / DF%
                         → rpict  (images)  → falsecolor / evalglare
Annual:  epw2wea → gendaymtx → .smx ─┐
         rfluxmtx → V, D  +  BSDF T ─┴→ dctimestep → rmtxop → .ill
```

### Silent failure modes — wrong numbers, not errors
1. **`gensky` output alone is a modifier, not a light source.** You must author
   `sky_geometry.rad` with `glow` + `source` primitives (sky `0 0 1 180`,
   ground `0 0 -1 180`) or the scene is unlit.
2. **`gensky`'s date is positional** — `gensky MONTH DAY TIME …`.
3. **The `-o` spec shifts the RGB fields.** Each letter contributes 3 fields:
   `o`=origin, `d`=direction, `v`=value. So bare `rtrace -I` → RGB in
   `$1 $2 $3`; **`-oov` → 6 fields, RGB in `$4 $5 $6`**; `-oodv` → `$7 $8 $9`.
   Verified: `$4 $5 $6` returns 9985 lux on a 10,000 lux sky; `$7 $8 $9` on
   `-oov` reads past the record and returns **0**, silently.
4. **`-B` and `-b` are different quantities.** Both can express a 10,000 lux
   sky: `-B 55.866` (horizontal **diffuse** irradiance, `E/179`) or `-b 22.863` (zenith
   radiance, `(9/7)E/(179π)`). Ratio `2.4435 = (7/9)·π`. Swapping them is a
   2.44× error.
5. **`rcontrib` defaults `-dj 0.9`** — unlike `rtrace`/`rpict`, which default to
   0 — so shadow rays jitter across the solar disc and the direct term varies
   between runs. Use `-dj 0 -dc 1` (`rcontrib`'s `-dt` is already 0).

### `oconv` order is semantic
```
oconv materials.mat  geometry.rad  sky.rad  sky_geometry.rad  > scene.oct
```
A modifier must be defined before it is referenced.

### The primitive grammar (geometry and materials alike)
```
modifier type identifier
N  S1 ... SN     # string argument count  (0 for most geometry, but e.g. 2 for gensky's brightfunc)
I  I1 ... II     # integer argument count (0 for geometry/materials; non-zero for *data types)
M  R1 ... RM     # real argument count
```
`void` is the modifier for a base material. Surfaces bind to materials by name.

### Photometry
- **lux** = `179 × (0.265R + 0.670G + 0.065B)` — weights sum to 1.0.
- **`rmtxop -c 47.4 119.9 11.6`** is the same weighting pre-multiplied by 179.
- **DF%** = interior lux ÷ exterior lux × 100. Keep the reference illuminance
  consistent across sky calibration, DF divisor and falsecolor multiplier.

### Parameter discipline
Match the parameter to the symptom — this is where most simulation time is wasted.
- `-ab` cures **darkness** — but only if `-lw` is lowered with it, or rays die first.
- `-ad` cures **splotches**. Double it. **Never lower `-aa` to fix splotches** —
  it costs hours and sharpens the splotch edges.
- `-aa` governs **interpolation** error only: smooth-but-wrong gradients, light
  bleeding around corners.
- `-ar` is relative to scene size; error rises below `scene_size / ar`.
- `-av 0 0 0` always — non-zero is non-physical fill light.

**Presets**: Draft `2/256/128/128/0.25` · **Standard `4/1024/512/512/0.2`** ·
High-Fidelity `7/4096/2048/1024/0.15`. Set `-as` ≈ half `-ad`.

### Annual matrix methods
- **DC (two-phase)**: `E = DC · S`. Simple, but bakes glazing into the matrix.
- **Three-Phase**: `i = V·T·D·s`. `V` and `D` depend on geometry and are computed
  once; `T` is the BSDF and is swappable — the whole point of the method.
  The window becomes an inward-facing **`glow` source** for V (verify with
  `rvu`); `genklemsamp -c N` (rays per Klems direction) and `rcontrib -c N`
  (rays averaged per record, default **1**) must agree; `-b kbinS/N/E/W/D` must
  match the window orientation.
- **Five-Phase**: `I = V·T·D·S − Vd·T·Dd·Sds + Cds·Ssun`, read as
  **total − wrong_direct + right_direct**. The accurate route to ASE with
  complex fenestration. Needs **two BSDFs** — Klems for terms 1–2, **tensor
  tree (`genBSDF -t4 N`)** for `Cds` — and all-black models (`-ab 1` Vd,
  `-ab 0` Dd, `-ab 1` Cds). Note `gendaymtx -5` **requires** a disk-size
  argument: `-5 0.533`.

`dctimestep` order is always **V T D S**. A `gendaymtx` `.smx` carries its own
header declaring the timestep count — `-n` is only for **headerless** sky input
(and `-n` must then match the actual column count, not a reflexive 8760).

### Annual metrics
Mask to occupied hours first, then apply two thresholds: one per hour, one per point.
- **sDA 300/50%** — ≥300 lux for ≥50% of occupied hours (% floor area)
- **ASE 1000,250** — ≥1000 lux **direct sun only** for >250 hours. LM-83
  excludes sky and interreflections, so this is *not* read off a total-illuminance
  annual result.
- **UDI** — bins at 100 / 500 / 2000 lux

### Glare
`rpict -vth -vh 180 -vv 180` at ~1000×1000, High-Fidelity settings, then
`evalglare -d -c check.hdr`. DGP: <0.35 imperceptible, 0.40 perceptible,
0.45 disturbing, ≥0.45 intolerable. Always inspect the check image.

### Geometry conventions
X→East, Y→North, Z→Zenith; consistent units. Polygon vertices counter-clockwise
from the front; room enclosures need inward normals (`genbox -i`).
`glass` takes **transmissivity**, not transmittance (Tn 0.88 → tn ≈ 0.9584), one
surface per pane.

---

## Chapter Index

| # | Title | Key Content |
|---|-------|-------------|
| [ch01](chapters/ch01-scene-input-files.md) | Scene Input Files | Primitive grammar, material types, transmissivity, normals, generators |
| [ch02](chapters/ch02-simulation-parameters.md) | Calculation Parameters | Ambient + direct parameters, quality presets, the `-aa` misconception, `-ab`/`-lw` interdependency |
| [ch03](chapters/ch03-core-simulation-chain.md) | Core Simulation Chain | Five-step chain, sky geometry, `oconv` order, `-oov` field rule, RGB→lux |
| [ch04](chapters/ch04-visualization-analysis.md) | Visualization & Analysis | `objview`, `rvu`, `rpict`, `ximage`, `falsecolor`, HDR export |
| [ch05](chapters/ch05-file-organization.md) | Project Organization | Numbered `01_`–`11_` layout, relative paths, authored vs derived |
| [ch06](chapters/ch06-daylight-factor-workflow.md) | Daylight Factor Workflow | `-B` vs `-b` calibration, the DF pipeline, folded `rcalc` |
| [ch07](chapters/ch07-glare-dgp.md) | Glare / DGP | Fisheye requirement, `evalglare`, thresholds, check images |
| [ch08](chapters/ch08-daylight-coefficients.md) | Daylight Coefficients | `E = DC·S`, sky discretization, `rcontrib` binning |
| [ch09](chapters/ch09-three-phase-method.md) | Three-Phase Method | `V·T·D·s`, glow window, `genklemsamp`, Klems binning, stencil geometry, `dctimestep` |
| [ch10](chapters/ch10-five-phase-method.md) | Five-Phase Method | Direct sun correction, two BSDFs, suns file, all-black models, `-dj 0` |
| [ch11](chapters/ch11-annual-metrics-postprocessing.md) | Annual Metrics | sDA, ASE, UDI, DA; occupied-hours masking |

## Topic Index

- **Ambient parameters** → ch02, cheatsheet
- **Annual simulation** → ch08, ch09, ch10, ch11
- **ASE** → ch11, ch10 (method requirement)
- **BSDF / complex fenestration** → ch09, ch10
- **Daylight coefficients** → ch08
- **Daylight factor** → ch06, ch02, ch04 (mapping)
- **Direct calculation (`-dj`/`-dt`/`-dc`)** → ch02, ch10
- **DGP / glare** → ch07
- **`dctimestep`** → ch09, ch10
- **`evalglare`** → ch07
- **`falsecolor`** → ch04
- **File layout** → ch05
- **Fisheye views** → ch07, ch04
- **Five-phase method** → ch10
- **`genBSDF`** → ch10, ch09
- **`gendaymtx` / `epw2wea`** → ch08, ch10
- **`genklemsamp`** → ch09, ch10
- **`gensky` / sky setup** → ch02, ch03, ch06
- **`genskyvec` / sky vectors** → ch09
- **Generators (`genbox`, …)** → ch01
- **Glass / transmissivity** → ch01
- **Glow window (View Matrix)** → ch09
- **Klems basis / `kbin`** → ch09, ch10
- **HDR export / gamma** → ch04
- **Illuminance calculation** → ch03, ch06
- **Materials** → ch01
- **`oconv` / octree** → ch03
- **`-oov` field offset** → ch03, troubleshooting
- **Photometric conversion (179)** → ch03, ch09
- **Primitive syntax** → ch01
- **Quality presets** → ch02, cheatsheet
- **`rcontrib` / `rfluxmtx`** → ch08, ch09, ch10
- **Reinhart / sky discretization** → ch08, ch10
- **`rmtxop`** → ch09
- **`rpict`** → ch04, ch07
- **`rtrace`** → ch03, ch06
- **`rvu`** → ch04
- **sDA / UDI / DA** → ch11
- **Sensor grid** → ch02, ch03
- **Sky geometry (`glow`+`source`)** → ch03
- **Stencil / secondary reference geometry** → ch09
- **Surface normals** → ch01
- **Three-phase method** → ch09
- **`vwrays`** → ch09
- **Troubleshooting** → troubleshooting.md
- **View files (`.vf`)** → ch04
- **`ximage`** → ch04
- **Zenith brightness / `-B` vs `-b`** → ch06, ch02

## Supporting Files

- [troubleshooting.md](troubleshooting.md) — symptom → cause → fix
- [cheatsheet.md](cheatsheet.md) — numbers, method selection, decision rules
- [glossary.md](glossary.md) — every flag, tool and term
- [patterns.md](patterns.md) — reusable techniques with trade-offs

---

## Scope & Limits

Covers the Radiance CLI from scene authoring through annual matrix simulation:
point-in-time illuminance, daylight factor, DGP glare, daylight coefficients,
three-phase and five-phase methods, and annual metric computation.

**Not covered**: electric lighting design and IES luminaire workflows, spectral
/ melanopic (circadian) calculations, and the rad-mcp tool surface itself.

**Sibling skills — know which one you want:**
- **`lighting-standards-compliance`** owns the *obligation*: which metric and
  threshold a study is judged against, grid spacing, analysis periods, blind
  operation, dirt depreciation, pass/fail bands (EN 12464-1/-2, EN 17037,
  LM-83, EN 13363, EN 15193). **This** skill owns the *computation* — the
  commands that produce the number. Reporting a compliance result needs both.
- **The rad-mcp server's tools** drive these computations for you; call them
  directly. This skill is the CLI substrate those tools wrap; reach for it when
  debugging what a tool emitted, or when working outside the tool surface.

**Sources** — four guides merged. Chapters 9–10 follow the LBNL treatment
(A. McNeil, "The Three-Phase Method for Simulating Complex Fenestration with
Radiance"; McNeil & Lee 2013 for the five-phase parameters), which is the most
authoritative and command-level of the four.

## Verification status (2026-08-07, three review passes)

**Pass 3 — reviewed against the sibling `lighting-standards-compliance` skill.**
No contradictions found between the two on shared numbers (sDA 300/50, ASE
1000/250, DGP 0.35/0.40/0.45, Reinhart 2,305 = MF:4 — all agree). Four gaps
closed where this skill computed a number without saying what the standard then
requires of it:

- **ch08** now records LM-83's **MF:4 (2,305 patch) minimum** for sDA/ASE work.
  Radiance defaults to `-m 1`; using it for an LM-83 study is a compliance
  failure no error will flag.
- **ch07** now separates the Wienold **single-moment** perception bands from
  EN 17037's `DGP_e<5%` **exceedance-over-usage-time** criterion. Same numbers,
  different questions — one render cannot answer the second.
- **ch06** now flags that EN 17037 Method 1 prescribes the **ISO 15469 overcast
  sky** and that `D_T` targets are **city-specific** (Athens 1.5%, Reykjavik
  2.6%). The 10,000 lux convention here is for comparability, not a pass mark.
- `-B` is horizontal **diffuse** irradiance (the man page's wording), now
  consistent across all four places it appears.
- **ch08** documents the **half-hour `.wea` timestamp** convention LM-83 §2.2.1
  requires. Good news, verified against a real fixture: `epw2wea` already writes
  `0.500`/`1.500`, so the standard chain satisfies it — the risk is hand-built
  `.wea` files on whole hours, which shift every solar position by 30 min.
- **ch07** drops its "Verdict" column (perception labels were reading as
  pass/fail) and gains `evalglare`'s **380-lux underestimate notice**.
- **ch03** notes grid points must sit at **cell centres** with a perimeter band
  excluded for EN 12464/17037 — the generator shown places them at origins.
- **Working plane** is no longer described as a "convention": the standards fix
  it (EN 17037 0.85 m, LM-83 0.8 m, EN 12464-1 the task plane).
- **troubleshooting.md** gains an **octree / file-resolution** section (non-frozen
  octrees and relative paths, `RAYPATH` missing `.` — where `oconv` warns but
  **exits 0**, so a scripted `$?` check passes against an empty scene).

Two fixes landed in the sibling skill: its cheatsheet had softened LM-83 §2.3.3
into engineering advice ("a floor, not a ceiling") where the standard only
documents what its research used, and its chapters now carry outbound pointers
here rather than routing only from its front page.

## Pass 2 (2026-08-07)

**Pass 2 — run against a local Radiance 6.1a install.** Ten further defects
found and fixed, including three that broke a command or produced a wrong number:

- **`ch09`'s glow primitive was `4 1 1 0`** — four declared reals, three
  supplied. `oconv: fatal - bad arguments for glow`. The V-matrix chapter could
  not be executed as written. Now `4 1 1 1 0`.
- **`ch06`'s canonical DF example omitted `sky_geometry.rad`** from `oconv` —
  the exact trap this skill warns about three times. It returned **0 lux**. Now
  fixed and verified end to end: an unobstructed exterior point returns
  **99.84% DF**, which is now included as a self-test.
- **The sky/ground `glow` colours were dimming the calibrated sky.** The
  "normalise so `0.265R+0.670G+0.065B = 1`" rule was the lux conversion
  misapplied. Measured on a 10,000 lux sky: the old values → **9416 lux**,
  neutral `1 1 1` → **9982**. Every DF built on it was ~6% biased.
- **`gensky -o`/`-m` are degrees WEST** (negative for east). The naive reading
  moves the sun **44° in azimuth** for a European site. Now documented with a
  worked Athens/New York table.
- **`rcontrib` defaults `-dj 0.9`**, not 1 — and defaults differ per program
  (`rtrace`/`rpict` use 0). A verified defaults table is now in ch02.
- **`glow` maxrad `0` does not mean "no light"** — the surface still contributes
  through the ambient calculation (measured 0.759 W/m² at `-ab 1`); only a
  *negative* radius silences it.
- **The primitive grammar's middle `0` is not always 0** — `gensky`'s own
  `brightfunc` has a string count of 2. All three counts vary by type.
- **`ximage`'s value probe is lowercase `l`**, not `L`; `=` (not `e`) sets
  exposure to the picked area; `rvu`'s commands are words (`view`, `last`,
  `trace`, `new`, `quit`) and there is no `render`.
- **`pfilt -e 1` is a ×1 multiplier**, not auto-exposure.
- Internal contradictions reconciled: ASE guidance (3 stale spots), the
  all-black `-ab` values in patterns.md, ch02's "typical" column vs its own
  presets, and ch07's three conflicting "High-Fidelity" definitions.

**Added in pass 2**: parallelism (`-n`) and the ambient cache (`-af`) with its
staleness hazard; an operational convergence-checking procedure; the sensor
points file contract; a real `rmtxop` section (previously a dead index pointer);
`falsecolor -s a` auto-scale and the `-m` default of 179.

## Pass 1 (2026-08-07)

Reviewed against the official Radiance man pages, the `NREL/Radiance` source,
and LM-83; several claims were checked by **running Radiance locally**.

**Empirically verified**: `rtrace -oov` returns 6 fields with RGB in `$4 $5 $6`
(a 10,000 lux calibrated sky yields 9985 lux via `$4 $5 $6`, and **0** via
`$7 $8 $9`); Reinhart counts via `reinhart.cal` (`1 + 144·MF²` → 145 / 577 /
2305 / 5185); `gensky` rejects a missing positional date ("bad month");
`179 × (0.265, 0.670, 0.065) = 47.4 / 119.9 / 11.6`; the transmissivity formula
(Tn 0.88 → 0.9584, and an independent source's Tn 0.48 → 0.5232).

**Corrected in this pass** (each was wrong before): `-oov` field indices
(was `$7 $8 $9`); `gendaymtx -5` needs a disk-size argument (`-5 0.533`, else it
exits); the tensor-tree `genBSDF` call needs `-t4 N` (without it you get Klems);
`epw2wea` takes two positional filenames and cannot be piped; Reinhart MF:2 is
577, not 580; `dctimestep -n` is only for headerless sky input, not a reflexive
`-n 8760`; ASE is direct-sun-only per LM-83, not "requires 5PM"; `evalglare -t`
takes three arguments and pairs with `-b`; `genklemsamp -c` and `rcontrib -c`
are different quantities (rcontrib's default is 1, not 1000).

**Asserted but not executed**: the full 3PM/5PM matrix pipelines (no BSDF or
weather data on hand). Their syntax was checked flag-by-flag against the man
pages, but the chains have not been run end to end — treat first use as a test.

**Where the guides differ:**
- The transmissivity formula restores a square root lost in DOCX conversion.
  Without it, Tn=0.88 yields −22.18 instead of the ≈0.96 both guides state.
- Sky calibration appears as `-b` (zenith radiance) in one guide and `-B`
  (horizontal irradiance) in the other. Both are documented in ch06; they
  describe the same sky and must not be interchanged.
- `rcalc` field indices differ between the guides because one uses `-oov` and
  the other does not. The rule is in ch03.
- The overview guide describes 5PM with a single BSDF; the LBNL tutorial
  requires **two** (Klems + tensor tree). Ch 10 follows the tutorial — using one
  BSDF makes the "accurate" direct term no better than the 3PM one it replaces.
- Ambient parameters for matrix generation are far higher in the LBNL source
  (`-ab 10/12 -ad 50000/65536 -lw 1.52e-5`) than the general-purpose presets in
  ch02. Matrix errors propagate into all 8,760 hours; use the ch09/ch10 values
  for matrix work.
