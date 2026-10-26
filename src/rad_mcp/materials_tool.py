"""The `materials` tool -- docs/tool-surface.md#5-materials.

Thin, in CONTEXT.md's sense: this module validates and contains arguments, picks the engine call,
maps findings onto the wire and shapes the envelope. **It contains no optical arithmetic.**
`rad_core.materials` owns the ranges, the energy balance, the glass transmissivity conversion, the
`trans` split and the LM-83 default table; `rad_engine.materials` owns the four primitives and the
`.rad` file. Everything reported here is a value one of those two computed.

**Where the values come from, and why none of them is re-derived here.** `emit_assignment` returns
an `EmittedMaterial` whose `reflectance`/`transmittance`/`glass_transmissivity`/`trans_split` are
already `rad_core.manifest.ProvenanceValue`s. This module copies them onto the wire model unchanged.
A `float(...)` anywhere in that path would be a second arithmetic path, which is the defect Task 6's
own review measured at four to ten times the magnitude for the `trans` split.

**`role` is ruling R25's channel and it is not a second ground reflectance.**
`assignments[modifier].role` says *which modifier is the ground* so that
`GROUND_REFLECTANCE_INCONSISTENT` can compare the modelled ground material against the project
value. The project value is set in exactly one place -- `project(ground_reflectance=...)` -- and this
signature deliberately has no argument for it (docs/tool-surface.md#ground-reflectance-is-one-project-value-passed-to-every-sky-generator,
check 1.10, which introspects every registered tool's signature).

**Two files, not one, and that is the point.** `action="set"` writes `02_materials/set.rad` and
`action="apply_defaults"` writes `02_materials/lm83_defaults.rad`, each registered as its own input.
That is the multi-file case `rad_engine.materials.scene_files_with_materials` exists for: it refuses
the same modifier defined in two of them, which `oconv` would accept silently, taking the last
definition and exiting 0 (`DuplicateModifierError`'s own measurement).

**Each action's file accumulates; it is never rewritten from one call's arguments alone.**
This is the model, and it was chosen after the alternative shipped and lost data. A `set` that wrote
only what that call named destroyed every material an earlier `set` had assigned -- two successful
calls for two *different* modifiers, no advisory, and the first modifier simply gone from the file
and from `list` (fix round 1, defect C1). Every call in isolation was correct; the composition lost
the work, which is why no per-call test saw it.

So `_merge` folds a call's assignments over the ones the manifest already records for that action,
and the file is re-emitted from the union. Three consequences, all of them the point:

- **No call can lose another call's work.** Not "is unlikely to" -- there is no code path that
  writes a file holding less than the union, so the safety is structural rather than a rule a
  future action has to remember.
- **Re-assigning a modifier is still how you change a material** -- the later value wins, exactly as
  `register_input` replaces a record in place for the same reason -- and the result names every
  modifier whose earlier value it overwrote in `MaterialsWritten.replaced`. A client learns it
  replaced something by reading a key, not by parsing a sentence or by diffing two `list` calls.
- **The two actions still accumulate separately**, so `apply_defaults` filling an unknown
  reflectance and `set` stating a measured one never end up in one file under one provenance -- and
  the cross-file duplicate refusal above stays reachable, which it would not be with a single file.

The file-per-call alternative (one `02_materials/<id>.rad` per invocation) is defensible too, and was
rejected on two counts: `materials(action="set")` publishes no `name`, so the ids would have to be
server-generated and would grow without bound with no way for a client to name one for removal; and
`register_input` replaces a record in place, so the manifest would need an input added per call and a
removal path this tool does not have.

**Across the two actions, the most recent call owns the modifier** (`_withdraw_files` /
`_withdraw_record`). Assigning a
modifier withdraws it from the *other* action's file and record, so the two can never both define it.

This is the half of the model that lets a file **shrink**, and without it accumulation was a trap:
`set` and `apply_defaults` could each be made to define `box_mat` -- two successful calls, no
advisory -- after which `scene_files_with_materials` refused **every** `geometry(action="add")` for
the life of the project, with no call able to withdraw either definition (fix round 2, defect C3).
The refusal was right; the unrecoverable state behind it was not.

Refusing the second call instead was the smaller change and was rejected, because it makes the
workflow §5 actually describes unreachable: *"fills unknown reflectances"* invites a user to default
a whole room and then `set` the one wall they later measure, and under a refuse-early rule that
`set` would be rejected with no way to move the modifier out of the defaults file. Ownership moving
to the later call *is* that workflow, in both directions -- and it needs no new action and no new
argument, so the published surface is unchanged. It resolves behaviour §5 leaves undefined rather
than adding to it: a modifier explicitly `set` is not "unknown", so `apply_defaults` should not keep
it, and a `set` after a default is precisely the case of it becoming known.

A withdrawal is never silent: the taken modifiers appear in `MaterialsWritten.replaced` alongside
same-file overwrites, because from the client's side both are the same fact -- *the value you had
for this modifier is gone, here is what it is now*.

**How `list`/`inspect` know what was assigned.** The manifest records, per input, the `ToolCall` that
registered it -- `tool`, `action` and `arguments`. `list` re-runs the same pure validation over those
recorded arguments rather than parsing the `.rad` file back or keeping a second copy of the values:
one source of truth, and a `list` that could disagree with the file is exactly the drift the manifest
exists to prevent. See `_recorded_assignments`.
"""

from __future__ import annotations

import errno
import logging
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Annotated, Any, Final, Literal

from mcp_types import CallToolResult
from pydantic import BaseModel, ConfigDict, Field, RootModel

from rad_core.manifest import InputRecord, ProvenanceValue, ToolCall
from rad_core.materials import (
    MaterialRole,
    MaterialsError,
    apply_defaults,
    ground_reflectance_agrees,
)
from rad_engine import (
    EmittedMaterial,
    LoadedProject,
    MaterialsEmissionError,
    MaterialsFile,
    ProjectError,
    ProjectPaths,
    RadianceError,
    RadianceNotFound,
    SceneFileNotFoundError,
    SceneInlineCommandError,
    discover,
    emit_assignment,
    emit_defaults,
    open_project,
    read_scene,
    register_input,
    save_manifest,
    write_materials,
)
from rad_mcp.advisories import warnings_to_wire
from rad_mcp.envelope import (
    Advisory,
    RadResult,
    Refusal,
    emit,
    one_of_schema,
    refuse,
    refuse_missing,
)
from rad_engine.safe_io import NotARegularFileError, resolved_parent_within
from rad_mcp.paths import PathOutsideRoot, RootsNotConfigured
from rad_mcp.runtime import Runtime, current, refuse_engine_error

logger = logging.getLogger(__name__)

__all__ = [
    "DESTRUCTIVE_ACTIONS",
    "AssignmentRefused",
    "GroundReflectanceRefused",
    "InputStillBuiltFrom",
    "MaterialsCommitted",
    "MaterialsResult",
    "commit_materials_locked",
    "ground_finding",
    "materials",
    "refuse_if_built_from",
    "replay_materials",
    "set_assignments_for_import",
]

Action = Literal["set", "list", "inspect", "apply_defaults"]

#: **`set` and `apply_defaults` are destructive, and saying otherwise was false as soon as
#: `_withdraw` landed.** This constant read `()` and the comment above it read *"`materials` writes
#: but never removes"* while the code, one fix round earlier, had started deregistering an input and
#: deleting a `.rad` file the server wrote -- an implicit, unannounced removal behind a call whose
#: advertised purpose is to assign a material (fix round 3, finding N2).
#:
#: That is not a documentation nit. The MCP annotation is what a **host shows a user before
#: approving a call**, and ruling R17 settled that the host prompt -- not `confirm`, which the model
#: supplies -- is the real consent gate. An annotation that under-declares makes the one gate that
#: works show the wrong thing, and `destructiveHint` defaults to *true* on the wire precisely
#: because saying nothing is meant to be the cautious answer rather than the neutral one.
#:
#: Both writing actions are named, not just one: either can take a modifier from the other and empty
#: its file. `list` and `inspect` are read-only and are deliberately absent, which is what makes this
#: list per-action information rather than a flag
#: (docs/tool-surface.md#annotations-are-coarser-than-the-surface). `tests/test_protocol.py` reads
#: this constant and requires every name in it to appear in the tool's description, so the wire
#: annotation, the description a host renders and this list cannot drift apart.
DESTRUCTIVE_ACTIONS: tuple[str, ...] = ("set", "apply_defaults")

