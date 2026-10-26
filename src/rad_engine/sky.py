"""Building `gensky`/`gendaylit` command lines and describing the resulting sky.

**Scope** (docs/tool-surface.md §10, docs/plan.md Phase 3, ruling R34): this module owns the sky
half of `run_point_in_time` -- the five CIE `gensky` distributions, the Perez `gendaylit` all-weather
distribution, the daylight-factor forced-overcast substitution, and ground reflectance / north
sign convention as they reach either binary. It does **not** run `rtrace` or parse irradiance
(`rad_engine.rtrace`, a sibling module) and does **not** build the wire envelope (`rad_mcp`).

**Ruling R34 (2026-08-03).** docs/tool-surface.md §10's "Layers" line originally assigned the
`gensky` command line to `rad_engine.rtrace` and named no `sky.py`, contradicting docs/plan.md's
Phase 3 file list, which names this module. The owner settled it in the file list's favour: sky
generation and ray tracing are separate concerns over separate binaries, and Phase 4's `gendaymtx`
needs this same seam (same location, ground reflectance and north convention) with no home inside a
module named for the ray tracer. `docs/tool-surface.md` has been amended accordingly.

**Argv is data, not an interpolated string.** docs/tool-surface.md §10: "`argv` is load-bearing:
the exact command line is returned to the client, so the sky is auditable." Every `build_*_argv`
function here returns a plain `list[str]` built field by field, matching `rad_engine.octree`'s and
`rad_engine.rtrace`'s own contract of returning an argv that leads with the bare binary *name*, the
first token `rad_engine.run.run` substitutes the resolved path for -- never a shell string.

**Ground reflectance is one project value, passed here as a `ProvenanceValue`
(docs/tool-surface.md#ground-reflectance-is-one-project-value-passed-to-every-sky-generator).**
`gensky -g` and `gendaylit -g` both take it verbatim from `SkySpec.ground_reflectance.value` --
this module never invents or inherits a binary default; a caller with no project value passes the
labelled default computed by `rad_core.manifest.default_ground_reflectance()`, which is never absent.

**North.** `rad_core.manifest.Project.north_convention` is `degrees_east_of_north` (surveying);
Radiance's own `gendaymtx -r` rotates *west* of north
(docs/tool-surface.md#how-provenance-invalidates-a-matrix, sourced for `gendaymtx -r`), so
`rad_engine.skymatrix` emits `-r -north` through `north_rotation_flag` below. `gensky` and
`gendaylit` are rotated a different way, by `north_rotation_xform_argv`, **not** by their own `-r`
flag -- that inference (an identical `-r -north` sign flip applied to `gensky`/`gendaylit` by
analogy with `gendaymtx`) was flagged in this docstring for empirical confirmation before Phase 4
leaned on it, and confirming it is what found it wrong. **Ruling R694 (measured 2026-09-24, this
machine's Radiance 6.1a):** `gensky -r` is not a rotation at all -- it is the solar radiance
constant (`gensky -defaults` lists it as such, and passing a `north` value there changed the sun's
brightness, not its position) -- and `gendaylit -r` is "Use error - unknown option" on this build's
`gendaylit`; it does not exist. On a project that declares a north, `gensky` silently dimmed or
dropped the sun, and `gendaylit` refused the call: it exited 1, `run` raised `RadianceError`, and
the caller got an uncoded refusal quoting its stderr (docs/plan.md Still open item 11). The fix
does not touch either generator's argv: the generated sky text is piped through a spawned `xform -rz <-north>` instead, which rotates
both the sun direction and the `skyfunc` brightfunc's own orientation, so sun and sky glow turn
together. See `north_rotation_xform_argv` for the sign convention and what was measured.

**The `gendaylit` registration gap, closed.** `gendaylit` was for a time registered in neither
`_ESSENTIAL` nor any `_CAPABILITY_BINARIES` tuple in `src/rad_engine/env.py`, so
`env.path("gendaylit")` raised `RadianceNotFound` unconditionally -- reporting a binary present on
disk as missing, which made this entire Perez path dead on every install while `dry_run=True` on the
identical call still returned success. It is now registered under the `weather` capability (see that
entry's own comment for why there rather than `_ESSENTIAL`), so `run_gendaylit` reaches the real
binary. An install genuinely lacking it still raises `RadianceNotFound`, which is now a true
statement about that install rather than a false one about every install.

**Two facts verified against a live Radiance 6.1a on 2026-08-03, both the kind that produce a
plausible wrong number if missed.**

1. **`gensky` takes month/day/hour positionally, *before* its options; the DF example prose
   (`gensky -c -B 55.866`) fails outright** (`gensky: Use error - bad month`). `build_gensky_argv`
   already placed the date first from the start (`docs/specs/radiance-engine-overview.md`'s own
   sourced examples all do, e.g. line 722: ``gensky 12 21 12:00 -c -B 55.866``), so this needed no
   code change -- only confirming the ordering already matched what a real `gensky` demands, not
   just what the prose showed. The `-ang altitude azimuth` alternative form (bypassing date/location
   entirely) is not built here: nothing in this phase's `sky` dict contract
   (docs/tool-surface.md §10) offers altitude/azimuth as an input, only month/day/hour and a site
   (`latitude`, `longitude_east`, `meridian_east`, R762), so there is no caller shape that would
   ever reach it.

   **`forced_overcast_sky_spec` still needs a month/day/hour even with no caller-given date**, since
   `gensky`'s positional contract requires them regardless of which distribution flag follows. The
   default is **December 21, 12:00** -- the exact date the corpus's own sourced DF examples use,
   verbatim, in two places (`docs/specs/radiance-engine-overview.md` lines 722 and 1042: ``gensky 12
   21 12:00 -c -B 55.866 > df_sky.rad``). This is deterministic and explicit rather than "today"
   (which would make the forced sky's `argv` -- returned to the client for audit -- change from run
   to run for a query whose CIE overcast *shape* does not depend on solar position at all under
   `-c`), and it is not an invented convention: it is the one date the sourced material actually
   uses for this exact command.

2. **`gensky`'s own stdout is not a usable sky, and the failure is silent to `oconv`.** The emitted
   `void brightfunc skyfunc ...` is a modifier, not a light source: `oconv` compiles it with exit 0
   and empty stderr either way, and `rtrace -I` against the result returns exactly `0 0 0` with the
   only signal being `rtrace: warning - no light sources found` on stderr (already classified by
   `rad_engine.advisories.classify_line` as `JOB_STDERR_WARNINGS` at `warn` -- see that module's own
   comment on the `rtrace:`/`rcontrib:` asymmetry; not touched here, outside this module's
   ownership). The fix is the standard `glow`/`source` pair every sourced `gensky` workflow appends
   after its own output (`docs/specs/radiance-practical-guide.md` line 190: "gensky defines the
   sky's luminance, not its geometry. You must still define a glow material and apply it to a source
   primitive to create the physical sky dome."):

       skyfunc glow skyglow
       0
       0
       4 1 1 1 0

       skyglow source sky
       0
       0
       4 0 0 1 180

   `sky_description_text` below appends exactly this after `gensky`'s stdout, so nothing calling
   this module ever gets the bare, silently-zero form. Verified end to end on this machine: a ground
   plane plus the complete text plus a `1 1 1 0 0 1` sensor reads 9989.6 lux against check 3.4's
   derived 10 000 lux reference -- inside +/-2% with an order of magnitude of margin -- while the
   *bare* `gensky` stdout (no glow/source) reads exactly `0 0 0` with the same scene otherwise
   unchanged.

   **Ruling R36 (2026-08-03) added the matching *ground* glow.** The pair above covers only the
   upper hemisphere (`0 0 1`); with it alone, every downward direction radiates **exactly zero**
   (measured: an open-air probe reading straight down returns `0 0 0` with the sky glow alone,
   `5.986` once R36's `0.2` ground glow is appended). `sky_description_text` now emits both; it
   coloured the ground glow by the project's ground reflectance until ruling R761 made it white -- see
   `_GROUND_GLOW_SOURCE_SUFFIX` and the comment above it for the measurements that settled the
   colour.

   *An earlier revision of this docstring claimed a second, independent trap: that scene file order
   on the `oconv` command line mattered when `-f` (freeze) was not used, with `ground.rad` first
   silently zeroing. **That claim was wrong and is retracted.** Re-measured deliberately, all six
   combinations of {ground first, sky first, `-f`} x {complete sky, bare sky}: the complete sky
   reads 9988.49 lux in all three orderings and the bare sky reads exactly 0 in all three. The
   zeroing tracks the missing glow/source pair alone; the apparent `-f` "fix" was a coincidence of
   which sky file each trial happened to use. There is one trap here, not two -- and `oconv` file
   order is not it.*
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Final, Literal

from rad_core.manifest import SKY_GROUND_GLOW_RGB, ProvenanceValue
from rad_core.photometry import REFERENCE_IRRADIANCE_WM2
from rad_core.weather import EPWLocation

from .advisories import EngineAdvisory
from .convert import _quoted_stderr
from .env import RadianceEnv
from .run import Result, run

__all__ = [
    "CIE_SKY_TYPES",
    "DAYLIGHT_FACTOR_DEFAULT_MONTH",
    "DAYLIGHT_FACTOR_DEFAULT_DAY",
    "DAYLIGHT_FACTOR_DEFAULT_HOUR",
    "DaylightFactorSkyRefused",
    "GROUND_GLOW_RGB",
    "PEREZ_SINGLE_VALUE_REFUSED_MESSAGE",
    "SkyError",
    "SkySite",
    "SkySiteRequired",
    "SkySpec",
    "SkyResult",
    "build_gendaylit_argv",
    "build_gensky_argv",
    "df_sky_forced_advisory",
    "forced_overcast_sky_spec",
    "generate_sky",
    "north_rotation_flag",
    "north_rotation_xform_argv",
    "resolve_daylight_factor_sky",
    "resolve_perez_inputs",
    "run_gendaylit",
    "run_gensky",
    "site_from_epw_location",
    "sky_description_text",
    "sky_generator_advisories",
    "sky_generator_argv",
    "sky_integer_token",
    "sky_interval_corrected_advisory",
    "sky_irradiance_from_epw_advisory",
    "sky_location_from_epw_advisory",
    "validate_sky_dates",
    "CIE_TYPES_WITH_SUN",
    "EPW_DEFAULT_INTERVAL_MIN",
    "sky_result_from_spec",
]

#: The forced-overcast DF sky's date when the caller gives none, verbatim from the corpus's own
#: sourced DF example, in two places (docs/specs/radiance-engine-overview.md lines 722 and 1042):
#: ``gensky 12 21 12:00 -c -B 55.866 > df_sky.rad``. `gensky` demands month/day/hour positionally
#: regardless of which distribution flag follows it (verified live 2026-08-03: `gensky -c -B 55.866`
#: with no date fails with "bad month"), even though a CIE overcast sky's *luminance distribution*
#: under `-c` does not depend on solar position -- so a deterministic, explicit default beats
#: "today", which would make the forced sky's audited `argv` change from run to run for a query
#: whose answer does not.
DAYLIGHT_FACTOR_DEFAULT_MONTH = 12
DAYLIGHT_FACTOR_DEFAULT_DAY = 21
DAYLIGHT_FACTOR_DEFAULT_HOUR = 12.0

#: The standard `glow`/`source` pair `gensky`'s own stdout needs appended to become a usable light
#: source rather than a silently-inert modifier (module docstring, verified fact 2;
#: docs/specs/radiance-practical-guide.md line 190: "You must still define a glow material and apply
#: it to a source primitive to create the physical sky dome."). `skyfunc` is `gensky`'s own fixed
#: modifier name for the brightness function it just emitted, so this text is appendable verbatim
#: after any `gensky` stdout regardless of sky type, date, or flags -- nothing in it varies per call.
_SKY_GLOW_SOURCE_SUFFIX = (
    "\nskyfunc glow skyglow\n"
    "0\n"
    "0\n"
    "4 1 1 1 0\n"
    "\n"
    "skyglow source sky\n"
    "0\n"
    "0\n"
    "4 0 0 1 180\n"
)

#: The ground glow's colour (ruling R761, owner decision D1 of 2026-10-02, reversing R36's
#: `rho rho rho`). Recorded on `sky:point_in_time` as `ground_glow`, so `rad_engine.export`
#: refuses to reproduce a sky sealed with any other glow. Bound to
#: `rad_core.manifest.SKY_GROUND_GLOW_RGB`, the one definition (R777).
GROUND_GLOW_RGB: Final = SKY_GROUND_GLOW_RGB

#: The **ground** half of the glow/source pair. Ruling R36 (2026-08-03) added it: without it every
#: direction with `Dz < 0` radiates exactly zero. Ruling R761 made it white.
#:
#: **Why white.** `gensky -g` already carries the reflectance: it moves exactly one number of its
#: output, `skybright.cal`'s third `skyfunc` argument (A3, "ground plane brightness"), which
#: `skybr` returns for downward directions -- measured exactly linear, 149.7 x g. A `glow`'s colour
#: multiplies the brightfunc's value, so R36's `rho rho rho` applied the reflectance a second time.
#:
#: - **Open air** (plan M-B1, Athens 21 June noon, `-g 0.2`, no geometry): an up-facing sensor reads
#:   83 957 lux under either glow; a down-facing one 3 360.87 lux under `0.2 0.2 0.2` and 16 804
#:   under `1 1 1` -- a factor of 5.0 = 1/rho (the Phase 3 review measured the same factor). Only
#:   the white glow gives down/up = rho (0.20015), what a ground of reflectance rho returns.
#:   `tests/test_sky.py::TestTheGroundGlowAppliesRhoOnce` pins it.
#: - **In a room** (design F4: the office fixture without its ground polygon, `gensky 3 21 12 -c
#:   -B 55.866 -g 0.2`, `-ab 4 -ad 4096`, five points 1 to 13 m from the window): the `0.2` glow
#:   read 7, 17, 25, 22 and 30 % below the white one, each beyond the two-run spread. Ground light
#:   is inter-reflected light, so the error grew with depth.
#: - **`genskyvec`** (plan M-B1b): the ground patch is 29.90 under the white glow against 5.98
#:   under `0.2`; bare `gensky` gives 28.70 / 30.02 / 33.43, the same ground in the sky's colour.
#:
#: R36 chose `rho rho rho` on two sources, both still true as statements: the guide's convention
#: (`library/a-guide-to-radiance.txt:744`, "for the ground is dark Grey (`.2 .2 .2`)") and the
#: office fixture's hand-written skies (`4 0.200 0.200 0.200 0`). They are the convention this
#: server stopped following; the test_room fixture's Boulder sky already uses `4 1.000 1.000 1.000
#: 0`. R36's third reason -- that no Phase 3 quantity could tell the two apart -- held for an
#: up-facing sensor in open air and was false inside a room (F4).
#:
#: `gensky` never emits this pair itself (its stdout ends at the `brightfunc`), so appending it is
#: the caller's job, as `_SKY_GLOW_SOURCE_SUFFIX` is.
_GROUND_GLOW_SOURCE_SUFFIX: Final = (
    "\nskyfunc glow groundglow\n"
    "0\n"
    "0\n"
    f"4 {GROUND_GLOW_RGB} 0\n"
    "\n"
    "groundglow source ground\n"
    "0\n"
    "0\n"
    "4 0 0 -1 180\n"
)

#: The five idealised CIE distributions `gensky` produces, matching the wire `sky.type` values
#: docs/tool-surface.md §10 allows for anything other than `"perez"`.
CIE_SKY_TYPES = ("clear", "clear_no_sun", "overcast", "intermediate", "uniform")

#: `gensky`'s own flag for each CIE type (docs/specs/radiance-engine-overview.md §8;
#: docs/specs/en-17037-compliance.md line 240 for `+s`; docs/specs/radiance-practical-guide.md
#: line 167 for `-c`). `clear` carries the sun (`+s`, "sunny sky with sun"); `clear_no_sun` is the
#: same distribution's sky component alone (`-s`, the man page's minus-sign sense: sunny sky
#: *without* the sun as a separate light source -- gensky's own convention that `+` requests a
#: feature and `-` withholds it, which also governs `+i`/`-i` for intermediate below).
_CIE_TYPE_FLAGS: dict[str, str] = {
    "clear": "+s",
    "clear_no_sun": "-s",
    "overcast": "-c",
    "intermediate": "+i",
    "uniform": "-u",
}

#: Only `clear` (`+s`) puts a discrete sun in the scene. Every other `gensky` type -- including
#: `clear_no_sun`, which is the *same* clear-sky luminance distribution with the solar disc
#: withdrawn -- has no separate sun source. Its readers are `SkySpec.has_sun` and
#: `rad_mcp.point_in_time_tool`'s `DIRECT_EXACT_FORCED` decision in `_run_pipeline`
#: (docs/tool-surface.md §10), both of which read this rather than re-deriving it from the flag
#: string. **`resolve_daylight_factor_sky` was named here and never read it** -- it compared
#: `sky.type != "overcast"` and now, under ruling R389, tests no type at all; the name was corrected
#: to the two sites an attribute walk actually finds.
CIE_TYPES_WITH_SUN = frozenset({"clear"})

#: docs/tool-surface.md §10: a single global-horizontal irradiance/illuminance value would use
#: `gendaylit -E`, "not offered", because a modelled diffuse-fraction split of a measured total is a
#: substitution the caller cannot see. Cited in the refusal message rather than re-typed at each
#: call site.
PEREZ_SINGLE_VALUE_REFUSED_MESSAGE = (
    "a single global-horizontal irradiance or illuminance value would use gendaylit -E, which "
    "splits the diffuse fraction by the Erbs model. This is not offered: \"due to the high "
    "uncertainty of the model, the results have to be handled with care. A second irradiance "
    "value, if available, is definitely recommended\" (docs/tool-surface.md §10). Pass both "
    "irradiance=[dni, dhi] (-W) or illuminance=[dni_l, dhi_l] (-L), or name a registered weather "
    "file."
)


class SkyError(RuntimeError):
    """Something this module refuses to build.

    Mirrors `rad_engine.project.ProjectError`'s shape (docs/tool-surface.md#refusals): a refusal is
    `{"refused": true, "reason": ..., "advisories": [...]}` on the wire, never an exception there,
    but that shape belongs to `rad_mcp`. This class carries the allocated `code` (`None` where the
    register has none) so the layer that builds the envelope does not have to invent one.
    """

    code: str | None = None
    severity: Literal["info", "warn", "error"] = "error"
    field: str | None = None
    """The wire argument a refusal is about, when it is one argument (`SkySiteRequired`: `"sky"`).
    Each sky tool's `except SkyError` passes it to the advisory (R762)."""

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        self.code = code


