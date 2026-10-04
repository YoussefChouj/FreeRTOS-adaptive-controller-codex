'use strict';
/**
 * WP-35 components harness: the design system and safety UX, offline (fake DOM, fake clock, fake fetch).
 *   K  ui/ui-kit.js (window.GSUI): pill, table, empty state, stale mark, disabled reason, two-click confirm,
 *      toast, one error path, fetchJson timeout, poller backoff, shortcut focus guard, theme, campaign actions
 *   S  plugins/flight-strip.js: flight-critical state, Pause / Land / Abort (single click, disabled with the
 *      reason only when no run is active, enabled when the state is unknown), P = Pause with a focus guard
 *   H  index.html: tokens / components / kit load before plugins, strip outside #app, no inline :root
 *   L  lint rules that need no npm: no confirm/alert, no empty catch, no hex colours / inline onclick in the
 *      converted files, components.css uses tokens only
 * Run: node ground_station/service/tests/ui_components_harness.js   (exit 0 = all pass)
 */
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const { KIT } = require('./ui_kit_loader');

const SHELL = path.join(__dirname, '..', '..', '..', 'docs', 'dashboard-platform', 'shell');
const PLUGINS = path.join(SHELL, 'plugins');
const read = (p) => fs.readFileSync(p, 'utf8');

function check(cond, msg) { if (!cond) throw new Error(msg); }
function pass(tag, msg) { console.log(tag + ' ' + msg); }
const flush = () => new Promise((r) => setImmediate(r));
async function settle() { for (let i = 0; i < 5; i++) await flush(); }

// ── fake DOM ─────────────────────────────────────────────────────────────────────────────────────────
class El {
  constructor(tag, doc) {
    this.tagName = String(tag).toUpperCase(); this.doc = doc; this.id = ''; this.className = '';
    this.textContent = ''; this._html = ''; this.title = ''; this.disabled = false; this.value = '';
    this.style = {}; this.dataset = {}; this.attrs = {}; this.children = []; this.parentNode = null;
    this.handlers = {}; this._closest = null;
  }
  setAttribute(k, v) { this.attrs[k] = String(v); if (k === 'id') this.id = String(v); }
  getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; }
  appendChild(c) { c.parentNode = this; this.children.push(c); if (c.id) this.doc.byId[c.id] = c; return c; }
  removeChild(c) { const i = this.children.indexOf(c); if (i >= 0) this.children.splice(i, 1); c.parentNode = null; return c; }
  addEventListener(t, fn) { (this.handlers[t] = this.handlers[t] || []).push(fn); }
  removeEventListener(t, fn) { this.handlers[t] = (this.handlers[t] || []).filter((f) => f !== fn); }
  click() { if (this.disabled) return; (this.handlers.click || []).forEach((fn) => fn({ type: 'click', target: this })); }
  closest() { return this._closest; }
  get innerHTML() { return this._html; }
  set innerHTML(h) { this._html = h; this.doc.scan(h); }
}

function makeDoc() {
  const doc = {
    byId: {}, handlers: {}, activeElement: null,
    createElement(tag) { return new El(tag, doc); },
    getElementById(id) { return doc.byId[id] || null; },
    addEventListener(t, fn) { (doc.handlers[t] = doc.handlers[t] || []).push(fn); },
    removeEventListener(t, fn) { doc.handlers[t] = (doc.handlers[t] || []).filter((f) => f !== fn); },
    key(ev) { (doc.handlers.keydown || []).forEach((fn) => fn(Object.assign({ preventDefault() {} }, ev))); },
    scan(html) {
      for (const tag of html.match(/<[a-zA-Z][^>]*>/g) || []) {
        const idm = tag.match(/\bid="([^"]+)"/);
        if (!idm) continue;
        const el = doc.byId[idm[1]] || new El(tag.slice(1).split(/[\s>]/)[0], doc);
        el.id = idm[1];
        const cm = tag.match(/\bclass="([^"]*)"/); if (cm) el.className = cm[1];
        const tm = tag.match(/\btitle="([^"]*)"/); if (tm) el.title = tm[1];
        if (/\sdisabled[\s>]/.test(tag)) el.disabled = true;
        doc.byId[idm[1]] = el;
      }
    },
  };
  doc.body = new El('body', doc);
  doc.documentElement = new El('html', doc);
  return doc;
}

