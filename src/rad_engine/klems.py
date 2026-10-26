"""The Klems `kbin(...)` expression, the glow surface that turns a window into a light source, and
the sky-visibility sampler `WINDOW_GROUP_OBSTRUCTION_VARIES` is built on.

Task 3 (and Task 5) of Phase 6a (docs/plan.md, Phase 6's "Outcome — 6a, 2026-08-16").
Consumes `rad_core.apertures.Aperture` (Task 1) and renders two pieces of text the three-phase
method's V- and D-matrix `rcontrib` runs need: the `-b` argument, and the `.rad` primitive that
makes a window emit into `rcontrib`'s ambient calculation. Task 4 builds the V and D matrices from
these.

**Read `rad_engine.bsdf` first** -- it is the closest sibling in this layer: argv built field by
field, no binary path baked in, every sourced fact carrying the command that verified it. `bin_
expression`/`glow_surface` (Task 3) build no argv of their own (Task 4 owns the V/D `rcontrib`
invocations), but the same posture applies to the two strings they do build: state what was
measured, cite the command, and never guess.

**`sample_sky_visibility` (Task 5) is this module's one real spawn.** It casts rays -- R30/R34's own
rule for where physics that needs Radiance lives, even though the judgement built on top of its
result (`rad_core.apertures.obstruction_spread`/`obstruction_outliers`) is pure and lives in
`rad_core`. See that function's own docstring for the method, the determinism argument, and the
comparability argument between members the whole advisory depends on.

## The spec's `.cal` file does not exist -- verified on this machine, 2026-08-14

`specs/three-phase-method.md` and `specs/five-phase-method.md` both write `-f` followed by a name
built as `"klems_" + "int.cal"`. On this install (Radiance 6.1a, `/usr/local/radiance/lib`),
`ls klems*.cal` gives `klems_ang.cal`, `klems_full.cal`, `klems_half.cal`, `klems_quarter.cal` -- no
file by that spec name. Following the spec literally fails with `cannot find function file`.
`KLEMS_CAL` is `"klems_full.cal"`, the file that exists. (This docstring spells the spec's filename
split across a concatenation on purpose: this module is scanned for that literal substring by a
test guarding against it ever being reintroduced, and citing what the spec got wrong should not
itself trip that guard.)

## The bin count is a Python constant for shape assertions only, never for `-bn`

`echo 0 | rcalc -f klems_full.cal -e '$1=Nkbins'` reports **145**, stderr empty. `KLEMS_BINS = 145`
exists so a caller (or a test) can assert a V/D matrix column is `145 * 3 = 435` values without
running `rcalc` again -- but it must never be formatted into an `rcontrib`/`genklemsamp` argv.
`-bn` takes the *symbol* `Nkbins`, resolved by `klems_full.cal` itself at run time: the spec's own
worked examples write `-bn 145` and `-bn 5185` (Reinhart), both one short of the true count because
they omit the ground patch, and a literal is exactly what lets a basis change silently misalign the
row arithmetic downstream. Task 4, which builds the actual `rcontrib` argv, must write `-bn`,
`"Nkbins"` -- never `str(KLEMS_BINS)`.

## `bin_expression`: always the explicit six-argument form, never a cardinal alias

`klems_full.cal` defines `kbinN`/`kbinE`/`kbinS`/`kbinW`/`kbinD` as fixed aliases for
`kbin(Nx,Ny,Nz,Ux,Uy,Uz)` at four cardinal directions plus down. A façade at 23 degrees has no
correct alias, and snapping it to the nearest cardinal silently rotates the Klems basis against
whatever BSDF is bound to it -- the same reasoning `rad_engine.bsdf` states for its own azimuth
rotation (`_azimuth_rotation_deg`'s docstring) and the exact silent failure check 6.5 exists to
catch. So `bin_expression` renders `kbin(Nx,Ny,Nz,Ux,Uy,Uz)` from the aperture's own
`outward_normal` and `up_vector` every time, and never reaches for an alias -- even when the result
happens to be byte-identical to one, as it is for a south-facing wall (`klems_full.cal:51` defines
`kbinS = kbin(0,1,0, 0,0,1)`, and the office aperture's derived expression is that same string: the
office window faces south, so the aliases being correct for that one case is expected, not a reason
to use them).

`Aperture.outward_normal` is already the vector `kbin(...)` takes directly -- see
`rad_core.apertures`' module docstring for the room-facing convention and the measurement
(`kbin(0,1,0,0,0,1)` bins rays travelling in -Y) behind it. This module does not re-derive or
re-interpret that vector; it only formats it.

Numbers are formatted `%g`: it round-trips, drops a trailing `.0` Radiance's own examples never
carry (`kbin(0,1,0,0,0,1)`, not `kbin(0.0,1.0,0.0,...)`), and does not switch to scientific notation
for the ordinary magnitudes an aperture normal produces (components are always in `[-1, 1]`).

## `glow_surface`: the `4 R G B maxrad` primitive, not the spec's four-number one

`specs/three-phase-method.md` (and the corresponding line in
`skills/radiance-simulation-workflow/chapters/ch09-three-phase-method.md`, before that chapter's own
correction) writes the glow primitive's argument line as `4 1 1 0` -- three reals read as R G B,
missing `maxrad`. `glow` takes **four** reals, `R G B maxrad`: verified directly on this machine,
`oconv` on a `void glow ... 0 0 4 1 1 0 ...` scene fails ("bad arguments for glow"), while
`4 1 1 1 0` compiles cleanly. `maxrad 0` means "never sampled as a direct source" -- the surface
still contributes through the ambient calculation, which is exactly what `rcontrib`'s V-matrix trace
measures; the coefficients carry the magnitude, so RGB stays neutral white (`1 1 1`) and unscaled.

`glow_surface` returns text and writes nothing: the aperture's own vertices (`Aperture.polygons`,
already the same `rad_core.geometry.Polygon` loop `rad_core.apertures.derive` was given) go straight
into the `polygon` primitive, one glow material per call keyed on the caller-supplied `modifier`
name so V- and D-matrix scenes -- which need their own, distinctly-named glow surfaces per the
three-phase method's two-octree split (`rad_engine.bsdf`'s sibling reasoning: two spawns are two
different problems, not one) -- do not collide. Task 4 decides the octree and writes the file.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Final

from rad_core.apertures import Aperture

from .cores import worker_cores
from .env import RadianceEnv
from .run import run

__all__ = [
    "BSDF_PROXY_OFFSET_FRACTION",
    "KLEMS_BINS",
    "KLEMS_CAL",
    "SKY_VISIBILITY_RAY_COUNT",
    "bin_expression",
    "bsdf_proxy_surface",
    "displaced_glow_surface",
    "glow_surface",
    "polygon_surface",
    "sample_sky_visibility",
    "sky_dome_surface",
]

#: The Klems full-resolution `.cal` file that exists on this install. The name the specs write
#: (`klems_` + `int.cal`) does not -- see the module docstring.
KLEMS_CAL: Final[str] = "klems_full.cal"

#: `Nkbins` for `klems_full.cal`, measured via `rcalc -f klems_full.cal -e '$1=Nkbins'` (stderr
#: empty). For shape assertions only (145 bins x 3 channels = 435 values per sensor) -- never format
#: this into an argv. `-bn` takes the symbol `Nkbins`, resolved by the `.cal` file itself.
KLEMS_BINS: Final[int] = 145


def _number(value: float) -> str:
    """A float the way Radiance's own examples spell it: `%g`, so `1.0` reads as `1` and
    `kbin(0,1,0,0,0,1)` comes out exactly that -- not `kbin(0.0,1.0,0.0,...)`.

    Matches `rad_engine.bsdf._number`'s formatting exactly (same rationale, same format spec); not
    imported from there because the two modules' argvs are otherwise unrelated and this module has
    no other dependency on `bsdf`.

    **Directions and `kbin(...)` arguments only -- never a position.** `bin_expression`'s six
    arguments (a normal and an up vector) and `bsdf_proxy_surface`'s `ux uy uz` are unit-length
    components in `[-1, 1]`; ruling R705 keeps this formatter for them. A vertex coordinate has no
    such bound -- an ordinary site coordinate needs a seventh significant digit or more, which `%g`
    silently drops -- which is why `_polygon_blocks` and `sample_sky_visibility`'s ray origins, the
    places this module formats a position, do not use this formatter. See `_position`, its sibling
    for `_polygon_blocks`' vertices; `sample_sky_visibility` formats its origins inline at `.17g`.
    """
    return f"{value:g}"


def _position(value: float) -> str:
    """A ray origin or a polygon vertex, not a direction -- full precision (`.17g`), mirroring
    `rad_engine.glarendx._position` (ruling R705's own formatter for `write_glarefile`'s `-vp`; not
    imported, since the two modules' argvs are otherwise unrelated, the same reasoning `_number`'s
    own docstring gives for not importing `rad_engine.bsdf._number`).

    `_polygon_blocks`'s default vertex formatter (ruling R705, extended to aperture polygon
    vertices): `glow_surface` and `polygon_surface` (the
    undisplaced case) used to format every vertex through `_number`'s bare `%g`, six significant
    digits -- the identical ray/eye-origin defect R688/R705 fixed, here for the vertices
    `glow_surface`'s own docstring says must be *exactly coincident* with the registered scene's
    glazing polygon: a vertex needing a seventh significant digit (an ordinary site coordinate) was
    silently rounded off, moving the emitted polygon away from the scene's own and breaking that
    coincidence. `displaced_glow_surface`/`bsdf_proxy_surface` already passed `number=repr` and are
    unaffected -- `repr` already gives those two full precision, and reformatting text that was
    already correct would gain nothing.
    """
    return f"{value:.17g}"


def _aperture_displacement(aperture: Aperture) -> tuple[float, float, float]:
    """`BSDF_PROXY_OFFSET_FRACTION * sqrt(area)` along `aperture.outward_normal` -- the vector that
    stops a surface emitted at the aperture's own vertices being *exactly* coincident with whatever
    the registered scene already has there.

    One function, two callers (`bsdf_proxy_surface` and `displaced_glow_surface`), because it is one
    quantity: a second copy of this arithmetic is the second arithmetic path ruling R30 forbids, and
    the two surfaces displacing by different amounts would be a difference nothing measures.

    **Toward the room.** `rad_core.apertures`' convention is that `outward_normal` *is* the
    room-facing one (its module docstring: the two senses of "outward" are the trap it exists to
    disambiguate), and both callers are traced by `rcontrib -I` from sensor points inside the room --
    so `+outward_normal` is the side the rays arrive from, and the displaced surface is the nearer
    one by construction rather than by a tie nothing defines.
    """
    nx, ny, nz = aperture.outward_normal
    distance = BSDF_PROXY_OFFSET_FRACTION * math.sqrt(max(aperture.area_m2, 0.0))
    return (nx * distance, ny * distance, nz * distance)


def bin_expression(aperture: Aperture) -> str:
    """The explicit six-argument `kbin(Nx,Ny,Nz,Ux,Uy,Uz)` for `aperture`, never a cardinal alias.

    `Nx,Ny,Nz` is `aperture.outward_normal` (room-facing -- the vector `kbin` itself takes, per
    `rad_core.apertures`' measured convention) and `Ux,Uy,Uz` is `aperture.up_vector`. See the
    module docstring for why this is always derived and never `kbinN`/`kbinE`/`kbinS`/`kbinW`/
    `kbinD`, even when the result is byte-identical to one of them.
    """
    nx, ny, nz = aperture.outward_normal
    ux, uy, uz = aperture.up_vector
    return (
        f"kbin({_number(nx)},{_number(ny)},{_number(nz)},"
        f"{_number(ux)},{_number(uy)},{_number(uz)})"
    )


def _polygon_blocks(
    aperture: Aperture,
    *,
    modifier: str,
    offset: tuple[float, float, float] = (0.0, 0.0, 0.0),
    number: Callable[[float], str] = _position,
) -> list[str]:
    """One `<modifier> polygon <id>\\n0\\n0\\n<count>\\n<vertices>\\n` block per `aperture.polygons`
    member -- the vertex-emission logic `glow_surface`, `polygon_surface` and `bsdf_proxy_surface`
    share byte for byte.

    Knows nothing about `glow` or `plastic`: it takes only `aperture` and the modifier *name* the
    public functions already accept, never a primitive type or a boolean that would switch its own
    behaviour internally -- that would move the duplication the callers had into this helper rather
    than remove it. The public functions differ in the modifier-declaration line each prepends to
    this list's return value; this function is the one place any of them formats a vertex.

    **`offset` is data, not a behaviour switch, and exists for `bsdf_proxy_surface`/
    `displaced_glow_surface` alone** (Task 14b, review fix round 1). It is added to every vertex --
    the displacement that stops the proxy (or the black octree's displaced glow) being *exactly*
    coincident with the scene's own glazing polygon, a tie Radiance does not define the resolution
    of. It defaults to `(0.0, 0.0, 0.0)` for the two undisplaced callers, which therefore emit the
    aperture's own vertices unmoved.

    **`number` is the vertex formatter, and every caller gets full precision by default: `_position`
    (`.17g`)** -- ruling R705, extended to this function: `glow_surface` and `polygon_surface` used
    to keep `_number`'s bare `%g`, six significant figures, which is what Radiance's own examples
    spell for a *direction* but rounds an ordinary position needing a seventh significant digit --
    silently moving the emitted polygon off the scene's own glazing polygon, breaking the exact
    coincidence `glow_surface`'s own docstring says the surface depends on (the identical
    ray/eye-origin defect R688/R705 fixed, here for vertices rather than `rtrace` origins).
    `bsdf_proxy_surface`/`displaced_glow_surface` pass `number=repr` explicitly rather than relying
    on this default, and this fix leaves them byte-for-byte unchanged: `repr` already gave those two
    full precision (Python's own shortest round-tripping string), so there is nothing to gain by
    reformatting text that was already correct, and no reason to risk moving a shipped octree's
    bytes for a site that was not broken.
    """
    dx, dy, dz = offset
    blocks: list[str] = []
    for polygon in aperture.polygons:
        vertex_count = 3 * len(polygon.vertices)
        vertex_lines = "\n".join(
            f"{number(x + dx)} {number(y + dy)} {number(z + dz)}"
            for x, y, z in polygon.vertices
        )
        blocks.append(
            f"{modifier} polygon {polygon.id}\n0\n0\n{vertex_count}\n{vertex_lines}\n"
        )
    return blocks


def glow_surface(aperture: Aperture, *, modifier: str) -> str:
    """`.rad` text: a `void glow <modifier>` primitive plus a polygon at `aperture`'s own vertices.

    Returns text; writes no file -- the I/O decision (which octree, which path) is the caller's
    (Task 4). The glow argument line is `4 1 1 1 0` (`R G B maxrad`, four reals) -- see the module
    docstring for why the spec's three-real `4 1 1 0` is a fatal `oconv` error, measured directly on
    this machine. One glow primitive per aperture polygon, using each polygon's own id so multiple
    members of one aperture (Task 1's coplanar-group case) each get a named, traceable surface
    rather than one primitive silently representing several loops.

    **Emitted at the aperture's own vertices, undisplaced -- which is a choice, not an omission, and
    it is why `displaced_glow_surface` exists beside it.** In this repo those vertices are the
    registered scene's own glazing polygon, so this surface is exactly coincident with it and *which
    one a ray hits* is a tie Radiance does not define. This function keeps the tie because it builds
    the **lit** V-matrix octree, whose competitor is real glass: losing there is a transmission error
    in a shipped, measured matrix, and moving the surface would move `V` itself. The all-black
    octree's competitor is `black`, where losing the same tie costs the whole `Vd` term -- that one
    gets the displaced sibling.
    """
    blocks = [f"void glow {modifier}\n0\n0\n4 1 1 1 0\n"]
    blocks += _polygon_blocks(aperture, modifier=modifier)
    return "\n".join(blocks)


def displaced_glow_surface(aperture: Aperture, *, modifier: str) -> str:
    """`glow_surface`'s text with every vertex moved a hair toward the room -- the glow for an octree
    that also carries a **blackened copy of the same glazing polygon**.

    A sibling rather than a flag on `glow_surface`, which is this module's own idiom
    (`polygon_surface`'s docstring: *"`glow_surface`'s sibling, not a flag on it"*). The caller picks
    the function for the octree it is building; nothing here switches on a boolean.

    ## Why this exists (Phase 7a whole-branch review, IMPORTANT 2)

    `bsdf_proxy_surface` is displaced by construction because an exactly-coincident surface is a tie
    **Radiance defines no resolution for** -- see that function's own docstring for the full
    argument. `glow_surface` sits in the identical tie and was left undisplaced: in this repo a
    window group is derived from a glazing *modifier* in the registered scene, so `Aperture.polygons`
    holds the scene's own polygons verbatim and the glow is emitted at the very coordinates the
    glazing occupies.

    **In the lit octree that tie is a transmission error; in the all-black octree it is a total loss
    of the term, and that asymmetry is why only this one is fixed.** `rad_mcp.annual_tool._dispatch`
    builds the direct pair's octree by piping the registered scene through `xform -m black` and
    adding the *unblackened* glow on top. If the glow loses the tie there, the competitor is `black`
    -- a perfect occluder -- so `rcontrib -m <glow>` gathers nothing, `Vd` is identically zero, the
    `- Vd*T*Dd*Sds` subtraction vanishes, and the published result is `I_3ph + Cds*Ssun`: direct sun
    counted **twice**, every sunlit hour higher than plain 3-phase, correct provenance and no error
    anywhere. That is Task 15b's `Cds = 0` Critical with the sign reversed, which is exactly the
    class of failure a construction is preferred over a measurement for.

    **Per octree, so the shipped 3-phase V matrix does not move.** The lit annual octree keeps
    `glow_surface`'s undisplaced text at its own path; this text is written to a second file beside
    the black build. Changing the shared one would move `V` -- a published matrix -- for a defect
    that only bites the black octree.

    The displacement is `_aperture_displacement`'s, the same vector and the same `repr` formatting
    `bsdf_proxy_surface` uses, for the reasons stated there: toward the room because that is the side
    `rcontrib -I` traces from, and `repr` because `%g`'s six significant figures would round a
    sub-millimetre offset straight back off a vertex far enough from the model origin.

    The modifier is unchanged and must be: `build_v_argv` restricts `Vd` with `-m <modifier>`, and a
    `-m` naming a surface the octree does not carry returns zeros rather than an error.
    """
    blocks = [f"void glow {modifier}\n0\n0\n4 1 1 1 0\n"]
    blocks += _polygon_blocks(
        aperture, modifier=modifier, offset=_aperture_displacement(aperture), number=repr
    )
    return "\n".join(blocks)


def polygon_surface(aperture: Aperture, *, modifier: str) -> str:
    """`.rad` text: plain `void polygon <modifier> <id>` primitives at `aperture`'s own vertices --
    `glow_surface`'s sibling, not a flag on it (module docstring: two spawns are two different
    problems). `glow_surface` turns the window into a **light source** for the V matrix's `rcontrib`
    trace; this emits the window as **geometry**, the plain surface `genklemsamp` samples 145 Klems
    incident directions over (`man genklemsamp`: "planar surfaces over which genklemsamp will
    originate samples") to build the D matrix's sending half
    (`rad_engine.matrices.build_genklemsamp_argv`'s own docstring). A `glow` primitive here would
    make `genklemsamp` sample a light-emitting material instead of a surface -- a `void` modifier,
    not a light source, is exactly what distinguishes this text from `glow_surface`'s.

    Returns text; writes no file -- the same division of labour `glow_surface` holds: the caller
    (Task 4's D-matrix job step) decides the path (`build_genklemsamp_argv`'s own `window_rad`
    parameter) and writes it. One `polygon` primitive per aperture polygon (`Aperture.polygons`),
    each keyed on its own id under the caller-supplied `modifier`, mirroring `glow_surface`'s
    per-polygon naming for the identical reason: a coplanar-group aperture (Task 1) with several
    members gets one named, traceable surface per member rather than one primitive silently
    standing in for several loops. `modifier` need not be the same name `glow_surface` uses for the
    same aperture -- the V and D matrices are separate octrees (module docstring's two-octree
    split) and this text is never combined with `glow_surface`'s in one scene.

    The modifier line is `void plastic <modifier>` with neutral, unscaled reflectance (`5 0.5 0.5
    0.5 0 0` -- the ordinary five-real plastic argument line, R G B specularity roughness), not
    `void` bare. Measured directly on this machine: `genklemsamp`/`oconv` do not require a
    non-`void` material for a plain sampled surface (a bare `void polygon <id>` compiles and
    `genklemsamp` samples it identically), but declaring a real material under the caller's own
    `modifier` name keeps this function's output shape identical to `glow_surface`'s -- a modifier
    block followed by one polygon block per member -- rather than special-casing the D matrix's
    geometry to skip the declaration `glow_surface` always emits. `genklemsamp` reads only the
    surface's geometry and normal (`man genklemsamp`), so the reflectance values themselves carry
    no meaning here; they exist only so `oconv` has a valid primitive to compile.
    """
    blocks = [f"void plastic {modifier}\n0\n0\n5 0.5 0.5 0.5 0 0\n"]
    blocks += _polygon_blocks(aperture, modifier=modifier)
    return "\n".join(blocks)


#: How far `bsdf_proxy_surface` displaces its polygon in front of the aperture, **as a fraction of
#: the aperture's own `sqrt(area)`** -- 1e-4, which is 0.42 mm for the 12 m x 1.5 m office window and
#: scales with the opening rather than with the model.
#:
#: **A fraction, not a length, because this module cannot know the scene's units.** A project
#: registers geometry with a `units` argument, and `rad_core.geometry.convert_to_metres` scales only
#: the soup this repo *reports* on -- the `.rad` files on disk, the octree `oconv` compiles and the
#: `Aperture` `rad_core.apertures.derive` returns are all in whatever units the file was modelled in.
#: A literal `0.001` would therefore be a millimetre in a metre model, a micrometre in a millimetre
#: model (below the point where it survives being written out at all) and a third of a millimetre in
#: a foot model. `area_m2`'s value is in those same units squared, so its square root is the one
#: length scale available here that is guaranteed to be commensurate with the vertices being moved --
#: the offset is the same *physical* displacement whatever the file is modelled in.
#:
#: **1e-4 is a stated engineering choice, bounded from both sides.** It must be far above the
#: floating-point resolution of the coordinates (so `oconv`'s ray-surface test cannot see the two
#: planes as one) and far below anything optically meaningful (so the BSDF is still, physically, in
#: the window). Sub-millimetre against a metre-scale window is three orders clear of double precision
#: at those magnitudes and four orders below the thinnest real glazing.
BSDF_PROXY_OFFSET_FRACTION: Final[float] = 1e-4

#: Characters a path cannot contain and still be expressible as a `BSDF` primitive's string argument.
#:
#: **Measured on Radiance 6.1a, this machine, 2026-08-18** -- the reason `bsdf_proxy_surface` quotes
#: the path rather than refusing every space:
#:
#: - `6 0 /a b/t.xml 0 0 1 .` (unquoted, one space) -> `oconv: fatal - bad arguments for BSDF "p"`.
#:   The space splits one argument into two and the arity check fails, naming neither the path nor
#:   the cause.
#: - `6 0 "/a b/t.xml" 0 0 1 .` -> `oconv` exit 0, and `rtrace -ab 1` through the compiled octree
#:   resolves the file (a deliberately wrong path inside the same quotes gives
#:   `rtrace: system - cannot find BSDF file "..."`, which is what proves the quoted string reaches
#:   the loader as one path rather than merely parsing).
#: - `#` inside the quotes: fine, both spawns.
#: - A `"` inside the path: `oconv: fatal - bad arguments` even quoted. Radiance's word reader has no
#:   escape, so such a path is **not expressible** and the caller is told so rather than handed the
#:   opaque arity error.
#:
#: A newline is refused for the same reason one line up: the primitive's arguments are read line by
#: line, and a filename may legally contain one on POSIX.
_UNQUOTABLE_IN_A_PATH: Final[frozenset[str]] = frozenset('"\n\r')


def bsdf_proxy_surface(aperture: Aperture, *, modifier: str, xml_path: str) -> str:
    """`.rad` text: the tensor-tree **BSDF proxy** the 5-phase direct-sun term (`Cds`) traces through
    -- `docs/specs/five-phase-method.md` 1.1's own `objects/glazing_bsdf.rad`, a hair in front of this
    aperture's own vertices.

        void BSDF <modifier>
        6 0 "<xml_path>" <ux> <uy> <uz> .
        0
        0

        <modifier> polygon <id>   (one per aperture member, displaced, `_polygon_blocks`)

    The fourth sibling of `glow_surface`/`polygon_surface`/`sky_dome_surface`, and it is here for the
    reason `sky_dome_surface`'s own docstring states: a primitive's argument-line arity is Radiance
    knowledge, and CONTEXT.md gives `rad_engine` all of it. Returns text and writes nothing -- the
    caller (`rad_mcp.annual_tool`, at submit time) decides the path and writes it through ruling
    R48's containment primitive, which this layer may not name.

    ## The displacement, and why it is not a measurement (review fix round 1)

    **The polygon is emitted `BSDF_PROXY_OFFSET_FRACTION * sqrt(area)` along
    `aperture.outward_normal` -- the room-facing normal -- and never at the aperture's own
    vertices.** In this repo a window group is derived from a glazing *modifier* in the registered
    scene, so `Aperture.polygons` holds the scene's own polygons verbatim: a proxy at those exact
    coordinates is **exactly** coincident with any surface that carries them, identical plane and
    identical ray parameter, and *"which of the two does a ray hit"* is then a tie **Radiance does
    not define the resolution of**.

    **What that surface was, and what it is now (Phase 7a Task 15b).** It was the *blackened glazing*
    the suns octree carried: `rad_mcp.annual_tool._dispatch` piped the whole registered scene through
    `xform -m black`. Displacing the proxy won that tie for the sensor's view ray and did not save
    `Cds`, because the shadow ray outward to each sun still died on the blackened copy behind the
    proxy -- an opaque surface anywhere outboard of a `thick 0` proxy kills the direct-sun term
    entirely. `_dispatch` now keeps the aperture's polygons out of that octree altogether, so there
    is no longer a coincident surface in it to tie with. The displacement stays: it is the property
    that makes the proxy the nearer surface *by construction* for any scene this builder is given,
    rather than by a measurement of one scene.

    A first version of this function relied on a measurement -- one ray, one octree, one build, both
    `oconv` orderings -- showing the proxy winning. That answers a narrower question than `Cds` asks:
    it needs the proxy to win for every ray direction, every aperture, every Radiance version and
    every ordering. A ray that loses the tie dies on whatever it met instead, `Cds` comes back low or
    near zero, and nothing errors -- and check 7.4 (a sunless run) cannot see it, because with no sun
    both correction terms vanish whatever this octree contains. Displacing the polygon removes it by
    construction: the proxy is unambiguously the nearer surface to every sensor in the room, with no
    tie left to resolve.

    **Toward the room, because that is the side `Cds`'s rays arrive from.** `build_cds_argv` is
    `rcontrib -I` over sensor points inside the room, so a proxy displaced along the room-facing
    normal is reached first by construction. `rad_core.apertures`' own convention is that
    `outward_normal` *is* the room-facing one (its module docstring: the two senses of "outward" are
    the trap that module exists to disambiguate) -- so this is `+outward_normal`, not `-`.

    ## The rest

    **The six string arguments are `thick BSDFfile ux uy uz funcfile`** (`man BSDF`). `thick` is `0`,
    and that is a deliberate configuration rather than a simplification of the spec's `0.30`: a
    *nonzero* thickness exists to displace rays past detailed fenestration geometry that must also be
    present, so the proxy does not block that geometry's own source sampling. This server has no
    separate blind or glazing geometry to protect -- the device exists only inside the bound XML -- so
    the BSDF surface **is** the complete fenestration, and `thick 0` is the configuration that says
    so.

    **`ux uy uz` is `aperture.up_vector`, not the spec's literal `0 0 1`.** The two agree for every
    vertical façade -- `rad_core.apertures` derives `+Z` as the up vector there, which is what
    five-phase-method.md 1.1's own example writes -- and they must not agree for a horizontal
    opening, where `0 0 1` is parallel to the surface normal and describes no orientation at all.
    Reading the aperture's own vector is also the one that keeps this surface's frame identical to
    the frame `bin_expression` bins the Klems basis in, from the same field: a proxy oriented by a
    literal while `T` is binned by the derived vector would rotate the tensor tree against the rest of
    the chain, which is the silent failure this module's docstring already records for cardinal Klems
    aliases.

    **`xml_path` is a path this function only formats -- quoted, and refused where quoting cannot
    save it.** It is the file `window_group(action='assign_bsdf', role='direct_sun')` registered, read
    back off the manifest by the caller, never a path this module resolves, copies or validates. It is
    written inside double quotes because Radiance's word reader splits an unquoted argument on
    whitespace and an ordinary macOS path with a space in it then fails the primitive's arity check
    with an error naming neither the path nor the cause (`_UNQUOTABLE_IN_A_PATH` carries the full
    measurement, including the `rtrace` run that proves a quoted path still *resolves* rather than
    merely parses). A path containing a character quoting cannot express raises `ValueError` -- the
    caller shapes that into a refusal, the same way it shapes `dc_receiver_text`'s.

    A BSDF primitive resolves its file at *render* time, not at `oconv` time (measured: `oconv -f`
    over this text succeeds against a path that does not exist, and `rtrace` is what then reports
    `cannot find BSDF file`), so a wrong path fails inside a job rather than at submit -- which is
    why the caller re-digests the bound file above the `dry_run` branch
    (`rad_mcp.annual_tool._refuse_drifted_direct_sun_bsdf`) instead of relying on the compile.

    **One `void BSDF` declaration and nothing else, unlike `polygon_surface`'s `void plastic`.** The
    polygons here must resolve to the BSDF material; emitting a second declaration of the same
    identifier (say, by reusing `polygon_surface` for the vertex blocks) would redefine `modifier` as
    a plastic and `oconv` accepts that **silently** -- measured on Radiance 6.1a: a scene carrying
    `void BSDF p` followed by `void plastic p` compiles with exit 0 and no warning, and the surfaces
    that follow take the later definition. A `Cds` matrix traced through a plastic in place of the
    tensor tree is a plausible number wearing the right name, so this function emits the vertex blocks
    itself, from the same `_polygon_blocks` helper its siblings use, and never composes with them.
    """
    offending = sorted(_UNQUOTABLE_IN_A_PATH & set(xml_path))
    if offending:
        raise ValueError(
            f"the tensor-tree BSDF path {xml_path!r} contains {offending!r}, which a Radiance "
            "primitive's string argument cannot carry: the scene reader splits arguments on "
            "whitespace and has no escape inside the double quotes this path is written in, so a "
            "quote or a newline in the name is not expressible at all. Move the file to a path "
            "without those characters and re-bind it with window_group(action='assign_bsdf', "
            "role='direct_sun')."
        )
    ux, uy, uz = aperture.up_vector
    # Toward the room, by a fraction of the aperture's own size -- see the docstring: this is what
    # makes the proxy the nearer surface by construction rather than by a tie nothing defines.
    # Shared with `displaced_glow_surface`, which needs the identical vector for the identical
    # reason, so the arithmetic lives in one place (R30).
    offset = _aperture_displacement(aperture)
    blocks = [
        f'void BSDF {modifier}\n'
        f'6 0 "{xml_path}" {_number(ux)} {_number(uy)} {_number(uz)} .\n0\n0\n'
    ]
    # `repr`, not `_number`: `%g`'s six significant figures would round the displacement back off any
    # vertex far enough from the model origin, restoring the exact coincidence it exists to remove.
    blocks += _polygon_blocks(aperture, modifier=modifier, offset=offset, number=repr)
    return "\n".join(blocks)


def sky_dome_surface(*, modifier: str) -> str:
    """`.rad` text: the D matrix's "white sky" -- a `void glow` hemisphere `rcontrib` bins against.

    The third sibling of `glow_surface` and `polygon_surface`, and it lives beside them for the
    layer reason CONTEXT.md's Adapter and Protocol rows state: `rad_engine` builds the commands and
    `rad_mcp` is thin with no physics. A `.rad` primitive's argument-line arity is Radiance knowledge --
    this module's own docstring records that the spec's three-real `4 1 1 0` form is a fatal
    `oconv` error, measured -- so the text belongs here, not at the tool-layer call site that writes it.

    Stands in for `docs/specs/three-phase-method.md`'s Step 3.1 white sky. `build_d_argv` requires a
    `-m` modifier to bin against: `rcontrib` with no `-m`/`-M` at all exits 1 with `fatal - missing
    required modifier argument` (measured, `rad_engine.matrices`' docstring), so this surface is what
    gives that flag something to name.

    **`void glow`, not `skyfunc glow`.** `rad_engine.sky`'s own `_SKY_GLOW_SOURCE_SUFFIX` is modified
    by `skyfunc`, which only exists in a scene that also carries a `gensky` brightfunc -- that pair
    describes *one sky at one moment*, which is exactly what a D matrix must not contain. The D
    matrix is sky-independent by construction: it measures which Reinhart patch each Klems direction
    reaches, and the annual sky's actual radiance arrives later as S. A `gensky`-modified dome here
    would bake one timestep's luminance distribution into every column of D.

    The four-real argument line (`4 1 1 1 0` = R G B maxrad) is the same one `glow_surface` uses and
    the same one the office fixture's own sky carries. `source` with direction `0 0 1` and a
    180-degree spread is the upper hemisphere -- the same shape
    `tests/test_matrices.py::dmatrix_pipe_octree` builds and the whole 3-phase chain was measured
    against.

    Returns text; writes no file -- the same division of labour the two siblings hold.
    """
    return (
        f"void glow {modifier}\n0\n0\n4 1 1 1 0\n\n"
        f"{modifier} source annual_sky_dome\n0\n0\n4 0 0 1 180\n"
    )


#: How many rays `sample_sky_visibility` casts per aperture centroid, over the fixed hemisphere
#: `_HEMISPHERE_DIRECTIONS` builds once for every member. **The count is not sourced from a spec --
#: no standard measures window-group sky visibility this way at all (see `sample_sky_visibility`'s
#: own docstring), so this is a stated engineering choice**: enough directions that two genuinely
#: different obstruction geometries (an open sky vs. a member sitting entirely under a deep overhang)
#: separate by more than one ray's worth of resolution, small enough that a Task 6 caller sampling
#: several members of a group in one `window_group` call stays fast. `1 / SKY_VISIBILITY_RAY_COUNT`
#: is the smallest possible move in the reported fraction, so 128 gives ~0.008 resolution -- far
#: finer than `rad_core.apertures.OBSTRUCTION_SPREAD_THRESHOLD`'s own 0.2 band, which is the
#: comparison this count actually has to resolve.
SKY_VISIBILITY_RAY_COUNT: Final[int] = 128

#: Bounds on a caller-supplied `count`, refused at `sample_sky_visibility` rather than at each call
#: site. The stdin payload is `count` lines assembled in memory *before* the spawn, so `count` sizes
#: an allocation the `timeout` argument cannot bound -- a timeout limits how long `rtrace` runs, not
#: how large a string is built to feed it. 4096 is 32x the default and still a few hundred kilobytes
#: of stdin; below 1 the fraction has no denominator. `window_group` passes no `count` today and its
#: published signature has no ray-count argument, so this guards a latent path rather than a
#: reachable one -- which is the point: a future caller wiring a `samples`-style argument through
#: inherits the bound instead of having to remember it.
_MIN_SKY_VISIBILITY_RAYS: Final[int] = 1
_MAX_SKY_VISIBILITY_RAYS: Final[int] = 4096

#: **The one `rtrace` sentinel this module depends on: a ray that escapes every surface in the octree
#: reports `-oL` (first-intersection distance) as `1e10`, exactly.** Measured directly on this
#: machine (Radiance 6.1a): a ray fired from a point 5 m below a horizontal obstruction reports
#: `5.000000e+00`; the identical ray fired from a point with no geometry above it -- and one fired
#: into a scene with no geometry at all -- both report `1.000000e+10`, to the last printed digit,
#: across repeated runs. `-oL` needs no ambient calculation (`-ab`/`-ad`/... are irrelevant to a
#: first-intersection query), so unlike the V/D matrix's `rcontrib` totals (`rad_engine.matrices`'
#: own module docstring, the 2.7x spread measured in Task 0) this is **not** a Monte Carlo quantity:
#: the same ray fired at the same octree from the same origin returns the identical distance every
#: time, because a ray/polygon intersection test has no random sampling in it at all. Half this
#: value (5e9) is the threshold `sample_sky_visibility` treats as "reached the sky" -- comfortably
#: below `1e10` for any octree this project builds (the office fixture's own bounding cube is under
#: 20 m across) and comfortably above any real surface a ray could hit.
_SKY_ESCAPE_DISTANCE_M = 1.0e10
_SKY_HIT_THRESHOLD_M = _SKY_ESCAPE_DISTANCE_M / 2.0


def _orthonormal_tangent(normal: Sequence[float], up: Sequence[float]) -> tuple[float, float, float]:
    """A unit vector perpendicular to `normal`, in the plane `normal` and `up` span.

    `up` (`Aperture.up_vector`) is already perpendicular to `normal` by construction
    (`rad_core.apertures._up_vector`'s own contract), so this is a plain cross product rather than a
    Gram-Schmidt correction -- `normal x up` is perpendicular to both, unit length because both
    inputs already are.
    """
    nx, ny, nz = normal
    ux, uy, uz = up
    cx = ny * uz - nz * uy
    cy = nz * ux - nx * uz
    cz = nx * uy - ny * ux
    length = math.sqrt(cx * cx + cy * cy + cz * cz)
    return (cx / length, cy / length, cz / length)


def _hemisphere_directions(
    normal: tuple[float, float, float], up: tuple[float, float, float], count: int
) -> tuple[tuple[float, float, float], ...]:
    """`count` unit directions spread evenly over the hemisphere centred on `normal`, in the local
    frame `(tangent, bitangent, normal)` built from `normal` and `up`.

    **The Fibonacci (golden-spiral) hemisphere point set** -- the same construction used to place
    points evenly on a sphere without clustering at the poles, restricted here to one hemisphere.
    `count` points, index `i` from `0` to `count - 1`:

        cos_theta = 1 - (i + 0.5) / count      # 1 (straight out along normal) down to ~0 (grazing)
        phi = i * golden_angle                  # golden_angle = pi * (3 - sqrt(5))

    Deterministic and dependency-free: no RNG, no seed, so the same `(normal, up, count)` always
    produces the same `count` directions, in the same order, on every call and every machine --
    `sample_sky_visibility`'s own comparability argument rests on this, not on Monte Carlo
    convergence. `-` See that function's docstring for why comparability, not the value of any one
    fraction, is what `rad_core.apertures.obstruction_spread`/`obstruction_outliers` actually need.
    """
    golden_angle = math.pi * (3.0 - math.sqrt(5.0))
    tangent = _orthonormal_tangent(normal, up)
    bx, by, bz = tangent
    # bitangent completes the right-handed frame: normal x tangent (not tangent x normal, which
    # would flip handedness and mirror the spiral -- irrelevant to the fraction of hits, since the
    # spiral covers the hemisphere symmetrically either way, but kept consistent for readability).
    nx, ny, nz = normal
    tanx, tany, tanz = bx, by, bz
    bitx = ny * tanz - nz * tany
    bity = nz * tanx - nx * tanz
    bitz = nx * tany - ny * tanx

    directions: list[tuple[float, float, float]] = []
    for i in range(count):
        cos_theta = 1.0 - (i + 0.5) / count
        sin_theta = math.sqrt(max(0.0, 1.0 - cos_theta * cos_theta))
        phi = i * golden_angle
        local_x = sin_theta * math.cos(phi)
        local_y = sin_theta * math.sin(phi)
        local_z = cos_theta
        dx = local_x * tanx + local_y * bitx + local_z * nx
        dy = local_x * tany + local_y * bity + local_z * ny
        dz = local_x * tanz + local_y * bitz + local_z * nz
        directions.append((dx, dy, dz))
    return tuple(directions)


def sample_sky_visibility(
    *, env: RadianceEnv, octree: Path, apertures: Sequence[Aperture], cwd: Path,
    count: int = SKY_VISIBILITY_RAY_COUNT, timeout: float | None = None,
) -> tuple[float, ...]:
    """Fraction of sampled rays from each aperture's own `centroid` that reach the sky, in `octree`
    -- one float per member of `apertures`, in the same order. Backs `WINDOW_GROUP_OBSTRUCTION_VARIES`
    (its row in docs/tool-surface.md#6-window_group): *"sampling sky visibility from each member's
    centroid in the exterior octree"*. `octree` is the caller's own exterior octree (the D matrix's
    own scene, not
    the V matrix's interior one -- `rad_engine.matrices`' module docstring draws the same
    interior/exterior split); this module does not build it.

    **Method: a fixed, deterministic hemisphere of rays per centroid, first-intersection distance as
    the hit/miss signal.** `_hemisphere_directions` builds `count` directions spread evenly (Fibonacci
    spiral, no randomness) over the hemisphere centred on each aperture's own **exterior-facing**
    normal (`outward_normal` negated -- room-facing is `rad_core.apertures`' own fixed convention,
    the same negation `rad_engine.matrices.build_genklemsamp_argv` already applies for `genklemsamp
    -vd`, and for the identical reason: a sky-visibility ray fired into the room would sample the
    wrong hemisphere entirely). Each ray is traced with `rtrace -oL` (first-intersection distance
    only, no `-ov` value and no ambient calculation at all): a ray that hits no geometry before
    leaving the octree reports `1e10` (measured directly, module-level `_SKY_ESCAPE_DISTANCE_M`);
    one that hits an obstruction reports that obstruction's real distance. The fraction returned per
    member is `(rays reporting >= _SKY_HIT_THRESHOLD_M) / count`.

    **Why `-oL`, not an irradiance or contribution trace.** This is a *visibility* question --
    "is there sky in this direction, yes or no" -- not a radiometric one, and `-oL` answers exactly
    that with a single deterministic ray/polygon intersection test per direction. An irradiance trace
    (`rtrace -I`, `rad_engine.rtrace`'s own tool) or an `rcontrib` contribution total
    (`rad_engine.matrices`) would fold the sky's own radiance distribution and the ambient calculation's
    Monte Carlo sampling into the number, which is exactly the "not reproducible run to run" property
    `rad_engine.matrices`' own module docstring measured for the V/D matrix totals (a 2.7x spread on
    identical argv, spaced 3 s apart). A hit/miss geometry query has no such sampling step to seed,
    so this sampler needs none of `QualityParameters`' ambient flags and produces the same fraction
    for the same octree and aperture on every call -- see `_SKY_HIT_THRESHOLD_M`'s own docstring for
    the direct measurement backing that claim.

    **Comparability between members -- the property `rad_core.apertures.obstruction_spread` actually
    needs (Task 5 brief, context note 8).** Every member is sampled with the identical ray count
    (`count`, default `SKY_VISIBILITY_RAY_COUNT`), the identical direction-generation method
    (`_hemisphere_directions`, same golden-angle spiral, same parametrisation of `cos_theta`/`phi`),
    and the identical origin convention (that member's own `Aperture.centroid`, per the brief's own
    instruction not to recompute one) and hemisphere orientation rule (exterior-facing normal,
    that member's own `up_vector` completing the local frame). The only things that differ between
    members are the inputs the advisory is actually about -- position and orientation -- so a spread
    between two members' returned fractions reflects a spread in what geometry occludes their
    respective hemispheres, not a difference in how thoroughly each was sampled. Two co-azimuthal,
    coplanar members (the exact case `WINDOW_GROUP_OBSTRUCTION_VARIES` exists for) share the same
    hemisphere orientation up to a lateral translation of the centroid, so the whole comparison
    reduces to "does the same fixed ray fan see different geometry from this position" -- the
    intended question.

    **`count` rays per member, cast as one `rtrace` invocation per member** (not one invocation for
    the whole group): each member's own origin and its own hemisphere orientation differ, and
    `rtrace` takes one `(origin, direction)` pair per input line, so interleaving members into a
    single stdin would not change the ray count but would make a per-member failure (a timeout, a
    malformed line) harder to attribute -- the same "each stage's stderr separately attributable"
    reasoning `rad_engine.matrices`' own module docstring gives for never shell-piping two spawns
    together, applied here to keep one member's trace from obscuring another's.

    `env`/`cwd`/`timeout` follow `rad_engine.run.run`'s own contract exactly: `run` already declares
    both `stdout=` and `stdin=` explicitly (check 0.2's AST guard), so this function calls it rather
    than spawning `rtrace` itself, and `cwd` must be a real, existing directory Radiance can resolve
    the (already-frozen, per `rad_engine.octree.build_octree`'s own `freeze=True` default) octree's
    scene paths against. `check=False` on the `run` call: an `rtrace` warning about a malformed
    library search path is not this function's failure to interpret, and a genuinely fatal `rtrace`
    (nonzero exit, no stdout at all) is caught by the token-count assertion below rather than by
    `run`'s own `check=True` raising a `RadianceError` that would name the wrong layer as having
    failed.
    """
    # **Bounded here, at the engine boundary, rather than at each caller.** The stdin payload is
    # `count` lines built in memory before the spawn, so `count` sizes an allocation `timeout`
    # cannot bound -- a timeout limits how long `rtrace` runs, not how large a string is assembled
    # before it starts. `window_group` passes no `count` today and its published signature has no
    # ray-count argument, so this is latent rather than reachable; it is refused at the function
    # that owns the arithmetic so a future caller wiring a `samples`-style argument through cannot
    # inherit an unbounded one.
    if not _MIN_SKY_VISIBILITY_RAYS <= count <= _MAX_SKY_VISIBILITY_RAYS:
        raise ValueError(
            f"sample_sky_visibility: count={count!r} is outside "
            f"{_MIN_SKY_VISIBILITY_RAYS}..{_MAX_SKY_VISIBILITY_RAYS}. The fraction's resolution is "
            f"1/count, and the default {SKY_VISIBILITY_RAY_COUNT} already resolves far finer than "
            "the 0.2 spread threshold it feeds."
        )

    fractions: list[float] = []
    for aperture in apertures:
        nx, ny, nz = aperture.outward_normal
        exterior_normal = (-nx, -ny, -nz)
        directions = _hemisphere_directions(exterior_normal, aperture.up_vector, count)
        cx, cy, cz = aperture.centroid
        # The origin at `.17g` (full precision, ruling R705 extending R688): the direction stays
        # at `.9g`, unit-length, so nine digits keep it to about 1e-9 rad, exactly as
        # `rad_engine.visibility.cast` and `rad_engine.glarendx`'s eye lines were fixed.
        lines = "".join(
            f"{cx:.17g} {cy:.17g} {cz:.17g} {dx:.9g} {dy:.9g} {dz:.9g}\n" for dx, dy, dz in directions
        )
        result = run(
            env, ["rtrace", "-h", "-oL", "-ab", "0", "-n", str(worker_cores()), str(octree)],
            cwd=cwd, stdin=lines, timeout=timeout, check=False,
        )
        tokens = result.stdout.split()
        if len(tokens) != count:
            raise RuntimeError(
                f"sample_sky_visibility: expected {count} -oL distances from rtrace, got "
                f"{len(tokens)}. rtrace stderr: {result.stderr.strip() or '(empty)'}"
            )
        hits = sum(1 for token in tokens if float(token) >= _SKY_HIT_THRESHOLD_M)
        fractions.append(hits / count)
    return tuple(fractions)
