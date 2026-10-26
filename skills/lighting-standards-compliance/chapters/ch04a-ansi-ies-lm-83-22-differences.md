# ANSI/IES LM-83-22 — what differs from LM-83-12 (read with ch04)

**Source**: ANSI/IES LM-83-22, cited below by section number. Where the published text appears
internally inconsistent, the value is used as written and flagged as an *unconfirmed value* wherever
it is used; check it against your own copy of the standard. LM-83-12 stays the default: a caller who
names no edition gets ch04.

**How a caller picks the edition on rad-mcp.** `lm83_edition="LM-83-22"` on `sensor_grid` (action
`create`, `standard="lm83"`), `run_annual` (an LM-83 run: `sda` or `ase` in `metrics`, or
`blind_control="lm83"`) and `results` (`compliance` with `standard="lm83"`, `metrics` when it reports
`da`, and `blind_schedule`). It is refused where it would do nothing, never ignored. Pass the same value to
all three: a blended result's hours were decided by the closing rule it was sealed under, and `results`
refuses a mismatch below 1 000 sf, and at any area when the schedule has an automated group with an hourly
schedule bound (`LM83_EDITION_MISMATCH`). State the edition when reporting.

## What the server applies under `lm83_edition="LM-83-22"`

- **The ASE line of concern is 20 %.** LM-83-22 section 3.2 sets an ASE_net of 20 % or more as the
  level of concern, up from 10 % in the 2012 edition. The server labels `level_of_concern` at 20.0 % or
  more and `below_level_of_concern` under it. LM-83-22 has no 3 % and 7 % bands and none of LM-83-12's
  labels. **Whether the 20 % boundary is inclusive is an *unconfirmed value***: the text states it six
  ways, three inclusive ("or higher", "less than", "below") and three strict ("exceeds" twice,
  "greater than"). The server carries it inclusive. The line is stated for ASE_net, so the server judges
  it on ASE_net (`ASE_NET_LEVEL_OF_CONCERN`); the ASE_net bullet below says how a window group gets into it.
- **Blinds close on more than 2 % of the points at every area.** LM-83-22 section 4.6: blinds are
  operated so that, in every hour of the year, direct sunlight of 1,000 lux or more falls on at most 2 %
  of the space's analysis points. LM-83-12's small-area rows (500 to 999 sf: more than 5 points; 200 to
  499 sf: more than 3; under 200 sf: more than 1) do not exist, and neither does the 10 000 sf cap. A
  400 sf room with 103 points closes above 2 points under LM-83-22 (2 % of 103 is 2.06) and above 3 under
  LM-83-12.
- **The analysis area is points times spacing.** The LM-83-22 glossary defines it as the point count
  multiplied by the row and column spacings. The server reads both off the grid's own point file. It
  decides no hour under LM-83-22 (the rule is flat); it is the area the schedule records and reports.
- **The default height is 0.76 m** (30 in.) above finished floor, LM-83-22 section 4.1.2. LM-83-12's is
  0.8 m (30 in.). A grid built at 0.8 m and judged under LM-83-22 is judged without comment; measured on
  one office, ASE rose by 3 points of 456 and sDA moved inside its run-to-run spread.
- **The latitude band is 35 to 55 degrees**, LM-83-22 section 1.3, given as the latitudes to which the
  studied spaces apply best. It is an applicability band, where LM-83-12's 37° to 48° N is where its
  supporting research was conducted. The server reads both as north-positive.
- **The sky floor stays subdivision 4**, cited to LM-83-22 section 4.12.1 (at least 2,305 sky patches
  for the direct solar contribution). This server's suns share the sky's subdivision, so LM-83-22 section
  4.12.2's 145-patch sky floor does not apply to it. LM-83-22 section 4.12.1 also asks for the solar
  position within 1 degree (that 1 degree, beside the same section's 2,305 patches, is an *unconfirmed
  value*: the server's own patch-radius arithmetic puts 2,305 patches at an equivalent radius of about
  1.7 degrees, so the two disagree); no supported subdivision meets 1 degree.
