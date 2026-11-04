#!/usr/bin/env bash
# Build the backend bundle for the desktop app: a standalone CPython (python-build-standalone, as
# `uv python install` downloads it) with Iris and its dependencies installed into it, at
# desktop/build/backend/python. electron-builder copies it into Iris.app/Contents/Resources.
#
# Everything happens inside desktop/build/backend; no temporary path ends up in the bundle. The
# bundle runs from any folder with an empty environment (no repo, Homebrew or uv), started as
# `python3 -I -m iris serve` (the console scripts' shebangs hold this machine's build path).
set -euo pipefail

DESKTOP="$(cd "$(dirname "$0")/.." && pwd)"
REPO="$(cd "$DESKTOP/.." && pwd)"
BUILD="$DESKTOP/build/backend"
PY_VERSION="$(cat "$REPO/backend/.python-version")"

case "$(uname -s)-$(uname -m)" in
  Darwin-arm64) PLATFORM="macos-aarch64-none" ;;
  *) echo "build-backend.sh: only Apple Silicon macOS is supported for now" >&2; exit 1 ;;
esac

echo "==> Fresh build folder: $BUILD"
rm -rf "$BUILD"
mkdir -p "$BUILD"

echo "==> Python $PY_VERSION (python-build-standalone)"
uv python install "$PY_VERSION" --install-dir "$BUILD/pythons" --no-bin
shopt -s nullglob
found=("$BUILD"/pythons/cpython-"$PY_VERSION".*-"$PLATFORM")
shopt -u nullglob
if [ "${#found[@]}" -ne 1 ]; then
  echo "build-backend.sh: expected one cpython-$PY_VERSION.*-$PLATFORM in $BUILD/pythons, found ${#found[@]}" >&2
  exit 1
fi
cp -a "${found[0]}" "$BUILD/python"
rm -rf "$BUILD/pythons"
PY="$BUILD/python/bin/python3"
SITE="$("$PY" -I -c 'import sysconfig; print(sysconfig.get_path("purelib"))')"
STDLIB="$("$PY" -I -c 'import sysconfig; print(sysconfig.get_path("stdlib"))')"

echo "==> Iris and its locked dependencies"
(cd "$REPO/backend" && uv export --no-dev --frozen --no-emit-project --no-header -o "$BUILD/requirements.txt" >/dev/null)
uv pip install --python "$PY" --break-system-packages --link-mode copy -r "$BUILD/requirements.txt"
uv pip install --python "$PY" --break-system-packages --link-mode copy --no-deps "$REPO/backend"

echo "==> Trimming what the app never uses"
# pip and setuptools (nothing installs at run time), the GUI toolkit and its demos, and the
# console scripts, whose shebangs point at this build folder.
rm -rf "$SITE"/pip "$SITE"/pip-* "$SITE"/setuptools "$SITE"/setuptools-* "$SITE"/_distutils_hack
rm -rf "$STDLIB"/ensurepip "$STDLIB"/idlelib "$STDLIB"/tkinter "$STDLIB"/turtledemo "$STDLIB"/turtle.py
rm -f "$STDLIB"/lib-dynload/_tkinter.*.so
rm -rf "$BUILD"/python/lib/tcl* "$BUILD"/python/lib/tk* "$BUILD"/python/lib/itcl* "$BUILD"/python/lib/thread*
rm -f "$BUILD"/python/lib/libtcl* "$BUILD"/python/lib/libtk*
find "$BUILD/python/bin" -type f ! -name 'python3*' -delete
find "$BUILD/python/bin" -type l ! -name 'python3*' -delete
find "$BUILD/python" -name '__pycache__' -type d -prune -exec rm -rf {} +

echo "==> Precompiling bytecode (the signed app must not write .pyc into itself)"
"$PY" -I -m compileall -q -j 0 "$BUILD/python/lib" >/dev/null

echo "==> Smoke test from a copy in a path with a space, with an empty environment"
SMOKE="$BUILD/smoke test"
rm -rf "$SMOKE"
mkdir -p "$SMOKE"
cp -a "$BUILD/python" "$SMOKE/python"
(cd "$SMOKE" && env -i HOME="$HOME" PATH=/usr/bin:/bin \
  "$SMOKE/python/bin/python3" -I "$DESKTOP/scripts/smoke_backend.py" "$SMOKE/work")
rm -rf "$SMOKE"

echo "==> Backend bundle: $(du -sh "$BUILD/python" | cut -f1) in $BUILD/python"
