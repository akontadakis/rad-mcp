"""Building and validating octrees.

Three traps live here, all verified (see CONTEXT.md and tests/fixtures/README.md):

1. ``oconv`` without ``-f`` writes an octree that stores *relative paths* to its source ``.rad``
   files. Every downstream tool re-reads those files against its own cwd. We always freeze.
2. ``oconv`` exits 0 when every ``!xform`` include failed, producing a well-formed empty octree.
   We validate the bounding cube via ``getinfo -d`` instead of trusting the return code.
3. ``oconv`` *aborts* (nonzero exit, no octree) on two specific, sourced failure modes that read as
   the same generic ``RadianceError`` as any other crash unless caught by name: too many scene files
   on the command line, and a "set overflow" when too many surfaces sit on top of each other or the
   bounding cube is inflated by an oversized object (`OCTREE_SET_OVERFLOW`/`OCTREE_TOO_MANY_FILES`,
   docs/tool-surface.md). Both were reproduced against Radiance 6.1a while building the fixtures in
   ``tests/test_engine_octree.py`` -- 300 one-polygon files (100 does not trip it) and 30 000
   coincident 1 mm quads plus one 1000 m quad, each in well under a second.

4. **``oconv`` executes any ``!`` line in a scene file through ``/bin/sh``** (ruling R33). That is
   the include mechanism this project depends on and arbitrary command execution as the server user,
   and there is no Radiance flag that disables it -- so `build_octree` refuses it in Python,
   immediately before the spawn. `_refuse_inline_commands` lives *here*, in the module that owns the
   only `oconv` spawn in `src/`, rather than in `rad_engine.scene` where R33 first put it. See
   `build_octree`'s own comment for why the boundary moved and what it closes.
"""

from __future__ import annotations

import errno
import hashlib
import os
import re
import time
from collections import deque
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from .env import RadianceEnv
from .run import RadianceError, Result, run, run_to_file
from .safe_io import DIGEST_BLOCK, open_regular_file

__all__ = [
    "BLACK_MODIFIER",
    "INCLUDE_SEARCH_DEADLINE_S",
    "MAX_INCLUDE_SEARCH_PAIRS",
    "SHELL_SAFE_PATH_CHARACTERS",
    "EmptyOctreeError",
    "Octree",
    "OctreeSetOverflowError",
    "OctreeTooManyFilesError",
    "SceneFileOutsideRootError",
    "SceneInlineCommandError",
    "ShellUnsafePathError",
    "black_material",
    "bounding_cube",
    "build_ase_octree",
    "build_black_octree",
    "build_octree",
    "build_suns_octree",
    "dash_safe_operand",
    "octree_body_digest",
    "read_scene_text",
    "refuse_inline_commands_in_files",
    "refuse_inline_commands_in_text",
    "refuse_shell_unsafe_paths",
    "SceneIncludeReach",
    "inside_allowed",
    "scene_include_files",
    "scene_include_reach",
]

#: The modifier `_build_black_backed_octree` rewrites every piped surface to (`xform -m black`), and
#: the identifier `black_material` below declares -- **one constant, because the rewrite and the
#: declaration are two halves of one fact.** `xform -m <name>` produces surfaces modified by a name
#: that must already be defined among the `oconv` scene files, or the compile ends
#: `oconv: fatal - (standard input): undefined modifier "black"` (measured, and the ordering trap
#: `_build_black_backed_octree`'s own docstring states). Written as one name here rather than as a
#: literal in the argv and a second literal in the primitive text, so the two cannot drift.
BLACK_MODIFIER: Final[str] = "black"

#: `oconv`'s own wording for the set-overflow abort, case-insensitive, with the surface id it names
#: captured -- verified on this machine 2026-08-02: ``oconv: internal - set overflow in addobject
#: (tiny_8191)``. The register requires the id on the advisory "because the offending geometry is
#: near it" (docs/tool-surface.md); this is the only place that id is ever printed.
_SET_OVERFLOW = re.compile(r"set overflow in addobject\s*\(([^)]*)\)", re.IGNORECASE)

#: Metadata queries (getinfo and friends) read a header and exit. They must never hang the server,
#: so they get a hard bound. Simulation calls are a different matter -- see the job model in
#: CONTEXT.md; those get an explicit timeout from the caller, not a default.
_METADATA_TIMEOUT_S = 30.0

#: The only commands a `!` line in a scene file may name (ruling R33).
#:
#: Radiance's `!` substitution hands the rest of the line to `/bin/sh` and splices the output in as
#: scene text. That is the include mechanism this project depends on -- `test_room/scene.rad` is six
#: `!xform` lines, and one of them pulls in a `.sky` whose own line is `!gensky` -- and it is also
#: arbitrary command execution as the server user. Measured against Radiance 6.1a while allocating
#: R33: a `.rad` whose first line is `!/bin/sh -c "id > MARKER"` compiles through `oconv -f` with
#: exit 0 and the single stderr line `warning - ...: empty file`, and the marker fills with the
#: output of `id`. The same line through `xform` on stdin -- `read_scene`'s own spawn -- does the
#: same. There is no Radiance flag that disables `!`, so it must be refused here, in Python, before
#: the spawn.
#:
#: These two names are what the fixtures actually use; nothing in `src/` ever writes a `!` line.
_ALLOWED_BANG_COMMANDS = frozenset({"xform", "gensky"})

#: Characters that give `/bin/sh` a second command, a substitution or a redirection.
#:
#: **This, not the command allowlist, is the load-bearing half of the guard**, and it is here because
#: the obvious rule is unsafe. Measured, same session: `!xform ./mat.rad; id > M` begins with
#: `!xform` and still runs `id`, because the *whole line* -- not its first token -- is what reaches
#: the shell. `$(...)`, backticks and `| tee` each executed the same way. So an allowlist checked
#: against the first word alone would refuse `!/bin/sh` and wave through `!xform x; /bin/sh`, which
#: is the same hole with a longer prefix.
#:
#: Refused as a set of *characters* rather than parsed as shell: this module has no business
#: reimplementing `/bin/sh`'s grammar, and every legitimate `!` line in this project's corpus -- and
#: every one in `docs/specs`, which carry `-t`, `-rx`, `-rz` and negative numbers -- is a command
#: name followed by plain tokens of `[A-Za-z0-9._/:-]`. None contains any of these. Refusing the
#: character class is checkable by reading it; recognising "a safe pipeline" is not.
_SHELL_METACHARACTERS = frozenset("|;&<>`$(){}[]*?~!\\'\"\n\r")

#: The whitespace `readobj.c`'s object loop skips, and `fgetword` breaks a word on: C `isspace` in
#: the "C" locale, minus the two characters that end a line. **Not `str.split()`'s idea of
#: whitespace**, which also breaks on `\x1c`-`\x1f`, `\x85` and the Unicode separators -- splitting
#: where Radiance does not would let this guard reason about a "word" Radiance never sees.
_RADIANCE_WORD_SEPARATORS = " \t\v\f"

#: `fgetword`'s buffer, `MAXSTR` (`src/common/object.h:35`, Radiance 6.1a `39b99660`). On reaching it
#: `fgetword` breaks **having already consumed the character that broke it**, so the tail of an
#: over-long token is left in the stream and read by the object loop -- which is where a `!` glued to
#: a 512-character argument becomes a command. Measured: it executed under both `xform` and `oconv`.
_RADIANCE_MAX_WORD = 512

#: `readobj.c`'s own line buffer, used by `fgets` for a `#` comment and by `fgetline` for a command.
#: A comment longer than this leaves its tail in the stream for the object loop, and a `!` positioned
#: at the seam becomes a command -- measured executing under `oconv` at exactly that offset.
_RADIANCE_LINE_BUFFER = 2048


def _radiance_physical_lines(text: str) -> list[str]:
    """``text`` split where `fgets` ends a line: at ``\\n``, and nowhere else.

    **Radiance's two readers disagree about a lone ``\\r``, and using one model for both was a
    working bypass.** A `#` comment is read by `fgets` (`readobj.c:57`), which stops only at ``\\n``;
    a `!` command is read by `fgetline` (`fgetline.c:26`), which also ends at a lone ``\\r``. So the
    2048-byte comment buffer has to be measured over *this* split, while `!` commands are located
    over `_radiance_command_lines`. Measuring the buffer over the ``\\r`` split let a comment of
    2 168 bytes containing one lone ``\\r`` read as two short lines to this guard and as one
    over-long comment to Radiance: `fgets` truncated at byte 2047, leaving the `!` at byte 2048 at an
    object boundary. Measured before this split existed -- the guard allowed it and `oconv -f`
    exited 0 having run the payload.
    """
    return text.split("\n")


def _radiance_command_lines(physical: str) -> list[str]:
    """One physical line split where `fgetline` ends a command: at a lone ``\\r``.

    **Not `str.splitlines()`, and that difference was a working bypass.** `splitlines` also breaks on
    ``\\v``, ``\\f``, ``\\x1c``-``\\x1e``, ``\\x85``, ``\\u2028`` and ``\\u2029``; Radiance treats every
    one of those as an ordinary character inside the line (`fgetline`, `src/common/fgetline.c`, ends
    only at ``\\n``, or at ``\\r`` when the next character is not ``\\n``). So
    ``!xform ok.rad\\v; touch M`` read to this guard as an allowed include followed by a separate,
    `!`-free line, and to Radiance as **one** command line whose whole text reached `/bin/sh`.
    Measured executing for ``\\v``, ``\\f``, ``\\x1c`` and ``\\x85`` before this function existed.

    A ``\\r\\n`` leaves an empty trailing piece here, which carries no words and is skipped.
    """
    return physical.split("\r")


#: One or more of the characters `fgetword` breaks a word on, compiled once.
_RADIANCE_WORD_SPLIT: Final = re.compile(f"[{re.escape(_RADIANCE_WORD_SEPARATORS)}]+")


def _radiance_words(line: str) -> list[str]:
    """``line`` split into the words `fgetword` would read from it.

    `str.split()` is deliberately not used: it breaks on separators Radiance keeps inside a word
    (`_RADIANCE_WORD_SEPARATORS` says which), and a guard that invents a word boundary Radiance does
    not have would reason about text that never reaches an object boundary. Quoted words are not
    reassembled -- `fgetword` honours `"` and `'`, so a quoted string is one word to Radiance and
    several here, which can only over-report a word start and never hide one. Its *length* is
    another matter: a quoted word's pieces can each be short while the word is not, so the word
    rule's quoted half is `_fgetword_word_lengths` (R851, register N-109), not this split.
    """
    return [word for word in _RADIANCE_WORD_SPLIT.split(line) if word]


#: C `isspace` in the "C" locale: what `fgetword` skips before a word and ends an unquoted word on,
#: and what `readobj`'s object loop skips between objects. The two line-ending characters are in it
#: here because this model reads the whole stream, not one line at a time.
_RADIANCE_ISSPACE: Final = " \t\n\v\f\r"

#: Whitespace `fgetword` and the object loop skip, compiled once.
_RADIANCE_SPACE_RUN: Final = re.compile(f"[{re.escape(_RADIANCE_ISSPACE)}]*")

#: One word as `fgetword` reads it (ruling R851, register N-109). Measured against Radiance 6.1a,
#: 2026-10-10: a `"` or `'` opens a quoted section **anywhere** in a word (`a"b c"` is one word);
#: only the same character closes it (`'a"b'` stores `a"b`); a closing quote does **not** end the
#: word (`"m"0` stores `m0`); a quoted section runs over spaces and line ends; and a quote never
#: closed runs to end of file. Outside a quote, `isspace` ends the word.
_FGETWORD_WORD: Final = re.compile(
    f"""(?:[^{re.escape(_RADIANCE_ISSPACE)}"']+|"[^"]*"?|'[^']*'?)+"""
)

#: One unquoted run or one quoted section of a `_FGETWORD_WORD` match.
_FGETWORD_SEGMENT: Final = re.compile(f"""[^{re.escape(_RADIANCE_ISSPACE)}"']+|"[^"]*"?|'[^']*'?""")

#: `isint`'s accepted shape, read laxly: a prefix match, so text `isint` rejects (`5x`) is read as
#: a count and the scan goes on where Radiance would have stopped -- which can only scan more.
_RADIANCE_COUNT: Final = re.compile(f"[{re.escape(_RADIANCE_ISSPACE)}]*([+-]?[0-9]+)", re.ASCII)


def _fgetword_stored(word: str) -> str:
    """What `fgetword` stores for ``word``: the text with each opening and closing quote removed."""
    if '"' not in word and "'" not in word:
        return word
    stored = []
    for segment in _FGETWORD_SEGMENT.findall(word):
        if segment[0] in "\"'":
            closed = len(segment) > 1 and segment[-1] == segment[0]
            stored.append(segment[1:-1] if closed else segment[1:])
        else:
            stored.append(segment)
    return "".join(stored)


def _fgetword_unclosed(word: str) -> bool:
    """Whether ``word`` ends inside a quoted section `fgetword` never saw closed: the text ended
    first (`_FGETWORD_WORD` lets a quote run to the end of the text)."""
    if '"' not in word and "'" not in word:
        return False
    last = _FGETWORD_SEGMENT.findall(word)[-1]
    return last[0] in "\"'" and not (len(last) > 1 and last[-1] == last[0])


