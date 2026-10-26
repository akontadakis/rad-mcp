"""Locating Radiance and building a correct environment for it.

Owns the RAYPATH rule. See CONTEXT.md: RAYPATH must contain both ``.`` and the Radiance library
directory. Unset gives a hard error (``cannot find function file "rayinit.cal"``); library-only
makes relative ``!xform`` includes fail while ``oconv`` still exits 0.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path

__all__ = ["RadianceEnv", "RadianceNotFound", "discover", "locate", "raypath_dirs_of"]

#: Prefixes to probe when Radiance is not already on PATH.
_COMMON_PREFIXES = (
    "/usr/local/radiance",
    "/usr/local",
    "/opt/radiance",
    "/opt/homebrew",
    "C:/Radiance",
)

#: Binaries we consider non-negotiable. Absence means the install is unusable, not merely limited.
#: `xform` joined this tuple under ruling R694 (Phase 13a): every sky `rad_engine.sky.generate_sky`
#: produces on a project that declares a north is rotated by a spawned `xform -rz`, because
#: `gensky -r`/`gendaylit -r` do not rotate on this Radiance 6.1a (`gensky -r` is the solar
#: radiance, `gendaylit -r` an unknown option) -- so a server that can build a point-in-time,
#: render or glare sky must be able to run `xform` on it, the same test every other member of this
#: tuple is placed by. It was already listed twice below, under `photometry` and
#: `geometry_import`, for two other spawns that predate this one; both comments are corrected
#: rather than removed, since they still explain why `xform` was needed for those capabilities
#: first.
_ESSENTIAL = ("oconv", "rtrace", "rpict", "getinfo", "gensky", "xform")

#: Binaries whose absence disables a specific capability rather than the whole server.
_CAPABILITY_BINARIES = {
    # `rcontrib` added alongside the binaries already here (Task 0, Phase 3): it is the
    # matrix-generation binary Phase 6 actually invokes -- `rcontrib` + `genklemsamp`, not
    # `rfluxmtx`, per the 2026-07-30 ruling recorded in docs/plan.md's decision log -- and until now
    # it appeared in neither this tuple nor `_ESSENTIAL`, so `env.path("rcontrib")` raised on every
    # install `capabilities()` reported as `matrix_methods: True`. Not `_ESSENTIAL`: a project that
    # never reaches Phase 4/5/6 (view rendering only) never calls it -- the same test `getbbox`'s own
    # comment below gives for its placement. (`xform` was reasoned the identical way when it was
    # added below, under `photometry` and `geometry_import`, until ruling R694 (Phase 13a) made it
    # `_ESSENTIAL` for an unrelated reason: rotating a declared-north sky.)
    # `genskyvec` added the same way (Phase 4 Task 4), and it is the third instance of this exact
    # gap after `rcontrib` above and `gendaylit` below: a binary present on every install, spawned
    # by a real code path, and absent from both this dict and `_ESSENTIAL`, so `env.path` refused
    # it unconditionally while `capabilities()` advertised `matrix_methods: True`. It converts a
    # `.rad` sky description into the one-column sky vector `dctimestep` multiplies the daylight
    # coefficient matrix by, so a 2-phase point-in-time run cannot complete without it. Three
    # occurrences is a pattern rather than three oversights -- a binary earns an entry here the
    # moment any module spawns it, and check 1.x's capability assertions do not catch the gap
    # because they test the capability flag, not that every binary behind it resolves.
    # `genklemsamp` added alongside `rad_engine.matrices.build_genklemsamp_argv` (Phase 6a Task 4,
    # fix round 1): the comment two lines up already named it as the D matrix's own binary the day
    # `rcontrib` was added here, and it sat unlisted until the D-matrix pipe was actually built and
    # `env.path("genklemsamp")` raised `RadianceNotFound` against an install that has the binary at
    # `/usr/local/radiance/bin/genklemsamp` -- the fourth occurrence of the exact gap this comment
    # chain already describes three times over.
    # `cnt` and `rcalc` added alongside `rad_engine.suns.build_suns_argv` (Phase 7a Task 7): the
    # fifth occurrence of the identical gap -- `env.path("cnt")` raised `RadianceNotFound` against an
    # install that has it at `/usr/local/radiance/bin/cnt` (and `rcalc` alongside it) the moment
    # check 7.2 first ran the real two-spawn suns pipe. `rcalc` is general-purpose (also used bare by
    # the D-matrix pipe's own `reinhart.cal` measurements in this module's sibling docstrings, though
    # never spawned by code until now), and `cnt` exists only to feed it a record count -- both are
    # the 5-phase method's own binaries, so both are grouped here rather than given a new capability.
    # `rcollate` and `rlam` added alongside `rad_engine.chain.five_phase_steps`' own combination
    # pipeline (Phase 7a Task 12): the sixth occurrence of the identical gap, and the first where the
    # module doing the spawning sits on the same branch as this comment chain. Both binaries back
    # `rad_engine.annual.build_rcollate_leg_argv`/`build_rcollate_final_argv`/`build_rlam_argv` --
    # four `rcollate` steps and one `rlam` step per 5-phase run -- and neither name appeared here
    # until this fix, so every one of those five steps raised `RadianceNotFound` on every install,
    # however complete, while `capabilities()["matrix_methods"]` still reported `True`. Registered
    # under `matrix_methods` rather than a new capability for the identical reason `cnt`/`rcalc` are:
    # both are the 5-phase method's own binaries, useless without the rest of this tuple.
    "matrix_methods": (
        "rfluxmtx", "dctimestep", "rmtxop", "gendaymtx", "rcontrib", "genskyvec", "genklemsamp",
        "cnt", "rcalc", "rcollate", "rlam",
    ),
    # `genblinds`, `checkBSDF` and `wrapBSDF` added alongside `genBSDF` (Phase 5): this is the
    # **fourth** instance of the gap the `matrix_methods` comment above describes three times, and
    # the pattern it predicted -- "a binary earns an entry here the moment any module spawns it".
    # `rad_engine.bsdf` spawns `genblinds` for the slat geometry and `checkBSDF` for check 5.3's
    # cross-check on the pure parser, and both resolve through `env.path`, so before this row grew
    # they raised `RadianceNotFound` on an install carrying them at `bin/checkBSDF` while this same
    # dict advertised `bsdf: True`. Measured, not assumed: all six are present in
    # /usr/local/radiance/bin on this machine (Radiance 6.1a).
    #
    # `wrapBSDF` is listed though `rad_engine.bsdf` never spawns it directly -- `genBSDF -W` does,
    # internally, and an install missing it fails *inside* a 90-second generation rather than at
    # argv time. Listing it turns that into a capability answer before any ray is traced. The two
    # conversion binaries `bsdf2klems`/`bsdf2ttree` are deliberately **absent**: Phase 5 defers
    # basis conversion (docs/plan.md Phase 5, Defers), and a binary listed here is a binary this
    # server claims to need -- an install without them can still do everything the `bsdf` tool
    # offers today.
    #
    # `getbbox` is here rather than in `_ESSENTIAL` and the choice is deliberate. It is a general
    # geometry reader, not a BSDF binary -- `rad_engine.bsdf.bounding_box_of` spawns it to check
    # genBSDF's half-space precondition, and Phase 10's CAD import will want it too. But
    # `_ESSENTIAL` means "absence makes the install unusable", and an install without `getbbox` can
    # still run every point-in-time and annual study in Phases 3 and 4. So it is registered under the
    # capability that actually spawns it today, and moves to a shared home when a second capability
    # needs it -- the same test every other entry in this dict is placed by.
    "bsdf": ("genBSDF", "genblinds", "checkBSDF", "wrapBSDF", "getbbox"),
    # `glarendx` added alongside `rad_engine.glarendx.run_ugr_index` (Phase 11c Task 10): the
    # **ninth** occurrence of the gap the `matrix_methods`/`bsdf` comments above already describe
    # eight times over, and the first one found by a caller rather than by a reviewer.
    # `rad_engine.glarendx` (Phase 11c Task 6) spawns `glarendx -t ugr` through `env.path`, and the
    # name appeared in neither this dict nor `_ESSENTIAL`, so **every** `run_ugr` call raised
    # `RadianceNotFound` -- measured against this machine's own
    # `/usr/local/radiance/bin/glarendx`, which fact 13 of
    # docs/tool-surface.md#verified-engine-facts-this-spec-depends-on already records as present.
    # Registered under `glare` rather than a capability of its own for the reason every
    # other entry here is placed by: it is a glare binary, `run_ugr` is part of this server's glare
    # surface, and an install that cannot rate glare should not advertise `glare: True`. A new
    # capability key would also have to be added to the published `radiance_status` block in
    # docs/tool-surface.md, which `tests/test_env.py::TestThePublishedCapabilityGroupsMatchTheCode`
    # parses -- a documentation change this task does not own.
    "glare": ("evalglare", "glarendx"),
    # `pcond` added alongside `rad_engine.postprocess` (Phase 9 Task 7): it conditions a rendered
    # picture for output (dynamic-range compression, optionally the human-vision-mimicking bundle),
    # the same post-processing family `falsecolor`/`pcomb`/`pfilt` are already grouped under here --
    # not a BSDF, glare or geometry binary, and not worth a capability of its own for the identical
    # reason `ies2rad` isn't split out from `photometry` below. Measured present on this machine
    # (Radiance 6.1a, `/usr/local/radiance/bin/pcond`).
    "falsecolor": ("falsecolor", "pcomb", "pfilt", "pcond"),
    # `xform` added alongside `rad_engine.ies.convert_ies` (Phase 11a Task 8, Amendment 3): its
    # compile check writes a scratch scene of one `!xform <name>.rad` line and hands it to
    # `build_octree`, whose `oconv` spawns `xform` itself to expand that include -- so an install
    # missing `xform` fails *inside* that spawn rather than at `env.path` time, the same gap the
    # `matrix_methods`/`bsdf` comments above describe for their own binaries. `xform` joined
    # `_ESSENTIAL` under ruling R694 (Phase 13a, see that constant's own comment); it is kept listed
    # here too because `photometry` is the capability an IES conversion actually belongs to, and this
    # tuple states what each capability needs regardless of what else already guarantees the binary.
    "photometry": ("ies2rad", "xform"),
    # `gendaylit` added alongside `epw2wea` (Phase 3 fix wave): it is the Perez all-weather sky
    # generator `rad_engine.sky.run_gendaylit` spawns for `sky.type="perez"`, and until now it
    # appeared in neither this dict nor `_ESSENTIAL`, so `env.path("gendaylit")` raised
    # `RADIANCE_NOT_FOUND` on every install -- claiming a binary that is present on disk was
    # missing, while that same message advertised `weather` as an available capability and refused
    # the one sky path that consumes weather data. Registered here rather than in `_ESSENTIAL`
    # despite sitting beside `gensky`, which is essential: an install without `gendaylit` can still
    # run every CIE sky (`gensky` covers all five idealised distributions) and therefore every check
    # in docs/plan.md's Phase 3 table, so its absence disables one sky type rather than the whole
    # server -- the same test `rcontrib` is placed by above. (`xform`, listed under `photometry`
    # above and `geometry_import` below, was placed by it too until ruling R694 made `xform`
    # `_ESSENTIAL`.) Grouped under `weather` because that is the capability a Perez sky belongs
    # to: it is the distribution driven by measured DNI/DHI, which is what `epw2wea` exists to
    # supply.
    "weather": ("epw2wea", "gendaylit"),
    "photon_map": ("mkpmap",),
    # `xform` added under this capability (Task 5, not just obj2rad/robjutil's OBJ import): every
    # ``!xform`` include chain a `.rad` scene uses is resolved through this same binary, and
    # `rad_engine.scene.read_scene` calls it directly (`env.path("xform")`) to flatten one for
    # parsing -- `oconv` also spawns it via `!xform` lines, but that path goes through PATH
    # (`RadianceEnv.environ`), not `env.path()`, so it worked before this without `xform` ever being
    # registered here. `xform` became `_ESSENTIAL` under ruling R694 (Phase 13a, see that constant's
    # own comment) for an unrelated reason -- rotating a declared-north sky -- so it is kept listed
    # here too, stating what `geometry_import` itself needs independent of that later reason.
    #
    # `obj2mesh` is deliberately absent from this tuple: owner decision 4 defers the mesh-import
    # path, and a binary listed here is a binary this server claims to need today. `obj2rad` and
    # `robjutil` are the two converters Phase 10's OBJ import actually spawns
    # (`rad_engine.convert`); `obj2mesh` moves in only when a mesh-import capability is built.
    "geometry_import": ("obj2rad", "robjutil", "xform"),
    # `pextrem` and `pvalue` added alongside `rad_engine.image.read_image` (Phase 8 Task 2): the
    # same gap this comment chain documents above for `rcontrib`, `genskyvec`, `gendaylit`,
    # `genklemsamp`, `cnt`/`rcalc`, `rcollate`/`rlam` and `genBSDF`'s three siblings -- both
    # binaries are present on this machine (measured: `/usr/local/radiance/bin/pextrem` and
    # `.../pvalue`, Radiance 6.1a) and neither appeared in this dict or `_ESSENTIAL` until
    # `read_image` became the first code path to spawn them, so `env.path("pextrem")` raised
    # `RadianceNotFound` against a fully-installed Radiance the moment it was first called.
    # `getinfo` needs no entry here -- it is already in `_ESSENTIAL`.
    #
    # `ra_bmp`/`ra_tiff`/`ra_ppm` added alongside `rad_engine.postprocess.LDR_CONVERTERS` (Phase 9
    # Task 7): they are the LDR export converters `image(action="convert")` needs (`docs/
    # tool-surface.md` #15), the same tool `pextrem`/`pvalue` already serve through
    # `action="inspect"` -- grouped here rather than under `falsecolor` because they belong to the
    # `image` tool's own binaries, not the tone-mapping family. All three are measured present on
    # this machine (Radiance 6.1a), so this group is still `True` after this change.
    "image": ("pextrem", "pvalue", "ra_bmp", "ra_tiff", "ra_ppm"),
    # `ra_png` is deliberately **not** in the group above. Ruling for this task: a capability group
    # whose members are not all present reports the whole group `False`, and `ra_png` is measured
    # **absent** on this machine (Radiance 6.1a) while `ra_bmp`/`ra_tiff`/`ra_ppm` are present --
    # folding it into `image` would report the entire image tool unavailable over one missing PNG
    # converter, which is false: `action="inspect"` and every other LDR format still work. A
    # capability reporting `False` is only the correct answer when the capability itself is
    # genuinely just about the thing that's missing, so PNG gets its own one-binary group instead.
    # `LDR_FORMAT_SUBSTITUTED` (already allocated, `docs/tool-surface.md`'s advisory register) is
    # what a caller sees when `png` is requested and this group is `False`.
    "png_export": ("ra_png",),
}


class RadianceNotFound(RuntimeError):
    """Radiance is absent or too incomplete to run anything."""


@dataclass(frozen=True, eq=False)
class RadianceEnv:
    """A located Radiance installation and the environment needed to invoke it."""

    bin_dir: Path
    lib_dir: Path | None
    version: str | None = None
    _found: Mapping[str, Path] = field(default_factory=dict, repr=False)
    """Binary name -> resolved path. Stores the resolved path rather than the bare name so
    Windows' ``.exe`` suffix is carried through; ``bin_dir / "oconv"`` does not exist there."""

    def has(self, binary: str) -> bool:
        return binary in self._found

    def path(self, binary: str) -> Path:
        """Absolute path to a binary. Raises if absent, rather than deferring to a shell error."""
        try:
            return self._found[binary]
        except KeyError:
            raise RadianceNotFound(
                f"{binary!r} not found in {self.bin_dir}. "
                f"Available capabilities: {sorted(k for k, v in self.capabilities().items() if v)}"
            ) from None

    def capabilities(self) -> dict[str, bool]:
        """Which workflows this install can actually support."""
        return {
            name: all(b in self._found for b in binaries)
            for name, binaries in _CAPABILITY_BINARIES.items()
        }

    def environ(self, work_dir: Path | str | None = None) -> dict[str, str]:
        """Environment for a Radiance subprocess.

        RAYPATH is set explicitly and always leads with ``.`` -- never inherited, because an
        inherited value missing ``.`` produces an empty octree with a zero exit status.
        ``.`` resolves against the subprocess cwd, which is why callers must pass ``cwd``
        explicitly rather than relying on the server's own working directory.

        Every key starting ``RAD_MCP_`` is dropped (ruling R829): it is this server's own
        configuration (`rad_mcp.paths`, the only reader), and no child reads one -- the children are
        Radiance binaries and ``python -m rad_engine.<module>`` helpers, and nothing under
        `rad_engine` or `rad_core` imports `rad_mcp`. The rest of the environment is inherited by
        decision, so a site-specific variable an install relies on still reaches it.
        """
        env = {key: value for key, value in os.environ.items() if not key.startswith("RAD_MCP_")}
        raypath = ["."]
        if work_dir is not None:
            raypath.append(str(Path(work_dir).resolve()))
        if self.lib_dir is not None:
            raypath.append(str(self.lib_dir))
        env["RAYPATH"] = os.pathsep.join(raypath)
        env["PATH"] = os.pathsep.join([str(self.bin_dir), env.get("PATH", "")])
        return env

    def raypath_dirs(self, work_dir: Path | str) -> tuple[Path, ...]:
        """The directories `environ(work_dir)` puts on RAYPATH after its leading `.`: the resolved
        work directory, then the library directory when there is one.

        Read back from `environ` rather than rebuilt, so the `!`-line guard (ruling R753) searches
        the directories the spawn will search and the two cannot drift. `.` is left out: it is the
        spawn's own working directory, which is the work directory for a top-level file and the
        directory of whichever file a nested `!xform` was reached from, and the guard adds both itself.
        """
        return raypath_dirs_of(self.environ(work_dir), cwd=work_dir)


