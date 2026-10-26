"""Assemble the provenance narrative for one artifact -- pure, no Radiance, no subprocess, no path
that must exist.

Phase 9 Task 12 (docs/plan.md, "Phase 9 — rendering, quality, export").
docs/tool-surface.md §17 (`export`), `action="report"`: *"inputs with provenance, method and why,
the matrices and their ages, every advisory raised during the run, the Radiance version, and the
standard edition. Every `default` provenance appears in a 'Assumptions' section, labelled."*
docs/plan.md check 9.11.

**This module is a `rad_core` module: it takes the manifest as data and returns a dataclass, never
an engine type** (`tests/test_purity.py`'s layering guard -- `rad_core` never imports
`rad_engine`, not even under `TYPE_CHECKING`). It does not read `10_jobs/<job_id>/job.json`, does
not run `getinfo`, and does not know what a `JobRecord` is.

**`job_id` names an artifact, not a `rad_engine.jobs.JobRecord`** -- the same finding
`rad_engine.export`'s module docstring records at length, for the same reason: a bare `Manifest`
has no other id this function could resolve. **`run_point_in_time` now registers one** (ruling
R348, Phase 9 Task 14: `octree:point_in_time`, sealed by `_register_point_in_time_octree`, renamed
`_register_point_in_time_artifacts` by R730) -- this sentence previously said it "registers no
artifact at all", which R348 made false. That registration did not, by itself, make check 9.10
runnable: `run_point_in_time` never submits a job to `runtime.store`, so no `job_id` it returns
resolves through `rad_mcp.export_tool`'s `job_id`-based lookup at all. *(2026-09-27, Phase 14 Branch
D: still true of `job_id`; `run_point_in_time` now registers its sky and its result too (R730), and
its result's `artifact_id` reaches `export` with no job at all (R729) -- `rad_engine.export`'s
module docstring has the rest.)*

**Scope: the report narrates `job_id`'s own build chain, not the whole project.** `.inputs`,
`.matrices`, `.assumptions`, `.advisories` and `.standard_edition` (R868) are all restricted to
the artifacts and inputs `job_id` transitively depends on (`built_from`, walked the same way
`rad_core.manifest._walk` walks it for digests) -- a report about *this* result should not claim
assumptions, advisories or an LM-83 edition that belong to an unrelated matrix elsewhere in the
same project.
"""

from __future__ import annotations

from dataclasses import dataclass

from rad_core.lm83_edition import DEFAULT_LM83_EDITION, LM83_22, LM83_EDITIONS, edition_title
from rad_core.manifest import (
    GROUND_REFLECTANCE_INPUT_ID,
    SCALAR_INPUT_PREFIX,
    Artifact,
    ArtifactWarning,
    InputRecord,
    Manifest,
    ManifestError,
)

__all__ = ["STANDARD_EDITION", "Assumption", "Report", "assemble"]

#: Sourced to the compliance skill's own corpus header: `skills/lighting-standards-compliance/
#: chapters/ch03-en-17037-daylight.md:1` states "EN 17037:2018 -- Daylight in buildings" and cites
#: "BS EN 17037:2018" directly beneath it; `skills/lighting-standards-compliance/SKILL.md`'s corpus
#: line names "IES LM-83-12" for sDA/ASE, the same citation `rad_core.compliance`, `rad_core.grids`
#: and `rad_core.materials` already use throughout. Neither module states a combined "edition"
#: string today -- this is the first thing on the published surface that has to, so it states both
#: rather than picking one and silently dropping the other's scope (sDA/ASE vs. daylight provision).
#:
#: **The line for a chain no ancestor of which recorded ANSI/IES LM-83-22** (R868): LM-83-12 is the
#: default edition, and every chain built before LM-83-22 was selectable is one of these. A chain that
#: recorded LM-83-22 names it instead, or both editions beside each other (`_lm83_editions`).
STANDARD_EDITION = "IES LM-83-12; BS EN 17037:2018"
_EN_17037 = "BS EN 17037:2018"

#: The key both LM-83 ancestors record the edition under: `sched:<grid>`'s `parameters` and the grid's
#: `registered_by.arguments`. `rad_engine.schedule.LM83_EDITION_PARAMETER` is the same string;
#: `rad_core` may not import it (`tests/test_purity.py`), so `tests/test_report.py` pins the two equal.
_LM83_EDITION_KEY = "lm83_edition"


