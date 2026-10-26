"""`rtrace` for point-in-time illuminance: quality presets, argv construction, spawning, parsing.

**Scope** (docs/tool-surface.md §10, docs/plan.md Phase 3): this module owns the `rtrace` command
line, the quality presets and the two direct/ambient flag rules that ride on top of them, and
turning `rtrace -I` output into RGB irradiance tuples in grid order. It does **not** own sky
generation (`rad_engine.sky`, a sibling task), the RGB -> photopic arithmetic
(`rad_core.photometry.to_lux`, sibling task -- see the ``rcalc`` decision below), or the wire
contract (`job_id`, `octree_cube`, `source`, later task). ``tests/test_purity.py`` and
``tests/test_advisories.py`` both hold this module to the same boundary every other `rad_engine`
module already keeps.

**Why no `rcalc`.** The sourced pipeline
([workflows-point-in-time-to-annual.md](../../docs/specs/workflows-point-in-time-to-annual.md) line
188) pipes `rtrace -I` into ``rcalc -e '$1=179*($1*0.265+$2*0.670+$3*0.065)'``. That expression is
*exactly* ``rad_core.photometry.to_lux`` -- same coefficients, same 179 lm/W constant -- typed a
second time in a shell string nothing in this repository tests. Ruling R30 (docs/plan.md) already
made this call for the analogous `trans` reflectance/transmittance split: physics lives in
`rad_core`, `rad_engine` consumes it, "so there is one arithmetic path by construction rather than
by discipline" -- rather than by two people remembering to keep two copies in sync. Spawning a
second Radiance process per point-in-time run to do arithmetic `to_lux` already does in Python buys
nothing except a second place the coefficients could drift, and drift here is exactly what
`rad_core.photometry`'s own module docstring says it exists to prevent (finding D5: the spec itself
states the conversion two different ways). So this module parses `rtrace -I`'s three-float-per-line
stdout into RGB tuples and leaves the RGB -> lux conversion to whatever caller has `to_lux` in scope
one layer up -- `run_point_in_time`'s composition, not this module, since converting here would
require importing `rad_core.photometry` and This Module Only Builds Argv And Parses is the line
`tests/test_purity.py` draws for every other `rad_engine` module.

Numerical equivalence to the sourced `rcalc` expression is asserted at the arithmetic's own home,
`tests/test_photometry.py` (sibling task) -- `to_lux((1,1,1)) == 179.0`, etc. -- not re-derived here;
re-deriving it here would be the second arithmetic path this decision exists to avoid.

**Why `-w` is dropped even though the sourced script carries it.** The sourced pipeline runs
`rtrace -I -h -w ...`. `-w` is documented as "Boolean switch to suppress warning messages" -- not a
data-stream separator; `-h` already does the header-suppression job stream separation actually needs
(radiance-practical-guide.md, `rtrace` and `oconv` `-w` entries, and `test_room`'s own
`TestStreamSeparation` uses `-h -w` together for the *header*, not the warnings, since a header line
would otherwise land in the stdout token stream `float()` must parse). Passing `-w` here would mean
`rtrace` never writes anything to stderr, and this project's whole advisory system
(`rad_engine.advisories.classify_line`) exists to classify exactly those lines -- check 1.8 was
written because a benign warning must not look like a failure, which presupposes the warning was
allowed to reach stderr in the first place. Suppressing it at the source is silence, not a good
outcome, and is worse than the failure check 1.8 guards against: a run whose ambient parameters are
badly matched to the scene (`AMBIENT_ARTIFACT_LIKELY`-class conditions) writes exactly the kind of
warning this table needs to see. So this module keeps `-h` (needed for stream cleanliness) and drops
`-w` (would blind the advisory pipeline for a class of finding this project was built to surface).

**`-I -h` are both non-negotiable and are not part of the quality dict** the caller can override.
`-I` selects irradiance mode -- the whole point of this tool; `-h` keeps the header off stdout so
"every token of captured stdout parses as a float" (check 3.7) holds. Neither is exposed for
override, unlike the five ambient flags and `-dt`/`-dc`, because there is no scenario in this tool's
contract where either should change.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Final

from rad_core.quality import (
    AMBIENT_PRESETS as QUALITY_PRESETS,
    QualityParameters,
    ambient_interpolation_radius_m,
)

from .advisories import EngineAdvisory
from .env import RadianceEnv
from .run import Result, run

#: A 3-tuple of floats -- a point or an RGB triple, depending on context. Defined locally rather
#: than imported from `rad_core.ugr`: this module's own purity ratchet
#: (`tests/test_rtrace.py::TestPurity`) holds it to building argv and parsing output only, and an
#: import solely for a type alias would be exactly the kind of cross-module dependency that ratchet
#: watches for.
Vec = tuple[float, float, float]

__all__ = [
    "AMBIENT_BOUNCES_MINIMUM_RECOMMENDED",
    "ESCAPED_MODIFIER",
    "QUALITY_PRESETS",
    "QualityParameters",
    "RtraceOutputError",
    "RtraceRequest",
    "ambient_bounces_low_advisory",
    "ambient_interpolation_radius_m",
    "build_rtrace_argv",
    "direct_exact_forced",
    "parameters_dict",
    "parse_irradiance",
    "parse_rtrace_hits",
    "resolve_quality",
    "run_rtrace",
]


#: "An -ab value of at least 3 is recommended"
#: ([workflows §5.1](../../docs/specs/workflows-point-in-time-to-annual.md)) for "standard
#: point-in-time illuminance reports". `AMBIENT_BOUNCES_LOW` fires strictly below this, which is why
#: `draft`'s own `-ab 2` warns by construction (docs/plan.md check 3.8) -- draft is fine for a view
#: setup, not for a number someone will compare against a standard.
AMBIENT_BOUNCES_MINIMUM_RECOMMENDED = 3


_OVERRIDABLE_FIELDS = {
    "ab": "ambient_bounces",
    "ad": "ambient_divisions",
    "as": "ambient_super_samples",
    "ar": "ambient_resolution",
    "aa": "ambient_accuracy",
}


#: The four override keys `rtrace` requires to be integers. `-aa` is the one genuine float.
_INTEGER_OVERRIDES = frozenset({"ab", "ad", "as", "ar"})

#: **Which integer flags accept zero.** Decided per flag against measured Radiance 6.1a behaviour
#: on the office fixture, because the four flags do not behave alike and a uniform rule would
#: either refuse a legal calculation or admit a broken one.
#:
#: * ``-ab 0`` is **legal, and the measurement is the reason it is not refused here.** Zero ambient
#:   bounces on an *interior* sensor returns exactly `0.0` -- but that is correct physics, not a
#:   defect: every photon reaching an interior point has bounced or passed through glazing at least
#:   once, so a zero-bounce calculation genuinely has nothing to report. On an *unobstructed*
#:   sensor in the same octree, ``-ab 0`` returns ``4.155760e+02`` against ``-ab 4``'s
#:   ``4.730378e+02`` -- a real direct-sky measurement a caller may legitimately want. Refusing
#:   ``-ab 0`` would therefore refuse a valid exterior study in order to prevent an interior result
#:   that is arithmetically right. `AMBIENT_BOUNCES_LOW` (warn) is the register's existing, correct
#:   vocabulary for "this is lower than an interior study should use", and it already fires.
#: * ``-as 0`` is **legal**: `-as` is the *super*-sample count layered on top of `-ad`, and zero
#:   means "do not refine". Measured: ``-as 0`` returns ``1.600006e+00`` where ``-as 512`` returns
#:   ``1.438862e+00`` -- a real value, differently sampled, not an absence of one.
#: * ``-ad 0`` is **refused**: `-ad` is the number of sample rays cast per ambient bounce, so zero
#:   divisions means the indirect calculation draws no samples at all and every interior *and*
#:   exterior sensor reads `0.0`. Unlike ``-ab 0`` there is no sensor placement at which this
#:   produces a meaningful number.
#: * ``-ar 0`` is **refused**: it divides the ambient interpolation radius
#:   (``Rmin = maxSize x aa / ar``), and is already refused one layer up with its own message.
#:
#: Negative values are refused for **all four** regardless: none of the four flags has a meaning
#: below zero, and every negative value measured returned either an all-zero grid (`-ab -5`,
#: `-ad -1024`) or a silently-different number from the one the caller asked for (`-ar -512`,
#: `-as -1`) -- the latter is the more dangerous of the two, because it looks like a real answer.
_ZERO_IS_LEGAL = frozenset({"ab", "as"})

#: **How far above the highest published preset an override may go.** The floors below were each
#: decided from a measurement of what the flag *does* at zero; there was no ceiling of any kind
#: until 2026-08-31, and the whole-branch security review graded that Important:
#: ``quality={"ab": 1000000, "ad": 10**9}`` was accepted and rendered ``-ab 1000000 -ad 1000000000``
#: onto an `rcontrib` argv, on a queue whose own docstring says *"Concurrency 1. One worker thread
#: runs one job at a time"* -- so one such call holds the only worker for as long as it runs, and
#: no `Step` in the repo sets `timeout_s`.
#:
#: **Stated honestly: this is a policy bound, not a measured performance cliff.** The floors could be
#: measured because a wrong floor produces a *wrong number* (`-ad 0` returns 0.0 lux for every
#: sensor, and rtrace does not complain). A ceiling produces a *slow* run, and the point at which
#: slow becomes unacceptable is a property of the host and the scene, not of the flag -- there is no
#: single number to measure. So the rule is a ratio to the published presets rather than four
#: independent guesses: **sixteen times the `high` preset's value for each flag**. `high` is the
#: most expensive setting this server documents (``-ab 7 -ad 4096 -as 2048 -ar 1024``), sixteen
#: times it is an order of magnitude beyond anything the source workflows describe, and a study that
#: genuinely needs more than that is one whose operator should be running Radiance directly rather
#: than through a concurrency-1 queue with no time bound. Nothing legitimate is refused; what is
#: refused is a value whose only effect is to occupy the queue.
#:
#: Kept as one rule with one multiplier rather than four literals, so the reasoning is checkable and
#: a change to a preset carries its ceiling with it.
_OVERRIDE_CEILING_FACTOR: Final[int] = 16
_INTEGER_OVERRIDE_CEILINGS: Final[dict[str, int]] = {
    "ab": _OVERRIDE_CEILING_FACTOR * QUALITY_PRESETS["high"].ambient_bounces,
    "ad": _OVERRIDE_CEILING_FACTOR * QUALITY_PRESETS["high"].ambient_divisions,
    "as": _OVERRIDE_CEILING_FACTOR * QUALITY_PRESETS["high"].ambient_super_samples,
    "ar": _OVERRIDE_CEILING_FACTOR * QUALITY_PRESETS["high"].ambient_resolution,
}

#: `-aa` is an *accuracy tolerance*, a fraction, so its ceiling is not a ratio to a preset: above
#: 1.0 it asks for an interpolation error larger than the value being interpolated, which is not a
#: coarser calculation but a meaningless one. `0.0` stays legal -- it is the `reference` preset's own
#: value and disables ambient interpolation entirely (see `ambient_interpolation_radius_m`).
_AMBIENT_ACCURACY_MAX: Final[float] = 1.0


def _coerce_override(key: str, value: float) -> float:
    """Narrow an integer-valued override back to `int`, refusing a genuinely fractional one.

    The wire type for `quality` is ``dict[str, float]`` (docs/tool-surface.md §10), and JSON has one
    number type, so a client asking for ``{"ab": 3}`` may arrive here as ``3.0``. Passed through,
    that reaches the command line as ``-ab 3.0`` and **`rtrace` refuses to start**: measured against
    Radiance 6.1a, ``rtrace -I -h -ab 3.0 -ad 1024 ...`` exits 1 with ``rtrace: fatal - command line
    error at '-ab'``. Through the wire that surfaced as a refusal quoting a raw Radiance fatal, so
    every documented `quality` override was broken for every caller -- the four presets were
    unaffected, because their own values are `int` literals and never make this trip.

    A fractional value is **refused rather than truncated**: `-ab 3.7` is not a typo for `-ab 3`, and
    silently discarding the fraction would answer a question the caller did not ask (the same reason
    `rad_core.photometry` refuses a zero reference rather than dividing). `-aa` is left alone; it is
    the one flag whose value is genuinely continuous.

    **`bool` is refused, and the limit of that refusal is worth stating precisely.** `bool` is an
    `int` subclass, so ``{"ab": True}`` is a well-formed whole number to every check below and would
    otherwise reach the command line as ``-ab 1``. This guard catches that for any *in-process*
    caller. It does **not** catch it over the wire, and cannot: `quality`'s published schema is
    ``dict[str, float]``, so Pydantic coerces JSON ``true`` to ``1.0`` at the schema boundary before
    this module is reached (verified: ``TypeAdapter(dict[str, float]).validate_python({"ab": True})``
    -> ``{'ab': 1.0}``). By the time any Python here runs, ``true`` and ``1`` are the same value and
    are no longer distinguishable. Narrowing the published schema so ``true`` is rejected as a type
    error is a change to the wire contract in docs/tool-surface.md, not a change this module can
    make -- it is reported rather than worked around.

    **Non-positive values are refused per flag, not uniformly** -- see `_ZERO_IS_LEGAL` for which
    flags accept zero and the measurement behind each. This is the refusal that matters most: with
    the full argv this tool builds, `rtrace` does *not* fatal on ``-ad 0`` or ``-ab -5`` the way a
    bare `rtrace` does. It runs, and returns `0.0` for every sensor, which this server then reported
    as a successful daylight result with a mean of `0.0` lux.
    """
    # **Finiteness first, and for every key rather than only the four integer ones.** `nan` and
    # `inf` reach here from the wire -- pydantic-core's JSON parser reads `1e999`, `NaN` and
    # `Infinity` as floats for a declared `float` -- and the two behaved differently and both wrongly:
    #
    # - on an integer flag, `int(inf)` raises `OverflowError`, which is an `ArithmeticError` and
    #   therefore in **none** of the exception families `run_point_in_time` catches, so
    #   `quality={"ab": 1e999}` left the tool as `isError=True` with no `structuredContent` at all;
    # - on `aa`, which took the early return below, nothing was checked at all:
    #   `resolve_quality({"aa": float("nan")})` produced `-aa nan` on a real argv (measured), and C's
    #   `atof` reads that as a NaN. No crash, no advisory, no refusal -- a wrong ambient-accuracy
    #   parameter with a plausible-looking run around it.
    #
    # `int(nan)` raises `ValueError`, which *was* caught, so the `NaN` case on an integer flag was
    # already a clean refusal. That asymmetry is the tell: one wording for one rule, made before the
    # dispatch on `key` so no flag can be added later that misses it.
    if not math.isfinite(value):
        raise ValueError(
            f"quality override -{key} {value!r} is not a finite number. It would reach the Radiance "
            f"command line as `-{key} {value!r}`, where atof reads it as a NaN or an infinity and "
            "the run proceeds without complaint at a setting nobody chose."
        )

    if key not in _INTEGER_OVERRIDES:
        if key == "aa" and not 0.0 <= value <= _AMBIENT_ACCURACY_MAX:
            raise ValueError(
                f"quality override -aa {value} is refused: it must be between 0.0 and "
                f"{_AMBIENT_ACCURACY_MAX}. -aa is a fractional error tolerance on the ambient "
                "interpolation, so a negative value has no meaning and a value above 1 asks for an "
                "error larger than the quantity being interpolated. The presets use 0.0 "
                "(reference, interpolation disabled) to 0.25 (draft)."
            )
        return value
    if isinstance(value, bool):
        raise ValueError(
            f"quality override -{key} must be a whole number, got {value!r}; JSON true/false is not "
            f"a sample count. Pass the integer you mean (e.g. -{key} 1) rather than a boolean, "
            "which Python would silently read as 1 or 0."
        )
    as_int = int(value)
    if as_int != value:
        raise ValueError(
            f"quality override -{key} must be a whole number, got {value!r}; rtrace refuses a "
            f"fractional value for this flag ('command line error at '-{key}'')"
        )
    if as_int < 0 or (as_int == 0 and key not in _ZERO_IS_LEGAL):
        floor = 0 if key in _ZERO_IS_LEGAL else 1
        raise ValueError(
            f"quality override -{key} {as_int} is refused: it must be at least {floor}. With the "
            "full command line this tool builds, rtrace does not reject this value -- it runs and "
            "returns 0.0 for every sensor, which would be published as a successful result with a "
            "mean of 0.0. A study cannot be run at this setting; raise it to a positive value (the "
            "presets use -ab 2..7, -ad 256..4096, -as 128..2048, -ar 128..1024)."
        )
    ceiling = _INTEGER_OVERRIDE_CEILINGS[key]
    if as_int > ceiling:
        raise ValueError(
            f"quality override -{key} {as_int} is refused: it must be at most {ceiling}, "
            f"{_OVERRIDE_CEILING_FACTOR}x the 'high' preset's -{key} "
            f"{ceiling // _OVERRIDE_CEILING_FACTOR}. This is a queue bound, not a physics one: jobs "
            "run one at a time and no step carries a time limit, so a setting an order of magnitude "
            "beyond the most expensive documented preset occupies the only worker for as long as it "
            "runs. Use a preset, or a value within it."
        )
    return as_int


def resolve_quality(
    quality: str | dict[str, float | str] | QualityParameters,
) -> QualityParameters:
    """A preset name, or a dict overriding individual flags on top of `standard`.

    docs/tool-surface.md §10: "A dict overrides individual flags and the result reports the
    effective set." A bare dict (no preset named inside it) has no base of its own to override, so
    the only sensible base is the tool's own default quality, `standard` -- the same default
    `run_point_in_time`'s own `quality` parameter states. A dict may also carry a `"base"` key naming
    a preset explicitly, so a caller can say "high, but -ab 5" without retyping the other four flags.

    Overriding `-av` is refused by `AMBIENT_VALUE_SET` at the `run_point_in_time` tool layer
    (docs/tool-surface.md §10, engine invariant 5) -- this function does not accept an `av` key at
    all, so there is nothing here to refuse; a caller that wants to reject an `av` key from client
    input does so before calling this function, since only that layer has the coded refusal to raise.
    """
    if isinstance(quality, QualityParameters):
        return quality
    if isinstance(quality, str):
        try:
            return QUALITY_PRESETS[quality]
        except KeyError:
            raise ValueError(
                f"unknown quality preset {quality!r}; choose one of "
                f"{sorted(QUALITY_PRESETS)} or pass a dict of overrides"
            ) from None

    # `"base"` is the one key whose value is a preset *name* rather than a flag value, which is why
    # the annotation is `float | str` rather than `float` (the five overridable flags are numeric;
    # this one is not). A non-string `base` -- `{"base": 3}` off the wire -- is refused through the
    # same `ValueError` as an unknown name rather than raising `TypeError` from inside the lookup:
    # both are "that is not a preset", and one recovery shape.
    base_name = quality.get("base", "standard")
    if not isinstance(base_name, str) or base_name not in QUALITY_PRESETS:
        raise ValueError(
            f"unknown quality base {base_name!r}; choose one of {sorted(QUALITY_PRESETS)}"
        )
    base = QUALITY_PRESETS[base_name]

    changes: dict[str, float] = {}
    for key, value in quality.items():
        if key == "base":
            continue
        if key not in _OVERRIDABLE_FIELDS:
            raise ValueError(
                f"quality override key {key!r} is not one of the five ambient flags this preset "
                f"controls ({sorted(_OVERRIDABLE_FIELDS)}); -av is never accepted here -- "
                "AMBIENT_VALUE_SET refuses it at the tool layer, before this function is reached"
            )
        # `"base"` is the only key whose value is a string (handled above, and `continue`d past), so
        # every key reaching here is one of the five numeric flags. A string arriving on one of them
        # -- `{"ab": "5"}` from a hand-built call -- is refused here rather than at the command line,
        # where it would surface as a raw Radiance fatal the way `-ab 3.0` did before
        # `_coerce_override` existed (see that function's docstring).
        if isinstance(value, str):
            raise ValueError(
                f"quality override {key!r} must be a number, not the string {value!r}; only "
                '"base" takes a string, and it names a preset'
            )
        changes[_OVERRIDABLE_FIELDS[key]] = _coerce_override(key, value)
    return replace(base, **changes)


def ambient_bounces_low_advisory(parameters: QualityParameters) -> EngineAdvisory | None:
    """`AMBIENT_BOUNCES_LOW` (warn) when `-ab` is below the recommended floor for an interior
    illuminance study -- docs/tool-surface.md §10 advisories table, sourced from workflows §5.1.
    `None` when `-ab` meets or exceeds it. This is a judgement about the *parameters*, so it is
    built directly rather than by classifying a Radiance stderr line -- the same pattern
    `rad_engine.scene._resolution_advisory` already uses for a finding proven by a number this
    module computed itself, not by anything Radiance wrote.
    """
    if parameters.ambient_bounces >= AMBIENT_BOUNCES_MINIMUM_RECOMMENDED:
        return None
    return EngineAdvisory(
        code="AMBIENT_BOUNCES_LOW",
        severity="warn",
        message=(
            f"-ab {parameters.ambient_bounces} is below the recommended minimum of "
            f"{AMBIENT_BOUNCES_MINIMUM_RECOMMENDED} for an interior illuminance study "
            '("An -ab value of at least 3 is recommended", workflows-point-in-time-to-annual.md '
            "§5.1). Expect indirect light to be undersampled away from the window."
        ),
        lines=(),
    )


def direct_exact_forced(
    parameters: QualityParameters, *, sky_has_sun: bool, quantity: str, luminaires: bool = False
) -> tuple[QualityParameters, EngineAdvisory | None]:
    """Apply `DIRECT_EXACT_FORCED` (info) when the sky has a sun and the quantity is illuminance,
    **or luminaires are on** (Phase 11a spec §7): at `-ab 0`, the defaults' source culling reads a
    5 x 4 array **deterministically low** -- 0.26-0.95 % low at the worst point and 0.00-0.16 % low
    in the grid mean (spec §1.4, ruling R588; the 0.79-1.07 % figure this docstring used to cite was
    the per-point spread, not the bias itself) -- so every lit run traces every lamp instead.

    docs/tool-surface.md §10: "The sky has a sun and the quantity is illuminance, so -dt 0 -dc 1
    were set." Sourced verbatim: *"For high-accuracy simulations, especially those involving direct
    sunlight where no contribution can be missed, it is standard practice to set -dt 0 and -dc 1.
    This combination disables the statistical approximation"*
    (docs/specs/radiance-practical-guide.md §direct, line 93). "The presets otherwise only set
    ambient parameters, which leaves the direct calculation approximating shadow tests
    statistically -- acceptable in a picture, not in a compliance number" (docs/tool-surface.md §10).

    Returns the (possibly updated) parameters and the advisory, or `(parameters, None)` unchanged
    when the condition does not hold -- luminance and daylight-factor quantities are unaffected
    (docs/tool-surface.md §10: "Daylight factor is unaffected -- the overcast sky has no disc to
    smear"), and neither is an overcast/sunless sky.
    """
    sun_case = sky_has_sun and quantity == "illuminance"
    if not (sun_case or luminaires):
        return parameters, None
    forced = replace(parameters, direct_threshold=0.0, direct_certainty=1.0)
    if sun_case:
        message = (
            "the sky has a sun and the quantity is illuminance, so -dt 0 -dc 1 were set. This "
            "disables the statistical shadow-testing approximation so no direct-sun contribution "
            "can be missed (radiance-practical-guide.md §direct)."
        )
    else:
        message = (
            "luminaires are on, so -dt 0 -dc 1 were set. This disables the statistical shadow-testing "
            "approximation so no lamp in an array is culled."
        )
    advisory = EngineAdvisory(code="DIRECT_EXACT_FORCED", severity="info", message=message, lines=())
    return forced, advisory


def parameters_dict(parameters: QualityParameters) -> dict[str, float | int | None]:
    """The wire `parameters` dict's field set (docs/tool-surface.md §10), for a caller assembling
    the full `run_point_in_time` result. Keys spelled as the wire names it: `ab, ad, as, ar, aa, lw,
    dt, dc, dj, av`.

    **`lw` and `dj` are always `None` here, not a guessed rtrace default.** Neither this tool's
    presets (§1.2) nor `DIRECT_EXACT_FORCED` ever sets `-lw` or `-dj` -- the corpus states what each
    flag *does* (docs/specs/radiance-practical-guide.md) but nowhere states a numeric default for
    plain `rtrace` the way it does for, say, `-r maxres` (`oconv`, default 16384) or `-aC` (`mkpmap`,
    default 1M). Reporting an invented number in a field this project treats as "the effective set,
    post-override" would misstate what was actually passed to `rtrace`; `None` says truthfully that
    this run left the flag at whatever `rtrace`'s own binary default is, unstated by this tool.
    `dt`/`dc` are `None` unless `direct_exact_forced` has run and set them.

    **What that default is, and why leaving `-lw` unset is safe here (ruling R41).** Unstated by the
    corpus, but measurable: ``rtrace -defaults`` prints ``-lw 1.00e-04`` on this install. Engine
    invariant 4 asks for ``lw ~= 1/ad``, and the binary default is *stricter* than that at every
    preset -- draft 39x, standard 10x, high 2.4x -- so rays are cut later than the invariant asks
    and none terminates prematurely. Measured on the office fixture at the three check-3.1 points,
    default versus an explicit ``-lw = 1/ad``: no detectable difference (deep point, ``-ad 4096``,
    n=14 per condition, **-0.42 %, t = 0.47**).

    **The crossover is the part worth knowing.** Past ``-ad 10000``, ``1/ad`` falls below
    ``1.0e-04`` and the default becomes the *looser* of the two, so the invariant stops holding by
    accident. That is reachable today -- check 3.1's own fixture runs at ``-ad 32768`` (raised from
    16 384 by ruling R96) and any ``quality={"ad": ...}`` past 10 000 is a legal override -- and was
    measured in that regime (13 m, ``-ad 16384``, n=8 per condition): **-1.06 %, t = 1.16**, still
    not detectable. The measurement was made one rung below where check 3.1 now runs and is *not*
    re-stated as if it had been made at 32 768; what it establishes is that the crossover is already
    well past and remains undetectable there, which R96's rung does not change. So this stays unset
    on accuracy grounds; the remaining case for emitting it is that the spec states an invariant the
    code does not enforce, which becomes load-bearing when a phase sets ``-ad`` past 10 000 by
    default.

    **`av` is always `0`** -- docs/tool-surface.md §10 engine invariant 5: "`-av` is zero on every
    quantitative run" for illuminance/DF/glare/matrix work, and a `quality` dict override setting
    `-av` is refused (`AMBIENT_VALUE_SET`) before it would ever reach this function.
    """
    return {
        "ab": parameters.ambient_bounces,
        "ad": parameters.ambient_divisions,
        "as": parameters.ambient_super_samples,
        "ar": parameters.ambient_resolution,
        "aa": parameters.ambient_accuracy,
        "lw": None,
        "dt": parameters.direct_threshold,
        "dc": parameters.direct_certainty,
        "dj": None,
        "av": 0,
    }


@dataclass(frozen=True)
class RtraceRequest:
    """Everything `build_rtrace_argv` needs to build one `rtrace -I` command line."""

    octree: Path
    parameters: QualityParameters
    processes: int | None = None
    """`rtrace -n`: `None` passes no flag. The inline callers pass `rad_engine.cores.worker_cores()`
    (R780); `rad_engine.export` passes none, so an exported script carries no machine's count."""


