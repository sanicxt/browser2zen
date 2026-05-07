"""PyInstaller entry point. The bundled binary executes this script
directly, so we cannot rely on `python -m app` semantics. We make sure
the repo root is on sys.path and then call into the package as if it
were imported normally."""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path


def _bootstrap_path() -> None:
    here = Path(__file__).resolve().parent
    # When running from source (`python build/run_app.py`) the repo root
    # is the parent of build/. When running inside a PyInstaller bundle
    # `_MEIPASS` is the unpacked Resources directory.
    candidates = [here.parent, Path(getattr(sys, "_MEIPASS", ""))]
    for c in candidates:
        if c and str(c) not in sys.path:
            sys.path.insert(0, str(c))


def main() -> int:
    _bootstrap_path()
    from app.window import launch  # imported after sys.path is fixed

    parser = argparse.ArgumentParser(prog="browser2zen")
    parser.add_argument("--debug", action="store_true",
                        help="Enable WebKit devtools and verbose logging")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    launch(debug=args.debug)
    return 0


if __name__ == "__main__":
    sys.exit(main())
