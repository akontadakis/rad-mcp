"""Regenerate Radiance command lines from the project manifest, and export them as a
self-contained script.

Phase 9 Task 12 (docs/plan.md, "Phase 9 — rendering, quality, export").
docs/tool-surface.md §17 (`export`): *"the exact command sequence that produced a result ... a
script that merges streams would corrupt the artifact it writes"* (the closing clause is §17's own
citation to CONTEXT.md), docs/plan.md checks 9.9 and 9.10. **The first half used to read "the
generated script that produced a result", which is not what §17 says** -- corrected by the
whole-branch fix wave (R373). Half an elided quotation had been rewritten in this module's own
words while the ellipsis made the whole thing read as continuous document text.

**This module owns the argv. It never reads a command string a job recorded verbatim** -- the
script generator regenerates argv from `kind` + `parameters` + `built_from`, so an exported script
reproduces the *method*, not a bug the original invocation happened to have. `frozen_octree_digest`
is the one piece of this contract check 9.10 (Task 14) can verify end to end: `oconv -f` freezes the
octree's body against the input *bytes*, not the input *path*, which is what lets a script built
here, run somewhere else entirely, produce an octree whose header-stripped body matches the
original's (measured table below). What else a script reproduces exactly, and what only within a
tolerance, is R732's (the "Four kinds" section below).

## `job_id` names an artifact, not a `rad_engine.jobs.JobRecord` -- a finding, not an assumption

The brief's signatures (`script_for_job(manifest, job_id, ...)`, and
`rad_core.report.assemble(manifest, job_id)`) both take only a `Manifest` and a `job_id` string.
**`rad_core.manifest.Manifest` has no concept of a job at all** -- verified by reading the module: it
has `Project`, `InputRecord` and `Artifact`, nothing else, and job records (`rad_engine.jobs.
JobRecord`, `10_jobs/<job_id>/job.json`) are a wholly separate, non-pure store this module may not
touch (`rad_core.report` is pure; even here, reading job.json would mean regenerating a script from
what the job *did*, which is exactly the "recorded verbatim" failure this module exists to avoid).
The only id a bare `Manifest` can resolve at all is an `Artifact.id`, so **`job_id`, at this layer,
names the target `Artifact`** whose build chain is being exported -- `manifest.artifact(job_id)`,
raising `KeyError` exactly as that lookup does when the id is unknown. Task 13's `export_tool`,
which does have access to `rad_engine.jobs`, is where a caller-facing `job_id` (a `JobRecord`) would
be translated into the artifact id(s) that job produced, before calling into this module.

**This had a real consequence for check 9.10, and Phase 9 Task 14 has since closed one half of it
and found the other half to be a different, deeper gap.** The "Phase 3 job" check 9.10 names is
`run_point_in_time`. **Ruling R348 (Task 14, fix round 1) widened it to register the octree it
builds** -- `_register_point_in_time_artifacts` (`src/rad_mcp/point_in_time_tool.py`, renamed
from `_register_point_in_time_octree` when R730 added the sky and the result) calls
`register_artifact` after every real call, so a point-in-time run's manifest genuinely carries an
`octree:point_in_time` artifact this module's own functions can resolve by id. That closes what
this section used to describe as an open decision for "whoever owns that file next" -- it has an
owner now, and the decision is made.

**What R348 did not close, and what has closed it since.** `run_point_in_time` still never
calls `runtime.store.submit()` -- every call runs inline, with no `JobRecord` ever created for it
(see that tool's own "KNOWN LIMITATION" docstring). `rad_mcp.export_tool`'s `_job_target` resolves
a caller's wire-level `job_id` (a `JobRecord` id, a different `job_id` than the artifact id this
module's own functions take) by calling `runtime.store.status(job_id)` *before* it ever reads a
manifest artifact -- so a point-in-time run's wire-level `job_id` is unresolvable through that path
regardless of what the manifest now records: the failure sits at the job-store layer this pure
module has no concept of, one step before the artifact-registration layer R348 fixed. Phase 9's
Outcome in docs/plan.md (check 9.10's UNMET bullet) records the measurement, and it is why check
9.10 shipped unmet in Phase 9. Two further gaps that report named sat
in this module -- no `rtrace` step, and an octree without the sky -- and **R731 closes both**: R730
registers the run's sky (`sky:point_in_time`) and result (`point_in_time:<grid>`), and this module
now reconstructs the whole chain, sky to `rcalc`. The job-store gap is not this module's and is
unchanged here; an `artifact_id` route into `export` (R729), which hands this module the artifact
id directly, is what goes around it.

## Four kinds are reconstructed; `vmx` is a documented gap, not a guarantee

`_STEP_BUILDERS` is the one authority on which kinds are reconstructed; every step's argv leads with
the bare binary name.

`"octree"` is reconstructed fully and is the one this module's own tests run against real
`oconv`: `built_from`'s file-backed edges (`InputRecord.files`), in order, become `oconv -f`'s
positional arguments -- a built artifact (the point-in-time sky) by its output name; the synthetic
`project:*` scalar edges (`SCALAR_INPUT_PREFIX`) are skipped -- they carry no file, and their effect
(e.g. ground reflectance) is already baked into whichever file encodes the sky. **A luminaire support
or type edge is skipped too (N14, R731):** a lit octree names the project's `source.cal` record and
each placed type (`.ies`, `.rad`, `.dat`) so that a change to them invalidates it, but `oconv` reads
only the placements, whose `!xform` pulls the type's `.rad` in; `source.cal` and the `.dat` are read
at trace time. Handed to `oconv`, a `.ies`, a `.dat` or a `.cal` is read as a scene description,
which none of them is. **A `run_annual` octree is rebuilt from what its build compiled (R849):**
its `built_from` names no materials (R361, kept at R385), and its build compiled files no edge
names (per-call glow files, the sky dome, the suns scene, a blackened shell), so each such octree
records them (`COMPILED_PARAMETER`) and `_compiled_octree_step` names them in build order. One sealed
without that record gets the project's materials files ahead of `built_from`'s (R833), and
`exported_octree_differences` says its octree can differ from the run's.

`"sky"` (R731) is the recorded generator argv (`gensky` or `gendaylit`), then, when the project
declares a north, the recorded `xform` reading the generator's file on stdin; then the glow and
ground text appended by `printf`
-- `rad_engine.sky.sky_description_text("")`, the function that wrote the
tool's own sky. **What it reproduces (R732, measured as M3):** the tool's text byte for byte with no
north; with one, every line but `xform`'s own comment, which records its `argv[0]` -- an absolute
path when the tool spawns it, a bare name under a script. `oconv` drops comments, so the octree body
(`frozen_octree_digest`) matches either way.

`"point_in_time"` (R731) is `rtrace` with `rad_engine.rtrace.build_rtrace_argv`'s own flags, rebuilt
from the recorded `parameters` rather than re-spelled here, over the one octree edge, reading the one
sensor grid's `.pts` on stdin, then `rcalc` reading `rtrace`'s file with `rad_core.photometry`'s
own constants (the
illuminance conversion, or the daylight factor's division by `REFERENCE_ILLUMINANCE_LUX`). **What it
reproduces (R732, M2 and M4):** `rtrace` samples uncorrelated by default (`-u+`), so above `-ab 0`
the values agree with the tool's within check 3.1's tolerances, not exactly; a lamp-only `-ab 0` run
agrees to `rcalc`'s printed precision.

**No step pipes (Ruling 10, R731).** Under `set -e` a pipe's status is its last command's
(`sh -c 'set -e; false | true; echo $?'` prints 0), so a failing `gensky` or `rtrace` in
`gensky | xform` or `rtrace | rcalc` would publish an empty sky or result with exit 0. Each command
writes its own file beside the script (`_INTERMEDIATE_SUFFIXES`: `<output>.gen`, `<output>.rgb`) and
the next reads it on stdin, so every failing command stops the script
(`tests/test_export.py::TestAFailingStepFailsTheScript`).

**`bat` refuses `sky` and `point_in_time` (`_SH_ONLY_KINDS`, R731).** Their steps chain one
command's file into the next on stdin, read a points file on stdin and append text; the `sh` script is the one this server
can run and test (`tests/test_export.py::TestTheScriptRunsFromACopy`), and there is no `cmd.exe`
here to run a translation. `octree` and `vmx` keep their `bat` script. The `octree`/`vmx` `.bat`
(R739) switches delayed expansion off, double-quotes any token holding a character outside
`_BAT_BARE`, doubles `%` in every path and the backslashes that end a quoted token, and refuses a
path holding `"` or a control character. **No `.bat` has ever been run here**: this machine has no
`cmd.exe`, `wine` or `pwsh`, so these rules are checked as text only; `%%` as a literal percent is
not stated on any Microsoft page found (unverified).

`"vmx"` is reconstructed generically, from `Artifact.parameters` read as a flag set plus one
resolved octree positional -- and this is where the manifest, **as `rad_engine.chain` populates it
today**, falls short of what the brief assumed. Read directly (`src/rad_engine/chain.py`, the V-matrix
step): the sealed `parameters` dict is `{**parameters_dict(quality), "lw": ..., "klems_bins": ...,
"n_sensors": ...}` -- the ambient family plus three bookkeeping numbers. It carries **none** of
`-I+`, `-h`, `-f klems_full.cal`, `-b <kbin(...)>`, `-bn Nkbins` or `-m <glow modifier>`, all of
which `rad_engine.matrices.build_v_argv` hard-codes as non-negotiable rather than accepting from
`parameters` -- and the points file `-I+` reads on stdin is not an edge or a parameter at all. **A
real `chain.py`-sealed `vmx` artifact cannot be reconstructed byte-faithfully by this module today.**
That is a genuine finding about what the manifest captures, the exact shape the plan's own Phase 9
"risk that would make me re-plan" paragraph anticipated for check 9.10 -- reported here rather than
silently patched by reading `chain.py` (not in this task's Files) or by inventing a plausible-looking
`-b`/`-m` this module cannot verify (the R68 trap: "no error, no warning, a plausible optical
description of something that is not your blind").

So why does this module support `"vmx"` at all? Because `commands_for_job`'s and `script_for_job`'s
own contract -- regenerate from `kind` + `parameters` + `built_from`, nothing read verbatim -- is
correct *relative to what a manifest carries*, and R317(c) requires a fixture whose script genuinely
contains a resolved `kbin(...)`, not a vacuous one. `tests/conftest.py`'s `completed_job` fixture is
therefore a **hand-built, complete** manifest (every flag `build_v_argv` would emit, present in
`parameters`) -- proof of this module's own mechanism, not a claim that today's `chain.py` output
would round-trip. Closing that gap means widening what `chain.py` records, which is a different
task's file.

Every other kind (`dmx`, `tmx`, `smx`, `dsmx`, `vdmx`, `ddmx`, `cdsmx`, `asemx`, `smxd`, `smxsun`,
`sched`, `illblend`, `ill`, `hdr`, `pts`, `amb`) raises `UnsupportedArtifactKindError` rather than
guess: several of them are not even a single binary invocation (`sched`/`illblend` are Python-side
recomputation over existing matrices; the D matrix is a `genklemsamp | rcontrib` pipe, not one
command).

## The working-directory rule (R731)

A script changes into the project folder -- its first argument, or the folder the project was
exported from -- reads every input inside the project by a project-relative path and every input
outside it by its absolute path (`_input_path`), and writes its own outputs and `.err` files beside
itself (`OUT_DIR`). **Measured (M2):** a placement file is `!xform ... 12_luminaires/types/<t>.rad`
and a type file names its `.dat` and `source.cal`, all relative to the working directory, and
`oconv` exits 0 when an `!xform` fails -- a lamp-only script run from a bare directory read 0, 0, 0
with a zero exit. Run with the project copied elsewhere and the original moved away, the same script
reproduced the octree body and the values. `.` stays first on `RAYPATH`, so the project's own
`source.cal` is the one read. An input registered outside the project (the office fixture's
geometry is) is read where it was registered, which only exists on the exporting machine
(`EXPORT_PATHS_ABSOLUTE`).

## The measurement `frozen_octree_digest` and check 9.9 depend on

Measured on this machine (reproduce before trusting the numbers) with `oconv -f` over
`tests/fixtures/scenes/office/officeA_base.rad`, header-stripped via `getinfo -`:

| Command | repo path | copied path |
|---|---|---|
| `oconv f.rad` (no `-f`) | `e0f76caad1243807cfad58222ad6fd95` | `a5abe625507f634e3acd29193f1b5cbf` |
| `oconv -f f.rad` | `6b38ba3bbb586fffb77760f70d7866a4` | `6b38ba3bbb586fffb77760f70d7866a4` |

Without `-f` the octree stores file *references*, so its body moves with the input path string;
`-f` freezes the body against the bytes. This is why `script_for_job` always emits `oconv -f`, never
plain `oconv` -- check 9.9's assertion, and the property check 9.10 (Task 14) depends on.

## Streams never merge (check 9.9)

Every command the rendered script emits redirects its own stderr to a private file
(`"<output>.err"`, or one per command of a chained step, `_ERR_SUFFIXES`) -- never `2>&1`. Radiance
writes its data to stdout and its diagnostics to stderr; a merged stream puts warning text inside
the very octree or matrix the command is building (CONTEXT.md). `RAYPATH` is computed **inside the
generated script**, from wherever `oconv` is found on the machine that runs it -- and by the same
lib-candidate rule `rad_engine.env.discover` applies, not a narrower one (finding `oconv` itself,
the `.bat` looks for `oconv.exe` only, R745): it tries `_LIB_CANDIDATES` (`../lib`,
`../share/radiance/lib`, `../lib/radiance`, `discover`'s own three, in the same order) relative to
the resolved `bin` directory, accepts the first that actually contains `rayinit.cal`, and omits the
lib entry from `RAYPATH` entirely if none match -- `discover`'s own `lib_dir = None` case, which
`RadianceEnv.environ()` then leaves out rather than guessing. **Fix round 1, ruling R343:** this
section previously claimed the rule was `.` then `bin_dir/../lib` unconditionally -- true on this
machine, whose install happens to use that one candidate, and false in general: `discover` never
trusts a candidate that merely exists, and a hard-coded first guess with no existence check is not
"the same rule", it is the special case that made the gap invisible here. `RAYPATH` is never a value
baked in at export time, and never `$RAYPATH`/`${RAYPATH}` (an inherited value is exactly the
failure mode check 9.10's risk paragraph names).
"""

