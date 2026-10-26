# EN 13363-1 / -2 — Solar protection devices combined with glazing

**Sources**: EN 13363-1:2003+A1:2007 (simplified method) and EN 13363-2:2005 (detailed
calculation method; Part 1 clause 5 also calls it the *reference method*), cited by clause and page.
**EN 410**, which actually defines `g` and `τ_v`, and **ISO 15099**, the angular-capable
escalation, are both referenced by these standards but are **not summarised here**.

## Core Idea

These standards define how the combined optical performance of **shading device + glazing** is
declared: total solar energy transmittance `g` and light transmittance `τ_v`. They are the reason a
manufacturer's shading datasheet has the numbers it has.

> ### The prohibition that governs this whole chapter
>
> **EN 13363-1, clause 6, p. 9** — immediately after the `τ_v,t` equation (Eq. 4), the standard
> notes that the equation ignores both angular dependence and spectral differences, and then
> rules: *"**It shall not be used for calculation of daylight distribution in rooms.**"*
>
> That is a normative `shall not`, aimed exactly at what a Radiance daylight model does. The
> declared `τ_v` is for **energy and solar-gain declaration**, not for daylight distribution. Do not
> feed it to a daylight simulation — not as a primary input, and not as a "cross-check" of a BSDF.

**Part 1 is simplified, Part 2 is detailed — but both are normal-incidence.** Neither supports
angular behaviour. This is the most commonly assumed-otherwise fact about these standards.

## Part 1 — simplified method

Estimates the combined `g` from the glazing's thermal transmittance and `g`, plus the shading
device's light transmittance and reflectance (Scope, p. 3).

**Applicability limits — two gates, not one:**
- Valid only when the **glazing's `g` is between 0,15 and 0,85**.
- **And** when the device itself satisfies **`0 ≤ τ_e,B ≤ 0,5`** and **`0,1 ≤ ρ_e,B ≤ 0,8`**
  (Scope, p. 3). Outside these, Part 2 applies — a highly reflective device (ρ_e,B = 0,9)
  is out of scope even with perfectly compliant glazing.
- Based on **normal incidence** only.
- Results are explicitly **approximate**: the Scope (p. 3) puts the deviation from the detailed
  method between +0,10 and −0,02.
- Venetian blinds are assumed **adjusted so there is no direct solar penetration**; a 45°-open
  correction is given (4.2, p. 6).
- Outputs are position-dependent: clause 5 `g_t`, clause 6 `τ_v,t`, clause 7 solar direct
  transmittance `τ_e,t`. For **integrated** (between-pane) devices, clauses 6 and 7 both defer to
  EN 410 — Part 1 gives you no `τ_v` there at all.
- Part 1 says when to abandon Part 1: for internal blinds with solar-protection glass, Eq. (2)
  gives very conservative values and Part 2 is recommended (5.2 NOTE, p. 8).

**Neither part defines `g` or `τ_v`.** Part 1 clause 3 delegates to **EN 410:1998** — the actual
defining document, which is not summarised here.

## Part 2 — detailed method

Spectral, layered, iterative — and still **normal incidence**. EN 13363-2:2005, Scope, p. 4:

> The method is *"based on a normal incidence of radiation and does not take into account an
> angular dependence of transmittance or reflectance"*. Diffuse irradiation, including radiation
> scattered by the shading device, is handled as though it were direct. For cases beyond its
> scope the standard points to **ISO 15099**, which has a wider range.

What it actually reports (**Table 1, 6.2.2, p. 15**): the total solar energy transmittance **`g`**
(to two decimals) and its parts (to three). `g` is the directly transmitted solar fraction `τ_e`
plus the secondary internal heat gain `q_i`, and `q_i` is in turn the sum of three factors — the
long-wave (thermal radiation) term `g_th`, the convective term `g_c` and the ventilation term `g_v`.

`τ_v` is **not a required output**. Clause 5.2 (p. 8) notes that light transmittance and light
reflectance can be computed the same way, but the two parts are not symmetric `g`/`τ_v`
producers, and Part 2's reporting table is a thermal one.