- **ASE_net: apertures with automated shading or dynamic glazing are left out.** LM-83-22 defines
  ASE_gross and ASE_net in section 3.0 and section 4.2.2, and states the 20 % line for ASE_net in
  section 3.2. Mark such a group with `window_group(action="define", automated=True)`; `run_annual` then
  traces the whole grid once more with those apertures blacked, and `results` publishes `lm83.ase`
  (gross, unchanged) and `lm83.ase_net`, with `lm83.automated_groups` and `bands.ase_judged` saying
  which value the band was judged on. Under LM-83-22 that is ASE_net; under LM-83-12 it stays the gross
  value (its bands have no net). With no group marked the two are equal and the payload says why.
  Rationale: the standard states its line for ASE_net, and judging a gross value against it can only
  overstate the concern; a group the model marks wrongly is the caller's declaration, as `closing_role`
  is. A project whose every opening belongs to a window group marked automated has ASE_net 0 %.
- **The default 5 % blind (opt-in).** LM-83-22 section 4.6 has blinds modelled even where the design
  specifies none, and section 4.7 gives 5 % for a blind of unknown VLT; LM-83-12 section 2.2.7 p. 6 gives
  the same 5 % (a purely diffuse 5 % VLT with no specular part) for a shade of unknown VLT, so the
  state is available under either edition. Bind it by name:
  `window_group(action="assign_bsdf", states={"closed": "lm83_default"})` after the group's open state is a
  Klems BSDF. The server composes a Lambertian Klems state transmitting 5 % of the open state's
  diffuse-diffuse transmittance, the 5 % blind behind the group's real glass, and seals it as the group's
  closed state. Rationale: both editions say 5 % and the server already holds the open state, so only
  the product needs computing; it is opt-in because choosing a blind is the caller's modelling decision.
  The louvre rows of LM-83-22 Table 4-1 are not wired.