function makeEnv() {
  const clock = { now: 1e6, timers: [], intervals: {}, seq: 0 };
  const warns = [];
  const fetches = [];
  const store = {};
  const doc = makeDoc();
  const ctx = {
    window: {}, document: doc, JSON, Math,
    console: { warn: (m) => warns.push(String(m)), error() {}, log() {} },
    Date: { now: () => clock.now },
    setTimeout(fn, ms) { const id = ++clock.seq; clock.timers.push({ id, fn, at: clock.now + (ms || 0) }); return id; },
    clearTimeout(id) { clock.timers = clock.timers.filter((t) => t.id !== id); },
    setInterval(fn) { const id = ++clock.seq; clock.intervals[id] = fn; return id; },
    clearInterval(id) { delete clock.intervals[id]; },
    localStorage: { getItem: (k) => (k in store ? store[k] : null), setItem: (k, v) => { store[k] = String(v); } },
    fetch(url, init) { fetches.push({ url, init: init || {} }); return ctx._route(url, init || {}); },
    _route: () => new Promise(() => {}),
  };
  ctx.window.document = doc;
  vm.createContext(ctx);
  vm.runInContext(read(KIT), ctx, { filename: KIT });
  const advance = (ms) => {
    clock.now += ms;
    const due = clock.timers.filter((t) => t.at <= clock.now);
    clock.timers = clock.timers.filter((t) => t.at > clock.now);
    due.forEach((t) => t.fn());
  };
  const tickAll = () => Object.values(clock.intervals).forEach((fn) => fn());
  return { ctx, doc, clock, warns, fetches, store, advance, tickAll, UI: ctx.window.GSUI };
}

const reply = (ok, status, body) => Promise.resolve({ ok, status, json: () => Promise.resolve(body) });