class DaylightFactorSkyRefused(SkyError):
    """`quantity="daylight_factor"` was requested alongside a caller-supplied `sky` of any kind.

    **Widened from "a non-overcast sky" to "any supplied sky" by ruling R389.** The narrow version
    let an overcast sky of the caller's own through while both consumers went on dividing by the
    10 000 lux reference -- `rad_core.photometry.daylight_factor`'s `reference_lux` default and
    `rad_engine.postprocess`'s `falsecolor -m 1.79` -- so the published percentage was a ratio
    between two different skies. Measured on this machine's Radiance 6.1a, `gensky 6 21 12.0 -c
    -g 0.2` is 1.82x the reference `gensky 12 21 12.0 -c -g 0.2 -B 55.866` on both terms of its
    `3 2` row, which is a 200 lux sensor publishing 2.0 % where the definitional answer is 1.10 %.

    No advisory code, before and after the widening: the register's `DF_SKY_FORCED` is for the
    *substitution* case (no sky given), not this refusal, and no other code in the register's
    Simulation group names this combination. The `rad_mcp` layer that catches this attaches
    `ARGUMENT_OUT_OF_RANGE` with `field="sky"` (R14), which is the register entry that applies.
    """

    def __init__(self, supplied: str) -> None:
        """`supplied` describes what the caller passed, e.g. ``sky.type='clear'`` or
        ``sky='/p/x.rad'``. It is interpolated into the message as written, so a caller that has
        only a `SkySpec` passes ``f"sky.type={spec.type!r}"`` and one holding the wire value
        describes that instead."""
        super().__init__(
            f"quantity=\"daylight_factor\" was requested alongside a caller-supplied sky "
            f"({supplied}), and a supplied sky is refused for this quantity. A daylight factor is "
            "*defined* as the ratio of the interior illuminance to the unobstructed exterior "
            "horizontal illuminance under the CIE standard overcast sky, which this server takes "
            "as the conventional 10 000 lux -- and it divides by that 10 000 lux whichever sky was "
            "actually rendered (rad_core.photometry.daylight_factor's reference_lux default; "
            "rad_engine.postprocess's falsecolor -m 1.79). Your own sky is therefore not measured "
            "against itself but against a reference it does not match, so the percentage would not "
            "be a daylight factor. Measured on this machine's Radiance, gensky 6 21 12.0 -c -g 0.2 "
            "is 1.82x the reference gensky 12 21 12.0 -c -g 0.2 -B 55.866 on both terms of its "
            "3 2 row: a 200 lux sensor publishes 2.0 % where the definitional answer is 1.10 %. "
            "Omit sky entirely and run_point_in_time substitutes the reference sky itself, "
            "reporting DF_SKY_FORCED; a non-overcast sky was never valid here in any case, since a "
            "daylight factor \"is only valid for the overcast sky condition and does not account "
            "for direct sunlight\" (workflows §2.4).",
            code=None,
        )


