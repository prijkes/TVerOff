#!/usr/bin/env python3
"""On-device smoke test: does the INSTALLED TVerOff payload still defeat TVer's ad insertion?

TVer redeploys its web app all the time. Run this after a TVer/webOS update (or whenever the red
"ads not blocked" badge shows up) to find out in about a minute whether the patch still works.

  set TV_HOST=<tv-ip>
  python development/smoke_test.py [--count N] [--shots DIR]

It plays the first N distinct episodes TVer offers on the home screen (default 1; muted, ~15 s each; usually
different broadcasters = different STREAKS projects) and asserts, for every one:
  * the payload rewrote the playback request (ssai=1 -> ssai=0)
  * no ad-stitched stream was requested (ssai-manifest / ssai/session / ssai/ad_info)
  * the plain manifest was used and no ad-break beacons fired
  * the video is really playing
and finally restores the TV to exactly the state it found (service on/off, no bridge left behind).
Side effects to know about: every episode played here is a real playback for the TVer profile (a few seconds of
viewing history, and TVer's own `totalPlayCount` goes up). After a number of plays TVer starts asking its 3-step
survey (gender / viewing environment) before *every* episode until someone answers it on the TV with the remote.
The test never answers it: it skips such episodes, and stops with a message if TVer asks for it twice in a row.
So: keep --count small, and answer the survey once on the TV if you hit it.
"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tvlink import TV, Bridge, TVER_ID, TVER_DIR  # noqa: E402

PAYLOAD = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src", "payload", "userScript.js")

# %(idx)d = which of the distinct episode links on the home screen to play
JS_OPEN = r"""
var P = HTMLMediaElement.prototype.play;
HTMLMediaElement.prototype.play = function () { try { this.muted = true; } catch (e) {} return P.apply(this, arguments); };
var seen = {}, links = [];
Array.prototype.forEach.call(document.querySelectorAll('a[href^="/ostvweb/csr/episodes/"]'), function (x) {
  var h = x.getAttribute('href'); if (!seen[h]) { seen[h] = 1; links.push(x); }
});
var a = links[%(idx)d];
if (!a) return { err: 'episode link #%(idx)d not found on ' + location.href + ' (' + links.length + ' links) - TVer changed its home page, or use a smaller --count' };
a.click();
await new Promise(function (r) { setTimeout(r, 3500); });
var btn = Array.prototype.slice.call(document.querySelectorAll('button')).filter(function (b) { return /\u6700\u521D\u304B\u3089\u518D\u751F/.test(b.textContent || ''); })[0]
       || Array.prototype.slice.call(document.querySelectorAll('button')).filter(function (b) { return /\u518D\u751F/.test(b.textContent || ''); })[0];
