"""Surface optical properties: range/energy-balance validation and the LM-83 default table (pure).

Pure: no Radiance, no subprocesses, no file IO, no file formats. Runs green with no binaries
installed. `rad_core.materials` "validates reflectance/transmittance ranges and energy balance, and
owns the LM-83 default table" (docs/tool-surface.md#5-materials). `rad_engine.materials` is the layer
that emits Radiance primitives (`plastic`, `metal`, `glass`, `trans`; `BSDF` is refused by this
module's `validate_material_type`) from the values this module validates or defaults -- nothing
here knows what a Radiance primitive looks like, the same split `rad_core.geometry` draws against
`rad_engine`'s `.rad` parsing (see that module's own docstring) and `rad_core.grids` draws against
`rad_engine`'s `.pts` writer.

**The provenance guarantee is the point of this module, not a side effect of it.** Every reflectance
this module *writes* -- every value `apply_defaults` substitutes, and every value
`glass_transmissivity` or `trans_split` derives -- is a `rad_core.manifest.ProvenanceValue`, never a
bare `float`. A
bare float returned from a function that fills in an assumption is exactly the "substituted default
reported as a measured value" this whole project exists to refuse
(docs/tool-surface.md#provenance-a-default-is-never-a-measurement). Values this module only
*validates* (a caller's own reflectance/transmittance, already labelled by whoever supplied it) stay
plain floats in and out: this module did not choose them, so it has no provenance of its own to
attach, and attaching one anyway would invent a source it does not have.

**How advisories leave this layer**, the same split `rad_core.geometry` and `rad_core.grids` use:

- A **refusal** (`error` severity, the operation did not happen) is a raised exception.
  `MaterialsError` and its subclasses carry a class-attribute `code`, exactly the shape
  `rad_core.grids.GridError`/`rad_core.geometry.GeometryError` use.
- A **finding that does not stop the operation** (`info`/`warn`, the result is still usable) is
  `rad_core.manifest.ArtifactWarning`, reused rather than a second shape invented here.

**The LM-83 default table has exactly one entry point: `apply_defaults`.** There is no
`defaults="typical"` and no second table -- an unsourced default table is the confidently-wrong
threshold this project refuses to ship (docs/tool-surface.md#5-materials). Every citation in
`_LM83_SOURCE` below is what ch04 (skills/lighting-standards-compliance/chapters/
ch04-ies-lm-83-sda-ase.md) actually says, quoted where the chapter gives a clause number and flagged
where it does not -- see that dict's own comment and task-3-report.md's citation table for the
discrepancy this surfaced (docs/tool-surface.md's own illustrative example cites a clause ch04 does
not contain).
*(Corrected 2026-10-12, R862: the interior defaults are IES LM-83-12 §2.2.10, and `_LM83_SOURCE`
now cites it; see the note above that dict.)*

**Ground reflectance does not get a second home here.** `apply_defaults` takes exactly one argument,
`role_by_modifier` -- no ground-reflectance parameter of any shape. Ground is one more role in that
mapping, the same as floor, wall, ceiling, furniture, exterior vertical or trees; its 10 % value
(ch04 2.2.8) is fixed by the table like every other role and is never read from or written to the
project-level value `rad_core.manifest.resolve_ground_reflectance` resolves. That project value
reaches sky generators (`gensky -g`, `gendaymtx -g`); this table reaches one modelled polygon's
material. `AppliedDefaults.ground_modifiers` (and the general `modifiers_with_role`) exists so a
caller -- Task 4's `InputRecord.role` (ruling R9) and Task 7's cross-cutting schema test -- can ask
"which modifier did this call assign the ground role to" without either re-deriving that mapping or
this module inventing a channel for the project value to leak through
(docs/tool-surface.md#ground-reflectance-is-one-project-value-passed-to-every-sky-generator; check
1.10 asserts no tool signature outside `project` accepts a ground reflectance).
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Final

from rad_core.manifest import ArtifactWarning, Provenance, ProvenanceValue

__all__ = [
    "ENERGY_BALANCE_MAX",
    "FRACTION_MAX",
    "FRACTION_MIN",
    "GROUND_REFLECTANCE_AGREEMENT_TOLERANCE",
    "MODIFIER_IDENTIFIER_MAX_LENGTH",
    "MODIFIER_IDENTIFIER_PATTERN",
    "PLAUSIBLE_MAX",
    "PLAUSIBLE_MIN",
    "RESERVED_MODIFIER_IDENTIFIERS",
    "AppliedDefaults",
    "AssignmentValidation",
    "BSDFTypeRefusedError",
    "EnergyBalanceViolatedError",
    "InvalidRoleError",
    "LM83Default",
    "MaterialRole",
    "MaterialsError",
    "ReflectanceRangeError",
    "TransSplit",
    "TransmittanceRangeError",
    "apply_defaults",
    "check_energy_balance",
    "glass_transmissivity",
    "ground_reflectance_agrees",
    "modifier_identifier_problem",
    "reflectance_plausibility",
    "trans_split",
    "transmittance_plausibility",
    "validate_assignment",
    "validate_material_type",
    "validate_reflectance",
    "validate_transmittance",
]

#: A reflectance or transmittance is a unitless fraction of incident light -- it cannot be negative
#: or exceed 1 regardless of how implausible-but-legal the value is (see `PLAUSIBLE_MIN`/`_MAX` for
#: the softer judgement). `validate_reflectance`/`validate_transmittance` refuse outside this band.
FRACTION_MIN = 0.0
FRACTION_MAX = 1.0

#: `REFLECTANCE_IMPLAUSIBLE`'s own band (docs/tool-surface.md#5-materials, task-3-brief.md). Applied
#: to both reflectance and transmittance -- the register allocates one code for the plausibility
#: judgement, the same "one code for a whole class, `field` says which argument" reasoning
#: `rad_core.grids.PolygonError` and `rad_engine.project.ProjectNameError` already give for
#: `ARGUMENT_OUT_OF_RANGE` (see those classes' own docstrings).
PLAUSIBLE_MIN = 0.02
PLAUSIBLE_MAX = 0.95

#: rho + tau <= 1: docs/specs/materials.md's Trans row -- "Transmitted and diffusely reflected light
#: is modified by the material color", both drawn from the one incident flux a surface receives.
ENERGY_BALANCE_MAX = 1.0

#: How far the **modelled ground material** may sit from the **project's ground reflectance** before
#: the two are reported as disagreeing (`GROUND_REFLECTANCE_INCONSISTENT`). Sourced:
#: docs/tool-surface.md#ground-reflectance-is-one-project-value-passed-to-every-sky-generator states
#: it directly -- *"A project whose modelled ground material and project ground reflectance disagree
#: by more than 0.02 emits `GROUND_REFLECTANCE_INCONSISTENT` at `error`, naming both values."*
#:
#: **This is an agreement tolerance, not a physical threshold, and the distinction is worth stating**
#: because the two are recorded differently in this project. `PLAUSIBLE_MIN`/`PLAUSIBLE_MAX` are a
#: judgement about what a real surface measures; this is a slack band around an equality that ought
#: to hold exactly. The two numbers are the *same quantity in two places* -- Radiance sets ground
#: reflectance in three independent homes and none of them knows about the others (CONTEXT.md) --
#: so the only question is whether they were meant to be the same, and 0.02 is the width below which
#: a difference reads as rounding rather than as two different grounds. It is the same kind of
#: constant as `rad_core.grids._CELL_ASPECT_TOLERANCE` and `_GEOM_EPS`, one band wider, because what
#: it absorbs is a user typing 0.20 for a value stored as 0.2000 rather than float noise.
#:
#: It lives here, beside the other bands, and **not** in the layer that emits the advisory. Task 7b
#: shipped it in `rad_mcp.materials_tool` and reported it as a known layer violation; fix round 1
#: ruled it back down. `rad_mcp` carries no thresholds (CONTEXT.md, Layers) -- and a threshold in the
#: protocol layer is invisible to every reader who comes looking for this project's numbers where
#: all the others are.
GROUND_REFLECTANCE_AGREEMENT_TOLERANCE = 0.02

#: Float slack around the band boundary above -- **not** a relaxation of it, exactly as
#: `rad_core.grids._CELL_ASPECT_TOLERANCE` is slack around the cell-aspect boundary rather than a
#: softer rule. Two reflectances a caller means to be 0.02 apart are frequently not: `0.10` and
#: `0.08` differ by `0.020000000000000004` in binary floating point, so the spec's *"more than
#: 0.02"* would report a pair the user wrote as exactly two hundredths apart as a disagreement. The
#: comparison is over values a human typed as short decimals, which is where this representation gap
#: actually bites.
_AGREEMENT_EPS = 1e-9


# --------------------------------------------------------------------------------------------
# Modifier identifier -- the one rule `rad_engine.materials` and `rad_core.obj` both refuse names by
# --------------------------------------------------------------------------------------------

#: How long a modifier identifier may be. Radiance reads a primitive's tokens into a fixed
#: `MAXSTR`-sized buffer; measured on this machine's Radiance 6.1a, a 300-character identifier
#: compiles and a 600-character one kills `oconv` outright (the process died on a signal, with no
#: diagnostic at all). 128 is generous for a name whose whole job is to be referenced by geometry,
#: and it stays far clear of the boundary rather than tuned to it -- the same posture
#: `rad_engine.project._COMPONENT`'s 64-character bound takes for a directory name.
MODIFIER_IDENTIFIER_MAX_LENGTH: Final = 128

#: What this module will write as a primitive's identifier.
#:
#: docs/specs/artificial-lighting.md:59 states the one rule the format itself gives -- "This
#: identifier must not contain whitespace" -- and that rule alone is not enough, because the
#: characters Radiance's *lexer* gives meaning to are not documented there and were measured instead
#: (Radiance 6.1a, `oconv` on a one-primitive file, see task-6-report.md fix round 1):
#:
#: - `"my mat"` -- a plausible CAD layer name -- `oconv: fatal - bad arguments for plastic "my"`;
#:   `"a\tb"` and `""` likewise. Loud, but it names a token the user never typed.
#: - `"good\n0\n0\n5 .2 .2 .2 0 0\nvoid plastic evil"` -- **`oconv` exits 0** having defined two
#:   modifiers, `good` and `evil`, neither of which `MaterialsFile.modifiers` claims. That set is
#:   documented as "exactly what a caller should pass on as `rad_core.geometry.inventory`'s
#:   `known_modifiers`", so `UNDEFINED_MODIFIER` would then be gating on a scene the octree does not
#:   contain. This is the case that must not survive, and it is the reason this is a whitelist.
#: - `"#wall"` -- **`oconv` exits 0** too, and defines *nothing*: a leading `#` comments the whole
#:   line out, so the primitive silently vanishes and its argument lines are re-read as a primitive
#:   of their own. The second silent exit-0.
#: - `'wa"ll'` and `"wa'll"` -- a leading-or-embedded quote puts Radiance's `fgetword` into quoted
#:   mode and swallows the rest of the primitive; `oconv: fatal`, with the whole primitive named as
#:   one token.
#: - `"void"` -- `oconv: fatal - illegal modifier name "void"`. Radiance's own reserved word.
#:
#: So: letters, digits, `_`, `.`, `+` and `-`, with the first character restricted to
#: `[A-Za-z0-9_]` -- the character class is what keeps whitespace, quotes, `#` and `!` out
#: everywhere, and the anchored start additionally keeps `.` and `-` out of the one position where a
#: name is read as something other than a name (a leading `-` reads as an option to more than one
#: Radiance tool; a leading `.` is the hidden-file convention `rad_engine.project._COMPONENT` refuses
#: for the same reason).
#:
#: Characters that are merely *legal* and exotic are refused too, and deliberately: `oconv` accepts
#: `:`, `/`, `$`, `(`, non-ASCII, and even a mid-word `#` (`wall#mat` compiles and resolves), and
#: none of them is admitted here. That is the trade this rule makes on purpose -- refusing a legal
#: name costs a caller one rename and the message says exactly what to change, while accepting an
#: injecting one costs a study its octree with nothing reported anywhere.
MODIFIER_IDENTIFIER_PATTERN: Final = re.compile(
    rf"[A-Za-z0-9_][A-Za-z0-9_.+-]{{0,{MODIFIER_IDENTIFIER_MAX_LENGTH - 1}}}\Z"
)

#: Radiance's own reserved identifier -- `oconv: fatal - illegal modifier name "void"` (measured).
RESERVED_MODIFIER_IDENTIFIERS: Final = frozenset({"void"})


def modifier_identifier_problem(identifier: str) -> str | None:
    """Why `identifier` cannot be one plain Radiance identifier, or `None` when it can.

    The one rule, pure, so `rad_core.obj` can refuse an OBJ name before any converter runs and
    `rad_engine.materials.validate_modifier_identifier` can refuse a materials name, from one copy.
    """
    if identifier in RESERVED_MODIFIER_IDENTIFIERS:
        return "it is Radiance's reserved word `void`"
    if not MODIFIER_IDENTIFIER_PATTERN.match(identifier or ""):
        return (
            f"it must be 1-{MODIFIER_IDENTIFIER_MAX_LENGTH} characters of letters, digits, '_', '.', "
            "'+' or '-', starting with a letter, digit or '_'"
        )
    return None


# --------------------------------------------------------------------------------------------
# Refusals
# --------------------------------------------------------------------------------------------


class MaterialsError(ValueError):
    """Something this module refuses to validate or convert.

    Mirrors `rad_core.grids.GridError`/`rad_core.geometry.GeometryError`'s shape: a class-attribute
    `code`, read by whichever layer eventually builds the wire envelope -- this layer only needs to
    carry *which* allocated code applies.
    """

    code: str | None = None
    severity: str = "error"
    field: str | None = None

    def __init__(self, message: str, *, code: str | None = None, field: str | None = None) -> None:
        super().__init__(message)
        if code is not None:
            self.code = code
        if field is not None:
            self.field = field


class ReflectanceRangeError(MaterialsError):
    """A reflectance outside [0, 1] -- not a plausibility judgement (`REFLECTANCE_IMPLAUSIBLE`,
    warn), a physical impossibility: Radiance's `plastic`/`metal`/`trans` reflectance argument is a
    unitless fraction of incident light and cannot be negative or exceed the light that arrived.

    `ARGUMENT_OUT_OF_RANGE` rather than a dedicated code -- ruling R14's own reasoning
    (docs/tool-surface.md#advisory-code-register) is "an argument rejected on its face, before any
    work was attempted", and `field` is what keeps one code actionable across many arguments, the
    same choice `rad_core.grids.PolygonError` and `rad_engine.project.ProjectNameError` already made
    for their own out-of-range refusals.
    """

    code = "ARGUMENT_OUT_OF_RANGE"
    field = "reflectance"


class TransmittanceRangeError(MaterialsError):
    """A transmittance outside [0, 1]. See `ReflectanceRangeError` -- same reasoning, the other
    field `ARGUMENT_OUT_OF_RANGE`'s `field` key distinguishes.

    Also raised by `glass_transmissivity` when the *converted* transmissivity it computed falls
    outside [0, 1] -- `field` stays `"transmittance"` because the actionable fix is still "supply a
    different transmittance"; see that function's docstring for why the input band alone does not
    guarantee this (code review finding I1, task-3-report.md fix round 1).
    """

    code = "ARGUMENT_OUT_OF_RANGE"
    field = "transmittance"


class EnergyBalanceViolatedError(MaterialsError):
    """rho + tau > 1: more light leaving a surface than arrived at it.

    `field` is left `None`, the same reasoning `rad_core.geometry.UndefinedModifierError` gives for
    its own mismatch refusal: neither `reflectance` nor `transmittance` is individually wrong here --
    it is their sum.
    """

    code = "ENERGY_BALANCE_VIOLATED"


class BSDFTypeRefusedError(MaterialsError):
    """`type="BSDF"` (any spelling) through this tool.

    BSDF binding is a `window_group` concern with its own validation (docs/tool-surface.md
    #5-materials): "two places to set it is two places for it to disagree." `ARGUMENT_OUT_OF_RANGE`
    covers this too -- ruling R14's other half is exactly "supplied with a value the tool will not
    accept".
    """

    code = "ARGUMENT_OUT_OF_RANGE"
    field = "type"


class InvalidRoleError(MaterialsError):
    """A role value that is not a `MaterialRole` member.

    `MaterialRole` subclasses `str` (module below), so a plain string equal to a role's value --
    `"ground"` for `MaterialRole.GROUND` -- compares equal and hashes the same, and would otherwise
    be accepted by `apply_defaults`'s dict lookup *silently*, write the correct reflectance and
    citation by that coincidence, and then fail identity comparison (`is`) on the read side
    (`AppliedDefaults.ground_modifiers`/`modifiers_with_role`), reporting the modifier as if it had
    no role at all. Refused here instead of accepted by luck (code review finding Minor 4,
    task-3-report.md fix round 1) -- consistent with this module's own rule that nothing gets to work
    by coincidence rather than by contract.

    `field` defaults to `role_by_modifier`, `apply_defaults`'s own argument -- `validate_assignment`
    raises this same class for its own `role` argument (ruling R25) with `field="role"` passed to the
    constructor, the identical guard reused rather than duplicated for a one-word difference in what
    it is about.
    """

    code = "ARGUMENT_OUT_OF_RANGE"
    field = "role_by_modifier"


# --------------------------------------------------------------------------------------------
# Range, plausibility, and energy balance
# --------------------------------------------------------------------------------------------


def validate_reflectance(value: float) -> None:
    """Refuse a reflectance outside [`FRACTION_MIN`, `FRACTION_MAX`]. See `ReflectanceRangeError`."""
    if not (FRACTION_MIN <= value <= FRACTION_MAX):
        raise ReflectanceRangeError(
            f"reflectance must be between {FRACTION_MIN:g} and {FRACTION_MAX:g} (a fraction of "
            f"incident light), got {value!r}."
        )


def validate_transmittance(value: float) -> None:
    """Refuse a transmittance outside [`FRACTION_MIN`, `FRACTION_MAX`]. See
    `TransmittanceRangeError`."""
    if not (FRACTION_MIN <= value <= FRACTION_MAX):
        raise TransmittanceRangeError(
            f"transmittance must be between {FRACTION_MIN:g} and {FRACTION_MAX:g} (a fraction of "
            f"incident light), got {value!r}."
        )


def _plausibility_warning(value: float, *, field: str) -> ArtifactWarning | None:
    if PLAUSIBLE_MIN <= value <= PLAUSIBLE_MAX:
        return None
    return ArtifactWarning(
        code="REFLECTANCE_IMPLAUSIBLE",
        severity="warn",
        field=field,
        message=(
            f"{field} {value:g} is outside the {PLAUSIBLE_MIN:g}-{PLAUSIBLE_MAX:g} band a real "
            "surface plausibly measures in -- verify this was not a units or percentage-vs-fraction "
            "mistake (e.g. 50 typed for 0.50)."
        ),
    )


def reflectance_plausibility(value: float) -> ArtifactWarning | None:
    """`REFLECTANCE_IMPLAUSIBLE` (warn) outside 0.02-0.95 -- legal per `validate_reflectance` but a
    real surface essentially never measures at the extremes; usually a units or
    percentage-vs-fraction mistake (50 typed for 0.50)."""
    return _plausibility_warning(value, field="reflectance")


def transmittance_plausibility(value: float) -> ArtifactWarning | None:
    """`REFLECTANCE_IMPLAUSIBLE` (warn) outside 0.02-0.95, applied to a transmittance. One code
    covers both fields -- see `PLAUSIBLE_MIN`/`PLAUSIBLE_MAX`'s own comment."""
    return _plausibility_warning(value, field="transmittance")


def check_energy_balance(reflectance: float, transmittance: float) -> None:
    """Refuse (`EnergyBalanceViolatedError`) rho + tau > `ENERGY_BALANCE_MAX`."""
    total = reflectance + transmittance
    if total > ENERGY_BALANCE_MAX:
        raise EnergyBalanceViolatedError(
            f"reflectance {reflectance!r} + transmittance {transmittance!r} = {total!r} exceeds "
            f"{ENERGY_BALANCE_MAX:g} -- more light leaving this surface than arrives at it."
        )


def ground_reflectance_agrees(modelled: float, project_value: float) -> bool:
    """Whether a modelled ground material and the project's ground reflectance are the same value.

    The comparison `GROUND_REFLECTANCE_INCONSISTENT` is built on, and the reason it is a function
    rather than an inlined `abs(a - b) <= 0.02` at the call site: the band is
    `GROUND_REFLECTANCE_AGREEMENT_TOLERANCE` and belongs in this module with every other band this
    project holds, so the layer that *reports* the finding consumes an answer rather than computing
    one (CONTEXT.md, Layers -- `rad_mcp` is argument plumbing and the wire contract, and carries no
    thresholds).

    Pure and symmetric; it makes no claim about which of the two values is right. Deciding what to do
    about a disagreement -- refuse, or report it on a successful result -- is the caller's, and turns
    on whether anyone ever *chose* the project value
    (docs/tool-surface.md#ground-reflectance-is-one-project-value-passed-to-every-sky-generator).
    """
    return (
        abs(modelled - project_value)
        <= GROUND_REFLECTANCE_AGREEMENT_TOLERANCE + _AGREEMENT_EPS
    )


def validate_material_type(material_type: str) -> None:
    """Refuse `type="BSDF"` (case-insensitive, surrounding whitespace ignored). See
    `BSDFTypeRefusedError`."""
    if material_type.strip().casefold() == "bsdf":
        raise BSDFTypeRefusedError(
            f'materials refuses type={material_type!r} -- BSDF binding is a window_group concern '
            '(docs/tool-surface.md#5-materials); use window_group(action="assign_bsdf") instead.'
        )


@dataclass(frozen=True)
class AssignmentValidation:
    """`validate_assignment`'s result: findings that don't stop the operation, plus whichever
    type-specific arithmetic this module owns for the `material_type` it was given.

    `glass_transmissivity` is `None` for every type but `glass`, or when `type == "glass"` but no
    `transmittance` was given (nothing to convert). It is never a bare `float` for the same reason
    `LM83Default.reflectance` is not one -- see the module docstring's provenance guarantee.

    `trans_split` is its exact counterpart for `trans` (ruling R30) and is `None` for every other
    type, or when either fraction was not given. Both exist so that `rad_engine.materials` emits a
    primitive out of numbers **this** module computed: one arithmetic path per primitive, consumed
    rather than re-derived one layer up, which is the discipline the glass conversion's own fix
    round established and the trans split's fix round repeated at four to ten times the magnitude.
    """

    warnings: tuple[ArtifactWarning, ...]
    glass_transmissivity: ProvenanceValue | None = None
    role: MaterialRole | None = None
    trans_split: TransSplit | None = None
    """Carried straight through from `validate_assignment`'s own `role` argument -- ruling R25's
    channel for a ground (or any LM-83 role) assigned manually through `materials(action="set")`
    rather than through `apply_defaults`. `None` when no role was given; this module does not act on
    it -- no tool argument is added here, no `InputRecord` is written -- that is Task 7's wire
    plumbing and `rad_engine.project.register_input`'s job, which now has the same `role` channel
    (ruling R9)."""


def validate_assignment(
    *,
    material_type: str,
    reflectance: float | None = None,
    transmittance: float | None = None,
    provenance: Provenance = "assumed",
    source: str | None = None,
    role: MaterialRole | None = None,
) -> AssignmentValidation:
    """Validate one modifier's optical properties end to end: refuses `type="BSDF"`
    (`validate_material_type`), refuses either fraction outside [0, 1] (`validate_reflectance`/
    `validate_transmittance`), refuses `reflectance + transmittance > 1` (`check_energy_balance`),
    returns whichever `REFLECTANCE_IMPLAUSIBLE` warnings apply, and -- **when `material_type` is
    `"glass"` and `transmittance` was given** -- converts it via `glass_transmissivity` and returns
    the converted `ProvenanceValue` alongside its `GLASS_TRANSMISSIVITY_NOTE`.

    **When `material_type` is `"trans"` and both fractions were given**, it likewise returns
    `trans_split`'s `AssignmentValidation.trans_split` (ruling R30) -- the `trans` primitive's colour
    and transmissivity arguments, which are neither of the two numbers passed in. Same reason as the
    glass conversion, and the same consequence if a caller reaches for the raw pair instead: the
    emitted material delivers neither the reflectance nor the transmittance it was given (measured
    at -30% to -70% and -50% to -80% respectively, task-6-report.md fix round 1).

    The one entry point `rad_engine.materials`/Task 6 needs per `assignments[modifier]` from the
    `materials(action="set")` tool signature (docs/tool-surface.md#5-materials: `{type,
    reflectance|transmittance, specular, roughness, provenance}`). This function's own docstring used
    to describe itself that way while skipping the one conversion this module exists to own -- a
    `materials(action="set", assignments={"glazing_mat": {"type": "glass", "transmittance": 0.74}})`
    call is precisely the shape Task 6 receives from the tool signature above, so validation that
    does not also convert leaves the caller to reach for `glass_transmissivity` separately and by
    memory, which is exactly the kind of silent gap this project does not ship (code review finding
    I2, task-3-report.md fix round 1: measured at n=1.52, an unconverted 0.74 written straight into
    `glass` transmits 0.679 instead of 0.740, -8.2% on every ray through the window).

    `provenance`/`source` are forwarded to `glass_transmissivity` unchanged when a glass conversion
    runs; they are otherwise unused (a non-glass `reflectance`/`transmittance` is not rewrapped in a
    `ProvenanceValue` here -- see the module docstring on why this module only attaches provenance to
    values it computed itself).

    `specular`/`roughness` are not validated here -- out of this task's stated scope
    ("Reflectance/transmittance range validation, energy balance", task-3-brief.md); see
    task-3-report.md.

    `role`, where given, must be a genuine `MaterialRole` member -- refused (`InvalidRoleError`,
    `field="role"`) otherwise, the identical trap `apply_defaults`'s own `role_by_modifier` check
    closes and for the identical reason (`MaterialRole` subclasses `str`, so a plain string equal to
    a role's value would otherwise be accepted and then compared `is`-wrong by a reader downstream --
    see `InvalidRoleError`). Validated and carried straight through onto
    `AssignmentValidation.role`; this function does not otherwise act on it (ruling R25 -- see that
    field's own docstring).
    """
    validate_material_type(material_type)
    if role is not None and not isinstance(role, MaterialRole):
        raise InvalidRoleError(
            f"role={role!r} is not a MaterialRole member (got {type(role).__name__}). Pass one of "
            f"{[r.value for r in MaterialRole]!r}.",
            field="role",
        )
    kind = material_type.strip().casefold()
    is_glass = kind == "glass"
    is_trans = kind == "trans"

    warnings: list[ArtifactWarning] = []
    if reflectance is not None:
        validate_reflectance(reflectance)
        warning = reflectance_plausibility(reflectance)
        if warning is not None:
            warnings.append(warning)

    glass_value: ProvenanceValue | None = None
    if transmittance is not None:
        validate_transmittance(transmittance)
        warning = transmittance_plausibility(transmittance)
        if warning is not None:
            warnings.append(warning)
        if is_glass:
            glass_value, note = glass_transmissivity(transmittance, provenance=provenance, source=source)
            warnings.append(note)

    if reflectance is not None and transmittance is not None:
        check_energy_balance(reflectance, transmittance)

    split: TransSplit | None = None
    if is_trans and reflectance is not None and transmittance is not None:
        split = trans_split(reflectance, transmittance, provenance=provenance, source=source)

    return AssignmentValidation(
        warnings=tuple(warnings),
        glass_transmissivity=glass_value,
        role=role,
        trans_split=split,
    )


# --------------------------------------------------------------------------------------------
# Glass: transmittance -> transmissivity
# --------------------------------------------------------------------------------------------

#: Radiance `glass` primitive coefficients (docs/specs/materials.md's Glass row, the Radiance
#: reference manual verbatim): "To compute transmissivity (tn) from transmittance (Tn) use:
#: tn = (sqrt(.8402528435+.0072522239*Tn*Tn)-.9166530661)/.0036261119/Tn". Verified against the same
#: source's own worked example -- "Standard 88% transmittance glass has a transmissivity of 0.96" --
#: see task-3-report.md's numeric check (0.88 -> 0.9584, which the source states to two significant
#: figures as 0.96).
_GLASS_A = 0.8402528435
_GLASS_B = 0.0072522239
_GLASS_C = 0.9166530661
_GLASS_D = 0.0036261119


def glass_transmissivity(
    transmittance: float,
    *,
    provenance: Provenance = "assumed",
    source: str | None = None,
) -> tuple[ProvenanceValue, ArtifactWarning]:
    """Convert a glazing's visible transmittance (what a spec sheet states, what a user types) to
    the transmissivity Radiance's `glass` primitive actually takes.

    docs/specs/materials.md: "Transmissivity is the amount of light not absorbed in one traversal of
    the material. Transmittance -- the value usually measured -- is the total light transmitted
    through the pane including multiple reflections." The two are different quantities by
    construction, not by approximation, so a user who types 0.74 (their glazing's visible
    transmittance) has typed a slightly different number than the one `glass` needs
    (`GLASS_TRANSMISSIVITY_NOTE`, info, docs/tool-surface.md#5-materials) -- this function owns that
    arithmetic; emitting the `glass` primitive with the converted value is `rad_engine.materials`/
    Task 6's job (module docstring).

    `provenance`/`source` describe the *input* `transmittance` (default `assumed`, matching
    `rad_core.manifest.resolve_ground_reflectance`'s own default: "user gave it, unmeasured"), and
    are carried onto the returned `ProvenanceValue` unchanged -- converting units does not change how
    confident anyone is in the number, so relabelling it here would manufacture a confidence change
    out of an arithmetic step.

    Refuses (`TransmittanceRangeError`) a transmittance outside (0, 1]: the formula divides by `Tn`,
    so 0 has no finite image, and a transmittance above 1 was already refused by
    `validate_transmittance` before the conversion runs.

    **Also refuses (`TransmittanceRangeError`) when the *converted* transmissivity itself falls
    outside [0, 1], even though the input passed validation.** `sqrt(A + B*Tn^2) - C` is a
    near-cancellation for `Tn` close to 1 (`C == sqrt(A)` to full precision, so the subtrahend is
    almost the whole square root): `tn` crosses 1.0 at `Tn` ~= 0.918318 -- *inside* the
    `PLAUSIBLE_MIN`-`PLAUSIBLE_MAX` band, so no plausibility warning fires either -- and crosses 0
    (goes negative) below `Tn` ~= 1.11728e-4. Both are unreachable-in-practice inputs for real glass
    (float glass tops out well under 0.92) but not refused by `validate_transmittance` alone, and a
    `tn` above 1 or below 0 is not a transmissivity Radiance's `glass` primitive can represent --
    reporting it anyway is exactly the "plausible provenance on a wrong number" failure mode this
    project exists to prevent (code review finding I1, task-3-report.md fix round 1, boundary values
    measured there).
    """
    validate_transmittance(transmittance)
    if transmittance == 0.0:
        raise TransmittanceRangeError(
            "transmittance must be > 0 to convert to a glass transmissivity -- the Radiance formula "
            "divides by it (docs/specs/materials.md)."
        )

    tn = (math.sqrt(_GLASS_A + _GLASS_B * transmittance * transmittance) - _GLASS_C) / _GLASS_D / (
        transmittance
    )

    if not (FRACTION_MIN <= tn <= FRACTION_MAX):
        raise TransmittanceRangeError(
            f"transmittance {transmittance:g} converts to a Radiance glass transmissivity of "
            f"{tn!r}, outside [{FRACTION_MIN:g}, {FRACTION_MAX:g}] -- not a value the `glass` "
            "primitive can represent. This transmittance is outside the range real glazing "
            "products are measured in; verify it before retrying."
        )

    converted = ProvenanceValue(
        value=tn,
        provenance=provenance,
        source=source,
        note=(
            f"glass transmissivity converted from visible transmittance {transmittance:g} "
            "(docs/specs/materials.md)"
        ),
    )
    note = ArtifactWarning(
        code="GLASS_TRANSMISSIVITY_NOTE",
        severity="info",
        message=(
            f"visible transmittance {transmittance:g} converted to Radiance glass transmissivity "
            f"{tn:.4f} -- glass takes the light not absorbed in one traversal, not the total "
            "transmitted through multiple internal reflections (docs/specs/materials.md)."
        ),
    )
    return converted, note


# --------------------------------------------------------------------------------------------
# Trans: reflectance + transmittance -> the primitive's colour and its transmissivity (ruling R30)
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class TransSplit:
    """`trans_split`'s result: the two numbers Radiance's `trans` model actually takes.

    `colour` is the primitive's **combined diffuse fraction** (its `red green blue` arguments, which
    are one value here for the reason `rad_engine.materials.render_plastic` gives: no tool signature
    in this project exposes a per-channel reflectance); `transmissivity` is the argument the
    reference manual calls `trans` -- "the fraction of **penetrating** light that travels all the way
    through the material" (docs/specs/materials.md's Trans row). Neither is the caller's reflectance
    and neither is the caller's transmittance, which is exactly why they are named for what the
    primitive calls them and returned as their own type rather than handed back as a plain pair of
    floats a reader could put in either slot.

    `ProvenanceValue`, not bare floats, for the reason the module docstring gives: these are numbers
    *this* module computed, so they carry where they came from -- the same treatment
    `glass_transmissivity`'s converted value gets.
    """

    colour: ProvenanceValue
    transmissivity: ProvenanceValue


def trans_split(
    reflectance: float,
    transmittance: float,
    *,
    provenance: Provenance = "assumed",
    source: str | None = None,
) -> TransSplit:
    """Split a diffuse reflectance and a diffuse transmittance into the colour and transmissivity
    Radiance's `trans` primitive takes (ruling R30).

    **The `trans` primitive does not take a reflectance and a transmittance.** docs/specs/materials.md's
    Trans row: "The transmissivity is the fraction of **penetrating** light that travels all the way
    through the material ... Transmitted and diffusely reflected light is modified by the material
    color." So the colour arguments carry the *combined* diffuse fraction and the transmissivity
    *splits* it -- Radiance delivers

        rho = colour * (1 - specular) * (1 - transmissivity)
        tau = colour * (1 - specular) * transmissivity

    and writing a caller's reflectance into the colour and their transmittance into the
    transmissivity therefore delivers neither. Inverting the pair above at `specular = 0` gives this
    function: `colour = rho + tau`, `transmissivity = tau / colour`.

    Measured against Radiance 6.1a (`oconv` + `rtrace -ab 0`, normalised against a
    `plastic` reflectance-1.0 control, see task-6-report.md fix round 1 for the rig): asking for
    rho=0.4 tau=0.3 the unsplit mapping delivers 0.280/0.120, and this one delivers 0.400/0.300;
    likewise 0.5/0.4 -> 0.300/0.200 vs 0.500/0.400 and 0.2/0.7 -> 0.060/0.140 vs 0.200/0.700. This is
    the same defect class, four to ten times larger, as the glass transmittance/transmissivity error
    `glass_transmissivity` above exists to prevent -- and it is owned here for the same reason: the
    arithmetic is physics, and physics lives in `rad_core` (CONTEXT.md's layer rule). Emitting the
    primitive from these two numbers is `rad_engine.materials`'s job.

    **`specular` is not an argument, and the split is deliberately defined at `specular = 0`.**
    Radiance scales *both* delivered fractions by the same `(1 - specular)` factor it already scales
    a `plastic`'s reflectance by, so compensating for it here would make `trans` disagree with
    `plastic` about what a specular fraction means. Measured on the same rig at specular=0.08: the
    ratio of the delivered diffuse term to its specular=0 value is 0.9063961 for a `plastic`'s
    reflectance, 0.9063963 for this split's rho and 0.9063961 for its tau -- one factor, applied
    alike. The consequence worth having is that `tau = 0` makes `colour = rho` and
    `transmissivity = 0`, so a `trans` emitted from this split is **bit-identical** to the `plastic`
    the same reflectance/specular/roughness would have produced (verified on the same install: both
    primitives return the identical `rtrace` radiance to every digit).

    **`reflectance + transmittance == 0` returns `colour = 0`, `transmissivity = 0`, and does not
    divide.** It is not a refusal, because the material is representable and the answer is exact: at
    `colour = 0` both delivered fractions are zero for *every* transmissivity, so the split is
    undefined but immaterial, and 0 is chosen as the one value that also keeps the
    `tau = 0 -> plastic` identity above (a perfect absorber emitted as `trans` is the perfect
    absorber `plastic` would have been). Confirmed on the same install: `7 0 0 0 0 0 0 0` measures
    rho=0.0000 tau=0.0000. Refusing instead would refuse a legal, in-range assignment for the sake of
    a division this function does not need to perform -- and a caller who typed two zeros is already
    told twice, by `REFLECTANCE_IMPLAUSIBLE` on each field (both are below `PLAUSIBLE_MIN`).

    Refuses what the arithmetic cannot produce, through this module's own existing checks rather
    than a second copy of them: either fraction outside [0, 1] (`validate_reflectance` /
    `validate_transmittance`), and `rho + tau > 1` (`check_energy_balance`) -- which is precisely the
    condition under which `colour` would exceed `FRACTION_MAX` and stop being a colour the primitive
    can represent. Inside those bounds `colour` is in [0, 1] and `transmissivity = tau / colour` is
    in [0, 1] by construction, so there is no third, separately-reachable refusal to write.

    `provenance`/`source` describe the *inputs* and are carried onto both returned values unchanged,
    for the reason `glass_transmissivity` gives: rearranging two numbers into the form a primitive
    takes does not change how confident anyone is in them.
    """
    validate_reflectance(reflectance)
    validate_transmittance(transmittance)
    check_energy_balance(reflectance, transmittance)

    colour = reflectance + transmittance
    transmissivity = 0.0 if colour == 0.0 else transmittance / colour

    note = (
        f"Radiance trans split of reflectance {reflectance:g} / transmittance {transmittance:g} "
        "into the primitive's combined diffuse colour and its transmissivity "
        "(docs/specs/materials.md, ruling R30)"
    )
    return TransSplit(
        colour=ProvenanceValue(value=colour, provenance=provenance, source=source, note=note),
        transmissivity=ProvenanceValue(
            value=transmissivity, provenance=provenance, source=source, note=note
        ),
    )


# --------------------------------------------------------------------------------------------
# The LM-83 default table
# --------------------------------------------------------------------------------------------


class MaterialRole(str, Enum):
    """Which LM-83 default-table row a modifier plays.

    ch04's "Default model assumptions" (skills/lighting-standards-compliance/chapters/
    ch04-ies-lm-83-sda-ase.md) names exactly these seven; there is no eighth role and no
    `role="other"` fallback -- a modifier this table has no default for is a modifier
    `apply_defaults` was not asked about (see that function's docstring).

    `GROUND` is the one ruling R9 (Task 4, `InputRecord.role`) and Task 7's schema test both need to
    recover from an `AppliedDefaults` without re-deriving "which polygon is ground" -- see
    `AppliedDefaults.ground_modifiers`.
    """

    FLOOR = "floor"
    WALL = "wall"
    CEILING = "ceiling"
    FURNITURE = "furniture"
    GROUND = "ground"
    EXTERIOR_VERTICAL = "exterior_vertical"
    TREES = "trees"


#: ch04's own values (skills/lighting-standards-compliance/chapters/ch04-ies-lm-83-sda-ase.md,
#: "Default model assumptions"), unchanged from task-3-brief.md's list -- all seven matched the
#: chapter exactly (task-3-report.md's citation table records the check). No `"typical"` table
#: exists alongside this one (module docstring) and none is added here.
_LM83_REFLECTANCE: dict[MaterialRole, float] = {
    MaterialRole.FLOOR: 0.20,
    MaterialRole.WALL: 0.50,
    MaterialRole.CEILING: 0.70,
    MaterialRole.FURNITURE: 0.50,
    MaterialRole.GROUND: 0.10,
    MaterialRole.EXTERIOR_VERTICAL: 0.30,
    MaterialRole.TREES: 0.20,
}

#: Cited to what ch04 actually states, clause by clause, not to the file alone (task-3-brief.md
#: instruction). The chapter numbers its "Exterior (2.2.8)" line but *not* its "Interior
#: reflectances" line -- no clause number precedes floor/wall/ceiling/furniture anywhere in the
#: source, only the section heading "Default model assumptions" (ch04's own editorial heading, not an
#: IES LM-83-12 clause -- the four interior citations below say so explicitly, and identically to one
#: another, rather than only on the entry a reviewer happened to check (code review finding Minor 2,
#: task-3-report.md fix round 1)). docs/tool-surface.md's own illustrative JSON example *used to*
#: cite "LM-83-12 §2.2.9" for a 0.20 reflectance, and "2.2.9" appears nowhere in ch04 --
#: task-3-report.md flagged it as an unverified citation in the spec, ruling R23 (2026-08-01) settled
#: that it is not established by this corpus, and Task 7a landed the correction: that example now
#: cites the ground 10 % / §2.2.8 pair, which ch04 does clause-source. Nothing here changed; this
#: note is past-tense so it is not read as a live discrepancy that still needs fixing.
#:
#: *(Corrected 2026-10-12, Phase 25 Branch B Task 5 fix round 1, R862: the four interior strings
#: said "(ch04's wording, not a numbered IES LM-83-12 clause -- see task-3-report.md)". Both halves
#: were false on the wire: the interior defaults are IES LM-83-12 §2.2.10, "Interior Surface
#: Reflectances" (printed page 8; the four values head page 9), read on the standard itself, and
#: task-3-report.md is a working file no user has. The strings now cite §2.2.10 and ch04's heading.
#: The note above is right that ch04 numbers no clause for these; it was the standard that did.
#: The source is not recorded in the manifest (only the call's role assignments are, under
#: `registered_by.arguments`); every read re-derives it through `replay_materials`, so projects
#: written before this change report §2.2.10 too, and no digest changes (measured 2026-10-12:
#: manifest.json and lm83_defaults.rad byte-identical across old-string and new-string applies).
#: Furniture's 50 % is §2.2.10's list; §2.2.11 "Furniture and Partitions" (printed page 9) is the
#: furniture modelling clause ch04 numbers, not this default.)*
_LM83_SOURCE: dict[MaterialRole, str] = {
    MaterialRole.FLOOR: (
        'IES LM-83-12 §2.2.10, "Interior Surface Reflectances": "If actual reflectance values are '
        'not known, use the following default reflectance values" (ch04-ies-lm-83-sda-ase.md, '
        '"Default model assumptions -- Interior reflectances (when unknown)"): floor 20%.'
    ),
    MaterialRole.WALL: (
        'IES LM-83-12 §2.2.10, "Interior Surface Reflectances": "If actual reflectance values are '
        'not known, use the following default reflectance values" (ch04-ies-lm-83-sda-ase.md, '
        '"Default model assumptions -- Interior reflectances (when unknown)"): walls 50%.'
    ),
    MaterialRole.CEILING: (
        'IES LM-83-12 §2.2.10, "Interior Surface Reflectances": "If actual reflectance values are '
        'not known, use the following default reflectance values" (ch04-ies-lm-83-sda-ase.md, '
        '"Default model assumptions -- Interior reflectances (when unknown)"): ceiling 70%.'
    ),
    MaterialRole.FURNITURE: (
        'IES LM-83-12 §2.2.10, "Interior Surface Reflectances": "If actual reflectance values are '
        'not known, use the following default reflectance values" (ch04-ies-lm-83-sda-ase.md, '
        '"Default model assumptions -- Interior reflectances (when unknown)"): furniture 50%.'
    ),
    MaterialRole.GROUND: 'IES LM-83-12 §2.2.8 (ch04-ies-lm-83-sda-ase.md "Exterior"): "ground 10%."',
    MaterialRole.EXTERIOR_VERTICAL: (
        'IES LM-83-12 §2.2.8 (ch04-ies-lm-83-sda-ase.md "Exterior"): "Unknown vertical surface '
        'reflectance 30%."'
    ),
    MaterialRole.TREES: (
        'IES LM-83-12 §2.2.8 (ch04-ies-lm-83-sda-ase.md "Exterior"): "trees as cones/spheres/'
        'cylinders at 20% reflective."'
    ),
}


@dataclass(frozen=True)
class LM83Default:
    """One modifier's LM-83 default reflectance, as `apply_defaults` wrote it."""

    modifier: str
    role: MaterialRole
    reflectance: ProvenanceValue

    def __post_init__(self) -> None:
        """Structural half of the provenance guarantee (code review finding Minor 1,
        task-3-report.md fix round 1): `reflectance` being a bare float used to be caught only by
        the test suite (`TestApplyDefaultsProvenanceGuaranteeIsNonVacuous`), which meant a caller
        constructing `LM83Default` directly -- inside or outside this module -- could still build the
        exact object the provenance rule forbids. `TypeError` rather than a `MaterialsError`
        subclass: this is a programming error in how the dataclass was built, not a refusal of a
        client-supplied argument at the module's own boundary (`apply_defaults` itself never permits
        this to happen -- see the loop there)."""
        if not isinstance(self.reflectance, ProvenanceValue):
            raise TypeError(
                f"LM83Default.reflectance must be a ProvenanceValue, got "
                f"{type(self.reflectance).__name__} -- a bare float here would be a default reported "
                "as if it were a measurement (rad_core.manifest's own provenance rule)."
            )


@dataclass(frozen=True)
class AppliedDefaults:
    """`apply_defaults`'s return: what it wrote, and the one warning that applies whenever it wrote
    anything."""

    defaults: tuple[LM83Default, ...]
    warnings: tuple[ArtifactWarning, ...]

    def modifiers_with_role(self, role: MaterialRole) -> frozenset[str]:
        """Every modifier this call assigned `role` to. The general form of `ground_modifiers`.

        Compares with `==`, not `is` -- `MaterialRole` subclasses `str`, and `apply_defaults` refuses
        (`InvalidRoleError`) any non-`MaterialRole` value before a `LM83Default` is ever built, but
        `==` is used here anyway as the correct comparison for a `str`-backed enum regardless of that
        upstream guard (code review finding Minor 4, task-3-report.md fix round 1: `is` previously
        made a role that was accidentally-but-legitimately equal, rather than identical, invisible to
        every reader of this method)."""
        return frozenset(d.modifier for d in self.defaults if d.role == role)

    @property
    def ground_modifiers(self) -> frozenset[str]:
        """Which modifier(s) this call assigned the LM-83 ground row to -- ruling R9's
        `InputRecord.role` (Task 4) and Task 7's cross-cutting schema test both need this without
        re-deriving "which polygon is ground" themselves; only this module's own role mapping knows
        (module docstring)."""
        return self.modifiers_with_role(MaterialRole.GROUND)


def apply_defaults(role_by_modifier: Mapping[str, MaterialRole]) -> AppliedDefaults:
    """Write LM-83's default reflectance (ch04) for every modifier in `role_by_modifier`, each
    carrying `provenance="default"` and the exact clause citation `_LM83_SOURCE` gives its role.

    `role_by_modifier` is the caller's own statement of "these modifiers have no known reflectance,
    and here is which role each plays" -- `rad_engine.materials`/Task 6 builds it from whichever
    modifiers `materials(action="apply_defaults")` was asked to fill (docs/tool-surface.md's own
    "fills unknown reflectances" phrasing). This function does not itself decide which modifiers are
    unknown; it holds no state across calls to decide that from, being pure (module docstring) --
    every modifier in the mapping gets a default, and a modifier the caller no longer wants defaulted
    is a modifier the caller no longer puts in the mapping.

    **No ground-reflectance argument exists on this signature, and none should be added** -- see the
    module docstring's "Ground reflectance does not get a second home here" section and check 1.10.

    `DEFAULTS_SUBSTITUTED` fires once per call, not once per modifier, when `role_by_modifier` is
    non-empty -- "warn, always, when any default is in play" (docs/tool-surface.md#5-materials): the
    finding is "this result contains at least one default", not N separate findings that each say the
    same thing.

    Refuses (`InvalidRoleError`) any `role_by_modifier` value that is not a `MaterialRole` member.
    `MaterialRole` subclasses `str`, so a plain string equal to a role's own value (`"ground"` for
    `MaterialRole.GROUND`) would otherwise be accepted silently -- the dict lookup below succeeds by
    hash/eq coincidence -- and then read back wrong (code review finding Minor 4, task-3-report.md
    fix round 1): checked explicitly here rather than left to work by accident.
    """
    for modifier, role in role_by_modifier.items():
        if not isinstance(role, MaterialRole):
            raise InvalidRoleError(
                f"role_by_modifier[{modifier!r}] = {role!r} is not a MaterialRole member (got "
                f"{type(role).__name__}). Pass one of {[r.value for r in MaterialRole]!r}."
            )

    defaults = tuple(
        LM83Default(
            modifier=modifier,
            role=role,
            reflectance=ProvenanceValue(
                value=_LM83_REFLECTANCE[role],
                provenance="default",
                source=_LM83_SOURCE[role],
            ),
        )
        for modifier, role in role_by_modifier.items()
    )

    warnings: tuple[ArtifactWarning, ...] = ()
    if defaults:
        modifiers = ", ".join(sorted(d.modifier for d in defaults))
        warnings = (
            ArtifactWarning(
                code="DEFAULTS_SUBSTITUTED",
                severity="warn",
                message=(
                    f"{len(defaults)} modifier(s) ({modifiers}) received LM-83 default reflectances "
                    "(ch04) rather than a measured or user-stated value."
                ),
            ),
        )
    return AppliedDefaults(defaults=defaults, warnings=warnings)
