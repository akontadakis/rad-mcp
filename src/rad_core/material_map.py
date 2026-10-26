"""The JSON material map beside an OBJ -- Phase 10 design §6.

Pure. The map **sets** material values (owner decision 1); this module only validates the document
and translates each surface row into the assignment shape `materials(action="set")` takes, so the
values reach the project through that tool's own code path. Nothing named in the document is ever
opened: a luminaire row's `ies`/`spd`/`rot` are not validated here, only returned as written in
`MaterialMap.luminaire_rows`, which `luminaire(action="place")` reads `ies` and `rot` from (and `spd`
only to say it is not read).
"""

from __future__ import annotations

import json
import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Final, Literal

from rad_core.geometry import GeometryError
from rad_core.manifest import Units

__all__ = [
    "MATERIAL_MAP_MAX_BYTES",
    "OBJ_NAMES_LISTED_MAX",
    "MaterialMap",
    "MaterialMapError",
    "MaterialUnmappedError",
    "parse_material_map",
]

#: The largest material map accepted, in bytes. **A default, not a measurement**: the owner's map
#: is 3 005 bytes. Too low refuses a large map; too high reads an arbitrary file into memory.
MATERIAL_MAP_MAX_BYTES: Final[int] = 1_048_576

#: How many names one refusal message lists before counting the rest. **A default**: too low hides
#: names a caller must add; too high floods the message.
OBJ_NAMES_LISTED_MAX: Final[int] = 50

_UNITS: Final = ("m", "cm", "mm", "ft", "in")
_SURFACE_KEYS: Final = frozenset({"type", "refl", "roughness", "specularity", "provenance", "source"})
_GLASS_KEYS: Final = frozenset({"type", "tvis", "provenance", "source"})
_LUMINAIRE_KEYS: Final = frozenset({"type", "ies", "spd", "rot"})
_ROLE: Final[dict[str, str | None]] = {"plastic": None, "floor": "floor", "ground": "ground", "context": None}


class MaterialMapError(GeometryError):
    code = "ARGUMENT_OUT_OF_RANGE"
    field = "material_map"


class MaterialUnmappedError(GeometryError):
    code = "OBJ_MATERIAL_UNMAPPED"
    field = "material_map"


def _listed(names: Sequence[str]) -> str:
    shown = ", ".join(repr(name) for name in names[:OBJ_NAMES_LISTED_MAX])
    rest = len(names) - OBJ_NAMES_LISTED_MAX
    return shown + (f" and {rest} more" if rest > 0 else "")


@dataclass(frozen=True)
class MaterialMap:
    source: str
    units: Units | None
    up_axis: Literal["Z", "Y"] | None
    assignments: dict[str, dict[str, Any]]
    luminaire_names: frozenset[str]
    context_names: frozenset[str]
    glass_names: frozenset[str]
    #: Each luminaire row as the document wrote it, keyed by its name (N-67): the one parse
    #: `luminaire(action="place")` reads, so the map is never decoded a second way.
    luminaire_rows: dict[str, dict[str, Any]]

    def refuse_unmapped(self, discovered: Sequence[str]) -> None:
        known = set(self.assignments) | self.luminaire_names
        unmapped = [name for name in discovered if name not in known]
        if unmapped:
            raise MaterialUnmappedError(
                f"the material map has no row for {_listed(unmapped)}. Every name the OBJ uses needs "
                f"a row: a generated rule file deletes a surface no rule matches. Names the OBJ uses: "
                f"{_listed(list(discovered))}."
            )

    def unused(self, discovered: Sequence[str]) -> tuple[str, ...]:
        used = set(discovered)
        return tuple(sorted((set(self.assignments) | self.luminaire_names) - used))


def _no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    document: dict[str, Any] = {}
    for key, value in pairs:
        if key in document:
            raise MaterialMapError(f"the material map repeats the duplicate key {key!r}; JSON keeps only the last.")
        document[key] = value
    return document


def _no_constant(name: str) -> float:
    raise MaterialMapError(f"the material map contains {name}, which is not a finite number.")


def _finite_float(text: str) -> float:
    value = float(text)
    if not math.isfinite(value):
        raise MaterialMapError(f"the material map contains {text}, which is not finite.")
    return value


def _unit_interval(name: str, row: dict[str, Any], key: str) -> float:
    value = row[key]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise MaterialMapError(f"row {name!r}: {key} must be a number, got {value!r}.")
    if not 0.0 <= float(value) <= 1.0:
        raise MaterialMapError(f"row {name!r}: {key}={value!r} is outside [0, 1].")
    return float(value)