def build_rtrace_argv(request: RtraceRequest) -> list[str]:
    """The `rtrace` argv for a point-in-time irradiance run, in the sourced flag order
    ([workflows-point-in-time-to-annual.md](../../docs/specs/workflows-point-in-time-to-annual.md)
    line 188): ``rtrace -I -h -w -ab AB -ad AD -as AS -ar AR -aa AA OCTREE``, minus `-w` -- see the
    module docstring for why `-w` is dropped -- and with `-dt`/`-dc` appended only when
    `DIRECT_EXACT_FORCED` has set them, since `None` means "do not pass this flag", not "pass it as
    the Python `None`", and `-n` before the octree when `request.processes` is set (R780).

    Returns a plain argv **leading with the bare binary name** -- `rad_engine.run.run`
    **substitutes** that token with the resolved binary (`env.path("rtrace")`), splitting
    `binary, *args = argv` and spawning `[str(env.path(binary)), *args]`.
    Nothing is added, and the name the old wording called missing is the one being resolved. Same
    contract `build_octree` follows for `oconv`. Corrected by the whole-branch fix wave (ruling
    R323); this module names `run`, not `run_to_file`, which is why a sweep keyed on the latter
    found only half of it. Points arrive on stdin (`rad_engine.sensors.write_points`'s `.pts` file, read by the
    caller and passed as `run`'s `stdin`) -- this function has no sensor data to see and does not
    read the file, matching `sensors.py`'s own boundary ("It spawns nothing").
    """
    p = request.parameters
    argv: list[str] = [
        "rtrace", "-I", "-h",
        "-ab", str(p.ambient_bounces),
        "-ad", str(p.ambient_divisions),
        "-as", str(p.ambient_super_samples),
        "-ar", str(p.ambient_resolution),
        "-aa", repr(float(p.ambient_accuracy)),
    ]
    if p.direct_threshold is not None:
        argv.extend(["-dt", repr(float(p.direct_threshold))])
    if p.direct_certainty is not None:
        argv.extend(["-dc", repr(float(p.direct_certainty))])
    if request.processes is not None:
        # R780: immediately before the octree. No builder passes `-P`/`-PP`, a `t` output letter,
        # `-x`/`-y` or `-af`, the four things `rtrace -n` conflicts or interacts with (rtrace.1).
        argv.extend(["-n", str(int(request.processes))])
    argv.append(str(request.octree))
    return argv


