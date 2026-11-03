#!/bin/sh
# Build the IPK on Linux (WSL works) from a clean, LF-normalised, mode-fixed copy of src/, then verify the
# IPK itself.  See PORTING-GUIDE.md section 5 for why each step exists.
#
#   wsl -d Alpine -- sh /mnt/e/git/extern/TVerOff/build/build-ipk.sh
#
# needs: node, ares-package (npm i -g @webos-tools/cli), ar, tar, sed, sha256sum
set -eu

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(dirname "$HERE")"
SRC="$ROOT/src"
DIST="$ROOT/dist"
WORK="$(mktemp -d /tmp/tveroff-build.XXXXXX)"
trap 'rm -rf "$WORK"' EXIT

APPID="$(sed -n 's/.*"id" *: *"\([^"]*\)".*/\1/p' "$SRC/appinfo.json" | head -1)"
VER="$(sed -n 's/.*"version" *: *"\([^"]*\)".*/\1/p' "$SRC/appinfo.json" | head -1)"
PAYLOAD_VER="$(sed -n "s/.*VERSION = '\([^']*\)'.*/\1/p" "$SRC/payload/userScript.js" | head -1)"

echo "== $APPID $VER"
[ "$PAYLOAD_VER" = "$VER" ] || { echo "version mismatch: appinfo.json $VER vs payload $PAYLOAD_VER" >&2; exit 1; }

echo "== payload tests"
( cd "$ROOT" && node test/userscript.test.js )

echo "== clean copy (LF only, explicit modes)"
mkdir -p "$WORK/app"
cp -R "$SRC/." "$WORK/app/"
find "$WORK/app" -type f \( -name '*.sh' -o -name '*.js' -o -name '*.json' -o -name '*.html' -o -name '*.css' \) -exec sed -i 's/\r$//' {} +
find "$WORK/app" -type d -exec chmod 755 {} +
find "$WORK/app" -type f -exec chmod 644 {} +
find "$WORK/app/tools" -type f -name '*.sh' -exec chmod 755 {} +

echo "== ares-package --no-minify"
mkdir -p "$DIST" "$WORK/out"
rm -f "$DIST/${APPID}_"*.ipk
( cd "$WORK" && ares-package --no-minify app -o "$WORK/out" )
IPK="$(ls "$WORK/out/"*.ipk)"
cp "$IPK" "$DIST/"

echo "== verify"
sh "$HERE/verify-ipk.sh" "$DIST/$(basename "$IPK")"
