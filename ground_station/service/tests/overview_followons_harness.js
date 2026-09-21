'use strict';
/**
 * Offline verification harness for the overview-panel follow-ons
 * (task 20260921-065323): the attitude indicator (artificial horizon)
 * and the pre-flight checklist.
 *
 * Same fake-DOM + stubbed-shell-API pattern as overview_panel_harness.js.
 * Checks:
 *   1. Attitude indicator, live: horizon transform tracks roll/pitch and
 *      the numeric readouts carry units.
 *   2. Attitude indicator, keys ABSENT: instrument is visibly dead —
 *      grey NO DATA legend, horizon hidden — never a level horizon.
 *   3. Attitude indicator, keys STALE: amber age readout, then grey
 *      NO DATA with a frozen-age legend after the slot TTL.
 *   4. Checklist from real synthetic telemetry: every verdict is PASS /
 *      FAIL with the reason; violated conditions read FAIL, never PASS.
 *   5. Checklist with no data at all: every row reads UNKNOWN, none PASS.
 *
 * Run:  node ground_station/service/tests/overview_followons_harness.js
 * Exit code 0 iff every check passes.
 */
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const assert = require('assert');

const PANEL = path.join(__dirname, '..', '..', '..',
  'docs', 'dashboard-platform', 'shell', 'plugins', 'overview-panel.js');

// ── Fake DOM (same shape as overview_panel_harness.js) ─────────────────
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

// ── Stubbed shell API ─────────────────────────────────────────────────────
function makeApi() {
  return {
    stateCb: null,
    subscribe(cb) { this.stateCb = cb; },
    registerPanel(name, renderFn) { this.renderFn = renderFn; },
  };
}

function loadPanel(withClock) {
  const doc = new FakeDocument();
  const container = new Element('container', 'div');
  container.doc = doc;
  const api = makeApi();
  // Optional controllable clock: the session-history widgets (sparkline
  // span, battery slope) accumulate samples over TIME, which a test cannot
  // wait for. withClock freezes the panel's Date at an advanceable instant.
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
    sandbox, doc, api, container,
    feed(state) { api.stateCb(state); },
    advance(ms) { clockOverride += ms; },
    destroy() { sandbox.pluginDestroy(); },
  };
}

let clockOverride = null;
const NOW = () => (clockOverride == null ? Date.now() : clockOverride);
const nsAgo = (ms) => (NOW() - ms) * 1e6;

/* Flight-ready synthetic telemetry: every checklist condition met. */
function flightReadyState() {
  return {
    connected: true,
    slot_freshness_ttl_ns: 30e9,
    streams: {
      '0': {
        last_update_ns: nsAgo(100),
        values: {
          'status.arm': 0, 'status.flymode': 0, 'status.vbat': 16.2,
          'status.rc_authority': 1, 'status.sbus': 0, 'status.estimator_ready': 1,
          'c.gyro_x': 0.01, 'c.gyro_y': -0.02, 'c.gyro_z': 0.03,
          'c.roll': 2.5, 'c.pitch': -1.25, 'c.yaw': 180.0,
          'c.altitude': 1.2,
        },
      },
      '1': { last_update_ns: nsAgo(100), values: { 'pid.gyrox.FB': 1.0 } },
      '3': {
        last_update_ns: nsAgo(100),
        values: {},
      },
    },
  };
}

const CHECK_IDS = ['link', 'disarm', 'battery', 'imu', 'attitude', 'rc', 'estimator', 'faults'];

function verdicts(doc) {
  const out = [];
  for (let i = 0; i < CHECK_IDS.length; i++) {
    out.push({
      id: CHECK_IDS[i],
      v: doc.getElementById('ov-chk-v-' + i).textContent,
      cls: doc.getElementById('ov-chk-v-' + i).className,
      d: doc.getElementById('ov-chk-d-' + i).textContent,
    });
  }
  return out;
}

