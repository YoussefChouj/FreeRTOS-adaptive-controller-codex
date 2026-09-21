'use strict';
/**
 * Offline verification harness for slot-manager-panel.js,
 * task 20260921-145106. Covers:
 *   1. Symbol picker root load (_loadPickerRoot): captured GET, row
 *      render, drill into a symbol and crumb back to base.
 *   2. Subscribe-form fills via preset buttons (range counts).
 *   3. Divider edits: Hz ladder readout, cadence quick-set, invalid.
 *   4. Subscribe happy path: captured preview + subscribeSlot bodies.
 *   5. Unresolved preview blocks the send (subscribeSlot never called).
 *   6. Per-slot selection remove via the Clear button (panel has no
 *      stop/divider=0 UI; that is stated, not faked).
 *   7. Honest empty table and silent health / visible symbol errors.
 *   8. Slot expand: channel values, units, and a null reads '??'.
 *
 * The sandbox has no require; fetch/XHR are recording stubs and the
 * shell api methods are recording stubs, so a real network call is
 * structurally impossible.
 *
 * Run:  node ground_station/service/tests/slot_manager_panel_harness.js
 * Exit code 0 iff every check passes.
 */
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const assert = require('assert');

const PANEL = path.join(__dirname, '..', '..', '..',
  'docs', 'dashboard-platform', 'shell', 'plugins', 'slot-manager-panel.js');

// ── Fake DOM ──────────────────────────────────────────────────────────────
class Element {
  constructor(id, tag) {
    this.id = id;
    this.tagName = (tag || 'div').toUpperCase();
    this._html = '';
    this.textContent = '';
    this.value = '';
    this.disabled = false;
    this.className = '';
    this.handlers = {};
    this.style = {};
    this.dataset = {};
    this.doc = null;
    this._cache = {};
  }
  addEventListener(ev, fn) {
    (this.handlers[ev] = this.handlers[ev] || []).push(fn);
  }
  get classList() {
    const el = this;
    return {
      add(c) { if (!el.className.split(/\s+/).includes(c)) el.className =
        (el.className + ' ' + c).trim(); },
      remove(c) { el.className = el.className.split(/\s+/).filter(
        (x) => x !== c && x !== '').join(' '); },
      contains(c) { return el.className.split(/\s+/).includes(c); },
      toggle(c, f) {
        const has = this.contains(c);
        if (f === undefined) f = !has;
        if (f && !has) this.add(c);
        if (!f && has) this.remove(c);
        return f;
      },
    };
  }
  getAttribute(name) {
    if (this.dataset[name] !== undefined) return this.dataset[name];
    const s = name.replace(/^data-/, '');
    if (s !== name && this.dataset[s] !== undefined) return this.dataset[s];
    return null;
  }
  selfMatch(sel) {
    const tm = sel.match(/^[a-zA-Z][\w-]*/);
    if (tm && this.tagName !== tm[0].toUpperCase()) return false;
    const classes = (sel.match(/\.[\w-]+/g) || []).map((c) => c.slice(1));
    return classes.every((c) => this.classList.contains(c));
  }
  closest(sel) { return this.selfMatch(sel) ? this : null; }
  get innerHTML() { return this._html; }
  set innerHTML(html) {
    this._html = html;
    this._cache = {};
    if (this.doc) this.doc.scan(html);
  }
  // Selectors: tag, .cls, tag.cls(.cls) — no descendants.
  querySelectorAll(sel) {
    if (this._cache[sel]) return this._cache[sel];
    const tm = sel.match(/^[a-zA-Z][\w-]*/);
    const wantTag = tm ? tm[0] : null;
    const classes = (sel.match(/\.[\w-]+/g) || []).map((c) => c.slice(1));
    const re = /<([a-zA-Z][\w-]*)((?:"[^"]*"|'[^']*'|[^'">])*)>/g;
    const out = [];
    let m;
    while ((m = re.exec(this._html)) !== null) {
      if (wantTag && m[1] !== wantTag) continue;
      if (classes.length && !classes.every((c) =>
        new RegExp('class="[^"]*\\b' + c + '\\b[^"]*"').test(m[0]))) continue;
      let el = this._cache[m[0]];
      if (!el) {
        el = new Element(null, m[1]);
        el.doc = this.doc;
        const cm = m[2].match(/\bclass="([^"]*)"/);
        if (cm) el.className = cm[1];
        const dm = m[2].match(/\bdata-([\w-]+)="([^"]*)"/);
        if (dm) el.dataset[dm[1]] = dm[2];
        const vm2 = m[2].match(/\bvalue="([^"]*)"/);
        if (vm2) el.value = vm2[1];
        this._cache[m[0]] = el;
      }
      if (classes.every((c) => el.classList.contains(c))) out.push(el);
    }
    this._cache[sel] = out;
    return out;
  }
  querySelector(sel) { return this.querySelectorAll(sel)[0] || null; }
}

