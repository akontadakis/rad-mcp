"""The one check every tool's `inline_timeout_s` goes through (Phase 13 Branch C, ruling R704).

**The boundary, measured on this machine** (`.venv/bin/python`, CPython 3.11.12, macOS/arm64),
by driving `subprocess.run([sys.executable, "-c", "import time; time.sleep(0.2)"], timeout=t,
check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)`. Piping a stream is what makes this
reproduce the real code path rather than an artefact of the probe. A bare
`subprocess.run(..., timeout=t)` with **no** piped stream does *not* reproduce this on this
machine: CPython's `Popen.communicate()` only enters the `selectors`-based wait loop when at least
one stream is registered, and skips straight to the pure-Python polling `Popen._wait` otherwise
(`Lib/subprocess.py`), which never converts `timeout` to anything -- every value up to and
including `nan`/`inf` returns "ok" there. Every spawn in `rad_engine.run` pipes at least stdin and
stderr (its to-file spawn sends stdout to a file; the others pipe stdout too), so it enters the
selector-based wait, and the piped-probe numbers below are the ones that apply::

    t                    outcome
    -----------------    -------------------------------------------------------------------
    2147483.0            ok
    2147483.647          ok
    2147483.648          OverflowError: timeout is too large
    2147484.0            OverflowError: timeout is too large
    1e9                  OverflowError: timeout is too large
    1e18                 OverflowError: timestamp too large to convert to C _PyTime_t
    1e300                OverflowError: timestamp too large to convert to C _PyTime_t
    inf                  OverflowError: cannot convert float infinity to integer
    nan                  ValueError: cannot convert float NaN to integer

2147483.647 s is 2**31 - 1 milliseconds: the boundary is the C `int` millisecond conversion
`selectors`'s poll/kqueue backends apply once a real wait is entered. 2147483.648 already
overflows it, so the ceiling below is the largest value actually measured `ok`, not a rounded
guess.

**Per-tool measurement, through the MCP layer, `dry_run=True`, before this module existed**
(`nan`, `inf`, `1e9`, `0`, `-1`, JSON `true`):

- `run_point_in_time` (and `run_electric`/`run_ugr`, which delegate to its validator): `0` and
  `-1` refused coded (`ARGUMENT_OUT_OF_RANGE`, `field="inline_timeout_s"`); `nan`, `inf` and `1e9`
  all passed validation unrefused (the old `<= 0` test lets every non-finite and every
  large-but-positive value through).
- `run_view_and_sunlight`: `0`, `-1`, `nan` and `inf` refused coded (its `math.isfinite(...) and
  ... > 0` test already caught non-finite values); `1e9` passed unrefused -- no ceiling existed.
- `run_render` and `run_glare`: **no clause examined `inline_timeout_s` at all.** On a project
  with no window group registered, every one of the six values, including `0` and `-1`, reached a
  clean `dry_run` success on both tools. **`run_glare` is the sharper case, and it is not merely
  "ran clean": on a project that DOES carry a registered window group** (`_resolve_dgp_scope`'s
  aperture read, which runs above `dry_run`), a real `1e9` reaches `rad_engine.run.run`'s
  `subprocess.run(..., timeout=1e9)` spawn *during* `dry_run=True` and raises an uncaught
  `OverflowError: timeout is too large` straight out of `run_glare` -- a check-0.4 crash, not a
  refusal at all, confirmed by running `tests/test_glare_tool.py::TestTheInlineTimeoutIsChecked`
  against the code before this module existed (`office`-only project measurements miss this
  because they carry no window group, so `_resolve_dgp_scope` never spawns `xform` at all).
  `run_render` cannot be driven to the same crash under `dry_run`: nothing in its own pre-`dry_run`
  path spawns a process, so a bad `inline_timeout_s` there was only ever reachable on a live
  (`dry_run=False`) call.

**`run_view_and_sunlight`'s own `dry_run` was not clean either before this module existed, on a
project with a real window group -- re-measured against a copy of that code with
`view_out_roots`'s fixture** (`tests/test_view_sunlight_tool.py::office`, which registers one):
`inline_timeout_s=1e9`, `dry_run=True` logged `run_view_and_sunlight failed in arithmetic:
OverflowError('timeout is too large')` and returned `refused: true` with **no advisory** -- a
code-less refusal, from the window groups' `xform` read, which this tool's own `dry_run` preview
already performs (its docstring: *"the preview opens the project, validates the named grid and
reads the window groups -- and reading a group's aperture shells out to `xform`"*). **So the gap
this module closes was never "past `dry_run`, at a live spawn only"**; the `office`-only
measurement in the bullet above under-reports it for exactly the reason the `run_glare` finding
does. `run_point_in_time`/`run_electric`/`run_ugr` are different: nothing before their own
`dry_run` return touches `inline_timeout_s` at all (point_in_time_tool's own KNOWN LIMITATION
note), so a code-less refusal or a crash on those three, before this module existed, was
reachable only on `dry_run=False`.

**Since R750 (Phase 17) no inline spawn in `rad_engine.run` or `rad_engine.postprocess` calls
`subprocess.run`**: they spawn through `rad_engine.process_group.run_in_own_session`, which waits
with the same `Popen.communicate(timeout=...)` call, so the boundary is the same code path.
`tests/test_process_group.py::TestTheTimeoutBoundaryIsUnchanged` re-measures the two rows that
bound the ceiling (`2147483.647` ok, `2147483.648` `OverflowError`) through the helper. `bsdf`
(`action="generate"`, R751) uses this check too; its booleans were not measured over the wire.

**On that live (`dry_run=False`) path, `run_point_in_time`'s outcome for `inf`/`1e9` and for `nan`
differed before this module existed, and the difference matters.** Both raise at the same
`subprocess.run(..., timeout=...)` spawn, but as different exception types (the boundary table
above): `inf` and `1e9` raise `OverflowError`, a subclass of `ArithmeticError`, which
`run_point_in_time`'s own outer handler catches (`except ArithmeticError as exc:`,
`point_in_time_tool.py`) and turns into a **code-less refusal**. `nan` raises `ValueError`, which
no clause in `run_point_in_time`'s handler chain catches (measured), so `nan` was a raw, uncaught
crash, not a refusal of any kind. `electric_tool`/`ugr_tool` inherit the same shape (each has its
own, identical `except ArithmeticError` clause). `render_tool` and `glare_tool` catch neither:
read off the code, `render_tool.py` has one narrow `except (ValueError, ArithmeticError)` around
`resolve_quality` only (not around any spawn), and `glare_tool.py` catches no `ArithmeticError`
anywhere -- so on those two tools every one of `nan`/`inf`/`1e9` reaching a live spawn, before this
module existed, was a raw crash, and remains one on any future platform whose real ceiling is lower
than this module's (see the closing paragraph below).

**Booleans never reach this check over MCP** (R854). `inline_timeout_s` is `StrictFloat` in every
tool's signature, so the MCP SDK's own argument model refuses a JSON `true`/`false`, naming the
argument, **before any tool code runs**; a JSON integer still arrives as a number. (Until R854 the
argument was a lax `float`, and the SDK coerced `true`/`false` to `1.0`/`0.0`, so `true` passed this
check as a 1-second bound.) `point_in_time_tool._validate_arguments`'s own
`isinstance(inline_timeout_s, bool)` guard does work only for an **in-process Python caller** (one
that calls the function directly with a literal `bool`, as this repo's own unit tests can). Pinned
over MCP for point_in_time, electric, ugr, view_sunlight and bsdf, and by
`tests/test_unknown_arguments.py::TestFloatArgumentsAreStrict` for every tool; in-process for render
and glare, where a `bool` gets the plain numeric read.

Unsourced values row (docs/tool-surface.md `## Unsourced values`): the ceiling is measured on one
platform -- macOS/CPython on this machine -- and another platform's C `int` size or clock
resolution may differ. Errs toward refusal if the real limit on another platform is higher (a
caller is told to submit a shorter timeout when a larger one would in fact have worked), and
toward the pre-existing arithmetic catch-all if lower -- **but that catch-all is not universal**:
a code-less refusal, still `is_error=True`, on `point_in_time_tool`/`electric_tool`/`ugr_tool`
(each has its own `except ArithmeticError`) and on `view_sunlight_tool` (same clause, reached from
its `dry_run` path too, per the `run_view_and_sunlight` paragraph above); an uncaught crash on
`render_tool` and `glare_tool`, which catch no `ArithmeticError` around a spawn at all.
"""

