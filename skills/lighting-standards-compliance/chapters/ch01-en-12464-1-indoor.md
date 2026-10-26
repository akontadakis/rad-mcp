# EN 12464-1 — Lighting of indoor work places

**Sources**: an EN 12464-1:2011 presentation deck (Peter Thorns / ZLD), the prEN 12464-1:2002
draft text, the licht.de guide to DIN EN 12464-1 and the ZVEI guide to EN 12464-1 (application
guides), cited by clause or page.

**Edition warning**: these sources straddle the **2002** and **2011** editions, and they differ on
uniformity and on whether a background area exists. A newer edition (2021) is *not* covered here.
Always state which edition a value came from.

## Core Idea

Performance is specified per task, not per room: a table of `E_m` / `UGR_L` / `U_o` / `R_a` keyed by
activity. Three concentric zones — **task area**, **immediate surrounding area**, **background
area** — each with its own illuminance and uniformity floor.

## The three zones

| Zone | Extent |
|---|---|
| Task area | where the visual task is performed; may be horizontal, vertical or inclined |
| Immediate surrounding area | band around the task area, **≥ 0.5 m wide** |
| Background area | band beyond the surrounding, **≥ 3 m wide**, within the limits of the space *(2011 onward)* |

## Illuminance relationship and uniformity

**2002 edition** — read directly from `prEN_12464-1` (the file is EN 12464-1:2002 (E), an ÖNORM
reprint). **Table 1, p.7**, titled *"Uniformities and relationship of illuminances of immediate
surrounding areas to task area"* — illuminances and uniformities are in the **same** table:

| Task illuminance (lx) | Immediate surrounding (lx) |
|---|---|
| ≥ 750 | 500 |
| 500 | 300 |
| 300 | 200 |
| ≤ 200 | E_task |
| **Uniformity ≥ 0,7** | **Uniformity ≥ 0,5** |

Clause **4.3.3** carries the obligation: light the task area as uniformly as possible, and keep
the uniformity of the task area and of the immediate surrounding at or above the Table 1 values.

**In 2002 uniformity is global, not per-activity.** The clause 5 application tables in this edition
have four columns — `Ēm`, `UGR_L`, `R_a`, Remarks — and **no `U_o` column**. Every activity inherits
0,7 / 0,5 from Table 1. That is the structural change 2011 made: it moved task uniformity *into* the
per-activity tables (licht.de p.4), which is why a single 2011 task figure does not exist.

Also normative here: **in continuously occupied areas `Ē_m` shall be not less than 200 lx** (4.3.1).

Separately, under **6.1 Verification procedures** — not 4.3.1 — *"the average illuminance and
uniformity shall be calculated and shall be not less than the values given in clause 5
**and Table 1 respectively**."* The closing words matter: **clause 5 binds illuminance, Table 1
binds uniformity**. Quoting the sentence without them loses what `U_o` is actually measured against.

**What these sources can and cannot tell you about 2011.** None of them is the full 2011
normative text. The 2011 source is a presentation deck (Peter Thorns / ZLD), and the only
normative-style text drawn on is the **2002 draft**. The licht.de and ZVEI guides
describe the 2011 edition at second hand but quote its values directly. So:

- The **2002** task/surrounding uniformities above are sourced.
- The 2011 **background area** (≥ 3 m, `U_o ≥ 0,10`) and the room-surface minima below are sourced
  from the deck.
- The 2011 **immediate surrounding** uniformity **is** sourced — it is **`U_o ≥ 0,40`**, relaxed
  from the 2002 value of 0,5. The licht.de guide (p.8) explains that the clause 5.3
  tables give `U_o` for task, activity and interior areas, while immediate surrounding and
  background areas get fixed values of 0.40 and 0.10.
- The 2011 **task-area** uniformity is **not a single value** and is not established here: 2011 moved
  it into a per-activity column of the clause 5.3 tables — a column the 2002 tables do not have. Go
  to the standard for a given activity. **Do not substitute 2002's global 0,7**, which was written
  for a table structure that no longer exists.