def _fgetword_word_lengths(text: str) -> Iterator[tuple[int, int, bool]]:
    """``(stored bytes, offset, unclosed)`` of each word `readobj`'s object loop hands `fgetword`,
    in order (ruling R851, register N-109). ``unclosed`` is `_fgetword_unclosed`: the text ended
    inside the word's quote.

    **Why the stream, not the line.** `fgetword` reads a quoted section to its closing quote, spaces
    and line ends included, so a quoted word's length is not bounded by `_radiance_words`' whitespace
    split, nor by a line. Measured, Radiance 6.1a: 511 bytes between the quotes compiled under
    `oconv -f`; 512 exited 133 (SIGTRAP), and so did `rtrace` and `rcontrib` re-reading that file
    for an unfrozen octree; the same file split into 50-byte lines crashed the same way.

    **Why the object grammar, not every quote.** A `#` comment is read by `fgets` and a `!` line by
    `fgetline`, so a quote there opens nothing: an apostrophe in `# Antonio's room` read as a quote
    would make the rest of the file one word, and refuse a legitimate scene. So this follows
    `readobj.c`'s loop: between objects, skip `isspace`, skip a `#` or `!` line, else read one object
    -- modifier, type and identifier, then one more word for an `alias`, else three counted argument
    lists (`readfargs`: a count, then that many words, three times). Where a count is not an integer
    Radiance stops reading the file (`bad arguments`), and so does this.

    It never reads less than Radiance: a count is read laxly (`_RADIANCE_COUNT`), an unknown type is
    read as `readfargs` would read it, and a word is measured whole. Lengths are the stored bytes
    (`_fgetword_stored`), quote characters not counted -- `"A"` repeated 511 times compiled, 512
    times crashed."""
    end = len(text)
    position = 0

    def word() -> re.Match[str] | None:
        nonlocal position
        position = _RADIANCE_SPACE_RUN.match(text, position).end()
        found = _FGETWORD_WORD.match(text, position)
        if found is not None:
            position = found.end()
        return found

    while True:
        position = _RADIANCE_SPACE_RUN.match(text, position).end()
        if position >= end:
            return
        if text[position] == "#":
            # `fgets` to the end of the line: `\n` only (`_radiance_physical_lines`).
            newline = text.find("\n", position)
            position = end if newline < 0 else newline + 1
            continue
        if text[position] == "!":
            # `fgetline` to `\n` or a lone `\r` (`_radiance_command_lines`).
            stop = position
            while stop < end and text[stop] != "\n" and not (
                text[stop] == "\r" and text[stop + 1 : stop + 2] != "\n"
            ):
                stop += 1
            position = stop + 1
            continue
        header = []
        for _ in range(3):
            found = word()
            if found is None:
                return
            yield (
                _radiance_byte_length(_fgetword_stored(found.group())), found.start(),
                _fgetword_unclosed(found.group()),
            )
            header.append(found)
        if _fgetword_stored(header[1].group()) == "alias":
            found = word()
            if found is None:
                return
            yield (
                _radiance_byte_length(_fgetword_stored(found.group())), found.start(),
                _fgetword_unclosed(found.group()),
            )
            continue
        for _ in range(3):
            found = word()
            if found is None:
                return
            yield (
                _radiance_byte_length(_fgetword_stored(found.group())), found.start(),
                _fgetword_unclosed(found.group()),
            )
            count = _RADIANCE_COUNT.match(_fgetword_stored(found.group()))
            if count is None:
                return
            for _ in range(max(int(count.group(1)), 0)):
                found = word()
                if found is None:
                    return
                yield (
                    _radiance_byte_length(_fgetword_stored(found.group())), found.start(),
                    _fgetword_unclosed(found.group()),
                )


def _reads_a_whole_line(physical: str) -> bool:
    """Whether ``physical`` carries a `#` or a `!`, the only characters that make `readobj` read a
    line into its `_RADIANCE_LINE_BUFFER` (ruling R824, register N-44; narrowing R391's rule).

    `readobj.c`'s object loop reads `#` to end of line with `fgets` into its 2048-byte buffer, and
    `!` to end of line with `fgetline`; anything else is an object, read by `fgetword` one word at
    a time, and no line buffer is involved however long the line is. So an over-long line with
    neither character leaves no line-buffer tail in the stream, and the word rule
    (`_RADIANCE_MAX_WORD`) still bounds each of its unquoted words; a quoted word, which `fgetword`
    reads to its closing quote over spaces and line ends, is measured whole by
    `_fgetword_word_lengths` (R851, register N-109). Measured by the re-reviewer of R447: a 6 906-byte one-line 300-vertex
    polygon that `oconv` compiles correctly was refused by the unconditional rule. Either character
    anywhere on the line keeps the rule, because a `#` or `!` can start an object boundary after a
    primitive ends mid-line; a `#` consumed as a string argument over-refuses, which fails closed.
    Both are ASCII, so the decoded line holds them exactly where the file's bytes do.
    """
    return "#" in physical or "!" in physical


def _radiance_byte_length(text: str) -> int:
    """How many bytes of Radiance's buffers ``text`` fills.

    **Both Radiance limits are byte limits, and measuring them in code points was a working
    bypass.** `readobj.c:34,57` reads a comment into `char buf[2048]` with `fgets`, and `MAXSTR`
    (`object.h:35`) bounds `fgetword` in bytes; `_decode_scene_bytes` maps one *byte* to one code
    point only for ASCII and for invalid bytes. A comment of legitimate multi-byte UTF-8 -- an
    accented or CJK annotation any CAD exporter may write -- is therefore shorter in characters than
    in bytes, and a `!` glued past byte 2047 sits at an object boundary while every character-counted
    rule reads the line as short. Measured against this guard before this function existed: a
    1 152-character, 2 174-byte comment was **allowed**, and `oconv -f` exited 0 having run the
    payload (so did `xform` on stdin).

    `surrogateescape` is the exact inverse of `_decode_scene_bytes`, so for text this guard was
    handed by `read_scene_text` the count is the file's own byte count. The fallback covers a lone
    surrogate that decode never produces (a caller passing a hand-built string): `surrogatepass`
    encodes it as three bytes rather than raising, which over-counts and so can only refuse more.
    """
    try:
        return len(text.encode("utf-8", "surrogateescape"))
    except UnicodeEncodeError:
        return len(text.encode("utf-8", "surrogatepass"))


class EmptyOctreeError(RuntimeError):
    """oconv succeeded but compiled in no geometry.

    Almost always a path problem: RAYPATH missing ``.``, a wrong cwd, or an ``!xform`` target that
    does not exist. The warnings on the ``oconv`` result say which.

    ``code`` and ``severity`` bring this refusal into the same taxonomy every other one in this
    codebase already carries (``GridError``, ``ProjectError``, ``GeometryError``, ``MaterialsError``)
    -- this class predated it. ``OCTREE_EMPTY`` is defined by the register as *"bounding cube size 0
    with exit status 0"*, which is exactly the condition ``build_octree`` checked before raising, so
    no other code could apply here.

    ``warnings`` carries ``Octree.warnings`` -- the raw ``oconv`` stderr lines that were already read
    to prove the cube is empty -- so a caller that catches this exception can classify *why* without
    re-running anything or re-parsing this exception's own message string. Classifying those lines
    into ``OCTREE_INPUT_DROPPED`` findings is deliberately not done here: that is a claim about the
    *run*, proven from stderr, and this class only ever carries the claim about the *artifact*,
    proven by the cube (``rad_engine.scene``, ruling R5, keeps the two apart).
    """

    code = "OCTREE_EMPTY"
    severity = "error"

    def __init__(self, message: str, *, warnings: list[str]) -> None:
        super().__init__(message)
        self.warnings = warnings


class OctreeSetOverflowError(RuntimeError):
    """`oconv` aborted mid-build: *"Set overflow in addobject (id)"*.

    The man page names the cause precisely: "either too many surfaces are lying right on top of
    each other, or the bounding cube is inflated from an oversized object ... If hundreds of
    triangles come together at a common vertex, it may not be possible to create an octree from the
    object." No octree exists at ``out_path`` when this is raised -- `oconv` aborted before writing
    one, unlike the two-trap conditions above where a well-formed (possibly empty) octree always
    exists. ``surface_id`` is the id `oconv` named in its own message, carried structurally (not only
    in prose) because the register requires it: "the offending geometry is near it."
    """

    code = "OCTREE_SET_OVERFLOW"
    severity = "error"

    def __init__(self, message: str, *, surface_id: str | None) -> None:
        super().__init__(message)
        self.surface_id = surface_id


class OctreeTooManyFilesError(RuntimeError):
    """`oconv` aborted mid-build: *"Too many scene files"* -- more files on the command line than
    `oconv`'s own argument-count limit. The fix is structural (combine files, or route the extras
    through an `!xform` hierarchy so `oconv` sees one file instead of many), so the message states
    it rather than leaving the caller to guess at the limit (docs/tool-surface.md's own wording for
    this code).
    """

    code = "OCTREE_TOO_MANY_FILES"
    severity = "error"


class SceneInlineCommandError(RuntimeError):
    """A scene file carries a `!` line that is not one of the includes this project depends on.

    ``code`` is ``SCENE_INLINE_COMMAND`` (ruling R33, allocated `error`). Two existing codes were
    checked and rejected before allocating a new one, the same way ruling R29 allocated
    `SCENE_FILE_NOT_FOUND`:

    - ``ARGUMENT_OUT_OF_RANGE`` does not fit. The argument -- a path -- is valid, inside the root,
      and names a file that exists. What is hostile is the file's *content*, which no argument
      check can see.
    - ``SCENE_FILE_NOT_FOUND`` is the opposite claim: the file was found, read, and refused.

    **Why this is refused in Python rather than by a flag.** Radiance's `!` substitution runs the
    rest of the line through `/bin/sh` and splices its stdout in as scene text. `oconv` and `xform`
    both do it, both exit 0, and both report nothing but `warning - ...: empty file` -- so the
    refusal cannot be a check on the exit status or the stderr of a spawn that has already run the
    command. There is no `oconv`/`xform` option that disables it.

    **Lives in this module, not in `rad_engine.scene` where R33 first put it.** The refusal has to
    sit next to the spawn it protects, and `build_octree` below owns the only `oconv` spawn in
    `src/`; `scene.py` imports *this* module, so the scanner could not be imported the other way
    without a circular import (verified: `ImportError: cannot import name 'Octree' from partially
    initialized module`). `rad_engine.scene` re-exports this name, so every existing caller that
    catches it -- `rad_mcp.geometry_tool`, `rad_mcp.materials_tool` -- is unchanged.

    ``path`` and ``line_number`` locate the offending line; ``line`` is the text, truncated, so a
    refusal is diagnosable without re-reading the file.
    """

    code = "SCENE_INLINE_COMMAND"
    severity = "error"

    def __init__(
        self,
        message: str,
        *,
        path: Path,
        line_number: int,
        line: str,
        include_tree_too_large: bool = False,
    ) -> None:
        super().__init__(message)
        self.path = path
        self.line_number = line_number
        self.line = line
        #: True for the R753 cap refusal: no line was found to be an inline command, the include
        #: tree is too large to verify, and ``line_number`` is 0 and ``line`` empty.
        self.include_tree_too_large = include_tree_too_large


#: The characters a path may hold when it is handed to a Radiance program that builds a `/bin/sh`
#: command line from its file arguments (ruling R754). An allow-list, not a list of shell
#: metacharacters: genBSDF and genklemsamp interpolate their arguments unquoted (genklemsamp's line
#: 102, `system "xform -m void @ARGV | oconv - > ..."`, read); rfluxmtx single-quotes a name only
#: when it holds some characters (space, `;`, `(`, `|`, `&`, `*`, `"`, `!`, `=` and tab were seen
#: quoted) and passes others bare, so `>`, `#` and a leading `~` reach the shell unquoted (measured
#: by the final review: `x>MARK_gt` created `MARK_gt`), and a `'` closes the quote it adds. A
#: deny-list would have to be right for all three and for every shell. Unsourced: chosen as the
#: portable filename set (POSIX's `[A-Za-z0-9._-]`) plus `/` for a path and `+`, none of which any
#: POSIX shell treats specially outside a word's first position. The queued rfluxmtx and
#: genklemsamp steps hand these programs names relative to the step's working directory, so the
#: project root's own name never reaches the shell line (`rad_engine.chain`, final review).
SHELL_SAFE_PATH_CHARACTERS: Final = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._/+-"
)


class ShellUnsafePathError(SceneInlineCommandError):
    """A path bound for a shell-building Radiance program holds a character outside
    `SHELL_SAFE_PATH_CHARACTERS` (ruling R754).

    A subclass of `SceneInlineCommandError`, so it carries `SCENE_INLINE_COMMAND` and every caller
    that already shapes that refusal shapes this one: the hazard is the same, a command Radiance
    would hand to `/bin/sh`, reached through a file's *name* rather than its content. ``field`` is
    the tool argument the path came from; ``character`` is the first character refused.
    """

    def __init__(self, message: str, *, path: Path, field: str, character: str) -> None:
        super().__init__(message, path=path, line_number=0, line="")
        self.field = field
        self.character = character


