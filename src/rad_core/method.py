"""Method inference for `run_annual` -- pure, no Radiance, fully unit-testable.

Check 4.5 (docs/plan.md): `infer()` is metrics + fenestration facts in, method + reasons out,
runnable with no Radiance binary installed. Specified in
docs/tool-surface.md#method-inference-for-run_annual, which this module implements verbatim.

**Layers.** `rad_core.method` decides *which* method a `run_annual` call should use and *why*.
`rad_core.annual` (a sibling module, out of this module's scope) owns the metric arithmetic that
runs once a method has been chosen -- DA/sDA/ASE/UDI thresholds, tallies, the blind trigger.
`rad_engine.matrices` spawns the Radiance tools that actually build V/T/D/S/Cds. Nothing here
imports either: `infer()` never runs a subprocess and never reaches for `rad_engine`, so it is
green on a machine with no Radiance installed (`tests/test_purity.py`).

**The five rules, in order -- first match wins, and every rule records its reason.** This mirrors
`rad_core.grids`' `plan_grid`/`place_sensor_grid`: one ordered decision, no per-kind branch table
duplicated elsewhere.

1. `ase` in `metrics` -> **5-phase**. 3-phase averages the sun's energy over one of
   146 Reinhart patches, losing directional precision and softening shadows; ASE is defined on the
   direct beam alone, so the smearing hits it directly. "Using the standard 3PM for ASE is a
   methodological error" (specs/workflows-point-in-time-to-annual.md §3.3;
   specs/sda-ase-lm83.md).
2. Any window group has a light-redirecting or specular state (light shelf, prismatic, specular
   louvre, articulated venetian) -> **5-phase**. These systems "are designed to interact with
   sunlight arriving from very specific angles" and patch averaging misrepresents them
   (specs/sda-ase-lm83.md).
3. Any window group has >= 2 states (dynamic blinds) *and* `sda` in `metrics` -> **5-phase**. The
   blind schedule is triggered by a direct-sunlight threshold, and the sDA number depends entirely
   on the schedule (specs/sda-ase-lm83.md §"Blind Schedule Generation";
   skills/lighting-standards-compliance/patterns.md).
4. Any window group has a BSDF, or a non-specular shading device -> **3-phase**. The BSDF *is* the
   T matrix; a daylight-coefficient method bakes the fenestration into one set of coefficients, so
   a glazing change means re-running everything (specs/workflows-point-in-time-to-annual.md
   §3.1-3.2).
5. Otherwise -> **2-phase daylight coefficients**. With simple glazing and no direct-sun-sensitive
   metric, the DC method "brilliantly solves the speed problem" and the 3-phase modularity buys
   nothing (specs/workflows-point-in-time-to-annual.md §3.1).

**Override behaviour.** Rule 1 is a *refusal* of a contradicting `method=` override: `ase` in
`metrics` together with a `requested` that is not `"auto"` and not `"5phase"` is
refused (`METHOD_OVERRIDE_UNSAFE`, error) -- the chosen method stays 5-phase regardless, because a
refused override does not get to stand. Rules 2 and 3 are not refusals, because "a user with a
reason to accept the smearing may have one": an override away from their answer is honoured
(`method` becomes whatever was requested) and a note naming the rule is appended to `reasons`.
No advisory is attached for it (owner decision of 2026-10-02, ruling R766). `METHOD_INFERRED`'s
message carries only the rule's answer and `reasons[0]`, so the note reaches a caller only
through `reasons` (`run_annual`'s `method.reasons`). `BLIND_SCHEDULE_FROM_SMEARED_SUN` is a
different code: it names a schedule derived under 3-phase, which Phase 7c's dedicated direct-sun
leg made unreachable. `method="5phase"` requested on a scene that rules 1-3 would not themselves
have escalated to 5-phase is `METHOD_OVERRIDE_WASTEFUL` at `warn`, not refused -- correct, but
"computationally excessive for simple cases" (specs/sda-ase-lm83.md comparison table).
`METHOD_INFERRED` (info) is emitted on every call, override accepted or not, refused or not --
"the inference must be reported, not hidden".

**Why `ArtifactWarning`, not a raised exception, for the refusal.** `infer()`'s own contract
(docs/tool-surface.md) is "returns the method, the reasons, and *whether an override is refused*"
-- a returned fact, not a raise. This differs from `rad_core.grids.GridError`, whose refusals stop
an operation that was already underway (placing a grid); `infer()` runs before any job starts, and
the tool layer built from `run_annual`'s spec decides what a refusal does next (refuse the call
outright, or -- as `run_annual`'s own refusal text describes -- name the metric and offer
`method="auto"`). `rad_core.manifest.ArtifactWarning` already exists to be "one classified advisory,
kept with" a result rather than a third code/severity/message shape invented here -- same reuse
`rad_core.grids` documents for its own `GRID_POINTS_CLIPPED`.

**Vocabulary.** `Metric` restates `run_annual`'s own `metrics: list[Literal[...]]`
(docs/tool-surface.md §11) rather than importing it, because nothing in `rad_core` -- which may not
import `rad_mcp` -- defines it yet; if that vocabulary is later hoisted into `rad_core.manifest`
alongside `InputKind`/`ArtifactKind`, this should be updated to import it rather than restate it, to
avoid the drift a duplicated vocabulary risks (docs/plan.md's Phase 3 sky-type-list finding).
`StateKind` is new vocabulary this module owns: `window_group.states` is a bare
`dict[str, str]` (state name -> BSDF path) at the wire level with no notion of what kind of state it
is, and rules 2-4 need exactly that classification. It is the tool layer's job to derive each
state's `StateKind` from the BSDF assigned (or not) and whatever the caller tells `window_group`
about the device, and pass the result in as `WindowGroupFacts` -- `infer()` takes facts, not
opinions.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import Literal

from rad_core.manifest import ArtifactWarning

__all__ = [
    "Inference",
    "Metric",
    "Method",
    "RequestedMethod",
    "StateKind",
    "WindowGroupFacts",
    "infer",
]

#: `run_annual`'s own metrics vocabulary (docs/tool-surface.md §11,
#: `metrics: list[Literal["sda","ase","udi","da","en17037_provision","illuminance_timeseries"]]`).
#: See the module docstring's Vocabulary section for why this is restated rather than imported.
#: `annual_dgp` was withdrawn by R815 (docs/plan.md): annual glare needs an image-based view
#: matrix this server does not build.
Metric = Literal["sda", "ase", "udi", "da", "en17037_provision", "illuminance_timeseries"]

#: An *outcome* of inference -- never `"auto"`, which is a request, not an answer.
Method = Literal["2phase", "3phase", "5phase"]

#: What the caller asked for. `run_annual`'s own `method` argument
#: (docs/tool-surface.md §11): `Literal["auto","2phase","3phase","5phase"] = "auto"`.
RequestedMethod = Literal["auto", "2phase", "3phase", "5phase"]


class StateKind(str, Enum):
    """What one window-group state *is*, for the purposes of rules 2 and 4.

    Not `window_group`'s own vocabulary -- there isn't one; `states` is a bare
    `dict[str, str]` (state name -> BSDF XML path) at the wire level (docs/tool-surface.md §6). This
    is the classification `infer()` actually needs, named here because nothing upstream names it.
    See the module docstring's Vocabulary section.
    """

    NONE = "none"
    """No BSDF, no shading device -- simple, static glazing (clear, tinted, diffuse)."""

    BSDF = "bsdf"
    """A BSDF is assigned and it is not one of the light-redirecting/specular kinds below. Rule 4."""

    SHADING_NON_SPECULAR = "shading_non_specular"
    """A non-specular shading device with no BSDF -- LM-83's fallback-ladder territory
    (docs/tool-surface.md §6, `NO_BSDF_FOR_SHADING_DEVICE`). Rule 4."""

    LIGHT_REDIRECTING = "light_redirecting"
    """Light shelf, prismatic glazing, specular louvre, or articulated venetian -- systems that
    "interact with sunlight arriving from very specific angles" (specs/sda-ase-lm83.md). Rule 2,
    ahead of rule 4 regardless of whether a BSDF is also assigned to this or any other state."""


@dataclass(frozen=True)
class WindowGroupFacts:
    """The fenestration facts about one window group that `infer()`'s rules 2-4 need.

    `states` is one `StateKind` per state the group has, in no particular order -- rule 3 only
    needs `len(states) >= 2`, and rules 2/4 only need "any". A group with a single
    `StateKind.NONE` state is static, simple glazing and matches none of rules 2-4.
    """

    name: str
    states: tuple[StateKind, ...]


@dataclass(frozen=True)
class Inference:
    """`infer()`'s return: the method, the reasons, and whether an override is refused.

    Carries everything a caller needs to report the decision without re-deriving it (docs/tool-
    surface.md: "Reported, not hidden") -- `method` is what to actually run with; `requested` is
    what the caller asked for, echoed back so the two can be compared; `reasons` is the human-
    readable citation trail behind `method`, always non-empty; `refused` is true exactly when
    `requested` contradicted rule 1 and was overruled -- `method` is `"5phase"` in that case
    regardless, since a refused override does not take effect; `warnings` carries every
    `ArtifactWarning` this call produced, always including `METHOD_INFERRED` and, when `refused` is
    true, `METHOD_OVERRIDE_UNSAFE` alongside it.
    """

    method: Method
    requested: RequestedMethod
    reasons: tuple[str, ...]
    refused: bool
    warnings: tuple[ArtifactWarning, ...] = field(default_factory=tuple)


def _rule_1_direct_sun_metric(metrics: tuple[str, ...]) -> str | None:
    """Rule 1's reason string, or `None` if it does not match."""
    if "ase" not in metrics:
        return None
    return (
        "metric 'ase' requires an accurate direct-sun component; 3-phase averages the solar "
        "disc into one of 146 Reinhart patches, losing directional precision -- using 3-phase for "
        "ASE is a methodological error (sda-ase-lm83.md, workflows-point-in-time-to-annual.md §3.3)"
    )


