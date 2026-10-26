# IES LM-83-12 — sDA and ASE

**Source**: IES LM-83-12, cited by section and page. The supporting research it rests on (Heschong,
*Daylight Metrics*, CEC-500-2012-053) is **not summarised here**.

**Note**: superseded by ANSI/IES LM-83-22 (differences in ch04a). Values below are
the -12 edition; rad-mcp defaults to LM-83-12 and takes `lm83_edition="LM-83-22"` (ch04a). Flag the edition whenever reporting.

> ### Latitude limitation — LM-83 requires you to report it
>
> The supporting research was conducted between **37°–48° N latitude in North America**
> (§2.2.1 DISCUSSION, p.3). The criteria *"may be applicable beyond this range; however it is
> recommended that the caveat regarding this uncertainty is noted in the evaluation report."*
>
> This bites hardest in exactly the context this skill serves: most of Europe north of Lyon is
> outside the calibration band. Applying sDA/ASE thresholds to Stockholm or Reykjavik without the
> caveat is a reporting omission the standard itself calls for.

## Core Idea

Two metrics, always reported **together**: sDA says whether there is enough daylight, ASE says
whether there is too much sun. They use the same geometry and the same analysis period but
**different simulation setups** — sDA runs with operable blinds, ASE runs with none.

## sDA_300/50%

Percent of analysis area meeting **300 lux for ≥ 50 % of the analysis period**.

| Criterion | sDA_300/50% |
|---|---|
| Preferred | ≥ 75 % |
| Nominally accepted | ≥ 55 % |

- **Illuminance threshold**: 300 lux, horizontal workplane at 30 in / 0.8 m above finished floor.
- **Analysis period**: fixed 10 h/day, **08:00–18:00 local clock time**, *"accounting for daylight savings time and longitude adjustments"* (§2.2.1 p.3), = 3 650 h/yr. 50 % of that
  is **1 825 h**. Calculation is **on the half hour** — TMY hourly data is a band average, so "hour
  9" covers 08:00–09:00 and should use the solar position for **08:30** (Glossary p.12, §2.2.1). The rad-mcp server applies daylight saving only when `weather(action="load", clock_zone=...)` names the site's IANA zone; otherwise the window is placed in the EPW's local standard time and an info advisory says so.
- **Analysis area** = regularly occupied areas. Excludable: very low occupancy (elevator lobbies,
  circulation, janitor/storage closets, copy rooms, data closets) and substantially different visual
  tasks (retail/museum display, manufacturing). Whole-building reports **must state the percent of
  gross building area excluded** (§2.2.4, §2.4).
- **sDA is built on DA** — the per-point Daylight Autonomy of Reinhart & Walkenhorst; sDA is the
  percent of area meeting it (§2.1.1).
- **Analysis points**: continuous, spacing **≤ 24 in (0.6 m) on centre**, offset 12–24 in from
  walls, height 30 in. Grids preferably centred on the space.
- Points whose **yearly maximum is < 1 lux** and which are adjacent to higher-valued points may be
  dropped, if they sit inside an opaque body (furniture, closet).

## ASE_1000,250h

Percent of analysis area receiving **≥ 1 000 lux of direct sunlight for > 250 h/yr**, computed
**before any operable blinds or shades are deployed**. Same 10 h/day period as sDA, maximum **2 ft
(0.6 m)** point spacing. Direct sunlight is *"1,000 lux or more"* (§2.2.6 p.5, §3.2.2 p.11, Glossary p.12); §3.1.2 p.10 words it *"more than 1000 lux"*, but the hours are strictly *"more than 250 hours"*.

"Direct sunlight" = direct beam from the solar orb only, after glazing transmittance — **zero-bounce,
no diffuse sky, no inter-reflection** (in Radiance: `-ab 0`). §3.3 p.12: ASE *"does not require
modeling of sky luminance or ground reflectance."*

**"No blinds" means no *operable* blinds — fixed shading stays.** §3.2.6 p.12: the ASE analysis
deploys no blinds or shades, so every operable one stays open, while fixed or static shading
(overhangs, light shelves) belongs in the ASE model. Stripping
overhangs from the ASE run is a real and common mis-implementation.

| ASE_1000,250h | Judgement |
|---|---|
| < 3 % | clearly acceptable |
| < 7 % | neutral / nominally acceptable |
| > 10 % | unsatisfactory visual comfort |

