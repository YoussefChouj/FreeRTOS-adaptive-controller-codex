'use strict';
/**
 * Offline harness for streams-panel.js (Phase 3 Streams panel).
 * Fake DOM (id registry, additive innerHTML scan, delegated events), a
 * routing fetch stub that records every call, and a manual timer queue so
 * the poll / plan / autocomplete debounces can be driven deterministically.
 * No network call is possible: fetch is the stub, XHR throws.
 *
 * Run:  node ground_station/service/tests/streams_panel_harness.js
 */
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const assert = require('assert');

const PANEL = path.join(__dirname, '..', '..', '..',
  'docs', 'dashboard-platform', 'shell', 'plugins', 'streams-panel.js');

class Element {
  constructor(doc, id, tag, value) {
    this.doc = doc; this.id = id; this.tagName = tag.toUpperCase();
    this.value = value || ''; this.textContent = ''; this.disabled = false;
    this.className = ''; this.style = {}; this.handlers = {};
    this.parentNode = null; this.dataset = {}; this._html = '';
  }
  addEventListener(ev, fn) { (this.handlers[ev] = this.handlers[ev] || []).push(fn); }
  removeEventListener(ev, fn) {
    this.handlers[ev] = (this.handlers[ev] || []).filter((f) => f !== fn);
  }
  getAttribute(n) { return this.dataset[n] !== undefined ? this.dataset[n] : null; }
  get innerHTML() { return this._html; }
  set innerHTML(h) { this._html = h; this.doc.scan(h); }
  focus() { this.doc.focused = this.id; }
}
class FakeDocument {
  constructor() { this.elements = {}; this.focused = null; }
  scan(html) {
    const re = /<([a-zA-Z0-9]+)([^>]*)>/g;
    let m;
    while ((m = re.exec(html))) {
      const idm = m[2].match(/\bid="([^"]+)"/);
      if (!idm) continue;
      const valm = m[2].match(/\bvalue="([^"]*)"/);
      let val = valm ? valm[1] : '';
      if (m[1] === 'textarea') {
        const end = html.indexOf('</textarea>', re.lastIndex);
        val = end >= 0 ? html.slice(re.lastIndex, end) : '';
      }
      this.elements[idm[1]] = new Element(this, idm[1], m[1], val);
    }
  }
  getElementById(id) { return this.elements[id] || null; }
}

function load(routes) {
  const doc = new FakeDocument();
  const container = new Element(doc, 'container', 'div');
  container.parentNode = null;
  const calls = [];
  const timers = [];
  const sandbox = {
    document: doc,
    console: { log() {}, warn() {}, error() {} },
    Date, Math, Number, String, Boolean, Array, Object, JSON, RegExp,
    parseFloat, parseInt, isNaN, isFinite, encodeURIComponent, Promise,
    setTimeout(fn, ms) { timers.push({ fn, ms }); return timers.length; },
    clearTimeout(id) { if (timers[id - 1]) timers[id - 1].fn = null; },
    fetch(url, opts) {
      const method = (opts && opts.method) || 'GET';
      const body = opts && opts.body ? JSON.parse(opts.body) : null;
      calls.push({ url, method, body });
      const key = method + ' ' + url.split('?')[0];
      let hit = routes[key];
      if (typeof hit === 'function') hit = hit(url, body);
      hit = hit || { status: 404, body: {} };
      if (hit.reject) return Promise.reject(new Error(hit.reject));
      return Promise.resolve({ ok: hit.status < 400, status: hit.status,
        json: () => Promise.resolve(hit.body) });
    },
    XMLHttpRequest: function () { throw new Error('XHR blocked'); },
    window: null,
  };
  sandbox.window = sandbox;
  sandbox.__registerPlugin__ = function (n, i, d) { sandbox.pname = n; sandbox.pinit = i; sandbox.pdestroy = d; };
  vm.createContext(sandbox);
  vm.runInContext(fs.readFileSync(PANEL, 'utf8'), sandbox, { filename: PANEL });
  const api = { registerPanel(n, fn) { this.fn = fn; this.name = n; } };
  sandbox.pinit(api);
  const tick = () => new Promise((r) => setImmediate(r));
  const h = {
    doc, container, sandbox, api, calls, timers,
    T: sandbox.__STREAMS_TEST__,
    async settle() { for (let i = 0; i < 6; i++) await tick(); },
    async flush() {
      const run = timers.splice(0);
      run.forEach((t) => t.fn && t.fn());
      await h.settle();
    },
    render() { api.fn(container); },
    // Delegated event on a synthetic target.
    fire(ev, attrs, props) {
      const t = new Element(doc, (props && props.id) || '', 'button', (props && props.value) || '');
      Object.keys(attrs || {}).forEach((k) => { t.dataset[k] = String(attrs[k]); });
      t.parentNode = container;
      (container.handlers[ev] || []).slice().forEach((fn) => fn({ target: t }));
    },
    click(attrs, props) { h.fire('click', attrs, props); },
    body(url) { return calls.filter((c) => c.url.indexOf(url) === 0); },
  };
  return h;
}

