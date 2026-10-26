"""`ies2rad`, run the one way rad_mcp runs it, and rad_mcp's own photometric correction file.

Spec: docs/superpowers/specs/2026-09-13-phase-11a-luminaire-placement-design.md §5.1, R469, R472,
R473, R486-R488. Every conversion is screened by `rad_core.photometry_file.screen_ies` first; nothing
this module spawns ever sees a refused file.
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass, replace
from importlib import resources
from pathlib import Path
from typing import Final, Iterable, Literal

from rad_core.photometry import to_lux
from rad_core.photometry_file import (
    TYPES_PREFIX,
    ConvertedType,
    IesHeader,
    IesUnsupportedError,
    c0_lobe_ies,
    isotropic_point_ies,
    read_converted,
    screen_ies,
)
from rad_core.quality import AMBIENT_PRESETS

from .cores import worker_cores
from .env import RadianceEnv
from .octree import EmptyOctreeError, _decode_scene_bytes, build_octree, refuse_inline_commands_in_text
from .project import ProvenanceError, file_digest, fsync_directory
from .rtrace import RtraceRequest, run_rtrace
from .run import RadianceError, run
from .safe_io import open_regular_file, open_staging_file, refuse_existing_non_regular_destination

__all__ = [
    "SOURCE_CAL_BYTES",
    "SOURCE_CAL_DIGEST",
    "ConvertedIes",
    "IesConversionError",
    "PhotometryUnverifiedError",
    "SourceCalState",
    "convert_ies",
    "ies2rad_argv",
    "modifier_collisions",
    "source_cal_state",
    "verify_photometry",
    "write_source_cal",
]

# The one exception to "every filesystem open in rad_engine goes through safe_io.open_regular_file"
# (Task 8's layering rule): this is the `importlib.resources` read of the packaged file, not an open
# of a caller- or project-controlled path.
SOURCE_CAL_BYTES: Final[bytes] = resources.files("rad_engine").joinpath("data/source.cal").read_bytes()
#: Pinned by tests/test_ies_engine.py; measured by Task 8 Step 2.
SOURCE_CAL_DIGEST: Final[str] = "sha256:b2332d9a62c46c2f8cc68dd564aa44286b4e58cadad787479a2b5e0222b3deb5"


class IesConversionError(RuntimeError):
    code = "IES_CONVERSION_FAILED"
    severity = "error"
    field = "ies"


@dataclass(frozen=True)
class ConvertedIes:
    header: IesHeader
    converted: ConvertedType
    argv: list[str]
    rad_bytes: bytes
    data_files: dict[str, bytes]


def _fmt(value: float) -> str:
    return format(value, ".10g")


def ies2rad_argv(*, name: str, light_loss_factor: float | None, colour: tuple[float, float, float] | None) -> list[str]:
    argv = ["ies2rad", "-dm", "-t", "default"]
    if light_loss_factor is not None:
        argv += ["-m", _fmt(light_loss_factor)]
    if colour is not None:
        argv += ["-c", *(_fmt(c) for c in colour)]
    return [*argv, "-l", ".", "-p", TYPES_PREFIX, "-o", name, f"{TYPES_PREFIX}/{name}.ies"]


def _write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open_regular_file(path, "wb") as handle:
        handle.write(data)


def _read(path: Path) -> bytes:
    with open_regular_file(path, "rb") as handle:
        return handle.read()


def convert_ies(env: RadianceEnv, data: bytes, *, name: str, light_loss_factor: float | None,
                colour: tuple[float, float, float] | None, scratch: Path, timeout: float) -> ConvertedIes:
    """Screen, install and convert one `.ies` with `ies2rad`; `IesHeaderError` or
    `IesUnsupportedError` from the screen, `IesUnsupportedError` or `IesConversionError` from the
    conversion, otherwise.

    Any `makeshape:` line in `ies2rad`'s stderr classifies the failure as unsupported
    (`IES_UNSUPPORTED`), even when other warnings accompany it: the substituted shape is the decisive
    fault either way, and both codes refuse."""
    header = screen_ies(data)  # R490/R491 and every header refusal, before anything is written or spawned
    root = scratch / "ies"
    _write(root / TYPES_PREFIX / f"{name}.ies", data)
    argv = ies2rad_argv(name=name, light_loss_factor=light_loss_factor, colour=colour)
    result = run(env, argv, cwd=root, timeout=timeout, check=False)
    stderr = result.stderr.strip()
    if "makeshape:" in stderr:
        raise IesUnsupportedError(f"ies2rad substituted the luminous shape: {stderr}")
    rad_path = root / TYPES_PREFIX / f"{name}.rad"
    if result.returncode != 0 or stderr or not rad_path.is_file():
        raise IesConversionError(f"ies2rad exit {result.returncode}: {stderr or 'no output file'}")
    rad_bytes = _read(rad_path)
    text = _decode_scene_bytes(rad_bytes)
    refuse_inline_commands_in_text(text, label=rad_path, cwd=root)
    converted = read_converted(text, prefix=TYPES_PREFIX)
    if converted.shape_class == "cylinder":
        raise IesUnsupportedError("the converted luminaire is a cylinder, which is not supported; a point (a sphere), rectangle, disk or box luminous shape is.")
    compile_scene = root / "_compile.rad"
    _write(compile_scene, f"!xform {TYPES_PREFIX}/{name}.rad\n".encode("ascii"))
    try:
        build_octree(env, [compile_scene.name], out_path=root / "_compile.oct", cwd=root, timeout=timeout)
    except (RadianceError, EmptyOctreeError) as exc:
        raise IesConversionError(f"the converted luminaire does not compile: {exc}") from exc
    data_files = {relative: _read(root / relative) for relative in converted.data_files}
    return ConvertedIes(header=header, converted=converted, argv=argv, rad_bytes=rad_bytes, data_files=data_files)


#: R486's probe geometry. Analytic, not sourced: a 1000 cd point at 2 m gives an exactly computable
#: I/d^2 of 250 lux, and the lobe probe's expected values (module docstring of `verify_photometry`)
#: follow from the same two numbers and simple trigonometry -- neither is a substituted default, so
#: neither gets an "Unsourced values" row (ruling R514).
PROBE_DISTANCE_M: Final[float] = 2.0
PROBE_CANDELA: Final[float] = 1000.0

#: R514: unlike the two geometry constants above, this *is* a substituted default -- nothing sources
#: a 1 % pass band for this probe. See docs/tool-surface.md's "Unsourced values" table.
PROBE_TOLERANCE: Final[float] = 0.01

#: `(package_digest, bin_dir, lib_dir, version)` tuples R486 has already verified in this process.
#: `verify_photometry` is meant to run once per project per install, not once per lit run -- a probe
#: this cheap is still two `ies2rad` conversions, two `oconv`s and two `rtrace`s, and nothing about
#: the answer can change without one of the four key fields changing with it.
_VERIFIED: set[tuple[str, str, str, str]] = set()


class PhotometryUnverifiedError(RuntimeError):
    """R486/R487: this install's photometric correction did not reproduce I/d^2 for the probe, or
    the project's root `source.cal` is not one this server wrote."""

    code = "LUMINAIRE_PHOTOMETRY_UNVERIFIED"
    severity = "error"
    field = "project"