@dataclass(frozen=True)
class Assumption:
    """One `provenance: "default"` value, labelled with its clause citation (check 9.11).

    `value` is `None` for a file-backed `InputRecord` default (a file has no scalar value to
    report) and set for a project scalar such as `ground_reflectance` (a `ProvenanceValue`, which
    does carry one).
    """

    id: str
    clause: str
    value: float | None = None
    note: str | None = None


@dataclass(frozen=True)
class Report:
    """`radiance_version` is the version that built the **target** artifact (`job_id`), not the
    installed one -- each of `matrices` carries its own `radiance_version` too, so a chain that
    crosses a Radiance upgrade is narrated as it was actually built, one version per artifact
    (R738). `manifest.project.radiance_version` is not read here: `rad_engine.project.
    _load_manifest` overwrites it with the installed version whenever it is given one (plan M12),
    so it names the last install that opened the project, never what a caller might expect --
    "the version that built this project's artifacts"."""

    assumptions: tuple[Assumption, ...]
    inputs: tuple[InputRecord, ...]
    method: str | None
    matrices: tuple[Artifact, ...]
    advisories: tuple[ArtifactWarning, ...]
    radiance_version: str
    standard_edition: str


def _ancestor_ids(manifest: Manifest, job_id: str) -> tuple[list[str], list[str]]:
    """(artifact ids, input ids) `job_id` transitively depends on, artifacts ancestors-first.

    `rad_engine.export._ancestor_artifacts`'s own small copy of this walk, over the same manifest
    shape -- `rad_core` may not import `rad_engine`'s copy, and `rad_core.manifest._walk` is
    private to that module and answers a different question (digests, not build order). Two
    independent ten-line copies is cheaper than inventing a shared home for one across a layer
    boundary that exists on purpose.
    """
    artifacts_by_id = {artifact.id: artifact for artifact in manifest.artifacts}
    artifact_order: list[str] = []
    input_ids: list[str] = []
    seen: set[str] = set()

    def visit(node_id: str) -> None:
        if node_id in seen:
            return
        seen.add(node_id)
        artifact = artifacts_by_id.get(node_id)
        if artifact is not None:
            for edge in artifact.built_from:
                visit(edge)
            artifact_order.append(node_id)
        elif not node_id.startswith(SCALAR_INPUT_PREFIX):
            input_ids.append(node_id)
        # a `project:*` scalar edge carries no `InputRecord`; its value is reported through
        # `manifest.project.ground_reflectance` directly, in `assemble` below.

    visit(job_id)
    return artifact_order, input_ids


def _recorded_edition(record: dict, *, where: str, remedy: str, detail: str = "") -> str:
    """The edition one LM-83 ancestor recorded: the key's absence is LM-83-12, which every record
    written before R864, and every one written under LM-83-12, means. A value that is present and is
    not exactly one of `LM83_EDITIONS` raises `ManifestError` and is never read as either edition --
    `rad_engine.schedule.sealed_lm83_edition`'s rule, restated here because `rad_core` may not
    import it."""
    if _LM83_EDITION_KEY not in record:
        return DEFAULT_LM83_EDITION
    value = record[_LM83_EDITION_KEY]
    if not isinstance(value, str) or value not in LM83_EDITIONS:
        raise ManifestError(
            f"{where} records lm83_edition={value!r}{detail}, which is not an LM-83 edition: the "
            f"editions are exactly {' and '.join(repr(e) for e in LM83_EDITIONS)}, letter case "
            "included. The export report names the LM-83 edition its chain recorded, so the value is "
            f"refused rather than read as either. {remedy}"
        )
    return value


def _lm83_editions(manifest: Manifest, artifact_ids: list[str], input_ids: list[str]) -> set[str]:
    """The LM-83 editions the chain's LM-83 ancestors recorded (R868).

    **An LM-83 ancestor is one built for LM-83**, and only two kinds of record say so:

    - a `sched:<grid>` artifact (`kind="sched"`): only `run_annual(blind_control="lm83")` seals one.
      `parameters["lm83_edition"]` is `"LM-83-22"` when it was sealed under LM-83-22, and absent
      when it was sealed under LM-83-12 (R864);
    - a grid input whose creating call was `sensor_grid(action="create", standard="lm83")`.
      `registered_by.arguments["lm83_edition"]` is the edition the caller sent, absent when the
      caller sent none (R864), which is LM-83-12, the default.

    **Any other ancestor names no LM-83 edition**: a grid created for `en17037` or `en12464`, or one
    with no recorded creating call, is not counted as LM-83-12 for lacking the key, and its
    arguments are not read at all -- `sensor_grid` refuses `lm83_edition` beside any other standard,
    so a key there was written by hand and describes nothing LM-83 judged.
    """
    editions: set[str] = set()
    for artifact_id in artifact_ids:
        artifact = manifest.artifact(artifact_id)
        if artifact.kind == "sched":
            editions.add(_recorded_edition(
                artifact.parameters, where=f"the blind schedule {artifact.id!r}",
                remedy="Re-run run_annual(..., blind_control='lm83') to seal the schedule again.",
            ))
    for input_id in input_ids:
        call = manifest.input(input_id).registered_by
        if (
            call is not None and call.tool == "sensor_grid" and call.action == "create"
            and call.arguments.get("standard") == "lm83"
        ):
            editions.add(_recorded_edition(
                call.arguments, where=f"the sensor grid {input_id!r}",
                detail=" in the arguments of the sensor_grid call that created it",
                remedy='Re-create the grid with sensor_grid(action="create", standard="lm83").',
            ))
    return editions


