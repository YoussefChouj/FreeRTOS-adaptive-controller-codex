'use strict';
/**
 * Offline verification harness for fft-panel.js (FFT Spectrum),
 * task 20260921-153142. Covers:
 *   1. Honest empty state with zero samples: "Collecting samples… (0/64)",
 *      no bars, peak read "Peak: —" (no fabricated magnitude).
 *   2. Partial collection: progress counter (30/64), still no spectrum.
 *   3. Full window: 64 samples of an on-bin 12.5 Hz sine (fs=100) ->
 *      31 bars, peak label 12.5Hz, exactly one red peak bar.
 *   4. Variable (config) change: buffer cleared, honest collecting state
 *      and stale peak text reset; new key then renders normally.
 *   5. Sample-rate (window) change accepted (fs=200 footer, peak rescales
 *      to 25.0Hz); out-of-range 5000 rejected.
 *   6. Constant signal -> no fake 0.0Hz peak label; and the fetch/XHR
 *      stubs record ZERO sends (panel has no network path; sandbox has no
 *      require, so a real network call is structurally impossible).
 *
 * Run:  node ground_station/service/tests/fft_panel_harness.js
 * Exit code 0 iff every check passes.
 */
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const assert = require('assert');

const PANEL = path.join(__dirname, '..', '..', '..',
  'docs', 'dashboard-platform', 'shell', 'plugins', 'fft-panel.js');

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
    return this.dataset[name] !== undefined ? this.dataset[name] : null;
  }
  setAttribute(name, val) { this.dataset[name] = String(val); }
  get innerHTML() { return this._html; }
  set innerHTML(html) {
    this._html = html;
    if (this.doc) this.doc.scan(html, this);
  }
}

class FakeDocument {
  constructor() {
    this.elements = {};
  }
  scan(html) {
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
  };
  const timerFns = [];
  const fetchRecords = [];
  let xhrBuilt = 0;
  const sandbox = {
    document: doc,
    console: { log() {}, warn() {}, error() {} },
    Date, Math, Number, String, Boolean, Array, Object, JSON,
    parseFloat, parseInt, isNaN, Float64Array,
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
    xhrCount: () => xhrBuilt,
    flushTimers() {
      const fns = timerFns.splice(0, timerFns.length);
      fns.forEach((fn) => fn());
    },
    feed(values) {
      api.stateCb({ connected: true, streams: { '0': { values } } });
    },
    change(id, value) {
      const el = doc.getElementById(id);
      assert.ok(el && el.handlers.change, 'no change handler on #' + id);
      el.value = value;
      el.handlers.change.forEach((fn) => fn.call(el));
    },
    destroy() { sandbox.pluginDestroy(); },
  };
}

// n samples of a sine with f Hz; fs fixed at 100 for generation.
function sine(f, n) {
  const out = new Array(n);
  for (let i = 0; i < n; i++) {
    out[i] = Math.sin(2 * Math.PI * f * (i / 100));
  }
  return out;
}

function countTags(html, tag) {
  return (html.match(new RegExp('<' + tag + '(\\s|>)', 'g')) || []).length;
}

