# EN 12464-2:2007 — Lighting of outdoor work places

**Source**: EN 12464-2:2007, cited by clause and table.

## Core Idea

The same task/surrounding framework as [ch01](ch01-en-12464-1-indoor.md), applied outdoors, with two
substitutions that matter for simulation: glare is rated by **GR** (CIE Glare Rating), not UGR, and
a whole additional criterion appears — **obtrusive light** spilling off site.

## Calculation grid — explicit, and clearer than the indoor text

```
p = 0,2 × 5^log d          p ≤ 10 m          (clause 4.3.3, Formula 1)
```

- `d` = the **longer** dimension of the area **if the longer:shorter ratio is < 2**; otherwise `d`
  is the **shorter** dimension.
- Grid cell length:width ratio kept between **0,5 and 2**.
- A grid is required for the task **and** surrounding areas.

This is the same coefficient (0,2) as EN 12464-1, and it states the `p ≤ 10 m` cap explicitly.

## Zones — the surrounding area has a stated width

The **surrounding area** is the *"band surrounding the task area within the field of vision"*, and
the NOTE to 3.7 gives it a width: **at least 2 m**. That is a directly modelable parameter and it is
not the indoor 0,5 m.

**Table 1 (4.3.2)** — surrounding illuminance, a normative `shall`, and much lower than indoors:

| Task Ē_m (lx) | Surrounding Ē_m (lx) |
|---|---|
| ≥ 500 | 100 |
| 300 | 75 |
| 200 | 50 |
| 150 | 30 |
| 50 ≤ Ē_m ≤ 100 | 20 |
| < 50 | no specification |

Illuminance scale runs 5…2000 lx (4.3.1).

## Uniformity (4.3.4)

- Task area: per the application tables in clause 5.
- **Surroundings: U_o ≥ 0,10** — much lower than the indoor surrounding requirement.
- For some applications (railways is the named example) **illuminance diversity** is also a quality
  criterion, separate from uniformity.

## Glare — GR, not UGR (4.4, 6.2)

Outdoor discomfort glare uses the **CIE Glare Rating** system. The tabulated per-application limit
is `GR_L` (3.3). Do not carry a UGR limit across from EN 12464-1, and do not report GR indoors.

The formula (4.4.1, Eq. 2) — needed if you are computing it rather than quoting it:

```
GR = 27 + 24 × log10( L_vl / L_ve^0,9 )

L_v  = 10 × (E_eye × Θ^-2)
L_ve = 0,035 × ρ × E_hav × π^-1
```

**Sampling instruction**: GR is computed at the 4.3.3 grid positions, at **45° intervals radially**,
with 0° parallel to the long side.

## Obtrusive light (4.5)

A criterion with no indoor equivalent: light escaping the installation and affecting the
surroundings. Governed with reference to **CIE 150:2003**. Includes the **upward light ratio** —
the fraction of luminaire flux emitted above the horizontal.

**The limits are Tables 2 and 3 in clause 4.5**, not clause 6.4 — 6.4 is verification only, and
requires the designer to provide calculated E_v, I, ULR, L_b, L_s and TI.

- **Table 2**: pre-curfew / post-curfew × environmental zones **E1–E4**, across five quantities
  (E_v, I, ULR, L_b, L_s). ULR limits by zone are **0 / 5 / 15 / 25 %**.
- **Table 3**: **threshold increment TI** limits against road lighting class — a *second, separate*
  obtrusive-light criterion, and one a simulation can produce.

A **curfew** time may apply after which stricter limits take effect — per 3.1, *"a condition of use
of lighting applied by a government controlling authority, usually the local government."*

## Maintenance factor (4.9)

Normatively references **CIE 154:2003** — the outdoor route, not CIE 97 which serves indoors. Three
designer duties: state the MF and its assumptions, specify suitable equipment, and prepare a
maintenance schedule. Same discipline as [ch01](ch01-en-12464-1-indoor.md); different reference.

## Safety and security lighting (Annex A, informative)

Tabulated by risk level — very low / low / medium / high → **Ē_m 5 / 10 / 20 / 50 lx**, each with its
own U_o, GR_L and R_a. Also 4.7.2: **R_a ≥ 20** as a floor wherever safety colours must be
recognised.

## Anti-patterns

- **Reporting UGR for an outdoor installation.** Use GR.
- **Applying the indoor surrounding uniformity outdoors.** Indoors it is 0,5 (2002) or 0,40 (2011);
  the floor here is **0,10**.
- **Using the indoor 0,5 m surrounding band.** Outdoors the surrounding area is **≥ 2 m**.
- **Looking in clause 6.4 for obtrusive-light limits.** They are Tables 2–3 in clause 4.5; 6.4 only
  says what must be verified.
- **Forgetting TI.** Threshold increment is a second obtrusive-light criterion alongside E_v, I,
  ULR, L_b and L_s.
- **Applying CIE 97 maintenance factors outdoors.** 4.9 points to **CIE 154:2003**.
- **Ignoring obtrusive light** because it is not a task-plane quantity — it is a compliance
  criterion, and upward light ratio is a property of the luminaire model.

## Depth note

Thinner than [ch01](ch01-en-12464-1-indoor.md) — it draws on the text of EN 12464-2 alone, with no
application guide. The **per-application tables in clause 5** (five criteria columns: `Ē_m`, `U_o`,
`GR_L`, `R_a`, plus Remarks) and the **numeric limit values inside Tables 2 and 3** are not
reproduced; consult the standard for a specific application. Note also that the standard explicitly
disclaims worker health and safety scope (clause 1).

The claim that the grid coefficient `0,2` matches EN 12464-1 is **not verifiable from this document**
— EN 12464-1 appears only as an informative Bibliography entry. It is sourced in
[ch01](ch01-en-12464-1-indoor.md).

## Key Takeaways

1. Same grid formula and coefficient as indoor, with `p ≤ 10 m` stated explicitly in 4.3.3.
2. Surrounding area is **≥ 2 m wide**, with its own illuminance table (4.3.2) and `U_o ≥ 0,10`.
3. Glare is GR (CIE), not UGR — formula in 4.4.1, sampled at 45° intervals on the grid.
4. Obtrusive light: Tables 2–3 in **4.5**, zones E1–E4, pre/post curfew, ULR 0/5/15/25 %, plus TI.
5. Maintenance factor routes to **CIE 154:2003**, not CIE 97.

## Connects To

- **[ch01](ch01-en-12464-1-indoor.md)** — the indoor framework this mirrors.
