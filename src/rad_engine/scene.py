"""Assembling and importing a scene: the `.rad` parser, and combining the octree with the pure
geometry checks `rad_core.geometry` computes over what it parses.

Three traps this module exists to keep apart, all from CONTEXT.md, `octree.py`'s own docstring, and
this branch's code review:

1. **The `!xform` include chain must be flattened by the real `xform` binary, not re-derived --
   and `xform` must resolve it exactly the way `oconv` does.** `rad_core.geometry` may parse nothing
   (module docstring: no file formats), and a Python re-implementation of `xform`'s own RAYPATH
   search and per-file directory change would be exactly the class of bug CONTEXT.md's traps already
   are. But `xform <file>` and `oconv <file>` do **not** resolve a nested `!xform ./inner.rad`
   identically: unless `-c` is given, `xform` changes into *the named file's own directory* before
   parsing it (its man page, verbatim), while `oconv` never changes directory at all and resolves
   every `!command` relative to its own cwd. Measured: a scene in `sub/` that includes `./inner.rad`,
   with a decoy `inner.rad` also sitting at the parent cwd, compiles the parent's decoy through
   `oconv` and used to read back the `sub/` file's real content through `xform <file>` -- two
   different scenes reported as one. `read_scene` instead feeds the scene text to `xform` on
   **stdin**: with no file argument there is nothing for `xform` to change directory *to*, so it
   resolves every nested include relative to the process cwd, exactly like `oconv`. Verified the same
   way: both probes now agree with the octree.

2. **A dropped input is a dropped input whichever binary noticed it.** `read_scene`'s own `xform`
   run can drop a nested include exactly as `oconv`'s can (a *different* process, same failure mode),
   and discarding its stderr -- as an earlier version of this module did -- is the exact trap this
   module's other half exists to catch, one binary over. None of `rad_engine.advisories.classify`'s
   `OCTREE_INPUT_DROPPED` rules are pinned to `oconv`'s own prefix (verified: `xform: cannot find
   file`, `warning - bad status from` + `!xform`, and `warning -` + `empty file` all match
   identically whether `xform` or `oconv` is the process that printed them), so no new code is
   needed -- `assemble_scene` below classifies `read_scene`'s warnings together with `oconv`'s own,
   in one `classify()` call so the same dropped include reported by both merges into one finding
   rather than two.

3. **`OCTREE_EMPTY` and `OCTREE_INPUT_DROPPED` are two different claims and must not collapse into
   one.** Ruling R5 (docs/plan.md): `OCTREE_EMPTY` is provable only by `bounding_cube()` in
   `octree.py` -- a claim about the *artifact*. `OCTREE_INPUT_DROPPED` is provable only by
   classifying stderr -- a claim about the *run*. An eight-file scene that lost one include compiles
   a cube of the right size and the wrong scene: `OCTREE_INPUT_DROPPED` is true of that run and
   `OCTREE_EMPTY` is not. `assemble_scene` classifies stderr independently of whether the cube came
   back empty, and `SceneAssemblyError` carries whichever of the two applies -- one, the other, or
   (`test_room` copied without `objects/`, check 2.2) both at once.

**Ruling R27 (2026-08-01): the `-r` half of `OCTREE_RESOLUTION_LOW` is this module's, not Task 7's.**
`docs/tool-surface.md`'s own register row says `rad_core.geometry` computes the ratio and "`rad_engine`
raises `-r` to meet it"; `octree_resolution_ratio`'s docstring says the same from the other side.
`-r` is an `oconv` flag, so only `build_octree` -- in this task's scope -- can pass it, and the flag
must be decided *before* `oconv` runs, which means from the polygons `read_scene` already parses.
`assemble_scene` always raises `-r` to at least the computed ratio when it exceeds
`OCONV_DEFAULT_MAXRES`, so the failure the register describes ("the octree under-resolves fine
geometry with no error") cannot happen by construction here, **and** emits `OCTREE_RESOLUTION_LOW`
(warn) whenever it did so -- not instead of raising, alongside it, because a scene needing a raised
resolution is itself diagnostic (an odd aspect ratio, a units mistake) even once the engine has
protected the octree from it. `SceneAssembly.resolution_ratio` carries the computed ratio (`None` when
it could not be computed -- an empty or fully-degenerate soup) so a caller never has to re-derive it.

**R27's own reordering had a cost, now paid: `read_scene` runs first, so it is the thing that now
reports a missing top-level scene file, not `oconv`.** Before R27 moved the polygon parse ahead of the
compile, a missing entry in `scene_files` reached `oconv` first and came back as a typed
`RadianceError`. Now `read_scene` reads the file itself before `oconv` ever runs, and a stdlib
`FileNotFoundError` is not a member of this codebase's error vocabulary -- so `read_scene` raises
`SceneFileNotFoundError` instead, carrying `SCENE_FILE_NOT_FOUND` (ruling R29; see that class's own
docstring for the three existing codes checked and rejected before this one was allocated).
Restoring the old ordering was considered and rejected: it would undo the one thing R27 requires,
that the polygons exist before `-r` is decided.

**This module makes no reuse or rebuild-skip decision.** `assemble_scene` always builds: it has no
`LoadedProject` to ask, and Ruling R26 already closed the one place a stale reuse decision could leak
back in (`rad_engine.project.is_reusable`, which re-runs `check_edges` over an artifact's stored
`built_from`). Whether to call this module at all for a given `geometry(action="add")` -- versus
reusing an already-registered octree artifact -- is Task 7's decision, made with the project state
this module deliberately does not have.

**Scope, deliberately narrower than every pure check `rad_core.geometry` offers.** This module is
scoped to checks 2.1-2.3 plus ruling R27: the octree/`!xform` trap, the modifier inventory, and the
`-r` half of resolution. It also wires `enclosure_warning` (`NOT_ENCLOSED`), because check 2.1's own
fixture (`test_room`, "not an enclosed room despite the name") already exercises it for free, with the
polygon soup this module parses anyway. It deliberately does **not** call `coincident_surfaces` or
`scale_warning`: neither is in the plan's Task 5 text or ruled onto it, and none of checks 2.1-2.3's
fixtures give a positive case for either -- a call this module cannot exercise red-capably is untested
surface area, not a feature (docs/plan.md's own rule: "a test that cannot fail is a defect").
`SceneAssembly.polygons` and `.bbox` are exposed precisely so a caller that needs those checks --
Task 7, once it has a fixture that trips them -- can run them directly over the same soup without
re-flattening or re-parsing.
"""

from __future__ import annotations

import math
import re
from collections.abc import Collection, Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from rad_core.geometry import (
    OCONV_DEFAULT_MAXRES,
    BoundingBox,
    GeometryError,
    GeometryInventory,
    Polygon,
    bounding_box,
    enclosure_warning,
    inventory,
    octree_resolution_ratio,
)
from rad_core.manifest import ArtifactWarning

from .advisories import EngineAdvisory, classify
from .env import RadianceEnv
from .octree import (
    Octree,
    SceneInlineCommandError,
    _close_inline_reach,
    _echo_directories,
    _InlineReach,
    _refuse_inline_commands,
    build_octree,
    read_scene_text,
)
from .run import Result, run

__all__ = [
    "ASE_BLACK_MODIFIER",
    "ASE_BLACK_PRIMITIVE",
    "defined_modifier_names",
    "AseScene",
    "FlattenedScene",
    "LIGHT_REDIRECTING_TYPES",
    "ReadScene",
    "SceneAssembly",
    "SceneAssemblyError",
    "SceneFileNotFoundError",
    "SceneInlineCommandError",
    "StrippedScene",
    "TRANSMITTING_PRIMITIVE_TYPES",
    "assemble_scene",
    "build_ase_net_scene",
    "build_ase_scene",
    "flatten_scene",
    "glass_modifiers",
    "light_redirecting_modifiers",
    "light_redirecting_modifiers_in",
    "non_polygon_surfaces",
    "parse_polygons",
    "probe_unfollowed_materials",
    "read_scene",
    "scene_without_modifiers",
    "strip_primitives_by_modifier",
    "two_phase_probe_glazing",
    "unclaimed_glazing_modifiers",
    "unclaimed_transmitting_modifiers",
]

# `_ALLOWED_BANG_COMMANDS` and `_SHELL_METACHARACTERS` moved to `rad_engine.octree` with the scanner
# that reads them (ruling R33; see the import above and `build_octree`'s comment for why).


#: A Radiance `alias` primitive has no string/int/float argument-count triple at all -- its full
#: syntax is `modifier alias identifier reference`, the reference being one bare token naming the
#: object being aliased. Every other primitive type in this project's fixtures (and every one this
#: tokeniser has been run against) carries the triple; `alias` is the one documented exception
#: (verified against Radiance 6.1a while fixing this: `oconv -f` accepts `void alias short_mat` /
#: `\treal_mat` immediately followed by geometry using `short_mat`, exit 0, correct cube -- and
#: `xform` passes it through unchanged, which is why a scene containing one reaches this tokeniser
#: at all).
_NO_ARGUMENT_TRIPLE = frozenset({"alias"})


