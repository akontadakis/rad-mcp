# Chapter 9: The Three-Phase Method (3PM)

> **Credit.** Command sequence adapted from A. McNeil, *The Three-Phase Method for Simulating
> Complex Fenestration with Radiance*, LBNL tutorial (2010, revised 2013).
> Also drawn on: "Three-Phase Simulation in Radiance Using Secondary Reference Geometry".

## Core Idea
3PM decouples the light path into three independently computed matrices —
interior transport (V), the fenestration's BSDF (T), and exterior transport (D)
— so swapping a glazing system means swapping one XML file and re-running a fast
multiply, not recomputing the scene.

## Frameworks Introduced

- **The governing equation**: `i = V·T·D·s` (point-in-time, sky **vector**) or
  `I = V·T·D·S` (annual, sky **matrix**).
  | Matrix | Describes | Depends on |
  |---|---|---|
  | **S / s** | Sky patch luminances | Climate file / moment |
  | **D** | Sky → **exterior** face of the window | Site, obstructions |
  | **T** | The fenestration BSDF | **The product being tested** |
  | **V** | **Interior** window face → sensors or pixels | Room geometry |
  - Lowercase `s`/`i` denote the single-timestep case; the machinery is identical.

- **The window becomes a `glow` source for V** — the central trick of the method.
  - How: redefine the window with a `glow` material whose **normal faces into
    the room**, build a separate octree, and let `rcontrib` measure how light
    leaving that surface reaches the sensors.
  - Verify with `rvu -ab 2 -vf views/back.vf model_vmx.oct` — confirm it emits
    inwards. A flipped normal silently yields near-zero.
  - The primitive is `4 R G B maxrad` — **four** reals. `4 1 1 1 0` is correct;
    `4 1 1 0` is a fatal `oconv` error ("bad arguments for glow").
  - `maxrad 0` means "never tested as a *direct* source"; the surface still
    contributes through the ambient calculation, which is exactly what the
    V-matrix trace measures (see Ch 1). The *coefficients* carry the magnitude,
    so the RGB stays neutral `1 1 1`.

- **Two octrees, two roles** — do not conflate them:
  | Octree | Contains | Used for |
  |---|---|---|
  | `model_dmx.oct` | Building + context + ground + **white sky** | **D** matrix |
  | `model_vmx.oct` | Room interior + **window as `glow`** + materials | **V** matrix |
  - The D octree deliberately **excludes room interior** if it doesn't affect
    light arriving at the window exterior.

- **`genklemsamp` → `rcontrib` for D**: `genklemsamp` emits rays from the window
  distributed over the Klems basis; `rcontrib` bins their sky contributions.
  - **The two `-c` flags are different quantities that must agree.**
    `genklemsamp -c N` = rays sampled **per Klems direction** (default 1000).
    `rcontrib -c N` = how many input rays to **average into one output record**
    (default **1**). Set rcontrib's `-c` to the same N so it averages exactly
    the batch genklemsamp emits per direction — otherwise record boundaries
    misalign and the D matrix is wrong.
  - `-vd` is the window's **outward** normal (e.g. `0 -1 0` south-facing, Y north).

- **Klems binning orientation** (`-b kbinS|kbinN|kbinE|kbinW|kbinD`): the V-matrix
  binning must name the window's orientation, or use the general
  `kbin(nx,ny,nz,ux,uy,uz)` form. `Nkbins` (145) is defined in `klems_int.cal`.

- **Secondary reference geometry (stencil)** — constrain V to a specific area,
  such as a virtual work-plane, producing illuminance-map HDRs. See Worked Example.

- **`rfluxmtx` is the modern alternative to raw `rcontrib`** — it wraps the
  sampling and binning the commands below do by hand.
  - **Prefer it for new work.** The Radiance man page recommends it over raw
    `rcontrib` for exactly this task; it removes both the `genklemsamp -c` /
    `rcontrib -c` pairing and the `kbin` orientation bookkeeping — the two most
    common sources of a silently wrong matrix.
  - The `rcontrib` form is documented here because the LBNL tutorials this
    chapter follows predate it and most published scripts use it, so you need to
    be able to read them. Understand the manual path; reach for `rfluxmtx`.

## Key Concepts
- **`.vmx` / `.dmx` / `.skv` / `.smx` / `.xml`** — view / daylight matrix, sky vector, sky matrix, BSDF.
- **Klems basis** — 145-direction hemispherical subdivision; `klems_int.cal`.
- **Tregenza / Reinhart sky** — sky discretization; `reinhart.cal`.
- **`genklemsamp`** — generates Klems-distributed sample rays on a window.
- **`vwrays`** — generates camera rays for image-based matrices.
- **Stencil geometry** — secondary `glow` surface used to mask V to an area.

## Reference Tables