class SceneFileOutsideRootError(SceneInlineCommandError):
    """A scene file, or a file its `!xform` chain reaches, resolves outside every allowed directory
    (ruling R823): the configured roots and the Radiance library directory, which the tool layer
    passes as `refuse_inline_commands_in_files`' ``allowed_dirs``.

    A subclass of `SceneInlineCommandError`, so every caller that already catches that and reads
    ``exc.code`` shapes this one, with ``PATH_OUTSIDE_ROOT``: the same policy boundary every path
    argument meets (`rad_mcp.paths.contain`), reached through a scene file's content or a symlink
    rather than an argument. For a top-level scene file, ``path`` and the message name it as the
    caller passed it. For an include, they name the file that includes it and ``line_number`` the
    line, and the include's target is never put in the message: written as an absolute path, the
    target *is* the outside location, and through a symlink its resolution would be. The message
    gives the fix (add the directory to RAD_MCP_ROOTS, or copy the file into the project).
    """

    code = "PATH_OUTSIDE_ROOT"

    def __init__(self, message: str, *, path: Path, line_number: int = 0) -> None:
        super().__init__(message, path=path, line_number=line_number, line="")


def _inside_allowed(path: Path, allowed: Sequence[Path] | None) -> bool:
    """Whether ``path`` resolves inside one of ``allowed`` (already resolved), or ``allowed`` is
    ``None`` (ruling R823: `rad_engine`'s own callers hold no roots and check nothing new). A path
    that does not exist is not this check's claim: the spawn cannot read it either, and a missing
    input is `OCTREE_INPUT_DROPPED`'s (ruling R5). A path whose resolution raises (a symlink loop) is
    skipped for the same reason."""
    if allowed is None:
        return True
    try:
        if not path.exists():
            return True
        real = path.resolve()
    except (OSError, RuntimeError):
        return True
    if any(real == directory or directory in real.parents for directory in allowed):
        return True
    # `resolve()` does not canonicalise letter case, so on a case-insensitive filesystem (APFS's
    # default) `/Users/x/ROOT/inc.rad` is a file inside the root `/Users/x/Root` that the string
    # comparison above misses. Asked of the filesystem instead: is any ancestor the same directory
    # as an allowed one (`os.path.samefile`, device and inode)? On a case-sensitive filesystem a
    # differently cased name is a different directory, and this finds nothing new.
    for ancestor in (real, *real.parents):
        for directory in allowed:
            try:
                if os.path.samefile(ancestor, directory):
                    return True
            except OSError:
                continue
    return False


#: Phase 25 C2a final fix E, fix round 5 (N1): the containment the scan applies to an include, for
#: `rad_mcp.results_tool`, which re-checks a recorded include on every read and must agree with it.
inside_allowed = _inside_allowed


#: The fix every outside-root refusal states (ruling R823), in the host's terms.
_OUTSIDE_ROOT_FIX = (
    "Move the file inside a root: add that directory to RAD_MCP_ROOTS, or copy the file into the "
    "project."
)
_ALLOWED_ROOTS_PHRASE = "every allowed root (the roots in RAD_MCP_ROOTS and the Radiance library directory)"


def _refuse_outside(named: Path) -> None:
    """A top-level scene file outside the roots, named as the caller passed it."""
    raise SceneFileOutsideRootError(
        f"scene file {named} resolves outside {_ALLOWED_ROOTS_PHRASE}, so it is not compiled: a "
        f"file outside the roots is outside what this server may read. {_OUTSIDE_ROOT_FIX}",
        path=named,
    )


def _refuse_outside_include(source: tuple[Path, int] | None) -> None:
    """An include outside the roots, named through the file and line that include it -- never by
    its target, which may be written as an absolute path outside the roots (Task 8 fix round 1)."""
    if source is None:  # every target is recorded with its source; defensive only
        where, path, line_number = "a scene file", Path(), 0
    else:
        path, line_number = source
        where = f"{path} line {line_number}"
    raise SceneFileOutsideRootError(
        f"{where} names an include that resolves outside {_ALLOWED_ROOTS_PHRASE}, so it is not "
        f"compiled: a file outside the roots is outside what this server may read. "
        f"{_OUTSIDE_ROOT_FIX}",
        path=path,
        line_number=line_number,
    )


def dash_safe_operand(name: str) -> str:
    """`name` as a file operand no Radiance program can read as an option (ruling R782).

    A name beginning with `-` is returned with `./` in front; every other name is returned as it
    is. `rfluxmtx` hands its scene operands to `oconv -f <files> receiver_mf{mf}.rad`, and `oconv`
    reads options until its first non-option argument, so a first operand `-f.rad` was read as the
    `-f` switch and the file dropped (measured, Radiance 6.1a: a `run_annual` job over the office
    scene saved as `-f.rad` succeeded with an `.ill` 22.84 times the one from `geom.rad`). `oconv`
    has no `--` (`oconv -f -- -x.rad` is "unknown option: '--'"); `./-f.rad` is read as a file.
    An absolute path begins with `/` and is never changed.
    """
    return f"./{name}" if name.startswith("-") else name


def refuse_shell_unsafe_paths(
    paths: Sequence[str | Path], *, field: str, program: str
) -> None:
    """Raise `ShellUnsafePathError` for the first path holding a character outside
    `SHELL_SAFE_PATH_CHARACTERS`; return `None` when every path is inside the set.

    For the paths handed to a Radiance program that builds a `/bin/sh` command line from its file
    arguments, called immediately before that program (or a program that reads the same paths
    first) spawns. Measured 2026-10-02 on Radiance 6.1a: genBSDF runs
    `system "xform -e @ARGV > ..."`, so a contained geometry file named `x;touch MARK;.rad`, whose
    content R33's `!`-line scan passes, ran `touch MARK`; rfluxmtx builds
    `rcontrib ... "!oconv -f <file> ..."`, single-quoting a name only when it holds some characters,
    so a name holding `'` ran its tail and one holding `>` reached the shell bare (the final
    review's measurement); genklemsamp runs `xform -m void @ARGV` unquoted (read). The path's whole
    string form is checked, directories included, because the program receives it whole: a path
    with a space or a quote in it is refused, so callers that can (the queued rfluxmtx and
    genklemsamp steps) pass names relative to the spawn's working directory instead.
    """
    for path in paths:
        text = str(path)
        for character in text:
            if character not in SHELL_SAFE_PATH_CHARACTERS:
                raise ShellUnsafePathError(
                    f"{text!r} holds the character {character!r}, and {program} builds a /bin/sh "
                    "command line from its file arguments, where that character could end the "
                    "argument and run a command as this server's user. A path handed to "
                    f"{program} may hold only the characters [A-Za-z0-9._/+-]. Refused before "
                    f"{program} was handed it. Rename the file; if the character is in a "
                    "directory above it, including the project root itself, move the project to a "
                    "path made only of those characters (ruling R754, docs/plan.md).",
                    path=Path(text),
                    field=field,
                    character=character,
                )


def _decode_scene_bytes(data: bytes) -> str:
    """Decode a scene file's raw bytes into text **that the `!`-line guard can always scan**.

    `errors="surrogateescape"` is chosen for one property and one only: *it cannot raise*. Every
    byte that is not valid UTF-8 is mapped to a lone surrogate, round-trippable and harmless, so
    `_refuse_inline_commands` sees the file's full line structure no matter what encoding -- or
    deliberate garbage -- the bytes are in.

    **This closes a remote code execution hole, reproduced on this machine against Radiance 6.1a.**
    The guard previously read every scene file with `path.read_text(encoding="utf-8")` inside an
    `except (OSError, UnicodeDecodeError): continue`, so a `UnicodeDecodeError` **skipped the file
    entirely, unscanned**. `oconv` does not decode at all -- it reads raw bytes and hands any `!`
    line to `/bin/sh`. A single invalid byte hidden in a comment therefore bought arbitrary command
    execution as the server user::

        # \\xff hidden
        !/bin/sh -c "id > /tmp/MARKER"

    Measured before this change: the guard skipped the file ("'utf-8' codec can't decode byte 0xff
    in position 2"), `oconv` exited 0 with only `warning - ...: empty file`, and MARKER filled with
    the output of `id`. Both the top-level scan in `build_octree` and the `!xform` include recursion
    had it, so a clean, valid-UTF-8 parent that included a non-UTF-8 child was equally exploitable.
    `rad_engine.scene.read_scene` had the same blindness, where it instead surfaced a raw
    `UnicodeDecodeError` to the wire with no advisory code -- a check 0.4 violation of its own.

    Commit 9a93e51 moved this refusal to the `oconv` boundary specifically to "read the same bytes
    oconv will". A strict-UTF-8 decode did not do that; a decode that cannot fail does.

    **Why decode at all rather than match `!` on the raw bytes.** Byte-level matching would be
    equally unskippable, but it would fork the scanner: `_refuse_inline_commands` compares against
    `str` command names and a `str` metacharacter set, and both it and `read_scene`'s `texts` list
    need `str` regardless (that text is fed to `xform` on stdin). Decoding once here keeps **one**
    scanner over **one** type -- ruling R30's "a second copy is a second thing to drift" -- and
    surrogate-escaping is byte-preserving, so nothing in the line structure the scanner reads is
    lost or altered. The ASCII bytes that matter to the guard (`!`, the command name, every
    metacharacter in `_SHELL_METACHARACTERS`) are exactly themselves under this decode, because
    UTF-8 is ASCII-transparent and non-ASCII bytes only ever become surrogates, never ASCII.

    A file that genuinely cannot be *opened* is still skipped by the callers' `except OSError` --
    that remains `OCTREE_INPUT_DROPPED`'s claim (or `SCENE_FILE_NOT_FOUND`'s upstream), proven from
    the stderr of the run that dropped it, and ruling R5 keeps this from becoming a second,
    differently-coded refusal for one event. Only the *decode* case changed.
    """
    return data.decode("utf-8", errors="surrogateescape")


def read_scene_text(path: Path) -> str:
    """Read a scene file for the `!`-line guard, refusing anything that is not a regular file.

    **The one reader for every scene-file read in this project, and it exists because the reads it
    replaces could wedge the whole server** (ruling R48, "in either direction"). Three sites read a
    **manifest-registered** geometry path with `path.read_bytes()` and no re-containment --
    `build_octree`'s pre-spawn scan, `_refuse_inline_commands`' include recursion, and
    `rad_mcp.point_in_time_tool`'s advisory dry-run scan, which reaches into this module by
    attribute precisely so there is one scanner rather than two.

    Containment is not the guard here and adding one would not close this. A path is contained when
    it is *registered*; these reads happen later, against whatever the path names by then. Measured
    on 2026-08-09: hand-editing `manifest.json` so a registered geometry input points at a FIFO --
    a FIFO **inside** the project, which passes every containment check legitimately -- wedged
    `run_annual(dry_run=True)` in `open()` forever. `SIGALRM` in the main thread could not interrupt
    the `anyio` worker parked there and the process needed `kill -9`. The `anyio` limiter is 40, so
    forty such paths stall every tool the server offers, not merely the one that was called.

    So the guard is `open_regular_file`'s `O_NONBLOCK` (the open *returns* instead of hanging) and
    its `S_ISREG` on the **opened descriptor** (no TOCTOU window to swap a FIFO in after a `stat`),
    and neither needs a `roots` argument -- which is why this fix is not blocked on the open
    path-policy decision.

    **The path is resolved before it is opened, and leaving that out was a verified RCE.**
    `O_NOFOLLOW` makes a symlinked scene file raise `ELOOP`, every top-level scan treats an `OSError`
    as "skip -- the spawn will report it", and that reasoning is false for a symlink: `oconv`,
    `rfluxmtx` and a by-filename `xform` open scene files with `fopen`, which **follows** the link,
    reads the real target and runs its `!` lines. So the guard read *less* than the spawn did.
    Measured through the public `build_octree` before this resolve existed: a registered
    `scene.rad -> hostile.rad` symlink produced no refusal, a valid non-empty octree, exit 0, and the
    payload ran. Ruling R48's `O_NOFOLLOW` (which closed a FIFO hang) is what turned "unreadable to
    the guard" into "invisible to the guard"; resolving first restores the invariant R33 depends on --
    the guard reads the same bytes the spawn will -- and is what `_refuse_inline_commands`' include
    recursion has always done.

    `O_NOFOLLOW` still applies, now to the **resolved** target, so a symlink swapped in after the
    resolve is refused rather than followed, and `O_NONBLOCK`/`S_ISREG` still stop a FIFO from
    wedging the worker. `OSError` therefore still means "this file could not be opened", and a
    genuinely unopenable input keeps its old disposition: skipped here, reported by
    `OCTREE_INPUT_DROPPED` from the stderr of the run that dropped it (ruling R5).
    """
    try:
        real = path.resolve()
    except RuntimeError as loop:
        # `Path.resolve()` raises a bare `RuntimeError` on a symlink loop, which is not an `OSError`
        # and would escape every caller's disposition uncoded. Translated here so a loop lands
        # exactly where an unopenable file does.
        raise OSError(errno.ELOOP, "symlink loop", str(path)) from loop
    with open_regular_file(real, "rb") as handle:
        return _decode_scene_bytes(handle.read())


