"""`genBSDF`, `genblinds`, `checkBSDF` -- argv construction, the orientation transform, and the runs.

**Scope** (docs/tool-surface.md#9-bsdf, docs/plan.md#phase-5--bsdf). This module owns every piece of
Radiance knowledge the `bsdf` tool needs: the `genBSDF` command line, the `genblinds` slat
description, the `xform` rotation that drops an assembly into `genBSDF`'s half-space, the
`checkBSDF` invocation and the parse of what it reports. `rad_core.bsdf` owns the XML structure and
the preconditions -- both pure, both testable with no binary installed -- and this module reads the
disk and spawns the processes on its behalf.

**Read `rad_engine.skymatrix` first.** It is the closest sibling: argv built field by field as
`list[str]` with no binary path baked in, `rad_engine.run.run` substituting the resolved binary for
that first token, and every sourced fact carrying the command that verified it. This module matches
its structure.

## The orientation transform is the hardest part of the procedure, and it is the reason this exists

`genblinds` emits slats *"with the long dimension of the blinds running along with the global
Y-dimension"* and *"all coordinates are positive"* -- the depth along X, width along Y, height along
Z (`man genblinds`, verified on this install). `genBSDF` wants something different and
incompatible: width along **X**, height along **Y**, depth along **Z**, the whole assembly in the
**negative Z** half-space with +Z pointing into the room (`man genBSDF`). Nothing warns when the two
disagree; you get a valid XML of a device in the wrong orientation, which is the failure
docs/tool-surface.md calls out as *"a valid-looking XML of the wrong device"*.

The man page's own example composes the two rotations that reconcile them:

    genblinds blind_white blind1 .07 3 1.5 30 40 | xform -rz -90 -rx 90

Measured on this machine on a `.025 x 1.2 x 1.5`, 40-slat, 45-degree blind (recorded in
`tests/fixtures/README.md`):

    genblinds output       X: 0 .. 1.2        Y: 0.0176 .. 0.01875   Z: 0 .. 1.49893
    after -rz -90 -rx 90   X: 0 .. 1.2        Y: -1.49893 .. -0.01875  Z: -0.0176 .. 1.64e-16

Height moved from Z to (negative) Y, depth moved from X to negative Z, and the assembly now sits in
the negative Z half-space. The residual `1.64e-16` of positive Z is the reason
`rad_core.bsdf.check_generate_geometry` carries a scaled tolerance rather than testing `z_max > 0`
-- see that module.

**The azimuth rotation is composed on top, and it is taken from the aperture's computed azimuth
rather than a cardinal name.** `genblinds`' unrotated output faces **west**; the sourced table is
south `-rz 90`, east `-rz 180`, north `-rz 270` (or `-90`)
(specs/radiance-practical-guide.md §genblinds). A facade at 23 degrees is not in that table, and
snapping it to the nearest cardinal would silently model a different building -- the same reasoning
that makes `rad_engine` emit an explicit six-argument `kbin(...)` rather than `kbinS`
(docs/tool-surface.md §6). So the table is expressed as the formula it is a sample of, and the four
cardinals are asserted against it in `tests/test_bsdf_engine.py`.

## Slat count follows the sourced overlap rule

`n = 1.15 * height / slat_width` -- the 15 % overlap rule
(docs/tool-surface.md#9-bsdf, specs/radiance-practical-guide.md). Rounded **up**: a fractional slat
is not a thing, and rounding down opens a gap the rule exists to close.

## What this module does not do

It does not decide *whether* geometry may be generated -- `rad_core.bsdf.check_generate_geometry`
does, and the tool layer calls it **before** anything here spawns `genBSDF`, which is what makes
check 5.5's "asserted by checking no `genBSDF` process was spawned" true by construction. It does
not build the T matrix or run `dctimestep` (Phase 6). It does not convert between bases:
`bsdf2klems` and `bsdf2ttree` are deferred, because the five-phase spec warns `bsdf2klems`
*"might not work well with specular components"* and generating twice is the sourced approach
(docs/plan.md Phase 5, Defers).
"""

from __future__ import annotations

import itertools
import logging
import math
import os
import re
import shutil
import stat
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from rad_core.bsdf import Basis, BoundingBox, BSDFFile, parse_bsdf
from rad_engine.env import RadianceEnv
from rad_engine.octree import (
    refuse_inline_commands_in_files,
    refuse_inline_commands_in_text,
    refuse_shell_unsafe_paths,
)
from rad_engine.run import RadianceError, Result, run, run_to_sink
from rad_engine.safe_io import (
    open_regular_file,
    open_unique_staging_file,
    refuse_existing_non_regular_destination,
    rmtree_logging_errors,
)

__all__ = [
    "MAX_BSDF_BYTES",
    "BSDFEngineError",
    "bounding_box_of",
    "looks_anisotropic",
    "BlindSpec",
    "CheckBSDFReport",
    "GenBSDFSpec",
    "SLAT_OVERLAP_FACTOR",
    "build_genbsdf_argv",
    "build_genblinds_argv",
    "build_xform_argv",
    "check_bsdf",
    "generate_bsdf",
    "generate_blind_geometry",
    "inspect_bsdf",
    "slat_count",
    "commit_staged",
]