#: The input id and filename each action owns. Fixed rather than caller-supplied: two `set` calls
#: must replace one another's assignment file (`register_input` replaces an id in place -- that is
#: how "change the material" happens), and the two actions must **not** share a file, because
#: `apply_defaults` filling an unknown reflectance and `set` stating a measured one are two different
#: provenances for the same project.
_FILES: dict[str, tuple[str, str]] = {
    "set": ("materials_set", "set.rad"),
    "apply_defaults": ("materials_lm83_defaults", "lm83_defaults.rad"),
}

#: Which action's file a modifier is withdrawn *from* when the other one claims it. Derived from
#: `_FILES` rather than written out, so a third writing action cannot be added without deciding what
#: it displaces -- it would raise here on the first call instead of quietly defining a modifier twice.
_OTHER_ACTION: dict[str, str] = {
    action: next(other for other in _FILES if other != action) for action in _FILES
}

#: How long `_geometry_usage`'s `xform` read of one registered geometry input may take.
#:
#: `read_scene` here took `rad_engine.run.run`'s `timeout=None` default -- no bound at all -- so
#: `materials(action="inspect")`, a read-only-looking call, could block the request thread forever on
#: a scene it does not own. Shorter than `geometry_tool._ASSEMBLY_TIMEOUT_S` because this path builds
#: nothing: it flattens already-registered inputs to answer `used_by`, and it already degrades to
#: `None` on every other failure, so a slow read is better abandoned than waited on.
_USAGE_TIMEOUT_S: Final[float] = 60.0

#: Keys `assignments[modifier]` may carry. Anything else is refused rather than ignored: a
#: misspelled `reflectence` silently dropped is a material assigned something other than what was
#: asked for, with no error anywhere -- the failure class this whole project is built against.
_ASSIGNMENT_KEYS = frozenset(
    {
        "type",
        "reflectance",
        "transmittance",
        "specular",
        "roughness",
        "trans_specular",
        "provenance",
        "source",
        "role",
    }
)


# --- nested shapes -----------------------------------------------------------------------------
# Plain models: only a top-level result carries `advisories`.


class _Nested(BaseModel):
    model_config = ConfigDict(extra="forbid")


# R810 moved this from the published text: every field is a `rad_core.manifest.ProvenanceValue`,
# the structural half of "a default is never a measurement"
# (docs/tool-surface.md#provenance-a-default-is-never-a-measurement): a bare `0.20` fails the
# tool's own output schema at the boundary; `tests/test_server.py` holds the cross-cutting form.
# Glass's `transmittance` is withheld by `rad_engine.materials.EmittedMaterial`, so the raw input
# cannot be written into a `glass` primitive by mistake.
class MaterialValues(_Nested):
    """One modifier's optical values, each a `{value, provenance, source}` object, never a bare number.

    A field is `null` when the modifier's type has no such value. For `glass`, `transmittance` is
    always `null`: the transmittance given is converted, and `glass_transmissivity` is the number
    written into the `glass` primitive. For `trans`, `trans_colour` and `trans_transmissivity` are
    the two numbers the `trans` primitive takes, computed from `reflectance` and `transmittance`.
    """

    reflectance: ProvenanceValue | None = None
    transmittance: ProvenanceValue | None = None
    glass_transmissivity: ProvenanceValue | None = None
    trans_colour: ProvenanceValue | None = None
    trans_transmissivity: ProvenanceValue | None = None


# R810 moved this from the published text: `used_by` is the fourth key docs/tool-surface.md §5's
# return block publishes. Omitted rather than `[]` because `[]` would assert "used by nothing" as a
# measured fact -- "a default is never a measurement", applied to a list. `set`/`apply_defaults`
# never establish it (`_listed`'s comment), which the old text did not say; R810 added it.
class MaterialView(_Nested):
    """One modifier as the project holds it.

    `used_by` names the registered geometry inputs whose scene uses this modifier; `[]` means no
    registered geometry uses it. It is omitted, not `null` or `[]`, when the server did not
    establish it: on `set` and `apply_defaults`, on a project with no geometry registered, and
    when Radiance is unavailable or any registered scene cannot be read.
    """

    modifier: str
    type: str
    role: str | None = None
    values: MaterialValues
    used_by: list[str] | None = None


# --- the result branches -----------------------------------------------------------------------


class MaterialsBase(RadResult):
    """What every materials-shaped answer carries. Also the union schema's shared base."""

    project: str
    root: str
    materials: list[MaterialView]

    def payload(self) -> dict[str, Any]:
        """`RadResult.payload`, plus: a `used_by` the server could not establish is **removed**, not
        emitted as `null`.

        The same treatment `RadResult.payload` already gives an advisory's `field`, and for the same
        reason: `null` and absent are different statements, and a key the server has no answer for
        should not be in the document at all. See `MaterialView.used_by`.
        """
        data = super().payload()
        for material in data.get("materials") or []:
            if material.get("used_by") is None:
                material.pop("used_by", None)
        return data


class MaterialsWritten(MaterialsBase):
    """`action="set"` and `action="apply_defaults"`.

    `materials` is every modifier this action has assigned so far, this call's assignments merged
    over the earlier ones; the other action's modifiers are not listed. `file` is the materials
    file written. `defaults` names the table applied (`"lm83"`), and is `null` for `set`.
    `replaced` lists, sorted, the modifiers whose earlier assignment by either action this call
    overwrote, `[]` when none.
    """

    input_id: str
    file: str
    defaults: str | None = None
    replaced: list[str] = Field(default_factory=list)
    """Modifiers whose previously assigned value this call overwrote, sorted.

    Always present, `[]` when nothing was replaced: it is a fact this call measured about itself, and
    an absent key would leave a client unable to tell "replaced nothing" from "did not look". It is
    the answer to *how does a client learn a modifier was replaced* -- see the module docstring's
    accumulation section, and `_merge`.
    """


# R810 moved this from the published text: one branch for both actions, because a second shape
# carrying the identical keys would make the `oneOf` ambiguous rather than more informative.
class MaterialsListed(MaterialsBase):
    """`action="list"`, and `action="inspect"`, which is `list` filtered to one `modifier`.

    `materials` holds both actions' modifiers; an `inspect` of a modifier the project does not
    have returns `materials: []`, not a refusal. `files` lists the registered materials files.
    When any one recorded assignment of an action is one the server would no longer accept, none of
    that action's modifiers are listed; an advisory carrying the underlying refusal's code names
    the input, whose file is still on disk.
    """

    files: list[str]


MaterialsPayload = MaterialsWritten | MaterialsListed | Refusal