class RtraceOutputError(RuntimeError):
    """`rtrace -I` stdout did not parse as whole triples of floats.

    A guard against a corrupted or truncated capture (or a caller who forgot `-h`, or fed `-I`
    output through a merged stream), the same class of defensive exception
    `rad_engine.sensors.PointsFileEmptyError` is for its own module -- a bug this module can detect
    in its own contract. This module attaches no code to it; `run_electric` catches it and refuses
    with the register's `RTRACE_OUTPUT_MISMATCH` (Phase 11b), and so does `run_point_in_time`
    (R830, register N-74).
    """


def parse_irradiance(stdout: str) -> list[tuple[float, float, float]]:
    """`rtrace -I -h` stdout -> one `(R, G, B)` W/m^2 tuple per sensor point, in grid order.

    `-I` writes exactly three whitespace-separated floats per input point and nothing else once `-h`
    has suppressed the header -- this is the property check 3.7 asserts end to end ("every token of
    captured stdout parses as a float"). Splitting on all whitespace rather than by line tolerates
    `rtrace` wrapping long lines, which the header-suppressed format does not do in practice but
    which nothing here should depend on.

    Raises `RtraceOutputError` if the token count is not a multiple of three, or if any token is not
    a float -- both would otherwise silently misalign every point after the bad one, which is a
    wrong-but-plausible-looking illuminance grid, exactly the class of failure this project's other
    "prove it, do not assume it" modules (`bounding_cube`, `PointsFile`) refuse to let through.
    """
    tokens = stdout.split()
    if len(tokens) % 3 != 0:
        raise RtraceOutputError(
            f"rtrace -I stdout has {len(tokens)} numeric tokens, not a multiple of 3 -- expected "
            "one (R, G, B) irradiance triple per sensor point."
        )
    try:
        values = [float(tok) for tok in tokens]
    except ValueError as exc:
        raise RtraceOutputError(
            f"rtrace -I stdout contained a non-numeric token: {exc}. Every token of stdout must be "
            "float-parseable data -- a stray warning or header line would land here if stderr had "
            "merged into stdout or -h had been dropped."
        ) from exc
    return [(values[i], values[i + 1], values[i + 2]) for i in range(0, len(values), 3)]