#: One token of Radiance scene text, **with its position**, in the three forms this tokeniser has to
#: tell apart: a `#` comment to end of line, a quoted string (a `BSDF` primitive's XML path may
#: contain spaces and is quoted when it does), and a bare token. Ordered alternation, so a `#` always
#: opens a comment and a `"` always opens a string; a bare token stops at either.
#:
#: **Positions are the whole reason this exists.** `parse_polygons` only ever needed the token
#: *values* and split on whitespace to get them; `strip_primitives_by_modifier` has to give back the
#: primitives it kept as the **original bytes**, which means knowing where each one starts and ends.
#: Re-emitting a primitive from its parsed tokens would drop the quoting a path with a space needs
#: and rewrite every float through `repr`, so the filter slices the input text instead.
_TOKEN_RE = re.compile(r'#[^\n]*|"[^"]*"|[^\s#]+')


@dataclass(frozen=True)
class _Primitive:
    """One Radiance primitive as `_walk_primitives` read it, and the span of text it occupies.

    `start` is the offset of the modifier token; `end` is one past the last token of the primitive's
    own arguments. `text[start:end]` is therefore the primitive verbatim, comments and blank lines
    between primitives excluded.
    """

    modifier: str
    ptype: str
    identifier: str
    floats: tuple[float, ...]
    start: int
    end: int
    reference: str = ""
    """An `alias`'s one bare reference token (the identifier it aliases); empty for every other type."""
    strings: tuple[str, ...] = ()
    """The primitive's string arguments (a `mirror`'s substitute material, a `BSDF`'s file, ...)."""


def _walk_primitives(text: str) -> Iterator[_Primitive]:
    """Every primitive in ``text``, read generically as ``modifier type id`` followed by its own
    declared string/int/float argument counts -- so a material definition (``plastic``, ``glass``,
    ``glow``, ...) or a sky primitive (``brightfunc``, ``source``) is walked past by its own declared
    counts rather than assumed absent. ``alias`` is the one primitive type with no such triple (see
    `_NO_ARGUMENT_TRIPLE`) and is handled separately.

    Deliberately the same technique as ``tests/test_geometry.py``'s own ``_parse_rad_polygons``,
    which Task 2's code review independently re-derived and checked byte-for-byte against a hand
    transcription of the office fixture -- promoted here to production code rather than reinvented
    differently, since ``rad_engine`` needed exactly this (module docstring: parsing `.rad` text is
    this layer's job, not ``rad_core``'s).

    **One walker, two consumers** (R30): `parse_polygons` keeps the ``polygon`` primitives and
    `strip_primitives_by_modifier` drops the ones a caller names. A second copy of the argument-count
    arithmetic is a second thing to drift, and the two consumers must agree on where a primitive ends
    or the filter would slice one in half.

    Raises ``ValueError`` on truncated or malformed input -- a primitive whose declared argument
    counts run past the end of the token stream, or a non-numeric count -- rather than letting a
    bare ``IndexError``/``ValueError`` surface from deep inside the loop with no context. This should
    never happen on real `xform` output; it exists as a guard, not a case any fixture exercises.
    """
    tokens = [
        (match.group(), match.start(), match.end())
        for match in _TOKEN_RE.finditer(text)
        if not match.group().startswith("#")
    ]
    i, n = 0, len(tokens)
    try:
        while i < n:
            start = tokens[i][1]
            modifier, ptype, ident = tokens[i][0], tokens[i + 1][0], tokens[i + 2][0]
            i += 3
            if ptype in _NO_ARGUMENT_TRIPLE:
                i += 1  # the one bare reference token; alias never contributes a polygon
                yield _Primitive(modifier, ptype, ident, (), start, tokens[i - 1][2],
                                 reference=tokens[i - 1][0])
                continue
            n_str = int(tokens[i][0])
            strings = tuple(tokens[i + 1 + k][0] for k in range(n_str))
            i += 1 + n_str
            n_int = int(tokens[i][0])
            i += 1 + n_int
            n_flt = int(tokens[i][0])
            i += 1
            floats = tuple(float(tokens[i + k][0]) for k in range(n_flt))
            i += n_flt
            yield _Primitive(modifier, ptype, ident, floats, start, tokens[i - 1][2], strings=strings)
    except (IndexError, ValueError) as exc:
        raise ValueError(
            f"malformed Radiance primitive text near token {i} of {n}: {exc}. This should never "
            "happen on real xform output -- the scene text may be truncated or corrupted."
        ) from exc


#: The occlusion probe's material rules (ruling R848). Measured 2026-10-05 on Radiance 6.1a, a pane
#: between a point and a unit glow sky, `rtrace -ab 0 -av 0 0 0` (does the probe ray follow it) and
#: `rtrace -I -ab 0 -dj 0` under a `light` sun (does a direct shadow ray): `glass` 0.90 / 5.50,
#: `dielectric` 1.74 / 0 (a single surface hit from its back side, below; a two-surface slab passes
#: the shadow ray at 0.061, measured by the Task 12 re-review), `trans` all-specular (tspec 1)
#: 0.72 / 4.79, `trans` all-diffuse (tspec 0) 0 / 0, `BRTDfunc` constant specular 0.80 / 5.33,
#: `aBSDF` (venetian Klems) 0.61 / 4.03, `BSDF` (the same file) 0.65 / 0, `transfunc` and
#: `transdata` 0 / 0, `plastic` 0 / 0. The view ray follows only the specular, constant part of a
#: transmitter, so a type whose transmission is data, a function or diffuse gives the probe no
#: verdict.
#:
#: **`dielectric` is not followed** (ruling R857, amending R848). Radiance decides inside/outside
#: from the side a ray hits: a ray meeting a lone, single-surface dielectric against its normal is
#: taken to enter the medium and, with no second surface to leave by, is absorbed over the 1e10 m
#: path to the probe sky (a correct two-surface slab is left by its second surface). Measured
#: 2026-10-11 (Phase 24), the window of `tests/test_jobs.py`'s `_PLACEHOLDER_ROOM` as a lone
#: dielectric, every mf 1 dome direction from two points: normal toward the grid, 0 of 290 rays
#: reach; the same polygon reversed, 27 of 290; a two-surface slab with its normals into the glass,
#: 0. With such a pane the probe answered "no sun" and the `asemx` was all zero while the same pane
#: drawn as `glass` admits sun: a sunlit room sealed at ASE 0 %. Orientation and slab pairing cannot
#: be read cheaply from the text, so a scene that uses `dielectric` gives no verdict.
_PROBE_OPAQUE_TYPES: Final = frozenset({
    "plastic", "metal", "plastic2", "metal2", "plasfunc", "metfunc", "plasdata", "metdata",
    "mirror", "ashik2",
})
_PROBE_FOLLOWED_TYPES: Final = frozenset({"glass"})
#: A ray that meets one of these returns its radiance, i.e. "reaches": the safe direction.
_PROBE_EMITTING_TYPES: Final = frozenset({"glow", "light", "spotlight"})
_PROBE_SURFACE_TYPES: Final = frozenset({
    "polygon", "sphere", "bubble", "cone", "cup", "cylinder", "tube", "ring", "source",
})
_PROBE_PATTERN_TYPES: Final = frozenset({
    "colorfunc", "brightfunc", "colordata", "brightdata", "colorpict", "colortext", "brighttext",
    "texfunc", "texdata", "specfunc", "specdata", "specpict",
})


def probe_unfollowed_materials(text: str) -> tuple[str, ...]:
    """The materials (and opaque references) in ``text`` whose transmission the occlusion probe's
    direct ray does not follow, as `"<type> '<identifier>'"` -- empty when the probe's answer can be
    trusted (ruling R848).

    Only materials some surface in ``text`` uses count (an `alias` passes its use on to what it
    aliases), so a definition left behind by the aperture strip does not count. A material is
    followed when it is opaque (`plastic`, `metal`, ...), emitting (`glow`, `light`, `spotlight`:
    the probe reads them as reaching), `glass`, or a `trans` with no diffuse transmission
    (`trans * (1 - tspec) == 0`). Every other type -- `dielectric` (ruling R857: a lone
    single surface facing the grid, or an inverted slab, absorbs every probe ray; see
    `_PROBE_OPAQUE_TYPES`'s comment), `BSDF`, `aBSDF`,
    `transfunc`, `transdata`, `BRTDfunc`, `trans2`, mixtures, `prism1`/`prism2`, `mist`, `illum`,
    `interface`, `antimatter`, and any type this list does not name -- is not. An `instance` or
    `mesh` surface is listed too: its materials are not in the text to check.

    **Every definition of a used identifier is checked** (ruling R848, fix round 2): `oconv` binds
    each surface to the definition preceding it, so a name defined twice can put an unfollowed
    material on some surfaces under a followed one's name. And a `mirror`'s substitute material
    (its one string argument, which non-mirror rays see) is followed like an `alias`.
    """
    primitives = list(_walk_primitives(text))
    by_id: dict[str, list[_Primitive]] = {}
    for p in primitives:
        if p.ptype not in _PROBE_SURFACE_TYPES:
            by_id.setdefault(p.identifier, []).append(p)
    found: list[str] = []
    used: set[str] = set()
    for primitive in primitives:
        if primitive.ptype in ("instance", "mesh"):
            found.append(f"{primitive.ptype} '{primitive.identifier}'")
        elif primitive.ptype in _PROBE_SURFACE_TYPES:
            used.add(primitive.modifier)
    pending, seen = list(used), set()
    while pending:
        name = pending.pop()
        if name in seen or name == "void":
            continue
        seen.add(name)
        for material in by_id.get(name, ()):
            ptype = material.ptype
            if ptype == "alias":
                pending.append(material.reference)
            elif ptype == "mirror" and material.strings:
                pending.append(material.strings[0])
            elif ptype in _PROBE_PATTERN_TYPES:
                continue
            elif (
                ptype in _PROBE_OPAQUE_TYPES or ptype in _PROBE_FOLLOWED_TYPES
                or ptype in _PROBE_EMITTING_TYPES
            ):
                continue
            elif ptype == "trans" and len(material.floats) == 7:
                if material.floats[5] * (1.0 - material.floats[6]) > 0.0:
                    found.append(f"trans '{name}' (diffuse transmission)")
            else:
                found.append(f"{ptype} '{name}'")
    return tuple(found)


