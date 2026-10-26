"""`rcontrib` argv for the three-phase V and D matrices, and the symbolic bin counts their shapes are
pinned against.

Task 4 of Phase 6a (docs/plan.md, Phase 6's "Outcome — 6a, 2026-08-16").
Consumes `rad_engine.klems.bin_expression`/`KLEMS_CAL` (Task 3) and `rad_core.apertures.Aperture`
(Task 1). Read `rad_engine.klems` first -- this module renders its two strings into an actual
`rcontrib` command line and adds the Reinhart-basis sibling for the D matrix.

**Scope, read twice before adding anything here.** `rad_engine.annual` (Phase 4 Task 4, 662 lines)
already owns `build_dctimestep_argv`, `run_dctimestep`, `parse_dctimestep_output`,
`read_matrix_header` and `check_matrix_shape` -- the whole "read a matrix header off disk and refuse
a mismatched shape" chain. This module does **not** duplicate any of them: doing so would give
`MATRIX_SHAPE_MISMATCH` two plausible emitters, exactly what `tests/test_advisories.py::
_ONE_PER_EMITTER` exists to catch. What does not exist yet, and is this module's whole job, is the
`rcontrib` argv that *produces* a V or D matrix in the first place -- `annual.py`'s chain starts one
step downstream of that, with a matrix already on disk. (The module docstring's own "open gap"
section below records that `man genklemsamp` describes a second, un-built way to produce the D
matrix specifically -- this module implements one verified pipeline, not both.)

## The V and D matrices are two structurally different pipelines, not two branches of one

**Owner ruling, docs/plan.md Decisions table, 2026-07-30: "Matrix generation uses `rcontrib` + `genklemsamp`,
following the algorithm specs, not `rfluxmtx`."** This module's D matrix follows that ruling exactly
-- an earlier draft of this module traced a glow surface with `rcontrib -I+ -m` for both matrices
(the V matrix's own, correct shape) and used the same shape for the D matrix. That was measured and
found wrong: it produces a **single row** of 438 values (one sky-basis measurement at one point),
not the **145-row x 438-column** matrix docs/plan.md check 6.6 names -- 145 Klems incident directions
each with a full 146-bin (x 3 channel) Reinhart sky measurement. A single row is a plausible-looking
number that is not the object check 6.6 asserts, exactly the class of defect a shape-only,
column-count-only test cannot catch (R47/R52: a number nobody watched go red on the *right* axis is
not pinned). The fix is structural, not numerical.

The V matrix (window -> sensor points, in the Klems basis) treats the window as a **light source**:
`rad_engine.klems.glow_surface` turns it into a `glow` primitive, and one `rcontrib -I+ -m` run traces
real sensor points against it, binning the window's *outgoing* directions into the Klems basis (`-b`
from `bin_expression`, `-bn Nkbins`, `-f klems_full.cal`). `build_v_argv` is unchanged from the version
that passed checks 6.4 and 6.5 and is not touched by this fix.

The D matrix (sky patch -> window) has no sensor points and no glow surface -- it measures the window
as a **receiving aperture**, and the object with the 145-row shape comes from sampling that aperture's
own surface at 145 Klems incident directions and firing rays *outward* from each. That sampling is
`genklemsamp`'s job, not `rcontrib`'s: verified directly against `man genklemsamp` on this machine and
reproduced end to end on the office fixture (this task's own measurement, this file's docstring, and
`tests/test_matrices.py`), the real pipe is

    genklemsamp -c count -vd <exterior-facing normal> -vu <up> window.rad \
      | rcontrib -c count -h -ab <bounces> -e 'MF:mf' -f reinhart.cal -b rbin -bn Nrbins -m sky_modifier exterior.oct

`build_genklemsamp_argv` builds the sending half; `build_d_argv` builds the receiving half. A caller
runs them as two spawns with the first's stdout piped into the second's stdin (never a shell pipe --
`rad_engine.run.run` takes argv as a list, and `rad_engine.bsdf.generate_blind_geometry`'s own
`genblinds | xform` docstring states why: it is what keeps each stage's stderr separately
attributable). Neither builder resolves a binary path or spawns anything itself, the same contract
every `build_*_argv` in this codebase holds.

**`-c` must match between the two commands, and one `count` parameter makes mismatching it a caller
error rather than a silent one.** `man genklemsamp`: "The -c option specifies the number of rays to
sample per Klems direction." `man rcontrib`'s own worked example reuses the identical `-c 500` on
both sides. Rather than trusting two independent call sites to type the same literal twice, both
`build_genklemsamp_argv` and `build_d_argv` take `count: int` as the **same-named** keyword, so a
caller building both argvs from one local variable cannot drift the two apart by construction (the
same "one value, not two typed literals" discipline `rad_engine.klems`'s `KLEMS_CAL` constant already
holds against `KLEMS_BINS` for a different pair of numbers). This is a naming discipline, not a
runtime assertion across two calls that share no state -- a mismatch is still possible if a caller
passes two different literals to the two calls, exactly as a caller could still misuse
`build_dctimestep_argv`'s two file arguments in the wrong order (`rad_engine.annual`'s own docstring:
"Order is the arithmetic, not a convention"). Both docstrings state the requirement in the same words
so a reader of either function sees it.

**`rcontrib -m` is required on the D matrix too, and `man genklemsamp`'s own worked example is
itself incomplete as transcribed.** `man genklemsamp`'s worked example --
``genklemsamp -c 500 -vd 0 -1 0 -vu 0 0 1 south_windows.rad | rcontrib -c 500 -e MF:1 -f reinhart.cal
-b rbin -bn Nrbins exterior.oct`` -- passes no `-m`/`-M` at all. Run verbatim against Radiance 6.1a on
this machine, that omission is fatal: `rcontrib: fatal - missing required modifier argument`, matching
`man rcontrib`'s own SYNOPSIS, which lists `{ -m mod | -M file }` in **curly** braces (a required
choice), not the square brackets used for every genuinely optional flag around it, and its
DESCRIPTION states plainly that contribution is computed "for objects whose modifiers are named in
one or more `-m` settings." This is a third spec/binary divergence in the same family as the `-faf`
gap and the spec's non-existent V-matrix `.cal` name recorded below -- except this one is in the
*man page* Radiance ships with its own binary, not the project's extracted specs, so there is no
"leave the source verbatim" policy to defer to; the working command simply needs a `-m` the page's
own example omits. The
coordinator's own measured, working reproduction of this pipe (fix round 1) carries `-m win_glow_mat`
for exactly this reason. `build_d_argv` therefore takes `sky_modifier: str` as a **required** keyword
-- naming the exterior octree's own sky material, an exterior-scene-building concern
(`docs/specs/three-phase-method.md`'s Step 3.1) this module cannot supply a constant for, but cannot
omit either.

**`rcontrib -faf` does not exist on this install.** `docs/specs/three-phase-method.md`'s D-matrix
example writes `-faf` ("input rays are already formatted") on the `rcontrib` command line; `man
rcontrib`'s full synopsis and `rcontrib -defaults` on this machine (Radiance 6.1a, re-verified for
this fix) show only `-fa{a,f,d}` (ASCII/float/double I/O format, default `-faa`) and a separate `-fo`
(force-overwrite for file output) -- no `-faf` token exists anywhere in either. `man genklemsamp`'s
own worked example pipes ASCII rays into a plain `rcontrib` invocation with no format flag at all,
which is what `build_d_argv` does too. Recorded here as the same class of spec/binary mismatch already
tracked for the spec's non-existent V-matrix `.cal` name (`"klems_" + "int.cal"`, `rad_engine.klems`'s
own module docstring -- spelled split for the identical reason that docstring gives: this file is
scanned by the same literal-substring guard) and the three-real glow primitive (verified fact 18,
docs/tool-surface.md) -- not corrected in the spec files themselves (docs/specs/README.md: extracted
source, left verbatim).

The two `.cal` files the two matrices bin into -- `klems_full.cal` for the V matrix's outgoing Klems
directions, `reinhart.cal` for the D matrix's incoming Reinhart sky patches -- are unrelated symbol
tables sharing no names, which is why `reinhart_bins` below is a second function rather than a second
branch of `rad_engine.klems.KLEMS_BINS`.

## `genklemsamp -vd` is the aperture's exterior-facing normal -- the negation of `outward_normal`

`rad_core.apertures.Aperture.outward_normal` is **room-facing** by this codebase's own fixed
convention (that module's docstring: measured directly, `kbin(0,1,0,0,0,1)` bins rays travelling in
-Y, so the vector Klems binning needs is the one pointing *into* the room). `genklemsamp -vd` wants
the opposite sense -- `docs/specs/three-phase-method.md`: "View direction (normal vector pointing
outwards from the window exterior). Example: 0 -1 0 for a south-facing window if Y is North", matching
`man genklemsamp`'s own worked example verbatim (`-vd 0 -1 0 -vu 0 0 1 south_windows.rad`). For the
office fixture, room-facing (`Aperture.outward_normal`) is measured as `(0, 1, 0)` and the man page's
own south-facing example is `(0, -1, 0)` -- the two are consistent only if `-vd` is `outward_normal`
**negated**. `build_genklemsamp_argv` computes that negation itself rather than asking a caller to
pass a second, oppositely-signed vector for the same physical aperture: getting this backwards samples
the exterior hemisphere from the *room* side, the same silent-wrong-hemisphere failure class check 6.5
exists to catch for `kbin(...)`, and it is exactly the kind of sign a caller re-deriving it by hand
would be liable to get wrong. `-vu` (the Klems azimuth=90-degree reference) is `aperture.up_vector`
unchanged -- `Aperture.up_vector`'s own convention (global +Z for a non-horizontal aperture) already
matches `man genklemsamp`'s own `-vu 0 0 1` example, and up is not a room/exterior-facing distinction
the way the normal is.

## `-e 'MF:{mf}'` must precede `-f reinhart.cal` -- order is the arithmetic, not a style choice

Verified directly on this machine (Radiance 6.1a): ``echo 0 | rcalc -e 'MF:1' -f reinhart.cal -e
'$1=Nrbins'`` reports 146 with empty stderr; reversing the two flags fails with `MF: undefined
variable`, because `reinhart.cal` reads `MF` at **load** time, when the `-f` argument is parsed, not
lazily when `Nrbins` is evaluated. `-e` arguments execute left to right as rcalc/rcontrib encounters
them on the command line, so an `-f` naming a file that reads `MF` before any `-e 'MF:...'` has run
sees an undefined variable. `build_d_argv` below therefore places `-e f"MF:{mf}"` immediately before
`-f reinhart.cal`, and `tests/test_matrices.py` asserts the two tokens are adjacent **in that order**,
not merely that both appear somewhere in the argv.

## Reinhart bin counts include the ground patch -- measured, and the specs are one short

`echo 0 | rcalc -e 'MF:1' -f reinhart.cal -e '$1=Nrbins'` -> 146. **The attribution below was wrong
until Phase 7a Task 17 and is now measured rather than recalled** (ruling R90, docs/plan.md): this
paragraph used to credit `specs/three-phase-method.md` and `specs/five-phase-method.md` with the
literals, and **neither file contains either** -- both write the symbols `Nrbins`/`Nkbins`
throughout. Measured on this checkout: `-bn 145` appears in `specs/workflows-point-in-time-to-
annual.md`, `specs/radiance-practical-guide.md`, `specs/radiance-engine-overview.md` and
`specs/workflows-comprehensive.md`; `-bn 5185` appears exactly once, `specs/workflows-point-in-time-
to-annual.md:819`, on a line whose own `-e MF:6` makes it an MF **6** command rather than the MF 4
this docstring used to claim. Those specs write `-bn 145` (MF 1) and `-bn 5185` (MF 6) -- both one
short, because
they count only the 144*MF^2 sky patches and omit the single ground patch `reinhart.cal` adds on top,
the exact "one row/column short" trap `rad_engine.annual`'s own module docstring names for the
`dc_receiver_text` ground hemisphere. `reinhart_bins` reports the measured, ground-inclusive count;
`build_d_argv` writes the **symbol** `Nrbins` on the command line and never a literal digit, so a
basis change can never silently misalign the row arithmetic downstream (the same discipline
`rad_engine.klems`'s module docstring states for `Nkbins`/`KLEMS_BINS`).

## `-lw` is check 6.5's own argument, not a new field on `QualityParameters`

`rad_engine.rtrace.QualityParameters` deliberately never populates `-lw` or `-dj` -- its docstring
names them *"the fields this tool's presets never touch and never derives a value for."* Check 6.5's
discriminating run needs `-lw 1e-4`, a value with no relationship to any point-in-time preset. Adding
an `-lw` field to `QualityParameters` would change a frozen dataclass three shipped tools
(`run_point_in_time`, `run_annual`, `rad_engine.annual.build_rfluxmtx_argv`) already resolve, for a
value only this module's two matrix builders need. So `build_v_argv` and `build_d_argv` take
`limit_weight: float` as their own explicit keyword -- matrix work has its own sampling contract,
stated at the call site rather than inherited from a type that was never about it.

## Check 6.4 vs check 6.5 are deliberately different runs, and comparing their totals would be wrong

Check 6.4 (shape) uses `-ab 0 -ad 16 -lw 0.1`: cheap, and sufic to prove the column count is 435
regardless of what the numbers are. At that setting **both** the correct and a reversed Klems basis
total exactly 0.0 -- `rcontrib` writes `warning - no light sources found` to stderr, because a `glow`
primitive is not itself a light source and `-ab 0` never reaches the sky that illuminates it. So 6.4
cannot discriminate a correct basis from a reversed one; it only proves the shape.

Check 6.5 (discrimination) uses `-ab 1 -ad 512 -lw 1e-4`, the argv this module's own Task 0 spike
derived specifically because it is the cheapest setting at which the correct arm is measurably
nonzero. **The correct arm's total is a Monte Carlo quantity and is not reproducible bit-for-bit --
or even to one significant figure -- across separate invocations of the identical argv**: measured
directly on this machine, four runs spaced 3 s apart gave totals of 0.0534, 0.0713, 0.0713, 0.1425 (a
2.7x spread), because `rcontrib`'s ambient sampling seeds itself from wall-clock time and nothing in
this project's argv pins a seed. `tests/test_matrices.py` therefore asserts the correct arm is
strictly positive (an order-of-magnitude claim, `> 0.01`) and never an exact value or a tolerance band
around one -- see the test's own docstring for why that is the right invariant for this quantity
rather than a weakening of one. The **reversed** arm is a different kind of number: it is exactly
0.0, every run, because a Klems bin index computed against the wrong hemisphere's normal addresses a
bin no ray ever lands in -- a geometric impossibility, not a small measurement. `check_matrix_shape`
and `rad_engine.annual.check_matrix_shape` and ruling R43 both make the same point about comparing
Monte Carlo matrices: totals or row sums, never bytes.

## `Cds`, the five-phase direct-sun coefficient matrix (Phase 7a Task 10)

`docs/specs/five-phase-method.md` 4.3: many discrete sun sources -> direct illuminance at sensor
points, traced through the high-resolution tensor-tree BSDF proxy on the all-suns octree Task 8
builds. Structurally it is a third `rcontrib` pipeline beside V's and D's -- sensor points on stdin,
like V, but binned into the Reinhart *sun-source* basis D's own `-b rbin -bn Nrbins -f reinhart.cal`
already uses, because `rcontrib`'s contribution bins are indexed by *source* modifier and the suns
scene's own sources are what `-m suns_modifier` restricts the trace to (`rad_engine.suns`, Task 6).

**`-I`, not `-I+`.** The spec's own 4.3 line writes bare `-I` (irradiance mode with the sign
unspecified), unlike the V matrix's `-I+` (`build_v_argv`'s own docstring). `build_cds_argv` follows
the spec verbatim here rather than assuming the V matrix's own convention transfers -- the two traces
differ in more than sign (V measures one glow surface's outgoing contribution; `Cds` measures a
window's transmission of many independent point sources), so there is no a priori reason the flag
should match.

**`-ab 1` is fixed, not read from `parameters` -- the same posture `build_d_argv` holds for its own
`-ab 0`, for the identical reason.** five-phase-method.md 4.3: *"-ab 1: Necessary for BSDF sampling.
The all-black model prevents unwanted contributions from spawned direct rays."* That is the term's own
definition, not a quality choice a caller's preset should be free to override -- exactly the
"`Vd`'s `-ab` is forced to 1 regardless of `request.parameters`" reasoning `rad_engine.chain.
five_phase_steps`'s own docstring gives for the sibling term. `parameters` is still accepted and still
supplies `-ad`/`-as`/`-ar`/`-aa` (`_non_bounce_ambient_flags`, the same helper `build_d_argv` reuses)
-- only the bounce count is fixed by the pipeline shape.

**The direct-calculation flags -- `-dc 1 -dt 0 -dj 0 -st 1 -ss 0` -- are the term's own fixed set,
never derived from `parameters`.** `rad_engine.rtrace.QualityParameters` deliberately never populates
`-dj` (that module's own docstring, restated by `build_v_argv`'s "`-lw` is check 6.5's own argument"
section above for the sibling case of `-lw`), so there is no field to read even if one wanted to.
`-dj 0` is what makes the shadows sharp (five-phase-method.md 4.3); the rest of the set is what BSDF
sampling under a direct calculation needs, sourced from the same spec line.

**`-bn` is the symbol `Nrbins`, never a literal digit, for `reinhart_bins`'s own reason -- and the
spec's own worked example is one short in a way that is easy to conflate with the *different* "one
short" gap `reinhart_bins`'s docstring already records.** `specs/five-phase-method.md` 4.1 writes
`cnt 5185 | rcalc ...` to build the suns scene: **5185 is the correct, measured count of sun
*sources*** at MF:6 (`rad_engine.suns`, Task 6's own pinned count) -- the ground patch a *sky*
subdivision carries has no sun-source analogue, so this number is not one short the way `-bn 145`/
`-bn 5185` are elsewhere in this file. The **bin count** `-bn Nrbins` resolves to is a separate fact:
`reinhart_bins(6)` is `5186`, the ground-patch-inclusive Reinhart bin count `reinhart.cal` itself
defines, which is what `-b rbin -bn Nrbins` bins *into* regardless of how many of those bins the suns
scene's own sources actually populate. Two correct numbers, 5185 and 5186, answering two different
questions -- writing either literal here would silently pick the wrong one for the other question,
which is exactly why `-bn` is the bare symbol and never either digit.

**`-e 'MF:{mf}'` precedes `-f reinhart.cal`, `build_d_argv`'s own load-bearing order** (see that
function's docstring and the module docstring's own section above): `reinhart.cal` reads `MF` when
`-f` is parsed, not lazily when `Nrbins`/`rbin` is evaluated.

**No `-faf`.** The spec's own 4.3 line writes `-faf` ("input rays are already formatted"); `man
rcontrib`'s full synopsis and `rcontrib -defaults` on this machine (Radiance 6.1a) show no such token
exists -- the identical spec/binary divergence `build_d_argv`'s own docstring already records for the
D-matrix pipe, reproduced here rather than re-derived, since it is one fact about this Radiance
install and not two.

**`header` defaults `True`, unlike every other builder in this module.** `build_v_argv` and
`build_d_argv` both default `header=False`, because their first, still-live callers (`tests/
test_matrices.py`'s shape and basis checks) parse `stdout` directly and a header would be one more
token to skip. `Cds` has no such caller: every production and test use writes it to a file
`dctimestep` will later read (`rad_engine.chain.five_phase_steps`, following `Vd`/`Dd`'s own
`header=True` call sites), and a caller that forgot to pass `header=True` for that purpose would
reproduce the exact `dctimestep` `fatal - unexpected EOF in header` `build_v_argv`'s docstring
measures. Defaulting `True` here makes the safe case the one that requires no argument, rather than
the one that is easy to omit.

## The ASE direct-sun matrix (Phase 7c Task 8)

`build_ase_direct_argv` is a **fourth** `rcontrib` pipeline in this module, and the one thing to know
at this level is why it is a fourth rather than an argument on the third. LM-83 sec 3.3's annual
sunlight exposure is the direct beam from the solar orb alone, evaluated pre-blind, at **zero**
bounces; `build_cds_argv` fixes `-ab 1` as an internal literal serving the five-phase direct term,
and ruling R92's lesson is that a builder which fixes a flag fixes it for every caller. Every other
token of the two argvs is identical, which is exactly the shape that invites a `bounces=` parameter
-- and that parameter would put `Cds`'s own bounce count within reach of every caller of
`build_cds_argv`. The duplication is the cheaper of the two. The full derivation of each flag,
including the measured `rcontrib -defaults` `-dj 0.9` that makes `-dj 0` load-bearing here, lives on
`build_ase_direct_argv` itself rather than being restated in this docstring.
"""

