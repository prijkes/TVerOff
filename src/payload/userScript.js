/*!
 * TVerOff user script  (injected by webOS WAM from <TVer app dir>/webOSUserScripts/userScript.js)
 *
 * TVer's TV app is a thin loader that opens https://webapp.tver.jp/... in WAM. Its ads are inserted
 * server-side (STREAKS SSAI): the player asks the playback API for a stream with `ssai=1` and gets an
 * ad-stitched manifest back. TVer's own home-screen previews ask for `ssai=0` and get the plain stream.
 *
 * This script does one thing: make the *playback request* ask for `ssai=0`. It never touches the video,
 * the DOM of the player, or any other request, and it makes no network calls of its own.
 *
 * ES5 on purpose: it has to parse on every WAM generation.
 */
(function () {
  'use strict';

  var VERSION = '0.1.1';
  var w = window;

  try {
    if (!w || w.__TVEROFF__ || (w.location && w.location.protocol === 'file:')) { return; }
    if (w.localStorage && w.localStorage.getItem('tveroff.disable') === '1') { return; }
  } catch (e) { return; }

  var T = w.__TVEROFF__ = {
    version: VERSION,
    rewrites: 0,        // playback requests switched from ssai=1 to ssai=0
    legacyFixes: 0,     // streaks_player page loads/navigations switched to ads_enabled=false
    ssaiSeen: 0,        // ad-stitched streams that still got through (should stay 0)
    last: ''
  };

  var PLAYBACK_HOST = /^playback\.([a-z0-9-]+\.)*streaks\.jp$/i;
  var PLAYBACK_PATH = /^\/v1\/projects\/[^\/]+\/(medias|contents)\//;
  var SSAI_SEEN = /(^|\/\/)ssai-manifest\.|\/ssai\/session(\?|$)/i;
  var LEGACY_PLAYER = /\/streaks_player\/?$/;

  function log() {
    try { if (w.console && w.console.info) { w.console.info.apply(w.console, ['[TVerOff]'].concat([].slice.call(arguments))); } } catch (e) {}
  }

  function badgeEnabled() {
    try { return !(w.localStorage && w.localStorage.getItem('tveroff.badge') === '0'); } catch (e) { return true; }
  }

  // Live playback is left alone: its "ads" are the broadcaster's breaks, and ssai=0 there means slates.
  function isLive() {
    try {
      return /(^|[?&])delivery_type=live(&|$)/.test(w.location.search) || /\/live(\/|$)/.test(w.location.pathname);
    } catch (e) { return false; }
  }

  /* ---- 1. the actual patch: playback API request ssai=1 -> ssai=0 --------------------------------- */

  function rewritePlaybackUrl(url) {
    var u;
    try { u = new w.URL(String(url), w.location.href); } catch (e) { return url; }
    if (!PLAYBACK_HOST.test(u.hostname) || !PLAYBACK_PATH.test(u.pathname)) { return url; }
    var v = u.searchParams.get('ssai');
    if (v === null || v === '0' || v === 'false') { return url; }
    if (isLive()) { return url; }
    u.searchParams.set('ssai', '0');
    return u.toString();
  }

  function noteRewrite(from, to) {
    T.rewrites++;
    T.last = String(to).replace(/[?#].*$/, '');
    log('playback request switched to ssai=0 (#' + T.rewrites + '):', T.last);
    showBadge('TVerOff \u2713 \u5E83\u544A\u306A\u3057 / ad-free', false);
  }

  function patchXhr() {
    var proto = w.XMLHttpRequest && w.XMLHttpRequest.prototype;
    if (!proto || typeof proto.open !== 'function') { return; }
    var open = proto.open;
    proto.open = function (method, url) {
      var args = Array.prototype.slice.call(arguments);
      try {
        var nu = rewritePlaybackUrl(url);
        if (nu !== url) { noteRewrite(url, nu); args[1] = nu; }
      } catch (e) {}
      return open.apply(this, args);
    };
  }

  function patchFetch() {
    if (typeof w.fetch !== 'function') { return; }
    var orig = w.fetch;
    w.fetch = function (input, init) {
      try {
        var isReq = w.Request && input instanceof w.Request;
        var url = isReq ? input.url : input;
        var nu = rewritePlaybackUrl(url);
        if (nu !== url) {
          noteRewrite(url, nu);
          input = isReq ? new w.Request(nu, input) : nu;
        }
      } catch (e) {}
      return orig.call(this, input, init);
    };
  }

  /* ---- 2. legacy SSR player page (/ostvweb/streaks_player?...): ads_enabled=false ------------------- */

  function fixLegacyPlayerUrl(url) {
    var u;
    try { u = new w.URL(String(url), w.location.href); } catch (e) { return null; }
    if (!LEGACY_PLAYER.test(u.pathname)) { return null; }
    if (u.searchParams.get('delivery_type') === 'live') { return null; }
    if (u.searchParams.get('ads_enabled') === 'false' || u.searchParams.get('ad_flag') === '0') { return null; }
    u.searchParams.set('ads_enabled', 'false');
    return u.toString();
  }

  function patchUrlSearchParams() {
    var P = w.URLSearchParams && w.URLSearchParams.prototype;
    if (!P) { return; }
    ['append', 'set'].forEach(function (name) {
      var orig = P[name];
      if (typeof orig !== 'function') { return; }
      P[name] = function (k, v) {
        if (k === 'ads_enabled' && String(v) !== 'false' && !isLive()) { v = 'false'; }
        return orig.call(this, k, v);
      };
    });
  }

  function guardLegacyNavigation() {
    try {
      if (w.navigation && typeof w.navigation.addEventListener === 'function') {
        w.navigation.addEventListener('navigate', function (e) {
          var nu = fixLegacyPlayerUrl(e.destination && e.destination.url);
          if (nu && e.cancelable) {
            e.preventDefault();
            T.legacyFixes++;
            log('legacy player navigation switched to ads_enabled=false');
            w.location.replace(nu);
          }
        });
      }
    } catch (e) {}
    try {
      var nu = fixLegacyPlayerUrl(w.location.href);
      if (nu) {
        T.legacyFixes++;
        log('legacy player page reloaded with ads_enabled=false');
        w.location.replace(nu);
      }
    } catch (e) {}
  }

  /* ---- 3. tripwire: if an ad-stitched stream still shows up, say so instead of failing silently -------- */

  function watchForSsai() {
    try {
      if (typeof w.PerformanceObserver !== 'function') { return; }
      var po = new w.PerformanceObserver(function (list) {
        var entries = list.getEntries();
        for (var i = 0; i < entries.length; i++) {
          if (SSAI_SEEN.test(entries[i].name) && !isLive()) {
            T.ssaiSeen++;
            log('WARNING: an ad-stitched (SSAI) stream was requested:', entries[i].name.replace(/[?#].*$/, ''));
            showBadge('TVerOff: \u5E83\u544A\u3092\u9632\u3052\u307E\u305B\u3093\u3067\u3057\u305F / ads not blocked', true);
          }
        }
      });
      po.observe({ entryTypes: ['resource'] });
    } catch (e) {}
  }

  /* ---- tiny non-interactive on-screen confirmation ------------------------------------------------ */

  var badgeEl = null, badgeTimer = 0, badgeAt = 0;
  function showBadge(text, bad) {
    try {
      if (!badgeEnabled() || !w.document || !(w.document.body || w.document.documentElement)) { return; }
      var now = Date.now();
      if (!bad && now - badgeAt < 4000) { return; }
      badgeAt = now;
      if (!badgeEl) {
        badgeEl = w.document.createElement('div');
        badgeEl.setAttribute('aria-hidden', 'true');
        badgeEl.style.cssText = 'position:fixed;top:28px;right:36px;z-index:2147483647;padding:10px 18px;border-radius:10px;' +
          'font:600 24px/1.25 sans-serif;color:#fff;pointer-events:none;opacity:0;transition:opacity .35s;';
        (w.document.body || w.document.documentElement).appendChild(badgeEl);
      }
      badgeEl.textContent = text;
      badgeEl.style.background = bad ? 'rgba(170,30,30,.92)' : 'rgba(10,110,60,.9)';
      badgeEl.style.opacity = '1';
      w.clearTimeout(badgeTimer);
      badgeTimer = w.setTimeout(function () { if (badgeEl) { badgeEl.style.opacity = '0'; } }, bad ? 6000 : 2800);
    } catch (e) {}
  }

  patchXhr();
  patchFetch();
  patchUrlSearchParams();
  guardLegacyNavigation();
  watchForSsai();

  // exposed for tests / diagnostics only
  T._rewritePlaybackUrl = rewritePlaybackUrl;
  T._fixLegacyPlayerUrl = fixLegacyPlayerUrl;

  log('active v' + VERSION + ' on ' + (w.location ? w.location.pathname : '?'));
})();
