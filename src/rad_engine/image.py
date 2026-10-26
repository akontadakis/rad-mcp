"""`rad_engine.image` -- reading an HDR's resolution, view header and statistics.

Owns `getinfo` and `pextrem`/`pvalue` (docs/tool-surface.md #15, `image`, "Layers"). This module
knows how to ask Radiance what a picture *is* -- its pixel dimensions, the `VIEW=` line `rpict`
wrote into it, and the luminance range and mean over its pixels -- and nothing about falsecolor,
tone mapping or format conversion, which are later tasks against the same binaries.

**Every spawn goes through `rad_engine.run.run`, stderr is never merged into stdout, and every
warning is surfaced on `ImageInfo.warnings`** (CONTEXT.md; this module's own single most
load-bearing rule). "Never merged" is only half of it: a Radiance process that exits 0 having
written a warning to stderr is, per CONTEXT.md, this project's single most common route to a
confidently wrong answer, and a caller can only act on a warning it can see. `ImageInfo.warnings`
therefore carries `Result.warnings` from all four spawns (`getinfo`, `getinfo -d`, `pextrem -o`,
and `pvalue` inside `_pixel_statistics`), verbatim and in call order, never collapsed or reworded --
the same contract `Octree.warnings`, `ReadScene.warnings` and `StrippedScene.warnings` already keep
for their own spawns (`rad_engine.octree`, `rad_engine.scene`).

**`getinfo -d` writes `-Y` before `+X`.** Measured on this machine (Radiance 6.1a):

    $ getinfo -d < tests/fixtures/hdr/high_fish_view.hdr
    -Y      512 +X      512

Reading the two numbers in written order would report `(512, 512)` for a square image and
silently transpose every non-square one -- measured against `high_presp_view.hdr` (512 wide by
253 high): `getinfo -d` there prints `-Y      253 +X      512`, so position 0 is the *height* and
position 1 is the *width*. `parse_resolution` therefore reads the `+X`/`-Y` fields **by name**
(a regex search, unanchored), never by position.

**`getinfo` is invoked over stdin with only the header block, never given the path as an
argument.** `getinfo <path>` and `getinfo -d <path>` both prefix their output with `"<path>: "`
(and, for the header form, indent every subsequent line with a literal tab) -- measured on this
machine, and different from the prefix-free, unindented text this module's own parsers (and the
task's measured fixture lines) are written against. Feeding `getinfo` the file's bytes over stdin
instead, as `getinfo -d < file` was actually measured, sidesteps that reformatting rather than
stripping it back off afterwards. `_read_header_block` reads only the ASCII header prefix (through
the blank line and the resolution string) off the open file descriptor -- measured on this machine
that `getinfo`/`getinfo -d` read no further than that boundary, so the multi-megabyte RLE pixel
payload never has to pass through Python or through `run`'s text-mode stdin at all. `pextrem` and
`pvalue`, by contrast, are given the path directly: their output is measured to be identical either
way (no prefix in either mode), and passing the path lets them read the binary pixel data directly
rather than needing it decoded through a Python string.

**`read_image` refuses a path that is not a regular file before spawning anything.** It opens
`path` through `rad_engine.safe_io.open_regular_file` (the same guard `rad_engine.run.run_to_file`
uses on the write side) before any `run()` call -- a symlink or a FIFO handed to this function is
refused here rather than reaching a Radiance binary that would either read through the link or
block forever on the open.

**`ImageStatistics`'s three fields are luminance, in cd/m^2, not raw RGB.** `pextrem -o`'s two
lines each carry an `(R, G, B)` triple at the extremal pixel; `minimum`/`maximum` are that triple
run through `rad_core.photometry.to_lux` -- the same `179 * (0.265 R + 0.670 G + 0.065 B)` formula
`rad_core.photometry`'s own docstring derives, reused rather than re-typed a second time (the
project's standing rule against a second copy of that exact expression: see
`rad_engine.rtrace`/`rad_engine.annual`'s own docstrings on the same point). It is named `to_lux`
because its first caller converts `rtrace -I` irradiance (W/m^2) to illuminance (lux); applied here
to a picture's RGB, which is radiance (W/sr/m^2, `pextrem -o`'s "original (radiance) values"), the
identical arithmetic yields luminance (cd/m^2) instead -- the same CIE photopic weights and the
same 179 lm/W luminous efficacy govern both conversions, only the input/output quantity's name
changes. `mean` is the same conversion applied to the per-channel means of every pixel `pvalue -d
-h -H -o <path>` prints (one `R G B` triple per pixel, ASCII, no header, no position, no exposure
or colour correction) -- computed as `to_lux` of the mean triple rather than the mean of every
per-pixel `to_lux`, which is the same number because `to_lux` is a fixed linear combination of R,
G and B.

**`exposure` is the product of every `EXPOSURE=` line in the header, defaulting to `1.0`.** Radiance
allows more than one `EXPOSURE=` line (successive `pfilt -e`/`pcond` passes each add one), and
their effect on the image is cumulative -- none of this task's four fixtures carries one, so this
path is exercised only by its default.

**Ruling R764: a 180-degree fisheye's statistics are also taken over its image circle**
(`ImageInfo.in_view_statistics`; see `_image_circle_spans` for which pictures and why). The
corners outside the circle are zero on `high_fish_view.hdr` (56 132 of the 56 252 pixels whose
centre lies outside it, 21.5 % of the image; the other 120 run from 8.8 to 24.4 cd/m^2), so the
whole-image mean understated the view by 22 % (67.75 against 86.63 cd/m^2, plan M-B4).
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import IO

from rad_core.glare import (
    EVALGLARE_VIEW_TYPES,
    FISHEYE_ANGLE_DEG,
    VIEW_ANGLE_TOLERANCE_DEG,
    ViewHeader,
)
from rad_core.photometry import to_lux
from rad_core.views import fisheye_row_spans

from .env import RadianceEnv
from .run import run
from .safe_io import open_regular_file

__all__ = [
    "ImageInfo",
    "ImageStatistics",
    "parse_resolution",
    "parse_view_line",
    "read_image",
]

#: `getinfo -d`'s two resolution fields, matched by name (`X`/`Y`) rather than by position -- see
#: the module docstring for why position is unsafe. The sign (`+`/`-`) records scan direction, not
#: anything this module reports, so it is matched but not captured.
_RESOLUTION_FIELD = re.compile(r"[+-]([XY])\s+(\d+)")

#: How many space-separated values follow each `VIEW=` flag `parse_view_line` must step over, so
#: the flags it reads (`vt`, `vh`, `vv`, `vp`, `vd`, and since R771 `vs` and `vl`) stay aligned.
#: `vt` is not listed here: its value is a single letter glued directly onto the
#: flag (`-vth`, never `-vt h`), so it takes no separate token at all.
_VIEW_FLAG_ARG_COUNTS = {
    "vp": 3, "vd": 3, "vu": 3,
    "vh": 1, "vv": 1, "vo": 1, "va": 1, "vs": 1, "vl": 1,
}

#: A header `EXPOSURE=` line, e.g. `EXPOSURE=      0.5`.
_EXPOSURE_LINE = re.compile(r"^EXPOSURE=\s*([-+0-9.eEnNaAiIfF]+)\s*$")

#: `_read_header_block` reads in chunks this size looking for the header/resolution boundary,
#: rather than reading the whole (possibly multi-megabyte, binary) picture into memory for a
#: question only its first few hundred bytes can answer.
_HEADER_READ_CHUNK = 4096

#: Generous headroom over any real Radiance header (this task's four fixtures are all under 300
#: bytes of header) -- a file that has not produced a blank line followed by a resolution string
#: within this many bytes is not a well-formed Radiance picture, and `_read_header_block` gives up
#: rather than reading an unbounded binary payload looking for one.
_HEADER_READ_LIMIT = 1 << 20


@dataclass(frozen=True)
class ImageStatistics:
    """Luminance (cd/m^2) statistics over every pixel of an HDR image. See the module docstring
    for how each field is derived from `pextrem -o` and `pvalue -o`."""

    minimum: float
    maximum: float
    mean: float


@dataclass(frozen=True)
class ImageInfo:
    """What `read_image` reports about one HDR file.

    `warnings` is every stderr line from every spawn `read_image` made (`getinfo`, `getinfo -d`,
    `pextrem -o`, `pvalue`), verbatim and in call order -- never merged into a spawn's own stdout,
    never dropped. See the module docstring.
    """

    resolution: tuple[int, int]
    view: ViewHeader | None
    exposure: float
    statistics: ImageStatistics
    raw_header: tuple[str, ...]
    warnings: tuple[str, ...] = ()
    in_view_statistics: ImageStatistics | None = None
    """Ruling R764: the statistics of the pixels wholly inside the image circle, for a 180 x 180
    degree `h` or `a` fisheye scanned in the standard order; `None` for anything else. Computed in
    the same `pvalue` pass as `statistics.mean`, so `minimum`/`maximum` here carry `pvalue`'s four
    significant figures rather than `pextrem`'s three."""


