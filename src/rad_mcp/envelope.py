"""Building tool results.

The one place in the server where a `CallToolResult` is constructed. Two rules live here, both
from the MCP specification revision 2026-07-28, and both stated in
docs/tool-surface.md#protocol-conformance:

- **A declared output schema binds.** *"If an output schema is provided: Servers MUST provide
  structured results that conform to this schema"* -- so every tool's return type is a model in
  this module's shape, not a bare ``dict``. A ``dict[str, Any]`` return annotation yields the
  schema ``{"type": "object", "additionalProperties": true}``, which cannot fail, and a schema that
  cannot fail is decoration.
- **A refusal is a tool execution error.** *"Input validation errors ... Business logic errors ...
  are reported in tool results with `isError: true`"*, because *"Clients SHOULD provide tool
  execution errors to language models to enable self-correction."*

``is_error`` describes the **call**; an `error`-severity advisory describes a **value**. They are
not the same flag and are deliberately not derived from each other: `radiance_status` reporting an
install whose library directory is missing has done its job and returns `is_error=False` carrying
an `error` advisory, while a call that could not do its job sets `is_error=True`.
"""

from __future__ import annotations

import json
from typing import Any, ClassVar, Literal

from mcp_types import CallToolResult, TextContent
from pydantic import BaseModel, ConfigDict

__all__ = [
    "Advisory",
    "RadResult",
    "Refusal",
    "Severity",
    "emit",
    "one_of_schema",
    "refuse",
    "refuse_missing",
]

Severity = Literal["info", "warn", "error"]


# R810 moved this from the published text: codes come from the advisory-code register in
# docs/tool-surface.md, allocated once and never renamed.
class Advisory(BaseModel):
    """One entry of an `advisories` list: a finding about the call or a value in its result.

    `code` is a stable machine-readable code; branch on it, not on `message`. `field` names the
    argument or result field the finding is about, and is absent or `null` when it is about none.
    An `error` advisory does not by itself mean the call was refused; a refusal has `refused: true`.
    """

    model_config = ConfigDict(extra="forbid")

    code: str
    severity: Severity
    field: str | None = None
    message: str


class RadResult(BaseModel):
    """Base of every tool result.

    Subclasses declare their own fields. ``OMIT_WHEN_NONE`` names the keys that disappear from the
    payload when they are ``None`` -- ``advisories`` is omitted when empty, which is the existing
    convention. Every other ``None`` is emitted as an explicit ``null``, because some are
    load-bearing: `progress` is null when a step cannot report it, and EN 17037's `overall` is
    structurally always null. A blanket ``exclude_none`` would silently delete both.
    """

    model_config = ConfigDict(extra="forbid")

    advisories: list[Advisory] | None = None

    OMIT_WHEN_NONE: ClassVar[frozenset[str]] = frozenset({"advisories"})

    def payload(self) -> dict[str, Any]:
        """The `structuredContent` for this result."""
        data = self.model_dump(mode="json")
        for key in type(self).OMIT_WHEN_NONE:
            if data.get(key) is None:
                data.pop(key, None)
        for advisory in data.get("advisories") or []:
            if advisory.get("field") is None:
                advisory.pop("field", None)
        return data


# R810 moved this from the published text. The shape is docs/tool-surface.md#refusals'
# `{"refused": true, "reason": ..., "advisories": [...]}`. It is a member of every tool's declared
# output union, not a shape that escapes the schema: `isError: true` does not exempt a result from
# "Servers MUST provide structured results that conform to this schema", and a refusal that failed
# its own tool's schema would be rejected at the boundary. `refused` is `Literal[True]` so no
# successful branch of a `one_of_schema` union can satisfy it.
class Refusal(RadResult):
    """The call was refused, and the tool result has `isError: true`.

    `reason` says why and what to change. `advisories` carries the coded cause, each with the
    `field` to fix when an argument is at fault; it is absent or empty when no code applies, and
    `reason` is then the whole answer.
    """

    refused: Literal[True] = True
    reason: str


def refuse(reason: str, advisories: list[Advisory] | None = None) -> CallToolResult:
    """The refusal envelope: `isError: true` **and** the payload the model needs to self-correct.

    Both halves are load-bearing. The flag is how the host renders it as a failure and how the
    client knows to hand it back; the payload is how the model knows what to fix
    (docs/tool-surface.md#a-refusal-is-a-tool-execution-error-not-a-successful-result).

    `advisories` may legitimately be empty: the register is the complete allocated list of codes and
    codes are allocated once and never renamed, so a refusal the register has no code for carries
    its reason as prose rather than a fabricated code (docs/tool-surface.md#advisory-code-register).
    """
    return emit(Refusal(reason=reason, advisories=advisories or None), is_error=True)


