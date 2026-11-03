#!/usr/bin/env python3
"""On-device lifecycle test: install -> upgrade in place -> uninstall, with the service running.

  set TV_HOST=<tv-ip>
  python development/lifecycle_test.py dist/com.tveroff.patcher_0.1.0_all.ipk

Checks that
  * a fresh install can be started (patch file byte-identical to the payload, monitor alive, autostart entry set),
  * an upgrade in place does NOT make the monitor think the app was uninstalled (files vanish for a moment),
  * a real uninstall while the service runs leaves nothing behind (patch file, autostart entry, monitor),
and then reinstalls the IPK and puts the service back into the state it found.
Takes ~3 minutes; briefly opens and closes TVer on the TV a few times.
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tvlink import TV, TVER_ID, TVER_DIR, APP_ID  # noqa: E402

APP_DIR = "/media/developer/apps/usr/palm/applications/" + APP_ID
PATCH = TVER_DIR + "/webOSUserScripts/userScript.js"
BOOT = "/var/lib/webosbrew/init.d/50-custom-tveroff"
LOG_DIR = "/var/lib/webosbrew/tveroff"
failures = []


def check(name, ok):
    print("  %-4s %s" % ("ok" if ok else "FAIL", name))
    if not ok:
        failures.append(name)


def screen_event(tv):
    """Make the foreground app change (the monitor reacts to those)."""
    tv.launch(TVER_ID)
    time.sleep(4)
    tv.close(TVER_ID)
    time.sleep(1)


def monitor_alive(tv):
    return tv.run("p=$(cat /tmp/tveroff/monitor.pid 2>/dev/null); [ -n \"$p\" ] && kill -0 $p && echo yes || echo no").strip() == "yes"


def wait_installed(tv, present, timeout=60):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if tv.exists(APP_DIR + "/tools/tverctl.sh") == present:
            return True
        time.sleep(1)
    return False


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    ipk = sys.argv[1]
    tv = TV()
    was_installed = tv.exists(APP_DIR + "/tools/tverctl.sh")
    before = tv.ctl("status") if was_installed else None
    print("before: " + ("not installed" if not before else "service=%s autostart=%s" % (before["service"], before["autostart"])))
    try:
        print("1. install (or upgrade in place) and start")
        tv.install(ipk)
        check("app files present after install", wait_installed(tv, True))
        tv.ctl("stop")                                   # make sure the NEW monitor code is the one running
        st = tv.ctl("start")
        check("service running", st["service"] == "running")
        check("patch file byte-identical to the shipped payload",
              tv.run("cmp %s/payload/userScript.js %s && echo same" % (APP_DIR, PATCH)).strip() == "same")
        check("monitor alive", monitor_alive(tv))
        if not tv.ctl("status")["autostart"]:
            tv.ctl("autostart_toggle")
        check("autostart entry set", tv.exists(BOOT) or tv.run("ls %s 2>/dev/null" % BOOT).strip() != "")

        print("2. files vanish for a moment (what an upgrade does) - must NOT be treated as an uninstall")
        # only the payload directory moves: moving the whole app folder could make SAM drop the app from its list
        tv.run(": > %s/monitor.log; mv %s/payload %s/payload.away" % (LOG_DIR, APP_DIR, APP_DIR))
        tv.launch(TVER_ID)
        time.sleep(6)
        tv.run("mv %s/payload.away %s/payload" % (APP_DIR, APP_DIR))
        time.sleep(36)                                   # > the monitor's 30 s grace period
        tv.close(TVER_ID)
        check("patch file still there", tv.exists(PATCH))
        check("autostart entry still there", tv.run("ls %s 2>/dev/null" % BOOT).strip() != "")
        check("monitor still alive", monitor_alive(tv))
        check("monitor did not log an uninstall", "uninstalled" not in tv.run("cat %s/monitor.log" % LOG_DIR))

        print("3. real uninstall while the service is running")
        last = tv.remove()
        print("   installer said: " + last.replace("\n", " ")[-260:])
        check("app files gone after remove", wait_installed(tv, False))
        check("(precondition) patch file is still there right after the removal", tv.exists(PATCH))
        screen_event(tv)
        time.sleep(40)
        check("patch file removed by the monitor", not tv.exists(PATCH))
        check("autostart entry removed by the monitor", tv.run("ls %s 2>/dev/null" % BOOT).strip() == "")
        check("monitor exited", not monitor_alive(tv))
        check("monitor logged why", "uninstalled" in tv.run("cat %s/monitor.log" % LOG_DIR))
    finally:
        print("4. restore")
        tv.close(TVER_ID)
        if not tv.exists(APP_DIR + "/tools/tverctl.sh"):
            tv.install(ipk)
            wait_installed(tv, True)
        if before and before["service"] == "running" or not before:
            tv.ctl("stop")
            tv.ctl("start")
        else:
            tv.ctl("stop")
        want_auto = before["autostart"] if before else True
        if tv.ctl("status")["autostart"] != want_auto:
            tv.ctl("autostart_toggle")
        after = tv.ctl("status")
        print("after: service=%s autostart=%s" % (after["service"], after["autostart"]))
    tv.close_connection()
    if failures:
        print("\nLIFECYCLE TEST FAILED: " + "; ".join(failures))
        sys.exit(1)
    print("\nLIFECYCLE TEST PASSED")


if __name__ == "__main__":
    main()