// ── K: the kit ───────────────────────────────────────────────────────────────────────────────────────
async function kitChecks() {
  const env = makeEnv();
  const { UI, doc } = env;
  check(UI && UI.version === 1, 'window.GSUI missing');
  check(UI.esc('<a href="x">&\'') === '&lt;a href=&quot;x&quot;&gt;&amp;&#39;', 'esc');
  pass('K1', 'GSUI loads and escapes HTML');

  const p = UI.pill('fail', '<b>LOST</b>', 'why', 'p1');
  check(/id="p1"/.test(p) && /gs-pill gs-pill--fail/.test(p) && /data-status="fail"/.test(p) &&
        p.includes('&lt;b&gt;LOST') && /title="why"/.test(p), 'pill markup ' + p);
  check(/gs-pill--stale/.test(UI.pill('bogus', 'x')), 'unknown status must fall back to stale');
  const pe = new El('span', doc); pe.className = 'gs-pill gs-pill--ok';
  UI.setPill(pe, 'warn', 'ARMED', 't');
  check(pe.className === 'gs-pill gs-pill--warn' && pe.textContent === 'ARMED' && pe.title === 't', 'setPill ' + pe.className);
  pass('K2', 'status pill: one modifier from ok/warn/fail/stale/info, escaped, updated in place');

  const cols = [{ key: 'name', label: 'Check' }, { label: 'Mark', render: (r) => UI.pill(r.s, r.s) }];
  const t = UI.table(cols, [{ name: '<x>', s: 'ok' }], { bodyId: 'tb', rowClass: (r) => 'gs-row--' + r.s });
  check(t.includes('<th>Check</th>') && t.includes('id="tb"') && t.includes('<tr class="gs-row--ok">') &&
        t.includes('&lt;x&gt;') && t.includes('gs-pill--ok'), 'table ' + t);
  const e = UI.tableRows(cols, [], { empty: 'No flights yet', emptyHint: 'later' });
  check(e.includes('colspan="2"') && e.includes('class="gs-empty"') && e.includes('No flights yet') &&
        e.includes('gs-empty__hint'), 'empty table ' + e);
  pass('K3', 'data table and empty state');

  check(UI.ageStatus(500) === 'ok' && UI.ageStatus(1500) === 'warn' && UI.ageStatus(5000) === 'stale' &&
        UI.ageStatus(null) === 'stale', 'ageStatus');
  check(UI.fmtAge(1234) === '1.2 s' && UI.fmtAge(45000) === '45 s' && UI.fmtAge(600000) === '10 min' &&
        UI.fmtAge(undefined) === 'no data' && UI.fmtAge(-5) === '0.0 s', 'fmtAge');
  check(/gs-age gs-age--warn/.test(UI.staleMark(2000)) && UI.staleMark(2000).includes('2.0 s') &&
        /age of the last frame/.test(UI.staleMark(2000)), 'staleMark');
  pass('K4', 'stale-data mark: age of the last frame, ok < 1 s <= warn < 3 s <= stale');

  const b = new El('button', doc); b.title = 'Send'; const why = new El('span', doc);
  UI.setDisabled(b, 'tick 2 checklist items', why);
  check(b.disabled && b.title === 'Disabled: tick 2 checklist items' && why.textContent === 'tick 2 checklist items', 'setDisabled on');
  UI.setDisabled(b, '', why);
  check(!b.disabled && b.title === 'Send' && why.textContent === '', 'setDisabled off');
  pass('K5', 'disabled state shows its reason next to the control');

  const btn = new El('button', doc); btn.innerHTML = 'Grant'; btn.textContent = 'Grant';
  let runs = 0;
  UI.twoClick(btn, () => { runs++; });
  btn.click();
  check(runs === 0 && btn.textContent === 'Confirm Grant?' && /is-armed/.test(btn.className), 'first click must only arm');
  btn.click();
  check(runs === 1 && btn.innerHTML === 'Grant' && !/is-armed/.test(btn.className), 'second click must run and restore');
  btn.click(); env.advance(5001);
  check(!/is-armed/.test(btn.className) && btn.innerHTML === 'Grant', 'arming must expire after the window');
  btn.click();
  check(runs === 1, 'a click after the window re-arms instead of running');
  btn.disabled = true; btn.click(); btn.click();
  check(runs === 1, 'disabled button never runs');
  const b2 = new El('button', doc); b2.innerHTML = 'Reset';
  check(UI.confirmClick(b2, { armedLabel: 'Confirm reset?' }) === false && b2.textContent === 'Confirm reset?' &&
        UI.confirmClick(b2) === true, 'confirmClick imperative core');
  pass('K6', 'two-click confirm: arm, confirm inside 5 s, expire, never on a disabled button');

  for (let i = 0; i < 6; i++) UI.toast('t' + i, i === 5 ? 'fail' : 'ok');
  const box = doc.getElementById('gs-toasts');
  check(box && box.getAttribute('role') === 'status' && box.getAttribute('aria-live') === 'polite', 'toast region');
  check(box.children.length === 4 && box.children[3].className === 'gs-toast gs-toast--fail', 'toast cap / status');
  env.advance(10001);
  check(box.children.length === 0, 'toasts expire');
  pass('K7', 'toast: live region, at most 4, status colour, auto-dismiss');

  const txt = UI.report('Go', new Error('Conflict 409'));
  check(txt === 'Go: Conflict 409' && env.warns.some((w) => w.includes('Go: Conflict 409')) &&
        box.children.length === 1 && box.children[0].className === 'gs-toast gs-toast--fail', 'report');
  const before = env.warns.length;
  UI.report('localStorage', new Error('blocked'), { toast: false, once: 's' });
  UI.report('localStorage', new Error('blocked'), { toast: false, once: 's' });
  check(env.warns.length === before + 1 && box.children.length === 1, 'report once / no toast');
  pass('K8', 'one error path: console + toast, returns the in-panel text, once-only option');

  env.ctx._route = (url) => url === '/bad' ? reply(false, 409, { error: 'no active run' })
    : (url === '/html' ? Promise.resolve({ ok: false, status: 502, json: () => Promise.reject(new Error('x')) })
      : (url === '/hang' ? new Promise(() => {}) : reply(true, 200, { a: 1 })));
  const pending = env.clock.timers.length;
  const ok = await UI.fetchJson('/good');
  check(ok.a === 1 && env.clock.timers.length === pending, 'fetchJson ok must clear its timer');
  let err = await UI.fetchJson('/bad').catch((x) => x);
  check(err.message === 'no active run', 'server error text: ' + err.message);
  err = await UI.fetchJson('/html').catch((x) => x);
  check(err.message === 'HTTP 502', 'non-JSON error: ' + err.message);
  const hung = UI.fetchJson('/hang', { timeoutMs: 1000 }).catch((x) => x);
  env.advance(1001);
  err = await hung;
  check(err.message === 'no reply in 1 s', 'timeout: ' + err.message);
  await UI.fetchJson('/good', { json: { source: 'operator' } });
  const last = env.fetches[env.fetches.length - 1];
  check(last.init.method === 'POST' && last.init.headers['Content-Type'] === 'application/json' &&
        JSON.parse(last.init.body).source === 'operator', 'json body');
  pass('K9', 'fetchJson: server error text, HTTP fallback, timeout, JSON POST');

  let calls = 0, fail = false, release = null;
  const poll = UI.poller(() => { calls++; return fail ? Promise.reject(new Error('down')) : new Promise((r) => { release = r; }); });
  env.tickAll(); env.tickAll();
  check(calls === 1, 'no second run while one is in flight');
  release(); await settle();
  fail = true;
  env.tickAll(); await settle();
  env.tickAll(); await settle();
  check(calls === 3 && poll.failures() === 2, 'two failures counted');
  env.tickAll(); await settle();
  check(calls === 3, 'backoff skips the next tick after the 2nd failure');
  env.tickAll(); await settle();
  check(calls === 4, 'runs again after the backoff gap');
  fail = false; release = null;
  env.tickAll(); await settle(); env.tickAll(); await settle(); env.tickAll(); await settle();
  check(calls === 4, 'after the 3rd failure the next 3 ticks are skipped');
  env.tickAll(); await settle();
  check(calls === 5, 'runs again after the 3-tick gap');
  release(); await settle();
  check(poll.failures() === 0, 'success resets the failure count');
  const nIntervals = Object.keys(env.clock.intervals).length;
  poll.stop();
  check(Object.keys(env.clock.intervals).length === nIntervals - 1, 'stop clears the interval');
  pass('K10', 'poller: one in flight, backoff doubles from the 2nd failure, resets on success, stops');

  let hits = 0;
  const off = UI.shortcut('p', () => { hits++; });
  doc.key({ key: 'p', target: doc.body });
  doc.key({ key: 'P', target: doc.body });
  doc.key({ key: 'p', target: new El('input', doc) });
  doc.key({ key: 'p', target: new El('textarea', doc) });
  doc.key({ key: 'p', target: Object.assign(new El('div', doc), { isContentEditable: true }) });
  doc.key({ key: 'p', target: Object.assign(new El('div', doc), { _closest: {} }) });
  doc.key({ key: 'p', ctrlKey: true, target: doc.body });
  doc.key({ key: 'p', repeat: true, target: doc.body });
  doc.activeElement = new El('select', doc); doc.key({ key: 'p', target: doc.body }); doc.activeElement = null;
  check(hits === 2, 'shortcut fired ' + hits + ' times, want 2 (plain p / P on the page only)');
  off(); doc.key({ key: 'p', target: doc.body });
  check(hits === 2, 'removed shortcut still fires');
  pass('K11', 'shortcut focus guard: ignores fields, terminal, modifiers, auto-repeat');

  check(doc.documentElement.getAttribute('data-theme') === 'dark', 'dark is the default theme');
  UI.setTheme('light');
  check(UI.getTheme() === 'light' && env.store.gs_theme === 'light', 'setTheme light');
  env.store.gs_theme = 'neon'; UI.initTheme();
  check(UI.getTheme() === 'dark', 'unknown stored theme falls back to dark');
  pass('K12', 'theme: dark default, light stored, garbage -> dark');

  check(UI.campaignActive({ status: 'running' }) && UI.campaignActive({ status: 'waiting_for_go' }) &&
        !UI.campaignActive({ status: 'idle' }) && !UI.campaignActive({ status: 'operator_needed' }) &&
        !UI.campaignActive(null), 'campaignActive');
  env.ctx._route = () => reply(true, 200, { ok: true });
  await UI.campaignCommand('abort');
  const ab = env.fetches[env.fetches.length - 1];
  check(ab.url === '/api/campaign/abort' && ab.init.method === 'POST' && JSON.parse(ab.init.body).source === 'operator', 'campaignCommand');
  pass('K13', 'campaign safety actions: active = running | waiting_for_go; POST {source: operator}');
}