def defined_modifier_names(text: str) -> frozenset[str]:
    """The names ``text`` itself defines as a modifier -- every primitive's identifier that is not a
    surface's (a material, a pattern, an alias) -- read by `_walk_primitives` on the text as given,
    with no ``!xform`` include followed (final fix E7: which file a polygon's material definition
    lives in decides where a repair to it goes, since Radiance reads the last definition before a
    surface, and `scene_files_with_materials` puts the registered materials ahead of the geometry).
    Raises ``ValueError`` on malformed input, as every other consumer of the walker does."""
    # Fix round 1 (review I4): a `!` command line (an `!xform` include) or a `#` comment line is
    # not primitive text; `readobj` reads each to its line end, so each is dropped before the walk.
    kept = "\n".join(
        "" if line.lstrip().startswith(("!", "#")) else line for line in text.split("\n")
    )
    return frozenset(
        primitive.identifier for primitive in _walk_primitives(kept)
        if primitive.ptype not in _PROBE_SURFACE_TYPES
    )


def parse_polygons(text: str) -> tuple[Polygon, ...]:
    """Every ``polygon`` primitive in ``text``, read through `_walk_primitives` (see there for the
    tokeniser and for why every other primitive type is walked past rather than pattern-matched).

    ``#`` starts a comment that runs to end of line, matching every `.rad` file and every `xform`
    output seen in this project's fixtures (``## materials``, ``# xform``, ...). An input with no
    primitives at all (an empty scene, or one whose every primitive was skipped) returns an empty
    tuple rather than raising -- callers that need "this scene is empty" as a refusal already have it
    from the octree's own bounding cube, which is the authoritative check (module docstring).

    Raises ``ValueError`` on truncated or malformed input -- see `_walk_primitives`.
    """
    return tuple(
        Polygon(
            primitive.identifier,
            primitive.modifier,
            tuple(
                (primitive.floats[k], primitive.floats[k + 1], primitive.floats[k + 2])
                for k in range(0, len(primitive.floats), 3)
            ),
        )
        for primitive in _walk_primitives(text)
        if primitive.ptype == "polygon"
    )


#: Radiance's surface primitive types -- everything that can put a point inside `oconv`'s bounding
#: cube. `src/common/otypes.h` (Radiance 6.1a): the `OBJ_*` block, minus `polygon`, which is the one
#: `parse_polygons` returns and therefore the one already accounted for. `source` is in the list
#: even though a distant source has direction rather than extent: over-reporting a surface type can
#: only make `non_polygon_surfaces` non-empty and so make a caller *skip* a check, never run one on
#: geometry it cannot see.
_NON_POLYGON_SURFACE_TYPES = frozenset(
    {"source", "sphere", "bubble", "cone", "cup", "cylinder", "tube", "ring", "instance", "mesh"}
)


def non_polygon_surfaces(text: str) -> frozenset[str]:
    """The surface primitive types in ``text`` that `parse_polygons` does **not** return.

    **The precondition for any check that reasons from the parsed polygon soup to the octree.**
    `parse_polygons` returns `polygon` primitives and nothing else, so on a scene carrying a
    `sphere` the soup is a strict subset of what `oconv` compiled and the two cannot be compared.
    Measured: a one-polygon scene of extent 1.0 with `m sphere s 0 0 4 5 5 5 2` beside it compiles
    to a bounding cube of **7.00002** -- seven times the parsed extent, with nothing wrong.

    Empty means the soup is the whole scene, and only then may a caller treat the octree's cube as a
    statement about the polygons it holds (`rad_mcp.geometry_tool._refuse_unexplained_octree`).

    Same walker as `parse_polygons`, so a primitive is identified by its own declared argument
    counts rather than by a pattern over the text -- see `_walk_primitives`.
    """
    return frozenset(
        primitive.ptype
        for primitive in _walk_primitives(text)
        if primitive.ptype in _NON_POLYGON_SURFACE_TYPES
    )


def strip_primitives_by_modifier(
    text: str, modifiers: Collection[str]
) -> tuple[str, tuple[str, ...]]:
    """``text`` with every primitive whose **modifier** is named in ``modifiers`` removed, and the
    identifiers of the primitives that were removed.

    Pure text in, pure text out: no binary is spawned, nothing is re-formatted. Each surviving
    primitive is copied out as the exact bytes it occupied (`_Primitive`'s own span), so a quoted
    path keeps its quotes and a float keeps the digits it was written with. What is *not* preserved
    is the whitespace and the comments **between** primitives -- the output is the kept primitives,
    one per line group, separated by a blank line -- because a comment carries no modifier and there
    is no defensible rule for which side of a removed primitive one belongs to.

    **A modifier, not an identifier, and the difference is this repo's own definition of an
    aperture**: `window_group(action="define")` takes *modifier names* and derives its `Aperture`
    from every polygon carrying them (`rad_mcp.window_group_tool`), so "the aperture's own polygons"
    is exactly this set. A primitive that merely *declares* one of those names -- the `void glass
    glazing_mat` material definition itself, whose own modifier is `void` -- is **kept**: it defines
    the name rather than using it, `oconv` accepts a material nothing references, and removing a
    definition something else in the scene still uses would fail the compile outright.

    Raises ``ValueError`` on malformed input, exactly as `parse_polygons` does and for the same
    reason -- both walk the same token stream.
    """
    excluded = frozenset(modifiers)
    kept: list[str] = []
    removed: list[str] = []
    for primitive in _walk_primitives(text):
        if primitive.modifier in excluded:
            removed.append(primitive.identifier)
            continue
        kept.append(text[primitive.start:primitive.end])
    return ("\n\n".join(kept) + "\n" if kept else ""), tuple(removed)


#: The opaque modifier every non-target window group's aperture takes in a per-group ASE scene.
#: Zero reflectance and zero specularity: this surface must neither transmit nor bounce, because
#: LM-83 sec 3.3's ASE term is the direct beam alone and any bounce it added is the quantity the
#: term excludes.
#:
#: **Not `rad_engine.octree.black_material`'s `black`.** That name is defined by the *five-phase*
#: octrees' own material file and piped through `xform -m black`; this one is defined inside the
#: scene text this function returns, in a file that carries no `black` definition at all. Two
#: independent uses of one name in one octree is how a later change to either becomes a silent
#: change to the other.
ASE_BLACK_MODIFIER: Final[str] = "rad_mcp_ase_black"

#: The `void plastic` definition `build_ase_scene` prepends, verbatim. Public because a caller (and
#: a test) that wants to know the exact bytes must not re-type them: `5 0 0 0 0 0` is RGB
#: reflectance, specularity and roughness, every one of them zero.
#:
#: **Prepended rather than appended**, because `oconv` resolves a modifier against what it has
#: already read: a `.rad` that uses this name before defining it fails the compile outright.
ASE_BLACK_PRIMITIVE: Final[str] = f"void plastic {ASE_BLACK_MODIFIER}\n0\n0\n5 0 0 0 0 0\n\n"


@dataclass(frozen=True)
class AseScene:
    """`build_ase_scene`'s result: the scene one window group's ASE run is traced against, and what
    was blacked to make it.

    `blacked` and `blacked_modifiers` are what make the blacking **checkable by the caller** rather
    than a claim -- `StrippedScene.removed`'s own reason, one function over. An empty
    `blacked_modifiers` where the caller named other groups means those apertures are not in this
    scene text, so nothing was closed and this group's ASE run would carry another group's sun with
    no error anywhere. Returned rather than refused here because the repair is the caller's:
    `rad_mcp.annual_tool` already raises exactly that refusal for the suns octree's own shell, with
    the `geometry(action='add', ...)` instruction attached, and this layer may not name a wire type.
    """

    text: str
    blacked: tuple[str, ...]
    """The identifiers of the primitives whose modifier was replaced, in scene order."""
    blacked_modifiers: tuple[str, ...]
    """The other groups' modifiers that actually matched a primitive, sorted."""


