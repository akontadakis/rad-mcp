"""The `quality` tool -- docs/tool-surface.md#16-quality.

Publishes what Phase 9 Task 3 built at `rad_core.quality` (the sourced preset tables, the
explain/diagnose lookups) as the fifteenth tool on the wire. `rad_core.quality` is pure lookup and
arithmetic; this module contains and validates arguments, picks the lookup, and shapes the envelope
-- no number reported here is computed here, the same posture `image_tool` states for its own reads.

**`quality` refuses an unknown `action` and, on `action="estimate"` only, an unknown key in `plan`
(R719); nothing else.** docs/tool-surface.md #16 read *"Refuses
-- nothing. It is advisory by nature."* through Phase 9 Task 15; that sentence was **false of this
tool from the day it was built** -- measured: the commit that added this file, `0edb430`, already
carried the `ARGUMENT_OUT_OF_RANGE`/`field="action"` refusal -- and Phase 9
Task 16 replaced it with the one exception plus the reason. The quotation is kept as the superseded
reading rather than dropped, so the reason this module's contract names an unknown `action` as a
refusal stays legible. R719 (2026-09-26) added the second refusal: `plan`'s one key is `steps`, and a
`plan` of `{"stepz": [...]}` had returned an empty `estimate.steps` with no advisory; it is now
refused with `ARGUMENT_OUT_OF_RANGE`, `field="plan"`, one advisory per unknown key. `presets`,
`explain` and `diagnose` return before `plan` is checked, so a `plan` sent with them is neither read
nor refused. An unknown `symptom` or `parameter` is answered, not refused:
`action="diagnose"` with a symptom this tool does not recognise returns `diagnosis: null` and lists
the symptoms that *do* match, and `action="explain"` with an unrecognised `parameter` returns a
payload naming the known flags, in both cases through the normal success envelope. An unknown
`action` is refused on its face, with `ARGUMENT_OUT_OF_RANGE` and `field="action"` -- ruling R14's
code for an argument rejected before any lookup is attempted.

**`action="presets"` returns two named families, and never merges them (check 9.8).** `ambient`
(`draft`/`standard`/`high`/`reference`, from `docs/specs/workflows-point-in-time-to-annual.md`
section 1.2) and `rpict` (`fast`/`accurate`/`maximum`, from `docs/specs/rpict-parameter-guide.md`'s
"Quality presets" table) are different tables written for different purposes -- one for illuminance
work, one for a DGP recipe -- and are returned under their own names, each carrying the spec file it
came from in `source`, rather than folded into one invented scale. This is the check 9.8 defect this
tool exists to make impossible: an ambient `"high"` and an rpict `"accurate"` share no flag values,
and a caller asking for either gets that family's own sourced numbers.

**Controller ruling R306: seven preset names, not six.** `PRESET_FAMILY` (`rad_core.quality`) and
this tool's own `preset` argument both carry all seven -- four ambient plus three rpict. The
`rad_engine.rpict.GLARE_QUALITY_PRESETS` two-member subset (`accurate`/`maximum`, no `fast`) is a
deliberate, unrelated restriction on what `run_glare` accepts, not a defect in this tool's
Literal -- see `rad_core.quality`'s own module docstring.

**Controller ruling R314: there is no `rad` DETAIL x VARIABILITY grid, and this tool does not invent
one.** An earlier draft of this project's design cited a nine-cell grid at
`docs/specs/radiance-engine-overview.md` section "rad variables"; measured at 7fec46f, that heading
does not exist, "average ZONE dimension" appears nowhere under `docs/specs/`, and
`grep -rn "VARIABILITY" docs/specs/` returns exactly two lines (`radiance-engine-overview.md:496`
and `:508`) -- two example `rad` variables blocks giving a plain integer `-ar` for two of nine
cells, not the scene-relative `Nd` grid once described. **Scoped to `docs/specs/` deliberately, and
stated without a totality word over the whole repo (Task 16 fix round 1):** `VARIABILITY` also
appears in `tests/fixtures/scenes/test_room/scene.rif:4-5` (`DETAIL= high`/`VARIABILITY= high`), a
test fixture's own `rad` options file whose `render=` line carries a hand-written `-ar 4224` and
which states no mapping from the pair to it -- a third DETAIL/VARIABILITY cell in this repository,
and not a source for one. `docs/tool-surface.md` #16 carries the full classified table.

`docs/tool-surface.md` #16 carried the "rad variables" citation and it was the defect;
**Phase 9 Task 16 corrected it** -- #16 now records the measurement above and keeps the superseded
reading beside it (the R101/R106 shape) instead of asserting the grid.

So `detail`/`variability` **stay in the published signature** (R302's reasoning: shrinking a wire
contract is a breaking change on a tool with no callers is still a breaking change), and when
either is given, `action="presets"` adds a third family named `rad` whose
content **states that the grid is not published, and why** -- a result, never a refusal, exactly as
this tool's own contract requires.

**`diagnose`'s `do_not` is negative advice, and it is the part not to skim.** The two entries users
get backwards -- "lower `-aa` to fix splotches" and "raise `-ab` without lowering `-lw`" -- are both
wrong in the direction that looks plausible, so `do_not` is its own field on the wire rather than
prose folded into `cause`, carried through unchanged from `rad_core.quality.Diagnosis.do_not`.

**`action="estimate"` is uncalibrated, and says so on every call.** No project in this codebase yet
records enough `wall_time_s` samples to fit a per-step estimator against -- the same state
`rad_engine.jobs.JobStore.submit` already reports, via the same code, on the jobs whose steps
declare an `estimate_s` (`JobStore.submit`'s `if record.estimate_s is not None:` guards that append,
and `rad_engine.jobs.Step.estimate_s` defaults to `None`, so a job whose steps declare none is
accepted and carries no such advisory -- a different trigger, one shared meaning). Until that
self-calibration lands, this action's `wall_time_s` and every step's `estimate_s` are `0.0`: a
stated absence of data, not a guessed duration, so the register's `RUNTIME_ESTIMATE_UNCALIBRATED`
(info) fires on every call rather than an invented number being reported as a measurement. `project`
is accepted (it is part of the published signature) but not read this phase -- there is no
per-project recorded-time store yet for it to read.

**`PRESET_INAPPROPRIATE_FOR_METRIC` (warn) is allocated and deliberately not emitted here.**
Verified: `tests/test_advisories.py`'s `_ALLOCATED_WITHOUT_AN_EMITTER` still names it under
"16. quality", and building a firing condition for it in this task would have to remove it from that
list in the same commit -- an edit to a test file this task's own file list does not include.
`preset` is therefore accepted on this tool's signature (it is part of the published contract) but
does not yet drive a check; a later task may give it one.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from mcp_types import CallToolResult
from pydantic import BaseModel, ConfigDict, Field, RootModel

from rad_core.quality import (
    AMBIENT_PRESETS,
    DGP_PRESETS,
    DIAGNOSES,
    PARAMETERS,
    diagnose,
)

from rad_mcp.envelope import Advisory, RadResult, Refusal, emit, one_of_schema, refuse
from rad_mcp.unknown_arguments import unknown_key_advisories

__all__ = ["QUALITY_ACTIONS", "QualityToolResult", "quality"]

#: The whole published action set (docs/tool-surface.md #16). All four are built by this task --
#: unlike `image`'s Phase 8 landing, there is no live/specified split here.
QUALITY_ACTIONS: tuple[str, ...] = ("presets", "explain", "diagnose", "estimate")

#: `rpict-parameter-guide.md`'s 10th flag row (line 32, "Specular jitter") is `-sj`, which is not a
#: real `rpict` option (ruling R271); its values are carried under `-ss` in both `DGP_PRESETS` and
#: `PARAMETERS`. `explain(parameter="-sj")` resolves here rather than returning nothing, so a caller
#: reading the source table's own flag name is pointed at the substitution instead of a blank.
_PARAMETER_ALIASES: dict[str, str] = {"-sj": "-ss"}

#: Ruling R314 (Task 3, carried to this tool): the reason `action="presets"` states in the `rad`
#: family's payload and its `source` entry when `detail`/`variability` are given. No source in this
#: repository states the nine-cell DETAIL x VARIABILITY grid `docs/tool-surface.md#16-quality` once
#: cited -- `radiance-engine-overview.md` has no "rad variables" heading, and "average ZONE
#: dimension" does not appear anywhere under `docs/specs/`. `grep -rn "VARIABILITY" docs/specs/`
#: returns exactly two lines, two example `rad` variables blocks
#: (`radiance-engine-overview.md:495-496`, `:507-508`) giving an integer `-ar` for two of nine
#: cells -- not the grid once described. (`tests/fixtures/scenes/test_room/scene.rif:4-5` is a
#: third such cell, in a test fixture rather than a spec, and sources no cell value either; see
#: this module's own docstring.) Publishing even those two would imply the other seven
#: exist, which they do not (`rad_core.quality`'s own module docstring makes the same call for
#: `RAD_GRID`), so this tool reports the absence and its reason rather than a partial table.
_RAD_GRID_NOT_PUBLISHED_REASON: str = (
    "The rad DETAIL x VARIABILITY grid is not published (ruling R314). "
    "docs/specs/radiance-engine-overview.md has no 'rad variables' heading, and 'average ZONE "
    "dimension' appears nowhere under docs/specs/. Under docs/specs/ -- the sourcing corpus this "
    "claim is scoped to -- the only DETAIL/VARIABILITY text is two example rad variables blocks "
    "(radiance-engine-overview.md, section 6.3 'Scene-Specific Optimization') giving an integer "
    "-ar for two of nine cells; a third cell exists in a test fixture (the test_room scene's "
    "scene.rif) and sources no value either. Neither is the nine-cell scene-relative grid "
    "docs/tool-surface.md #16 once cited; that citation was itself the defect, and #16 now keeps "
    "it only as a labelled superseded reading beside its correction. Publishing two of nine cells "
    "would imply the other seven exist; they do not, so this tool reports the gap rather than "
    "inventing them."
)

#: `rad_engine.jobs.JobStore.submit` attaches this same code, for the same reason, to the jobs that
#: declare an `estimate_s` (NOT to every job it accepts -- `submit` guards the append with `if
#: record.estimate_s is not None:`, and its own `estimate_s=(...)` makes that `None` whenever any step
#: has no estimate) -- no project in this codebase yet records enough `wall_time_s` samples for a
#: per-step estimator to be fit against. `quality(action="estimate")` states it again here
#: because this is
#: the tool a caller reaches for specifically to ask "how long will this take", and every number it
#: can return today is `0.0` -- an absence of data, not a measured duration.
_ESTIMATE_UNCALIBRATED_MESSAGE: str = (
    "This estimate is uncalibrated: no project in this codebase yet records enough wall_time_s "
    "samples to fit a per-step estimator against (rad_engine.jobs.JobStore.submit attaches this "
    "same code, for the same reason, to the jobs that declare an estimate_s). wall_time_s and "
    "every step's "
    "estimate_s below are 0.0 -- a stated absence of data, not an invented duration."
)


# --- nested shapes ------------------------------------------------------------------------------


class _Nested(BaseModel):
    model_config = ConfigDict(extra="forbid")


# R810 moved this from the published text: the eighteen rows are `rad_core.quality.PARAMETERS`
# (docs/tool-surface.md #16), and an unrecognised parameter is answered rather than refused because
# `quality` refuses only an unknown `action` and an unknown `plan` key.
class QualityParameterPayload(_Nested):
    """One parameter's explanation from `quality(action="explain")`.

    `controls`, `artifact_it_fixes` and `render_time_weight` are `null`, `interacts_with` is empty
    and `notes` lists the known flags when `parameter` is not one of the eighteen sourced flags, or
    was not given (`flag` is then `""`): an answer, not a refusal. `"-sj"` is answered as `"-ss"`,
    the flag its sourced values are carried under.
    """

    flag: str
    controls: str | None = None
    artifact_it_fixes: str | None = None
    render_time_weight: int | None = None
    interacts_with: list[str] = []
    notes: list[str] = []


# R810 corrected the old wire text, which said `changes` is empty "exactly when the sourced fix
# names a flag `rad_core.quality.PARAMETERS` does not carry" (`"shadows glow"` -> `-aw`): `"no glare
# sources found"` also has empty `changes`, because the parameter set is not its cause
# (`rad_core.quality.DIAGNOSES`). Either way `do_not` says why, rather than a change being invented.
class QualityDiagnosisPayload(_Nested):
    """One symptom's diagnosis from `quality(action="diagnose")`.

    `changes` maps each flag to its recommended value. It is empty when no change is recommended,
    and `do_not` then says why. `do_not` also warns against plausible wrong fixes, such as lowering
    `-aa` for splotches: read it before applying `changes`. `source` names the document the
    diagnosis comes from.
    """

    symptom: str
    cause: str
    changes: dict[str, float | int]
    do_not: list[str]
    source: str


class QualityEstimateStepPayload(_Nested):
    step: str
    artifact: str | None = None
    estimate_s: float
    reuse: bool


class QualityEstimatePayload(_Nested):
    wall_time_s: float
    basis: Literal["calibrated", "uncalibrated"]
    n_samples: int
    steps: list[QualityEstimateStepPayload]


# --- the result branches -------------------------------------------------------------------------


class QualityBase(RadResult):
    """What every quality-shaped answer carries. Also the union schema's shared base.

    Carries no fields of its own beyond `RadResult.advisories`: each branch's own `action` literal
    lives on the branch, not here, so it is never inlined at the schema's top level as a
    single-valued discriminator that would constrain every sibling payload
    (`tests/test_protocol.py::test_no_inlined_base_carries_a_single_valued_discriminator`).
    """


# The module docstring's ruling R314 section is the full argument; the pointer to it left the
# published description under R809 (item 38), and R810 moved the ruling's number out.
class QualityPresets(QualityBase):
    """The sourced preset tables from `quality(action="presets")`.

    `families` holds two tables that are never merged into one scale: `ambient` (`draft`,
    `standard`, `high`, `reference`, as `ab`/`ad`/`as`/`ar`/`aa`), for illuminance work, and
    `rpict` (`fast`, `accurate`, `maximum`, each flag without its dash), for a DGP render. `source`
    names where each table comes from. `families["rad"]` and `source["rad"]` are present only when
    `detail` or `variability` was given, and state that `rad`'s DETAIL x VARIABILITY grid is not
    published (`published: false` and a `reason`), not a value.
    """

    action: Literal["presets"]
    families: dict[str, dict[str, Any]]
    source: dict[str, str]


class QualityExplained(QualityBase):
    """`quality(action="explain")`'s answer for one `parameter`."""

    action: Literal["explain"]
    parameter: QualityParameterPayload