class SkySiteRequired(SkyError):
    """A CIE or Perez sky with no site, in a project with no registered weather file (ruling R762,
    owner decision D2 of 2026-10-02).

    `gensky`/`gendaylit` place the sun from `-a`/`-o`/`-m`; given none they use their own defaults
    (`gensky -defaults`: `-a 37.815214 -o 122.040010 -m 120`, California) and say nothing, so a
    sky with no site was a sky somewhere the caller never named (design F5).

    Also raised by `rad_mcp.point_in_time_tool._resolve_sky` as a backstop when a CIE or Perez sky
    reaches it with no site; every sky tool resolves the site first
    (`rad_mcp.sky_site.resolve_sky_inputs`, R762), so no tool call reaches that raise (R778).
    """

    code = "SKY_SITE_REQUIRED"
    field = "sky"

    def __init__(self, message: str) -> None:
        # `code=self.code`, not the literal: a `code="..."` keyword with no `severity=` beside it
        # reads to tests/test_advisories.py's scan as an emission with an unreadable severity. The
        # class body above is the allocation the scan reads, with `SkyError`'s severity.
        super().__init__(message, code=self.code)


@dataclass(frozen=True)
class SkySite:
    """Where a sky is, in degrees, **east-positive** (ruling R762, owner decision D3).

    East-positive like the `weather` tool's `site.longitude_east` and `run_view_and_sunlight`'s
    `location`. `gensky -o`/`-m` and `gendaylit -o`/`-m` are west-positive, so the argv builders
    negate both; this type never holds a west-positive value. Athens: `latitude 37.97`,
    `longitude_east 23.72`, `meridian_east 30` (UTC+2) reaches `gensky` as
    `-a 37.97 -o -23.72 -m -30.0` (plan M-B2: solar altitude 74.4, azimuth -23.2 at 21 June noon).
    """

    latitude: float
    longitude_east: float
    meridian_east: float


def site_from_epw_location(location: EPWLocation) -> SkySite:
    """The site an EPW's LOCATION line records (R762): its latitude, its own east-positive
    longitude, and its time zone as an east-positive standard meridian (`time_zone_hours x 15`,
    the negation of `EPWLocation.time_zone_meridian_deg`, which is the `.wea`'s west-positive
    form). Athens, UTC+2: meridian_east 30."""
    return SkySite(
        latitude=float(location.latitude),
        longitude_east=float(location.longitude_east_deg),
        meridian_east=-float(location.time_zone_meridian_deg),
    )


@dataclass(frozen=True)
class SkySpec:
    """One resolved sky request -- everything `build_gensky_argv`/`build_gendaylit_argv` need.

    This is the *engine's* normalised sky, one layer below the wire `sky` dict
    (docs/tool-surface.md §10) that `rad_mcp` accepts from a caller: north and ground reflectance
    have already been read from the project, and the Perez route (weather/measured/refused) has
    already been chosen by `resolve_perez_inputs` before a `SkySpec` naming `type="perez"` is built.
    """

    type: Literal["clear", "clear_no_sun", "overcast", "intermediate", "uniform", "perez"]
    month: int
    day: int
    hour: float
    ground_reflectance: ProvenanceValue
    site: SkySite | None = None
    """Where the sky is, east-positive (R762). `None` only on the forced daylight-factor sky, whose
    `-c -B 55.866` distribution is site-independent (plan measurement M-B2b); the builders refuse
    `None` on any other spec."""
    north: float | None = None
    """Degrees east of north (`rad_core.manifest.Project.north_convention`), or `None` if the
    project declares no north scalar. Applied by `north_rotation_xform_argv` (ruling R694), which
    pipes the generated sky through a spawned `xform -rz` -- never by `gensky`/`gendaylit`'s own
    `-r`, which is not a rotation on either binary (see `north_rotation_xform_argv`'s docstring)."""
    forced_irradiance_wm2: float | None = None
    """`gensky -B`, set only by `forced_overcast_sky_spec` for the daylight-factor path."""
    perez_irradiance: tuple[float, float] | None = None
    """`(dni, dhi)` W/m^2 -- `gendaylit -W`. Mutually exclusive with `perez_illuminance`."""
    perez_illuminance: tuple[float, float] | None = None
    """`(dni_l, dhi_l)` lux -- `gendaylit -L`. Mutually exclusive with `perez_irradiance`."""
    interval_min: int | None = None
    """`gendaylit -i`. `rad_mcp.point_in_time_tool._resolve_sky` defaults this to
    `EPW_DEFAULT_INTERVAL_MIN` (60) on the EPW route, R765 (docs/tool-surface.md §10: "EPW rows are
    hourly means")."""
    resolved_from: Literal["epw", "user", "forced_overcast"] = "user"
    """The wire result's `sky.resolved_from` (docs/tool-surface.md §10) -- carried on the spec
    itself so a caller assembling the result does not have to re-derive which branch produced it."""


