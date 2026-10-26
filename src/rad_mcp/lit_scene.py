"""What a lit run needs from the luminaire records (Phase 11a spec §7; R478, R479, R485, R487, R489,
R492, R493, R551-R555, R563-R575).

Shared by `run_point_in_time` and `run_render`; plumbing and digest checks only (CONTEXT.md). Every
record reader is `rad_mcp.luminaire_tool`'s own (R553/R506/R576), and the support record is
re-registered through that module's one spelling of it (`_register_support`, R551/R567).

Two entry points, in call order (R569):
- `verify_lit_files` makes every lit-run refusal and writes nothing -- it runs straight after the
  project is opened, also under `dry_run`;
- `commit_lit_support` is a real lit run's only project write, called at the last point before its
  first spawn, after every refusal the run can make.
"""

from __future__ import annotations

import math

from collections.abc import Callable
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from rad_core.luminaire_layout import RUN_MAX_INSTANCES, near_field_count, rotated_extent
from rad_core.manifest import InputRecord, ProvenanceValue
from rad_core.photometry_file import IesHeader
from rad_engine import open_project, save_manifest
from rad_engine.ies import (
    SOURCE_CAL_DIGEST,
    PhotometryUnverifiedError,
    modifier_collisions,
    source_cal_state,
    write_source_cal,
)
from rad_engine.project import LoadedProject
from rad_mcp.envelope import Advisory
from rad_mcp.geometry_tool import drift
from rad_mcp.luminaire_tool import (
    _known_digests,
    _material_modifiers,
    _placement_records,
    _register_support,
    _support_digests,
    _type_records,
)

__all__ = [
    "DAYLIGHT_FACTOR_WITH_LUMINAIRES_MESSAGE",
    "LitPlacementView",
    "LitSceneRefusal",
    "commit_lit_support",
    "excluded_advisory",
    "lit_advisories",
    "lit_placements_view",
    "luminaire_edge_ids",
    "near_field_advisory",
    "placement_files",
    "placement_records",
    "verify_lit_files",
]

#: R457's refusal, one spelling shared by `run_point_in_time` and `run_render` (R580) rather than two
#: copies that could drift. `code="DAYLIGHT_FACTOR_WITH_LUMINAIRES"` and `severity="error"` stay
#: literals at each `Advisory(...)` call site (R497): `tests/test_advisories.py`'s register scan reads
#: a code's severity only from a literal at the call site, so only the *message* -- which the scan
#: never reads -- is shared here.
DAYLIGHT_FACTOR_WITH_LUMINAIRES_MESSAGE = (
    "quantity='daylight_factor' with luminaires=true is refused: a daylight factor is defined under the "
    "reference overcast sky alone, so lamps cannot be part of it (R457). Use quantity='illuminance' "
    "with luminaires=true for lux from the lamps, or luminaires=false for the daylight factor."
)


class LitPlacementView(BaseModel):
    model_config = ConfigDict(extra="forbid")
    placement: str
    type_name: str
    instances: int
    colour_provenance: Literal["assumed", "default"]
    #: R563: the same published shape as `luminaire`'s `TypeView.light_loss_factor` (R517) -- one
    #: shape per quantity, and check 2.9 refuses a bare number where the name carries provenance.
    light_loss_factor: ProvenanceValue | None


class LitSceneRefusal(ValueError):
    """A refusal this module decides. **Every raise passes `code=` and `severity=` as literals**
    (R497/R554): `tests/test_advisories.py` reads a code's severity only from the call site."""

    def __init__(self, message: str, *, code: str, severity: str, field: str | None) -> None:
        super().__init__(message)
        self.code, self.severity, self.field = code, severity, field


def placement_records(manifest: Any) -> list[InputRecord]:
    return _placement_records(manifest)


def _types_by_name(manifest: Any, field: str | None = "luminaires") -> dict[str, InputRecord]:
    return {_recorded(r, "name", field): r for r in _type_records(manifest) if r.registered_by}