**Spectral bands**: solar/light **0,3–2,5 µm**; thermal radiation **3–100 µm** (opaque layers
usually handled 5–50 µm via corrected emissivity). Per-wavelength recursion integrated to broadband
per EN 410. Without spectral data the standard allows the calculation on integrated (broadband)
values, at reduced accuracy.

**Layer model** (5.1, p. 6) — the real difference from Part 1: n solid layers separated by gas
spaces, one-dimensional transport, no upper limit on the layer count, solved **iteratively**
because temperature and heat transport interact non-linearly. Part 1, by contrast, fits three fixed
configurations with notional parameters.

**Annex A (normative) — louvres and venetian blinds.** The closest thing here to angular treatment:
view-factor formulation with **separate direct and diffuse** system properties, and a global-
radiation mixing rule `τ = 0,85·τ_D + 0,15·τ_d` (A.6, p. 17). Its NOTE matters for blind modelling:
for louvres and venetian blinds especially, the diffuse-radiation solar transmittance can exceed the
direct-radiation one. Annex A still assumes the blind is
adjusted to eliminate direct beam and that the material is diffuse (A.1, p. 16).

## Why this sits in a Radiance skill

Radiance wants a **BSDF** for a complex fenestration system (`genBSDF`, or WINDOW6 XML). EN 13363
gives you neither — and clause 6 forbids using its `τ_v` for daylight distribution anyway. The
practical consequences:

- **EN 13363 is not a daylight-model input.** Its role in a Radiance workflow is to tell you what a
  manufacturer's datasheet numbers *mean* and where they came from, not to supply them to the model.
- A declared `τ_v` is a **normal-incidence scalar**. Using it as a Radiance `trans` transmissivity
  throws away all angular behaviour, which is exactly what a venetian blind or louvre is *for*.
- If a scalar is genuinely all you have, LM-83's fallback table
  ([ch04](ch04-ies-lm-83-sda-ase.md)) is the simulation-ready route, because it splits specular from
  diffuse and was written for this purpose. Label it a default.
- For angular behaviour the escalation path the standard itself names is **ISO 15099**, not
  EN 13363-2. Better still, measure or generate a BSDF.

## Anti-patterns

- **Using an EN 13363 `τ_v` to compute daylight in a room.** EN 13363-1 clause 6 says it *shall not*
  be used for this. This is the chapter's headline rule.
- **Reaching for Part 2 because you need angular data.** Part 2 is normal-incidence too. It is
  spectral and layered, not angular. ISO 15099 is the wider-scope document.
- **Checking only the glazing `g` range.** The device gates `0 ≤ τ_e,B ≤ 0,5` and
  `0,1 ≤ ρ_e,B ≤ 0,8` apply independently.
- **Expecting a `τ_v` for an integrated (between-pane) device from Part 1.** Clauses 6 and 7 hand
  you to EN 410.
- **Treating a normal-incidence `τ_v` as angle-independent** in an annual simulation. Direct-sun
  hours are exactly where the angular dependence dominates.
- **Confusing `g` (total solar energy transmittance, thermal) with `τ_v` (light transmittance,
  visual).** Neither belongs in a daylight model, but only `g` is a thermal quantity.

## Key Takeaways

1. **EN 13363-1 clause 6 forbids using its `τ_v` for daylight distribution in rooms.** Everything
   else in this chapter is subordinate to that.
2. Part 1 = simplified; glazing `g` in 0,15–0,85 **and** device `τ_e,B ≤ 0,5`, `ρ_e,B` in 0,1–0,8;
   deviations +0,10 / −0,02 by declaration.
3. Part 2 = detailed, spectral (0,3–2,5 µm), layered, iterative — and **still normal incidence**.
   Its reported output is thermal: `g` and its components, not `τ_v`.
4. For angular behaviour: ISO 15099, or a real BSDF. Not either part of EN 13363.
5. `g` is a thermal quantity; `τ_v` is the visual one; **EN 410** is what defines both.

## Connects To

- **[ch04](ch04-ies-lm-83-sda-ase.md)** — LM-83's specular/diffuse fallback table for blinds; the
  route to use when EN 13363 numbers are all the datasheet gives you.
- **[ch03](ch03-en-17037-daylight.md)** — shading devices are recommended for glare control (5.4.1).
