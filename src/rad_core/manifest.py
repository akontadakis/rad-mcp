"""The project manifest: schema, content digests, and the invalidation graph.

Pure: no Radiance, no subprocesses, no file IO; it defines the manifest's JSON schema and
serialises it in memory. Runs green with no binaries installed. `rad_engine.project` owns the
directory layout and reads/writes `manifest.json`;
`rad_engine` also computes per-file digests and hands them here as data. Specified in
docs/tool-surface.md#the-project-manifest.

**Invalidation is derived from the edges, never from a per-kind rule table.** An artifact is valid
when the digest recomputed from its current `built_from` edges equals the digest stored when it was
built, recomputed transitively. There is deliberately no branch anywhere in this module that names
an artifact kind: the eleven rows of the table in
docs/tool-surface.md#how-provenance-invalidates-a-matrix are a *test* of the graph, not its
implementation. A `if kind == "tmx"` special case would pass those eleven rows and be wrong on the
twelfth situation nobody wrote down.

**How project-level scalars enter the graph.** `ground_reflectance` and `north` change results but
have no file to digest, so they cannot be file inputs -- and giving them a rule of their own is the
per-kind table this module exists to avoid. They are therefore **synthetic inputs**: reserved ids
under the ``project:`` prefix whose digest is computed from the scalar's value, merged into the same
flat ``{input_id: digest}`` map as every file input before the walk starts. An artifact names
``project:ground_reflectance`` in `built_from` exactly as it names ``bsdf:venetian``, and the walk
cannot tell them apart. That is what makes the two ground rows of the table come out opposite ways
round with no code that knows about ground: the project value is an edge of the sky matrix, the
modelled ground polygon's material is a separate `materials` input and an edge of the exterior
octree, so changing one leaves the other's artifacts untouched
(docs/tool-surface.md#ground-reflectance-is-one-project-value-passed-to-every-sky-generator).

Project `units` is deliberately *not* a synthetic input: each input carries its own declared units,
which are already inside that input's content digest, so the conversion that actually reaches
Radiance is already an edge.

Pydantic rather than dataclasses: the manifest is serialised to JSON, round-tripped from disk by
another layer, and reflected into `project(action="describe")`'s result -- so the literal sets
(`units`, input kinds, artifact kinds, provenance) need to be validated on load, not trusted. The
models are mutable because the manifest is a record `rad_engine` loads, amends and writes back.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

__all__ = [
    "DIGEST_EXCLUDED_PARAMETERS",
    "GROUND_REFLECTANCE_DEFAULT",
    "GROUND_REFLECTANCE_DEFAULT_SOURCE",
    "GROUND_REFLECTANCE_INPUT_ID",
    "SCALAR_INPUT_PREFIX",
    "SCHEMA_VERSION",
    "SKY_GROUND_GLOW_RGB",
    "Artifact",
    "ArtifactKind",
    "ArtifactStatus",
    "ArtifactWarning",
    "DanglingEdgeError",
    "FileRef",
    "InputKind",
    "InputRecord",
    "Manifest",
    "ManifestError",
    "Project",
    "Provenance",
    "ProvenanceValue",
    "ReuseSummary",
    "ToolCall",
    "Units",
    "ZoneRecord",
    "combine_file_digests",
    "compute_digest",
    "default_ground_reflectance",
    "evaluate",
    "input_digest",
    "normalise_parameters",
    "recompute_digests",
    "resolve_built_from_digests",
    "resolve_ground_reflectance",
    "reuse_summary",
    "scalar_digest",
    "stale_reason",
]

Units = Literal["m", "cm", "mm", "ft", "in"]

#: Three values only: `measured`, `assumed` (user gave it, unmeasured), `default` (server
#: substituted) -- docs/tool-surface.md#provenance-a-default-is-never-a-measurement.
Provenance = Literal["measured", "assumed", "default"]

InputKind = Literal[
    "geometry", "materials", "window_group", "sensor_grid", "weather", "bsdf",
    # Phase 11a (spec §4, R467, R485): one imported IES type, one set of placed instances, and the
    # project-root source.cal every lit run depends on.
    "luminaire_type", "luminaire_placement", "luminaire_support",
    # Phase 25 C2a (R870): an automated window group's hourly open/closed schedule, the copy
    # `window_group(action="bind_schedule")` writes into `05_bsdf/automated_schedules/`.
    # `SCHEMA_VERSION` does not move, `clock_zone`'s precedent: an old document satisfies the new
    # shape, since it holds no record of this kind. A server from before this kind cannot read a
    # manifest that holds one, as it cannot read one holding `clock_zone`.
    "automated_schedule",
]

ArtifactKind = Literal[
    "octree", "vmx", "dmx", "tmx", "smx", "dsmx", "ill", "hdr", "pts", "amb",
    # Phase 7a Task 13's five, added here **together with** `rad_engine.project.ARTIFACT_DIRECTORY`
    # and their `REQUIRED_EDGES` rows, because `tests/test_project.py::TestLayout::
    # test_every_artifact_kind_has_a_home` asserts `set(get_args(ArtifactKind)) ==
    # set(ARTIFACT_DIRECTORY)` -- a `Literal` member with no matching directory entry fails a
    # shipped test immediately (reproduced by Tasks 9/10/12, which recorded the finding here rather
    # than each rediscovering it). `ArtifactSpec.kind` is typed against this `Literal` but nothing
    # in this module enforces it at runtime, which is why `five_phase_steps` could construct and
    # seal these five before they existed here.
    #
    # **`cdsmx`, not `dsmx`.** `docs/specs/five-phase-method.md` 4.3 names the `Cds` file
    # `directsun.dsmx`, and `dsmx` is already bound (R44(2)) to the 2-phase daylight-coefficient
    # matrix. Following the spec's filename would have put two physically different matrices on one
    # kind, and `ARTIFACT_DIRECTORY`/`REQUIRED_EDGES` are both keyed by kind -- so the collision
    # would have surfaced as the wrong edge requirement, not as a name clash.
    "vdmx", "ddmx", "cdsmx", "smxd", "smxsun",
    #
    # **Phase 7c's `asemx`, added under the same rule as the five above** -- one commit with its
    # `ARTIFACT_DIRECTORY` row and its `REQUIRED_EDGES` row, for the two distinct reasons the note
    # above records: the directory row because `test_every_artifact_kind_has_a_home` asserts the two
    # sets are identical and fails the moment they are not, and the edge row because
    # `rad_engine.project.check_edges` **returns early for a kind it has no row for** -- so a kind
    # missing from the third set is not a failure at all, it is a matrix that seals naming whatever
    # it likes and is reported valid forever. The first omission is loud, the second is silent, and
    # the second is the one worth moving three files in one commit to avoid.
    #
    # `asemx` is the pre-blind, zero-bounce direct-sun coefficient matrix LM-83 sec 3.3's annual
    # sunlight exposure is tallied from -- sensors x Reinhart sun bins, `rcontrib -I -ab 0` over
    # `rad_engine.matrices.build_ase_direct_argv`. It is **not** `cdsmx`: that is the five-phase
    # `Cds` term, traced at `-ab 1` through a tensor-tree BSDF proxy on an aperture-stripped
    # blackened shell, and the two are computed on different octrees from different scenes with
    # different bounce counts. Putting both on one kind would repeat the `cdsmx`/`dsmx` mistake the
    # paragraph above describes, and would repeat it on the pair whose edge rows genuinely differ:
    # `cdsmx` requires a bsdf edge, `asemx` must not.
    #
    # **`illdir` is deliberately absent.** The direct-sun illuminance the ASE hours are counted from
    # is still an annual illuminance matrix and takes `"ill"`, exactly as `ill5:` does; the
    # invalidation table keys on kind, and what differs is only which id it occupies. A kind added
    # for it would be a second name for a thing already named.
    "asemx",
    #
    # **Phase 7d-1's `sched`, added under the same rule as every kind above** -- one commit with its
    # `ARTIFACT_DIRECTORY` row and its `REQUIRED_EDGES` row, for the two reasons `asemx` records:
    # the directory row because `test_every_artifact_kind_has_a_home` asserts the two sets are
    # identical, and the edge row because `check_edges` **returns early for a kind it has no row
    # for**, so a kind missing from the third set seals naming whatever it likes and is reported
    # valid forever.
    #
    # `sched:<grid>` is LM-83 section 2.2.6's hourly blind schedule -- one state per window group
    # per column -- derived by `rad_engine.schedule` from the per-group direct-sun matrices and
    # consumed by `rad_engine.blend`. It is **JSON, not a matrix**, and it is the first artifact
    # kind in this list that is neither: that is why it gets a kind of its own rather than sharing
    # `ill`. Ruling R107 is why it is sealed at all -- Phase 7c derived the same quantity at read
    # time and sealed nothing, because nothing consumed it; in 7d a number is computed against it,
    # so it is sealed with the number.
    #
    # **`illdir` is still not a kind and this is not one either by another name** (ruling R122). The
    # Phase 7d-1 Task 11 brief asked for `REQUIRED_EDGES["sched"] = ("illdir",)`, over a kind that
    # does not exist and never did; the schedule's operands are `kind="ill"` artifacts under
    # `illdir:` ids, and its own row names them by kind `ill`.
    "sched",
    #
    # **Phase 7d-1 Task 12's `illblend`, added under the same three-in-one-commit rule.**
    #
    # `illblend:<grid>` is the annual illuminance the LM-83 section 2.2.6 schedule selected: the
    # per-group, per-state `ill:<grid>:<g>:<s>` results summed hour by hour at the state each group
    # was in, written by `rad_engine.blend`.
    #
    # **This is the one place `illdir`'s reasoning does not carry over, and the difference is
    # `REQUIRED_EDGES`.** `illdir` stays `kind="ill"` because nothing needs to tell it apart by
    # kind: there is no `ill` row and there can be none (an `ill` is reachable from a point-in-time
    # run that names no matrix at all), so a kind for it would buy nothing. The blend has an edge
    # requirement that *can* be stated -- the schedule and the per-state results, both -- and
    # `REQUIRED_EDGES` is keyed by kind. Sealed as `"ill"` it would inherit the absent row,
    # `check_edges` would return after merely proving each named edge resolves, and a blend could
    # seal naming nothing at all: "reported valid and reused forever", against a matrix whose
    # schedule has since been rebuilt. It is still a result and still lives in `08_results/`.
    "illblend",
    #
    # **Phase 14d's `sky` and `point_in_time` (R730), added under the same three-in-one-commit
    # rule** -- each with its `ARTIFACT_DIRECTORY` row and its `REQUIRED_EDGES` row, for the two
    # reasons `asemx` records.
    #
    # `sky:point_in_time` is the sky primitive text `run_point_in_time` compiles into its octree:
    # the generator's output (piped through the north `xform` when the project declares one) plus
    # R36's glow and ground. It was a scratch file with only its hash on the octree; sealed, its
    # `parameters` carry the argv that regenerates it, so an export can rebuild the sky and not
    # merely detect that it changed. Not `smx`: that is an annual sky *matrix* from a weather file,
    # and `REQUIRED_EDGES["smx"]` requires a weather input this sky does not have.
    #
    # `point_in_time:<grid>` is that tool's single-sky, single-column result file. **Not `ill`**, for
    # two reasons: `results_tool`'s annual selection keys on `ill:` ids and would read this file as
    # an annual matrix, and an `ill` row -- if one were written -- would name matrices this result is
    # not built from. It has an edge requirement that can be stated (its octree and its grid), so it
    # gets a kind whose row states it.
    "sky", "point_in_time",
]


#: Bumped when a document at the old shape cannot already satisfy the new one by its own field
#: defaults. `MANIFEST_SCHEMA_OLD` is emitted against it; migration from any older (or unversioned)
#: document to the current shape runs in `rad_engine.project` on load (ruling R16) -- this module is
#: pure and may not read or write the file a migration needs, so it owns the shape a migration
#: produces and not the act of producing one.
#: 2 -- `Artifact.built_from_digests`.
#: 3 -- `Artifact.content_digest` (R7) and `Project.archived_at` (R8).
#: 4 -- `InputRecord.role` (R9): a real project can already exist at an older version, so this is
#: the first bump `rad_engine.project`'s migration actually has to act on (ruling R16). Ruling R24's
#: `ArtifactWarning.field` shipped the same task without moving this number: it is optional with a
#: `None` default an old document already satisfies, so there is nothing for a migration to decide.
#: Phase 10's `InputRecord.sources` and `.server_written` shipped without moving this number, for
#: R24's reason: both are optional with defaults an old document already satisfies.
#: 5: Phase 11a -- the three luminaire input kinds and `12_luminaires/`; no existing record changes
#: (R482).
#: Phase 11b's InputRecord.zones shipped without moving this number, for R24's reason and Phase 10's
#: precedent: optional, defaulting to None. A v5 server cannot read a manifest holding a zoned grid
#: (extra="forbid"), exactly as it cannot read Phase 10's source records (ruling R620).
#: *(Corrected 2026-10-12, Phase 25 Branch B, R860: it said "A v5 server cannot read a manifest
#: holding a zoned grid". `save_manifest` writes `model_dump_json()` with no `exclude_none`, so
#: every input record this server saves carries `"zones": null`, and an `InputRecord` from before
#: the field refuses even that (extra="forbid", measured at 31c295c's parent): a v5 server from
#: before Phase 11b cannot read any manifest a later server has saved.)*
#: Phase 25's `InputRecord.clock_zone` (R860) shipped without moving this number, for R24's reason:
#: optional, defaulting to None, so an old document already satisfies it. A server from before it
#: cannot read any manifest this server has saved: every input record, of every kind, is written
#: with `"clock_zone": null` or a zone, and that `InputRecord` refuses the key (extra="forbid";
#: measured against 7dcc9e0's).
SCHEMA_VERSION = 5

#: `gensky -defaults` prints `-g 0.200000   # Ground plane reflectance` (verified on Radiance 6.1a).
#: Unset resolves to this *labelled*, never to an absent field: the binary default made explicit
#: rather than inherited silently, which is the whole point of check 1.10.
GROUND_REFLECTANCE_DEFAULT = 0.20
GROUND_REFLECTANCE_DEFAULT_SOURCE = "gensky -defaults"

#: Reserved id namespace for the synthetic inputs described in the module docstring. A file input
#: may not claim an id in it, or a scalar and a file could silently shadow one another.
SCALAR_INPUT_PREFIX = "project:"
GROUND_REFLECTANCE_INPUT_ID = SCALAR_INPUT_PREFIX + "ground_reflectance"

#: Excluded from an artifact's digest. Since R780 `point_in_time:<grid>` records it (the `-n`
#: `rtrace` was given; docs/tool-surface.md#concurrency), and since R781 so does every matrix
#: `run_annual` builds with `rcontrib` or `rfluxmtx` (`dsmx`, `vmx`, `dmx`, `vdmx`, `ddmx`, `cdsmx`,
#: `asemx`; `rad_engine.chain._processes_parameter`). Until R780 this said: "no artifact
#: records it today, because no spawn passes a process count". Core count changes wall time, and
#: for `rcontrib` which random draws each ray gets; for `rtrace -n` at `-ab` > 0 with no ambient
#: file it also changes the ambient-cache regime, measured within the recorded bands (R780). Until
#: R780 this said "Core count changes wall time, not results". A digest that included it would
#: discard every reusable matrix the first time the project moved to a machine with a different
#: core count.
#: `-ab`/`-ad`/`-lw`/`-bn` are *in* the digest -- they are baked into the matrix and corrupt all
#: 8760 hourly results.
#:
#: `grid_behind_aperture` (R846) is the second member: `vmx:<group>:<grid>` records whether every
#: grid point is behind the group's room-facing plane (`rad_core.apertures.grid_behind_aperture`).
#: It is not a flag any binary is given; it is a fact derived from V's own edges (geometry, window
#: group, grid), so a change to it moves those edges' digests already, and keeping it out means a V
#: sealed before R846 recorded it is still reused rather than rebuilt for a fact it cannot change.
#:
#: `compiled` (R849) is the third: the files on a `run_annual` octree's build command lines, in order,
#: and the files their `!xform` lines include, with each one's digest
#: (`rad_engine.export.compiled_scene_record`), which `export(action="scripts")` rebuilds the octree
#: from. Every octree build writes fresh per-call glow file names into it (R822),
#: so in the digest it would make each octree, and every matrix naming it, new on every run.
DIGEST_EXCLUDED_PARAMETERS = frozenset({"n", "grid_behind_aperture", "compiled"})


class ManifestError(ValueError):
    """A manifest that cannot be evaluated: a dangling edge, a cycle, a duplicate id."""


class DanglingEdgeError(ManifestError):
    """`built_from` names an id no input or artifact carries.

    A subclass rather than a new top-level exception: every existing `except ManifestError` (a cycle,
    a duplicate id, the reserved-scalar-namespace guard) must keep catching this too, and narrowing a
    `raise ManifestError` to `raise DanglingEdgeError` at its one call site changes nothing any of
    those catch by type.

    Split out because `rad_engine` needs to tell this one apart from the rest of `ManifestError`
    **by type**, not by parsing the message: `rad_engine.project.EdgesIncompleteError`
    (`ARTIFACT_EDGES_INCOMPLETE`) already names exactly this event -- `built_from` "omits an edge
    this artifact's kind requires, **or names something unknown**" -- for the pre-build case
    `check_edges` catches. A dangling edge discovered on *load* (a hand-edited or corrupted
    `manifest.json`) is the same fact about the manifest, found later; recoding it into the same
    advisory needs a type `rad_engine` can catch without inspecting prose, since a later change to
    this message's wording must not silently stop the recoding from firing. `rad_core` may not import
    `rad_engine`'s exception (the layer rule runs one way), so the type this module offers is the
    narrowest one that still lets the layer above translate without string matching.
    """


# --------------------------------------------------------------------------------------------
# Schema
# --------------------------------------------------------------------------------------------


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


# R810 moved this from the published text: emitted as an object and never as a bare scalar, so a
# code path returning `0.20` for a field declared this shape is a validation failure at the client,
# which is exactly the alarm we want
# (docs/tool-surface.md#results-are-structured-and-a-structured-result-has-a-schema).
class ProvenanceValue(_Model):
    """A number the server may have substituted, carrying where it came from.

    `provenance` is `measured`, `assumed` (stated, not measured) or `default` (a standard's or a
    command's default value). A `default` always carries `source`, the clause or command it was
    taken from, and is never a measurement of this building: report it labelled. `source` and
    `note` are `null` when none was recorded.
    """

    value: float
    provenance: Provenance
    source: str | None = None
    """Citation for a `default` -- the clause or the command whose default was adopted."""
    note: str | None = None

    @model_validator(mode="after")
    def _a_default_must_cite_its_source(self) -> ProvenanceValue:
        """*"A default is never a measurement"* -- and a default with nothing behind it is not even a
        default. Refuses `provenance="default"` with no `source`.

        This field's own docstring one line up already says `source` is *"citation for a `default`"*,
        and `apply_defaults` -- the one producer that is *supposed* to emit `default` -- guarantees
        one structurally (`_LM83_SOURCE[role]`, a bare lookup that would `KeyError` rather than
        yield `None`). The invariant was enforced in that one function and by no type, so a second
        producer escaped it: `materials(action="set")` passes `provenance` and `source` straight
        from caller JSON as two independent `.get`s, so a client could send
        `{"provenance": "default"}` with no citation and the value meaning *"the server substituted
        this from a table"* arrived with **no table behind it** -- `is_error=False`, no
        `DEFAULTS_SUBSTITUTED`, and `source=None` on the wire. `list`/`inspect` then told the client
        *"the server substituted it from a table"* about a value no table produced.

        Enforced here rather than at the `set` path because this is where the value is *validated*,
        so every construction site is covered by having been constructed -- the same discipline
        `tests/test_purity.py` applies by globbing rather than naming. Check 2.9's schema test is
        extended alongside it to require `source` wherever the `provenance` enum admits `default`.

        `ARGUMENT_OUT_OF_RANGE` with `field="source"`, both already allocated: an argument rejected
        on its face before any work was attempted (ruling R14's own reasoning), and `field` is what
        keeps that one code actionable across many arguments -- exactly the choice
        `ReflectanceRangeError` and `TransmittanceRangeError` already made.
        """
        if self.provenance == "default" and self.source is None:
            raise ValueError(
                "a value with provenance 'default' must carry a source -- the clause or command "
                "whose default was adopted. 'default' means the server substituted this from a "
                "table, and a substitution a client cannot check against its table is "
                "indistinguishable from a measurement (docs/tool-surface.md#provenance-a-default-"
                "is-never-a-measurement). Supply source, or use provenance 'assumed'."
            )
        return self

    @model_validator(mode="after")
    def _the_value_must_be_finite(self) -> ProvenanceValue:
        """Refuses `NaN` and `+-inf` for `value`, at construction and at load.

        **The writer must refuse what the reader refuses.** Until 2026-08-31 it did not, and the
        gap was a Critical: `project(action="update", ground_reflectance=NaN)` passed pydantic's
        `float` (pydantic-core's JSON parser accepts `NaN`, `Infinity` and `1e999` for a `float`
        field), `_commit` saved the manifest **before** `describe` ran, and the serializer wrote
        JSON `null` where a non-nullable `float` is declared. Every later call on that project --
        `describe`, `update`, and `delete` even with `confirm=true, force=true` -- then refused
        `MANIFEST_UNREADABLE`, because `delete_project` loads the manifest unconditionally as its
        evidence that the tree is the project. One ordinary tool argument, and the project was
        unreachable through the entire tool surface; recovery meant hand-editing `manifest.json`
        outside the server. `math.isfinite` was already enforced in `_normalise_value` -- but that
        sits on the *digest* walk, which runs after the save.

        Enforced on the **type** rather than at `resolve_ground_reflectance`, for the same reason
        `_a_default_must_cite_its_source` is: every construction site is covered by having been
        constructed. `ProvenanceValue` also carries material reflectance and transmittance
        (`rad_engine.materials`), converted lengths (`rad_core.materials`) and sensor heights
        (`rad_core.grids`), and none of those had a finiteness check either.

        `rad_engine.project` separately refuses a non-finite or out-of-range `ground_reflectance`
        with `ARGUMENT_OUT_OF_RANGE` and `field="ground_reflectance"` before anything is written,
        so the wire sees an actionable refusal rather than this `ValidationError`. This is the
        backstop under it, not the message a client reads.
        """
        if not math.isfinite(self.value):
            raise ValueError(
                f"value {self.value!r} is not finite. A manifest cannot carry it: the serializer "
                "writes it as JSON null and this model declares a non-nullable float, so the "
                "document would be written in a shape this server cannot read back."
            )
        return self


class ArtifactWarning(_Model):
    """One classified advisory from the build's stderr, kept with the artifact.

    Deliberately not `rad_mcp.envelope.Advisory`: `rad_core` importing the protocol layer would
    invert the layer rule in CONTEXT.md. `rad_mcp` maps this to an `Advisory` on the way out.
    """

    code: str
    severity: Literal["info", "warn", "error"]
    field: str | None = None
    """Which structured key this finding is about, where it is about one -- mirrors
    `rad_mcp.envelope.Advisory.field` (same name, type, default, and position ahead of `message`) so
    the two read as one shape (ruling R24). Before this field existed, `REFLECTANCE_IMPLAUSIBLE`
    reused for both reflectance and transmittance (`_plausibility_warning`,
    `rad_core.materials`) could only spell the difference into `message`'s prose -- the same
    distinction `ARGUMENT_OUT_OF_RANGE` makes structurally on a refusal, unavailable on a warning
    because this carrier had nowhere to put it. Optional and defaulted: an existing serialised
    `ArtifactWarning` has no such key, and reading it back must not require one -- see `SCHEMA_VERSION`
    for why that does not itself require a migration.
    """
    message: str


class ToolCall(_Model):
    """The tool call that registered an input -- required by "What it records"."""

    tool: str
    action: str | None = None
    arguments: dict[str, Any] = Field(default_factory=dict)
    called_at: datetime | None = None


class FileRef(_Model):
    """One file of an input: its absolute path and its content digest.

    `rad_core` is pure and never reads the file. The digest arrives as data from `rad_engine`,
    which is the only layer allowed to touch the disk.
    """

    path: str
    """Absolute. All paths in the manifest are absolute (docs/tool-surface.md#paths)."""
    digest: str


class ZoneRecord(_Model):
    """One zone of an EN 12464 zoned grid (Phase 11b design §4). `file_index` points into the owning
    `InputRecord.files`, which is authoritative; `None` records an absent zone, so a reader learns
    absences from the record rather than re-deriving them. Lengths in metres; `spacing_m` and
    `formula_spacing_m` are `[x, y]`, `circumscribing_m` is `[x0, y0, x1, y1]`."""

    zone: str
    group: str | None = None
    file_index: int | None
    points: int
    spacing_m: list[float] | None = None
    formula_spacing_m: list[float] | None = None
    circumscribing_m: list[float] | None = None
    ribbon_clamped: bool = False
    task_in_strip: bool = False


class InputRecord(_Model):
    """A registered input: geometry, materials, a window group, a grid, weather, or a BSDF."""

    id: str
    kind: InputKind
    files: list[FileRef] = Field(default_factory=list)
    units: Units | None = None
    """Declared, never inferred: a scene modelled in feet and read as metres is wrong by 3.28x with
    no error (docs/tool-surface.md#required-with-no-default)."""
    provenance: Provenance | None = None
    source: str | None = None
    registered_by: ToolCall | None = None

    role: str | None = None
    """Which role this input plays -- `"ground"` is the one ruling R9 needs, so
    `rad_engine.project.REQUIRED_EDGES["dmx"]` can require *the* materials input playing it rather
    than merely *an* input of kind `materials` (docs/plan.md check 1.1's ground pair). A plain
    string, not `rad_core.materials.MaterialRole`: this module owns the persisted schema and must not
    import a module about LM-83 reflectance defaults (or about LM-83 blind-closing classes, the
    second vocabulary below), which is a coupling with nothing to do with what a manifest records.

    **Two writers, two disjoint vocabularies, as of Phase 7d-1 Task 1 (ruling R133).**
    `rad_engine.project.register_input`'s own typed `role=` parameter is where a `MaterialRole` is
    written in and read back (`REQUIRED_EDGES["dmx"]`) as this field's stored value -- see that
    module for the one place the two are compared. `rad_mcp.window_group_tool`'s `define` action
    writes the other: LM-83 section 2.2.6's two-value closing class (`"view"`/`"clerestory"`,
    `window_group`'s `closing_role` argument), and it does **not** go through `register_input`'s
    `role=` parameter -- that parameter is typed to `MaterialRole` and raises `InvalidRoleError` for
    anything else, so `window_group_tool._define` calls `rad_engine.project.
    register_input_and_set_role` instead: the general escape hatch that registers the input and
    then sets this field on the record directly, in the one manifest write that branch was always
    going to make. Both writers land in the same plain string field for the same reason: a
    persisted-schema module may not import either vocabulary's owning module, so the field stays
    generic and each caller's own layer is where its value is validated and compared.

    Excluded from `content_digest()`, the same choice `provenance` makes below: it does not change
    what reaches Radiance, only what a caller believes an input is *for*, so relabelling it must not
    invalidate anything built from the input's actual bytes.
    """

    sources: list[FileRef] = Field(default_factory=list)
    """The files an imported input was converted from -- an OBJ and its JSON map, or a non-metre
    `.rad` and every include it read (Phase 10 design §5.9, §5.11). No consumer reads them;
    `geometry(action="list")` re-digests them to report a changed source. Not in `content_digest`:
    what reaches Radiance is `files`."""

    server_written: bool = False
    """True when this server wrote `files` itself, in metres, into `01_geometry/`. Then the stored
    coordinates are metres whatever `units` says: `units` records the unit the caller declared."""

    zones: list[ZoneRecord] | None = None
    """Phase 11b: the zones of an EN 12464 zoned grid, or `None` for every other input. Not in
    `content_digest`, and neither are the recorded arguments it is derived from: the digest covers
    the `files` (and `kind`, `units`) only. A hand edit of `zones` alone therefore invalidates nothing;
    `run_electric` checks each present zone against its file (name, digest, count) instead."""

    clock_zone: str | None = None
    """R860: the IANA time zone a weather input's LM-83 08:00-18:00 window is read in, such as
    `"Europe/Athens"`, set by `weather(action="load", clock_zone=...)` after
    `rad_engine.clock.check_clock_zone` accepted it against the EPW's UTC offset; `None` for every
    other input, for a weather load that named none, and for a record written before the field
    existed. Then the window stays in the EPW's own standard time.

    Not in `content_digest`, like `role`: it reaches no Radiance input (not the `.wea`, not a
    matrix), only which columns a results read counts, so naming or changing a zone makes nothing
    built from the input stale."""

    def content_digest(self) -> str:
        return input_digest(kind=self.kind, file_digests=[f.digest for f in self.files], units=self.units)


class Artifact(_Model):
    """A built thing. The field set is fixed by the table in docs/tool-surface.md#what-it-records."""

    id: str
    """Stable and structured: `vmx:<window_group>:<grid>`, `tmx:<window_group>:<state>`."""
    kind: ArtifactKind
    path: str
    built_from: list[str] = Field(default_factory=list)
    """Input and artifact ids -- the edges of the graph, and the only thing invalidation reads."""
    built_from_digests: dict[str, str] = Field(default_factory=dict)
    """Each edge's digest as it stood at build time
    (docs/tool-surface.md#what-it-records). Written by whoever seals the artifact; see
    `resolve_built_from_digests`."""
    parameters: dict[str, Any] = Field(default_factory=dict)
    """The normalised flag set actually passed. Includes `n`; the digest excludes it."""
    digest: str
    """As computed at build time. Validity is this compared against the recomputed value."""
    content_digest: str | None = None
    """The digest of the artifact's **own bytes** at seal time (docs/tool-surface.md#what-it-records).

    `digest` says the provenance is unchanged; this says the file on disk is still the file that
    provenance describes. The manifest digests every input file and, before R7, digested nothing it
    produced -- so a `.vmx` truncated by a full disk or edited by hand stayed valid forever.

    Optional in the schema and never computed here: this module is pure and may not read a file, so
    requiring it would make a valid `Artifact` unconstructable in the layer that defines it.
    `rad_engine.project.register_artifact` sets it on everything it seals, and treats `None` as
    "cannot be proved current" rather than as "fine".
    """
    radiance_version: str
    """The full version string from `rpict -version`, as of *this artifact's* build."""
    built_at: datetime | None = None
    wall_time_s: float | None = None
    warnings: list[ArtifactWarning] = Field(default_factory=list)


class Project(_Model):
    """The project-level record."""

    name: str
    units: Units
    radiance_version: str
    """The install's *current* version. Differing from an artifact's flags it; see `evaluate`."""
    schema_version: int = SCHEMA_VERSION
    created_at: datetime | None = None
    description: str | None = None

    archived_at: datetime | None = None
    """Set by `project(action="archive")`, which marks as well as moves (docs/plan.md, Phase 1).
    A moved directory that is not marked can only be recognised as archived by where it sits, which
    stops being true the moment anyone moves it again."""

    ground_reflectance: ProvenanceValue = Field(default_factory=lambda: default_ground_reflectance())
    """Set in exactly one place, `project(ground_reflectance=...)`, because Radiance sets it in three
    independent places that do not know about each other -- `gensky -g`, `gendaymtx -g`, and the
    modelled ground polygon's material. Unset is the labelled default, never an absent field
    (docs/tool-surface.md#ground-reflectance-is-one-project-value-passed-to-every-sky-generator)."""

    scalars: dict[str, float] = Field(default_factory=dict)
    """Other project-level scalars that reach a Radiance command line but have no file to digest --
    `north` is the one the spec names. Each becomes a synthetic input `project:<name>`."""

    @field_validator("scalars", mode="before")
    @classmethod
    def _no_scalar_is_a_boolean(cls, value: Any) -> Any:
        """Refuses a JSON `true`/`false` in `scalars`, at construction and at load (R710).

        Measured 2026-09-24: a hand-edited `"north": true` opened, and pydantic's lax `float` read
        it back as `1.0` -- a silent one-degree rotation. Refusing it makes the manifest unreadable,
        the outcome a list-valued north already had. Only `bool` is refused: a JSON `90` arrives as
        an `int` and must keep loading, and a numeric string keeps pydantic's lax reading.
        """
        if isinstance(value, Mapping):
            for scalar_name, scalar in value.items():
                if isinstance(scalar, bool):
                    raise ValueError(
                        f"scalars.{scalar_name} is a boolean ({scalar!r}), not a number"
                    )
        return value

    north_convention: Literal["degrees_east_of_north"] = "degrees_east_of_north"
    """Recorded so a project cannot silently mix conventions. `north` is stated east of north, the
    surveying convention; `gendaymtx -r` rotates *west* of north, so the engine emits `-r -north`
    (docs/tool-surface.md#how-provenance-invalidates-a-matrix). One legal value, so it never varies
    and is not in any digest -- a project on the other convention would carry a different `north`."""


class Manifest(_Model):
    """Everything `project(action="describe")` reports, and the graph invalidation walks."""

    project: Project
    inputs: list[InputRecord] = Field(default_factory=list)
    artifacts: list[Artifact] = Field(default_factory=list)

    def input(self, input_id: str) -> InputRecord:
        for record in self.inputs:
            if record.id == input_id:
                return record
        raise KeyError(input_id)

    def artifact(self, artifact_id: str) -> Artifact:
        for artifact in self.artifacts:
            if artifact.id == artifact_id:
                return artifact
        raise KeyError(artifact_id)

    def input_digests(self) -> dict[str, str]:
        """Every id the graph can name as a source, file-backed and synthetic alike, in one map.

        The merge happens here so that no code downstream of it can tell the two apart.
        """
        digests: dict[str, str] = {}
        for record in self.inputs:
            if record.id.startswith(SCALAR_INPUT_PREFIX):
                raise ManifestError(
                    f"input id {record.id!r} uses the reserved {SCALAR_INPUT_PREFIX!r} namespace"
                )
            if record.id in digests:
                raise ManifestError(f"duplicate input id {record.id!r}")
            digests[record.id] = record.content_digest()

        for scalar_id, digest in _synthetic_input_digests(self.project).items():
            digests[scalar_id] = digest
        return digests


class ArtifactStatus(_Model):
    """The per-artifact verdict `project(action="describe")` publishes."""

    id: str
    kind: ArtifactKind
    valid: bool
    digest: str
    recomputed_digest: str
    invalidated_by: list[str] = Field(default_factory=list)
    """The **exact** edges whose digest no longer matches the one recorded in `built_from_digests`
    -- added, removed, or changed. Empty when nothing upstream moved."""
    parameters_changed: bool = False
    """True when the artifact's own digest components no longer reproduce its stored digest from the
    edge digests it was built from. In practice that is `parameters`: `kind` and `radiance_version`
    are written once at build time. An invalid artifact always has a non-empty `invalidated_by`, or
    this, or a `stale_reason` (R777), so a cause is never omitted."""
    stale_reason: str | None = None
    """Set when a kind rule makes the artifact stale whatever its digest says (R777): today, a `sky`
    sealed before R761. An invalid artifact reports `invalidated_by`, `parameters_changed` or this."""
    radiance_version_changed: bool = False
    """Flagged, never invalidating. Recomputing every matrix on an upgrade is hours for a 5-phase
    project, so a version change is a `warn` advisory listing which artifacts predate the current
    version (docs/tool-surface.md#how-provenance-invalidates-a-matrix)."""


class ReuseSummary(_Model):
    valid: int
    invalid: int
    rebuild_estimate_s: float | None = None
    """Sum of the recorded `wall_time_s` of the invalid artifacts. None when any of them never
    recorded one, because a partial sum reported as a total is an under-estimate that reads as fact."""


# --------------------------------------------------------------------------------------------
# Digests
# --------------------------------------------------------------------------------------------


def _canonical_json(payload: Any) -> str:
    """The one serialisation every digest in this module hashes.

    `sort_keys` is what makes parameter key order irrelevant; `allow_nan=False` turns a NaN
    parameter into an error rather than into the non-JSON token `NaN`; nothing here is seeded by
    anything process-local, so the same inputs hash identically in a fresh interpreter regardless
    of `PYTHONHASHSEED` (check 1.3).
    """
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)


def _sha256(payload: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _normalise_value(value: Any) -> Any:
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ManifestError(f"parameter value {value!r} is not finite")
        # `-ad 1024` and `-ad 1024.0` are the same flag on the same command line, so they must not
        # be two digests. Non-integral floats keep full precision: `-lw 1.52e-5` matters.
        return int(value) if value.is_integer() else value
    if isinstance(value, str) or value is None:
        return value
    if isinstance(value, Mapping):
        return {str(k): _normalise_value(v) for k, v in value.items()}
    if isinstance(value, Sequence):
        # `-g 0.1 0.1 0.1` is one flag with three values; order is the RGB order and is significant.
        return [_normalise_value(v) for v in value]
    raise ManifestError(f"parameter value of type {type(value).__name__} cannot be digested")


def normalise_parameters(parameters: Mapping[str, Any]) -> dict[str, Any]:
    """Canonical form of a flag set: leading dashes stripped, values coerced, key order irrelevant.

    Case is **not** folded. Radiance flags are case-sensitive and the pairs are not synonyms --
    `rtrace -i` and `-I` are both irradiance switches with different meanings, so collapsing them
    would make two different commands share a digest.
    """
    normalised: dict[str, Any] = {}
    for raw_key, value in parameters.items():
        key = str(raw_key).lstrip("-")
        if not key:
            raise ManifestError(f"parameter key {raw_key!r} is empty once dashes are stripped")
        if key in normalised:
            raise ManifestError(f"parameter {key!r} is given twice under different spellings")
        normalised[key] = _normalise_value(value)
    return normalised


def combine_file_digests(file_digests: Sequence[str]) -> str:
    """Fold a per-file digest list into one digest for the input that owns those files.

    `rad_core` never computes a file digest -- it receives them. Order is preserved rather than
    sorted: the list is the order the files are handed to `oconv`, and the manifest has to be able
    to tell two orderings apart.
    """
    return _sha256({"files": list(file_digests)})


def input_digest(*, kind: str, file_digests: Sequence[str], units: str | None = None) -> str:
    """The content digest of one input.

    `units` is in it because the same bytes read as feet rather than metres are a different scene by
    a factor of 3.28 with no error anywhere (docs/tool-surface.md#required-with-no-default). The
    provenance *label* is not: relabelling a measured file as assumed changes nobody's confidence
    into different geometry, and re-running every matrix over an annotation would be absurd.

    The file list is folded by `combine_file_digests` rather than re-derived here, so there is one
    file-folding rule and the order property is a property of the digest the graph actually walks.
    """
    return _sha256({"kind": kind, "files": combine_file_digests(file_digests), "units": units})


def scalar_digest(name: str, value: float) -> str:
    """The content digest of a synthetic project-scalar input."""
    return _sha256({"scalar": name, "value": _normalise_value(float(value))})


def compute_digest(
    *,
    kind: str,
    edges: Mapping[str, str],
    parameters: Mapping[str, Any],
    radiance_version: str,
) -> str:
    """`sha256(input digests || normalised parameters || radiance_version || kind)`.

    `edges` maps each `built_from` id to the digest currently resolved for it. Ids are hashed
    alongside their digests so that moving a digest from one edge to another is a change, and the
    pairs are sorted so that reordering `built_from` is not.

    `radiance_version` is in the digest so two builds under different versions can never be mistaken
    for the same content -- but validity compares an artifact against *its own* recorded version, so
    an upgrade flags rather than invalidates. See `evaluate`.
    """
    payload = {
        "kind": kind,
        "edges": sorted([edge_id, digest] for edge_id, digest in edges.items()),
        "parameters": {
            key: value
            for key, value in normalise_parameters(parameters).items()
            if key not in DIGEST_EXCLUDED_PARAMETERS
        },
        "radiance_version": radiance_version,
    }
    return _sha256(payload)


def _synthetic_input_digests(project: Project) -> dict[str, str]:
    if "ground_reflectance" in project.scalars:
        raise ManifestError(
            "ground_reflectance is a project field, not a free scalar -- it is set in exactly one "
            "place. A second copy is the failure this rule exists to prevent."
        )
    scalars: dict[str, float] = {
        "ground_reflectance": project.ground_reflectance.value,
        **project.scalars,
    }
    return {SCALAR_INPUT_PREFIX + name: scalar_digest(name, value) for name, value in scalars.items()}


# --------------------------------------------------------------------------------------------
# The invalidation graph
# --------------------------------------------------------------------------------------------


#: The ground glow a `sky` artifact records when it is current (ruling R761: white, because
#: `gensky -g` already carries the reflectance). The one definition: `rad_engine.sky.GROUND_GLOW_RGB`
#: is bound to it, so the glow written and the glow judged cannot drift (R777).
SKY_GROUND_GLOW_RGB = "1 1 1"


def stale_reason(artifact: Artifact) -> str | None:
    """Why `artifact` is stale whatever its digest says, or `None` (ruling R777, owner decision E6
    of 2026-10-03). One kind rule: a `sky` recorded without `SKY_GROUND_GLOW_RGB` was sealed before
    R761, when the ground glow carried the reflectance a second time; its inputs never changed, so
    its digest cannot say so. `_walk` moves its resolved digest, so everything built from it is
    invalid by its edges, and `evaluate` marks it invalid even when its stored digest agrees."""
    if artifact.kind == "sky" and artifact.parameters.get("ground_glow") != SKY_GROUND_GLOW_RGB:
        return (
            f"sealed with ground_glow={artifact.parameters.get('ground_glow')!r}, before ruling R761 "
            f"made the sky's ground glow {SKY_GROUND_GLOW_RGB!r}; a point-in-time result traced "
            "through it applied the ground reflectance twice. Re-run run_point_in_time with a sky."
        )
    return None


def _resolved_digest(artifact: Artifact, edges: Mapping[str, str]) -> str:
    """`compute_digest`, moved by `stale_reason` when a kind rule applies (R777)."""
    digest = compute_digest(
        kind=artifact.kind, edges=edges, parameters=artifact.parameters,
        radiance_version=artifact.radiance_version,
    )
    reason = stale_reason(artifact)
    return digest if reason is None else _sha256({"stale_reason": reason, "digest": digest})


def _walk(manifest: Manifest) -> tuple[dict[str, str], dict[str, dict[str, str]]]:
    """Resolve every artifact's digest and the edge digests it was resolved from."""
    input_digests = manifest.input_digests()

    artifacts: dict[str, Artifact] = {}
    for artifact in manifest.artifacts:
        if artifact.id in artifacts:
            raise ManifestError(f"duplicate artifact id {artifact.id!r}")
        if artifact.id in input_digests:
            raise ManifestError(f"id {artifact.id!r} names both an input and an artifact")
        artifacts[artifact.id] = artifact

    resolved: dict[str, str] = {}
    resolved_edges: dict[str, dict[str, str]] = {}
    in_progress: set[str] = set()

    def resolve(artifact_id: str) -> str:
        if artifact_id in resolved:
            return resolved[artifact_id]
        if artifact_id in in_progress:
            raise ManifestError(f"cycle in built_from through {artifact_id!r}")
        in_progress.add(artifact_id)

        artifact = artifacts[artifact_id]
        edges: dict[str, str] = {}
        for edge in artifact.built_from:
            if edge in input_digests:
                edges[edge] = input_digests[edge]
            elif edge in artifacts:
                edges[edge] = resolve(edge)
            else:
                # A dangling edge is a corrupt manifest, not a valid artifact. Treating it as
                # "nothing changed" would report a matrix built from a deleted input as reusable.
                # `DanglingEdgeError`, not the bare `ManifestError` the other raises in this module
                # use: `rad_engine` recodes exactly this one into `ARTIFACT_EDGES_INCOMPLETE`, and it
                # needs a type to catch, not this message's wording.
                raise DanglingEdgeError(f"artifact {artifact_id!r} is built from unknown id {edge!r}")

        digest = _resolved_digest(artifact, edges)
        in_progress.discard(artifact_id)
        resolved[artifact_id] = digest
        resolved_edges[artifact_id] = edges
        return digest

    for artifact_id in artifacts:
        resolve(artifact_id)
    return resolved, resolved_edges


def recompute_digests(manifest: Manifest) -> dict[str, str]:
    """The digest each artifact *would* have, given the manifest as it now stands.

    Resolved transitively: an artifact edge contributes the upstream artifact's **recomputed**
    digest, not its stored one, which is what carries a changed input all the way down to the `.ill`
    without anything in this function knowing what an `.ill` is.
    """
    return _walk(manifest)[0]


def resolve_built_from_digests(manifest: Manifest) -> dict[str, dict[str, str]]:
    """Per artifact, the digest currently resolved for each of its `built_from` edges.

    This is what `rad_engine` stores into `Artifact.built_from_digests` when it seals a freshly
    built artifact, so that a later `describe` can say *which* edge moved rather than listing all
    of them (docs/tool-surface.md#what-it-records).
    """
    return _walk(manifest)[1]


def evaluate(manifest: Manifest) -> dict[str, ArtifactStatus]:
    """Per-artifact validity, in manifest order.

    Valid means the recomputed digest equals the stored one and no kind rule applies. The eleven
    rows of the table fall out of `built_from` alone; one kind rule, `stale_reason` (R777), is the
    only place `kind` decides anything in this evaluation.
    """
    recomputed, resolved_edges = _walk(manifest)

    statuses: dict[str, ArtifactStatus] = {}
    for artifact in manifest.artifacts:
        reason = stale_reason(artifact)
        valid = recomputed[artifact.id] == artifact.digest and reason is None
        changed_edges = [] if valid else _changed_edges(artifact, resolved_edges[artifact.id])
        statuses[artifact.id] = ArtifactStatus(
            id=artifact.id,
            kind=artifact.kind,
            valid=valid,
            digest=artifact.digest,
            recomputed_digest=recomputed[artifact.id],
            invalidated_by=changed_edges,
            parameters_changed=False if valid else _own_build_changed(artifact),
            stale_reason=reason,
            # Flagged, not invalidating: `valid` above never reads this.
            radiance_version_changed=artifact.radiance_version != manifest.project.radiance_version,
        )
    return statuses


def _changed_edges(artifact: Artifact, resolved: Mapping[str, str]) -> list[str]:
    """The edges that actually moved since the artifact was built.

    Exact rather than a candidate set, because `built_from_digests` records what each edge hashed to
    at build time. An id in one map and not the other is an edge added or removed, which is a change
    too. This answers "which of my five inputs cost me this matrix", which is the question the
    provenance view exists for (docs/tool-surface.md#what-it-records).
    """
    stored = artifact.built_from_digests
    return sorted(
        edge for edge in set(stored) | set(resolved) if stored.get(edge) != resolved.get(edge)
    )


def _own_build_changed(artifact: Artifact) -> bool:
    """Whether the artifact's own digest components moved, independently of its edges.

    Recomputing against the edge digests *stored with the artifact* holds the upstream fixed, so
    what is left is `parameters` (and, in principle, `kind`/`radiance_version`). Without this an
    artifact invalidated by a raised `-ab`/`-ad` would report a set of unchanged inputs as its
    cause -- a cause set that provably cannot contain the cause.

    The stored digest is compared with both the plain `compute_digest` and the one `stale_reason`
    moves (R777): a sky sealed before the rule stored the plain one, a glowless sky sealed since
    stores the moved one, and in neither case did its own components change -- the rule, reported
    as `stale_reason`, is the cause.
    """
    plain = compute_digest(
        kind=artifact.kind,
        edges=artifact.built_from_digests,
        parameters=artifact.parameters,
        radiance_version=artifact.radiance_version,
    )
    return artifact.digest not in {plain, _resolved_digest(artifact, artifact.built_from_digests)}


def reuse_summary(statuses: Mapping[str, ArtifactStatus], manifest: Manifest) -> ReuseSummary:
    """The counts behind `project(action="describe")`'s `reuse_summary`."""
    artifacts = {artifact.id: artifact for artifact in manifest.artifacts}
    invalid = [artifacts[status.id] for status in statuses.values() if not status.valid]

    times = [artifact.wall_time_s for artifact in invalid]
    estimate = None if any(t is None for t in times) else sum(t for t in times if t is not None)
    return ReuseSummary(
        valid=sum(1 for status in statuses.values() if status.valid),
        invalid=len(invalid),
        rebuild_estimate_s=estimate,
    )


# --------------------------------------------------------------------------------------------
# Ground reflectance
# --------------------------------------------------------------------------------------------


def default_ground_reflectance() -> ProvenanceValue:
    """0.20, labelled `default`, sourced to `gensky -defaults`."""
    return ProvenanceValue(
        value=GROUND_REFLECTANCE_DEFAULT,
        provenance="default",
        source=GROUND_REFLECTANCE_DEFAULT_SOURCE,
    )


def resolve_ground_reflectance(
    value: float | None,
    *,
    provenance: Provenance = "assumed",
    source: str | None = None,
    note: str | None = None,
) -> ProvenanceValue:
    """What `project(action="create"|"update")` stores for `ground_reflectance`.

    `None` is the labelled default, never an absent field: a project with no ground reflectance is a
    project where three Radiance defaults are about to disagree in silence. A value the user gave is
    `assumed` unless they state it was measured -- "user gave it, unmeasured" is what `assumed`
    means (docs/tool-surface.md#provenance-a-default-is-never-a-measurement).
    """
    if value is None:
        return default_ground_reflectance()
    return ProvenanceValue(value=value, provenance=provenance, source=source, note=note)
