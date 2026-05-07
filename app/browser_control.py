"""
Browser control: graceful quit, launch Zen, reveal-in-shell.

Cross-platform:
- macOS: ``osascript -e 'tell application "X" to quit'`` + ``open -a`` + ``open -R``.
- Windows: ``taskkill /im X.exe`` (graceful WM_CLOSE, falls back to /f) +
  ``Popen([zen.exe])`` from the path that ``env_check`` discovered, +
  ``explorer /select,<path>``.

Each ``quit_browser`` call polls for up to ``timeout`` seconds and reports
what actually exited.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Literal, Optional

from .env_check import (
    is_arc_running,
    is_zen_running,
    list_zen_profiles,
)

BrowserName = Literal["arc", "zen"]


# ---------- macOS-specific helpers ----------

_APPLESCRIPT_QUIT = {
    "arc": 'tell application "Arc" to quit',
    "zen": 'tell application "Zen" to quit',
}


def _run_osascript(script: str, timeout: float = 5.0) -> bool:
    try:
        r = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, text=True, timeout=timeout,
        )
        return r.returncode == 0
    except Exception:
        return False


# ---------- Windows-specific helpers ----------

# Process names per browser. We taskkill all matches in case the user has
# the older standalone-installer Arc (just "Arc.exe") alongside the UWP one.
_WINDOWS_PROCESS_NAMES = {
    "arc": ("Arc.exe",),
    "zen": ("zen.exe", "zen-bin.exe"),
}


def _taskkill(image_name: str, force: bool) -> bool:
    flags = ["/im", image_name]
    if force:
        flags.append("/f")
    try:
        r = subprocess.run(
            ["taskkill", *flags],
            capture_output=True, text=True, timeout=5,
        )
    except Exception:
        return False
    # taskkill returns 0 if at least one process was signalled, 128 if no
    # process matched. Treat both as "did our best".
    return r.returncode in (0, 128)


def _windows_zen_exe() -> Path | None:
    """Find ``zen.exe`` (or ``zen-bin.exe``) under the active Zen profile.

    Zen on Windows installs alongside the user profile; the profile's
    parent dir contains the executable. Falls back to common install
    locations.
    """
    home = Path.home()
    candidates: list[Path] = []
    profiles = list_zen_profiles()
    for prof in profiles:
        # profile is at <root>/Profiles/<id>.<name>/; walk up two levels
        # and check siblings.
        root = prof.path.parent.parent
        for name in ("zen.exe", "zen-bin.exe"):
            candidates.append(root / name)
    candidates += [
        home / "AppData/Local/zen/zen.exe",
        Path("C:/Program Files/Zen Browser/zen.exe"),
        Path("C:/Program Files (x86)/Zen Browser/zen.exe"),
    ]
    for c in candidates:
        if c.is_file():
            return c
    return None


# ---------- public API ----------


def quit_browser(name: BrowserName, timeout: float = 6.0) -> dict:
    """Ask the OS to quit a browser, then poll until it actually exits.

    Returns ``{"ok": bool, "running": bool, "elapsed": float}``.
    """
    if name not in ("arc", "zen"):
        return {"ok": False, "running": _is_running(name), "elapsed": 0.0,
                "error": f"unknown browser: {name}"}

    started = time.time()

    if sys.platform == "darwin":
        script = _APPLESCRIPT_QUIT[name]
        sent = _run_osascript(script, timeout=2.0)
        if not sent:
            return {"ok": False, "running": _is_running(name),
                    "elapsed": time.time() - started,
                    "error": "osascript failed (is the browser actually open?)"}
    elif os.name == "nt":
        any_signalled = False
        for image in _WINDOWS_PROCESS_NAMES[name]:
            if _taskkill(image, force=False):
                any_signalled = True
        if not any_signalled:
            return {"ok": False, "running": _is_running(name),
                    "elapsed": time.time() - started,
                    "error": "taskkill could not signal the browser"}
    else:
        return {"ok": False, "running": _is_running(name), "elapsed": 0.0,
                "error": "graceful quit only supports macOS and Windows"}

    deadline = started + timeout
    while time.time() < deadline:
        if not _is_running(name):
            return {"ok": True, "running": False, "elapsed": time.time() - started}
        time.sleep(0.25)

    # Soft quit timed out. On Windows, escalate to /f and report what happens.
    if os.name == "nt":
        for image in _WINDOWS_PROCESS_NAMES[name]:
            _taskkill(image, force=True)
        time.sleep(0.5)
        if not _is_running(name):
            return {"ok": True, "running": False, "elapsed": time.time() - started,
                    "forced": True}

    return {"ok": False, "running": True, "elapsed": time.time() - started,
            "error": "browser did not quit within timeout"}


def _is_running(name: BrowserName) -> bool:
    return is_arc_running() if name == "arc" else is_zen_running()


def launch_zen() -> bool:
    """Open Zen using the OS shell. Returns True on success."""
    if sys.platform == "darwin":
        for app_name in ("Zen", "Zen Browser", "zen"):
            try:
                r = subprocess.run(["open", "-a", app_name],
                                   capture_output=True, text=True, timeout=5)
            except Exception:
                continue
            if r.returncode == 0:
                return True
        return False

    if os.name == "nt":
        exe = _windows_zen_exe()
        if exe is None:
            return False
        try:
            subprocess.Popen([str(exe)],
                             creationflags=getattr(subprocess, "DETACHED_PROCESS", 0))
            return True
        except Exception:
            return False

    return False


def open_in_finder(path: str) -> bool:
    """Reveal a file or folder in the OS file manager."""
    if sys.platform == "darwin":
        try:
            r = subprocess.run(["open", "-R", path],
                               capture_output=True, text=True, timeout=5)
        except Exception:
            return False
        return r.returncode == 0

    if os.name == "nt":
        try:
            # `explorer /select,<path>` opens the parent folder with the
            # file selected. Note the comma syntax is intentional.
            subprocess.Popen(["explorer", f"/select,{path}"])
            return True
        except Exception:
            return False

    return False
