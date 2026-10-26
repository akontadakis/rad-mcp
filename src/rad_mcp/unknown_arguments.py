"""Refuse an argument a tool does not name (Phase 13 Branch D; rulings R713-R716), and a misspelt
key inside a dict argument (Phase 14 Branch A; R719).

The SDK's argument model ignores a key the tool's signature does not name, so a misspelt optional
argument ran with its default and published a wrong number, not an error. `rad_mcp.server`'s
`RadMCPServer.call_tool` calls `refuse_unknown_arguments` before the SDK validates anything. The
SDK's schema stops there: a dict argument's own keys (`sky`, `view`, ...) are never checked, so a
tool validates those itself with `unknown_key_advisories`, deep inside its handler.

Pure: no SDK server import, so it is tested without one.
"""

from __future__ import annotations

import difflib
from collections.abc import Callable, Collection, Mapping
from typing import Any, Final

from mcp_types import CallToolResult

from rad_mcp.envelope import Advisory, refuse

#: `difflib.get_close_matches`' own default. Chosen, not measured against callers: see
#: docs/tool-surface.md#unsourced-values.
CLOSE_MATCH_CUTOFF: Final = 0.6

#: At most this many closest names per unknown key. Chosen: see docs/tool-surface.md#unsourced-values.
MAX_SUGGESTIONS: Final = 2

#: Builds a retired argument's message from the call's whole, unvalidated `arguments`.
RetiredMessage = Callable[[Mapping[str, Any]], str]


def _closest_sentence(key: str, names: list[str]) -> str | None:
    """The closest-name sentence shared by `unknown_argument_message` and `unknown_key_message`, or
    `None` when nothing is close enough. The closest names are a hint, never applied: measured,
    `maintenance` on `run_electric` scores `maintenance_note` above `maintenance_factor`, so the
    full list is always given as well (R719)."""
    close = difflib.get_close_matches(key, names, n=MAX_SUGGESTIONS, cutoff=CLOSE_MATCH_CUTOFF)
    if len(close) == 1:
        return f"The closest name is {close[0]!r}."
    if close:
        return "The closest names are " + ", ".join(repr(c) for c in close) + "."
    return None


def unknown_argument_message(tool: str, key: str, known: Collection[str]) -> str:
    """The generic message: the unknown key, up to two closest names, and every valid name."""
    names = sorted(known)
    parts = [f"{tool} has no argument {key!r}."]
    if (close := _closest_sentence(key, names)) is not None:
        parts.append(close)
    parts.append(f"Its arguments are: {', '.join(names)}." if names else "It takes no arguments.")
    return " ".join(parts)


def unknown_key_message(where: str, key: str, known: Collection[str]) -> str:
    """The nested-key message (R719): the unknown key, up to two closest keys, and every valid key.
    `where` names the dict, e.g. `"sky"` or `"run_render's view"`."""
    names = sorted(known)
    parts = [f"{where} has no key {key!r}."]
    if (close := _closest_sentence(key, names)) is not None:
        parts.append(close)
    parts.append(f"Its keys are: {', '.join(names)}.")
    return " ".join(parts)


def unknown_key_advisories(
    where: str,
    mapping: Mapping[str, Any],
    known: Collection[str],
    *,
    field: str,
    explain: Mapping[str, str] | None = None,
) -> list[Advisory]:
    """One `ARGUMENT_OUT_OF_RANGE` advisory per key of `mapping` that `known` does not name, sorted;
    `field` is the top-level argument the dict arrived in (R719). Empty when every key is known.
    `explain[key]` replaces the generic message for that key."""
    explain = explain or {}
    return [
        Advisory(
            code="ARGUMENT_OUT_OF_RANGE",
            severity="error",
            field=field,
            message=explain.get(key) or unknown_key_message(where, key, known),
        )
        for key in sorted(str(k) for k in set(mapping) - set(known))
    ]


class UnknownKeysRefused(ValueError):
    """Raised by a function that validates a nested dict deep inside a tool; the tool's handler
    returns `refuse(str(exc), exc.advisories)` (R719)."""

    def __init__(self, advisories: list[Advisory]) -> None:
        super().__init__(" ".join(a.message for a in advisories))
        self.advisories = advisories


def refuse_unknown_arguments(
    tool: str,
    arguments: Mapping[str, Any],
    known: Collection[str],
    retired: Mapping[str, RetiredMessage] | None = None,
) -> CallToolResult | None:
    """The refusal for every key in `arguments` that `known` does not name, or `None`.

    One `ARGUMENT_OUT_OF_RANGE` advisory per unknown key, `field` the key as sent, sorted -- the
    shape `envelope.refuse_missing` gives absent arguments (ruling R14's one code for an argument
    refused on its face). A key in `retired` gets its tool's own message instead of the generic one.
    """
    unknown = sorted(str(key) for key in set(arguments) - set(known))
    if not unknown:
        return None
    retired = retired or {}
    advisories = [
        Advisory(
            code="ARGUMENT_OUT_OF_RANGE",
            severity="error",
            field=key,
            message=(
                retired[key](arguments)
                if key in retired
                else unknown_argument_message(tool, key, known)
            ),
        )
        for key in unknown
    ]
    return refuse(" ".join(a.message for a in advisories), advisories)
