"""Radiance primitive emission for surface optical properties.

`rad_core.materials` "validates reflectance/transmittance ranges and energy balance, and owns the
LM-83 default table" -- this module is the layer its own docstring names as the one still to come:
"`rad_engine.materials` (a later task) is the layer that emits Radiance primitives (`plastic`,
`metal`, `glass`, `trans`, `BSDF`) from the values this module validates or defaults -- nothing here
knows what a Radiance primitive looks like". This module is where that knowledge lives (this
project's layer split: `rad_engine` holds primitive syntax, and `rad_mcp` holds none).

**What this module does not do.** It does not re-validate a reflectance/transmittance range, re-check
energy balance, re-derive the glass transmittance -> transmissivity conversion, or re-derive the
`trans` reflectance/transmittance -> colour/transmissivity split (ruling R30) --
`rad_core.materials.validate_assignment` is the one entry point per assignment (its own docstring:
"the one entry point `rad_engine.materials`/Task 6 needs"), and its `AssignmentValidation` result is
consumed, never re-derived. `emit_assignment` below is the only place this module calls it; the only
numbers this module writes into a `glass` primitive are `validation.glass_transmissivity.value`, and
the only ones it writes into a `trans` primitive's colour and transmissivity slots are
`validation.trans_split.colour.value`/`.transmissivity.value`. This module imports neither
`glass_transmissivity` nor `trans_split`, so it has no way to call either -- see `emit_assignment`'s
docstring for how that is proven rather than claimed.

**What this module alone can do: the modifier name.** `validate_assignment` takes no `modifier`
argument -- it validates optical properties -- and `_primitive_text` below is the first and only
place a modifier name becomes Radiance syntax. So the identifier check lives here, next to the
primitive syntax that gives those characters their meaning. See `validate_modifier_identifier`.

**BSDF.** Refused by `rad_core.materials.validate_material_type` (`BSDFTypeRefusedError`), reached
through `validate_assignment` before this module ever inspects `material_type`. No second refusal is
written here -- see `emit_assignment`.

**The ordering guarantee, and how it was verified.** docs/specs/artificial-lighting.md §5.2: "A
fundamental rule of Radiance is that a modifier must be defined before it is referenced" -- materials
must precede the geometry that uses them on the `oconv`/`xform` command line, or the run fails.
Verified directly against this machine's Radiance 6.1a install while writing this module (not taken
on faith from the spec):

    $ oconv materials.rad geom.rad >correct.oct   # exit 0
    $ oconv geom.rad materials.rad >wrong.oct     # exit 1
    oconv: fatal - (geom.rad): undefined modifier "red_paint"

`oconv`/`xform` read `scene_files` strictly in the order given -- there is no flag that changes this.
So "materials must precede geometry" cannot be satisfied by a comment asking a caller to remember an
order; it has to be true of the sequence a caller can actually construct. `scene_files_with_materials`
is the one function in this module (and, by contract, the one a caller should use) that builds
`rad_engine.scene.assemble_scene`'s `scene_files` argument out of a `MaterialsFile` this module wrote
and a geometry file list -- it always places every materials path ahead of every geometry path, by
construction, so a caller cannot invoke it and get the wrong order. See that function's own docstring,
and task-6-report.md for the end-to-end proof against a real octree build.

**Provenance, carried through for check 2.9.** docs/plan.md check 2.9: "No result anywhere reports a
default value without provenance -- enforced by a schema test over every tool's return." Task 3's
`AppliedDefaults.defaults` already carries `LM83Default.reflectance` as a `ProvenanceValue` (it
computed that number itself). But `validate_assignment` deliberately does *not* wrap a caller-supplied
`reflectance`/`transmittance` in a `ProvenanceValue` -- its own module docstring: "Values this module
only validates ... stay plain floats in and out: this module did not choose them, so it has no
provenance of its own to attach." This module is the one place that *does* have both pieces --
the raw, validated float, and the `provenance`/`source` the caller stated for it -- so `emit_assignment`
re-attaches them into a `ProvenanceValue` on `EmittedMaterial.reflectance`/`.transmittance`, and
`emit_defaults` carries `LM83Default.reflectance` straight across unchanged. Task 7's wire layer can
therefore read a `ProvenanceValue` off every `EmittedMaterial` it receives from this module and never
has to reconstruct one, or risk shipping a bare float that "reads as measured" at the wire
(docs/tool-surface.md#provenance-a-default-is-never-a-measurement).

**Field-bearing warnings.** `AssignmentValidation.warnings` are `rad_core.manifest.ArtifactWarning`,
which (ruling R24) carries an optional `field` distinguishing `REFLECTANCE_IMPLAUSIBLE` on a
reflectance from the same code on a transmittance. This module passes them straight through on
`EmittedMaterial.warnings` -- untouched, unconverted, `field` intact -- rather than lowering them into
`rad_engine.advisories.EngineAdvisory`, which (ruling R28, amended) has no `field` slot and must never
be asked to carry one. `rad_mcp.advisories.to_wire` already has the channel for a field-bearing
finding to reach the wire (`field` as an explicit keyword), so Task 7 reads `warning.field` directly
off what this module returns.

**Ground reflectance stays one project value.** Nothing here reads or writes
`rad_core.manifest.Project.ground_reflectance`, and `emit_assignment`'s `role` argument does not
create a second channel for it -- consistent with `rad_core.materials`'s own module docstring ("Ground
reflectance does not get a second home here") and this task's constraint 7. What this module *does*
do is make `GROUND_REFLECTANCE_INCONSISTENT` reachable: an `EmittedMaterial` (or an
`EmittedDefaults` entry) carrying `role=MaterialRole.GROUND` also carries the modelled ground
material's own reflectance as a `ProvenanceValue`, so Task 7's comparison against
`project.ground_reflectance` has both numbers to hand without re-deriving either.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from rad_core.manifest import ArtifactWarning, Provenance, ProvenanceValue
from rad_engine.safe_io import open_regular_file
from rad_core.materials import (
    AppliedDefaults,
    MODIFIER_IDENTIFIER_MAX_LENGTH,
    MODIFIER_IDENTIFIER_PATTERN as _MODIFIER_IDENTIFIER,
    MaterialRole,
    RESERVED_MODIFIER_IDENTIFIERS as _RESERVED_IDENTIFIERS,
    TransSplit,
    validate_assignment,
)

__all__ = [
    "MODIFIER_IDENTIFIER_MAX_LENGTH",
    "SUPPORTED_MATERIAL_TYPES",
    "DuplicateModifierError",
    "EmittedDefaults",
    "EmittedMaterial",
    "MaterialArgumentError",
    "MaterialsEmissionError",
    "MaterialsFile",
    "ModifierIdentifierError",
    "UnsupportedMaterialTypeError",
    "emit_assignment",
    "emit_defaults",
    "render_glass",
    "render_materials_text",
    "render_metal",
    "render_plastic",
    "render_trans",
    "scene_files_with_materials",
    "validate_modifier_identifier",
    "write_materials",
]

#: The four primitive types this module knows how to emit, verbatim from docs/specs/materials.md
#: (brief: "use these exact shapes"). Every other Radiance material type (`mirror`, `light`, `illum`,
#: `BRTDfunc`, ...) is out of this task's stated scope and refused by `UnsupportedMaterialTypeError`.
#: `BSDF` is not in this set -- it is refused earlier, by `rad_core.materials.validate_material_type`,
#: and never reaches the point where this set would be consulted.
SUPPORTED_MATERIAL_TYPES = frozenset({"plastic", "metal", "glass", "trans"})

#: Every primitive this module emits is a base material with no further modifier.
#: docs/specs/artificial-lighting.md: "The special keyword void is used to signify that no modifier
#: is being applied, which is the standard practice when defining a base material." None of the four
#: types above chain to another named modifier -- that is a `mixfunc`/`illum`/`mirror`
#: alternate-material concern this task's brief does not ask this module to emit.
_VOID = "void"


# --------------------------------------------------------------------------------------------
# Refusals
# --------------------------------------------------------------------------------------------


class MaterialsEmissionError(RuntimeError):
    """Something this module refuses to emit as a Radiance primitive.

    A `RuntimeError`, not a `rad_core.materials.MaterialsError` subclass: this module's own refusals
    are about primitive syntax only `rad_engine` knows (CONTEXT.md's layer rule), never about a value
    `rad_core` already validated, so extending `rad_core`'s exception hierarchy across the layer
    boundary would be the wrong inheritance. The same choice `rad_engine.scene.SceneFileNotFoundError`
    and `rad_engine.octree.EmptyOctreeError` already made for their own refusals.
    """

    code: str | None = None
    severity: str = "error"
    field: str | None = None

    def __init__(self, message: str, *, field: str | None = None) -> None:
        super().__init__(message)
        if field is not None:
            self.field = field


class UnsupportedMaterialTypeError(MaterialsEmissionError):
    """`material_type` is not one of `SUPPORTED_MATERIAL_TYPES`.

    `BSDF` never reaches this check -- `rad_core.materials.validate_material_type` (reached through
    `validate_assignment`) refuses it first, with its own code. This class is for every *other*
    Radiance material type the spec documents that this task's brief does not ask this module to
    emit.

    Reuses `ARGUMENT_OUT_OF_RANGE`: ruling R14's own reasoning -- "supplied with a value the tool will
    not accept" -- is exactly this case, and it is the same code `BSDFTypeRefusedError` reuses one
    layer down for the identical class of refusal.
    """

    code = "ARGUMENT_OUT_OF_RANGE"
    field = "type"


class MaterialArgumentError(MaterialsEmissionError):
    """A `reflectance`/`transmittance` argument this `material_type`'s primitive either requires and
    was not given, or has no slot for and was given anyway.

    Only this layer can tell. `rad_core.materials.validate_assignment` validates whichever of
    `reflectance`/`transmittance` it is handed, generically, with no notion of which primitive a
    caller intends -- it does not know primitive syntax (module docstring). But `plastic`/`metal` have
    no `transmittance` argument at all, `glass` has no `reflectance` argument, and `trans` needs both
    (docs/specs/materials.md) -- so a mismatch between what a chosen type's primitive accepts and what
    was supplied can only be caught here. Silently accepting the extra value would drop it from the
    primitive with no record; silently accepting the missing one would emit a malformed primitive.

    Reuses `ARGUMENT_OUT_OF_RANGE`, `field` naming whichever of `reflectance`/`transmittance` is
    wrong -- the same code and the same "field says which argument" reasoning
    `rad_core.materials.ReflectanceRangeError`/`TransmittanceRangeError` already use for a different
    failure on the same two fields.
    """

    code = "ARGUMENT_OUT_OF_RANGE"


class ModifierIdentifierError(MaterialsEmissionError):
    """A modifier name that cannot be written into a Radiance primitive as one plain identifier.

    **Only this module can catch it, and nothing else was.** `_primitive_text` is the first and only
    place a modifier name becomes Radiance syntax (CONTEXT.md's layer rule: `rad_engine` owns
    primitive syntax), and `rad_core.materials.validate_assignment` takes no `modifier` argument at
    all -- it validates optical properties, not names. So before this check existed no layer looked
    at the string that ends up as the third token of every primitive this module writes.

    Reuses `ARGUMENT_OUT_OF_RANGE` with `field="modifier"`: ruling R14's own reasoning, "an argument
    rejected on its face, before any work was attempted", and the same code/`field` pairing
    `UnsupportedMaterialTypeError` and `MaterialArgumentError` above already use for the other two
    arguments of the same call.
    """

    code = "ARGUMENT_OUT_OF_RANGE"
    field = "modifier"


class DuplicateModifierError(MaterialsEmissionError):
    """The same modifier is defined twice -- in one materials file, or across two of them.

    Radiance does not report this. Measured on this machine's Radiance 6.1a: two `void plastic w`
    definitions with reflectances 0.2 and 0.8, both in one file *and* split across two files listed
    in order, each give `oconv` exit 0 with empty stderr, and `rtrace` reads back 0.8 -- the last
    definition silently wins, and which one that is depends on the order a caller happened to pass
    its files in.

    Refused rather than warned, and this module deliberately does not pick a winner. Override may
    well be what a caller wants -- the multi-file case is real (one materials file per
    `materials(action="set")` call, plus one from `apply_defaults`) -- but an override that is
    unstated, order-dependent and invisible is the "silent wrong answer" this project's posture turns
    into an immediate one (ruling R28's guard, `LM83Default.__post_init__`, `apply_defaults`'s role
    check). A caller who means to override says so by not passing both definitions. There is no
    allocated `warn`-severity code for "this modifier is defined twice", and none is invented here.

    Reuses `ARGUMENT_OUT_OF_RANGE` with `field="modifier"` -- see `ModifierIdentifierError`.
    """

    code = "ARGUMENT_OUT_OF_RANGE"
    field = "modifier"


# --------------------------------------------------------------------------------------------
# Primitive text
# --------------------------------------------------------------------------------------------

#: `MODIFIER_IDENTIFIER_MAX_LENGTH`, `_MODIFIER_IDENTIFIER` and `_RESERVED_IDENTIFIERS` are imported
#: from `rad_core.materials` above (as `MODIFIER_IDENTIFIER_PATTERN`/`RESERVED_MODIFIER_IDENTIFIERS`)
#: rather than defined here -- the rule is pure (no Radiance syntax, no primitive text) and
#: `rad_core.obj` needs the identical check to refuse an OBJ name before any converter runs. See that
#: module's own comment above `MODIFIER_IDENTIFIER_PATTERN` for the measurements the pattern encodes,
#: and `modifier_identifier_problem` for the one function both layers refuse a name through.
#:
#: `_VOID` is written as *every* primitive's modifier here, so a material also *named* `void` would
#: be a definition whose own name is the keyword saying it has no modifier -- which is why `"void"` is
#: reserved.


def validate_modifier_identifier(identifier: str) -> str:
    """Refuse a modifier name this module cannot write as one plain Radiance identifier.

    See `_MODIFIER_IDENTIFIER` for the rule and the measurements behind it, and
    `ModifierIdentifierError` for why this check has to live in this module. Returns the identifier
    unchanged so it can be used inline; it is never normalised, because silently rewriting a caller's
    modifier name would break the match against the geometry that references it.
    """
    if identifier in _RESERVED_IDENTIFIERS:
        raise ModifierIdentifierError(
            f"modifier {identifier!r} is reserved by Radiance -- `void` is the keyword meaning "
            '"no modifier is being applied" (docs/specs/artificial-lighting.md), and oconv refuses '
            "it as a name with `illegal modifier name`."
        )
    if not _MODIFIER_IDENTIFIER.match(identifier or ""):
        raise ModifierIdentifierError(
            f"modifier {identifier!r} is not a usable Radiance identifier. It must be 1-"
            f"{MODIFIER_IDENTIFIER_MAX_LENGTH} characters of letters, digits, '_', '.', '+' or '-', "
            "starting with a letter, digit or '_'. An identifier becomes the third token of a "
            "primitive and the first token of every geometry line that references it, so "
            "whitespace, quotes and a leading '#' or '!' do not name a material -- they change what "
            "the file says (docs/specs/artificial-lighting.md: \"This identifier must not contain "
            "whitespace\")."
        )
    return identifier


def _format_real(value: float) -> str:
    """One real argument, exactly. `repr`, not a fixed-precision format: this project's own review
    of the glass conversion measured an 8.2% error from a wrong *value*, and truncating a correct
    value's precision on the way to disk is the same class of mistake with fewer digits. Radiance's
    number parser accepts this shape (`0.0`, `0.679045...`) exactly as it accepts a shorter one --
    verified against this machine's Radiance 6.1a install alongside the ordering check in the module
    docstring.
    """
    return repr(float(value))


def _primitive_text(ptype: str, identifier: str, real_args: Sequence[float]) -> str:
    """`modifier type identifier` / `0` / `0` / `N v1 .. vN`, `modifier` always `_VOID` -- the one
    shape every primitive in `docs/specs/materials.md`'s Plastic/Metal/Trans/Glass rows share, with
    zero string and zero int arguments (the brief's own verbatim shapes for all four).

    `identifier` is validated here rather than in each `render_*` or in `emit_assignment`, because
    this is the single funnel every primitive this module writes passes through -- a check anywhere
    else would be one a future fifth primitive could be written without. See
    `validate_modifier_identifier`.
    """
    validate_modifier_identifier(identifier)
    reals = " ".join(_format_real(v) for v in real_args)
    return f"{_VOID} {ptype} {identifier}\n0\n0\n{len(real_args)} {reals}\n"


def render_plastic(identifier: str, *, reflectance: float, specular: float, roughness: float) -> str:
    """docs/specs/materials.md:115-125, verbatim shape: ``5 red green blue spec rough``.
    `reflectance` is applied to all three colour channels -- neither `rad_core.materials` nor
    `docs/tool-surface.md`'s `materials(action="set")` signature expose a per-channel reflectance."""
    return _primitive_text("plastic", identifier, [reflectance, reflectance, reflectance, specular, roughness])


def render_metal(identifier: str, *, reflectance: float, specular: float, roughness: float) -> str:
    """docs/specs/materials.md:127: "similar to plastic, but specular highlights are modified by the
    material color" -- same five-argument shape as `render_plastic`."""
    return _primitive_text("metal", identifier, [reflectance, reflectance, reflectance, specular, roughness])


def render_glass(identifier: str, *, transmissivity: float) -> str:
    """docs/specs/materials.md:195-208, verbatim shape: ``3 rtn gtn btn``.

    Takes **transmissivity**, not transmittance -- see the module docstring and `emit_assignment` for
    why the caller of this function must always be `validation.glass_transmissivity.value`, never a
    transmittance a caller typed directly. This function itself does no conversion and cannot tell the
    two apart; that is precisely why it must never be called with an unconverted number.
    """
    return _primitive_text("glass", identifier, [transmissivity, transmissivity, transmissivity])


def render_trans(
    identifier: str,
    *,
    colour: float,
    specular: float,
    roughness: float,
    transmissivity: float,
    trans_specular: float,
) -> str:
    """docs/specs/materials.md:131-139, verbatim shape: ``7 red green blue spec rough trans tspec``.

    **Takes a colour and a transmissivity -- neither of which is a reflectance or a transmittance.**
    docs/specs/materials.md's Trans row: "The transmissivity is the fraction of **penetrating** light
    that travels all the way through the material ... Transmitted and diffusely reflected light is
    modified by the material color." The `red green blue` arguments carry the *combined* diffuse
    fraction and `trans` splits it, so Radiance delivers `rho = colour * (1 - spec) * (1 -
    transmissivity)` and `tau = colour * (1 - spec) * transmissivity`. A caller who writes their
    reflectance into the colour and their transmittance into the transmissivity gets neither back:
    measured on Radiance 6.1a, asking rho=0.4 tau=0.3 that way delivers 0.280/0.120.

    `rad_core.materials.trans_split` (ruling R30) owns the inversion and is the single arithmetic
    path, exactly as `rad_core.materials.glass_transmissivity` is for `render_glass`. Both parameters
    are named for what the *primitive* calls them rather than for what a user typed, for the reason
    `render_glass`'s `transmissivity=` is: it is the one guard available at this level against
    passing the raw pair straight in, which is the defect this signature was changed to make
    unsayable.
    """
    return _primitive_text(
        "trans",
        identifier,
        [colour, colour, colour, specular, roughness, transmissivity, trans_specular],
    )


# --------------------------------------------------------------------------------------------
# Assignment -> primitive
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class EmittedMaterial:
    """One modifier's Radiance primitive, plus everything Task 7 needs to build a provenance-labelled
    wire result from it (check 2.9) without re-deriving anything this layer already knows.

    `reflectance`/`transmittance` are `ProvenanceValue | None`, never a bare float where a value was
    given -- see the module docstring's provenance section for why this module, not `rad_core`, is the
    one that attaches it. `transmittance` is `None` for a `glass` material even when a transmittance
    was supplied: the raw, unconverted number a caller typed is not what reached the primitive
    (`glass_transmissivity` is), and carrying it under a second key would invite a reader to write the
    wrong one into `glass` -- the exact defect Task 3's fix round closed one layer down.
    """

    modifier: str
    material_type: str
    primitive_text: str
    reflectance: ProvenanceValue | None = None
    transmittance: ProvenanceValue | None = None
    glass_transmissivity: ProvenanceValue | None = None
    warnings: tuple[ArtifactWarning, ...] = ()
    role: MaterialRole | None = None
    trans_split: TransSplit | None = None
    """The `trans` primitive's own colour and transmissivity arguments, straight from
    `rad_core.materials.trans_split` (ruling R30); `None` for every other type. Set only so a reader
    can see which two numbers reached the primitive -- unlike `glass`, whose raw input is deliberately
    *not* carried on `.transmittance`, a `trans` material's `.reflectance`/`.transmittance` are what
    the emitted primitive genuinely delivers, so both keys describe the same material rather than
    competing to be written into it."""


#: Which of `reflectance`/`transmittance` each supported type's primitive requires or has no slot
#: for. Drives `emit_assignment`'s completeness check -- see `MaterialArgumentError`.
_REQUIRES_REFLECTANCE = frozenset({"plastic", "metal", "trans"})
_FORBIDS_REFLECTANCE = frozenset({"glass"})
_REQUIRES_TRANSMITTANCE = frozenset({"glass", "trans"})
_FORBIDS_TRANSMITTANCE = frozenset({"plastic", "metal"})


def emit_assignment(
    modifier: str,
    *,
    material_type: str,
    reflectance: float | None = None,
    transmittance: float | None = None,
    specular: float = 0.0,
    roughness: float = 0.0,
    trans_specular: float = 0.0,
    provenance: Provenance = "assumed",
    source: str | None = None,
    role: MaterialRole | None = None,
) -> EmittedMaterial:
    """Validate one modifier's assignment through `rad_core.materials.validate_assignment` -- the
    entry point that module's own docstring names for this task -- and emit its Radiance primitive.

    **The glass conversion is consumed, never repeated.** When `material_type` is `"glass"`,
    `render_glass` is called with `validation.glass_transmissivity.value` -- the number
    `rad_core.materials.glass_transmissivity` already computed -- and with nothing else. This
    function contains no copy of the conversion formula; see task-6-report.md for how that is proven
    (grepping this module's own source for the reference manual's constants) rather than merely
    asserted here.

    **So is the `trans` split, for the identical reason** (ruling R30). When `material_type` is
    `"trans"`, `render_trans` is called with `validation.trans_split.colour.value` and
    `.transmissivity.value` -- **not** with this function's own `reflectance`/`transmittance`
    arguments, which are not what that primitive's slots mean (see `render_trans`). Writing the raw
    pair in delivered -30% to -70% of the requested reflectance and -50% to -80% of the requested
    transmittance on every blind, shade and diffusing panel, measured through real `oconv`/`rtrace`;
    `rad_core.materials.trans_split` owns the inversion and this module does not import it, so there
    is no second path here that could diverge from it.

    **BSDF is refused before this function inspects `material_type` at all** -- `validate_assignment`
    calls `rad_core.materials.validate_material_type`, which raises `BSDFTypeRefusedError` first. No
    second refusal with different wording is written here (brief: "do not add a second refusal").

    `specular`/`roughness` default to 0 (a perfectly smooth, non-specular surface --
    docs/specs/materials.md: "a value of 0 corresponds to a perfectly smooth surface"), a well-defined
    Radiance value in its own right rather than an unstated guess, and are therefore not wrapped in a
    `ProvenanceValue` the way `reflectance`/`transmittance` are: this project's provenance rule
    (constraint 3) is about a *measurement* substituted without a source, not about a numeric
    primitive parameter no schema currently tracks.

    **`reflectance`/`transmittance` must match what the chosen type's primitive can express** --
    `plastic`/`metal` require `reflectance` and forbid `transmittance`; `glass` requires
    `transmittance` and forbids `reflectance`; `trans` requires both. A mismatch raises
    `MaterialArgumentError`, `field` naming the offending argument -- see that class's own docstring
    for why only this layer can catch it.

    `role`, where given, is validated by `validate_assignment` (refusing a plain string that merely
    compares equal to a `MaterialRole` member -- see that function's own docstring) and carried
    straight onto `EmittedMaterial.role` unchanged; this function does not otherwise act on it.
    """
    validation = validate_assignment(
        material_type=material_type,
        reflectance=reflectance,
        transmittance=transmittance,
        provenance=provenance,
        source=source,
        role=role,
    )

    kind = material_type.strip().casefold()
    if kind not in SUPPORTED_MATERIAL_TYPES:
        raise UnsupportedMaterialTypeError(
            f"material_type={material_type!r} is not one of {sorted(SUPPORTED_MATERIAL_TYPES)!r} -- "
            "this module emits plastic, metal, glass and trans only (docs/specs/materials.md)."
        )

    if kind in _REQUIRES_REFLECTANCE and reflectance is None:
        raise MaterialArgumentError(
            f"{kind} requires reflectance -- none was given for modifier {modifier!r}.",
            field="reflectance",
        )
    if kind in _FORBIDS_REFLECTANCE and reflectance is not None:
        raise MaterialArgumentError(
            f"{kind} has no reflectance argument (docs/specs/materials.md) -- modifier {modifier!r} "
            "was given one, which would be silently dropped rather than written to the primitive.",
            field="reflectance",
        )
    if kind in _REQUIRES_TRANSMITTANCE and transmittance is None:
        raise MaterialArgumentError(
            f"{kind} requires transmittance -- none was given for modifier {modifier!r}.",
            field="transmittance",
        )
    if kind in _FORBIDS_TRANSMITTANCE and transmittance is not None:
        raise MaterialArgumentError(
            f"{kind} has no transmittance argument (docs/specs/materials.md) -- modifier {modifier!r} "
            "was given one, which would be silently dropped rather than written to the primitive.",
            field="transmittance",
        )

    # The asserts below restate what the guards above already enforce, for a reader and a type
    # checker that cannot connect a membership test on `kind` to this dispatch. They are assertions
    # rather than defaults precisely because of what the alternative costs: a substituted number
    # emits a material that silently transmits or reflects something other than what was asked for,
    # which is the class of defect the glass and trans fix rounds were both about.
    if kind == "plastic":
        assert reflectance is not None  # _REQUIRES_REFLECTANCE, checked above
        text = render_plastic(modifier, reflectance=reflectance, specular=specular, roughness=roughness)
    elif kind == "metal":
        assert reflectance is not None  # _REQUIRES_REFLECTANCE, checked above
        text = render_metal(modifier, reflectance=reflectance, specular=specular, roughness=roughness)
    elif kind == "trans":
        # Both fractions were required above, so validate_assignment computed the split (ruling R30).
        split = validation.trans_split
        assert split is not None
        text = render_trans(
            modifier,
            colour=split.colour.value,
            specular=specular,
            roughness=roughness,
            transmissivity=split.transmissivity.value,
            trans_specular=trans_specular,
        )
    else:  # kind == "glass" -- required above, and validate_assignment converted it.
        converted = validation.glass_transmissivity
        assert converted is not None
        text = render_glass(modifier, transmissivity=converted.value)

    reflectance_value = (
        ProvenanceValue(value=reflectance, provenance=provenance, source=source)
        if reflectance is not None
        else None
    )
    transmittance_value = (
        ProvenanceValue(value=transmittance, provenance=provenance, source=source)
        if transmittance is not None and kind != "glass"
        else None
    )

    return EmittedMaterial(
        modifier=modifier,
        material_type=kind,
        primitive_text=text,
        reflectance=reflectance_value,
        transmittance=transmittance_value,
        glass_transmissivity=validation.glass_transmissivity,
        trans_split=validation.trans_split,
        warnings=validation.warnings,
        role=validation.role,
    )


# --------------------------------------------------------------------------------------------
# LM-83 defaults -> primitives
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class EmittedDefaults:
    """`emit_defaults`'s result: every LM-83 default written as a primitive, plus the one
    `DEFAULTS_SUBSTITUTED` warning that applies whenever `apply_defaults` wrote anything -- mirrors
    `rad_core.materials.AppliedDefaults`'s own split between per-modifier data and one call-level
    finding, rather than duplicating the finding onto every `EmittedMaterial`."""

    materials: tuple[EmittedMaterial, ...]
    warnings: tuple[ArtifactWarning, ...]


def emit_defaults(applied: AppliedDefaults) -> EmittedDefaults:
    """Every `rad_core.materials.LM83Default` `apply_defaults` wrote, as a `plastic` primitive.

    Always `plastic`, specular 0, roughness 0: none of LM-83's seven roles (floor, wall, ceiling,
    furniture, ground, exterior vertical, trees) is a glazing role, and ch04 states a reflectance only
    -- never a surface finish -- so a fully diffuse `plastic` (docs/specs/materials.md: "a value of 0
    corresponds to a perfectly smooth surface") is the one choice this layer can make without
    inventing a finish the standard never states.

    `LM83Default.reflectance` is already a `ProvenanceValue` (`provenance="default"`, the clause
    citation as `source`) -- carried onto `EmittedMaterial.reflectance` unchanged, the same value used
    to build the primitive's RGB, so a caller never sees two different numbers for the same modifier.
    """
    materials = tuple(
        EmittedMaterial(
            modifier=default.modifier,
            material_type="plastic",
            primitive_text=render_plastic(
                default.modifier, reflectance=default.reflectance.value, specular=0.0, roughness=0.0
            ),
            reflectance=default.reflectance,
            role=default.role,
        )
        for default in applied.defaults
    )
    return EmittedDefaults(materials=materials, warnings=applied.warnings)


# --------------------------------------------------------------------------------------------
# Disk: the ordering guarantee
# --------------------------------------------------------------------------------------------


def _refuse_duplicate_modifiers(modifiers: Sequence[str], *, what: str) -> None:
    """`DuplicateModifierError` naming every modifier that appears more than once in `modifiers`.

    One helper for both places a collision can be built here -- two `EmittedMaterial`s in one file
    (`render_materials_text`) and two `MaterialsFile`s on one `oconv` command line
    (`scene_files_with_materials`) -- because `oconv`'s behaviour is identical in both: exit 0, empty
    stderr, last definition wins. See `DuplicateModifierError`.
    """
    seen: set[str] = set()
    duplicates: list[str] = []
    for modifier in modifiers:
        if modifier in seen and modifier not in duplicates:
            duplicates.append(modifier)
        seen.add(modifier)
    if duplicates:
        raise DuplicateModifierError(
            f"{', '.join(sorted(duplicates))} defined more than once across {what}. oconv would "
            "take the last definition, exit 0 and report nothing, so which one applies would depend "
            "on the order these were passed in. Pass one definition per modifier; to override an "
            "earlier one, do not also pass the earlier one."
        )


def render_materials_text(materials: Sequence[EmittedMaterial]) -> str:
    """Concatenate every given primitive's text into one `.rad`-ready document, ordered by modifier
    name -- never by whatever order `materials` arrived in.

    None of `plastic`/`metal`/`glass`/`trans` reference another modifier by name (module docstring:
    every primitive this module emits has `void` for its own modifier), so *within* one materials
    file `oconv` does not care what order the primitives appear in. But this project's digests do
    (`rad_core.manifest._canonical_json` sorts every key, `compute_digest` sorts its edge pairs, for
    the identical reason): a caller that built `materials` by iterating a `dict` --
    `materials(action="set")`'s own `assignments` argument, or `apply_defaults`'s `role_by_modifier`
    upstream of `emit_defaults` -- would otherwise hand this function whatever order Python's dict
    preserved, and the same set of assignments supplied in a different call order would write
    different bytes to disk for no physical reason. Sorting here removes that dependency structurally
    rather than trusting every caller to pass materials pre-sorted -- the same "not merely detectable
    after the fact" standard the module docstring's ordering guarantee holds itself to, applied to
    reproducibility instead of to `oconv` correctness.

    Refuses (`DuplicateModifierError`) two materials with the same modifier. `oconv` does not: it
    takes the last definition, exits 0 and says nothing, so sorting the same name into two adjacent
    primitives would produce a file whose meaning depends on which of the two the sort happened to
    put second. See that class for the measurement and for why this is a refusal rather than a
    documented override.
    """
    _refuse_duplicate_modifiers(
        [material.modifier for material in materials],
        what="materials in one file",
    )
    ordered = sorted(materials, key=lambda material: material.modifier)
    return "".join(material.primitive_text for material in ordered)


@dataclass(frozen=True)
class MaterialsFile:
    """One materials `.rad` file `write_materials` wrote, and the modifiers it defined."""

    path: Path
    modifiers: frozenset[str]


def write_materials(path: Path | str, materials: Sequence[EmittedMaterial]) -> MaterialsFile:
    """Write every given primitive to one `.rad` file at `path`, deterministically ordered
    (`render_materials_text`), and return the modifiers it defined.

    The returned `modifiers` set is exactly what a caller should pass on as
    `rad_core.geometry.inventory`'s `known_modifiers` (`rad_engine.scene.assemble_scene`'s argument
    of the same name) -- this module already knows which identifiers it just defined, so a caller does
    not have to re-derive that set from the primitives a second time.

    `path`'s parent directories are created if missing, the same convention
    `rad_engine.octree.build_octree`/`run_to_file` already use for an artifact's own output path.

    The write goes through `rad_engine.safe_io.open_regular_file` (ruling R48), so a symlink or a
    FIFO at `path` is refused rather than followed. Raises `NotARegularFileError` for a FIFO or a
    device, and a raw `OSError` with `errno.ELOOP` for a symlink -- `O_NOFOLLOW` fails the open
    *before* there is a descriptor to `fstat`, so the regular-file branch is never reached for the
    case the guard exists to stop. A caller that shapes these into a refusal must catch both.

    **Containment of the parent chain stays the caller's**, exactly as R48 left `write_wea` and
    `write_points`: `O_NOFOLLOW` does not see a *directory* symlink partway up the chain, and only
    the tool layer holds the allowed roots. `rad_mcp.materials_tool` applies
    `resolved_parent_within` before calling this.
    """
    path = Path(path)
    text = render_materials_text(materials)  # raises before anything is created
    path.parent.mkdir(parents=True, exist_ok=True)
    # **Not** `path.write_text`, which follows a symlink at the final component and blocks forever
    # on a FIFO. Adopted 2026-08-10, closing the last write site R48's follow-up named: the caller
    # influences this filename (`materials(action="set")`'s target becomes `<role>.rad`), so a
    # planted symlink here can be aimed at rather than merely waited for -- the same property that
    # ranked `write_points` first of the seventeen.
    with open_regular_file(path, "wb") as handle:
        handle.write(text.encode("utf-8"))
    return MaterialsFile(path=path, modifiers=frozenset(material.modifier for material in materials))


def scene_files_with_materials(
    materials: MaterialsFile | Sequence[MaterialsFile],
    geometry_files: Sequence[str | Path],
) -> tuple[str, ...]:
    """The one correct way to build `rad_engine.scene.assemble_scene`'s `scene_files` argument out of
    a `MaterialsFile` this module wrote and the geometry files that use it.

    See the module docstring's ordering-guarantee section for the verified fact this function exists
    to make structurally true: `oconv`/`xform` read `scene_files` strictly in argument order, and a
    modifier must be defined before it is referenced, with no flag that changes either. This function
    is the only place in this module that assembles that sequence, and it always places every
    materials path ahead of every geometry path, by construction -- a caller that uses it cannot
    produce the wrong order, which is what makes this a guarantee rather than a convention a caller
    has to remember (the brief's own distinction). A caller that instead hand-builds `scene_files`
    without calling this function is, of course, still free to get the order wrong -- exactly as with
    any function whose contract is not enforced on a caller who does not invoke it.

    Accepts one `MaterialsFile` or several -- a project can register more than one materials input
    (one per `materials(action="set")` call, plus one from `apply_defaults`) -- and geometry files as
    `str` or `Path`; all materials precede all geometry, each group in the order given.

    Refuses (`DuplicateModifierError`) two materials files that define the same modifier. That is
    the multi-file half of the same silent collision `render_materials_text` refuses inside one file,
    and it is exactly this function's to catch: it is the one place the `oconv` argument list is
    built, so it is the one place both files' `MaterialsFile.modifiers` sets are in scope at once.
    """
    files = (materials,) if isinstance(materials, MaterialsFile) else tuple(materials)
    _refuse_duplicate_modifiers(
        [modifier for f in files for modifier in sorted(f.modifiers)],
        what="materials files",
    )
    return tuple(str(m.path) for m in files) + tuple(str(g) for g in geometry_files)