#: Ceiling on a BSDF XML, applied to the read **before** the parse. The tensor-tree fixture is 4.4 MB
#: (CONTEXT.md), so 64 MB sits an order of magnitude above any real device description while bounding
#: the allocation `parse_bsdf` makes from it. Not a security boundary on its own --
#: `open_regular_file` already refuses the non-regular files that would stream forever -- but the file
#: still has to be held in memory twice to be parsed, and nothing else bounds that. Measured at 875 MB
#: peak through `parse_bsdf` on a 315 MB file, on an `anyio` worker whose pool is 40.
#:
#: **It lives here, beside the reads, because there are now two of them and Phase 7a adds a third.**
#: It was `rad_mcp.window_group_tool._MAX_BSDF_BYTES` until Phase 7a Task 4, which moved the sealed
#: `tmx` read into `rad_engine.chain.three_phase_target` -- a module that cannot import the protocol
#: layer at all. The first fix threaded the ceiling down as a `max_bsdf_bytes` argument, and the
#: review found what that costs: **the binding at the production call site became unasserted**, so
#: `max_bsdf_bytes=1 << 40` at the one call site left the whole suite green while re-opening the
#: 875 MB peak. Threading it a second time, for the `Cds` term's tensor-tree read, would be the copy
#: ruling R30 exists to forbid.
#:
#: So the constant sits where the reads are and every reader imports **this object**:
#: `window_group_tool` (the `assign_bsdf` validation read) and `chain.three_phase_target` (the sealed
#: `tmx` read). Two ceilings that could drift apart would mean a file `assign_bsdf` accepted and
#: `run_annual` refused, or the reverse; one object makes that unexpressible, and
#: `tests/test_three_phase.py::TestTheSealedBsdfReadIsBounded` asserts the identity rather than the
#: value so it cannot pass by being updated in lockstep with a drift.
#:
#: **`inspect_bsdf` in this module does *not* apply it**, and that gap is reported rather than closed
#: here: it reads `handle.read()` unbounded. Bounding it is a behaviour change to a shipped path and
#: belongs to whoever owns that path, not to the refactor that moved this constant.
MAX_BSDF_BYTES = 64 << 20


_log = logging.getLogger(__name__)

#: Where genBSDF makes its working directory on POSIX: `mkdtemp("/tmp/genBSDF.XXXXXX")`, a literal
#: in `genBSDF.pl` (Radiance 6.1a), which does not read TMPDIR. genBSDF removes it only on its own
#: success path, so every failed, timed-out or stopped run left one: 232 on this machine on
#: 2026-10-02 (Phase 18 design section 1). A module attribute so tests can point it elsewhere.
_GENBSDF_TEMP_PARENT: Path = Path("/tmp")
_GENBSDF_TEMP_PREFIX: Final = "genBSDF."
#: genBSDF writes its arguments here, one per line, before it compiles the device
#: (`savedARGV.txt` in genBSDF.pl): how a directory is told to be this call's (R758).
_GENBSDF_SAVED_ARGV: Final = "savedARGV.txt"

_genbsdf_lock = threading.Lock()
_genbsdf_tokens = itertools.count()
_genbsdf_overlapped: dict[int, bool] = {}
"""Live `generate_bsdf` calls in this process -> whether another overlapped it at any point."""


def _genbsdf_temp_dirs() -> frozenset[str]:
    """Names of the `genBSDF.*` directories under `_GENBSDF_TEMP_PARENT` this user owns.

    `lstat`, so a symlink is never one whatever it points at. Empty on Windows, where genBSDF uses
    `%TMP%` instead, so nothing is removed there.
    """
    if os.name == "nt":  # pragma: no cover - no Windows here
        return frozenset()
    try:
        entries = list(os.scandir(_GENBSDF_TEMP_PARENT))
    except OSError:
        return frozenset()
    uid = os.getuid()
    names: set[str] = set()
    for entry in entries:
        if not entry.name.startswith(_GENBSDF_TEMP_PREFIX):
            continue
        try:
            info = entry.stat(follow_symlinks=False)
        except OSError:
            continue
        if stat.S_ISDIR(info.st_mode) and info.st_uid == uid:
            names.add(entry.name)
    return frozenset(names)


def _is_this_runs(directory: Path, argv: list[str]) -> bool:
    """Whether genBSDF recorded `argv[1:]` as its arguments in `directory`."""
    try:
        with open_regular_file(directory / _GENBSDF_SAVED_ARGV, "rb") as handle:
            recorded = handle.read().decode("utf-8", "surrogateescape").splitlines()
    except OSError:
        return False
    return recorded == list(argv[1:])


def _begin_genbsdf() -> int:
    with _genbsdf_lock:
        token = next(_genbsdf_tokens)
        for other in _genbsdf_overlapped:
            _genbsdf_overlapped[other] = True
        _genbsdf_overlapped[token] = bool(_genbsdf_overlapped)
        return token


def _end_genbsdf(token: int) -> bool:
    """Whether another `generate_bsdf` in this process ran at any point during this one."""
    with _genbsdf_lock:
        return _genbsdf_overlapped.pop(token)


def _remove_this_runs_temp_dirs(
    before: frozenset[str], argv: list[str], *, overlapped: bool
) -> list[Path]:
    """Remove the `genBSDF.*` directories that appeared during a failed run and record its
    arguments (R758); return those that are gone afterwards. Nothing is removed when another
    `generate_bsdf` in this process overlapped the run: two calls with the same arguments write
    the same `savedARGV.txt`, and the other's directory may still be in use.

    Called with `_genbsdf_lock` held and the run's token still registered (R758 final review), so
    no generate can begin between the overlap verdict and the removal."""
    new = sorted(_genbsdf_temp_dirs() - before)
    if not new:
        return []
    if overlapped:
        _log.warning(
            "genBSDF failed while another generate ran in this server; left %s under %s rather "
            "than risk removing the other run's directory", new, _GENBSDF_TEMP_PARENT,
        )
        return []
    removed: list[Path] = []
    for name in new:
        directory = _GENBSDF_TEMP_PARENT / name
        if _is_this_runs(directory, argv):
            rmtree_logging_errors(directory, log=_log)
            if not os.path.lexists(directory):
                removed.append(directory)
        else:
            _log.info("left %s: it does not record this run's arguments", directory)
    return removed


