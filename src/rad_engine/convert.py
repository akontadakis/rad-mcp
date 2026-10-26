"""The converters -- `obj2rad`, `robjutil`, `xform` -- for `geometry`'s import (Phase 10 design §5.5).

Every stage is a separate `rad_engine.run` spawn, fed on stdin, never a shell string and never the
caller's path, bounded by the caller's `timeout`, with its stderr kept. Every stage's output passes
the R391 guard before the next stage reads it. `rad_mcp` passes the timeout: this layer does not pick
a number for its callers.
"""

from __future__ import annotations

import logging
import re
import tempfile
import unicodedata
from collections import Counter
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal

from rad_core.geometry import Point3, Polygon
from rad_core.obj import ObjModel
from rad_engine.env import RadianceEnv
from rad_engine.octree import refuse_inline_commands_in_text
from rad_engine.run import RadianceTimeoutError, run
from rad_engine.safe_io import open_regular_file, resolved_parent_within, rmtree_logging_errors
from rad_engine.scene import non_polygon_surfaces, parse_polygons

__all__ = [
    "ConvertedObj", "GeometryConversionError", "ScratchOutsideRootsError", "account", "convert_obj",
    "face_number", "obj2rad_argv", "rules_text", "scale_scene_text", "scratch_directory",
    "transform_points", "triangulate_obj", "xform_argv",
]

_log = logging.getLogger(__name__)

_FACE_NUMBER = re.compile(r"\.(\d+)\Z")

#: The most of a converter's stderr a refusal quotes, in **bytes** of UTF-8 (ruling R760). It was
#: 500 code points, which let a multi-byte stderr carry up to 2000 bytes.
_STDERR_QUOTE_BYTES: Final = 500

#: Unicode categories a quoted stderr loses (R760): controls (C0, C1 and ESC, so no terminal escape
#: sequence survives), format characters (bidirectional overrides, zero-width joiners), surrogates,
#: and the line and paragraph separators. Newline, carriage return and tab become spaces first.
_UNQUOTED_CATEGORIES: Final = frozenset({"Cc", "Cf", "Cs", "Zl", "Zp"})


def _quoted_stderr(stderr: str) -> str:
    """A converter's stderr as a refusal may quote it (ruling R760): stripped, `\\n`, `\\r` and
    `\\t` turned to spaces, every character in `_UNQUOTED_CATEGORIES` removed, then cut to
    `_STDERR_QUOTE_BYTES` bytes of UTF-8 without splitting a character.

    `run(stdin_bytes=...)` has already decoded the bytes with `errors="replace"`, so an invalid
    byte arrives here as U+FFFD and nothing here can raise on it.
    """
    kept = "".join(
        " " if character in "\n\r\t" else character
        for character in stderr.strip()
        if character in "\n\r\t" or unicodedata.category(character) not in _UNQUOTED_CATEGORIES
    ).strip()
    return kept.encode("utf-8")[:_STDERR_QUOTE_BYTES].decode("utf-8", "ignore")


class GeometryConversionError(RuntimeError):
    """A converter stage failed, or its output does not account for every kept face."""

    code = "GEOMETRY_CONVERSION_FAILED"
    severity = "error"
    field = "files"


class ScratchOutsideRootsError(RuntimeError):
    """`scratch_directory`'s parent chain resolves outside every allowed root.

    `PATH_OUTSIDE_ROOT` at `error`, which is the severity its register entry allocates
    (docs/tool-surface.md, Protocol group). `field="project"` because the parent is
    `ProjectPaths.jobs` -- the caller never names it, so the argument that put the scratch directory
    there is the project handle, not a path the caller typed.
    """

    code = "PATH_OUTSIDE_ROOT"
    severity = "error"
    field = "project"


@contextmanager
def scratch_directory(parent: Path, roots: tuple[Path, ...]) -> Iterator[Path]:
    """A private directory for one import's rule file, stage outputs and validation copies (design
    §5.5 step 1). Under `10_jobs/`, which `JobStore.recover` ignores without a job record. The parent
    chain is checked before anything is created; `10_jobs/` is server-owned scratch, so a missing one
    is created then rather than failing the import (ruling R830). Only that last component is
    created (no `parents=True`), inside a parent the check has just resolved within the roots, and
    the chain is checked again before `mkdtemp`, for a link planted at the name in between. The directory is removed in `finally`, and an entry that
    cannot be removed is logged (`rmtree_logging_errors`); nothing else writes into it."""
    if not resolved_parent_within(parent / "probe", roots):
        raise ScratchOutsideRootsError(f"{parent} resolves outside every allowed root; nothing was created.")
    parent.mkdir(exist_ok=True)
    if not resolved_parent_within(parent / "probe", roots):
        raise ScratchOutsideRootsError(f"{parent} resolves outside every allowed root; nothing was created in it.")
    scratch = Path(tempfile.mkdtemp(prefix="geometry-", dir=parent))
    try:
        yield scratch
    finally:
        rmtree_logging_errors(scratch, log=_log)