def build_ase_scene(
    scene_text: str, *, target_group: str, groups: Mapping[str, Sequence[str]]
) -> AseScene:
    """The pre-blind, zero-bounce ASE scene for **one** window group.

    ``groups`` maps each window group's name to the modifiers its aperture polygons carry -- exactly
    what `window_group(action="define")` takes and what `rad_core.apertures` derives an `Aperture`
    from. The target group keeps its own modifier; **every other group's polygons are re-modified to
    an opaque black** (`ASE_BLACK_MODIFIER`), so the run measures the direct beam through this group
    and no other.

    **Why per group at all.** LM-83 sec 2.2.6's residual asks how many points are still in sun *with
    group g closed*, and one direct-sun run aggregated over every aperture cannot answer it -- ruling
    R116's finding, and the reason `results(action="blind_schedule")` published `groups: null`.

    **Blacked, never deleted.** Deleting the other groups' polygons would leave a hole in the
    envelope and let the sun through where the window was -- a strictly *larger* direct-sun answer
    than either the group open or the group closed, and the one outcome that cannot be right. This
    is the opposite decision from `scene_without_modifiers`, whose caller *removes* the aperture, and
    the two are not in tension: there the blackened surface sits outboard of a `thick 0` BSDF proxy
    and kills the shadow ray the proxy was there to carry, while here there is no proxy and the
    blacked surface is exactly the closed window the residual is about.

    **Only the modifier token moves.** Each blacked primitive is spliced in place -- the rest of the
    input is returned as its own bytes, comments, whitespace, quoted paths and float digits included
    -- so this function cannot change the scene in any way other than the one it is named for. That
    is a stronger guarantee than `strip_primitives_by_modifier`'s (which re-emits the kept
    primitives and drops the comments between them) and it is worth the difference here: a caller
    comparing the polygon count of the blacked scene against the source is checking a real
    invariant, not an artefact of re-emission.

    **A material *definition* is kept, never blacked.** `void glass glazing_south` declares the name;
    its own modifier is `void`, so it does not match, and it must not -- rewriting it would change
    every other surface in the scene that uses that glass, which is the whole facade rather than one
    aperture. Only primitives that *carry* a named modifier are re-modified.

    Refuses, both with `ValueError`, rather than producing a scene that measures the wrong thing:

    - a ``target_group`` that is not a key of ``groups`` -- there is nothing to keep open, so every
      aperture in the scene would be blacked and the run would report zero direct sun for a room
      whose windows it had just made opaque;
    - a modifier shared between the target group and another -- blacking it blacks the **target's
      own** aperture, and the result is a plausible, far too small direct-sun number under an id
      that says this group was open.

    Raises ``ValueError`` on malformed input too, exactly as `parse_polygons` and
    `strip_primitives_by_modifier` do and for the same reason -- all three walk the same token
    stream (`_walk_primitives`).
    """
    if target_group not in groups:
        raise ValueError(
            f"build_ase_scene was asked for window group {target_group!r}, which is not one of the "
            f"groups it was given ({sorted(groups) or 'none at all'}). The target group is the one "
            "aperture this ASE run leaves open; with it absent every aperture in the scene would "
            "take the opaque black modifier, and the run would report zero direct sun for a room "
            "whose windows it had just made opaque."
        )
    target_modifiers = frozenset(groups[target_group])
    others: dict[str, str] = {}
    for name, modifiers in groups.items():
        if name == target_group:
            continue
        for modifier in modifiers:
            shared = modifier in target_modifiers
            if shared:
                raise ValueError(
                    f"window groups {target_group!r} and {name!r} both carry the aperture modifier "
                    f"{modifier!r}, so this ASE run cannot isolate one from the other: blacking it "
                    f"for {name!r} blacks the aperture of {target_group!r} too, and the run would report "
                    f"a plausible, far too small direct-sun number under an id that says "
                    f"{target_group!r} was open. Give each window group its own modifier "
                    "(window_group(action='define')) so the two apertures are separable."
                )
            others[modifier] = name
    return _blacked_scene(scene_text, frozenset(others))


def build_ase_net_scene(
    scene_text: str, *, blacked_groups: Sequence[str], groups: Mapping[str, Sequence[str]]
) -> AseScene:
    """The pre-blind, zero-bounce ASE_net scene (ruling R869): **every** polygon whose modifier
    belongs to a window group in ``blacked_groups`` -- the groups marked automated -- takes
    `ASE_BLACK_MODIFIER`, and every other group's aperture is left open.

    ASE_net is ASE counted from the direct sun that reaches the grid with the automated groups
    closed (R869); a closed automated group is taken to pass no direct sun, the rule the blind
    schedule applies to a group it closes. This is `build_ase_scene` with the open/blacked split
    turned round -- there one group stays open and every other is blacked; here the named groups
    are blacked and every other stays open -- and it splices the modifier token in place exactly
    as that function does (`_blacked_scene`), with `ASE_BLACK_PRIMITIVE` prepended, so the result
    can differ from the input in no other way. A material *definition* is never blacked, for that
    function's reason.

    Refuses, each with `ValueError`:

    - an empty ``blacked_groups`` -- nothing to black is the whole-grid scene, which is a different
      octree under a different id, and an ASE_net leg traced on it would publish ASE_gross as
      ASE_net;
    - a name in ``blacked_groups`` that is not a key of ``groups`` -- its aperture would stay open
      and the run would count its sun in ASE_net;
    - a modifier shared between a blacked group and an unblacked one -- blacking it closes the
      unblacked group's opening too and understates ASE_net, the favourable direction.

    A modifier two blacked groups share is not refused: both are closed either way.
    """
    if not blacked_groups:
        raise ValueError(
            "build_ase_net_scene was given no window group to black. The ASE_net scene blacks the "
            "groups marked automated; with none it is the whole-grid scene, and an ASE_net run "
            "traced on it would publish ASE_gross under ASE_net's name."
        )
    unknown = sorted(set(blacked_groups) - set(groups))
    if unknown:
        raise ValueError(
            f"build_ase_net_scene was asked to black window group(s) {unknown}, which are not "
            f"among the groups it was given ({sorted(groups) or 'none at all'}). Their apertures "
            "would stay open, and the ASE_net run would count their sun."
        )
    blacked_names = frozenset(blacked_groups)
    blacked_modifiers = {
        modifier for name in blacked_names for modifier in groups[name]
    }
    for name, modifiers in groups.items():
        if name in blacked_names:
            continue
        for modifier in modifiers:
            if modifier in blacked_modifiers:
                owner = sorted(
                    other for other in blacked_names if modifier in groups[other]
                )[0]
                raise ValueError(
                    f"window groups {owner!r} (marked automated) and {name!r} (not marked) both "
                    f"carry the aperture modifier {modifier!r}, so the ASE_net run cannot close "
                    f"one and leave the other open: blacking it for {owner!r} closes the opening "
                    f"of {name!r} too, and ASE_net would come out smaller than it is, the "
                    "favourable direction. Give each window group its own modifier "
                    "(window_group(action='define')) so the two apertures are separable."
                )
    return _blacked_scene(scene_text, frozenset(blacked_modifiers))


def _blacked_scene(scene_text: str, modifiers: frozenset[str]) -> AseScene:
    """``scene_text`` with every primitive carrying one of ``modifiers`` re-modified to
    `ASE_BLACK_MODIFIER`, spliced in place, and `ASE_BLACK_PRIMITIVE` prepended -- the one splice
    `build_ase_scene` and `build_ase_net_scene` share."""
    spans: list[tuple[int, int]] = []
    blacked: list[str] = []
    found: set[str] = set()
    for primitive in _walk_primitives(scene_text):
        if primitive.modifier not in modifiers:
            continue
        spans.append((primitive.start, primitive.start + len(primitive.modifier)))
        blacked.append(primitive.identifier)
        found.add(primitive.modifier)

    # `_walk_primitives` yields in scene order, so the spans are already sorted and disjoint.
    pieces: list[str] = [ASE_BLACK_PRIMITIVE]
    cursor = 0
    for start, end in spans:
        pieces.append(scene_text[cursor:start])
        pieces.append(ASE_BLACK_MODIFIER)
        cursor = end
    pieces.append(scene_text[cursor:])
    return AseScene(
        text="".join(pieces),
        blacked=tuple(blacked),
        blacked_modifiers=tuple(sorted(found)),
    )


#: The Radiance material types a direct beam can cross. **Types, not measured transmittances**: a
#: `trans` with zero transmissivity is in this set and transmits nothing, which over-reports -- and
#: over-reporting is the safe direction for the advisory this feeds, where the alternative is
#: parsing every material's arguments and getting `BRTDfunc`'s wrong.
#:
#: **Necessary, not sufficient, and that is stated rather than implied.** A modifier whose type is
#: outside this set is **not** detected: an `alias` to a glass (the walker records no reference
#: token), a `mixfunc` blending one in, a transmitting material introduced by a Radiance release
#: later than this list. So an empty result is *"nothing in this set was found unclaimed"*, never
#: *"every direct path crosses a window group"*.
#:
#: `glow`, `light`, `illum` and `mirror` are deliberately out: the first three **emit** rather than
#: transmit (and this repo puts a `glow` on every aperture itself, which would make every scene
#: report its own window groups), and a mirror reflects. `antimatter` is in, because it deletes the
#: geometry behind it and the beam goes through the hole.
TRANSMITTING_PRIMITIVE_TYPES: Final[frozenset[str]] = frozenset({
    "BRTDfunc", "BSDF", "antimatter", "dielectric", "glass", "interface", "mist",
    "prism1", "prism2", "trans", "transdata", "transfunc",
})


