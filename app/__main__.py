"""
Entry point: ``python -m app``.

In a PyInstaller-bundled .app this is invoked indirectly via the main script
declared in the spec file.
"""

from __future__ import annotations

import argparse
import logging
import sys

from .window import launch


def main() -> int:
    parser = argparse.ArgumentParser(prog="arc2zen", description="Arc to Zen migration GUI")
    parser.add_argument("--debug", action="store_true", help="Enable WebKit devtools and verbose logging")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    launch(debug=args.debug)
    return 0


if __name__ == "__main__":
    sys.exit(main())
