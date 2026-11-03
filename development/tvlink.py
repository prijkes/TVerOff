#!/usr/bin/env python3
"""Developer tooling for a rooted webOS TV. Not needed to *use* TVerOff.

  set TV_HOST=<tv-ip>                  (TV_PASS defaults to "alpine", the Homebrew Channel SSH default)
  python development/tvlink.py install dist/com.tveroff.patcher_0.1.0_all.ipk
  python development/tvlink.py remove
  python development/tvlink.py ctl status        # runs the installed tverctl.sh (status|start|stop|autostart_toggle)
  python development/tvlink.py shot out.jpg      # screenshot of what the TV is showing right now
  python development/tvlink.py run "uname -a"

As a library it also provides `Bridge`: a temporary, loopback-only JS channel into the page of a webOS web app,
built on the same webOSUserScripts hook TVerOff uses (see NOTES.md). smoke_test.py uses it.

Needs:  pip install paramiko
"""
import json
import os
import sys
import time
import uuid

import paramiko

TVER_ID = "jp.co.tver.tvapp"
TVER_DIR = "/media/cryptofs/apps/usr/palm/applications/" + TVER_ID
APP_ID = "com.tveroff.patcher"
CTL = "/media/developer/apps/usr/palm/applications/%s/tools/tverctl.sh" % APP_ID


class TV:
    def __init__(self, host=None, password=None):
        host = host or os.environ.get("TV_HOST")
        if not host:
            sys.exit("set TV_HOST to the TV's IP address (and TV_PASS if it is not 'alpine')")
        self.c = paramiko.SSHClient()
        self.c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        self.c.connect(host, username="root", password=password or os.environ.get("TV_PASS", "alpine"),
                       look_for_keys=False, allow_agent=False, timeout=15)
        self._sftp = None

    @property
    def sftp(self):
        if self._sftp is None:
            self._sftp = self.c.open_sftp()
        return self._sftp

    def run(self, cmd, timeout=60):
        """Run a shell command; returns stdout (+ stderr if any). stdin is detached for the whole command:
        luna-send reads stdin and would otherwise eat or hang on it."""
        _, o, e = self.c.exec_command("exec </dev/null; " + cmd, timeout=timeout)
        out, err = o.read().decode("utf-8", "replace"), e.read().decode("utf-8", "replace")
        return out + (("\n[stderr] " + err) if err.strip() else "")

    def put(self, remote, text, mode=0o644):
        with self.sftp.open(remote, "wb") as f:
            f.write(text.replace("\r\n", "\n").encode("utf-8"))
        self.sftp.chmod(remote, mode)

    def get(self, remote, local):
        self.sftp.get(remote, local)

    def exists(self, remote):
        try:
            self.sftp.stat(remote)
            return True
        except IOError:
            return False

    def read(self, remote):
        with self.sftp.open(remote, "rb") as f:
            return f.read().decode("utf-8", "replace")

    def luna(self, uri, payload="{}"):
        return self.run("luna-send -n 1 %s '%s'" % (uri, payload))

    def launch(self, app_id):
        self.luna("luna://com.webos.applicationManager/launch", json.dumps({"id": app_id}))

    def close(self, app_id):
        self.luna("luna://com.webos.applicationManager/closeByAppId", json.dumps({"id": app_id}))

    def ctl(self, *args):
        """Run the installed tverctl.sh and return its parsed {"state": ...}."""
        out = self.run(CTL + " " + " ".join(args)).strip().splitlines()
        for line in reversed(out):
            if line.startswith('{"state"'):
                return json.loads(line)["state"]
        raise RuntimeError("tverctl.sh printed no state:\n" + "\n".join(out))

    def screenshot(self, local):
        self.run("rm -f /tmp/.shot.jpg; luna-send -n 1 luna://com.webos.service.capture/executeOneShot "
                 "'{\"path\":\"/tmp/.shot.jpg\",\"method\":\"DISPLAY\",\"width\":960,\"height\":540,\"format\":\"JPG\",\"quality\":85}'")
        time.sleep(0.4)
        self.get("/tmp/.shot.jpg", local)
        self.run("rm -f /tmp/.shot.jpg")

    def _stream(self, uri, payload, done, timeout):
        """Run a subscribing luna-send until `done` (a substring) shows up in its output; returns the last lines.
        The pid is tracked explicitly: pgrep -f 'luna-send -i' would also hit TVerOff's own monitor subscription."""
        f = "/tmp/.luna-stream.out"
        pid = self.run("rm -f %s; luna-send -i %s '%s' >%s 2>&1 & echo $!" % (f, uri, payload, f)).strip()
        t0, out = time.time(), ""
        while time.time() - t0 < timeout:
            out = self.run("cat %s 2>/dev/null" % f)
            if done in out or "errorText" in out or '"returnValue":false' in out:
                break
            time.sleep(1)
        self.run("kill %s 2>/dev/null; rm -f %s" % (pid, f))
        return "\n".join(out.strip().splitlines()[-2:])

    def install(self, ipk, timeout=60):
        """Install/upgrade an IPK with appInstallService/dev/install (what Homebrew Channel and Dev Manager call)."""
        remote = "/tmp/" + os.path.basename(ipk)
        self.sftp.put(ipk, remote)
        app = os.path.basename(ipk).split("_")[0]
        out = self._stream("luna://com.webos.appInstallService/dev/install",
                           '{"id":"%s","ipkUrl":"%s","subscribe":true}' % (app, remote), '"state":"installed"', timeout)
        self.run("rm -f " + remote)
        return out

    def remove(self, app_id=APP_ID, timeout=40):
        return self._stream("luna://com.webos.appInstallService/dev/remove",
                            '{"id":"%s","subscribe":true}' % app_id, '"state":"removed"', timeout)

    def close_connection(self):
        self.c.close()