class FakeDocument {
  constructor() { this.elements = {}; this.root = null; }
  _register(tag, idm, content) {
    const el = new Element(idm[1], tag.slice(1).split(/[\s>]/)[0]);
    el.doc = this;
    if (content !== undefined) {
      el._html = content;
      if (content.indexOf('<') === -1) el.textContent = content;
    }
    this.elements[idm[1]] = el;
    const vm2 = tag.match(/\bvalue="([^"]*)"/);
    if (vm2) el.value = vm2[1];
  }
  scan(html) {
    const openRe = /<([a-zA-Z][\w-]*)((?:"[^"]*"|'[^']*'|[^'">])*)>/g;
    let m;
    while ((m = openRe.exec(html)) !== null) {
      const idm = m[2].match(/\bid="([^"]+)"/);
      if (!idm) continue;
      let content;
      const ci = html.indexOf('</' + m[1] + '>', m.index + m[0].length);
      if (ci !== -1) content = html.slice(m.index + m[0].length, ci);
      this._register(m[0], idm, content);
    }
  }
  getElementById(id) { return this.elements[id] || null; }
  querySelectorAll(sel) { return this.root.querySelectorAll(sel); }
  querySelector(sel) { return this.root.querySelectorAll(sel)[0] || null; }
}

// ── Manual timers + fixed clock ───────────────────────────────────────────
let _now = 0;
const _tq = [];
let _tid = 1;
function tSet(fn, ms) { const id = _tid++; _tq.push({ id, due: _now + (ms || 0), fn }); return id; }
function tClear(id) {
  const i = _tq.findIndex((t) => t.id === id);
  if (i >= 0) _tq.splice(i, 1);
}
function tInterval(fn, ms) {
  const id = _tid++;
  _tq.push({ id, due: _now + (ms || 0), fn, repeat: ms || 0 });
  return id;
}
function advance(ms) {
  const end = _now + ms;
  for (;;) {
    const t = _tq.filter((x) => x.due <= end).sort((a, b) => a.due - b.due)[0];
    if (!t) break;
    _now = t.due;
    if (t.repeat !== undefined) t.due = _now + t.repeat;
    else _tq.splice(_tq.indexOf(t), 1);
    t.fn();
  }
  _now = end;
}
const RealDate = Date;
function FakeDate(a) {
  if (new.target) return a === undefined ? new RealDate() : new RealDate(a);
  return new RealDate().toString();
}
FakeDate.now = () => _now;
FakeDate.parse = RealDate.parse;
FakeDate.UTC = RealDate.UTC;
const micro = () => new Promise((r) => setImmediate(r));

