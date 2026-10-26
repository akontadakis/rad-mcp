# Chapter 10: The Five-Phase Method (5PM)

> **Credit.** Command sequence adapted from A. McNeil, *The Five-Phase Method for Simulating
> Complex Fenestration with Radiance*, LBNL tutorial (2013).
> Matrix parameters follow McNeil & Lee (2013).

## Core Idea
3PM smears the sun's intense, directional energy across a whole sky patch. 5PM
**subtracts** that inaccurate direct term and **adds back** an accurate one
computed from thousands of discrete sun positions against a high-resolution
BSDF. It is the method for ASE and for any direct-sun-critical study.

## Frameworks Introduced

- **The governing equation**: `I_5ph = V·T·D·S − Vd·T·Dd·Sds + Cds·Ssun`
  - Term 1 — full 3PM (accurate diffuse, inaccurate direct)
  - Term 2 — the *same* 3PM machinery restricted to direct sun: deliberately
    the wrong part, computed so it can be removed
  - Term 3 — accurate direct sun via the sun-coefficient matrix
  - Read as **total − wrong_direct + right_direct**.

- **Two BSDFs are required** — the detail that most summaries omit:
  | BSDF | Basis | Used by |
  |---|---|---|
  | `fullwindow_klems.xml` | Klems (145) | Terms 1 and 2 (`I_3ph`, `I_ds3ph`) |
  | `fullwindow_t45.xml` | **Tensor tree** | Term 3 (`Cds`) — the accurate direct term |
  - Generate **both with `genBSDF`**. Converting via `bsdf2klems` is discouraged
    here: it handles specular components poorly.

- **`genBSDF` geometry precondition**: the geometry must fit a rectangular
  profile — **width along X, height along Y, depth along Z** — with the
  **positive Z axis pointing into the room**, and the input geometry must **not
  extend into the room** (i.e. it sits in −Z). Use `xform` to get it there first.
  - `-t4 <Nlog2>` produces a rank-4 tensor tree; `-t3` a rank-3 (faster, only
    valid in special cases). **Omitting `-t` entirely yields the Klems basis.**
  - A typical high-resolution production form:
    `genBSDF -n 24 -t4 6 -c 40960 -geom meter +backward +forward fullwindow.rad`

- **The all-black model**: black every non-fenestration material so only the
  intended direct path survives.
  - `-ab 1` for **Vd** — one bounce, with the black model preventing specular
    contamination that `-ab 1` would otherwise admit.
  - `-ab 0` for **Dd** — direct sky component only.
  - `-ab 1` for **Cds** — needed for BSDF sampling; the black model again keeps
    spawned rays from contributing.

- **Direct-calculation parameter discipline**: `-dj 0 -dt 0 -dc 1 -st 1 -ss 0`.
  `-dj 0` aims the shadow ray at the solar disc **centre** for consistency;
  `-dt 0 -dc 1` disable statistical termination. Leaving `-dj` at `rcontrib`'s
  default of **0.9** is the classic cause of "annual simulation missing direct
  sun".

  **Verified defaults** (`<prog> -defaults`) — they differ per program, so the
  flag you must set depends on which binary you are calling:
  | | `-dj` | `-dt` | `-dc` | `-ab` |
  |---|---|---|---|---|
  | `rtrace` | 0 | 0.03 | 0.75 | 0 |
  | `rpict` | 0 | 0.05 | 0.50 | 0 |
  | **`rcontrib`** | **0.9** | **0** | 0.75 | **1** |

  So for `rcontrib` the genuinely *needed* overrides are `-dj 0` and `-dc 1`;
  its `-dt` is already 0 and its `-ab` already 1. Stating all of them is still
  good practice — it makes the run self-documenting and immune to a future
  default change.

- **Three sky matrices, three roles**:
  | Command | File | Role |
  |---|---|---|
  | `gendaymtx -of` | `OakLand.smx` | **S** — total sky |
  | `gendaymtx -of -d` | `OakLand_direct.smx` | **Sds** — direct only, Klems-side |
  | `gendaymtx -5 0.533 -d -m 6 -of` | `OakLand_direct_m6.smx` | **Ssun** — 5PM suns, solid-angle adjusted |

  **`-5` requires the solar disk size in degrees as an argument.** The source
  parses it as `atof(argv[++i])` and exits with *"missing solar disk size
  argument for '-5' option"* if the next token isn't a positive number — so a
  bare `gendaymtx -5 -d …` consumes `-d` as the argument and dies.

## Key Concepts
- **`Vd` / `Dd`** — direct-only view and daylight matrices (all-black model).
- **`Cds`** — direct sun coefficient matrix (`.dsmx`).
- **`Ssun`** — 5PM direct sun matrix; solar energy per sun position.
- **`reinsrc.cal`** — generates sun source directions.
- **BSDF proxy** — a `void BSDF` surface referencing the tensor-tree XML, co-planar with the view-matrix surface.
- **`rcollate`** — reshape/transpose between sensor-major and hour-major.
- **`rlam`** — paste records from multiple files side by side.

## Reference Tables