from __future__ import annotations

import hashlib
import math
import shlex
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from rad_core.manifest import SCALAR_INPUT_PREFIX, Artifact, Manifest
from rad_core.photometry import (
    CIE_PHOTOPIC_WEIGHTS,
    LUMINOUS_EFFICACY_LM_PER_W,
    REFERENCE_ILLUMINANCE_LUX,
)
from rad_engine.octree import (
    BLACK_MODIFIER,
    SceneInlineCommandError,
    dash_safe_operand,
    refuse_inline_commands_in_text,
    scene_include_reach,
)
from rad_engine.rtrace import QualityParameters, RtraceRequest, build_rtrace_argv
from rad_engine.safe_io import NotARegularFileError, handle_digest, open_regular_file
from rad_engine.sky import GROUND_GLOW_RGB, sky_description_text

__all__ = [
    "COMPILED_PARAMETER",
    "SCENE_INCLUDES_PARAMETER",
    "SCENE_INCLUDE_LINKS_PARAMETER",
    "SCENE_INCLUDES_DROPPED_PARAMETER",
    "SCENE_INCLUDE_FAILED_PARAMETER",
    "BatPathUnquotableError",
    "UnsupportedArtifactKindError",
    "commands_for_job",
    "compiled_scene_record",
    "current_file_digest",
    "exported_octree_differences",
    "frozen_octree_digest",
    "script_for_job",
]


class UnsupportedArtifactKindError(ValueError):
    """`commands_for_job`/`script_for_job` cannot reconstruct this artifact's command line. Either its
    `kind` is not in `_STEP_BUILDERS` at all; or its `built_from` or `parameters` lack something that
    kind's shape requires (the single octree edge, a required flag, a recorded generator); or a
    recorded value is not the type its use takes (R737); or the script asked for is `bat` and the
    chain holds a kind only the `sh` script reconstructs (R731).

    Raised rather than emitting a plausible-looking argv this module has not verified. See the module
    docstring's "Four kinds are reconstructed" section for why guessing is the wrong failure mode
    here.
    """


class BatPathUnquotableError(ValueError):
    """A path holds `"` or a control character (U+0000–U+001F, U+007F), which this module does not
    write into a `.bat` (R739). A line break ends a `cmd` line, and no Microsoft page found says how
    `cmd` carries a `"` or another control character inside a quoted token. Refused at export as
    `ARGUMENT_OUT_OF_RANGE`, `field="shell"`; the `sh` script carries it."""


