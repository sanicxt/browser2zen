#!/usr/bin/env bash
# Render app/assets/icon.svg into app/assets/icon.icns at all macOS sizes.
# Run locally before building; produces a deterministic .icns that gets bundled.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SVG="$ROOT/app/assets/icon.svg"
OUT="$ROOT/app/assets/icon.icns"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

if [[ ! -f "$SVG" ]]; then
  echo "icon.svg not found at $SVG" >&2
  exit 1
fi

# Render the SVG once at 1024px via Quick Look (built into macOS, no deps).
qlmanage -t -s 1024 -o "$TMP" "$SVG" >/dev/null
SRC_PNG="$TMP/$(basename "$SVG").png"
[[ -f "$SRC_PNG" ]] || { echo "qlmanage rendering failed"; exit 1; }

ICONSET="$TMP/icon.iconset"
mkdir -p "$ICONSET"

# macOS expects these exact filenames in a .iconset directory.
declare -a sizes=(
  "16  icon_16x16.png"
  "32  icon_16x16@2x.png"
  "32  icon_32x32.png"
  "64  icon_32x32@2x.png"
  "128 icon_128x128.png"
  "256 icon_128x128@2x.png"
  "256 icon_256x256.png"
  "512 icon_256x256@2x.png"
  "512 icon_512x512.png"
  "1024 icon_512x512@2x.png"
)
for entry in "${sizes[@]}"; do
  size="${entry%% *}"
  name="${entry##* }"
  sips -z "$size" "$size" "$SRC_PNG" --out "$ICONSET/$name" >/dev/null
done

iconutil -c icns "$ICONSET" -o "$OUT"
echo "wrote $OUT ($(stat -f '%z' "$OUT") bytes)"