from __future__ import annotations

from pathlib import Path
from typing import Final

from rad_core.apertures import Aperture

from .klems import KLEMS_CAL, bin_expression
from .rtrace import QualityParameters

__all__ = [
    "REINHART_CAL",
    "build_ase_direct_argv",
    "build_cds_argv",
    "build_d_argv",
    "build_genklemsamp_argv",
    "build_v_argv",
    "reinhart_bins",
]

#: The Reinhart tensor-tree/Klems sky-subdivision `.cal` file `genklemsamp`/`rcontrib` read the `rbin`
#: symbol from. Unlike `rad_engine.klems.KLEMS_CAL`, the spec's own name for this file
#: (`reinhart.cal`) is the file that actually exists on this install (Radiance 6.1a,
#: `/usr/local/radiance/lib`) -- there is no analogous "spec says a name that does not exist" gap to
#: record here.
REINHART_CAL: Final[str] = "reinhart.cal"

#: `Nrbins` at each Reinhart subdivision `mf` this project uses, measured via ``echo 0 | rcalc -e
#: 'MF:{mf}' -f reinhart.cal -e '$1=Nrbins'`` on this machine (Radiance 6.1a), stderr empty for every
#: entry. Each count is `144*mf^2 + **2**` -- `144*mf^2 + 1` is the *sky patch* count (the `+1`
#: being the zenith cap), which `rad_engine.suns.sun_count` returns and which the specs' own worked
#: examples (`-bn 145`, `-bn 5185`) use; `reinhart.cal` adds the **ground** patch on top of it, and
#: that is the one the specs omit. Corrected on the whole-branch review: this comment read
#: `144*mf^2 + 1` while the table beside it was `+2` at every entry (`144*1+1 = 145`, not 146) --
#: off by exactly the ground patch, in the module whose whole documented purpose is making that
#: off-by-one impossible to reintroduce. See the module docstring for why `reinhart_bins` exists at
#: all rather than inlining either formula: the point is that this is a *measured* fact about
#: `reinhart.cal`, carried as data, not a formula this module derived and hopes matches the `.cal`
#: file's own definition.
_REINHART_BINS: Final[dict[int, int]] = {
    1: 146,
    2: 578,
    4: 2306,
    6: 5186,
}