#: Local output filename extension per kind, for the script-relative names `_local_name` mints.
_KIND_EXTENSION: dict[str, str] = {
    "octree": ".oct",
    "vmx": ".mtx",
    "sky": ".rad",
    "point_in_time": ".txt",
}

#: R731: the kinds only the `sh` script reconstructs. Their steps chain one command's file into the
#: next on stdin, read a points file on stdin and append the glow text with `printf`; `sh` is the script this server can
#: run and test (`tests/test_export.py::TestTheScriptRunsFromACopy`), and there is no `cmd.exe` here
#: to run a batch equivalent (`TestRaypathBatMirrorsSh`'s own note), so `bat` refuses rather than
#: emitting an untested translation.
_SH_ONLY_KINDS = frozenset({"sky", "point_in_time"})

#: R731: input kinds that name no scene file for `oconv` (N14). A lit octree's `built_from` names the
#: luminaire support record (the project's `source.cal`) and each placed type (`.ies`, `.rad`,
#: `.dat`) for invalidation, but `oconv` reads only the placements: a placement's `!xform` pulls its
#: type's `.rad` in, and `source.cal`/the `.dat` are read at trace time through `RAYPATH` and the
#: working directory; none of them is a scene description.
#: R870: an automated group's hourly schedule (`automated_schedule`) is a CSV of `open`/`closed`
#: rows, read by the blind schedule and never by `oconv`, so it is not a scene file either.
_NOT_SCENE_INPUT_KINDS = frozenset({"luminaire_support", "luminaire_type", "automated_schedule"})

#: Per command of a chained step, the suffix its private stderr file gets after the step's output
#: name; a step of any other kind is one command whose stderr is `<output>.err`. Every command keeps
#: its own stderr (check 9.9): nothing is merged, nothing shared between commands.
_ERR_SUFFIXES: dict[str, tuple[str, ...]] = {
    "sky": (".gen.err", ".rot.err"),
    "point_in_time": (".rtrace.err", ".err"),
}

#: Per command of a chained step but the last, the suffix of the file it writes after the step's
#: output name -- the file the next command reads on stdin (Ruling 10: no pipes, so `set -e` sees
#: every command's status). The last command writes the step's output itself; a sky with no north
#: has one command, which writes `<output>` directly.
_INTERMEDIATE_SUFFIXES: dict[str, tuple[str, ...]] = {
    "sky": (".gen",),
    "point_in_time": (".rgb",),
}

#: The sky generators a recorded `sky` artifact may name, and the one rotation binary: the binaries
#: `rad_engine.sky`'s own argv builders spawn (`build_gensky_argv`, `build_gendaylit_argv`,
#: `north_rotation_xform_argv`). A hand-edited manifest naming anything else is refused, not written
#: into a script a user will run.
_SKY_GENERATORS = frozenset({"gensky", "gendaylit"})
_SKY_ROTATION = "xform"

#: `sky` parameters a reconstruction needs (Task 2's `sky:point_in_time` record, R730).
_SKY_REQUIRED_KEYS = frozenset(
    {"generator_argv", "rotation_argv", "ground_reflectance", "ground_glow"}
)

#: `point_in_time` parameters a reconstruction needs: the quantity, and the five ambient flags
#: `build_rtrace_argv` always emits. `dt`/`dc` are optional: recorded only when `rtrace` got them.
_POINT_IN_TIME_REQUIRED_KEYS = frozenset({"quantity", "ab", "ad", "as", "ar", "aa"})

#: Keys `Artifact.parameters` carries that are bookkeeping, not a Radiance flag -- sourced directly
#: from `src/rad_engine/chain.py`'s own `ArtifactSpec(parameters=...)` call sites (grepped, not
#: guessed): `"method"` (`rad_mcp.results_tool`'s chain-selection convention, `_recorded_chain`
#: and its neighbours), `"klems_bins"` and `"n_sensors"` (the V-matrix step, `chain._v_step`).
#: Emitting any of these as `-klems_bins 145` would be a fabricated flag no Radiance binary accepts.
#: `"n"` (R781): the process count `chain.py`'s `_processes_parameter` records on every matrix it
#: built with `rcontrib`/`rfluxmtx`. It is a real flag, but this machine's core count, so an
#: exported `vmx` step carries no `-n`, as `_point_in_time_step`'s rebuilt `rtrace` carries none
#: (it builds `RtraceRequest` without `processes`, R780). `"grid_behind_aperture"` (R846): the
#: facing `chain._v_step` records on a V matrix, a fact about the geometry and no flag at all.
_NON_FLAG_PARAMETER_KEYS = frozenset(
    {"method", "klems_bins", "n_sensors", "n", "grid_behind_aperture"})

#: The keys required for a kind, per `_assert_required_keys_present` below -- this dict IS the
#: authority on what counts as "complete" for a kind, so nothing here restates its own values as
#: prose that could then drift from it (fix round 2: a prior version of this comment enumerated
#: `vmx`'s five keys and called the enumeration complete, which was itself untrue -- `-lw` is also
#: unconditional in `rad_engine.matrices.build_v_argv`'s own `-lw` -- so the fix is to stop
#: restating the set rather than to correct the restatement).
#:
#: Sourced from `build_v_argv`'s own hard-coded, non-negotiable flags (`matrices.build_v_argv`),
#: **excluding `-lw`**: a real `chain.py`-sealed `vmx` artifact's `parameters` always carries an
#: `lw` key regardless -- `rad_engine.rtrace.parameters_dict` includes it unconditionally
#: (its `"lw": None`), and `chain._v_step` separately overrides its *value* to the
#: real limit weight without touching whether the key exists. So requiring `lw` here would guard
#: against a gap that does not exist on the one production shape this dict exists to catch.
#: `-h` is excluded for a different reason -- it is conditional on `build_v_argv`'s own `header=`
#: argument (present for a matrix `dctimestep` reads from a file, absent for a caller parsing
#: stdout), so its absence is not evidence of anything truncated.
#:
#: **Fix round 1, ruling R344.** Before this dict existed, `commands_for_job` validated only
#: "exactly one octree edge" and then emitted whatever keys happened to be in `parameters` --
#: against a real `chain.py`-sealed `vmx` artifact (which carries none of these; see
#: `_NON_FLAG_PARAMETER_KEYS` above and ruling R345) that produced a silently truncated
#: `rcontrib ... <octree>` with no modifier, no basis file and no cal file, disagreeing with this
#: module's own stated philosophy of refusing rather than guessing.
_REQUIRED_KEYS_FOR_KIND: dict[str, frozenset[str]] = {
    "vmx": frozenset({"I+", "f", "b", "bn", "m"}),
}

#: Relative candidates `rad_engine.env.discover` probes for the Radiance `lib` directory, in the
#: exact order its loop tries them -- the first one containing `rayinit.cal` wins, and none
#: matching means `RAYPATH` carries no lib entry at all (`RadianceEnv.environ()`'s own behaviour
#: when `lib_dir is None`). The generated script re-derives this at runtime rather than baking in a
#: value measured on the machine that exported it, so a script built here still resolves correctly
#: wherever the install layout differs (fix round 1, ruling R343).
_LIB_CANDIDATES: tuple[str, ...] = ("../lib", "../share/radiance/lib", "../lib/radiance")


def frozen_octree_digest(path: Path) -> str:
    """md5 of the octree's body, with the Radiance header stripped.

    **Never digest a whole Radiance output file on this project.** Measured at 41ca7b3 on this
    machine: two `oconv -f` runs of the same scene from different directories give different
    whole-file md5s because the header records the invoking path, while their header-stripped
    bodies are byte-identical.

    The header ends at the first empty line, the same convention `getinfo` uses -- verified directly
    against the binary rather than trusted (see this task's report for the reproduction).

    **Not `path.read_bytes()`.** Every filesystem open in `rad_engine` goes through
    `rad_engine.safe_io.open_regular_file` (R48's primitive, `tests/test_protocol.py::
    TestCheck09SafeIo`'s ratchet) -- a FIFO planted at `path` would otherwise block this call
    forever with no timeout armed anywhere above it.
    """
    with open_regular_file(path, "rb") as handle:
        raw = handle.read()
    marker = b"\n\n"
    index = raw.find(marker)
    body = raw[index + len(marker) :] if index != -1 else raw
    return hashlib.md5(body).hexdigest()