def refuse_missing(call: str, *, hint: str | None = None, **arguments: Any) -> CallToolResult | None:
    """The refusal for required arguments that were not given, or ``None`` if they all were.

    Ruling R14 allocates ``ARGUMENT_OUT_OF_RANGE`` for *an argument refused on its face*, and the
    owner extended it to cover a missing required argument as well as one supplied with a bad value:
    same channel, same recovery shape -- the model reads the code, re-reads the schema, and retries
    with the argument supplied. Splitting the class in two would leave half the argument refusals
    machine-branchable and half of them prose, and *"a register that is inconsistent about its own
    class boundaries is one nobody trusts"*.

    **One advisory per absent argument, each carrying its own ``field``.** ``field`` is what makes
    the advisory actionable -- the code says *what kind* of thing went wrong and the field says
    *which* -- so a call missing both ``root`` and ``units`` returns two advisories and not one
    naming a list. A single advisory would force the client to parse the reason sentence to find out
    what to supply, which is the work the structured channel exists to remove.

    Shared between the tools rather than written twice: this is the wire's refusal shape, and two
    copies of it would drift the same way the register's codes are allocated once to avoid.
    """
    absent = sorted(key for key, value in arguments.items() if value is None)
    if not absent:
        return None

    reason = f"{call} requires {', '.join(absent)}. " + (
        hint or "It has no default: see docs/tool-surface.md#required-with-no-default."
    )
    return refuse(
        reason,
        [
            Advisory(
                code="ARGUMENT_OUT_OF_RANGE", severity="error", field=name, message=reason
            )
            for name in absent
        ],
    )


def emit(
    result: RadResult, *, is_error: bool = False, meta: dict[str, Any] | None = None
) -> CallToolResult:
    """Wrap a result model as a tool result.

    The JSON is carried twice on purpose: in `structuredContent` for a client that validates
    against the tool's `outputSchema`, and mirrored into a text block because *"a tool that returns
    structured content SHOULD also return the serialized JSON in a TextContent block."*

    ``meta`` is the result's `_meta`, and it is **not** a second place to put results. It carries
    protocol-level annotations addressed to something other than the model -- today only
    `rad_mcp.tasks.HANDOFF_META_KEY`, which marks a `run_*` result as a job hand-off so the Tasks
    interceptor can convert it for a client that declared the extension, and which that interceptor
    strips before the result reaches the wire. Anything a client should *read* belongs in the
    payload, where the declared output schema binds it.
    """
    payload = result.payload()
    # **`allow_nan=False`, and the flag is the second half of ruling R200.** The default `True`
    # made the sentence above -- *"the JSON is carried twice"* -- false for exactly one class of
    # value, and made it false in two different directions at once. Measured on a real
    # `CallToolResult` before this flag was set:
    #
    #     json.dumps(payload)  -> {"area_m2": NaN, "area_sf": Infinity, "refused": false}
    #     structured_content   -> {"area_m2": null, "area_sf": null,    "refused": false}
    #
    # `NaN` and `Infinity` are not JSON (RFC 8259 defines neither), so Go's `encoding/json`,
    # `serde_json` and `JSON.parse` reject the **whole message** on the text mirror; while a client
    # validating `structuredContent` against the published `outputSchema` sees `null` on a field
    # declared a **required, non-nullable `float`** -- a schema violation rather than a parse
    # failure. Two carriers of "the same JSON", two different answers, neither of them the value.
    #
    # A `ValueError` here is deliberately *not* caught and turned into a refusal. A tool that cannot
    # serialize its own result has computed something no client can be told about, and the loud
    # failure is the alarm; silently emitting two disagreeing documents is what this replaces. The
    # producers are guarded at their own sources -- `rad_core.grids._as_points` and
    # `polygon_area_m2` for the polygon route that reached this (R200's own), and
    # `ProvenanceValue._the_value_must_be_finite` for the manifest route -- so this is the backstop
    # under those, not the working refusal.
    return CallToolResult(
        content=[TextContent(type="text", text=json.dumps(payload, indent=2, allow_nan=False))],
        structured_content=payload,
        is_error=is_error,
        meta=meta,
    )


def one_of_schema(schema: dict[str, Any], base: type[BaseModel]) -> dict[str, Any]:
    """Rewrite a `RootModel[Union[...]]` schema into the `oneOf` a state-dependent tool publishes.

    Required by docs/tool-surface.md#results-are-structured-and-a-structured-result-has-a-schema:
    *"`job` returns a shape that varies by state, and one tool gets one schema ... The schema is
    therefore a `oneOf` discriminated on `state`, with `job_id`/`project`/`tool`/`state`/
    `created_at` in the shared base — not a permissive object that would let a `running` payload
    validate as `succeeded`."*

    Two rewrites, and each is doing something the generated schema does not:

    - **`anyOf` becomes `oneOf`.** `anyOf` is satisfied by *at least* one branch, which is true of
      an unsound union as well as a sound one; `oneOf` demands *exactly* one and therefore fails
      loudly the day two branches start overlapping. That is only a legitimate rewrite while the
      branches are mutually exclusive, so it is asserted rather than assumed --
      `tests/test_job_tool.py` validates every payload the tool can produce and every branch is
      required to match exactly one.
    - **The shared base is inlined as top-level `type`/`properties`.** A bare union publishes
      neither, and check 0.5 requires every tool's schema to name an object with properties. The
      base is not repeated in `required`: it constrains the common fields' *types* wherever they
      appear, and which of them are mandatory is each branch's business.

    Each branch keeps `additionalProperties: false` (from ``RadResult``'s ``extra="forbid"``), which
    is what makes a mislabelled payload fail rather than validate against a longer sibling.
    """
    branches = schema.pop("anyOf", None)
    if branches is None:  # pragma: no cover - a one-member union is a bug in the caller
        raise ValueError(
            "one_of_schema expects a union root model; got a schema with no 'anyOf'. A single-shape "
            "result should declare that shape directly."
        )

    base_schema = base.model_json_schema()
    definitions = schema.setdefault("$defs", {})
    definitions.update(base_schema.pop("$defs", {}))

    schema["type"] = "object"
    schema["properties"] = base_schema.get("properties", {})
    schema["oneOf"] = branches
    return schema