def _probe(env: RadianceEnv, root: Path, name: str, data: bytes, points: str, timeout: float) -> list[float]:
    """Convert one synthetic IES `data` to a `sphere`, place it `PROBE_DISTANCE_M` above the origin,
    and read `points` (a `.pts`-shaped block, `rtrace -I` order) back as lux. Raises
    `PhotometryUnverifiedError` if the probe's own `rtrace` wrote anything to stderr -- a probe that
    warns has not measured what it claims to."""
    convert_ies(env, data, name=name, light_loss_factor=None, colour=None, scratch=root, timeout=timeout)
    ies_root = root / "ies"
    scene = ies_root / f"_{name}_scene.rad"
    _write(scene, f"!xform -t 0 0 {PROBE_DISTANCE_M:g} {TYPES_PREFIX}/{name}.rad\n".encode("ascii"))
    octree = build_octree(env, [scene.name], out_path=ies_root / f"_{name}.oct", cwd=ies_root, timeout=timeout)
    parameters = replace(AMBIENT_PRESETS["draft"], ambient_bounces=0, direct_threshold=0.0, direct_certainty=1.0)
    rgb, result = run_rtrace(
        env,
        RtraceRequest(octree=octree.path, parameters=parameters, processes=worker_cores()),
        points_text=points, cwd=ies_root, timeout=timeout,
    )
    if result.warnings:
        raise PhotometryUnverifiedError(f"the photometric probe wrote stderr: {result.warnings}")
    return [to_lux(value) for value in rgb]


