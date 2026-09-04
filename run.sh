#!/usr/bin/env bash
# Dev launcher: ensure deps via uv, then run the GUI app.
set -euo pipefail
cd "$(dirname "$0")"

if ! command -v uv >/dev/null 2>&1; then
    echo "error: uv not found. Install it first: curl -LsSf https://astral.sh/uv/install.sh | sh" >&2
    exit 1
fi

if [ ! -d .venv ]; then
    echo "==> creating venv + installing deps"
    uv venv
    uv pip install -r requirements.txt -r requirements-build.txt
fi

exec uv run python -m app "$@"