function runChecks() {
  console.log('--- OVERVIEW FOLLOW-ONS (ATTITUDE + CHECKLIST) OFFLINE CHECKS ---');

  // 1. Attitude indicator — live: tracks roll/pitch, readouts with units
  {
    console.log('\n[CHECK 1: Attitude Indicator — live, tracks roll/pitch]');
    const env = loadPanel();
    env.feed(flightReadyState());
    const doc = env.doc;

    const h = doc.getElementById('ov-ai-horizon');
    assert.strictEqual(h.getAttribute('transform'), 'rotate(-2.5 100 100) translate(0 -2.50)',
      'horizon must bank opposite roll and drop with pitch');
    assert.strictEqual(doc.getElementById('ov-ai-world').style.display, '');
    assert.strictEqual(doc.getElementById('ov-ai-dead').style.display, 'none');
    assert.strictEqual(doc.getElementById('ov-ai-flag').textContent, 'LIVE');
    assert.strictEqual(doc.getElementById('ov-ai-box').className, 'ov-ai-box ov-ai-box-ok');
    assert.strictEqual(doc.getElementById('ov-ai-ro-0').textContent, '+2.5 deg');
    assert.strictEqual(doc.getElementById('ov-ai-ro-1').textContent, '-1.3 deg');
    assert.strictEqual(doc.getElementById('ov-ai-ro-2').textContent, '+180.0 deg');
    console.log('  PASS: roll 2.5° / pitch -1.25° → transform "' + h.getAttribute('transform') +
      '"; readouts +2.5 deg / -1.3 deg / +180.0 deg; flag LIVE');

    // It TRACKS: feed a new attitude, the horizon follows.
    const banked = flightReadyState();
    banked.streams['0'].values['c.roll'] = 30.0;
    banked.streams['0'].values['c.pitch'] = 10.0;
    env.feed(banked);
    assert.strictEqual(h.getAttribute('transform'), 'rotate(-30 100 100) translate(0 20.00)');
    assert.strictEqual(doc.getElementById('ov-ai-ro-0').textContent, '+30.0 deg');
    assert.strictEqual(doc.getElementById('ov-ai-ro-1').textContent, '+10.0 deg');
    console.log('  PASS: new attitude 30°/-10°... roll 30° pitch 10° → transform "' +
      h.getAttribute('transform') + '" — indicator tracks');
    env.destroy();
  }

  // 2. Attitude indicator — keys ABSENT: visibly dead, never level-looking
  {
    console.log('\n[CHECK 2: Attitude Indicator — keys absent → dead, NO DATA legend]');
    const env = loadPanel();
    // Slot 3 never arrives at all.
    env.feed({ streams: { '0': { last_update_ns: nsAgo(100),
      values: { 'status.arm': 0, 'status.vbat': 16.0 } } } });
    const doc = env.doc;
    assert.strictEqual(doc.getElementById('ov-ai-dead').style.display, '',
      'dead overlay must be visible');
    assert.strictEqual(doc.getElementById('ov-ai-world').style.display, 'none',
      'horizon must be hidden — no level horizon with no data');
    assert.strictEqual(doc.getElementById('ov-ai-flag').textContent, 'NO DATA');
    assert.strictEqual(doc.getElementById('ov-ai-box').className, 'ov-ai-box ov-ai-box-nodata');
    assert.strictEqual(doc.getElementById('ov-ai-ro-0').textContent, 'NO DATA');
    assert.strictEqual(doc.getElementById('ov-ai-ro-1').textContent, 'NO DATA');
    const sub1 = doc.getElementById('ov-ai-deadsub').textContent;
    assert.strictEqual(sub1, 'not published by this build');
    console.log('  PASS: stream absent → world hidden, grey "NO DATA" legend, sub "' + sub1 + '"');

    // Stream 3 arrives but the build does not publish c.roll / c.pitch.
    env.feed({ streams: { '3': { last_update_ns: nsAgo(100), values: { 'c.altitude': 1.0 } } } });
    assert.strictEqual(doc.getElementById('ov-ai-dead').style.display, '');
    assert.strictEqual(doc.getElementById('ov-ai-world').style.display, 'none');
    const sub2 = doc.getElementById('ov-ai-deadsub').textContent;
    assert.strictEqual(sub2, 'stream 0 not received');
    assert.strictEqual(doc.getElementById('ov-ai-ro-0').textContent, 'NO DATA');
    console.log('  PASS: key absent from live stream → dead overlay, sub "' + sub2 + '" — still no level horizon');
    env.destroy();
  }

  // 3. Attitude indicator — STALE: amber with age, then grey/frozen past TTL
  {
    console.log('\n[CHECK 3: Attitude Indicator — stale: amber age, then grey frozen]');
    const env = loadPanel();
    let st = flightReadyState();
    st.streams['0'].last_update_ns = nsAgo(3000);   // slot 0 stopped 3 s ago
    env.feed(st);
    const doc = env.doc;
    assert.strictEqual(doc.getElementById('ov-ai-flag').textContent, 'STALE');
    assert.strictEqual(doc.getElementById('ov-ai-box').className, 'ov-ai-box ov-ai-box-warn');
    const sub = doc.getElementById('ov-ai-sub').textContent;
    assert.ok(/^age 3\.\d s$/.test(sub), 'amber age readout, got "' + sub + '"');
    assert.strictEqual(doc.getElementById('ov-ai-world').style.display, '',
      'at warn the horizon may still show the last value');
    assert.strictEqual(doc.getElementById('ov-ai-ro-0').textContent, '+2.5 deg');
    console.log('  PASS: 3 s old → amber STALE "' + sub + '", readout amber-valued');

    st = flightReadyState();
    st.streams['0'].last_update_ns = nsAgo(35000);  // past the 30 s slot TTL
    env.feed(st);
    assert.strictEqual(doc.getElementById('ov-ai-flag').textContent, 'NO DATA');
    assert.strictEqual(doc.getElementById('ov-ai-dead').style.display, '');
    assert.strictEqual(doc.getElementById('ov-ai-world').style.display, 'none');
    const sub2 = doc.getElementById('ov-ai-deadsub').textContent;
    assert.ok(sub2.indexOf('stale 3') === 0, 'frozen age legend, got "' + sub2 + '"');
    assert.strictEqual(doc.getElementById('ov-ai-ro-0').textContent, 'NO DATA',
      'frozen value must not be displayed as live');
    console.log('  PASS: 35 s old → grey NO DATA, world hidden, legend "' + sub2 + '"');
    env.destroy();
  }

  // 4. Checklist — verdicts from real synthetic telemetry
  {
    console.log('\n[CHECK 4: Checklist — live verdicts from synthetic telemetry]');
    const env = loadPanel();
    env.feed(flightReadyState());
    const doc = env.doc;
    let vs = verdicts(doc);
    assert.strictEqual(vs.length, 8, 'eight checklist rows');
    for (const row of vs) {
      assert.strictEqual(row.v, 'PASS', row.id + ' should PASS on flight-ready telemetry');
      assert.strictEqual(row.cls, 'ov-chk-verdict ov-chk-pass');
      assert.ok(row.d.length > 0, row.id + ' must state why it passed');
    }
    console.log('  PASS: flight-ready telemetry → all 8 rows PASS, e.g.');
    console.log('    link      : ' + vs[0].d);
    console.log('    battery   : ' + vs[2].d);

    // Unsafe telemetry: armed, low battery, RC link lost, estimator down.
    const bad = flightReadyState();
    bad.streams['0'].values['status.arm'] = 1;
    bad.streams['0'].values['status.vbat'] = 14.2;
    bad.streams['0'].values['status.sbus'] = 1;
    bad.streams['0'].values['status.estimator_ready'] = 0;
    env.feed(bad);
    vs = verdicts(doc);
    const byId = {};
    vs.forEach((r) => { byId[r.id] = r; });
    assert.strictEqual(byId.disarm.v, 'FAIL');
    assert.ok(byId.disarm.d.indexOf('AIRCRAFT IS ARMED') !== -1);
    assert.strictEqual(byId.battery.v, 'FAIL');
    assert.ok(byId.battery.d.indexOf('below firmware beep threshold') !== -1);
    assert.strictEqual(byId.rc.v, 'FAIL');
    assert.ok(byId.rc.d.indexOf('RC RECEIVER LINK LOST') !== -1);
    assert.strictEqual(byId.estimator.v, 'FAIL');
    assert.strictEqual(byId.faults.v, 'FAIL');
    assert.ok(byId.faults.d.indexOf('RED:') === 0, 'faults row names the worst alarm');
    assert.strictEqual(byId.imu.v, 'PASS');
    assert.strictEqual(byId.attitude.v, 'PASS');
    assert.strictEqual(byId.link.v, 'PASS');
    console.log('  PASS: armed/low-bat/no-RC/no-estimator → those rows FAIL:');
    console.log('    disarm    : ' + byId.disarm.d);
    console.log('    battery   : ' + byId.battery.d);
    console.log('    rc        : ' + byId.rc.d);
    console.log('    faults    : ' + byId.faults.d);

    // Frame C dies while the link is up: IMU and attitude rows FAIL.
    const noFrameC = flightReadyState();
    delete noFrameC.streams['0'].values['c.gyro_x'];
    delete noFrameC.streams['0'].values['c.gyro_y'];
    delete noFrameC.streams['0'].values['c.gyro_z'];
    delete noFrameC.streams['0'].values['c.roll'];
    delete noFrameC.streams['0'].values['c.pitch'];
    delete noFrameC.streams['3'];
    env.feed(noFrameC);
    vs = verdicts(doc);
    vs.forEach((r) => { byId[r.id] = r; });
    assert.strictEqual(byId.imu.v, 'FAIL');
    assert.strictEqual(byId.attitude.v, 'FAIL');
    assert.ok(byId.imu.d.indexOf('Frame C (slot 3) not received') !== -1);
    console.log('  PASS: Frame C gone while link up → imu/attitude FAIL ("' + byId.imu.d + '")');
    env.destroy();
  }

  // 5. Checklist — no data at all: every row UNKNOWN, none PASS
  {
    console.log('\n[CHECK 5: Checklist — no data: all UNKNOWN, none PASS]');
    const env = loadPanel();
    env.feed({ streams: {} });
    const doc = env.doc;
    const vs = verdicts(doc);
    for (const row of vs) {
      assert.strictEqual(row.v, 'UNKNOWN', row.id + ' must be UNKNOWN with no data');
      assert.strictEqual(row.cls, 'ov-chk-verdict ov-chk-unknown');
      assert.notStrictEqual(row.cls, 'ov-chk-verdict ov-chk-pass');
      assert.ok(row.d.length > 0);
    }
    const aiFlag = doc.getElementById('ov-ai-flag').textContent;
    assert.strictEqual(aiFlag, 'NO DATA', 'attitude instrument dead too');
    console.log('  PASS: no telemetry → all 8 rows UNKNOWN (e.g. "' + vs[0].d + '"); no PASS anywhere');

    // Streams present but no packet yet: still UNKNOWN, never PASS.
    env.feed({ streams: { '0': { values: {} }, '3': { values: {} } } });
    const vs2 = verdicts(doc);
    for (const row of vs2) {
      assert.strictEqual(row.v, 'UNKNOWN', row.id + ' must stay UNKNOWN before any packet');
    }
    assert.strictEqual(vs2[0].d, 'subscribed but no packet yet');
    console.log('  PASS: subscribed-but-no-packet-yet → all rows UNKNOWN ("' + vs2[0].d + '")');
    env.destroy();
  }

  // 6. Alarm history — raise/clear episodes, acknowledge, silence, CSV export
  {
    console.log('\n[CHECK 6: Alarm History — episodes, ACK, SILENCE, CSV export]');
    const env = loadPanel();
    const doc = env.doc;
    const click = (el) => env.container.handlers['click'][0]({ target: el });

    // Raise one red alarm (battery low), then clear it.
    const low = flightReadyState();
    low.streams['0'].values['status.vbat'] = 14.2;
    env.feed(low);
    let hist = doc.getElementById('ov-hist-rows').innerHTML;
    assert.ok(hist.indexOf('RAISED') !== -1, 'raise must be logged');
    assert.ok(hist.indexOf('BATTERY LOW') !== -1, 'raise keeps the alarm text');
    assert.ok(/\d{2}:\d{2}:\d{2}/.test(hist), 'raise row carries a timestamp');
    assert.strictEqual((hist.match(/RAISED/g) || []).length, 1);
    assert.strictEqual((hist.match(/CLEARED/g) || []).length, 0, 'no clear yet');
    console.log('  PASS: raised red alarm → history row "RAISED … BATTERY LOW" with hh:mm:ss timestamp');

    env.feed(flightReadyState());   // battery back in range → clear
    hist = doc.getElementById('ov-hist-rows').innerHTML;
    assert.strictEqual((hist.match(/RAISED/g) || []).length, 1);
    assert.strictEqual((hist.match(/CLEARED/g) || []).length, 1, 'clear must be logged');
    const raisedTs = hist.match(/\d{2}:\d{2}:\d{2}/g);
    assert.strictEqual(raisedTs.length, 2, 'raised and cleared rows each carry a timestamp');
    assert.ok(hist.indexOf('ov-hist-ev-cleared') !== -1, 'cleared row is visibly distinct');
    console.log('  PASS: alarm raised then cleared → TWO entries, timestamps ' +
      raisedTs[0] + ' (RAISED) and ' + raisedTs[1] + ' (CLEARED)');

    // ACKNOWLEDGE: stays in the log, visibly distinct, display-local.
    const ackBtn = doc.getElementById('ov-ack-1');
    assert.ok(ackBtn, 'ack button exists for episode 1');
    click(ackBtn);
    hist = doc.getElementById('ov-hist-rows').innerHTML;
    assert.ok(hist.indexOf('BATTERY LOW') !== -1, 'acked alarm STAYS in the log');
    assert.ok(hist.indexOf('ov-hist-ep-acked') !== -1, 'acked episode is visibly distinct');
    assert.ok(hist.indexOf('>ACK<') !== -1, 'ACK tag shown');
    assert.strictEqual(doc.getElementById('ov-ack-1').textContent, 'ACKED');
    console.log('  PASS: ACK → episode stays in log with ACK tag and ov-hist-ep-acked styling');

    // SILENCE: suppresses the banner nag, never the record; reversible.
    env.feed(low);                   // re-raised → new episode (ref 2)
    assert.ok(doc.getElementById('ov-banner').textContent.indexOf('BATTERY LOW') !== -1,
      'banner shows the un-silenced alarm');
    click(doc.getElementById('ov-sil-2'));
    assert.ok(doc.getElementById('ov-banner').textContent.indexOf('SILENCED BY OPERATOR') !== -1,
      'silenced alarm no longer drives the banner');
    assert.ok(doc.getElementById('ov-alarm-list').innerHTML.indexOf('[SILENCED]') !== -1,
      'active list marks the alarm SILENCED');
    hist = doc.getElementById('ov-hist-rows').innerHTML;
    assert.strictEqual((hist.match(/RAISED/g) || []).length, 2,
      'the silenced alarm is STILL in the record');
    assert.ok(hist.indexOf('ov-hist-tag-sil') !== -1, 'silence tag on the episode');
    click(doc.getElementById('ov-sil-2'));   // un-silence
    assert.ok(doc.getElementById('ov-banner').textContent.indexOf('BATTERY LOW') !== -1,
      'un-silencing restores the banner nag');
    console.log('  PASS: SILENCE → banner nag suppressed, record kept, reversible');

    // CSV export: parseable, header + one row per episode.
    const csv = env.sandbox.module.exports.exportAlarmLogCsv();
    const lines = csv.split('\r\n').filter((l) => l.length > 0);
    assert.strictEqual(lines.length, 3, 'header + 2 episodes');
    const cells = (line) => line.match(/("([^"]|"")*"|[^,]*)/g)
      .filter((c) => c !== undefined && c !== '')
      .map((c) => c.replace(/^"|"$/g, '').replace(/""/g, '"'));
    const header = cells(lines[0]);
    assert.deepStrictEqual(header,
      ['raised_at', 'cleared_at', 'id', 'severity', 'text_raised', 'text_last', 'acknowledged', 'silenced']);
    const r1 = cells(lines[1]);
    assert.strictEqual(r1[2], 'vbat-low');
    assert.strictEqual(r1[3], 'red');
    assert.ok(r1[0].indexOf('T') !== -1, 'raised_at is an ISO timestamp');
    assert.ok(r1[1].length > 0, 'cleared_at set for the cleared episode');
    assert.strictEqual(r1[6], 'yes', 'episode 1 was acknowledged');
    const r2 = cells(lines[2]);
    assert.strictEqual(r2[1], '', 'episode 2 still open → empty cleared_at');
    console.log('  PASS: CSV export parses — ' + lines.length + ' lines; first lines:');
    console.log('    ' + lines[0]);
    console.log('    ' + lines[1]);
    console.log('    ' + lines[2]);
    env.destroy();
  }

  // 7. Trend-on-demand — sparkline, insufficient history, gap as a hole
  {
    console.log('\n[CHECK 7: Trend-on-Demand — sparkline / insufficient / gap]');
    // 7a. Full buffer → sparkline.
    let env = loadPanel(true);
    for (let i = 0; i < 12; i++) {
      const st = flightReadyState();
      st.streams['0'].values['c.altitude'] = 1.0 + i * 0.1;
      env.feed(st);
      env.advance(500);
    }
    env.container.handlers['click'][0]({ target: env.doc.getElementById('ov-val-pos-2') });
    let body = env.doc.getElementById('ov-trend-body').innerHTML;
    assert.ok(body.indexOf('<svg') !== -1, 'sparkline SVG rendered');
    assert.ok(body.indexOf('<polyline') !== -1);
    console.log('  PASS: 12 altitude samples → draws the sparkline SVG and line');

    env = loadPanel(true);
    for (let i = 0; i < 3; i++) { env.feed(flightReadyState()); env.advance(500); }
    env.container.handlers['click'][0]({ target: env.doc.getElementById('ov-val-att-0') });
    body = env.doc.getElementById('ov-trend-body').innerHTML;
    assert.ok(body.indexOf('INSUFFICIENT HISTORY') !== -1, 'explicit insufficient state');
    assert.strictEqual(body.indexOf('<svg'), -1, 'no sparkline drawn');
    assert.strictEqual(body.indexOf('<polyline'), -1, 'never a 3-point pseudo-line');
    console.log('  PASS: 3 samples only → "INSUFFICIENT HISTORY", skips the draw');

    env = loadPanel(true);
    for (let i = 0; i < 14; i++) {
      const st = flightReadyState();
      if (i >= 5 && i <= 7) delete st.streams['0'].values['c.yaw'];  // 3-sample hole
      else st.streams['0'].values['c.yaw'] = 170 + i;
      env.feed(st);
      env.advance(500);
    }
    env.container.handlers['click'][0]({ target: env.doc.getElementById('ov-val-att-2') });
    body = env.doc.getElementById('ov-trend-body').innerHTML;
    assert.strictEqual((body.match(/<polyline/g) || []).length, 2,
      'two segments — the line BREAKS at the gap');
    assert.ok(body.indexOf('ov-spark-gap') !== -1, 'the gap renders as a visible hole band');
    assert.ok(body.indexOf('1 gap(s)') !== -1, 'the gap is counted in the basis line');
    console.log('  PASS: 3-sample hole → line breaks into 2 segments + grey gap band ("gap(s)" counted)');
    env.destroy();
  }

  // 8. Battery trend — falling / flat / rising / absent / insufficient
  {
    console.log('\n[CHECK 8: Battery Trend — estimate, no-estimate, not-published]');
    const vbatState = (v) => ({
      connected: true,
      slot_freshness_ttl_ns: 30e9,
      streams: {
        '0': {
          last_update_ns: nsAgo(100),
          _key_ts: { 'status.vbat': nsAgo(100) },
          values: { 'status.arm': 0, 'status.flymode': 0, 'status.vbat': v,
            'status.rc_authority': 1, 'status.sbus': 0, 'status.estimator_ready': 1 },
        },
      },
    });
    const feedSeries = (env, fn, n, stepMs) => {
      for (let i = 0; i < n; i++) { env.feed(vbatState(fn(i))); env.advance(stepMs); }
    };

    // 8a. Falling slope → time-to-empty WITH its basis.
    let env = loadPanel(true);
    feedSeries(env, (i) => 16.4 - i * 0.025, 20, 6000);   // −0.25 V/min over 114 s
    let body = env.doc.getElementById('ov-bat-body').innerHTML;
    if (body.indexOf('TIME TO 15.0 V') === -1) console.log('CHECK 8A BODY WAS:', body);
    assert.ok(body.indexOf('TIME TO 15.0 V') !== -1, 'falling slope gives an estimate');
    assert.ok(/−?\d+\.\d+ mV\/min|-250\.0 mV\/min/.test(body), 'slope shown: ' + body.match(/-?[\d.]+ mV\/min/));
    assert.ok(body.indexOf('basis: 20 samples') !== -1, 'sample count in the basis');
    assert.ok(body.indexOf('min span') !== -1, 'time span in the basis');
    console.log('  PASS: −0.25 V/min → "' + body.replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').trim() + '"');
    env.destroy();

    // 8b. Flat slope → NO estimate, no comforting number.
    env = loadPanel(true);
    feedSeries(env, () => 16.0, 20, 6000);
    body = env.doc.getElementById('ov-bat-body').innerHTML;
    assert.ok(body.indexOf('NO ESTIMATE — voltage not falling') !== -1, 'flat gives no estimate');
    assert.strictEqual(body.indexOf('TIME TO'), -1);
    console.log('  PASS: flat 16.0 V → "NO ESTIMATE — voltage not falling" (slope +0.0 mV/min)');
    env.destroy();

    // 8c. Rising slope → still no estimate.
    env = loadPanel(true);
    feedSeries(env, (i) => 15.8 + i * 0.02, 20, 6000);
    body = env.doc.getElementById('ov-bat-body').innerHTML;
    assert.ok(body.indexOf('NO ESTIMATE') !== -1);
    assert.strictEqual(body.indexOf('TIME TO'), -1);
    console.log('  PASS: rising slope → NO ESTIMATE (never a large number)');
    env.destroy();

    // 8d. vbat not published → NOT PUBLISHED, no nominal substitute.
    env = loadPanel(true);
    const noVbat = flightReadyState();
    delete noVbat.streams['0'].values['status.vbat'];
    env.feed(noVbat);
    env.advance(6000);
    body = env.doc.getElementById('ov-bat-body').innerHTML;
    assert.ok(body.indexOf('NOT PUBLISHED') !== -1);
    assert.strictEqual(body.indexOf('TIME TO'), -1);
    console.log('  PASS: status.vbat absent → amber "NOT PUBLISHED", nothing substituted');
    env.destroy();

    // 8e. Published but too little history → INSUFFICIENT, no slope.
    env = loadPanel(true);
    feedSeries(env, (i) => 16.4 - i * 0.025, 4, 6000);
    body = env.doc.getElementById('ov-bat-body').innerHTML;
    assert.ok(body.indexOf('INSUFFICIENT HISTORY') !== -1);
    assert.strictEqual(body.indexOf('TIME TO'), -1);
    assert.strictEqual(body.indexOf('mV/min'), -1, 'no slope shown below the minimum');
    console.log('  PASS: 4 samples → "INSUFFICIENT HISTORY", no slope, no estimate');
    env.destroy();
  }

  // 9. Read-only confirmation — nothing sends, arms or gates
  {
    console.log('\n[CHECK 9: Read-Only — no widget sends, arms or gates anything]');
    const src = fs.readFileSync(PANEL, 'utf8');
    for (const marker of ['submitCommand', 'subscribeSlot', 'unsubscribeSlot',
      'fetch(', 'XMLHttpRequest', 'getGates', 'getArmState', 'api.command']) {
      assert.ok(src.indexOf(marker) === -1, 'panel must not contain ' + marker);
    }
    // And functionally: drive every interaction, assert the stubbed API saw
    // nothing beyond the one subscribe() from panel init.
    const env = loadPanel();
    const low = flightReadyState();
    low.streams['0'].values['status.vbat'] = 14.2;
    env.feed(low);
    const click = (el) => env.container.handlers['click'][0]({ target: el });
    click(env.doc.getElementById('ov-ack-1'));
    click(env.doc.getElementById('ov-sil-1'));
    click(env.doc.getElementById('ov-val-att-0'));
    click(env.doc.getElementById('ov-export-btn'));
    assert.strictEqual(typeof env.api.stateCb, 'function', 'still just the state subscription');
    assert.strictEqual(env.api.submitCommand, undefined);
    assert.strictEqual(env.api.subscribeSlot, undefined);
    assert.deepStrictEqual(Object.keys(env.api).sort(), ['registerPanel', 'renderFn', 'stateCb', 'subscribe']);
    console.log('  PASS: source has no command/arm/gate call; ACK/SILENCE/trend/export clicks ' +
      'leave the API untouched — acknowledge and silence are display-local');
    env.destroy();
  }

  console.log('\nALL CHECKS PASSED SUCCESSFULLY.');
}

try { runChecks(); } catch (e) { console.error(e); process.exit(1); }
// Panel timers keep the event loop alive; exit once every check has run.
process.exit(0);