def _row(name: str, row: dict[str, Any], *, source: str) -> tuple[str, dict[str, Any] | None]:
    kind = row.get("type")
    allowed = {"glass": _GLASS_KEYS, "luminaire": _LUMINAIRE_KEYS}.get(kind) if isinstance(kind, str) else None
    if allowed is None and kind in _ROLE:
        allowed = _SURFACE_KEYS
    if allowed is None:
        raise MaterialMapError(
            f"row {name!r}: type {kind!r} is not one of plastic, floor, ground, context, glass or luminaire."
        )
    unknown = sorted(set(row) - allowed)
    if unknown:
        raise MaterialMapError(f"row {name!r} ({kind}) has unknown key(s) {unknown!r}; it accepts {sorted(allowed)!r}.")
    if kind == "luminaire":
        return kind, None
    provenance = row.get("provenance", "assumed")
    if provenance not in ("measured", "assumed"):
        raise MaterialMapError(
            f"row {name!r}: provenance {provenance!r} is not 'measured' or 'assumed'. 'default' means the "
            "server substituted a value from a table, which a material map never does."
        )
    row_source = row.get("source", source)
    if not isinstance(row_source, str):
        raise MaterialMapError(f"row {name!r}: source must be a string.")
    required = "tvis" if kind == "glass" else "refl"
    if required not in row:
        raise MaterialMapError(f"row {name!r} ({kind}) needs {required}.")
    if kind == "glass":
        return kind, {"type": "glass", "transmittance": _unit_interval(name, row, "tvis"),
                      "provenance": provenance, "source": row_source}
    spec: dict[str, Any] = {"type": "plastic", "reflectance": _unit_interval(name, row, "refl")}
    if "roughness" in row:
        spec["roughness"] = _unit_interval(name, row, "roughness")
    if "specularity" in row:
        spec["specular"] = _unit_interval(name, row, "specularity")
    spec["provenance"] = provenance
    spec["source"] = row_source
    if _ROLE[kind] is not None:
        spec["role"] = _ROLE[kind]
    return kind, spec


def parse_material_map(data: bytes, *, source: str) -> MaterialMap:
    if len(data) > MATERIAL_MAP_MAX_BYTES:
        raise MaterialMapError(
            f"the material map is {len(data)} bytes, over the {MATERIAL_MAP_MAX_BYTES}-byte cap "
            "(MATERIAL_MAP_MAX_BYTES, a default recorded under docs/tool-surface.md 'Unsourced values')."
        )
    try:
        document = json.loads(
            data.decode("utf-8"),
            object_pairs_hook=_no_duplicates, parse_constant=_no_constant, parse_float=_finite_float,
        )
    except MaterialMapError:
        raise
    except (UnicodeDecodeError, ValueError) as exc:
        raise MaterialMapError(f"the material map is not valid JSON: {exc}") from None
    if not isinstance(document, dict):
        raise MaterialMapError("the material map must be a JSON object whose keys name OBJ materials.")

    units: Units | None = None
    up_axis: Literal["Z", "Y"] | None = None
    assignments: dict[str, dict[str, Any]] = {}
    luminaires: dict[str, dict[str, Any]] = {}
    contexts: set[str] = set()
    glass: set[str] = set()
    for key, value in document.items():
        if key.startswith("_"):
            continue
        if key == "units":
            if value not in _UNITS:
                raise MaterialMapError(f"units {value!r} is not one of {list(_UNITS)!r}.")
            units = value
        elif key == "up_axis":
            normalised = value.strip().upper() if isinstance(value, str) else None
            if normalised not in ("Z", "Y"):
                raise MaterialMapError(f"up_axis {value!r} is not 'Z' or 'Y'.")
            up_axis = normalised  # type: ignore[assignment]
        elif key == "north_angle_deg":
            if isinstance(value, bool) or not isinstance(value, (int, float)) or value != 0:
                raise MaterialMapError(
                    f"north_angle_deg {value!r} is refused: this server never rotates a building, it "
                    "rotates the sky. Leave north_angle_deg at 0 here and state north where the run takes it."
                )
        elif isinstance(value, dict):
            kind, spec = _row(key, value, source=source)
            if spec is None:
                luminaires[key] = value
                continue
            assignments[key] = spec
            if kind == "context":
                contexts.add(key)
            if kind == "glass":
                glass.add(key)
        else:
            raise MaterialMapError(
                f"key {key!r} must be an object describing the material, got {value!r}. Library "
                "names are not accepted: a default is never a measurement."
            )
    return MaterialMap(source, units, up_axis, assignments, frozenset(luminaires), frozenset(contexts), frozenset(glass),
                       luminaires)