// ── S: flight strip ──────────────────────────────────────────────────────────────────────────────────
async function stripChecks() {
  const env = makeEnv();
  const { ctx, doc, UI } = env;
  const strip = new El('div', doc); strip.id = 'flight-strip'; doc.byId['flight-strip'] = strip;
  let reg = null;
  ctx.window.__registerPlugin__ = (name, init, destroy, meta) => { reg = { name, init, destroy, meta }; };
  let camp = { status: 'idle', banner: 'idle' }, campFail = false;
  const posts = [];
  ctx._route = (url, init) => {
    if (url === '/api/campaign/state') return campFail ? reply(false, 500, { error: 'service down' }) : reply(true, 200, camp);
    if (init.method === 'POST') { posts.push({ url, body: JSON.parse(init.body) }); return reply(true, 200, { ok: true }); }
    return reply(false, 404, { error: 'not found' });
  };
  const nowNs = () => env.clock.now * 1e6;
  let state = null, arm = null, subscriber = null;
  const api = { subscribe(cb) { subscriber = cb; }, getState: () => state, getArmState: () => arm };
  vm.runInContext(read(path.join(PLUGINS, 'flight-strip.js')), ctx, { filename: 'flight-strip.js' });
  check(reg && reg.name === 'Flight Strip' && reg.meta.workspace === 'all', 'strip registration');
  reg.init(api);
  await settle();
  const el = (id) => doc.getElementById(id);
  ['fs-arm', 'fs-mode', 'fs-bat', 'fs-rc', 'fs-link', 'fs-campaign', 'fs-pause', 'fs-land', 'fs-abort', 'fs-reason']
    .forEach((id) => check(el(id), 'strip element missing: ' + id));
  check(typeof subscriber === 'function', 'strip must subscribe to shell state');
  pass('S1', 'registers for every workspace and renders ARM/MODE/BAT/RC/LINK/campaign + Pause/Land/Abort');

  const ev = ctx.window.__gs_ui_state__.flightStrip.evaluate;
  const live = { connected: true, streams: { 0: { last_update_ns: nowNs() - 2e8, values: {
    'status.flymode': 1, 'status.vbat': 16.2, 'status.sbus_lost': 0 } } } };
  let v = ev(live, 'armed', env.clock.now);
  check(v.arm.status === 'warn' && v.arm.text === 'ARMED' && v.mode.text === 'SDK' && v.bat.text === '16.20 V' &&
        v.bat.status === 'ok' && v.rc.status === 'ok' && v.link.status === 'ok' && v.link.text === '0.2 s', JSON.stringify(v));
  check(ev(live, 'disarmed', env.clock.now).arm.status === 'ok' && ev(live, null, env.clock.now).arm.text === 'UNKNOWN' &&
        ev(live, { armed: false }, env.clock.now).arm.status === 'stale', 'arm mapping');
  const lost = { connected: true, streams: { 0: { last_update_ns: nowNs() - 2e9, values: { 'slot0.status.sbus_lost': 1 } } } };
  v = ev(lost, null, env.clock.now);
  check(v.rc.status === 'fail' && v.rc.text === 'LOST' && v.link.status === 'warn' && v.bat.text === 'n/p', JSON.stringify(v));
  const old = { connected: true, streams: { 0: { last_update_ns: nowNs() - 5e9, values: { 'status.vbat': 15.9 } } } };
  v = ev(old, null, env.clock.now);
  check(v.link.status === 'fail' && v.bat.status === 'stale' && /ago/.test(v.bat.title), JSON.stringify(v));
  check(ev(null, null, env.clock.now).link.text === 'NO LINK' &&
        ev({ connected: true, streams: {} }, null, env.clock.now).link.text === 'NO DATA', 'no link / no data');
  pass('S2', 'state mapping: armed amber, RC lost red, link age ok/warn/red, frozen values grey with age, no invented battery limit');

  state = live; arm = 'disarmed'; subscriber(live);
  check(el('fs-arm').className.includes('gs-pill--ok') && el('fs-link').textContent === '0.2 s', 'strip renders shell state');
  check(el('fs-land').disabled && el('fs-abort').disabled && el('fs-pause').disabled &&
        el('fs-reason').textContent === 'no campaign running (idle)' && /Disabled: no campaign running/.test(el('fs-abort').title),
    'idle: safety buttons disabled with the reason shown');
  pass('S3', 'idle campaign: Pause/Land/Abort disabled, reason visible beside them');

  camp = { status: 'running', banner: 'flying flight 2/3: hover_z070' };
  env.tickAll(); await settle();
  check(!el('fs-land').disabled && !el('fs-abort').disabled && el('fs-reason').textContent === '' &&
        el('fs-campaign-text').textContent === 'flying flight 2/3: hover_z070' && el('fs-campaign').className.includes('gs-pill--info'),
    'running: enabled, banner shown');
  posts.length = 0;
  el('fs-land').click(); await settle();
  check(posts.length === 1 && posts[0].url === '/api/campaign/land' && posts[0].body.source === 'operator', 'land: one POST per click');
  pass('S4', 'running campaign: one click on Land sends exactly one POST (never delayed)');

  posts.length = 0;
  doc.key({ key: 'p', target: doc.body }); await settle();
  doc.key({ key: 'p', target: new El('input', doc) }); await settle();
  check(posts.length === 1 && posts[0].url === '/api/campaign/pause', 'P pauses once; typing p in a field does not');
  pass('S5', 'P = Pause, focus-guarded');

  campFail = true;
  env.tickAll(); await settle();
  check(!el('fs-abort').disabled && el('fs-campaign').textContent === 'unknown' &&
        el('fs-campaign-text').textContent.includes('service down'), 'unknown state keeps safety actions enabled');
  campFail = false; camp = { status: 'complete', banner: 'done: logs/x' };
  env.tickAll(); await settle(); env.tickAll(); await settle(); env.tickAll(); await settle();
  check(el('fs-abort').disabled, 'complete: disabled again');
  posts.length = 0;
  doc.key({ key: 'p', target: doc.body }); await settle();
  check(posts.length === 0 && doc.getElementById('gs-toasts').children.some((t) => /Pause not sent/.test(t.textContent)),
    'P with no run: no POST, a warning toast');
  pass('S6', 'campaign state unknown: actions stay enabled (fail-open); no run: P only warns');

  const nIntervals = Object.keys(env.clock.intervals).length;
  reg.destroy();
  check(Object.keys(env.clock.intervals).length === nIntervals - 2, 'destroy must clear the poll and the clock');
  check((doc.handlers.keydown || []).length === 0, 'destroy must remove the P handler');
  pass('S7', 'teardown clears timers and the shortcut');
}

