"""`findglare`'s glarefile format, written by this server, and `glarendx -t ugr`.

Phase 11c design SS6. This module owns the format and the two spawns; `rad_core.ugr` owns the
arithmetic and never sees a Radiance file.

The `VIEW=` line is load-bearing: design SS4's F8 measured a missing one silently defaulting to
`-vd 0 1 0` and returning a wrong number at exit 0, and the up vector alone moving one source's
rating by 8.59 UGR units. Exactly one indirect-illuminance row is written, at angle 0, because F8
also measured a second row rotating the view and the rows coming back in reverse input order.

**Why `write_glarefile` stages and renames rather than writing the destination directly** (the
whole-branch review's Critical 1). Every filesystem open in `rad_engine`, in either direction, goes
through `rad_engine.safe_io` (that module's own docstring): `Path.write_text` follows a symlink at
the final component and blocks forever on a FIFO, which is the write-side hazard it exists to
close. `open_regular_file(path, "wb")` -- what this function used to do, `rad_engine.ies._write`'s
shape -- closes those two and leaves a third open: `"wb"` is `O_CREAT | O_TRUNC` with no `O_EXCL`,
so a **hardlink** planted at the destination is written *through*, truncating and replacing a file
that can be outside every allowed root, under all of its names. Measured on this tool's own
`08_results/ugr.glr` by the review's security pass, and `contain()` cannot see it, because
`Path.resolve()` on a hardlink returns the path itself.

So the four steps are `rad_mcp.electric_tool._write_results`', which already carried them
(R526/R612/R640/R646): refuse anything at the destination that is not a regular file, open a
`.partial` staging file exclusively, write it, and `os.replace` it into place. A hardlink at the
name is **replaced, never written through**, a failed write leaves the previous file, and a
directory or a FIFO at the name raises `NotARegularFileError` -- a refusal the caller has a code
for -- rather than `IsADirectoryError`/`ENXIO` escaping uncoded.

**The staging name is this call's own, which is where this module leaves `_write_results`.** The
published glarefile is one fixed name per project, and two `run_ugr` calls on one project are
ordinary (a second server, or a CLI on the same root). Under one fixed `<name>.partial`,
`open_staging_file` had each call unlink the other's staging file as "stale": the loser's
`os.replace` raised `FileNotFoundError`, or its exclusive open raised `FileExistsError`, and
`run_ugr` has a code for neither. Measured: eight concurrent writers, all eight raised. So the
staging file is `.<name>.<random>.partial` (`safe_io.open_unique_staging_file`), created
`O_EXCL | O_NOFOLLOW` exactly as before -- still only ever a file this call created -- and no call
can name another's. **The cost:** a
fixed name cleaned up after a crashed call on the next write; a random one does not, so a process
killed between the open and the rename leaves one stray dot-file beside the glarefile.
"""
from __future__ import annotations

import math
from pathlib import Path
from typing import Final

from rad_core.photometry import to_lux
from rad_core.quality import QualityParameters
from rad_core.ugr import Cone, SourceRow, Vec, ray_directions

from .cores import worker_cores
from .env import RadianceEnv
from .rtrace import RtraceRequest, run_rtrace
from .rtrace import parse_rtrace_hits as _parse_rtrace_hits_raw
from .run import run
from .safe_io import refuse_existing_non_regular_destination, replace_file_atomically

__all__ = [
    "ANGLE_ZERO_TOLERANCE",
    "DEFAULT_RAYS_PER_EMITTER",
    "EmitterSample",
    "GlarendxValueNotFinite",
    "RtraceOutputMismatch",
    "assign_hits_to_emitters",
    "parse_ugr",
    "run_ugr_index",
    "sample_emitters",
    "vertical_illuminance_at",
    "write_glarefile",
]


def _g(v: float) -> str:
    return f"{v:g}"


def _position(v: float) -> str:
    """A ray origin, not a direction -- full precision (`.17g`), the same formatter
    `rad_engine.visibility.cast` and this module's own `sample_emitters`/`vertical_illuminance_at`
    write eyes at. `write_glarefile`'s `-vp` is a third literal eye-position site R688's fix wave
    did not reach (ruling R705, extending R688). `_g`'s bare `{:g}` (six significant digits) stays
    for `-vd`/`-vu`, unit vectors R688 left alone."""
    return f"{v:.17g}"