These bands are printed **in LM-83-12 itself**, §3.1.3 DISCUSSION p.10, reporting what the supporting
research judged — not an outside source.

**They do not scale to a floor plate.** The same clause says the interpretation of ASE over larger
areas — a whole floor plate or a whole building — is not yet understood. §3.2.4 adds that the
research spaces averaged 1 200 ft² and were rarely more than 40 ft from a window group, so ASE per
whole-building floor area has no interpretive basis yet. Report ASE per space.

ASE is a **relative, worst-case** indicator — smaller is better, and it is a check on sDA, not a
standalone metric. §2.4 p.10: annual summary values **shall** be reported for both sDA and ASE for
each identified analysis area (§3.1.3 phrases it as "should also report ASE").

## Blinds / shades operation (2.2.6) — the part that is easy to get wrong

All exterior windows are modelled **with interior blinds or shades** unless *both*: they are excluded
by the design documents, **and** the ASE for that window group already meets "nominally acceptable".

Blinds close, per window group, per hour, whenever more than **2 % of analysis points** receive
direct sunlight (1 000 lux or more of direct beam, §2.2.6 p.5). Small-area corrections, because a 2 ft grid under-samples small rooms — the 2 %
figure is the general rule and the three bands below are its stated exceptions, not a fourth tier:

| Analysis area | Blinds close when direct sunlight reaches |
|---|---|
| ≥ 1 000 sf | > 2 % of points *(the general rule)* |
| 500–999 sf | > 5 points |
| 200–499 sf | > 3 points |
| < 200 sf | > 1 point |

**Closing order** (§2.2.6 DISCUSSION p.6): *"it is advisable to close the blinds for the 'view'
window groups first and 'clerestory' window groups last."*

Analysis areas for blind scheduling are grouped by façade orientation and never exceed 10 000 sf;
grids must span the whole space. Blinds for multiple window groups may close in any combination
until the criterion is met, evaluated **for each hour of the year**.

A **window group** = coplanar windows with similar shadow patterns and similar shading type and
operation. Windows within **15° of the same azimuth (±7.5°)** may be treated as one group.

## Blind and shade optical properties (2.2.7)

Prefer **BSDF** (WINDOW6 output) or geometric louvre model.

Fallbacks when BSDF is unavailable:

| Case | Specular T | Diffuse T |
|---|---|---|
| Fabric shade, openness factor known | = openness factor | VLT − openness |
| Fabric shade, VLT known, openness unknown | 0 | = VLT |
| Shade VLT unknown | 0 | 5 % |
| White louvre blind (> 80 % reflectance) | — | 20 % VLT |
| Louvre 60 % reflectance | — | 17.5 % VLT |
| Louvre 40 % reflectance | — | 15 % VLT |
| Black louvre (0 % reflectance) | — | 10 % VLT (lower limit) |

Blind slat angle / shade position is set to block direct sun from the **lowest solar angle** for that
façade orientation in that calendar month.

## Default model assumptions

**Interior reflectances** (when unknown): floor 20 %, walls 50 %, ceiling 70 %, furniture 50 %.

**Exterior** (2.2.8): model buildings and opaque structures within **100 ft**, at ≥ 10 ft
resolution. Unknown vertical surface reflectance **30 %**; ground **10 %**; trees as cones/spheres/
cylinders at **20 %** reflective. If obstructions are unknown, assume equal-height buildings on
adjoining parcels with a setback equal to the study building.

**Furniture** (2.2.11): anything extending ≥ 36 in above the floor is modelled to within 6 in.

**Sky and sun** (2.3.1–2.3.2): TMY hourly data, **Perez skies**, minimum **2 305 Reinhart sky
patches**. Simulated solar position within **5°** of actual.

**Radiance parameters** — printed in LM-83-12 §2.3.3 p.9–10 as the settings the supporting research
used with the Radiance three-phase daylight-coefficient method:

| Parameter | Value |
|---|---|
| ambient bounces `-ab` | 6 |
| ambient divisions `-ad` | 1000 |
| direct threshold `-dt` | 0 |

**This is not a required parameter set.** §2.3.3 instead requires parameters high enough to give
reliable results, ideally justified by sensitivity studies, and software able to model the space's
geometry and daylighting technologies accurately. Treat the table as a documented floor, not a mandate.

