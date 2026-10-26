# Part I: Preparation — Creating Input Files

## Core Idea
Radiance scenes are plain text. Every surface is a primitive written in one
fixed five-line grammar, and every surface's optical behaviour comes from a
*material* referenced by name — so geometry and materials are authored in
separate files and bound only by that identifier.

## Frameworks Introduced

- **The Radiance primitive grammar**: every primitive — geometric or material —
  is written the same way.
  ```
  modifier type identifier
  N  S1 S2 ... SN     # N string arguments
  I  I1 ... II        # I integer arguments (0 for every primitive in this chapter)
  M  R1 R2 ... RM     # M real arguments
  ```
  **All three counts vary by primitive type — none is structurally fixed.** The
  integer count is 0 for the geometry and materials here, which is why the line
  usually reads as a bare `0`, but data-driven types (`brightdata`, `colordata`,
  `mixdata`) and `instance`/`mesh` use it. The string count is likewise often 0
  for geometry but not in general: `gensky`'s own output opens with
  `void brightfunc skyfunc` / `2 skybr skybright.cal` — a **string count of 2**.
  When hand-parsing, read each count and consume that many values; do not assume.
  - When to use: authoring or parsing any `.rad` / `.mat` content by hand.
  - How: `modifier` names the material (or `void` for a base material);
    `type` is the primitive kind (`polygon`, `sphere`, `plastic`, `glass`…);
    `identifier` is a unique name used for reference and debugging.
    The middle `0` is not optional padding — it is the integer-argument count,
    and it is always 0 for current primitives.
  - Why it works: one grammar for geometry and materials means `oconv` needs a
    single parser, and a material is referenced purely by name — which is why
    ordering at octree time matters (Ch 3).

- **Modifier binding**: a surface's `modifier` field is the *identifier* of a
  material primitive defined elsewhere.
  - When to use: whenever a surface renders black or wrong.
  - How: confirm the material name exists, is spelled identically, and is fed
    to `oconv` **before** the geometry that references it.

- **Transmissivity ≠ transmittance** (`glass`): manufacturers publish
  transmittance `Tn`; Radiance's `glass` wants transmissivity `tn`.
  - When to use: every time you translate a glazing datasheet into Radiance.
  - How: convert with the formula below. For `Tn = 0.88`, `tn ≈ 0.96`.
  - Failure mode: passing `Tn` straight in under-transmits the window and
    silently depresses every interior illuminance — a wrong number, not an error.

## Key Concepts
- **Primitive** — one geometric surface or material definition in the shared grammar.
- **Modifier** — the material identifier applied to a surface; `void` for a base material.
- **Surface normal** — the "front" of a polygon; vertices counter-clockwise viewed from the front.
- **Transmissivity (tn)** — the internal quantity Radiance's `glass` expects.
- **Transmittance (Tn)** — the measured quantity manufacturers report.
- **Generator** — a CLI program (`genbox`, `genprism`, `genrev`, `gensurf`, `genworm`) that emits `.rad` text.

## Mental Models
- Think of a `.rad` file as **a list of nouns** and the `.mat` file as
  **the dictionary that gives them behaviour** — neither means anything alone.
- Think of the surface normal as **which side the light is allowed to see**.
  For an enclosed room, all wall normals point *inwards*.
- Use a **generator** rather than hand-typing vertices whenever the form is
  standard; hand-authoring is for what generators can't express.

## Reference Tables

### Coordinate convention
| Axis | Points toward |
|---|---|
| X | East |
| Y | North |
| Z | Zenith (up) |

Keep units consistent (typically metres) across the whole project.

### Common material types
| Type | Use for | Note |
|---|---|---|
| `plastic` | Painted walls, wood, fabric — most surfaces | Uncoloured specular highlights |
| `metal` | Metallic surfaces | Highlights coloured by the material |
| `glass` | Thin glazing, single pane | Use **one** surface per pane |
| `light` | Primary emitters | Args are R,G,B radiance (W/sr/m²) |
| `glow` | Sky dome, distant luminous surfaces | 4th arg = **maxrad**, the radius within which it is tested as a *direct* source — see below |
| `illum` | Secondary sources (e.g. a window redistributing sky light) | Acts as a source for indirect; can look different when viewed directly |
| `trans` | Translucent — frosted glass, lampshades | Scatters transmitted light |
| `mirror` | Planar mirrors | Costly: triggers virtual light source calculations |