def north_rotation_flag(north: float | None) -> list[str]:
    """`["-r", str(-north)]`, or `[]` if no north is declared.

    **Serves `gendaymtx` only, via `rad_engine.skymatrix`.** docs/tool-surface.md: north is stated
    `degrees_east_of_north` (the surveying convention) while `gendaymtx -r` rotates *west* of north,
    so `rad_engine.skymatrix` emits `-r -north` through this function. `gensky` and `gendaylit` do
    **not** take this flag -- ruling R694 found `gensky -r` is the solar radiance constant and
    `gendaylit -r` an unknown option on this Radiance 6.1a, so a `gensky`/`gendaylit` sky is rotated
    instead by `north_rotation_xform_argv`, below. This function stays here (rather than moving to
    `rad_engine.skymatrix`) only because `rad_engine.skymatrix` re-exports it by identity --
    `tests/test_skymatrix.py` pins that it is reused, not reimplemented.
    """
    if north is None:
        return []
    return ["-r", repr(float(-north))]


def north_rotation_xform_argv(north: float | None) -> list[str]:
    """`["xform", "-rz", repr(float(-north))]`, or `[]` when no north is declared or north is 0.

    **Ruling R694.** `gensky` and `gendaylit` do not rotate: on Radiance 6.1a `gensky -r` is the
    solar radiance and `gendaylit -r` is an unknown option (docs/plan.md Still open item 11). The
    generated sky is rotated instead by piping it through `xform -rz`, which turns the `source`
    direction and appends the same `-rz` to the `skyfunc` brightfunc, so sun and sky glow turn
    together (measured: the rotated sky along R.d equals the plain sky along d to seven digits).
    `xform -rz` turns counter-clockwise seen from above, i.e. west of north, and `north` is east of
    north, so the angle is `-north`: a sun at true azimuth A lands at model azimuth
    `(A + north) mod 360`, the sense ruling R682 measured against `gendaymtx -r -north`.
    North 0 (including -0.0) is the identity and emits nothing, so a project declaring 0 gets the
    same sky text as one declaring no north.

    A non-finite north (NaN or infinity, which a hand-edited manifest can carry) raises
    `ValueError` rather than building `xform -rz nan`. The three sky tools refuse such a project
    before they get here; this is the engine's own backstop.
    """
    if north is None:
        return []
    value = float(north)
    if not math.isfinite(value):
        raise ValueError(f"north={north!r} is not a finite number of degrees; no rotation argv built")
    if value == 0.0:
        return []
    return ["xform", "-rz", repr(-value)]


def sky_generator_argv(spec: SkySpec) -> list[str]:
    """The generator argv for `spec`: `gendaylit` for a Perez sky, `gensky` for every CIE type."""
    return build_gendaylit_argv(spec) if spec.type == "perez" else build_gensky_argv(spec)


def sky_integer_token(value: object, *, field: str, low: int, high: int) -> str:
    """One `gensky`/`gendaylit` integer argument, coerced and range-checked before it reaches argv.

    Every *other* field these two builders emit goes through `repr(float(...))`; `month`, `day` and
    `interval_min` alone went through a bare `str()`. `SkySpec.month`/`day` are typed `int` but
    nothing enforces that at runtime, and the wire schema is `sky: dict[str, Any]`, so a string
    reached argv verbatim -- measured, with `month="-i"` and `day="-B 9999"`::

        ['gensky', '-i', '-B 9999', '12.0', '+s', '-g', '0.2']

    That is **argument** injection, not command injection: `rad_engine.run.run` spawns a list argv
    with no shell, so no metacharacter can start a second command, and the tokens can only ever be
    read by `gensky`'s own parser. It also **failed closed already** -- `gensky` rejects `-i` where
    it wants a month with "bad month", surfacing as a clean `RadianceError`. This is therefore a
    hardening fix, not a live hole: it stops the argv's correctness from depending on a downstream
    binary's positional parser, and puts these three fields under the same numeric discipline the
    rest of the argv has always had.

    Raises `SkyError` (`code=None`, matching every other refusal this module raises for an argument
    it will not build a command from) rather than letting a `ValueError`/`TypeError` escape
    uncoded -- the same check 0.4 property `_coerce_perez_pair` restores next door.

    `int(value)` rather than `int(float(value))`: a fractional month is a caller error worth naming,
    not something to silently truncate. `bool` is excluded explicitly -- it is an `int` subclass, so
    `month=True` would otherwise pass as month 1, which no caller can have meant.

    **Public since R765** (it was `_date_token`): `rad_mcp.sky_site` range-checks a Perez sky's
    `month`/`day` with it before reading the weather file's row, and R736 keeps `rad_mcp` off
    private engine names.
    """
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise SkyError(
            f"sky.{field} must be a whole number between {low} and {high}, but got {value!r} "
            f"({type(value).__name__}).",
            code=None,
        )
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise SkyError(
            f"sky.{field} must be a whole number between {low} and {high}, but got {value!r} "
            f"({type(value).__name__}).",
            code=None,
        ) from exc
    if not low <= number <= high:
        raise SkyError(
            f"sky.{field} must be between {low} and {high}, but got {number}.",
            code=None,
        )
    return str(number)


def validate_sky_dates(spec: SkySpec) -> None:
    """Refuse an out-of-range `month`/`day`/`interval_min` *before* anything spawns.

    The same coercion and range check `build_gensky_argv`/`build_gendaylit_argv` already apply,
    lifted so a caller can run it during validation. Those builders keep their own calls -- this is
    not a replacement for them, and a spec that reaches argv construction is still checked there.

    It exists because the argv builders run inside the pipeline, *below* `run_point_in_time`'s
    `dry_run` branch, so a typo'd month refused on the real call and returned success under
    `dry_run` -- the divergence check 3.9 forbids, in the one class the `_validate_scene` hoist did
    not reach. Validation a caller can only trigger by paying for a trace is not validation.
    """
    sky_integer_token(spec.month, field="month", low=1, high=12)
    sky_integer_token(spec.day, field="day", low=1, high=31)
    if spec.interval_min is not None:
        sky_integer_token(spec.interval_min, field="interval_min", low=1, high=1440)


def _site_argv(spec: SkySpec, binary: str) -> list[str]:
    """`-a lat -o lon -m meridian` for `spec.site`, with longitude and meridian **negated**:
    `gensky`/`gendaylit` take them west-positive, `SkySite` holds them east-positive (R762, owner
    decision D3). Always emitted, so neither binary ever falls back to its own built-in site,
    except on the forced daylight-factor sky (`resolved_from == "forced_overcast"`), which is
    site-independent. Any other spec with no site raises `ValueError`: the tools refuse first
    (`SkySiteRequired`), so this is the engine's backstop."""
    if spec.site is None:
        if spec.resolved_from == "forced_overcast":
            return []
        raise ValueError(
            f"{binary} needs a site: this {spec.type!r} sky has none, and {binary} would use its "
            "own built-in site in California (R762). rad_mcp.sky_site.resolve_sky_inputs resolves "
            "one, or refuses with SKY_SITE_REQUIRED."
        )
    site = spec.site
    # R772 (owner decision E7): `+ 0.0` turns IEEE `-0.0` into `0.0`, so a UTC site records
    # `-m 0.0`, not `-m -0.0`. The binaries read both as zero.
    return [
        "-a", repr(float(site.latitude) + 0.0),
        "-o", repr(-float(site.longitude_east) + 0.0),
        "-m", repr(-float(site.meridian_east) + 0.0),
    ]