#: The most (directory, target) pairs the `!xform` include search will try before refusing the scene
#: (ruling R753). Real scenes have few directories and few distinct target strings, because
#: instances reuse one target file. This bounds pairs, not time: each pair costs one `resolve()`,
#: and the scene sets that cost (a target through 30 symlinks that each walk 190 components measured
#: about 8 ms per pair, so 20,000 pairs would be about 160 s). No per-pair figure bounds the time;
#: `INCLUDE_SEARCH_DEADLINE_S` does.
MAX_INCLUDE_SEARCH_PAIRS: Final = 20_000

#: The longest, in seconds of `time.monotonic()`, `_close_inline_reach` searches before refusing the
#: scene as too large to verify (ruling R753). Checked before each pair, so a refusal comes at most
#: one pair's `resolve()` and scan past it. Fail-closed: past it the scene is refused, never passed
#: partly scanned. Machine-dependent: a slow or loaded machine can refuse a large legitimate tree.
#: Read at call time, so a test can patch it.
INCLUDE_SEARCH_DEADLINE_S: Final = 10.0


class _InlineReach:
    """What the `!`-line scan has learned about where a `!xform` could read a file from (R753).

    **A global fixpoint, not a per-includer search.** `xform` finds its file argument on RAYPATH,
    whose `.` is the process's own directory, and unless given `-c` changes into that file's
    directory before reading it (`src/gen/xform.c` `openmain`, Radiance 6.1a); with `-c` it does
    not, so the reading process can sit in the directory of an *ancestor*. Which directory a nested
    `!xform b.rad` is read from therefore depends on the whole chain above it, including options
    and names the scan would have to model. Instead `targets` collects every `!xform` target string
    seen in any scanned file, and `directories` every directory a reading process could be in: the
    top-level working directory, each RAYPATH entry, and the named and the resolved parent of every
    file reached. Each (directory, target) pair is queued exactly once, when its second member
    arrives: a new target is queued against every known directory, a new directory against every
    known target. `_close_inline_reach` drains the queue until it is empty. A strict superset of
    what Radiance reads, so aliasing a file under a second name (a symlink) and `-c` cannot make it
    under-scan. The cost: a scene is refused when a hostile file sits where Radiance would not have
    read it.

    **Bounded two ways.** Directories are kept resolved, so a cycle of includes adds none after the
    first lap; and the pairs queued are capped at `MAX_INCLUDE_SEARCH_PAIRS`, past which the scene
    is *refused* -- never partly scanned -- as too large to verify. The cap bounds pairs, not time,
    because the scene sets the cost of a pair; `_close_inline_reach` also refuses past
    `INCLUDE_SEARCH_DEADLINE_S`.
    """

    def __init__(self, cwd: Path, raypath: Sequence[Path]) -> None:
        self.cwd = cwd
        self.targets: list[str] = []
        self.directories: list[Path] = []
        self.queue: deque[tuple[Path, str]] = deque()
        #: The first file (as named) and line that wrote each target, so a refusal of the target
        #: can name where it came from without quoting the target itself (R823).
        self.sources: dict[str, tuple[Path, int]] = {}
        #: Phase 25 C2a final fix E, fix round 5 (N2, N3): each existing candidate whose name
        #: resolves to another path (a symlink, at the file or at a directory on the way), as
        #: `{absolute name: resolved file}`, and the file (as named) whose `!xform` line first
        #: wrote the target each scanned file was reached by, `{resolved file: includer}`. Read by
        #: `scene_include_reach`; the scan itself decides nothing from either.
        self.links: dict[Path, Path] = {}
        self.included_by: dict[Path, Path] = {}
        self.add_directory(cwd)
        for entry in raypath:
            self.add_directory(Path(entry))

    def _check_cap(self) -> None:
        pairs = len(self.directories) * len(self.targets)
        if pairs > MAX_INCLUDE_SEARCH_PAIRS:
            raise SceneInlineCommandError(
                f"the `!xform` include tree under {self.cwd} is too large to verify: "
                f"{len(self.directories)} directories x {len(self.targets)} distinct include targets "
                f"is {pairs} candidate paths, past the {MAX_INCLUDE_SEARCH_PAIRS} the `!`-line guard "
                "will search. Refused rather than partly scanned, because a file left unscanned is a "
                "file Radiance could run a `!` line from (ruling R753, docs/plan.md).",
                path=self.cwd,
                line_number=0,
                line="",
                include_tree_too_large=True,
            )

    def refuse_past_deadline(self, deadline_s: float, elapsed_s: float) -> None:
        raise SceneInlineCommandError(
            f"the `!xform` include tree under {self.cwd} could not be verified within the "
            f"{deadline_s:g} s deadline the `!`-line guard allows (stopped after {elapsed_s:.1f} s, "
            f"{len(self.directories)} directories x {len(self.targets)} distinct include targets "
            f"known, {len(self.queue)} candidate paths still unsearched); it is too large to verify. "
            "Refused rather than partly scanned, because a file left unscanned is a file Radiance "
            "could run a `!` line from (ruling R753, docs/plan.md).",
            path=self.cwd,
            line_number=0,
            line="",
            include_tree_too_large=True,
        )

    def add_directory(self, directory: Path) -> None:
        """Directories are kept resolved, which bounds the search on a cycle of includes: an
        unresolved parent of `sub/../top.rad` is `sub/..`, and a cycle would grow it without end. A
        symlink to a directory is read through by `chdir`, so the resolved directory is the one
        entered."""
        try:
            directory = directory.resolve()
        except (OSError, RuntimeError):
            return
        if directory in self.directories:
            return
        self.directories.append(directory)
        self._check_cap()
        self.queue.extend((directory, target) for target in self.targets)

    def add_target(self, target: str, source: tuple[Path, int] | None = None) -> None:
        if target in self.targets:
            return
        if source is not None:
            self.sources[target] = source
        self.targets.append(target)
        self._check_cap()
        self.queue.extend((directory, target) for directory in self.directories)

    def add_file(self, named: Path, resolved: Path) -> None:
        """A file was reached: a process that reads it, or `xform`-ed from it, may be in its
        directory, as named (a symlinked file can make that differ) and as resolved."""
        self.add_directory(named.parent)
        self.add_directory(resolved.parent)


def _close_inline_reach(
    reach: _InlineReach,
    *,
    cwd: Path,
    seen: set[Path],
    raypath: Sequence[Path],
    echo_dirs: tuple[Path, ...],
    allowed: Sequence[Path] | None = None,
) -> None:
    """Drain `reach`'s queue of (directory, target) pairs, scanning each candidate file that exists
    (ruling R753). Scanning a file can add targets and directories, which queue their own new pairs;
    a pair is never queued twice, so the work is the number of pairs, which `reach` caps. The time
    is bounded separately, because the scene sets the cost of a pair: before each pair the elapsed
    `time.monotonic()` since this call began is checked against `INCLUDE_SEARCH_DEADLINE_S`, and past
    it the scene is refused as too large to verify -- never passed partly scanned.
    ``seen`` stays keyed on the resolved path, because the scan reads content; the directory set is
    what is global, so a file reached a second time under another name is not scanned again but its
    name's directory is added.

    A candidate whose resolution raises is skipped: on a symlink loop `Path.resolve()` raises a bare
    `RuntimeError` on Python 3.11, and the loop makes the file unreadable to Radiance too.

    ``echo_dirs`` (ruling R759) is the top-level scan's: a file scanned here is quoted in a refusal
    only when the candidate it was reached by passes `_may_echo` against them.

    ``allowed`` (ruling R823) is the resolved allowed directories, or ``None`` to check nothing:
    a candidate that exists and resolves outside all of them is refused `SceneFileOutsideRootError`
    before it is read, named through the file and line that wrote its target (`reach.sources`). The search is a superset of what Radiance reads, so this can refuse an
    outside file Radiance would not have read -- R753's stated cost, now for a location as well as
    for content.
    """
    deadline_s = INCLUDE_SEARCH_DEADLINE_S  # read at call time, so a test can patch the module's
    started = time.monotonic()
    while reach.queue:
        elapsed_s = time.monotonic() - started
        if elapsed_s > deadline_s:
            reach.refuse_past_deadline(deadline_s, elapsed_s)
        directory, target = reach.queue.popleft()
        candidate = directory / target
        if not os.path.lexists(candidate):
            continue  # the common case by far; skips the `resolve()` below
        try:
            resolved = candidate.resolve()
        except (OSError, RuntimeError):
            continue
        if not _inside_allowed(candidate, allowed):
            _refuse_outside_include(reach.sources.get(target))
        named = Path(os.path.abspath(candidate))
        if named != resolved:
            reach.links.setdefault(named, resolved)
        if resolved in seen:
            # Scanned already under another name. What the name adds is its directory, and only a
            # symlinked file can make that differ from the resolved file's own: `chdir` follows
            # directory links, so any other spelling enters a directory already known. The test
            # keeps the dense case (n directories, n `../dN/f.rad` targets) from resolving twice
            # per pair.
            if os.path.islink(candidate) and candidate.exists():
                reach.add_file(candidate, resolved)
            continue
        try:
            included_text = read_scene_text(resolved)
        except OSError:
            continue  # OCTREE_INPUT_DROPPED's claim, not this one's -- see the docstring.
        seen.add(resolved)
        source = reach.sources.get(target)
        if source is not None:
            reach.included_by.setdefault(resolved, source[0])
        _refuse_inline_commands(
            included_text, path=resolved, cwd=cwd, seen=seen,
            raypath=raypath, named=candidate, reach=reach, echo_dirs=echo_dirs,
        )


def _resolved_directories(directories: Sequence[Path]) -> tuple[Path, ...]:
    """``directories`` resolved, in order, dropping any whose resolution raises (ruling R759)."""
    found: list[Path] = []
    for directory in directories:
        try:
            resolved = directory.resolve()
        except (OSError, RuntimeError):
            continue
        if resolved not in found:
            found.append(resolved)
    return tuple(found)


def _echo_directories(cwd: Path, scene_files: Sequence[str]) -> tuple[Path, ...]:
    """The directories a top-level scan of ``scene_files`` may quote from (ruling R759): ``cwd`` and
    each scene file's own directory, as the spawn names it (``cwd / scene_file``)."""
    return _resolved_directories((cwd, *((cwd / scene_file).parent for scene_file in scene_files)))


def _may_echo(named: Path, echo_dirs: Sequence[Path]) -> bool:
    """Whether a refusal may quote a line of `named` back to the client (ruling R759).

    Only when `named` is not a symlink and resolves inside one of `echo_dirs`: the top-level `cwd`
    and the directory of each top-level scene file. `rad_engine` has no allowed roots
    (`read_scene_text`), so this is only as tight as the directories its callers pass: the tool layer
    keeps `cwd` and a registered scene file inside a root, and a relative text label is not allowed
    to add the process cwd (`refuse_inline_commands_in_text`). Within that, it may withhold an include
    elsewhere inside the roots.
    `echo_dirs` must already be resolved (`_resolved_directories`).
    """
    if named.is_symlink():
        return False
    try:
        real = named.resolve()
    except (OSError, RuntimeError):
        return False
    return any(real == directory or directory in real.parents for directory in echo_dirs)


