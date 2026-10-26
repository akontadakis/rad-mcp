# Troubleshooting — symptom → cause → fix

The controlling principle: **match the parameter to the symptom.** Most wasted
simulation time comes from turning the wrong dial — especially lowering `-aa` to
fix splotches, which costs hours and makes them worse.

## Rendering artifacts

**Large splotchy / blotchy patches**
`-ad` too low — the hemisphere is undersampled, so a point's average brightness
is wrong across a whole region.
→ **Double `-ad`** and re-render. **Do not lower `-aa`** to fix this; it
increases runtime drastically and sharpens the splotch edges, making them worse.
Tell: the patches are far larger than a pixel.

**Shadows smooth but with blocky or unnatural boundaries**
Poor irradiance-cache interpolation — `-aa` too high, or `-ar` too low.
→ Decrease `-aa` by ~25% and/or increase `-ar`.

**Speckled / noisy highlights on rough surfaces**
Insufficient specular sampling, or a jittered direct source under-sampled.
→ Increase `-ss` (specular sampling), or if using `-dj`, trace more rays
(lower pixel variance settings).

**Light leaks at corners or geometry edges**
Coincident surfaces or floating-point error at intersections.
→ Ensure geometry is well-constructed with no gaps. Radiance prefers opaque over
transparent at coincident locations. Forcing triangulation of OBJ input with
`robjutil +T` can help.

## Illuminance / brightness

**Scene too dark, especially far from windows**
Light isn't propagating deep enough: `-ab` too low, **or** `-lw` too high so rays
die before they get there.
→ Raise `-ab` to 5+ **and** lower `-lw` to `1e-5`/`1e-6`. These must move
together — see the interdependency in Ch 2. Do **not** substitute `-av`.

**All results zero or near-zero**
See Ch 3's two silent traps: (1) `gensky` output used without separately
authored sky/ground `source` geometry; (2) `gensky`'s date passed
non-positionally.

**Illuminance plausible but systematically wrong**
Check the `-oov` field offset (Ch 3): RGB are `$1 $2 $3` without `-oov`, but
**`$4 $5 $6`** with it (`o`=3 fields, then `v`=3). `$7 $8 $9` reads past the
end of the record and yields 0 silently.

**Daylight factor off by ~2.44×**
`-B` and `-b` values interchanged (Ch 6). `-B 55.866` (horizontal irradiance)
and `-b 22.863` (zenith radiance) describe the same 10,000 lux sky;
`-B/-b = (7/9)·π = 2.4435`.

**Materially wrong interior levels with correct geometry**
Check surface reflectances — a wall at 80% instead of an actual 50% dramatically
alters results, and nothing flags it.

## Annual / matrix methods

**Annual simulation missing direct sun contribution**
`rcontrib` defaults to **`-dj 0.9`** (verified via `rcontrib -defaults`; note
`rtrace` and `rpict` both default to 0, so the hazard is specific to `rcontrib`),
which jitters shadow rays across the solar disc and makes the direct term
inconsistent between runs.
→ Set `-dj 0` (shadow ray to solar disc centre), plus `-dt 0 -dc 1` to prevent
statistical termination of shadow rays.

**ASE results look wrong or implausible**
ASE counts **direct sunlight only** — LM-83 excludes sky and interreflections —
so the standard route is a direct-only run (`-ab 0`, sun-only sky), *not* a
filtered total-illuminance result. Five-Phase is needed only for complex
fenestration. A 3PM
result smears the solar disc across a sky patch and cannot support ASE (Ch 11).

**Matrix multiplication fails or misaligns**
Bin counts must agree across the suns file, `rcontrib -bn`, the `.cal`
subdivision, and `gendaymtx -m`. A BSDF's basis (Klems vs tensor tree) must match
what V and D were generated against.

**View matrix is near-zero**
The glow window's normal faces **out** of the room instead of in.
→ Check with `rvu -ab 2 -vf views/back.vf model_vmx.oct` — the window should
visibly emit inwards.

