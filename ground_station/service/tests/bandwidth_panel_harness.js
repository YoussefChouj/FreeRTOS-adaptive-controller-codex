'use strict';
/**
 * Offline verification harness for bandwidth-panel.js (Bandwidth Manager),
 * task 20260921-153142. Covers:
 *   1. Honest empty state: "No active streams", warning hidden, used 0.0.
 *   2. Request form open / cancel.
 *   3. Submit defaults (rate 10 -> divider 20, channel 1 -> slot 1): the
 *      recording fetch stub captures the POST body; assert it exactly.
 *   4. Validation: out-of-range channel clamped to 1; non-numeric rate
 *      defaults to 10; divider clamped/derived per case.
 *   5. Budget warning: 70 Hz -> WARNING (88%); 85 Hz -> BUDGET EXCEEDED.
 *   6. Honesty: fresh slot without loss metadata reads NOT PUBLISHED
 *      (never 0.00%); an uncomputed rate reads NO DATA (never 0.00 Hz).
 *   7. Unsubscribe button captured by fetch stub: body {slot, divider:0}.
 *
 * The sandbox has no require and fetch/XHR are recording/blocking stubs,
 * so a real network call is structurally impossible.
 *
 * Run:  node ground_station/service/tests/bandwidth_panel_harness.js
 * Exit code 0 iff every check passes.
 */
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const assert = require('assert');

const PANEL = path.join(__dirname, '..', '..', '..',
  'docs', 'dashboard-platform', 'shell', 'plugins', 'bandwidth-panel.js');

// ── Fake DOM ──────────────────────────────────────────────────────────────
class Element {
  constructor(id, tag) {
    this.id = id;
    this.tagName = (tag || 'div').toUpperCase();
    this._html = '';
    this.textContent = '';
    this.value = '';
    this.checked = false;
    this.disabled = false;
    this.className = '';
    this.handlers = {};
    this.style = {};
    this.dataset = {};
    this.doc = null;
    const self = this;
    this.classList = {
      add(c) { if (!self.className.split(/\s+/).includes(c)) self.className =
        (self.className + ' ' + c).trim(); },
      remove(c) { self.className = self.className.split(/\s+/)
        .filter((x) => x !== c).join(' '); },
      contains(c) { return self.className.split(/\s+/).includes(c); },
    };
  }
  addEventListener(ev, fn) {
    (this.handlers[ev] = this.handlers[ev] || []).push(fn);
  }
  getAttribute(name) {
    var key = /^data-/.test(name) ? name.slice(5) : name;
    return this.dataset[key] !== undefined ? this.dataset[key] : null;
  }
  setAttribute(name, val) {
    var key = /^data-/.test(name) ? name.slice(5) : name;
    this.dataset[key] = String(val);
  }
  get innerHTML() { return this._html; }
  set innerHTML(html) {
    this._html = html;
    if (this.doc) this.doc.scan(html);
  }
}

class FakeDocument {
  constructor() {
    this.elements = {};
    this.containerHTML = '';
    this._nodeCache = {};
  }
  scan(html) {
    this.containerHTML = html;
    const tags = html.match(/<[a-zA-Z0-9-]+[^>]*>/g) || [];
    for (const tag of tags) {
      const idm = tag.match(/\bid="([^"]+)"/);
      if (!idm) continue;
      const id = idm[1];
      const el = new Element(id, tag.slice(1).split(/[\s>]/)[0]);
      el.doc = this;
      const vm2 = tag.match(/\bvalue="([^"]*)"/);
      if (vm2) el.value = vm2[1];
      const textMatch = html.match(
        new RegExp('<[a-zA-Z0-9-]+[^>]*\\bid="' + id + '"[^>]*>([^<]*)<'));
      if (textMatch) { el.textContent = textMatch[1]; el._html = textMatch[1]; }
      this.elements[id] = el;
    }
  }
  getElementById(id) { return this.elements[id] || null; }
  /* Parse class-bearing tags out of the container markup; stable node
   * identity per tag string while markup is unchanged. */
  querySelectorAll(cls) {
    const dot = cls.replace(/^\./, '');
    const out = [];
    // Search every live node's markup: innerHTML replaces on one node must
    // not hide elements that live inside a sibling (e.g. tbody rows while
    // #bw-budget-warning was the last element written).
    Object.keys(this.elements).forEach((nid) => {
      this._collectTags(this.elements[nid]._html, dot, out);
    });
    return out;
  }
  _collectTags(html, dot, out) {
    const re = /<[a-zA-Z0-9-]+[^>]*>/g;
    let m;
    while ((m = re.exec(html)) !== null) {
      const tag = m[0];
      if (!new RegExp('class="[^"]*\\b' + dot + '\\b[^"]*"').test(tag)) continue;
      let el = this._nodeCache[tag];
      if (!el) {
        const idm = tag.match(/\bid="([^"]+)"/);
        el = new Element(idm ? idm[1] : null,
          tag.slice(1).split(/[\s>]/)[0]);
        el.doc = this;
        this._nodeCache[tag] = el;
      }
      const dsm = tag.match(/\bdata-slot="([^"]+)"/);
      if (dsm) el.dataset.slot = dsm[1];
      out.push(el);
    }
  }
}