def _refuse_inline_commands(
    text: str,
    *,
    path: Path,
    cwd: Path,
    seen: set[Path],
    raypath: Sequence[Path] = (),
    named: Path | None = None,
    reach: _InlineReach | None = None,
    echo_dirs: tuple[Path, ...] | None = None,
) -> None:
    """Refuse any `!` line in ``text`` that is not a bare, metacharacter-free call to one of
    `_ALLOWED_BANG_COMMANDS`, then **recurse into every `!xform` target it names** (ruling R33).

    Two conditions per include, both required, and the second is the one that matters -- see
    `_SHELL_METACHARACTERS` for the measurement that shows why the first alone is not a guard:

    1. the first token after `!` is `xform` or `gensky`, and
    2. the line contains no shell metacharacter at all.

    **Everything else carrying a `!` is refused, because Radiance's boundary is not a line start.**
    `readobj.c:53-65` is a character loop *between objects*: skip `isspace`, then `#` reads to end of
    line, `!` reads the rest of the line and `popen`s it, anything else is one object read by
    `fgetword` and its declared argument counts. An object can end in the middle of a line, so a `!`
    after it is a command; a `#` consumed as a string *argument* does not comment out its line; and
    either buffer limit (`fgetword`'s `MAXSTR`, `readobj`'s line buffer) can leave a token's tail in
    the stream with **anything at all** at the seam. All three executed under real Radiance against
    this guard's previous line-start rule -- the measurements are in
    `tests/test_scene.py::TestTheGuardMatchesRadiancesOwnGrammar`. So outside an allowed include a
    `!` is refused when it starts a word, and **the two length rules do not wait for a `!` at a
    word start**: any word at or past `_RADIANCE_MAX_WORD` is refused whether or not a `!` appears
    anywhere on its line, and any physical line at or past `_RADIANCE_LINE_BUFFER` is refused when
    it carries a `#` or a `!` anywhere (ruling R824 narrowed this from every line: a line with
    neither is read word by word, never into the line buffer -- `_reads_a_whole_line`). A word that
    holds a quote is measured a second time, whole, as `fgetword` reads it over spaces and line
    ends (`_fgetword_word_lengths`, R851, register N-109): its quote characters are not counted.

    **The length rules were once conditional on a `!` being present, and that modelled the wrong
    hazard.** What a truncation leaves in the stream is read by the object loop as scene text, so
    the seam admits the entire Radiance grammar, not just `!`. Measured on this branch: a trailing
    2048-byte comment containing no `!` injected a polygon that grew a 1 m scene's octree cube to
    40 m, and a second one redefined a material's reflectance from 0.5 to 0.02 -- in the octree
    only, invisible to `parse_polygons` and therefore to every check built on it.

    A `!` inside a short word on a short line (`# exported by a CAD tool!`) cannot reach a boundary
    and stays legal: the fixtures' longest line is 221 bytes and longest word 37 (re-measured 2026-09-13 over the 38 committed `.rad`/`.sky`/`.mat`/`.rif` files under `tests/`, and pinned by `tests/test_scene.py::TestTheGuardMatchesRadiancesOwnGrammar::test_the_fixture_corpus_stays_far_from_both_limits`), so neither
    length rule is anywhere near real scene text.

    **The recursion is not a refinement; without it the guard is decorative.** Measured against
    Radiance 6.1a: a top-level file whose only line is a permitted `!xform ./inner.rad` executes an
    `!/bin/sh` line sitting inside `inner.rad`, because `xform` expands the chain itself. Scanning
    only the files named in ``scene_files`` would refuse the naive attack and pass the one-level
    indirection, so every file the chain reaches is read and scanned here, before the spawn that
    would run it.

    ``seen`` makes a cyclic or diamond include chain terminate, and is carried across the whole
    ``scene_files`` sequence by the caller so a file included twice is scanned once.

    ``raypath`` and ``named`` (ruling R753): ``raypath`` is the RAYPATH directories the spawn gets
    (`RadianceEnv.raypath_dirs`), and ``named`` is the path this file was reached by, before
    resolution (``path`` is resolved). Both feed ``reach`` (`_InlineReach`), which collects every
    `!xform` target and every directory a reading process could be in, and which
    `_close_inline_reach` searches as a global fixpoint, so a target is scanned in every directory
    Radiance could read it from. A call without ``reach`` is a top-level call: it makes one and
    closes it before returning; a caller scanning several files passes one shared ``reach`` and
    closes it itself.

    A `!xform` target that cannot be read is **not** refused: a missing include is already
    `OCTREE_INPUT_DROPPED`'s claim, proven from stderr by the run that dropped it, and raising a
    second, differently-coded refusal for it here would report one event under two definitions --
    the mistake ruling R5 exists to prevent. It is skipped, and the run reports it the existing way.

    CRLF is handled by `_radiance_command_lines`, which ends a command exactly where `fgetline` does
    (the comment buffer is measured over `_radiance_physical_lines` instead, because `fgets` ends a
    comment only at `\\n` -- the two readers genuinely disagree about a lone `\\r`) -- so
    `xform\\r` never has to be compared against `xform`, and, more importantly, a separator Python
    calls a line ending and Radiance does not can no longer split one command line into two
    innocent-looking halves. Nothing else is normalised:
    the trailing space on `test_room/scene.rad`'s first line is load-bearing (it is asserted
    verbatim in `tests/test_advisories.py`) and splitting on whitespace preserves it untouched here.

    ``echo_dirs`` (ruling R759) is the resolved directories a refusal may quote a line from
    (`_may_echo`). A top-level caller scanning several files passes `_echo_directories` of them; a
    call without it uses ``cwd`` and ``named``'s own directory.
    """
    named = path if named is None else named  # as Radiance named it; `path` is resolved (R753)
    top_level = reach is None
    if reach is None:
        reach = _InlineReach(cwd, raypath)
    try:
        resolved_path = path.resolve()
    except (OSError, RuntimeError):
        resolved_path = path
    reach.add_file(named, resolved_path)
    # **Never echo the content of a file the caller did not name.** `read_scene_text` follows a
    # symlink (it must -- the spawn does), so the bytes scanned here can come from a file outside
    # every allowed root while the *named* path is inside one. Both refusals below quote the
    # offending line, in the message and in the structured `line` field, and `rad_mcp` returns both
    # to the client -- so on a symlinked scene file that quote is a read oracle over out-of-root
    # content. Reported by the fourth review of this fix and reproduced: a `scene.rad` pointing at a
    # file beginning `db_password !…` put those bytes on the wire. Suppressed rather than contained,
    # because containment needs the roots and this layer deliberately has none (`read_scene_text`);
    # whether the tool layer should also refuse an out-of-root resolution is the owner's call.
    # R759: withheld too when the file lies outside the caller's directories -- an include reached
    # by an absolute path or found on RAYPATH was quoted, because `path` is resolved there and was
    # never a symlink. Every refusal below that quotes text quotes it through `echo`. See `_may_echo`.
    if echo_dirs is None:
        echo_dirs = _resolved_directories((cwd, named.parent))
    borrowed = not _may_echo(named, echo_dirs)

    def echo(text: str) -> str:
        """The offending line as the client may see it -- withheld when it is not the caller's."""
        if borrowed:
            return (
                "<suppressed: the scene file is a symlink or lies outside the caller's directories, "
                "so its content is not echoed>"
            )
        return text[:120]
    # Two readers, two line models, so the scan carries both: `physical` is what `fgets` fills for a
    # `#` comment (it ends only at `\n`), `line` is what `fgetline` hands to /bin/sh for a command
    # (it also ends at a lone `\r`). Measuring the comment buffer over the `\r` split was a working
    # bypass -- see `_radiance_physical_lines`.
    scan = [
        (number, physical, command)
        for number, physical in enumerate(_radiance_physical_lines(text), start=1)
        for command in _radiance_command_lines(physical)
    ]
    for number, physical, line in scan:
        words = _radiance_words(line)
        if not words:
            continue
        stripped = line.lstrip(_RADIANCE_WORD_SEPARATORS)
        if not stripped.startswith("!"):
            # Not an include line, so nothing here may be a command -- and that is three separate
            # claims, because Radiance reaches an object boundary in three ways this guard once
            # missed. See `TestTheGuardMatchesRadiancesOwnGrammar` for the measurement of each.
            if any(word.startswith("!") for word in words):
                detail = (
                    "a word begins with `!`, and Radiance runs a `!` found at any object boundary "
                    "-- not only at the start of a line. A primitive that ends mid-line puts the "
                    "next word at a boundary, and a `#` consumed as a string argument does not "
                    "make the rest of its line a comment"
                )
            elif (
                borrowed
                and _reads_a_whole_line(physical)
                and _radiance_byte_length(physical) >= _RADIANCE_LINE_BUFFER
            ):
                # R759 fix round 1: a withheld line's measured length is derived from it too.
                detail = (
                    f"it is a line at or past Radiance's {_RADIANCE_LINE_BUFFER}-byte buffer, "
                    "whose tail the object loop would read as scene text"
                )
            elif (
                _reads_a_whole_line(physical)
                and _radiance_byte_length(physical) >= _RADIANCE_LINE_BUFFER
            ):
                # R824 (register N-44): only a line carrying `#` or `!` -- see `_reads_a_whole_line`
                # for why a line with neither is read token by token and cannot reach this buffer.
                #
                # **Unconditional on `!`, and that is the whole fix.** This rule used to fire only
                # when the line also carried a `!`, because the seam was modelled as a *command*
                # hazard. It is not: what the truncation leaves in the stream is read by the object
                # loop as **scene text**, and the whole Radiance grammar is available there.
                # Measured on this branch, two scenes differing only in a trailing 2048-byte comment
                # with no `!` in it at all: `clean` compiled to a cube of 1.00002, `inj2` to
                # 40.00002 -- a 1 m scene became a 40 m octree. A second payload,
                # `void plastic m 0 0 5 0.02 0.02 0.02 0 0`, redefined a material the manifest
                # records at 0.5 down to 0.02 in the octree only, because `oconv` takes the last
                # definition silently. `parse_polygons` sees only a comment, so `account`, `_health`,
                # `_checks`, `bounding_box` and `FacesView` all report the injected primitive as
                # absent. Offsets 2046, 2047 and 2049 are refused; only 2048 lands.
                detail = (
                    f"it is a physical line of {_radiance_byte_length(physical)} bytes, at or past "
                    f"Radiance's own {_RADIANCE_LINE_BUFFER}-byte line buffer. A comment longer "
                    "than that buffer leaves its tail in the stream, where the object loop reads it "
                    "as scene text -- so whatever sits at the seam becomes geometry, a material, or "
                    "a command, none of which the parsed scene will show. Measured over the whole "
                    "`\\n`-delimited line on purpose: a lone `\\r` does not end a comment, because "
                    "`fgets` stops only at `\\n`"
                )
            elif borrowed and any(_radiance_byte_length(w) >= _RADIANCE_MAX_WORD for w in words):
                detail = (
                    f"it carries a word at or past the {_RADIANCE_MAX_WORD}-byte limit (`MAXSTR`) "
                    "of `fgetword`, which leaves the rest of the token in the stream"
                )
            elif any(_radiance_byte_length(word) >= _RADIANCE_MAX_WORD for word in words):
                # Unconditional for the same reason: `fgetword` breaks at `MAXSTR` having already
                # consumed the character that broke it, so the tail is at an object boundary whether
                # or not anything on the line is a `!`.
                detail = (
                    f"it carries a word of "
                    f"{max(_radiance_byte_length(w) for w in words)} bytes, at or past `fgetword`'s "
                    f"{_RADIANCE_MAX_WORD}-byte limit (`MAXSTR`). On reaching that limit `fgetword` "
                    "drops the character it broke on and leaves the rest of the token in the "
                    "stream, which puts whatever follows at an object boundary"
                )
            else:
                continue
            raise SceneInlineCommandError(
                f"{path} line {number} could feed text to Radiance past a buffer boundary, where "
                "the object loop reads it as scene input -- including as a command run through "
                f"/bin/sh as this server's user: {echo(line)!r}. Refused because {detail}. Outside "
                "a bare `!xform <path>` or `!gensky <args>` include, a scene file may not carry a "
                "`!` at a word start, nor any over-long word or line (ruling R33, docs/plan.md).",
                path=path,
                line_number=number,
                line=echo(line),
            )
        body = stripped[1:]
        fields = _radiance_words(body)
        command = fields[0] if fields else ""
        offending = sorted(_SHELL_METACHARACTERS & set(body))
        if command not in _ALLOWED_BANG_COMMANDS or offending:
            detail = (
                # `command` is the line's first word, so it is quoted through `echo` too (R759).
                f"it names {echo(command)!r}, and only {sorted(_ALLOWED_BANG_COMMANDS)} are allowed"
                if command not in _ALLOWED_BANG_COMMANDS
                # R759 fix round 1: which metacharacters is derived from a withheld line too.
                else "it contains a shell metacharacter"
                if borrowed
                else f"it contains the shell metacharacter(s) {''.join(offending)!r}"
            )
            raise SceneInlineCommandError(
                f"{path} line {number} is an inline command Radiance would run through /bin/sh as "
                f"this server's user: {echo(line)!r}. Refused because {detail}. A `!` line in a "
                "scene file may only be a bare `!xform <path>` or `!gensky <args>` include with no "
                "shell metacharacters (ruling R33, docs/plan.md).",
                path=path,
                line_number=number,
                line=echo(line),
            )
        if command != "xform":
            continue
        # `xform`'s own options precede the file and the file is the last field -- the same reading
        # `rad_engine.advisories` already applies to an `oconv` warning subject. A bare `!xform`
        # with no argument names no file and reaches this with `fields[1:]` empty.
        for target in fields[1:]:
            if target.startswith("-"):
                continue
            # R753: only the target string is recorded here. `_close_inline_reach` searches it in
            # every directory a reading process could be in, and holds the symlink-loop and
            # unreadable-include handling this loop used to carry (an include that cannot be read is
            # `OCTREE_INPUT_DROPPED`'s claim, not this one's -- see the docstring).
            reach.add_target(target, source=(named, number))
    # R851 (register N-109): a quoted word, measured whole. Without a quote character every
    # `fgetword` word is one of `_radiance_words`' pieces, which the loop above has measured.
    if '"' in text or "'" in text:
        for stored, offset, unclosed in _fgetword_word_lengths(text):
            if unclosed and stored < _RADIANCE_MAX_WORD:  # an over-long one is refused below
                # R851, fix round 1 (register N-109): a file that ends inside an open quote.
                # `fgetword` reads a quote to its closing quote, so where several files are read
                # on one stream -- `read_scene` joins them on `xform`'s stdin -- the next file's
                # text joins this word, and no scan of either file alone measures the result.
                number = text.count("\n", 0, offset) + 1
                line = _radiance_physical_lines(text)[number - 1]
                raise SceneInlineCommandError(
                    f"{path} line {number} starts a quoted word the file never closes: "
                    f"{echo(line)!r}. An apostrophe or an unclosed quote in a name -- an OBJ "
                    "material name that obj2rad passed through, for instance -- is the usual cause. "
                    "Radiance's `fgetword` reads a quote to its closing quote, so it would read the "
                    "rest of this file, and where files are read on one stream the next file too, "
                    "as part of this one word, and misread the scene. Close the quote, or remove "
                    "it from the name (ruling R851, docs/plan.md).",
                    path=path,
                    line_number=number,
                    line=echo(line),
                )
            if stored < _RADIANCE_MAX_WORD:
                continue
            number = text.count("\n", 0, offset) + 1
            line = _radiance_physical_lines(text)[number - 1]
            # R759: a withheld line's measured length is withheld with it.
            measured = "" if borrowed else f" of {stored} bytes between its quotes"
            raise SceneInlineCommandError(
                f"{path} line {number} could feed text to Radiance past a buffer boundary, where "
                "the object loop reads it as scene input -- including as a command run through "
                f"/bin/sh as this server's user: {echo(line)!r}. Refused because it starts a quoted "
                f"word{measured}, at or past `fgetword`'s "
                f"{_RADIANCE_MAX_WORD}-byte limit (`MAXSTR`). `fgetword` reads a quoted word to its "
                "closing quote, spaces and line ends included, so splitting the line on spaces does "
                "not shorten it; measured, `oconv`, `rtrace` and `rcontrib` stop on SIGTRAP at that "
                "length. Outside a bare `!xform <path>` or `!gensky <args>` include, a scene file may "
                "not carry a `!` at a word start, nor any over-long word or line (ruling R33, "
                "docs/plan.md).",
                path=path,
                line_number=number,
                line=echo(line),
            )
    if top_level:
        _close_inline_reach(reach, cwd=cwd, seen=seen, raypath=raypath, echo_dirs=echo_dirs)