def unclaimed_transmitting_modifiers(
    scene_text: str, *, claimed: Collection[str]
) -> tuple[str, ...]:
    """Modifiers in ``scene_text`` that **transmit light and no window group claims**, sorted.

    **What this is for (ruling R147).** LM-83 sec 2.2.6's blind schedule subtracts one window
    group's direct-sun column from a sum of all of them, and both halves of that arithmetic rest on
    one precondition: *every direct path from a sun to a sensor crosses exactly one window group's
    aperture*. An opening that transmits and belongs to no group breaks it -- it is in **every**
    per-group column, because no per-group ASE octree blacks it -- and
    `rad_core.blinds.group_schedule`'s own docstring carries the measured consequences.

    **This returns CANDIDATES, and a modifier is not a path** (ruling R150). What it computes is: a
    modifier of a transmitting **type** (`TRANSMITTING_PRIMITIVE_TYPES`) that at least one primitive
    is modified by, and that is not in ``claimed``. Whether any of those lies on a path from a sun
    to a sensor is the question the scene *text* cannot answer, and this function does not claim to.
    An earlier draft of this paragraph called it *"the half of the precondition detectable from the
    scene text alone"*, which over-claimed in both directions:

    - **It reports scenes with nothing wrong with them.** An interior glass partition, a
      `dielectric` tabletop, water, a glazed internal door: all correct modelling, none of them
      fenestration a blind schedule could or should close. A caller is told so
      (`BLIND_SCHEDULE_UNCLAIMED_APERTURE`) rather than told to define a window group over their
      furniture -- a `warn` that fires on ordinary scenes gets ignored, and then it is not there for
      the case it exists for.
    - **It is silent on scenes that do break the precondition.** An opening reached through an
      `alias` (the walker records no reference token); a transmitting type outside the set; two
      group apertures **in series** on one path, which needs occlusion geometry this phase does not
      have; and **a hole** -- an absent wall or a dropped `!xform` include admits sun crossing
      **zero** apertures, which is the same arithmetic with nothing here to see. Ruling R150 named
      that last one, and it is the one a "detected/not detected" reading would have missed.

    So an empty result means *"no unclaimed modifier of a type in this set"*, never *"the
    precondition holds"*.

    **A material that nothing uses is not an opening**, so a definition with no surface carrying it
    is excluded: `materials(action='apply_defaults')` writes a palette, and a scene that declares a
    glass it never applies has no hole in it.

    ``claimed`` is the union of every window group's aperture modifiers -- exactly what
    `window_group(action='define')` takes, and what `rad_core.apertures` derives an `Aperture` from.

    Raises ``ValueError`` on malformed input, as every other consumer of `_walk_primitives` does.
    """
    excluded = frozenset(claimed)
    transmitting: set[str] = set()
    used: set[str] = set()
    for primitive in _walk_primitives(scene_text):
        if primitive.ptype in TRANSMITTING_PRIMITIVE_TYPES:
            transmitting.add(primitive.identifier)
        used.add(primitive.modifier)
    return tuple(sorted((transmitting & used) - excluded))


#: Ruling R853's glazing types, read by `unclaimed_glazing_modifiers` and `two_phase_probe_glazing`
#: only: `TRANSMITTING_PRIMITIVE_TYPES` plus `aBSDF` and `trans2`, which transmit and which that set
#: (R147's, feeding `BLIND_SCHEDULE_UNCLAIMED_APERTURE`, left unchanged) does not name.
_GLAZING_PRIMITIVE_TYPES: Final[frozenset[str]] = TRANSMITTING_PRIMITIVE_TYPES | {"aBSDF", "trans2"}

#: The mixtures whose first two string arguments name the two materials they blend.
_MIXTURE_TYPES: Final = frozenset({"mixfunc", "mixdata", "mixpict", "mixtext"})


def _definitions_by_id(primitives: Sequence[_Primitive]) -> dict[str, list[_Primitive]]:
    """Every primitive in ``primitives`` whose type is not in `_PROBE_SURFACE_TYPES`, under its
    identifier, in text order (a name defined twice keeps both, as `oconv` binds each surface to the
    definition preceding it)."""
    by_id: dict[str, list[_Primitive]] = {}
    for p in primitives:
        if p.ptype not in _PROBE_SURFACE_TYPES:
            by_id.setdefault(p.identifier, []).append(p)
    return by_id


def _leads_to(
    name: str, by_id: Mapping[str, Sequence[_Primitive]], ptypes: Collection[str],
) -> bool:
    """Whether any definition of ``name``, or of what it leads to, has a type in ``ptypes``:
    following an `alias` to what it aliases and a mixture (`_MIXTURE_TYPES`) to both materials it
    blends, every definition of each name checked. The one walk `unclaimed_glazing_modifiers` and
    `two_phase_probe_glazing` share."""
    pending, seen = [name], set()
    while pending:
        current = pending.pop()
        if current in seen or current == "void":
            continue
        seen.add(current)
        for material in by_id.get(current, ()):
            if material.ptype in ptypes:
                return True
            if material.ptype == "alias":
                pending.append(material.reference)
            elif material.ptype in _MIXTURE_TYPES:
                pending.extend(material.strings[:2])
    return False


def unclaimed_glazing_modifiers(scene_text: str, *, claimed: Collection[str]) -> tuple[str, ...]:
    """The surface modifiers in ``scene_text`` that reach a transmitting material and that no window
    group claims, sorted (ruling R853: a two-phase result's daylight coefficients trace light through
    each of them, so a class judged from the window groups alone is not known).

    A surface's modifier transmits when any definition of it, or of what it leads to, is of a type in
    `_GLAZING_PRIMITIVE_TYPES`, following an `alias` to what it aliases and a mixture
    (`mixfunc`, `mixdata`, `mixpict`, `mixtext`) to both materials it blends. Every definition of a
    name is followed, as `probe_unfollowed_materials` does. By type, as R147's reading is: a `trans`
    with zero transmissivity counts, and so does an interior glass partition. An `instance` or `mesh`
    is not looked into. ``claimed`` is the union of every window group's aperture modifiers.

    Raises ``ValueError`` on malformed input, as every other consumer of `_walk_primitives` does.
    """
    primitives = list(_walk_primitives(scene_text))
    by_id = _definitions_by_id(primitives)
    surfaces = {p.modifier for p in primitives if p.ptype in _PROBE_SURFACE_TYPES}
    excluded = frozenset(claimed)
    return tuple(sorted(
        m for m in surfaces - excluded if _leads_to(m, by_id, _GLAZING_PRIMITIVE_TYPES)))


def two_phase_probe_glazing(scene_text: str) -> tuple[str, ...]:
    """The surface modifiers the occlusion probe strips on a two-phase run (ruling R856): every
    glazing surface `unclaimed_glazing_modifiers` finds with no window group claiming any, except
    one whose modifier leads to `antimatter`. Removing an antimatter surface fills the hole it
    cuts, so stripping it could seal a lit room; it stays, and `probe_unfollowed_materials` then
    gives no verdict on the scene. Followed through `alias` and the mixtures by the same walk
    (`_leads_to`) `unclaimed_glazing_modifiers` uses. Sorted. Raises ``ValueError`` on malformed
    input."""
    primitives = list(_walk_primitives(scene_text))
    by_id = _definitions_by_id(primitives)
    surfaces = {p.modifier for p in primitives if p.ptype in _PROBE_SURFACE_TYPES}
    return tuple(sorted(
        m for m in surfaces
        if _leads_to(m, by_id, _GLAZING_PRIMITIVE_TYPES)
        and not _leads_to(m, by_id, frozenset({"antimatter"}))
    ))


@dataclass(frozen=True)
class ReadScene:
    """`read_scene`'s result: the parsed polygon soup, and the raw stderr `xform` wrote while
    flattening it. `warnings` exists so a caller can classify a dropped nested include exactly as it
    would classify `oconv`'s own -- see the module docstring's trap 2.
    """

    polygons: tuple[Polygon, ...]
    warnings: list[str]


class SceneFileNotFoundError(RuntimeError):
    """A top-level entry in ``scene_files`` does not exist on disk.

    Code review round 3, finding N1. Ruling R27 requires the polygon soup to be parsed *before*
    `oconv` runs (to decide `-r`), so ``read_scene`` now reads every top-level file itself and runs
    first -- before that reordering, `build_octree`/`oconv` ran first and reported this exact scenario
    as a typed `RadianceError` ("cannot open scene file ..."), never a bare Python exception. Restoring
    that ordering is not available here without undoing R27 (the `-r` decision needs the polygons
    `read_scene` produces, and `oconv` is the only thing that can consume `-r`), so the fix is to
    translate the read failure into this module's own vocabulary at the point it happens, rather than
    let it escape as a stdlib ``FileNotFoundError`` no caller in this codebase's taxonomy catches.

    ``code`` is ``SCENE_FILE_NOT_FOUND`` (ruling R29, allocated `error`; round 3 shipped this raising
    with `code=None` and reported the gap, which is what made the allocation possible rather than
    guessed at). Three existing codes were checked and rejected before allocating a new one:

    - ``ARGUMENT_OUT_OF_RANGE`` does not fit -- the argument was valid when it was given. The file
      vanished afterwards, which is a fact about the filesystem, not about the call.
    - ``PATH_OUTSIDE_ROOT`` names a *policy* boundary, not a missing file.
    - ``OCTREE_INPUT_DROPPED`` is the tempting one, and it is the trap this docstring exists to name
      for the next reader who reaches for it: its own definition begins *"`oconv` exited 0 having
      silently omitted one of its inputs"* -- and here **`oconv` never ran at all**. Claiming it would
      emit a code whose own definition is untrue of the run, which is exactly the mistake ruling R5
      was raised to correct for `OCTREE_EMPTY` vs. `OCTREE_INPUT_DROPPED`, one task earlier in this
      same module.

    Scoped to scenes (`SCENE_*`) rather than generalised to inputs, matching the register's existing
    per-domain habit (`OCTREE_*`, `BSDF_*`, `ILLUM_*`): under allocate-once-never-rename, an
    over-general name is harder to live with than a second specific one later.
    """

    code = "SCENE_FILE_NOT_FOUND"
    severity = "error"

    def __init__(self, message: str, *, path: Path) -> None:
        super().__init__(message)
        self.path = path