def parse_resolution(line: str) -> tuple[int, int]:
    """`getinfo -d`'s resolution line -> `(width, height)`.

    Reads the `+X`/`-Y` fields by name via an unanchored search, so a filename prefix or leading
    whitespace ahead of the fields (neither of which this module's own `getinfo` invocation
    produces, but a caller experimenting by hand might feed in) does not defeat it. Raises
    `ValueError` -- never guesses a resolution -- when both fields cannot be named in `line`.
    """
    found = dict(_RESOLUTION_FIELD.findall(line))
    if "X" not in found or "Y" not in found:
        raise ValueError(
            f"could not read a resolution from {line!r}; expected a '+X <n>' and a '-Y <n>' "
            "field, as printed by `getinfo -d`."
        )
    return int(found["X"]), int(found["Y"])


def parse_view_line(line: str) -> ViewHeader | None:
    """One line of a `getinfo` header -> a `ViewHeader`, or `None` if `line` is not a `VIEW=` line.

    An angle (`-vh`/`-vv`) absent from the line is reported as `None` on the matching field, never
    defaulted to any value (180 degrees or otherwise) -- a caller that wants to know whether a
    fisheye's angles were actually stated must be able to tell "absent" from "stated as 180".
    `position`/`direction` follow the same rule: `None` when `-vp`/`-vd` is absent, never a zero
    vector standing in for "not given".
    `-vs`/`-vl` absent are reported as `0.0`, Radiance's own default, because unlike an angle their
    absence has one meaning.
    """
    if not line.startswith("VIEW="):
        return None

    tokens = line[len("VIEW="):].split()
    view_type = ""
    vh_deg: float | None = None
    vv_deg: float | None = None
    position: tuple[float, float, float] | None = None
    direction: tuple[float, float, float] | None = None
    shift = 0.0
    lift = 0.0

    i = 0
    while i < len(tokens):
        token = tokens[i]
        if token.startswith("-vt") and len(token) == 4:
            view_type = token[3]
            i += 1
            continue
        flag = token[1:] if token.startswith("-") else ""
        nargs = _VIEW_FLAG_ARG_COUNTS.get(flag)
        if nargs is None:
            # An unrecognised token (or one of this line's own values, if the tokeniser is ever
            # fed something malformed): skip just this one token rather than guessing its shape.
            i += 1
            continue
        values = tokens[i + 1 : i + 1 + nargs]
        if flag == "vh" and len(values) == 1:
            vh_deg = float(values[0])
        elif flag == "vv" and len(values) == 1:
            vv_deg = float(values[0])
        elif flag == "vp" and len(values) == 3:
            position = (float(values[0]), float(values[1]), float(values[2]))
        elif flag == "vd" and len(values) == 3:
            direction = (float(values[0]), float(values[1]), float(values[2]))
        elif flag == "vs" and len(values) == 1:
            shift = float(values[0])
        elif flag == "vl" and len(values) == 1:
            lift = float(values[0])
        i += 1 + nargs

    return ViewHeader(
        view_type=view_type, vh_deg=vh_deg, vv_deg=vv_deg,
        position=position, direction=direction, shift=shift, lift=lift,
    )