function runChecks() {
  console.log('--- FFT PANEL OFFLINE CHECKS ---');

  // 1. Honest empty state with no samples
  {
    console.log('\n[CHECK 1: zero samples -> "Collecting samples… (0/64)", no bars, peak —]');
    const env = loadPanel();
    env.flushTimers();   // initial setTimeout render
    let svg = env.doc.getElementById('fft-chart-svg').innerHTML;
    assert.ok(svg.includes('Collecting samples…') && svg.includes('(0/64)'),
      'empty chart must read collecting 0/64: ' + svg);
    assert.strictEqual(countTags(svg, 'rect'), 0, 'no bars without samples');
    // Feeding an unrelated key must not push a fake zero into the buffer.
    env.feed({ 'c.altitude': 1.5 });
    svg = env.doc.getElementById('fft-chart-svg').innerHTML;
    assert.ok(svg.includes('(0/64)'), 'unpublished key must not become a sample: ' + svg);
    assert.strictEqual(env.doc.getElementById('fft-peak-info').innerHTML,
      'Peak: —');
    console.log('  PASS: honest collecting state; missing key not faked; peak reads —');
    env.destroy();
  }

  // 2. Partial collection
  {
    console.log('\n[CHECK 2: 30 samples -> "(30/64)", still no spectrum]');
    const env = loadPanel();
    sine(12.5, 30).forEach((v) => env.feed({ 'status.roll_deg': v }));
    const svg = env.doc.getElementById('fft-chart-svg').innerHTML;
    assert.ok(svg.includes('(30/64)'), 'progress counter: ' + svg);
    assert.strictEqual(countTags(svg, 'rect'), 0);
    console.log('  PASS: counter at 30/64 and zero bars');
    env.destroy();
  }

  // 3. Full window: on-bin 12.5 Hz sine
  {
    console.log('\n[CHECK 3: 64 samples 12.5Hz -> 31 bars, peak 12.5Hz, red peak bar]');
    const env = loadPanel();
    sine(12.5, 64).forEach((v) => env.feed({ 'status.roll_deg': v }));
    const svg = env.doc.getElementById('fft-chart-svg').innerHTML;
    assert.strictEqual(countTags(svg, 'rect'), 31, 'numBars = halfN-1 = 31');
    assert.ok(svg.includes('>12.5Hz<'), 'peak frequency label: ' + svg);
    const red = (svg.match(/<rect[^>]*fill="#e94560"/g) || []).length;
    assert.strictEqual(red, 1, 'exactly one red (peak) bar');
    const info = env.doc.getElementById('fft-peak-info').innerHTML;
    assert.ok(info.includes('12.5 Hz'), 'peak info: ' + info);
    console.log('  PASS: 31 bars, peak label 12.5Hz, one red bar, info updated');
    env.destroy();
  }

  // 4. Variable (config) change
  {
    console.log('\n[CHECK 4: variable change resets buffer + peak; new key renders]');
    const env = loadPanel();
    sine(12.5, 64).forEach((v) => env.feed({ 'status.roll_deg': v }));
    assert.ok(env.doc.getElementById('fft-chart-svg').innerHTML.includes('12.5Hz'));

    env.change('fft-var', 'status.pitch_deg');
    const svg = env.doc.getElementById('fft-chart-svg').innerHTML;
    assert.ok(svg.includes('(0/64)'), 'buffer cleared on variable change: ' + svg);
    // Bug fix: the stale peak text must be reset by the collecting branch.
    assert.strictEqual(env.doc.getElementById('fft-peak-info').innerHTML,
      'Peak: —', 'stale peak must not linger after switching variables');
    console.log('  PASS: buffer cleared, stale peak reset to —');

    sine(6.25, 64).forEach((v) => env.feed({ 'status.pitch_deg': v }));
    assert.ok(env.doc.getElementById('fft-chart-svg').innerHTML.includes('>6.3Hz<'),
      'pitch bin 4 -> 6.3Hz (label rounding)');
    console.log('  PASS: new variable renders its own peak 6.3Hz');
    env.destroy();
  }

  // 5. Sample-rate (window) change
  {
    console.log('\n[CHECK 5: rate change to 200 accepted (peak 25.0Hz); 5000 rejected]');
    const env = loadPanel();
    sine(12.5, 64).forEach((v) => env.feed({ 'status.roll_deg': v }));

    env.change('fft-sr', '200');
    const svg = env.doc.getElementById('fft-chart-svg').innerHTML;
    assert.ok(svg.includes('fs=200Hz'), 'footer reflects new window: ' + svg);
    assert.ok(svg.includes('>25.0Hz<'), 'bin 8 at fs 200 -> 25.0Hz: ' + svg);
    console.log('  PASS: fs=200 accepted, peak rescaled to 25.0Hz');

    env.change('fft-sr', '5000');
    const svg2 = env.doc.getElementById('fft-chart-svg').innerHTML;
    assert.ok(svg2.includes('fs=200Hz'), 'out-of-range rejected, window unchanged');
    console.log('  PASS: 5000Hz silently rejected, fs stays 200');
    env.destroy();
  }

  // 6. Constant signal honesty + zero-network guard
  {
    console.log('\n[CHECK 6: constant signal -> no fake 0Hz peak label; zero sends]');
    const env = loadPanel();
    for (let i = 0; i < 64; i++) env.feed({ 'status.roll_deg': 3 });
    const svg = env.doc.getElementById('fft-chart-svg').innerHTML;
    assert.ok(!svg.includes('>0.0Hz<'),
      'a zero-frequency "peak" must not be labelled above the chart: ' + svg);
    assert.ok(!/undefined|NaN/.test(svg), 'no undefined/NaN text');
    const records = env.fetchRecords();
    assert.strictEqual(records.length, 0, 'no fetch sends: ' + JSON.stringify(records));
    assert.strictEqual(env.xhrCount(), 0);
    assert.strictEqual(env.sandbox.require, undefined);
    const src = fs.readFileSync(PANEL, 'utf8');
    assert.strictEqual(src.indexOf('fetch('), -1);
    console.log('  PASS: no fake 0.0Hz label; zero fetch/XHR; no require/network path');
    env.destroy();
  }

  console.log('\nALL CHECKS PASSED SUCCESSFULLY.');
}

try { runChecks(); } catch (e) { console.error(e); process.exit(1); }