def _local_name(artifact: Artifact) -> str:
    """A script-relative output filename for `artifact` -- safe on both `sh` and `bat`.

    Never `Artifact.path`: that path is specific to the project that built it, and an exported
    script (check 9.10) writes its outputs beside itself, not over the project's own (R731).
    Derived from the artifact id alone, so every reference to this artifact within one script
    agrees without touching the disk.
    """
    safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in artifact.id)
    return safe + _KIND_EXTENSION.get(artifact.kind, ".dat")


def _ancestor_artifacts(manifest: Manifest, job_id: str) -> list[Artifact]:
    """Every artifact `job_id` transitively depends on, ancestors first, `job_id` last.

    A depth-first walk of `built_from`, the same edge set `rad_core.manifest._walk` resolves
    digests over -- but this only orders artifacts to build, it does not touch digests, which
    `rad_core` has already computed. `rad_core.report` cannot call this directly (layering:
    `rad_core` never imports `rad_engine`), so it carries its own small copy over the same shape.
    """
    artifacts_by_id = {artifact.id: artifact for artifact in manifest.artifacts}
    order: list[Artifact] = []
    seen: set[str] = set()

    def visit(artifact_id: str) -> None:
        if artifact_id in seen:
            return
        artifact = artifacts_by_id.get(artifact_id)
        if artifact is None:
            return  # an input id, or a `project:*` synthetic scalar -- nothing to build
        seen.add(artifact_id)
        for edge in artifact.built_from:
            visit(edge)
        order.append(artifact)

    visit(job_id)
    return order


class _OutRef(str):
    """A token naming one of the script's own outputs, by its `_local_name`. Each renderer writes
    it beside the script (`"$OUT_DIR"/<name>` in `sh`, `"%OUT_DIR%<name>"` in `bat`) and never
    quotes it as an input path; in `commands_for_job`'s argv it compares equal to the bare name."""

    __slots__ = ()


@dataclass(frozen=True)
class _Step:
    """One step: `argv`, reading `stdin` (an input path) when set; then each of `then` in turn, one
    script line each, reading the previous command's file on stdin; the last command writes
    `artifact`'s output. Then `append`, when set, is added to that output with `printf` (the sky's
    glow text). No step pipes (Ruling 10)."""

    artifact: Artifact
    argv: tuple[str, ...]
    stdin: str | None = None
    then: tuple[tuple[str, ...], ...] = ()
    append: str | None = None
    prewrites: tuple[tuple[str, str], ...] = ()
    """R849: `(name, text)` pairs written with `printf` beside the script before `argv` runs -- a
    file the run compiled and then removed, recreated from the text recorded for it."""
    err_suffixes: tuple[str, ...] | None = None
    """Per command, the stderr suffix; `None` takes `_ERR_SUFFIXES` by kind."""
    intermediate_suffixes: tuple[str, ...] | None = None
    """Per command but the last, the suffix of the file it writes; `None` takes
    `_INTERMEDIATE_SUFFIXES` by kind."""
    differs: tuple[str, ...] = ()
    """R849: why the octree this step writes will not be the one the run compiled, one sentence per
    cause; empty when the step names every file the run compiled, with the bytes it compiled."""

    @property
    def sh_only(self) -> bool:
        """A step the `.bat` renderer cannot write: one that chains, reads stdin, appends or
        recreates a file."""
        return bool(self.then or self.stdin is not None or self.append is not None or self.prewrites)


def _input_path(path: str, project_root: Path) -> str:
    """R731: an input inside `project_root` as a project-relative path, which the script reads after
    changing into the project folder (its first argument, or the folder it was exported from); one
    outside it as the absolute path it was registered by, which only exists on this machine
    (`EXPORT_PATHS_ABSOLUTE`). Compared as recorded first, then with symlinks resolved on both
    sides, so a root reached through a link (`/tmp` -> `/private/tmp`) still counts as inside.

    A name that would begin with `-` gets `./` (R782), as `rad_engine.chain._relative_to_cwd`'s
    does: the script hands it to `oconv -f`."""
    recorded = Path(path)
    for candidate, root in ((recorded, project_root), (recorded.resolve(), project_root.resolve())):
        if candidate.is_absolute() and candidate != root and candidate.is_relative_to(root):
            return dash_safe_operand(candidate.relative_to(root).as_posix())
    return dash_safe_operand(path)


#: R833, widened by R849: the id prefixes `rad_mcp.annual_tool` gives the octrees `run_annual`
#: compiles (`octree:annual:<grid>` and its `:exterior` twin, the whole-grid and per-group ASE
#: octrees, the five-phase black and suns octrees). Their `built_from` names the geometry and not the
#: materials (R361, R385), while each build compiled the materials too.
_ANNUAL_OCTREE_PREFIXES = ("octree:annual:", "octree:ase:", "octree:five_phase:")

#: R849: the octree parameter holding the files on its build's command lines, in build order, and the
#: files their `!xform` lines include. Digest-excluded
#: (`rad_core.manifest.DIGEST_EXCLUDED_PARAMETERS`), so recording it moves no digest and no reuse
#: decision. Written by `compiled_scene_record`, read by `_octree_step`.
COMPILED_PARAMETER = "compiled"

#: Phase 25 C2a final fix E, fix round 4 (X1): the octree parameter holding `{path: digest}` for
#: every file the `!xform` include search reaches from the registered scene, taken from the run's
#: first `compiled_scene_record` (`includes`). **Digest-included**, unlike `COMPILED_PARAMETER`: a
#: file the registered geometry includes is read in place and is no input, so before this an edit
#: to it moved no digest, a re-run reused every matrix traced over the old file (measured: 3 of 3
#: two-phase steps reused) and `results` published them. Recorded only when the scene includes
#: something, so an octree over a scene with no include is sealed with the parameters, and the
#: digest, it always had. `rad_mcp.results_tool` re-hashes these files on every read.
SCENE_INCLUDES_PARAMETER = "scene_includes"

#: Final fix E, fix round 5 (N2): beside `SCENE_INCLUDES_PARAMETER`, `{name: resolved file}` for
#: each include the search reached by a name that resolves elsewhere (a symlink). The digests key on
#: the resolved file, so a link retargeted to another file since left every digest it was read
#: against unchanged; `rad_mcp.results_tool` re-resolves each name. Digest-included, and recorded
#: only where such a name exists.
SCENE_INCLUDE_LINKS_PARAMETER = "scene_include_links"

#: Final fix F, fix round 2 (G1): beside `SCENE_INCLUDES_PARAMETER`, the sorted names of the
#: `!xform` includes the run's oconv dropped (`OCTREE_INPUT_DROPPED`, classified from that build's
#: own stderr: `xform: cannot find file "<name>"`), each as the line wrote it. oconv exits 0 when
#: the dropped file defined nothing the scene uses, so the run succeeds and its results describe the
#: scene without that file; the include search records only files that exist, so nothing else in
#: the record says so. `rad_mcp.results_tool` carries the finding on every read of those results
#: and refuses them once a file is back at that name. Digest-included, recorded only on a drop.
SCENE_INCLUDES_DROPPED_PARAMETER = "scene_includes_dropped"

#: Final fix F, fix round 4 (I1): beside `SCENE_INCLUDES_DROPPED_PARAMETER`, every inline command
#: of the scene whose output oconv did not receive -- each `bad status from "<command>"` line of
#: that build's stderr, oconv's for a command in a registered scene file and xform's for one in an
#: include -- as `{"at": "<file> line <n>" or the file alone or None, "names": [the files xform
#: could not find in it]}`. A command xform rejected for another reason (measured: `!xform -zz
#: <file>`, "command line error") has no names, and an empty or comment-only include, which writes
#: no such line, is not one. `at` names the command by the file and line that wrote it (R823), so a
#: name outside every root is never put in a message. Digest-included, recorded only on a drop.
SCENE_INCLUDE_FAILED_PARAMETER = "scene_include_failed"