def _read_header_block(fh: IO[bytes]) -> bytes:
    """Read an open HDR file up through its header's terminating blank line and the resolution
    string that follows -- and no further. See the module docstring: `getinfo`/`getinfo -d` read no
    more than this (measured by truncating a fixture at exactly this boundary and confirming both
    invocations print the same thing as against the untruncated file), so a header question never
    has to hold the picture's binary pixel payload in memory.
    """
    buf = b""
    while len(buf) < _HEADER_READ_LIMIT:
        chunk = fh.read(_HEADER_READ_CHUNK)
        if not chunk:
            break
        buf += chunk
        blank = buf.find(b"\n\n")
        if blank == -1:
            continue
        end = buf.find(b"\n", blank + 2)
        if end != -1:
            return buf[: end + 1]
    raise ValueError(
        "could not find the header's terminating blank line and resolution string within "
        f"{_HEADER_READ_LIMIT} bytes; this does not look like a Radiance picture."
    )


def _parse_exposure(raw_header: tuple[str, ...]) -> float:
    """Cumulative `EXPOSURE=` value, `1.0` if the header carries none. See the module docstring."""
    exposure = 1.0
    for line in raw_header:
        match = _EXPOSURE_LINE.match(line.strip())
        if match:
            exposure *= float(match.group(1))
    return exposure