def verify_photometry(env: RadianceEnv, *, scratch: Path, timeout: float) -> None:
    """R486: rad_mcp's own `source.cal`, on this install, reproduces I/d^2 for a synthetic point
    source and a synthetic C0 lobe -- the correction the packaged calibration file supplies, proven
    against this specific Radiance binary and library rather than assumed portable across installs.

    Writes the packaged `SOURCE_CAL_BYTES` into its own scratch root and never touches a project
    directory: `RAYPATH` leads with `.` (`RadianceEnv.environ`), so a probe run with `cwd=<scratch
    root>/ies` always reads its own copy, never a project's root `source.cal` -- the two file states
    `source_cal_state`/`write_source_cal` manage below are a different, later concern (R487).

    Cached per `(SOURCE_CAL_DIGEST, bin_dir, lib_dir, version)` in `_VERIFIED`: the same packaged
    file against the same install can only ever measure the same thing twice.
    """
    key = (SOURCE_CAL_DIGEST, str(env.bin_dir), str(env.lib_dir), env.version or "")
    if key in _VERIFIED:
        return
    root = scratch / "probe"
    _write(root / "ies" / "source.cal", SOURCE_CAL_BYTES)
    [nadir] = _probe(env, root, "probe_point", isotropic_point_ies(PROBE_CANDELA), "0 0 0 0 0 1\n", timeout)
    expected = PROBE_CANDELA / PROBE_DISTANCE_M ** 2
    if not math.isclose(nadir, expected, rel_tol=PROBE_TOLERANCE):
        raise PhotometryUnverifiedError(
            f"a {PROBE_CANDELA:g} cd point {PROBE_DISTANCE_M:g} m above a sensor read {nadir:.6g} lux, not "
            f"{expected:g} +/- {PROBE_TOLERANCE:.0%}; this install's photometric correction is not what rad_mcp expects."
        )
    d = PROBE_DISTANCE_M
    # Four sensors in a ring d m out and d m below the C0-lobe source, so each sees the lamp at
    # exactly 45 deg -- the only angle the lobe (0 at V=0/90, `PROBE_CANDELA` at V=45) is non-zero.
    # `(2 * d * d)` is the squared lamp-sensor distance for a sensor d m out and d m below: d^2 + d^2.
    ring = f"{d:g} 0 0 0 0 1\n0 {d:g} 0 0 0 1\n-{d:g} 0 0 0 0 1\n0 -{d:g} 0 0 0 1\n"
    px, py, mx, my = _probe(env, root, "probe_lobe", c0_lobe_ies(PROBE_CANDELA), ring, timeout)
    lobe = PROBE_CANDELA * math.cos(math.radians(45.0)) / (2 * d * d)
    if not (math.isclose(px, lobe, rel_tol=PROBE_TOLERANCE) and max(py, mx, my) < 1e-6 * px):
        raise PhotometryUnverifiedError(
            f"a C0 lobe read +X {px:.6g}, +Y {py:.6g}, -X {mx:.6g}, -Y {my:.6g} lux; expected {lobe:.6g} toward +X only."
        )
    _VERIFIED.add(key)


SourceCalState = Literal["current", "previous", "foreign", "missing"]