# R810 moved this from the published text: the quotation of docs/tool-surface.md #16, *"an unknown
# `symptom` or `parameter` is answered, not refused"*.
class QualityDiagnosed(QualityBase):
    """`quality(action="diagnose")`'s answer for one `symptom`.

    `diagnosis` is `null` and `known_symptoms` lists every symptom this tool recognises when
    `symptom` matched none of them or was not given: an answer, not a refusal. `known_symptoms` is
    `null` when `diagnosis` is set.
    """

    action: Literal["diagnose"]
    diagnosis: QualityDiagnosisPayload | None
    known_symptoms: list[str] | None = None


class QualityEstimated(QualityBase):
    """`quality(action="estimate")`'s answer: one `estimate.steps` entry per name in `plan["steps"]`.

    No estimator is fitted yet: `wall_time_s` and every step's `estimate_s` are `0.0`, an absence of
    data and not a duration; `basis` is `"uncalibrated"`, `n_samples` is `0`, and
    `RUNTIME_ESTIMATE_UNCALIBRATED` (info) is attached on every call. Each step's `artifact` is
    `null` and `reuse` is `false`: no project is read.
    """

    action: Literal["estimate"]
    estimate: QualityEstimatePayload


QualityPayload = QualityPresets | QualityExplained | QualityDiagnosed | QualityEstimated | Refusal