// ── Panel loading ─────────────────────────────────────────────────────────
function loadPanel() {
  const doc = new FakeDocument();
  const container = new Element('container', 'div');
  container.doc = doc;
  const api = {
    stateCb: null,
    subscribe(cb) { this.stateCb = cb; },
    registerPanel(name, renderFn) { this.renderFn = renderFn; },
    getState() { return this._lastState || null; },
  };
  const timerFns = [];
  const fetchRecords = [];
  let xhrBuilt = 0;
  const sandbox = {
    document: doc,
    console: { log() {}, warn() {}, error() {} },
    Date, Math, Number, String, Boolean, Array, Object, JSON,
    parseFloat, parseInt, isNaN,
    setTimeout(fn) { timerFns.push(fn); return timerFns.length; },
    clearTimeout() {},
    setInterval() { return 0; },
    clearInterval() {},
    requestAnimationFrame(fn) { fn(); return 0; },
    fetch(url, opts) {
      fetchRecords.push({ url, opts: opts || null });
      return Promise.resolve({
        ok: true, status: 200,
        json: () => Promise.resolve({}),
      });
    },
    XMLHttpRequest: function () {
      xhrBuilt += 1;
      throw new Error('XMLHttpRequest is blocked in this harness');
    },
  };
  sandbox.window = sandbox;
  // Recording stand-in for the shell api the panel sends through; records
  // the body it would POST and never touches the network.
  sandbox.__gs_shell_api__ = {
    subscribeSlot(slot, divider, ranges) {
      fetchRecords.push({ url: '/subscribe', opts: { method: 'POST',
        body: JSON.stringify({ slot, divider, ranges }) } });
      return Promise.resolve({ via: '/subscribe' });
    },
    unsubscribeSlot(slot) {
      fetchRecords.push({ url: '/subscribe', opts: { method: 'POST',
        body: JSON.stringify({ slot, divider: 0 }) } });
      return Promise.resolve({ via: '/subscribe' });
    },
  };
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
    doc, api, sandbox,
    fetchRecords: () => fetchRecords.slice(),
    lastBody() {
      const r = fetchRecords[fetchRecords.length - 1];
      return JSON.parse(r.opts.body);
    },
    flushTimers() {
      const fns = timerFns.splice(0, timerFns.length);
      fns.forEach((fn) => fn());
    },
    feed(state) {
      api._lastState = state;
      api.stateCb(state);
    },
    click(id) {
      const el = doc.getElementById(id);
      assert.ok(el && el.handlers.click, 'no click handler on #' + id);
      el.handlers.click.forEach((fn) => fn.call(el));
    },
    clickEl(el) {
      el.handlers.click.forEach((fn) => fn.call(el));
    },
    destroy() { sandbox.pluginDestroy(); },
  };
}

function tick() { return new Promise((r) => setImmediate(r)); }

function slot1Sample(seq, tms, extra) {
  return {
    streams: {
      '1': Object.assign({
        sequence: seq, received: seq, dropped: 0,
        source_time_ms: tms, values: { 'a': 1 },
      }, extra),
    },
  };
}

