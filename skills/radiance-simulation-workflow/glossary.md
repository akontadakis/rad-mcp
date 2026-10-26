# Glossary

**`-aa` (ambient accuracy)** — error tolerance for **interpolation** by the irradiance cache; lower = more accurate, significantly slower. Not a general accuracy dial — do not use it to fix splotches. `-aa 0` disables interpolation. (Ch 2)

**`-B`** — `gensky` flag setting horizontal **diffuse irradiance** (W/m²); `E/179`, so 55.866 for 10,000 lux. Under `-c` all light is diffuse. (Ch 6)

**`-b`** — `gensky` flag setting **zenith radiance** (W/m²/sr); `(9/7)E/(179π)`, so 22.863 for 10,000 lux. (Ch 2, Ch 6)

**`-dc` (direct certainty)** — `1` guarantees explicit calculation of all shadow lines above the `-dt` contrast threshold. (Ch 2, Ch 10)

**`-dj` (direct jittering)** — stochastic distribution of shadow rays across an area source; `0` gives hard shadows and is **required** for consistent `rcontrib` direct sun. (Ch 2, Ch 10)

**`-ds` (direct sampling ratio)** — adaptive subdivision of large area light sources. (Ch 2)

**`-dt` (direct thresholding)** — halts shadow testing for sources contributing below a fraction; `0` disables the shortcut. (Ch 2, Ch 10)

**`-I+`** — `rcontrib`/`rfluxmtx` flag computing irradiance contributions. (Ch 8, Ch 9)

**`-ss` (specular sampling)** — controls sampling of specular/rough reflections; raise to fix speckled highlights. (troubleshooting)

**`.amb` file** — the stored irradiance cache. (Ch 2)

**`.dsmx`** — direct sun coefficient matrix file (5PM). (Ch 10)

**`.ill`** — annual illuminance results: 8,760 rows × N sensor columns. (Ch 11)

**`.smx`** — sky matrix; per-patch luminance per timestep. (Ch 8)

**`.vmx` / `.dmx`** — view and daylight matrix files (3PM). (Ch 9)

**`.wea`** — Radiance-native weather format, converted from EPW by `epw2wea`. (Ch 8)

**ASE (Annual Sunlight Exposure)** — % of floor area receiving ≥1000 lux of **direct sunlight only** (LM-83 excludes sky and interreflections) for ≥250 occupied hours. Computed from a direct-only run (`-ab 0`, sun-only sky); 5PM for complex fenestration. (Ch 11)

**Backward ray tracing** — Radiance's strategy: trace from camera/sensor into the scene rather than forward from sources. (Ch 2)

**BSDF** — Bidirectional Scattering Distribution Function; the fenestration's optical description, stored as XML. (Ch 9)

**`Cds`** — direct sun coefficient matrix relating sun positions to sensors (5PM). (Ch 10)

**DA (Daylight Autonomy)** — per-point % of occupied hours above an illuminance threshold. (Ch 11)

**`dctimestep`** — multiplies the phase matrices across timesteps to produce results. (Ch 9)

**Daylight coefficient** — transfer relationship between one sky patch and one sensor point. (Ch 8)

**Daylight Matrix (D)** — flux transfer from sky patches to the exterior of the fenestration. (Ch 9)

**DGP (Daylight Glare Probability)** — fraction of people disturbed by daylight glare, 0–1. Thresholds 0.35/0.40/0.45. (Ch 7)

**`epw2wea`** — converts EnergyPlus EPW weather to Radiance `.wea`. (Ch 8)

**`evalglare`** — computes DGP and other glare indices from a 180° fisheye HDR. (Ch 7)

**Fisheye view** — `-vth` (hemispherical) or `-vta` (angular); mandatory for `evalglare`, with `-vh 180 -vv 180`. (Ch 7)

**`gendaymtx`** — generates annual sky matrices; `-d` direct-only, `-5 <deg>` for the 5PM direct sun matrix (**requires the solar disk size, e.g. `-5 0.533`**), `-m` subdivision factor. (Ch 8, Ch 10)

**`genBSDF`** — generates BSDF XML from a model of a fenestration device. (Ch 9)

