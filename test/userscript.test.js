'use strict';
// Runs the injected payload in a sandbox with fake XHR/fetch/location/navigation and checks what it does.
//   node test/userscript.test.js
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const assert = require('assert');

const FILE = path.join(__dirname, '..', 'src', 'payload', 'userScript.js');
const SRC = fs.readFileSync(FILE, 'utf8');

const PLAYBACK = 'https://playback.api.streaks.jp/v1/projects/tver-cx/medias/7f101d1ee89b434dbdb91e1f46cd718c';

function makeEnv(href, opts) {
  opts = opts || {};
  const calls = { xhr: [], fetch: [], replace: [], navListeners: [], logs: [], badges: [] };
  const loc = new URL(href);
  loc.replace = (u) => calls.replace.push(String(u));
  class XHR { open(m, u) { calls.xhr.push(String(u)); } }
  class Req { constructor(u, init) { this.url = String(u); this.init = init; } }
  const doc = {
    body: { appendChild: (el) => calls.badges.push(el) },
    documentElement: { appendChild: (el) => calls.badges.push(el) },
    createElement: () => ({ style: {}, setAttribute() {} })
  };
  const win = {
    URL, URLSearchParams, XMLHttpRequest: XHR, Request: Req, Date,
    fetch: (i) => { calls.fetch.push(i && i.url ? i.url : String(i)); return Promise.resolve('ok'); },
    location: loc,
    document: doc,
    localStorage: { getItem: (k) => (opts.ls && k in opts.ls ? opts.ls[k] : null) },
    console: { info: (...a) => calls.logs.push(a.join(' ')) },
    setTimeout: () => 0, clearTimeout: () => {},
    navigation: opts.nav ? { addEventListener: (t, f) => calls.navListeners.push(f) } : undefined
  };
  win.window = win;
  vm.createContext(win);
  vm.runInContext(SRC, win, { filename: 'userScript.js' });
  return { win, calls };
}

let n = 0, failed = 0;
function test(name, fn) {
  n++;
  try { fn(); console.log('  ok   ' + name); } catch (e) { failed++; console.log('  FAIL ' + name + '\n       ' + (e && e.message)); }
}

console.log('userScript.js');