def compiled_scene_record(
    files: Sequence[str | Path],
    *,
    black: Sequence[str | Path] = (),
    recreate: Iterable[str | Path] = (),
    cwd: Path,
    raypath: Sequence[Path] = (),
) -> dict[str, Any]:
    """R849: what one `oconv` build compiled, for `COMPILED_PARAMETER`.

    `files` are `oconv -f`'s scene arguments in order; `black` the files piped through
    `xform -m black` and read as `oconv`'s `-` scene, after `files` (`rad_engine.octree.
    _build_black_backed_octree`). Each entry records the absolute path and the `sha256:` of the bytes
    read here, which the caller reads right after the build. A file in `recreate` -- one the run
    removes once the octree is built (R822's per-call glow files) -- also records its text, so an
    exported script can write it again. A file that cannot be read here records `digest: None`, and
    its export warns rather than this record failing the run that built the octree.

    `raypath` records the build's RAYPATH directories, which the export's `!`-line scan of recorded
    text searches as the build's did. `includes` records, the same way, each file the `!xform`
    include search reaches from `files` and `black` (`rad_engine.octree.scene_include_files`, with
    the build's ``raypath``). These are
    not on the `oconv` line -- the script's own scene files pull them in again -- but a change to
    one changes the octree, so the export checks their digests too. `includes: None` when that
    search raises, and the export then warns.
    """
    removed = {str(cwd / Path(path)) for path in recreate}

    def entry(path: str | Path) -> dict[str, Any]:
        absolute = str(cwd / Path(path))
        try:
            with open_regular_file(Path(absolute), "rb") as handle:
                data = handle.read()
        except (OSError, NotARegularFileError):
            return {"path": absolute, "digest": None}
        record: dict[str, Any] = {
            "path": absolute, "digest": "sha256:" + hashlib.sha256(data).hexdigest(),
        }
        if absolute in removed:
            try:
                record["text"] = data.decode("utf-8")
            except UnicodeDecodeError:
                pass  # no text recorded: the export names the file and warns once it is gone
        return record

    try:
        reached = scene_include_reach(
            [str(cwd / Path(path)) for path in (*files, *black)], cwd=cwd, raypath=raypath,
        )
    except SceneInlineCommandError:
        includes: list[dict[str, Any]] | None = None
    else:
        # Final fix E, fix round 5 (N2, N3): each include also records the names the search
        # reached it by that resolve elsewhere (a symlink), and the file whose `!xform` line
        # names it, which `rad_mcp.results_tool` reads; neither key is written where empty.
        includes = []
        for path in reached.files:
            record = entry(path)
            named = sorted(str(name) for name, target in reached.links.items() if target == path)
            if named:
                record["named"] = named
            if path in reached.included_by:
                record["included_by"] = str(reached.included_by[path])
            includes.append(record)
    return {
        "files": [entry(path) for path in files],
        "black": [entry(path) for path in black],
        "includes": includes,
        # The build's RAYPATH directories, so the export's scan of recorded text searches what the
        # build's scan searched (final review of 2026-10-05: it searched none).
        "raypath": [str(directory) for directory in raypath],
    }


def _current_digest(path: Path) -> str | None:
    """The `sha256:` of `path`'s bytes now, or `None` when it is not a readable regular file."""
    try:
        with open_regular_file(path, "rb") as handle:
            return handle_digest(handle)
    except (OSError, NotARegularFileError):
        return None


#: The read `compiled_scene_record` digests an entry with, for a caller outside this module that
#: compares a recorded entry with the file now (`rad_mcp.results_tool`, final fix E round 4, X1).
current_file_digest = _current_digest


def _compiled_entries(artifact: Artifact, key: str) -> list[dict[str, Any]]:
    record = artifact.parameters[COMPILED_PARAMETER]
    entries = record.get(key, []) if isinstance(record, dict) else None
    if key == "includes" and isinstance(record, dict) and record.get(key, []) is None:
        entries = []
    if not isinstance(entries, list) or not all(
        isinstance(e, dict) and isinstance(e.get("path"), str)
        and (e.get("digest") is None or isinstance(e.get("digest"), str))
        and isinstance(e.get("text", ""), str)
        for e in entries
    ):
        raise _refuse_type(
            artifact, COMPILED_PARAMETER, record,
            "a record of lists of {path, digest[, text]} entries",
        )
    return entries


def _compiled_octree_step(artifact: Artifact, project_root: Path) -> _Step:
    """R849: the octree rebuilt from the files its own build compiled (`COMPILED_PARAMETER`), in that
    build's order. A file still on disk with the recorded bytes is named by `_input_path`. A removed
    file whose text was recorded is written again beside the script (`prewrites`) and named there.
    A file that is gone with no text, or whose bytes changed since the build (a materials edit after
    the run, say), is named as it is now and recorded in `differs`, which `exported_octree_differences`
    reports."""
    out = _local_name(artifact)
    prewrites: list[tuple[str, str]] = []
    differs: list[str] = []
    recorded = artifact.parameters.get(COMPILED_PARAMETER)
    recorded_raypath = recorded.get("raypath") if isinstance(recorded, dict) else None
    # A record sealed before the raypath was recorded, or a hand-edited one, scans with none.
    raypath = (tuple(Path(directory) for directory in recorded_raypath)
               if isinstance(recorded_raypath, list) and all(isinstance(d, str) for d in recorded_raypath)
               else ())

    def operand(entry: dict[str, Any], label: str) -> str:
        path = Path(entry["path"])
        shown = _input_path(entry["path"], project_root)
        if entry["digest"] is None:
            differs.append(f"{shown} could not be read when the run recorded what it compiled")
            return shown
        current = _current_digest(path)
        if current == entry["digest"]:
            return shown
        text = entry.get("text")
        if text is not None:
            if "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest() != entry["digest"]:
                differs.append(f"the text recorded for {shown} is not the text the run compiled")
                return shown
            try:
                # The build's own `!`-line scan (R33), over the text before the script writes it.
                refuse_inline_commands_in_text(text, label=path, cwd=project_root, raypath=raypath)
            except SceneInlineCommandError:
                differs.append(
                    f"the text recorded for {shown} holds a `!` line the inline-command scan "
                    "refuses, so the script does not write it"
                )
                return shown
            name = f"{out}.{label}.rad"
            prewrites.append((name, text))
            return _OutRef(name)
        if current is None:
            differs.append(f"{shown} is gone")
        else:
            differs.append(f"{shown} has changed since the run compiled it")
        return shown

    files = [operand(e, f"in{i}") for i, e in enumerate(_compiled_entries(artifact, "files"))]
    black = [operand(e, f"black{i}") for i, e in enumerate(_compiled_entries(artifact, "black"))]
    record = artifact.parameters[COMPILED_PARAMETER]
    if isinstance(record, dict) and "includes" in record and record["includes"] is None:
        differs.append("the files its scene's `!xform` lines include could not be listed at the build")
    for entry in _compiled_entries(artifact, "includes"):
        shown = _input_path(entry["path"], project_root)
        current = _current_digest(Path(entry["path"]))
        if entry["digest"] is not None and current == entry["digest"]:
            continue
        what = "is gone" if current is None else "has changed since the run compiled it"
        differs.append(f"{shown}, which a scene file includes with `!xform`, {what}")
    if not black:
        return _Step(
            artifact, ("oconv", "-f", *files), prewrites=tuple(prewrites), differs=tuple(differs),
        )
    return _Step(
        artifact,
        ("xform", "-m", BLACK_MODIFIER, *black),
        then=(("oconv", "-f", *files, "-"),),
        prewrites=tuple(prewrites),
        err_suffixes=(".xform.err", ".err"),
        intermediate_suffixes=(".xform",),
        differs=tuple(differs),
    )


