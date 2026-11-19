#!/usr/bin/env bash
set -euo pipefail

DESKTOP="$(cd "$(dirname "$0")/.." && pwd)"
REPO="$(cd "$DESKTOP/.." && pwd)"
export APPLE_KEYCHAIN_PROFILE="${APPLE_KEYCHAIN_PROFILE:-iris-notary}"
unset APPLE_ID APPLE_APP_SPECIFIC_PASSWORD APPLE_TEAM_ID APPLE_API_KEY APPLE_API_KEY_ID APPLE_API_ISSUER APPLE_KEYCHAIN

echo "==> Signing and notarization credentials"
identities="$(security find-identity -v -p codesigning)"
if [[ "$identities" != *'"Developer ID Application: '* ]]; then
  echo "build-app.sh: no Developer ID Application certificate with its private key in the keychain." >&2
  echo "Create one at developer.apple.com (Certificates), or import your .p12 backup into the login keychain." >&2
  exit 1
fi
if ! notary_check="$(xcrun notarytool history --keychain-profile "$APPLE_KEYCHAIN_PROFILE" 2>&1)"; then
  echo "build-app.sh: can't notarize with the keychain profile \"$APPLE_KEYCHAIN_PROFILE\": $notary_check" >&2
  echo "Save it with: xcrun notarytool store-credentials $APPLE_KEYCHAIN_PROFILE --apple-id <your Apple ID> --team-id <your team ID>" >&2
  exit 1
fi

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
echo "==> Smoke test of the signed backend in Iris.app"
SMOKE="$DESKTOP/build/backend/smoke signed"
rm -rf "$SMOKE"
env -i HOME="$HOME" PATH=/usr/bin:/bin \
  "$APP/Contents/Resources/backend/python/bin/python3" -I "$DESKTOP/scripts/smoke_backend.py" "$SMOKE"
rm -rf "$SMOKE"
codesign --verify --deep --strict "$APP"
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
  if [[ "$entitlements" == *disable-library-validation* ]]; then
    echo "build-app.sh: $bin loads libraries from any developer (disable-library-validation)" >&2; exit 1
  fi
done
echo "==> Gatekeeper"
xcrun stapler validate "$APP"
assessment="$(spctl --assess --type execute -vv "$APP" 2>&1)" || {
  echo "build-app.sh: Gatekeeper rejects $APP: $assessment" >&2; exit 1
}
if [[ "$assessment" != *"source=Notarized Developer ID"* ]]; then
  echo "build-app.sh: $APP is not notarized with a Developer ID: $assessment" >&2; exit 1
fi
echo "==> Built $APP ($(du -sh "$APP" | cut -f1)) and $(ls "$DESKTOP"/dist/*.dmg) ($(du -sh "$DESKTOP"/dist/*.dmg | cut -f1))"
