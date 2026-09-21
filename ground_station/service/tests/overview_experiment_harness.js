'use strict';
/**
 * Offline verification harness for the overview-panel experiment
 * observability widgets (task 20260921-103141):
 *   A. Flight FSM view — known state/phase highlights + dwell + last
 *      transition; keys absent → NOT PUBLISHED, never a default state;
 *      stale key → amber STALE with age; an unrecognized prior value in
 *      the transition falls back to its number, never "null →".
 *   B. Adaptation (MRAC) view — converging series shows the verdict and
 *      its numeric evidence; too little history → UNKNOWN; a gap in the
 *      samples shows as a broken line + gap band, never interpolated;
 *      keys absent → NOT PUBLISHED; stale/frozen value cells name their
 *      age and reuse the FSM pill styling; all four published axes
 *      (roll, pitch, yaw, z_rate) are shown.
 *   C. Read-only — the new code contains no command/POST markers.
 *
 * Same fake-DOM + advanceable-clock pattern as overview_followons_harness.js.
 * Run:  node ground_station/service/tests/overview_experiment_harness.js
 * Exit code 0 iff every check passes.
 */
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const assert = require('assert');

const PANEL = path.join(__dirname, '..', '..', '..',
  'docs', 'dashboard-platform', 'shell', 'plugins', 'overview-panel.js');

// ── Fake DOM (same shape as overview_followons_harness.js) ───────────────
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
    this.classList = {
      _classes: new Set(),
      add: (c) => this.classList._classes.add(c),
      remove: (c) => this.classList._classes.delete(c),
      contains: (c) => this.classList._classes.has(c),
    };
    this.handlers = {};
    this.style = {};
    this.dataset = {};
    this.doc = null;
  }
  addEventListener(ev, fn) { (this.handlers[ev] = this.handlers[ev] || []).push(fn); }
  getAttribute(name) { return this.dataset[name] !== undefined ? this.dataset[name] : null; }
  setAttribute(name, val) { this.dataset[name] = String(val); }
  get innerHTML() { return this._html; }
  set innerHTML(html) {
    this._html = html;
    if (this.doc) this.doc.scan(html);
  }
}
class FakeDocument {
  constructor() { this.elements = {}; }
  scan(html) {
    const tags = html.match(/<[a-zA-Z0-9-]+[^>]*>/g) || [];
    for (const tag of tags) {
      const idm = tag.match(/\bid="([^"]+)"/);
      if (!idm) continue;
      const id = idm[1];
      if (!this.elements[id]) {
        const el = new Element(id, tag.slice(1).split(/[\s>]/)[0]);
        el.doc = this;
        this.elements[id] = el;
      }
      const textMatch = html.match(new RegExp('<[a-zA-Z0-9-]+[^>]*\\bid="' + id + '"[^>]*>([^<]*)<'));
      if (textMatch) {
        this.elements[id].textContent = textMatch[1];
      }
    }
  }
  getElementById(id) { return this.elements[id] || null; }
}

// ── Stubbed shell API ──────────────────────────────────────────────────────
function makeApi() {
  return {
    stateCb: null,
    subscribe(cb) { this.stateCb = cb; },
    registerPanel(name, renderFn) { this.renderFn = renderFn; },
  };
}

let clockOverride = null;
const NOW = () => (clockOverride == null ? Date.now() : clockOverride);
const nsAgo = (ms) => (NOW() - ms) * 1e6;

function loadPanel(withClock) {
  const doc = new FakeDocument();
  const container = new Element('container', 'div');
  container.doc = doc;
  const api = makeApi();
  clockOverride = withClock ? Date.now() : null;
  class SandboxDate extends Date {
    constructor(...args) { if (args.length) super(...args); else super(NOW()); }
    static now() { return NOW(); }
  }
  const sandbox = {
    document: doc,
    console: { log() {}, warn() {}, error() {} },
    Date: withClock ? SandboxDate : Date,
    Math, Number, String, Boolean, Array, Object, JSON,
    setInterval, clearInterval, setTimeout, clearTimeout,
    module: { exports: {} },
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
    sandbox, doc, api,
    feed(state) { api.stateCb(state); },
    advance(ms) { clockOverride += ms; },
    destroy() { sandbox.pluginDestroy(); },
  };
}