#: genBSDF's `print STDERR "Recover using: $0 -recover $td\n"` (`genBSDF.pl`), printed once the
#: device is set up. The tool hands the error's text to the caller. Not anchored to a line start:
#: `RadianceError`'s message puts `"<argv0> exited <n>: "` before stderr's first line.
_RECOVER_LINE = re.compile(r"Recover using: [^\n]* -recover (?P<dir>\S+)[ \t]*$", re.MULTILINE)


def _retract_recover_lines(error: BaseException, removed: list[Path]) -> None:
    """Replace genBSDF's `Recover using: ... -recover <dir>` line in `error`'s text when `<dir>` is
    one this call removed, so the refusal does not point at a deleted directory (R758 fix round 1).
    A line naming a directory that still exists is kept. It works when the cleanup left that
    directory alone; a directory the removal only partly deleted (an entry it could not remove,
    logged) keeps its line too, and once its `savedARGV.txt` is gone the recovery it offers fails
    (R758 final review)."""
    if not removed or not isinstance(error, RadianceError):
        return
    gone = {str(directory) for directory in removed}

    def retract(match: re.Match[str]) -> str:
        if match.group("dir") not in gone:
            return match.group(0)
        return (
            f"(genBSDF's working directory {match.group('dir')} was removed after this failure, "
            "so it cannot be resumed; run the generate again.)"
        )

    error.stderr = _RECOVER_LINE.sub(retract, error.stderr)
    error.args = tuple(
        _RECOVER_LINE.sub(retract, item) if isinstance(item, str) else item for item in error.args
    )

#: The 15 %-overlap rule's coefficient: `n = 1.15 * height / slat_width`
#: (docs/tool-surface.md#9-bsdf, specs/radiance-practical-guide.md §genblinds).
SLAT_OVERLAP_FACTOR = 1.15

#: `genblinds`' unrotated slats face **west**, so a west-facing aperture needs no azimuth rotation.
#: Every other orientation is measured from it. See the module docstring for why this is a formula
#: rather than the four-row table the guide states it as.
_GENBLINDS_NATIVE_AZIMUTH_DEG = 270.0


class BSDFEngineError(RuntimeError):
    """A `genBSDF`/`genblinds`/`checkBSDF` invocation could not be completed or believed.

    Carries a class-level `code`/`field` in the shape `rad_engine.project.ProjectError` established,
    so the protocol layer shapes one advisory from the exception rather than re-classifying per call
    site. `code` is `None` where the advisory register allocates nothing for the condition -- the
    posture `rad_engine.weather.WeatherFileUnreadableError` documents.
    """

    code: str | None = None
    field: str | None = None


# --- genblinds ------------------------------------------------------------------------------------


@dataclass(frozen=True)
class BlindSpec:
    """A venetian blind, in the terms `genblinds` takes them.

    `azimuth_deg` is the **aperture's** azimuth, degrees east of north, exactly as `window_group`
    computes it -- not a cardinal name. See the module docstring: snapping 23 degrees to the nearest
    cardinal models a different building.
    """

    material: str
    name: str
    depth_m: float
    width_m: float
    height_m: float
    angle_deg: float
    azimuth_deg: float = _GENBLINDS_NATIVE_AZIMUTH_DEG
    slats: int | None = None
    """Explicit slat count, or `None` to derive it from the overlap rule."""

    curvature_m: float | None = None
    """`genblinds -r`/`+r` radius. `+r` (upward, "the usual configuration") when positive."""

    def resolved_slats(self) -> int:
        return self.slats if self.slats is not None else slat_count(self.height_m, self.depth_m)


def slat_count(height_m: float, slat_width_m: float) -> int:
    """`ceil(1.15 * height / slat_width)` -- the sourced 15 %-overlap rule.

    Rounded up: a fractional slat does not exist, and rounding down opens the gap the overlap rule
    exists to close. `slat_width_m` is the blind's **depth** in `genblinds` terms (the slat's own
    across-the-window dimension), which is the number the rule is stated against.
    """
    if height_m <= 0.0 or slat_width_m <= 0.0:
        raise BSDFEngineError(
            f"a slat count needs a positive height and slat width, got height={height_m!r} and "
            f"slat_width={slat_width_m!r}."
        )
    return math.ceil(SLAT_OVERLAP_FACTOR * height_m / slat_width_m)


def build_genblinds_argv(spec: BlindSpec) -> list[str]:
    """`genblinds mat name depth width height nslats angle [ -r|+r rcurv ]`, argv as data.

    Positional order is the man page's and is not rearranged: depth (X), width (Y), height (Z),
    then the slat count and the angle.
    """
    argv = [
        "genblinds",
        spec.material,
        spec.name,
        _number(spec.depth_m),
        _number(spec.width_m),
        _number(spec.height_m),
        str(spec.resolved_slats()),
        _number(spec.angle_deg),
    ]
    if spec.curvature_m is not None:
        # `+r` is curvature upward, "the usual configuration" (`man genblinds`); `-r` is downward.
        # The sign of the argument chooses which flag, and the radius itself is always positive.
        flag = "+r" if spec.curvature_m >= 0.0 else "-r"
        argv += [flag, _number(abs(spec.curvature_m))]
    return argv


def build_xform_argv(azimuth_deg: float) -> list[str]:
    """The `xform` rotation that takes `genblinds` output into `genBSDF`'s frame.

    Two rotations, composed in the man page's own order: `-rz <azimuth>` then `-rx 90`. The example
    is `xform -rz -90 -rx 90`, whose `-rz -90` is the west-facing case -- `genblinds`' native
    orientation -- and whose `-rx 90` is the half-space drop that every orientation needs.

    **`xform` applies its rotations left to right**, so the order here is load-bearing: `-rx 90`
    first would rotate the azimuth about an already-tilted axis and put the slats' long dimension
    somewhere neither man page describes.
    """
    return ["xform", "-rz", _number(_azimuth_rotation_deg(azimuth_deg)), "-rx", "90"]


