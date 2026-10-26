"""OBJ text, read the way `obj2rad` reads it -- Phase 10 design §3 and §5.3.

Pure: bytes in, an `ObjModel` or a refusal out. Every rule here exists because `obj2rad` does
something silent or dangerous with the input it guards (design §1.2, measured on Radiance 6.1a):

- A name reaches the first token of a scene line, where `!` starts a shell command, so every
  `usemtl`, `g` and `o` name must be a plain Radiance identifier.
- A NUL ends a name inside obj2rad but not in Python; a line ending in `\\` joins the next line
  (an `f` line disappears); a lone `\\r` is a line break. So NUL and continuation are refused and
  lines split on `\\n`, `\\r\\n` and a lone `\\r`.
- A `g` line with 9 or more names crashes obj2rad (exit -11).
- A bare `usemtl` keeps the previous material while the identifier takes the group, so it is refused.
- Faces before the first `usemtl`, or unnamed faces under a map, are deleted by a generated rule file.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Final, Literal

from rad_core.geometry import GeometryError, Point3
from rad_core.materials import modifier_identifier_problem

__all__ = [
    "OBJ_MAX_BYTES",
    "OBJ_MAX_COMMENT_BYTES",
    "OBJ_MAX_GROUP_NAMES",
    "NonFiniteCoordinatesError",
    "ObjError",
    "ObjFace",
    "ObjFacesWithoutMaterialError",
    "ObjModel",
    "ObjPoint",
    "parse_obj",
]

#: The largest OBJ accepted, in bytes -- 64 MiB. **Still a default rather than a measurement of a
#: real export** (recorded under docs/tool-surface.md `## Unsourced values`), but its *upper* end is
#: now measured: it is the largest synthetic OBJ whose `geometry(action="check")` finishes inside
#: `rad_mcp.geometry_tool._ASSEMBLY_TIMEOUT_S`, the 300 s figure the same tool uses. That figure bounds
#: each Radiance spawn, not the call; the owner ruled against bounding the whole import (R453), so the
#: cap is sized against it as a reference, not as an enforced limit.
#:
#: Phase 10 Task 10, on a 2026 laptop with 8 GiB of RAM. At the previous 128 MiB value a
#: 134 000 073-byte OBJ (1 565 131 quads) checked in **348.97 s**, over that bound, at a maximum
#: resident set size of 2.55 GiB and a peak memory footprint of 10.28 GiB -- and it did **not**
#: time out, because the bound is passed to `rad_engine.convert` and `assemble_scene` and so covers
#: only their subprocesses; `parse_obj`, `_health` and `_checks` are pure Python over the whole
#: soup and run unbounded on the request thread. Halved, a 67 108 808-byte OBJ (797 300 quads)
#: checked in **150.47 s**, max RSS 2.38 GiB, peak footprint 5.34 GiB. So this is the size at which
#: one call still finishes inside that 300 s figure on that machine, rather than a size at which it
#: merely refuses.
#:
#: Too low refuses a legitimate model; too high lets memory and wall time grow past the bound the
#: tool publishes, which is the failure actually measured here.
OBJ_MAX_BYTES: Final[int] = 67_108_864

#: The most names one `g` line may carry. Measured, not chosen, and the count -- not the line
#: length -- is what decides (design §1.2).
#:
#: **Was 8 until the whole-branch review, and 8 is one past what obj2rad survives.** The
#: measurement that set it read the **exit code** and did not read the output. Re-measured
#: independently on obj2rad 6.1a, one `g` line, 3 vertices, 1 face:
#:
#:     n=6  rc=0    # Done processing file: /…/g6.obj
#:     n=7  rc=0    # Done processing file: /…/g7.obj
#:     n=8  rc=0    # Done processing file: 2^?^?j^A        <- raw bytes where the filename goes
#:     n=9  rc=139  (SIGSEGV, no output at all)
#:
#: At exactly 8 obj2rad exits 0 and prints raw memory instead of the filename in its own
#: `# Done processing file:` line -- a read past the end of the group array. Those bytes are
#: written **verbatim into `01_geometry/<id>.rad`**, and a `\n` among them would split the comment
#: and make the tail scene text, which is `rad_engine.octree._refuse_inline_commands`' whole
#: hazard arrived at by accident. The leaked bytes are not stable between runs (`2^?^?j^A` and
#: `2~` on two runs of the same input), so no claim is made here about which bytes they are.
#:
#: Too low refuses a legal `g` line, recoverable by shortening it. Too high writes uninitialised
#: process memory into a scene file this server then stores and compiles.
OBJ_MAX_GROUP_NAMES: Final[int] = 7

#: The longest `#` comment line an OBJ may carry, in bytes. **Sourced, not chosen:**
#: `readobj.c:34,57` (Radiance 6.1a) reads a comment into `char buf[2048]` with `fgets`, and a
#: comment longer than that leaves its tail in the stream where `readobj`'s object loop reads it as
#: **scene text** -- geometry, a material redefinition, or a `!` command. obj2rad echoes a `#`
#: comment through verbatim (measured: never longer out than in; `#  two spaces` came back as
#: `# two spaces`, `#noSpaceHere` unchanged), and `xform -c -s` passes it on intact, so an OBJ
#: comment reaches `oconv` byte for byte.
#:
#: `rad_engine.octree._refuse_inline_commands` refuses the same length at the `oconv` boundary and
#: is the load-bearing guard; this one exists so the OBJ path is **answerable with no Radiance
#: install**, which is the property `_dispatch`'s own comment claims for every other rule in this
#: module. The two values are pinned equal by
#: `tests/test_obj.py::test_the_obj_comment_cap_matches_radiances_own_line_buffer`; they cannot be
#: one symbol because `rad_core` may not import `rad_engine` (the layering proof in CONTEXT.md).
#:
#: Measured over the same `_LINE_BREAK` split the rest of this parser uses, because **obj2rad ends
#: a comment at a lone `\r` too** -- measured: `# headA…\r# tailB…\n` came back as two `#` lines.
#: That is not true of Radiance's own `fgets`, which stops only at `\n`; the difference is safe in
#: this direction, since splitting where obj2rad splits can only over-count lines and so refuse
#: more, never less.
OBJ_MAX_COMMENT_BYTES: Final[int] = 2048

_LINE_BREAK = re.compile(r"\r\n?|\n")


class ObjError(GeometryError):
    """An OBJ refused on its face, before any converter runs."""

    code = "ARGUMENT_OUT_OF_RANGE"
    field = "files"


class ObjFacesWithoutMaterialError(GeometryError):
    """Faces a generated rule file would delete because nothing names them."""

    code = "OBJ_FACES_WITHOUT_MATERIAL"
    field = "files"


class NonFiniteCoordinatesError(GeometryError):
    """A vertex coordinate is NaN or infinite."""

    code = "NON_FINITE_COORDINATES"
    field = "files"


@dataclass(frozen=True)
class ObjFace:
    number: int
    """1-based across the whole file: the `N` obj2rad writes into `<object>.<N>`."""
    material: str | None
    groups: tuple[str, ...]
    obj: str | None
    vertices: tuple[int, ...]
    """Resolved, 1-based vertex indices."""
    line: int

    @property
    def group(self) -> str | None:
        return self.groups[-1] if self.groups else None

    @property
    def has_repeated_vertex(self) -> bool:
        return len(set(self.vertices)) != len(self.vertices)


@dataclass(frozen=True)
class ObjPoint:
    """One vertex of a `p` line: a luminaire marker (spec §6). Never part of any geometry path."""

    material: str | None
    groups: tuple[str, ...]
    obj: str | None
    vertex: int
    line: int


@dataclass(frozen=True)
class ObjModel:
    vertices: tuple[Point3, ...]
    faces: tuple[ObjFace, ...]
    uses_usemtl: bool
    points: tuple[ObjPoint, ...] = ()

    @property
    def qualifier(self) -> Literal["Material", "Group"]:
        return "Material" if self.uses_usemtl else "Group"

    def face_names(self, face: ObjFace) -> tuple[str, ...]:
        """Every name a rule could match `face` by. `("white",)` for an unnamed face with no map."""
        if self.uses_usemtl:
            return (face.material,) if face.material is not None else ()
        return face.groups if face.groups else ("white",)

    def point_names(self, point: ObjPoint) -> tuple[str, ...]:
        """Every name a rule could match `point` by. Same rule as `face_names`."""
        if self.uses_usemtl:
            return (point.material,) if point.material is not None else ()
        return point.groups if point.groups else ("white",)

    @property
    def names(self) -> tuple[str, ...]:
        """Every name some face carries, first appearance first. Empty groups are not names."""
        return tuple(dict.fromkeys(name for face in self.faces for name in self.face_names(face)))

    def face_vertices(self, face: ObjFace) -> tuple[Point3, ...]:
        return tuple(self.vertices[index - 1] for index in face.vertices)


def _shown(token: str) -> str:
    return repr(token if len(token) <= 60 else token[:60] + "...")


def _name(token: str, line: int, keyword: str) -> str:
    problem = modifier_identifier_problem(token)
    if problem is not None:
        raise ObjError(
            f"line {line}: {keyword} name {_shown(token)} is not a usable Radiance identifier: "
            f"{problem}. obj2rad writes this name as the first token of a scene line, where a '!' "
            "runs a shell command and a quote or '#' changes what the line means. Rename it in the "
            "model and export again."
        )
    return token


def _vertex(tokens: list[str], line: int) -> Point3:
    if len(tokens) < 4:
        raise ObjError(f"line {line}: a `v` line needs three coordinates, got {len(tokens) - 1}.")
    try:
        x, y, z = (float(token) for token in tokens[1:4])
    except ValueError:
        raise ObjError(f"line {line}: a `v` coordinate is not a number: {tokens[1:4]!r}.") from None
    if not all(math.isfinite(value) for value in (x, y, z)):
        raise NonFiniteCoordinatesError(
            f"line {line}: vertex ({tokens[1]}, {tokens[2]}, {tokens[3]}) is not finite. obj2rad "
            "writes an overflowing coordinate as inf, and Radiance cannot trace it. Repair the "
            "coordinate and export again."
        )
    return (x, y, z)


def _face(
    tokens: list[str], line: int, number: int, vertex_count: int,
    material: str | None, groups: tuple[str, ...], obj: str | None,
) -> ObjFace:
    entries = tokens[1:]
    if len(entries) < 3:
        raise ObjError(
            f"line {line}: a face needs at least 3 vertices, got {len(entries)}. obj2rad stops with "
            "'Too few vertices'."
        )
    resolved: list[int] = []
    for entry in entries:
        try:
            index = int(entry.split("/")[0])
        except ValueError:
            raise ObjError(f"line {line}: {entry!r} is not a vertex reference.") from None
        position = index if index > 0 else vertex_count + 1 + index
        if index == 0 or not 1 <= position <= vertex_count:
            raise ObjError(
                f"line {line}: vertex reference {index} is out of range ({vertex_count} vertices "
                "defined so far)."
            )
        resolved.append(position)
    return ObjFace(number, material, groups, obj, tuple(resolved), line)


def parse_obj(data: bytes, *, mapped: bool) -> ObjModel:
    """Parse and validate an OBJ. `mapped` says whether a material map will generate rule files."""
    if len(data) > OBJ_MAX_BYTES:
        raise ObjError(
            f"the OBJ is {len(data)} bytes, over the {OBJ_MAX_BYTES}-byte cap (OBJ_MAX_BYTES, a "
            "default recorded under docs/tool-surface.md 'Unsourced values')."
        )
    nul = data.find(b"\0")
    if nul != -1:
        raise ObjError(
            f"the OBJ holds a NUL byte at offset {nul}. obj2rad ends a name at a NUL while this "
            "server would read past it, so the two would disagree about what the file names."
        )
    text = data.decode("utf-8", "surrogateescape")
    vertices: list[Point3] = []
    faces: list[ObjFace] = []
    points: list[ObjPoint] = []
    material: str | None = None
    groups: tuple[str, ...] = ()
    obj: str | None = None
    uses_usemtl = False
    for line, raw in enumerate(_LINE_BREAK.split(text), start=1):
        if raw.endswith("\\"):
            raise ObjError(
                f"line {line} ends with a backslash. obj2rad joins it to the next line, so a face "
                "on that line would silently disappear. Remove the trailing backslash."
            )
        tokens = raw.split()
        if not tokens:
            continue
        if tokens[0].startswith("#"):
            # **A comment is not inert.** obj2rad echoes it verbatim into the `.rad`, `xform` passes
            # it on, and past `readobj`'s 2048-byte `fgets` buffer its tail is read by the object
            # loop as scene text. Measured on this branch, whole-branch review Critical 4: a
            # trailing 2048-byte comment carrying no `!` at all grew a 1 m scene's octree cube to
            # 40 m, and a second one redefined a material's reflectance from 0.5 to 0.02 -- both
            # invisible to `parse_polygons`, so `account`, `_health` and `_checks` all reported the
            # injected primitive as absent while the import registered clean.
            #
            # Bytes, not characters: `readobj`'s buffer is a byte buffer, and counting code points
            # let a 1 152-character, 2 174-byte accented comment through the twin rule in
            # `rad_engine.octree` (`_radiance_byte_length`'s docstring carries that measurement).
            length = len(raw.encode("utf-8", "surrogateescape"))
            if length >= OBJ_MAX_COMMENT_BYTES:
                raise ObjError(
                    f"line {line} is a `#` comment of {length} bytes, at or past Radiance's own "
                    f"{OBJ_MAX_COMMENT_BYTES}-byte comment buffer (OBJ_MAX_COMMENT_BYTES, sourced "
                    "from readobj.c:34,57). obj2rad echoes a comment through verbatim, and a "
                    "comment longer than that buffer leaves its tail in the stream where oconv "
                    "reads it as scene text -- geometry or a material this server would never see. "
                    "Shorten or remove the comment."
                )
            continue
        keyword = tokens[0]
        if keyword == "v":
            vertices.append(_vertex(tokens, line))
        elif keyword == "usemtl":
            if len(tokens) < 2:
                raise ObjError(
                    f"line {line} is a bare `usemtl`. obj2rad keeps the previous material for the "
                    "faces after it while naming them after their group, which silently reassigns "
                    "them. Name the material."
                )
            if len(tokens) > 2:
                raise ObjError(
                    f"line {line}: usemtl name {' '.join(tokens[1:])!r} contains whitespace; a "
                    "Radiance identifier cannot."
                )
            material = _name(tokens[1], line, "usemtl")
            uses_usemtl = True
        elif keyword == "g":
            names = tokens[1:]
            if len(names) > OBJ_MAX_GROUP_NAMES:
                raise ObjError(
                    f"line {line}: a `g` line with {len(names)} names. obj2rad 6.1a writes raw "
                    "memory in place of the filename at exactly 8 names (exit 0, so nothing "
                    "reports it) and segfaults at 9 or more, so at most "
                    f"{OBJ_MAX_GROUP_NAMES} are accepted (OBJ_MAX_GROUP_NAMES). Shorten the `g` "
                    "line."
                )
            groups = tuple(_name(name, line, "g") for name in names)
        elif keyword == "o":
            if len(tokens) > 2:
                raise ObjError(
                    f"line {line}: o name {' '.join(tokens[1:])!r} contains whitespace; a Radiance "
                    "identifier cannot."
                )
            obj = _name(tokens[1], line, "o") if len(tokens) == 2 else None
        elif keyword == "f":
            faces.append(_face(tokens, line, len(faces) + 1, len(vertices), material, groups, obj))
        elif keyword == "p":
            if len(tokens) == 1:
                raise ObjError(f"line {line}: p has no vertex")
            for entry in tokens[1:]:
                # A point is resolved as a degenerate three-entry face (the same entry three times)
                # so that it shares `_face`'s index checks (zero and out of range are refused) and its resolution of
                # a negative index and of a `/vt` suffix.
                [resolved] = _face(
                    ["f", entry, entry, entry], line, 0, len(vertices), material, groups, obj
                ).vertices[:1]
                points.append(ObjPoint(material=material, groups=groups, obj=obj, vertex=resolved, line=line))

    if uses_usemtl:
        unnamed = [face.number for face in faces if face.material is None]
        if unnamed:
            raise ObjFacesWithoutMaterialError(
                f"{len(unnamed)} face(s) come before the first usemtl (face numbers "
                f"{unnamed[:10]}). A generated rule file deletes a face no rule matches. Put a "
                "usemtl before them."
            )
    elif mapped:
        unnamed = [face.number for face in faces if not face.groups]
        if unnamed:
            raise ObjFacesWithoutMaterialError(
                f"{len(unnamed)} face(s) have no usemtl and no group (face numbers {unnamed[:10]}). "
                "Under a material map obj2rad deletes them. Give them a usemtl or a group."
            )
    return ObjModel(tuple(vertices), tuple(faces), uses_usemtl, tuple(points))
