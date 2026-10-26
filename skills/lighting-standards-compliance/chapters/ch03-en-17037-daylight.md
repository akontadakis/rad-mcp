# EN 17037:2018 — Daylight in buildings

**Source**: BS EN 17037:2018, cited by clause, table and annex. Values below are summarised for
orientation; read the standard itself before relying on a number in a report.
**Status of values**: Clause 5 is normative; **Annex A is informative** — its numbers are
recommendations, not requirements. Say "recommendation level" not "pass".

## Core Idea

A space is assessed on four independent axes — daylight provision, view out, sunlight exposure,
glare — each at one of three recommendation levels (**minimum / medium / high**). Minimum should be
provided. There is no single composite score; report all four separately.

## Recommendation levels at a glance (Annex A)

One matrix, our arrangement, collecting the numeric values of Tables A.1, A.2, A.5, A.6 and A.7.
"F_plane" is the share of the reference plane, "F_time" the share of daylight hours.

| Axis (Annex A table) | Minimum | Medium | High |
|---|---|---|---|
| Daylight, vertical/inclined openings (A.1): target E_T over 50 % of plane | 300 lx | 500 lx | 750 lx |
| …and minimum target E_TM over 95 % of plane (A.1) | 100 lx | 300 lx | 500 lx |
| Daylight, horizontal openings (A.2): E_T over 95 % of plane | 300 lx | 500 lx | 750 lx |
| F_time for every daylight row above | 50 % | 50 % | 50 % |
| View: horizontal sight angle (A.5) | ≥ 14° | ≥ 28° | ≥ 54° |
| View: outside distance (A.5) | ≥ 6 m | ≥ 20 m | ≥ 50 m |
| View: layers visible from ≥ 75 % of utilized area (A.5) | landscape at least | landscape + one other, same opening | sky, landscape and ground, same opening |
| Sunlight on the reference day (A.6) | 1,5 h | 3,0 h | 4,0 h |
| Glare: DGP not exceeded for more than 5 % of usage time (A.7) | 0,45 | 0,40 | 0,35 |

## Daylight provision (5.1, A.2)

A space provides adequate daylight if a **target illuminance** is met over a fraction of the
reference plane for at least half the daylight hours. The reference plane is **0.85 m above floor**
unless otherwise specified.

Vertical and inclined openings carry two criteria (a target over half the plane and a lower minimum
target over 95 % of it); horizontal openings carry a single target, applied over 95 % of the plane.
An opening counts as **horizontal** if its entire area is above the reference plane.

**Two calculation methods (5.1.3):** they are **alternatives** — 5.1.4 lets the assessor use
either one, and both must use **validated software**.
- **Method 1** — daylight factor against target D_T / D_TM from **Table A.3** (vertical and inclined
  openings) or **Table A.4** (horizontal openings with diffusing material), per CEN capital city.
- **Method 2** — climate-based illuminance from hourly climate data, time step **≤ 1 hour**, with
  any moveable shading modelled dynamically.

Method 1 is not a shortcut with the same thresholds: D_T is city-specific because it derives from
that city's median external diffuse illuminance. Athens (φ 37,90, E_v,d,med 19 400 lx) needs
**D = 1,5 %** to exceed 300 lx; Reykjavik (φ 64,13, 11 500 lx) needs **2,6 %**.

> **Two tables, and mixing them is silent.** **Table A.3** is keyed on median external **diffuse**
> illuminance `E_v,d,med` and covers **vertical and inclined** openings. **Table A.4** is keyed on
> median external **global** illuminance `E_v,g,med` and covers **only horizontal openings with
> diffusing material**. For one city they differ by more than a factor of 1,8 — Athens needs 1,5 %
> in A.3 and 0,8 % in A.4 for the same 300 lx. Confirm which table applies before reading a number
> out of it.

**Worked examples from Table A.3** (vertical/inclined openings; D needed to exceed the stated
illuminance for half the daylight hours). The table itself lists 33 CEN capitals; look up the
project's city there.