const SLOTS = [
  { slot: 0, vars: ['a', 'b'], divider: 4, name: 'Dashboard layout', rate: 25, source: 'default', default: true, state: 'active', fixed: true },
  { slot: 1, vars: ['x.y'], divider: 5, name: 'Dashboard layout', rate: 20, source: 'default', default: true, state: 'active', fixed: false },
  { slot: 2, vars: ['p'], divider: 2, name: 'Sysid', rate: 50, source: 'preset:sysid', default: false, state: 'active', fixed: false },
  { slot: 3, vars: ['m'], divider: 4, name: 'Dashboard layout', rate: 25, source: 'default', default: true, state: 'active', fixed: false },
];
function snap(over) {
  return Object.assign({
    slots: SLOTS, arm_state: 'disarmed', can_swap: true, active_preset: null,
    plan: { total_bps: 20000, budget_bps: 87552, ok: true, slots: [0, 1, 2, 3].map((n) => ({ slot: n, ranges: 10, max_ranges: 62, payload: 100, bps: 5000, divider: 4, rate_real: 25, vars: [], errors: [] })) },
    tabs: [
      { tab: 'estimator', label: 'Estimator', ok: true, missing: [], reason: null, restore_slots: [] },
      { tab: 'motor', label: 'Motor bench', ok: false, missing: [{ var: 'motor.rpm_1', slot: 3 }], reason: 'not streamed: slot 3 holds Sysid', restore_slots: [3] },
    ],
    apply: { busy: false, error: null }, log: { active: false, files: [] }, forward: { active: false },
  }, over || {});
}
function routes(s, extra) {
  return Object.assign({
    'GET /api/streams': { status: 200, body: s },
    'GET /api/streams/presets': { status: 200, body: { presets: [{ name: 'sysid', notes: '', n_vars: 4, rates: [50] }] } },
    'GET /api/streams/presets/sysid': { status: 200, body: { name: 'sysid', slots: [{ rate: 50, vars: ['s1', 's2'] }, { rate: 10, vars: ['s3'] }] } },
    'POST /api/streams/plan': { status: 200, body: { slots: [0, 1, 2, 3].map((n) => ({ slot: n, ranges: 3, max_ranges: 62, payload: 30, bps: 900, divider: 2, rate_real: 50, vars: [{ spec: 'zz', ok: false }], errors: [] })), total_bps: 3600, budget_bps: 87552, ok: false, errors: [] } },
    'POST /api/streams/apply': { status: 202, body: { ok: true, started: true } },
    'POST /api/streams/restore': { status: 202, body: { ok: true, started: true } },
    'POST /api/streams/log/start': { status: 200, body: { active: true } },
    'POST /api/streams/log/stop': { status: 200, body: { active: false } },
    'POST /api/streams/forward': { status: 200, body: { active: true } },
    'GET /api/symbols': (u) => ({ status: 200, body: { names: /parent=/.test(u) ? ['P.child'] : ['gyro_x', 'gyro_y'] } }),
  }, extra || {});
}

const same = (a, b) => assert.strictEqual(JSON.stringify(a), JSON.stringify(b));
let passed = 0;
async function check(name, fn) {
  try { await fn(); passed += 1; console.log('  ok   ' + name); }
  catch (e) { console.log('  FAIL ' + name + ': ' + (e.stack || e)); process.exitCode = 1; }
}