def _extrema_luminance(stdout: str) -> tuple[float, float]:
    """`pextrem -o`'s two lines (`"x y  R G B"`, minimum then maximum) -> `(minimum, maximum)`
    luminance, cd/m^2. See the module docstring for the `to_lux` reuse."""
    lines = [ln for ln in stdout.splitlines() if ln.strip()]
    if len(lines) != 2:
        raise ValueError(
            f"expected `pextrem -o` to print exactly two lines (minimum, maximum); got {stdout!r}"
        )

    def rgb_of(line: str) -> tuple[float, float, float]:
        r, g, b = (float(v) for v in line.split()[-3:])
        return r, g, b

    return to_lux(rgb_of(lines[0])), to_lux(rgb_of(lines[1]))


def _image_circle_spans(
    view: ViewHeader | None, resolution_line: str, width: int, height: int
) -> tuple[tuple[int, int], ...] | None:
    """The image circle's row spans (R764), or `None` when this picture has no circle to mask.

    Only an `h` or `a` view (`rad_core.glare.EVALGLARE_VIEW_TYPES`, the set `image inspect` calls a
    fisheye) at 180 x 180 degrees within `VIEW_ANGLE_TOLERANCE_DEG`: at that angle the inscribed
    circle is the stated field of both projections. Only with no `-vs`/`-vl` shift (R771).
    And only a Y-major scan (`getinfo -d` writing
    `-Y`/`+Y` first, as `rpict` does): `pvalue -d` prints pixels in scan order, so the row/column
    of the n-th line is known only for that order.
    """
    if view is None or view.view_type not in EVALGLARE_VIEW_TYPES:
        return None
    if view.vh_deg is None or view.vv_deg is None:
        return None
    if (abs(view.vh_deg - FISHEYE_ANGLE_DEG) > VIEW_ANGLE_TOLERANCE_DEG
            or abs(view.vv_deg - FISHEYE_ANGLE_DEG) > VIEW_ANGLE_TOLERANCE_DEG):
        return None
    # R771 (owner decision E2): a shifted or lifted view moves the field off the picture's centre
    # (Radiance's `viewray`: `x += hoff - 0.5`, read, not run); `fisheye_row_spans` centres it, so
    # the statistics fall back to the whole image, as for any other view.
    if view.shift != 0.0 or view.lift != 0.0:
        return None
    if not resolution_line.lstrip().startswith(("-Y", "+Y")):
        return None
    return fisheye_row_spans(width, height)