## `glow`'s 4th argument (maxrad) — what it actually does

`maxrad` is the distance within which the surface is tested as a **direct light
source**. It does **not** switch the light off. Measured with a glow panel 2 m
from the sensor (W/m²):

| maxrad | `-ab 0` (direct only) | `-ab 1` (direct + ambient) |
|---|---|---|
| `0` | 0.000 | **0.759** — still lights the scene |
| `5` | 0.755 | 0.755 — within range, so tested directly |
| `-1` | 0.000 | **0.000** — invisible to ambient rays too |

So:
- **`0`** = never tested as a direct source, but **still contributes through the
  ambient calculation**. This is what sky domes use (Ch 3): the `source`
  primitives do the direct work, and the glow supplies the rest.
- **positive** = also acts as a direct source out to that radius.
- **negative** = invisible to ambient rays as well — the only way to make a glow
  contribute nothing.

If you believed `0` meant "no light", a glow-lit scene's brightness looks
inexplicable. It is the ambient path.

## Code Examples

Floor polygon (material `grey_floor_paint` defined in a `.mat`):
```
grey_floor_paint polygon floor_main
0
0
12          # 4 vertices × 3 coords = 12 real arguments
  0 0 0     # Vertex 1
  5 0 0     # Vertex 2
  5 5 0     # Vertex 3
  0 5 0     # Vertex 4
```
- **What it demonstrates**: the grammar — the two zeros are string- and
  integer-argument counts, and `12` must match the coordinates that follow.

Diffuse white paint:
```
void plastic white_wall_paint
0
0
5  0.8 0.8 0.8  0 0
# Red_refl Green_refl Blue_refl Specularity Roughness
# Specularity: 0 (purely diffuse) → 1 (highly specular)
# Roughness:   0 (perfectly smooth) → higher = rougher
```

Window glass, from a datasheet `Tn`:
```
void glass window_pane_material
0
0
3  0.96 0.96 0.96
# Red/Green/Blue transmissivity. Optional 4th arg = refractive index (default 1.52).
```

Inward-facing room shell:
```bash
genbox material_name box_name x_dim y_dim z_dim -i > mybox.rad
# -i flips normals inward — correct for a room enclosure
```

## Worked Example — datasheet to Radiance glazing

A supplier reports visible transmittance **Tn = 0.88**. Radiance's `glass`
needs transmissivity:

```
tn = (sqrt(0.8402528435 + 0.0072522239·Tn²) − 0.9166530661) / (0.0036261119·Tn)
```

(Source: *Basic tutorial — Radiance*, p. 10; *Radiance Cookbook*, p. 20.)

For `Tn = 0.88` this gives **tn ≈ 0.96**, which is what goes in the primitive —
note it is *higher* than the transmittance, because transmissivity excludes the
surface reflection losses that Radiance models separately. Writing `0.88` into
the primitive instead would quietly cost ~8% of transmitted light everywhere.

## Anti-patterns
- **Modelling a window pane as two parallel surfaces with `glass`** — `glass`
  assumes a single thin surface; doubling it double-counts reflection losses.
- **Outward-facing normals on a room enclosure** — light leaks or surfaces go
  black; use `genbox -i`.
- **Entering catalogue `Tn` as transmissivity** — see the worked example.
- **Reaching for `mirror` casually** — virtual light source calculations can
  dominate runtime.
- **Mixed units across files** — nothing errors; the geometry is just wrong.

## Key Takeaways
1. One grammar covers geometry and materials; the standalone `0` is the
   integer-argument count and is always present.
2. Materials bind to surfaces by *name*, so definition order at `oconv` time
   matters.
3. `glass` takes transmissivity, not transmittance — always convert.
4. Room enclosures need inward normals (`genbox -i`).
5. Split large scenes into `walls.rad`, `windows.rad`, `furniture.rad`; keep
   materials in dedicated `.mat` files.

## Connects To
- **Ch 3**: `oconv` argument order depends on materials preceding geometry.
- **Ch 2**: `glow` and `source` primitives are how the sky becomes geometry.
- **Ch 5**: file-organisation conventions for `materials/` and `geometry/`.
