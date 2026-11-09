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
# Again, now with the Python as signed inside the app: the hardened runtime decides what it may
# load. Run without -B, so any module Python had to compile again (stale bytecode) is written into
# the app and the signature check below fails.
echo "==> Smoke test of the signed backend in Iris.app"
SMOKE="$DESKTOP/build/backend/smoke signed"
rm -rf "$SMOKE"
env -i HOME="$HOME" PATH=/usr/bin:/bin \
  "$APP/Contents/Resources/backend/python/bin/python3" -I "$DESKTOP/scripts/smoke_backend.py" "$SMOKE"
rm -rf "$SMOKE"
codesign --verify --deep --strict "$APP"
# The hardened runtime is the point of the signature: fail if a config change dropped it, or let a
# debugger in, for the app or its engine.
# Captured first, not piped to grep -q: under pipefail, grep quitting early fails the pipeline.
for bin in "$APP" "$APP/Contents/Resources/backend/python/bin/python3"; do
  signature="$(codesign -dv "$bin" 2>&1)" || {
    echo "build-app.sh: can't read the signature of $bin: $signature" >&2; exit 1
  }
  entitlements="$(codesign -d --entitlements - "$bin" 2>/dev/null)"
  if [[ "$signature" != *flags=*runtime* ]]; then
    echo "build-app.sh: $bin is not signed with the hardened runtime" >&2; exit 1
  fi
  if [[ "$entitlements" == *get-task-allow* ]]; then
    echo "build-app.sh: $bin lets a debugger attach (get-task-allow)" >&2; exit 1
  fi
done
echo "==> Built $APP ($(du -sh "$APP" | cut -f1)) and $(ls "$DESKTOP"/dist/*.dmg) ($(du -sh "$DESKTOP"/dist/*.dmg | cut -f1))"