def _type_of(types: dict[str, InputRecord], placement: InputRecord, field: str | None = "luminaires") -> InputRecord:
    """The type record a placement names, or R566's coded refusal -- the one lookup every function here
    goes through, so a hand-edited manifest never lets an uncoded `KeyError` leave this module."""
    type_name = _recorded(placement, "type_name", field)
    try:
        record = types.get(type_name)
    except TypeError as exc:  # N-111: a hand-edited `type_name` that is not hashable (a list, say)
        raise LitSceneRefusal(
            f"luminaire record {placement.id!r} does not record a readable 'type_name' ({type(exc).__name__}: {exc}); "
            "re-place that placement.",
            code="LUMINAIRE_FILES_CHANGED", severity="error", field=field) from exc
    if record is None:
        raise LitSceneRefusal(
            f"luminaire record {placement.id!r} (placement {_recorded(placement, 'name', field)!r}) names "
            f"type {type_name!r}, which has no record in this project; re-import that type or re-place the "
            "placement.",
            code="LUMINAIRE_FILES_CHANGED", severity="error", field=field)
    return record


def _recorded(record: InputRecord, key: str, field: str | None = "luminaires", *,
              read: Callable[[Any], Any] | None = None) -> Any:
    """One recorded argument of a type or placement record -- passed through `read` when given (`len`,
    say) -- or R566's coded refusal (L-55, N-111): an argument missing from a hand-edited record
    (`KeyError`), or one `read` cannot take (`TypeError`, `IndexError`, `ValueError`), is
    `LUMINAIRE_FILES_CHANGED`, the way `_header` refuses an unreadable header, never an uncoded
    exception out of a lit tool. Callers pass only records whose `registered_by` is set: a placement
    with none is refused by `verify_lit_files` first, and `_types_by_name` skips a type with none."""
    try:
        value = record.registered_by.arguments[key]  # type: ignore[union-attr]
        return read(value) if read is not None else value
    except (KeyError, TypeError, IndexError, ValueError) as exc:
        remedy = "re-import that type" if record.kind == "luminaire_type" else "re-place that placement"
        raise LitSceneRefusal(
            f"luminaire record {record.id!r} does not record a readable {key!r} ({type(exc).__name__}: {exc}); {remedy}.",
            code="LUMINAIRE_FILES_CHANGED", severity="error", field=field) from exc


def _is_finite_number(value: Any) -> bool:
    """An `int` or `float` that is not a `bool` and is finite. An `int` past float range is not one
    (`math.isfinite` raises `OverflowError` on it, which is answered as not finite)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _point(value: Any) -> tuple[float, float, float]:
    """Three floats from a recorded triple of numbers; `ValueError` otherwise. Strict (fix round 3 of
    R844, controller ruling): a string, a bool or a non-finite value is refused, never converted, so
    `run_ugr`, `run_electric`, the other lit tools and `luminaire` agree on one record -- `"123"` was
    read here as `[1, 2, 3]` while `luminaire` refused it."""
    if not isinstance(value, (list, tuple)) or len(value) != 3 or not all(_is_finite_number(v) for v in value):
        raise ValueError(f"{value!r} is not three finite numbers")
    return (float(value[0]), float(value[1]), float(value[2]))


def _points(value: Any) -> list[tuple[float, float, float]]:
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"{value!r} is not a list of points")
    return [_point(p) for p in value]


def _rot(value: Any) -> tuple[float, float, float] | None:
    return None if value is None else _point(value)


def placement_files(manifest: Any) -> list[str]:
    """Every placement's `.rad`, in manifest order -- appended after materials and geometry (spec §7)."""
    return [f.path for r in _placement_records(manifest) for f in r.files]


def luminaire_edge_ids(manifest: Any) -> list[str]:
    """R480: the support record, every placed type and every placement -- a lit octree's extra edges."""
    placements = _placement_records(manifest)
    used = {_recorded(r, "type_name") for r in placements if r.registered_by}
    types = [r.id for name, r in _types_by_name(manifest).items() if name in used]
    support = [r.id for r in manifest.inputs if r.kind == "luminaire_support"]
    return [*support, *types, *(r.id for r in placements)]


