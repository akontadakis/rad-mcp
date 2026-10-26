"""`run_annual` -- docs/tool-surface.md#11-run_annual.

Phase 4's headline deliverable, and **the first tool in this repo that actually submits to
`JobStore`**. `run_point_in_time` runs inline and synthesises an id (see its module docstring's gap
note, and ruling R43, which added the `Step.stdin_path` that closed the gap); this tool builds three
real `rad_engine.jobs.Step`s, hands them to `JobStore.submit`, and returns the durable job id that
call produces. The only Radiance this module spawns before the job exists is what R44(3) puts at
submit time: the octrees (`build_octree`/`build_black_octree`/`build_suns_octree`/
`build_ase_octree`, **up to five of them** for a 5-phase call whose `metrics` name `ase`) and, for
the suns and ASE octrees, the `cnt`/`rcalc` pair that writes the suns scene both compile
(`rad_engine.suns.suns_scene_text`, Task 14b -- a two-spawn producer of *text*, never a file, so the
write still goes through this module's own containment primitive; written once per call and shared,
`_write_suns_scene`). Everything else runs on the job
worker's thread, and the immediate return carries the inference and the plan so *"the user sees the
decision before waiting for it"*.

**Layers.** This module contains no physics and builds no Radiance command line itself.
`rad_core.method.infer` decides the method; `rad_engine.annual` owns the `rfluxmtx` and `dctimestep`
argv and the fixed sky+ground receiver; `rad_engine.skymatrix` owns the `gendaymtx` argv, the
Reinhart row count, the LM-83 subdivision floor and the north/ground advisories;
`rad_engine.octree.build_octree` compiles the scene. `rad_mcp` plumbs, contains paths, shapes the
envelope and emits the advisories -- §11's own "Layers" line.

**Since Phase 7a the step lists themselves live in `rad_engine.chain`**, which is the same line
carried one level further. `Step(` was constructed only here, in a file past 3 600 lines, and Phase
7a's five extra matrices and three extra `dctimestep` invocations would have pushed a protocol-layer
module past 5 000 -- a physics leak the Layers rule already forbids and a scan cannot see. What
moved is the two step lists, their edge sets, the 3-phase target and shape precondition, the reuse
decision and the sampling constants; what stayed is `_validate_arguments`, every refusal, every
advisory, `_dispatch`, the submit-time octree and the contained writes (`_write_contained` raises
`rad_mcp.paths.PathOutsideRoot`, a wire type the engine may not name). `_chain_request` is the seam:
`_ValidatedProject` is a protocol-layer aggregate, so the chain is handed a `ChainRequest` carrying
engine and `rad_core` types only. Nothing about the behaviour changed with the move.

## The chain, and the sky it multiplies against

    rad_engine.annual.build_rfluxmtx_argv      -> DC matrix   (sensor points on stdin_path, R43)
    rad_engine.skymatrix.build_gendaymtx_argv  -> annual sky matrix, from the project's `.wea`
    rad_engine.annual.build_dctimestep_argv    -> the `.ill`  (no stdin; both inputs are files)

**The middle step is `gendaymtx`, not `genskyvec`, and the distinction is the whole meaning of the
word "annual" in this tool's name.** `genskyvec -m 4` produces a **single-timestep** sky vector
(`NROWS=2306 NCOLS=1`); `gendaymtx -m 4` over a `.wea` produces the annual sky matrix
(`NROWS=2306 NCOLS=8760`). Both multiply cleanly against this project's DC matrix -- measured on
Radiance 6.1a on this machine, both exit 0 -- and the two `.ill` files differ only in a column count
that nothing in the output announces. §11 makes `epw` and `north` **required with no default**
precisely because this chain consumes them, annotates `sky_subdivision` as `# gendaymtx -m`, and
lists `gendaymtx` in its Layers row; `genskyvec` belonged to §10's `POINT_IN_TIME_FROM_MATRICES`
path, a different tool's, which was withdrawn with `use_matrices` (R816), and today only check
4.6's test (`tests/test_annual.py`) builds a `genskyvec` argv. Check 4.7 asserts the `.ill` is `n_points x 8760`, which only the
`gendaymtx` chain can produce. This module's own end-to-end test (`tests/test_annual_tool.py::
TestEndToEnd`) produced an `.ill` of `NROWS=456 NCOLS=8760` over a 456-point LM-83 grid, from a
`456 x 2306` DC matrix and a `2306 x 8760` sky matrix -- the three shapes it asserts off the real
headers, so a swap back to `genskyvec` (whose vector is `NCOLS=1`) goes red rather than quiet.

`rad_engine.annual`'s `build_genskyvec_argv` is therefore **not** called here, and its absence is
deliberate rather than an oversight: it is check 4.6's chain, where comparing the DC method against
`rtrace` at *one* sky is the entire point.

## What R43 and R44 decided, and where each lands

- **R43 -- `Step.stdin_path` is a path, not bytes.** `rfluxmtx` takes sensor rays *only* on stdin
  (four escape routes measured, all four fail), so `_dc_matrix_step` writes the grid's `.pts` text
  into the job directory and names it as `stdin_path`. The other two steps pass `stdin_path=None`,
  which the worker turns into `DEVNULL`.
- **R44(1) -- closed by Phase 7a Task 5, and the refusal it created is deleted rather than
  narrowed.** Until this task `DC_RECEIVER_TEXT` pinned `h=r4` -> `NCOLS=2306`, so any other
  `sky_subdivision` was refused above the `dry_run` branch (an opaque `fatal - unexpected column
  count in header` was the failure the refusal stood in for). `rad_engine.annual.dc_receiver_text(mf)`
  now builds the receiver at whatever `mf` the resolved subdivision is, so the mismatch the refusal
  guarded against is unexpressible rather than merely refused: `_shape_check` predicts both matrix
  headers from the identical `subdivision` value, and the receiver written to disk is built from that
  same value, so a caller cannot ask for a DC matrix and a sky matrix at different subdivisions any
  more than `check_matrix_shape` could see them disagree. What replaced the deleted refusal is a
  narrower one: `dc_receiver_text` itself still refuses an `mf` this project has never measured
  `rfluxmtx`/`gendaymtx` against (`SUPPORTED_SUBDIVISIONS = {1, 2, 4, 6}`, docs/tool-surface.md
  verified fact 3), and `_dispatch` turns that `ValueError` into a coded `ARGUMENT_OUT_OF_RANGE`
  refusal before the octree is built, the same position the old refusal held.
- **R44(2) -- the DC matrix commits as `dsmx`, not `vmx`.** `REQUIRED_EDGES["vmx"]` demands a
  `window_group`, which a 2-phase DC matrix does not have and cannot acquire; `dsmx` is one of the
  four kinds that table deliberately leaves uncovered. **The edge set is named in full anyway** --
  geometry, materials, sensor grid and the octree artifact (`_dc_edges`) -- because `check_edges`
  returning early for an uncovered kind is not permission to under-record. No `dsmx` row is added to
  `REQUIRED_EDGES`.
- **R44(3) -- the octree is built at submit time**, by `rad_engine.octree.build_octree`, not as a
  bare `oconv` step. A `Step` would lose R33/R38's `!`-line scan (an RCE guard whose value is that
  it reads the bytes `oconv` reads immediately before the spawn), the empty-octree check (a second
  `getinfo -d` spawn nothing would parse) and the stderr triage that raises
  `OctreeSetOverflowError`/`OctreeTooManyFilesError`. It also puts the octree above the `dry_run`
  branch, so R39 holds for free.

## One shape record, closed by a finding rather than by the original task

**M1, closed.** `receiver.rad` used to be a fixed, shared-project-root filename written on every
2-phase call regardless of `sky_subdivision`, which was benign only for a specific reason: while
`DC_RECEIVER_TEXT` was itself a fixed module constant, every writer wrote identical bytes, so which
call's write "won" a race with another never mattered. Phase 7a Task 5's own first commit
parameterised the receiver's *content* by `mf` without also parameterising its *path*, and review
measured what that reopened: two ordinary sequential `run_annual` calls -- call A at
`sky_subdivision=4`, call B at `sky_subdivision=6` racing the same shared path before call A's job
had even spawned `rfluxmtx` -- left call A's already-*queued* job reading call B's receiver, sealing
`dsmx:grid` with `parameters["mf"]=4` over a matrix physically built at MF=6, `dctimestep` exiting 1
against the *unrelated* sky matrix that did get built at `-m 4`. **No concurrency was needed** --
the write sits above the `dry_run` return, so a `dry_run=True` call -- documented at `run_annual`'s
own docstring as validating and refusing exactly as a real call would "but queue nothing" -- could
still silently mutate an in-flight real job's input by overwriting the shared file it depended on,
which is exactly the kind of work "queue nothing" says a dry run does not do.

The fix is `rad_engine.annual.dc_receiver_filename(mf)`, deriving the receiver's filename from the
same `sky_subdivision` value that already drives its content -- `receiver_mf4.rad` and
`receiver_mf6.rad`, never one shared `receiver.rad` -- so two calls at different subdivisions write
to two different paths and cannot collide by construction, no signature change required
(`DcMatrixSpec.sky_subdivision` and `build_rfluxmtx_argv(spec)` already carried the value this
needed). Two calls at the identical `mf` still share a path, exactly as every call did before this
fix, and remain benign for the identical reason: `dc_receiver_text` is pure, so both writers write
identical bytes. `_shape_check` (`rad_mcp.annual_tool`) additionally reads the receiver's own
`h=r{mf}` line back off disk before the job is queued, independently of whichever call believes it
wrote it -- the second, disk-sourced check the same review asked for, so a regression in the
filename derivation is caught by a check that can go red rather than by inspection alone.

**`infer` runs twice and the first result is discarded (M2).** Once before the project is opened, so
an unbuildable-method refusal does not depend on the manifest loading cleanly, and again against the
real `_group_facts(_window_groups(...))` once there is a manifest to read. **The two can now
genuinely disagree** -- until Phase 6b Task 5 they could not, because the second call returned `()`
unconditionally, and this paragraph recorded the second call as kept for a day that had not arrived.
It has: a project with a BSDF-bound window group reaches rule 4 on the second call and rule 5 on the
first, so the second is the one whose answer is reported and the first is genuinely provisional. The
first is still kept, for its original reason -- a refusal about an unbuildable method must not
require the manifest to open cleanly first -- and its cost is one pure function call with no I/O. The
second call's cost is no longer nothing: it reads the registered geometry back through `read_scene`
(see `_window_groups`), which is a spawn, and it is placed *after* `_validate_project` so the cheaper
manifest refusals still win the race to be reported.

## Every refusal sits above the `dry_run` branch (R39)

`_validate_arguments` (arguments alone) and `_validate_project` (the manifest, the grid, the
weather record, the scene scan) both run on the single path in `_dispatch` *before* `if dry_run`,
exactly as `point_in_time_tool` arranges it. There is no second copy for `dry_run` to drift from,
and every refusal is asserted under **both** `dry_run=True` and `dry_run=False` -- in
`tests/test_annual_tool.py` for the argument and project-state refusals, and in
`tests/test_five_phase.py` (`TestRunLevelDirectSunBinding`, `TestRunLevelDirectSunDrift`) for the two
that need a bound fenestration to reach at all. **The file names are stated because this sentence has
already been falsified once**: Phase 7a Task 13 shipped `BSDF_DIRECT_SUN_DRIFTED` with six tests that
all called the refusal function *directly*, so nothing exercised its call site below and deleting
that line left the suite green -- the claim was true of the file it named and false of the server.
A refusal added here is not covered until a test reaches it through `run_annual` under both values.

**All four of §11's refusals are built as of Phase 6b Task 9.** Two of them were unbuilt for three
phases and the recorded reason was that they *could not* be evaluated: both need window groups, no
`window_group` tool existed, and the group facts function was an empty tuple on every call, so no
BSDF, blind state or opening orientation could reach this server by any route at all. **That premise
died in Phase 6a**, which shipped `window_group(action='define'/'assign_bsdf')`, and Phase 6b Task 5
closed the last half of it: `_group_facts(_window_groups(...))` reads the project's real groups --
name, states scanned off the `tmx:<group>:<state>` artifact ids, and the whole `Aperture` re-derived
from the registered `.rad` files. This task wrote the two branches that were waiting on those facts,
so the paragraph that used to explain why they were absent is gone rather than merely amended: a
stated reason that has stopped being true is a defect in its own right, the class §9 records for an
advisory whose definition is untrue of its condition.

- ~~**ASE with any blind state other than fully open** -- `_refuse_ase_with_blinds`,
  `ASE_WITH_BLINDS_REFUSED`.~~ **Retired at Phase 7d-1 Task 19 fix, owner ruling R228.** It read a
  bound state's *name* and refused `ase` for anything that did not read as open. Measured:
  `rad_engine.octree.build_ase_octree` is the registered scene plus the suns scene, with no
  aperture strip and no BSDF proxy, so a bound state enters the T matrix and **never** the ASE
  octree, under any `blind_control`. The refusal therefore blocked, on a name, a computation
  LM-83 sec 3.2.6 requires and this server performs correctly. What replaces it is
  `_ase_blind_geometry_advisory` (`ASE_BLIND_GEOMETRY_UNDETECTED`, `warn`), which names the hazard
  that is real -- an operable blind modelled as *scene geometry*, which IS in the ASE octree -- and
  states plainly that this server cannot detect it. See that function's docstring.
- ~~**`annual_dgp` on a horizontal opening** -- `_refuse_dgp_on_a_horizontal_opening`.~~
  **Deleted by R815 (owner decision of 2026-10-05), with the metric.** `annual_dgp` is withdrawn
  from `metrics`, so a call naming it is refused as a withdrawn metric in `_validate_arguments`
  (`_WITHDRAWN_METRICS`) before any project is opened. `run_glare` keeps its own refusal of DGP
  against a horizontal opening.

**The ordering these refusals needed is gone with them.** `ase` and `annual_dgp` both drove `infer`
rule 1 to 5-phase unconditionally, so each fenestration refusal had to sit above the
unbuildable-method refusal and, from Phase 7a Task 14 to R815, above `METRIC_NOT_COMPUTED_YET`'s
refusal of `annual_dgp`, and `_dispatch`'s early method refusal deferred for the metrics they keyed
on. With both refusals gone (R228, R815), nothing defers: the early refusal answers every call,
which is M2's property. `method="3phase"` with `ase` (`METHOD_OVERRIDE_UNSAFE`, raised by `infer`
itself and shaped here) and `sda` without `ase` (`SDA_REPORTED_ALONE`) were evaluable all along.

**`udi` was refused from R106 until Phase 7c Task 19**, under `METRIC_NOT_COMPUTED_YET`, while no
UDI tally existed in `rad_core`; Phase 7c built the tally (`rad_core.metrics.
useful_daylight_illuminance`, `spatial_udi`, `udi_hours_required`) and the reader
(`results(action="metrics", metrics=["udi"])`), and the refusal was deleted rather than re-pointed
at a later phase.

**`blind_control` was narrowed in Phase 6b Task 9 (owner decision, 2026-08-16) and fully lifted at
Phase 7c Task 11: all three of `none`, `fixed` and `lm83` are accepted.** `none` and `fixed` need no
machinery -- both are single-state runs, which is the only kind `chain.three_phase_target` builds.
`lm83` was refused for one stated reason, *"this server does not build that schedule"*, and Task 11
built the run the schedule is read off: the pre-blind, zero-bounce direct-sun leg
(`chain.direct_sun_steps`, sealed as `illdir:<grid>`). A refusal whose stated reason has stopped
being true is a defect in its own right, so it was deleted rather than re-pointed at a later phase.
**Accepting it now IS applying it, on the 3-phase chain -- Phase 7d-1 Task 19.** `_dispatch` hands
`three_phase_steps` the whole resolved `ThreePhaseTarget` on an `lm83` run, so the multiply fans out
per (group, state); the direct-sun leg follows; `chain.blind_schedule_steps` seals `sched:<grid>`
from the leg's per-group `illdir`; and `chain.blend_steps` seals `illblend:<grid>` from the schedule
and every per-(group, state) `.ill`. Multiply, `illdir`, `sched`, blend, in that order in the one
list `reuse_decision` walks -- which is the order, not a preference: a `sched:<grid>` ordered above
its own `illdir` operands would be reported reused in the very job that rebuilt them.
`BLIND_SCHEDULE_DERIVED` still names `results(action="blind_schedule")` as where the schedule is
read. `SDA_WITHOUT_BLIND_SCHEDULE` is now **narrowed** rather than universal: it fires on an `lm83`
run whose chain seals no blend -- a 2-phase run, or a 5-phase one, `blend_steps` being a step of the
three-phase chain and of no other -- and not on the 3-phase run that seals one, whose sDA genuinely
is computed against the schedule. (~~Because sDA is not blind-aware until the blend lands in Phase
7d~~ -- amended at Phase 7d-1 Task 15a (R178) to *"because this run seals no blended result"*, and
narrowed here to the runs of which that is still true.) See `_BLIND_CONTROL_ACCEPTED` and
`_plans_blend`.

**What reading the groups costs, and what it can now refuse.** Re-deriving an aperture opens the
registered geometry, so two conditions that were previously unreachable from this tool are now
shaped into coded refusals in the handler chain rather than escaping as tracebacks: a registered
`.rad` deleted after registration (`SCENE_FILE_NOT_FOUND`, ruling R29's code, read off
`SceneFileNotFoundError` itself) and a group whose polygons no longer form one coplanar aperture
(`APERTURE_NOT_COPLANAR` / `APERTURE_NORMAL_AMBIGUOUS`, read off the `ApertureError` itself). No
code is invented for either -- both were already allocated for `window_group`'s own definition-time
refusals, and this is the read-time echo of the same fact.

## A method reported but not built is a wrong number waiting to be read

`infer` runs on **every** call and `METHOD_INFERRED` is emitted on every call, refusals included --
a claim this module made and did not keep until finding I2, when it was measured on **2 of 9** exit
paths. It is now carried by `_inference_context`, attached to every refusal in `_validate_arguments`
and in the handler chain, and enforced by `TestMethodInferredIsAlwaysEmitted::
test_method_inferred_is_on_every_refusal`, which parametrises the same `_REFUSALS` table the R39
sweep uses, so a refusal added later is covered by this property without anyone remembering to.
This module builds **all three methods** as of Phase 7a. The rule that got it here was never
relaxed, only satisfied: an inference this server could not build was **refused** -- naming the
method, its reasons and the phase that would build it -- rather than quietly running a lesser chain
under a `method.chosen` that said otherwise.

**Phase 6b Task 6 turned the 3-phase half of that refusal off by building the chain**
(`chain.three_phase_steps`, the five-step `V -> genklemsamp -> D -> S -> V*T*D*S` list), and
**Phase 7a Tasks 14/14b turned the 5-phase half off the same way.** Task 14 admitted `5phase` to
`_BUILDABLE_METHODS` and routed a 5-phase inference to `three_phase_target` and
`five_phase_shape_check` -- then found that nothing in `src/` had ever built the two octrees the
direct-sun terms trace against, and refused (`FIVE_PHASE_OCTREE_NOT_WIRED`) rather than ship a
3-phase plan wearing a 5-phase label. Task 14b built that caller (below, beside the two annual
octrees) and **deleted the code in the same commit**: a registered advisory code for a state that can
no longer occur is the defect docs/tool-surface.md §9 names. `annual_dgp` was refused on every
call (`METRIC_NOT_COMPUTED_YET`, R81, named since R101 for its absent image-based V matrix) until
R815 withdrew it from `metrics`; the code is retired. `sda`/`ase` graduated out of that refusal at
R101 (`results(action="compliance")` computes them; `sda` alone is refused instead for LM-83's own
pairing rule, `SDA_REPORTED_ALONE`), and `udi` at Phase 7c Task 19. Never the method, either way.

**This sentence said `udi` was still refused until the whole-branch fix wave**, which is the third
task in a row to carry it: it was filed against Task 19, re-dispatched to Task 20, and survived
both because both greps looked at `src/` call sites and not at the prose beside them. A documented
refusal that does not exist is the mirror of the defect §9 names -- a client reads the tool
description and never sends the argument.

## The 3-phase chain, and the three things about it that are not obvious

    matrices.build_v_argv         -> vmx:<group>:<grid>   (sensor rays on stdin_path, R43)
    matrices.build_genklemsamp_argv -> rays file           (stdout_path; step 3 reads it)
    matrices.build_d_argv         -> dmx:<group>           (the rays on stdin_path)
    skymatrix.build_gendaymtx_argv -> smx:<epw>
    annual.build_three_phase_argv -> ill:<grid>            (dctimestep V T D S)

- **T is not a step** (ruling R62). It is the BSDF XML `window_group(action="assign_bsdf")` copied
  into `07_matrices/` and sealed as `tmx:<group>:<state>`; step 5 names that file as its second
  operand and the `.ill` names it as an edge. That is what makes T the only term a BSDF change
  invalidates -- if T were built here, a BSDF change would invalidate the step that built it and the
  distinction would collapse.
- **Steps 2 and 3 are one pipe, two spawns, never a shell pipe.** `rad_engine.matrices`' docstring
  gives the reason (separately attributable stderr); the mechanism is R43's `stdin_path`, with step
  2's `stdout_path` and step 3's `stdin_path` naming one job-local file.
- **Two octrees.** The V matrix traces the interior with the aperture as a `glow` light source; the
  D matrix traces the exterior against a white sky dome, with the aperture sampled as plain geometry
  from a job-local `.rad`. `rad_engine.klems`' `glow_surface`/`polygon_surface` split is the engine's
  own statement that these are different scenes, and this module is the caller that honours it.

**`sky_subdivision` is genuinely free on this path**, which is ruling R67's narrowing made real: the
D matrix is built at whatever `mf` is asked for and the sky is built to match, so the pair agrees by
construction (`rad_engine.matrices.reinhart_bins` is the analogous "measured subdivisions only" guard
on this path, pre-existing and outside this module). **Since Phase 7a Task 5 the 2-phase path is
free the same way** -- `dc_receiver_text(mf)` builds the DC-matrix receiver at whatever `mf` is
asked for, so both paths now agree by construction rather than one of them being pinned.

## The 5-phase chain's own two octrees, the ASE one beside them, and where all five are built

Phase 7a Task 14b built the two five-phase octrees; **Phase 7c Task 11 added a fifth**, and the
count in this section is the number a caller can actually pay for on one call.

`chain.five_phase_steps` reuses the 3-phase chain's own first four steps verbatim, so a 5-phase call
compiles **everything a 3-phase call compiles, plus two octrees of its own** -- and a call that also
plans the direct-sun leg compiles one more. All five at submit time, all five through
`rad_engine.octree`'s guarded entry points, all five above the `dry_run` branch:

    build_octree        annual_<grid>.oct                 V's interior scene (glow aperture)
    build_octree        annual_<grid>_exterior.oct        D's exterior scene (white sky dome)
    build_black_octree  five_phase_<grid>_black.oct       Vd and Dd (5ph-method.md 3.1)
    build_suns_octree   five_phase_<grid>_mf<mf>_suns.oct Cds (5ph-method.md 4.2)
    build_ase_octree    ase_<grid>_mf<mf>.oct             asemx, LM-83 ASE (ch04 sec 3.3)

**The fifth is not a variant of the fourth**, and reading it as one is the mistake
`build_ase_octree`'s docstring is written to prevent: the suns octree is the registered scene
**blackened and aperture-stripped** with a tensor-tree BSDF proxy in the aperture's place, while the
ASE octree is the registered scene **whole** -- glazing, opaque shell and fixed shading all in it,
all keeping their own materials -- plus the same suns scene. ASE is traced at `-ab 0`, so there is
no bounce for a black shell to suppress, no proxy is wanted (LM-83 measures the beam *after* the
glazing's own transmittance), and the aperture must stay or every sensor sees the unattenuated beam.
It is built only when the leg is planned (`"ase" in metrics` or `blind_control == "lm83"`): a DA-only
run pays for neither it nor the `rcontrib` that reads it.

The two new ones are `xform -m black` over a scene piped into `oconv`, differing in what is added
**unblackened** on the `oconv` side and -- since Task 15b -- in *which* scene is blackened, which
together are what makes them two scenes rather than one:

- the black octree blackens the registered scene and adds `octree.black_material()` plus the same
  glow and sky-dome `.rad` files the two annual octrees already carry (3.1 adds
  `viewmtxsurf.rad`/`sky_white.rad` for exactly that reason);
- the suns octree blackens the registered scene **minus the window group's own aperture polygons**
  (`scene.scene_without_modifiers`, written as `five_phase_<grid>_shell.rad`) and adds
  `black_material()`, the tensor-tree BSDF proxy (`klems.bsdf_proxy_surface`, over the XML the
  `role='direct_sun'` binding registered) and the suns scene (`suns.suns_scene_text`,
  `1 + 144*mf^2` named sources). **Blackening the aperture into it instead made `Cds` identically
  zero on every project this server can build** -- a blackened surface is a perfect occluder, and one
  behind the proxy kills the shadow ray to every sun. That was this branch's Critical; see
  `_dispatch`'s own comment at the build call, and `docs/tool-surface.md` §11.

**The scene files those octrees are composed from are written by this module, contained (R48), and
the suns scene carries `mf` in its filename.** Its *content* is a function of `mf`, and a scene file
whose content depends on an argument but whose path does not is the receiver-collision defect Phase 7a Task 5's
review measured in full (see "One shape record" above): two calls at different subdivisions, one
overwriting what the other is about to compile, with no concurrency needed because the write sits
above `dry_run`. `suns.suns_scene_filename(mf)` derives it, exactly as `annual.dc_receiver_filename`
does for the receiver, and the suns *octree* carries `mf` too because that scene is compiled into it.

All of them are sealed as `octree`-kind artifacts before `submit` (`_register_octree`), because
`REQUIRED_EDGES` carries `vdmx`/`ddmx`/`cdsmx`/`asemx` rows naming them and `check_edges` refuses an
edge that resolves to nothing. The suns octree additionally names the `role='direct_sun'` BSDF input
as an edge: the proxy compiled into it references that file, so a re-bind must invalidate the octree
and `Cds` with it. **The ASE octree names no BSDF at all** -- there is no proxy compiled into it, and
ASE is evaluated pre-blind, so a blind re-bind must *not* invalidate it.

## Reuse, which CONTEXT.md calls "the entire value of 3-phase" (Task 8, check 6.8)

Change a glazing BSDF and **T alone** invalidates: V, D and S survive, because T is not a step (R62)
and the `.ill` names all four terms as edges. Until this task nothing exercised that.
`rad_engine.project.is_reusable` -- the four-condition predicate (digest unchanged, file present,
bytes still sealed per R7, edges still satisfied per R26) -- had been built, tested at the engine
layer and given **zero production call sites** since Phase 2; `reuse_advisory` had never constructed
a `MATRIX_REUSED`; `Step.reuse` was a flag nothing set. `chain.reuse_decision` is the one function that
calls all three, and it lives here rather than in `rad_engine.jobs` for the reason that module's own
`Step.reuse` docstring gives: *"the reuse decision is `is_reusable`'s, taken in the tool layer where
the manifest is read. This is the mechanism, not the policy."*

Three properties are worth stating because each one is a way this could have been built wrong:

- **The reused step stays in the submitted list.** Filtering it out satisfies check 6.8(d) -- no
  `rcontrib` spawned -- and breaks 6.8(b), which requires the reused artifact still to appear in the
  plan under `reuse: true`. `_plan` derives the plan from the steps actually submitted (R30), so a
  removed step is an absent plan row. R65 resolved the pull the other way: the flag rides on the
  step and `JobStore._run` declines to spawn it.
- **A step with no artifact is never reused.** Step 2 of the 3-phase chain (`genklemsamp`) writes a
  job-local rays file that step 3 reads on stdin and that nothing seals; skipping it would leave
  step 3 reading a file this job never wrote.
- **`rebuild` beats the predicate, and the refusal that used to guard it is gone rather than
  narrowed.** Its stated reason -- *"this phase reuses no matrices... there is nothing for rebuild
  to force past"* -- stopped being true the moment `chain.reuse_decision` existed, and a refusal whose
  reason is false is a defect in its own right.

`RADIANCE_VERSION_CHANGED` is **not** built here and is deliberately not an invalidator:
`rad_engine.project.open_project` emits it and `is_reusable` never consults version, so an upgrade
flags rather than invalidates. What this module now does is **relay** it (`_dispatch`), which it
never did -- measured absent before Task 8, while `project(action='describe')` reported it off the
same manifest. With reuse real, that warning is the only thing telling a caller their reused
matrices were built by a different binary than the one about to multiply them.
"""

from __future__ import annotations

import errno
import logging
import os
import re
import tempfile
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import replace
from pathlib import Path
from types import MappingProxyType
from typing import Annotated, Any, Final, Literal, NamedTuple, cast

from mcp_types import CallToolResult
from pydantic import BaseModel, ConfigDict, Field, RootModel, StrictInt, StrictFloat

from rad_core.apertures import Aperture, ApertureError, derive
#: The two words LM-83 sec 2.2.6's schedule names a state by, imported rather than retyped --
#: see `_refuse_unbindable_blend_states`.
from rad_core.blinds import (
    AUTOMATED_SCHEDULE_HOURS,
    BLIND_STATES,
    MAX_AUTOMATED_SCHEDULE_BYTES,
    AutomatedScheduleError,
    parse_automated_schedule,
)
from rad_core.bsdf import BSDFError
from rad_core.lm83_edition import (
    DEFAULT_LM83_EDITION,
    LM83_22,
    LM83_22_NOT_APPLIED,
    LM83_EDITIONS,
    Lm83Edition,
    edition_title,
    section,
)
from rad_core.geometry import BoundingBox, Polygon, bounding_box
from rad_core.manifest import (
    ArtifactWarning,
    InputRecord,
    ProvenanceValue,
)
from rad_core.method import (
    Inference,
    Metric,
    RequestedMethod,
    StateKind,
    WindowGroupFacts,
    infer,
)
from rad_core.weather import wea_hour_count
from rad_engine import (
    LoadedProject,
    ProjectError,
    ProjectPaths,
    ProvenanceError,
    RadianceEnv,
    RadianceError,
    RadianceNotFound,
    RadianceTimeoutError,
    SceneFileNotFoundError,
    build_ase_scene,
    bytes_digest,
    discover,
    drifted_inputs,
    evaluate_loaded,
    file_digest,
    open_project,
    parse_polygons,
    read_scene,
    register_artifact,
    reuse_advisory,
    scene_without_modifiers,
    unclaimed_transmitting_modifiers,
    worker_cores,
)
from rad_engine.annual import (
    SUPPORTED_SUBDIVISIONS,
    DcMatrixHeader,
    MatrixFileUnreadableError,
    MatrixShapeError,
    check_matrix_shape,
    dc_receiver_filename,
    dc_receiver_text,
    read_receiver_subdivision,
)
from rad_engine.chain import (
    ANNUAL_TIMESTEPS,
    BSDF_PROXY_MODIFIER,
    GLOW_MODIFIER,
    LM83_BLIND_CONTROL,
    SAMPLE_MODIFIER,
    SKY_MODIFIER,
    AutomatedSchedule,
    AutomatedScheduleRecordError,
    ChainRequest,
    ThreePhaseGroup,
    ThreePhaseTarget,
    WindowGroup,
    automated_group_names,
    automated_schedule_file,
    automated_schedules,
    blend_steps,
    blind_schedule_steps,
    direct_sun_groups,
    direct_sun_steps,
    refuse_colliding_outputs,
    SparseLm83GridError,
    five_phase_shape_check,
    five_phase_steps,
    reuse_decision,
    sample_rad_paths,
    states_by_group as _states_by_group,
    three_phase_shape_check,
    three_phase_steps,
    three_phase_target,
    two_phase_steps,
)
# Aliased because `_dispatch`'s local variable for the result is `reused_ids`, which the bare
# import would shadow. The private spelling is also the name this function had while it lived in
# this module, so the call site below is unchanged by the move.
from rad_engine.chain import WINDOW_GROUP_INPUT_PREFIX as _WINDOW_GROUP_PREFIX
from rad_engine.chain import reused_ids as _reused_ids
from rad_engine.chain import AUTOMATED_SCHEDULE_INPUT_PREFIX as _SCHEDULE_PREFIX
from rad_engine.jobs import JobError, Step, new_job_id
from rad_engine.klems import (
    bsdf_proxy_surface,
    displaced_glow_surface,
    glow_surface,
    polygon_surface,
    sky_dome_surface,
)
from rad_engine.advisories import classify_line
from rad_engine.export import (
    COMPILED_PARAMETER, SCENE_INCLUDE_FAILED_PARAMETER, SCENE_INCLUDE_LINKS_PARAMETER,
    SCENE_INCLUDES_DROPPED_PARAMETER, SCENE_INCLUDES_PARAMETER, compiled_scene_record,
)
from rad_engine.octree import (
    read_scene_text,
    octree_body_digest,
    EmptyOctreeError,
    OctreeSetOverflowError,
    OctreeTooManyFilesError,
    SceneInlineCommandError,
    black_material,
    build_ase_octree,
    build_black_octree,
    build_octree,
    build_suns_octree,
)
from rad_engine.scene import build_ase_net_scene
from rad_engine.rtrace import QualityParameters, ambient_interpolation_radius_m, resolve_quality
from rad_engine.safe_io import NotARegularFileError, open_regular_file, resolved_parent_within
from rad_engine.skymatrix import (
    LM83_MIN_SKY_SUBDIVISION,
    north_convention_advisory,
    reinhart_row_count,
    resolve_lm83_sky_subdivision,
    sky_subdivision_raised_advisory,
)
from rad_engine.suns import (
    SunPosition,
    sun_directions,
    suns_scene_filename,
    suns_scene_text,
)
from rad_engine.weather import WEA_ENCODING
from rad_mcp.advisories import to_wire
from rad_mcp.names import SENSOR_GRID_INPUT_PREFIX, is_legal_name, name_refusal_reason
from rad_mcp.envelope import Advisory, RadResult, Refusal, emit, one_of_schema, refuse, refuse_missing
from rad_mcp.lit_scene import excluded_advisory
from rad_mcp.paths import PathOutsideRoot, RootsNotConfigured, contain_octree_destination
from rad_mcp.point_in_time_tool import (
    GridEmptyOnDiskError,
    GridNotFoundError,
    GridUnreadableError,
    NoGeometryRegisteredError,
    _grid_points_file,
    _read_points,
    _scan_scene_for_inline_commands,
    _scene_files,
    refuse_grid_off_recorded_floor,
)
# **The one source for LM-83 sec 2.2.6's analysis area, imported rather than re-derived (ruling
# R172, Phase 7d-1 Task 19).** `_analysis_area` turns the polygon `sensor_grid(action='create')`
# recorded for a grid into the square feet the blind-trigger table selects a row from, and it is
# what `results(action='blind_schedule')` already reads. The submit side needs the identical number
# -- the schedule this call seals is the schedule that read publishes -- so it takes it off the same
# function instead of writing a second `polygon_area_m2 * M2_TO_SQFT` here. A private name across
# modules is the lesser evil: the alternative is two derivations of the number the one-source rule
# exists to keep single. `rad_mcp.results_tool` imports nothing from this module, so the pair does
# not cycle -- `tests/test_annual_tool.py` asserts that rather than leaving it to the next import.
from rad_mcp.results_tool import (
    _analysis_area, _and_list, _dropped_outside_roots, _dropped_outside_sentence,
    _failed_command_label, _failed_command_parts, _missing_include_label, _where_named,
    _stopped_after_sentences, GridFileOutsideRoot, _grid_outside_refusal,
)
from rad_mcp.scene_warnings import light_redirecting_advisory
from rad_mcp.runtime import Runtime, current, refuse_engine_error
from rad_mcp.geometry_tool import _INPUT_PREFIX as _GEOMETRY_PREFIX
from rad_mcp.weather_tool import _INPUT_PREFIX as _WEATHER_PREFIX
from rad_mcp.window_group_tool import LM83_DEFAULT, _bsdf_input_role, _redefine_call

logger = logging.getLogger(__name__)

__all__ = [
    "ANNUAL_TIMESTEPS",
    "DESTRUCTIVE_ACTIONS",
    "AnnualResponse",
    "AnnualResult",
    "run_annual",
]

#: **Empty, and that is the claim** -- the same property `point_in_time_tool.DESTRUCTIVE_ACTIONS`
#: exports for check 0.7's sweep. `run_annual` takes no `action` argument, deregisters no input and
#: deletes no user file: it writes a job directory, an octree and the matrices it names, all of
#: which it owns. Exported rather than omitted so the sweep reads this property off the surface
#: itself rather than off a list kept in the test.
DESTRUCTIVE_ACTIONS: tuple[str, ...] = ()

#: A hard bound on the submit-time `oconv`, in seconds. **300 s, taken from what this repo already
#: does rather than chosen: `geometry_tool._ASSEMBLY_TIMEOUT_S` is 300.0 and bounds
#: `assemble_scene`'s octree build** -- the same `oconv` spawn, over the same registered geometry,
#: one tool over. Reusing that number is what keeps two tools from disagreeing about how long
#: compiling the same project's geometry may take.
#:
#: **Why a bound at all.** `build_octree` takes a `timeout` and passes it to `run_to_file`, but
#: defaulted to `None` here, so nothing bounded this spawn in production. The Python-side scan
#: refuses a FIFO among the *scene files*, but ruling R5 gives that scan `continue` as its
#: disposition -- an unreadable file is left for `oconv` to report -- so the path still reaches
#: `oconv`, which blocks on it with no bound. `run_annual` builds the octree **above** the `dry_run`
#: branch (R44(3)/R39), so `run_annual(dry_run=True)` parks in the spawn's wait indefinitely, in an
#: `anyio` worker thread whose pool limiter is 40.
#:
#: **Why 300 s and not the two smaller candidates.** R44(3) says an octree build is seconds in the
#: normal case, so the bound must be generous relative to that and still finite -- it exists to end
#: a hang, not to cap a slow-but-working build, and a bound that fires on real geometry is a
#: regression dressed as a guard. `octree._METADATA_TIMEOUT_S` (30 s) bounds `getinfo -d`, a
#: metadata read, not a compile. `point_in_time_tool.DEFAULT_INLINE_TIMEOUT_S` (20 s) bounds an
#: *inline trace*, sized to return inside a client's patience. Neither is sized for compiling a
#: large architectural model; `_ASSEMBLY_TIMEOUT_S` is the one that already is, and it is the same
#: operation. 300 s is ~2 orders of magnitude above the normal case and still finite.
#:
#: `jobs.Step.timeout_s` is the right bound for the *steps* this call queues, and they carry their
#: own; it cannot bound these spawns, which happen at submit time before any step exists.
#:
#: **It bounds every submit-time spawn, not one of them, and the count grew to five at Phase 7c
#: Task 11**: `build_octree` twice (the interior and exterior annual octrees), `build_black_octree`
#: and `build_suns_octree` on a five-phase call, and `build_ase_octree` on any call that plans the
#: direct-sun leg -- plus `scene_without_modifiers` and `suns_scene_text`, the two `xform`/`cnt`
#: producers that write the scenes two of those compile. Every one of them passes this constant, and
#: `tests/test_annual_tool.py::TestTheOctreeBuildIsBounded::test_the_call_site_passes_the_bound`
#: checks the keyword over the AST for all of them: a builder added with no `timeout=` is invisible
#: to any test that does not actually hang, which is how the first of these sites went unbounded
#: through a whole phase.
OCTREE_BUILD_TIMEOUT_S: float = 300.0

#: `metrics` values that put a call under LM-83, and therefore under its 2 305-Reinhart-patch sky
#: floor (`resolve_lm83_sky_subdivision`). sDA and ASE are LM-83's own two metrics
#: ([ch04](../../skills/lighting-standards-compliance/chapters/ch04-ies-lm-83-sda-ase.md)); the
#: others are EN 17037's, CIE's, or a raw timeseries, and none of them cites LM-83's sky resolution.
#: `rad_engine.skymatrix` deliberately gives no opinion on which metrics count as LM-83 -- its own
#: docstring assigns that decision to this layer -- so this is the set, named once.
_LM83_METRICS: frozenset[str] = frozenset({"sda", "ase"})

#: The metrics vocabulary §11 publishes, restated for the wire signature. Matches
#: `rad_core.method.Metric` exactly; that module's docstring records why it restates rather than
#: imports, and the same applies in the other direction here -- this is the published argument type.
_METRICS: tuple[str, ...] = (
    "sda", "ase", "udi", "da", "en17037_provision", "illuminance_timeseries",
)

#: Metric names this tool published once and has withdrawn, each with the sentence the
#: unknown-metric refusal in `_validate_arguments` appends when a call still names it, so a saved
#: call is told which edit makes it run.
#:
#: **`annual_dgp`, withdrawn by R815 (owner decision of 2026-10-05, register N-8).** It was
#: published and refused on every call: `METRIC_NOT_COMPUTED_YET` named its absent image-based V
#: matrix, and on a horizontal opening an `ARGUMENT_OUT_OF_RANGE` refusal said DGP does not apply.
#: Both refusals, the sets that keyed them (`_UNCOMPUTED_METRICS`, `_HORIZONTAL_UNDEFINED_METRICS`)
#: and the deferral predicate that kept them reachable (`_may_reach_a_fenestration_refusal`) left
#: with it, and `METRIC_NOT_COMPUTED_YET` is retired.
_WITHDRAWN_METRICS: Final[dict[str, str]] = {
    "annual_dgp": (
        "'annual_dgp' was withdrawn: annual glare needs an image-based view matrix this server "
        "does not build. Drop it from metrics; run_glare gives DGP for one sky and view."
    ),
}

#: **`_UNCOMPUTED_METRICS` and `_UNCOMPUTED_METRIC_REASONS` were deleted by R815.** They named the
#: metrics this server published but could not produce a number for (R81; `sda`/`ase` left at R101,
#: `udi` joined at R106 and left when Phase 7c built its tally), and were refused with
#: `METRIC_NOT_COMPUTED_YET`. Their last member, `annual_dgp`, was withdrawn from `_METRICS` instead
#: (`_WITHDRAWN_METRICS`), so the refusal and its code are gone.
#:
#: **UDI's provenance, stated exactly, because a metric's grounds are as reportable as its numbers.**
#: The band comes from R106 and the papers it cites and the period question from R111 -- **not from
#: any standard**. `skills/lighting-standards-compliance/`'s corpus (EN 12464-1/-2, EN 17037,
#: LM-83-12, EN 13363, EN 15193) defines UDI nowhere; re-verified for Task 19, where the skill's only
#: three mentions of the string are cross-references to the sibling `radiance-simulation-workflow`
#: skill for how to compute it. Nothing in this module may name a standard as UDI's source. `da` is
#: the contrasting case and keeps its citation: LM-83's fixed 08:00-18:00, 3 650 h/yr window is in
#: the corpus, and sDA is built on DA.


def _as_metrics(metrics: list[str]) -> list[Metric]:
    """The `list[str]` off the wire as the `list[Metric]` `infer` declares, membership re-checked.

    **Not a cast.** `_validate_arguments` has already refused any name outside `_METRICS` above the
    `dry_run` branch, so by here every element is a `Metric` -- but that proof lives in another
    function and no checker can follow it, which would leave the call site either annotated as a lie
    or silenced with a `# type: ignore`. Both hide the one thing worth keeping visible: `infer` does
    **not** validate its own input. An unknown metric falls through all five rules and is answered
    `2phase` with no reason and no error (measured; pinned by
    `TestTheMetricVocabularyCannotDrift::test_infer_answers_2phase_for_an_unknown_metric`), so the
    membership check is the only thing between a typo and a silently wrong method inference.

    Re-checking here costs one pass over a short list and turns "an earlier function
    guarantees this" into something that fails loudly if it ever stops being true. The `ValueError`
    is deliberately not a coded refusal: reaching it means `_validate_arguments` was bypassed or has
    drifted, which is a defect in this module rather than anything a caller can act on.
    """
    unknown = [m for m in metrics if m not in _METRICS]
    if unknown:  # pragma: no cover -- unreachable while _validate_arguments runs first
        raise ValueError(
            f"metrics {unknown} reached the inference without being validated. "
            "_validate_arguments refuses unknown metrics above the dry_run branch; if this "
            "raised, that ordering has been broken."
        )
    return cast("list[Metric]", metrics)

#: What this phase can actually build. The inference may legitimately answer `5phase`; see the module
#: docstring's last section for why that is a refusal rather than a silent downgrade.
#:
#: **`3phase` joined this set in Phase 6b Task 6**, when `chain.three_phase_steps` gave the inference's
#: 3-phase answer a chain to run.
#:
#: **`5phase` joined this set in Phase 7a Task 14.** The set says what the *chain* can build, and it
#: is not the same question as what the *metrics* can report: `rad_core.method.infer` answers
#: `5phase` for `ase`, so widening this set widens what a caller reaches without naming it.
#: (`annual_dgp` also drove rule 1 until R815 withdrew it; `METRIC_NOT_COMPUTED_YET` refused it in
#: front of the 5-phase chain it could not report from.)
_BUILDABLE_METHODS: frozenset[str] = frozenset({"2phase", "3phase", "5phase"})

#: docs/tool-surface.md §11's own equation line for the method this phase builds. Reported on the
#: `method` object so the caller can see the arithmetic behind the number, exactly as §11's example
#: return does for 5-phase (`I_5ph = V*T*D*S - Vd*T*Dd*Sds + Cds*Ssun`).
_EQUATION: dict[str, str] = {
    "2phase": "I_2ph = DC * S",
    "3phase": "I_3ph = V*T*D*S",
    "5phase": "I_5ph = V*T*D*S - Vd*T*Dd*Sds + Cds*Ssun",
}

# **`_METHOD_PHASE` was removed by R811 (Phase 22, item 40).** It mapped each method to the phase
# of docs/plan.md that built it (`{"3phase": "Phase 6", "5phase": "Phase 7a"}`) and its only reader
# was `_refuse_unbuildable_method`'s unreachable backstop, whose message named the phase. That
# message now says what the backstop means -- this server cannot build the inferred method yet --
# and names no phase. The comment below is the record it carried, kept for its history; the
# `_refuse_uncomputed_metric` and `_UNCOMPUTED_METRICS` it names were deleted by R815.
#
# Which phase of docs/plan.md built each method. Kept as the historical record of which phase built
# what -- **not** as a live source for a "this method is unbuildable" refusal any more.
#
# **Both rows are now unreachable from `_refuse_unbuildable_method`, and that is Task 14's own
# change, not a pre-existing fact restated.** `_BUILDABLE_METHODS` is `{"2phase", "3phase",
# "5phase"}` -- the whole of `rad_core.method.Method` -- so `_refuse_unbuildable_method`'s own guard
# (`inference.method in _BUILDABLE_METHODS and not inference.refused`) is `True` for every method
# that function could ever be asked about, and returned `None` before `_METHOD_PHASE` was ever read
# for a refusal. The one path left that could make it `False` -- `inference.refused`, which only
# rule 1 (`ase`/`annual_dgp` with a contradicting `method=`) can set -- is itself pre-empted at both
# of `_refuse_unbuildable_method`'s own call sites in `_dispatch`: `_refuse_uncomputed_metric` is
# called immediately before it, at the identical position, and `inference.refused` implies `ase` or
# `annual_dgp` is in `metrics` (rule 1's own precondition), so `_refuse_uncomputed_metric` always
# has something to refuse whenever `_refuse_unbuildable_method` otherwise would. `infer` itself
# still runs -- `ase`/`annual_dgp` are deferred *past* `_validate_arguments`, not blocked by it, so
# the fenestration and direct-sun refusals that key on the real inference stay reachable
# (`_UNCOMPUTED_METRICS`'s own docstring). `_refuse_unbuildable_method` is kept anyway, as the
# defensive backstop it always was for a method `_BUILDABLE_METHODS` does not name -- seven lines
# that cost nothing and stop meaning "the calendar" the moment a new `Method` value (or a narrowed
# `_BUILDABLE_METHODS`) makes it reachable again.
#
# **No other reader is left for `_METHOD_PHASE['5phase']` either.** The `blind_control='lm83'`
# refusal used to name it, then said **Phase 7b** in a literal string instead, and **is gone
# altogether as of Phase 7c Task 11** -- the schedule's own direct-sun run is built, so the refusal
# has no reason left to state (`_BLIND_CONTROL_ACCEPTED`). Both edits were the same rule applied
# twice: a refusal naming the wrong phase is worse than one naming none, and a refusal whose reason
# has stopped being true is worse than either. The
# `sda`-without-`ase` refusal used to share that reasoning too, back when its own gap was Phase 7b's
# arithmetic; as of R101, Phase 7b Task 15 it is LM-83's own permanent pairing rule (ch04 §2.4)
# rather than a temporary calendar gap, so its message names no phase at all -- there is no future
# phase after which `metrics=['sda']` alone stops being refused.

#: The `blind_control` values this server honours -- **owner decision, 2026-08-16 (Phase 6b Task 9),
#: completed at Phase 7c Task 11 when `lm83` joined the set.**
#:
#: §11 publishes `Literal["lm83","none","fixed"] | None` and all three are now admitted. `none` and
#: `fixed` were admitted at Phase 6b Task 9 because both describe the single-state run
#: `chain.three_phase_target` already builds. `lm83` was refused for one reason and one only -- *"it
#: selects an hourly per-group blind schedule derived from a direct-only run, and this server does
#: not build that schedule"* (that refusal's own words, quoted; what 7c derives is the WHOLE-GRID
#: schedule, and ruling R116 publishes `groups: null` for the per-group dimension `illdir:<grid>`
#: cannot resolve) -- and Phase 7c Task 11 built the run that reason named: the
#: dedicated pre-blind, zero-bounce direct-sun leg (`chain.direct_sun_steps`), sealed as
#: `illdir:<grid>`. **A refusal whose stated reason has stopped being true is a defect in its own
#: right** (the same class §9 records for an advisory), so the refusal is deleted rather than
#: re-worded onto a later phase.
#:
#: **Admitting it is not applying it, and the run says so rather than letting a caller infer
#: otherwise.** `BLIND_SCHEDULE_DERIVED` names `results(action="blind_schedule")` as where the
#: schedule is read from -- 7c derives it at read time and seals nothing (R107) -- and
#: `SDA_WITHOUT_BLIND_SCHEDULE` keeps firing on any run whose `metrics` name `sda`, ~~because sDA
#: is not blind-aware until the blend lands in Phase 7d~~ -- **amended at Phase 7d-1 Task 15a
#: (R178): because this run seals no blended result.** ~~What no run this server submits does yet
#: is produce one, because `_dispatch`'s one-group `blind_control="none"` wrap stands until Phase
#: 7d-1 Task 19.~~ **Task 19 landed that wiring**: a 3-phase `lm83` run now fans out per (group,
#: state) and seals `illblend:<grid>`, so the caveat is narrowed by `_plans_blend` to the runs that
#: still seal none -- 2-phase and 5-phase. Both are attached in `_dispatch`.
#:
#: Named as a set rather than checked with an `in (...)` literal at the one call site so the refusal
#: message can enumerate the vocabulary from the same source that decides it, rather than restating
#: it in prose that can drift from the check beside it.
_BLIND_CONTROL_ACCEPTED: frozenset[str] = frozenset({"none", "fixed", "lm83"})

#: The one `blind_control` value that selects LM-83's hourly blind schedule, and therefore the
#: one that makes `run_annual` plan the direct-sun leg regardless of `metrics`. **Not a per-group
#: schedule**: `illdir:<grid>` is one matrix per grid, and `results(action="blind_schedule")`
#: publishes `groups: null` rather than attributing a whole-grid count to a group (R116). Named because two
#: places read it -- the leg's trigger and the two advisories above -- and a bare `"lm83"` literal in
#: each is two independent statements of one decision.
#:
#: **Phase 7d-1 Task 6: the value itself now comes from `rad_engine.chain`, not from a literal
#: here.** `chain.three_phase_target` resolves a multi-state window group only under this value, and
#: this module plans the direct-sun leg only under it -- one decision, taken in two layers, so the
#: engine owns the string and this name re-exports it rather than restating it (ruling R30).
_LM83_BLIND_CONTROL: Final[str] = LM83_BLIND_CONTROL


def _plans_blend(*, method: str, blind_control: str | None) -> bool:
    """Whether this call will plan LM-83 section 2.2.6's blend -- **one predicate, two readers**.

    Phase 7d-1 Task 19 wires `chain.blind_schedule_steps` and `chain.blend_steps` into the submitted
    step list, and two places in `_dispatch` have to agree about whether that happened: the wiring
    itself, and `SDA_WITHOUT_BLIND_SCHEDULE`, whose whole message is *"this run seals no blended
    result"*. An advisory that says so on a run that seals one is a false published sentence, which
    is what section 9 of docs/tool-surface.md forbids -- and the two sites are three hundred lines
    apart, which is exactly the distance over which two hand-written conditions drift. Ruling R30's
    posture, and `chain.direct_sun_groups`' own reason for being a function.

    **Two conditions, and both are narrower than they look.**

    - `method == "3phase"`. `chain.blend_steps` is a step of the three-phase chain and of no other
      -- `rad_mcp.results_tool` already publishes that about `illblend:<grid>` -- because
      `five_phase_steps` is single-group, single-state by construction. A 5-phase run under
      `blind_control="lm83"` therefore still seals no blend, and still gets the advisory.
    - `blind_control == "lm83"`. The same value `chain.direct_sun_groups` keys on.

    **Why this is not a third statement of `direct_sun_groups`' condition.** That function answers
    over a resolved `ThreePhaseTarget`, which does not exist yet at the advisory site; this answers
    over the two arguments that decide whether one will be resolved at all. The two are equivalent
    at the wiring site and `tests/test_annual_tool.py` asserts the equivalence over the whole cross
    product of method and `blind_control` rather than leaving it as this paragraph's claim: a
    3-phase inference resolves a target or refuses the call outright (`three_phase_target` refuses a
    project with no window group), and `direct_sun_groups` returns that target's groups for `lm83`
    and `()` for everything else.
    """
    return method == "3phase" and blind_control == _LM83_BLIND_CONTROL


#: **`_HORIZONTAL_UNDEFINED_METRICS` was deleted by R815.** Its one member, `annual_dgp`, keyed the
#: DGP-on-a-horizontal-opening refusal; the metric is withdrawn, so no call can reach that refusal.
#: `run_glare` makes the same refusal for its own placement (`glare_tool`).

#: **`_OPEN_STATE_NAMES` was deleted here (owner ruling R228, 2026-08-31).** It was the set
#: `_refuse_ase_with_blinds` matched a bound state's name against to decide whether a blind was
#: "deployed" for the ASE run. That refusal is retired -- `rad_engine.octree.build_ase_octree`
#: compiles the registered scene plus the suns scene with no BSDF anywhere in it, so a bound state
#: never reaches the ASE octree on any path and the name was a signal with no causal link to the
#: quantity. `_ase_blind_geometry_advisory` replaces it and keys on nothing about the project, for
#: the reason recorded in its own docstring: a name-based gate at a lower severity would be the
#: same wrong signal. Nothing else in `src/` read the set.


# --- nested shapes -----------------------------------------------------------------------------
# Plain models: only a top-level result carries `advisories` (`envelope.RadResult`).


class _Nested(BaseModel):
    model_config = ConfigDict(extra="forbid")


# docs/tool-surface.md §11's `method` object. *"Reported, not hidden"*: `reasons` is the citation
# trail `rad_core.method.infer` built, carried verbatim rather than re-worded here. R810 moved the
# pointers out and added `equation` (`_EQUATION`, one entry per method).
class MethodView(_Nested):
    """The calculation method `run_annual` chose, reported before the job starts.

    `chosen` is the method the job runs (`"2phase"`, `"3phase"` or `"5phase"`). `requested` echoes
    the `method` argument (`"auto"` by default) so the two can be compared. `reasons` is the
    inference's own reasoning, with its citations. `equation` is the chosen method's matrix
    equation, such as `I_3ph = V*T*D*S`.
    """

    chosen: str
    requested: str
    reasons: list[str]
    equation: str


# One row of docs/tool-surface.md §11's `plan` array. `reuse` is real as of Phase 6b Task 8: it is
# `Step.reuse` verbatim, the same flag the worker reads (`JobStore._run`), so the plan's claim and
# the job's behaviour are one fact. Check 6.8(b) asserts the `reuse: true` set is exactly {S, V, D},
# which a filtered plan could not report. R810 moved this out and added `artifact`'s null and
# `estimate_s`'s 0.0 (`_plan`: `step.artifact.artifact_id if step.artifact else None`,
# `step.estimate_s or 0.0`).
class PlanStep(_Nested):
    """One step of the job's plan: what will run, what it commits, and its estimated cost.

    `step` names the step and `artifact` the artifact it commits, `null` for a step that commits
    none. `reuse: true` means the job will not run this step: its existing artifact is still valid
    and is used as it is. A reused step is still listed. `estimate_s` is the step's declared
    estimate in seconds, `0.0` when it declares none; it is uncalibrated, not a measured time.
    """

    step: str
    artifact: str | None
    reuse: bool
    estimate_s: float


# Real as of Phase 6b Task 8, and CONTEXT.md calls the thing it now reports "the entire value of
# 3-phase". Until that task every field here was a measured zero: `run_annual` submitted every step
# on every call, `rad_engine.project.is_reusable` had zero production call sites despite being built
# and tested at the engine layer since Phase 2, and `MATRIX_REUSED` was allocated and never emitted.
# `chain.reuse_decision` is the caller that closed all three. This history left the published
# description under R809 (item 38). R810 moved out the pointers: `JobStore.submit` attaches
# `RUNTIME_ESTIMATE_UNCALIBRATED` to the job record (its status, not this result) when the job
# declares an estimate; the BSDF claim is the invalidation table's T row (T is not a step, R62),
# asserted end to end by check 6.8. "`reused` counts the steps flagged" is
# `len(_reused_ids(steps))`.
class ReuseView(_Nested):
    """What this call skipped because a valid artifact already exists, and what that saved.

    `reused` counts the plan's `reuse: true` steps; `rebuilt` counts every step that will run,
    including steps that commit no artifact. `saved_estimate_s` sums the reused steps' estimates:
    like every estimate here it is uncalibrated, a guide to relative cost, not a measured saving of
    wall-clock time.

    In a 3-phase job, a change to a bound BSDF invalidates the `.ill` result, while the V, D and
    sky matrices survive it.
    """

    reused: int
    rebuilt: int
    saved_estimate_s: float


# docs/tool-surface.md §11's return. R810 moved the pointer out and lifted the field notes on
# `timesteps` (`state.weather.n_hours`, not `ANNUAL_TIMESTEPS`), `sky_subdivision` (after
# `resolve_lm83_sky_subdivision`; default `LM83_MIN_SKY_SUBDIVISION`) and
# `ambient_interpolation_radius_m` (`AMBIENT_RADIUS_COARSE` against `_room_bounding_box`), which
# never reached the wire. "This is the hand-off, not the values" is new host text: `run_annual`
# queues the job and returns; the values are read with `results` (`results_tool`).
class AnnualResult(RadResult):
    """The queued annual job: its id, the method chosen, the plan, and what was reused.

    This is the hand-off, not the values. `job_id` names a real job, recorded before this returns:
    poll it with `job(action="status")`, and read the results with `results` once it has finished.

    `n_points` is the grid's sensor count, the `.ill`'s row count. `timesteps` is the column count
    the job intends to produce, one per timestep of the loaded weather (not always 8 760);
    `results` reports the count actually written. `sky_subdivision` is the Reinhart subdivision
    used (`gendaymtx -m`), after any raise to LM-83's floor (`SKY_SUBDIVISION_RAISED` says so), 4
    when none was given. `ambient_interpolation_radius_m` is the octree's bounding-cube size x
    `-aa` / `-ar`; `AMBIENT_RADIUS_COARSE` is attached when it exceeds one twentieth of the
    shortest side of the bounding box of the project's geometry (not checked when that side is 0).
    """

    job_id: str
    method: MethodView
    plan: list[PlanStep]
    reuse: ReuseView
    n_points: int
    """Sensor count of the grid this job traces -- the `.ill`'s row count, and the `-y` passed to
    `rfluxmtx`. Published because it is half the result shape a caller needs to read the matrix."""
    timesteps: int
    """The `.ill`'s expected column count: one per `.wea` timestep. **The number this tool intends
    to produce, from the sky matrix it is about to build** -- `results` reports the count actually
    written, read from the matrix header. See `rad_engine.chain.ANNUAL_TIMESTEPS` on why the two are
    kept apart.

    **Published from `state.weather.n_hours` since Phase 7a Task 14's review round 1 (Important 5),
    not from `ANNUAL_TIMESTEPS`.** The constant is still this field's documented *source* in
    `ANNUAL_TIMESTEPS`'s own docstring, written when nothing in this module measured a real hour
    count; now that `_weather` does, publishing the constant instead would report `8760` against a
    matrix this tool is about to build with a different column count -- exactly the "intends to
    produce" claim turning into a measurable falsehood the moment the real number was in hand, rather
    than a stated approximation of one nothing here could see yet."""
    sky_subdivision: int
    """`gendaymtx -m`, after the LM-83 floor was applied. The value actually used, per check 4.4."""
    ambient_interpolation_radius_m: float
    """``Rmin = maxSize x aa / ar`` (`rad_engine.rtrace.ambient_interpolation_radius_m`), off the
    parameters this call's own steps queue and the bounding cube of the octree those steps trace
    against -- docs/tool-surface.md#16-quality's *"every `run_*` result reports
    `ambient_interpolation_radius_m`"*, mirroring `PointInTimeResult`'s own field
    (`point_in_time_tool.PointInTimeResult.ambient_interpolation_radius_m`).
    `AMBIENT_RADIUS_COARSE` (warn) is attached to `advisories` when this exceeds one twentieth of
    the project's own smallest room dimension -- see `_room_bounding_box`."""


# `method` and `plan` are computed above the `dry_run` branch, so a preview answers the question the
# caller asked; no `job_id` is synthesised (`point_in_time_tool.DryRunOk` shares the name). R810
# corrected "every argument validated ... nothing was refused" (`runtime.store.submit` can still
# refuse a full queue below the branch) and added, from `_dispatch`'s own comment at the branch,
# that a dry run compiles the octrees and writes files into the project without recording them, and
# that `AMBIENT_RADIUS_COARSE` is checked only below the branch.
class DryRunOk(RadResult):
    """`run_annual` with `dry_run=true`, and nothing was refused: no job was queued.

    `method` and `plan` are what a real call would return, reuse flags included. A dry run is not
    free of side effects: it compiles the octrees the plan needs and writes them, and the job's
    input files, into the project without recording them in the manifest. `advisories` lacks
    `AMBIENT_RADIUS_COARSE`, which only a real call checks.
    """

    dry_run: Literal[True] = True
    method: MethodView
    plan: list[PlanStep]


AnnualPayload = AnnualResult | DryRunOk | Refusal


# The same `oneOf` pattern `PointInTimeResponse`/`ProjectResult`/`JobResult` use. Declared as the
# tool's return annotation so the SDK validates every payload against it before it reaches the
# wire; the function body still returns a `CallToolResult` directly via `emit`. R810 moved this out.
class AnnualResponse(RootModel[AnnualPayload]):
    """`run_annual`'s result: an `AnnualResult`, a `DryRunOk` when `dry_run=true`, or a
    refusal."""

    @classmethod
    def model_json_schema(cls, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return one_of_schema(super().model_json_schema(*args, **kwargs), AnnualResult)


# --- argument-shape refusals, shared by the real call and dry_run -------------------------------


def _refuse_argument(
    message: str, *, field: str, advisories: list[Advisory] | None = None
) -> CallToolResult:
    """`ARGUMENT_OUT_OF_RANGE` at `error`, naming the argument -- the register's general code for a
    refused argument, and *"the `field` key already says which argument"* is why one code covers the
    class (`runtime.refuse_engine_error`).

    `advisories` carries the inference context (`METHOD_INFERRED`, and `LM83_EDITION` where it
    applies) so a refusal reports the inference rather than only the reason -- finding I2. It comes
    first, matching `rad_core.method`'s own guarantee that `METHOD_INFERRED` is the first warning in
    any list it builds.
    """
    return refuse(
        message,
        [
            *(advisories or []),
            Advisory(
                code="ARGUMENT_OUT_OF_RANGE", severity="error", field=field, message=message
            ),
        ],
    )


def _refuse_not_a_regular_file(
    message: str, *, advisories: list[Advisory] | None = None
) -> CallToolResult:
    """`PROJECT_PATH_NOT_A_REGULAR_FILE` at `error` -- **ruling R58, 2026-08-10.**

    A path the server was asked to read or write is not a regular file, refused by
    `rad_engine.safe_io.open_regular_file`'s `S_ISREG` on the descriptor it has **already opened**
    -- not before the open completes, which is what this sentence said through Phase 9 Task 11 fix
    round 4: the whole point of that guard is that the check runs on an open descriptor rather than
    on the path, so there is no TOCTOU window. A symlink at the final component therefore does
    *not* arrive here as this exception at all -- `O_NOFOLLOW` fails the open first, as a plain
    `OSError` (`ELOOP`).

    **Where that `ELOOP` lands depends on which read raised it, and it is not always this
    advisory.** This sentence used to end *"which this module's own handler chain codes to the same
    advisory"*, unscoped; the whole-branch fix wave measured reads in this module that publish
    three different codes for it. `run_annual`'s octree read gates its `except OSError` on
    `errno.ELOOP` and refuses through this function, so **that** route does publish
    `PROJECT_PATH_NOT_A_REGULAR_FILE`, for the reason ruling R58 gives. `_write_contained`'s
    `except OSError` raises `PathOutsideRoot`, which the handler chain shapes into
    `PATH_OUTSIDE_ROOT`. `_measured_n_hours` catches `(NotARegularFileError, OSError)` together and
    raises `WeatherNotLoadedError`. Only the first of those publishes this advisory. **The three
    are what was measured, not a claim that no fourth route exists** -- what is corrected here is
    the old sentence's unscoped "the same advisory", which was false for two of the three the fix
    wave looked at.

    It is deliberately *not* `_refuse_argument`: R14 scopes
    `ARGUMENT_OUT_OF_RANGE` to an argument rejected **on its face, before any work was attempted**,
    and this is rejected on the file's type, discovered by opening it, with the argument perfectly
    well-formed.

    The two want opposite responses, which is the whole reason for the split -- one is repaired by
    passing a different value, the other by removing what was planted at a path the caller may not
    have named. Before this code existed both left `run_annual` as `ARGUMENT_OUT_OF_RANGE` with
    `field="project"`, indistinguishable on the wire.

    Always `field="project"`: the offending path is project state rather than an argument of the
    call, and there is no argument to name. Kept as a parameterless field rather than a `field=`
    argument so no call site can quietly point it at something else.
    """
    return refuse(
        message,
        [
            *(advisories or []),
            Advisory(
                code="PROJECT_PATH_NOT_A_REGULAR_FILE",
                severity="error",
                field="project",
                message=message,
            ),
        ],
    )


def _with_context(result: CallToolResult, context: list[Advisory]) -> CallToolResult:
    """Prepend the inference context to an already-built refusal envelope.

    For the one refusal path this module does not construct itself: `runtime.refuse_engine_error`
    owns the engine-exception -> wire-refusal mapping for **every** tool and takes no advisory list.
    Adding a parameter there to satisfy one tool's "always emitted" advisory would put this tool's
    concern inside the single place those two shapes are supposed to meet generically.

    The context goes **first**, matching `rad_core.method`'s guarantee that `METHOD_INFERRED` leads
    any list it builds, and the refusal's own advisories keep their order after it.
    """
    payload = result.structured_content
    if not context or not isinstance(payload, dict):
        return result
    merged = [a.model_dump(exclude_none=True) for a in context] + list(
        payload.get("advisories") or []
    )
    return refuse(str(payload.get("reason", "")), [Advisory(**a) for a in merged])


def _inference_context(
    metrics: object,
    method: object,
    blind_control: object = None,
    edition: object = DEFAULT_LM83_EDITION,
) -> list[Advisory]:
    """The advisories every exit from `run_annual` carries: `METHOD_INFERRED`, plus `LM83_EDITION`
    on an LM-83 run, plus `ASE_BLIND_GEOMETRY_UNDETECTED` where the metrics name `ase` --
    **finding I2**, widened by the whole-branch fix wave.

    **An LM-83 run is `_is_lm83_run`'s** (R864, R866): `sda` or `ase` in `metrics`, or
    `blind_control="lm83"`, which applies LM-83's blind rule whatever the metrics. `edition` names
    the edition in both advisories; a value outside `LM83_EDITIONS` is read as the default here,
    because this function cannot raise -- `run_annual` refuses that value itself.

    **The ASE caveat is here because the sentence that publishes it says "every run", and it was
    not.** `_dispatch` appends it too, for the successful path, and its own comment claimed that
    appending it above the refusals below made a refused call carry it. Measured at `7a18916` with
    `metrics=["sda","ase"]`: **present** on a run that is accepted, **absent** on a window group
    with no BSDF bound, on no window group at all, on `epw="nosuch"`, on `grid="nosuchgrid"` and on
    a `method="3phase"` override — because every one of those refusals is *raised* out of
    `_dispatch` and caught by `run_annual`'s handler chain, which rebuilds the list from this
    function's result and never sees `_dispatch`'s local one. The only refusals genuinely below
    that append were the fenestration refusals, whose one member needed `annual_dgp`, which was
    itself refused (and is withdrawn since R815). So the caller the comment named -- *"the caller who is about to fix a binding and
    retry"* -- was exactly the caller who never got it.

    **Fixed here rather than by softening the sentence** (owner ruling R253): R228 allocated the
    code to carry an honest caveat about a hazard this server cannot detect, and a caveat that
    vanishes on the refusal paths is the defect, not the wording. Two call sites of one factory,
    which is `LM83_EDITION`'s own shape: this function for every exit `run_annual` builds, and
    `_dispatch` for the one it returns itself. They never merge, so nothing is emitted twice.

    **This cannot raise, and that is its whole job.** It runs *before* `_validate_arguments`, so
    `metrics` may be `None`, a non-list, or hold values `infer` has never heard of; a refusal about
    a malformed argument must still report what the inference could honestly say, and must never be
    replaced by a traceback from the reporting code itself. Anything unusable degrades to the empty
    metric list, which `infer` answers with rule 5 (2-phase) -- an honest answer to "no
    direct-sun-sensitive metric was requested" rather than a fabricated one. The ASE caveat is
    keyed on that same filtered list, so a `metrics` this function could not read asks for no ASE
    and is told nothing about one.

    `window_groups` is empty here by necessity rather than by choice: this runs before the project
    is opened. `_dispatch` re-runs the inference against the real manifest once it has one, and it
    is that result which reaches a successful call's `method` object. **Since Phase 6b Task 5 the
    two can differ** -- `_group_facts(_window_groups(...))` reads real groups, so a project with a
    BSDF-bound group is answered rule 5 here and rule 4 there. That is not a defect in this
    function: an *argument* refusal is about the arguments, and reporting the fenestration-free
    inference on it is the most this function can honestly say before any project has been opened.
    What it must never do is report that answer on a *successful* call, and it does not --
    `_dispatch`'s second call is what reaches `MethodView`.
    """
    safe_metrics: list[Metric] = []
    if isinstance(metrics, list):
        safe_metrics = [m for m in metrics if m in _METRICS]  # type: ignore[misc]
    safe_method: RequestedMethod = (
        method if method in ("auto", "2phase", "3phase", "5phase") else "auto"  # type: ignore[assignment]
    )
    safe_edition: Lm83Edition = (
        edition if edition in LM83_EDITIONS else DEFAULT_LM83_EDITION  # type: ignore[assignment]
    )
    advisories = _inference_advisories(infer(safe_metrics, [], safe_method))
    if _is_lm83_run(safe_metrics, blind_control):
        advisories.append(_lm83_edition_advisory(safe_edition))
    # `list(safe_metrics)` rather than `metrics`: the caveat's own published condition is "the
    # metrics name `ase`", and `safe_metrics` is the only reading of that this function trusts.
    if (
        ase_caveat := _ase_blind_geometry_advisory(list(safe_metrics), edition=safe_edition)
    ) is not None:
        advisories.append(ase_caveat)
    return advisories


def _is_lm83_run(metrics: Sequence[object], blind_control: object) -> bool:
    """Whether a call is an **LM-83 run** -- `sda` or `ase` in `metrics`, or `blind_control="lm83"`
    (R864, R866). One predicate, read by the sky-subdivision floor, by `LM83_EDITION` and by the
    `lm83_edition` refusal, so the three cannot disagree about which runs LM-83 governs. Before R866
    the floor and the advisory read the metrics alone, so a blended `metrics=["da"]` run applied
    LM-83's blind rule at subdivision 2 with no edition stated.
    """
    return any(m in _LM83_METRICS for m in metrics) or blind_control == _LM83_BLIND_CONTROL


def _lm83_edition_advisory(edition: Lm83Edition = DEFAULT_LM83_EDITION) -> Advisory:
    """`LM83_EDITION` (info) -- stated once, so the refusal path and the success path cannot drift.

    **What it may claim** (Task 4 fix round 1): this run's edition decides only the clauses its own
    advisories cite and, on a three-phase `blind_control="lm83"` blend, the blind rule sealed into
    `sched:<grid>`. `results` judges against its own per-call `lm83_edition`, so the message says
    which edition this run used and tells the caller to pass the same one to `results`; it does not
    claim the thresholds a later judgement will use.

    *"Thresholds are LM-83-12; ANSI/IES LM-83-22 supersedes it, and this server judges against
    LM-83-12 only. State the edition."* (R862: the owner established on 2026-10-11 that the edition
    superseding LM-83-12 is ANSI/IES LM-83-22; this line and the message used to name another.)

    **R864: the edition is now the call's** (`lm83_edition`), so the message names it by
    `edition_title` and cites its own period and ASE-band clauses from `section`. Under the
    default it says what it said, except that LM-83-22 is no longer "not a selectable edition": it
    says how to select it. Under LM-83-22 it names the edition's source document and
    the LM-83-22 rules this server does not apply (Branch C1 listed Still open items 47 to 51;
    the paragraph below gives today's list).

    **R869, R870 and R871 took Still open items 47, 50 and 53** (gross and net ASE, the default 5 %
    blind, automated glazing): under LM-83-22 the message now says how ASE_net and the default blind
    are reached (`window_group` `automated=True` and
    `states={'closed': 'lm83_default'}`), and the not-applied list is what is left -- items 48, 49,
    51, 52 and 54, and Table 4-1's louvre defaults and section 4.8's dynamic-glazing rule, which
    R871 and R870 left unwired (`rad_core.lm83_edition.LM83_22_NOT_APPLIED`, the one sentence
    `results` lists too). The automated groups' exclusion from the trigger is stated for a
    three-phase blend only, the one run that seals a schedule (final review, W-M2). The LM-83-12
    text is unchanged.
    """
    period, bands = section("period", edition), section("ase_bands", edition)
    rule = section("blind_trigger", edition)
    if edition == LM83_22:
        message = (
            f"This run's LM-83 edition is {edition_title(edition)} (lm83_edition='LM-83-22'), cited "
            "by section number: its advisories "
            f"cite that edition (analysis period {period}, ASE line {bands}), and a three-phase "
            "blind_control='lm83' blend seals its blind rule into the schedule ("
            f"{rule}: more than 2 % of the points at every area). results judges against the "
            "lm83_edition it is given, so pass lm83_edition='LM-83-22' to results when judging "
            "these results. ASE_net is computed with the window groups marked automated "
            "(window_group automated=True) blacked, and on a three-phase blind_control='lm83' "
            f"blend their sunlight leaves the 2 % trigger ({rule}); LM-83-22's default blind, 5 % "
            "of the open state's diffuse transmittance, is window_group "
            f"states={{'closed': 'lm83_default'}}. {LM83_22_NOT_APPLIED} -- state the edition "
            "when reporting."
        )
    else:
        message = (
            f"This run's LM-83 edition is {edition_title(edition)}, the default edition: its "
            f"advisories cite that edition (analysis period {period}, ASE bands {bands}), and a "
            "three-phase blind_control='lm83' blend seals its blind rule into the schedule "
            f"({rule}'s area table). ANSI/IES LM-83-22 supersedes it and can be chosen with "
            "lm83_edition='LM-83-22'. results judges against the lm83_edition it is given, the "
            "default when none is, so pass the same lm83_edition to results when judging these "
            "results -- state the edition when reporting."
        )
    return Advisory(code="LM83_EDITION", severity="info", field="metrics", message=message)


def _validate_arguments(
    *,
    grid: str,
    metrics: list[str] | None,
    north: float | None,
    method: str,
    sky_subdivision: int | None,
    quality: str | dict[str, Any],
    blind_control: str | None,
    advisories: list[Advisory] | None = None,
    edition: Lm83Edition = DEFAULT_LM83_EDITION,
) -> CallToolResult | None:
    """Every refusal readable from the arguments alone, or `None` if there is none.

    Runs **above** the `dry_run` branch (R39). Ordering is deliberate: the shape of `metrics` is
    checked before anything that reads it, and `sky_subdivision` -- R44(1)'s refusal -- is checked
    before the project is even opened, since it cannot be made valid by any project state.

    `advisories` is the inference context every refusal here carries (finding I2). Bound once into
    the local `_refuse_argument` below rather than threaded through twelve call sites by hand: a
    refusal that forgot to pass it would be exactly the silent omission this fix exists to close,
    and a local binding makes forgetting impossible rather than merely unlikely.

    `edition` is the call's `lm83_edition` where it is one of the two, else the default (it is
    refused after this function): the `SDA_REPORTED_ALONE` refusal cites that edition's reporting
    clause (final review, W4).
    """
    def _refuse(message: str, *, field: str) -> CallToolResult:
        return _refuse_argument(message, field=field, advisories=advisories)

    # **`grid` is charset-validated here, and it was not before.** It is an ordinary string that
    # `rad_engine.chain` concatenates into filenames -- `matrices / f"dsmx_{grid}.mtx"`,
    # `results_dir / f"annual_{grid}.ill"`, and the direct-sun leg added two more on the Phase 7c
    # branch -- and **neither `chain.py` nor `jobs.py` calls `contain()`**.
    #
    # **No escape was achieved, and that is exactly why this is here.** The `dsmx_`/`annual_`
    # prefix absorbs the leading segment of a traversal, so `../../etc/passwd` becomes
    # `dsmx_../../etc/passwd` -- one directory shallower than intended and, on this repo's layout,
    # a write that fails ENOENT. That is the accident `server.py`'s own containment note already
    # refuses to rely on: *"neither of those is a control"*. A prefix is not a control, a failing
    # write is not a control, and the next path this leg constructs may have neither property.
    #
    # `is_legal_name` is the one rule (`rad_mcp.names`, ruling R61) every other name-taking tool
    # already applies. `grid` names a sensor grid the project registered under exactly that rule,
    # so nothing legal is refused: a `grid` this check rejects could not have been created.
    # **`isinstance(grid, str)` first**, because a *missing* `grid` is a different refusal with a
    # different message: `refuse_missing` inside `_dispatch` names the absent argument, and running
    # a charset check over `None` here would replace "you did not pass grid" with a TypeError.
    if isinstance(grid, str) and not is_legal_name(grid):
        return _refuse(
            name_refusal_reason(grid, kind="sensor grid", examples="floor, level_2, open-plan"),
            field="grid",
        )

    if metrics is not None and not isinstance(metrics, list):
        return _refuse(
            f"metrics must be a list of metric names, not {type(metrics).__name__}. Choose from: "
            f"{', '.join(_METRICS)}.",
            field="metrics",
        )
    if metrics is not None and not metrics:
        return _refuse(
            "metrics is empty. run_annual infers the method from what you are measuring, so it "
            f"needs at least one metric. Choose from: {', '.join(_METRICS)}.",
            field="metrics",
        )
    if metrics is not None:
        unknown = [m for m in metrics if m not in _METRICS]
        if unknown:
            # R815: a withdrawn name is still unknown, but a saved call naming it is told why and
            # which edit makes it run.
            withdrawn = "".join(
                f" {_WITHDRAWN_METRICS[m]}" for m in dict.fromkeys(unknown) if m in _WITHDRAWN_METRICS
            )
            return _refuse(
                f"unknown metric(s) {unknown}. Choose from: {', '.join(_METRICS)}.{withdrawn}",
                field="metrics",
            )

    # **The `METRIC_NOT_COMPUTED_YET` gate that stood here was deleted by R815**, with
    # `_UNCOMPUTED_METRICS` and its last member, `annual_dgp`, which is now refused above as a
    # withdrawn metric.
    if method not in ("auto", "2phase", "3phase", "5phase"):
        return _refuse(
            f"method={method!r} is not one of auto, 2phase, 3phase, 5phase.", field="method"
        )

    # **sda without ase -- §11's own refusal, reachable again since R101, Phase 7b Task 15.**
    # "Reporting sDA without ASE" is an LM-83 anti-pattern: "Any area reporting sDA must also report
    # ASE" (ch04). Before Task 15, `sda` alone was in `_UNCOMPUTED_METRICS` (deleted by R815) and
    # never deferred, so `metrics=['sda']` alone already refused with `METRIC_NOT_COMPUTED_YET`
    # (retired by R815) above this line -- there was no `metrics`
    # value carrying `sda` without `ase` that still reached this check. Task 15 removed `sda` from
    # that set, so `metrics=['sda']` now falls all the way through to here unrefused by anything
    # above it, and this check is what answers. (`metrics=['sda', 'ase']` still passes through
    # untouched -- it satisfies the pairing rule and was never this check's business.)
    #
    # **`SDA_REPORTED_ALONE`, not `ARGUMENT_OUT_OF_RANGE`.** `results(action="compliance")` already
    # allocates and emits this code for the identical LM-83 §2.4 rule on its own `metrics` argument
    # (`results_tool._refuse_sda_alone`); reusing it here rather than the generic argument code says
    # the same fact the same way from both tools that can be asked for `sda` alone, so a client
    # branching on the code gets one answer regardless of which tool told it.
    if metrics is not None and "sda" in metrics and "ase" not in metrics:
        clause = (
            section("reporting", edition) if edition == LM83_22 else "LM-83 ch04 section 2.4"
        )
        message = (
            f"metrics requests 'sda' without 'ase'. {clause}: annual values shall be "
            "reported for both sDA and ASE, together or not at all -- there is no legitimate "
            "reading in which sDA is published without ASE, because sDA alone reports the daylight "
            "a space gets and says nothing about the direct sun it also gets. Pass "
            "metrics=['sda', 'ase'], or drop 'sda'."
        )
        return refuse(
            message,
            [
                *(advisories or []),
                Advisory(
                    code="SDA_REPORTED_ALONE", severity="error", field="metrics", message=message
                ),
            ],
        )

    if north is not None and not isinstance(north, int | float):
        return _refuse(
            f"north must be a number in degrees east of north, not {type(north).__name__}.",
            field="north",
        )
    if north is not None and isinstance(north, bool):
        # `bool` is an `int` subclass, so the check above admits `True`. Ruling R40 refused exactly
        # this coercion at the schema boundary for `quality`; here the type is `float` and the
        # in-process guard is reachable, so it is made rather than documented as impossible.
        return _refuse("north must be a number, not a boolean.", field="north")

    # **History: this used to also carry a value pin, and it is gone rather than widened here.**
    # R44(1) refused every `sky_subdivision != 4` on the 2-phase path because `DC_RECEIVER_TEXT`
    # hardcoded `h=r4` -> `NCOLS=2306`, and a mismatched sky matrix exited `dctimestep` at 1 with an
    # opaque `fatal - unexpected column count in header` from inside a job. Phase 6b Task 1 could not
    # keep that pin here, because whether it even *applies* depends on the resolved method (2-phase
    # vs 3-phase), and only `_dispatch` -- below this function -- has read the manifest to know which:
    # `method="auto"` resolves per-project, and `_validate_arguments` runs before any project is
    # opened. So the value pin moved to `_dispatch`, against the *resolved* inference rather than an
    # approximation of it.
    #
    # **Phase 7a Task 5 deleted that moved refusal rather than adjusting where it lived.**
    # `rad_engine.annual.dc_receiver_text(mf)` builds the 2-phase receiver at whatever `mf` the
    # resolved subdivision is, so the fact the old pin protected -- "the receiver only agrees with a
    # `-m 4` sky" -- is no longer true of either method, and there is nothing left to refuse in
    # `_dispatch` either. What both methods still refuse is an `mf` this project has never measured
    # `rfluxmtx`/`gendaymtx` against, and that refusal lives where its own data lives:
    # `dc_receiver_text`'s `SUPPORTED_SUBDIVISIONS` guard for 2-phase, `rad_engine.matrices.
    # reinhart_bins` for 3-phase -- neither of which this function can read any earlier than
    # `_dispatch` can, for the identical "no project opened yet" reason the old value pin could not
    # live here.
    #
    # **Only the type check remains here**, because it alone is readable from the argument alone, is
    # true on every method, and needs no project -- the same split Phase 6b Task 1 drew, with the
    # other half now deleted instead of relocated.
    if sky_subdivision is not None and (
        isinstance(sky_subdivision, bool) or not isinstance(sky_subdivision, int)
    ):
        return _refuse(
            f"sky_subdivision must be an integer (gendaymtx -m), not "
            f"{type(sky_subdivision).__name__}.",
            field="sky_subdivision",
        )

    # **The value check belongs here too, and the whole-branch review is why.** The paragraph above
    # says the content check "needs a project" and lives further down -- true of the *method*, and
    # false of this one argument. `dc_receiver_text` (2-phase) and `rad_engine.matrices.
    # reinhart_bins` (3-phase and 5-phase) are measured at the **same** four subdivisions, so the
    # admissible set is one set, readable from the argument alone, on every method.
    #
    # It was not checked that way. The shaped refusal lived only around `dc_receiver_text`, on the
    # `target is None` branch; a 3-phase or 5-phase call reaches `three_phase_shape_check`/
    # `five_phase_shape_check` first, and `reinhart_bins`' bare `ValueError` -- plus
    # `suns_scene_text -> sun_count`'s, on the 5-phase path -- is caught by no `except` clause in
    # `run_annual`'s handler chain. It escaped uncoded: check 0.4 and check 0.5 violated together,
    # the identical shape `resolve_quality`'s own `ValueError` had a few lines below. It failed
    # closed (nothing was submitted), so this is hygiene, not a breach.
    #
    # `dc_receiver_text`'s own `try/except ValueError` stays where it is. It is not dead: that
    # function is a public engine entry point with its own callers and its own tests, and its
    # message says more about *why* the set is what it is than this refusal can. Two guards on one
    # value is not two paths for it to drift -- both read `SUPPORTED_SUBDIVISIONS`, one object.
    if sky_subdivision is not None and sky_subdivision not in SUPPORTED_SUBDIVISIONS:
        return _refuse(
            f"sky_subdivision={sky_subdivision} is not one of "
            f"{sorted(SUPPORTED_SUBDIVISIONS)}, the Reinhart subdivisions this project has "
            "measured its receiver and its Reinhart bin counts against (gendaymtx -m; "
            "rad_engine.annual.SUPPORTED_SUBDIVISIONS). reinhart.cal itself would compute a bin "
            "count for other values, but this server has not measured a run built at one.",
            field="sky_subdivision",
        )

    if not isinstance(quality, str | dict):
        return _refuse(
            f"quality must be a preset name or a dict of flag overrides, not "
            f"{type(quality).__name__}.",
            field="quality",
        )

    if isinstance(quality, dict) and "av" in quality:
        # R722: the code the other four quality-taking tools give (engine invariant 5), which
        # `rad_engine.rtrace.resolve_quality`'s own message already claims happens "at the tool layer".
        # `advisories` (the METHOD_INFERRED context, finding I2) is carried the same way
        # `_refuse_argument` carries it -- this refusal predates that helper's fixed `field`, so it
        # cannot reuse it, but must not drop the inference either.
        message = (
            f"quality override -av {quality['av']!r} is refused: the ambient value is a constant "
            "substituted for the indirect calculation, so a non-zero -av adds light that is not in "
            "the scene. docs/tool-surface.md engine invariant 5 requires -av to be zero on every "
            "quantitative run. Remove -av from the quality dict."
        )
        return refuse(
            message,
            [
                *(advisories or []),
                Advisory(code="AMBIENT_VALUE_SET", severity="error", field="quality", message=message),
            ],
        )

    # **Correction, controller ruling R322 fix round 1: this guard does NOT prevent a live
    # `ZeroDivisionError`, and the comment that used to stand here claimed it did.**
    # `rad_engine.rtrace._coerce_override`'s own `_ZERO_IS_LEGAL = {"ab", "as"}` does **not**
    # include `"ar"`, so `resolve_quality({"ar": 0})` already raises `ValueError` at the engine
    # layer -- verified directly, not assumed -- and `_dispatch`'s own pre-existing, unchanged
    # `try: resolve_quality(quality) except ValueError: return _refuse(...)` a few lines below
    # already turns that into a coded refusal (the same generic path
    # `TestAnInvalidQualityIsACodedRefusalNotAToolError` covers for four other malformed contents).
    # So `-ar 0` was already refused before Task 5, just with the engine's generic wording
    # ("it must be at least 1..."). This guard is kept anyway because its message is **more
    # specific** -- it names the actual formula (`Rmin = maxSize x aa / ar`) and the field this run
    # cannot report without a positive `-ar` -- and
    # `TestTheArZeroGuardIsDistinctFromTheGenericEngineRefusal` is the test that proves the two
    # messages really differ (deleting this block makes that test fail with the generic wording,
    # not pass vacuously). It is a strict improvement in message clarity, not a crash fix.
    if isinstance(quality, dict) and "ar" in quality:
        try:
            resolution = float(quality["ar"])
        except (TypeError, ValueError):
            resolution = None  # resolve_quality below owns the refusal for a non-numeric value
        if resolution == 0:
            return _refuse(
                "quality override -ar 0 is refused: the ambient resolution divides the ambient "
                "interpolation radius (Rmin = maxSize x aa / ar, radiance-engine-overview.md), so "
                "0 has no meaning and this run cannot report `ambient_interpolation_radius_m` at "
                "all. Pass a positive -ar (the presets use 128 to 1024), or use the 'reference' "
                "preset's -aa 0.0 if the intent is to disable ambient interpolation.",
                field="quality",
            )

    # **The type check above is not the content check**, and for one commit this function stopped
    # there. `resolve_quality` raises `ValueError` for four distinct contents that are all correctly
    # typed -- an unknown preset name, an unknown `base`, a non-string `base`, and an override key
    # that is not one of the five ambient flags -- and no `except ValueError` existed anywhere in
    # this module, so all four escaped `_dispatch` uncoded. Reproduced at the wire:
    # `quality="NOT_A_PRESET"` surfaced as a bare `ToolError` string with no `structuredContent`,
    # no advisory and no `refused` field, which is **check 0.4** (a refusal must be a tool execution
    # error carrying `isError` and a coded payload) and **check 0.5** (a declared `outputSchema`
    # with no conforming result) violated together. Two of the four -- non-string `base` and a bad
    # override key -- reach `ValueError` only through the guard a commit on this branch added to
    # `rad_engine.rtrace.resolve_quality`, so this branch widened the hole it is closing.
    #
    # Resolving here rather than trusting `_dispatch`'s own `resolve_quality(quality)` is what puts
    # the refusal **above the `dry_run` branch** (R39): `_validate_arguments` runs on both paths, so
    # `dry_run=True` refuses identically instead of a caller having to pay for a trace to learn the
    # preset name was wrong. The result is discarded -- `_dispatch` resolves again for its own use,
    # and `resolve_quality` is pure, so the second call cannot disagree with this one.
    #
    # The precedent is `point_in_time_tool._validate_quality`, which wraps the same call in the same
    # `try/except ValueError` -> `_refuse_argument(field="quality")`. `ARGUMENT_OUT_OF_RANGE` is
    # already the register's general code for a refused argument; nothing is invented here.
    try:
        resolve_quality(quality)
    except ValueError as exc:
        return _refuse(str(exc), field="quality")

    # `blind_control` is window-group vocabulary (§11: lm83/none/fixed are about how a group's
    # apertures are operated; `lm83` selects the hourly blind schedule, which this server derives
    # for the whole grid and not per group -- R116). **All three are admitted as of Phase 7c Task 11** -- see
    # `_BLIND_CONTROL_ACCEPTED` for the history and for why the `lm83` refusal was deleted rather
    # than re-worded onto a later phase. What survives here is the *unrecognised-value* refusal, and
    # it survives for a measured reason rather than by inertia.
    #
    # **An unknown value is refused here rather than at the schema boundary**, which is the opposite
    # disposition from `rebuild` a few lines below, and the difference was measured rather than
    # assumed: `rebuild: list[str] | None` publishes an enforcing schema, so a malformed shape never
    # reaches this function, while `blind_control: str | None` publishes a bare string
    # (`{"anyOf": [{"type": "string"}, {"type": "null"}]}` -- read off `mcp.list_tools()` on this
    # branch). §11's own signature is `Literal["lm83","none","fixed"] | None`, so the vocabulary is
    # published even though the wire type does not enforce it, and an unrecognised value must not be
    # silently ignored: a caller who typed `blind_control="auto"` and got a run would believe a blind
    # policy was applied when none was. Refused rather than ignored throughout, the same posture
    # `run_point_in_time` takes for `window_group_states`.
    #
    # **Above the `dry_run` branch** (R39), as every refusal in this function is: the value is
    # rejected on its face, before any project is read.
    if blind_control is not None and blind_control not in _BLIND_CONTROL_ACCEPTED:
        return _refuse(
            f"blind_control={blind_control!r} is not one of "
            f"{', '.join(sorted(_BLIND_CONTROL_ACCEPTED))}. 'none' and 'fixed' are single-state "
            "runs; 'lm83' additionally plans the pre-blind direct-sun run LM-83's hourly blind "
            "schedule is read from (results(action='blind_schedule')) -- one series for the whole "
            "grid, not one per window group. Omit the argument to run each group's single bound "
            "state.",
            field="blind_control",
        )

    # **`rebuild` is honoured as of Phase 6b Task 8, and this function no longer takes it at all.**
    #
    # The refusal it used to carry is gone rather than narrowed. It said *"this phase reuses no
    # matrices, so every artifact is rebuilt on every call and there is nothing for rebuild to force
    # past"*, which was true of every phase up to this one and is false now: `chain.reuse_decision` calls
    # `rad_engine.project.is_reusable` for every step carrying an artifact, and an id named in
    # `rebuild` is never flagged reusable whatever that predicate says. A refusal whose stated reason
    # has stopped being true is a defect in its own right -- the same class §9 records for an
    # advisory whose definition is untrue of its condition -- so it is the *reason* that decided
    # this, not merely the arrival of the feature.
    #
    # **Nothing replaces it here, which is a decision rather than an omission.** Two candidate
    # refusals were written in this function and both were withdrawn on measurement:
    #
    # - **A shape check** ("rebuild must be a list of strings") is **unreachable from the wire**, and
    #   that was measured rather than reasoned: `rebuild: list[str] | None` is the published
    #   signature, so the SDK validates it at the schema boundary and `rebuild="dsmx:grid"` /
    #   `rebuild=[7]` are rejected as `ToolError` type errors before this function is entered. Both
    #   were added to `_REFUSALS` and both went red for that reason. An in-process guard below an
    #   enforcing schema is a branch no caller can reach, and a sweep row that cannot go red on
    #   unfixed code proves nothing. This is ruling R40's disposition reached from the other
    #   direction: there the refusal had to move *into* the published type because lax coercion let
    #   `true` through as `1.0`; here the published type already refuses, so nothing is owed below
    #   it. `run_annual` therefore has no `rebuild` argument refusal, and `_REFUSALS` names none.
    # - **An unknown-id check** would make `rebuild` depend on the project, which this function
    #   cannot read -- it runs above the `dry_run` branch *and* above `open_project`. It is also
    #   wrong on the merits: an id naming no step of this run asks for something already true, and
    #   refusing it would refuse a caller who passes one id set across a 2-phase and a 3-phase
    #   project on whichever plan does not contain it.
    #
    # So `rebuild` travels from `run_annual` straight to `_dispatch`, and is consumed in
    # `chain.reuse_decision` where the ids meet the artifacts they name.
    return None


# --- the inference, and the methods this phase can build ----------------------------------------


def _window_groups(
    loaded: LoadedProject, env: RadianceEnv, cwd: Path, *, project: str | None = None,
) -> list[WindowGroup]:
    """Every `window_group` input of this project, with its states scanned and its geometry
    re-derived.

    **The polygons come back through `rad_engine.scene.read_scene` over the *geometry* inputs'
    files, not over the window group's own** -- and that is not an accident of convenience, it is
    the only route that reaches the same `Aperture` `window_group(action="define")` reached.
    `window_group` registers its input with *the exact same `files` the owning geometry input
    already carries* (that module's "Registering a group is registering an edge, not a new file"),
    then resolves each `aperture` modifier name against every polygon any registered geometry input
    uses. Resolving against a narrower file set here would find a different -- possibly empty --
    member list for the same group name, and an aperture derived from a subset of its own members
    has a different normal and a different area. One route, one answer (ruling R30).

    The member modifiers are read back from the group's own recorded `registered_by.arguments`,
    which is where `window_group(action='define')` wrote the caller's `aperture` argument verbatim.
    Nothing is guessed: a group whose record carries no such call, or whose names resolve to no
    polygon, is **refused** rather than derived from a partial member list -- see `_group_members`
    and `StaleWindowGroupError`. It was *skipped* until Phase 6b Task 6; ruling 4 reversed that,
    because a skipped BSDF-bound group silently drops the inference from 3-phase to 2-phase now
    that 3-phase is a method this server actually builds.

    Raises `rad_engine.scene.SceneFileNotFoundError` when a registered geometry file has been
    deleted since it was registered, `rad_core.apertures.ApertureError` when the members no longer
    form one coplanar aperture, and `StaleWindowGroupError` when they resolve to nothing at all.
    All three are shaped into coded refusals by `run_annual`'s handler chain rather than escaping
    as a traceback.

    **The automated mark and the bound schedule (R869, R870)** are read off the manifest here too:
    `automated` is `define`'s recorded `automated=True` (`chain.automated_group_names`' rule, a
    record with no `registered_by` reading as unmarked) and `automated_schedule` the group's
    `automated_schedule_<g>` record (`chain.automated_schedules`, through `_bound_schedules`); the
    plan-time read (`_refuse_unusable_schedules`) checks its rows on the one run that reads the
    file. A schedule record naming no one file (`AutomatedScheduleRecordError`) is
    not raised from here: that group reads as having no schedule bound, every other group's is read
    as usual, and `_refuse_unusable_schedules` refuses the record as `AUTOMATED_SCHEDULE_UNUSABLE`
    on a run that seals a schedule -- the one run that would read it. A run that reads no schedule
    is not refused over one.
    """
    records = [record for record in loaded.manifest.inputs if record.kind == "window_group"]
    if not records:
        return []

    by_modifier = _polygons_by_modifier(loaded, env, cwd)
    states_by_group = _states_by_group(loaded)
    marked = frozenset(automated_group_names(loaded))
    schedules, _ = _bound_schedules(loaded)

    # Final fix F, fix round 4 (K1): a group a sealed artifact names (a bound BSDF's `tmx`)
    # cannot be removed -- `window_group(action='remove')` refuses ARTIFACT_EDGES_INCOMPLETE.
    held_inputs = {edge for artifact in loaded.manifest.artifacts for edge in artifact.built_from}
    groups: list[WindowGroup] = []
    for record in records:
        name = (
            record.id[len(_WINDOW_GROUP_PREFIX):]
            if record.id.startswith(_WINDOW_GROUP_PREFIX)
            else record.id
        )
        members = _group_members(
            record, by_modifier, project=project, name=name, held=record.id in held_inputs,
        )
        groups.append(
            WindowGroup(
                name=name,
                input_id=record.id,
                files=tuple(ref.path for ref in record.files),
                states=states_by_group.get(name, ()),
                aperture=derive(members),
                # `record.role` is the manifest's own, generic field (`rad_core.manifest.
                # InputRecord.role`) -- `window_group(action="define")`'s `closing_role` argument
                # is what wrote it (ruling R133; see that module and `chain.WindowGroup`'s own
                # comment for why the wire argument is not named `role`).
                closing_role=record.role,
                automated=name in marked,
                automated_schedule=schedules.get(name),
            )
        )
    return groups


def _room_bounding_box(state: "_ValidatedProject", env: RadianceEnv) -> BoundingBox | None:
    """This project's own registered geometry, as one axis-aligned box -- `AMBIENT_RADIUS_COARSE`'s
    comparison target (docs/tool-surface.md's Advisory code register). **Never the octree's own
    bounding cube**: that cube is a single enclosing edge over the *whole* compiled scene, ground
    plane included -- the office fixture's is measured at ~42 m
    (`rad_engine.rtrace`'s own `ambient_interpolation_radius_m` docstring) against a 3 m-tall room --
    so comparing an interior interpolation radius against it would essentially never fire. Task 5's
    brief gives this same reasoning, and `rad_mcp.glare_tool` carries its own copy of it for the
    identical advisory.

    Reads `state.scene_files` -- the identical file list that just compiled into the octree captured
    a few lines above this call's own site in `_dispatch`, never a second, possibly-different
    geometry-input enumeration re-read off the manifest (R30) -- through `rad_engine.scene.
    read_scene`, the same route `_polygons_by_modifier` below already takes for aperture derivation.
    This is a **second** `xform` spawn beyond that one on a project that also has a window group
    registered (the two reads are not merged: `_polygons_by_modifier` runs only from `_window_groups`,
    which itself runs only when at least one `window_group` input exists, while this runs on every
    real call), bounded by `OCTREE_BUILD_TIMEOUT_S` like every other metadata spawn `run_annual`
    makes.

    `None` only when the read yields no polygon at all -- unreachable in practice on any call that
    reaches this function, since `_validate_project`'s own `_scene_files` (shared with
    `point_in_time_tool`) already refused a project with no geometry registered long before
    `_dispatch` got this far, and the octree captured above already proved `state.scene_files`
    compiles to a non-empty scene (`OCTREE_EMPTY` raises first otherwise). Kept as a defensive
    `None` rather than assumed non-`None`, per ruling R313: a comparison this function cannot
    honestly make must raise nothing, never substitute the octree cube for it.
    """
    scene = read_scene(env, state.scene_files, cwd=state.paths.root, timeout=OCTREE_BUILD_TIMEOUT_S)
    if not scene.polygons:
        return None
    return bounding_box(scene.polygons)


def _ambient_radius_coarse_advisory(radius_m: float, smallest_dimension_m: float) -> Advisory:
    """`AMBIENT_RADIUS_COARSE` (warn) -- docs/tool-surface.md's Advisory code register: fires when
    the ambient interpolation radius exceeds one twentieth of this project's smallest room
    dimension. The threshold is computed once, at the call site, and passed in rather than
    recomputed here, so the message and the `if` that decided to build this advisory can never
    disagree about which number crossed which line.
    """
    return Advisory(
        code="AMBIENT_RADIUS_COARSE",
        severity="warn",
        field="quality",
        message=(
            f"the ambient interpolation radius ({radius_m:.4g} m) exceeds one twentieth of this "
            f"project's smallest room dimension ({smallest_dimension_m:.4g} m -> "
            f"{smallest_dimension_m / 20:.4g} m). Cached irradiance is being interpolated over a "
            "sphere large relative to the room, which can blur real gradients near walls and "
            "windows. Raise -ar (ambient resolution) or lower -aa (ambient accuracy) for this "
            "quality preset (docs/tool-surface.md #10)."
        ),
    )


def _polygons_by_modifier(
    loaded: LoadedProject, env: RadianceEnv, cwd: Path
) -> dict[str, list[Polygon]]:
    """Every polygon any registered `geometry` input uses, indexed by its modifier.

    `window_group_tool._registered_polygons`' route, and deliberately the same one: read every
    geometry input's files together in **one** `read_scene` call, because an aperture's members can
    legitimately span more than one registered input and a per-input read would split a legitimate
    group. There is no "what does this file declare" accessor at the engine layer, only "what do its
    polygons use" -- so `Polygon.modifier` off a parsed scene is the only thing either tool can
    index on.
    """
    files: list[str] = []
    for record in loaded.manifest.inputs:
        if record.kind == "geometry":
            files.extend(ref.path for ref in record.files)
    if not files:
        return {}
    by_modifier: dict[str, list[Polygon]] = {}
    for polygon in read_scene(env, files, cwd=cwd, timeout=OCTREE_BUILD_TIMEOUT_S).polygons:
        by_modifier.setdefault(polygon.modifier, []).append(polygon)
    return by_modifier


class StaleWindowGroupError(ProjectError):
    """A registered window group whose members no longer resolve to any polygon.

    **Ruling 4 of Phase 6b Task 6, and the reversal of Task 5's disposition.** Task 5 *skipped* such
    a group -- see `_group_members` -- and that was correct only under the premise this task
    removes. While `_BUILDABLE_METHODS` was `{"2phase"}`, a skipped group changed nothing a caller
    could observe: every inferred 3-phase run was refused a few lines later regardless, so the
    conservative direction really was "leave `infer` on the rule it would have reached anyway".

    **Once 3-phase is buildable that premise is false, and the skip becomes a silent wrong number.**
    Measured directly: `infer(["da"], [WindowGroupFacts(name="main", states=(StateKind.BSDF,))],
    "auto")` answers `3phase`, and `infer(["da"], [], "auto")` -- the same call with the group
    skipped -- answers `2phase`. So a project whose BSDF-bound group has gone stale is now
    **built**, as 2-phase, under a `method.chosen` of `"2phase"` that looks entirely reasonable, and
    the resulting `.ill` is an annual illuminance computed with no fenestration transmission at all.
    That is precisely the failure this module's own last docstring section names: *"a method
    reported but not built is a wrong number waiting to be read"*, arriving from the other
    direction -- a method built but not the one the building needs.

    The old reasoning's remaining half does not survive either. It argued that refusing would turn
    "one group in this project is stale" into "this project cannot run an annual study, including
    the 2-phase path that never reads the group at all". But a project that has *defined a window
    group* is not a project doing 2-phase work by intent: the group is a declaration that this
    building's fenestration matters, and answering it with a 2-phase number is the wrong answer to
    the question asked rather than a graceful degradation of it. A caller who genuinely wants the
    2-phase number can remove the group -- one call, `window_group(action="remove")` -- and get it
    without ambiguity.

    **No code is invented.** `SCENE_FILE_NOT_FOUND`, `APERTURE_NOT_COPLANAR` and
    `APERTURE_NORMAL_AMBIGUOUS` are already allocated and already emitted from this path for the
    other three ways a group can fail to re-derive (see the module docstring's "What reading the
    groups costs"). This is the fourth way and it is the same class of fact -- registered geometry
    that moved out from under a group -- so it carries `SCENE_FILE_NOT_FOUND`, the code ruling R29
    allocated for registered scene content that is no longer there. `ProjectError`, so
    `run_annual`'s existing handler chain shapes it through `refuse_engine_error` with the
    inference context attached, exactly as every other project-state refusal here is.
    """

    code = "SCENE_FILE_NOT_FOUND"


def _group_members(
    record: Any, by_modifier: dict[str, list[Polygon]], *, project: str | None = None,
    name: str | None = None, held: bool = False,
) -> list[Polygon]:
    """The polygons one window-group record names. Raises rather than skipping when they are gone.

    **Ruling 4 -- this function returned `None` for "cannot resolve" until Phase 6b Task 6.** See
    `StaleWindowGroupError` for why the skip was safe while `_BUILDABLE_METHODS` was `{"2phase"}`
    and why it stops being safe the moment 3-phase is buildable: a skipped BSDF-bound group flips
    the inference from 3-phase to 2-phase with nothing on the wire to say so.

    Two conditions reach the refusal, and they are kept as one because a caller repairs them the
    same way -- by re-registering the geometry the group names, or by removing the group:

    - **a record with no `registered_by` call** to read the modifier names off. This can only arise
      from a hand-edited manifest, and `window_group(action='define')` writes the call verbatim on
      every legitimate definition.
    - **names that now resolve to no polygon at all**, which means the registered geometry moved
      out from under a group `window_group(action='define')` refused to register in that state
      (that tool's own "a group pointing at a modifier that does not exist produces an all-zero V
      matrix, and an all-zero matrix multiplies to a plausible zero").

    Deriving anyway is still not an option and never was: `derive([])` raises
    `ApertureNormalAmbiguousError`, which would report "polygon winding gives no consistent normal"
    about a group whose polygons are simply absent -- a true-sounding message about the wrong fact.
    The refusal below names what actually happened.

    Final fix F, fix round 4 (K1): its `window_group(action='remove', ...)` is spelled whole from
    `project` and the group's `name` (the record's id less its prefix), as `run_annual` reads them;
    it ended `name=...)`. A caller that passes neither (a test calling this directly) gets the
    group's name and no project. Measured through the real tool, that removal is refused
    (ARTIFACT_EDGES_INCOMPLETE) while a sealed artifact names the group -- a bound BSDF's
    `tmx:<group>:<state>` does, and no tool removes an artifact -- so where `held`, the refusal
    says the group cannot be dropped instead of offering a call that would be refused.
    """
    call = record.registered_by
    names: tuple[str, ...] = ()
    if call is not None:
        raw = call.arguments.get("aperture")
        names = (raw,) if isinstance(raw, str) else tuple(raw or ())
    members: list[Polygon] = []
    for modifier in names:
        members.extend(by_modifier.get(modifier, []))
    if members:
        return members
    group = name if name is not None else record.id.removeprefix(_WINDOW_GROUP_PREFIX)
    remove = (
        "window_group(action='remove', "
        + (f"project={project!r}, " if project is not None else "")
        + f"name={group!r})"
    )
    raise StaleWindowGroupError(
        f"window group {record.id!r} names "
        + (
            f"aperture modifier(s) {list(names)}, none of which is used by any polygon in this "
            "project's registered geometry"
            if names
            else "no aperture modifiers at all -- its manifest record carries no "
            "window_group(action='define') call to read them from, which a hand-edited manifest is "
            "the only way to produce"
        )
        + ". The group cannot be re-derived, so this server cannot tell whether this building's "
        "fenestration needs the 3-phase chain. Refused rather than skipped: a skipped group leaves "
        "the method inference on 2-phase, and an annual result computed with no fenestration "
        "transmission is a plausible number that answers a different question than the one asked. "
        + (
            "Re-register the geometry those modifiers live in with geometry(action='add'). The "
            "group cannot be dropped instead while the BSDF matrices bound to it name it: "
            f"{remove} refuses (ARTIFACT_EDGES_INCOMPLETE)."
            if held else
            "Re-register the geometry those modifiers live in with geometry(action='add'), or "
            f"drop the group with {remove} if it no longer describes this building."
        )
    )


def _group_facts(groups: list[WindowGroup]) -> list[WindowGroupFacts]:
    """The fenestration facts `infer`'s rules 2-4 read, from the project's real window groups.

    **`infer` takes facts, not opinions** (`rad_core.method`'s Vocabulary note), and the fact it
    needs per state is a `StateKind`. This layer is the one that can derive it -- `window_group`'s
    published `states` is `dict[str, str]`, a state name mapped to a BSDF XML path, with no notion
    of what kind of device the state *is*.

    **Every bound state classifies as `StateKind.BSDF`, and the three other kinds are unreachable
    from this project's data rather than unimplemented:**

    - `SHADING_NON_SPECULAR` and `LIGHT_REDIRECTING` need the caller to have said "this state is a
      white louvre" or "this is a light shelf". §6's `states` argument carries a path and nothing
      else, so no such statement exists anywhere in the manifest. Classifying a bound XML as one of
      them would be this module guessing at the device behind the file -- the same invention
      rulings R60 and R63 refuse, and it would silently escalate the inference to 5-phase.
    - `NONE` is "no BSDF, no shading device", which is a group with **no** `tmx` artifacts at all.
      That group gets `states=()`, not `states=(NONE,)`: rule 3 counts `len(states) >= 2` and rule 4
      asks whether any state is a BSDF, and an empty tuple answers both correctly, while a
      one-element `NONE` tuple would answer rule 3's count question with a state the project does
      not have. `rad_core.method`'s own note that "a group with a single `StateKind.NONE` state is
      static, simple glazing" describes a caller who knows there is one state; here, an unbound
      group is a group whose states have not been declared.

    So: a defined group with no BSDF leaves `infer` on rule 5 (2-phase) exactly as an empty project
    does, and the first `assign_bsdf` call moves it to rule 4 (3-phase) without an edit here.
    """
    return [
        WindowGroupFacts(
            name=group.name,
            states=tuple(StateKind.BSDF for _ in group.states),
        )
        for group in groups
    ]


def _method_view(inference: Inference) -> MethodView:
    return MethodView(
        chosen=inference.method,
        requested=inference.requested,
        reasons=list(inference.reasons),
        equation=_EQUATION.get(inference.method, ""),
    )


def _inference_advisories(inference: Inference) -> list[Advisory]:
    """`infer`'s own `ArtifactWarning`s, on the wire.

    `METHOD_INFERRED` is always first and always present (`rad_core.method` guarantees the order and
    this module does not re-derive it), joined by `METHOD_OVERRIDE_UNSAFE` or
    `METHOD_OVERRIDE_WASTEFUL` when they apply. Built directly rather than through
    `advisories.to_wire`, which maps `rad_engine.advisories.EngineAdvisory`: `rad_core` sits below
    `rad_engine` and cannot construct one (CONTEXT.md, Layers), and `ArtifactWarning` already
    carries exactly the four fields the wire model needs -- the same route
    `runtime.refuse_engine_error` takes for `rad_core.grids.GridError`.
    """
    return [_advisory_of(w) for w in inference.warnings]


def _advisory_of(warning: ArtifactWarning) -> Advisory:
    return Advisory(
        code=warning.code,
        severity=warning.severity,
        field=warning.field,
        message=warning.message,
    )


# **R44(1)'s `sky_subdivision != 4` refusal (`_refuse_two_phase_sky_subdivision`) was deleted here,
# not narrowed, by Phase 7a Task 5.** Its premise -- that the 2-phase receiver was pinned to
# `h=r4` and could not be multiplied against any other sky subdivision -- is gone:
# `rad_engine.annual.dc_receiver_text(mf)` builds a correct receiver at whatever `mf` is asked for,
# and `_shape_check` (below) predicts both matrix headers from the identical resolved `subdivision`
# value, so the two cannot disagree by construction. What the deleted refusal protected against --
# `dctimestep` exiting 1 with `fatal - unexpected column count in header` on a mismatched pair --
# cannot occur once one `mf` builds both the receiver and drives `gendaymtx -m`: there is no second
# value anywhere in this call for the receiver and the sky matrix to disagree about. The residual
# risk is a *different* one, recorded rather than silently accepted -- see this module's own M1 note
# (module docstring) on the shared `receiver.rad` path under concurrent submits at different `mf`.
#
# What replaced it is `dc_receiver_text`'s own `SUPPORTED_SUBDIVISIONS` guard, applied to the
# 2-phase path just below `_shape_check` -- narrower than the old refusal (it admits 1/2/4/6, not
# only 4) and honest about what it actually protects: this project has only measured
# `rfluxmtx`/`gendaymtx` against those four subdivisions (docs/tool-surface.md verified fact 3).


def _ase_blind_geometry_advisory(
    metrics: list[str], *, edition: Lm83Edition = DEFAULT_LM83_EDITION
) -> Advisory | None:
    """`ASE_BLIND_GEOMETRY_UNDETECTED` -- the caveat that replaces `ASE_WITH_BLINDS_REFUSED`.

    **Owner ruling R228, 2026-08-31: `_refuse_ase_with_blinds` is retired as a refusal and replaced
    by this advisory.** What follows is the measurement that ruling rests on, kept here because a
    retired refusal leaves nothing else that records why it went.

    ## What the refusal claimed, and why the claim was false of this server

    It refused `ase` whenever any bound state's **name** read as non-open, on the premise that
    *"computing it through a deployed blind ... silently understates it"*. LM-83-12 sec 3.2.6 is
    the clause behind that premise, and it is real:
    ([ch04](../../skills/lighting-standards-compliance/chapters/ch04-ies-lm-83-sda-ase.md)) *"Blinds
    and shades are NOT deployed for the ASE analysis. Any operable blind or shade is left OPEN...
    Any fixed or static shading devices, such as overhangs or light shelves, should be included in
    the ASE model."*

    **But this server's ASE run is already exactly that, on every path and under every
    `blind_control`.** `rad_engine.octree.build_ase_octree` compiles *the registered scene, whole,
    plus the suns scene* -- `oconv -f <scene_files...> <suns_scene>`, every file keeping its own
    materials, **no aperture strip and no BSDF proxy**. A state bound by
    `window_group(action='assign_bsdf')` lands in `07_matrices/<name>_<state>.xml` and enters only
    the **T** matrix of the three-phase multiply; it is in no ASE octree this server compiles. So
    the bound state's name has no causal relationship to what the ASE run traces, and the refusal
    fired on projects whose ASE would have been computed correctly. It was a false positive under
    every `blind_control`, not only under `lm83`.

    ## What the caveat is for, and the half of it this server genuinely cannot see

    The hazard the refusal gestured at is real and it lives one layer away: **a blind modelled as
    ordinary scene geometry** -- slats written into a registered `.rad` file rather than bound as a
    BSDF state -- **is** in the ASE octree, because `build_ase_octree` takes the registered scene
    whole. Such a run genuinely understates ASE, and it publishes a number rather than an error.

    **This server cannot detect that, and the advisory says so rather than implying a check.**
    Nothing in the manifest records what a primitive *is*: a registered `.rad` carries geometry and
    modifier names, and an operable venetian, a fixed overhang and a bookshelf are the same kind of
    thing to every reader in this tree. The retired refusal could not detect it either -- it read
    `group.states`, so it refused exactly the projects that were safe and passed exactly the one
    that was not.

    So this fires on **every** run whose `metrics` name `ase`, unconditionally, and is keyed on
    nothing about the project. A gate on state names would be the retired refusal's own wrong
    signal wearing a lower severity, and a gate on "does the scene look like it has blinds in it"
    would be a claim this server has no data to make. `None` when `ase` is not requested, which is
    the only condition it has.

    ## "Every run" includes the refused ones, and did not until the whole-branch fix wave

    **Two call sites, one factory, and the second one is what makes the sentence above true**
    (owner ruling R253). `_dispatch` appends this for the run it returns itself;
    `_inference_context` attaches it to the list `run_annual`'s handler chain rebuilds every
    refusal from. Before that second site, a refusal *raised* out of `_dispatch` -- which is every
    refusal below the append, all of them -- was shaped from `context` and dropped this advisory
    silently. Measured at `7a18916`, `metrics=["sda","ase"]`: present on an accepted run, absent on
    a window group with no BSDF bound, on no window group at all, on `epw="nosuch"`, on
    `grid="nosuchgrid"` and on `method="3phase"`. The two lists are never merged, so nothing is
    emitted twice; `tests/test_annual_tool.py::TestTheAseCaveatRidesEveryExit` pins both halves.

    **`edition` is a call argument, not a project fact** (R864), so the rule above -- keyed on
    nothing about the project -- still holds. It chooses the clause the first and last sentences
    cite: LM-83-12 section 3.2.6 (this text, unchanged, is reachable only under LM-83-12), or
    LM-83-22 section 3.1's definition of direct sunlight, which "excludes the effect of any blinds
    or operable shading devices" and keeps exterior obstructions "including fixed shading".
    """
    if "ase" not in metrics:
        return None
    lm83_22 = edition == LM83_22
    clause = section("ase_definition", edition) if lm83_22 else "sec 3.2.6"
    return Advisory(
        code="ASE_BLIND_GEOMETRY_UNDETECTED",
        severity="warn",
        field="metrics",
        message=(
            (
                "ASE counts direct sunlight that \"excludes the effect of any blinds or operable "
                f"shading devices\" ({clause}: any operable blind or shade is left OPEN, while "
                "exterior obstructions, fixed shading included, stay in the model). "
                if lm83_22 else
                "ASE is defined \"before any operable blinds or shades are deployed\" (LM-83-12 "
                "sec 3.2.6: any operable blind or shade is left OPEN, while fixed or static "
                "shading such as overhangs and light shelves stays in the model). "
            )
            + "This run honours that for every "
            "blind bound as a BSDF state: the ASE octree is the registered scene plus the suns "
            "scene, with no aperture strip and no BSDF proxy, so a bound state enters the T matrix "
            "of the three-phase multiply and never the direct-sun trace -- whatever blind_control "
            "is set to. What this server CANNOT detect is an operable blind modelled as ordinary "
            "scene geometry: slats written into a registered .rad file are in the ASE octree like "
            "any other surface, the ASE is then computed through a deployed blind and is silently "
            "UNDERSTATED -- the half of the sDA/ASE pair that flatters the design. Nothing in the "
            "manifest records what a primitive is, so this cannot be checked and is not: if this "
            "project models an operable blind as geometry, remove it from the scene the ASE run "
            f"reads, or report the ASE as not conforming to {clause}. Fixed shading is required "
            "to stay."
        ),
    )


def _refuse_missing_direct_sun_binding(
    inference: Inference, loaded: LoadedProject, groups: list["WindowGroup"],
    advisories: list[Advisory],
) -> CallToolResult | None:
    """`BSDF_DIRECT_SUN_NOT_BOUND` -- Phase 7a Task 10's own run-level consequence of check 5.4
    clause 2.

    Task 10a taught `window_group(action='assign_bsdf')` a second binding, `role='direct_sun'`: the
    tensor-tree BSDF `Cds` -- the five-phase direct-sun coefficient matrix -- is built from. A group
    bound only at `role='matrix'` (the Klems file, for `T`) has nothing for `Cds` to read at all, and
    a run that reached that gap **inside a job** would fail with a bare missing-file error from deep
    in an `rcontrib`/`oconv` spawn -- exactly the "opaque `fatal -` error a caller pays for a trace to
    learn" R39 exists to prevent. This function is the refusal that answers instead, before any
    matrix is paid for.

    ## Where it sits

    It reads `inference.method` itself, the final answer: 5-phase is reached by three different
    rules (a direct-sun metric, a light-redirecting state, dynamic blinds with `sda`), and the
    refusal applies to whichever of them fired. `_dispatch` calls it after the manifest read and
    above `_refuse_unbuildable_method`'s second call, which is a backstop that answers `None` there
    (see `_dispatch`). Until R815 it also sat below the DGP-on-a-horizontal-opening refusal and above
    `METRIC_NOT_COMPUTED_YET`'s late call, both of which left with `annual_dgp`.

    **The message promises a working run again as of Phase 7a Task 14b, and the history of that
    sentence is worth keeping.** Task 14's own review (Important 6) removed a "before retrying this
    method" phrasing precisely because it was false when written: a caller who bound
    `role='direct_sun'` and retried landed on `FIVE_PHASE_OCTREE_NOT_WIRED`, a refusal with no remedy
    at all, so the message had to say -- truthfully, and less helpfully -- that clearing this refusal
    did not yet produce a result. Task 14b built the two octrees that refusal stood for and deleted
    the code, so binding is once again the whole repair, and the message says so. The rule the two
    revisions share is the one worth restating: a refusal may promise a remedy only while the remedy
    is true, and *"a wrong number wearing a right one's name"* has an exact analogue in a fix
    instruction that leads to another wall.

    ## What is checked, and what is deliberately not

    Every state a group has **sealed as `tmx`** (`group.states`, scanned off `tmx:<group>:<state>`
    artifact ids -- the only kind of bound state `_window_groups` can see) is checked for the
    matching `bsdf_<group>_<state>_direct_sun` input `role='direct_sun'` would have registered
    (Task 10a's own naming, `window_group_tool._assign_bsdf`). A group with **no** matrix binding at
    all contributes nothing here: rule 4 could not have inferred a BSDF-driven method from it either,
    so it is not part of "the fenestration this run would actually multiply" -- the same posture
    the retired `_refuse_ase_with_blinds` took for a group with `states=()` (owner ruling R228).

    ## Which records count as direct-sun bindings, and why it is not the id suffix

    **Read off `registered_by.arguments["role"]`, via `window_group_tool._bsdf_input_role` -- never
    `record.id.endswith("_direct_sun")`** (Task 10's review, Minor M2, fixed in Task 13). The two
    roles' input ids are not disjoint by construction: `rad_mcp.names.NAME_PATTERN` permits `_` in a
    state name, so a state legally named `<x>_direct_sun` bound at `role="matrix"` registers the
    identical string `role="direct_sun"` registers for state `<x>`
    (`window_group_tool._refuse_bsdf_input_id_collision`'s own docstring reproduces both orders). A
    suffix scan therefore reads that Klems record as a direct-sun binding, and state `<x>` drops out
    of `missing` -- the run is still refused, because `<x>_direct_sun` itself is unbound and appears,
    but the message names one of the two offending states instead of both, and the caller binds one
    file and is refused again.

    `_bsdf_input_role` is imported rather than restated, including its migration default: a `bsdf`
    input registered before the `role` argument existed carries no `"role"` key at all, and absence
    means `"matrix"` -- the only role that ever wrote one before Task 10a -- not "unowned". Reading
    absence as unowned here would silently exempt every pre-existing binding, which is the exact
    defect that function's own review round 2 closed on the other side of this pair.

    ## A project with more than one window group is not this function's to refuse

    **Returns `None` above two groups, deliberately, as of the whole-branch review's Minor 3.** This
    function ran over *every* group and fired before `chain.three_phase_target`'s own single-group
    refusal, so a two-group project was told to go and bind two tensor trees -- and, having bound
    them, was told the run could not happen at all. That breaks this function's own rule three
    paragraphs up: *a refusal may promise a remedy only while the remedy is true*. With two groups the
    remedy is false, so the refusal that owns the group count answers instead.

    It is a deferral and not a hole: `needs_target` is true for every `5phase` inference, so
    `three_phase_target` runs on the same path a few lines below `_dispatch`'s call to this function
    and refuses the multi-group project outright. Nothing reaches an octree build unrefused.

    Returns `None` when the inferred method is not `5phase` (every 2-phase and 3-phase run reads
    nothing this checks), when the project has more than one window group, or when every
    matrix-bound state also carries its own direct-sun binding.
    """
    if inference.method != "5phase":
        return None
    if len(groups) > 1:
        # See the docstring: `three_phase_target` owns the group count, and telling a caller to bind
        # N tensor trees for a run that cannot happen is a fix instruction leading to another wall.
        return None
    direct_sun_ids = {
        record.id for record in loaded.manifest.inputs
        if record.kind == "bsdf" and _bsdf_input_role(record) == "direct_sun"
    }
    missing = [
        (group.name, state)
        for group in groups
        for state in group.states
        if f"bsdf_{group.name}_{state}_direct_sun" not in direct_sun_ids
    ]
    if not missing:
        return None
    named = ", ".join(f"{name!r} in state {state!r}" for name, state in missing)
    message = (
        f"the inferred method is '5phase', whose direct-sun term (Cds) is built from a tensor-tree "
        f"BSDF bound at role='direct_sun' -- and {named} carries no such binding, only the Klems "
        "file bound at role='matrix' (for T). Bind one with window_group(action='assign_bsdf', "
        "name=..., states={...}, role='direct_sun') and re-run: the tensor tree is what the suns "
        "octree's BSDF proxy surface is baked from, and with it bound this server builds the whole "
        "five-phase chain. Drop the metric or state driving the inference to 5-phase if you would "
        "rather have a 3-phase result instead."
    )
    return refuse(
        message,
        [
            *advisories,
            Advisory(
                code="BSDF_DIRECT_SUN_NOT_BOUND", severity="error", field="metrics", message=message,
            ),
        ],
    )


def _refuse_drifted_direct_sun_bsdf(
    inference: Inference, loaded: LoadedProject, advisories: list[Advisory],
) -> CallToolResult | None:
    """`BSDF_DIRECT_SUN_DRIFTED` -- the re-digest `docs/tool-surface.md` [§6](#6-window_group)
    promised to Phase 7a Task 13, which is this one.

    ## The hole this closes, stated as the sequence that produced it

    `window_group(action='assign_bsdf', role='direct_sun')` registers the caller's tensor-tree XML as
    a plain `bsdf` **input**, pointing at the caller's own path -- nothing is copied and nothing is
    sealed, because `Cds` has to read the file where `oconv` can find it and a sealed copy would also
    need the scene's BSDF primitive reference rewritten (Task 10a's own ruling). A digest is recorded
    at bind time (`FileRef.digest`). Until this function existed, **nothing in `src/` ever re-read
    it**: `is_reusable` re-digests an *artifact's* sealed bytes (ruling R7), `evaluate` walks
    *recorded* input digests, and `rescan_inputs` -- the one function that could have re-read this
    one -- had no production caller at all. So:

    1. bind a tensor tree; the manifest records its digest;
    2. edit that file (or replace it, or delete it) outside this server;
    3. run 5-phase. `Cds` traces the edited bytes, the `cdsmx` artifact seals against a digest
       computed from the *recorded* input digest, and `project(action='describe')` reports the whole
       graph clean.

    Nothing was wrong with the edge set, so `check_edges` cannot see this and `REQUIRED_EDGES
    ["cdsmx"]`'s own bsdf row does not close it: that row proves an input of kind `bsdf` is **named**,
    which is a statement about the graph's shape, not about the bytes on disk. Two different
    questions; this is the second one.

    ## Where it is asked, and why not deeper

    Here, in the tool layer, immediately after `_refuse_missing_direct_sun_binding` -- the two are one
    pre-flight over the same binding, asked in the only order that reads well ("is there one" before
    "is it still what you bound"), and both refuse before a single matrix is paid for. Not in
    `rad_engine.chain`, whose module docstring forbids file writes and whose `five_phase_steps` builds
    argv without touching the filesystem; not in `JobStore`, where a refusal is a failed job rather
    than a `CallToolResult`. `rad_engine.project.drifted_inputs` does the digesting -- read-only, and
    explicitly not `rescan_inputs(save=False)`, which would leave this call's own `LoadedProject`
    holding fresh digests that were never committed and take every later reuse decision against them.

    **Every `role="direct_sun"` input is checked, not only the ones this run's groups name.** The
    narrower scan would need `groups`, and would then answer "clean" for a project whose *other*
    group's tensor tree drifted -- a state the caller is about to hit on their next call, reported
    then instead of now, for no saving: the check is a digest per bound XML.

    Returns `None` when the inferred method is not `5phase` (nothing else reads this binding) or when
    every direct-sun binding still folds to the digest it was registered with.
    """
    if inference.method != "5phase":
        return None
    bound = [
        record.id for record in loaded.manifest.inputs
        if record.kind == "bsdf" and _bsdf_input_role(record) == "direct_sun"
    ]
    drifted = drifted_inputs(loaded, bound)
    if not drifted:
        return None
    named = ", ".join(f"{input_id!r} ({reason})" for input_id, reason in sorted(drifted.items()))
    message = (
        "the inferred method is '5phase', whose direct-sun term (Cds) traces the tensor-tree BSDF "
        f"at the path it was bound from -- and {named}. The bytes on disk are no longer the bytes "
        "window_group(action='assign_bsdf', role='direct_sun') validated and digested, so this run "
        "would trace a file nothing has checked while provenance reported clean. Re-bind the file "
        "with window_group(action='assign_bsdf', name=..., states={...}, role='direct_sun') to "
        "record the current bytes, or restore the file this project was bound to."
    )
    return refuse(
        message,
        [
            *advisories,
            Advisory(
                code="BSDF_DIRECT_SUN_DRIFTED", severity="error", field="project", message=message,
            ),
        ],
    )


def _refuse_drifted_sealed_tmx(
    groups: Sequence[WindowGroup], loaded: LoadedProject, *, project: str,
    advisories: list[Advisory],
) -> CallToolResult | None:
    """`BSDF_MATRIX_DRIFTED` (error): a sealed `tmx` copy that no longer holds the bytes its record
    was sealed with -- `role="matrix"`'s sibling of `_refuse_drifted_direct_sun_bsdf` above.

    **Final fix D, fix round 4 (review N3).** `docs/tool-surface.md` §6 said `role="matrix"` gets
    its drift check "from `is_reusable` re-digesting the sealed copy on every reuse (ruling R7)".
    Measured false: T is not a step (R62), so no reuse decision is ever asked about a `tmx`;
    `chain.three_phase_target` reads the copy for its Klems bin count only, and
    `_with_operand_digests` covers the `ill` operands of the schedule and blend steps. A copy whose
    bytes moved under an unchanged record -- a hand edit of `07_matrices/`, or the copy
    `window_group(action='assign_bsdf')` writes before its one manifest save, when that save then
    fails -- was multiplied by `dctimestep`, and the job succeeded with provenance clean.

    **Every sealed state of every window group is checked**, not only the ones this run narrows
    to: `three_phase_target`, which this check runs immediately before (above `dry_run`, R39, and
    above every octree build), reads every group's copies, and the narrower scan would answer
    "clean" for a drift the caller meets on the next call -- `_refuse_drifted_direct_sun_bsdf`'s
    reasoning. A copy that cannot be read as a regular file, or whose record holds no digest, is
    the same "no": what was sealed cannot be shown to be there.

    One advisory per group, whose remedy is one whole `assign_bsdf` call over every file-bound
    drifted state, spelled from each `bsdf_<group>_<state>` record's one source file (`files`,
    the path `drifted_inputs` digests, so the call and the sentence about it name one file even
    where a hand edit made the recorded call's `path` disagree -- fix round 5, review M1) at
    `role='matrix'`, followed by the `lm83_default` composition for a composed state (after the
    rebind, since composing reads the open copy). Whether that source still holds the bytes it
    was bound with is checked (`drifted_inputs`, read-only) and said either way, because rebinding
    a changed source seals the change rather than restoring the copy -- and where it has changed,
    a state `lm83_default` composed from it is named with its compose call too, since the rebind
    leaves it stale (fix round 5, review M3). "either" is said only where the copy is known to
    differ, not where it is unreadable or has no digest (review M2). A record with no single
    source file gets a placeholder, and a state composed from it is foretold on the condition
    that the file the caller names holds other bytes (final fix E4).
    """
    artifacts = {artifact.id: artifact for artifact in loaded.manifest.artifacts}
    inputs = {record.id: record for record in loaded.manifest.inputs}
    messages: list[str] = []
    for group in sorted(groups, key=lambda candidate: candidate.name):
        name = group.name
        faults: list[str] = []
        rebind: dict[str, str] = {}
        notes: list[str] = []
        composed: list[str] = []
        # tmx id of a state `lm83_default` composed from a rebound input -> the condition under
        # which the rebind leaves it stale.
        stale: dict[str, str] = {}
        for state in sorted(group.states):
            tmx_id = f"tmx:{name}:{state}"
            record = artifacts.get(tmx_id)
            if record is None:  # `states` is derived from these very ids
                continue
            path = Path(record.path)
            unreadable = False
            # Whether the copy is known to differ from what was sealed (fix round 5, review M2):
            # only then can the source note say "either".
            known_different = False
            try:
                fresh = file_digest(path)
            except (ProvenanceError, OSError) as exc:
                reason, unreadable = f"could not be read as a regular file ({exc})", True
            else:
                if record.content_digest is None:
                    reason = f"cannot be checked: {tmx_id} records no content digest"
                elif fresh != record.content_digest:
                    reason = f"no longer holds the bytes {tmx_id} was sealed with"
                    known_different = True
                else:
                    continue
            faults.append(
                f"the sealed copy of state {state!r} of window group {name!r} at {path} {reason}"
            )
            if unreadable:
                notes.append(
                    f"If something other than a regular file is at {path}, remove it first: "
                    "assign_bsdf will not write a copy through it."
                )
            if record.parameters.get("lm83_default") is True:
                composed.append(state)
                continue
            input_id = f"bsdf_{name}_{state}"
            source_record = inputs.get(input_id)
            # Fix round 5 (review M1): the call is spelled from the record's own `files`, the
            # path `drifted_inputs` digests, so the sentence about that file and the call that
            # reads it name one file even on a manifest whose recorded call was edited by hand.
            files = list(source_record.files) if source_record is not None else []
            # A state `lm83_default` composed from this input goes stale once the rebind seals
            # other bytes, so its compose call follows (fix round 5, review M3).
            composed_from = [
                candidate.id for candidate in loaded.manifest.artifacts
                if candidate.kind == "tmx"
                and candidate.id.startswith(f"tmx:{name}:")
                and candidate.parameters.get("lm83_default") is True
                and input_id in candidate.built_from
            ]
            if len(files) != 1:
                rebind[state] = "<path to the BSDF XML>"
                notes.append(
                    f"{input_id} records {'no' if not files else len(files)} source "
                    f"file{'' if len(files) == 1 else 's'} where assign_bsdf records one, so "
                    "name the file to bind in place of the placeholder."
                )
                # Final fix E4 (part D round-5 re-review N1): which bytes the caller names is not
                # known here, so the composition is foretold on that condition.
                for tmx_id in composed_from:
                    stale.setdefault(
                        tmx_id,
                        f"If the file you name does not hold the bytes {input_id} was bound with",
                    )
                continue
            recorded = str(files[0].path)
            rebind[state] = recorded
            moved = drifted_inputs(loaded, [input_id]).get(input_id)
            either = " either" if known_different else ""
            notes.append(
                f"{recorded} still holds the bytes {input_id} was bound with, so that call "
                "restores them."
                if moved is None else
                f"{recorded} no longer holds the bytes {input_id} was bound with{either} "
                f"({moved}), so that call seals what that file holds now, or is refused if it "
                "cannot be read: put back the file that was bound first to keep its glazing."
            )
            if moved is not None:
                for tmx_id in composed_from:
                    stale.setdefault(tmx_id, "If that call seals changed bytes")
        if not faults:
            continue
        one = len(faults) == 1
        remedy: list[str] = []
        if rebind:
            spelled = ", ".join(f"{state!r}: {path!r}" for state, path in rebind.items())
            single = len(rebind) == 1
            remedy.append(
                f"Bind {'it' if single else 'them'} again from the "
                f"{'file it was' if single else 'files they were'} bound from, which copies and "
                f"seals {'it' if single else 'them'} anew: window_group(action='assign_bsdf', "
                f"project={project!r}, name={name!r}, states={{{spelled}}}, role='matrix')."
            )
        remedy.extend(notes)
        for tmx_id, condition in sorted(stale.items()):
            state = tmx_id.rsplit(":", 1)[1]
            if state in composed:
                continue  # its own drift already spells the compose call below
            remedy.append(
                f"{condition}, the {state} state {LM83_DEFAULT} composed "
                "from it is left stale; compose it again after that: "
                f"window_group(action='assign_bsdf', project={project!r}, name={name!r}, "
                f"states={{{state!r}: {LM83_DEFAULT!r}}})."
            )
        for state in composed:
            remedy.append(
                f"{'It' if one else f'State {state!r}'} was composed by {LM83_DEFAULT} from the "
                f"open state; compose it again{' after that' if rebind else ''}: "
                f"window_group(action='assign_bsdf', project={project!r}, name={name!r}, "
                f"states={{{state!r}: {LM83_DEFAULT!r}}})."
            )
        what = "a T matrix that is" if one else "T matrices that are"
        messages.append(
            "; ".join(faults)
            + f", so this run is refused rather than multiply {what} not known to be the BSDF "
            "the manifest records. "
            + " ".join(remedy)
        )
    if not messages:
        return None
    return refuse(
        " ".join(messages),
        [
            *advisories,
            *(
                Advisory(
                    code="BSDF_MATRIX_DRIFTED", severity="error", field="project",
                    message=message,
                )
                for message in messages
            ),
        ],
    )


#: What the geometry-disagreement refusals say when no project record is at hand to spell the call
#: from (a direct call of the builders, as the tests make). `run_annual` passes
#: `_geometry_add_calls`' rendering instead.
_GEOMETRY_ADD_TEMPLATE: Final[str] = (
    "geometry(action='add', project=<project>, files=[<the scene files>], units=<their units>)"
)


def _geometry_add_calls(
    loaded: LoadedProject, project: str, *, input_ids: Collection[str] | None = None,
) -> str:
    """Every `geometry(action='add', ...)` call this project's geometry was registered by, spelled
    from each record's own `registered_by.arguments` alone, joined with "; " -- or, for a record
    that carries no recorded call with its files, the same call with this project and the record's
    own name (its id without `geometry_`, so the add replaces that record) and placeholders for the
    files and units alone, which only the caller knows.

    The remedy of the refusals that find the window groups' polygons and the registered scene in
    disagreement (`AseSceneNotBlackedError`, `SunsShellNotStrippedError`): adding the files again
    re-registers them under the same name from the bytes on disk. Before this function the remedy
    named `geometry(action='register')`, which is not an action of that tool (add, list, remove,
    check), so it failed if followed.
    """
    calls: list[str] = []
    for record in loaded.manifest.inputs:
        if record.kind != "geometry":
            continue
        if input_ids is not None and record.id not in input_ids:
            continue  # final fix E7: only the records a caller names
        arguments = dict(record.registered_by.arguments) if record.registered_by is not None else {}
        if not arguments.get("files"):
            # No recorded call to repeat. The record's own files are the server's stored copy,
            # possibly already scaled to metres, and its `units` the unit the caller declared for
            # the source, so a call spelled from the two would scale the model a second time with
            # no error. The call is spelled with this project and the record's own name, and
            # placeholders only for the source files and their units, which the caller supplies.
            calls.append(
                f"geometry(action='add', project={project!r}, files=[<the source scene files>], "
                f"units=<their units>, name={_strip_prefix(record.id, _GEOMETRY_PREFIX)!r})"
            )
            continue
        parts = ["action='add'", f"project={project!r}"] + [
            f"{key}={value!r}" for key, value in arguments.items() if value is not None
        ]
        calls.append(f"geometry({', '.join(parts)})")
    return "; ".join(calls) or _GEOMETRY_ADD_TEMPLATE


# --- the automated mark, its bound schedule and the composed closed state (R869, R870, R871) ---


def _strip_prefix(record_id: str, prefix: str) -> str:
    return record_id[len(prefix):] if record_id.startswith(prefix) else record_id


def _window_group_records(loaded: LoadedProject) -> dict[str, InputRecord]:
    """Every `window_group` record, by group name (the input id without its prefix)."""
    return {
        _strip_prefix(record.id, _WINDOW_GROUP_PREFIX): record
        for record in loaded.manifest.inputs
        if record.kind == "window_group"
    }


def _bound_schedules(
    loaded: LoadedProject,
) -> tuple[dict[str, AutomatedSchedule], dict[str, InputRecord]]:
    """`chain.automated_schedules` over the records it can read, and the records it cannot (R870).

    A record that names no one file (`chain.automated_schedule_file` answers `None`) makes
    `automated_schedules` raise `AutomatedScheduleRecordError`. That is mapped to the plan-time
    refusal `AUTOMATED_SCHEDULE_UNUSABLE` -- on the run that would read it
    (`_refuse_unusable_schedules`) -- and not to "no schedule bound", which would hold the group
    closed every hour. Every *other* group's schedule is still read, through the same function over
    the manifest with the unreadable records left out: one bad record does not make a sound one
    read as unbound, which would hold that group closed and say so falsely.
    """
    unreadable = {
        _strip_prefix(record.id, _SCHEDULE_PREFIX): record
        for record in loaded.manifest.inputs
        if record.kind == "automated_schedule" and automated_schedule_file(record) is None
    }
    try:
        return automated_schedules(loaded), unreadable
    except AutomatedScheduleRecordError:
        readable = [
            record for record in loaded.manifest.inputs
            if not (record.kind == "automated_schedule" and automated_schedule_file(record) is None)
        ]
        manifest = loaded.manifest.model_copy(update={"inputs": readable})
        return automated_schedules(replace(loaded, manifest=manifest)), unreadable


def _refuse_stale_lm83_default(
    target: ThreePhaseTarget | None,
    loaded: LoadedProject,
    *,
    project: str,
    advisories: list[Advisory],
) -> CallToolResult | None:
    """`LM83_DEFAULT_STATE_STALE` (error, R871): a state `window_group(action='assign_bsdf',
    states={'closed': 'lm83_default'})` composed, which `evaluate` now reports invalid.

    The composed `tmx` names `bsdf_<g>_open` in its `built_from`, so rebinding the open state makes
    it stale; nothing else would stop this run multiplying a 5 % of a glazing the group no longer
    has. Checked over the groups this three- or five-phase run resolved (`target`), above `dry_run`
    (R39) and above every octree build. The message says the open state was rebound only where that
    edge is the one that moved (`ArtifactStatus.invalidated_by`); otherwise it names what did: the
    edges that moved, a change to its own sealed parameters (`ArtifactStatus.parameters_changed`)
    and any kind rule (`stale_reason`). Composing it again re-seals it from the current open state
    either way.
    """
    if target is None:
        return None
    composed = {
        artifact.id for artifact in loaded.manifest.artifacts
        if artifact.kind == "tmx" and artifact.parameters.get("lm83_default") is True
    }
    named = [
        (tp_group.group.name, state, tmx_id)
        for tp_group in target.groups
        for state, tmx_id in sorted(tp_group.tmx_ids.items())
        if tmx_id in composed
    ]
    if not named:
        return None
    statuses = evaluate_loaded(loaded.manifest)
    messages: list[str] = []
    for name, state, tmx_id in named:
        status = statuses[tmx_id]
        if status.valid:
            continue
        compose = (
            f"window_group(action='assign_bsdf', project={project!r}, name={name!r}, "
            f"states={{{state!r}: {LM83_DEFAULT!r}}})"
        )
        if f"bsdf_{name}_open" in status.invalidated_by:
            why = (
                f"the {state} state of window group {name!r} was composed by {LM83_DEFAULT} from "
                "an open state that has since been rebound, so it no longer transmits 5 % of the "
                "open state's diffuse transmittance."
            )
        else:
            causes = list(status.invalidated_by)
            if status.parameters_changed:
                causes.append("its own sealed parameters no longer reproduce its digest")
            if status.stale_reason:
                causes.append(status.stale_reason)
            moved = "; ".join(causes) or "its digest no longer matches"
            why = (
                f"the {state} state of window group {name!r} was composed by {LM83_DEFAULT}, and "
                f"what it was composed from has changed since ({moved}), so it is no longer known "
                "to be 5 % of the current open state's diffuse transmittance."
            )
        messages.append(f"{why} Compose it again from the current open state: {compose}.")
    if not messages:
        return None
    return refuse(
        " ".join(messages),
        [
            *advisories,
            *(
                Advisory(
                    code="LM83_DEFAULT_STATE_STALE", severity="error", field="project",
                    message=message,
                )
                for message in messages
            ),
        ],
    )


def _weather_load_call(loaded: LoadedProject, project: str, epw: str) -> str:
    """`weather(action='load', ...)` for a full year's file, under this run's weather name and with
    the clock zone the current record holds (R860): `load` replaces the record and records none
    when none is named, so a remedy that omitted a recorded zone would clear it."""
    candidates = {epw, f"{_WEATHER_PREFIX}{epw}"}
    record = next(
        (r for r in loaded.manifest.inputs if r.kind == "weather" and r.id in candidates), None
    )
    name = _strip_prefix(record.id, _WEATHER_PREFIX) if record is not None else epw
    zone = getattr(record, "clock_zone", None) if record is not None else None
    return (
        f"weather(action='load', project={project!r}, epw='/path/to/a.epw', name={name!r}"
        + (f", clock_zone={zone!r}" if zone else "")
        + ")"
    )


def _bind_schedule_call(project: str, name: str) -> str:
    """The rebind remedy: the CSV path is the caller's own file, so it is the one placeholder."""
    return (
        f"window_group(action='bind_schedule', project={project!r}, name={name!r}, "
        "schedule='/path/to/schedule.csv')"
    )


def _refuse_unusable_schedules(
    groups: Sequence[WindowGroup],
    loaded: LoadedProject,
    *,
    project: str,
    epw: str,
    n_hours: int,
    advisories: list[Advisory],
) -> CallToolResult | None:
    """`AUTOMATED_SCHEDULE_UNUSABLE` (error, R870): the plan-time read of every bound schedule this
    run seals, and the refusal of one it cannot use.

    Called only on a run that seals a blind schedule (`_plans_blend`), and only for groups marked
    automated -- the one run and the only groups whose schedule `sched:<grid>` reads. Each bound
    copy is read **once**, bounded at `MAX_AUTOMATED_SCHEDULE_BYTES + 1` through
    `open_regular_file`; its bytes are digested against the record's `FileRef.digest` (an edit in
    the project since `bind_schedule` copied it is refused, not read); it is parsed; and its rows
    are checked against **this run's** weather (`state.weather.n_hours`), not against a common
    year. A record that names no one file (`_bound_schedules`) is refused here too. Nothing is
    kept from a read that passes: the helper reads the same file again against its own `--n-hours`.

    Above `dry_run` (R39) and above every octree build, so a refused run compiles nothing.
    """
    records = _window_group_records(loaded)
    _, unreadable = _bound_schedules(loaded)
    messages: list[tuple[str, str]] = []
    for group in groups:
        if not group.automated:
            continue
        name = group.name
        rebind = _bind_schedule_call(project, name)
        if name in unreadable:
            record = unreadable[name]
            messages.append((name, (
                f"window group {name!r} is marked automated and the manifest's record "
                f"{record.id!r} for its hourly schedule names {len(record.files)} file(s), where "
                "window_group(action='bind_schedule') registers exactly one, so which hours its "
                f"device is closed cannot be read. Bind the schedule again: {rebind}."
            )))
            continue
        bound = group.automated_schedule
        if bound is None:
            continue
        path = bound.path
        try:
            with open_regular_file(path, "rb") as handle:
                data = handle.read(MAX_AUTOMATED_SCHEDULE_BYTES + 1)
        except OSError as exc:
            messages.append((name, (
                f"window group {name!r} is marked automated and its bound schedule {path} could "
                f"not be read as a regular file ({exc}). Bind the schedule again: {rebind}."
            )))
            continue
        if bytes_digest(data) != bound.digest:
            messages.append((name, (
                f"window group {name!r} is marked automated and its bound schedule {path} no "
                "longer holds the bytes window_group(action='bind_schedule') copied into the "
                "project: the file was edited after it was bound, so the hours it closes are not "
                f"the ones that were checked. Bind the schedule again: {rebind}."
            )))
            continue
        try:
            parse_automated_schedule(data, hours=n_hours)
        except AutomatedScheduleError as exc:
            if n_hours in AUTOMATED_SCHEDULE_HOURS:
                remedy = (
                    f"Bind a schedule of {n_hours} rows, one per hour of this project's weather "
                    "file in its own row order: window_group(action='bind_schedule', "
                    f"project={project!r}, name={name!r}, schedule='/path/to/a.csv')."
                )
            else:
                remedy = (
                    f"This project's weather file has {n_hours} hours and a bound schedule has "
                    f"{AUTOMATED_SCHEDULE_HOURS[0]} or {AUTOMATED_SCHEDULE_HOURS[1]} rows, so no "
                    "schedule can match it: load a full year's weather with "
                    f"{_weather_load_call(loaded, project, epw)}, or redefine the group without "
                    "the mark so the trigger "
                    f"closes it: {_redefine_call(project, name, records[name], automated=False)}."
                )
            messages.append((name, (
                f"window group {name!r} is marked automated and its bound schedule {path} cannot "
                f"be used by this run: {exc} {remedy}"
            )))
            continue
    if not messages:
        return None
    return refuse(
        " ".join(message for _, message in messages),
        [
            *advisories,
            *(
                Advisory(
                    code="AUTOMATED_SCHEDULE_UNUSABLE", severity="error", field="project",
                    message=message,
                )
                for _, message in messages
            ),
        ],
    )


class ApertureModifierSharedError(ProjectError):
    """Two window groups carry one aperture modifier, and this run must black it for one of them
    and leave it open for the other (R869).

    `rad_engine.scene.build_ase_scene` (the per-group ASE scene) and `build_ase_net_scene` (the
    ASE_net scene) refuse it with a `ValueError` whose remedy names no whole call, and the
    per-group one reached the wire uncaught. `_refuse_shared_aperture_modifiers` raises this first,
    from the groups' own records, with every affected group's whole call spelled.

    No register code, for `AseSceneNotBlackedError`'s reason (the per-group ASE scene's other
    refusal): a disagreement between the window groups' definitions with one repair, which the
    sentence spells.
    """

    code = None


def _names(names: Sequence[str]) -> str:
    quoted = [repr(name) for name in names]
    return quoted[0] if len(quoted) == 1 else ", ".join(quoted[:-1]) + " and " + quoted[-1]


def _refuse_shared_aperture_modifiers(
    loaded: LoadedProject,
    *,
    project: str,
    groups: Sequence[WindowGroup],
    net: bool,
    edition: Lm83Edition = DEFAULT_LM83_EDITION,
) -> None:
    """Raise `ApertureModifierSharedError` when a modifier is carried by two of `groups` that this
    run must tell apart: any two on the per-group ASE scenes (`net=False`, where each group is open
    in its own scene and closed in every other), or one marked automated and one not on the ASE_net
    scene (`net=True`, where only the marked are closed -- two marked groups sharing one are both
    closed either way, as `build_ase_net_scene` allows).

    The remedy is spelled per affected group from its own record, `closing_role` and `automated`
    included (R133: a redefine that omits `closing_role` clears it): a redefine without the shared
    modifier where the group has others; otherwise `remove`, or a modifier of its own in the
    geometry and the group's whole call with that modifier in place of the shared one.
    """
    modifiers = {
        group.name: {polygon.modifier for polygon in group.aperture.polygons} for group in groups
    }
    by_name = {group.name: group for group in groups}
    conflicts: dict[str, list[str]] = {}
    for modifier in sorted({m for carried in modifiers.values() for m in carried}):
        owners = sorted(name for name, carried in modifiers.items() if modifier in carried)
        if net:
            marked = [name for name in owners if by_name[name].automated]
            if marked and len(marked) < len(owners):
                conflicts[modifier] = owners
        elif len(owners) > 1:
            conflicts[modifier] = owners
    if not conflicts:
        return
    sentences: list[str] = []
    for modifier, owners in conflicts.items():
        carry = "both carry" if len(owners) == 2 else "all carry"
        if net:
            marked = [name for name in owners if by_name[name].automated]
            unmarked = [name for name in owners if not by_name[name].automated]
            consequence = (
                f"so the ASE_net scene cannot close {_names(marked)} (marked automated) and leave "
                f"{_names(unmarked)} open: blacking the modifier closes every opening that carries "
                "it, and ASE_net would come out smaller than it is, the favourable direction."
            )
        else:
            other, others_opening = (
                ("the other", "the other's opening") if len(owners) == 2
                else ("the others", "the others' openings")
            )
            consequence = (
                f"so the per-group ASE scene that leaves one of them open and closes {other} "
                f"cannot be built: blacking the modifier for one closes {others_opening} too, "
                f"and the {section('blind_trigger', edition)} residual would come out far too "
                "small, under an id that says the group was open."
            )
        sentences.append(
            f"window groups {_names(owners)} {carry} the aperture modifier {modifier!r}, "
            f"{consequence}"
        )
    records = _window_group_records(loaded)
    remedies: list[str] = []
    for name in sorted({owner for owners in conflicts.values() for owner in owners}):
        record = records[name]
        group = by_name[name]
        shared = sorted(modifier for modifier, owners in conflicts.items() if name in owners)
        recorded = (
            list(record.registered_by.arguments.get("aperture") or [])
            if record.registered_by is not None else []
        )
        rest = [modifier for modifier in recorded if modifier not in shared]
        if rest:
            remedies.append(
                f"for {name!r}, redefine it without {_names(shared)}: "
                f"{_redefine_call(project, name, record, automated=group.automated, aperture=rest)}"
            )
        else:
            remedies.append(
                f"for {name!r}, remove it (window_group(action='remove', project={project!r}, "
                f"name={name!r}), its bindings with it), or give its polygons a modifier of "
                f"their own in the registered geometry and redefine it with that modifier in "
                f"place of {_names(shared)}, the rest of its call as it stands: "
                f"{_redefine_call(project, name, record, automated=group.automated)}"
            )
    raise ApertureModifierSharedError(
        " ".join(sentences)
        + " A modifier names every polygon that carries it, so each shared one must be left to a "
        + "single group: change every group that carries it but one. For each group, the change "
        + "is -- "
        + "; ".join(remedies)
        + " -- and re-run."
    )


def _automated_advisories(
    groups: Sequence[WindowGroup],
    loaded: LoadedProject,
    *,
    project: str,
    method: str,
    blind_control: str | None,
    plans_blend: bool,
    plans_direct_leg: bool,
) -> list[Advisory]:
    """`AUTOMATED_GROUP_HELD_CLOSED` and `AUTOMATED_SCHEDULE_UNUSED` (warn, R870), each true of the
    run it rides.

    - **Held closed**: a run that seals a blind schedule (`plans_blend`), per group marked
      automated with no schedule bound. `blind_schedule_steps` seals it `"none"` and the helper
      closes it in every hour. On a run that seals no schedule nothing is held closed by one, so
      the mark alone never fires it.
    - **Unused**: per group with a schedule bound that this run does not read, with the reason for
      this state and only that one -- (a) the group is not marked (no run reads it), or (b) it is
      marked and this run seals no schedule. The tail of (b) says what the mark does here instead:
      it blacks the group in the ASE_net trace where this run plans the direct-sun leg, and nothing
      it computes where it plans none.

    Every redefine is spelled from the group's own record (`_redefine_call`), `closing_role`
    included.
    """
    records = _window_group_records(loaded)
    advisories: list[Advisory] = []
    for group in groups:
        name = group.name
        record = records[name]
        bound = group.automated_schedule
        if group.automated and bound is None:
            if not plans_blend:
                continue
            advisories.append(Advisory(
                code="AUTOMATED_GROUP_HELD_CLOSED", severity="warn", field="project",
                message=(
                    f"window group {name!r} is marked automated and has no hourly schedule bound, "
                    "so the blind schedule holds it closed in every hour: its closed state is "
                    "blended in every hour and its sunlight takes no part in the blind trigger "
                    "that closes the other groups. Bind the device's hourly schedule with "
                    f"{_bind_schedule_call(project, name)}, or redefine the group without the mark "
                    "so the trigger closes it: "
                    f"{_redefine_call(project, name, record, automated=False)}."
                ),
            ))
        elif bound is not None and not group.automated:
            reads = (
                "so this run's blind schedule closes it by the trigger and does not read the file"
                if plans_blend else
                "so no run reads the file: a blind schedule closes an unmarked group by the trigger"
            )
            advisories.append(Advisory(
                code="AUTOMATED_SCHEDULE_UNUSED", severity="warn", field="project",
                message=(
                    f"window group {name!r} has an hourly schedule bound ({bound.path}) but is not "
                    f"marked automated, {reads}. To use it, redefine the group with the mark: "
                    f"{_redefine_call(project, name, record, automated=True)}"
                    + (
                        "." if plans_blend else
                        "; only a three-phase run with blind_control='lm83' then reads it, so this "
                        "run would still not."
                    )
                ),
            ))
        elif bound is not None and not plans_blend:
            seals = (
                "a five-phase run builds no blend" if method == "5phase" else
                "blind_control='lm83' on a three-phase run seals one, and this run is "
                f"method={method!r}, blind_control={blind_control!r}"
            )
            mark = (
                "here the mark only blacks the group in the ASE_net trace."
                if plans_direct_leg else
                "this run plans no direct-sun leg, so here the mark changes nothing it computes."
            )
            advisories.append(Advisory(
                code="AUTOMATED_SCHEDULE_UNUSED", severity="warn", field="project",
                message=(
                    f"window group {name!r} is marked automated with an hourly schedule bound "
                    f"({bound.path}), and this run seals no blind schedule ({seals}), so the file "
                    f"is not read; {mark}"
                ),
            ))
    return advisories


#: **`_FENESTRATION_REFUSALS` was deleted by R815.** Its one member, the DGP-on-a-horizontal-
#: opening refusal, keyed on `annual_dgp`, which is withdrawn.


def _refuse_unbuildable_method(
    inference: Inference, advisories: list[Advisory]
) -> CallToolResult | None:
    """Refuse an inference `infer` itself refused on the arguments alone (`METHOD_OVERRIDE_UNSAFE`),
    or one whose method this phase genuinely cannot build -- **with `METHOD_INFERRED` still
    attached**. `None` if neither applies.

    `infer`'s own `refused` flag (rule 1's `METHOD_OVERRIDE_UNSAFE`) is folded in here rather than
    handled separately: a refused override still leaves `method` at whatever rule 1 chose, which is
    always `5phase`.

    **Reachable since R101, Phase 7b Task 15.** Before it, `metrics=["ase","sda"], method="3phase"`
    reached `_refuse_uncomputed_metric` (`METRIC_NOT_COMPUTED_YET`, both deleted by R815) first,
    because `ase`/`sda` were members of the set it refused. Task 15 removed them, so *this* function
    is what answers -- carrying a live `inference.refused=True` whose `inference.method` ('5phase')
    genuinely **is** in `_BUILDABLE_METHODS`.

    **The two conditions the guard below distinguishes are not the same fact, and conflating them
    was the defect this reachability would have shipped silently.** `inference.method not in
    _BUILDABLE_METHODS` means *this method cannot be built yet, wait for a later phase* -- true of
    nothing today (`_BUILDABLE_METHODS` is every `Method` value `rad_core.method` can produce), kept
    as a backstop for a future `Method` `_BUILDABLE_METHODS` does not name. `inference.refused`
    means *the method IS buildable, but the caller's own `method=` override contradicted a
    direct-sun-sensitive metric and `infer` refused to honour it* -- the opposite fact. The template
    below ("cannot build yet") is **false** of the second condition: `metrics=["ase","sda"],
    method="3phase"` chooses `'5phase'`, which this server builds today, so a message assembled from
    that template would tell the caller the wrong thing is wrong. `infer` already built the correct,
    actionable sentence for this case -- the `METHOD_OVERRIDE_UNSAFE` advisory's own `message`
    (`"method='3phase' was refused: ... pass method='auto' or method='5phase' instead."`) -- so it
    is read back off `advisories` and used verbatim as the wire `reason`, rather than re-derived, so
    the top-level reason and the advisory that explains it cannot read as two different refusals.
    """
    if inference.method in _BUILDABLE_METHODS and not inference.refused:
        return None
    if inference.refused:
        unsafe = next((a for a in advisories if a.code == "METHOD_OVERRIDE_UNSAFE"), None)
        # **The `None` branch is unreachable today, and this says so rather than leaving it
        # uncommented -- Task 15 review, Minor 3.** `rad_core.method.infer` sets `refused = True` in
        # exactly one place (`method.py`'s rule 1), and appends the `METHOD_OVERRIDE_UNSAFE`
        # `ArtifactWarning` in the identical `if` block -- so `inference.refused` implies `advisories`
        # already carries one, and `unsafe` can never be `None` through this module's own call sites.
        # Kept as a defensive fallback rather than an assertion: if `infer` ever grew a second way to
        # set `refused = True` without a matching advisory, a bare reason-fragment on the wire is a
        # worse failure than a crash, but neither should ship untested -- there is no test for this
        # branch because there is no reachable input that exercises it, the same posture the
        # "cannot build yet" backstop three lines below states for itself.
        message = unsafe.message if unsafe is not None else " ".join(inference.reasons)
        return refuse(message, advisories)
    # Unreachable through this module's own public entry point today: `_BUILDABLE_METHODS` is every
    # `Method` value `rad_core.method` can produce, so `inference.method in _BUILDABLE_METHODS` is
    # `True` unconditionally and the `inference.refused` branch above already returned. Kept as a
    # backstop for a future `Method` `_BUILDABLE_METHODS` does not name -- no `# pragma: no cover`, this repo
    # enforces no coverage threshold (review round 1's Minor 10). Its message used to name the plan
    # phase that would build the method (`_METHOD_PHASE`, removed by R811); it now says only what is
    # true when it fires: the inferred method is not one this server builds. "cannot build yet" is
    # kept word for word: tests/test_annual_tool.py's negative assertions key on it to prove this
    # backstop did not answer a call.
    reasons = " ".join(inference.reasons)
    message = (
        f"the method inferred for these metrics is '{inference.method}', which this server cannot "
        f"build yet. The inference ran and is reported rather than hidden: "
        f"{reasons} This call is refused rather than run as a lesser method under a "
        f"'{inference.method}' label, because a method reported but not built is a wrong number "
        "waiting to be read."
    )
    return refuse(message, advisories)


# --- project state, read once and shared by both paths ------------------------------------------


class _Weather(BaseModel):
    """The `.wea` a `weather(action="load")` call left in this project, and the input id that names
    it -- carried together so the step builder and the edge set cannot disagree about which record
    they found."""

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    input_id: str
    wea_path: Path
    n_hours: int
    """**Carried in from Task 12's own review, closed here.** The real column count of the weather
    this run loaded -- never `ANNUAL_TIMESTEPS`. `ChainRequest.n_hours`'s own docstring names this
    task by number: *"the moment `_chain_request` ... starts constructing `ChainRequest` for a real
    `method='5phase'` run, that call site MUST pass `n_hours=` the actual column count of the weather
    this run loaded"* -- and its final `rcollate -h -fa1 -ic n_hours -t` is silently wrong, not loud,
    for any weather shorter or longer than 8760 hours if that instruction is skipped (`rad_engine.
    annual.build_rcollate_final_argv`'s own docstring, and ruling R47's history for the identical
    failure shape on a different field).

    **Measured by `_measured_n_hours`: the registered `.epw` when it can be read, the registered
    `.wea` when it cannot -- fixed from a review round 1 finding (Important 4), not the original
    design.** The first cut always re-read the caller's own external `.epw`, which made every
    subsequent annual call -- 2-phase and 3-phase reuse runs included -- depend on a file this server
    never owns and never deletes (`weather_tool._load`'s own docstring) still being where it was.
    `_measured_n_hours`'s own docstring carries the full reasoning, including why the alternative
    (deferring the read behind `inference.method == "5phase"`) was rejected: at the time no 5-phase
    call reached `ChainRequest` construction at all, so a deferred read would have been wiring no real
    call could exercise. Task 14b built that caller, and the read stays unconditional -- one code
    path, exercised by every method.

    **Paid on every call, 2-phase and 3-phase included**, not gated behind a 5-phase check: this
    field is on `_Weather`, which `_dispatch` resolves once regardless of method. The cost is one
    read of the file this project already wrote and already validated is present -- the caller's
    `.epw` is no longer read here at all, which is the whole-branch review's Important 1 (see
    `_measured_n_hours`). No new failure mode reaches the wire uncoded: a read that fails raises
    `WeatherNotLoadedError`, this module's own established exception, rather than a bare
    `OSError` `run_annual`'s handler chain has never had to catch."""


class WeatherNotLoadedError(ProjectError):
    """No `weather` input matches `epw`, or the one that does has no `.wea`.

    No register code: like `GridNotFoundError`, `epw` is an ordinary tool argument naming a handle
    inside the project, and the register allocates codes for the protocol handles (`project`,
    `job_id`) rather than for every argument that can miss. The refusal carries its reason as prose
    and names what *is* registered, which is what keeps a code-less refusal actionable
    (`weather_tool._unknown_name`'s own posture).
    """

    code = None


class SunsShellNotStrippedError(ProjectError):
    """`scene_without_modifiers` removed **nothing** while building the suns octree's opaque shell --
    the whole-branch review's IMPORTANT 4.

    Task 15b's Critical fix is *"keep the aperture's polygons out of the `xform -m black` pipe"*, and
    `StrippedScene.removed` is the only evidence that it happened. `strip_primitives_by_modifier`
    returns `()` silently when a modifier matches nothing, so an empty `removed` is a fix that did
    not apply, reported by nothing: `Cds` goes back to identically zero, the job exits 0, and every
    sunlit hour is published lower than plain 3-phase with correct provenance and no error.

    Raised **above the `dry_run` branch** (R39), so a preview refuses exactly as a real call does.

    No register code, for `DirectSunBindingUnusableError`'s reason: this is a disagreement between
    the window group's own polygons and the registered scene files they were derived from, and there
    is one repair -- re-register the geometry -- so a client has nothing to branch on that the
    sentence does not already say. A code would also be a row the register could not honestly
    describe as a state a caller reaches by doing something.
    """

    code = None


class AseSceneNotBlackedError(ProjectError):
    """`build_ase_scene` blacked **none** of another window group's aperture while building a
    per-group ASE scene -- `SunsShellNotStrippedError`'s sibling, one octree over.

    Phase 7d-1 Task 8's whole point is *"group g open, every other group closed"*, and
    `AseScene.blacked_modifiers` is the only evidence it happened. A modifier that matches no
    primitive blacks nothing **silently** (`build_ase_scene`'s own contract, and the right
    behaviour for a pure text function), so an unblacked run measures the beam through every
    aperture and seals it under one group's id: LM-83 section 2.2.6's residual then comes out the
    same for every group, the schedule closes nothing, and `sda` publishes an unshaded year with
    correct provenance and no error.

    Raised **above the `dry_run` branch** (R39), so a preview refuses exactly as a real call does.

    Unreachable on a single-group run, and that is not an exemption: with one group there is no
    other aperture to close, the expected set is empty, and the check passes because the claim it
    makes is true.

    No register code, for `SunsShellNotStrippedError`'s reason -- this is a disagreement between the
    window groups' own polygons and the registered scene files they were derived from, with one
    repair, which the sentence already names.
    """

    code = None


class DirectSunBindingUnusableError(ProjectError):
    """The `role='direct_sun'` BSDF binding the suns octree's proxy is built from cannot be used --
    Task 14b, two conditions with one repair.

    1. **No such input in the manifest**, raised by `_direct_sun_xml_path`, whose own docstring
       carries the reachability argument (`_refuse_missing_direct_sun_binding` and
       `three_phase_target` between them make it unreachable from the wire).
    2. **The bound path cannot be written into a Radiance primitive** -- a `"` or a newline in the
       filename, which the scene reader has no escape for even inside the quotes this server writes
       the path in (`rad_engine.klems._UNQUOTABLE_IN_A_PATH` carries the measurement). Reachable: a
       caller chooses that path. Refused here, above `dry_run`, rather than left to fail inside
       `oconv` with `bad arguments for BSDF` -- an error naming neither the path nor the fix
       (review fix round 1, Minor 5).

    One exception for both because the repair is one sentence -- re-bind the file from a usable path
    -- and a client that had to branch between "absent" and "unwritable" would do the same thing
    either way.

    No register code, for `WeatherNotLoadedError`'s reason and one more: `BSDF_DIRECT_SUN_NOT_BOUND`
    is the *published* code for the first condition and it is emitted by `_refuse_missing_direct_sun_
    binding`, which answers first for every call that can reach this server. Minting a second code
    for the same fact discovered one layer deeper would give a client two codes to branch on for one
    repair.
    """

    code = None


class BlendStatesNotBoundError(ProjectError):
    """A blended run whose window group does not bind both of the states the schedule can name.

    **Phase 7d-1 Task 19, and this is a hazard the wiring itself armed.** LM-83 sec 2.2.6's
    schedule is written in the standard's own two words: `rad_core.blinds` emits literally
    `BLIND_OPEN` (`"open"`) or `BLIND_CLOSED` (`"closed"`) for each group in each hour, and nothing
    maps those onto whatever a caller happened to name its states -- `window_group(action=
    'assign_bsdf')`'s state names are the caller's, and `'open', 'closed', 'blind-45'` are offered
    there as *examples*.

    So a group that binds only `{"closed"}` -- or only `{"open"}`, or `{"day", "night"}` -- plans a
    perfectly well-formed job whose blend step then finds no contribution for the state the
    schedule selected. `rad_core.blinds.blend_row` refuses that loudly (*"the schedule selects state
    'open' for window group 'main' in hour 6, but no contribution was given for that pair. A
    missing contribution sums as zero and publishes a darker room than was computed"*) -- **after**
    every V, D and multiply has been paid for. Refused here instead, above the octree builds and
    above `dry_run`, which is R39's own direction and the same trade `blind_schedule_steps` already
    makes for a missing analysis area.

    **Extra states are fine and are not refused.** The schedule can name only these two, so a group
    binding `{"open", "closed", "blind-45"}` passes every state as a contribution, has both of the
    two the scan can select, and blends correctly; `blend_row`'s ghost check is over *groups*, not
    states.

    **Only a blended run is checked.** Under `blind_control="none"` or `"fixed"` no schedule exists,
    nothing selects a state by name, and a lone `{"night"}` state is simply the one T that run
    multiplies -- which is why this is not a `window_group` refusal.

    No register code, for `DirectSunBindingUnusableError`'s reason: the repair is one sentence and a
    client would take the same action whichever half was missing.
    """

    code = None


def _refuse_unbindable_blend_states(
    target: "ThreePhaseTarget", *, edition: Lm83Edition = DEFAULT_LM83_EDITION
) -> None:
    """Raise `BlendStatesNotBoundError` for the first group missing `open` or `closed`.

    `BLIND_STATES` is imported from `rad_core.blinds`, never retyped: the two words this checks for
    are the two words `group_schedule` emits, and a hand-typed pair here would agree with them until
    one moved. Ruling R30's posture, and the same one `_LM83_BLIND_CONTROL` takes towards
    `chain.LM83_BLIND_CONTROL`.
    """
    for tp_group in target.groups:
        missing = BLIND_STATES - set(tp_group.states)
        if missing:
            bound = sorted(tp_group.states)
            # **Ruling R189, measured by the Task 19 review: no recovery sentence may name a call
            # that cannot succeed.** The single sentence this replaces offered
            # `blind_control='none'` unconditionally -- and on a group binding two states named
            # neither `open` nor `closed` (`{"day", "night"}`, the review's own case) that call is
            # refused in turn by `chain.ThreePhaseGroup.state`, which raises for
            # `len(states) != 1`. The two refusals pointed at each other, and only the one-state
            # case was tested. The clause is therefore branched on the count this group actually
            # binds, so each arm names a call that runs.
            unscheduled = (
                "or run this project with blind_control='none', which multiplies the one state "
                "this group binds and chooses between none."
                if len(bound) == 1 else
                "or, if no schedule was intended, leave exactly one state bound to this group and "
                "run it with blind_control='none', which multiplies that one state and chooses "
                f"between none. As it stands that run is refused too: {len(bound)} states are "
                f"bound ({', '.join(bound)}) and an unscheduled run has nothing to choose between "
                "them with."
            )
            raise BlendStatesNotBoundError(
                f"blind_control='lm83' blends window group {tp_group.group.name!r} between the "
                f"states the schedule of {section('blind_trigger', edition)} names, and that "
                "group binds "
                f"{bound} -- it is missing {sorted(missing)}. The schedule emits "
                f"the standard's own two words, {sorted(BLIND_STATES)}, for every group in every "
                "hour, and the blend selects the contribution sealed under that exact name; a "
                "state the schedule can name and the project has not bound has no matrix to "
                "select, so the run would fail inside the blend after every matrix had been "
                "built. Bind both with window_group(action='assign_bsdf', name="
                f"{tp_group.group.name!r}, states={{'open': ..., 'closed': ...}}), " + unscheduled
            )


def _measured_n_hours(wea: Path, *, epw: str) -> int:
    """`_Weather.n_hours`'s own measurement -- **the project's own `.wea`, and nothing else.**

    **The whole-branch review's Important 1, and this function's second correction.** The number
    returned here reshapes the published five-phase `.ill`: `rad_engine.annual.
    build_rcollate_final_argv` passes it as `rcollate -h -fa1 -ic <n_hours> -t`. The matrix being
    reshaped is built by `gendaymtx` from the `.wea` in this project's `04_skies/`. So the count
    must be measured from *that* file -- the one whose columns it is describing -- and the caller's
    `.epw` is not that file. It is external, this server never owns it (`weather_tool._load`'s own
    docstring), never re-digests it, and `drifted_inputs` is wired to the direct-sun BSDF alone, so
    nothing anywhere notices when it changes.

    Measured in the review: a `.wea` of 8760 data lines against an `.epw` since edited to 48 records
    returned **48**. Both outcomes of that are bad and neither is loud. `rcollate -h -fa1 -ic N -t`
    over a 24-value stream was measured to exit **0 with a valid, silently wrong 3x8** at `-ic 3`,
    and to exit 1 (`Bad dimension(s)`) at `-ic 5` -- so a stale count that happens to divide
    `n_sensors * n_wea_hours` publishes a reshaped result with correct-looking provenance and no
    advisory, and one that does not kills the job at the last step after the whole chain has been
    paid for. `AnnualResult.timesteps` publishes this same field for **every** method, 2-phase and
    3-phase included, so the misreport was never confined to the 5-phase path.

    **This is not a reversal of review round 1's Important 4 -- it completes it.** That finding was
    that re-reading the caller's external `.epw` made every annual call depend on a file that may
    have been moved or tidied away; the fix then was to fall back to the `.wea`. The fallback was
    the right file all along, and keeping the `.epw` as the *primary* left the dependency in place
    for every caller whose file still existed but no longer said the same thing -- the harder half
    of the same defect, because it fails silently instead of raising.

    **What was given up, deliberately.** The old docstring's argument for the `.epw` was precision:
    it is the file `weather_tool.describe` measures for its own `timesteps`, so reading it here kept
    the two numbers from disagreeing. They can now disagree, and that is correct -- `describe`
    answers "what is in the source file the caller registered", this answers "how many columns will
    the sky matrix have". When the source file has been edited since load, those are two different
    questions with two different true answers, and the old code made this one return the answer to
    the other one. The disagreement is not left to be discovered downstream either:
    `rad_engine.chain._predict_v_t_d_s` now joins this count against the **real** `smx` header on
    disk whenever one exists, which is a comparison of two independently measured facts rather than
    of a value with itself.

    The `.wea` is never external, never user-managed and never absent for a record that reaches this
    function (its presence is already checked by the caller), and it is opened through
    `open_regular_file` -- the same FIFO/symlink/device guard every other manifest-derived path in
    this module goes through (R48/R50). `WeatherNotLoadedError` is raised only if the project's own
    file cannot be read, which is the same guarantee every other reused matrix already has.
    """
    try:
        with open_regular_file(wea, "rb") as handle:
            wea_text = handle.read().decode(WEA_ENCODING, errors="surrogateescape")
    except (NotARegularFileError, OSError) as exc:
        raise WeatherNotLoadedError(
            f"weather input {epw!r} was loaded, but the .wea this project wrote for it ({wea}) "
            f"could not be read to measure its hour count: {exc} That file is what gendaymtx "
            "reads, so its line count is the column count of every sky matrix built from it, and "
            "the source .epw is deliberately not consulted as a substitute -- it can have been "
            f"edited since load. Re-load it with weather(action='load', epw=..., name={epw!r})."
        ) from exc
    return wea_hour_count(wea_text)


def _weather(loaded: LoadedProject, epw: str) -> _Weather:
    """The registered weather input `epw` names, and its `.wea`.

    `epw` is a **handle into the project**, not a filesystem path: `weather(action="load")` is what
    converts an EPW and registers it, and this tool multiplies against the `.wea` that produced.
    Taking a raw path here instead would mean this tool re-deriving a `.wea` -- a second conversion
    path beside `rad_engine.weather.write_wea`, with its own chance to disagree about the longitude
    sign that `WEA_LONGITUDE_SIGN` exists to make visible (ruling R30's "second copy is a second
    thing to drift", applied to a sign convention this project has already been bitten by).

    Matched on the bare name and the `weather_`-prefixed id alike, the two candidates
    `weather(action="describe"/"remove")` already accept, so a caller passes whichever they have.
    """
    # `weather_tool._INPUT_PREFIX`, imported rather than re-typed -- the same reason
    # `_WINDOW_GROUP_PREFIX` is imported above, and the same defect class the whole-branch fix wave
    # closed for `sensor_grid_`: the prefix was written out three times in this function alone.
    candidates = {epw, f"{_WEATHER_PREFIX}{epw}"}
    known: list[str] = []
    for record in loaded.manifest.inputs:
        if record.kind != "weather":
            continue
        name = (
            record.id[len(_WEATHER_PREFIX):]
            if record.id.startswith(_WEATHER_PREFIX)
            else record.id
        )
        known.append(name)
        if record.id in candidates:
            wea = next(
                (Path(f.path) for f in record.files if Path(f.path).suffix.lower() == ".wea"), None
            )
            if wea is None:
                raise WeatherNotLoadedError(
                    f"weather input {epw!r} is registered but carries no .wea file, so there is no "
                    "annual sky matrix to build from it. Re-load it with "
                    f"weather(action='load', epw=..., name={epw!r})."
                )
            n_hours = _measured_n_hours(wea, epw=epw)
            return _Weather(input_id=record.id, wea_path=wea, n_hours=n_hours)
    raise WeatherNotLoadedError(
        f"no weather input named {epw!r} is loaded in this project. "
        + (f"Registered: {', '.join(sorted(known))}. " if known else "")
        + "run_annual multiplies the daylight-coefficient matrix against an annual sky matrix built "
        "from a .wea, so load the EPW first with weather(action='load', epw='/path/to/file.epw')."
    )


class _ValidatedProject:
    """The project state `run_annual` needs, proven readable **before** anything is queued.

    Produced by `_validate_project` and consumed by both the `dry_run` return and the submit path,
    so the checks that decide whether a call *can* run and the code that then runs it read the same
    values rather than each resolving the manifest for themselves -- `_ValidatedScene`'s own
    contract in `point_in_time_tool`, and the second half of what makes R39 structural here.
    """

    def __init__(
        self,
        *,
        loaded: LoadedProject,
        paths: ProjectPaths,
        points_text: str,
        n_points: int,
        scene_files: list[str],
        points: tuple[tuple[float, float, float], ...] = (),
        grid_input_id: str,
        weather: _Weather,
        ground_reflectance: ProvenanceValue,
    ) -> None:
        self.loaded = loaded
        self.paths = paths
        self.points_text = points_text
        self.n_points = n_points
        self.points = points
        """The grid's sensor positions, for R846's `grid_behind_aperture` on each V matrix."""
        self.scene_files = scene_files
        self.grid_input_id = grid_input_id
        self.weather = weather
        self.ground_reflectance = ground_reflectance


def _grid_input_id(loaded: LoadedProject, grid: str) -> str:
    """The manifest id of the sensor grid `grid` names -- `grid` or `sensor_grid_<grid>`.

    The **id**, not the path: `built_from` names manifest ids, and naming the bare argument would
    record an edge that resolves to nothing for a grid registered under the prefixed form.
    `check_edges` refuses exactly that ("an edge that resolves to nothing cannot carry a change, so
    the artifact would be permanently valid"), which is the failure this function exists to avoid.
    """
    candidates = {grid, f"{SENSOR_GRID_INPUT_PREFIX}{grid}"}
    for record in loaded.manifest.inputs:
        if record.kind == "sensor_grid" and record.id in candidates:
            return record.id
    raise GridNotFoundError(
        f"no sensor grid named {grid!r} is registered in this project. Call "
        f"sensor_grid(action='create', name={grid!r}, ...) first, or sensor_grid(action='list') to "
        "see what is registered."
    )


def _validate_project(
    runtime: Runtime, *, project: str, grid: str, epw: str
) -> _ValidatedProject:
    """Every project-state refusal `run_annual` can produce, in one function, queueing nothing.

    The counterpart to `_validate_arguments`, and the half that needs the manifest: no geometry
    registered, no such grid, a grid whose `.pts` is empty or malformed, no weather loaded, the
    `!`-line scan over the registered scene, and a grid whose points are not on its recorded floor
    (R776, `GRID_NOT_ON_RECORDED_FLOOR`). Both run above the `dry_run` branch, so `dry_run`
    cannot reach a decision a real call would not have reached identically.

    **Ordering is a contract, not an accident**, and it is `point_in_time_tool._validate_scene`'s:
    geometry first, because "this project has no scene at all" is the more fundamental fact than
    "that grid does not exist"; then the grid; then the weather; then the scene scan, so the
    cheaper manifest refusals win the race to be reported; then the grid's floor (R776), last, after
    the scan as in `run_point_in_time`. *(Corrected 2026-10-03, Phase 18 Branch D, R776: it said
    "then the scene scan **last**".)*

    The scene scan is reused from `point_in_time_tool` rather than re-implemented -- it reaches
    `rad_engine.octree`'s own scanner, and a second copy is the drift ruling R30 forbids and R38's
    RCE fix would silently fail to reach.
    """
    loaded = runtime.open(project)
    paths = ProjectPaths(loaded.paths.root)

    scene_files = _scene_files(loaded, luminaires=False)
    points_path, polygon, recorded_height, recorded_digest = _grid_points_file(loaded, grid)
    # Final fix G, fix round 3 (S1, controller ruling): a grid file that resolves outside every
    # root is refused PATH_OUTSIDE_ROOT (`field="grid"`) with the words every results read refuses
    # it with, before it is read -- measured before this, with `11_sensors` linked outside the
    # root, this run planned and ran, and every read of what it sealed was refused.
    if (outside := _grid_outside_refusal(points_path, grid=grid)) is not None:
        raise outside
    try:
        points, points_text = _read_points(points_path, recorded_digest, grid=grid)
    except UnicodeDecodeError:
        # Task 4 fix round 2: `_read_points` decodes strictly after the digest check, so a `.pts`
        # whose recorded digest matches and whose bytes are not UTF-8 raised a bare `ValueError`
        # that no clause of `run_annual`'s handler chain catches -- an uncoded `ToolError`. It is
        # the same fact as a `.pts` that cannot be read, so it takes that refusal's class and code
        # (`GridUnreadableError`, `ARGUMENT_OUT_OF_RANGE`, `field="grid"`).
        raise GridUnreadableError(
            f"{points_path} cannot be read as a sensor grid: it is not UTF-8 text. Re-create it "
            "with sensor_grid(action='create')."
        ) from None
    grid_id = _grid_input_id(loaded, grid)
    weather = _weather(loaded, epw)
    _scan_scene_for_inline_commands(scene_files, cwd=paths.root)
    # R776, last, as in run_point_in_time; R784: at the floor plus the recorded height, if any.
    refuse_grid_off_recorded_floor(grid, polygon, points, height=recorded_height)

    return _ValidatedProject(
        loaded=loaded,
        paths=paths,
        points_text=points_text,
        n_points=len(points),
        scene_files=scene_files,
        points=tuple((p.x, p.y, p.z) for p in points),
        grid_input_id=grid_id,
        weather=weather,
        ground_reflectance=loaded.manifest.project.ground_reflectance,
    )


# --- the shape check, at submit time (R44(1)) ---------------------------------------------------


def _shape_check(*, n_points: int, sky_subdivision: int, receiver_path: Path) -> None:
    """Run `check_matrix_shape` over the shapes this job *will* produce, before it is queued -- from
    **two independent sources**, not one restated twice.

    **Source 1: what this call computed.** `check_matrix_shape` is engine invariant 3's own check,
    and R44 requires it *"at submit time, where its result can still become a refusal"* rather than
    after a DC matrix has been paid for. The DC matrix's `NCOLS` and the sky matrix's `NROWS`
    (`gendaymtx -m`'s) are both `reinhart_row_count` of the identical `sky_subdivision` argument --
    one function, one argument, evaluated twice. **This is a tautology, not a defect covered up: it
    cannot go red for any input**, because both sides are the same expression. It is kept because it
    is cheap and catches a wiring error in this function itself (e.g. one side computed from the
    wrong variable) before the second source below is even read -- but it proves nothing about
    whether the *file on disk* the job's `rfluxmtx` step will actually open agrees with either side.

    **Source 2: what the receiver file on disk actually declares**, read back independently via
    `rad_engine.annual.read_receiver_subdivision` rather than trusted from the in-memory
    `dc_receiver_text(sky_subdivision)` string this call built. This is the check that can go red,
    and the reason it needs to exist: `receiver_path`'s filename is derived from `sky_subdivision`
    (`rad_engine.annual.dc_receiver_filename`) precisely so two calls at different subdivisions never
    share one path, but a derived filename is a *prevention*, not a *proof* -- reading the actual
    bytes back is what turns "this collision should not happen" into "this collision did not happen
    for this call", and is the second, disk-sourced check the receiver-filename-collision finding
    asked this function to have. If `dc_receiver_filename`'s own derivation were ever reverted to a
    fixed name, this is the assertion that goes red -- not source 1, which would keep passing by
    construction regardless.

    Raises `MatrixShapeError` from either source -- source 1's own message names the predicted
    numbers; source 2 wraps `MatrixFileUnreadableError`/`ValueError` (the receiver could not be read,
    or its first line does not declare an `mf` `parse_receiver_subdivision` recognises) as a
    `MatrixShapeError` too, so `run_annual`'s single `except MatrixShapeError` clause shapes every
    failure from this function identically rather than needing a second coded path for "the receiver
    could not be verified".
    """
    dc = DcMatrixHeader(
        nrows=n_points, ncols=reinhart_row_count(sky_subdivision), ncomp=3, format="ascii"
    )
    sky = DcMatrixHeader(
        nrows=reinhart_row_count(sky_subdivision), ncols=ANNUAL_TIMESTEPS, ncomp=3, format="ascii"
    )
    check_matrix_shape(dc, sky)

    try:
        receiver_mf = read_receiver_subdivision(receiver_path)
    except (MatrixFileUnreadableError, ValueError) as exc:
        raise MatrixShapeError(
            f"the daylight-coefficient receiver at {receiver_path} could not be verified against "
            f"sky_subdivision={sky_subdivision}: {exc}"
        ) from exc
    disk_dc = DcMatrixHeader(
        nrows=n_points, ncols=reinhart_row_count(receiver_mf), ncomp=3, format="ascii"
    )
    check_matrix_shape(disk_dc, sky)


# --- the files this tool writes at submit, and the seam the chains are built through -------------


def _write_contained(path: Path, text: str, *, roots: tuple[Path, ...], what: str) -> None:
    """Write `text` to `path`, refusing anything that escapes `roots` **before** the syscall.

    **Ruling R48's primitive, applied to this tool's two writes -- finding C1.** Both were
    `Path.write_text`, which follows a symlink at the final component, and neither was contained. A
    symlink planted at `<project_root>/receiver.rad` or at `10_jobs/dry_run/points.pts` had the
    victim file outside every allowed root overwritten while the tool returned **success**, on
    `dry_run=True`, with no Radiance installed. That is R48's defect verbatim -- the ruling whose own
    finding was `write_wea` reporting `refused: true` about a breach that had already completed --
    reintroduced in the same phase the ruling landed.

    R48 is written about `rad_engine`, and these two writes are in `rad_mcp`, so its letter does not
    literally bind them. Its *reasoning* does, and the primitive is importable:
    `rad_mcp.weather_tool` already imports `resolved_parent_within` from `rad_engine.safe_io` for
    exactly this, so this is the established route rather than a new boundary crossing. The
    alternative -- a second containment helper local to `rad_mcp` -- is the second copy R30 forbids,
    and this particular logic guards an arbitrary-file overwrite, which is the worst thing to hold
    two versions of.

    **Two guards, because neither covers the other's case**, and both run before anything is
    created:

    - `resolved_parent_within` resolves the *parent chain*. `O_NOFOLLOW` refuses a symlink at the
      final component only, so a **directory** symlink at `10_jobs` walks a newly created file
      straight out of the root with no symlink at the leaf at all. Checked before `mkdir`, because
      `mkdir(parents=True)` would otherwise traverse it and create the escape itself.
    - `open_regular_file(..., "wb")` refuses a symlink at the final component (`O_NOFOLLOW`) and
      anything that is not a regular file (`S_ISREG` on the opened descriptor, never on the path,
      so there is no TOCTOU window to swap a FIFO in).

    Raises `PathOutsideRoot`, which `run_annual`'s handler chain already shapes into a
    `PATH_OUTSIDE_ROOT` refusal, and `NotARegularFileError` for a non-regular target. Nothing is
    written on either path -- the refusal precedes the syscall rather than describing it.
    """
    if not resolved_parent_within(path, roots):
        raise PathOutsideRoot(
            f"the {what} would be written to {path.parent}, which resolves outside every allowed "
            f"root. Nothing has been written. A symlinked directory in a project is the usual "
            "cause; the file this server writes must stay inside the project it belongs to."
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open_regular_file(path, "wb") as handle:
            handle.write(text.encode("utf-8"))
    except NotARegularFileError:
        raise
    except OSError as exc:
        # **`O_NOFOLLOW` reports a symlink as `ELOOP`, not as `NotARegularFileError`** -- the open
        # fails before there is a descriptor to `fstat`, so the "not a regular file" branch is
        # never reached for the very case that motivated this fix. Measured: a symlink at
        # `10_jobs/dry_run/points.pts` gives `[Errno 62] Too many levels of symbolic links`, which
        # escaped as a raw `ToolError` with an absolute path in the message -- a check-0.4
        # violation, and the same shape the refusal is meant to replace. Translated here into the
        # refusal the caller can act on, with the path named as project state rather than echoed
        # raw.
        raise PathOutsideRoot(
            f"the {what} could not be written to {path}: {exc.strerror or exc}. A symlink at that "
            "path is the usual cause -- this server refuses to write through one, because the "
            "target may be any file the server user can reach. Nothing has been written. Remove "
            "the symlink and re-run."
        ) from exc


#: The suffix of every per-call glow `.rad` (R822); `_write_per_call` is the one writer of it.
GLOW_TEMP_SUFFIX: Final = "-glow.rad"


def _write_per_call(
    directory: Path, *, prefix: str, text: str, roots: tuple[Path, ...], what: str
) -> Path:
    """Write `text` to a new file in `directory` whose name no other call shares, and return it.

    **Ruling R822 (register N-20).** The glow `.rad` files were written at fixed paths derived from
    the grid and the window group, so two concurrent submits at one grid and group, straddling a
    `window_group` re-register, raced over one `oconv` input: one call could compile its V octree
    against the other's aperture, a wrong number with no error (R141). `tempfile.mkstemp` gives each
    call its own name, so no other call can write the file this one compiles.

    Contained the way `_write_contained` contains its writes: the directory is resolved against
    `roots` before `mkdir`, and `mkstemp` creates the file with `O_CREAT | O_EXCL`, which refuses
    any existing entry at the name, a symlink included, so nothing is written through one. The
    caller removes the file once the octree that reads it is built; every such octree is frozen
    (`oconv -f`), so no later spawn reads the file.
    """
    if not resolved_parent_within(directory / "x", roots):
        raise PathOutsideRoot(
            f"the {what} would be written to {directory}, which resolves outside every allowed "
            "root. Nothing has been written. A symlinked directory in a project is the usual "
            "cause; the file this server writes must stay inside the project it belongs to."
        )
    directory.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=directory, prefix=prefix, suffix=GLOW_TEMP_SUFFIX)
    path = Path(name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(text.encode("utf-8"))
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    return path


def _remove_per_call(paths: Iterable[Path | str]) -> None:
    """Remove the per-call files `_write_per_call` wrote (R822), once their octree is built."""
    for path in paths:
        Path(path).unlink(missing_ok=True)


def _contain_chain_directory(directory: Path, *, roots: tuple[Path, ...], what: str) -> None:
    """Refuse a chain output directory that escapes `roots`, **before** any step is submitted.

    `rad_engine.blend._write_blend`'s docstring states this requirement in
    prose:

        *"So the `--out` Task 12 builds must be contained before the step is submitted, and the
        containment has to cover the sibling name too: a rule written about `out` alone would say
        nothing about the file this actually creates first."*

    **Nothing implemented it.** `grep -n "contain(" src/rad_engine/jobs.py src/rad_engine/chain.py`
    returned no containment call in either file, and this module mentioned `.results` exactly once,
    at the line that passed it straight into `ChainRequest`. Every other submit-time destination on
    this path was guarded (`rad_mcp.paths.contain_octree_destination`, `_write_contained`, seven call sites);
    `08_results` and `07_matrices` were not. On this repo's own standard -- *"a rule written down in
    prose beside code that does not obey it is worse than either"* (CONTEXT.md) -- the docstring was
    the part that could not merge as is.

    **Reproduced by that review**, `schedule._write_schedule` through a **directory symlink** at
    `08_results`::

        results dir: .../proj-63zfxxt9/08_results
        planted:     .../proj-63zfxxt9/08_results -> .../victim-g5_62_8j
        victim dir listing: ['blind_schedule_floor.json']
        escaped file exists: True   content: {"hello": "escaped"}
        victim_parent is outside root: True

    `open_regular_file`'s `O_NOFOLLOW` does not fire, because the final component is not a symlink.
    That is byte-for-byte the third reproduction `rad_mcp.paths.contain_octree_destination`'s own docstring
    records for `06_octrees`, one directory over, and it is why `resolved_parent_within` -- which
    resolves the whole parent chain -- is the primitive rather than anything the open can do.
    `blend._write_blend` has the same structure, plus the `<out>.partial` sibling it creates and
    unlinks; containing the **directory** covers the sibling by construction, which a rule written
    about `out` alone would not.

    **Directory rather than filename, deliberately.** Every path either helper writes --
    `blind_schedule_<grid>.json`, `annual_blended_<grid>.ill`, that file's `.partial` sibling, and
    the `annual_<grid>.ill` the pre-existing three-phase leg writes through an equally unguarded
    `Step.stdout_path` -- is `directory / <name>`. Containing the parent answers all of them at once
    and keeps answering when the next one is added.

    **The destination is targetable rather than merely waited for**, which is what settles the
    severity: both filenames carry `grid`, a caller-supplied argument, so an attacker chooses which
    filename in that directory is written rather than waiting for one -- the property that ranked
    `rad_engine.sensors.write_points` first among seventeen sites.

    `07_matrices` is contained on the same call for the same reason and is **not** scope creep: it
    is the identical structure one directory over, holding the `dmx`/`vmx`/`dsmx` a chain writes
    through the same unguarded `Step` mechanism. Guarding results and leaving matrices would be
    exactly the "a guard protects only what its words say" failure ruling R48 was minted for.

    Raises `PathOutsideRoot`, which `run_annual`'s handler chain shapes into a `PATH_OUTSIDE_ROOT`
    refusal.
    """
    # `resolved_parent_within` answers about a **file**'s parent chain -- it resolves `path.parent`
    # -- so the question "is this directory inside a root" is asked as "is the parent of a file in
    # it inside a root". The joined name is never touched, created or stat'd; only `.parent` is
    # read. Asking through the shared primitive rather than re-deriving `.resolve()` and the
    # `in .parents` test here is the point: one definition of what "inside a root" means.
    if not resolved_parent_within(directory / "any-file-written-here", roots):
        raise PathOutsideRoot(
            f"this run's {what} directory resolves to {directory.resolve()}, which is "
            "outside every allowed root. Nothing has been written and no job has been submitted. A "
            "symlinked directory inside the project is the usual cause; every file this chain "
            "writes must stay inside the project it belongs to."
        )


def _write_suns_scene(
    runtime: Runtime, state: _ValidatedProject, env: RadianceEnv, *, subdivision: int
) -> Path:
    """The `1 + 144*mf^2`-source suns scene, written into `03_octrees/` and returned.

    **One definition, two consumers** (R30): `Cds`'s suns octree (5-phase, Phase 7a Task 14b) and
    the ASE octree (Phase 7c Task 11) compile the identical file. It was inline in the 5-phase block
    until Task 11 needed it on the 2-phase path too, and a second copy there would have been two
    independently maintained derivations of one filename and one body of text.

    **The filename carries `mf`** -- `suns.suns_scene_filename(mf)`, exactly as
    `annual.dc_receiver_filename` carries it, and for that function's own recorded reason: the bytes
    are `1 + 144*mf^2` sources, so two calls at different subdivisions writing one shared
    `suns.rad` is the receiver collision under a different name. This write sits **above the
    `dry_run` branch**, so even a preview of one subdivision could otherwise overwrite the scene
    another call is about to compile. Two subdivisions, two paths, no collision by construction; two
    calls at the same `mf` share a path and stay benign because `suns_scene_text` is pure in `mf`.

    `cnt` piped into `rcalc`, two declared spawns and never a shell pipe (`rad_engine.suns`' own
    module docstring). The engine runs them and returns text; this layer writes it, because the
    containment primitive raises a wire type the engine may not name. Bounded by the octree build's
    own timeout rather than a second number: both are submit-time spawns on an `anyio` worker
    thread, and the bound exists to end a hang, not to cap a slow build.
    """
    path = state.paths.octrees / suns_scene_filename(subdivision)
    _write_contained(
        path,
        suns_scene_text(env, subdivision, cwd=state.paths.root, timeout=OCTREE_BUILD_TIMEOUT_S),
        roots=runtime.roots,
        what="suns scene",
    )
    return path


def _write_sample_geometry(
    request: ChainRequest,
    *,
    groups: Sequence[ThreePhaseGroup],
    roots: tuple[Path, ...],
) -> None:
    """Write one plain-`polygon` sampling surface per window group, at the paths the chain will
    name (`ChainRequest.sample_rad_paths`, `chain._sample_rad_for`).

    **Both the geometry and the modifier are formatted from `tp_group`, the group being written,
    and the two fail differently** -- fix round 2, item 5. A hoisted `SAMPLE_MODIFIER` is cosmetic
    here and `polygon_surface`'s own measured docstring says why: `genklemsamp` reads the surfaces'
    geometry and normals and never the modifier, so the wrong name on the right polygons samples the
    right directions. A hoisted **aperture** is a wrong number: group B's file would carry group A's
    polygons, `genklemsamp` would sample A's window, and B's D matrix would describe A's view of the
    sky under B's `dmx:` id -- Important 1 again, wearing the right filename this time.

    Both are pinned by test rather than left to the reading, because the loop sees one group in
    every run this server submits today and will see several the moment Task 12 lifts the narrowing.
    """
    for tp_group in groups:
        _write_contained(
            request.sample_rad_paths[tp_group.group.name],
            polygon_surface(
                tp_group.group.aperture,
                modifier=SAMPLE_MODIFIER.format(group=tp_group.group.name),
            ),
            roots=roots,
            what="job's aperture sampling geometry",
        )


class AseOctreeNames(NamedTuple):
    """What one window group's ASE build is called: its artifact id, its blacked `.rad`, its `.oct`.

    **The naming convention, in one place** (ruling R30) -- `chain.sample_rad_paths`' own posture one
    octree over, and for the same two reasons. One: three names that must agree about which group
    they are for are derived together or they drift. Two: a hoisted *id* is invisible to every test
    that only looks at the files, and a hoisted *path* is invisible to every test that only looks at
    the ids -- so both come out of one call a test can make directly, with no `oconv` in the way.
    (Measured: with the ids minted by an f-string at the call site and asserted only by a
    `@pytest.mark.radiance` test, hoisting them to one constant left `pytest -m "not radiance"`
    green -- twice, on the same line, one review round apart.)
    """

    artifact_id: str
    scene_path: Path
    octree_path: Path


def ase_octree_names(
    *, grid: str, subdivision: int, octrees_dir: Path, group_names: Iterable[str]
) -> dict[str, AseOctreeNames]:
    """`AseOctreeNames` per window group.

    **`mf` is in the octree's name and in the id, and not in the scene's** -- the suns shell's own
    rule beside it. The blacked `.rad`'s bytes depend on the scene and on which aperture is blacked,
    on neither the subdivision nor the sky; the octree compiles a `1 + 144*mf^2`-source suns scene
    into itself, so two subdivisions are two octrees rather than one row pointing at whichever ran
    last while an `asemx` from the earlier one still names it as an edge.

    **The id carries the group between the grid and the `mf`**, so it cannot be confused with the
    whole-grid `octree:ase:<grid>:mf<n>` beside it -- a different octree for a different quantity
    (every aperture open, LM-83 sec 3.3), never one of these.
    """
    return {
        name: AseOctreeNames(
            artifact_id=f"octree:ase:{grid}:{name}:mf{subdivision}",
            scene_path=octrees_dir / f"ase_{grid}_{name}.rad",
            octree_path=octrees_dir / f"ase_{grid}_{name}_mf{subdivision}.oct",
        )
        for name in group_names
    }


def _unclaimed_aperture_advisory(
    modifiers: Sequence[str],
    *,
    group_count: int,
    chain: str,
    grid: str,
    edition: Lm83Edition = DEFAULT_LM83_EDITION,
) -> Advisory:
    """`BLIND_SCHEDULE_UNCLAIMED_APERTURE` for this run -- rulings R147, R149 and R150.

    **The precondition, stated nowhere else a caller can see.** LM-83 sec 2.2.6's scan builds its
    residual as the sum of every group's direct-sun column and removes a closed group by subtracting
    its column. Both halves hold only if every direct path from a sun to a sensor crosses **exactly
    one** window group's aperture. An opening no group claims is in every column, because no
    per-group ASE octree blacks it.

    **The consequence depends on `group_count`, and this names THIS run's rather than a direction
    that may not apply to it** (ruling R149). The error at prefix `k` is `(G-k-1)*D`, exactly zero
    at `k = G-1`: at `G = 1` -- the shape every run **outside** a 3-phase `blind_control="lm83"`
    one is still narrowed to by `resolved_target.single` -- the one closure decision is honest and
    the failure is that the residual afterwards reads zero for a room still in sun. Both shapes are
    measured against the real `group_schedule`
    (`tests/test_blinds.py::TestTheSubtractionPrecondition`). The wording this replaces, *"for
    `k < G` it over-counts"*, named a direction that could not occur on **any** run able to receive
    the advisory.

    **A function rather than an inline block, for `_build_per_group_ase_octrees`' own reason**: the
    `G >= 2` arm was unreachable from the wire while every run was narrowed to one group, and a
    branch no test can execute is the "published is not reachable" failure this branch keeps
    finding. **It is reachable from the wire as of Phase 7d-1 Task 19** -- not Task 12, which is
    what this paragraph named until the Task 19 review measured it: `_dispatch` skips
    `resolved_target.single` on a blended run, and `TestTheBlendFansOutOverEveryWindowGroup`
    asserts a two-group plan. Called directly with either count, both arms were assertable before
    that too.

    **Candidates, not paths** (ruling R150). The detector reads material *types* off the scene text;
    whether any of them lies on a path from a sun to a sensor is what the text cannot answer. So the
    message names what it legitimately fires on, says it is necessary and not sufficient in **both**
    directions, and does not tell a caller to define a window group over an interior partition.
    """
    if group_count == 1:
        shape = (
            "This run resolved 1 window group, and with one group the closure decision itself is "
            "unaffected -- measured against the real scan over 20 000 randomised draws, 0 "
            "disagreements with an honest one. What changes is what the scan believes afterwards: "
            "closing that group subtracts this light along with the group's own beam, so the "
            "residual reads zero while the room still carries it. The schedule then reports the "
            "criterion met when no combination of blinds can meet it, because this light comes "
            "through something no blind covers."
        )
    else:
        shape = (
            f"This run resolved {group_count} window groups, so while any of them is still open "
            "the residual carries this light once per open group instead of once: the scan closes "
            "at least as many groups as an honest one and sometimes strictly more (measured "
            "against the real scan, 1 108 disagreements in 20 000 draws at 2 groups, every one of "
            "them in that direction), which retains less daylight and understates sDA. Once every "
            "group is closed the residual reads zero while the room still carries this light, and "
            "the criterion is reported met for a room still in direct sun."
        )
    return Advisory(
        code="BLIND_SCHEDULE_UNCLAIMED_APERTURE",
        severity="warn",
        field="blind_control",
        message=(
            "the registered scene transmits light through modifier(s) "
            f"{', '.join(repr(m) for m in modifiers)}, which no window group claims. This "
            f"server's blind schedule ({section('blind_trigger', edition)}) sums every window "
            "group's direct-sun column and "
            "removes a closed group by subtracting its own column, which holds only if every "
            "direct path from a sun to a sensor crosses exactly one window group's aperture. "
            + (
                "Light through an opening no group claims is in EVERY group's column, because no "
                "per-group direct-sun run blacks it. "
                if chain == "3phase"
                # Final review A2: a five-phase run plans no per-group direct-sun column
                # (`chain.direct_sun_steps`), so the columns this names are a three-phase run's.
                else "This run is five-phase and traces no per-group direct-sun column; on a "
                "three-phase blind_control='lm83' run of this scene, light through an opening no "
                "group claims would be in EVERY group's column, because no per-group direct-sun "
                "run blacks it, and what follows is what that run's scan would do. "
            )
            + f"{shape} "
            "THIS IS A LIST OF CANDIDATES, NOT OF PATHS: it reads material types off the scene, "
            "and whether any of these lies on a path from a sun to a sensor is not answerable from "
            "the text. It fires on ordinary correct scenes -- an interior glass partition, a "
            "dielectric tabletop, water, a glazed internal door -- and none of those is a defect. "
            "It is also silent on cases that are: an opening reached through an alias, a "
            "transmitting type outside the list it checks, a HOLE (an absent wall or a dropped "
            "!xform include admits sun through no aperture at all, which is the same arithmetic), "
            "and apertures in series, which is a separate undetected case. So: check whether any "
            "modifier named above can carry direct sun to a sensor. If one is operable "
            "fenestration the schedule should be able to close, define a window group over it "
            "(window_group(action='define')). If it is interior glazing, furniture or a fixed "
            "opening, this warning does not apply to it and nothing needs changing. "
            + (
                # Final review A2: the old sentence said rad_core.blinds.group_schedule had no
                # caller; rad_engine.schedule calls it and seals 'sched:<grid>', which the blend
                # reads, so a three-phase lm83 run's published per-group numbers carry the error.
                f"The schedule these columns feed is sealed as 'sched:{grid}' "
                "(rad_engine.schedule, through rad_core.blinds.group_schedule) and decides the "
                f"blend 'illblend:{grid}', so the per-group states "
                "results(action='blind_schedule') publishes and any sDA read from the blend "
                "carry this error; the whole-grid "
                f"states and ASE, both from 'illdir:{grid}' with every aperture open, do not"
                if chain == "3phase"
                else "This five-phase run seals no schedule (rad_mcp.annual_tool._plans_blend), "
                "so no number this run publishes is affected"
            )
            + " -- see rad_core.blinds.group_schedule for the measurements and for what stays "
            "undetected."
        ),
    )


def _unclaimed_glazing_advisory(
    loaded: LoadedProject, env: RadianceEnv, *, project: str, grid: str, chain: str,
    blended: bool, metrics: Sequence[str] = (),
) -> Advisory | None:
    """`UNCLAIMED_GLAZING_NOT_COUNTED` (warn, `field="project"`), or `None` (final fix E1(c)).

    **Measured** (`tests/test_annual_tool.py::TestAResultBuiltForOtherWindowGroupsIsRefused`): the
    V-matrix octree is the scene plus one glow surface per window group and no sky, so a ray that
    leaves through glazing no group claims adds zero, and the three- or five-phase result omits
    that light; on the office scene with its south glazing split and the east half claimed by no
    group, the two-phase mean was 2 337 lux with that half glass and 1 202 lux with it opaque,
    while the three-phase means were 434 and 401 lux. `BLIND_SCHEDULE_UNCLAIMED_APERTURE` says so
    only on a `blind_control='lm83'` run (and its dry run), and about the schedule; this is said on
    every three- and five-phase run and its dry run, about the result. *(Corrected, fix round 1,
    review M1: it said "only on a blind_control='lm83' run, after the dry run"; that advisory is
    appended above `if dry_run`.)*

    The candidates are `rad_engine.scene.unclaimed_glazing_modifiers` over the registered materials
    and geometry, flattened once (`results_tool._glazing_in_no_window_group`, the read
    `results`' EN 17037 class makes), against every group's aperture. Like that read it lists
    material types, not paths, so an interior glass partition is listed too, and the message says
    so. A scene that cannot be flattened or parsed gives no advisory here; the octree build after it
    refuses such a scene."""
    from rad_mcp.results_tool import _glazing_in_no_window_group

    records = [record for record in loaded.manifest.inputs if record.kind == "window_group"]
    try:
        unclaimed = _glazing_in_no_window_group(loaded, env, records)
    except (ValueError, OSError):
        return None
    if not unclaimed:
        return None
    named = ", ".join(repr(name) for name in unclaimed)
    phase = "five-phase" if chain == "5phase" else "three-phase"
    sealed = (
        f"the blend 'illblend:{grid}' and its per-group operands" if blended
        else f"'ill5:{grid}'" if chain == "5phase" else f"'ill:{grid}'"
    )
    message = (
        f"the registered scene has surfaces whose modifier(s) {named} transmit light and no window "
        f"group claims. A {phase} run counts daylight only through the glazing of window groups: "
        "each group's view matrix traces the room to that group's aperture alone, with no sky, so "
        f"light through these surfaces is in no part of {sealed}, and every value read from it "
        "omits that light. THIS IS A LIST OF CANDIDATES, NOT OF PATHS: it reads material types off "
        "the scene, so an interior glass partition or a glazed internal door is listed too, and "
        "nothing is lost through one. If one is fenestration, "
        + _unclaimed_glazing_routes(
            unclaimed, project=project, chain=chain, blended=blended, metrics=metrics,
        )
    )
    return Advisory(
        code="UNCLAIMED_GLAZING_NOT_COUNTED", severity="warn", field="project", message=message,
    )


def _unclaimed_glazing_routes(
    unclaimed: Sequence[str], *, project: str, chain: str, blended: bool,
    metrics: Sequence[str] = (),
) -> str:
    """`UNCLAIMED_GLAZING_NOT_COUNTED`'s routes, only those that succeed on this chain (final fix E,
    fix round 1, review I2; each measured through the real tool,
    `tests/test_annual_tool.py::TestAResultBuiltForOtherWindowGroupsIsRefused`). A two-phase run
    traces every aperture on every chain. A second window group is read only by a three-phase
    `blind_control='lm83'` run -- `ThreePhaseTarget.single` refuses more than one group on a
    three-phase run without it and on a five-phase run under every `blind_control` -- and that run
    needs 'open' and 'closed' bound on every group (`BlendStatesNotBoundError`)."""
    aperture = repr([unclaimed[0]]) if len(unclaimed) == 1 else "[<one of those modifiers>]"
    define = (
        f"window_group(action='define', project={project!r}, name=<a name>, aperture={aperture})"
    )
    # With 'ase' asked, rule 1 of `rad_core.method` keeps the five-phase chain and refuses
    # method='2phase' (METHOD_OVERRIDE_UNSAFE), and 'sda' alone is refused (SDA_REPORTED_ALONE's
    # run refusal), so both go and 'da' stands in; blind_control='lm83' plans the direct-sun leg
    # compliance(standard='lm83') reads ASE from on any chain (measured on two-phase).
    kept = [metric for metric in metrics if metric not in ("sda", "ase")]
    instead = kept if "da" in kept else ["da", *kept]
    two_phase = (
        "make this call with method='2phase', which traces every aperture of the scene, claimed "
        "or not"
        + (
            f", with metrics={instead!r} and blind_control='lm83', which plans the direct-sun "
            "leg compliance(standard='lm83') reads sDA and ASE from (with 'ase' in metrics the "
            "five-phase chain is kept and method='2phase' refused)"
            if "ase" in metrics else ""
        )
    )
    bind = (
        "bind its 'open' and 'closed' states with window_group(action='assign_bsdf', ...)"
    )
    if blended:
        return (
            f"either define a window group over it -- {define} -- {bind} and make this call "
            f"again, or {two_phase}."
        )
    if chain == "5phase":
        return (
            f"{two_phase}. A five-phase run resolves exactly one window group under every "
            "blind_control, so a window group over it is read only by a three-phase "
            f"blind_control='lm83' run: define it -- {define} -- {bind}, bind both on every other "
            "group too, and make this call with method='3phase' and blind_control='lm83'"
            # Fix round 3 (review I2): with 'ase' asked, method='3phase' is refused
            # METHOD_OVERRIDE_UNSAFE just as method='2phase' is, so the same metrics stand in.
            + (f", with metrics={instead!r}" if "ase" in metrics else "")
            + "."
        )
    return (
        f"either {two_phase}, or define a window group over it -- {define} -- {bind}, bind both "
        "on every other group too, and make this call with blind_control='lm83': a three-phase run "
        "without it resolves exactly one window group."
    )


class PerGroupAseOctrees(NamedTuple):
    """What `_build_per_group_ase_octrees` compiled, and the one precondition it could check.

    `unclaimed_transmitting` is ruling R147's checkable half: modifiers in the registered scene that
    transmit light and that **no** window group claims. It comes back from here rather than from a
    second scan in `_dispatch` because this function has already paid for the `xform` flatten, and a
    second flatten is a second spawn over the same bytes for the same answer (R30).
    """

    ids: dict[str, str]
    paths: dict[str, Path]
    unclaimed_transmitting: tuple[str, ...]
    digests: dict[str, str]
    """Each octree's `octree_body_digest`, keyed by `str(path)`, taken right after its own build
    (R822): what the job's steps are checked against before they spawn."""
    compiled: dict[str, dict[str, Any]]
    """R849: each octree's `compiled_scene_record`, keyed by artifact id, taken right after its own
    build: the flattened per-group scene, then the suns scene."""
    flattened_text: str = ""
    """The registered scene flattened through `xform`, which the per-group scenes were blacked
    from: `_build_ase_net_octree` blacks the ASE_net scene from the same text rather than paying
    for a second flatten of the same bytes (R30)."""


def _build_per_group_ase_octrees(
    env: RadianceEnv,
    *,
    groups: Sequence[ThreePhaseGroup],
    scene_files: Sequence[str],
    suns_scene: str,
    grid: str,
    subdivision: int,
    octrees_dir: Path,
    cwd: Path,
    roots: tuple[Path, ...],
    edition: Lm83Edition = DEFAULT_LM83_EDITION,
    geometry_remedy: str = _GEOMETRY_ADD_TEMPLATE,
) -> PerGroupAseOctrees:
    """One blacked ASE scene and one ASE octree per window group; their ids and paths, by name.

    **What "blacked" means here** (`rad_engine.scene.build_ase_scene`): for group `g`'s octree,
    every **other** group's aperture polygons take an opaque black modifier. Not deleted -- deleting
    them leaves a hole in the envelope and lets the sun through where the window was, which is a
    strictly larger direct-sun answer than either the group open or the group closed, and the one
    outcome that cannot be right.

    **Why per group at all.** LM-83 section 2.2.6's residual asks how many points are still in sun
    *with group g closed*, and one direct-sun run over every aperture cannot answer it (ruling
    R116). The whole-grid ASE octree the caller compiled beside these is a different quantity --
    section 3.3's pre-blind ASE, every aperture open -- and neither substitutes for the other.

    **A function rather than a block inside `_dispatch`, and that is testability rather than
    tidiness** -- `_write_sample_geometry`'s own reason, one octree over. `annual_tool` narrows
    every run **except a 3-phase `blind_control="lm83"` one** to a single window group
    (`ThreePhaseTarget.single`), so until Phase 7d-1 Task 19 the loop below saw one group in
    production and the *"two groups get two different octrees"* property -- the property Task 7's
    review found violated three times in one task -- was asserted by nothing on the wire. Task 19,
    not Task 12, is what lifted that narrowing. Called directly with two groups, it was assertable
    before either.

    Every per-group thing is per group and each is derived from `tp_group` alone: the blacked scene
    text, the `.rad` it is written to, the `.oct` compiled from it, and the artifact id that names
    it. A hoisted one of any of these is a valid octree measuring another facade's window.

    Raises `AseSceneNotBlackedError` if another group's aperture modifiers match no primitive in
    the flattened scene -- see that exception, and `SunsShellNotStrippedError`, its sibling.
    """
    ase_octree_ids: dict[str, str] = {}
    ase_octree_paths: dict[str, Path] = {}
    digests: dict[str, str] = {}
    compiled: dict[str, dict[str, Any]] = {}
    naming = ase_octree_names(
        grid=grid, subdivision=subdivision, octrees_dir=octrees_dir,
        group_names=[tp_group.group.name for tp_group in groups],
    )
    modifiers_by_group = {
        tp_group.group.name: sorted(
            {polygon.modifier for polygon in tp_group.group.aperture.polygons}
        )
        for tp_group in groups
    }
    # **Flattened first, and that is not optional.** A registered scene may name its
    # geometry through an `!xform` include chain, so a blacker that read the top-level files
    # would leave any aperture polygon living one level down open -- R33's own indirection,
    # and the same reason the suns shell is flattened. `modifiers=()` removes nothing: this
    # call is here for the flatten and for the `!`-line scan it runs on the way.
    flattened = scene_without_modifiers(
        env,
        scene_files,
        modifiers=(),
        cwd=cwd,
        timeout=OCTREE_BUILD_TIMEOUT_S,
    )
    for tp_group in groups:
        group_name = tp_group.group.name
        blacked = build_ase_scene(
            flattened.text, target_group=group_name, groups=modifiers_by_group
        )
        # **The success condition, inspected rather than assumed** -- the whole-branch
        # review's IMPORTANT 4 one octree over, and `build_ase_scene` returns the evidence
        # for exactly this. A modifier that matches no primitive blacks nothing *silently*,
        # and this run would then measure the beam through every aperture while sealing it
        # under one group's id: the residual would be zero for every group, every hour, and
        # `sda` would publish an unshaded year with no error anywhere.
        expected = {
            modifier
            for name, modifiers in modifiers_by_group.items()
            if name != group_name
            for modifier in modifiers
        }
        missing = sorted(expected - set(blacked.blacked_modifiers))
        if missing:
            raise AseSceneNotBlackedError(
                f"the per-group ASE scene for window group {group_name!r} could not be "
                f"built: the aperture modifier(s) {', '.join(repr(m) for m in missing)}, "
                "which belong to the other window group(s) this run must close, matched no "
                "primitive in the registered scene once it was flattened through `xform`, "
                "so nothing was blacked for them. This group's direct-sun run would measure "
                f"the beam through those apertures too, the {section('blind_trigger', edition)} "
                "residual "
                "would come out the same for every group, and the schedule would leave "
                "blinds open on a plausible number with no error. Add this project's "
                f"geometry again so the window groups' polygons and the scene files agree -- "
                f"{geometry_remedy} -- and re-run."
            )
        # **The escape nobody has taken, written down rather than left to be rediscovered** (the
        # project owner's decision -- recorded here, deliberately NOT implemented). Production is
        # G = 1 on every run **except a 3-phase `blind_control="lm83"` one**, which Phase 7d-1
        # Task 19 stopped narrowing through `ThreePhaseTarget.single`. On the narrowed runs
        # `blacked.blacked_modifiers` is `()` -- there is no other aperture to close -- and this
        # octree is compiled from a scene that is byte-identical to the whole-grid one, so such an
        # `lm83` run pays **two** `rcontrib`s over the same geometry for two matrices that must
        # agree cell for cell. On a blended two-group run the blacking is real and the saving is
        # not available at all, which is a second reason not to take the escape.
        #
        # The escape is one line here: when `blacked.blacked_modifiers == ()` the per-group run is
        # the whole-grid run, so skip the compile and let the group's `asemx`/`illdir` come from the
        # whole-grid octree. **What it costs, which is why it is not taken here.** (1) Two artifact
        # ids would name one octree and, downstream, `illdir:<grid>:<g>` and `illdir:<grid>` would
        # want one path -- the two-ids-one-path data loss `refuse_colliding_outputs` exists for --
        # so a consumer-side fallback ("no per-group id? read the grid's") has to be built and
        # tested first, in `results_tool` and in Task 11's `sched`. (2) The condition is a property
        # of *this call's* group set, not of the project, so a run that later gains a second group
        # rebuilds what the elided run never sealed, and `reuse_decision` cannot see the difference.
        # (3) It makes the cheap path the untested one: the two-group case would then be the only
        # one that compiles anything here. None of that is hard; all of it is a task.
        #
        # **Every name for this group comes from one call** (`ase_octree_names`, R30): the blacked
        # `.rad`, the `.oct` and the artifact id that names it. Three separate f-strings here is how
        # one of them comes to say a different group from the other two.
        names = naming[group_name]
        scene_path = names.scene_path
        _write_contained(
            scene_path,
            "## The per-group ASE scene -- rad_mcp.annual_tool._dispatch, LM-83 section\n"
            "## 2.2.6. The registered scene, flattened through `xform`, with every other\n"
            "## window group's aperture polygons re-modified to an opaque black rather\n"
            "## than removed: removing them leaves a hole in the envelope and lets the sun\n"
            "## through where the window was, which is larger than either the group open\n"
            "## or the group closed.\n"
            f"## Window group left open: {group_name}\n"
            f"## Blacked {len(blacked.blacked)} primitive(s) under "
            f"{', '.join(blacked.blacked_modifiers) or '(no other group)'}: "
            f"{', '.join(blacked.blacked) or '(none)'}\n\n"
            + blacked.text,
            roots=roots,
            what="per-group ASE scene",
        )
        group_octree_path = names.octree_path
        contain_octree_destination(group_octree_path, roots=roots)
        ase_octree_ids[group_name] = names.artifact_id
        ase_octree_paths[group_name] = group_octree_path
        build_ase_octree(
            env,
            [str(scene_path)],
            suns_scene,
            out_path=group_octree_path,
            cwd=cwd,
            timeout=OCTREE_BUILD_TIMEOUT_S,
        )
        digests[str(group_octree_path)] = octree_body_digest(group_octree_path)
        compiled[names.artifact_id] = compiled_scene_record(
            [str(scene_path), suns_scene], cwd=Path(cwd), raypath=env.raypath_dirs(Path(cwd)),
        )
    # **Ruling R147's checkable half, computed off the flatten this function already paid for.**
    # An opening that transmits and belongs to no window group is in **every** per-group column --
    # nothing blacks it in any of them -- and it breaks the precondition LM-83 sec 2.2.6's
    # subtraction rests on. `rad_core.blinds.group_schedule` carries the measured consequences and
    # `_dispatch` turns this into `BLIND_SCHEDULE_UNCLAIMED_APERTURE`.
    unclaimed = unclaimed_transmitting_modifiers(
        flattened.text,
        claimed={
            modifier for modifiers in modifiers_by_group.values() for modifier in modifiers
        },
    )
    return PerGroupAseOctrees(
        ids=ase_octree_ids, paths=ase_octree_paths, unclaimed_transmitting=unclaimed,
        digests=digests, compiled=compiled, flattened_text=flattened.text,
    )


def ase_net_octree_names(*, grid: str, subdivision: int, octrees_dir: Path) -> AseOctreeNames:
    """What the ASE_net build is called (R869): `octree:asenet:<grid>:mf<n>`, `asenet_<grid>.rad`
    and `asenet_<grid>_mf<n>.oct` -- `ase_octree_names`' rule for one octree more, minted in one
    place for that function's reason. `mf` is in the octree's name and id and not in the scene's,
    as there. The `asenet` stem keeps every name apart from a per-group octree's, whatever the
    group is called: those are `ase_<grid>_<g>...` and `octree:ase:<grid>:<g>:mf<n>`.
    """
    return AseOctreeNames(
        artifact_id=f"octree:asenet:{grid}:mf{subdivision}",
        scene_path=octrees_dir / f"asenet_{grid}.rad",
        octree_path=octrees_dir / f"asenet_{grid}_mf{subdivision}.oct",
    )


class AseNetOctree(NamedTuple):
    """What `_build_ase_net_octree` compiled, and what the chain's net leg needs of it (R869)."""

    artifact_id: str
    path: Path
    groups: tuple[str, ...]
    """The window groups marked automated, sorted: the ones blacked."""
    apertures: tuple[Aperture, ...]
    """One aperture per group in `groups`, derived from the flattened scene the octree was
    blacked from -- what the net probe closes (`AseOcclusionProbe.closed`)."""
    digest: str
    """The octree's `octree_body_digest`, taken right after its build (R822)."""
    compiled: dict[str, Any]
    """Its `compiled_scene_record` (R849): the ASE_net scene, then the suns scene."""


def _build_ase_net_octree(
    env: RadianceEnv,
    *,
    groups: Sequence[WindowGroup],
    flattened_text: str,
    suns_scene: str,
    grid: str,
    subdivision: int,
    octrees_dir: Path,
    cwd: Path,
    roots: tuple[Path, ...],
    geometry_remedy: str = _GEOMETRY_ADD_TEMPLATE,
) -> AseNetOctree:
    """The ASE_net scene and octree (R869): the registered scene with every aperture polygon of the
    window groups marked automated blacked and every other group's left open, compiled with the
    suns scene as the whole-grid ASE octree is (`rad_engine.scene.build_ase_net_scene`).

    `groups` is **every** window group of the project (`_window_groups`), not a resolved target's:
    a two-phase run resolves no target and its marked groups are blacked all the same, and every
    unmarked group must be named so that a modifier shared with a marked one is refused rather than
    blacked (`_refuse_shared_aperture_modifiers` refuses it first, with the whole remedy).

    **The apertures the net probe closes come from `flattened_text`, the scene this octree was
    blacked from.** The probe strips the same registered files, flattened through the same `xform`
    (`rad_engine.jobs`), and lays a black surface over each closed polygon; a polygon taken from
    any other reading could differ from the scene's own in identifier or vertex, and the probe's
    coincidence check compares vertices within `_SAME_VERTEX_TOLERANCE`. So each marked group's
    aperture is `derive`d here from the flattened scene's polygons that carry its modifiers --
    exactly the polygons `build_ase_net_scene` blacked.

    Raises `AseSceneNotBlackedError` when a marked group's modifier matches no primitive in the
    flattened scene, for that exception's reason: the ASE_net run would trace the group open.
    """
    blacked = tuple(sorted(group.name for group in groups if group.automated))
    modifiers_by_group = {
        group.name: sorted({polygon.modifier for polygon in group.aperture.polygons})
        for group in groups
    }
    net = build_ase_net_scene(
        flattened_text, blacked_groups=blacked, groups=modifiers_by_group
    )
    expected = {modifier for name in blacked for modifier in modifiers_by_group[name]}
    missing = sorted(expected - set(net.blacked_modifiers))
    if missing:
        raise AseSceneNotBlackedError(
            "the ASE_net scene could not be built: the aperture modifier(s) "
            f"{', '.join(repr(m) for m in missing)}, which belong to the window group(s) marked "
            "automated, matched no primitive in the registered scene once it was flattened "
            "through `xform`, so nothing was blacked for them. The ASE_net run would trace those "
            "openings open and publish ASE_gross under ASE_net's name. Add this project's geometry "
            "again so the window groups' polygons and the scene files agree -- "
            f"{geometry_remedy} -- and re-run."
        )
    flattened_polygons = parse_polygons(flattened_text)
    apertures = tuple(
        derive([
            polygon for polygon in flattened_polygons
            if polygon.modifier in modifiers_by_group[name]
        ])
        for name in blacked
    )
    names = ase_net_octree_names(grid=grid, subdivision=subdivision, octrees_dir=octrees_dir)
    _write_contained(
        names.scene_path,
        "## The ASE_net scene -- rad_mcp.annual_tool._dispatch (R869). The registered scene,\n"
        "## flattened through `xform`, with every aperture polygon of the window groups marked\n"
        "## automated re-modified to an opaque black and every other group's left open.\n"
        f"## Window groups blacked: {', '.join(blacked)}\n"
        f"## Blacked {len(net.blacked)} primitive(s) under "
        f"{', '.join(net.blacked_modifiers)}: {', '.join(net.blacked) or '(none)'}\n\n"
        + net.text,
        roots=roots,
        what="ASE_net scene",
    )
    contain_octree_destination(names.octree_path, roots=roots)
    build_ase_octree(
        env,
        [str(names.scene_path)],
        suns_scene,
        out_path=names.octree_path,
        cwd=cwd,
        timeout=OCTREE_BUILD_TIMEOUT_S,
    )
    return AseNetOctree(
        artifact_id=names.artifact_id,
        path=names.octree_path,
        groups=blacked,
        apertures=apertures,
        digest=octree_body_digest(names.octree_path),
        compiled=compiled_scene_record(
            [str(names.scene_path), suns_scene], cwd=Path(cwd),
            raypath=env.raypath_dirs(Path(cwd)),
        ),
    )


def _write_glow_surfaces(
    state: _ValidatedProject,
    *,
    grid: str,
    groups: Sequence[ThreePhaseGroup],
    roots: tuple[Path, ...],
) -> list[str]:
    """Write one `glow` aperture surface per window group and return the paths `build_octree` adds
    to the **interior** octree.

    **Ruling R140, and it is Important 1's unfixed sibling.** This wrote *one* file, from *one*
    group's aperture, under *one* `GLOW_MODIFIER.format(group=...)` -- while `chain._v_step` asks
    `rcontrib -m wg_<g>_glow` **per group**. A second group's V therefore restricts contribution to
    a modifier no surface in the octree carries, and `rcontrib` answers with an all-zero matrix: not
    an error, not a missing file, a correctly shaped V full of zeros, and `I = sum_g V_g*T_g*D_g*S`
    then reports that facade as contributing **nothing**.

    **It is the mirror of the hazard `GLOW_MODIFIER`'s own comment warns about, not the same one.**
    That one is a shared modifier making every V capture every window -- N times too bright. This
    one is a per-group modifier with no matching surface -- one group too dark and the rest right.
    Both are valid files with wrong answers, and neither raises.

    **All of them go into the one interior octree, and here is what actually makes that correct.**
    `rcontrib` attributes contribution by modifier name, so group `g`'s trace gathers only what
    struck `wg_g_glow`: the other groups' glow surfaces are in the octree but contribute nothing to
    `V_g`, which is exactly the separation `I = sum_g V_g*T_g*D_g*S` needs. One octree per group
    would be N compilations of one building to obtain the same N matrices.

    **A justification this docstring carried until fix round 3, and it was false:** that the extra
    surfaces make each aperture a *boundary* rather than *a hole rays pass through*. Both halves are
    wrong. `glow` is a light-type material that **terminates** a ray, not a transparent one, and the
    apertures were never holes -- the registered scene's own glazing polygon is already there, and
    `glow_surface` is emitted **undisplaced, exactly coincident with it** (`rad_engine.klems.
    glow_surface`, the "Emitted at the aperture's own vertices" paragraph). What
    N > 1 adds is N-1 further coincident surfaces, each competing for a tie that paragraph records
    Radiance does not define -- which is a real thing to know about this scene, and it is not what
    the conclusion rests on. The conclusion rests on the modifier, above.

    **Not live before Task 12** -- `_dispatch` still narrows to one group -- and fixed here anyway,
    because Task 12 is precisely the commit that lifts the narrowing, and it should not open onto a
    V already known to be wrong.

    The `.rad` files are transient inputs to `oconv` rather than sealed artifacts, and the octree
    is what the manifest records. *(R822:)* each is written under a name unique to this call
    (`_write_per_call`, prefix `.annual-<grid>-<group>-`), and the caller removes every one once
    the interior octree is built. If a write fails part-way, the files already written are removed
    before the error propagates.
    """
    paths: list[str] = []
    try:
        for tp_group in groups:
            # **Both the geometry and the modifier come from `tp_group`, the group being written.**
            # Hoisting either one is a silent wrong answer and they fail differently: a hoisted
            # modifier gives one name to several surfaces (every V captures every window); a hoisted
            # *aperture* writes one facade's polygons under another facade's name, so group B's V
            # measures group A's window and reports it as B's.
            glow_path = _write_per_call(
                state.paths.octrees,
                prefix=f".annual-{grid}-{tp_group.group.name}-",
                text=glow_surface(
                    tp_group.group.aperture,
                    modifier=GLOW_MODIFIER.format(group=tp_group.group.name),
                ),
                roots=roots,
                what="V-matrix aperture glow surface",
            )
            paths.append(str(glow_path))
    except BaseException:
        _remove_per_call(paths)
        raise
    return paths


def _chain_request(
    state: _ValidatedProject,
    *,
    grid: str,
    parameters: QualityParameters,
    sky_subdivision: int,
    north: float,
    octree_id: str,
    octree_path: Path,
    exterior_octree_id: str,
    exterior_octree_path: Path,
    job_dir: Path,
    sample_groups: Sequence[ThreePhaseGroup] = (),
    target: ThreePhaseGroup | None = None,
    black_octree_id: str | None = None,
    black_octree_path: Path | None = None,
    suns_octree_id: str | None = None,
    suns_octree_path: Path | None = None,
    ase_octree_id: str | None = None,
    ase_octree_path: Path | None = None,
    ase_octree_ids: Mapping[str, str] = MappingProxyType({}),
    ase_octree_paths: Mapping[str, Path] = MappingProxyType({}),
    ase_suns: tuple[SunPosition, ...] | None = None,
    ase_suns_scene: Path | None = None,
    ase_scene_digest: str | None = None,
    ase_net_octree_id: str | None = None,
    ase_net_octree_path: Path | None = None,
    ase_net_groups: tuple[str, ...] = (),
    ase_net_apertures: tuple[Aperture, ...] = (),
) -> ChainRequest:
    """`_ValidatedProject` and this call's resolved arguments, as the one type `rad_engine.chain`
    takes.

    **The seam, and the reason it has to exist.** `_ValidatedProject` is a protocol-layer aggregate:
    it holds `ProjectPaths`, the points text read off the grid, and the `_Weather` record this module
    resolved. `rad_engine.chain` cannot name any of that without importing `rad_mcp`, which inverts
    CONTEXT.md's Layers rule (`tests/test_purity.py::TestTheEngineDoesNotImportTheProtocolLayer`
    asserts it by AST). So this function unpacks the aggregate into `ChainRequest`, which carries
    engine and `rad_core` types only, and that dataclass is the whole of what the two layers share.

    **The job-local paths are resolved here rather than inside the chain**, because two of the three
    are written before any `Step` exists and the write has to go through `_write_contained` --
    ruling R48's containment primitive, which raises `rad_mcp.paths.PathOutsideRoot`, a wire type the
    engine may not name. Resolving them once, here, is what keeps the file this module writes and the
    file the chain names as `stdin_path` one path rather than two derivations of it (R30).

    **The five 5-phase fields default to `None` and are passed together or not at all (Task 14b).**
    `target` is the `ThreePhaseGroup` `_dispatch` already narrowed to, for both 3-phase and 5-phase
    alike, and the two octree id/path pairs are what its own submit-time block compiled a few lines
    earlier. They
    are optional here rather than required because a 2-phase call has no window group to resolve and
    builds neither octree -- and `five_phase_steps` raises rather than reading through a half-populated
    request, which is why `_dispatch` fills all five from one branch. Before Task 14b nothing set any
    of them and no production call reached `five_phase_steps` at all.

    **The two chain-wide output directories are contained here, and this is the one place they can
    be.** See `_contain_chain_directory` for what was reproduced without it. The check sits before
    the `ChainRequest` is built rather than beside each `Step`, because every path the chain names
    in either directory is `directory / <filename>` and they therefore share one parent: one check
    answers all of them, and a check written per filename would be a rule that has to be re-applied
    to the next artifact somebody adds. It runs on `dry_run` as well as on a submit, for the same
    reason `rad_mcp.paths.contain_octree_destination` does -- a preview that reports a path outside every root
    has already answered wrongly.
    """
    _contain_chain_directory(state.paths.results, roots=current().roots, what="results")
    _contain_chain_directory(state.paths.matrices, roots=current().roots, what="matrices")
    return ChainRequest(
        loaded=state.loaded,
        root=state.paths.root,
        grid=grid,
        grid_input_id=state.grid_input_id,
        scene_files=state.scene_files,
        octree_id=octree_id,
        octree_path=octree_path,
        exterior_octree_id=exterior_octree_id,
        exterior_octree_path=exterior_octree_path,
        points_path=job_dir / "points.pts",
        # **One sampling file per window group, keyed by name (Phase 7d-1 Task 7, review round 1's
        # Important 1).** It was one `aperture_sample.rad` for the whole job, written from one
        # group's aperture and named by every group's `genklemsamp` -- so a second group's rays were
        # sampled over the first group's polygons and its daylight matrix described the wrong
        # facade, at the right shape, with no error. Built here, in the seam, so the dict the writer
        # loops over and the dict the chain looks up in are one object rather than two derivations
        # of one filename convention (R30) -- which is the whole reason these paths are resolved by
        # the writer at all (`ChainRequest`'s own docstring).
        #
        # `sample_groups` is empty for a 2-phase run, which samples nothing.
        sample_rad_paths=sample_rad_paths(
            job_dir, (tp_group.group.name for tp_group in sample_groups)
        ),
        job_dir=job_dir,
        n_sensors=state.n_points,
        grid_points=state.points,
        parameters=parameters,
        weather_input_id=state.weather.input_id,
        weather_path=state.weather.wea_path,
        ground_reflectance=state.ground_reflectance,
        north=north,
        mf=sky_subdivision,
        matrices_dir=state.paths.matrices,
        results_dir=state.paths.results,
        # **Task 14's own instruction, from `ChainRequest.n_hours`'s docstring.** `state.weather`
        # carries the real hour count measured off the registered `.epw` (`_weather`, above); passed
        # here rather than left at `ChainRequest`'s own `ANNUAL_TIMESTEPS` default so the eventual
        # 5-phase `rcollate -ic n_hours -t` reshapes into what this run's weather actually contains,
        # never a hardcoded 8760 regardless of the file. Harmless for 2-phase and 3-phase, which
        # never read this field (`two_phase_steps`/`three_phase_steps`'s own signatures).
        n_hours=state.weather.n_hours,
        target=target,
        # R781 (owner decision E1): `-n` = cores - 1 for each rcontrib/rfluxmtx step, recorded as
        # `n` on its artifact.
        processes=worker_cores(),
        black_octree_id=black_octree_id,
        black_octree_path=black_octree_path,
        suns_octree_id=suns_octree_id,
        suns_octree_path=suns_octree_path,
        # **The direct-sun leg's pair (Phase 7c Task 11), and they are `build_ase_octree`'s output,
        # never `build_suns_octree`'s.** `ChainRequest.ase_octree_path`'s own docstring says why
        # substituting one for the other runs cleanly and answers wrongly: the suns octree has the
        # aperture stripped and a `thick 0` BSDF proxy in its place, so `rcontrib -I -ab 0` against
        # it measures the beam through a tensor tree instead of through the glazing's own
        # transmittance -- which is the one thing LM-83 sec 3.3 says ASE is measured *after*.
        # `None` on any run that does not plan the leg; `direct_sun_steps` raises per field rather
        # than reading through either.
        ase_octree_id=ase_octree_id,
        ase_octree_path=ase_octree_path,
        # **The per-group ASE octrees and the dome they were compiled over (Phase 7d-1 Task 8).**
        # Empty and `None` on every run that is not blind_control='lm83' over window groups --
        # `chain.direct_sun_groups` is the one place that decides. A five-phase lm83 run fills them
        # though it plans no schedule, and its chain traces none of them (`chain.direct_sun_steps`);
        # `chain._ase_octree_for` refuses a group whose octree is missing rather than tracing it
        # against another facade's scene.
        ase_octree_ids=dict(ase_octree_ids),
        ase_octree_paths=dict(ase_octree_paths),
        ase_suns=ase_suns,
        # R848: what the occlusion probe recompiles and compares, so it judges the scene the
        # matrix was traced against.
        ase_suns_scene=ase_suns_scene,
        ase_scene_digest=ase_scene_digest,
        # R869: the ASE_net octree and the automated groups it blacks, empty and `None` on a run
        # with no group marked automated or no direct-sun leg -- which plans no net leg
        # (`chain.direct_sun_steps`). `ase_net_apertures` is one aperture per group, in
        # `ase_net_groups` order, derived from the scene the octree was blacked from
        # (`_build_ase_net_octree`): what the net probe closes.
        ase_net_octree_id=ase_net_octree_id,
        ase_net_octree_path=ase_net_octree_path,
        ase_net_groups=tuple(ase_net_groups),
        ase_net_apertures=tuple(ase_net_apertures),
    )


class DuplicateArtifactStepError(ProjectError):
    """Two steps in one submitted plan seal the same artifact id and are **not** the same step.

    `_append_leg`'s refusal. An identical duplicate is a wasted spawn and is dropped silently; two
    *different* commands sealing one id are two different matrices under one name, and whichever
    finishes second is the one the manifest ends up describing. That is not a cost, it is a wrong
    answer with correct-looking provenance, and there is no repair a caller could make -- so it is
    raised rather than reported.

    Unreachable from any argument a caller can pass: it fires only if two chain builders in this
    repo disagree about a spec they are supposed to share. No register code, for
    `SunsShellNotStrippedError`'s reason -- there is nothing for a client to branch on.
    """

    code = None


def _append_leg(steps: list[Step], leg: list[Step]) -> list[Step]:
    """`steps + leg`, with any step of `leg` that re-seals an artifact `steps` already seals dropped.

    **Task 10's hand-off.** `five_phase_steps` and `direct_sun_steps` both call
    `chain._sun_disk_sky_step`, so a five-phase run whose `metrics` name `ase` concatenates two
    steps sealing `smxsun:<weather>`. They are the identical `Step` -- `tests/test_five_phase.py::
    TestTheDirectSunLegSharesTheFivePhaseSunMatrix` compares them whole -- so the answer is not
    wrong, but it is a duplicated ~45 s `gendaymtx` and a plan row a client cannot make sense of.
    `_plan` derives the plan from the steps actually submitted (R30), so the only place to fix it is
    the list.

    **The de-duplication lives here rather than in `rad_engine.chain`** because only the assembling
    caller knows which legs it combined; `_sun_disk_sky_step`'s own docstring assigns it here for
    that reason.

    **A step that seals nothing is never dropped.** The `genklemsamp`, `dctimestep`, `rcollate` and
    `rcalc` halves have no artifact id to compare, and collapsing them would leave a later step
    reading a file this job never wrote -- `reuse_decision` records the same rule for the same
    reason.

    **Two non-identical steps sealing one id raise** (`DuplicateArtifactStepError`) rather than
    silently keeping the first. Keeping the first would publish whichever matrix the *other* spec
    described under an id nothing could tell apart.
    """
    sealed = {
        step.artifact.artifact_id: step for step in steps if step.artifact is not None
    }
    merged = list(steps)
    for step in leg:
        if step.artifact is None:
            merged.append(step)
            continue
        already = sealed.get(step.artifact.artifact_id)
        if already is None:
            sealed[step.artifact.artifact_id] = step
            merged.append(step)
            continue
        if already != step:
            raise DuplicateArtifactStepError(
                f"two steps in one plan seal {step.artifact.artifact_id!r} and they are not the "
                f"same step: {already.phase!r} runs {' '.join(already.argv)} and {step.phase!r} "
                f"runs {' '.join(step.argv)}. One artifact id must describe one command; whichever "
                "finished second would be the bytes the manifest ends up describing, with the "
                "other's provenance."
            )
    return merged


def _plan(steps: list[Step]) -> list[PlanStep]:
    """§11's `plan` array, derived from the steps actually submitted.

    Derived rather than written out a second time: a plan that is authored separately from the steps
    is a plan that can describe a job the worker is not running, which is the drift ruling R30 names
    -- and the immediate return exists precisely so *"the user sees the decision before waiting for
    it"*, so it must describe the real one.

    **`reuse` is read off the step rather than hardcoded `False`, as of Phase 6b Task 8.** That is
    one line, and it is what makes R30's invariant carry the reuse decision instead of merely
    surviving it: the plan says `reuse: true` for exactly the steps `JobStore._run` will decline to
    spawn, because both read the same flag on the same objects.
    """
    return [
        PlanStep(
            step=step.phase,
            artifact=step.artifact.artifact_id if step.artifact else None,
            reuse=step.reuse,
            estimate_s=step.estimate_s or 0.0,
        )
        for step in steps
    ]


# --- the tool -----------------------------------------------------------------------------------


def run_annual(
    project: Annotated[
        str | None,
        Field(
            description=(
                "the project handle. Needs registered geometry, the named sensor grid, and "
                "weather loaded with `weather(action='load')`."
            )
        ),
    ] = None,
    grid: Annotated[
        str | None,
        Field(
            description=(
                "the sensor grid's handle inside the project. Its points become the `.ill`'s rows."
            )
        ),
    ] = None,
    metrics: Annotated[
        list[str] | None,
        Field(
            description=(
                "what you are measuring -- one or more of sda, ase, udi, da, en17037_provision, "
                "illuminance_timeseries. Drives the method inference. `sda` requires "
                "`ase` alongside it (LM-83: any area reporting sDA must also report ASE). `udi` is "
                "accepted and computed -- its tally is published by results(action='metrics'), "
                "and its 100-2000 lux band is sourced (docs/tool-surface.md sec 11)."
            )
        ),
    ] = None,
    epw: Annotated[
        str | None,
        Field(
            description=(
                "the handle of a weather input already loaded into this project (the `name` "
                "given to `weather(action='load')`, or its `weather_`-prefixed id) -- not a "
                "filesystem path. Its `.wea` is what the annual sky matrix is built from."
            )
        ),
    ] = None,
    north: Annotated[
        StrictFloat | None,
        Field(
            description=(
                "degrees **east** of north (the surveying convention). Required, no default: a "
                "90-degrees-wrong north produces a fully plausible annual result. Emitted to "
                "`gendaymtx` as `-r -north`, since `-r` rotates west of north; both numbers are "
                "reported."
            )
        ),
    ] = None,
    method: Annotated[
        RequestedMethod,
        Field(
            description=(
                "\"auto\" (default) or an explicit method. An override contradicting a direct-sun "
                "metric is refused (LM-83: using 3-phase for ASE is a methodological error)."
            )
        ),
    ] = "auto",
    sky_subdivision: Annotated[
        StrictInt | None,
        Field(
            description=(
                "`gendaymtx -m`. **Both methods accept any measured subdivision** -- 1, 2, 4 or "
                "6, the Reinhart subdivisions this server has measured `rfluxmtx`/`gendaymtx` "
                "against (docs/tool-surface.md verified fact 3). The 2-phase daylight-coefficient "
                "receiver is built at whatever `mf` is asked for, the same way the 3-phase D "
                "matrix is, so the receiver and the sky matrix agree by construction on both "
                "paths. A subdivision outside the measured four is refused naming the ones this "
                "server accepts. Omit it to get 4."
            )
        ),
    ] = None,
    quality: Annotated[
        str | dict[str, Any],
        Field(
            description=(
                "a preset name (draft/standard/high/reference) or a dict overriding individual "
                "ambient flags. Matrix parameters are baked into all 8760 hours and cannot be "
                "corrected later. An override is bounded on both sides: every value must be "
                "finite, the integer flags are capped at 16x the 'high' preset (-ab 112, -ad "
                "65536, -as 32768, -ar 16384) and -aa lies in [0, 1]. Outside those, "
                "ARGUMENT_OUT_OF_RANGE naming the flag."
            )
        ),
    ] = "standard",
    blind_control: Annotated[
        str | None,
        Field(
            description=(
                "\"none\", \"fixed\" or \"lm83\". \"none\" and \"fixed\" are single-state runs -- "
                "the run multiplies through the group's one bound BSDF state, which is what the "
                "2-, 3- and 5-phase chains build. Omit `blind_control` to run each group's single "
                "bound state. **Under LM-83-12, below 1 000 sf, \"lm83\" refuses to plan the "
                "blend on a grid under half as dense as LM-83's 0.6 m placement**: section "
                "2.2.6's small-area rows are absolute point counts, so such a grid leaves blinds "
                "open when the standard closes them and overstates sDA. `results` refuses or flags "
                "that basis at `error` (`BLIND_TRIGGER_MIXED_BASIS`)."
            )
        ),
    ] = None,
    lm83_edition: Annotated[
        Lm83Edition | None,
        Field(
            description=(
                "the LM-83 edition, \"LM-83-12\" (default) or \"LM-83-22\". It decides the "
                "blind rule sealed into `sched:<grid>` (LM-83-22: blinds close on more than 2 % of "
                "the points at every area; LM-83-12: its area table) and the edition the "
                "advisories name. Read only on an LM-83 run -- `sda` or `ase` in `metrics`, or "
                "`blind_control=\"lm83\"`; refused on any other run."
            )
        ),
    ] = None,
    rebuild: Annotated[
        list[str] | None,
        Field(
            description=(
                "artifact ids to force-rebuild, naming the values the result's `plan` reports "
                "under `artifact` (for example 'vmx:main:grid'). An id named here is never "
                "reused, whatever the provenance graph says -- the escape hatch for a matrix you "
                "distrust for a reason the graph cannot see, such as a Radiance upgrade, which "
                "flags rather than invalidates. An id this run's plan does not contain is a "
                "no-op, not an error. Omit it to reuse every artifact whose provenance is "
                "unchanged (see the result's `reuse` object)."
            )
        ),
    ] = None,
    dry_run: Annotated[
        bool,
        Field(
            description=(
                "validate every argument and refuse exactly as a real call would, but queue "
                "nothing; a full queue (`QUEUE_FULL`) is found only by the real call."
            )
        ),
    ] = False,
) -> AnnualResponse:  # noqa: DOC201
    """Annual daylight simulation. The server infers the method; you state the metrics.

    Use it for annual metrics over a sensor grid; for illuminance under one sky use
    `run_point_in_time`.

    Returns a job id immediately -- annual matrix work outlives any client timeout -- along with the
    inferred method and the plan, so the decision is visible before the work finishes. Poll with
    `job(action="status")`, then read the result with `results`.

    The 2-phase, 3-phase and 5-phase chains are all built. Annual DGP is not: annual glare needs an
    image-based view matrix this server does not build. The inference always runs and is always reported. A 3-phase or
    5-phase run needs exactly one window group with exactly one BSDF state bound -- **except a
    3-phase run under `blind_control="lm83"`, which lifts both narrowings**: it fans out over every
    window group and every state each one binds, and every such group must bind `open` and `closed`
    (see `blind_control` below).

    **`blind_control="lm83"`: LM-83's hourly blind schedule.** "lm83" plans the pre-blind,
    zero-bounce direct-sun run LM-83's hourly blind schedule is derived from, sealed as
    `illdir:<grid>`; read the schedule with `results(action='blind_schedule')`. Where the run
    resolves a window group it also traces one direct-sun matrix per group, with every *other*
    group's aperture blacked, and seals the per-group schedule as `sched:<grid>`; that
    is what the payload's `groups` carries. **The payload's `states` is a different quantity and is
    never to be distributed over window groups** -- it is derived from the whole-grid
    `illdir:<grid>`, aggregated over every aperture, where a count of points in sun cannot be
    attributed to the group that lit them. **On the 3-phase chain it now produces the blended
    result a scheduled sDA is read from.** The multiply fans out per (group, state), and
    `illblend:<grid>` is sealed from the schedule and every per-state result; read the sDA with
    `results(action='compliance', standard='lm83', blind_control='lm83')`. A group must bind
    **both** `open` and `closed` for this, and a group that has not is refused before
    anything is built. On a 2-phase or 5-phase run `lm83` still seals no blend, and
    `SDA_WITHOUT_BLIND_SCHEDULE` says so on any such run whose metrics name `sda`. Place the grid
    with `sensor_grid(action='create', standard='lm83', ...)` before this run, not after it.
    """
    # **`METHOD_INFERRED` is emitted on EVERY path out of this function -- finding I2.**
    # docs/tool-surface.md says "Always emitted" twice (the §11 advisories table and the method-
    # inference section), and this module's docstring says "on every call, refusals included". All
    # three were false: the advisory was measured on 2 of 9 exits, absent from all five
    # argument-shape refusals and both project-state refusals. `sda`-without-`ase` is the sharpest
    # case -- a refusal *about the metrics*, where the inference is both meaningful and computable
    # from the arguments already in hand, telling the caller nothing about what their metrics imply.
    #
    # Computed here rather than inside `_dispatch` because the argument refusals return before
    # `_dispatch` is ever called. `_inference_context` cannot raise, so a malformed `metrics` still
    # reaches its own refusal with whatever the inference could honestly say.
    context = _inference_context(
        metrics, method, blind_control, lm83_edition or DEFAULT_LM83_EDITION
    )

    validation_refusal = _validate_arguments(
        grid=grid,
        metrics=metrics, north=north, method=method, sky_subdivision=sky_subdivision,
        quality=quality, blind_control=blind_control,
        # `rebuild` is deliberately **not** passed: it has no argument-shape refusal, because the
        # published `list[str] | None` already refuses every malformed shape at the schema boundary
        # (measured -- see `_validate_arguments`' closing note). It goes to `_dispatch` instead.
        advisories=context,
        # An edition outside the two is refused just below; until then it cites the default's.
        edition=lm83_edition if lm83_edition in LM83_EDITIONS else DEFAULT_LM83_EDITION,
    )
    if validation_refusal is not None:
        return validation_refusal
    if (
        edition_refusal := _refuse_unread_edition(metrics, blind_control, lm83_edition, context)
    ) is not None:
        return edition_refusal

    runtime = current()
    try:
        return _dispatch(
            runtime, project=project, grid=grid, metrics=metrics, epw=epw, north=north,
            method=method, sky_subdivision=sky_subdivision, quality=quality,
            blind_control=blind_control, rebuild=rebuild, dry_run=dry_run,
            edition=lm83_edition or DEFAULT_LM83_EDITION,
        )
    except MatrixShapeError as exc:
        # Engine invariant 3, caught at submit time (R44(1)) so it is a refusal rather than a
        # Radiance fatal from inside a job. No register code: `check_matrix_shape` raises a
        # defensive engine exception, not a client-facing coded refusal (its own docstring says so),
        # and `field="sky_subdivision"` is what names the argument that moves it.
        logger.info("run_annual refused: %s", exc)
        return _refuse_argument(str(exc), field="sky_subdivision", advisories=context)
    except (GridEmptyOnDiskError, GridUnreadableError) as exc:
        logger.info("run_annual refused: %s", exc)
        return refuse(
            str(exc),
            [*context, Advisory(code=exc.code, severity="error", field="grid", message=str(exc))],
        )
    except GridNotFoundError as exc:
        logger.info("run_annual refused: %s", exc)
        return refuse(str(exc), list(context))
    except WeatherNotLoadedError as exc:
        logger.info("run_annual refused: %s", exc)
        return refuse(str(exc), list(context))
    except NoGeometryRegisteredError as exc:
        logger.info("run_annual refused: %s", exc)
        return refuse(
            str(exc),
            [
                *context,
                Advisory(code=exc.code, severity="error", field="project", message=str(exc)),
            ],
        )
    except SceneFileNotFoundError as exc:
        # **A registered geometry file that has been deleted since it was registered.** Reachable
        # only since Task 5: `_group_facts(_window_groups(...))` reads the aperture polygons back
        # through `rad_engine.scene.read_scene`, which is the first thing on this path that *opens* a
        # registered scene file rather than merely naming it. The submit-time octree opens them too,
        # but `read_scene` now runs first; the `!`-line scan in `_validate_project` runs earlier
        # still and deliberately does **not** refuse here (`point_in_time_tool.
        # _scan_scene_for_inline_commands` has `except OSError: continue`, ruling R5's one-code-per-
        # event, so a file it cannot open is left for the run that drops it to report).
        #
        # **`SCENE_FILE_NOT_FOUND`, the code `SceneFileNotFoundError` already carries** (ruling
        # R29). Nothing is invented and nothing is chosen here: the exception's own `.code` is read
        # off it, exactly as `geometry_tool` and `materials_tool` already do for the same class.
        # It is a `RuntimeError` rather than a `ProjectError`, so it is disjoint from every other
        # clause in this chain -- left uncaught it reaches the wire as a bare `ToolError` with an
        # absolute path and no `structuredContent`, which is check 0.4 and check 0.5 violated
        # together, the same shape `SceneInlineCommandError`'s own clause below exists to prevent.
        logger.info("run_annual refused: %s", exc)
        return refuse(
            str(exc),
            [
                *context,
                Advisory(
                    code=exc.code, severity=exc.severity, field="project", message=str(exc)
                ),
            ],
        )
    except ApertureError as exc:
        # A window group whose registered polygons no longer form one coplanar aperture, or whose
        # winding gives no consistent normal -- `derive`'s own two refusals, raised while
        # re-deriving a group's geometry for the inference. `window_group(action='define')` refuses
        # both at definition time; this is the read-time echo for geometry edited afterwards.
        #
        # The code is the exception's own (`APERTURE_NOT_COPLANAR` / `APERTURE_NORMAL_AMBIGUOUS`,
        # both allocated), read off it rather than chosen here -- `window_group`'s own handler takes
        # the identical route. `ShadingLadderExhaustedError` is the one `ApertureError` with
        # `code = None`; it is unreachable from this path (nothing here calls `substitute_shading`)
        # but the `None` branch is kept so a code-less member cannot reach `Advisory(code=None)`.
        #
        # `field="project"`: the offending geometry is project state, not an argument of this call.
        # There is no `aperture` argument on `run_annual` to name, which is what separates this from
        # `window_group`'s own `field="aperture"` for the same exception.
        logger.info("run_annual refused: %s", exc)
        if exc.code is None:
            return refuse(str(exc), list(context))
        return refuse(
            str(exc),
            [
                *context,
                Advisory(
                    code=exc.code, severity=exc.severity, field="project", message=str(exc)
                ),
            ],
        )
    except BSDFError as exc:
        # The sealed `tmx` copy is not a parseable BSDF -- raised while reading T's bin count for
        # the 3-phase shape precondition (`chain.three_phase_target`). Reachable only from a `tmx` that
        # was valid when `assign_bsdf` sealed it and has been edited or truncated since, which is
        # exactly the drift ruling R62's copy exists to make detectable rather than silent.
        #
        # **No code**, and the posture is `window_group`'s own for the identical exception: §9
        # records that no code is allocated for "this XML is not parseable as a BSDF", and a
        # condition with no allocated code carries its reason as prose rather than borrowing a code
        # that means something else. Shaped here rather than left to escape: `BSDFError` is disjoint
        # from every other family in this chain, so an uncaught one reaches the wire as a bare
        # `ToolError` with an absolute path -- checks 0.4 and 0.5 together.
        logger.info("run_annual refused: %s", exc)
        return refuse(str(exc), list(context))
    except SceneInlineCommandError as exc:
        # Ruling R33/R38: a registered scene file carrying a hostile `!` line is refused before
        # `oconv` runs. Shaped with its own allocated code rather than allowed to escape as a raw
        # ToolError carrying an absolute path (check 0.4), the same handling `point_in_time_tool`
        # gives this exception.
        logger.info("run_annual refused: %s", exc)
        return refuse(
            str(exc),
            [*context, Advisory(
                code=getattr(exc, "code", "SCENE_INLINE_COMMAND"), severity="error",
                field="project", message=str(exc),
            )],
        )
    except (EmptyOctreeError, OctreeSetOverflowError, OctreeTooManyFilesError) as exc:
        logger.info("run_annual refused: %s", exc)
        code = getattr(exc, "code", None)
        if code is None:
            return refuse(str(exc), list(context))
        return refuse(
            str(exc),
            [
                *context,
                Advisory(
                    code=code, severity=getattr(exc, "severity", "error"), message=str(exc)
                ),
            ],
        )
    except RadianceTimeoutError as exc:
        # **The octree build's hard bound, fired.** Without this clause the bound would trade a hang
        # for an uncoded `ToolError` carrying an absolute path -- check 0.4 again, and not a fix:
        # the caller would still have no machine-readable way to tell "your geometry is pathological
        # or a scene file is a pipe" from any other transport failure.
        #
        # **`RadianceTimeoutError`, not `subprocess.TimeoutExpired`, and this clause sits above the
        # `except RadianceError` one.** Naming `subprocess` here would import it into `rad_mcp`,
        # which `tests/test_purity.py::TestTheProtocolLayerSpawnsNothing` refuses -- *"rad_mcp is
        # thin -- no subprocess calls"* (CONTEXT.md, Layers) -- so `rad_engine.run` translates the
        # expiry at the spawn and this layer names only the engine's own type. It subclasses
        # `RadianceError`, so ordering is load-bearing: below that clause it would be swallowed and
        # reported as a generic Radiance failure with no mention of the bound.
        #
        # **`OCTREE_BUILD_TIMEOUT`, `field="project"`** -- allocated 2026-08-09 (ruling R56) for
        # exactly this refusal. It carried `ARGUMENT_OUT_OF_RANGE` for the one commit between the
        # bound landing and the allocation, because a code is never invented to fill a gap (Global
        # Constraints) and the implementer reported it instead. The gap that earned the allocation:
        # under the shared code a caller could not tell *this* from the symlinked-octree refusal
        # below, which is also `ARGUMENT_OUT_OF_RANGE` with `field="project"` -- and the two want
        # opposite responses. That one is a **security refusal**, answered by removing the symlink;
        # this one is **retryable as-is**, with nothing for the user to repair, so a client that
        # reads codes can back off where one reading a sentence can only surface it
        # (`MANIFEST_LOCK_BUSY`'s argument). The honest difference from that code is that a retry is
        # not *promised* to work -- the same expiry also means the geometry is genuinely too large
        # -- which is why the message states both forks rather than advertising a retry.
        #
        # It is also not `ARGUMENT_OUT_OF_RANGE` on the merits: R14 defines that code as *an
        # argument rejected on its face, before any work was attempted*, and here the argument was
        # accepted and the work ran for the full bound.
        #
        # Nothing has been queued when this fires: the build is above the `dry_run` branch and the
        # steps are built after it, so the refusal is honest that no job exists.
        logger.info("run_annual refused: %s", exc)
        message = (
            f"compiling an octree for this run exceeded its {OCTREE_BUILD_TIMEOUT_S:g} s bound and "
            "was terminated. A run compiles up to five: the two annual octrees, the all-black and "
            "suns octrees a five-phase call adds, and the ASE octree a run that measures LM-83's "
            "direct sun adds. An octree build is seconds for normal geometry, so retry once -- and "
            "if it expires again the geometry is the problem, not the moment: either pathological "
            "geometry (hundreds of triangles at a shared vertex -- see OCTREE_SET_OVERFLOW) or a "
            "registered scene file that is a pipe or device rather than a regular file, which "
            "oconv blocks on. Nothing has been written and no job has been submitted. Check the "
            "files registered by geometry(action='add') and re-run."
        )
        return refuse(
            message,
            [
                *context,
                Advisory(
                    code="OCTREE_BUILD_TIMEOUT",
                    severity="error",
                    field="project",
                    message=message,
                ),
            ],
        )
    except NotARegularFileError as exc:
        # A write target that is a FIFO, a device, or a symlink at the final component
        # (`safe_io.open_regular_file`'s `O_NOFOLLOW`/`S_ISREG` guards, finding C1). Shaped here
        # rather than allowed to escape: `NotARegularFileError` is an `OSError`, disjoint from this
        # module's other exception families, so an uncaught one reaches the wire as a raw
        # `ToolError` with no code and an absolute path in the message -- the check-0.4 violation
        # class `point_in_time_tool` already documents for `SceneInlineCommandError`.
        # **`PROJECT_PATH_NOT_A_REGULAR_FILE`, not `ARGUMENT_OUT_OF_RANGE` -- ruling R58,
        # 2026-08-10.** This carried the argument code because the register allocated nothing for
        # "that path is a device", and the implementer reported the gap rather than inventing a
        # code (Global Constraints). The gap is now filled, for the reason R56 gave when it split
        # `OCTREE_BUILD_TIMEOUT` off the same overload: R14 defines `ARGUMENT_OUT_OF_RANGE` as *an
        # argument rejected on its face, before any work was attempted*, and this one is rejected
        # on the **file's type**, discovered by opening it, with the argument itself well-formed.
        #
        # The repairs differ, which is the test that matters: an argument refusal is fixed by
        # passing a different value, and this is fixed by removing whatever was planted at a path
        # the caller may never have named. `field="project"` stays -- the offending path is project
        # state, not an argument of this call.
        logger.info("run_annual refused: %s", exc)
        return _refuse_not_a_regular_file(str(exc), advisories=context)
    except RadianceNotFound as exc:
        logger.info("run_annual refused: %s", exc)
        return refuse(
            str(exc),
            [*context, Advisory(code="RADIANCE_NOT_FOUND", severity="error", message=str(exc))],
        )
    except RadianceError as exc:
        logger.info("run_annual failed: %s", exc)
        return refuse(str(exc), list(context))
    except GridFileOutsideRoot as exc:
        # Final fix G, fix round 3 (S1, S2): `field="grid"`, as every results read gives it.
        logger.info("run_annual refused: %s", exc)
        return refuse(str(exc), [*context, Advisory(
            code="PATH_OUTSIDE_ROOT", severity="error", field=exc.field, message=str(exc))])
    except (ProjectError, JobError, PathOutsideRoot, RootsNotConfigured) as exc:
        # `refuse_engine_error` owns the engine->wire shape and takes no advisory list, so the
        # inference context is merged onto the result it builds rather than passed into it --
        # reaching into that function would put this tool's concern inside the one place the two
        # shapes are supposed to meet for every tool.
        logger.info("run_annual refused: %s", exc)
        return _with_context(refuse_engine_error(exc), context)
    except OSError as exc:
        # **`O_NOFOLLOW` reports a symlink as `ELOOP`, not as `NotARegularFileError`** -- the open
        # fails before there is a descriptor to `fstat`, so the clause above is never reached for
        # the very case that motivated routing `run_to_file`'s sink through `open_regular_file`.
        # Measured on this branch: a symlink at `06_octrees/annual_<grid>.oct` gives
        # `[Errno 62] Too many levels of symbolic links`. Left uncaught, that escapes as a raw
        # `ToolError` carrying an absolute path -- trading a silent breach for the uncoded transport
        # failure check 0.4 forbids, which is not a fix. `_write_contained` and
        # `results_tool` both document the same asymmetry; this is the third instance.
        #
        # Last in the chain deliberately: `NotARegularFileError` is itself an `OSError` and keeps
        # its own, more specific message above.
        #
        # **Ruling R58 moves this onto `PROJECT_PATH_NOT_A_REGULAR_FILE` too**, with the clause
        # above. `ELOOP` from `O_NOFOLLOW` and `NotARegularFileError` from `S_ISREG` are the *same
        # refusal* -- "the thing at that path is not a regular file the server will write" -- split
        # only by which guard happens to fire first, which is an implementation detail of the open
        # and not something a client should have to branch on. Giving them one code is the point of
        # the allocation; leaving the symlink half on `ARGUMENT_OUT_OF_RANGE` would have recreated
        # the overload one clause lower down.
        #
        # **Gated on `ELOOP` as of the whole-branch audit, 2026-08-10.** This `try` wraps the single
        # statement `return _dispatch(...)`, and `_dispatch` is ~170 lines: manifest reads, weather
        # lookups, grid reads, the octree build, step-file writes, job submission. Coding *every*
        # `OSError` from all of that `PROJECT_PATH_NOT_A_REGULAR_FILE` told a caller whose disk was
        # full, whose mount was read-only, or who lacked a permission to "remove the symlink" that
        # nobody planted -- a security refusal reported for a condition that is not one. Measured:
        # `O_NOFOLLOW` gives errno 62 (ELOOP) and EACCES/ENOSPC/EROFS/ENOENT are all distinct, so
        # the two separate cleanly. `materials_tool` reached the same conclusion for the same reason
        # and left its clause uncoded; this keeps the code for the case it was allocated for and
        # falls through to an uncoded refusal for the rest, which is the narrower of the two fixes.
        logger.info("run_annual refused: %s", exc)
        if exc.errno != errno.ELOOP:
            return _with_context(
                refuse(
                    f"run_annual could not complete: {exc.strerror or exc}. Nothing about the "
                    "arguments is wrong -- the filesystem refused the operation."
                ),
                context,
            )
        return _refuse_not_a_regular_file(
            f"the octree for this run could not be written: {exc.strerror or exc}. A symlink at "
            "that path is the usual cause -- this server refuses to write through one, because the "
            "target may be any file the server user can reach. Nothing has been written and no job "
            "has been submitted. Remove the symlink and re-run.",
            advisories=context,
        )


def _refuse_unread_edition(
    metrics: object, blind_control: object, lm83_edition: object, context: list[Advisory]
) -> CallToolResult | None:
    """R864 (Review Focus 4): `lm83_edition` is read only on an LM-83 run (`_is_lm83_run`), so on
    any other run it would do nothing and is refused rather than ignored -- `sensor_grid`'s
    `_refuse_unread_edition` takes the same posture for its own argument. A value outside
    `LM83_EDITIONS` is refused too, before anything reads it as LM-83-12; the input schema's
    `Literal` stops one at the wire, so that arm guards a direct call. Runs after
    `_validate_arguments`, so `metrics` is a list of known metrics by then; `None` leaves the
    missing-argument refusal to `_dispatch`.
    """
    if lm83_edition is None:
        return None
    if lm83_edition not in LM83_EDITIONS:
        message = (
            f"lm83_edition={lm83_edition!r} is not an LM-83 edition; the editions are exactly "
            "'LM-83-12' and 'LM-83-22', letter case included."
        )
        return _refuse_argument(message, field="lm83_edition", advisories=context)
    if metrics is None or _is_lm83_run(metrics if isinstance(metrics, list) else [], blind_control):
        return None
    message = (
        f"lm83_edition is read only on an LM-83 run -- one with 'sda' or 'ase' in metrics, or "
        f"blind_control='lm83' -- and this run has neither (metrics={metrics!r}, "
        f"blind_control={blind_control!r}), so it is refused rather than ignored. Leave it out, "
        "or ask for an LM-83 metric or the LM-83 blind schedule."
    )
    return _refuse_argument(message, field="lm83_edition", advisories=context)


def _dispatch(
    runtime: Runtime,
    *,
    project: str,
    grid: str,
    metrics: list[str],
    epw: str,
    north: float,
    method: RequestedMethod,
    sky_subdivision: int | None,
    quality: str | dict[str, Any],
    blind_control: str | None,
    rebuild: list[str] | None,
    dry_run: bool,
    edition: Lm83Edition = DEFAULT_LM83_EDITION,
) -> CallToolResult:
    """The one validation path both `dry_run` and a real call walk (R39).

    `edition` is the call's `lm83_edition`, already refused by `run_annual` unless this is an
    LM-83 run (R864): it names the edition in the advisories and chooses the blind rule sealed
    into `sched:<grid>`.

    Everything above the `if dry_run` line is validation: reading the manifest, running the
    inference, resolving the sky subdivision and the quality preset, proving the geometry, grid and
    weather are all there and readable, compiling the octree, and shape-checking the two matrices
    this job would produce. Nothing has been queued. A refusal raised by any of it leaves this
    function by exception and is shaped identically for both paths in `run_annual`'s one handler
    chain.
    """
    if (
        refusal := refuse_missing(
            "run_annual", project=project, grid=grid, metrics=metrics, epw=epw, north=north
        )
    ) is not None:
        return refusal

    # **Everything derivable from `metrics` alone is computed before the first refusal can
    # return**, so a refusal carries the advisories that qualify it rather than only its reason.
    # This ordering is load-bearing and was a defect first: `LM83_EDITION` was attached *after* the
    # unbuildable-method refusal, so a caller asking for `sda`+`ase` -- the one request that is
    # guaranteed to hit that refusal this phase -- was refused without ever being told which edition
    # of LM-83 the thresholds they will eventually be judged against come from. Caught by
    # `TestNorthAndSkyAdvisories::test_lm83_edition_is_stated_for_an_lm83_metric`, which asserts the
    # advisory on the refusal specifically.
    lm83 = any(m in _LM83_METRICS for m in metrics)
    # **An LM-83 run, which is wider than an LM-83 metric** (R866): `blind_control="lm83"` applies
    # LM-83's blind rule whatever the metrics, so it is under the sky floor and states its edition
    # like an `sda` or `ase` run. `lm83` alone still means "an LM-83 metric" below.
    lm83_run = _is_lm83_run(metrics, blind_control)
    # **The direct-sun leg's trigger, decided here and read three times below** -- the subdivision
    # floor, the octree build, and the step list. `chain.direct_sun_steps` costs an ASE octree, a
    # `1 + 144*mf^2`-source suns scene and an `rcontrib -I -ab 0` over every sensor, so a run that
    # needs neither LM-83's ASE tally nor its blind schedule must not pay for any of it.
    #
    # **`"ase" in metrics` OR `blind_control == "lm83"`, and the second disjunct is not redundant.**
    # `ase` is what the matrix is *tallied* into (Task 14); the LM-83 blind schedule is *read off the
    # same matrix* (`results(action="blind_schedule")`, Task 13), and a caller can ask for that on a
    # run whose metrics are `da` alone. `sda` is deliberately **not** a trigger: an sDA asked for
    # without `blind_control="lm83"` is the unscheduled total, which needs no direct matrix at all,
    # and `SDA_WITHOUT_BLIND_SCHEDULE` is what states that rather than a leg nothing reads. (The
    # sentence here read *"sDA is not blind-aware until Phase 7d"*; amended at Phase 7d-1 Task 15a
    # for the reason that emitter's own comment gives -- the phase arrived, and what decides
    # whether an sDA is blind-aware is now the artifact it is read from.)
    plans_direct_leg = "ase" in metrics or blind_control == _LM83_BLIND_CONTROL
    metric_advisories: list[Advisory] = []
    if lm83_run:
        # **`_lm83_edition_advisory()`, not a second literal.** That function's own docstring says
        # the advisory is *"stated once, so the refusal path and the success path cannot drift"* --
        # and until the whole-branch fix wave this site held a hand-typed copy of the same message,
        # so the docstring was false and the two were one edit apart from disagreeing. A shipped
        # sentence that claims a single definition has to be one.
        metric_advisories.append(_lm83_edition_advisory(edition))
    # **The ASE caveat belongs in `metric_advisories` for the reason the block above this one
    # gives**: everything derivable from `metrics` alone is computed before the first refusal can
    # return, so a refusal carries the advisories that qualify it. It used to be appended ~150
    # lines below, past `_refuse_unbuildable_method` and every other `return refuse(...)` in this
    # function, and its own comment claimed a refused call still carried it. It did not -- measured
    # at `7a18916`, `metrics=["sda","ase"], method="3phase"` was refused `METHOD_OVERRIDE_UNSAFE`
    # with the caveat absent, and so was every refusal *raised* out of this function (those are
    # covered by `_inference_context`'s own call to the same factory). Owner ruling R253.
    if (ase_caveat := _ase_blind_geometry_advisory(metrics, edition=edition)) is not None:
        metric_advisories.append(ase_caveat)

    # **The inference runs before the project is even opened**, and its advisories ride on every
    # exit below -- METHOD_INFERRED is emitted on every call, refusals included. Window-group facts
    # are read from the manifest a moment later and **can now change the answer** (Task 5), so this
    # early call is explicitly the one whose result is discarded (M2): it exists so that a refusal
    # about an unbuildable method does not depend on the project opening cleanly first, not to
    # report a method.
    inference = infer(_as_metrics(metrics), [], method)
    advisories = _inference_advisories(inference) + metric_advisories
    # **This early refusal answers before the manifest is read** -- M2: *"a refusal about an
    # unbuildable method must not depend on the manifest loading cleanly first"*. It is where
    # `METHOD_OVERRIDE_UNSAFE` (`infer` refusing `method="3phase"` with `ase`, from the arguments
    # alone) reaches the caller; `TestTheEarlyMethodRefusalStillAnswersWithoutAManifest` pins it
    # against a nonexistent project.
    #
    # **It no longer defers for any metric (R815).** From Phase 6b Task 9 it stood aside, unless the
    # inference was already argument-refused, for the metrics a fenestration refusal keyed on, so
    # those refusals stayed reachable after the manifest read: `ase` until R228 retired
    # `ASE_WITH_BLINDS_REFUSED`, then `annual_dgp` alone, whose DGP-on-a-horizontal-opening refusal
    # and `METRIC_NOT_COMPUTED_YET` refusal both sat below. R815 withdrew `annual_dgp`, so the
    # deferral predicate (`_may_reach_a_fenestration_refusal`) answered `False` for every metric left
    # and was deleted with them.
    if (refusal := _refuse_unbuildable_method(inference, advisories)) is not None:
        return refusal

    state = _validate_project(runtime, project=project, grid=grid, epw=epw)

    # Re-run the inference now that the manifest has been read, so the reported method is derived
    # from the project's real fenestration rather than from an assumption. **This is the call whose
    # answer is reported**, and since Task 5 it is the one that can differ from the early call: a
    # project with a BSDF-bound window group reaches rule 4 here and nowhere else.
    #
    # `discover()` and the project root are what `_group_facts(_window_groups(...))` needs to read
    # the aperture geometry back: the members are re-derived from the registered `.rad` files through
    # `rad_engine.scene.read_scene`, the same route `window_group(action='define')` took, so the two
    # tools cannot disagree about a group's normal.
    #
    # **This is the single `discover()` of this call**, moved up from the octree build below rather
    # than added beside it: one binding is one thing to be wrong, and `RadianceNotFound` from it
    # reaches the same handler either way.
    #
    # **Moving it earlier can only change which refusal a caller sees, and the interval it crossed
    # holds no reachable one** -- checked rather than assumed, because a reordered refusal is how a
    # caller comes to be told the wrong thing to fix. Between the old position and this one lie
    # `resolve_lm83_sky_subdivision`, `north_convention_advisory`, `resolve_quality` and
    # `_shape_check`. The first two cannot refuse at all (they return values and advisories);
    # `resolve_quality`'s `ValueError` is already refused by `_validate_arguments` above the
    # `dry_run` branch, so it cannot first arise here; and `_shape_check`'s `MatrixShapeError` is
    # unreachable from the wire because both matrix headers it predicts are derived from the
    # identical resolved `subdivision` value below (Phase 7a Task 5 -- previously this depended on
    # `_validate_arguments` refusing every `sky_subdivision != 4`, which no longer happens, but the
    # check is unreachable for a stronger reason now: there is no second value left to disagree with
    # it). So no call that previously reached a refusal now reaches `RADIANCE_NOT_FOUND` instead.
    env = discover()
    # The groups themselves, not only the facts derived from them: the 3-phase chain needs the
    # `Aperture` this read already derived (for `bin_expression`, `genklemsamp -vd` and the two
    # surface texts), and re-deriving it below would be the second copy ruling R30 forbids -- two
    # routes to one aperture is how two call sites come to disagree about a normal.
    groups = _window_groups(state.loaded, env, state.paths.root, project=project)
    inference = infer(_as_metrics(metrics), _group_facts(groups), method)
    advisories = _inference_advisories(inference) + metric_advisories
    # **The manifest's own advisories, relayed -- `RADIANCE_VERSION_CHANGED` above all.**
    #
    # `open_project` emits it when the live install's version differs from the one the project last
    # recorded, and its message says the affected artifacts are *"flagged, not invalidated"*:
    # `is_reusable` deliberately never consults version, so a Radiance upgrade does **not** stop this
    # tool reusing V and D. That is the right physics -- a version change moves no photon by itself
    # -- but it makes the warning the *only* thing that tells a caller their reused matrices were
    # built by a different binary than the one that will multiply them. Reused silently and
    # unannounced is the combination worth preventing, and Task 8 is the task that made reuse real.
    #
    # It was measured absent before this line: `run_annual` never read `LoadedProject.advisories` at
    # all, so a project opened under a changed version reached the wire carrying `METHOD_INFERRED`
    # and nothing else, while `project(action='describe')` on the same manifest reported the warning.
    # Two tools reading one manifest and disagreeing about what it says is the drift R30 names.
    #
    # Relayed rather than re-derived: this module never compares version strings itself. Placed here
    # so every exit below carries it -- refusals included, on the same reasoning finding I2 gave
    # `METHOD_INFERRED` -- and after the inference advisories so `METHOD_INFERRED` keeps the first
    # position `rad_core.method` guarantees it.
    advisories.extend(to_wire(advisory) for advisory in state.loaded.advisories)

    # **The fenestration refusals that stood here were deleted by R815.** The last of them refused
    # `annual_dgp` on a horizontal opening; `ASE_WITH_BLINDS_REFUSED` had gone at R228. The ASE caveat
    # (`ASE_BLIND_GEOMETRY_UNDETECTED`) is built with `metric_advisories` at the top of this function
    # and attached to `_inference_context` for the raised refusals (owner rulings R228, R253).

    # Task 10's own run-level consequence of check 5.4 clause 2 (Task 10a): a window group bound
    # only at role='matrix' has no tensor tree for Cds to read, and a 5-phase run must say so here
    # rather than inside a job, as a missing-file error paid for by a real trace. Reachable for `da`
    # (rule 4, a bound BSDF with no direct-sun role) exactly as much as for `ase`: it keys on
    # `inference.method`, not on which metric drove it.
    if (
        refusal := _refuse_missing_direct_sun_binding(inference, state.loaded, groups, advisories)
    ) is not None:
        return refusal

    # Task 13's own half of the same pre-flight, and the enforcement `docs/tool-surface.md` §6 named
    # this task for: the binding exists, but are its bytes still the bytes that were digested? Asked
    # immediately after "is there one", and before any matrix is paid for. See
    # `_refuse_drifted_direct_sun_bsdf`'s docstring.
    if (refusal := _refuse_drifted_direct_sun_bsdf(inference, state.loaded, advisories)) is not None:
        return refusal

    # `_refuse_unbuildable_method` again, against the inference read off the real manifest: a
    # defensive backstop that answers `None` every time this line runs. `inference.refused` depends
    # on `metrics` and `method` alone, so a refused inference was already answered by the early call
    # above, and `_BUILDABLE_METHODS` names every `Method`. (Until R815 the late half of
    # `METRIC_NOT_COMPUTED_YET`'s refusal, for `annual_dgp`, sat beside it.)
    if (refusal := _refuse_unbuildable_method(inference, advisories)) is not None:
        return refusal

    # Check 4.4: LM-83's own floor of 2 305 Reinhart sky patches, applied by the engine's resolver
    # rather than re-derived here. `run_annual` decides *which metrics count as LM-83*
    # (`_LM83_METRICS`); `rad_engine.skymatrix` owns the floor and the advisory.
    #
    # **And check 4.4's second clause, added at Phase 7c Task 11: sec 2.3.1-2's solar position.**
    # LM-83 requires the simulated solar position within 5 degrees of the actual one, and the
    # direct-sun leg models the sun as one `source` per Reinhart patch -- so the clause is a floor on
    # `mf` too, measured at `LM83_MIN_SUN_POSITION_SUBDIVISION` (2; `mf=1` is 6.733 degrees and
    # fails, `mf=2` is 3.374 and clears). It is passed as its own flag rather than folded into
    # `lm83_run` because the two floors are different numbers and compose as a maximum: sec 2.3.2's
    # 4 is stricter, so on every LM-83 run this clause is already satisfied and changes nothing.
    # **R866 removed the one call it could change.** That was `blind_control="lm83"` with neither
    # `sda` nor `ase` in `metrics`, which planned the leg while the metric flag was `False` and so
    # ran at subdivision 2 -- and `results` then judged its sDA and ASE against the 2 305-patch
    # floor with no advisory. `lm83_metric` is now `lm83_run`, which includes that call, and every
    # path that plans the direct leg (`ase` in metrics, or `blind_control="lm83"`) is an LM-83 run,
    # so `direct_sun_leg` changes no call of this tool. It stays as the stated second clause: the
    # resolver and the advisory keep naming it when a request is below its floor too.
    #
    # **No new advisory code.** `SKY_SUBDIVISION_RAISED`'s row already covers being raised to meet
    # an LM-83 requirement; its *text* is widened to name whichever clause actually applied, which
    # is the difference between an advisory that is true of its condition and one that names the
    # 2 305-patch clause for a raise that clause did not cause.
    resolved_subdivision, raised = resolve_lm83_sky_subdivision(
        sky_subdivision, lm83_metric=lm83_run, direct_sun_leg=plans_direct_leg
    )
    if raised:
        advisories.append(
            to_wire(
                sky_subdivision_raised_advisory(
                    # **`sky_subdivision` itself, `None` included** -- it was coerced to `0` here
                    # until Phase 7c Task 11's review round 1. The advisory now decides which
                    # clause to name by comparing the request against each clause's own floor, and
                    # `0` is not "no preference": it is a request below every floor, which would
                    # name the solar-position clause for a `None` that clause never raises.
                    requested=sky_subdivision,
                    used=resolved_subdivision,
                    lm83_metric=lm83_run,
                    direct_sun_leg=plans_direct_leg,
                    edition=edition,
                ),
                field="sky_subdivision",
            )
        )
    # `None` with no LM-83 metric passes through unraised -- §11's "None -> from the metric". The
    # default still lands on 4 -- `LM83_MIN_SKY_SUBDIVISION`, the LM-83 floor's own constant, one of
    # the four subdivisions `dc_receiver_text` accepts -- but since Phase 7a Task 5 that is a choice
    # of default, not a pin: the receiver is no longer built at MF=4 regardless of what is asked for.
    subdivision = resolved_subdivision if resolved_subdivision is not None else LM83_MIN_SKY_SUBDIVISION

    # The north sign, always stated. A 90-degrees-wrong north produces a fully plausible annual
    # result and a *sign*-wrong one produces a plausible result symmetric about the meridian, which
    # is worse -- so both the input value and the emitted flag are reported. The flip itself is
    # `rad_engine.sky.north_rotation_flag`, reached through `build_gendaymtx_argv`; this module
    # never performs a second one.
    advisories.append(
        to_wire(
            north_convention_advisory(
                north_east_of_north=float(north), gendaymtx_r_flag=-float(north)
            ),
            field="north",
        )
    )

    # **`blind_control="lm83"` is admitted as of Phase 7c Task 11, and these two advisories are what
    # keep "admitted" from reading as "applied".** Both are attached here, on the one path `dry_run`
    # and a real call share, so a preview says exactly what a submit says.
    if blind_control == _LM83_BLIND_CONTROL:
        # `BLIND_SCHEDULE_DERIVED` (info). Its register row **as originally allocated** read *"the
        # hourly per-group schedule was built from the direct-only run; states the trigger used"*;
        # Task 16 corrected it, because one `illdir:<grid>` per grid cannot produce a per-group
        # schedule and R116 publishes `groups: null` in place of one. 7c derived the schedule at
        # **read** time and sealed nothing (R107). Naming the artifact by id rather than describing
        # it is what makes the advisory actionable: it is the string a caller passes to the next
        # call.
        #
        # **The seal clause is now decided by `_plans_blend`, and Phase 7d-1 Task 19 is the task
        # that had to decide it -- because the sentence it replaces was FALSE OF EVERY RUN.**
        #
        # Task 14 struck *"and is not sealed by this run"* and wrote *"where this run resolves a
        # window group it also seals ... 'sched:<grid>'"*, on the premise that Task 11 had made the
        # old wording false. Task 11 built `chain.blind_schedule_steps`; it did not call it, and
        # nothing in `src/` called it until this task -- measured, `git grep blind_schedule_steps
        # 31af722 -- src/` returns docstrings and one comment and no call. So from Task 14 to this
        # commit the advisory told every `lm83` caller that a file was sealed that no run wrote:
        # the exact failure section 9 of docs/tool-surface.md names, and the same failure this
        # message had already been corrected for once.
        #
        # It is true of a run that plans the blend and false of one that does not, so it is
        # **branched** rather than softened into a clause that is true of neither: a 3-phase `lm83`
        # run seals the schedule and the blend that consumes it; a 5-phase or 2-phase `lm83` run
        # seals neither, and its schedule really is read-time only. Sealing a schedule on the runs
        # that cannot consume it is what R107 forbids (*"sealing a derived value with no consumer
        # would be storage for its own sake"*), which is why the fix is to state the condition
        # rather than to widen the wiring.
        #
        # The whole-grid series `results(action="blind_schedule")` publishes as `states` is derived
        # at read time from `illdir:<grid>` and is sealed nowhere on either branch; the two halves
        # are named separately for the same reason section 14 names them separately.
        seals_schedule = _plans_blend(method=inference.method, blind_control=blind_control)
        advisories.append(Advisory(
            code="BLIND_SCHEDULE_DERIVED",
            severity="info",
            field="blind_control",
            message=(
                f"blind_control='lm83' plans the pre-blind, zero-bounce direct-sun run this "
                f"schedule is derived from, sealed as 'illdir:{grid}'. "
                + (
                    f"This run also seals the per-group schedule of "
                    f"{section('blind_trigger', edition)} as 'sched:{grid}' "
                    f"and the blended annual result it selects, 'illblend:{grid}'. "
                    if seals_schedule else
                    f"This run seals no 'sched:{grid}': the schedule is sealed by the chain that "
                    "consumes it, which is the 3-phase blend, and this run is not one "
                    f"(method={inference.method}). Its schedule is derived at read time instead. "
                )
                + "The whole-grid series is derived at read time and sealed nowhere. Read it with "
                # W5 (final review): under LM-83-22 the read must name the edition, or the default
                # read refuses the schedule this run sealed.
                + (
                    "results(action='blind_schedule'), which states the "
                    if edition != LM83_22 else
                    f"results(action='blind_schedule', lm83_edition={LM83_22!r}), which states the "
                )
                + "trigger, the threshold and the band it used. "
                f"{section('ase_definition', edition)} defines the direct beam this run measures."
            ),
        ))
        if "sda" in metrics and not _plans_blend(
            method=inference.method, blind_control=blind_control
        ):
            # **`_plans_blend` is what stops this becoming a false published sentence at Phase 7d-1
            # Task 19.** Until this task the code fired on *every* `lm83` submit that asked for sDA,
            # and every word of it was true: `_dispatch` handed `three_phase_steps` a one-group
            # target labelled `blind_control="none"` and nothing in `src/` called
            # `chain.blend_steps`. Task 19 wires both, so a 3-phase `lm83` run now seals
            # `illblend:<grid>` and its sDA **is** computed against the schedule -- at which point
            # the sentence *"this run seals no blended result"* would be untrue of the run it
            # describes, which is the failure section 9 of docs/tool-surface.md names and which the
            # same message was corrected for once already at Task 14.
            #
            # It is a narrowing, not a retirement, and the runs it still fires on are real: a
            # **2-phase** run (no fenestration to schedule) and a **5-phase** run (single-group,
            # single-state by construction, so `chain.blend_steps` is a step of the three-phase
            # chain and of no other). On both, `blind_control='lm83'` still buys the direct-sun
            # matrix and still buys no scheduled sDA. `results_tool` drops the same code on a
            # result read from `illblend:<grid>`, so the read side and the submit side now agree
            # about which runs carry it.
            #
            # **Measured: the gate is INERT today, and it is kept anyway.** `sda` cannot be
            # requested without `ase` (`SDA_REPORTED_ALONE`, LM-83 sec 2.4), and `ase` drives
            # `rad_core.method.infer` rule 1 to `5phase` **unconditionally** -- an explicit
            # `method="3phase"` beside it is refused, not honoured (`infer(["sda","ase"],
            # "3phase").refused is True`). So no run that names `sda` in `metrics` can reach a
            # 3-phase inference, `_plans_blend` is `False` at this line on every call that gets
            # here, and the advisory fires exactly as often as it did before this task. It is a
            # guard against a state that is currently unreachable, kept for
            # `chain.three_phase_shape_check`'s stated reason -- *"a precondition that cannot fail
            # is still a guarantee nobody holds"* -- and it is the condition that becomes load
            # bearing the moment the sDA/ASE pairing or the `ase`-infers-5-phase rule moves. The
            # branch that IS live on both values is `BLIND_SCHEDULE_DERIVED`'s seal clause above.
            #
            # **The consequence of that measurement is a finding, not a defect of this line**, and
            # it is recorded rather than worked around: a blind-aware sDA is reachable only by
            # submitting the blend under some other metric (`da`, say) and reading
            # `results(action='compliance', standard='lm83', blind_control='lm83')` afterwards.
            # **The reason is the inference rule and nothing else, as of owner ruling R228.** That
            # sentence used to name `_refuse_ase_with_blinds` as a second closure of the same
            # route; that refusal is retired, and the route is still closed, because `ase` drives
            # `infer` rule 1 to `5phase` unconditionally and `_plans_blend` requires `3phase`.
            # Retiring the refusal therefore did not make a blend-aware sDA requestable at submit,
            # and this gate is inert for exactly one reason rather than two.
            #
            # The condition it names, "sDA
            # computed from a run that carries no operable-blind schedule", is *more* likely to be
            # misread on this call than on any other, because this is the call where the caller
            # asked for one.
            #
            # **The phase reference is gone (Phase 7d-1 Task 15a, ruling R178).** The message said
            # *"sDA is not blind-aware until Phase 7d, which blends a blinds-open and a
            # blinds-closed result hour by hour"*, and Phase 7d-1 is what shipped that blend: the
            # read side resolves `illblend:<grid>` under `results(blind_control='lm83')` and drops
            # this very code on such a result. A sentence naming the phase that has arrived is the
            # datedness `results_tool`'s own emitter of this code was corrected for once already,
            # so what this one states is the condition -- what *this* run seals -- rather than the
            # calendar.
            #
            # `results(action='compliance', standard='lm83')` attaches the same code to the sDA it
            # reports (`results_tool`); this one is attached at submit, where the caller can still
            # decide not to spend the run. Same code, same condition, two moments -- which is the
            # register's own posture, not a duplicate allocation.
            advisories.append(Advisory(
                code="SDA_WITHOUT_BLIND_SCHEDULE",
                severity="warn",
                field="metrics",
                message=(
                    "blind_control='lm83' was accepted, but the sDA this run leads to is NOT "
                    "computed against that schedule: this run seals no blended result, so its "
                    "sDA is read off a matrix in which every hour is the same bound BSDF state. "
                    "What 'lm83' buys on this call is the direct-sun matrix the schedule is read "
                    f"from, sealed as 'illdir:{grid}', and nothing beyond it: this run seals no "
                    f"'sched:{grid}' either, because the schedule is sealed by the chain that "
                    "consumes it and that chain is the 3-phase blend, which this run is not "
                    f"(method={inference.method}). Read the schedule at read time with "
                    "results(action='blind_schedule'). What 'lm83' does not buy on this call is a "
                    "scheduled sDA. "
                    + (
                        f"{section('blind_trigger', edition)} models interior blinds that close "
                        "by window group even where the design specifies none, so an unscheduled "
                        "sDA does not meet it unless every window group is exempt under the same "
                        "section (glazing that diffuses away the direct beam, or adjustable "
                        "transmittance reducible below 2 %); blind_schedule_used is false, and "
                        "the condition "
                        "travels with the number rather than being silently assumed. "
                        if edition == LM83_22 else
                        f"{section('blind_trigger', edition)}'s escape clause makes an "
                        "unscheduled sDA legitimate for a design that excludes operable blinds; "
                        "blind_schedule_used is false either way, and the condition travels with "
                        "the number rather than being silently assumed. "
                    )
                    + "results() reads a blended sDA from 'illblend:<grid>' with "
                    "blind_control='lm83' wherever a run has sealed one."
                ),
            ))
    # **`FIVE_PHASE_RESULT_NOT_YET_READABLE` retired here (Phase 7b Task 4).** It used to warn on
    # every five-phase submit that `results(...)` could not read the `ill5:<grid>` matrix this run
    # was about to seal -- true when `rad_engine.results` only parsed `dctimestep`'s headered,
    # sensor-major, RGB shape. `results_tool._resolve` now serves `ill5:<grid>` (alone, or by
    # `chain="5phase"`; R105) and `iter_annual_lux_rows` reads its headered, scalar-lux rows, so
    # both halves of the advisory's
    # own claim (unreadable, headerless) are false of the condition it names. A code whose
    # definition is untrue of its condition is worse than no code, so it is removed rather than left
    # to warn about a limitation that no longer exists. Its register row is removed in a later task.

    parameters = resolve_quality(quality)

    # **`target` is resolved for 3-phase AND 5-phase alike, as of Phase 7a Task 14.** Both chains
    # multiply `V*T*D*S` -- 5-phase's own first term, reused verbatim from `three_phase_steps` per
    # `chain.five_phase_steps`'s own docstring ("Steps 1-4 are `three_phase_steps(request, target)`'s
    # own first four `Step`s, unpacked and used as-is") -- so both need the identical
    # `ThreePhaseGroup` this call resolves and narrows to. Before this task `target` stayed `None` for a 5-phase inference (the
    # literal `== "3phase"` check below), which is not merely incomplete: with `5phase` now in
    # `_BUILDABLE_METHODS`, a `None` target would have sent a 5-phase-labelled call down the 2-phase
    # branch below -- `dc_receiver_text`, a single interior octree, `two_phase_steps` -- exactly the
    # "a method reported but not built is a wrong number waiting to be read" failure this module's
    # own refusal message states, except silently built rather than refused.
    needs_target = inference.method in ("3phase", "5phase")
    # **The target is resolved above the `dry_run` branch (R39)**, so every refusal about
    # this project's fenestration -- no group, several groups, an unbound or multi-state group, an
    # unreadable T, two states on two Klems bases -- is reachable without paying for a job.
    # `chain.three_phase_target` and `chain.ThreePhaseTarget.single` are where they all live;
    # nothing below re-checks any of them. It is the identical call for 3-phase and 5-phase:
    # 5-phase needs no *second* window-group resolution, because it is the same group, the same
    # sealed T, and the same aperture the 3-phase leg already reads.
    #
    # **No ceiling is passed for the sealed BSDF read, and that is the fix rather than an omission.**
    # It was passed here for one commit, as `max_bsdf_bytes=window_group_tool._MAX_BSDF_BYTES`, and
    # review found that the binding was the one thing no test asserted: `1 << 40` at this line left
    # the whole suite green while re-opening the 875 MB peak Phase 6b measured and closed. The
    # constant now lives in `rad_engine.bsdf`, beside the two reads it bounds, and both import it --
    # so there is no argument here to get wrong (R30).
    #
    # **`blind_control` reaches it as of Phase 7d-1 Task 6**, because it is what decides whether a
    # multi-state group resolves at all: only `lm83` derives a schedule that chooses between the
    # states hour by hour. `None` is the caller not asking for blind control, which chooses nothing,
    # so it resolves the same way `"none"` does -- the same reading `plans_direct_leg` above takes
    # of it.
    # Final fix D, fix round 4 (review N3): every sealed `tmx` copy `three_phase_target` is about
    # to read is re-digested against its record first. See `_refuse_drifted_sealed_tmx`.
    if needs_target and (
        refusal := _refuse_drifted_sealed_tmx(
            groups, state.loaded, project=project, advisories=advisories
        )
    ) is not None:
        return refusal
    resolved_target = (
        three_phase_target(state.loaded, groups, blind_control=blind_control or "none")
        if needs_target else None
    )

    # **Whether this call plans the LM-83 sec 2.2.6 blend -- Phase 7d-1 Task 19, and it is the one
    # condition the narrowing below now bends for.** The predicate is `_plans_blend`'s, the same
    # call `SDA_WITHOUT_BLIND_SCHEDULE` is gated on three hundred lines above, so the advisory that
    # says *"this run seals no blended result"* and the code that decides whether one is sealed
    # cannot disagree. `chain.direct_sun_groups` remains the chain layer's own half of the same
    # decision -- `blind_schedule_steps` and `blend_steps` each return `[]` when it is empty, so a
    # predicate that ever drifted eager could not conjure a schedule out of a run with no groups.
    plans_blend = _plans_blend(method=inference.method, blind_control=blind_control)

    # **`resolved_target is not None` is what says "this is a matrix run", not `target is not
    # None`.** They were the same fact until this task, because every matrix run narrowed to
    # exactly one group; a blended run does not narrow at all, so the two separate here. Every
    # branch below that means *"2-phase, no fenestration"* asks this flag, and the three that
    # genuinely need **one** group (the 5-phase octree block, `ChainRequest.target`, and the
    # single-group shape check) keep asking `target`.
    matrix_run = resolved_target is not None

    # **Narrowed to the one group and the one state every chain this server ships multiplies, here
    # rather than inside step building (R39, R126).** `three_phase_target` now resolves every group
    # and every sealed state a project registers -- that is what Phase 7d's blend consumes -- but
    # `five_phase_steps` still builds one V, T and D, so the multi-group and multi-state refusals
    # fire at the narrowing. Taken *here*, at the point the old refusals fired, so both stay
    # reachable before an octree is compiled or a job is queued; the alternative is a caller who
    # registered two window groups paying for `build_black_octree` and then being refused.
    # `_ = target.state` is that second narrowing forced rather than awaited: nothing on the
    # 3-phase path reads it until `three_phase_steps`, three hundred lines and one octree build
    # below.
    #
    # **Both lines are skipped for a blended run, and Phase 7d-1 Task 19 is what skips them.** That
    # is the whole of the lift: `three_phase_steps` has fanned out per (group, state) since Task 7,
    # `_write_glow_surfaces` and `_write_sample_geometry` have written one file per group since the
    # same task, and `_build_per_group_ase_octrees` has compiled one octree per group since Task 8
    # -- the narrowing was the last thing standing between all of that and a submitted job. It is
    # **not** deleted, because a `blind_control="none"` run still has nothing to choose between its
    # states and a 5-phase run still multiplies one T; both keep the refusals they had.
    target = None
    if resolved_target is not None and not plans_blend:
        target = resolved_target.single
        _ = target.state

    # Final fix E1(c): a three- or five-phase result counts no light through glazing no window
    # group claims, on every such run and its dry run, blinds or none. See
    # `_unclaimed_glazing_advisory`.
    if resolved_target is not None:
        advisory = _unclaimed_glazing_advisory(
            state.loaded, env, project=project, grid=grid, chain=inference.method,
            blended=plans_blend, metrics=metrics,
        )
        if advisory is not None:
            advisories.append(advisory)

    if plans_blend:
        # **Above every octree build and above `dry_run` (R39), because the alternative is a job
        # that fails at its last step.** The schedule names each hour's state in LM-83's own two
        # words; a group that has not bound both of them plans a well-formed job whose blend finds
        # no matrix for the state the scan selected. See `BlendStatesNotBoundError`.
        _refuse_unbindable_blend_states(resolved_target, edition=edition)
        # **The shape precondition, once per window group** (R44's placement, applied to the fan-out
        # the blend introduces). `three_phase_shape_check` reads `klems_bins`, which
        # `three_phase_target` has already proved is one number for the whole group -- every state's
        # sealed file declares the same basis or the group is refused outright -- so a check per
        # group covers every state's multiply and a check per (group, state) would run the identical
        # prediction `len(states)` times. Skipping it for a blended run would leave a 73-bin T
        # failing inside the job after every V and D had been paid for, which is the one thing this
        # precondition exists to prevent.
        for tp_group in resolved_target.groups:
            three_phase_shape_check(
                tp_group,
                n_points=state.n_points,
                sky_subdivision=subdivision,
                matrices=state.paths.matrices,
                weather_input_id=state.weather.input_id,
                n_hours=state.weather.n_hours,
            )
    elif target is not None:
        # **Ruling 1: the shape precondition runs ONCE, here, over whatever exists on disk now.**
        # See `chain.three_phase_shape_check` for why the brief's second placement ("again in full
        # before step 5") is unimplementable -- R44's own finding that the job worker offers no
        # per-step Python hook -- and why that is not a gap. **Ruling R71 -- the identical placement
        # governs `five_phase_shape_check` too**, for the same reason: the job worker spawns
        # processes and offers no per-step Python hook, so a submit-time precondition is the only
        # home either check can have. `inference.method` (not `target` alone, which both methods
        # share) is what selects which of the two runs, because `five_phase_shape_check` predicts
        # the identical `V*T*D*S` join `three_phase_shape_check` does and adds nothing past it
        # (that function's own docstring records why its fourth join was removed as unfalsifiable)
        # -- calling both here would run the same three predictions twice.
        if inference.method == "5phase":
            five_phase_shape_check(
                target,
                n_points=state.n_points,
                sky_subdivision=subdivision,
                matrices=state.paths.matrices,
                weather_input_id=state.weather.input_id,
                n_hours=state.weather.n_hours,
            )
        else:
            three_phase_shape_check(
                target,
                n_points=state.n_points,
                sky_subdivision=subdivision,
                matrices=state.paths.matrices,
                weather_input_id=state.weather.input_id,
                n_hours=state.weather.n_hours,
            )
    # **R871, R870, R869: the refusals the composed closed state, the bound schedule and the
    # automated mark bring, in that order, above every octree build and above `dry_run` (R39)** --
    # so a refused run compiles nothing it will not use, and a preview refuses as a real call does.
    # After the narrowing above, so a run that one-group or one-state chain refuses is refused for
    # that first, as it always was.
    #
    # 1. A state `lm83_default` composed from an open state rebound since (`evaluate` says stale).
    if (
        refusal := _refuse_stale_lm83_default(
            resolved_target, state.loaded, project=project, advisories=advisories
        )
    ) is not None:
        return refusal
    # 2. Every bound schedule this run's `sched:<grid>` reads, read once, digested, parsed and
    #    counted against this run's weather -- only on a run that seals one.
    if plans_blend and (
        refusal := _refuse_unusable_schedules(
            groups, state.loaded, project=project, epw=epw, n_hours=state.weather.n_hours,
            advisories=advisories,
        )
    ) is not None:
        return refusal
    # 3. The groups the ASE_net scene blacks: every window group of the project marked automated
    #    (`_window_groups`' list, so a two-phase run's groups count -- it resolves no target), on a
    #    run that plans the direct-sun leg; none on any other. A modifier the scenes this run
    #    compiles cannot tell apart is refused here with the whole remedy, before
    #    `build_ase_scene` or `build_ase_net_scene` would raise their own `ValueError`.
    ase_net_groups = (
        tuple(sorted(group.name for group in groups if group.automated))
        if plans_direct_leg else ()
    )
    if plans_direct_leg:
        per_group = direct_sun_groups(resolved_target)
        if len(per_group) > 1:
            _refuse_shared_aperture_modifiers(
                state.loaded, project=project, groups=[tp_group.group for tp_group in per_group],
                net=False, edition=edition,
            )
        if ase_net_groups:
            _refuse_shared_aperture_modifiers(
                state.loaded, project=project, groups=groups, net=True, edition=edition,
            )

    if not matrix_run:
        # **The receiver is built and written HERE, above the octree build and the `dry_run`
        # branch -- and, since the finding that moved it here, above `_shape_check` too, because
        # `_shape_check`'s second source reads this exact file back off disk (its own docstring).**
        # A read-back check cannot verify a file nobody has written yet; writing first is what lets
        # the check that follows be a check of *this call's own* receiver rather than of whatever an
        # unrelated call happened to leave at a `dc_receiver_filename`-derived, per-`mf` path.
        #
        # `dc_receiver_text` raises `ValueError` for an `mf` outside `SUPPORTED_SUBDIVISIONS` -- the
        # subdivisions this project has actually run its receiver against (docs/tool-surface.md
        # verified fact 3, and `rad_engine.annual`'s own note on which of the four were exercised
        # through real `rfluxmtx` rather than only through `gendaymtx`) -- and this is where that
        # becomes a coded refusal rather than an uncaught exception reaching the wire as a bare
        # `ToolError` (check 0.4/0.5, the same discipline `resolve_quality`'s own `except ValueError`
        # above holds for the `quality` argument).
        try:
            receiver_text = dc_receiver_text(subdivision)
        except ValueError as exc:
            return _refuse_argument(str(exc), field="sky_subdivision", advisories=advisories)
        # **`dc_receiver_filename(subdivision)`, not a fixed name -- the fix for the finding that two
        # `run_annual` calls at different subdivisions could race on one shared `receiver.rad` path,
        # including across a real call and a `dry_run=True` "preview" of a different one, because
        # this write already sits above the `dry_run` return.** Deriving the filename from `mf` means
        # two calls at different subdivisions write to two different paths and can never collide; two
        # calls at the *same* `mf` still share a path, exactly as every call did before this fix, and
        # remain benign for the identical reason -- `dc_receiver_text` is pure, so both writers write
        # identical bytes.
        receiver_path = state.paths.root / dc_receiver_filename(subdivision)
        _write_contained(
            receiver_path, receiver_text, roots=runtime.roots,
            what="daylight-coefficient receiver file",
        )
        _shape_check(
            n_points=state.n_points, sky_subdivision=subdivision, receiver_path=receiver_path
        )

    # R44(3): the octree is built here, at submit time, not as a bare `oconv` Step -- it keeps
    # R33/R38's `!`-line scan against the bytes `oconv` reads, the `getinfo -d` empty check, and the
    # stderr triage, none of which a Step could carry. It is also the one Radiance spawn above the
    # `dry_run` branch, which is what makes those refusals reachable under `dry_run` (R39).
    #
    # `env` is the one resolved above for the window-group read, not a second `discover()`: it is
    # the same PATH lookup with the same answer, and one binding is one thing to be wrong.
    octree_path = state.paths.octrees / f"annual_{grid}.oct"
    contain_octree_destination(octree_path, roots=runtime.roots)
    octree_path.parent.mkdir(parents=True, exist_ok=True)
    octree_id = f"octree:annual:{grid}"

    exterior_octree_path = state.paths.octrees / f"annual_{grid}_exterior.oct"
    exterior_octree_id = f"octree:annual:{grid}:exterior"

    # The 5-phase pair, `None` unless the block below builds them. Named here rather than inside the
    # branch so `_chain_request` reads one set of variables on every path: `five_phase_steps` refuses
    # a request carrying four of the five (its own `ValueError`), so the branch that fills them fills
    # all of them or none.
    black_octree_id: str | None = None
    black_octree_path: Path | None = None
    suns_octree_id: str | None = None
    suns_octree_path: Path | None = None
    direct_sun_input_id: str | None = None

    # The direct-sun leg's pair (Phase 7c Task 11), `None` unless `plans_direct_leg` fills them.
    # `direct_sun_steps` raises a per-field `ValueError` rather than reading through either as
    # `None`, so a half-populated request cannot produce a plausible-but-wrong plan.
    ase_octree_id: str | None = None
    ase_octree_path: Path | None = None

    # **The per-group ASE octrees (Phase 7d-1 Task 8), one per window group the schedule is derived
    # over.** Empty for every run that is not blind_control='lm83' over window groups (a 2-phase
    # run resolves none); a five-phase lm83 run fills them and traces none -- see
    # `chain.direct_sun_groups`, which is the one definition of which groups these are. They are a
    # *different* octree from the pair above: that one is every aperture open (LM-83 sec 3.3's ASE),
    # each of these has every other group's aperture blacked (sec 2.2.6's residual).
    ase_octree_ids: dict[str, str] = {}
    ase_octree_paths: dict[str, Path] = {}
    # The Reinhart dome those octrees carry a `source` at, read back out of the suns scene (R144).
    # `None` unless `plans_direct_leg` reads it below (R856: every run with the leg, two-phase
    # included); `register_artifact` refuses a partial set.
    ase_suns: tuple[SunPosition, ...] | None = None
    # **The ASE_net octree (R869)**, `None` unless this run plans the direct-sun leg and a window
    # group is marked automated (`ase_net_groups`).
    ase_net: AseNetOctree | None = None

    # **One suns scene per call, shared by the two octrees that need it.** `Cds`'s suns octree
    # (5-phase) and the ASE octree (Task 11) both compile the same `1 + 144*mf^2`-source file, and
    # `suns_scene_text` is pure in `mf` -- so writing it twice would be the same bytes at the same
    # path via a second `cnt`/`rcalc` pair. This memo is what keeps a five-phase ASE run to one
    # spawn. It is `None` until the first consumer writes it, and both consumers below read it.
    suns_scene_path: Path | None = None

    # **R822, extended: each octree's body digest (`octree_body_digest`, the bytes after its
    # header), taken as soon as it is built.** Every one of them sits at a path fixed per grid, so
    # another `run_annual` at this grid (a `dry_run` too) rewrites it in place while this job waits
    # in the queue; an identical rebuild changes only the header (the per-call glow names), which
    # the digest skips. `_with_octree_digests` puts each
    # digest on the steps that read the file, and the worker refuses a step whose octree no longer
    # matches (`OCTREE_REBUILT_SINCE_SUBMIT`). Not a provenance digest: reuse never reads these.
    octree_digests: dict[str, str] = {}
    # **R849: what each octree's build compiled, keyed by artifact id** -- the files in build order,
    # each with the digest of the bytes `oconv` (or `xform`) read, and a per-call file's text, taken
    # right after the build and before `_remove_per_call`. `_register_octree` seals each into the
    # octree's `compiled` parameter, which `export(action="scripts")` rebuilds the octree from.
    compiled_scenes: dict[str, dict[str, Any]] = {}

    if not matrix_run:
        # **Captured, not discarded** -- `octree.bbox` (`(x, y, z, size)` from `getinfo -d`) is the
        # `max_size` `ambient_interpolation_radius_m` needs (`point_in_time_tool._dispatch`'s own
        # `pipeline.octree_cube[3]`), read near `AnnualResult`'s construction far below. Every branch
        # of `matrix_run` sets this same name exactly once, above every refusal and above `dry_run`
        # (R44(3)), so it is always bound by the time the result is built.
        octree = build_octree(
            env,
            state.scene_files,
            out_path=octree_path,
            cwd=state.paths.root,
            timeout=OCTREE_BUILD_TIMEOUT_S,
        )
        octree_digests[str(octree_path)] = octree_body_digest(octree_path)
        compiled_scenes[octree_id] = compiled_scene_record(
            state.scene_files, cwd=state.paths.root, raypath=env.raypath_dirs(state.paths.root),
        )
    else:
        # **Two octrees, compiled at submit for R44(3)'s reasons, and they are different scenes.**
        #
        # The *interior* one carries the registered geometry plus the aperture as a `glow` light
        # source, which is what makes `rcontrib -I+ -m <glow>` a V-matrix trace at all: without the
        # glow primitive there is no modifier for `-m` to gather contribution against, and the
        # matrix is empty rather than wrong.
        #
        # The *exterior* one carries the same geometry plus a white sky dome, because the D matrix
        # bins rays by which Reinhart sky patch they reach and `rcontrib` refuses to run with no
        # `-m` at all (measured: `fatal - missing required modifier argument`). The aperture is
        # **not** a glow here -- `genklemsamp` samples it as plain geometry from a job-local `.rad`,
        # so it does not belong in this octree.
        #
        # Both go through `rad_mcp.paths.contain_octree_destination` before anything is created, for the reason
        # that function records: this tool hoists `build_octree` above its own `dry_run` branch, so
        # an unguarded destination is an arbitrary-file write on a path that submits no job.
        contain_octree_destination(exterior_octree_path, roots=runtime.roots)
        sky_path_rad = state.paths.octrees / f"annual_{grid}_sky.rad"
        # **One glow surface per window group, all of them compiled into the one interior octree**
        # -- ruling R140, Phase 7d-1 Task 7 fix round 2. See `_write_glow_surfaces`.
        glow_paths = _write_glow_surfaces(
            state, grid=grid, groups=resolved_target.groups, roots=runtime.roots,
        )
        # **R822: the per-call glow files are removed once the interior octree is built, whether
        # the build succeeded or not.** Safe because that octree is frozen (`freeze=True`,
        # `oconv -f`): the geometry is embedded and no later spawn reads the
        # glow file. No other octree reads these files (the all-black one gets its own displaced
        # copy, below).
        try:
            _write_contained(
                sky_path_rad, sky_dome_surface(modifier=SKY_MODIFIER), roots=runtime.roots,
                what="D-matrix exterior sky dome",
            )
            # **The interior (V-matrix) octree is the one captured** -- the aperture is a `glow`
            # light source in this one, not the exterior's white sky dome, so its bounding cube is
            # the closer analogue of `point_in_time_tool`'s single octree. The exterior build below
            # stays discarded, as it always was: nothing downstream needs its bbox.
            octree = build_octree(
                env,
                [*state.scene_files, *glow_paths],
                out_path=octree_path,
                cwd=state.paths.root,
                freeze=True,
                timeout=OCTREE_BUILD_TIMEOUT_S,
            )
            compiled_scenes[octree_id] = compiled_scene_record(
                [*state.scene_files, *glow_paths], recreate=glow_paths, cwd=state.paths.root,
                raypath=env.raypath_dirs(state.paths.root),
            )
        finally:
            _remove_per_call(glow_paths)
        octree_digests[str(octree_path)] = octree_body_digest(octree_path)
        build_octree(
            env,
            [*state.scene_files, str(sky_path_rad)],
            out_path=exterior_octree_path,
            cwd=state.paths.root,
            timeout=OCTREE_BUILD_TIMEOUT_S,
        )
        octree_digests[str(exterior_octree_path)] = octree_body_digest(exterior_octree_path)
        compiled_scenes[exterior_octree_id] = compiled_scene_record(
            [*state.scene_files, str(sky_path_rad)], cwd=state.paths.root,
            raypath=env.raypath_dirs(state.paths.root),
        )

        if inference.method == "5phase":
            # **The two octrees the direct-sun terms trace against, compiled here for R44(3)'s
            # reasons and nowhere else** -- Phase 7a Task 14b, the wiring Task 14 found no task
            # owned. Task 14 refused a fully-bound 5-phase call at this point
            # (`FIVE_PHASE_OCTREE_NOT_WIRED`) because `build_black_octree`, `build_suns_octree` and
            # `build_suns_argv` had shipped with **no production caller**, and a 5-phase branch
            # without them would have built a 3-phase plan under a 5-phase label. This block is that
            # caller, and the code is deleted rather than narrowed: a registered code for a state
            # that can no longer occur is the defect docs/tool-surface.md §9 names.
            #
            # **Through Task 8's guarded entry points, never a bare `oconv`** (R44(3)): both keep
            # R33's `!`-line scan against the bytes `oconv`/`xform` are about to read -- twice here,
            # once per spawn -- the `getinfo -d` empty check, and the stderr triage. **Above the
            # `dry_run` branch** (R39), so a dry run refuses an empty octree, a hostile `!` line or a
            # symlinked destination exactly as a real call does.
            #
            # `Vd` and `Dd` share the all-black octree (five-phase-method.md 3.1: *"This octree will
            # be used for both Vd and Dd"*): the registered scene piped through `xform -m black`, and
            # unblackened, the `black` definition itself plus **the same glow and sky-dome surfaces
            # the two annual octrees above carry** -- 3.1's own `oconv` line adds `viewmtxsurf.rad`
            # (V's glow) and `sky_white.rad` (D's dome) for exactly that reason, and reusing the two
            # files this call already wrote is what keeps `V` and `Vd` tracing one aperture surface
            # rather than two derivations of it (R30). Both modifiers must survive into this octree
            # or the terms built on it gather nothing: `build_v_argv` restricts `Vd` to the glow and
            # `build_d_argv` restricts `Dd` to the dome, and `rcontrib` with a `-m` naming a surface
            # the octree does not carry returns zeros rather than an error. The sky dome is reused
            # from the file this call already wrote, which is what keeps `D` and `Dd` binning against
            # one dome rather than two derivations of it (R30); the glow is **not**, for the reason
            # immediately below. A ray at the floor reports `black` either way, which is what
            # `tests/test_five_phase.py::TestFivePhaseIsAdmitted::
            # test_the_octree_the_direct_terms_get_is_the_black_one_not_the_lit_one` traces.
            #
            # **The glow this octree gets is a displaced per-octree copy, never the lit octree's own
            # file** -- the whole-branch review's IMPORTANT 2, and the fix this block's own previous
            # comment prescribed for itself.
            #
            # Task 14b measured the undisplaced glow winning the tie against the blackened copy of
            # the same glazing polygon (this repo's aperture *is* the scene's own glazing) and left
            # it there, recording the case for this review. A measurement is the wrong instrument:
            # an exactly-coincident surface is a tie Radiance defines no resolution for, so one ray
            # winning on one build says nothing about every ray, every aperture, every `oconv`
            # ordering and every version -- and here the losing outcome is silent and expensive. If
            # the glow loses, `rcontrib -m <glow>` gathers nothing against a perfect occluder, `Vd`
            # is identically zero, `- Vd*T*Dd*Sds` vanishes, and the chain publishes
            # `I_3ph + Cds*Ssun`: direct sun counted **twice**, every sunlit hour *higher* than plain
            # 3-phase, correct provenance and no error. That is Task 15b's `Cds = 0` Critical with
            # the sign reversed, and it is the same argument `bsdf_proxy_surface`'s own displacement
            # already won for the surface beside it.
            #
            # **Scoped per octree.** The lit annual octree's glow files, written above, are not
            # touched, so the shipped 3-phase `V` matrix does not move; this is a second file
            # carrying `displaced_glow_surface`'s text and the **same modifier**, since
            # `build_v_argv` restricts `Vd` with `-m <modifier>`. Its bytes depend on the aperture,
            # which a `window_group` re-register can change between two concurrent calls (R141), so
            # it is written under a name unique to this call (R822), not at a path shared by grid.
            black_octree_path = state.paths.octrees / f"five_phase_{grid}_black.oct"
            contain_octree_destination(black_octree_path, roots=runtime.roots)
            black_octree_id = f"octree:five_phase:{grid}:black"
            black_material_path = state.paths.octrees / "five_phase_black.mat"
            _write_contained(
                black_material_path, black_material(), roots=runtime.roots,
                what="all-black octree's black material definition",
            )
            # *(R822:)* written under a name unique to this call and removed once the all-black
            # octree is built; that octree is frozen unconditionally (`build_black_octree`'s stdin
            # scene), so no later spawn reads the file.
            black_glow_path = _write_per_call(
                state.paths.octrees,
                prefix=f".five-phase-{grid}-black-",
                text=displaced_glow_surface(
                    target.group.aperture,
                    modifier=GLOW_MODIFIER.format(group=target.group.name),
                ),
                roots=runtime.roots,
                what="Vd aperture glow surface (displaced, all-black octree)",
            )
            try:
                build_black_octree(
                    env,
                    state.scene_files,
                    [str(black_material_path), str(black_glow_path), str(sky_path_rad)],
                    out_path=black_octree_path,
                    cwd=state.paths.root,
                    timeout=OCTREE_BUILD_TIMEOUT_S,
                )
                compiled_scenes[black_octree_id] = compiled_scene_record(
                    [str(black_material_path), str(black_glow_path), str(sky_path_rad)],
                    black=state.scene_files, recreate=[black_glow_path], cwd=state.paths.root,
                    raypath=env.raypath_dirs(state.paths.root),
                )
            finally:
                _remove_per_call([black_glow_path])
            octree_digests[str(black_octree_path)] = octree_body_digest(black_octree_path)

            # `Cds`'s own octree is a different scene, not a second use of the black one
            # (five-phase-method.md 4.2): the same opaque shell blackened, **minus the aperture**
            # (Task 15b, below), plus the tensor-tree **BSDF proxy** the direct-sun binding names and
            # the suns scene that gives every Reinhart patch its own traceable source.
            #
            # **One deviation from 4.2, published in docs/tool-surface.md §11 rather than only
            # here.** The spec keeps `glazing.rad` and `venetianblind.rad` out of the
            # `xform -m black` pipe and adds them back **unblackened** beside a proxy of thickness
            # `0.30`; this server keeps the aperture's own polygons out of the pipe and does **not**
            # add them back, with a proxy of thickness `0`. That pair is one coherent configuration
            # rather than a compromise: a nonzero thickness exists to displace rays past detailed
            # fenestration geometry that must also be present, and this repo has none to protect --
            # a window group is a glazing *modifier* in the registered scene and the device exists
            # only inside the bound XML, so the BSDF surface **is** the whole fenestration. Adding
            # the real glass back on top of it is not available here for the same reason: measured
            # on `Cds` itself over `tests/fixtures/scenes/micro` at five-phase-method.md 4.3's own
            # sampling, the spec's arrangement returns **65-69% of removal** -- the modelled
            # glazing's own 0.74 transmittance applied a second time, on top of the tensor tree's,
            # which for a tensor tree that *is* the whole window is a double count.
            #
            # **Task 15b, this branch's Critical: "nothing may compete with it" is not "blacken
            # it".** Blackening does not remove a surface -- it converts it into a perfect occluder.
            # Until 15b this block piped `state.scene_files` (which contains the window group's own
            # glazing polygon) through `xform -m black`, so the displaced proxy won the *view* ray
            # from the sensor while the *shadow* ray outward to each sun died on the blackened
            # glazing a fraction of a millimetre behind it. `Cds` was identically zero on every
            # project this server can build -- every entry, exactly 0, measured on the micro scene
            # and on this fixture; with the aperture kept out the same octree recovers roughly a
            # quarter to a third of what a bare opening transmits (`tests/fixtures/README.md`,
            # finding 4, with the sampling it was measured at) -- and since the chain
            # publishes `I_5ph = V*T*D*S - Vd*T*Dd*Sds + Cds*Ssun`, every sunlit hour came out
            # *lower* than plain 3-phase with correct provenance and no error anywhere. Four other
            # candidate fixes were measured and ruled out (a larger displacement: any opaque surface
            # outboard of the proxy kills the shadow ray; a nonzero `thick`: winding-dependent, and
            # with no black behind it the sun passes the BSDF *unattenuated*; reordering `oconv`;
            # restoring real glass, above). Pinned by `tests/test_five_phase.py::
            # TestFivePhaseIsAdmitted::test_cds_is_not_identically_zero_through_the_sealed_suns_octree`,
            # which traces the sealed octree rather than comparing an argv -- no argv test can see
            # this, which is how it survived fifteen tasks.
            #
            # **The proxy is still emitted displaced, and that survives 15b** (review fix round 1):
            # `bsdf_proxy_surface` puts it a fraction of the aperture's own size toward the room, so
            # it is the nearer surface to every sensor by construction. Removing the aperture from
            # the shell above removes the surface it used to tie *with* -- so the displacement now
            # guards nothing in this particular octree, and it stays because it is a property of the
            # builder for any scene, not a measurement of this one: an exactly-coincident proxy is a
            # tie Radiance defines no resolution for, and the losing outcome is silent. Pinned from
            # both sides by `tests/test_five_phase.py::TestFivePhaseIsAdmitted::
            # test_cds_traces_the_tensor_tree_proxy_and_not_an_open_aperture` (the ray reports
            # the proxy's modifier, and the written polygon stands in front of the y=0 aperture
            # plane) -- dropping the proxy from this `oconv` list left the whole suite green before
            # it, and since 15b it no longer makes `Cds` small but makes it the **bare opening's**,
            # 4.2x the tensor tree's own value, which the same test still catches.
            #
            # The proxy's XML is read back off the manifest record
            # `window_group(action='assign_bsdf', role='direct_sun')` registered -- never a path this
            # module composes -- so a re-bound tensor tree compiles a genuinely different octree, and
            # `_refuse_drifted_direct_sun_bsdf` above has already proved those bytes are the bytes
            # that were digested.
            direct_sun_input_id = f"bsdf_{target.group.name}_{target.state}_direct_sun"
            proxy_path = state.paths.octrees / f"five_phase_{grid}_proxy.rad"
            try:
                proxy_text = bsdf_proxy_surface(
                    target.group.aperture,
                    modifier=BSDF_PROXY_MODIFIER.format(group=target.group.name),
                    xml_path=_direct_sun_xml_path(state.loaded, direct_sun_input_id),
                )
            except ValueError as exc:
                # A bound path a Radiance primitive cannot carry (a `"` or a newline in the
                # filename -- `klems._UNQUOTABLE_IN_A_PATH` carries the measurement). Shaped into
                # this module's own codeless project refusal at the boundary, exactly as
                # `dc_receiver_text`'s `ValueError` is shaped a few lines above, rather than
                # escaping as an uncoded `ToolError` (check 0.4) or failing later inside `oconv`
                # with an arity error naming neither the path nor the fix.
                raise DirectSunBindingUnusableError(str(exc)) from exc
            _write_contained(
                proxy_path, proxy_text, roots=runtime.roots,
                what="Cds tensor-tree BSDF proxy surface",
            )
            # **The suns scene's filename is derived from `mf`, exactly as the 2-phase receiver's
            # is** (`rad_engine.suns.suns_scene_filename`, and `rad_engine.annual.
            # dc_receiver_filename`'s own history for why). Its bytes are `1 + 144*mf^2` sources, so
            # two calls at different subdivisions writing one shared `suns.rad` is the receiver
            # collision again with a different filename: this write sits above the `dry_run` branch,
            # so even a preview of one subdivision could overwrite the scene another call is about to
            # compile. Two subdivisions, two paths, no collision by construction; two calls at the
            # same `mf` share a path and stay benign because `suns_scene_text` is pure in `mf`.
            #
            # The suns *octree* carries `mf` in its filename for the same reason -- its compiled
            # content contains that scene -- while the black octree does not, because nothing about
            # it depends on the subdivision.
            # Written through `_write_suns_scene`, the one helper the ASE octree below shares
            # (R30): two independent writes of `1 + 144*mf^2` sources under one filename is one
            # fact spelled twice, and `suns_scene_path` above is the memo that keeps a five-phase
            # ASE run to a single `cnt`/`rcalc` pair.
            suns_scene_path = _write_suns_scene(runtime, state, env, subdivision=subdivision)
            suns_octree_path = (
                state.paths.octrees / f"five_phase_{grid}_mf{subdivision}_suns.oct"
            )
            contain_octree_destination(suns_octree_path, roots=runtime.roots)
            # **The id carries `mf` because the path and the content do** -- review fix round 1,
            # Minor 3: the first cut stopped one step short of its own reasoning. A single
            # `octree:five_phase:<grid>:suns` row would have described two files on disk after two
            # runs at different subdivisions, pointing at whichever ran last, while `Cds` from the
            # earlier run still names it as an edge. Reuse stayed correct either way -- `cdsmx`
            # records `mf` in its own parameters, so `_parameters_match` refuses the cache hit -- so
            # this is provenance rather than a wrong number, and provenance that says one artifact
            # where there are two is exactly what the manifest exists not to do.
            suns_octree_id = f"octree:five_phase:{grid}:suns:mf{subdivision}"
            # **The opaque shell this octree blackens is the registered scene *minus the aperture*,
            # written here as its own file** (Task 15b, and the block's own comment above for why).
            # `scene_without_modifiers` flattens `state.scene_files` through `xform` first, because a
            # scene may name its geometry through an `!xform` include chain and a filter that read
            # only the top-level files would keep any glazing polygon living one level down -- the
            # same indirection ruling R33's own recursion exists to close. The engine returns text
            # and this layer writes it, exactly as it does for the suns scene above: the containment
            # primitive raises a wire type `rad_engine` may not name.
            #
            # R33's `!`-line scan still covers everything that reaches a spawn, in both directions:
            # `scene_without_modifiers` scans every registered scene file immediately before *its*
            # `xform`, and `build_suns_octree` scans this written shell before its own `xform -m
            # black` and the proxy/suns/material list before `oconv` (`_build_black_backed_octree`,
            # two independent scans). The shell itself carries no `!` line by construction -- `xform`
            # has already expanded every include into it.
            #
            # The path carries `grid` and not `mf`, following the proxy beside it: its bytes depend
            # on the scene and the aperture, on neither the subdivision nor the grid. Two concurrent
            # calls at the same grid therefore write identical bytes to one path, the receiver
            # file's own benign case, rather than racing over different content.
            aperture_modifiers = sorted(
                {polygon.modifier for polygon in target.group.aperture.polygons}
            )
            shell = scene_without_modifiers(
                env,
                state.scene_files,
                modifiers=aperture_modifiers,
                cwd=state.paths.root,
                timeout=OCTREE_BUILD_TIMEOUT_S,
            )
            # **The success condition of Task 15b's fix, inspected instead of written into a comment
            # and forgotten** -- the whole-branch review's IMPORTANT 4.
            #
            # `strip_primitives_by_modifier` returns `()` **silently** for a modifier matching
            # nothing: supported and tested behaviour (`tests/test_scene.py`), correct for that
            # function, and exactly wrong to ignore here. `removed` is the entire fix -- if it is
            # empty the aperture went into the `xform -m black` pipe after all, becomes a perfect
            # occluder behind a `thick 0` proxy, and `Cds` returns to identically zero: `oconv` exits
            # 0, the job succeeds, every sunlit hour comes out *lower* than plain 3-phase, and
            # nothing anywhere reports it. That is this branch's own Critical, re-opened.
            #
            # `rad_engine.octree`'s docstring states the invariant as prose for whoever composes the
            # next caller. Prose beside code that does not enforce it is what CONTEXT.md calls worse
            # than either, so this is the enforcement. Reachable through any future divergence
            # between `_polygons_by_modifier`'s geometry-only read of the scene and `state.scene_
            # files` -- an alias, a route the flatten changes, a modifier renamed between the two --
            # none of which is hypothetical enough to leave to a comment.
            if not shell.removed:
                raise SunsShellNotStrippedError(
                    f"the suns octree's opaque shell for window group {target.group.name!r} could "
                    f"not be built: the aperture's modifier(s) "
                    f"{', '.join(repr(m) for m in aperture_modifiers)} matched no primitive in the "
                    "registered scene once it was flattened through `xform`, so nothing was "
                    "removed. The direct-sun term Cds is computed against a scene the aperture has "
                    "been taken out of; with the aperture still in it, Cds is identically zero and "
                    "the run would publish a plausible number that is too low, with no error. "
                    "Add this project's geometry again so the window group's polygons and the "
                    "scene files agree -- "
                    f"{_geometry_add_calls(state.loaded, project)} -- and re-run."
                )
            suns_shell_path = state.paths.octrees / f"five_phase_{grid}_shell.rad"
            _write_contained(
                suns_shell_path,
                "## The suns octree's opaque shell -- rad_mcp.annual_tool._dispatch,\n"
                "## docs/specs/five-phase-method.md 4.2. The registered scene, flattened through\n"
                "## `xform`, with every primitive modified by this window group's aperture removed\n"
                "## rather than blackened: blackening does not remove a surface, it makes it a\n"
                "## perfect occluder, and one behind the BSDF proxy kills the shadow ray out to\n"
                "## every sun.\n"
                f"## Window group: {target.group.name}\n"
                f"## Aperture modifiers: {', '.join(aperture_modifiers)}\n"
                f"## Removed {len(shell.removed)} primitive(s): "
                f"{', '.join(shell.removed) or '(none)'}\n\n"
                + shell.text,
                roots=runtime.roots,
                what="Cds suns octree opaque shell",
            )
            build_suns_octree(
                env,
                [str(suns_shell_path)],
                [str(black_material_path), str(proxy_path), str(suns_scene_path)],
                out_path=suns_octree_path,
                cwd=state.paths.root,
                timeout=OCTREE_BUILD_TIMEOUT_S,
            )
            octree_digests[str(suns_octree_path)] = octree_body_digest(suns_octree_path)
            compiled_scenes[suns_octree_id] = compiled_scene_record(
                [str(black_material_path), str(proxy_path), str(suns_scene_path)],
                black=[str(suns_shell_path)], cwd=state.paths.root,
                raypath=env.raypath_dirs(state.paths.root),
            )

    # **The fifth octree, and the one that makes this an LM-83 ASE run** -- Phase 7c Task 11.
    #
    # `build_ase_octree` is the registered scene **whole** plus the suns scene: no blackened shell,
    # no aperture strip, no BSDF proxy. Each difference from `build_suns_octree` is required rather
    # than chosen and that function's docstring gives all three; the one this call site owns is the
    # composition of `scene_files`.
    #
    # **`state.scene_files` is the right list, and "fixed but not operable shading" is why.** LM-83
    # sec 3.2.6 leaves *"any fixed or static shading devices, such as overhangs or light shelves"*
    # in the ASE model, while operable shading is the thing ASE is measured *without* (sec 3.3,
    # pre-blind). In this server those two are already different objects, which is what makes the
    # distinction structural here rather than a judgement call: fixed shading is geometry, and it is
    # in `state.scene_files` because `geometry(action='add')` registered it; an operable device is a
    # **BSDF state bound to a window group** (`window_group(action='assign_bsdf')`, a `bsdf` input),
    # and no route exists by which one becomes a scene primitive. So the registered scene is
    # pre-blind by construction, and passing it whole is the correct composition rather than a
    # convenient one.
    #
    # **`allow_empty` is left at its default `False`, deliberately.** An empty ASE octree returns
    # all-zero direct illuminance, which is indistinguishable at the tally from the *correct*
    # all-zero a deep room produces when its aperture cannot admit a high summer sun -- Task 1
    # measured exactly that on the micro fixture. The emptiness has to be refused here, where it is
    # still nameable, and `build_octree`'s `getinfo -d` check is what names it (`OCTREE_EMPTY`).
    #
    # **Above the `dry_run` branch** (R39) and through `rad_engine.octree`'s guarded entry point
    # (R44(3)), like the four before it: R33's `!`-line scan, the empty check and the stderr triage
    # all run on a preview exactly as they do on a submit. `rad_mcp.paths.contain_octree_destination` first, for
    # the reason that function records.
    if plans_direct_leg:
        if suns_scene_path is None:
            suns_scene_path = _write_suns_scene(runtime, state, env, subdivision=subdivision)
        # The path carries `mf` because its compiled content contains a `1 + 144*mf^2`-source
        # scene -- the suns octree's own reasoning, and the id carries it for the same reason: two
        # runs at different subdivisions are two octrees, not one row pointing at whichever ran
        # last while an `asemx` from the earlier run still names it as an edge.
        ase_octree_path = state.paths.octrees / f"ase_{grid}_mf{subdivision}.oct"
        contain_octree_destination(ase_octree_path, roots=runtime.roots)
        ase_octree_id = f"octree:ase:{grid}:mf{subdivision}"
        build_ase_octree(
            env,
            state.scene_files,
            str(suns_scene_path),
            out_path=ase_octree_path,
            cwd=state.paths.root,
            timeout=OCTREE_BUILD_TIMEOUT_S,
        )
        octree_digests[str(ase_octree_path)] = octree_body_digest(ase_octree_path)
        compiled_scenes[ase_octree_id] = compiled_scene_record(
            [*state.scene_files, str(suns_scene_path)], cwd=state.paths.root,
            raypath=env.raypath_dirs(state.paths.root),
        )

        # **One further ASE octree per window group -- Phase 7d-1 Task 8, and the octree above is
        # not one of them.** That one is every aperture open, which is the pre-blind whole-building
        # quantity LM-83 sec 3.3 defines ASE over. These answer sec 2.2.6's residual instead --
        # *"how many points are still in sun with group g closed"* -- which one aggregated run
        # cannot answer whatever it is multiplied by (ruling R116, and the reason `blind_schedule`
        # published `groups: null`).
        #
        # `chain.direct_sun_groups` is what decides which groups these are, imported rather than
        # re-stated: the chain names one octree per group in that same tuple, and two independently
        # written conditions is how a submit comes to compile an octree no step traces, or a step
        # comes to name one nothing built (R30). On a five-phase run the chain plans no per-group
        # leg (`chain.direct_sun_steps`, Phase 25 final review) and these octrees are compiled for
        # `BLIND_SCHEDULE_UNCLAIMED_APERTURE` alone, which reads the blacked scene they record.
        ase_groups = direct_sun_groups(resolved_target)
        # **The whole Reinhart dome, read back out of the suns scene these octrees compile**
        # (ruling R144). It is what `register_artifact`'s sun-geometry guard reasons from at
        # seal time, and that guard checks `len(suns) == sun_count(mf)` -- so it is the full
        # dome or nothing. Cached per (install, mf) inside `rad_engine.suns`, because the guard
        # wants it at every `asemx` seal. **Read on every run with the leg, not only when it fans
        # out** (rulings R831, R848, R856): the occlusion probe every `asemx` carries casts along
        # these same directions, and the whole-grid one is on every run with the leg -- a two-phase
        # run's included, which resolves no window group and whose probe strips every glazing
        # surface instead (R856). The per-group aperture pair stays per group: `chain`'s
        # `sun_geometry` sets it only on a per-group `asemx`.
        ase_suns = sun_directions(
            env, subdivision, cwd=state.paths.root, timeout=OCTREE_BUILD_TIMEOUT_S
        )
        if ase_groups:
            built = _build_per_group_ase_octrees(
                env,
                groups=ase_groups,
                scene_files=state.scene_files,
                suns_scene=str(suns_scene_path),
                grid=grid,
                subdivision=subdivision,
                octrees_dir=state.paths.octrees,
                cwd=state.paths.root,
                roots=runtime.roots,
                edition=edition,
                geometry_remedy=_geometry_add_calls(state.loaded, project),
            )
            ase_octree_ids, ase_octree_paths = built.ids, built.paths
            octree_digests.update(built.digests)
            compiled_scenes.update(built.compiled)
            if built.unclaimed_transmitting:
                advisories.append(_unclaimed_aperture_advisory(
                    built.unclaimed_transmitting, group_count=len(ase_groups),
                    chain=inference.method, grid=grid, edition=edition,
                ))
        # **The ASE_net octree (R869), compiled after the per-group ones**: the registered scene
        # with the automated groups' apertures blacked and every other group's open, on every
        # chain that plans the leg. The flatten the per-group build already paid for is reused
        # (R30); a run that built none flattens once here.
        if ase_net_groups:
            flattened_text = (
                built.flattened_text if ase_groups else scene_without_modifiers(
                    env,
                    state.scene_files,
                    modifiers=(),
                    cwd=state.paths.root,
                    timeout=OCTREE_BUILD_TIMEOUT_S,
                ).text
            )
            ase_net = _build_ase_net_octree(
                env,
                groups=groups,
                flattened_text=flattened_text,
                suns_scene=str(suns_scene_path),
                grid=grid,
                subdivision=subdivision,
                octrees_dir=state.paths.octrees,
                cwd=state.paths.root,
                roots=runtime.roots,
                geometry_remedy=_geometry_add_calls(state.loaded, project),
            )
            octree_digests[str(ase_net.path)] = ase_net.digest
            compiled_scenes[ase_net.artifact_id] = ase_net.compiled

    # **The automated groups' advisories (R870)**, after the refusals and the builds above, on the
    # one list both exits read. Each is a function of this run's state (`_automated_advisories`).
    advisories.extend(_automated_advisories(
        groups, state.loaded, project=project, method=inference.method,
        blind_control=blind_control, plans_blend=plans_blend, plans_direct_leg=plans_direct_leg,
    ))

    method_view = _method_view(inference)

    # **The job id is allocated here, before the steps, and handed to `submit` rather than left for
    # it to mint.** `Step.stdin_path` must name a file that exists by the time the worker opens it
    # (R45 opens it with `O_NOFOLLOW` and an `S_ISREG` check and *raises* rather than falling back
    # to `DEVNULL`, because a silent fallback would hide a bad path behind an empty matrix), and the
    # only directory that is unambiguously this job's is `10_jobs/<job_id>/`. Minting the id first
    # is what lets the points file be written there instead of into a shared scratch path two
    # concurrent calls would race over. `submit` validates the id as a path component and uses it
    # verbatim.
    #
    # A dry run mints one too, so both paths build the identical `Step` list from the identical
    # code -- but writes into `10_jobs/dry_run/`, and never submits it, so no id a client could hold
    # is ever created for work that was not queued.
    job_id = new_job_id() if not dry_run else "dry_run"
    job_dir = state.paths.job_dir(job_id)
    job_dir.mkdir(parents=True, exist_ok=True)
    request = _chain_request(
        state, grid=grid, parameters=parameters, sky_subdivision=subdivision, north=float(north),
        octree_id=octree_id, octree_path=octree_path, exterior_octree_id=exterior_octree_id,
        exterior_octree_path=exterior_octree_path, job_dir=job_dir,
        # **Every resolved group, not the one `target` narrowed to** (Phase 7d-1 Task 7, review
        # round 1's Important 1). `genklemsamp` samples the polygons it is handed, so each group
        # needs its own file; passing the narrowed group here would leave the chain naming one
        # group's geometry for all of them, which is the defect that review found. The narrowing
        # above still refuses a multi-group run, so this is one entry today -- and it is the half
        # of the fix that is already right when Task 12 lifts that refusal.
        sample_groups=resolved_target.groups if resolved_target is not None else (),
        # Task 14b: the five fields `five_phase_steps` requires, passed on every call and `None`
        # except where the block above filled them. `target` is the same `ThreePhaseGroup` the
        # 3-phase branch below passes positionally -- one resolution, read by whichever chain
        # runs.
        target=target, black_octree_id=black_octree_id, black_octree_path=black_octree_path,
        suns_octree_id=suns_octree_id, suns_octree_path=suns_octree_path,
        # Task 11: `None` unless `plans_direct_leg` compiled the ASE octree above.
        ase_octree_id=ase_octree_id, ase_octree_path=ase_octree_path,
        # Task 8: one blacked ASE octree per window group the schedule is derived over, empty
        # unless the block above fanned out; and the Reinhart dome they were compiled against,
        # set on every run with the leg (R831's probe reads it too, and R856's two-phase one).
        ase_octree_ids=ase_octree_ids, ase_octree_paths=ase_octree_paths, ase_suns=ase_suns,
        ase_suns_scene=suns_scene_path if ase_octree_path is not None else None,
        ase_scene_digest=(
            octree_digests.get(str(ase_octree_path)) if ase_octree_path is not None else None
        ),
        # R869: the ASE_net octree and the groups it blacks; empty unless the block above built it.
        ase_net_octree_id=ase_net.artifact_id if ase_net is not None else None,
        ase_net_octree_path=ase_net.path if ase_net is not None else None,
        ase_net_groups=ase_net.groups if ase_net is not None else (),
        ase_net_apertures=ase_net.apertures if ase_net is not None else (),
    )

    # **The submit-time files the chains name, written here rather than inside them.** Each of these
    # paths is under a project directory a caller can plant a symlink in, and a dry run's job id is
    # the fixed string "dry_run", so the path is predictable rather than minted -- which is why every
    # one goes through `_write_contained` (R48/finding C1) and never `write_text`. That primitive
    # raises `rad_mcp.paths.PathOutsideRoot`, a wire type `rad_engine` may not name, so the writes
    # stay in this layer and `rad_engine.chain` is handed the paths (`ChainRequest`).
    #
    # R43: `rfluxmtx` and `rcontrib -I+` take sensor rays **only** on stdin, and `stdin_path` is a
    # path rather than bytes so the payload never lands in the durable record. It goes into the job
    # directory, which this tool owns, rather than beside the grid's own `.pts`: the file the worker
    # opens must be the file this submit resolved (R45), and a job-local copy cannot be swapped by a
    # later `sensor_grid` call mid-run.
    _write_contained(
        request.points_path, state.points_text, roots=runtime.roots,
        what="job's sensor-points file",
    )
    # **One sampling file per resolved window group** -- `polygon_surface`, not the glow surface.
    # Written into the **job directory** for `points.pts`' reason: each is derived from this call's
    # own re-derivation of that group's aperture, and a job-local copy cannot be swapped by a
    # concurrent `window_group` call between submit and step 2 (R45).
    #
    # **A loop over every group rather than one write from `target` (review round 1's Important
    # 1).** Both the aperture and the modifier are formatted from the group being written, for
    # `GLOW_MODIFIER`'s reason one step over: `genklemsamp` samples whatever geometry it is given,
    # so one file standing in for several traces the wrong facade at exactly the right shape. The
    # paths come from `request.sample_rad_paths`, the same dict `_sample_rad_for` looks up in, so
    # the file this loop writes and the file the step names cannot be two paths.
    _write_sample_geometry(
        request,
        groups=resolved_target.groups if resolved_target is not None else (),
        roots=runtime.roots,
    )
    if matrix_run:
        # **The 5-phase branch, beside the 3-phase one -- Task 14b.** Both need the aperture sample
        # file written immediately above (`five_phase_steps` builds a second `genklemsamp` pipe for
        # `Dd` over the same geometry), and both read the same resolved `target`; what differs is the
        # chain. Selected on `inference.method`, the answer `MethodView` reports, so the plan the
        # caller is shown and the steps the worker runs are decided by one value -- a 5-phase label
        # over a 3-phase step list is the exact failure Task 14's refusal existed to prevent.
        #
        # **The `blind_control="none"` wrap is now the UNBLENDED branch only -- Phase 7d-1 Task 19.**
        # It is still not a lie about what the caller asked for on that branch: the narrowing above
        # has refused every run it would misdescribe, so what reaches it is one group with one
        # state, and a chain that multiplies one T with no schedule to choose between states **is**
        # the `"none"` chain whatever `blind_control` the caller named. What changed is that a
        # blended run no longer reaches it: `resolved_target` is passed through whole, carrying its
        # own `blind_control="lm83"`, which is what makes `three_phase_steps` fan the ids out to
        # `ill:<grid>:<g>:<s>` and emit no whole-grid `ill:<grid>` at all. That id stopping existing
        # is the design's own construction (spec sec 5, ruling R107): the blended run seals
        # `illblend:<grid>` instead, `results_tool` resolves it under `blind_control="lm83"` (Task
        # 15), and the two ids can therefore never rebuild in place over one another.
        steps = (
            five_phase_steps(request) if inference.method == "5phase"
            else three_phase_steps(
                request,
                resolved_target if plans_blend
                else ThreePhaseTarget(groups=(target,), blind_control="none"),
            )
        )
    else:
        # **The receiver file was already written above, before `_shape_check` ran** -- not here.
        # `build_rfluxmtx_argv` derives `dc_receiver_filename(spec.sky_subdivision)` from the same
        # `DcMatrixSpec` `two_phase_steps` builds below, so it names the identical path this branch
        # already wrote to; there is nothing left for this branch to write, and writing it a second
        # time here would be the second copy ruling R30 forbids for what is otherwise one fact
        # (this call's receiver bytes) written once.
        steps = two_phase_steps(request)

    # **The direct-sun leg, appended to whichever chain ran** -- Phase 7c Task 11. It is a leg
    # rather than a branch: it does not replace the 2-phase, 3-phase or 5-phase chain, it runs
    # beside it, because ASE and the blind schedule are read off a *different* matrix from the one
    # that answers sDA/DA/UDI. `direct_sun_steps` needs no window group and no `target`, which is
    # what lets it be planned on the 2-phase path this `else` branch just built.
    #
    # `_append_leg` rather than `+`: on a five-phase run both chains plan the identical
    # `smxsun:<weather>` step (they share `chain._sun_disk_sky_step`) and the duplicate must be
    # dropped. See that function for why the drop is a refusal when the two steps disagree.
    if plans_direct_leg:
        # **The resolved target, not `None`, and it is what fans the leg out per window group**
        # (Phase 7d-1 Task 8). `direct_sun_groups` reads `blind_control` off it and returns the
        # groups this run needs a residual for; on a 2-phase run there is no target at all and the
        # leg is the whole-grid pair it has always been. The argument is required rather than
        # defaulted so this call site states which it is.
        steps = _append_leg(
            steps, direct_sun_steps(request, resolved_target, chain=inference.method)
        )

    if plans_blend:
        # **The schedule and the blend, appended HERE and in THIS order -- Phase 7d-1 Task 19, and
        # the order is the whole of why they are not appended anywhere else.**
        #
        # `reuse_decision` walks this one list in order and poisons a step whose `built_from` names
        # an id an *earlier* step of the same list is rebuilding. `sched:<grid>` names every
        # `illdir:<grid>:<g>`, which the direct leg above produces; `illblend:<grid>` names
        # `sched:<grid>` and every `ill:<grid>:<g>:<s>`, which `three_phase_steps` produced further
        # up. So the only ordering in which both are decided against a truthful rebuild set is
        # multiply -> illdir -> sched -> blend, and this is the one point in the module where all
        # four exist in one list. Appended earlier -- inside `three_phase_steps`, say -- the
        # schedule would sit *above* its own `illdir` operands, and a run that rebuilt them would
        # still report the schedule reused: the stale-`sched` poisoning, reported as a saving.
        #
        # **`area_sf` comes off `results_tool._analysis_area`, and ruling R172 is why it may come
        # off nothing else.** LM-83 sec 2.2.6 selects a row of its table from this area and then
        # applies that row's own **point count**; below 1 000 sf the criterion is an absolute count,
        # so an area measured over a different region than the grid was placed in under-closes the
        # blinds and OVERSTATES the sDA the blend then computes. `_analysis_area` is the one
        # function that derives the area from the polygon `sensor_grid(action='create')` recorded
        # for **this** grid, and it is the same function `results(action='blind_schedule')` refuses
        # a mixed basis from (Task 16) -- so the read side and the plan side take the number from
        # one source rather than from two that agree today. Its `_AnalysisAreaNotDerivable` is a
        # `ProjectError` and reaches the caller as a refusal, before the first `rcontrib`, which is
        # R39's own direction: a grid whose polygon cannot be sized cannot select a band, and the
        # band is what decides every hour of the schedule.
        #
        # Only the area and the count are taken. `_analysis_area`'s own advisories belong to the
        # **read** action that publishes an analysis area; attaching them to a submit would put
        # `ANALYSIS_AREA_UNDER_COVERED` on a payload that publishes no coverage for it to be about.
        #
        # **One call, bound to a local, read twice -- owner ruling R242.** `area_sf` and
        # `points_expected` are the two halves of R172's one source: §2.2.6 selects a row off the
        # area and then applies that row's own point count, so the two must come from a single
        # read of a single polygon. Two `_analysis_area(...)` calls here would re-read the manifest
        # and re-grid the polygon between the area and the count -- the second derivation the rule
        # exists to forbid, and the one shape an AST ratchet on `.area_sf` alone would not catch.
        #
        # **Under LM-83-22 the same one call gives the LM-83-22 glossary's area** (R864): the grid's
        # points times its row and column spacings, read back off its `.pts`. It decides nothing
        # there (one 2 % rule at every area) and is sealed beside the schedule, which is what
        # `results` compares against 1 000 sf when the two editions meet.
        analysis_area = _analysis_area(state.loaded, grid=grid, edition=edition)
        try:
            steps = steps + blind_schedule_steps(
                request,
                resolved_target,
                area_sf=analysis_area.area_sf,
                points_expected=analysis_area.points_expected,
                edition=edition,
            )
        except SparseLm83GridError as exc:
            # Final fix F, fix round 5 (P3): the remedy spelled whole -- `rad_engine` holds none
            # of the arguments, so its message names no call; this is where they are.
            spelled = _sparse_grid_remedy(
                state.loaded, project=project, grid=grid, metrics=metrics, epw=epw, north=north,
                method=method, sky_subdivision=sky_subdivision, quality=quality,
                blind_control=blind_control, rebuild=rebuild,
            )
            if spelled is None:
                raise
            raise SparseLm83GridError(exc.finding, spelled, grid=exc.grid) from exc
        steps = steps + blend_steps(request, resolved_target)

    # **The `_`-join collision check, over the plan this call actually submits** (Phase 7d-1 Task 8,
    # fix round 1). `three_phase_steps` runs `refuse_colliding_outputs` over its own list and can see
    # nothing appended to it afterwards; `five_phase_steps` reaches that same call through its
    # one-group wrap and then **discards the list it checked**; `two_phase_steps` never calls it at
    # all. So every plan but the plain 3-phase one reached `submit` unchecked, and this line is
    # where the composed list exists. It is idempotent over a plan that already passed, so the
    # 3-phase case pays a dict of the steps.
    #
    # **Unconditional, not inside `if plans_direct_leg:` above.** It sat inside that branch for one
    # commit, which left a 2-phase-only or 5-phase-only plan reaching `submit` unchecked -- both have
    # the same `_`-join exposure and neither has a direct leg to trigger it.
    #
    # **What it catches, and as of Phase 7d-1 Task 19 that is stated as what it catches** (R137 the
    # other way round: the sentence said *"not reachable from the wire in this commit"*, and this
    # is the commit that made it reachable). A `blind_control="lm83"` run now hands
    # `three_phase_steps` the whole resolved target, so `fan_out` is True and `ill:<grid>:<g>:<s>`
    # is planned beside the direct leg's `illdir:<grid>:<g>` -- at which point a window group named
    # `north` with a state named `direct` puts `ill:<grid>:north:direct` and `illdir:<grid>:north`
    # both at `08_results/annual_<grid>_north_direct.ill`, in one job, with two `dctimestep`s
    # writing it: two ids, two manifest records, one file, and whichever finishes second is the one
    # both records then describe. State names are the caller's
    # (`window_group(action='assign_bsdf')`), so nothing but this line stands between that and a
    # silent overwrite.
    refuse_colliding_outputs(steps, state.loaded)

    # **The reuse decision, on the one path both `dry_run` and a real call walk.** `reuse_decision`
    # asks `rad_engine.project.is_reusable` about every step that commits an artifact and sets
    # `Step.reuse` accordingly; `_plan` reads that flag, so a dry run previews the same reuse a real
    # call would make rather than a plan that rebuilds everything.
    #
    # **Read from `state.loaded`, which is the manifest as it stands *before* the octrees below are
    # re-sealed, and the two cannot disagree.** Checked rather than assumed, because a reuse decision
    # taken against the wrong snapshot is precisely the silent wrong answer this predicate exists to
    # prevent. `_register_octree` replaces the octree artifact with one carrying a fresh `built_at`
    # and a freshly computed `content_digest` -- and `rad_core.manifest.compute_digest` hashes
    # `kind`, the resolved edge digests, the normalised parameters and `radiance_version`, none of
    # which either field is in. The geometry inputs, `{"freeze": True}` and the install's version are
    # identical across the two calls, so the octree's *provenance* digest is the same before and
    # after, and V's and D's validity -- which reads that digest, not the octree's bytes -- is
    # unmoved. (Final fix E, fix round 4, X1: but for the files the scene includes, whose digests
    # are now in that provenance; `_foretell_scene_includes` below lays them on this snapshot
    # first, so the two still cannot disagree.) On a first run there is no `vmx`/`dmx` artifact at all and `is_reusable` returns
    # `False` from its `KeyError` branch regardless of snapshot.
    #
    # **This is also the only place `Step.operand_digests` becomes argv** (whole-branch review,
    # m14). `reuse_decision` is where `_with_operand_digests` runs, so skipping or
    # conditionalising this call disables every `--group-digest`/`--contribution-digest`/
    # `--sched-digest` check in the repo, silently -- a builder that declared `operand_digests` and
    # never reached this call would read every operand unverified with no error and no advisory.
    # This is the repo's one production call site, and `tests/test_annual_tool.py` pins the
    # production path through it rather than around it.
    # Final fix E, fix round 4 (X1): the files the registered scene includes, with the digests
    # its first octree's build read (`compiled_scene_record`, taken right after that build), are
    # sealed into every octree this run registers (`SCENE_INCLUDES_PARAMETER`, digest-included) --
    # the flattened ones too (the per-group ASE, ASE_net and suns shells), which record no include
    # of their own though the include's text is in them. The reuse decision below is taken before
    # those octrees are re-sealed, so the parameter is laid on this snapshot's octree records
    # first (in memory, never saved: `_register_octree` re-opens the manifest from disk): with an
    # include edited, each octree's resolved digest moves and every matrix built on it is rebuilt
    # rather than reused. Measured before this: the same run re-sent after the edit reused 3 of 3
    # two-phase steps (6 of 9 with blind_control='lm83', 4 of 5 three-phase), and the reads
    # published the old values again.
    # With no include, nothing is laid and nothing moves.
    scene_includes = _scene_includes(compiled_scenes.get(octree_id))
    # Final fix F, fix round 2 (G1): an include the first octree's oconv dropped, from that build's
    # own stderr. Above `dry_run` (the octree is built there, R44(3)), so the preview and the real
    # call both name it, and sealed on every octree with the includes, so every results read of
    # what this run seals carries it.
    # Fix round 4 (I1): keyed on oconv's (or, in an include, xform's) `bad status from` line, so
    # an include xform rejected for any reason is caught, each named by the file and line that
    # wrote it (R823); the names xform could not find are still the ones a restore can bring back.
    failed_includes = _failed_include_commands(
        octree.warnings, root=state.paths.root, scene_files=state.scene_files,
    )
    dropped_includes = _dropped_includes(octree.warnings)
    if dropped_includes:
        scene_includes[SCENE_INCLUDES_DROPPED_PARAMETER] = list(dropped_includes)
    if failed_includes:
        scene_includes[SCENE_INCLUDE_FAILED_PARAMETER] = [dict(entry) for entry in failed_includes]
    if dropped_includes or failed_includes:
        advisories.append(_dropped_includes_advisory(
            dropped_includes,
            outside=_dropped_outside_roots(state.loaded.paths.root, dropped_includes),
            failed=failed_includes, root=state.loaded.paths.root,
        ))
    _foretell_scene_includes(state.loaded, set(compiled_scenes), scene_includes)
    steps = reuse_decision(state.loaded, steps, rebuild)
    reused_ids = _reused_ids(steps)
    if reused_ids:
        # `MATRIX_REUSED`, from `rad_engine.project.reuse_advisory` -- built there rather than here,
        # because its own docstring reserves it for the caller that *actually skipped* a build, and
        # this is that caller. Emitted once per call over the set actually flagged, not once per
        # step: the advisory names its artifacts, and one per step would be the same claim repeated.
        advisories.append(to_wire(reuse_advisory(reused_ids)))

    # `LUMINAIRES_EXCLUDED` (info, spec §7/R479): `run_annual` never lights a scene, so any placement
    # in this project is left out of both the octree it just built and the matrices it is about to
    # queue. Appended to the one `advisories` list both exits below read -- `DryRunOk` and
    # `AnnualResult` -- so the hand-off preview and the real call's job-submission result
    # carry the identical advisory rather than two builders that could drift (Task 17, R479).
    excluded = excluded_advisory(state.loaded.manifest, severity="info", tool="run_annual")
    if excluded is not None:
        advisories.append(excluded)
    # R818: a mirror or prism in the files this run compiles, on the same list both exits read.
    redirecting = light_redirecting_advisory(state.scene_files, tool="run_annual")
    if redirecting is not None:
        advisories.append(redirecting)

    if dry_run:
        # **A dry run has already written files, and none of them is in the manifest** -- stated here
        # and in docs/tool-surface.md §11 rather than left to be discovered (whole-branch review,
        # Minor 2). R39 puts every refusal above this line, and R44(3) makes an octree build one of
        # them, so a preview compiles the annual and exterior octrees -- and, on a 5-phase inference,
        # the all-black and suns octrees, the BSDF proxy, the opaque shell and a `1 + 144*mf^2`-source
        # suns scene (the glow files are per call and already removed, R822). `_register_octree`
        # runs *below* this return, because a preview must not mutate the manifest.
        #
        # So the trade is deliberate and it is the right way round: the alternative to unrecorded
        # bytes on disk is either a dry run that cannot refuse what a real call refuses, or a preview
        # that writes to the manifest. What it costs is provenance -- `project(action='describe')`
        # will never mention these files -- and one more thing: the octrees sit at paths fixed per
        # grid, so this preview has just rewritten, in place, any octree a queued job at this grid
        # still names. That job's steps carry the digest the octree had at their submit
        # (`_with_octree_digests`), so the worker refuses them with `OCTREE_REBUILT_SINCE_SUBMIT`
        # rather than tracing this preview's scene (R822, extended; measured before it: the queued
        # job succeeded with a V traced against the preview's geometry).
        return emit(DryRunOk(
            method=method_view, plan=_plan(steps), advisories=advisories or None
        ))

    # The octree is registered before the steps that name it as an edge: `submit` checks every
    # `built_from` that does not point at an artifact an earlier step of this same job will build,
    # and the octree is built by neither.
    _register_octree(
        runtime, state, octree_id=octree_id, octree_path=octree_path,
        compiled=compiled_scenes.get(octree_id), scene_includes=scene_includes,
    )
    if matrix_run:
        # The exterior octree is a second artifact, sealed the same way and for the same reason:
        # the `dmx` names it in `built_from`, and `submit`'s `check_edges` refuses an edge that
        # resolves to nothing.
        _register_octree(
            runtime, state, octree_id=exterior_octree_id, octree_path=exterior_octree_path,
            compiled=compiled_scenes.get(exterior_octree_id),
            scene_includes=scene_includes,
        )
    if black_octree_id is not None and black_octree_path is not None:
        # The all-black octree, sealed for the same reason as the two above: `REQUIRED_EDGES` carries
        # `vdmx` and `ddmx` rows (Task 13, R82) and both name this artifact, so `check_edges` refuses
        # the submit outright if it resolves to nothing. `black: True` in the parameters is what keeps
        # its provenance digest distinct from the lit octree built over the identical geometry
        # inputs -- two artifacts with the same edges and the same parameters would otherwise be
        # indistinguishable to `compute_digest`.
        _register_octree(
            runtime, state, octree_id=black_octree_id, octree_path=black_octree_path,
            parameters={"freeze": True, "black": True},
            compiled=compiled_scenes.get(black_octree_id),
            scene_includes=scene_includes,
        )
    if suns_octree_id is not None and suns_octree_path is not None:
        # The suns octree names the tensor-tree binding it was compiled from as an **edge**, on top of
        # the geometry every octree names: the BSDF proxy baked into it references that XML, so a
        # re-bound direct-sun BSDF must move this octree's digest and `cdsmx`'s with it. `mf` is in
        # the parameters because the scene compiled into it is `1 + 144*mf^2` sources -- a re-run at a
        # different subdivision is a different octree, not a cache hit.
        _register_octree(
            runtime, state, octree_id=suns_octree_id, octree_path=suns_octree_path,
            extra_edges=(direct_sun_input_id,) if direct_sun_input_id else (),
            parameters={"freeze": True, "suns": True, "mf": subdivision},
            compiled=compiled_scenes.get(suns_octree_id),
            scene_includes=scene_includes,
        )
    if ase_octree_id is not None and ase_octree_path is not None:
        # The ASE octree, sealed for the same reason as the four above: `REQUIRED_EDGES["asemx"]`
        # requires an artifact of kind `octree` in `built_from`, and `submit`'s `check_edges`
        # refuses an edge that resolves to nothing. It names **no** BSDF input, unlike the suns
        # octree beside it -- there is no proxy compiled into it to invalidate (`build_ase_octree`:
        # *"No BSDF proxy"*), and ASE is pre-blind, so a blind re-bind must not invalidate it.
        # `ase: True` keeps its provenance digest distinct from the lit octree built over the
        # identical geometry inputs, and `mf` is in the parameters because the scene compiled into
        # it is `1 + 144*mf^2` sources -- a re-run at a different subdivision is a different
        # octree, not a cache hit.
        _register_octree(
            runtime, state, octree_id=ase_octree_id, octree_path=ase_octree_path,
            parameters={"freeze": True, "ase": True, "mf": subdivision},
            compiled=compiled_scenes.get(ase_octree_id),
            scene_includes=scene_includes,
        )
    for group_name, group_octree_id in ase_octree_ids.items():
        # **Each per-group ASE octree, sealed for the reason the whole-grid one above is**:
        # `REQUIRED_EDGES["asemx"]` requires an artifact of kind `octree` in `built_from`, each
        # group's `asemx:<grid>:<g>` names its own, and `check_edges` refuses an edge that resolves
        # to nothing. `window_group` is in the parameters because these octrees differ from each
        # other and from the whole-grid one **only** in which apertures are blacked -- the geometry
        # inputs, `freeze` and the install's version are identical, so without it `compute_digest`
        # could not tell one group's octree from another's and `is_reusable` would serve either.
        _register_octree(
            runtime, state, octree_id=group_octree_id,
            octree_path=ase_octree_paths[group_name],
            parameters={
                "freeze": True, "ase": True, "mf": subdivision, "window_group": group_name,
            },
            compiled=compiled_scenes.get(group_octree_id),
            scene_includes=scene_includes,
        )
    if ase_net is not None:
        # **The ASE_net octree (R869)**, sealed after the per-group ones for their reason:
        # `asemxnet:<grid>` names it in `built_from`. `ase_net_blacked` is what tells it apart from
        # the whole-grid ASE octree and from an ASE_net octree that blacked another set -- the
        # geometry inputs, `freeze` and the install's version are the same for all of them.
        _register_octree(
            runtime, state, octree_id=ase_net.artifact_id, octree_path=ase_net.path,
            parameters={
                "freeze": True, "ase": True, "mf": subdivision,
                "ase_net_blacked": list(ase_net.groups),
            },
            compiled=compiled_scenes.get(ase_net.artifact_id),
            scene_includes=scene_includes,
        )

    # **`ambient_interpolation_radius_m`, docs/tool-surface.md#16-quality's totality claim -- "every
    # `run_*` result reports" it.** `octree` is whichever branch of `matrix_run` ran above (both set
    # it, above every refusal and above `dry_run`); `parameters` is `resolve_quality`'s own return
    # from the top of this function, so this is the parameter set the steps below actually queue,
    # never a preset re-looked-up by name (a `quality` dict override would otherwise disagree with
    # what the job traces). `AMBIENT_RADIUS_COARSE` is checked against this project's own geometry,
    # not the octree cube -- see `_room_bounding_box`.
    ambient_radius = ambient_interpolation_radius_m(parameters, octree.bbox[3])
    room_box = _room_bounding_box(state, env)
    if room_box is not None:
        smallest_dimension = min(room_box.size_x, room_box.size_y, room_box.size_z)
        if smallest_dimension > 0 and ambient_radius > smallest_dimension / 20:
            advisories.append(_ambient_radius_coarse_advisory(ambient_radius, smallest_dimension))

    record = runtime.store.submit(
        state.loaded, tool="run_annual", steps=_with_octree_digests(steps, octree_digests),
        job_id=job_id,
    )

    result = AnnualResult(
        job_id=record.job_id,
        ambient_interpolation_radius_m=ambient_radius,
        method=method_view,
        plan=_plan(steps),
        # **Counted off the flags actually set, never re-derived.** `reused` is the number of steps
        # the worker will decline to spawn; `rebuilt` is every other step, *including the ones that
        # commit no artifact* (the `genklemsamp` pipe half), because those genuinely run. The saving
        # is the sum of the reused steps' own declared estimates -- the same numbers the plan
        # publishes, so the arithmetic is checkable against the rows beside it rather than being a
        # second opinion about the same job.
        reuse=ReuseView(
            reused=len(reused_ids),
            rebuilt=sum(1 for step in steps if not step.reuse),
            saved_estimate_s=sum(step.estimate_s or 0.0 for step in steps if step.reuse),
        ),
        n_points=state.n_points,
        # **`state.weather.n_hours`, not `ANNUAL_TIMESTEPS` -- review round 1's Important 5.** This
        # field's own docstring calls it "the number this tool intends to produce, from the sky
        # matrix it is about to build", and as of Task 14's `n_hours` wiring, this module actually
        # knows that number rather than assuming a full common year: for the 24-hour EPW fixture
        # `TestNHoursIsWiredFromTheRealWeather` builds, publishing `8760` here would have been a
        # measurable falsehood the moment `n_hours` was in hand, not merely an approximation.
        timesteps=state.weather.n_hours,
        sky_subdivision=subdivision,
        advisories=advisories or None,
    )
    return emit(result)


def _with_octree_digests(steps: Sequence[Step], digests: Mapping[str, str]) -> list[Step]:
    """`steps`, each carrying `Step.input_digests` for every submit-time octree its argv names
    (R822, extended). Matched on the argv token as the chain builders write it, `str(path)`; a step
    naming no octree is returned unchanged. The worker hashes each listed file once, before the
    spawn."""
    out: list[Step] = []
    for step in steps:
        named = tuple((token, digests[token]) for token in step.argv if token in digests)
        out.append(replace(step, input_digests=named) if named else step)
    return out


def _direct_sun_xml_path(loaded: LoadedProject, input_id: str) -> str:
    """The tensor-tree XML `window_group(action='assign_bsdf', role='direct_sun')` registered under
    `input_id`, as the path the suns octree's BSDF proxy must reference.

    **Read off the manifest record rather than composed from the group and state names**, because
    what the proxy has to name is the file the *binding* points at -- `role='direct_sun'` copies
    nothing (Task 10a's ruling: `Cds` has to read the file where `oconv` can find it), so the caller's
    own path is the only path there is, and re-binding a different tensor tree must compile a
    genuinely different octree rather than the same one under a moved digest.

    Raises `DirectSunBindingUnusableError` when the record is absent or carries no file. Unreachable
    from the wire: `_refuse_missing_direct_sun_binding` has already refused, above the `dry_run`
    branch, any 5-phase call whose matrix-bound states do not each carry this exact input id, and
    `three_phase_target` has already refused every group/state combination but the single one this id
    is built from. It raises a `ProjectError` rather than a bare `KeyError`/`StopIteration` anyway,
    so that if those two ever stop implying it, the caller gets a shaped refusal instead of an
    uncoded `ToolError` carrying a traceback (check 0.4).
    """
    record = next((r for r in loaded.manifest.inputs if r.id == input_id), None)
    if record is None or not record.files:
        raise DirectSunBindingUnusableError(
            f"the 5-phase direct-sun term needs the tensor-tree BSDF registered as {input_id!r}, "
            "and this project has no such input with a file. Bind one with "
            "window_group(action='assign_bsdf', name=..., states={...}, role='direct_sun')."
        )
    return record.files[0].path


def _scene_includes(compiled: dict[str, Any] | None) -> dict[str, Any]:
    """The octree parameters the `includes` of a `compiled_scene_record` seal, or `{}` when it
    holds none (final fix E, fix rounds 4 and 5): `{path: digest}` under `SCENE_INCLUDES_PARAMETER`
    and, where an include was reached by a name that resolves elsewhere (a symlink), `{name:
    resolved}` under `SCENE_INCLUDE_LINKS_PARAMETER` (N2). `includes` is `None` when the include
    search raised, which `_scan_scene_for_inline_commands` refuses before any octree is built, so
    a run reaching here does not record one."""
    includes = compiled.get("includes") if isinstance(compiled, dict) else None
    if not isinstance(includes, list):
        return {}
    entries = [
        entry for entry in includes
        if isinstance(entry, dict) and isinstance(entry.get("path"), str)
    ]
    if not entries:
        return {}
    links = {
        name: entry["path"] for entry in entries
        for name in entry.get("named", ()) if isinstance(name, str)
    }
    return {
        SCENE_INCLUDES_PARAMETER: {entry["path"]: entry.get("digest") for entry in entries},
        **({SCENE_INCLUDE_LINKS_PARAMETER: links} if links else {}),
    }


#: The line xform writes for an include it cannot open (`rad_engine.advisories.RULES`' first
#: `OCTREE_INPUT_DROPPED` row), lower-cased as `Rule.matches` compares it.
_XFORM_CANNOT_FIND: Final[str] = "xform: cannot find file"

#: The line oconv writes for an inline command of a scene file that exited non-zero, and the one
#: xform writes for such a command inside an include it is reading (measured, Radiance 6.1a):
#: `oconv: warning - bad status from "!xform -zz <file>"` and `xform: (<include>): warning - bad
#: status from "!xform <file>"`. Neither is written for an empty or comment-only include, which xform
#: reads and exits 0 on (`(<file>): empty file` only).
_BAD_STATUS: Final = re.compile(
    r'^(?:oconv: warning - |xform: \((?P<file>.*)\): warning - )bad status from "(?P<command>.*)"$'
)

#: Final fix F, fix round 5 (L1): the line xform writes for an error inside a file it is reading
#: (measured, Radiance 6.1a: `xform: (garbage.rad): unknown object type "is"`), written before the
#: `bad status` line of the command that read it. Its own warnings (`warning - empty file`, a nested
#: `warning - bad status from`) are not errors and are not this line. The file is named as xform
#: opened it: as the `!xform` line wrote it, without a leading `./`, or by the path RAYPATH found
#: it at.
_XFORM_FILE_ERROR: Final = re.compile(r"^xform: \((?P<file>.*)\): (?!warning - )")

#: The line xform writes for an `!xform` line it rejects before reading any file (measured:
#: `xform: command line error at '-zz'`). A failure that writes neither this nor
#: `_XFORM_FILE_ERROR` nor a missing file is sealed with no kind and worded for either.
_XFORM_LINE_ERROR: Final[str] = "xform: command line error"

#: Final fix G, fix round 3 (S3): every text xform 6.1a writes for a line it rejects before reading
#: any file (its binary's format strings; the first three measured, each followed by oconv's bad
#: status): `-zz`, `-f` with no file, `-a` with no count, and `-` twice.
_XFORM_LINE_ERRORS: Final[tuple[str, ...]] = (
    _XFORM_LINE_ERROR, "xform: missing file for -f option", "xform: missing count for -a option",
    "xform: cannot use stdin more than once",
)

#: Final fix G, fix round 3 (S3): the argument file of `-f` that xform could not open, or found
#: empty (measured: `!xform -f nosuch.txt` writes `xform: cannot open argument file "nosuch.txt"`,
#: `!xform -f empty.txt` writes `xform: empty argument file "empty.txt"`; each then oconv's bad
#: status, and nothing of the line). It was sealed with no kind, and worded "not for a file it
#: could not find".
_XFORM_ARGFILE_ERROR: Final = re.compile(
    r'^xform: (?P<how>cannot open|empty) argument file "(?P<file>.*)"$'
)


def _dropped_includes(warnings: Sequence[str]) -> tuple[str, ...]:
    """The `!xform` includes an octree build dropped because xform could not find them, sorted,
    each as the line wrote it (final fix F, fix round 2, G1). oconv exits 0 on such a drop when
    the file defined nothing the rest of the scene uses (measured), so the build succeeds.

    **Only xform's own `cannot find file "<name>"` lines** (fix round 3, H4), each line's quoted
    name taken whole. oconv's two lines about the same command quote the command, not the file,
    and `OCTREE_INPUT_DROPPED`'s subject for them is its last word: measured (Radiance 6.1a),
    `!xform tab<TAB>x.rad` failed on `tab` and xform never looked at `x.rad`, which the
    command's last word named; and oconv writes `(<file>): empty file` for an include that exists
    and holds no geometry (an empty or comment-only file), and for the registered scene file itself
    when every line of it failed. Either, named here, would have every results read refused
    `SCENE_INCLUDE_CHANGED` for a file that "can be read now", by every re-run again. An
    unreadable file is reported by xform as not found too (measured), and is one.

    These are the names a restore can bring back. Every command whose output oconv did not
    receive, these included, is `_failed_include_commands`' (fix round 4, I1)."""
    names: set[str] = set()
    for line in warnings:
        advisory = classify_line(line)
        if (
            advisory.code == "OCTREE_INPUT_DROPPED" and advisory.subject
            and _XFORM_CANNOT_FIND in line.lower()
        ):
            names.add(advisory.subject)
    return tuple(sorted(names))


#: The `sensor_grid(action='create')` arguments a grid placed under LM-83 takes, of those a
#: create records (`rad_mcp.server.sensor_grid`): the others belong to EN 12464-1's zoned grids.
_LM83_CREATE_KEYS: Final = ("name", "polygon", "height", "normal", "lm83_edition")


def _sparse_grid_remedy(
    loaded: LoadedProject, *, project: str, grid: str, metrics: Sequence[str], epw: str,
    north: float, method: str, sky_subdivision: int | None, quality: str | dict[str, Any],
    blind_control: str | None, rebuild: Sequence[str] | None,
) -> str | None:
    """`SparseLm83GridError`'s remedy, spelled whole (final fix F, fix round 5, P3): the grid's
    recorded create with `standard='lm83'` (its polygon and normal kept, and its height only where
    the record was an LM-83 grid: final fix G, G-5), then this call re-sent, every argument as
    given. `None` where the
    grid's record holds no create call or no polygon (a zoned grid, or a record from before the
    arguments were recorded), and `rad_engine`'s words stand.

    Measured before this (R242's recovery, `TestTheBlendIsNotPlannedOnAGridTooSparseForItsBand`):
    the create over the same polygon with LM-83's placement, then the same run, plans the blend."""
    record = next(
        (r for r in loaded.manifest.inputs
         if r.kind == "sensor_grid" and r.id == f"{SENSOR_GRID_INPUT_PREFIX}{grid}"),
        None,
    )
    if (
        record is None or record.zones is not None or record.registered_by is None
        or record.registered_by.action != "create"
        or not record.registered_by.arguments.get("polygon")
    ):
        return None
    recorded = record.registered_by.arguments
    # Final fix G (G-5, controller ruling): the recorded height (and edition, which only chooses
    # the default height) is kept only from a grid that was itself LM-83's; from another
    # standard's record (EN 17037's 0.85 m) it is left out, and sensor_grid applies LM-83's own
    # default. This refusal is LM-83-12's alone (`chain.blind_schedule_steps` does not apply it
    # under LM-83-22), and LM-83-12 is sensor_grid's default edition: 0.8 m.
    keys = (
        _LM83_CREATE_KEYS if recorded.get("standard") == "lm83"
        else tuple(key for key in _LM83_CREATE_KEYS if key not in ("height", "lm83_edition"))
    )
    create = ", ".join(
        ["action='create'", f"project={project!r}", "standard='lm83'"]
        + [f"{key}={recorded[key]!r}" for key in keys if recorded.get(key) is not None]
    )
    sent: list[tuple[str, Any]] = [
        ("project", project), ("grid", grid), ("metrics", list(metrics)),
        ("blind_control", blind_control), ("epw", epw), ("north", north),
        ("sky_subdivision", sky_subdivision), ("quality", quality), ("method", method),
        ("rebuild", list(rebuild) if rebuild is not None else None),
    ]
    rerun = ", ".join(f"{key}={value!r}" for key, value in sent if value is not None)
    return (
        f"Re-create grid {grid!r} over the same polygon with LM-83's own placement, "
        f"sensor_grid({create}), then send this call again, run_annual({rerun}). If the polygon "
        "is the number that is wrong, re-create the grid with the outline the space actually has "
        "instead."
    )


def _failed_include_commands(
    warnings: Sequence[str], *, root: Path, scene_files: Sequence[str],
) -> tuple[dict[str, Any], ...]:
    """Every inline command of the scene whose output oconv did not receive, from the octree
    build's stderr (final fix F, fix round 4, I1): one entry per `bad status from` line
    (`_BAD_STATUS`), in the order written, as `SCENE_INCLUDE_FAILED_PARAMETER` seals it --
    `{"command": its program, "file": ..., "line": ..., "names": [...]}`.

    Round 3 named only xform's `cannot find file` lines, so an include xform rejected for any other
    reason (measured: `!xform -zz <existing file>`, "command line error at '-zz'") was dropped with
    no flag on the reply or on any read. Every failure writes this line and an empty include does
    not, so it is the key. `names` are the files xform could not find among the command's words,
    or quoted in it (`_dropped_includes`), empty for any other failure.

    `file` and `line` name the command by the file and line that wrote it (R823: an include's
    target is never put in a message): oconv's line comes from a registered scene file, searched
    in `scene_files` order, and xform's names the include it was reading. The k-th failure of one
    command text is its k-th occurrence. Where no line holds the command, `line` is `None`, and
    `file` too for oconv's.

    Fix round 5 (L1, P1): what the failure lost, measured (Radiance 6.1a). xform reads the files a
    line names in order and stops at the first it cannot find or cannot parse, so every file the
    line names after that one gives oconv nothing (`!xform a.rad b.rad` with `a.rad` missing drops
    `b.rad`'s geometry too), and a file with an error in it gives what xform wrote before the error
    (valid geometry, then an unknown object type: the leading geometry is in the octree). So each
    entry also carries `kind` -- `"missing"` (a name xform could not find), `"content"` (an error
    inside a file the line names: `_XFORM_FILE_ERROR` written since the previous `bad status`
    line), `"line"` (xform's `_XFORM_LINE_ERROR`, the line itself, as `-zz`) or `None` (none of
    these was written, and the message says either of the last two) -- `error_in`, the word of
    the line naming the file the error was in (`None` where none matches it), and `after`, the
    words the line writes after the file xform stopped at.

    Final fix G, fix round 2 (Q2): `kind` is `"unlisted"` where xform could not find a file that
    is not among the line's own words (an `-f` file can list it): nothing more is known of what
    it lost, so `before` and `after` are `[]`.

    Final fix G (G-4): and `before`, the files an `!xform` line whose `-a` options make more than
    one copy names before the one xform stopped at, each of which gave oconv its first copy only
    (`_first_copy_only`); `[]` otherwise."""
    missing = _dropped_includes(warnings)
    entries: list[dict[str, Any]] = []
    taken: dict[tuple[str | None, str], int] = {}
    since: list[str] = []
    for raw in warnings:
        found = _BAD_STATUS.match(raw.rstrip("\r\n"))
        if found is None:
            since.append(raw.rstrip("\r\n"))
            continue
        errors = [
            error.group("file") for error in map(_XFORM_FILE_ERROR.match, since)
            if error is not None
        ]
        argfiles = [
            argfile for argfile in map(_XFORM_ARGFILE_ERROR.match, since) if argfile is not None
        ]
        since_command, since = since, []
        command = found.group("command").rstrip(" \t")
        words = [word for word in re.split(r"[ \t]+", command.lstrip("!")) if word]
        includer = found.group("file")
        if includer is None:
            candidates = [
                Path(name) if Path(name).is_absolute() else root / name for name in scene_files
            ]
        else:
            candidates = [Path(includer) if Path(includer).is_absolute() else root / includer]
        key = (includer, command)
        skip = taken.get(key, 0)
        taken[key] = skip + 1
        where = _command_location(candidates, command, skip=skip)
        names = [
            name for name in missing
            if name in words[1:] or f'"{name}"' in command or f"'{name}'" in command
        ]
        arguments = words[1:]
        error_in = (
            _erroring_word(errors[-1], arguments) if errors and not names
            # Fix round 3 (S3): the argument file, as the line wrote it (xform quotes it so).
            else _erroring_word(argfiles[-1].group("file"), arguments) if argfiles and not names
            else None
        )
        stopped = (
            next((word for word in arguments if word in names), None) if names
            else None if argfiles else error_in
        )
        entries.append({
            "command": words[0] if words else "",
            "file": (
                str(where[0]) if where is not None
                else str(candidates[0]) if includer is not None else None
            ),
            "line": where[1] if where is not None else None,
            "names": names,
            "kind": (
                "missing" if names
                # Final fix G, fix round 2 (Q2): xform could not find a file that is not one of the
                # line's own words -- one an `-f` file lists (measured: `!xform -f args.txt`, the
                # file holding `-a 2 -t 2 0 0 shelf.rad missing.rad after.rad`, kept copy 1 of the
                # shelf alone). Which files and copies it lost is not known from the line.
                else "unlisted" if words and words[0] == "xform" and any(
                    _XFORM_CANNOT_FIND in line.lower() for line in since_command)
                # Fix round 3 (S3): the `-f` file itself could not be opened, or was empty.
                else ("argfile" if argfiles[-1].group("how") == "cannot open" else "argfile_empty")
                if argfiles
                else "content" if errors
                else "line" if any(
                    line.startswith(_XFORM_LINE_ERRORS) for line in since_command)
                else None
            ),
            "error_in": error_in,
            "after": (
                arguments[arguments.index(stopped) + 1:] if stopped is not None else []
            ),
            "before": (
                _first_copy_only(arguments, stopped)
                if stopped is not None and words and words[0] == "xform" else []
            ),
        })
    return tuple(entries)


#: How many words each `xform` option takes after itself (`xform(1)`; `-e` measured: accepted
#: alone). `-f` reads further arguments from a file, which this cannot see.
_XFORM_OPTION_ARITY: Final = {
    "-c": 0, "-I": 0, "-e": 0, "-n": 1, "-m": 1, "-t": 3, "-rx": 1, "-ry": 1, "-rz": 1, "-s": 1,
    "-mx": 0, "-my": 0, "-mz": 0, "-i": 1, "-a": 1,
}


def _first_copy_only(arguments: Sequence[str], stopped: str) -> list[str]:
    """The files an `!xform` line names before `stopped` (the file xform stopped at) where its `-a`
    options make more than one copy, else `[]` (final fix G, G-4). xform writes every file of one
    copy before the next, so stopping in the first copy leaves each of these its first copy only
    (measured, Radiance 6.1a: `-a 2 -t 2 0 0 good.rad missing.rad` writes `a0.goodshelf` alone;
    `-a 2 ... -a 3 ...` writes `a0.0.goodshelf` alone; without `-a`, or with `-a 1`, nothing is
    lost before it). The files start after the options, as xform reads them; an option it does
    not take, or `-f`, leaves the files unknown, and `[]`."""
    index, copies = 0, 1
    while index < len(arguments) and arguments[index].startswith("-"):
        option = arguments[index]
        arity = _XFORM_OPTION_ARITY.get(option)
        if arity is None:
            return []
        if option == "-a":
            try:
                copies *= int(arguments[index + 1])
            except (IndexError, ValueError):
                return []
        index += 1 + arity
    files = list(arguments[index:])
    if copies <= 1 or stopped not in files:
        return []
    return files[:files.index(stopped)]


def _erroring_word(opened: str, words: Sequence[str]) -> str | None:
    """The word of an `!xform` line naming the file xform reported an error in (fix round 5, L1),
    or `None`. xform names it as it opened it (measured: `./a.rad` as `a.rad`, a file RAYPATH found
    by its full path), so the word is the one written the same, or else the one word whose final
    component is the same; two such words, or none, name nothing."""
    if opened in words:
        return opened
    same = {word for word in words if Path(word).name == Path(opened).name}
    return next(iter(same)) if len(same) == 1 else None


def _command_location(files: Sequence[Path], command: str, *, skip: int) -> tuple[Path, int] | None:
    """The file and line number of the `skip`-th line, over `files` in order, that ends with
    `command` as its own words (the text from its `!` to the line's end, as oconv and xform quote
    it), or `None`. Lines are counted where `fgets` counts them (`\\n`), and a lone `\\r` ends a
    command as `fgetline` ends one."""
    seen = 0
    for path in files:
        try:
            text = read_scene_text(path)
        except (OSError, ValueError):
            continue
        for number, physical in enumerate(text.split("\n"), start=1):
            for piece in physical.split("\r"):
                stripped = piece.rstrip(" \t")
                if not stripped.endswith(command):
                    continue
                before = stripped[:-len(command)]
                if before and before[-1] not in " \t":
                    continue
                if seen == skip:
                    return path, number
                seen += 1
    return None


def _dropped_includes_advisory(
    dropped: Sequence[str], *, outside: Collection[str] = frozenset(),
    failed: Sequence[dict[str, Any]] = (), root: Path | None = None,
) -> Advisory:
    """`OCTREE_INPUT_DROPPED` (error) on `run_annual`'s preview and reply, naming each include
    its octree build dropped (final fix F, fix round 2, G1). Before this the finding reached the
    caller only on `job(action='status')`, from the worker's own spawn, and named no file; the
    results it sealed read with no flag.

    Fix round 3 (H1): a name in `outside` resolves outside every allowed directory
    (`results_tool._dropped_outside_roots`). Put back there, this tool refuses the scene
    (PATH_OUTSIDE_ROOT) and no results read looks for it, so "restore it" is not said of it: it is
    told to move inside a root. With no such name the message is the round-2 one.

    Fix round 4: such a name is named by the file and line that wrote it (J1, R823); and a command
    in `failed` (`_failed_include_commands`) that names no missing file -- one xform rejected for
    another reason -- is named the same way and told to be corrected (I1)."""
    rejected = [entry for entry in failed if not entry.get("names")]
    parts: list[str] = []
    if dropped:
        one = len(dropped) == 1
        it = "it" if one else "them"
        shown = [
            name if name not in outside else _missing_include_label(name, failed)
            for name in dropped
        ]
        head = (
            f"this run compiles the registered scene without {', '.join(shown)}: xform could "
            f"not find {'that file' if one else 'those files'} where "
            + _where_named(failed, one=one)
            + ", and oconv exited 0 without "
            f"{'its' if one else 'their'} geometry, so the results this run seals describe the "
            f"scene without {it}."
        )
        away = [name for name in dropped if name in outside]
        if not away:
            parts.append(
                f"{head} If the scene should include {it}, restore {it} and send run_annual "
                "again. Every results read of these results says so, and refuses them "
                "(SCENE_INCLUDE_CHANGED) once a file is back at that name, until run_annual is "
                "re-run."
            )
        else:
            kept = [name for name in dropped if name not in outside]
            parts.append(f"{head} Every results read of these results says so." + (
                f" If the scene should include {', '.join(kept)}, restore "
                f"{'it' if len(kept) == 1 else 'them'} and send run_annual again: once a file is "
                f"back at {'that name' if len(kept) == 1 else 'one of those names'}, every "
                "results read refuses these results (SCENE_INCLUDE_CHANGED) until run_annual is "
                "re-run."
                if kept else ""
            ) + " " + _dropped_outside_sentence(
                [_missing_include_label(name, failed) for name in away],
                then="send run_annual again.",
            ))
        # Fix round 5 (P1): what xform never read after a missing file, after the restore
        # sentence, so its "it" still names the missing file.
        parts.extend(_stopped_after_sentences(root or Path("."), failed))
    if rejected:
        # Fix round 5 (L1): worded per failure kind (`results_tool._failed_command_parts`).
        parts.extend(_failed_command_parts(
            root or Path("."), rejected,
            opening=(
                "this run compiles the registered scene without all of the output of"
                if not dropped else "It also compiles it without all of the output of"
            ),
            also="It also compiles it without all of the output of",
            again="send run_annual again.", at_run="",
            values="the results this run seals",
            says="" if dropped else "Every results read of these results says so.",
        ))
    return Advisory(code="OCTREE_INPUT_DROPPED", severity="error", message=" ".join(parts))


def _foretell_scene_includes(
    loaded: LoadedProject, octree_ids: set[str], scene_includes: dict[str, Any],
) -> None:
    """Lay `scene_includes` on the in-memory records of `octree_ids` already in `loaded`'s
    manifest, as `_register_octree` will seal them, so `reuse_decision` judges each matrix against
    the octree digest it is about to have (final fix E, fix round 4, X1). Every other parameter is
    the one this run seals too (each octree's id names what else differs), and `compiled` is
    digest-excluded. Nothing is saved: a dry run leaves the manifest as it found it."""
    for artifact in loaded.manifest.artifacts:
        if artifact.kind != "octree" or artifact.id not in octree_ids:
            continue
        parameters = {
            key: value for key, value in artifact.parameters.items()
            if key not in (
                SCENE_INCLUDES_PARAMETER, SCENE_INCLUDE_LINKS_PARAMETER,
                SCENE_INCLUDES_DROPPED_PARAMETER, SCENE_INCLUDE_FAILED_PARAMETER,
            )
        }
        parameters.update(scene_includes)
        artifact.parameters = parameters


def _register_octree(
    runtime: Runtime,
    state: _ValidatedProject,
    *,
    octree_id: str,
    octree_path: Path,
    extra_edges: tuple[str, ...] = (),
    parameters: dict[str, Any] | None = None,
    compiled: dict[str, Any] | None = None,
    scene_includes: dict[str, Any] | None = None,
) -> None:
    """Seal a submit-time octree into the manifest so the matrices built on it may name it as an edge.

    Under both locks in the one legal order (`Runtime.modifying`) -- thread lock then file lock --
    because this is a read-modify-write of the manifest racing the job worker and any second
    process, exactly the interval `save_manifest`'s whole-document write loses.

    **`extra_edges` and `parameters` exist for the two 5-phase octrees (Task 14b), and default to
    what the three older call sites already passed.** Every octree names this project's geometry
    inputs; the suns octree additionally names the `role='direct_sun'` BSDF input, because the proxy
    surface compiled into it references that file and a re-bind must invalidate it. `parameters`
    defaults to `{"freeze": True}` -- the same dict the annual and exterior octrees have carried since
    Phase 4 -- and is overridden rather than extended at the call site, so what each octree records is
    readable in one place beside the build that produced it. It is not decoration:
    `rad_core.manifest.compute_digest` hashes it, so it is what tells two octrees built from the
    identical geometry inputs apart.

    **The `built_from` list below names this project's geometry inputs and NOT its materials
    inputs, so ruling R361 is UNFIXED here -- deliberately, on measurement.**
    `point_in_time_tool._scene_files(loaded)` -- what `_validate_project` seals into
    `state.scene_files` -- builds its materials half from an unfiltered comprehension over
    `loaded.manifest.inputs` where `record.kind == "materials"`, so the scene carries every such
    input while the comprehension below names only the geometry ones. The other way an id could
    reach this list is `extra_edges`, and no caller uses it for materials: an `ast` walk over this
    module's `_register_octree(...)` calls finds exactly one passing
    `extra_edges` at all -- the suns octree, passing the `role='direct_sun'` BSDF input -- so no
    call site supplies the materials that way either. A materials-only edit is therefore invisible
    to the sealed octree's own provenance: `project(action="describe")` reports the octree valid
    over a scene it no longer matches (the `octree:annual:grid  valid=True  invalidated_by=[]` row
    measured for the R361 finding). It used to be invisible in a second place too:
    `rad_engine.export._octree_command` (`_octree_step` since R731, 2026-09-27) rendered `oconv -f`
    over `built_from`'s file-backed edges alone, so the exported script's file list had no
    materials in it (ruling R358). **R358 is fixed for the export (R833, R849) and R361 stands for
    `built_from`**: `compiled`, when given, is the build's `rad_engine.export.compiled_scene_record`
    and is sealed as the octree's `COMPILED_PARAMETER`, from which `export(action="scripts")`
    rebuilds the octree: the files on its build's `oconv` and `xform` lines in build order, and the
    digests of the files their `!xform` lines include, which the export checks. It is digest-excluded
    (`rad_core.manifest.DIGEST_EXCLUDED_PARAMETERS`), so no digest and no reuse decision changes.
    `rad_mcp.point_in_time_tool`'s own registrar *does* name both kinds (ruling R356); the two
    registrars differ, and here the difference is a decision rather than an oversight.

    **What that costs is the label and not the number, and the distinction is a property of
    `rad_engine.project.REQUIRED_EDGES`.** `vmx`, `vdmx`, `dmx`, `ddmx`, `cdsmx` and `asemx` each
    name `materials` in their own `EdgeRequirement.input_kinds`, while `REQUIRED_EDGES["octree"]`
    names only `geometry`. A materials edit therefore invalidates each of those matrices on its own
    edge and `is_reusable` returns `False` for it whatever the octree says -- so the matrices an
    sDA or an ASE number is computed from rebuild on the edit itself, and the octree's stale
    `valid=True` row is a wrong label rather than a wrong number.

    **The materials edges were added (whole-branch fix wave, B4) and then reverted, because naming
    them cost real recomputation.** With the comprehension present, a five-phase run after a
    three-phase run over the same project stops reusing the V and D it should reuse:
    `tests/test_five_phase.py::TestCrossMethodReuse::test_a_five_phase_run_after_a_three_phase_run_reuses_v_t_d_and_s`
    asserts `run.reused_kinds == {"vmx", "dmx", "smx"}` and gets `{"smx"}` -- of those three kinds,
    the two that stop being reused are exactly the two whose `input_kinds` carry `materials`, and
    `smx`'s carry only `weather`. Measured at the revert by single-variable mutation of this one
    comprehension, in both directions on the same machine, with the file restored byte-identical
    after: absent -> `1 passed`, present -> `1 failed` on `assert {'smx'} == {'dmx','smx','vmx'}`.
    A provenance label was being traded for two genuinely rebuilt matrices, and the owner ruled on
    that measurement. `tests/test_export_tool.py::TestTheOctreeProvenanceDoesNotNameTheMaterials`
    pins the absence from `built_from`, so restoring the edges turns that test red rather than
    passing quietly, and pins the materials on the exported `oconv` line (R833).
    """
    with runtime.modifying(state.paths):
        project = open_project(state.paths.root, radiance_version=runtime.launcher.version)
        register_artifact(
            project,
            artifact_id=octree_id,
            kind="octree",
            path=octree_path,
            built_from=[
                *(record.id for record in project.manifest.inputs if record.kind == "geometry"),
                *extra_edges,
            ],
            parameters={
                **(parameters if parameters is not None else {"freeze": True}),
                **({COMPILED_PARAMETER: compiled} if compiled is not None else {}),
                # Final fix E, fix round 4 (X1): digest-included, and only when the scene includes
                # a file, so an octree over a scene with none keeps the digest it always had.
                **(scene_includes or {}),
            },
            radiance_version=runtime.launcher.version,
        )
        # `register_artifact` persists the manifest itself (`save=True` by default), so the
        # re-open below reads the sealed document rather than the pre-registration one this
        # function was handed -- `submit`'s `check_edges` must see the octree it is about to name.
        state.loaded = open_project(state.paths.root, radiance_version=runtime.launcher.version)
