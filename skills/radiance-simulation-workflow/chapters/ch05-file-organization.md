# Part V: File Organization (Best Practices)

## Core Idea
Radiance projects accumulate many small text files across several categories
(materials, geometry, skies, views, octrees, images, results, scripts). A
directory layout that mirrors the *pipeline stages* keeps scenarios separable
and makes it obvious which artifacts are derived and safe to regenerate.

## Frameworks Introduced

- **Stage-mirrored directory layout**: one folder per pipeline stage, so a
  file's location tells you where in the chain it came from.
  - When to use: any project beyond a single throwaway test.
  - How: the tree below.
  - Why it works: separates *authored* inputs (materials, geometry, views) from
    *derived* outputs (octrees, images, results) — the derived ones can be
    deleted and rebuilt, the authored ones cannot.

- **Scenario separation by filename**: encode the variant in the name
  (`sky_overcast.rad`, `sky_sunny_june_12pm.rad`, `scene_overcast.oct`).
  - When to use: comparing sky conditions, glazing options, or shading states.
  - How: name the sky and the octree for the condition, so an octree is never
    ambiguous about which sky it baked in.
  - Failure mode: a generically named `scene.oct` silently carries yesterday's
    sky into today's run.

## Key Concepts
- **Authored input** — hand-written and irreplaceable: `.mat`, `.rad`, `.vf`.
- **Derived artifact** — regenerable from inputs: `.oct`, `.hdr`, results files.
- **Scenario** — one combination of sky, geometry and material state.

## Mental Models
- Think of the tree as **the pipeline laid out on disk**: `materials/` and
  `geometry/` feed `octrees/`, which feed `images/` and `results/`.
- Treat **`octrees/` and `images/raw_hdr/` as a cache** — anything there should
  be reproducible from `scripts/` plus the authored inputs.

## Reference Tables

### Numbered structure (preferred — pipeline order encoded in the names)
```
project_directory/
├── 01_geometry/        # .rad scene geometry
├── 02_materials/       # .mat / .rad material definitions
├── 03_views/           # .vf camera views
├── 04_skies/           # .rad sky files and .epw climate files
├── 05_bsdf/            # .xml complex fenestration (BSDF)
├── 06_octrees/         # compiled .oct
├── 07_scripts/         # .sh / .bat / .py — scripts RUN FROM HERE
├── 08_results/         # .ill, .dgp, .txt, .mtx, grid.pts, view_grid.ray
├── 09_images/
│   ├── hdr/            # raw HDR
│   └── png/            # post-processed for reports
├── 10_schedules/       # occupancy schedules (.csv)
└── 11_files/           # miscellaneous
```

**Scripts live in `07_scripts/` and use relative paths** (`../01_geometry/room.rad`).
That single convention is what lets the whole project folder be moved, copied or
shared without breaking anything.

The numeric prefixes sort the directories into pipeline order, so the layout
itself teaches the workflow — inputs (01–05), compiled (06), automation (07),
outputs (08–09).

### Alternative flat naming
The same separation without numbering, if you prefer names to sort alphabetically:
```
project_directory/
├── materials/          # .mat files (building_materials.mat, furniture_materials.mat)
├── geometry/           # .rad files (room.rad, site.rad, window_details.rad)
├── skies/              # sky definitions (sky_overcast.rad, sky_sunny_june_12pm.rad)
├── views/              # .vf files (interior_view1.vf, plan_view_wp.vf)
├── octrees/            # compiled .oct files (scene_overcast.oct)
├── images/             # output images
│   ├── raw_hdr/
│   └── final_png/
├── results/            # numerical data (.txt, .csv, .dat from rtrace)
├── scripts/            # BASH or other automation
└── README.md           # settings, scenarios, notes
```

### What belongs where
| Directory | Contents | Regenerable? |
|---|---|---|
| `materials/`, `geometry/`, `views/` | Authored inputs | **No** — back these up |
| `skies/` | `gensky` output + hand-written sky geometry | Partly (the geometry is authored) |
| `octrees/` | `.oct` from `oconv` | Yes |
| `images/raw_hdr/` | `rpict` output | Yes (expensively) |
| `images/final_png/` | Tone-mapped exports | Yes |
| `results/` | `rtrace`/`rcalc` numerical output | Yes (expensively) |
| `scripts/` | The automation that makes the above true | **No** |

## Worked Example — adapting a flat script to the tree

The guide's example script assumes every file sits in the current directory,
which is fine for a first run. Promoting it to the structured layout is a
matter of pathing the variables — and the win is that the octree name now
records its own sky:

```bash
MATERIAL_FILE="materials/project_materials.mat"
GEOMETRY_FILE="geometry/room.rad"
SKY_FILE="skies/sky_overcast.rad"
SKY_GEOMETRY_FILE="skies/sky_geometry.rad"
OCTREE_FILE="octrees/scene_overcast.oct"
POINTS_FILE="results/calc_points.txt"

oconv $MATERIAL_FILE $GEOMETRY_FILE $SKY_FILE $SKY_GEOMETRY_FILE > $OCTREE_FILE
```

Swapping to a sunny scenario now changes two names (`sky_sunny_june_12pm.rad`,
`scene_sunny.oct`) and leaves the overcast results intact for comparison —
whereas the flat version would have overwritten `scene.oct` in place.

## Anti-patterns
- **A single generic `scene.oct`** — you lose track of which sky is baked in and
  can't compare scenarios.
- **Committing `.oct` and `.hdr` to version control** — large, binary, and
  regenerable; track the inputs and scripts instead.
- **Keeping only the final PNG** — the linear `.hdr` holds the actual data; the
  export is lossy and re-tone-mapping later needs the original.
- **No README** — six months on, nobody remembers which ambient parameters or
  exterior illuminance produced the numbers in `results/`.

## Key Takeaways
1. Mirror the pipeline in the directory tree; location then implies provenance.
2. Separate authored inputs from derived artifacts — back up the first, cache
   the second.
3. Encode the scenario in sky and octree filenames.
4. Record simulation settings (ambient parameters, exterior illuminance, sky
   date/time) in the README — they are not recoverable from the outputs.

## Connects To
- **Ch 3**: `oconv` ordering is unchanged by the layout, only the paths grow.
- **Ch 4**: `images/raw_hdr/` vs `final_png/` follows the linear-then-exported
  distinction.
- **Ch 2**: the settings worth recording in the README are the parameters set there.