def reinhart_bins(mf: int) -> int:
    """`Nrbins` for Reinhart subdivision `mf` -- the ground-patch-inclusive count measured on this
    machine (module docstring, `_REINHART_BINS`). For shape assertions only, exactly as
    `rad_engine.klems.KLEMS_BINS` is for the Klems basis: never format this into an argv. `build_d_argv`
    writes the symbol `Nrbins`, resolved by `reinhart.cal` itself at run time.

    Raises `ValueError` for an `mf` this project has not measured -- returning a computed
    `144*mf*mf + 1` for an unmeasured subdivision would be exactly the "formula nobody checked against
    the real `.cal` file" gap this module's own docstring describes avoiding.
    """
    try:
        return _REINHART_BINS[mf]
    except KeyError:
        raise ValueError(
            f"reinhart_bins has no measured count for mf={mf!r}; measured subdivisions are "
            f"{sorted(_REINHART_BINS)}. Measure `echo 0 | rcalc -e 'MF:{mf}' -f reinhart.cal -e "
            "'$1=Nrbins'` on this machine before adding a new entry -- a computed 144*mf^2+1 that "
            "was never checked against the real .cal file is exactly the gap this function exists "
            "to avoid (see the module docstring)."
        ) from None


def _ambient_flags(parameters: QualityParameters) -> list[str]:
    """The five ambient flags, `-ab`/`-ad`/`-as`/`-ar`/`-aa`, in that order -- the same order and the
    same reuse `rad_engine.annual.build_rfluxmtx_argv` and `rad_engine.rtrace.build_rtrace_argv` both
    already hold for the identical `QualityParameters` fields. `-aa` is `repr`'d rather than `str`'d
    for the same reason those two callers do: a float's `str` can round-trip incorrectly for some
    values while `repr` is guaranteed to, and `-aa 0.0` (the `reference` preset) must not silently
    become `-aa 0` and change meaning.
    """
    return [
        "-ab", str(parameters.ambient_bounces),
        "-ad", str(parameters.ambient_divisions),
        "-as", str(parameters.ambient_super_samples),
        "-ar", str(parameters.ambient_resolution),
        "-aa", repr(float(parameters.ambient_accuracy)),
    ]


