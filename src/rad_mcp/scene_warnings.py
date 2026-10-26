"""Warnings about what a project holds that a run tool does not model (R818, R819).

Both are knowable before anything is spawned, so each run tool computes them above its `dry_run`
branch and a preview carries the same warning the real call does.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from rad_engine import LoadedProject
from rad_engine.octree import read_scene_text
from rad_engine.scene import light_redirecting_modifiers_in
from rad_mcp.envelope import Advisory
from rad_mcp.window_group_tool import _bsdf_input_group

logger = logging.getLogger(__name__)


def light_redirecting_advisory(scene_files: Sequence[str], *, tool: str) -> Advisory | None:
    """`LIGHT_REDIRECTING_MATERIAL` (warn) when the scene files use a `mirror`, `prism1` or
    `prism2` material (`rad_engine.scene.LIGHT_REDIRECTING_TYPES`), directly or through an `alias`,
    or `None`.

    Read by `rad_engine.scene.light_redirecting_modifiers_in`: each file is walked on its own and
    "used" is decided over all of them, because `oconv` compiles them as one scene and a mirror
    defined in one registered file can be used in another; a file that does not parse costs only
    its own primitives. Only the files named are read: a mirror inside an `!xform` include is not
    seen. A file that cannot be opened is skipped here; the real call's `oconv` then refuses the
    run (measured: `oconv` exits 2, "cannot open scene file").
    """
    texts: list[str] = []
    for path in scene_files:
        try:
            texts.append(read_scene_text(Path(path)))
        except (OSError, ValueError) as exc:
            logger.info("light-redirecting scan skipped %s: %s", path, exc)
    found = light_redirecting_modifiers_in(texts)
    if not found:
        return None
    return Advisory(code="LIGHT_REDIRECTING_MATERIAL", severity="warn", field="project", message=(
        f"The scene uses light-redirecting material(s) {sorted(found)} (Radiance mirror, prism1 or "
        f"prism2). Light they redirect, as from a light shelf, a light pipe or a prismatic film, "
        f"can be under-counted by {tool}'s ambient sampling, so the result can read low with "
        "nothing else flagging it."))


def _is_binding(record: Any) -> bool:
    """Whether a `bsdf` input is a window-group binding. `window_group(action="assign_bsdf")`
    records itself in `registered_by`; `bsdf(action="generate")` registers a library BSDF under the
    same kind, bound to nothing, and is not one. A record with no `registered_by` at all cannot say,
    and is counted as a binding (the conservative direction for a warning)."""
    call = record.registered_by
    return call is None or (call.tool == "window_group" and call.action == "assign_bsdf")


def bsdf_not_applied_advisory(loaded: LoadedProject, *, tool: str) -> Advisory | None:
    """`BSDF_NOT_APPLIED` (warn) when the project has any `window_group(action="assign_bsdf")`
    binding (`_is_binding`), or `None`. `tool` traces the scene's own glazing material; only
    `run_annual` reads a bound BSDF."""
    records = [record for record in loaded.manifest.inputs
               if record.kind == "bsdf" and _is_binding(record)]
    if not records:
        return None
    groups = sorted({group for record in records if (group := _bsdf_input_group(record)) is not None})
    unrecorded = sum(1 for record in records if _bsdf_input_group(record) is None)
    named = f"Window group(s) {groups} have a bound BSDF" if groups else "This project has a bound BSDF"
    if groups and unrecorded:
        named += f", and {unrecorded} binding(s) whose window group the manifest does not record"
    elif unrecorded:
        named += f" ({unrecorded} binding(s) whose window group the manifest does not record)"
    return Advisory(code="BSDF_NOT_APPLIED", severity="warn", field="project", message=(
        f"{named}. {tool} traces the glazing material registered in the scene, not the BSDF; a "
        "bound BSDF is used only by run_annual."))
