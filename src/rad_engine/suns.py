"""The suns scene: `1 + 144*MF^2` discrete `source` primitives, one per Reinhart sky patch, that
make the 5-phase method's direct term (`Cds*Ssun`, `docs/specs/five-phase-method.md` sec 4/5)
possible at all -- a matrix method treats direct sun as one more diffuse basis, and this module is
what gives that basis its own named, individually-traceable light for every patch.

Task 7 of Phase 7a (docs/plan.md, "Phase 7a — the 5-phase chain"). Consumes
`rad_engine.skymatrix.SOLAR_DISK_DEGREES` (Task 6) for the one number this scene shares with the sun
*matrix* -- see that constant's own docstring: "the sun matrix distributes each hour's direct-normal
irradiance over a disk of this angular size, and the suns scene's point sources must claim the
identical solid angle -- two literals here would describe two different suns feeding the same
5-phase sum." This module imports it rather than writing a second `0.533`.

## Two spawns, not a shell pipe -- `build_suns_argv` returns both argvs, never joins them

`docs/specs/five-phase-method.md` sec 4.1 writes the suns scene as a shell pipe:

    echo "void light solar 0 0 3 1e6 1e6 1e6" > skies/suns.rad
    cnt 5185 | rcalc -e MF:6 -f reinsrc.cal -e Rbin=recno \\
      -o 'solar source sun${Rbin} 0 0 4 ${Dx} ${Dy} ${Dz} 0.533' >> skies/suns.rad

`cnt` and `rcalc` are two separate binaries, and this codebase never spawns a shell to join them --
the same division `rad_engine.matrices.build_genklemsamp_argv`/`build_d_argv` hold for
`genklemsamp | rcontrib`, and for the identical reason (that pair's own module docstring): a shell
pipe is a third process nothing here declares, and it hides both children's stderr behind one exit
status. `build_suns_argv` returns `(cnt_argv, rcalc_argv)`; a caller runs `cnt_argv`, threads its
stdout into `rcalc_argv`'s stdin (two `rad_engine.run.run` calls, matching
`tests/test_matrices.py::_run_dmatrix_pipe`'s own two-spawn shape for the D-matrix pipe), and
prepends `SOLAR_LIGHT_PRIMITIVE` to what it captures before writing a scene file. Neither builder
resolves a binary path or spawns anything itself -- the same contract every `build_*_argv` in this
codebase holds.

`cnt N` (`man cnt`: "counts from 0 to N-1, producing N lines of output") supplies `rcalc`'s input
records; `rcalc`'s own `recno` built-in ("the number of records recognized thus far") is 1-indexed
regardless of `cnt`'s own 0-indexed values, which is why `Rbin=recno` names the first sun `sun1`, not
`sun0` -- verified directly on this machine (module docstring's own measurement below).

## `-e 'MF:{mf}'` must precede `-f reinsrc.cal` -- the same load-bearing order as `reinhart.cal`

`reinsrc.cal` reads `MF` when `-f` is parsed, not lazily when `Dx`/`Dy`/`Dz` are evaluated -- the
identical fact `rad_engine.matrices.build_d_argv`'s own docstring states and measures for
`reinhart.cal` (`echo 0 | rcalc -e 'MF:1' -f reinhart.cal -e '$1=Nrbins'` succeeds; reversing the two
flags fails with `MF: undefined variable`). `-e`/`-f` execute left to right as `rcalc` encounters them
on the command line, so `build_suns_argv` places `-e f"MF:{mf}"` immediately before
`-f reinsrc.cal`, and `tests/test_suns.py` asserts the two tokens are adjacent in that order.

## `sun_count` is one less than `reinhart_bins` -- derived, not a second formula

**5185 and 5186 are both correct and are not the same quantity.** `sun_count(mf)` is `1 + 144*mf^2`
-- the Reinhart *sky*-patch count, the `+1` being the single zenith cap patch every Reinhart
subdivision carries. `rad_engine.matrices.reinhart_bins(mf)` is `Nrbins`, the *matrix* row count,
which adds one further row for the ground patch that `reinhart.cal` appends on top of the sky
subdivision (that function's own docstring, and `docs/tool-surface.md` verified fact 3). No sun ever
occupies the ground patch -- there is no such thing as a "ground sun" -- so the suns scene has
exactly one *fewer* source than `Nrbins`, never the same count.

`sun_count` is written as `reinhart_bins(mf) - 1`, not as an independently re-derived
`144*mf*mf + 1`. `reinhart_bins` is itself the *measured* table (`rad_engine.matrices`' own module
docstring: "a measured fact about `reinhart.cal`, carried as data, not a formula this module derived
and hopes matches the `.cal` file's own definition"); subtracting one from that measured value, rather
than writing a second closed-form expression that happens to agree with it today, is what keeps the
two counts from being able to drift apart silently if a future `mf` were ever measured wrong in one
place and not the other. **Whoever next reads this code must not "simplify" `sun_count` and
`reinhart_bins` into one shared formula** -- the module docstring of `rad_engine.matrices` and this
module's own tests (`test_sun_count_is_the_sky_patch_count_without_the_ground`) exist specifically so
that collapsing them is caught, not merely discouraged in prose.

`docs/specs/five-phase-method.md` sec 4.1 and `docs/tool-surface.md`'s own verified-facts table (fact
3) both record that the specs write `-bn 145`/`cnt 5185` as sourced literals -- `cnt 5185` is in fact
*correct* for the sun count (unlike the D-matrix specs' `-bn 145`/`-bn 5185`, which are one short of
`Nrbins`), because `cnt` here counts *suns*, not Reinhart matrix rows. This module never writes
either count as a literal into an argv regardless: `build_suns_argv` calls `sun_count(mf)` itself
rather than trusting a caller to pass the right number for the right reason.

## Measured against Radiance 6.1a on this machine, 2026-08-17

    printf 'void light solar 0 0 3 1e6 1e6 1e6\\n' > suns.rad
    cnt 5 | rcalc -e 'MF:1' -f reinsrc.cal -e 'Rbin=recno' \\
      -o $'solar source sun${Rbin}\\n0\\n0\\n4 ${Dx} ${Dy} ${Dz} 0.533' >> suns.rad

produced, stderr empty, exit 0:

    void light solar 0 0 3 1e6 1e6 1e6
    solar source sun1
    0
    0
    4     0 0.994522 0.104528 0.533
    solar source sun2
    0
    0
    4 0.206773 0.972789 0.104528 0.533
    ...

`-o`'s format string carries its own embedded newlines (`\\n0\\n0\\n4 ...`) rather than the spec's
single-line `-o '... 0 0 4 ... 0.533'` -- both are valid Radiance primitive syntax (a `source`
primitive is whitespace-delimited, not newline-delimited: `void light solar 0 0 3 1e6 1e6 1e6` on one
line is exactly as valid as the same tokens spread across four), but the multi-line form is what makes
each `solar source sunN` name land on a line of its own, which is what check 7.2's own
`^solar source (sun\\d+)$` line-anchored assertion needs to find it. `rcalc -o` interpolates
`${Rbin}`/`${Dx}`/`${Dy}`/`${Dz}` as named numeric fields (`man rcalc`: "This is a numeric field:
${vname}"), and the trailing `0.533` is `repr(SOLAR_DISK_DEGREES)`, the same constant Task 6's sun
*matrix* (`gendaymtx -5`) carries -- one physical quantity, formatted once.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from rad_core.apertures import Aperture

from .env import RadianceEnv
from .matrices import reinhart_bins
from .run import run
from .scene import _walk_primitives
from .skymatrix import SOLAR_DISK_DEGREES

__all__ = [
    "SOLAR_LIGHT_PRIMITIVE",
    "SUNS_MODIFIER",
    "SunPosition",
    "build_suns_argv",
    "exterior_elevation_deg",
    "clear_sun_direction_cache",
    "exterior_normal",
    "parse_sun_directions",
    "sun_count",
    "sun_directions",
    "suns_in_hemisphere",
    "suns_scene_filename",
    "suns_scene_text",
]

#: The one `void light` primitive every suns scene opens with -- `docs/specs/five-phase-method.md`
#: sec 4.1's own `echo "void light solar 0 0 3 1e6 1e6 1e6"`. `SUNS_MODIFIER` (`"solar"`) is what
#: every `source sunN` primitive `build_suns_argv`'s `rcalc` half writes is modified by; `1e6 1e6 1e6`
#: (R, G, B) is the spec's own value -- an unphysically bright neutral white, deliberately: the 5-phase
#: method scales each sun's actual contribution later via the `Cds` coefficient matrix, so this
#: primitive's own brightness is a placeholder unit the coefficients multiply, not a modelled radiance.
SOLAR_LIGHT_PRIMITIVE: Final[str] = "void light solar 0 0 3 1e6 1e6 1e6"

#: The modifier name every `source sunN` primitive in the scene is declared under -- the identifier
#: `SOLAR_LIGHT_PRIMITIVE` itself introduces (`void light solar ...`). A caller assembling the scene
#: text checks a written primitive's modifier against this rather than the bare string `"solar"`, the
#: same "one named constant, not two independently-typed literals" discipline this module's own
#: docstring states for `SOLAR_DISK_DEGREES`.
SUNS_MODIFIER: Final[str] = "solar"

#: `rcalc -o`'s output-format argument for the suns scene's `source` primitives -- one `%g`-formatted
#: literal for the angular size (`SOLAR_DISK_DEGREES`, `repr`'d for the same round-trip reason
#: `rad_engine.matrices._number`/`rad_engine.klems._number` `repr` their own floats), embedded
#: newlines so each `solar source sunN` name lands on its own line (module docstring's own measurement
#: of why this differs from the spec's single-line form).
_SUNS_OUTPUT_FORMAT: Final[str] = (
    "solar source sun${Rbin}\n0\n0\n4 ${Dx} ${Dy} ${Dz} " + repr(SOLAR_DISK_DEGREES)
)


def sun_count(mf: int) -> int:
    """The number of discrete suns for Reinhart subdivision `mf`: `1 + 144*mf^2`, the sky-patch count
    (the `+1` is the single zenith cap), **one fewer** than `rad_engine.matrices.reinhart_bins(mf)`
    (`Nrbins`), which adds a further ground-patch row no sun occupies.

    Computed as `reinhart_bins(mf) - 1`, not as an independently re-derived `144*mf*mf + 1` -- see the
    module docstring's own section on why: `reinhart_bins` is the *measured* table, and subtracting
    one from a measured value (rather than writing a second formula that happens to agree with it
    today) is what keeps the sun count and the bin count from being able to drift apart if either is
    ever revised in only one place.

    Raises `ValueError` for an `mf` `reinhart_bins` has not measured -- the same refusal, for the same
    reason, that function's own docstring gives.
    """
    return reinhart_bins(mf) - 1


def build_suns_argv(mf: int) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """The two spawns that write the suns scene's `source` primitives: `cnt sun_count(mf)` and the
    `rcalc` that formats each of its records into one named sun.

    ``cnt sun_count(mf)`` -- one record per sun, `cnt`'s own 0-indexed output (`man cnt`) consumed
    only as `rcalc`'s record count; `rcalc`'s `recno` (1-indexed) is what actually numbers the suns.

    ``rcalc -e 'MF:{mf}' -f reinsrc.cal -e 'Rbin=recno' -o '<format>'`` -- `-e 'MF:{mf}'` precedes
    `-f reinsrc.cal`, and that order is load-bearing exactly as it is for `reinhart.cal` in
    `rad_engine.matrices.build_d_argv` (module docstring): `reinsrc.cal` reads `MF` when `-f` is
    parsed, not lazily. `-o` is `_SUNS_OUTPUT_FORMAT`, which carries `SOLAR_DISK_DEGREES` (imported
    from `rad_engine.skymatrix`, never a second `0.533` literal) as the fixed trailing angular-size
    field, and `${Rbin}` as the name each sun is uniquely numbered by.

    **Two spawns, not a shell pipe** -- the module docstring's own citation of
    `rad_engine.matrices.build_genklemsamp_argv`/`build_d_argv`: a caller runs `cnt`'s argv, threads
    its stdout into `rcalc`'s stdin as a second, separate `rad_engine.run.run` call, and never joins
    them with a shell pipe, which would hide each child's own stderr behind one exit status.

    Returns two plain argvs, **without** either binary name, the same contract every `build_*_argv`
    in this codebase holds -- a caller substitutes `env.path("cnt")`/`env.path("rcalc")` for that
    first token.
    """
    cnt_argv = ("cnt", str(sun_count(mf)))
    rcalc_argv = (
        "rcalc",
        "-e", f"MF:{mf}",
        "-f", "reinsrc.cal",
        "-e", "Rbin=recno",
        "-o", _SUNS_OUTPUT_FORMAT,
    )
    return cnt_argv, rcalc_argv


def suns_scene_filename(mf: int) -> str:
    """The suns scene's own filename for subdivision `mf`: `suns_mf{mf}.rad`.

    **Derived from `mf` because the file's *content* is derived from `mf`** -- `rad_engine.annual.
    dc_receiver_filename`'s reasoning, applied to the second submit-time scene file this repo writes
    whose bytes depend on a caller argument. That function's own history is the whole argument:
    Phase 7a Task 5 parameterised the 2-phase receiver's content by `mf` without parameterising its
    path, and review measured what one shared `receiver.rad` re-opened -- two ordinary `run_annual`
    calls at different subdivisions, one overwriting the file the other's job was about to read,
    with no concurrency needed because both writes sit above the `dry_run` branch.

    The suns scene has the identical shape: `build_suns_argv(mf)` emits `1 + 144*mf^2` sources, so
    an `mf=1` scene and an `mf=6` scene are different files, and `rad_mcp.annual_tool` writes it at
    submit and compiles it into the suns octree a moment later. Two calls at different subdivisions
    therefore write two paths and cannot collide by construction; two calls at the *same* `mf` still
    share one path and stay benign for `dc_receiver_filename`'s own reason -- the text is a pure
    function of `mf`, so both writers write identical bytes.

    A bare filename, not a path: which directory it lands in is the caller's decision (and the
    caller's containment check), exactly as `dc_receiver_filename`'s is.
    """
    return f"suns_mf{mf}.rad"


def suns_scene_text(
    env: RadianceEnv, mf: int, *, cwd: Path | str, timeout: float | None = None
) -> str:
    """The whole suns scene as text: `SOLAR_LIGHT_PRIMITIVE`, then one named `source` per Reinhart
    sky patch -- the two spawns `build_suns_argv` describes, run and threaded, and nothing written.

    **Two spawns, never a shell pipe** (module docstring): `cnt sun_count(mf)`'s stdout is captured
    through `rad_engine.run.run` and handed to `rcalc` as its `stdin` on a second `run` call, so each
    child's stderr stays separately attributable and no third process exists that nothing declared.
    `cnt` itself is given an empty stdin rather than the caller's -- it reads none (`man cnt`: it
    counts), and leaving stdin undeclared is what this codebase's own rule forbids.

    **This function spawns; it does not write.** `rad_engine.klems.sample_sky_visibility` is the
    established precedent for a spawn in this layer, and the split is the same one every scene text
    in this repo holds: the engine produces bytes, and the protocol layer writes them at a path it
    has contained (ruling R48's primitive raises `rad_mcp.paths.PathOutsideRoot`, a wire type this
    layer may not name). `suns_scene_filename(mf)` above is the filename that write should use.

    `timeout` bounds each spawn independently, as `rad_engine.octree._build_black_backed_octree`'s
    own does and for the same stated reason: `cnt` and `rcalc` do different work, and halving a
    caller's bound would starve whichever turns out to be slower. Raises `RadianceError` from either
    spawn (`check=True`), and `ValueError` from `sun_count` for an `mf` `reinhart_bins` has never
    measured -- both before any file exists, because this function creates none.
    """
    cnt_argv, rcalc_argv = build_suns_argv(mf)
    counted = run(env, list(cnt_argv), cwd=cwd, stdin="", timeout=timeout, check=True)
    formatted = run(
        env, list(rcalc_argv), cwd=cwd, stdin=counted.stdout, timeout=timeout, check=True
    )
    return SOLAR_LIGHT_PRIMITIVE + "\n" + formatted.stdout


# --------------------------------------------------------------------------------------------
# Sun geometry against an aperture -- Phase 7d-1 Task 18
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class SunPosition:
    """One sun direction, in the **scene's own** horizontal frame: +X east, +Y north, +Z up.

    `azimuth_deg` is **degrees east of north**, the identical convention
    `rad_core.apertures.Aperture.azimuth_deg` publishes -- 0 is north, 90 east, 180 south. The two
    are compared against each other by `suns_in_hemisphere`, so writing them in two conventions
    would be a silent rotation, which is the defect class ruling R68 was raised for.

    **The frame is the scene's, not true north's.** `rad_engine.chain.ChainRequest.north` rotates
    the *sky* relative to the model; an aperture's `azimuth_deg` is read off model coordinates and
    knows nothing about it. A caller that supplies sun positions computed for true north on a model
    whose `north` is not 0 must rotate them into model coordinates first. This type carries no
    `north` of its own precisely so that responsibility cannot be silently split between two
    places -- there is one frame here, and it is the one the polygons are in.

    `altitude_deg` is degrees above the horizon and may be negative. `suns_in_hemisphere` does not
    filter on it (see that function): a sun below the horizon is still a well-defined direction,
    and which suns belong in the list at all is the caller's question, not this module's.
    """

    altitude_deg: float
    azimuth_deg: float

    @property
    def direction(self) -> tuple[float, float, float]:
        """The unit vector pointing **from the scene towards the sun**, in scene coordinates.

        `(cos(alt)*sin(az), cos(alt)*cos(az), sin(alt))` -- `sin` on the *east* component and `cos`
        on the *north* one, because the angle is measured **from north towards east**, which is
        `atan2(x, y)` and not the mathematician's `atan2(y, x)`. `rad_core.apertures` derives
        `azimuth_deg` with exactly that `math.atan2(exterior_normal[0], exterior_normal[1])`, so
        these two expressions are inverses of each other and are asserted to be in
        `tests/test_suns.py::TestSunPositionDirection`.
        """
        altitude = math.radians(self.altitude_deg)
        azimuth = math.radians(self.azimuth_deg)
        horizontal = math.cos(altitude)
        return (
            horizontal * math.sin(azimuth),
            horizontal * math.cos(azimuth),
            math.sin(altitude),
        )


#: The dot product below which a sun counts as **grazing** the aperture rather than lying in front
#: of it: `1e-9`, i.e. a cosine of incidence of one part in a billion, 89.999999942 degrees off the
#: exterior normal.
#:
#: **Not `0.0`, because trigonometry in binary does not return 0.** Measured on this machine
#: (CPython 3.12): `math.cos(math.radians(90))` is `6.123233995736766e-17`, so a due-east sun
#: against a due-north-facing aperture -- geometrically *exactly* in its plane -- dots to
#: `+6.123233995736766e-17`, and `> 0.0` counts it as being in the hemisphere.
#:
#: **Which way it errs, stated rather than left to be inferred.** A sun excluded by this threshold
#: has a cosine of incidence below `1e-9`; the beam it delivers through the aperture is that
#: fraction of its normal irradiance, i.e. nothing at any irradiance a weather file can carry.
#: Excluding it makes `rad_engine.project`'s `asemx` guard slightly *more* willing to seal, and the
#: quantity traded away is `1e-9` of one sun's contribution. The opposite choice -- rounding the
#: residue up into "a sun is in front of this aperture" -- would refuse a run over a number that is
#: an artefact of `math.radians`. Too HIGH and a real low-angle beam is discarded as grazing, which
#: makes that guard seal a matrix it should have refused; the upper end is pinned by
#: `tests/test_suns.py::...::test_a_real_low_angle_beam_is_not_discarded_as_grazing`, which asserts
#: a sun 1 degree off the aperture plane (dot 0.01745) counts.
#:
#: **A substituted default, not a measured value**: only the 6.12e-17 floor is measured, and the
#: bound itself is recorded in docs/tool-surface.md's "Unsourced values" table with what would
#: establish it -- the same posture `rad_core.geometry._MIN_EDGE_LENGTH_M` holds for its own floor.
_GRAZING_DOT: Final[float] = 1e-9


def exterior_normal(aperture: Aperture) -> tuple[float, float, float]:
    """`-aperture.outward_normal`: the unit vector pointing **away from the room**, out through the
    facade.

    **The one place this module writes this sign -- and it is NOT the only place in the repo.**
    `rad_core.apertures`' module docstring fixes `outward_normal` as the **room-facing** vector --
    the one `kbin` needs, measured on this machine -- while `azimuth_deg` is derived from its
    negation because an azimuth names which way the *facade* looks. Conflating the two inverts by
    exactly 180 degrees, which is the Phase 6a whole-branch review's finding and ruling R68.

    Every consumer *in this module* goes through this function rather than negating in place, so
    there is one site to get wrong here instead of one per caller. **Three** other sites in `src/`
    negate the same field for their own reasons and are deliberately not folded in here:
    `rad_core.apertures.derive` (the `xy` half of it, to build `azimuth_deg`; `rad_core` cannot
    import `rad_engine` to reach this function), `rad_engine.klems.sample_sky_visibility`, and
    `rad_engine.matrices.build_genklemsamp_argv` (`genklemsamp -vd`, sampling out through the
    facade -- the only one of the three that negates all three components inline in an argv). All
    three are known and all three are the whole-branch review's, not this task's. **Anyone counting
    on "one site" repo-wide is counting on something that is not true.**

    **That count is a claim, so it is ratcheted and not merely asserted.** Fix round 3 replaced a
    false "the one place this sign is written" with a false "two other sites" -- a retraction of an
    over-general count that published an over-general count. A grep for the *sentence* cannot see
    the *sites the sentence counts*, because those sites contain none of the swept words.
    `tests/test_suns.py::TestEveryNegationOfAnApertureNormalIsEnumerated` walks the AST of every
    module under `src/` instead, and enumerates them: a fourth one added later reddens this
    docstring rather than quietly outdating it.
    """
    return (
        -aperture.outward_normal[0],
        -aperture.outward_normal[1],
        -aperture.outward_normal[2],
    )


def exterior_elevation_deg(aperture: Aperture) -> float:
    """The **signed** elevation of the aperture's exterior normal: `+90` for one facing straight up,
    `0` for a vertical wall, `-90` for one facing straight down.

    **This exists because `Aperture.tilt_deg` cannot distinguish the two ends.** `apertures.derive`
    computes tilt as `acos(abs(outward_normal[2]))`, and the `abs()` is sign-blind: a down-facing
    floor light and an up-facing skylight both report `tilt_deg == 0.0`, and both report
    `azimuth_deg == 180.0` -- which is an `atan2(-0.0, -0.0)` artifact, not an orientation. They are
    opposite apertures with opposite answers here (0 dome directions in hemisphere versus all of
    them), so a message or a branch that explained itself in terms of `tilt_deg` would be describing
    a quantity that cannot tell its two sides apart.

    `asin` is clamped into `[-1, 1]` against float residue from the normal's own division, the same
    guard `rad_core.apertures` puts on its own `acos`.
    """
    return math.degrees(math.asin(max(-1.0, min(1.0, exterior_normal(aperture)[2]))))


def suns_in_hemisphere(aperture: Aperture, suns: Sequence[SunPosition]) -> int:
    """How many of `suns` lie in `aperture`'s **exterior** hemisphere: the count of directions
    whose dot product with `-aperture.outward_normal` is strictly positive.

    **The sign is the whole function, and it is the trap.** `rad_core.apertures`' own module
    docstring fixes `outward_normal` as the **room-facing** vector -- the one `kbin` needs,
    measured on this machine -- while `azimuth_deg` is derived from the **exterior**-facing
    `-outward_normal` (`apertures.derive`), because an azimuth names which way the *facade* looks.
    A hemisphere test written against `outward_normal` reports a north-facing aperture as sunlit in
    Athens and a south-facing one as never sunlit, inverted by exactly 180 degrees -- the Phase 6a
    whole-branch review's finding one module over, ruling R68 -- and **every test written against
    the same mistake passes**, which is why `tests/test_suns.py` asserts the fixture apertures'
    `outward_normal` and `azimuth_deg` separately before it asserts the count.

    **The boundary is `> _GRAZING_DOT`, and `> 0.0` does not work** -- measured, not assumed. A sun
    exactly in the aperture's own plane has a zero cosine of incidence and delivers no beam through
    it whatever its irradiance, so it must not count. But its dot product is not 0 in binary:
    `math.cos(math.radians(90))` is `6.123233995736766e-17` on this machine, so a due-east sun
    against a north-facing aperture dots to `+6.12e-17` and a bare `> 0.0` counts it. The first
    version of this function did exactly that and
    `test_a_sun_exactly_in_the_apertures_own_plane_is_not_in_its_hemisphere` caught it. See
    `_GRAZING_DOT` for the threshold and which way it errs.

    **No altitude filter, deliberately.** This answers about the *aperture's* hemisphere and
    nothing else; a sun below the horizon is still a direction, and whether such a sun belongs in
    the list is the caller's question. Two filters in two modules is how one of them ends up
    unowned.

    **An empty `suns` returns 0, and 0 is not evidence of anything.** *"Nothing was supplied"* and
    *"nothing is in the hemisphere"* are the same integer here, and a caller that concludes
    geometry from it would be concluding it from an empty list. `rad_engine.project`'s `asemx`
    guard refuses an empty list outright rather than reading this 0; the arithmetic is left plain
    here so that refusal has one obvious place to live.
    """
    exterior = exterior_normal(aperture)
    count = 0
    for sun in suns:
        x, y, z = sun.direction
        if x * exterior[0] + y * exterior[1] + z * exterior[2] > _GRAZING_DOT:
            count += 1
    return count


# --------------------------------------------------------------------------------------------
# Reading the dome back out of the scene -- Phase 7d-1 Task 8, ruling R144
# --------------------------------------------------------------------------------------------


#: `sun_directions`' parsed domes, keyed by `(bin_dir, version, mf)`.
#:
#: **Keyed on the install as well as `mf`** because the table is `reinsrc.cal`'s, not this module's:
#: two Radiance installs are two files, and a cache keyed on `mf` alone would serve the first
#: install's dome to the second. Not `functools.lru_cache` over the `RadianceEnv` itself --
#: `RadianceEnv` is `eq=False`, so it hashes by identity and a caller that builds a fresh one per
#: request (which `rad_mcp` does) would miss the cache on every call while filling it for ever.
#:
#: Concern 7 of Task 18's report is what this is for: `rad_engine.project`'s `asemx` guard needs the
#: whole dome at every seal, and the dome costs a `cnt | rcalc` pair over `1 + 144*mf^2` records.
_SUN_DIRECTION_CACHE: dict[tuple[str, str | None, int], tuple[SunPosition, ...]] = {}


def clear_sun_direction_cache() -> None:
    """Empty `_SUN_DIRECTION_CACHE`. For tests that count spawns; nothing in production calls it."""
    _SUN_DIRECTION_CACHE.clear()


def parse_sun_directions(scene_text: str) -> tuple[SunPosition, ...]:
    """Every `source` primitive in a suns scene, read back as a `SunPosition`.

    **The inverse of `_SUNS_OUTPUT_FORMAT`, and deliberately the same bytes.** `build_ase_octree`
    compiles the *written* suns scene, and `rad_engine.project`'s `asemx` guard is only sound over
    the set the matrix was built over -- so the directions it reasons from are read out of that same
    text rather than obtained from a second `rcalc` spawn with a numeric `-o`. A second derivation
    of the Reinhart table is a second thing to drift, and the drift would be silent: both tables are
    the right length and both look like a dome.

    **`_walk_primitives`, not a regex** (ruling R30): `rad_engine.scene` already owns the
    string/int/float argument-count arithmetic that says where a Radiance primitive ends, and this
    is its third consumer. A regex here would be a fourth reading of the same grammar, and the one
    that has to cope with `void light solar 0 0 3 1e6 1e6 1e6` sitting in the same file.

    **The `light` primitive is not a sun.** `SOLAR_LIGHT_PRIMITIVE` declares the modifier the
    sources are *under*; its three arguments are RGB radiance. Only `source` primitives are read,
    and only those modified by `SUNS_MODIFIER` -- which is what `rcontrib -m solar` bins, so a
    `source` under any other modifier is not in the matrix at all and counting it would let the
    guard's `len(suns) == sun_count(mf)` check pass over a set the matrix was not built from.

    Raises `ValueError` for a `source` under another modifier, for one whose direction is the zero
    vector (no direction at all, and `asin` of the normalised `z` is undefined), and -- through
    `_walk_primitives` -- for malformed text.

    Directions are normalised before the angles are taken: `reinsrc.cal` emits unit vectors, and a
    non-unit one would otherwise put `asin(z)` at the wrong altitude rather than raise.
    """
    suns: list[SunPosition] = []
    for primitive in _walk_primitives(scene_text):
        if primitive.ptype != "source":
            continue
        if primitive.modifier != SUNS_MODIFIER:
            raise ValueError(
                f"the suns scene declares source {primitive.identifier!r} under modifier "
                f"{primitive.modifier!r}, not {SUNS_MODIFIER!r}. rcontrib bins the direct beam by "
                f"-m {SUNS_MODIFIER}, so a source under any other modifier contributes no column to "
                "the ASE matrix -- reading it as one of the dome's directions would describe a set "
                "the matrix was not built over."
            )
        x, y, z = primitive.floats[0], primitive.floats[1], primitive.floats[2]
        norm = math.sqrt(x * x + y * y + z * z)
        if norm == 0.0:
            raise ValueError(
                f"source {primitive.identifier!r} in the suns scene has the zero vector as its "
                "direction, which is not a direction. A sun with no direction has no altitude and "
                "no azimuth, and Radiance's own -ab 0 trace would find it nowhere."
            )
        x, y, z = x / norm, y / norm, z / norm
        suns.append(SunPosition(
            altitude_deg=math.degrees(math.asin(max(-1.0, min(1.0, z)))),
            # `atan2(x, y)`, east over north -- degrees east of north, the convention
            # `SunPosition.azimuth_deg` and `rad_core.apertures.Aperture.azimuth_deg` both hold.
            # The mathematician's `atan2(y, x)` is the same number rotated 90 degrees, which is
            # ruling R68's defect class and is silent in both directions.
            azimuth_deg=math.degrees(math.atan2(x, y)) % 360.0,
        ))
    return tuple(suns)


def sun_directions(
    env: RadianceEnv, mf: int, *, cwd: Path | str, timeout: float | None = None
) -> tuple[SunPosition, ...]:
    """The Reinhart patch centres subdivision `mf`'s suns scene puts a `source` at -- **the whole
    dome**, in the scene's own frame.

    Ruling R144's source, and the one `rad_engine.project`'s `asemx` guard may reason from: it runs
    `suns_scene_text` (the identical two spawns whose output `rad_mcp.annual_tool` writes and
    `build_ase_octree` compiles) and reads the directions back out with `parse_sun_directions`.

    **The full dome, never a subset.** `register_artifact` refuses the sun geometry unless
    `len(suns) == sun_count(mf)`, and that check is what makes the seal sound -- a filtered or
    deduplicated list would be a claim about a quantity the matrix does not contain. This function
    therefore filters nothing: every direction, including the ones behind whatever aperture the
    caller is about to ask about.

    **It carries no weather.** These are patch centres, not sun positions for a place and a time;
    which of them the sun visits, and when, arrives later as `smxsun`. That is why a north-facing
    aperture's `asemx` is not all-zero -- the dome spans every azimuth.

    Cached per `(install, mf)` -- see `_SUN_DIRECTION_CACHE`, and Task 18's own concern 7, which is
    what the cache answers: the guard wants this at every `asemx` seal.

    The count is checked against `sun_count(mf)` before the result is cached, so a truncated or
    partly-written scene fails here rather than at the seal, where the message would be about an
    aperture rather than about the file.
    """
    key = (str(env.bin_dir), env.version, mf)
    cached = _SUN_DIRECTION_CACHE.get(key)
    if cached is not None:
        return cached
    parsed = parse_sun_directions(suns_scene_text(env, mf, cwd=cwd, timeout=timeout))
    if len(parsed) != sun_count(mf):
        raise ValueError(
            f"the suns scene for mf={mf} yielded {len(parsed)} sun direction(s), and "
            f"rad_engine.suns.sun_count({mf}) is {sun_count(mf)}. The dome this returns is what "
            "rad_engine.project's asemx guard checks its own set against, so a short list would be "
            "refused there with a message about an aperture instead of about this file."
        )
    _SUN_DIRECTION_CACHE[key] = parsed
    return parsed