def _write(path: Path, data: bytes) -> None:
    """`data` at `path`, staged and renamed in -- the module docstring's four steps.

    The staging open is **outside** the cleanup, for `rad_engine.blend`'s reason and
    `electric_tool._write_results`': an exclusive open that fails created nothing of ours, so there
    is nothing of ours to unlink. The name is random per call (the module docstring), so nothing
    is unlinked *before* the open either: there is no stale file of ours at a name nobody has used.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    refuse_existing_non_regular_destination(path)
    # `replace_file_atomically` (R855): the same per-call staging name and rename, and it also refuses
    # a read-only destination and keeps a writable one's permission bits, which this module's own
    # rename did not.
    replace_file_atomically(path, lambda handle: handle.write(data))


def write_glarefile(path: Path, *, view_position: Vec, view_direction: Vec, view_up: Vec,
                    rows: list[SourceRow], indirect_lux: float) -> None:
    """Write a `findglare`-shaped glarefile (design SS4, F1) directly -- this server never runs
    `findglare` itself, since it already knows the sources it wants published.

    Exactly one indirect-illuminance row, at angle 0 (F8): a second row makes `glarendx` rotate
    the view per row and return the rows in reverse input order, which is a defect this module
    sidesteps by never writing a second row rather than by parsing around it.

    Staged and renamed in (the module docstring), so `path` is only ever replaced, never written
    through. Callers that need a file **no other call can reach** -- `rad_mcp.ugr_tool`, whose
    published name is one per project and whose glarefile is an input to the rating -- pass a name
    of their own making; this function does not choose one.
    """
    view = (f"-vth -vp {' '.join(_position(c) for c in view_position)}"
            f" -vd {' '.join(_g(c) for c in view_direction)}"
            f" -vu {' '.join(_g(c) for c in view_up)}"
            " -vh 180 -vv 180 -vo 0 -va 0 -vs 0 -vl 0")
    lines = ["#?RADIANCE", "rad_mcp run_ugr", f"VIEW= {view}", "FORMAT=ascii", "", "BEGIN glare source"]
    for row in rows:
        d = " ".join(f"{c:.6f}" for c in row.direction)
        lines.append(f"\t{d}\t{row.omega_sr:.6e}\t{row.luminance_cd_m2:.6e}")
    lines += ["END glare source", "BEGIN indirect illuminance",
              f"\t0\t{indirect_lux:.6f}", "END indirect illuminance", ""]
    _write(path, "\n".join(lines).encode("ascii"))


ANGLE_ZERO_TOLERANCE: Final[float] = 1e-9


class GlarendxValueNotFinite(RuntimeError):
    """`glarendx` exited 0 but the angle-0 row is absent or not a finite number.

    F12 measured `inf` for a zero background illuminance and `nan` for a negative one, both at
    exit 0 with empty stderr. Neither is a rating and `inf` is not valid JSON.
    """


def parse_ugr(stdout: str) -> float:
    """The value on the row whose angle is 0, never the first line (design SS4, F8: rows come back
    in reverse input order, so a parser that reads the first line reads the wrong one, silently)."""
    for line in stdout.splitlines():
        parts = line.split()
        if len(parts) != 2:
            continue
        try:
            angle, value = float(parts[0]), float(parts[1])
        except ValueError:
            continue
        if abs(angle) <= ANGLE_ZERO_TOLERANCE:
            if not math.isfinite(value):
                raise GlarendxValueNotFinite(
                    f"glarendx returned {parts[1]} for the view direction, which is not a rating; "
                    "this happens when the background illuminance is not positive (F12)."
                )
            return value
    raise GlarendxValueNotFinite("glarendx printed no row at angle 0.")


def run_ugr_index(env: RadianceEnv, glarefile: Path, *, cwd: Path, timeout_s: float) -> float:
    """Spawn `glarendx -t ugr` on `glarefile` and parse the angle-0 row."""
    result = run(env, ["glarendx", "-t", "ugr", str(glarefile)], cwd=cwd, timeout=timeout_s)
    return parse_ugr(result.stdout)


# --------------------------------------------------------------------------------------------
# The emitter sampler.
#
# P1 (ruling): a ray is assigned to an instance by its intersection point, not by its modifier
# alone. There is one `.rad` file per luminaire *type* (`rad_engine.ies.convert_ies`) and every
# placement is a bare `!xform` of that type file with no `-m`
# (`luminaire_layout.hand_line`, `array_line`) -- so `light`'s modifier names the *type*, and every
# instance of that type, wherever it is placed, shares it. Keying by modifier alone would collapse
# a four-lamp ceiling grid of one type into one cone and one row. Instances are therefore keyed by
# a synthetic instance key (`f"{placement}#{index}"`, built by the caller) and a hit is assigned to
# an instance only when its modifier matches *and* the intersection point falls inside that
# instance's own absolute emitter box.

DEFAULT_RAYS_PER_EMITTER: Final[int] = 20_000

# Not a sourced threshold. `rtrace -op` reports the intersection point as computed floating-point
# geometry, not the analytic box boundary itself, so a point that is geometrically "on" a face can
# land a few ULPs outside the stored box. This is floating-point slack on a number Radiance
# computed, not a physical margin around the emitter.
_BOX_EPSILON_M: Final[float] = 1e-6


class EmitterSample:
    """One placed instance to sample from the eye: its aimed cone, its type's own `light`
    modifier, and its absolute emitter box, kept together rather than in three parallel dicts
    keyed by instance -- the same reasoning `rad_core.ugr.Instance` carries bounds instead of
    just a centre for. A cone, a modifier and a box built from three separate dicts (and three
    separate loops) could silently drift out of correspondence and assign hits to the wrong
    instance; one object per instance makes that impossible to construct.
    """

    __slots__ = ("cone", "modifier", "bounds_min", "bounds_max")

    def __init__(self, cone: Cone, modifier: str, bounds_min: Vec, bounds_max: Vec) -> None:
        self.cone = cone
        self.modifier = modifier
        self.bounds_min = bounds_min
        self.bounds_max = bounds_max

    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return (f"EmitterSample(cone={self.cone!r}, modifier={self.modifier!r}, "
                f"bounds_min={self.bounds_min!r}, bounds_max={self.bounds_max!r})")

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, EmitterSample):
            return NotImplemented
        return (self.cone, self.modifier, self.bounds_min, self.bounds_max) == (
            other.cone, other.modifier, other.bounds_min, other.bounds_max)


class RtraceOutputMismatch(RuntimeError):
    """`rtrace` returned a row count other than the number of rays it was given.

    Its own class, and refused rather than worked around: the rows are matched to rays **by
    index**, so a short or long answer cannot be attributed at all -- see
    `assign_hits_to_emitters`, which measures what a partial loss costs the published rating.
    `rad_mcp.ugr_tool` maps it to the register's `RTRACE_OUTPUT_MISMATCH`, allocated in Phase 11b
    for `run_electric`'s own "one `rtrace`, a value count other than the points traced": the same
    condition, the same recovery (report the fault), so the code is reused rather than a second one
    allocated for it.
    """


def _point_in_box(point: Vec, bounds_min: Vec, bounds_max: Vec, epsilon: float) -> bool:
    return all(bounds_min[i] - epsilon <= point[i] <= bounds_max[i] + epsilon for i in range(3))


def assign_hits_to_emitters(
    hits: list[tuple[Vec, str, float] | None],
    directions: list[Vec],
    spans: dict[str, range],
    emitters: dict[str, EmitterSample],
) -> dict[str, list[tuple[Vec, float]]]:
    """Group parsed `rtrace` hits by instance key, scoped to the span of ray indices cast for
    that instance's own cone.

    A hit at ray index `i` counts for instance `key` only when **both** hold: the reported
    modifier equals `emitters[key].modifier` (that instance's type), and the intersection point
    lies inside `emitters[key]`'s absolute box, widened by `_BOX_EPSILON_M` (P1).

    **A row count other than the ray count is raised, never worked around** (whole-branch review,
    Important 1). This function guarded a short `rtrace` stdout with `i >= len(hits): continue`,
    which the Task 5-7 ledger deferred as "backstopped downstream by `MIN_HITS` ... so it cannot
    reach a wrong published number". **That is measurably false.** Measured against this function
    and `rad_core.ugr.reduce_samples`: 1000 rays, 600 genuine hits, stdout truncated after ray 500
    -- 500 hits, still 2.5x over the 200 floor, so nothing downstream refuses -- publishes an
    `omega_sr` **16.7 % low** and a UGR **0.633450 units low** (8*log10 of the omega ratio), at exit 0,
    with no advisory, **in the optimistic direction**. `MIN_HITS` backstops total loss, not partial
    loss. The dropped rays are also silently attributed to the *last* instances in `order`, since
    the spans are laid out instance by instance.

    The per-hit guards below are unchanged and are not this case: a row that carries no usable
    intersection is `None` from `parse_rtrace_hits` and is a ray that hit nothing, which is a
    measurement, not a missing measurement.

    The box test is
    scoped to `spans[key]` -- the rays cast through *that* instance's own cone -- rather than
    searched across every emitter, which is what stops a *near* lamp of the same type, caught
    inside a *far* lamp's widened margin cone, from being counted as the far lamp's: a ray in the
    far instance's span that actually struck the near instance reports the shared type modifier,
    fails the far instance's own box test (the point is in the near instance's box, not the far
    one's), and is simply excluded -- exactly the free-occlusion reasoning of F10, extended to a
    same-type instance standing in for an occluder. It is not reattributed to the near instance
    either, because it was never cast under the near instance's own ray budget (`n_rays`), and
    counting it there would inflate the near instance's solid-angle estimate beyond what its own
    cone's rays actually measured.
    """
    if len(hits) != len(directions):
        raise RtraceOutputMismatch(
            f"rtrace returned {len(hits)} rows for {len(directions)} sampling rays. The rows are "
            "matched to rays by index, so a short or long answer cannot be attributed: no solid "
            "angle is computed from it. This is a fault inside the server or its Radiance install, "
            "not something about the arguments; nothing was written, and the failure should be "
            "reported."
        )
    out: dict[str, list[tuple[Vec, float]]] = {key: [] for key in spans}
    for key, span in spans.items():
        emitter = emitters[key]
        for i in span:
            hit = hits[i]
            if hit is None:
                continue
            point, modifier, luminance = hit
            if modifier != emitter.modifier:
                continue  # missed the emitter, or was blocked: not a hit (F10)
            if not _point_in_box(point, emitter.bounds_min, emitter.bounds_max, _BOX_EPSILON_M):
                continue  # struck a different instance (or surface) sharing this type's modifier
            out[key].append((directions[i], luminance))
    return out


def sample_emitters(
    env: RadianceEnv, octree: Path, *, eye: Vec, emitters: dict[str, EmitterSample],
    n_rays: int, seed: int, cwd: Path, timeout_s: float,
) -> dict[str, list[tuple[Vec, float]]]:
    """Sample every built emitter from `eye` in one `rtrace` call, keyed by instance key.

    One spawn carries every instance's rays, so the spawn count does not grow with the placement
    count. Rays are emitted instance by instance (through that instance's own aimed cone) and the
    offsets are recorded as `spans`, so a ray's index identifies which instance's cast it belongs
    to; `assign_hits_to_emitters` then does the modifier-and-box assignment (P7: this function
    calls that helper rather than re-implementing its grouping logic inline).

    **What does grow with the placement count is the memory, linearly and before the spawn**
    (whole-branch review, Critical 2). `lines`, `directions` and the joined stdin blob are all
    built here, in full, for `n_rays * len(emitters)` rays, and `rad_engine.run` then encodes that to
    bytes (a copy) and hands it to `rad_engine.process_group.run_in_own_session` (R750;
    `subprocess.run(input=..., capture_output=True)` before it), whose `Popen.communicate` buffers
    the whole of stdout again. Measured on this machine at `n_rays=20_000` for one fixture
    emitter: 291 bytes per ray, 5.82 MB per instance, none of it bounded by `timeout_s` -- the
    lists exist before the spawn the timeout arms. `n_rays` is the caller's to bound, and
    `rad_core.ugr.rays_per_emitter` is what `rad_mcp.ugr_tool` bounds it with; this function casts
    exactly what it is given.

    **`-ab 0`, and `quality` plays no part here** (P10). This function measures two purely
    geometric/radiometric quantities: what fraction of a cone's rays land on an emitter (omega --
    pure geometry) and the emitter's own radiance at the first intersection (L, what a `light`
    primitive returns on a primary hit). Neither is touched by an ambient bounce: no indirect
    contribution enters a `light` primitive's own reported radiance. What ambient parameters change
    here is only cost -- every ray that *misses* every emitter still pays a full ambient evaluation
    against every other primitive in the scene, tens of thousands of times per call, against a
    20 s inline budget -- so `-ab 0` is a cost cut with (claimed) zero effect on the answer, and
    `n_rays` is this sampler's real precision knob. This is a claim, not yet a proof: Task 16
    measures the same emitter sampled with and without `-ab 0` and checks that `L` and omega agree
    within sampling noise. `vertical_illuminance_at`, by contrast, keeps the full quality argv,
    because ambient genuinely is the quantity it measures.
    """
    order = list(emitters)
    lines: list[str] = []
    directions: list[Vec] = []
    spans: dict[str, range] = {}
    for key in order:
        start = len(lines)
        for d in ray_directions(emitters[key].cone, n_rays, seed):
            # The eye at `.17g`, exactly as `rad_engine.visibility.cast` writes its origins and for
            # its reason (final whole-branch review, C1): `{:g}` moved an eye in site coordinates by
            # metres.
            lines.append(
                f"{eye[0]:.17g} {eye[1]:.17g} {eye[2]:.17g} {d[0]:.9g} {d[1]:.9g} {d[2]:.9g}"
            )
            directions.append(d)
        spans[key] = range(start, len(lines))

    result = run(
        env, ["rtrace", "-h", "-faa", "-ovpm", "-ab", "0", "-n", str(worker_cores()), str(octree)],
        cwd=cwd, timeout=timeout_s, stdin="\n".join(lines) + "\n" if lines else "",
    )
    raw_hits = _parse_rtrace_hits_raw(result.stdout)
    # `rad_engine.rtrace.parse_rtrace_hits` returns the raw (R, G, B) radiance triple -- it does
    # not import `to_lux` (purity ratchet, `tests/test_rtrace.py::TestPurity`). The photometric
    # conversion happens here, the one place this module reads that third element.
    hits = [None if h is None else (h[0], h[1], to_lux(h[2])) for h in raw_hits]
    return assign_hits_to_emitters(hits, directions, spans, emitters)


def vertical_illuminance_at(
    env: RadianceEnv, octree: Path, *, eye: Vec, view_direction: Vec,
    parameters: QualityParameters, cwd: Path, timeout_s: float,
) -> float:
    """`E_total` at `eye` along `view_direction`, in lux (P2, design SS6 step 7).

    One `rtrace -I` ray, through `rad_engine.rtrace`'s own `RtraceRequest`/`run_rtrace` rather than
    a second `rtrace -I` spawn path -- that module already owns the argv construction and the
    irradiance parse, and `docs/plan.md` ruling R30's "one arithmetic path by construction" applies
    equally to one spawn-and-parse path. Keeps the **full** quality argv, unlike `sample_emitters`
    (P10): ambient genuinely contributes to the illuminance at the eye, which is the quantity being
    measured here.
    """
    points_text = (
        f"{eye[0]:.17g} {eye[1]:.17g} {eye[2]:.17g} "  # full precision: `sample_emitters`' comment
        f"{view_direction[0]:.9g} {view_direction[1]:.9g} {view_direction[2]:.9g}\n"
    )
    request = RtraceRequest(octree=octree, parameters=parameters, processes=worker_cores())
    [rgb], _result = run_rtrace(env, request, points_text=points_text, cwd=cwd, timeout=timeout_s)
    return to_lux(rgb)
