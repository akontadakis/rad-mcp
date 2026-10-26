# Part IV: Visualization and Further Analysis

## Core Idea
Visualization splits by *when* you need it: `objline`/`objview` before the
octree exists (geometry sanity), `rvu` interactively on the octree (view
setup and probing), `rpict` for batch-quality output, and `falsecolor` to turn
a radiometric image into a readable quantitative map.

## Frameworks Introduced

- **Preview before you compile**: check geometry and material assignment
  *without* building an octree.
  - When to use: immediately after authoring `.rad`/`.mat`, before any long run.
  - How: `objline` for a wireframe (no materials needed), `objview` for an
    interactive shaded preview (builds a temp octree, adds default lighting).
  - Why it works: catches flipped normals and missing modifiers in seconds
    rather than after a multi-hour trace.

- **The rvu → rpict handoff**: compose the view interactively, then render it
  in batch.
  - When to use: any image you want at final quality.
  - How: in `rvu`, position the camera and press `view my_view.vf` to save;
    then `rpict -vf my_view.vf …`. A `.vf` stores the view command-line options.

- **The two-image falsecolor pattern**: a quantitative map needs *two* renders
  of the **same view** — an illuminance image for data, a luminance image for
  visual background.
  - When to use: producing a daylight factor or illuminance map.
  - How: `rpict -i -vf plan_view.vf` → data image; `rpict -vf plan_view.vf`
    (same view, no `-i`) → background; combine with `falsecolor -i … -p …`.
  - Failure mode: different views between the two renders silently misregisters
    the data against the background.

- **The falsecolor multiplier**: converts image values into legend units.
  - How: for DF%, `M = 179 × 100 / E_external`. With 10,000 lux → **1.79**.
  - Why: the image holds radiometric values; 179 converts to lux, ×100/E turns
    lux into a percentage.

## Key Concepts
- **`.vf` (view file)** — stored view parameters (`vp` point, `vd` direction, `vu` up, `vh`/`vv` angles).
- **`.hdr`** — Radiance picture file; **linear**, high dynamic range, not display-ready.
- **`rpict -i`** — render an *irradiance/illuminance* image rather than luminance.
- **Tone mapping** — compressing HDR into displayable range (`ximage h`, `normtiff`).
- **Gamma correction** — required (≈2.2) when exporting linear `.hdr` to PNG/JPEG.

## Reference Tables

### Which tool, when
| Stage | Tool | Needs octree? | Purpose |
|---|---|---|---|
| Pre-octree | `objline \| x11meta` | No | Wireframe; geometry only |
| Pre-octree | `objview` | No (makes a temp one) | Interactive shaded check |
| Interactive | `rvu` | Yes | Explore, set view, probe values |
| Batch | `rpict` | Yes | High-quality `.hdr` |
| Display | `ximage` | — | View `.hdr` |
| Analysis | `falsecolor` | — | Pseudo-colour quantitative map |
| Post | `pfilt`, `normtiff`, `convert` | — | Filter, tone-map, export |

### `rvu` interactive commands

**`rvu` commands are words, not single keystrokes** — a single letter works only
as an unambiguous abbreviation (`v` → `view`, `t` → `trace`, `q` → `quit`).

| Command | Action |
|---|---|
| `view [file]` | **Save** the current view to a file (no arg: show current view) |
| `last [file]` | **Restore** a view previously saved with `view` |
| `L [vw [rfile]]` | Load a view from a **rad input file** — *not* a `.vf`; different command |
| `exposure [spec]` | Adjust exposure. Plain number = multiplier; `+n`/`-n` = f-stops; `=` = to picked area |
| `trace` + pick | Trace a ray at the picked point; prints intersection, material, radiance |
| `set [var [val]]` | Change a render parameter live (e.g. `set ab 3`) |
| `new [nproc]` | **Re-render** with the current settings (there is no `render` command) |
| `quit` | Quit |

### `ximage` interactive commands

Here a command **is** a single character, and most act on an *area of interest*
dragged with the left mouse button (a click without drag = a single point).

| Key | Action |
|---|---|
| `a` | Automatic exposure compensation |
| `h` | Human exposure compensation (tone mapping) |
| `=` | Adjust exposure to the area of interest (**not** `e`) |
| **`l`** | **Report photometrically-weighted luminance** over the area — lowercase; there is no `L` |
| `<return>` | Report averaged radiance (max of the three RGB channels) |
| `c` | Report colour, as adjusted by the current exposure |
| `q` | Quit (also `Q`, `^D`) |
| `^R` | Redraw the entire image |
| `q` | Quit |