if (!btn) return { err: 'no play button on ' + location.href + ' - update the selector here' };
btn.click();
await new Promise(function (r) { setTimeout(r, 1500); });
// some episodes first show TVer's own 3-step survey; playback only starts after someone answers it with the remote
if (/\u30A2\u30F3\u30B1\u30FC\u30C8/.test(document.body.innerText || '')) return { skipped: 'TVer survey dialog', clicked: a.getAttribute('href') };
return { clicked: a.getAttribute('href') };
"""

JS_STATE = r"""
var T = window.__TVEROFF__, v = document.querySelector('video');
var res = performance.getEntriesByType('resource').map(function (r) { return r.name; });
function cnt(re) { return res.filter(function (n) { return re.test(n); }).length; }
var pb = res.filter(function (n) { return /playback\.api\.streaks/.test(n); }).map(function (n) { return n.replace(/(ati)=[^&]*/, '$1=...'); });
return {
  href: location.href,
  totalPlayCount: window.localStorage.getItem('totalPlayCount'),
  payload: T ? { version: T.version, rewrites: T.rewrites, ssaiSeen: T.ssaiSeen } : null,
  video: v ? { t: +v.currentTime.toFixed(1), d: +v.duration.toFixed(1), paused: v.paused } : null,
  playbackRequests: pb,
  project: (pb[0] || '').replace(/^.*\/projects\/([^\/]+)\/.*$/, '$1'),
  ssaiManifest: cnt(/ssai-manifest\./), ssaiSession: cnt(/\/ssai\/session/), adInfo: cnt(/\/ssai\/ad_info/),
  plainManifest: cnt(/\/\/manifest\.streaks\.jp\//), adBeacons: cnt(/tver-metrics[^?]*\/(adBreakStart|adStart)/)
};
"""

# a full navigation: fresh document, fresh performance buffer, user script runs again
JS_HOME = r"""
var v = document.querySelector('video'); if (v) { try { v.pause(); } catch (e) {} }
setTimeout(function () { location.href = '/ostvweb/csr/home'; }, 200);
return 'ok';
"""


def evaluate(result):
    p, v = result["payload"], result["video"]
    return [
        ("payload loaded in the page", p is not None),
        ("playback request rewritten to ssai=0", bool(p) and p["rewrites"] >= 1 and any("ssai=0" in u for u in result["playbackRequests"])),
        ("no ad-stitched stream requested", result["ssaiManifest"] == 0 and result["ssaiSession"] == 0 and result["adInfo"] == 0 and (not p or p["ssaiSeen"] == 0)),
        ("plain manifest used", result["plainManifest"] >= 1),
        ("no ad-break beacons", result["adBeacons"] == 0),
        ("video is playing", bool(v) and v["t"] > 3 and not v["paused"]),
    ]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--count", type=int, default=1, help="how many distinct episodes to play")
    ap.add_argument("--shots", help="save screenshots of the player into this directory")
    args = ap.parse_args()
    if args.shots:
        os.makedirs(args.shots, exist_ok=True)

    tv = TV()
    state = tv.ctl("status")
    if not state["tver"]["installed"]:
        sys.exit("TVer is not installed on this TV")
    if not os.path.exists(PAYLOAD):
        sys.exit("payload not found: " + PAYLOAD)
    payload = open(PAYLOAD, encoding="utf-8").read()
    was_running = state["service"] == "running"
    print("service was %s, autostart %s" % (state["service"], state["autostart"]))

    bridge = Bridge(tv, TVER_DIR, prefix=payload)
    failures = []
    try:
        # the monitor would 'repair' (overwrite) the bridged copy of the payload, so pause it
        tv.run("p=$(cat /tmp/tveroff/monitor.pid 2>/dev/null); [ -n \"$p\" ] && kill -TERM -$p; sleep 1; rm -f /tmp/tveroff/monitor.pid; true")
        tv.close(TVER_ID)
        bridge.attach()
        time.sleep(1)
        tv.launch(TVER_ID)
        if not bridge.wait_page("csr/home", 90):
            sys.exit("TVer's home page did not come up (login/EULA prompt? network?)")

        played, idx, surveys_in_a_row = 0, 0, 0
        while played < args.count and idx < args.count * 3 + 2:
            time.sleep(3)
            opened = json.loads(bridge.eval(JS_OPEN % {"idx": idx}))
            idx += 1
            if "err" in opened:
                failures.append(opened["err"])
                break
            if "skipped" in opened:
                surveys_in_a_row += 1
                print("\nskipped %s: TVer is asking its survey before playing" % opened["clicked"])
                if surveys_in_a_row >= 2:
                    failures.append("TVer asks its survey before every episode (after enough plays it does, until it is answered): "
                                    "answer it once on the TV with the remote, then run this again")
                    break
            else:
                surveys_in_a_row = 0
                played += 1
                if args.shots:
                    time.sleep(0.3)
                    tv.screenshot(os.path.join(args.shots, "ep%d_start.jpg" % played))
                    time.sleep(11)
                else:
                    time.sleep(11.5)
                result = json.loads(bridge.eval(JS_STATE))
                print("\nepisode #%d  %s  project=%s  video t=%s/d=%s  (TVer's totalPlayCount now %s)" % (
                    played, opened["clicked"], result["project"], (result["video"] or {}).get("t"), (result["video"] or {}).get("d"),
                    result.get("totalPlayCount")))
                for name, ok in evaluate(result):
                    print("  %-4s %s" % ("ok" if ok else "FAIL", name))
                    if not ok:
                        failures.append("#%d: %s" % (played, name))
            if played < args.count:
                tv.run(": > /tmp/tveroff-bridge/hits.log")
                bridge.eval(JS_HOME)
                if not bridge.wait_page("csr/home", 40):
                    failures.append("did not get back to the home page")
                    break
        if not failures and played < args.count:
            failures.append("only %d of %d episodes could be played" % (played, args.count))
    finally:
        bridge.detach()
        tv.close(TVER_ID)
        # put the TV back exactly as found
        if was_running:
            tv.ctl("start", "boot")
        else:
            tv.ctl("stop")

    after = tv.ctl("status")
    print("\nrestored: service=%s autostart=%s" % (after["service"], after["autostart"]))
    tv.close_connection()
    if failures:
        print("\nSMOKE TEST FAILED: " + "; ".join(failures))
        sys.exit(1)
    print("\nSMOKE TEST PASSED (%d episode%s)" % (args.count, "" if args.count == 1 else "s"))


if __name__ == "__main__":
    main()