- **Automated groups follow their own schedule.** LM-83-22 section 4.6 leaves an automated group's
  sunlight out of the 2 % trigger analysis; LM-83-12 section 2.2.6 p. 5 allows the manufacturer's blind
  control protocol for motorised blinds and gives no exclusion. Bind the device's hourly schedule, one
  `open` or `closed` row per hour (8 760 or 8 784 rows, an optional `state` header), with
  `window_group(action="bind_schedule", name=..., schedule=<csv path>)`. The group leaves the closing order
  and takes its row for each hour; with no schedule bound, a run that seals a blind schedule holds it
  closed every hour, with a warning. Under LM-83-22 its direct sun never counts toward the trigger; under
  LM-83-12 it counts in the hours the group is open. Rationale: the two editions differ on exactly that
  one point (LM-83-12 section 2.2.6 gives no exclusion, allowing only the manufacturer's control
  protocol; counting the group's open hours, with a closed blind taken to pass no direct sun, is the
  server's choice; LM-83-22 section 4.6 excludes them), so the server refuses to read a schedule sealed
  under the other edition (`LM83_EDITION_MISMATCH`).
  **Dynamic glazing** (section 4.8's simplified approach, a VLT down to 2 % with direct sunlight negated) is
  not wired as a rule; model it as an automated group whose `open` and `closed` states are the clear and
  tinted BSDFs, with a bound schedule.
- **Citations and labels.** Messages cite LM-83-22 section numbers (period section 4.4, blinds section 4.6,
  direct sunlight section 3.1, reporting section 4.14) and write `sDA300/50%` and `ASE_gross1000/250h`. The
  separator changes from a comma to a slash, and the subscript carries the threshold and a gross or net tag.

**Both editions:** the analysis window leaves out 29 February, so every whole year counts
3 650 hours (both editions give that figure; LM-83-22 section 4.4 states it), and a blended
`blind_control="lm83"` run is raised to sky subdivision 4 like an `sda` or `ase` run.

## What LM-83-22 changes that the server does not yet apply

One line each. The automated-group exclusion, the default 5 % blind and ASE_net are applied (above);
the louvre rows of LM-83-22 Table 4-1 (section 4.7) are the exception and stay unwired.

- **The 150 lux threshold.** LM-83-22 section 2.1 and section 4.2.1 allow 150 lux or 300 lux; the server
  computes 300 lux only.
- **sDA with blinds open, closed and operated.** LM-83-22 sections 2.1, 4.6, 4.14 and 4.15.1 report all
  three, and where no trigger or automated value exists the blinds-closed result applies; the server
  publishes one sDA and `blind_control="none"` is not LM-83-22 compliant (an automated group with no
  schedule is held closed, but the three sDA values are not published).
- **Dirt and mullion factors.** LM-83-22 section 4.5 requires a dirt depreciation fraction (5 %, 10 %,
  15 % by slope) and a mullion reduction; the server applies neither under either edition and says so.
- **Building reports and excluded areas.** LM-83-22 section 4.1.1 and section 4.15 (Tables 4-2 to 4-5);
  the server reports per grid and excludes nothing.
- **Sun positions finer than 1 degree.** LM-83-22 section 4.12.1; see above.

## Modelling guidance that differs

These change how the model is built, not a number the server computes.

- **"Closed" is a 60-degree slat angle.** LM-83-22 section 2.1 calls horizontal blinds closed at a
  60-degree slat angle, the position at which they hide the horizon. The monthly rule is in both
  editions: LM-83-22 section 4.7 fixes the slat angle each month at the angle that fully blocks direct
  sun at that month's lowest solar altitude.
- **The grid runs wall to wall.** LM-83-22 section 4.1.2: points no greater than 0.61 m (2 ft) on
  centre, the grid covering the space wall to wall in every direction, with only points within 0.30 m
  (1 ft) of the walls allowed to be excluded. LM-83-12's 12 to 24 in. wall offset is a different rule.
  The server places its LM-83 grids by LM-83-12's offsets under either edition, so its first row sits at
  least 0.3048 m and under 0.6048 m from a wall. That complies with LM-83-22 section 4.1.2 (the server's
  own arithmetic): at its fixed 0.6 m pitch, the wall-to-wall lattice row one pitch further out lies at
  the offset minus the pitch, between -0.2952 and +0.0048 m, outside the room or within 0.30 m of the
  wall, so it may be excluded, and what remains is exactly the server's points. The server drops no point.
- **Points inside furniture may be dropped.** LM-83-22 section 4.1.2 allows dropping points that fall
  inside furniture (inside a bookcase or a file cabinet, for example). LM-83-12's rule (a point with a
  yearly maximum under 1 lux next to higher ones) is gone, so the 1 lux rule is not a reason to drop a point.
- **Large surfaces of differing reflectance.** LM-83-22 section 4.10: over 3 m (10 ft) in any dimension,
  an area whose reflectance is more than ±10 percentage points away from the primary-area reflectance is
  modelled separately (an 85 % whiteboard on a 50 % wall, whose range is 40 % to 60 %). LM-83-12's
  tolerance was 20 % between areas.
- **Obstructions.** LM-83-22 section 4.9: model every building and opaque structure that blocks more than
  10 degrees of sky above the horizon, as seen from ground level anywhere along the study building's base,
  at 3 m (10 ft) resolution; when none are known, assume buildings of the study building's height on
  adjoining and opposing parcels with its setback; model trees at their expected height at least 5 years
  beyond completion. Defaults: 30 % vertical exterior, 20 % trees, 10 % ground within 30 m (100 ft). This
  replaces LM-83-12's 100 ft distance rule with the horizon rule.
- **Furniture.** LM-83-22 section 4.11: partitions and furniture 0.9 m (36 in.) or taller are modelled
  to an accuracy given as "0.3 m (6 in.)". **That pair is an *unconfirmed value***: 0.3 m is about 12 in.,
  and 6 in. is about 0.15 m (LM-83-12 section 2.2.11 says 36 in. and 6 in. accuracy). Use the tighter
  figure until the discrepancy is resolved.
- **Labels.** Write `sDA300/50%` (threshold as a subscript, then the percentage of time) and
  `ASE_gross1000/250h` for LM-83-22, and `sDA_300/50%` and `ASE_1000,250h` for LM-83-12; do not mix them in
  one report.