**`genklemsamp`** — generates Klems-distributed sample rays on a window; `-c N` = rays per Klems direction. Pair it with `rcontrib -c N` so rcontrib averages exactly the batch emitted per direction (rcontrib's own `-c` defaults to 1). (Ch 9)

**`genskyvec`** — converts `gensky` output into a discretized sky vector (`.skv`). (Ch 9)

**`.skv`** — sky vector for a single timestep. (Ch 9)

**`kbinS` / `kbinN` / `kbinE` / `kbinW` / `kbinD`** — Klems binning variables selecting the window orientation in `klems_int.cal`. (Ch 9)

**`klems_int.cal`** — calculation file defining interior Klems binning; supplies `Nkbins` (145). (Ch 9)

**`reinhart.cal`** — calculation file for Reinhart sky subdivision; supplies `Nrbins`. (Ch 9, Ch 10)

**`reinsrc.cal`** — calculation file generating sun source directions for the suns file. (Ch 10)

**BSDF proxy** — a `void BSDF` surface referencing a tensor-tree XML, co-planar with the view-matrix surface. (Ch 10)

**Stencil / secondary reference geometry** — a `glow` surface used to mask a View Matrix to a specific area, producing illuminance maps. (Ch 9)

**Tregenza sky** — 145-patch sky discretization; `gendaymtx`'s default. (Ch 9)

**`viewmtxsurf` / `daymtxsurf`** — the inside-face glow surface (V) and outside-face surface (D) of the window opening. (Ch 10)

**`vwrays`** — generates camera rays for image-based matrix generation. (Ch 9)

**Irradiance cache** — adaptive sampling structure storing computed indirect values for interpolation. (Ch 2)

**Klems basis** — the standard 145-direction hemispherical BSDF subdivision. (Ch 9)

**`-lr`** — limit on total reflections; must rise alongside `-ab` for deep bounces. (Ch 2)

**Reinhart subdivision (MF)** — sky discretization, `1 + 144·MF²` patches: MF:1=145, MF:2=577, MF:4=2305, MF:6=5185. (Ch 8, Ch 10)

**`rcollate`** — reshapes/transposes matrix data between sensor-major and hour-major. (Ch 10)

**`rcontrib`** — computes per-modifier contribution coefficients; the engine of all matrix methods. (Ch 8)

**`rfluxmtx`** — wrapper around `rcontrib` that simplifies generating V and D matrices. (Ch 9)

**`rlam`** — pastes records from multiple files side by side. (Ch 10)

**`rmtxop`** — matrix operations; `-c 47.4 119.9 11.6` applies the photometric weighting (= 179 × 0.265/0.670/0.065). (Ch 9)

**sDA (Spatial Daylight Autonomy)** — % of floor area receiving ≥300 lux for ≥50% of occupied hours. (Ch 11)

**Sky patch** — one discretized region of the sky dome with its own modifier. (Ch 8)

**Tensor tree** — high-resolution BSDF representation (`-t3`/`-t4`); required for 5PM's direct term. (Ch 9, Ch 10)

**Transmission Matrix (T)** — the fenestration BSDF; the swappable component in 3PM. (Ch 9)

**UDI (Useful Daylight Illuminance)** — % of occupied hours in bins: <100, 100–500, 500–2000, ≥2000 lux. (Ch 11)

**View Matrix (V)** — flux transfer from the interior fenestration face to sensors or pixels. (Ch 9)

**`-vth` / `-vta`** — hemispherical / angular fisheye view types. (Ch 7)

**`-ab` (ambient bounces)** — max diffuse reflections traced for indirect light; 4 (Standard) to 7 (High-Fidelity). **Defaults to 0** in rtrace/rpict. Too low → dark, flat shadows. (Ch 2)

**`-ad` (ambient divisions)** — initial rays sampling the hemisphere at a point; too low → splotchy artifacts. 1024 (Standard) / 4096 (High-Fidelity). (Ch 2)

**`-ar` (ambient resolution)** — density of the cached indirect-value grid; error rises on surfaces spaced closer than `scene_size / ar`. 512 (Standard) / 1024 (High-Fidelity). (Ch 2)

**`-as` (ambient super-samples)** — extra rays sent where `-ad` found high variance; cleans residual noise. ≈ half `-ad`: 512 (Standard) / 2048 (High-Fidelity). (Ch 2)

**`-av` (ambient value)** — global fill light; set `0 0 0` for physically accurate work. Non-zero is non-physical. (Ch 2)

**`-I`** — `rtrace` flag computing irradiance at a point for a given sensor orientation; required for illuminance. (Ch 3)

**`-i`** — `rpict` flag rendering an illuminance/irradiance image instead of luminance. (Ch 4)

**`-lw` (limit weight)** — minimum ray contribution still worth tracing; 0.001–0.0001. (Ch 2)

**`-oov`** — `rtrace` output spec `ov`: Origin (3 fields) then Value (3); RGB land in fields **4, 5, 6**. Direction would need `d`. (Ch 3)

**Authored input** — hand-written, irreplaceable file: `.mat`, `.rad`, `.vf`. (Ch 5)

**Daylight Factor (DF)** — interior illuminance ÷ simultaneous exterior horizontal illuminance × 100, under an overcast sky. (Ch 3)

**Derived artifact** — regenerable output: `.oct`, `.hdr`, results files. (Ch 5)

**`falsecolor`** — converts a Radiance image's data into pseudo-colour with a legend. (Ch 4)

**`gensky`** — generates the sky luminance distribution and sun description; emits modifiers, **not** surfaces. (Ch 2, Ch 3)

**`genbox`** — generator for six-sided boxes; `-i` gives inward normals for room enclosures. (Ch 1)

**Generator** — CLI program emitting `.rad` text: `genbox`, `genprism`, `genrev`, `gensurf`, `genworm`. (Ch 1)

**`glass`** — material type for thin single-pane glazing; takes **transmissivity**. Use one surface per pane. (Ch 1)

**`glow`** — self-luminous material that doesn't strongly light the scene; 4th arg is max glow radius (`0` = none). Used for sky dome and ground. (Ch 1, Ch 3)

**`.hdr`** — Radiance picture file; linear and high dynamic range, needs gamma correction (≈2.2) for display. (Ch 4)

**`illum`** — secondary light source; redistributes light from other sources, can appear as a different material when viewed directly. (Ch 1)

**Limit weight** — see `-lw`. (Ch 2)

**`light`** — primary self-luminous material; args are R,G,B radiance in W/sr/m². (Ch 1)

**Luminous efficacy constant (179)** — lm/W factor converting Radiance's radiometric RGB to lux. (Ch 3)

**Meridian** — standard timezone meridian, `15 × UTC offset` (0 = GMT, −75 = EST). Not longitude. (Ch 2)

**`metal`** — material type whose specular highlights are coloured by the material. (Ch 1)

**`mirror`** — planar mirror material; costly due to virtual light source calculations. (Ch 1)

**Modifier** — the material identifier applied to a surface; `void` for a base material definition. Must be defined before it is referenced. (Ch 1, Ch 3)

**`normtiff`** — Radiance tool converting `.hdr` to `.tif` with tone mapping and gamma correction (default 2.2). (Ch 4)

**`objline`** — emits a line drawing of `.rad` files (metafile); pipe to `x11meta`. No materials needed. (Ch 4)

**`objview`** — interactive preview from `.rad`/`.mat` directly; builds a temp octree and adds default lighting. (Ch 4)

**`oconv`** — compiles text scene files into a binary octree. Argument order is semantic. (Ch 3)

**Octree (`.oct`)** — binary spatial data structure enabling efficient ray tracing. (Ch 3)

**`pfilt`** — filters Radiance images: anti-aliasing, resizing, exposure adjustment. (Ch 4)

**`plastic`** — most common material type; diffuse/specular with uncoloured highlights. (Ch 1)

**Primitive** — one geometry or material definition in Radiance's shared five-part grammar. (Ch 1)

**`rcalc`** — record calculator; used to convert RGB irradiance to lux and lux to DF. (Ch 3)

**`rpict`** — batch renderer producing high-quality `.hdr` images from an octree and view. (Ch 4)

**`rtrace`** — traces rays to compute values (e.g. illuminance) at specified points. (Ch 3)

**`rvu`** — interactive renderer for exploring an octree and composing views. (Ch 4)

**Scenario** — one combination of sky, geometry and material state; encode it in filenames. (Ch 5)

**Sensor record** — one line of `x y z dx dy dz`; `0 0 1` = upward horizontal sensor. (Ch 3)

**`skyfunc`** — the sky luminance-distribution pattern generated by `gensky`. (Ch 3)

**`source`** — primitive describing a distant emitter by direction vector and subtended angle (e.g. `0 0 1 180` = sky hemisphere). (Ch 3)

**Specularity** — material argument, 0 (purely diffuse) → 1 (highly specular). (Ch 1)

**Surface normal** — the front face direction; polygon vertices counter-clockwise from the front. Room enclosures point inward. (Ch 1)

**Tone mapping** — compressing HDR into displayable range (`ximage h`, `normtiff`). (Ch 4)

**`trans`** — translucent material scattering transmitted light (frosted glass, lampshades). (Ch 1)

**Transmissivity (tn)** — the internal quantity Radiance `glass` expects; ≈0.96 for Tn=0.88. (Ch 1)

**Transmittance (Tn)** — the measured quantity manufacturers publish. (Ch 1)

**View file (`.vf`)** — stored view parameters (`vp`, `vd`, `vu`, `vh`, `vv`). (Ch 4)

**`void`** — the null modifier used when defining a base material. (Ch 1)

**Working plane** — the measurement plane. Standards fix it: EN 17037 0.85 m, LM-83 0.8 m (30 in), EN 12464-1 the task plane (which may be vertical or inclined). Not a free choice in compliance work — see `lighting-standards-compliance`. (Ch 2)

**`ximage`** — X11 viewer for `.hdr`; lowercase **`l`** reports luminance over the picked area, `=` sets exposure to it. (Ch 4)

**Zenith brightness (`-b`)** — sky brightness set to produce a known exterior illuminance; 22.86 W/m²/sr for 10,000 lux. (Ch 2)

**`-af`** — ambient file: persists the irradiance cache between/across runs. **Delete it whenever parameters or geometry change** — a stale `.amb` silently mixes settings. (Ch 2)

**`-e MF:N`** — sets the Reinhart subdivision factor inside a `.cal` evaluation (`rcontrib`/`rfluxmtx`). Must match the sky matrix's `-m`. (Ch 8, Ch 9, Ch 10)

**`-faf` / `-ff` / `-ffc`** — binary format flags: `-faf` float in/ASCII out, `-ff` float both, `-ffc` float in/colour (`.hdr`) out. (Ch 9, Ch 10)

**`-n nproc`** — run on `nproc` parallel processes (`rtrace`, `rcontrib`; default 1). No benefit beyond core count; incompatible with `-P`/`-PP` and with ray-tree output. `rpict` has no `-n`. (Ch 2)

**`-ss` (specular sampling)** — two distinct uses: in rendering it controls sampling of rough specular reflections (raise to fix speckle); in the 5PM direct-sun `rcontrib` run `-ss 0` disables specular sampling for consistency. Same flag, opposite intent. (Ch 2, Ch 10, troubleshooting)

**`-st` (specular threshold)** — cutoff below which specular sampling is skipped; `-st 1` in the 5PM direct run. (Ch 10)

**`bsdf2klems`** — converts a BSDF to the Klems basis. Discouraged for 5PM: handles specular components poorly — run `genBSDF` twice instead. (Ch 10)

**`cnt`** — emits N sequential integers; used with `rcalc` to generate grids and sun lists. (Ch 3, Ch 10)

**`gengrid`** — utility that generates a sensor point grid. (Ch 6)

**`getinfo`** — prints a Radiance file's header; use it to read the ambient parameters recorded in an `.amb`, or a matrix's timestep count. (Ch 2, troubleshooting)

**`lookamb`** — inspects the binary contents of an ambient (`.amb`) file. (Ch 2)

**maxrad** — `glow`'s 4th real argument: the radius within which it is tested as a *direct* source. `0` = ambient contribution only; negative = contributes nothing. (Ch 1)

**`rlam`** — pastes records from multiple files side by side, column-wise. (Ch 10, patterns)

**`robjutil`** — OBJ utility; `+T` forces triangulation, which can cure coincident-surface light leaks. (troubleshooting)

**`rpiece`** — renders an image in pieces across processes; the `rpict` route to parallelism. (Ch 2)

**`xform`** — transforms Radiance scene files (translate `-t`, rotate `-rx/-ry/-rz`, replace material `-m`). Used to position BSDF geometry and to build all-black models. (Ch 9, Ch 10)
