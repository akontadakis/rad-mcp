"""The LM-83 edition a judgement is made against, and every clause number that depends on it.

Pure: no Radiance binaries, no subprocesses, no file IO -- see CONTEXT.md, Layers.

IES LM-83-12 is the default (owner decision, 2026-10-11). ANSI/IES LM-83-22 is the newer edition,
cited from ANSI/IES LM-83-22, by section number (R864).

**The rule for edition-dependent messages:** a message whose clause number differs between the
editions takes it from `section` rather than re-typing it, so an LM-83-22 payload cannot carry an
LM-83-12 clause by accident. Messages that only ever cite LM-83-12 still carry their clause as
literal text; this module does not reach them.

The two edition strings are matched exactly, letter case included: `"lm-83-22"` is not an edition,
and every function here raises `ValueError` on a value outside `LM83_EDITIONS` rather than reading
it as either edition.
"""

from __future__ import annotations

from typing import Final, Literal

__all__ = [
    "Lm83Edition",
    "LM83_EDITIONS",
    "DEFAULT_LM83_EDITION",
    "LM83_22",
    "LM83_22_NOT_APPLIED",
    "edition_title",
    "section",
    "sda_label",
    "ase_label",
]

Lm83Edition = Literal["LM-83-12", "LM-83-22"]
LM83_EDITIONS: Final[tuple[str, str]] = ("LM-83-12", "LM-83-22")
DEFAULT_LM83_EDITION: Final[Lm83Edition] = "LM-83-12"
LM83_22: Final[Lm83Edition] = "LM-83-22"

#: The LM-83-22 rules this server does not apply, one sentence for the two `LM83_EDITION`
#: messages that list them (`rad_mcp.annual_tool._lm83_edition_advisory` and
#: `rad_mcp.results_tool._compliance_lm83_edition_advisory`), so the two lists cannot drift apart.
#: Still open items for what is still to be decided; R871 and R870 for the two rules those rulings
#: closed their items without wiring (Table 4-1's louvre rows, section 4.8's dynamic glazing).
LM83_22_NOT_APPLIED: Final[str] = (
    "LM-83-22's 150 lux threshold, its blinds-open, -closed and -operated sDA reports, its dirt "
    "and mullion factors, its building summary tables, its excluded non-critical areas and its "
    "sun positions finer than 1 degree are not applied (Still open items 48, 49, 51, 52 and 54), "
    "and Table 4-1's louvre defaults and section 4.8's simplified dynamic-glazing rule are not "
    "wired (R871, R870)"
)

_TITLES: Final[dict[str, str]] = {"LM-83-12": "IES LM-83-12", "LM-83-22": "ANSI/IES LM-83-22"}

#: topic -> (LM-83-12 clause, LM-83-22 clause). LM-83-12 by its own section numbers; LM-83-22 by
#: the section numbers of ANSI/IES LM-83-22.
_SECTIONS: Final[dict[str, tuple[str, str]]] = {
    "blind_trigger": ("section 2.2.6", "section 4.6"),
    "window_group": ("section 2.2.6", "section 4.6"),
    "period": ("section 2.2.1", "section 4.4"),
    "sky_patches": ("section 2.3.2", "section 4.12.1"),
    "solar_position": ("section 2.3.1", "section 4.12.1"),
    "ase_definition": ("section 3.3", "section 3.1"),
    "ase_bands": ("section 3.1.3", "section 3.2"),
    "latitude": ("section 2.2.1 DISCUSSION", "section 1.3"),
    "reference_height": ("section 2.2.2", "sections 4.1.2 and 4.2.1"),
    "analysis_area": ("glossary", "glossary"),
    "dirt": ("section 2.2.9", "section 4.5"),
    # sDA and ASE reported together: LM-83-12 section 2.4, "annual summary values shall be reported
    # for both sDA and ASE"; LM-83-22 section 4.14, the same sentence, "so that they can be
    # considered together".
    "reporting": ("section 2.4", "section 4.14"),
}


def _require_edition(edition: str) -> None:
    """Raise `ValueError` unless `edition` is exactly one of `LM83_EDITIONS`.

    Without it an unknown value falls through every `== LM83_22` test and is read as LM-83-12:
    `"lm-83-22"` would yield `"lm-83-22 section 2.2.6"`, an LM-83-12 clause under a mis-cased name.
    """
    if edition not in LM83_EDITIONS:
        raise ValueError(
            f"{edition!r} is not an LM-83 edition; the editions are exactly {LM83_EDITIONS!r}."
        )


def edition_title(edition: Lm83Edition) -> str:
    """The edition's full title: `"IES LM-83-12"` or `"ANSI/IES LM-83-22"`."""
    _require_edition(edition)
    return _TITLES[edition]


def section(topic: str, edition: Lm83Edition) -> str:
    """The clause `edition` states `topic` in, prefixed with the edition: for example
    `section("blind_trigger", "LM-83-22") == "LM-83-22 section 4.6"`.

    Raises `KeyError` on a topic this table does not name, so a call site cannot fall back to a
    clause number of its own, and `ValueError` on an edition outside `LM83_EDITIONS`.
    """
    _require_edition(edition)
    old, new = _SECTIONS[topic]
    return f"{edition} {new if edition == LM83_22 else old}"


def sda_label(edition: Lm83Edition) -> str:
    """The sDA metric's name as `edition` prints it. Raises `ValueError` on an unknown edition."""
    _require_edition(edition)
    return "sDA300/50%" if edition == LM83_22 else "sDA_300/50%"


def ase_label(edition: Lm83Edition) -> str:
    """The ASE metric's name as `edition` prints it. Raises `ValueError` on an unknown edition."""
    _require_edition(edition)
    return "ASE_gross1000/250h" if edition == LM83_22 else "ASE_1000,250h"
