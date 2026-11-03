#!/bin/sh
# Verify the contents of a built IPK, not just the sources (PORTING-GUIDE.md section 5):
#   exec bits present, no CR in scripts/configs/payload, sh syntax valid (busybox ash), payload byte-identical
#   to the tested source, nothing owned by a non-root uid.
#
#   sh build/verify-ipk.sh dist/com.tveroff.patcher_0.1.0_all.ipk
set -eu

IPK="$(readlink -f "$1")"
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(dirname "$HERE")"
T="$(mktemp -d /tmp/tveroff-verify.XXXXXX)"
trap 'rm -rf "$T"' EXIT

fail=0
ok()  { echo "  ok    $*"; }
bad() { echo "  FAIL  $*"; fail=1; }

cd "$T"
ar x "$IPK"
for m in debian-binary control.tar.gz data.tar.gz; do
  [ -f "$m" ] && ok "ipk member $m" || bad "missing ipk member $m"
done
mkdir data control
tar -xzf data.tar.gz -C data
tar -xzf control.tar.gz -C control

APPDIR="$(ls -d data/usr/palm/applications/* 2>/dev/null | head -1)"
[ -n "$APPDIR" ] || { echo "  FAIL  no application directory in data.tar.gz" >&2; exit 1; }
APPID="$(basename "$APPDIR")"
ok "application dir: /usr/palm/applications/$APPID"
[ -f "$APPDIR/appinfo.json" ] && ok "appinfo.json" || bad "appinfo.json missing"
for f in index.html js/app.js css/style.css icon.png largeIcon.png payload/userScript.js tools/tverctl.sh tools/autostart.sh tools/lib/common.sh tools/lib/monitor.sh; do
  [ -f "$APPDIR/$f" ] || bad "missing $f"
done

echo "-- exec bits"
for f in "$APPDIR"/tools/*.sh "$APPDIR"/tools/lib/*.sh; do
  [ -x "$f" ] && ok "+x ${f#$APPDIR/}" || bad "not executable: ${f#$APPDIR/}"
done

echo "-- no CR in text files (CRLF breaks busybox sh)"
crs=0
for f in $(find "$APPDIR" -type f \( -name '*.sh' -o -name '*.js' -o -name '*.json' -o -name '*.html' -o -name '*.css' \)); do
  n="$(tr -cd '\r' < "$f" | wc -c)"
  [ "$n" -eq 0 ] || { bad "$n CR in ${f#$APPDIR/}"; crs=1; }
done
[ "$crs" -eq 0 ] && ok "no CR anywhere"

echo "-- sh syntax"
for f in "$APPDIR"/tools/*.sh "$APPDIR"/tools/lib/*.sh; do
  if sh -n "$f" 2>/dev/null; then ok "sh -n ${f#$APPDIR/}"; else bad "syntax error in ${f#$APPDIR/}"; fi
done

echo "-- payload"
want="$(tr -d '\r' < "$ROOT/src/payload/userScript.js" | sha256sum | cut -d' ' -f1)"
got="$(sha256sum "$APPDIR/payload/userScript.js" | cut -d' ' -f1)"
if [ "$want" = "$got" ]; then ok "payload byte-identical to the tested source ($got)"; else bad "payload differs from tested source ($got vs $want)"; fi
grep -q 'TVerOff user script' "$APPDIR/payload/userScript.js" && ok "payload is readable (not minified)" || bad "payload looks minified/mangled"

echo "-- ownership"
# GNU tar prints root/root, busybox tar prints root/0 or 0/0: all mean uid 0 / gid 0
nonroot="$(tar -tvzf data.tar.gz | awk '{print $2}' | grep -Ev '^(root|0)/(root|0)$' | sort -u | tr '\n' ' ')"
[ -z "$nonroot" ] && ok "everything owned by root" || bad "non-root owners in data.tar.gz: $nonroot"

echo "-- control"
sed 's/^/        /' control/control

if [ "$fail" -eq 0 ]; then
  echo "IPK OK: $IPK ($(wc -c < "$IPK") bytes)"
else
  echo "IPK FAILED verification" >&2
  exit 1
fi