async function runChecks() {
  console.log('--- BANDWIDTH PANEL OFFLINE CHECKS ---');

  // 1. Empty state
  {
    console.log('\n[CHECK 1: no streams -> "No active streams", warning hidden]');
    const env = loadPanel();
    env.flushTimers();
    assert.ok(env.doc.getElementById('bw-chart-svg').innerHTML
      .includes('No active streams'));
    assert.ok(env.doc.getElementById('bw-stream-tbody').innerHTML
      .includes('No active streams'));
    env.feed({ streams: {} });   // triggers renderBudgetWarning
    assert.strictEqual(env.doc.getElementById('bw-budget-warning').style.display,
      'none');
    assert.strictEqual(env.doc.getElementById('bw-used').textContent, '0.0');
    console.log('  PASS: chart + table honest, warning hidden, used 0.0');
    env.destroy();
  }

  // 2. Form open / cancel
  {
    console.log('\n[CHECK 2: request form opens and cancels]');
    const env = loadPanel();
    env.click('bw-request-btn');
    const form = env.doc.getElementById('bw-request-form');
    assert.ok(form.classList.contains('visible'), 'form shown');
    env.doc.getElementById('bw-request-result').textContent = 'stale';
    env.click('bw-cancel-request');
    assert.ok(!form.classList.contains('visible'), 'form hidden after cancel');
    assert.strictEqual(env.doc.getElementById('bw-request-result').textContent,
      '');
    console.log('  PASS: form visible then hidden, result cleared');
    env.destroy();
  }

  // 3. Submit defaults — captured body
  {
    console.log('\n[CHECK 3: default submit -> POST body {slot:1,divider:20}]');
    const env = loadPanel();
    env.click('bw-request-btn');
    env.click('bw-submit-request');
    await tick(); await tick();
    const recs = env.fetchRecords();
    assert.strictEqual(recs.length, 1);
    assert.strictEqual(recs[0].url, '/subscribe');
    assert.strictEqual(recs[0].opts.method, 'POST');
    assert.deepStrictEqual(env.lastBody(),
      { slot: 1, divider: 20, ranges: [] });
    assert.ok(env.doc.getElementById('bw-request-result').innerHTML
      .includes('Subscribed slot 1'));
    env.flushTimers();   // form auto-hide after 1.5s
    assert.ok(!env.doc.getElementById('bw-request-form').classList
      .contains('visible'));
    console.log('  PASS: body captured exactly; success shown; form auto-hides');
    env.destroy();
  }

  // 4. Validation
  {
    console.log('\n[CHECK 4: channel 9 clamps to slot 1; bad rate defaults 10]');
    const env = loadPanel();
    env.click('bw-request-btn');
    env.doc.getElementById('bw-new-rate').value = '50';
    env.doc.getElementById('bw-new-channel').value = '9';
    env.click('bw-submit-request');
    await tick(); await tick();
    assert.deepStrictEqual(env.lastBody(),
      { slot: 1, divider: 4, ranges: [] }, 'rate 50 -> divider 4');
    env.flushTimers();
    console.log('  PASS: channel 9 clamped slot 1, divider round(200/50)=4');

    env.click('bw-request-btn');
    env.doc.getElementById('bw-new-rate').value = 'abc';
    env.doc.getElementById('bw-new-channel').value = '2';
    env.click('bw-submit-request');
    await tick(); await tick();
    assert.deepStrictEqual(env.lastBody(),
      { slot: 2, divider: 20, ranges: [] }, 'NaN rate -> 10 Hz -> divider 20');
    env.flushTimers();
    console.log('  PASS: non-numeric rate defaults 10Hz, slot 2 used');
    env.destroy();
  }

  // 5. Budget warning
  {
    console.log('\n[CHECK 5: 70Hz WARNING/88%; 85Hz BUDGET EXCEEDED]');
    const env = loadPanel();
    env.feed(slot1Sample(1, 0));       // first sample: rate unknown
    env.feed(slot1Sample(71, 1000));   // 70 seq / 1s = 70 Hz
    const warn = env.doc.getElementById('bw-budget-warning');
    assert.notStrictEqual(warn.style.display, 'none');
    assert.ok(warn.innerHTML.includes('WARNING') && warn.innerHTML.includes('88%'),
      warn.innerHTML);
    console.log('  PASS: 70 Hz -> WARNING at 88%');

    env.feed(slot1Sample(156, 2000));  // 85 seq / 1s = 85 Hz
    assert.ok(env.doc.getElementById('bw-budget-warning').innerHTML
      .includes('BUDGET EXCEEDED'));
    assert.strictEqual(env.doc.getElementById('bw-used').textContent, '85.0');
    console.log('  PASS: 85 Hz -> BUDGET EXCEEDED, used reads 85.0');
    env.destroy();
  }

  // 6. Honesty: loss NOT PUBLISHED, rate NO DATA
  {
    console.log('\n[CHECK 6: missing loss -> NOT PUBLISHED; unknown rate -> NO DATA]');
    const env = loadPanel();
    env.feed(slot1Sample(5, 0));   // single sample, no loss_pct field
    const row = env.doc.getElementById('bw-stream-tbody').innerHTML;
    assert.ok(row.includes('NOT PUBLISHED'), row);
    assert.ok(!row.includes('0.00%'), 'no fake zero loss: ' + row);
    assert.ok(row.includes('NO DATA'), 'rate uncomputed -> NO DATA: ' + row);
    console.log('  PASS: loss reads NOT PUBLISHED; rate reads NO DATA');
    env.destroy();
  }

  // 7. Unsubscribe captured
  {
    console.log('\n[CHECK 7: remove slot -> POST body {slot:1,divider:0}]');
    const env = loadPanel();
    env.feed(slot1Sample(1, 0));
    env.feed(slot1Sample(11, 100));   // 100 Hz slot
    const btns = env.doc.querySelectorAll('.bw-remove-btn');
    assert.strictEqual(btns.length, 1);
    env.clickEl(btns[0]);
    await tick(); await tick();
    assert.deepStrictEqual(env.lastBody(),
      { slot: 1, divider: 0, ranges: [] });
    assert.ok(env.doc.getElementById('bw-stream-tbody').innerHTML
      .includes('No active streams'), 'slot removed after unsubscribe');
    console.log('  PASS: unsubscribe body captured; slot row gone');
    env.destroy();
  }

  console.log('\nALL CHECKS PASSED SUCCESSFULLY.');
}

runChecks().catch((e) => { console.error(e); process.exit(1); });