### Sky discretization (`genskyvec -m` / `gendaymtx -m`)
| `-m` | Scheme | Patches |
|---|---|---|
| 1 | Tregenza (default for `gendaymtx`) | 145 |
| 2 | Reinhart MF:2 | 577 |
| 4 | Reinhart MF:4 | 2,305 — better direct solar handling |

### The sensor points file (`.pts`)

One record per sensor, **six numbers**: `x y z  dx dy dz` — position then sensor
normal. `0 0 1` is an upward-facing horizontal sensor (the usual work-plane case).

- **Row order is the contract.** It sets the column order of every downstream
  matrix and of the final `.ill`, so Ch 11's per-point metrics map back to floor
  positions only if you keep the file that generated them. Keep it beside the
  results.
- Units follow the scene (metres if the model is in metres).
- No header. Comments are not part of the format — keep the file pure numbers.
- Generate it reproducibly (`cnt` + `rcalc`, `gengrid`, or a script) rather than
  by hand, so the ordering is regenerable.

### `rmtxop` — matrix operations and the photometric step

`dctimestep` emits RGB; `rmtxop` turns that into a single photometric column and
does any matrix algebra you need.

| Flag | Meaning |
|---|---|
| `-fa` | ASCII output (`-ff`/`-fd` for float/double) |
| `-t` | Transpose |
| `-c r g b` | Per-component coefficients — **three values collapse RGB to one column** |

```bash
dctimestep V.vmx T.xml D.dmx sky.smx | rmtxop -fa -t -c 47.4 119.9 11.6 - > out.ill
```

Those coefficients are just **179 × (0.265, 0.670, 0.065)** — the same photopic
weighting as the `rcalc` form in Ch 3, pre-multiplied. Either route is valid;
`rmtxop -c` keeps it inside the matrix pipeline, `rcalc` is easier to read. The
trailing `-` means "read the matrix from stdin".

### File extension conventions (McNeil)
| Ext | Contents |
|---|---|
| `.vmx` | View matrix (illuminance at points) |
| `.hdr` | View matrix as renderings, one per Klems bin |
| `.dmx` | Daylight matrix |
| `.skv` | Sky vector (single timestep) |
| `.smx` | Sky matrix (annual) |
| `.xml` | Transmission matrix (BSDF from WINDOW) |
| `.pts` | Sensor points (`x y z dx dy dz`) |

### LBNL WINDOW settings for a Radiance BSDF
- Check **"Use matrix method for specular systems"**
- Select **"Visible band"**
- Angular basis → **"W6 standard basis"** (Klems 145)

### Typical matrix parameters (McNeil & Lee 2013)
| Matrix | Parameters |
|---|---|
| V | `-ab 12 -ad 50000 -lw 2e-5` (tutorial also uses `-ab 10 -ad 65536 -lw 1.52e-5`) |
| D | `-c 1000 -ab 2 -ad 1024` |

## Code Examples

**Sky — point-in-time vector, then annual matrix**:
```bash
gensky 1 21 11 | genskyvec -m 4 > sky_0121_1100_m4.skv

epw2wea USA_IL_Chicago-Midway.AP.725340_TMY.epw Chicago.wea
gendaymtx -m 4 Chicago.wea > Chicago_m4.smx
```

**D — daylight matrix** (south-facing, outward normal `0 -1 0`):
```bash
oconv materials/testroom.mat objects/ground.rad objects/testroom.rad \
      skies/sky_white1.rad > model_dmx.oct

genklemsamp -vd 0 -1 0 objects/window.rad | \
  rcontrib -c 1000 -e MF:4 -f reinhart.cal -b rbin -bn Nrbins \
           -m sky_glow -faf model_dmx.oct > results/south.dmx
```

**V — window as an inward-facing glow source**:
```
void glow window_glow_material
0
0
4 1 1 1 0   # R G B maxrad — FOUR reals. `4 1 1 0` is a fatal oconv error.

window_glow_material polygon window_surface
0
0
12 <v1x> <v1y> <v1z> ...
```
```bash
oconv materials/testroom.mat objects/window_glow.rad objects/testroom.rad > model_vmx.oct
rvu -ab 2 -vf views/back.vf model_vmx.oct      # verify it glows INWARD

rcontrib -f klems_int.cal -b kbinS -bn Nkbins -m window_glow_material \
         -I+ -ab 12 -ad 50000 -lw 2e-5 \
         model_vmx.oct < data/photocells.pts > results/photocells.vmx
```

**V as images** — 145 HDRs, one per Klems bin:
```bash
vwrays -ff -vf views/back.vf -x 600 -y 600 | \
  rcontrib `vwrays -vf views/back.vf -x 600 -y 600 -d` -ffc -fo \
  -o images/vmx/window_%03d.hdr -f klems_int.cal -b kbinS -bn Nkbins \
  -m window_glow_material -ab 12 -ad 50000 -lw 2e-5 model_vmx.oct
```

