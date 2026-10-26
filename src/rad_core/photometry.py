"""RGB irradiance -> photopic lux, and the daylight-factor normalisation.

Pure: no Radiance, no subprocesses, no file IO. Runs green with no binaries installed -- see
`tests/test_purity.py`. This module is arithmetic only; nothing here constructs a wire `Advisory`
or an `ArtifactWarning` (unlike `rad_core.grids`), because there is no finding to attach: a lux
value or a DF percent is either computed or refused outright, never a success with a caveat riding
along on the result.

**Why this module exists at all** (docs/plan.md finding D5): the spec states the photometric
conversion two ways -- `179 x (0.265 R + 0.670 G + 0.065 B)` in
[docs/tool-surface.md](../../docs/tool-surface.md) §10, and `rmtxop -c 47.4 119.9 11.6` in the
3-phase fixture scripts. Two independently-typed constants for the same physical conversion is
exactly how they drift apart. This module states the conversion once (`to_lux`), and any other
caller -- `rad_engine.rtrace`'s point-in-time path, later the 3-phase annual path -- gets it from
here rather than re-typing the coefficients.

**The source, verbatim.** [workflows-point-in-time-to-annual.md](../../docs/specs/workflows-point-in-time-to-annual.md)
line 108 states the physical basis: *"applying the standard luminous efficacy for daylight (179
lumens/watt) and CIE photopic weighting factors to convert RGB irradiance into a single lux
value."* Line 189 gives the actual `rcalc` expression the sourced workflow script runs:

    rcalc -e '$1=179*($1*0.265+$2*0.670+$3*0.065)'

`to_lux` is that expression, unchanged, with `$1`/`$2`/`$3` renamed to the RGB irradiance tuple
`rtrace -I` actually emits (R, G, B, each W/m^2).

**Check 3.3 (docs/plan.md) fixes this function's name and signature.** `to_lux` takes a 3-tuple and
returns a float; the four reference values below are asserted to 1e-9 and must not move:
`to_lux((1,0,0)) == 47.435`, `to_lux((0,1,0)) == 119.93`, `to_lux((0,0,1)) == 11.635`,
`to_lux((1,1,1)) == 179.0`. All four fall straight out of the one expression above -- they are not
separately-fitted constants, which is the whole point of owning the conversion in one place.

**The DF normalisation, and its asymmetry.** [workflows-point-in-time-to-annual.md] lines 476-480
run:

    # To achieve a reference of 10,000 lux, we convert this to irradiance
    # using the default luminous efficacy of 179 lm/W.
    # Calculation: 10000 lux / 179 lm/W = 55.866 W/m^2
    HORIZ_IRRAD=55.866
    gensky -c -B $HORIZ_IRRAD

10000 / 179 is 55.8659217877..., not 55.866 -- the sourced script rounds to three decimals before
handing the number to `gensky -B`. That rounding is deliberate and is what `gensky` is actually
told to build the reference sky from, so `REFERENCE_IRRADIANCE_WM2` below is the *literal*
55.866, not the exact quotient, with the difference stated here rather than left for a reader to
notice in the fifth decimal. Check 3.4 (a sibling task, `rad_engine.rtrace`) asserts the resulting
unobstructed sensor reads 10 000 lux +/- 2%, which is orders of magnitude looser than the ~1e-5
relative error the rounding itself introduces -- so the rounding is inert for every consumer this
phase has, but a caller reading this module's source should not have to independently rediscover
that 55.866 is a rounded literal rather than a second, disagreeing constant. `REFERENCE_ILLUMINANCE_LUX`
(10000, exact) is the number `daylight_factor` actually normalises against, per
[docs/tool-surface.md] §10: *"DF = 100 x E_interior / 10 000"*. `REFERENCE_IRRADIANCE_WM2` is
recorded only because it is the number the sourced `gensky -B` invocation is built from -- callers
computing a DF percent from a measured interior lux want `REFERENCE_ILLUMINANCE_LUX`, not this one.
"""

