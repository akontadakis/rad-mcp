"""`rad_engine.evalglare` -- building the `evalglare` argv, running it, and parsing both its
plain and detailed (`-d`) output forms and its stderr notices.

Owns `evalglare` (docs/tool-surface.md, tool 13). This module knows how to ask `evalglare` for a
daylight glare probability (DGP) and its companions, and nothing about whether a `ViewHeader` or a
resolution is a *valid* input for that question -- `rad_core.glare.check_glare_input` answers that,
purely, before this module is ever called.

**Ground truth measured on this machine, `evalglare 3.06 release 01.10.2025 by J.Wienold, EPFL`,
2026-09-03, all against `tests/fixtures/hdr/high_fish_view.hdr` unless stated otherwise:**

    $ evalglare tests/fixtures/hdr/high_fish_view.hdr
    (stdout) dgp,dgi,ugr,vcp,cgi,Lveil: 0.211771 14.926289 18.194143 42.333836 21.095667 27.245396
    (stderr) Notice: Low brightness scene. Vertical illuminance less than 380 lux! dgp might
             underestimate glare sources
    rc 0

**R257 -- the bare default is uncorrected; `evalglare` does NOT apply low-light correction by
default, contrary to what an earlier reading of the man page assumed.** `evalglare -C l+` on the
same fixture gives `dgp 0.195696` (not `0.211771`) and a second notice, `"Low brightness scene. dgp
below 0.2!"`. Passing `-C l+` to match that assumption would silently change the number every
caller of this module gets back. `build_evalglare_argv` therefore never emits `-C` at all, and
docs/tool-surface.md's claim that correction is on by default is corrected by Phase 8 Task 8, not
by this module pretending otherwise.

**R259 -- `-V` and `-N` are never emitted.** `evalglare -V tests/fixtures/hdr/high_fish_view.hdr`
prints only `270.804432` (the vertical illuminance) and **exits 1** -- a "success" this project's
`run()` would raise on as a failure. `evalglare -N 1 tests/fixtures/hdr/high_fish_view.hdr`
**segfaults, rc 139**, on this build. Neither flag is ever built into the argv. Vertical
illuminance is read from the `-d` line's `E_v` field instead, which exits 0.

**R260 -- `run_evalglare` always passes `-d`.** `evalglare -d` prints a per-source table (irrelevant
here) followed by a 23-field summary line whose first six named values reproduce the six plain-form
values exactly (`0.211771 56.674602 270.804432 71.383854 46.545439 14.926289 ...` -- `dgp` first,
`av_lum` second, matching the six PLAIN_FIELDS values in the same relative order once the extra
fields are read out by name, not position). `-d` changes the *report*, never the *computation* --
`tests/test_evalglare.py::TestCheck81TheExactRecordedValues::test_the_plain_and_detailed_forms_agree`
asserts the two forms agree to 1e-6 on every value the plain form carries, which is what would catch
a field-order change in either form.

**`evalglare` does not refuse a perspective image.** `evalglare tests/fixtures/hdr/high_presp_view.hdr`
returns rc 0 and `dgp 0.220343` with only a stderr warning (`"warning: image has perspective view
type specified in header !"`), not a refusal. Whether this project accepts that view is
`rad_core.glare.check_glare_input`'s job, exercised at the tool layer -- this module runs whatever
argv it is asked to build.

**Ruling R269 -- `-q` moves `dgi` and `ugr` only. It does NOT move `cgi`, `dgp`, `vcp` or `Lveil`.**
An earlier reading of this docstring (and the brief it was copied from) claimed `cgi` moved too;
measured, it does not. All three field lines, `high_fish_view.hdr`, field order
`dgp,dgi,ugr,vcp,cgi,Lveil`:

    -q 0 (CIE, default): 0.211771 14.926289 18.194143 42.333836 21.095667 27.245396
    -q 1 (average):      0.211771 16.131821 19.520985 42.333836 21.095667 27.245396
    -q 2 (Ev/pi):        0.211771 14.287387 17.538897 42.333836 21.095667 27.245396

`dgi` and `ugr` are the only columns that differ across the three rows; `dgp` (0.211771), `vcp`
(42.333836), `cgi` (21.095667) and `Lveil` (27.245396) are identical in all three. `background_mode`
maps `"cie"`/`"average"`/`"ev_over_pi"` onto `-q 0`/`-q 1`/`-q 2` and always emits the flag
explicitly -- `"cie"` reproduces the measured default rather than relying on it, the same posture
that makes the `-C` omission above a deliberate choice rather than an oversight.

**Notices are `Result.warnings`, carried verbatim, in order, never reworded or dropped.** The same
contract `ImageInfo.warnings`, `Octree.warnings`, `ReadScene.warnings` and `StrippedScene.warnings`
already keep for their own spawns (`rad_engine.image`, `rad_engine.octree`, `rad_engine.scene`): a
Radiance process that exits 0 having written a warning to stderr is, per CONTEXT.md, this project's
single most common route to a confidently wrong answer. `EvalglareResult.notices` flows through
`rad_engine.advisories.classify` at the tool layer; `GLARE_LOW_LIGHT_SCENE`'s rule in that module's
`RULES` already recognises the exact notice above.

**`evalglare -c` opens its check-image destination itself, so `rad_engine.safe_io.open_regular_file`
cannot guard that open the way `run_to_file` guards a written octree (ruling R48's area).** Instead,
before spawning anything, `run_evalglare` refuses a `check_image` destination that already exists as
a symlink or as a non-regular file (`NotARegularFileError`, the same type `open_regular_file` raises)
-- the same posture as R48's guards, applied where the file is opened by a child process rather than
by this process. This narrows the window; it does not close it the way `O_NOFOLLOW`+`S_ISREG` on an
open file descriptor does, because there is no descriptor here for this module to hold.

**Ruling R268 -- `evalglare -c`'s check image does NOT preserve luminance, and its own man page says
it does.** The man page: *"the detected glare sources will be colored to different colors where the
rest of the image is set to gray. The luminance values of all pixels are kept to the initial
value."* Measured false on `tests/fixtures/hdr/high_fish_view.hdr`, whole-image `pvalue -h -H -o -d`
scan: of 262144 pixels, 259634 are bit-identical, 2510 change, and 1051 of those are zeroed outright
(`(0.0, 0.0, 0.0)`) rather than recoloured. The input's true maximum luminance is 2187.38; the check
image's true maximum is 2149.79, ratio 0.9828150572831426 -- lower, not equal. What `-c` actually
guarantees is that it *marks* a small, exact minority of pixels rather than tone-mapping the whole
image; `tests/test_evalglare.py::TestCheck89TheCheckImageIsMarkedNotToneMapped` asserts exactly that,
never preservation.

**Never md5 a Radiance output file to compare it against another, on this project.** `evalglare`
writes `CAPDATE=`, `GMT=` and the full `-c` output path into the header on every run, so the whole
file's md5 differs run to run even when every pixel is identical -- measured: five plain re-runs of
the command above, plus one with `-d` and one with `-q 1`, gave seven different whole-file md5s and
one single `pvalue`-body md5 (`77d22d2c74e3436b953d8fece7fbdf1b`) shared by all seven. Compare pixel
bodies (`pvalue -h -H -o -d`'s output), never the file bytes or the file's own md5.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .env import RadianceEnv
from .run import run
from .safe_io import NotARegularFileError

__all__ = [
    "DETAILED_FIELDS",
    "PLAIN_FIELDS",
    "THRESHOLD_FACTOR_ABSOLUTE_BOUNDARY",
    "EvalglareResult",
    "GlareValues",
    "build_evalglare_argv",
    "parse_detailed",
    "parse_plain",
    "run_evalglare",
]

#: `evalglare`'s bare-form summary header, measured verbatim (see the module docstring).
PLAIN_FIELDS: tuple[str, ...] = ("dgp", "dgi", "ugr", "vcp", "cgi", "Lveil")

#: `evalglare -d`'s summary header, measured verbatim (see the module docstring). 23 fields; the
#: first five are `dgp`, `av_lum`, `E_v`, `lum_backg`, `E_v_dir` -- `E_v` (index 2) is the vertical
#: illuminance this module reports as `GlareValues.vertical_illuminance_lux`.
DETAILED_FIELDS: tuple[str, ...] = (
    "dgp", "av_lum", "E_v", "lum_backg", "E_v_dir", "dgi", "ugr", "vcp", "cgi",
    "lum_sources", "omega_sources", "Lveil", "Lveil_cie", "dgr", "ugp", "ugr_exp",
    "dgi_mod", "av_lum_pos", "av_lum_pos2", "med_lum", "med_lum_pos", "med_lum_pos2", "ugp2",
)

#: `evalglare -b`'s factor-versus-absolute boundary, in cd/m2, verbatim from the man page as this
#: corpus transcribes it (docs/specs/radiance-practical-guide.md:1492-1496, §evalglare):
#:
#:   *"-b factor: Specifies the threshold factor for detecting glare sources.*
#:   *If factor is greater than 100, it is used as a constant threshold in cd/m2.*
#:   *If factor is less than or equal to 100 and a task position is given (with -t or -T), this*
#:   *factor multiplied by the average task luminance will be the threshold.*
#:   *If factor is less than or equal to 100 and no task position is given, this factor multiplied*
#:   *by the average luminance in the entire picture will be used (not recommended)."*
#:
#: It lives here rather than at the tool layer because it is a fact about `evalglare`'s own argv
#: semantics, and CONTEXT.md's Layers table says `rad_mcp` is thin with *"No physics"*. It was a
#: bare `100` in `rad_mcp.glare_tool`'s `GLARE_NO_TASK_AREA` condition, uncited, until the Phase 8
#: whole-branch fix wave.
#:
#: Note the asymmetry the man page states and this constant preserves: the boundary is **`> 100`**
#: for the absolute reading, so exactly 100 is still a *factor*. That is the condition
#: `GLARE_NO_TASK_AREA` fires on, and it is why the advisory is correctly silent at 100.1.
THRESHOLD_FACTOR_ABSOLUTE_BOUNDARY: float = 100.0

#: `background_mode` -> `-q` value. Ruling R269, measured: only `dgi` and `ugr` move across these
#: three; `dgp`, `vcp`, `cgi` and `Lveil` do not. See the module docstring for the three field
#: lines this was measured from.
_BACKGROUND_MODE_TO_Q = {"cie": "0", "average": "1", "ev_over_pi": "2"}


def _number(value: float) -> str:
    """A float as `evalglare`'s argv sees it: `%g`, so `20.0` becomes `20`. Same convention as
    `rad_engine.bsdf._number` and `rad_engine.klems._number` -- one formatting rule, not a third
    copy of it (ruling R30)."""
    return f"{value:g}"


@dataclass(frozen=True)
class GlareValues:
    """The six values every `evalglare` invocation reports, plus the vertical illuminance read off
    the `-d` line's `E_v` field. All `float`."""

    dgp: float
    dgi: float
    ugr: float
    vcp: float
    cgi: float
    lveil: float
    vertical_illuminance_lux: float


