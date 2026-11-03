#!/bin/sh
# Boot hook: /var/lib/webosbrew/init.d/50-custom-tveroff -> this file (Homebrew Channel run-parts).
# run-parts is synchronous, so this must return quickly; tverctl.sh detaches its daemon.

DIR="$(dirname "$(readlink -f "$0")")"
"$DIR/tverctl.sh" start boot >/dev/null 2>&1
exit 0