/* State carrying the FSM keys in slot 0 (raw subscribe passthrough). */
function fsmState(s, ph) {
  const values = {};
  if (s != null) values['s_state'] = s;
  if (ph != null) values['flight_phase'] = ph;
  return {
    connected: true,
    slot_freshness_ttl_ns: 30e9,
    streams: {
      '0': { last_update_ns: nsAgo(100), values: values },
    },
  };
}

/* Frame-B-style payload: dotted theta keys on slot 1 (tag b → slot 1).
 * theta1Override lets a feed omit/replace theta_1 (for gaps). */
function weightsState(theta1Override) {
  const values = {};
  for (let n = 0; n < 6; n++) {
    if (n === 1 && theta1Override !== undefined) {
      if (theta1Override != null) values['mrac.roll.theta_1'] = theta1Override;
      continue;
    }
    values['mrac.roll.theta_' + n] = (n === 1) ? theta1Override : 0.01;
  }
  return {
    connected: true,
    slot_freshness_ttl_ns: 30e9,
    streams: {
      '0': { last_update_ns: nsAgo(100), values: { 'status.arm': 0 } },
      '1': { last_update_ns: nsAgo(100), values: values },
    },
  };
}

/* theta_1 value at snapshot i for the converging series: big 0.008 steps
 * for the first 7 moves, 0.0002 steps after — the transient decays. */
function convergingTheta(i) {
  let v = 0.02;
  for (let k = 0; k < i; k++) v += (k < 7) ? 0.008 : 0.0002;
  return v;
}

