"""The one rule for a name that is both a manifest input id and a filename.

**Why this module exists at all.** Three tools take a `name` that does the same two jobs: it becomes
a manifest input id (`weather_athens`, `bsdf_venetian_45`) *and* a filename inside the project
(`04_skies/athens.wea`, `05_bsdf/venetian_45.xml`). Until 2026-08-12 each tool carried its own
character-for-character copy of the same regex, with each copy's comment citing the others as *"the
same rule and the same reasoning"* -- a convention held by prose, which is exactly the arrangement
that lets the next copy inherit half of it.

That is not hypothetical: the duplication is what made the leading-hyphen weakness below a standing
open decision rather than a fix. Tightening one copy and not the others would have left three subtly
different rules for one job, which the plan's own note calls *"a worse state than the current one and
exactly how the next copy inherits the wrong half"*. Hoisting the rule here makes "one decision about
all of them" structural instead of a promise, and it is why [R61](../../docs/plan.md#settled) could
then take that decision in a single place.

## What the rule is, and what each half of it defends

    \\A[A-Za-z0-9_][A-Za-z0-9_.-]{0,63}\\Z

A **whitelist**, because the failure mode of an over-tight name is a caller renaming their file and
the failure mode of an over-loose one is a path escape.

- **1-64 characters** total: the first, plus up to 63 more.
- **The first character may not be `-` or `.`**, which is the half [R61](../../docs/plan.md#settled)
  added. A leading `-` makes the name a filename that a command-line tool can read as a **flag**
  rather than a path -- `05_bsdf/-rf.xml` handed to a binary as `-rf`. A leading `.` makes it a
  hidden file, and `.`/`..` are the two names that traverse.
- **`.` is allowed after the first character**, so `tmy.2020` and `blind.closed` stay legal names.

**The leading-hyphen hole was confirmed *not* exploitable when it was closed, and it was closed
anyway.** The Phase 4 security pass measured it: `ProjectPaths` yields absolute directories, so the
argv token a Radiance binary actually receives always begins with `/`, and `shell=True` appears
nowhere in `src/`. So this is defence in depth against a future call site that builds a relative
path or reaches a shell -- the class of change that is easy to make and hard to notice, on a rule
that then has to be re-derived from three places. Closing it cost one character class.

An all-dots name (`.`, `..`, `...`) is refused by the leading-character class outright, so the
separate all-dots test the two older copies carried is now redundant -- kept in
`refuse_reason` only as the explanation, not as a second check.
"""

from __future__ import annotations

import re
from typing import Final

__all__ = ["NAME_PATTERN", "is_legal_name", "name_refusal_reason", "SENSOR_GRID_INPUT_PREFIX"]

#: The one pattern. See the module docstring for what each half defends.
#:
#: Anchored with `\A`/`\Z` rather than `^`/`$`: `$` matches before a trailing newline, so
#: `^[A-Za-z0-9_]...$` would accept `"athens\n"` -- a name carrying a newline into a manifest id and
#: a filename alike. `\Z` has no such exception. The three copies this replaced were all correctly
#: anchored; stating it here keeps that property from being lost the next time the rule is edited.
NAME_PATTERN = re.compile(r"\A[A-Za-z0-9_][A-Za-z0-9_.-]{0,63}\Z")


def is_legal_name(name: str) -> bool:
    """Whether `name` may be both a manifest input id and a filename."""
    return NAME_PATTERN.match(name) is not None


def name_refusal_reason(name: str, *, kind: str, examples: str) -> str:
    """The refusal text for an illegal name, in one voice across every tool that has one.

    `kind` names what is being named (`"weather"`, `"BSDF"`, `"sensor grid"`) and `examples` gives
    two or three legal names for that tool, because a refusal that does not show the caller a legal
    value makes them guess at the charset.

    The message states the rule rather than echoing the regex: a caller reading `\\A[A-Za-z0-9_]` has
    to parse it to learn they may not start with a hyphen.
    """
    return (
        f"{kind} name {name!r} is refused. This name becomes a manifest input id and a filename "
        "inside the project, so it is constrained to 1-64 characters of ASCII letters, digits, "
        "underscore, hyphen and dot, and it may not begin with a hyphen or a dot -- a leading "
        "hyphen makes a filename that a command-line tool can read as a flag, and a leading dot "
        f"makes a hidden file ('.' and '..' being the two names that traverse). Rename it -- e.g. "
        f"{examples}."
    )


#: The input-id prefix `sensor_grid(action="create")` writes, and that four other modules read back.
#:
#: **One definition, never two that agree.** This was a bare `"sensor_grid_"` literal at six sites
#: -- one write (`server.py`) and five reads (`server.py`, `point_in_time_tool`, `results_tool`
#: twice, `annual_tool`) -- while `geometry_tool`, `bsdf_tool`, `window_group_tool` and
#: `weather_tool` each factor theirs behind an `_INPUT_PREFIX`. Two of the six were new or moved on
#: the Phase 7c branch. It is the same shape as `_window_group_names`, which this repo has already
#: caught once.
#:
#: **It lives here rather than beside its writer** for the reason this module exists at all: the
#: writer is `server.py`, which sits at the top of the import graph and imports every tool module,
#: so a tool reading the constant from there would be a cycle. `annual_tool` importing
#: `window_group_tool._INPUT_PREFIX` works only because the writer happens to be a leaf; this one
#: is not, and the fix for that is a lower shared home, not five copies. This module is that home:
#: it already owns the rule for what a manifest input id may be made of, and this is what one of
#: them is made of.
SENSOR_GRID_INPUT_PREFIX: Final[str] = "sensor_grid_"