def _azimuth_rotation_deg(azimuth_deg: float) -> float:
    """Degrees of `-rz` for an aperture at `azimuth_deg` east of north.

    The sourced table (specs/radiance-practical-guide.md §genblinds) is south `-rz 90`, east
    `-rz 180`, north `-rz 270`/`-90`, with west unrotated -- and `xform -rz` rotates
    counter-clockwise about Z. Reading those four rows as samples of one linear relation:

        south (180 east of north) -> 90      east (90)  -> 180
        north (0 or 360)          -> 270     west (270) -> 0 (or 360)

    which is `rotation = 270 - azimuth (mod 360)`. Verified against all four rows in
    `tests/test_bsdf_engine.py`, which asserts the table's literals rather than this formula's
    output, so the formula has to reproduce the source rather than define it.

    Normalised into `[-180, 180]` so a north-facing aperture emits `-rz -90` -- the man page's own
    spelling -- rather than the arithmetically equal `-rz 270`.
    """
    rotation = (_GENBLINDS_NATIVE_AZIMUTH_DEG - azimuth_deg) % 360.0
    return rotation - 360.0 if rotation > 180.0 else rotation


def generate_blind_geometry(
    env: RadianceEnv, spec: BlindSpec, *, out_path: Path, cwd: Path, timeout: float | None = None
) -> tuple[Path, list[str]]:
    """Run `genblinds | xform` and write the oriented `.rad`. Returns the path and both argvs.

    Two spawns rather than a shell pipe, because a shell pipe is a shell: `run` takes argv as a list
    and never a string, and `stdin=` carries the first stage's stdout into the second. This is also
    what keeps stderr from each stage separately attributable -- a `genblinds` warning and an
    `xform` warning are different problems.

    Raises `SceneInlineCommandError` (R754) before `genblinds` spawns when the material or name
    carries a `!` line ruling R33 refuses, and before `xform` spawns when `genblinds`' output does.
    """
    # Ruling R754, fix round 1: `genblinds` echoes `spec.material` and `spec.name` into its stdout,
    # which `xform` reads as scene text and whose `!` lines it runs through /bin/sh. Measured:
    # `genblinds "$(printf 'm\n!touch MARK2\n#')" b .07 1 1 3 30 | xform -rz 0 -rx 90` created
    # MARK2. The two strings are scanned before `genblinds` spawns, and its output before `xform`
    # does, in `xform`'s own `cwd` and RAYPATH (R753). The labels are anchored at that `cwd`, because
    # a relative label resolved against the server's process cwd (R759 fix round 1).
    root = Path(cwd)
    raypath = env.raypath_dirs(root)
    refuse_inline_commands_in_text(
        f"{spec.material}\n{spec.name}\n", label=root / "genblinds material and name", cwd=root,
        raypath=raypath,
    )
    blinds_argv = build_genblinds_argv(spec)
    slats = run(env, blinds_argv, cwd=cwd, timeout=timeout)

    refuse_inline_commands_in_text(
        slats.stdout, label=root / "genblinds output", cwd=root, raypath=raypath
    )
    xform_argv = build_xform_argv(spec.azimuth_deg)
    oriented = run(env, xform_argv, cwd=cwd, stdin=slats.stdout, timeout=timeout)

    # `open_regular_file` for the same reason every other write in this package uses it: ruling
    # R48's one vetted primitive, refusing a symlink at the final component and anything that is not
    # a regular file on the **opened descriptor**. Containment of the parent chain is the caller's,
    # as `run_to_file`'s docstring states for the same split.
    # `"wb"`, not `"w"`: the primitive is typed `Literal["rb", "wb", "ab"]` and derives `O_TRUNC`
    # from that exact spelling, so `"w"` would open `O_WRONLY | O_CREAT` **without** truncating and
    # leave the tail of a longer previous file behind the new geometry. See `inspect_bsdf`.
    with open_regular_file(out_path, "wb") as sink:
        sink.write(oriented.stdout.encode())
    return out_path, [*blinds_argv, "|", *xform_argv]


# --- genBSDF --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class GenBSDFSpec:
    """One `genBSDF` run.

    `samples` and `resolution` are not defaulted here: the floor is basis-dependent and
    `rad_core.bsdf.sample_floor` owns it, so a default invented at this layer would be a second,
    disagreeing rule. The tool layer resolves them and passes them in.
    """

    basis: Basis
    geometry: tuple[Path, ...]
    samples: int | None = None
    resolution: int | None = None
    both_sides: bool = True
    colour: bool = False
    processes: int | None = None
    dim: tuple[float, float, float, float, float, float] | None = None
    reciprocity_averaging: bool = True


