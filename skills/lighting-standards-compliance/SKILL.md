---
name: lighting-standards-compliance
description: "Compliance requirements distilled from EN 12464-1/-2, EN 17037, IES LM-83-12, EN 13363 and EN 15193. Use when deciding which metric and threshold a daylight or electric-lighting study must be judged against, and under what reporting conditions — sDA, ASE, daylight factor, daylight provision, DGP, UGR, GR, illuminance uniformity, calculation-grid spacing and reference planes, maintenance factors, analysis periods, pass/fail bands. Answers 'what does the standard require', not 'how do I make the simulation produce it' — for the Radiance commands that compute these numbers, use radiance-simulation-workflow instead."
---

<!-- argument-hint: [metric, standard number, or chapter e.g. ch03] -->

# Lighting & daylight standards — compliance requirements

**Sources**: EN 12464-1/-2, EN 17037, IES LM-83-12 and ANSI/IES LM-83-22, EN 13363-1/-2,
EN 15193 and associated published guides, cited throughout by document number and clause.
Thresholds, grid rules and calculation protocols only — **no standard text is reproduced beyond
short cited quotations**. The standards themselves are copyrighted and are not distributed with
this skill; obtain them from CEN, your national standards body or the IES.

## How to Use This Skill

- **Without arguments** — the decision rules below
- **With a metric** — ask about `sDA`, `ASE`, `DGP`, `UGR`, `daylight provision`; I read the chapter
- **With a standard** — ask for `EN 17037`, `LM-83`, `EN 12464-1`
- **Start here for a decision** — [cheatsheet.md](cheatsheet.md)

---

## Core rules

### Pick the right metric — they are not interchangeable

| Question | Metric | Standard |
|---|---|---|
| Enough daylight? (EU) | daylight provision E_T / E_TM | EN 17037 5.1 |
| Enough daylight? (US) | sDA_300/50% | LM-83 |
| Too much sun? | ASE_1000,250h | LM-83 |
| Daylight glare? | **DGP** | EN 17037 5.4 |
| Electric glare, indoor? | **UGR** | EN 12464-1 |
| Electric glare, outdoor? | **GR** | EN 12464-2 |
| Task lighting adequate? | E_m, U_o per zone | EN 12464-1 |

**UGR must never be substituted for DGP** — EN 17037 5.4.2 forbids it. sDA and EN 17037 daylight
provision both use "300 lux, 50 % of the time" and are still **not convertible**: different analysis
periods, blind assumptions and spatial fractions.

### The grid formula depends on the standard

```
EN 12464-1 / -2 :  p = 0,2 × 5^log10(d)
EN 17037 B.2    :  p = 0,5 × 5^log10(d)
IES LM-83       :  p ≤ 0.6 m fixed
```

Same shape, different coefficient — swapping them produces a plausible grid of the wrong density and
no error.

For the two formula-based standards: `p ≤ 10 m` and cell ratio 0,5–2. **Points at cell centres is an
EN 12464 rule**; LM-83 instead fixes spacing at ≤ 0.6 m with a 12–24 in wall offset and has no 10 m
cap.

**How `d` is chosen differs even between the two EN 12464 parts:**

| Standard | `d` |
|---|---|
| EN 12464-1 | **longer dimension, unconditionally** |
| EN 12464-2 (4.3.3) | longer if long:short < 2, else **shorter** |
| EN 17037 (B.2) | longer; if ratio ≥ 2, **shorter** |

On a 2 m × 10 m band that is `p = 1.0 m` indoors versus `p = 0,32 m` outdoors — same formula, same
coefficient, 3× apart.

Both EN standards **exclude a 0,5 m perimeter band** from the calculation area — EN 12464-1 unless
task areas extend into it, EN 17037 unless otherwise specified. Omitting it shifts E_avg, U_o and
F_plane in every room.

### Reference plane heights differ

EN 17037: **0.85 m**. LM-83: **0.8 m / 30 in**. EN 12464-1: the task plane, which may be vertical or
inclined.

### ASE and sDA are two different runs

ASE is **pre-blind and zero-bounce** (`-ab 0`, direct beam only). sDA is **fully inter-reflected
with blinds operating hourly**. The ASE run produces the blind schedule the sDA run consumes, so
they are sequential. Computing ASE with blinds down understates ASE; computing it with `-ab > 0`
inflates ASE instead — the two mistakes move the number in opposite directions.

