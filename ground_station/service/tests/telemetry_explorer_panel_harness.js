'use strict';
/**
 * Offline verification harness for telemetry-explorer-panel.js,
 * task 20260921-145106. Covers:
 *   1. Honest "Waiting for telemetry..." initial state; meta reads dash,
 *      never a fabricated count.
 *   2. Rows rendered from stream values, including a legitimate 0 which
 *      must display "0" (zero is not missing).
 *   3. Filter narrows rows (debounced) and the no-match state is honest.
 *   4. Key accumulation across polls (shown/total count).
 *   5. Null values render "null"; missing schema reads dash; null state
 *      does not crash.
 *   6. Namespace-prefix shortening keeps the full key in the title.
 *
 * NOTE: the shipped panel has no expandable rows and no NOT PUBLISHED /
 * staleness rendering; the title attribute is its only "see more"
 * affordance, and its empty states are "Waiting for telemetry..." /
 * "No keys match". Those are tested instead of features that do not
 * exist.
 *
 * The sandbox has no require and fetch/XHR are recording stubs, so a
 * real network call is structurally impossible.
 *
 * Run:  node ground_station/service/tests/telemetry_explorer_panel_harness.js
 * Exit code 0 iff every check passes.
 */
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const assert = require('assert');

const PANEL = path.join(__dirname, '..', '..', '..',
  'docs', 'dashboard-platform', 'shell', 'plugins',
  'telemetry-explorer-panel.js');

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
  get innerHTML() { return this._html; }
  set innerHTML(html) {
    this._html = html;
    this._cache = {};
    if (this.doc) this.doc.scan(html);
  }
}

class FakeDocument {
  constructor() { this.elements = {}; }
  _register(tag, idm, content) {
    const id = idm[1];
    const el = new Element(id, tag.slice(1).split(/[\s>]/)[0]);
    el.doc = this;
    if (content !== undefined) {
      el._html = content;
      if (content.indexOf('<') === -1) el.textContent = content;
    }
    this.elements[id] = el;
    const vm2 = tag.match(/\bvalue="([^"]*)"/);
    if (vm2) el.value = vm2[1];
  }
  scan(html) {
    // Find every opening tag (quoted attrs may contain '>' safely).
    // Outer pairs cannot swallow inner ids; content is captured up to
    // the first matching close (nested same-tag markup truncates).
    const openRe = /<([a-zA-Z][\w-]*)((?:"[^"]*"|'[^']*'|[^'">])*)>/g;
    let m;
    while ((m = openRe.exec(html)) !== null) {
      const idm = m[2].match(/\bid="([^"]+)"/);
      if (!idm) continue;
      let content;
      const close = '</' + m[1] + '>';
      const start = m.index + m[0].length;
      const ci = html.indexOf(close, start);
      if (ci !== -1) content = html.slice(start, ci);
      this._register(m[0], idm, content);
    }
  }
  getElementById(id) { return this.elements[id] || null; }
}

