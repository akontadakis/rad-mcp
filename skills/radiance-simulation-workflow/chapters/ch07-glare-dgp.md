# Chapter 7: Glare Analysis (DGP with evalglare)

## Core Idea
DGP is a two-step process: `rpict` renders a **180° fisheye HDR** from the
occupant's eye position, then `evalglare` finds glare sources in that image and
computes the probability. Both steps have mandatory requirements — a non-fisheye
view or a low-quality render produces a number that looks fine and is wrong.

## Frameworks Introduced

- **The fisheye requirement**: `evalglare` is designed for a full 180°
  hemisphere and its maths assumes one.
  - When to use: every DGP run, without exception.
  - How: `-vth` (hemispherical fisheye) or `-vta` (angular fisheye), **plus**
    `-vh 180 -vv 180`. The view type alone is not enough — the angles must also
    be 180.
  - Failure mode: a perspective view yields a DGP that is simply not DGP.

- **Background luminance drives DGP** — so render quality is not cosmetic.
  - When to use: choosing ambient parameters for a glare study.
  - How: use Ch 2's **High-Fidelity** preset — `-ab 7 -ad 4096 -as 2048
    -ar 1024 -aa 0.15`. DGP depends on vertical eye illuminance and on the
    contrast against the background; under-sampled indirect light corrupts both.
  - The `-ab 6 -ad 2048 -as 1024 -ar 512 -aa 0.15` set quoted in some guides is
    a reasonable floor, but it is *not* the High-Fidelity preset — do not call
    it that in a report.

- **Always generate the check image** (`evalglare -c`): it renders which pixels
  were classified as glare sources.
  - When to use: every run, and especially when a result surprises you.
  - Why: it converts "the number seems wrong" into "the window was/wasn't
    detected", which is a diagnosable statement.

- **Task-area thresholding**: when the default fixed threshold misfires in a
  very bright or very dim scene, define a task area whose luminance sets the
  detection context.
  - **`-t xpos ypos angle`** takes **three mandatory arguments** — task centre
    x, y (pixel coords) and the opening angle in **radians**. A bare `-t` will
    silently swallow following tokens as coordinates.
  - **`-b factor`** is the separate multiplier applied to that task luminance
    (default 5); with a fixed threshold it is the cd/m² value (default 2000).
    `-t` alone changes nothing about the multiplier.

## Key Concepts
- **DGP (Daylight Glare Probability)** — fraction of people disturbed by daylight glare, 0–1.
- **Fisheye view** — `-vth` hemispherical (equidistant) or `-vta` angular projection.
- **Glare source** — a contiguous bright region above the detection threshold.
- **Check image (`-c`)** — diagnostic HDR marking detected glare sources in colour, the rest greyscale at original luminance.
- **`-t xpos ypos angle`** — task-area position (pixels) and opening angle (radians); three arguments.
- **`-b factor`** — multiplier on task luminance (default 5), or the fixed threshold in cd/m² (default 2000).
- **Low-light correction** — applied to DGP when vertical illuminance < 500 lux.
- **`evalglare` notices — read them.** *"Vertical illuminance less than 380 lux"*
  means the DGP is probably an **underestimate**; the low-light regime is where
  the correlation is weakest. A confident-looking low DGP on an obviously glary
  scene is often this notice being ignored.
- **Other indices** — `evalglare` also reports DGI, DGI_MOD, UGR, UGR_EXP, VCP, CGI, UDP.

## Reference Tables

### DGP interpretation — perception bands (one moment)
| DGP | Perception (EN 17037 Table E.1) |
|---|---|
| ≤ 0.35 | Not perceived |
| 0.35 – 0.40 | Perceived, mostly not disturbing |
| 0.40 – 0.45 | Often disturbing |
| ≥ 0.45 | Mostly intolerable |

**These are perception labels, not a verdict.** A single DGP is never a
pass/fail — see below.

These are the Wienold perception classes for a **single point in time** — one
image, one DGP. That is what the workflow in this chapter produces.

### A compliance verdict is not a single DGP

**EN 17037 judges glare over time, not at one moment.** Its criterion is
`DGP_e<5%`: DGP must not exceed the level for more than **5% of usage time**,
against a defined reference usage period (08:00–18:00, Mon–Fri, year-round).
Its levels — Minimum 0.45, Medium 0.40, High 0.35 — are the *same numbers* as
the bands above but mean something different: an exceedance ceiling, not an
instantaneous rating.

Practical consequence: a compliance study needs DGP at **many timesteps** from
the expected worst-case position (usually near the façade), then the fraction of
usage time exceeding the level — not one render. Quoting a single "DGP = 0.38,
acceptable" against EN 17037 is a category error.

The standard also **rules DGP out** in cases where vertical illuminance predicts
glare poorly (horizontal openings, deep or dark spaces with small openings,
positions far from the openings), and forbids substituting UGR for it. See the
`lighting-standards-compliance` skill (ch03) for the obligation and the exclusions
— this chapter only produces the number.

### View types (`-vt`)
| Flag | Name | Use |
|---|---|---|
| `v` | Perspective | Architectural visualization |
| `l` | Parallel | Elevations, plans, axonometric |
| `c` | Cylindrical panorama | 360° panoramas |
| **`h`** | **Hemispherical fisheye** | **Required for `evalglare`** |
| `a` | Angular fisheye | Full 360°; also suitable for glare |
| `s` | Planisphere (stereographic) | Sun-path diagrams |

### Image resolution for `evalglare`
| | Pixels |
|---|---|
| Recommended | 1000 × 1000 |
| Minimum | 800 × 800 |
| Do not exceed | 1500 × 1500 (runtime) |

## Code Examples

Render the fisheye:
```bash
rpict -vth -vh 180 -vv 180 \
      -vp [x y z] -vd [xd yd zd] -vu [xu yu zu] \
      -x 1000 -y 1000 \
      -ab 7 -ad 4096 -as 2048 -ar 1024 -aa 0.15 \
      scene.oct > view.hdr
