#!/bin/sh
# TVerOff - foreground monitor.
#
# The patch itself is passive (one file, loaded by WAM when TVer starts) so nothing has to run for ads to
# be blocked. The monitor only adds two conveniences:
#   * a toast when TVer comes to the foreground, and
#   * self-healing: an LG Content Store update of TVer replaces its folder (and our file with it); when
#     TVer is opened in that state we put the patch back and restart TVer once.

monitor_running() { pid_alive "$(read_pidfile "$MONITOR_PID")"; }

mlog() { echo "$(date +%H:%M:%S) $*" >> "$MONITOR_LOG"; }

monitor_start() {
  if monitor_running; then
    log "monitor already running (pid $(read_pidfile "$MONITOR_PID"))"
    return 0
  fi
  daemonize "$MONITOR_LOG" "$TOOLS_DIR/tverctl.sh" monitor-run
  i=0
  while [ $i -lt 20 ]; do
    monitor_running && { log "monitor started (pid $(read_pidfile "$MONITOR_PID"))"; return 0; }
    sleep 0.2
    i=$((i + 1))
  done
  log "monitor did not start (see $MONITOR_LOG)"
  return 1
}

monitor_stop() {
  pid="$(read_pidfile "$MONITOR_PID")"
  if pid_alive "$pid"; then
    # the monitor is a session leader (setsid): signal the whole group so luna-send goes down with it
    kill -TERM "-$pid" 2>/dev/null || kill -TERM "$pid" 2>/dev/null
    i=0
    while pid_alive "$pid" && [ $i -lt 20 ]; do sleep 0.2; i=$((i + 1)); done
    pid_alive "$pid" && kill -KILL "-$pid" 2>/dev/null
    log "monitor stopped"
  fi
  rm -f "$MONITOR_PID"
  return 0
}

# Our own files are gone: the app was uninstalled while the service was running.
monitor_orphaned() { [ ! -f "$PAYLOAD" ] || [ ! -f "$TOOLS_DIR/tverctl.sh" ]; }

# Leave nothing behind: without this the patch would keep working with no way left to switch it off.
monitor_forget() {
  mlog "TVerOff was uninstalled - removing the patch and the autostart entry"
  rm -f "$INJECT_FILE"
  rmdir "$INJECT_DIR" 2>/dev/null
  rm -f "$AUTOSTART_DIR/$AUTOSTART_ENTRY" "$MONITOR_PID"
  # a running TVer still holds the patch in memory; do not yank it away if the user is watching it
  [ "$1" != "$TVER_ID" ] && tver_close
  kill -TERM "-$$" 2>/dev/null
  exit 0
}

on_tver_foreground() {
  if patch_current; then
    mlog "TVer opened, patch in place"
    toast "TVerOff: ad-free playback is active"
    return 0
  fi

  # repair at most once a minute so a broken install can never turn into a restart loop
  now="$(date +%s)"
  last="$(cat "$REPAIR_STAMP" 2>/dev/null)"
  if [ -n "$last" ] && [ $((now - last)) -lt 60 ]; then
    mlog "TVer opened without the patch, repair skipped (done $((now - last))s ago)"
    return 0
  fi
  echo "$now" > "$REPAIR_STAMP"

  mlog "TVer opened without the patch (app updated?) - repairing"
  if patch_install >/dev/null 2>&1; then
    mlog "patch restored, restarting TVer"
    toast "TVerOff: patch restored, restarting TVer"
    tver_close
    sleep 2
    tver_launch
  else
    mlog "could not restore the patch"
    toast "TVerOff: could not restore the patch"
  fi
}

# long-running; started through daemonize as "tverctl.sh monitor-run"
monitor_run() {
  echo $$ > "$MONITOR_PID"
  trap 'rm -f "$MONITOR_PID"; exit 0' TERM INT
  while :; do
    prev=""
    luna-send -i luna://com.webos.applicationManager/getForegroundAppInfo '{"subscribe":true}' </dev/null 2>/dev/null |
    while IFS= read -r line; do
      app="$(echo "$line" | sed -n 's/.*"appId" *: *"\([^"]*\)".*/\1/p')"
      [ -n "$app" ] || continue
      if monitor_orphaned; then
        # an upgrade replaces the app folder for a moment: only act if it is still gone after a grace period
        sleep 30
        monitor_orphaned && monitor_forget "$app"
      fi
      if [ "$app" = "$TVER_ID" ] && [ "$prev" != "$TVER_ID" ]; then
        on_tver_foreground
      fi
      prev="$app"
    done
    # subscription ended (luna bus restarted?) - try again shortly
    sleep 5
  done
}
