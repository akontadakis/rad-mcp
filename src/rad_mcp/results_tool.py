"""`results` -- docs/tool-surface.md#14-results.

Turns the annual `.ill` `run_annual` committed into numbers a caller can read. Separate from
`run_annual` because *"the run outlives the connection and the same `.ill` supports several metric
readings"* (§14).

**Layers.** This module contains no physics and no Radiance knowledge. `rad_engine.results` owns the
matrix format -- the streaming read, the header, the shape refusals -- and, as of Phase 7b Task 4,
the RGB/scalar-to-lux conversion too: `iter_verified_annual_lux_rows` is the one reader every action
below consumes -- `iter_annual_lux_rows`' loop with the manifest's sealed digest checked on the same
held descriptor -- and that loop is the only caller of `rad_core.photometry.to_lux` in this path
(one arithmetic path, ruling R30). `rad_mcp` plumbs: it resolves the artifact (on every action but
`blind_schedule`, which reads `illdir:<grid>`) -- the one annual result registered for the grid, or
the one `chain` selects after `blind_control` narrows them; more than one left and no `chain` is a
**refusal** (ruling R105), except on `compliance`, which reports them all in `by_chain` (R113), and
there is no preference order -- applies the inline bound, shapes the envelope and emits the
advisories.

## Seven actions, and none of them deferred any more

`summary`, `points`, `timeseries`, `export(format="csv")`, `compliance` (`standard="lm83"` as of
Phase 7b Task 12; `standard="en17037"`, `method="2"` as of Task 13), `blind_schedule` as of Phase 7c
Task 16 -- which needed the pre-blind zero-bounce direct-sun run 7c's earlier tasks build, because
the schedule is derived from `illdir:<grid>` and from nothing else -- and `metrics` as of Phase 7c
Task 18. `_DISPATCHABLE_ACTIONS`
is now the whole published set, so this module holds no action that is accepted at the schema
boundary and refused in the body; `_DEFERRED` and `_refuse_deferred` were deleted with the last key
rather than left as a dead branch behind a docstring claiming otherwise.

What still refuses is narrower and sits with the argument it is about, which is the point of the
change: a half-built answer is worse than a refusal -- a caller who receives `{"sda": null}` has to
know it means "not built" rather than "zero" -- but a *blanket* refusal keyed on the action name
outlives its own reason, which is what happened to `compliance` in 7b and to `blind_schedule` here.

`compliance(standard="en12464")` still refuses -- EN 12464-1 illuminance and zone uniformity are
computed by `run_electric` on a zoned en12464 grid, not here (Phase 11b R604). `compliance(standard="en17037", method="1")` also refuses
(`EN17037_METHOD_1_NOT_AVAILABLE`): Method 1 compares a daylight factor against Table A.3, and
`run_point_in_time` registers no artifact this action can read one from. Only Method 2 -- the
climate-based route `run_annual`'s matrix actually feeds -- is built, and only its
`daylight_provision` axis: `view_out`, `sunlight_exposure` and `glare` are EN 17037's other three
independent criteria (ch03:9-11) and are reported `assessed: false` rather than computed
(`rad_core.compliance.en17037_object`). A caller asking for `axis="glare"` is refused naming
`usage_hours` (`EN17037_USAGE_HOURS_REQUIRED`) or, once that is supplied, naming the annual-DGP
matrix this phase does not build -- glare is a later phase's own work, not silently answered from
provision's own denominator.

**`compliance(standard="lm83")` does no arithmetic of its own.** Every threshold and tally comes
from `rad_core.metrics` (`PointTally`, `spatial_daylight_autonomy`, `annual_sunlight_exposure`) and
every judgement band from `rad_core.compliance` (`sda_band`, `ase_band`) -- this module resolves the
artifact, builds the tallies from the matrix `_lux_rows` already streams, and shapes the result and
its advisories. sDA and ASE are reported **together or not at all**
(`SDA_REPORTED_ALONE`, ch04 §2.4).

**`compliance` answers a multi-result grid from EVERY result** (Phase 7c Task 17, ruling R113;
widened at Phase 7d-1 Task 15a by ruling R174). Where `summary`/`points`/`timeseries`/`export`
refuse an unselected call on a grid holding more than one annual result
(`ANNUAL_RESULT_AMBIGUOUS`, R105), `compliance` publishes a `by_chain` block keyed by the recorded
method **and the blind treatment** -- `"3phase"` for the unscheduled total and `"3phase+lm83"` for
the blend, which record the same method (R158) and would otherwise collide -- with the
single-result keys **absent rather than null**. An entry is **whole or it is a refusal**, and each carries its own `analysis` and its own
advisories -- the two matrices can differ in column count and in what is true of them. Every entry
is built by calling the same `_compliance_lm83`/`_compliance_en17037_provision` the single-result
path calls, so no threshold, advisory or refusal ordering is expressed twice. That is also how
Task 13's refusal ordering survives into an entry: it is not re-expressed anywhere.

**sDA and ASE come from two different matrices** (Phase 7c Task 13): sDA from the chain result
`_resolve` selects, ASE from `illdir:<grid>` -- the pre-blind, zero-bounce direct-sun run
`chain.direct_sun_steps` seals -- because §3.3 defines ASE as the direct beam from the solar orb
alone, after glazing transmittance, and says it *"does not require modeling of sky luminance or
ground reflectance"*. Which matrix ASE reads is decided by the **manifest**, never by
`method_used`: `run_annual` plans the direct leg on `"ase" in metrics` or `blind_control="lm83"`
and nothing in the chain keys on the method, so `illdir:<grid>` is reachable on a two- or
three-phase run. `ASE_FROM_SMEARED_SUN` refuses when there is **no** direct matrix *and* the chain
result is not five-phase -- the widened form of the same rule, said about the matrix the number
would have been read off rather than about the chain. With a five-phase chain result and no direct
leg the number still ships, labelled `ASE_NOT_FROM_DIRECT_ONLY_RUN` (ruling R108); that label is
now conditional, and its absence is the positive statement that ASE is the standard's own quantity.

**`action="metrics"` publishes UDI and DA and judges neither, and that is the whole distinction
between it and `compliance`.** `compliance` means *"judged against a standard"*; neither metric here
has one. UDI's `100-2000` lux band is sourced (R106) but its 2 000 lux ceiling is contested research
and **no source in this corpus states an analysis period for it at all** (R111), so the occupied
mask is a labelled substitution (`UDI_PERIOD_ASSUMED`, `analysis.provenance == "default"`). DA_300
is LM-83's own per-point quantity, but its only standardised aggregate is sDA -- which LM-83 ch04
section 2.4 forbids reporting without ASE -- so `DaView` publishes **no spatial figure** and carries
`SDA_REPORTED_ALONE` saying so, the same reasoning `_SUMMARY_SDA_REPORTED_ALONE_ADVISORY` already
applies to `summary`. No `bands`, no `level_met`, no `statement` and no `edition` field appears
anywhere in that action's payload.

**Two `analysis` blocks, because there are two periods.** UDI's is `occupied_hours` (2 600 h in a
common year); DA's is `lm83_window` (3 650 h) -- the same clock window *without* the weekday filter.
One shared block would publish `da_300` under a denominator that is not its own, and
`rad_core.metrics._ensure_window_matches` raises if a tally built against one window is handed to a
metric expecting the other. So `_metrics` builds **two tally sets in one streaming pass** rather
than one shared set reported twice: the guard stays load-bearing instead of being worked around.

**`metrics` does not refuse a sub-annual matrix, and that is R104's own reasoning applied rather
than ignored.** `_refuse_sub_annual_matrix` states the rule -- it is *"the band and the level
statement that a sub-annual period makes false"*, which is why `summary`/`points`/`timeseries`/
`export` are untouched by it. The arithmetic behind it is a threshold that does **not scale**:
ASE counts against an absolute 250 hours, so a short window guarantees `clearly_acceptable`. Neither
metric here has such a threshold -- UDI's second one is a *fraction* of the window and DA's
denominator *is* the window -- and this action publishes no band for a short period to make false.
An **empty** window is refused, because that is a missing denominator rather than a short one, and
the two metrics can disagree about the same matrix: `occupied_hours(24)` is empty (2023-01-01 is a
Sunday) while `lm83_window(24)` has ten columns.

**Which sDA is blind-aware is decided by the artifact this call resolved, not by the phase.**
~~Nothing here computes sDA against a blind schedule. sDA is not blind-aware until Phase 7d, which
blends a blinds-open and a blinds-closed result hour by hour (R107).~~ **Amended at Phase 7d-1 Task
15a (ruling R178).** An sDA read from `illblend:<grid>` -- `blind_control="lm83"` -- **is** computed
against LM-83 §2.2.6's schedule: every hour of that matrix is the per-group, per-state contribution
the sealed `sched:<grid>` selected. It reports `analysis.blind_schedule_used: true` and carries no
escape-clause advisory. An sDA read from `ill:<grid>` or `ill5:<grid>` is the unscheduled total, and
still carries LM-83 §2.2.6's escape clause named on it (`SDA_WITHOUT_BLIND_SCHEDULE`) rather than
silently implying a schedule that was never applied -- **including in a project that also holds a
blend**, because the condition is a statement about the matrix this call read. Every result states
which edition of LM-83 it was judged against (`LM83_EDITION`).

**This module used to name a phase in its refusals, and no longer does.** It was re-worded from
"Phase 7" to "Phase 7b" in Phase 7a Task 14, when Phase 7 split into 7a (the 5-phase chain) and 7b
(the metric arithmetic `compliance` and `blind_schedule` then refused for); 7b Task 12 built
`compliance` and 7c Task 16 built `blind_schedule`, and the last phase-naming refusal went with it.
The rule that produced those re-wordings stands and is worth keeping written down: a refusal that
names a phase acquires a maintenance obligation the moment that phase lands, and one still naming
"Phase 7" after 7a merged is the wrong-phase defect R81 exists to close. What replaced it here is
narrower refusals that name a *missing input* rather than a calendar -- `BLIND_SCHEDULE_NO_DIRECT_
MATRIX` names the `run_annual` call that seals the matrix, which stays true whatever phase it is.

`export` was moved into scope on 2026-08-09 (owner decision) after the first brief grouped it with
those two. It does not belong with them: a CSV of a raw timeseries is a **transcription** of the same
rows `timeseries` already returns and needs none of the deferred arithmetic. Check 4.8's own CSV
clause -- *"`format="csv"` export reproduces them"* -- depends on it, so deferring it would have
closed 4.8 only partially while the plan recorded it as covered. `format="json"` and
`format="parquet"` still refuse: parquet needs a dependency this project has not taken, and neither
is load-bearing for 4.8.

## The bound, and why its two actions are bounded differently

Tool results have **no pagination in MCP** -- `tools/list` and `resources/list` paginate, tool
*results* do not (docs/tool-surface.md, and check 4.8). So a result that drops rows must say so, and
*"silently returning the first N rows of an annual result -- January reported as the year"* is the
failure the check exists to prevent.

`MAX_INLINE_VALUES = 20_000` is the established bound (`point_in_time_tool`, taken from the spec's
own `GRID_VERY_LARGE` threshold so there are not two definitions of "large grid"). This tool reuses
that number and **applies it to a different dimension per action, because the two actions publish
different shapes**:

| Action | Published shape | Bounded on | Crosses 20 000 at |
|---|---|---|---|
| `summary` | fixed-size aggregate | never bounded | -- |
| `points` | one aggregate per point (`n_points` values) | **points** | 20 000 points |
| `metrics` | one aggregate per point, per requested metric | **points** | 20 000 points |
| `timeseries` | `n_points x n_hours` triples | **total triples** | **3 points** of an 8 760-hour year |

**`points` and `metrics` truncate; `timeseries` refuses.** That asymmetry is the decision this module was asked to
make explicitly, and the arithmetic is why. On a triple count a full year crosses 20 000 at **three
sensors** -- so a `timeseries` bound would fire on essentially every real call, and "truncated" would
be the normal answer rather than the exceptional one. An advisory that is always on is not a bound;
it is noise that trains a caller to ignore the one signal that matters. Worse, a truncation there
answers a question nobody asked: the first 20 000 triples of a 456-point year is two and a bit
sensors' worth of hours, which is neither a grid nor a period.

So `timeseries` **requires** §14's existing `point_ids`/`hours` selectors and refuses without them,
naming both counts and the selectors that make the request answerable. A refusal that says "ask for
the points and hours you want" is more useful than a truncation nobody asked for -- and it is
honest, where an always-truncating action pretends to a completeness it never has. `points` keeps the
truncation shape, because there the bound is genuinely exceptional: 20 000 points is the spec's own
"very large grid", and every grid it considers ordinary is inlined complete.

**Statistics are computed over the FULL set before truncation**, the rule carried from
`point_in_time_tool.MAX_INLINE_VALUES` -- a mean over a truncated list is a wrong number. Here that
is structural rather than careful: the streaming reader visits every row, and the aggregate is
accumulated as it goes, so the truncation applies only to the list that is *published*.

## Every number published is read from the matrix, never from a constant

`rad_mcp.annual_tool.ANNUAL_TIMESTEPS` is 8 760 -- the column count `run_annual` *intends* to
produce. This tool never compares against it and never reports it. `hours` comes from
`AnnualMatrix.hours`, read from the `.ill`'s own header, which is ruling R47's requirement: a 24-hour
file, a truncated file and an 8 784-hour leap year must not all report a complete common year.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import math
import re
import stat
import os
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from dataclasses import field as _dataclass_field
from pathlib import Path
from typing import IO, Annotated, Any, ClassVar, Final, Literal, TypeVar

from mcp_types import CallToolResult, ResourceLink, TextContent
from pydantic import BaseModel, ConfigDict, Field, RootModel, model_serializer, StrictInt, StrictFloat

from rad_core.blinds import (
    BLIND_STATES,
    CLOSING_ROLES,
    LM83_BLIND_ANALYSIS_AREA_MAX_SF,
    BlindSchedule,
    GroupFacts,
    closing_order,
    derive_schedule,
    same_closing_rule,
)
from rad_core.blinds import _area_band
from rad_core.apertures import ApertureError
from rad_core.compliance import (
    EN17037_ABOVE_PLANE_TOLERANCE_M,
    OpeningClass,
    ase_band,
    en17037_object,
    en17037_opening_class,
    en17037_opening_is_horizontal,
    en17037_provision,
    en17037_thresholds,
    lm83_latitude_band,
    lm83_latitude_in_calibration_band,
    sda_band,
)
from rad_core.metrics import (
    LM83_ASE_THRESHOLD_LUX,
    LM83_BLIND_AREA_GENERAL_SF,
    LM83_SDA_THRESHOLD_LUX,
    UDI_BAND_LOWER_LUX,
    UDI_BAND_UPPER_LUX,
    UDI_SPATIAL_TIME_FRACTION,
    PointTally,
    TallyResult,
    annual_sunlight_exposure,
    daylight_autonomy,
    f_plane,
    sda_hours_required,
    spatial_daylight_autonomy,
    spatial_udi,
    udi_hours_required,
    useful_daylight_illuminance,
)
from rad_core.periods import (
    LM83_DAYLIT_FRACTION,
    MAX_DECLARED_HOURS,
    OCCUPIED_END_HOUR,
    OCCUPIED_REFERENCE_YEAR,
    OCCUPIED_START_HOUR,
    WHOLE_YEAR_HOURS,
    daylight_hours,
    is_whole_year,
    lm83_window,
    occupied_hours,
)
from rad_core.grids import (
    GRID_POLYGON_COVERAGE_MIN,
    LM83_GRID_POINT_DENSITY_MIN,
    M2_TO_SQFT,
    MATRIX_MAX_DECLARED_POINTS,
    GridError,
    PolygonError,
    grid_polygon_coverage,
    grid_spacing_m,
    lm83_grid_point_count,
    lm83_grid_point_density,
    lm83_points_area_sf,
    polygon_area_m2,
)
from rad_core.lm83_edition import (
    DEFAULT_LM83_EDITION,
    LM83_22,
    LM83_22_NOT_APPLIED,
    LM83_EDITIONS,
    Lm83Edition,
    ase_label,
    edition_title,
    sda_label,
    section,
)
from rad_core.quality import AMBIENT_PRESETS
from rad_core.weather import EPWError, EPWFile
from rad_core.manifest import InputRecord, ManifestError
from rad_engine import (
    LoadedProject, ManifestUnreadableError, ProjectError, ProjectPaths, RadianceError,
    RadianceNotFound, SceneFileNotFoundError, SceneInlineCommandError, discover, evaluate_loaded,
)
from rad_engine.blend import SCHEDULE_DIGEST_KEY, compare_schedule_digest
from rad_engine.env import locate
from rad_engine.export import (
    COMPILED_PARAMETER, SCENE_INCLUDE_FAILED_PARAMETER, SCENE_INCLUDE_LINKS_PARAMETER,
    SCENE_INCLUDES_DROPPED_PARAMETER,
    SCENE_INCLUDES_PARAMETER,
    current_file_digest,
)
from rad_engine.octree import inside_allowed
from rad_engine.chain import (
    WINDOW_GROUP_INPUT_PREFIX, automated_group_names, blend_ill_id, blind_schedule_id, is_automated,
    direct_ill_net_id, states_by_group,
)
from rad_engine.schedule import sealed_lm83_edition
from rad_engine.skymatrix import (
    LM83_MIN_REINHART_PATCHES,
    LM83_MIN_SKY_SUBDIVISION,
    reinhart_row_count,
)
from rad_engine.clock import REFERENCE_YEAR, ClockZoneError, check_clock_zone, clock_shifts
from rad_engine.project import TRANSMITTING_MATERIAL_WHERE, bytes_digest
from rad_engine.scene import defined_modifier_names, flatten_scene, unclaimed_glazing_modifiers
from rad_engine.results import (
    AnnualMatrix,
    CoefficientMatrixUnreadableError,
    MatrixContentDriftedError,
    MatrixDigestUnreadableError,
    MatrixFileUnreadableError,
    NonFiniteMatrixCellError,
    coefficient_matrix_is_all_zero,
    iter_verified_annual_lux_rows,
    read_annual_header,
)
from rad_engine.safe_io import (
    NotARegularFileError,
    open_regular_file,
    refuse_existing_non_regular_destination,
    refuse_linked_components,
    replace_file_atomically,
    resolved_parent_within,
)
from rad_engine.weather import WeatherFileUnreadableError, read_epw_with_digest
from rad_mcp.advisories import warning_to_wire
from rad_mcp.names import SENSOR_GRID_INPUT_PREFIX
from rad_mcp.envelope import Advisory, RadResult, Refusal, emit, one_of_schema, refuse, refuse_missing
from rad_mcp.export_tool import stale_detail
from rad_mcp.glare_tool import _project_apertures
from rad_mcp.window_group_tool import _redefine_call
from rad_mcp.paths import CodedWriteRefusal, PathOutsideRoot, RootsNotConfigured, contain, contain_as_named, expanded_root, write_failure
from rad_mcp.point_in_time_tool import (
    MAX_INLINE_VALUES,
    GridEmptyOnDiskError,
    GridFileChangedError,
    GridNotFoundError,
    GridRecordNameError,
    GridUnreadableError,
    _grid_points_file,
    _read_points,
    refuse_grid_off_recorded_floor,
)
from rad_mcp.runtime import Runtime, current, refuse_engine_error
from rad_mcp.weather_tool import _files_of

logger = logging.getLogger(__name__)

__all__ = [
    "DESTRUCTIVE_ACTIONS",
    "GridFileOutsideRoot",
    "ResultsResponse",
    "results",
]

#: **Empty, and that is the claim** -- the property check 0.7's sweep reads off the surface itself.
#: `results` reads a committed `.ill` and, for `export`, writes a CSV into `08_results/` that it owns
#: and names. It deregisters no manifest input, deletes no user file and removes nothing a caller
#: registered -- the same property that earns `run_annual` and `run_point_in_time` the `_WRITES`
#: annotation rather than `_DESTRUCTIVE`.
DESTRUCTIVE_ACTIONS: tuple[str, ...] = ()

#: The actions §14 publishes. Every one of them now dispatches: `compliance` stopped being deferred
#: in Phase 7b Task 12 and `blind_schedule` in Phase 7c Task 16, so the module holds no
#: "accepted at the schema boundary, refused in the body" action any more. Which *standards* within
#: `compliance` still refuse is `_refuse_standard_deferred`'s decision, not this type's.
Action = Literal[
    "summary", "points", "timeseries", "compliance", "metrics", "blind_schedule", "export",
]

#: **The set the dispatch gate reads, and the only place the action names are written down beside
#: `Action` itself.** Introduced in Phase 7c Task 16 in place of the inline literal tuple the gate
#: used to carry, because two spellings of one set agree exactly until one of them moves -- and
#: `blind_schedule` had already been left out of that tuple once, sending a caller who mistyped an
#: action to a list of actions that could not answer.
#:
#: Consumed outside this module by Phase 7c Task 20's `published_actions()`, so the name is fixed.
_DISPATCHABLE_ACTIONS: Final[frozenset[str]] = frozenset({
    "summary", "points", "timeseries", "compliance", "metrics", "blind_schedule", "export",
})

#: Which chain built the matrix this call answered from -- not a schema addition invented for its
#: own sake, but the field Task 4's fallback made load-bearing. `ResultsSummary` itself had no
#: `method_used` field before that task; it was added here and to every other action's result
#: model, because all four resolve through the same `_resolve` and the resolution order then could
#: silently answer from `ill5:<grid>` instead of `ill:<grid>` on any of them, not summary alone.
#: Since R105 it never does: `_resolve` has no preference order, and two registered results with no
#: `chain` are refused (or, on `compliance`, both reported).
#: Derived from the artifact's own `parameters.get("method")`, defaulting to `"2phase"` when the key
#: is absent -- `chain.two_phase_steps`'s seal records no `"method"` key, so the absence is the
#: normal case for most projects and must never raise.
MethodUsed = Literal["2phase", "3phase", "5phase"]

#: **Which chain's annual result this call reads** -- `results(chain=...)`, added 2026-08-21 by
#: ruling [R105]. Deliberately **not** called `method`: `results` already publishes a `method`
#: argument, and it means EN 17037's Method 1 vs Method 2 (`en17037_thresholds`' own two routes),
#: which is a different question entirely. `chain` is this codebase's own word for the thing being
#: selected -- `chain.two_phase_steps`, `chain.three_phase_steps`, `chain.five_phase_steps`,
#: `ChainRequest` -- so the argument takes the name the artifacts were sealed under.
#:
#: The three values map onto **two** artifact ids, not three: `chain.two_phase_steps` and
#: `chain.three_phase_steps` both seal `ill:<grid>`, and only `chain.five_phase_steps` seals
#: `ill5:<grid>` (`_ARTIFACT_PREFIX_FOR_CHAIN`). So the id alone cannot tell `"2phase"` from
#: `"3phase"`, and `_resolve` checks the artifact's own recorded `method` parameter against what
#: was asked rather than trusting the prefix -- see `_ChainMethodMismatch`.
Chain = Literal["2phase", "3phase", "5phase"]

#: **Which blind treatment this call reads** -- `results(blind_control=...)`, added at Phase 7d-1
#: Task 15a (ruling R188). Orthogonal to `chain`, and that is the whole design: `chain` names the
#: *method* that wrote a matrix (2-, 3- or 5-phase) and `blind_control` names whether the matrix
#: was blended against LM-83 section 2.2.6's hourly schedule. A blended result records
#: `method="3phase"` exactly as the unblended total does (ruling R158), so the two questions cannot
#: be folded into one argument without one of the answers becoming unreachable -- which is why
#: `chain` gains no `"illblend"` value.
#:
#: **All three of `run_annual`'s values, and `"fixed"` is a SYNONYM for `"none"` rather than a
#: refusal** -- owner decision R197, 2026-08-30, superseding this argument's own two-value
#: vocabulary and R188's requirement that `"fixed"` be refused with a recovery sentence.
#:
#: ~~Two values, not three. `results(blind_control="fixed")` is refused with
#: `ARGUMENT_OUT_OF_RANGE` and the message names `'none'` as the value that reaches a `fixed`
#: run's result.~~ **Retired, and the reason is that the refusal could not fire.** Typed
#: `BlindControl | None`, pydantic rejected `"fixed"` at the schema boundary with a bare
#: *"Input should be 'lm83' or 'none'"* and **no** recovery -- measured through `mcp.call_tool` --
#: so the actionable refusal R188 asked for was unreachable from every client, while
#: `docs/tool-surface.md` published it as a row a client could receive. A refusal no caller can
#: obtain is not a refusal; it is a false entry on the surface.
#:
#: **Why a synonym is right rather than a third behaviour.** A `fixed` run seals *exactly* the same
#: unblended `ill:<grid>` a `none` run does -- both multiply through the group's one bound BSDF
#: state, which is the only kind `chain.three_phase_target` builds without a schedule -- so there
#: is nothing to distinguish and nothing for a third value to select. Accepting the caller's own
#: word answers from the artifact their run actually sealed; the refusal row disappears rather than
#: becoming reachable, and the surface is true by construction instead of by a sentence that has to
#: stay in step with a guard.
BlindControl = Literal["lm83", "none", "fixed"]

#: The `blind_control` values the **read** side takes, as a set rather than an `in (...)` literal
#: at the one call site -- `annual_tool._BLIND_CONTROL_ACCEPTED`'s own stated reason for existing
#: on the write side.
#:
#: **The two sets are now identical** (R197). They differed by `"fixed"` for one commit, which is
#: what put an unreachable refusal on the published surface; `results` accepting every value
#: `run_annual` accepts is what removes the mismatch rather than documenting it.
_READ_BLIND_CONTROLS: Final[frozenset[str]] = frozenset({"lm83", "none", "fixed"})

#: The `blind_control` value that names the blended result, imported from nowhere because it is
#: this layer's own wire vocabulary rather than `rad_engine`'s: `chain.LM83_BLIND_CONTROL` is what
#: a *run* is submitted under, and the two happen to spell the same word for the same reason a
#: caller would expect them to. Named once so the refusals, the resolver and the payload keying
#: cannot disagree about which string selects the blend.
_LM83_BLIND_CONTROL_READ: Final[str] = "lm83"

#: The artifact-id prefix `chain.blend_ill_id` mints, **derived from that function rather than
#: typed beside it**. `blend_ill_id`'s own docstring names this module as its second reader and
#: says what a hand-typed second spelling costs: a resolver reporting *no annual result* for a
#: project that holds one, after the whole per-(group, state) fan-out and the blend have been paid
#: for. Deriving the prefix from the helper is the cheapest form of "one spelling" available --
#: a rename of the id reaches this constant without anyone remembering to follow it.
_BLENDED_PREFIX: Final[str] = blend_ill_id("")

#: The one `chain` value that can ever name a blended result. `blend_steps` is a step of the
#: three-phase chain and of no other (`chain.blend_steps`), and the seal records
#: `method="3phase"`, so `blind_control="lm83"` with `chain="2phase"` or `chain="5phase"` is
#: unsatisfiable by construction rather than merely unregistered today.
_BLEND_CHAIN: Final[str] = "3phase"

#: **`_DEFERRED` and `_refuse_deferred` are gone**, removed in Phase 7c Task 16 rather than left
#: empty. `compliance` left that dict in Phase 7b Task 12 and `blind_schedule` was its last key; an
#: empty dict, and a `_refuse_deferred` no dispatch can reach, would be a dead branch behind a
#: docstring claiming otherwise -- which is one of the three ways `annual_tool._UNCOMPUTED_METRICS`
#: (deleted by R815) bit this project. Nothing outside this module read either symbol. What each *standard* within
#: `compliance` still defers is `_refuse_standard_deferred`'s and `_validate_en17037_arguments`'
#: own decision, made once `standard` is known.
#:
#: The single key that stood here read *"the LM-83 blind-control trigger and its small-area
#: corrections, which also need the window groups Phase 5/6 registers."* `_blind_schedule` now
#: answers the first half; the second is answered by **not** answering it -- `groups` is published
#: `null` (R116), because one `illdir:<grid>` per grid cannot be attributed to a group. Recorded so
#: the deletion is legible rather than silent.

#: `export` formats -- the `Literal` docs/tool-surface.md §14 publishes for `format`, and the
#: type `results(format=...)` is annotated with. Only `csv` is built; `json` and `parquet`
#: refuse in `_export`'s body (see the module docstring). **It was declared here and used
#: nowhere** until the Phase 7b whole-branch review: `results` typed `format: str`, so the
#: published `Literal` was enforced at no boundary and an unknown format reached the body as a
#: string rather than being rejected at the schema. The body check stays -- it is what a
#: direct, in-process caller hits, and it is what tells a caller *why* `json` refuses -- but
#: the wire now rejects a format that is not one of the three before this module runs at all.
ExportFormat = Literal["json", "csv", "parquet"]

#: What a CSV export is written as, and the mime type its `resource_link` declares.
_CSV_MIME_TYPE = "text/csv"

#: The artifact-id prefix `chain.direct_sun_steps` seals the pre-blind, zero-bounce direct-sun
#: matrix under. Factored out because it is now read as well as written -- `_lux_rows` branches on
#: it to choose between `DIRECT_MATRIX_UNREADABLE` and `ANNUAL_MATRIX_UNREADABLE` -- and a prefix
#: that is compared against in one place and constructed in two others is the `_window_group_names`
#: shape this repo has already caught once ("one definition, never two that agree").
_DIRECT_ARTIFACT_PREFIX: Final[str] = "illdir:"

#: The two notices `rad_engine.project.register_artifact` stores on an all-zero `asemx` it SEALED
#: rather than refused: `ASE_DIRECT_ALL_ZERO_NO_SUN` (no dome direction in front of the aperture,
#: ruling R143) and `ASE_DIRECT_ALL_ZERO_OCCLUDED` (the occlusion probe found no sun reaching the
#: grid, rulings R831, R856, R858). On a per-group `asemx:<grid>:<group>` the second means no sun
#: reaches the grid through THIS group's opening (R858's difference trace); other groups may be lit.
#: Every other code on an `asemx` stays on the manifest.
_ASE_SEAL_CODES: Final[frozenset[str]] = frozenset({
    "ASE_DIRECT_ALL_ZERO_NO_SUN", "ASE_DIRECT_ALL_ZERO_OCCLUDED",
})


def _sealed_ase_matrix_advisories(
    loaded: LoadedProject, published: Sequence[str]
) -> list[Advisory]:
    """Every seal notice (`_ASE_SEAL_CODES`) stored on an `asemx` artifact that the artifacts in
    `published` were built from, on the wire, in manifest order (Phase 25 Task 4b).

    **Why this re-emits a stored notice.** The notice lives on the manifest artifact's `warnings`,
    and no surface a host reads carries those: `job_tool.CommittedArtifactView` has no warnings
    field and `project` publishes none. Before ruling R858 an occlusion seal needed the probe to
    find no sun at all, so `illdir:<grid>` was all zero and `_compliance_lm83`'s
    `ASE_DIRECT_MATRIX_ALL_ZERO` named a seal as a possible cause. R858 seals a per-group
    `asemx:<grid>:<group>` while the rest of the grid is lit; the per-group blind schedule (and the
    blend it decides) then reads a matrix sealed on the probe's say-so, and without this nothing
    reachable says so. A per-group `ASE_DIRECT_ALL_ZERO_NO_SUN` seal had the same manifest-only gap
    before R858, and is repeated the same way.

    **Selected from what the call publishes, never from the grid's name.** `published` is each
    artifact whose numbers the payload carries (`illdir:<grid>`, the chain result, `sched:<grid>`
    where its per-group states are published), and the `asemx` records counted are those in the
    transitive `built_from` closure of them. Nothing removes an artifact from the manifest, so a
    grid-prefix match would repeat a seal from a group since renamed (an orphan) or from an earlier
    lm83 run whose geometry has since changed and whose re-run built no per-group legs (a stale
    record): neither is upstream of anything current. A record still upstream of what is published
    needs no validity check here either: an input or parameter change that invalidates it moves
    the recomputed digest of every artifact built from it (`rad_core.manifest.recompute_digests`
    carries an upstream's recomputed digest down its edges), so `_refuse_stale_results` refuses
    the call before any advisory is built. That covers changes to what a record was built from,
    not a hand-edited stored digest, which invalidates only the record it was written on. The
    `seen` set bounds the walk over a hand-edited cycle (R50, `_reachable_smx_edges`' reason); an
    edge naming no artifact (an input, or a deregistered id) is skipped.

    The stored message is published unchanged: it names the matrix (and so the group), what was
    checked and what to check before reporting.
    """
    seen: set[str] = set(published)
    frontier: list[str] = list(published)
    while frontier:
        current_id = frontier.pop()
        try:
            artifact = loaded.manifest.artifact(current_id)
        except KeyError:
            continue
        for edge in artifact.built_from:
            if edge not in seen:
                seen.add(edge)
                frontier.append(edge)
    return [
        warning_to_wire(warning)
        for artifact in loaded.manifest.artifacts
        if artifact.kind == "asemx" and artifact.id in seen
        for warning in artifact.warnings
        if warning.code in _ASE_SEAL_CODES
    ]


class _Nested(BaseModel):
    model_config = ConfigDict(extra="forbid")


# R810 moved this from the published text: `hours` is what `gendaymtx` wrote from the loaded
# `.wea`, never `ANNUAL_TIMESTEPS` (R47), and the shape is published on every action so a caller
# always sees what it is reading.
class ShapeView(_Nested):
    """The annual matrix's shape, read from the matrix file's own header.

    `hours` is the number of hourly columns the matrix holds, which follows the loaded weather file
    and need not be 8 760. `triples` is `points * hours`.
    """

    points: int
    hours: int
    triples: int
    """`points * hours` -- the quantity the inline bound is measured against for `timeseries`."""


# R810 moved this from the published text. `_Accumulator.add` refuses a non-finite lux rather than
# skipping it (whole-branch review, F5); upstream, `rad_engine.results` refuses it first
# (`_parse_row`/`_parse_lux_row` on tokens, `_stream_lux_from` on converted lux; fix round 2, N1),
# so a caller sees `ANNUAL_MATRIX_UNREADABLE`. `null`, not `0.0` or the accumulator's inf/nan
# sentinels: zeros would be substituted defaults reported as measured (CONTEXT.md), and inf/nan
# are not JSON. Only a zero-row matrix reaches the three `null`s; a matrix with rows and zero
# hours is refused (see `_Accumulator.view`). `point_in_time_tool.StatisticsView` shares this name
# and is deliberately non-nullable (one instant's non-empty list, plus `median` and
# `uniformity_u0`); `tests/test_results_tool.py` pins both halves.
class StatisticsView(_Nested):
    """Whole-matrix illuminance in lux, over every point and every hour.

    Never computed over a truncated list. A matrix holding a non-finite value is refused
    (`ANNUAL_MATRIX_UNREADABLE`) rather than summarised without it. `min`, `max` and `mean` are
    `null` only when the matrix has no sensor rows; `null` means no value was read, never 0 lux.
    """

    min: float | None
    max: float | None
    mean: float | None


# R810 moved this from the published text. docs/tool-surface.md §14 wrote one `value`; an annual
# point has 8 760, so `mean`/`min`/`max` are published instead and §14 was amended.
# `hours_above_threshold` (Phase 7b Task 16) is
# `rad_core.metrics.PointTally.result().hours_above[threshold_lux]`, the tally's strict `>`
# counter. Without a threshold the key is dropped by `_omit_hours_above_threshold_when_absent`
# rather than published `null` (CONTEXT.md: never a substituted default); `xyz: null` is
# different, a stated "not known" (see `_grid_positions`).
class PointView(_Nested):
    """One sensor's annual aggregate: `mean`, `min` and `max` lux over every hour.

    `id` is the zero-based row in the grid's point order. `xyz` is the position in metres from the
    grid's `.pts`, or `null` when the grid is no longer registered, is a zoned EN 12464 grid, cannot
    be read, or no longer matches the matrix's row count; the lux values stand either way.

    `hours_above_threshold` appears only when `threshold_lux` was given, and is absent otherwise. It
    counts hours strictly above the threshold (`>`), so it is not sDA's `>=` count even at 300 lux,
    nor ASE's at 1 000 lux, which counts 1 000 lux or more.
    """

    id: int
    """Zero-based row index in the `.ill`, which is the sensor grid's own point order."""
    xyz: list[float] | None
    """The sensor's position in metres, from the grid's `.pts`. `null` when the grid is no longer
    registered, is unreadable, or has drifted out of step with the matrix -- never a guess."""
    mean: float
    min: float
    max: float
    hours_above_threshold: int | None = None
    """`None` when `results(threshold_lux=...)` was not supplied for this call -- and then never
    published at all; see `_omit_hours_above_threshold_when_absent`.

    **Not sDA's own count, even at `threshold_lux=300`.** This is `PointTally.result().
    hours_above[threshold_lux]` -- strict `>` (Task 16 review Minor 3). sDA's per-point tally is
    `hours_at_or_above`, `>=` (ch04:38-40, inclusive). The two differ at every hour that sits
    exactly on the threshold, so a caller comparing this field against sDA's own 50%-of-window rule
    at 300 lux is comparing two different counts that happen to share a threshold value.

    **Not ASE's own count either, at `threshold_lux=1000`** (R861): ASE's per-point tally is
    `hours_at_or_above` too, since LM-83-12 section 2.2.6 defines direct sunlight as "1,000 lux or
    more", so this field and ASE's count differ at every hour of exactly 1 000 lux."""

    @model_serializer(mode="wrap")
    def _omit_hours_above_threshold_when_absent(self, handler: Any) -> dict[str, Any]:
        """Drop the key entirely when no threshold was asked for, rather than emit `null`.

        Scoped to this one field on this one model -- `RadResult.payload()`'s own `OMIT_WHEN_NONE`
        only ever pops top-level keys (`advisories`), and every other optional field on this file's
        models (`xyz`, `exclusion_reason`, EN 17037's `overall`) is a **stated** `null`: a caller
        asked a well-formed question and this is the answer, "not known"/"not applicable". A
        threshold nobody supplied is not that -- there is no question this field is answering, so
        there is no null to state, only a key that should not be there. A model-level
        `exclude_none=True` was rejected for the same reason `RadResult.payload()`'s own docstring
        rejects a blanket one: it would also silently delete `xyz: null`, which must stay.
        """
        data = handler(self)
        if self.hours_above_threshold is None:
            data.pop("hours_above_threshold", None)
        return data


class ResultsBase(RadResult):
    """What every `results`-shaped answer carries. Also the union schema's shared base.

    **The base is what `envelope.one_of_schema` inlines as the schema's top-level
    `properties`**, and top-level `properties` constrains the *instance*. So a base has to be a
    base: passing a concrete branch publishes that branch's `const`s as constraints on every other
    branch's payload. `results` did exactly that -- it passed `ResultsSummary`, whose `action` is
    `Literal["summary"]` and whose `unit` is `Literal["lux"]` -- and the published schema therefore
    rejected all nine of its other branches, `points` and `timeseries` included. Every other tool
    in this package already passes a base with no `const` field; this class is `results` joining
    them, not a new convention.

    Three fields, and each is here because **all nine successful branches carry it**:

    - `action` is typed `str`, not the `Action` `Literal`, and this is the `JobBase.state` pattern
      exactly: the discriminator belongs at the top level so a reader of the schema can see what
      the `oneOf` branches on, but it must be typed *wider* than any branch so each branch's own
      `Literal` narrows it rather than contradicting it. `Action` here would republish the same
      defect one level out -- an `enum` is a constraint too, and the day an action is dispatched
      that is not in that `Literal`, its payload is rejected by its own schema.
    - `project` and `grid` are the two arguments every answer echoes back.

    **`unit` is deliberately absent.** It was never shared: the four `compliance` branches and
    `blind_schedule` have no `unit` field at all, and it only reached the top level because
    `ResultsSummary` was standing in for a base.

    `Refusal` carries none of the three, and does not need to: `one_of_schema` inlines the base's
    `properties` but **not** its `required`, so these constrain the type of each key wherever it
    appears rather than demanding it appear. Which keys are mandatory stays each branch's business.
    """

    action: str
    project: str
    grid: str


class ResultsSummary(ResultsBase):
    """`action="summary"`: the matrix's shape and its whole-grid statistics. Never truncated -- the
    payload is a fixed handful of numbers regardless of how large the matrix is."""

    action: Literal["summary"] = "summary"
    artifact: str
    path: str
    method_used: MethodUsed
    """Which chain built the matrix this answered from -- `"3phase"`/`"5phase"` from the artifact's
    own `parameters["method"]`, or `"2phase"` when that key is absent (the two-phase seal's own
    shape).

    ~~Distinguishes an `ill:<grid>` answer from an `ill5:<grid>` fallback, which nothing else in
    this payload does.~~ **Struck at Phase 7d-1 Task 15a: it does not distinguish every pair, and
    it never was the only field that could.** `illblend:<grid>` records `method="3phase"` exactly
    as the unscheduled `ill:<grid>` does (ruling R158), so this value is identical for the blended
    and unblended results of one grid -- read `artifact`, which every one of these models also
    carries, to tell which matrix answered, and `analysis.blind_schedule_used` on an LM-83
    compliance result to tell whether the hours were chosen by a schedule."""
    shape: ShapeView
    statistics: StatisticsView
    unit: Literal["lux"] = "lux"


class ResultsPoints(ResultsBase):
    """`action="points"`: one annual aggregate per sensor.

    `points` is bounded: a list shorter than `n_points` was cut (`RESULT_TRUNCATED`). `statistics`
    still covers every point and every hour.
    """

    action: Literal["points"] = "points"
    artifact: str
    path: str
    method_used: MethodUsed
    """See `ResultsSummary.method_used`."""
    shape: ShapeView
    unit: Literal["lux"] = "lux"
    n_points: int
    """The real point count -- **not** `len(points)`. `len(points) < n_points` is what tells a caller
    the list was cut, and it is why both numbers are published."""
    points: list[PointView]
    statistics: StatisticsView
    """Over the full set, before truncation."""


class ResultsTimeseries(ResultsBase):
    """`action="timeseries"`: the selected points' hourly lux, for the selected hours.

    `values[i][j]` is point `point_ids[i]` at hour `hours[j]`, both zero-based indices into the
    matrix.
    """

    action: Literal["timeseries"] = "timeseries"
    artifact: str
    path: str
    method_used: MethodUsed
    """See `ResultsSummary.method_used`."""
    shape: ShapeView
    unit: Literal["lux"] = "lux"
    point_ids: list[int]
    """The rows returned, in the order returned -- echoed so a caller reading `values` never has to
    assume which points they asked for came back."""
    hours: list[int]
    """The hour indices returned, in the order returned."""
    values: list[list[float]]
    """`len(point_ids) x len(hours)` lux values."""


# R810 moved this from the published text: docs/tool-surface.md §14 publishes `{out, format,
# rows}`; `columns`, `shape` and the rest are this tool's additions.
class ResultsExport(ResultsBase):
    """`action="export"`: the whole matrix written as CSV to `out`, one row per sensor.

    `rows` counts sensor rows, the header line excluded; `columns` counts hourly lux values per row,
    after the leading `point_id` column.
    """

    action: Literal["export"] = "export"
    artifact: str
    out: str
    method_used: MethodUsed
    """See `ResultsSummary.method_used`."""
    format: Literal["csv"] = "csv"
    rows: int
    """Data rows written, excluding the header line."""
    columns: int
    """Values per data row, excluding the leading `point_id` column."""
    shape: ShapeView
    unit: Literal["lux"] = "lux"


# R810 moved this from the published text: every number here comes from `rad_core.metrics`; this
# model only shapes it for the wire.
class Lm83MetricView(_Nested):
    """One LM-83 percentage, sDA, ASE or ASE_net, and `artifact`, the annual result it was counted
    from.

    ASE is counted from the direct-sun result `illdir:<grid>` when one exists;
    `sda.artifact == ase.artifact` means it came from the chain result instead
    (`ASE_NOT_FROM_DIRECT_ONLY_RUN`). ASE_net is counted from `illdirnet:<grid>`, the direct sun
    with the window groups marked automated blacked; with no group marked it is `ase`.
    """

    value: float
    unit: Literal["%"] = "%"
    artifact: str
    """The artifact id this percentage was counted over.

    **Added Phase 7c Task 17, and it closes an absence-as-signal defect this task would otherwise
    double.** sDA and ASE come from two different matrices (Task 13): sDA from the chain result,
    ASE from `illdir:<grid>` whenever one is registered. `ResultsCompliance.artifact` names the
    sDA matrix only, so until now the payload's own statement about where the ASE number came from
    was the **absence** of `ASE_NOT_FROM_DIRECT_ONLY_RUN` -- a client had to know a code existed
    and reason about not seeing it in order to attribute a number to a file. No number was ever
    wrong; the *label* was missing, and `by_chain` doubles the ambiguity because each entry then
    carries two provenances of its own.

    Published on **both** metrics rather than on `ase` alone, deliberately. An `ase.artifact`
    standing beside a bare `sda.value` would leave a reader asking the same question about sDA
    that this field exists to answer about ASE, and the two are equal only in the fallback case --
    `sda.artifact == ase.artifact` is exactly the state `ASE_NOT_FROM_DIRECT_ONLY_RUN` describes,
    now readable from the numbers themselves rather than from an advisory's presence.

    **This is a change to the single-result payload as well as to `by_chain`**, and R113's
    *"byte-for-byte today's payload"* is deviated from on purpose: fixing the label only inside
    `by_chain` would give one field name two meanings depending on which shape a caller received,
    which is the defect one level up from the one being fixed."""


# R810 moved this from the published text: the bands come from
# `rad_core.compliance.sda_band`/`ase_band`, never computed here. LM-83 §2.4 returns sDA and ASE
# together or not at all, so there is no partial pairing to shape.
class Lm83BandsView(_Nested):
    """LM-83's judgement band for each metric; both are always present.

    `sda`: `"preferred"` (>= 75 %), `"nominally_accepted"` (>= 55 %) or `"below"`. `ase`:
    `"clearly_acceptable"` (< 3 %), `"neutral"` (< 7 %), `"unsatisfactory"` (> 10 %), or
    `"unclassified"` for 7-10 %, which LM-83-12 leaves unnamed; under LM-83-22,
    `"level_of_concern"` at 20 % or more (a value not yet checked against the printed standard), else
    `"below_level_of_concern"`. `ase_judged` names the value the `ase` band is judged on: `"ase"`
    under LM-83-12, `"ase_net"` under LM-83-22.
    """

    sda: str
    ase: str
    ase_judged: Literal["ase", "ase_net"]
    """R869: LM-83-22 section 3.2 states its 20 % line for ASE_net, so under that edition the band
    is `ase_band(ase_net.value, edition="LM-83-22")`; LM-83-12's research bands stay on `ase`."""


# R810 moved this from the published text: `SDA_REPORTED_ALONE` refuses before this model is ever
# built.
class Lm83View(_Nested):
    """The `lm83` block: `sda`, `ase` and `ase_net` always together, their `bands`, and the
    `edition`.

    `edition` is the LM-83 edition every threshold and band comes from (`"LM-83-12"`, or
    `"LM-83-22"` as asked); state it whenever reporting. `automated_groups` names the window groups
    blacked in the result `ase_net` was counted from, and is empty when no group is marked
    automated, where `ase_net` is `ase`.
    """

    edition: Lm83Edition = "LM-83-12"
    """The edition every threshold and band on this result comes from: the call's `lm83_edition`,
    `"LM-83-12"` when none was given (R864). `LM83_EDITION` carries the same fact as prose for a
    reader who reads the advisories and not the schema (ch04: *"Flag the edition whenever
    reporting"*). It said the type admits one value because the corpus held LM-83-12 only; R864
    made ANSI/IES LM-83-22 selectable, cited from ANSI/IES LM-83-22.
    This attribute text does not reach the output schema."""
    sda: Lm83MetricView
    ase: Lm83MetricView
    ase_net: Lm83MetricView
    """R869: ASE_net, published beside ASE under both editions. Counted from `illdirnet:<grid>`
    when a window group is marked automated (and refused, `ASE_NET_NOT_CURRENT`, when that result
    was not sealed with the groups marked now); otherwise the same value and artifact as `ase`, and
    an `illdirnet:<grid>` left by an earlier marked run is not read."""
    automated_groups: list[str] = []
    """R869: the groups `illdirnet:<grid>`'s sealed `ase_net_blacked` records -- what was blacked,
    read off the record rather than off the project's current marks, which are compared with it
    only to refuse a stale pairing. `[]` when no group is marked."""
    bands: Lm83BandsView


# R810 moved this from the published text: the period is reported with every metric per the
# periods table (docs/tool-surface.md#14-results). The old text called the blind schedule "the one
# input 7b does not build"; Phase 7d-1 built it, and `blind_schedule_used` reads the artifact
# (R178).
class ComplianceAnalysisView(_Nested):
    """The period and denominator LM-83's sDA and ASE were computed against.

    `period` is LM-83's 08:00-18:00 window and the time it was counted in: local clock time in
    `clock_zone`, daylight saving applied, when a zone was applied; else local standard time, with
    no daylight saving. `hours_total` is that window's hour count read from the matrix (3 650 for
    every whole year, 29 February excluded), not 8 760 and not EN 17037's daylight hours.
    `hours_required` is the hour count each point must reach for sDA (`>=`) and can be fractional.
    `points_excluded` is always 0: LM-83-12's exclusion of points whose yearly maximum is below 1
    lux is not applied (LM-83-22 has none).

    `blind_schedule_used` is `true` only for a blind-scheduled (blended) result. When `false`, every
    hour is the same blind state and `SDA_WITHOUT_BLIND_SCHEDULE` says so.

    `clock_zone` is the time zone the window was placed in, daylight saving included, with the
    changeover dates of `reference_year`. When both are `null` the window is in the weather file's
    standard time, and `LM83_CLOCK_ZONE_NOT_NAMED` or `LM83_CLOCK_ZONE_UNRESOLVED` says why.
    """

    period: str
    hours_total: int
    """`len(_lm83_window_for(...).columns)` -- the window as placed, shifted on the zone's clock or
    not; 3 650 for every whole year either way (10 columns a day, changeover days included, 29
    February excluded: R867), derived from the matrix's own header rather than a literal (R47)."""
    hours_required: float
    """`rad_core.metrics.sda_hours_required(window, fraction=LM83_DAYLIT_FRACTION)` -- the exact
    value `spatial_daylight_autonomy` compares each point's hour count against (`>=`, inclusive),
    **read from that function rather than recomputed here**, exactly as `UdiSpatialView.
    hours_required` is. This said `LM83_DAYLIT_FRACTION * hours_total`, an expression that no
    longer exists anywhere: the multiplication was hoisted into `sda_hours_required` so the number
    enforced and the number published cannot drift, and a docstring naming the arithmetic invites
    the next reader to reproduce it at a third site. **`float`, not `int`**:
    for any `hours_total` not a multiple of 2 (a matrix whose final day is truncated, so the last
    day's own window contribution is short of 10 columns) this is a genuine fraction -- 1 825.0 for
    a common year's 3 650, but not always a whole number -- and publishing `int(...)` of it
    silently understated the real requirement by up to 1 (Task 12 review, Important 1): a point at
    the truncated integer would read as compliant while `spatial_daylight_autonomy`'s own `>=
    required` (the untruncated float) judged it short."""
    points_total: int
    points_excluded: int
    """Always `0` this task -- LM-83's yearly-maximum-below-1-lux exclusion (ch04, `ANALYSIS_AREA_
    EXCLUDED`) is not implemented here; see the module docstring's layering note. Reported as `0`
    rather than omitted, because `0` is the true count of points *this action* excludes, not a
    substituted default standing in for arithmetic that was skipped."""
    exclusion_reason: str | None
    blind_schedule_used: bool
    """**True exactly when this result was read from `illblend:<grid>`** -- Phase 7d-1 Task 15a,
    ruling R178. Every hour of a blended matrix is the per-group, per-state contribution the sealed
    `sched:<grid>` selected, so the sDA tallied from it *was* computed against LM-83 §2.2.6's
    schedule.

    ~~Still always `False` after Phase 7c: sDA is not blind-aware until Phase 7d, which blends a
    blinds-open and a blinds-closed result hour by hour (ruling R107).~~ Retired with the blend it
    named as future. `False` still means what it always meant: an unscheduled total, where every
    hour is the same bound BSDF state, and `SDA_WITHOUT_BLIND_SCHEDULE` is attached beside it
    naming LM-83 §2.2.6's escape clause rather than silently implying a schedule nothing applied.

    **Read off the ARTIFACT, never off `method_used`.** The blend records `method="3phase"` exactly
    as the unscheduled `ill:<grid>` does (ruling R158), so `method_used` cannot tell them apart and
    a check written against it would publish `false` on every blend."""
    clock_zone: str | None = None
    """R860: the zone `_lm83_window_for` placed the window in -- the weather input's recorded
    `clock_zone` -- or `None` when the window is in the EPW's standard time."""
    reference_year: int | None = None
    """`rad_engine.clock.REFERENCE_YEAR` when `clock_zone` is set: the year the EPW rows' dates, and
    so the daylight-saving changeovers, were placed in. `None` when `clock_zone` is."""


# R810 moved this from the published text: `standard="en12464"` refuses in
# `_refuse_standard_deferred` and a one-sided `metrics` in `_validate_lm83_metrics`
# (`SDA_REPORTED_ALONE`); both fire before this model is built, so its presence guarantees both
# metrics. `standard="en17037"` is `ResultsComplianceEn17037`.
class ResultsCompliance(ResultsBase):
    """`action="compliance"`, `standard="lm83"`: sDA and ASE together, as LM-83 requires.

    `artifact`, `path` and `method_used` describe the matrix sDA was counted from; ASE's own is
    `lm83.ase.artifact`. `standard="en12464"`, and `metrics` naming only one of sDA and ASE
    (`SDA_REPORTED_ALONE`), are refused, so this result always carries both.
    """

    action: Literal["compliance"] = "compliance"
    artifact: str
    """The annual result `sDA` was tallied from. **Not necessarily the matrix `ase` came from**:
    when `illdir:<grid>` is registered, ASE is that direct-sun matrix's own number (LM-83 §3.3) and
    only sDA reads the artifact named here. **`lm83.ase.artifact` names ASE's own matrix
    directly** (Phase 7c Task 17), so a client reads provenance off the payload rather than
    inferring it from an absent advisory. This docstring pointed at that absence --
    *"the absence of `ASE_NOT_FROM_DIRECT_ONLY_RUN` means ASE came from the direct matrix"* --
    which was true and is now the long way round: absence-as-signal is exactly what Task 17
    published the field to replace. The code still fires when it is true; it is no longer how a
    client is expected to find out."""
    path: str
    """The filesystem path of `artifact`, above -- likewise the sDA matrix's."""
    method_used: MethodUsed
    """See `ResultsSummary.method_used`. **The chain that wrote the matrix `sda` was tallied from,
    which is not necessarily the one `ase` came from.** Until Phase 7c Task 13 this was always
    `"5phase"` on a successful `lm83` result; it can now be `"2phase"` or `"3phase"` too, because
    ASE is tallied from `illdir:<grid>` -- a dedicated zero-bounce direct-sun run that any chain
    can seal -- and `ASE_FROM_SMEARED_SUN` now refuses only when there is no such matrix *and* the
    chain result cannot supply the number either."""
    standard: Literal["lm83"] = "lm83"
    lm83: Lm83View
    analysis: ComplianceAnalysisView


# R810 moved this from the published text: this is `ComplianceAnalysisView`'s sibling, over a
# genuinely different period.
class En17037AnalysisView(_Nested):
    """The period and denominator EN 17037's daylight-provision F_plane was computed against.

    `period` is the daylight hours: the half of the weather file's hours with the highest diffuse
    horizontal illuminance, not LM-83's 08:00-18:00 window; the two are not interchangeable
    (`METRICS_NOT_CONVERTIBLE`). `hours_total` is that count, read from the weather file;
    `hours_required` is half of it, rounded down, the hours each point must reach (`>=`).
    `points_excluded` is always 0.
    """

    period: str
    hours_total: int
    """`rad_core.periods.DaylightHours.count` -- half of the EPW's own hourly record count, read
    from the file rather than a literal `4380` (R47's reasoning, applied to this period the same
    way `ComplianceAnalysisView.hours_total` applies it to LM-83's own 3 650)."""
    hours_required: float
    """`DaylightHours.required` -- half of `hours_total` again, the hour count each point's tally
    is compared against (`>=`, inclusive -- ch03:15-16's own "at least half the daylight hours").

    **`float`, not `int` (coordinator review, Task 13)** -- matching `ComplianceAnalysisView.
    hours_required`'s own type on the LM-83 side (and docs/tool-surface.md's published type for
    the field name both models share): one field name must not answer with two different wire
    types depending on which standard built it. `DaylightHours.required` is `count // 2`, always a
    whole number in practice (EN 17037's denominator is never truncated the way an odd LM-83
    window can be), but the type is still published as `float` so a caller reading either
    `analysis.hours_required` never has to branch on `standard` to know what type to expect."""
    points_total: int
    points_excluded: int
    """Always `0` this task -- no exclusion rule is implemented for EN 17037's provision axis; see
    `ComplianceAnalysisView.points_excluded`'s identical posture on the LM-83 side."""
    exclusion_reason: str | None


# The description used to say "this task computes" and ended "per the module docstring's "do not
# undo it" note" (a note the module docstring does not carry under that name). Both pointers left
# the published description under R809 (item 38).
# R810 moved this from the published text: `en17037` is `rad_core.compliance.en17037_object`'s own
# dict, unchanged (Table A.1/A.2, ch03 5.1/A.2); this model does not re-encode its `assessed:
# false` or `overall: None` guarantees.
class ResultsComplianceEn17037(ResultsBase):
    """`action="compliance"`, `standard="en17037"`, `method="2"`: daylight provision only.

    `en17037.daylight_provision` is the one criterion computed. `view_out`, `sunlight_exposure` and
    `glare` read `{"assessed": false}` (`EN17037_CRITERIA_NOT_ASSESSED`), and `overall` is always
    `null`: EN 17037 has no composite score, so this result gives no overall verdict.
    `daylight_provision.level_met` is the requested `level` when `meets_level` is true and `null`
    when it is false: a `null` says the requested level was not met, not that no level was.
    When `daylight_provision.opening_class` is "horizontal" (Table A.2) it has no `minimum_lux`,
    `f_plane_at_minimum` or `f_plane_at_minimum_required` key.
    """

    action: Literal["compliance"] = "compliance"
    artifact: str
    path: str
    method_used: MethodUsed
    """See `ResultsSummary.method_used`."""
    standard: Literal["en17037"] = "en17037"
    en17037: dict[str, Any]
    analysis: En17037AnalysisView


# R810 moved this from the published text: a code-less refusal is one this module leaves without a
# code for `_NoAnnualResult`'s stated reason (an argument naming a handle inside the project).
# `code` is published `null` rather than omitted because "the register has no code for this" is a
# fact about the refusal.
class ComplianceRefusalView(_Nested):
    """Why one `by_chain` entry was refused: `reason`, and `code`, the entry's first advisory code.

    `code` is `null` when the refusal carries no advisory code; the entry's `advisories` hold the
    full list.
    """

    code: str | None
    reason: str


class _ChainEntry(_Nested):
    """One `by_chain` entry: **whole, or a refusal, and never half** (ruling R113).

    `reported` is the flag, and the two shapes are mutually exclusive by construction -- a reported
    entry carries the standard's own block and its `analysis`, a refused one carries `refusal` and
    neither. The plan's own check 7.19 asked for the third shape (a three-phase entry carrying sDA
    beside a refused ASE) and **that payload cannot be built**: `Lm83View` requires both metrics,
    `Lm83BandsView` requires both bands as `str`, `_Nested` sets `extra="forbid"`, and
    `_refuse_sda_alone` states the rule -- *"together or not at all ... there is no legitimate
    reading in which one is published without the other."* The check's shape is corrected in
    `docs/plan.md` rather than worked around here.

    **The absent half is absent, not null.** `RadResult.payload`'s `OMIT_WHEN_NONE` applies to a
    tool result's own top level and nothing walks into a nested model, so the two conventions it
    keeps -- drop a `None` the schema calls optional, drop an advisory's `field` when it has none
    -- are applied here by this model's own serializer. Without it a refused entry would publish
    `"lm83": null` beside its `refusal`, which reads as *"this entry has an LM-83 block and it is
    empty"* rather than *"this entry has no LM-83 block"*, and every nested advisory would carry a
    `"field": null` that no root advisory carries. One wire contract, not two spellings of it.
    """

    reported: bool
    artifact: str
    """The annual result this entry was resolved to -- `ill:<grid>`, `ill5:<grid>` or
    `illblend:<grid>`. Per entry, because the root of a `by_chain` payload has no single artifact
    to name."""
    path: str
    method_used: MethodUsed
    """The chain that wrote `artifact`. **It is no longer the entry's own key in `by_chain`**
    (Phase 7d-1 Task 15a, ruling R174): `illblend:<grid>` records `method="3phase"` exactly as the
    unscheduled `ill:<grid>` does (R158), so keying on this value alone put two results under one
    key and dropped one. The key is `results_tool._by_chain_key` -- this value, plus `"+lm83"`
    where the result was blended. The *artifact id* is still not the key, for the reason this
    sentence has always given: `"2phase"` and `"3phase"` both seal `ill:<grid>`, so the id would
    name the chain less precisely than the record does."""
    refusal: ComplianceRefusalView | None = None
    advisories: list[Advisory] | None = None
    """**This entry's own advisories, never the call's.** The two matrices can differ in column
    count and in what is true of them: `NEGATIVE_ILLUMINANCE_PRESENT` is the five-phase
    combination's own subtraction and is not a statement `V*T*D*S` can make, so one hoisted list
    would be a claim about both entries that is true of at most one -- and an advisory untrue of
    its own condition is a defect in its own right (docs/tool-surface.md §9)."""

    @model_serializer(mode="wrap")
    def _omit_absent(self, handler: Any) -> dict[str, Any]:
        data = handler(self)
        data = {key: value for key, value in data.items() if value is not None}
        for advisory in data.get("advisories") or []:
            if advisory.get("field") is None:
                advisory.pop("field", None)
        return data


# R810 moved this from the published text: R113 - the three-phase arm's ASE "is not merely absent,
# it is refused for a stated reason a caller can read".
class Lm83ChainEntryView(_ChainEntry):
    """One `by_chain` entry for `standard="lm83"`.

    When `reported` is `true` it carries `lm83` and `analysis`; when `false`, `refusal` instead, and
    the missing blocks are absent, not `null`. `artifact`, `path`, `method_used` and `advisories`
    belong to this entry, not the others; `lm83.ase.artifact` may name the `illdir:<grid>` every
    entry shares. A two- or three-phase entry is refused with `ASE_FROM_SMEARED_SUN` when the grid
    holds no direct-sun result `illdir:<grid>`.
    """

    lm83: Lm83View | None = None
    analysis: ComplianceAnalysisView | None = None


# R810 moved this from the published text: EN 17037's provision axis has no method gate and needs
# no direct matrix, so entries are reported far more often than on the LM-83 arm; the only
# per-entry refusals are about the matrix itself.
class En17037ChainEntryView(_ChainEntry):
    """One `by_chain` entry for `standard="en17037"`, `method="2"`.

    When `reported` is `true` it carries `en17037` and `analysis`; when `false`, `refusal` instead,
    and the missing blocks are absent, not `null`. `advisories` belong to this entry, not the
    others.
    """

    en17037: dict[str, Any] | None = None
    analysis: En17037AnalysisView | None = None


#: The two `by_chain` entry shapes, as one type variable, so `_refused_chain_entry` builds either
#: without a second copy of itself. Bound to `_ChainEntry` rather than left free: what that helper
#: actually needs is the base's own fields, and the bound is what says so.
_ChainEntryT = TypeVar("_ChainEntryT", bound=_ChainEntry)


# R810 moved this from the published text: ruling R113, the end state of R105. The single-result
# keys are not fields here because each describes one matrix; publishing them `null` beside
# `by_chain` would be a second, emptier answer. Root advisories are omitted because the register
# allocates no code for "two results reported side by side", and inventing one is what §9 forbids.
class ResultsComplianceByChain(ResultsBase):
    """`standard="lm83"` compliance on a grid with several annual results and no `chain`.

    `by_chain` holds one entry per result, keyed by chain (`"2phase"`, `"3phase"`, `"5phase"`), with
    `"+lm83"` appended for a blind-scheduled (blended) result. The root has no `artifact`, `path`,
    `method_used`, `lm83` or `analysis`, and no advisories: each entry carries its own.
    """

    action: Literal["compliance"] = "compliance"
    standard: Literal["lm83"] = "lm83"
    by_chain: dict[str, Lm83ChainEntryView]


# R810 moved this from the published text: two models rather than one discriminated on `standard`,
# matching the `ResultsCompliance`/`ResultsComplianceEn17037` split; one model would have to admit
# both entry shapes as optional, a key `null` only because the other standard was asked for.
class ResultsComplianceEn17037ByChain(ResultsBase):
    """`ResultsComplianceByChain`'s shape for `standard="en17037"`: one entry per annual result.

    Keyed the same way; the root carries no `artifact`, `en17037`, `analysis` or advisories.
    """

    action: Literal["compliance"] = "compliance"
    standard: Literal["en17037"] = "en17037"
    by_chain: dict[str, En17037ChainEntryView]


# R810 moved this from the published text (docs/plan.md check 7.14). No `per_group` flag: it was
# `Literal[False]`, a constant no client can branch on; `ResultsBlindSchedule.groups` being `null`
# carries that fact (R116), and since Phase 7d-1 Task 14 it is live. The closing order (`order`,
# `order_substituted`) stands on `ResultsBlindSchedule`, not here: it describes the sealed
# per-group schedule, and every field here describes the whole-grid trigger from `illdir:<grid>`.
class BlindTriggerView(_Nested):
    """The whole-grid trigger that decided `states`: LM-83 section 2.2.6's inputs, or LM-83-22
    section 4.6's under `lm83_edition="LM-83-22"`.

    `threshold_lux` is LM-83-12 section 2.2.6's direct sunlight, "1,000 lux or more", also ASE's
    threshold (`threshold_provenance: "sourced"`); a point at or above it is in sun
    (`comparison`). `points_total` is the direct-sun matrix's sensor count. `area_m2`/`area_sf` are
    the area of the polygon `sensor_grid` recorded, and `area_band_sf` is the section 2.2.6 row that
    area selects. Under LM-83-22 they are the grid's points times their spacing, and
    `area_band_sf` is `"flat_2pct"`: more than 2 % of the points in sun, at every area.

    `coverage` is the fraction of the polygon the grid's sensors sample, `null` when unknown (never
    0 for unknown). `points_expected` is how many sensors LM-83's placement rule puts in the same
    polygon, `null` when it cannot be gridded. Under LM-83-12 only, a grid with fewer than half the
    sensors that rule places is refused below 1 000 sf and warned at or above
    (`BLIND_TRIGGER_MIXED_BASIS`).
    """

    threshold_lux: float
    """The illuminance at or above which a point counts as receiving direct sunlight, in lux.
    Always `rad_core.metrics.LM83_ASE_THRESHOLD_LUX` -- see `threshold_provenance`."""
    threshold_provenance: Literal["sourced"]
    """`"sourced"`, always: LM-83-12 section 2.2.6 defines direct sunlight as "1,000 lux or more"
    (R861; it was `"default"` under R110, whose premise that LM-83 states no threshold was false).
    It is not caller-settable: `results(threshold_lux=...)` is `action="points"`' argument and is
    not read here."""
    comparison: Literal[">="]
    """`>=`, ASE's comparison too (R861): a cell at exactly 1 000.0 lux is in sun, so the trigger
    and ASE agree at the threshold they share."""
    points_total: int
    """The denominator the 2 % rule and the point-count corrections are measured against -- the
    direct matrix's own `NROWS`, i.e. the sensors that were actually measured."""
    area_m2: float
    area_sf: float
    """LM-83's table is in square feet; `rad_core.grids.M2_TO_SQFT` converts once, in
    `_analysis_area` (and in `rad_core.grids.lm83_points_area_sf` under LM-83-22)."""
    area_band_sf: str
    """Which rule applied -- `rad_core.blinds.BlindSchedule.band`: under LM-83-12 the row of
    section 2.2.6's table, formatted from the same edges `blinds_close` branches on; under LM-83-22
    always `rad_core.blinds.BAND_FLAT` (`"flat_2pct"`), section 4.6's one rule."""
    area_source: str
    """Where the area came from. Under LM-83-12 `_AREA_SOURCE_POLYGON`: the shoelace area of the
    polygon `sensor_grid(action="create")` recorded, never a spacing and never the sensor cloud's
    extent. Under LM-83-22 `_AREA_SOURCE_POINTS`: the glossary's points times the row and column
    spacings read off the grid's own `.pts` (R864)."""
    coverage: float | None
    """What fraction of the polygon's floor area the placed grid's own sensor cells sample, or
    `null` when the grid's own `.pts` could not be read or supports no cell size. Never `0.0` for
    "not known".

    **An area, not a density**, and it is not what this action refuses on -- see `points_expected`.
    A bounding-box ratio until Phase 7d-1 Task 17 (ruling R160), which is why a value read from an
    older payload is not comparable with one read from this."""
    points_expected: int | None
    """How many sensors LM-83's own placement rule would put inside the grid's recorded polygon --
    under LM-83-12 the **same** polygon `area_sf` came from; under LM-83-22, whose `area_sf` is
    points times spacing, still that polygon, and no guard reads it
    (`rad_core.grids.lm83_grid_point_count`) -- or `null` where that polygon cannot be gridded.

    **Published so the two halves of §2.2.6's rule are visible as one basis or two** (Phase 7d-1
    Task 16). The standard selects a row off the area and then applies that row's *absolute point
    count*; `points_total` beside this field is the matrix's own `NROWS`, and where the two differ
    materially the row is being applied to a grid it was not calibrated for. `points_total /
    points_expected` is the density `rad_core.grids.LM83_GRID_POINT_DENSITY_MIN` is judged against
    under LM-83-12 only; below 1 000 sf a shortfall is refused (`BLIND_TRIGGER_MIXED_BASIS` at
    `error`) and at or above it is recorded (the same code at `warn`), because only below 1 000 sf
    does the error run in the flattering direction.

    **Not the same question as `coverage`.** That is a ratio of areas and says how much of the
    room's floor the grid actually *samples*; this is a count. A correctly-placed LM-83 grid in a
    1.2 x 40 m corridor reads `coverage` 0.495 with a density of exactly 1.000."""


# R810 moved this from the published text (Phase 7d-1 Task 14). The whole-grid half is derived
# exactly as in Phase 7c (R107: a pure function of `illdir:<grid>`, the polygon's area and the
# threshold -- under LM-83-22 of the matrix and the threshold alone, R864 -- so recomputing it is
# deterministic). The per-group half is read from `sched:<grid>`,
# built over per-group matrices whose octrees black every other group's aperture;
# `rad_engine.blend` consumes the sealed file, not this payload.
class ResultsBlindSchedule(ResultsBase):
    """`action="blind_schedule"`: LM-83 section 2.2.6's hourly blind states, and what decided them
    (LM-83-22 section 4.6's under `lm83_edition="LM-83-22"`).

    `states`, `hours_closed` and `trigger` are the whole-grid schedule, derived from `matrix`
    (always `illdir:<grid>`, the direct-sun result) with every aperture open. `states` is parallel
    to `columns`, the matrix hour columns of LM-83's 08:00-18:00 window. Never split `states` across
    window groups: it is an upper bound on closure, and doing so understates sDA.

    `clock_zone` is the time zone that window was placed in, daylight saving included, with the
    changeover dates of `reference_year`; both `null` mean standard time, and
    `LM83_CLOCK_ZONE_NOT_NAMED` or `LM83_CLOCK_ZONE_UNRESOLVED` says why.

    `groups` (one state list per window group, parallel to `columns`), `order` (first closed first)
    and `order_substituted` come from the per-group schedule a `run_annual(...,
    blind_control="lm83")` run sealed, and are all `null` when the project holds no usable one
    (`BLIND_SCHEDULE_NOT_PER_GROUP` says why). `order_substituted: true` means the order came from
    sill heights (`BLIND_ORDER_SUBSTITUTED`). The two schedules are different arithmetic and not
    comparable; `hours_closed` counts the whole-grid one.

    `automated` maps each window group marked automated to `"bound_schedule"` or `"held_closed"`:
    its states came from its bound hourly schedule, or it was held closed in every hour. Such a
    group is in `groups` and not in `order`; its sunlight is outside the closing trigger under
    LM-83-22 and its open hours count toward it under LM-83-12. `{}` when no group is automated,
    `null` with `groups`.
    """

    action: Literal["blind_schedule"] = "blind_schedule"
    matrix: str
    """The artifact id the schedule was derived from -- `illdir:<grid>`, always. Published rather
    than implied because *which* matrix a direct-sun trigger read is the whole claim: the same
    arithmetic over a chain result would be a smeared-sun schedule wearing this one's name."""
    path: str
    shape: ShapeView
    """The direct matrix's own shape, from its own header."""
    states: list[str]
    """One `"open"`/`"closed"` per column of `columns`, for the **whole grid** -- the trigger over
    `illdir:<grid>`, with every aperture open.

    **Never distribute this over window groups**, whether or not `groups` is populated beside it.
    The whole-grid in-sun count is greater than or equal to any single group's residual count, so
    each of these states is a strict **upper bound on closure**: a client that fans this list out
    per group closes windows the standard leaves open, loses daylight and **understates sDA**,
    one-directionally. `groups` is the per-group answer, and it is derived from different matrices
    by different arithmetic -- not from this list."""
    columns: list[int]
    """The analysis window's matrix columns, ascending. Parallel to `states`: without it a caller
    cannot map a state back to an hour of the year, because the window is LM-83's fixed 08:00-18:00
    and not a contiguous run."""
    clock_zone: str | None = None
    """R860: the zone `_lm83_window_for` placed `columns` in -- the weather input `illdir:<grid>`'s
    `smxsun:` edge reaches, when it records one -- or `None` for the EPW's standard time."""
    reference_year: int | None = None
    """`rad_engine.clock.REFERENCE_YEAR` when `clock_zone` is set, else `None`."""
    groups: dict[str, list[str]] | None = None
    """LM-83 section 2.2.6's **per window group** schedule, one `"open"`/`"closed"` list per group,
    each parallel to `columns` -- or `null` where this project holds no sealed one.

    **Read from `sched:<grid>`, never re-derived here** (Phase 7d-1 Task 14, filling the slot owner
    ruling R116 reserved). A `run_annual(..., blind_control="lm83")` run traces one direct-sun
    matrix per group with every *other* group's aperture blacked, and `rad_engine.schedule` runs
    section 2.2.6's prefix scan over them and seals the answer; this action publishes that file.
    It cannot be recomputed from `illdir:<grid>`, which is one matrix for the whole grid: a count
    of points in sun there cannot be attributed to the group that lit them, which is what R116
    measured and why the slot was left `null` rather than fanned out.

    **`null`, and never absent, whenever there is no sealed schedule to publish.** The key states
    that this dimension exists; a project that never ran `blind_control="lm83"` has no per-group
    matrices and therefore no answer, and `BLIND_SCHEDULE_NOT_PER_GROUP` names which of the reasons
    applied. What retired at Task 14 is that code's **unconditional** form, not the code.

    **Sliced to `columns`.** The sealed file covers every column of the annual matrix, because
    `rad_engine.blend` refuses a schedule that is not exactly `--hours` long and blinds are a state
    of the building in every hour; what is published here is its analysis-window slice, so these
    lists stay parallel to `columns` and the published shape does not move (ruling R163). A caller
    that needs the off-window hours reads the artifact.

    **`hours_closed` is not this schedule's count.** It is the whole-grid trigger's, over
    `columns`; the sealed file's own `hours_closed` counts columns of the whole matrix in which
    *any* group closed, over per-group matrices. Different subject and different denominator, and
    they are not comparable in either direction (ruling R164) -- which is why only one of them is
    published.
    """
    order: list[str] | None = None
    """The closing order the sealed prefix scan ran in, first-closed first -- or `null` with
    `groups`.

    Published because ordering *is* what decides which groups close: section 2.2.6 lets groups
    close *"in any combination until the criterion is met"* and its DISCUSSION advises closing
    "view" groups first and "clerestory" groups last, so reading that advice as the **search**
    order is this server's own reading and the order it used is part of the answer, not an
    implementation detail. Read from the sealed file, which lists the groups in exactly the order
    its scan closed them in."""
    order_substituted: bool | None = None
    """Whether that order was **substituted** rather than declared -- or `null` with `groups`.

    `true` when any window group's `closing_role` was not one of the two classes the DISCUSSION
    names, in which case the order was taken from each group's sill height, lowest aperture first.
    A substituted default is never reported as a measured value (CONTEXT.md), so this field is
    published *and* `BLIND_ORDER_SUBSTITUTED` is emitted at `warn` naming the substitution and what
    is known about its direction."""
    automated: dict[str, Literal["bound_schedule", "held_closed"]] | None = None
    """R869/R870: each window group marked automated in the sealed schedule, mapped to where its
    states came from -- `"bound_schedule"`, the hourly schedule bound to it, or `"held_closed"`,
    closed in every hour because none was bound. Read from the sealed file's `automated_sources`.
    `{}` on a sealed schedule with no automated group; `null` with `groups`. An automated group is
    in `groups` and never in `order`."""
    hours_closed: int
    """How many of `columns` the blinds are closed for, **on the whole-grid trigger** -- not on
    `groups`. See `groups` for why the sealed schedule's own count is a different number and is not
    published beside this one (ruling R164)."""
    trigger: BlindTriggerView


# --------------------------------------------------------------------------------------------
# `action="metrics"` -- UDI and DA, measured, with no verdict (Phase 7c Task 18)
# --------------------------------------------------------------------------------------------


# R810 moved this from the published text: one block per metric, because UDI's occupied mask and
# DA's LM-83 window differ; one shared block would publish `da_300` under a denominator not its
# own, and `rad_core.metrics._ensure_window_matches` exists to catch a tally built against the
# wrong window. Not `ComplianceAnalysisView`: its extra fields are about a judged result, and this
# action judges nothing.
class MetricsAnalysisView(_Nested):
    """The period and denominator one metric was computed against; `udi` and `da` each carry one.

    DA uses LM-83's 08:00-18:00 window (`provenance: "sourced"`); its `period` says whether that
    window was counted on `clock_zone`'s clock or in local standard time. UDI uses assumed weekday
    occupied hours (`provenance: "default"`, `UDI_PERIOD_ASSUMED`), because no source states a
    period for UDI. `hours_total` is read from the matrix, so the two differ (2 600 and 3 650 for a
    common year).

    On `da`, `clock_zone` is the time zone the window was placed in, daylight saving included, with
    the changeover dates of `reference_year`; both `null` mean standard time, and
    `LM83_CLOCK_ZONE_NOT_NAMED` or `LM83_CLOCK_ZONE_UNRESOLVED` says why. Always `null` on `udi`,
    whose occupied hours stay in standard time.
    """

    period: str
    hours_total: int
    """`len(occupied_hours(header.hours))` for UDI, `len(_lm83_window_for(...).columns)` for DA
    (the window as placed, R860; the count is the same shifted or not) --
    derived from the matrix's own header, never a literal (R47). A leap-year matrix answers 2 610
    for UDI and 3 650 for DA, whose window leaves 29 February out (R867)."""
    provenance: Literal["sourced", "default"]
    """`"sourced"` for DA -- LM-83 ch04:38-40 states the 08:00-18:00 window. `"default"` for UDI:
    **no source in this corpus states a period for UDI at all** (ruling R111), so the occupied mask
    is a labelled substitution and `UDI_PERIOD_ASSUMED` says so in prose beside it. The field exists
    so a client can branch on it without parsing an advisory, which is CONTEXT.md's *"never report a
    substituted default as a measured value"* made machine-readable. It has two values rather than
    one for the same reason `PointView.hours_above_threshold` is absent rather than null: a label
    that is constant carries no information."""
    points_total: int
    clock_zone: str | None = None
    """R860, on `da` only: the zone `_lm83_window_for` placed DA's window in, or `None` for
    standard time. Always `None` on `udi`."""
    reference_year: int | None = None
    """`rad_engine.clock.REFERENCE_YEAR` when `clock_zone` is set, else `None`."""


# R810 moved this from the published text: one name for "% of area" and "% of hours" is R103's
# defect in a new hat. Structurally an sDA (`rad_core.metrics.spatial_udi`); dropping the second
# threshold would give the mean per-point percentage.
class UdiSpatialView(_Nested):
    """Spatial UDI: percent of sensor points (the analysis area), not of hours.

    A point counts when it is in the 100-2 000 lux band for at least `hours_required` hours of the
    period (`>=`; can be fractional). This is not the per-point `udi_100_2000` and not its mean.
    """

    area_fraction_in_band: float
    unit: Literal["%"] = "%"
    hours_required: float
    """`udi_hours_required(window)` -- the exact count `spatial_udi` compares each point's band
    count against (`>=`, inclusive), **read from that function rather than recomputed here**.
    `sda_hours_required`'s own lesson: a published requirement that is not the enforced requirement
    is a false statement about the number beside it. `float`, not `int`, for the same reason
    `ComplianceAnalysisView.hours_required` is -- `int()` understates it by up to 1 on an odd
    window."""


# R810 moved this from the published text: R106's three bins (docs/tool-surface.md section 11).
# The lowest edge is `-inf`, not 0, because the five-phase combination subtracts (2 336-3 113
# negative cells in the runs recorded so far; draws, not bounds -- R814). Each bin is `rad_core.metrics.useful_daylight_illuminance` over its
# band, counted by `PointTally.observe`'s band loop, never derived by subtracting threshold
# counters.
class UdiPointView(_Nested):
    """One sensor's UDI: three percentages of the analysis period.

    `udi_below_100` (< 100 lux), `udi_100_2000` (100 to < 2 000 lux) and `udi_at_or_above_2000` (>=
    2 000 lux) sum to 100: negative lux, which a five-phase matrix can hold
    (`NEGATIVE_ILLUMINANCE_PRESENT`), counts as below 100. `xyz` is `null` on the same conditions as
    `PointView.xyz`.
    """

    id: int
    xyz: list[float] | None
    """The sensor's position from the grid's own `.pts`; `null` -- never guessed -- on the same
    conditions `PointView.xyz` states."""
    udi_below_100: float
    udi_100_2000: float
    """UDI proper: `>= UDI_BAND_LOWER_LUX` **and** `< UDI_BAND_UPPER_LUX` (R106 -- exactly 100 lux
    is useful, exactly 2 000 lux is exceeded). **This is the per-point figure**; the grid's own is
    `spatial.area_fraction_in_band` and shares no key with it."""
    udi_at_or_above_2000: float


# R810 moved this from the published text: R106 (the contested upper bound) and R111 (no sourced
# period).
class UdiView(_Nested):
    """The `udi` block of `action="metrics"`: measured values, with no band, level or verdict.

    UDI is not a standard: its 2 000 lux upper bound is contested and no source states its period.
    `band_lower_lux` and `band_upper_lux` are the edges every percentage was counted against.
    `points` is bounded; the result's `n_points` is the real count.
    """

    band_lower_lux: float = UDI_BAND_LOWER_LUX
    band_upper_lux: float = UDI_BAND_UPPER_LUX
    """The two edges every percentage above was counted against, published rather than left to be
    read off a key name -- and read from `rad_core.metrics`, so the numbers on the wire and the
    numbers `PointTally.observe` compared against cannot drift apart."""
    unit: Literal["%"] = "%"
    analysis: MetricsAnalysisView
    spatial: UdiSpatialView
    points: list[UdiPointView]
    """Bounded at `MAX_INLINE_VALUES`; `ResultsMetrics.n_points` carries the real count."""


# R810 moved this from the published text: the source is LM-83 ch04:38-40 (`>=`).
class DaPointView(_Nested):
    """One sensor's DA_300: percent of LM-83's 08:00-18:00 window hours at or above 300 lux."""

    id: int
    xyz: list[float] | None
    da_300: float


# R810 moved this from the published text: the same reasoning
# `_SUMMARY_SDA_REPORTED_ALONE_ADVISORY` applies to `action="summary"`, reused rather than
# restated.
class DaView(_Nested):
    """The `da` block of `action="metrics"`: per-point DA_300 only.

    There is no spatial figure: DA_300's spatial aggregate is sDA, which LM-83 does not allow
    reported without ASE (`SDA_REPORTED_ALONE` is attached). `results(action="compliance",
    standard="lm83")` reports the pair. `points` is bounded; the result's `n_points` is the real
    count.
    """

    threshold_lux: float = LM83_SDA_THRESHOLD_LUX
    unit: Literal["%"] = "%"
    analysis: MetricsAnalysisView
    points: list[DaPointView]


# R810 moved this from the published text: `compliance` keeps meaning "judged against a standard",
# and neither metric has one.
# `tests/test_results_tool.py::TestNoVerdictIsPublishedAnywhereInThisAction` walks the payload
# structurally, so advisory prose about the contested band may use the word. `udi`/`da` are
# omitted via `OMIT_WHEN_NONE`; a `null` would be a second, emptier answer.
class ResultsMetrics(ResultsBase):
    """`action="metrics"`: UDI and DA as measured values, judged against no standard.

    No bands, level or verdict are published. `udi` and `da` are present when `metrics` names them
    (both when it is omitted) and absent, not `null`, otherwise. A `points` list shorter than
    `n_points` was cut (`RESULT_TRUNCATED`).
    """

    action: Literal["metrics"] = "metrics"
    artifact: str
    path: str
    method_used: MethodUsed
    """See `ResultsSummary.method_used`."""
    shape: ShapeView
    n_points: int
    """The real point count -- **not** `len(udi.points)`. `len(points) < n_points` is what tells a
    caller the lists were cut."""
    udi: UdiView | None = None
    da: DaView | None = None

    OMIT_WHEN_NONE: ClassVar[frozenset[str]] = frozenset({"advisories", "udi", "da"})


ResultsPayload = (
    ResultsSummary
    | ResultsPoints
    | ResultsTimeseries
    | ResultsExport
    | ResultsCompliance
    | ResultsComplianceEn17037
    | ResultsComplianceByChain
    | ResultsComplianceEn17037ByChain
    | ResultsBlindSchedule
    | ResultsMetrics
    | Refusal
)


# R810 moved this from the published text: the same `oneOf` pattern as
# `AnnualResponse`/`PointInTimeResponse`/`JobResult`.
class ResultsResponse(RootModel[ResultsPayload]):
    """What `results` returns: one of the action-shaped results, or a `Refusal`."""

    @classmethod
    def model_json_schema(cls, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return one_of_schema(super().model_json_schema(*args, **kwargs), ResultsBase)


# --- refusals -----------------------------------------------------------------------------------


def _refuse_argument(message: str, *, field: str) -> CallToolResult:
    """`ARGUMENT_OUT_OF_RANGE` at `error`, naming the argument -- `runtime.refuse_engine_error`'s
    *"the `field` key already says which argument"*."""
    return refuse(
        message,
        [Advisory(code="ARGUMENT_OUT_OF_RANGE", severity="error", field=field, message=message)],
    )


def _validate_threshold_lux(threshold_lux: float | None) -> CallToolResult | None:
    """`threshold_lux`'s own refusal, or `None`. Pure -- no project is opened and no file is read to
    answer this, so it runs before `_resolve`/`current()` (R39's placement, the same posture
    `_validate_lm83_metrics` and `_validate_en17037_arguments` already take for their own arguments).

    Only `action="points"` reads `threshold_lux` (`_points`); this still validates it regardless of
    `action`, the same posture `city`/`maintenance_factor` take for standards that do not consume
    them -- an argument that is well-formed but unused for this call is not refused for being
    unused, only for being malformed. `threshold_lux`'s own argument description says plainly that
    on `summary`/`timeseries`/`export` it is accepted and silently unused, not refused -- Task 16
    review Minor 4: a caller of those actions gets no signal their argument did nothing, and no
    existing advisory code names that condition, so the decision is recorded in prose rather than
    left to be inferred (or misassigned to a code that does not mean this).

    **The `bool` branch below is checked twice, at two different layers, on purpose.** `results`'
    own signature types this parameter `Annotated[float, Field(strict=True)] | None`, inside the
    `Annotated` that carries its description since Phase 20 (Task 16 review Important 1),
    specifically so the SDK's schema validation rejects a JSON `true`/`false`
    *before this function -- or the rest of `results`' body -- ever runs*: pydantic's default
    (non-strict) `float` coerces `bool` silently (`bool` is an `int` subclass; `True` becomes
    `1.0`), which is exactly what let a caller's `threshold_lux=true` through as "hours above 1.0
    lux" with no refusal at all, unmeasured until the Task 16 review caught it by calling the real
    tool rather than this function directly. `test_a_boolean_threshold_lux_is_rejected_at_the_
    schema_boundary` (`tests/test_results_tool.py`) proves the schema guard fires through the real
    `mcp.call_tool` path, the same way `test_an_unknown_action_is_rejected_at_the_schema_boundary`
    already proves it for `action`.

    **The `isinstance(threshold_lux, bool)` check immediately below is therefore unreachable from
    the wire**, the same posture `_validate_selectors`' own `bool` check documents for `point_ids`/
    `hours`: pydantic has already turned `True`/`False` into `1.0`/`0.0` by the time any in-process
    Python value reaches here through the schema. It is kept anyway, for a direct, in-process
    caller that calls `results(threshold_lux=True)` as a plain function and bypasses the schema
    entirely -- every test in this file that imports `results` and calls it directly does exactly
    that -- and because a future loosening of the published annotation back to plain `float` would
    make it live again without anyone having to remember to re-add it.

    Existing declared thresholds -- `LM83_SDA_THRESHOLD_LUX`, `LM83_ASE_THRESHOLD_LUX` -- are not
    special-cased here: any finite, non-negative lux value is legal input, because
    `hours_above_threshold` is explicitly *not* LM-83's own sDA/ASE tally (those stay in
    `compliance`); it is a caller-chosen threshold for a caller-chosen reason.
    """
    if threshold_lux is None:
        return None
    if isinstance(threshold_lux, bool) or not isinstance(threshold_lux, (int, float)):
        return _refuse_argument(
            f"threshold_lux must be a number of lux, not {threshold_lux!r}.", field="threshold_lux",
        )
    if not math.isfinite(threshold_lux) or threshold_lux < 0:
        return _refuse_argument(
            f"threshold_lux must be a finite, non-negative number of lux, not {threshold_lux!r}.",
            field="threshold_lux",
        )
    return None


def _refuse_not_implemented(message: str, *, field: str) -> CallToolResult:
    """`ARGUMENT_NOT_IMPLEMENTED` at `error`: a published argument this action accepts but does
    not implement -- `job_id` and `result` (ruling R100), withdrawn from the specification and
    refused rather than silently ignored, so a caller written against the old spec gets a refusal
    rather than a silently different answer."""
    return refuse(
        message,
        [
            Advisory(
                code="ARGUMENT_NOT_IMPLEMENTED", severity="error", field=field, message=message
            )
        ],
    )


# --- resolving the artifact ----------------------------------------------------------------------


class _Resolved:
    """The `.ill` this call reads, proven present and contained before anything is parsed."""

    def __init__(
        self, *, loaded: LoadedProject, paths: ProjectPaths, artifact_id: str, path: Path,
        header: AnnualMatrix, method_used: MethodUsed, expected_points: int | None,
        project: str, content_digest: str | None = None, shadowed_ids: tuple[str, ...] = (),
        blend_advisories: tuple[Advisory, ...] = (),
    ) -> None:
        self.loaded = loaded
        self.project = project
        """The project handle the call named, which the remedies a read spells pass back (final fix
        F, fix round 3, H2)."""
        self.paths = paths
        self.artifact_id = artifact_id
        self.path = path
        self.header = header
        self.method_used = method_used
        self.expected_points = expected_points
        self.content_digest = content_digest
        """The `sha256:` the manifest recorded for these bytes when the run sealed them, or `None`
        for an artifact sealed by something other than `register_artifact` (`is_reusable`'s own
        documented case). `_lux_rows` refuses a read whose bytes no longer hash to it -- see that
        function for why the check is there and not in `_contained_matrix`."""
        self.shadowed_ids = shadowed_ids
        """This grid's *other* registered annual results -- every candidate `_resolve` saw except
        this one, in `_ARTIFACT_PREFIXES` order. Empty when only one is registered, which is the
        ordinary case.

        **Plural as of Phase 7d-1 Task 15a (ruling R187), and the singular form was a data-loss
        defect waiting for a third candidate.** It was `next((other for other in registered if
        other != artifact_id), None)` -- one arbitrary element of a set that could hold two the
        moment `illblend:<grid>` joined `_ARTIFACT_PREFIXES`, so a grid holding `ill:`, `ill5:` and
        `illblend:` published *one* of the two shadowed ids and dropped the other with nothing
        saying so. Its consumer, `_refuse_ase_smeared`, then asked whether that one arbitrary id
        `startswith("ill5:")` -- which is the whole of its five-phase test -- and answered *"no
        five-phase result is registered"* to a caller who has one whenever the arbitrary pick
        landed on the blend. Both halves are fixed here: the field carries the whole set and the
        consumer tests the whole set.

        **The invariant was restated once already, by Phase 7c Task 17 (ruling R113), because it
        stopped being true**, and that restatement stands: it read *"`None` whenever `chain` was
        not given, because an unselected call with two results is now refused before it can resolve
        either"* -- true while `_AmbiguousAnnualResult` fired for every action. `compliance` answers
        such a call from **both** results (`by_chain`), so two `_Resolved` objects reach a payload
        with no `chain` given and each names the other here. The rule is about how many results are
        registered, not about the argument: **every candidate this grid holds other than this one
        is listed**, whether a selector picked this one or `by_chain` reported it beside the
        others."""
        self.blend_advisories = blend_advisories
        """What reading a **blended** result has to say about itself, computed once in
        `_resolved_from` and carried here rather than recomputed per action.

        Its blend findings are absent for every unblended result, and for a blended one whose
        schedule still matches (`_stale_blend_advisories`) and whose grid carries LM-83's own
        density (`_mixed_basis_blend_advisories`, ruling R242). It exists because both of those are
        properties of the *artifact*, not of the action asking: `summary`, `points`, `timeseries`,
        `metrics`, `export` and both `compliance` standards all publish numbers decided hour by
        hour by the schedule they compare against, so attaching a finding to one action would
        leave the same wrong number unlabelled on six others.

        **Since R846 it also carries `WINDOW_GROUP_V_MATRIX_ALL_ZERO`**
        (`_dark_window_groups_advisory`) on any result, blended or not: a window group whose V
        matrix is zero on every grid is a property of the artifact too, and every action reading
        it publishes the same missing light.
        The field keeps its name; it is "what this artifact has to say about itself".

        **This field is the only place an `error`-severity advisory rides a payload `results` does
        not refuse**, and that is R242's mechanism rather than an oversight: `rad_mcp.envelope`
        defines `is_error` as describing the *call* and an `error` advisory as describing the
        *value*, and R174 -- unreversed -- says `compliance` never refuses. The signal therefore
        has to travel on the payload that carries the wrong verdict."""

    @property
    def blended(self) -> bool:
        """Was this matrix blended against LM-83 section 2.2.6's hourly schedule?

        **Keyed on the artifact id, never on `method_used`** -- the standing carry from Phase 7c
        Task 11, and here it is not merely a convention: a blended result records
        `method="3phase"` exactly as the unblended total does (ruling R158), so `method_used`
        cannot tell them apart at all and a check written against it would be silently false for
        every blend.
        """
        return self.artifact_id.startswith(_BLENDED_PREFIX)


#: Artifact id prefixes `_resolve` tries, in the order it tries them. **The order decides nothing
#: but the order candidates are listed in**: two or more registered with no selector is a refusal
#: (`_AmbiguousAnnualResult`), never a preference. Phase 7a split `ill:`/`ill5:` precisely so a
#: five-phase run destroys no readable three-phase result; both can exist for one grid, and picking
#: whichever is newer would recreate the exact ambiguity the split exists to avoid -- a three-phase
#: result answering yesterday and a five-phase one today, with nothing in the payload saying which.
#:
#: **`illblend:` joined at Phase 7d-1 Task 15a, as an ordinary candidate and with no preference of
#: its own.** It is the schedule-weighted result `chain.blend_steps` seals under
#: `blind_control="lm83"`, and until it was listed here `_resolve` found no candidate at all for an
#: `lm83` project -- which seals **no** whole-grid `ill:<grid>` (`chain.three_phase_steps`' summing
#: step is skipped under `LM83_BLIND_CONTROL`), so every `results` action that reaches `_resolve`
#: raised `_NoAnnualResult` on such a project -- every action but `blind_schedule`, whose dispatch
#: branch returns above the `_resolve` call (fix round 2, N5: this sentence carried the same
#: wrong-by-one *"every action"* quantifier the review found in two other places). A preference
#: order was considered and rejected: `ill:<grid>` and `illblend:<grid>` can only coexist across
#: **two separate runs**, so a project holding both holds two runs' output and at least one of them
#: is possibly stale -- exactly the condition R105 refuses rather than resolves. `blind_control=`
#: is the selector, `chain=` is not (ruling R188).
_ARTIFACT_PREFIXES: tuple[str, ...] = ("ill:", "ill5:", _BLENDED_PREFIX)

#: Which artifact id prefix each `chain` value names. **Three chains, two ids** -- the two- and
#: three-phase chains both seal `ill:<grid>` (`chain.two_phase_steps`/`three_phase_steps`), and only
#: the five-phase chain seals `ill5:<grid>` (`chain.five_phase_steps`). A caller naming `"3phase"`
#: therefore selects the *id*, and `_resolve` still has to check the artifact's own recorded
#: `method` before serving it -- the prefix cannot tell the first two apart.
_ARTIFACT_PREFIX_FOR_CHAIN: dict[str, str] = {
    "2phase": "ill:", "3phase": "ill:", "5phase": "ill5:",
}


def _selected_id(grid: str, *, chain: str, blind_control: str | None) -> str:
    """The artifact id a `chain=` call names, once `blind_control` has had its say.

    **`blind_control` wins here, and it is not a preference** -- it is the only argument that can
    name the blend at all. `_ARTIFACT_PREFIX_FOR_CHAIN` maps every `chain` value onto `ill:` or
    `ill5:` and nothing maps onto `illblend:`, by design (ruling R188: `chain` gains no
    `"illblend"` value), so `chain="3phase"` beside `blind_control="lm83"` would otherwise resolve
    the *unblended* total -- serving a caller who asked for the scheduled result the unscheduled
    one, labelled `method_used="3phase"`, which is true of both. That is the silent-substitution
    family this module refuses everywhere else.

    The other three pairs cannot reach here: `blind_control="lm83"` with `chain="2phase"` or
    `chain="5phase"` is refused above (`_BlendIsThreePhaseOnly`), and `blind_control="none"` has
    already narrowed the candidate set to the unblended ids, so the prefix map is exactly right
    for it.
    """
    if blind_control == _LM83_BLIND_CONTROL_READ:
        return blend_ill_id(grid)
    return f"{_ARTIFACT_PREFIX_FOR_CHAIN[chain]}{grid}"


class _AmbiguousAnnualResult(ProjectError):
    """**Both `ill:<grid>` and `ill5:<grid>` are registered and the call named no `chain`.**

    `ANNUAL_RESULT_AMBIGUOUS` at `error`, allocated 2026-08-21 by ruling R105 (owner) --
    *"i would like to have the 3 phase results and 5 phase reported separatly and not
    overwritten"*. Before it, `_resolve`'s `ill:`-before-`ill5:` order answered such a project
    from whichever the *older* chain wrote, in silence: a caller who ran `run_annual(metrics=
    ['da'])` (three-phase) and then `run_annual(metrics=['sda','ase'])` (five-phase) on one grid
    got LM-83 compliance judged from the three-phase matrix, ASE refused for ever, and EN 17037 --
    which has no method gate at all -- silently answered from the older one too.

    **This code is not a warning, and it puts no preference in the old order's place.** A grid
    with one result registered, called with no `chain`, is answered from it whichever id it is;
    what is refused is the *ambiguous* call, and only that. Passing `chain=` resolves it, which is
    why the argument shipped with the refusal rather than in Phase 7c: a refusal with no way to
    proceed would lock a caller holding both artifacts out of every action until 7c landed.

    Not `MATRIX_SHAPE_MISMATCH` (nothing has drifted -- both records are true of their own files),
    not `ARGUMENT_OUT_OF_RANGE` (R14: *refused before any work was attempted*; the project is open
    and its manifest read by this point, and no argument is malformed -- `chain` is absent, which
    is legal on every project that has one result), and not `METRIC_NOT_COMPUTED_YET` (both
    matrices exist and both are readable; that code is retired by R815). The condition the register had no name for is *two
    answers, no instruction which to publish*, and that is what the name says.
    """

    code = "ANNUAL_RESULT_AMBIGUOUS"


class _ChainResultNotRegistered(ProjectError):
    """`chain=` named a chain this project has no annual result for.

    **Code-less, for `_NoAnnualResult`'s own stated reason** rather than a new one: `chain`, like
    `grid`, is *"an ordinary tool argument naming a handle inside the project, and the register
    allocates codes for the protocol handles rather than for every argument that can miss"*. This
    refusal is the same condition as `_NoAnnualResult` narrowed by one argument -- *no annual
    result is registered for this grid **from that chain*** -- so it takes the same posture, and
    the message names what **is** registered so the caller can ask for that instead.
    """

    code = None


class _ChainMethodMismatch(ProjectError):
    """`chain=` named a chain, the id it selects is registered, and that artifact was written by a
    **different** chain.

    Reachable because two chains share one id: `chain="3phase"` selects `ill:<grid>`, which
    `chain.two_phase_steps` also seals. Serving it would answer a three-phase question from a
    two-phase matrix and label it `method_used="2phase"` in a field the caller did not ask about --
    the same silent-substitution family this whole ruling exists to close, one level down.

    **An absent `method` parameter is `"2phase"` and is not a mismatch for `chain="2phase"`.**
    `chain.two_phase_steps`' `ArtifactSpec` records no `"method"` key at all, so the absence is the
    normal case for every two-phase project, and reading it as "unknown, therefore refuse" would
    make `chain="2phase"` unusable on exactly the projects it exists for.

    Code-less for the same reason as `_ChainResultNotRegistered` above: it is the `chain` argument
    naming something the project does not hold.
    """

    code = None


class _BlindControlNotRegistered(ProjectError):
    """`blind_control=` named a blind treatment this project has no annual result for.

    The sibling of `_ChainResultNotRegistered` one argument over, and **code-less for its stated
    reason**: `blind_control`, like `chain` and `grid`, is an ordinary tool argument naming a handle
    inside the project, and the register allocates codes for protocol handles rather than for every
    argument that can miss. The condition is `_NoAnnualResult` narrowed by one argument -- *no
    annual result is registered for this grid **under that blind treatment*** -- so it takes the
    same posture and the message names what **is** registered, with the selector that reaches it.

    Reachable both ways: `blind_control="lm83"` on a project that ran unscheduled (no
    `illblend:<grid>` was ever sealed), and `blind_control="none"` on a project whose only annual
    result is the blend -- which is every `lm83`-only project, because `chain.three_phase_steps`
    skips its summing step under `LM83_BLIND_CONTROL` and seals no `ill:<grid>` at all.
    """

    code = None


class _BlendIsThreePhaseOnly(ProjectError):
    """`blind_control="lm83"` was asked for beside `chain="2phase"` or `chain="5phase"`.

    **Unsatisfiable by construction, not merely unregistered today**, which is why it is refused
    rather than answered as "nothing is registered under that pair": `chain.blend_steps` is a step
    of the three-phase chain and of no other, and the `ArtifactSpec` it seals records
    `method="3phase"` (ruling R158). No run this server can plan produces a blended two- or
    five-phase result, so no re-run recovers this call and a refusal that suggested one would be
    the actionable-recovery failure R189 exists to prevent.

    Code-less, for `_ChainMethodMismatch`'s own stated reason: it is the selection arguments naming
    a combination the project cannot hold.
    """

    code = None


def _recorded_chain(artifact: Any) -> str:
    """The chain that wrote `artifact`, from its own `parameters["method"]`.

    **One place, so the sentence a caller reads and the value `method_used` publishes can never
    disagree.** The `"2phase"` default is the same one `_resolve` applies: `chain.two_phase_steps`'
    `ArtifactSpec` records no `"method"` key at all, so an absent value *is* two-phase.
    """
    return artifact.parameters.get("method", "2phase")


def _unscheduled_rerun_clause(loaded: LoadedProject, *, chain: str | None = None) -> str:
    """The tail of the two refusals that used to offer a re-run **this project cannot make**.

    ## What was measured, at the tool boundary, at `4d60d58`

    `_BlindControlNotRegistered` ended *"…or re-run run_annual without blind_control='lm83' so an
    unscheduled result is sealed for this grid."* and `_ChainResultNotRegistered` ended *"…or
    re-run run_annual so the chain you asked for seals its own result."* Both fire on a grid whose
    only annual result is `illblend:<grid>`, and a project that can hold one has bound **two**
    states to a window group (`annual_tool._refuse_unbindable_blend_states` requires `open` and
    `closed` before the blend is planned at all). On such a project:

        run_annual(project='office', grid='grid', metrics=['da'], blind_control='none')  -> refused
        run_annual(project='office', grid='grid', metrics=['da'], blind_control='fixed') -> refused
          "window group 'main' has 2 BSDF states bound (closed, open), and blind_control=... chooses
           between them with nothing. … Run with blind_control='lm83', or bind a single state."

    and `blind_control='lm83'` seals the blend rather than an unscheduled result -- so **every**
    re-run the two sentences named was either refused or a re-derivation of the artifact the caller
    already has. Ruling R189: no recovery sentence may name a call that cannot succeed. It was
    minted on this branch and applied at one site (`_refuse_unbindable_blend_states`); these are
    two of the other three.

    ## The shape, which is the precedent's

    **Branched on what the project actually binds, so each arm names a call that runs** -- exactly
    `_refuse_unbindable_blend_states`' own device, and for the same reason: the multi-state case and
    the single-state case have different true answers, and one sentence covering both is false for
    one of them. `chain.states_by_group` is the measurement, scanned off `tmx:<group>:<state>`; it
    lives in `rad_engine` because `annual_tool` imports this module and the edge must stay one-way.

    - **No group binds more than one state.** The old sentence is true and is kept — **each site's
      own**, which `chain` selects. The two callers want different things: one wants an unscheduled
      result and the other wants the chain it named, and a `chain="5phase"` caller told merely to
      *"re-run without blind_control='lm83'"* would make a run that seals `ill:<grid>` and still
      not find `ill5:<grid>`. Collapsing them into one generic sentence was this fix's own first
      draft, and it was R189's defect reintroduced by the repair for it.
    - **Some group does.** The re-run is named as what it is -- refused, with the reason -- and the
      route that *succeeds* is named instead: read the result this grid holds. Reducing the group
      to one bound state is stated as a condition rather than as a call, because there is no
      `window_group` action that unbinds one state (`assign_bsdf` adds to what a role already
      bound; only `remove` deletes, and it deletes the whole group). Naming a call that does not
      exist is the same defect one step further along.
    """
    several = {
        group: states
        for group, states in states_by_group(loaded).items()
        if len(states) > 1
    }
    if not several:
        # **The reachable arm keeps each site's own sentence**, because the two sites are asking
        # different questions: `_BlindControlNotRegistered`'s caller wants *an unscheduled result*
        # and `_ChainResultNotRegistered`'s wants *the chain it named*, and a `chain="5phase"`
        # caller told merely to "re-run without blind_control='lm83'" would make a run that seals
        # `ill:<grid>` and still not find `ill5:<grid>`. Collapsing the two into one generic
        # sentence would have been R189's own defect introduced by the fix for it.
        return (
            f", or re-run run_annual so the {chain} chain seals its own result for this grid."
            if chain is not None else
            ", or re-run run_annual without blind_control='lm83' so an unscheduled result is "
            "sealed for this grid."
        )
    bound = "; ".join(
        f"{group!r} binds {len(states)} ({', '.join(states)})"
        for group, states in sorted(several.items())
    )
    return (
        ". Re-running run_annual is NOT the route out of this refusal for this project: "
        f"{bound}, and a run that seals an unscheduled result multiplies one T -- so "
        "blind_control='none' and blind_control='fixed' are both refused for it, and "
        "blind_control='lm83' re-derives the blend this grid already holds rather than an "
        "unscheduled result. Read what is registered, above. An unscheduled result becomes "
        "reachable only once every group binds exactly one state, and no window_group action "
        "unbinds one: assign_bsdf adds to what the role already bound, and remove deletes the "
        "whole group."
    )


def _describe_registered(artifact_id: str, artifact: Any) -> str:
    """`"'ill:floor' (written by the 3phase chain)"` -- one registered annual result, named with
    the chain that wrote it, for the refusals that have to tell them apart.

    **The blend needs a second clause, because the first one cannot tell it apart.** `illblend:
    <grid>` records `method="3phase"` exactly as the unblended total does (ruling R158), so a
    project holding both would otherwise be described as *"'ill:floor' (written by the 3phase
    chain) and 'illblend:floor' (written by the 3phase chain)"* -- two ids and one reason, which
    reads as a manifest that contradicts itself rather than as two results that differ by blind
    treatment.

    **Edition-neutral** (final review, W3): the schedule was sealed under one LM-83 edition, and
    this sentence reaches refusals of calls read under either, so it names no clause.
    """
    blended = (
        ", blended against its sealed blind schedule"
        if artifact_id.startswith(_BLENDED_PREFIX) else ""
    )
    return f"{artifact_id!r} (written by the {_recorded_chain(artifact)} chain{blended})"


def _selector_for(artifact_id: str, artifact: Any) -> str:
    """The **one** argument that resolves exactly `artifact_id`, as a sentence a caller can copy.

    **Ruling R189: every refusal names a call that succeeds.** Before Phase 7d-1 Task 15a this was
    written inline as `f"chain={_recorded_chain(other)!r} reads {other_id!r}"`, which is right for
    the two unblended ids and **unsatisfiable for the blend**: `_recorded_chain(illblend:<grid>)`
    is `"3phase"` and `_ARTIFACT_PREFIX_FOR_CHAIN["3phase"]` is `"ill:"`, so a caller who followed
    that sentence literally was sent to a *second* refusal naming a different artifact -- the exact
    "review Minor 1" defect the comment above the old expression closed for `chain` alone.

    `chain=` alone is enough for an unblended candidate even when a blend stands beside it, because
    `chain` selects the id directly through the prefix map; `blind_control='lm83'` alone is enough
    for the blend, because a grid holds at most one. Neither sentence ever needs the other argument,
    so no combination is offered that the prefix map cannot serve.
    """
    if artifact_id.startswith(_BLENDED_PREFIX):
        return f"blind_control={_LM83_BLIND_CONTROL_READ!r} reads {artifact_id!r}"
    return f"chain={_recorded_chain(artifact)!r} reads {artifact_id!r}"


#: **Which actions answer an unselected two-result call from BOTH results instead of refusing it**
#: -- ruling R113, the end state of [R105]. `compliance` is the only member and the reason is
#: stated in R105 itself: two full matrices double a payload that already carries a hard inline
#: bound (`MAX_INLINE_VALUES`), and the comparison they enable on `summary`/`points`/`timeseries`/
#: `export` is **per-cell**, which is not the question any of the standards ask. A compliance
#: verdict is a fixed-size aggregate per entry, so reporting two costs a constant.
#:
#: Read by `_resolve`, which is what makes this a policy rather than a comment: the function every
#: action goes through decides from the action it was told about, and an action added to
#: `_DISPATCHABLE_ACTIONS` without being added here keeps R105's refusal by default -- the safe
#: direction, since the refusal is actionable (`chain=`) and a doubled payload is not recoverable.
_ACTIONS_ANSWERING_FROM_BOTH_RESULTS: Final[frozenset[str]] = frozenset({"compliance"})


def _resolve(
    runtime: Runtime, *, project: str, grid: str, action: str, chain: str | None = None,
    blind_control: str | None = None,
) -> list[_Resolved]:
    """Find the annual `.ill`(s) `run_annual` committed for `grid`, and read their shape.

    **Returns a list, and `action` decides how long it can be** (Phase 7c Task 17, ruling R113).
    One element for every call except an unselected `compliance` on a grid holding two annual
    results, where it is two -- in `_ARTIFACT_PREFIXES`' own order, so `ill:<grid>` is first. This
    is a signature change to the function every action except `blind_schedule` goes through, and the
    design says so rather than claiming the work was additive: `_AmbiguousAnnualResult` used to be
    raised **before `action` was ever consulted**, so answering both required this function to learn
    which action is asking. `action` is keyword-only and has **no default**, so every call site has
    to state it
    -- a default would silently give a new caller whichever policy the default names.

    `_ACTIONS_ANSWERING_FROM_BOTH_RESULTS` is the policy; every other action still raises
    `_AmbiguousAnnualResult`, at the same placement, for R105's own stated reason.

    **`action="blind_schedule"` never reaches here at all** and must not start to. It reads
    `illdir:<grid>` and no chain result, so resolving one would make a chain result a precondition
    of an answer that does not depend on it, and would apply `ANNUAL_RESULT_AMBIGUOUS` to a call
    that opens neither of the two matrices that ambiguity is between (Task 16, deviation 1). The
    dispatch in `results` returns before this call for that action;
    `tests/test_results_tool.py::TestEveryOtherActionStillRefusesTheUnselectedTwoResultCall`
    measures which actions reach this function rather than asserting it from a list of names.

    **`chain` selects; without it, two registered results are a refusal, not a preference.**
    Added 2026-08-21 (ruling R105). When both `ill:<grid>` and `ill5:<grid>` are registered and
    `chain` is `None`, this raises `_AmbiguousAnnualResult` -- naming both ids, the chain that
    wrote each, and `chain` as the way to proceed -- **before the manifest path is contained or
    the header is read**, which is where every refusal in this module sits (R39: `results`
    publishes no `dry_run`, so its refusals fire before any path resolution or allocation). When
    `chain` names one, the matching id is resolved (`_ARTIFACT_PREFIX_FOR_CHAIN`) and its
    **recorded `method` is checked against what was asked** (`_ChainMethodMismatch`), because
    `"2phase"` and `"3phase"` share the `ill:` id and the prefix alone cannot tell them apart.

    **When only one result is registered nothing changes at all** -- same artifact, same payload,
    `chain` optional. A `chain` that agrees with the one registered result is served; one that
    names a chain this project has no result for is refused (`_ChainResultNotRegistered`) rather
    than quietly answered from the other.

    **No resolution order.** Both ids can exist for the same grid -- Phase 7a's whole-branch
    review split them precisely so a five-phase run cannot overwrite a readable three-phase result.
    Neither wins by position: with both registered, `chain="5phase"` is answered from `ill5:<grid>`
    and `chain="2phase"`/`"3phase"` from `ill:<grid>` (`_ARTIFACT_PREFIX_FOR_CHAIN`), and with no
    `chain` neither is preferred: every action that reaches this function but `compliance` refuses
    (above), and `compliance` answers from both in `by_chain`. `method_used` on the
    published result says which chain built the one that did.

    **When both exist and `chain` picked one, the one that lost is recorded on
    `_Resolved.shadowed_ids`.** The selection is the caller's, and the *silence* about the other
    result was the defect (whole-branch review, composition lens; written when an `ill:`-first
    order still chose, before R105 made the unselected call a refusal). Concretely:
    `run_annual(metrics=['da'])` then `run_annual(metrics=['sda','ase'])` on one grid leaves a
    three-phase `ill:` beside a five-phase `ill5:`, and `compliance(standard='lm83')` then refused
    `ASE_FROM_SMEARED_SUN` for ever with the false reason *"ASE can only be reported from 5phase"*
    -- said to a caller who has one. `_refuse_ase_smeared` names the shadowed id and the recovery,
    which closes that half, and it is **still reachable**: `chain="3phase"` on a project that also
    holds an `ill5:` is exactly the call that lands there.

    **The register gap this docstring used to report is closed by refusing instead of warning
    (ruling R105).** The missing piece was a `warn` on a *successful* result saying "a newer
    `ill5:<grid>` exists and this call did not read it" -- carried by `compliance(standard=
    'en17037')`, which has no method gate at all, and by `summary`/`points`/`timeseries`/`export`.
    There is now no silent shadowing left for such a warning to describe: an unselected call with
    two results either refuses (`_AmbiguousAnnualResult`) or -- on `compliance` -- reports **both**,
    and a selected one was told which matrix to read. In none of the three is a result read while
    another goes unmentioned, which is the only condition such a warning had to describe.

    **A stored result the manifest reports invalid is refused** (ruling R785), right after the
    registration check and before R776's floor check, through `_refuse_stale_results` over the ids
    this call's selectors can read (`_readable_ids`). `rad_engine.evaluate_loaded` is the judge, as
    for `export(artifact_id=...)`: a grid re-created, a scene edited or the weather replaced since
    the result was sealed refuses it, naming the moved edges.

    **The manifest path is re-contained before it is opened.** Ruling R50: *"a path out of the
    manifest is untrusted on every action that touches it"* -- registered is not currently valid, and
    the three routes to a stale or hostile record listed there do not require hand-editing. So the
    path goes through `rad_mcp.paths.contain` against the configured roots before
    `rad_engine.results` opens it, which is R48's *"containment checked before the syscall"*.

    **That containment and the header cross-check below both live in `_contained_matrix` now**
    (Phase 7c Task 12), which this function calls. Phase 7c opens a second manifest-registered
    matrix -- the direct-sun `illdir:<grid>` -- and one shared function is what stops it being
    opened a second way; the paragraphs below describe what that function does on this call site's
    behalf, unchanged.

    **The header's two integers are cross-checked against the manifest here, and a mismatch is
    raised before this function returns** -- `_MatrixShapeMismatch`, `MATRIX_SHAPE_MISMATCH` at
    `error`, engine invariant 3's own code and its own rule (*"matrix shape is checked against
    (`n_sensors`, `n_timesteps`) **before any metric is computed**"*). This is an ordering fix, not
    a new check: `iter_annual_lux_rows` already compared `NROWS` against `parameters["n_points"]`,
    but only from inside the generator -- i.e. **after** every caller had already sized an
    allocation from the same unvalidated integers. `AnnualMatrix.points`/`hours` are
    `int(fields["NROWS"/"NCOLS"])` with no magnitude bound (`rad_engine.annual`), and five call
    sites on this module's own new paths allocate straight from them: `lm83_window(header.hours)`
    builds a frozenset over `range(n_hours)`, and `[PointTally(...) for _ in
    range(header.points)]` costs a measured ~656 bytes per point, so a hand-edited or truncated
    manifest record declaring `NROWS=2000000000` attempted ~1.3 TB and OOM-killed the server while
    the refusal that would have stopped it waited two lines further down. Paying the cheap refusal
    first is the discipline `_refuse_ase_smeared` and `_refuse_empty_lm83_denominator` already
    keep; this puts the shape check on the same footing (whole-branch review, security lens).

    `n_hours` is compared **only when the artifact records one**: `chain.five_phase_steps` seals
    `parameters["n_hours"]`, and the two- and three-phase seals record no such key at all
    (`chain.two_phase_steps`/`three_phase_steps`), so an absent value is the normal case and is not
    a mismatch. `n_points` is recorded by all three.

    Reads only the header here, not the body: `_resolve` runs on every action except
    `blind_schedule`, including the ones that then refuse, and paying for a 6.8 GB read to answer
    "which grid?" would be the wrong order.
    """
    loaded = runtime.open(project)
    paths = ProjectPaths(loaded.paths.root)
    # Insertion order is `_ARTIFACT_PREFIXES`' order. It decides only the order candidates are
    # listed in: two or more left with no `chain` are refused below, or all reported on
    # `compliance`, so the order never picks one.
    registered: dict[str, Any] = {}
    for prefix in _ARTIFACT_PREFIXES:
        candidate_id = f"{prefix}{grid}"
        try:
            registered[candidate_id] = loaded.manifest.artifact(candidate_id)
        except KeyError:
            continue

    if not registered:
        # **The listing is of GRIDS, so an id carrying more than a grid after its prefix is not
        # one** (rulings R179/R157). Task 7 fans an `lm83` run out per (group, state) and seals
        # every operand under `ill:<grid>:<group>:<state>` -- ids that begin `ill:` and therefore
        # passed this filter, so a project that ran `lm83` was told its grids were
        # `floor:north:open, floor:north:closed, floor:south:open, ...`. Every one of those is a
        # string that, passed back as `grid=`, is now refused outright (`_refuse_split_grid`,
        # ruling R186) -- so the sentence advertised as a recovery was a list of calls that all
        # fail. Keeping only the ids with no further colon leaves exactly the whole-grid results.
        known = sorted({
            a.id[len(prefix):]
            for prefix in _ARTIFACT_PREFIXES
            for a in loaded.manifest.artifacts
            if a.id.startswith(prefix) and ":" not in a.id[len(prefix):]
        })
        raise _NoAnnualResult(
            f"no annual result is registered for grid {grid!r} in this project. "
            + (f"Grids with an annual result: {', '.join(known)}. " if known else "")
            + "run_annual(project=..., grid=..., metrics=[...], epw=..., north=...) produces one; "
            "poll it with job(action='status') and read it here once it has succeeded."
        ) from None
    # R785 (owner decision F4): a stored result the manifest reports invalid is refused, for any
    # cause, before R776's floor check, which judges the grid as it is now.
    readable = _readable_ids(registered, grid=grid, chain=chain, blind_control=blind_control)
    _refuse_stale_results(loaded, readable, name_selectors=chain is None)
    # Final fix E, fix round 4 (X1): what the manifest cannot see, the files its `!xform` lines
    # include, re-hashed against what each result's octrees compiled.
    _refuse_changed_scene_includes(loaded, readable, project=project, grid=grid)
    _refuse_grid_off_recorded_floor(loaded, grid, project=project)  # R776, after registration

    # **`blind_control` narrows first, `chain` selects within what is left** -- ruling R188's
    # orthogonality, expressed as two independent filters rather than one combined vocabulary. The
    # blend records `method="3phase"` (R158), so folding the two questions into `chain` would make
    # one of the two answers unreachable whatever value was added.
    # **The unnarrowed set, kept for `shadowed_ids`.** What `_refuse_ase_smeared` says -- *"a
    # five-phase result IS registered for this grid"* -- is a fact about the project, not about
    # this call's selectors, so narrowing it by `blind_control` would make the refusal deny a
    # result the caller holds exactly when the caller passed the argument that hid it.
    all_registered = dict(registered)

    if blind_control is not None:
        if blind_control == _LM83_BLIND_CONTROL_READ and chain is not None and chain != _BLEND_CHAIN:
            raise _BlendIsThreePhaseOnly(
                f"blind_control={blind_control!r} names the blended result and chain={chain!r} "
                f"names a chain that never seals one: chain.blend_steps is a step of the "
                f"{_BLEND_CHAIN} chain and of no other, and the blend it seals records "
                f"method={_BLEND_CHAIN!r}. No run this server can plan produces a blended "
                f"{chain} result, so re-running would not make this pair resolvable. Drop chain= "
                f"to read {_BLENDED_PREFIX}{grid} (it is a {_BLEND_CHAIN} result), or drop "
                f"blind_control= to read the {chain} result if this grid has one."
            )
        # **`"fixed"` and `"none"` land in the same branch, and that is R197's whole content**: a
        # fixed-blind run seals no blend, so both words name the unblended candidates and there is
        # no third partition for a third value to select. Expressed as *"is it the blend?"* rather
        # than as a three-way map, so a value added to `_READ_BLIND_CONTROLS` that is not `"lm83"`
        # cannot silently acquire a partition of its own.
        want_blended = blind_control == _LM83_BLIND_CONTROL_READ
        narrowed = {
            other_id: other for other_id, other in registered.items()
            if other_id.startswith(_BLENDED_PREFIX) is want_blended
        }
        if not narrowed:
            raise _BlindControlNotRegistered(
                f"blind_control={blind_control!r} names no annual result for grid {grid!r}: "
                + (
                    f"nothing is registered under {_BLENDED_PREFIX}{grid!s}, which is the id "
                    "a run_annual run with blind_control='lm83' seals once its blend runs. "
                    if want_blended
                    else "the only annual result this grid holds is the blended one. "
                )
                + "Registered for this grid: "
                + ", ".join(
                    _describe_registered(other_id, other)
                    for other_id, other in registered.items()
                )
                + ". Read one of those with "
                + ", or ".join(
                    _selector_for(other_id, other) for other_id, other in registered.items()
                )
                + (
                    _seal_blend_clause(loaded, project=project, grid=grid, results=tuple(registered))
                    if want_blended
                    else _unscheduled_rerun_clause(loaded)
                )
            )
        registered = narrowed

    # **Both selection refusals sit here**: above `contain`, above `read_annual_header`, and above
    # every allocation any action sizes from the header (R39's placement rule, which `results` --
    # publishing no `dry_run` -- keeps by refusing before any path resolution or file access).
    if chain is None:
        if len(registered) > 1 and action in _ACTIONS_ANSWERING_FROM_BOTH_RESULTS:
            # **`by_chain`'s only entry point** (ruling R113). Every guard above still ran, and
            # every guard below runs once per result rather than once per call, so nothing is
            # skipped for the second matrix -- `_contained_matrix` contains and cross-checks each
            # one on its own record. The keying happens in the payload, and the collision that
            # would drop one entry is refused there (`_refuse_indistinguishable_chains`), not
            # here: this function's job is to resolve, not to name.
            return [
                _resolved_from(
                    runtime, loaded=loaded, paths=paths, registered=all_registered,
                    artifact_id=other_id, grid=grid, project=project,
                )
                for other_id in registered
            ]
        if len(registered) > 1:
            raise _AmbiguousAnnualResult(
                f"grid {grid!r} has {len(registered)} annual results registered and this call did "
                "not say which to read: "
                + " and ".join(
                    _describe_registered(other_id, other) for other_id, other in registered.items()
                )
                + ". All of them survive by design -- the artifact ids were split so a five-phase "
                "run destroys no readable three-phase result, and the blend is sealed under an id "
                "of its own so a scheduled run destroys no unscheduled one -- so answering from "
                "one of them without being asked would judge an LM-83 or EN 17037 question "
                "against a matrix the caller never chose. Name which one this call reads: "
                # **Every sentence offered here is a call that succeeds** (ruling R189), which is
                # what `_selector_for` exists to guarantee: `chain=` for the candidates that
                # differ by method, `blind_control=` for the one that differs by blind treatment,
                # and never a combination `_ARTIFACT_PREFIX_FOR_CHAIN` cannot serve. Only the
                # chain each artifact actually records is offered -- `"2phase"` and `"3phase"`
                # both resolve to `ill:<grid>`, but only the one matching that artifact's own
                # recorded `method` is served, and offering both would send a caller who follows
                # this sentence literally into a *second* refusal (review Minor 1).
                + ", or ".join(
                    _selector_for(other_id, other) for other_id, other in registered.items()
                )
                + ". results(action='compliance') reports them all side by side in by_chain, "
                "keyed by the recorded method and the blind treatment together (rulings R113 and "
                "R174), and needs no selector; this action reads one matrix, so it needs one."
            )
        artifact_id, artifact = next(iter(registered.items()))
    else:
        artifact_id = _selected_id(grid, chain=chain, blind_control=blind_control)
        if artifact_id not in registered:
            raise _ChainResultNotRegistered(
                f"chain={chain!r} names no annual result for grid {grid!r}: nothing is registered "
                f"under {artifact_id!r}. Registered for this grid: "
                + ", ".join(
                    _describe_registered(other_id, other) for other_id, other in registered.items()
                )
                + ". Read one of those with "
                + ", or ".join(
                    _selector_for(other_id, other) for other_id, other in registered.items()
                )
                + _unscheduled_rerun_clause(loaded, chain=chain)
            )
        artifact = registered[artifact_id]
        recorded = _recorded_chain(artifact)
        if recorded != chain:
            # **Why the id could not tell them apart, said correctly for whichever id this is.**
            # The `ill:`-sharing sentence is the two-/three-phase collision, and it is simply
            # untrue of `illblend:<grid>`, which one chain seals under one id -- reachable here
            # only through `blind_control="lm83"` on a manifest whose blend records some other
            # method (R50: registered is not currently valid). A fixed sentence would have
            # explained a real refusal with a reason that is false of it.
            why = (
                f"That id is selected by blind_control={_LM83_BLIND_CONTROL_READ!r}, not by "
                f"chain=, so nothing about the id constrained the method"
                if artifact_id.startswith(_BLENDED_PREFIX)
                else f"The two- and three-phase chains both seal {artifact_id!r}, so the id "
                "cannot tell them apart"
            )
            raise _ChainMethodMismatch(
                f"chain={chain!r} was asked for, and {artifact_id!r} -- the id this call selects "
                f"-- records method={recorded!r}. {why}, and this call would otherwise "
                f"have answered a {chain} question from a {recorded} matrix. Pass "
                f"chain={recorded!r} to read it as what it is, or re-run run_annual so the {chain} "
                "chain seals its own result for this grid."
            )

    return [
        _resolved_from(
            runtime, loaded=loaded, paths=paths, registered=all_registered,
            artifact_id=artifact_id, grid=grid, project=project,
        )
    ]


def _contained_schedule_path(runtime: Runtime, record: Any) -> Path:
    """Prove a manifest-registered **schedule** path safe before anything opens it.

    **The one place a `sched:<grid>` record's path is contained**, as `_contained_matrix` is for an
    annual `.ill`. Extracted at Phase 7d-1 Task 15a because a second reader arrived:
    `_sealed_group_schedule` parses the document for `results(action='blind_schedule')`, and
    `_stale_blend_advisories` hands the same file to `rad_engine.blend.compare_schedule_digest` to
    be hashed. `tests/test_results_tool.py::
    test_resolve_and_the_direct_lookup_take_the_same_path_through_containment` asserts structurally
    that there is exactly one of these, because "two call sites that agree today" is precisely the
    state that extraction exists to prevent -- and the guard here is not decorative: R48 puts
    containment *before* the syscall and R50 holds a manifest path untrusted on every action that
    touches it, so a call site that reproduced the read and forgot the `contain` would be a
    security regression that every test of the first call site still passed.

    Raises `PathOutsideRoot`/`RootsNotConfigured`; what a caller does with that is its own
    decision. `_sealed_group_schedule` lets it become a `groups: null` with an advisory naming
    which guard fired, and `_stale_blend_advisories` logs it and says nothing on the wire, because
    an unreachable schedule is an absence of the thing to compare against and not evidence that
    the blend is stale.

    **Containment only.** The final-component and file-type guards -- symlink, FIFO, directory --
    belong to whoever opens the file (`open_regular_file`), which is R48's own split: the layer
    that holds the roots owns the parent chain, and the reader owns the object it opens.
    """
    return contain(record.path, runtime.roots)


def _stale_blend_advisories(
    runtime: Runtime, *, loaded: LoadedProject, artifact_id: str, header: AnnualMatrix,
    project: str,
) -> tuple[Advisory, ...]:
    """Does this blended matrix still match the schedule the project holds? -- ruling R191.

    **Empty for everything that is not a blend**, and the first line says so: an unblended result
    has no schedule to be stale against and pays nothing for this check. That first line is a
    *cost* guard rather than the correctness one, measured rather than assumed -- see the comment
    on it, and `mismatched`'s polarity below for what actually decides the answer.

    The comparison itself is `rad_engine.blend.compare_schedule_digest`, which owns both halves of
    what "the schedule's digest" means (`_write_blend` stamps it into the header, `_read_schedule`
    derives it) -- a second definition here would be two statements of one fact that agree until
    one of them moves. This function owns only what that one cannot: the containment of the
    schedule path, and the policy.

    **`contain` before the path is handed over, which is R48's split rather than a convenience.**
    `compare_schedule_digest` deliberately does not contain: it holds no roots and owns the
    final-component and file-type guards only (`open_regular_file`), exactly as
    `rad_engine.sensors.write_points` and `rad_engine.run.run_to_file` do. The layer that holds the
    roots -- this one -- owns the parent chain, and a manifest path is untrusted on every action
    that touches it (R50).

    **Advise, never refuse, and only on `mismatched`.** The polarity is the whole point and it is
    not `not agree`: `ScheduleDigestCheck.mismatched` is true only when **both** digests are in
    hand and they differ. A header carrying no `SCHEDULE_DIGEST=` and a schedule that will not read
    both report `False` with `reason` set, and neither is evidence of staleness -- advising on
    either would publish a substituted conclusion as a measured one, which is the failure
    CONTEXT.md forbids outright, in the shape an advisory can take. The unknown cases are logged so
    an operator can see them, and say nothing on the wire.

    Two conditions return empty without ever calling the comparator, and both are the same
    judgement: **there is nothing to compare against, which is not the same as a match.** No
    `sched:<grid>` registered at all (a hand-edited manifest, or a blend restored beside a project
    whose schedule record was removed) and a schedule path outside every allowed root are absences
    of the right-hand side, not evidence about the left.

    ## The code

    `BLIND_SCHEDULE_DRIFTED_SINCE_BLEND` at `warn`, allocated 2026-08-30 by ruling R191 (owner
    decision), with this emitter in the same commit. Named for the condition rather than for the
    check: what is wrong is that the sealed schedule has moved on since the blend was computed, and
    the blend -- every hour of which was decided by the schedule bytes it no longer matches -- is
    what a caller is being served. **Not `MATRIX_CONTENT_DRIFTED`**, whose condition is *this file
    no longer hashes to what was sealed* and which is an `error` refusing the read: here the matrix
    is intact, its own digest is unmoved, and every row of it is a real sum of real matrices. Only
    the *input that chose between them* has changed, which is a caveat on a number and not a reason
    to withhold it. **Not `MATRIX_REUSED`** either, which names a build this server *skipped*;
    nothing was skipped here, and the blend on disk was the right answer when it was written.

    **The code is written as a literal at the `Advisory(...)` keyword, not held in a module
    constant, and that is deliberate.** `tests/test_advisories.py` walks `src/` with an AST scan
    that reads `code=` only where it is a literal string, and asserts every emitted code is
    allocated in `docs/tool-surface.md`'s register at the severity it is emitted with. A constant
    here would have made this module's newest code **invisible to that scan** -- the register check
    would have passed while the register said nothing, which is a green test proving nothing rather
    than a defect it could catch. Measured: with `code=_STALE_BLEND_CODE`,
    `tests/test_advisories.py` was green on a register that did not allocate the code at all.
    """
    # **A cost short-circuit, not the correctness guard, and saying so is the point.** Measured
    # (mutation M17b*): removing this line and deriving the grid from any id leaves every
    # assertion in `TestTheStaleBlendAdvisory` green, because `ScheduleDigestCheck.mismatched` is
    # already false for a header carrying no `SCHEDULE_DIGEST=` -- which every unblended `.ill`
    # is. What this buys is that an unblended read never pays a bounded file read and a hash it
    # can only ever answer "nothing" to. The polarity below is what makes the answer right.
    if not artifact_id.startswith(_BLENDED_PREFIX):
        return ()
    grid = artifact_id[len(_BLENDED_PREFIX):]
    sched_id = blind_schedule_id(grid)
    try:
        record = loaded.manifest.artifact(sched_id)
    except KeyError:
        logger.info(
            "results: %s cannot be checked against %s -- no such artifact is registered",
            artifact_id, sched_id,
        )
        return ()
    try:
        sealed_path = _contained_schedule_path(runtime, record)
    except (PathOutsideRoot, RootsNotConfigured) as exc:
        logger.info(
            "results: %s cannot be checked against %s -- %s", artifact_id, sched_id, exc
        )
        return ()

    check = compare_schedule_digest(header, sealed_path)
    if not check.mismatched:
        if check.reason is not None:
            logger.info(
                "results: %s carries no comparable schedule digest -- %s", artifact_id,
                check.reason,
            )
        return ()
    # Final fix F, fix round 3 (H2): the re-run spelled whole from the records, under the edition
    # the schedule was sealed under; it was `run_annual(..., blind_control='lm83')`.
    try:
        sealed_edition = sealed_lm83_edition(record.parameters)
    except ValueError:
        sealed_edition = DEFAULT_LM83_EDITION
    first = _bind_first_clause(loaded, project=project)
    rerun = (
        f"{first}{'r' if first else 'R'}e-run "
        f"{_rerun_blend_call(loaded, project=project, grid=grid, edition=sealed_edition)}"
    )
    return (
        Advisory(
            code="BLIND_SCHEDULE_DRIFTED_SINCE_BLEND",
            severity="warn",
            field="grid",
            message=(
                f"{artifact_id!r} was blended against a schedule this project no longer holds: "
                f"its header declares {SCHEDULE_DIGEST_KEY}={check.declared}, and {sched_id!r} on "
                f"disk now hashes to {check.sealed}. Every hour of this matrix was decided by the "
                "schedule it declares -- which state each window group was in, and therefore "
                "which per-group contribution was summed -- so the numbers reported here are "
                "correct for that schedule and not for the one a fresh read of "
                "results(action='blind_schedule') will show. The matrix itself is intact and is "
                "reported rather than withheld: nothing about its own bytes has drifted. "
                f"{rerun} to rebuild the blend from the schedule the project holds now."
            ),
        ),
    )


def _blend_band_advisories(
    runtime: Runtime, *, loaded: LoadedProject, artifact_id: str, project: str,
) -> tuple[Advisory, ...]:
    """`BLIND_SCHEDULE_BAND_NOT_CURRENT` (warn) on every read of a blend whose `sched:<grid>` was
    scanned under another LM-83-12 section 2.2.6 row than the grid's recorded polygon selects now
    (final fix F, fix round 4, K3) -- guard 7's state (`_guard_7_reason`), which
    `results(action='blind_schedule')` withholds `groups` for, said where the blend is read.

    Reachable with nothing tampered with (measured through the real tools by the round-3
    re-review): a grid re-created over a polygon whose area crosses a row's edge but on which
    LM-83's centred 0.6 m placement puts the same points leaves the `.pts` bytes, its digest and
    every result current, while the row its schedule was scanned under is no longer the grid's.
    Every hour of the blend was decided under the old row, so its values are correct for that
    trigger and not for the one blind_schedule applies now -- a caveat on the number, carried like
    `BLIND_SCHEDULE_DRIFTED_SINCE_BLEND`, not a refusal.

    Compared under the edition the schedule was sealed under, by `same_closing_rule` on the band
    as guard 7 compares it: under LM-83-22 there is one rule at every area, so nothing is said. A
    schedule that cannot be read, or a grid whose area cannot be derived, says nothing here: other
    reads and refusals own those."""
    if not artifact_id.startswith(_BLENDED_PREFIX):
        return ()
    grid = artifact_id[len(_BLENDED_PREFIX):]
    sched_id = blind_schedule_id(grid)
    try:
        record = loaded.manifest.artifact(sched_id)
        sealed_edition = sealed_lm83_edition(record.parameters)
    except (KeyError, ValueError):
        return ()
    if sealed_edition == LM83_22:
        return ()
    try:
        with open_regular_file(_contained_schedule_path(runtime, record), "rb") as handle:
            if os.fstat(handle.fileno()).st_size > _MAX_SEALED_SCHEDULE_BYTES:
                return ()
            document = json.loads(handle.read(_MAX_SEALED_SCHEDULE_BYTES + 1).decode("utf-8"))
        area = _analysis_area(loaded, grid=grid, edition=sealed_edition)
    except (
        OSError, NotARegularFileError, PathOutsideRoot, RootsNotConfigured, UnicodeDecodeError,
        ValueError, ProjectError,
    ):
        return ()
    sealed_band = document.get("band") if isinstance(document, dict) else None
    if not isinstance(sealed_band, str):
        return ()
    band = _area_band(area.area_sf, edition=sealed_edition)
    if same_closing_rule(sealed_band, band):
        return ()
    first = _bind_first_clause(loaded, project=project)
    rerun = (
        f"{first}{'r' if first else 'R'}e-run "
        f"{_rerun_blend_call(loaded, project=project, grid=grid, edition=sealed_edition)}"
    )
    return (
        Advisory(
            code="BLIND_SCHEDULE_BAND_NOT_CURRENT",
            severity="warn",
            field="grid",
            message=(
                f"{artifact_id!r} was blended under {sched_id!r}, which was scanned under LM-83 "
                f"section 2.2.6's {sealed_band!r} row ({_area_sf_of(document)}), and the grid's "
                f"recorded polygon now selects the {band!r} row ({area.area_sf:.1f} sf). Every "
                "hour of this matrix was decided under the old row's trigger -- which state each "
                "window group was in, and so which per-group contribution was summed -- so the "
                "values reported here are correct for that trigger and not for the one "
                "results(action='blind_schedule') applies now, and that read publishes no "
                f"per-group schedule from {sched_id!r} (BLIND_SCHEDULE_NOT_PER_GROUP). {rerun}, "
                "to re-scan under the polygon the grid is registered with now and rebuild the "
                "blend."
            ),
        ),
    )


def _mixed_basis_blend_advisories(
    *, loaded: LoadedProject, artifact_id: str, header: AnnualMatrix, project: str,
    edition: Lm83Edition = DEFAULT_LM83_EDITION,
) -> tuple[Advisory, ...]:
    """Was this blend's schedule derived on a grid dense enough for the band it selected?
    -- **owner ruling R242, the read half.**

    ## Under `edition="LM-83-22"` (R864, design section 2, row 10)

    **Nothing, when the blend's `sched:<grid>` is registered.** The finding says the schedule's
    area row and its point count came from different bases, which over- or under-closed the blinds
    against LM-83-12's table. LM-83-22 has no table: its rule is more than 2 % of the points at
    every area. A blend whose schedule record is registered and that the read did not refuse was
    decided by exactly that rule -- sealed under LM-83-22, or under LM-83-12 over a polygon of
    1 000 sf or more (`_refuse_edition_mismatch`) -- so the finding is untrue of the number judged.

    **Nothing, under any edition, when that record was sealed under LM-83-22** (final review, W1):
    the reads that take no edition (`points`, `summary`, `timeseries`, `export`, `metrics` UDI)
    reach here under the default, and the sealed edition, read through `sealed_lm83_edition`,
    decided the hours, not the read. A recorded edition outside the two is not LM-83-22: the
    finding is kept as on a record-less blend, saying why (round 2, controller ruling), and the
    LM-83 reads refuse the record as `LM83_EDITION_MISMATCH`.

    **The finding, naming LM-83-12's table, when none is registered** (a hand-edited or restored
    manifest): nothing then says which edition decided the hours, and such a blend is most likely
    one sealed under LM-83-12 before R864, where the finding holds. The message says that, and
    cites the clause as LM-83-12's own, so an LM-83-22 payload never carries it as LM-83-22's.
    The default edition's message is unchanged.

    ## What was wrong, measured rather than reasoned

    `results(action="blind_schedule")` refuses `BLIND_TRIGGER_MIXED_BASIS` at `error` below
    `LM83_BLIND_AREA_GENERAL_SF`, in a message that states the harm in its own words: *"any sDA
    computed against the resulting schedule is OVERSTATED"*. That sDA is published by
    `compliance`, `summary`, `metrics`, `points`, `timeseries` and `export`, and until this
    function **none of them said anything at all**. Re-measured on one project and one grid at
    `4d60d58`: `blind_schedule` refused at a density of 25 %, and the `compliance` payload taken
    on the same project in the same interpreter was byte-identical to the one taken before the
    polygon moved -- `{"sda": {"value": 0.0, "artifact": "illblend:floor"}}`, advisories
    `['ASE_BAND_IS_RESEARCH', 'LM83_EDITION']`. One rule, three surfaces, one enforcement.

    ## Why an advisory here and not a refusal -- R174 is not reversed

    `compliance` **never refuses**, and R242 keeps it that way deliberately: the artifact this is
    about was sealed by a run that has already been paid for, and withholding the number leaves a
    caller holding an `.ill` no action will read. What R242 adds is the *severity*: `error`, which
    `rad_mcp.envelope`'s own contract defines as describing **the value** rather than the call
    (`is_error` describes the call). So the payload that carries the wrong verdict carries the
    signal, and a caller reading an artifact sealed before this fix is told.

    The plan side is where the refusal lives -- `rad_engine.chain.blind_schedule_steps` now raises
    before the first `rcontrib`, so no *new* overstated number is computed. This function exists
    for the artifacts that already exist.

    ## The two severities, and why the split is Task 16's and not a new decision

    Identical to `_blind_schedule`'s own polarity, from the same two constants, for the reason
    ruling R156 measured:

    - **below `LM83_BLIND_AREA_GENERAL_SF`** section 2.2.6's criterion is an absolute count
      (`> 5`, `> 3`, `> 1`) against an unchanged threshold, so a sparse grid puts fewer points in
      sun, **under**-closes, and the sDA read off the blend is **OVERSTATED** -- the flattering
      direction. `error`.
    - **at or above it** the criterion is `> 2 %` of points, a ratio in which the count cancels;
      what does not cancel is which row the area selected, and that error **over**-closes and
      **understates** sDA. Conservative, so `warn` -- the same trade `_blind_schedule` makes for
      the schedule itself.

    ## Never a refusal, in five conditions, and each is an absence rather than a finding

    A grid that is not registered, a registration with no usable polygon, a polygon that cannot be
    gridded (`points_expected is None`), a header declaring no positive point count, and an
    artifact that is not a blend at all. None of them is evidence that the density is wrong, and
    advising on any of them would publish a substituted conclusion as a measured one. They are
    logged and say nothing on the wire -- `_stale_blend_advisories`' own posture, one measurement
    over.

    **`GridNotFoundError` and `_AnalysisAreaNotDerivable` are caught here and nowhere above**,
    which is the whole of why this is a separate function from `_analysis_area`'s other callers:
    both are `ProjectError`s that `results` shapes into a **refusal**, and letting one escape from
    this call site would turn every `compliance` read of a blend on a hand-edited manifest into
    the refusal R174 says this action does not make.
    """
    if not artifact_id.startswith(_BLENDED_PREFIX):
        return ()
    grid = artifact_id[len(_BLENDED_PREFIX):]
    sched_id = blind_schedule_id(grid)
    under_lm83_22 = edition == LM83_22
    try:
        record = loaded.manifest.artifact(sched_id)
    except KeyError:
        record = None
    if record is not None:
        if under_lm83_22:
            return ()
        # **The sealed edition, not the call's** (final review, W1): every read that takes no
        # edition -- points, summary, timeseries, export, metrics(udi) -- reaches here under the
        # default, and a schedule sealed under LM-83-22 was decided by its 2 % rule whatever the
        # read asked.
        try:
            sealed: str | None = sealed_lm83_edition(record.parameters)
        except ValueError:
            # Round 2 (controller ruling): a recorded edition outside the two is not LM-83-22, so
            # the finding is kept as on a record-less blend, saying why; the LM-83 reads refuse the
            # record as `LM83_EDITION_MISMATCH` (`_refuse_sealed_edition_mismatch`).
            sealed = None
        if sealed == LM83_22:
            return ()
    else:
        sealed = None
    try:
        area = _analysis_area(loaded, grid=grid)
    except (GridNotFoundError, _AnalysisAreaNotDerivable) as exc:
        logger.info(
            "results: %s cannot be checked for a mixed basis -- %s", artifact_id, exc
        )
        return ()
    # **`header.points`, the number the matrix actually carries**, and `<= 0` rather than `== 0`
    # for the reason `_blind_schedule`'s own widening records: `NROWS` comes out of an `.ill`
    # header and a hand-edited manifest can agree with it, so `-5` reaches here as `-5` and
    # `lm83_grid_point_density` raises on it. A nonsense header is a matrix to refuse elsewhere,
    # not a density to publish.
    points_total = header.points
    if points_total <= 0 or area.points_expected is None:
        logger.info(
            "results: %s carries no measurable grid density -- points=%r, expected=%r",
            artifact_id, points_total, area.points_expected,
        )
        return ()
    density = lm83_grid_point_density(points_total, area.points_expected)
    if density >= LM83_GRID_POINT_DENSITY_MIN:
        return ()
    overstated = area.area_sf < LM83_BLIND_AREA_GENERAL_SF
    direction = (
        "the blinds stayed open in hours the standard closes them, more daylight was retained "
        "than the standard allows, and every sDA reported off this matrix is OVERSTATED"
        if overstated else
        "this trigger fired sooner than one keyed on the region the grid actually samples, so the "
        "blinds closed in hours the standard leaves them open and every sDA reported off this "
        "matrix is UNDERSTATED, never overstated"
    )
    # Under LM-83-22 this is reached only with no `sched:<grid>` registered (above): the clause is
    # named as LM-83-12's own, and the opening says why an LM-83-22 read carries it at all.
    if under_lm83_22:
        opening = (
            f"{artifact_id!r} was read under LM-83-22, whose 2 % rule has no area table, but no "
            f"{sched_id!r} is registered beside it, so nothing says which edition's rule decided "
            "its hours; a schedule sealed before lm83_edition existed is LM-83-12's, and if this "
            "one was, it was derived on a grid too sparse for the band that schedule's own area "
            "selected. "
        )
        basis = _MIXED_BASIS_SENTENCE.replace(
            "LM-83 section 2.2.6", section("blind_trigger", DEFAULT_LM83_EDITION), 1
        )
        row = f"{section('blind_trigger', DEFAULT_LM83_EDITION)}'s"
        # The direction is as conditional as the premise the opening states.
        consequence = "; if this blend was sealed under LM-83-12, "
        # Final fix F, fix round 3 (H2): spelled whole from the records; it was
        # `run_annual(project=..., ..., epw=..., north=...)`.
        first = _bind_first_clause(loaded, project=project)
        repair = (
            "To get a number decided by the rule this read applies, "
            + (f"{first[0].lower()}{first[1:]}" if first else "")
            + f"re-run {_rerun_blend_call(loaded, project=project, grid=grid, edition=LM83_22)}, "
            "which re-derives the schedule and the blend under LM-83-22's 2 % rule and registers "
            f"{sched_id!r} beside them."
        )
    else:
        opening = (
            f"{artifact_id!r} was blended against a schedule derived on a grid too sparse for "
            "the band that schedule's own area selected. "
        )
        basis = _MIXED_BASIS_SENTENCE
        row = "section 2.2.6's"
        consequence = ", so "
        # Final fix F, fix round 3 (H2): both calls spelled from the records, as
        # LM83_EDITION_MISMATCH's sparse-grid remedy spells them (G4); they were
        # `sensor_grid(..., polygon=[[x, y], ...])` and `run_annual(project=..., epw=..., ...)`.
        repair = (
            "To get a number computed against a grid LM-83's own rule would place, "
            f"re-create it with {_lm83_grid_recreate_call(loaded, project=project, grid=grid)} "
            "and re-run "
            f"{_rerun_blend_call(loaded, project=project, grid=grid, edition=DEFAULT_LM83_EDITION)}"
            ", which re-derives the schedule and the blend over the denser grid."
            + _lm83_grid_recreate_caveat(loaded, grid=grid)
        )
        if record is None or sealed is None:
            # W1: with no record, or one naming no edition, nothing says the blend was sealed under
            # LM-83-12 either. Round 2: the sensor_grid call takes the edition too, for its height.
            unrecorded = (
                f" No {sched_id!r} is registered beside this blend"
                if record is None else
                f" {sched_id!r} records lm83_edition="
                f"{record.parameters.get('lm83_edition')!r}, which is not an LM-83 edition"
            )
            repair += (
                f"{unrecorded}, so nothing records which edition sealed it: if this study is "
                f"judged under LM-83-22, pass lm83_edition={LM83_22!r} to that sensor_grid call "
                # Fix round 3 (H2): the call now spells the recorded height, which an edition
                # does not override, so the edition goes in its place where one is spelled.
                + ("in place of its height "
                   if (_recorded_grid_create(loaded, grid) or {}).get("height") is not None
                   else "")
                + "(for LM-83-22's 0.76 m workplane), to that run_annual call and to every LM-83 "
                "read of it, since LM-83-22's 2 % rule has no area table."
            )
    message = (
        f"{opening}{basis} This matrix "
        f"declares {points_total} sensor point(s); LM-83's own 0.6 m placement over the "
        f"polygon recorded for grid {grid!r} ({area.area_m2:.1f} m2, {area.area_sf:.0f} "
        f"sf, from the {area.area_source}) puts {area.points_expected} inside it -- a "
        f"density of {density:.0%}, below {LM83_GRID_POINT_DENSITY_MIN:.0%}. Below "
        f"{LM83_BLIND_AREA_GENERAL_SF:.0f} sf {row} row is an absolute point "
        "count rather than a percentage, and at or above it the count cancels but the row "
        f"selection does not -- this area is {area.area_sf:.0f} sf{consequence}{direction}. The "
        "matrix itself is intact and is reported rather than withheld: every row of it is "
        f"a real sum of real matrices, and what is wrong is the schedule that chose between "
        f"them. {repair}"
    )
    # **Two `Advisory(...)` constructions over one message, rather than one with a computed
    # `severity=`.** `tests/test_advisories.py` reads `severity=` only where it is a **literal**,
    # and a conditional expression there made this emitter's severity unreadable to the scan --
    # measured: `test_the_code_is_allocated_and_the_severity_with_it[...-None-2203]` failed rather
    # than passing, because that test refuses to skip a severity it cannot read (its own comment
    # records the six codes an earlier `if severity is None: return` had silently unasserted).
    # Adding the code to `_SEVERITY_NOT_STATICALLY_READABLE` would have made it green while making
    # the register check say nothing here, which is the shape `_stale_blend_advisories` records for
    # the `code=` half of the same scan. The message is built once above; only the letter differs.
    if overstated:
        return (Advisory(
            code="BLIND_TRIGGER_MIXED_BASIS", severity="error", field="grid", message=message,
        ),)
    return (Advisory(
        code="BLIND_TRIGGER_MIXED_BASIS", severity="warn", field="grid", message=message,
    ),)


def _resolved_from(
    runtime: Runtime, *, loaded: LoadedProject, paths: ProjectPaths,
    registered: dict[str, Any], artifact_id: str, grid: str, project: str,
) -> _Resolved:
    """Contain, read and shape **one** of the annual results `_resolve` selected.

    Extracted in Phase 7c Task 17 for the same reason `_contained_matrix` was extracted in Task 12:
    `by_chain` resolves two matrices in one call, and one function is what stops the second being
    resolved a second way. `shadowed_ids` is derived from `registered` here rather than passed in,
    so the entries of a `by_chain` payload each name the others without any call site computing it.

    **`registered` here is the grid's whole candidate set, not the set this call's selectors
    narrowed to** -- see `_resolve`'s `all_registered`. What `shadowed_ids` feeds is a sentence
    about the project (*"a five-phase result IS registered for this grid"*), and narrowing it by
    `blind_control` would make that sentence deny a result the caller holds exactly when the caller
    passed the argument that hid it.
    """
    artifact = registered[artifact_id]
    path, header = _contained_matrix(runtime, artifact, artifact_id=artifact_id)
    # `parameters.get("method")` is absent for the two-phase seal (`chain.two_phase_steps`'s own
    # `ArtifactSpec` records no `"method"` key) -- expected, and defaulting to `"2phase"` rather
    # than raising is the point: the absence is the normal case for most projects, not a defect.
    return _Resolved(
        loaded=loaded, paths=paths, artifact_id=artifact_id, path=path, header=header,
        project=project, method_used=_recorded_chain(artifact),
        expected_points=artifact.parameters.get("n_points"),
        content_digest=artifact.content_digest,
        # **The whole set, in `_ARTIFACT_PREFIXES` order, not one arbitrary element of it**
        # (ruling R187). See `_Resolved.shadowed_ids` for what the `next(...)` this replaces cost
        # once a grid could hold three candidates.
        shadowed_ids=tuple(other for other in registered if other != artifact_id),
        # **Two independent caveats about the same artifact, concatenated once here** (ruling
        # R251). `_stale_blend_advisories` asks *"was this blend computed against the schedule the
        # project still holds?"*; `_mixed_basis_blend_advisories` asks *"was that schedule derived
        # on a grid dense enough for the band it selected?"*. Neither subsumes the other -- a blend
        # whose schedule never moved can still have been derived on a quarter-density grid -- and
        # both are properties of the **artifact**, so both belong on the field every action that
        # reads a blend already publishes rather than on one action's payload.
        blend_advisories=(
            *_stale_blend_advisories(
                runtime, loaded=loaded, artifact_id=artifact_id, header=header, project=project,
            ),
            *_mixed_basis_blend_advisories(
                loaded=loaded, artifact_id=artifact_id, header=header, project=project,
            ),
            # Final fix F, fix round 4 (K3): guard 7's state, said on the blend too.
            *_blend_band_advisories(
                runtime, loaded=loaded, artifact_id=artifact_id, project=project,
            ),
            # R846: not about a blend, but a property of the artifact in the same way -- a window
            # group dark on every grid reads zero in every action -- so it rides the same field.
            *filter(None, [_dark_window_groups_advisory(loaded, artifact_id, grid)]),
            # Final fix F, fix round 2 (G1): the run compiled the scene without an include.
            *filter(None, [_dropped_includes_advisory(
                loaded, artifact_id, project=project, grid=grid)]),
        ),
    )


def _contained_matrix(
    runtime: Runtime, artifact: Any, *, artifact_id: str
) -> tuple[Path, AnnualMatrix]:
    """Contain one manifest-registered annual matrix path, read its header, and cross-check the
    header against the record that names it. **The only way this module opens a manifest path.**

    Extracted from `_resolve` in Phase 7c so the direct-sun matrix (`illdir:<grid>`) is opened the
    same way and not a second way. The two guards below are a pair, and their value is entirely in
    being applied *together* and *in this order*; a second call site that reproduced one of them
    and forgot the other would be a security regression that every test of the first call site
    still passed. `tests/test_results_tool.py::
    test_resolve_and_the_direct_lookup_take_the_same_path_through_containment` asserts structurally
    that there is one of each in this module, because "two call sites that agree today" is exactly
    the state this extraction exists to prevent.

    **Keyed on the artifact, never on `method_used`.** Phase 7c Task 11 made `illdir:` reachable on
    two-phase runs as well, so the chain a project ran is no longer a proxy for which matrices it
    holds. The caller passes the record it already resolved and the id it resolved it under; this
    function knows nothing about chains.

    Order, which is the whole point and must not be rearranged:

    1. `contain` -- R48's *"containment checked before the syscall"*, and R50's *"a path out of the
       manifest is untrusted on every action that touches it"*. Registered is not currently valid.
    2. `read_annual_header` -- the first and only syscall, and it reads the header alone. `_resolve`
       runs on every action except `blind_schedule`, including the ones that then refuse, so paying
       for a multi-gigabyte body read to answer "which grid?" would be the wrong order.
    3. the header/record cross-check -- engine invariant 3, *before any metric is computed* and,
       more to the point, before any caller sizes an allocation from `header.points`/`header.hours`.
       `_resolve`'s docstring carries the measurement: a record declaring `NROWS=2000000000`
       attempted ~1.3 TB and OOM-killed the server while the refusal that would have stopped it
       waited two lines further down.

    Raises `PathOutsideRoot` (shaped into a `PATH_OUTSIDE_ROOT` refusal by the tool's handler
    chain) or `_MatrixShapeMismatch` (`MATRIX_SHAPE_MISMATCH`). Every caller's own selection
    refusals must still sit *above* this call, not below it -- `results` publishes no `dry_run`, so
    R39's placement rule puts them before any path resolution or file access at all.
    """
    path = contain(artifact.path, runtime.roots)
    header = read_annual_header(path)
    _ensure_header_matches_manifest(
        artifact_id=artifact_id, path=path, header=header,
        expected_points=artifact.parameters.get("n_points"),
        expected_hours=artifact.parameters.get("n_hours"),
    )
    return path, header


class _MatrixShapeMismatch(ProjectError):
    """The `.ill`'s own header disagrees with the manifest record that names it.

    `MATRIX_SHAPE_MISMATCH` at `error` -- engine invariant 3's allocated code, whose register row
    is *"matrix shape is checked against (`n_sensors`, `n_timesteps`) before any metric is
    computed ... refuse on mismatch"*. That is exactly this condition and exactly this placement:
    raised from `_resolve`, before any action has read a row or sized anything from the header.

    Not `ARGUMENT_OUT_OF_RANGE` (R14: an argument refused *before any work was attempted* -- the
    project is open and a header has been read by this point), and not a code-less refusal, because
    the register does allocate a code for this and a client branching on it can act: the file and
    the record have drifted, and re-running `run_annual` for the grid re-seals both.
    """

    code = "MATRIX_SHAPE_MISMATCH"


class _MatrixTooLarge(ProjectError):
    """The shape a matrix declares is beyond anything this server can have produced.

    `MATRIX_TOO_LARGE_TO_READ` at `error`. **Not `MATRIX_SHAPE_MISMATCH`**: nothing mismatches here
    -- the header and the manifest agree, and what is refused is the number they agree *on*.

    **Why the cross-check could not be the bound.** `_ensure_header_matches_manifest` was the
    previous whole-branch review's fix for this same defect, and `_contained_matrix`'s docstring
    still credits it: *"a manifest declaring `NROWS=2000000000` attempted ~1.3 TB and OOM-killed
    the server."* It compares the header against the manifest, and ruling R50 holds a manifest
    untrusted -- so a hand-edited one edits both sides and the cross-check passes. Two guards are
    needed and only one existed: agreement, and magnitude.

    Measured at `6abd6ce` on this machine: `results(action="metrics")` against a record declaring
    `NROWS=400 000` over a three-row file built 400 000 `PointTally` objects and grew RSS
    43 -> 343 MB in 0.94 s, and only then hit the truncation refusal two lines below.
    `lm83_window(8_000_000)` costs 0.64 s and ~342 MB and is followed by an `is_whole_year` gate
    that returns `False` for it. Neither is a slow path; both are the refusal arriving after the
    cost it exists to avoid.

    **One place, four call sites inherit it**, because `_contained_matrix` is the only way this
    module opens a manifest path and this runs inside it -- so the bound is above every allocation
    every caller sizes from the header, which is R39's placement rule applied to the quantity R39
    is actually about.
    """

    code = "MATRIX_TOO_LARGE_TO_READ"
    field = "grid"


def _ensure_header_matches_manifest(
    *, artifact_id: str, path: Path, header: AnnualMatrix,
    expected_points: int | None, expected_hours: int | None,
) -> None:
    """Raise `_MatrixShapeMismatch` if the header's `NROWS`/`NCOLS` disagree with what the manifest
    recorded for this artifact, and `_MatrixTooLarge` if the shape they agree on is beyond anything
    this server can have produced. Silent when a count was never recorded -- see `_resolve`'s own
    docstring for why `n_hours` is absent on the two- and three-phase seals.

    **Both guards, and the magnitude one is not redundant.** The cross-check answers "is this the
    file that record describes"; the bound answers "could this server have produced a file that
    shape at all". A hand-edited manifest satisfies the first trivially by editing both sides
    (R50), which is why the first cannot be the second. See `_MatrixTooLarge`.

    **Both numbers are checked, and the manifest is the independent statement that makes the check
    able to fail at all.** A header alone cannot be wrong: two integers are consistent with any
    story about them (`iter_annual_lux_rows`' own words). The manifest's `n_points`/`n_hours` were
    written by the chain that produced the file, from the sensor grid and the weather record it
    actually ran against, so a disagreement means the file on disk is not the file that record
    describes -- a truncated write, a hand-edited manifest, or a matrix belonging to another grid.
    """
    # **The magnitude bound runs FIRST**, above the cross-check as well as above every caller: a
    # two-billion-row header that its manifest agrees with is refused for being two billion rows,
    # not reported as agreeing. The thresholds are `rad_core`'s -- this layer carries none
    # (CONTEXT.md, Layers) -- and both are stated as upper bounds, so a shape exactly at either is
    # accepted.
    #
    # **The order is pinned by `tests/test_results_tool.py::
    # TestTheMagnitudeBoundRunsAboveTheCrossCheck`**, and it is the only thing that pins it: it
    # reaches both guards at once by making the header implausible **and** the record disagree with
    # it, which is the state `_oversized_project` cannot reach because agreement is that fixture's
    # stated point. Measured with the two blocks moved below the cross-check, the whole of
    # `tests/test_results_tool.py` was 541 passed / 2 failed and both failures were those new rows;
    # every other test in the file, the thirteen in
    # `TestADeclaredShapeIsBoundedBeforeAnythingIsSizedFromIt` included, is blind to the order.
    if header.points > MATRIX_MAX_DECLARED_POINTS:
        raise _MatrixTooLarge(
            f"the annual matrix at {path} declares NROWS={header.points} sensor point(s) for "
            f"{artifact_id!r}, above the {MATRIX_MAX_DECLARED_POINTS} this server will read. "
            "Every action sizes one object per declared point before reading a row, so the "
            "declared count is an allocation instruction and is bounded rather than trusted -- "
            "the manifest record it agrees with is not independent of it (ruling R50: a "
            "hand-edited manifest edits both sides of the shape cross-check). Nothing has been "
            "allocated and nothing has been read. Re-run run_annual for this grid."
        )
    if header.hours > MAX_DECLARED_HOURS:
        raise _MatrixTooLarge(
            f"the annual matrix at {path} declares NCOLS={header.hours} hourly column(s) for "
            f"{artifact_id!r}, above the {MAX_DECLARED_HOURS} this server will read -- a leap "
            "year at one-minute resolution, which is finer than anything gendaymtx produces. "
            "The analysis-period sets every LM-83 and EN 17037 action builds are sized from this "
            "count. Nothing has been allocated and nothing has been read. Re-run run_annual for "
            "this grid."
        )
    if expected_points is not None and header.points != expected_points:
        raise _MatrixShapeMismatch(
            f"the annual matrix at {path} declares NROWS={header.points} while the manifest "
            f"records {expected_points} sensor point(s) for {artifact_id!r}. The file and the "
            "record have drifted apart, so every row would be labelled with a sensor it is not, "
            "and any aggregate over them would be computed for a grid that did not produce them. "
            "Re-run run_annual for this grid to seal a matching pair."
        )
    if expected_hours is not None and header.hours != expected_hours:
        raise _MatrixShapeMismatch(
            f"the annual matrix at {path} declares NCOLS={header.hours} while the manifest records "
            f"{expected_hours} hourly column(s) for {artifact_id!r}. The file and the record have "
            "drifted apart, so every column would be labelled with an hour it is not, and every "
            "period-scoped tally (LM-83's 08:00-18:00 window, EN 17037's daylight hours) would be "
            "taken over the wrong columns. Re-run run_annual for this grid to seal a matching "
            "pair."
        )


def _latitude_outside_calibration_advisory(
    latitude: float, *, reported: str = "DA_300 is", edition: Lm83Edition = DEFAULT_LM83_EDITION,
) -> Advisory:
    """`LM83_LATITUDE_OUTSIDE_CALIBRATION` for a site outside `edition`'s latitude band.

    Shared by `_compliance_lm83` and `_metrics` rather than written twice: they publish different
    numbers from the same standard's same research over the same period, and `reported` is the one
    clause that differs. Two copies of a caveat is how one of them acquires a different band or a
    different citation with every test green -- the shape `_negative_cell_advisory` above was
    lifted for.

    **The band and its clause are `edition`'s (R864)**: LM-83-12's 37-48 N, where its supporting
    research was conducted (section 2.2.1 DISCUSSION), or LM-83-22's 35 to 55 degrees, the
    "similar latitudes" its section 1.3 says the studied spaces are most applicable to -- both
    signed, north-positive, by this server's choice (`rad_core.compliance.lm83_latitude_band`).
    The clause comes from `section`, so the LM-83-12 message now names its edition where it said
    "ch04 section 2.2.1 DISCUSSION".
    """
    low, high = lm83_latitude_band(edition)
    clause = section("latitude", edition)
    if edition == LM83_22:
        band = (
            f"the {low:.0f}-{high:.0f} band LM-83-22's studied spaces are most applicable to "
            f"({clause}: \"similar latitudes (35 to 55 degrees)\"; signed, north-positive, by this "
            "server's choice)"
        )
        basis = "the research the edition rests on"
    else:
        band = f"the {low:.0f}-{high:.0f} N band LM-83's supporting research was conducted in ({clause})"
        basis = "what calibrated the bands"
    return Advisory(
        code="LM83_LATITUDE_OUTSIDE_CALIBRATION",
        severity="warn",
        message=(
            f"the site latitude is {latitude:.2f}, outside {band}. "
            f"{reported} still computed and reported here; this caveat is that the sky "
            f"conditions this latitude actually sees were not part of {basis}."
        ),
    )


class _NegativeCount:
    """A running count of the cells an action read and of those below zero lux, with the lowest
    value seen -- what `NEGATIVE_ILLUMINANCE_PRESENT` reports.

    **Every action but LM-83 compliance folds each row in here** (`observe_row`): `summary`,
    `points`, `timeseries` and `export`, whose figures are not `PointTally`s, and EN 17037 provision
    and `metrics`, which name `windows` so the message can say how many of the whole-year negatives
    fall inside the hours their tallies count. `below zero` is `lux < 0`, the predicate `rad_core.metrics.PointTally.observe` counts
    `negatives` with; folding 4 million values through one `PointTally` was measured at 2.9 s
    against 0.3 s for the bare comparison (the 456-point office grid's year is 3 994 560 cells),
    which `summary` would pay for one integer.
    LM-83 compliance adds its `PointTally.result()`s instead (`add`), so the count it publishes is
    the one `rad_core` decided.
    """

    __slots__ = ("cells", "negatives", "minimum", "_windows", "in_window")

    def __init__(self, windows: dict[str, frozenset[int]] | None = None) -> None:
        self.cells = 0
        self.negatives = 0
        self.minimum: float | None = None
        self._windows = dict(windows or {})
        #: Per named window, how many of the negative cells fall in its columns. The count above
        #: is whole-year; a tally built over a window counts only these (`PointTally.observe`).
        self.in_window: dict[str, int] = {name: 0 for name in self._windows}

    def observe_row(self, row: Sequence[float]) -> None:
        """Fold in one sensor row, column `hour` at index `hour`. The one place this module
        decides a cell is negative; it runs once per cell of the matrix."""
        self.cells += len(row)
        for hour, lux in enumerate(row):
            if lux < 0:
                self.negatives += 1
                if self.minimum is None or lux < self.minimum:
                    self.minimum = lux
                for name, columns in self._windows.items():
                    if hour in columns:
                        self.in_window[name] += 1

    def add(self, result: TallyResult) -> None:
        """Fold in one point's whole-year `count`/`negatives`/`minimum`, as `rad_core` computed
        them. `minimum` is the point's lowest value, which is below zero exactly when the point
        carries a negative cell -- and only then is it the minimum the advisory reports."""
        self.cells += result.count
        self.negatives += result.negatives
        if result.negatives and result.minimum is not None and (
            self.minimum is None or result.minimum < self.minimum
        ):
            self.minimum = result.minimum


#: The sentence every `NEGATIVE_ILLUMINANCE_PRESENT` on a five-phase result carries (R814). It used
#: to say the threshold tallies were "unaffected" and "correct as computed", which was arithmetic
#: only: a cell traced on an earlier run of the office fixture, at -9 100.44 lux, had a three-phase
#: value of 7 595.32 lux, above every threshold it was counted below. Measured effect on that
#: fixture's sDA, and why nothing is clamped: docs/tool-surface.md section 14 (R814).
_NEGATIVE_TAIL_FIVE_PHASE: Final = (
    "Each negative cell is the five-phase subtraction of two estimates: it is counted below "
    "every threshold, although the same cell's three-phase value may lie above it, so an hour "
    "count against a threshold ({figures}) over this result can read low. No value is clamped; "
    "the count above is as computed."
)

#: The same statement for a two- or three-phase result, where no subtraction made the cell and
#: there is no three-phase value to compare it with.
_NEGATIVE_TAIL_OTHER_CHAIN: Final = (
    "A negative cell is counted below every threshold, so an hour count against a threshold "
    "({figures}) over this result can read low. No value is clamped; the count above is as "
    "computed."
)

#: The threshold figures a negative cell of the chain result can pull low. ASE is one of them
#: only when it was tallied from that result; LM-83 compliance passes `ase_from_this_result=False`
#: when it came from `illdir:<grid>`, whose cells this count does not include.
_NEGATIVE_FIGURES_WITH_ASE: Final = "sDA, ASE, DA, EN 17037 provision, UDI's bands above 100 lux"
_NEGATIVE_FIGURES_WITHOUT_ASE: Final = "sDA, DA, EN 17037 provision, UDI's bands above 100 lux"


def _negative_count_advisory(
    state: _Resolved, count: _NegativeCount, *, tail: str, ase_from_this_result: bool = True,
) -> Advisory | None:
    """`NEGATIVE_ILLUMINANCE_PRESENT` over `count`, or `None` when no cell is below zero.

    **One message for every action that reads the chain result** (R814): `compliance` (LM-83 and
    EN 17037), `metrics`, `summary`, `points`, `timeseries` and `export`. Before R814 only LM-83
    `compliance` and `metrics` carried it, and the others published the same five-phase matrix
    with no word about its negative cells. `blind_schedule` reads only `illdir:<grid>`, which
    subtracts nothing, and carries none.

    **The mechanism clause is stated only for a five-phase chain result**, because that is what the
    mechanism is: `Vd*T*Dd*Sds` (the correction term) can exceed `V*T*D*S` (the uncorrected
    three-phase term) at a cell the five-phase combination subtracts, giving a negative published
    lux that is real arithmetic and not a bug -- ch04 carries no floor at zero and `PointTally`
    never clamps. A two- or three-phase chain result is a product of non-negative operands, so a
    negative there is a corrupt or hand-edited matrix. The count is reported either way; only the
    explanation is withheld where it would be false.

    `tail` is the one part that differs per action: which of *its own* published figures the
    negatives are inside. A sentence about `udi_below_100` on a `compliance` payload would be an
    advisory untrue of the payload it travels on (docs/tool-surface.md section 9).
    """
    if not count.negatives:
        return None
    percentage = 100.0 * count.negatives / count.cells if count.cells else 0.0
    five_phase = state.method_used == _LM83_ASE_REQUIRES_METHOD
    mechanism = (
        " -- Vd*T*Dd*Sds can exceed V*T*D*S"
        if five_phase
        else f", which a {state.method_used} chain cannot produce by arithmetic"
    )
    shared = (_NEGATIVE_TAIL_FIVE_PHASE if five_phase else _NEGATIVE_TAIL_OTHER_CHAIN).format(
        figures=(
            _NEGATIVE_FIGURES_WITH_ASE if ase_from_this_result else _NEGATIVE_FIGURES_WITHOUT_ASE
        )
    )
    return Advisory(
        code="NEGATIVE_ILLUMINANCE_PRESENT",
        severity="warn",
        message=(
            f"{count.negatives} of {count.cells} cells ({percentage:.4f}%) in "
            f"{state.artifact_id!r}, this result's {state.method_used} matrix, are below zero "
            f"lux (minimum {count.minimum:.2f}){mechanism}. {shared}"
            + (f" {tail}" if tail else "")
        ),
    )


def _negative_cell_advisory(
    state: _Resolved, tallies: Sequence[PointTally], *, tail: str,
    ase_from_this_result: bool = True,
) -> Advisory | None:
    """`_negative_count_advisory` over `tallies`: LM-83 compliance's entry point.

    **Lifted by the whole-branch fix wave, because there were two of these.** `_compliance_lm83`
    and `_metrics` each carried the aggregation loop, the percentage expression, the whole
    `mechanism` ternary and the message prefix, byte-identical. Each was pinned to itself and
    nothing pinned them to each other, so the two could drift into publishing different
    percentages for the same condition with every test green. Since R814 the message itself lives
    in `_negative_count_advisory`, which the non-tallying actions share.

    **Aggregated from `PointTally.result()`, never re-derived.** `observe` already decided what
    counts as negative (`rad_core.metrics`) and kept the running count and minimum; summing those
    across points is bookkeeping over a value `rad_core` already computed.
    """
    count = _NegativeCount()
    for tally in tallies:
        count.add(tally.result())
    return _negative_count_advisory(
        state, count, tail=tail, ase_from_this_result=ase_from_this_result
    )


class _UnreadableAnnualMatrix(ProjectError):
    """A cell of the annual matrix an action is reading is not a finite illuminance -- either the
    token the file carries is not finite, or the lux it converts to is not.

    `ANNUAL_MATRIX_UNREADABLE` at `error` for a chain result (`ill:<grid>`, `ill5:<grid>`);
    `DIRECT_MATRIX_UNREADABLE` for `illdir:<grid>`, which already had a row of its own. The code is
    chosen per instance by `_lux_rows` below rather than by two classes, because the *condition* is
    one condition -- `rad_engine.results.NonFiniteMatrixCellError`, raised at one seam -- and only
    the matrix it is true of differs. `ProjectError.__init__` takes `code=` for exactly this.

    **Why the two codes are not one.** `DIRECT_MATRIX_UNREADABLE`'s register row is written about
    *"a cell of `illdir:<grid>`"* and about the **blind trigger**: it names `hours_closed: 0`, the
    year of `"open"` states, and `rad_core.blinds`. None of that is true of a chain matrix, whose
    failure is sDA/UDI/DA/statistics rather than a schedule -- and reporting one condition under
    another's definition is the defect docs/tool-surface.md section 9 names. That is the same
    adjudication `050e256` made one matrix upstream when it declined to reuse
    `ASE_DIRECT_MATRIX_EMPTY`, and the same one Task 15's reviewer made on the merits rather than
    by reflex. **`COEFFICIENT_MATRIX_UNREADABLE` is not reused either**: its row is written about
    `asemx:` **coefficient** bytes at **seal** time; this sits at read time over a `dctimestep`
    illuminance matrix.

    A `ProjectError`, so `refuse_engine_error` shapes it and nothing new is threaded through the
    dispatch.
    """

    code = "ANNUAL_MATRIX_UNREADABLE"
    field = "grid"


class _MatrixContentDrifted(ProjectError):
    """The bytes at a registered matrix path no longer hash to the digest the manifest sealed.

    `MATRIX_CONTENT_DRIFTED` at `error`, a sibling of `MATRIX_SHAPE_MISMATCH`: that code catches a
    file whose *shape* disagrees with its record, this one a file whose shape still agrees and
    whose **numbers** have moved. A hand-edited manifest can edit both sides of the shape
    cross-check (ruling R50), and an in-place rewrite of the body needs to edit neither -- measured
    at `6abd6ce`, `compliance` moved ASE 30.0 % -> 100.0 % and `blind_schedule` moved
    `hours_closed` 300 -> 3 650, both with `refused: None` and an identical advisory list.

    **The grading argument is an asymmetry inside this repo, not an external standard.**
    `rad_engine.project.is_reusable` refuses to *reuse* an artifact whose digest drifted, in its own
    words: *"one whose file was ... edited by hand is worse, because it is a matrix of the wrong
    numbers that nothing reports."* The build path refused and the read path published. And
    `_resolve_epw`, in this same module, already verifies the **weather** file's digest under
    ruling R97 and refuses on drift -- so the file a run was built from was checked while the
    matrix whose numbers become the certified sDA/ASE was not.
    """

    code = "MATRIX_CONTENT_DRIFTED"
    field = "grid"


def _lux_rows(
    path: Path, *, artifact_id: str, expected_points: int | None,
    content_digest: str | None,
) -> Iterator[list[float]]:
    """`iter_verified_annual_lux_rows`, with the engine's finiteness and drift refusals shaped into
    allocated codes. The sealed digest is verified before the first row is yielded.

    **Every read of an annual matrix in this module goes through here**, for the reason
    `_contained_matrix` exists: this module holds four unrelated cell-level readers
    (`_compliance_lm83`'s tally, `_metrics`' tally, `_in_sun_counts_by_column`, `_Accumulator`) and
    a guard written into one of them covers one of four. The guard itself is not here -- it is in
    `rad_engine.results`, where the fact *"a cell of a Radiance illuminance matrix must be a finite
    number"* belongs (CONTEXT.md, Layers). This is only the shaping: engine exception -> wire code.

    **The artifact id has to be passed in because the engine's message names the path.** A caller
    reading a refusal needs the manifest id it asked about, not an absolute path inside the project
    directory, to know which artifact to re-run.

    A generator, so the exception surfaces where the row is consumed rather than at construction --
    which is what `iter_verified_annual_lux_rows` already does (it is a generator too, so **its**
    digest check is taken at first iteration, not at construction), and what lets the eight call
    sites keep their loops unchanged.

    ## Why the digest check is here and not in `_contained_matrix`

    The wave's brief put it in `_contained_matrix`, after `contain` and before
    `read_annual_header`. **That placement was measured and declined**, because
    `_contained_matrix`'s cheap-header contract is a deliberate design property and it runs on
    every action *including the ones that then refuse from the header alone* -- sub-annual period,
    shape disagreement, zero points. Hashing there would make a refusal that costs one `read()`
    today cost a full pass over a matrix that is **~6.8 GB** at `GRID_VERY_LARGE`
    (`rad_engine.results`' own sizing table), which is R39's placement rule inverted: work above
    the refusal instead of below it.

    Here, the digest is paid **only on the path that was about to read the whole body anyway**, and
    measured on this machine it is a rounding error against that read: `file_digest` over a
    22.8 MB `.ill` took 0.010 s where consuming `iter_annual_lux_rows` over the same file took
    0.169 s -- **6.0 %**. One site still covers all eight actions plus `by_chain`, because
    `_lux_rows` is now the module's only reader, the same property that made `_contained_matrix`
    the only path-opening seam.

    ## Disclosed limitation: the window is NARROWED, not closed

    This was a **two-pass** check until Phase 7d-2 Task 1: `file_digest(path)` opened, hashed and
    closed, and `iter_annual_lux_rows(path)` then opened the file **twice more**. Three opens, and a
    writer racing between any two of them had its numbers published as measured.

    `iter_verified_annual_lux_rows` now does it on **one** descriptor: open once, hash from that
    handle in blocks, `seek(0)`, read the header, `seek(0)`, stream the rows. The reason the old
    docstring gave for not doing this -- *"an EPW fits in memory and an annual matrix explicitly
    does not"* -- **was wrong, and is retracted here**: one open was never one buffer. Peak memory
    is one 1 MiB block plus one row, exactly what it was before.

    **What that defeats: replacement.** A rename over the path, or an unlink-and-recreate, cannot
    change the bytes behind an already-open descriptor, because the fd binds the inode.

    **What it does NOT defeat: in-place modification of that same inode.** A `pwrite` or an
    `ftruncate` between the hash and the rewind still wins, and nothing in this path can detect it.
    The window is narrowed from "between two opens" to "between two seeks on one fd". It is not
    TOCTOU-proof and this function does not claim to be.

    **And "replacement defeated / in-place not defeated" is not an exhaustive enumeration.** Two
    further residual classes are disclosed in full at the published surface (`docs/tool-surface.md`
    §14, *"Two more residual classes"*; whole-branch review, F7): the digest's **trust root** is
    the same unsigned `manifest.json` that an attacker able to perform the in-place write already
    owns, so the read reports as verified with no race at all; and a **directory symlink planted in
    the parent chain** substitutes the path upstream of both seeks, which fails closed with a
    digest present and open without one. Neither is fixed; both are named so the pair above stops
    reading as complete.

    **And the reader is not the only open.** `_contained_matrix` calls `read_annual_header` on the
    same path before this function ever runs, and **that** header -- not the verified one -- is what
    `_MatrixTooLarge`'s bound, `ShapeView` and every per-point `PointTally` are sized from. The
    one-open property holds for the row read, not for the whole action.

    **Silent when the manifest recorded no digest.** `content_digest` is `None` for an artifact
    sealed by something other than `register_artifact` (`is_reusable` documents the same case), and
    refusing those would be a different decision -- refusing every project sealed before digests
    were recorded -- taken by a guard that was asked to catch drift.
    """
    try:
        yield from iter_verified_annual_lux_rows(
            path, expected_points=expected_points, content_digest=content_digest
        )
    except MatrixContentDriftedError as exc:
        raise _MatrixContentDrifted(
            f"the file registered as {artifact_id!r} no longer matches the bytes this project "
            f"sealed: the manifest records {exc.expected}, the file on disk hashes to "
            f"{exc.actual}. The shape still agrees with the record, so nothing else in this read "
            "path would have noticed -- only the numbers moved, and every metric computed "
            "from them would have been published as measured. It is refused rather than "
            "read. Re-run run_annual for this grid to seal a matching pair, or restore the "
            "file from the run that produced it."
        ) from exc
    except MatrixDigestUnreadableError as exc:
        # This clause replaces the `ProvenanceError` handler the two-pass form needed: `file_digest`
        # opened *and* hashed, and either failing was one `ProvenanceError` shaped into this code.
        # `MatrixDigestUnreadableError` is raised for exactly that phase and no other.
        #
        # **The narrowing is the fix for a false published sentence** (fix round 1, Critical 1).
        # This clause used to catch the base `MatrixFileUnreadableError`, which the engine also
        # raises when a `read()` fails during the *header scan* or the *row loop* -- after the
        # digest has already matched. Those were re-coded as `MATRIX_CONTENT_DRIFTED` and told the
        # operator the file's bytes no longer matched what was sealed and to re-run `run_annual`.
        # Reproduced: an `EIO` on any block after the first produced exactly that. The bytes had
        # not moved; the disk had failed. **No race was needed**, which is what the comment that
        # stood here claimed. A post-digest read failure now keeps the base class and reaches
        # `results`' own `except MatrixFileUnreadableError` clause, which returns a code-less
        # refusal naming the filesystem cause -- the behaviour that predates this task.
        #
        # `MatrixDigestUnreadableError` is raised only where `content_digest is not None`, so no
        # `content_digest is None` guard is needed here: with nothing to verify against, the engine
        # never enters the phase that raises it.
        raise _MatrixContentDrifted(
            f"the matrix registered as {artifact_id!r} could not be digested: {exc}. Its "
            "bytes cannot be shown to be the bytes this project sealed, so no number is "
            "published from them."
        ) from exc
    except NonFiniteMatrixCellError as exc:
        # The **class** is selected, never a code string: `tests/test_advisories.py`'s AST emitter
        # ratchet reads allocations out of class bodies, and a conditional code expression here
        # would be an emission it cannot resolve -- a code reaching the wire that the register
        # ratchet never saw is the exact hole that guard exists to close.
        refusal = (
            _UnreadableDirectMatrix
            if artifact_id.startswith(_DIRECT_ARTIFACT_PREFIX)
            else _UnreadableAnnualMatrix
        )
        raise refusal(
            f"the annual matrix registered as {artifact_id!r} could not be read: {exc}"
        ) from exc


class _NoAnnualResult(ProjectError):
    """**No** annual result -- `ill:<grid>`, `ill5:<grid>` or `illblend:<grid>` -- is registered in
    this project's manifest.

    `illblend:<grid>` joined the candidates at Phase 7d-1 Task 15a, and until it did this refusal
    was what **every** action on a `blind_control="lm83"` project returned: such a run seals no
    whole-grid `ill:<grid>` at all (`chain.three_phase_steps` skips its summing step), so the
    project held a blend, its operands and nothing this class could see.

    **The "Grids with an annual result: ..." sentence lists grids, not artifact ids** (rulings
    R179/R157): ids carrying a further colon after the prefix are the per-(group, state) operands,
    and every one of them is now refused if a caller passes it as `grid=` (R186). See `_resolve`.

    No register code, for `annual_tool.WeatherNotLoadedError`'s stated reason: `grid` is an ordinary
    tool argument naming a handle inside the project, and the register allocates codes for the
    protocol handles rather than for every argument that can miss.
    """

    code = None


def _shape_view(header: AnnualMatrix) -> ShapeView:
    return ShapeView(points=header.points, hours=header.hours, triples=header.triples)


# --- the EPW walk, shared by every consumer that needs the weather input a run was built from -----


#: The artifact-id prefix a sky matrix is sealed under -- `chain._sky_matrix_step`'s own
#: `f"smx:{weather_input_id}"`. Named once because two readers now split an id on it
#: (`_reachable_smx_edges` and `_resolve_epw`) and a bare `"smx"` in each is two statements of one
#: name. Not the sun-disk sky (`smxsun:`), which `str.split(":", 1)[0]` correctly keeps apart --
#: `startswith("smx")` would not.
_SMX_KIND: Final[str] = "smx"

#: The sun-disk sky matrix's prefix -- `chain._sun_disk_sky_step`'s `f"smxsun:{weather_input_id}"`.
#: `illdir:<grid>` is built from `asemx:<grid>` and this, and from no `smx:`, so the one read that
#: starts from it (`_blind_schedule`, R860's window) walks this kind instead.
_SMXSUN_KIND: Final[str] = "smxsun"


def _reachable_smx_edges(
    loaded: LoadedProject, artifact_id: str, *, kind: str = _SMX_KIND
) -> list[str]:
    """Every `smx:` edge reachable from `artifact_id` through `built_from`, deduplicated, in
    breadth-first order. `kind` names another sky-matrix prefix to collect instead
    (`_SMXSUN_KIND`, for `illdir:<grid>`); the walk is otherwise the same.

    **Breadth-first from the artifact itself, so the direct edge every chain result carries is
    found in the first iteration** and a two-, three- or five-phase result pays one pass over one
    short list, exactly as the `next(...)` this replaces did. The traversal only ever goes deeper
    for an artifact that has no `smx:` edge of its own, which today is `illblend:<grid>` and
    nothing else.

    **The `seen` set is not an optimisation.** `built_from` is a manifest field and [R50] holds a
    manifest untrusted on every action that touches it -- a hand-edited cycle would otherwise spin
    this function for ever inside an anyio worker thread, which is the wedge R45 names on the read
    side. Edges naming nothing registered are skipped rather than refused: this function answers
    *"which sky matrices can this result be traced to"*, and an edge to a deregistered input is not
    one of them. `_resolve_epw` reports the empty answer with its own reason.

    Returns a **list** rather than the first hit so its caller can tell "none" from "more than
    one"; see `_resolve_epw` for why an arbitrary pick between two would be a substituted default
    published as a measured value.
    """
    found: list[str] = []
    seen: set[str] = {artifact_id}
    frontier: list[str] = [artifact_id]
    while frontier:
        next_frontier: list[str] = []
        for current_id in frontier:
            try:
                artifact = loaded.manifest.artifact(current_id)
            except KeyError:
                continue
            for edge in artifact.built_from:
                if edge.split(":", 1)[0] == kind:
                    if edge not in found:
                        found.append(edge)
                    continue
                if edge not in seen:
                    seen.add(edge)
                    next_frontier.append(edge)
        # **Stop at the first level that answers.** A `dmx:`/`vmx:` operand of a per-group result
        # names its own inputs, not another sky, so going deeper after an answer is found buys
        # nothing and only widens the set an ambiguity report would have to explain.
        if found:
            return found
        frontier = next_frontier
    return found


def _resolve_epw(state: _Resolved) -> tuple[EPWFile | None, str | None]:
    """The parsed EPW `state`'s run was built from, or `(None, reason)` if it cannot be trusted.
    Thin over `_resolve_weather`, which also returns the weather record the walk found (R860:
    the LM-83 window needs its `clock_zone` even when its EPW cannot be read); the walk and every
    failure it names are documented here.

    **`_Resolved` carries nothing about weather** -- `loaded, paths, artifact_id, path, header` --
    so this walks the manifest graph the same way `chain.py` built it, rather than assuming a route
    exists:

    `state.artifact_id` -> **the first `smx:` edge reachable from its `built_from`, walked
    transitively** -> that `smx:` artifact's own `built_from`, which `_smx_edges` populates with
    the weather input's id -> the registered `InputRecord` of kind `"weather"` among those edges ->
    its **EPW**, told apart from the `.wea` the same record also carries by
    `weather_tool._files_of` -- suffix, never position (*"position is not a guarantee the schema
    makes"*) -- with the matching `FileRef.digest` checked against the file's current bytes before
    it is trusted (ruling R97: *"re-opening files this server does not own"*) ->
    `read_epw_with_digest(...)`, which hashes and parses **one** read so the bytes that satisfy the
    drift check are the bytes that get parsed.

    **The first hop is transitive as of Phase 7d-1 Task 15a, and the invariant this docstring used
    to assert was measured false.** It read: *"its own `built_from`, which **every** chain (2-, 3-
    and 5-phase) names an `f"smx:{weather_input_id}"` edge on directly, not two hops away"*, and
    the walk was a single-level `next(...)` over that one list. `illblend:<grid>` breaks it:
    `chain.blend_steps` names the sealed schedule and every `ill:<grid>:<group>:<state>` operand
    **and nothing else** (`project.REQUIRED_EDGES["illblend"].why`, in shipped prose), so the blend
    carries no `smx:` edge of its own. Task 15b measured the consequence on the shipped code:
    `'illblend:floor'` returned `(None, "'illblend:floor' carries no smx: edge")` while the control
    `ill:floor:north:open` passed. **Live effect:** EN 17037 refused on *every* blended result with
    `EN17037_DAYLIGHT_HOURS_UNAVAILABLE`, and `LM83_LATITUDE_OUTSIDE_CALIBRATION` silently stopped
    firing on blended sDA -- a substituted behaviour with no label, which CONTEXT.md forbids
    outright.

    **The fix is here and not an `smx:` edge on the blend**, and the reasoning is Task 15b's,
    verified rather than taken on trust: `built_from` is the *invalidation* contract (`check_edges`
    asserts it before a step spawns and `register_artifact` again at seal time), not a general
    provenance graph; `REQUIRED_EDGES["illblend"].why` states the *"and nothing else"* in published
    prose, which an added edge would make false; and the edge would carry **no invalidation the
    operand edges do not already carry**, because `reuse_decision` is transitive by its own
    statement and every `ill:<grid>:<group>:<state>` names `smx:<weather>` directly. A rebuilt sky
    matrix already rebuilds the blend. What was missing was a reader that walks the graph the
    writer actually builds, which is what this is.

    **Breadth-first, over a `seen` set, and every reachable `smx:` edge is collected rather than
    the first one taken.** Two properties this buys that a short-circuit would not: a cycle in a
    hand-edited manifest terminates (R50 -- registered is not currently valid), and a graph that
    reaches **two different** sky matrices is reported as such rather than resolved to whichever
    edge happened to come first. The second is the substituted-default rule applied to a walk: an
    arbitrary pick would put one site's weather under EN 17037's denominator with nothing saying
    another was equally reachable. A one-group run is unaffected -- `ill:`/`ill5:` still find their
    own edge at depth 0, in one iteration, exactly as before.

    **One walk, shared rather than duplicated -- Task 13's own reason for factoring this out of
    what used to be `_site_latitude`'s own body.** `_site_latitude` below and
    `_compliance_en17037_provision` both need the same weather input this run was built from, for
    two different fields of it (`location.latitude` vs. `records[*].diffuse_horizontal_lux`), and
    two walks that must independently agree is exactly the defect class this branch has already
    found twice (Task 12/13 reviews) -- one reads the wrong index, the other reads the right one,
    and nothing catches the disagreement until a caller notices the numbers differ. There is now
    exactly one place that decides what "the weather input this run used" means.

    **Every step returns `(None, reason)` rather than raising**, `reason` naming which stage
    failed: the artifact is not registered, it carries no `smx:` edge, that edge names nothing
    registered, the `smx:` artifact carries no weather edge, the weather input carries no EPW file,
    the EPW is outside every allowed root, it cannot be digested, its digest has drifted from what
    was registered, or it no longer parses. **A drifted digest is never read anyway** -- the value
    it would parse to belongs to a file that did not build the result being reported on, so using
    it would be exactly the silent swap R97 exists to catch. What a caller does with `(None,
    reason)` is its own decision: `_site_latitude` logs it and degrades to `None` (the sDA/ASE
    numbers it caveats are correct with or without the latitude note); `_compliance_en17037_
    provision` cannot degrade the same way -- EN 17037's whole denominator comes from this file --
    so it turns `reason` into a refusal instead.
    """
    _record, parsed, reason = _resolve_weather(state.loaded, state.artifact_id)
    return parsed, reason


def _resolve_weather(
    loaded: LoadedProject, artifact_id: str, *, sky_kind: str = _SMX_KIND
) -> tuple[InputRecord | None, EPWFile | None, str | None]:
    """`_resolve_epw`'s walk from `artifact_id`, returning the weather `InputRecord` it reached as
    well: `(record, parsed, None)` on success, `(record, None, reason)` when the record was found
    and its EPW then failed a check, and `(None, None, reason)` when no single record was reached.
    `sky_kind` is the sky-matrix prefix the first hop looks for (`_SMXSUN_KIND` from
    `illdir:<grid>`); every reason names the kind it walked.
    """
    try:
        loaded.manifest.artifact(artifact_id)
    except KeyError:
        return None, None, f"{artifact_id!r} is not registered"

    smx_edges = _reachable_smx_edges(loaded, artifact_id, kind=sky_kind)
    if not smx_edges:
        return None, None, (
            f"{artifact_id!r} carries no {sky_kind}: edge, and neither does anything it was built "
            "from"
        )
    if len(smx_edges) > 1:
        # **Reported, never resolved by picking one.** Two sky matrices reachable from one result
        # means two weather inputs could have built it, and EN 17037's denominator and LM-83's
        # latitude caveat would then be computed against whichever edge the traversal saw first --
        # a substituted default published as a measured value, which is the one thing CONTEXT.md
        # forbids outright. Unreachable from any plan `chain.py` builds (one run resolves one
        # weather input), and reachable from a hand-edited manifest, which R50 says is exactly the
        # class this module re-checks everything for.
        return None, None, (
            f"{artifact_id!r} reaches {len(smx_edges)} different sky matrices "
            f"({', '.join(smx_edges)}), so which weather input built it cannot be decided from "
            "the manifest"
        )
    smx_edge = smx_edges[0]

    try:
        smx_artifact = loaded.manifest.artifact(smx_edge)
    except KeyError:
        return None, None, f"edge {smx_edge!r} names no registered artifact"

    weather_ids = {record.id for record in loaded.manifest.inputs if record.kind == "weather"}
    weather_edge = next((edge for edge in smx_artifact.built_from if edge in weather_ids), None)
    if weather_edge is None:
        return None, None, f"{smx_edge!r} carries no weather edge"

    record = loaded.manifest.input(weather_edge)
    # **By suffix, not by position** -- `weather_tool._files_of`'s own reason: `InputRecord.files`
    # is a list nothing in `rad_core.manifest` orders, so `files[0]` as "the EPW" is an assumption
    # about `weather_tool.load`'s own write order that a hand-edited or re-registered manifest can
    # break silently. `_files_of` is the one place that decision already lives; imported rather
    # than re-derived here so the two tools cannot answer "which file is the EPW" differently.
    epw_path, _wea_path = _files_of(record)
    if epw_path is None:
        return record, None, f"weather input {weather_edge!r} carries no EPW file"

    file_ref = next((f for f in record.files if Path(f.path) == epw_path), None)
    if file_ref is None:
        # Unreachable through any path `_files_of` can return an EPW from -- it only ever returns a
        # path drawn from `record.files` itself -- but kept rather than assumed, the same posture
        # `_grid_positions` takes for its own defence-in-depth checks.
        return record, None, (
            f"the EPW of weather input {weather_edge!r} is not one of that input's own registered "
            "files"
        )

    try:
        contained_epw = contain(epw_path, current().roots)
    except (PathOutsideRoot, RootsNotConfigured) as exc:
        return record, None, f"weather file for {weather_edge!r} is outside every allowed root ({exc})"

    # **One read, hashed and parsed -- never `file_digest(path)` followed by `read_epw(path)`.**
    # Those are two opens, and the bytes that satisfied the drift check would not be the bytes
    # parsed: a local writer inside an allowed root who swaps the file between them passes the gate
    # and feeds another site's weather into EN 17037's denominator, which is exactly the silent
    # swap R97's check exists to catch. `read_epw_with_digest` returns the digest of the buffer it
    # parsed, in `file_digest`'s own format, so the comparison below is against the content this
    # function actually goes on to use (whole-branch review, security lens). The bound
    # (`MAX_EPW_BYTES`, measured on the descriptor) and the `open_regular_file` guard are
    # unchanged -- this is the same read `read_epw` always did, with its bytes kept one moment
    # longer.
    try:
        current_digest, parsed = read_epw_with_digest(contained_epw)
    except (WeatherFileUnreadableError, EPWError) as exc:
        return record, None, f"weather file for {weather_edge!r} is unreadable ({exc})"

    if current_digest != file_ref.digest:
        # **R97: a file this server does not own, re-opened long after the run.** The bytes on
        # disk no longer match what was digested when this weather input was registered -- the EPW
        # may have been swapped for a different site's since the `.ill` this call is answering
        # about was built. Using it anyway would be exactly the silent swap R97 exists to catch, so
        # this is reported and `parsed` is discarded unread: it exists here only because the digest
        # and the parse now come from one read, and returning `None` is what keeps a drifted file's
        # *content* from ever reaching a caller.
        return record, None, (
            f"weather file for {weather_edge!r} has drifted from its registered digest "
            f"(expected {file_ref.digest}, found {current_digest})"
        )

    return record, parsed, None


#: `LM83_CLOCK_ZONE_NOT_NAMED`'s message (R860). True on a site with no daylight saving as well as
#: on one with it: it says where the window was placed and what LM-83 asks, never that the site
#: observes daylight saving, because the server cannot tell which it has.
_LM83_CLOCK_ZONE_NOT_NAMED_MESSAGE: Final[str] = (
    "LM-83's 08:00–18:00 window was placed in the weather file's local standard time, because no "
    "clock zone is recorded for it. LM-83 asks for local clock time with daylight saving where "
    "the site observes it; name the site's zone with weather(action=\"load\", clock_zone=...) to "
    "apply it."
)

def _lm83_dirt_depreciation_message(edition: Lm83Edition) -> str:
    """`LM83_DIRT_DEPRECIATION_NOT_APPLIED`'s message (R862) under `edition`, published by
    `_compliance_lm83` and by the `metrics` `da` block -- the two places `LM83_EDITION` rides.

    LM-83-12 section 2.2.9 asks for the factor "when manufacturer's data are used". LM-83-22
    section 4.5 asks for it on all glazing (section 1.2: "Dirt depreciation factors are now required
    on all glazing surfaces") at the same three minima, with the slope band written "between 20 and
    85 degrees from horizontal" -- cited from ANSI/IES LM-83-22 (R864).
    The clause comes from `section`; the factor itself is Still open item 51.
    """
    clause = section("dirt", edition)
    if edition == LM83_22:
        rule = (
            f"{clause} requires window and skylight glazing to be modeled with its actual visible "
            "light transmittance less a dirt depreciation fraction, at a minimum 5 % for vertical "
            "or exterior downward-facing windows, 10 % for glazing sloped between 20 and 85 "
            "degrees from horizontal, and 15 % for skylights at less than a 20 degree slope (read "
            "from ANSI/IES LM-83-22)."
        )
    else:
        rule = (
            f"{clause} requires window and skylight glazing to be modeled with its actual visible "
            "light transmittance less a dirt depreciation factor; when manufacturer's data are "
            "used, at a minimum 5 % for vertical windows, 10 % for sloped glazing between 85 and "
            "20 degrees of vertical, and 15 % for skylights at less than a 20 degree slope."
        )
    return (
        f"{rule} This server applies none: the transmittance of the glazing materials and BSDFs "
        f"you supplied is used as given, so include the factor in them if the judgement must meet "
        f"{clause}."
    )


@dataclass(frozen=True)
class _Lm83Window:
    """LM-83's 08:00-18:00 analysis window for one read, and where it was placed (R860)."""

    columns: frozenset[int]
    clock_zone: str | None
    """The zone the window was placed in, or `None` when it is in the EPW's standard time."""
    reference_year: int | None
    """`rad_engine.clock.REFERENCE_YEAR` when a zone was applied, else `None`."""
    advisory: Advisory | None
    """`None` when the zone was applied; `LM83_CLOCK_ZONE_NOT_NAMED` (info) when no zone is
    recorded, `LM83_CLOCK_ZONE_UNRESOLVED` (warn, naming the reason) when one is and was not."""
    epw: EPWFile | None
    """The EPW `_resolve_weather` parsed on the way, or `None` with `epw_reason`. Handed to
    `_site_latitude` by the reads that walk the same `smx:` edge, so one read parses the file
    once."""
    epw_reason: str | None

    @property
    def period(self) -> str:
        """`analysis.period` for this window: `_lm83_period(self.clock_zone)`."""
        return _lm83_period(self.clock_zone)


def _zones_reached(
    loaded: LoadedProject, artifact_id: str, *, sky_kind: str
) -> list[tuple[str, str]]:
    """`(input id, clock_zone)` for every zoned weather input any reachable sky matrix names.

    Only consulted when `_resolve_weather` reached no single record -- the one such case that can
    still name a zone is a hand-edited graph reaching two sky matrices -- so that a recorded zone
    is never reported as "not recorded"."""
    inputs = {record.id: record for record in loaded.manifest.inputs if record.kind == "weather"}
    found: list[tuple[str, str]] = []
    for edge in _reachable_smx_edges(loaded, artifact_id, kind=sky_kind):
        try:
            sky = loaded.manifest.artifact(edge)
        except KeyError:
            continue
        for input_id in sky.built_from:
            record = inputs.get(input_id)
            if record is not None and record.clock_zone is not None:
                if (input_id, record.clock_zone) not in found:
                    found.append((input_id, record.clock_zone))
    return found


def _lm83_window_for(
    loaded: LoadedProject, artifact_id: str, n_hours: int, *, sky_kind: str = _SMX_KIND
) -> _Lm83Window:
    """The LM-83 window for a matrix of `n_hours` columns read from `artifact_id`, on the clock of
    the zone its weather input records (R860), applied when results are read so naming or
    changing a zone makes nothing stale.

    **The zone is applied only when every condition holds**: the matrix is a whole year
    (`is_whole_year`), the walk (`_resolve_weather`, from `artifact_id` through `sky_kind`) reaches
    one weather record, that record names a zone, its EPW passes the walk's checks (contained,
    readable, digest intact), the EPW has exactly `n_hours` records -- its own `(month, day,
    hour)` rows date the columns, in `REFERENCE_YEAR` -- and `clock_shifts` answers. Otherwise the
    window is today's standard-time one, and the advisory says which: **no zone known**
    (no record reached, or the record names none -- a record saved before the field existed reads
    as `None`) is `LM83_CLOCK_ZONE_NOT_NAMED` at info; **a zone recorded and not applied** is
    `LM83_CLOCK_ZONE_UNRESOLVED` at warn, carrying the failed condition. Never a refusal: the
    standard-time numbers are what every read published before R860.

    Built after each caller's own `is_whole_year`/empty-matrix refusals where it has them, so the
    EPW is not read for a call that refuses anyway; `_metrics` reads sub-annual matrices, which is
    why the whole-year condition is checked here too.
    """
    record, parsed, reason = _resolve_weather(loaded, artifact_id, sky_kind=sky_kind)
    epw_reason = reason
    if record is not None:
        zoned = [(record.id, record.clock_zone)] if record.clock_zone is not None else []
    else:
        zoned = _zones_reached(loaded, artifact_id, sky_kind=sky_kind)
    standard = lm83_window(n_hours)
    if not zoned:
        return _Lm83Window(
            columns=standard, clock_zone=None, reference_year=None,
            advisory=Advisory(
                code="LM83_CLOCK_ZONE_NOT_NAMED", severity="info",
                message=_LM83_CLOCK_ZONE_NOT_NAMED_MESSAGE,
            ),
            epw=parsed, epw_reason=reason,
        )

    shifts: tuple[float, ...] | None = None
    if not is_whole_year(n_hours):
        reason = (
            f"the matrix has {n_hours} hour columns, not a whole year (8 760, or 8 784 in a leap "
            "year)"
        )
    elif parsed is not None and len(parsed.records) != n_hours:
        reason = (
            f"the weather file has {len(parsed.records)} hourly records and the matrix has "
            f"{n_hours} hour columns, so its rows cannot date the matrix's columns"
        )
    elif parsed is not None:
        # **Re-checked here, not trusted from the load** (R50): `clock_zone` is outside the
        # record's content digest, so a hand edit can name any zone, and `clock_shifts` would
        # apply one whose standard offset is not the EPW's without a word (Asia/Tokyo on a UTC+2
        # file moves every column by 7 h). `ValueError` covers `ClockZoneError` and the plain
        # `ValueError` `datetime` raises on a row whose date does not exist (`parse_epw` does not
        # range-check month and day); either leaves the window in standard time, said.
        try:
            check_clock_zone(zoned[0][1], parsed.location.time_zone_hours)
            shifts = clock_shifts(
                [(row.month, row.day, row.hour) for row in parsed.records],
                zone=zoned[0][1], time_zone_hours=parsed.location.time_zone_hours,
            )
        except ValueError as exc:
            reason = (
                str(exc) if isinstance(exc, ClockZoneError)
                else f"a row of the weather file has no date in {REFERENCE_YEAR} ({exc})"
            )
    if shifts is not None:
        return _Lm83Window(
            columns=lm83_window(n_hours, clock_shift_h=shifts), clock_zone=zoned[0][1],
            reference_year=REFERENCE_YEAR, advisory=None, epw=parsed, epw_reason=None,
        )

    recorded = ", ".join(f"{zone!r} on weather input {input_id!r}" for input_id, zone in zoned)
    logger.info("results: LM-83 clock zone not applied -- %s", reason)
    return _Lm83Window(
        columns=standard, clock_zone=None, reference_year=None, epw=parsed,
        epw_reason=epw_reason,
        advisory=Advisory(
            code="LM83_CLOCK_ZONE_UNRESOLVED", severity="warn",
            message=(
                "LM-83's 08:00–18:00 window was placed in the weather file's local standard time, "
                f"not in the clock zone recorded ({recorded}), because {reason}. LM-83 asks for "
                "local clock time with daylight saving where the site observes it, so on any day "
                "the site's clock differs from the weather file's standard time, the hours "
                "counted here are not the clock hours LM-83 names: they sit off them by that "
                "difference."
            ),
        ),
    )


def _site_latitude(
    state: _Resolved, *, walked: tuple[EPWFile | None, str | None] | None = None
) -> float | None:
    """The site latitude `LM83_LATITUDE_OUTSIDE_CALIBRATION` needs, or `None` if it cannot be
    found. Thin over `_resolve_epw` -- see that function's own docstring for the walk and every
    failure it can return; this is the one consumer that logs a failure and degrades rather than
    refusing, because the sDA/ASE numbers this caveat attaches to are correct with or without it.

    `walked` is that walk's `(parsed, reason)` when the caller already made it from the same
    artifact over the same `smx:` kind (`_Lm83Window.epw`/`epw_reason`), so the EPW is read once.
    """
    parsed, reason = walked if walked is not None else _resolve_epw(state)
    if parsed is None:
        logger.info("results: site latitude unavailable -- %s", reason)
        return None
    return parsed.location.latitude


# --- the streaming aggregates --------------------------------------------------------------------


def _overflowed_total(artifact_id: str, total: float) -> _UnreadableAnnualMatrix:
    """The refusal for a sum of finite cells that a double cannot hold (R830, register N-30),
    worded as `_lux_rows` words a non-finite cell: the same code and the same repair."""
    return _UnreadableAnnualMatrix(
        f"the annual matrix registered as {artifact_id!r} could not be read: each of its cells is "
        f"finite, but their sum is {total!r}, not representable as a double, so no mean can be "
        "published from it. These are not illuminances a chain produces. Re-run the chain that "
        "produced it."
    )


class _Accumulator:
    """Whole-matrix min/max/mean in lux, accumulated one row at a time.

    **This is what makes "statistics over the full set" structural rather than careful.** Nothing
    here holds a row after it has been folded in, so the aggregate is over every point and every hour
    by construction -- there is no list for a truncation to be applied to by mistake.
    """

    def __init__(self, artifact_id: str) -> None:
        # The id the overflow refusal in `view` names, as `_lux_rows` names it (R830).
        self.artifact_id = artifact_id
        self.count = 0
        self.total = 0.0
        self.minimum = float("inf")
        self.maximum = float("-inf")

    def add(self, lux: float) -> None:
        # **Refuses, matching `rad_core.metrics.PointTally.observe` -- fixed at the whole-branch
        # review, F5.** `min(inf, nan)` returns `inf` and `max(-inf, nan)` returns `-inf`, so a NaN
        # that was merely SKIPPED neither displaces a sentinel nor changes one, while `count` rises
        # and `total` becomes `nan` -- which is how a tally of only NaN published
        # `min=inf, max=-inf, mean=nan` with `count == 5` and the `count == 0` guard never firing.
        # The branch first closed that by skipping rather than raising, which left this class and
        # `PointTally.observe` -- both new on this branch, both self-described as "a second guard"
        # behind the same upstream refusal -- disagreeing: the same non-finite cell would refuse
        # `results(action="points")` (through `PointTally.observe`) and succeed under
        # `results(action="summary")` with a quietly reduced denominator (through this method's
        # skip). One matrix, two verdicts, is exactly what CONTEXT.md's "one predicate is one
        # arithmetic path" (R30) forbids, so this now raises rather than skips.
        #
        # **A second guard, not the first, and deliberately so.** `rad_engine.results` already
        # refuses a non-finite cell before any row is yielded, so on today's `results` path this
        # branch is unreachable; the point is to settle the question at the source and stop
        # depending on that reader's guard for reachability, and to answer the same way `observe`
        # does if that upstream guard is ever narrowed or bypassed.
        #
        # **That sentence took TWO guards upstream to be true, and it was false until fix round 2**
        # (N1). `_parse_row`/`_parse_lux_row` check the parsed *tokens*, which is a fact about the
        # file; `to_lux` then multiplies by 179 lm/W, so three finite components convert to an
        # infinite lux the token check cannot see. Measured before the fix: a `NCOMP=3` matrix of
        # `1e308` tokens reached this method as `inf`, and the bare `ValueError` below fell to
        # `results`' broad `except ValueError` and produced a **code-less** refusal on
        # `action="summary"` and `action="points"` alike. `_stream_lux_from` now carries the
        # converted-value check as well, so the branch is unreachable **through the reader** and
        # a matrix like that is refused with `ANNUAL_MATRIX_UNREADABLE` instead.
        if not math.isfinite(lux):
            raise ValueError(
                f"a non-finite lux value ({lux!r}) cannot be folded into a whole-grid statistic -- "
                "the matrix carries a cell rad_engine.results should already have refused before "
                "yielding it."
            )
        self.count += 1
        self.total += lux
        self.minimum = min(self.minimum, lux)
        self.maximum = max(self.maximum, lux)

    def view(self) -> StatisticsView:
        if self.count == 0:
            # **One way in, and it is reachable through the tool: a matrix with no rows, so the
            # row loop never runs.** Neither `_summary` nor `_points` guards
            # `header.points == 0`, and `_stream_lux_from`'s truncation guard cannot fire when
            # `seen == header.points == 0`.
            #
            # **Zero *hours* is not a second way in** (fix round 3; the previous round wrote "(or
            # no hours)" here and it is false). Measured on both actions over `mcp.call_tool`:
            # `NROWS=2 NCOLS=0` is REFUSED by that same truncation guard
            # (`rad_engine.results._stream_lux_from`'s `if seen != header.points:`) -- `refused=True`,
            # `statistics=None` -- and `NROWS=0 NCOLS=0` publishes three `None`s only because it
            # is the zero-row case above. The two parametrised
            # `test_a_zero_row_matrix_reaches_the_client_with_three_nulls` rows drive `summary` and
            # `points` over `mcp.call_tool` and are that measurement.
            #
            # **There is no second way any more** (fix round 2, N2). This comment used to name one
            # -- a tally of only non-finite cells, which `add` "declines to fold in" -- and cite
            # the direct `_Accumulator` unit rows that covered it. `add` no longer declines
            # anything: it RAISES (whole-branch review, F5), and the same commit deleted those
            # unit rows. A tally of only non-finite cells cannot reach `view()` at all now, because
            # the first such cell refuses the call; upstream of that, `rad_engine.results` refuses
            # the matrix outright (`_parse_row`/`_parse_lux_row` on the tokens, `_stream_lux_from`
            # on the converted lux) and the caller gets `ANNUAL_MATRIX_UNREADABLE`.
            #
            # `None` rather than zeros or sentinels: nothing was measured, so no number is
            # published.
            return StatisticsView(min=None, max=None, mean=None)
        if not math.isfinite(self.total):
            # R830 (register N-30): `add` checks each cell, not their sum, and finite cells can sum
            # past a double -- two `1e308` lux cells give `inf`. Measured before this guard:
            # `summary` refused code-less (the JSON writer rejected `mean: inf`) and `points`
            # published `statistics.mean: inf` and a point `mean: null` with no refusal at all.
            raise _overflowed_total(self.artifact_id, self.total)
        return StatisticsView(
            min=self.minimum, max=self.maximum, mean=self.total / self.count
        )


# --- the actions ---------------------------------------------------------------------------------


#: `action="summary"` carries no `metrics` block and never will in this shape: `ResultsSummary`
#: (above) declares no field named `metrics`, `sda_300_50` or `ase_1000_250` at all, so this
#: advisory is not caveating a value that is present elsewhere on the payload -- there is no such
#: value anywhere on `summary`'s wire. Attached to **every** `summary` call, unconditionally, for
#: three reasons that must all keep holding (module docstring, and design doc "Two published
#: fields"):
#:
#: 1. ASE needs the pre-blind zero-bounce run Phase 7c builds -- `summary` cannot compute it any
#:    more than `compliance` can before that lands.
#: 2. `summary` takes no arguments (`results(action="summary", project=..., grid=...)` is the whole
#:    signature) -- it cannot select a period or a threshold the way `compliance` does, so even sDA
#:    alone has no denominator this action could name.
#: 3. **A `summary` that reported sDA beside an absent ASE would be sDA reported alone** -- exactly
#:    the condition `SDA_REPORTED_ALONE` (LM-83 ch04 section 2.4) forbids on the `compliance` path,
#:    reused here for the identical rule rather than a second code meaning the same thing (the
#:    register was searched; `SDA_REPORTED_ALONE` is the one it allocates for "sDA without ASE").
#:
#: `severity="error"` is the register's own allocation for this code (docs/tool-surface.md's
#: register lists only `error` for `SDA_REPORTED_ALONE`) -- it describes the **value** ("the pair is
#: not here"), not the **call**: `summary` still succeeds (`is_error=False`), the same split
#: `envelope.py`'s own docstring states for `radiance_status`.
_SUMMARY_SDA_REPORTED_ALONE_ADVISORY: Final = Advisory(
    code="SDA_REPORTED_ALONE",
    severity="error",
    message=(
        "results(action='summary') never reports sDA or ASE. ASE needs the pre-blind, zero-bounce "
        "run that run_annual makes (when metrics includes 'ase' or blind_control='lm83'); "
        "summary takes no period or threshold argument to compute either "
        "against; and reporting sDA here beside an absent ASE would itself be sDA reported alone, "
        "which LM-83 ch04 section 2.4 forbids. "
        "results(action='compliance', standard='lm83') is where the pair is reported, together."
    ),
)


def _summary(state: _Resolved, *, project: str, grid: str) -> CallToolResult:
    """Whole-grid statistics across every point and every hour, holding one row at a time."""
    stats = _Accumulator(state.artifact_id)
    negative = _NegativeCount()
    for lux in _lux_rows(
        state.path, artifact_id=state.artifact_id, expected_points=state.expected_points,
        content_digest=state.content_digest,
    ):
        for value in lux:
            stats.add(value)
        negative.observe_row(lux)
    advisories = [_SUMMARY_SDA_REPORTED_ALONE_ADVISORY, *state.blend_advisories]
    if (warning := _negative_count_advisory(
        state, negative, tail="statistics.min and statistics.mean include them as published.",
    )) is not None:
        advisories.append(warning)
    return emit(ResultsSummary(
        project=project, grid=grid, artifact=state.artifact_id, path=str(state.path),
        method_used=state.method_used, shape=_shape_view(state.header), statistics=stats.view(),
        advisories=advisories,
    ))


# --------------------------------------------------------------------------------------------
# The analysis area LM-83 section 2.2.6's blind bands are keyed on
# --------------------------------------------------------------------------------------------


class _AnalysisAreaNotDerivable(ProjectError):
    """No analysis area could be derived from the grid's recorded polygon.

    Two conditions, one code, and the message says which: the registration recorded **no** polygon
    at all (a `.pts` that reached the project by some route other than `sensor_grid`, or a manifest
    edited by hand), or what it recorded **encloses no area** (collinear or coincident vertices --
    `sensor_grid(action="create")` refuses those at write time, so this too is only reachable off a
    hand-edited manifest). Both are the same fact for a caller: the server does not know how big
    the space is.

    **A refusal rather than a guess, because LM-83's bands span an order of magnitude.** Section
    2.2.6's table runs from `< 200 sf` (blinds close on > 1 point in sun) to `>= 1 000 sf` (> 2 % of
    points), so picking the wrong row moves the trigger by a factor of ten and every hour of the
    resulting schedule is decided by it. A refusal naming the repair beats a plausible schedule.

    Deliberately **not** the sensor cloud's own extent as a fallback: LM-83 requires the grid to
    span the whole space and insets it 12-24 in from every wall, so the cloud is always smaller than
    the space by an unknown margin -- and substituting it would be reporting a substituted default
    as a measured value, which CONTEXT.md's reporting rules forbid outright.
    """

    code = "ANALYSIS_AREA_NOT_DERIVABLE"
    field = "grid"


@dataclass(frozen=True)
class _AnalysisArea:
    """The analysis area a blind trigger is keyed on, and where it came from.

    `area_source` is published beside the number because the area is **derived**, not measured or
    configured: it is the shoelace area of the polygon `sensor_grid(action="create")` recorded, and
    a caller has to be able to tell that from a default someone typed.

    `coverage` is `None` when the grid's own `.pts` could not be read, when its points occupy a
    single position and support no cell size, when the polygon's area is not a finite number, or
    when the grid is a zoned EN 12464 grid (Phase 11b R610: its zones are separate lattices and
    reading only the first `.pts` would silently score one task area against the whole polygon) --
    unknown, never zero, because zero would read as "the grid covers none of the room". (Before
    Phase 7d-1 Task 17 a single-column grid *did* read exactly 0.0, which is the false positive
    ruling R160 replaced the measure to remove.)

    **A `None` coverage silences `ANALYSIS_AREA_UNDER_COVERED` as well**, and that half is a
    wire-visible change of its own: `_analysis_area` gates the advisory on `coverage is not None`,
    so a grid whose points occupy a single position -- one LM-83 sensor, which is every room under
    ~1.21 m on **both** axes -- used to publish `coverage: 0.0` **and** the advisory, and now
    publishes `null` and no advisory at all. An unknown fraction is not an under-coverage, so this
    is the intended reading; it is stated here because a caller diffing payloads across Task 17
    sees two fields move, not one. Both halves are asserted together in
    `tests/test_results_tool.py::TestTheAnalysisAreaComesFromTheRecordedPolygon`.

    `points_expected` is **the same polygon's** other consequence: how many sensors LM-83's own
    placement rule would put inside it (`rad_core.grids.lm83_grid_point_count`). It is here, beside
    `area_sf`, because Phase 7d-1 Task 16's whole subject is that the two must come from **one
    source**: §2.2.6 selects a row off the area and then applies that row's absolute point count, so
    an area from the polygon and a point count from somewhere else is two bases for one rule. `None`
    when the polygon cannot be gridded (never `0`: `place_sensor_grid` refuses an empty grid rather
    than returning one, so zero would be a value this field can never honestly hold).
    """

    area_m2: float
    area_sf: float
    area_source: str
    coverage: float | None
    points_expected: int | None
    advisories: tuple[Advisory, ...]


#: What `_AnalysisArea.area_source` says. One string, so the published provenance label and the
#: thing it names cannot drift apart.
#: LM-83's own analysis window, as `ComplianceAnalysisView.period` and `MetricsAnalysisView.period`
#: both publish it, through `_Lm83Window.period`.
#:
#: **One function, read twice.** The text was once written out at two sites ~700 lines apart under
#: a comment claiming they were byte-for-byte identical, and nothing enforced it; it was hoisted
#: into one constant so `_compliance_lm83` and `_metrics` publish the same string by construction.
#: **R860 final review (Important 1) made it a function of where the window was placed**: the
#: constant read "08:00-18:00 local clock, 10 h/day" on every read, so a read whose window stayed in
#: the EPW's standard time -- no zone recorded, or one recorded and not applied -- published "local
#: clock" beside `LM83_CLOCK_ZONE_NOT_NAMED`/`_UNRESOLVED` saying standard time. A zone applied
#: names local clock time, the zone and daylight saving; otherwise local standard time with none.
#: "as the zone observes it" keeps the first true on a zone with no daylight saving
#: (Africa/Johannesburg, applied with a zero shift) and on one whose clock goes back.
_LM83_PERIOD_STANDARD: Final[str] = (
    "08:00-18:00 local standard time (no daylight saving applied), 10 h/day"
)


def _lm83_period(clock_zone: str | None) -> str:
    """The `period` text for LM-83's window placed on `clock_zone`'s clock, or in standard time."""
    if clock_zone is None:
        return _LM83_PERIOD_STANDARD
    return (
        f"08:00-18:00 local clock time ({clock_zone}, daylight saving applied as the zone observes "
        "it), 10 h/day"
    )


_AREA_SOURCE_POLYGON: Final[str] = "sensor_grid polygon"
#: The LM-83-22 glossary's analysis area, "the number of analysis points times the spacing
#: distances for the rows and columns" (R864): the area `_analysis_area` returns under LM-83-22.
_AREA_SOURCE_POINTS: Final[str] = "LM-83-22 glossary: points x spacing"


class _ResultStale(ProjectError):
    """A stored result the manifest reports invalid (ruling R785, owner decision F4 of 2026-10-04).

    `rad_core.manifest.evaluate` marks `ill:<grid>` invalid when anything it was built from moved
    since it was sealed -- the grid re-created, a scene or material edited, the weather replaced --
    and `results` read it anyway until R785 (Still open item 30, measured: a re-created grid's old
    `.ill` answered `summary` with no refusal). Refused as `export` refuses a stale `artifact_id`
    (`export_tool._artifact_target`): `ARGUMENT_OUT_OF_RANGE`, naming what changed through the same
    `stale_detail`, with `field="grid"`, the argument that named the result."""

    code = "ARGUMENT_OUT_OF_RANGE"
    field = "grid"


def _readable_ids(
    registered: dict[str, Any], *, grid: str, chain: str | None, blind_control: str | None
) -> list[str]:
    """The registered result ids this call's selectors can still read, narrowed without refusing.

    `_resolve` refuses an unsatisfiable selector further down; this keeps the stale check (R785)
    from refusing a call over a result its selectors exclude -- `chain="3phase"` beside a stale
    `ill5:<grid>`, say. It does not predict `_resolve`'s later refusals: a selector `_resolve`
    will refuse (`chain="2phase"` with `blind_control="lm83"`, or over an `ill:` recording
    "3phase") can be refused as stale first when the one result it names is stale; both are refused
    either way, only the message differs. With no selector, every registered result is checked: such
    a call either reads them all (`compliance`) or is refused as ambiguous."""
    ids = list(registered)
    if blind_control is not None:
        want_blended = blind_control == _LM83_BLIND_CONTROL_READ
        ids = [other for other in ids if other.startswith(_BLENDED_PREFIX) is want_blended]
    if chain is not None and chain in _ARTIFACT_PREFIX_FOR_CHAIN:
        selected = _selected_id(grid, chain=chain, blind_control=blind_control)
        ids = [other for other in ids if other == selected]
    return ids


def _refuse_stale_results(
    loaded: LoadedProject, artifact_ids: list[str], *, name_selectors: bool = False
) -> None:
    """Raise `_ResultStale` for the first of `artifact_ids` the manifest reports invalid (R785).

    `rad_engine.evaluate_loaded`, as `export` evaluates, so a dangling edge anywhere in the manifest
    is refused `ARTIFACT_EDGES_INCOMPLETE` here too, before any value is read. A `built_from` cycle
    or a repeated artifact id, which `evaluate_loaded` leaves as a bare `ManifestError`, is refused
    `MANIFEST_UNREADABLE` (R790): `results` used to answer such a manifest, and refused it with no
    code once R785 evaluated it.

    `name_selectors` is `_resolve`'s call with no `chain`: when more than one result is checked, the
    refusal also names the selector (`_selector_for`) that reads each one still current (R790), so
    the caller is not told to re-run when passing `chain=` reads a current result now."""
    if not artifact_ids:
        return
    try:
        statuses = evaluate_loaded(loaded.manifest)
    except ManifestError as exc:
        raise ManifestUnreadableError(
            f"manifest.json could not be evaluated: {exc}. No run of this server writes such a "
            "manifest; repair or restore manifest.json. No stored value was read."
        ) from exc
    for artifact_id in artifact_ids:
        status = statuses[artifact_id]
        if not status.valid:
            current = [
                _selector_for(other, loaded.manifest.artifact(other)) + ", which is current"
                for other in artifact_ids
                if name_selectors and len(artifact_ids) > 1 and statuses[other].valid
            ]
            raise _ResultStale(
                f"the stored result {artifact_id!r} is stale ({stale_detail(status)}): something "
                "it was built from changed after it was sealed, so its values no longer describe "
                "this project. Re-run run_annual to seal a fresh one"
                + (f", or read a current result instead: {'; '.join(current)}" if current else "")
                + ". No stored value was read."
            )

class _SceneIncludeChanged(ProjectError):
    """`SCENE_INCLUDE_CHANGED` (error, `field="grid"`): a file the registered scene includes with
    `!xform` holds other bytes than the run that sealed the stored result compiled (final fix E,
    fix round 4, X1).

    An include is read in place and is no input of the project, so no digest moved when it was
    edited: `evaluate_loaded` kept the result valid and every read published values traced over a
    scene the project no longer holds. Not `ARGUMENT_OUT_OF_RANGE` as `_ResultStale` is: the
    manifest still calls the result current, and the remedy is a re-run that seals the files as they
    are now (`annual_tool` records them, digest-included, on every octree it registers)."""

    code = "SCENE_INCLUDE_CHANGED"
    field = "grid"


@dataclass(frozen=True)
class _SealedIncludes:
    """What the octrees a result reaches recorded of the files their scene included (final fix E,
    fix rounds 4 and 5): `digests`, `{path: {digest, ...}}`; `links`, `{name: {resolved, ...}}`
    for each include reached by a name that resolved elsewhere (N2); and `included_by`, `{path:
    the file whose !xform line names it}` where the record says (N3); and `dropped`, the names of
    the includes the run's oconv dropped (`SCENE_INCLUDES_DROPPED_PARAMETER`, final fix F, fix
    round 2, G1); and `failed`, each inline command whose output oconv did not receive
    (`SCENE_INCLUDE_FAILED_PARAMETER`, fix round 4, I1), once each."""

    digests: dict[str, set[str | None]]
    links: dict[str, set[str]]
    included_by: dict[str, str]
    dropped: set[str]
    failed: list[dict[str, Any]] = _dataclass_field(default_factory=list)


def _sealed_scene_includes(loaded: LoadedProject, artifact_id: str) -> _SealedIncludes:
    """`_SealedIncludes` of every octree `artifact_id` reaches through `built_from` (final fix E,
    fix round 4, X1): its `SCENE_INCLUDES_PARAMETER` and `SCENE_INCLUDE_LINKS_PARAMETER`, or, for
    an octree sealed before round 4 recorded them, the `includes` of its `COMPILED_PARAMETER`
    (R849), whose entries also give `named` and `included_by` from round 5 on. Edges naming
    nothing registered are skipped, and the walk keeps a `seen` set for `_reachable_smx_edges`'
    reason (a hand-edited cycle)."""
    artifacts = {artifact.id: artifact for artifact in loaded.manifest.artifacts}
    sealed = _SealedIncludes(digests={}, links={}, included_by={}, dropped=set())
    seen: set[str] = {artifact_id}
    frontier = [artifact_id]
    while frontier:
        next_frontier: list[str] = []
        for current_id in frontier:
            artifact = artifacts.get(current_id)
            if artifact is None:
                continue
            if artifact.kind == "octree":
                compiled = artifact.parameters.get(COMPILED_PARAMETER)
                includes = compiled.get("includes") if isinstance(compiled, dict) else None
                compiled_entries = [
                    entry for entry in (includes if isinstance(includes, list) else ())
                    if isinstance(entry, dict) and isinstance(entry.get("path"), str)
                ]
                recorded = artifact.parameters.get(SCENE_INCLUDES_PARAMETER)
                if isinstance(recorded, dict):
                    entries = list(recorded.items())
                    recorded_links = artifact.parameters.get(SCENE_INCLUDE_LINKS_PARAMETER)
                    links = (list(recorded_links.items())
                             if isinstance(recorded_links, dict) else [])
                else:
                    entries = [(entry["path"], entry.get("digest")) for entry in compiled_entries]
                    links = [
                        (name, entry["path"]) for entry in compiled_entries
                        for name in (entry.get("named") if isinstance(entry.get("named"), list)
                                     else ())
                    ]
                for path, digest in entries:
                    if isinstance(path, str) and (digest is None or isinstance(digest, str)):
                        sealed.digests.setdefault(path, set()).add(digest)
                for name, resolved in links:
                    if isinstance(name, str) and isinstance(resolved, str):
                        sealed.links.setdefault(name, set()).add(resolved)
                recorded_dropped = artifact.parameters.get(SCENE_INCLUDES_DROPPED_PARAMETER)
                if isinstance(recorded_dropped, list):
                    sealed.dropped.update(
                        name for name in recorded_dropped if isinstance(name, str) and name)
                recorded_failed = artifact.parameters.get(SCENE_INCLUDE_FAILED_PARAMETER)
                for entry in (recorded_failed if isinstance(recorded_failed, list) else ()):
                    if not isinstance(entry, dict):
                        continue
                    names = entry.get("names")
                    kept = {
                        "command": entry.get("command") if isinstance(
                            entry.get("command"), str) else "",
                        "file": entry.get("file") if isinstance(entry.get("file"), str) else None,
                        "line": entry.get("line") if isinstance(entry.get("line"), int)
                        and not isinstance(entry.get("line"), bool) else None,
                        "names": [name for name in (names if isinstance(names, list) else ())
                                  if isinstance(name, str) and name],
                        # Fix round 5 (L1, P1): what the failure lost; a seal from round 4
                        # carries none of the three, and is worded for either failure.
                        "kind": entry.get("kind") if entry.get("kind") in (
                            "missing", "content", "line", "unlisted", "argfile", "argfile_empty",
                        ) else None,
                        "error_in": entry.get("error_in") if isinstance(
                            entry.get("error_in"), str) and entry.get("error_in") else None,
                        "after": [word for word in (
                            entry.get("after") if isinstance(entry.get("after"), list) else ())
                            if isinstance(word, str) and word],
                        # Final fix G (G-4): the files of an `-a` line before the one xform
                        # stopped in, which gave their first copy only; none from round 5.
                        "before": [word for word in (
                            entry.get("before") if isinstance(entry.get("before"), list) else ())
                            if isinstance(word, str) and word],
                    }
                    if kept not in sealed.failed:
                        sealed.failed.append(kept)
                for entry in compiled_entries:
                    includer = entry.get("included_by")
                    if isinstance(includer, str):
                        sealed.included_by.setdefault(entry["path"], includer)
            for edge in artifact.built_from:
                if edge not in seen:
                    seen.add(edge)
                    next_frontier.append(edge)
        frontier = next_frontier
    return sealed


@dataclass(frozen=True)
class _ChangedInclude:
    """One included file `_changed_scene_includes` refuses on: `described` is "<path> (<how>)",
    `missing` whether no readable file is at that name now (a re-run may still compile without it:
    final fix F, F2), `includer` the file whose `!xform` line names
    it where the record says (final fix E, fix rounds 4 and 5)."""

    described: str
    path: str
    missing: bool
    includer: str | None


def _changed_scene_includes(loaded: LoadedProject, artifact_id: str) -> list[_ChangedInclude]:
    """Each included file `_sealed_scene_includes` finds that does not hold the bytes recorded for
    it now, or (fix round 5, N2) each name that resolved to a recorded file and resolves to another
    now, sorted by path. A path is re-contained before it is opened (R50: the manifest is
    untrusted), and the way `run_annual`'s scan contains an include (fix round 5, N1):
    `rad_engine.octree.inside_allowed`, inside the configured roots or the Radiance library
    directory by resolved path or, on a case-insensitive filesystem, by the same directory under
    another letter case. One outside both is not opened and is reported so, since a re-run refuses
    it there too. The read is `compiled_scene_record`'s own (`current_file_digest`), so a file that
    is missing or not a regular file reads as `None`."""
    sealed = _sealed_scene_includes(loaded, artifact_id)
    if not sealed.digests and not sealed.dropped:
        return []
    allowed: list[Path] = []
    for root in current().roots:
        try:
            allowed.append(Path(root).resolve())
        except (OSError, RuntimeError):
            continue
    library_looked = False

    def inside(path: Path) -> bool:
        nonlocal library_looked
        if inside_allowed(path, allowed):
            return True
        if not library_looked:
            library_looked = True
            try:
                allowed.append(locate().lib_dir.resolve())
            except (RadianceNotFound, OSError, RuntimeError):
                return False
            return inside_allowed(path, allowed)
        return False

    changed: list[_ChangedInclude] = []
    for path in sorted(sealed.digests):
        named = Path(path)
        includer = sealed.included_by.get(path)
        if not inside(named):
            changed.append(_ChangedInclude(
                f"{path} (it lies outside the allowed directories now, so it was not read)",
                path, False, includer,
            ))
            continue
        now = current_file_digest(named)
        for digest in sealed.digests[path]:
            if now == digest:
                continue
            changed.append(_ChangedInclude(
                f"{path} ("
                + ("it is missing or not a readable regular file now" if now is None
                   else "it could not be read at the run and can be now" if digest is None
                   else "its bytes differ from those the run compiled")
                + ")",
                path, now is None, includer,
            ))
            break
    for name in sorted(sealed.links):
        try:
            resolved_now = str(Path(name).resolve())
            exists = Path(name).exists()
        except (OSError, RuntimeError):
            resolved_now, exists = None, False
        if resolved_now in sealed.links[name] and exists:
            continue
        recorded = _and_list(sorted(sealed.links[name]))
        # Final fix F (F1): a target outside the roots is never put in a message (R823, as the
        # scan names an outside include through the file and line that wrote it); the link's own
        # name, and what it resolved to at the run, were inside them when the run sealed them.
        now_is = (
            "resolves to no readable file now" if not exists
            else f"resolves to {resolved_now} now" if inside(Path(resolved_now))
            else "resolves outside the allowed directories now, so it was not read"
        )
        changed.append(_ChangedInclude(
            f"{name} (it resolved to {recorded} at the run, and {now_is})",
            name, not exists, sealed.included_by.get(next(iter(sorted(sealed.links[name])))),
        ))
    # Final fix F, fix round 2 (G1): an include the run's oconv dropped, back at its name now. The
    # stored values describe the scene without it, and the include search records only files that
    # exist, so nothing above sees it return. A name is read as xform read it, from oconv's
    # working directory (the project root), and is contained before it is opened; one outside
    # every root is not looked at, and `_dropped_includes_advisory` keeps saying what the run did.
    #
    # Final fix F, fix round 5 (L3): read through a symbolic link, as xform reads it. The digest
    # read refuses a final-component link (`open_regular_file`'s O_NOFOLLOW), so a dropped name
    # that is a link whose target was put back read as still missing: measured with the link's
    # target directory added to RAD_MCP_ROOTS, the read promised this refusal and kept answering.
    # `inside` has contained the resolved file already.
    for name in sorted(sealed.dropped):
        named = Path(name) if Path(name).is_absolute() else loaded.paths.root / name
        if not inside(named):
            continue
        try:
            target = named.resolve()
        except (OSError, RuntimeError):
            continue
        if current_file_digest(target) is None:
            continue
        changed.append(_ChangedInclude(
            f"{name} (oconv could not find it at the run and dropped it, and it can be read now)",
            name, False, None,
        ))
    return changed


def _include_allowed_dirs() -> list[Path]:
    """The directories an include may resolve inside (ruling R823): the configured roots and the
    Radiance library directory, resolved, as `run_annual`'s scan allows them
    (`point_in_time_tool._scan_scene_for_inline_commands`). One that cannot be resolved, or a
    Radiance that cannot be located, is left out."""
    allowed: list[Path] = []
    for root in current().roots:
        try:
            allowed.append(Path(root).resolve())
        except (OSError, RuntimeError):
            continue
    try:
        allowed.append(locate().lib_dir.resolve())
    except (RadianceNotFound, OSError, RuntimeError):
        pass
    return allowed


def _resolves_outside(path: Path, allowed: Sequence[Path]) -> bool:
    """Whether `path`, existing or not, resolves outside every one of `allowed` (final fix F, fix
    round 3, H1). `inside_allowed` answers only for a file that exists (a missing one is
    `OCTREE_INPUT_DROPPED`'s claim, R5), and a dropped include is asked about while it is missing:
    so the name is resolved (symlinks followed, a dangling one to its target) and its nearest
    existing ancestor is contained instead, by the same rule. A resolution that raises is not
    called outside."""
    try:
        probe = path.resolve()
        while not probe.exists() and probe.parent != probe:
            probe = probe.parent
    except (OSError, RuntimeError):
        return False
    return not inside_allowed(probe, list(allowed))


def _dropped_outside_roots(root: Path, dropped: Sequence[str]) -> frozenset[str]:
    """The names in `dropped` (as the `!xform` lines wrote them, a relative one read from `root`,
    oconv's working directory) that resolve outside every allowed directory (final fix F, fix
    round 3, H1). `run_annual`'s scan refuses a scene whose include is there once a file is
    (PATH_OUTSIDE_ROOT), and `_changed_scene_includes` never looks at one, so restoring such a
    file neither re-runs nor refuses a read."""
    if not dropped:
        return frozenset()
    allowed = _include_allowed_dirs()
    return frozenset(
        name for name in dropped
        if _resolves_outside(Path(name) if Path(name).is_absolute() else root / name, allowed)
    )


def _failed_command_label(entry: dict[str, Any]) -> str:
    """How a failed command is named (R823): by the file and line that wrote it."""
    program = entry.get("command") or "inline"
    if entry.get("file") and entry.get("line"):
        return f"the !{program} command at {entry['file']} line {entry['line']}"
    if entry.get("file"):
        return f"an !{program} command in {entry['file']}"
    return f"an !{program} command in the registered scene files"


def _missing_include_label(name: str, failed: Sequence[dict[str, Any]]) -> str:
    """How a missing include outside every allowed directory is named (R823, fix round 4, J1):
    by the file and line whose `!xform` named it, never by the name itself, which for such an
    include is the outside location."""
    for entry in failed:
        if name in entry.get("names", ()) and entry.get("file") and entry.get("line"):
            return f"the include named at {entry['file']} line {entry['line']}"
    return "an include the registered scene names"


def _dropped_outside_sentence(labels: Sequence[str], *, then: str) -> str:
    """What a dropped include outside every allowed directory needs (final fix F, fix round 3,
    H1): its file put inside one, since put back where it is named, `run_annual` refuses the scene
    (PATH_OUTSIDE_ROOT) and no results read looks for it. `labels` name each by the file and line
    whose `!xform` wrote it (`_missing_include_label`, fix round 4, J1): R823 never puts an
    include's target in a message, and a name outside every root is that target.

    Fix round 4 (J2): the remedy is true where the name is a symbolic link inside a root whose
    target lies outside -- "its directory" was the root and "copy it" copied the link. Adding the
    directory the name *resolves into* covers both, and so does copying the file itself, never a
    link to it, and naming the copy. `then` finishes the sentence with what follows the move."""
    one = len(labels) == 1
    it = "it" if one else "them"
    named = _and_list(list(labels))
    return (
        f"{named[:1].upper()}{named[1:]} {'resolves' if one else 'resolve'} outside every allowed "
        "directory (the roots in RAD_MCP_ROOTS and the Radiance library directory), so "
        f"run_annual refuses a scene that includes a file there (PATH_OUTSIDE_ROOT) and no "
        f"results read looks for {it} there; where an !xform line names a symbolic link, it is "
        f"the link's target that lies there. To trace the scene with {it}, put "
        f"{'the file' if one else 'each file'} inside a root: add the directory "
        f"{'it resolves' if one else 'each resolves'} into to RAD_MCP_ROOTS, or copy the file "
        f"itself (not a link to it) into the project and change the !xform "
        f"{'line' if one else 'lines'} to name the copy, and then {then}"
    )


def _where_named(failed: Sequence[dict[str, Any]], *, one: bool) -> str:
    """Where a dropped include was named (final fix G, fix round 2, Q2): "the !xform line names
    it", unless a failed command stopped at a file it could not find among its own words
    (`kind` `"unlisted"`), which a file of arguments read with `-f` can name instead."""
    if any(entry.get("kind") == "unlisted" for entry in failed):
        return (
            "an !xform line, or a file it reads its arguments from with -f, "
            + ("names it" if one else "names them")
        )
    return f"the !xform {'line names it' if one else 'lines name them'}"


def _after_phrase(root: Path, after: Sequence[str]) -> str:
    """The files an `!xform` line names after the one xform stopped at (final fix F, fix round 5,
    P1), as the line wrote them, those resolving outside every allowed directory counted, never
    named (R823)."""
    away = _dropped_outside_roots(root, after)
    shown = [word for word in after if word not in away]
    count = len(after) - len(shown)
    return _and_list(shown + (
        [f"{count} more {'file' if count == 1 else 'files'} outside every allowed directory"]
        if count else []
    ))


def _stopped_after_sentences(root: Path, failed: Sequence[dict[str, Any]]) -> list[str]:
    """For each command xform stopped at a file it could not find while the line names more after
    it (final fix F, fix round 5, P1): xform reads a line's files in order and stops there, so
    those gave oconv nothing either (measured: `!xform missing.rad good.rad` leaves the octree
    empty).

    Final fix G (G-4): where the line's `-a` options make more than one copy, xform writes every
    file of the first copy before the second, so it stops in the first copy, and each file the line
    names before the missing one gave oconv its first copy only (`before`, sealed by
    `annual_tool._failed_include_commands`; measured, Radiance 6.1a: `!xform -a 3 -t 2 0 0 good.rad
    b.rad missing.rad` leaves `a0.goodshelf` and `a0.bshelf` alone, and rtrace finds neither
    shelf under the second or third copy)."""
    sentences = []
    for entry in failed:
        if not entry.get("names"):
            continue
        label = _failed_command_label(entry)
        before, after = entry.get("before"), entry.get("after")
        if before:
            sentences.append(
                f"xform stops at the first file it cannot find, and {label} repeats what it "
                "names with its -a option, which xform writes one copy at a time, so it also gave "
                "oconv only the first copy of what that line names before the file xform could "
                f"not find: {_after_phrase(root, before)}"
                + (
                    f"; and nothing from what it names after that file: "
                    f"{_after_phrase(root, after)}" if after else ""
                )
                + "."
            )
        elif after:
            sentences.append(
                f"xform stops at the first file it cannot find, so {label} also gave oconv "
                f"nothing from what that line names after it: {_after_phrase(root, after)}."
            )
    return sentences


def _failed_command_parts(
    root: Path, rejected: Sequence[dict[str, Any]], *, opening: str, again: str,
    also: str, at_run: str, values: str, says: str,
) -> list[str]:
    """The sentences naming each command whose output oconv did not all receive for another reason
    than a missing file (final fix F, fix rounds 4 and 5, I1, L1), for the run (`run_annual`)
    and for the read (`results`), worded per failure kind
    (`annual_tool._failed_include_commands`), since each needs another correction and loses
    another share:

    - `"line"`: xform rejected the line itself (measured: `-zz`, "command line error"); correcting
      the line is the fix.
    - `"content"`: xform stopped at an error inside a file the line names (measured: an unknown
      object type), after writing what came before it -- the leading geometry is in the octree --
      and wrote nothing of the files the line names after it; correcting that file is the fix (the
      `!xform` line is not wrong, and re-sent unchanged over the same file it fails again).
    - `None`, a failure that wrote neither xform line, or a seal from round 4, which did not record
      the kind: either is said.

    Final fix G (G-1): those three are xform's. Another program's failure (`!gensky 13 21`,
    measured: "gensky: Use error - arg count", oconv's bad status, exit 0) is named by its own
    program and told to correct its line, whatever its kind: gensky includes no file, and round 5
    called it xform's, with an include. G-4: a content error on a line whose `-a` options make more
    than one copy also left only the first copy of each file the line names before the one xform
    stopped in (`before`).

    `opening` starts the first sentence, `also` each later one; `again` ends each with the step
    after the correction; `at_run` is " at the run" on a read; `values` names what describes the
    scene without the output; `says` follows the first sentence on the run only."""
    parts: list[str] = []

    def lead() -> str:
        return opening if not parts else also

    def finish(cause: str, remedy: str) -> None:
        parts.append(cause + (f" {says}" if says and not parts else "") + f" {remedy}")

    xform = [entry for entry in rejected if entry.get("command") == "xform"]
    lines = [entry for entry in xform if entry.get("kind") == "line"]
    unknown = [entry for entry in xform if entry.get("kind") not in (
        "line", "content", "unlisted", "argfile", "argfile_empty")]
    programs = sorted({entry.get("command") or "" for entry in rejected} - {"xform"})
    groups: list[tuple[list[dict[str, Any]], str | None]] = [(lines, "itself"), (unknown, None)]
    groups += [
        ([entry for entry in rejected if (entry.get("command") or "") == program], program)
        for program in programs
    ]
    for group, where in groups:
        if not group:
            continue
        one = len(group) == 1
        it = "it" if one else "them"
        line_word = "that line" if one else "those lines"
        if where == "itself":
            cause = (
                f"xform exited with an error on {line_word} itself{at_run} (not for a file it "
                "could not find, nor inside a file it includes)"
            )
            fix = f"correct {line_word}"
        elif where is None:
            # Fix round 3 (S3): what is left here is xform's own surroundings (a temporary file it
            # could not create, a directory it could not enter, a file it found but could not
            # open: its binary's other format strings) or a round-4 seal of any kind, so the
            # sentence names no cause it cannot see.
            cause = (
                f"xform exited with an error{at_run} that this server does not tell apart from what "
                f"it wrote -- on {line_word}, in a file xform reads for "
                f"{'it' if one else 'them'}, or in xform's own surroundings (a file or directory "
                "it found but could not open, a temporary file it could not create)"
            )
            fix = "correct what xform reported"
        else:
            # G-1: another program than xform, named as base 33ad6d6 named it; it includes no file.
            cause = f"{where or 'the command'} exited with an error on {line_word}{at_run}"
            fix = f"correct {line_word}"
        finish(
            f"{lead()} {_and_list([_failed_command_label(entry) for entry in group])}: {cause}, "
            f"and oconv exited 0 without all of {'its' if one else 'their'} output, so {values} "
            f"describe the scene without all of {it}.",
            f"To trace the scene with {it}, {fix} and {again}",
        )
    for entry in xform:
        if entry.get("kind") not in ("argfile", "argfile_empty"):
            continue
        # Fix round 3 (S3): a file of arguments read with -f could not be opened, or was empty
        # (measured: `xform: cannot open argument file`, `empty argument file`). xform stopped
        # there: on the line's own `-f` it had written nothing, but a `-f` inside a file of
        # arguments can follow lines xform had already written (measured), so the sentence says
        # only that. The file is named as the line wrote it where it is one of the line's words
        # and does not resolve outside every allowed directory (R823).
        word = entry.get("error_in")
        if word and _dropped_outside_roots(root, [word]):
            word = None
        which = (
            f"the file of arguments that line names with -f ({word})" if word else
            "a file of arguments that line reads with -f"
        )
        how = "could not open" if entry["kind"] == "argfile" else "found empty"
        finish(
            f"{lead()} {_failed_command_label(entry)}: xform {how}{at_run} {which} and stopped "
            "there, so oconv received only what xform had written before that point, if anything, "
            "and other files or -a copies that the line's arguments name may be missing too; "
            f"oconv exited 0, so {values} describe the scene without all of it.",
            f"To trace the scene with it, {'restore' if entry['kind'] == 'argfile' else 'fill'} "
            f"that file of arguments, or correct what names it, and {again}",
        )
    for entry in xform:
        if entry.get("kind") != "unlisted":
            continue
        # Fix round 2 (Q2): only what is known -- the line failed on a file xform could not find,
        # which the line does not write among its own words (the dropped-file sentence names it),
        # and what else it lost is not known from the line.
        finish(
            f"{lead()} {_failed_command_label(entry)}: xform stopped{at_run} at a file it could "
            "not find that is not among that line's own words (a file the line reads arguments "
            "from with -f can name it), so oconv received only what xform had written before "
            "that point, and other files or -a copies that the line's arguments name may be "
            f"missing too; oconv exited 0, so {values} describe the scene without all of it.",
            "To trace the scene with it, restore the file xform could not find, or correct what "
            f"names it, and {again}",
        )
    for entry in xform:
        if entry.get("kind") != "content":
            continue
        word = entry.get("error_in")
        if word and _dropped_outside_roots(root, [word]):
            word = None
        inside_file = f"the file {word}" if word else "a file"
        received = ["none, or only part, of what that file holds"]
        if entry.get("before"):
            # G-4: xform writes one -a copy at a time and stopped in the first.
            received.append(
                "only the first of the copies its -a option makes of what that line names before "
                f"it ({_after_phrase(root, entry['before'])})"
            )
        if entry.get("after"):
            received.append(
                f"nothing of what that line names after it ({_after_phrase(root, entry['after'])})"
            )
        lost = (
            received[0] if len(received) == 1
            else f"{', '.join(received[:-1])}, and {received[-1]}"
        )
        finish(
            f"{lead()} {_failed_command_label(entry)}: xform stopped{at_run} at an error inside "
            f"{inside_file}, which that line includes, so oconv received only what xform had "
            f"written before that error -- {lost} -- "
            f"and exited 0, so {values} describe the scene without all of it.",
            "To trace the scene with it, correct "
            f"{word if word else 'the file that line includes in which xform found the error'} "
            f"and {again}",
        )
    return parts


def _dropped_includes_advisory(
    loaded: LoadedProject, artifact_id: str, *, project: str, grid: str,
) -> Advisory | None:
    """`OCTREE_INPUT_DROPPED` (error) on every read of a result whose run compiled the scene
    without an include (final fix F, fix round 2, G1), or `None`.

    **Carried, not refused.** `rad_mcp.envelope`: an `error` advisory describes the value and a
    refusal the call. The values are a correct trace of the scene the run compiled; what is wrong
    is that the scene is not the one the registered files name, the same kind of fact about the
    artifact that `WINDOW_GROUP_V_MATRIX_ALL_ZERO` rides `_Resolved.blend_advisories` for (R846),
    and `compliance` never refuses on a property of its values (R174). The run said so on its
    preview and reply, and the job on its status; a refusal here would hold back every read of a
    result whose caller may have meant the file to be gone, with no re-run that clears it but
    restoring the file or editing the scene. Once the file is back, `_changed_scene_includes`
    refuses the read (SCENE_INCLUDE_CHANGED), so this runs only while it is still missing -- or,
    for a name outside every allowed directory (fix round 3, H1), for as long as it is there,
    since no read looks at it: the message then says it must move inside a root, and spells the
    re-run that follows (`_scene_include_rerun`).

    Fix round 4: such a name is named by the file and line whose `!xform` wrote it (J1, R823), and
    a command xform rejected for any other reason (`SCENE_INCLUDE_FAILED_PARAMETER`, I1) is carried
    on every read the same way, named by its file and line and told to be corrected before the
    re-run. No read can see that line corrected, so it is carried until the re-run reseals."""
    sealed = _sealed_scene_includes(loaded, artifact_id)
    dropped = sorted(sealed.dropped)
    # Fix round 4 (I1): a command xform rejected for another reason than a missing file.
    rejected = [entry for entry in sealed.failed if not entry["names"]]
    if not dropped and not rejected:
        return None
    rerun_cache: list[str] = []

    def rerun() -> str:
        if not rerun_cache:
            spelled = _scene_include_rerun(
                loaded, project=project, grid=grid, artifact_id=artifact_id,
                edition=DEFAULT_LM83_EDITION,
            )
            rerun_cache.append(f"{spelled[0].lower()}{spelled[1:]}")
        return rerun_cache[0]

    parts: list[str] = []
    if dropped:
        one = len(dropped) == 1
        it = "it" if one else "them"
        # Final fix F, fix round 3 (H1): a name outside every allowed directory is never looked at
        # by `_changed_scene_includes`, and a re-run is refused PATH_OUTSIDE_ROOT once a file is
        # there, so "restore it" is offered only for the names inside them. Fix round 4 (J1): such
        # a name is named by the file and line whose !xform wrote it, never by itself (R823).
        outside = _dropped_outside_roots(loaded.paths.root, dropped)
        inside = [name for name in dropped if name not in outside]
        shown = [
            name if name not in outside else _missing_include_label(name, sealed.failed)
            for name in dropped
        ]
        # Where `_changed_scene_includes` looks for it, and where xform reads a relative name:
        # oconv's working directory, the project root.
        restore = [
            str(Path(name) if Path(name).is_absolute() else loaded.paths.root / name)
            for name in inside
        ]
        head = (
            f"the stored result {artifact_id!r} was traced over a scene without "
            f"{', '.join(shown)}: the run that sealed it could not find "
            f"{'that file' if one else 'those files'} where "
            + _where_named(sealed.failed, one=one)
            + ", and oconv exited 0 without "
            f"{'its' if one else 'their'} geometry, so these values describe the scene without "
            f"{it}."
        )
        if not outside:
            parts.append(
                f"{head} To trace the scene with {it}, restore {_and_list(restore)}: every read "
                "of this result is then refused (SCENE_INCLUDE_CHANGED) with the run_annual "
                "re-run that reseals it."
            )
        else:
            parts.append(head + (
                f" To trace the scene with {_and_list(inside)}, restore {_and_list(restore)}: "
                "every read of this result is then refused (SCENE_INCLUDE_CHANGED) with the "
                "run_annual re-run that reseals it."
                if inside else ""
            ) + " " + _dropped_outside_sentence(
                [_missing_include_label(name, sealed.failed) for name in dropped
                 if name in outside],
                then=rerun(),
            ))
        # Fix round 5 (P1): what xform never read after a missing file, after the restore
        # sentence, so its "it" still names the missing file.
        parts.extend(_stopped_after_sentences(loaded.paths.root, sealed.failed))
    if rejected:
        # Fix round 5 (L1): worded per failure kind (`_failed_command_parts`).
        parts.extend(_failed_command_parts(
            loaded.paths.root, rejected,
            opening=(
                f"the stored result {artifact_id!r} was traced over a scene without all of the "
                "output of" if not dropped else "Nor did it receive all of the output of"
            ),
            also="Nor did it receive all of the output of", again=f"then {rerun()}",
            at_run=" at the run", values="these values", says="",
        ))
    message = " ".join(parts)
    return Advisory(
        code="OCTREE_INPUT_DROPPED", severity="error", field="grid", message=message,
    )


def _refuse_changed_scene_includes(
    loaded: LoadedProject, artifact_ids: Sequence[str], *, project: str, grid: str,
    rerun_for: str | None = None, edition: Lm83Edition = DEFAULT_LM83_EDITION,
) -> None:
    """Raise `_SceneIncludeChanged` for the first of `artifact_ids` an octree of which compiled an
    included file that has changed since (final fix E, fix round 4, X1). Run right after
    `_refuse_stale_results` on the same ids, at each of its call sites: what that judges off the
    manifest, this judges off the files the manifest cannot see. `rerun_for` names the result the
    remedy re-runs, where the id refused is a direct-sun matrix or schedule read beside it."""
    for artifact_id in artifact_ids:
        changed = _changed_scene_includes(loaded, artifact_id)
        if not changed:
            continue
        files = "; ".join(change.described for change in changed)
        remedy = _scene_include_rerun(
            loaded, project=project, grid=grid, artifact_id=rerun_for or artifact_id,
            edition=edition,
        )
        unreadable = [change for change in changed if change.missing]
        if unreadable:
            # Final fix F (F2): whether that re-run compiles is not known here. The include search
            # records every file a name could reach, a superset of what Radiance reads, so a
            # missing name Radiance never read leaves the re-run to compile and succeed (measured);
            # one it does read is refused, oconv exiting 1 on a modifier the file defined
            # (measured), unless it defined nothing the scene uses, when oconv warns and compiles
            # without it. So the sentence is conditional, and restoring is its only fix: removing
            # the !xform line would leave that modifier undefined, and in the unread case would
            # drop the include Radiance does read. Fix round 5 (N3): the line that names it is in
            # the file the record says includes it -- another include, for a nested one.
            # Fix round 2 (G2): oconv stops at the first undefined modifier, so a refusal names
            # one missing file, not all of them; the condition is on one, the restore on all.
            one = len(unreadable) == 1
            remedy += (
                " If that re-run is refused because oconv cannot find "
                + ("this file (it exits 1 when a modifier the file defined is left undefined)"
                   if one else
                   "one of these files (it exits 1 when a modifier one of them defined is left "
                   "undefined, and names the first file it could not find)")
                + ", restore "
                + ("" if one else "all of them: ")
                + (_and_list([change.path for change in unreadable])
                   if all(change.includer is None for change in unreadable)
                   else "; and ".join(
                       change.path
                       + (f", which an !xform line in {change.includer} names"
                          if change.includer is not None else "")
                       for change in unreadable
                   ))
                + ", and send the re-run again; if it runs, it compiled the scene as it is now, "
                + ("without that file, and its reply names it if oconv looked for it"
                   if one else
                   "without those files, and its reply names each one oconv looked for")
                + " (OCTREE_INPUT_DROPPED)."
            )
        raise _SceneIncludeChanged(
            f"the stored result {artifact_id!r} was traced over a scene whose !xform "
            f"{'include has' if len(changed) == 1 else 'includes have'} changed since the run "
            f"that sealed it compiled {'it' if len(changed) == 1 else 'them'}: {files}. An "
            "included file is read in place and is no input of the project, so no digest moved "
            "when it changed and the manifest still calls the result current, but its values "
            "describe the scene as it was. No stored value was read. " + remedy
        )


def _rerun_scan_refusal(loaded: LoadedProject) -> SceneInlineCommandError | None:
    """The refusal `run_annual`'s `!`-line scan gives the registered scene as it is now, or `None`
    (final fix E, fix round 4: X1, and review M1). The scan itself, over the files `run_annual`
    compiles (`point_in_time_tool._scan_scene_for_inline_commands` over `_scene_files`), so its code
    is the one the re-run is refused with: `SCENE_INLINE_COMMAND`, or `PATH_OUTSIDE_ROOT` for a
    file outside every allowed directory. Anything else the scene files raise is left to the
    re-run's own refusal, which says what it is."""
    from rad_mcp.point_in_time_tool import _scan_scene_for_inline_commands, _scene_files

    try:
        _scan_scene_for_inline_commands(
            _scene_files(loaded, luminaires=False), cwd=loaded.paths.root
        )
    except SceneInlineCommandError as exc:
        return exc
    except (
        ProjectError, PathOutsideRoot, RootsNotConfigured, SceneFileNotFoundError, OSError,
        ValueError,
    ):
        return None
    return None


def _scene_include_rerun(
    loaded: LoadedProject, *, project: str, grid: str, artifact_id: str, edition: Lm83Edition,
) -> str:
    """`SCENE_INCLUDE_CHANGED`'s remedy (final fix E, fix round 4, X1): the `run_annual` call that
    reseals `artifact_id`'s chain from the files as they are now. Its octrees record the included
    files' digests in their provenance (`SCENE_INCLUDES_PARAMETER`), so every matrix traced over
    the old bytes is rebuilt rather than reused (measured before that: a two-phase re-run reused
    3 of its 3 steps).

    A blend or its schedule: `_blend_rerun_remedy`, `BLIND_SCHEDULE_NOT_CURRENT`'s calls, under the
    edition the schedule was sealed under. Another result: its own chain (`_recorded_chain`), with
    `blind_control='lm83'` on a two- or five-phase one where `illdir:<grid>` is registered, so the
    direct-sun leg is re-planned too (on a three-phase one it would plan a blend instead). An
    unblended three- or five-phase result built from other window groups than the project
    registers is not re-run on its own chain, which `run_annual` refuses with more than one group
    (`WINDOW_GROUPS_NOT_CURRENT`'s measurement); the two-phase run that traces every aperture is
    spelled instead. `_rerun_blocked_by_grid` is honoured. Where `run_annual`'s `!`-line scan
    refuses the scene as it is now, the remedy says so: the re-run is refused for it first."""
    refused = _rerun_scan_refusal(loaded)
    scan = (
        f" run_annual refuses that re-run for the scene as it is now ({refused.code}) until this "
        f"is fixed: {str(refused).rstrip('.')}."
        if refused is not None else ""
    )
    ids = {artifact.id for artifact in loaded.manifest.artifacts}
    blend, schedule = blend_ill_id(grid), blind_schedule_id(grid)
    if artifact_id.startswith((_DIRECT_ARTIFACT_PREFIX, "illdirnet:")):
        artifact_id = next(
            (other for other in (blend, f"ill:{grid}", f"ill5:{grid}") if other in ids),
            artifact_id,
        )
    if artifact_id in (blend, schedule):
        try:
            sealed_edition = sealed_lm83_edition(loaded.manifest.artifact(schedule).parameters)
        except (KeyError, ValueError):
            sealed_edition = edition
        current_groups = sorted(
            record.id.removeprefix(WINDOW_GROUP_INPUT_PREFIX) for record in loaded.manifest.inputs
            if record.kind == "window_group"
        )
        reached = _window_groups_reached(loaded, artifact_id)
        added = [name for name in current_groups if reached and name not in reached]
        return _blend_rerun_remedy(
            loaded, project=project, grid=grid, edition=sealed_edition, added=added,
        ) + scan
    # Final fix G (G-3): the re-run is spelled from the sealed records whether or not the grid
    # blocks it -- it is the call this read spells once the grid is back -- and, where it is
    # blocked, the grid's refusal follows it, as `_blend_rerun_remedy` words the blend's.
    blocked = _rerun_blocked_by_grid(loaded, grid, project=project)
    try:
        method = _recorded_chain(loaded.manifest.artifact(artifact_id))
    except KeyError:
        method = "2phase"
    if method != "2phase" and _groups_not_current(loaded, artifact_id):
        call = _rerun_call(
            loaded, project=project, grid=grid, starts=((artifact_id, _SMX_KIND),),
            quality_from=artifact_id, arguments="metrics=['da'], blind_control='lm83'",
            method="2phase",
        )
        refused_now = (
            f"A {'five' if method == '5phase' else 'three'}-phase re-run is refused while the "
            "project registers other window groups than this result was built from, and a "
            "two-phase run traces every aperture of the scene, claimed by a window group or not, "
        )
        if blocked is not None:
            return (
                f"{refused_now}from the files as they are now -- {call}, to be read with "
                f"chain='2phase' -- but it would be refused on this grid first: {blocked}" + scan
            )
        return (
            f"{refused_now}from the files as they are now: {call}; then read it with "
            "chain='2phase'." + scan
        )
    direct = method != "3phase" and f"{_DIRECT_ARTIFACT_PREFIX}{grid}" in ids
    call = _rerun_call(
        loaded, project=project, grid=grid,
        starts=((artifact_id, _SMX_KIND), (artifact_id, _SMXSUN_KIND)),
        quality_from=artifact_id,
        arguments="metrics=['da']" + (", blind_control='lm83'" if direct else ""),
        method=method,
    )
    if blocked is not None:
        # Fix round 2 (Q1): imperative, so that after OCTREE_INPUT_DROPPED's "and then" it is one
        # instruction ("A re-run, ..., reseals it" read "and then a re-run ... reseals it").
        return (
            f"Re-run {call} to reseal it from the files as they are now, once run_annual is no "
            f"longer refused on this grid, as it is now: {blocked}" + scan
        )
    return (
        f"Re-run {call}: its octrees record the included files as they are when it runs, so "
        "every matrix traced over other bytes than those is rebuilt rather than reused." + scan
    )


def _refuse_grid_off_recorded_floor(
    loaded: LoadedProject, grid: str, *, project: str | None = None,
) -> None:
    """R776: `results` refuses a grid whose points are not on its recorded floor, as the run tools do.

    Only that, and (final fix E2) a grid whose `.pts` no longer holds the bytes its record digested:
    a grid that is not registered, is zoned, records no polygon or cannot be read is left to the
    paths that already handle it (`xyz: null`, `coverage: null`, the refusals of `_analysis_area`).
    The grid judged is the grid as it is now; a stored result traced from an earlier grid is refused
    as stale before this runs (`_refuse_stale_results`, R785), so the refusal's remedy names the
    re-run of `run_annual` as well as the re-create.

    **The digest is checked on every read, polygon or none** (final fix E2). Staleness is read off
    the manifest alone (`evaluate_loaded` never re-hashes an input file), so a `.pts` edited by hand
    left every stored result valid, and compliance, metrics and blind_schedule (under LM-83-12 or
    the default) published values traced at points the file no longer holds; only the reads that
    open the points themselves refused. The check is `_read_points`' own -- the read and digest
    `run_annual`'s `_validate_project` makes -- and it refuses `SENSOR_GRID_FILES_CHANGED`
    (`GridFileChangedError`) with its own remedy and a sentence saying what re-sending the recorded
    create does here (`_changed_grid_file`)."""
    candidates = {grid, f"{SENSOR_GRID_INPUT_PREFIX}{grid}"}
    record = next(
        (r for r in loaded.manifest.inputs if r.kind == "sensor_grid" and r.id in candidates), None
    )
    if record is None or record.zones is not None or not record.files:
        return
    # Final fix E, fix round 3 (review N2): the path as named, as run_annual reads it
    # (`open_regular_file`'s O_NOFOLLOW). `contain` resolves a symlink, so a `.pts` replaced by one
    # was read through (or, aimed outside every root, left to PATH_OUTSIDE_ROOT and read past)
    # while run_annual refused it "Too many levels of symbolic links".
    named = Path(record.files[0].path)
    try:
        linked = stat.S_ISLNK(named.lstat().st_mode)
    except OSError:
        linked = False  # missing: `_read_points` refuses it below
    if linked:
        raise _changed_grid_file(
            GridUnreadableError(
                f"{named} cannot be read as a sensor grid: it is a symbolic link, and run_annual "
                "reads a grid's file only as the regular file its record names."
            ),
            record, grid=grid, project=project,
        )
    try:
        points, _text = _read_points(
            contain_as_named(named, current().roots), record.files[0].digest, grid=grid
        )
    except GridFileChangedError as exc:
        raise _changed_grid_file(exc, record, grid=grid, project=project) from None
    except (GridUnreadableError, GridEmptyOnDiskError) as exc:
        # Final fix E, fix round 2 (review O2): a `.pts` that is missing, is not a regular file,
        # cannot be opened or is over `MAX_POINTS_BYTES` no longer holds the points its record
        # digested either, so it is refused the same way rather than read past. Every value the
        # read would publish was traced at points no file now holds.
        raise _changed_grid_file(exc, record, grid=grid, project=project) from None
    except UnicodeDecodeError:
        # Decoded only after the digest matched, so its bytes are the ones the record digested
        # and sensor_grid wrote UTF-8: not reachable without a hand-edited manifest, which moves
        # the grid's digest and is refused stale first. Left to the paths that handle it.
        return
    except PathOutsideRoot:
        # Final fix G, fix round 2 (Q1, controller ruling): this returned, so a `.pts` reached
        # through a directory linked outside every root was never digested, and every read
        # answered -- compliance included -- over a file edited there (measured through the real
        # tools). It is refused, as sensor_grid refuses to write there.
        raise _grid_file_outside_roots(named, grid=grid) from None
    except (RootsNotConfigured, ValueError, OSError):
        # A root-less runtime is another refusal's to make.
        return
    if record.registered_by is None:
        return
    polygon = record.registered_by.arguments.get("polygon")
    if not polygon:
        return
    refuse_grid_off_recorded_floor(
        grid, polygon, points,
        height=record.registered_by.arguments.get("height"),  # R784
        # Final review I2 (2026-10-04), corrected by R785: re-creating the grid makes the stored
        # result stale, and `_refuse_stale_results` then refuses it, so the remedy names the re-run.
        outcome=(
            "No stored value was read. After re-creating it, re-run run_annual before asking "
            "results again: the result already stored was traced at these points, and once the "
            "grid is re-created results refuses it as stale."
        ),
    )


class GridFileOutsideRoot(PathOutsideRoot):
    """PATH_OUTSIDE_ROOT for a sensor grid whose `.pts` resolves outside every allowed root (final
    fix G, fix rounds 2 and 3, Q1, S1, S2): refused by every `results` read and by `run_annual`, with
    `field="grid"`, the argument the caller changes (controller ruling S2), where every other
    `PathOutsideRoot` carries `"root"` (`runtime.refuse_engine_error`)."""

    field = "grid"


def _named_inside_a_root(named: Path) -> bool:
    """Whether `named` lies inside an allowed root before the symbolic link that takes it outside:
    some ancestor of it, as written, resolves inside one (final fix G, fix round 3, N1). Both sides
    are resolved -- `rad_engine.octree.inside_allowed` over the roots `expanded_root` resolves, as
    `contain` judges -- so a root named through a symbolic link (macOS's `/tmp`, R823) holds a
    record that names its resolved path. Measured before this: with `RAD_MCP_ROOTS` an alias of
    the real root, the read said "the grid's record names it there" of a record naming a file
    inside the root. Where no ancestor resolves inside a root, the record itself names a place
    outside them, which is not printed (R823)."""
    try:
        roots = current().roots
    except RootsNotConfigured:
        return False
    allowed = [root for root in (expanded_root(r) for r in roots) if root is not None]
    lexical = Path(os.path.normpath(os.path.abspath(named)))
    for ancestor in lexical.parents:
        try:
            if ancestor.exists() and inside_allowed(ancestor, allowed):
                return True
        except (OSError, RuntimeError):
            continue
    return False


def _grid_file_outside_roots(named: Path, *, grid: str) -> GridFileOutsideRoot:
    """The refusal of a grid whose `.pts` resolves outside every allowed root (final fix G, fix
    rounds 2 and 3, Q1, S1): the same words on every `results` read and on `run_annual` (controller
    ruling S1), so no run produces a result every read refuses. The grid is named by its handle and
    by its path as the record names it, never by the outside target (R823); where the record's
    path is itself outside, it is not printed.

    Measured through the real tools (`yard/11_sensors` moved outside the root and replaced by a
    symbolic link to it): with `RAD_MCP_ROOTS` extended by the directory it resolves into, or the
    link replaced by the directory itself, the call sent again checks the file against the grid's
    record, and goes on, or is refused SENSOR_GRID_FILES_CHANGED where the file was edited."""
    linked = _named_inside_a_root(named)
    where = (
        f"the file of sensor grid {grid!r} ({named}) resolves outside every allowed directory (the "
        "roots in RAD_MCP_ROOTS and the Radiance library directory): a directory on that path is "
        "a symbolic link whose target lies outside them"
        if linked else
        f"the file of sensor grid {grid!r} lies outside every allowed directory (the roots in "
        "RAD_MCP_ROOTS and the Radiance library directory): the grid's record names it there"
    )
    fix = (
        "add the directory it resolves into to RAD_MCP_ROOTS, or replace that symbolic link with "
        "the directory itself"
        if linked else "add the directory it lies in to RAD_MCP_ROOTS"
    )
    return GridFileOutsideRoot(
        f"{where}. Nothing was read or traced: this server reads a grid's file only inside them, "
        "where it checks that the file still holds the points the grid's record digested"
        + (", and sensor_grid refuses to write the grid's file through that link too "
           "(PATH_OUTSIDE_ROOT)" if linked else "")
        + f". Put the file inside a root -- {fix} -- and send the call again: the file is then "
        "checked against the grid's record."
    )


def _grid_outside_refusal(named: Path, *, grid: str) -> GridFileOutsideRoot | None:
    """`_grid_file_outside_roots` where `named` resolves outside every allowed root, else `None`
    (final fix G, fix round 3, S1): the one check the read, `run_annual` and the remedy that
    foretells `run_annual`'s refusal (`_rerun_blocked_by_grid`) make, before the file is read."""
    try:
        contain_as_named(named, current().roots)
    except PathOutsideRoot:
        return _grid_file_outside_roots(named, grid=grid)
    except (RootsNotConfigured, ValueError, OSError, RuntimeError):
        return None
    return None


def _unrecorded_normal_caveat(why: str, *, call: str = "that call") -> str:
    """The sentence a re-create spelled from a grid record that predates the recorded normal
    carries (fix round 2, review O1; shared by final fix F, fix round 3, H3): the call names no
    normal, `why` says why it is not read from elsewhere, and the caller is told what to add to
    `call`."""
    return (
        f" This record predates the recorded normal{why}: if the grid was created facing other "
        "than straight up, add normal=[<x>, <y>, <z>] with the normal it was created with to "
        f"{call}."
    )


def _changed_grid_file(
    exc: Exception, record: Any, *, grid: str, project: str | None, quoted: bool = False,
) -> GridFileChangedError:
    """`exc` -- `_read_points`' own `SENSOR_GRID_FILES_CHANGED` refusal, its remedy included --
    with what a `results` read adds (final fix E2): that no stored value was read, and the create
    call the grid's record holds, spelled whole, with what re-sending it does. `sensor_grid` places
    the same points for the same arguments, so where they are the points the stored result was
    traced at the read then answers; otherwise the re-create moves the grid's digest and the
    result is refused as stale until `run_annual` is re-run. A record carrying no create call gets
    the first sentence alone.

    Fix round 2 (review O1): `sensor_grid` records `normal` from now on, and it is spelled with the
    rest. A record from before that holds none; the `.pts` holds each point's normal, but in every
    state this refusal is given the file is not the one the record digested, so the normal is not
    read from it, and the sentence says what to pass instead.

    Final fix F, fix round 5 (L2): `quoted` is the form `_rerun_blocked_by_grid` gives, where a
    remedy says the re-run `run_annual` would be refused on this grid first. There the refusal is
    `run_annual`'s, so nothing a read adds is in it, and what the re-create does is said of that
    run: measured through the real tools, after the pasted create the read was still refused
    (SCENE_INCLUDE_CHANGED) until the re-run, so "results then reads it" was false there.

    Final fix G, fix round 3 (S1): a grid whose file resolves outside every root never reaches
    this -- the read and `run_annual` refuse it PATH_OUTSIDE_ROOT first, and
    `_rerun_blocked_by_grid` quotes that -- so the create it spells is not refused for that."""
    arguments = (
        dict(record.registered_by.arguments)
        if record.registered_by is not None and record.registered_by.action == "create"
        else None
    )
    spelled = ""
    if arguments and project is not None:
        parts = ["action='create'", f"project={project!r}"] + [
            f"{key}={value!r}" for key, value in arguments.items() if value is not None
        ]
        spelled = (
            f" Re-sent as recorded, sensor_grid({', '.join(parts)}) writes the points it places "
            + (
                "for those arguments, and run_annual is then not refused on this grid for its "
                "points."
                if quoted else
                "for those arguments: where they are the points the stored result was traced at, "
                "results then reads it; otherwise results refuses that result as stale until "
                "run_annual is re-run."
            )
            + (
                "" if "normal" in arguments else _unrecorded_normal_caveat(
                    ", and the grid's file, which holds each point's normal, is not the one the "
                    "record digested, so the normal is not read from it",
                )
            )
        )
    # Fix round 2 (review O2): `sensor_grid` will not write through a directory, symlink or FIFO
    # at the grid's path (measured: "exists and is not a regular file"), so the re-create is
    # foretold to need it removed first.
    obstruction = ""
    try:
        target = Path(record.files[0].path)
        mode = target.lstat().st_mode
    except (OSError, IndexError):
        mode = None
    if mode is not None and stat.S_ISLNK(mode):
        # Fix round 3 (review N2): replacing the link with the file it points to restores the
        # points where that file holds the bytes the record digested; otherwise the re-create.
        # Fix round 4 (review M2): "the call above" only where one is spelled; a record holding
        # no create call (reachable by a manifest edit) is told to re-create the grid instead.
        recreate = (
            "send the call above" if spelled else
            "re-create the grid with sensor_grid(action='create', "
            + (f"project={project!r}, " if project is not None else "")
            + f"name={grid!r}, ...)"
        )
        # Final fix G (G-2): quoted, this sits inside another refusal (SCENE_INCLUDE_CHANGED,
        # measured still refused after the link was replaced), so the refusal it ends is named.
        ends = "end run_annual's refusal on this grid" if quoted else "restore the read"
        obstruction = (
            f" A symbolic link is at {target}: replace it with the file itself (the bytes the "
            f"record digested {ends}), or remove "
            f"it and {recreate}, since sensor_grid will not write the grid's points through it."
            + ("" if spelled or quoted else
               " Unless the re-created grid holds the points the record digested, results then "
               "refuses the stored result as stale until run_annual is re-run.")
        )
    elif mode is not None and not stat.S_ISREG(mode):
        obstruction = (
            f" Something other than a regular file is at {target}; remove it first, since "
            "sensor_grid will not write the grid's points through it."
        )
    refusal = GridFileChangedError(
        f"{exc}{spelled}{obstruction}" if quoted else
        f"{exc} No stored value was read: the stored results of grid {grid!r} were traced at "
        f"the points its record digested.{spelled}{obstruction}"
    )
    refusal.field = "grid"  # the argument that named the grid, as `_ResultStale` says
    return refusal


def _analysis_area(
    loaded: LoadedProject, *, grid: str, edition: Lm83Edition = DEFAULT_LM83_EDITION
) -> _AnalysisArea:
    """The analysis area for `grid`, derived from the polygon its registration recorded.

    **Under `edition="LM-83-22"` (R864) the area is the LM-83-22 glossary's instead**: the grid's
    own point count times the row and column spacings read back off its `.pts`
    (`rad_core.grids.grid_spacing_m`, `lm83_points_area_sf`), with `area_source`
    `_AREA_SOURCE_POINTS`. The polygon is still required and still gives `points_expected` and
    `coverage`, so the one-source rule below holds under both editions;
    `ANALYSIS_AREA_UNDER_COVERED` is not built under LM-83-22, because its sentence says the
    published area is the polygon's.
    A grid whose points give no spacing (one sensor, a zoned grid, no `.pts`) is refused with
    `ANALYSIS_AREA_NOT_DERIVABLE`, never given a guessed one.

    **The polygon, not the sensor cloud, and not a spacing.** No manifest field records a grid's
    spacing at all -- `sensor_grid(action="create")` registers `standard`, `name` and `polygon`, and
    `InputRecord` has no `parameters` field -- so the recorded polygon is the only thing an area can
    come from at read time. It is also the right thing to come from: LM-83 section 2.2.6 keys its
    blind bands on the area of the *space* and requires grids to span it
    ([ch04](../../skills/lighting-standards-compliance/chapters/ch04-ies-lm-83-sda-ase.md)), while
    the same clause's 12-24 in wall offset guarantees the sensor cloud is smaller than the space.
    `point_in_time_tool._grid_points_file` reads the same field for the same reason and calls it
    "the room outline"; this is the second reader, not a second source.

    **Metres in, square feet out, once.** The polygon is always in metres (`sensor_grid` has no
    `units` argument and its docstring says so; `InputRecord.units` is the *scene* input's declared
    unit and is never applied to a polygon), and the table is in square feet, so the single
    `rad_core.grids.M2_TO_SQFT` conversion happens in this function and in no *other* function --
    which is what matters, and is not what this sentence used to say. It said "here and nowhere
    else" while `area_m2 * M2_TO_SQFT` is written at two expressions **inside** it, the advisory
    message recomputing what the returned value already carries. The claim a reader needs is that
    no second function converts, and that is the claim now made; the two expressions within one
    function are pinned by `tests/test_grids.py::TestSquareFootConversion`, which counts the factor
    across `src/`.

    **Refuses above every file access** (R39): the polygon comes out of the manifest and is turned
    into an area before the grid's `.pts` is opened at all, so a grid the server cannot size is
    refused without touching the disk. The `.pts` is only read afterwards, for the coverage
    advisory, and it is re-contained on the way (R50) because a path out of a manifest is untrusted
    on every action that touches it.

    **Under-coverage advises and never refuses** (owner ruling, Phase 7c Task 15). A correctly-inset
    LM-83 grid covers less than its polygon by construction, so refusing on the gap would reject the
    grids the standard asks for; `ANALYSIS_AREA_UNDER_COVERED` states it at `info` and the caller
    decides. `rad_core.grids.GRID_POLYGON_COVERAGE_MIN` holds the fraction and the reasoning -- the
    number is a substituted default and lives in `rad_core`, because `rad_mcp` carries no thresholds
    (CONTEXT.md, Layers).

    **That ruling is untouched by the refusal Task 16 added**, and the distinction is the whole of
    why (ruling R171). `coverage` is an **area** -- the floor the grid's own sensor cells sample,
    over the polygon's real area -- and it still gates nothing but an `info`. `points_expected`
    below is a **count**, and it is the density formed from *it* that `_blind_schedule` refuses on,
    and only below 1 000 sf. The two disagree in both directions on grids this server places itself,
    so neither can stand in for the other: a correctly-placed LM-83 grid in a 1.2 x 40 m corridor
    samples 0.495 of its polygon at density 1.000, and a 3 m grid over a 12 x 15 m room samples
    1.000 at density 0.044.

    **The measure changed in Phase 7d-1 Task 17 and the threshold did not** (ruling R160). It was a
    ratio of bounding boxes, whose least observable value was 0.0 -- a single column of sensors has
    a zero-area box -- so no positive floor could close the false-positive class it produced. The
    office fixture read 0.828 under it and reads 0.912 under this one.

    **`points_expected` comes from the same polygon as the area, in the same function, deliberately.**
    That is the "one source" this task is named for: a point count derived here cannot drift from the
    area that selects the row, because one call produced both.

    Raises `GridNotFoundError` if no such grid is registered, `_AnalysisAreaNotDerivable` if its
    registration recorded no usable polygon. Both are `ProjectError`s, which is the family `results`
    already shapes into a refusal.
    """
    candidates = {grid, f"{SENSOR_GRID_INPUT_PREFIX}{grid}"}
    record = next(
        (
            r for r in loaded.manifest.inputs
            if r.kind == "sensor_grid" and r.id in candidates
        ),
        None,
    )
    if record is None:
        raise GridNotFoundError(
            f"no sensor grid named {grid!r} is registered in this project. Call "
            f"sensor_grid(action='create', name={grid!r}, ...) first, or sensor_grid(action='list') "
            "to see what is registered."
        )

    polygon = None
    if record.registered_by is not None:
        polygon = record.registered_by.arguments.get("polygon")
    if not polygon and edition == LM83_22:
        # R864: LM-83-22's area is points x spacing, so the polygon is not what sizes it; it is
        # still what `points_expected` and `coverage` come from, and the one-source rule needs both.
        raise _AnalysisAreaNotDerivable(
            f"sensor grid {grid!r} records no room polygon. Under LM-83-22 the analysis area is "
            "the grid's points times their spacing, but this server also counts the points LM-83's "
            "0.6 m placement puts inside the room outline and how much of it the grid samples, "
            "and reports both beside the area when the blind schedule is read, so a grid with no "
            "recorded outline is not read. "
            f"Re-create the grid with sensor_grid(action='create', name={grid!r}, "
            "polygon=[[x, y], ...]), which records the outline it places points inside."
        )
    if not polygon:
        raise _AnalysisAreaNotDerivable(
            f"sensor grid {grid!r} records no room polygon, so this server cannot say how large "
            "the analysis area is. LM-83 section 2.2.6 keys its blind trigger on that area and its "
            "bands span an order of magnitude -- from '> 1 point in sun' below 200 sf to '> 2 % of "
            "points' at 1 000 sf and above -- so guessing one moves the trigger by a factor of ten "
            "on every hour of the schedule. Re-create the grid with "
            f"sensor_grid(action='create', name={grid!r}, polygon=[[x, y], ...]), which records the "
            "outline it places points inside."
        )

    try:
        area_m2 = polygon_area_m2(polygon)
    except (PolygonError, TypeError, ValueError, IndexError, KeyError) as exc:
        # **Wider than `PolygonError`, and the width is load-bearing rather than defensive habit.**
        # `rad_core.grids._as_points` normalises every malformed-vertex failure into `PolygonError`
        # as of fix round 1, so in a correct build this clause only ever sees that -- but
        # `PolygonError` is a `ValueError` *subclass*, and before that normalisation existed
        # `float("a")` on a hand-edited manifest polygon raised the bare base class straight past
        # this handler and out of `results` as a crash. The polygon is manifest content, which R50
        # holds untrusted on every action that touches it, so this seam keeps the base class in its
        # own catch: `tests/test_results_tool.py` proves the clause still fires with the pure
        # layer's guard removed, so it is a live second line rather than a dead branch.
        # `_grid_polygon_coverage_or_none` below has always caught the base class; the two paths in
        # this file now agree.
        raise _AnalysisAreaNotDerivable(
            f"sensor grid {grid!r} records a room polygon this server cannot take an area from: "
            f"{exc} Re-create the grid with a polygon that encloses the space."
        ) from exc

    coverage = _grid_polygon_coverage_or_none(record, polygon, grid=grid)
    if edition == LM83_22:
        n_points, dx, dy = _points_and_spacing(record, grid=grid)
        return _AnalysisArea(
            area_m2=n_points * dx * dy,
            area_sf=lm83_points_area_sf(n_points, dx, dy),
            area_source=_AREA_SOURCE_POINTS,
            coverage=coverage,
            points_expected=_lm83_expected_points_or_none(polygon, grid=grid),
            advisories=(),
        )
    advisories: list[Advisory] = []
    if coverage is not None and coverage < GRID_POLYGON_COVERAGE_MIN:
        advisories.append(Advisory(
            code="ANALYSIS_AREA_UNDER_COVERED",
            severity="info",
            field="grid",
            message=(
                f"the sensor grid {grid!r} samples {coverage:.0%} of its recorded polygon's floor "
                f"area, below {GRID_POLYGON_COVERAGE_MIN:.0%}. The published analysis area is the "
                f"polygon's ({area_m2:.1f} m2, {area_m2 * M2_TO_SQFT:.0f} sf) because LM-83 section "
                "2.2.6 keys its blind bands on the space and requires grids to span it -- but this "
                "grid's own sensor cells cover materially less of that space than the area claims. "
                "LM-83's own "
                "12-24 in wall offset accounts for some of the gap; a grid built over part of the "
                "space, or a polygon enlarged after the grid was placed, accounts for the rest. "
                "Nothing is refused on this: the area stands and the discrepancy is yours to judge."
            ),
        ))

    return _AnalysisArea(
        area_m2=area_m2,
        area_sf=area_m2 * M2_TO_SQFT,
        area_source=_AREA_SOURCE_POLYGON,
        coverage=coverage,
        points_expected=_lm83_expected_points_or_none(polygon, grid=grid),
        advisories=tuple(advisories),
    )


def _points_and_spacing(record: InputRecord, *, grid: str) -> tuple[int, float, float]:
    """`(n_points, dx_m, dy_m)` off the grid's own `.pts` -- the LM-83-22 glossary's two factors.

    The `.pts` path is re-contained (R50: a manifest path is untrusted). `GridUnreadableError`,
    `GridEmptyOnDiskError` and the containment refusals propagate as the refusals they already
    are; a grid with no single spacing to read is `_AnalysisAreaNotDerivable`, because a guessed
    spacing would be a substituted default published as a derived area. A `.pts` that is not UTF-8
    raises `UnicodeDecodeError` (a `ValueError`) out of `_read_points`: no `run_annual` handler
    catches a bare `ValueError`, and in `results` it would reach the generic `except ValueError`
    arm as an uncoded refusal blaming the annual result ("the annual result for grid ... could not
    be read"), so it is converted to the coded refusal here.

    **A second read of a file `run_annual` already read, deliberately.** `_dispatch`'s
    `_validate_project` reads the grid before this; passing those points down would widen
    `_analysis_area`'s signature for every reader (Task 6's `results` among them). This read is
    digest-checked against the same manifest record (`_read_points(..., record.files[0].digest)`),
    so the two reads cannot see different bytes without one of them refusing.
    """
    if record.zones is not None or not record.files:
        raise _AnalysisAreaNotDerivable(
            f"sensor grid {grid!r} has no single LM-83 point file to read a spacing from, so the "
            "LM-83-22 analysis area (the number of analysis points times the row and column "
            "spacings) cannot be derived. Create the grid with sensor_grid(action='create', "
            f"standard='lm83', name={grid!r}, polygon=[[x, y], ...])."
        )
    try:
        points, _text = _read_points(
            contain(record.files[0].path, current().roots), record.files[0].digest
        )
    except UnicodeDecodeError as exc:
        raise _AnalysisAreaNotDerivable(
            f"the point file of sensor grid {grid!r} is not UTF-8 text, so the LM-83-22 "
            "analysis area (the number of analysis points times the row and column spacings) "
            "cannot be read from it. Re-create the grid with sensor_grid(action='create', "
            f"standard='lm83', name={grid!r}, polygon=[[x, y], ...])."
        ) from exc
    try:
        dx, dy = grid_spacing_m([(p.x, p.y) for p in points])
    except ValueError as exc:
        raise _AnalysisAreaNotDerivable(
            f"sensor grid {grid!r} gives no LM-83-22 analysis area (the number of analysis points "
            f"times the row and column spacings): {exc} Re-create the grid with "
            f"sensor_grid(action='create', standard='lm83', name={grid!r}, polygon=[[x, y], ...])."
        ) from exc
    return len(points), dx, dy


def _lm83_expected_points_or_none(polygon, *, grid: str) -> int | None:
    """How many sensors LM-83's own placement would put inside `polygon`, or `None` if unknowable.

    `rad_core.grids.lm83_grid_point_count` with every refusal turned into `None`, for the reason
    `_grid_polygon_coverage_or_none` gives for its own: the analysis area came out of the polygon
    and must not be lost because a *second* question about that polygon has no answer. The refusals
    that can arrive here are real and reachable off a hand-edited manifest (R50 holds manifest
    content untrusted): a self-intersecting outline, a degenerate bounding box, an outline whose
    circumscribing rectangle is too large to grid at 0.6 m, and a **non-finite coordinate**.

    **That last one was not a refusal when this function first shipped -- it was a crash** (fix
    round 1, C1). `1e999` is valid JSON, parses to `inf`, and reached `_lm83_axis`'s `math.floor` as
    an `OverflowError`, which is an `ArithmeticError` and was in none of the exception families
    caught below, so the sentence above was false of the code beneath it. Two changes make it true
    and they are not redundant: `lm83_grid_point_count` now refuses a non-finite coordinate as a
    `PolygonError` at the layer that owns the polygon, and `ArithmeticError` joined the catch so
    that *"every refusal"* is a promise this function keeps rather than one it borrows from a
    callee's current arithmetic.

    **`None` is the availability-safe direction and the unsafe one for the defect being fixed, and
    that is a decision rather than a fall-through.** `_blind_schedule` refuses on a *measured*
    shortfall; an unmeasurable one lets the schedule through. The alternative -- refusing whenever
    the count is unknown -- would make an unreadable polygon fatal to an action whose area came from
    that same polygon and succeeded, and every route to `None` here is one that `polygon_area_m2`
    above has already declined to refuse on (it takes an area from a self-intersecting polygon
    quite happily). Refusing here on a state the area was derived from anyway would be the two
    functions disagreeing about the same polygon, which is this task's own defect in miniature.
    The unknown is not passed off as a measurement: `trigger.points_expected` publishes `null`.

    Note this reads **no file**: the polygon is manifest content and `lm83_grid_point_count` is pure
    arithmetic over it, so unlike the coverage this costs no syscall and cannot be defeated by an
    unreadable `.pts`.
    """
    try:
        return lm83_grid_point_count(polygon)
    except (GridError, ValueError, TypeError, IndexError, KeyError, ArithmeticError) as exc:
        # `GridError` covers `PolygonError` and `GridEmptyError`; the base `ValueError` stays in its
        # own catch for the reason `_analysis_area` records one clause up -- `PolygonError` is a
        # `ValueError` subclass, and a caller catching only the subclass does not catch the base.
        #
        # **`ArithmeticError` is here because its absence was a crash, not because a family looks
        # tidy** (fix round 1, C1). A polygon carrying `inf` -- `1e999` is valid JSON and parses to
        # it, and this polygon is manifest content R50 holds untrusted -- reached `_lm83_axis`'s
        # `math.floor` and raised `OverflowError`, which is an `ArithmeticError` and was in none of
        # the five families above. Nothing in `results_tool`, `runtime`, `server` or `envelope`
        # catches `Exception`, so it left the tool as `isError: true` with **no
        # `structuredContent` at all** -- the envelope shape check 0.4 forbids and ruling R38
        # already graded a violation. `lm83_grid_point_count` now refuses a non-finite coordinate as
        # a `PolygonError` at the layer that owns the polygon, which is the real fix; this clause
        # stays because the docstring's promise below is *"every refusal"*, and a promise held by a
        # callee's current translation of its own arithmetic is the shape
        # `_grid_polygon_coverage_or_none`'s missing `OSError` already recorded.
        logger.info(
            "results: grid %r LM-83 point count unavailable (%s); points_expected will be null",
            grid, exc,
        )
        return None


def _grid_polygon_coverage_or_none(record, polygon, *, grid: str) -> float | None:
    """What fraction of `polygon`'s area the grid's placed points sample, or `None` if unknowable.

    Everything that can go wrong reading a `.pts` returns `None` rather than raising: the analysis
    area came out of the manifest and is unaffected by an unreadable point file, so losing the
    coverage must not cost the area. `_grid_positions` makes the same trade for `xyz` and for the
    same reason. `None` and not `0.0`: publishing zero for "not known" would read as "this grid
    covers none of the room".

    **One route to `None` is arithmetic rather than filesystem**, and it arrived with the Task 17
    measure: a grid whose points occupy a single position supports no cell size on either axis, so
    `grid_polygon_coverage` raises `ValueError` rather than inventing one, and it lands in the
    `ValueError` arm of the clause below. That is the honest answer for a one-sensor grid -- an
    unknown fraction, not a zero one. Under the bounding-box measure it replaced, that grid read
    exactly 0.0, and so did every single-column grid. A non-finite polygon area takes the same
    route, for the same reason and by the same exception (ruling R200 leaves `polygon_area_m2`
    itself returning `nan`; what it must not do is become a confident `1.0` here).

    **`ValueError` in that tuple is therefore load-bearing, not defensive.** Remove it and the
    one-sensor grid raises out of `_analysis_area` and out of `results`; the caller-visible
    contract -- `coverage: null`, no `ANALYSIS_AREA_UNDER_COVERED`, the area still published -- is
    held by `tests/test_results_tool.py::TestTheAnalysisAreaComesFromTheRecordedPolygon::
    test_a_one_sensor_grid_publishes_a_null_coverage_and_no_under_coverage_advisory`, which goes
    red on exactly that deletion.
    """
    if record.zones is not None:
        logger.info("results: grid %r is a zoned EN 12464 grid; coverage will be null", grid)
        return None
    if not record.files:
        logger.info("results: grid %r has no registered files; coverage will be null", grid)
        return None
    try:
        # R50 -- the `.pts` path comes out of the manifest, so it is re-contained here rather than
        # trusted because it was contained when the grid was written.
        pts_path = contain(record.files[0].path, current().roots)
        points, _text = _read_points(pts_path, record.files[0].digest)
        return grid_polygon_coverage([(p.x, p.y) for p in points], polygon)
    except (
        GridUnreadableError, GridEmptyOnDiskError, PathOutsideRoot, RootsNotConfigured,
        PolygonError, ValueError, TypeError, IndexError,
        # **`OSError` was missing, and its absence was load-bearing on an accident.** Every
        # filesystem failure reaching here is converted by `_read_points` into
        # `GridUnreadableError`, so no `OSError` escapes it *today* -- which is exactly why the
        # omission was invisible. `contain()` on the line above raises neither, but the next thing
        # added to this `try` need not be so careful, and this whole function exists to guarantee
        # that losing the coverage never costs the area. A `try` whose stated contract is
        # "everything that can go wrong returns None" and whose clause list depends on a callee's
        # current translation is a contract held by a coincidence.
        OSError,
    ) as exc:
        logger.info("results: grid %r coverage unavailable (%s); coverage will be null", grid, exc)
        return None


def _grid_positions(state: _Resolved, *, grid: str) -> list[list[float]] | None:
    """The sensor positions §14's `xyz` publishes, or `None` if they cannot be trusted.

    **The coordinates live in a different file from the values.** The `.ill`'s rows are in the
    grid's point order, but the grid's `.pts` is a separate, mutable input -- `sensor_grid` can
    re-create it after the annual run. A chain-built `.ill` is then invalid (its `dsmx:<grid>` is
    built from the grid) and `_resolve` refuses it before this runs (R785); a result registered with
    no edge to the grid is not, so the count is still checked against the matrix's own `NROWS`
    before a single coordinate is published. *(Corrected 2026-10-04, Phase 19, R785: it said "and
    nothing invalidates the `.ill` when it does"; `rad_core.manifest.evaluate` does, for a
    chain-built `.ill`.)*

    **A mismatch returns `None` rather than a partial or a best-effort zip.** Labelling row *i* with
    whatever point *i* happens to be in a re-created grid is precisely the "valid file, plausible
    wrong answer" shape engine invariant 3 names: every number would look right and each would
    describe a different sensor than it claims. `null` says "not known", which a caller can act on;
    a wrong coordinate is one they cannot detect.

    The same answer for a grid that was deregistered or whose `.pts` no longer reads. None of those
    is an error worth refusing the whole result over -- the illuminance values are still correct and
    still the thing that was asked for -- so `xyz` is `null` and the values stand.
    """
    candidates = {grid, f"{SENSOR_GRID_INPUT_PREFIX}{grid}"}
    record = next(
        (
            r for r in state.loaded.manifest.inputs
            if r.kind == "sensor_grid" and r.id in candidates
        ),
        None,
    )
    if record is None or not record.files:
        logger.info("results: grid %r is not registered; xyz will be null", grid)
        return None
    if record.zones is not None:
        logger.info("results: grid %r is a zoned EN 12464 grid; xyz will be null", grid)
        return None

    try:
        pts_path = contain(record.files[0].path, current().roots)
        points, _text = _read_points(pts_path, record.files[0].digest)
    except (
        GridUnreadableError, GridEmptyOnDiskError, PathOutsideRoot, RootsNotConfigured,
        # Final review, P1 (pre-existing): a digest-matching `.pts` that is not UTF-8 raises
        # `UnicodeDecodeError` out of `_read_points`; uncaught, `results`' generic arm refused the
        # whole `points` call blaming the annual result. It is a `.pts` that cannot be read.
        UnicodeDecodeError,
    ) as exc:
        # The values are unaffected by an unreadable `.pts`, so this is a missing field rather than
        # a failed call -- and it is logged, because a silently absent coordinate set is the kind of
        # thing that reads as "this grid has no geometry" if nothing says otherwise.
        logger.info("results: grid %r positions unavailable (%s); xyz will be null", grid, exc)
        return None

    if len(points) != state.header.points:
        logger.info(
            "results: grid %r has %d points but the matrix has %d rows; xyz will be null",
            grid, len(points), state.header.points,
        )
        return None
    return [[p.x, p.y, p.z] for p in points]


def _points(
    state: _Resolved, *, project: str, grid: str, threshold_lux: float | None = None
) -> CallToolResult:
    """One annual aggregate per sensor, bounded on the point dimension.

    The per-point aggregates are built for **every** point as the matrix streams past; only the
    published list is cut. `statistics` is accumulated over the same full pass.

    **`hours_above_threshold` is counted through `rad_core.metrics.PointTally`, never by a
    comparison written here.** When `threshold_lux` is supplied, one `PointTally` per point is
    built against the **whole-year** window (`points` selects no period the way `compliance` does)
    and declared to track exactly `threshold_lux`; `observe` folds every hour in, and the published
    count is read back from `.result().hours_above[threshold_lux]` -- the tally's own strict `>`
    counter, matching the field's name. This is the same accumulator `_compliance_lm83` and
    `_compliance_en17037_provision` already build tallies with; nothing here re-derives what
    "above" means for a threshold a second time.
    """
    positions = _grid_positions(state, grid=grid)
    stats = _Accumulator(state.artifact_id)
    negative = _NegativeCount()
    views: list[PointView] = []
    whole_year = frozenset(range(state.header.hours)) if threshold_lux is not None else frozenset()
    rows = _lux_rows(
        state.path, artifact_id=state.artifact_id, expected_points=state.expected_points,
        content_digest=state.content_digest,
    )
    for index, lux in enumerate(rows):
        for value in lux:
            stats.add(value)
        negative.observe_row(lux)
        hours_above: int | None = None
        if threshold_lux is not None:
            tally = PointTally(whole_year, thresholds=(threshold_lux,))
            for hour, value in enumerate(lux):
                tally.observe(hour, value)
            hours_above = tally.result().hours_above[float(threshold_lux)]
        # An empty `lux` is unreachable here (a blank line is skipped by the row reader and the
        # row-count refusal comes first), so the `0.0` substituted below for an empty row is never
        # published; plan.md's Recorded limits row for N-28 and L-5 (R845) records that.
        mean = sum(lux) / len(lux) if lux else 0.0
        if not math.isfinite(mean):
            # R830: a row of finite cells can sum past a double even where the whole-grid total,
            # with negative cells before it, does not; refused as `_Accumulator.view` refuses.
            raise _overflowed_total(state.artifact_id, mean)
        views.append(PointView(
            id=index,
            xyz=positions[index] if positions is not None else None,
            mean=mean,
            min=min(lux) if lux else 0.0,
            max=max(lux) if lux else 0.0,
            hours_above_threshold=hours_above,
        ))

    # **`state.blend_advisories` first, so a stale blend is labelled on every action that
    # reads one** (ruling R191). Empty for every unblended result and for a blend whose
    # schedule still matches, so this changes no payload that was not already a blend.
    advisories: list[Advisory] = list(state.blend_advisories)
    if (warning := _negative_count_advisory(
        state, negative,
        tail=(
            "Each point's min and mean, its hours_above_threshold when one was asked for, and "
            "statistics include them as published."
        ),
    )) is not None:
        advisories.append(warning)
    inline = views[:MAX_INLINE_VALUES]
    if len(inline) < len(views):
        advisories.append(Advisory(
            code="RESULT_TRUNCATED",
            severity="warn",
            field="points",
            message=(
                f"`points` carries {len(inline)} of {len(views)} sensor points; tool results have "
                f"no pagination in MCP, so the list is bounded at {MAX_INLINE_VALUES}. "
                f"`n_points` reports the real count ({len(views)}), and `statistics` are computed "
                f"over all {len(views)} points and all {state.header.hours} hours, not over this "
                f"list. The complete matrix is the committed .ill at `path` ({state.path}), also "
                "linked as a resource on this result; results(action='export', format='csv') "
                "writes every value as text."
            ),
        ))

    result = ResultsPoints(
        project=project, grid=grid, artifact=state.artifact_id, path=str(state.path),
        method_used=state.method_used, shape=_shape_view(state.header), n_points=len(views),
        points=inline, statistics=stats.view(), advisories=advisories or None,
    )
    return _emit_with_resource_link(
        result, path=state.path, project=project, grid=grid,
        description=(
            f"The annual illuminance matrix: {state.header.points} sensor rows x "
            f"{state.header.hours} hourly values, in the {grid!r} grid's own point order."
        ),
    )


def _validate_selectors(
    *, point_ids: list[int] | None, hours: list[int] | None, header: AnnualMatrix
) -> CallToolResult | None:
    """Every `timeseries` selector refusal, or `None`.

    **The unbounded-request refusal is here**, and it is the module docstring's decision in code: a
    full year crosses `MAX_INLINE_VALUES` triples at three sensors, so an unselected `timeseries` is
    never inlineable and is refused rather than truncated to a number nobody asked for.

    **Duplicates are honoured rather than collapsed, and that is a choice.** `point_ids=[0, 0]`
    returns row 0 twice and counts as two against the bound. The alternative -- silently
    de-duplicating -- would return a result whose `values` length did not match the `point_ids` the
    caller passed, which is worse than the redundancy: a caller building a fixed-width table from
    their own selector list would have it silently shift. Echoing `point_ids` back makes what
    happened visible either way, and counting duplicates against the bound keeps the bound a
    statement about payload size, which is what it is for.

    **The `bool` check below is defence in depth and is unreachable from the wire.** Pydantic
    resolves `list[int]` at the schema boundary before this function runs, so `[true]` has already
    become `[1]` by here -- the same coercion ruling R40 closed for `quality`. It is kept for
    in-process callers and because a future widening of the published type would make it live.
    """
    if point_ids is None and hours is None:
        return _refuse_argument(
            f"results(action='timeseries') needs point_ids, hours, or both. This matrix is "
            f"{header.points} points x {header.hours} hours = {header.triples} values, and tool "
            f"results have no pagination in MCP, so anything past {MAX_INLINE_VALUES} values "
            "cannot be returned inline. Truncating would answer a question you did not ask -- the "
            "first N values of an annual matrix is neither a whole grid nor a whole period, and "
            "January reported as the year is the failure this refusal exists to prevent. Select "
            "what you need: point_ids=[0, 1, 2] for specific sensors, hours=[8, 9, 10] for "
            "specific hours of the year (0-based), or both. For whole-grid numbers use "
            "action='summary' or action='points'; for the complete matrix as text use "
            "action='export', format='csv'.",
            field="point_ids",
        )

    for name, selected, limit in (
        ("point_ids", point_ids, header.points), ("hours", hours, header.hours)
    ):
        if selected is None:
            continue
        if not isinstance(selected, list):
            return _refuse_argument(
                f"{name} must be a list of integers, not {type(selected).__name__}.", field=name
            )
        if not selected:
            return _refuse_argument(
                f"{name} is empty. Omit it to select all, or name the indices you want.",
                field=name,
            )
        for value in selected:
            if isinstance(value, bool) or not isinstance(value, int):
                return _refuse_argument(
                    f"{name} must contain integers; found {value!r}.", field=name
                )
            if not 0 <= value < limit:
                return _refuse_argument(
                    f"{name} contains {value}, which is outside this matrix's 0..{limit - 1} "
                    f"({limit} available, read from the .ill's own header). Indices are 0-based.",
                    field=name,
                )
    return None


def _timeseries(
    state: _Resolved, *, project: str, grid: str,
    point_ids: list[int] | None, hours: list[int] | None,
) -> CallToolResult:
    """Hourly lux for the selected points and hours.

    Selectors are validated against the **real** header before anything is read, so an out-of-range
    index is a refusal rather than a short list a caller might read as "that point had no data".
    """
    if (refusal := _validate_selectors(
        point_ids=point_ids, hours=hours, header=state.header
    )) is not None:
        return refusal

    wanted_points = point_ids if point_ids is not None else list(range(state.header.points))
    wanted_hours = hours if hours is not None else list(range(state.header.hours))

    # The selection is bounded too -- a caller may legitimately name 20 000 points and 8 760 hours,
    # which is the same explosion the unselected case was refused for. Checked before the read.
    selected = len(wanted_points) * len(wanted_hours)
    if selected > MAX_INLINE_VALUES:
        return _refuse_argument(
            f"the selection is {len(wanted_points)} point(s) x {len(wanted_hours)} hour(s) = "
            f"{selected} values, past the {MAX_INLINE_VALUES} a tool result can carry inline "
            "(tool results have no pagination in MCP). Narrow point_ids or hours, or use "
            "action='export', format='csv' to write the whole matrix to a file.",
            field="point_ids",
        )

    wanted = set(wanted_points)
    collected: dict[int, list[float]] = {}
    negative = _NegativeCount()
    rows = _lux_rows(
        state.path, artifact_id=state.artifact_id, expected_points=state.expected_points,
        content_digest=state.content_digest,
    )
    for index, lux in enumerate(rows):
        # Every row is streamed whatever the selection, so the count is over the whole matrix.
        negative.observe_row(lux)
        if index not in wanted:
            continue
        collected[index] = [lux[hour] for hour in wanted_hours]
    advisories = list(state.blend_advisories)
    if (warning := _negative_count_advisory(
        state, negative,
        tail=(
            "The count is over every cell of the matrix this action read, not only the values "
            "returned; any value below zero in values is one of them, as published."
        ),
    )) is not None:
        advisories.append(warning)

    return emit(ResultsTimeseries(
        project=project, grid=grid, artifact=state.artifact_id, path=str(state.path),
        method_used=state.method_used, shape=_shape_view(state.header), point_ids=wanted_points,
        hours=wanted_hours, values=[collected[point] for point in wanted_points],
        # `timeseries` and `export` carried no advisories at all before Phase 7d-1 Task 15a, and
        # `RadResult` omits the key entirely when it is `None`, so an unblended read is byte-for-
        # byte what it was. What is new is that a **stale blend** is labelled on every action that
        # reads one: the hours in `values` were each chosen by the schedule the header declares.
        # Since R814 a five-phase result's negative cells are labelled here too.
        advisories=advisories or None,
    ))


# --- LM-83 compliance -------------------------------------------------------------------------


#: The two metrics LM-83 §2.4 requires together. `metrics=` may name a subset to *ask* for, but a
#: subset missing either half is refused (`_validate_lm83_metrics`) rather than answered partially.
_LM83_METRICS: Final = frozenset({"sda", "ase"})

#: The one method that keeps direct sun as its own matrix (`chain.five_phase_steps`'s `Ssun`/`Sds`
#: split). ASE_1000,250h counts direct-sun hours; a 2- or 3-phase `.ill` has already smeared the sun
#: into the same daylight-coefficient matrix as everything else, at whatever `mf` `gendaymtx`
#: subdivided the sky to -- which is exactly what `ASE_FROM_SMEARED_SUN` refuses.
_LM83_ASE_REQUIRES_METHOD: Final = "5phase"


def _refuse_sub_annual_matrix(
    state: _Resolved, *, grid: str, standard: str, direct: tuple[str, int] | None = None,
    edition: Lm83Edition = DEFAULT_LM83_EDITION, metric: str = "ASE",
) -> CallToolResult:
    """`action="compliance"` against a matrix that is not a whole year: refused, **before any band
    or level statement is built**.

    **`direct` names the direct-sun matrix instead, when that is the one that is short** (Phase 7c
    Task 13): `(artifact_id, hours)`. R104's rule is about *the matrix a judged number is tallied
    from*, and once ASE comes from `illdir:<grid>` that matrix can be sub-annual while the chain
    result this `_Resolved` describes is a full year. One code, because what a client does about
    either is identical -- re-run over a full-year EPW -- and the message says which of the two
    matrices is short so the re-run is aimed at the right leg.

    **Refused rather than warned, and the reason is that a band over 48 hours is not a weaker
    answer -- it is a different quantity wearing the same name.** Both standards' thresholds are
    absolute annual counts that do not scale with the period
    (`rad_core.periods.is_whole_year`'s own docstring carries the arithmetic):

    - ASE_1000,250h counts hours `> 250` (ch04:52-54). Any window shorter than 251 hours makes
      `annual_sunlight_exposure` structurally `0.0`, and `rad_core.compliance.ase_band(0.0)` is
      `"clearly_acceptable"` -- a **guaranteed** favourable band from a period that never had the
      hours to earn an unfavourable one. Measured on a 48-hour `.ill`: `ase: 0.0`,
      `bands.ase: "clearly_acceptable"`, `hours_total: 20`.
    - EN 17037 mirrors it. `daylight_hours` retains half of whatever series it is given, so a
      48-record EPW yields `count=24, required=12` and a real-looking *"meets the medium
      recommendation level"* computed over two days.

    A `warn` beside a published `"clearly_acceptable"` would leave the favourable band on the wire
    for any client that reads the value and not the advisories -- and this branch has already
    shipped and fixed two members of exactly this family (an empty window reporting sDA = 100 %, a
    percentage compared against a fraction reporting a met level at 2 % of a grid). Refusing is
    the only answer that cannot be read as a verdict.

    **`ANALYSIS_PERIOD_NOT_ANNUAL` at `error`, allocated 2026-08-21 by ruling R104 (owner).**
    This refusal shipped code-less: the register was searched and allocated nothing naming "the
    analysis period is not a year", and the two near misses were both measured untrue of it rather
    than stretched. `MATRIX_SHAPE_MISMATCH` is a **drift** between a file and the record naming it,
    and nothing has drifted here -- the header, the manifest and the weather all agree with each
    other (`_ensure_header_matches_manifest` is the function whose condition that code does name).
    `ARGUMENT_OUT_OF_RANGE` is untrue of its own definition (R14: refused *before any work was
    attempted*; the project is open and the header read). The owner allocated a code rather than
    accept prose, and its name is true of the condition it fires on: the **period** the matrix
    covers is **not annual**, so no annual band and no annual level statement can be judged from
    it. It is an `error` because nothing is computed and nothing is returned.

    **Both standards emit it**, on the same condition and from this one function -- LM-83 through
    `_compliance_lm83` and EN 17037 through `_compliance_en17037_provision`. One code, because
    what a client does about either is identical: re-run over a full-year EPW, or read the matrix
    with an action that passes no judgement.

    **`summary`, `points`, `timeseries` and `export` are deliberately untouched by this.** They
    publish measured values over whatever period the matrix carries and pass no judgement on them;
    it is the *band* and the *level statement* that a sub-annual period makes false, so the
    refusal sits on `compliance` alone.

    `edition` (R864) names ASE's clause and the favourable verdict a short window guarantees:
    LM-83-12's "clearly acceptable" band, or LM-83-22's `below_level_of_concern`.
    """
    favourable = (
        "the 'below_level_of_concern' label" if edition == LM83_22
        else "the 'clearly acceptable' band"
    )
    if direct is None:
        subject, hours = "the matrix", state.header.hours
    else:
        direct_id, hours = direct
        subject = (
            f"the direct-sun matrix {direct_id!r} -- the one {metric} is tallied from "
            f"({section('ase_definition', edition)}), while sDA is tallied from "
            f"{state.artifact_id!r} --"
        )
    reason = (
        f"{subject} carries {hours} hourly column(s), which is not a whole year "
        f"({' or '.join(str(h) for h in sorted(WHOLE_YEAR_HOURS))} columns). Both LM-83's and EN "
        "17037's thresholds are annual by construction and do not scale with the period: ASE "
        "counts hours at 1 000 lux or more against an absolute 250, so any window shorter than 251 "
        f"hours reports ASE = 0 % and {favourable} no matter what the space does, "
        "and EN 17037's F_time denominator is half of whatever series it is given, so a two-day "
        "file yields a real-looking recommendation level computed over two days. A band or a "
        "recommendation level over a sub-annual matrix is a different quantity wearing the same "
        "name, so none is reported. run_annual over a full-year EPW produces a matrix this action "
        "can judge; results(action='summary'/'points'/'timeseries'/'export') still read this one."
    )
    message = (
        f"{standard} compliance for grid {grid!r} could not be computed: {reason}"
    )
    logger.info("results(action='compliance', standard=%r) refused: %s", standard, message)
    return refuse(
        message,
        [Advisory(code="ANALYSIS_PERIOD_NOT_ANNUAL", severity="error", message=message)],
    )


def _refuse_standard_deferred(standard: str) -> CallToolResult:
    """`compliance` with `standard="en12464"`: refused, naming `run_electric`, which computes EN
    12464-1 illuminance and zone uniformity on a zoned en12464 grid (Phase 11b R604) -- not this
    task's own name-and-a-reason refusals below, which are about
    `standard="en17037"` at `method="1"`/`axis="glare"`, arguments this same standard's `method="2"`
    daylight-provision axis is built for.
    """
    message = (
        f"results(action='compliance', standard={standard!r}) is refused: EN 12464-1 illuminance "
        "and zone uniformity are computed by run_electric on a zoned en12464 grid "
        "(sensor_grid(action='create', standard='en12464', task_zones=[...])), not by results. "
        "Available here: standard='lm83', or standard='en17037' with method='2' (daylight provision)."
    )
    return refuse(message)


# --- EN 17037, Method 2 (daylight provision) -------------------------------------------------


#: ch03 5.4/E.3.1: the sourced reference usage time for glare compliance, when a caller supplies
#: no `usage_hours` of its own. Named here only for the refusal message -- this task computes
#: nothing over it, since `axis="glare"` still refuses once `usage_hours` is present (below).
_EN17037_GLARE_REFERENCE_USAGE = "08:00-18:00, Monday to Friday (about 2 610 h/yr)"

#: The bound on the one `xform` read that derives the grid's window-group apertures for EN 17037's
#: opening class (`_en17037_opening_class`). A chosen value: docs/tool-surface.md, Unsourced values.
_APERTURE_READ_TIMEOUT_S: Final = 60.0


def _lighting_window_groups(
    loaded: LoadedProject, artifact_id: str, grid: str
) -> tuple[tuple[str, ...], tuple[str, ...]] | str:
    """`(lighting, dark)` from `_window_groups_by_v`, or a reason as a clause -- that function's,
    or, when no group lights `grid` at all, that every V matrix is all zero."""
    split = _window_groups_by_v(loaded, artifact_id, grid)
    if isinstance(split, str):
        return split
    lighting, dark = split
    if not lighting:
        return (
            f"every window group's V matrix to grid {grid!r} is all zero "
            f"({', '.join(dark)}), so no window group lights it"
        )
    return lighting, dark


def _window_groups_by_v(
    loaded: LoadedProject, artifact_id: str, grid: str
) -> tuple[tuple[str, ...], tuple[str, ...]] | str:
    """`(lighting, dark)`: the window groups whose V matrix to `grid` has a non-zero cell, and the
    ones whose V matrix is all zero -- or, when that cannot be said, the reason, as a clause.
    `lighting` may be empty.

    R813 (owner decision): a window group lights a grid when its V matrix for that grid is not all
    zero. The V matrices read are the `vmx:<g>:<grid>` artifacts the chain result `artifact_id` was
    built from, found by walking `built_from` (a three-phase sum reaches them through its per-group
    results, a five-phase result names one directly). `run_annual` traces a V matrix for every
    registered group on every grid, so a group behind opaque walls -- another room, another
    storey -- has one, all zero: measured on a two-storey box (2026-10-05, Radiance 6.1a,
    `standard` preset), the upper window's V from ground-floor points was 0 in all 3 915 cells and
    the ground window's was non-zero in 3 153 in one clock-seeded draw (a re-run measured 3 105).

    **Exact zero, `coefficient_matrix_is_all_zero`**, the test `ASE_DIRECT_MATRIX_EMPTY` already
    applies: "nothing at all", never "not much", so a group contributing little still lights the
    grid. The scan stops at the first non-zero cell; an all-zero matrix is read whole. A result
    built from no V matrix (a two-phase result) cannot say which groups light the grid."""
    found = _v_matrices_behind(loaded, artifact_id, grid)
    if not found:
        return (
            f"the result {artifact_id!r} was built from no V matrix to grid {grid!r} (a two-phase "
            "result), so which window groups light the grid could not be determined"
        )
    lighting: list[str] = []
    dark: list[str] = []
    for group, vmx in sorted(found.items()):
        if vmx is None:
            return f"the V matrix 'vmx:{group}:{grid}' is not registered"
        all_zero = _v_matrix_is_all_zero(vmx)
        if all_zero is None:
            return f"the V matrix {vmx.id!r} could not be read as a coefficient matrix"
        (dark if all_zero else lighting).append(group)
    return tuple(lighting), tuple(dark)


def _v_matrices_behind(loaded: LoadedProject, artifact_id: str, grid: str) -> dict[str, Any]:
    """`{group: the registered vmx:<group>:<grid> artifact, or None}` for every V matrix to `grid`
    the result `artifact_id` was built from, found by walking `built_from`; empty for a result built
    from none (a two-phase result)."""
    by_id = {artifact.id: artifact for artifact in loaded.manifest.artifacts}
    prefix, suffix = "vmx:", f":{grid}"
    seen: set[str] = set()
    stack = [artifact_id]
    found: dict[str, Any] = {}
    while stack:
        current_id = stack.pop()
        if current_id in seen:
            continue
        seen.add(current_id)
        group = current_id[len(prefix):-len(suffix)]
        if (current_id.startswith(prefix) and current_id.endswith(suffix) and group
                and ":" not in group):
            found[group] = by_id.get(current_id)
            continue
        artifact = by_id.get(current_id)
        if artifact is not None:
            stack.extend(artifact.built_from)
    return found


def _v_matrix_is_all_zero(vmx: Any) -> bool | None:
    """Whether the registered V matrix `vmx` is exactly zero in every cell, or `None` when it
    cannot be read as a coefficient matrix. The one place this module reads a V matrix.

    Contained before the read (R48/R50), as `_contained_matrix` contains an annual matrix; not
    through it, because a V matrix has no `NROWS` for its header cross-check to read
    (`coefficient_matrix_is_all_zero`'s docstring), and this read asks only about its cells."""
    try:
        return coefficient_matrix_is_all_zero(contain(vmx.path, current().roots))
    except (CoefficientMatrixUnreadableError, MatrixFileUnreadableError) as exc:
        logger.info("results: V matrix %r unreadable (%s)", vmx.id, exc)
        return None


def _dark_window_groups_advisory(
    loaded: LoadedProject, artifact_id: str, grid: str
) -> Advisory | None:
    """`WINDOW_GROUP_V_MATRIX_ALL_ZERO` (warn, R846) for each window group whose V matrix is all
    zero on every grid it was traced for AND whose every V record carries
    `grid_behind_aperture: true` (every point of that grid behind the group's room-facing plane,
    `rad_core.apertures.grid_behind_aperture`, recorded by `run_annual`), or `None`.

    Both halves, because each alone is wrong somewhere. An all-zero V alone is also a correctly
    wound window in another room, behind opaque walls: that room's pane has the grid in front of
    its plane in a convex building, records `false`, and says nothing. The facing alone is a
    prediction; the all-zero V is the measurement. A V record without the key (sealed before R846)
    says nothing. The groups tested are those `_window_groups_by_v` finds dark on `grid`, the scan
    R813 reads; every other registered `vmx:<group>:<grid>` is checked for the key first and then
    read by `_v_matrix_is_all_zero`; one lit, unreadable or without the key settles the group as
    not flagged. When this grid's own V matrices cannot be read, nothing is said.
    """
    split = _window_groups_by_v(loaded, artifact_id, grid)
    if isinstance(split, str):
        return None
    _lighting, dark = split
    flagged: list[str] = []
    for group in dark:
        prefix = f"vmx:{group}:"
        records = [
            artifact for artifact in loaded.manifest.artifacts
            if artifact.id.startswith(prefix) and artifact.id[len(prefix):]
            and ":" not in artifact.id[len(prefix):]
        ]
        if not all(
            (getattr(artifact, "parameters", None) or {}).get("grid_behind_aperture") is True
            for artifact in records
        ):
            continue
        if not all(_v_matrix_is_all_zero(artifact) is True
                   for artifact in records if artifact.id != f"{prefix}{grid}"):
            continue
        grids = sorted(artifact.id[len(prefix):] for artifact in records)
        label = "grid" if len(grids) == 1 else "grids"
        flagged.append(f"{group} ({label} {', '.join(grids)})")
    if not flagged:
        return None
    return Advisory(code="WINDOW_GROUP_V_MATRIX_ALL_ZERO", severity="warn", field=None, message=(
        f"window group(s) {'; '.join(flagged)}: the V matrix is exactly zero on every grid it was "
        "traced for, and every point of those grids lies behind the plane the group's "
        "outward_normal faces (window_group(action='define') reports it; it must point into the "
        "room), so the group contributes nothing to any annual result. The likely cause is a window "
        "polygon wound with its normal facing out of the room: reverse the order of its vertices "
        "and re-run run_annual. A window that really faces away from every grid, as across a "
        "courtyard, reads zero correctly."))


def _grid_reference_plane_z(loaded: LoadedProject, grid: str) -> float | None:
    """The z of `grid`'s reference plane: the highest z among the points of its registered
    `sensor_grid` record (every file, so a zoned grid's zones too), or `None` when the grid is not
    registered or its points cannot be read.

    `sensor_grid(action="create")` places every point at the floor's z plus one height, and
    `rad_core.grids.polygon_floor_z` refuses a floor whose vertices spread more than 1 mm, so a
    grid it wrote has one point height within 1 mm. A grid whose points differ more (hand-placed,
    or written before R763) is taken at its highest point, so an opening counts as above the plane
    only when it is above every point of it -- the literal reading of "entire area above the
    reference plane" against a plane that is not one height."""
    candidates = {grid, f"{SENSOR_GRID_INPUT_PREFIX}{grid}"}
    record = next(
        (r for r in loaded.manifest.inputs if r.kind == "sensor_grid" and r.id in candidates),
        None,
    )
    if record is None or not record.files:
        return None
    heights: list[float] = []
    try:
        for ref in record.files:
            points, _text = _read_points(contain(ref.path, current().roots), ref.digest)
            heights.extend(point.z for point in points)
    except (
        GridUnreadableError, GridEmptyOnDiskError, PathOutsideRoot, RootsNotConfigured,
        ValueError, OSError,
    ) as exc:
        logger.info("results: grid %r reference plane unavailable (%s)", grid, exc)
        return None
    finite = [z for z in heights if math.isfinite(z)]
    return max(finite) if finite and len(finite) == len(heights) else None


@dataclass(frozen=True)
class _OpeningClassFound:
    """`_en17037_opening_class`'s answer when it does not refuse."""

    opening_class: OpeningClass
    advisory: Advisory


def _window_group_name(record: Any) -> str:
    """A `window_group` input's own name: the `name` its `define` call recorded, else its input id."""
    call = record.registered_by
    name = call.arguments.get("name") if call is not None else None
    return name if isinstance(name, str) else record.id


def _opening_refusal(exc: Exception, *, code: str | None, field: str | None) -> CallToolResult:
    # `severity` is the literal "error" for every code passed here, `ApertureError`'s included
    # (`ApertureError.severity` is "error"), so `tests/test_advisories.py` can read it statically.
    logger.info("results(action='compliance', standard='en17037') refused: %s", exc)
    if code is None:
        return refuse(str(exc))
    return refuse(
        str(exc), [Advisory(code=code, severity="error", field=field, message=str(exc))]
    )


def _en17037_opening_class(
    state: _Resolved, *, grid: str
) -> _OpeningClassFound | CallToolResult:
    """Which EN 17037 table judges `grid`, by ch03's position rule: "An opening counts as
    horizontal if its entire area is above the reference plane".

    The window groups tested are the ones that light the grid: those whose V matrix to it, among
    the ones the chain result was built from, is not all zero (`_lighting_window_groups`). The
    reference plane is the grid's own point height (`_grid_reference_plane_z`). The apertures are
    derived from the registered geometry by `glare_tool._project_apertures` -- one `xform` spawn
    (two on a result built from no V matrix, below) after `discover()`'s probe, each bounded by
    `_APERTURE_READ_TIMEOUT_S`, and only once the project
    has a window group, the plane is known and some group lights the grid. Every lighting group
    wholly above the plane -> Table A.2; none -> Table A.1; both -> refused
    `EN17037_OPENINGS_MIXED`. No group, no readable grid points, no V matrix with a non-zero cell,
    or a lighting group whose modifiers resolve to no polygon leaves the class undetermined: Table
    A.1 is used and `EN17037_OPENING_CLASS` says so at `warn`.

    **A result built from no V matrix** (a two-phase result, the default route for
    `en17037_provision` with ordinary glazing; R853) cannot say which groups light the grid, so
    every registered group is tested. All wholly above the plane -> Table A.2: whichever of them
    lights the grid is wholly above it, and a grid none of them lights has no daylight and fails
    either table. None above -> Table A.1, stated at `info`. Some of each -> the class is
    undetermined (Table A.1 at `warn`), naming the groups on each side, because which of them light
    the grid decides it and a three-phase run (`method="3phase"`) records that. Both `info` answers
    hold only when every transmitting surface in the scene belongs to a window group: two-phase
    coefficients trace light through every one, so a transmitting modifier no group's `aperture`
    names (`_glazing_in_no_window_group`, a second `xform` spawn) leaves the class undetermined
    (Table A.1 at `warn`), naming it.
    """
    loaded = state.loaded
    records = [record for record in loaded.manifest.inputs if record.kind == "window_group"]
    if not records:
        return _OpeningClassFound("undetermined", _opening_class_undetermined(
            "no window group is registered in this project"
        ))
    plane_z = _grid_reference_plane_z(loaded, grid)
    if plane_z is None:
        return _OpeningClassFound("undetermined", _opening_class_undetermined(
            f"grid {grid!r} has no registered points this action can read, so there is no "
            "reference plane to test the window groups against"
        ))
    two_phase = not _v_matrices_behind(loaded, state.artifact_id, grid)
    if two_phase:
        # R853: no V matrix says which groups light the grid, so every registered group is tested.
        named: tuple[str, ...] = tuple(sorted(_window_group_name(record) for record in records))
        dark: tuple[str, ...] = ()
    else:
        lit = _lighting_window_groups(loaded, state.artifact_id, grid)
        if isinstance(lit, str):
            return _OpeningClassFound("undetermined", _opening_class_undetermined(lit))
        named, dark = lit
    try:
        env = discover()
        apertures = _project_apertures(
            loaded, env, loaded.paths.root, timeout=_APERTURE_READ_TIMEOUT_S, names=named,
        )
        names_by_id = {record.id: _window_group_name(record) for record in records}
        found = sorted(
            ((names_by_id.get(input_id, input_id), aperture) for input_id, aperture in apertures),
            key=lambda pair: pair[0],
        )
        missing = sorted(set(named) - {name for name, _ in found})
        # R853: the scene's glazing is read only when the class is still to be decided -- a group
        # resolving to no polygon leaves it undetermined already, so that spawn is not paid.
        unclaimed = (
            _glazing_in_no_window_group(loaded, env, records) if two_phase and not missing else ()
        )
    except RadianceNotFound as exc:
        return _opening_refusal(exc, code="RADIANCE_NOT_FOUND", field=None)
    except ApertureError as exc:
        return _opening_refusal(exc, code=exc.code, field="project")
    except SceneInlineCommandError as exc:
        return _opening_refusal(
            exc, code=getattr(exc, "code", "SCENE_INLINE_COMMAND"), field="project"
        )
    except SceneFileNotFoundError as exc:
        return _opening_refusal(exc, code=exc.code, field="project")
    except NotARegularFileError as exc:
        return _opening_refusal(exc, code="PROJECT_PATH_NOT_A_REGULAR_FILE", field="project")
    except RadianceError as exc:
        return _opening_refusal(exc, code=None, field=None)
    except OSError as exc:
        # A scene file the R33 scan could not open (a symlink, an unreadable file) -- neither this
        # function's other clauses nor `results`' outer chain names a bare `OSError`.
        return _opening_refusal(exc, code=None, field=None)

    if missing:
        return _OpeningClassFound("undetermined", _opening_class_undetermined(
            f"window group(s) {', '.join(missing)} resolve to no polygon in the registered "
            "geometry"
        ))

    listing = ", ".join(
        f"{name} (lowest vertex z = "
        f"{min(v[2] for polygon in aperture.polygons for v in polygon.vertices):.3f} m)"
        for name, aperture in found
    )
    which = f"the window group(s) whose V matrix to grid {grid!r} is not all zero" + (
        f" ({', '.join(dark)} "
        + ("has an all-zero V matrix and does not light it)" if len(dark) == 1
           else "have all-zero V matrices and do not light it)")
        if dark else ""
    )
    plane = (
        f"the reference plane at z = {plane_z:.3f} m, the highest point of grid {grid!r}"
    )
    if two_phase:
        return _two_phase_opening_class(state.artifact_id, grid, found, plane=plane, plane_z=plane_z,
                                        listing=listing, unclaimed=unclaimed)
    opening_class = en17037_opening_class([aperture for _, aperture in found], plane_z=plane_z)
    if opening_class == "mixed":
        message = (
            f"results(action='compliance', standard='en17037') cannot judge grid {grid!r}: of "
            f"{which}, some lie wholly above {plane}, and some do not -- {listing}. EN 17037 counts "
            "an opening as horizontal if its entire area is above the reference plane (Table "
            "A.2) and gives Table A.1 for the others, with no rule for a plane lit through both "
            "kinds, so no table is chosen. A window group whose V matrix to this grid is all zero "
            "(behind opaque walls or floors) is not counted, so only openings that light this "
            "grid decide it; a plane lit through both kinds is not judged here. Rooms joined by an "
            "opening share light, so an opening in a neighbouring room seen through a doorway "
            "counts as lighting this grid, and whether its V matrix has a non-zero cell can "
            "depend on the sampling preset."
        )
        logger.info("results(action='compliance', standard='en17037') refused: %s", message)
        return refuse(message, [Advisory(
            code="EN17037_OPENINGS_MIXED", severity="error", field="grid", message=message,
        )])
    table = (
        "Table A.2 (horizontal openings): the target illuminance over 95 % of the plane, with no "
        "minimum-target condition"
        if opening_class == "horizontal"
        else "Table A.1 (vertical and inclined openings): the target illuminance over 50 % of the "
        "plane and the minimum target over 95 %"
    )
    kind = "every one lies" if opening_class == "horizontal" else "none lies"
    return _OpeningClassFound(opening_class, Advisory(
        code="EN17037_OPENING_CLASS", severity="info",
        message=(
            f"opening class by EN 17037's rule that an opening counts as horizontal if its entire "
            f"area is above the reference plane, tested on {which} against {plane}: {listing}. "
            f"{kind.capitalize()} wholly above it (every vertex more than "
            f"{EN17037_ABOVE_PLANE_TOLERANCE_M:g} m above), so {table}."
        ),
    ))


def _glazing_in_no_window_group(loaded: LoadedProject, env: Any, records: Sequence[Any]) -> tuple[str, ...]:
    """R853: the transmitting modifiers in the registered scene that no window group's `aperture`
    names, sorted -- `rad_engine.scene.unclaimed_glazing_modifiers` over the materials and
    geometry files flattened through `xform` (one spawn, bounded by `_APERTURE_READ_TIMEOUT_S`),
    materials first, as `run_annual` compiles them. A two-phase result's daylight coefficients trace
    light through every one of them, in a window group or not."""
    files = [
        ref.path
        for kind in ("materials", "geometry")
        for record in loaded.manifest.inputs if record.kind == kind
        for ref in record.files
    ]
    if not files:
        return ()
    claimed: set[str] = set()
    for record in records:
        call = record.registered_by
        raw = call.arguments.get("aperture") if call is not None else None
        claimed.update((raw,) if isinstance(raw, str) else tuple(raw or ()))
    text = flatten_scene(env, files, cwd=loaded.paths.root, timeout=_APERTURE_READ_TIMEOUT_S).text
    return unclaimed_glazing_modifiers(text, claimed=claimed)


def _two_phase_opening_class(artifact_id: str, grid: str, found: Sequence[tuple[str, Any]], *, plane: str,
                             plane_z: float, listing: str,
                             unclaimed: Sequence[str] = ()) -> _OpeningClassFound:
    """R853: the opening class of a result built from no V matrix, from every registered window
    group (`_en17037_opening_class`'s docstring) -- when every transmitting modifier in the scene
    belongs to one (`unclaimed` empty); otherwise undetermined, naming them."""
    above = [name for name, aperture in found if en17037_opening_is_horizontal(aperture, plane_z=plane_z)]
    below = [name for name, aperture in found if name not in above]
    which = (
        f"the result {artifact_id!r} was built from no V matrix to grid {grid!r} (a two-phase result), "
        "so which window groups light the grid is not known, and every registered window group was "
        f"tested against {plane}: {listing}"
    )
    if unclaimed:
        return _OpeningClassFound("undetermined", Advisory(
            code="EN17037_OPENING_CLASS", severity="warn",
            message=(
                f"{which}. The scene also has transmitting surfaces whose modifier no window group "
                f"names: {', '.join(unclaimed)}. A two-phase result's daylight coefficients trace "
                "light through every transmitting surface in the scene, in a window group or not, so "
                "the opening class is not decided and Table A.1 (vertical openings) is assumed; for a "
                "plane lit only through openings wholly above it, Table A.2 is the stricter test. "
                "Define each of them that is an opening as a window group (window_group(action="
                "'define')); once every transmitting surface belongs to one, results decides the "
                "class from the groups. A transmitting surface that is not an opening (an interior "
                "partition, a neighbour's glazing) keeps this warning; run_annual with "
                "method='3phase' records which window groups light the grid, and results then tests "
                "only those."
            ),
        ))
    if above and below:
        return _OpeningClassFound("undetermined", Advisory(
            code="EN17037_OPENING_CLASS", severity="warn",
            message=(
                f"{which}. Wholly above it: {', '.join(above)}; not wholly above it: {', '.join(below)}. "
                "Which of them light the grid decides the table, so the opening class is not decided "
                "and Table A.1 (vertical openings) is assumed; for a plane lit only through openings "
                "wholly above it, Table A.2 is the stricter test. run_annual with method='3phase' "
                "records which window groups light the grid, and results then decides it."
            ),
        ))
    if above:
        return _OpeningClassFound("horizontal", Advisory(
            code="EN17037_OPENING_CLASS", severity="info",
            message=(
                f"{which}. Every one lies wholly above it (every vertex more than "
                f"{EN17037_ABOVE_PLANE_TOLERANCE_M:g} m above), so whichever of them lights the grid, "
                "Table A.2 (horizontal openings): the target illuminance over 95 % of the plane, with "
                "no minimum-target condition. A grid none of them lights has no daylight and fails "
                "either table."
            ),
        ))
    return _OpeningClassFound("vertical", Advisory(
        code="EN17037_OPENING_CLASS", severity="info",
        message=(
            f"{which}. None lies wholly above it, so whichever of them lights the grid, Table A.1 "
            "(vertical and inclined openings): the target illuminance over 50 % of the plane and the "
            "minimum target over 95 %."
        ),
    ))


def _opening_class_undetermined(reason: str) -> Advisory:
    return Advisory(
        code="EN17037_OPENING_CLASS", severity="warn",
        message=(
            f"{reason}, so the opening class was not tested and Table A.1 (vertical openings) is "
            "assumed; for a plane lit only through openings wholly above it, Table A.2 is the "
            "stricter test."
        ),
    )


def _refuse_en17037_method_1() -> CallToolResult:
    """`EN17037_METHOD_1_NOT_AVAILABLE`: Method 1 compares a daylight factor against Table A.3's
    D_T for a named city (ch03 §5.1.3), and `run_point_in_time` registers no artifact this action
    can read a daylight factor from -- there is nothing in the manifest Method 1 could compute
    over. Only Method 2, the climate-based route `run_annual`'s matrix actually feeds, is built.
    """
    message = (
        "results(action='compliance', standard='en17037', method='1') is not available -- Method "
        "1 compares a daylight factor against Table A.3's D_T for a named city, and "
        "run_point_in_time registers no artifact this action can read a daylight factor from. "
        "Pass method='2': the climate-based route run_annual's matrix actually feeds."
    )
    return refuse(
        message,
        [
            Advisory(
                code="EN17037_METHOD_1_NOT_AVAILABLE", severity="error", field="method",
                message=message,
            )
        ],
    )


def _refuse_en17037_usage_hours_required() -> CallToolResult:
    """`EN17037_USAGE_HOURS_REQUIRED`: `axis="glare"` with no `usage_hours`. ch03's glare
    criterion (F_DGP,exceed <= 5%) is scoped to **usage time**, an occupancy input with no
    defensible default -- a substituted one would move the 5% exceedance criterion by whatever the
    substitution was wrong by (docs/tool-surface.md's own periods table)."""
    message = (
        "results(action='compliance', standard='en17037', axis='glare') requires usage_hours -- "
        "there is no defensible default occupancy for EN 17037's glare criterion (F_DGP,exceed <= "
        "5% of usage time), and a substituted one would move that 5% by whatever the substitution "
        f"was wrong by. The sourced reference usage time is {_EN17037_GLARE_REFERENCE_USAGE} "
        "(ch03 section 5.4/E.3.1)."
    )
    return refuse(
        message,
        [
            Advisory(
                code="EN17037_USAGE_HOURS_REQUIRED", severity="error", field="usage_hours",
                message=message,
            )
        ],
    )


def _refuse_en17037_glare_not_built() -> CallToolResult:
    """`axis="glare"` with `usage_hours` supplied: the argument is answerable, but glare itself is
    not built this task -- it needs the image-based annual DGP matrix (`ANNUAL_DGP_COST`,
    docs/tool-surface.md), which this phase does not compute. Code-less: the register allocates no
    code for "this axis exists but is not built" -- the posture `annual_tool.WeatherNotLoadedError`
    and `weather_tool._unknown_name` already ship, and the one `_refuse_deferred` took for
    `blind_schedule` until Phase 7c Task 16 built that action and deleted the function.
    """
    message = (
        "results(action='compliance', standard='en17037', axis='glare') is not built -- glare "
        "needs an annual DGP, and annual DGP is not built. Available "
        "today: axis='provision' (the default), method='2'."
    )
    return refuse(message)


def _validate_en17037_arguments(
    *, axis: str | None, method: str | None, level: str | None, usage_hours: dict[str, Any] | None,
) -> CallToolResult | None:
    """Every `standard="en17037"` argument refusal, or `None`. Pure -- no project is opened and no
    file is read to answer any of these, so this runs before `_resolve`/`current()` (R39's
    placement), matching `_validate_lm83_metrics`'s own posture.

    `axis` selects which of EN 17037's four independent criteria (ch03:9-11) this call asks
    about: `"provision"` (the default, this task's only computed axis) or `"glare"`, which this
    task does not compute but still validates `usage_hours` for -- closing the allocated
    `EN17037_USAGE_HOURS_REQUIRED` code rather than leaving it reachable only once glare itself is
    built.
    """
    if axis == "glare":
        if usage_hours is None:
            return _refuse_en17037_usage_hours_required()
        return _refuse_en17037_glare_not_built()
    if axis not in (None, "provision"):
        return _refuse_argument(
            f"axis={axis!r} is not one of 'provision', 'glare'.", field="axis",
        )
    if level not in ("minimum", "medium", "high"):
        return _refuse_argument(
            "results(action='compliance', standard='en17037') requires level -- one of 'minimum', "
            f"'medium', 'high', not {level!r}.",
            field="level",
        )
    if method not in ("1", "2"):
        return _refuse_argument(
            "results(action='compliance', standard='en17037') requires method -- '1' or '2', not "
            f"{method!r}.",
            field="method",
        )
    if method == "1":
        return _refuse_en17037_method_1()
    return None


def _refuse_en17037_epw_unavailable(*, grid: str, reason: str) -> CallToolResult:
    """`EN17037_DAYLIGHT_HOURS_UNAVAILABLE`: the one code every EPW-shaped failure in
    `_compliance_en17037_provision` maps to, whatever stage failed. `_resolve_epw`'s own three
    named cases (the file is gone, its digest has drifted, no weather input is found at all), a
    matrix/EPW hour-count mismatch, an EPW with no usable Diffuse Horizontal Illuminance column,
    and `rad_core.periods.daylight_hours`/`rad_core.metrics.f_plane`'s own `ValueError` on an
    empty denominator all reach here -- the practical consequence is identical in every case (there
    is no trustworthy daylight-hours denominator to report a percentage against), even though the
    proximate cause differs, so `reason` carries the specific stage and the code stays one already-
    allocated value rather than a family of new ones this task may not invent (the carried ruling).
    """
    message = (
        f"EN 17037 daylight provision for grid {grid!r} could not be computed: {reason}. The "
        "F_time denominator (ch03 B.3.3/B.4) is derived by rank-ordering the year's hourly diffuse "
        "horizontal illuminance -- without a trustworthy EPW there is nothing to rank."
    )
    logger.info("results(action='compliance', standard='en17037') refused: %s", message)
    return refuse(
        message,
        [
            Advisory(
                code="EN17037_DAYLIGHT_HOURS_UNAVAILABLE", severity="error", message=message
            )
        ],
    )


def _refuse_en17037_no_points(state: _Resolved, *, grid: str) -> CallToolResult:
    """No register code names "no analysis area" (the same gap `_refuse_empty_lm83_denominator`
    documents on the LM-83 side) -- code-less, matching that function's posture exactly."""
    message = (
        f"EN 17037 daylight provision for grid {grid!r} could not be computed: the matrix carries "
        f"{state.header.points} sensor points -- there is no reference plane to report a "
        "fraction of points over, so there is no denominator and therefore no proportion to "
        "report."
    )
    logger.info("results(action='compliance', standard='en17037') refused: %s", message)
    return refuse(message)


def _compliance_en17037_provision(
    state: _Resolved, *, project: str, grid: str, level: str
) -> CallToolResult | ResultsComplianceEn17037:
    """`action="compliance"`, `standard="en17037"`, `method="2"`: EN 17037's daylight-provision
    axis. **Returns the result model on success and a shaped `CallToolResult` on a refusal**, for
    `_compliance_lm83`'s own stated reason (Phase 7c Task 17). Computed
    from `rad_core.periods.daylight_hours`'s denominator and `rad_core.metrics.f_plane`'s
    tallies, judged by `rad_core.compliance.en17037_provision`/`en17037_object`. No arithmetic
    lives here -- this resolves the EPW (`_resolve_epw`, the one walk `_site_latitude` also uses),
    builds the per-point `PointTally` objects the matrix streams into, and shapes the result and
    its advisories, the same division of labour `_compliance_lm83` keeps for LM-83.

    Called only once `_validate_en17037_arguments` has already accepted `level`/`method` -- by the
    time control reaches here, `level` is one of `"minimum"/"medium"/"high"` and `method == "2"`.

    **The whole-year guard runs first, above even the EPW walk.** It is knowable from the header
    alone, and `_refuse_sub_annual_matrix`'s own docstring gives the defect: F_time is half of
    whatever series it is handed, so a two-day file produces a real-looking recommendation level
    rather than an error. Placed above `_resolve_epw` so a matrix that can never be judged does not
    first pay for a 1.5 MB weather read.
    """
    # Final fix E1: a result built from other window groups than the project registers is refused.
    refusal = _refuse_window_groups_not_current(
        state, project=project, grid=grid, reads="compliance",
    )
    if refusal is not None:
        return refusal
    if not is_whole_year(state.header.hours):
        return _refuse_sub_annual_matrix(state, grid=grid, standard="EN 17037")

    parsed, reason = _resolve_epw(state)
    if parsed is None:
        return _refuse_en17037_epw_unavailable(grid=grid, reason=reason or "unknown")

    diffuse_lux: list[float | None] = [
        record.diffuse_horizontal_lux for record in parsed.records
    ]
    if len(diffuse_lux) != state.header.hours:
        return _refuse_en17037_epw_unavailable(
            grid=grid,
            reason=(
                f"the EPW carries {len(diffuse_lux)} hourly record(s) and the matrix carries "
                f"{state.header.hours} column(s) -- an hour of the EPW must name the same hour as "
                "a column of the matrix for the daylight-hours denominator to mean anything "
                "against this grid's own illuminance"
            ),
        )

    # **The gate `rad_core.periods.daylight_hours` alone does not provide.** A `None` in the
    # series is caught below by that function's own `ValueError` (the carried ruling); an
    # all-zero series is not -- every hour ties at 0.0, `daylight_hours` returns a real-looking
    # `DaylightHours` whose entire 4 380-hour retained set was decided by the earliest-hour
    # tie-break rather than by any real ranking. `rad_core.weather.EPWDataQuality.
    # illuminance_present` is the one place this "all-zero means absent" rule is decided
    # (coordinator review, Task 13: it used to be re-derived here, beside its sibling
    # `albedo_present`'s identical rule for a different field, one layer up from where that
    # sibling actually lives) -- `parsed.data_quality` already computed it from the same
    # `records` this function reads `diffuse_horizontal_lux` out of, so this is a read, not a
    # second, independent copy of the rule.
    if not parsed.data_quality.illuminance_present:
        return _refuse_en17037_epw_unavailable(
            grid=grid,
            reason=(
                "the EPW's Diffuse Horizontal Illuminance column (field 19) carries no usable "
                "value -- every hour is missing, at or above the 999900 missing-data floor, or "
                "exactly zero for the whole year, which is not a physically plausible measurement "
                "and would let the earliest-hour tie-break alone decide the entire retained set "
                "rather than a real ranking"
            ),
        )

    if state.header.points == 0:
        return _refuse_en17037_no_points(state, grid=grid)

    try:
        dh = daylight_hours(diffuse_lux)
    except ValueError as exc:
        # **The carried ruling.** `rad_core.periods.daylight_hours` raises `ValueError` on a
        # `None` in the series -- the gate above already refuses the all-`None` case, but a
        # partially-missing series (some real hours, some `None`) reaches here instead, and this
        # is defence in depth for it rather than the only guard standing between it and a stack
        # trace on the wire.
        return _refuse_en17037_epw_unavailable(grid=grid, reason=str(exc))

    opening = _en17037_opening_class(state, grid=grid)
    if isinstance(opening, CallToolResult):
        return opening
    table = "horizontal" if opening.opening_class == "horizontal" else "vertical"
    target_lux, minimum_lux = en17037_thresholds(level, table)
    thresholds = (target_lux,) if minimum_lux is None else (target_lux, minimum_lux)
    tallies = [
        PointTally(dh.columns, thresholds=thresholds) for _ in range(state.header.points)
    ]
    negative_cells = _NegativeCount({"daylight": dh.columns})
    rows = _lux_rows(
        state.path, artifact_id=state.artifact_id, expected_points=state.expected_points,
        content_digest=state.content_digest,
    )
    for index, lux in enumerate(rows):
        tally = tallies[index]
        for hour, value in enumerate(lux):
            tally.observe(hour, value)
        negative_cells.observe_row(lux)

    try:
        f_target = f_plane(
            tallies, target_lux=target_lux, retained=dh.columns, required=dh.required
        )
        f_minimum = None if minimum_lux is None else f_plane(
            tallies, target_lux=minimum_lux, retained=dh.columns, required=dh.required
        )
    except ValueError as exc:
        # Defence in depth, matching `_compliance_lm83`'s own posture: the `points == 0` guard
        # above makes `f_plane`'s empty-`tallies` case unreachable here, and the mismatch/`usable`
        # gates make its empty-`retained` case likewise unreachable -- kept in case `rad_core`
        # changes what it treats as an empty denominator, rather than trusting today's guards to
        # stay exhaustive forever.
        return _refuse_en17037_epw_unavailable(grid=grid, reason=str(exc))

    provision = en17037_provision(f_target, f_minimum, level=level, opening_class=table)
    en17037 = en17037_object(provision)
    # R814: F_plane is an hour count against a threshold over the same matrix, so a five-phase
    # result's negative cells are labelled here as they are on LM-83's sDA. The count is
    # whole-year and F_plane counts only the retained daylight hours, so the tail says how many of
    # the cells are inside them.
    negative = _negative_count_advisory(
        state, negative_cells,
        tail=(
            f"Inside the {dh.count} retained daylight hours F_plane is counted over: "
            f"{negative_cells.in_window['daylight']} of them, below every target as published; "
            "the rest are in no F_plane fraction."
        ),
    )

    # **`state.blend_advisories` first, so a stale blend is labelled on every action that reads
    # one** (ruling R191). Empty for every unblended result and for a blend whose schedule still
    # matches, so this changes no payload that was not already a blend. EN 17037 has no
    # blind-schedule concept of its own -- what the advisory says here is what it says everywhere:
    # the hours this verdict was computed over were each chosen by a schedule the project no
    # longer holds.
    advisories: list[Advisory] = [
        *state.blend_advisories,
        Advisory(
            code="EN17037_INFORMATIVE", severity="info",
            message=(
                "EN 17037 Annex A is informative, not normative (ch03: 'Clause 5 is normative; "
                f"Annex A is informative') -- this result states whether the space meets the "
                f"{level} recommendation level, never 'passes', and carries no pass key."
            ),
        ),
        opening.advisory,
        Advisory(
            code="EN17037_CRITERIA_NOT_ASSESSED", severity="warn",
            message=(
                "view_out, sunlight_exposure and glare are reported assessed: false -- this "
                "action computes daylight_provision only, ch03 states there is no single "
                "composite score across the four independent axes (ch03:9-11), and overall is "
                "structurally null."
            ),
        ),
        Advisory(
            code="METRICS_NOT_CONVERTIBLE", severity="warn",
            message=(
                f"this run's F_time denominator is EN 17037's daylight hours ({dh.count} h this "
                "year, climate- and site-dependent) -- not LM-83's fixed 08:00-18:00, 3 650 h/yr "
                "window sDA and ASE use. The same annual matrix can answer both standards, but "
                "the two percentages are not convertible into each other; analysis.period states "
                "which period this result used."
            ),
        ),
        Advisory(
            # **`warn`, not `info` (coordinator review, Task 13).** This is not a passive fact --
            # it names a bias that moves the headline F_plane number, the same posture
            # `EN17037_CRITERIA_NOT_ASSESSED` already takes for the axes this action skips.
            code="EN17037_PERIMETER_BAND", severity="warn",
            message=(
                "ch03 B.2 excludes a 0,5 m perimeter band along the walls from the calculation "
                "grid. This action reads whatever grid run_annual's matrix carries and has no "
                "room-boundary geometry of its own to apply that exclusion against, so it was NOT "
                "applied here -- a grid that still includes its perimeter points reports a "
                "F_plane biased low relative to what the standard's own grid would give, "
                "especially in small rooms."
            ),
        ),
    ]
    if negative is not None:
        advisories.append(negative)
    if dh.tied_at_cut > 1:
        advisories.append(Advisory(
            code="EN17037_DAYLIGHT_HOURS_TIE_BROKEN", severity="info",
            message=(
                f"{dh.tied_at_cut} hours carry the same diffuse horizontal illuminance "
                f"({dh.cut_value:g} lux) as the rank-order cut that decides the retained daylight "
                "hours -- ch03 B.3.3/B.4 says nothing about ties, so this result breaks the tie "
                "earliest-hour-of-year-first (rad_core.periods.daylight_hours) and states the "
                "count and the rule rather than leaving it implicit."
            ),
        ))

    analysis = En17037AnalysisView(
        period="daylight hours (top half of the year's ranked diffuse horizontal illuminance)",
        hours_total=dh.count,
        # `float(...)`, explicit rather than relying on pydantic's int->float coercion at the
        # boundary (coordinator review, Task 13) -- `dh.required` is `count // 2`, a genuine
        # Python `int` (unlike LM-83's `hours_required`, which is `float * int` and therefore
        # already a `float` by construction with no cast needed). Matching the field's own type
        # deliberately here, the same posture `_compliance_lm83`'s own comment takes for its
        # sibling field.
        hours_required=float(dh.required),
        points_total=state.header.points,
        points_excluded=0,
        exclusion_reason=None,
    )
    return ResultsComplianceEn17037(
        project=project, grid=grid, artifact=state.artifact_id, path=str(state.path),
        method_used=state.method_used, en17037=en17037, analysis=analysis, advisories=advisories,
    )


def _refuse_sda_alone(
    missing: str, metrics: list[str], *, edition: Lm83Edition = DEFAULT_LM83_EDITION
) -> CallToolResult:
    """`SDA_REPORTED_ALONE`: `metrics` was given and omits one of the required pair.

    ch04 §2.4: annual values **shall** be reported for both sDA and ASE. The register allocates one
    code for this condition, worded from sDA's side (`"sDA requested without ASE"`); it is reused
    for the pair's other incomplete direction too (`metrics=["ase"]`, omitting `sda`) rather than
    inventing a second code the register does not have -- `missing` always names which half is
    absent, so the message is accurate either way even though the code's name is not symmetric.

    R864: the clause is `edition`'s -- LM-83-12 section 2.4, or LM-83-22 section 4.14, whose
    "Annual summary values ... shall be reported for both sDA and ASE so that they can be
    considered together" is the same rule (it said "LM-83 ch04 section 2.4").
    """
    message = (
        f"results(action='compliance', standard='lm83', metrics={metrics!r}) omits {missing!r}. "
        f"{section('reporting', edition)}: annual values shall be reported for both sDA and ASE, "
        "together or not at all -- there is no legitimate reading in which one is published "
        "without the other. Pass metrics=['sda', 'ase'], or omit metrics entirely to get both."
    )
    return refuse(
        message,
        [
            Advisory(
                code="SDA_REPORTED_ALONE", severity="error", field="metrics", message=message
            )
        ],
    )


def _validate_lm83_metrics(
    metrics: list[str] | None, *, edition: Lm83Edition = DEFAULT_LM83_EDITION
) -> CallToolResult | None:
    """Every `metrics` refusal for `standard="lm83"`, or `None`. Pure argument validation -- no
    project is opened and no file is read to answer this, so it runs before `_resolve`/`current()`
    (R39's placement: as early as a refusal can fire)."""
    if metrics is None:
        return None
    if not isinstance(metrics, list) or not metrics:
        return _refuse_argument(
            f"metrics must be a non-empty list drawn from {sorted(_LM83_METRICS)}, not {metrics!r}.",
            field="metrics",
        )
    unknown = sorted({m for m in metrics if m not in _LM83_METRICS})
    if unknown:
        return _refuse_argument(
            f"metrics names {unknown}, outside LM-83's {sorted(_LM83_METRICS)}.", field="metrics",
        )
    missing = sorted(_LM83_METRICS - set(metrics))
    if missing:
        return _refuse_sda_alone(missing[0], metrics, edition=edition)
    return None


def _refuse_ase_smeared(
    state: _Resolved, *, grid: str, edition: Lm83Edition = DEFAULT_LM83_EDITION
) -> CallToolResult:
    """`ASE_FROM_SMEARED_SUN`: **the matrix this call would have tallied ASE from smeared the solar
    disc into its daylight-coefficient sky**, so there is no isolated direct-sun term to count.

    **Widened in Phase 7c Task 13 -- the row's condition is now a property of the matrix, not of
    the chain.** Until this task the check was `state.method_used != "5phase"` and nothing else,
    which was a true statement about 7b (the only matrix ASE could be tallied from was the chain
    result, and only the five-phase chain keeps `Ssun` separate). Task 11 made `illdir:<grid>` --
    a dedicated `-ab 0`, sun-only run -- reachable on **two- and three-phase** runs as well, and
    `_compliance_lm83` now tallies ASE from it whenever one is registered. A three-phase project
    holding a valid `illdir:` therefore reports a correct, standard-shaped ASE, and refusing it on
    `method_used` would be refusing a number that is right. The row is **widened, not
    re-allocated**: same condition in substance -- the ASE number must not be read off a matrix
    that smeared the sun -- said about the matrix it is actually read off.

    Reached from exactly one state now: **no `illdir:<grid>` is registered and the chain result is
    not five-phase.** With a five-phase chain result and no direct leg, 7b's behaviour is unchanged
    and the number ships with `ASE_NOT_FROM_DIRECT_ONLY_RUN` attached (ruling R108) rather than
    being refused -- narrowing that to a refusal here would delete that advisory's only reachable
    condition and withdraw a published capability.

    Checked from the manifest and `state.method_used` alone -- no tally is built, and the only file
    touched is the header `_resolve` already read -- so this is refused before the action pays for
    a streaming pass over a matrix whose ASE it was never going to be allowed to publish.

    **The message names the shadowed five-phase result when one is registered, and that closes a
    false reason** (whole-branch review, composition lens). `run_annual(metrics=['da'])` followed
    by `run_annual(metrics=['sda','ase'])` on one grid leaves a three-phase `ill:` beside a
    five-phase `ill5:` -- and this refusal then said *"ASE can only be reported from
    method_used='5phase'"* to a caller who **has** one, for ever, with nothing naming it. What was
    wrong was that the refusal read as "you have no five-phase result" when the truth was "you have
    one and this call did not read it". `state.shadowed_ids` carries every other candidate, so the
    message states the fact and the recovery instead of a reason that is not true of the project.

    **The five-phase test is over the whole set as of Phase 7d-1 Task 15a (ruling R187), and the
    singular form it replaces was a live false-negative.** `shadowed_id` held *one arbitrary
    element* of that set; the moment `illblend:<grid>` joined `_ARTIFACT_PREFIXES` a grid could
    hold three candidates, and a `chain="3phase"` call on a project holding `ill:`, `ill5:` and
    `illblend:` had a `shadowed_ids` of two -- so `startswith("ill5:")` on whichever came first
    answered *"no five-phase result is registered"* to a caller who has one, which is the exact
    false reason the paragraph above records this refusal shipping once already.

    **Still reachable after ruling R105, and this is the call that reaches it**: `chain="3phase"`
    against a grid that also holds an `ill5:`. R105 refuses the *unselected* call, not this one --
    a caller may legitimately ask for the three-phase result's sDA, and LM-83 §2.4 then requires
    ASE alongside it, which that matrix cannot supply. The recovery sentence names `chain='5phase'`
    because that is now one argument away rather than a re-seal of the project.
    """
    five_phase = next(
        (other for other in state.shadowed_ids if other.startswith("ill5:")), None
    )
    also_registered = (
        # Final fix E, fix round 1 (review I3, linked Minor): a five-phase result built from other
        # window groups than the project registers is refused WINDOW_GROUPS_NOT_CURRENT, so it is
        # not offered as the read that reports ASE.
        f" A five-phase result is registered for this grid, under {five_phase!r}, but it was "
        "built from other window groups than the project registers now, so results refuses it "
        "(WINDOW_GROUPS_NOT_CURRENT) and it reports no ASE either."
        if five_phase is not None and _groups_not_current(state.loaded, five_phase)
        else f" A five-phase result IS registered for this grid, under {five_phase!r} -- this "
        f"call read {state.artifact_id!r} instead. Pass chain='5phase' to read the five-phase "
        "matrix you already have, and ASE is reported from it."
        if five_phase is not None
        else ""
    )
    message = (
        f"results(action='compliance', standard='lm83') for grid {grid!r} would report ASE from a "
        f"method_used={state.method_used!r} result. {ase_label(edition)} is a direct-sun hour "
        "count, and "
        "only the five-phase chain keeps the direct-sun matrix (Ssun) separate from the diffuse "
        "sky -- a 2- or 3-phase .ill has already smeared the sun into the same daylight-coefficient "
        "matrix as everything else, at gendaymtx's own sky subdivision. This project registers no "
        f"'illdir:{grid}' either, so there is no direct-only matrix to tally instead. ASE can be "
        f"reported from a method_used={_LM83_ASE_REQUIRES_METHOD!r} result, or from any chain once "
        "run_annual has built the dedicated zero-bounce direct-sun leg for this grid "
        f"(metrics=['sda','ase'] or blind_control='lm83' plans it and seals 'illdir:{grid}')."
        f"{also_registered}"
    )
    return refuse(
        message,
        [Advisory(code="ASE_FROM_SMEARED_SUN", severity="error", message=message)],
    )


def _refuse_direct_shape_disagrees(
    state: _Resolved, *, grid: str, direct_id: str, direct: AnnualMatrix,
    edition: Lm83Edition = DEFAULT_LM83_EDITION, metric: str = "ASE",
) -> CallToolResult:
    """`ASE_DIRECT_SHAPE_DISAGREES`: the direct-sun matrix and the chain result do not declare the
    same `NROWS`/`NCOLS`, so sDA and ASE would be tallied over different grids or different periods
    and published side by side under one `analysis` block.

    **Allocated Phase 7c Task 13**, in the commit that first tallies ASE from a second matrix. The
    register was searched before allocating and none of the three near misses is true of this
    condition:

    - `MATRIX_SHAPE_MISMATCH` is a **drift between one file and the record that names it**, and
      `_contained_matrix` has already applied it to *both* matrices by the time this runs. Here
      neither file has drifted from its own record; two records that are each internally true
      disagree with **each other**, which is a different fact and has a different repair.
    - `ANALYSIS_PERIOD_NOT_ANNUAL` is about a matrix not spanning a year. Both of these can span a
      full year and still disagree -- 8 760 against 8 784 is two whole years of different length,
      and 455 points against 456 is not about the period at all.
    - `ANNUAL_RESULT_AMBIGUOUS` is *two answers and no instruction which to publish*; here there is
      exactly one answer per metric and the instruction is unambiguous. What is wrong is that the
      two answers do not describe the same space.

    `error`, because nothing can be computed: `analysis.points_total` is a single integer and could
    only ever describe one of the two grids, so any payload built from this pair would label one
    metric with the other's denominator. The repair is a caller action -- re-run `run_annual` for
    the grid so both legs are sealed from one sensor grid and one weather file.

    **Refused above every allocation sized from either header** (R39): `lm83_window` builds a
    frozenset over `range(hours)` and the tally lists are one `PointTally` per point, so both
    integers are allocation inputs and neither is trusted until this has passed.
    """
    message = (
        f"results(action='compliance', standard='lm83') for grid {grid!r} would tally sDA and "
        f"{metric} over two matrices of different shape: {state.artifact_id!r} declares NROWS="
        f"{state.header.points} x NCOLS={state.header.hours} and the direct-sun matrix "
        f"{direct_id!r} declares NROWS={direct.points} x NCOLS={direct.hours}. sDA is tallied from "
        f"the first and {metric} from the second ({section('ase_definition', edition)}: ASE is the "
        "direct beam from the solar orb alone, zero-bounce), so a disagreement means the two "
        "percentages describe "
        "different sensor grids or different periods while being published under one analysis "
        "block whose points_total and hours_total can only describe one of them. Neither number "
        "is reported. Re-run run_annual for this grid so both legs are built from one sensor grid "
        "and one weather file."
    )
    logger.info("results(action='compliance', standard='lm83') refused: %s", message)
    return refuse(
        message,
        [Advisory(code="ASE_DIRECT_SHAPE_DISAGREES", severity="error", message=message)],
    )


def _refuse_empty_lm83_denominator(
    state: _Resolved, *, grid: str, window: frozenset[int]
) -> CallToolResult:
    """`spatial_daylight_autonomy`/`annual_sunlight_exposure` raise `ValueError` on exactly two
    conditions: an empty `window` (`rad_core.periods.lm83_window`'s own micro-scene case -- a
    matrix too short to contain any 08:00-18:00 column) or an empty `tallies` list (no sensor
    points). Both are knowable from the header alone, so this is checked and refused **before** a
    single row is streamed (Task 12 review, Minor 5) -- the streaming `try/except` further down
    still exists and still carries the identical, code-less posture, but it should never actually
    fire for either of these two conditions once this guard is in place; it stays as defence in
    depth rather than the only line standing between an empty-denominator matrix and a stack trace.

    Code-less, matching `_NoAnnualResult`: the whole register was searched for a code naming "the
    analysis window is empty" and none exists. Not `ARGUMENT_OUT_OF_RANGE` -- R14 defines that as
    an argument rejected *before any work was attempted*, and by this point the project has been
    opened and the header read; using it here would be untrue of its own definition.
    """
    if not window:
        reason = (
            f"the matrix has {state.header.hours} hour column(s), and LM-83's fixed 08:00-18:00 "
            "window contains none of them -- there is no denominator and therefore no proportion "
            "to report."
        )
    else:
        reason = (
            f"the matrix carries {state.header.points} sensor points -- there is no analysis area "
            "to report a percentage of points over, so there is no denominator and therefore no "
            "proportion to report."
        )
    message = f"LM-83 compliance for grid {grid!r} could not be computed: {reason}"
    logger.info("results(action='compliance', standard='lm83') refused: %s", message)
    return refuse(message)


#: How this server treats an automated group's sunlight under LM-83-12 (final review, D-I1). The
#: rule is the server's, not section 2.2.6's text: that section allows only the manufacturer's
#: control protocol for motorized blinds and says nothing on the trigger, so the message says
#: which half is the standard's and which is this server's reading. `toward` names the trigger.
#: One wording for `LM83_EDITION_MISMATCH` and `BLIND_SCHEDULE_ORDERED_PREFIX`.
def _lm83_12_automated_rule(toward: str) -> str:
    return (
        "LM-83-12 gives no exclusion (section 2.2.6 allows only the manufacturer's control "
        f"protocol), so this server counts their open hours toward {toward} (a closed blind is "
        "taken to pass no direct sun)"
    )


def _refuse_edition_mismatch(
    state: _Resolved, *, project: str, grid: str, edition: Lm83Edition, named: bool, reports: str,
) -> CallToolResult | None:
    """`BLIND_SCHEDULE_NOT_CURRENT` or `LM83_EDITION_MISMATCH` (both error), or `None`: a blend
    whose sealed schedule no longer matches the project's window-group declarations, or read under
    an edition whose closing rule did not decide its hours (R864, design section 3).

    **Only a blend is checked**: every hour of `illblend:<grid>` was decided by the sealed
    `sched:<grid>`, and the declarations and the edition that schedule was sealed under chose the
    rule. The checks themselves are `_refuse_sealed_schedule_not_current` (final review, W-I1),
    first, and `_refuse_sealed_edition_mismatch`, which `_blind_schedule` applies in the same order
    to the same `sched:<grid>` when it publishes its per-group states. `reports` is `"compliance"`
    or `"metrics"`, for the wording.
    """
    if not state.blended:
        return None
    refusal = _refuse_sealed_schedule_not_current(
        state.loaded, project=project, grid=grid, edition=edition, reports=reports,
        blend_id=state.artifact_id,
    )
    if refusal is not None:
        return refusal
    return _refuse_sealed_edition_mismatch(
        state.loaded, project=project, grid=grid, edition=edition, named=named, reports=reports,
        blend_id=state.artifact_id,
    )


def _refuse_sealed_edition_mismatch(
    loaded: LoadedProject, *, project: str, grid: str, edition: Lm83Edition, named: bool,
    reports: str, blend_id: str,
) -> CallToolResult | None:
    """`LM83_EDITION_MISMATCH` (error), or `None`: `sched:<grid>` was sealed under an edition
    whose closing rule is not `edition`'s on this grid (R864, design section 3).

    The sealed edition is read through `rad_engine.schedule.sealed_lm83_edition`, the one reader of
    that key, so an absent key is LM-83-12 and a value outside the two editions is refused rather
    than read as either.

    **Accepted iff the grid's polygon area is at or above `LM83_BLIND_AREA_GENERAL_SF`** (Task 5
    fix round 1, controller ruling, correcting the design's "the area its sealed schedule
    recorded"). In any mismatch one side is LM-83-12. LM-83-22's rule is 2 % of the points at every
    area; LM-83-12's is 2 % exactly when its own area basis, the recorded polygon
    (`_analysis_area` under the default edition), selects the `>=1000sf` row -- the pair
    `rad_core.blinds.same_closing_rule` equates. The sealed `area_sf` is not that basis under
    LM-83-22 (it is points times spacing there), so it is not read. Without a bound automated
    schedule, a polygon area that cannot be derived returns `_analysis_area`'s own refusal, shaped
    by `refuse_engine_error`; with one, see below.

    `named` says whether the caller passed `lm83_edition`, so the message never quotes an argument
    the caller did not send; `reports` is `"compliance"`, `"metrics"` or `"blind_schedule"`, for
    the wording; `blend_id` is the blend the schedule decides, named in the remedy.

    No `sched:<grid>` registered is an absence of the thing to compare against, not evidence of a
    mismatch, and is logged rather than refused -- `_stale_blend_advisories`' posture.

    **R869/R870: a bound automated schedule is refused at any area.** When the record's
    `automated_schedules` maps any group to a digest (not `"none"`), that group's open hours were
    counted toward LM-83-12's trigger or excluded from LM-83-22's, so the two editions' rules
    differ at every area. The area then decides only which remedies are offered, under either
    edition: where none can be derived the mismatch is still refused, and the remedy offers the
    read under the sealed edition and says a re-run needs the area first (`run_annual` derives it;
    LM-83-22 still requires the polygon). A group held closed (`"none"`) has no open hours and
    leaves the area rule as it was.
    """
    sched_id = blind_schedule_id(grid)
    try:
        record = loaded.manifest.artifact(sched_id)
    except KeyError:
        logger.info(
            "results: %s cannot be checked for its sealed LM-83 edition -- no %s is registered",
            blend_id, sched_id,
        )
        return None
    try:
        sealed = sealed_lm83_edition(record.parameters)
    except ValueError as exc:
        if reports == "blind_schedule":
            message = (
                f"the blind schedule for grid {grid!r} was not published from {sched_id!r}: {exc}"
            )
        else:
            verb = "judged" if reports == "compliance" else "reported"
            message = (
                f"LM-83 results for grid {grid!r} were not {verb} from {blend_id!r}: {exc}"
            )
        logger.info("results refused: %s", message)
        return refuse(message, [Advisory(
            code="LM83_EDITION_MISMATCH", severity="error", field="lm83_edition", message=message,
        )])
    if sealed == edition:
        return None
    # R869/R870: a group marked automated with an hourly schedule bound is in LM-83-12's trigger
    # (its open hours count) and outside LM-83-22's, so the two rules differ at every area. Keyed
    # on what the record sealed -- `automated_schedules`, a digest for a bound schedule and
    # `"none"` for a group held closed -- and decided before the area is derived, since it does
    # not depend on it. A group held closed has no open hours, so it is in sun under neither rule
    # and leaves the area rule alone.
    schedules = record.parameters.get("automated_schedules")
    bound = sorted(
        str(name) for name, digest in schedules.items() if digest != "none"
    ) if isinstance(schedules, dict) else []
    # For a bound schedule the area decides only which remedies are offered: a re-run needs the
    # area `run_annual` derives -- the ASKED edition's (points times spacing under LM-83-22, which
    # still requires the polygon) -- and the sparse-grid remedy reads it (LM-83-12 only, where the
    # asked edition's area is the polygon's). So it is derived under the asked edition, and an
    # underivable one does not refuse here: the mismatch is refused, and the remedy offers only
    # the read under the sealed edition, with that edition's own reason for what a re-run needs
    # first. Without a bound schedule the area is the mismatch's own basis -- the polygon's,
    # LM-83-12's -- so it is derived under the default edition and its refusal stands.
    area: _AnalysisArea | None = None
    area_missing: str | None = None
    try:
        area = _analysis_area(
            loaded, grid=grid, edition=edition if bound else DEFAULT_LM83_EDITION,
        )
    except (ProjectError, PathOutsideRoot, RootsNotConfigured) as exc:
        if not bound:
            # `_analysis_area`'s own refusal (no grid registered, a polygon that cannot be sized,
            # an unreadable `.pts`), returned rather than raised so a `by_chain` entry carries it
            # as its own refusal; `results`' handler shapes the same exception the same way on a
            # single read.
            logger.info("results refused: %s", exc)
            return refuse_engine_error(exc)
        area_missing = str(exc)
    if not bound and area is not None and area.area_sf >= LM83_BLIND_AREA_GENERAL_SF:
        return None
    asked = (
        f"lm83_edition={edition!r}" if named
        else "lm83_edition omitted, which reads as LM-83-12"
    )
    recorded = (
        f"records lm83_edition={sealed!r}" if sealed == LM83_22
        else "records no lm83_edition, which reads as LM-83-12"
    )
    subject = "blend"
    udi = ""
    if reports == "compliance":
        opening = f"results was asked to judge grid {grid!r} under {edition} ({asked})"
        outcome = "nothing is judged from it under that edition"
    elif reports == "metrics":
        opening = (
            f"results(action='metrics') was asked to report DA_300 for grid {grid!r} under "
            f"{edition} ({asked})"
        )
        outcome = "no DA_300 is reported from it under that edition"
        udi = (
            " UDI is governed by no LM-83 edition: metrics=['udi'] reports it alone, with "
            "lm83_edition omitted."
        )
    else:
        subject = "schedule"
        opening = (
            f"results(action='blind_schedule') was asked to publish the blind schedule for grid "
            f"{grid!r} under {edition} ({asked})"
        )
        outcome = "no blind schedule is published from it under that edition"
    if subject == "blend":
        sealed_by = (
            f"{blend_id!r} is a blend whose every hour was decided by the blind schedule "
            f"{sched_id!r}, sealed under {sealed} (the record {recorded})"
        )
    else:
        sealed_by = (
            f"the per-group schedule it publishes, {sched_id!r}, was sealed under {sealed} (the "
            f"record {recorded})"
        )
    # Final fix F, fix round 2 (G4): the re-run spelled whole, from the records, as the sibling
    # remedies spell it (`_rerun_blend_call`); it was `run_annual(..., ...)`, which fails pasted.
    # An LM-83-12 re-run omits `lm83_edition`, which reads as LM-83-12.
    blend_call = _rerun_blend_call(loaded, project=project, grid=grid, edition=edition)
    rerun = (
        f"re-run {blend_call}: it rebuilds only {sched_id!r} and {blend_id!r} and reuses every "
        "matrix"
    )
    read = (
        f"read this {subject} under the edition its schedule was sealed under, lm83_edition="
        f"{sealed!r}"
    )
    # W5 (final review): an LM-83-12 re-run on a grid too sparse for LM-83-12's small-area rows is
    # refused by run_annual's density guard (`chain.blind_schedule_steps`), so it is not offered as
    # if it would succeed. The sealed `n_points` is the grid's point count that guard divides.
    n_points = record.parameters.get("n_points")
    # Only below 1 000 sf, where that guard refuses; a bound automated schedule reaches this at any
    # area (R869), and at or above 1 000 sf the LM-83-12 re-run is planned.
    sparse = (
        edition != LM83_22 and area is not None and area.area_sf < LM83_BLIND_AREA_GENERAL_SF
        and area.points_expected is not None and area.points_expected > 0
        and isinstance(n_points, int) and not isinstance(n_points, bool) and n_points > 0
        and lm83_grid_point_density(n_points, area.points_expected) < LM83_GRID_POINT_DENSITY_MIN
    )
    # Final review (Task 6's deferred item, pre-existing at master) offered the re-run only when
    # run_annual's grid validation (`_rerun_blocked_by_grid`) would pass, and quoted its refusal
    # otherwise. Final fix E, fix round 2 (review O2): that quote is removed, because it can no
    # longer be reached. Every state it quoted -- a `.pts` changed, missing, unreadable,
    # oversized or a symlink (round 3), or a grid off its recorded floor -- is refused by the read
    # itself before this
    # (`_refuse_grid_off_recorded_floor`). An unregistered or polygon-less grid derives no area
    # and takes the area branch below.
    if area_missing is not None:
        remedy = (
            f"Read this {subject} under lm83_edition={sealed!r}, the edition its schedule was "
            f"sealed under. A re-run under {edition} needs an analysis area first, and none can "
            f"be derived for this grid: {area_missing}"
        )
    elif sparse:
        density = lm83_grid_point_density(n_points, area.points_expected)
        remedy = (
            f"Read this {subject} under lm83_edition={sealed!r}, the edition its schedule was "
            f"sealed under. An {edition} re-run needs an LM-83 grid first: this grid holds "
            f"{n_points} point(s) where LM-83's own 0.6 m placement over its polygon puts "
            f"{area.points_expected} (a density of {density:.0%}, below "
            f"{LM83_GRID_POINT_DENSITY_MIN:.0%}), so run_annual with blind_control='lm83' refuses "
            f"to plan an {edition} blend on it. Re-create it with "
            f"{_lm83_grid_recreate_call(loaded, project=project, grid=grid)}, which places LM-83's "
            f"own points over that polygon, then re-run {blend_call}: the grid's points move, so "
            "every "
            "stored result on it is refused as stale until that re-run rebuilds them."
            + _lm83_grid_recreate_caveat(loaded, grid=grid)
        )
    else:
        remedy = (
            f"Read this {subject} under lm83_edition={sealed!r}, the edition its schedule was "
            f"sealed under, or {rerun}."
            if not named and sealed == LM83_22
            else f"{rerun[0].upper()}{rerun[1:]}. Or {read}."
        )
    decided = "This blend's" if subject == "blend" else "That schedule's"
    if bound:
        message = (
            f"{opening}, and {sealed_by}. The schedule sealed for grid {grid!r} records automated "
            f"group(s) {', '.join(bound)} with an hourly schedule bound. "
            f"{_lm83_12_automated_rule('the closing trigger')}; "
            f"{section('blind_trigger', LM83_22)} excludes them. So "
            f"{decided[0].lower()}{decided[1:]} hours were decided by {sealed}'s rule, not "
            f"{edition}'s, at any area, and {outcome}.{udi} {remedy}"
        )
        logger.info("results refused: %s", message)
        return refuse(message, [Advisory(
            code="LM83_EDITION_MISMATCH", severity="error", field="lm83_edition",
            message=message,
        )])
    assert area is not None  # no bound schedule: derived above, or refused there
    message = (
        f"{opening}, and {sealed_by}. The grid's "
        f"polygon area -- the basis LM-83-12's area table is keyed on -- is {area.area_sf:.0f} sf, "
        f"below {LM83_BLIND_AREA_GENERAL_SF:.0f} sf, where the two editions close the blinds by "
        "different rules: LM-83-12 by the absolute point counts of its area table "
        f"({section('blind_trigger', 'LM-83-12')}), LM-83-22 when more than 2 % of the points "
        f"are in direct sun ({section('blind_trigger', LM83_22)}). {decided} hours were not "
        f"decided by {edition}'s rule, so {outcome}.{udi} {remedy}"
    )
    logger.info("results refused: %s", message)
    return refuse(message, [Advisory(
        code="LM83_EDITION_MISMATCH", severity="error", field="lm83_edition", message=message,
    )])


#: `_read_points`' own re-create sentence, whose call is elided (`name=..., ...)` or no name at
#: all): left out of `_rerun_blocked_by_grid`'s quote where the record's create is spelled after it.
_ELIDED_GRID_RECREATE: Final = re.compile(
    r" Re-create (?:the grid|it) with sensor_grid\(action='create'(?:, name='[^']*', \.\.\.)?\)\."
)


def _rerun_blocked_by_grid(
    loaded: LoadedProject, grid: str, *, project: str | None = None,
) -> str | None:
    """Why `run_annual` would refuse this grid before planning anything, or `None` (final review,
    Task 6's deferred item).

    `run_annual`'s `_validate_project` reads the grid through `_grid_points_file` and
    `_read_points`, which compares the record's digest with the `.pts` bytes, and then
    `refuse_grid_off_recorded_floor`. The same three run here, so a remedy offers a re-run only
    when that run would not be refused on its grid; otherwise the remedy quotes the refusal, which
    names what fails and the call that fixes it.

    Final fix F, fix round 4 (K2): a `.pts` that no longer holds the points its record digested,
    or cannot be read, is quoted as `SENSOR_GRID_FILES_CHANGED` quotes it on a read
    (`_changed_grid_file`): `_read_points`' own sentence less its elided `sensor_grid(action=
    'create', name=..., ...)` (`_ELIDED_GRID_RECREATE`), then the create call the record holds,
    spelled whole with `project`, and what re-sending it does. A record holding no create call
    keeps the elided one, the only one there is.

    Fix round 5 (L2): `_changed_grid_file(quoted=True)`, so the refusal quoted is `run_annual`'s
    alone and what the re-create does is said of that run, not of a read, which the refusal this
    sentence sits in still refuses until the re-run."""
    try:
        path, polygon, height, digest = _grid_points_file(loaded, grid)
        # Fix round 3 (S1): run_annual refuses a grid file outside every root before reading it,
        # with the read's own words, so that is the refusal quoted.
        if (outside := _grid_outside_refusal(path, grid=grid)) is not None:
            return str(outside)
        points, _text = _read_points(path, digest, grid=grid)
        refuse_grid_off_recorded_floor(grid, polygon, points, height=height)
    except (GridFileChangedError, GridUnreadableError, GridEmptyOnDiskError) as exc:
        record = next(
            (r for r in loaded.manifest.inputs
             if r.kind == "sensor_grid" and r.id == f"{SENSOR_GRID_INPUT_PREFIX}{grid}"),
            None,
        )
        if (
            record is None or record.zones is not None or not record.files
            or isinstance(exc, GridRecordNameError)
        ):
            return str(exc)
        refusal = str(_changed_grid_file(exc, record, grid=grid, project=project, quoted=True))
        if " Re-sent as recorded, sensor_grid(" in refusal:
            # The call spelled whole follows, so `_read_points`' elided one is left out.
            refusal = _ELIDED_GRID_RECREATE.sub("", refusal, count=1)
        return refusal
    except UnicodeDecodeError:
        return (
            f"the grid's .pts cannot be read as a sensor grid: it is not UTF-8 text. Re-create it "
            f"with sensor_grid(action='create', name={grid!r}, ...)."
        )
    except (ProjectError, PathOutsideRoot, RootsNotConfigured) as exc:
        return str(exc)
    return None


def _bind_first_clause(loaded: LoadedProject, *, project: str) -> str:
    """The `assign_bsdf` calls a three-phase `blind_control='lm83'` re-run needs first, as a
    sentence ending in "Then ", or `""` when every registered window group binds both of the states
    the schedule names (final fix A, fix round 5, N2). `run_annual` refuses before planning a group
    with no state bound (`chain.three_phase_target`, "has no BSDF bound") or without `open` and
    `closed` (`annual_tool.BlendStatesNotBoundError`), so a re-run offered without these calls is
    refused as pasted. Each call names only the missing states -- `assign_bsdf` seals one `tmx`
    per state it is given and leaves the others bound -- with `...` for each path, which the
    sentence says how to fill."""
    bound = states_by_group(loaded)
    calls = []
    for record in sorted(
        (record for record in loaded.manifest.inputs if record.kind == "window_group"),
        key=lambda record: record.id,
    ):
        name = record.id.removeprefix(WINDOW_GROUP_INPUT_PREFIX)
        missing = sorted(BLIND_STATES - set(bound.get(name, ())))
        if missing:
            states = ", ".join(f"{state!r}: ..." for state in missing)
            calls.append(
                f"window_group(action='assign_bsdf', project={project!r}, name={name!r}, "
                f"states={{{states}}})"
            )
    if not calls:
        return ""
    return (
        "Bind the 'open' and 'closed' states each window group lacks first -- "
        + "; ".join(calls)
        + ", passing as each state the path of that state's Klems BSDF XML -- because run_annual "
        "refuses a blind_control='lm83' run before planning it while any window group lacks "
        "either. Then "
    )


def _recorded_grid_create(loaded: LoadedProject, grid: str) -> dict[str, Any] | None:
    """The arguments of the `sensor_grid(action='create')` call `grid`'s record holds, or `None`
    when the grid is unregistered or its record holds no create call."""
    try:
        record = loaded.manifest.input(f"{SENSOR_GRID_INPUT_PREFIX}{grid}")
    except KeyError:
        return None
    if record.registered_by is None or record.registered_by.action != "create":
        return None
    return dict(record.registered_by.arguments)


def _lm83_grid_recreate_caveat(loaded: LoadedProject, *, grid: str) -> str:
    """`_unrecorded_normal_caveat` for `_lm83_grid_recreate_call`, or `""` (final fix F, fix
    round 3, H3): a record of a create call from before `sensor_grid` recorded `normal` spells
    none, as `SENSOR_GRID_FILES_CHANGED`'s re-create says of the same record. Here the grid's file
    is the one the record digested (the read refused it otherwise), but a re-create is spelled
    from the record, as that one is, so the caller is told what to add rather than the server
    choosing a normal for it."""
    arguments = _recorded_grid_create(loaded, grid)
    if arguments is None or arguments.get("normal") is not None:
        return ""
    return _unrecorded_normal_caveat(", so that call names none", call="that sensor_grid call")


def _lm83_grid_recreate_call(loaded: LoadedProject, *, project: str, grid: str) -> str:
    """`sensor_grid(action='create', ...)` that re-creates `grid` as an LM-83 grid over the polygon
    its record holds (final fix F, fix round 2, G4): the recorded create call, with `standard=
    'lm83'` and only the arguments an LM-83 create takes (`polygon`, `height`, `normal`), as
    `SENSOR_GRID_FILES_CHANGED`'s re-create is spelled from the same record. Another standard's
    arguments (`task_zones`, `surround`, ...) decided the sparse placement and are dropped. A
    record with no recorded polygon keeps a placeholder, named as one; one with no recorded normal
    names none, and `_lm83_grid_recreate_caveat` says so where the call is offered.

    Final fix G (G-5, controller ruling): `height` is kept only from a record that was itself an
    LM-83 grid (`standard='lm83'`), whose recorded height is the plane LM-83 placed it on. From
    another standard's record (EN 17037's 0.85 m) it is left out, so `sensor_grid` applies LM-83's
    own default. Every caller is an LM-83-12 remedy -- the density screens this re-create answers
    are not applied under LM-83-22 -- and LM-83-12 is the default edition, so no `lm83_edition` is
    spelled for such a record and the default is 0.8 m. An LM-83 record's own `lm83_edition` is
    spelled (fix round 2, Q4), so the re-create reproduces it."""
    arguments = _recorded_grid_create(loaded, grid) or {}
    parts = ["action='create'", f"project={project!r}", "standard='lm83'", f"name={grid!r}"]
    polygon = arguments.get("polygon")
    parts.append(f"polygon={polygon!r}" if polygon else "polygon=[[x, y], ...]")
    # Fix round 2 (Q4, controller ruling): the re-create reproduces the record, so an LM-83
    # record's `lm83_edition` is spelled too (an LM-83-22 grid's 0.76 m with its edition).
    keys = (
        ("height", "normal", "lm83_edition") if arguments.get("standard") == "lm83"
        else ("normal",)
    )
    for key in keys:
        if arguments.get(key) is not None:
            parts.append(f"{key}={arguments[key]!r}")
    return f"sensor_grid({', '.join(parts)})" + (
        "" if polygon else ", passing as polygon the room's floor polygon"
    )


def _rerun_blend_call(
    loaded: LoadedProject, *, project: str, grid: str, edition: Lm83Edition,
    also_from: Sequence[str] = (),
) -> str:
    """The `run_annual` call that re-derives `sched:<grid>` and `illblend:<grid>` from the project
    as it is now: the three-phase `blind_control='lm83'` run, the one run that seals a schedule
    (`_THREE_PHASE_ROUTE`), under the asked edition so the read that follows is not refused for it.

    **Every argument that decides what that run builds is the one the sealed run recorded**, so
    the call pastes as written and rebuilds only what the declarations decide (final fix A, fix
    round 1, N6): `epw` and `north` are the weather input and `north_east_of_north` of the one sky
    matrix the blend (or, with no blend, the schedule) reaches, `sky_subdivision` is that matrix's
    `mf`, `quality` is the ambient flag set every V and D matrix the blend reaches records
    (`_recorded_quality`), and `method` is `'3phase'`, the chain that seals a blend -- spelled
    rather than left to the inference, which on a light-redirecting state picks the five-phase
    chain that seals none. Where the manifest does not record one of them it stays `...`, and the
    sentence names that argument, and only that one, with what to pass.

    `also_from` (final fix F, fix round 3, H2) names the grid's other results, read after the
    blend and the schedule, for a grid that holds neither yet (`_seal_blend_clause`): the arguments
    the run that sealed them recorded."""
    ids = {artifact.id for artifact in loaded.manifest.artifacts}
    blend = blend_ill_id(grid)
    return _rerun_call(
        loaded, project=project, grid=grid,
        starts=(
            (blend, _SMX_KIND), (blind_schedule_id(grid), _SMXSUN_KIND),
            *((other, _SMX_KIND) for other in also_from),
        ),
        quality_from=blend if blend in ids or not also_from else also_from[0],
        arguments=(
            "metrics=['da'], blind_control='lm83'"
            + (f", lm83_edition={LM83_22!r}" if edition == LM83_22 else "")
        ),
        method="3phase",
    )


def _seal_blend_clause(
    loaded: LoadedProject, *, project: str, grid: str, results: Sequence[str],
) -> str:
    """The end of `_BlindControlNotRegistered`'s sentence where `blind_control='lm83'` found no
    blend (final fix F, fix round 3, H2; it was "or re-run run_annual(..., blind_control='lm83')"):
    the three-phase run that seals one, spelled from what the grid's `results` recorded
    (`_rerun_blend_call`), after the `assign_bsdf` calls it needs first (`_bind_first_clause`).
    A project defining no window group cannot seal one by any re-run, and is told what it needs
    (`_no_schedule_repair`)."""
    if not any(record.kind == "window_group" for record in loaded.manifest.inputs):
        return ". " + _no_schedule_repair(loaded, grid=grid)
    call = _rerun_blend_call(
        loaded, project=project, grid=grid, edition=DEFAULT_LM83_EDITION, also_from=results,
    )
    first = _bind_first_clause(loaded, project=project)
    if first:
        return f". To seal the blend for this grid instead: {first}re-run {call}."
    return f", or re-run {call} so the blend is sealed for this grid."


def _rerun_call(
    loaded: LoadedProject, *, project: str, grid: str, starts: Sequence[tuple[str, str]],
    quality_from: str, arguments: str, method: str,
) -> str:
    """A whole `run_annual` call for `grid`, with `arguments` (Python source) after `grid`, and
    `epw`, `north`, `sky_subdivision` and `quality` read off the records: the weather and north of
    the one sky matrix the first of `starts` (artifact id, sky kind) to reach exactly one reaches,
    its `mf`, and the V and D matrices `quality_from` reaches (`_recorded_quality`). What the
    manifest does not record stays `...`, and the sentence names that argument with what to pass.
    `_rerun_blend_call`'s builder, shared with the two-phase re-run `WINDOW_GROUPS_NOT_CURRENT`
    spells (final fix E1)."""
    sky: str | None = None
    for start, kind in starts:
        edges = _reachable_smx_edges(loaded, start, kind=kind)
        if len(edges) == 1:
            sky = edges[0]
            break
    epw: str | None = None
    north: float | None = None
    subdivision: int | None = None
    if sky is not None:
        try:
            artifact = loaded.manifest.artifact(sky)
        except KeyError:
            artifact = None
        if artifact is not None:
            weather_ids = {
                record.id for record in loaded.manifest.inputs if record.kind == "weather"
            }
            epw = next((edge for edge in artifact.built_from if edge in weather_ids), None)
            value = artifact.parameters.get("north_east_of_north")
            if (
                isinstance(value, (int, float)) and not isinstance(value, bool)
                and math.isfinite(value)
            ):
                north = float(value)
            mf = artifact.parameters.get("mf")
            if isinstance(mf, int) and not isinstance(mf, bool):
                subdivision = mf
            elif isinstance(mf, float) and mf.is_integer():
                subdivision = int(mf)
    quality = _recorded_quality(loaded, quality_from)
    call = (
        f"run_annual(project={project!r}, grid={grid!r}, {arguments}, "
        f"epw={'...' if epw is None else repr(epw)}, "
        f"north={'...' if north is None else repr(north)}, "
        f"sky_subdivision={'...' if subdivision is None else subdivision}, "
        f"quality={'...' if quality is None else quality}, method={method!r})"
    )
    missing = [
        (argument, what) for argument, what, value in (
            ("epw", "the weather handle", epw), ("north", "the north", north),
            ("sky_subdivision", "the sky subdivision", subdivision),
            ("quality", "the quality", quality),
        ) if value is None
    ]
    if missing:
        call += (
            f", passing as {_and_list([argument for argument, _ in missing])} "
            f"{_and_list([what for _, what in missing])} this grid's results were built with"
        )
    return call


def _and_list(items: Sequence[str]) -> str:
    """`a`, `a and b`, `a, b and c`."""
    return items[0] if len(items) == 1 else f"{', '.join(items[:-1])} and {items[-1]}"


#: The five ambient flags a `run_annual` `quality` sets, as the V and D matrices record them.
_QUALITY_FLAGS: Final[tuple[str, ...]] = ("ab", "ad", "as", "ar", "aa")


def _recorded_quality(loaded: LoadedProject, start: str) -> str | None:
    """The `quality` argument, as Python source, that rebuilds the V and D matrices `start`
    reaches with the ambient flags they record -- a preset's name when the five flags are exactly
    that preset's, else a dict of all five (`rad_engine.rtrace.resolve_quality` lays a dict over
    `standard`, so naming all five leaves nothing to the base) -- or `None` when no V or D matrix
    is reached, one records no number for a flag, or two record different sets: a quality this
    function would have to choose is not the one the run recorded."""
    flag_sets: set[tuple[float, ...]] = set()
    # Final fix E, fix round 4 (X1): `dsmx` too, the two-phase daylight coefficients, which record
    # the same flags; no earlier caller's `start` reaches one.
    for kind in ("vmx", "dmx", "dsmx"):
        for edge in _reachable_smx_edges(loaded, start, kind=kind):
            try:
                parameters = loaded.manifest.artifact(edge).parameters
            except KeyError:
                return None
            values = [parameters.get(flag) for flag in _QUALITY_FLAGS]
            if not all(
                isinstance(value, (int, float)) and not isinstance(value, bool)
                and math.isfinite(value) for value in values
            ):
                return None
            flag_sets.add(tuple(float(value) for value in values))  # type: ignore[arg-type]
    if len(flag_sets) != 1:
        return None
    [flags] = flag_sets
    if not all(value.is_integer() for value in flags[:4]):
        return None
    for name, preset in AMBIENT_PRESETS.items():
        if flags == (
            preset.ambient_bounces, preset.ambient_divisions, preset.ambient_super_samples,
            preset.ambient_resolution, preset.ambient_accuracy,
        ):
            return repr(name)
    spelled = {flag: int(value) for flag, value in zip(_QUALITY_FLAGS[:4], flags[:4])}
    return repr({**spelled, "aa": flags[4]})


def _blend_rerun_remedy(
    loaded: LoadedProject, *, project: str, grid: str, edition: Lm83Edition, added: Sequence[str],
) -> str:
    """The remedy that re-derives `sched:<grid>` and `illblend:<grid>`: the binding calls a
    three-phase `blind_control='lm83'` run needs first (`_bind_first_clause`), then that run spelled
    whole (`_rerun_blend_call`), or, where `run_annual` would refuse the grid, that refusal quoted
    (`_rerun_blocked_by_grid`). With window groups `added` since the run, it says what a re-run
    refused at a group's own direct-sun matrix needs first (final fix A, rounds 4 and 5, N3) and
    where an opaque aperture polygon's material goes (final fix E7, `_transmitting_repair`).
    `BLIND_SCHEDULE_NOT_CURRENT`'s remedy, moved here unchanged otherwise so that
    `WINDOW_GROUPS_NOT_CURRENT` offers the same calls for the same blend (final fix E1)."""
    sched_id = blind_schedule_id(grid)
    blocked = _rerun_blocked_by_grid(loaded, grid, project=project)
    if blocked is not None:
        # Fix round 4 (K2): the ids named, since in SCENE_INCLUDE_CHANGED's message "them" had no
        # antecedent (measured through the real tools by the round-3 re-review).
        return (
            f"A re-run, {_rerun_blend_call(loaded, project=project, grid=grid, edition=edition)}, "
            f"re-derives {sched_id!r} and {blend_ill_id(grid)!r} from the declarations recorded "
            f"now, and it would be refused on this grid first: {blocked}"
        )
    first = _bind_first_clause(loaded, project=project)
    rerun = f"re-run {_rerun_blend_call(loaded, project=project, grid=grid, edition=edition)}"
    return (
        (f"{first}{rerun}" if first else f"R{rerun[1:]}")
        + f": it re-derives {sched_id!r} and {blend_ill_id(grid)!r} from the declarations "
        "recorded now"
        + (
            # Final fix A, fix rounds 4 and 5 (N3), measured through the real tool
            # (test_annual_tool.TestWhatARefusedAseRunLeavesReadable): a group added in series
            # with another, or over an aperture polygon that does not transmit, is refused at
            # its own direct-sun matrix on every re-run, so what has to change is named.
            ", unless it is refused at a window group's own direct-sun matrix "
            "(ASE_DIRECT_MATRIX_EMPTY). Then no re-run seals them until that group changes: "
            "while its light passes another window group (a light well, an outer skin) the "
            "per-group schedule cannot be sealed, and where its aperture polygon does not "
            "transmit, the polygon needs a transmitting material, for example glass, before the "
            "re-run: " + _transmitting_repair(loaded, project=project, groups=added)
            if added else "."
        )
    )


def _transmitting_repair(loaded: LoadedProject, *, project: str, groups: Sequence[str]) -> str:
    """Where the aperture polygons of `groups` get a transmitting material, said from the records
    (final fix E7, part A round-5 re-review N4), as the clause after "needs a transmitting
    material, for example glass, before the re-run: ". Radiance reads the last definition of a
    modifier before a surface, and `run_annual` puts the registered materials ahead of the
    geometry (`scene_files_with_materials`), so a modifier the registered geometry defines itself
    -- in its own file or (fix round 1, review I4) in a file it includes with `!xform`, found by
    the same flatten `run_annual` reads -- is repaired in that file and the geometry re-added with
    the `geometry(action='add', ...)` call its record holds; measured, `materials(action='set')`
    over either was accepted and the re-run refused again. One `materials(action='set')`
    assigned, and the geometry does not define, is set again with that call, its recorded
    reflectance cleared. Where the records cannot tell (a file that cannot be read, flattened or
    parsed, a modifier `apply_defaults` wrote, or one defined nowhere this finds), the conditional
    sentence `rad_engine.project` itself gives (`TRANSMITTING_MATERIAL_WHERE`) is used."""
    from rad_mcp.annual_tool import _geometry_add_calls  # annual_tool imports this module

    generic = TRANSMITTING_MATERIAL_WHERE
    modifiers: list[str] = []
    for record in loaded.manifest.inputs:
        if record.kind != "window_group" or (
            record.id.removeprefix(WINDOW_GROUP_INPUT_PREFIX) not in groups
        ):
            continue
        raw = record.registered_by.arguments.get("aperture") if record.registered_by else None
        for name in ((raw,) if isinstance(raw, str) else tuple(raw or ())):
            if isinstance(name, str) and name not in modifiers:
                modifiers.append(name)
    if not modifiers:
        return generic

    def names_in(path: Path) -> frozenset[str]:
        with open_regular_file(contain(path, current().roots), "rb") as handle:
            return defined_modifier_names(handle.read().decode("utf-8"))

    # modifier -> (geometry record ids, the included file that defines it, or None when the
    # record's own file does)
    in_geometry: dict[str, tuple[list[str], Path | None]] = {}
    in_materials: set[str] = set()
    try:
        env = None
        for record in loaded.manifest.inputs:
            if record.kind == "materials":
                for ref in record.files:
                    in_materials |= names_in(Path(ref.path)) & set(modifiers)
            if record.kind != "geometry":
                continue
            own = [Path(ref.path) for ref in record.files]
            for path in own:
                for name in names_in(path) & set(modifiers):
                    in_geometry.setdefault(name, ([], None))[0].append(record.id)
            missing = [name for name in modifiers if name not in in_geometry]
            if not missing:
                continue
            env = env or discover()
            flattened = flatten_scene(
                env, [str(path) for path in own], cwd=loaded.paths.root,
                timeout=_APERTURE_READ_TIMEOUT_S,
            )
            resolved_own = {path.resolve() for path in own}
            for include in sorted(flattened.includes):
                if Path(include).resolve() in resolved_own:
                    continue
                for name in names_in(Path(include)) & set(missing):
                    in_geometry.setdefault(name, ([record.id], Path(include)))
    except (
        OSError, ValueError, PathOutsideRoot, RootsNotConfigured, ProjectError, RadianceError,
        RadianceNotFound,
    ):
        return generic
    except SceneInlineCommandError as exc:
        # Fix round 3 (review N1): a `RuntimeError`, raised by the R33 scan inside the flatten for
        # a file the geometry includes that holds a disallowed `!` line. Escaping, it turned the
        # coded refusal this remedy is part of into an uncoded transport error. Fix round 4
        # (review M1): `run_annual`'s own scan (`_rerun_scan_refusal`) refuses the re-run this
        # remedy spells before it plans anything -- measured: SCENE_INLINE_COMMAND on the same
        # line, or PATH_OUTSIDE_ROOT where the include lies outside every root, which this
        # flatten does not check -- so that refusal is named, with its own code.
        refused = _rerun_scan_refusal(loaded)
        return (
            f"{generic} Where it is defined could not be read off the registered geometry: "
            f"{str(exc).rstrip('.')}."
            + (
                f" run_annual refuses the re-run above for the scene as it is now "
                f"({refused.code}), so fix that first: {str(refused).rstrip('.')}."
                if refused is not None else ""
            )
        )
    except SceneFileNotFoundError:
        # Fix round 4 (review M3): the scan raises this only for a missing top-level scene file
        # (`rad_engine.scene`); a missing include takes the `RadianceError` arm above, and
        # `names_in` has read each top-level file before the flatten, so a `FileNotFoundError`
        # there is caught as `OSError` first. Kept for a file removed between the two reads,
        # where nothing could be read either.
        return generic
    # A modifier `materials(action='set')` assigned: re-set as glass. That call merges per field
    # (`materials_tool._merge`) over its own recorded assignments, so a recorded reflectance, which
    # glass refuses, is cleared with `None` (measured: refused ENERGY_BALANCE_VIOLATED without it).
    # One `apply_defaults` wrote gets the conditional sentence (fix round 1, review M4: `set`
    # withdraws it from the defaults file, so it would not be defined twice; the spelled call is
    # simply not measured for that state).
    set_specs: dict[str, dict[str, Any]] = {}
    for record in loaded.manifest.inputs:
        call = record.registered_by
        if record.kind == "materials" and call is not None and call.action == "set":
            assignments = call.arguments.get("assignments")
            if isinstance(assignments, dict):
                set_specs.update({
                    name: spec for name, spec in assignments.items() if isinstance(spec, dict)
                })
    registered = [
        name for name in modifiers
        if name not in in_geometry and name in in_materials and name in set_specs
    ]
    if len(in_geometry) + len(registered) != len(modifiers):
        return generic
    parts: list[str] = []
    inline = [name for name in modifiers if name in in_geometry and in_geometry[name][1] is None]
    included = [name for name in modifiers if name in in_geometry and in_geometry[name][1]]
    if inline:
        records = sorted({record_id for name in inline for record_id in in_geometry[name][0]})
        one = len(inline) == 1
        parts.append(
            f"{_and_list([repr(name) for name in inline])} {'is' if one else 'are'} defined in "
            "the registered geometry itself, and that definition is the one its polygons read, "
            f"ahead of the registered materials, so redefine {'it' if one else 'them'} as glass "
            "in the source file that call adds and add it again: "
            + _geometry_add_calls(loaded, project, input_ids=records)
        )
    for name in included:
        records, include = in_geometry[name]
        parts.append(
            f"{name!r} is defined in {str(include)!r}, which the registered geometry includes "
            "with !xform, and that definition is the one its polygons read, ahead of the "
            "registered materials, so redefine it as glass in that file and add the geometry "
            "again: " + _geometry_add_calls(loaded, project, input_ids=records)
        )
    if registered:
        spelled = ", ".join(
            f"{name!r}: {{'type': 'glass', 'transmittance': ..."
            + (", 'reflectance': None" if set_specs[name].get("reflectance") is not None else "")
            + "}"
            for name in registered
        )
        parts.append(
            f"{_and_list([repr(name) for name in registered])} "
            f"{'is' if len(registered) == 1 else 'are'} defined in the registered materials: "
            f"materials(action='set', project={project!r}, assignments={{{spelled}}}), passing "
            "as each transmittance that glazing's"
        )
    return "; ".join(parts) + "."


def _window_groups_reached(loaded: LoadedProject, artifact_id: str) -> list[str]:
    """The window groups `artifact_id` was built from: every `window_group` input its `built_from`
    edges reach, transitively, by name, sorted (final fix E1). A three- or five-phase result reaches
    them through its view matrices (`REQUIRED_EDGES["vmx"]` requires the group's input), a blend
    through its per-group operands; a two-phase result reaches none, since its daylight
    coefficients trace the whole scene. Edges naming nothing registered are skipped, and the walk
    keeps a `seen` set for `_reachable_smx_edges`' reason (a hand-edited cycle)."""
    groups = {
        record.id: record.id.removeprefix(WINDOW_GROUP_INPUT_PREFIX)
        for record in loaded.manifest.inputs if record.kind == "window_group"
    }
    artifacts = {artifact.id: artifact for artifact in loaded.manifest.artifacts}
    found: set[str] = set()
    seen: set[str] = {artifact_id}
    frontier = [artifact_id]
    while frontier:
        next_frontier: list[str] = []
        for current_id in frontier:
            artifact = artifacts.get(current_id)
            if artifact is None:
                continue
            for edge in artifact.built_from:
                if edge in groups:
                    found.add(groups[edge])
                elif edge not in seen:
                    seen.add(edge)
                    next_frontier.append(edge)
        frontier = next_frontier
    return sorted(found)


def _groups_not_current(loaded: LoadedProject, artifact_id: str) -> bool:
    """Whether `_refuse_window_groups_not_current` refuses `artifact_id`: it reaches window groups
    and they are not the registered set."""
    sealed = _window_groups_reached(loaded, artifact_id)
    current = sorted(
        record.id.removeprefix(WINDOW_GROUP_INPUT_PREFIX) for record in loaded.manifest.inputs
        if record.kind == "window_group"
    )
    return bool(sealed) and sealed != current


def _refuse_window_groups_not_current(
    state: _Resolved, *, project: str, grid: str, reads: str,
    edition: Lm83Edition = DEFAULT_LM83_EDITION,
) -> CallToolResult | None:
    """`WINDOW_GROUPS_NOT_CURRENT` (error, `field="grid"`), or `None`: the result this call reads
    was built from another set of window groups than the project registers now (final fix E1).

    **Measured** (`tests/test_annual_tool.py::TestAResultBuiltForOtherWindowGroupsIsRefused`): a
    three- or five-phase result counts daylight only through the glazing of the groups it was built
    from -- each group's V matrix traces the room to one glow surface over that group's aperture,
    with no sky, so a ray leaving through glazing no group claims adds zero. A group defined later
    over that glazing moves no digest the result was built from, so nothing marked it stale, and
    every read published the old number, which omits that window. A two-phase result
    (`_window_groups_reached` empty) traces every aperture of the scene and is not compared, and
    neither is a record that reaches no group edge, which no chain of this server seals.

    **Keyed on what was sealed**: the groups the result's own edges reach, against every
    registered `window_group` input. A group no longer registered is named too; through the wire
    `window_group(action='remove')` refuses while a sealed matrix names it, and a hand-removed input
    is refused as an incomplete edge set first (`_refuse_stale_results`).

    **One refusal per state**: on a blend, compliance(standard='lm83') and metrics reporting DA_300
    are refused `BLIND_SCHEDULE_NOT_CURRENT` first, which says the same of the sealed schedule
    and is the more specific (it names the closing order and the hours the schedule decided);
    their callers run this after that check, so it fires there only where that one did not.

    The remedy follows the result's chain. A blend: `_blend_rerun_remedy`, the same calls
    `BLIND_SCHEDULE_NOT_CURRENT` gives. An unblended three- or five-phase result: the run that
    sealed it is refused with more than one group registered (`ThreePhaseTarget.single`,
    measured), so it names removing the added groups, where no sealed matrix names them yet, and
    the two-phase re-run (`_rerun_call`), which traces every aperture; `_rerun_blocked_by_grid`
    is honoured. `reads` is the action, for the opening."""
    sealed = _window_groups_reached(state.loaded, state.artifact_id)
    if not sealed:
        return None
    loaded = state.loaded
    current = sorted(
        record.id.removeprefix(WINDOW_GROUP_INPUT_PREFIX) for record in loaded.manifest.inputs
        if record.kind == "window_group"
    )
    if sealed == current:
        return None
    added = [name for name in current if name not in sealed]
    removed = [name for name in sealed if name not in current]

    def named(names: Sequence[str]) -> str:
        return f"window group(s) {', '.join(names)}" if names else "no window group"

    notes = [
        *([f"{named(added)} defined since the run"] if added else []),
        *([f"{named(removed)} no longer registered"] if removed else []),
    ]
    consequences = [
        *([f"the light through the glazing of {named(added)} is in no part of it"]
          if added else []),
        *([f"it still carries the light through the glazing of {named(removed)}"]
          if removed else []),
    ]
    chain = (
        "blended three-phase" if state.blended
        else "five-phase" if state.method_used == "5phase" else "three-phase"
    )
    if state.blended:
        # Fix round 1 (review I1): the re-run reseals the schedule, so it is spelled under the
        # edition `sched:<grid>` was sealed under -- not the call's, which on a read that takes no
        # `lm83_edition` is the default -- or the edition's own reads of the blend are refused
        # LM83_EDITION_MISMATCH after it. A record naming no edition this server reads keeps the
        # call's.
        try:
            sealed_edition = sealed_lm83_edition(
                loaded.manifest.artifact(blind_schedule_id(grid)).parameters
            )
        except (KeyError, ValueError):
            sealed_edition = edition
        remedy = _blend_rerun_remedy(
            loaded, project=project, grid=grid, edition=sealed_edition, added=added
        )
    else:
        remedy = _unblended_groups_remedy(
            loaded, project=project, grid=grid, artifact_id=state.artifact_id, added=added,
            chain=chain,
        )
    message = (
        f"results(action={reads!r}) was asked to read grid {grid!r} from {state.artifact_id!r}, "
        f"a {chain} result built from {named(sealed)}, and the project now registers "
        f"{named(current)} ({'; '.join(notes)}). A three- or five-phase result counts daylight "
        "only through the glazing of the window groups it was built from -- each group's view "
        "matrix traces the room to that group's aperture alone, with no sky -- so "
        f"{', and '.join(consequences)}. No stored value was read. {remedy}"
    )
    logger.info("results refused: %s", message)
    return refuse(message, [Advisory(
        code="WINDOW_GROUPS_NOT_CURRENT", severity="error", field="grid", message=message,
    )])


def _unblended_groups_remedy(
    loaded: LoadedProject, *, project: str, grid: str, artifact_id: str, added: Sequence[str],
    chain: str,
) -> str:
    """`WINDOW_GROUPS_NOT_CURRENT`'s remedy for an unblended three- or five-phase result (final fix
    E1). Measured through the real tool: with a second group registered, the run that sealed it is
    refused ("this chain builds for exactly one"), so that re-run is not offered. What reads this
    grid again: removing the groups defined since -- pasteable only where no sealed artifact names
    one yet (`window_group(action='remove')` refuses a group a bound BSDF's `tmx` names, which is
    said) -- after which this result is current again, still without that glazing's light; and a
    two-phase run, which traces every aperture of the scene, claimed or not, and seals
    `ill:<grid>`, read with `chain='2phase'`. Where `run_annual` would refuse the grid that
    refusal is quoted in place of the run."""
    def named_groups(names: Sequence[str]) -> str:
        return f"window group(s) {', '.join(names)}"

    built_from = {edge for artifact in loaded.manifest.artifacts for edge in artifact.built_from}
    removable = [name for name in added if f"{WINDOW_GROUP_INPUT_PREFIX}{name}" not in built_from]
    held = [name for name in added if name not in removable]
    parts = [
        # Fix round 1 (review I2's sibling): a five-phase run resolves one group under every
        # blind_control (`_plans_blend` is three-phase only).
        (f"A five-phase run resolves exactly one window group under every blind_control, so the "
         if chain == "five-phase" else
         "A three-phase run without blind_control='lm83' resolves exactly one window group, so "
         "the ")
        + f"run that sealed {artifact_id!r} is refused while more than one is registered."
    ]
    if removable and not held:
        calls = "; ".join(
            f"window_group(action='remove', project={project!r}, name={name!r})"
            for name in removable
        )
        parts.append(
            f"Removing {named_groups(removable)} -- {calls} -- makes this result current again, "
            "still without the light through that glazing."
        )
    elif held:
        parts.append(
            f"{named_groups(held)} cannot be removed while the BSDF matrices bound to "
            f"{'it' if len(held) == 1 else 'them'} name {'it' if len(held) == 1 else 'them'} "
            "(window_group(action='remove') refuses)."
        )
    blocked = _rerun_blocked_by_grid(loaded, grid, project=project)
    if blocked is None:
        call = _rerun_call(
            loaded, project=project, grid=grid, starts=((artifact_id, _SMX_KIND),),
            quality_from=artifact_id, arguments="metrics=['da'], blind_control='lm83'",
            method="2phase",
        )
        # Fix round 1 (review I3): `blind_control='lm83'` plans the zero-bounce direct-sun leg on
        # any chain, so compliance(standard='lm83') reads ASE from 'illdir:<grid>' after it.
        # `metrics=['sda', 'ase']` is not the route: rule 1 of `rad_core.method` then chooses the
        # five-phase chain and refuses method='2phase' (METHOD_OVERRIDE_UNSAFE, measured).
        parts.append(
            "A two-phase run traces every aperture of the scene, claimed by a window group or "
            f"not, and seals 'ill:{grid}', and with blind_control='lm83' the zero-bounce "
            f"direct-sun result 'illdir:{grid}' ASE is read from: {call}; then read it with "
            "chain='2phase'."
        )
    else:
        parts.append(
            f"A two-phase run, which traces every aperture of the scene, would be refused on this "
            f"grid first: {blocked}"
        )
    return " ".join(parts)


def _refuse_sealed_schedule_not_current(
    loaded: LoadedProject, *, project: str, grid: str, edition: Lm83Edition, reports: str,
    blend_id: str,
) -> CallToolResult | None:
    """`BLIND_SCHEDULE_NOT_CURRENT` (error, `field="grid"`), or `None`: the sealed `sched:<grid>`
    decided its hours for window-group declarations the project no longer records (final review,
    W-I1).

    **Keyed on what was sealed** (global constraints): the schedule's `automated_groups` (absent is
    `[]`, the record an unmarked run writes) against `rad_engine.chain.automated_group_names`, and
    its `order` / `order_substituted` against the closing order the current `closing_role` values
    give. Either differing means an hour of the blend, or a state of the published per-group
    schedule, was decided by a rule the project's records no longer state: an automated group takes
    its states from its own schedule and leaves the closing order, and the order decides which
    manual groups close. Neither moves an artifact digest -- a redefine re-registers the same
    files -- so nothing marks the schedule stale, and without this the blend's sDA was published
    beside an ASE_net judged on the new marks.

    **The closing order needs no geometry here.** `rad_core.blinds.closing_order` ranks by role when
    every manual group declares one, and by sill height otherwise; a role edit therefore moves the
    order only when every role is declared now and ranks the groups otherwise than the sealed
    order (computed by `closing_order` itself -- `sill_z` is not read on that branch; a sealed
    substituted order the declared roles rank the same way decided the same hours, and is read),
    or when the sealed order was declared and not every role is declared now. Two substituted
    orders rank by sill height, which no role edit moves; a moved aperture moves the geometry
    digests and is staleness's (`_refuse_stale_results`). A malformed record is left to
    `_sealed_group_schedule`'s guards.

    **The group set is compared first** (final fix A, fix round 2, G1): the groups the schedule
    was sealed for, its `order` and its `automated_groups`, against every registered window group.
    No edge check sees a group defined after the run -- `vmx`, `dmx` and `tmx` name only their own
    group's input and the direct-sun matrices name none, so nothing goes stale -- and a three-phase
    lm83 run fans out over every window group, so a registered group the record does not name was
    defined since. A group removed since the run is refused here too; through the wire
    `window_group(action='remove')` refuses while a sealed matrix names it, so that state is a
    hand-edited manifest. The single-group schedule is compared like any other.

    `reports` is `"compliance"`, `"metrics"` or `"blind_schedule"`, for the wording. Called before
    `_refuse_sealed_edition_mismatch`, whose remedies read the schedule as sealed. No
    `sched:<grid>` registered is nothing to compare, and returns `None`.
    """
    sched_id = blind_schedule_id(grid)
    try:
        record = loaded.manifest.artifact(sched_id)
    except KeyError:
        return None
    raw = record.parameters.get("automated_groups", [])
    sealed_marks = sorted(str(name) for name in raw) if isinstance(raw, list) else []
    current_marks = list(automated_group_names(loaded))

    def named(names: Sequence[str]) -> str:
        return f"window group(s) {', '.join(names)}" if names else "no window group"

    raw_order = record.parameters.get("order")
    current_groups = sorted(
        record_.id.removeprefix(WINDOW_GROUP_INPUT_PREFIX) for record_ in loaded.manifest.inputs
        if record_.kind == "window_group"
    )
    # Final fix A, fix round 2, G1: the groups the schedule was sealed for are its closing order
    # and its automated groups; a malformed order is `_sealed_group_schedule`'s to refuse.
    sealed_groups = (
        sorted({*raw_order, *sealed_marks})
        if isinstance(raw_order, list) and all(isinstance(name, str) for name in raw_order)
        else None
    )
    changed: str | None = None
    added: list[str] = []
    if sealed_groups is not None and sealed_groups != current_groups:
        added = [name for name in current_groups if name not in sealed_groups]
        removed = [name for name in sealed_groups if name not in current_groups]
        notes = [
            *([f"{named(added)} defined since the run"] if added else []),
            *([f"{named(removed)} no longer registered"] if removed else []),
        ]
        consequences = [
            *(["a group defined since the run is in no part of it: the closing order never "
               "included it, no hour decided its blind, and no per-group result was traced "
               "through its aperture"] if added else []),
            *(["a group no longer registered is still in it"] if removed else []),
        ]
        changed = (
            f"{sched_id!r} was sealed for {named(sealed_groups)}, and the project now registers "
            f"{named(current_groups)} ({'; '.join(notes)}). Every hour of a sealed schedule was "
            f"decided over the groups it was sealed for, so {', and '.join(consequences)}"
        )
    elif sealed_marks != current_marks:
        changed = (
            f"{sched_id!r} was sealed with {named(sealed_marks)} marked automated, and the "
            f"project now marks {named(current_marks)}. An automated group "
            "takes its hourly states from its own bound schedule (or is held closed) and is out "
            f"of the closing order, while every other group is closed by {_sealed_trigger(record)}"
        )
    else:
        changed = _closing_order_change(loaded, record, sched_id=sched_id, automated=current_marks)
    if changed is None:
        return None

    if reports == "compliance":
        opening = f"results was asked to judge grid {grid!r} from {blend_id!r}"
        subject = (
            f"{blend_id!r} is a blend whose every hour was decided by {sched_id!r}, and"
        )
        outcome = "nothing is judged from it"
    elif reports == "metrics":
        opening = f"results(action='metrics') was asked to report DA_300 for grid {grid!r}"
        subject = (
            f"{blend_id!r} is a blend whose every hour was decided by {sched_id!r}, and"
        )
        outcome = "no DA_300 is reported from it"
    else:
        opening = (
            f"results(action='blind_schedule') was asked to publish the blind schedule for grid "
            f"{grid!r}"
        )
        subject = f"its per-group states come from {sched_id!r}, and"
        outcome = "no blind schedule is published from it"
    decided = "Its states were" if reports == "blind_schedule" else "Its hours were"
    remedy = _blend_rerun_remedy(loaded, project=project, grid=grid, edition=edition, added=added)
    message = (
        f"{opening}; {subject} {changed}. {decided} decided for declarations the project no "
        f"longer records, so {outcome}. {remedy}"
    )
    logger.info("results refused: %s", message)
    return refuse(message, [Advisory(
        code="BLIND_SCHEDULE_NOT_CURRENT", severity="error", field="grid", message=message,
    )])


def _sealed_trigger(record: Any) -> str:
    """The closing trigger of the edition `sched:<grid>` was sealed under, as a noun phrase (final
    fix A, fix round 1, N3): LM-83-22's one 2 % rule, or LM-83-12's area table, whose rows below
    1 000 sf are point counts -- named whole, since the sealed area is not this sentence's to
    read. A record naming no edition this server reads gets the bare phrase."""
    try:
        edition = sealed_lm83_edition(record.parameters)
    except ValueError:
        return "the closing trigger it was sealed under"
    if edition == LM83_22:
        return f"{section('blind_trigger', LM83_22)}'s 2 % trigger"
    edge = f"{LM83_BLIND_AREA_GENERAL_SF:,.0f}".replace(",", " ")
    return (
        f"{section('blind_trigger', 'LM-83-12')}'s trigger (its area table's point counts below "
        f"{edge} sf, more than 2 % of the points at {edge} sf or more)"
    )


def _declared_order_now(
    loaded: LoadedProject, names: Sequence[str], *, automated: Sequence[str],
) -> tuple[str, ...] | None:
    """The closing order the `closing_role` values recorded now give `names`, or `None` unless
    every one of them is a registered window group, not marked automated, declaring a role, and
    they are exactly the project's unmarked groups. On that branch `rad_core.blinds.
    closing_order` ranks by role and name and never reads `sill_z`, so no geometry is read."""
    manual = {
        record.id.removeprefix(WINDOW_GROUP_INPUT_PREFIX): record
        for record in loaded.manifest.inputs
        if record.kind == "window_group"
        and record.id.removeprefix(WINDOW_GROUP_INPUT_PREFIX) not in set(automated)
    }
    if set(manual) != set(names) or len(set(names)) != len(names):
        return None
    roles = {name: manual[name].role for name in names}
    if not all(role in CLOSING_ROLES for role in roles.values()):
        return None
    return tuple(closing_order(
        [GroupFacts(name=name, role=roles[name], sill_z=0.0) for name in names]
    ).names)


def _closing_order_change(
    loaded: LoadedProject, record: Any, *, sched_id: str, automated: Sequence[str],
) -> str | None:
    """How the closing order the current `closing_role` values give differs from the one
    `sched:<grid>` sealed, or `None` when it cannot have moved -- see
    `_refuse_sealed_schedule_not_current` for why no geometry is read.

    **A substituted order the roles declared now rank the same way is not a change** (final fix
    A, fix round 1, N2, controller ruling): the same order over the same matrices decides the same
    hours, so it is read; `BLIND_ORDER_SUBSTITUTED` then says the roles are declared now and give
    that order (`_order_substituted_notice`)."""
    order = record.parameters.get("order")
    substituted = record.parameters.get("order_substituted")
    if (
        not isinstance(order, list) or not all(isinstance(name, str) for name in order)
        or not isinstance(substituted, bool) or len(set(order)) != len(order) or len(order) < 2
    ):
        return None
    manual = {
        record_.id.removeprefix(WINDOW_GROUP_INPUT_PREFIX): record_
        for record_ in loaded.manifest.inputs
        if record_.kind == "window_group"
        and record_.id.removeprefix(WINDOW_GROUP_INPUT_PREFIX) not in set(automated)
    }
    if set(manual) != set(order):
        return None
    roles = {name: manual[name].role for name in order}
    now = _declared_order_now(loaded, order, automated=automated)
    if now is not None:
        if list(now) == order:
            return None
        current = f"the declared order {', '.join(now)}"
    else:
        if substituted:
            return None
        current = "an order substituted by sill height, since not every group declares one"
    sealed = (
        f"{', '.join(order)}, "
        + ("substituted by sill height" if substituted else "declared by closing_role")
    )
    recorded = ", ".join(f"{name} {roles[name]!r}" for name in order)
    return (
        f"{sched_id!r} was sealed with the closing order {sealed}, and the closing_role values "
        f"the window groups record now ({recorded}) give {current}. The closing order decides "
        f"which groups {_sealed_trigger(record)} closes first"
    )


def _blend_advisories_under(state: _Resolved, edition: Lm83Edition) -> list[Advisory]:
    """`state.blend_advisories` as an LM-83 read under `edition` publishes them.

    `_resolved_from` computes them once, under no edition, for every action; under LM-83-22 the
    `BLIND_TRIGGER_MIXED_BASIS` entry is asked again of `_mixed_basis_blend_advisories` with the
    edition, which holds the rule (design section 2, row 10), and its answer -- nothing, or the
    finding naming LM-83-12's table -- takes the entry's place. Every other blend finding is kept,
    in order; the actions that take no edition publish `state.blend_advisories` unchanged.
    """
    if edition != LM83_22 or not state.blended:
        return list(state.blend_advisories)
    published: list[Advisory] = []
    for advisory in state.blend_advisories:
        if advisory.code != "BLIND_TRIGGER_MIXED_BASIS":
            published.append(advisory)
            continue
        published.extend(_mixed_basis_blend_advisories(
            loaded=state.loaded, artifact_id=state.artifact_id, header=state.header,
            edition=edition, project=state.project,
        ))
    return published


def _recorded_sky_subdivision(loaded: LoadedProject, published: Sequence[str]) -> int | None:
    """The smallest sky subdivision (`parameters["mf"]`) any artifact in `published` or in the
    transitive `built_from` closure of them records, or `None` when none records one.

    Every matrix `rad_engine.chain` builds over a sky records the run's `mf` -- the sky and sun
    matrices, the daylight-coefficient and direct-sun matrices and the two-phase result -- so the
    smallest is the subdivision the chain was built at. A recorded value that is not an integer
    (a hand-edited manifest, R50) is skipped rather than read as one. The walk is
    `_sealed_ase_matrix_advisories`', bounded by `seen` over a hand-edited cycle.
    """
    seen: set[str] = set(published)
    frontier: list[str] = list(published)
    found: list[int] = []
    while frontier:
        current_id = frontier.pop()
        try:
            artifact = loaded.manifest.artifact(current_id)
        except KeyError:
            continue
        mf = artifact.parameters.get("mf")
        if isinstance(mf, int) and not isinstance(mf, bool):
            found.append(mf)
        for edge in artifact.built_from:
            if edge not in seen:
                seen.add(edge)
                frontier.append(edge)
    return min(found) if found else None


def _sky_subdivision_below_floor_advisory(
    mf: int, *, edition: Lm83Edition, schedule: bool = False,
) -> Advisory:
    """`LM83_SKY_SUBDIVISION_BELOW_FLOOR` (warn): the judged chain was built below LM-83's sky
    floor (R866, design section 5).

    `schedule` words it for `results(action="blind_schedule")` (final review, W7): the schedule is
    an LM-83 result too -- the edition's blind operation, scanned off the direct solar contribution
    LM-83-22 section 4.12.1 states its floor for -- so it carries the same finding.

    `run_annual` raises every LM-83 run -- `sda` or `ase` in `metrics`, or
    `blind_control="lm83"` -- to `LM83_MIN_SKY_SUBDIVISION` (4). A blend sealed before R866 counted
    `blind_control="lm83"` as an LM-83 run, or a matrix built by a run that was not an LM-83 run,
    can still carry a lower subdivision, and nothing rebuilds it automatically. This states the
    fact; whether such a chain should be refused instead is not decided (design section 5).
    """
    clause = section("sky_patches", edition)
    # Round 2: under LM-83-22 the re-run names the edition, or it reseals the schedule under
    # LM-83-12 and the next LM-83-22 read is refused `LM83_EDITION_MISMATCH`. Round 3: and it says
    # the re-run must be an LM-83 run, since run_annual refuses lm83_edition on any other (a
    # five-phase `metrics=["da"]` chain is not one).
    rerun = (
        "Re-run run_annual for this grid as an LM-83 run (sda or ase in metrics, or "
        f"blind_control='lm83') with lm83_edition={LM83_22!r} and "
        f"sky_subdivision={LM83_MIN_SKY_SUBDIVISION} or omitted, which raises it to "
        f"{LM83_MIN_SKY_SUBDIVISION}."
        if edition == LM83_22 else
        "Re-run run_annual for this "
        f"grid with sky_subdivision={LM83_MIN_SKY_SUBDIVISION}, or with sky_subdivision "
        "omitted on an LM-83 run (sda or ase in metrics, or blind_control='lm83'), which "
        f"raises it to {LM83_MIN_SKY_SUBDIVISION}."
    )
    requirement = (
        f"{clause} asks for no fewer than {LM83_MIN_REINHART_PATCHES:,} sky patches for the "
        "direct solar contribution, which this server's suns share with the sky"
        if edition == LM83_22
        else f"{clause} requires a minimum of {LM83_MIN_REINHART_PATCHES:,} Reinhart sky patches"
    )
    return Advisory(
        code="LM83_SKY_SUBDIVISION_BELOW_FLOOR",
        severity="warn",
        message=(
            (
                f"this blind schedule comes from direct-sun matrices built at sky subdivision {mf} "
                f"({reinhart_row_count(mf) - 1} sky patches), the smallest any matrix behind it "
                f"records, and {requirement}, so it is not an LM-83 schedule. A chain sealed before "
                if schedule else
                f"this sDA and ASE come from a chain built at sky subdivision {mf} "
                f"({reinhart_row_count(mf) - 1} sky patches), the smallest any matrix behind them "
                f"records, and {requirement}, so they are not LM-83 results. A chain sealed before "
            ) +
            "run_annual raised a blind_control='lm83' run to subdivision "
            f"{LM83_MIN_SKY_SUBDIVISION}, or built by a run that was not an LM-83 run, can carry a "
            f"lower subdivision, and nothing rebuilds it automatically. {rerun}"
        ),
    )


def _refuse_ase_net_not_current(
    state: _Resolved, *, project: str, grid: str, current: tuple[str, ...],
    net_record: Any | None, direct_sealed: bool, edition: Lm83Edition,
) -> CallToolResult:
    """`ASE_NET_NOT_CURRENT` (error, `field="grid"`): a window group is marked automated and no
    `illdirnet:<grid>` sealed with exactly the groups marked now stands beside `illdir:<grid>`
    (R869).

    Three states, each named as it is: no net result sealed; one sealed with other groups blacked
    (an earlier run's marks); or one sealed with these groups while ASE itself fell back to the
    chain result because no `illdir:<grid>` is registered. The remedy is the LM-83 run that plans
    the direct-sun leg, which seals both halves with the groups marked now; under LM-83-22 it
    names the edition so a blind schedule it reseals is not sealed under LM-83-12. Refused under
    both editions: `ase_net` is published under both, and a value counted with the wrong groups
    blacked would be a different quantity under the same name.

    **The route offered is the judged result's own chain** (final review, W-M4): `'ase'` in
    `metrics` always infers five-phase, which a group binding two states refuses
    (`BSDF_DIRECT_SUN_NOT_BOUND`), so it is not offered beside a blend. A blend
    (`illblend:<grid>`) is re-run as the three-phase `blind_control='lm83'` run that sealed it
    (`_rerun_blend_call`; no read reaches this with a grid that run would refuse, J3); a
    five-phase result with `metrics=['sda', 'ase']`; a two-phase result with `blind_control='lm83'`; and an unblended
    three-phase result, which no run that plans the leg seals as it is, with both routes and the
    binding each needs.
    """
    net_id = direct_ill_net_id(grid)
    sealed = None if net_record is None else net_record.parameters.get("ase_net_blacked")
    if net_record is None:
        found = "no direct-sun result with them blacked is sealed for this grid"
    elif sealed == list(current) and not direct_sealed:
        found = (
            f"{net_id!r} was sealed with them blacked, but 'illdir:{grid}', the direct-sun result "
            "with every aperture open that ASE is counted from beside it, is not registered"
        )
    else:
        blacked = (
            ", ".join(str(name) for name in sealed)
            if isinstance(sealed, list) and sealed else "no group"
        )
        found = f"{net_id!r} was sealed with {blacked} blacked"
    edition_clause = (
        f", with lm83_edition={edition!r}" if edition == LM83_22 else ""
    )
    if state.blended:
        # Final fix F, fix round 4 (J3): no "it would be refused on this grid first" branch. This
        # read resolves through `_resolve`, whose `_refuse_grid_off_recorded_floor` refuses every
        # grid state `_rerun_blocked_by_grid` reads (a changed, missing, unreadable or off-floor
        # `.pts`) before this runs; the rest need a hand-edited manifest, which is stale first.
        first = _bind_first_clause(state.loaded, project=project)
        remedy = (
            f"{first}{'r' if first else 'R'}e-run the three-phase run that sealed this blend, "
            f"{_rerun_blend_call(state.loaded, project=project, grid=grid, edition=edition)}: "
            "it plans the direct-sun leg and seals it with the groups marked now."
        )
    elif state.method_used == _LM83_ASE_REQUIRES_METHOD:
        remedy = (
            "Re-run run_annual for this grid with metrics=['sda', 'ase'], the five-phase run "
            f"that sealed this result{edition_clause}; it plans the direct-sun leg and seals it "
            "with the groups marked now."
        )
    elif state.method_used == "2phase":
        remedy = (
            "Re-run run_annual for this grid with blind_control='lm83', with which a two-phase "
            f"run plans the direct-sun leg{edition_clause}; it seals the leg with the groups "
            "marked now."
        )
    else:
        remedy = (
            "Re-run run_annual for this grid as an LM-83 run that plans the direct-sun leg"
            f"{edition_clause}: blind_control='lm83' once every window group binds an 'open' and "
            "a 'closed' state, or metrics=['sda', 'ase'] (the five-phase chain) on a project with "
            "one window group whose states also carry a direct_sun BSDF; it seals the leg with "
            "the groups marked now."
        )
    message = (
        f"ASE_net for grid {grid!r} cannot be judged: window group(s) {', '.join(current)} are "
        f"marked automated, and {found}. {remedy}"
    )
    logger.info("results(action='compliance', standard='lm83') refused: %s", message)
    return refuse(message, [Advisory(
        code="ASE_NET_NOT_CURRENT", severity="error", field="grid", message=message,
    )])


@dataclass(frozen=True)
class _AseNet:
    """What `lm83.ase_net` was counted from, for `_ase_band_advisory`'s wording (R869).

    `blacked` is the sealed `ase_net_blacked` when `illdirnet:<grid>` was read, else empty;
    `groups_defined` says whether the project defines any window group at all, so a project with
    groups and no mark is not told it has none. `from_chain` says ASE (and so ASE_net, with no
    group marked) was counted from the five-phase chain result because no `illdir:<grid>` is
    registered, so the wording does not call it a direct-sun count (final review, W-M3)."""

    value: float
    artifact: str
    blacked: tuple[str, ...]
    groups_defined: bool
    from_chain: bool = False


def _ase_band_advisory(
    edition: Lm83Edition, *, ase: tuple[float, str] | None = None, net: _AseNet | None = None,
) -> Advisory:
    """What the ASE label on this result is and is not (R865, R869).

    Under LM-83-12, `ASE_BAND_IS_RESEARCH`: its four bands are supporting research, per space,
    judged on `ase`, `(value, artifact)`. Under LM-83-22, `ASE_NET_LEVEL_OF_CONCERN` in its place:
    section 3.2 states its 20 % line for ASE_net, and the band is judged on `ase_net` -- counted
    from `illdirnet:<grid>` with the automated groups blacked, or equal to ASE_gross because no group is
    marked automated (two wordings: groups with no mark, and no group at all). The 20 % boundary is
    a value not yet checked against the printed standard. `ASE_LEVEL_OF_CONCERN_ON_GROSS`, which said the label was
    judged on ASE_gross, is retired by R869. `ase` and `net` are required under LM-83-22.
    """
    if edition == LM83_22:
        if ase is None or net is None:  # pragma: no cover - `_compliance_lm83` passes both
            raise ValueError("the LM-83-22 ASE label needs ASE and ASE_net")
        clause = section("ase_bands", edition)
        if net.blacked:
            judged = (
                f"{net.value:.1f} %, counted from {net.artifact!r}, the direct-sun result with the "
                f"window group(s) marked automated ({', '.join(net.blacked)}) blacked; ASE_gross "
                f"with every aperture open is {ase[0]:.1f} % ({ase[1]!r})"
            )
        elif net.groups_defined:
            counted = (
                f"counted from {net.artifact!r}, the five-phase chain result ASE fell back to "
                "(ASE_NOT_FROM_DIRECT_ONLY_RUN)"
                if net.from_chain else
                f"counted with every aperture open from {net.artifact!r}"
            )
            judged = (
                f"which equals ASE_gross here, {net.value:.1f} % {counted}, because no window "
                "group of this project is marked automated"
            )
        else:
            judged = (
                f"which equals ASE_gross here, {net.value:.1f} % from {net.artifact!r}, because "
                "this project defines no window group, so no aperture can be marked automated"
            )
        return Advisory(
            code="ASE_NET_LEVEL_OF_CONCERN",
            severity="info",
            message=(
                f"{clause} states the 20% line for ASE_net, \"an ASE_net of 20% or higher has been "
                "determined to be a level of concern\"; the 20% boundary is not yet checked against the "
                f"printed standard. The band here is judged on ASE_net ({judged}). ASE values "
                f"are per space ({clause})."
            ),
        )
    return Advisory(
        code="ASE_BAND_IS_RESEARCH",
        severity="info",
        message=(
            "the ASE bands (< 3% clearly acceptable, < 7% neutral, > 10% unsatisfactory) are "
            "LM-83's own supporting research, not a normative threshold, and are reported per "
            "space: the research spaces averaged ~1,200 sf and were rarely more than 40 ft from "
            "a window group (ch04 section 3.2.4), so a value computed over a whole floor plate "
            "has no interpretive basis under this row."
        ),
    )


def _compliance_lm83_edition_advisory(edition: Lm83Edition) -> Advisory:
    """`LM83_EDITION` on an LM-83 compliance result: the edition `lm83.edition` reports (R864).

    Under the default it says what it said, except that LM-83-22 is no longer "not a selectable
    edition here": it says how to select it. Under LM-83-22 it names the edition's source
    document, its period and ASE-line clauses, that ASE_net is judged with the automated groups
    blacked (R869), and the LM-83-22 rules this server does not apply -- `run_annual`'s list, the
    one sentence `rad_core.lm83_edition.LM83_22_NOT_APPLIED` (Still open items 48, 49, 51, 52 and
    54, and the two rules R871 and R870 left unwired).
    """
    if edition == LM83_22:
        message = (
            "the thresholds, the 08:00-18:00 period and the ASE line applied here are "
            f"{edition_title(edition)}'s (lm83_edition='LM-83-22'), the edition `lm83.edition` "
            "reports on this result, cited from ANSI/IES LM-83-22, by "
            f"section number: analysis period {section('period', edition)}, ASE line "
            f"{section('ase_bands', edition)} (its 20 % boundary not yet checked "
            "against the printed standard). ASE_net is judged with the window groups marked automated blacked. "
            f"{LM83_22_NOT_APPLIED} -- state the edition when reporting."
        )
    else:
        message = (
            "the thresholds, the 08:00-18:00 period and the bands applied here are IES "
            "LM-83-12's -- the default edition, and the one `lm83.edition` reports on this "
            "result. ANSI/IES LM-83-22 supersedes it and can be chosen with "
            "lm83_edition='LM-83-22'; its revised parameters are not applied to this result, and "
            "a report that does not state the edition reads as current (ch04: \"Flag the edition "
            "whenever reporting\")."
        )
    return Advisory(code="LM83_EDITION", severity="info", message=message)


def _compliance_lm83(
    runtime: Runtime, state: _Resolved, *, project: str, grid: str, metrics: list[str] | None,
    edition: Lm83Edition = DEFAULT_LM83_EDITION, named: bool = False,
) -> CallToolResult | ResultsCompliance:
    """`action="compliance"`, `standard="lm83"`: sDA and ASE together, from `rad_core.metrics`'
    tallies and `rad_core.compliance`'s bands.

    **Returns the result MODEL on success and a shaped `CallToolResult` on a refusal** (Phase 7c
    Task 17). The single-result dispatch wraps the model with `emit`; `_compliance_by_chain`
    re-shapes it into a `by_chain` entry. Splitting the envelope off is what lets both shapes come
    from this one function rather than from two implementations that agree today -- and it is
    specifically what carries the refusal **ordering** below into a `by_chain` entry unchanged,
    which is ruling F11's carried concern. No arithmetic lives here -- this builds the per-point
    `PointTally` objects the matrices stream into and hands them to the pure layer, then aggregates
    the counters `PointTally.observe` already kept (never re-deriving what a cell's sign means).

    **Two matrices, one call, and each metric reads the one its own standard defines it over**
    (Phase 7c Task 13). sDA comes from the chain result `_resolve` selected; ASE comes from
    `illdir:<grid>`, the dedicated pre-blind, zero-bounce direct-sun run Task 11 seals -- LM-83
    ch04 §3.3 defines ASE as the direct beam from the solar orb alone, after glazing transmittance,
    and says it *"does not require modeling of sky luminance or ground reflectance"*. 7b tallied it
    from `V*T*D*S - Vd*T*Dd*Sds + Cds*Ssun`, which carries sky and inter-reflected light in every
    cell; the computation guide calls feeding that into the ASE formula wrong twice over.

    **Which matrix ASE is tallied from is decided by the manifest, never by `method_used`.**
    `run_annual` plans the direct leg whenever `"ase" in metrics` **or**
    `blind_control == "lm83"`, and its whole-grid half, which seals `illdir:<grid>`, is planned
    beside every chain (`chain.direct_sun_steps` keys only its per-group legs on the chain), so
    `illdir:<grid>` is reachable on a two- or three-phase run. When one is registered ASE is its
    number; when none is, ASE falls back to the chain result -- published with
    `ASE_NOT_FROM_DIRECT_ONLY_RUN` for a five-phase chain (7b's behaviour, unchanged) and refused
    with `ASE_FROM_SMEARED_SUN` for anything else.

    **`metrics` is validated by `_validate_lm83_metrics` before this function is ever called** (it
    runs before `_resolve`), so by the time control reaches here the request is for both sDA and
    ASE. Every refusal below then runs from headers and manifest records alone, and **all of them
    sit above every allocation sized from either header** (R39): `lm83_window` builds a frozenset
    over `range(hours)` and each tally list is one `PointTally` per point, so both integers of both
    matrices are allocation inputs and neither matrix's are trusted until its own guards have run.

    **That sentence was false of the two lines below it until the whole-branch fix wave**, and it
    is recorded here rather than quietly corrected. `lm83_window(state.header.hours)` was built one
    line *above* the `is_whole_year` gate that refuses exactly the inputs that make it expensive
    (`lm83_window(8_000_000)`: 0.64 s, ~342 MB, measured). The two are now in the stated order, and
    the header integers are additionally bounded in `_ensure_header_matches_manifest`
    (`MATRIX_TOO_LARGE_TO_READ`) so the claim does not depend on one function's line order.

    Order, direct matrix first:

    1. no `illdir:` and a chain result that smeared the sun -> `ASE_FROM_SMEARED_SUN`.
    2. `illdir:`'s own header against its own manifest record -> `MATRIX_SHAPE_MISMATCH`, via
       `_contained_matrix`, which is also where its path is contained (R50).
    3. `is_whole_year(illdir.hours)` -> `ANALYSIS_PERIOD_NOT_ANNUAL`. R104 applies to the matrix
       ASE is *actually tallied from*: a 48-hour direct matrix can never reach 251 hours, so ASE is
       structurally `0.0` and `ase_band(0.0)` is `"clearly_acceptable"` -- a verdict guaranteed by
       the period rather than earned by the space.
    4. the two matrices declare the same `NROWS`/`NCOLS` -> `ASE_DIRECT_SHAPE_DISAGREES`.
    5. R869, ASE_net: with a window group marked automated, `illdirnet:<grid>` sealed with
       exactly the groups marked now (`ase_net_blacked`) beside `illdir:<grid>`, else
       `ASE_NET_NOT_CURRENT`; then 2 to 4 again on it, through the same local function. With no
       group marked, ASE_net is ASE and `illdirnet:<grid>` is not read. `lm83.automated_groups` is
       the sealed `ase_net_blacked`; under LM-83-22 the `ase` band is judged on ASE_net
       (`bands.ase_judged`), under LM-83-12 on ASE.

    **3 sits above 4, and the task brief numbered them the other way round.** Under 4-then-3 the
    whole-year check could not fire on any state the shape check had not already caught: agreement
    forces `direct.hours == state.header.hours`, so `not is_whole_year(direct.hours)` implies the
    chain's own R104 refusal further down, with the identical code. The brief's own test for it --
    a 48-hour direct matrix beside an 8 760-hour chain result -- would have answered
    `ASE_DIRECT_SHAPE_DISAGREES`. Swapped, each refusal reports a state the other cannot reach.

    **The empty-denominator check stays above the chain's whole-year one, deliberately.** An empty
    window and a sub-annual window are both refusals, but they are different facts about the matrix
    and the emptier one is the more specific: a 3-hour micro-scene has *no* 08:00-18:00 column at
    all, which is worth saying plainly rather than folding into "not a year". Every matrix the
    second check refuses has a real, non-empty window -- which is exactly what makes its bands look
    plausible, and why it needs its own refusal (`_refuse_sub_annual_matrix`).

    **`edition` is the call's `lm83_edition` (R864, R865, R866)**: the ASE label (`ase_band`), the
    latitude band and every clause a message names come from it, and `lm83.edition` publishes it.
    Above the four refusals, a blend whose schedule was sealed for window-group declarations the
    project no longer records, or under the other edition below 1 000 sf or at any area with a
    bound automated schedule (R870), is refused (`_refuse_edition_mismatch`, manifest and grid
    only). On a published result, a chain
    any of whose matrices records a sky subdivision below 4 carries
    `LM83_SKY_SUBDIVISION_BELOW_FLOOR`.
    """
    refusal = _refuse_edition_mismatch(
        state, project=project, grid=grid, edition=edition, named=named, reports="compliance",
    )
    refusal = refusal if refusal is not None else _refuse_window_groups_not_current(
        state, project=project, grid=grid, reads="compliance", edition=edition,
    )  # final fix E1, after BLIND_SCHEDULE_NOT_CURRENT, the more specific
    if refusal is not None:
        return refusal
    direct_id = f"{_DIRECT_ARTIFACT_PREFIX}{grid}"
    try:
        direct_artifact: Any | None = state.loaded.manifest.artifact(direct_id)
    except KeyError:
        direct_artifact = None

    def checked_direct(
        artifact_id: str, artifact: Any, metric: str,
    ) -> CallToolResult | tuple[Path, AnnualMatrix]:
        """One direct-sun matrix opened and checked -- `illdir:<grid>` for ASE and, R869,
        `illdirnet:<grid>` for ASE_net, by this one function rather than a second copy (R30)."""
        # R785: `_resolve` checked the result this call resolved, not the direct matrix, which is
        # read here and is built from the scene and the grid as `ill:<grid>` is.
        _refuse_stale_results(state.loaded, [artifact_id])
        _refuse_changed_scene_includes(  # final fix E, fix round 4 (X1)
            state.loaded, [artifact_id], project=project, grid=grid,
            rerun_for=state.artifact_id, edition=edition,
        )
        # **The one way this module opens a manifest path** (`_contained_matrix`, Task 12):
        # containment before the syscall, then the header, then the header/record cross-check --
        # applied to the direct matrix by calling the same function, not by reproducing it.
        path, header = _contained_matrix(runtime, artifact, artifact_id=artifact_id)
        if not is_whole_year(header.hours):
            return _refuse_sub_annual_matrix(
                state, grid=grid, standard="LM-83",
                direct=(artifact_id, header.hours), edition=edition, metric=metric,
            )
        if header.points != state.header.points or header.hours != state.header.hours:
            return _refuse_direct_shape_disagrees(
                state, grid=grid, direct_id=artifact_id, direct=header, edition=edition,
                metric=metric,
            )
        return path, header

    direct_path: Path | None = None
    direct_header: AnnualMatrix | None = None
    if direct_artifact is None:
        if state.method_used != _LM83_ASE_REQUIRES_METHOD:
            return _refuse_ase_smeared(state, grid=grid, edition=edition)
    else:
        opened = checked_direct(direct_id, direct_artifact, "ASE")
        if isinstance(opened, CallToolResult):
            return opened
        direct_path, direct_header = opened

    # R869: ASE_net. With no group marked automated it is ASE and `illdirnet:<grid>` is not read
    # (one left by an earlier marked run describes marks the project no longer has). With a mark,
    # the net result must have been sealed with exactly the groups marked now, beside `illdir:`;
    # what was blacked is then read off the sealed record, not off the current marks.
    current = automated_group_names(state.loaded)
    net_id = direct_ill_net_id(grid)
    net_artifact: Any | None = None
    net_path: Path | None = None
    net_header: AnnualMatrix | None = None
    if current:
        try:
            net_artifact = state.loaded.manifest.artifact(net_id)
        except KeyError:
            net_artifact = None
        if (
            direct_header is None or net_artifact is None
            or net_artifact.parameters.get("ase_net_blacked") != list(current)
        ):
            return _refuse_ase_net_not_current(
                state, project=project, grid=grid, current=current, net_record=net_artifact,
                direct_sealed=direct_header is not None, edition=edition,
            )
        opened = checked_direct(net_id, net_artifact, "ASE_net")
        if isinstance(opened, CallToolResult):
            return opened
        net_path, net_header = opened

    # **`is_whole_year` FIRST, and `lm83_window` after it.** The two were the other way round, and
    # the order was the defect: `lm83_window(n)` builds a frozenset over `range(n)` -- measured,
    # `lm83_window(8_000_000)` costs 0.64 s and ~342 MB -- and `is_whole_year` then returns `False`
    # for exactly the inputs that make it expensive. R39's rule is that a refusal sits above the
    # allocation it prevents, and this one sat one line below it. `A3`'s magnitude bound in
    # `_ensure_header_matches_manifest` now caps the input as well; both stay, because the bound
    # guards every caller and the order guards this one against a bound that ever moves.
    if not is_whole_year(state.header.hours):
        return _refuse_sub_annual_matrix(state, grid=grid, standard="LM-83", edition=edition)

    # R860: on the clock of the zone the run's weather input records, when it records one. ASE's
    # direct matrix is tallied over this same set below, so sDA and ASE share one window.
    placed = _lm83_window_for(state.loaded, state.artifact_id, state.header.hours)
    window = placed.columns
    # **Two conditions, two `if`s, and the split is whole-branch test finding F8.** As one
    # `len(window) == 0 or state.header.points == 0`, widening the `or` to `and` left
    # `tests/test_results_tool.py` at **507 passed** -- because the two disjuncts were only ever
    # exercised together and nothing in 5 301 tests stood on the points half alone. Under `and`, a
    # matrix declaring `NROWS=0` with an ordinary 8 760-column year is *not* refused: `window` is
    # non-empty, the guard does not fire, `tallies` is built as `[... for _ in range(0)]`, and
    # `_compliance_lm83` publishes an LM-83 verdict over a grid of no points.
    #
    # That is not an isolated line. It is the same guard cited when finding F1 -- `rad_engine.blend`
    # sealing a zero-row matrix at exit 0 -- was calibrated down from Critical, on the reasoning
    # that *"the protocol layer's own guards catch the ones that would compute a number"*. Held
    # only jointly, the two findings compose: a `NROWS=0` blend could be **produced** with a zero
    # exit status and then **read and reported on**, each layer green on its own tests.
    #
    # Split, neither condition can hide behind the other and each has its own test
    # (`test_the_empty_point_set_refusal_never_reads_the_matrix`,
    # `test_an_empty_lm83_denominator_is_still_refused_and_still_code_less`).
    if len(window) == 0:
        # **Unreachable behind `is_whole_year` above, and kept deliberately.** A whole year always
        # contains 08:00-18:00 columns, so `lm83_window` cannot come back empty for anything that
        # reaches this line -- which is *why* the `or` was survivable in this direction and why the
        # honest thing is to say so rather than to leave a reader thinking both halves are live.
        # It stays for the reason `_lm83_expected_points_or_none`'s docstring gives: this function's
        # denominator is its own contract, and a guard deleted because a *different* predicate
        # currently implies it is a guard held by that predicate never moving.
        return _refuse_empty_lm83_denominator(state, grid=grid, window=window)
    if state.header.points <= 0:
        # `<= 0`, not `== 0`: `points` is an integer read out of a manifest-declared header, which
        # R50 holds untrusted, and a negative one is as empty a denominator as zero is. The same
        # comparison `_blind_schedule` and `_refuse_unschedulable_direct_matrix` already make.
        return _refuse_empty_lm83_denominator(state, grid=grid, window=window)

    tallies = [
        PointTally(window, thresholds=(LM83_SDA_THRESHOLD_LUX, LM83_ASE_THRESHOLD_LUX))
        for _ in range(state.header.points)
    ]
    rows = _lux_rows(
        state.path, artifact_id=state.artifact_id, expected_points=state.expected_points,
        content_digest=state.content_digest,
    )
    for index, lux in enumerate(rows):
        tally = tallies[index]
        for hour, value in enumerate(lux):
            tally.observe(hour, value)

    # **A second, independent tally set over the direct matrix, carrying only ASE's threshold.**
    # `PointTally` maintains one counter pair per declared threshold, and nothing on this path asks
    # the direct matrix an sDA question -- 300 lux of direct beam is not 300 lux of daylight, and a
    # counter nothing reads is a number a later reader could mistake for one that was defined.
    # The two matrices agree in shape by the guard above, so `window` is the same set for both.
    def direct_tallies(
        path: Path, header: AnnualMatrix, artifact_id: str, artifact: Any,
    ) -> tuple[list[PointTally], bool]:
        """ASE's tally set over one direct-sun matrix, and whether every cell was zero -- the one
        tally for `illdir:<grid>` and `illdirnet:<grid>` alike (R869)."""
        all_zero = True
        found = [
            PointTally(window, thresholds=(LM83_ASE_THRESHOLD_LUX,))
            for _ in range(header.points)
        ]
        rows = _lux_rows(
            path, artifact_id=artifact_id, expected_points=artifact.parameters.get("n_points"),
            content_digest=artifact.content_digest,
        )
        for index, lux in enumerate(rows):
            tally = found[index]
            if all_zero and any(lux):
                all_zero = False
            for hour, value in enumerate(lux):
                tally.observe(hour, value)
        return found, all_zero

    #: Whether every cell of the direct matrix is exactly zero -- `None` when there is no direct
    #: matrix to say it of. Tracked on the pass the tally already makes, with `any(lux)` per row
    #: rather than a branch per cell: one C-level scan over a list the loop below already holds,
    #: against 3.1 M Python comparisons at the office grid. See `ASE_DIRECT_MATRIX_ALL_ZERO` below.
    direct_all_zero: bool | None = None
    if direct_header is None or direct_path is None:
        ase_tallies = tallies
    else:
        ase_tallies, direct_all_zero = direct_tallies(
            direct_path, direct_header, direct_id, direct_artifact,
        )
    net_tallies: list[PointTally] | None = None
    if net_header is not None and net_path is not None:
        net_tallies, _ = direct_tallies(net_path, net_header, net_id, net_artifact)

    try:
        sda_value = spatial_daylight_autonomy(
            tallies, threshold_lux=LM83_SDA_THRESHOLD_LUX, window=window,
            fraction=LM83_DAYLIT_FRACTION,
        )
        ase_value = annual_sunlight_exposure(
            ase_tallies, threshold_lux=LM83_ASE_THRESHOLD_LUX, window=window
        )
        net_value = ase_value if net_tallies is None else annual_sunlight_exposure(
            net_tallies, threshold_lux=LM83_ASE_THRESHOLD_LUX, window=window
        )
    except ValueError as exc:
        # **Defence in depth**, kept even though the guard above now makes the empty-window and
        # empty-tallies cases unreachable here: a future change to what `rad_core.metrics` treats
        # as an empty denominator would otherwise reach the wire as a stack trace with nothing left
        # to catch it, which is the one outcome this ruling forbids.
        logger.info("results(action='compliance', standard='lm83') refused: %s", exc)
        return refuse(
            f"LM-83 compliance for grid {grid!r} could not be computed: {exc} No advisory code in "
            "the register names this condition, so this refusal carries none."
        )

    # **Over `tallies`, which is the CHAIN result's tally set, and never over `ase_tallies`.**
    # `NEGATIVE_ILLUMINANCE_PRESENT`'s allocated condition is the five-phase combination's own
    # subtraction -- `Vd*T*Dd*Sds` exceeding `V*T*D*S` -- which is a property of the chain result.
    # `illdir:<grid>` is `asemx x smxsun` and subtracts nothing, so that row's definition is not
    # true of it, and a code whose definition is untrue of its condition is a defect in its own
    # right (docs/tool-surface.md section 9). The two counts are therefore **not merged**: adding
    # the direct matrix's negatives here would publish one number that describes neither matrix.
    # The tail below names which matrix its count is over, and says the other was tallied
    # separately, so a reader is never left to assume it covers both.
    #
    # Everything shared with `_metrics`' copy -- the aggregation, the percentage, the mechanism
    # ternary and the message prefix -- lives in `_negative_cell_advisory`. There used to be two.
    # Since R814 the sentence about what a negative cell does to a threshold tally is shared too
    # (`_negative_count_advisory`); it used to say the tallies here were "unaffected".
    # **`state.blend_advisories` first, so a stale blend is labelled on every action that
    # reads one** (ruling R191). Empty for every unblended result and for a blend whose
    # schedule still matches, so this changes no payload that was not already a blend.
    advisories: list[Advisory] = _blend_advisories_under(state, edition)
    negative = _negative_cell_advisory(
        state, tallies,
        tail=(
            f"ASE was tallied from {direct_id!r}, a separate matrix, and its cells are NOT "
            "counted in this figure."
            if direct_header is not None
            else ""
        ),
        ase_from_this_result=direct_header is None,
    )
    if negative is not None:
        advisories.append(negative)

    if direct_all_zero:
        # **The LEGITIMATE all-zero case, published and labelled rather than refused** -- Phase 7c
        # Task 14, and a reversal of that task's own brief, which would have refused here. Task 1
        # measured the counter-example: on the micro fixture with its committed weather, a
        # transmitting `void glass` aperture, a valid octree, a non-zero `asemx` (465 of 2 306 bins)
        # and a non-zero `smxsun` (3 non-zero rows) multiplied to an identically zero `illdir`,
        # five runs out of five, because their non-zero bin sets do not intersect. Refusing that
        # would refuse a correct `ASE = 0 %` on every deep room and every high-latitude winter
        # study.
        #
        # **The degenerate cause is caught somewhere else, and that is why this can be `info`.** An
        # aperture polygon whose Radiance modifier does not transmit makes the COEFFICIENT matrix
        # `asemx:<grid>` all-zero, which carries no weather at all, and
        # `rad_engine.project.register_artifact` refuses to seal one (`ASE_DIRECT_MATRIX_EMPTY`,
        # `error`) -- so the run fails at the step that built it and no `illdir:` is ever
        # multiplied.
        #
        # **That sentence gained one exception on 2026-08-23 (Phase 7d-1 Task 18, ruling R143), and
        # the message below names it.** `register_artifact` now SEALS an all-zero `asemx` when none
        # of the Reinhart dome directions the matrix was built over lies in the aperture's exterior
        # hemisphere -- reachable only within the dome's lowest-ring angle of straight down, 6
        # degrees at mf 1 -- carrying
        # `ASE_DIRECT_ALL_ZERO_NO_SUN` on the `asemx` artifact. So "no `illdir:` is ever multiplied"
        # is true of the opaque-aperture case this comment is about and is no longer true without
        # qualification; the seal case is a *correct* all-zero, which is why this code stays `info`
        # either way.
        #
        # **A second exception since ruling R831**: an all-zero `asemx` whose occlusion probe found
        # that no sun reaches the grid -- a courtyard or lightwell -- seals too, carrying
        # `ASE_DIRECT_ALL_ZERO_OCCLUDED`, and the message names both. Since ruling R856 a two-phase
        # run's probe seals too, with every glazing surface removed rather than a window group's
        # aperture opened (it has no window group), so the message names both probes and asks for
        # the sun path to be checked against the openings, not "the aperture" (Phase 24 final
        # review: measured on a two-phase courtyard run, where neither existed).
        #
        # This module makes no second attempt at that check: re-reading `asemx` here
        # would be ~3.1 M floats on every `compliance` call to re-derive a fact the run already
        # knew, and `asemx` is an `rcontrib` coefficient matrix with no `NROWS` in its header, so
        # `iter_annual_lux_rows` cannot read it in the first place.
        advisories.append(Advisory(
            code="ASE_DIRECT_MATRIX_ALL_ZERO",
            severity="info",
            message=(
                f"every cell of {direct_id!r}, the direct-sun matrix ASE was tallied from, is "
                "exactly zero: no sensor on this grid received any direct sun in any hour of the "
                f"matrix. ASE is therefore 0.0% and its band {ase_band(0.0, edition=edition)!r} "
                f"-- the most favourable verdict {edition} has -- and the space did not have to "
                "earn it. That can "
                "be entirely correct: a deep room whose aperture cannot admit a high summer sun, "
                "or a high-latitude winter study, genuinely receives no direct beam on the "
                "workplane. Measured on this repo's own micro fixture, a 2 m deep room with an "
                "0.8 m window admits at most 48.8 degrees above the workplane while the June suns "
                "sit at 48.9 and 74.2 degrees. The degenerate cause -- an aperture polygon whose "
                "Radiance modifier does not transmit, so every shadow ray dies at the window -- "
                "makes the coefficient matrix "
                f"'asemx:{grid}' all-zero instead, and that is refused at build time with "
                "ASE_DIRECT_MATRIX_EMPTY -- so a direct matrix sealed by a build that carries that "
                "guard cannot have come from one, with two exceptions, each of which SEALS an "
                "all-zero asemx and carries its own code on the asemx artifact: an asemx none of "
                "whose Reinhart sun-dome directions lies in the aperture's exterior hemisphere -- "
                "an aperture within the dome's lowest-ring angle of straight down, 6 degrees at "
                "mf 1 -- carries ASE_DIRECT_ALL_ZERO_NO_SUN, and an asemx whose occlusion probe "
                "found that no sun direction reaches the grid -- with every window group's "
                "aperture opened on a three- or five-phase run, or with every glazing surface "
                "removed on a two-phase run; a courtyard or lightwell -- carries "
                "ASE_DIRECT_ALL_ZERO_OCCLUDED; either notice is also repeated in this payload "
                "when this result was built from a matrix that carries it. An "
                "illdir multiplied from either is all zero for that reason instead. This matrix "
                "may also predate it: "
                "the guard was added "
                "after matrices were already being sealed and it seals nothing retroactively, so an 'illdir:' sealed by an earlier "
                "build could have been multiplied from an all-zero coefficient matrix, and nothing "
                "readable from this file distinguishes the two. Read this number as measured, and "
                "check the sun path against the geometry of the openings it passes before "
                "reporting it."
            ),
        ))

    # Phase 25 Task 4b: each sealed `asemx` the published numbers were built from, named -- the
    # chain result's (a blend reads the per-group matrices through `sched:<grid>`) and, where ASE
    # was tallied from it, `illdir:<grid>`'s. Beside, never instead of,
    # `ASE_DIRECT_MATRIX_ALL_ZERO`, which says only that a seal is one possible cause.
    judged = [
        state.artifact_id,
        *([direct_id] if direct_header is not None else []),
        *([net_id] if net_header is not None else []),
    ]
    advisories.extend(_sealed_ase_matrix_advisories(state.loaded, judged))

    # R866: the subdivision every matrix behind both published numbers records; silent at 4 and
    # above, and when nothing records one.
    subdivision = _recorded_sky_subdivision(state.loaded, judged)
    if subdivision is not None and subdivision < LM83_MIN_SKY_SUBDIVISION:
        advisories.append(_sky_subdivision_below_floor_advisory(subdivision, edition=edition))

    ase_artifact = state.artifact_id if direct_header is None else direct_id
    # R869: what was blacked is the sealed record's, never the call's current marks -- equal here
    # by the refusal above, and read from the record so the payload states what the matrix is.
    blacked = (
        () if net_artifact is None or net_header is None
        else tuple(net_artifact.parameters["ase_net_blacked"])
    )
    ase_net = _AseNet(
        value=net_value,
        artifact=ase_artifact if net_header is None else net_id,
        blacked=blacked,
        groups_defined=any(
            record.kind == "window_group" for record in state.loaded.manifest.inputs
        ),
        from_chain=direct_header is None,
    )
    advisories.append(_ase_band_advisory(edition, ase=(ase_value, ase_artifact), net=ase_net))

    # **The honesty label on 7b's ASE -- and Task 13 is where it gains a condition to be FALSE
    # under.** Task 3 attached it ahead of this task so the branch was never in a state where the
    # number was wrong and unlabelled; it was unconditional then because no project could hold a
    # direct-only matrix. It is conditional now: emitted only when this call fell back to the chain
    # result, which is `V*T*D*S - Vd*T*Dd*Sds + Cds*Ssun` -- a TOTAL illuminance, sky and
    # inter-reflected light in every cell -- while LM-83 section 3.3 defines ASE as the direct beam
    # from the solar orb alone, zero-bounce. When `illdir:<grid>` is registered, ASE is that
    # matrix's own number and the label has nothing left to say, so it is not attached.
    # `tests/test_results_tool.py::TestAseComesFromTheDirectMatrix::
    # test_the_honesty_label_stops_being_emitted` is the absence proof Task 3 could not write.
    #
    # **The message names both mechanisms and refuses to name a direction, deliberately.** The
    # obvious argument -- extra light can only add, so ASE is inflated (ch04:181) -- is false for
    # *this* matrix, because the five-phase combination subtracts: `Vd*T*Dd*Sds` can exceed
    # `V*T*D*S` at a cell, which is the whole condition `NEGATIVE_ILLUMINANCE_PRESENT` above
    # reports and which this repo has measured at thousands of cells per run. A total can therefore
    # sit *below* its own isolated direct term, and an advisory that claimed "biased high" would be
    # a false statement shipped at `warn`. A previous design justified an owner decision on exactly
    # that claim and it was shown false.
    if direct_header is None:
        definition = (
            f"{section('ase_definition', edition)} defines the direct sunlight ASE counts as the "
            "unscattered sunbeam after the glazing, zero-bounce -- it has \"no contribution from "
            "the diffuse-sky component\""
            if edition == LM83_22 else
            f"{section('ase_definition', edition)} defines ASE as the direct beam from the solar "
            "orb only, after glazing transmittance and zero-bounce -- it \"does not require "
            "modeling of sky luminance or ground reflectance\""
        )
        advisories.append(Advisory(
            code="ASE_NOT_FROM_DIRECT_ONLY_RUN",
            severity="warn",
            message=(
                f"ASE here is counted from {state.artifact_id!r}, a five-phase TOTAL-illuminance "
                "matrix (V*T*D*S - Vd*T*Dd*Sds + Cds*Ssun): sky and inter-reflected light are in "
                f"every cell. {definition} -- so this is not the quantity the standard "
                "defines. The direction of the error is not guaranteed: the sky and "
                "inter-reflected "
                "light this matrix carries can push a cell over the 1 000 lux threshold that the "
                "direct beam alone would not reach, and the same five-phase combination also "
                "subtracts (Vd*T*Dd*Sds can exceed V*T*D*S -- see NEGATIVE_ILLUMINANCE_PRESENT), "
                "so a "
                "cell's total can equally sit below its own direct term. Re-run run_annual with "
                f"metrics=['sda','ase'] so it builds the dedicated zero-bounce direct-sun leg for "
                f"this grid and seals {direct_id!r}: ASE is then tallied from that matrix and this "
                "advisory stops being emitted."
            ),
        ))
    advisories.append(_compliance_lm83_edition_advisory(edition))
    # R862: the edition's dirt depreciation factor (LM-83-12 section 2.2.9, LM-83-22 section 4.5),
    # stated beside the edition on every LM-83 judgement because the server applies none.
    advisories.append(Advisory(
        code="LM83_DIRT_DEPRECIATION_NOT_APPLIED",
        severity="info",
        message=_lm83_dirt_depreciation_message(edition),
    ))
    # **Dated-but-true, and dated is enough to mislead** (fix wave B4.9, answering carried item
    # A12, which the ledger recorded as unresolved). Every clause below is literally true on every
    # call. But it was unconditional, and a caller who passed `blind_control='lm83'` -- and whose
    # project therefore holds the `illdir:<grid>` this very payload may have tallied ASE from --
    # reads "a run that carries no operable-blind schedule" as "your request did nothing". It did
    # something: it built the matrix `results(action='blind_schedule')` derives the schedule from.
    # `annual_tool` already carries the honest version at submit time; this is the same clause on
    # the read side, attached on the same condition the rest of this function keys on -- the
    # ARTIFACT's presence, never `method_used` (the standing carry from Task 11).
    scheduled_leg = (
        " This run DID build the direct-sun leg: the schedule is derivable now, with "
        f"results(action='blind_schedule', project={project!r}, grid={grid!r}"
        + (f", lm83_edition={edition!r}" if edition == LM83_22 else "")
        + f"), from {direct_id!r}. What "
        "is missing is not the schedule but the blend that would consume it."
        if direct_header is not None
        else ""
    )
    # **Retired on a blended read, and on nothing else** (ruling R178, owner decision). The code's
    # condition is *"sDA computed from a run that carries no operable-blind schedule"*, and a
    # result read from `illblend:<grid>` is the one case where that is false: every hour of it was
    # decided by the sealed `sched:<grid>`, group by group. It **keeps** firing on an `ill:<grid>`
    # read, including in a project that also holds a blend -- that matrix really is the unscheduled
    # total, and the condition is a statement about the matrix this call read, not about what else
    # the project happens to hold. Keyed on the ARTIFACT, never on `method_used`: the blend records
    # `method="3phase"` (R158), so `method_used` cannot see the difference at all.
    #
    # **The message no longer says "not blind-aware until Phase 7d".** That sentence was true when
    # it was written and became false the moment this phase shipped a blend the read side resolves;
    # what is true now is narrower and is what the caller needs -- *this run* carries no schedule,
    # and here is the run that would. An advisory whose stated reason has stopped being true is a
    # defect in its own right (docs/tool-surface.md section 9), which is the same rule that deleted
    # `run_annual`'s `blind_control='lm83'` refusal rather than re-pointing it at a later phase.
    if not state.blended:
        rule = section("blind_trigger", edition)
        # R864: LM-83-22 section 4.6 has no escape clause for a design without blinds -- it models
        # blinds "even if no blinds have been specified" -- and exempts only window groups whose
        # glazing diffuses the beam away or can be reduced below 2 % (`annual_tool`'s wording).
        exemption = (
            f"{rule} models interior blinds that close by window group even where the design "
            "specifies none, so an unscheduled sDA does not meet it unless every window group is "
            "exempt under the same section (glazing that diffuses away the direct beam, or "
            "adjustable transmittance reducible below 2 %)"
            if edition == LM83_22 else
            f"{rule}'s escape clause makes this legitimate for a design that excludes operable "
            "blinds"
        )
        advisories.append(Advisory(
            code="SDA_WITHOUT_BLIND_SCHEDULE",
            severity="warn",
            message=(
                f"sDA was computed from {state.artifact_id!r}, a result that carries no "
                "operable-blind schedule: every hour of it is the same bound BSDF state, so no "
                f"hour of it was chosen by {rule}'s closing rule. {exemption}; "
                "blind_schedule_used is false, and the condition travels with the number "
                "rather than being silently assumed. A blind-aware sDA is read from "
                f"{_BLENDED_PREFIX}{grid}, which a run_annual run with blind_control='lm83' seals "
                "by blending one result per window group and blind state hour by hour against the "
                f"sealed schedule; results(blind_control='lm83', grid={grid!r}) reads it where "
                f"this project holds one.{scheduled_leg}"
            ),
        ))

    latitude = _site_latitude(state, walked=(placed.epw, placed.epw_reason))
    if latitude is not None and not lm83_latitude_in_calibration_band(latitude, edition=edition):
        advisories.append(_latitude_outside_calibration_advisory(
            latitude, reported="sDA and ASE are", edition=edition,
        ))
    if placed.advisory is not None:
        advisories.append(placed.advisory)

    hours_total = len(window)
    lm83 = Lm83View(
        edition=edition,
        # **Each metric names the matrix it was actually tallied from** (Task 17, Correction 4).
        # `direct_header is None` is the fallback branch -- ASE came off the chain result, which is
        # what `ASE_NOT_FROM_DIRECT_ONLY_RUN` above says in prose -- and it is decided by the
        # ARTIFACT's presence, never by `method_used` (the standing carry from Task 11).
        sda=Lm83MetricView(value=sda_value, artifact=state.artifact_id),
        ase=Lm83MetricView(value=ase_value, artifact=ase_artifact),
        ase_net=Lm83MetricView(value=ase_net.value, artifact=ase_net.artifact),
        automated_groups=list(ase_net.blacked),
        # R869: LM-83-22 section 3.2 states its 20 % line for ASE_net; LM-83-12's research bands
        # stay on ASE. The edition alone decides which value is judged.
        bands=Lm83BandsView(
            sda=sda_band(sda_value),
            ase=(
                ase_band(ase_net.value, edition=edition) if edition == LM83_22
                else ase_band(ase_value, edition=edition)
            ),
            ase_judged="ase_net" if edition == LM83_22 else "ase",
        ),
    )
    analysis = ComplianceAnalysisView(
        period=placed.period,
        hours_total=hours_total,
        # **`rad_core.metrics.sda_hours_required`, not arithmetic written here (whole-branch
        # review, composition lens -- the last duplicated formula).** This line read
        # `LM83_DAYLIT_FRACTION * hours_total`: the same quantity `spatial_daylight_autonomy`
        # computes internally, spelled a second time in the thin layer, which is physics in
        # `rad_mcp` as well as two definitions of one number. They agreed only because both
        # operands were passed explicitly at this one call site -- and "two constants that merely
        # agree" is precisely the shape that produced two of this branch's own defects (a bare
        # `0.5` default matching `LM83_DAYLIT_FRACTION` by coincidence, Task 12 review round 2;
        # and, before that, an `int()` here that published a requirement a whole hour below the
        # one enforced). There is now one function, called by the metric that enforces the number
        # and by this line that publishes it, so a change to either reaches both.
        #
        # Still not `int(...)`: `sda_hours_required` returns the untruncated float each point's
        # hour count is compared against (`>=`, inclusive).
        hours_required=sda_hours_required(window, fraction=LM83_DAYLIT_FRACTION),
        points_total=state.header.points,
        points_excluded=0,
        exclusion_reason=None,
        # **The field the whole phase is about, and it is read off the artifact** (ruling R178).
        # It was the literal `False` on every call, which was true while nothing blended; a blended
        # result's every hour was chosen by the sealed `sched:<grid>`, so publishing `false` beside
        # it would be a measured property reported as its opposite.
        blind_schedule_used=state.blended,
        clock_zone=placed.clock_zone,
        reference_year=placed.reference_year,
    )
    return ResultsCompliance(
        project=project, grid=grid, artifact=state.artifact_id, path=str(state.path),
        method_used=state.method_used, lm83=lm83, analysis=analysis, advisories=advisories,
    )


#: What a `by_chain` key gains when the result it names was blended -- ruling R174 (owner
#: decision). `"3phase"` becomes `"3phase+lm83"`; every unblended key is unchanged, so a client
#: written against R113's vocabulary reads the same keys for the same results and only ever sees a
#: new one where a new result exists.
_BLENDED_KEY_SUFFIX: Final[str] = "+lm83"


def _by_chain_key(state: _Resolved) -> str:
    """The key one resolved result takes in a `by_chain` payload: **the recorded method plus the
    blind treatment**, not the method alone.

    **Ruling R174 (owner decision), and without it `compliance` would refuse.** `compliance` is the
    sole member of `_ACTIONS_ANSWERING_FROM_BOTH_RESULTS` precisely so that an unselected call on a
    grid with more than one result *never* refuses (R113). But `illblend:<grid>` and `ill:<grid>`
    both record `method="3phase"` (R158), so keying on the bare method put two entries under one
    key -- and `_refuse_indistinguishable_chains`, which exists to stop a payload silently dropping
    one of them, would have fired on the one action built so it would never refuse. Widening the
    key is what keeps both promises at once: one payload reports sDA with blinds and without, side
    by side, under two keys that say which is which.

    Read by `_compliance_by_chain` **and** by `_refuse_indistinguishable_chains`, so the collision
    guard tests the keys the payload will actually use rather than a second spelling of them; a
    guard keyed on anything else would refuse a payload that was fine, or admit one that drops an
    entry. `docs/tool-surface.md` §11 publishes the vocabulary.
    """
    return f"{state.method_used}{_BLENDED_KEY_SUFFIX}" if state.blended else state.method_used


def _refuse_indistinguishable_chains(states: list[_Resolved], *, grid: str) -> CallToolResult:
    """Two annual results whose manifest records name the **same** chain.

    `by_chain` is keyed by `_by_chain_key` -- the recorded method **and** the blind treatment
    (rulings R113 and R174) -- so two records that produce the same key would put two entries under
    one key and **publish one result while silently dropping the other** -- a data-loss defect that
    no assertion about the surviving entry can see. Refused instead, above the point where either
    matrix's body is read.

    **The blend is not such a collision, and widening the key rather than the guard is why.**
    `illblend:<grid>` and `ill:<grid>` both record `method="3phase"`, so on the bare method they
    collided and this refusal fired -- on `compliance`, the one action R113 built so it would never
    refuse. `_by_chain_key` keys them `"3phase+lm83"` and `"3phase"`, and this guard reads that same
    function, so it now fires on exactly what it was written for: two records that are genuinely
    indistinguishable.

    Not reachable from `run_annual`: `chain.two_phase_steps`/`three_phase_steps` seal `ill:<grid>`
    and only `chain.five_phase_steps` seals `ill5:<grid>`, and none of the three writes another
    chain's name into `parameters["method"]`. It is reachable from a hand-edited or drifted
    manifest, which is precisely the class R50 says *"registered is not currently valid"* about,
    and which this module already re-contains and cross-checks every path for.

    **Code-less**, for `_NoAnnualResult`'s own stated reason as `_ChainResultNotRegistered` and
    `_ChainMethodMismatch` already apply it: the register allocates codes for protocol handles,
    and this is a manifest record disagreeing with itself about a handle inside the project. It is
    **not** `ANNUAL_RESULT_AMBIGUOUS` -- that code means *"two answers, no instruction which to
    publish"*, and here the instruction is not needed: the call asked for both and the payload
    cannot carry them. The message names `chain=` because that is the one argument that still
    reaches either matrix.
    """
    described = " and ".join(
        f"{state.artifact_id!r} records method={state.method_used!r} and keys as "
        f"{_by_chain_key(state)!r}"
        for state in states
    )
    return refuse(
        f"grid {grid!r} has {len(states)} annual results registered whose manifest records give "
        f"them the same by_chain key: {described}. results(action='compliance') keys its "
        "by_chain block by the recorded method and the blind treatment together, so those results "
        "would be published under one key and all but one of them would be dropped from the "
        "payload with nothing saying so. Re-run run_annual for this grid so each chain seals its "
        "own result with its own method recorded, or pass chain= (or blind_control=) to read one "
        "of them on its own -- that path resolves the id directly and checks the recorded method "
        "against what was asked."
    )


def _refused_chain_entry(
    entry_type: type[_ChainEntryT], state: _Resolved, refusal: CallToolResult
) -> _ChainEntryT:
    """Shape one entry from the refusal its own `_compliance_*` call returned.

    **The refusal object is the single-result refusal, unchanged** -- same helper, same message,
    same code, produced by the same code path in the same order. That is the whole point of
    building `by_chain` out of the existing functions rather than a per-entry re-implementation:
    ruling F11's carried concern is that Task 13's refusal **ordering** inside `_compliance_lm83`
    must survive into each entry, and it survives here by construction, because the ordering is
    not re-expressed anywhere. `tests/test_results_tool.py::
    TestTask13sRefusalOrderingSurvivesPerEntry` proves it per entry rather than trusting that.
    """
    payload = refusal.structured_content or {}
    advisories = [Advisory.model_validate(entry) for entry in payload.get("advisories") or []]
    return entry_type(
        reported=False,
        artifact=state.artifact_id,
        path=str(state.path),
        method_used=state.method_used,
        refusal=ComplianceRefusalView(
            code=advisories[0].code if advisories else None,
            reason=str(payload.get("reason", "")),
        ),
        advisories=advisories or None,
    )


def _compliance_by_chain(
    runtime: Runtime, states: list[_Resolved], *, project: str, grid: str, standard: str,
    metrics: list[str] | None, level: str | None, edition: Lm83Edition = DEFAULT_LM83_EDITION,
    named: bool = False,
) -> CallToolResult:
    """`compliance` on a grid holding both annual results, with no `chain` -- ruling R113.

    **One entry per resolved matrix, built by calling the same `_compliance_*` function the
    single-result path calls.** No arithmetic, no advisory and no refusal is written twice: each
    entry is the payload that call would have returned on its own, re-shaped into an entry, so a
    change to either compliance function reaches the `by_chain` payload without anyone remembering
    to mirror it -- and, more to the point, so Task 13's refusal ordering cannot silently diverge
    between the two shapes.

    Advisories land **in the entry**, never at the root: the root carries only what is true of the
    *call*, which today is nothing (see `ResultsComplianceByChain`).
    """
    # **The keying guard, above every body read.** `_resolve` returned the states in
    # `_ARTIFACT_PREFIXES` order and each carries its own recorded method and blind treatment; two
    # that produce the same `_by_chain_key` would drop an entry, so it is refused before any matrix
    # is streamed. Read through the same function the payload keys with, never a second spelling.
    keys = [_by_chain_key(state) for state in states]
    if len(set(keys)) != len(keys):
        return _refuse_indistinguishable_chains(states, grid=grid)

    if standard == "lm83":
        lm83_entries: dict[str, Lm83ChainEntryView] = {}
        for state in states:
            built = _compliance_lm83(
                runtime, state, project=project, grid=grid, metrics=metrics, edition=edition,
                named=named,
            )
            lm83_entries[_by_chain_key(state)] = (
                _refused_chain_entry(Lm83ChainEntryView, state, built)
                if isinstance(built, CallToolResult)
                else Lm83ChainEntryView(
                    reported=True, artifact=built.artifact, path=built.path,
                    method_used=built.method_used, lm83=built.lm83, analysis=built.analysis,
                    advisories=built.advisories,
                )
            )
        return emit(ResultsComplianceByChain(
            project=project, grid=grid, by_chain=lm83_entries,
        ))

    # Only `standard="en17037"` reaches here, for the dispatch's own stated reason: `en12464` and
    # en17037's `method="1"`/`axis="glare"` all refused above `runtime = current()`.
    en17037_entries: dict[str, En17037ChainEntryView] = {}
    for state in states:
        built_en = _compliance_en17037_provision(
            state, project=project, grid=grid, level=level
        )
        en17037_entries[_by_chain_key(state)] = (
            _refused_chain_entry(En17037ChainEntryView, state, built_en)
            if isinstance(built_en, CallToolResult)
            else En17037ChainEntryView(
                reported=True, artifact=built_en.artifact, path=built_en.path,
                method_used=built_en.method_used, en17037=built_en.en17037,
                analysis=built_en.analysis, advisories=built_en.advisories,
            )
        )
    return emit(ResultsComplianceEn17037ByChain(
        project=project, grid=grid, by_chain=en17037_entries,
    ))


def _contained_csv_path(state: _Resolved, *, grid: str) -> Path:
    """Where `export` writes, proven safe **before** any syscall touches it.

    This is `annual_tool._write_step_file`'s pattern, adopted rather than re-invented -- commit
    `c140a33` established it as this repo's `rad_mcp` write shape and documented it guard by guard.
    The order below is the whole point, and each line closes a hole the others do not:

    - **`contain` first.** A check performed after a write reports a breach rather than preventing
      one, which is ruling R48's own finding (`write_wea` returned `refused: true` about damage
      already done).
    - **`resolved_parent_within` before `mkdir`.** `O_NOFOLLOW` refuses a symlink at the *final*
      component only, so a **directory** symlink anywhere in the parent chain walks a newly created
      file straight out of the root with no symlink at the leaf at all -- and
      `mkdir(parents=True)` would traverse it and *create the escape itself*. Checked before
      anything is created.
    - **`open_regular_file` at the call site**, not `Path.open`. This is the guard containment
      cannot provide, and the reason this function exists at all: a **FIFO planted inside an allowed
      root passes `contain` legitimately**, and `Path.open("w")` on it **blocks forever** --
      measured on this machine, the open never returns and only SIGALRM breaks it. In the server
      that open sits in an anyio worker thread whose default limiter is 40, so forty such paths
      stall *every tool the server offers*, not merely `export`. That is ruling R45's wedge on a
      write path. `open_regular_file` opens `O_NONBLOCK|O_NOFOLLOW` and `S_ISREG`-checks the
      **descriptor**, so it returns instead of hanging and there is no TOCTOU window to swap a FIFO
      in after a `stat`.

    The read side of this module already proved the point: Task 3's own Mutation 3 replaced
    `open_regular_file` with `path.open("rb")` in `rad_engine.results` and hung the FIFO test until
    it was killed. The write path did not inherit that guard until now.

    *(Amended 2026-10-05, R851, registers N-112 to N-114.)* The path is now contained as named
    (`contain_as_named`); a link at any component below the project root is refused
    (`refuse_linked_components`) -- `resolved_parent_within` passed one aimed at another directory
    inside the root; the name itself is `lstat`-checked (`refuse_existing_non_regular_destination`,
    which refuses the FIFO above before any open); and `_export` stages the rows beside the name and
    renames them over it (`replace_file_atomically`) instead of opening it `"wb"`, so a hardlink is
    replaced, not written through. The staging open is `open_regular_file`'s `"xb"`.

    Raises `PathOutsideRoot` for a containment failure -- shaped by `results`' handler chain into a
    `PATH_OUTSIDE_ROOT` refusal -- and nothing is written on any refusing path.
    """
    # N-112 (R851): the path as named, so `open_regular_file`'s `O_NOFOLLOW` sees a symlink at the
    # final component. `contain` returns the resolved path: a link to another file inside the root
    # passed it and that file was overwritten.
    out_path = contain_as_named(state.paths.results / f"annual_{grid}.csv", current().roots)
    # N-113 (R851): a link at any component below the project root (`08_results` aimed at another
    # directory) is refused before the `mkdir` and any write; then the name itself must be a regular
    # file or absent, since the staged rename below would otherwise replace a link silently.
    refuse_linked_components(out_path, below=state.paths.root)
    if not resolved_parent_within(out_path, current().roots):
        raise PathOutsideRoot(
            f"the CSV export would be written to {out_path.parent}, which resolves outside every "
            "allowed root. Nothing has been written. A symlinked directory inside the project is "
            "the usual cause; the file this server writes must stay inside the project it belongs "
            "to."
        )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        refuse_existing_non_regular_destination(out_path)
    except NotARegularFileError:
        # The helper's own remedy names an `out` argument this action does not have.
        raise NotARegularFileError(
            f"{str(out_path)!r} exists and is not a regular file (anything else is refused: a symbolic "
            "link, a FIFO, a device or a directory, for example), so the CSV was not written there. Nothing has been written. Remove "
            "whatever occupies that path and re-run."
        ) from None
    return out_path


def _export(
    state: _Resolved, *, project: str, grid: str, export_format: str
) -> CallToolResult:
    """Write the whole matrix as CSV and return a `resource_link` to it.

    **The transcription check 4.8's CSV clause is about.** One row per sensor: a leading `point_id`
    column then one lux value per hour, in the matrix's own order -- so `export` and `timeseries`
    read the same rows through the same reader and cannot disagree. Written streaming, one row at a
    time, for the same reason the reader streams.

    **It overwrites `08_results/annual_<grid>.csv` without asking, and that is deliberate.** The
    destination is derived from the grid name, not supplied by the caller, so the only file it can
    replace is this tool's own previous export of the same study -- re-exporting is what "export it
    again" means, and `idempotentHint: true` says the same thing from the other side. That is why
    `export` stays out of `DESTRUCTIVE_ACTIONS`: it removes nothing a caller registered and touches
    no file any other tool owns. A caller who wants both versions renames the first; nothing here
    can reach outside the project to destroy anything else (`_contained_csv_path`).
    """
    if export_format != "csv":
        return _refuse_argument(
            f"results(action='export') supports format='csv' only, not {export_format!r}. "
            "'parquet' needs a dependency this project has not taken, and 'json' is not built -- "
            "the JSON shape a caller wants is action='timeseries' with point_ids/hours, which "
            "returns it in the result rather than writing a file.",
            field="format",
        )

    out_path = _contained_csv_path(state, grid=grid)

    row_count = 0
    columns = 0
    negative = _NegativeCount()
    # **`open_regular_file` returns a BINARY handle**, so the text layer is explicit here rather
    # than implied. `newline=""` is `csv`'s own requirement (it writes its own `\r\n` terminators
    # and a translating layer would double them).
    # N-114 (R851): staged beside the name and renamed over it (`replace_file_atomically`, R526's
    # pattern), so a hardlink at `annual_<grid>.csv` is replaced, never written through -- the
    # `"wb"` open this replaced rewrote a hardlinked file outside every root.
    def _stream(raw_handle: IO[bytes]) -> None:
        nonlocal row_count, columns
        handle = io.TextIOWrapper(raw_handle, encoding="utf-8", newline="")
        try:
            writer = csv.writer(handle)
            writer.writerow(["point_id", *(f"hour_{h}" for h in range(state.header.hours))])
            matrix_rows = _lux_rows(
                state.path, artifact_id=state.artifact_id,
                expected_points=state.expected_points,
                content_digest=state.content_digest,
            )
            for index, lux in enumerate(matrix_rows):
                columns = len(lux)
                # **`repr` is load-bearing, not incidental formatting.** It writes a float's full
                # round-trip representation, which is what makes the CSV reproduce `timeseries`
                # *exactly* rather than approximately -- check 4.8's own clause, and the property
                # `test_the_csv_reproduces_what_timeseries_returns` asserts with `==`. Replacing
                # this with `f"{value:.6f}"` to tidy the output would silently turn that exact
                # assertion into a rounding failure, so it is spelled out here rather than left to
                # be re-derived by whoever next reads this line.
                writer.writerow([index, *(repr(value) for value in lux)])
                negative.observe_row(lux)
                row_count += 1
        finally:
            # Flush the text layer into the binary handle before `replace_file_atomically` closes
            # it underneath us (detaching first would leave buffered rows unwritten), then detach,
            # so the wrapper never closes the handle the helper owns.
            handle.flush()
            handle.detach()

    # R855: a write the disk or the permissions refuse is `PROJECT_WRITE_FAILED`, naming a read-only
    # CSV as the cause (`write_failure`); something not a regular file at the name keeps its own code.
    try:
        replace_file_atomically(out_path, _stream)
    except NotARegularFileError:
        raise
    except OSError as exc:
        raise write_failure(out_path, exc, what=f"the CSV export for grid {grid!r}", outcome=(
            "Nothing was committed, and a CSV already there is unchanged.")) from None

    out_path = out_path.resolve()  # the spelling `contain` gave before N-112; a regular file now
    result = ResultsExport(
        project=project, grid=grid, artifact=state.artifact_id, out=str(out_path),
        method_used=state.method_used, rows=row_count, columns=columns,
        shape=_shape_view(state.header),
        # See `_timeseries` for why this action gained an advisories list at Phase 7d-1 Task 15a.
        advisories=[
            *state.blend_advisories,
            *filter(None, [_negative_count_advisory(
                state, negative, tail="The CSV carries them as published.",
            )]),
        ] or None,
    )
    payload = result.payload()
    return CallToolResult(
        content=[
            TextContent(type="text", text=_json_dumps(payload)),
            ResourceLink(
                uri=out_path.as_uri(),
                name=f"{project}/{grid} annual results (CSV)",
                description=(
                    f"{row_count} sensor rows x {columns} hourly lux values, with a `point_id` column "
                    f"first -- the same values results(action='timeseries') returns."
                ),
                mimeType=_CSV_MIME_TYPE,
            ),
        ],
        structured_content=payload,
        is_error=False,
    )


def _emit_with_resource_link(
    result: RadResult, *, path: Path, project: str, grid: str, description: str
) -> CallToolResult:
    """`emit` plus a `resource_link` content block for the committed matrix --
    docs/tool-surface.md#artifacts-are-resource-links-as-well-as-paths.

    **The link travels alongside the payload, never instead of it.**
    `point_in_time_tool._emit_with_resource_link`'s own docstring records the defect this avoids: the
    link was emitted while the corresponding *field* was missing from the result model, so the
    absolute path existed only as a `file://` URI. Here `path` is a declared field on every result
    model that carries a link, and both are built from the same `path` argument, so they cannot name
    different files.
    """
    payload = result.payload()
    return CallToolResult(
        content=[
            TextContent(type="text", text=_json_dumps(payload)),
            ResourceLink(
                uri=path.as_uri(),
                name=f"{project}/{grid} annual results",
                description=description,
                mimeType="text/plain",
            ),
        ],
        structured_content=payload,
        is_error=False,
    )


def _json_dumps(payload: dict[str, Any]) -> str:
    """The JSON mirror `emit` puts in the `TextContent` block, in the same shape."""
    return json.dumps(payload, indent=2)


# --- the blind schedule (LM-83 section 2.2.6, Phase 7c Task 16) ----------------------------------


class _NoDirectMatrix(ProjectError):
    """**No `illdir:<grid>` is registered, so there is no direct-sun matrix to derive a schedule
    from.** `BLIND_SCHEDULE_NO_DIRECT_MATRIX` at `error`.

    **Keyed on the artifact, never on `method_used`** -- the load-bearing carry from Phase 7c Task
    11, and the reason this is not `ASE_FROM_SMEARED_SUN`'s condition wearing another name.
    `run_annual` plans the direct leg whenever `"ase" in metrics` **or**
    `blind_control == "lm83"`, and its whole-grid half, which seals `illdir:<grid>`, is planned
    beside every chain (`chain.direct_sun_steps` keys only its per-group legs on the chain), so
    `illdir:<grid>` is reachable on a two-phase run and absent from a five-phase one that asked for
    neither. A refusal that
    read the chain would refuse projects that hold a perfectly good direct matrix and answer ones
    that do not.

    Not `ARGUMENT_OUT_OF_RANGE` (R14: an argument rejected on its face -- `grid` resolves and the
    project is open by this point), and not code-less: a client can act on it, and the action is
    exactly one call.
    """

    code = "BLIND_SCHEDULE_NO_DIRECT_MATRIX"
    field = "grid"


class _UnreadableDirectMatrix(ProjectError):
    """**A cell of `illdir:<grid>` is not a finite illuminance -- either the token the file carries
    is not finite, or the lux it converts to is not -- so this matrix measured nothing the trigger
    can be asked about.** `DIRECT_MATRIX_UNREADABLE` at `error`.

    `nan >= 1 000.0` is `False` for every cell, so an all-`NaN` direct matrix produces a schedule
    that is `"open"` every hour of the year -- the **favourable** state, off a matrix that measured
    nothing, which `rad_core.blinds`' module docstring forbids and which was reaching the wire
    unremarked (measured at `b70be4a`: `refused=None`, `states={'open'}`, `hours_closed=0`).
    `-inf` understates closure the same way; `+inf` compares true against every threshold and
    overstates it. None of the three is an illuminance.

    **Follows `050e256`'s precedent, and takes its distinction rather than its code.** That commit
    ruled a non-finite cell *unreadable*, never *empty*: `ASE_DIRECT_MATRIX_EMPTY` says *every cell
    is zero*, which is untrue of a `NaN` matrix, and reporting one condition under another's
    definition is the defect docs/tool-surface.md section 9 names. Its code
    `COEFFICIENT_MATRIX_UNREADABLE` is not reused here for the same reason **one level up**: that
    row is written about *"the bytes at an `asemx:<grid>` path"* not being *"the ASCII `rcontrib`
    **coefficient** matrix that artifact kind requires"*, and `illdir:<grid>` is neither that path
    nor that kind -- it is a `dctimestep` illuminance matrix, and its guard sits at **read** time
    where that one sits at seal time. So the distinction is inherited and the code is its own.

    A `ProjectError`, so the dispatch's existing handler chain shapes it: `refuse_engine_error`
    reads `code`, `severity` and `field` and nothing new is threaded through.
    """

    code = "DIRECT_MATRIX_UNREADABLE"
    field = "grid"


def _direct_leg_rerun_call(
    loaded: LoadedProject, *, project: str, grid: str, edition: Lm83Edition,
) -> tuple[str, str]:
    """A whole `run_annual` call that plans the direct-sun leg (`illdir:<grid>`) for `grid` (final
    fix F, fix round 3, H2; it was `run_annual(project=..., grid=..., blind_control='lm83', ...)`),
    on the chain of a result the grid holds, with the arguments that result recorded
    (`_rerun_call`): a two-phase result with `blind_control='lm83'`; the blend, or an unblended
    three-phase result, as the three-phase `blind_control='lm83'` run (`_rerun_blend_call`, after
    the `assign_bsdf` calls it needs, `_bind_first_clause`), which plans the leg and seals a blend;
    a five-phase result with `metrics=['sda', 'ase']`, which plans it; and, with none, the
    two-phase run, its unrecorded arguments named as `_rerun_call` names them.

    Returns `(before, call)`: `before` is `_bind_first_clause`'s sentence ending "Then ", or
    `""`, for the caller to place where its own sentence allows."""
    records = {artifact.id: artifact for artifact in loaded.manifest.artifacts}
    two = f"ill:{grid}"
    if two in records and _recorded_chain(records[two]) == "2phase":
        start, method = two, "2phase"
    elif blend_ill_id(grid) in records or two in records:
        return _bind_first_clause(loaded, project=project), _rerun_blend_call(
            loaded, project=project, grid=grid, edition=edition,
            also_from=(two,) if two in records else (),
        )
    elif f"ill5:{grid}" in records:
        start, method = f"ill5:{grid}", "5phase"
    else:
        start, method = two, "2phase"
    return "", _rerun_call(
        loaded, project=project, grid=grid, starts=((start, _SMX_KIND),), quality_from=start,
        arguments=(
            "metrics=['sda', 'ase']" if method == "5phase"
            else "metrics=['da'], blind_control='lm83'"
        ),
        method=method,
    )


def _refuse_no_direct_matrix(
    *, grid: str, direct_id: str, loaded: LoadedProject, project: str,
    edition: Lm83Edition = DEFAULT_LM83_EDITION,
) -> CallToolResult:
    """`BLIND_SCHEDULE_NO_DIRECT_MATRIX`. Names the one call that produces the matrix; the clause
    is `edition`'s (R864), LM-83-12's text unchanged."""
    trigger = "LM-83 section 2.2.6" if edition != LM83_22 else section("blind_trigger", edition)
    before, call = _direct_leg_rerun_call(loaded, project=project, grid=grid, edition=edition)
    message = (
        f"no direct-sun matrix is registered for grid {grid!r}: nothing is registered under "
        f"{direct_id!r}. The blind schedule is {trigger}'s per-hour trigger on points "
        "*receiving direct sunlight*, and this server derives it from the pre-blind, zero-bounce "
        "direct-sun run and from nothing else -- deriving it from a chain result instead would be "
        "a smeared-sun schedule wearing a direct-sun trigger's name, because every cell of that "
        "matrix carries sky and inter-reflected light as well as the beam. A run_annual run "
        "with blind_control='lm83', or with metrics=['sda','ase'], seals it, on a two-, three- or "
        f"five-phase chain. {before}{'p' if before else 'P'}lan the direct leg with {call}."
    )
    logger.info("results(action='blind_schedule') refused: %s", message)
    return refuse(
        message,
        [Advisory(
            code=_NoDirectMatrix.code, severity="error", field="grid", message=message,
        )],
    )


def _refuse_unschedulable_direct_matrix(
    *, grid: str, direct_id: str, header: AnnualMatrix, window: frozenset[int],
    edition: Lm83Edition = DEFAULT_LM83_EDITION,
) -> CallToolResult:
    """The direct matrix cannot carry a year's schedule: it is not a whole year, or it has no
    sensor points.

    **Both are checked from the header alone, above every allocation sized from it** (R39, and
    `_resolve`'s own measurement: a record declaring `NROWS=2000000000` attempted ~1.3 TB). The two
    are one refusal because they are one repair -- re-run the direct leg over a full-year `.wea`
    against a real grid -- but they carry different codes, because only the first is a period.

    **The point-count half is `<= 0`, not `== 0`** (fix round 2, N1). `header.points` comes out of
    an `.ill` header, and a hand-edited manifest can agree with it on both sides -- the threat
    `_MatrixTooLarge`'s docstring is written against -- so `NROWS=-5` arrives here as `-5`. Kept in
    step with the caller's own guard, deliberately and not as symmetry for its own sake: measured,
    if the caller widened to `<= 0` and this branch did not, a negative count fell past it and was
    refused with the **period** message -- *"declares 8760 hour column(s), which is not a whole
    year"*, said of a matrix that is exactly a whole year. One condition, spelled the same way at
    both ends, and the message quotes the count rather than the word "0".

    The point-count half is **code-less**, the posture `_refuse_empty_lm83_denominator` already
    takes for the identical condition on the chain matrix; the register allocates nothing for "the
    matrix carries no sensor points". Measured, not assumed: `blinds_close(points_in_sun=0,
    points_total=0, area_sf=...)` returns `False`, so a zero-point matrix produces a schedule that
    is "open" every hour of the year off nothing at all, with no exception to say so. That is the
    favourable state as a by-product of an empty denominator, which `rad_core.blinds`' module
    docstring forbids in the one direction it can see (a missing column) and cannot see in this one.

    The period message cites `edition`'s clause (R864); LM-83-12's text is unchanged.
    """
    if header.points <= 0:
        message = (
            f"the blind schedule for grid {grid!r} could not be derived: {direct_id!r} carries "
            f"{header.points} sensor points, so there is no analysis area for the trigger to count "
            "points in sun over. Every hour would come back 'open' -- the favourable state -- off a "
            "denominator of nothing."
        )
        logger.info("results(action='blind_schedule') refused: %s", message)
        return refuse(message)

    message = (
        f"the blind schedule for grid {grid!r} could not be derived: {direct_id!r} declares "
        f"{header.hours} hour column(s), which is not a whole year "
        f"({' or '.join(str(hours) for hours in sorted(WHOLE_YEAR_HOURS))} hours), and LM-83's "
        f"08:00-18:00 window selects {len(window)} of them. "
        + (
            "Section 2.2.6 evaluates the trigger *for each hour of the year*"
            if edition != LM83_22
            else f"{section('blind_trigger', edition)} applies the rule *at every hour of the year*"
        )
        + ", so a schedule over a shorter period would be a partial year published under an "
        "annual schedule's name -- and one over a period containing no 08:00-18:00 column at all "
        "would be empty, which reads as 'the blinds never closed'. Re-run the direct leg over a "
        "full-year weather file."
    )
    logger.info("results(action='blind_schedule') refused: %s", message)
    return refuse(
        message,
        [Advisory(
            code="ANALYSIS_PERIOD_NOT_ANNUAL", severity="error", field="grid", message=message,
        )],
    )


#: **The one sentence `BLIND_TRIGGER_MIXED_BASIS` states, in both severities it is emitted at.**
#: Quoted verbatim in docs/tool-surface.md's §14 entry for the code, and
#: `tests/test_results_tool.py` asserts this exact string reaches the wire on both paths -- the
#: precedent is ruling R167 (Phase 7d-1 Task 14), where a canonical sentence in the register is what
#: keeps a published condition and its emitted message from drifting into two claims.
#:
#: It states the **fact** (two bases for one rule) and not the verdict, because the fact is what is
#: common to the refusal and the warning; each call site adds its own direction and its own repair.
_MIXED_BASIS_SENTENCE: Final[str] = (
    "LM-83 section 2.2.6 selects a row of its table from the analysis AREA and then applies that "
    "row's own POINT COUNT, and on this call those two numbers come from different bases: the area "
    "is the shoelace area of the grid's recorded polygon, while the point count is the direct "
    "matrix's own NROWS, and the matrix carries materially fewer points than LM-83's placement rule "
    "would put inside that polygon."
)


def _refuse_mixed_basis(
    *, grid: str, direct_id: str, points_total: int, points_expected: int, area: _AnalysisArea,
    loaded: LoadedProject, project: str, edition: Lm83Edition = DEFAULT_LM83_EDITION,
) -> CallToolResult:
    """`BLIND_TRIGGER_MIXED_BASIS` at `error` -- the small-area rows applied to a grid too sparse
    for them.

    **Only below `LM83_BLIND_AREA_GENERAL_SF`, and the asymmetry is measured rather than assumed**
    (ruling R156). At and above 1 000 sf §2.2.6's criterion is `points_in_sun / points_total > 2 %`,
    in which the point count cancels arithmetically; the row *selection* still does not, but that
    error makes the tool **over**-close -- less daylight retained, sDA understated, the conservative
    direction -- so it is recorded at `warn` and the schedule is published. Below 1 000 sf the
    criterion is an absolute count (`> 5`, `> 3`, `> 1`) against an unchanged threshold, so a sparse
    grid **under**-closes: blinds the standard would have shut stay open, more daylight is counted
    than the standard allows, and sDA comes out **overstated**. That is the flattering direction and
    it is the one refused.

    **The recovery names a call that exists**, which is the whole test of an actionable refusal:
    `sensor_grid(action="create", standard="lm83", ...)` -- `create` is one of the four published
    actions (`plan`, `create`, `list`, `remove`) and `standard="lm83"` is what makes the placement
    the one this density is measured against. An earlier draft of this task named
    `sensor_grid(action='place')`, which the schema rejects.

    Both numbers are in the message, because a refusal a caller cannot act on is not actionable:
    the count they have, the count LM-83 asks for, and the polygon that decides the second.
    """
    density = points_total / points_expected
    before, call = _direct_leg_rerun_call(loaded, project=project, grid=grid, edition=edition)
    message = (
        f"the blind schedule for grid {grid!r} was not derived. {_MIXED_BASIS_SENTENCE} "
        f"{direct_id!r} declares {points_total} sensor point(s); LM-83's own 0.6 m placement over "
        f"the recorded polygon ({area.area_m2:.1f} m2, {area.area_sf:.0f} sf, from the "
        f"{area.area_source}) puts {points_expected} inside it -- a density of {density:.0%}, below "
        f"{LM83_GRID_POINT_DENSITY_MIN:.0%}. Below "
        f"{LM83_BLIND_AREA_GENERAL_SF:.0f} sf the section 2.2.6 row is an absolute point count "
        "rather than a percentage, so a grid this sparse counts fewer points in sun against an "
        "unchanged threshold: the blinds stay open in hours the standard closes them, more daylight "
        "is retained than the standard allows, and any sDA computed against the resulting schedule "
        "is OVERSTATED. That direction is why this is refused rather than labelled -- above "
        f"{LM83_BLIND_AREA_GENERAL_SF:.0f} sf the same mismatch over-closes and understates sDA, "
        "and there it is reported and the schedule published. Place the grid with LM-83's own rule "
        "and re-run the direct leg over it -- "
        f"{_lm83_grid_recreate_call(loaded, project=project, grid=grid)} then {call} -- or, if "
        "the polygon is the number that is wrong, re-create the grid with the outline the matrix "
        "was actually measured over." + _lm83_grid_recreate_caveat(loaded, grid=grid)
        + (f" {before}send that run_annual call." if before else "")
    )
    logger.info("results(action='blind_schedule') refused: %s", message)
    return refuse(
        message,
        [Advisory(
            code="BLIND_TRIGGER_MIXED_BASIS", severity="error", field="grid", message=message,
        )],
    )


def _in_sun_counts_by_column(
    path: Path, *, direct_id: str, expected_points: int | None, window: frozenset[int],
    threshold_lux: float, content_digest: str | None,
) -> dict[int, int]:
    """Stream the direct matrix once, accumulating **one integer per column**: how many sensors
    were above `threshold_lux` in that hour.

    **Counts, never a matrix.** `rad_core.blinds`' seam takes counts precisely because a
    materialised annual matrix is ~607 MB at the 456-point office grid and tens of gigabytes at
    `GRID_VERY_LARGE`, and because the shape is hostile as well as large: the trigger needs a
    per-*hour* count while `iter_annual_lux_rows` yields per-*sensor* rows. So the accumulator is an
    `n_hours`-long integer vector filled as the rows go past, and nothing else is held.

    **The comparison is `>=`, against a threshold this function is handed** (R861: LM-83-12
    section 2.2.6 defines direct sunlight as "1,000 lux or more"; it was `>` under R110).
    `LM83_ASE_THRESHOLD_LUX` is imported by the caller and never re-typed here, so the trigger and
    ASE read the same number by construction rather than by two call sites agreeing.

    **A non-finite cell refuses the whole matrix, and the check is no longer written here.** It was
    -- one `math.isfinite` per cell in the loop below -- and that placement was the defect carried
    as E4: this module holds four unrelated cell-level readers and a guard in one of them covers
    one of four, which is exactly how `compliance`, `metrics`, `summary`, `points`, `timeseries`
    and `export` were all still reading `NaN` cells while this function refused them. The guard now
    lives in `rad_engine.results`' row parsers -- *"a cell of a Radiance illuminance matrix must be
    a finite number"* is a fact about the file format, which CONTEXT.md gives to `rad_engine` -- and
    `_lux_rows` shapes it back into `DIRECT_MATRIX_UNREADABLE` for this artifact. Nothing about the
    refusal's reach changes: the check still runs on **every** cell, inside the window and outside
    it, because what it establishes is a property of the file rather than of the analysis period.

    **The counts are seeded over `window`, not over `range(hours)`.** `derive_schedule` raises when
    a window column has no count, and that guard is only as good as the column space the two sides
    share: seeding from the same `window` object the schedule is derived over is what makes the
    agreement structural instead of coincidental. Columns outside the window are visited by the
    reader (the rows are dense) and deliberately not counted -- they are not part of the analysis
    period, and `derive_schedule` ignores keys outside `window` anyway.
    """
    counts = dict.fromkeys(window, 0)
    for lux in _lux_rows(
        path, artifact_id=direct_id, expected_points=expected_points,
        content_digest=content_digest,
    ):
        for hour, value in enumerate(lux):
            if value >= threshold_lux and hour in counts:
                counts[hour] += 1
    return counts


#: How many bytes of a sealed `sched:<grid>` this module will hold in memory to parse.
#:
#: **A bound on a manifest-named path, for `_MatrixTooLarge`'s reason one artifact kind over**
#: (ruling R50: a manifest is untrusted on every action that touches it). `json.load` has no
#: incremental mode and no size limit, so an unbounded read here is the same OOM shape
#: `_MatrixTooLarge` was written for, reached through a different file. The legitimate size is
#: known and small: `rad_engine.schedule` writes `n_groups * n_hours` state strings plus `n_hours`
#: column integers, which is **192 399 bytes** at two groups over a common year (measured on this
#: machine at `749fd6a` on this section's own fixture) and ~1.8 MB at twenty groups over a leap
#: year -- so 64 MiB is more than a real schedule can reach and far less than a machine's memory.
#: Past it the schedule is not parsed and `groups` stays `null`, with the reason named: the
#: whole-grid answer below is unaffected and withholding it would trade a correct number for a
#: repair only the caller can make.
_MAX_SEALED_SCHEDULE_BYTES: Final[int] = 64 * 1024 * 1024

#: The one sentence `BLIND_ORDER_SUBSTITUTED` carries verbatim, quoted in the register bullet.
#:
#: **The register row and the emitted message are one string, not two that agree today.**
#: `tests/test_advisories.py`'s ratchet scans for emit *sites* and never for truth, which is how
#: three advisory rows shipped untrue on the 7c branch; `tests/test_results_tool.py::
#: TestTheNewAdvisoryRowsAreTrue` parses the sentence out of docs/tool-surface.md's own bullet and
#: asserts it appears in what reached the wire, so editing one word of either goes red.
#:
#: **The direction is R137's, not the design brief's.** The brief for this task asked this row to
#: say a wrongly-ordered scan "closes a superset and therefore understates sDA". Owner ruling R137
#: struck exactly that claim from `rad_core.blinds.ClosingOrder` with a hand-derived
#: counterexample: two orders scan two different sequences of prefixes and the sets they settle on
#: can be **disjoint**, so neither is a superset of the other. What survives is the nesting case,
#: and it is stated as a case rather than as the rule.
_ORDER_SUBSTITUTED_SENTENCE: Final[str] = (
    "Not every window group declared a closing role, so the order LM-83 section 2.2.6's "
    "DISCUSSION "
    "advises -- 'view' window groups first and 'clerestory' groups last -- was substituted by "
    "sill height, lowest aperture first; where the substituted order's closed set nests around a "
    "correctly-ordered scan's, its extra closures are real over-closures that under-retain "
    "daylight and understate sDA, and where the two do not nest the direction is not determined."
)

#: The one sentence `BLIND_SCHEDULE_ORDERED_PREFIX` carries verbatim, quoted in the register
#: bullet. Same discipline as `_ORDER_SUBSTITUTED_SENTENCE`, and the same correction: the brief
#: asked for "closes a superset", and ruling R137 leaves only the cardinality bound.
_ORDERED_PREFIX_SENTENCE: Final[str] = (
    "LM-83 section 2.2.6 lets window groups close 'in any combination until the criterion is "
    "met', and this schedule searched that combination as an ordered prefix of the closing order "
    "rather than as a minimum-cardinality subset, so it closes at least as many groups as a true "
    "minimum-cardinality search would; where its closed set nests around that search's, the extra "
    "closures under-retain daylight and understate sDA, and where the two do not nest cardinality "
    "alone does not determine the direction."
)


def _order_substituted_sentence(edition: Lm83Edition) -> str:
    """`BLIND_ORDER_SUBSTITUTED`'s sentence under `edition`: `_ORDER_SUBSTITUTED_SENTENCE` under
    LM-83-12. Under LM-83-22 (R864, design section 2, row 12) section 4.6 advises no order --
    *"Any combination of fully open and fully closed settings across the different window groups
    that meets the 2% rule is permitted"* -- so the sentence says the fixed order is this server's
    choice, and claims no direction: a combination the rule permits is not an over-closure of it.
    """
    if edition != LM83_22:
        return _ORDER_SUBSTITUTED_SENTENCE
    return (
        "Not every window group declared a closing role, so the closing order was substituted by "
        "sill height, lowest aperture first. LM-83-22 section 4.6 permits any combination of open "
        "and closed groups that meets the 2% rule; the fixed order (view groups first, clerestory "
        "last) is this server's choice."
    )


def _declare_roles_remedy(loaded: LoadedProject, *, project: str, order: Sequence[str]) -> str:
    """`BLIND_ORDER_SUBSTITUTED`'s redefine, spelled whole for each group of `order` that declares
    no closing role now (final fix A, fix round 3; global constraints, R133): `aperture` from the
    group's own record (`window_group_tool._redefine_call`, which says what to pass when the record
    carries none), `closing_role='view'`, and the group's own mark. Groups that declare a role are
    left as they are, since a redefine of them changes nothing the order reads; the parenthesis
    names the one other role a group can take."""
    records = {
        record.id.removeprefix(WINDOW_GROUP_INPUT_PREFIX): record
        for record in loaded.manifest.inputs if record.kind == "window_group"
    }
    calls = [
        _redefine_call(
            project, name, records.get(name), automated=is_automated(records[name])
            if name in records else False, closing_role="view",
        )
        for name in order
        if getattr(records.get(name), "role", None) not in CLOSING_ROLES
    ]
    if not calls:
        return "Every window group of that order declares a closing role now, so redefine none"
    return (
        "Declare a closing role for each window group that has none -- "
        + "; ".join(calls)
        + " (closing_role='clerestory' in place of 'view' for a clerestory group)"
    )


def _ordered_prefix_sentence(edition: Lm83Edition) -> str:
    """`BLIND_SCHEDULE_ORDERED_PREFIX`'s sentence under `edition`: `_ORDERED_PREFIX_SENTENCE` under
    LM-83-12; under LM-83-22 the same cardinality bound, against section 4.6's rule (R864)."""
    if edition != LM83_22:
        return _ORDERED_PREFIX_SENTENCE
    return (
        "LM-83-22 section 4.6 permits any combination of fully open and fully closed window groups "
        "that meets the 2% rule, and this schedule searched for one as an ordered prefix of the "
        "closing order rather than as a minimum-cardinality subset, so it closes at least as many "
        "groups as a true minimum-cardinality search would; where its closed set nests around "
        "that search's, the extra closures retain less daylight and give a lower sDA than that "
        "search's combination, and where the two do not nest cardinality alone does not determine "
        "the direction."
    )


def _ordered_prefix_message(
    sealed: _SealedGroupSchedule, *, edition: Lm83Edition, direct_id: str,
) -> str:
    """`BLIND_SCHEDULE_ORDERED_PREFIX`'s message for a published sealed schedule.

    With no automated group it is the text it was. R869/R870: an automated group is in `groups`
    and out of the order, so the message names each one and where its states came from, and the
    edition's rule for its sunlight; with every group automated the order is empty and no prefix
    scan ran, which is said instead of the scan sentence.
    """
    tail = (
        f"`states` beside it is the whole-grid series over {direct_id!r} with every aperture open, "
        "which is a different quantity and must not be distributed over these groups."
    )
    if not sealed.automated:
        return (
            f"{_ordered_prefix_sentence(edition)} The order scanned was "
            f"{', '.join(sealed.order)}, first closed first, and `groups` carries one state "
            f"per analysis-window hour for each of them. {tail}"
        )
    scan = (
        f"{_ordered_prefix_sentence(edition)} The order scanned was {', '.join(sealed.order)}, "
        "first closed first."
        if sealed.order else
        "No window group is in the closing order, so no prefix scan ran: every group is marked "
        "automated."
    )
    origin = {
        "bound_schedule": "its bound hourly schedule",
        "held_closed": "held closed in every hour, with no schedule bound",
    }
    each = "; ".join(f"{name}, {origin[source]}" for name, source in sealed.automated.items())
    rule = (
        f"{section('blind_trigger', LM83_22)} excludes their sunlight from the 2 % trigger"
        if edition == LM83_22 else
        _lm83_12_automated_rule("the trigger")
    )
    return (
        f"{scan} Marked automated and out of the order, with the states each one's own source "
        f"decided: {each}; {rule}. `groups` carries one state per analysis-window hour for every "
        f"group. {tail}"
    )


def _area_sf_of(document: dict) -> str:
    """The sealed area, for guard 7's message only -- never for the comparison.

    Split out so the message can name a value the guard deliberately does not key on without the
    branch having to reason about whether it is there or what type it is: a document that reached
    guard 7 has been validated for `states`, `columns`, `order` and `order_substituted` and for
    nothing else.

    **`bool` is excluded explicitly**, because `isinstance(True, int)` is `True` in Python and this
    test used to be `isinstance(area, (int, float))` alone (fix round 2, M-d): a document carrying
    `"area_sf": true` rendered *"True sf sealed"* inside a message about a trigger disagreement --
    a nonsense quantity in the one sentence a caller reads to decide what to repair.
    """
    area = document.get("area_sf")
    if isinstance(area, bool) or not isinstance(area, (int, float)):
        return "no area recorded"
    return f"{area} sf sealed"


def _oversized_schedule(sched_id: str, size: int) -> str:
    """The reason `_MAX_SEALED_SCHEDULE_BYTES` refuses, written once for its two guards.

    Two, because the bound is checked twice deliberately: on the open descriptor before anything is
    allocated, and again on `len(raw)` because the read asks for one byte past the cap so a file
    that grows *during* the read cannot slip through. One string, so the two cannot come to say
    different things about the same limit.
    """
    return (
        f"{sched_id!r} is at least {size} bytes, past the {_MAX_SEALED_SCHEDULE_BYTES} this "
        "server will hold in memory to parse a schedule -- a real one is under two megabytes at "
        "twenty window groups over a leap year"
    )


@dataclass(frozen=True)
class _SealedGroupSchedule:
    """`sched:<grid>`'s three published fields, already sliced to the analysis window."""

    states: dict[str, list[str]]
    order: list[str]
    order_substituted: bool
    #: R869/R870: each automated group and where its states came from (`"bound_schedule"` or
    #: `"held_closed"`), from the file's `automated_sources`; `{}` when none is automated.
    automated: dict[str, str]


class _SealedScheduleUnusable(Exception):
    """No sealed per-group schedule could be published, and `reason` says which of the ways.

    **Not a refusal, and deliberately not a `ProjectError`.** Every condition it carries leaves the
    whole-grid schedule below correct and complete -- `groups` is the part that cannot be filled --
    and R116 chose `null` over an absent key precisely so this state is expressible. It is caught
    in `_blind_schedule` and turned into `BLIND_SCHEDULE_NOT_PER_GROUP` at `warn`, whose message
    embeds `reason`. The one condition that is *not* here is a path outside the roots: `contain`
    raises `PathOutsideRoot` straight past this class, because a manifest naming a path out of the
    project is R50's condition and is refused wherever it is met, never downgraded to an advisory.
    """

    def __init__(self, reason: str, *, repair: str | None = None, spelled: bool = False) -> None:
        super().__init__(reason)
        self.reason = reason
        #: Final fix F, fix round 5 (P2): `repair` spells the run_annual call whole, its
        #: `lm83_edition` included, so the LM-83-22 message adds no "pass lm83_edition" sentence.
        self.spelled = spelled
        #: The sentence `BLIND_SCHEDULE_NOT_PER_GROUP` ends with: what would seal a per-group
        #: schedule. `None` keeps the general re-run sentence; only the "nothing registered" case
        #: sets it, because there a re-run as it stands may seal nothing (`_no_schedule_repair`).
        self.repair = repair


#: The general repair `BLIND_SCHEDULE_NOT_PER_GROUP` ends with: true wherever a sealed schedule was
#: read and found unusable, since a three-phase lm83 run on window groups sealed it once.
_RESEAL_SCHEDULE_REPAIR: Final[str] = (
    "Re-run run_annual for this grid with blind_control='lm83' to seal a per-group schedule."
)


#: How a caller reaches the three-phase chain, which alone seals a schedule (final review A2,
#: measured through `run_annual(dry_run=True)` on a two-state project): `metrics=['da']` infers it
#: with `method` left `'auto'` or set to `'3phase'`; 'ase' forces the five-phase chain (rule 1, and
#: `method='3phase'` beside it is refused), 'sda' is accepted only beside 'ase'
#: (`SDA_REPORTED_ALONE`), so `rad_core.method`'s rule 3 never decides a `run_annual` call; and
#: an explicit `method='5phase'` is a five-phase run, which seals no schedule.
_THREE_PHASE_ROUTE: Final[str] = (
    "on the three-phase chain: metrics without 'ase' (for example metrics=['da']), with method "
    "left 'auto' or set to '3phase' -- 'ase' selects the five-phase chain, 'sda' is accepted only "
    "beside 'ase', and a five-phase run seals no schedule"
)


def _both_states_current(loaded: LoadedProject, *, grid: str) -> str | None:
    """A window group whose every CURRENTLY bound state has a current result on this grid, when it
    binds two or more -- the per-group results a three-phase `lm83` run seals before its schedule.

    Read from the project's present binding (`chain.states_by_group`, the `tmx:<group>:<state>`
    ids), not from every `ill:<grid>:<group>:<state>` id in the manifest: a `blind_control='none'`
    run mints that id too, so ids left by earlier runs can add up to two states for a group that
    binds one now (final review A2). Each result must be valid (`rad_engine.evaluate_loaded`) and
    built from that state's current `tmx`. With two states bound, `blind_control='none'` refuses
    the group (`chain.three_phase_target`), so a run that sealed both is an lm83 run, unless the
    results were left by single-state runs before the second state was bound; the sentence this
    feeds is conditional for that reason.
    """
    from rad_engine.chain import group_state_ill_id

    statuses = evaluate_loaded(loaded.manifest)
    for group, states in sorted(states_by_group(loaded).items()):
        if len(states) < 2:
            continue
        current = True
        for state in states:
            ill_id = group_state_ill_id(grid, group, state)
            try:
                record = loaded.manifest.artifact(ill_id)
            except KeyError:
                current = False
                break
            status = statuses.get(ill_id)
            from_tmx = f"tmx:{group}:{state}" in record.built_from
            if status is None or not status.valid or not from_tmx:
                current = False
                break
        if current:
            return group
    return None


def _no_schedule_repair(loaded: LoadedProject, *, grid: str) -> str:
    """What would seal `sched:<grid>` where none is registered (final review W-M5, A2).

    "Re-run with blind_control='lm83'" was false in two of the states that reach here, so the
    sentence is chosen from the manifest:

    - **no `window_group` input** -- a two-phase run resolves no window group, and re-running it
      with `lm83` traces no per-group matrix, so no re-run as it stands seals a schedule;
    - **a group binding two or more states has a current result for each on this grid**
      (`_both_states_current`) -- what a three-phase `lm83` run seals before its schedule, so the
      sentence says such a run may have stopped between the two (a refused per-group matrix, a
      cancel or another failure, which only that job's record says);
    - otherwise the general sentence, naming the route to the three-phase chain
      (`_THREE_PHASE_ROUTE`).
    """
    if not any(record.kind == "window_group" for record in loaded.manifest.inputs):
        return (
            "This project defines no window group (a two-phase run resolves none), so re-running "
            "it with blind_control='lm83' cannot seal a per-group schedule: define each window "
            "group over its aperture with window_group(action='define'), bind its 'open' and "
            "'closed' BSDF states with window_group(action='assign_bsdf'), and then run "
            f"run_annual for this grid with blind_control='lm83' {_THREE_PHASE_ROUTE}."
        )
    group = _both_states_current(loaded, grid=grid)
    if group is not None:
        return (
            f"Window group {group!r} has a current result on this grid for every blind state it "
            f"binds ('ill:{grid}:{group}:<state>'), which a run_annual run with "
            "blind_control='lm83' on the three-phase chain seals before its schedule, and "
            f"no {blind_schedule_id(grid)!r} is registered. If such a run stopped before sealing "
            "it, job(action='status') on that run names the step that stopped it; if that step "
            f"is a per-group matrix 'asemx:{grid}:<group>' refused with ASE_DIRECT_MATRIX_EMPTY, "
            "that refusal says why the per-group matrix was refused and what could repair it. "
            "Otherwise run run_annual for this grid with blind_control='lm83' "
            f"{_THREE_PHASE_ROUTE}."
        )
    return (
        "Re-run run_annual for this grid with blind_control='lm83', with each window group's "
        f"'open' and 'closed' BSDF states bound, {_THREE_PHASE_ROUTE}, to seal a per-group "
        "schedule."
    )


def _guard_7_reason(
    sched_id: str, document: dict, *, schedule: BlindSchedule, edition: Lm83Edition,
    loaded: LoadedProject, project: str, grid: str,
) -> _SealedScheduleUnusable:
    """Guard 7's refusal to publish, for `_sealed_group_schedule`'s one raise: the closing rule
    differs. Under LM-83-22 it cites section 4.6 and names LM-83-12's table without a clause
    number of its own.

    Final fix F, fix round 5: the re-scan, spelled whole from the records under this call's
    edition (`_rerun_blend_call`, the three-phase run that alone seals a schedule, after the
    `assign_bsdf` calls it needs first; fix round 3, H2), is the `repair` the message ends with,
    the one remedy it gives (P2: the general `_RESEAL_SCHEDULE_REPAIR` sentence followed it, a
    second, unspelled form of the same run, and under LM-83-22 so did "pass lm83_edition"). The
    threshold-only branch is gone (L5): `threshold_lux` is written only from
    `LM83_ASE_THRESHOLD_LUX`, which guard 7 reads too and which has been 1000.0 since it was
    introduced, and the sealed file is digest-checked (guard 4), so no tool and no project
    `master` wrote reaches a different one."""
    sealed_band = document.get("band")
    sealed_threshold = document.get("threshold_lux")
    first = _bind_first_clause(loaded, project=project)
    rerun = (
        f"{first}{'r' if first else 'R'}e-run "
        f"{_rerun_blend_call(loaded, project=project, grid=grid, edition=edition)}"
    )
    if edition == LM83_22:
        return _SealedScheduleUnusable(
            f"{sched_id!r} was scanned under the {sealed_band!r} rule at {sealed_threshold!r} "
            f"lux, and this call resolved {section('blind_trigger', edition)}'s "
            f"{schedule.band!r} rule at {schedule.threshold_lux!r} lux -- more than 2 % of the "
            "points in direct sun, at every area. Of LM-83-12's area table only the '>=1000sf' row "
            "is that rule; its other rows close on an absolute count of points in sun, so "
            "publishing the two side by side would put a per-group schedule and a whole-grid one "
            f"under triggers that are not the same trigger ({_area_sf_of(document)})",
            repair=f"{rerun}, to re-scan under this rule.", spelled=True,
        )
    return _SealedScheduleUnusable(
        f"{sched_id!r} was scanned under LM-83 section 2.2.6's {sealed_band!r} row at "
        f"{sealed_threshold!r} lux, and this call resolved the {schedule.band!r} row at "
        f"{schedule.threshold_lux!r} lux from the grid's recorded polygon "
        f"({_area_sf_of(document)}). The rows span an order of magnitude -- from more than 1 "
        "point in sun below 200 sf to more than 2 % of points at 1 000 sf and above -- so "
        "publishing the two side by side would put a per-group schedule and a whole-grid one "
        "under triggers that are not the same trigger",
        repair=f"{rerun}, to re-scan under the polygon it is registered with now.", spelled=True,
    )


def _sealed_group_schedule(
    runtime: Runtime, loaded: LoadedProject, *, grid: str, schedule: BlindSchedule, project: str,
    edition: Lm83Edition = DEFAULT_LM83_EDITION,
) -> _SealedGroupSchedule:
    """Read `sched:<grid>`, slice it to `schedule.columns`, and check it was scanned under
    `schedule`'s own trigger -- or raise `_SealedScheduleUnusable`.

    **The artefact, not the record of it.** `order` and `order_substituted` are read out of the
    JSON `rad_engine.schedule` wrote, not out of the manifest `parameters` that also carry them:
    the two are sealed by one function today and a payload that quoted the record while publishing
    the file's own states would be able to disagree with itself. The manifest's copy is what
    `_parameters_match` compares for reuse; this is what the run decided.

    **Every guard below leaves `groups` null rather than refusing**, for the reason
    `_SealedScheduleUnusable` gives, and every one of them is a state in which a *populated*
    `groups` would be a wrong per-group answer:

    1. **no `sched:<grid>` registered** -- the ordinary case, and not an error: `direct_sun_groups`
       returns nothing unless `blind_control="lm83"` (`rad_engine.chain`), so a two-phase run, a
       `blind_control="none"` run and a run with no window group at all all reach this action with
       `illdir:<grid>` sealed and no schedule beside it.
    2. **the file cannot be read**, or is not a regular file -- `open_regular_file`'s hazard, the
       same one `file_digest` adopted the primitive for: a FIFO at a registered path blocks a read
       to EOF forever.
    3. **it is larger than `_MAX_SEALED_SCHEDULE_BYTES`.**
    4. **its bytes are not the bytes this project sealed** -- the drift `MATRIX_CONTENT_DRIFTED`
       refuses one file kind over. **One read, so unlike that one it *is* TOCTOU-proof**: the
       buffer that is hashed is the buffer that is parsed, which `_lux_rows` cannot do because an
       annual matrix does not fit in memory and a schedule does. `_lux_rows` gets the weaker form
       of the same property -- one **held descriptor** rather than one buffer, which defeats
       replacing the file but not rewriting it in place; see that function.
    5. **it is not the JSON this server writes** -- a missing or mistyped `states`, `order` or
       `order_substituted`, a state that is neither `"open"` nor `"closed"`, a group in `states`
       that neither the order nor `automated` names, or a named group with no states. R869/R870:
       `automated` (absent means none) is a list of group names disjoint from `order`, no name
       is repeated in either list, `order` may be empty only when `automated` is not, and
       `automated_sources` names exactly the automated groups, each `"bound_schedule"` or
       `"held_closed"`; each shape has its own message.
    6. **it does not cover the analysis window** -- a sealed schedule built over a different
       matrix's hours. Silently dropping the missing columns would publish a short list beside a
       full `columns`, and reading a missing hour as `"open"` is the favourable state as a
       by-product of an hour nothing was measured for, which `rad_core.blinds` forbids.
    7. **it was scanned under a different trigger from the one this call resolved** -- fix round 1,
       Important 5, and the only *semantic* guard here; every other one is about shape or bytes.

       The file carries the `band` and `threshold_lux` its scan ran under
       (`rad_engine/schedule.py`), and this call re-derives its own from the grid's **current**
       recorded polygon. They can come apart: `sensor_grid(action="create")` performs no
       duplicate-name check and `register_input` replaces the whole record, so re-creating `floor`
       over a larger polygon moves the whole-grid half's band while the sealed half keeps the old
       one. LM-83 section 2.2.6's four rows span an order of magnitude -- the same fact
       `_analysis_area`'s own refusal is justified by -- so the two published schedules would be
       answering under triggers a factor of ten apart with nothing on the wire saying so.

       **`band` and `threshold_lux`, and deliberately not `area_sf`.** The reason is a bijection,
       not a count: `rad_core.blinds._area_band` and `rad_core.metrics.blinds_close` branch on the
       **same three constants in the same order** (`LM83_BLIND_AREA_GENERAL_SF`, `_MID_SF`,
       `_SMALL_SF`, each with the same fallthrough), so the band a schedule is labelled with and
       the branch `blinds_close` takes are the same choice named twice -- and *given equal band*
       the predicate is identical for every area inside it. So an exact float compare on a value
       that travels through `repr()` on an argv and is then re-derived from the polygon would
       withhold a correct schedule over a difference that changes no hour. The area is still named
       in the reason, because it is what a caller repairs. *(Final fix F, fix round 5, L5: the
       comparison is now the band's closing rule alone. `threshold_lux` is written only from
       `LM83_ASE_THRESHOLD_LUX`, the constant this call reads too, and the file is digest-checked
       by guard 4, so a different threshold needs the manifest's digest resealed by hand; the
       branch that named one is gone.)*

       **This paragraph said "exactly four `>=` comparisons" until fix round 2**, which is wrong as
       counted -- there are **three** comparisons and a fallthrough, four *bands* -- and the count
       was never the argument anyway. The bijection is, and it is the form that stays true if a
       fifth band is ever added.

       **The bands are compared by `rad_core.blinds.same_closing_rule`, not by equality** (R864,
       design section 3). LM-83-22's `"flat_2pct"` and LM-83-12's `">=1000sf"` are one predicate
       under two names -- more than 2 % of the points in sun -- so a schedule sealed under one and
       read under the other is the same trigger and is published. `edition` is the call's
       `lm83_edition`, for the reason's wording only: under LM-83-22 it cites section 4.6 and
       names LM-83-12's table without a clause number of its own.

       **`points_total` is not guarded, and that is a gap rather than a decision against it.** The
       `>= 1 000 sf` row divides by it, but the sealed JSON does not carry it -- `n_points` is on
       the manifest `parameters` only, and reading it from there is exactly the "record, not
       artefact" move the top of this docstring exists to refuse.

       **The per-point comparison is not guarded either, and that is a gap too.** The sealed JSON
       carries `threshold_lux` but not whether a point counted as in sun at `>` or at `>=` it, and
       nothing here re-checks `blind_schedule_revision`, so a schedule sealed before R861 (`>`) is
       served until the next `run_annual(..., blind_control='lm83')` re-derives it through the
       reuse path. No revision key is checked at read time anywhere. A cell of exactly 1 000.0 lux
       in a real direct-sun matrix was not observed on the measured run, and is argued against
       only for `rcalc`'s 179 times a 7-digit grey value (a weighted sum of three channels is not
       covered), so on real output the two rules are expected, not shown, to give the same
       schedule. *(Corrected 2026-10-12, Phase 25 Branch B final review, R861: it said "a real
       direct-sun matrix cannot hold exactly 1 000.0 lux (`rcalc`'s 179 times a 7-digit value),
       so the two rules give the same schedule on real output".)*
    """
    columns = schedule.columns
    sched_id = blind_schedule_id(grid)
    try:
        record = loaded.manifest.artifact(sched_id)
    except KeyError:
        raise _SealedScheduleUnusable(
            f"no {sched_id!r} is registered for this grid. That artifact is sealed only by a "
            "run_annual run with blind_control='lm83', which is the only run that traces one "
            "direct-sun matrix per window group with every other group's aperture blacked; "
            "without those matrices a count of points in sun cannot be attributed to the group "
            "that lit them",
            repair=_no_schedule_repair(loaded, grid=grid),
        ) from None

    # **A second named seam, not a second copy of the first.** `_contained_matrix` is still the one
    # way this module opens a manifest *matrix* -- it reads an annual `.ill` header and cross-checks
    # a shape, and neither applies to a JSON document. What both seams share is the order:
    # containment before the syscall (R48/R50).
    sealed_path = _contained_schedule_path(runtime, record)

    # **The drift check and the parse are one read**, which `_lux_rows` cannot manage and this can:
    # an annual matrix explicitly does not fit in memory and a schedule explicitly does, so the
    # bytes that are hashed are the bytes that are parsed and there is no window at all for a
    # writer inside an allowed root to change the file between the two. `_lux_rows` gets the weaker
    # form: one held descriptor rather than one buffer
    # (`rad_engine.results.iter_verified_annual_lux_rows`), which defeats replacement but leaves an
    # in-place rewrite of the same inode undetected. `bytes_digest` is `file_digest`'s own value
    # over a buffer -- one definition of "these are the sealed bytes", pinned equal in
    # `tests/test_project.py` -- and `read_epw_with_digest` is the same shape one file format over.
    #
    # **The size is measured on the descriptor and the read is bounded, which is that same shape
    # and was not, until fix round 1's Important 2.** `sealed_path.stat()` followed by an unbounded
    # `handle.read()` measured one resolution of the path and read another -- exactly the TOCTOU
    # `open_regular_file`'s own docstring names (*"a file that passes `path.stat()` can be swapped
    # ... `os.fstat` interrogates the object already open"*) -- and a file that grew between the two
    # was parsed whole regardless of the guard. `os.fstat` on the open handle, then a read of one
    # byte past the cap so a file that grows *during* the read is still caught by `len(raw)`:
    # `rad_engine.weather.read_epw_with_digest` does precisely this, and this comment used to cite
    # it while doing something weaker.
    try:
        with open_regular_file(sealed_path, "rb") as handle:
            size = os.fstat(handle.fileno()).st_size
            if size > _MAX_SEALED_SCHEDULE_BYTES:
                raise _SealedScheduleUnusable(_oversized_schedule(sched_id, size))
            raw = handle.read(_MAX_SEALED_SCHEDULE_BYTES + 1)
    except (OSError, NotARegularFileError) as error:
        raise _SealedScheduleUnusable(
            f"{sched_id!r} could not be read: {error}"
        ) from error
    if len(raw) > _MAX_SEALED_SCHEDULE_BYTES:
        raise _SealedScheduleUnusable(_oversized_schedule(sched_id, len(raw)))

    content_digest = record.content_digest
    if content_digest is not None:
        actual = bytes_digest(raw)
        if actual != content_digest:
            raise _SealedScheduleUnusable(
                f"{sched_id!r} no longer matches the bytes this project sealed: the manifest "
                f"records {content_digest} and the file on disk hashes to {actual}. Only the "
                "states moved, so nothing else in this read path would have noticed; re-run "
                "run_annual for this grid to seal a matching pair"
            )

    try:
        document = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as error:
        raise _SealedScheduleUnusable(
            f"{sched_id!r} could not be read as the JSON schedule rad_engine.schedule writes: "
            f"{error}"
        ) from error

    if not isinstance(document, dict):
        raise _SealedScheduleUnusable(
            f"{sched_id!r} is a {type(document).__name__}, not the JSON object a schedule is"
        )
    order = document.get("order")
    substituted = document.get("order_substituted")
    states = document.get("states")
    sealed_columns = document.get("columns")
    automated = document.get("automated", [])
    if not (isinstance(automated, list) and all(isinstance(name, str) for name in automated)):
        raise _SealedScheduleUnusable(
            f"{sched_id!r} carries an 'automated' field that is not a list of window group names, "
            "the shape rad_engine.schedule writes for the groups marked automated, so which "
            "groups' blinds followed their own schedule cannot be read from it"
        )
    if not (
        isinstance(order, list)
        and isinstance(automated, list)
        and (order or automated)
        and all(isinstance(name, str) for name in (*order, *automated))
        and isinstance(substituted, bool)
        and isinstance(states, dict)
        and isinstance(sealed_columns, list)
        and all(isinstance(column, int) and not isinstance(column, bool)
                for column in sealed_columns)
    ):
        raise _SealedScheduleUnusable(
            f"{sched_id!r} does not carry the 'states', 'columns', 'order' and "
            "'order_substituted' fields rad_engine.schedule writes, in the shapes it writes them"
        )
    named = [*order, *automated]
    if len(set(named)) != len(named):
        def twice(names: list[str]) -> str:
            repeated = sorted({name for name in names if names.count(name) > 1})
            return ", ".join(repr(name) for name in repeated)

        found: list[str] = []
        why: list[str] = []
        if len(set(order)) != len(order):
            found.append(f"{twice(order)} twice in its closing order")
        if len(set(automated)) != len(automated):
            found.append(f"{twice(automated)} twice among its automated groups")
        if len(set(order) | set(automated)) != len(set(order)) + len(set(automated)):
            both = ", ".join(repr(name) for name in sorted(set(order) & set(automated)))
            found.append(f"{both} both in its closing order and as automated")
            why.append(
                "an automated group's blind follows its own schedule and is never in the closing "
                "order"
            )
        if len(found) > len(why):
            why.insert(0, "a group listed twice was decided twice")
        raise _SealedScheduleUnusable(
            f"{sched_id!r} names {'; and '.join(found)}. "
            f"{'; and '.join(why)[:1].upper()}{'; and '.join(why)[1:]}"
        )
    if set(states) != set(named):
        listed = (
            f"{sorted(set(order))} in its closing order, {sorted(set(automated))} as automated"
            if automated else f"{sorted(set(order))} in its closing order"
        )
        raise _SealedScheduleUnusable(
            f"{sched_id!r} names {listed} and {sorted(states)} in its states. A group in one and "
            "not the other is a group whose blind nothing decided, or one decided and never "
            "scanned"
        )
    sources = document.get("automated_sources", {})
    if not (
        isinstance(sources, dict)
        and set(sources) == set(automated)
        and all(source in ("bound_schedule", "held_closed") for source in sources.values())
    ):
        recorded = (
            f"names {sorted(sources)} in its 'automated_sources'" if isinstance(sources, dict)
            else f"carries 'automated_sources' as a {type(sources).__name__}"
        )
        raise _SealedScheduleUnusable(
            f"{sched_id!r} names {sorted(set(automated))} as automated and {recorded}. "
            "rad_engine.schedule records each automated group there once, as 'bound_schedule' or "
            "'held_closed', so which hours were that group's own cannot be read from this file"
        )

    #: **Guard 7: the trigger, not the shape.** See this function's docstring for why `band` and
    #: not `area_sf`, and for how the two can come apart in a project nobody tampered with. Final
    #: fix F, fix round 5 (L5): the closing rule alone, no longer `threshold_lux` beside it; see
    #: `_guard_7_reason` for why.
    sealed_band = document.get("band")
    if not (isinstance(sealed_band, str) and same_closing_rule(sealed_band, schedule.band)):
        raise _guard_7_reason(
            sched_id, document, schedule=schedule, edition=edition,
            loaded=loaded, project=project, grid=grid,
        )

    published: dict[str, list[str]] = {}
    for name in named:
        row = states[name]
        if not (isinstance(row, list) and len(row) == len(sealed_columns)
                and all(state in BLIND_STATES for state in row)):
            raise _SealedScheduleUnusable(
                f"{sched_id!r} carries a states list for {name!r} that is not one "
                f"{'/'.join(sorted(BLIND_STATES))} per sealed column"
            )
        by_column = dict(zip(sealed_columns, row, strict=True))
        missing = [column for column in columns if column not in by_column]
        if missing:
            raise _SealedScheduleUnusable(
                f"{sched_id!r} covers {len(sealed_columns)} columns and says nothing about "
                f"{len(missing)} of the {len(columns)} analysis-window hours this schedule "
                f"publishes (the first is column {missing[0]}). It was sealed over a different "
                "matrix's hours, and an hour read as 'open' because it was absent is the "
                "favourable state as a by-product of an hour nothing was measured for"
            )
        published[name] = [by_column[column] for column in columns]

    return _SealedGroupSchedule(
        states=published, order=list(order), order_substituted=substituted,
        automated={name: sources[name] for name in automated},
    )


def _not_per_group_lm83_22(
    unusable: _SealedScheduleUnusable, *, direct_id: str,
) -> Advisory:
    """`BLIND_SCHEDULE_NOT_PER_GROUP` on an LM-83-22 read (R864): the same fact and direction as
    LM-83-12's message, against section 4.6.

    Section 4.6 closes blinds *by window group* and permits *any combination* of open and closed
    groups that meets the 2 % rule; it advises no closing order, so the LM-83-12 message's third
    clause has no counterpart. Closing every group is itself a combination the rule permits, so
    the direction is stated against the per-group schedule `run_annual` seals -- which closes
    groups in order only until the rule is met -- and not as an over-closure of the standard.
    """
    return Advisory(
        code="BLIND_SCHEDULE_NOT_PER_GROUP",
        severity="warn",
        field="grid",
        message=(
            f"this schedule is not resolved per window group, and `groups` is null because of it: "
            f"{unusable.reason}. The states above come from {direct_id!r}, which is one matrix for "
            f"the whole grid, so {section('blind_trigger', LM83_22)}'s requirement that blinds "
            "*close by window group* goes unimplemented on this call. **The error this would cause "
            "has a known direction.** The whole-grid in-sun count is greater than or equal to any "
            "single group's, so in every hour these states close they close every group, where a "
            "per-group schedule closes groups in order only until the 2 % rule is met -- these "
            "states are a strict UPPER BOUND on closure. Section 4.6 permits any combination that "
            "meets the rule, all groups closed included, but applied per group these states "
            "retain no more daylight than a per-group schedule would, and the sDA computed from "
            "them is no higher, so a passing space can be reported as failing. Use these states "
            "for the whole grid, and do not distribute them over window groups. "
            + (unusable.repair or _RESEAL_SCHEDULE_REPAIR)
            + ("" if unusable.spelled else
               f" Pass lm83_edition={LM83_22!r} to that run_annual call too, so the per-group "
               "schedule it seals is derived under the rule this read applies.")
        ),
    )


def _blind_schedule(
    runtime: Runtime, *, project: str, grid: str, lm83_edition: Lm83Edition = DEFAULT_LM83_EDITION,
    named: bool = False,
) -> CallToolResult:
    """`action="blind_schedule"`: LM-83 section 2.2.6's hourly schedule -- the whole-grid one
    derived here from `illdir:<grid>`, and the per-group one read from a sealed `sched:<grid>`
    where this project holds one.

    **This action does not resolve a chain result, and that is deliberate.** Every other action goes
    through `_resolve`, which selects between `ill:<grid>` and `ill5:<grid>`; the schedule reads
    neither. Routing it through `_resolve` anyway would (a) make a chain result a precondition of an
    answer that does not depend on one, and (b) drag R105's `ANNUAL_RESULT_AMBIGUOUS` onto a call
    that touches neither of the two matrices the ambiguity is between -- a caller told to pass
    `chain=` to disambiguate a matrix this action never opens. The project is opened here and
    `illdir:<grid>` goes through `_contained_matrix`, which is still *the* one way this module opens
    a manifest **matrix**; nothing about containment or the header cross-check is reproduced.
    `sched:<grid>` is a JSON document rather than an annual `.ill`, so neither the header read nor
    the shape cross-check applies to it and it has its own named seam,
    `_sealed_group_schedule` -- one seam per file format, and both contain before they open.

    Order, and every step above the one below it for a stated reason:

    1. `_analysis_area` -- `GridNotFoundError` / `_AnalysisAreaNotDerivable`, both from the manifest
       alone. Task 15 built this to be this call site's callee; the area, the ft2 conversion, the
       `area_source` label and the `ANALYSIS_AREA_UNDER_COVERED` advisory all come from there and
       are not re-derived here. A grid this server cannot size is refused before any matrix is
       looked up, because the band it selects moves the trigger by a factor of ten.

       **This is the one place the order is arguable and it is chosen deliberately.** R39 puts
       refusals above file access, and `_analysis_area` opens the grid's `.pts` (for the coverage
       advisory only, and only after its own refusals) *before* step 2's manifest lookup runs. Two
       reasons it stays here. First, the useful answer: a caller who named a grid that is not
       registered should be told *that*, not that `illdir:<a grid that does not exist>` is missing,
       which is the message they would get if step 2 ran first. Second, what R39 is actually
       protecting -- `_resolve`'s own docstring carries the measurement -- is allocations sized from
       unvalidated headers and multi-gigabyte matrix reads; a sensor grid's `.pts` is neither, and
       `_grid_polygon_coverage_or_none` swallows every failure reading it rather than letting it
       cost the area. The refusals that guard the expensive things are all still above them.
    2. the `illdir:` lookup -- `BLIND_SCHEDULE_NO_DIRECT_MATRIX`, manifest again, no file touched.
    3. `_contained_matrix` -- containment before the syscall (R48/R50), then the header, then the
       header/record cross-check (`MATRIX_SHAPE_MISMATCH`, engine invariant 3).
    4. the period and point-count guards -- both from the header, **above** `lm83_window`, the
       `dict.fromkeys` seeded from it, and the row stream (R39). **`lm83_window` is the expensive
       one and this docstring used to name only the `dict.fromkeys`**: the window is a frozenset
       over `range(header.hours)` -- 0.64 s and ~342 MB at `NCOLS=8 000 000`, measured -- and it
       was built one line above the `is_whole_year` gate that refuses it. Fixed in the whole-branch
       fix wave, together with a magnitude bound on the header integers themselves
       (`MATRIX_TOO_LARGE_TO_READ`, in `_ensure_header_matches_manifest`) so that step 3 caps both
       counts before step 4 reads either.
    4b. **the one-source guard** (Phase 7d-1 Task 16) -- `BLIND_TRIGGER_MIXED_BASIS`. `area_sf`
       selects §2.2.6's row off the recorded polygon and the row's criterion is then applied to
       `header.points`, which comes from the matrix; where the matrix carries materially fewer
       points than LM-83's own placement would put inside that same polygon
       (`rad_core.grids.lm83_grid_point_density`, i.e. `header.points / area.points_expected`,
       against `LM83_GRID_POINT_DENSITY_MIN`), the row is
       being applied to a grid it was not calibrated for. **Below 1 000 sf that refuses and at or
       above it warns**, because only below 1 000 sf is the criterion an absolute point count, and
       only there does the mismatch run in the flattering direction (sDA overstated); above it the
       criterion is a ratio, the residual error over-closes and sDA is understated. Here rather than
       lower because it needs the header and nothing more -- above the stream, above every
       allocation sized from the matrix.

       **This is not a reversal of the Phase 7c Task 15 under-coverage ruling** (R171). That ruling
       is about `grid_polygon_coverage`, a ratio of *areas* measuring how much of a room's floor a
       grid samples (a ratio of bounding boxes until Phase 7d-1 Task 17, ruling R160), and it still
       refuses nothing anywhere. This guard is a ratio of *counts*. The two disagree in both
       directions on grids this server places itself.
    5. the stream (which refuses a non-finite cell, `DIRECT_MATRIX_UNREADABLE` -- the third state a
       favourable schedule could otherwise be a by-product of, after a short matrix and an empty
       one), then `derive_schedule`.

    **`BLIND_SCHEDULE_FROM_SMEARED_SUN` is not attached, and check 7.14 therefore closes IN PART.**
    That code's condition is *"the schedule was derived under 3-phase"*, and this leg's dedicated
    `-ab 0` run makes the condition orthogonal to the chain: the schedule is derived from
    `illdir:<grid>` regardless of which chain sealed it, and no chain result enters the derivation
    at all. There is therefore no state this action can reach for which the code is true. Attaching
    it anyway would be an advisory untrue of its own condition (docs/tool-surface.md section 9); the
    plan row says the check closes in part rather than claiming a guard that cannot fire, and the
    code stays allocated for a route that can reach it.

    **6. the sealed per-group schedule, if this project holds one** -- `_sealed_group_schedule`,
    last, because it is the only step whose failure costs nothing: every one of its refusals leaves
    `groups` `null` beside a whole-grid schedule that is complete and correct, where every step
    above it decides whether there is an answer at all.

    ## `groups` is read, never re-derived, and `BLIND_SCHEDULE_NOT_PER_GROUP` is now conditional

    Phase 7c published `groups: null` on **every** schedule and said so unconditionally, because
    `illdir:<grid>` is one matrix for the whole grid and a count of points in sun there cannot be
    attributed to the group that lit them (ruling R116). Task 11 seals a per-group answer --
    `sched:<grid>`, section 2.2.6's prefix scan over one direct-sun matrix per group -- and this
    task publishes it. So the fact the code states is no longer invariant, and it is emitted where
    it is true and not otherwise.

    **What retired is the unconditional form, not the code**, and that is a deviation from this
    task's brief, which said the code retires outright. It cannot: `direct_sun_groups` returns
    nothing unless `blind_control="lm83"` (`rad_engine.chain`), while `illdir:<grid>` is sealed
    whenever the ASE leg runs -- so a two-phase run, a `blind_control="none"` run and a run with no
    window group all reach this action with a whole-grid matrix and no sealed schedule beside it.
    Retiring the code would leave those calls publishing `groups: null` with nothing saying why,
    which is the silence R116 allocated it against. Its condition narrows from *"always"* to
    *"`groups` is null"*, its message names which of `_sealed_group_schedule`'s reasons applied,
    and the register records the amendment rather than a retirement.

    **The published `groups` is the sealed file's analysis-window slice** (ruling R163). The sealed
    schedule covers every column of the matrix, because `rad_engine.blend` refuses one that is not
    exactly `--hours` long; slicing keeps `groups` parallel to `columns` and keeps the published
    length docs/tool-surface.md pins. **`hours_closed` is not re-subjected either** (ruling R164):
    it stays the whole-grid trigger's count over `columns`, and the sealed file's own count -- over
    the whole matrix, over per-group matrices, an hour counting when *any* group closed -- is a
    different number that is not published beside it.

    **`BLIND_SCHEDULE_FROM_SMEARED_SUN` is still not attached**, for the reason above: neither the
    whole-grid derivation nor the sealed schedule reads a chain result.

    ## `lm83_edition` (R864; design section 2, rows 9-12 and 39, and section 3)

    The call's edition decides the rule `derive_schedule` applies and every clause a message here
    cites. Under `"LM-83-22"`: one rule, more than 2 % of the points in direct sun at every area
    (section 4.6); `area_sf` is the glossary's points times spacing (`_analysis_area(edition=)`,
    the source `run_annual` seals the schedule from); step 4b's density guard is not applied --
    with no area table there is no row for a sparse grid to be miscalibrated against -- and neither
    is `BLIND_SCHEDULE_AREA_ABOVE_CAP` (section 4.1.1 states no cap); `BLIND_ORDER_SUBSTITUTED`
    says the fixed order is this server's choice. A per-group schedule sealed for window-group
    declarations the project no longer records is refused first (`BLIND_SCHEDULE_NOT_CURRENT`,
    `_refuse_sealed_schedule_not_current`, final review W-I1). One sealed under the other edition
    is refused below 1 000 sf of polygon, or at any area when it records an automated group with
    a bound schedule (R870) (`_refuse_sealed_edition_mismatch`, after the staleness and floor
    refusals, manifest and grid only), and published otherwise, where guard 7 reads the two bands
    as one rule. `named` says whether the caller passed the argument,
    for that refusal's wording. Under the default every output is LM-83-12's, as before.
    """
    loaded = runtime.open(project)
    direct_id = f"{_DIRECT_ARTIFACT_PREFIX}{grid}"
    registered_ids = {artifact.id for artifact in loaded.manifest.artifacts}
    # R785, before R776's floor check: `illdir:<grid>` is read below, and `sched:<grid>` by
    # `_sealed_group_schedule`, which publishes `groups` from it.
    sealed_ids = [
        artifact_id for artifact_id in (direct_id, blind_schedule_id(grid))
        if artifact_id in registered_ids
    ]
    _refuse_stale_results(loaded, sealed_ids)
    _refuse_changed_scene_includes(  # final fix E, fix round 4 (X1)
        loaded, sealed_ids, project=project, grid=grid, edition=lm83_edition,
    )
    _refuse_grid_off_recorded_floor(loaded, grid, project=project)  # R776, and E2's digest
    # Final fix E2: the area after the digest check, so LM-83-22's area (read off the points) is
    # refused by the same `SENSOR_GRID_FILES_CHANGED` every other read gives a hand-edited `.pts`.
    area = _analysis_area(loaded, grid=grid, edition=lm83_edition)
    # Final review, W-I1: a schedule sealed for marks or a closing order the project no longer
    # records is refused before the edition is compared, since that comparison's remedies read it.
    refusal = _refuse_sealed_schedule_not_current(
        loaded, project=project, grid=grid, edition=lm83_edition, reports="blind_schedule",
        blend_id=f"{_BLENDED_PREFIX}{grid}",
    )
    if refusal is None:
        refusal = _refuse_sealed_edition_mismatch(
            loaded, project=project, grid=grid, edition=lm83_edition, named=named,
            reports="blind_schedule", blend_id=f"{_BLENDED_PREFIX}{grid}",
        )
    if refusal is not None:
        return refusal

    try:
        artifact = loaded.manifest.artifact(direct_id)
    except KeyError:
        return _refuse_no_direct_matrix(
            grid=grid, direct_id=direct_id, edition=lm83_edition, loaded=loaded, project=project,
        )

    path, header = _contained_matrix(runtime, artifact, artifact_id=direct_id)
    # **The period gate above `lm83_window`, not below it.** `_refuse_unschedulable_direct_matrix`
    # takes `window` as an argument, so the call built the frozenset over `range(header.hours)`
    # before the guard that refuses a non-annual `header.hours` could run -- 0.64 s and ~342 MB at
    # `NCOLS=8 000 000`, measured. Split into two steps so the cheap predicate decides first and
    # `window` is only built for a matrix that is going to be scheduled; the refusal is the same
    # one either way, and it is handed an empty window when it fires on the period.
    # **`<= 0`, not `== 0`** (fix round 2, N1). `header.points` is read out of an `.ill` header,
    # and a hand-edited manifest can agree with it on both sides -- the threat `_MatrixTooLarge`'s
    # own docstring is written against -- so `NROWS=-5` reaches here as `-5`. Under `== 0` it fell
    # past this guard and into the density gate below, and **both** of that gate's historical
    # spellings answered it wrongly. Measured at the tool boundary, on one fixture, three states:
    #
    #   - inline division (Phase 7d-1 Task 16, first commit): refused with
    #     `BLIND_TRIGGER_MIXED_BASIS` at `error`, message *"a density of -10%, below 50%"* --
    #     a **negative density published as a measured value**, with a recovery (re-place the grid)
    #     that cannot repair a nonsense header;
    #   - through `lm83_grid_point_density` (fix round 1): its `points < 0` guard raises, and
    #     `results`' broad `except ValueError` further down this module shapes it into a **code-less
    #     refusal reading "the annual result for grid 'floor' could not be read: a grid cannot have
    #     -5 sensor points, so there is no density to measure"** -- attributed to an annual result
    #     this action opens none of (see this function's own first paragraph), carrying a `rad_core`
    #     density sentence to a caller whose problem is the matrix header.
    #
    #   The refusal is therefore **shaped** in both -- `structured_content` is populated and check
    #   0.4 is not violated, which is what the broad `ValueError` clause is there for -- but it is
    #   wrong in both, once as a false number and once as a false attribution.
    #
    # Widening here is the right direction rather than softening the pure layer's guard: a negative
    # sensor count is not a density this server should be computing, it is a matrix it should be
    # refusing, and it is now refused above the stream with every other header-derived guard, by the
    # branch whose message is actually about the point count.
    if header.points <= 0 or not is_whole_year(header.hours):
        return _refuse_unschedulable_direct_matrix(
            grid=grid, direct_id=direct_id, header=header, window=frozenset(),
            edition=lm83_edition,
        )
    # R860: `illdir:<grid>` reaches its weather input through `smxsun:<weather>`, not `smx:`.
    placed = _lm83_window_for(loaded, direct_id, header.hours, sky_kind=_SMXSUN_KIND)
    window = placed.columns
    if not window:
        return _refuse_unschedulable_direct_matrix(
            grid=grid, direct_id=direct_id, header=header, window=window, edition=lm83_edition,
        )

    #: **One source for the area and the point count** (Phase 7d-1 Task 16, rulings R156/R171/R172).
    #: `area.points_expected` is LM-83's own placement over the *same* polygon `area.area_sf` came
    #: from, so this division compares the count §2.2.6's row will be applied to against the count
    #: that row was calibrated for. `None` means the polygon could not be gridded, and an unmeasured
    #: density neither refuses nor warns -- see `_lm83_expected_points_or_none`.
    #:
    #: **Here, above the stream and below the header.** The refusal needs `header.points`, which is
    #: the number `blinds_close` actually divides and compares (the manifest's `n_points` is
    #: cross-checked against it by `_ensure_header_matches_manifest`, but it is not the number the
    #: arithmetic uses). That puts it below step 3 by necessity; putting it below the *stream* was
    #: the original design and would have paid for the whole matrix traversal before refusing, which
    #: is what R39 is about. It sits with the other header-derived guards, above every allocation
    #: sized from the matrix.
    #: **`points_total` is bound here rather than after the stream**, so the density is formed from
    #: the same local the derivation divides by and the payload publishes. It used to be bound below
    #: `_in_sun_counts_by_column` with a comment recording that a mutation changing only the
    #: derivation's copy survived the whole suite; moving the binding *up* to the first reader keeps
    #: that property with one more reader in it, where re-reading `header.points` here would have
    #: created exactly the second copy that comment is about.
    points_total = header.points
    # Not applied under LM-83-22 (design section 2, row 10): its one rule has no area row.
    density = (
        None if area.points_expected is None or lm83_edition == LM83_22
        else lm83_grid_point_density(points_total, area.points_expected)
    )
    mixed_basis: Advisory | None = None
    if density is not None and density < LM83_GRID_POINT_DENSITY_MIN:
        if area.area_sf < LM83_BLIND_AREA_GENERAL_SF:
            return _refuse_mixed_basis(
                grid=grid, direct_id=direct_id, points_total=points_total,
                points_expected=area.points_expected, area=area, loaded=loaded, project=project,
                edition=lm83_edition,
            )
        # At and above 1 000 sf the criterion is a ratio and the count cancels; what does not cancel
        # is which row the area selected, and that error over-closes -- less daylight retained, sDA
        # **understated**. Recorded, not refused: withholding a correct-and-conservative schedule
        # for a mismatch that cannot flatter the answer is the wrong trade, and it is the same trade
        # `BLIND_SCHEDULE_AREA_ABOVE_CAP` makes one screen below.
        before, call = _direct_leg_rerun_call(
            loaded, project=project, grid=grid, edition=lm83_edition,
        )
        mixed_basis = Advisory(
            code="BLIND_TRIGGER_MIXED_BASIS",
            severity="warn",
            field="grid",
            message=(
                f"{_MIXED_BASIS_SENTENCE} {direct_id!r} declares {points_total} sensor point(s); "
                f"LM-83's own 0.6 m placement over the recorded polygon ({area.area_m2:.1f} m2, "
                f"{area.area_sf:.0f} sf, from the {area.area_source}) puts {area.points_expected} "
                f"inside it -- a density of {density:.0%}, below "
                f"{LM83_GRID_POINT_DENSITY_MIN:.0%}. **The schedule below is still published, and "
                "the direction is why.** This area is at or above "
                f"{LM83_BLIND_AREA_GENERAL_SF:.0f} sf, where section 2.2.6's criterion is "
                "'more than 2 % of points' -- a ratio, in which the point count cancels. What does "
                "not cancel is which row the area selected: the row comes from the whole polygon "
                "while the points come from a grid measuring less of it, so this trigger fires "
                "sooner than one keyed on the region the grid actually samples. That OVER-closes "
                "-- blinds shut in hours the standard leaves them open, less daylight is retained, "
                "and any sDA computed against this schedule is UNDERSTATED, never overstated. Below "
                f"{LM83_BLIND_AREA_GENERAL_SF:.0f} sf the same mismatch runs the other way and is "
                "refused instead. To remove the mismatch rather than read past it, place the grid "
                "with LM-83's own rule -- "
                f"{_lm83_grid_recreate_call(loaded, project=project, grid=grid)} -- and re-run "
                f"the direct leg over it with {call}."
                + _lm83_grid_recreate_caveat(loaded, grid=grid)
                + (f" {before}send that run_annual call." if before else "")
            ),
        )

    counts = _in_sun_counts_by_column(
        path,
        direct_id=direct_id,
        expected_points=artifact.parameters.get("n_points"),
        window=window,
        threshold_lux=LM83_ASE_THRESHOLD_LUX,
        content_digest=artifact.content_digest,
    )
    #: **One binding, now three uses.** `points_total` is the denominator the 2 % rule divides by,
    #: the numerator of the density gate above, *and* the number published on the trigger, and they
    #: have to be the same number: a mutation that changed only the derivation's copy survived the
    #: whole suite, because this task's fixture sits in the `200-499 sf` row where the rule is a
    #: point *count* and the denominator does not enter the arithmetic at all. A single local is what
    #: makes them inseparable; it is bound above the density gate, which is its first reader, and the
    #: payload additionally cross-checks it against `shape.points`, which is the same header field.
    schedule: BlindSchedule = derive_schedule(
        counts,
        points_total=points_total,
        area_sf=area.area_sf,
        window=window,
        threshold_lux=LM83_ASE_THRESHOLD_LUX,
        edition=lm83_edition,
    )
    applied = (
        f"LM-83 section 2.2.6's {schedule.band} row applied over "
        if lm83_edition != LM83_22
        else f"{section('blind_trigger', lm83_edition)}'s one rule ({schedule.band}: more than "
        "2 % of the points in direct sun, at every area) applied over "
    )
    counted_on = (
        "on the row's own point count" if lm83_edition != LM83_22
        else f"on the matrix's own {points_total} points"
    )
    # Final review, W-M2: with a group marked automated the edition's rule treats its sunlight
    # apart (section 4.6 excludes it; under LM-83-12 this server counts it only in its open hours),
    # and this series, from `illdir:<grid>` with every aperture open, does not, so it says so.
    marked = automated_group_names(loaded)
    automated_clause = "" if not marked else (
        f" Window group(s) {', '.join(marked)} are marked automated, and this series counts their "
        "sunlight in every hour, with every aperture open, where "
        + (
            f"{section('blind_trigger', LM83_22)} excludes it from the 2 % trigger"
            if lm83_edition == LM83_22 else
            "this server counts it only in the hours their own bound schedule leaves them open, "
            "and in no hour for a group held closed with no schedule bound (LM-83-12 gives no "
            "exclusion; section 2.2.6 allows only the manufacturer's control protocol)"
        )
        + ", so it is an upper bound on the closure that rule makes."
    )

    advisories: list[Advisory] = [
        Advisory(
            code="BLIND_SCHEDULE_DERIVED",
            severity="info",
            field="grid",
            # **The parenthetical was reworded at Phase 7d-1 Task 15a fix round 1.** It read
            # *"nothing in this phase computes a number against it (sDA is not blind-aware until
            # the blend lands in Phase 7d, ruling R107)"*, and Task 15a itself made the second half
            # false: `results(blind_control='lm83')` computes a blind-aware sDA from
            # `illblend:<grid>`. This is the same datedness R178 retires one code over, and this
            # module's own comment cites the governing rule -- an advisory whose stated reason has
            # stopped being true is a defect in its own right (docs/tool-surface.md section 9) --
            # to justify rewording `SDA_WITHOUT_BLIND_SCHEDULE`, so not applying it here was a
            # sweep that stopped one emitter short.
            #
            # **What survives is the first clause, because it is still true and it is the useful
            # half**: *this* schedule -- the whole-grid series derived here, at read time, from
            # `illdir:<grid>` -- is consumed by nothing. The blend reads the **sealed**
            # `sched:<grid>`, a different artefact over per-group matrices (R164 says the two are
            # not comparable in either direction), so a blind-aware sDA existing does not make this
            # series an input to anything. The sentence now says which schedule it is talking about
            # instead of naming a phase.
            message=(
                f"this schedule was derived at read time from {direct_id!r} -- the pre-blind, "
                "zero-bounce direct-sun run -- and is sealed nowhere: it is a pure function of that "
                + (
                    "matrix, the recorded polygon's area and the threshold"
                    if lm83_edition != LM83_22
                    else "matrix and the threshold, since LM-83-22's rule is the same at every area"
                )
                + ", and nothing consumes THIS "
                "series -- the blend that produces a blind-aware sDA reads the sealed 'sched:"
                f"{grid}' instead, over per-group matrices, and this whole-grid series is not an "
                f"input to it or to anything else. {applied}"
                f"{area.area_m2:.1f} m2 ({area.area_sf:.0f} sf, from the {area.area_source}), "
                f"closing the blinds for {schedule.hours_closed} of {len(schedule.columns)} window "
                f"hours at {schedule.threshold_lux:.0f} lux or more {counted_on}."
                f"{automated_clause}"
            ),
        ),
    ]
    #: **The one place `groups` is decided**, and the advisory that names its absence is written
    #: against the same `try`, so a state in which `groups` is null and nothing says why is not
    #: expressible here.
    sealed: _SealedGroupSchedule | None
    try:
        sealed = _sealed_group_schedule(
            runtime, loaded, grid=grid, schedule=schedule, edition=lm83_edition, project=project,
        )
    except _SealedScheduleUnusable as unusable:
        sealed = None
        advisories.append(_not_per_group_lm83_22(
            unusable, direct_id=direct_id,
        ) if lm83_edition == LM83_22 else Advisory(
            code="BLIND_SCHEDULE_NOT_PER_GROUP",
            severity="warn",
            field="grid",
            message=(
                f"this schedule is not resolved per window group, and `groups` is null because of "
                f"it: {unusable.reason}. The states above come from {direct_id!r}, which is one "
                "matrix for the whole grid, so three clauses of LM-83 section 2.2.6 go "
                "unimplemented on this call: blinds are operated *per window group*; groups may "
                "close *'in any combination until the criterion is met'*; and the DISCUSSION "
                "advises a closing *order*, closing the 'view' window groups first and the "
                "'clerestory' groups last. **The error this would cause has a known direction.** "
                "The whole-grid in-sun count is greater than or equal to any single group's, and "
                "where the criterion fires this trigger would close every group where the standard "
                "closes the minimum combination and stops -- so these states are a strict UPPER "
                "BOUND on closure. Applied per group they over-close: windows the standard leaves "
                "open are shut, daylight is lost, and the resulting sDA is understated, so a "
                "passing space can be reported as failing. Use these states for the whole grid, "
                "and do not distribute them over window groups. "
                + (unusable.repair or _RESEAL_SCHEDULE_REPAIR)
            ),
        ))
    else:
        advisories.append(Advisory(
            code="BLIND_SCHEDULE_ORDERED_PREFIX",
            severity="info",
            field="grid",
            message=_ordered_prefix_message(sealed, edition=lm83_edition, direct_id=direct_id),
        ))
        declared_now = (
            _declared_order_now(loaded, sealed.order, automated=automated_group_names(loaded))
            if sealed.order_substituted else None
        )
        # Final fix A, fix round 2, G2 had either message quote the refusal a re-run would meet on
        # a grid run_annual refuses. Final fix E, fix round 2 (review O2) removes it: this action
        # refuses every such grid itself first (`_refuse_grid_off_recorded_floor`, above; a
        # symlinked `.pts` too since round 3), and
        # derives no area without a registered grid, so the quote could no longer be reached.
        edition_clause = "" if lm83_edition != LM83_22 else f" with lm83_edition={lm83_edition!r}"
        if sealed.order_substituted and declared_now == tuple(sealed.order):
            # Final fix A, fix round 1, N2: the roles are declared now and rank the groups in the
            # order the run substituted, so the canonical sentence's "not every window group
            # declared" and its remedy "declare each" are no longer true of the project.
            order_text = ", ".join(sealed.order)
            advisories.append(Advisory(
                code="BLIND_ORDER_SUBSTITUTED",
                severity="warn",
                field="grid",
                message=(
                    "Not every window group declared a closing role when "
                    f"{blind_schedule_id(grid)!r} was sealed, so its closing order "
                    f"({order_text}) was substituted by sill height, lowest aperture first. Every "
                    "window group declares one now, and the order those roles give is the same "
                    f"({order_text}), so these states are the ones that declared order decides. "
                    + f"Re-running run_annual for this grid{edition_clause} seals the same "
                    "order as declared."
                    + " `order_substituted` on the payload records what the run sealed."
                ),
            ))
        elif sealed.order_substituted:
            advisories.append(Advisory(
                code="BLIND_ORDER_SUBSTITUTED",
                severity="warn",
                field="grid",
                message=(
                    f"{_order_substituted_sentence(lm83_edition)} "
                    f"{_declare_roles_remedy(loaded, project=project, order=sealed.order)} -- "
                    + f"and re-run run_annual for this grid{edition_clause} to replace the "
                    "substitution with a declared order."
                    + " `order_substituted` on the payload carries the same fact as a field."
                ),
            ))
    # LM-83-12 only (design section 2, row 11): LM-83-22 section 4.1.1 states no cap.
    if lm83_edition != LM83_22 and area.area_sf > LM83_BLIND_ANALYSIS_AREA_MAX_SF:
        # **LM-83 ch04:105 is an upper bound on the region section 2.2.6's table is defined over**,
        # and this branch is the whole-branch fix wave reversing an earlier "downgraded on
        # measurement" call -- because the code changed, not because the reading did. While nothing
        # consumed an analysis area, docs/tool-surface.md filing the cap among rules "the server
        # states rather than enforces" was honest. `area.area_sf` now *selects the band* two lines
        # above, so an over-cap area takes the `>=1000sf` row and `BLIND_SCHEDULE_DERIVED` asserts
        # applicability, in prose, for a region the standard defines no schedule over.
        #
        # `warn` and not a refusal: the arithmetic is correct for the region it describes, and what
        # is missing is the caller's split by facade orientation -- which cannot be derived from
        # one grid, exactly as the three clauses `BLIND_SCHEDULE_NOT_PER_GROUP` names cannot be.
        # Withholding a correct number for a repair only the caller can make would be the wrong
        # trade.
        advisories.append(Advisory(
            code="BLIND_SCHEDULE_AREA_ABOVE_CAP",
            severity="warn",
            field="grid",
            message=(
                f"this analysis area is {area.area_sf:.0f} sf, above the "
                f"{LM83_BLIND_ANALYSIS_AREA_MAX_SF:.0f} sf ceiling LM-83 section 2.2.6 puts on a "
                "blind-scheduling analysis area (ch04:105: analysis areas are grouped by facade "
                "orientation and never exceed 10 000 sf). The section 2.2.6 row selected above "
                f"({schedule.band}) is chosen *from this area*, so the schedule is being derived "
                "under a rule the standard does not define over a region this size, and the "
                "trigger's own point-count criterion was calibrated on spaces averaging ~1 200 sf "
                "(section 3.2.4). Split the grid by facade orientation into areas at or below the "
                "ceiling and schedule each separately. The states below are still the correct "
                "arithmetic for the whole region; what is missing is a split this server cannot "
                "make from one grid -- the same shape of limit as the per-group split, which "
                "this server can now publish only where a run sealed one."
            ),
        ))
    if mixed_basis is not None:
        # Appended rather than built here, so the `warn` and the `error` are two severities of one
        # decision taken at one place -- the `if` above -- rather than two conditions that have to
        # be kept agreeing. It is added even though the schedule is published: the direction is
        # conservative, not absent.
        advisories.append(mixed_basis)
    # Phase 25 Task 4b: a per-group matrix this schedule's `groups` read may be sealed at zero on
    # the probe's say-so (ruling R858); say so where the schedule is published. `sched:<grid>` is
    # walked only when its states are published (`groups` is null otherwise).
    published = [direct_id, *([blind_schedule_id(grid)] if sealed is not None else [])]
    advisories.extend(_sealed_ase_matrix_advisories(loaded, published))
    # W7 (final review): the schedule is an LM-83 result, so a chain built below the sky floor is
    # stated here as on `compliance`; silent at 4 and above, and when nothing records one.
    subdivision = _recorded_sky_subdivision(loaded, published)
    if subdivision is not None and subdivision < LM83_MIN_SKY_SUBDIVISION:
        advisories.append(_sky_subdivision_below_floor_advisory(
            subdivision, edition=lm83_edition, schedule=True,
        ))
    advisories.extend(area.advisories)
    if placed.advisory is not None:
        advisories.append(placed.advisory)
    # Final fix F, fix round 2 (G1): the direct-sun result was traced over a scene without an
    # include the run's oconv dropped, so the schedule derived from it says so, as every read does.
    dropped = _dropped_includes_advisory(loaded, direct_id, project=project, grid=grid)
    if dropped is not None:
        advisories.append(dropped)

    return emit(ResultsBlindSchedule(
        project=project,
        grid=grid,
        matrix=direct_id,
        path=str(path),
        shape=ShapeView(
            points=header.points, hours=header.hours, triples=header.points * header.hours
        ),
        states=list(schedule.states),
        columns=list(schedule.columns),
        clock_zone=placed.clock_zone,
        reference_year=placed.reference_year,
        groups=None if sealed is None else sealed.states,
        order=None if sealed is None else sealed.order,
        automated=None if sealed is None else sealed.automated,
        order_substituted=None if sealed is None else sealed.order_substituted,
        hours_closed=schedule.hours_closed,
        trigger=BlindTriggerView(
            threshold_lux=schedule.threshold_lux,
            threshold_provenance="sourced",
            comparison=">=",
            points_total=points_total,
            area_m2=area.area_m2,
            area_sf=area.area_sf,
            area_band_sf=schedule.band,
            area_source=area.area_source,
            coverage=area.coverage,
            points_expected=area.points_expected,
        ),
        advisories=advisories,
    ))


# --- the tool ------------------------------------------------------------------------------------


# --------------------------------------------------------------------------------------------
# `action="metrics"` -- the arithmetic's plumbing (Phase 7c Task 18)
# --------------------------------------------------------------------------------------------


#: **`metrics` means two different things depending on `action`, and this is that resolved rather
#: than left.** `_LM83_METRICS` is `{"sda", "ase"}` -- the pair `compliance(standard="lm83")` must
#: report together or not at all. This action's set is `{"udi", "da"}` and the two sets are
#: **disjoint by construction**: `results(action="metrics", metrics=["sda"])` is refused naming this
#: set, and `results(action="compliance", metrics=["udi"])` is refused naming LM-83's, so the
#: boundary is enforced from both sides rather than only from the side that happens to work.
#:
#: The argument keeps its name by owner decision (Owner-6): validating per action is what resolves
#: the collision, and every alternative introduces a second vocabulary for one idea.
_ANNUAL_METRICS: Final = frozenset({"udi", "da"})

#: **R106's three bins, and the lowest one's lower edge is `-inf` rather than `0`.**
#: `PointTally.observe` counts a column when `lower <= value < upper`, so a lowest bin written
#: `(0.0, UDI_BAND_LOWER_LUX)` would count no negative cell at all -- and the five-phase
#: combination subtracts (`Vd*T*Dd*Sds` can exceed `V*T*D*S`; `NEGATIVE_ILLUMINANCE_PRESENT` is the
#: allocated code for it; 2 336-3 113 cells in the runs recorded so far, draws not bounds, R814). The three percentages would
#: then silently stop summing to 100 on exactly the matrices this action is most likely to read.
#: `-inf` is not a widened threshold: a cell below zero lux **is** below 100 lux, which is what the
#: bin's own name says. The upper bin's `inf` is the mirror of the same argument.
_UDI_BINS: Final[tuple[tuple[float, float], ...]] = (
    (float("-inf"), UDI_BAND_LOWER_LUX),
    (UDI_BAND_LOWER_LUX, UDI_BAND_UPPER_LUX),
    (UDI_BAND_UPPER_LUX, float("inf")),
)

_UDI_PERIOD = (
    f"assumed occupied hours -- {OCCUPIED_START_HOUR:02d}:00-{OCCUPIED_END_HOUR:02d}:59 on "
    f"weekdays of a synthetic {OCCUPIED_REFERENCE_YEAR} reference year"
)

#: Attached to every result carrying a `udi` block. **Two codes, because they are two different
#: caveats about two different numbers**, and collapsing them would leave a client unable to tell
#: "the ceiling is contested" from "the period is invented".
_UDI_PERIOD_ASSUMED_ADVISORY: Final = Advisory(
    code="UDI_PERIOD_ASSUMED",
    severity="info",
    field="metrics",
    message=(
        f"UDI is reported over {_UDI_PERIOD}, and **no source in this corpus states an analysis "
        "period for UDI at all** (ruling R111). The one UDI computation this repo holds masks "
        "08:00-17:59 Monday to Friday against a synthetic 2023 calendar it builds itself, and its "
        "own comment calls that schedule illustrative. The weekdays therefore come from that "
        "reference year and not from the weather file -- a TMY year is spliced from a different "
        "real year per month, so its weekday sequence jumps at every month boundary. "
        "analysis.provenance is 'default' to say this is a substitution and not a measurement. "
        "The hour count itself is derived from this matrix's own header, never assumed."
    ),
)

#: R106, and the owner's own wording: the 2 000 lux ceiling is contested research, not a threshold.
#: Modelled on `ASE_BAND_IS_RESEARCH`, which carries the same posture at the same severity for
#: LM-83's own non-normative bands.
_UDI_CEILING_CONTESTED_ADVISORY: Final = Advisory(
    code="UDI_UPPER_THRESHOLD_CONTESTED",
    severity="info",
    field="metrics",
    message=(
        f"UDI's upper band edge is {UDI_BAND_UPPER_LUX:.0f} lux, and **there is little research to "
        "support the selection of 2 000 lux as an absolute upper threshold** (ruling R106). The "
        f"lower edge ({UDI_BAND_LOWER_LUX:.0f} lux) is inclusive and the upper edge is exclusive, "
        "so exactly 100 lux is useful daylight and exactly 2 000 lux is exceeded. Every percentage "
        "in the udi block is a measured proportion of the analysis period against that band -- it "
        "is not a compliance band, there is no pass or fail attached to it, and no standard in "
        "this corpus judges it."
    ),
)

#: **The third caveat riding the UDI payload, and the one that had no wire label.** Two
#: carried an advisory (`UDI_PERIOD_ASSUMED`, `UDI_UPPER_THRESHOLD_CONTESTED`); this one carried
#: nothing, in a field whose *twin one action over is sourced from a document held here*.
#: **It is not a substitution** (ruling R255-16, owner, 2026-09-01): the fraction is sourced by the
#: owner and the publication is simply not held in this clone, cited by reference the way R106
#: rules this project cites Nabil & Mardaljevic and the edition the owner established supersedes
#: LM-83-12 (ANSI/IES LM-83-22, R862). The message below says
#: sourced-but-unheld, which is what the Unsourced-values table row in `docs/tool-surface.md`
#: has always said; the wire used to say "substitution" and that was the contradiction.
#:
#: That asymmetry is the defect: a client reading `udi.spatial.hours_required: 1300.0` beside
#: `lm83.analysis.hours_required: 1825.0` sees two identically named, identically shaped fields and
#: has nothing on the wire to tell it that one is 50 % of LM-83's own fixed 3 650 h window
#: (ch04:38-40) and the other is 50 % of an assumed period against a fraction no document held
#: here states -- because UDI is not an LM-83 metric and the 13-document standards corpus this
#: clone does hold carries no UDI threshold of any kind.
#:
#: **Labelled and kept, not dropped.** `UDI_SPATIAL_TIME_FRACTION` is load-bearing by this repo's
#: own record: without it `spatial_udi` stops being an sDA-shaped figure and becomes the mean of
#: the per-point percentages, which is a different and wrong number (`spatial_udi`'s own
#: docstring). `info`, matching the two advisories already on this block.
_UDI_SPATIAL_THRESHOLD_ADVISORY: Final = Advisory(
    code="UDI_SPATIAL_THRESHOLD_ASSUMED",
    severity="info",
    field="metrics",
    message=(
        f"udi.spatial.hours_required is {UDI_SPATIAL_TIME_FRACTION:.0%} of the analysis period, "
        "and **that fraction is sourced by the owner, from a publication this repo does not "
        "hold** -- cited by reference the way R106's own papers are, and not a substitution. UDI "
        "is not an LM-83 metric and the standards corpus holds no UDI "
        "threshold of any kind; the band itself is sourced (Nabil & Mardaljevic 2005, 100-2 000 "
        "lux) and this second threshold is sourced the same way, by reference. **It is stated "
        "because the field beside it can be checked against a document held here and this one "
        "cannot.** results(action='compliance', standard='lm83') publishes an identically named and "
        "identically shaped analysis.hours_required of 1 825 h -- sDA_300/50%'s -- which IS "
        "sourced: 50 % of LM-83's own fixed 3 650 h window (ch04:38-40). Two fields of the same "
        "name and shape, one traceable to a document this clone holds and one to a publication it "
        "does not, and without this label the symmetry implies a provenance this one cannot "
        "demonstrate here. The threshold is kept rather than dropped: "
        "removing it turns spatial UDI from a percent-of-area figure into the mean of the "
        "per-point percentages, which is a different quantity under the same name."
    ),
)


def _udi_spatial_threshold_advisory(edition: Lm83Edition) -> Advisory:
    """`UDI_SPATIAL_THRESHOLD_ASSUMED` under `edition` (R864, Task 5 fix round 1).

    Its sentence about compliance's `analysis.hours_required` names sDA's label and the clause of
    the LM-83 window, so under LM-83-22 -- reachable on a `metrics` call that reports both blocks
    with `lm83_edition="LM-83-22"` -- they are LM-83-22's, with no `ch04` line citation, and the
    "document held here" is ANSI/IES LM-83-22. Under LM-83-12 it is
    `_UDI_SPATIAL_THRESHOLD_ADVISORY`, byte for byte.
    """
    if edition != LM83_22:
        return _UDI_SPATIAL_THRESHOLD_ADVISORY
    return Advisory(
        code="UDI_SPATIAL_THRESHOLD_ASSUMED",
        severity="info",
        field="metrics",
        message=(
            f"udi.spatial.hours_required is {UDI_SPATIAL_TIME_FRACTION:.0%} of the analysis "
            "period, and **that fraction is sourced by the owner, from a publication this repo "
            "does not hold** -- cited by reference the way R106's own papers are, and not a "
            "substitution. UDI is not an LM-83 metric and the standards corpus holds no UDI "
            "threshold of any kind; the band itself is sourced (Nabil & Mardaljevic 2005, 100-2 "
            "000 lux) and this second threshold is sourced the same way, by reference. **It is "
            "stated because the field beside it can be checked against a document and this one "
            "cannot.** results(action='compliance', standard='lm83', lm83_edition='LM-83-22') "
            "publishes an identically named and identically shaped analysis.hours_required of "
            f"1 825 h -- {sda_label(edition)}'s -- which IS sourced: 50 % of LM-83-22's own fixed "
            f"3 650 h window ({section('period', edition)}, in ANSI/IES LM-83-22"
            "). Two fields of the same name and shape, one traceable to that document and "
            "one to a publication this repo does not hold, and without this label the symmetry "
            "implies a provenance this one cannot demonstrate here. The threshold is kept rather "
            "than dropped: removing it turns spatial UDI from a percent-of-area figure into the "
            "mean of the per-point percentages, which is a different quantity under the same name."
        ),
    )


def _metrics_sda_reported_alone_advisory(edition: Lm83Edition) -> Advisory:
    """`SDA_REPORTED_ALONE` on every result carrying a `da` block.

    The same rule `_SUMMARY_SDA_REPORTED_ALONE_ADVISORY` states for `action="summary"` at the same
    severity: the spatial aggregate of DA_300 over 50 % of the window **is** sDA, and LM-83 asks for
    sDA and ASE to be reported together (`section("reporting", edition)`: LM-83-12 section 2.4,
    LM-83-22 section 4.14). So `DaView` publishes no spatial figure and this says why, rather than
    leaving the absence to be inferred. `severity="error"` describes the *value* ("the pair is not
    here"), never the call -- the result still succeeds. R864: the clause and the sDA label are
    `edition`'s; the LM-83-12 message said "LM-83 ch04 section 2.4".
    """
    return Advisory(
        code="SDA_REPORTED_ALONE",
        severity="error",
        field="metrics",
        message=(
            "results(action='metrics') publishes DA_300 per point and **no spatial aggregate**. "
            f"The spatial aggregate of DA_300 over 50 % of LM-83's window is {sda_label(edition)}, "
            f"and {section('reporting', edition)} requires sDA and ASE to be reported together or "
            "not at all -- so publishing it here, where ASE is not computed, would itself be sDA "
            "reported alone. results(action='compliance', standard='lm83') is where the pair is "
            "reported, together, with its bands and the conditions LM-83 requires them under."
        ),
    )


def _metrics_lm83_edition_advisory(edition: Lm83Edition) -> Advisory:
    """`LM83_EDITION` on every result carrying a `da` block.

    **Its own sentence, not `_compliance_lm83`'s**: that one says "the thresholds, the 08:00-18:00
    period and the bands applied here ... the one `lm83.edition` reports on this result", and this
    payload has no bands and no `lm83.edition` field. A code whose message is untrue of its
    condition is a defect in its own right (docs/tool-surface.md section 9), so the code is shared
    and the sentence is not.

    R864: the edition is the call's `lm83_edition`. Under the default the message no longer says
    LM-83-22 "is not a selectable edition here": it says how to select it. Under LM-83-22 it names
    the edition's source document and the LM-83-22 rules not applied (Still open items
    48 and 51: the 150 lux threshold, and the dirt and mullion factors).
    """
    if edition == LM83_22:
        lead = (
            f"DA_{LM83_SDA_THRESHOLD_LUX:.0f}'s threshold and its 08:00-18:00, 10 h/day analysis "
            f"period ({section('period', edition)}) are {edition_title(edition)}'s "
            "(lm83_edition='LM-83-22'), cited from ANSI/IES LM-83-22, "
            "by section number. LM-83-22's 150 lux threshold for less critical visual tasks and "
            "its dirt depreciation and mullion factors are not applied (Still open items 48 and "
            "51) -- state the edition when reporting."
        )
    else:
        lead = (
            f"DA_{LM83_SDA_THRESHOLD_LUX:.0f}'s threshold and its 08:00-18:00, 10 h/day analysis "
            f"period are {edition_title(edition)}'s -- the default edition. ANSI/IES LM-83-22 "
            "supersedes it and can be chosen with lm83_edition='LM-83-22'; a report that does not "
            "state the edition reads as current (ch04: \"Flag the edition whenever reporting\")."
        )
    return Advisory(
        code="LM83_EDITION",
        severity="info",
        field="metrics",
        message=(
            f"{lead} **No band, level or verdict is attached to this number** -- the standard's "
            "own judged quantity is sDA, which this action does not report.\n"
            "UDI carries no edition line of its own because no standard in this corpus defines it."
        ),
    )


def _metrics_lm83_dirt_depreciation_advisory(edition: Lm83Edition) -> Advisory:
    """`LM83_DIRT_DEPRECIATION_NOT_APPLIED` (R862) on the `metrics` `da` block -- with
    `_compliance_lm83`, the two places `LM83_EDITION` rides in `results`, which are every LM-83
    judgement this tool makes. `_lm83_dirt_depreciation_message` holds both editions' wording; the
    factor itself is Still open item 51.
    """
    return Advisory(
        code="LM83_DIRT_DEPRECIATION_NOT_APPLIED",
        severity="info",
        field="metrics",
        message=_lm83_dirt_depreciation_message(edition),
    )


def _validate_annual_metrics(metrics: list[str] | None) -> CallToolResult | None:
    """Every `metrics` refusal for `action="metrics"`, or `None`.

    Pure argument validation -- no project is opened and no file is read to answer it -- so it runs
    before `_resolve`/`current()`, the same R39 placement `_validate_lm83_metrics` and
    `_validate_en17037_arguments` already take.

    **`None` means both**, matching `compliance(standard="lm83")`'s own posture for the same
    argument. An **empty list** is refused rather than read as "all": `metrics=[]` is a caller
    stating a selection, and answering it with everything would be answering a different question
    than the one asked.

    **There is no `SDA_REPORTED_ALONE` analogue here.** LM-83 binds sDA and ASE together; nothing
    binds UDI and DA, which come from two different periods and two different sources, so
    `metrics=["udi"]` alone is a complete and legitimate request.
    """
    if metrics is None:
        return None
    if not isinstance(metrics, list) or not metrics:
        return _refuse_argument(
            f"metrics must be a non-empty list drawn from {sorted(_ANNUAL_METRICS)}, not "
            f"{metrics!r}. Omit it entirely to report both.",
            field="metrics",
        )
    unknown = sorted({m for m in metrics if m not in _ANNUAL_METRICS})
    if unknown:
        return _refuse_argument(
            f"metrics names {unknown}, outside results(action='metrics')' own "
            f"{sorted(_ANNUAL_METRICS)}. `metrics` means a different set per action: "
            f"{sorted(_LM83_METRICS)} belong to action='compliance', standard='lm83', which "
            "reports them together with their judgement bands. This action reports measured "
            "values and no verdict.",
            field="metrics",
        )
    return None


def _refuse_empty_metrics_denominator(
    state: _Resolved, *, grid: str, metric: str, period: str
) -> CallToolResult:
    """A metric whose analysis window contains none of this matrix's columns: refused, never
    answered with a zero.

    **UDI's window can be empty where LM-83's is not, and the asymmetry is the weekday filter.**
    `occupied_hours(24)` is empty -- 2023-01-01 is a Sunday -- while `lm83_window(24)` has ten
    columns, so the same 24-hour matrix legitimately answers `da` and cannot answer `udi`. That is
    why this refusal names the metric and its period rather than the matrix.

    `useful_daylight_illuminance`/`daylight_autonomy` raise `ValueError` on this rather than
    returning `0.0`, for the reason their own docstrings give: a caller cannot tell *"in band for
    none of a real period"* from *"no period at all"*, and `0.0` is exactly as publishable and
    exactly as wrong as `100.0`. The check is made from the **header alone**, above every
    allocation sized from it (R39), so the streaming read never starts.

    Code-less, matching `_refuse_empty_lm83_denominator` and `_NoAnnualResult`: the register was
    searched and allocates nothing naming *"the analysis window is empty"*. Not
    `ANALYSIS_PERIOD_NOT_ANNUAL` -- that names a matrix shorter than a year, which is a **different**
    condition this action deliberately does not refuse (see `_metrics`).
    """
    reason = (
        f"the matrix has {state.header.hours} hour column(s), and {metric}'s analysis period "
        f"({period}) contains none of them -- there is no denominator and therefore no proportion "
        "to report. Nothing is published as zero, because a caller cannot tell 'none of a real "
        "period' from 'no period at all'."
    )
    message = f"{metric} for grid {grid!r} could not be computed: {reason}"
    logger.info("results(action='metrics') refused: %s", message)
    return refuse(message)


def _metrics(
    state: _Resolved, *, project: str, grid: str, metrics: list[str] | None,
    edition: Lm83Edition = DEFAULT_LM83_EDITION, named: bool = False,
) -> CallToolResult:
    """`action="metrics"`: UDI and DA per point, plus spatial UDI, judged against nothing.

    **Every number comes from `rad_core.metrics`.** This function resolves the artifact, builds the
    per-point `PointTally` objects the matrix streams into, and shapes the result -- no threshold,
    no band edge and no percentage is computed here. `useful_daylight_illuminance` answers each of
    the three UDI bins, `spatial_udi` the area fraction, `udi_hours_required` the enforced hour
    count published beside it, and `daylight_autonomy` each point's DA_300.

    **Two tally sets, one streaming pass.** The rows are read once and folded into both sets as
    they go, because the two metrics need two different windows -- `occupied_hours` for UDI,
    `lm83_window` for DA -- and `rad_core.metrics._ensure_window_matches` raises if a tally built
    against one is handed to a metric expecting the other. Building one shared set and reporting it
    under two denominators is the exact mistake that guard exists to catch, so it is not made
    convenient here: the cost of the second set is one more `observe` per cell, and what it buys is
    that the guard stays load-bearing.

    **A sub-annual matrix is NOT refused, and that is a measured departure from `compliance`.**
    `_refuse_sub_annual_matrix`'s own docstring states the rule this follows: *"`summary`,
    `points`, `timeseries` and `export` are deliberately untouched by this ... it is the band and
    the level statement that a sub-annual period makes false, so the refusal sits on `compliance`
    alone."* R104's arithmetic is about thresholds that **do not scale with the period** --
    ASE_1000,250h counts against an absolute 250 hours, so any window shorter than 251 reports 0 %
    and a guaranteed favourable band. Neither metric here has such a threshold: UDI's second
    threshold is a *fraction* of the window (`UDI_SPATIAL_TIME_FRACTION`) and DA's denominator is
    the window itself, so both scale exactly with whatever period the matrix carries, and both are
    published beside the `hours_total` they were divided by. What *is* refused is an **empty**
    window, which is a missing denominator rather than a short one.

    Order of refusals, all from the header and the manifest alone and all above every allocation
    sized from either (R39):

    1. no sensor points -> there is no analysis area to report a percentage of.
    2. an asked-for metric whose window is empty -> `_refuse_empty_metrics_denominator`, naming the
       metric and its period, because the two windows can disagree about the same matrix.

    **`edition` is the call's `lm83_edition` (R864)**, read only by the `da` block: its
    `LM83_EDITION`, dirt, `SDA_REPORTED_ALONE` and latitude advisories take their clauses and band
    from it, and a blend whose schedule was sealed for declarations the project no longer records,
    or under the other edition below 1 000 sf or at any area with a bound automated schedule
    (R870), is refused (`_refuse_edition_mismatch`), because DA_300 read off it was decided hour by
    hour by a closing rule the asked-for edition or the project does not have. A UDI-only call is
    never refused for it: `results` refuses `lm83_edition` on such a call before it gets here.
    """
    asked = set(metrics) if metrics else set(_ANNUAL_METRICS)
    want_udi = "udi" in asked
    want_da = "da" in asked
    if want_da:
        refusal = _refuse_edition_mismatch(
            state, project=project, grid=grid, edition=edition, named=named, reports="metrics",
        )
        if refusal is not None:
            return refusal
    # Final fix E1: after BLIND_SCHEDULE_NOT_CURRENT, the more specific, on a DA_300 read.
    refusal = _refuse_window_groups_not_current(
        state, project=project, grid=grid, reads="metrics", edition=edition,
    )
    if refusal is not None:
        return refusal

    if state.header.points == 0:
        reason = (
            f"the matrix carries {state.header.points} sensor points -- there is no analysis area "
            "to report a percentage of points over, so there is no denominator and therefore no "
            "proportion to report."
        )
        message = f"results(action='metrics') for grid {grid!r} could not be computed: {reason}"
        logger.info("results(action='metrics') refused: %s", message)
        return refuse(message)

    udi_window = occupied_hours(state.header.hours)
    # R860: DA's window is sDA's, on the zone's clock when one is recorded; UDI's occupied mask is
    # not LM-83's period and stays in standard time. Placed only when DA is asked for, so a UDI-only
    # call neither reads the EPW nor carries an LM-83 window advisory.
    placed = (
        _lm83_window_for(state.loaded, state.artifact_id, state.header.hours) if want_da else None
    )
    da_window = placed.columns if placed is not None else frozenset()
    if want_udi and not udi_window:
        return _refuse_empty_metrics_denominator(
            state, grid=grid, metric="UDI", period=_UDI_PERIOD
        )
    if want_da and not da_window:
        return _refuse_empty_metrics_denominator(
            state, grid=grid, metric="DA_300",
            period=_lm83_period(placed.clock_zone if placed is not None else None),
        )

    positions = _grid_positions(state, grid=grid)
    udi_tallies = (
        [PointTally(udi_window, bands=_UDI_BINS) for _ in range(state.header.points)]
        if want_udi
        else []
    )
    da_tallies = (
        [
            PointTally(da_window, thresholds=(LM83_SDA_THRESHOLD_LUX,))
            for _ in range(state.header.points)
        ]
        if want_da
        else []
    )
    negative_cells = _NegativeCount({
        name: window for name, window, wanted in (
            ("udi", udi_window, want_udi), ("da", da_window, want_da)
        ) if wanted
    })
    for index, lux in enumerate(_lux_rows(
        state.path, artifact_id=state.artifact_id, expected_points=state.expected_points,
        content_digest=state.content_digest,
    )):
        udi_tally = udi_tallies[index] if want_udi else None
        da_tally = da_tallies[index] if want_da else None
        for hour, value in enumerate(lux):
            if udi_tally is not None:
                udi_tally.observe(hour, value)
            if da_tally is not None:
                da_tally.observe(hour, value)
        negative_cells.observe_row(lux)

    def _xyz(index: int) -> list[float] | None:
        return positions[index] if positions is not None else None

    # **`state.blend_advisories` first, so a stale blend is labelled on every action that
    # reads one** (ruling R191). Empty for every unblended result and for a blend whose
    # schedule still matches, so this changes no payload that was not already a blend.
    advisories: list[Advisory] = _blend_advisories_under(state, edition)
    udi_view: UdiView | None = None
    if want_udi:
        below, in_band, above = _UDI_BINS
        udi_view = UdiView(
            analysis=MetricsAnalysisView(
                period=_UDI_PERIOD, hours_total=len(udi_window), provenance="default",
                points_total=len(udi_tallies),
            ),
            spatial=UdiSpatialView(
                area_fraction_in_band=spatial_udi(
                    udi_tallies, band=in_band, window=udi_window
                ),
                hours_required=udi_hours_required(udi_window),
            ),
            points=[
                UdiPointView(
                    id=index,
                    xyz=_xyz(index),
                    udi_below_100=useful_daylight_illuminance(
                        tally, band=below, window=udi_window
                    ),
                    udi_100_2000=useful_daylight_illuminance(
                        tally, band=in_band, window=udi_window
                    ),
                    udi_at_or_above_2000=useful_daylight_illuminance(
                        tally, band=above, window=udi_window
                    ),
                )
                for index, tally in enumerate(udi_tallies[:MAX_INLINE_VALUES])
            ],
        )
        advisories.append(_UDI_PERIOD_ASSUMED_ADVISORY)
        advisories.append(_UDI_CEILING_CONTESTED_ADVISORY)
        advisories.append(_udi_spatial_threshold_advisory(edition))

    da_view: DaView | None = None
    if want_da:
        da_view = DaView(
            analysis=MetricsAnalysisView(
                period=_lm83_period(placed.clock_zone if placed is not None else None),
                hours_total=len(da_window), provenance="sourced",
                points_total=len(da_tallies),
                clock_zone=placed.clock_zone if placed is not None else None,
                reference_year=placed.reference_year if placed is not None else None,
            ),
            points=[
                DaPointView(
                    id=index,
                    xyz=_xyz(index),
                    da_300=daylight_autonomy(
                        tally, threshold_lux=LM83_SDA_THRESHOLD_LUX, window=da_window
                    ),
                )
                for index, tally in enumerate(da_tallies[:MAX_INLINE_VALUES])
            ],
        )
        advisories.append(_metrics_sda_reported_alone_advisory(edition))
        advisories.append(_metrics_lm83_edition_advisory(edition))
        advisories.append(_metrics_lm83_dirt_depreciation_advisory(edition))
        # **The other half of the same obligation** (fix wave B4.5). Attaching `LM83_EDITION` is
        # this action accepting that DA_300's threshold and its 08:00-18:00 period are the
        # edition's and must be labelled -- and the edition's latitude band (LM-83-12's 37-48 N,
        # LM-83-22's 35 to 55 degrees, R864) is a property of the same standard's same research,
        # applied to the same
        # threshold over the same period. Stating one and withholding the other is not a narrower
        # claim, it is an inconsistent one, and `_site_latitude` takes the same `_Resolved`
        # `_compliance_lm83` already hands it.
        #
        # **On the `da` block only, never on `udi`.** `_metrics_lm83_edition_advisory`'s own
        # closing sentence is that UDI carries no edition line because no standard in this corpus
        # defines it; an LM-83 calibration caveat on a UDI-only payload would be an advisory untrue
        # of the number it travels on (docs/tool-surface.md section 9).
        latitude = _site_latitude(
            state, walked=(placed.epw, placed.epw_reason) if placed is not None else None
        )
        if latitude is not None and not lm83_latitude_in_calibration_band(
            latitude, edition=edition
        ):
            advisories.append(_latitude_outside_calibration_advisory(latitude, edition=edition))
        if placed is not None and placed.advisory is not None:
            advisories.append(placed.advisory)

    # The count is `negative_cells`', folded in with the tallies above: whole-year, with the
    # number inside each metric's window beside it. Everything else -- the percentage, the
    # mechanism ternary and the message prefix -- is `_negative_count_advisory`'s, shared with
    # every other reader rather than written out again. The tail below is the part that differs.
    #
    # **The tail names only the blocks this call actually published.** A sentence about
    # `udi_below_100` on a `metrics=["da"]` result would be an advisory whose message is untrue of
    # the payload it travels on, which docs/tool-surface.md section 9 makes a defect in its own
    # right.
    # **Each metric's figures hold only the negative cells inside its own window** (review of
    # R814): the count is whole-year, UDI tallies `occupied_hours` and DA `lm83_window`.
    where = " ".join(filter(None, (
        f"Inside UDI's {len(udi_window)}-hour occupied window: "
        f"{negative_cells.in_window['udi']} of them, counted in udi_below_100, the lowest bin."
        if want_udi else "",
        f"Inside DA's {len(da_window)}-hour 08:00-18:00 window: "
        f"{negative_cells.in_window['da']} of them, counted outside DA_300, which counts at or "
        "above 300 lux."
        if want_da else "",
    )))
    not_dropped = (
        " Inside UDI's window a negative cell is NOT dropped: UDI's lowest bin runs from minus "
        "infinity precisely so the three bins keep summing to 100 % of the analysis period on a "
        "matrix like this one."
        if want_udi
        else ""
    )
    negative = _negative_count_advisory(
        state, negative_cells,
        tail=f"{where} A negative cell outside a metric's window is in none of its figures."
        f"{not_dropped}",
    )
    if negative is not None:
        advisories.append(negative)

    published = len(udi_view.points) if udi_view is not None else len(da_view.points)  # type: ignore[union-attr]
    if published < state.header.points:
        blocks = " and ".join(
            f"`{name}.points`" for name, wanted in (("udi", want_udi), ("da", want_da)) if wanted
        )
        advisories.append(Advisory(
            code="RESULT_TRUNCATED",
            severity="warn",
            field="metrics",
            message=(
                f"{blocks} carr{'y' if len(asked) > 1 else 'ies'} {published} of "
                f"{state.header.points} sensor points; tool results have no pagination in MCP, so "
                f"each list is bounded at {MAX_INLINE_VALUES}. `n_points` reports the real count "
                f"({state.header.points}), and every aggregate here"
                f"{' -- the spatial UDI figure included --' if want_udi else ''} is computed over "
                "all of them, not over these lists. The complete "
                f"matrix is the committed .ill at `path` ({state.path}), also linked as a resource "
                "on this result; results(action='export', format='csv') writes every value as text."
            ),
        ))

    result = ResultsMetrics(
        project=project, grid=grid, artifact=state.artifact_id, path=str(state.path),
        method_used=state.method_used, shape=_shape_view(state.header),
        n_points=state.header.points, udi=udi_view, da=da_view,
        advisories=advisories or None,
    )
    return _emit_with_resource_link(
        result, path=state.path, project=project, grid=grid,
        description=(
            f"The annual illuminance matrix: {state.header.points} sensor rows x "
            f"{state.header.hours} hourly values, in the {grid!r} grid's own point order."
        ),
    )


def _refuse_unread_edition(
    lm83_edition: Lm83Edition | None, action: str, standard: str | None,
    metrics: list[str] | None,
) -> CallToolResult | None:
    """R864 (Review Focus 4): `lm83_edition` is read by `action="compliance"` with
    `standard="lm83"`, by `action="metrics"` when it reports `da` (named in `metrics`, or `metrics`
    omitted, which means both), and by `action="blind_schedule"`. Sent with any other call it would
    do nothing, and is refused before the project is opened rather than ignored -- `sensor_grid`'s
    and `run_annual`'s own `_refuse_unread_edition` take the same posture. Runs after every other
    pure argument check, so `standard` and `metrics` are already known to be valid.
    """
    if lm83_edition is None:
        return None
    if action == "compliance" and standard == "lm83":
        return None
    if action == "metrics" and (metrics is None or "da" in metrics):
        return None
    if action == "blind_schedule":
        return None
    asked = f"action={action!r}" + (
        f", standard={standard!r}" if action == "compliance"
        else f", metrics={metrics!r}" if action == "metrics" else ""
    )
    return _refuse_argument(
        "lm83_edition is read only by results(action='compliance', standard='lm83'), by "
        "action='metrics' when it reports 'da', and by action='blind_schedule'; this call "
        f"({asked}) reads no LM-83 edition, so it is refused rather than ignored. Leave it out.",
        field="lm83_edition",
    )


def results(
    action: Annotated[
        Action | None,
        Field(
            description=(
                "which read to make: \"summary\", \"points\", \"timeseries\", \"compliance\", "
                "\"blind_schedule\", \"metrics\" or \"export\". The tool description says what "
                "each one returns."
            )
        ),
    ] = None,
    project: Annotated[
        str | None,
        Field(
            description=(
                "the project handle."
            )
        ),
    ] = None,
    grid: Annotated[
        str | None,
        Field(
            description=(
                "the sensor grid whose annual result to read -- the same `grid` passed to "
                "`run_annual`. **A `grid` containing `\":\"` is refused** "
                "(`ARGUMENT_OUT_OF_RANGE`, ruling R186): an `lm83` run seals one operand per "
                "window group and blind state under `ill:<grid>:<group>:<state>`, so "
                "`grid=\"floor:north:open\"` used to resolve one facade's contribution in one "
                "blind state and publish it as the whole grid's sDA. The refusal names the real "
                "grid -- the part before the first colon."
            )
        ),
    ] = None,
    chain: Annotated[
        Chain | None,
        Field(
            description=(
                "\"2phase\" | \"3phase\" | \"5phase\" -- **which chain's annual result this call "
                "reads**. Optional when the grid has only one result registered. **Required when "
                "the grid has both** `ill:<grid>` and `ill5:<grid>` (the five-phase chain's): such "
                "a call is refused with `ANNUAL_RESULT_AMBIGUOUS` naming both ids rather than "
                "answered from whichever the older chain wrote. **`action=\"compliance\"` does "
                "not need it**: it reports the results side by side in `by_chain`, keyed by "
                "method and blind treatment. **Silently unused** by "
                "`action=\"blind_schedule\"`, which reads `illdir:<grid>`."
            )
        ),
    ] = None,
    blind_control: Annotated[
        BlindControl | None,
        Field(
            description=(
                "\"lm83\" | \"none\" | \"fixed\" -- **which blind treatment this call reads**, and "
                "it is ORTHOGONAL to `chain` (ruling R188). `\"lm83\"` reads the schedule-weighted "
                "blend `illblend:<grid>`. **`\"none\"` and `\"fixed\"` both read the unblended "
                "candidates, and they are the same answer** (ruling R197). Optional when the grid "
                "has only one annual result registered. **On a grid holding both a "
                "blended and an unblended result, either argument serves**: `chain=\"3phase\"` "
                "alone reads `ill:<grid>` past the blend. Only a call giving **neither** is "
                "refused, with `ANNUAL_RESULT_AMBIGUOUS`."
            )
        ),
    ] = None,
    job_id: Annotated[
        str | None,
        Field(
            description=(
                "withdrawn -- refused with `ARGUMENT_NOT_IMPLEMENTED` if supplied."
            )
        ),
    ] = None,
    result: Annotated[
        str | None,
        Field(
            description=(
                "withdrawn -- refused with `ARGUMENT_NOT_IMPLEMENTED` if supplied."
            )
        ),
    ] = None,
    standard: Annotated[
        Literal["lm83", "en17037", "en12464"] | None,
        Field(
            description=(
                "\"lm83\" | \"en17037\" | \"en12464\", required by `action=\"compliance\"`. "
                "\"lm83\" and \"en17037\" (`method=\"2\"`, `axis=\"provision\"`) are built; "
                "\"en12464\" and \"en17037\" (`method=\"1\"` or `axis=\"glare\"`) refuse "
                "naming what they are missing."
            )
        ),
    ] = None,
    level: Annotated[
        Literal["minimum", "medium", "high"] | None,
        Field(
            description=(
                "\"minimum\" | \"medium\" | \"high\", required by `action=\"compliance\"` with "
                "`standard=\"en17037\"`."
            )
        ),
    ] = None,
    city: Annotated[
        str | None,
        Field(
            description=(
                "an EN 17037 Method 1 city. Accepted, not consumed -- Method 1 refuses regardless."
            )
        ),
    ] = None,
    maintenance_factor: Annotated[
        StrictFloat | None,
        Field(
            description=(
                "read by no action: `action=\"compliance\"` with `standard=\"en12464\"` is "
                "refused, naming `run_electric`, which takes its own `maintenance_factor`. "
                "`city` and `maintenance_factor` are accepted here for `action=\"compliance\"` "
                "with `standard=\"en12464\"` or `en17037` Method 1, both refused, but consumed by "
                "nothing -- those paths refuse regardless of what these carry."
            )
        ),
    ] = None,
    usage_hours: Annotated[
        dict[str, Any] | None,
        Field(
            description=(
                "required by `standard=\"en17037\"`, `axis=\"glare\"` -- no default (ch03 "
                "5.4/E.3.1)."
            )
        ),
    ] = None,
    method: Annotated[
        Literal["1", "2"] | None,
        Field(
            description=(
                "\"1\" | \"2\", the EN 17037 daylight-provision method. Only \"2\" is built; \"1\" "
                "refuses naming Method 2."
            )
        ),
    ] = None,
    axis: Annotated[
        Literal["provision", "glare"] | None,
        Field(
            description=(
                "\"provision\" (the default) | \"glare\", which of EN 17037's four independent "
                "criteria (ch03:9-11) this call asks about. Only \"provision\" is computed; "
                "\"glare\" refuses, validating `usage_hours` on the way."
            )
        ),
    ] = None,
    metrics: Annotated[
        list[str] | None,
        Field(
            description=(
                "**means a different set depending on `action`, and is validated per action.** For "
                "`action=\"compliance\"`, `standard=\"lm83\"`: which of LM-83's metrics to report "
                "-- `[\"sda\", \"ase\"]` (the default, when omitted) or a legal subset. LM-83 ch04 "
                "section 2.4 requires sDA and ASE together, so a subset omitting either half "
                "refuses with `SDA_REPORTED_ALONE` rather than answering partially. For "
                "`action=\"metrics\"`: `[\"udi\", \"da\"]` (again the default when omitted), and "
                "nothing binds the two together -- `[\"udi\"]` alone is a complete request. Each "
                "action refuses the other's names."
            )
        ),
    ] = None,
    point_ids: Annotated[
        list[StrictInt] | None,
        Field(
            description=(
                "0-based sensor indices, in the grid's own point order. Required by \"timeseries\" "
                "(with `hours`, or instead of it): a full annual matrix is far past what a tool "
                "result can carry inline, so an unselected request is refused rather than "
                "truncated."
            )
        ),
    ] = None,
    hours: Annotated[
        list[StrictInt] | None,
        Field(
            description=(
                "0-based hour indices of the year. Same role as `point_ids`."
            )
        ),
    ] = None,
    threshold_lux: Annotated[
        Annotated[float, Field(strict=True)] | None,
        Field(
            description=(
                "an illuminance threshold in lux, consumed only by `action=\"points\"`. Each "
                "point's `hours_above_threshold` counts its hours strictly *above* this threshold, "
                "over the whole year. **This is not sDA's own count**: at `threshold_lux=300`, "
                "sDA's per-point hour tally is `>= 300` (inclusive) while this field is `> 300` "
                "(strict), so the two differ at every hour of exactly 300 lux and neither "
                "substitutes for the other. Omitted, the field is **absent**, never a substituted "
                "default. **Silently unused** -- not refused -- by \"summary\", \"timeseries\", \"export\", \"compliance\", "
                "\"metrics\" and \"blind_schedule\"."
            )
        ),
    ] = None,
    format: Annotated[
        ExportFormat,
        Field(
            description=(
                "\"csv\" for `action=\"export\"`. \"json\" and \"parquet\" are refused."
            )
        ),
    ] = "csv",
    lm83_edition: Annotated[
        Lm83Edition | None,
        Field(
            description=(
                "the LM-83 edition to judge against, \"LM-83-12\" (default) or \"LM-83-22\": "
                "its ASE label (LM-83-22: `level_of_concern` at 20 % or more, not yet checked against "
                "the printed standard), latitude band and "
                "cited clauses. Read by `action=\"compliance\"` with `standard=\"lm83\"`, by "
                "`action=\"metrics\"` when it reports `da`, and by `action=\"blind_schedule\"`; "
                "refused anywhere else. A blend or schedule sealed under the other edition is "
                "refused below 1 000 sf, or at any area when it records an automated group with "
                "a bound schedule (`LM83_EDITION_MISMATCH`)."
            )
        ),
    ] = None,
) -> ResultsResponse:  # noqa: DOC201
    """Read an annual simulation result: statistics, per-point aggregates, timeseries, CSV, or
    LM-83/EN 17037 compliance.

    Use it after `run_annual`: it reads the `.ill` matrix that run committed for a sensor grid.
    Values are photopic illuminance in **lux**, converted from the matrix's RGB irradiance.

    `action` selects one read per call. "summary" for whole-grid min/max/mean over every point and
    hour; "points" for one annual aggregate per sensor; "timeseries" for hourly values at selected
    points and hours; "compliance" for a judged verdict against a named standard; "blind_schedule"
    for LM-83 section 2.2.6's (or LM-83-22 section 4.6's) hourly blind states and the trigger that
    produced them -- `states` is
    **one series for the whole grid and is never to be distributed over window groups**, while
    `groups` carries the per-group schedule read from a sealed `sched:<grid>`. `groups` is `null`
    where no schedule could be published -- most often because the project holds none, since only
    `run_annual(blind_control='lm83')` seals one, but also where the sealed file cannot be trusted;
    an advisory always names which. **Under LM-83-12, refused with `BLIND_TRIGGER_MIXED_BASIS` on
    an analysis area below 1 000 sf whose direct matrix carries materially fewer sensor points than
    LM-83's own 0.6 m placement would put inside the grid's recorded polygon**: below that area
    section 2.2.6's row is an absolute point count rather than a percentage, so a sparse grid leaves
    blinds open in hours the standard closes them and overstates sDA. At or above 1 000 sf the same
    mismatch over-closes and understates sDA instead, and it is reported at `warn` with the schedule
    still published. `trigger.points_expected` carries LM-83's count on every schedule so the
    comparison is visible without a refusal; "metrics" for UDI and DA as measured values with **no
    verdict of any kind** -- UDI is not a standard, its 2 000 lux ceiling is contested research and
    this corpus states no analysis period for it; "export" to write the whole matrix as CSV.

    `compliance(standard="en12464")` is refused, naming `run_electric`, which computes EN 12464-1
    on a zoned grid. EN 17037's provision axis takes Table A.2 when every window group lighting
    the grid lies wholly above its reference plane, else A.1 (EN17037_OPENING_CLASS); a grid lit
    through both kinds is refused (EN17037_OPENINGS_MIXED).
    """
    if job_id is not None or result is not None:
        # **Withdrawn rather than built (ruling R100).** Both were published, neither was ever
        # implemented, and each is now answered a different way: `job_id` by `grid` plus the
        # project manifest, `result` by `grid` plus `chain`, which selects when the grid holds
        # more than one annual result (R105; there is no preference order). Refused above
        # `_resolve` and above the missing-argument check, so a caller written against the old
        # spec is told its argument is gone rather than answered from an artifact it did not name.
        field = "job_id" if job_id is not None else "result"
        return _refuse_not_implemented(
            f"results({field}=...) is withdrawn from the specification (ruling R100) and was "
            "never implemented. Pass grid=... instead -- results(grid=..., chain=...) reads the "
            "annual result; chain= selects which one when the grid holds more than one.",
            field=field,
        )

    if (refusal := refuse_missing("results", action=action, project=project, grid=grid)) is not None:
        return refusal

    if action not in _DISPATCHABLE_ACTIONS:
        # **The message is built from the set, not typed beside it.** It used to end
        # `"('blind_schedule' is published but not built -- it refuses, naming Phase 7c.)"`, which
        # became false the moment this task built the action; and before that it omitted
        # `blind_schedule` from a hand-typed list, so a caller who mistyped an action was told to
        # use one that could not answer. Both are the same defect -- a second spelling of the set
        # -- and generating the sentence from `_DISPATCHABLE_ACTIONS` is what stops it recurring.
        return _refuse_argument(
            f"action={action!r} is not one of {', '.join(sorted(_DISPATCHABLE_ACTIONS))}.",
            field="action",
        )

    if ":" in str(grid):
        # **Ruling R186, and it closes a live shipped defect rather than tightening a name.**
        # Phase 7d-1 Task 7 seals one annual result per (window group, blind state) under
        # `ill:<grid>:<group>:<state>`, and `_resolve` builds its candidate ids as
        # `f"{prefix}{grid}"` -- so `grid="floor:north:open"` resolved `ill:floor:north:open`, an
        # operand of the blend, and every action then reported **one facade's contribution in one
        # blind state** as the whole grid's answer: an sDA computed from a matrix that is missing
        # every other window group's light, published with no advisory and no way for a caller to
        # tell. Refused on its face, before the project is opened (R14's own definition of what
        # `ARGUMENT_OUT_OF_RANGE` is for), and the message names the grid that *does* answer.
        return _refuse_argument(
            f"grid={grid!r} contains ':', which is the separator this server's artifact ids use "
            f"and never part of a grid name. {str(grid).split(':', 1)[0]!r} is the grid here; the "
            "rest names a window group and a blind state, and the per-(group, state) matrices "
            "sealed under those ids are OPERANDS of the LM-83 blend, not results. Each carries "
            "only the light through one facade in one blind state, so reading one as a grid "
            "result would report an sDA missing every other window group's contribution. Pass "
            f"grid={str(grid).split(':', 1)[0]!r}, and blind_control='lm83' to read the blended "
            "result those operands were summed into.",
            field="grid",
        )

    if blind_control is not None and blind_control not in _READ_BLIND_CONTROLS:
        # **Unreachable from the wire**, and kept for the same reason `chain`'s branch below and
        # `standard`'s further down are: `blind_control` is typed `BlindControl | None`, so the SDK
        # rejects an out-of-set string at the schema boundary before this body runs. Every test in
        # this file that imports `results` and calls it as a plain function bypasses that schema
        # entirely, and without this an unknown string would fall through `_resolve`'s
        # `want_blended = blind_control == "lm83"` as **"not lm83", i.e. as `"none"`** -- a
        # malformed argument silently answered from the unblended result, which is worse than an
        # uncaught exception because nothing anywhere says it happened.
        #
        # **The comment this replaces opened "Reachable from the wire for exactly one value" and
        # was false when it was written** (Phase 7d-1 Task 15a, commit 6730ac9): it was about
        # `"fixed"`, which the Literal rejected at the boundary exactly as it rejects any other
        # value, so the branch's `fixed`-specific recovery sentence could never be delivered to a
        # client. R197 accepts `"fixed"` instead, so the special case is gone and this branch is
        # now what `chain`'s has always been -- and says the same thing about itself.
        return _refuse_argument(
            f"blind_control={blind_control!r} is not one of "
            f"{', '.join(sorted(_READ_BLIND_CONTROLS))}.",
            field="blind_control",
        )

    if chain is not None and chain not in _ARTIFACT_PREFIX_FOR_CHAIN:
        # **Unreachable from the wire**, and kept for the same reason `standard`'s own out-of-set
        # branch below is: `chain` is typed `Chain | None`, so the SDK rejects an out-of-set string
        # at the schema boundary before this body runs
        # (`test_an_unknown_chain_is_rejected_at_the_schema_boundary` asserts exactly that). Every
        # test in this file that imports `results` and calls it as a plain function bypasses that
        # schema entirely, and `_ARTIFACT_PREFIX_FOR_CHAIN[chain]` would `KeyError` out of
        # `_resolve` -- an uncaught exception, which check 0.4 forbids -- rather than refusing.
        return _refuse_argument(
            f"chain={chain!r} is not one of 2phase, 3phase, 5phase.", field="chain",
        )

    if lm83_edition is not None and lm83_edition not in LM83_EDITIONS:
        # **Unreachable from the wire**, like `chain`'s branch above: the input schema's `Literal`
        # rejects any other string first. A direct call reaching the body is refused here, before
        # anything below reads an unknown value as LM-83-12 (R864).
        return _refuse_argument(
            f"lm83_edition={lm83_edition!r} is not an LM-83 edition; the editions are exactly "
            "'LM-83-12' and 'LM-83-22', letter case included.",
            field="lm83_edition",
        )
    edition: Lm83Edition = lm83_edition if lm83_edition is not None else DEFAULT_LM83_EDITION

    if (refusal := _validate_threshold_lux(threshold_lux)) is not None:
        # **Above `runtime = current()`, action-agnostic** -- the same placement and posture
        # `_validate_lm83_metrics`/`_validate_en17037_arguments` take for their own arguments below.
        # Only `action="points"` reads `threshold_lux` (`_points`), but a malformed value is refused
        # regardless of `action`: a well-formed-but-unused argument (`city` on a non-Method-1 call)
        # is left alone, but a malformed one is not made to wait for the one action that would have
        # used it before being told it is wrong.
        return refusal

    # **`threshold_lux` on `action="summary"`, `"timeseries"` or `"export"` is accepted and
    # silently unused, not refused -- Task 16 review Minor 4.** Every branch below that dispatches
    # to one of those three actions simply never reads the local `threshold_lux`. No advisory is
    # attached and the register allocates no code for "this argument did nothing on this call" (the
    # register was searched; stretching an existing code to mean this would make that code mean two
    # things). `threshold_lux`'s own argument description states the rule plainly (Phase 20,
    # R797), where a caller of those three actions is reading this same signature -- that is the
    # one place this decision needed to be visible, and it now is documented rather than left to be
    # inferred.

    if action == "metrics":
        # **`metrics` means a different set per action, and the boundary is enforced from both
        # sides** (surface change 2, owner decision Owner-6: the argument keeps its name and the
        # collision is resolved by validating per action). Placed beside `compliance`'s own
        # validation and above `runtime = current()`, the same R39 posture: pure argument checks
        # first, no project opened to answer them.
        if (refusal := _validate_annual_metrics(metrics)) is not None:
            return refusal

    # R721: `usage_hours` is read by exactly one route; anywhere else it would be ignored.
    if usage_hours is not None and not (action == "compliance" and standard == "en17037" and axis == "glare"):
        return _refuse_argument(
            "usage_hours is read only by results(action='compliance', standard='en17037', "
            "axis='glare'); this call would ignore it.",
            field="usage_hours",
        )

    if action == "compliance":
        # **Every `compliance` argument refusal below is pure**: no project is opened, no path is
        # contained, no file is read. All of it runs before `runtime = current()`, which is as far
        # above any I/O as a refusal in this function can sit -- the same placement principle Task
        # 11's job_id/result check established (`results` publishes no `dry_run` for R39 to bind
        # to). `standard` is checked first because everything after it -- `metrics`, or `level`/
        # `method`/`axis`/`usage_hours` -- only makes sense once which standard is known;
        # validating one standard's own arguments against another's call would be validating an
        # argument that standard does not even use.
        if standard is None:
            return _refuse_argument(
                "results(action='compliance') requires standard -- one of 'lm83', 'en17037', "
                "'en12464'.",
                field="standard",
            )
        if standard not in ("lm83", "en17037", "en12464"):
            # **Unreachable from the wire.** `standard` is typed `Literal[...] | None`, so the SDK
            # already rejects an out-of-set string at the schema boundary before this body ever
            # runs -- `test_an_unknown_standard_is_rejected_at_the_schema_boundary` asserts exactly
            # that, the same pattern `_validate_selectors`' own `bool` check states for `point_ids`/
            # `hours`. Kept for a direct, in-process caller (every test in this file that imports
            # `results` and calls it as a plain function, bypassing the schema entirely).
            return _refuse_argument(
                f"standard={standard!r} is not one of lm83, en17037, en12464.", field="standard",
            )
        if standard == "en12464":
            return _refuse_standard_deferred(standard)
        if standard == "lm83":
            if (refusal := _validate_lm83_metrics(metrics, edition=edition)) is not None:
                return refusal
        else:
            if (
                refusal := _validate_en17037_arguments(
                    axis=axis, method=method, level=level, usage_hours=usage_hours
                )
            ) is not None:
                return refusal

    if (refusal := _refuse_unread_edition(lm83_edition, action, standard, metrics)) is not None:
        return refusal

    runtime = current()
    try:
        if action == "blind_schedule":
            # **Above `_resolve`, and outside it.** The schedule is derived from `illdir:<grid>`
            # alone; resolving a chain result here would make one a precondition of an answer that
            # does not depend on it, and would apply R105's `ANNUAL_RESULT_AMBIGUOUS` to a call that
            # opens neither of the two matrices that ambiguity is between. Inside the same `try`, so
            # the handler chain below shapes its refusals -- `_AnalysisAreaNotDerivable`,
            # `GridNotFoundError`, `_NoDirectMatrix` and `_MatrixShapeMismatch` are all
            # `ProjectError`s, and `PathOutsideRoot` is caught in the same clause.
            return _blind_schedule(
                runtime, project=project, grid=grid, lm83_edition=edition,
                named=lm83_edition is not None,
            )
        # **`_resolve` returns a list, and `action` is what decides how long it can be** (Phase 7c
        # Task 17, ruling R113). Two elements only for an unselected `compliance` call on a grid
        # holding both annual results; one for everything else, including every `chain=` call. The
        # argument is passed positionally-by-keyword and has no default, so a future action cannot
        # inherit a policy nobody chose for it.
        states = _resolve(
            runtime, project=project, grid=grid, chain=chain, blind_control=blind_control,
            action=action,
        )
        if len(states) > 1:
            # Only `compliance` can be here -- `_ACTIONS_ANSWERING_FROM_BOTH_RESULTS` is what
            # `_resolve` consulted, and every other action raised `_AmbiguousAnnualResult` there.
            return _compliance_by_chain(
                runtime, states, project=project, grid=grid, standard=str(standard),
                metrics=metrics, level=level, edition=edition, named=lm83_edition is not None,
            )
        state = states[0]
        if action in ("summary", "points", "timeseries", "export"):
            # Final fix E1. `metrics` and `compliance` check inside, after the sealed-schedule
            # refusal, which is the more specific on a blend.
            refusal = _refuse_window_groups_not_current(
                state, project=project, grid=grid, reads=action,
            )
            if refusal is not None:
                return refusal
        if action == "summary":
            return _summary(state, project=project, grid=grid)
        if action == "points":
            return _points(state, project=project, grid=grid, threshold_lux=threshold_lux)
        if action == "timeseries":
            return _timeseries(
                state, project=project, grid=grid, point_ids=point_ids, hours=hours
            )
        if action == "metrics":
            return _metrics(
                state, project=project, grid=grid, metrics=metrics, edition=edition,
                named=lm83_edition is not None,
            )
        if action == "compliance":
            # **Both `_compliance_*` functions return their result MODEL, not an envelope**, and
            # only a refusal comes back as a `CallToolResult` (Task 17). That is what lets
            # `_compliance_by_chain` above reuse the identical function per entry instead of a
            # second implementation: the single-result path wraps, the entry path re-shapes, and
            # neither re-expresses a threshold, an advisory or a refusal ordering.
            built = (
                _compliance_lm83(
                    runtime, state, project=project, grid=grid, metrics=metrics, edition=edition,
                    named=lm83_edition is not None,
                )
                if standard == "lm83"
                # Only `standard="en17037"` can reach here: `en12464` refused above, and en17037's
                # own `method="1"`/`axis="glare"` refused inside `_validate_en17037_arguments`,
                # also above -- so by this point `level` is one of "minimum"/"medium"/"high" and
                # `method == "2"`.
                else _compliance_en17037_provision(
                    state, project=project, grid=grid, level=level
                )
            )
            return built if isinstance(built, CallToolResult) else emit(built)
        return _export(state, project=project, grid=grid, export_format=format)
    except _NoAnnualResult as exc:
        logger.info("results refused: %s", exc)
        return refuse(str(exc))
    except MatrixFileUnreadableError as exc:
        # The `.ill` is registered but will not open -- a job that died before writing its
        # stdout_path, a deleted artifact, or a path that is no longer a regular file. Shaped rather
        # than allowed to escape as a raw OSError carrying an absolute path (check 0.4).
        logger.info("results refused: %s", exc)
        return refuse(str(exc))
    except GridFileOutsideRoot as exc:
        # Final fix G, fix round 3 (S2, controller ruling): `field="grid"`, the argument the caller
        # changes; every other PathOutsideRoot keeps `refuse_engine_error`'s "root".
        logger.info("results refused: %s", exc)
        return refuse(str(exc), [Advisory(
            code="PATH_OUTSIDE_ROOT", severity="error", field=exc.field, message=str(exc))])
    except (ProjectError, PathOutsideRoot, RootsNotConfigured, CodedWriteRefusal) as exc:
        # **Above the `ValueError` clause, and that ordering is the whole fix.** `CodedWriteRefusal`
        # (R855's `PROJECT_WRITE_FAILED` from the CSV write) is a `ValueError` too, for the same reason.
        # `PathOutsideRoot.__mro__` is `[PathOutsideRoot, ValueError, Exception, BaseException,
        # object]` -- verified -- and Python matches `except` clauses in source order, so while the
        # bare `ValueError` stood first this clause was **unreachable** for the one exception it
        # most exists to shape. Measured consequence: a containment breach came back with **no**
        # `PATH_OUTSIDE_ROOT` advisory and prose saying the file "could not be read", i.e. a
        # security refusal dressed as a data problem, which an operator triages as a corrupt matrix
        # rather than as an escape attempt. The identical breach through
        # `project(action="describe")` *did* carry the code, so this was inconsistent as well as
        # wrong. `test_results_tool.py` pins the code now; nothing did before.
        logger.info("results refused: %s", exc)
        return refuse_engine_error(exc)
    except ValueError as exc:
        # A matrix whose body contradicts its own header -- truncated, mis-shaped, or not an annual
        # result at all. `rad_engine.results` raises this with the row number and both counts.
        # Deliberately kept as the broad `ValueError` rather than narrowed: `rad_engine.results`
        # raises several unrelated `ValueError` subclasses for this one condition, and the clause
        # above now takes the containment family out of its path by ordering, which is the narrower
        # change of the two available.
        logger.info("results refused: %s", exc)
        return refuse(
            f"the annual result for grid {grid!r} could not be read: {exc}"
        )
    except NotARegularFileError as exc:
        # `export`'s destination is a FIFO, device or directory. `open_regular_file` refused it on
        # the descriptor rather than blocking, which is the whole point -- `Path.open("w")` on a
        # FIFO blocks forever and wedges an anyio worker. Shaped rather than escaping raw (0.4).
        #
        # **Coded as of ruling R58, 2026-08-10.** This was a bare `refuse` with no advisory at all:
        # shaped enough to satisfy check 0.4, but leaving a client with prose and nothing to branch
        # on. `PROJECT_PATH_NOT_A_REGULAR_FILE` is the same condition `run_annual` hits at its own
        # write path, so it takes the same code rather than a second one meaning the same thing in
        # a different tool.
        logger.info("results refused: %s", exc)
        # R851 fix round 3: every `NotARegularFileError` reaching here carries its own one remedy
        # and says nothing was written (`refuse_linked_components`, `_contained_csv_path`'s own);
        # appending a second one said "Nothing has been written." twice with two remedies.
        message = f"the CSV export could not be written for grid {grid!r}: {exc}"
        return refuse(
            message,
            [
                Advisory(
                    code="PROJECT_PATH_NOT_A_REGULAR_FILE",
                    severity="error",
                    field="project",
                    message=message,
                )
            ],
        )
    except OSError as exc:
        # *(Corrected 2026-10-05, R851 fix round 3: this clause documented `ELOOP` (a symlink at the
        # final component) and a FIFO's `ENXIO` as what arrives here, and called anything else "not
        # a regular file". Since R851 the CSV's name is `lstat`-checked and its rows are staged under
        # a random name opened `"xb"` (`replace_file_atomically`), so neither arrives from the
        # destination any more; what does is a write the disk or the permissions refuse -- a
        # read-only CSV (`EACCES`, kept, not replaced), a full volume, an I/O error mid-stream --
        # which was told to remove a FIFO, a device or a socket (measured with an injected
        # `OSError(5)`).)*
        #
        # **Audited 2026-08-10**: this `try` also wraps `_resolve` and the read actions, but
        # `rad_engine.results` wraps every matrix open in `MatrixFileUnreadableError`, caught above,
        # so the ordinary read failures do not reach here (`chmod 000` on `08_results` with
        # `action="summary"`, measured). Coded `PROJECT_WRITE_FAILED`, field `project` (R855; it was
        # `PATH_OUTSIDE_ROOT`, field `root`, L-112): the code the other writers give a write the disk
        # refuses. The CSV's own write is refused by `_export` through `write_failure`, which names
        # a read-only CSV; this clause is what any other `OSError` of the export reaches.
        logger.info("results refused: %s", exc)
        message = (
            f"the CSV export could not be written for grid {grid!r}: {exc.strerror or exc}. "
            "Nothing was committed, and a CSV already there is unchanged. Check whether the CSV "
            "itself is read-only, whether the project's results directory is writable, and whether "
            "its disk has space, and re-run."
        )
        return refuse(
            message,
            [
                Advisory(
                    code="PROJECT_WRITE_FAILED",
                    severity="error",
                    field="project",
                    message=message,
                )
            ],
        )