def build_gensky_argv(spec: SkySpec) -> list[str]:
    """The `gensky` argv for one of the five CIE sky types (`spec.type != "perez"`).

    Flag order follows the sourced examples (docs/specs/radiance-engine-overview.md §8,
    docs/specs/radiance-practical-guide.md line 189): ``gensky month day hour TYPE_FLAG -a lat -o
    -lon_east -m -meridian_east -g ground [-B irradiance]`` (R762: always, negated; never the
    binary's own site -- see `_site_argv`). Positional month/day/hour come first --
    `gensky`'s own contract, not a convention this module chose
    (docs/specs/radiance-engine-overview.md:1263: ``gensky month day time [options] > sky.rad``).
    **No `-r` (ruling R694): on this Radiance 6.1a `gensky -r` sets the solar radiance, not a
    rotation, so `spec.north` never reaches this argv -- see `north_rotation_xform_argv`.**

    Returns a plain argv **leading with the bare binary name**, matching `build_octree`'s and
    `build_rtrace_argv`'s own contract -- `rad_engine.run.run` substitutes `env.path("gensky")` for
    that first token.

    `-B` (forced irradiance) is mutually exclusive with the type flag being a request for a specific
    illuminance rather than a modelled one in real `gensky` usage, but the binary itself accepts
    both on the same line -- `-c -B 55.866` is exactly the sourced check-3.4 command
    (docs/plan.md check 3.4; docs/specs/radiance-practical-guide.md line 189: ``gensky -c -B
    55.866 > sky.rad``) -- so this function passes `-B` whenever `spec.forced_irradiance_wm2` is
    set, in addition to the type flag, rather than treating them as alternatives.
    """
    if spec.type == "perez":
        raise ValueError("build_gensky_argv does not build a Perez sky -- use build_gendaylit_argv")
    argv: list[str] = [
        "gensky",
        sky_integer_token(spec.month, field="month", low=1, high=12),
        sky_integer_token(spec.day, field="day", low=1, high=31),
        repr(float(spec.hour)),
        _CIE_TYPE_FLAGS[spec.type],
    ]
    argv.extend(_site_argv(spec, "gensky"))
    argv.extend(["-g", repr(float(spec.ground_reflectance.value))])
    if spec.forced_irradiance_wm2 is not None:
        argv.extend(["-B", repr(float(spec.forced_irradiance_wm2))])
    return argv


def build_gendaylit_argv(spec: SkySpec) -> list[str]:
    """The `gendaylit` argv for the Perez all-weather distribution (`spec.type == "perez"`).

    Flag order: ``gendaylit month day hour -a lat -o -lon_east -m -meridian_east -g ground
    (-W dni dhi | -L dni_l dhi_l) [-i interval]`` (R762: always, negated; never the binary's own
    site -- see `_site_argv`)
    (docs/specs/radiance-engine-overview.md line 736: ``gendaylit 6 21 14:30 -W 800 200 -g 0.2``).
    **No `-r` (ruling R694): `gendaylit -r` is "Use error - unknown option" on this Radiance 6.1a,
    so `spec.north` never reaches this argv -- see `north_rotation_xform_argv`.**

    Exactly one of `spec.perez_irradiance`/`spec.perez_illuminance` must be set -- `resolve_perez_
    inputs` is what enforces the three-way route preference and the single-value refusal
    (docs/tool-surface.md §10) before a `SkySpec` reaches this function; this function only asserts
    the invariant rather than re-deciding it, so a caller that bypasses `resolve_perez_inputs` gets a
    loud `ValueError` instead of a silently wrong command line.
    """
    if spec.type != "perez":
        raise ValueError(f"build_gendaylit_argv requires spec.type == 'perez', got {spec.type!r}")
    has_irr = spec.perez_irradiance is not None
    has_ill = spec.perez_illuminance is not None
    if has_irr == has_ill:
        raise ValueError(
            "exactly one of perez_irradiance (-W) or perez_illuminance (-L) must be set on a "
            f"perez SkySpec; got irradiance={spec.perez_irradiance!r}, "
            f"illuminance={spec.perez_illuminance!r}"
        )
    argv: list[str] = [
        "gendaylit",
        sky_integer_token(spec.month, field="month", low=1, high=12),
        sky_integer_token(spec.day, field="day", low=1, high=31),
        repr(float(spec.hour)),
    ]
    argv.extend(_site_argv(spec, "gendaylit"))
    argv.extend(["-g", repr(float(spec.ground_reflectance.value))])
    if has_irr:
        dni, dhi = spec.perez_irradiance  # type: ignore[misc]
        argv.extend(["-W", repr(float(dni)), repr(float(dhi))])
    else:
        dni_l, dhi_l = spec.perez_illuminance  # type: ignore[misc]
        argv.extend(["-L", repr(float(dni_l)), repr(float(dhi_l))])
    if spec.interval_min is not None:
        # Same untyped-token shape as month/day, and coerced the same way. The bounds are physical
        # rather than sourced: docs/tool-surface.md §10 fixes no ceiling for `-i`, but a
        # *measurement interval* below one minute or beyond a single day is not a value any weather
        # row can mean, and the EPW route's own default (`EPW_DEFAULT_INTERVAL_MIN`, 60) sits well
        # inside them.
        argv.extend(
            ["-i", sky_integer_token(spec.interval_min, field="interval_min", low=1, high=1440)]
        )
    return argv


@dataclass(frozen=True)
class SkyResult:
    """The material for the wire result's `sky` block (docs/tool-surface.md §10):

        sky: {type, resolved_from, month, day, hour, dni_wm2, dhi_wm2, ground_reflectance, argv}

    Deliberately the same field set, so `rad_mcp` copies rather than re-derives. `dni_wm2`/`dhi_wm2`
    are `None` for every non-perez sky and for a perez sky built from `-L` (illuminance, not
    irradiance) -- the wire comment reads "perez: what -W was given", so this module does not invent
    an irradiance figure for a sky it was never given one for.
    """

    type: str
    resolved_from: Literal["epw", "user", "forced_overcast"]
    month: int
    day: int
    hour: float
    ground_reflectance: ProvenanceValue
    argv: list[str]
    dni_wm2: float | None = None
    dhi_wm2: float | None = None
    rotation_argv: list[str] | None = None
    """Ruling R694: the `xform -rz` argv `generate_sky` piped the generated sky through to apply the
    project's declared north, or `None` when the project declares no north or declares 0 -- the same
    identity case `north_rotation_xform_argv` returns `[]` for. `argv` above is always the
    generator's own (`gensky`/`gendaylit`) and never carries `-r`; this field is the second, separate
    spawn that actually rotates the sky, published so a caller auditing the sky can see it happened."""

    @property
    def has_sun(self) -> bool:
        """Whether this sky places a discrete solar disc in the scene.

        Feeds `rad_engine.rtrace.direct_exact_forced`'s `sky_has_sun` argument
        (docs/tool-surface.md §10: `DIRECT_EXACT_FORCED` fires when "the sky has a sun and the
        quantity is illuminance"). A `perez` sky always has a sun -- `gendaylit` always models the
        sun's position from the date/time/location, unlike `gensky`'s `clear_no_sun`, which is the
        one CIE type that explicitly withholds it.
        """
        return self.type == "perez" or self.type in CIE_TYPES_WITH_SUN


def sky_result_from_spec(spec: SkySpec, argv: list[str]) -> SkyResult:
    """Assemble the wire-facing `SkyResult` from a resolved `SkySpec` and the argv actually built
    for it -- kept as a thin, explicit mapping rather than folded into `SkySpec` itself, since
    `SkySpec` is this module's internal working shape (it also carries `site`/`perez_irradiance`
    etc. that the wire block does not) and `SkyResult` is only the subset docs/tool-surface.md §10
    names.
    """
    dni_wm2 = spec.perez_irradiance[0] if spec.perez_irradiance is not None else None
    dhi_wm2 = spec.perez_irradiance[1] if spec.perez_irradiance is not None else None
    return SkyResult(
        type=spec.type,
        resolved_from=spec.resolved_from,
        month=spec.month,
        day=spec.day,
        hour=spec.hour,
        ground_reflectance=spec.ground_reflectance,
        argv=argv,
        dni_wm2=dni_wm2,
        dhi_wm2=dhi_wm2,
        rotation_argv=north_rotation_xform_argv(spec.north) or None,
    )