@dataclass(frozen=True)
class ConvertedObj:
    text: str
    polygons: tuple[Polygon, ...]
    split_faces: tuple[int, ...]


def rules_text(names: Sequence[str], *, qualifier: Literal["Material", "Group"]) -> str:
    """One `name (Qualifier "name") ;` per name -- obj2rad's own map format (man obj2rad). Names are
    identifier-safe already (rad_core.obj), so none can break the rule syntax."""
    return "".join(f'{name} ({qualifier} "{name}") ;\n' for name in names)


def obj2rad_argv(rules_path: Path | None) -> list[str]:
    return ["obj2rad", "-f", *(["-m", str(rules_path)] if rules_path is not None else [])]


def xform_argv(*, factor: float, up_axis: Literal["Z", "Y"]) -> list[str]:
    return ["xform", "-c", "-s", repr(factor), *(["-rx", "90"] if up_axis == "Y" else [])]


def face_number(identifier: str) -> int | None:
    match = _FACE_NUMBER.search(identifier)
    return int(match.group(1)) if match else None


def _stage(
    env: RadianceEnv, argv: list[str], *, data: bytes | None = None, text: str | None = None,
    scratch: Path, timeout: float, label: str,
) -> str:
    """One converter spawn, **fed bytes on every stage**, its output scanned before it is returned.

    **`text=` is re-encoded here rather than handed to `run(stdin=...)`, and that is whole-branch
    review Important 4.** `run`'s text branch passes `input=` under `text=True`, which encodes
    **strict**. `parse_obj` refuses only NUL, so a `0xff` in an OBJ *comment* survives the parse;
    `obj2rad` echoes it; `run(stdin_bytes=...)` decodes stdout with `surrogateescape`, correctly --
    and then this function handed that `str` straight to the next stage's strict encode.

    Measured: `UNCAUGHT: ToolError Error executing tool geometry: 'utf-8' codec can't encode
    characters in position 178-179: surrogates not allowed` -- `is_error`, no `structured_content`,
    no advisory, no code. `UnicodeEncodeError` is a `ValueError` and is not in `geometry`'s `except`
    tuple. The same on the `.rad` path.

    **Not adversarial-only:** a CAD exporter writing `# Exported by Café` in Latin-1 produces it.
    `_decode_scene_bytes`' docstring records this class being fixed on the **read** side; the
    re-encode was left. `surrogateescape` here is the exact inverse of that decode, so the bytes
    the next stage reads are the bytes the previous stage wrote -- catching `UnicodeError` and
    coding it would turn a fixable round trip into a refusal of a legal file.
    """
    payload = data if data is not None else (text or "").encode("utf-8", "surrogateescape")
    try:
        result = run(env, argv, cwd=scratch, stdin_bytes=payload, timeout=timeout, check=False)
    except RadianceTimeoutError as exc:
        raise GeometryConversionError(
            f"{label} ({' '.join(argv)}) did not finish within {timeout:g} s and was stopped. Nothing "
            "has been registered."
        ) from exc
    if result.returncode != 0:
        how = f"was killed by signal {-result.returncode}" if result.returncode < 0 else f"exited {result.returncode}"
        raise GeometryConversionError(
            f"{label} ({' '.join(argv)}) {how}: {_quoted_stderr(result.stderr) or '(no stderr)'}. "
            "Nothing has been registered."
        )
    refuse_inline_commands_in_text(result.stdout, label=scratch / f"{label}.out", cwd=scratch)
    return result.stdout