### `falsecolor` flags
| Flag | Meaning |
|---|---|
| `-i` | Input **data** image |
| `-p` | Background **picture** |
| `-l` | Legend label |
| `-m` | Multiplier: image values → legend units. **Default 179** (radiance→cd/m², or with `rpict -i`, irradiance→lux). `-m` *replaces* it. |
| `-s` | Scale maximum (e.g. `10` for 10% DF). **`-s a`** = *auto*: scale to the image maximum. |
| `-n` | Number of contours **and legend entries** (default **8**) |
| `-cl` | Contour lines instead of solid fill |

## Code Examples

```bash
# Pre-octree checks
objline your_geometry.rad | x11meta
objview your_materials.mat your_geometry.rad

# Interactive, with parameters
rvu -vf my_view.vf -ab 6 -ad 1024 scene.oct

# Batch: luminance image
rpict -vf my_view.vf -x 800 -y 600 \
      -ab 6 -ad 2048 -as 512 -ar 300 -aa 0.08 \
      scene.oct > rendered_image.hdr

# Batch: illuminance map (note -i, plan view)
rpict -vf plan_view.vf -x 512 -y 512 -i \
      -ab 6 -ad 2048 -as 512 -ar 300 -aa 0.08 \
      scene.oct > illuminance_map.hdr
```

Post-processing:
```bash
pfilt -x /2 -y /2 input.hdr > output_filtered.hdr        # halve size (box filter)
# NOTE: `-e 1` is NOT auto-exposure — `-e` is a straight multiplier, so -e 1
# does nothing. pfilt already sets exposure from its two-pass average.
# For tone mapping use pcond; for display autoexposure use ximage's `a`.
normtiff input.hdr output.tif                            # tone-map, gamma 2.2
convert input.hdr -gamma 2.2 output.png                  # ImageMagick
convert input.hdr -gamma 2.2 -quality 90 output.jpg
```

## Worked Example — a daylight factor map

Target: a plan-view DF map scaled 0–10%, against a 10,000 lux exterior.

```bash
ILLUM_IMG="illuminance_map.hdr"   # rpict -i -vf plan_view.vf
LUM_IMG="rendered_image.hdr"      # rpict    -vf plan_view.vf  (SAME view)
EXTERNAL_HORIZONTAL_ILLUMINANCE=10000

# M = 179 × 100 / E_external  →  1.79
FC_MULTIPLIER=$(awk "BEGIN {print 179 * 100 / $EXTERNAL_HORIZONTAL_ILLUMINANCE}")

falsecolor -i ${ILLUM_IMG} -p ${LUM_IMG} \
           -l "Daylight Factor (%)" -m ${FC_MULTIPLIER} \
           -s 10 -n 10 -cl \
           > daylight_factor_map.hdr

ximage daylight_factor_map.hdr
```

The `-s 10` caps the legend at 10% DF — anything above saturates, so choose the
scale after seeing the range, or a bright room reads as uniformly maxed out.
The multiplier and `-s` together decide whether the map is informative or a
flat wash.

## Anti-patterns
- **Rendering the data and background images from different views** — the
  falsecolor overlay misregisters with no warning.
- **Forgetting `-i` on the data render** — you map luminance while labelling it
  illuminance.
- **Sending a raw `.hdr` into a report** — it is linear; without gamma
  correction (≈2.2) it looks wrong on every normal display.
- **Picking `-s` before knowing the range** — saturates the map. Run once with
  **`-s a`** (auto-scale to the image maximum) to learn the range, then pin a
  round number so successive maps stay comparable.
- **Using preview lighting (`objview`) to judge brightness** — it adds default
  lights that are not your scene's.
- **Skimping on ambient parameters for images** — `-ab`/`-ad` drive `rpict`
  quality exactly as they drive `rtrace` accuracy.

## Key Takeaways
1. Preview with `objline`/`objview` *before* spending time on an octree.
2. Compose views in `rvu`, save with `view file.vf`, render with `rpict -vf`.
3. `falsecolor` needs two same-view renders: `-i` data and `-p` background.
4. DF multiplier = `179 × 100 / E_external` (1.79 for 10,000 lux).
5. `.hdr` is linear — gamma-correct before export.
6. `ximage` lowercase **`l`** over a picked area is the quickest way to read a
   luminance value off an image (`=` sets exposure to that area).

## Connects To
- **Ch 3**: consumes the same octree and exterior illuminance reference.
- **Ch 2**: ambient parameters govern `rpict` image quality identically.
- **Ch 5**: where `raw_hdr/` and `final_png/` outputs belong.