def build_genbsdf_argv(spec: GenBSDFSpec) -> list[str]:
    """`genBSDF` argv, field by field, argv as data.

    Flag order follows the man page's synopsis so a command line printed back to a caller reads the
    way the documentation does. Every flag is included only when it differs from `genBSDF`'s own
    default, **except** the ones whose default this server deliberately does not inherit:

    - `+forward` is emitted for `both_sides`, because the default computes backward components only
      and a file missing the forward ones has absent rather than zero exterior-side data. `+backward`
      is emitted alongside it explicitly: the man page says `+forward` *"may be given"* in addition,
      and stating both makes the command line say what it computes rather than relying on a default
      that would silently change the file if it ever moved.
    - `-t3`/`-t4` carry the resolution the caller chose; a tensor basis with no resolution is a
      caller error the tool layer refuses before reaching here.
    """
    argv = ["genBSDF"]

    if spec.processes is not None:
        argv += ["-n", str(spec.processes)]
    if spec.samples is not None:
        argv += ["-c", str(spec.samples)]

    if spec.basis is Basis.TENSOR4:
        argv += ["-t4", str(_require_resolution(spec))]
    elif spec.basis is Basis.TENSOR3:
        argv += ["-t3", str(_require_resolution(spec))]

    # `+C` is tristimulus colour, `-C` reduces to greyscale and is genBSDF's default. Emitted
    # explicitly either way: `BSDF_GREYSCALE` is an advisory this server raises, and a command line
    # that does not state the choice cannot be audited against the advisory.
    argv.append("+C" if spec.colour else "-C")

    if not spec.reciprocity_averaging:
        # `-a` turns reciprocity averaging off. Normally on, and "for some systems, reciprocity
        # averaging can actually make the output worse" -- so this is opt-out, never a default.
        argv.append("-a")

    if spec.both_sides:
        argv += ["+forward", "+backward"]

    if spec.dim is not None:
        argv += ["-dim", *(_number(value) for value in spec.dim)]

    argv += [str(path) for path in spec.geometry]
    return argv


def _require_resolution(spec: GenBSDFSpec) -> int:
    if spec.resolution is None:
        raise BSDFEngineError(
            f"basis {spec.basis.value!r} needs a resolution: -t3/-t4 take the log base 2 of the "
            "sampling resolution in each dimension, and genBSDF has no default for it."
        )
    return spec.resolution


def generate_bsdf(
    env: RadianceEnv,
    spec: GenBSDFSpec,
    *,
    out_path: Path,
    cwd: Path,
    timeout: float | None = None,
) -> tuple[Path, Result]:
    """Run `genBSDF` into a **staging file** beside `out_path`; return `(staging, result)`.

    `out_path` is not opened (ruling R751, design decision 14): a regenerate that fails or times
    out leaves an existing XML byte-identical. The caller checks the staging file and then moves it
    into place with `commit_staged`, or removes it. Anything not a regular file already at
    `out_path` (a symlink, FIFO or directory, say: `refuse_existing_non_regular_destination` is an
    `lstat` with `S_ISREG`) is refused here, before genBSDF spawns, as `run_to_file`'s `open_regular_file` refused
    it before R751. The staging file comes from `safe_io.open_unique_staging_file` (R668): two
    `generate` calls naming the same XML can run at once, and S1 forbids opening a staging name with
    `"wb"`. On any failure or timeout this call sees, the staging file is removed; a server
    stopped mid-run leaves it behind.

    stdout to a file, stderr kept apart: the XML can be megabytes, and genBSDF's warnings must stay
    readable. Never `2>&1` -- that corrupts the artifact, the trap CONTEXT.md records.

    `timeout` is the tool layer's `inline_timeout_s` (`rad_mcp.bsdf_tool.BSDF_INLINE_TIMEOUT_S`,
    1800 s by default, set from design M8: genBSDF run directly on the argv the tool builds took
    400.8 s for Klems and 684.3 s for tensor4 at resolution 4, both with one process).

    Raises `SceneInlineCommandError` (R754) before anything is written when the geometry or its
    include chain carries a `!` line ruling R33 refuses.

    **genBSDF's `/tmp` directory** (R758): after a non-zero exit, a timeout or a stop, each
    `genBSDF.*` directory under `/tmp` that appeared during this call, is owned by this user, is
    not a symlink, and records this call's arguments in `savedARGV.txt` is removed. Nothing is
    removed after a success (genBSDF removes its own), nor when another call to this function in
    this process overlapped this one. An entry the removal cannot delete is logged with its path.
    When a directory is removed, genBSDF's `Recover using: ... -recover <dir>` line naming it is
    replaced in the raised error's text, which the tool returns to the caller.
    """
    # Ruling R754: genBSDF runs `xform -e` over the geometry (`genBSDF.pl`), which runs every `!`
    # line through `/bin/sh`. Scanned here, before anything is written and immediately before this
    # spawn, in genBSDF's working directory and RAYPATH (R753). `bounding_box_of` scanned the same
    # files for its own spawn; this is the scan for this one, as `build_octree`'s is for `oconv`.
    # The path check first: genBSDF also puts each geometry path into /bin/sh command lines
    # unquoted (`system "xform -e @ARGV > ..."`, `-C "Created by: genBSDF @savedARGV"`), so a file
    # *named* `x;touch MARK;.rad` runs `touch MARK` whatever its content (measured, R754).
    refuse_shell_unsafe_paths(spec.geometry, field="geometry", program="genBSDF")
    refuse_inline_commands_in_files(
        [str(path) for path in spec.geometry], cwd=Path(cwd), raypath=env.raypath_dirs(cwd)
    )
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    refuse_existing_non_regular_destination(out_path)
    argv = build_genbsdf_argv(spec)
    staging, handle = open_unique_staging_file(out_path)
    # Both inside the `try`, so the token is popped however this call ends: a leaked token would
    # mark every later call overlapped and stop the cleanup for the server's lifetime.
    token: int | None = None
    before: frozenset[str] | None = None
    try:
        with handle:
            token = _begin_genbsdf()
            before = _genbsdf_temp_dirs()
            result = run_to_sink(env, argv, stdout=handle, stdin="", cwd=cwd, timeout=timeout)
    except BaseException as error:
        staging.unlink(missing_ok=True)
        if token is not None and before is not None:
            # R758: genBSDF removes its `/tmp/genBSDF.*` directory only when it succeeds. Never
            # allowed to replace the run's own exception. Final review: the token is held, under
            # `_genbsdf_lock`, across the listing and the removal and popped only after them, so a
            # generate that begins meanwhile either marked this one overlapped already or waits,
            # and cannot make a directory this cleanup then takes for its own.
            with _genbsdf_lock:
                try:
                    removed = _remove_this_runs_temp_dirs(
                        before, argv, overlapped=_genbsdf_overlapped[token]
                    )
                    _retract_recover_lines(error, removed)
                except Exception:  # noqa: BLE001
                    _log.exception("removing genBSDF's temporary directory failed")
                finally:
                    _genbsdf_overlapped.pop(token, None)
            token = None
        raise
    finally:
        if token is not None:
            _end_genbsdf(token)
    return staging, result