# ---- Bridge: run JS inside a webOS web app's page ---------------------------------------------------------

_TB = "/tmp/tveroff-bridge"
_PORT = 18888

_SERVER = r'''
import http.server, os, time, threading, glob, urllib.parse, select, socket
TB = "%(tb)s"
for d in ("queue", "out"):
    os.makedirs(os.path.join(TB, d), exist_ok=True)
LOCK = threading.Lock()
class H(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"
    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Private-Network", "true")
        self.send_header("Access-Control-Allow-Headers", "*")
    def do_OPTIONS(self):
        self.send_response(204); self._cors(); self.end_headers()
    def _log(self, line):
        with LOCK, open(os.path.join(TB, "hits.log"), "a") as f:
            f.write("%%s %%s\n" %% (time.strftime("%%H:%%M:%%S"), line))
    def do_GET(self):
        u = urllib.parse.urlsplit(self.path); q = urllib.parse.parse_qs(u.query)
        if u.path != "/cmd":
            self.send_response(204); self._cors(); self.end_headers(); return
        self._log("poll href=%%s" %% q.get("href", ["?"])[0][:200])
        deadline = time.time() + 20
        while time.time() < deadline:
            try:   # a page that navigated away must not swallow commands
                r, _, _ = select.select([self.connection], [], [], 0)
                if r and self.connection.recv(1, socket.MSG_PEEK) == b"":
                    return
            except Exception:
                return
            files = sorted(glob.glob(os.path.join(TB, "queue", "*.js")))
            if files:
                body = open(files[0], "rb").read(); os.remove(files[0])
                try:
                    self.send_response(200); self._cors()
                    self.send_header("Content-Type", "text/javascript; charset=utf-8"); self.send_header("Cache-Control", "no-store")
                    self.end_headers(); self.wfile.write(body)
                except Exception:
                    open(files[0], "wb").write(body)
                return
            time.sleep(0.25)
        self.send_response(200); self._cors(); self.send_header("Content-Type", "text/javascript"); self.end_headers(); self.wfile.write(b"/*noop*/")
    def do_POST(self):
        u = urllib.parse.urlsplit(self.path); q = urllib.parse.parse_qs(u.query)
        n = int(self.headers.get("Content-Length") or 0); body = self.rfile.read(n) if n else b""
        if u.path == "/r":
            rid = q["id"][0]; i = int(q.get("n", ["0"])[0])
            open(os.path.join(TB, "out", "%%s.%%04d" %% (rid, i)), "wb").write(body)
            if q.get("last", ["0"])[0] == "1":
                open(os.path.join(TB, "out", rid + ".done"), "w").write(str(i))
        self.send_response(204); self._cors(); self.end_headers()
    def log_message(self, *a): pass
class S(http.server.ThreadingHTTPServer):
    daemon_threads = True; allow_reuse_address = True
S(("127.0.0.1", %(port)d), H).serve_forever()
''' % {"tb": _TB, "port": _PORT}