// ── Panel loading ─────────────────────────────────────────────────────────
function loadPanel() {
  const doc = new FakeDocument();
  const container = new Element('container', 'div');
  container.doc = doc;
  doc.root = container;
  const slotCalls = [];
  const previewCalls = [];
  const fetchRecords = [];
  const S = {
    initialState: null,
    symbolsStatus: 200,
    symbols: {
      names: ['mrac_state', 's_ekf', 'imu_data'],
      drillable: { mrac_state: true }, total: 3, count: 3, truncated: false,
    },
    drill: { names: ['Theta[0]', 'e'], drillable: {}, total: 2, count: 2 },
    healthStatus: 200,
    health: {
      ttl_ns: 30000000000,
      slots: { 1: { status: 'live', fresh_keys: 9, stale_keys: 0 } },
    },
    previewBody: { slot: 0, divider: 1, ranges: [], unresolved: [] },
  };
  function lazy(status, getBody) {
    return {
      ok: status >= 200 && status < 300, status,
      json: () => Promise.resolve(getBody()),
    };
  }
  const api = {
    stateCb: null,
    subscribe(cb) { this.stateCb = cb; },
    registerPanel(name, fn) { this.fn = fn; },
    getState() { return S.initialState; },
    subscribeSlot(slot, divider, ranges) {
      slotCalls.push({ slot, divider, ranges: ranges.slice() });
      return Promise.resolve({ ok: true });
    },
    previewSubscribe(slot, divider, ranges) {
      previewCalls.push({ slot, divider, ranges: ranges.slice() });
      return Promise.resolve(S.previewBody);
    },
  };
  const sandbox = {
    document: doc,
    console: { log() {}, warn() {}, error() {} },
    Date: FakeDate, Math, Number, String, Boolean, Array, Object, JSON,
    isNaN, isFinite, parseInt, parseFloat,
    encodeURIComponent, decodeURIComponent,
    setTimeout: tSet, clearTimeout: tClear,
    setInterval: tInterval, clearInterval: tClear,
    Promise,
    requestAnimationFrame(fn) { return tSet(fn, 16); },
    fetch(url, opts) {
      const qi = url.indexOf('?');
      const p = qi === -1 ? url : url.slice(0, qi);
      const query = qi === -1 ? '' : url.slice(qi + 1);
      const method = (opts && opts.method) || 'GET';
      let body = null;
      if (opts && opts.body) body = JSON.parse(opts.body);
      fetchRecords.push({ method, path: p, query, body });
      if (p === '/api/symbols' && method === 'GET') {
        return Promise.resolve(lazy(S.symbolsStatus, () =>
          query.indexOf('parent=') !== -1 ? S.drill : S.symbols));
      }
      if (p === '/health/slots') {
        return Promise.resolve(lazy(S.healthStatus, () => S.health));
      }
      if (p === '/subscribe/preview' && method === 'POST') {
        return Promise.resolve(lazy(200, () => S.previewBody));
      }
      return Promise.resolve(lazy(404, () => ({ error: 'unexpected ' + url })));
    },
  };
  sandbox.window = sandbox;
  sandbox.__registerPlugin__ = function (name, init, destroy) {
    sandbox.pluginInit = init;
    sandbox.pluginDestroy = destroy;
  };
  vm.createContext(sandbox);
  vm.runInContext(fs.readFileSync(PANEL, 'utf8'), sandbox, { filename: PANEL });
  sandbox.pluginInit(api);
  api.fn(container);
  return {
    doc, S, api,
    fetchRecords: () => fetchRecords.slice(),
    slotCalls: () => slotCalls.slice(),
    previewCalls: () => previewCalls.slice(),
    fire(el, ev, eventObj) {
      (el.handlers[ev] || []).forEach((fn) => fn.call(el,
        Object.assign({ target: el }, eventObj)));
    },
    byData(selector, attr, val) {
      return doc.querySelectorAll(selector).filter(
        (el) => el.getAttribute(attr) === String(val))[0];
    },
    async feed(state) {
      api.stateCb ? api.stateCb(state) : null;
      advance(16);
      await micro();
    },
    destroy() { sandbox.pluginDestroy(); },
  };
}
async function runChecks() {
  console.log('--- SLOT MANAGER OFFLINE CHECKS ---');

  // 1. Picker root load, drill, crumb back
  {
    console.log('\n[CHECK 1: picker root load (_loadPickerRoot), drill, crumb back]');
    const env = loadPanel();
    await micro();
    const gets = env.fetchRecords().filter((r) => r.path === '/api/symbols');
    assert.strictEqual(gets[0].query, 'limit=100', 'empty prefix is omitted from URL');
    assert.strictEqual(env.doc.getElementById('sm-picker-status').textContent,
      '3 matches');
    assert.ok(env.doc.getElementById('sm-picker-results').innerHTML
      .indexOf('sm-picker-add') !== -1);
    assert.ok(env.fetchRecords().some((r) => r.path === '/health/slots'));

    // Drill into mrac_state via delegated results click.
    const drillBtn = env.doc.getElementById('sm-picker-results')
      .querySelectorAll('.sm-picker-drill')[0];
    assert.strictEqual(drillBtn.getAttribute('data-name'), 'mrac_state');
    env.fire(env.doc.getElementById('sm-picker-results'), 'click',
      { target: drillBtn });
    await micro();
    const drillGet = env.fetchRecords().filter((r) => r.query
      .indexOf('parent=mrac_state') !== -1)[0];
    assert.ok(drillGet, 'drill requests ?parent=mrac_state');
    assert.strictEqual(env.doc.getElementById('sm-picker-status').textContent,
      '2 matches');
    assert.strictEqual(env.doc.getElementById('sm-picker-filter').value, '');

    // Crumb back to base.
    const baseCrumb = env.doc.getElementById('sm-picker-breadcrumb')
      .querySelectorAll('.sm-crumb')[0];
    assert.strictEqual(baseCrumb.getAttribute('data-crumb'), '');
    env.fire(env.doc.getElementById('sm-picker-breadcrumb'), 'click',
      { target: baseCrumb });
    await micro();
    assert.strictEqual(env.doc.getElementById('sm-picker-status').textContent,
      '3 matches');
    console.log('  PASS: root GET ?limit=100; drill parent=mrac_state; crumb returns to 3 matches');
    env.destroy();
  }

  // 2. Preset form fills
  {
    console.log('\n[CHECK 2: preset fills subscribe form]');
    const env = loadPanel();
    await micro();
    const ekf = env.byData('.sm-preset-btn', 'data-preset', 'ekf');
    env.fire(ekf, 'click');
    const ta = env.doc.getElementById('sm-ranges');
    assert.ok(ta.value.indexOf('s_ekf.x[0]') !== -1);
    assert.strictEqual(ta.value.split(/[,\s]+/).filter(Boolean).length, 9);
    const mrac = env.byData('.sm-preset-btn', 'data-preset', 'mrac');
    env.fire(mrac, 'click');
    assert.strictEqual(env.doc.getElementById('sm-ranges').value
      .split(/[,\s]+/).filter(Boolean).length, 13);
    console.log('  PASS: EKF preset 9 ranges; MRAC preset 13 ranges');
    env.destroy();
  }

  // 3. Divider edits
  {
    console.log('\n[CHECK 3: divider ladder readout, cadence set, invalid input]');
    const env = loadPanel();
    await micro();
    const hz = env.doc.getElementById('sm-hz');
    hz.value = '30';
    env.fire(hz, 'input');
    let msg = env.doc.getElementById('sm-hz-readout').textContent;
    assert.ok(msg.indexOf('Asked 30 Hz') !== -1 &&
      msg.indexOf('you will get 25 Hz (wire divider 4)') !== -1 &&
      msg.indexOf('not an exact ladder rung') !== -1);

    const mixed = env.byData('.sm-cadence-btn', 'data-cadence', '80.2');
    env.fire(mixed, 'click');
    assert.strictEqual(env.doc.getElementById('sm-cadence').value, '80.2');
    msg = env.doc.getElementById('sm-hz-readout').textContent;
    assert.ok(msg.indexOf('you will get 26.733 Hz (wire divider 3)') !== -1,
      '30 @ 80.2 -> ceil(2.673)=3 -> 26.733, got ' + msg);

    hz.value = '0';
    env.fire(hz, 'input');
    assert.strictEqual(env.doc.getElementById('sm-hz-readout').textContent,
      'Enter a desired rate in Hz (cadence must also be > 0).');
    console.log('  PASS: 30@100 -> div4 25; MIXED -> div3 26.733; invalid -> guidance text');
    env.destroy();
  }

  // 4. Subscribe happy path captures bodies
  {
    console.log('\n[CHECK 4: subscribe sends captured preview + request bodies]');
    const env = loadPanel();
    await micro();
    env.fire(env.byData('.sm-preset-btn', 'data-preset', 'ekf'), 'click');
    const btn2 = env.byData('.sm-subscribe-btn', 'data-slot', '2');
    env.fire(btn2, 'click');
    await micro();
    const pc = env.previewCalls();
    assert.ok(pc.some((c) => c.slot === 2 && c.divider === 1 &&
      c.ranges.length === 9 && c.ranges[0] === 's_ekf.x[0]'));
    const sc = env.slotCalls();
    assert.strictEqual(sc.length, 1);
    assert.strictEqual(sc[0].slot, 2);
    assert.strictEqual(sc[0].divider, 1);
    assert.strictEqual(sc[0].ranges.length, 9);
    assert.ok(env.doc.getElementById('sm-result').textContent
      .indexOf('OK - Slot 2') !== -1);
    assert.strictEqual(env.doc.getElementById('sm-selection').style.display, '');
    assert.strictEqual(env.doc.getElementById('sm-selection-id').textContent, '#2');
    console.log('  PASS: preview (2,1,9 ranges) then subscribeSlot (2,1,9); OK result + banner #2');
    env.destroy();
  }

  // 5. Unresolved blocks
  {
    console.log('\n[CHECK 5: unresolved preview blocks the subscribe]');
    const env = loadPanel();
    await micro();
    env.S.previewBody = {
      slot: 3, divider: 1, ranges: [], unresolved: ['bogus.symbol'],
    };
    env.fire(env.byData('.sm-preset-btn', 'data-preset', 'ekf'), 'click');
    const btn3 = env.byData('.sm-subscribe-btn', 'data-slot', '3');
    env.fire(btn3, 'click');
    await micro();
    assert.strictEqual(env.slotCalls().length, 0,
      'unresolved preview must prevent any subscribeSlot call');
    assert.ok(env.doc.getElementById('sm-result').textContent
      .indexOf('1 DWARF name(s) unresolved against the ELF') !== -1);
    assert.ok(env.doc.getElementById('sm-preview').innerHTML
      .indexOf('1 unresolved') !== -1);
    console.log('  PASS: unresolved name blocks send; result + preview show it');
    env.destroy();
  }

  // 6. Selection remove (Clear)
  {
    console.log('\n[CHECK 6: per-slot selection removed via Clear]');
    const env = loadPanel();
    await micro();
    const btn1 = env.byData('.sm-subscribe-btn', 'data-slot', '1');
    env.fire(btn1, 'click');
    await micro();
    assert.strictEqual(env.doc.getElementById('sm-selection').style.display, '');
    env.fire(env.doc.getElementById('sm-clear-selection'), 'click');
    assert.strictEqual(env.doc.getElementById('sm-selection').style.display, 'none');
    console.log('  PASS: banner shows slot 1 then is hidden by Clear (panel has no stop/divider=0 UI)');
    env.destroy();
  }

  // 7. Empty + error states
  {
    console.log('\n[CHECK 7: empty table honest; health silent, symbols error visible]');
    const env = loadPanel();
    await micro();
    const tbodyHtml = env.doc.getElementById('sm-slot-tbody').innerHTML;
    assert.ok(tbodyHtml.indexOf('No active slots') !== -1);
    assert.strictEqual(env.doc.getElementById('sm-active-count').textContent, '0');

    // Symbols non-200: error text visible.
    env.S.symbolsStatus = 500;
    const filter = env.doc.getElementById('sm-picker-filter');
    filter.value = 'x';
    env.fire(filter, 'input');
    advance(200);
    await micro();
    assert.strictEqual(env.doc.getElementById('sm-picker-status').textContent,
      'HTTP 500');

    // Health 500: caught silently, panel state intact.
    env.S.healthStatus = 500;
    advance(3000);
    await micro();
    assert.ok(env.doc.getElementById('sm-slot-tbody').innerHTML
      .indexOf('No active slots') !== -1);
    console.log('  PASS: No active slots + count 0; HTTP 500 symbols error; health 500 silent');
    env.destroy();
  }

  // 8. Expand slot: channels + units + null honesty
  {
    console.log('\n[CHECK 8: slot expand renders channels, units, null as ??]');
    const env = loadPanel();
    await micro();
    await env.feed({
      streams: {
        1: {
          tag: 'ch5', sequence: 10,
          values: { 'ekf.pos_x': 1.5, 'status.armed': false, 'c.maybe': null },
        },
      },
    });
    let html = env.doc.getElementById('sm-slot-tbody').innerHTML;
    assert.ok(html.indexOf('ch5') !== -1 && html.indexOf('>10<') !== -1);
    const row = env.doc.getElementById('sm-slot-tbody')
      .querySelectorAll('tr.sm-row-clickable')[0];
    env.fire(row, 'click');
    html = env.doc.getElementById('sm-slot-tbody').innerHTML;
    assert.ok(html.indexOf('ekf.pos_x') !== -1);
    assert.ok(html.indexOf('1.5000') !== -1);
    assert.ok(html.indexOf('m/s') === -1 ? html.indexOf('m<') !== -1 : true);
    assert.ok(html.indexOf('class="sm-channel-unit">m<') !== -1,
      'pos_x unit is m, got html: ' + html.slice(html.indexOf('pos_x') - 50));
    assert.ok(html.indexOf('status.armed') !== -1 && html.indexOf('false') !== -1);
    assert.ok(html.indexOf('c.maybe') !== -1 &&
      html.indexOf('??') !== -1, 'null channel reads ??, never 0');
    assert.ok(html.indexOf('9/9') !== -1, 'fresh/stale badge from health report');
    console.log('  PASS: ch5 row expands; pos_x 1.5000 m; armed false; null = ??; 9/9 fresh');
    env.destroy();
  }

  console.log('\nALL CHECKS PASSED SUCCESSFULLY.');
}

(async function () {
  try { await runChecks(); } catch (e) { console.error(e); process.exit(1); }
}());