def _non_bounce_ambient_flags(parameters: QualityParameters) -> list[str]:
    """`-ad`/`-as`/`-ar`/`-aa` only -- `_ambient_flags` minus its own `-ab` term, for `build_d_argv`,
    whose `-ab` is fixed at `0` by the pipeline shape rather than read from `parameters` (that
    function's own docstring). A named function rather than slicing `_ambient_flags`'s return value,
    so a future reordering of that list's flags cannot silently change which pair this drops.
    """
    return _ambient_flags(parameters)[2:]


def _number(value: float) -> str:
    """A float as Radiance argv sees it: `%g`, matching `rad_engine.klems._number` and
    `rad_engine.bsdf._number`'s identical formatting -- not imported from either, since this module
    has no other dependency on those two and the format spec is one line each.

    **`-0.0` is normalised to `0.0` before formatting.** `f"{-0.0:g}"` is `"-0"` in Python -- IEEE 754
    negative zero, produced here whenever `build_genklemsamp_argv` negates an `outward_normal`
    component that was exactly `0.0` (the office fixture's own `(0, 1, 0)` negates to `(-0.0, -1.0,
    -0.0)`). `"-0"` is not wrong to `rcontrib`/`genklemsamp` (both parse it as zero), but it is not
    what `man genklemsamp`'s own worked example spells (`-vd 0 -1 0`, plain `0`), so a caller
    diffing this module's output against the sourced example would see a spurious mismatch on a
    component that carries no sign either way. `value + 0.0` folds `-0.0` to `0.0` per IEEE 754
    addition rules without touching any genuinely negative or positive value.
    """
    return f"{value + 0.0:g}"


