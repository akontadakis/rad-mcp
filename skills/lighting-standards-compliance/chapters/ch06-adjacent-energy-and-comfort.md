# Adjacent standards — EN 15193 and prEN 15251

**Sources — check what these documents actually are before citing them:**

| Document | Actually is | Status |
|---|---|---|
| "EN 15193-2" (as often cited) | **CEN/TR 15193-2:2017**, "Explanation and justification of EN 15193-1, Module M9", 187 pp | **Technical Report — not a standard, nothing in it is normative** |
| prEN 15193 | **prEN 15193:2006** — unpartitioned draft of the superseded single-part EN 15193:2007, at Formal Vote stage | Draft, and a decade older than the TR |
| prEN 15251 | **prEN 15251** working-group draft, 2014-08-20, CEN/TC 156 | Committee draft; superseded in service by EN 16798-1 |

**There is no "EN 15193-2".** The current normative text is **EN 15193-1:2017**, and it is **not
summarised here**. Everything below comes either from a 2017 commentary on that absent standard or from
a 2006 draft of its predecessor — which is why their symbol sets disagree (see the W-symbol note).

**Read this chapter as an index, not a specification.** These standards sit outside what a Radiance
simulation server computes. They are here because they consume its outputs and because a lighting
professional will be asked about them in the same conversation.

## EN 15193 — Energy performance of buildings: lighting

### LENI — the Lighting Energy Numeric Indicator

The headline output: annual lighting energy per floor area, **kWh/m²·year**. `LENI = W / A`, defined
at **building** level — `A` is the total useful floor area *of the building* (prEN 15193:2006, 4.2).
Rooms and zones are where the per-zone factors are computed and then aggregated; they are inputs to
LENI, not scopes of it. LENI exists to make installations of similar function but different size
directly comparable.

Two calculation routes, and they do not agree: a **Quick method** and a **Comprehensive method**
(6.2), with the standard warning that the Quick method *"will yield higher LENI values."* Say which
you used.

**Standby terms** — note these come from **CEN/TR 15193-2:2017 clause 4.1**, whose default values
live in *"Annex B of the standard"*, i.e. EN 15193-1:2017, absent here:

| Term | Meaning | Unit |
|---|---|---|
| `W_pc` | standby energy density, automatic lighting controls | kWh/m²·year |
| `W_pe` | standby energy density, emergency luminaire battery charging | kWh/m²·year |

**Do not look for these symbols in `prEN 15193:2006`** — it uses an entirely
different set (`P_ci`, `P_ei`, `P_pi`, `P_pc`, `W_p`) and contains no `W_pc` or `W_pe` at all.

It also carries **lamp maintenance data** — LLMF (lamp lumen maintenance factor) and LSF (lamp
survival factor) tabulated by lamp technology against **burning hours in thousands** (TR Table C.2).
These are the same quantities that feed the EN 12464-1 maintenance factor
([ch01](ch01-en-12464-1-indoor.md)), which is the one real point of contact with a simulation, via
`MF = LLMF × LSF × LMF × RSMF`. Two caveats: it is a **discrete lookup table**, not curves, and it
is **non-normative fallback** — the TR twice insists *"it is essential to consult manufacturers'
data."*

**LED is not in that table.** Every technology tabulated is legacy, and the footnote concedes
*"Data for LED's is changing rapidly."* The replacement framework is TR C.1.4.3.1 — LED LLMF/LSF
expressed as `(Lx, By)`, `(Lx, Cy)`, `(Lx, Fy)` per **EN 62722-2-1**.

### Where a daylight simulation feeds in

The term is the **daylight dependency factor `F_D`** (prEN 15193:2006, 3.7.1) — and it decomposes:

```
F_D,n = 1 − (F_D,S,n × F_D,C,n)
```

- `F_D,S` — **daylight supply factor**: daylight's contribution to required illuminance. **This is
  what an annual Radiance study supplies.**
- `F_D,C` — **daylight control factor**: the control system's ability to exploit it.

**Occupancy is a separate factor**, `F_O` (3.7.2), and a constant-illuminance factor `F_c` (6.2.3)
is separate again. The LENI expression keeps all three distinct:

```
LENI = { F_c × P_N/1000 × [ (t_D × F_D × F_O) + (t_N × F_O) ] } + …
```

Note the polarity: **`F_D = 1` in zones with no daylight availability.** It is a multiplier where 1
means no daylight benefit — the opposite of what "dependency" suggests.

`F_MF = 0,8 / MF` (TR C.4) is the explicit numeric bridge from the maintenance factor to lighting
energy.

LENI also has a **measured** route, not only a calculated one — load segregation and metering
(clause 5), with benchmarks in clause 7. Worth knowing before offering a simulated LENI as the
answer.

## prEN 15251 — Indoor environmental input parameters

Covers indoor environmental quality broadly: thermal comfort, indoor air quality, acoustics, and
lighting, as inputs to building energy performance assessment and to the design of the systems that
deliver them. It frames lighting as **one of four** environmental criteria — the framing a client
brief usually arrives in.

**It defers for electric task lighting, but sets its own daylight criteria.** For work places it
points to EN 12464-1 and EN 12193 (6.4, 7.4). But 6.4 also requires that *"windows shall be the
primary source of light during daylight hours and provide visual contact with the outside"* and that
daylight penetration be fulfilled in occupied spaces — and Annex B4 **Table B4-2** classifies
daylight availability by **daylight factor**, which a Radiance study computes directly:

| Availability | Vertical façades `D_Ca,j` | Rooflights `D_SNA` |
|---|---|---|
| **Strong** | ≥ 6 % | > 7 % |
| **Medium** | 4 % ≤ D < 6 % | 4 % ≤ D < 7 % |
| **Low** | 2 % ≤ D < 4 % | 2 % ≤ D < 4 % |
| **None** | < 2 % | 0 % ≤ D < 2 % |

**Upper bound**: *"Values of `D_SNA` > 10 % should be avoided due to danger of overheating"* — the
table classifies availability, but more is not simply better.

These thresholds are **not** in EN 12464-1. It also references prEN 15193-1 for occupancy periods
and daylight availability, and mandates EN 15193-1 for lighting energy calculations (7.4).

## Anti-patterns

- **Quoting LENI from a lighting simulation alone.** It needs operating hours, control strategy,
  standby loads and daylight dependency — none of which a point-in-time or even an annual
  illuminance study supplies by itself.
- **Citing "EN 15193-2".** No such standard exists. It is **CEN/TR 15193-2:2017**, a non-normative
  Technical Report explaining EN 15193-1:2017.
- **Reporting `F_D` as what a daylight simulation produces.** The simulation gives `F_D,S`; the
  control factor `F_D,C` and the occupancy factor `F_O` are separate, and `F_D = 1` means *no*
  daylight benefit.
- **Assuming prEN 15251 has no lighting requirements of its own.** It defers on electric task
  lighting but sets daylight-factor availability thresholds.
- **Using the TR's maintenance tables without checking lamp type and operating hours** — or at all
  for LED. They are non-normative fallback, legacy technologies only, and the TR says to use
  manufacturers' data.
- **Treating any of these three documents as current normative text.** One is a TR, two are drafts.

## Depth note

Deliberately shallow. CEN/TR 15193-2:2017 alone is 187 pages of justification commentary; prEN 15251
is 81 pages and mostly thermal and IAQ. Neither is summarised beyond the orientation above.

If a task genuinely needs LENI, note that **"go to the source" does not mean these documents** — the
normative source is **EN 15193-1:2017**.

## Connects To

- **[ch01](ch01-en-12464-1-indoor.md)** — maintenance factor, the shared quantity.