def _unverified(state: str) -> PhotometryUnverifiedError:
    return PhotometryUnverifiedError(
        f"the project's root source.cal is {state}: every lit run needs rad_mcp's own photometric "
        "correction file there (R487). Re-import any luminaire type to rewrite it."
    )


def verify_lit_files(runtime: Any, loaded: LoadedProject, *, field: str | None = "luminaires",
                     label: str = "luminaires=true") -> None:
    """Spec §7's after-open refusals, in order, also under `dry_run`, **writing nothing** (R569):
    `LUMINAIRES_NONE_PLACED`; a placement record with no registration (R573) or naming a type with no
    record (R566); the R489 run bound; then R487 -- the root `source.cal` state, every placed type's and
    placement's files, and modifier collisions. A `"previous"` `source.cal` passes here; a real run
    rewrites it later, in `commit_lit_support`.

    `field` and `label` let a caller with no `luminaires` argument of its own (`run_electric`, R574)
    pass `field=None` and its own sentence opener, without duplicating this function (design §6 step 2).
    """
    manifest, paths = loaded.manifest, loaded.paths
    placements = _placement_records(manifest)
    if not placements:
        raise LitSceneRefusal(f"{label}, but no luminaire is placed in this project; place one with luminaire(action='place').",
                              code="LUMINAIRES_NONE_PLACED", severity="error", field=field)
    for record in placements:
        if record.registered_by is None:
            raise LitSceneRefusal(
                f"luminaire placement record {record.id!r} carries no registration, so its type and instances cannot "
                "be read; re-place it with luminaire(action='place').",
                code="LUMINAIRE_FILES_CHANGED", severity="error", field=field)
    types = _types_by_name(manifest, field)
    placed_types = [_type_of(types, r, field) for r in placements]
    # Every placement's positions and `rot` are read as `run_ugr`, `run_electric` and `luminaire` read
    # them (`_points`, `_rot`: numbers only), here where every lit tool passes, so one malformed record
    # is refused alike by all of them -- `run_render` and `run_glare` counted positions with `len`
    # and never read `rot` (final review of 2026-10-05).
    for record in placements:
        _recorded(record, "rot", field, read=_rot)
    total = sum(_recorded(r, "positions", field, read=lambda v: len(_points(v))) for r in placements)
    if total > RUN_MAX_INSTANCES:
        raise LitSceneRefusal(f"{total} luminaire instances exceed the {RUN_MAX_INSTANCES}-instance bound on one lit run (R489).",
                              code="ARGUMENT_OUT_OF_RANGE", severity="error", field=field)
    state = source_cal_state(paths.source_cal, previous_digests=_known_digests(manifest))
    if state in ("missing", "foreign"):
        raise _unverified(state)
    for record in [*placements, *placed_types]:
        for ref in record.files:
            reason = drift(ref, cap=None, roots=runtime.roots)
            if reason is not None:
                raise LitSceneRefusal(f"luminaire record {record.id!r}: {ref.path} is {reason}; re-import or re-place it.",
                                      code="LUMINAIRE_FILES_CHANGED", severity="error", field=field)
    materials = _material_modifiers(loaded)
    for record in types.values():
        collisions = modifier_collisions(_recorded(record, "modifiers", field, read=lambda v: [str(m) for m in v]), materials)
        if collisions:
            raise LitSceneRefusal(f"luminaire type {record.id!r} defines {collisions}, which are also project materials.",
                                  code="LUMINAIRE_MODIFIER_COLLISION", severity="error", field=field)


