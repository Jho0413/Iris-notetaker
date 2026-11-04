"""Exit when the desktop app is gone (``IRIS_PARENT_PID``).

The desktop app starts the backend and stops it on quit. If the app crashes or is force-quit it
can't, so the backend watches the app's process and shuts itself down (cleanly, as on Ctrl-C) once
it disappears, rather than keep running in the background with the user's API key loaded.
"""

import asyncio
import logging
import os
import signal
from collections.abc import Callable

log = logging.getLogger(__name__)


def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)  # signal 0: existence check only
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, owned by someone else
    return True


def request_shutdown() -> None:
    """Ask the server to stop, as Ctrl-C would: uvicorn finishes requests and runs shutdown."""
    os.kill(os.getpid(), signal.SIGTERM)


async def watch_parent(
    pid: int,
    interval_s: float,
    *,
    is_alive: Callable[[int], bool] | None = None,
    on_gone: Callable[[], None] | None = None,
) -> None:
    """Poll every ``interval_s`` until ``pid`` is gone, then call ``on_gone`` once. Defaults:
    ``pid_alive`` and ``request_shutdown``, looked up at call time so tests can replace them."""
    is_alive = is_alive or pid_alive
    on_gone = on_gone or request_shutdown
    while is_alive(pid):
        await asyncio.sleep(interval_s)
    log.warning("parent process %d is gone: shutting down", pid)
    on_gone()