- **Athens** (φ 37,90; E_v,d,med 19 400 lx): 100 lx → 0,5 %; 300 lx → 1,5 %.
- **London** (φ 51,51; E_v,d,med 14 100 lx): 100 lx → 0,7 %; 300 lx → 2,1 %.
- **Reykjavik** (φ 64,13; E_v,d,med 11 500 lx): 100 lx → 0,9 %; 300 lx → 2,6 %.

For the **minimum** level, the 300 lx value is **D_T** (over 50 % of the plane) and the 100 lx
value is **D_TM** (over 95 %). Read the pair, not a single column.

## Calculation grid (B.2) — NOT the EN 12464-1 grid

```
p = 0,5 × 5^log10(d)      p ≤ 10 m
```

- `d` = longer dimension of the calculation area; **if the long:short ratio is ≥ 2, use the shorter
  dimension instead**.
- Points in that dimension = nearest whole number ≥ d/p. Spacing from that result then sets the
  count in the other dimension, giving near-square cells.
- Cell length:width must stay between **0.5 and 2**.
- **Exclude a 0,5 m band along the walls** from the grid area, unless otherwise specified (B.2).
  This changes the point count and the computed F_plane materially in small rooms, and it is easy to
  miss. (Annex F records a Slovak A-deviation requiring a 1 m perimeter to be *included* instead.)
- Reference plane 0.85 m.

## What the calculation assumes

> **Annex B is *informative*.** Exactly as with Annex A, these are **recommended** settings, not
> requirements you can cite as mandatory. The sentences inside Annex B are nevertheless written
> with "shall" (for the cell ratio and the maximum grid size, for instance), which is what makes
> this easy to misread: the wording is prescriptive, but the annex containing it does not bind.
>
> Report these as "calculated in accordance with the recommendations of Annex B",
> not "as required by EN 17037". Where a national annex or a client spec adopts
> Annex B by reference, it becomes binding through *that* document, not this one.

With that caveat, these are the settings a Radiance run actually needs:

- **Sky model, Method 1** (B.3.2): any reliable daylight-factor method may be used provided it
  rests on the **ISO 15469:2004 standard overcast sky, type 1 or type 16**. ISO 15469 is a
  normative reference.
- **Daylight hours** — not sun-up hours. Rank-order the 8 760 hourly diffuse horizontal illuminance
  values and take the **highest 4 380**; half the daylight hours is therefore **2 190 h** (B.3.3,
  B.4). The retained set may contain zeros and the discarded set may contain non-zeros; the standard
  says this is expected and does not affect the outcome. Implementing "daylight hours" as sun-up
  hours gives a different answer.
- **Surface reflectances** (B.3.1): ceiling **0,7–0,9**, interior walls **0,5–0,8**, floor
  **0,2–0,4**, exterior walls **0,2–0,4**, exterior ground usually **0,2**. For tested or verified
  calculations use defaults **floor 0,2 / walls 0,5 / ceiling 0,7**. Deviations are permitted with
  justification.
- **Glazing dirt** (B.3.1): the standard recommends allowing for the loss of glazing transmittance
  caused by dirt. The UK National Annex NA.3 gives a full method — base loss by building
  type and pollution (4 % rural residential to 24 % polluted), × exposure (vertical ×1, inclined ×2,
  horizontal ×3), × special factors (sheltered from rain, weathered, leaded each ×3).
- **Context**: external obstructions, glazing transmittance, wall/roof thickness, internal
  partitions, surface reflectance and furniture must all be accounted for (5.1.1, B.3.1).
- **All assumptions shall be stated** (B.3.1).

**The coefficient is 0,5 here and 0,2 in EN 12464-1.** Same formula shape, different constant —
using 12464's grid for a 17037 study gives a needlessly fine grid, and the reverse silently
under-samples. See [ch01](ch01-en-12464-1-indoor.md).

## View out (5.2, A.3)

Assessed from reference positions in the **utilized area** — the part of the space meant for
occupation (3.20). Not the room footprint, and not the Annex B calculation grid. A **view opening**
(3.23) is any part of the envelope through which a view is possible, glazed walls and doors
included, which is not the same set as the daylight openings.

