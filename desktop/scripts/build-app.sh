#!/usr/bin/env bash
# Build Iris.app and a .dmg in desktop/dist (make desktop): the UI, the backend bundle, the icon,
# then electron-builder. Apple Silicon macOS only.
set -euo pipefail

DESKTOP="$(cd "$(dirname "$0")/.." && pwd)"
REPO="$(cd "$DESKTOP/.." && pwd)"

echo "==> UI (frontend/dist)"
(cd "$REPO/frontend" && npm run build)

"$DESKTOP/scripts/build-backend.sh"

echo "==> Icon"
ICONSET="$DESKTOP/build/icon.iconset"
rm -rf "$ICONSET" && mkdir -p "$ICONSET"
"$DESKTOP/build/backend/python/bin/python3" -I -B - "$DESKTOP/build-resources/icon.svg" "$ICONSET" <<'PY'
import sys
from pathlib import Path

import resvg_py

svg, out = Path(sys.argv[1]).read_text(), Path(sys.argv[2])
for size in (16, 32, 128, 256, 512):
    for scale in (1, 2):
        name = f"icon_{size}x{size}{'@2x' if scale == 2 else ''}.png"
        (out / name).write_bytes(bytes(resvg_py.svg_to_bytes(svg_string=svg, width=size * scale)))
PY
iconutil -c icns "$ICONSET" -o "$DESKTOP/build/icon.icns"

echo "==> Iris.app and .dmg (electron-builder)"
cd "$DESKTOP"
rm -rf dist
npx electron-builder --mac --arm64 --publish never

APP="$DESKTOP/dist/mac-arm64/Iris.app"
codesign --verify --deep --strict "$APP"
echo "==> Built $APP ($(du -sh "$APP" | cut -f1)) and $(ls "$DESKTOP"/dist/*.dmg) ($(du -sh "$DESKTOP"/dist/*.dmg | cut -f1))"
