"""
PyWebView window setup.

Single 760×580 frameless window, vibrancy on macOS for the modern translucent
chrome. Frontend assets are served by pywebview's built-in HTTP server so we
avoid WKWebView's local-file restrictions.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import webview

from .bridge import Bridge

logger = logging.getLogger(__name__)


def _frontend_dir() -> Path:
    """Find the bundled frontend assets, both in dev and PyInstaller modes."""
    here = Path(__file__).resolve().parent / "frontend"
    if here.is_dir():
        return here
    # PyInstaller --onedir: assets are next to the binary in Contents/Resources.
    if hasattr(sys, "_MEIPASS"):
        bundled = Path(sys._MEIPASS) / "app" / "frontend"
        if bundled.is_dir():
            return bundled
    raise RuntimeError(f"Frontend directory not found (tried {here})")


def launch(debug: bool = False) -> None:
    bridge = Bridge()

    frontend = _frontend_dir()
    # Use a file:// URL (simpler and avoids the bottle HTTP server thread).
    # We don't need cross-origin fetches; everything goes through the JS bridge.
    entry = (frontend / "index.html").as_uri()

    window = webview.create_window(
        title="Arc2Zen",
        url=entry,
        js_api=bridge,
        width=760,
        height=580,
        min_size=(760, 580),
        resizable=False,
        frameless=True,
        # easy_drag=True enables Cocoa's `mouseDownCanMoveWindow`, letting users
        # drag the window from any non-interactive area while buttons still work.
        # CSS `-webkit-app-region: drag` is not honoured by WKWebView.
        easy_drag=True,
        background_color="#0E0F12",
        vibrancy=(sys.platform == "darwin"),
    )
    bridge.set_window(window)

    webview.start(
        debug=debug,
        private_mode=False,
    )
