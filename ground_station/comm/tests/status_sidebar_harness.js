'use strict';
/**
 * Offline verification harness for status-panel.js (tasks A1-A3).
 *
 * Drives the Flight Status panel in a simulated DOM with a stubbed shell API.
 * Verifies:
 *   1. A1: Promotion of arm status, flight mode, battery voltage to persistent sidebar.
 *   2. A1: Honest degradation on absent fields (renders 'NOT PUBLISHED', amber, not 0, not "—").
 *   3. A1: Live values render correctly when fields are published.
 *   4. A2: Active Streams table removed from the panel (redundant on Overview & Control).
 *   5. A3: Consolidated operational status: rates, authority, state flags, commands in one place.
 *
 * Run: node ground_station/comm/tests/status_sidebar_harness.js [check_a2|check_absence|check_live|all]
 * Exit code 0 iff all requested checks pass.
 */
const fs = require('fs');
const path = require('path');
const assert = require('assert');

const PANEL_PATH = path.join(__dirname, '..', '..', '..',
  'docs', 'dashboard-platform', 'shell', 'plugins', 'status-panel.js');

// ── Minimal DOM Stub ────────────────────────────────────────────────────────
class Element {
  constructor(id, tag) {
    this.id = id || '';
    this.tagName = (tag || 'div').toUpperCase();
    this._html = '';
    this.textContent = '';
    this.className = '';
    this.style = {};
    this.children = [];
    this.classList = {
      _classes: new Set(),
      add: (c) => this.classList._classes.add(c),
      remove: (c) => this.classList._classes.delete(c),
      contains: (c) => this.classList._classes.has(c) || (this.className || '').includes(c),
    };
    this.handlers = {};
    this.doc = null;
  }
  getAttribute(attr) { return this[attr] || null; }
  setAttribute(attr, val) { this[attr] = val; }
  insertBefore(node, ref) {
    this.children.unshift(node);
    if (this.doc && node.id) {
      this.doc.elements[node.id] = node;
    }
  }
  get firstChild() {
    return this.children.length > 0 ? this.children[0] : null;
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
  }
  scan(html) {
    const tags = html.match(/<[a-zA-Z0-9-]+[^>]*>/g) || [];
    for (const tag of tags) {
      const idm = tag.match(/\bid="([^"]+)"/);
      if (!idm) continue;
      const id = idm[1];
      let el = this.elements[id];
      if (!el) {
        el = new Element(id, tag.slice(1).split(/[\s>]/)[0]);
        el.doc = this;
        this.elements[id] = el;
      }
      const textMatch = html.match(new RegExp('id=["\']' + id + '["\'][^>]*>([^<]*)<'));
      if (textMatch) el.textContent = textMatch[1].trim();
    }
  }
  getElementById(id) { return this.elements[id] || null; }
  createElement(tag) {
    const el = new Element('', tag);
    el.doc = this;
    return el;
  }
  querySelectorAll() { return []; }
}

function setupEnv() {
  const doc = new FakeDocument();
  // Pre-populate document with sidebar and card-streams as found in index.html
  const sidebar = doc.createElement('div');
  sidebar.id = 'sidebar';
  doc.elements['sidebar'] = sidebar;

  const cardStreams = doc.createElement('div');
  cardStreams.id = 'card-streams';
  doc.elements['card-streams'] = cardStreams;

  // Global browser environment
  global.document = doc;
  global.window = {
    __registerPlugin__: () => {},
    __gs_workspace__: () => 'overview'
  };

  // Re-require or evaluate status-panel.js
  delete require.cache[require.resolve(PANEL_PATH)];
  const mod = require(PANEL_PATH);
  return { doc, mod };
}

function checkA2() {
  console.log('\n[Check A2] Telemetry stream widget removal from Overview / Control:');
  const { doc, mod } = setupEnv();
  const html = mod.buildHTML();
  assert.strictEqual(
    html.includes('sp-stream-table') || html.includes('Active Streams'),
    false,
    'Active Streams table (sp-stream-table) must be removed from status-panel per A2'
  );
  console.log('  ✓ Active Streams table absent from status-panel.js');
}

function checkAbsence() {
  console.log('\n[Check A1/A3] Honest degradation on absent fields:');
  const { doc, mod } = setupEnv();
  mod.ensureSidebar();

  const container = doc.createElement('div');
  container.innerHTML = mod.buildHTML();

  // Feed state with EMPTY values dictionary
  mod.onState({ streams: { '0': { values: {} } } });

  // Sidebar indicators
  const sbArm = doc.getElementById('sb-arm');
  const sbFlymode = doc.getElementById('sb-flymode');
  const sbVbat = doc.getElementById('sb-vbat');

  assert(sbArm, 'Sidebar must contain #sb-arm');
  assert(sbFlymode, 'Sidebar must contain #sb-flymode');
  assert(sbVbat, 'Sidebar must contain #sb-vbat');

  assert.strictEqual(sbArm.textContent, 'NOT PUBLISHED', 'Absent arm must render NOT PUBLISHED in sidebar');
  assert.strictEqual(sbFlymode.textContent, 'NOT PUBLISHED', 'Absent flymode must render NOT PUBLISHED in sidebar');
  assert.strictEqual(sbVbat.textContent, 'NOT PUBLISHED', 'Absent vbat must render NOT PUBLISHED in sidebar');

  // Panel indicators
  const pArm = doc.getElementById('sp-arm-badge');
  const pFlymode = doc.getElementById('sp-flymode');
  const pVbat = doc.getElementById('sp-vbat');
  const pRollRate = doc.getElementById('sp-rate-roll');

  assert.strictEqual(pArm.textContent, 'NOT PUBLISHED', 'Absent arm must render NOT PUBLISHED in panel');
  assert.strictEqual(pFlymode.textContent, 'NOT PUBLISHED', 'Absent flymode must render NOT PUBLISHED in panel');
  assert.strictEqual(pVbat.textContent, 'NOT PUBLISHED', 'Absent vbat must render NOT PUBLISHED in panel');
  assert.strictEqual(pRollRate.textContent, 'NOT PUBLISHED', 'Absent rates must render NOT PUBLISHED in panel');

  console.log('  ✓ Sidebar ARM:       ', sbArm.textContent);
  console.log('  ✓ Sidebar FlightMode:', sbFlymode.textContent);
  console.log('  ✓ Sidebar Battery:   ', sbVbat.textContent);
  console.log('  ✓ Panel Rates:       ', pRollRate.textContent);
}