"Pre-blind" means **operable** blinds only — **fixed shading stays in the ASE model**. LM-83's
thresholds are also calibrated for **37°–48° N**; outside that band the standard asks you to say so.

### E_m is maintained, simulation output is initial

Multiply by the maintenance factor before comparing to any EN 12464-1 requirement, and report the MF
with its assumptions. MF is typically 0.67–0.80 — enough to flip a pass into a fail.

### Say what kind of value you are reporting

- **EN 17037 Annex A is informative** — "meets the medium recommendation level", not "passes". And
  check for a **national annex**; the UK NA materially relaxes dwellings.
- **LM-83-12 is superseded** by ANSI/IES LM-83-22; the rad-mcp server defaults to LM-83-12 and takes `lm83_edition="LM-83-22"` (ch04a). State the edition and its latitude band (LM-83-12's 37°–48° N; LM-83-22's 35°–55°). rad-mcp also computes ASE_net, applies the 5 % default blind on request and reads automated-group schedules (ch04a).
- **EN 12464-1**: this skill draws on the **2002 normative text** plus 2011 presentation and guide
  material. 2002 uniformity is **global** (task 0,7 / surrounding 0,5, Table 1); 2011 relaxed the
  surrounding to **0,40** and moved task uniformity into a per-activity column that is **not**
  summarised here.
  Do not fill that gap with 0,7 — see [ch01](chapters/ch01-en-12464-1-indoor.md).
- **EN 13363 is not a daylight input.** Clause 6 of Part 1 forbids using its `τ_v` for daylight
  distribution in rooms; neither part is angular.
- **EN 15193**: the normative text (EN 15193-1:2017) is **not summarised here** — only a Technical
  Report about it and a 2006 draft of its predecessor.
- **Defaults are not measurements.** LM-83's 20/50/70 reflectances, 10 % ground, 5 % shade VLT are
  substitutions and must be labelled as such.

### Threshold summary

| | Minimum | Medium | High |
|---|---|---|---|
| EN 17037 daylight E_T / E_TM | 300 / 100 lx | 500 / 300 lx | 750 / 500 lx |
| EN 17037 sunlight exposure | 1,5 h | 3,0 h | 4,0 h |
| EN 17037 DGP (≤ 5 % of time) | 0,45 | 0,40 | 0,35 |

| | Threshold |
|---|---|
| sDA_300/50% preferred / accepted | ≥ 75 % / ≥ 55 % |
| ASE_1000,250h acceptable / neutral / unsatisfactory | < 3 % / < 7 % / > 10 % (per space) |
| UGR_L office & classroom / drawing / corridor | 19 / 16 / 28 |
| EN 12464-1 surrounding for 500 lx task | 300 lx, `U_o ≥ 0,40` (2011) |
| EN 12464-2 surrounding for 500 lx task | 100 lx, `U_o ≥ 0,10`, band ≥ 2 m |

---

## Chapter Index

| # | Standard | Key content |
|---|---|---|
| [ch01](chapters/ch01-en-12464-1-indoor.md) | EN 12464-1 | zones, E_m/U_o, grid `0,2`, maintenance factor, UGR |
| [ch02](chapters/ch02-en-12464-2-outdoor.md) | EN 12464-2 | outdoor grid, GR, obtrusive light, upward light ratio |
| [ch03](chapters/ch03-en-17037-daylight.md) | EN 17037 | provision, view out, sunlight exposure, DGP, grid `0,5` |
| [ch04](chapters/ch04-ies-lm-83-sda-ase.md) | IES LM-83-12 | sDA, ASE, blind protocol, model defaults, Radiance params; LM-83-22 differences: [ch04a](chapters/ch04a-ansi-ies-lm-83-22-differences.md) |
| [ch05](chapters/ch05-en-13363-solar-shading.md) | EN 13363-1/-2 | shading + glazing `g` and `τ_v`, why BSDF beats both |
| [ch06](chapters/ch06-adjacent-energy-and-comfort.md) | EN 15193, prEN 15251 | LENI, maintenance curves — index only |

## Topic Index

