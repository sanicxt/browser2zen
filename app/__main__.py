"""
Entry point: ``python -m app``.

In a PyInstaller-bundled .app/.exe this is invoked indirectly via the main
script declared in the spec file.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys

from .window import launch


def _set_windows_app_user_model_id() -> None:
    """Tell Windows that this process is its own application.

    Without this, the WebView2 host inherits Python's default AppUserModelID
    and Windows groups every Arc2Zen launch under a generic "Python" entry
    in the taskbar. Setting it makes the icon, taskbar grouping, and pin
    behaviour all line up correctly.
    """
    if os.name != "nt":
        return
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            "com.arc2zen.app"
        )
    except Exception:
        pass


def main() -> int:
    parser = argparse.ArgumentParser(prog="arc2zen", description="Arc to Zen migration GUI")
    parser.add_argument("--debug", action="store_true", help="Enable WebKit devtools and verbose logging")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    _set_windows_app_user_model_id()
    launch(debug=args.debug)
    return 0


if __name__ == "__main__":
    sys.exit(main())
