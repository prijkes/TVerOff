/* TVerOff UI (ES5). Drives tools/tverctl.sh through the Homebrew Channel exec service. */
(function () {
  'use strict';

  var APP_ID = 'com.tveroff.patcher';
  var TVER_ID = 'jp.co.tver.tvapp';
  var CTL = '/media/developer/apps/usr/palm/applications/' + APP_ID + '/tools/tverctl.sh';

  var state = { service: 'unknown', autostart: false, valid: false, details: false, note: '', noteBad: false, logs: '', info: null };
  var busy = false;

  function $(id) { return document.getElementById(id); }

  /* ---- Luna bridge (what LG's webOSTV.js does, without shipping the library) ------------------------ */

  function luna(uri, params, cb, timeoutMs) {
    var finished = false, timer = null, bridge = null;
    function done(r) {
      if (finished) { return; }
      finished = true;
      if (timer) { clearTimeout(timer); }
      try { if (bridge && bridge.cancel) { bridge.cancel(); } } catch (e) {}
      cb(r);
    }
    if (!window.PalmServiceBridge) {
      done({ returnValue: false, errorText: 'PalmServiceBridge is not available (not running on webOS?)' });
      return;
    }
    try {
      bridge = new window.PalmServiceBridge();
      bridge.onservicecallback = function (msg) {
        var r;
        try { r = JSON.parse(msg); } catch (e) { r = { returnValue: false, errorText: String(msg) }; }
        r.returnValue = !(r.errorCode || r.returnValue === false);
        done(r);
      };
      bridge.call(uri, JSON.stringify(params || {}));
    } catch (e) {
      done({ returnValue: false, errorText: String(e) });
      return;
    }
    timer = setTimeout(function () { done({ returnValue: false, errorText: 'timeout' }); }, timeoutMs || 10000);
  }

  function exec(command, cb, timeoutMs) {
    luna('luna://org.webosbrew.hbchannel.service/exec', { command: command }, cb, timeoutMs);
  }

  /* ---- talking to tverctl.sh -------------------------------------------------------------------------- */

  function decodeLogs(b64) {
    try { return window.atob(b64 || ''); } catch (e) { return ''; }
  }

  function hintFor(r) {
    var t = String((r && (r.errorText || r.stderrString)) || '');
    if (/Denied method call/i.test(t)) {
      return 'Homebrew Channel refused the call: on webOS 11 versions 0.7.x of Homebrew Channel block other apps from ' +
        'running commands. Update Homebrew Channel to 0.8.0 or newer (or see "How it works" for the SSH commands). ' +
        'Start at boot still works without this.';
    }
    if (/timeout/i.test(t)) { return 'Homebrew Channel did not answer in time.'; }
    if (/Unknown (service|method)|ServiceUnknown|not registered|not running/i.test(t)) {
      return 'Homebrew Channel service not found. TVerOff needs a rooted TV with Homebrew Channel installed.';
    }
    return t ? 'Could not run the controller: ' + t : 'Could not run the controller.';
  }

  function applyResult(r) {
    var parsed = null;
    if (r && r.returnValue && r.stdoutString) {
      var lines = String(r.stdoutString).replace(/\s+$/, '').split('\n');
      try { parsed = JSON.parse(lines[lines.length - 1]).state; } catch (e) { parsed = null; }
    }
    if (!parsed) {
      state.valid = false;
      state.service = 'error';
      state.info = null;
      state.logs = (r && (r.errorText || r.stderrString || r.stdoutString)) || 'no output';
      state.note = hintFor(r);
      state.noteBad = true;
      return;
    }
    state.valid = true;
    state.service = parsed.service;
    state.autostart = !!parsed.autostart;
    state.logs = decodeLogs(parsed.logs);
    state.info = parsed;
    state.note = '';
    state.noteBad = false;
    if (parsed.tver && parsed.tver.installed === false) {
      state.note = 'The TVer app is not installed on this TV. Install it from the LG Content Store first.';
      state.noteBad = true;
    } else if (parsed.service === 'error' && parsed.patch && parsed.patch.present && !parsed.patch.current) {
      state.note = 'The installed patch is out of date. Press Start Service to update it.';
    }
  }

  function ctl(arg, text, timeoutMs) {
    if (busy) { return; }
    busy = true;
    setLoading(true, text);
    exec(CTL + ' ' + arg, function (r) {
      busy = false;
      setLoading(false);
      applyResult(r);
      render();
    }, timeoutMs || 15000);
  }

  /* ---- rendering ------------------------------------------------------------------------------------ */

  function setLoading(on, text) {
    $('loadText').textContent = text || 'Working\u2026';
    $('loading').className = on ? 'overlay' : 'overlay hidden';
  }

  function factsHtml() {
    var i = state.info;
    if (!i) { return '<span class="bad">no data</span>'; }
    function f(label, ok, text) { return '<span>' + label + ' <b class="' + (ok ? 'ok' : 'bad') + '">' + text + '</b></span>'; }
    var out = [];
    out.push(f('TVer app', i.tver && i.tver.installed, i.tver && i.tver.installed ? 'v' + i.tver.version : 'missing'));
    out.push(f('Patch', i.patch && i.patch.current, i.patch && i.patch.current ? 'in place' : (i.patch && i.patch.present ? 'outdated' : 'not installed')));
    out.push(f('Monitor', i.monitor, i.monitor ? 'running' : 'off'));
    return out.join('');
  }

  function render() {
    var pill = $('pill');
    pill.className = 'pill ' + state.service;
    pill.textContent = state.service === 'running' ? 'ACTIVE' : state.service === 'stopped' ? 'OFF' : state.service === 'error' ? 'ERROR' : '\u2026';

    $('svcBtn').textContent = state.service === 'running' ? 'Stop Service' : 'Start Service';

    var auto = $('autoBtn');
    auto.className = 'item switch' + (state.autostart ? ' on' : '');
    auto.setAttribute('aria-pressed', state.autostart ? 'true' : 'false');
    if (state.valid) { auto.removeAttribute('disabled'); } else { auto.setAttribute('disabled', 'disabled'); }

    var note = $('note');
    note.className = 'note' + (state.note ? '' : ' hidden') + (state.noteBad ? ' bad' : '');
    note.textContent = state.note;

    $('details').className = state.details ? '' : 'hidden';
    if (state.details) {
      $('facts').innerHTML = factsHtml();
      $('log').value = state.logs;
    }
  }

  /* ---- actions ---------------------------------------------------------------------------------------- */

  function toggleService() {
    if (state.service === 'running') { ctl('stop', 'Stopping\u2026', 20000); } else { ctl('start', 'Starting\u2026', 20000); }
  }

  function toggleAutostart() {
    if (!state.valid) { return; }
    ctl('autostart_toggle', 'Updating\u2026', 15000);
  }

  function openTver() {
    luna('luna://com.webos.applicationManager/launch', { id: TVER_ID }, function (r) {
      if (!r.returnValue) {
        state.note = 'Could not open TVer' + (r.errorText ? ': ' + r.errorText : '') + '.';
        state.noteBad = true;
        render();
      }
    }, 8000);
  }

  function toggleDetails() {
    state.details = !state.details;
    render();
    if (state.details) { $('log').focus(); } else { $('detailsBtn').focus(); }
  }

  function helpOpen() { return $('helpLayer').className.indexOf('hidden') < 0; }
  function showHelp() { $('helpLayer').className = 'overlay'; $('closeBtn').focus(); }
  function hideHelp() { $('helpLayer').className = 'overlay hidden'; $('helpBtn').focus(); }

  function back() {
    if (helpOpen()) { hideHelp(); return; }
    if (state.details) { toggleDetails(); return; }
    if (window.PalmSystem && window.PalmSystem.platformBack) { window.PalmSystem.platformBack(); } else { window.close(); }
  }

  /* ---- D-pad navigation (nearest focusable item in the pressed direction) ----------------------------- */

  function visibleItems() {
    var root = helpOpen() ? $('helpLayer') : document;
    var all = root.querySelectorAll('.item'), out = [], i;
    for (i = 0; i < all.length; i++) {
      var el = all[i];
      if (el.offsetWidth > 0 && el.offsetHeight > 0 && !el.hasAttribute('disabled')) { out.push(el); }
    }
    return out;
  }

  function centerOf(el) {
    var r = el.getBoundingClientRect();
    return { x: r.left + r.width / 2, y: r.top + r.height / 2 };
  }

  function move(dir) {
    var items = visibleItems(), cur = document.activeElement;
    if (!items.length) { return; }
    if (!cur || cur.className.indexOf('item') < 0 || items.indexOf(cur) < 0) { items[0].focus(); return; }
    var c = centerOf(cur), best = null, bestScore = Infinity, i;
    for (i = 0; i < items.length; i++) {
      var el = items[i];
      if (el === cur) { continue; }
      var p = centerOf(el), dx = p.x - c.x, dy = p.y - c.y, ok, primary, secondary;
      if (dir === 'left') { ok = dx < -4; primary = -dx; secondary = Math.abs(dy); }
      else if (dir === 'right') { ok = dx > 4; primary = dx; secondary = Math.abs(dy); }
      else if (dir === 'up') { ok = dy < -4; primary = -dy; secondary = Math.abs(dx); }
      else { ok = dy > 4; primary = dy; secondary = Math.abs(dx); }
      if (!ok) { continue; }
      var score = primary + secondary * 2;
      if (score < bestScore) { best = el; bestScore = score; }
    }
    if (best) { best.focus(); }
  }

  function onKey(e) {
    var k = e.keyCode;
    if (busy) { if (k === 461) { e.preventDefault(); } return; }
    if (k === 461 || k === 27) { e.preventDefault(); back(); return; }
    var dir = k === 37 ? 'left' : k === 38 ? 'up' : k === 39 ? 'right' : k === 40 ? 'down' : null;
    if (!dir) { return; }
    var cur = document.activeElement;
    if (cur && cur.id === 'log' && (dir === 'up' || dir === 'down')) { return; }   // let the log scroll
    e.preventDefault();
    move(dir);
  }

  /* ---- boot -------------------------------------------------------------------------------------------- */

  function init() {
    $('svcBtn').onclick = toggleService;
    $('launchBtn').onclick = openTver;
    $('detailsBtn').onclick = toggleDetails;
    $('autoBtn').onclick = toggleAutostart;
    $('helpBtn').onclick = showHelp;
    $('closeBtn').onclick = hideHelp;
    document.addEventListener('keydown', onKey, false);
    render();
    $('svcBtn').focus();
    ctl('status', 'Checking\u2026', 12000);
  }

  window.addEventListener('load', init, false);
})();