def build_v_argv(
    *,
    octree: Path,
    points: Path,
    aperture: Aperture,
    glow_modifier: str,
    parameters: QualityParameters,
    limit_weight: float,
    header: bool = False,
    processes: int | None = None,
) -> tuple[str, ...]:
    """The `rcontrib` argv for one V-matrix run: window glow surface -> Klems basis, traced from
    `points` through `octree`.

    ``rcontrib -I+ [-h] -ab .. -ad .. -as .. -ar .. -aa .. -lw limit_weight -f klems_full.cal -b
    bin_expression(aperture) -bn Nkbins -m glow_modifier octree``.

    `-I+` selects irradiance mode with the +/- convention `rfluxmtx`/`rcontrib` share with
    `rad_engine.rtrace.build_rtrace_argv`'s own `-I` -- the whole point of a V-matrix trace, so it is
    not part of `parameters` and cannot be overridden away, the same posture `rtrace.py`'s module
    docstring states for its own non-negotiable flags.

    **`header` decides whether `-h` is emitted, and getting it wrong is a job failure rather than a
    wrong number.** `-h` suppresses the `#?RADIANCE` header so captured stdout is bin values only,
    which is what a caller *parsing stdout* wants and is why it was unconditional until Phase 6b
    Task 6. It is exactly wrong for a matrix written to a file that `dctimestep` will later read:
    measured directly against Radiance 6.1a on this machine, ``dctimestep V T D S`` over a
    headerless V (or a headerless D) exits 1 with ``fatal - unexpected EOF in header``, while the
    identical chain with both headers present exits 0. `dctimestep` reads `NCOLS`/`NCOMP` off the
    header to know the matrix shape; there is nothing else in an ASCII matrix file that carries it.

    So the two uses are genuinely different and neither is the default for the other:

    - `header=False` (the default, unchanged behaviour) for a caller that parses stdout itself --
      `tests/test_matrices.py`'s shape and basis checks, which split stdout into floats.
    - `header=True` for a matrix destined for a file `dctimestep` will consume -- `run_annual`'s
      3-phase chain, where the V matrix is a job step's `stdout_path`.

    Defaulted to the old value rather than flipped, so no existing caller changes meaning silently;
    the new caller states what it needs at the call site.

    `-b` is `bin_expression(aperture)` -- always the explicit six-argument `kbin(...)` form, never a
    cardinal alias (`rad_engine.klems`'s own module docstring). `-bn` is the **symbol** `"Nkbins"`,
    never a literal digit -- `rad_engine.klems.KLEMS_BINS` exists for shape assertions only and must
    never be formatted here (that module's own docstring). `-m glow_modifier` restricts contribution
    calculation to the one glow surface `rad_engine.klems.glow_surface` wrote under this name, so the
    V matrix measures only the window's own contribution, not every glow primitive in the octree.

    `octree` and `points` are both accepted so the caller's job spec carries the points file even
    though this function does not read it -- points arrive on stdin at spawn time, the same
    `rad_engine.rtrace.build_rtrace_argv` convention, and `octree` is the final positional argument.
    `points` is not referenced in the returned argv; it is part of the signature because a caller
    assembling a job step needs both the argv and the stdin source from one call, mirroring
    `rad_engine.annual.DcMatrixSpec`'s own carried-but-unread `sky_subdivision` field.

    `processes` adds `-n` immediately before the octree (R781).

    Returns a plain argv **leading with the bare binary name**, the contract every `build_*_argv`
    in this codebase holds -- `rad_engine.run.run` substitutes `env.path("rcontrib")` for that
    first token.
    """
    del points  # carried on the call for the caller's stdin wiring; not part of the argv itself
    argv: list[str] = ["rcontrib", "-I+"]
    if not header:
        argv.append("-h")
    argv += [*_ambient_flags(parameters)]
    argv += ["-lw", repr(float(limit_weight))]
    argv += ["-f", KLEMS_CAL]
    argv += ["-b", bin_expression(aperture)]
    argv += ["-bn", "Nkbins"]
    argv += ["-m", glow_modifier]
    if processes is not None:
        argv += ["-n", str(int(processes))]  # R781: before the octree
    argv.append(str(octree))
    return tuple(argv)


def build_genklemsamp_argv(
    *, aperture: Aperture, count: int, window_rad: Path,
) -> tuple[str, ...]:
    """The `genklemsamp` argv that samples 145 Klems incident directions over `aperture`'s own
    polygons and writes `count` ray origins/directions per direction to stdout -- the sending half of
    the D-matrix pipe (module docstring, owner ruling of 2026-07-30 in docs/plan.md's Decisions
    table).

    ``genklemsamp -c count -vd <exterior-facing normal> -vu <up> window_rad``.

    `-vd` is `aperture.outward_normal` **negated** -- room-facing by this codebase's own convention,
    while `genklemsamp -vd` wants the exterior-facing sense (module docstring's own derivation from
    `man genklemsamp`'s south-facing example). `-vu` is `aperture.up_vector` unchanged: it is the
    Klems azimuth reference, not a room/exterior-facing distinction, and `Aperture.up_vector`'s own
    convention already matches the man page's own `-vu 0 0 1` example.

    `window_rad` is the `.rad` file containing the aperture's own polygon(s) -- **not** the glow
    surface `rad_engine.klems.glow_surface` builds for the V matrix: `genklemsamp` samples plain
    geometry directly (`man genklemsamp`: "planar surfaces over which genklemsamp will originate
    samples"), it does not need a light-emitting material, and passing a `glow` primitive here would
    be building V-matrix geometry into a D-matrix pipe that does not use it (module docstring: the two
    matrices are structurally different pipelines, not two branches of one glow surface). Writing
    `window_rad`'s contents is the caller's job, matching `build_v_argv`'s own division of labour for
    `glow_surface`'s returned text.

    `count` **must equal** the `count` passed to the paired `build_d_argv` call -- see that function's
    own docstring for why this is a shared keyword name rather than two independently-typed literals.

    Returns a plain argv **leading with the bare binary name**, the contract every `build_*_argv`
    in this codebase holds -- `rad_engine.run.run` substitutes `env.path("genklemsamp")` for that
    first token.
    """
    nx, ny, nz = aperture.outward_normal
    ux, uy, uz = aperture.up_vector
    return (
        "genklemsamp",
        "-c", str(int(count)),
        "-vd", _number(-nx), _number(-ny), _number(-nz),
        "-vu", _number(ux), _number(uy), _number(uz),
        str(window_rad),
    )