# `SceneInlineCommandError` and `_refuse_inline_commands` are imported from `rad_engine.octree`
# above, not defined here. Ruling R33 originally put both in this module, because `read_scene` was
# the only thing that read a scene file as text before spawning something that would execute its `!`
# lines. Phase 3 added a second route -- `run_point_in_time` re-reads the registered geometry from
# the manifest and hands it straight to `build_octree` -- which never passed through here, so the
# guard moved down to the `oconv` boundary that every route shares. See `build_octree` for why that
# placement also closes the TOCTOU window this one could not.
#
# Re-exported through `__all__` so `rad_mcp.geometry_tool` and `rad_mcp.materials_tool` keep
# catching `rad_engine.scene.SceneInlineCommandError` exactly as before: one class, one code, one
# scanner (ruling R30 -- a second copy is a second thing to drift).


def _flatten_scene(
    env: RadianceEnv,
    scene_files: Sequence[str],
    *,
    cwd: Path,
    timeout: float | None,
) -> tuple[Result, frozenset[Path]]:
    """``scene_files`` read, R33-scanned and flattened through the real ``xform`` binary -- the
    mechanics `read_scene` and `scene_without_modifiers` share (R30), returning `xform`'s own
    `Result` alongside every path the R33 guard's include recursion read (`seen`), so each caller
    can do what it likes with the text, the warnings and the include set.

    See `read_scene`'s docstring for why `xform` is fed on **stdin** rather than by file argument,
    and for what `SceneFileNotFoundError` means here.

    An include the guard could not read is not in the returned set -- existing behaviour
    (`octree.py`'s own R391 guard), not something this function changes.
    """
    texts: list[str] = []
    seen: set[Path] = set()
    # R753: the RAYPATH this function's own `xform` spawn gets, so the guard searches it too.
    raypath = env.raypath_dirs(cwd)
    reach = _InlineReach(cwd, raypath)
    echo_dirs = _echo_directories(cwd, scene_files)  # R759: what a refusal may quote from
    for scene_file in scene_files:
        path = cwd / scene_file
        try:
            # Read as **bytes** and decode in a way that cannot raise (`_decode_scene_bytes`). This
            # was `read_text(encoding="utf-8")`, which had two defects at once: a scene file
            # carrying any non-UTF-8 byte surfaced a raw `UnicodeDecodeError` to the wire --
            # `is_error=True`, `structured_content=None`, no advisory code, the uncoded transport
            # failure check 0.4 forbids -- *and* it read different bytes from the ones `xform` and
            # `oconv` go on to execute the `!` lines of. Both are gone: a hostile `!` line hidden
            # behind an invalid byte is now scanned and refused as `SCENE_INLINE_COMMAND` here, at
            # registration, exactly as it is at the `oconv` boundary.
            #
            # Read through `octree.read_scene_text` -- `open_regular_file` under the same decode --
            # rather than `path.read_bytes()`, ruling R48's second direction. `read_bytes` on a FIFO
            # blocks in `open()` forever and wedges the `anyio` worker; measured on a
            # manifest-registered geometry path pointed at one, the process needed `kill -9`.
            # Containment cannot close it: the path was contained when it was *registered*, and a
            # FIFO planted inside the project passes containment legitimately. `S_ISREG` on the
            # opened descriptor is the only guard, and it needs no roots.
            #
            # `FileNotFoundError` below still fires unchanged (it is an `OSError` and
            # `open_regular_file` raises it from `os.open`). **A symlinked top-level entry is
            # followed to its target and scanned** -- `read_scene_text` resolves first, because
            # `oconv`/`xform`/`rfluxmtx` open scene files with `fopen`, which follows the link, and a
            # guard that skipped it read less than the spawn did (a verified RCE; see that reader).
            # Only a symlink *loop* or a non-regular target still raises `ELOOP`/
            # `NotARegularFileError`, `OSError`s this function does not catch and `rad_mcp` shapes at
            # the boundary -- a refusal rather than a hang.
            text = read_scene_text(path)
        except FileNotFoundError:
            # **Narrow on purpose, and widening it broke a deliberate contract.** The third review of
            # the inline-command fix called a symlinked top-level entry escaping here as a bare
            # `OSError` an uncoded transport failure; widening this to `except OSError` and raising
            # `SceneFileNotFoundError` turned `TestAFifoCannotWedgeTheScanningReaders::
            # test_read_scene_refuses_a_fifo_top_level_entry_rather_than_blocking` red, because that
            # test pins the opposite: a non-regular top-level entry "surfaces as an `OSError` the
            # tool layer shapes rather than as a hang", and `SceneFileNotFoundError` is a
            # `RuntimeError`. `NotARegularFileError` carries `PROJECT_PATH_NOT_A_REGULAR_FILE` at the
            # boundary (ruling R58), so swallowing it here would have replaced a specific code with a
            # wrong one. Only the genuinely-missing case belongs to `SCENE_FILE_NOT_FOUND`.
            raise SceneFileNotFoundError(
                f"scene file {scene_file!r} does not exist at {path} -- nothing to build from.",
                path=path,
            ) from None
        # Ruling R33: refuse an inline command before `xform` is spawned. This is the point the
        # content is first in hand, and the last point before Radiance would run it.
        _refuse_inline_commands(
            text, path=path, cwd=cwd, seen=seen, raypath=raypath, reach=reach, echo_dirs=echo_dirs
        )
        texts.append(text)
    _close_inline_reach(reach, cwd=cwd, seen=seen, raypath=raypath, echo_dirs=echo_dirs)
    # **Bytes, not text** (whole-branch review Important 4). `run`'s text branch encodes `input=`
    # strict, and these texts came from `_decode_scene_bytes`, which maps an invalid byte to a lone
    # surrogate -- so one `0xff` in a caller's `.rad` comment raised `UnicodeEncodeError` here, a
    # `ValueError` no tool's `except` tuple catches, and it left the wire as an uncoded transport
    # failure with no `structured_content` at all. `surrogateescape` is the exact inverse of that
    # decode, so `xform` reads the bytes the file held.
    payload = "\n".join(texts).encode("utf-8", "surrogateescape")
    return run(env, ["xform"], cwd=cwd, stdin_bytes=payload, timeout=timeout), frozenset(seen)


@dataclass(frozen=True)
class StrippedScene:
    """`scene_without_modifiers`' result: the flattened scene minus the primitives it was asked to
    drop, the identifiers of the ones it dropped, and the raw stderr `xform` wrote while flattening.

    `removed` is what makes the removal *checkable* by the caller rather than a claim: an empty tuple
    means the named modifiers matched nothing in this scene, which for the suns octree's aperture
    would mean the blackened glazing is still in the pipe.

    `warnings` carries `xform`'s stderr for the same reason `ReadScene.warnings` does -- a dropped
    nested include is a dropped include whichever binary noticed it (module docstring's trap 2).
    """

    text: str
    removed: tuple[str, ...]
    warnings: list[str]


def scene_without_modifiers(
    env: RadianceEnv,
    scene_files: Sequence[str],
    *,
    modifiers: Collection[str],
    cwd: Path | str,
    timeout: float | None = None,
) -> StrippedScene:
    """``scene_files``, flattened through `xform` and with every primitive modified by one of
    ``modifiers`` removed -- one self-contained scene text a caller can write to a file and compile.

    **Why the flatten is not optional.** A `.rad` file may name its geometry through an `!xform`
    include chain (`tests/fixtures/scenes/test_room` is five deep), so a filter that read the
    top-level files alone would silently keep any excluded primitive that lives one level down --
    the same "passes the naive case, misses the indirection" shape ruling R33's own recursion exists
    to close. Flattening first means the filter sees every primitive `oconv` would have seen, and the
    text it returns carries no `!` line at all.

    **What this is for.** The five-phase suns octree (`docs/specs/five-phase-method.md` 4.2) must not
    carry the window group's own glazing polygon: `rad_mcp.annual_tool._dispatch` pipes the registered
    scene through ``xform -m black``, and blackening a surface does not remove it -- it turns it into
    a perfect occluder. With the glazing blackened a fraction of a millimetre behind the BSDF proxy,
    the proxy still wins the *view* ray from the sensor while the *shadow* ray outward to each sun
    dies on `black`, and `Cds` comes back **identically zero** -- every entry, exactly 0 -- with no
    error anywhere (Phase 7a Task 15b). With the glazing kept out, the same octree recovers roughly a
    quarter to a third of what a bare opening transmits, which is what a white venetian tensor tree
    should do; `tests/fixtures/README.md`, finding 4, carries the measurement and its sampling.
    **No single irradiance figure is quoted here on purpose**: at the sampling this server's own
    default preset asks for, that number is an unconverged draw (`docs/tool-surface.md` §11).
    This function is how the caller keeps it out.

    Raises `SceneFileNotFoundError` for a missing top-level entry and `SceneInlineCommandError` for a
    hostile `!` line, exactly as `read_scene` does -- both run before `xform` is spawned.
    """
    result, _ = _flatten_scene(env, scene_files, cwd=Path(cwd), timeout=timeout)
    text, removed = strip_primitives_by_modifier(result.stdout, modifiers)
    return StrippedScene(text=text, removed=removed, warnings=result.warnings)