// ── H: shell wiring ──────────────────────────────────────────────────────────────────────────────────
function shellChecks() {
  const html = read(path.join(SHELL, 'index.html'));
  const iTok = html.indexOf('href="/ui/tokens.css"'), iCmp = html.indexOf('href="/ui/components.css"');
  const iKit = html.indexOf('<script src="/ui/ui-kit.js"></script>'), iMain = html.indexOf('<script>');
  check(iTok > 0 && iCmp > iTok && iKit > iCmp && iMain > iKit, 'tokens -> components -> kit -> shell script order');
  const files = html.match(/const PLUGIN_FILES = \[([\s\S]*?)\];/);
  check(files && /^\s*'\/plugins\/flight-strip\.js'/.test(files[1]), 'flight-strip.js must be the first plugin');
  const iStrip = html.indexOf('<div id="flight-strip"'), iBar = html.indexOf('<div id="workspace-bar"');
  const iApp = html.indexOf('<div id="app">');
  check(iStrip > 0 && iStrip < iBar && iStrip < iApp, '#flight-strip above the tabs and outside #app');
  check(!/:root\s*\{/.test(html), 'index.html must not define its own :root tokens');
  pass('H1', 'index.html loads tokens, components and kit before plugins; strip is outside every workspace');

  const tok = read(path.join(SHELL, 'ui', 'tokens.css'));
  check(/:root\[data-theme="light"\]/.test(tok) && /:root\[data-theme="dark"\]/.test(tok), 'both themes');
  ['ok', 'warn', 'fail', 'stale', 'info'].forEach((s) => check(new RegExp('--gs-' + s + ':').test(tok) &&
    new RegExp('--gs-' + s + '-bg:').test(tok), 'status token ' + s));
  ['--gs-space-1', '--gs-space-5', '--gs-text-xs', '--gs-text-xl', '--gs-radius-sm', '--gs-radius-pill', '--gs-font-mono',
   '--red: var(--gs-fail)', '--muted: var(--gs-text-muted)'].forEach((t) => check(tok.includes(t), 'token ' + t));
  const cmp = read(path.join(SHELL, 'ui', 'components.css'));
  check(!/#[0-9a-fA-F]{3,8}\b(?![\w-])/.test(cmp.replace(/#(gs-toasts|flight-strip)\b/g, '')), 'components.css hex colour');
  pass('H2', 'tokens.css: dark + light, status scale with backgrounds, spacing, type, radius, legacy aliases; components use tokens only');
}

// ── L: lint rules (no npm) ───────────────────────────────────────────────────────────────────────────
function lintChecks() {
  const all = fs.readdirSync(PLUGINS).filter((f) => f.endsWith('.js'));
  for (const f of all) {
    check(!/\b(window\.)?(confirm|alert)\(/.test(read(path.join(PLUGINS, f))), f + ' calls confirm/alert');
  }
  pass('L1', 'no window.confirm / alert in any plugin (' + all.length + ' files)');
  const converted = ['campaign-panel.js', 'estimator-panel.js', 'flight-strip.js', 'approval-queue.js'];
  const sources = converted.map((f) => [f, read(path.join(PLUGINS, f))]).concat([['ui-kit.js', read(KIT)],
    ['index.html', read(path.join(SHELL, 'index.html'))]]);
  for (const [f, src] of sources) {
    check(!/catch\s*(\(\s*\w*\s*\))?\s*\{\s*(\/\*[^*]*\*\/\s*)?\}/.test(src), f + ' has an empty catch');
  }
  pass('L2', 'no empty / comment-only catch in the converted files and the shell');
  for (const [f, src] of sources) {
    check(!/#[0-9a-fA-F]{6}\b|rgba\(/.test(src), f + ' has a hex/rgba colour (use a token)');
    check(!/\sonclick=/.test(src), f + ' has an inline onclick');
  }
  check(fs.existsSync(path.join(SHELL, '..', 'eslint.config.mjs')), 'dev-only eslint config missing');
  pass('L3', 'converted files + shell: colours via tokens only, no inline onclick; eslint config present (dev-only)');
}

(async () => {
  await kitChecks();
  await stripChecks();
  shellChecks();
  lintChecks();
  console.log('ALL CHECKS PASSED');
})().catch((e) => { console.error('FAIL: ' + e.message); process.exit(1); });