def commit_lit_support(runtime: Any, loaded: LoadedProject, *,
                       tool: Literal["run_point_in_time", "run_render", "run_electric", "run_ugr",
                                     "run_glare"]) -> None:
    """A real lit run's only project write (R485/R551/R568/R569), called at the last point before its
    first spawn, never on `dry_run`. Under one lock, against the manifest and file state re-read there:

    `tool` has no default (R585, closing R567 for `run_render`): the re-registered support record's
    `registered_by` names the lit tool that actually rewrote the file or healed the record.

    `"run_ugr"` and `"run_glare"` were added to that `Literal` by Phase 11c Task 10's fix round 1.
    `run_ugr` calls this function today (`tests/test_protocol.py`'s own annotation for that tool
    already says it writes `source.cal` through here), and design §7 schedules `run_glare`'s own
    call for Task 13; both are listed now rather than one now and one later, because the
    alternative was a type suppression at the call site that would outlive its excuse by two tasks.
    Nothing else about this function changed.

    - `"previous"`: rewrite `source.cal` with this package's bytes (an `OSError` is
      `LUMINAIRE_PHOTOMETRY_UNVERIFIED` naming it, and nothing is re-registered -- R564), then
      re-register the support record naming this run (`tool`, R567/R585);
    - `"current"` with a support record whose `package_digest` is not this package's: an earlier
      rewrite's record was never saved, so re-register it now -- the heal R568 requires;
    - `"current"` and a current record: nothing to do;
    - anything else: refused, as `verify_lit_files` would have.

    A failure re-registering or saving is `LUMINAIRE_STORED_FILES_REPLACED` (R568, after
    `luminaire_tool._write_and_register`'s R528): the file on disk is newer than its record. Its message
    says `source.cal` was rewritten only when this call rewrote it; on the heal path the file was
    already current and only the record was being re-registered (fix wave, I2 (c))."""
    paths = loaded.paths
    with runtime.modifying(paths):
        project = open_project(paths.root, radiance_version=runtime.launcher.version)
        state = source_cal_state(paths.source_cal, previous_digests=_known_digests(project.manifest))
        package, _earlier = _support_digests(project.manifest)
        if state == "current" and package == SOURCE_CAL_DIGEST:
            return
        if state == "previous":
            try:
                # Code review M4: `write_source_cal` stages a sibling and renames it in, so a failed write
                # leaves the earlier file in place, and a hardlink at the root name is replaced, never
                # written through (the rename swaps the directory entry; the old inode keeps its bytes).
                write_source_cal(paths.source_cal)
            except OSError as exc:
                # L-49: the directory sync runs after the rename, so an `OSError` can arrive with the
                # new bytes already in place. The file is read to tell the two apart; the support
                # record is unchanged either way, and a repeat run heals it (R568).
                if source_cal_state(paths.source_cal, previous_digests=set()) == "current":
                    raise PhotometryUnverifiedError(
                        f"the project's root source.cal was rewritten to this package's bytes, but the directory "
                        f"sync after the rename failed ({type(exc).__name__}: {exc}), so the rename may not survive "
                        "a crash; the support record was not changed. Repeat the lit run to record it."
                    ) from exc
                raise PhotometryUnverifiedError(
                    f"the project's root source.cal was written by an earlier rad_mcp package and could not be "
                    f"rewritten ({type(exc).__name__}: {exc}); the support record was not changed. Re-import any "
                    "luminaire type to rewrite it."
                ) from exc
        elif state != "current":
            raise _unverified(state)
        try:
            _register_support(project, paths, tool=tool, action=None)
            save_manifest(paths, project.manifest)
        except Exception as exc:
            what = (
                "was rewritten to this package's bytes and its support record was not saved"
                if state == "previous" else
                "already holds this package's bytes, but its support record still names an earlier package and "
                "re-registering it was not saved"
            )
            raise LitSceneRefusal(
                f"the project's root source.cal {what} ({type(exc).__name__}: {exc}); the file on disk is newer "
                "than its record. Repeat the lit run to record it.",
                code="LUMINAIRE_STORED_FILES_REPLACED", severity="error", field="project") from exc


def lit_placements_view(manifest: Any, field: str | None = "luminaires") -> list[LitPlacementView]:
    types = _types_by_name(manifest, field)
    views = []
    for record in _placement_records(manifest):
        if record.registered_by is None:
            continue
        type_record = _type_of(types, record, field)
        llf = _recorded(type_record, "light_loss_factor", field)
        views.append(LitPlacementView(placement=_recorded(record, "name", field, read=str),
                                      type_name=_recorded(record, "type_name", field, read=str),
                                      instances=_recorded(record, "positions", field, read=lambda v: len(_points(v))),
                                      colour_provenance="default" if _recorded(type_record, "colour_given", field) is None else "assumed",
                                      light_loss_factor=None if llf is None else ProvenanceValue(value=llf, provenance="assumed")))
    return views