#: The modifier `rtrace` prints for a ray that hits nothing (F1, measured 2026-09-21 on this
#: install). **Not Radiance's `void`** -- a miss prints `*`, with an intersection point of
#: `(0, 0, 0)`. Anything that derives a distance from a hit point must test this first, because
#: `(0, 0, 0)` is a finite coordinate and looks like a real answer.
ESCAPED_MODIFIER: Final[str] = "*"


def parse_rtrace_hits(stdout: str) -> list[tuple[Vec, str, Vec] | None]:
    """`rtrace -faa -ovpm` stdout -> one entry per ray, in input order.

    `-ovpm` prints, per ray: the radiance value (3 floats), the intersection point (3 floats) and
    the modifier name (1 token) -- `v`, `p` and `m` are all valid `rtrace -o` spec letters with
    exactly this meaning (verified against this machine's `rtrace` man page). With `-faa` that is
    7 tab-separated fields per line. A row with fewer than 7 fields, or whose first six fields do
    not all parse as floats, carries no usable intersection and is `None` -- this is how a
    truncated or otherwise unusable row is told apart from a real hit, without guessing at what a
    short row means.

    The third element is the **raw (R, G, B) radiance triple, not lux**: this module's purity
    ratchet (`tests/test_rtrace.py::TestPurity`) holds it to building argv and parsing output only,
    the same boundary the module docstring states -- `rad_core.photometry.to_lux` is one layer up,
    in whatever caller needs a photometric value (`rad_engine.glarendx`'s `sample_emitters`, as of
    this move). A view/sunlight caller that only needs the point and the modifier never touches this
    element at all.

    Pure extraction only: this function does not know which modifiers are emitters or which point
    belongs to which instance -- that is `assign_hits_to_emitters`'s job, so the two do not drift
    apart the way a single function that both parsed and grouped inline would (P7).
    """
    out: list[tuple[Vec, str, Vec] | None] = []
    for line in stdout.splitlines():
        parts = line.split("\t")
        if len(parts) < 7:
            out.append(None)
            continue
        try:
            r, g, b = float(parts[0]), float(parts[1]), float(parts[2])
            x, y, z = float(parts[3]), float(parts[4]), float(parts[5])
        except ValueError:
            out.append(None)
            continue
        modifier = parts[6].strip()
        out.append(((x, y, z), modifier, (r, g, b)))
    return out