@dataclass(frozen=True)
class EvalglareResult:
    """One `run_evalglare` invocation's outcome.

    `notices` is `Result.warnings`, verbatim and in order -- see the module docstring; never
    collapsed, never reworded. `check_image` is the path passed to `run_evalglare`, or `None` if no
    check image was requested. `argv` is the resolved argv `run()` actually executed (the same shape
    `RadianceError.argv` carries), for a caller's audit trail.
    """

    values: GlareValues
    notices: tuple[str, ...]
    check_image: Path | None
    argv: tuple[str, ...]


def build_evalglare_argv(
    image: Path | str,
    *,
    detailed: bool = True,
    check_image: Path | str | None = None,
    task_area: tuple[float, float, float] | None = None,
    threshold_factor: float | None = None,
    background_mode: str = "cie",
) -> list[str]:
    """Build the `evalglare` argv. Never emits `-C`, `-V` or `-N` -- see the module docstring for
    R257 and R259. `image` is always the last argument.

    `background_mode` selects `-q` (`"cie"` -> `0`, `"average"` -> `1`, `"ev_over_pi"` -> `2`),
    raising `ValueError` on anything else rather than silently defaulting to `"cie"` -- a caller
    that misspells the mode learns immediately, not from a downstream value that quietly used the
    CIE weighting it did not ask for.
    """
    try:
        q_flag = _BACKGROUND_MODE_TO_Q[background_mode]
    except KeyError:
        raise ValueError(
            f"unknown background_mode {background_mode!r}; expected one of "
            f"{sorted(_BACKGROUND_MODE_TO_Q)}."
        ) from None

    argv: list[str] = ["evalglare"]
    if detailed:
        argv.append("-d")
    argv += ["-q", q_flag]
    if check_image is not None:
        argv += ["-c", str(check_image)]
    if task_area is not None:
        x_pos, y_pos, angle = task_area
        argv += ["-t", _number(x_pos), _number(y_pos), _number(angle)]
    if threshold_factor is not None:
        argv += ["-b", _number(threshold_factor)]
    argv.append(str(image))
    return argv