@dataclass(frozen=True)
class SceneIncludeReach:
    """What `scene_include_reach` found (Phase 25 C2a final fix E, fix round 5).

    ``files`` is `scene_include_files`' answer. ``links`` maps each name the search reached that
    resolves to another path (a symlinked include, or one under a symlinked directory) to the file
    it resolved to, so a reader can tell a link retargeted since from the file it named then (N2).
    ``included_by`` maps each file in ``files`` to the file, as named, whose `!xform` line first
    wrote the target it was reached by (N3)."""

    files: tuple[Path, ...]
    links: dict[Path, Path]
    included_by: dict[Path, Path]


def scene_include_files(
    scene_files: Sequence[str], *, cwd: Path, raypath: Sequence[Path]
) -> tuple[Path, ...]:
    """The files the `!xform` include search reaches from ``scene_files``, resolved, sorted, and
    without the scene files themselves (R849): what an `oconv` over ``scene_files`` may read besides
    its own arguments. It is the search `refuse_inline_commands_in_files` runs, with a fresh `seen`,
    so it is the same superset of what Radiance reads (`_InlineReach`), and it raises what that scan
    raises. An unreadable scene file is skipped, as that scan skips it."""
    return scene_include_reach(scene_files, cwd=cwd, raypath=raypath).files


def scene_include_reach(
    scene_files: Sequence[str], *, cwd: Path, raypath: Sequence[Path]
) -> SceneIncludeReach:
    """`scene_include_files`' search, with the names that resolved elsewhere and who included
    each file (`SceneIncludeReach`). One search: `scene_include_files` is this one's `files`."""
    seen: set[Path] = set()
    reach = _InlineReach(cwd, raypath)
    echo_dirs = _echo_directories(cwd, scene_files)
    tops: set[Path] = set()
    for scene_file in scene_files:
        path = cwd / scene_file
        try:
            text = read_scene_text(path)
        except OSError:
            continue
        tops.add(path.resolve())
        _refuse_inline_commands(
            text, path=path, cwd=cwd, seen=seen, raypath=raypath, reach=reach, echo_dirs=echo_dirs
        )
    _close_inline_reach(reach, cwd=cwd, seen=seen, raypath=raypath, echo_dirs=echo_dirs)
    files = tuple(sorted(seen - tops))
    return SceneIncludeReach(
        files=files,
        links={named: resolved for named, resolved in reach.links.items() if resolved in files},
        included_by={path: reach.included_by[path] for path in files if path in reach.included_by},
    )


def refuse_inline_commands_in_text(
    text: str, *, label: Path, cwd: Path, raypath: Sequence[Path] = ()
) -> None:
    """The R391 guard over text no scene file holds: a converter stage's output, or the text about to
    be stored in `01_geometry/`. `xform -c` executes nothing but keeps a `!` line's text (design
    §1.2), so its output must be scanned before anything reads it. `label` names the text in the
    refusal; `cwd` resolves an allowed include exactly as a file's would be, and `raypath` adds the
    RAYPATH directories a later spawn would search (R753; empty by default, because this text is
    scanned again by `refuse_inline_commands_in_files` before any spawn compiles it). A fresh `seen`
    per call, for the reason `refuse_inline_commands_in_files` gives. A refusal may quote a line from
    `cwd` and, when `label` is absolute, `label`'s directory (R759); pass an absolute `label`."""
    # R759 fix round 1: a relative `label` resolves against the server's *process* cwd, which is no
    # directory of the caller's, so it would make that directory an echo directory. Only `cwd` then.
    echo_dirs = None if label.is_absolute() else _resolved_directories((cwd,))
    _refuse_inline_commands(
        text, path=label, cwd=cwd, seen=set(), raypath=raypath, echo_dirs=echo_dirs
    )


@dataclass(frozen=True)
class Octree:
    path: Path
    frozen: bool
    bbox: tuple[float, float, float, float]
    """Octree bounding cube as ``(x, y, z, size)``, from ``getinfo -d``."""
    warnings: list[str]
    source_files: list[str]

    @property
    def is_empty(self) -> bool:
        return self.bbox[3] <= 0.0


def bounding_cube(env: RadianceEnv, octree: Path | str, *, cwd: Path | str) -> tuple[float, float, float, float]:
    """Read an octree's own bounding cube via ``getinfo -d``.

    Returns ``(x, y, z, size)``. A size of 0 means no geometry was compiled in.

    ``getbbox`` is NOT usable for this: on a non-frozen octree it tries to re-read the source scene
    files and fails with ``unknown type "<file>"``, which looks like a corrupt octree but is only a
    working-directory problem.
    """
    octree = Path(octree)
    result = run(env, ["getinfo", "-d", str(octree)], cwd=cwd, timeout=_METADATA_TIMEOUT_S)

    # getinfo prefixes each line with the filename: "/path/to.oct: -15.0 -21.0 -19.5 42.0".
    # The four numbers are always last, which also survives a path containing spaces -- but parse
    # them deliberately rather than relying on that, so a "cannot open" line gives a clear error
    # instead of a ValueError from float().
    fields = result.stdout.split()
    try:
        x, y, z, size = (float(v) for v in fields[-4:])
    except (ValueError, TypeError) as exc:
        raise RuntimeError(
            f"could not read bounding cube from {octree}. "
            f"getinfo -d printed {result.stdout.strip()!r}"
            + (f"; stderr: {result.stderr.strip()!r}" if result.stderr.strip() else "")
        ) from exc
    return x, y, z, size


def refuse_inline_commands_in_files(
    scene_files: Sequence[str],
    *,
    cwd: Path,
    raypath: Sequence[Path],
    allowed_dirs: Sequence[Path] | None = None,
) -> None:
    """Ruling R33's `!`-line scan, run against every file in ``scene_files`` immediately before the
    spawn that will read them -- shared by `build_octree` and the two black-backed builders below,
    which each call this once per spawn whose argv names scene files directly (`build_octree`'s one
    `oconv`; `build_black_octree`/`build_suns_octree`'s `xform` spawn *and* their `oconv` spawn).

    Public since R747: `rad_mcp.point_in_time_tool._scan_scene_for_inline_commands` also calls it
    (and `annual_tool`, `electric_tool`, `ugr_tool` and `view_sunlight_tool` call that function).
    That call runs earlier, above the tool's `dry_run` exit and before any spawn, so that `dry_run`
    refuses what the real call refuses; it is advisory, and the builders' calls in this module
    remain the security boundary.

    ``raypath`` (ruling R753) is the RAYPATH directories the spawn will get --
    `RadianceEnv.raypath_dirs(cwd)` -- and is required, so every caller states it. `()` is for tests
    that spawn nothing; the advisory scan in `rad_mcp.point_in_time_tool` passes
    `locate().raypath_dirs(cwd)`.

    ``allowed_dirs`` (ruling R823, register N-41) is where a scene file may resolve: the advisory
    scan passes the configured roots and the Radiance library directory. Each top-level scene file,
    and each include candidate the search finds, that exists and resolves (symlinks followed) outside
    every one of them is refused `SceneFileOutsideRootError` (`PATH_OUTSIDE_ROOT`) before its
    content is read. Both sides are resolved, so a root that is itself a symlink, or macOS's `/var`
    and `/tmp` spelled either way, compares as the same place. ``None`` -- every caller in
    `rad_engine` -- checks nothing new, because `rad_engine` holds no roots; the tool layer's scan
    runs first on every tool that calls it, above `dry_run`.

    Extracted from `build_octree` (Task 8) rather than copied: R30 forbids a second copy of R33's
    scan, and the two new builders need it run twice per build -- once against the files handed to
    `xform`, once against the files handed to `oconv` -- which a single inline block in `build_octree`
    could not express without `build_octree` itself growing an unrelated second call site.

    **Why this scan exists at all, and why it runs here, immediately before the spawn.** `oconv` runs
    every `!` line through `/bin/sh`, so the refusal has to happen right before the spawn that would
    execute it -- and that is what every call site in this module is. R33 originally scanned in
    `rad_engine.scene.read_scene`, reached only from `geometry(action="add")` and `materials(...)`,
    i.e. at **registration** time. Phase 3's `run_point_in_time` re-reads registered geometry from the
    manifest and comes straight to a spawn, never through that scan: a scene clean when it was added
    and hostile by the time it was compiled would have executed. **Scanning here closes that TOCTOU
    window, because the check now runs against the same bytes the next spawn will read.** A
    registration-time scan can only ever prove something about the file as it was at registration;
    between then and any later build the file can be replaced. Reading the files here, immediately
    before the spawn, leaves **no useful** interval in which the bytes can change without the guard
    seeing it -- and it covers every caller by construction, rather than by each one remembering to
    scan first. **Not *no* interval, and the fourth review of the inline-command fix was right to
    say so**: the guard opens and reads the file, then `oconv` opens it again *by name* a moment
    later, so a swap between the two opens is inherent to reading the file twice and is not
    eliminated here. Closing it completely would mean handing `oconv` the exact bytes this guard
    verified rather than a filename. What this placement does remove is the interval that mattered --
    registration-to-build, the whole life of a project -- rather than open-to-open.
    Costs one read per scene file (plus its include chain) per call. Accepted deliberately: these are
    the same files the next spawn is about to read and parse, the read is small next to the spawn, and
    the alternative is trusting a check that ran at a different time against possibly different bytes
    -- which is the defect this closes.

    **A fresh `seen` set is created on every call, not accepted as a parameter and threaded by the
    caller across calls.** Code review round 2 (Task 8) found a Critical here: an earlier version of
    this function took `seen` as a parameter, and `_build_black_backed_octree` threaded *one* `seen`
    across its two calls (once for `black_scene_files` before the `xform` spawn, once for
    `scene_files` before the `oconv` spawn). `_refuse_inline_commands` skips any `!xform` target
    already in `seen` -- so a file reachable, via `!xform`, from **both** lists was scanned once,
    before `xform`, and silently *not* re-read before `oconv`. That is exactly the interval this
    scan's own rationale (two paragraphs up) says must not exist: `oconv` would then read bytes last
    verified across an entire subprocess boundary, unseen by the guard between the two spawns.
    Reproduced directly: a `shared.rad` reachable from both lists passed the second scan when `seen`
    was threaded across the two calls, and was refused by both scans once each call got its own fresh
    `seen`. Owning `seen` internally, rather than accepting it as a parameter, makes the bug
    structurally impossible to reintroduce -- there is no longer a `seen` value a caller *could*
    thread across calls. Within one call, `seen` still dedupes a cyclic or diamond include chain
    reached twice from the *same* scene-file list, which is safe: that memoization never crosses a
    spawn boundary, only recursion within the one spawn's own pre-read.
    """
    seen: set[Path] = set()
    reach = _InlineReach(cwd, raypath)
    echo_dirs = _echo_directories(cwd, scene_files)  # R759
    allowed = None if allowed_dirs is None else _resolved_directories(
        [Path(directory).expanduser() for directory in allowed_dirs]
    )
    for scene_file in scene_files:
        path = cwd / scene_file
        if not _inside_allowed(path, allowed):
            _refuse_outside(Path(scene_file))
        try:
            text = read_scene_text(path)
        except OSError:
            # Unreadable here means the spawn that reads it will report it -- a missing input is
            # `OCTREE_INPUT_DROPPED`'s claim (or `SCENE_FILE_NOT_FOUND`'s, upstream), proven from the
            # stderr of the run that dropped it. Raising a second, differently-coded refusal would
            # report one event under two definitions, the mistake ruling R5 exists to prevent. This
            # matches `_refuse_inline_commands`' own disposition for an unreadable include, and it
            # keeps this scan from changing which error any existing caller sees.
            #
            # **`UnicodeDecodeError` is deliberately NOT in this tuple**, and removing it closed a
            # verified RCE: a file that cannot be *decoded* is still a file the spawn will happily
            # read as bytes and execute the `!` lines of, so skipping it unscanned handed the
            # attacker the whole guard. `_decode_scene_bytes` cannot raise it any more -- see there.
            # Only a file that cannot be *opened* is skipped, because that one the spawn cannot read
            # either.
            continue
        _refuse_inline_commands(
            text, path=path, cwd=cwd, seen=seen, raypath=raypath, reach=reach, echo_dirs=echo_dirs
        )
    _close_inline_reach(
        reach, cwd=cwd, seen=seen, raypath=raypath, echo_dirs=echo_dirs, allowed=allowed
    )