# R810 moved this from the published text: the `oneOf` is the pattern
# `image_tool.ImageToolResult`/`materials_tool.MaterialsResult` already use, not a second one
# invented for this tool (Global Constraints).
class QualityToolResult(RootModel[QualityPayload]):
    """`quality`'s result: one of the four action shapes, or a refusal."""

    @classmethod
    def model_json_schema(cls, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return one_of_schema(super().model_json_schema(*args, **kwargs), QualityBase)


# --- action="presets" ----------------------------------------------------------------------------


def _numeric(value: str) -> int | float:
    """`DGP_PRESETS` values are the strings `rpict`'s argv carries (ruling R306's own module
    docstring: `"-ab": "5"`, not `5`). This tool's wire shape reports numbers, so the normalisation
    happens here rather than in `rad_core.quality`, which moves the table unchanged."""
    try:
        return int(value)
    except ValueError:
        return float(value)


def _ambient_family_payload() -> dict[str, dict[str, int | float]]:
    return {
        name: {
            "ab": p.ambient_bounces,
            "ad": p.ambient_divisions,
            "as": p.ambient_super_samples,
            "ar": p.ambient_resolution,
            "aa": p.ambient_accuracy,
        }
        for name, p in AMBIENT_PRESETS.items()
    }


def _rpict_family_payload() -> dict[str, dict[str, int | float]]:
    return {
        name: {flag.lstrip("-"): _numeric(value) for flag, value in preset.items()}
        for name, preset in DGP_PRESETS.items()
    }


def _presets(*, detail: str | None, variability: str | None) -> QualityPresets:
    families: dict[str, dict[str, Any]] = {
        "ambient": _ambient_family_payload(),
        "rpict": _rpict_family_payload(),
    }
    source: dict[str, str] = {
        "ambient": "docs/specs/workflows-point-in-time-to-annual.md section 1.2",
        "rpict": "docs/specs/rpict-parameter-guide.md, 'Quality presets' table",
    }
    if detail is not None or variability is not None:
        families["rad"] = {"published": False, "reason": _RAD_GRID_NOT_PUBLISHED_REASON}
        source["rad"] = "no source in this repository (ruling R314)"
    return QualityPresets(action="presets", families=families, source=source)


# --- action="explain" ----------------------------------------------------------------------------


def _normalise_flag(parameter: str) -> str:
    flag = parameter if parameter.startswith("-") else f"-{parameter}"
    return _PARAMETER_ALIASES.get(flag, flag)


def _explained(parameter: str | None) -> QualityExplained:
    if parameter is None:
        known = ", ".join(sorted(PARAMETERS))
        payload = QualityParameterPayload(
            flag="", notes=[f"No parameter given. Known parameters: {known}."]
        )
        return QualityExplained(action="explain", parameter=payload)

    flag = _normalise_flag(parameter)
    spec = PARAMETERS.get(flag)
    if spec is None:
        known = ", ".join(sorted(PARAMETERS))
        payload = QualityParameterPayload(
            flag=flag, notes=[f"Unknown parameter {parameter!r}. Known parameters: {known}."]
        )
        return QualityExplained(action="explain", parameter=payload)

    payload = QualityParameterPayload(
        flag=spec.flag,
        controls=spec.controls,
        artifact_it_fixes=spec.artifact_it_fixes,
        render_time_weight=spec.render_time_weight,
        interacts_with=list(spec.interacts_with),
        notes=list(spec.notes),
    )
    return QualityExplained(action="explain", parameter=payload)


# --- action="diagnose" ---------------------------------------------------------------------------


def _diagnosed(symptom: str | None) -> QualityDiagnosed:
    if symptom is None:
        return QualityDiagnosed(action="diagnose", diagnosis=None, known_symptoms=sorted(DIAGNOSES))

    d = diagnose(symptom)
    if d is None:
        return QualityDiagnosed(action="diagnose", diagnosis=None, known_symptoms=sorted(DIAGNOSES))

    payload = QualityDiagnosisPayload(
        symptom=d.symptom, cause=d.cause, changes=dict(d.changes), do_not=list(d.do_not),
        source=d.source,
    )
    return QualityDiagnosed(action="diagnose", diagnosis=payload)


# --- action="estimate" ---------------------------------------------------------------------------


def _estimated(plan: dict[str, Any] | None) -> tuple[QualityEstimated, list[Advisory]]:
    steps_in = (plan or {}).get("steps") or []
    steps = [
        QualityEstimateStepPayload(step=str(step_name), artifact=None, estimate_s=0.0, reuse=False)
        for step_name in steps_in
    ]
    estimate = QualityEstimatePayload(
        wall_time_s=0.0, basis="uncalibrated", n_samples=0, steps=steps
    )
    advisory = Advisory(
        code="RUNTIME_ESTIMATE_UNCALIBRATED", severity="info", message=_ESTIMATE_UNCALIBRATED_MESSAGE
    )
    return QualityEstimated(action="estimate", estimate=estimate), [advisory]


# --- argument checks -----------------------------------------------------------------------------


def _refuse_unknown_action(action: str) -> CallToolResult:
    """One of the two refusals this tool has (R719 added the other, an unknown key in `plan`):
    docs/tool-surface.md #16 reads *"Refuses -- an `action` outside the four published members,
    and nothing else."* -- quoted with the line-wrap collapsed, `**Refuses**`'s bold dropped and
    the em dash written `--`; under whitespace normalisation alone it is not a substring of that
    document. (Task 10 updates that sentence to name both refusals; this docstring's quotation is
    of the pre-Task-5 text.)

    That section said *"Refuses -- nothing. It is advisory by nature."* until Phase 9 Task 16, and
    this docstring quoted it as current until fix round 2 -- a quotation of text the document no
    longer carries, which is the same defect the section itself had. Ruling R14's code for an
    argument rejected on its face, `field="action"`, the same shape `image_tool._refuse_argument`
    and every other tool's argument refusal use."""
    message = (
        f"quality(action={action!r}) is not one of {QUALITY_ACTIONS}. An unknown symptom or "
        "parameter is answered, not refused, but an unknown action is rejected on its face like "
        "every other tool's argument (ruling R14)."
    )
    return refuse(
        message,
        [Advisory(code="ARGUMENT_OUT_OF_RANGE", severity="error", field="action", message=message)],
    )


# --- the tool -------------------------------------------------------------------------------------


def quality(
    action: Annotated[
        Literal["presets", "explain", "diagnose", "estimate"],
        Field(
            description=(
                "which operation: \"presets\", \"explain\", \"diagnose\" or \"estimate\". The tool "
                "description says what each one does."
            )
        ),
    ],
    preset: Annotated[
        Literal["draft", "standard", "high", "reference", "fast", "accurate", "maximum"] | None,
        Field(
            description=(
                "accepted on every action -- part of the published signature -- but read by none. "
                "`PRESET_INAPPROPRIATE_FOR_METRIC` stays allocated and unemitted "
                "(see docs/tool-surface.md's advisory register); no check emits it."
            )
        ),
    ] = None,
    parameter: Annotated[
        str | None,
        Field(
            description=(
                "\"explain\" only. One Radiance flag, e.g. \"-aa\" or \"aa\" (the leading dash is "
                "optional). \"-sj\" resolves to \"-ss\", the flag its sourced values are actually "
                "carried under (ruling R271) -- it is not a real `rpict` option. Unrecognised "
                "values return a payload naming the flags this tool does know, not a refusal."
            )
        ),
    ] = None,
    symptom: Annotated[
        str | None,
        Field(
            description=(
                "\"diagnose\" only. A visual symptom, e.g. \"splotches\" or \"missing direct sun\", "
                "matched case- and whitespace-insensitively. An unrecognised symptom returns "
                "`diagnosis: null` and lists the symptoms that do match, not a refusal."
            )
        ),
    ] = None,
    project: Annotated[
        str | None,
        Field(
            description=(
                "\"estimate\" only, part of the published signature. Not read: no estimator is "
                "fitted to the `wall_time_s` a project records, so every estimate is "
                "`basis=\"uncalibrated\"` regardless of what project is named."
            )
        ),
    ] = None,
    plan: Annotated[
        dict[str, Any] | None,
        Field(
            description=(
                "\"estimate\" only. `{\"steps\": [...]}`, a list of step names. Each becomes one entry "
                "in `estimate.steps`, at `estimate_s=0.0` -- an absence of data, not a guess."
            )
        ),
    ] = None,
    detail: Annotated[
        Literal["low", "medium", "high"] | None,
        Field(
            description=(
                "\"presets\" only. When given (with or without `variability`), the result gains a "
                "third family, `rad`, whose content states that the DETAIL x VARIABILITY grid is "
                "not published in this repository, and why (ruling R314) -- never a partial table."
            )
        ),
    ] = None,
    variability: Annotated[
        Literal["low", "medium", "high"] | None,
        Field(
            description=(
                "\"presets\" only. Same effect as `detail` -- either one given is enough to add the "
                "`rad` family's \"not published\" statement."
            )
        ),
    ] = None,
) -> QualityToolResult:
    """The parameter guardrails: the audience knows what a splotchy render means physically but not
    that `-ad` fixes it and `-aa` does not.

    Use it before choosing ambient or `rpict` parameters, or when a render or a result looks wrong: it looks up
    the presets, one parameter or one symptom's fix, or lists a plan's steps without a wall-time
    estimate.

    Refuses an unknown `action` and an unknown key in `plan` (R719); nothing else -- it is
    advisory by nature otherwise. An unrecognised `symptom` or `parameter` is answered, listing
    what this tool does recognise, rather than refused.

    "presets" returns the two sourced preset families (`ambient` and `rpict`), never merged into
    one scale (check 9.8). "explain" looks up one parameter's artifact and render-time weight.
    "diagnose" looks up one symptom's fix. "estimate" lists a plan's steps with no wall-time
    estimate: every step and the total are 0.0 s, `basis="uncalibrated"`, reported by
    `RUNTIME_ESTIMATE_UNCALIBRATED`.
    """
    if action not in QUALITY_ACTIONS:
        return _refuse_unknown_action(action)

    if action == "presets":
        return emit(_presets(detail=detail, variability=variability))

    if action == "explain":
        return emit(_explained(parameter))

    if action == "diagnose":
        return emit(_diagnosed(symptom))

    # R719: `plan`'s only known key is `steps`; a misspelt or stray key would be ignored.
    if plan is not None and (
        key_advisories := unknown_key_advisories("quality's plan", plan, ("steps",), field="plan")
    ):
        return refuse(" ".join(a.message for a in key_advisories), key_advisories)

    result, advisories = _estimated(plan)
    result.advisories = advisories
    return emit(result)
