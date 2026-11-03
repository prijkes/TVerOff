#!/bin/sh
# TVerOff controller.
#
# Called by the app UI through the Homebrew Channel exec service, by the init.d hook at boot, or by hand
# over SSH:   tverctl.sh start | stop | status | autostart_toggle | restart-tver
#
# Prints one JSON line for the UI:
#   {"state":{"autostart":bool,"service":"running|stopped|error","logs":"<base64>", ...}}

TOOLS_DIR="$(dirname "$(readlink -f "$0")")"
. "$TOOLS_DIR/lib/common.sh"
. "$TOOLS_DIR/lib/monitor.sh"

require_root

jbool() { if "$@"; then echo true; else echo false; fi; }

autostart_enabled() { [ -L "$AUTOSTART_DIR/$AUTOSTART_ENTRY" ] || [ -e "$AUTOSTART_DIR/$AUTOSTART_ENTRY" ]; }

# running = TVer's folder carries exactly the payload we ship; the monitor is a bonus, not a requirement.
SERVICESTATE="unknown"
compute_state() {
  if ! tver_installed; then
    SERVICESTATE="error"
  elif patch_current; then
    SERVICESTATE="running"
  elif patch_present; then
    SERVICESTATE="error"
  else
    SERVICESTATE="stopped"
  fi
}

print_state() {
  compute_state
  b64="$(base64 < "$LOG" | tr -d '\n')"
  printf '{"state":{"autostart":%s,"service":"%s","logs":"%s","tver":{"installed":%s,"version":"%s"},"patch":{"present":%s,"current":%s},"monitor":%s}}\n' \
    "$(jbool autostart_enabled)" "$SERVICESTATE" "$b64" \
    "$(jbool tver_installed)" "$(tver_version)" \
    "$(jbool patch_present)" "$(jbool patch_current)" \
    "$(jbool monitor_running)"
}

status_log() {
  if tver_installed; then
    log "TVer app: installed (v$(tver_version))"
  else
    log "TVer app: NOT installed ($TVER_DIR missing)"
  fi
  if patch_current; then
    log "patch: installed, up to date (v$(payload_version "$INJECT_FILE"))"
  elif patch_present; then
    log "patch: installed but outdated (v$(payload_version "$INJECT_FILE") vs v$(payload_version "$PAYLOAD")) - press Start"
  else
    log "patch: not installed"
  fi
  if monitor_running; then
    log "monitor: running (pid $(read_pidfile "$MONITOR_PID"))"
  else
    log "monitor: not running"
  fi
  if autostart_enabled; then log "autostart: enabled"; else log "autostart: disabled"; fi
}

case "$1" in
  start)
    : > "$LOG"
    log "starting TVerOff v$(payload_version "$PAYLOAD")"
    if ! tver_installed; then
      log "ERROR: TVer ($TVER_ID) is not installed on this TV"
    elif patch_install; then
      monitor_start
      # boot: nothing is running yet. interactive: a running TVer has the old script cached in memory.
      if [ "$2" != "boot" ] && tver_running; then
        log "closing the running TVer so it loads the patch on next launch"
        tver_close
      fi
      log "Start successful"
    else
      log "Start failed"
    fi
    ;;
  stop)
    : > "$LOG"
    log "stopping TVerOff"
    monitor_stop
    patch_remove
    if tver_running; then
      log "closing the running TVer so it loads unpatched on next launch"
      tver_close
    fi
    log "Stop successful"
    ;;
  status)
    : > "$LOG"
    status_log
    ;;
  autostart_toggle)
    : > "$LOG"
    if autostart_enabled; then
      rm -f "$AUTOSTART_DIR/$AUTOSTART_ENTRY" && log "Removed autostart"
    else
      mkdir -p "$AUTOSTART_DIR" && ln -sf "$AUTOSTART_SCRIPT" "$AUTOSTART_DIR/$AUTOSTART_ENTRY" && log "Set autostart"
    fi
    ;;
  restart-tver)
    : > "$LOG"
    tver_close
    sleep 2
    tver_launch
    log "TVer restarted"
    ;;
  monitor-run)
    monitor_run
    exit 0
    ;;
  *)
    : > "$LOG"
    log "usage: tverctl.sh start|stop|status|autostart_toggle|restart-tver"
    print_state
    exit 1
    ;;
esac

print_state
exit 0