from __future__ import annotations

import math
from typing import Final

from mcp_types import CallToolResult

from rad_mcp.envelope import Advisory, refuse

__all__ = ["SPAWN_TIMEOUT_CEILING_S", "refuse_inline_timeout"]

#: The largest `inline_timeout_s` a spawn accepted on the measuring machine (this module's
#: docstring). Not a rounded guess: `2147483.647` is the literal measured boundary, exactly
#: `2**31 - 1` milliseconds, the largest value the C `int` millisecond conversion `selectors`'s
#: poll/kqueue backends apply once a real wait is entered accepts. See the `## Unsourced values`
#: row this constant cites.
SPAWN_TIMEOUT_CEILING_S: Final[float] = 2147483.647


def refuse_inline_timeout(value: float) -> CallToolResult | None:
    """The one `inline_timeout_s` range check every tool that accepts the argument goes through.

    Accepts a finite value strictly above 0 and at most `SPAWN_TIMEOUT_CEILING_S`; refuses
    everything else, coded `ARGUMENT_OUT_OF_RANGE` with `field="inline_timeout_s"`. Does not
    special-case `bool` -- there is nothing to special-case on the wire, since every tool types
    `inline_timeout_s` as `StrictFloat` and the MCP SDK refuses a JSON `true`/`false` before this
    function is ever called (R854); see the module docstring's "Booleans" paragraph, and for
    `point_in_time_tool`'s own `isinstance(bool)` guard, which is reachable only by an in-process
    Python caller.

    The message prints the ceiling with `!r` (`2147483.647`), not `:g` (`2.14748e+06`, 3,647 s
    under the value accepted), and says it was measured rather than claiming it for every
    platform this server may run on.
    """
    if math.isfinite(value) and 0.0 < value <= SPAWN_TIMEOUT_CEILING_S:
        return None
    message = (
        f"inline_timeout_s={value!r} is not a finite number of seconds above 0 and at most "
        f"{SPAWN_TIMEOUT_CEILING_S!r}, the largest timeout a spawn was measured to accept "
        "(2**31 - 1 milliseconds, on macOS with CPython 3.11). Pass the seconds this call may "
        "run for. Omit it to accept this tool's default."
    )
    return refuse(message, [Advisory(code="ARGUMENT_OUT_OF_RANGE", severity="error",
                                     field="inline_timeout_s", message=message)])