def _header(record: InputRecord, field: str | None = "luminaires") -> IesHeader:
    """The type record's recorded `IesHeader`, or R566's coded refusal (S2, whole-branch fix wave): a
    hand-edited `header` -- missing (`KeyError`), not a mapping or short of a field (`TypeError`), or
    holding a field `IesHeader` refuses, a wrong type or a non-finite number (`ValueError`, L-53) --
    is `LUMINAIRE_FILES_CHANGED`, the way `_type_of` refuses a
    missing type record, never an uncoded exception out of `run_point_in_time`, `run_render` or
    `run_electric` (F9: reached here, through `lit_advisories`, never through `verify_lit_files`)."""
    try:
        return IesHeader(**record.registered_by.arguments["header"])  # type: ignore[union-attr]
    except (TypeError, KeyError, ValueError) as exc:
        raise LitSceneRefusal(
            f"luminaire type record {record.id!r} carries no readable IES header ({type(exc).__name__}: {exc}); "
            "re-import that type.",
            code="LUMINAIRE_FILES_CHANGED", severity="error", field=field) from exc


def lit_advisories(manifest: Any, *, quantity: str, field: str | None = "luminaires",
                   initial_notice: bool = True, result_field: str = "values") -> list[Advisory]:
    """`DEFAULTS_SUBSTITUTED` for a defaulted colour; `RESULT_IS_INITIAL_ILLUMINANCE`, at `quantity`
    "illuminance", "luminance" or "dgp" (R821, each with its own message), only when nothing
    dimmed a placed lamp -- no `light_loss_factor` and no header factor applied (R493). The header
    factors are `IesHeader.applied_factors`, the one spelling `luminaire(action="import")` announces.
    Each message names the lamps' share: a lit run may also carry a sky, whose daylight is not lamp
    output.

    `field` names no argument for a caller with none of its own (`run_electric`, R574); `initial_notice`
    lets `run_electric` omit `RESULT_IS_INITIAL_ILLUMINANCE` entirely (R607), since its own maintenance
    factor already speaks to whether the result is initial or maintained. `result_field` is the result
    key the notice is about (R821): "values" for `run_point_in_time`, "hdr" for `run_render` (whose
    result has no `values`), "dgp" for `run_glare`."""
    views = lit_placements_view(manifest, field)
    types = _types_by_name(manifest, field)  # every view's type exists: `lit_placements_view` refused otherwise
    advisories: list[Advisory] = []
    defaulted = sorted({v.type_name for v in views if v.colour_provenance == "default"})
    if defaulted:
        advisories.append(Advisory(code="DEFAULTS_SUBSTITUTED", severity="warn", field=field,
                                   message=f"luminaire types {defaulted} use ies2rad's default white light colour (provenance default)."))
    dimmed = [
        view.type_name for view in views
        if view.light_loss_factor is not None or _header(types[view.type_name], field).applied_factors
    ]
    # R821: a luminance render's and a glare run's lamps are as undimmed as an illuminance run's, so the
    # notice reaches them under the same code (codes are never renamed), each message naming its quantity.
    # The DGP message makes no direction claim: in the DGP formula, brighter lamps raise both the source
    # luminances and the vertical illuminance the contrast term divides by, so a maintained (dimmer) run
    # can give a higher DGP when the glare source is daylight.
    if initial_notice and quantity == "illuminance" and not dimmed:
        advisories.append(Advisory(code="RESULT_IS_INITIAL_ILLUMINANCE", severity="info", field=result_field,
                                   message="the lamps' share of these illuminance values is at initial output, not "
                                           "maintained: no light_loss_factor or header factor dims any placed luminaire type."))
    elif initial_notice and quantity == "luminance" and not dimmed:
        advisories.append(Advisory(code="RESULT_IS_INITIAL_ILLUMINANCE", severity="info", field=result_field,
                                   message="the lamps' share of these luminances is at initial output, not maintained: no "
                                           "light_loss_factor or header factor dims any placed luminaire type."))
    elif initial_notice and quantity == "dgp" and not dimmed:
        advisories.append(Advisory(code="RESULT_IS_INITIAL_ILLUMINANCE", severity="info", field=result_field,
                                   message="the lamps' share of the luminances this DGP is computed from is at initial "
                                           "output, not maintained: no light_loss_factor or header factor dims any placed "
                                           "luminaire type. Maintained output does not necessarily give a lower DGP: dimmer "
                                           "lamps also lower the vertical illuminance its contrast term divides by."))
    return advisories