test('is ES5-only (no arrows, let/const, template strings, async, spread, classes, ?. ??)', () => {
  const code = SRC.replace(/\/\*[\s\S]*?\*\//g, '').replace(/\/\/.*$/gm, '').replace(/'(?:\\.|[^'\\])*'/g, "''");
  const bad = [/=>/, /\blet\b/, /\bconst\b/, /`/, /\basync\b/, /\bawait\b/, /\.\.\./, /\bclass\b/, /\?\./, /\?\?/];
  bad.forEach((re) => assert(!re.test(code), 'found ' + re));
});

test('XHR: playback request ssai=1 -> ssai=0, other params kept', () => {
  const { win, calls } = makeEnv('https://webapp.tver.jp/ostvweb/csr/episodes/eper115qft/player');
  new win.XMLHttpRequest().open('GET', PLAYBACK + '?ssai=1&ati=ABC123');
  assert.strictEqual(calls.xhr.length, 1);
  const u = new URL(calls.xhr[0]);
  assert.strictEqual(u.searchParams.get('ssai'), '0');
  assert.strictEqual(u.searchParams.get('ati'), 'ABC123');
  assert.strictEqual(u.pathname, '/v1/projects/tver-cx/medias/7f101d1ee89b434dbdb91e1f46cd718c');
  assert.strictEqual(win.__TVEROFF__.rewrites, 1);
});

test('XHR: leaves everything else alone', () => {
  const { win, calls } = makeEnv('https://webapp.tver.jp/ostvweb/csr/home');
  const x = new win.XMLHttpRequest();
  const same = [
    PLAYBACK + '?ssai=0&ati=ABC',                                              // already ad-free (TVer previews)
    PLAYBACK + '?ati=ABC',                                                     // no ssai param at all
    'https://tver-metrics.streaks.jp/v2/init?ssai=1',                          // other host
    'https://playback.api.streaks.jp/v1/projects/tver-cx/other?ssai=1',        // other path
    'https://example.com/v1/projects/p/medias/m?ssai=1',                       // other domain
    'https://webapp.tver.jp/ostvweb/csr/home?ssai=1'
  ];
  same.forEach((u) => x.open('GET', u));
  assert.deepStrictEqual(calls.xhr, same);
  assert.strictEqual(win.__TVEROFF__.rewrites, 0);
});

test('XHR: relative URLs and URL objects do not crash', () => {
  const { win, calls } = makeEnv('https://webapp.tver.jp/ostvweb/csr/home');
  const x = new win.XMLHttpRequest();
  x.open('GET', '/some/relative/path');
  x.open('GET', new URL(PLAYBACK + '?ssai=1'));
  assert.strictEqual(calls.xhr.length, 2);
  assert.strictEqual(new URL(calls.xhr[1]).searchParams.get('ssai'), '0');
});

test('fetch: string URL and Request are rewritten', () => {
  const { win, calls } = makeEnv('https://webapp.tver.jp/ostvweb/csr/home');
  win.fetch(PLAYBACK + '?ssai=1&ati=Z');
  win.fetch(new win.Request(PLAYBACK + '?ssai=1&ati=Z'));
  win.fetch('https://example.com/x?ssai=1');
  assert.strictEqual(new URL(calls.fetch[0]).searchParams.get('ssai'), '0');
  assert.strictEqual(new URL(calls.fetch[1]).searchParams.get('ssai'), '0');
  assert.strictEqual(calls.fetch[2], 'https://example.com/x?ssai=1');
});

test('URLSearchParams: ads_enabled is forced to false (append and set)', () => {
  const { win } = makeEnv('https://webapp.tver.jp/ostvweb/csr/home');
  const p = new win.URLSearchParams();
  p.append('ads_enabled', 'true');
  p.append('other', 'x');
  assert.strictEqual(p.get('ads_enabled'), 'false');
  p.set('ads_enabled', 'true');
  assert.strictEqual(p.get('ads_enabled'), 'false');
  assert.strictEqual(p.get('other'), 'x');
});

test('legacy player page opened without ads_enabled=false is reloaded once with it (hash kept)', () => {
  const { calls } = makeEnv('https://webapp.tver.jp/ostvweb/streaks_player?projectid=tver-cx&media_id=abc#status=&playCount=1');
  assert.strictEqual(calls.replace.length, 1);
  const u = new URL(calls.replace[0]);
  assert.strictEqual(u.searchParams.get('ads_enabled'), 'false');
  assert.strictEqual(u.searchParams.get('media_id'), 'abc');
  assert.strictEqual(u.hash, '#status=&playCount=1');
});

test('legacy player page already ad-free / ad_flag=0 / live is left alone (no reload loop)', () => {
  ['https://webapp.tver.jp/ostvweb/streaks_player?ads_enabled=false&media_id=abc',
   'https://webapp.tver.jp/ostvweb/streaks_player?ad_flag=0&media_id=abc',
   'https://webapp.tver.jp/ostvweb/streaks_player?delivery_type=live&media_id=abc'
  ].forEach((href) => assert.strictEqual(makeEnv(href).calls.replace.length, 0, href));
});

test('Navigation API: navigation to legacy player is cancelled and redirected with ads_enabled=false', () => {
  const { calls } = makeEnv('https://webapp.tver.jp/ostvweb/csr/home', { nav: true });
  assert.strictEqual(calls.navListeners.length, 1);
  let prevented = false;
  calls.navListeners[0]({ destination: { url: 'https://webapp.tver.jp/ostvweb/streaks_player?media_id=abc' }, cancelable: true, preventDefault() { prevented = true; } });
  assert(prevented);
  assert.strictEqual(new URL(calls.replace[0]).searchParams.get('ads_enabled'), 'false');
  // the redirected URL itself must pass through untouched (no loop), as must unrelated navigations
  let again = false;
  calls.navListeners[0]({ destination: { url: calls.replace[0] }, cancelable: true, preventDefault() { again = true; } });
  calls.navListeners[0]({ destination: { url: 'https://webapp.tver.jp/ostvweb/csr/episodes/epx' }, cancelable: true, preventDefault() { again = true; } });
  assert(!again);
  assert.strictEqual(calls.replace.length, 1);
});

test('live: nothing is rewritten on live pages', () => {
  const { win, calls } = makeEnv('https://webapp.tver.jp/ostvweb/csr/live/ntv');
  new win.XMLHttpRequest().open('GET', PLAYBACK + '?ssai=1');
  assert.strictEqual(calls.xhr[0], PLAYBACK + '?ssai=1');
  const l2 = makeEnv('https://webapp.tver.jp/ostvweb/streaks_player?delivery_type=live&ssai=1');
  new l2.win.XMLHttpRequest().open('GET', PLAYBACK + '?ssai=1');
  assert.strictEqual(l2.calls.xhr[0], PLAYBACK + '?ssai=1');
});

test('kill switch: localStorage tveroff.disable=1 disables everything', () => {
  const { win, calls } = makeEnv('https://webapp.tver.jp/ostvweb/streaks_player?x=1', { ls: { 'tveroff.disable': '1' }, nav: true });
  new win.XMLHttpRequest().open('GET', PLAYBACK + '?ssai=1');
  assert.strictEqual(calls.xhr[0], PLAYBACK + '?ssai=1');
  assert.strictEqual(calls.replace.length, 0);
  assert.strictEqual(calls.navListeners.length, 0);
  assert.strictEqual(win.__TVEROFF__, undefined);
});

test('does nothing inside the local file:// loader page', () => {
  const { win } = makeEnv('file:///media/cryptofs/apps/usr/palm/applications/jp.co.tver.tvapp/index.html');
  assert.strictEqual(win.__TVEROFF__, undefined);
});

test('idempotent: running the script twice in one page does not double-wrap', () => {
  const { win, calls } = makeEnv('https://webapp.tver.jp/ostvweb/csr/home');
  vm.runInContext(SRC, win);
  new win.XMLHttpRequest().open('GET', PLAYBACK + '?ssai=1');
  assert.strictEqual(win.__TVEROFF__.rewrites, 1);
  assert.strictEqual(calls.xhr.length, 1);
});

test('shows a badge on rewrite, and none when disabled', () => {
  const a = makeEnv('https://webapp.tver.jp/ostvweb/csr/home');
  new a.win.XMLHttpRequest().open('GET', PLAYBACK + '?ssai=1');
  assert.strictEqual(a.calls.badges.length, 1);
  const b = makeEnv('https://webapp.tver.jp/ostvweb/csr/home', { ls: { 'tveroff.badge': '0' } });
  new b.win.XMLHttpRequest().open('GET', PLAYBACK + '?ssai=1');
  assert.strictEqual(b.calls.badges.length, 0);
});

console.log('\n' + (n - failed) + '/' + n + ' passed');
process.exit(failed ? 1 : 0);