def run_rtrace(
    env: RadianceEnv,
    request: RtraceRequest,
    *,
    points_text: str,
    cwd: Path | str,
    timeout: float | None = None,
) -> tuple[list[tuple[float, float, float]], Result]:
    """Run `rtrace -I` over `points_text` (a `.pts` file's own text, `rad_engine.sensors`'s
    contract) and parse its stdout.

    Uses `rad_engine.run.run` -- the one spawn path this project already built stderr/stdout
    separation into (module docstring: "stderr is never merged into stdout") -- rather than a second
    `subprocess` call, so this module inherits that guarantee instead of re-implementing it. `check`
    is left at `run`'s own default (`True`): an `rtrace` that exits non-zero produced no usable
    output, and `RadianceError` (raised by `run`) already carries the stderr that explains why,
    which is more informative than this module inventing its own wrapper around the same fact.

    Returns both the parsed values (grid order, matching `points_text`'s line order) and the raw
    `Result`, so a caller that also wants `result.warnings` for `rad_engine.advisories.classify`
    (the *un*-suppressed stderr this module's `-w` decision keeps populated) does not have to
    re-invoke `rtrace` to get it.
    """
    result = run(
        env, build_rtrace_argv(request), cwd=cwd, stdin=points_text, timeout=timeout
    )
    return parse_irradiance(result.stdout), result