def _raise_for_oconv_failure(
    result: Result, *, out_path: Path, scene_files: list[str]
) -> None:
    """`oconv`'s stderr triage (module docstring's trap 3), shared by every builder that spawns
    `oconv` with ``check=False``: `build_octree` and the two black-backed builders below. Returns
    ``None`` when ``result.returncode == 0``; every non-zero exit raises, sourced-and-coded when the
    stderr names one of `oconv`'s two documented abort modes, generic otherwise.

    ``scene_files`` is used only for the `OctreeTooManyFilesError` message's file count -- for the
    black-backed builders that count is the files handed to `oconv` directly (the piped, blackened
    geometry does not add to `oconv`'s own argument list, so it cannot be the cause of *that*
    particular abort).
    """
    if result.returncode == 0:
        return
    stderr = result.stderr
    overflow = _SET_OVERFLOW.search(stderr)
    if overflow is not None:
        surface_id = overflow.group(1) or None
        raise OctreeSetOverflowError(
            f"oconv aborted building {out_path.name}: too many surfaces lie on top of each "
            f"other, or the bounding cube is inflated by an oversized object, near surface "
            f"{surface_id!r}. oconv said: {stderr.strip()}",
            surface_id=surface_id,
        )
    if "too many scene files" in stderr.lower():
        raise OctreeTooManyFilesError(
            f"oconv aborted building {out_path.name}: too many scene files "
            f"({len(scene_files)} given). Combine files, or route them through an !xform "
            f"hierarchy so oconv sees fewer arguments. oconv said: {stderr.strip()}"
        )
    # Neither sourced failure mode: preserve exactly what `run_to_file(check=True)` would have
    # raised, since this function's contract for every *other* oconv failure is unchanged.
    raise RadianceError(result.argv, result.returncode, stderr)


def _finalize_octree(
    env: RadianceEnv,
    *,
    out_path: Path,
    cwd: Path,
    freeze: bool,
    warnings: list[str],
    source_files: list[str],
    allow_empty: bool,
) -> Octree:
    """The empty-octree check (module docstring's trap 2), shared by every builder below: read the
    bounding cube back with `getinfo -d` -- never trust `oconv`'s exit code alone -- and raise
    `EmptyOctreeError` unless the caller opted out with ``allow_empty``.

    Called only after `_raise_for_oconv_failure` has already returned normally, so ``out_path`` is
    known to hold a well-formed octree (possibly empty) rather than nothing at all.
    """
    bbox = bounding_cube(env, out_path, cwd=cwd)
    octree = Octree(
        path=out_path.resolve(),
        frozen=freeze,
        bbox=bbox,
        warnings=warnings,
        source_files=source_files,
    )

    if octree.is_empty and not allow_empty:
        detail = "; ".join(octree.warnings) or "oconv reported nothing"
        raise EmptyOctreeError(
            f"{out_path.name} contains no geometry (bounding cube size 0) although oconv exited 0. "
            f"Check RAYPATH contains '.', and that scene paths resolve from {Path(cwd)}. "
            f"oconv said: {detail}",
            warnings=octree.warnings,
        )
    return octree


def build_octree(
    env: RadianceEnv,
    scene_files: list[str],
    *,
    out_path: Path | str,
    cwd: Path | str,
    freeze: bool = True,
    allow_empty: bool = False,
    maxres: int | None = None,
    timeout: float | None = None,
) -> Octree:
    """Compile ``scene_files`` into an octree, then prove it is not empty.

    ``freeze`` (``oconv -f``) embeds the geometry so the octree is portable across working
    directories. Leave it on unless you have a specific reason -- the size cost is real but far
    smaller than the cost of an octree that silently reads the wrong file later.

    ``scene_files`` are resolved relative to ``cwd``, matching how Radiance itself resolves them.

    ``maxres`` is passed through as `oconv -r maxres` when given; ``None`` leaves `oconv` at its own
    default (16384). This module does not decide what value to pass -- it has no polygon soup to
    compute a ratio from, and computing one is `rad_core.geometry.octree_resolution_ratio`'s job over
    a soup only `rad_engine.scene.read_scene` has parsed (ruling R27). Passing the flag mechanically
    is all a module that only ever sees ``scene_files`` can correctly own.

    Runs `oconv` with ``check=False`` (unlike every other call in this codebase) because two of its
    failure modes are sourced and coded (`OctreeSetOverflowError`, `OctreeTooManyFilesError`, module
    docstring's trap 3) and must be told apart from an arbitrary crash by inspecting stderr *before*
    deciding whether to raise the generic `RadianceError`.

    Raises ``SceneInlineCommandError`` when any file in the include chain carries a hostile `!` line
    (ruling R33, module docstring's trap 4). Nothing is spawned when it does.
    """
    out_path = Path(out_path)
    cwd = Path(cwd)

    # Ruling R33's `!`-line scan, immediately before the spawn that would execute a hostile line --
    # see `refuse_inline_commands_in_files`'s own docstring for the full rationale (why here, why immediately
    # before the spawn, and the TOCTOU finding that governs how `seen` is scoped).
    refuse_inline_commands_in_files(scene_files, cwd=cwd, raypath=env.raypath_dirs(cwd))

    argv = ["oconv"]
    if freeze:
        argv.append("-f")
    if maxres is not None:
        argv.extend(["-r", str(maxres)])
    argv.extend(scene_files)

    result: Result = run_to_file(
        env, argv, out_path=out_path, cwd=cwd, timeout=timeout, check=False
    )
    _raise_for_oconv_failure(result, out_path=out_path, scene_files=scene_files)

    return _finalize_octree(
        env, out_path=out_path, cwd=cwd, freeze=freeze, warnings=result.warnings,
        source_files=list(scene_files), allow_empty=allow_empty,
    )


def black_material() -> str:
    """`.rad` text: the one `void plastic black` primitive both black-backed octrees need on their
    `oconv` side -- `docs/specs/five-phase-method.md` 1.1's own *"Add a black material to your main
    materials file"* (`void plastic black / 0 / 0 / 5 0 0 0 0 0`).

    **It lives here, beside `BLACK_MODIFIER` and the `xform -m` argv that references it**, rather
    than in `rad_engine.klems` with the other `.rad` text this repo generates, because it is not a
    surface: it is the declaration of the exact identifier `_build_black_backed_octree` rewrites
    every piped primitive to. Split across two modules, the pair could drift into a compile that ends
    `oconv: fatal - (standard input): undefined modifier "black"` -- measured -- and the failure would
    be reported against whichever caller happened to combine them.

    **Why a function and not a module constant.** Both public builders take file *paths*; nothing in
    this module writes a file (ruling R48's containment primitive lives in `rad_mcp`, which this layer
    may not import), so a caller writes this text at a path it has contained itself. A function keeps
    that read as a call at the write site rather than as a constant a caller might be tempted to
    inline into its own string.

    Five reals `0 0 0 0 0` -- R G B specularity roughness -- is *black* in the sense the all-black
    model needs: an opaque surface that reflects nothing, so a specular path cannot contribute at
    `-ab 1` (five-phase-method.md 3.2's own reason for the octree existing). A `void` modifier, never
    `BLACK_MODIFIER` itself: the material is declared under `void` and *used* as the modifier of the
    blackened surfaces.
    """
    return f"void plastic {BLACK_MODIFIER}\n0\n0\n5 0 0 0 0 0\n"


def _build_black_backed_octree(
    env: RadianceEnv,
    black_scene_files: Sequence[str],
    scene_files: Sequence[str],
    *,
    out_path: Path | str,
    cwd: Path | str,
    allow_empty: bool,
    maxres: int | None,
    timeout: float | None,
) -> Octree:
    """Mechanics shared by `build_black_octree` (five-phase-method.md 3.1) and `build_suns_octree`
    (4.2): pipe ``black_scene_files`` through ``xform -m black`` and feed that output to `oconv` as
    its stdin scene (`-`), with ``scene_files`` added directly so they keep their own materials.

    A private helper rather than a second copy of either public function's body (R30) -- the two
    spec sections describe the *identical* two-spawn shape (`xform -m black ... | oconv materials -
    other...`); they differ only in which files the caller passes into which list and why (opaque
    room shell vs. opaque shell + suns + BSDF proxy), which is exactly what the two public wrappers'
    own docstrings, not their bodies, exist to say.

    **No ``freeze`` parameter, unlike `build_octree`.** `man oconv`, verified against Radiance 6.1a on
    this machine: *"A hyphen by itself ('-') tells oconv to read scene data from its standard input.
    This also implies the -f option."* Every octree this helper builds has `-` in its argv by
    construction -- there is no way to call `xform -m black ...` and not pipe the result somewhere --
    so the octree is unconditionally frozen regardless of whether `-f` is also passed explicitly. `-f`
    is still written into the argv below (the task's own "``oconv -f`` always" rule, and it documents
    intent even where it is redundant), but a `freeze` parameter offering a caller the choice to turn
    it off would be a lie: passing `freeze=False` would silently build a frozen octree anyway, the
    exact silently-wrong-by-a-flag shape this codebase's own error taxonomy exists to prevent
    elsewhere. Measured directly: dropping the `-f` token from this function's `oconv` argv while
    leaving `-` in place produces a byte-identical, still-frozen, still-cross-cwd-readable octree --
    the mutation `build_octree`'s own frozen/unfrozen contrast depends on has no observable effect
    here, because `-` alone already forces it. `Octree.frozen` is therefore set to `True`
    unconditionally by this helper, reporting what `oconv` actually did rather than echoing an
    argument that cannot change it.

    **Two spawns, not a shell pipe** -- the same discipline `rad_engine.suns.build_suns_argv` and
    `rad_engine.matrices.build_genklemsamp_argv`/`build_d_argv` hold, and for the identical reason:
    a shell pipe is a third process nothing here declares, and it hides `xform`'s own stderr behind
    `oconv`'s single exit status. `xform`'s output is captured through `rad_engine.run.run` (text,
    stdout/stderr kept apart) and threaded into `oconv` as `run_to_file`'s own ``stdin`` argument --
    never through a shell.

    **``scene_files`` is placed entirely before the ``-`` token in `oconv`'s argv, never after.**
    Measured against Radiance 6.1a on this machine: `oconv` resolves modifiers in the single streamed
    order its scene-file arguments are given, not by a full-file symbol table built up front --
    ``oconv -f - materials.rad`` (piped content first, its ``black`` modifier reference read before
    `materials.rad`'s own `void plastic black` definition) fails ``oconv: fatal - (standard input):
    undefined modifier "black"``, while ``oconv -f materials.rad - `` (materials first) succeeds.
    Every caller of this helper is expected to place whatever `scene_files` entry defines `black` (or
    any other modifier the piped, blackened geometry references) before any entry that does not need
    to precede it -- the same ordering discipline `build_octree`'s own ``scene_files`` argument already
    carries for cross-file modifier references, unstated until this measurement forced it into a
    docstring.

    **Ruling R33's scan runs twice, once per spawn, in spawn order, each with its own fresh `seen`.**
    Against ``black_scene_files`` immediately before the `xform` spawn, then against ``scene_files``
    immediately before the `oconv` spawn -- because each spawn is a place `!` lines execute (module
    docstring's trap 4: "`oconv` and `xform` both do it"), and the guard's whole value is reading the
    same bytes the *next* spawn will read, immediately before it runs. `refuse_inline_commands_in_files` creates its
    own `seen` internally on every call rather than accepting one as a parameter -- code review round
    2 (Task 8) found that an earlier version threaded one `seen` across these two calls, and a file
    reachable, via `!xform`, from both lists was scanned once (before `xform`) and silently skipped
    the second time (before `oconv`), reopening the exact TOCTOU window this scan exists to close. See
    `refuse_inline_commands_in_files`'s own docstring for the reproduction. Two independent scans, not one shared
    across a spawn boundary, is now structural: there is no `seen` value left for a caller to thread.

    `xform`'s own failure (a missing ``black_scene_files`` entry, say) is not one of `oconv`'s two
    sourced/coded abort modes -- those are `oconv`-specific (module docstring's trap 3) -- so it is
    left to raise the ordinary `RadianceError` `rad_engine.run.run`'s own ``check=True`` already
    raises. `oconv`'s failure modes still go through `_raise_for_oconv_failure`, unchanged.

    Both spawns' stderr warnings are carried onto the returned `Octree.warnings` (`xform`'s first,
    then `oconv`'s) so a caller classifying *why* an octree came back empty sees whichever spawn
    reported it -- `build_octree`'s own contract for `Octree.warnings` extended to a build with two
    spawns instead of one, not narrowed to only the last one.

    ``timeout``, unlike every other parameter here, is **not** split between the two spawns -- it is
    passed to `xform`'s `run` call and to `oconv`'s `run_to_file` call independently, each getting the
    full bound. A caller's worst-case wall time for one of these builders is therefore up to `2 *
    timeout`, not `timeout`, unlike `build_octree`'s single-spawn bound. Left undivided deliberately:
    `xform` and `oconv` do genuinely different work (rewriting modifiers vs. compiling and validating
    the octree) and a scene large enough to need a generous bound on one is not guaranteed to need the
    same generous bound on the other, so halving a caller-supplied bound could starve whichever spawn
    turns out to be the slower one for a given scene. A caller that wants a hard ceiling on the whole
    build's wall time should halve ``timeout`` itself before calling; this function does not guess at
    that split on the caller's behalf.
    """
    out_path = Path(out_path)
    cwd = Path(cwd)
    black_scene_files = list(black_scene_files)
    scene_files = list(scene_files)

    # Two independent scans, each with `refuse_inline_commands_in_files`' own fresh `seen` -- not one `seen`
    # threaded across both. See that function's own docstring for the Critical this closes: a shared
    # `seen` let a file reachable from both lists be scanned once, before `xform`, and skipped before
    # `oconv`, reopening the TOCTOU window the scan exists to close.
    refuse_inline_commands_in_files(black_scene_files, cwd=cwd, raypath=env.raypath_dirs(cwd))

    xform_argv = ["xform", "-m", BLACK_MODIFIER, *black_scene_files]
    # `stdin_bytes=b""`, not the text branch (ruling R450): `xform` echoes the caller's scene, comments
    # included, and a Latin-1 `# Exported by Café` raised `UnicodeDecodeError` under the text
    # branch's strict decode. The bytes branch decodes with `surrogateescape`, and `run_to_file`
    # re-encodes with its exact inverse, so `oconv` reads the bytes `xform` wrote.
    xform_result: Result = run(env, xform_argv, cwd=cwd, stdin_bytes=b"", timeout=timeout, check=True)

    refuse_inline_commands_in_files(scene_files, cwd=cwd, raypath=env.raypath_dirs(cwd))

    argv = ["oconv", "-f"]
    if maxres is not None:
        argv.extend(["-r", str(maxres)])
    argv.extend(scene_files)
    argv.append("-")

    result: Result = run_to_file(
        env, argv, out_path=out_path, cwd=cwd, stdin=xform_result.stdout,
        timeout=timeout, check=False,
    )
    _raise_for_oconv_failure(result, out_path=out_path, scene_files=scene_files)

    return _finalize_octree(
        env, out_path=out_path, cwd=cwd, freeze=True,
        warnings=[*xform_result.warnings, *result.warnings],
        source_files=[*black_scene_files, *scene_files],
        allow_empty=allow_empty,
    )