A view has three layers — **sky, landscape, ground** (5.2.1).

**Clause 5.2.2 sets four criteria, and Table A.5 quantifies only three.** The fourth is the glazing:
5.2.2 asks that the view through it look clear, undistorted and colour-neutral. It has no numeric
threshold and no simulation answers it, so a tool that reports the three table rows has not assessed
view out — it has assessed the part that can be computed.

Table A.5's three criteria are horizontal sight angle, outside distance of view and the number of
layers seen (values in the matrix above).

**The 75 % qualifier belongs to the layers criterion only.** It is part of that column's own header;
the sight-angle and distance columns carry no area fraction.

Table A.5's footnote a adds a size recommendation for rooms deeper than 4 m: the view opening(s)
should together measure at least 1,0 m wide by 1,25 m high. **Annex A is informative and this is a
recommendation**, not a requirement.

**Table A.5 covers vertical, inclined AND horizontal openings** (A.3). This is the opposite of the
glare axis, where DGP explicitly does not apply to horizontal openings — do not carry the rooflight
exclusion across.

**Two of the three criteria depend on what is outside, not only on the opening.** *Outside distance
of view* (3.12) is measured from the inside face of the view opening to the major obstructions
opposite it, and an obstruction (3.11) is any external object that hides part of the sky from
direct view. The layer count is likewise read off the opposing obstruction's head and base. Only the
**horizontal sight angle** is a property of the opening and the room alone. A model with no
surrounding context cannot assess two of the three.

**Merging openings** (A.3): openings close together may be treated as a single opening.
**How close is undefined** — any tool must choose, and the choice is not sourced.

**Annex C adds the quantitative rules** the table alone does not give:

- **Eye heights** (C.4.1): **1,2 m** seated, **1,7 m** standing. Without these a layer count is not
  reproducible.
- **Reference points** (C.3): they may sit anywhere in the utilized area. So view out is **per
  point, aggregated to an area fraction** — not a single worst case, and not a fixed grid.
- **Opening width** for a *medium* rating (C.3): `b_w1 + b_w2 ≥ a/2` **and** `≥ b/2`, where `a` is
  the distance from façade to the most remote part of the utilized area and `b` the façade width
  between interior walls — i.e. total view-opening width ≥ half the room depth **and** ≥ half the
  façade width. **Stated for the medium level**; the minimum and high width criteria are read off
  Figures C.2–C.4 instead, which are not summarised here.
- **Aggregation** (C.1): the overall view-out level is the level of the weakest of the three
  criteria. Three parameters, worst one wins.
- Two verification procedures (5.2.3): the **simplified** method (C.4.1) and the **advanced** method
  (C.4.2). C.4.1 is a section-cut construction: at eye level, the no-sky line and no-ground line
  divide the floor into depth bands seeing one, two or three layers, and the area fraction is read
  off. It assumes width and distance already satisfy C.3. C.4.2 is a projection method using an
  **equidistant fish-eye** from eye point Q, reading the sight angle and layers off a γ/β grid. The
  standard recommends it where openings are complex in shape or numerous, or contain components
  such as shading devices, and allows other projections (cylindrical coordinates, for example).
- A view onto an **atrium** may substitute where view out is impossible, with opposite atrium façades
  recommended more than 20 m from the daylight opening.

## Exposure to sunlight (5.3, A.4)

Sunlight hours on a **cloudless reference day** that the assessor picks between 1 February and
21 March (A.4) — the date is chosen within that window, not fixed. Assessed at reference point P
(Annex D). The Table A.6 durations are in the matrix above.

The axis applies (5.3.1) to hospital patient rooms, nursery play rooms and at least one habitable
space in each dwelling.

**Point P** (D.2) lies in the plane of the aperture's inside face, midway across its width. Its
height is at least 1,2 m over floor level and at least 0,3 m over the sill; with no sill, it is
1,2 m over the floor.

