'use strict';
/**
 * Offline verification harness for the Control-tab command-panel widgets
 * (docs/dashboard-platform/plugins/command-panel.js, task 20260922-061106,
 * AUDIT_2026-09-21 §3.1–3.2).
 *
 * Drives the panel in a fake DOM with a stubbed shell API and asserts the
 * usable shortcuts + safety invariants the sections add:
 *   - value-parameter commands render as widgets (numeric input; slider when
 *     the catalog gives a finite range), never as list-only entries
 *   - flag commands render as checkboxes/toggles whose state follows telemetry
 *     (never optimistic)
 *   - input validation disables Send with a reason (NaN / out of range)
 *   - every send routes through the existing arm gate (fail-closed)
 *   - the render/poll loop survives a failed command POST (Bug 1, commit 7dea39f)
 *
 * Run:  node ground_station/service/tests/command_panel_harness.js
 * Exit code 0 iff every check passes.
 */
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const PANEL = path.join(__dirname, '..', '..', '..',
  'docs', 'dashboard-platform', 'shell', 'plugins', 'command-panel.js');

// ── Fake DOM ───────────────────────────────────────────────────────────────
class Element {
  constructor(id, tag) {
    this.id = id;
    this.tagName = (tag || 'div').toUpperCase();
    this._html = '';
    this.textContent = '';
    this.value = '';
    this.checked = false;
    this.disabled = false;
    this.min = '';
    this.max = '';
    this.className = '';
    this.handlers = {};
    this.style = {};
    this._valueInit = false;
    this.doc = null;
  }
  addEventListener(ev, fn) { (this.handlers[ev] = this.handlers[ev] || []).push(fn); }
  dispatch(ev) {
    (this.handlers[ev] || []).forEach((fn) => fn.call(this, { type: ev, target: this }));
  }
  get innerHTML() { return this._html; }
  set innerHTML(html) { this._html = html; if (this.doc) this.doc.scan(html); }
}

class FakeDocument {
  constructor() {
    this.elements = {};
    this.hidden = false;
  }
  scan(html) {
    const tags = html.match(/<[a-zA-Z][^>]*>/g) || [];
    for (const tag of tags) {
      const idm = tag.match(/\bid="([^"]+)"/);
      if (!idm) continue;
      let el = this.elements[idm[1]];
      if (!el) { el = new Element(idm[1], tag.slice(1)); this.elements[idm[1]] = el; }
      const vm_ = tag.match(/\bvalue="([^"]*)"/);
      if (vm_ && el._valueInit !== true) { el.value = vm_[1]; el._valueInit = true; }
      const mn = tag.match(/\bmin="([^"]*)"/);
      if (mn) el.min = mn[1];
      const mx = tag.match(/\bmax="([^"]*)"/);
      if (mx) el.max = mx[1];
      if (/\bchecked\b/.test(tag)) el.checked = true;
    }
  }
  getElementById(id) { return this.elements[id] || null; }
  querySelectorAll() { return []; }
  querySelector() { return null; }
}

// ── Stubbed shell API — records every command the panel would send ────────
function makeApi(opts) {
  opts = opts || {};
  const api = {
    commands: [],
    armState: opts.armState !== undefined ? opts.armState : 'disarmed',
    rejectsSoFar: 0,            // first N submitCommand calls reject
    rejectCount: opts.rejectCount || 0,
    txid: 0,
    getArmState() { return api.armState; },
    submitCommand(id, idx, val) {
      api.rejectsSoFar += 1;
      api.commands.push({ kind: 'submit', id, idx, val });
      if (api.rejectsSoFar <= api.rejectCount) {
        return Promise.reject(new Error('simulated send failure #' + api.rejectsSoFar));
      }
      api.txid += 1; api.lastTxid = api.txid;
      return Promise.resolve({ transaction_id: api.txid, ok: true });
    },
    getState() { return null; },
    subscribe(cb) { api.stateCb = cb; },
    registerPanel(name, renderFn) { api.panelName = name; api.renderFn = renderFn; },
  };
  return api;
}

