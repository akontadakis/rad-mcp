"""Building `gendaymtx` command lines and parsing the resulting annual sky-matrix header.

**Scope** (docs/tool-surface.md §10/§11, docs/plan.md Phase 4, ruling R34): this module is
`rad_engine.sky`'s sibling for the annual sky matrix -- same location, ground reflectance and north
convention, a different binary (`gendaymtx` instead of `gensky`/`gendaylit`) and an annual matrix
(one row per Reinhart sky patch, one column per hour) instead of one point-in-time sky. Ruling R34
is why this lives in its own module rather than inside `rad_engine.rtrace` or `rad_engine.sky`
itself: sky generation and ray tracing are separate concerns over separate binaries, and this seam
was reserved for exactly this task when `sky.py` was written. **Read `rad_engine.sky` first** -- it
is the closest sibling, and this module matches its structure and docstring style throughout.

This module does **not** run `rcontrib`/`dctimestep` or compute any annual metric (`rad_engine`'s
matrix-method modules, Phase 6/7) and does **not** build the wire envelope or decide method
inference (`rad_core.method`, `rad_mcp`) -- it owns `gendaymtx` argv construction and reading back
what `gendaymtx` reports about the matrix it just built, nothing else.

**Argv is data, not an interpolated string** -- the identical contract `sky.py`'s `build_*_argv`
functions hold (docs/tool-surface.md §10: "argv is load-bearing: the exact command line is returned
to the client, so the sky is auditable"). `build_gendaymtx_argv` returns a plain `list[str]` built
field by field, with no binary path baked in; `rad_engine.run.run` substitutes
`env.path("gendaymtx")` for that first token.

**Ground reflectance is one project value, passed here as a `ProvenanceValue`
(docs/tool-surface.md#ground-reflectance-is-one-project-value-passed-to-every-sky-generator).**
`gendaymtx -g` takes it verbatim from `SkyMatrixSpec.ground_reflectance.value`, the same treatment
`sky.py` gives `gensky -g`/`gendaylit -g` -- this module never invents or inherits `gendaymtx`'s own
0.2 default. Verified live on this machine (docs/tool-surface.md verified fact 10): `-g 0.1 0.1 0.1`
halves the ground-patch row (`40 40 40` -> `20 20 20`) on a one-hour Athens `.wea` relative to the
0.2 default, stderr empty, exit 0 -- so an omitted `-g` is not a no-op, it is a second, disagreeing
ground reflectance entering the same scene.

**North.** `gendaymtx -r` rotates the sky counter-clockwise, **west of north**
(docs/tool-surface.md, sourced directly for `gendaymtx`: *"gendaymtx assumes the positive Y-axis
points north ... the -r (or -rz) option rotates the sky the specified number of degrees
counter-clockwise about the zenith, i.e., west of north"*), while `SkyMatrixSpec.north` -- like
`run_annual.north` -- is degrees **east of north** (the surveying convention). This module reuses
`rad_engine.sky.north_rotation_flag` verbatim rather than reimplementing the sign flip, and since
ruling R694 `build_gendaymtx_argv` below is that function's only caller in `src/`: `gensky` and
`gendaylit` are no longer given `-r` (on this Radiance 6.1a `gensky -r` is the solar radiance and
`gendaylit -r` an unknown option) and are rotated by `rad_engine.sky.north_rotation_xform_argv`
instead. The `-r` west-of-north sense is independently sourced for `gendaymtx` specifically, so
this is the one call site the corpus actually names.

**Reinhart row count -- the reason check 4.3 exists.** `gendaymtx -m N` produces `144*N^2 + 2` rows:
`144*N^2 + 1` Reinhart sky patches (the Tregenza 145-patch sky at N=1 is itself `144*1 + 1`) **plus
one ground patch**. Verified against all four values recorded in docs/tool-surface.md's own
verified-facts table (fact 3, `Nrbins` from `rcalc -f reinhart.cal`) and independently confirmed by
running `gendaymtx -m N` directly on this machine for this task:

    N=1 -> 146   (144*1 + 2)
    N=2 -> 578   (144*4 + 2)
    N=4 -> 2306  (144*16 + 2)
    N=6 -> 5186  (144*36 + 2)

The formula reproduces all four measured values exactly, so `reinhart_row_count` computes it rather
than hard-coding two magic numbers -- but **146 and 2306 are asserted as literals in
`tests/test_skymatrix.py` first**, against both a captured real `gendaymtx` header and a live spawn,
so a typo in the formula cannot silently pass. **145 is a wrong-but-plausible number for N=1**: it
is the correct Tregenza *sky*-patch count, and docs/tool-surface.md's verified-facts table records
that two sourced specs ([workflows §3.2](../../docs/specs/workflows-point-in-time-to-annual.md))
wrote `-bn 145`/`-bn 5185` -- both one short, both this exact off-by-the-ground-patch error. This
module's job is to make that error impossible to reintroduce by construction: `reinhart_row_count`
is the only place the row count is computed, and it is never 144*N^2 + 1.

**LM-83's sky-resolution floor is a *patch* count; the matrix row count above includes the
ground patch -- these are two different numbers and this module keeps them named distinctly.**
LM-83-12 sec 2.3.2 states "minimum 2 305 Reinhart sky patches"
([ch04](../../skills/lighting-standards-compliance/chapters/ch04-ies-lm-83-sda-ase.md) sec 2.3.2)
-- `LM83_MIN_REINHART_PATCHES = 2305`, the *sky*-patch count at `-m 4` (2306 matrix rows minus the
one ground patch). `resolve_lm83_sky_subdivision` raises a request below the subdivision that meets
this floor (`-m 4`, the smallest N with `144*N^2 + 1 >= 2305`) and reports the fact via
`sky_subdivision_raised_advisory`; this module supplies the mechanism and the fact only -- the tool
layer (`run_annual`, outside this module's ownership) decides *when* a metric counts as "LM-83" and
builds the wire advisory envelope from what this module returns.

**Advisory codes used here -- `SKY_SUBDIVISION_RAISED`, `GROUND_REFLECTANCE_INCONSISTENT`,
`NORTH_CONVENTION` -- are all already allocated** in docs/tool-surface.md's advisory register (141
codes total, allocate-once); none is invented here. This module builds each one directly, the same
convention `sky.df_sky_forced_advisory` uses: a finding proven by this module's own decision or by a
comparison the caller already made, not by anything Radiance wrote to stderr, so each carries
`lines=()`.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal

from rad_core.lm83_edition import DEFAULT_LM83_EDITION, LM83_22, Lm83Edition, section
from rad_core.manifest import ProvenanceValue

from .advisories import EngineAdvisory
from .sky import north_rotation_flag

__all__ = [
    "LM83_MIN_REINHART_PATCHES",
    "LM83_MIN_SKY_SUBDIVISION",
    "LM83_MIN_SUN_POSITION_SUBDIVISION",
    "LM83_SOLAR_POSITION_TOLERANCE_DEG",
    "SOLAR_DISK_DEGREES",
    "SkyMatrixHeader",
    "SkyMatrixSpec",
    "build_gendaymtx_argv",
    "ground_reflectance_inconsistent_advisory",
    "north_convention_advisory",
    "north_rotation_flag",
    "parse_gendaymtx_header",
    "reinhart_row_count",
    "resolve_lm83_sky_subdivision",
    "sky_subdivision_raised_advisory",
]

#: LM-83-12 sec 2.3.2's own floor, in **sky patches**, not matrix rows: "minimum 2 305 Reinhart sky
#: patches" ([ch04](../../skills/lighting-standards-compliance/chapters/ch04-ies-lm-83-sda-ase.md)
#: sec 2.3.2). This is `reinhart_row_count(4) - 1` -- the matrix row count at `-m 4` (2306) minus the
#: one ground patch every Reinhart matrix carries as an extra row. **Never conflate this with
#: `reinhart_row_count(4)` (2306)** -- one counts what LM-83 requires be modelled, the other counts
#: what `gendaymtx` writes to disk; see the module docstring for why this distinction is exactly the
#: class of error check 4.3 exists to catch.
LM83_MIN_REINHART_PATCHES = 2305

#: The smallest `-m N` whose sky-patch count (`144*N^2 + 1`) meets `LM83_MIN_REINHART_PATCHES`.
#: `144*4^2 + 1 = 2305` exactly -- `-m 4` meets the floor with no slack, so `-m 3` (`144*9+1=1297`)
#: does not qualify and `-m 4` is the correct raise target, not merely *a* value above the floor.
LM83_MIN_SKY_SUBDIVISION = 4

#: LM-83-12 **sec 2.3.1-2**'s own floor -- a *different* clause from the 2 305-patch one above, and
#: it lands on a different number. Sec 2.3.1-2 requires the **simulated solar position to be within
#: 5 degrees of the actual position**, which is a statement about how finely the sun is discretised
#: rather than about how many sky patches the diffuse sky carries. The direct-sun leg models the sun
#: as one `source` per Reinhart patch (`rad_engine.suns.build_suns_argv`), so the worst-case angular
#: error is the equivalent radius of one patch: sharing a hemisphere's `2*pi` sr equally over
#: `sun_count(mf)` patches gives `omega = 2*pi / N`, and the half-angle of a cap of that solid angle
#: is `acos(1 - 1/N)`.
#:
#: **Measured, from `rad_engine.suns.sun_count` itself** (Phase 7c Task 1, reproduced at this task;
#: `tests/fixtures/README.md`, "the direct-sun leg is bit-deterministic"):
#:
#: ```
#: mf=1 suns=145  equivalent_radius_deg=6.733   <- fails the 5 degree requirement
#: mf=2 suns=577  equivalent_radius_deg=3.374   <- clears it
#: mf=4 suns=2305 equivalent_radius_deg=1.688
#: mf=6 suns=5185 equivalent_radius_deg=1.125
#: ```
#:
#: So `mf=1` is the only subdivision in `SUPPORTED_SUBDIVISIONS` that fails, and **2** is the floor.
#: `LM83_MIN_SKY_SUBDIVISION` (4) is stricter, so on every path that resolves through sec 2.3.2 this
#: clause is already satisfied as a side effect and this constant never fires. The path it existed
#: for was the one that escaped sec 2.3.2: `run_annual(blind_control="lm83")` with neither `sda` nor
#: `ase` in `metrics` planned the direct-sun leg while `lm83_metric` was `False`. **Since R866 that
#: path is an LM-83 request too** (`run_annual` passes `lm83_metric` for `blind_control="lm83"`),
#: so no `run_annual` call reaches this floor alone any more; it stays the stated second clause, and
#: what keeps it from firing alone is the caller's flag, not this module.
#:
#: Not re-derived from `144*mf^2 + 1`: the number is pinned against `sun_count` in
#: `tests/test_skymatrix.py`, so a revision to the measured bin table moves the assertion rather
#: than silently invalidating a literal here.
LM83_MIN_SUN_POSITION_SUBDIVISION = 2

#: LM-83-12 sec 2.3.1-2's tolerance itself, in degrees, named so the test that pins
#: `LM83_MIN_SUN_POSITION_SUBDIVISION` against `sun_count` compares against the standard's own
#: number rather than a second copy of it.
LM83_SOLAR_POSITION_TOLERANCE_DEG: Final[float] = 5.0


def reinhart_row_count(mf: int) -> int:
    """The number of rows `gendaymtx -m mf` writes: `144*mf^2 + 2`.

    `144*mf^2 + 1` Reinhart sky patches (the Tregenza 145-patch sky at `mf=1` is itself
    `144*1^2 + 1`) **plus one ground patch** -- every Reinhart daylight matrix carries a ground-patch
    row that has no sky-patch counterpart, which is exactly what makes the naive `144*mf^2 + 1`
    wrong for describing a *matrix row count* even though it is the right count for *sky patches*.

    Reproduces all four values verified on this machine (docs/tool-surface.md's own verified-facts
    table, fact 3 -- `Nrbins` from `rcalc -f reinhart.cal`, and re-confirmed for this task by running
    `gendaymtx -m N` directly): `mf=1 -> 146`, `mf=2 -> 578`, `mf=4 -> 2306`, `mf=6 -> 5186`. Not
    re-derived from any other formula in this codebase -- it is checked against those four literals
    in `tests/test_skymatrix.py` before anything here relies on it.
    """
    return 144 * mf * mf + 2


def resolve_lm83_sky_subdivision(
    requested: int | None, *, lm83_metric: bool, direct_sun_leg: bool = False
) -> tuple[int | None, bool]:
    """Raise `requested` to whichever LM-83 floor applies to this run. Returns
    `(subdivision_to_use, raised)`.

    **Two clauses, two floors, one maximum.**

    - `lm83_metric` -- the run is an LM-83 run: it reports sDA or ASE, or (R866) applies the LM-83
      blind schedule, so sec 2.3.2's *2 305 Reinhart sky patches* applies (LM-83-22 section
      4.12.1's 2,305 under that edition) and the floor is `LM83_MIN_SKY_SUBDIVISION` (4).
    - `direct_sun_leg` -- the run plans the pre-blind direct-sun leg, so sec 2.3.1-2's *simulated
      solar position within 5 degrees* applies and the floor is
      `LM83_MIN_SUN_POSITION_SUBDIVISION` (2). See that constant for the measurement.

    A run under both takes the larger, which is 4: the clauses compose as a maximum rather than as
    a choice, and a run that dropped from 4 to 2 because it also planned a direct leg would have
    traded a compliant sky for a compliant sun. `direct_sun_leg` therefore changes nothing on any
    path that is already `lm83_metric`. It was a separate flag because one call escaped
    `lm83_metric`: `blind_control="lm83"` with neither `sda` nor `ase` in `metrics`. **Since R866
    `run_annual` passes `lm83_metric=True` for that call too**, so `direct_sun_leg` now changes no
    `run_annual` call; it is kept as the stated second clause, for a caller that plans a direct leg
    outside an LM-83 request.

    `requested=None` (no caller preference) is passed through unchanged unless `lm83_metric` is
    true, in which case it is raised to `4` -- a caller who never mentioned `sky_subdivision` still
    gets a compliant sky for an LM-83 request rather than whatever `gendaymtx`'s own default would
    be. **`direct_sun_leg` deliberately does not substitute for `None`**, and that is not an
    inconsistency: `None` means "the caller's own default applies", `run_annual`'s default is
    `LM83_MIN_SKY_SUBDIVISION` (4), and 4 already clears this floor -- so substituting 2 here would
    *lower* a run that was going to be compliant anyway.
    `tests/test_skymatrix.py::test_the_repos_own_default_clears_the_sun_position_floor` is what
    keeps that reasoning true if either constant moves.

    A `requested` already at or above the applicable floor is left exactly as given: these are
    floors, not pins, so `-m 6` requested alongside an LM-83 metric stays `-m 6`.

    Pure and gives no opinion on *which* metrics count as "LM-83", nor on when a run plans a direct
    leg -- those decisions (docs/tool-surface.md §11's method-inference rules) belong to
    `rad_core.method`/`run_annual`, outside this module's ownership; this function only applies the
    floors once the caller has already decided both flags.
    """
    if lm83_metric:
        floor = LM83_MIN_SKY_SUBDIVISION
    elif direct_sun_leg and requested is not None:
        floor = LM83_MIN_SUN_POSITION_SUBDIVISION
    else:
        return requested, False
    if requested is not None and requested >= floor:
        return requested, False
    return floor, True


#: `gendaymtx -5`'s mandatory argument, and the `source` size in the suns scene
#: (`rad_engine.suns`, Task 7). One constant for both because they are one physical quantity: the
#: sun matrix (`mode="sun_disk"`) distributes each hour's direct-normal irradiance over a disk of
#: this angular size, and the suns scene's point sources must claim the identical solid angle --
#: two literals here would describe two different suns feeding the same 5-phase sum. **Never write
#: bare `-5`**: verified live on this machine at the start of this branch (`tests/fixtures/
#: README.md`, "Five-phase costs") -- `gendaymtx: missing solar disk size argument for '-5' option`,
#: exit 1. Both `docs/specs/five-phase-method.md` sec 4.4 and `docs/specs/workflows-point-in-time-
#: to-annual.md` sec 3.3 write bare `-5`; this constant is why the code built from them does not.
SOLAR_DISK_DEGREES: Final[float] = 0.533

#: The extra `gendaymtx` flags each `SkyMatrixSpec.mode` adds, inserted immediately after `-m` (see
#: `build_gendaymtx_argv`'s docstring for why that position and not another). `"total"` is the empty
#: tuple deliberately -- it is what every 2-phase/3-phase caller already built before this task, and
#: `test_total_mode_is_unchanged` pins that an empty contribution here leaves their argv byte-
#: identical. `"sun_disk"` carries its `-d` inline (a sun matrix is direct-only by construction: a
#: disk of sky with no diffuse component would double-count against `"direct"`'s own Sds term), so a
#: caller cannot select `-5` without also selecting `-d` by forgetting a second flag.
_MODE_FLAGS: Final[Mapping[str, tuple[str, ...]]] = {
    "total": (),
    "direct": ("-d",),
    "sky_only": ("-s",),
    "sun_disk": ("-5", repr(SOLAR_DISK_DEGREES), "-d"),
}


@dataclass(frozen=True)
class SkyMatrixSpec:
    """One resolved annual sky-matrix request -- everything `build_gendaymtx_argv` needs.

    The `gendaymtx` counterpart to `rad_engine.sky.SkySpec`: north and ground reflectance are
    already-resolved project values by the time a caller builds this, exactly as `SkySpec` expects.
    """

    weather_path: Path
    """Path to the `.wea` file `epw2wea` produced (`rad_core.weather`'s output, outside this
    module's ownership) -- `gendaymtx`'s own positional input."""
    sky_subdivision: int
    """`gendaymtx -m`. The Reinhart subdivision -- 1 (146 rows) through 6 (5186 rows, the 5-phase
    sun-matrix value). Already resolved by the caller (e.g. via `resolve_lm83_sky_subdivision`);
    this spec carries the value to use, not a request to be raised."""
    ground_reflectance: ProvenanceValue
    """`gendaymtx -g` takes this verbatim -- see the module docstring on why it is required, not
    defaulted, matching `SkySpec.ground_reflectance`'s own discipline."""
    north: float | None = None
    """Degrees east of north (`rad_core.manifest.Project.north_convention`), or `None` if the
    project declares no north scalar. Converted to `gendaymtx`'s own `-r` (west of north) by the
    re-exported `north_rotation_flag`."""
    mode: Literal["total", "direct", "sky_only", "sun_disk"] = "total"
    """Which `gendaymtx` matrix this spec builds -- the 5-phase method's four sky-side inputs share
    one builder rather than four:

    - `"total"` (default): the plain annual sky matrix every 2-phase/3-phase caller already built.
      No extra flags -- `_MODE_FLAGS["total"] == ()` -- so existing callers are unaffected by this
      field's addition (`test_total_mode_is_unchanged`).
    - `"direct"`: `-d`, direct-sun-only, for the 5-phase Sds term (docs/specs/five-phase-method.md
      sec 5.3.4).
    - `"sky_only"`: `-s`, sky-with-no-sun, for check 7.4's sunless-sky gate.
    - `"sun_disk"`: `-5 0.533 -d`, the high-resolution sun matrix for Ssun -- `SOLAR_DISK_DEGREES`
      is this module's own constant, never a bare `-5` (that constant's own docstring has the
      verified failure)."""


def build_gendaymtx_argv(spec: SkyMatrixSpec) -> list[str]:
    """The `gendaymtx` argv for one annual sky matrix.

    Flag order: ``gendaymtx -m subdivision [mode flags] [-r north] -g ground weather_path`` --
    `-m` first since it is the defining shape of the matrix, then `spec.mode`'s flags (`_MODE_FLAGS`),
    then `-r`/`-g` in the same relative order `sky.py`'s builders use (rotation before ground), then
    the positional `.wea` path last, matching the sourced example shape (docs/tool-surface.md
    verified fact 10: ``gendaymtx -g 0.1 0.1 0.1 <file>.wea``; check 4.3's own transcript:
    ``gendaymtx -m 1 tests/fixtures/weather/athens.wea``).

    **Mode flags land right after `-m` and nowhere else**, so `"total"`'s empty contribution leaves
    every token after it -- `-r`, `-g`, the weather path -- at the exact index it held before this
    field existed. `test_total_mode_is_unchanged` is the assertion that pins this: the 2-phase and
    3-phase paths already call this function with `mode` left at its `"total"` default, and inserting
    anywhere `-g`'s or the weather path's position could shift would change what those existing
    callers send to `gendaymtx` without either caller's own code changing.

    Returns a plain argv **leading with the bare binary name**, matching `build_gensky_argv`'s and
    `build_octree`'s own contract -- `rad_engine.run.run` substitutes `env.path("gendaymtx")` for
    that first token.
    """
    argv: list[str] = ["gendaymtx", "-m", str(int(spec.sky_subdivision))]
    argv.extend(_MODE_FLAGS[spec.mode])
    argv.extend(north_rotation_flag(spec.north))
    argv.extend(["-g", *([repr(float(spec.ground_reflectance.value))] * 3)])
    argv.append(str(spec.weather_path))
    return argv


@dataclass(frozen=True)
class SkyMatrixHeader:
    """The parsed `#?RADIANCE` header `gendaymtx` writes before the matrix data -- the fields
    `parse_gendaymtx_header` reads, nothing more. Mirrors the plain, explicit shape
    `rad_engine.sky.SkyResult` uses for its own wire-adjacent material.
    """

    nrows: int
    """The Reinhart matrix row count *including the ground patch* -- see the module docstring.
    **146 at `-m 1`, never 145.**"""
    ncols: int
    """One column per timestep in the source `.wea` -- 8760 for a full annual run, fewer for a
    truncated file (this module places no requirement on it)."""
    ncomp: int
    """Components per cell -- 3 (R, G, B) for the default `gendaymtx` output this module builds."""
    format: str
    """The data encoding `gendaymtx` reports (``ascii``, ``float``, or ``double`` under `-o`) --
    carried through as text; this module does not itself read matrix data of any encoding."""


def parse_gendaymtx_header(text: str) -> SkyMatrixHeader:
    """Parse a `gendaymtx` header from captured stdout text.

    Reads only the lines before the header's terminating blank line -- the same boundary
    `gendaymtx` itself uses before the data rows begin -- so a caller never needs to materialise the
    (potentially very large: ~200 MB for a full 8760-hour `-m 4` run, measured on this machine) rest
    of the matrix just to learn its shape. Takes plain text, not a `Result`, matching
    `rad_engine.sky.sky_description_text`'s own text-in-text-out shape: this module has no opinion
    on whether the caller got the text from `Result.stdout` or read it back from a file already on
    disk.

    Raises `ValueError` if `NROWS` is absent -- a header this parser cannot read a row count from is
    not a header a caller should trust silently as some default (0, None) that might read as a real,
    if empty, matrix.
    """
    header_block = text.split("\n\n", 1)[0]
    fields: dict[str, str] = {}
    for line in header_block.splitlines():
        if "=" in line:
            key, _, value = line.partition("=")
            fields[key.strip()] = value.strip()

    if "NROWS" not in fields:
        raise ValueError(
            "gendaymtx header has no NROWS field; cannot determine the matrix row count. "
            f"Header text was: {header_block!r}"
        )
    if "NCOLS" not in fields:
        raise ValueError(
            "gendaymtx header has no NCOLS field; cannot determine the matrix column count. "
            f"Header text was: {header_block!r}"
        )
    if "NCOMP" not in fields:
        raise ValueError(
            "gendaymtx header has no NCOMP field; cannot determine the matrix component count. "
            f"Header text was: {header_block!r}"
        )

    return SkyMatrixHeader(
        nrows=int(fields["NROWS"]),
        ncols=int(fields["NCOLS"]),
        ncomp=int(fields["NCOMP"]),
        format=fields.get("FORMAT", ""),
    )


def sky_subdivision_raised_advisory(
    *,
    requested: int | None,
    used: int,
    lm83_metric: bool = True,
    direct_sun_leg: bool = False,
    edition: Lm83Edition = DEFAULT_LM83_EDITION,
) -> EngineAdvisory:
    """`SKY_SUBDIVISION_RAISED` (info) -- docs/tool-surface.md advisories table: "`-m` raised to
    meet an LM-83 minimum". States the value actually used, per the task brief: "the manifest
    records the value actually used."

    **Widened at Phase 7c Task 11 to name sec 2.3.1-2's solar-position clause alongside sec
    2.3.2's patch-count one, rather than allocating a second code.** The register row already covers
    *being raised to meet an LM-83 requirement*; what it could not do was say *which* requirement,
    and a message that named the 2 305-patch clause for a raise that clause did not cause would be
    an advisory untrue of its own condition -- the defect docs/tool-surface.md section 9 names.
    The two flags are the same two `resolve_lm83_sky_subdivision` took, forwarded rather than
    re-derived, so the sentence and the arithmetic cannot disagree about which floor applied.

    **A clause is named only if it actually caused the raise** -- Task 11 review, the Minor. The
    first cut named every clause that *applied to the run*, which is a different set: at
    `requested=2` under both flags the value is raised to 4 by sec 2.3.2 alone, because 2 already
    clears sec 2.3.1-2's floor -- and the message named 2.3.1-2 anyway, in a sentence that then
    went on to say the requirement "clears from sky_subdivision=2 upward". An advisory listing a
    reason for a raise that reason did not cause is untrue of its own condition, which
    docs/tool-surface.md section 9 makes a defect in its own right, and the register row publishes
    the falsifiable claim that this names *"whichever clause or clauses actually raised the
    value"*. So each clause is gated on `requested` falling below **its own** floor, which is the
    same comparison `resolve_lm83_sky_subdivision` makes.

    `requested=None` means "no caller preference", and only the sec 2.3.2 clause substitutes for it
    (see the resolver): a `None` under `direct_sun_leg` alone is passed through unraised, so this
    function is never called for it, and a `None` under `lm83_metric` is raised by sec 2.3.2 and by
    nothing else.

    **The clauses are `edition`'s (R864, R866)**, each from `rad_core.lm83_edition.section`, so an
    LM-83-22 run cannot carry an LM-83-12 clause number. `lm83_metric` is an LM-83 *run* -- `sda` or
    `ase` in `metrics`, or `blind_control="lm83"` (R866) -- and the sentence says so rather than
    "an LM-83 metric", which a blended `metrics=["da"]` run does not have. Under LM-83-22 the floor
    stays 4 and is cited to section 4.12.1's 2,305 patches for the direct solar contribution: this
    server's suns share the sky's subdivision, so section 4.12.2's 145-patch sky floor is not open
    to it. The same section asks for a solar position under 1 degree, which no subdivision this
    server builds meets (Still open item 54); the message says so. The 5-degree solar-position
    floor is LM-83-12 section 2.3.1's, so under LM-83-22 the second clause names it as this
    server's floor, not as that edition's requirement.

    Built directly, never from a stderr line (`lines=()`), the same convention `sky.
    df_sky_forced_advisory` uses for a finding proven by this module's own decision.
    """
    lm83_22 = edition == LM83_22
    sky_clause = section("sky_patches", edition)
    clauses: list[str] = []
    if lm83_metric and (requested is None or requested < LM83_MIN_SKY_SUBDIVISION):
        clauses.append(
            "this is an LM-83 run (sda or ase in metrics, or blind_control='lm83'), and "
            + (
                f"{sky_clause} asks for no fewer than {LM83_MIN_REINHART_PATCHES} sky patches for "
                "the direct solar contribution, which this server's suns share with the sky (the "
                "same section's solar position under 1 degree is met at no subdivision this "
                "server builds: Still open item 54; that 1 degree, beside the same section's "
                f"{LM83_MIN_REINHART_PATCHES} patches, is a value not yet checked against the printed standard, and the "
                "two disagree, since this server's own patch-radius arithmetic puts "
                f"{LM83_MIN_REINHART_PATCHES} patches at about 1.7 degrees)"
                if lm83_22 else
                f"{sky_clause} requires a minimum of {LM83_MIN_REINHART_PATCHES} Reinhart sky "
                "patches"
            )
        )
    if (
        direct_sun_leg
        and requested is not None
        and requested < LM83_MIN_SUN_POSITION_SUBDIVISION
    ):
        clauses.append(
            "the pre-blind direct-sun leg is planned, and "
            + (
                "this server holds the simulated solar position within "
                f"{LM83_SOLAR_POSITION_TOLERANCE_DEG:g} degrees of the actual one"
                if lm83_22 else
                f"{section('solar_position', edition)} requires the simulated solar position "
                f"within {LM83_SOLAR_POSITION_TOLERANCE_DEG:g} degrees of the actual one"
            )
            + " -- one sun per Reinhart patch clears that from "
            f"sky_subdivision={LM83_MIN_SUN_POSITION_SUBDIVISION} upward"
        )
    if not clauses:
        # Unreachable from `resolve_lm83_sky_subdivision`, which only reports `raised=True` when one
        # of these two flags is set -- but a caller that got here with neither would produce
        # "sky_subdivision=1 was requested and . Raised to ...", an advisory announcing a raise it
        # cannot attribute to any clause. Refused rather than emitted: an advisory that names no
        # reason is the shape docs/tool-surface.md section 9 calls worse than no advisory.
        raise ValueError(
            f"sky_subdivision_raised_advisory was called with requested={requested}, "
            f"lm83_metric={lm83_metric} and direct_sun_leg={direct_sun_leg}, and no LM-83 clause "
            "applies a floor above that request -- so there is nothing to name as the reason for "
            "the raise. Pass the same requested value and the same two flags that were passed to "
            "resolve_lm83_sky_subdivision, and call this only when it answered raised=True."
        )
    # **`requested=None` is not a raise** (Task 4 fix round 1): every path resolves an omitted
    # subdivision to the floor, so that sentence states the value used and the floor, and claims no
    # raise. The code and the runs that carry it are unchanged.
    if requested is None:
        message = (
            f"no sky_subdivision was requested, so this run uses sky_subdivision={used} "
            f"(gendaymtx -m {used}), the LM-83 floor: " + " and ".join(clauses) + "."
        )
    else:
        message = (
            f"sky_subdivision={requested} was requested and " + " and ".join(clauses) + ". "
            f"Raised to sky_subdivision={used} (gendaymtx -m {used})."
        )
    return EngineAdvisory(
        code="SKY_SUBDIVISION_RAISED", severity="info", message=message, lines=()
    )


def ground_reflectance_inconsistent_advisory(
    *, project_value: float, modelled_value: float
) -> EngineAdvisory:
    """`GROUND_REFLECTANCE_INCONSISTENT` (error) -- docs/tool-surface.md#ground-reflectance-is-one-
    project-value-passed-to-every-sky-generator: a project whose modelled ground material and
    project ground reflectance disagree by more than 0.02, naming both values; "the number is not
    usable because it is not clear which ground produced it."

    The 0.02 tolerance comparison itself is the caller's responsibility (a project-level comparison
    outside this module's ownership) -- this function only builds the message once a caller has
    already decided the two values disagree, matching every other advisory builder in this module.
    """
    return EngineAdvisory(
        code="GROUND_REFLECTANCE_INCONSISTENT",
        severity="error",
        message=(
            f"the project ground reflectance ({project_value:g}) and the modelled ground material's "
            f"reflectance ({modelled_value:g}) disagree by more than 0.02. The sky matrix radiates "
            f"{project_value:g} (gendaymtx -g) while the exterior octree's ground polygon reflects "
            f"{modelled_value:g}, and it is not clear which ground produced any result computed "
            "from them."
        ),
        lines=(),
    )


def north_convention_advisory(*, north_east_of_north: float, gendaymtx_r_flag: float) -> EngineAdvisory:
    """`NORTH_CONVENTION` (info), always emitted, stating both numbers -- docs/tool-surface.md:
    "north is stated degrees_east_of_north ... while gendaymtx's own -r rotates west of north ...
    NORTH_CONVENTION at info, always emitted, stating both numbers. ... this project has already
    been bitten once by exactly that class of error."

    `gendaymtx_r_flag` is the caller's already-computed `-r` value (i.e. `-north_east_of_north`,
    what `north_rotation_flag` returns as a string) -- passed in rather than recomputed here so the
    advisory always states the exact number that reached the argv, not a value this function derived
    a second time from the same input by a different path.
    """
    return EngineAdvisory(
        code="NORTH_CONVENTION",
        severity="info",
        message=(
            f"north={north_east_of_north:g} (degrees east of north, the surveying convention) was "
            f"rotated to gendaymtx -r {gendaymtx_r_flag:g} (degrees west of north, gendaymtx's own "
            "convention: 'the -r option rotates the sky the specified number of degrees "
            "counter-clockwise about the zenith, i.e., west of north')."
        ),
        lines=(),
    )
