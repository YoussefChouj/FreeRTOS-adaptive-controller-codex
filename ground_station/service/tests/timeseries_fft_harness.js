'use strict';
/**
 * Offline harness for the dashboard Time Series and FFT Spectrum panels
 * (Bug 4, AUDIT_2026-09-21 §Bug 4). Loads the two panel plugins from
 * docs/dashboard-platform/shell/plugins/ into a fake DOM, feeds states
 * shaped exactly like the live /state stream the Telemetry Explorer
 * reads (raw slot0.<dwarf> keys plus the spec aliases the telemetry
 * adapter publishes), and asserts:
 *
 *   1. Time Series plots the selected variables: the SVG renders
 *      <polyline points="..."> for the enabled keys instead of the
 *      "Waiting for data on selected variables…" placeholder.
 *   2. The selected keys bind through BOTH spellings the Explorer
 *      streams: the bare spec key ('status.roll_deg', …) AND the
 *      slot-prefixed raw DWARF key ('slot0.imu_data.rol', …).
 *   3. FFT Spectrum fills its sample buffer from the selected variable
 *      and renders a spectrum (its svg leaves the "Collecting samples…"
 *      state) rather than dropping every sample.
 *   4. Honesty: when every selected variable is genuinely absent from
 *      the stream, Time Series renders the explicit placeholder and
 *      NO fabricated zero line.
 *
 * Prints a JSON result on stdout; exits non-zero on failure.
 */

const fs = require('fs');
const path = require('path');
const vm = require('vm');

const PLUGIN_DIR = path.join(
  __dirname, '..', '..', '..', 'docs', 'dashboard-platform', 'shell', 'plugins');

const TSPANEL = path.join(PLUGIN_DIR, 'time-series-panel.js');
const FFTSPANEL = path.join(PLUGIN_DIR, 'fft-panel.js');

// ── Minimal DOM stubs ────────────────────────────────────────────────────
class El {
  constructor(id) {
    this.id = id || '';
    this.tagName = 'DIV';
    this._text = '';
    this._html = '';
    this.className = '';
    this.style = {};
    this.dataset = {};
    this.children = [];
    this.title = '';
    this.value = '';
    this.checked = false;
    const set = new Set();
    this.classList = {
      add: (c) => set.add(c),
      remove: (c) => set.delete(c),
      toggle: (c, force) => {
        const on = force === undefined ? !set.has(c) : !!force;
        if (on) set.add(c); else set.delete(c);
      },
      contains: (c) => set.has(c),
    };
  }
  get textContent() { return this._text; }
  set textContent(v) { this._text = String(v); this._html = String(v); }
  get innerHTML() { return this._html; }
  set innerHTML(v) { this._html = String(v); this._text = String(v); }
  appendChild(child) { this.children.push(child); return child; }
  setAttribute(name, val) { this.dataset[name] = String(val); }
  getAttribute(name) { return name in this.dataset ? this.dataset[name] : null; }
  addEventListener() {}
  remove() {}
}

function makeHarness() {
  const elements = new Map();
  const errors = [];
  const subscriptions = [];
  let pluginInit = null;
  let pluginDestroy = null;

  const doc = {
    getElementById(id) {
      if (!elements.has(id)) elements.set(id, new El(id));
      return elements.get(id);
    },
    createElement() { return new El(''); },
    querySelectorAll() { return []; },
    querySelector() { return null; },
    body: new El('body'),
  };

  const api = {
    subscribe(fn) { subscriptions.push(fn); },
    registerPanel(name, renderCb) {
      const container = new El('container-' + name.toLowerCase().replace(/\W+/g, '-'));
      renderCb(container);
    },
  };

  const sandbox = {
    console: {
      log() {},
      warn() {},
      error(...args) { errors.push(args.map(String).join(' ')); },
    },
    document: doc,
    localStorage: { getItem: () => null, setItem: () => {} },
    setTimeout: () => 1,
    clearTimeout: () => {},
    setInterval: () => 0,
    clearInterval: () => {},
    requestAnimationFrame: (fn) => { fn(); return 1; },
    fetch: () => Promise.resolve({ ok: true, json: () => Promise.resolve({}) }),
    __registerPlugin__: (name, init, destroy) => { pluginInit = init; pluginDestroy = destroy; },
  };
  sandbox.window = sandbox;
  vm.createContext(sandbox);
  return { sandbox, doc, elements, errors, subscriptions, api: ((_s, _d) => api)(),
           dispatch: (state) => subscriptions.forEach((fn) => fn(state)) };
}

// ── Live stream shape ────────────────────────────────────────────────────
let tick = 0;
function liveState(aliased) {
  tick++;
  const t = tick;
  const raw = {
    'slot0.DroneStatus.ARM_Status': 1,
    'slot0.DroneStatus.FlyMode': 2,
    'slot0.imu_data.rol': (-1.0 + t * 0.01),
    'slot0.imu_data.pit': (-2.0 + t * 0.01),
    'slot0.imu_data.yaw': (157.3 + t * 0.05),
    'slot0.mrac_state.roll.e': (0.02 * Math.sin(t)),
    'slot0.real_voltage': 23.4,
    'slot0.seq': 100 + t,
  };
  if (aliased) {
    // Spec aliases the telemetry adapter publishes (slot-0 binding table):
    raw['status.roll_deg'] = raw['slot0.imu_data.rol'];
    raw['status.pitch_deg'] = raw['slot0.imu_data.pit'];
    raw['status.yaw_deg'] = raw['slot0.imu_data.yaw'];
    raw['mrac.roll.e'] = raw['slot0.mrac_state.roll.e'];
    raw['status.vbat'] = raw['slot0.real_voltage'];
  }
  return {
    schema_id: 'r1-s1-TEST',
    session_id: 'sess-test',
    connected: true,
    samples: t,
    streams: { '0': { sequence: t, values: raw } },
    command_results: [],
  };
}

