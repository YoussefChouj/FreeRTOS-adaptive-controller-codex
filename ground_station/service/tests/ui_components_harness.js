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
 *   B  ui/alarms.js (WP-39, WP-32 D2): one registry, three levels, every alarm names its action, debounce over
 *      N samples, advisories log only, ack / silence / CSV, the shell sidebar and the Overview read it
 *   C  voice (D3): speechSynthesis for warnings only, one utterance per episode, silence cancels it
 * Run: node ground_station/service/tests/ui_components_harness.js   (exit 0 = all pass)
 */
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const { KIT, ALARMS } = require('./ui_kit_loader');

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
  contains() { return false; }
  get classList() {
    const el = this;
    const list = () => String(el.className || '').split(/\s+/).filter(Boolean);
    return {
      add(c) { const l = list(); if (!l.includes(c)) l.push(c); el.className = l.join(' '); },
      remove(c) { el.className = list().filter((x) => x !== c).join(' '); },
      contains(c) { return list().includes(c); },
    };
  }
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

function makeEnv(extra, sharedStore) {
  const clock = { now: 1e6, timers: [], intervals: {}, seq: 0 };
  const warns = [];
  const fetches = [];
  const store = sharedStore || {};
  const doc = makeDoc();
  const ctx = {
    window: {}, document: doc, JSON, Math,
    console: { warn: (m) => warns.push(String(m)), error() {}, log() {} },
    // new Date(ms) stays real (CSV timestamps); Date.now() is the fake clock
    Date: Object.assign(function FakeDate(...a) { return new Date(...a); }, { now: () => clock.now }),
    setTimeout(fn, ms) { const id = ++clock.seq; clock.timers.push({ id, fn, at: clock.now + (ms || 0) }); return id; },
    clearTimeout(id) { clock.timers = clock.timers.filter((t) => t.id !== id); },
    setInterval(fn) { const id = ++clock.seq; clock.intervals[id] = fn; return id; },
    clearInterval(id) { delete clock.intervals[id]; },
    localStorage: { getItem: (k) => (k in store ? store[k] : null), setItem: (k, v) => { store[k] = String(v); } },
    fetch(url, init) { fetches.push({ url, init: init || {} }); return ctx._route(url, init || {}); },
    _route: () => new Promise(() => {}),
  };
  ctx.window.document = doc;
  Object.assign(ctx.window, extra || {});
  vm.createContext(ctx);
  vm.runInContext(read(KIT), ctx, { filename: KIT });
  vm.runInContext(read(ALARMS), ctx, { filename: ALARMS });
  const advance = (ms) => {
    clock.now += ms;
    const due = clock.timers.filter((t) => t.at <= clock.now);
    clock.timers = clock.timers.filter((t) => t.at > clock.now);
    due.forEach((t) => t.fn());
  };
  const tickAll = () => Object.values(clock.intervals).forEach((fn) => fn());
  return { ctx, doc, clock, warns, fetches, store, advance, tickAll, UI: ctx.window.GSUI, AL: ctx.window.GSAlarms };
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

// ── B: alarm registry (ui/alarms.js) ─────────────────────────────────────────────────────────────────
// a /state snapshot: slot 0 fresh (ageMs old) with the given values
function snap(nowMs, values, opts) {
  opts = opts || {};
  const s = { slot_freshness_ttl_ns: 30e9, streams: { 0: { last_update_ns: (nowMs - (opts.ageMs || 100)) * 1e6,
    loss_pct: opts.loss || 0, values: Object.assign({ 'status.arm': 1, 'status.vbat': 16.2, 'status.sbus_lost': 0,
      'status.estimator_ready': 1 }, values) } } };
  if (opts.slot3AgeMs !== undefined) s.streams[3] = { last_update_ns: (nowMs - opts.slot3AgeMs) * 1e6, values: {} };
  return s;
}

function alarmChecks() {
  const { AL } = makeEnv();
  check(AL && AL.version === 1 && AL.RULES.length >= 10, 'window.GSAlarms missing');
  check(AL.LEVELS.warning.status === 'fail' && AL.LEVELS.warning.sev === 'red' && AL.LEVELS.warning.speak &&
        AL.LEVELS.caution.status === 'warn' && !AL.LEVELS.caution.speak &&
        AL.LEVELS.advisory.status === 'info' && !AL.LEVELS.advisory.shown, 'level mapping');
  AL.RULES.forEach((r) => check(AL.LEVELS[r.level].shown ? r.action.trim().length > 10 : true,
    r.id + ' (' + r.level + ') must name its required action'));
  const ok = { id: 'x', level: 'caution', n: 1, check: () => null, action: 'Do something' };
  const refuse = (rule, why) => {
    let err = null;
    try { AL.validate([rule]); } catch (e) { err = e; }
    check(err && why.test(err.message), 'validate must refuse: ' + JSON.stringify(rule) + ' -> ' + (err && err.message));
  };
  refuse(Object.assign({}, ok, { action: ' ' }), /must name its required action/);
  refuse(Object.assign({}, ok, { level: 'red' }), /unknown level/);
  refuse(Object.assign({}, ok, { n: 0 }), /debounce/);
  check(AL.validate([Object.assign({}, ok, { level: 'advisory', action: '' })]).length === 1, 'advisory needs no action');
  let dup = null;
  try { AL.validate([ok, ok]); } catch (e) { dup = e; }
  check(dup && /duplicate/.test(dup.message), 'duplicate id refused');
  pass('B1', 'one registry: warning red / caution amber / advisory log only; every alarm names its action or validate() refuses it');

  // debounce: vbat-low (n = 3) raises on the 3rd consecutive sample, clears after 3 good ones
  const eng = new AL.Engine({ speaker: null });
  let t = 1e9;
  const low = () => snap(t, { 'status.vbat': 14.2 });
  const good = () => snap(t, {});
  check(eng.update(low(), t += 500).length === 0 && eng.update(low(), t += 500).length === 0, 'raised before 3 samples');
  eng.update(good(), t += 500);
  check(eng.update(low(), t += 500).length === 0 && eng.update(low(), t += 500).length === 0,
    'a good sample in between must restart the count');
  let a = eng.update(low(), t += 500);
  check(a.length === 1 && a[0].id === 'vbat-low' && a[0].level === 'warning' && a[0].label === 'WARNING' &&
        a[0].action === 'Land now and swap the pack' && /BATTERY LOW: 14\.20 V/.test(a[0].text), JSON.stringify(a));
  const same = low();
  eng.update(same, t += 500);
  eng.update(same, t += 10);                         // same snapshot again inside SAMPLE_MS: not a new sample
  eng.update(good(), t += 500); eng.update(good(), t += 500);
  check(eng.alarms().length === 1, 'clears only after 3 good samples');
  const batEp = () => eng.log().find((ep) => ep.id === 'vbat-low');
  check(eng.update(good(), t += 500).length === 0 && batEp().clearedAt === t, 'cleared on the 3rd good sample');
  check(eng.log().some((ep) => ep.id === 'armed' && ep.level === 'advisory'), 'armed is logged as an advisory');
  const frozen = snap(t, {}, { ageMs: 100 });
  eng.update(frozen, t += 100);
  check(eng.update(frozen, t += AL.SAMPLE_MS + 4000).some((x) => x.id === 'link-lost'),
    'a frozen snapshot is re-evaluated after SAMPLE_MS: link lost fires when the poll dies');
  pass('B2', 'debounce over N samples (raise and clear); one sample per /state snapshot; frozen state still ages');

  // same condition, level by context: may fly (armed or arm unknown) = warning, disarmed = caution
  const lvl = (values, opts) => {
    const e = new AL.Engine({ speaker: null });
    let tt = 2e9, out = [];
    for (let i = 0; i < 3; i++) out = e.update(snap(tt, values, opts), tt += 500);
    return out.map((x) => x.id + ':' + x.level).sort().join(',');
  };
  check(lvl({ 'status.vbat': 14.2, 'status.arm': 0 }) === 'vbat-low-ground:caution', 'low battery on the ground');
  check(lvl({ 'status.sbus_lost': 1 }) === 'rc-lost:warning' && lvl({ 'status.sbus_lost': 1, 'status.arm': 0 }) === 'rc-off:caution',
    'RC lost: warning while it may fly, caution on the ground');
  check(lvl({}, { ageMs: 5000 }) === 'link-lost:warning' && lvl({ 'status.arm': 0 }, { ageMs: 5000 }) === 'link-down:caution',
    'link lost: warning while armed, caution disarmed');
  check(lvl({ 'status.vbat': 15.3 }) === 'vbat-warn:caution' && lvl({ 'status.estimator_ready': 0 }) === 'estimator:caution',
    'early battery warning and estimator are cautions');
  check(lvl({}, { loss: 6 }) === 'loss-0:caution', 'loss above 5 % is a caution per slot');
  pass('B3', 'act now vs act soon by context: armed / arm unknown = warning, known disarmed = caution');

  // advisories: log only; notelem: shown, not logged
  const e2 = new AL.Engine({ speaker: null });
  let t2 = 3e9;
  for (let i = 0; i < 3; i++) e2.update(snap(t2, {}, { slot3AgeMs: 2500, loss: 2 }), t2 += 500);
  check(e2.alarms().length === 0, 'advisories must not be alarm rows: ' + JSON.stringify(e2.alarms()));
  const adv = e2.advisories().map((x) => x.id).sort().join(',');
  check(adv === 'armed,loss-minor-0,slow-3', 'advisories ' + adv);
  check(e2.log().length === 3 && e2.log().every((ep) => ep.level === 'advisory'), 'advisories go to the log');
  const e3 = new AL.Engine({ speaker: null });
  const none = e3.update({ streams: {} }, 1);
  check(none.length === 1 && none[0].id === 'notelem' && none[0].level === 'caution' && e3.log().length === 0,
    'no telemetry is shown (caution) but is not a logged episode');
  pass('B4', 'advisory = log only (slow slot, minor loss, armed); start-up "no telemetry" shown, not logged');

  const ref = a[0].ref;
  check(eng.ack(ref) && batEp().ack && eng.silence(ref) && batEp().silenced &&
        eng.silence(ref) && !batEp().silenced && !eng.ack(999), 'ack / silence toggles');
  const csv = eng.csv().split('\r\n');
  check(/^"raised_at",.*"required_action"$/.test(csv[0]) &&
        csv.some((l) => /"vbat-low","warning",.*"yes","no","Land now and swap the pack"$/.test(l)),
    'csv ' + csv.join(' | '));
  const small = new AL.Engine({ speaker: null, logMax: 2 });
  let t4 = 4e9;
  for (let i = 0; i < 3; i++) {
    small.update(snap(t4, { 'status.estimator_ready': 0 }), t4 += 500);
    small.update(snap(t4, { 'status.estimator_ready': 0 }), t4 += 500);
    small.update(snap(t4, {}), t4 += 500); small.update(snap(t4, {}), t4 += 500);
  }
  check(small.log().filter((ep) => ep.id === 'estimator').length === 2 && small.dropped() >= 1, 'bounded log counts drops');
  pass('B5', 'ack and silence are display-only flags; CSV keeps level + required action; bounded log counts drops');

  const html = read(path.join(SHELL, 'index.html'));
  const iKit = html.indexOf('<script src="/ui/ui-kit.js"></script>');
  const iAl = html.indexOf('<script src="/ui/alarms.js"></script>');
  check(iAl > iKit && iAl < html.indexOf('<script>'), 'alarms.js must load after ui-kit.js and before the shell script');
  check(/GSAlarms\.shared\(\)/.test(html) && !/function buildAlarms/.test(html) && /alarm-action/.test(html) &&
        /data-alarm-ref/.test(html), 'sidebar Alarms card must render the shared engine (level, action, Silence)');
  const ov = read(path.join(PLUGINS, 'overview-panel.js'));
  check(/GSAlarms\.shared\(\)/.test(ov) && !/VBAT_RED_V\s*=\s*15/.test(ov) && /eng\.silence\(ref\)/.test(ov),
    'Overview must read the same engine and its LIMITS');
  pass('B6', 'shell sidebar and Overview both read GSAlarms.shared(); no second rule set left');
}

// ── C: voice for warnings (speechSynthesis) ──────────────────────────────────────────────────────────
function voiceChecks() {
  const said = [], cancels = [];
  const synth = { speak(u) { said.push(u.text); }, cancel() { cancels.push(said.length); } };
  function Utt(text) { this.text = text; }
  const { AL } = makeEnv({ speechSynthesis: synth, SpeechSynthesisUtterance: Utt });
  const eng = AL.shared();
  check(eng === AL.shared() && eng.speaker, 'shared engine with the browser speaker');
  let t = 1e9;
  for (let i = 0; i < 6; i++) eng.update(snap(t, { 'status.sbus_lost': 1 }), t += 500);
  check(said.length === 1 && said[0] === 'Warning. R C link lost. Press Abort or Land, then check the RC transmitter is on and in range.',
    'one utterance per warning episode: ' + JSON.stringify(said));
  for (let i = 0; i < 3; i++) eng.update(snap(t, { 'status.estimator_ready': 0, 'status.arm': 0 }), t += 500);
  check(said.length === 1, 'cautions and advisories never speak: ' + JSON.stringify(said));
  pass('C1', 'speechSynthesis for warnings only, once per episode (6 samples active -> 1 utterance)');

  for (let i = 0; i < 3; i++) eng.update(snap(t, { 'status.vbat': 14.0 }), t += 500);
  check(said.length === 2 && /^Warning\. Battery low\. Land now/.test(said[1]), 'battery warning spoken');
  const bat = eng.alarms().find((x) => x.id === 'vbat-low');
  eng.silence(bat.ref);
  check(cancels.length === 1 && cancels[0] === 2, 'silencing the speaking episode cancels its utterance');
  eng.silence(bat.ref);
  for (let i = 0; i < 3; i++) eng.update(snap(t, { 'status.vbat': 14.0 }), t += 500);
  check(said.length === 2, 'un-silencing never repeats the utterance');
  for (let i = 0; i < 3; i++) eng.update(snap(t, {}), t += 500);
  for (let i = 0; i < 3; i++) eng.update(snap(t, { 'status.vbat': 14.0 }), t += 500);
  check(said.length === 3, 'a new episode (after a clear) speaks again');
  pass('C2', 'silence (the existing control) cancels the voice; a re-raise after a clear is a new episode');

  const quiet = makeEnv().AL;
  check(quiet.browserSpeaker() === null && quiet.shared().speaker === null, 'no speech API -> no speaker');
  let q = 1e9;
  for (let i = 0; i < 2; i++) quiet.shared().update(snap(q, { 'status.sbus_lost': 1 }), q += 500);
  check(quiet.shared().alarms()[0].id === 'rc-lost', 'alarms work without speech');
  const loud = makeEnv({ speechSynthesis: { speak() { throw new Error('not allowed'); }, cancel() {} },
    SpeechSynthesisUtterance: Utt });
  let r = 1e9;
  loud.AL.shared().update(snap(r, { 'status.sbus_lost': 1 }), r += 500);
  check(loud.AL.shared().alarms()[0].id === 'rc-lost' && loud.warns.some((w) => /alarm voice: not allowed/.test(w)),
    'a refused utterance is reported and the alarm still shows');
  pass('C3', 'no speech API or a refused utterance: the alarm still shows, the failure is reported once');
}

// ── E: drag a telemetry key onto a plot; named plot layouts (WP-32 D5) ───────────────────────────────
function fire(el, type, ev) { (el.handlers[type] || []).forEach((fn) => fn(ev)); return ev; }
function dragEvent(type, data, types) {
  return { type, prevented: false, preventDefault() { this.prevented = true; },
    dataTransfer: { types: types || Object.keys(data || {}), dropEffect: 'none', effectAllowed: 'all', _d: Object.assign({}, data),
      getData(t) { return this._d[t] || ''; }, setData(t, v) { this._d[t] = v; this.types = Object.keys(this._d); } } };
}

function loadTimeSeries(env) {
  let reg = null, render = null, stateCb = null;
  env.ctx.window.__registerPlugin__ = (name, init, destroy) => { reg = { name, init, destroy }; };
  env.ctx.requestAnimationFrame = (cb) => { cb(); return 1; };
  vm.runInContext(read(path.join(PLUGINS, 'time-series-panel.js')), env.ctx, { filename: 'time-series-panel.js' });
  reg.init({ registerPanel(n, fn) { render = fn; }, subscribe(cb) { stateCb = cb; } });
  render(new El('div', env.doc));
  return { feed: (s) => stateCb(s), TS: env.ctx.window.__gs_ui_state__.timeSeries, el: (id) => env.doc.getElementById(id) };
}

function plotChecks() {
  const env = makeEnv();
  const { UI, ctx, doc } = env;
  ctx.window.__registerPlugin__ = () => {};
  vm.runInContext(read(path.join(PLUGINS, 'telemetry-explorer-panel.js')), ctx, { filename: 'telemetry-explorer-panel.js' });
  const src = read(path.join(PLUGINS, 'telemetry-explorer-panel.js'));
  check(/<tr draggable="true" data-gs-key="' \+ escapeHtml\(e\.key\)/.test(src) && /addEventListener\('dragstart'/.test(src),
    'explorer rows must be drag sources carrying the full key');
  const row = new El('tr', doc); row.setAttribute('data-gs-key', 'mrac.pitch.theta[2]');
  const ds = dragEvent('dragstart', {});
  ctx.window.__gs_ui_state__.telemetryExplorer.onDragStart(Object.assign(ds, { target: row }));
  check(ds.dataTransfer._d[UI.KEY_MIME] === 'mrac.pitch.theta[2]' && ds.dataTransfer._d['text/plain'] === 'mrac.pitch.theta[2]' &&
        ds.dataTransfer.effectAllowed === 'copy', 'dragstart sets the key');
  check(UI.droppedKey(dragEvent('drop', { 'text/plain': '<img src=x>' })) === '' &&
        UI.droppedKey(dragEvent('drop', { 'text/plain': '  ekf.vel_x ' })) === 'ekf.vel_x' &&
        !UI.carriesKey(dragEvent('dragover', {}, ['Files'])), 'drop accepts telemetry keys only');
  pass('E1', 'Telemetry Explorer rows are drag sources (key in a private MIME type + text/plain); junk is refused on drop');

  const ts = loadTimeSeries(env);
  const sample = () => ({ streams: { 0: { values: { 'c.altitude': 2.0, 'ekf.vel_x': 1.5 } } } });
  for (let i = 0; i < 3; i++) ts.feed(sample());
  const wrap = ts.el('ts-chart-wrap');
  check(wrap && wrap.handlers.drop, 'plot area must be a drop target');
  const over = fire(wrap, 'dragover', dragEvent('dragover', {}, [UI.KEY_MIME]));
  const foreign = fire(wrap, 'dragover', dragEvent('dragover', {}, ['Files']));
  check(over.prevented && over.dataTransfer.dropEffect === 'copy' && !foreign.prevented, 'dragover accepts only telemetry keys');
  fire(wrap, 'dragenter', dragEvent('dragenter', {}, [UI.KEY_MIME]));
  check(/ts-drop-active/.test(wrap.className), 'drop zone highlights while a key is over it');
  check(ts.TS.enabledKeys().indexOf('ekf.vel_x') < 0, 'ekf.vel_x starts unplotted');
  fire(wrap, 'drop', dragEvent('drop', { [UI.KEY_MIME]: 'ekf.vel_x' }));
  check(ts.TS.enabledKeys().indexOf('ekf.vel_x') >= 0 && !/ts-drop-active/.test(wrap.className) &&
        ts.el('ts-layout-msg').textContent === 'plotting ekf.vel_x', 'drop plots the key: ' + ts.el('ts-layout-msg').textContent);
  fire(wrap, 'drop', dragEvent('drop', { [UI.KEY_MIME]: 'ekf.vel_x' }));
  check(ts.el('ts-layout-msg').textContent === 'ekf.vel_x is already plotted', 'second drop is a no-op');
  fire(wrap, 'drop', dragEvent('drop', { 'text/plain': 'rm -rf /' }));
  check(/not a telemetry key/.test(ts.el('ts-layout-msg').textContent) && ts.el('ts-layout-msg').className === 'gs-reason', 'junk drop refused');
  fire(wrap, 'drop', dragEvent('drop', { [UI.KEY_MIME]: 'mrac.yaw.e' }));
  check(ts.TS.bufferLength('mrac.yaw.e') === ts.TS.sampleCount() && ts.TS.sampleCount() === 3,
    'a key that is not streaming yet is padded with gaps, aligned to the sample clock');
  pass('E2', 'drop a key on the plot: plotted at once, highlighted while over, duplicates and junk refused, gaps never invented');

  const save = ts.el('ts-layout-save'), name = ts.el('ts-layout-name'), sel = ts.el('ts-layout-select'), del = ts.el('ts-layout-delete');
  name.value = '  '; save.click();
  check(ts.el('ts-layout-msg').textContent === 'type a layout name first', 'empty name refused');
  name.value = 'hover tuning'; save.click();
  const stored = JSON.parse(env.store.gs_ts_layouts_v1);
  const hoverKeys = ts.TS.enabledKeys();
  check(JSON.stringify(stored['hover tuning'].keys) === JSON.stringify(hoverKeys) && stored['hover tuning'].viewMode === 'separate' &&
        /^saved "hover tuning": \d+ keys, separate$/.test(ts.el('ts-layout-msg').textContent), 'save: ' + env.store.gs_ts_layouts_v1);
  check(/<option value="hover tuning">hover tuning<\/option>/.test(sel.innerHTML) && sel.value === 'hover tuning', 'select lists the saved layout');
  ts.el('ts-mode-overlay').click();
  ts.TS.addKey('c.earth_x');
  name.value = 'overlay set'; save.click();
  check(JSON.parse(env.store.gs_ts_layouts_v1)['overlay set'].viewMode === 'overlay', 'view mode is part of the layout');
  ts.el('ts-btn-preset-clear').click();
  check(ts.TS.enabledKeys().length === 0, 'cleared');
  sel.value = 'hover tuning'; fire(sel, 'change', { target: sel });
  check(JSON.stringify(ts.TS.enabledKeys()) === JSON.stringify(hoverKeys) && ts.TS.viewMode() === 'separate' &&
        new RegExp('^restored "hover tuning": ' + hoverKeys.length + ' keys, 5 not streaming yet \\(shown as no data\\)$')
          .test(ts.el('ts-layout-msg').textContent),   // 4 unstreamed defaults + the dropped mrac.yaw.e
    'restore: ' + ts.el('ts-layout-msg').textContent);
  check(env.fetches.length === 0, 'layouts never touch the service');
  del.click();
  check(JSON.parse(env.store.gs_ts_layouts_v1)['hover tuning'] && /is-armed/.test(del.className), 'first Delete click only arms');
  del.click();
  check(!JSON.parse(env.store.gs_ts_layouts_v1)['hover tuning'] && ts.el('ts-layout-msg').textContent === 'deleted "hover tuning"',
    'second click deletes');
  pass('E3', 'named layouts: save (keys + view mode), list, restore, two-click delete; no request to the service');

  const env2 = makeEnv(null, env.store);   // a page reload in the same browser
  const ts2 = loadTimeSeries(env2);
  check(Object.keys(ts2.TS.readLayouts()).join() === 'overlay set' && ts2.TS.restoreLayout('overlay set').ok &&
        ts2.TS.viewMode() === 'overlay' && ts2.TS.enabledKeys().indexOf('c.earth_x') >= 0, 'layouts survive a reload');
  env2.store.gs_ts_layouts_v1 = '{not json';
  check(Object.keys(ts2.TS.readLayouts()).length === 0 && env2.warns.some((w) => /saved layouts/.test(w)), 'corrupt storage reported, not thrown');
  pass('E4', 'per-viewer storage (this browser): layouts survive a reload; corrupt storage is reported and ignored');
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
  const converted = ['campaign-panel.js', 'estimator-panel.js', 'flight-strip.js', 'approval-queue.js',
    // WP-39
    'overview-panel.js', 'status-panel.js', 'safety-panel.js', 'path-panel.js', 'time-series-panel.js',
    'telemetry-explorer-panel.js', 'streams-panel.js', 'slot-manager-panel.js', 'bandwidth-panel.js', 'fft-panel.js'];
  const sources = converted.map((f) => [f, read(path.join(PLUGINS, f))]).concat([['ui-kit.js', read(KIT)],
    ['alarms.js', read(ALARMS)], ['index.html', read(path.join(SHELL, 'index.html'))]]);
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
  alarmChecks();
  voiceChecks();
  plotChecks();
  shellChecks();
  lintChecks();
  console.log('ALL CHECKS PASSED');
})().catch((e) => { console.error('FAIL: ' + e.message); process.exit(1); });