### The three terms
| Term | Matrices | Sky | BSDF | Octree |
|---|---|---|---|---|
| `I_3ph` | V · T · D | `S` | Klems | `model_3ph.oct` |
| `I_ds3ph` | Vd · T · Dd | `Sds` | Klems | `model_allblack.oct` |
| `I_ds5ph` | Cds | `Ssun` | **Tensor tree** | `model_suns.oct` |

### Matrix parameters (tutorial values)
| Matrix | Command parameters |
|---|---|
| V | `-I+ -ab 10 -ad 65536 -lw 1.52e-5` |
| D | `-c 1000 -ab 2 -ad 1024 -e MF:1` |
| Vd | `-I+ -ab 1 -ad 65536 -lw 1.52e-5` |
| Dd | `-c 1000 -ab 0 -e MF:1` |
| Cds | `-I -ab 1 -ad 65536 -lw 1.52e-5 -dc 1 -dt 0 -dj 0 -st 1 -ss 0 -e MF:6` |

### Sun counts
| MF | Suns |
|---|---|
| MF:1 | 145 |
| **MF:6** | **5,185** — used for `Cds`/`Ssun` |

Solar disc angular diameter: **0.533°**.

## Code Examples

**Model prep — BSDF proxy surface**:
```
# objects/glazing_bsdf.rad
void BSDF BSDFproxy
6 0.30 bsdf/fullwindow_t45.xml 0 0 1 .
0
0

BSDFproxy polygon inside
0
0
12 0.5 0 1   0.5 0 2   3.5 0 2   3.5 0 1
```
```
# materials/testroom.mat — the black material the all-black models need
void plastic black
0
0
5 0 0 0 0 0
```

**Both BSDFs** (geometry first rotated into the −Z half-space):
```bash
xform -rz 180 -rx -90 -t 0 0 -0.15 objects/glazing.rad objects/venetianblind.rad \
      materials/testroom.mat objects/testroom_Swall.rad > bsdf/fullwindow.rad

# Tensor tree — the -t4 <Nlog2> flag is REQUIRED; without it you get Klems
# output under a tensor-tree filename, and term 3 is no better than term 2.
genBSDF +f +b -t4 5 -geom meter -dim 0.5 3.5 1 2 -0.3 0 \
        bsdf/fullwindow.rad > bsdf/fullwindow_t45.xml          # tensor tree

# Klems (default basis — no -t flag)
genBSDF +f +b -geom meter -c 4000 -dim 0.5 3.5 1 2 -0.3 0 \
        bsdf/fullwindow.rad > bsdf/fullwindow_klems.xml        # Klems
```

**Term 1 — `I_3ph`**:
```bash
oconv materials/testroom.mat objects/daymtxsurf.rad objects/ground.rad \
      objects/testroom.rad objects/testroom_Swall.rad \
      objects/viewmtxsurf.rad skies/sky_white.rad > octs/model_3ph.oct

rcontrib -I+ -ab 10 -ad 65536 -lw 1.52e-5 \
         -f klems_int.cal -b kbinS -bn Nkbins -m viewsurf \
         octs/model_3ph.oct < data/photocells.pts > matrices/viewmatrix.vmx

genklemsamp -c 1000 -vd 0 -1 0 objects/daymtxsurf.rad | \
  rcontrib -c 1000 -ab 2 -ad 1024 -e MF:1 -f reinhart.cal -b rbin -bn Nrbins \
           -m sky_glow octs/model_3ph.oct > matrices/daylightmatrix.dmx

epw2wea skies/USA_CA_Oakland.Intl.AP.724930_TMY3.epw skies/OakLand.wea
gendaymtx -of skies/OakLand.wea > matrices/OakLand.smx

# -if = headerless float input, so -n IS required here to declare the count
dctimestep -n 8760 -if matrices/viewmatrix.vmx bsdf/fullwindow_klems.xml \
           matrices/daylightmatrix.dmx matrices/OakLand.smx | \
  rcollate -h -oc 1 > data/i_3ph.txt
```

**Term 2 — `I_ds3ph`** (all-black octree; note `-ab 1` / `-ab 0`):
```bash
xform -m black objects/testroom_Swall.rad objects/testroom.rad \
      objects/ground.rad objects/venetianblind.rad | \
  oconv materials/testroom.mat - objects/viewmtxsurf.rad \
        objects/daymtxsurf.rad skies/sky_white.rad > octs/model_allblack.oct

rcontrib -I+ -ab 1 -ad 65536 -lw 1.52e-5 \
         -f klems_int.cal -b kbinS -bn Nkbins -m viewsurf \
         octs/model_allblack.oct < data/photocells.pts > matrices/viewmatrix_direct.vmx

genklemsamp -c 1000 -vd 0 -1 0 objects/daymtxsurf.rad | \
  rcontrib -c 1000 -ab 0 -e MF:1 -f reinhart.cal -b rbin -bn Nrbins \
           -m sky_glow octs/model_allblack.oct > matrices/daylightmatrix_direct.dmx

gendaymtx -of -d skies/OakLand.wea > matrices/OakLand_direct.smx

dctimestep -n 8760 -if matrices/viewmatrix_direct.vmx bsdf/fullwindow_klems.xml \
           matrices/daylightmatrix_direct.dmx matrices/OakLand_direct.smx | \
  rcollate -h -oc 1 > data/i_ds3ph.txt
```