def _rule_2_light_redirecting(window_groups: tuple[WindowGroupFacts, ...]) -> str | None:
    """Rule 2's reason string, or `None` if it does not match."""
    offenders = [
        wg.name for wg in window_groups if StateKind.LIGHT_REDIRECTING in wg.states
    ]
    if not offenders:
        return None
    names = ", ".join(offenders)
    return (
        f"window group(s) {names} have a light-redirecting or specular state (light shelf, "
        "prismatic, specular louvre, articulated venetian), which is designed to interact with "
        "sunlight from very specific angles that patch averaging misrepresents (sda-ase-lm83.md)"
    )


def _rule_3_dynamic_blinds_with_sda(
    metrics: tuple[str, ...], window_groups: tuple[WindowGroupFacts, ...]
) -> str | None:
    """Rule 3's reason string, or `None` if it does not match. Requires BOTH >= 2 states on some
    group AND `sda` requested."""
    if "sda" not in metrics:
        return None
    offenders = [wg.name for wg in window_groups if len(wg.states) >= 2]
    if not offenders:
        return None
    names = ", ".join(offenders)
    return (
        f"window group(s) {names} have >= 2 states (dynamic blinds) and 'sda' was requested; the "
        "blind schedule is triggered by a direct-sunlight threshold and the sDA number depends "
        "entirely on it (sda-ase-lm83.md §'Blind Schedule Generation')"
    )


