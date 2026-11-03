#!/bin/sh
# TVerOff - shared helpers. busybox sh, runs as root (Homebrew Channel exec / init.d).
# The entry script sets TOOLS_DIR before sourcing this file.

APP_ID="com.tveroff.patcher"
TVER_ID="jp.co.tver.tvapp"
TVER_BASE_DIR="/media/cryptofs/apps/usr/palm/applications"
TVER_DIR="$TVER_BASE_DIR/$TVER_ID"

# WAM injects <app dir>/webOSUserScripts/userScript.js into every page of the app.
INJECT_DIR="$TVER_DIR/webOSUserScripts"
INJECT_FILE="$INJECT_DIR/userScript.js"

PATCHER_DIR="$(dirname "$TOOLS_DIR")"
PAYLOAD="$PATCHER_DIR/payload/userScript.js"
ICON="$PATCHER_DIR/icon.png"

BASE="/tmp/tveroff"
# Logs live under /var/lib/webosbrew/ (not /tmp): they survive a reboot, so what the boot hook did
# can still be read afterwards - and it is where webosbrew expects startup scripts to report.
LOG_DIR="/var/lib/webosbrew/tveroff"
LOG="$LOG_DIR/tveroff.log"
MONITOR_PID="$BASE/monitor.pid"    # transient by nature
MONITOR_LOG="$LOG_DIR/monitor.log"
REPAIR_STAMP="$LOG_DIR/last_repair"

AUTOSTART_DIR="/var/lib/webosbrew/init.d"
AUTOSTART_ENTRY="50-custom-tveroff"
AUTOSTART_SCRIPT="$TOOLS_DIR/autostart.sh"

mkdir -m 777 -p "$BASE" "$LOG_DIR" 2>/dev/null

log() {
  [ -f "$LOG" ] || : > "$LOG"
  echo "$(date +%H:%M:%S) $*" >> "$LOG"
}

die() {
  log "ERROR: $*"
  exit 1
}

require_root() {
  [ "$(id -u 2>/dev/null)" = "0" ] || die "not root (must be run through the Homebrew Channel exec service)"
}

# luna-send reads stdin if it is a pipe/tty, so always detach it; never wait forever on the bus.
luna() {
  timeout 8 luna-send -n 1 "$@" </dev/null 2>/dev/null
}

toast() {
  luna luna://com.webos.notification/createToast \
    "{\"message\":\"$1\",\"iconUrl\":\"$ICON\",\"sourceId\":\"$APP_ID\"}" >/dev/null
}

# ---- TVer app --------------------------------------------------------------------------------------

tver_installed() { [ -d "$TVER_DIR" ] && [ -f "$TVER_DIR/appinfo.json" ]; }

tver_version() {
  grep -o '"version" *: *"[^"]*"' "$TVER_DIR/appinfo.json" 2>/dev/null | head -1 | cut -d'"' -f4
}

tver_running() {
  luna luna://com.webos.applicationManager/running '{}' | grep -q "\"id\":\"$TVER_ID\""
}

tver_close() { luna luna://com.webos.applicationManager/closeByAppId "{\"id\":\"$TVER_ID\"}" >/dev/null; }

tver_launch() { luna luna://com.webos.applicationManager/launch "{\"id\":\"$TVER_ID\"}" >/dev/null; }

# ---- the patch (one file inside the TVer app dir) -----------------------------------------------------

patch_present() { [ -f "$INJECT_FILE" ]; }

# installed file is byte-identical to the payload we ship
patch_current() { [ -f "$INJECT_FILE" ] && [ -f "$PAYLOAD" ] && cmp -s "$PAYLOAD" "$INJECT_FILE"; }

payload_version() {
  grep -o "VERSION = '[^']*'" "$1" 2>/dev/null | head -1 | cut -d"'" -f2
}

patch_install() {
  [ -f "$PAYLOAD" ] || { log "payload missing: $PAYLOAD"; return 1; }
  mkdir -p "$INJECT_DIR" || { log "cannot create $INJECT_DIR"; return 1; }
  chmod 755 "$INJECT_DIR"
  tmp="$INJECT_DIR/.userScript.js.$$"
  if cp "$PAYLOAD" "$tmp" && chmod 644 "$tmp" && mv -f "$tmp" "$INJECT_FILE"; then
    :
  else
    rm -f "$tmp"
    log "cannot write $INJECT_FILE"
    return 1
  fi
  if patch_current; then
    log "patch installed (payload v$(payload_version "$PAYLOAD"))"
    return 0
  fi
  log "installed patch differs from payload"
  return 1
}

patch_remove() {
  if [ -e "$INJECT_FILE" ]; then
    rm -f "$INJECT_FILE" && log "patch removed"
  fi
  rmdir "$INJECT_DIR" 2>/dev/null
  return 0
}

# ---- background processes ------------------------------------------------------------------------------

pid_alive() { [ -n "$1" ] && kill -0 "$1" 2>/dev/null; }

read_pidfile() { [ -f "$1" ] && cat "$1" 2>/dev/null; }

# Start "$@" detached from the caller: new session, no inherited fds (the Homebrew Channel exec call
# only returns when every inherited pipe is closed, and init.d scripts must not block boot).
daemonize() {
  log_daemon="$1"; shift
  echo "starting pid=$$ $*" > "$log_daemon"
  setsid sh -c '
    log_daemon="$1"; shift
    exec </dev/null >>"$log_daemon" 2>&1
    for fdpath in /proc/$$/fd/*; do
      fd="${fdpath##*/}"
      case "$fd" in
        0|1|2) ;;
        *) eval "exec ${fd}>&- ${fd}<&-" 2>/dev/null ;;
      esac
    done
    exec "$@"
  ' sh "$log_daemon" "$@" &
}
