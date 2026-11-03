# Field notes: TVer on webOS (how TVerOff was found, and how to re-check it)

Companion to `PORTING-GUIDE.md`. Everything here was observed on real hardware on 2026-11-03:
LG OLED55C2PJA, webOS 11.2.0 (kernel 6.12.44 aarch64, 32-bit ARM userland), Homebrew Channel 0.7.3, TVer app
`jp.co.tver.tvapp` 1.0.2 (web build reports `av=7.0.15`), WAM Chromium 132.0.6834.207.

The guide's methodology (section 4) held: *do not assume the Prime architecture transfers, discover it on the device*.
Almost none of it transferred, and the result is much simpler than AmazOff.

## 1. What the TVer app is

* `jp.co.tver.tvapp`, `"type": "web"`, in `/media/cryptofs/apps/usr/palm/applications/jp.co.tver.tvapp/`
  (a plain, writable directory: no squashfs, no overlay). 128 KB in total.
* Its `index.html` is NCG-encrypted (LG Content Store wrapper). It is a stub that opens
  `https://webapp.tver.jp/ostvweb/csr/`. **All real code is remote**, served from `webapp.tver.jp`.
* Two generations of that web app are served side by side:
  * **CSR single-page app** (`/ostvweb/csr/...`). Vite build, chunk names carry a content hash and change on every
    deploy (`assets/index-<hash>.js`). Plays *in page* at `/ostvweb/csr/episodes/<id>/player`. This is what the
    TV opens today.
  * **SSR pages** (`/ostvweb/top`, `/program`, `/streaks_player`, `public/js/bundle.js?v=NN`,
    `public/streaksPlayer/prod/prod.js`). The older UI; `streaks_player` is its player page. Still reachable.
* Player: **STREAKS** (`streaksplayer.min.js`, a Video.js build) with STREAKS' playback/SSAI/metrics backends.

Because the code is remote and re-deployed constantly, patching files (the guide's route) is a dead end here, and
anything keyed on minified identifiers or chunk hashes would break within days. Hence: hook the *network contract*.

## 2. How TVer's ads work (this is the part the guide told us to verify first)

Server-side ad insertion (SSAI) by STREAKS, **but with a client-selectable switch**:

```
GET https://playback.api.streaks.jp/v1/projects/<project>/medias/<mediaId>?ssai=1&ati=<ad-template-id>
```

With `ssai=1` the response points at an ad-stitched HLS stream (`ssai-manifest.streaks.jp`, `ssai-variants.streaks.jp`),
the player opens an SSAI session (`POST ssai.api.streaks.jp/.../ssai/session`), polls `.../ssai/ad_info`, and reports
`adBreakStart` / `adStart` beacons (observed: a 15 s pre-roll, adSystem `aja.vision`).
With `ssai=0` it points at the plain programme stream (`manifest.streaks.jp/.../hls/v3/manifest.m3u8`).

TVer's own home-screen preview players already request `ssai=0` and are served the plain stream with ordinary client
credentials, so the backend does not gate it. `prod.js` (SSR player) contains the same switch:
`if (params["ad_flag"] === "0" || params["ads_enabled"] === "false") { playbackParams.ssai = 0; }`.

Measured A/B on the TV, same episode, muted (patch off vs. patch on):

|                              | stock (`ssai=1`)                                      | TVerOff (`ssai=0`)                    |
|------------------------------|-------------------------------------------------------|---------------------------------------|
| playback request             | `.../medias/<id>?ssai=1&ati=...`                      | `.../medias/<id>?ssai=0&ati=...`      |
| manifest host                | `ssai-manifest.streaks.jp`, `ssai-variants.streaks.jp`| `manifest.streaks.jp`, `variants.streaks.jp` |
| SSAI session / `ad_info`     | yes                                                   | none                                  |
| `adBreakStart` / `adStart`   | 3 / 3 beacons in the capture window                   | 0 / 0                                 |
| `video.duration`             | 2731 s and 2746 s (two runs; the ads differ per session) | 2701 s in every run (4 runs)       |
| player at t = 10 s           | inside the pre-roll ad break (per the beacons)        | the programme's own opening title (screenshot) |

So the guide's pessimistic branch (SSAI cannot be removed client-side, needs a manifest-rewriting proxy) does **not**
apply: one URL parameter is enough.

Not touched on purpose: live playback, the native "PR" promo cards on the home screen, and the
`service-ads-api.tver.jp/ads/api/v1/vast_ad` request the home page makes. If TVer ever stops honouring `ssai=0`, the
fallback is the guide's option (a)/(b): parse the SSAI media playlist (`EXT-X-DISCONTINUITY` / `DATERANGE` / cue tags,
or the `adBreakStart`/`adBreakStop` beacons) and seek over the breaks. `userScript.js` already raises a red badge when an
SSAI stream gets through, so that failure is visible rather than silent.

## 3. Interception primitives that were tried on webOS 11 (web app, not native)

| primitive | result |
|---|---|
| edit the app's files | `index.html` is NCG-encrypted and is only a redirect; the code is remote. Dead end. |
| Chromium DevTools (WAM is started with `--remote-debugging-port=9998`) | The port is **not listening**. `luna://com.palm.webappmanager/setInspectorEnable` answers *"Not allowed; need accessUsb authentication first"*: LG gates it on `com.webos.service.sm/accessusb` (`isAuthenticated: false`). That is an LG authentication check; it was not bypassed. |
| USB `proxy.txt` (`/tmp/usb/sda/sda1/proxy.txt`, string `[PROXY] From USB` in `libWebAppMgr`) | Same accessusb gate; not tried. |
| system proxy / DNS / hosts / CA pinning (the AmazOff chain) | Not needed, so not built. WAM has no per-app proxy key. |
| **`<app dir>/webOSUserScripts/userScript.js`** | **Works.** `libWebAppMgrCore`/`libWebAppMgr` reference `webOSUserScripts/userScript.js` (`WebPageBase::setCustomUserScript`, `addUserScriptUrl`). WAM injects it into the app's pages. |