**Daylight matrix wrong with no error**
`genklemsamp -c N` (rays per Klems direction, default 1000) and `rcontrib -c N`
(rays averaged into one record, default **1**) must agree. Also verify
`genklemsamp -vd` is the window's **outward** normal.

**Every Klems direction misbinned**
`-b kbinS` used on a window that isn't south-facing.
→ Use the matching `kbinN`/`kbinE`/`kbinW`/`kbinD`, or the general
`kbin(nx,ny,nz,ux,uy,uz)` form.

**5PM direct term no more accurate than 3PM**
One BSDF used for all three terms. `Cds` requires the **tensor-tree** BSDF;
only terms 1–2 use the Klems basis. Also check the blind geometry was blacked in
the all-black model, and that `-ab 1` (not 0) was used for `Cds`.

**`genBSDF` output looks wrong**
Geometry wasn't transformed into the required position first — it must be
parallel to the XY plane and in the **negative Z half-space**. Use `xform`.

**Annual `dctimestep` returns a single timestep**
You passed a sky **vector** (`.skv`, one timestep) instead of a sky **matrix**
(`.smx`). `-n` does not fix this — it only declares the count for *headerless*
sky input, and a `gendaymtx` `.smx` already carries its own header.

**Swapping glazing is slow**
You're on two-phase DC, which bakes glazing into the matrix. Move to 3PM (Ch 9),
where only `T` changes.

## Glare (DGP)

**DGP results seem incorrect**
Multiple possible causes:
1. The `rpict` image was not a 180° fisheye → verify `-vth -vh 180 -vv 180`.
2. Render quality too low → background luminance is wrong; re-render at
   High-Fidelity settings.
3. Inappropriate threshold method → use `-t` to define a task area.
Always generate a check image with `-c` and inspect it.

**DGP looks implausibly low on a visibly glary scene**
Check `evalglare`'s notices. *"Vertical illuminance less than 380 lux"* means the
result is likely an **underestimate** — DGP correlates poorly in the low-light
regime. The notice is easy to scroll past; the number looks confident regardless.

**`evalglare` finds no glare sources on an obviously bright window**
The detection threshold is too high — common when the default fixed threshold
meets a very bright scene.
→ Define a task area with **`-t xpos ypos angle`** (three arguments: centre x, y
in pixels and opening angle in radians) and tune the multiplier with **`-b
factor`** (default 5). Use `-c` to see which pixels were classified as sources.

## Octree and file resolution

These bite before any physics does — the run fails, or silently traces an empty
scene.

**`cannot open scene file "x.rad"` from `rtrace`/`rpict`/`getbbox`**
A non-frozen octree stores **relative** paths to its source files, and the tool
ran from a different working directory than `oconv` did.
→ Build with **`oconv -f`** (frozen: embeds the geometry), or always run from
the same cwd as the `oconv` that produced the octree.

**Octree builds fine but the scene is empty** (`getinfo -d` reports `0 0 0 0`)
`RAYPATH` is missing `.`, so relative `!xform` includes silently failed.
**`oconv` warns but exits 0** — so a scripted `$?` check passes and the run
continues against nothing.
→ Set `RAYPATH=.:/usr/local/radiance/lib` (the leading `.` is the fix).

**`cannot find function file "rayinit.cal"`**
`RAYPATH` is unset entirely.
→ Point it at the Radiance library directory, keeping `.` first.

**`getbbox` reports `unknown type "…"` on a valid octree**
The same cwd problem — `getbbox` is failing to re-read the *source* files, not
diagnosing the octree itself.

## Diagnostic tools
| Tool | Use |
|---|---|
| `rvu` `trace` (`t`) + pick | Prints intersection, material, radiance at that point |
| `ximage` lowercase `l` | Report luminance over the picked area (not `L`) |
| `evalglare -c <img>` | Check image showing detected glare sources |
| `objview` / `objline` | Validate geometry and material assignment pre-octree |
| `-ab 0` | Isolate the direct component |
| `getinfo` | Inspect a Radiance file's header |