def build_d_argv(
    *,
    octree: Path,
    aperture: Aperture,
    mf: int,
    parameters: QualityParameters,
    count: int,
    sky_modifier: str,
    header: bool = False,
    processes: int | None = None,
) -> tuple[str, ...]:
    """The `rcontrib` argv for the receiving half of one D-matrix run: `genklemsamp`'s piped rays ->
    Reinhart sky basis (module docstring, owner ruling of 2026-07-30 in docs/plan.md's Decisions
    table). Paired with
    `build_genklemsamp_argv`, whose stdout a caller pipes into this argv's stdin (two spawns, not a
    shell pipe -- see the module docstring).

    ``rcontrib -c count [-h] -ab <parameters.ambient_bounces> -e 'MF:mf' -f reinhart.cal -b rbin
    -bn Nrbins -m sky_modifier octree``. **The `-ab` is the caller's, and this synopsis said `0`
    for two commits after ruling R92 stopped hardcoding it** -- three paragraphs above the section
    of this same docstring headed "`-ab` is read from `parameters`, and this function used to
    hardcode `0` instead". Corrected on the whole-branch review; a synopsis naming a flag value is
    a claim about the argv this function builds.

    **`header` decides whether `-h` is emitted** -- see `build_v_argv`'s own docstring for the
    measurement: `dctimestep` over a headerless D exits 1 with ``fatal - unexpected EOF in header``,
    because the `#?RADIANCE` header is where `NCOLS`/`NCOMP` live and an ASCII matrix carries the
    shape nowhere else. `False` (the default, unchanged) for a caller parsing stdout itself; `True`
    for a matrix written to a file the 3-phase chain will multiply.

    **`sky_modifier` is required, not optional.** An earlier draft of this function made it
    `str | None = None`, reasoning from `man rcontrib`'s synopsis line
    (``[ -m mod | -M file ]``, in square brackets) that it could be omitted. Measured directly on this
    machine and found wrong: `rcontrib` with no `-m`/`-M` at all exits 1 with
    `rcontrib: fatal - missing required modifier argument`. The synopsis's brackets group the
    `-m`/`-M` **choice** as one optional-looking unit at the top level, but the DESCRIPTION section
    states plainly that one of the two is mandatory -- the coordinator's own working, measured D-matrix
    command also always carries `-m win_glow_mat`. So `sky_modifier` is a required keyword, and
    `build_d_argv` cannot build a valid D-matrix argv without a name for the octree's own sky material.

    **`-ab` is read from `parameters`, and this function used to hardcode `0` instead.** The old
    reasoning was that the rays entering this `rcontrib` run already came from `genklemsamp`'s own
    deterministic surface sampling rather than from an ambient calculation needing a seed, so no
    bounces were required. That conflates two different things. `-ab` here does not govern how the
    rays are *born*; it governs how many times each one may bounce **after it leaves the window** --
    off the ground, off exterior obstructions -- before it reaches the sky. At `-ab 0` every such ray
    is killed on first contact, so the daylight matrix carries **no ground-reflected or
    obstruction-reflected light at all**.

    `docs/specs/five-phase-method.md` is explicit that the pair differs in exactly this flag, and
    only this flag: §2.3 builds `D` with `-ab 2` (*"Standard ambient bounces for daylight matrix"*)
    against the exterior octree, §3.3 builds `Dd` with `-ab 0` (*"Zero ambient bounces to ensure only
    direct sky component"*) against the all-black one. One builder hardcoding `0` made `D` **be**
    `Dd` apart from the octree -- so the five-phase chain subtracted a correctly-sized direct term
    from a total that was missing its reflected light, and the shipped three-phase result was low by
    the same amount.

    Measured on the office fixture, one variable changed (`-ab 0` -> `-ab 2`, same rays file, same
    octree, same `V`/`T`/`S`): three-phase mean illuminance over lit hours **526.35 -> 686.35 lux,
    +30.4%**; peak only +4%, because peak is direct sun and what was missing is the reflected light.
    A fresh `-ab 0` control reproduced the shipped mean to the digit, so the difference is the
    bounces and not run-to-run noise. Negative values in the five-phase `.ill` fell 4 054 -> 2 943
    (-27%) -- real, and not the whole cause; the remainder is the separately-recorded convergence
    question.

    **`Dd`'s zero did not disappear, it moved to the call site**, exactly where `Vd`'s `-ab 1`
    already lived (`five_phase_steps` builds `dd_parameters` with `ambient_bounces=0` and passes it
    here). A term whose bounce count is part of its *definition* states that itself; this builder no
    longer states it on every caller's behalf. `parameters` continues to supply `-ad`/`-as`/`-ar`/
    `-aa` through `_non_bounce_ambient_flags`.

    **No `-I+`.** `-I+` (irradiance mode) is the V matrix's own flag, needed because that trace
    measures illuminance at real sensor points (`build_v_argv`'s own docstring). This pipeline has no
    sensor points -- `genklemsamp`'s rays are the input being binned, not something being measured at
    a point -- and `man rcontrib`'s own worked example passes no `-I` flag at all.

    **`-e 'MF:{mf}'` precedes `-f reinhart.cal` and that order is load-bearing** -- see the module
    docstring: `reinhart.cal` reads `MF` when `-f` is parsed, not lazily when `rbin`/`Nrbins` is
    evaluated, so the reverse order fails with `MF: undefined variable`. `-bn` is the symbol
    `"Nrbins"`, never `reinhart_bins(mf)`'s own integer return value -- the same "symbol, never a
    literal" discipline `build_v_argv` holds for `Nkbins`, and for the identical reason: the specs'
    own worked examples write `-bn 5185` and are one short (module docstring).

    `-b` is the bare `rbin` symbol `reinhart.cal` defines, not a formula -- unlike the Klems `-b`,
    which must be the aperture's own explicit `kbin(...)` because a facade at an arbitrary azimuth has
    no correct cardinal alias (`rad_engine.klems`), the Reinhart *sky* basis has no aperture-dependent
    orientation to encode: it bins by which sky patch a ray reaches, not by the window's own normal.
    `aperture` is therefore not a parameter here at all -- unlike the earlier, wrong glow-surface
    design this replaced, nothing in this pipeline's `rcontrib` half reads the aperture's own geometry
    or orientation; `genklemsamp` (the sending half) is where the aperture matters.

    `-m sky_modifier` names the exterior octree's own sky material -- unlike the V matrix's
    `-m glow_modifier`, which restricts a trace away from other geometry in a shared octree, this `-m`
    is the mandatory selector `rcontrib` itself requires (see above): "these modifiers are usually
    materials associated with light sources or sky domes" (`man rcontrib`'s DESCRIPTION). What that
    material is named is the caller's own scene-building concern (a "white sky" glow per
    `docs/specs/three-phase-method.md`'s Step 3.1) and not a constant this module can supply.
    `octree` is the final positional argument, the exterior scene (module docstring's Step 3.1
    division of labour), never the V-matrix's interior octree.

    `count` **must equal** the `count` passed to the paired `build_genklemsamp_argv` call -- `man
    genklemsamp`: "-c ... rays to sample per Klems direction"; `man rcontrib`'s own worked example
    reuses the identical `-c 500` value on both sides of the pipe. A shared keyword name on both
    functions rather than two independently-typed literals; see `build_genklemsamp_argv`'s own
    docstring for why this is a naming discipline rather than a runtime cross-call assertion.

    `processes` adds `-n` immediately before the octree (R781).

    Returns a plain argv **leading with the bare binary name**, the same contract every
    `build_*_argv` in this codebase holds.
    """
    del aperture  # kept for signature symmetry with build_v_argv; see the docstring on why this
    # pipeline's rcontrib half does not read it -- genklemsamp is where the aperture matters.
    argv: list[str] = ["rcontrib", "-c", str(int(count))]
    if not header:
        argv.append("-h")
    argv += ["-ab", str(parameters.ambient_bounces)]
    argv += _non_bounce_ambient_flags(parameters)
    argv += ["-e", f"MF:{mf}"]
    argv += ["-f", REINHART_CAL]
    argv += ["-b", "rbin"]
    argv += ["-bn", "Nrbins"]
    argv += ["-m", sky_modifier]
    if processes is not None:
        argv += ["-n", str(int(processes))]  # R781: before the octree
    argv.append(str(octree))
    return tuple(argv)