```

Preview the same view interactively before committing to a render:
```bash
rvu -vth -vp 2.5 1.5 1.2 -vd 0 1 0 -vu 0 0 1 -vh 180 -vv 180 scene.oct
```

Evaluate, with a check image:
```bash
evalglare -d -c glare_check.hdr view.hdr > dgp.txt
```

## Worked Example — a south-facing desk at 2:30 PM

```bash
AB=7; AD=4096; AS=2048; AR=1024; AA=0.15   # Ch 2 High-Fidelity preset

# 1. Sky for the moment of interest (note: NOT overcast — glare needs the sun)
gensky $MONTH $DAY $TIME -a $LATITUDE -o $LONGITUDE -m $MERIDIAN > $SKY_FILE

# 2. Octree
oconv ${MAT_DIR}/*.mat ${GEOM_DIR}/*.rad ${SKY_FILE} > $OCTREE_FILE
if [ $? -ne 0 ]; then echo "Error during oconv. Exiting."; exit 1; fi

# 3. Fisheye render from the occupant's eye position
rpict -vf $VIEW_FILE -x 1000 -y 1000 \
      -ab $AB -ad $AD -as $AS -ar $AR -aa $AA \
      $OCTREE_FILE > $HDR_IMAGE
if [ $? -ne 0 ]; then echo "Error during rpict. Exiting."; exit 1; fi

# 4. DGP + check image
evalglare -d -c $GLARE_CHECK_IMG $HDR_IMAGE > $GLARE_RESULTS
```

The view file `glare_view.vf` must itself carry `-vth -vh 180 -vv 180`; putting
the view in a `.vf` does not exempt it from the fisheye requirement. Open
`$GLARE_CHECK_IMG` before believing the DGP — if the window isn't marked as a
source, the threshold is wrong, not the building.

## Anti-patterns
- **Perspective view into `evalglare`** — the most common DGP error.
- **Setting `-vth` but leaving `-vh`/`-vv` at defaults** — both are required.
- **Draft ambient settings** — background luminance is wrong, so DGP is wrong.
- **Skipping the `-c` check image** — you lose the only cheap diagnostic.
- **Accepting "no glare sources found" on an obviously bright window** — the
  threshold is too high; set a task area with `-t xpos ypos angle` and tune the
  multiplier with `-b factor`.
- **Writing a bare `-t`** — it takes three arguments and will otherwise consume
  whatever follows as coordinates.
- **Rendering above 1500×1500** — runtime cost with no accuracy gain.
- **Using an overcast sky** — glare studies need the sun that causes the glare.
- **Reporting one DGP as an EN 17037 verdict** — the standard's criterion is
  exceedance over 5% of usage time, which needs many timesteps.

## Key Takeaways
1. `-vth -vh 180 -vv 180` is mandatory; view type and angles both.
2. Use High-Fidelity ambient settings — DGP depends on background luminance.
3. 1000×1000 is the sweet spot; 800×800 floor, 1500×1500 ceiling.
4. Always write a `-c` check image and actually look at it.
5. Thresholds 0.35 / 0.40 / 0.45 separate imperceptible / perceptible /
   disturbing / intolerable **at one moment**. EN 17037 reuses the same numbers
   as a `DGP_e<5%` exceedance ceiling over usage time — a different question
   needing many timesteps, not one render.
6. `-t xpos ypos angle` (3 args) plus `-b factor` rescue scenes where the fixed
   threshold misfires — `-t` sets *where*, `-b` sets *how much*.

## Connects To
- **Ch 4**: `rpict` view parameters and `.vf` files.
- **Ch 2**: the ambient parameter tiers.
- **Ch 10**: five-phase method, when annual glare/direct sun matters.
- **lighting-standards-compliance** (ch03): EN 17037's `DGP_e<5%` criterion, the
  reference usage period, the worst-case-position rule, where DGP is ruled out,
  and why UGR cannot be substituted. That skill owns the verdict; this one owns
  the render.
