"""Keep a Windows PC from going to sleep while the screener runs.

The Windows equivalent of macOS `caffeinate -i`: the screen may still turn
off, but the computer stays awake so scans and emails keep happening. The
request ends automatically when the process exits; `release()` ends it
earlier. On other systems both functions do nothing (on a Mac the start
file already uses caffeinate).
"""

from __future__ import annotations

import logging
import sys

log = logging.getLogger(__name__)

ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001


def _kernel32():
    if sys.platform != "win32":
        return None
    try:
        import ctypes

        return ctypes.windll.kernel32
    except (ImportError, AttributeError, OSError):
        return None


def prevent_sleep() -> bool:
    """Ask Windows not to sleep. Returns True if the request was accepted."""
    kernel32 = _kernel32()
    if kernel32 is None:
        return False
    try:
        ok = bool(kernel32.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED))
    except Exception:  # never let this stop the screener from starting
        log.warning("could not ask Windows to stay awake", exc_info=True)
        return False
    if ok:
        log.info("keeping this PC awake while the screener runs (the screen can still turn off)")
    else:
        log.warning("Windows refused the keep-awake request; check the PC's sleep settings")
    return ok


def release() -> None:
    """Let Windows sleep normally again."""
    kernel32 = _kernel32()
    if kernel32 is None:
        return
    try:
        kernel32.SetThreadExecutionState(ES_CONTINUOUS)
    except Exception:
        pass