def build_cds_argv(
    *,
    octree: Path,
    points: Path,
    mf: int,
    parameters: QualityParameters,
    limit_weight: float,
    suns_modifier: str,
    header: bool = True,
    processes: int | None = None,
) -> tuple[str, ...]:
    """The `rcontrib` argv for `Cds`, the five-phase direct-sun coefficient matrix: many discrete sun
    sources -> direct illuminance at sensor points, traced through the tensor-tree BSDF proxy on the
    all-suns octree (`docs/specs/five-phase-method.md` 4.3; see the module docstring's own "`Cds`,
    the five-phase direct-sun coefficient matrix" section for the full derivation of every flag
    below).

    ``rcontrib -I [-h] -ab 1 -ad .. -as .. -ar .. -aa .. -lw limit_weight -dc 1 -dt 0 -dj 0 -st 1
    -ss 0 -e 'MF:mf' -f reinhart.cal -b rbin -bn Nrbins -m suns_modifier octree``.

    `-I`, not `-I+` -- the spec's own line, unlike `build_v_argv`'s `-I+` (module docstring).
    `-ab 1` and the direct-calculation set (`-dc 1 -dt 0 -dj 0 -st 1 -ss 0`) are the term's own fixed
    definition and are never read from `parameters`, the same posture `build_d_argv` holds for its own
    fixed `-ab 0` -- `parameters` still supplies `-ad`/`-as`/`-ar`/`-aa` via `_non_bounce_ambient_flags`.
    `-lw` is the caller's own value, matching `build_v_argv`'s identical `limit_weight` keyword
    (`QualityParameters` never populates it) -- **not** `build_d_argv`'s: that builder has no
    `limit_weight` parameter and emits no `-lw` at all (verified against its signature and its argv
    assembly below). The claim above is true of `build_v_argv` alone; a false cross-reference here
    is exactly the kind of premise the next task into this file would otherwise inherit unchecked.
    `-bn` is the symbol `"Nrbins"`, never a literal
    digit -- `reinhart_bins(mf)` is for shape assertions only, and see the module docstring for why
    the suns scene's own `sun_count(mf)` (5185 at MF:6) is a *different*, also-correct number that
    must never be substituted here. `-e f"MF:{mf}"` precedes `-f reinhart.cal`, the same load-bearing
    order `build_d_argv` holds for the identical reason (`reinhart.cal` reads `MF` when `-f` is
    parsed). No `-faf`: the spec's own token does not exist on this install (module docstring, and
    `build_d_argv`'s own identical finding). `-m suns_modifier` is the suns scene's shared source
    modifier (`rad_engine.suns`'s `SOLAR_LIGHT_PRIMITIVE`, "solar" in the spec) -- every named sun
    source is modified by it, so `-m` restricts the trace to the whole suns scene rather than to one
    of them.

    `points` is carried, not referenced in the returned argv -- the caller's stdin wiring, the same
    division `build_v_argv` holds for its own `points` argument (rays arrive on stdin at spawn time,
    never as an argv token).

    **`header` defaults `True`, the one builder in this module where the safe case needs no
    argument** -- see the module docstring's own section for why: every caller of this function
    writes `Cds` to a file `dctimestep` will read, unlike `build_v_argv`/`build_d_argv`'s remaining
    `header=False`-default callers, which still parse `stdout` directly.

    `processes` adds `-n` immediately before the octree (R781).

    Returns a plain argv **leading with the bare binary name**, the contract every `build_*_argv`
    in this codebase holds -- `rad_engine.run.run` substitutes `env.path("rcontrib")` for that
    first token.
    """
    del points  # carried for the caller's stdin wiring; rcontrib -I takes sensor rays on stdin only
    argv: list[str] = ["rcontrib", "-I"]
    if not header:
        argv.append("-h")
    argv += ["-ab", "1"]
    argv += _non_bounce_ambient_flags(parameters)
    argv += ["-lw", repr(float(limit_weight))]
    argv += ["-dc", "1", "-dt", "0", "-dj", "0", "-st", "1", "-ss", "0"]
    argv += ["-e", f"MF:{mf}"]
    argv += ["-f", REINHART_CAL]
    argv += ["-b", "rbin"]
    argv += ["-bn", "Nrbins"]
    argv += ["-m", suns_modifier]
    if processes is not None:
        argv += ["-n", str(int(processes))]  # R781: before the octree
    argv.append(str(octree))
    return tuple(argv)


