"""Stopping the server stops its inline Radiance children (Phase 17, R750; design section 3.3).

Since R750 every inline spawn in `rad_engine.run` and `rad_engine.postprocess` runs its child in its
own session on POSIX (`rad_engine.process_group`; the install probe in `rad_engine.env`, a
`-version` call under a fixed 10 s bound, does not), so a client that stops this server by `killpg`
of the server's group no longer reaches such a child: measured, a genBSDF tree ran on 10 s after the
client had returned (design M7). These handlers close that: SIGTERM, SIGINT and SIGHUP first SIGKILL
every recorded inline child group (`rad_engine.process_group.begin_stop_sweep`, once), then give the
signal its previous effect.

**Installed from `lifespan`, inside the running event loop**, so the previous SIGINT handler is the
one asyncio's `Runner` installed (`Runner._on_sigint`, design P9): chaining to it keeps Ctrl-C's
meaning -- a first one cancels the main task, a second raises `KeyboardInterrupt`. **Only when
`main()` called `enable()`**: the in-process test client (`mcp.client.Client(mcp)`) enters
`lifespan` too, and process-wide handlers installed there would outlive the session inside pytest.
A signal inherited as ignored (SIGHUP under `nohup`) gets no handler. On `lifespan` exit each
handler is put back if it is still ours. POSIX only.

**No lock anywhere on this path** (design P7: a handler waiting on a lock its own thread held never
returned). The sweep happens once; **the previous effect is applied on every signal**, because a
handler that returned early on a stop already in progress swallowed the SIGTERM that followed a
SIGINT, and the server lived on (design P9).

**What is not covered:** SIGKILL cannot be caught, so a stop that sends only SIGKILL leaves inline
children running; and a SIGTERM or SIGHUP ends the server inside this handler (design P1), so a
worker thread inside `Popen` at that instant never records its child.
"""

from __future__ import annotations

import os
import signal
import threading
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, Final

from rad_engine.process_group import begin_stop_sweep

__all__ = ["STOP_SIGNALS", "enable", "lifespan"]

STOP_SIGNALS: Final = (signal.SIGTERM, signal.SIGINT, signal.SIGHUP) if os.name != "nt" else ()

#: Set by `rad_mcp.server.main`. False for every in-process use of the server object.
_enabled = False

#: The disposition each signal had when `lifespan` installed ours.
_previous: dict[int, Any] = {}


def enable() -> None:
    """Called by `rad_mcp.server.main` before `mcp.run`: this process is the real server."""
    global _enabled
    _enabled = True


def _handler(signum: int, frame: Any) -> None:
    try:
        begin_stop_sweep()  # sets the flag first; a second call does nothing
    except Exception:  # noqa: BLE001 - the previous effect below must run whatever this did
        pass
    previous = _previous.get(signum, signal.SIG_DFL)
    if callable(previous):
        previous(signum, frame)  # SIGINT: asyncio's Runner._on_sigint, or default_int_handler
        return
    # SIG_DFL, or None (a disposition not installed from Python): restore the default and re-send,
    # so SIGTERM and SIGHUP still end the server as they did before R750.
    signal.signal(signum, signal.SIG_DFL)
    os.kill(os.getpid(), signum)


@asynccontextmanager
async def lifespan(app: Any) -> AsyncIterator[dict[str, Any]]:
    installed: list[int] = []
    if _enabled and threading.current_thread() is threading.main_thread():
        for signum in STOP_SIGNALS:
            previous = signal.getsignal(signum)
            if previous is signal.SIG_IGN:
                continue
            _previous[signum] = previous
            signal.signal(signum, _handler)
            installed.append(signum)
    try:
        yield {}
    finally:
        for signum in installed:
            if signal.getsignal(signum) is _handler:
                previous = _previous.pop(signum)
                signal.signal(signum, previous if previous is not None else signal.SIG_DFL)
