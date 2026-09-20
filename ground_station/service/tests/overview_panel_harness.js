'use strict';
/**
 * Offline verification harness for overview-panel.js (task 20260921-044235).
 *
 * Drives the PLC-style HMI overview panel in a fake DOM with a stubbed shell
 * API, the same way time_series_panel_harness.js does. Checks:
 *   1. Live render: every stage shows the fed values, with units.
 *   2. Degradation: a missing stage renders grey "NO DATA", never a zero.
 *   3. Staleness: a value that stops updating shows its age (amber) and then
 *      goes grey (NO DATA) after the slot TTL — and faults localise to the
 *      affected stage only.
 *   4. Alarm: battery low drives the red banner; a cleared alarm stays
 *      visible as "RECENT (cleared … ago)". Packet loss drives a red stage.
 *   5. No synthetic demo data in the shipped panel.
 *
 * Run:  node ground_station/service/tests/overview_panel_harness.js
 * Exit code 0 iff every check passes.
 */
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const assert = require('assert');

const PANEL = path.join(__dirname, '..', '..', '..',
  'docs', 'dashboard-platform', 'shell', 'plugins', 'overview-panel.js');

// ── Fake DOM (same shape as time_series_panel_harness.js) ─────────────────
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
      if (!this.elements[idm[1]]) {
        const el = new Element(idm[1], tag.slice(1).split(/[\s>]/)[0]);
        el.doc = this;
        this.elements[idm[1]] = el;
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

function loadPanel() {
  const doc = new FakeDocument();
  const container = new Element('container', 'div');
  container.doc = doc;
  const api = makeApi();
  const sandbox = {
    document: doc,
    console: { log() {}, warn() {}, error() {} },
    Date, Math, Number, String, Boolean, Array, Object, JSON,
    setInterval, clearInterval, setTimeout, clearTimeout,
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
    destroy() { sandbox.pluginDestroy(); },
  };
}

const NOW = () => Date.now();
const nsAgo = (ms) => (NOW() - ms) * 1e6;

function fullState() {
  return {
    connected: true,
    slot_freshness_ttl_ns: 30e9,
    streams: {
      '0': {
        last_update_ns: nsAgo(100),
        values: {
          'status.arm': 1, 'status.flymode': 1, 'status.vbat': 16.2,
          'status.rc_authority': 1, 'status.sbus': 0, 'status.estimator_ready': 1,
          'mrac.roll.u_ad': 0.0123, 'mrac.roll.e': -0.0456,
          'mrac.pitch.u_ad': -0.021, 'mrac.pitch.e': 0.033,
          'ekf.vel_x': 1.5, 'ekf.vel_y': -0.5, 'ekf.vel_z': 0.1,
          'ekf.bias_gyro_x': 0.0001, 'ekf.bias_gyro_y': -0.0002, 'ekf.bias_gyro_z': 0.0003,
        },
      },
      '1': {
        last_update_ns: nsAgo(100),
        values: {
          'pid.gyrox.FB': 12.3, 'pid.gyroy.FB': -45.6, 'pid.gyroz.FB': 7.8,
          'pid.gyrox.U': 0.123, 'pid.gyroy.U': -0.234, 'pid.gyroz.U': 0.345,
        },
      },
      '3': {
        last_update_ns: nsAgo(100),
        values: {
          'c.gyro_x': 0.12, 'c.gyro_y': -0.34, 'c.gyro_z': 0.56,
          'c.roll': 2.5, 'c.pitch': -1.25, 'c.yaw': 180.0,
          'c.earth_x': 12.34, 'c.earth_y': -5.67, 'c.altitude': 2.75,
          'motor.rpm_0': 5400, 'motor.rpm_1': 5500, 'motor.rpm_2': 5600, 'motor.rpm_3': 5700,
        },
      },
    },
  };
}

function runChecks() {
  console.log('--- RUNNING OVERVIEW PANEL (PLC-STYLE HMI) OFFLINE CHECKS ---');

  // 1. Live render with known values
  {
    console.log('\n[CHECK 1: Live Render — fed values with units]');
    const env = loadPanel();
    env.feed(fullState());
    const doc = env.doc;

    assert.strictEqual(doc.getElementById('ov-arm').textContent, 'ARMED');
    assert.strictEqual(doc.getElementById('ov-flymode').textContent, 'AltHold');
    assert.strictEqual(doc.getElementById('ov-vbat').textContent, '16.20 V');
    console.log('  PASS: strip shows ARMED / AltHold / 16.20 V');

    const expect = [
      ['ov-val-imu-0', '+0.12 rad/s'],   ['ov-val-imu-1', '-0.34 rad/s'],
      ['ov-val-filter-0', '+12.3 deg/s'], ['ov-val-filter-1', '-45.6 deg/s'],
      ['ov-val-att-0', '+2.5 deg'],      ['ov-val-att-1', '-1.3 deg'],
      ['ov-val-pos-0', '+12.34 m'],     ['ov-val-pos-2', '2.75 m'],
      ['ov-val-rctrl-0', '+0.123 cmd'],  ['ov-val-rctrl-1', '-0.234 cmd'],
      ['ov-val-mrac-0', '+0.012 cmd'],   ['ov-val-mrac-1', '-0.05 rad/s'],
      ['ov-val-motors-0', '5400 rpm'],   ['ov-val-motors-3', '5700 rpm'],
      ['ov-shadow-val-0', '+1.50 m/s'],  ['ov-shadow-val-3', '+0.0001 rad/s'],
    ];
    for (const [id, want] of expect) {
      const got = doc.getElementById(id).textContent;
      assert.strictEqual(got, want, `${id}: expected "${want}", got "${got}"`);
    }
    console.log('  PASS: all ' + expect.length + ' diagram values render exactly as fed, with units');

    for (const st of ['imu', 'filter', 'att', 'pos', 'rctrl', 'mrac', 'motors']) {
      assert.strictEqual(doc.getElementById('ov-stage-' + st).className, 'ov-stage ov-stage-ok',
        'stage ' + st + ' should be green/ok');
      assert.strictEqual(doc.getElementById('ov-flag-' + st).textContent, 'LIVE');
    }
    assert.strictEqual(doc.getElementById('ov-banner').textContent,
      'SYSTEM NORMAL — no active alarms');
    console.log('  PASS: every stage green LIVE; banner SYSTEM NORMAL');
    env.destroy();
  }

  // 2. Degradation: whole stage missing → grey NO DATA, never a zero
  {
    console.log('\n[CHECK 2: Degradation — missing stage is grey "NO DATA", not 0]');
    const env = loadPanel();
    // Only slot 0 arrives: sensor/attitude/controller stages have no stream.
    env.feed({ streams: { '0': { last_update_ns: nsAgo(100), values: { 'status.arm': 0, 'status.vbat': 16.0 } } } });
    const doc = env.doc;

    for (const st of ['imu', 'filter', 'att', 'pos', 'rctrl', 'motors']) {
      const box = doc.getElementById('ov-stage-' + st);
      assert.strictEqual(box.className, 'ov-stage ov-stage-nodata',
        'stage ' + st + ' must be grey/nodata');
      assert.strictEqual(doc.getElementById('ov-flag-' + st).textContent, 'NO DATA');
    }
    const v = doc.getElementById('ov-val-imu-0').textContent;
    assert.notStrictEqual(v, '0'); assert.notStrictEqual(v, '0.00 rad/s'); assert.notStrictEqual(v, '—');
    assert.strictEqual(v, 'NO DATA');
    assert.strictEqual(doc.getElementById('ov-sub-imu').textContent, 'stream 3 not received');
    console.log('  PASS: absent stages render grey "NO DATA / stream N not received" — no zeros, no blanks');

    // Key absent while slot present → "not published by this build"
    env.feed({ streams: { '3': { last_update_ns: nsAgo(100), values: { 'c.roll': 1.0 } } } });
    assert.strictEqual(doc.getElementById('ov-sub-motors').textContent, 'not published by this build');
    assert.strictEqual(doc.getElementById('ov-val-motors-0').textContent, 'NO DATA');
    console.log('  PASS: present stream without the key says "not published by this build"');
    env.destroy();
  }

  // 3. Staleness: age shown amber, then grey after TTL; fault localises
  {
    console.log('\n[CHECK 3: Staleness — age readout, then grey]');
    const env = loadPanel();
    let st = fullState();
    st.streams['3'].last_update_ns = nsAgo(3000);   // slot 3 stopped 3 s ago
    env.feed(st);
    const doc = env.doc;

    const att = doc.getElementById('ov-stage-att');
    assert.strictEqual(att.className, 'ov-stage ov-stage-warn', 'stale stage must be amber');
    assert.strictEqual(doc.getElementById('ov-flag-att').textContent, 'STALE');
    const sub = doc.getElementById('ov-sub-att').textContent;
    assert.ok(/^age 3\.\d s$/.test(sub), 'age readout must be shown, got "' + sub + '"');
    assert.strictEqual(doc.getElementById('ov-stage-mrac').className, 'ov-stage ov-stage-ok',
      'slot-0 stage stays green — fault localises to slot 3 only');
    assert.ok(doc.getElementById('ov-banner').textContent.indexOf('Telemetry slow') !== -1,
      'banner raises the staleness warning');
    console.log('  PASS: stale stage amber with readout "' + sub + '"; fresh stages stay green');

    st = fullState();
    st.streams['3'].last_update_ns = nsAgo(35000);  // past the 30 s slot TTL
    env.feed(st);
    assert.strictEqual(doc.getElementById('ov-stage-att').className, 'ov-stage ov-stage-nodata',
      'past TTL the stage must go grey');
    const sub2 = doc.getElementById('ov-sub-att').textContent;
    assert.ok(sub2.indexOf('stale 3') === 0, 'grey sub shows frozen age, got "' + sub2 + '"');
    assert.strictEqual(doc.getElementById('ov-val-att-0').textContent, 'NO DATA',
      'frozen last value must NOT be displayed as live');
    console.log('  PASS: after TTL the stage is grey "NO DATA" (' + sub2 + ') — frozen value suppressed');
    env.destroy();
  }

  // 4. Alarms: red banner on battery low; cleared alarm stays visible as recent
  {
    console.log('\n[CHECK 4: Alarm Banner, List and Recent-Cleared]');
    const env = loadPanel();
    let st = fullState();
    st.streams['0'].values['status.vbat'] = 14.2;
    env.feed(st);
    const doc = env.doc;

    const banner = doc.getElementById('ov-banner');
    assert.strictEqual(banner.className, 'ov-banner ov-banner-alarm', 'banner must be red');
    assert.ok(banner.textContent.indexOf('BATTERY LOW') !== -1, 'banner names the condition');
    assert.ok(banner.textContent.indexOf('14.20 V') !== -1, 'banner shows the value with unit');
    const list = doc.getElementById('ov-alarm-list').innerHTML;
    assert.ok(list.indexOf('ov-alarm-red') !== -1, 'alarm list contains the red entry');
    assert.strictEqual(doc.getElementById('ov-vbat').style.color, 'var(--red)');
    console.log('  PASS: banner red "' + banner.textContent + '"');

    // Condition clears → banner green, but the alarm stays visible as recent
    st = fullState();
    st.streams['0'].values['status.vbat'] = 16.0;
    env.feed(st);
    assert.strictEqual(doc.getElementById('ov-banner').textContent, 'SYSTEM NORMAL — no active alarms');
    const list2 = doc.getElementById('ov-alarm-list').innerHTML;
    assert.ok(list2.indexOf('RECENT (cleared') !== -1,
      'cleared alarm must remain visible as RECENT');
    assert.ok(list2.indexOf('BATTERY LOW') !== -1, 'recent entry keeps the alarm text');
    console.log('  PASS: cleared alarm remains listed as "RECENT (cleared … ago): BATTERY LOW …"');

    // Packet loss > 5% → red stage + red banner
    let st2 = fullState();
    st2.streams['3'].loss_pct = 6.0;
    env.feed(st2);
    assert.strictEqual(doc.getElementById('ov-stage-motors').className, 'ov-stage ov-stage-alarm',
      'loss > 5% must mark the stage red');
    assert.strictEqual(doc.getElementById('ov-flag-motors').textContent, 'LOSS 6.0%');
    assert.ok(doc.getElementById('ov-banner').textContent.indexOf('Packet loss 6.0%') !== -1);
    console.log('  PASS: loss 6.0% → stage red "LOSS 6.0%", banner "Packet loss 6.0% on stream 3 …"');
    env.destroy();
  }

  // 5. No synthetic data
  {
    console.log('\n[CHECK 5: No Synthetic Data]');
    const src = fs.readFileSync(PANEL, 'utf8');
    for (const marker of ['Math.sin(', 'Math.random(', 'generateDemo', 'DEMO_', 'fakeValue']) {
      assert.ok(src.indexOf(marker) === -1, 'panel must not contain ' + marker);
    }
    console.log('  PASS: no demo/placeholder/synthetic generators in the shipped panel');
  }

  console.log('\nALL CHECKS PASSED SUCCESSFULLY.');
}

try { runChecks(); } catch (e) { console.error(e); process.exit(1); }