def build_ase_direct_argv(
    *,
    octree: Path,
    points: Path,
    mf: int,
    parameters: QualityParameters,
    limit_weight: float,
    suns_modifier: str,
    header: bool = True,
    processes: int | None = None,
) -> tuple[str, ...]:
    """The `rcontrib` argv for LM-83's ASE term: many discrete sun sources -> **direct-beam-only**
    illuminance at sensor points, traced on the **pre-blind** all-suns octree.

    ``rcontrib -I [-h] -ab 0 -ad .. -as .. -ar .. -aa .. -lw limit_weight -dc 1 -dt 0 -dj 0 -st 1
    -ss 0 -e 'MF:mf' -f reinhart.cal -b rbin -bn Nrbins -m suns_modifier octree``.

    **The term.** LM-83 sec 3.3 defines annual sunlight exposure as the direct beam from the solar
    orb alone -- sky and interreflections excluded -- so ASE is *not* read off a total-illuminance
    annual result (`skills/radiance-simulation-workflow`, ch11: "≥1000 lux **direct sun only** for
    ≥250 hours. LM-83 excludes sky and interreflections, so this is *not* read off a total-
    illuminance annual result"). It is also evaluated **pre-blind** -- ASE is the quantity that
    justifies the shading device, so a run with the device deployed cannot measure it. Both
    exclusions are properties of the octree and of `-ab` below; neither is a setting a caller tunes.

    **`-ab 0` is the term's own definition, not a quality choice**, which is why it is a literal here
    and is never read from `parameters` -- the identical posture `build_d_argv` holds for its own
    fixed `-ab 0` and `build_cds_argv` for its `-ab 1`. One bounce would add the first
    inter-reflection, which is precisely the quantity LM-83 excludes: the run would still succeed and
    still publish a plausible ASE (engine invariant 3, a valid file with a wrong answer). A caller at
    the `high` preset (`ambient_bounces=7`) still gets `-ab 0`; `parameters` supplies only
    `-ad`/`-as`/`-ar`/`-aa`, via `_non_bounce_ambient_flags`.

    **Why this is a separate function and not a `bounces=` parameter on `build_cds_argv`** (ruling
    R92). That builder fixes `-ab 1` as an internal literal serving the five-phase direct term
    `Cds`, and R92's lesson is that a builder which fixes a flag fixes it for *every* caller -- the
    defect it was written for cost the three-phase daylight matrix 30 % of its light for two phases.
    Adding a parameter here to let one caller move the bounce count would put `Cds`'s own `-ab 1`
    within reach of every other caller of that function, trading a duplicated eleven-line assembly
    for a shared mutable flag on the five-phase chain's most expensive matrix. ASE is a different
    term, with a different bounce count, on a different octree; it gets its own builder.

    **`-dj 0` is load-bearing, and it counters `rcontrib`'s own default rather than restating a
    zero.** Measured on this machine (Radiance 6.1a): ``rcontrib -defaults`` prints
    ``-dj 0.900000`` while ``rtrace -defaults`` prints ``-dj 0.000000`` -- the two programs disagree,
    and it is `rcontrib` that jitters. Left at its default, shadow rays are scattered across the
    solar disc and the direct term moves between runs; ASE counts hours over a hard 1000 lux
    threshold, so a term that wanders re-crosses that threshold and the metric is no longer
    reproducible. With `-ab 0 -dj 0 -dc 1 -st 1 -ss 0` there is no stochastic input left, and that is
    measured, not argued: Phase 7c Task 1 built this leg by hand on the micro fixture and ran it five
    times against rebuilt octrees -- byte-identical every time, max/min = 1.000000, MD5-pinned.

    **`-bn` is the symbol `"Nrbins"`, never a literal digit**, the same discipline `build_d_argv` and
    `build_cds_argv` hold: `reinhart_bins(mf)` exists for shape assertions only, and the suns scene's
    own `rad_engine.suns.sun_count(mf)` is a *different*, also-correct number (5185 against 5186 at
    MF:6) that must never be substituted here. `-e f"MF:{mf}"` precedes `-f reinhart.cal` for the
    load-bearing reason those two functions state -- `reinhart.cal` reads `MF` when `-f` is parsed.
    The `mf` reaching `-e MF:` and `-bn`, and the `-m` of the `gendaymtx` sun matrix this matrix is
    multiplied against, must all be **one number**; a mismatch across the three is the anti-pattern
    ch10 of `skills/radiance-simulation-workflow` names, and Task 10 asserts they agree rather than
    leaving the agreement to the caller.

    `-m suns_modifier` restricts the trace to the suns scene's shared source modifier
    (`rad_engine.suns.SUNS_MODIFIER`, `"solar"`) -- every named sun source is modified by it.

    `points` is carried, not referenced in the returned argv -- the caller's stdin wiring, the same
    division `build_cds_argv` and `build_v_argv` hold (`rcontrib -I` takes sensor rays on stdin only,
    never as an argv token).

    `header` defaults `True`, matching `build_cds_argv`: this matrix is written to a file
    `dctimestep` reads, and a headerless operand is the failure that default exists to prevent.

    **The returned argv leads with the bare binary *name*, `"rcontrib"`, exactly as every sibling in
    this module does** -- `rad_engine.run.run` splits `binary, *args = argv` and
    then builds `[str(env.path(binary)), *args]`: it **substitutes** the first token, resolving the
    name rather than receiving an argv that lacks one.

    Said plainly, because the alternative was inheriting a false premise: **the closing sentence
    every other builder in this codebase shared was wrong, not merely loose.** They ended "returns
    a plain argv *without* the binary name" while their own first token was the binary name, and
    several went further and said `run.run` "resolves and **prepends**" the path -- false on both
    halves: nothing is prepended, and the token supposedly absent is the very one being resolved.

    **Swept 2026-08-22 by the whole-branch fix wave: 20 paragraphs across 7 modules.** The
    resume prompt's estimate was twelve, this docstring said twelve, and a later review said ten;
    all three were wrong, in both directions, because each was transcribed rather than re-grepped.
    `tests/test_matrices.py::TestTheArgvContractIsStatedTruthfully` now scans `src/` for the three
    false phrasings and holds the count at zero, so this cannot rot again -- and it deliberately
    does not repeat those phrasings here, because a docstring quoting the strings its own guard
    forbids is a self-match, which is how the first version of that guard went red against correct
    prose. A claim about how many sentences say something is exactly the kind of number this branch
    has watched fail to reproduce five times; the scan replaces the claim.

    `processes` adds `-n` immediately before the octree (R781).
    """
    del points  # carried for the caller's stdin wiring; rcontrib -I takes sensor rays on stdin only
    argv: list[str] = ["rcontrib", "-I"]
    if not header:
        argv.append("-h")
    argv += ["-ab", "0"]
    argv += _non_bounce_ambient_flags(parameters)
    argv += ["-lw", repr(float(limit_weight))]
    argv += ["-dc", "1", "-dt", "0", "-dj", "0", "-st", "1", "-ss", "0"]
    argv += ["-e", f"MF:{mf}"]
    argv += ["-f", REINHART_CAL]
    argv += ["-b", "rbin"]
    argv += ["-bn", "Nrbins"]
    argv += ["-m", suns_modifier]
    if processes is not None:
        argv += ["-n", str(int(processes))]  # R781: before the octree
    argv.append(str(octree))
    return tuple(argv)