def build_black_octree(
    env: RadianceEnv,
    black_scene_files: Sequence[str],
    scene_files: Sequence[str] = (),
    *,
    out_path: Path | str,
    cwd: Path | str,
    allow_empty: bool = False,
    maxres: int | None = None,
    timeout: float | None = None,
) -> Octree:
    """The all-black octree the 5-phase method's direct terms (`Vd`, `Dd`) share
    (`docs/specs/five-phase-method.md` sec 3.1): every opaque surface rewritten to the `black`
    modifier so a specular surface cannot contribute at `-ab 1`, while the window/receiver surfaces
    keep their own materials.

    ``black_scene_files`` are piped through ``xform -m black`` (never through a bare `oconv`, which
    has no flag that rewrites a modifier) and fed to `oconv` as its ``-`` stdin scene.
    ``scene_files`` -- typically a materials file that defines ``black`` itself, plus the
    view-matrix/daylight-matrix receiver surfaces sec 3.1 names (`objects/viewmtxsurf.rad`,
    `objects/daymtxsurf.rad`, `skies/sky_white.rad`) -- are added directly, unblackened, and must be
    ordered so anything the piped geometry's `black` reference needs is defined before it (see
    `_build_black_backed_octree`'s own docstring for the measured `oconv` ordering trap this states).

    No ``freeze`` parameter: this octree's ``-`` stdin scene makes `oconv` freeze it unconditionally
    (`man oconv`; see `_build_black_backed_octree`'s own docstring for the measurement), so offering
    one would let a caller pass `freeze=False` and silently get a frozen octree anyway.

    Reuses `_build_black_backed_octree`, the same private mechanics `build_suns_octree` below shares
    -- R30 forbids a second copy of R33's scan, `oconv`'s stderr triage, or the empty-octree check,
    and this function differs from `build_suns_octree` only in *which* files a caller passes and why,
    which its own callers (not this function) decide.
    """
    return _build_black_backed_octree(
        env, black_scene_files, scene_files, out_path=out_path, cwd=cwd,
        allow_empty=allow_empty, maxres=maxres, timeout=timeout,
    )


def build_suns_octree(
    env: RadianceEnv,
    black_scene_files: Sequence[str],
    scene_files: Sequence[str] = (),
    *,
    out_path: Path | str,
    cwd: Path | str,
    allow_empty: bool = False,
    maxres: int | None = None,
    timeout: float | None = None,
) -> Octree:
    """The suns octree `Cds` reads (`docs/specs/five-phase-method.md` sec 4.2): the same all-black
    opaque shell `build_black_octree` builds, plus the *actual* fenestration geometry (glazing, blind)
    and the BSDF proxy surface referencing the tensor-tree BSDF -- kept unblackened, because `Cds`
    traces through them -- and the suns scene (`rad_engine.suns.build_suns_argv`'s output, prefixed
    with `SOLAR_LIGHT_PRIMITIVE`) that gives the sun a named, individually-traceable source per
    Reinhart patch.

    ``black_scene_files`` are the opaque shell surfaces, piped through ``xform -m black`` exactly as
    `build_black_octree` pipes its own. ``scene_files`` carries everything sec 4.2 adds unblackened:
    a materials file defining ``black``, the glazing/blind/BSDF-proxy geometry, and the written suns
    scene file -- again ordered so `black`'s definition precedes the piped geometry that references
    it (see `_build_black_backed_octree`'s docstring).

    **What this server's own caller passes, which is not what sec 4.2 passes** (Phase 7a Task 15b;
    the deviation is argued in `rad_mcp.annual_tool._dispatch` and published in
    `docs/tool-surface.md` §11): ``black_scene_files`` is the registered scene **minus the window
    group's own aperture polygons** (`rad_engine.scene.scene_without_modifiers`), and ``scene_files``
    carries no glazing or blind geometry at all -- only the `black` definition, the `thick 0` BSDF
    proxy and the suns scene. The reason belongs to whoever next composes a caller for this function:
    **the aperture's polygons must reach neither list.** In the blackened list they become a perfect
    occluder, and any opaque surface outboard of a `thick 0` proxy kills the shadow ray from every
    sensor to every sun -- `Cds` comes back identically zero, `oconv` exits 0, and nothing anywhere
    reports it. In the unblackened list they double-count the transmission a tensor tree that *is*
    the whole window already carries.

    No ``freeze`` parameter, for the same reason `build_black_octree` has none -- see its docstring
    and `_build_black_backed_octree`'s own.

    Reuses `_build_black_backed_octree`, the same private mechanics `build_black_octree` shares --
    see that function's own docstring for why this is not a second copy of R33's scan, `oconv`'s
    stderr triage, or the empty-octree check.
    """
    return _build_black_backed_octree(
        env, black_scene_files, scene_files, out_path=out_path, cwd=cwd,
        allow_empty=allow_empty, maxres=maxres, timeout=timeout,
    )


def build_ase_octree(
    env: RadianceEnv,
    scene_files: Sequence[str],
    suns_scene: str,
    *,
    out_path: Path | str,
    cwd: Path | str,
    allow_empty: bool = False,
    maxres: int | None = None,
    timeout: float | None = None,
) -> Octree:
    """The octree LM-83's annual sunlight exposure is traced on (`sec 3.3`): **the registered scene,
    whole, plus the suns scene.** ``oconv -f <scene_files...> <suns_scene>``.

    ``scene_files`` is the project's own scene as registered -- glazing, opaque shell and fixed
    shading all in it, all keeping their own materials. ``suns_scene`` is the written suns file
    (`rad_engine.suns.build_suns_argv`'s output, prefixed with `SOLAR_LIGHT_PRIMITIVE`) that gives
    the sun a named, individually-traceable source per Reinhart patch, so
    `rad_engine.matrices.build_ase_direct_argv`'s `rcontrib -I -ab 0` can bin the direct beam by
    sun. It is passed as its own argument rather than as one more entry in ``scene_files`` because
    it is not scene: nothing registers it, it is regenerated per subdivision, and a caller that
    could fold it into the scene list could also forget it -- and an ASE octree with no suns in it
    compiles cleanly, reports a full bounding cube, and returns identically zero.

    **What this is NOT, and why each difference is required rather than chosen.** It is not
    `build_suns_octree`'s output, which is a different octree for a different term. Read that
    function's docstring beside this one:

    - **Not the blackened shell.** `build_suns_octree` pipes the opaque surfaces through
      ``xform -m black`` so a specular path cannot contribute at `-ab 1`, which is `Cds`'s bounce
      count. ASE is traced at **`-ab 0`** -- LM-83 sec 3.3 is the direct beam from the solar orb
      alone, sky and interreflections excluded -- so there is no bounce for a black shell to
      suppress. Blackening here would not make the answer wrong so much as make it a different
      model of the same room for no reason, and it would take the fixed shading's own reflectance
      with it.
    - **Not aperture-stripped.** `build_suns_octree`'s caller removes the window group's aperture
      polygons from both of its lists, because an opaque surface outboard of a `thick 0` BSDF proxy
      kills the shadow ray from every sensor to every sun. There is no proxy here, so there is
      nothing to occlude -- and the aperture must stay, because LM-83 measures the direct beam
      **after glazing transmittance** (ch04:58-60, sec 3.3). Strip it and every sensor sees the
      unattenuated beam: the ASE hours rise, over a hard 1000 lux threshold, on a room that does not
      exist.
    - **No BSDF proxy.** `Cds` needs one because the five-phase direct term is traced through a
      tensor tree that *is* the whole window. ASE crosses the aperture's own `glass` primitive
      instead, at zero bounces, which is what "after glazing transmittance" means when there is no
      matrix in the path. Adding a proxy would double-count the transmission the glazing already
      applies.
    - **The fixed shading stays.** sec 3.2.6 leaves "any fixed or static shading devices, such as
      overhangs or light shelves" in the ASE model. Operable shading is the thing ASE is measured
      *without* (sec 3.3, pre-blind); fixed shading is part of the building. The distinction is the
      caller's to make when it composes ``scene_files``, and it is stated here because the two are
      one word apart in the standard and a whole facade apart in the result.

    Delegates to `build_octree` rather than reimplementing the spawn: ruling R30 forbids a second
    copy of R33's `!`-line scan, `oconv`'s stderr triage or the empty-octree check, and this builder
    needs all three unchanged. ``freeze=True`` is not offered as a parameter -- CONTEXT.md's first
    engine trap is unconditional here, and unlike `_build_black_backed_octree`'s `-` stdin scene
    (which implies `-f` whatever the argv says) this argv is a plain file list, so dropping `-f`
    really would write an octree that stores relative paths to `scene_files` and re-reads them
    against whatever cwd `rtrace` happens to run in.

    ``allow_empty`` is left available and defaulted off for `build_octree`'s own reasons. It should
    stay off for a real ASE build: an empty octree returns all-zero direct illuminance, which is
    indistinguishable at the tally from the *correct* all-zero a deep room can produce when its
    aperture cannot admit a high summer sun -- so the emptiness has to be refused here, where it is
    still nameable.
    """
    return build_octree(
        env,
        [*scene_files, suns_scene],
        out_path=out_path,
        cwd=cwd,
        freeze=True,
        allow_empty=allow_empty,
        maxres=maxres,
        timeout=timeout,
    )


#: Where a Radiance header ends: its first empty line (`getinfo`'s own convention).
_HEADER_END: Final = b"\n\n"


def octree_body_digest(path: Path | str) -> str:
    """`sha256:` over an octree's **body**, the bytes after its Radiance header (ruling R822).

    `oconv` writes its own command line into the header, so two builds of one scene whose input
    files have different names -- `run_annual`'s glow files are named per call -- differ there and
    nowhere else. Measured 2026-10-05 on the office fixture: `oconv -f` over two identical glow files
    named `.annual-grid-main-abc12345-glow.rad` and `.annual-grid-main-zz9_q8r7-glow.rad` gave
    different whole files and byte-identical bodies, and the body carried no file name (`-f`
    embeds the scene, not its paths; `rad_engine.export.frozen_octree_digest` records the same
    property across directories). So this digest moves when the scene does, not when a file name
    does.

    Streamed in `DIGEST_BLOCK` blocks with only one byte carried across a block boundary, so a
    large octree costs one block of memory. A file with no header end is digested whole. Opened
    with `open_regular_file` (R48), so a FIFO at `path` is refused rather than blocking.
    """
    whole = hashlib.sha256()
    with open_regular_file(Path(path), "rb") as handle:
        carry = b""
        while block := handle.read(DIGEST_BLOCK):
            whole.update(block)
            data = carry + block
            index = data.find(_HEADER_END)
            if index != -1:
                body = hashlib.sha256(data[index + len(_HEADER_END):])
                while rest := handle.read(DIGEST_BLOCK):
                    body.update(rest)
                return "sha256:" + body.hexdigest()
            carry = data[-1:]
    return "sha256:" + whole.hexdigest()

