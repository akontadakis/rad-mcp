"""The process count every Radiance binary that accepts `-n` is given (docs/tool-surface.md#concurrency).

A leaf module (it imports only `os`), so `rad_engine.klems`, `visibility`, `glarendx`, `ies` and
`jobs` can import it (`jobs` re-exports it; `chain` takes the count as `ChainRequest.processes`):
`rad_engine.jobs` imports `suns`, which imports `matrices`, which imports
`klems`, so `klems` importing `jobs` would be a cycle (R780).
"""

from __future__ import annotations

import os

__all__ = ["worker_cores"]


def worker_cores() -> int:
    """The `-n` the spec decides for a Radiance binary that accepts it: **cores - 1** (2026-07-30).

    Reserving one core rather than all of them leaves the server responsive enough to answer a
    `job(action="status")` call while a matrix build saturates the rest (an Unsourced value:
    docs/tool-surface.md#unsourced-values). Since R780 `rtrace` gets it on every inline call (the
    `rtrace -version` install probe traces nothing and is not given it), and since R781
    `rcontrib`/`rfluxmtx` in `run_annual`'s steps; `genBSDF` does not (owner decision E1).
    The digest excludes it (`rad_core.manifest.DIGEST_EXCLUDED_PARAMETERS`): core count changes
    wall time, and an artifact moved to another machine stays reusable.
    """
    return max(1, (os.cpu_count() or 2) - 1)
