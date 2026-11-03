# Field Notes: Building an Ad-Blocking Player Patch for LG webOS TVs

> **Purpose of this document.** Handoff notes for porting the [AmazOff](https://github.com/azoffshowy/AmazOff)
> approach (ad-blocking for the stock **Amazon Prime Video** app) to a *different streaming provider*
> — e.g. **TVer** (the Japanese free streaming service). Written after a full on-device port of AmazOff
> to webOS 11 / Amazon app 11.0.3 (PR: [azoffshowy/AmazOff#18](https://github.com/azoffshowy/AmazOff/pull/18)).
> Everything here was **verified on real hardware**: LG OLED55C2PJA, webOS 11.2.0-3704, rooted.

---

## 1. What we set out to do

AmazOff is a **patch-based customization layer** for the stock Prime Video app on rooted LG webOS TVs:

- It does **not** replace the target app. The app stays stock and keeps auto-updating its own native code.
- Instead it intercepts the app's download of its **player bundle** (a large JS player, `ATVUnfPlayerBundle.js`,
  fetched from the provider's CDN) and serves a **patched copy** with ad-logic disabled
  (`resolveWithAdBreaks` → always resolve to no breaks, playlist filters, cache paths forced `hasAds = false`, etc.).
- All launch paths (including the remote's dedicated button) keep working because the real app is still the app.

**The same idea applies to any provider whose app downloads its player/UI as JS** at runtime:
find the asset, reproduce/patch it, intercept its download, serve the patched one, and restore
everything on "stop".

**Goal for the TVer port:** an IPK, installed via Homebrew Channel / Dev Manager, that patches the
TVer app's player assets the same way, with a Start/Stop service and autostart.

---

## 2. What the issue was (why the existing AmazOff did not work on webOS 11)

The stock AmazOff 0.2.3 supported webOS 3.x–10.x and used this interception chain:

```
patch jail /etc/hosts  →  cloudfront.xp-assets.aiv-cdn.net resolves to 127.0.0.1
local nginx (port 443, self-signed cert + CA pinned into the app's cert store) serves the patched bundle
```

On webOS 11.2 (Amazon app 11.0.3) **every assumption of that chain broke**:

| # | Old assumption | webOS 11 reality | Consequence |
|---|---|---|---|
| 1 | `/etc/hosts` is writable | read-only rootfs (`mount: ro`) | hosts patch dies |
| 2 | app resolves via hosts file | app downloader is **curl 8.9 with c-ares**: resolves via system resolver, ignores `/etc/hosts` entirely | even a writable hosts file would be bypassed for bundle downloads |
| 3 | DNS hijack works | app ignores jail `resolv.conf`; connman resolves upstream | DNS redirect captures nothing |
| 4 | app trusts pinned CA after `disable-ssl-cert` | native downloader rejects self-signed TLS **regardless** of the config flag (webview honors it, curl does not) | MITM approaches fail (`tls: bad record MAC`) |
| 5 | app dir is a plain writable dir | app ships as **squashfs**, mounted as **overlayfs** only while the app runs (`upperdir=/media/data/data/<app>`) | `bin/certs` pin path only exists at runtime; also *persists* in upperdir across boots (useful!) |
| 6 | bundle URL is `family/lg/ATVUnfPlayerBundle-1.0/<gen>/js/...` with fixed gens | app now requests **versioned** `wave3` paths (`.../ATVUnfPlayerBundle-1.0.25364.0/wave3/js/...`), host delivered by config service | nginx never matched → 404 → app fell back to cached/CDN bundle |
| 7 | shipped nginx supports regex locations | **built without PCRE** → any `location ~` is a fatal config error | nginx fails to start |
| 8 | Homebrew Channel `exec` service callable from any app | Luna **ACG security groups** deny jailed apps on webOS 11 (HBC 0.7.3; fixed in HBC 0.8.0 / webosbrew#233) | app UI shows `Denied method call "exec" for category "/"` |

And two packaging traps that produced silently-broken builds:

- **`ares-package` minifies JS by default** → it re-minifies the already-minified player bundle,
  renaming/restructuring it (we verified: upstream's own released IPK ships a re-minified bundle).
  For byte-exact bundle shipping you must disable minify (`--no-minify` or patch the CLI default).
- **Windows checkout = CRLF + lost exec bits.** `ares-package` packs the working tree verbatim:
  CRLF broke busybox `sh` (`: not found` syntax errors), nginx configs, and inflated the bundle +32 KB;
  `git archive` on Windows strips `+x` modes. **Always build the IPK on Linux** (we used WSL Alpine).

---

## 3. How we fixed it (what works on webOS 11)

### 3.1 The winning interception: app-config proxy + CONNECT redirect

The one interception primitive that works on webOS 11 is an **app config key**:

```json
// injected into the target app dir as default_config.json (merged with existing config)
{
    "log-level": "ALL:WARNING",
    "disable-ssl-cert": "1",
    "http-proxy-server": "http://127.0.0.1:8998"
}
```

`http-proxy-server` is honored by the app's whole network stack (it's an ignition framework key).
Then run a tiny **CONNECT-redirect proxy** (Go, `tools/mitm/mitm`, ~200 lines with `goproxy`):

- every `CONNECT host:443` is answered `200` and **tunnelled byte-for-byte untouched** (no TLS MITM —
  the app's API/stream traffic is never disturbed, so nothing breaks);
- only the **player-bundle host** gets `CONNECT`-hijacked and piped into the local nginx instead,
  where the app does a *real* TLS handshake with nginx's cert (trusted via the pinned CA — see 3.2).

This avoids every failed approach (no DNS fight, no hosts file, no TLS re-encryption).
The goproxy primitive is `HandleConnectFunc` returning `ConnectAction{Action: ConnectHijack, Hijack: ...}`.

### 3.2 Cert trust: pin the CA into the app's overlay cert store

- While the app runs, `<appdir>/bin/certs/` is an OpenSSL **CApath-style** dir (hash-named files,
  e.g. `fe1de658.0` = the CA cert `openssl x509 -subject_hash` name).
- Writing the patcher CA there **persists in the overlay upperdir** (`/media/data/data/<app>/`)
  across app restarts *and* reboots (webOS commits overlay changes into the app dir).
- Fast path: check upperdir first → no app launch needed. Slow path (first run): launch the target app
  once to mount its overlay, copy the CA into `bin/certs/`, done forever.

### 3.3 nginx: serve the patched bundle

- One **prefix location** catches all bundle generation paths (the CDN serves the same current bundle
  on `onebox1`/`wave1`/`wave2`/`wave3`, versioned and plain — verified by direct fetches):

```nginx
location ^~ /family/lg/ATVUnfPlayerBundle-1.0/ {
    try_files /wave3/ATVUnfPlayerBundle.js =404;
}
```

- **No regex locations** (no PCRE in the shipped nginx). No `set`/`rewrite` either (no rewrite module).

### 3.4 Service + autostart

- `patchctl.sh start` = patch config + pin CA (+ jail hosts on ≤10) → start nginx → start redirect proxy
  (webOS 11) → start toast monitor daemon.
- Autostart: symlink into `/var/lib/webosbrew/init.d/` (HBC startup.sh runs `run-parts` on it at boot).
- Stop must tolerate the read-only `/etc/hosts` (log-and-continue, don't die mid-restore),
  and remove both hosts entries in **one awk pass** (two sequential awks into the same temp file
  truncate each other — leaves the IPv4 override behind; bug independently found in PR #12).

### 3.5 Luna permissions prerequisite (not app-specific)

On webOS 11 + HBC 0.7.3, jailed apps get `Denied method call "exec"`. Fixes (webosbrew ecosystem):
use HBC ≥ 0.8.0 (ships the fix), or manually register the api-permissions file **in the hub manifest**
(`manifests.d/org.webosbrew.hbchannel.json → apiPermissionFiles[] →
api-permissions.d/org.webosbrew.hbchannel.service.api.lunabus.query.json` containing
`{"lunabus.query":["org.webosbrew.hbchannel.service/*"]}`) and restart `ls-hubd` (or reboot).

---

## 4. Porting methodology for a new provider (TVer)

Do **not** assume Prime's architecture transfers. Discover it on-device, in this order:

1. **Root + SSH** — root via webosbrew ([cani.rootmy.tv](https://cani.rootmy.tv)); enable HBC SSH
   (`alpine` password) for root@TV:22. Apply the permission fix from §3.5 if you need the HBC `exec`
   service from your app UI.
2. **Map the app.** Find it: `/media/cryptofs/apps/usr/palm/applications/<appid>/`.
   Read `appinfo.json` first:
   - `"type": "native"` (like Prime 11.0.3, `main: bin/ignition`) → network interception needed (this doc).
   - `"type": "web"` (HTML/JS app) → **easier**: the JS lives on disk in the app dir (or in a mounted
     squashfs) — you may be able to patch files directly (see AmazOff's old `lovefilm`/webOS 3 approach)
     or redirect assets the same way.
   - Note `"fileSystemType": "squashfs"` → app dir is an overlay at runtime (§3.2 applies).
3. **Capture the app's traffic** with a *pure CONNECT relay* (we wrote `connect_relay.py`, ~80 lines:
   answer CONNECT, tunnel bytes, log the target host of every CONNECT). Inject it via the app config
   `http-proxy-server` key (or `default_config.json` equivalent for web apps). Play content.
   You get the full host list — including the player-asset host(s) and, importantly, **which hosts serve ads**.
   (Useful byproduct: telemetry/ad-beacon hosts you may also want to blackhole.)
4. **Identify the player + ad architecture.** For TVer specifically, expect **server-side ad insertion
   (SSAI)**: many Japanese services stitch ad segments into the HLS manifest server-side, so *no client
   JS patch can remove them* (the segments arrive inside the same manifest/playlist as content).
   Verify early: if ads are SSAI, a JS player patch won't cut it; options become (a) manifest-filtering
   proxy (rewrite m3u8 on the fly to drop ad segments — heavier, needs per-title logic), or
   (b) segment-sniffing + skip logic in the player, or (c) accept the limitation and patch only
   client-side ad logic (pauses, overlays, tracking beacons).
5. **Get the clean asset** (the JS bundle) — from the CDN host/path captured in step 3
   (`wget` it from any PC; check whether the path is generation/version-dependent).
6. **Patch it.** Pipeline that worked for the Prime bundle:
   - `js-beautify` (pin the version, e.g. 1.15.4) → readable source
   - locate ad-logic anchors (`grep` for ad-break resolvers, playlist filters, ad detection, seek
     interceptors, cache flags)
   - add a global flag (`__NOADS = !0`) + minimal early-returns at each anchor
   - `uglifyjs --no-annotations --keep-fargs --keep-fnames --no-rename --validate` → ship bytes
     **byte-identical to what you tested** (see packaging traps, §5)
   - keep the beautified pair + unified diff (`development/`) so the next bundle update is diffable.
   Each `patch` run **asserts exactly one match per anchor** — upstream renames minified identifiers
   on every release, so a static patch file will not apply to the next version. Consider PR #12's
   *structural transformer* (anchor-count + source-hash checked) as the more robust long-term approach.
7. **Serve it** — nginx with a prefix location (§3.3) + the CONNECT redirect (§3.1) + cert pin (§3.2).
   If the asset host serves other things too, prefer *exact-path* locations over a broad prefix so you
   only intercept the bundle and let the rest of that host pass through untouched.
8. **Service + autostart** (§3.4) + UI app calling the HBC `exec` service (or a `luna-send` on boot).
9. **Package on Linux** (§5) and **test from scratch** (§6).

---

## 5. Packaging: the traps that cost us hours

Build the IPK **on Linux** (WSL works):

```sh
# LF-clean source of the exact commit you tested:
git archive HEAD | tar -x -C ipkbuild
cd ipkbuild
ares-package --no-minify .          # or patch lib default: this.minify = false
```

Then **verify the IPK contents, not just the sources**:

```sh
ar x app.ipk && tar -xzf data.tar.gz -C out/
# exec bits present?
ls -l out/usr/palm/applications/<id>/tools/*
# no CR in scripts/configs/bundle?
tr -cd '\r' < <file> | wc -c          # must be 0
# sh syntax OK on-device (busybox sh!):
sh -n out/usr/palm/applications/<id>/tools/lib/*.sh
# bundle byte-identical to the tested dist:
sha256sum out/.../bundle.js < tested-dist.js
```

Checklist of failures we hit (all real, all verified):

- `ares-package` minifies JS by default → mangled bundle (upstream's own release ships mangled, too —
  it happens to still work because minify is semantics-preserving, but you lose byte-exactness).
- Windows working tree → CRLF in scripts (`sh: not found`, `unexpected "elif"`), configs, bundle (+32 KB).
- `git archive` on Windows → all modes 644 → `Permission denied` running `patchctl.sh` on device.
- A `.gitattributes` with `* text=auto eol=lf` + building from `git archive` on Linux fixes all three classes.

---

## 6. Testing protocol (from-scratch, like a user)

1. **Pristine reset** on-device: remove injected config, pinned CA (check **both** overlay upperdir
   `/media/data/data/<app>/` and the merged view — the pin persists and will fool you), restore jail hosts
   from the pre-patch backup, stop all daemons.
2. Fresh install of the IPK (via Dev Manager on webOS 11 — HBC's own install/remove buttons hit the
   same ACG bug and fail).
3. Start Service from the app UI → expect: first-run CA pin (may flash the target app briefly to mount
   its overlay), then `running`, nginx + redirect proxy + monitor daemons up.
4. **Cold-boot the TV** (not standby!) before judging ads: the app **caches the player bundle** in its
   `persist/` dir (MD5-named blobs) and will keep using the cached unpatched bundle otherwise.
5. Play ad-supported content across several titles; then reboot → verify autostart brings the chain up
   with zero user actions.
6. Stop Service → verify the target app still works *unpatched* (this catches the unpatch/hosts bugs).

---

## 7. Tools we built (all in the deploy scratch dir, reimplement freely)

| Tool | Purpose |
|---|---|
| `connect_relay.py` | pure CONNECT tunnel + host logger (step-3 discovery; zero-risk, no TLS touch) |
| `mitm` (Go, `tools/mitm/main.go`) | production redirect proxy: `REDIRECT_TARGET=127.0.0.1:443` → CONNECT-hijack only the bundle host, tunnel everything else; no expiry in redirect mode |
| `verify_ipk.sh` | IPK content verification (modes/CR/syntax/bundle hash) |
| `read_manifests.py`, `apply_233.py` | Luna permission diagnosis + the HBC manifest api-permissions registration fix |
| LF-safe upload helpers | any file that must run on-device: strip `\r`, upload via SFTP, `sh -n` on device |

On-device recon commands that kept paying off:

```sh
cat /proc/mounts | grep <appdir>                      # overlay upperdir location
for p in /proc/[0-9]*; do readlink $p/root; done     # find the running app's jail/pid
strings <appdir>/bin/* | grep -iE 'proxy|cert|url'   # config keys the app supports
cat <appdir>/persist/playbackCapabilities            # player bundle version on Prime
luna-send -n 1 luna://com.webos.applicationManager/launch '{"id":"<appid>"}'
```

---

## 8. References

- Upstream: https://github.com/azoffshowy/AmazOff (our port: PR #18; old-generation rework: PR #12 —
  read #12's `unpatch_hosts` bug + `--no-minify` notes even if you don't take its transformer)
- webosbrew rooting matrix: https://cani.rootmy.tv / https://www.webosbrew.org/
- HBC ACG bug: webosbrew/webos-homebrew-channel#255, fix PR #233 (shipped in HBC 0.8.0)
- Dev Manager (SSH install path on webOS 11): https://github.com/webosbrew/dev-manager-desktop
- Prime player bundle CDN (for reference): `cloudfront.xp-assets.aiv-cdn.net/family/lg/...`

*— End of notes. Written 2026-11-03 after the from-scratch verification pass.*