**Dirt depreciation is mandatory and quantified** (§2.2.9 p.8): **5 %** on vertical windows,
**10 %** on sloped glazing 85–20° from vertical, **15 %** on skylights below 20° slope; plus 10 % on
horizontal exterior surfaces (§2.2.8).

**Window framing** (§2.2.9 p.8): details > 2 in must be modelled; otherwise apply a **20 %**
reduction for windows and **10 %** for skylights relative to the rough opening. Unknown recesses or
jambs: **6 in** depth at **50 %** reflectance.

**Dynamic glazing and motorised blinds** (§2.2.6 p.5) are handled separately from the 2 % protocol:
manufacturers' blind-control protocol may be used for motorised blinds, and electrochromic glazing
follows its hourly VLT control protocol per design specification. Temperature-driven protocols must
be imported from thermal analysis. rad-mcp models such a group once the caller marks it automated=True (left out of ASE_net; hourly schedule via bind_schedule; dynamic glazing's states are its clear and tinted BSDFs) (ch04a).

**Large adjacent surfaces** (§2.2.10 p.8): surfaces > 10 ft in any direction whose reflectances
differ by > 20 % must be modelled separately — a whiteboard at 85 % against a 50 % wall, carpet at
20 % against a 45 % hard floor.

## Anti-patterns

- **Computing ASE with blinds deployed.** ASE is explicitly a pre-blind, worst-case figure.
- **Computing ASE with inter-reflection.** It is zero-bounce direct beam only; any `-ab > 0`
  inflates it.
- **Reporting sDA without ASE.** The standard requires both.
- **Using a grid coarser than 0.6 m**, or applying the flat 2 % blind trigger to a small room where
  the point-count corrections apply.
- **Treating sDA as interchangeable with EN 17037 daylight provision.** Both use 300 lux and 50 %
  of time, which invites the confusion — but the analysis periods, blind assumptions and spatial
  fractions all differ. They are not convertible.
- **Reporting -12 thresholds as current.** ANSI/IES LM-83-22 supersedes this edition; see ch04a.
- **Stripping fixed shading from the ASE run.** Only *operable* blinds come out; overhangs and light
  shelves stay.
- **Applying the ASE bands to a floor plate or whole building.** The standard says that
  interpretation is not yet established.
- **Omitting the 37°–48° N caveat** outside that band — LM-83 asks for it explicitly.
- **Treating `-ab 6 / -ad 1000 / -dt 0` as the required parameters.** They are what the research
  used; the requirement is sensitivity-justified adequacy.
- **Skipping dirt depreciation and frame reduction.** Both are specified and both cut delivered
  illuminance.

## Reporting requirements (§2.4)

Not optional, and easy to forget:

- Annual summary sDA **and** ASE per identified analysis area.
- Geometric plots showing **true solar north** and the analysis areas.
- **Window groupings must be indicated.**
- Excluded areas and the resulting percent of gross building area.
- *"The justification for any variation from standard modeling methodology… must be noted."*

## Key Takeaways

1. sDA ≥ 75 % preferred, ≥ 55 % nominally accepted; 300 lux, 50 % of a fixed 08:00–18:00 year.
2. ASE_1000,250h is pre-blind and zero-bounce; < 3 % good, > 10 % unsatisfactory.
3. Grid ≤ 0.6 m, workplane 0.8 m, 12–24 in wall offset.
4. Blinds close at > 2 % of points in direct sun, with point-count corrections below 1 000 sf.
5. Window groups are ±7.5° of azimuth.
6. Defaults matter and must be reported as defaults: 20/50/70/50 interior, 10 % ground, 30 %
   exterior vertical.

## Connects To

- **[ch03](ch03-en-17037-daylight.md)** — the European answer to the same question, not convertible.
- **[ch05](ch05-en-13363-solar-shading.md)** — where declared shading-device optical data comes from.

**To actually compute these metrics** — the sibling `radiance-simulation-workflow` skill:
- **ch11** — sDA/ASE/UDI reduction from an annual `.ill` (the two-stage threshold pattern).
- **ch09** — three-phase method, adequate for sDA and UDI.
- **ch10** — five-phase, the accurate ASE route for complex fenestration.
- **ch08** — sky matrix generation, and the `-m 4` mapping for the 2 305-patch
  minimum this chapter requires; also the half-hour `.wea` timestamp convention.
- **ch02** — ambient parameters (note this chapter's §2.3.3 numbers are a floor, not a target).