_STUB = r'''
(function () {
  try {
    if (window.__tbridge) return;
    var B = "http://127.0.0.1:%(port)d", cid = Math.random().toString(36).slice(2, 8);
    window.__tbridge = { post: function (p, t) { try { fetch(B + p, { method: "POST", mode: "no-cors", body: t }); } catch (e) {} } };
    (function loop() {
      var s = document.createElement("script");
      s.src = B + "/cmd?cid=" + cid + "&href=" + encodeURIComponent(location.href) + "&t=" + Date.now();
      s.onload = function () { s.remove(); setTimeout(loop, 30); };
      s.onerror = function () { s.remove(); setTimeout(loop, 3000); };
      (document.head || document.documentElement).appendChild(s);
    })();
  } catch (e) {}
})();
''' % {"port": _PORT}

_WRAP = r'''(function (id) {
  function out(v) {
    try {
      var s = typeof v === "string" ? v : JSON.stringify(v);
      if (s === undefined) s = "undefined";
      for (var i = 0, n = 0; i < s.length || n === 0; i += 30000, n++)
        window.__tbridge.post("/r?id=" + id + "&n=" + n + "&last=" + (i + 30000 >= s.length ? 1 : 0), s.slice(i, i + 30000));
    } catch (e) { window.__tbridge.post("/r?id=" + id + "&n=0&last=1", "ERR " + e); }
  }
  Promise.resolve().then(function () { return (async function () {
%s
  })(); }).then(out, function (e) { out("ERR " + (e && e.stack || e)); });
})("%s");
'''


class Bridge:
    """Temporary JS channel into an app's page: a loopback server on the TV + the app's own webOSUserScripts hook.

    The script file is read when the app *launches*, so launch (or relaunch) the app after attach().
    Always call detach(): it removes the whole webOSUserScripts directory of the app.
    """

    def __init__(self, tv, app_dir=TVER_DIR, prefix=""):
        self.tv, self.app_dir, self.prefix = tv, app_dir, prefix

    def attach(self):
        tv = self.tv
        # '[s]erver.py': the pattern must not match the shell that is running this very command line
        tv.run("for p in $(pgrep -f 'tveroff-bridge/[s]erver.py'); do kill $p; done; sleep 1; rm -rf %s; mkdir -p %s/queue %s/out" % (_TB, _TB, _TB))
        tv.put(_TB + "/server.py", _SERVER)
        tv.run("setsid nohup python3 %s/server.py >%s/server.log 2>&1 &" % (_TB, _TB))
        time.sleep(1.2)
        tv.run("mkdir -p %s/webOSUserScripts && chmod 755 %s/webOSUserScripts" % (self.app_dir, self.app_dir))
        tv.put(self.app_dir + "/webOSUserScripts/userScript.js", self.prefix + "\n" + _STUB)

    def detach(self):
        self.tv.run("for p in $(pgrep -f 'tveroff-bridge/[s]erver.py'); do kill $p; done; "
                    "rm -rf %s/webOSUserScripts %s" % (self.app_dir, _TB))

    def wait_page(self, contains, timeout=60):
        t0 = time.time()
        while time.time() - t0 < timeout:
            if contains in self.tv.run("tail -n 4 %s/hits.log 2>/dev/null" % _TB):
                return True
            time.sleep(1)
        return False

    def eval(self, code, timeout=40):
        """Run `code` (the body of an async function; use `return`) in the page. Returns the result as text."""
        rid = uuid.uuid4().hex[:8]
        self.tv.put("%s/queue/%.3f.tmp" % (_TB, time.time()), _WRAP % (code, rid))
        self.tv.run("cd %s/queue && for f in *.tmp; do mv $f ${f%%.tmp}.js; done" % _TB)
        t0 = time.time()
        while time.time() - t0 < timeout:
            if self.tv.exists("%s/out/%s.done" % (_TB, rid)):
                last = int(self.tv.read("%s/out/%s.done" % (_TB, rid)).strip() or 0)
                return "".join(self.tv.read("%s/out/%s.%04d" % (_TB, rid, i)) for i in range(last + 1))
            time.sleep(0.7)
        raise TimeoutError("no answer from the page (not connected, or busy). Last polls:\n" +
                           self.tv.run("tail -n 4 %s/hits.log" % _TB))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    tv = TV()
    cmd, args = sys.argv[1], sys.argv[2:]
    if cmd == "install":
        print(tv.install(args[0]))
    elif cmd == "remove":
        print(tv.remove())
    elif cmd == "ctl":
        print(json.dumps(tv.ctl(*args), indent=1))
    elif cmd == "shot":
        tv.screenshot(args[0]); print("saved", args[0])
    elif cmd == "run":
        print(tv.run(" ".join(args)))
    else:
        sys.exit(__doc__)
    tv.close_connection()