def forced_overcast_sky_spec(
    *,
    month: int | None = None, day: int | None = None, hour: float | None = None,
    ground_reflectance: ProvenanceValue,
    north: float | None = None,
) -> SkySpec:
    """The CIE overcast sky `quantity="daylight_factor"` forces: `gensky 12 21 12 -c -B 55.866`.

    docs/tool-surface.md §10: "`quantity="daylight_factor"` forces a CIE overcast sky at a known
    horizontal illuminance and normalises: `gensky -c -B 55.866` gives 10 000 lux via
    10000/179 = 55.866 W/m²". `REFERENCE_IRRADIANCE_WM2` is imported from `rad_core.photometry`
    rather than re-typed here -- that module owns the constant (it is *derived* from the DF
    arithmetic `rad_core.photometry` also owns) and this module only needs the literal for the `-B`
    flag, exactly the seam the task brief asked to be left for integration.

    `month`/`day`/`hour` are still needed even though the resulting sky is idealised and
    location-independent for `-c`: **verified live against Radiance 6.1a (2026-08-03) that `gensky`
    demands them positionally regardless of which distribution flag follows** -- `gensky -c -B
    55.866` with no date fails outright (`gensky: Use error - bad month`) -- and the wire result
    reports them (`sky.month`, `sky.day`, `sky.hour`) for audit even though an overcast sky's
    luminance distribution does not depend on solar position. **Any argument left `None` (the usual
    case: `quantity="daylight_factor"` with no `sky` at all has nothing to read a date from) defaults
    to `DAYLIGHT_FACTOR_DEFAULT_MONTH/DAY/HOUR`** -- December 21, 12:00, the exact date the corpus's
    own sourced DF example uses verbatim in two places. Explicit and deterministic rather than
    "today", which would make the forced sky's audited `argv` change from run to run for a query
    whose answer does not depend on the date at all.

    `site` is `None`: under `-c -B` the sky is site-independent -- measured (plan M-B2b),
    `gensky 12 21 12.0 -c -g 0.2 -B 55.866` with and without a site differs only in its comment
    lines -- so this is the one spec the builders build without `-a -o -m` (R762).
    """
    return SkySpec(
        type="overcast",
        month=month if month is not None else DAYLIGHT_FACTOR_DEFAULT_MONTH,
        day=day if day is not None else DAYLIGHT_FACTOR_DEFAULT_DAY,
        hour=hour if hour is not None else DAYLIGHT_FACTOR_DEFAULT_HOUR,
        ground_reflectance=ground_reflectance,
        north=north,
        forced_irradiance_wm2=REFERENCE_IRRADIANCE_WM2,
        resolved_from="forced_overcast",
    )


def df_sky_forced_advisory(spec: SkySpec) -> EngineAdvisory:
    """`DF_SKY_FORCED` (info) -- docs/tool-surface.md §10 advisories table: "An overcast 10 000 lux
    sky was substituted because DF was requested." docs/plan.md check 3.5 requires this "stating the
    substituted sky", so the message names the actual `gensky -B` literal rather than only the code.

    Built directly, never from a stderr line (`lines=()`) -- the same convention
    `rad_engine.scene._resolution_advisory` uses for a finding proven by this module's own decision,
    not by anything Radiance wrote.
    """
    return EngineAdvisory(
        code="DF_SKY_FORCED",
        severity="info",
        message=(
            "no sky was given for a daylight_factor query, so a CIE overcast sky forced to "
            f"{spec.forced_irradiance_wm2:g} W/m^2 horizontal irradiance (gensky -c -B "
            f"{spec.forced_irradiance_wm2:g}, 10 000 lux) was substituted. Daylight factor "
            "\"is only valid for the overcast sky condition\" (workflows §2.4)."
        ),
        lines=(),
    )


def resolve_daylight_factor_sky(
    sky: SkySpec | None,
    *,
    month: int | None = None, day: int | None = None, hour: float | None = None,
    ground_reflectance: ProvenanceValue,
    north: float | None = None,
) -> tuple[SkySpec, EngineAdvisory | None]:
    """The two-way `quantity="daylight_factor"` decision (docs/plan.md check 3.5,
    docs/tool-surface.md §10), **narrowed from three cases to two by ruling R389**:

    1. **No sky given (`sky is None`)** -- succeeds, substituting `forced_overcast_sky_spec`, and
       returns `DF_SKY_FORCED` at `info` naming the substituted sky. `month`/`day`/`hour` default to
       `forced_overcast_sky_spec`'s own December-21-noon default when left `None` -- the ordinary
       case for this branch, since a caller with *no* `sky` typically has no date either.
    2. **Any sky given at all** -- refused (`DaylightFactorSkyRefused`). The `sky is not None` test
       on the line below is what makes that "any": there is no type test left, so the overcast type
       is refused on the same line as every other one.

    **What case 3 was, and why the owner removed it.** Until R389 an explicitly *overcast* sky was
    accepted and passed through unchanged with no advisory, on the reasoning that a caller who had
    already chosen an overcast sky satisfied workflows §2.4's "does not account for direct sunlight"
    and that re-forcing `gensky -c -B 55.866` would silently discard their own ground reflectance,
    location and north. (North is not among what re-forcing discards: this function passes its
    `north` -- the project's, as `rad_mcp.point_in_time_tool._resolve_sky` calls it -- to
    `forced_overcast_sky_spec`, and since ruling R694 `generate_sky` turns the forced sky by it
    with `xform -rz`, a rotation that changes no value because a CIE overcast sky varies with
    altitude only.) That reasoning weighed the *distribution* and never the *magnitude*: both
    consumers go on dividing by the 10 000 lux reference whatever sky is rendered
    (`rad_core.photometry.daylight_factor`'s `reference_lux=10000.0`, which `run_point_in_time`
    never overrides, and `rad_engine.postprocess.build_falsecolor_argv`, which appends
    `falsecolor -m 1.79` for every `quantity="daylight_factor"` argv it builds -- `postprocess.py`'s
    `if quantity == "daylight_factor": argv += ["-m", _DAYLIGHT_FACTOR_MULTIPLIER]`, with no
    reference to which sky was rendered on either line). Measured
    on this machine's Radiance 6.1a, the `3 2` zenith/ground row of `gensky 6 21 12.0 -c -g 0.2` is
    `4.162e+01 6.474e+00` against the reference `gensky 12 21 12.0 -c -g 0.2 -B 55.866`'s
    `2.286e+01 3.557e+00` -- 1.8206 and 1.8200, the same 1.82 on both terms under the identical
    `-c` distribution. A 200 lux interior sensor therefore published DF = 2.0 % where the honest
    figure is 1.10 %, passing a 2 % criterion it fails, with no error and no advisory. Daylight
    factor is defined against the reference sky; accepting another one and calling the result a
    daylight factor is not a feature, so the pass-through is gone rather than papered over with a
    warning.

    Returns `(spec, advisory)`. Only case 1 returns at all -- case 2 raises -- and case 1's
    `advisory` is `df_sky_forced_advisory(forced)`, whose body is the single unconditional
    `return EngineAdvisory(...)` at `:666`, so it is never `None` in practice. The `| None` is kept
    in the signature because `rad_mcp.point_in_time_tool._resolve_sky` still guards on it and
    because a future third outcome should not have to widen the type back.
    """
    if sky is None:
        forced = forced_overcast_sky_spec(
            month=month, day=day, hour=hour,
            ground_reflectance=ground_reflectance, north=north,
        )
        return forced, df_sky_forced_advisory(forced)
    raise DaylightFactorSkyRefused(f"sky.type={sky.type!r}")


def sky_location_from_epw_advisory(site: SkySite, *, weather: str) -> EngineAdvisory:
    """`SKY_LOCATION_FROM_EPW` (info) -- docs/tool-surface.md §10: "Latitude/longitude/meridian
    taken from the registered weather file." Reached since R762 through
    `rad_mcp.sky_site.resolve_sky_inputs`, when a sky gives no site and the project's weather
    files describe one. The message states the site east-positive and names the weather input."""
    return EngineAdvisory(
        code="SKY_LOCATION_FROM_EPW",
        severity="info",
        message=(
            f"latitude {site.latitude:g}, longitude {site.longitude_east:g} E, meridian "
            f"{site.meridian_east:g} E were taken from the registered weather file {weather!r}."
        ),
        lines=(),
    )


def sky_irradiance_from_epw_advisory(dni: float, dhi: float, *, month: int, day: int, hour: float) -> EngineAdvisory:
    """`SKY_IRRADIANCE_FROM_EPW` (info) -- "`gendaylit -W` inputs were read from the registered EPW
    at that hour; states the DNI/DHI used, so the sky is auditable." See `sky_location_from_epw_
    advisory`'s note on what reaches it: since R765, `rad_mcp.sky_site.resolve_sky_inputs`, when a
    Perez sky names `weather` or names no source in a project with exactly one weather input.
    """
    return EngineAdvisory(
        code="SKY_IRRADIANCE_FROM_EPW",
        severity="info",
        message=(
            f"gendaylit -W {dni:g} {dhi:g} was read from the registered weather file for "
            f"{month:02d}/{day:02d} at {hour:g}h."
        ),
        lines=(),
    )


