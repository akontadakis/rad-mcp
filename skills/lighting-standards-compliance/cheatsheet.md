# Cheatsheet — decisions, thresholds, traps

## The trap that costs the most: two grid formulas

Same shape, different coefficient. Neither errors — you just get the wrong density.

| Standard | Formula | Cap | `d` selection |
|---|---|---|---|
| **EN 12464-1** | `p = 0,2 × 5^log10(d)` | `p ≤ 10 m` | **longer dimension, unconditionally** — no flip |
| **EN 12464-2** 4.3.3 | `p = 0,2 × 5^log10(d)` | `p ≤ 10 m` (stated) | longer dimension if long:short **< 2**, else shorter |
| **EN 17037** B.2 | `p = 0,5 × 5^log10(d)` | `p ≤ 10 m` | longer dimension; if ratio **≥ 2**, use shorter |
| **IES LM-83** | fixed **≤ 0.6 m** (24 in) on centre | — | 12–24 in offset from walls |

Cell length:width between **0,5 and 2** in all three. EN 12464 puts points at **cell centres**.

**Perimeter exclusion** — both EN standards drop a band along the walls, and forgetting it shifts
E_avg, U_o and F_plane: **EN 12464-1** 0,5 m (unless task areas reach into it); **EN 17037** 0,5 m
(B.2). LM-83 instead offsets points 12–24 in from walls.

**The three rows differ on `d`, and this is the trap inside the trap.** EN 12464-1 takes the longer
dimension whatever the proportions; **-2 and 17037 flip to the shorter** past their ratio tests. On a
2 m × 10 m band, EN 12464-1 gives `p = 1.0 m` and EN 12464-2 gives `p = 0,32 m` — same formula, same
coefficient, 3× apart.

The "next whole number" and cell-centre rules for EN 12464-1 come from the **licht.de/ZVEI guides**,
not the standard text (the 2011 presentation deck says simply `d/p`). They refine rather than
contradict. A finer grid is never non-compliant — state which convention you applied.

## Which metric answers which question

| Question | Metric | Standard |
|---|---|---|
| Enough daylight? (EU) | daylight provision, E_T over F_plane for F_time | EN 17037 5.1 |
| Enough daylight? (US) | sDA_300/50% | LM-83 |
| Too much sun? | ASE_1000,250h | LM-83 |
| Daylight glare? | **DGP** | EN 17037 5.4 |
| Electric-light glare, indoor? | **UGR** | EN 12464-1 |
| Electric-light glare, outdoor? | **GR** | EN 12464-2 |
| Enough task light? | E_m, U_o per zone | EN 12464-1 |
| Energy? | LENI | EN 15193 |

**Never substitute UGR for DGP** — EN 17037 5.4.2 forbids it explicitly. **Never report GR indoors
or UGR outdoors.**

## Threshold quick reference

**EN 17037 — minimum / medium / high**

| Axis | Min | Medium | High |
|---|---|---|---|
| Daylight provision E_T (50 % plane, 50 % time) | 300 lx | 500 lx | 750 lx |
| …minimum target E_TM (95 % of plane) | 100 lx | 300 lx | 500 lx |
| Sunlight exposure (clear day, 1 Feb–21 Mar) | 1,5 h | 3,0 h | 4,0 h |
| DGP, exceeded ≤ 5 % of usage time | 0,45 | 0,40 | 0,35 |
| View: horizontal sight angle | ≥ 14° | ≥ 28° | ≥ 54° |
| View: outside distance | ≥ 6 m | ≥ 20 m | ≥ 50 m |

Horizontal openings: target at **95 %** of the plane, no minimum-target column.

**LM-83**

| Metric | Value |
|---|---|
| sDA_300/50% preferred | ≥ 75 % |
| sDA_300/50% nominally accepted | ≥ 55 % |
| ASE_1000,250h clearly acceptable | < 3 % |
| ASE_1000,250h neutral | < 7 % |
| ASE_1000,250h unsatisfactory | > 10 % |
| Analysis period | 08:00–18:00 local clock time, *"accounting for daylight savings time and longitude adjustments"* (§2.2.1), 10 h/day, 3 650 h/yr, solar position on the half hour. rad-mcp applies daylight saving only when `weather(action="load", clock_zone=...)` names the site's IANA zone; otherwise the window is in the EPW's local standard time and an info advisory says so |
| Blinds close when | > 2 % of points in direct sun |
| Calibrated latitude band | **37°–48° N** — outside it, report the caveat |

ASE bands apply **per space**, not per floor plate. The ASE run keeps **fixed** shading (overhangs,
light shelves) and only opens *operable* blinds.

**EN 12464-1 zones**

| Task E (lx) | Surrounding E (lx) |
|---|---|
| ≥ 750 | 500 |
| 500 | 300 |
| 300 | 200 |
| ≤ 200 | E_task |

**Uniformity by edition** — the surrounding value changed:

| Zone | 2002 | 2011 |
|---|---|---|
| Task | 0,7 — **global**, Table 1 | per-activity (clause 5.3 tables, not summarised here) |
| Immediate surrounding | 0,5 | **0,40** |
| Background (≥ 3 m) | — | 0,10 |

2002's clause 5 tables have **no `U_o` column** — every activity inherits 0,7 / 0,5 from Table 1.
2011 added that column, so there is no single 2011 task figure to quote. Don't fill the gap with
0,7.

Room surfaces (2011): ceiling E_m 30 lx, walls 50 lx, both U_o ≥ 0,10 — **raised to 50 / 75 lx in
offices, classrooms, hospitals, corridors, stairwells**. Plus `Ē_z` ≥ 50 lx (≥ 150 lx where visual
communication matters) and modelling index `Ē_z/Ē_h` 0,30–0,60.