**Combine** (order is always V T D S):
```bash
# Point in time, illuminance at points
dctimestep results/photocells.vmx data/your_bsdf.xml \
           results/south.dmx skies/sky_0121_1100_m4.skv | \
  rcalc -e '$1=179*($1*0.265+$2*0.670+$3*0.065)' > results/illum_lux.dat

# Annual — the .smx header carries the timestep count, so no -n is needed
dctimestep results/photocells.vmx data/your_bsdf.xml \
           results/south.dmx Chicago_m4.smx > results/annual_illum_points.dat

# Annual renderings
dctimestep -o annual_images/image_%04d.hdr \
           images/vmx/window_%03d.hdr data/your_bsdf.xml \
           results/south.dmx Chicago_m4.smx
```

## Worked Example — masking V to a virtual work-plane

To get an illuminance *map* of a specific surface rather than a whole view,
trace the view rays through a **stencil octree** first and keep only the hits.

Stencil geometry (`objects/ref_plane.rad`) — a `glow` plane whose blue channel
acts as the hit flag:
```
void glow stencil_material
0
0
4 1 1 1 0

stencil_material polygon workplane_stencil
0
0
12  -1 -1 0    1 -1 0    1  1 0   -1  1 0
```
```bash
xform -t 2 2 0.75 objects/ref_plane.rad > objects/ref_plane_transformed.rad
oconv objects/ref_plane_transformed.rad > model_ref_stencil.oct

vwrays -ff -vf views/floor.vf -x 600 -y 600 \
  | rtrace -w -h -ff -opv model_ref_stencil.oct \
  | rcalc -if6 -of -e '$1=$1;$2=$2;$3=$3;$4=0;$5=0;$6=if($6-0.5,1,0)' \
  | rcontrib `vwrays -vf views/floor.vf -x 600 -y 600 -d` -n `nproc` -ffc -fo \
    -o images/vmx/workplane_contrib_%03d.hdr -f klems_int.cal -b kbinS -bn Nkbins \
    -m window_glow_material -I -ab 12 -ad 50000 -lw 2e-5 model_vmx.oct
```

Read the `rcalc` expression as the mask: `$1 $2 $3` keep the hit point,
`$4 $5` are zeroed, and `$6 = if($6-0.5, 1, 0)` sets the final component to 1
only where the stencil's blue channel exceeds 0.5 — i.e. where the ray actually
hit the reference plane. With `-I`, `rcontrib` **skips** any ray whose last
component is 0, so only stencil hits are computed. Feed the resulting HDRs into
`dctimestep` exactly like any other V matrix.

## Anti-patterns
- **Leaving `rcontrib -c` at its default of 1** while `genklemsamp` emits 1000
  rays per direction — record boundaries misalign and the D matrix is silently
  wrong. They are different quantities, not two copies of one constant.
- **Glow window normal facing outward** — near-zero V matrix. Check with `rvu`.
- **Wrong `kbin` orientation** — `kbinS` on an east-facing window misbins every
  direction.
- **Recomputing V and D per glazing option** — discards the method's whole point.
- **One D matrix across non-uniformly shaded windows** — subdivide (see below).
- **Using 3PM for ASE or direct-sun-critical work** — see Ch 10.
- **Reflexively adding `-n 8760`** — only headerless sky input needs `-n`, and
  then it must match the real timestep count (an abbreviated `.wea` may have far
  fewer than 8760).

## Window grouping and subdivision (PDF appendix)
- **D**: a single matrix per orientation is fine **only** when there are no
  significant local obstructions. Non-uniform external shading across a facade
  requires separate D matrices per window group, or subdivision of one window.
- **V**: divisions should generally mirror the D divisions.
- **Variable-height shading** (raisable blinds): subdivide the window into
  horizontal bands for V, use multiple BSDFs (glazed part vs shaded part), and
  combine the results. The stencil technique adapts to analysing such bands.

## Key Takeaways
1. `i = V·T·D·s`; only `T` changes when the glazing changes.
2. The window is a `glow` source facing **into** the room for V — verify with `rvu`.
3. Two octrees: `model_dmx.oct` (with white sky) and `model_vmx.oct` (glow window).
4. `genklemsamp -c N` (rays per Klems direction) and `rcontrib -c N` (rays
   averaged per record) must agree — rcontrib's own default is 1, not 1000.
5. `-b kbinS/N/E/W/D` must match the window orientation.
6. `dctimestep` order is always V T D S. A `gendaymtx` `.smx` is
   self-describing; `-n` is only for **headerless** input (e.g. with `-if`).
7. Subdivide D (and mirror in V) when external shading is non-uniform.

## Connects To
- **Ch 8**: the daylight-coefficient foundation this refines.
- **Ch 10**: five-phase, which corrects this method's direct solar term.
- **Ch 11**: turning the output into DA/UDI/ASE.