class MaterialsResult(RootModel[MaterialsPayload]):
    """What `materials` returns: the `set`/`apply_defaults` or `list`/`inspect` result, or a refusal."""

    @classmethod
    def model_json_schema(cls, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return one_of_schema(super().model_json_schema(*args, **kwargs), MaterialsBase)


# --- argument validation ------------------------------------------------------------------------


class AssignmentRefused(ValueError):
    """An argument this tool rejects on its face. Carries the `field` that says which one.

    An exception rather than a returned refusal so the per-modifier loops below read as one pass;
    `materials` catches it and builds the envelope in one place.

    `code`/`severity`/`field` are the shape `rad_engine.project.ProjectError` established, so
    `rad_mcp.geometry_tool` -- which reaches this module through `replay_materials` and can be
    refused by it -- maps it with the same `getattr` its other refusals go through, rather than
    needing to know this class.
    """

    code = "ARGUMENT_OUT_OF_RANGE"
    severity = "error"

    def __init__(self, message: str, *, field: str) -> None:
        super().__init__(message)
        self.field = field


def _role(value: Any) -> MaterialRole | None:
    """A wire `role` string as the `MaterialRole` member it names.

    *"A **legal** value still arrives over the wire as a plain JSON string, and the wire layer maps
    it onto the matching `MaterialRole` member before it reaches `register_input` rather than passing
    the string through: `MaterialRole` subclasses `str`, so a plain string equal to a role's value
    would otherwise compare correct and then fail identity on the read side by luck rather than by
    contract"* (docs/tool-surface.md#5-materials). Mapping it here is the whole of that sentence.
    """
    if value is None:
        return None
    for member in MaterialRole:
        if value == member.value:
            return member
    raise AssignmentRefused(
        f"role={value!r} is not one of {[r.value for r in MaterialRole]!r}. There is no eighth "
        'role and no role="other" fallback (docs/tool-surface.md#5-materials).',
        field="role",
    )


def _spec(modifier: str, raw: Any) -> dict[str, Any]:
    """One `assignments[modifier]` value, checked shape-first."""
    if not isinstance(raw, dict):
        raise AssignmentRefused(
            f"assignments[{modifier!r}] must be an object describing the material, got "
            f"{type(raw).__name__}.",
            field="assignments",
        )
    unknown = sorted(set(raw) - _ASSIGNMENT_KEYS)
    if unknown:
        raise AssignmentRefused(
            f"assignments[{modifier!r}] has unknown key(s) {unknown!r}; this tool accepts "
            f"{sorted(_ASSIGNMENT_KEYS)!r}. An unrecognised key silently dropped is a material "
            "assigned something other than what was asked for.",
            field="assignments",
        )
    # `rad_core.manifest.ProvenanceValue` refuses this combination too, and is the enforcement that
    # covers every construction site. It raises a pydantic `ValidationError`, which no layer here
    # catches -- it would escape as a JSON-RPC error with no advisory payload, the check-0.4
    # regression class. So the client-facing path refuses it first, coded and with a `field`, and the
    # model behind it stays as the guarantee no *other* producer can slip past.
    if raw.get("provenance") == "default" and raw.get("source") is None:
        raise AssignmentRefused(
            f"assignments[{modifier!r}] declares provenance 'default' with no source. 'default' "
            "means the server substituted the value from a table, so it must cite the clause or "
            "command it came from -- a substitution a client cannot check against its table is "
            "indistinguishable from a measurement "
            "(docs/tool-surface.md#provenance-a-default-is-never-a-measurement). Supply source, or "
            "use provenance 'assumed' for a value you are stating but have not measured.",
            field="source",
        )
    return raw


def _emit_one(modifier: str, raw: Any) -> EmittedMaterial:
    """One assignment, validated and emitted. Every number in the result comes from the layers
    below; nothing is computed here."""
    spec = _spec(modifier, raw)
    material_type = spec.get("type")
    if not isinstance(material_type, str):
        raise AssignmentRefused(
            f"assignments[{modifier!r}] needs a type -- one of plastic, metal, glass or trans "
            "(BSDF is window_group's; docs/tool-surface.md#5-materials).",
            field="type",
        )
    return emit_assignment(
        modifier,
        material_type=material_type,
        reflectance=spec.get("reflectance"),
        transmittance=spec.get("transmittance"),
        specular=spec.get("specular", 0.0),
        roughness=spec.get("roughness", 0.0),
        trans_specular=spec.get("trans_specular", 0.0),
        provenance=spec.get("provenance", "assumed"),
        source=spec.get("source"),
        role=_role(spec.get("role")),
    )


def _geometry_records(loaded: LoadedProject) -> list[InputRecord]:
    """Every registered `geometry` input, in manifest order. Factored out of `_geometry_usage`
    (R722) so `_unused_modifier_advisories` can ask "is any geometry registered at all" without
    paying for `_geometry_usage`'s own `xform` flatten when the answer is "no"."""
    return [record for record in loaded.manifest.inputs if record.kind == "geometry"]


def _geometry_usage(loaded: LoadedProject) -> dict[str, frozenset[str]] | None:
    """`{geometry input id: the modifiers its scene references}`, or `None` if unanswerable.

    What `MaterialView.used_by` is built from. The modifier set of a scene is
    `rad_core.geometry.inventory`'s own answer and `rad_engine.scene.read_scene` is what produces the
    polygons to compute it over -- no `.rad` parsing happens at this layer.

    **`None` is a real answer and is not the same as an empty mapping.** A project with no geometry
    registered, an install that cannot run `xform`, or a scene file that has since moved all mean
    *the server cannot say which surfaces use this material* -- and `used_by` is then omitted from
    the result rather than reported as `[]`, which would assert "nothing uses it" as a measured fact.
    A partial answer is refused for the same reason: one unreadable input out of three would make
    every `used_by` in the result quietly understate itself.
    """
    records = _geometry_records(loaded)
    if not records:
        return None
    try:
        env = discover()
    except RadianceNotFound as exc:
        logger.info("used_by cannot be established: %s", exc)
        return None

    usage: dict[str, frozenset[str]] = {}
    for record in records:
        try:
            parsed = read_scene(
                env,
                [ref.path for ref in record.files],
                cwd=loaded.paths.root,
                timeout=_USAGE_TIMEOUT_S,
            )
        except SceneInlineCommandError as exc:
            # Ruling R33. `inspect` re-reads every registered geometry input, so a hostile file
            # registered once used to re-execute its `!` line on every call -- the reason this
            # read-only-looking action is one of R33's two entry points. Logged at `warning`, not
            # `info`: the others here are "Radiance could not tell us", this one is "a registered
            # input tried to run a command", which is not a degraded answer but an attack.
            if exc.include_tree_too_large:
                # R753: no inline command was found; the include tree is too large to verify.
                logger.warning(
                    "used_by cannot be established from input %s: the include tree under %s is "
                    "too large to verify (%s)",
                    record.id,
                    exc.path,
                    exc.code,
                )
                return None
            logger.warning(
                "used_by cannot be established from input %s: refused an inline command in %s "
                "line %d (%s)",
                record.id,
                exc.path,
                exc.line_number,
                exc.code,
            )
            return None
        except (RadianceError, SceneFileNotFoundError, ValueError) as exc:
            logger.info("used_by cannot be established from input %s: %s", record.id, exc)
            return None
        usage[record.id] = frozenset(polygon.modifier for polygon in parsed.polygons)
    return usage


def _unused_modifier_advisories(loaded: LoadedProject, assigned: Sequence[str]) -> list[Advisory]:
    """R722, owner decision 2026-09-26: a name this call assigned that no registered geometry uses
    is almost always a misspelling -- the intended surface keeps its old material and nothing says
    so. Warned, not refused: materials may be set before geometry exists. Nothing when no geometry
    is registered; `MATERIAL_USAGE_UNCHECKED` when geometry exists but cannot be read."""
    if not _geometry_records(loaded):
        return []
    usage = _geometry_usage(loaded)
    if usage is None:
        message = ("The registered geometry could not be read, so whether each assigned modifier is "
                   "used by any surface was not checked.")
        return [Advisory(code="MATERIAL_USAGE_UNCHECKED", severity="info", field="assignments", message=message)]
    used = frozenset().union(*usage.values())
    unused = [name for name in assigned if name not in used]
    if not unused:
        return []
    message = (f"No registered geometry uses {', '.join(repr(n) for n in unused)}, so "
               f"{'it has' if len(unused) == 1 else 'they have'} no effect on any result. Check the spelling "
               "against materials(action='list').")
    return [Advisory(code="MATERIAL_MODIFIER_UNUSED", severity="warn", field="assignments", message=message)]


def _view(
    material: EmittedMaterial, usage: dict[str, frozenset[str]] | None = None
) -> MaterialView:
    split = material.trans_split
    return MaterialView(
        modifier=material.modifier,
        type=material.material_type,
        role=material.role.value if material.role is not None else None,
        used_by=(
            None
            if usage is None
            else sorted(
                input_id
                for input_id, modifiers in usage.items()
                if material.modifier in modifiers
            )
        ),
        values=MaterialValues(
            reflectance=material.reflectance,
            transmittance=material.transmittance,
            glass_transmissivity=material.glass_transmissivity,
            trans_colour=split.colour if split is not None else None,
            trans_transmissivity=split.transmissivity if split is not None else None,
        ),
    )


# --- the ground consistency check ----------------------------------------------------------------


def _ground_advisory(
    materials_: list[EmittedMaterial], project_ground: ProvenanceValue
) -> Advisory | None:
    """`GROUND_REFLECTANCE_INCONSISTENT` when the modelled ground and the project value disagree.

    Ruling R25 opened `role` on `action="set"` for exactly this: *"a ground polygon assigned its
    reflectance by hand is otherwise invisible to `GROUND_REFLECTANCE_INCONSISTENT` ... that check
    compares the modelled ground material against the project value, and nothing can compare against
    a ground it was never told about."* Nothing had fired it before this function.

    `error`, because the two grounds produce a valid sky matrix and a valid daylight matrix built
    from *different* grounds, and *"nothing in the digest graph can detect that, because both are
    individually consistent with their own inputs"*
    (docs/tool-surface.md#how-provenance-invalidates-a-matrix). The comparison itself is
    `rad_core.materials.ground_reflectance_agrees`, consumed rather than computed here: the band it
    applies is a threshold, and `rad_mcp` carries none (CONTEXT.md, Layers). Fix round 1 moved it
    down after 7b shipped it inline in this module and reported it as a known violation.

    Whether it *refuses* is `_refuses_ground_mismatch`'s question, not this one's.
    """
    for material in materials_:
        if material.role is not MaterialRole.GROUND or material.reflectance is None:
            continue
        modelled = material.reflectance.value
        if ground_reflectance_agrees(modelled, project_ground.value):
            continue
        return Advisory(
            code="GROUND_REFLECTANCE_INCONSISTENT",
            severity="error",
            field="ground_reflectance",
            message=(
                f"modifier {material.modifier!r} models the ground at {modelled:g} while the "
                f"project's ground reflectance is {project_ground.value:g} "
                f"({project_ground.provenance}). gensky, gendaylit and gendaymtx are each given the "
                "project value as -g, and the octree gets this polygon, so the study would carry two "
                "different grounds. "
                'Set them to agree: project(action="update", ground_reflectance=...), or assign '
                "this modifier the project's value."
            ),
        )
    return None


def _refuses_ground_mismatch(project_ground: ProvenanceValue) -> bool:
    """Whether a ground disagreement stops the call, or only qualifies it.

    docs/tool-surface.md#ground-reflectance-is-one-project-value-passed-to-every-sky-generator:
    applying LM-83's 10 % ground *"is refused unless the project value is 0.10 **or unset**. Unset,
    it resolves to `0.20` with `provenance: "default"` and source `gensky -defaults` -- the binary
    default made explicit and labelled, rather than inherited silently."*

    So the discriminator is **whether anyone chose the project value**, not what it is. A project
    whose ground reflectance is still the labelled binary default has expressed no opinion for a
    material assignment to contradict, and refusing there would make the LM-83 table unusable on
    every project that has not first been told the same number twice. A project whose value was
    *given* -- `assumed` or `measured` -- has, and a material that contradicts it is the two-grounds
    study this whole section exists to prevent.

    Either way the disagreement is reported at `error`: on the unset path it rides a successful
    result, which is the same split `radiance_status` already makes for an install it found and
    whose library directory is missing -- *"`is_error` describes the call; an `error`-severity
    advisory describes a value"* (`rad_mcp.envelope`).
    """
    return project_ground.provenance != "default"


class GroundReflectanceRefused(ValueError):
    """The modelled ground contradicts a project ground reflectance someone chose (see `_refuses_ground_mismatch`)."""

    code = "GROUND_REFLECTANCE_INCONSISTENT"
    severity = "error"
    field = "ground_reflectance"


def ground_finding(
    loaded: LoadedProject, materials_: list[EmittedMaterial]
) -> tuple[Advisory | None, bool]:
    """`(advisory, refuses)` for an import's emitted materials against `loaded`'s project ground.

    Composes the two functions `_write` already runs for `materials(action="set")`
    (`_ground_advisory`, `_refuses_ground_mismatch`) so `geometry(action="add")` gets the identical
    finding for the identical reason, rather than a second ground check drifting from this one."""
    project_ground = loaded.manifest.project.ground_reflectance
    advisory = _ground_advisory(materials_, project_ground)
    return advisory, advisory is not None and _refuses_ground_mismatch(project_ground)


# --- reading a previous call back ----------------------------------------------------------------


def _recorded_assignments(record: InputRecord) -> dict[str, Any]:
    """The `assignments` argument the call that registered `record` was made with.

    `rad_core.manifest.ToolCall.arguments` is the manifest's record of that call, so this is the
    manifest reporting what it was told rather than a second copy of the values kept alongside it.
    An input registered by something other than this tool contributes nothing.
    """
    call = record.registered_by
    if call is None or call.tool != "materials":
        return {}
    assignments = call.arguments.get("assignments")
    return assignments if isinstance(assignments, dict) else {}


def _recorded_for(loaded: LoadedProject, input_id: str) -> dict[str, Any]:
    """The `assignments` already recorded under `input_id`, or `{}` if there is no such input yet."""
    for record in loaded.manifest.inputs:
        if record.id == input_id and record.kind == "materials":
            return _recorded_assignments(record)
    return {}


def _merge(
    previous: dict[str, Any], incoming: dict[str, Any]
) -> tuple[dict[str, Any], list[str]]:
    """`(the union this call will write, the modifiers it overwrote)`.

    The whole of the accumulation model -- see the module docstring for why it exists and what
    shipping the other model cost. `incoming` wins per modifier, because re-assigning a modifier is
    how a material is changed; every other modifier is carried forward untouched, which is the part
    that was missing.

    **The union is taken per *field*, not per modifier, and that distinction is the fix for a second
    instance of the same defect.** An earlier version returned `{**previous, **incoming}`, replacing
    each modifier's spec object wholesale -- so a second `set` naming a modifier the first had tagged
    `role="ground"`, without repeating the tag, silently dropped it. Measured: `role='ground'` became
    `role=None` with a **byte-identical digest**, because `role` sits in no digest by design (ruling
    R26) and is not a primitive argument, so nothing could invalidate and nothing announced it --
    `describe` still reported `valid=1 invalid=0`. Downstream, `_input_role` recomputed `None`,
    `REQUIRED_EDGES["dmx"]` lost the ground materials input, and `geometry_tool._ground_modifiers`
    stopped scoping the enclosure check, turning a valid scene into `NOT_ENCLOSED` -- *"this
    project's worst failure class after a silent wrong answer"* in `geometry_tool`'s own words.

    Five other keys had the same exposure and the same silence (`provenance`, `source`, `specular`,
    `roughness`, `trans_specular`), each of which `_emit_one` reads with a silent default, so a
    re-`set` omitting `provenance` quietly downgraded `measured` to `assumed`.

    The module docstring's invariant is stated over *work*, not over modifier names: *"there is no
    code path that writes a file holding less than the union"*. A spec that lost a field held less
    than the union while satisfying the letter of the modifier-set reading. Merging per field is what
    makes the stated invariant true as written.

    `replaced` is still per modifier, and deliberately: it answers *"the value you had for this
    modifier is gone"*, which remains exactly true of a field-level overwrite.
    """
    replaced = sorted(set(previous) & set(incoming))
    merged: dict[str, Any] = dict(previous)
    for modifier, spec in incoming.items():
        earlier = merged.get(modifier)
        # Only merge when both sides are specs. A non-dict on either side is malformed input that
        # `_spec` refuses by itself, and quietly unioning it here would hide that refusal.
        merged[modifier] = (
            {**earlier, **spec}
            if isinstance(earlier, dict) and isinstance(spec, dict)
            else spec
        )
    return merged, replaced


def replay_materials(record: InputRecord) -> list[EmittedMaterial]:
    """Every material `record`'s registering call assigned, re-derived through the same engine call
    that wrote the file.

    Public because `rad_mcp.geometry_tool` needs the same answer for two things `oconv` correctness
    depends on: the `known_modifiers` set an import is validated against, and the per-file modifier
    sets `rad_engine.materials.scene_files_with_materials` compares to refuse the same modifier
    defined in two materials files. One derivation, used by both readers.

    Re-derived rather than stored: the file on disk and this list are then two renderings of one
    validated input, and cannot disagree. A recorded call that no longer validates -- because the
    layers below tightened a rule since -- is skipped rather than reported as a material, and says so
    in the log; reporting a value the current code would refuse to write is the worse of the two.
    """
    action = record.registered_by.action if record.registered_by else None
    try:
        return _emit_recorded(action, _recorded_assignments(record))
    except (MaterialsError, MaterialsEmissionError, AssignmentRefused) as exc:
        # **Not silent.** The caller is handed the failure so it can put an advisory on the result:
        # a `list` that answered "this project has no materials" while the `.rad` file on disk held
        # two would be a false statement the client has nothing to act on, and the same call path
        # feeds `geometry`'s `known_modifiers`, so a silently dropped input silently narrows what an
        # import will accept (fix round 2, finding I3).
        logger.warning("input %s no longer validates: %s", record.id, exc)
        raise


def _emit_recorded(action: str | None, assignments: dict[str, Any]) -> list[EmittedMaterial]:
    """Emit one recorded call's assignments through the same engine call that first wrote them.

    Re-derived rather than stored: the file on disk and this list are then two renderings of one
    validated input, and cannot disagree. The one derivation every reader shares -- `list`/`inspect`
    through `replay_materials`, `geometry`'s `known_modifiers`, and `_withdraw` when it rewrites the
    other action's file.
    """
    if action == "apply_defaults":
        # Through `_spec` rather than straight to `.get`, so a hand-edited manifest whose
        # `assignments` values are not objects is refused by this module's own vocabulary instead
        # of raising `AttributeError` from inside a comprehension.
        roles = {m: _role(_spec(m, raw).get("role")) for m, raw in assignments.items()}
        return list(emit_defaults(apply_defaults(roles)).materials)
    return [_emit_one(modifier, spec) for modifier, spec in assignments.items()]


def set_assignments_for_import(
    loaded: LoadedProject, incoming: dict[str, dict[str, Any]]
) -> tuple[dict[str, Any], list[str], list[EmittedMaterial]]:
    """A material map's rows folded over action `set`'s recorded assignments, **each named modifier
    replaced whole**: a key deleted from a map row is gone (design §6.4). Every other modifier is
    carried unchanged. Validated by emitting the whole union.

    Deliberately not `_merge`, which unions *per field* so a re-`set` omitting a field cannot drop it
    silently (see the module docstring and `_merge`'s own docstring for why that matters for a hand
    call). An import's material map is a whole document describing the modifier as of that import;
    replacing it whole is the union `geometry(action="add")` needs, and mixing an import's fields
    into a hand-typed modifier's `role` or `provenance` would attribute a value to the wrong source.
    """
    previous = _recorded_for(loaded, _FILES["set"][0])
    merged = {**previous, **incoming}
    replaced = sorted(set(previous) & set(incoming))
    return merged, replaced, [_emit_one(name, raw) for name, raw in merged.items()]


class InputStillBuiltFrom(ValueError):
    """Deregistering this input would leave a sealed artifact pointing at nothing.

    `rad_core.manifest` walks `built_from` on every read, so an artifact naming an id that is no
    longer in `inputs` makes the whole document unevaluable: `ManifestError: artifact 'oct1' is built
    from unknown id ...`, and from then on **every** call that opens the project fails. Measured end
    to end -- `project(action="describe")` came back as a bare `ToolError` with no payload at all,
    which is check 0.4's own failure mode on top of the corruption (fix round 3, finding N2).

    So the removal is refused *before* the record is touched. `ARTIFACT_EDGES_INCOMPLETE` is the
    allocated code and it is the right one rather than a near miss: ruling R6 allocated it for *"an
    artifact whose `built_from` omits an edge its kind requires ... Refused rather than sealed: an
    artifact with a missing edge is reported valid and reused forever, which is worse than one that
    was never built."* Removing the input under a sealed artifact produces exactly that artifact,
    one step later, and the same sentence is the reason to refuse.

    Shared by both removal paths -- `materials`' withdrawal and `geometry(action="remove")` -- which
    had the identical shape and the identical corruption. The guard lives here rather than in
    `rad_engine.project` only because that module is closed to this task; see this class's mention in
    task-7b-report.md for the recommendation that it move down beside `check_edges`, which already
    owns the artifact graph and would then protect every caller rather than these two.
    """

    code = "ARTIFACT_EDGES_INCOMPLETE"
    severity = "error"
    field = "project"


def refuse_if_built_from(manifest: Any, input_ids: Iterable[str]) -> None:
    """Raise `InputStillBuiltFrom` if any sealed artifact names one of `input_ids` in `built_from`.

    Read-only over the manifest already in hand: no extra IO, and safe to call inside the lock.
    """
    wanted = set(input_ids)
    blocked = {
        input_id: sorted(
            artifact.id for artifact in manifest.artifacts if input_id in artifact.built_from
        )
        for input_id in sorted(wanted)
    }
    named = {k: v for k, v in blocked.items() if v}
    if not named:
        return
    detail = "; ".join(f"{input_id!r} by {', '.join(ids)}" for input_id, ids in named.items())
    raise InputStillBuiltFrom(
        f"cannot deregister {detail} -- those artifacts record it in built_from, and an artifact "
        "pointing at an input that is not there makes the whole manifest unreadable, not just that "
        "artifact. Remove or rebuild the artifacts first."
    )


#: The withdrawal, in two phases.
#:
#: Remove `taken` from the *other* action's file and record, so no modifier is defined twice.
#:
#:     **The half of the accumulation model that lets a file shrink** (fix round 2, defect C3). Without
#:     it, `set` and `apply_defaults` could each be made to define the same modifier -- two successful
#:     calls, no advisory -- and `rad_engine.materials.scene_files_with_materials` then refused **every**
#:     subsequent `geometry(action="add")` for the life of the project. That refusal is correct: `oconv`
#:     takes the last definition, exits 0 and says nothing, so which reflectance applies would depend on
#:     argument order. What was wrong is that nothing could undo it: `materials` has no `remove`, and
#:     `_merge` only ever unions, so neither file could shrink and the project was recoverable only by
#:     editing files outside the server.
#:
#:     If the withdrawal empties the other file, its **input is deregistered and the file removed**
#:     rather than left as an empty `.rad`. An input with no materials in it contributes a constant to
#:     every digest downstream of it -- the same reason an empty `assignments` object is refused at the
#:     door.
#:
#:     Runs inside the caller's `Runtime.modifying` lock, against the project the caller re-opened
#:     there.


def _contain_materials_destination(target: Path, *, roots: tuple[Path, ...]) -> None:
    """Refuse a materials `.rad` destination whose parent chain escapes `roots`, before any write.

    **The tool-layer half of ruling R48's split**, matching `paths.contain_octree_destination`
    and `server._commit_grid_files`: `rad_engine.materials.write_materials` owns the final-component
    guard (`O_NOFOLLOW`, `S_ISREG`, via `open_regular_file`) because that is a property of the open,
    and containment stays here because only this layer holds the allowed roots.

    Both guards are needed and neither substitutes for the other. `O_NOFOLLOW` does not see a
    *directory* symlink partway up the chain -- `05_materials -> /somewhere/else` puts a newly
    created file entirely outside every root with no symlink at the final component at all -- and
    containment cannot refuse a FIFO planted legitimately *inside* the project.

    Checked before `write_materials`, which does the `mkdir`: a check after it would let a hostile
    chain bring a directory tree into existence outside the roots before anything refused. Raises
    `PathOutsideRoot`, which this tool's handler chain shapes into a `PATH_OUTSIDE_ROOT` refusal.
    """
    if not resolved_parent_within(target, roots):
        raise PathOutsideRoot(
            f"the materials file would be written to {target.parent}, which resolves outside every "
            "allowed root. Nothing has been written. A directory on that path is a symlink leading "
            "out of the workspace; remove it and re-run."
        )


def _withdraw_files(
    survey: LoadedProject, paths: ProjectPaths, *, other_action: str, taken: list[str]
) -> dict[str, Any] | None:
    """Rewrite the other action's `.rad` for a withdrawal, and answer what its record should become.

    **File IO only; the manifest is not touched here.** Split from `_withdraw_record` so that every
    write in `_write` happens before any manifest read that a save is based on -- otherwise the
    freshest read is the one *before* the writes and a competing update landing during them is
    erased, which is the lost update `Runtime.modifying` exists to prevent (found by
    `test_a_write_does_not_erase_one_that_landed_after_this_call_read` when the ordering moved for
    N1).

    Returns the remaining assignments, or `None` when nothing is left and the input should be
    deregistered instead.
    """
    other_id, other_filename = _FILES[other_action]
    remaining = {
        modifier: spec
        for modifier, spec in _recorded_for(survey, other_id).items()
        if modifier not in taken
    }
    if not remaining:
        # Refused *before* anything is written or removed -- see `InputStillBuiltFrom`. A withdrawal
        # that emptied a file an artifact was built from used to deregister the input and make the
        # project unreadable from the next call onward.
        refuse_if_built_from(survey.manifest, [other_id])
        # Contained for the same reason the rewrite below is, and it was missed because R48's rule
        # was worded as "every filesystem *open*" -- `unlink` opens nothing. It still resolves a
        # directory symlink in the parent chain, which `O_NOFOLLOW` cannot see (`safe_io`), so an
        # unguarded unlink here deletes an arbitrary file outside every root and *then* refuses.
        # Placed after `refuse_if_built_from` so refusal ordering is unchanged.
        other_target = paths.materials / other_filename
        _contain_materials_destination(other_target, roots=current().roots)
        other_target.unlink(missing_ok=True)
        return None

    other_target = paths.materials / other_filename
    _contain_materials_destination(other_target, roots=current().roots)
    write_materials(other_target, _emit_recorded(other_action, remaining))
    return remaining


def _withdraw_record(
    current: LoadedProject,
    paths: ProjectPaths,
    *,
    other_action: str,
    remaining: dict[str, Any] | None,
    save: bool = True,
) -> None:
    """Bring the other action's manifest record in line with the file `_withdraw_files` just wrote.

    Applied against a project opened **after** every write in this call, so the document it saves
    carries anything another writer committed in between.

    `save=False` leaves `manifest.json` untouched -- `current.manifest` still carries the mutation --
    so `commit_materials_locked`'s caller can fold this change and its own into one save.
    """
    other_id, other_filename = _FILES[other_action]
    if remaining is None:
        keep = [record for record in current.manifest.inputs if record.id != other_id]
        if len(keep) != len(current.manifest.inputs):
            current.manifest.inputs = keep
            if save:
                save_manifest(paths, current.manifest)
        return

    arguments: dict[str, Any] = {"assignments": remaining}
    if other_action == "apply_defaults":
        arguments["defaults"] = "lm83"
    register_input(
        current,
        input_id=other_id,
        kind="materials",
        files=[paths.materials / other_filename],
        role=_input_role(_emit_recorded(other_action, remaining)),
        registered_by=ToolCall(tool="materials", action=other_action, arguments=arguments),
        save=save,
    )


def _input_role(materials_: list[EmittedMaterial]) -> MaterialRole | None:
    """`InputRecord.role` for a materials input -- ruling R9's channel.

    `REQUIRED_EDGES["dmx"]` requires the materials input *whose role is the ground*, so an input
    holding a ground modifier is tagged as such. This is `InputRecord.role`, and it is **not**
    `geometry`'s `role` -- see `rad_mcp.geometry_tool` and ruling R32.
    """
    return (
        MaterialRole.GROUND
        if any(material.role is MaterialRole.GROUND for material in materials_)
        else None
    )


# --- the tool -------------------------------------------------------------------------------


def materials(
    action: Annotated[
        Action,
        Field(
            description=(
                "which operation: \"set\", \"apply_defaults\", \"list\" or \"inspect\". The tool "
                "description says what each one does."
            )
        ),
    ],
    project: Annotated[
        str | None,
        Field(description="the project handle. Required for every action."),
    ] = None,
    assignments: Annotated[
        dict[str, Any] | None,
        Field(
            description=(
                "modifier -> {type, reflectance|transmittance, specular, roughness, provenance, "
                "source, role}. `type` is one of plastic, metal, glass, trans -- BSDF binding "
                "belongs to window_group, which validates it against the Klems basis. On "
                "\"apply_defaults\" only `role` is read, and it says which table row each modifier "
                "takes. `role` is one of floor, wall, ceiling, furniture, ground, "
                "exterior_vertical, trees. It says which surface a modifier *is*; it never sets a "
                "reflectance by itself."
            )
        ),
    ] = None,
    defaults: Annotated[
        Literal["lm83"] | None,
        Field(
            description=(
                "REQUIRED on \"apply_defaults\", and \"lm83\" is the only table. There is no "
                "defaults=\"typical\": an unsourced default table is a confidently wrong number "
                "that ships."
            )
        ),
    ] = None,
    modifier: Annotated[
        str | None,
        Field(description="REQUIRED on \"inspect\" -- which modifier to report."),
    ] = None,
) -> MaterialsResult:
    """Set and inspect surface optical properties, with provenance on every value.

    Use it to give each modifier its optical properties, or to read back what the project has been
    given.

    Every value this tool reports is an object carrying `provenance` -- `measured`, `assumed` (you
    gave it, unmeasured) or `default` (the server substituted it) -- never a bare number. A
    `default` is an assumption about the building, not a property of it, and must be reported as one.

    DESTRUCTIVE ACTIONS: `set` and `apply_defaults` both write. Either can take a modifier the other
    action had assigned -- the most recent call owns it -- which rewrites that action's materials
    file, and removes the file and deregisters its input if nothing is left in it. Nothing you put
    on disk is touched; what is removed is a file this server wrote.

    "set" assigns materials to modifiers and writes them as Radiance primitives. "apply_defaults"
    fills unknown reflectances from a sourced table. "list" reports every material this project has
    been given. "inspect" reports one modifier.

    NOTE ground reflectance is a single project value and is set only by
    project(ground_reflectance=...). This tool never takes one. Assigning a modifier role="ground"
    tells the server which polygon is the ground so the two can be compared, and a disagreement
    beyond 0.02 is refused (GROUND_REFLECTANCE_INCONSISTENT) rather than simulated.
    """
    runtime = current()
    try:
        return _dispatch(
            runtime,
            action,
            project=project,
            assignments=assignments,
            defaults=defaults,
            modifier=modifier,
        )
    except AssignmentRefused as exc:
        logger.info("materials(action=%s) refused: %s", action, exc)
        return refuse(
            str(exc),
            [
                Advisory(
                    code="ARGUMENT_OUT_OF_RANGE",
                    severity="error",
                    field=exc.field,
                    message=str(exc),
                )
            ],
        )
    except (MaterialsError, MaterialsEmissionError, InputStillBuiltFrom) as exc:
        # Every refusal `rad_core.materials` and `rad_engine.materials` raise carries a `code`, a
        # `severity` and the `field` it is about, in the shape `ProjectError` established. Mapped
        # here rather than re-worded: the layer that refused is the layer that knows why.
        logger.info("materials(action=%s) refused: %s", action, exc)
        code = getattr(exc, "code", None)
        if code is None:
            return refuse(str(exc))
        return refuse(
            str(exc),
            [
                Advisory(
                    code=code,
                    severity=getattr(exc, "severity", "error"),
                    field=getattr(exc, "field", None),
                    message=str(exc),
                )
            ],
        )
    except (ProjectError, PathOutsideRoot, RootsNotConfigured) as exc:
        logger.info("materials(action=%s) refused: %s", action, exc)
        return refuse_engine_error(exc)
    except NotARegularFileError as exc:
        # The materials `.rad` destination is a FIFO, a device, or a symlink at its final
        # component, refused by `write_materials`'s `open_regular_file` (R48's primitive, adopted
        # here 2026-08-10). Coded `PROJECT_PATH_NOT_A_REGULAR_FILE` under ruling R58, the same code
        # `run_annual` and `results` carry for the identical condition at their own write paths.
        #
        # **Above the `OSError` clause, which it subclasses.** Below it this would be swallowed
        # into the generic filesystem refusal and lose its code -- the same ordering hazard the
        # `RadianceTimeoutError` clauses document.
        logger.info("materials(action=%s) refused: %s", action, exc)
        message = (
            f"the materials file could not be written: {exc} Nothing has been written. Remove "
            "whatever occupies that path and re-run."
        )
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
        # `O_NOFOLLOW` surfaces a symlink as `ELOOP` before any descriptor exists, so the clause
        # above never sees that case -- the asymmetry `annual_tool` and `results_tool` both
        # document, and which they answer by coding their `OSError` clause too.
        #
        # **This one deliberately does not**, because this `try` wraps the whole of `_dispatch`, so
        # every unrelated filesystem refusal lands here as well -- a full disk, a read-only mount, a
        # permission the server does not hold, a manifest that cannot be read.
        #
        # **Corrected 2026-08-10.** This paragraph used to justify the asymmetry with "theirs wraps
        # a single write; this wraps the whole of `_dispatch`", which was false of both: `run_annual`
        # and `results` each wrap a whole `_dispatch` too. The reasoning below was right and the
        # stated reason for the difference was not -- and the false premise is what made
        # `run_annual`'s clause look deliberate rather than a defect, which the whole-branch audit
        # found: it coded every `OSError` from ~170 lines, telling a caller with a full disk to
        # remove a symlink nobody planted. That clause is now gated on `errno.ELOOP`; `results`
        # was measured and needs no gate, because `rad_engine.results` wraps its opens in
        # `MatrixFileUnreadableError` and they never reach the terminal clause.
        # Coding all of those `PROJECT_PATH_NOT_A_REGULAR_FILE` would be confidently wrong far more
        # often than it would be right. Narrowing the `try` to the write alone is the change that
        # would let this be coded; that is a larger refactor than R48's adoption and is not smuggled
        # in here.
        #
        # The symlink case was therefore reported as prose until R830 (below), a real, named gap rather
        # than an oversight: `materials` is the one adopter where the parent-chain guard
        # (`_contain_materials_destination`) catches the *directory* symlink vector first, so what
        # reaches here is only a symlink at the final component inside an otherwise-contained
        # directory -- refused correctly, just without a code to branch on.
        #
        # **This claim is only true because all three call sites are guarded** -- `_write`, and both
        # branches of `_withdraw_files`. Until 2026-08-10 the withdrawal's unlink branch was not,
        # and this comment was consequently false: that path deleted through a directory symlink
        # before refusing. Whole-branch audit Critical; if a fourth mutation site is ever added
        # unguarded, this paragraph goes stale again rather than merely incomplete.
        #
        # **R830, register N-102: gated on `ELOOP`, as `run_annual`'s clause is.** `O_NOFOLLOW`
        # raises `ELOOP` for a symlink at the final component and for nothing a full disk, a
        # read-only mount or a withheld permission produces, so that one errno is coded
        # `PROJECT_PATH_NOT_A_REGULAR_FILE` and every other `OSError` keeps the prose below.
        if exc.errno == errno.ELOOP:
            logger.info("materials(action=%s) refused: %s", action, exc)
            message = (
                f"materials(action={action!r}) refused: a file it opens is a symbolic link, which "
                f"this server does not follow: {exc}. Nothing was written through it. Remove the "
                "link and re-run."
            )
            return refuse(
                message,
                [Advisory(code="PROJECT_PATH_NOT_A_REGULAR_FILE", severity="error", field="project",
                          message=message)],
            )
        logger.warning("materials(action=%s) failed on the filesystem: %s", action, exc)
        return refuse(
            f"materials(action={action!r}) could not complete: {exc}. Nothing about the arguments "
            "is wrong -- the filesystem refused the operation."
        )


def _dispatch(
    runtime: Runtime,
    action: Action,
    *,
    project: str | None,
    assignments: dict[str, Any] | None,
    defaults: str | None,
    modifier: str | None,
) -> CallToolResult:
    if (refusal := refuse_missing(f"materials(action={action!r})", project=project)) is not None:
        return refusal

    loaded = runtime.open(project)

    if action == "list":
        return _listed(loaded, modifier=None)
    if action == "inspect":
        if (
            refusal := refuse_missing(f"materials(action={action!r})", modifier=modifier)
        ) is not None:
            return refusal
        return _listed(loaded, modifier=modifier)

    if (
        refusal := refuse_missing(f"materials(action={action!r})", assignments=assignments)
    ) is not None:
        return refusal
    if not assignments:
        raise AssignmentRefused(
            f"materials(action={action!r}) was given an empty assignments object, so there is "
            "nothing to write. An input with no materials in it would register a file that "
            "contributes a constant to every digest downstream of it.",
            field="assignments",
        )

    # Everything this action has assigned before, folded under everything this call assigns. See
    # `_merge` and the module docstring: a call that wrote only its own arguments destroyed the
    # earlier ones (fix round 1, defect C1).
    input_id, _ = _FILES[action]
    merged, replaced = _merge(_recorded_for(loaded, input_id), assignments)

    if action == "apply_defaults":
        if (
            refusal := refuse_missing(f"materials(action={action!r})", defaults=defaults)
        ) is not None:
            return refusal
        if defaults != "lm83":
            raise AssignmentRefused(
                f"defaults={defaults!r} is not a table this server has. Only 'lm83' exists, and "
                "there is deliberately no 'typical': an unsourced default table is exactly the "
                "confidently-wrong threshold this project refuses to ship "
                "(docs/tool-surface.md#5-materials).",
                field="defaults",
            )
        roles: dict[str, MaterialRole] = {}
        for name, raw in merged.items():
            spec = _spec(name, raw)
            # `apply_defaults` reads **only** `role`. A `reflectance` passed here would be silently
            # replaced by the table's value -- the caller would ask for 0.30 and get 0.20 with no
            # error anywhere, which is the exact failure class this project exists to prevent. So it
            # is refused, and the refusal names the action that does accept it.
            extra = sorted(set(spec) - {"role"})
            if extra:
                raise AssignmentRefused(
                    f"assignments[{name!r}] carries {extra!r}, and apply_defaults reads only role: "
                    "every value it writes comes from the LM-83 table. A value passed here would be "
                    'replaced by the table\'s without a word. Use action="set" to state a value you '
                    "know.",
                    field="assignments",
                )
            role = _role(spec.get("role"))
            if role is None:
                raise AssignmentRefused(
                    f'assignments[{name!r}] has no role, and apply_defaults has nothing to look up '
                    "without one: the LM-83 table is indexed by role, and guessing a modifier's "
                    "role from its name is how an exterior wall gets a ceiling's 70 %.",
                    field="role",
                )
            roles[name] = role
        applied = apply_defaults(roles)
        emitted = emit_defaults(applied)
        return _write(
            runtime,
            loaded,
            action=action,
            materials_=list(emitted.materials),
            warnings=list(emitted.warnings),
            arguments={"assignments": merged, "defaults": defaults},
            defaults=defaults,
            replaced=replaced,
            assigned=sorted(assignments),
        )

    emitted_set = [_emit_one(name, raw) for name, raw in merged.items()]
    return _write(
        runtime,
        loaded,
        action=action,
        materials_=emitted_set,
        warnings=[w for material in emitted_set for w in material.warnings],
        arguments={"assignments": merged},
        defaults=None,
        replaced=replaced,
        assigned=sorted(assignments),
    )


@dataclass(frozen=True)
class MaterialsCommitted:
    current: LoadedProject
    written: MaterialsFile
    taken: list[str]


def commit_materials_locked(
    runtime: Runtime,
    paths: ProjectPaths,
    *,
    action: str,
    materials_: list[EmittedMaterial],
    arguments: dict[str, Any],
    save: bool,
) -> MaterialsCommitted:
    """Every write of a materials call, then every manifest mutation, in `_write`'s proven order.

    **The caller holds `runtime.modifying(paths)`** -- this function takes no lock, so `geometry` can
    commit materials and its own record under one lock with one manifest save (Phase 10 design §5.9).
    With `save=False` `manifest.json` is untouched and `current.manifest` holds the change; the caller
    saves it.

    **`save=False` defers only the manifest write. The filesystem side is already final.** By the
    time this returns, `_withdraw_files` has already unlinked or rewritten the other action's `.rad`
    file and `write_materials` has already written this call's claim -- neither has a rollback. So a
    caller passing `save=False` must call `save_manifest` on `current.manifest` before releasing
    `runtime.modifying(paths)`, and must not refuse or return early between this call and that save:
    doing either leaves the materials files and `manifest.json` permanently disagreeing (a withdrawn
    modifier gone from the file the manifest still says defines it, or a newly written file the
    manifest never learns about).
    """
    input_id, filename = _FILES[action]
    other_action = _OTHER_ACTION[action]

    # Why every open below runs while the caller's lock is held: see `_write`, at its
    # `with runtime.modifying(paths):`.
    #
    # **`taken` is computed inside the lock, and that placement is the whole of
    # it.** Derived from `loaded` -- opened in `_dispatch` before the lock -- a modifier another
    # writer added to the other action's file in between was never in `taken`, so it was never
    # withdrawn, and the call left it defined in *both* files reporting `replaced: []` and no
    # error: C3's lockout state, re-created by the very code that fixed it (fix round 3,
    # finding N1). Measured under an interleaved writer before this moved.
    #
    # **The withdrawal now runs before the claiming write, for the same reason.** With the claim
    # committed first, a withdrawal that failed part way -- `write_materials` hitting a full disk
    # -- left the claim on disk and the other file still defining the modifier, which is the same
    # both-files state arrived at through partial failure instead of through interleave.
    # Withdrawing first inverts which half survives: a failure between the two leaves the
    # modifier defined in *neither* file, and `register_input`'s own rollback (`_commit` restores
    # the in-memory manifest on any exception) keeps the manifest matching. That is a materials
    # file missing an entry the caller asked for -- recoverable by repeating the same call, which
    # is what the caller was doing anyway -- rather than a project that refuses every subsequent
    # `geometry(action="add")`. Neither ordering is atomic; this is the one whose partial state
    # is the recoverable one.
    survey = open_project(paths.root, radiance_version=runtime.launcher.version)
    taken = sorted(
        set(_recorded_for(survey, _FILES[other_action][0])) & set(arguments["assignments"])
    )

    remaining: dict[str, Any] | None = None
    if taken:
        remaining = _withdraw_files(survey, paths, other_action=other_action, taken=taken)
    target = paths.materials / filename
    _contain_materials_destination(target, roots=runtime.roots)
    written = write_materials(target, materials_)

    # **Every write above, every manifest mutation below.** `register_input` saves the whole
    # document with no compare-against-disk, so the read it is based on has to be the latest one
    # this call takes -- a read from before the writes would erase an update another writer
    # committed during them, which is the lost update `Runtime.modifying` exists to prevent.
    current = open_project(paths.root, radiance_version=runtime.launcher.version)
    if taken:
        _withdraw_record(current, paths, other_action=other_action, remaining=remaining, save=save)
    register_input(
        current,
        input_id=input_id,
        kind="materials",
        files=[written.path],
        role=_input_role(materials_),
        registered_by=ToolCall(tool="materials", action=action, arguments=arguments),
        save=save,
    )
    return MaterialsCommitted(current=current, written=written, taken=taken)


def _write(
    runtime: Runtime,
    loaded: LoadedProject,
    *,
    action: str,
    materials_: list[EmittedMaterial],
    warnings: list[Any],
    arguments: dict[str, Any],
    defaults: str | None,
    replaced: list[str],
    assigned: Sequence[str],
) -> CallToolResult:
    """Run the ground check, commit under the lock, and answer.

    `materials_` is the **accumulated** set for this action -- everything it has ever assigned, with
    this call's assignments folded over the earlier ones (`_merge`). The file is re-emitted from that
    union, so it can never hold less than it did before the call.

    The ground check runs **before** anything is written. A refusal that had already written the
    file would leave the project holding a ground material the same call reported as unusable.

    The write itself is `commit_materials_locked`, shared with `geometry` so the two can commit under
    one lock and one manifest save (Phase 10 design §5.9); this call always saves (`save=True`).

    `assigned` is R722's channel: the keys of **this call's** `assignments`, not `materials_`'s
    accumulated set -- `_unused_modifier_advisories` warns about a name *this call* just assigned,
    not about every modifier the action has ever written. Both actions pass it: `apply_defaults`'
    values come from the LM-83 table, indexed by role, but the modifier names they are written under
    are its `assignments` keys, which a caller types as `set`'s are (R722, 2026-09-27).
    """
    project_ground = loaded.manifest.project.ground_reflectance
    ground = _ground_advisory(materials_, project_ground)
    if ground is not None and _refuses_ground_mismatch(project_ground):
        return refuse(ground.message, [ground])

    input_id, _ = _FILES[action]
    paths = ProjectPaths(loaded.paths.root)

    # **Opened inside both locks, not outside them.** `register_input` writes the whole manifest with
    # no compare-against-disk, so what loses an update is the interval between the read and the
    # write; a lock taken around only the write serialises two writers who have already read the
    # same stale document (`Runtime.modifying`, and `project_tool._update`'s own note).
    with runtime.modifying(paths):
        committed = commit_materials_locked(
            runtime, paths, action=action, materials_=materials_, arguments=arguments, save=True,
        )

    unused = _unused_modifier_advisories(loaded, assigned)

    return emit(
        MaterialsWritten(
            project=loaded.paths.name,
            root=str(loaded.paths.root),
            materials=[_view(material) for material in materials_],
            input_id=input_id,
            file=str(committed.written.path),
            defaults=defaults,
            replaced=sorted(set(replaced) | set(committed.taken)),
            advisories=(
                warnings_to_wire(warnings) + ([ground] if ground is not None else []) + unused
            )
            or None,
        )
    )


def _listed(loaded: LoadedProject, *, modifier: str | None) -> CallToolResult:
    # `used_by` is established on the *inspection* actions only. `set`/`apply_defaults` leave it
    # omitted -- "this call did not establish it", which is exactly what an absent key means here --
    # so assigning a material never depends on a Radiance install or pays for flattening every
    # registered scene. (R722: `set` and `apply_defaults` now *also* read the registered geometry, in
    # `_write`, but only to warn on a modifier name nothing uses -- `used_by` on those actions' own
    # responses stays omitted, and this function's own read has nothing to do with that warning.)
    usage = _geometry_usage(loaded)
    records = [record for record in loaded.manifest.inputs if record.kind == "materials"]
    views: list[MaterialView] = []
    files: list[str] = []
    advisories: list[Advisory] = []
    for record in records:
        files.extend(ref.path for ref in record.files)
        try:
            replayed = replay_materials(record)
        except (MaterialsError, MaterialsEmissionError, AssignmentRefused) as exc:
            # **Reported, never silently omitted** (fix round 2, finding I3). A recorded call that no
            # longer validates -- because a layer below tightened a rule, or the manifest was edited
            # -- used to be dropped with only a `logger.warning`, so `list` answered "this project
            # has no materials" while the `.rad` file on disk held two. That is a false statement the
            # client is given nothing to act on, and the log line reaches an operator, not the model.
            #
            # The value is still not reported -- the docstring's original reasoning holds, and
            # showing a number the current code would refuse to write is the worse of the two
            # failures. What changes is that the omission is *announced*.
            #
            # **The code is the underlying refusal's own**, relayed rather than chosen: whatever made
            # the recorded assignment invalid already carries an allocated code that is true of it
            # (`ARGUMENT_OUT_OF_RANGE` for a value out of range, `ENERGY_BALANCE_VIOLATED` for rho +
            # tau > 1, and so on). No code is invented here, and none is needed: the register has one
            # for every way an assignment can fail, because every one of them is a way a *live*
            # assignment can fail too.
            advisories.append(
                Advisory(
                    code=getattr(exc, "code", None) or "ARGUMENT_OUT_OF_RANGE",
                    severity=getattr(exc, "severity", "error"),
                    field=getattr(exc, "field", None),
                    message=(
                        f"input {record.id!r} records an assignment this server would no longer "
                        f"accept, so its materials are not listed: {exc} The file it wrote is still "
                        "on disk and still reaches oconv. Re-assign the affected modifier through "
                        'materials(action="set") to bring the record back into agreement.'
                    ),
                )
            )
            continue
        views.extend(
            _view(material, usage)
            for material in replayed
            if modifier is None or material.modifier == modifier
        )

    if modifier is not None and not views:
        # Not a refusal: "this project has no such modifier" is a true, useful answer, and the empty
        # list says it. `ARGUMENT_OUT_OF_RANGE` is for an argument rejected on its face, and a
        # modifier name is not wrong on its face -- it is simply not here yet.
        logger.info("materials(action='inspect') found no modifier %r", modifier)
    if any(
        value is not None and value.provenance == "default"
        for view in views
        for value in (view.values.reflectance, view.values.transmittance)
    ):
        # `DEFAULTS_SUBSTITUTED` fires once per *result* that contains a default, not once per
        # modifier: the finding is "this result contains at least one default"
        # (`rad_core.materials.apply_defaults`'s own reasoning, applied to the read side, where
        # `apply_defaults`'s own call-level warning is not in scope).
        advisories.append(
            Advisory(
                code="DEFAULTS_SUBSTITUTED",
                severity="warn",
                field="materials",
                message=(
                    "at least one reported value carries provenance 'default' -- the server "
                    "substituted it from a table, and it is an assumption about this building "
                    "rather than a measurement of it."
                ),
            )
        )

    return emit(
        MaterialsListed(
            project=loaded.paths.name,
            root=str(loaded.paths.root),
            materials=views,
            files=sorted(set(files)),
            advisories=advisories or None,
        )
    )
