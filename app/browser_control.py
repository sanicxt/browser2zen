"""
Browser control via AppleScript / `open`.

Used for two GUI affordances:
- Quitting Arc / Zen gracefully when the Detect screen finds them running.
- Launching Zen from the Done screen so the user can see the result immediately.

AppleScript `tell application "X" to quit` requests a clean shutdown, which
gives the browser a chance to save its session. We poll for up to ``timeout``
seconds and report what actually quit.
"""

from __future__ import annotations

import subprocess
import sys
import time
from typing import Literal

from .env_check import is_arc_running, is_zen_running

BrowserName = Literal["arc", "zen"]


_APPLESCRIPT_QUIT = {
    "arc": 'tell application "Arc" to quit',
    "zen": 'tell application "Zen" to quit',
}


def _run_osascript(script: str, timeout: float = 5.0) -> bool:
    if sys.platform != "darwin":
        return False
    try:
        r = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, text=True, timeout=timeout,
        )
        return r.returncode == 0
    except Exception:
        return False


def quit_browser(name: BrowserName, timeout: float = 6.0) -> dict:
    """Ask AppleScript to quit a browser, then poll until it actually exits.

    Returns ``{"ok": bool, "running": bool, "elapsed": float}``.
    """
    if sys.platform != "darwin":
        return {"ok": False, "running": _is_running(name), "elapsed": 0.0,
                "error": "AppleScript quit only works on macOS"}

    script = _APPLESCRIPT_QUIT.get(name)
    if script is None:
        return {"ok": False, "running": _is_running(name), "elapsed": 0.0,
                "error": f"unknown browser: {name}"}

    started = time.time()
    sent = _run_osascript(script, timeout=2.0)
    if not sent:
        return {"ok": False, "running": _is_running(name),
                "elapsed": time.time() - started,
                "error": "osascript failed (is the browser actually open?)"}

    deadline = started + timeout
    while time.time() < deadline:
        if not _is_running(name):
            return {"ok": True, "running": False, "elapsed": time.time() - started}
        time.sleep(0.25)

    return {"ok": False, "running": True, "elapsed": time.time() - started,
            "error": "browser did not quit within timeout"}


def _is_running(name: BrowserName) -> bool:
    return is_arc_running() if name == "arc" else is_zen_running()


def launch_zen() -> bool:
    """Open Zen via macOS `open -a`. No-op on non-macOS."""
    if sys.platform != "darwin":
        return False
    for app_name in ("Zen", "Zen Browser", "zen"):
        try:
            r = subprocess.run(["open", "-a", app_name], capture_output=True, text=True, timeout=5)
        except Exception:
            continue
        if r.returncode == 0:
            return True
    return False


def open_in_finder(path: str) -> bool:
    """Reveal a file or folder in Finder."""
    if sys.platform != "darwin":
        return False
    try:
        r = subprocess.run(["open", "-R", path], capture_output=True, text=True, timeout=5)
    except Exception:
        return False
    return r.returncode == 0