**The assessment is per opening, not per space.** D.2 runs it for every opening of the space, and
5.3.3 verifies compliance using as many daylight openings as it takes to reach the recommended
value. Where a space has apertures in different façades, times may be **cumulated if they do not
coincide** (D.2). So the answer is a set of intervals per aperture, unioned under a
non-coincidence rule — not one number per room.

**Obstructions are part of the definition, not a refinement.** 3.19 defines sunlight exposure as
the time the sun stands above the real horizon under a cloudless sky, a horizon that permanent
obstructions such as terrain or buildings can raise.

**The acceptance angle α_a** (D.6): P counts as sunlit only when the sun reaches it within the
plan acceptance angle α_a, which is bounded at the start and end of the day by the azimuths at
which the sun is at the minimum altitude γ_s,min. This is the reveal and lining model — a deep
reveal, a loggia or a self-shading façade narrows α_a. Ignoring it treats every window as flush.

**The clock is True Solar Time.** Table D.1's first note sets the minimum criteria at True Solar
Time for each CEN capital's latitude, and excludes from the evaluation both cloud and any sun
below the tabulated altitudes. Solar azimuth runs clockwise from north, 0° to 360° (3.17, Note 1).
A tool that counts local clock hours is systematically wrong.

D.6 is permissive on method: any reliable calculation of sunlight duration is acceptable if it
assumes a cloudless sky and the room's correct orientation. Nothing here is radiometry — both
this axis and view out are visibility geometry.

**Minimum solar altitude — sunlight below it does not count** (Table D.1, **D.5**):
`γ_s,min` — Athens 20°, London 12°, Reykjavik 6°.

> **Check the conditions before reusing these.** Table D.1 gives γ_s,min for **21 March** and for
> the **1,5 h** (minimum) recommendation only, for 33 CEN capitals. It is not a free-standing
> per-latitude constant. **Table D.2 is a different quantity entirely**, not another γ_s,min table:
> for **1 February**, with γ_s,min fixed at **3°**, it gives the largest angle (in degrees from
> south) the opening normal may turn and still meet the recommendation, with **one column per
> recommendation level** (e.g. Stockholm 92 / 71 / 56). Different date, different fixed γ_s,min,
> different output, and D.2 covers all three levels while D.1 covers only 1,5 h. Both tables note
> that countries may add further cities, so neither is a closed list.

Table D.1's NOTE 2 is explicit that direct sunlight below the minimum altitude is not counted.
Omitting this over-counts sunlight hours at low sun, which is precisely when a 1 Feb–21 Mar
assessment accumulates them.

**Not summarised here**, and to be read from the standard before being relied on: Figures C.2–C.4
(the minimum and high opening-width curves), C.3's rule for spaces with openings in more than one
façade, C.3 on sills above eye level, C.2's subjective environmental-information rating, C.1's
relaxation for spaces over 2 000 m², and D.5's solar-position formulae (equation of time,
declination, hour angle).

## Protection from glare (5.4, A.5)

**DGP**, for spaces with vertical or inclined openings where occupants cannot choose their position
or view direction. **Not applicable to horizontal openings.**

DGP must not exceed the threshold for more than **F_DGP,exceed = 5 %** of usage time (thresholds
per level in the matrix above, from Table A.7).

**The denominator is defined** (E.3.1). Reference usage time `t_ref` is **08:00–18:00, Monday to
Friday, year-round** — used as the reference even where real usage differs. Alternative usage times
may be applied, but E.3.1 states that their results are not comparable with results on the default
reference usage time. You cannot compute the 5 % without fixing this.

**Assess the worst case** (E.2): investigate the expected worst-case position, usually close to the
façade.

**Where DGP does not apply** (E.2 NOTE 2) — beyond horizontal openings, the standard rules it out
wherever vertical illuminance is a poor glare predictor: vending areas of shops, sports halls, deep
or dark spaces with very small openings, and positions remote from the openings or with little
daylight.

Table E.1 gives the perception bands behind the A.7 thresholds: ≤ 0,35 not perceived; 0,35–0,40
perceived but mostly not disturbing; 0,40–0,45 often disturbing; ≥ 0,45 mostly intolerable.