def _octree_step(manifest: Manifest, artifact: Artifact, project_root: Path) -> _Step:
    """`oconv -f` over `built_from`'s scene files, in edge order: a built artifact (the sky) by its
    output name, an input by `_input_path`. A `project:*` scalar carries no file, and a luminaire
    support or type record, or an automated group's hourly schedule, names none `oconv` may read
    (`_NOT_SCENE_INPUT_KINDS`, N14, R731, R870).

    **An octree that records what it compiled is rebuilt from that (R849)**: `_compiled_octree_step`.
    `run_annual` records it on every octree it seals.

    **A `run_annual` octree sealed without that record names the materials first (R833).**
    `rad_mcp.annual_tool._register_octree` leaves the materials out of `built_from` (R361, kept by
    the owner at R385), but every such build compiled `point_in_time_tool._scene_files`, which puts
    every `kind="materials"` input's files ahead of the geometry, in manifest order. For an id with
    one of `_ANNUAL_OCTREE_PREFIXES` this step puts the same files, in the same order, ahead of
    `built_from`'s, so `oconv` meets each modifier before the geometry that uses it; a materials
    record `built_from` also names is not passed twice. What else that build compiled is not known,
    so the step records in `differs` that its octree can differ from the run's."""
    if COMPILED_PARAMETER in artifact.parameters:
        return _compiled_octree_step(artifact, project_root)
    files: list[str] = []
    named: set[str] = set()
    differs: tuple[str, ...] = ()
    if artifact.id.startswith(_ANNUAL_OCTREE_PREFIXES):
        differs = (
            "it was registered without the list of files its build compiled, so the script names "
            "the project's materials and the octree's recorded inputs only",
        )
        for record in manifest.inputs:
            if record.kind == "materials":
                named.add(record.id)
                files.extend(_input_path(file_ref.path, project_root) for file_ref in record.files)
    artifact_ids = {a.id for a in manifest.artifacts}
    for edge in artifact.built_from:
        if edge.startswith(SCALAR_INPUT_PREFIX):
            continue  # a project scalar (ground_reflectance, north): no file to pass to oconv
        if edge in artifact_ids:
            files.append(_OutRef(_local_name(manifest.artifact(edge))))
            continue
        if edge in named:
            continue  # already on the line, ahead of the geometry (R833)
        record = manifest.input(edge)
        if record.kind in _NOT_SCENE_INPUT_KINDS:
            continue
        files.extend(_input_path(file_ref.path, project_root) for file_ref in record.files)
    return _Step(artifact, ("oconv", "-f", *files), differs=differs)


def _vmx_step(manifest: Manifest, artifact: Artifact, project_root: Path) -> _Step:
    """`rcontrib` from the recorded flag set plus the one octree positional (R344's guard first)."""
    _assert_required_keys_present(artifact)
    octree_positional = _resolve_single_octree_positional(manifest, artifact)
    flags = _flags_from_parameters(artifact.parameters)
    return _Step(artifact, ("rcontrib", *flags, octree_positional))


def _refuse_missing(artifact: Artifact, required: frozenset[str]) -> None:
    missing = sorted(required - artifact.parameters.keys())
    if missing:
        raise UnsupportedArtifactKindError(
            f"artifact {artifact.id!r} (kind {artifact.kind!r}) is missing {missing} from its own "
            "parameters, so its command line cannot be rebuilt from the manifest."
        )


def _refuse_type(artifact: Artifact, key: str, value: Any, wanted: str) -> UnsupportedArtifactKindError:
    return UnsupportedArtifactKindError(
        f"artifact {artifact.id!r} (kind {artifact.kind!r}) records {key}={value!r}; {wanted} is "
        "required, so its command line cannot be rebuilt from the manifest (R737)."
    )


def _recorded_int(artifact: Artifact, key: str) -> int:
    """An integer flag as recorded: an `int` that is not a `bool`. `"3"`, `3.0` and `true` are
    refused, not coerced (R737): the tool records these flags as integers (plan M27)."""
    value = artifact.parameters[key]
    if isinstance(value, bool) or not isinstance(value, int):
        raise _refuse_type(artifact, key, value, "an integer")
    return value


def _recorded_real(artifact: Artifact, key: str, *, optional: bool = False) -> float | None:
    """A real flag as recorded: a finite `int` or `float` that is not a `bool`; `null` or absent
    too when `optional` (`dt`, `dc`, which then omit their flag). An `int` is accepted because the
    tool records `aa 0.0` as `0` (plan M27)."""
    value = artifact.parameters.get(key)
    if optional and value is None:
        return None
    wanted = "a finite number" + (" or null" if optional else "")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _refuse_type(artifact, key, value, wanted)
    try:
        real = float(value)  # an int past ~1.8e308 raises OverflowError here, not inf (R737)
    except OverflowError:
        raise _refuse_type(artifact, key, value, wanted) from None
    if not math.isfinite(real):
        raise _refuse_type(artifact, key, value, wanted)
    return real


def _recorded_argv(artifact: Artifact, key: str) -> tuple[str, ...]:
    """A recorded argv: a JSON list of strings. A bare string is refused rather than iterated
    character by character (plan M20)."""
    value = artifact.parameters[key]
    if not isinstance(value, list) or not all(isinstance(token, str) for token in value):
        raise _refuse_type(artifact, key, value, "a list of strings")
    return tuple(value)


def _sky_step(manifest: Manifest, artifact: Artifact, project_root: Path) -> _Step:
    """The recorded generator, then the recorded north `xform` over its file when there is one, then
    the glow and ground text appended -- `rad_engine.sky.sky_description_text` over an empty generator
    output, so the text is the tool's own, formatted by the one function that formats it (R730).

    Since R761 the glow carries no reflectance: the recorded reflectance reaches the script through
    the recorded `generator_argv`'s `-g`, and is still read here (`_recorded_real`) so a malformed
    record is refused. A sky that records no `ground_glow`, or another one, was sealed with a
    different glow (R36's `rho rho rho` before R761) and is refused rather than exported with this
    one."""
    recorded_glow = artifact.parameters.get("ground_glow")
    if recorded_glow != GROUND_GLOW_RGB:
        raise UnsupportedArtifactKindError(
            f"artifact {artifact.id!r} records ground_glow={recorded_glow!r}; this server writes "
            f"the ground glow {GROUND_GLOW_RGB!r} and carries the ground reflectance in the sky generator's -g, "
            "so a sky sealed with another glow, including one sealed by an earlier version of this "
            "server, cannot be reproduced. Re-run run_point_in_time, then export."
        )
    _refuse_missing(artifact, _SKY_REQUIRED_KEYS)
    generator = _recorded_argv(artifact, "generator_argv")
    rotation = _recorded_argv(artifact, "rotation_argv")
    if not generator or generator[0] not in _SKY_GENERATORS:
        raise UnsupportedArtifactKindError(
            f"artifact {artifact.id!r} records sky generator {generator[:1]!r}; only "
            f"{sorted(_SKY_GENERATORS)} are reconstructed."
        )
    if rotation and rotation[0] != _SKY_ROTATION:
        raise UnsupportedArtifactKindError(
            f"artifact {artifact.id!r} records sky rotation {rotation[:1]!r}; only "
            f"{_SKY_ROTATION!r} is reconstructed."
        )
    _recorded_real(artifact, "ground_reflectance")
    glow = sky_description_text("")
    return _Step(artifact, generator, then=(rotation,) if rotation else (), append=glow)


def _rcalc_expression(artifact: Artifact) -> str:
    """`rad_core.photometry`'s own conversion, as `rcalc` text: `to_lux` for illuminance, and
    `daylight_factor`'s percentage of `REFERENCE_ILLUMINANCE_LUX` for a daylight factor. Every
    number is written with `repr(float(...))`, so the script carries the constants' own values."""
    r, g, b = (repr(float(weight)) for weight in CIE_PHOTOPIC_WEIGHTS)
    lux = f"{float(LUMINOUS_EFFICACY_LM_PER_W)!r}*($1*{r}+$2*{g}+$3*{b})"
    quantity = artifact.parameters["quantity"]
    if quantity == "illuminance":
        return f"$1={lux}"
    if quantity == "daylight_factor":
        return f"$1=100*{lux}/{float(REFERENCE_ILLUMINANCE_LUX)!r}"
    raise UnsupportedArtifactKindError(
        f"artifact {artifact.id!r} records quantity {quantity!r}; only 'illuminance' and "
        "'daylight_factor' are reconstructed."
    )


def _point_in_time_step(manifest: Manifest, artifact: Artifact, project_root: Path) -> _Step:
    """`rtrace` with `build_rtrace_argv`'s own flags, rebuilt from the recorded `parameters` (never
    re-spelled here), over the one octree edge, reading the one sensor grid's points on stdin; then
    `rcalc` (`_rcalc_expression`) over `rtrace`'s file."""
    _refuse_missing(artifact, _POINT_IN_TIME_REQUIRED_KEYS)
    expression = _rcalc_expression(artifact)
    octree = _resolve_single_octree_positional(manifest, artifact)
    input_ids = {record.id for record in manifest.inputs}
    grids = [
        manifest.input(edge) for edge in artifact.built_from
        if edge in input_ids and manifest.input(edge).kind == "sensor_grid"
    ]
    if len(grids) != 1 or len(grids[0].files) != 1:
        raise UnsupportedArtifactKindError(
            f"artifact {artifact.id!r} (kind {artifact.kind!r}) names {len(grids)} sensor grid "
            "edges in built_from; exactly one, with one points file, is required."
        )
    quality = QualityParameters(
        ambient_bounces=_recorded_int(artifact, "ab"),
        ambient_divisions=_recorded_int(artifact, "ad"),
        ambient_super_samples=_recorded_int(artifact, "as"),
        ambient_resolution=_recorded_int(artifact, "ar"),
        ambient_accuracy=_recorded_real(artifact, "aa"),
        direct_threshold=_recorded_real(artifact, "dt", optional=True),
        direct_certainty=_recorded_real(artifact, "dc", optional=True),
    )
    argv = build_rtrace_argv(RtraceRequest(octree=Path(octree), parameters=quality))
    return _Step(
        artifact,
        (*argv[:-1], octree),
        stdin=_input_path(grids[0].files[0].path, project_root),
        then=(("rcalc", "-e", expression),),
    )


