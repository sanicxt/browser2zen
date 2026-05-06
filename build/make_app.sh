#!/usr/bin/env bash
# Build dist/Arc2Zen.app from app/ + src/ + the PyInstaller spec, then
# ad-hoc codesign the bundle. Outputs a runnable .app — no .dmg yet
# (see make_dmg.sh for that step).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

# 1. Render the app icon if it isn't on disk yet (`.icns` is gitignored).
[[ -f app/assets/icon.icns ]] || bash build/make_iconset.sh

# 2. PyInstaller. --clean nukes any cached state from a prior failed run.
echo "==> pyinstaller"
pyinstaller --noconfirm --clean build/arc2zen.spec

# 3. Ad-hoc codesign every bundled binary so Gatekeeper doesn't flag the
# app as "damaged" (which it does for unsigned PyInstaller bundles
# whose embedded dylibs lose their original signatures during reassembly).
echo "==> codesign --deep --sign -"
codesign --force --deep --sign - "dist/Arc2Zen.app"

# 4. Verify.
codesign --verify --deep --strict --verbose=2 "dist/Arc2Zen.app" 2>&1 | tail -3 || {
  echo "codesign verification failed" >&2
  exit 1
}

echo "==> done: dist/Arc2Zen.app"
ls -lah dist/Arc2Zen.app/Contents/MacOS/