def _standard_edition(editions: set[str]) -> str:
    """`STANDARD_EDITION` unless an LM-83 ancestor recorded LM-83-22: then LM-83-22 alone when no
    other LM-83 ancestor recorded LM-83-12, and both editions when one did."""
    if LM83_22 not in editions:
        return STANDARD_EDITION
    titles = " and ".join(edition_title(e) for e in (DEFAULT_LM83_EDITION, LM83_22) if e in editions)
    return f"{titles}; {_EN_17037}"


def assemble(manifest: Manifest, job_id: str) -> Report:
    """The provenance narrative for `job_id` -- docs/tool-surface.md §17's `action="report"`.

    Raises `KeyError` exactly as `Manifest.artifact` does when `job_id` names nothing.

    **`standard_edition` is the LM-83 edition the chain recorded, never a call's** (R868): the
    editions `_lm83_editions` reads off the chain's LM-83 ancestors. `STANDARD_EDITION` when none
    recorded ANSI/IES LM-83-22, a chain with no LM-83 ancestor at all included; `"ANSI/IES LM-83-22;
    BS EN 17037:2018"` when one did and none recorded LM-83-12; and `"IES LM-83-12 and ANSI/IES
    LM-83-22; BS EN 17037:2018"` when one LM-83 ancestor recorded LM-83-22 and another LM-83
    ancestor -- a `sched:<grid>` or a grid created with `standard="lm83"` -- recorded no edition, or
    LM-83-12. Raises `ManifestError` when an LM-83 ancestor records a value that is not an edition.
    """
    target = manifest.artifact(job_id)  # KeyError before assembling anything, for an unknown id
    artifact_ids, input_ids = _ancestor_ids(manifest, job_id)

    inputs = tuple(manifest.input(input_id) for input_id in input_ids)
    matrices = tuple(manifest.artifact(artifact_id) for artifact_id in artifact_ids)
    standard_edition = _standard_edition(_lm83_editions(manifest, artifact_ids, input_ids))

    # `InputRecord` carries `source` but, unlike `ProvenanceValue`, no `note` field -- a file input
    # has nowhere to put one, so `Assumption.note` stays `None` for these.
    assumptions = [
        Assumption(id=record.id, clause=record.source or "")
        for record in inputs
        if record.provenance == "default"
    ]
    ground = manifest.project.ground_reflectance
    uses_ground_reflectance = any(
        GROUND_REFLECTANCE_INPUT_ID in manifest.artifact(artifact_id).built_from
        for artifact_id in artifact_ids
    )
    if uses_ground_reflectance and ground.provenance == "default":
        assumptions.append(
            Assumption(
                id=GROUND_REFLECTANCE_INPUT_ID, clause=ground.source or "",
                value=ground.value, note=ground.note,
            )
        )

    advisories = tuple(warning for artifact in matrices for warning in artifact.warnings)

    # `rad_mcp.results_tool`'s own convention (`_recorded_chain`, `artifact.parameters.get(
    # "method", "2phase")`) is scoped to the `ill`/`ill5`/`illblend` kinds `run_annual`'s chain
    # writes -- an octree or a V/D/T matrix never carries a `"method"` key, and defaulting one to
    # `"2phase"` here would misdescribe it. `None` says truthfully that this artifact's own build
    # never recorded a method label, rather than guessing the annual convention's default onto a
    # kind that convention was never about.
    method = target.parameters.get("method")

    return Report(
        assumptions=tuple(assumptions),
        inputs=inputs,
        method=method,
        matrices=matrices,
        advisories=advisories,
        radiance_version=target.radiance_version,
        standard_edition=standard_edition,
    )