#: R731: the one authority on which kinds are reconstructed -- kind -> the function that builds its
#: step. Every other `ArtifactKind` raises `UnsupportedArtifactKindError` (module docstring).
_STEP_BUILDERS: dict[str, Callable[[Manifest, Artifact, Path], _Step]] = {
    "octree": _octree_step,
    "vmx": _vmx_step,
    "sky": _sky_step,
    "point_in_time": _point_in_time_step,
}


def _flags_from_parameters(parameters: dict[str, Any]) -> list[str]:
    """`{flag: value}` (as stored, dashes already stripped) -> argv flags.

    `None` means "left at the binary's own default, never stated" (see `rad_engine.rtrace.
    parameters_dict`'s own docstring for `lw`/`dj`) and is omitted rather than emitted as a literal
    `None`. A bare `True` is a flag with no value (`-h`); `False` omits the flag entirely. A list is
    one flag with several space-separated values (`-g r g b`). Sorted by key for a deterministic
    script -- Radiance flag order does not change what a flag means.
    """
    flags: list[str] = []
    for key in sorted(parameters):
        if key in _NON_FLAG_PARAMETER_KEYS:
            continue
        value = parameters[key]
        if value is None:
            continue
        if isinstance(value, bool):
            if value:
                flags.append(f"-{key}")
            continue
        flags.append(f"-{key}")
        if isinstance(value, list):
            flags.extend(str(v) for v in value)
        else:
            flags.append(str(value))
    return flags


def _assert_required_keys_present(artifact: Artifact) -> None:
    """Refuse to reconstruct `artifact` from a `parameters` dict missing the flags its kind cannot
    be built without -- ruling R344. `_REQUIRED_KEYS_FOR_KIND` is the one place those flags are
    named (not restated here, so this docstring cannot drift from it); a `vmx` missing any of them
    is not "a plausible-looking argv this module has not verified", it is a syntactically valid
    `rcontrib ... <octree>` with something load-bearing left out -- exactly the failure mode
    `UnsupportedArtifactKindError`'s own docstring promises never to emit silently.
    """
    required = _REQUIRED_KEYS_FOR_KIND.get(artifact.kind, frozenset())
    missing = sorted(required - artifact.parameters.keys())
    if missing:
        raise UnsupportedArtifactKindError(
            f"artifact {artifact.id!r} (kind {artifact.kind!r}) is missing {missing} from its own "
            "parameters. Reconstructing its command line without them would be a truncated argv "
            "this module has not verified, not the artifact's real build. A hand-built manifest "
            "proving the happy path (tests/conftest.py's completed_job) carries every one of "
            f"{sorted(required)}; a real chain.py-sealed artifact of this kind does not yet "
            "(ruling R345), which is why this raises rather than emits."
        )


def _resolve_single_octree_positional(manifest: Manifest, artifact: Artifact) -> str:
    """The one `built_from` edge naming a built `octree` artifact, as this kind's trailing
    positional argument.

    Every other edge (aperture geometry, materials, sensor grid, weather) is provenance only for
    this kind -- its effect is already inside the octree or baked into the recorded `parameters`
    (module docstring's `-b`/`-m` gap notwithstanding). Exactly one is required: zero means nothing
    to trace against, and more than one is a shape this module has no rule for.
    """
    octree_ids = {a.id for a in manifest.artifacts if a.kind == "octree"}
    matches = [edge for edge in artifact.built_from if edge in octree_ids]
    if len(matches) != 1:
        raise UnsupportedArtifactKindError(
            f"artifact {artifact.id!r} (kind {artifact.kind!r}) names {len(matches)} octree edges "
            "in built_from; exactly one is required to reconstruct its trailing positional argument."
        )
    return _OutRef(_local_name(manifest.artifact(matches[0])))


def _steps(manifest: Manifest, job_id: str, project_root: Path) -> list[_Step]:
    manifest.artifact(job_id)  # KeyError before building anything, for an unknown id
    steps: list[_Step] = []
    for artifact in _ancestor_artifacts(manifest, job_id):
        builder = _STEP_BUILDERS.get(artifact.kind)
        if builder is None:
            raise UnsupportedArtifactKindError(
                f"artifact {artifact.id!r} has kind {artifact.kind!r}, which this module does not "
                f"yet reconstruct a command line for. Supported kinds: {sorted(_STEP_BUILDERS)}."
            )
        steps.append(builder(manifest, artifact, project_root))
    return steps


def exported_octree_differences(
    manifest: Manifest, job_id: str, *, project_root: Path
) -> list[tuple[str, str]]:
    """R849: `(octree id, cause)` for each octree in `job_id`'s chain whose exported script will
    not write the octree the run compiled -- `_Step.differs`. Empty when every octree step names
    each file its build compiled, with the bytes it compiled."""
    return [
        (step.artifact.id, cause)
        for step in _steps(manifest, job_id, project_root)
        for cause in step.differs
    ]


def commands_for_job(manifest: Manifest, job_id: str, *, project_root: Path) -> list[list[str]]:
    """The argv sequence that reconstructs `job_id` from the manifest alone, ancestors first.

    `job_id` names an `Artifact.id` (see the module docstring's "job_id names an artifact" section)
    -- `manifest.artifact(job_id)` raises `KeyError` if it names nothing, checked before any
    argv is built. Each command's argv leads with the bare binary name, the same convention every
    `build_*_argv` in `rad_engine` holds; `rad_engine.run.run_to_file` (or a generated script's own
    `PATH`) is what resolves it to a real binary, never this function.

    One argv per step: a chained step (the sky's rotation, the point-in-time result's `rcalc`) gives
    its first command here, and only the rendered script carries the commands after it. An input path is
    spelled as `_input_path` spells it against `project_root` (R731); an output as its bare
    `_local_name`.
    """
    return [list(step.argv) for step in _steps(manifest, job_id, project_root)]


def _raypath_lines_sh() -> list[str]:
    """`RAYPATH`, computed by `_LIB_CANDIDATES`' own rule: try each relative to wherever `oconv`
    is found, on this machine, at runtime; take the first containing `rayinit.cal`; carry no lib
    entry at all if none match (ruling R343 -- `discover`'s own behaviour, not a narrower one)."""
    lines = ['BIN_DIR=$(dirname "$(command -v oconv)")', 'LIB_DIR=""']
    for candidate in _LIB_CANDIDATES:
        lines += [
            f'if [ -z "$LIB_DIR" ] && [ -f "$BIN_DIR/{candidate}/rayinit.cal" ]; then',
            f'    LIB_DIR="$BIN_DIR/{candidate}"',
            "fi",
        ]
    lines += [
        'if [ -n "$LIB_DIR" ]; then',
        '    RAYPATH=".:${LIB_DIR}"',
        "else",
        '    RAYPATH="."',
        "fi",
        "export RAYPATH",
    ]
    return lines