def _parse_summary_line(stdout: str, fields: tuple[str, ...]) -> dict[str, float]:
    """Locate the line whose comma-separated header equals `fields` exactly, split it on `": "`,
    and return `{field: value}`. Never indexes by line position: a leading `band:` line (from
    `-B`, which this module never passes but a hand-run command might) is skipped because its
    header does not equal `fields`, not because it comes first.

    Raises `ValueError` if no line's header equals `fields`, or if a line whose header does equal
    `fields` nonetheless carries a different number of values -- a version change that adds or
    drops a column must fail loudly rather than silently misassign the remaining values.
    """
    for line in stdout.splitlines():
        line = line.strip()
        if ": " not in line:
            continue
        header_part, _, values_part = line.partition(": ")
        header_fields = tuple(f.strip() for f in header_part.split(","))
        if header_fields != fields:
            continue
        values = values_part.split()
        if len(values) != len(fields):
            raise ValueError(
                f"evalglare's summary line names {len(fields)} fields "
                f"({','.join(fields)}) but carries {len(values)} values: {line!r}"
            )
        return dict(zip(fields, (float(v) for v in values)))
    raise ValueError(
        f"no line in evalglare's output carries the header {','.join(fields)}: {stdout!r}"
    )


def parse_plain(stdout: str) -> dict[str, float]:
    """The bare-form summary line -> `{field: value}` over `PLAIN_FIELDS`. See
    `_parse_summary_line`."""
    return _parse_summary_line(stdout, PLAIN_FIELDS)