def _rule_4_bsdf_or_shading(window_groups: tuple[WindowGroupFacts, ...]) -> str | None:
    """Rule 4's reason string, or `None` if it does not match."""
    offenders = [
        wg.name
        for wg in window_groups
        if any(s in (StateKind.BSDF, StateKind.SHADING_NON_SPECULAR) for s in wg.states)
    ]
    if not offenders:
        return None
    names = ", ".join(offenders)
    return (
        f"window group(s) {names} have a BSDF, or a non-specular shading device, so a daylight-"
        "coefficient method cannot represent the fenestration -- the BSDF is the T matrix "
        "(workflows-point-in-time-to-annual.md §3.1-3.2)"
    )


_RULE_5_REASON = (
    "no direct-sun-sensitive metric was requested and no window group has complex fenestration; "
    "2-phase daylight coefficients solve the speed problem and the 3-phase modularity buys nothing "
    "(workflows-point-in-time-to-annual.md §3.1)"
)


def infer(
    metrics: Sequence[Metric],
    window_groups: Sequence[WindowGroupFacts],
    requested: RequestedMethod,
) -> Inference:
    """Returns the method, the reasons, and whether an override is refused.

    `metrics` and `window_groups` are facts about the project, not opinions -- see the module
    docstring. `requested` is `run_annual`'s own `method` argument, `"auto"` by default. The five
    rules run in order and the first match decides `chosen`, the method the rules alone would pick;
    `requested` then either confirms it, is refused (rule 1 only), is honoured with a note in
    `reasons` (rules 2/3 only), or triggers `METHOD_OVERRIDE_WASTEFUL` (`method="5phase"`
    requested where `chosen` was not itself 5-phase). `METHOD_INFERRED` is always the first
    warning in the result.
    """
    metrics_t = tuple(metrics)
    groups_t = tuple(window_groups)

    reason_1 = _rule_1_direct_sun_metric(metrics_t)
    reason_2 = _rule_2_light_redirecting(groups_t) if reason_1 is None else None
    reason_3 = (
        _rule_3_dynamic_blinds_with_sda(metrics_t, groups_t)
        if reason_1 is None and reason_2 is None
        else None
    )
    reason_4 = (
        _rule_4_bsdf_or_shading(groups_t)
        if reason_1 is None and reason_2 is None and reason_3 is None
        else None
    )

    if reason_1 is not None:
        chosen: Method = "5phase"
        matched_rule = 1
        reasons = [reason_1]
    elif reason_2 is not None:
        chosen = "5phase"
        matched_rule = 2
        reasons = [reason_2]
    elif reason_3 is not None:
        chosen = "5phase"
        matched_rule = 3
        reasons = [reason_3]
    elif reason_4 is not None:
        chosen = "3phase"
        matched_rule = 4
        reasons = [reason_4]
    else:
        chosen = "2phase"
        matched_rule = 5
        reasons = [_RULE_5_REASON]

    warnings: list[ArtifactWarning] = []
    refused = False
    method: Method = chosen

    if matched_rule == 1:
        # Rule 1 is a refusal of any override that is not "auto" and does not already agree.
        if requested not in ("auto", chosen):
            refused = True
            warnings.append(
                ArtifactWarning(
                    code="METHOD_OVERRIDE_UNSAFE",
                    severity="error",
                    field="method",
                    message=(
                        f"method='{requested}' was refused: {reason_1} run_annual's method stays "
                        f"'{chosen}'; pass method='auto' or method='{chosen}' instead."
                    ),
                )
            )
        # method is always the rule's own answer -- a refused override never takes effect, and an
        # accepted override at this rule can only be "auto" or already "5phase".
        method = chosen
    elif matched_rule in (2, 3):
        # Not refusals: a user with a reason to accept the smearing may have one. The
        # override is honoured -- method becomes whatever was requested.
        #
        # Neither rule gets a dedicated warn-severity ArtifactWarning here. Neither row in the
        # spec's own table carries a "warn if overridden" annotation: rule 2's has none, and rule
        # 3's said "warn if overridden to 3-phase" until R766. The one allocated code whose
        # meaning matches it -- BLIND_SCHEDULE_FROM_SMEARED_SUN -- names a schedule derived under
        # 3-phase, a condition Phase 7c's dedicated direct-sun leg made unreachable; nothing
        # attaches it, and `results(action="blind_schedule")` does not either. Inventing a fresh
        # code for either rule is out of this module's scope -- the register is allocate-once and
        # the owner allocates. The override-accepted fact is still reported, not hidden: it is
        # appended to `reasons`, which `run_annual` publishes as `method.reasons`.
        # METHOD_INFERRED's message carries `reasons[0]` only. The owner decided on 2026-10-02 not
        # to allocate a code for it (R766).
        if requested not in ("auto", chosen):
            method = requested  # type: ignore[assignment]  # requested != "auto" in this branch
            rule_label = "2 (light-redirecting/specular state)" if matched_rule == 2 else "3 (dynamic blinds + sda)"
            reasons.append(
                f"method='{requested}' overrides the '{chosen}' answer of rule {rule_label} and was "
                "honoured -- a user with a reason to accept the smearing may have one."
            )
        else:
            method = chosen
    else:
        # Rules 4/5: no refusal, no smearing-specific warning -- only METHOD_OVERRIDE_WASTEFUL can
        # apply here, handled uniformly below.
        method = requested if requested != "auto" else chosen

    # METHOD_OVERRIDE_WASTEFUL: method="5phase" requested where the rules alone did not themselves
    # land on 5-phase (i.e. matched_rule is 4 or 5). Independent of the rules-2/3 override note
    # above -- that is appended when the override moves AWAY from a 5-phase answer; this fires
    # when it moves TO 5-phase from an answer that did not need it.
    if requested == "5phase" and matched_rule in (4, 5):
        warnings.append(
            ArtifactWarning(
                code="METHOD_OVERRIDE_WASTEFUL",
                severity="warn",
                field="method",
                message=(
                    "method='5phase' requested with no direct-sun-sensitive metric and no complex "
                    "fenestration: correct, but computationally excessive for simple cases "
                    "(sda-ase-lm83.md comparison table)."
                ),
            )
        )

    # METHOD_INFERRED: always emitted, always first, always info. "The inference must be reported,
    # not hidden."
    warnings.insert(
        0,
        ArtifactWarning(
            code="METHOD_INFERRED",
            severity="info",
            field="method",
            message=f"method inferred as '{chosen}': {reasons[0]}",
        ),
    )

    return Inference(
        method=method,
        requested=requested,
        reasons=tuple(reasons),
        refused=refused,
        warnings=tuple(warnings),
    )