def _pixel_statistics(
    env: RadianceEnv,
    path: Path,
    *,
    width: int,
    spans: tuple[tuple[int, int], ...] | None,
    cwd: Path | str,
    timeout: float | None,
) -> tuple[float, ImageStatistics | None, tuple[str, ...]]:
    """`(whole-image mean, in-circle statistics or None, warnings)` from one `pvalue -d -h -H -o`
    pass (one `R G B` triple per pixel, in scan order, uncorrected).

    The whole-image mean is `to_lux` of the mean triple, as before R764 (the module docstring says
    why that equals the mean of per-pixel luminances). With `spans`, the pixels wholly inside the
    image circle are also summed, and their extrema taken per pixel. A pixel count that is not
    `width x len(spans)` is refused rather than masked against the wrong rows.
    """
    result = run(env, ["pvalue", "-d", "-h", "-H", "-o", str(path)], cwd=cwd, timeout=timeout)
    total_r = total_g = total_b = 0.0
    count = 0
    in_r = in_g = in_b = 0.0
    in_count = 0
    low, high = math.inf, -math.inf
    for line in result.stdout.splitlines():
        if not line.strip():
            continue
        r, g, b = (float(v) for v in line.split())
        total_r += r
        total_g += g
        total_b += b
        if spans is not None:
            row, col = divmod(count, width)
            first, stop = spans[row] if row < len(spans) else (0, 0)
            if first <= col < stop:
                in_r += r
                in_g += g
                in_b += b
                in_count += 1
                luminance = to_lux((r, g, b))
                low = min(low, luminance)
                high = max(high, luminance)
        count += 1
    if count == 0:
        raise ValueError(f"`pvalue` produced no pixel data for {path}")
    if spans is not None and count != width * len(spans):
        raise ValueError(
            f"`pvalue` printed {count} pixels for {path}, not the {width} x {len(spans)} its "
            "header declares, so the image circle cannot be placed."
        )
    mean = to_lux((total_r / count, total_g / count, total_b / count))
    in_view = None
    if spans is not None and in_count:
        in_view = ImageStatistics(
            minimum=low, maximum=high,
            mean=to_lux((in_r / in_count, in_g / in_count, in_b / in_count)),
        )
    return mean, in_view, tuple(result.warnings)


def read_image(
    env: RadianceEnv,
    path: Path | str,
    *,
    cwd: Path | str,
    timeout: float | None = None,
) -> ImageInfo:
    """Read `path`'s resolution, view header, exposure and luminance statistics.

    Refuses a `path` that is not a regular file (see `rad_engine.safe_io.open_regular_file`)
    before any Radiance binary is spawned. See the module docstring for which binary reads which
    field and why `getinfo` is invoked over stdin while `pextrem`/`pvalue` are given the path.
    """
    path = Path(path)
    with open_regular_file(path, "rb") as fh:
        header_bytes = _read_header_block(fh)
    header_text = header_bytes.decode("ascii")

    header_result = run(env, ["getinfo"], cwd=cwd, stdin=header_text, timeout=timeout)
    raw_header = tuple(line for line in header_result.stdout.splitlines() if line.strip())

    view: ViewHeader | None = None
    for line in raw_header:
        parsed = parse_view_line(line)
        if parsed is not None:
            view = parsed
            break

    dim_result = run(env, ["getinfo", "-d"], cwd=cwd, stdin=header_text, timeout=timeout)
    resolution = parse_resolution(dim_result.stdout)

    stats_result = run(env, ["pextrem", "-o", str(path)], cwd=cwd, timeout=timeout)
    minimum, maximum = _extrema_luminance(stats_result.stdout)
    spans = _image_circle_spans(view, dim_result.stdout, *resolution)
    mean, in_view, mean_warnings = _pixel_statistics(
        env, path, width=resolution[0], spans=spans, cwd=cwd, timeout=timeout,
    )

    # Every spawn's stderr, verbatim, in call order -- never merged into its own stdout, never
    # dropped. See the module docstring and `ImageInfo.warnings`.
    warnings = (
        *header_result.warnings,
        *dim_result.warnings,
        *stats_result.warnings,
        *mean_warnings,
    )

    return ImageInfo(
        resolution=resolution,
        view=view,
        exposure=_parse_exposure(raw_header),
        statistics=ImageStatistics(minimum=minimum, maximum=maximum, mean=mean),
        raw_header=raw_header,
        warnings=warnings,
        in_view_statistics=in_view,
    )