def sky_interval_corrected_advisory(interval_min: int) -> EngineAdvisory:
    """`SKY_INTERVAL_CORRECTED` (info) -- "`gendaylit -i` applied a low-sun-altitude correction for
    the measurement interval." docs/tool-surface.md §10: emitted "when `gendaylit` reports it
    applied a correction" -- not whenever `-i` was merely passed. `sky_generator_advisories` builds
    it when `gendaylit`'s stderr carries `_INTERVAL_CORRECTION_PREFIX` (final review FW-1); until
    then nothing called this function, and the report was discarded with the rest of the stderr.
    """
    return EngineAdvisory(
        code="SKY_INTERVAL_CORRECTED",
        severity="info",
        message=(
            f"gendaylit -i {interval_min} applied a low-sun-altitude correction for the "
            "measurement interval, since \"the specified instantaneous points of time may be "
            "incorrect\" for hourly-mean weather data (docs/tool-surface.md §10)."
        ),
        lines=(),
    )


def _coerce_perez_pair(
    pair: tuple[float, float], *, field: str, flag: str
) -> tuple[float, float]:
    """`(float(pair[0]), float(pair[1]))`, with a non-numeric element refused as a `SkyError`
    instead of escaping as a bare `ValueError`/`TypeError`.

    `resolve_perez_inputs` checked each pair's *length* and never its element *type*, then called
    `float()` outside the `SkyError` contract. The wire schema is `sky: dict[str, Any]`, so a caller
    reaches this with anything at all, and neither exception is in any handler chain in
    `rad_mcp.point_in_time_tool`. Measured at the wire before this change: `is_error=True`,
    `structured_content=None`, body `Error executing tool run_point_in_time: could not convert
    string to float: 'a'` -- an uncoded, payload-less transport failure, which is the third such
    escape class check 0.4 forbids. All four shapes escaped identically::

        irradiance=["a", "b"]   -> ValueError: could not convert string to float: 'a'
        illuminance=["x", "y"]  -> ValueError: could not convert string to float: 'x'
        irradiance=[None, 200]  -> TypeError: float() argument must be ... not 'NoneType'
        irradiance=[{}, 200]    -> TypeError: float() argument must be ... not 'dict'

    **Carries `code=None`, the same as every other refusal in `resolve_perez_inputs`** -- the
    length refusal directly above each call site, the both-given refusal, and the none-given
    refusal all raise `SkyError(..., code=None)`. No code is invented: the register holds exactly
    141 codes, nothing in its Simulation group describes "a Perez input pair whose elements are not
    numbers", and `rad_mcp.point_in_time_tool`'s `except SkyError` already turns `code=None` into a
    proper structured `refuse(...)` envelope (`refused: true`, `is_error=False`), so the refusal is
    check-0.4-clean without one. Only the *escape* was the violation, not the absence of a code.

    `bool` is accepted rather than specially refused: it is an `int` subclass and `float(True)` is
    1.0, which is the same latitude every other `float()` in this module already gives a caller.
    """
    values: list[float] = []
    for index, element in enumerate(pair):
        try:
            values.append(float(element))
        except (TypeError, ValueError) as exc:
            raise SkyError(
                f"{field}[{index}] must be a number, but got {element!r} "
                f"({type(element).__name__}). gendaylit {flag} takes two numeric values "
                f"({field}=[dni, dhi] in "
                + ("W/m^2" if flag == "-W" else "lux")
                + ").",
                code=None,
            ) from exc
    return (values[0], values[1])


def resolve_perez_inputs(
    *,
    weather: str | None,
    irradiance: tuple[float, float] | None,
    illuminance: tuple[float, float] | None,
    interval_min: int | None = None,
    epw_irradiance: tuple[float, float] | None = None,
) -> tuple[float, float] | tuple[None, tuple[float, float]]:
    """The three-way Perez route preference (docs/tool-surface.md §10). Route 1's EPW read happens
    upstream (R765) and arrives here as `epw_irradiance`.

    Returns either `(dni, dhi)` for `-W`-shaped irradiance, or `(None, (dni_l, dhi_l))` tagging an
    `-L`-shaped illuminance pair, so a caller can tell which flag to build without re-inspecting
    which argument was non-`None`. Raises `SkyError` for every refusal case.

    **Route 1, `weather=<name>`, is read upstream since R765.** `rad_mcp.sky_site.
    resolve_sky_inputs` reads the registered EPW's row above `dry_run` and hands its `(dni, dhi)` in
    as `epw_irradiance`; this function returns that pair, whether the sky named `weather` or named
    no source and no measured pair (the project's only weather input, where `weather` is `None`).
    It refuses route 1 only as the backstop:
    `weather` with a measured pair as well (two sources for one input), and `weather` with nothing
    read from it (a caller that did not go through the resolver). Both carry `code=None`. Before
    R765 route 1 was refused outright, naming the then-missing `weather` tool, rather than silently
    falling through to route 2 or 3; a caller who named a weather file never got a sky computed
    from something else.

    Refuses (also `SkyError`, `code=None`, per docs/tool-surface.md §10 route 3): both `irradiance`
    and `illuminance` given (ambiguous -- which one is authoritative is undecidable without a rule
    the spec does not state); neither given (nothing to build `-W`/`-L` from, and `weather` was
    either absent or already refused above); either being anything other than a genuine two-value
    pair (the single-global-horizontal-value case route 3 refuses by name,
    `PEREZ_SINGLE_VALUE_REFUSED_MESSAGE`).
    """
    if weather is not None:
        if irradiance is not None or illuminance is not None:
            raise SkyError(
                f"weather={weather!r} and a measured irradiance or illuminance pair both name "
                "gendaylit's input; give one source (docs/tool-surface.md §10).",
                code=None,
            )
        if epw_irradiance is None:
            raise SkyError(
                f"weather={weather!r} was named but nothing was read from it: "
                "rad_mcp.sky_site.resolve_sky_inputs reads the registered EPW above dry_run (R765), "
                "and this call did not go through it.",
                code=None,
            )
        return _coerce_perez_pair(epw_irradiance, field="weather", flag="-W")
    if epw_irradiance is not None and irradiance is None and illuminance is None:
        # Route 1 with no `weather` key: the project's only weather input, read upstream (R765).
        return _coerce_perez_pair(epw_irradiance, field="weather", flag="-W")
    if irradiance is not None and illuminance is not None:
        raise SkyError(
            "both irradiance=[dni, dhi] (-W) and illuminance=[dni_l, dhi_l] (-L) were given; pass "
            "exactly one measured pair -- gendaylit accepts only one input mode per call.",
            code=None,
        )
    if irradiance is not None:
        if len(irradiance) != 2:
            raise SkyError(PEREZ_SINGLE_VALUE_REFUSED_MESSAGE, code=None)
        return _coerce_perez_pair(irradiance, field="irradiance", flag="-W")
    if illuminance is not None:
        if len(illuminance) != 2:
            raise SkyError(PEREZ_SINGLE_VALUE_REFUSED_MESSAGE, code=None)
        return (None, _coerce_perez_pair(illuminance, field="illuminance", flag="-L"))
    raise SkyError(
        "a perez sky needs weather=<registered EPW>, irradiance=[dni, dhi], or "
        "illuminance=[dni_l, dhi_l]; none was given (docs/tool-surface.md §10).",
        code=None,
    )


#: docs/tool-surface.md §10: "EPW rows are hourly means, so the EPW path sets -i 60 by default."
#: Named so the EPW route (`rad_mcp.point_in_time_tool._resolve_sky`'s Perez branch, R765) and any
#: caller building a `SkySpec` by hand for testing share one literal rather than two.
EPW_DEFAULT_INTERVAL_MIN = 60