// ── Load the plugin into a fresh sandbox ───────────────────────────────────
function loadPanel(api) {
  const doc = new FakeDocument();
  const container = new Element('cp-container', 'div');
  container.doc = doc;
  const sandbox = {
    document: doc,
    console: { log() {}, warn() {}, error() {} },
    setInterval, clearInterval, setTimeout, clearTimeout,
    alert() {},
    fetch: () => Promise.resolve({ json: () => Promise.resolve({ commands: {} }) }),
    localStorage: {
      _s: {},
      getItem(k) { return Object.prototype.hasOwnProperty.call(this._s, k) ? this._s[k] : null; },
      setItem(k, v) { this._s[k] = String(v); },
      removeItem(k) { delete this._s[k]; },
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
  return { sandbox, doc, container, api };
}

let failures = 0;
function check(ok, label) {
  console.log((ok ? 'PASS' : 'FAIL') + ': ' + label);
  if (!ok) failures++;
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// ── 1. Widget grouping ─────────────────────────────────────────────────────
let scenarioGrouping = () => {
  console.log('== 1. Widget grouping: value-params as widgets, flags as toggles ==');
  const api = makeApi();
  const h = loadPanel(api);
  const html = h.container.innerHTML;
  const doc = h.doc;

  const valueCards = (html.match(/data-widget="value"/g) || []).length;
  const flagCards = (html.match(/data-widget="flag"/g) || []).length;
  check(valueCards === 7, '7 value-parameter commands render as widget cards (got ' + valueCards + ')');
  check(flagCards === 2, '2 flag command cards render (0x0E, 0x0F) (got ' + flagCards + ')');

  // PID Gain (0x01) value param has finite range [0,200] -> slider + numeric.
  check(doc.getElementById('cp-widget-slider-1-2') !== null,
    'PID Gain value param (0x01 idx2, range 0..200) gets a slider');
  check(doc.getElementById('cp-widget-input-1-2') !== null,
    'PID Gain value param gets a numeric input');
  // MRAC Gamma (0x02) value param has NO max -> numeric input ONLY, no slider.
  check(doc.getElementById('cp-widget-input-2-2') !== null,
    'MRAC Gamma value param (0x02 idx2, no max) gets a numeric input');
  check(doc.getElementById('cp-widget-slider-2-2') === null,
    'MRAC Gamma value param gets NO slider (no finite max)');
  check(doc.getElementById('cp-widget-input-18-0') !== null,
    'Waypoint Spacing (0x12 idx0) is a numeric-input widget');

  // Flag toggles: 0x0F has 13 bools, 0x0E synthesizes 1.
  const flagCheckboxes = doc.elements;
  let ofCount = 0, aeCount = 0;
  Object.keys(flagCheckboxes).forEach((k) => {
    if (/^cp-flag-15-\d+$/.test(k)) ofCount++;
    if (/^cp-flag-14-\d+$/.test(k)) aeCount++;
  });
  check(ofCount === 13, 'Runtime Flags 0x0F renders 13 flag toggles (got ' + ofCount + ')');
  check(aeCount === 1, 'SDK Arm Authority 0x0E renders 1 toggle (got ' + aeCount + ')');
};

// ── 2. Range / NaN validation disables Send with a reason ─────────────────
let scenarioValidation = () => {
  console.log('== 2. Input validation: NaN / out of range disables Send with reason ==');
  const api = makeApi();
  const h = loadPanel(api);
  const doc = h.doc;
  const input = doc.getElementById('cp-widget-input-2-2');   // gamma, min 0 no max
  const send = doc.getElementById('cp-widget-send-2-2');
  const warn = doc.getElementById('cp-widget-warn-2-2');

  input.value = '-5';
  input.dispatch('input');
  check(send.disabled === true, 'gamma = -5 (below min 0) => Send DISABLED');
  check(warn.innerHTML.indexOf('below min') !== -1, 'warn explains the range violation: ' + warn.innerHTML.replace(/<[^>]*>/g, ' ').trim());

  input.value = 'abc';
  input.dispatch('input');
  check(send.disabled === true, 'gamma = abc (NaN) => Send DISABLED');
  check(warn.innerHTML.indexOf('numeric') !== -1, 'warn explains NaN input');

  input.value = '10';
  input.dispatch('input');
  check(send.disabled === false, 'gamma = 10 (valid) => Send ENABLED');
  check(warn.innerHTML === '', 'no warning for a valid value');
};

// ── 3. Gate fail-closed: armed blocks a disarmed-gated value widget ───────
let scenarioGate = () => {
  console.log('== 3. Gate fail-closed: disarmed-gated widget blocked while armed ==');
  const api = makeApi();
  const h = loadPanel(api);
  const doc = h.doc;
  api.stateCb({ streams: { '0': { values: { 'status.arm': 1 } } } });

  const input = doc.getElementById('cp-widget-input-1-2');   // PID gain value
  const send = doc.getElementById('cp-widget-send-1-2');
  input.value = '50';
  send.dispatch('click');
  // submitCommand() is not called -> nothing goes on the wire, interlock shown.
  check(api.commands.length === 0,
    'armed + disarmed-gated PID-Gain widget => ZERO commands emitted');
  const result = doc.getElementById('cp-result-box');
  check(result && result.innerHTML.indexOf('SAFETY') !== -1,
    'result box reports the safety interlock: ' + (result ? result.innerHTML.replace(/<[^>]*>/g, ' ').trim() : ''));

  // Disarmed now -> the same send IS allowed out (gate not fail-open).
  const api2 = makeApi();
  const h2 = loadPanel(api2);
  api2.stateCb({ streams: { '0': { values: { 'status.arm': 0 } } } });
  const in2 = h2.doc.getElementById('cp-widget-input-1-2');
  const sn2 = h2.doc.getElementById('cp-widget-send-1-2');
  in2.value = '50';
  sn2.dispatch('click');
  check(api2.commands.length === 1 && api2.commands[0].id === 1 && api2.commands[0].idx === 2,
    'disarmed => same PID-Gain widget send goes out as (1, 2, 50)');
};

// ── 4. Flag toggle state follows telemetry (never optimistic) ──────────────
let scenarioFlags = () => {
  console.log('== 4. Flag toggles follow telemetry, never optimistic ==');
  const api = makeApi();
  const h = loadPanel(api);
  const doc = h.doc;
  const cb = doc.getElementById('cp-flag-14-0');          // SDK arm authority
  const badge = doc.getElementById('cp-flag-state-14-0');

  // No telemetry symbol yet -> "not published", unchecked regardless of click.
  check(cb.checked === false, 'flag unchecked when telemetry absent');
  check(badge.innerHTML.indexOf('not published') !== -1,
    'flag state shows "not published" when telemetry absent');

  // SDK authority ON in telemetry -> toggle reflects it.
  api.stateCb({ streams: { '0': { values: { 'status.rc_authority': 1 } } } });
  check(cb.checked === true, 'telemetry rc_authority=1 => toggle checked (ON)');
  check(badge.textContent === 'ON', 'flag badge shows ON from telemetry');

  // Clicking does NOT optimistically flip state; it only sends a command.
  cb.checked = false; // simulate the browser leaving the box unchecked until telemetry confirms
  cb.dispatch('change');
  check(api.commands.length === 1 && api.commands[0].id === 14 && api.commands[0].idx === 0,
    'clicking the toggle emits (0x0E, 0, <checked-state>)');

  // Now telemetry goes OFF -> toggle reflects OFF (follows telemetry, not last click).
  api.stateCb({ streams: { '0': { values: { 'status.rc_authority': 0 } } } });
  check(cb.checked === false, 'telemetry rc_authority=0 => toggle unchecked (OFF)');
  check(badge.textContent === 'OFF', 'flag badge shows OFF from telemetry');

  // Telemetry goes stale/missing -> back to not-published (never shows 0).
  api.stateCb({ streams: { '0': { values: {} } } });
  check(badge.innerHTML.indexOf('not published') !== -1,
    'missing telemetry falls back to "not published", never 0');
};

// ── 5. Loop survives a failed command POST ─────────────────────────────────
let scenarioLoopSurvives = async () => {
  console.log('== 5. Render/poll loop survives a failed command POST ==');
  const api = makeApi({ rejectCount: 1 });   // first submitCommand rejects
  const h = loadPanel(api);
  const doc = h.doc;

  const input = doc.getElementById('cp-widget-input-18-0'); // waypoint spacing
  const send = doc.getElementById('cp-widget-send-18-0');
  input.value = '15';
  send.dispatch('click');
  await sleep(20);
  check(api.commands.length === 1,
    'first send attempted (rejectCount=1) then POST fails: ' + api.commands.length + ' command(s) recorded');
  const result = doc.getElementById('cp-result-box');
  check(result.innerHTML.indexOf('REJECTED') !== -1,
    'failed POST surfaces as a REJECTED result status');

  // The panel is still alive: a state tick refreshes widgets without throwing.
  api.stateCb({ streams: { '0': { values: { 'status.arm': 1 } } } });
  const armBadge = doc.getElementById('cp-arm-badge');
  check(armBadge && armBadge.innerHTML.indexOf('ARMED') !== -1,
    'after failure a state tick still refreshes the arm badge (render loop alive)');

  // A subsequent send still goes through the same path (loop not dead).
  api.stateCb({ streams: { '0': { values: { 'status.arm': 0 } } } });
  input.value = '20';
  send.dispatch('click');
  await sleep(20);
  check(api.commands.length === 2,
    'after a failed POST a later send still reaches the API (' + api.commands.length + ' total)');
};

// ── main ───────────────────────────────────────────────────────────────────
(async () => {
  scenarioGrouping();
  scenarioValidation();
  scenarioGate();
  scenarioFlags();
  await scenarioLoopSurvives();
  console.log(failures === 0 ? 'ALL CHECKS PASSED' : failures + ' CHECK(S) FAILED');
  process.exit(failures === 0 ? 0 : 1);
})().catch((e) => { console.error(e); process.exit(1); });