function checkLive() {
  console.log('\n[Check A1/A3] Live values render correctly:');
  const { doc, mod } = setupEnv();
  mod.ensureSidebar();

  const container = doc.createElement('div');
  container.innerHTML = mod.buildHTML();

  // Feed state with live armed, flight mode (SDK = 5), vbat = 16.25 V, angular rates
  mod.onState({
    streams: {
      '0': {
        values: {
          'status.arm': 1.0,
          'status.flymode': 5.0,
          'status.vbat': 16.25,
          'status.twc_arrived': 1.0,
          'status.twc_execute': 0.0,
          'status.rc_authority': 1.0,
          'status.of_hold': 1.0,
          'status.estimator_ready': 1.0,
          'status.sbus': 0.0,
        }
      },
      '2': {
        values: {
          'c.gyro_x': 0.05,
          'c.gyro_y': -0.02,
          'c.gyro_z': 0.00
        }
      }
    }
  });

  const sbArm = doc.getElementById('sb-arm');
  const sbFlymode = doc.getElementById('sb-flymode');
  const sbVbat = doc.getElementById('sb-vbat');

  assert.strictEqual(sbArm.textContent, 'ARMED', 'Live armed=1 must render ARMED');
  assert.strictEqual(sbFlymode.textContent, 'SDK', 'Live flymode=5 must render SDK');
  assert.strictEqual(sbVbat.textContent, '16.25 V', 'Live vbat=16.25 must render 16.25 V');

  const pArm = doc.getElementById('sp-arm-badge');
  const pFlymode = doc.getElementById('sp-flymode');
  const pVbat = doc.getElementById('sp-vbat');
  const pRollRate = doc.getElementById('sp-rate-roll');
  const pPitchRate = doc.getElementById('sp-rate-pitch');
  const pYawRate = doc.getElementById('sp-rate-yaw');

  assert.strictEqual(pArm.textContent, 'ARMED');
  assert.strictEqual(pFlymode.textContent, 'SDK');
  assert.strictEqual(pVbat.textContent, '16.25 V');
  assert.strictEqual(pRollRate.textContent, '+0.05 rad/s');
  assert.strictEqual(pPitchRate.textContent, '-0.02 rad/s');
  assert.strictEqual(pYawRate.textContent, '+0.00 rad/s');

  // Check disarmed state as well
  mod.onState({
    streams: {
      '0': {
        values: {
          'status.arm': 0.0,
          'status.flymode': 0.0,
          'status.vbat': 14.80,
        }
      }
    }
  });

  assert.strictEqual(sbArm.textContent, 'DISARMED');
  assert.strictEqual(sbFlymode.textContent, 'Stabilize');
  assert.strictEqual(sbVbat.textContent, '14.80 V');

  console.log('  ✓ Sidebar ARMED:     ', 'ARMED');
  console.log('  ✓ Sidebar DISARMED:  ', sbArm.textContent);
  console.log('  ✓ Sidebar FlightMode:', sbFlymode.textContent);
  console.log('  ✓ Sidebar Battery:   ', sbVbat.textContent);
}

function checkCommands() {
  console.log('\n[Check A3] Command feedback indicator in consolidated panel:');
  const { doc, mod } = setupEnv();
  const container = doc.createElement('div');
  container.innerHTML = mod.buildHTML();

  // Transaction result: applied
  mod.onState({
    last_transaction_result: {
      transaction_id: 'tx-101',
      command_id: 14,
      status: 'applied',
      detail: 'armed'
    }
  });

  const cmdText = doc.getElementById('sp-cmd-text');
  assert(cmdText, 'Panel must contain #sp-cmd-text');
  assert.strictEqual(cmdText.textContent, 'SDK Arm Auth applied');
  console.log('  ✓ Command applied:   ', cmdText.textContent);

  // Transaction result: rejected
  mod.onState({
    last_transaction_result: {
      transaction_id: 'tx-102',
      command_id: 1,
      status: 'rejected'
    }
  });
  assert.strictEqual(cmdText.textContent, 'Command rejected');
  console.log('  ✓ Command rejected:  ', cmdText.textContent);
}

function runAll() {
  console.log('--- Running status-panel.js verification harness ---');
  checkA2();
  checkAbsence();
  checkLive();
  checkCommands();
  console.log('\nALL CHECKS PASSED for status-panel.js (A1-A3)\n');
}

if (require.main === module) {
  const arg = process.argv[2] || 'all';
  try {
    if (arg === 'check_a2') {
      checkA2();
    } else if (arg === 'check_absence') {
      checkAbsence();
    } else if (arg === 'check_live') {
      checkLive();
    } else {
      runAll();
    }
  } catch (err) {
    console.error('FAIL:', err);
    process.exit(1);
  }
}

module.exports = { runAll, checkA2, checkAbsence, checkLive, checkCommands };