from __future__ import annotations

__all__ = [
    "CIE_PHOTOPIC_WEIGHTS",
    "LUMINOUS_EFFICACY_LM_PER_W",
    "REFERENCE_ILLUMINANCE_LUX",
    "REFERENCE_IRRADIANCE_WM2",
    "daylight_factor",
    "to_lux",
]

#: Standard luminous efficacy for daylight, lm/W (workflows-point-in-time-to-annual.md line 108:
#: "the standard luminous efficacy for daylight (179 lumens/watt)").
LUMINOUS_EFFICACY_LM_PER_W = 179.0

#: CIE photopic weighting factors (R, G, B), applied to `rtrace -I`'s RGB irradiance before scaling
#: by `LUMINOUS_EFFICACY_LM_PER_W`. Order matches the sourced `rcalc` expression's `$1, $2, $3`
#: (workflows-point-in-time-to-annual.md line 189):
#: ``rcalc -e '$1=179*($1*0.265+$2*0.670+$3*0.065)'``.
CIE_PHOTOPIC_WEIGHTS = (0.265, 0.670, 0.065)

#: The reference horizontal illuminance a forced-overcast daylight-factor sky targets, lux
#: (docs/tool-surface.md §10: "DF = 100 x E_interior / 10 000"). Exact -- DF is defined against
#: this number, not against whatever `gensky -B` actually delivers once rounded to a literal.
REFERENCE_ILLUMINANCE_LUX = 10000.0

#: The literal `gensky -c -B` irradiance the sourced workflow script uses to build a
#: `REFERENCE_ILLUMINANCE_LUX` sky, W/m^2 (workflows-point-in-time-to-annual.md lines 476-480).
#:
#: **This is the rounded literal the source actually runs, not `REFERENCE_ILLUMINANCE_LUX /
#: LUMINOUS_EFFICACY_LM_PER_W`.** That exact quotient is 55.8659217877...; the sourced script
#: rounds it to three decimals -- "Calculation: 10000 lux / 179 lm/W = 55.866 W/m^2" -- and passes
#: the rounded value to `gensky -B`. The two are kept as two separate named constants rather than
#: one derived from the other so that whichever one a caller reaches for is the one the docstring
#: says it is: a caller building the actual `gensky` command line wants this literal (it is what
#: the real Radiance run was verified against); a caller normalising a measured interior lux into a
#: DF percent wants `REFERENCE_ILLUMINANCE_LUX`, the round number DF is defined against, not this
#: rounded irradiance. `daylight_factor` below uses only `REFERENCE_ILLUMINANCE_LUX` for exactly
#: this reason -- the DF formula (docs/tool-surface.md §10) is stated in lux, not W/m^2, so
#: `REFERENCE_IRRADIANCE_WM2` never enters that arithmetic at all.
REFERENCE_IRRADIANCE_WM2 = 55.866