function runChecks() {
  console.log('--- OVERVIEW EXPERIMENT WIDGETS (FSM + MRAC) OFFLINE CHECKS ---');

  // A1. FSM — known state/phase: highlight + dwell, then last transition
  {
    console.log('\n[CHECK A1: FSM — known state highlights, dwell and last transition]');
    const env = loadPanel(true);
    env.feed(fsmState(0, 0));
    env.advance(500);
    env.feed(fsmState(0, 0));
    const doc = env.doc;

    let pill = doc.getElementById('ov-fsm-state-0');
    assert.strictEqual(pill.className, 'ov-fsm-state ov-fsm-cur-ok',
      'DISARMED pill must be highlighted live');
    assert.strictEqual(doc.getElementById('ov-fsm-state-1').className, 'ov-fsm-state');
    assert.strictEqual(doc.getElementById('ov-fph-state-0').className,
      'ov-fsm-state ov-fsm-cur-ok', 'GROUND_IDLE phase highlighted');
    let meta = doc.getElementById('ov-fsm-meta').innerHTML;
    assert.ok(meta.indexOf('LIVE') !== -1 && meta.indexOf('dwell') !== -1,
      'meta shows LIVE + dwell, got: ' + meta);
    assert.ok(meta.indexOf('since first packet') !== -1);
    console.log('  PASS: state DISARMED / phase GROUND_IDLE highlighted, meta "' +
      meta.replace(/<[^>]+>/g, '').trim() + '"');

    // Transition: DISARMED → ARMED, phase → FLYING.
    env.advance(500);
    env.feed(fsmState(1, 1));
    assert.strictEqual(doc.getElementById('ov-fsm-state-1').className,
      'ov-fsm-state ov-fsm-cur-ok', 'ARMED now highlighted');
    assert.strictEqual(doc.getElementById('ov-fsm-state-0').className, 'ov-fsm-state');
    assert.strictEqual(doc.getElementById('ov-fph-state-1').className,
      'ov-fsm-state ov-fsm-cur-ok', 'FLYING now highlighted');
    meta = doc.getElementById('ov-fsm-meta').innerHTML;
    assert.ok(meta.indexOf('DISARMED → ARMED') !== -1, 'last transition named, got: ' + meta);
    assert.ok(meta.indexOf('dwell 0 ms') !== -1, 'dwell reset at the transition, got: ' + meta);
    console.log('  PASS: transition → ARMED/FLYING; meta "' +
      meta.replace(/<[^>]+>/g, '').trim() + '"');
    env.destroy();
  }

  // A2. FSM — keys absent: NOT PUBLISHED, no default state highlighted
  {
    console.log('\n[CHECK A2: FSM — keys absent → NOT PUBLISHED, no default highlight]');
    const env = loadPanel();
    env.feed({ streams: { '0': { last_update_ns: nsAgo(100), values: { 'status.arm': 1 } } } });
    const doc = env.doc;

    for (const v of [0, 1, 2]) {
      assert.strictEqual(doc.getElementById('ov-fsm-state-' + v).className, 'ov-fsm-state',
        'no state pill may be highlighted without the key');
      assert.strictEqual(doc.getElementById('ov-fph-state-' + v).className, 'ov-fsm-state');
    }
    assert.strictEqual(doc.getElementById('ov-fph-state-3').className, 'ov-fsm-state');
    let meta = doc.getElementById('ov-fsm-meta').innerHTML;
    assert.ok(meta.indexOf('NOT PUBLISHED') !== -1, 'state meta NOT PUBLISHED: ' + meta);
    meta = doc.getElementById('ov-fph-meta').innerHTML;
    assert.ok(meta.indexOf('NOT PUBLISHED') !== -1, 'phase meta NOT PUBLISHED: ' + meta);
    console.log('  PASS: both metas read "NOT PUBLISHED — not published by this build"; zero highlighted pills');
    env.destroy();
  }

  // A3. FSM — stale key: amber STALE with age, not a confident LIVE state
  {
    console.log('\n[CHECK A3: FSM — stale key: amber STALE with age]');
    const env = loadPanel();
    const st = fsmState(1, 1);
    st.streams['0'].last_update_ns = nsAgo(3000);
    env.feed(st);
    const doc = env.doc;

    assert.strictEqual(doc.getElementById('ov-fsm-state-1').className,
      'ov-fsm-state ov-fsm-cur-warn', 'current pill amber when stale');
    const meta = doc.getElementById('ov-fsm-meta').innerHTML;
    assert.ok(meta.indexOf('STALE') !== -1 && meta.indexOf('age 3.') !== -1,
      'meta shows STALE + age, got: ' + meta);
    console.log('  PASS: 3 s old → ARMED pill ov-fsm-cur-warn, meta "' +
      meta.replace(/<[^>]+>/g, '').trim() + '"');
    env.destroy();
  }

  // A4. FSM — unrecognized prior value in the transition: numeric, never "null →"
  {
    console.log('\n[CHECK A4: FSM — unrecognized prior value falls back to numeric]');
    const env = loadPanel(true);
    env.feed(fsmState(99, 0));   // 99 not in API/flight_fsm.h enum
    env.advance(500);
    env.feed(fsmState(1, 1));    // transition 99 → ARMED
    const doc = env.doc;

    const meta = doc.getElementById('ov-fsm-meta').innerHTML;
    assert.ok(meta.indexOf('99 → ARMED') !== -1,
      'unknown prior enum rendered numerically, got: ' + meta);
    assert.strictEqual(meta.indexOf('null →'), -1,
      'transition line must never contain "null →", got: ' + meta);
    console.log('  PASS: transition "' +
      meta.replace(/<[^>]+>/g, '').match(/last transition:[^·]*/)[0].trim() +
      '" — numeric fallback, no "null →"');
    env.destroy();
  }

  // B1. Adaptation — converging series: verdict + numeric evidence
  {
    console.log('\n[CHECK B1: MRAC — converging series shows verdict and evidence]');
    const env = loadPanel(true);
    for (let i = 0; i < 16; i++) {
      env.feed(weightsState(convergingTheta(i)));
      env.advance(500);
    }
    const doc = env.doc;

    const verdict = doc.getElementById('ov-adapt-verdict-roll');
    assert.strictEqual(verdict.textContent, 'CONVERGING');
    assert.strictEqual(verdict.className, 'ov-adapt-verdict ov-adapt-converging');
    const ev = doc.getElementById('ov-adapt-ev-roll').textContent;
    assert.ok(ev.indexOf('window 20 snapshots (16 used)') !== -1, 'window/snapshot evidence: ' + ev);
    assert.ok(ev.indexOf('early Δ') !== -1 && ev.indexOf('late Δ') !== -1,
      'step evidence shown: ' + ev);
    assert.ok(ev.indexOf('travel') !== -1, 'travel shown: ' + ev);
    console.log('  PASS: verdict CONVERGING; ' + ev);

    const val = doc.getElementById('ov-weight-val-roll-1').textContent;
    assert.ok(/^\+0\.\d{4}$/.test(val), 'current theta_1 value shown: ' + val);
    const plot = doc.getElementById('ov-weight-plot-roll-1').innerHTML;
    assert.ok(plot.indexOf('ov-weight-spark') !== -1 && plot.indexOf('ov-spark-line') !== -1,
      'weight sparkline drawn from reused machinery');
    console.log('  PASS: current theta_1 ' + val + ' with session sparkline');
    env.destroy();
  }

  // B2. Adaptation — too little history: UNKNOWN, no verdict guessed
  {
    console.log('\n[CHECK B2: MRAC — short series renders UNKNOWN]');
    const env = loadPanel(true);
    for (let i = 0; i < 4; i++) {
      env.feed(weightsState(convergingTheta(i)));
      env.advance(500);
    }
    const doc = env.doc;

    const verdict = doc.getElementById('ov-adapt-verdict-roll');
    assert.strictEqual(verdict.textContent, 'UNKNOWN');
    assert.strictEqual(verdict.className, 'ov-adapt-verdict ov-adapt-unknown');
    const ev = doc.getElementById('ov-adapt-ev-roll').textContent;
    assert.ok(ev.indexOf('need ≥ 10 snapshots') !== -1, 'evidence states the gate: ' + ev);
    console.log('  PASS: verdict UNKNOWN; ' + ev);

    const plot = doc.getElementById('ov-weight-plot-roll-1').innerHTML;
    assert.ok(/4\/10 samples/.test(plot), 'cell shows honest 4/10 fill state: ' + plot);
    assert.strictEqual(plot.indexOf('<polyline'), -1, 'no line under the minimum');
    console.log('  PASS: weight cell reads "4/10 samples", no pseudo-line');
    env.destroy();
  }

  // B3. Adaptation — gap: line breaks, gap band shown, never interpolated
  {
    console.log('\n[CHECK B3: MRAC — gap shown as a broken line + gap band]');
    const env = loadPanel(true);
    for (let i = 0; i < 16; i++) {
      let t;
      if (i >= 5 && i <= 7) t = weightsState(null);           // theta_1 absent
      else t = weightsState(convergingTheta(i));
      env.feed(t);
      env.advance(500);
    }
    const doc = env.doc;

    const plot = doc.getElementById('ov-weight-plot-roll-1').innerHTML;
    assert.strictEqual((plot.match(/<polyline/g) || []).length, 2,
      'two segments — line BREAKS at the gap');
    assert.ok(plot.indexOf('ov-spark-gap') !== -1, 'gap band rendered: ' + plot);
    console.log('  PASS: 3-snapshot hole → 2 polyline segments + ov-spark-gap band; gap is visible, never bridged');

    // The gap also appears in the evidence basis: per-weight sample counts.
    const ev = doc.getElementById('ov-adapt-ev-roll').textContent;
    assert.ok(ev.indexOf('window 20 snapshots (16 used)') !== -1 &&
      ev.indexOf('13–16 samples/weight') !== -1,
      'gap visible as 13 vs 16 samples/weight: ' + ev);
    console.log('  PASS: evidence "' + ev + '" — the missing weight has 13 of 16 samples');
    env.destroy();
  }

  // B4. Adaptation — keys absent: NOT PUBLISHED
  {
    console.log('\n[CHECK B4: MRAC — keys absent → NOT PUBLISHED]');
    const env = loadPanel();
    env.feed({ streams: { '0': { last_update_ns: nsAgo(100), values: {} } } });
    const doc = env.doc;

    const verdict = doc.getElementById('ov-adapt-verdict-roll');
    assert.strictEqual(verdict.textContent, 'UNKNOWN');
    const ev = doc.getElementById('ov-adapt-ev-roll').textContent;
    assert.ok(ev.indexOf('0 of 6 weights published') !== -1, ev);
    const val = doc.getElementById('ov-weight-val-roll-1').textContent;
    assert.strictEqual(val, 'NOT PUBLISHED');
    console.log('  PASS: no theta keys → "UNKNOWN — only 0 of 6 weights published"; weight values NOT PUBLISHED');
    env.destroy();
  }

  // B5. Adaptation — stale / frozen weight values show age + FSM pill styling
  {
    console.log('\n[CHECK B5: MRAC — stale weight values show age and frozen styling]');

    // Stale (2–30 s): amber, age named.
    let env = loadPanel();
    let st = weightsState(0.01);
    st.streams['1'].last_update_ns = nsAgo(5000);
    env.feed(st);
    let cell = env.doc.getElementById('ov-weight-val-roll-1');
    assert.ok(/^\+0\.0100 · age 5\./.test(cell.textContent),
      'stale value keeps its value and names its age, got: ' + cell.textContent);
    assert.strictEqual(cell.className, 'ov-weight-val ov-fsm-cur-warn',
      'stale cell reuses the FSM stale pill style');
    console.log('  PASS: 5 s old → "' + cell.textContent + '" with ov-fsm-cur-warn');
    env.destroy();

    // Frozen past TTL (> 30 s): muted, frozen age named.
    env = loadPanel();
    st = weightsState(0.01);
    st.streams['1'].last_update_ns = nsAgo(40000);
    env.feed(st);
    cell = env.doc.getElementById('ov-weight-val-roll-1');
    assert.ok(/^\+0\.0100 · frozen 40/.test(cell.textContent),
      'frozen value names its age, got: ' + cell.textContent);
    assert.strictEqual(cell.className, 'ov-weight-val ov-fsm-cur-nodata',
      'frozen cell reuses the FSM frozen pill style');
    console.log('  PASS: 40 s old (past TTL) → "' + cell.textContent + '" with ov-fsm-cur-nodata');
    env.destroy();
  }

  // B6. Adaptation — four axes (roll, pitch, yaw, z_rate); unpublished → NOT PUBLISHED
  {
    console.log('\n[CHECK B6: MRAC — four adaptation axes: yaw and z rate]');

    // Build publishes raw slot-0 paths for yaw and z_rate (boot_default_layout.py).
    const raw = {};
    for (let n = 0; n < 6; n++) {
      raw['mrac_state.yaw.Theta[' + n + ']'] = 0.03;
      raw['mrac_state.z_rate.Theta[' + n + ']'] = 0.04;
    }
    let env = loadPanel();
    env.feed({ streams: { '0': { last_update_ns: nsAgo(100), values: raw } } });
    let yawCell = env.doc.getElementById('ov-weight-val-yaw-0');
    let zCell = env.doc.getElementById('ov-weight-val-z_rate-0');
    assert.ok(yawCell && /^\+0\.0300$/.test(yawCell.textContent),
      'yaw weight shown via raw alias, got: ' + (yawCell && yawCell.textContent));
    assert.ok(zCell && /^\+0\.0400$/.test(zCell.textContent),
      'z_rate weight shown via raw alias, got: ' + (zCell && zCell.textContent));
    assert.ok(env.doc.getElementById('ov-adapt-verdict-yaw') &&
      env.doc.getElementById('ov-adapt-verdict-z_rate'),
      'verdict boxes exist for all four axes');
    console.log('  PASS: yaw / z_rate weight cells and verdicts rendered; raw Theta paths resolve');
    env.destroy();

    // A build that omits an axis entirely: NOT PUBLISHED, never 0.
    env = loadPanel();
    env.feed({ streams: { '0': { last_update_ns: nsAgo(100), values: {} } } });
    yawCell = env.doc.getElementById('ov-weight-val-yaw-0');
    zCell = env.doc.getElementById('ov-weight-val-z_rate-0');
    assert.strictEqual(yawCell.textContent, 'NOT PUBLISHED');
    assert.strictEqual(zCell.textContent, 'NOT PUBLISHED');
    assert.strictEqual(yawCell.textContent.indexOf('0'), -1, 'no fake zero');
    console.log('  PASS: omitted yaw / z_rate axes read NOT PUBLISHED, never 0');
    env.destroy();
  }

  // C. Read-only — no command/POST path in the shipped source
  {
    console.log('\n[CHECK C: Read-Only — no send/arm/gate markers in source]');
    const src = fs.readFileSync(PANEL, 'utf8');
    for (const marker of ['submitCommand', 'subscribeSlot', 'unsubscribeSlot',
      'fetch(', 'XMLHttpRequest', 'api.command', 'POST']) {
      assert.ok(src.indexOf(marker) === -1, 'panel must not contain ' + marker);
    }
    console.log('  PASS: source contains none of submitCommand / subscribeSlot / fetch / XMLHttpRequest / POST');
  }

  console.log('\nALL CHECKS PASSED SUCCESSFULLY.');
}

try { runChecks(); } catch (e) { console.error(e); process.exit(1); }