def account(polygons: Sequence[Polygon], *, model: ObjModel, omitted_faces: frozenset[int]) -> tuple[int, ...]:
    """Match output polygons back to OBJ faces by the `.N` obj2rad writes (design §5.5 step 6).
    Nothing predicts obj2rad's split rule; this only checks that every kept face arrived and nothing
    else did. Returns the faces split into several polygons, excluding faces that repeat a vertex."""
    kept = {face.number for face in model.faces} - omitted_faces
    repeated = {face.number for face in model.faces if face.has_repeated_vertex}
    counts: Counter[int] = Counter()
    for polygon in polygons:
        number = face_number(polygon.id)
        if number is None:
            raise GeometryConversionError(f"output polygon {polygon.id!r} carries no face number.")
        counts[number] += 1
    missing, unknown = sorted(kept - set(counts)), sorted(set(counts) - kept)
    if missing or unknown:
        raise GeometryConversionError(
            f"the converted scene does not account for the OBJ: missing face(s) {missing[:20]}, "
            f"unknown face(s) {unknown[:20]}. Nothing has been registered."
        )
    return tuple(sorted(n for n, count in counts.items() if count > 1 and n not in repeated))


def convert_obj(
    env: RadianceEnv, data: bytes, *, model: ObjModel, rules: str | None, omitted_faces: frozenset[int],
    factor: float, up_axis: Literal["Z", "Y"], scratch: Path, timeout: float,
) -> ConvertedObj:
    rules_path = None
    if rules is not None:
        rules_path = scratch / "rules.map"
        with open_regular_file(rules_path, "wb") as handle:
            handle.write(rules.encode("ascii"))
    converted = _stage(env, obj2rad_argv(rules_path), data=data, scratch=scratch, timeout=timeout, label="obj2rad")
    scaled = _stage(env, xform_argv(factor=factor, up_axis=up_axis), text=converted, scratch=scratch, timeout=timeout, label="xform")
    try:
        polygons = parse_polygons(scaled)
    except ValueError as exc:
        raise GeometryConversionError(f"the converted scene could not be read back: {exc}") from exc
    return ConvertedObj(scaled, polygons, account(polygons, model=model, omitted_faces=omitted_faces))


def scale_scene_text(env: RadianceEnv, text: str, *, factor: float, scratch: Path, timeout: float) -> str:
    """A flattened `.rad` scaled by `factor`, commands kept unexpanded and then refused by the guard."""
    return _stage(env, ["xform", "-c", "-s", repr(factor)], text=text, scratch=scratch, timeout=timeout, label="xform")


def triangulate_obj(env: RadianceEnv, data: bytes, *, scratch: Path, timeout: float) -> bytes:
    """`robjutil +T` on the OBJ bytes; its OBJ output must be parsed again by `rad_core.obj`."""
    out = _stage(env, ["robjutil", "+T"], data=data, scratch=scratch, timeout=timeout, label="robjutil")
    return out.encode("utf-8", "surrogateescape")


def transform_points(
    env: RadianceEnv, points: Sequence[Point3], *, factor: float, up_axis: Literal["Z", "Y"],
    scratch: Path, timeout: float,
) -> list[Point3]:
    """`points` through the same `xform` argv a geometry import used (R477), as degenerate polygons.

    The conversion is never re-spelled in Python: a record's scale and up-axis change is whatever
    `xform_argv` builds, and this function feeds the real binary. Read back through `parse_polygons`
    (R516) -- the one walker `scene.py` already provides for every other caller in this file (R30) --
    rather than a second hand-rolled primitive parser with its own error handling to keep in sync.
    Each point is the first vertex of its polygon; the other two are the same point, repeated."""
    if not points:
        return []
    text = "".join(
        f"void polygon p{index}\n0\n0\n9 {x!r} {y!r} {z!r} {x!r} {y!r} {z!r} {x!r} {y!r} {z!r}\n"
        for index, (x, y, z) in enumerate(points)
    )
    out = _stage(
        env, xform_argv(factor=factor, up_axis=up_axis), text=text, scratch=scratch, timeout=timeout,
        label="luminaire positions",
    )
    try:
        converted = [polygon.vertices[0] for polygon in parse_polygons(out)]
        others = non_polygon_surfaces(out)
    except (ValueError, IndexError) as exc:
        raise GeometryConversionError(f"the converted luminaire positions could not be read back: {exc}") from exc
    if others:
        raise GeometryConversionError(
            f"xform returned surface type(s) {sorted(others)!r} among the positions; only polygons were sent."
        )
    if len(converted) != len(points):
        raise GeometryConversionError(f"xform returned {len(converted)} positions for {len(points)} inputs.")
    return converted