Beware: licht.de also quotes 0,6 / 0,5 — those are **ASR A3.4**, a stricter German national rule,
not EN values.

## Simulation setup decisions

| If you are computing… | Then… | Because |
|---|---|---|
| **ASE** | `-ab 0`, direct beam only, **operable blinds open, fixed shading kept** | ASE is defined pre-blind and zero-bounce; overhangs are not blinds |
| **sDA** | full inter-reflection, **blinds operating hourly** | sDA models a realistic operated space |
| **DGP** | high ambient quality, 180° fisheye, worst-case position | background luminance drives DGP; a bad `-ab` gives a confident wrong number |
| **EN 12464-1 E_m** | multiply simulated initial illuminance by **MF** | requirements are maintained, output is initial |
| **EN 17037 Method 1** | pick D_T for **that city**, from **A.3 or A.4** | D_T is climate-specific, and the two tables differ ~1,8× |
| **EN 17037 Method 1 sky** | ISO 15469:2004 overcast, TYPE 1 or 16 | B.3.2 — Annex B is **informative**: recommended, not mandatory |
| **EN 17037 Method 2** | time step ≤ 1 h, dynamic shading | B.3.1 / B.3.3 — **informative** annex |
| **complex fenestration** | BSDF (genBSDF/WINDOW6); ISO 15099 if you need a standards route | EN 13363 **forbids** using its τ_v for daylight distribution, and both parts are normal-incidence |

LM-83 §2.3.3 prints `-ab 6`, `-ad 1000`, `-dt 0` (3-phase daylight-coefficient model) as the
settings its supporting research used — a **documented floor, not a mandate**. §2.3.3 instead
requires parameters high enough for reliable results, ideally justified by sensitivity studies.

Do not treat those numbers as a target: matrix generation typically needs far more (`-ab 10–12` for
a View matrix). For what to actually run, see **radiance-simulation-workflow** ch02 (presets) and
ch09/ch10 (matrix parameters).

## Tells — something is wrong

| Symptom | Likely cause |
|---|---|
| `cannot open scene file "x.rad"` from rtrace/rpict/getbbox | non-frozen octree stores **relative** paths; the tool ran from a different cwd than `oconv`. Use `oconv -f`, or match the cwd |
| Octree builds fine but the scene is empty (`getinfo -d` → `0 0 0 0`) | `RAYPATH` missing `.` → relative `!xform` includes failed. `oconv` **warns but exits 0** |
| `cannot find function file "rayinit.cal"` | `RAYPATH` unset entirely |
| `getbbox` says `unknown type "…"` on a valid octree | same cwd problem — it is failing to re-read sources, not diagnosing the octree |
| ASE implausibly low | blinds were deployed |
| ASE implausibly high | `-ab > 0`, or read off a total-illuminance `.ill` |
| sDA implausibly high | blinds never closing; check the 2 % trigger and window grouping |
| DGP ≈ 0.2 on an obviously glary scene | evalglare's "vertical illuminance < 380 lux" notice was dropped |
| Uniformity fails everywhere | U_o computed over the whole room instead of per zone |
| Grid 2.5× too coarse or fine | 0,2 vs 0,5 coefficient swapped |
| Grid ~3× off on a narrow band or corridor | `d` rule crossed between standards — EN 12464-1 never flips to the shorter dimension, -2 and 17037 do |
| Surrounding area fails a 2011 job by a hair | 2002's 0,5 applied where 2011 says **0,40** |
| E_avg slightly high, U_o slightly low | perimeter 0,5 m band not excluded from the grid |
| Northern-city DF target looks generous | Table **A.4** values read as Table A.3 — Reykjavik is 2,6 %, not 2,1 % |
| Annual daylight hours ≈ 4 500 | "daylight hours" implemented as sun-up hours, not the top 4 380 |
| Sunlight exposure passes on low winter sun | latitude `γ_s,min` gate not applied |
| DGP exceedance denominator disputed | usage time other than 08:00–18:00 Mon–Fri, results not comparable |

## Defaults that must be labelled as defaults

Never report a substituted default as a measured value.

| Quantity | LM-83 default |
|---|---|
| Floor / walls / ceiling / furniture reflectance | 20 % / 50 % / 70 % / 50 % |
| Ground reflectance | 10 % |
| Exterior vertical surfaces | 30 % |
| Trees | 20 % |
| Shade VLT, unknown | 5 % diffuse, no specular |

## Edition warnings

- **LM-83-12** is superseded by **ANSI/IES LM-83-22**; the rad-mcp
  server defaults to LM-83-12; `lm83_edition="LM-83-22"` selects the newer (ch04a). State the edition. -12 is calibrated for **37°–48° N**. It also computes ASE_net, applies the 5 % default blind on request and reads automated schedules (ch04a).
- **EN 12464-1** material here straddles **2002** and **2011**; a 2021 edition exists and is not
  covered. The 2011 *task* uniformities are per-activity and not summarised here; the 2011
  *surrounding* value (0,40) is.
- **EN 17037 Annex A is informative.** Its numbers are recommendations, not pass/fail. Check for a
  **national annex** — the UK NA materially relaxes dwellings (bedroom 100 lx, living 150, kitchen
  200) and waives the 95 % minimum-target rule there.
- **EN 13363-1 clause 6 forbids** using its `τ_v` for daylight distribution in rooms. Neither part
  is angular; ISO 15099 is the escalation.
- **There is no "EN 15193-2".** The document often cited that way is **CEN/TR 15193-2:2017**, a
  non-normative report explaining **EN 15193-1:2017** (not summarised here). The older
  **prEN 15193:2006** is
  unpartitioned and a decade older — different symbol set.
- **prEN 15251** is an unfinished committee draft, superseded in service by EN 16798-1.
