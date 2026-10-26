# Patterns — compliance workflows

## Build a compliant calculation grid

**When**: any illuminance or daylight-factor study that will be reported against a standard.

**How**
1. Identify the reference surface and its long/short dimensions.
2. Pick the formula by standard — `0,2` for EN 12464-1/-2, `0,5` for EN 17037, fixed 0.6 m for
   LM-83.
3. Pick `d` — **and the three standards do not agree**:
   - **EN 12464-1**: the **longer** dimension, unconditionally. No ratio test.
   - **EN 12464-2** (4.3.3): longer if long:short **< 2**, otherwise **shorter**.
   - **EN 17037** (B.2): longer, but if the ratio is **≥ 2**, use the **shorter**.

   Getting this wrong on a narrow band — a surrounding-area ribbon, a corridor — is a 3× grid-density
   error with no warning. See [ch01](chapters/ch01-en-12464-1-indoor.md).
4. `n = ` next whole number ≥ `d / p`. Derive the other dimension's count from the resulting
   spacing so cells stay near-square (ratio 0,5–2).
5. Place points at **cell centres** (EN 12464). Offset 12–24 in from walls (LM-83).
6. **Exclude the perimeter band**: 0,5 m in both EN 12464-1 (unless task areas reach into it) and
   EN 17037 B.2. This changes E_avg, U_o and F_plane — it is not a rounding detail.
7. Cap at `p ≤ 10 m`.

**Trade-offs**: a finer grid is never non-compliant, only slower — but a *coarser* one silently
changes the reported average and uniformity. When unsure, go finer and say so.

## Non-rectangular and ribbon-shaped surfaces

**When**: L/T/U-shaped plans, or the surrounding-area band around a task.

**How**
- **Polygon**: size the grid from a circumscribing rectangle, then compute mean and uniformity from
  **only the points inside the real polygon**.
- **Ribbon**: size from the ribbon's **widest** point, then clamp so the grid is no coarser than
  **half the narrowest dimension** where that is ≥ 0.5 m.

**Trade-offs**: the circumscribing rectangle covers area that is outside the room. Points there are
not just wrong, they are systematically bright — they see unobstructed sky. Clip before averaging.

## sDA and ASE as one study, two runs

**When**: LM-83 reporting. Both metrics are required together.

**How**
1. One geometry, one grid (≤ 0.6 m), one analysis period (08:00–18:00, 3 650 h), solar positions on
   the **half hour**.
2. **ASE run**: operable blinds open, **fixed shading retained** (overhangs, light shelves), direct
   beam only, zero-bounce. No sky luminance or ground reflectance needed. Count points ≥ 1 000 lx (LM-83-12 §2.2.6: *"1,000 lux or more"*)
   for > 250 h.
3. Derive the **blind schedule**: per window group, per hour, blinds close when > 2 % of points see
   direct sunlight (with the small-area point-count corrections below 1 000 sf). Close *view* groups
   before *clerestory* groups.
4. **sDA run**: full inter-reflection, blinds following that schedule. Count points ≥ 300 lx for
   ≥ 1 825 h.
5. Report both, per space, plus the percentage of gross floor area excluded, the window groupings,
   true solar north, and any deviation from standard methodology.
6. Outside **37°–48° N**, note the latitude caveat — LM-83 asks for it.

**Trade-offs**: step 2's output feeds step 3, so they cannot run in parallel. The ASE run is cheap
(`-ab 0`); the sDA run is not. Do not aggregate the ASE bands over a floor plate — the standard says
that interpretation is not established.

## EN 17037 Method 1 vs Method 2

**When**: assessing daylight provision.

**How**
- **Method 2** (climate-based illuminance) is the general route: hourly climate data, time step
  **≤ 1 h**, moveable shading modelled dynamically, compared against E_T / E_TM.