def parse_detailed(stdout: str) -> dict[str, float]:
    """The `-d` summary line -> `{field: value}` over `DETAILED_FIELDS`. See
    `_parse_summary_line`."""
    return _parse_summary_line(stdout, DETAILED_FIELDS)


def _refuse_unsafe_check_image_destination(path: Path) -> None:
    """Refuse a `check_image` destination that is already a symlink or a non-regular file, before
    `evalglare` is ever spawned. See the module docstring: `evalglare -c` opens this path itself, so
    `rad_engine.safe_io.open_regular_file` cannot guard the open the way `run_to_file` guards a
    written octree. This is the best this process can do without a descriptor of its own to hold --
    it narrows the window, it does not close it.
    """
    if path.is_symlink():
        raise NotARegularFileError(
            f"{str(path)!r} is a symlink. evalglare -c opens its check-image destination itself, "
            "so it cannot be guarded with an O_NOFOLLOW open the way a written octree is; refusing "
            "before spawning evalglare instead."
        )
    if path.exists() and not path.is_file():
        raise NotARegularFileError(
            f"{str(path)!r} exists and is not a regular file. evalglare -c opens its check-image "
            "destination itself; refusing before spawning evalglare rather than letting it open a "
            "FIFO or device and block or misbehave."
        )


def run_evalglare(
    env: RadianceEnv,
    image: Path | str,
    *,
    cwd: Path | str,
    check_image: Path | str | None = None,
    task_area: tuple[float, float, float] | None = None,
    threshold_factor: float | None = None,
    background_mode: str = "cie",
    timeout: float | None = None,
) -> EvalglareResult:
    """Run `evalglare` against `image` and parse its detailed output.

    Always passes `-d` (ruling R260): vertical illuminance (`GlareValues.vertical_illuminance_lux`)
    is only available on the detailed line's `E_v` field, and `-d` is measured to change the report,
    never the six shared values. Never emits `-C`, `-V` or `-N` (rulings R257, R259) -- see the
    module docstring.

    `check_image`, if given, is refused before spawning if it already exists as a symlink or a
    non-regular file (see `_refuse_unsafe_check_image_destination`); otherwise it is passed straight
    through to `evalglare -c`, which creates or overwrites it itself.
    """
    check_image_path: Path | None = None
    if check_image is not None:
        check_image_path = Path(check_image)
        _refuse_unsafe_check_image_destination(check_image_path)

    argv = build_evalglare_argv(
        image,
        detailed=True,
        check_image=check_image_path,
        task_area=task_area,
        threshold_factor=threshold_factor,
        background_mode=background_mode,
    )
    result = run(env, argv, cwd=cwd, timeout=timeout)
    parsed = parse_detailed(result.stdout)

    values = GlareValues(
        dgp=parsed["dgp"],
        dgi=parsed["dgi"],
        ugr=parsed["ugr"],
        vcp=parsed["vcp"],
        cgi=parsed["cgi"],
        lveil=parsed["Lveil"],
        vertical_illuminance_lux=parsed["E_v"],
    )

    return EvalglareResult(
        values=values,
        # Every stderr line, verbatim and in order -- never merged into stdout, never dropped. See
        # the module docstring and `EvalglareResult.notices`.
        notices=tuple(result.warnings),
        check_image=check_image_path,
        argv=tuple(result.argv),
    )