**2011 uniformity summary**

| Zone | 2002 | 2011 |
|---|---|---|
| Task area | `U_o ≥ 0,7` — **global**, Table 1 | per-activity, clause 5.3 tables — **not summarised here** |
| Immediate surrounding | `U_o ≥ 0,5` | **`U_o ≥ 0,40`** |
| Background | — (zone does not exist) | `U_o ≥ 0,10` |

Sourced: 2002 column from `prEN_12464-1` Table 1 / 4.3.3 directly; 2011 surrounding and background
from licht.de p.8; 2011 task from neither.

`U_o ≥ 0,40` therefore appears in **three** distinct places, and reading it as belonging to only one
of them is the classic error:

1. The 2011 **immediate surrounding area** (above).
2. **Corridors and circulation** — the reference-surface uniformity (licht.de p.14: *"DIN EN 12464-1
   stipulates 100 lx with 0.40 uniformity"*).
3. The **unknown task area** case in the deck — where the task area is not known, the whole area is
   lit uniformly (U_o ≥ 0,40) to a designer-chosen illuminance, and the scheme is redesigned once
   the task area is known. Here it is *not* a task-area uniformity.

**Do not confuse these with ASR A3.4.** The licht.de guide also quotes 0,6 (work station) and 0,5
(surrounding) — those are the **German national workplace rule**, which the guide explicitly
contrasts as *stricter* than DIN EN 12464-1. They are not EN values.

The 2011 deck also gives room-surface minima:

| Surface | Reflectance ρ | Baseline maintained illuminance | Raised tier | U_o |
|---|---|---|---|---|
| Ceiling | 0.7 – 0.9 | **over** 30 lx | **50 lx** | ≥ 0.10 (raised tier) |
| Walls | 0.5 – 0.8 | **significantly higher than** 50 lx | **75 lx** | ≥ 0.10 (raised tier) |
| Floor | 0.2 – 0.4 | — | — | — |

**These are floors, not targets.** licht.de p.19 asks for wall illuminance *"significantly higher
than 50 lx"* and ceiling illuminance above 30 lx — so hitting exactly 50/30 does not satisfy the
guidance. The `U_o ≥ 0,10` is attached in the source to the **raised** 75/50 tier (the sentence
requiring uniformity above 0.10 follows the raised-tier sentence), not to the baseline row.

The **raised tier applies to offices, classrooms, hospitals, corridors and stairwells** — i.e. most
of what gets simulated. licht.de p.19 recommends raising maintained illuminance to 75 lx on walls
and 50 lx on ceilings for these spaces. Do not report the 30/50 baseline for an office.

2002 reflectance ranges differ and are wider: ceiling **0,6–0,9**, walls **0,3–0,8**, working plane
**0,2–0,6**, floor **0,1–0,5** (prEN 4.2, p.6). 2011 drops the working-plane range and adds major
objects (furniture, machinery) at **0,2–0,7**. These are Radiance material inputs — state the
edition you took them from.

`U_o = E_min / E_avg` over the zone in question — computed **per zone**, never over the whole room.
Note the rounding convention: 2011 quotes `U_o` to two decimals deliberately, so "0.50" means
0.495–0.504, not 0.45–0.54 (licht.de p.8).

## Cylindrical illuminance and modelling — 2011 additions, directly simulatable

Both are headline 2011 additions (licht.de p.4) and both fall straight out of a Radiance run, so
there is no reason to omit them from a compliance report.

**Mean cylindrical illuminance `Ē_z`** (6.1): **≥ 50 lx** — the guide words this as *"must be no
lower than 50 lx"*. Where good visual communication is crucial (offices, meeting rooms, classrooms)
it *"**should** be raised to 150 lx"* — a recommendation, not the same modal force as the 50 lx
floor, so report the two differently. *"In both cases, uniformity is required to be higher than
0,10."* Evaluated at
**1.2 m** above floor for seated occupants and **1.6 m** for standing, in activity and interior
areas. It is the measure for how faces are lit.

**Modelling index `Ē_z / Ē_h`** (6.2): ratio of cylindrical to horizontal illuminance at a point,
normally 1.2 m above floor. **0.30 – 0.60** indicates good modelling — faces neither harshly shaded
nor flat. (DIN 5035 calls this "shadow effect" with 0.3 a minimum.)

## Calculation grid — `p = 0,2 × 5^log10(d)`

```
p = 0,2 × 5^log10(d)
```

- `p` = maximum grid cell size (m); `d` = **the longer dimension of the reference surface** (m),
  unconditionally. There is no aspect-ratio flip in EN 12464-1.
- Number of points = **next whole number** of `d / p`.
- Cells are roughly square, with calculation points **at cell centres** — not at the corners, and
  not on the boundary.
- Average illuminance = arithmetic mean of the points.

**Why `d` is unconditional here, unlike the other two standards.** The only 2011-labelled source
used here — the deck — states it plainly: `d` is *"the longer dimension of the area"*, and *"the
number of grid points is then d/p"*. No ratio test. The 0.5–2.0 flip circulating in practice comes
from the **licht.de guide Appendix 3, p.36** and **ZVEI Appendix 2, p.21**, presented there as an experience-based upper limit on grid size —
industry guidance, not standard text.

The two give different answers: on a 2 m × 10 m surrounding band, longer-dimension gives
`p = 0,2 × 5^1 = 1.0 m`; the guides' flip to the shorter gives `p = 0,2 × 5^0,301 = 0,32 m`, a 3×
finer grid. **This chapter uses the longer dimension.** A finer grid is never non-compliant, so if
you follow the guides instead, that is defensible — just say which you applied.

**The neighbouring standards do have the flip, in their own normative text** — EN 12464-2 clause
4.3.3 (`< 2` → longer, else shorter) and EN 17037 B.2 (`≥ 2` → shorter). Do not carry EN 12464-1's
unconditional rule across to them, or theirs back to it.

The "next whole number" rounding, cell centres, and the polygon/ribbon rules below also come from
the guides rather than the deck (which says simply `d/p`). They refine rather than contradict, so
this chapter keeps them.

**The 0.5 m marginal strip — excluded from the calculation area.** licht.de p.17: a 0.5 m strip along
the walls is left out of the calculation area unless task areas lie in it or reach into it. Worked through at pp.10–13 and p.18. Two counter-cases:

- If a surface extending into the strip is **work space** (a desktop), its points **must** be
  included; if it is movement space, ignore it.
- Where work areas may legitimately reach the room boundaries, the whole room counts as the work
  area and no marginal strip is deducted (p.13).

This changes `E_avg` and `U_o` in nearly every room. A grid built without it is not comparable to a
compliance figure.

**Non-rectangular surfaces**: use a circumscribing rectangle to set the grid, then compute mean and
uniformity from **only the points falling inside the actual polygon**.

**Ribbon-shaped surfaces** (the usual shape of a surrounding-area band): size the grid from the
ribbon's **widest** point, but the resulting grid must be **no greater than half the ribbon's
narrowest dimension** where that is ≥ 0.5 m.

**Further practice constraints** (licht.de p.17, ZVEI p.11): the luminaire arrangement and the
measurement-point arrangement **should not be identical**; point spacing must be **less than the
mounting height**; in high bays, beams should overlap at height, not only on the reference surface.

**Contrast with EN 17037**, which uses the same formula shape with coefficient **0,5** and a 10 m
cap. Mixing them up is silent — you get a plausible grid of the wrong density. See
[ch03](ch03-en-17037-daylight.md).

## Maintenance factor

Requirements are **maintained** illuminance `E_m`, not initial. The design must state the MF and its
underlying LLMF / LSF / LMF / RSMF assumptions and the associated maintenance schedule. Simulation
output is initial illuminance: **multiply by MF before comparing to `E_m`**, and report the MF used.

## UGR

`UGR_L` is a **limit per task**, from the application tables. Representative maxima (licht.de guide):

| Activity | UGR_L |
|---|---|
| Technical drawing | 16 |
| Reading, writing, classrooms, computer work, inspection | 19 |
| Industry and craft workshops, reception | 22 |
| Rough work, staircases | 25 |
| Corridors | 28 |

The tabular UGR from a catalogue is the **reference-room** value `UGR_R` (4H × 8H, spacing-to-height
0.25). It characterises the luminaire, not the installation. Position-specific UGR must be computed
for the actual geometry and observer positions.

UGR is defined for **electric** installations. EN 17037 5.4.2 states it cannot be used to rate
discomfort glare from vertical or inclined daylight openings.

## Anti-patterns

- **Comparing initial simulated illuminance to `E_m`.** `E_m` is maintained; apply the MF first.
- **Using the EN 17037 grid coefficient (0,5).** This standard uses **0,2**.
- **Computing U_o over the whole room.** It is per zone.
- **Reporting the catalogue `UGR_R`** as the installation's glare rating.
- **Mixing editions, or inventing what 2011 says.** The sources here are 2002 normative text plus
  2011 presentation and guide material. They **do** establish the 2011 surrounding uniformity (0,40);
  they do **not** establish the 2011 per-activity task uniformities. State the edition, and go to the
  standard for anything not sourced here.
- **Applying the 2002 surrounding uniformity (0,5) to a 2011 assessment.** It was relaxed to 0,40 —
  installations that comply will be failed.
- **Applying 2002's global task 0,7 to a 2011 assessment.** 2002 had one value for every activity
  because its tables had no `U_o` column; 2011 has one per activity. Look the activity up.
- **Reading `U_o ≥ 0,40` as belonging to one case only.** It is the 2011 surrounding-area value, the
  corridor reference-surface value, *and* the unknown-task-area value. Only the third is not a
  zone uniformity.
- **Quoting ASR A3.4's 0,6 / 0,5 as EN values.** They are a stricter German national workplace rule.
- **Grid points at cell corners.** They belong at cell centres.
- **Applying an aspect-ratio flip to an EN 12464-1 grid.** `d` is the longer dimension whatever the
  proportions. The flip belongs to EN 12464-2 and EN 17037.
- **Gridding the full room without excluding the 0.5 m marginal strip** — unless task areas reach
  into it, in which case those points stay.
- **Reporting the 30 lx / 50 lx room-surface baseline for an office or classroom.** The raised tier
  (50 / 75 lx) applies there.
- **Omitting `Ē_z` and the modelling index** from a 2011 report. The 50 lx `Ē_z` floor is a
  requirement; the 150 lx raised level and the modelling-index band are recommendations. Both are
  free from a simulation you have already run.

## Key Takeaways

1. Three zones with their own illuminance and uniformity floors; ≥ 0.5 m surrounding, ≥ 3 m
   background.
2. Grid `p = 0,2 × 5^log10(d)`, points at cell centres, count = next whole number of d/p.
3. `d` is the **longer** dimension, unconditionally — no aspect-ratio flip. EN 12464-2 and EN 17037
   *do* have one; do not carry it back here.
4. `E_m` is maintained — MF is part of the result, not a footnote.
5. `UGR_L` is per task and position-dependent; catalogue values are reference-room only.
6. Room surfaces have their own minima (ceiling 30 lx, walls 50 lx, both U_o ≥ 0.10) in 2011.

## Connects To

- **[ch03](ch03-en-17037-daylight.md)** — the other grid formula; UGR vs DGP boundary.
- **[ch02](ch02-en-12464-2-outdoor.md)** — same framework applied outdoors.