- **Method 1** (daylight factor) needs the target D_T for **the specific city** — Athens 1,5 % vs
  Reykjavik **2,6 %** for the same 300 lx. Pick the right table: **A.3** for vertical/inclined
  openings (diffuse illuminance), **A.4** for horizontal openings with diffusing material (global
  illuminance). They differ by up to ~1,8× for one city.
- Both routes require **validated software**; they are alternatives, not a cross-check.

**Trade-offs**: Method 1 is far cheaper and defensible for a CEN capital, but a daylight factor is a
CIE-overcast quantity — specifically the **ISO 15469:2004 standard overcast sky, TYPE 1 or 16**. It
cannot represent orientation, and two façades with identical DF can have very different annual
behaviour. Prefer Method 2 wherever orientation or shading matters.

**Daylight hours are defined, and not as sun-up hours**: rank-order the 8 760 hourly diffuse
horizontal illuminances and keep the top **4 380**. Half of that — the F_time = 50 % criterion — is
**2 190 h**.

## Applying the maintenance factor

**When**: any EN 12464-1 illuminance comparison.

**How**
1. Simulate initial illuminance.
2. Determine MF from LLMF, LSF, LMF, RSMF for the actual lamp type, operating hours and cleaning
   interval. Indoors the reference is **CIE 97**; **outdoors it is CIE 154:2003** (EN 12464-2, 4.9).
   For LED, the legacy lamp tables do not apply — use EN 62722-2-1 `(Lx, By/Cy/Fy)` data.
3. `E_maintained = E_initial × MF`. Compare **that** to `E_m`.
4. Report the MF and its assumptions alongside the result, plus the maintenance schedule.

A defensible anchor: licht.de recommends **MF = 0.67** for typical commercial interiors, from a
worked LMF 0.79 × LLMF 0.90 × LSF 1.00 × RMF 0.94. (The guide writes `RMF`, room maintenance factor;
the 2011 deck writes `RSMF` for the same term.)

**Trade-offs**: a design that passes on initial illuminance and fails on maintained is the single
most common false pass. MF is typically 0.67–0.80 — large enough to flip a result.

## DGP that means something

**When**: EN 17037 glare protection.

**How** — the render and `evalglare` invocation belong to
**radiance-simulation-workflow ch07** (fisheye view type *and* `-vh 180 -vv 180`,
High-Fidelity ambient settings, the `-c` check image, `-t`/`-b` thresholding).
What this standard adds on top:

1. Annual: count the fraction of usage time above threshold; must be ≤ 5 %. **Usage time is defined**
   as 08:00–18:00 Mon–Fri (EN 17037 E.3.1) — results on any other basis are not comparable.
2. Assess the **worst-case position**, usually close to the façade — not a room-average.
3. One render is never a verdict: the criterion is temporal, so you need DGP across many timesteps.

**Where DGP does not apply**: horizontal openings, and any position where vertical illuminance is a
poor glare predictor — deep or dark spaces with small openings, sports halls, shop vending areas, or
positions far from the openings (E.2 NOTE 2).

**Trade-offs**: a low-quality ambient calculation produces a confident, plausible, wrong DGP. This
is the metric where cheap parameters do the most damage.

## Complex fenestration optical data

**When**: blinds, louvres, redirecting glazing.

**How**, in order of preference:
1. Measured or `genBSDF`-generated BSDF, or WINDOW6 XML.
2. LM-83 fallback split (specular = openness factor, diffuse = VLT − openness) — label it a default.
3. ISO 15099 if you need angular data from a standards route.

**Not an option: EN 13363.** Clause 6 of EN 13363-1 states its `τ_v` *"shall not be used for
calculation of daylight distribution in rooms."* Both parts are normal-incidence — Part 2 is
spectral and layered, but no more angular than Part 1. Use EN 13363 to understand a datasheet, not
to feed a daylight model. See [ch05](chapters/ch05-en-13363-solar-shading.md).

**Trade-offs**: option 2 discards angular behaviour, which is precisely what a shading device exists
to control. Under direct sun — the hours that dominate ASE and DGP — the error is largest.