function loadPlugin(file) {
  const src = fs.readFileSync(file, 'utf8');
  const h = makeHarness();
  vm.runInContext(src, h.sandbox, { filename: 'panel.js' });
  if (typeof h.sandbox.window.__PLUGIN_INIT__ === 'function') {
    h.sandbox.window.__PLUGIN_INIT__(h.api);
  }
  return h;
}

function assert(cond, msg, result) {
  if (!cond) {
    result.result = 'FAIL';
    result.failure = msg;
  }
}

const result = {
  result: 'PASS',
  failure: null,
  time_series_aliased_plots: false,
  time_series_raw_prefix_plots: false,
  time_series_waiting: false,
  fft_aliased_plots: false,
  fft_raw_prefix_plots: false,
  honest_absent_not_zero: false,
};

try {
  // 1. Time Series — aliased spec keys (post-adapter live stream).
  let h = loadPlugin(TSPANEL);
  for (let i = 0; i < 20; i++) h.dispatch(liveState(true));
  let html = String(h.doc.getElementById('ts-variable-rows').innerHTML);
  result.time_series_aliased_plots = /<polyline[^>]*points=/.test(html);
  result.time_series_waiting = html.indexOf('Waiting for data on selected variables') !== -1;
  assert(result.time_series_aliased_plots && !result.time_series_waiting,
    'Time Series did not plot under aliased keys; html=' + html.slice(0, 120), result);

  // 2. Time Series — RAW slot0.<dwarf> keys only, no aliases: exactly what
  //    the Telemetry Explorer surfaces unfailingly. The panel must bind.
  h = loadPlugin(TSPANEL);
  for (let i = 0; i < 20; i++) h.dispatch(liveState(false));
  html = String(h.doc.getElementById('ts-variable-rows').innerHTML);
  result.time_series_raw_prefix_plots = /<polyline[^>]*points=/.test(html)
    && html.indexOf('Waiting for data on selected variables') === -1;
  assert(result.time_series_raw_prefix_plots,
    'Time Series did not plot raw slot-prefixed keys; html=' + html.slice(0, 120), result);

  // 3. Honesty — every default-selected variable absent: explicit placeholder,
  //    no fabricated polyline.
  h = loadPlugin(TSPANEL);
  for (let i = 0; i < 20; i++) {
    h.dispatch({ streams: { '0': { values: { 'slot0.DroneStatus.ARM_Status': 1, 'slot0.seq': i } } } });
  }
  html = String(h.doc.getElementById('ts-variable-rows').innerHTML);
  result.honest_absent_not_zero =
    html.indexOf('— (no data)') !== -1
    && html.indexOf('<polyline') === -1;
  assert(result.honest_absent_not_zero,
    'Time Series fabricated a line when every selected variable was absent; html=' + html.slice(0, 120),
    result);

  // 4. FFT Spectrum — aliased keys (default selection = status.roll_deg).
  h = loadPlugin(FFTSPANEL);
  for (let i = 0; i < 140; i++) h.dispatch(liveState(true));
  html = String(h.doc.getElementById('fft-chart-svg').innerHTML);
  result.fft_aliased_plots =
    html.indexOf('Collecting samples…') === -1 && html.length > 100;
  assert(result.fft_aliased_plots,
    'FFT never left "Collecting samples…" under aliased keys; svg=' + html.slice(0, 120), result);

  // 5. FFT Spectrum — RAW slot-prefixed keys only.
  h = loadPlugin(FFTSPANEL);
  for (let i = 0; i < 140; i++) h.dispatch(liveState(false));
  html = String(h.doc.getElementById('fft-chart-svg').innerHTML);
  result.fft_raw_prefix_plots =
    html.indexOf('Collecting samples…') === -1 && html.length > 100;
  assert(result.fft_raw_prefix_plots,
    'FFT never left "Collecting samples…" under raw slot-prefixed keys; svg=' + html.slice(0, 120), result);

  const out = {
    result: result.result,
    failure: result.failure,
    checks: {
      time_series_aliased_plots: result.time_series_aliased_plots,
      time_series_raw_prefix_plots: result.time_series_raw_prefix_plots,
      time_series_waiting: result.time_series_waiting,
      honest_absent_not_zero: result.honest_absent_not_zero,
      fft_aliased_plots: result.fft_aliased_plots,
      fft_raw_prefix_plots: result.fft_raw_prefix_plots,
    },
  };
  console.log(JSON.stringify(out));
  if (result.failure) process.exit(1);
} catch (err) {
  console.error('TELEM PLOT HARNESS FAILED: ' + (err && err.stack || err));
  process.exit(1);
}