def to_lux(rgb_irradiance: tuple[float, float, float]) -> float:
    """RGB irradiance (W/m^2, `rtrace -I` order) -> photopic illuminance (lux).

    ``179 * (0.265 R + 0.670 G + 0.065 B)`` -- the sourced `rcalc` expression
    (workflows-point-in-time-to-annual.md line 189), unchanged. See the module docstring for the
    full derivation and check 3.3's four fixed reference values.

    **Negative components are accepted, not refused or clamped.** `rtrace`'s Monte Carlo
    interpolation can legitimately return a small negative irradiance component near a sharp
    illumination boundary -- the ambient cache interpolates between sample points and nothing
    about that interpolation guarantees non-negativity, even though true physical irradiance is
    never negative. Two wrong ways to handle it: refusing outright treats every such point as a
    hard error, when the actual cause is a few-percent-of-a-sample-error near an edge, not a
    misconfigured study -- exactly the `DEEP_POINT_NOISE` situation
    (docs/tool-surface.md §10 advisories) callers already expect to see and work around by raising
    `-ad`/`-ab`, not by having the run abort. Silently clamping each component to zero before
    weighting would hide the noise inside the reported lux value instead: a clamped point reads as
    a slightly-too-bright but otherwise unremarkable result, indistinguishable from a point that
    never had a negative sample, and a caller auditing "why is this point brighter than its
    neighbours" has nothing left to find. So this function does neither: it applies the formula
    exactly as given to whatever components it is handed, negative or not, and lets the result be
    what it is (typically a very small perturbation of an otherwise-correct lux value, since a
    physically-plausible negative excursion from noise is small relative to the true irradiance).
    A caller that wants to flag or clamp noisy input is `rad_engine.rtrace`, which has the point
    index and the context (proximity to an aperture, ambient parameters) to decide whether a
    negative component is `DEEP_POINT_NOISE` or something worse -- this module only converts
    numbers, per CONTEXT.md's layer rule that a threshold or a judgement belongs one layer up from
    pure arithmetic.
    """
    r, g, b = rgb_irradiance
    w_r, w_g, w_b = CIE_PHOTOPIC_WEIGHTS
    return LUMINOUS_EFFICACY_LM_PER_W * (r * w_r + g * w_g + b * w_b)


def daylight_factor(
    interior_lux: float, reference_lux: float = REFERENCE_ILLUMINANCE_LUX
) -> float:
    """Daylight factor, percent: ``100 * interior_lux / reference_lux``.

    docs/tool-surface.md §10: *"DF = 100 x E_interior / 10 000"*. `reference_lux` defaults to
    `REFERENCE_ILLUMINANCE_LUX` (10 000 lux, the forced-overcast sky's target) but is accepted as
    an argument rather than hardwired, since the arithmetic itself has no opinion on the reference
    -- only `daylight_factor`'s caller (`run_point_in_time`, forcing the CIE overcast sky) does.

    **Not clamped to 100%.** Check 3.4's own unobstructed sensor sits at *exactly* 100% under the
    reference sky by construction, but that is the ceiling for an idealised, fully-unobstructed
    point directly under the forced overcast sky this function is normally fed from -- it is not a
    ceiling the arithmetic itself should impose. An interior sensor result and a reference-sky
    illuminance are two independent numbers computed however the caller chose (measured, a
    different sky, a modelling shortcut); clamping their ratio would silently discard evidence that
    something upstream disagrees with the 10 000 lux assumption (a mis-forced sky, a units error, a
    non-standard reference) exactly where that evidence is cheapest to see. `daylight_factor` is
    arithmetic on two floats, not a DF-specific validity check on either one.

    **`reference_lux <= 0` is refused, not divided.** A zero or negative reference illuminance is
    not a small or noisy input the way a negative RGB component in `to_lux` is -- there is no sky
    the reference could describe, no rounding history that could produce it (`gensky -B` cannot
    emit a non-positive illuminance), and dividing by it produces `inf`, `-inf`, or `nan` silently,
    which is a worse failure than an immediate, legible one. Refused with `ValueError` rather than
    a `GridError`-shaped code-carrying exception (contrast `rad_core.grids.GridError`): this
    module's callers are `rad_engine`/`rad_mcp` computing an internal ratio, not a client-facing
    tool argument with an allocated advisory code in the register -- and this module may allocate
    none (CONTEXT.md; docs/tool-surface.md's advisory register is closed at 141 codes). A caller
    that needs to surface this as a wire refusal wraps it in its own coded exception, the same way
    `rad_engine.project.ProjectError` wraps a bare `ValueError` from a lower layer.
    """
    if reference_lux <= 0:
        raise ValueError(
            f"reference illuminance must be positive, got {reference_lux:g} lux -- a daylight "
            "factor has no meaning against a zero or negative reference."
        )
    return 100.0 * interior_lux / reference_lux