def source_cal_state(path: Path, *, previous_digests: Iterable[str]) -> SourceCalState:
    """The state of a project's root `source.cal` against this package's own digest history.

    **R503: `previous_digests` comes from the caller, not a module constant.** Spec R485 records
    `package_digest` and `previous_package_digests` on the `luminaire_support` manifest record, and a
    later task writes and updates that record; this function is the pure comparison over whatever the
    caller currently holds, so nothing here freezes a digest history of its own.

    **R515: this function is total -- it always returns one of the four literals and never
    raises.** `"missing"` only when there is genuinely nothing at `path`: neither `path.exists()`
    (which follows symlinks) nor `path.is_symlink()` (which does not) finds anything there. Every
    other object at `path` that `file_digest` cannot open and hash as a regular file --
    **a symlink, dangling or not, a directory, or a FIFO** -- is `"foreign"`, never `"missing"` and
    never a propagated exception: a `"missing"` state invites a caller to fix it by writing there
    (`write_source_cal`), and writing through a symlink is the exact hazard `open_regular_file`'s
    `O_NOFOLLOW` exists to refuse, not one this function should invite by mislabelling it recoverable
    the same way an empty path is. `"foreign"` is the correct outcome for anything rad_mcp did not
    itself write as a plain file, symlink included -- **even a symlink whose target holds exactly the
    packaged bytes is `"foreign"`, never `"current"`**, because `source.cal` living at a project's
    root is meant to be a plain file this server wrote and can safely overwrite in place; a symlink
    there might point anywhere else `write_source_cal` should never be trusted to write through.

    Found round 1 (controller ruling R515, whole-branch review): the previous version returned
    `"missing"` for a **dangling** symlink -- `path.exists()` alone follows symlinks and reports
    `False` for one whose target is gone, indistinguishable from nothing being there at all -- and a
    caller that trusted `"missing"` and called `write_source_cal` hit a raw, uncaught `OSError`
    (`open_regular_file`'s `O_NOFOLLOW`, errno `ELOOP`) instead of a clean refusal.

    `"current"` if the digest is `SOURCE_CAL_DIGEST` -- read from module globals **at call time**,
    so a caller (or a test) that reassigns `ies_module.SOURCE_CAL_DIGEST` changes what this function
    reports without changing this function. `"current"` wins even when the digest also appears in
    `previous_digests`: a package's own current digest can never simultaneously be one of its own
    predecessors, but if a caller's `previous_digests` were ever built carelessly enough to include
    it, silently downgrading to `"previous"` would be the wrong answer to have. `"previous"` if the
    digest is in `previous_digests` (and is not current). `"foreign"` otherwise -- a file this server
    did not write, at any version it remembers, or anything that is not a plain file at all.

    **Why this checks existence and symlink-ness first, rather than only catching
    `ProvenanceError`.** `file_digest` never raises `FileNotFoundError`: `project.py`'s own module --
    `open_regular_file`, then `IsADirectoryError`/`FileNotFoundError`/`NotARegularFileError`/`ELOOP`
    -- translates every one of those into `ProvenanceError` (measured: `file_digest` of a missing
    path raises `ProvenanceError("input file does not exist: ...")`, not `FileNotFoundError`; of a
    dangling *or* live symlink, `ProvenanceError("... is a symlink ...")`, via the same `ELOOP` a
    live symlink's `O_NOFOLLOW` open produces). A bare `except ProvenanceError` around `file_digest`
    would therefore never see the "missing" case as an exception at all and cannot distinguish it
    from a directory or a symlink by type -- so existence is still checked first, and everything past
    that point that `file_digest` refuses is caught and reported as `"foreign"` (R515), rather than
    left to propagate as R503's first pass required.
    """
    if not path.exists() and not path.is_symlink():
        return "missing"
    try:
        digest = file_digest(path)
    except (ProvenanceError, OSError):
        # I1 (whole-branch fix wave): `file_digest` translates the missing/directory/symlink/FIFO
        # refusals into `ProvenanceError` but re-raises any other `OSError` -- a `0o000` regular
        # file's `PermissionError`, an `EIO` -- as itself. R515 says total: a file this server
        # cannot even read is not one it can vouch for, so it is `"foreign"` like the rest.
        return "foreign"
    if digest == SOURCE_CAL_DIGEST:
        return "current"
    return "previous" if digest in previous_digests else "foreign"


def write_source_cal(path: Path) -> None:
    """Replace a project's root `source.cal` with the packaged, current bytes -- **atomically**.

    Code review M4 (whole-branch fix wave): the lit rewrite (`rad_mcp.lit_scene.commit_lit_support`)
    unlinked the file and then wrote it in place, so a write that failed part-way left an empty or
    missing `source.cal` and every later lit run refused until a re-import. It now stages a sibling
    `source.cal.partial` through `safe_io.open_staging_file` (a planted hardlink is never written
    through), fsyncs it, `os.replace`s it in and then, outside the staging file's cleanup, fsyncs
    the directory -- the same two primitives `luminaire(action="import")` stages its files with
    (`luminaire_tool._write_and_register`). Import
    keeps its own batch because its root `source.cal` must rename in the same all-or-nothing set as
    the type files it records beside it; this is the single-file form.

    A non-regular object at `path` (a symlink, live or dangling; a directory) is refused with
    `NotARegularFileError` before anything is created: `os.replace` would replace a symlink rather
    than write through it, but a `source.cal` that is not a plain file is `"foreign"` and never ours
    to replace (R515). The staging open sits outside the cleanup, `blend._write_blend`'s reason: when
    it refuses, nothing of ours exists to remove."""
    refuse_existing_non_regular_destination(path)
    partial = path.with_name(path.name + ".partial")
    handle = open_staging_file(partial)
    try:
        with handle:
            handle.write(SOURCE_CAL_BYTES)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(partial, path)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise
    # L-49: after the rename, outside the cleanup. A failure here propagates with the new bytes
    # already at `path`; `commit_lit_support` reads the file to say so rather than "could not be
    # rewritten".
    fsync_directory(path.parent)


def modifier_collisions(type_modifiers: Iterable[str], material_modifiers: Iterable[str]) -> list[str]:
    """R487: modifier names a luminaire type and the project's own materials both declare -- an
    import that would silently overwrite (or be overwritten by) a project material at render time.
    Sorted, so the result is deterministic and diffable rather than set-iteration order."""
    return sorted(set(type_modifiers) & set(material_modifiers))