def commit_staged(staging: Path, out_path: Path) -> Path:
    """Move a staging file onto `out_path` with `os.replace` and return `out_path`.

    `rename(2)` replaces a file or a symlink at `out_path` without dereferencing it (a directory
    there makes `os.replace` raise `IsADirectoryError`), and a reader of `out_path` sees the whole
    old file or the whole new one, never a partial one.
    """
    os.replace(staging, out_path)
    return Path(out_path)


# --- reading a file back --------------------------------------------------------------------------


def inspect_bsdf(path: Path) -> BSDFFile:
    """Read a BSDF XML from disk and hand its text to the pure parser.

    The whole of this function is the layer split: the read happens here, the structure is decided
    in `rad_core.bsdf`, which never sees a path. Opened through `safe_io.open_regular_file` -- R48's
    primitive covers reads as well as writes, and a caller-supplied path is exactly the untrusted
    input it exists for.

    **`"rb"`, and the mode is not a cosmetic choice.** `open_regular_file` is typed
    `Literal["rb", "wb", "ab"]` and computes its flags by testing against those three spellings, so
    a mode outside them -- `"r"` -- falls through to `O_WRONLY | O_CREAT` and the read fails with
    `EBADF` on a write-only descriptor. Decoded here rather than opened in text mode because the
    primitive returns `IO[bytes]` by contract; a BSDF XML declares its own encoding in its
    prologue, and `genBSDF` writes ASCII.

    **Bounded by `MAX_BSDF_BYTES`, the same object the other two reads of a caller-supplied BSDF
    already use** (`rad_mcp.window_group_tool._assign_bsdf` and `rad_engine.chain`'s sealed-T read) --
    the whole-branch review's IMPORTANT 5. The read was `handle.read()` with no ceiling while the
    constant sat two modules away for exactly this, and **both** callers
    (`rad_mcp.bsdf_tool`'s `inspect` and `validate` actions, and its post-generation re-read) take a
    path the caller chose. `open_regular_file` already closes the streaming case -- a FIFO or a
    character device cannot feed this forever -- so what was left is an ordinary regular file of
    arbitrary size held in memory three times over: once as bytes, once decoded, once through
    `parse_bsdf`. Phase 6b measured that at 875 MB peak on a 315 MB file, on an `anyio` worker whose
    pool is 40. One constant, imported rather than restated, so the three reads cannot drift (R30).

    A file over the ceiling raises `BSDFEngineError` -- the same class the unreadable case raises, and
    the one `rad_mcp.bsdf_tool` already shapes into a refusal.
    """
    try:
        with open_regular_file(path, "rb") as handle:
            # `MAX_BSDF_BYTES + 1`, so "over the ceiling" is decided from the length actually read
            # rather than from a `stat` that could disagree with the bytes that arrive.
            raw_bytes = handle.read(MAX_BSDF_BYTES + 1)
    except OSError as exc:
        raise BSDFEngineError(
            f"the BSDF file {path} could not be read: {exc}."
        ) from exc
    if len(raw_bytes) > MAX_BSDF_BYTES:
        raise BSDFEngineError(
            f"the BSDF file {path} is larger than {MAX_BSDF_BYTES >> 20} MB. A BSDF XML describes "
            "one device's optical behaviour; a file this size is not one, and reading it would hold "
            "it in memory several times over. Supply the device's own XML."
        )
    return parse_bsdf(raw_bytes.decode("utf-8", errors="replace"))


@dataclass(frozen=True)
class CheckBSDFReport:
    """What `checkBSDF` says about a file, as data.

    Deliberately a small set of fields: this exists so check 5.3 can assert that `checkBSDF`'s
    component set agrees with what `rad_core.bsdf` parsed without running a binary. It is a
    cross-check on the pure layer, not a second source of truth for the tool's result.
    """

    ok: bool
    basis_type: str | None
    has_geometry: bool | None
    colour: bool | None
    components: tuple[str, ...]
    stdout: str


#: `checkBSDF` writes `Type: Klems_Full`, `Has Geometry: 1`, `Color: 0` -- one `Label: value` per
#: line. Captured live on the generated fixture (`tests/fixtures/README.md`).
_CHECK_FIELD = re.compile(r"^(?P<label>[A-Za-z][A-Za-z .>-]*?):\s*(?P<value>.+?)\s*$", re.MULTILINE)

#: The component rows of `checkBSDF`'s first table, whose names are its own spelling.
_CHECK_COMPONENTS = ("Interior Refl", "Exterior Refl", "Int->Ext Trans", "Ext->Int Trans")


def check_bsdf(
    env: RadianceEnv, path: Path, *, cwd: Path, timeout: float | None = None
) -> CheckBSDFReport:
    """Run `checkBSDF` and parse what it reports.

    `check=False`: a non-zero exit is a *finding about the file*, which is this function's return
    value, not an exception. A file `checkBSDF` rejects is still a file the caller needs told about
    -- the same posture `weather` takes toward an EPW with problems.
    """
    result = run(env, ["checkBSDF", str(path)], cwd=cwd, timeout=timeout, check=False)
    fields = {
        match.group("label").strip(): match.group("value").strip()
        for match in _CHECK_FIELD.finditer(result.stdout)
    }
    present = tuple(
        name
        for name in _CHECK_COMPONENTS
        if any(
            line.startswith(name) and not _is_empty_component(line)
            for line in _distribution_table(result.stdout)
        )
    )
    return CheckBSDFReport(
        ok=result.returncode == 0,
        basis_type=fields.get("Type"),
        has_geometry=_flag(fields.get("Has Geometry")),
        colour=_flag(fields.get("Color")),
        components=present,
        stdout=result.stdout,
    )