def excluded_advisory(manifest: Any, *, severity: Literal["warn", "info"], tool: str) -> Advisory | None:
    """`LUMINAIRES_EXCLUDED` (spec §7, R479): `warn` for a caller who left `luminaires=false`, `info`
    for a tool that cannot include them. Two literal branches, so the register scan reads each (R554).
    Only the `warn` branch names `field="luminaires"`: `run_annual` takes no such argument (R574).

    **`run_glare` moved to the `warn` branch in Phase 11c Task 13**, once it gained a `luminaires`
    argument of its own to name -- R574's reason for `field=None` on the `info` branch now covers
    only `run_annual`; the half naming `run_glare` is retired."""
    count = len(_placement_records(manifest))
    if count == 0:
        return None
    if severity == "warn":
        return Advisory(code="LUMINAIRES_EXCLUDED", severity="warn", field="luminaires",
                        message=f"{count} luminaire placement(s) in this project are not in this result: luminaires=false.")
    return Advisory(code="LUMINAIRES_EXCLUDED", severity="info", field=None,
                    message=f"{count} luminaire placement(s) in this project are not in this result: {tool} cannot include luminaires.")


def near_field_advisory(manifest: Any, sensors: list[tuple[float, float, float]],
                        field: str | None = "luminaires") -> Advisory | None:
    """R492/R512/R552: sensors closer to an instance's rotated emitter box than its type's unrotated
    horizontal footprint, `max(x extent, y extent)` of the type record's `emitter_min`/`emitter_max`.
    The rotated box is `rotated_extent` of the **current** type record's extent under the placement's
    own `rot`, not the placement's recorded `rotated_min`/`rotated_max` snapshot (R726): the snapshot
    is recorded and refreshed by a re-import (R724), but a manifest re-imported before R724, or edited
    by hand, can carry a stale one; the overlap check reads the type record for the same reason (R675).

    N-111: every recorded value is read through `_recorded`, so a hand-edited placement missing `rot`
    or `positions`, or a type missing `emitter_min`/`emitter_max` (or holding a short or non-numeric
    triple), is `LUMINAIRE_FILES_CHANGED` under `field`, not an uncoded exception."""
    types = _types_by_name(manifest, field)
    instances = []
    for record in _placement_records(manifest):
        if record.registered_by is None:
            continue
        type_record = _type_of(types, record, field)
        emitter_min = _recorded(type_record, "emitter_min", field, read=_point)
        emitter_max = _recorded(type_record, "emitter_max", field, read=_point)
        footprint_m = max(emitter_max[0] - emitter_min[0], emitter_max[1] - emitter_min[1])
        rot = _recorded(record, "rot", field, read=_rot)
        low, high = rotated_extent(emitter_min, emitter_max, rot)
        instances.extend((p, low, high, footprint_m) for p in _recorded(record, "positions", field, read=_points))
    count = near_field_count(sensors, instances)  # type: ignore[arg-type]
    if count == 0:
        return None
    return Advisory(code="LUMINAIRE_NEAR_FIELD", severity="warn", field="grid",
                    message=f"{count} of {len(sensors)} sensors are closer to a luminaire than its longest horizontal "
                            "footprint dimension; far-field photometry and -ds 0.2 are least accurate there (R492).")