def read_scene(
    env: RadianceEnv,
    scene_files: Sequence[str],
    *,
    cwd: Path | str,
    timeout: float | None = None,
) -> ReadScene:
    """Resolve every ``!xform`` include in ``scene_files`` through the real ``xform`` binary and
    parse the flattened result into ``Polygon``s.

    ``scene_files`` are read as text and fed to ``xform`` on **stdin**, with no file argument -- see
    the module docstring's trap 1 for why this is not merely a style choice: with a file argument,
    `xform` changes into that file's own directory before parsing it (unless `-c`, which would also
    stop `!xform` lines from being expanded at all), resolving nested includes differently from how
    `oconv` resolves the same scene. Reading from stdin gives `xform` no file to change directory to,
    so it resolves relative to the process cwd -- identical to `oconv`'s own rule, and to every other
    call this module and `octree.py` make (`run`'s ``cwd``, RAYPATH via ``env.environ(cwd)``).

    Verified against both fixtures this module's tests use: the office fixture (self-contained) and
    ``test_room/scene.rad`` (a five-deep `!xform` chain) each reproduce, byte-for-byte in polygon
    count and vertex data, the fixtures' own README reference values -- unchanged by reading via
    stdin rather than a file argument, because neither fixture's scene lives anywhere but its own
    `cwd` (see the module docstring's trap 1 for the constructed case where that would matter).

    Raises ``SceneFileNotFoundError`` -- not a bare ``FileNotFoundError`` -- when a *top-level* entry
    in ``scene_files`` does not exist. See that class's own docstring for why this module, rather than
    `oconv`, is now the thing that reports it (ruling R27's ordering).

    The reading, the R33 scan and the `xform` spawn are `_flatten_scene`'s, shared with
    `scene_without_modifiers` (R30) -- this function is the parse on top of them.
    """
    result, _ = _flatten_scene(env, scene_files, cwd=Path(cwd), timeout=timeout)
    return ReadScene(polygons=parse_polygons(result.stdout), warnings=result.warnings)


@dataclass(frozen=True)
class FlattenedScene:
    """`scene_files` flattened through `xform`, with the includes the R391 guard read to get there."""

    text: str
    includes: frozenset[Path]
    warnings: list[str]


def flatten_scene(
    env: RadianceEnv, scene_files: Sequence[str], *, cwd: Path | str, timeout: float | None = None
) -> FlattenedScene:
    """`scene_files`, flattened through `xform` exactly as `read_scene` does, but exposing the
    `!xform` includes the R33/R391 guard read on the way -- Phase 10's `geometry(action="add")` needs
    them to record a non-metre `.rad` import's `sources` (design §5.9, §5.11): every file the chain
    pulled in via `!xform`, recursively. `scene_files` themselves are **not** in `includes` -- they
    are the caller's own top-level entries, already known without this function's help, exactly as
    `_flatten_scene`'s `seen` never carries them either. Not returned by `read_scene` or
    `scene_without_modifiers`, which had no caller that needed the set before this."""
    result, includes = _flatten_scene(env, scene_files, cwd=Path(cwd), timeout=timeout)
    return FlattenedScene(result.stdout, includes, result.warnings)


def glass_modifiers(text: str) -> frozenset[str]:
    """Identifiers of every `glass` primitive in `text`, walked by `_walk_primitives` (R30)."""
    return frozenset(primitive.identifier for primitive in _walk_primitives(text) if primitive.ptype == "glass")


#: The Radiance material types that redirect light specularly and that the ambient calculation
#: samples poorly: a mirrored light shelf or light pipe, and the two prismatic types (R818). Three
#: type names and no specularity threshold: a specular `metal` or `plastic` is not in the set.
LIGHT_REDIRECTING_TYPES: Final = frozenset({"mirror", "prism1", "prism2"})


def _without_command_lines(text: str) -> str:
    """``text`` with every ``!`` command line removed, a backslash-continued one with its
    continuation lines. A command line is not a primitive, so `_walk_primitives` cannot read past
    one; what the command would produce (an ``!xform`` include) is not read here at all."""
    kept: list[str] = []
    continuing = False
    for line in text.splitlines():
        if continuing or line.lstrip().startswith("!"):
            continuing = line.endswith("\\")
            continue
        kept.append(line)
    return "\n".join(kept)


#: A `LIGHT_REDIRECTING_TYPES` name as a whole token. Text without one cannot hold such a material,
#: so the walk is skipped: it measured 1.83 s on a 9.7 MB scene (Phase 23 review).
_REDIRECTING_TOKEN = re.compile(r"(?<!\S)(?:mirror|prism1|prism2)(?!\S)")


def _redirecting_in(primitives: Sequence[_Primitive]) -> frozenset[str]:
    """Identifiers of the `LIGHT_REDIRECTING_TYPES` primitives in `primitives` that modify another
    non-`alias` primitive, directly or through a chain of `alias`es."""
    redirecting = {p.identifier for p in primitives if p.ptype in LIGHT_REDIRECTING_TYPES}
    alias_of = {p.identifier: p.reference for p in primitives if p.ptype in _NO_ARGUMENT_TRIPLE}

    def underlying(name: str) -> str:
        seen: set[str] = set()
        while name in alias_of and name not in seen:
            seen.add(name)
            name = alias_of[name]
        return name

    return frozenset(
        root for p in primitives
        if p.ptype not in _NO_ARGUMENT_TRIPLE and (root := underlying(p.modifier)) in redirecting
    )


def light_redirecting_modifiers(text: str) -> frozenset[str]:
    """Identifiers of the `LIGHT_REDIRECTING_TYPES` primitives in ``text`` that modify at least one
    other primitive there, directly or through an `alias` (R818). A definition nothing uses is not
    reported: it redirects no light. Text with no such type name as a token is not walked.

    ``!`` command lines are skipped (`_without_command_lines`), so a mirror inside an ``!xform``
    include is not seen. Raises ``ValueError`` on malformed text, as `_walk_primitives` does.
    """
    if _REDIRECTING_TOKEN.search(text) is None:
        return frozenset()
    return _redirecting_in(list(_walk_primitives(_without_command_lines(text))))


def light_redirecting_modifiers_in(texts: Sequence[str]) -> frozenset[str]:
    """`light_redirecting_modifiers` over several scene files compiled together: each text is
    walked on its own (primitive syntax never spans files) and "used" is decided over the union,
    so a mirror defined in one file and used in another is found. A text that does not parse
    contributes nothing and the others are still read. Not walked at all when no text names a
    `LIGHT_REDIRECTING_TYPES` type."""
    if not any(_REDIRECTING_TOKEN.search(text) for text in texts):
        return frozenset()
    primitives: list[_Primitive] = []
    for text in texts:
        try:
            walked = list(_walk_primitives(_without_command_lines(text)))
        except ValueError:
            continue
        primitives.extend(walked)
    return _redirecting_in(primitives)


class SceneAssemblyError(RuntimeError):
    """The octree assembled from ``scene_files`` is not the scene that was requested.

    Unlike every other refusal in this codebase, this one does **not** carry a single ``code``: ruling
    R5 defines ``OCTREE_EMPTY`` and ``OCTREE_INPUT_DROPPED`` as two independently provable claims (see
    the module docstring), and this exception is raised exactly when one or both apply. ``advisories``
    carries whichever of the two fired -- always in this order when both do, ``OCTREE_EMPTY`` first
    because it is the claim about the artifact the caller was building, then one
    ``OCTREE_INPUT_DROPPED`` per dropped include (merged across both `xform`'s and `oconv`'s stderr --
    module docstring, trap 2) in the order `classify()` reported them.
    """

    def __init__(self, message: str, *, advisories: Sequence[EngineAdvisory]) -> None:
        super().__init__(message)
        self.advisories = tuple(advisories)