def _distribution_table(stdout: str) -> list[str]:
    """The rows of `checkBSDF`'s **first** table only -- the Lambertian/max-direction one.

    `checkBSDF` prints two tables, both with a `Component` header and overlapping row names, and
    scanning the whole output conflates them. Measured on the generated fixture (recorded in
    `tests/fixtures/README.md`):

        Component       Lambertian XYZ (%)      Max. Tot. Dir   Min. Angle
        Exterior Refl    0.0  0.0  0.0             0.0%         180 deg      <- absent
        ...
        Component       Reciprocity Error (min avg max %)
        Exterior Refl      0       0       0                                 <- no angle column

    The second table's `Exterior Refl` row carries no `deg` column, so the 180-degree
    absent-component sentinel is simply not there and the row reads as a present component. That is
    how a backward-only file came to report `Exterior Refl` as present -- an empty hemisphere in the
    reported component set. `Ext->Int Trans` escaped the same fate only because the second table
    spells it `Transmission`, which is luck rather than logic.

    Delimited by the second `Component` header rather than by a row count, because the row count is
    what varies between files while the header is `checkBSDF`'s own structure.
    """
    rows: list[str] = []
    seen_header = False
    for line in stdout.splitlines():
        if line.startswith("Component"):
            if seen_header:
                break
            seen_header = True
            continue
        if seen_header:
            rows.append(line)
    return rows


def _is_empty_component(line: str) -> bool:
    """Whether a `checkBSDF` component row describes an absent component rather than a measured one.

    `checkBSDF` prints every row whether or not the file carries that component, and an absent one
    is `0.0 0.0 0.0 / 0.0% / 180 deg` -- measured on the backward-only Klems fixture, where
    `Exterior Refl` and `Ext->Int Trans` both read that way (`tests/fixtures/README.md`). The
    discriminator is the **minimum angle of 180 degrees**: a real component has a finite minimum
    scattering angle, and 180 is the sentinel for "nothing here". Keyed on that rather than on the
    percentage, because a genuinely near-black component could round to 0.0 % while still being
    present.

    **Compared as a number, not as the string `"180"`.** An exact string match is the kind of parse
    that works on the one output it was written against and fails silently everywhere else: a build
    printing `180.0 deg`, or padding the column differently, would classify an absent component as
    present and put an empty hemisphere into the reported component set. The angle is the token
    before the `deg` unit, so it is located by that unit rather than by position.
    """
    tokens = line.split()
    for index, token in enumerate(tokens):
        if token.rstrip(".,").lower() != "deg" or index == 0:
            continue
        try:
            return math.isclose(float(tokens[index - 1]), 180.0, abs_tol=1e-6)
        except ValueError:
            return False
    return False


def _flag(value: str | None) -> bool | None:
    """`checkBSDF`'s `1`/`0` flags. `None` when the field was absent, which is not `False` --
    absent means this build of `checkBSDF` did not report it."""
    if value is None:
        return None
    return value.strip() not in {"0", "", "no", "No"}


def _number(value: float) -> str:
    """A float as Radiance argv sees it: `%g`, so `0.025` stays `0.025` and `90.0` becomes `90`.

    Trailing-zero forms are avoided because argv is published to the caller and `-rz 90` is the
    spelling the man page uses; `-rz 90.0` is the same rotation but not the same audit trail.
    """
    return f"{value:g}"


def bounding_box_of(
    env: RadianceEnv, geometry: tuple[Path, ...], *, timeout: float | None = None
) -> BoundingBox:
    """The geometry's bounding box, from `getbbox -h`. Reads the geometry; traces nothing.

    **`getbbox` on `.rad` source files, never on an octree.** `rad_engine.octree` records why the
    distinction matters: on a non-frozen octree `getbbox` tries to re-read the source scene and
    fails in ways that read as a diagnosis of the octree. Here the inputs *are* the `.rad` files, so
    the hazard does not arise -- but the reason it does not is worth stating, because handing this
    function an octree would reintroduce it.

    `-h` suppresses the header so stdout is six numbers. Radiance writes them in the order
    `xmin xmax ymin ymax zmin zmax`.

    `cwd` is the first geometry file's directory, so any `!xform` include inside it resolves the way
    it would for `oconv` -- a bounding box computed with unresolved includes is the box of an
    emptier scene, which is the trap CONTEXT.md records for octrees in a different guise.

    `timeout` bounds the `getbbox` spawn (`bsdf generate` passes its `inline_timeout_s`, R751).

    Raises `SceneInlineCommandError` (R754) before the spawn when the geometry or a file its
    `!xform` chain can reach carries a `!` line ruling R33 refuses.
    """
    if not geometry:
        raise BSDFEngineError(
            "a bounding box needs at least one geometry file; none was given."
        )
    spawn_cwd = geometry[0].parent
    # Ruling R754, fix round 1: genBSDF interpolates these same paths into /bin/sh unquoted
    # (`genBSDF.pl`: `system "xform -e @ARGV > ..."`), and `bsdf generate` calls this function
    # first, so the path check here is the one that precedes every spawn of the call.
    refuse_shell_unsafe_paths(geometry, field="geometry", program="genBSDF")
    # Ruling R754 (R33's scan; Phase 18 design F1): `getbbox` reads the geometry through Radiance's
    # object reader, which runs every `!` line through `/bin/sh` -- measured: a contained geometry
    # file beginning `!touch <marker>` created the marker through `bsdf generate`. Scanned here,
    # immediately before the spawn, in this spawn's own `cwd` and RAYPATH (R753).
    refuse_inline_commands_in_files(
        [str(path) for path in geometry], cwd=spawn_cwd, raypath=env.raypath_dirs(spawn_cwd)
    )
    argv = ["getbbox", "-h", *(str(path) for path in geometry)]
    result = run(env, argv, cwd=spawn_cwd, timeout=timeout)
    fields = result.stdout.split()
    if len(fields) < 6:
        raise BSDFEngineError(
            f"getbbox reported {len(fields)} value(s) rather than the six of a bounding box, so the "
            f"geometry's extent could not be established. Its output was {result.stdout!r} and its "
            f"warnings were {result.warnings!r}."
        )
    try:
        values = [float(field) for field in fields[:6]]
    except ValueError as exc:
        raise BSDFEngineError(
            f"getbbox reported {fields[:6]!r}, which is not six numbers, so the geometry's extent "
            "could not be established."
        ) from exc
    return BoundingBox(*values)


