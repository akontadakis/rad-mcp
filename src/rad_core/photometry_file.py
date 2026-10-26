"""LM-63 photometry files, read the way `ies2rad` reads them, and refused before any spawn.

Pure (CONTEXT.md). Spec: docs/superpowers/specs/2026-09-13-phase-11a-luminaire-placement-design.md
§1.3, rulings R471, R472, R490, R491.

**Why a screen at all.** `ies2rad` executes the path on a `[LUMINOUSGEOMETRY]` line through `system()`
(spec §1.3, measured with a canary file), applies header field 2 as a factor for some keywords and not
others, and mirrors type C files whose horizontal angles start at 90. None of that is visible in its
exit status. So every byte of a caller's file is read here first, and a file this module refuses never
reaches the binary.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Final, Literal, Sequence, get_args

from rad_core.geometry import Point3
from rad_core.photometry import CIE_PHOTOPIC_WEIGHTS

__all__ = [
    "FLAT_SHAPES",
    "IES_MAX_BYTES",
    "TYPES_PREFIX",
    "ConvertedType",
    "Format",
    "IesHeader",
    "IesHeaderError",
    "IesUnsupportedError",
    "ShapeClass",
    "c0_lobe_ies",
    "isotropic_point_ies",
    "normalised_colour",
    "read_converted",
    "screen_ies",
    "surface_centres",
]

#: Cap on a caller's IES file. Unsourced (docs/tool-surface.md `## Unsourced values`): the two fixtures
#: are 543 B and 16 KB; an LM-63 file with 361 x 181 candela values at 8 bytes each is about 0.5 MB.
IES_MAX_BYTES: Final[int] = 4_194_304

#: The `-p` prefix every type is converted under, relative to the project root (R473, R483).
TYPES_PREFIX: Final[str] = "12_luminaires/types"

Format = Literal["LM-63-1986", "LM-63-1991", "LM-63-1995", "LM-63-2002", "LM-63-2019"]
_FORMATS: Final[tuple[str, ...]] = get_args(Format)

ShapeClass = Literal["point", "rectangle", "disk", "box", "cylinder"]
FLAT_SHAPES: Final[frozenset[str]] = frozenset({"point", "rectangle", "disk"})
_SURFACE_TYPES: Final = frozenset({"polygon", "ring", "sphere", "cylinder"})

#: The exact first lines `ies2rad` recognises, after trailing spaces and tabs are stripped (§1.3).
_KEYWORDS: Final[dict[bytes, Format]] = {
    b"IESNA91": "LM-63-1991",
    b"IESNA:LM-63-1995": "LM-63-1995",
    b"IESNA:LM-63-2002": "LM-63-2002",
    b"IES:LM-63-2019": "LM-63-2019",
}
_FIELD2_MEANING: Final[dict[Format, str]] = {
    "LM-63-1986": "factor",
    "LM-63-1991": "factor",
    "LM-63-1995": "reserved",
    "LM-63-2002": "reserved",
    "LM-63-2019": "file_generation_type",
}
_METRES_PER_UNITS_TYPE: Final[dict[int, float]] = {1: 0.3048, 2: 1.0}
_TYPE_NAMES: Final[dict[int, str]] = {1: "C", 2: "B", 3: "A"}


class IesHeaderError(ValueError):
    code = "IES_HEADER_INVALID"
    severity = "error"
    field = "ies"


class IesUnsupportedError(ValueError):
    code = "IES_UNSUPPORTED"
    severity = "error"
    field = "ies"


@dataclass(frozen=True)
class IesHeader:
    format: Format
    tilt: Literal["NONE", "INCLUDE"]
    lamps: int
    lumens_per_lamp: float | None
    candela_multiplier: float
    vertical_angles: int
    horizontal_angles: int
    photometric_type: int
    units_type: int
    width_m: float
    length_m: float
    height_m: float
    ballast_factor: float
    field2: float
    field2_meaning: Literal["factor", "reserved", "file_generation_type"]
    watts: float | None
    first_horizontal_angle: float

    def __post_init__(self) -> None:
        """Every field of the type `screen_ies` gives it, or `ValueError` naming the field (L-53): a
        header rebuilt from a hand-edited manifest record (`lit_scene._header`) is refused there as
        `LUMINAIRE_FILES_CHANGED`, never carried into a run. An `int` field takes an `int`, never a
        `bool`; a number field an `int` or `float`, finite, never a `bool` or a string; `lumens_per_lamp`
        and `watts` may also be `None`; a literal field one of its values."""
        literals = {"format": _FORMATS, "tilt": ("NONE", "INCLUDE"),
                    "field2_meaning": ("factor", "reserved", "file_generation_type")}
        for key, allowed in literals.items():
            value = getattr(self, key)
            if not isinstance(value, str) or value not in allowed:
                raise ValueError(f"IES header field {key!r} is {value!r}, not one of {list(allowed)}")
        for key in ("lamps", "vertical_angles", "horizontal_angles", "photometric_type", "units_type"):
            value = getattr(self, key)
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(f"IES header field {key!r} is {value!r}, not a whole number")
        for key in ("lumens_per_lamp", "candela_multiplier", "width_m", "length_m", "height_m",
                    "ballast_factor", "field2", "watts", "first_horizontal_angle"):
            value = getattr(self, key)
            if value is None and key in ("lumens_per_lamp", "watts"):
                continue
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError(f"IES header field {key!r} is {value!r}, not a finite number")

    @property
    def photometry(self) -> Literal["relative", "absolute"]:
        return "absolute" if self.lumens_per_lamp is None else "relative"

    @property
    def applied_factors(self) -> tuple[tuple[str, float], ...]:
        """The header factors other than 1 that `ies2rad` multiplies into its output (§1.3)."""
        applied: list[tuple[str, float]] = []
        if self.ballast_factor != 1.0:
            applied.append(("ballast_factor", self.ballast_factor))
        if self.field2_meaning == "factor" and self.field2 != 1.0:
            applied.append(("ballast_lamp_factor", self.field2))
        return tuple(applied)


def _number(token: bytes, what: str) -> float:
    try:
        value = float(token)
    except ValueError:
        raise IesHeaderError(f"{what} is {token[:40]!r}, not a number.") from None
    if not math.isfinite(value):
        raise IesHeaderError(f"{what} is {token[:40]!r}, not a finite number.")
    return value


def _whole_number(token: bytes, what: str, *, expected: str = "a whole number") -> int:
    """R509: refuse a type field that is not a whole number rather than truncating it.

    The reference `ies2rad` reading this module documents applies `int()` to the photometric and
    units type tokens, so a caller-supplied `1.5` would silently become `1`. That is a header this
    module should refuse, not one it should quietly reinterpret -- so this is a deliberate departure
    from the brief's own reference implementation for those two fields. `expected` is the phrase the
    refusal ends with, so `_count` keeps its own wording.
    """
    value = _number(token, what)
    if value != int(value):
        raise IesHeaderError(f"{what} is {token[:40]!r}, not {expected}.")
    return int(value)


def _count(token: bytes, what: str) -> int:
    expected = "a whole number of at least 1"
    value = _whole_number(token, what, expected=expected)
    if value < 1:
        raise IesHeaderError(f"{what} is {token[:40]!r}, not {expected}.")
    return value


def screen_ies(data: bytes) -> IesHeader:
    """Read an LM-63 file's header the way `ies2rad` 6.1a does, refusing what rad_mcp will not convert.

    Raises `IesHeaderError` (unparseable, BOM, over the cap) or `IesUnsupportedError` (luminous
    geometry, `TILT=<file>`, type A or B, type C not starting at horizontal angle 0 or not ending at
    0, 90, 180 or 360 -- R590).
    """
    if len(data) > IES_MAX_BYTES:
        raise IesHeaderError(f"the IES file is {len(data)} bytes, larger than the {IES_MAX_BYTES}-byte cap.")
    if data.startswith(b"\xef\xbb\xbf"):
        raise IesHeaderError(
            "the IES file starts with a UTF-8 byte-order mark; ies2rad would read its keyword line as "
            "LM-63-1986 and apply header field 2 as a factor. Save it without a BOM."
        )
    if b"luminousgeometry" in data.lower():
        raise IesUnsupportedError(
            "the file names a luminous geometry file ([LUMINOUSGEOMETRY]); ies2rad passes that path to a "
            "shell, so rad_mcp refuses it before any spawn (R490)."
        )
    lines = data.replace(b"\r\n", b"\n").replace(b"\r", b"\n").split(b"\n")
    if not lines or not lines[0].strip():
        raise IesHeaderError("the IES file is empty.")
    fmt: Format = _KEYWORDS.get(lines[0].rstrip(b" \t"), "LM-63-1986")
    tilt_index = next((i for i, line in enumerate(lines) if line.startswith(b"TILT=")), None)
    if tilt_index is None:
        if any(line.lower().startswith(b"tilt=") for line in lines):
            raise IesHeaderError("the TILT= line must be upper case; ies2rad does not read lower-case tilt=.")
        raise IesHeaderError("the IES file has no TILT= line.")
    tilt_value = lines[tilt_index][len(b"TILT="):].strip()
    tokens = b" ".join(lines[tilt_index + 1:]).split()
    if tilt_value == b"NONE":
        tilt: Literal["NONE", "INCLUDE"] = "NONE"
    elif tilt_value == b"INCLUDE":
        tilt = "INCLUDE"
        if len(tokens) < 2:
            raise IesHeaderError("TILT=INCLUDE has no tilt block.")
        pairs = _count(tokens[1], "the TILT=INCLUDE angle count")
        tokens = tokens[2 + 2 * pairs:]
    else:
        raise IesUnsupportedError(f"TILT=<file> ({tilt_value[:40]!r}) names an external tilt file, which is not supported.")
    if len(tokens) < 13:
        raise IesHeaderError(f"the numeric block after TILT= has {len(tokens)} tokens; it needs at least 13.")
    lamps = _count(tokens[0], "the lamp count")
    lumens = _number(tokens[1], "lumens per lamp")
    multiplier = _number(tokens[2], "the candela multiplier")
    n_vertical = _count(tokens[3], "the vertical angle count")
    n_horizontal = _count(tokens[4], "the horizontal angle count")
    # R509: refuse a non-integer photometric or units type rather than truncating it (see
    # `_whole_number`'s docstring for why this departs from the brief's `int(_number(...))`).
    photometric_type = _whole_number(tokens[5], "the photometric type")
    units_type = _whole_number(tokens[6], "the units type")
    if photometric_type not in _TYPE_NAMES:
        raise IesHeaderError(f"photometric type {tokens[5]!r} is not 1, 2 or 3.")
    if units_type not in _METRES_PER_UNITS_TYPE:
        raise IesHeaderError(f"units type {tokens[6]!r} is not 1 (feet) or 2 (metres).")
    scale = _METRES_PER_UNITS_TYPE[units_type]
    width, length, height = (_number(tokens[i], name) * scale for i, name in ((7, "width"), (8, "length"), (9, "height")))
    ballast, field2, watts = (_number(tokens[i], name) for i, name in ((10, "the ballast factor"), (11, "field 2"), (12, "input watts")))
    needed = 13 + n_vertical + n_horizontal + n_vertical * n_horizontal
    if len(tokens) < needed:
        raise IesHeaderError(f"the file declares {n_vertical} x {n_horizontal} angles and holds {len(tokens) - 13} values after the header; it needs {needed - 13}.")
    if photometric_type != 1:
        raise IesUnsupportedError(f"photometric type {_TYPE_NAMES[photometric_type]} is not supported; only type C files are.")
    first_horizontal = _number(tokens[13 + n_vertical], "the first horizontal angle")
    if first_horizontal != 0.0:
        raise IesUnsupportedError(
            f"horizontal angles start at {first_horizontal:g}, not 0; ies2rad mirrors such type C files (R491)."
        )
    # R590 (whole-branch fix wave, Radiance M4): LM-63 type C horizontal angles end at a symmetry plane
    # -- 0 (one plane, rotationally symmetric), 90, 180 or 360. A file ending anywhere else (270, say)
    # converts, but after `-rz -90` its C0 lobe reads 0 in every direction (measured): a silent zero.
    last_horizontal = _number(tokens[13 + n_vertical + n_horizontal - 1], "the last horizontal angle")
    if last_horizontal not in (0.0, 90.0, 180.0, 360.0):
        raise IesUnsupportedError(
            f"horizontal angles end at {last_horizontal:g}, not 0, 90, 180 or 360; a type C file must end at a "
            "symmetry plane, and ies2rad's conversion of one that does not loses light under rotation (R590)."
        )
    return IesHeader(
        format=fmt, tilt=tilt, lamps=lamps, lumens_per_lamp=None if lumens == -1 else lumens,
        candela_multiplier=multiplier, vertical_angles=n_vertical, horizontal_angles=n_horizontal,
        photometric_type=photometric_type, units_type=units_type, width_m=width, length_m=length,
        height_m=height, ballast_factor=ballast, field2=field2,
        field2_meaning=_FIELD2_MEANING[fmt], watts=watts if watts > 0 else None,  # type: ignore[arg-type]
        first_horizontal_angle=first_horizontal,
    )


def normalised_colour(colour: Sequence[float]) -> tuple[float, float, float]:
    """`colour` scaled to photopic weight 1, so `ies2rad -c` changes hue and never brightness (R470)."""
    if len(colour) != 3 or any(isinstance(c, bool) or not isinstance(c, (int, float)) for c in colour):
        raise ValueError(f"colour must be three numbers, got {colour!r}.")
    if any(not math.isfinite(c) or c < 0 for c in colour):
        raise ValueError(f"colour components must be finite and >= 0, got {colour!r}.")
    weight = sum(w * c for w, c in zip(CIE_PHOTOPIC_WEIGHTS, colour))
    if weight <= 0:
        raise ValueError(f"colour {colour!r} has no photopic weight.")
    r, g, b = (c / weight for c in colour)
    return (r, g, b)


def isotropic_point_ies(candela: float) -> bytes:
    """A type C point source of `candela` in every direction (R486's first probe)."""
    return "\r\n".join([
        "IESNA:LM-63-2002", "TILT=NONE",
        "1 1000 1 3 1 1 2 0 0 0", "1.0 1.0 10",
        "0 90 180", "0",
        f"{candela:g} {candela:g} {candela:g}", "",
    ]).encode("ascii")


def c0_lobe_ies(candela: float) -> bytes:
    """`candela` only at V = 45 deg on the C0 half-plane, zero elsewhere (R486's second probe)."""
    zero, lobe = "0 0 0", f"0 {candela:g} 0"
    return "\r\n".join([
        "IESNA:LM-63-2002", "TILT=NONE",
        "1 1000 1 3 5 1 2 0 0 0", "1.0 1.0 10",
        "0 45 90", "0 90 180 270 360",
        lobe, zero, zero, zero, lobe, "",
    ]).encode("ascii")


@dataclass(frozen=True)
class _Primitive:
    modifier: str
    kind: str
    identifier: str
    strings: tuple[str, ...]
    reals: tuple[float, ...]


def _unquote(word: str) -> str:
    """One whitespace-delimited word as Radiance's `fgetword` stores it: a quoted section opens at a
    `'` or `"` anywhere in the word and closes at the same character, and the quote characters are not
    stored, so `'f1''light'` is `f1light` and `"a"b` is `ab`. `_primitives` has already refused a word
    with an odd count of either quote character."""
    stored: list[str] = []
    quote: str | None = None
    for char in word:
        if quote is None and char in "'\"":
            quote = char
        elif char == quote:
            quote = None
        else:
            stored.append(char)
    return "".join(stored)


def _primitives(text: str) -> list[_Primitive]:
    """The Radiance primitives of `ies2rad`'s own output, split on whitespace.

    It reads that output only. A quoted string containing a space would split into two words and
    shift every count after it, which can still read as a valid file (the displaced words line up
    with an integer), so a word with an unbalanced quote, anywhere in it, is refused on its face.
    The integer-argument block is skipped by its count, not validated: a malformed one is
    refused later, by `oconv`, as `IES_CONVERSION_FAILED`. `ies2rad`'s own output names its files
    from a name `rad_mcp.names` allows, which has no space, so the refusal is a guard and not a case.
    """
    words = [w for line in text.splitlines() if not line.lstrip().startswith("#") for w in line.split()]
    for word in words:
        for quote in ("'", '"'):
            if word.count(quote) % 2:
                raise IesUnsupportedError(
                    f"the converted file has a word with an unbalanced quote ({word[:40]!r}), which this "
                    "reader cannot split correctly.")
    out: list[_Primitive] = []
    i = 0
    try:
        while i < len(words):
            modifier, kind, identifier = (_unquote(w) for w in words[i:i + 3])
            n1 = int(words[i + 3])
            strings = tuple(_unquote(w) for w in words[i + 4:i + 4 + n1])
            j = i + 4 + n1
            n2 = int(words[j])
            j += 1 + n2
            n3 = int(words[j])
            reals = tuple(float(w) for w in words[j + 1:j + 1 + n3])
            if len(reals) != n3 or len(strings) != n1:
                raise ValueError
            out.append(_Primitive(modifier, kind, identifier, strings, reals))
            i = j + 1 + n3
    except (ValueError, IndexError):
        raise IesUnsupportedError("the converted file is not a sequence of Radiance primitives.") from None
    return out


def _extent(p: _Primitive) -> tuple[Point3, Point3]:
    r = p.reals
    if p.kind == "polygon":
        xs, ys, zs = r[0::3], r[1::3], r[2::3]
        return (min(xs), min(ys), min(zs)), (max(xs), max(ys), max(zs))
    if p.kind == "sphere":
        return (r[0] - r[3], r[1] - r[3], r[2] - r[3]), (r[0] + r[3], r[1] + r[3], r[2] + r[3])
    if p.kind == "ring":
        centre, normal, radius = r[0:3], r[3:6], r[7]
        length = math.sqrt(sum(n * n for n in normal)) or 1.0
        half = tuple(radius * math.sqrt(max(0.0, 1.0 - (n / length) ** 2)) for n in normal)
        return tuple(c - h for c, h in zip(centre, half)), tuple(c + h for c, h in zip(centre, half))  # type: ignore[return-value]
    p0, p1, radius = r[0:3], r[3:6], r[6]
    axis = tuple(b - a for a, b in zip(p0, p1))
    length = math.sqrt(sum(a * a for a in axis)) or 1.0
    half = tuple(radius * math.sqrt(max(0.0, 1.0 - (a / length) ** 2)) for a in axis)
    lows = tuple(min(a, b) - h for a, b, h in zip(p0, p1, half))
    highs = tuple(max(a, b) + h for a, b, h in zip(p0, p1, half))
    return lows, highs  # type: ignore[return-value]


@dataclass(frozen=True)
class ConvertedType:
    shape_class: ShapeClass
    emitter_min: Point3
    emitter_max: Point3
    data_files: tuple[str, ...]
    modifiers: tuple[str, ...]

    @property
    def centre(self) -> Point3:
        return tuple((a + b) / 2 for a, b in zip(self.emitter_min, self.emitter_max))  # type: ignore[return-value]


def read_converted(text: str, *, prefix: str) -> ConvertedType:
    """The shape class, unrotated emitter extent, data files and modifier names of an `ies2rad` output (R488, Amendment 1)."""
    primitives = _primitives(text)
    surfaces = [p for p in primitives if p.kind in _SURFACE_TYPES]
    counts = {kind: sum(1 for p in surfaces if p.kind == kind) for kind in _SURFACE_TYPES}
    if counts == {"polygon": 0, "ring": 0, "sphere": 1, "cylinder": 0}:
        shape: ShapeClass = "point"
    elif counts["polygon"] in (1, 2) and counts["ring"] == counts["sphere"] == counts["cylinder"] == 0:
        shape = "rectangle"
    elif counts["polygon"] == 6 and counts["ring"] == counts["sphere"] == counts["cylinder"] == 0:
        shape = "box"
    elif counts["ring"] == 2 and counts["cylinder"] == 0 and counts["polygon"] == counts["sphere"] == 0:
        shape = "disk"
    elif counts["ring"] == 2 and counts["cylinder"] == 1 and counts["polygon"] == counts["sphere"] == 0:
        shape = "cylinder"
    else:
        raise IesUnsupportedError(f"unrecognised emitter set {counts} in the converted file.")
    if any(p.kind not in _SURFACE_TYPES | {"brightdata", "light"} for p in primitives):
        raise IesUnsupportedError("unrecognised emitter primitive in the converted file.")
    lows, highs = zip(*(_extent(p) for p in surfaces))
    emitter_min = tuple(min(v[i] for v in lows) for i in range(3))
    emitter_max = tuple(max(v[i] for v in highs) for i in range(3))
    data_files = tuple(sorted({s for p in primitives if p.kind == "brightdata" for s in p.strings if s.startswith(prefix + "/")}))
    modifiers = tuple(p.identifier for p in primitives if p.kind not in _SURFACE_TYPES)
    return ConvertedType(shape, emitter_min, emitter_max, data_files, modifiers)  # type: ignore[arg-type]


def surface_centres(text: str, *, suffix: str) -> list[tuple[str, Point3]]:
    """`(identifier, centre)` of every polygon, ring or sphere whose identifier ends with `suffix`, in file order."""
    found: list[tuple[str, Point3]] = []
    for p in _primitives(text):
        if not p.identifier.endswith(suffix) or p.kind not in ("polygon", "ring", "sphere"):
            continue
        if p.kind == "polygon":
            n = len(p.reals) // 3
            centre = tuple(sum(p.reals[k::3]) / n for k in range(3))
        else:
            centre = tuple(p.reals[0:3])
        found.append((p.identifier, centre))  # type: ignore[arg-type]
    return found