def _artifact_warning_to_advisory(warning: ArtifactWarning) -> EngineAdvisory:
    """``rad_core.geometry``'s own finding shape, promoted to this layer's ``EngineAdvisory`` so
    ``SceneAssembly.advisories`` is one type regardless of whether a finding came from stderr
    (``classify``) or from a pure check over the parsed polygon soup.

    The reverse of ``rad_engine.project._artifact_warning``, which exists for the identical reason
    one layer up. ``lines`` is empty for the same reason that function's own manifest-derived
    advisories carry no lines: this finding never came from a Radiance stream, and inventing one
    would misrepresent where it came from.

    **Ruling R28 (amended, docs/plan.md Settled): refuses a ``warning`` that carries a ``field``,
    rather than silently dropping it.** ``ArtifactWarning`` gained an optional ``field`` under ruling
    R24 specifically so a finding like `REFLECTANCE_IMPLAUSIBLE`
    (`rad_core.materials._plausibility_warning`) is machine-distinguishable between which argument it
    is about. ``EngineAdvisory`` has no such slot, and R28's amendment settled that it never should:
    its own docstring is explicit that a stderr-derived finding "qualifies the whole run rather than
    one result field", so a field-bearing finding must not be laundered through it in the first place
    -- ``ArtifactWarning`` (which already carries ``field``) is the shape for that, all the way to
    ``rad_mcp.advisories.to_wire``'s own ``field`` keyword. An earlier version of this function dropped
    ``field`` here with an accurate comment saying so; a comment does not stop the next caller, so this
    now raises instead -- the same posture ``LM83Default.__post_init__`` and `apply_defaults`'s role
    check already take elsewhere in this project: turn a silent wrong answer into an immediate one.
    No finding this module's own callers produce today sets ``field`` (`enclosure_warning` does not),
    so this guard has no live caller to break; if one is ever added that legitimately needs to convert
    a field-bearing warning here, that is new evidence the amended ruling is wrong and should be
    reported, not worked around.
    """
    if warning.field is not None:
        raise ValueError(
            f"_artifact_warning_to_advisory cannot convert {warning.code!r} "
            f"(field={warning.field!r}) -- EngineAdvisory has no field slot, and ruling R28 "
            "(docs/plan.md) refuses to launder a field-bearing ArtifactWarning through it silently. "
            "A field-bearing finding belongs on ArtifactWarning all the way to rad_mcp.advisories."
        )
    return EngineAdvisory(
        code=warning.code, severity=warning.severity, message=warning.message, lines=()
    )


def _empty_octree_advisory(octree: Octree, *, out_path: Path, cwd: Path) -> EngineAdvisory:
    """The one ``OCTREE_EMPTY`` advisory, built directly from the bounding cube -- never from a
    stderr line (module docstring, trap 3). ``lines`` is empty for the same reason as
    ``_artifact_warning_to_advisory``: what proves this is `getinfo -d`, not anything Radiance wrote
    to stderr.
    """
    return EngineAdvisory(
        code="OCTREE_EMPTY",
        severity="error",
        message=(
            f"{out_path.name} contains no geometry (bounding cube size 0) although oconv exited 0. "
            f"Check RAYPATH contains '.', and that scene paths resolve from {cwd}."
        ),
        lines=(),
    )


def _resolution_advisory(ratio: float, maxres: int) -> EngineAdvisory:
    """``OCTREE_RESOLUTION_LOW`` (warn), emitted whenever `-r` was raised above `oconv`'s own
    default to meet the computed ratio (ruling R27) -- reporting that the raise happened, not that
    the octree is under-resolved (this module never lets that happen -- see the module docstring).
    """
    return EngineAdvisory(
        code="OCTREE_RESOLUTION_LOW",
        severity="warn",
        message=(
            f"this scene's largest-bounding-box-dimension to smallest-edge ratio is {ratio:.1f}, "
            f"above oconv's default -r {OCONV_DEFAULT_MAXRES} -- raised to -r {maxres} so the octree "
            "does not silently under-resolve fine geometry (docs/tool-surface.md)."
        ),
        lines=(),
    )


@dataclass(frozen=True)
class SceneAssembly:
    """Everything ``geometry(action="add"/"check")`` (Task 7) needs to fill its envelope, computed
    once and handed across as data rather than re-derived at the wire layer.

    ``polygons`` is the parsed soup ``inventory``/``bbox``/``enclosed``/``resolution_ratio`` were all
    computed from, kept on the result so a caller with a further pure check to run (a role-specific
    enclosure test, say) does not have to re-flatten and re-parse the scene to get it.

    ``resolution_ratio`` is ``octree_resolution_ratio(polygons)`` (ruling R27), or ``None`` when it
    could not be computed -- an empty soup, or one where every edge sits at or below the
    tessellation-noise floor (`rad_core.geometry._MIN_EDGE_LENGTH_M`). Carried forward so a caller
    never has to recompute it to learn what `-r` was chosen against.
    """

    octree: Octree
    polygons: tuple[Polygon, ...]
    inventory: GeometryInventory
    bbox: BoundingBox
    enclosed: bool
    resolution_ratio: float | None
    advisories: tuple[EngineAdvisory, ...]


def assemble_scene(
    env: RadianceEnv,
    scene_files: Sequence[str],
    *,
    known_modifiers: Collection[str],
    out_path: Path | str,
    cwd: Path | str,
    ground_modifiers: Collection[str] = (),
    freeze: bool = True,
    timeout: float | None = None,
) -> SceneAssembly:
    """Parse ``scene_files``, decide `oconv`'s `-r` from what was parsed (ruling R27), build the
    frozen octree, and prove the two agree before inventorying the geometry.

    Three refusals, all propagated rather than caught:

    - ``SceneAssemblyError`` (this module) when the octree is empty, or a `!xform` include was
      silently dropped (by `xform`'s own run, `oconv`'s, or both), or both apply at once -- see the
      module docstring's trap 3 and ``SceneAssemblyError``'s own docstring for exactly which
      advisories it carries and why there is no single ``code``.
    - ``rad_engine.octree.OctreeSetOverflowError`` / ``OctreeTooManyFilesError`` when `oconv` aborts
      outright (no octree written at all) -- already coded (``OCTREE_SET_OVERFLOW``/
      ``OCTREE_TOO_MANY_FILES``), propagated unchanged from `build_octree`.
    - ``rad_core.geometry.UndefinedModifierError`` when a parsed polygon's modifier is not in
      ``known_modifiers`` -- the materials this call's caller has already registered. That set is
      always the caller's to supply (``rad_core.geometry.inventory``'s own docstring): this module
      builds octrees, not materials, and cannot know what `oconv` read.

    On success, every remaining stderr warning (`classify`'s output over `xform`'s and `oconv`'s
    combined warnings, minus whatever ``OCTREE_INPUT_DROPPED`` findings would have already triggered
    a refusal -- there cannot be any left at this point), ``NOT_ENCLOSED`` if the soup (minus
    ``ground_modifiers``) does not bound a volume, and ``OCTREE_RESOLUTION_LOW`` if `-r` had to be
    raised are returned on ``SceneAssembly.advisories``, never silently dropped.

    ``ground_modifiers`` is forwarded to ``enclosure_warning`` unchanged: "a way to say what is
    ground" is Task 2's, not this module's to re-derive (``rad_core.geometry.free_edge_count``'s own
    docstring) -- an empty collection (the default) checks the whole soup, exactly as passing nothing
    already does one layer down.
    """
    cwd = Path(cwd)
    parsed = read_scene(env, scene_files, cwd=cwd, timeout=timeout)

    # Ruling R27: decide -r from the polygons before oconv ever runs. Guarded rather than assumed --
    # an empty or fully-degenerate soup cannot produce a ratio, and that is never this function's own
    # refusal to raise; the octree build below still runs and reports emptiness/drops the normal way.
    ratio: float | None = None
    maxres: int | None = None
    if parsed.polygons:
        try:
            ratio = octree_resolution_ratio(parsed.polygons)
        except GeometryError:
            ratio = None
        if ratio is not None and ratio > OCONV_DEFAULT_MAXRES:
            maxres = math.ceil(ratio)

    octree = build_octree(
        env,
        list(scene_files),
        out_path=out_path,
        cwd=cwd,
        freeze=freeze,
        allow_empty=True,  # this function decides how to report emptiness, not build_octree
        maxres=maxres,
        timeout=timeout,
    )

    # Trap 2: classify xform's and oconv's stderr together, in one call, so the same dropped include
    # reported by both merges into one finding rather than two.
    stderr_advisories = classify([*parsed.warnings, *octree.warnings])
    dropped = tuple(a for a in stderr_advisories if a.code == "OCTREE_INPUT_DROPPED")

    if octree.is_empty or dropped:
        advisories: list[EngineAdvisory] = []
        if octree.is_empty:
            advisories.append(_empty_octree_advisory(octree, out_path=Path(out_path), cwd=cwd))
        advisories.extend(dropped)
        raise SceneAssemblyError(
            f"the octree assembled from {list(scene_files)} is not the scene requested: "
            + "; ".join(f"{a.code} ({a.subject or a.message})" for a in advisories),
            advisories=advisories,
        )

    geometry_inventory = inventory(parsed.polygons, known_modifiers)  # raises UndefinedModifierError
    bbox = bounding_box(parsed.polygons)

    findings: list[EngineAdvisory] = list(stderr_advisories)  # none can be OCTREE_INPUT_DROPPED here
    enclosure = enclosure_warning(parsed.polygons, ground_modifiers=ground_modifiers)
    if enclosure is not None:
        findings.append(_artifact_warning_to_advisory(enclosure))
    # Both, not just `maxres`: the advisory quotes the ratio, so it needs the number that justified
    # the raise as much as the raise itself. Today `maxres` is only ever set inside the
    # `ratio is not None` branch above, but that couples two separate variables by accident rather
    # than by construction -- a `maxres` reaching this function from anywhere else (a caller-supplied
    # override being the obvious one) would format `None` and raise, after the octree was already
    # built, in the one advisory that exists to report an odd aspect ratio or a units mistake.
    if maxres is not None and ratio is not None:
        findings.append(_resolution_advisory(ratio, maxres))

    return SceneAssembly(
        octree=octree,
        polygons=parsed.polygons,
        inventory=geometry_inventory,
        bbox=bbox,
        enclosed=enclosure is None,
        resolution_ratio=ratio,
        advisories=tuple(findings),
    )