(async function main() {
  await check('registers as Streams and loads snapshot + presets on render', async () => {
    const h = load(routes(snap()));
    h.render();
    await h.settle();
    assert.strictEqual(h.api.name, 'Streams');
    assert.ok(h.body('/api/streams').some((c) => c.url === '/api/streams'));
    assert.ok(h.body('/api/streams/presets').length);
    const tbl = h.doc.getElementById('st-table').innerHTML;
    assert.ok(/Dashboard layout/.test(tbl) && /fixed/.test(tbl));
    assert.ok(/custom/.test(tbl), 'slot 2 is a custom swap');
  });

  await check('slot 0 has no Edit; slots 1-3 do, enabled while disarmed', async () => {
    const h = load(routes(snap()));
    h.render(); await h.settle();
    const tbl = h.doc.getElementById('st-table').innerHTML;
    assert.strictEqual((tbl.match(/data-act="edit"/g) || []).length, 3);
    assert.ok(!/data-act="edit" data-slot="0"/.test(tbl));
    assert.ok(!/disabled/.test(tbl));
  });

  await check('armed disables swaps and explains why', async () => {
    const h = load(routes(snap({ arm_state: 'armed', can_swap: false })));
    h.render(); await h.settle();
    const tbl = h.doc.getElementById('st-table').innerHTML;
    assert.ok(/disabled/.test(tbl));
    assert.ok(/disarmed/.test(h.doc.getElementById('st-head').innerHTML));
    assert.strictEqual(h.T.canSwap(), false);
  });

  await check('active full preset gates swaps', async () => {
    const h = load(routes(snap({ active_preset: 'flight' })));
    h.render(); await h.settle();
    assert.strictEqual(h.T.canSwap(), false);
    assert.ok(/flight/.test(h.T.gateReason()));
  });

  await check('lost tab shows reason and one-click Restore', async () => {
    const h = load(routes(snap()));
    h.render(); await h.settle();
    const tabs = h.doc.getElementById('st-tabs').innerHTML;
    assert.ok(/not streamed: slot 3 holds Sysid/.test(tabs));
    assert.ok(/data-act="restore" data-slot="3"/.test(tabs));
    h.click({ 'data-act': 'restore', 'data-slot': '3' });
    await h.settle();
    const post = h.calls.filter((c) => c.method === 'POST' && c.url === '/api/streams/restore');
    assert.strictEqual(post.length, 1);
    same(post[0].body, { slots: [3] });
  });

  await check('edit opens an editor seeded from the slot; input builds the apply body', async () => {
    const h = load(routes(snap()));
    h.render(); await h.settle();
    h.click({ 'data-act': 'edit', 'data-slot': '1' });
    assert.ok(h.doc.getElementById('st-vars-1'));
    h.fire('input', { 'data-act': 'vars', 'data-slot': '1' }, { value: 'alpha, beta;gamma\ndelta' });
    h.fire('input', { 'data-act': 'rate', 'data-slot': '1' }, { value: '30' });
    same(h.T.draftAssignments().map((a) => [a.slot, a.vars, a.rate]),
      [[1, ['alpha', 'beta', 'gamma', 'delta'], 30]]);
    await h.flush();   // debounced plan
    const plans = h.calls.filter((c) => c.url === '/api/streams/plan');
    assert.ok(plans.length >= 1);
    const last = plans[plans.length - 1].body.slots;
    assert.strictEqual(last.length, 4);
    same(last[1], { rate: 30, vars: ['alpha', 'beta', 'gamma', 'delta'] });
    same(last[0].vars, ['a', 'b'], 'slot 0 passes through unchanged');
    assert.ok(/zz\?/.test(h.doc.getElementById('st-plan-1').innerHTML), 'unresolved var flagged');
    h.click({ 'data-act': 'apply', 'data-slot': '1' });
    await h.settle();
    const ap = h.calls.filter((c) => c.url === '/api/streams/apply');
    assert.strictEqual(ap.length, 1);
    same(ap[0].body.slots[0].vars, ['alpha', 'beta', 'gamma', 'delta']);
    assert.strictEqual(ap[0].body.slots[0].slot, 1);
  });

  await check('after apply the table drops the draft plan for the snapshot plan', async () => {
    const h = load(routes(snap()));
    h.render(); await h.settle();
    h.click({ 'data-act': 'edit', 'data-slot': '1' });
    h.fire('input', { 'data-act': 'vars', 'data-slot': '1' }, { value: 'alpha' });
    await h.flush();
    assert.strictEqual(h.T.planOf(1).payload, 30, 'draft plan while editing');
    h.click({ 'data-act': 'apply', 'data-slot': '1' });
    await h.settle();
    assert.strictEqual(h.T.planOf(1).payload, 100, 'snapshot plan after apply');
  });

  await check('suggestions stay with the slot that asked for them', async () => {
    const h = load(routes(snap()));
    h.render(); await h.settle();
    h.click({ 'data-act': 'edit', 'data-slot': '1' });
    h.click({ 'data-act': 'edit', 'data-slot': '2' });
    h.fire('input', { 'data-act': 'vars', 'data-slot': '1' }, { value: 'P.' });
    await h.flush();
    h.fire('input', { 'data-act': 'vars', 'data-slot': '2' }, { value: 'gy' });
    await h.flush();
    h.click({ 'data-act': 'ac', 'data-slot': '1', 'data-i': '0' });
    assert.strictEqual(h.T.draftAssignments().filter((a) => a.slot === 1)[0].vars.join(' '), 'P.child');
  });

  await check('network failure on log/forward/preset shows a message, no unhandled rejection', async () => {
    const down = { reject: 'net down' };
    const h = load(routes(snap(), { 'POST /api/streams/log/start': down, 'POST /api/streams/log/stop': down,
      'POST /api/streams/forward': down }));
    let unhandled = 0;
    const onU = () => { unhandled += 1; };
    process.on('unhandledRejection', onU);
    h.render(); await h.settle();
    h.click({}, { id: 'st-log-start' });
    await h.settle();
    assert.ok(/log start failed: .*net down/.test(h.doc.getElementById('st-msg').textContent));
    h.click({}, { id: 'st-log-stop' });
    await h.settle();
    assert.ok(/log stop failed/.test(h.doc.getElementById('st-msg').textContent));
    h.click({}, { id: 'st-fwd-btn' });
    await h.settle();
    assert.ok(/forward failed/.test(h.doc.getElementById('st-msg').textContent));
    await new Promise((r) => setImmediate(r));
    process.off('unhandledRejection', onU);
    assert.strictEqual(unhandled, 0);
  });

  await check('apply refusal (409) surfaces the service message', async () => {
    const h = load(routes(snap(), { 'POST /api/streams/apply': { status: 409, body: { ok: false, error: 'refused: arm state is armed' } } }));
    h.render(); await h.settle();
    h.click({ 'data-act': 'edit', 'data-slot': '1' });
    h.click({ 'data-act': 'apply', 'data-slot': '1' });
    await h.settle();
    assert.ok(/refused: arm state is armed/.test(h.doc.getElementById('st-msg').textContent));
  });

  await check('preset pick fills variables/rate and marks the source', async () => {
    const h = load(routes(snap()));
    h.render(); await h.settle();
    h.click({ 'data-act': 'edit', 'data-slot': '3' });
    h.fire('change', { 'data-act': 'preset', 'data-slot': '3' }, { value: 'sysid' });
    await h.settle();
    const a = h.T.draftAssignments()[0];
    same(a.vars, ['s1', 's2']);
    assert.strictEqual(a.rate, 50);
    assert.strictEqual(a.source, 'preset:sysid');
    h.fire('change', { 'data-act': 'part', 'data-slot': '3' }, { value: '1' });
    same(h.T.draftAssignments()[0].vars, ['s3']);
  });

  await check('autocomplete hits /api/symbols with the last token and completes it', async () => {
    const h = load(routes(snap()));
    h.render(); await h.settle();
    h.click({ 'data-act': 'edit', 'data-slot': '1' });
    h.fire('input', { 'data-act': 'vars', 'data-slot': '1' }, { value: 'x.y gyr' });
    await h.flush();
    const sym = h.calls.filter((c) => c.url.indexOf('/api/symbols') === 0);
    assert.ok(sym.some((c) => /prefix=gyr/.test(c.url)), sym.map((c) => c.url).join());
    assert.ok(/gyro_x/.test(h.doc.getElementById('st-ac-1').innerHTML));
    h.click({ 'data-act': 'ac', 'data-slot': '1', 'data-i': '0' });
    same(h.T.draftAssignments()[0].vars, ['x.y', 'gyro_x']);
    assert.strictEqual(h.doc.focused, 'st-vars-1');
  });

  await check('a trailing dot drills into members', async () => {
    const h = load(routes(snap()));
    h.render(); await h.settle();
    h.click({ 'data-act': 'edit', 'data-slot': '1' });
    h.fire('input', { 'data-act': 'vars', 'data-slot': '1' }, { value: 'P.' });
    await h.flush();
    assert.ok(h.calls.some((c) => /parent=P$/.test(c.url)));
  });

  await check('logging start bodies per mode', async () => {
    const h = load(routes(snap()));
    h.render(); await h.settle();
    const set = (id, v) => { h.doc.getElementById(id).value = v; };
    set('st-log-mode', 'rolling'); set('st-log-secs', '20'); set('st-log-name', 'run1');
    h.click({}, { id: 'st-log-start' }); await h.settle();
    set('st-log-mode', 'timed'); set('st-log-secs', '5');
    h.click({}, { id: 'st-log-start' }); await h.settle();
    set('st-log-mode', 'unlimited');
    h.click({}, { id: 'st-log-start' }); await h.settle();
    const posts = h.calls.filter((c) => c.url === '/api/streams/log/start').map((c) => c.body);
    same(posts[0], { name: 'run1', mode: 'rolling', window_s: 20 });
    assert.strictEqual(posts[1].mode, 'timed'); assert.strictEqual(posts[1].seconds, 5);
    assert.strictEqual(posts[2].mode, 'unlimited');
    assert.ok(!('seconds' in posts[2]) && !('window_s' in posts[2]));
    h.click({}, { id: 'st-log-stop' }); await h.settle();
    assert.ok(h.calls.some((c) => c.url === '/api/streams/log/stop'));
  });

  await check('forward toggle posts channels/addr, then disable', async () => {
    const h = load(routes(snap()));
    h.render(); await h.settle();
    assert.ok(/x\.y/.test(h.doc.getElementById('st-fwd-ch').value), 'channels seeded from slots 1-3');
    h.click({}, { id: 'st-fwd-btn' }); await h.settle();
    const on = h.calls.find((c) => c.url === '/api/streams/forward');
    assert.strictEqual(on.body.enable, true);
    assert.strictEqual(on.body.addr, '127.0.0.1:1347');
    assert.ok(on.body.channels.indexOf('x.y') >= 0);
  });

  await check('status lines reflect active log and forward', async () => {
    const h = load(routes(snap({
      log: { active: true, name: 'r', mode: 'rolling', elapsed_s: 12, rows: 340, files: [] },
      forward: { active: true, addr: '127.0.0.1:1347', channels: ['a', 'b'], sent: 9 },
    })));
    h.render(); await h.settle();
    assert.ok(/logging r \(rolling\)/.test(h.doc.getElementById('st-log-status').textContent));
    assert.ok(/forwarding 2 channels/.test(h.doc.getElementById('st-fwd-status').textContent));
    assert.strictEqual(h.doc.getElementById('st-log-start').disabled, true);
    assert.strictEqual(h.doc.getElementById('st-fwd-btn').textContent, 'Stop forward');
  });

  await check('polls faster while a swap is busy', async () => {
    const h = load(routes(snap({ apply: { busy: true, error: null } })));
    h.render(); await h.settle();
    assert.ok(h.timers.some((t) => t.ms === 800));
    assert.strictEqual(h.T.canSwap(), false);
  });

  await check('helpers: parseVars, lastToken, fmtBps, budget bar colour', async () => {
    const h = load(routes(snap()));
    same(Array.from(h.T.parseVars(' a,b ;c\n d ')), ['a', 'b', 'c', 'd']);
    assert.strictEqual(h.T.lastToken('a b gy'), 'gy');
    assert.strictEqual(h.T.lastToken('a b '), '');
    assert.strictEqual(h.T.fmtBps(1500), '1.5 kB/s');
    assert.strictEqual(h.T.fmtBps(900), '900 B/s');
    assert.ok(/st-bar-hot/.test(h.T.bar(0.95)) && /st-bar-ok/.test(h.T.bar(0.3)));
  });

  await check('destroy removes listeners and stops timers', async () => {
    const h = load(routes(snap()));
    h.render(); await h.settle();
    h.sandbox.pdestroy();
    assert.strictEqual((h.container.handlers.click || []).length, 0);
    const before = h.calls.length;
    await h.flush();
    assert.strictEqual(h.calls.length, before, 'no polling after destroy');
  });

  console.log(passed + ' passed' + (process.exitCode ? ', with FAILURES' : ''));
})();
