"""Classifying Radiance stderr into advisories.

A Radiance process that exits 0 having written warnings is the single most common way to get a
confidently wrong answer (CONTEXT.md). ``oconv`` returns 0 with a well-formed 77-byte octree when
every ``!xform`` include failed; ``rcontrib`` returns 0 with a perfectly correct V matrix while
saying it found no light sources. The exit code cannot tell those apart, and neither can a human
reading a wall of stderr. This module is the table that can.

Three outcomes, and the third is the one that keeps the table honest
([docs/plan.md](../../docs/plan.md) check 1.8):

- a line matching a **known-benign** row becomes ``info`` -- it is surfaced, not hidden, but it does
  not make a correct run look broken;
- a line matching a **known-fatal** row becomes ``error`` -- the result exists but must not be used;
- **anything else** becomes ``warn`` and is passed through *verbatim*: never dropped, never
  truncated, never reworded. An unrecognised Radiance message is not evidence of nothing happening,
  it is evidence that this table is incomplete, and the user is better placed to judge it than we
  are. Rows are added as later phases source them from the Radiance guides; the fallback is what
  makes an incomplete table safe.

**Matching is by stable substring, not regex.** The failure mode that matters here is a pattern
that over-matches and swallows a real error into ``info``. Where a message is ambiguous the rule is
to have no row at all and let it fall through to ``warn``.

**What this module classifies, and what it must not claim.** A stderr line is evidence about a run;
it is not a measurement of the artifact. So a dropped ``!xform`` include is reported as
``OCTREE_INPUT_DROPPED`` -- true of the run whatever the octree turned out to be -- and never as
``OCTREE_EMPTY``, which the register defines as *"bounding cube size 0 with exit status 0"* and
which only ``bounding_cube()`` in `octree.py` can prove. The distinction is not pedantry: an
eight-file scene that lost one include compiles a bounding cube of the right size and the wrong
scene, and calling that ``OCTREE_EMPTY`` would report a code whose own definition is untrue of the
run.

**Why this duplicates a model in the layer above.** The wire-format ``Advisory``
(`src/rad_mcp/envelope.py`) is a pydantic model with the JSON contract on it -- the ``field`` key,
``extra="forbid"``, the omit-when-empty rule. ``rad_engine`` sits
*below* ``rad_mcp`` (CONTEXT.md, Layers), so importing that model here would invert the dependency
and drag pydantic and the wire contract into the adapter layer. Instead this module emits a plain
frozen dataclass and ``rad_mcp`` maps it -- a few fields copied at the boundary, in exchange for a
layer rule that holds. The duplication is the cheaper of the two costs, and it is deliberate.

Codes come from the register in [docs/tool-surface.md](../../docs/tool-surface.md), which is the
complete allocated list: codes are allocated once and never renamed, so a row here must cite an
existing code *at a severity that code was allocated at*. `tests/test_advisories.py` parses the
register and checks both.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

__all__ = [
    "EngineAdvisory",
    "Rule",
    "RULES",
    "Severity",
    "UNCLASSIFIED_CODE",
    "classify",
    "classify_line",
]

Severity = Literal["info", "warn", "error"]
"""Deliberately a copy of ``rad_mcp.envelope.Severity`` -- see the module docstring on layering."""


@dataclass(frozen=True)
class EngineAdvisory:
    """One classified finding, carrying every raw stderr line that produced it.

    ``lines`` is a tuple rather than a single string because Radiance reports one problem across
    several lines: a single failed ``!xform`` include writes three, one from ``xform`` and two from
    ``oconv``, all naming the same file. Those are one finding, not three -- and collapsing them is
    only safe if the text they carried survives the collapse, hence a tuple rather than a discard.
    ``line`` remains available as the representative first line.

    ``subject`` is the thing the finding is *about* -- the dropped include's path, say. It is the
    merge key, and it is what lets a reader see which file failed without re-reading the raw text.

    ``rad_mcp`` maps this onto the wire ``Advisory``; the wire model's optional ``field`` key has no
    meaning here, because a stderr line qualifies the whole run rather than one result field.
    """

    code: str
    severity: Severity
    message: str
    lines: tuple[str, ...]
    subject: str | None = None

    @property
    def line(self) -> str:
        """The representative raw line -- the first one Radiance wrote about this subject."""
        return self.lines[0]


@dataclass(frozen=True)
class Rule:
    """One row of the classification table.

    To add a row: append a ``Rule`` to ``RULES`` with the substring(s) that identify the message, an
    observed ``example`` line, a code already in the advisory-code register, its severity, and a
    comment citing where the string and the classification come from. No code changes anywhere else.
    """

    patterns: tuple[str, ...]
    """Lower-cased substrings, **all** of which must appear in the line for the row to match.
    Several narrow substrings are safer than one wide one: they let a row be pinned to the binary
    that emitted it, which is what stops a benign message being matched on a run where the same
    words mean something else."""

    code: str
    severity: Severity

    example: str
    """A line actually observed from Radiance, which this row must classify as itself.

    Required, with no default, so a row cannot be added without the evidence it was written from.
    `tests/test_advisories.py` runs every example back through ``classify_line`` and asserts it
    lands on its own row -- which proves the pattern still matches the string it came from, and
    catches a broad row silently shadowing a narrower one as the table grows. First-match-wins
    ordering is otherwise only a comment, and a comment cannot fail."""

    message: str | None = None
    """One actionable sentence. ``None`` means surface the Radiance line verbatim -- used where the
    register says the message is surfaced as written, and by the unrecognised-line fallback."""

    subject_pattern: str | None = None
    """Regex with one capturing group extracting *the thing that failed* from the line.

    Rows that leave this ``None`` produce one advisory per line. Rows that set it produce one
    advisory per distinct subject, which is what stops a single missing include being reported three
    times because Radiance mentioned it three times."""

    def matches(self, lowered: str) -> bool:
        return all(pattern in lowered for pattern in self.patterns)

    def subject_of(self, line: str) -> str | None:
        """The merge key for ``line``, or ``None`` if this row does not extract one."""
        if self.subject_pattern is None:
            return None
        found = re.search(self.subject_pattern, line)
        if found is None:
            # The row matched but the subject regex did not. Report the line on its own rather than
            # guess: merging on a subject we could not read would fuse unrelated findings, which is
            # a worse failure than repeating one.
            return None
        return _normalise_subject(found.group(1))


def _normalise_subject(raw: str) -> str:
    """Reduce a captured subject to the input it names.

    ``oconv`` quotes the whole failing command -- ``"!xform objects/x.rad"`` -- while ``xform``
    quotes only the file it could not open. Both are the same dropped input, so both must reduce to
    the same key or the merge does nothing.
    """
    # Final fix F, fix round 3 (H4): split, and strip, only where `/bin/sh` splits the command's
    # words -- a space or a tab (its default IFS, less the newline no line holds) -- not on
    # `str.split()`'s Unicode whitespace. A file named with a no-break space is one word to the
    # shell and to xform (measured: `xform: cannot find file "nb<U+00A0>shelf.rad"`), and splitting
    # it here made oconv's lines a second finding, about "shelf.rad".
    subject = raw.strip(_SHELL_WORD_SEPARATORS)
    if subject.startswith("!"):
        # "!xform objects/x.rad" -> "objects/x.rad". xform's own options precede the file, so the
        # last field is the file; a bare "!command" with no argument keeps its whole text.
        fields = [field for field in re.split(f"[{_SHELL_WORD_SEPARATORS}]+", subject) if field]
        subject = fields[-1] if len(fields) > 1 else subject
    return subject


#: Where `/bin/sh` splits a command line into words: its default IFS, less the newline.
_SHELL_WORD_SEPARATORS = " \t"


#: Where an unrecognised line lands. `JOB_STDERR_WARNINGS` is the register's code for "the run
#: exited 0 but wrote warnings; the classified list is attached", allocated `I/W`
#: ([docs/tool-surface.md](../../docs/tool-surface.md) tool 3): `warn` for a line this table cannot
#: vouch for, `info` for one it recognises as benign.
UNCLASSIFIED_CODE = "JOB_STDERR_WARNINGS"

#: How a dropped `oconv` input is named. `xform` quotes the file it could not find; `oconv` quotes
#: or parenthesises the whole `!xform` command. Both reduce to the same path.
_QUOTED = r'"([^"]+)"'
_PARENTHESISED = r"\(([^)]+)\)"

#: The table. Ordered: the first matching row wins, so narrower rows precede broader ones -- an
#: ordering the `example` round-trip test enforces rather than merely documenting.
RULES: tuple[Rule, ...] = (
    # ---- benign -------------------------------------------------------------------------------
    # Verified fact 6 in docs/tool-surface.md, and re-run on this machine 2026-07-31: the Klems
    # V-matrix command from verified fact 5 against the office fixture with a glow aperture writes
    # exactly this line, exits 0, and returns the correct 435 values. A glow-modified aperture is
    # not a light source and rcontrib says so on every well-formed matrix run; reporting it as an
    # error would make the correct setup look broken.
    #
    # The `rcontrib:` prefix is load-bearing and is NOT decoration. Verified on this machine the
    # same day: `rtrace -I -ab 1` against an octree with no sky writes the identical sentence under
    # its own name -- `rtrace: warning - no light sources found` -- and returns 0.0 lux. That is a
    # confidently wrong answer, not a benign one. Dropping the prefix from this pattern would
    # classify it `info` and hide it. If a further binary is ever confirmed benign here, it gets its
    # own row.
    Rule(
        patterns=("rcontrib: warning - no light sources found",),
        code=UNCLASSIFIED_CODE,
        severity="info",
        example="rcontrib: warning - no light sources found",
        message=(
            "rcontrib reported no light sources. Expected when the aperture is a glow surface, "
            "which is not a light source; the coefficients are valid."
        ),
    ),
    # ---- dropped oconv inputs ------------------------------------------------------------------
    # CONTEXT.md and tests/fixtures/README.md §2: with RAYPATH lacking '.', or an !xform target that
    # does not exist, oconv emits warnings, returns status 0, and writes a well-formed but nearly
    # empty octree (77 B vs 236 B on test_room). Code that checks only the return code simulates an
    # empty scene.
    #
    # All three strings below were reproduced on this machine 2026-07-31 against a copy of
    # tests/fixtures/scenes/test_room, both ways round -- RAYPATH=<lib only> on the intact fixture,
    # and a correct RAYPATH with a missing include -- giving `getinfo -d` -> `0 0 0 0` and 77 B in
    # both cases. They arrive as a set: one failing include produces the xform line, then both oconv
    # lines, all three naming the same file. `subject_pattern` collapses them to one advisory per
    # dropped input.
    #
    # `OCTREE_INPUT_DROPPED`, not `OCTREE_EMPTY`: see the module docstring. Still `error`, because a
    # silently dropped input means the scene compiled is not the scene requested, whatever the
    # bounding cube turns out to say.
    Rule(
        patterns=("xform: cannot find file",),
        code="OCTREE_INPUT_DROPPED",
        severity="error",
        example='xform: cannot find file "objects/nowhere.rad"',
        message=(
            "An !xform include could not be found, so its geometry is missing although oconv "
            "exited 0. Check RAYPATH contains '.' and that the path resolves from the working "
            "directory."
        ),
        subject_pattern=_QUOTED,
    ),
    Rule(
        patterns=("warning - bad status from", "!xform"),
        code="OCTREE_INPUT_DROPPED",
        severity="error",
        example='oconv: warning - bad status from "!xform objects/nowhere.rad"',
        message=(
            "An !xform include command failed; oconv continued without its geometry and still "
            "exited 0. The scene compiled is not the scene requested."
        ),
        subject_pattern=_QUOTED,
    ),
    Rule(
        patterns=("warning -", "empty file"),
        code="OCTREE_INPUT_DROPPED",
        severity="error",
        example="oconv: warning - (!xform objects/nowhere.rad): empty file",
        message=(
            "An oconv input contributed no geometry. Either the file is genuinely empty or an "
            "!xform include inside it failed."
        ),
        subject_pattern=_PARENTHESISED,
    ),
    # ---- other strings verified on this machine ------------------------------------------------
    # tests/fixtures/README.md, hdr/: evalglare on high_fish_view.hdr returns dgp 0.211771 -- which
    # reads as "imperceptible glare" -- and emits this notice. Re-run 2026-07-31 against the
    # fixture: the dgp line reproduces the README's recorded values exactly and the notice below is
    # verbatim, with no trailing full stop. The register requires the notice be "surfaced verbatim"
    # (docs/tool-surface.md, tool 13), hence message=None; that entry also records that this
    # advisory existing is the reason the advisory mechanism exists.
    Rule(
        patterns=("low brightness scene",),
        code="GLARE_LOW_LIGHT_SCENE",
        severity="warn",
        example=(
            "Notice: Low brightness scene. Vertical illuminance less than 380 lux! "
            "dgp might underestimate glare sources"
        ),
    ),
)


def classify_line(line: str) -> EngineAdvisory:
    """Classify one stderr line.

    An unrecognised line comes back at ``warn`` with the Radiance text as its message, unchanged.
    """
    lowered = line.lower()
    for rule in RULES:
        if rule.matches(lowered):
            return EngineAdvisory(
                code=rule.code,
                severity=rule.severity,
                message=rule.message if rule.message is not None else line,
                lines=(line,),
                subject=rule.subject_of(line),
            )
    return EngineAdvisory(
        code=UNCLASSIFIED_CODE, severity="warn", message=line, lines=(line,)
    )


def classify(lines: Iterable[str]) -> list[EngineAdvisory]:
    """Classify a ``Result.warnings`` list into one advisory per finding, in order.

    Lines naming the same subject under the same code are one finding: a failed ``!xform`` include
    produces three lines about one file, and three advisories saying so is noise that teaches a
    reader to skim. Merging is keyed on **what failed**, never on the text -- so a scene with five
    broken includes still reports five advisories, one per file, which is what makes the list
    actionable. Every raw line is kept on the advisory that absorbed it; nothing Radiance wrote is
    discarded, and a line whose subject could not be read is never merged.

    Blank lines are skipped -- ``Result.warnings`` already strips them, and an advisory whose
    message is the empty string carries no information.
    """
    ordered: list[EngineAdvisory] = []
    first_seen: dict[tuple[str, str], int] = {}

    for line in lines:
        if not line.strip():
            continue
        advisory = classify_line(line)
        if advisory.subject is None:
            ordered.append(advisory)
            continue

        key = (advisory.code, advisory.subject)
        index = first_seen.get(key)
        if index is None:
            first_seen[key] = len(ordered)
            ordered.append(advisory)
        else:
            # Absorb into the finding already reported for this subject. The first line keeps its
            # position and its message -- it is the one Radiance wrote first, and on a dropped
            # include that is `xform`'s, which names the file most directly.
            first = ordered[index]
            ordered[index] = EngineAdvisory(
                code=first.code,
                severity=first.severity,
                message=first.message,
                lines=(*first.lines, line),
                subject=first.subject,
            )
    return ordered
