"""`rad_engine.rpict` -- the fisheye renderer `run_glare` needs, and nothing else `rpict` can do.

Owns exactly one `rpict` invocation shape: a hemispherical fisheye view, at one of two quality
presets, with no post-processing. `docs/plan.md` Phase 8 puts this module in Phase 8 rather than
Phase 9 deliberately -- without it, `run_glare` could only ever accept a caller-supplied `image=`
(half a tool), or Phase 8 would have to reach into Phase 9's general-purpose renderer and pull it
forward without saying so. What follows is the restricted slice Phase 8 actually needs; a
general `rpict` (perspective views, `-av`/`-aw` ambient file reuse, `pfilt` tone-mapping) is
Phase 9's, not this module's.

**No `pfilt`, ever.** `pfilt` averages neighbouring pixels together (its `-r` radius and `-e`
exposure both work by blending), and the whole reason `rad_core.glare.MIN_RESOLUTION_PX` sets an
800x800 floor is that a glare source can be small enough to hide inside a single pixel at lower
resolution -- averaging that pixel with its neighbours is precisely how a real bright source goes
undetected. `docs/plan.md` Phase 8 names `pfilt` as the one Phase 9 capability that must not reach
this path, so this module never imports it, never spawns it, and never emits its name in an argv.
`TestTheArgvIsAFisheyeAndNothingElse.test_pfilt_never_appears` asserts this by negative match.

**Only `-vth`, never `-vtv`/`-vtl`/`-vtc`/`-vts`.** `evalglare` requires a fisheye
(`rad_core.glare.EVALGLARE_VIEW_TYPES = {"h", "a"}`), and this renderer only ever builds the
hemispherical ('h') member of that set -- the angular fisheye ('a') is a real evalglare-legal view
this module simply does not build, because nothing in this phase asks for it. `build_rpict_argv`
hardcodes `-vth` and both view angles at `rad_core.glare.FISHEYE_ANGLE_DEG` (180 degrees, sourced
"for fisheye images, this should be 180 degrees" -- see that module).

**No `fast` quality.** `GLARE_QUALITY_PRESETS` accepts only `"accurate"` and `"maximum"`; any
other string raises `ValueError` rather than silently falling back to a cheaper preset. The source
table's `Fast / preview` column is the one deliberately not offered (ruling R299).
`docs/tool-surface.md` #13: *"A low-quality ambient calculation produces a confident, plausible,
wrong DGP. This is the metric where cheap parameters do the most damage."* A glare render is the
one Radiance output this project treats a bad ambient calculation as unacceptable for, not merely
slow.

**Measured: `docs/specs/rpict-parameter-guide.md`'s `-sj` ("Specular jitter") is not a real
`rpict` flag; `-ss` is used in its place, carrying the same table values.** `rpict -defaults` and
`man rpict` on this machine (Radiance 6.1a) list no `-sj` option at all -- the specular-family
flags are exactly `-ss` (specular sampling) and `-st` (specular threshold), and `man rpict`'s own
`-ss` entry is the one that talks about jitter ("A value of zero means that no jittering will
take place, and all reflections will appear sharp"). Passing `-sj` verbatim, as the source table's
row literally names it, was measured to fail every single invocation: `rpict -vth ... -sj 0.7 ...`
exits 1 with `rpict: fatal - command line error at '-sj'` before a single ray is traced. This is a
documentation defect in the source table, caught by this module's own `@pytest.mark.radiance`
render test (Task 5's mandatory real render) rather than assumed away -- the same "measured, not
assumed" posture `rad_engine.evalglare` already applies (rulings R257, R259, R260, R268, R269, all
"the spec/man-page claim is X; measured against real Radiance it is Y"). That row's values (`0.7`
Accurate, `1` Maximum) are carried through unchanged onto `-ss`; only the flag name changes.

**The line citations in this module were re-anchored at the Phase 8 whole-branch fix wave (finding
I8).** Task 8's commit `4bf635f` inserted a twelve-line R271 correction block at the top of
`docs/specs/rpict-parameter-guide.md` and re-anchored nothing, so every `:9`/`:11-28` citation below
pointed twelve lines short. The table's header is now line **21** and its eighteen flag rows are
lines **23-40**, with `-sj` at line **32**. Three sites also called `-sj` "row 20" or "the 20th
row", which was wrong *before* that commit as well as after it: `-sj` is the table's **10th flag row
of 18**, and the number 20 was never a row index -- it was the guide's old line number for that row,
read as one.
Ruling R262 binds which *column* of which *table* this module's two presets come from -- it does
not, and cannot, certify that every flag name printed in that table is one `rpict`'s own parser
accepts, and this correction does not touch which family or which column either preset draws from.

**Ruling R262 -- both presets are one column each of one table, not a blend of two tables.**
`docs/specs/rpict-parameter-guide.md`, the "Quality presets" table (header line 21, eighteen flag
rows at lines 23-40), publishes three columns for one DGP-oriented parameter family: Fast/preview,
Accurate, Maximum. `"accurate"` is that table's **Accurate** column, transcribed whole (all 18
flags); `"maximum"` is its **Maximum** column, transcribed whole. Two other sourced parameter
sets exist in this repository and neither is used here:
`docs/specs/workflows-point-in-time-to-annual.md:81` (the *ambient family*'s High-Fidelity row,
`-ab 7 -ad 4096 -as 2048 -ar 1024 -aa 0.15`) and the same file's DGP script config block at
lines 356-361 (`AB=6 AD=2048 AS=1024 AR=512 AA=0.15`). `docs/plan.md` check 9.8 forbids blending
the ambient family (draft/standard/high/reference -- see `rad_engine.rtrace.QUALITY_PRESETS`) with
this DGP family (fast/accurate/maximum) into one invented scale. **Ruling R299 renamed this
module's first preset from `"high"` to `"accurate"` for exactly that reason**: `"high"` is the
ambient family's word and it was carrying the DGP family's numbers, which is the blend check 9.8
exists to prevent, written in one name instead of one table. Both preset names now come from the
DGP table, and so do their values -- the Accurate and Maximum columns only, never either
ambient-family set.
`TestThereIsNoFastQuality.test_neither_preset_is_the_ambient_familys_high_fidelity_row` asserts
the values are not the ambient family's High-Fidelity `-ab 7 -ad 4096` pair, by negative match.

**Warnings are carried, verbatim and in order, on `RenderedFisheye.warnings`.** This is the same
contract `Octree.warnings`, `ReadScene.warnings`, `StrippedScene.warnings`, `ImageInfo.warnings`
and `EvalglareResult.notices` already keep for their own spawns (`rad_engine.octree`,
`rad_engine.scene`, `rad_engine.image`, `rad_engine.evalglare`): per CONTEXT.md, a Radiance process
that exits 0 having written a warning to stderr is this project's single most common route to a
confidently wrong answer, and `rpict` at the Accurate or Maximum preset is the heaviest single
spawn in this phase -- it has the most ambient-calculation warnings to say something with.
`render_fisheye` therefore does **not** return a bare `Path` -- a caller could not learn what
`rpict` said on stderr from one -- it returns a `RenderedFisheye`, whose `.path` is the written
file and whose `.warnings` is `Result.warnings` carried through unchanged: never merged, never
reworded, never truncated to "the first line" or "non-empty".

**Every spawn goes through `rad_engine.render.spawn_render`, never a bare `subprocess` call, and
stdout is never merged with stderr.** `rpict` writes the HDR image to stdout; merging stderr into
that stream (`> file 2>&1`) corrupts the picture with warning text, which is exactly why
`rad_engine.run.run_to_file` exists (`spawn_render`'s own one call to it, see its docstring) and
exactly why this module's one spawn goes there rather than `run`, which is for text output.

**Phase 9 Task 8: this module holds no `subprocess` call and no `run_to_file` call of its own.**
Before Task 8, `render_fisheye` called `rad_engine.run.run_to_file` directly -- a real spawn, but
one that started whatever program its argv named without checking what that program was.
`rad_engine.render.spawn_render` is the allow-list that checks (`_ALLOWED_PROGRAMS = {"rpict"}`,
`ProgramNotAllowed` for anything else), and this module now goes through it instead: the same
structural guarantee `rad_engine.render`'s own module docstring calls "the second half of the
`pfilt` boundary" (Task 8 is the first, the import-closure ratchet `tests/test_pfilt_boundary.py`
asserts). Nothing about the argv this module builds changed -- only the function that starts the
process.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Final

from rad_core.glare import FISHEYE_ANGLE_DEG
from rad_core.quality import DGP_PRESETS, QualityParameters

from .env import RadianceEnv
from .render import spawn_render

__all__ = [
    "GLARE_QUALITY_PRESETS",
    "RenderedFisheye",
    "build_rpict_argv",
    "dgp_ambient_parameters",
    "pixel_sampling_jitter",
    "render_fisheye",
]

#: **Not the identity re-export a pure move would give.** `rad_core.quality.DGP_PRESETS` (Phase 9
#: Task 2) holds the sourced three-member family (`fast`/`accurate`/`maximum`) transcribed from
#: `docs/specs/rpict-parameter-guide.md`'s "Quality presets" table (header :21, rows :23-40). This
#: module deliberately publishes only an explicit two-member subset, built by name -- `"accurate"`
#: is that table's **Accurate** column, `"maximum"` is its **Maximum** column (rulings R262, which
#: column; R299, which name) -- because ruling R299/docs/tool-surface.md #13 says a glare render
#: never gets the cheap `fast` column: "a low-quality ambient calculation produces a confident,
#: plausible, wrong DGP." See the module docstring for why the other two sourced parameter sets in
#: this repository (the ambient family's High-Fidelity row and the DGP script's own config block,
#: both in `workflows-point-in-time-to-annual.md`) are deliberately not used here either. The `-sj`
#: -> `-ss` substitution (ruling R271, see the module docstring) already happened once, in
#: `rad_core.quality`; it is not repeated here.
GLARE_QUALITY_PRESETS: Final[dict[str, dict[str, str]]] = {
    key: DGP_PRESETS[key] for key in ("accurate", "maximum")
}


def _number(value: float) -> str:
    """A float as `rpict`'s argv sees it: `%g`, so `180.0` becomes `180` and `2.0` becomes `2` --
    matching the validated fixture line in
    `tests/fixtures/scripts/prespective&fish-eye_rpict.sh` (`-vp 2 5 1.6`, not `-vp 2.0 5.0 1.6`).
    Same convention as `rad_engine.evalglare`/`rad_engine.matrices`/`rad_engine.bsdf`/
    `rad_engine.klems`'s own `_number` (ruling R30: one formatting rule, not a further copy of
    it) -- kept as a module-local one-liner rather than imported, matching how each of those four
    modules already keeps its own copy of this exact expression."""
    return f"{value:g}"


def dgp_ambient_parameters(quality: str) -> QualityParameters:
    """`GLARE_QUALITY_PRESETS[quality]`'s five ambient flags (`-ab`/`-ad`/`-as`/`-ar`/`-aa`), as the
    `rad_core.quality.QualityParameters` `rad_core.quality.ambient_interpolation_radius_m` takes.

    **Phase 9 Task 5.** `GLARE_QUALITY_PRESETS` holds each preset as the raw `{flag: value}` string
    dict `build_rpict_argv` writes straight into an argv -- there was no typed `QualityParameters`
    for the DGP family before this task, because nothing needed one; `ambient_interpolation_radius_m`
    is the first caller that does. This is the one place that conversion happens, so `run_glare` can
    resolve the preset it named without hand-parsing `GLARE_QUALITY_PRESETS` a second, drift-prone
    time in `rad_mcp` -- the same "one conversion, not a copy of it" reasoning
    `parameters_dict`/`resolve_quality` already hold for the ambient family. `render_fisheye` calls
    this internally for the render path; `run_glare`'s `image=` path (nothing rendered this call, so
    there is no `RenderedFisheye` to read a resolved preset off) calls it directly.

    Raises `ValueError` for an unknown `quality`, naming the two valid choices -- the same failure
    `build_rpict_argv` raises for the identical condition, so a caller cannot tell which of the two
    functions rejected it from the message alone, which is fine: both mean "not accurate or maximum".
    """
    try:
        preset = GLARE_QUALITY_PRESETS[quality]
    except KeyError:
        raise ValueError(
            f"unknown quality {quality!r}; expected one of {sorted(GLARE_QUALITY_PRESETS)}. "
            "There is no `fast` quality for a glare render -- docs/tool-surface.md #13: a "
            "low-quality ambient calculation produces a confident, plausible, wrong DGP."
        ) from None
    return QualityParameters(
        ambient_bounces=int(preset["-ab"]),
        ambient_divisions=int(preset["-ad"]),
        ambient_super_samples=int(preset["-as"]),
        ambient_resolution=int(preset["-ar"]),
        ambient_accuracy=float(preset["-aa"]),
    )


def pixel_sampling_jitter(quality: str) -> tuple[float, float, float]:
    """`(-dj, -pj, -ps)` for `quality`, as floats -- `PIXEL_SAMPLING_CONFLICT`'s own three inputs
    (docs/tool-surface.md #16: *"-dj > 0 requires -ps 1"*, and `-pj` carries the same note).

    **Verified to fire by construction on `"accurate"`, not a hypothetical.** `GLARE_QUALITY_
    PRESETS["accurate"]` is `-ps 4` with `-dj 0.7` and `-pj 0.9` (this module's own docstring,
    Ruling R262); `"maximum"` is `-ps 1` with `-dj 1` and `-pj 1` -- `-ps 1` is exactly the fix the
    sourced note recommends, so `"maximum"` never conflicts and `"accurate"`, `run_glare`'s own
    default, always does.

    A plain tuple of the three raw numbers, not a bool or a pre-built message: `run_glare` decides
    whether the condition holds and writes the wire `Advisory` (the same "resolve the preset here,
    decide and shape at the tool layer" split `dgp_ambient_parameters` already keeps), since the
    condition and its wording are this task's own decision, not `rad_engine`'s.
    """
    preset = GLARE_QUALITY_PRESETS[quality]
    return float(preset["-dj"]), float(preset["-pj"]), float(preset["-ps"])


def build_rpict_argv(
    octree: Path | str,
    *,
    position: tuple[float, float, float],
    direction: tuple[float, float, float],
    up: tuple[float, float, float] = (0.0, 0.0, 1.0),
    resolution: int,
    quality: str,
) -> list[str]:
    """The `rpict` argv for one hemispherical fisheye render, `octree` always last.

    Emits `-vth` and only `-vth` -- never `-vtv`, `-vtl`, `-vtc` or `-vts` -- with both `-vh` and
    `-vv` fixed at `rad_core.glare.FISHEYE_ANGLE_DEG` (180 degrees). `resolution` becomes both
    `-x` and `-y`: this module does not itself enforce
    `rad_core.glare.MIN_RESOLUTION_PX`/`MAX_RESOLUTION_PX` -- that is
    `rad_core.glare.check_render_resolution`'s job, run at the tool layer before this function is
    ever called, matching how `rad_engine.evalglare` "knows nothing about whether a `ViewHeader` or
    a resolution is a *valid* input" for its own caller's question.

    **That sentence was aspirational until the Phase 8 whole-branch fix wave** (finding I2). It
    named `check_glare_input`, which `run_glare` reached only *after* rendering, from the header of
    the image this function had just written -- so the floor this docstring stated as enforced
    before the call was in fact enforced after it, and a `resolution=500` render was paid for in
    full and then discarded. `check_render_resolution` is the pure function that answers the
    question from the argument, and `run_glare` calls it with its other argument checks.

    `quality` selects a whole row from `GLARE_QUALITY_PRESETS` (`"accurate"` or `"maximum"` only);
    any other string raises `ValueError` naming the valid choices, rather than silently
    substituting one of them -- there is no `fast` quality for a glare render
    (`docs/tool-surface.md` #13, quoted in the module docstring).

    Returns a plain argv **leading with the bare binary name** (`"rpict"`), matching
    `build_octree`'s and `build_rtrace_argv`'s own contract: `rad_engine.run.run_to_file`
    **substitutes** that first token, splitting `binary, *args = argv` and spawning
    `[str(env.path(binary)), *args]`. Nothing is added: the argv spawned is
    never longer than the one built here, and the token the old wording called missing is the very
    one being resolved. Corrected by the whole-branch fix wave (ruling R323), which also made
    `tests/test_matrices.py::TestTheArgvContractIsStatedTruthfully` normalise whitespace before
    matching -- this sentence was wrapped across a line break, and the guard matched per line.
    """
    try:
        preset = GLARE_QUALITY_PRESETS[quality]
    except KeyError:
        raise ValueError(
            f"unknown quality {quality!r}; expected one of {sorted(GLARE_QUALITY_PRESETS)}. "
            "There is no `fast` quality for a glare render -- docs/tool-surface.md #13: a "
            "low-quality ambient calculation produces a confident, plausible, wrong DGP."
        ) from None

    px, py, pz = position
    dx, dy, dz = direction
    ux, uy, uz = up

    argv: list[str] = [
        "rpict",
        "-vth",
        "-vp", _number(px), _number(py), _number(pz),
        "-vd", _number(dx), _number(dy), _number(dz),
        "-vu", _number(ux), _number(uy), _number(uz),
        "-vh", _number(FISHEYE_ANGLE_DEG),
        "-vv", _number(FISHEYE_ANGLE_DEG),
        "-x", str(int(resolution)),
        "-y", str(int(resolution)),
    ]
    # Table row order (rpict-parameter-guide.md :23-40), preserved by `GLARE_QUALITY_PRESETS`
    # being an insertion-ordered dict literal -- not that any test or caller depends on the
    # ordering of these particular flags among themselves, only that all 18 are present.
    for flag, value in preset.items():
        argv += [flag, value]
    argv.append(str(octree))
    return argv


@dataclass(frozen=True)
class RenderedFisheye:
    """`render_fisheye`'s outcome: the file it wrote, and the `rpict` stderr carried through.

    `warnings` is `rad_engine.render.RenderedImage.warnings` (itself `rad_engine.run.Result.
    warnings`), verbatim and in call order -- see the module docstring for why this is not a bare
    `Path`. `argv` is the argv `build_rpict_argv` built (bare `"rpict"` as `argv[0]`, not the
    resolved binary path `run_to_file` substitutes in) -- Phase 9 Task 8 routed the spawn itself
    through `rad_engine.render.spawn_render`, whose own `RenderedImage` does not carry the
    resolved argv back out, so this field is the pre-spawn argv rather than a repeat of a value
    the allow-list already discards. Still a caller's audit trail, matching `EvalglareResult.
    argv`'s own reason for existing -- only the *which* argv changed, not the reason.
    """

    path: Path
    warnings: tuple[str, ...]
    argv: tuple[str, ...]
    parameters: QualityParameters
    """The `quality` preset actually used for this render, resolved by `dgp_ambient_parameters`
    (Phase 9 Task 5) -- exposed so `run_glare` can compute `rad_core.quality.
    ambient_interpolation_radius_m` as `point_in_time_tool._dispatch` does off its own pipeline's
    `.parameters`, without re-deriving or hand-parsing `GLARE_QUALITY_PRESETS` a second time in
    `rad_mcp`. **Not** the octree's own bounding cube -- this module builds no octree of its own
    (see the module docstring), so it has no `Octree.bbox` to carry; `run_glare` already has that
    from its own `build_octree` call, the same one it hands this function `octree`'s path from, so
    threading it back through here would be a second copy of a value the caller never lost."""


def render_fisheye(
    env: RadianceEnv,
    octree: Path | str,
    *,
    out_path: Path | str,
    position: tuple[float, float, float],
    direction: tuple[float, float, float],
    resolution: int,
    quality: str,
    cwd: Path | str,
    timeout: float | None = None,
) -> RenderedFisheye:
    """Render `octree` as a hemispherical fisheye HDR at `out_path`.

    Builds the argv with `build_rpict_argv` (fixed `up=(0, 0, 1)` -- Phase 8 has no caller that
    needs a tilted fisheye) and runs it through `rad_engine.render.spawn_render`, which writes
    `rpict`'s stdout (the HDR image) to `out_path` and keeps stderr separate in memory -- never
    `> out_path 2>&1`, which would corrupt the image with warning text spliced into the pixel
    stream.

    **Phase 9 Task 8: the spawn goes through `spawn_render`, not a bare `run_to_file` call.**
    `spawn_render` is `rad_engine.render`'s allow-list (`_ALLOWED_PROGRAMS = {"rpict"}`) -- this
    function used to call `rad_engine.run.run_to_file` directly, which starts a process without
    checking what it is being asked to start. Nothing about the argv this function builds changed;
    only the function that starts the process moved, so a `pfilt` (or any other post-processing
    program) spawned anywhere in this module's own render path now raises `rad_engine.render.
    ProgramNotAllowed` rather than silently running. See `tests/test_pfilt_boundary.py` and this
    phase's task-8 report for the two mutations that prove both this and the import-closure guard
    below it are capable of catching a real regression.

    Raises `RadianceError` if `rpict` exits non-zero (via `spawn_render`'s own `run_to_file` call,
    which defaults `check=True`); the caller gets the same class every other engine module already
    raises for a failed spawn, carrying `rpict`'s stderr. Raises `rad_engine.render.
    ProgramNotAllowed` if `argv[0]`'s basename is ever anything but `rpict` -- unreachable through
    this function's own `build_rpict_argv` call, but not through a caller that hands `render_fisheye`
    a hand-built argv, and not through a future edit to this module that starts building a
    different one.

    **A failed render leaves no file behind, and no longer destroys a pre-existing one.**
    `spawn_render` has to open a descriptor before it spawns -- the file is `rpict`'s stdout -- so
    a render killed by its timeout or exiting non-zero leaves a truncated HDR behind. Measured here,
    the office fixture
    at `resolution=20000, quality="maximum"`: killed cleanly by its bound with no orphaned `rpict`
    process, leaving a partial `.hdr` of **563 bytes at a 3 s timeout, 2 682 at 20 s and 7 880 at
    60 s** -- the size is how far the render got, not a property of the resolution (Phase 8
    whole-branch review, finding I5, whose own 2 805 343-byte figure does not reproduce for that
    reason). That file has a valid-looking Radiance header and a truncated pixel body, which is
    the worst thing this project can produce: `getinfo` reads it, `image(action="inspect")` reports
    a resolution for it, and only `evalglare` or a pixel scan discovers it is short. Since the
    Phase 9 whole-branch fix wave that partial is a **temp file in the destination directory**,
    unlinked on the way out with the exception re-raised unchanged, and `out_path` itself is
    written only by the closing `os.replace` -- so a failed render neither leaves a partial nor
    truncates whatever was already there. `spawn_render`'s own job as of Task 8, not this
    function's; see that function's docstring for the guarantee, kept there rather than duplicated
    here.
    """
    argv = build_rpict_argv(
        octree,
        position=position,
        direction=direction,
        resolution=resolution,
        quality=quality,
    )
    # Resolved before the spawn, not after: a bad `quality` is `build_rpict_argv`'s `ValueError`
    # above regardless, so nothing here can fail on a request the argv build did not already refuse.
    parameters = dgp_ambient_parameters(quality)
    # `spawn_render` is the allow-list (`rad_engine.render._ALLOWED_PROGRAMS = {"rpict"}`) --
    # this is the one spawn this module makes, and it is no longer a bare `run_to_file` call the
    # allow-list cannot see. `spawn_render` renders into a temp file in the destination directory,
    # unlinks *that* and re-raises on any `BaseException`, and `os.replace`s onto `out_path` only
    # on success (its own docstring), so no cleanup is needed here. **This function has no
    # exception handler and needs none** -- said plainly because the sentence this replaced was
    # circular: `spawn_render`'s handler used to justify itself as "matching
    # `rad_engine.rpict.render_fisheye`'s own handler", which does not exist and never did.
    image = spawn_render(env, argv, out_path=out_path, cwd=cwd, timeout=timeout)
    return RenderedFisheye(
        path=image.path,
        warnings=image.warnings,
        argv=tuple(argv),
        parameters=parameters,
    )