def _raypath_lines_bat() -> list[str]:
    """`_raypath_lines_sh`'s own logic, in `cmd.exe` batch syntax -- the same three candidates,
    the same order, the same `rayinit.cal` gate, the same "carry no lib entry" fallback.

    R745. docs/tool-surface.md §17 quotes the Microsoft pages these lines rest on. The `for` command's
    `%~dp$PATH:I` gives the drive and path of the first `oconv.exe` in the directories `PATH` lists,
    as `command -v` takes the first; `%~$PATH:I` is empty when there is none, and then `BIN_DIR`
    stays cleared. `set VAR=` clears a variable. Every path read from `PATH`, `BIN_DIR` or
    `LIB_DIR`, and the `RAYPATH` value that carries `;`, sits inside quotation marks, which keep
    `&`, `(`, `)`, `;` and a space as data; `set RAYPATH=.` is the one value assigned bare, and no
    command is grouped in parentheses. **Unverified:** no Microsoft page found says that
    `set "VAR=value"` leaves the quotation marks out of the value; the `set` page documents only
    `set VAR="value"`, which keeps them, and the `cmd` page says that with double quotation marks
    "everything following the equal sign is taken as the value". Nor does any say that
    `%~dp$PATH:I` ends in a backslash, which
    `%BIN_DIR%..\\lib` and the other candidates need, as the lines before R745 did, or that `%`
    expands inside double quotation marks, which every candidate line needs (the R739 note). None
    of this has run in `cmd.exe`."""
    lines = [
        "set BIN_DIR=",
        'for %%B in (oconv.exe) do if not "%%~$PATH:B"=="" set "BIN_DIR=%%~dp$PATH:B"',
        "set LIB_DIR=",
    ]
    for candidate in _LIB_CANDIDATES:
        windows_candidate = candidate.replace("/", "\\")
        lines.append(
            f'if not defined LIB_DIR if exist "%BIN_DIR%{windows_candidate}\\rayinit.cal" '
            f'set "LIB_DIR=%BIN_DIR%{windows_candidate}"'
        )
    lines += ["set RAYPATH=.", 'if defined LIB_DIR set "RAYPATH=.;%LIB_DIR%"']
    return lines


def _single_quoted(text: str) -> str:
    """`text` in single quotes whatever it holds -- `shlex.quote` leaves a plain path bare."""
    return "'" + text.replace("'", "'\"'\"'") + "'"


def _err_names(step: _Step, commands: int) -> list[str]:
    out = _local_name(step.artifact)
    suffixes = step.err_suffixes
    if suffixes is None:
        suffixes = _ERR_SUFFIXES.get(step.artifact.kind, (".err",))
    return [out + suffix for suffix in suffixes[:commands]]


def _render_sh(steps: list[_Step], project_root: Path) -> str:
    """R731's prelude -- the script's own folder, then the project folder (the first argument, or
    the one it was exported from), changed into -- then `RAYPATH`, then one line per step."""

    def word(token: str) -> str:
        if isinstance(token, _OutRef):
            return f'"$OUT_DIR"/{shlex.quote(token)}'
        return shlex.quote(token)

    lines = [
        "#!/bin/sh",
        "set -e",
        "",
        'OUT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)',
        f"PROJECT_DIR=${{1:-{_single_quoted(str(project_root))}}}",
        'cd -- "$PROJECT_DIR"',
        "",
        *_raypath_lines_sh(),
        "",
    ]
    for step in steps:
        out = _OutRef(_local_name(step.artifact))
        commands = [step.argv, *step.then]
        errs = _err_names(step, len(commands))
        intermediates = step.intermediate_suffixes
        if intermediates is None:
            intermediates = _INTERMEDIATE_SUFFIXES.get(step.artifact.kind, ())
        for name, text in step.prewrites:
            lines.append(f"printf '%s' {shlex.quote(text)} > {word(_OutRef(name))}")
        reads = shlex.quote(step.stdin) if step.stdin is not None else None
        for index, argv in enumerate(commands):
            last = index == len(commands) - 1
            writes = out if last else _OutRef(out + intermediates[index])
            words = [word(token) for token in argv]
            if reads is not None:
                words += ["<", reads]
            words += [">", word(writes), "2>", word(_OutRef(errs[index]))]
            lines.append(" ".join(words))
            reads = word(writes)
        if step.append is not None:
            lines.append(f"printf '%s' {shlex.quote(step.append)} >> {word(out)}")
    lines.append("")
    return "\n".join(lines)


#: R739: characters a `.bat` token may hold bare. Everything else -- a space, and each of the
#: characters Microsoft's `cmd` page says must be quoted (`& < > [ ] | { } ^ = ; ! ' + ,` a
#: backtick and `~`), plus `%`, `(`, `)` and any character not listed here -- makes `_bat_quote`
#: double-quote the token.
_BAT_BARE = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-./\\:")


def _bat_literal(text: str) -> str:
    """`text` as it may sit inside a double-quoted `cmd` token, with `%` doubled so that `cmd` does not
    read `%NAME%` as a variable. A `"` or a control character is not written, and is refused.
    **Unverified:** no Microsoft page found states that `%%` is a literal percent in a batch file
    (plan M29)."""
    bad = '"' if '"' in text else next((c for c in text if ord(c) < 0x20 or c == "\x7f"), None)
    if bad is not None:
        what = "a double quote" if bad == '"' else f"control character {bad!r}"
        raise BatPathUnquotableError(
            f"{text!r} holds {what}, which this server does not write into a .bat script. "
            "Ask for shell='sh', or rename the file or folder (R739)."
        )
    return text.replace("%", "%%")


def _bat_quote(token: str) -> str:
    if isinstance(token, _OutRef):
        return f'"%OUT_DIR%{token}"'
    literal = _bat_literal(token)
    if token and set(token) <= _BAT_BARE:
        return literal
    # A program's own argument parser reads `\"` as a literal quote and 2n backslashes before a
    # quote as n (Microsoft, "Parsing C command-line arguments"), so the backslashes that end a
    # quoted token are doubled to keep its closing quote a delimiter.
    body = literal.rstrip("\\")
    return '"' + body + "\\" * (2 * (len(literal) - len(body))) + '"'


def _render_bat(steps: list[_Step], project_root: Path) -> str:
    """`_render_sh`'s prelude in `cmd.exe` syntax (`%~dp0` ends in a separator, so an output is
    `%OUT_DIR%<name>`). Only single-command steps reach here: `script_for_job` refuses the kinds
    whose steps chain commands, read stdin or append (`_SH_ONLY_KINDS`)."""
    lines = [
        "@echo off",
        "setlocal disabledelayedexpansion",
        'set "OUT_DIR=%~dp0"',
        'set "PROJECT_DIR=%~1"',
        f'if "%PROJECT_DIR%"=="" set "PROJECT_DIR={_bat_literal(str(project_root))}"',
        'cd /d "%PROJECT_DIR%"',
        *_raypath_lines_bat(),
        "",
    ]
    for step in steps:
        out = _OutRef(_local_name(step.artifact))
        [err] = _err_names(step, 1)
        quoted = " ".join(_bat_quote(token) for token in step.argv)
        lines.append(f"{quoted} > {_bat_quote(out)} 2> {_bat_quote(_OutRef(err))}")
    return "\r\n".join(lines) + "\r\n"


def script_for_job(
    manifest: Manifest, job_id: str, *, shell: Literal["sh", "bat"], project_root: Path
) -> str:
    """The exported script for `job_id` -- docs/tool-surface.md §17, `action="scripts"`.

    Every command redirects its own stderr to a private file and none merges streams (check 9.9);
    `RAYPATH` is computed inside the script itself from the machine that runs it, never inherited
    and never a value baked in at export time (see module docstring). The script changes into the
    project folder -- its first argument, or `project_root` -- reads every input there by the path
    `_input_path` gives it, and writes every output beside itself (R731). `bat` refuses a chain
    holding a kind in `_SH_ONLY_KINDS`.
    """
    if shell not in ("sh", "bat"):
        raise ValueError(f"shell={shell!r} is not 'sh' or 'bat'")
    steps = _steps(manifest, job_id, project_root)
    if shell == "sh":
        return _render_sh(steps, project_root)
    sh_only = sorted({step.artifact.kind for step in steps} & _SH_ONLY_KINDS)
    if sh_only:
        raise UnsupportedArtifactKindError(
            f"the chain of artifact {job_id!r} holds kind(s) {sh_only!r}, which only the sh script "
            "reconstructs: their steps chain one command's file into the next and read points on "
            "stdin, and "
            "the sh script is the one this server can run and test. Ask for shell='sh' (R731)."
        )
    octrees = [step.artifact.id for step in steps if step.artifact.kind == "octree" and step.sh_only]
    if octrees:
        raise UnsupportedArtifactKindError(
            f"the chain of artifact {job_id!r} holds the octree(s) {octrees!r}, whose step only the sh "
            "script writes (R849): a matrix run's interior octree writes its removed glow files "
            "again with printf, and the five-phase black and suns octrees chain `xform -m black` "
            "into `oconv`. Every other octree, and every vmx, keeps its bat script. Ask for "
            "shell='sh'."
        )
    return _render_bat(steps, project_root)