### Measured semantics of the user-script hook

* **Scope:** the app's *top frame* only (an iframe such as `s.tver.jp/.../tvappsdkplf.html` never reported in). It also
  runs in the local `file://.../index.html` stub, hence the `file:` guard in the payload.
* **Timing:** when the document finishes loading, i.e. `readyState` is `interactive`/`complete` and the page's own
  parser-inserted and module scripts have already run (14 scripts present on the CSR page). So it can wrap requests the
  app makes *later, on user action* (playback) but cannot influence what a page does while it is loading. The SSR
  `streaks_player` page issues its playback request synchronously at load, so for that flow the payload redirects the
  navigation (Navigation API `navigate` event) or reloads the page once with `ads_enabled=false`.
* **Caching:** the file is read when the app *launches*. Changing it requires closing the app; `tverctl.sh start|stop`
  therefore closes a running TVer.
* **World:** the main world, so wrapping `XMLHttpRequest.prototype.open`, `fetch` and `URLSearchParams` works and the
  page's own references see it.
* **Lifetime:** survives reboots; **dies when the LG Content Store replaces the TVer folder** (app update). The monitor
  puts it back the next time TVer is opened (and restarts TVer once); the boot hook does the same at boot.

## 4. The shipped payload (`src/payload/userScript.js`)

1. `XMLHttpRequest.prototype.open` + `fetch`: a request to `playback.*streaks.jp/v1/projects/*/(medias|contents)/*`
   with `ssai` other than 0 is sent with `ssai=0`. Anything else is passed through untouched.
2. `URLSearchParams.append|set('ads_enabled', x)` forces `false` (what the SSR builders read).
3. `/streaks_player` navigations and page loads without `ads_enabled=false` are redirected once to the same URL with it.
4. A `PerformanceObserver` flags any SSAI manifest/session request that still happens (red badge).
5. A green badge for ~3 s whenever a playback request was switched.
6. Skips live (`/live` path, `delivery_type=live`); kill switch `localStorage['tveroff.disable']='1'`; badge off with
   `localStorage['tveroff.badge']='0'` (both on the `webapp.tver.jp` origin).

ES5 only (enforced by `test/userscript.test.js`), pure ASCII source (non-ASCII strings use `\u` escapes), no network
requests of its own.

## 5. Re-checking after a TVer / webOS update

```
set TV_HOST=<tv ip>
python development/smoke_test.py --shots out/
```

It plays the first episode of the home screen muted for ~15 s with the *installed* payload and asserts the five
observable facts of section 2, then restores the TV. If it fails, look in this order:

1. **No episode link / no play button**: TVer re-skinned its home page; update the selectors at the top of
   `smoke_test.py` (they are only used by the test, not by the payload).
2. **`rewrites` is 0**: the hook no longer sees the request. Either the SDK now uses `fetch` captured before the user
   script runs, or the URL/param changed. Attach the bridge (below) and look at what the player requests.
3. **`rewrites` > 0 but `ssaiManifest` > 0**: TVer stopped honouring `ssai=0`. Implement the fallback in section 2.
4. **User script not loading at all**: a webOS update changed WAM's hook (check `libWebAppMgr*.so` still mentions
   `webOSUserScripts/userScript.js`, and that the file is there: `tverctl.sh status`).

### TVer's own survey gate (not ours, but you will run into it)

TVer counts playbacks in `localStorage.totalPlayCount` (webapp.tver.jp origin). After enough plays (it appeared
between the 8th and 9th playback while developing this) TVer shows a 3-step survey ("アンケートに回答": gender, viewing
environment, ...) before **every** episode until it is answered on the TV with the remote: *"エピソードはアンケート回答後に再生されます"*
("the episode plays after the survey"). No playback API call is made while it is up, so TVerOff has nothing to act on.
It is TVer's prompt and independent of the patch; the test tooling neither answers it nor edits the counter. If
`smoke_test.py` reports it, answer the survey once on the TV and re-run (keep `--count` small: every test play counts).

### The in-page bridge (how all of the above was discovered)

`development/tvlink.py` contains `Bridge`: it runs a loopback-only HTTP server on the TV (python3 is on the TV) and
installs a stub into an app's `webOSUserScripts/userScript.js` that long-polls it and `eval`s what it gets. You can
then run arbitrary JS inside TVer's real page from the PC and read results back, with the TV's own session and cookies.
Used with `performance.getEntriesByType('resource')` and wrappers on XHR/fetch it shows every request the player makes.
It needs a rooted TV, is removed by `Bridge.detach()`, and the smoke test uses it. Screenshots come from
`luna://com.webos.service.capture/executeOneShot` (`method: DISPLAY` includes the video plane).

Traps hit while building the tooling, worth remembering:

* `luna-send` reads stdin: run it with `</dev/null`, or it eats the rest of a piped script.
* `pgrep -f '<pattern>'` also matches the shell running that very command line. Use `'[p]attern'`.
* A `luna-send -i` subscription in a pipeline needs the monitor to be a session leader (`setsid`) so one group kill
  takes the whole pipeline down.
* HBC's `exec` returns only when every inherited pipe is closed: detach daemons from fds 0-2 *and* everything above.