**Term 3 — `I_ds5ph`** (5,185 suns, tensor-tree BSDF):
```bash
echo "void light solar 0 0 3 1e6 1e6 1e6" > skies/suns.rad
cnt 5185 | rcalc -e MF:6 -f reinsrc.cal -e Rbin=recno \
  -o 'solar source sun${Rbin} 0 0 4 ${Dx} ${Dy} ${Dz} 0.533' >> skies/suns.rad

xform -m black objects/testroom_Swall.rad objects/testroom.rad objects/ground.rad | \
  oconv materials/testroom.mat - objects/glazing.rad objects/venetianblind.rad \
        objects/glazing_bsdf.rad skies/suns.rad > octs/model_suns.oct

rcontrib -I -ab 1 -ad 65536 -lw 1.52e-5 -dc 1 -dt 0 -dj 0 -st 1 -ss 0 -faf \
         -e MF:6 -f reinhart.cal -b rbin -bn Nrbins -m solar \
         octs/model_suns.oct < data/photocells.pts > matrices/directsun.dsmx

gendaymtx -5 0.533 -d -m 6 -of skies/OakLand.wea > matrices/OakLand_direct_m6.smx

dctimestep -n 8760 -if matrices/directsun.dsmx matrices/OakLand_direct_m6.smx | \
  rcollate -h -oc 1 > data/i_ds5ph.txt
```

## Worked Example — combining the three terms

`rlam` pastes the three sensor-major files side by side so one `rcalc` spans all
nine columns:

```bash
rlam data/i_3ph.txt data/i_ds3ph.txt data/i_ds5ph.txt | \
  rcalc -e 'r=$1-$4+$7; g=$2-$5+$8; b=$3-$6+$9' \
        -e '$1=179*(.265*r+.670*g+.065*b)' | \
  rcollate -h -fa1 -ic 8760 -t > results/illum.txt
```

Read the indices against the equation: `$1..$3` are the 3PM RGB, `$4..$6` the
term to subtract, `$7..$9` the term to add — so `$1-$4+$7` per channel *is*
`total − wrong_direct + right_direct`. The subtraction happens per channel
**before** the photopic conversion, which is what keeps all three terms on a
common radiometric basis.

`rcollate -h -fa1 -ic 8760 -t` reshapes the single result column into
**8,760 rows × N sensor columns** — hour-major, ready for Ch 11's metrics.

## Anti-patterns
- **Using one BSDF for everything** — Klems for terms 1–2, tensor tree for term 3.
- **Omitting `-t4`/`-t3` from the tensor-tree `genBSDF` run** — you get a Klems
  BSDF under a tensor-tree filename, and the "accurate" direct term is no better
  than the one it replaces. The filename is not the format.
- **Bare `gendaymtx -5`** — `-5` takes the solar disk size (`-5 0.533`); without
  it the command exits with an error.
- **`bsdf2klems` to derive the Klems BSDF** — poor with specular components; run
  `genBSDF` twice instead.
- **Skipping the `xform`** — geometry must be width-X / height-Y / depth-Z with
  +Z into the room, not extending into it.
- **Leaving `-dj` at `rcontrib`'s 0.9 default** — inconsistent direct sun.
- **Omitting `-dt 0 -dc 1`** — statistical termination drops sun contributions.
- **`-ab 0` for `Cds`** — BSDF sampling needs `-ab 1`; the black model, not
  `-ab 0`, is what suppresses interreflection.
- **Forgetting to black `venetianblind.rad`** in the all-black model — the blind
  itself must be black for the direct terms.
- **Mismatched MF** across suns file, `rcontrib -e MF:`, and `gendaymtx -m`.
- **Sign error** — minus the 3PM direct, plus the 5PM direct.

## Key Takeaways
1. `I_5ph = V·T·D·S − Vd·T·Dd·Sds + Cds·Ssun` — total − wrong + right.
2. **Two BSDFs**: Klems for terms 1–2, tensor tree (**`-t4 N`**) for term 3;
   both via `genBSDF`. Without `-t` you get Klems regardless of the filename.
3. All-black models isolate direct paths: `-ab 1` Vd, `-ab 0` Dd, `-ab 1` Cds.
4. Three sky matrices: `-of` (S), `-of -d` (Sds), `-5 0.533 -d -m 6 -of` (Ssun)
   — `-5` **requires** the disk-size argument.
5. MF:6 → 5,185 suns at 0.533°.
6. `-dj 0 -dt 0 -dc 1 -st 1 -ss 0` for the direct sun coefficient run.
7. Subtract per RGB channel *before* converting to lux.

## Connects To
- **Ch 9**: supplies terms 1 and 2 and all the 3PM machinery.
- **Ch 8**: the coefficient concept underlying `Cds`.
- **Ch 11**: ASE is direct-sun-only; this method's direct term is the accurate
  route **for complex fenestration** — simple glazing needs only `-ab 0` with a
  sun-only sky.