#: How many distinct parallel surfaces along one axis make geometry "anisotropic" for the `-t3` rule.
#: A venetian blind is many slats stacked along one axis; a flat sheet or a radially symmetric
#: perforated panel is not. Three is the smallest count that cannot be a single surface plus its two
#: edges, which is what a plain glazing pane looks like once its sill is modelled.
_ANISOTROPY_SURFACE_THRESHOLD = 3


def looks_anisotropic(geometry: tuple[Path, ...]) -> bool:
    """Whether the geometry looks anisotropic, for the `-t3` refusal only.

    **Stated rather than guessed**, because the alternative is a heuristic nobody can predict
    (`rad_core.bsdf`, "Anisotropy is a refusal"): a directional count of distinct parallel polygons.
    Venetian slats are many polygons repeating along one axis and in none of the others, which is
    exactly what radial symmetry about Z is not.

    Deliberately a **shallow, syntactic** read of the `.rad` text: it counts `polygon` primitives and
    the distinct planes they lie in. It is not a geometry engine and does not pretend to be -- the
    consequence of a false positive is a refusal the caller can override with `isotropic=True`, and
    the consequence of a false negative is the rank-3 tree the man page warns about. So it is tuned to
    catch the case the guide names (venetian slats) and to say so loudly when it does, rather than to
    be right about every device.

    Counted per file and summed: a blind assembly is conventionally one `.rad` for the slats and
    another for the glazing, and the slats are what the count is about.

    **The stacking axis is not assumed.** Distinct planes are counted along all three axes
    independently and the largest count decides, because which axis the slats stack along depends on
    whether the orientation transform has been applied yet: `genblinds` stacks along **Z** (its
    "height"), and after `xform -rz -90 -rx 90` the same slats stack along **Y**. Measured on the
    venetian fixture, whose 40 slats give 40 distinct first-vertex Y values and exactly one distinct
    X and Z (`tests/fixtures/README.md`). Keying on one axis got this backwards and reported 40
    venetian slats as isotropic -- a false negative, which is the direction that produces the wrong
    answer silently rather than a refusal the caller can override.
    """
    xs: set[float] = set()
    ys: set[float] = set()
    zs: set[float] = set()
    polygons = 0
    for path in geometry:
        try:
            with open_regular_file(path, "rb") as handle:
                text = handle.read().decode("utf-8", errors="replace")
        except OSError as exc:
            raise BSDFEngineError(
                f"the geometry file {path} could not be read: {exc}."
            ) from exc
        tokens = text.split()
        for index, token in enumerate(tokens):
            if token != "polygon":
                continue
            polygons += 1
            # A polygon primitive is `mod polygon name 0 0 N x1 y1 z1 ...` -- so from the `polygon`
            # token: +1 name, +2 and +3 the two zero argument counts, +4 the vertex-float count, and
            # the first vertex's x/y/z at +5/+6/+7. Rounded, because two slats of one stack differ in
            # the last bits after two rotations.
            try:
                x, y, z = (float(tokens[index + offset]) for offset in (5, 6, 7))
            except (IndexError, ValueError):
                continue
            xs.add(round(x, 4))
            ys.add(round(y, 4))
            zs.add(round(z, 4))
    # **A stack varies along exactly ONE axis and stays put in the other two.** Counting distinct
    # coordinates per axis and taking the largest is not the same test, and it fails check 5.6's
    # accepted case: a circular perforated panel's holes vary in *both* X and Y within one plane, so
    # a max-count rule reports 12 "planes" and refuses geometry the man page's radial-symmetry
    # exception is precisely about. Requiring the other two axes to be near-constant is what
    # distinguishes 40 parallel slats from 12 holes arranged in a circle -- measured on both
    # (`tests/fixtures/README.md`).
    counts = (len(xs), len(ys), len(zs))
    varying = [count for count in counts if count >= _ANISOTROPY_SURFACE_THRESHOLD]
    return polygons >= _ANISOTROPY_SURFACE_THRESHOLD and len(varying) == 1


def genbsdf_available(env: RadianceEnv) -> bool:
    """Whether `genBSDF` can be run at all.

    `genBSDF` is a Perl script in every Radiance distribution this project has seen, so its presence
    on the PATH does not guarantee a working interpreter. Checked as "the binary resolves", which is
    the same claim `radiance_status` makes about every other tool -- the tool layer does **not** hide
    the `bsdf` tool when this is false (docs/tool-surface.md forbids exactly that: "hiding `bsdf`
    when `genBSDF` is missing"), it reports the capability.
    """
    try:
        return env.path("genBSDF") is not None
    except Exception:  # pragma: no cover - env.path's own failure mode is its business
        return shutil.which("genBSDF") is not None