// ── Manual timers + fixed clock ───────────────────────────────────────────
let _now = 0;
const _q = [];
let _tid = 1;
function tSet(fn, ms) { const id = _tid++; _q.push({ id, due: _now + (ms || 0), fn }); return id; }
function tClear(id) {
  const i = _q.findIndex((t) => t.id === id);
  if (i >= 0) _q.splice(i, 1);
}
function tInterval(fn, ms) {
  const id = _tid++;
  _q.push({ id, due: _now + (ms || 0), fn, repeat: ms || 0 });
  return id;
}
function advance(ms) {
  const end = _now + ms;
  for (;;) {
    const due = _q.filter((t) => t.due <= end).sort((a, b) => a.due - b.due)[0];
    if (!due) break;
    _now = due.due;
    if (due.repeat !== undefined) due.due = _now + due.repeat;
    else _q.splice(_q.indexOf(due), 1);
    due.fn();
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
  const api = {
    stateCb: null,
    subscribe(cb) { this.stateCb = cb; },
    registerPanel(name, renderFn) { this.renderFn = renderFn; },
  };
  const fetchRecords = [];
  const sandbox = {
    document: doc,
    console: { log() {}, warn() {}, error() {} },
    Date: FakeDate, Math, Number, String, Boolean, Array, Object, JSON,
    isNaN, isFinite, parseInt, parseFloat,
    setTimeout: tSet, clearTimeout: tClear,
    setInterval: tInterval, clearInterval: tClear,
    Promise,
    fetch(url, opts) {
      fetchRecords.push({ url, opts });
      return Promise.resolve({
        ok: true, status: 200,
        json: () => Promise.resolve({}),
      });
    },
  };
  sandbox.window = sandbox;
  sandbox.__registerPlugin__ = function (name, init, destroy) {
    sandbox.pluginName = name;
    sandbox.pluginInit = init;
    sandbox.pluginDestroy = destroy;
  };
  vm.createContext(sandbox);
  vm.runInContext(fs.readFileSync(PANEL, 'utf8'), sandbox, { filename: PANEL });
  sandbox.pluginInit(api);
  api.renderFn(container);
  return {
    doc, sandbox,
    fetchRecords: () => fetchRecords.slice(),
    feed(state) { api.stateCb(state); },
    fire(el, ev) {
      (el.handlers[ev] || []).forEach((fn) => fn.call(el, { target: el }));
    },
    destroy() { sandbox.pluginDestroy(); },
  };
}

function state(streams) { return { schema_id: 'sch-1', streams }; }

function runChecks() {
  console.log('--- TELEMETRY EXPLORER OFFLINE CHECKS ---');

  // 1. Initial honest waiting state
  {
    console.log('\n[CHECK 1: initial "Waiting for telemetry", meta honest]');
    const env = loadPanel();
    assert.strictEqual(env.doc.getElementById('te-tbody').innerHTML.indexOf(
      'Waiting for telemetry…') !== -1, true);
    assert.strictEqual(env.doc.getElementById('te-schema').textContent, '—');
    assert.strictEqual(env.doc.getElementById('te-slots').textContent, '—');
    assert.strictEqual(env.doc.getElementById('te-count').textContent, '—');
    console.log('  PASS: waiting row shown; schema/slots/count all dash, no fake data');
    env.destroy();
  }

  // 2. Rows render; zero is not missing
  {
    console.log('\n[CHECK 2: rows render; legitimate zero displays 0]');
    const env = loadPanel();
    env.feed(state({ 3: { values: { 'c.altitude': 0, 'c.temperature': 21.5 } } }));
    const html = env.doc.getElementById('te-tbody').innerHTML;
    assert.ok(html.indexOf('"0"') !== -1 || html.indexOf('>0<') !== -1,
      'zero value must render 0, got ' + html);
    assert.ok(html.indexOf('21.5000') !== -1);
    assert.strictEqual(env.doc.getElementById('te-schema').textContent, 'sch-1');
    assert.strictEqual(env.doc.getElementById('te-slots').textContent, '3');
    assert.strictEqual(env.doc.getElementById('te-count').innerHTML,
      '<strong>2</strong> / 2');
    console.log('  PASS: rows show 0 and 21.5000; meta + count populated');
    env.destroy();
  }

  // 3. Filter narrows; no match is honest
  {
    console.log('\n[CHECK 3: filter narrows rows; no-match state]');
    const env = loadPanel();
    env.feed(state({ 1: { values: { 'ekf.pos_x': 1, 'ekf.pos_y': 2,
      'c.altitude': 3 } } }));
    const filter = env.doc.getElementById('te-filter');
    filter.value = 'pos';
    env.fire(filter, 'input');
    advance(150);
    let html = env.doc.getElementById('te-tbody').innerHTML;
    assert.ok(html.indexOf('pos_x') !== -1 && html.indexOf('pos_y') !== -1);
    assert.strictEqual(html.indexOf('altitude'), -1);
    assert.strictEqual(env.doc.getElementById('te-count').innerHTML,
      '<strong>2</strong> / 3');
    filter.value = 'zzz';
    env.fire(filter, 'input');
    advance(150);
    html = env.doc.getElementById('te-tbody').innerHTML;
    assert.ok(html.indexOf('No keys match "zzz"') !== -1);
    assert.strictEqual(env.doc.getElementById('te-count').innerHTML, '0');
    console.log('  PASS: filter shows 2 pos keys then honest no-match, count 0');
    env.destroy();
  }

  // 4. Accumulated key set across polls
  {
    console.log('\n[CHECK 4: key set accumulates across polls]');
    const env = loadPanel();
    env.feed(state({ 1: { values: { a: 1 } } }));
    env.feed(state({ 1: { values: { b: 2 } } }));
    assert.strictEqual(env.doc.getElementById('te-count').innerHTML,
      '<strong>1</strong> / 2', 'shown 1, total accumulated 2');
    console.log('  PASS: count reads 1 shown / 2 accumulated');
    env.destroy();
  }

  // 5. Null is "null", absent schema dash, null state safe
  {
    console.log('\n[CHECK 5: null value, missing schema, null state]');
    const env = loadPanel();
    env.feed(state({ 1: { values: { 'c.maybe': null } } }));
    assert.ok(env.doc.getElementById('te-tbody').innerHTML.indexOf('null') !== -1,
      'null must read null, never 0');
    env.feed({ streams: { 1: { values: { x: 1 } } } });
    assert.strictEqual(env.doc.getElementById('te-schema').textContent, '—');
    env.feed(null);
    assert.ok(env.doc.getElementById('te-tbody').innerHTML.indexOf('x') !== -1,
      'null state must not overwrite the table');
    console.log('  PASS: null rendered "null"; missing schema dash; null state ignored');
    env.destroy();
  }

  // 6. Prefix shortening keeps full key in title
  {
    console.log('\n[CHECK 6: short display key, full key in title]');
    const env = loadPanel();
    env.feed(state({ 1: { values: { 'ekf.pos_x': 7 } } }));
    const html = env.doc.getElementById('te-tbody').innerHTML;
    assert.ok(html.indexOf('title="ekf.pos_x"') !== -1);
    assert.ok(html.indexOf('>pos_x<') !== -1);
    console.log('  PASS: displays pos_x with full key ekf.pos_x in title');
    env.destroy();
  }

  console.log('\nALL CHECKS PASSED SUCCESSFULLY.');
}

(async function () {
  try { runChecks(); } catch (e) { console.error(e); process.exit(1); }
}());
