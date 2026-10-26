"""`EngineAdvisory` -> the wire `Advisory`. The one place that knows both types.

`rad_engine.advisories.classify()` returns frozen `EngineAdvisory` dataclasses; the wire model is
`rad_mcp.envelope.Advisory`, a pydantic model carrying the JSON contract. The duplication is
deliberate and is explained on both sides: `rad_engine` sits *below* `rad_mcp` (CONTEXT.md, Layers),
so importing the wire model downward would invert the layer dependency and drag pydantic and the
`extra="forbid"` contract into the adapter.

The mapping itself still has to live somewhere, and `rad_mcp` is the only layer allowed to know both
types. Without it, docs/tool-surface.md's rule that *"every `Result.warnings` list is classified into
advisories, never discarded, and never passed through raw"* holds inside `rad_engine` and stops at
the wire.

**Two fields do not cross, and both omissions are deliberate.**

- `Advisory.field` stays `None` for anything derived from stderr. `field` names the *result key* an
  advisory is about; a Radiance stderr line qualifies the whole run and not one result field, and
  attaching it to an arbitrary key would be a claim nothing made
  (`rad_engine.advisories.EngineAdvisory`, closing paragraph).
- `EngineAdvisory.lines` and `subject` do not cross either. The wire advisory is the *finding*; the
  raw text that produced it is the *evidence*, and the evidence stays where a reader can get all of
  it rather than the merged subset one advisory happened to absorb -- `job(action="logs")` for a
  run, `manifest.json`'s per-artifact `warnings` for an artifact. An unrecognised line is not lost
  by this: `classify_line` puts the Radiance text in `message` verbatim precisely because the table
  cannot vouch for it.

**Read `message`, never `line`.** `EngineAdvisory.line` indexes `lines[0]` and raises `IndexError`
on the manifest-derived advisories, whose `lines` tuple is empty by construction
(`rad_engine.project._advisory`). Everything here goes through `message`.
"""

from __future__ import annotations

from collections.abc import Iterable

from rad_core.manifest import ArtifactWarning
from rad_engine import EngineAdvisory
from rad_mcp.envelope import Advisory

__all__ = ["to_wire", "to_wire_all", "warning_to_wire", "warnings_to_wire"]


def to_wire(advisory: EngineAdvisory, *, field: str | None = None) -> Advisory:
    """One engine advisory as the client sees it.

    `field` is an argument rather than a mapped attribute because only the caller knows whether the
    finding is about a result key. A tool that emits an advisory *about* one of its own outputs
    passes the key; a tool relaying a classified stderr line passes nothing.
    """
    return Advisory(
        code=advisory.code,
        severity=advisory.severity,
        field=field,
        message=advisory.message,
    )


def to_wire_all(advisories: Iterable[EngineAdvisory]) -> list[Advisory]:
    """The whole list, in order. Order is the order Radiance wrote them (`classify`)."""
    return [to_wire(advisory) for advisory in advisories]


def warning_to_wire(warning: ArtifactWarning) -> Advisory:
    """One `rad_core.manifest.ArtifactWarning` as the client sees it, **`field` and all**.

    The sibling `to_wire` needs and could not be, and the second half of ruling R28 as amended.
    `to_wire` takes an `EngineAdvisory`, which deliberately has no `field` slot -- it models a
    Radiance stderr line, and a stderr line qualifies the whole run rather than one result field
    (`rad_engine.advisories.EngineAdvisory`, closing paragraph). `ArtifactWarning` is the *other*
    finding shape, produced by the pure checks, and it carries `field` under ruling R24 for one
    concrete reason: **`REFLECTANCE_IMPLAUSIBLE` is emitted by both
    `rad_core.materials.reflectance_plausibility` and `transmittance_plausibility`, and `field` is
    the only thing that tells a suspicious reflectance from a suspicious transmittance.**

    So a field-bearing finding must reach the wire down *this* path and never through `to_wire`.
    `rad_engine.scene._artifact_warning_to_advisory` refuses to convert one into an `EngineAdvisory`
    at all (R28 as amended), which closes the other end: the loss fails loudly instead of arriving
    as an advisory that names no argument. `field` is copied rather than passed as a keyword,
    because unlike a stderr line the warning already knows which result key it is about -- that is
    the whole difference between the two shapes.

    `field` stays `None` where the warning carries none (`DEFAULTS_SUBSTITUTED` is about the call,
    not about one key), and `RadResult.payload` then omits the key entirely.
    """
    return Advisory(
        code=warning.code,
        severity=warning.severity,
        field=warning.field,
        message=warning.message,
    )


def warnings_to_wire(warnings: Iterable[ArtifactWarning]) -> list[Advisory]:
    """The whole list, in order. Order is the order the pure checks produced them."""
    return [warning_to_wire(warning) for warning in warnings]