**UGR (EN 12464-1) cannot be substituted for DGP.** 5.4.2 states this explicitly: ratings for
electric-lighting discomfort glare do not apply to daylight from vertical or inclined openings.

## Anti-patterns

- **Reporting Annex A values as compliance.** Annex A is informative. Clause 5 sets the criteria;
  Annex A proposes the numbers.
- **Using the EN 12464-1 grid formula.** Different coefficient — see above.
- **Substituting UGR for DGP** on a daylight study, or computing DGP for a rooflit space. Note the
  standard rules DGP *out* for horizontal openings without naming a replacement — it does not
  license UGR there.
- **Reading Table A.4 values as Table A.3.** A.4 is horizontal openings with diffusing material, on
  *global* illuminance. The two differ by up to ~1,8× for the same city.
- **Applying Method 1 daylight factors from the wrong city.** D_T is latitude- and climate-specific.
- **Ignoring the ≥ 2 aspect-ratio rule**, which flips `d` from the longer to the shorter dimension.
- **Gridding to the walls.** A 0,5 m perimeter band is excluded.
- **Implementing "daylight hours" as sun-up hours.** It is the top 4 380 of the rank-ordered hourly
  diffuse illuminances; half is 2 190 h.
- **Computing DGP exceedance against occupied hours you invented.** The reference denominator is
  08:00–18:00 Mon–Fri, and results on other usage times are not comparable.
- **Counting sunlight below the latitude's `γ_s,min`.** It does not count.

## Key Takeaways

1. Four independent axes, three levels each; minimum should be provided; report separately.
2. Reference plane 0.85 m; grid `p = 0,5 × 5^log10(d)`, p ≤ 10 m, cell ratio 0.5–2.
3. Daylight provision has both a target (50 % of plane) and a minimum target (95 % of plane) for
   vertical/inclined openings — horizontal openings only have the target, at 95 %.
4. F_time = 50 % of daylight hours in every case — and daylight hours means the top **4 380**
   rank-ordered hourly diffuse illuminances, so half is **2 190 h**.
5. Glare is DGP with a 5 %-of-usage-time exceedance allowance; 0.45 / 0.40 / 0.35. Usage time is
   defined: 08:00–18:00 Mon–Fri.
6. Sunlight exposure is a clear-sky single-day count, not an annual metric, and sunlight below the
   latitude's `γ_s,min` does not count.
7. Method 1 has **two** tables: A.3 for vertical/inclined, A.4 for horizontal diffusing. Method 1
   is CIE/ISO 15469 overcast; Method 2 needs ≤ 1 h time steps and dynamic shading.

## National annexes

BS EN 17037:2018 carries a **UK National Annex (informative)** that materially relaxes the
recommendations for dwellings: alternative targets of **bedroom 100 lx, living room 150 lx, kitchen
200 lx** over ≥ 50 % of points for ≥ half the daylight hours, and an explicit statement that the
95 %-of-plane minimum-target recommendation "need not be applied to rooms in dwellings". It also
adds an overheating check where a dwelling room exceeds 500 lx on 50 % of points for more than half
the daylight hours, the glazing maintenance method (NA.3), and UK-localised supplements to Tables
A.3, A.4, D.1 and D.2 for ten UK/Channel Islands locations.

If you are reporting in a CEN member state, check for its national annex before quoting Annex A.

## Connects To

- **[ch01](ch01-en-12464-1-indoor.md)** — the other grid formula, and UGR vs DGP.
- **[ch04](ch04-ies-lm-83-sda-ase.md)** — LM-83 measures the same "is it daylit" question with a
  different metric (sDA) and different assumptions; the two do not convert.

**To actually compute these** — the sibling `radiance-simulation-workflow` skill:
- **ch06** — the daylight factor pipeline for Method 1 (`gensky -c` calibration,
  and the sky-geometry step whose omission returns 0 lux).
- **ch07** — the DGP render (`rpict` fisheye + `evalglare`). Note that chapter
  produces a *single-moment* DGP; this chapter's `DGP_e<5%` criterion needs many
  timesteps.
- **ch09/ch11** — climate-based illuminance for Method 2.
