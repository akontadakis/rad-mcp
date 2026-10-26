"""Both quality-preset families, at the pure layer both `rad_engine.rtrace` and `rad_engine.rpict`
depend on.

**Why this module exists.** `rad_core` never imports `rad_engine` (this repo's whole-project
layering rule, verified by `grep -rnE "^\\s*(from|import)\\s+rad_engine" src/rad_core/*.py` exiting 1
with no output). `docs/plan.md` check 9.8 needs both preset families in one place a pure module can
read, and Phase 9 Tasks 3, 4, 5 and 6 all consume these tables from `rad_core`, not from either
engine module -- so the tables move down, not sideways.

**The ambient half is a pure move.** `QualityParameters`, `AMBIENT_PRESETS`
(`rad_engine.rtrace.QUALITY_PRESETS`'s new name here) and `ambient_interpolation_radius_m` are cut
from `rad_engine.rtrace` verbatim, docstrings and all, and `rtrace.py` re-exports the same objects
-- not copies -- so no existing caller changes and nothing can drift between two definitions.

**The DGP half is NOT a pure move (ruling R306).** `docs/plan.md` check 9.8 (the binding authority)
and `docs/tool-surface.md` §16 publish the rpict preset family as three names --
`fast`/`accurate`/`maximum` -- and owner ruling R299 (`docs/plan.md`'s `### Settled` table, the row
headed "Ruling R299 (owner)"; cited by heading and row name rather than by line number, because the
`docs/plan.md:4054` this sentence used to carry had drifted onto an unrelated row by Phase 9
Task 17) names `fast` as the
member `rad_engine.rpict.GLARE_QUALITY_PRESETS` deliberately omits for `run_glare`
(`docs/tool-surface.md` #13: "a low-quality ambient calculation produces a confident, plausible,
wrong DGP" -- a glare render is the one output this project will not let a caller ask for cheaply).
So `DGP_PRESETS` here holds the sourced three, transcribed from
`docs/specs/rpict-parameter-guide.md`'s "Quality presets" table (header line 21, eighteen flag rows
23-40, columns `Fast / preview`/`Accurate`/`Maximum`), and
`rad_engine.rpict.GLARE_QUALITY_PRESETS` becomes an explicit two-member subset built by name --
`{k: DGP_PRESETS[k] for k in ("accurate", "maximum")}` -- not an identity re-export the way the
ambient family is. `tests/test_rpict.py::TestThereIsNoFastQuality` (`sorted(GLARE_QUALITY_PRESETS)
== ["accurate", "maximum"]`) is unchanged by this move and stays green with no edit.

**`-sj` is not a real `rpict` flag (ruling R271).** The source table's 10th flag row (line 32,
"Specular jitter") is `-sj`; measured on this machine's Radiance 6.1a, `rpict -sj 0.7 -defaults`
exits 1 with `rpict: fatal - command line error at '-sj'` before a single ray is traced. `man
rpict`'s specular options are exactly `-ss` (specular sampling) and `-st` (specular threshold), and
`-ss`'s own description is the one about jitter ("a value of zero means that no jittering will take
place"). The table's values (`0.3` Fast, `0.7` Accurate, `1` Maximum) are carried through unchanged
onto `-ss`; only the flag name changes -- exactly the substitution `rad_engine.rpict` already
applies to its `accurate`/`maximum` columns, now applied identically to `fast`.

**Phase 9 Task 3 adds the explain/diagnose tables (checks 9.6, 9.7, 9.8).** `PARAMETERS` transcribes
the eighteen-row explain table at `docs/specs/rpict-parameter-guide.md:50-67` (columns `Flag | Name
| Family | Controls | Artifact -> fix | Render time impact (weight N/100)`), with the same `-sj` ->
`-ss` substitution as `DGP_PRESETS` above. `DIAGNOSES`/`diagnose()` implement the symptom -> fix
table sourced from `docs/specs/workflows-point-in-time-to-annual.md` sections 1.2 and 5.2.
`PRESET_FAMILY` maps all seven published preset names (`draft`/`standard`/`high`/`reference` from
`AMBIENT_PRESETS`, `fast`/`accurate`/`maximum` from `DGP_PRESETS`) to their family.

**Controller ruling R314: no `RAD_GRID`.** An earlier draft of this task's brief called for a
nine-cell `rad` DETAIL x VARIABILITY grid citing `radiance-engine-overview.md` section "rad
variables". That heading does not exist in this repository (`grep -rn "rad variables"
docs/specs/` exits 1, no output), `grep -rn "average ZONE dimension" docs/specs/` exits 1 with no
output, and `grep -rn "16d\\|32d\\|64d" docs/specs/` exits 1 with no output either. **That last
sentence used to say the grep "matches only unrelated `%04d` format strings in `dctimestep`
examples"; it matches nothing at all** -- `%04d` contains none of the three strings searched for --
and it is corrected here rather than erased (measured at Phase 9 Task 17, which found the same
claim, in different words, in `tests/test_quality.py`). It does not weaken the paragraph: a grep
that returns nothing supports "no source in this repository" at least as well as one that returns
unrelated hits. Under `docs/specs/` -- the corpus this claim is scoped to --
`grep -rn "VARIABILITY" docs/specs/` returns exactly two lines: two example `rad` variables
blocks at `radiance-engine-overview.md:495-496` and `:507-508`, giving integer `-ar` for two of
nine cells,
not the nine-cell scene-relative grid the brief described. (A third DETAIL/VARIABILITY cell exists
in this repository -- `tests/fixtures/scenes/test_room/scene.rif:4-5`, a test fixture's own `rad`
options file -- and sources no cell value either. Recorded at Task 16 fix round 1, because the
sentence this replaces said "the only real DETAIL/VARIABILITY text" without qualification.)
`docs/plan.md` check 9.8 (the binding
authority for this task) does not mention a grid at all; only `docs/tool-surface.md` #16
carried it, and that citation was itself the defect (corrected at Phase 9 Task 16, which now
records this measurement in #16 and keeps the superseded reading beside it). So `RAD_GRID` is not built
here: publishing even the two sourced cells would imply the other seven exist, which they do not.

**The `"shadows glow"` diagnosis recommends no changes.** The source material describing this
symptom uses `-aw` (ambient weighting), a flag that is not one of the sourced eighteen in
`PARAMETERS` -- no `ParameterSpec` row is invented for it. `Diagnosis` (unlike `ParameterSpec`) has
no `notes` field in this task's published interface, so the omission is recorded in `do_not`
instead: `changes` is empty and `do_not` states why, so `explain("-aw")` correctly returns nothing
and no caller is told to change a flag this tool cannot explain.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

__all__ = [
    "AMBIENT_PRESETS",
    "DGP_PRESETS",
    "DIAGNOSES",
    "PARAMETERS",
    "PRESET_FAMILY",
    "Diagnosis",
    "ParameterSpec",
    "QualityParameters",
    "ambient_interpolation_radius_m",
    "diagnose",
]


@dataclass(frozen=True)
class QualityParameters:
    """The five ambient flags a quality preset or override controls, plus the direct-calculation
    pair `DIRECT_EXACT_FORCED` sets. Mirrors the wire result's `parameters` shape
    (docs/tool-surface.md §10: ``{ab, ad, as, ar, aa, lw, dt, dc, dj, av: 0}``) minus the three
    fields (`lw`, `dj`) this tool's presets never touch and never derives a value for -- see
    `parameters_dict`.

    Field names avoid the bare Radiance flags `as`/`ar` because `as` is a Python keyword; `ambient_*`
    names are used throughout and mapped to Radiance's own `-ab/-ad/-as/-ar/-aa` spelling only where
    an argv or a wire dict is actually built.
    """

    ambient_bounces: int
    """`-ab`."""
    ambient_divisions: int
    """`-ad`."""
    ambient_super_samples: int
    """`-as`."""
    ambient_resolution: int
    """`-ar`."""
    ambient_accuracy: float
    """`-aa`. `0.0` (the `reference` preset) disables ambient interpolation entirely -- not a
    division artefact, see `ambient_interpolation_radius_m`."""
    direct_threshold: float | None = None
    """`-dt`, or `None` if nothing in this tool's contract has set it. `None` renders as no `-dt`
    flag at all rather than as some assumed rtrace default -- see the module-level note on why `-lw`
    and `-dj` are never populated here."""
    direct_certainty: float | None = None
    """`-dc`, or `None`. Set to `1.0` alongside `direct_threshold=0.0` by `DIRECT_EXACT_FORCED`."""
    ambient_value: float | None = None
    """`-av`, or `None` for no flag at all -- the same "`None` means no flag, not an assumed
    default" contract `direct_threshold`/`direct_certainty` already keep. `-av red grn blu` "sets
    the ambient value to a radiance of red grn blu ... used in place of an indirect light
    calculation" (docs/specs/radiance-practical-guide.md:1705); this project applies one scalar as
    a grey value (`red == grn == blu`), which is the shape `run_render`'s own `quality={"av": N}`
    override already publishes (a single number, matching every other overridable ambient flag).

    **Every preset in `AMBIENT_PRESETS` leaves this `None`.** `-av` is not part of the sourced
    ambient family at all -- engine invariant 5 states it is zero on every *quantitative* run
    (illuminance, daylight factor, glare, matrix work) and refused as a `quality` override
    everywhere else. `run_render(mode="hdr")` is the one call this project lets set it, as a
    presentation choice rather than a reported number (ruling R327, docs/tool-surface.md's engine
    invariants table) -- `rad_mcp.render_tool._apply_av_override` is the one place that ever
    constructs a `QualityParameters` with this field non-`None`, via `dataclasses.replace`.
    """


#: docs/tool-surface.md §10, sourced from
#: [workflows-point-in-time-to-annual.md §1.2](../../docs/specs/workflows-point-in-time-to-annual.md):
#: "draft -ab 2 -ad 256 -as 128 -ar 128 -aa 0.25; standard -ab 4 -ad 1024 -as 512 -ar 512 -aa 0.2;
#: high -ab 7 -ad 4096 -as 2048 -ar 1024 -aa 0.15; reference -ab 5 -ad 1024 -as 512 -ar 512 -aa 0.0
#: (no interpolation, "extremely slow", for ground truth)". Implemented literally: `reference` is
#: `-ab 5`, not "higher than `high`"'s `-ab 7` -- the source's own table (§1.2 row "Reference (No
#: Interpolation)") gives `-ab 5`, because the point of this preset is disabling interpolation via
#: `-aa 0.0`, not maximising every other ambient flag. Not "corrected" here even though it reads
#: like a typo next to `high`'s `-ab 7` -- see the module docstring's citation discipline.
AMBIENT_PRESETS: dict[str, QualityParameters] = {
    "draft": QualityParameters(
        ambient_bounces=2, ambient_divisions=256, ambient_super_samples=128,
        ambient_resolution=128, ambient_accuracy=0.25,
    ),
    "standard": QualityParameters(
        ambient_bounces=4, ambient_divisions=1024, ambient_super_samples=512,
        ambient_resolution=512, ambient_accuracy=0.2,
    ),
    "high": QualityParameters(
        ambient_bounces=7, ambient_divisions=4096, ambient_super_samples=2048,
        ambient_resolution=1024, ambient_accuracy=0.15,
    ),
    "reference": QualityParameters(
        ambient_bounces=5, ambient_divisions=1024, ambient_super_samples=512,
        ambient_resolution=512, ambient_accuracy=0.0,
    ),
}


def ambient_interpolation_radius_m(parameters: QualityParameters, max_size: float) -> float:
    """``Rmin = maxSize x aa / ar`` -- docs/tool-surface.md §10's
    `ambient_interpolation_radius_m`.

    **Source found and cross-checked.** [radiance-engine-overview.md]
    (../../docs/specs/radiance-engine-overview.md) line 439, "Formula for minimum sphere radius":
    ``R_min = (Scene_Size x -aa) / -ar``. This agrees with the formula stated in the task and in
    docs/tool-surface.md -- `maxSize` is that same `Scene_Size`, i.e. the octree's own bounding cube
    edge length (`rad_engine.octree.bounding_cube`'s `size`, `getinfo -d`'s fourth field) rather than
    any dimension of the sensor grid or the room polygon, since the ambient cache spans the whole
    compiled scene, not one grid. `docs/specs/rpict-parameter-guide.md` (the guide the task flagged
    as the *likely* source) describes `-ar`/`-aa` only qualitatively and states no formula at all, so
    it is not in tension with this one -- `radiance-engine-overview.md` is where the formula
    actually lives, and it matches the spec exactly. No disagreement to escalate.

    Divides by `ar`, never by `aa` -- `-aa 0.0` (the `reference` preset) makes the numerator, and
    therefore the whole radius, exactly `0.0`. That is the correct reading of "no interpolation":
    a zero-radius ambient sphere is one that cannot be reused for any other point, i.e. every point
    gets its own fresh calculation, which is what "disables ambient interpolation entirely" means.
    It is asserted explicitly in tests rather than left to fall out of the arithmetic, per the task's
    own instruction not to let this be mistaken for a division artefact.
    """
    return max_size * parameters.ambient_accuracy / parameters.ambient_resolution


#: `docs/specs/rpict-parameter-guide.md`, "Quality presets" table (header :21, rows :23-40).
#: The values follow Greg Ward's "rpict options" note in the Radiance documentation
#: (Radiance Software License; see LICENSES/Radiance-License.txt).
#: `"fast"` is the **Fast / preview** column, `"accurate"` is the **Accurate** column, `"maximum"`
#: is the **Maximum** column -- rulings R262 (which column) and R299 (which name for the
#: second), R306 (all three sourced names belong here, at `rad_core`; `rad_engine.rpict` publishes
#: only the two `run_glare` allows). See the module docstring for why the other two sourced
#: parameter sets in this repository (the ambient family's High-Fidelity row and the DGP script's
#: own config block, both in `workflows-point-in-time-to-annual.md`) are deliberately not used
#: here. Transcribed row by row, in the table's own row order; values are the strings `rpict`'s
#: argv carries, not floats, so a table entry like `512` or `0.15` is written exactly once and can
#: never format differently at two call sites.
#:
#: **One row's flag name is corrected**: the source table's 10th flag row (line 32 of the guide) is
#: `-sj` ("Specular jitter") -- measured, on this machine's Radiance 6.1a, not to exist as an
#: `rpict` option (`rpict -defaults` lists none; `rpict ... -sj 0.7 ...` exits 1 with
#: `fatal - command line error at '-sj'`, before a single ray is traced). `man rpict`'s specular
#: options are exactly `-ss` (specular sampling) and `-st` (specular threshold); `-ss`'s own
#: description is the one about jitter ("a value of zero means that no jittering will take
#: place"). The table's `0.3`/`0.7`/`1` values are kept, only written under `-ss` instead of `-sj`.
DGP_PRESETS: Final[dict[str, dict[str, str]]] = {
    "fast": {
        "-ps": "8", "-pt": "0.15", "-pj": "0.6", "-dj": "0", "-ds": "0.5", "-dt": "0.5",
        "-dc": "0.25", "-dr": "1", "-dp": "64", "-ss": "0.3", "-st": "0.85", "-ab": "2",
        "-aa": "0.2", "-ar": "32", "-ad": "64", "-as": "32", "-lr": "4", "-lw": "0.01",
    },
    "accurate": {
        "-ps": "4", "-pt": "0.05", "-pj": "0.9", "-dj": "0.7", "-ds": "0.15", "-dt": "0.05",
        "-dc": "0.5", "-dr": "3", "-dp": "512", "-ss": "0.7", "-st": "0.15", "-ab": "5",
        "-aa": "0.15", "-ar": "128", "-ad": "512", "-as": "256", "-lr": "8", "-lw": "0.002",
    },
    "maximum": {
        "-ps": "1", "-pt": "0", "-pj": "1", "-dj": "1", "-ds": "0.02", "-dt": "0",
        "-dc": "1", "-dr": "6", "-dp": "0", "-ss": "1", "-st": "0", "-ab": "8",
        "-aa": "0", "-ar": "0", "-ad": "4096", "-as": "1024", "-lr": "16", "-lw": "0",
    },
}


@dataclass(frozen=True)
class ParameterSpec:
    """One row of `rpict-parameter-guide.md`'s eighteen-row "Parameter detail" table
    (lines 50-67), columns `Flag | Name | Category | Meaning | Artifact it fixes |
    Render-time impact`. `family` carries the source's `Category` column value verbatim
    (`pixel`/`direct`/`specular`/`ambient`/`limit`)."""

    flag: str
    name: str
    family: str
    controls: str
    artifact_it_fixes: str
    render_time_weight: int
    interacts_with: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()


#: Transcribed verbatim from `docs/specs/rpict-parameter-guide.md:50-67`. **`-sj` is not a real
#: `rpict` flag (ruling R271)**: the source's 10th row (line 59, "Specular jitter") is carried
#: here under `-ss` instead, with its own row's `controls`/`artifact_it_fixes`/`render_time_weight`
#: values unchanged -- exactly the substitution `DGP_PRESETS` above already applies. `notes`
#: records the substitution so a reader of this dict alone (without the module docstring) still
#: sees it.
PARAMETERS: dict[str, ParameterSpec] = {
    "-ps": ParameterSpec(
        flag="-ps", name="Pixel sampling rate", family="pixel",
        controls="Adaptive sampling rate per pixel. Lower = more samples = finer detail.",
        artifact_it_fixes="Missing pieces on objects → decrease by 50%.",
        render_time_weight=90,
        notes=("Inverse: halving value can quadruple render time.",),
    ),
    "-pt": ParameterSpec(
        flag="-pt", name="Sampling threshold", family="pixel",
        controls="Threshold to skip sampling where neighbouring pixels are similar.",
        artifact_it_fixes="Soft edges look jagged → decrease by 50%.",
        render_time_weight=15,
        notes=("Minor — modest increase as value decreases.",),
    ),
    "-pj": ParameterSpec(
        flag="-pj", name="Anti-aliasing jitter", family="pixel",
        controls="Randomises pixel sample positions to reduce aliasing. No render time cost.",
        artifact_it_fixes="Staircase edges → raise to 0.9. Erratic edges → lower to 0.4.",
        render_time_weight=0,
        notes=("No effect on render time.",),
    ),
    "-dj": ParameterSpec(
        flag="-dj", name="Source jitter", family="direct",
        controls="Jitters source positions for soft shadows. Requires -ps 1 for best results.",
        artifact_it_fixes="Unnaturally sharp shadows → increase to 0.7.",
        render_time_weight=20,
        interacts_with=("-ps",),
        notes=("Indirect — requires reducing -ps, which can be expensive.",),
    ),
    "-ds": ParameterSpec(
        flag="-ds", name="Source substructuring", family="direct",
        controls="Divides large sources into sub-sources for more accurate shadow casting.",
        artifact_it_fixes="Large sources cast unnatural shadows → decrease by 50%.",
        render_time_weight=60,
        notes=("Inverse: halving value doubles render time.",),
    ),
    "-dt": ParameterSpec(
        flag="-dt", name="Direct thresholding", family="direct",
        controls=(
            "Skips direct illumination calculations below this threshold. Set to 0 for full "
            "accuracy."
        ),
        artifact_it_fixes="Shadows disappear → decrease by 50%.",
        render_time_weight=30,
        notes=("Inverse — halving increases time ~50%.",),
    ),
    "-dc": ParameterSpec(
        flag="-dc", name="Direct certainty", family="direct",
        controls="Fraction of sources fully tested vs estimated. Higher = more accurate.",
        artifact_it_fixes="Inaccurate values with many sources → increase to 0.75.",
        render_time_weight=40,
        notes=("Direct: up to 50% impact over 0–1 range.",),
    ),
    "-dr": ParameterSpec(
        flag="-dr", name="Direct relays", family="direct",
        controls="Number of specular relay bounces for mirror reflections.",
        artifact_it_fixes="Missing mirror reflections → increment.",
        render_time_weight=50,
        notes=("Each new reflection can double time.",),
    ),
    "-dp": ParameterSpec(
        flag="-dp", name="Direct pretest density", family="direct",
        controls="Sample density for pretest of direct sources.",
        artifact_it_fixes="Incorrect mirror reflections → double value.",
        render_time_weight=5,
        notes=("Minor — affects start-up time only.",),
    ),
    "-ss": ParameterSpec(
        flag="-ss", name="Specular jitter", family="specular",
        controls="Jitter for specular reflections. No render time cost.",
        artifact_it_fixes="Too fuzzy → decrease to 0.2. Too sharp → increase to 1.",
        render_time_weight=0,
        notes=(
            "The source table names this row '-sj' (rpict-parameter-guide.md:59). Ruling R271: "
            "`rpict -sj 0.7 -defaults` exits 1 with 'rpict: fatal - command line error at -sj' on "
            "this build, while `-ss` is accepted and `rpict -defaults` prints it as 'specular "
            "sampling'. The row's values are carried here unchanged; only the flag name is "
            "corrected.",
        ),
    ),
    "-st": ParameterSpec(
        flag="-st", name="Specular threshold", family="specular",
        controls="Threshold below which specular contributions are ignored.",
        artifact_it_fixes="Some specular highlights missing → decrease to 0.02.",
        render_time_weight=10,
        notes=("Minor — zero may increase time 50% over max.",),
    ),
    "-ab": ParameterSpec(
        flag="-ab", name="Ambient bounces", family="ambient",
        controls=(
            "Number of indirect light bounces. Critical for DGP — determines how much "
            "inter-reflected light is captured."
        ),
        artifact_it_fixes="Lighting in shadows too flat → increment.",
        render_time_weight=85,
        notes=("Direct: doubling this value can double render time.",),
    ),
    "-aa": ParameterSpec(
        flag="-aa", name="Ambient accuracy", family="ambient",
        controls=(
            "Controls density of ambient cache samples. Lower = more accurate, smaller cache "
            "cells."
        ),
        artifact_it_fixes="Uneven shading boundaries in shadows → decrease by 25%.",
        render_time_weight=75,
        interacts_with=("-ar",),
        notes=("Direct: doubling quadruples render time.",),
    ),
    "-ar": ParameterSpec(
        flag="-ar", name="Ambient resolution", family="ambient",
        controls="Number of ambient cache cells per scene unit. Higher = finer ambient mesh.",
        artifact_it_fixes="Shading wrong in some areas → double or quadruple.",
        render_time_weight=70,
        interacts_with=("-aa",),
        notes=("Direct: can quadruple time for double value.",),
    ),
    "-ad": ParameterSpec(
        flag="-ad", name="Ambient divisions", family="ambient",
        controls="Hemisphere samples per ambient calculation. Eliminates splotches.",
        artifact_it_fixes="Splotches of light → double value.",
        render_time_weight=80,
        interacts_with=("-as",),
        notes=("Direct: doubling may double render time.",),
    ),
    "-as": ParameterSpec(
        flag="-as", name="Ambient super-samples", family="ambient",
        controls="Additional samples in highly variable areas. Acts as a complement to -ad.",
        artifact_it_fixes="Splotches of light → increase to half of -ad.",
        render_time_weight=65,
        interacts_with=("-ad",),
        notes=("Direct: effectively adds to -ad cost.",),
    ),
    "-lr": ParameterSpec(
        flag="-lr", name="Limit reflection", family="limit",
        controls="Maximum ray reflection count. Prevents infinite recursion.",
        artifact_it_fixes="Some specular reflections gone → increment.",
        render_time_weight=8,
        notes=("Minor — very slight increase.",),
    ),
    "-lw": ParameterSpec(
        flag="-lw", name="Limit weight", family="limit",
        controls="Minimum ray contribution to continue tracing. Smaller = trace weaker rays.",
        artifact_it_fixes="Specular reflections gone → decrease by 50%.",
        render_time_weight=8,
        notes=("Minor — very slight increase.",),
    ),
}


@dataclass(frozen=True)
class Diagnosis:
    """One row of the symptom -> fix table, sourced from
    `docs/specs/workflows-point-in-time-to-annual.md` sections 1.2 and 5.2. `do_not` is a field
    rather than prose folded into `cause` because the two symptoms users get backwards
    (`splotches`, `scene too dark`) are both negative advice, and a recommendation buried in a
    paragraph is a recommendation that gets skimmed past."""

    symptom: str
    cause: str
    changes: dict[str, float | int]
    do_not: tuple[str, ...]
    source: str


#: Keyed by a normalised symptom string (`_normalise_symptom`: lower-cased, whitespace-collapsed).
#: **Check 9.6** binds `"splotches"`: it must double `-ad`, raise `-as` to half of `-ad`, and
#: never suggest lowering `-aa` -- source: `workflows-point-in-time-to-annual.md` section 1.2,
#: *"Attempting to fix blotchy artifacts (an -ad problem) by lowering -aa is incorrect and will
#: drastically increase computation time."* (also section 5.2's troubleshooting-table row).
#: **Check 9.7** binds `"missing direct sun"` to exactly `{-dj: 0, -dt: 0, -dc: 1}` -- source:
#: section 5.2, *"Set -dj 0 to force shadow rays to the center of the solar disc. Also ensure
#: -dt 0 and -dc 1."*
#:
#: **`"shadows glow"` recommends no changes.** The source material for this symptom names `-aw`
#: (ambient weighting), which is not one of the sourced eighteen flags in `PARAMETERS` -- no
#: `ParameterSpec` row is invented for it (Task 3 correction). `changes` is therefore empty and
#: `do_not` records why, rather than publishing a change to a flag `explain()` cannot describe.
DIAGNOSES: dict[str, Diagnosis] = {
    "splotches": Diagnosis(
        symptom="Splotches or blotchy patches",
        cause="Too few hemisphere samples per ambient calculation. This is an -ad problem.",
        changes={"-ad": 1024, "-as": 512},
        do_not=(
            "Do not lower -aa to fix this. -aa controls interpolation error, not sampling "
            "noise: 'Attempting to fix blotchy artifacts (an -ad problem) by lowering -aa is "
            "incorrect and will drastically increase computation time.'",
        ),
        source="docs/specs/workflows-point-in-time-to-annual.md section 1.2",
    ),
    "missing direct sun": Diagnosis(
        symptom="Annual simulation missing direct sun",
        cause="Jittered shadow rays are missing the solar disc.",
        changes={"-dj": 0, "-dt": 0, "-dc": 1},
        do_not=(),
        source="docs/specs/workflows-point-in-time-to-annual.md section 5.2",
    ),
    "scene too dark": Diagnosis(
        symptom="Scene too dark, especially far from windows",
        cause=(
            "The -ab value is too low for the scene's complexity, or the -lw (limit weight) "
            "value is too high, causing rays to be terminated before they can bounce deep into "
            "the space."
        ),
        changes={"-ab": 5, "-lw": 1e-5},
        do_not=(
            "Do not raise -ab without lowering -lw. Raising -ab alone is inert -- the extra "
            "bounces are terminated by the weight limit before they arrive.",
        ),
        source="docs/specs/workflows-point-in-time-to-annual.md section 5.2",
    ),
    "shadows glow": Diagnosis(
        symptom="Shadows appear to glow",
        cause=(
            "The ambient value is being modified by computed indirect values through a moving "
            "average."
        ),
        changes={},
        do_not=(
            "No change is recommended: the flag this symptom's fix would use is -aw (ambient "
            "weighting), which has no ParameterSpec row in PARAMETERS -- it is not one of the "
            "sourced eighteen flags in docs/specs/rpict-parameter-guide.md:50-67, so it is not "
            "invented here.",
        ),
        source="docs/specs/rpict-parameter-guide.md",
    ),
    "no glare sources found": Diagnosis(
        symptom="evalglare finds no glare sources on an obviously bright window",
        cause=(
            "Not a rendering fault. The glare source detection threshold is too high for a "
            "bright scene."
        ),
        changes={},
        do_not=(
            "Do not raise the render quality to chase this. The parameter set is not the "
            "cause.",
        ),
        source="docs/specs/workflows-point-in-time-to-annual.md section 5.2",
    ),
}


def _normalise_symptom(symptom: str) -> str:
    """Lower-cases and collapses all whitespace runs (including leading/trailing) to single
    spaces, so `"  Splotches  "`, `"splotches"` and `"SPLOTCHES"` all key the same row."""

    return " ".join(symptom.split()).lower()


def diagnose(symptom: str) -> Diagnosis | None:
    """Look up `symptom` in `DIAGNOSES` after normalising case and whitespace. Returns `None` on
    an unknown symptom rather than guessing at the nearest one -- a wrong diagnosis costs more
    than no diagnosis."""

    return DIAGNOSES.get(_normalise_symptom(symptom))


#: Every published preset name (`AMBIENT_PRESETS` and `DGP_PRESETS`) mapped to its family --
#: `"ambient"` or `"rpict"`. Seven names, not six: four ambient (`draft`/`standard`/`high`/
#: `reference`) plus three DGP (`fast`/`accurate`/`maximum`, ruling R306). Built from the two
#: preset dicts themselves rather than a literal list, so it cannot drift from them.
PRESET_FAMILY: dict[str, str] = {
    **{name: "ambient" for name in AMBIENT_PRESETS},
    **{name: "rpict" for name in DGP_PRESETS},
}