- **ASE** → ch04 · **background area** → ch01 · **blind operation** → ch04 · **BSDF** → ch04, ch05
- **calculation grid** → ch01, ch02, ch03, ch04 · **curfew** → ch02
- **cylindrical illuminance / modelling index** → ch01 · **daylight hours** → ch03
- **daylight factor / D_T** → ch03 · **daylight provision** → ch03 · **DGP** → ch03
- **dirt / glazing maintenance** → ch03, ch04 · **F_D (daylight dependency)** → ch06
- **GR** → ch02 · **glare (daylight)** → ch03 · **glare (electric)** → ch01, ch02
- **latitude limits** → ch04 · **LENI** → ch06 · **maintenance factor** → ch01, ch02, ch06
- **national annexes** → ch03 · **obtrusive light / TI** → ch02 · **perimeter exclusion** → ch01, ch03
- **reporting requirements** → ch04 · **sky model** → ch03, ch04 · **usage time (glare)** → ch03
- **obtrusive light** → ch02 · **openness factor** → ch04
- **Perez sky / Reinhart patches** → ch04 · **reference plane** → ch03, ch04
- **reflectance defaults** → ch04 · **sDA** → ch04 · **sunlight exposure** → ch03
- **task / surrounding area** → ch01 · **uniformity U_o** → ch01, ch02
- **UGR** → ch01 · **view out** → ch03 · **window group** → ch04 · **τ_v vs g** → ch05

## Supporting Files

- [cheatsheet.md](cheatsheet.md) — decision tables, thresholds, failure tells, edition warnings
- [patterns.md](patterns.md) — grid construction, sDA/ASE sequencing, MF application, DGP protocol
- [glossary.md](glossary.md) — terms with chapter references

---

## Scope & Limits

**This skill owns the obligation, not the computation.** It says which metric
applies, which threshold it is judged against, how the grid is built and what the
reporting conditions are. It deliberately does **not** say how to make Radiance
produce the number.

For that, use the sibling skill **`radiance-simulation-workflow`** (versioned
alongside this one in `skills/`):

| You need | Go to |
|---|---|
| Which metric / threshold / grid / period applies | **here** |
| `gensky` calibration for a DF study | radiance-simulation-workflow ch06 |
| Computing sDA / UDI (three-phase) | ch09, ch11 |
| Computing ASE (direct-sun-only run; 5PM for CFS) | ch10, ch11 |
| DGP: fisheye render + `evalglare` | ch07 |
| Ambient parameters, `-n`, `.amb` staleness | ch02 |
| A result that looks wrong | its troubleshooting.md |

Reporting a compliance result normally needs both: the threshold from here, the
number from there.

Summarised from the sources named above. **Per-application tables are not reproduced** —
EN 12464-1's full `E_m`/`UGR_L`/`R_a`/`U_o` table by activity, EN 12464-2 clause 5 and its
obtrusive-light limit values, and EN 17037 Tables A.3/A.4 beyond the worked examples in ch03. For a
specific activity or city, consult the standard.

**Referenced but not summarised here**, so unverifiable from this skill: EN 15193-1:2017 (the
normative lighting-energy text), EN 410 (which defines `g` and `τ_v`), ISO 15099, CIE 97 / 150 / 154,
and LM-83's supporting research (Heschong, CEC-500-2012-053).

**Easy misreadings worth checking** in any report built on these standards:

- **EN 17037 Annex B is *informative***. Its internal wording says *shall*, but the annex does not
  bind. Report as "in accordance with the recommendations of Annex B", not "as required by EN 17037".
- In EN 12464-1, the requirement to calculate average illuminance and uniformity sits in **6.1
  Verification procedures**, not 4.3.1 — and it is the reference to Table 1 in that sentence that
  binds uniformity.
- **EN 17037 Table D.1 `γ_s,min` is conditioned** on 21 March *and* on the 1,5 h minimum level; it is
  not a free-standing per-latitude constant. Table D.2 uses a different basis (3°, 1 February).
- EN 12464-1 room-surface illuminances (walls above 50 lx, ceilings above 30 lx) are **floors**, not
  targets; the `U_o ≥ 0,10` attaches to the raised 75/50 tier.
- Cylindrical `Ē_z`: 50 lx is a *must*, the 150 lx raised level is a *should* — different modal force.
- The prEN 15251 daylight table has **four classes**, including Low and None, and warns against
  D_SNA above 10 % because of overheating.
- Grid arithmetic: `0,2 × 5^0,301` = **0,32 m**.

Editions cited here may be superseded — see the edition warnings above. This skill
supports simulation and reporting decisions; it is not a substitute for the standards in a
contractual or certification context.