def sky_description_text(gensky_or_gendaylit_stdout: str) -> str:
    """Turn `gensky`/`gendaylit` stdout into a scene-ready `.rad` sky description.

    **Ruling R36 (2026-08-03): this emits a *ground* glow as well as a sky glow.** Before R36 it
    appended only `_SKY_GLOW_SOURCE_SUFFIX`, so the entire lower hemisphere radiated exactly zero
    and every point lost its inter-reflected ground light. The error grew with depth -- the
    signature of a missing inter-reflected component, since a deep point's signal is almost entirely
    inter-reflected: -0.47 % at 2 m, -3.51 % at 7.5 m, **-7.43 % at 13 m** against the office
    fixture. It sat *inside* check 3.1's own +/-2/+/-6/+/-12 % tolerances at every point, so no
    assertion in the phase could have caught it.

    **The ground glow is white (ruling R761).** The project's ground reflectance reaches the sky
    through `gensky -g`/`gendaylit -g` alone (`build_gensky_argv`), so it is applied once; see
    `_GROUND_GLOW_SOURCE_SUFFIX` for what was measured. Until R761 this function took the
    reflectance and coloured the glow with it, applying it twice.

    **Verified fact 2 (module docstring, 2026-08-03): `gensky`'s bare stdout is not a usable light
    source.** It defines only `void brightfunc skyfunc ...` -- a modifier, not a light-emitting
    primitive -- so `oconv` compiles it with exit 0 and empty stderr, and `rtrace -I` against the
    result silently returns `0 0 0` with only a stderr line (`rtrace: warning - no light sources
    found`) as the tell. This function appends the standard `glow`/`source` pair
    (`_SKY_GLOW_SOURCE_SUFFIX`) that every sourced `gensky` workflow in this corpus adds by hand
    (docs/specs/radiance-practical-guide.md line 190), so nothing that calls this function -- rather
    than using `gensky`'s stdout directly -- can produce that silent zero.

    Applies identically to `gendaylit` stdout: `gendaylit` emits the same `skyfunc` modifier shape
    (docs/specs/radiance-engine-overview.md examples), so the same suffix completes it.

    Takes plain text, not a `Result` or a file path: this module has no opinion on whether the
    caller got the text from `run_gensky`/`run_gendaylit`'s `.stdout` or read it back from a file a
    caller already wrote, matching `rad_engine.rtrace.parse_irradiance`'s own text-in-text-out shape
    rather than reaching into a `Result` itself.
    """
    return gensky_or_gendaylit_stdout + _SKY_GLOW_SOURCE_SUFFIX + _GROUND_GLOW_SOURCE_SUFFIX


def run_gensky(
    env: RadianceEnv, argv: list[str], *, cwd: Path | str, timeout: float | None = None,
) -> Result:
    """Spawn `gensky`, returning the raw `Result` (`.stdout` is the `.rad` sky description text --
    see `sky_description_text` before compiling it into a scene; `.stderr`/`.warnings` carry
    anything `gensky` reported, e.g. a low-sun-altitude notice a caller classifies via
    `rad_engine.advisories.classify`).

    Uses `rad_engine.run.run` -- the one spawn path with stdout/stderr kept separate, matching every
    other `rad_engine` module's spawn contract (`rad_engine.rtrace.run_rtrace`, `rad_engine.octree.
    build_octree`). `check` is left at `run`'s default (`True`): a `gensky` that exits non-zero wrote
    no usable sky description, and `RadianceError` already carries the stderr that says why.

    `gensky` is registered in `_ESSENTIAL` (`src/rad_engine/env.py`), so `env.path("gensky")` never
    raises `RadianceNotFound` on an install this project considers usable at all.
    """
    return run(env, argv, cwd=cwd, timeout=timeout)


def run_gendaylit(
    env: RadianceEnv, argv: list[str], *, cwd: Path | str, timeout: float | None = None,
) -> Result:
    """Spawn `gendaylit`. Identical contract to `run_gensky` -- see its docstring.

    `gendaylit` is registered under the `weather` capability in `src/rad_engine/env.py`, so
    `env.path("gendaylit")` resolves on any install that ships it. On an install that genuinely
    lacks it, `run` raises `RadianceNotFound` naming the binary -- a true report about that install,
    unlike the unconditional raise this path returned before the binary was registered.
    """
    return run(env, argv, cwd=cwd, timeout=timeout)


#: What `gendaylit -i` writes to stderr when it moves the sun for the measurement interval,
#: measured on this machine's Radiance: `gendaylit 6 21 5.5 -a 37.9 -o -23.73 -m -30.0 -g 0.2
#: -W 0.0 16.0 -i 60` prints `Solar position corrected at time step 6 21 5.500` and exits 0.
_INTERVAL_CORRECTION_PREFIX: Final = "Solar position corrected at time step"

#: The generator lines reported at `info` (ruling R774, owner decision E7' of 2026-10-03):
#: `gendaylit` with the sun below the horizon. Measured on Radiance 6.1a (`gendaylit` 2.5) through
#: `build_gendaylit_argv`, Athens, 1 21 0.5: a caller's irradiance (no `-i`) prints `Warning: sun
#: altitude below zero at time step 1 21 0.50, printing error sky`; the EPW route (`-i 60`) prints
#: `Warning: sun position too low, printing error sky at 1 21 0.500`. The sky printed with either
#: has `perezlum.cal`'s A1 to A7 (diffuse normalization, ground brightness, Perez coefficients)
#: all zero, so `skybright` is 0 everywhere, and `rtrace` read 0 in each direction traced: a dark
#: sky, the right sky at night. Each entry is a set of substrings that must all appear, since both
#: lines carry a time stamp; the daytime `Warning: zero irradiance at sun altitude > 0, printing
#: error sky` matches neither and stays `warn`, as does a Radiance that rewords these lines -- the
#: safe side.
_NIGHT_ERROR_SKY_PARTS: Final = (
    ("sun altitude below zero", "printing error sky"),
    ("sun position too low, printing error sky",),
)


def _is_night_error_sky(line: str) -> bool:
    return any(all(part in line for part in parts) for parts in _NIGHT_ERROR_SKY_PARTS)


def sky_generator_warning_advisory(line: str) -> EngineAdvisory:
    """`SKY_GENERATOR_WARNING` -- `warn`, or `info` for gendaylit's two sun-below-horizon lines
    (R774) -- one line `gensky`/`gendaylit` wrote to stderr while still
    writing a sky (final review FW-1). Quoted as `rad_engine.convert` quotes a converter's stderr
    (`_quoted_stderr`: control characters stripped, at most 500 bytes).

    Measured: a site with `longitude_east` negated and `meridian_east` not (Athens as
    `-23.72`/`30`) makes `gensky` print `gensky: warning - 3.6 hours btwn. standard meridian and
    longitude` and place the sun 3.6 hours off, exit 0. A line naming the meridian gets a hint at
    R762's east-positive keys, since that is how a caller produces it through this server.
    """
    if _is_night_error_sky(line):
        return EngineAdvisory(
            code="SKY_GENERATOR_WARNING", severity="info",
            message=(
                f'The sky generator reported "{_quoted_stderr(line)}": the sun is below the '
                "horizon, so the sky carries no sun and no sky light, and was used as generated."
            ),
            lines=(line,),
        )
    message = f'The sky generator reported "{_quoted_stderr(line)}" and the sky was used as generated.'
    if "meridian" in line:
        message += (
            " sky.longitude_east and sky.meridian_east are both east-positive (R762); a site with "
            "one negated and the other not puts them hours apart."
        )
    return EngineAdvisory(code="SKY_GENERATOR_WARNING", severity="warn", message=message, lines=(line,))


def sky_generator_advisories(stderr: str, spec: SkySpec) -> list[EngineAdvisory]:
    """What `generate_sky`'s generator wrote to stderr, as advisories, one per non-blank line in
    the order written: `SKY_INTERVAL_CORRECTED` for `gendaylit`'s interval-correction report on a
    spec that passed `-i`, `SKY_GENERATOR_WARNING` for any other line (final review FW-1). Until
    FW-1 `generate_sky` kept stdout alone, so both reports were discarded."""
    advisories: list[EngineAdvisory] = []
    for line in (raw.strip() for raw in stderr.splitlines()):
        if not line:
            continue
        if line.startswith(_INTERVAL_CORRECTION_PREFIX) and spec.interval_min is not None:
            corrected = sky_interval_corrected_advisory(spec.interval_min)
            advisories.append(replace(corrected, lines=(line,)))
        else:
            advisories.append(sky_generator_warning_advisory(line))
    return advisories


def generate_sky(
    env: RadianceEnv, spec: SkySpec, *, cwd: Path | str, timeout: float | None = None,
    advisories: list[EngineAdvisory] | None = None,
) -> str:
    """Spawn the generator for `spec` and return its stdout, rotated by `spec.north` (R694).

    The one place `run_point_in_time`, `run_render` and `run_glare` turn a `SkySpec` into sky text;
    pass the result to `sky_description_text`. Each spawn gets `timeout` on its own; the `xform`
    step reads a sky description of a few lines.

    `advisories`, when given, is extended with `sky_generator_advisories` of the generator's stderr
    (final review FW-1). A list the caller passes rather than a second return value, so the text
    return every caller and test already reads, and the seam `run_glare`'s tests patch, stay as
    they were.
    """
    argv = sky_generator_argv(spec)
    spawn = run_gendaylit if spec.type == "perez" else run_gensky
    generated = spawn(env, argv, cwd=cwd, timeout=timeout)
    if advisories is not None:
        advisories.extend(sky_generator_advisories(generated.stderr, spec))
    text = generated.stdout
    rotation = north_rotation_xform_argv(spec.north)
    if not rotation:
        return text
    return run(env, rotation, cwd=cwd, stdin=text, timeout=timeout).stdout