def raypath_dirs_of(environ: Mapping[str, str], *, cwd: Path | str) -> tuple[Path, ...]:
    """The directories a spawn with `environ`, run in `cwd`, searches on RAYPATH, other than its own
    working directory: every non-empty entry except `.`, in order. An environment with no RAYPATH
    gives `()`.

    The one parse of a RAYPATH value, shared by `RadianceEnv.raypath_dirs` and the job worker's
    pre-spawn scan (ruling R755), which reads the environment its launcher resolved, so the two
    cannot drift. `.` is left out for `raypath_dirs`' reason: the `!`-line guard adds the spawn's
    working directory, and the directory of whichever file a nested `!xform` came from, itself.

    A relative entry (`lib`, `./`) is joined to `cwd` (ruling R755, final review): the spawn
    resolves it against its own working directory, and `Path(entry)` alone was resolved against
    this server's, so the scan searched a directory the spawn never reads and missed the one it does.
    """
    base = Path(cwd)
    entries = environ.get("RAYPATH", "").split(os.pathsep)
    return tuple(base / entry for entry in entries if entry and entry != ".")


def _probe_version(env: RadianceEnv) -> str | None:
    """Read the Radiance version string, e.g. ``RADIANCE 6.1a 2026-05-05 LBNL (6.1.39b9966033)``.

    Not every binary implements ``-version`` -- ``oconv`` rejects it as an unknown option -- so
    probe the ray-tracers, which do.

    **The probe runs under ``env.environ()``, never the inherited environment.** A ray-tracer reads
    ``rayinit.cal`` before it prints anything, including its own version, so with RAYPATH unset it
    exits 2 and reports nothing -- on a perfectly good install. Verified on this machine against
    Radiance 6.1a::

        $ env -u RAYPATH /usr/local/radiance/bin/rpict -version
        rpict: system - cannot find function file "rayinit.cal": No such file or directory
        exit 2
        $ env RAYPATH=.:/usr/local/radiance/lib /usr/local/radiance/bin/rpict -version
        RADIANCE 6.1a 2026-05-05 LBNL (6.1.39b9966033)

    That made the failure the *normal* case rather than an exotic one: an MCP server launched by a
    desktop client inherits no RAYPATH, so ``radiance_status`` reported a found install with no
    version, and every project created against it recorded ``radiance_version: "unknown"`` -- which
    is the string ``RADIANCE_VERSION_CHANGED`` compares against, so the check could never fire.
    This module owns the RAYPATH rule (see the module docstring); obeying it in its own probe is
    that rule, not an exception to it.
    """
    for name in ("rpict", "rtrace"):
        exe = env._found.get(name)
        if exe is None:
            continue
        try:
            proc = subprocess.run(
                [str(exe), "-version"],
                env=env.environ(),
                capture_output=True,
                text=True,
                check=False,  # the fallback below reads proc.stderr on a nonzero exit too
                # CONTEXT.md: on stdio, stdin is the JSON-RPC request stream. Every spawn declares
                # it, including this one -- a binary that inherits it eats the client's requests.
                stdin=subprocess.DEVNULL,
                timeout=10,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        line = (proc.stdout or proc.stderr).strip().splitlines()
        if line and line[0].startswith("RADIANCE"):
            return line[0]
    return None


def locate(bin_dir: Path | str | None = None) -> RadianceEnv:
    """Find a Radiance installation **without running anything** (ruling R753).

    The same search as `discover` -- explicit ``bin_dir``, then ``$RADIANCE_BIN``, then ``oconv`` on
    PATH, then the common install prefixes -- checking only that files exist. ``version`` is
    ``None``: reading it needs the probe's spawn. `rad_mcp.point_in_time_tool`'s scan above
    `dry_run` uses this to search the RAYPATH the real call's spawn will get, with nothing spawned.
    """
    if bin_dir is not None:
        # An explicit bin_dir is an assertion, not a hint. Falling back to some other install
        # would silently mask a misconfiguration and run a different Radiance than asked for.
        candidates = [Path(bin_dir)]
    else:
        candidates = []
        if env_bin := os.environ.get("RADIANCE_BIN"):
            candidates.append(Path(env_bin))
        if on_path := shutil.which("oconv"):
            candidates.append(Path(on_path).parent)
        candidates.extend(Path(p) / "bin" for p in _COMMON_PREFIXES)

    for candidate in candidates:
        if not (candidate / "oconv").exists() and not (candidate / "oconv.exe").exists():
            continue
        found: dict[str, Path] = {}
        for name in (*_ESSENTIAL, *(b for bs in _CAPABILITY_BINARIES.values() for b in bs)):
            for exe in (candidate / name, candidate / f"{name}.exe"):
                if exe.exists():
                    found[name] = exe.resolve()
                    break
        missing = [b for b in _ESSENTIAL if b not in found]
        if missing:
            continue

        lib_dir = None
        for rel in ("../lib", "../share/radiance/lib", "../lib/radiance"):
            probe = (candidate / rel).resolve()
            if (probe / "rayinit.cal").exists():
                lib_dir = probe
                break

        return RadianceEnv(bin_dir=candidate.resolve(), lib_dir=lib_dir, _found=dict(found))

    if bin_dir is not None:
        raise RadianceNotFound(
            f"No usable Radiance installation at {Path(bin_dir)}. Required binaries: "
            + ", ".join(_ESSENTIAL)
        )
    raise RadianceNotFound(
        "No Radiance installation found. Looked on PATH, in $RADIANCE_BIN, and in: "
        + ", ".join(_COMMON_PREFIXES)
    )


def discover(bin_dir: Path | str | None = None) -> RadianceEnv:
    """Locate a Radiance installation and read its version.

    Search order: explicit ``bin_dir``, then ``$RADIANCE_BIN``, then ``oconv`` on PATH, then the
    common install prefixes (`locate`, R753).
    """
    located = locate(bin_dir)
    # Two steps, because the probe needs the environment this object builds: the version is a
    # property read *through* a correct RAYPATH, not a property of the directory. `replace`
    # rather than a mutable field, so the frozen invariant holds and nothing can be observed
    # in the half-built state.
    return replace(located, version=_probe_version(located))
