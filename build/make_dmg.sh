#!/usr/bin/env bash
# Package dist/Arc2Zen.app into a .dmg with a "drag to Applications" layout
# and an INSTRUCTIONS.txt the user sees inside the mounted volume.
#
# Uses hdiutil only (ships with macOS, no extra dependencies).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

VERSION="${VERSION:-1.0.0}"
DMG_NAME="Arc2Zen-${VERSION}-arm64.dmg"
VOLNAME="Arc2Zen"

[[ -d dist/Arc2Zen.app ]] || { echo "dist/Arc2Zen.app not found. Run build/make_app.sh first." >&2; exit 1; }

# Stage the .app + Applications shortcut + INSTRUCTIONS.txt into a clean
# directory so the mounted volume has only the things we want visible.
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT

cp -R "dist/Arc2Zen.app" "$STAGE/"
# Arc2Zen is run-once-and-trash software, so we don't ship the
# usual "Applications" symlink. Users double-click the app inside
# the mounted DMG, run the migration, then drag the DMG to Trash.
[[ -f build/INSTRUCTIONS.txt ]] && cp build/INSTRUCTIONS.txt "$STAGE/"

# Strip xattrs so Finder doesn't show stale spotlight metadata.
xattr -cr "$STAGE" 2>/dev/null || true

rm -f "dist/$DMG_NAME"

echo "==> hdiutil create"
hdiutil create \
  -volname "$VOLNAME" \
  -srcfolder "$STAGE" \
  -fs HFS+ \
  -format UDZO \
  -imagekey zlib-level=9 \
  -ov \
  "dist/$DMG_NAME" >/dev/null

# Verify the image opens cleanly.
echo "==> hdiutil verify"
hdiutil verify "dist/$DMG_NAME" >/dev/null

echo "==> done: dist/$DMG_NAME ($(du -h "dist/$DMG_NAME" | cut -f1))"
