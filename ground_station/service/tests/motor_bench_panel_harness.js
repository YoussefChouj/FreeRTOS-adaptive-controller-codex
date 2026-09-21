'use strict';
/**
 * Offline verification harness for motor-bench-panel.js (task 20260921-012643).
 *
 * Drives the panel in a fake DOM with a stubbed shell API and prints the
 * actual (cmdId, index, value) tuples the panel would put on the wire. No
 * network, no service, no hardware — every send is captured by the stub.
 *
 * Run:  node ground_station/service/tests/motor_bench_panel_harness.js
 * Exit code 0 iff every check passes.
 */
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const PANEL = path.join(__dirname, '..', '..', '..',
  'docs', 'dashboard-platform', 'shell', 'plugins', 'motor-bench-panel.js');

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
    this.className = '';
    this.handlers = {};
    this.style = {};
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
    this.docHandlers = {};
    this.hidden = false;
  }
  scan(html) {
    const tags = html.match(/<[a-zA-Z][^>]*>/g) || [];
    for (const tag of tags) {
      const idm = tag.match(/\bid="([^"]+)"/);
      if (!idm) continue;
      let el = this.elements[idm[1]];
      if (!el) { el = new Element(idm[1], tag.slice(1)); this.elements[idm[1]] = el; }
      const vm = tag.match(/\bvalue="([^"]*)"/);
      if (vm && el._valueInit !== true) { el.value = vm[1]; el._valueInit = true; }
      if (/\bchecked\b/.test(tag)) el.checked = true;
    }
  }
  getElementById(id) { return this.elements[id] || null; }
  addEventListener(ev, fn) { (this.docHandlers[ev] = this.docHandlers[ev] || []).push(fn); }
  dispatchDoc(ev) {
    (this.docHandlers[ev] || []).forEach((fn) => fn.call(this, { type: ev }));
  }
}

// ── Stubbed shell API — records every command the panel would send ────────
function makeApi(opts) {
  opts = opts || {};
  const api = {
    commands: [],
    alerts: [],
    disarmed: opts.disarmed !== undefined ? opts.disarmed : true,
    armState: opts.armState !== undefined ? opts.armState : 'disarmed',
    failAfter: opts.failAfter, // gatedCommand rejects once command count reaches this
    txid: 0,
    gatedCommand(id, idx, val, gates) {
      api.commands.push({
        t: Date.now(), kind: 'gated', id, idx, val,
        gates: gates ? gates.slice() : null,
      });
      if (api.failAfter !== undefined && api.commands.length >= api.failAfter) {
        return Promise.reject(new Error('simulated send failure #' + api.commands.length));
      }
      // Mirror the real /commands POST: a backend transaction id comes back.
      api.txid += 1; api.lastTxid = api.txid;
      return Promise.resolve({ transaction_id: api.txid, ok: true });
    },
    submitCommand(id, idx, val) {
      api.commands.push({ t: Date.now(), kind: 'submit', id, idx, val, gates: null });
      return Promise.resolve({ ok: true });
    },
    isDisarmed() { return api.disarmed; },
    getArmState() { return api.armState; },
    subscribe(cb) { api.stateCb = cb; },
    registerPanel(name, renderFn) { api.panelName = name; api.renderFn = renderFn; },
  };
  return api;
}

// ── Load the plugin into a fresh sandbox ───────────────────────────────────
function loadPanel(api) {
  const doc = new FakeDocument();
  const container = new Element('mb-container', 'div');
  container.doc = doc;
  const sandbox = {
    document: doc,
    console: { log() {}, warn() {}, error() {} },
    setInterval, clearInterval, setTimeout, clearTimeout,
    alert: (m) => api.alerts.push(m),
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

// ── Helpers ────────────────────────────────────────────────────────────────
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
async function until(cond, timeoutMs) {
  const t0 = Date.now();
  while (!cond()) {
    if (Date.now() - t0 > timeoutMs) return false;
    await sleep(10);
  }
  return true;
}

let failures = 0;
function check(ok, label) {
  console.log((ok ? 'PASS' : 'FAIL') + ': ' + label);
  if (!ok) failures++;
}
function tuples(api) {
  return api.commands.map((c, i) =>
    '  #' + i + ' (' + c.id + ', ' + c.idx + ', ' + c.val + ')' +
    (c.gates ? '  gates=' + JSON.stringify(c.gates) : ''));
}
const isSend = (c, idx, val) => c.kind === 'gated' && c.id === 22 && c.idx === idx && c.val === val;

// Enable the bench (select defaults M1) and wait until the heartbeat has
// produced `hbCount` (idx0, val1) sends AFTER the enable send itself.
async function enableAndWait(api, doc, hbCount) {
  doc.getElementById('mb-enable-on').dispatch('click');
  await until(() => api.commands.filter((c) => isSend(c, 0, 1)).length >= 1 + hbCount, 3000);
  await sleep(20);
}

// ── 1. Protocol correctness + 4. no auto-enable + 5. gate on every send ────
async function scenarioSequence() {
  console.log('== 1/4/5. Sequence: select M3 -> CCR 2650 -> enable -> 3 heartbeats -> disable ==');
  const api = makeApi();
  const h = loadPanel(api);
  const doc = h.doc;

  check(api.commands.length === 0, 'mount + render emits ZERO commands (no auto-enable)');
  // Feed telemetry state too — must still emit nothing.
  api.stateCb({ streams: { '3': { values: {} } } });
  check(api.commands.length === 0, 'mount + render + first state tick emits ZERO commands');

  // Operator: select M3
  doc.elements['mb-mot-1'].checked = false;
  doc.elements['mb-mot-3'].checked = true;
  doc.elements['mb-mot-3'].dispatch('change');
  await sleep(20);

  // Operator: set CCR 2650
  const slider = doc.getElementById('mb-ccr');
  slider.value = '2650';
  slider.dispatch('input');
  slider.dispatch('change');
  await sleep(20);

  // Operator: enable
  doc.getElementById('mb-enable-on').dispatch('click');
  await until(() => api.commands.filter((c) => isSend(c, 0, 1)).length >= 4, 3000);
  await sleep(20);

  // Operator: disable
  doc.getElementById('mb-enable-off').dispatch('click');
  await sleep(350); // long enough that a runaway heartbeat would show up

  console.log('verbatim ordered command tuples:');
  tuples(api).forEach((l) => console.log(l));

  const cmds = api.commands;
  // Enable send = first (0,1); must be preceded immediately by (1,3) and (2,2000).
  const enableIdx = cmds.findIndex((c) => isSend(c, 0, 1));
  check(enableIdx === 4, 'enable send is command #4 (after select + CCR sends and their re-send)');
  check(isSend(cmds[enableIdx - 1], 2, 2000) && isSend(cmds[enableIdx - 2], 1, 3),
    'before enable: motor select (22,1,3) then CCR=2000 (22,2,2000), enable last');
  // After disable: last two must be CCR=2000 then enable=0.
  const n = cmds.length;
  check(isSend(cmds[n - 1], 0, 0) && isSend(cmds[n - 2], 2, 2000),
    'on disable: CCR=2000 first, then enable=0');
  // Heartbeats between enable and disable are all (22,0,1).
  const mid = cmds.slice(enableIdx + 1, n - 2);
  check(mid.every((c) => isSend(c, 0, 1)) && mid.length >= 3,
    'between enable and disable: only (22,0,1) heartbeat sends (' + mid.length + ' ticks)');
  check(cmds.every((c, i) => i === n - 1 || true) &&
    cmds.filter((c) => c.kind === 'submit').length === 0,
    'no ungated submitCommand on the bench path');
  check(cmds.filter((c) => c.kind === 'gated').every((c) =>
    JSON.stringify(c.gates) === '["disarmed"]'),
    'every emitted send (heartbeats included) goes through gatedCommand(..., ["disarmed"])');
  const selectVals = cmds.filter((c) => c.idx === 1).map((c) => c.val);
  check(selectVals.every((v) => Number.isInteger(v) && v >= 1 && v <= 4),
    'idx-1 motor select values are integers 1..4 only: ' + JSON.stringify(selectVals));
  const ccrVals = cmds.filter((c) => c.idx === 2).map((c) => c.val);
  check(ccrVals.every((v) => v >= 2000 && v <= 4000),
    'idx-2 CCR values all inside [2000,4000]: ' + JSON.stringify(ccrVals));

  // Gate reports not-disarmed: nothing may leave the panel.
  const api2 = makeApi({ disarmed: false, armState: 'armed' });
  const h2 = loadPanel(api2);
  h2.doc.elements['mb-mot-2'].checked = true;
  h2.doc.elements['mb-mot-2'].dispatch('change');
  const s2 = h2.doc.getElementById('mb-ccr');
  s2.value = '3000'; s2.dispatch('input'); s2.dispatch('change');
  h2.doc.getElementById('mb-enable-on').dispatch('click');
  await sleep(400);
  check(api2.commands.length === 0,
    'gate not satisfied (not disarmed): ZERO commands emitted, no heartbeat started');

  // RPM feedback via Frame C
  api.stateCb({ streams: { '3': { values: {
    'motor.rpm_0': 1000, 'motor.rpm_1': 2000, 'motor.rpm_2': 4120, 'motor.rpm_3': 800,
  } } } });
  const fb = h.doc.getElementById('mb-feedback-area').innerHTML;
  check(fb.indexOf('4120 RPM') !== -1 && fb.indexOf('Motor 3') !== -1,
    'RPM feedback populates from state.streams["3"] motor.rpm_* (M3: 4120)');
  check(h.doc.getElementById('mb-arm-badge').textContent === 'DISARMED',
    'arm badge shows DISARMED from api.getArmState()');

  return h;
}

// ── 2. Heartbeat timing vs the 500 ms dead-man ─────────────────────────────
async function scenarioTiming() {
  console.log('== 2. Heartbeat timing vs 500 ms firmware dead-man ==');
  const api = makeApi();
  const h = loadPanel(api);
  await enableAndWait(api, h.doc, 10); // ~10 heartbeat ticks
  h.doc.getElementById('mb-enable-off').dispatch('click');
  await sleep(50);

  const hb = api.commands.filter((c) => isSend(c, 0, 1)).map((c) => c.t);
  const deltas = [];
  for (let i = 1; i < hb.length; i++) deltas.push(hb[i] - hb[i - 1]);
  const min = Math.min(...deltas), max = Math.max(...deltas);
  const avg = deltas.reduce((a, b) => a + b, 0) / deltas.length;
  console.log('heartbeat sends: ' + hb.length + ', intervals ms: [' + deltas.join(', ') + ']');
  console.log('min=' + min + ' ms  max=' + max + ' ms  avg=' + avg.toFixed(1) + ' ms');
  console.log('dead-man window 500 ms; worst observed interval ' + max + ' ms; margin = ' +
    (500 - max) + ' ms (' + (500 / max).toFixed(1) + 'x)');
  check(max < 500, 'worst heartbeat interval ' + max + ' ms is inside the 500 ms dead-man window');
  check(min > 0 && avg > 40 && avg < 160, 'average interval ~100 ms (10 Hz)');
}

// ── 3. Heartbeat stop triggers ──────────────────────────────────────────────
async function scenarioStops() {
  console.log('== 3. Heartbeat stop triggers ==');

  async function heartbeatSilentAfter(label, trigger) {
    const api = makeApi();
    const h = loadPanel(api);
    await enableAndWait(api, h.doc, 3);
    const countAtTrigger = api.commands.length;
    trigger(api, h);
    await sleep(500); // 5 heartbeat periods — a surviving heartbeat would fire ~5 times
    const after = api.commands.slice(countAtTrigger);
    const hbAfter = after.filter((c) => isSend(c, 0, 1)).length;
    check(hbAfter === 0, label + ' — heartbeat silent for 500 ms after trigger ' +
      '(sends after trigger: ' + after.map((c) => '(' + c.id + ',' + c.idx + ',' + c.val + ')').join(' ') + ')');
    return { api, h };
  }

  // (a) operator toggles OFF
  await heartbeatSilentAfter('toggle OFF', (api, h) => {
    h.doc.getElementById('mb-enable-off').dispatch('click');
  });

  // (b) abort pressed
  const ab = await heartbeatSilentAfter('ABORT pressed', (api, h) => {
    h.doc.getElementById('mb-estop').dispatch('click');
  });
  check(ab.api.commands.some((c) => c.kind === 'submit' && c.id === 13 && c.idx === 0 && c.val === 0),
    'abort still sends submitCommand(13, 0, 0) — kept as-is');

  // (c) panel teardown (__PLUGIN_DESTROY__)
  const td = await heartbeatSilentAfter('panel teardown', (api, h) => {
    h.sandbox.pluginDestroy();
  });
  const tdSends = td.api.commands.slice(-2);
  check(isSend(tdSends[0], 2, 2000) && isSend(tdSends[1], 0, 0),
    'teardown best-effort sends CCR=2000 then enable=0 (belt-and-braces; firmware dead-man is the guarantee)');

  // (d) page hidden (visibilitychange)
  const hd = await heartbeatSilentAfter('page hidden', (api, h) => {
    h.doc.hidden = true;
    h.doc.dispatchDoc('visibilitychange');
  });
  const hdSends = hd.api.commands.slice(-2);
  check(isSend(hdSends[0], 2, 2000) && isSend(hdSends[1], 0, 0),
    'page-hidden path sends CCR=2000 then enable=0 and does not re-enable on return');

  // (e) arm state leaves DISARMED
  const ar = await heartbeatSilentAfter('arm state leaves DISARMED', (api, h) => {
    api.armState = 'armed';
    api.stateCb({ streams: { '3': { values: { 'status.arm': 1 } } } });
  });
  check(ar.h.doc.getElementById('mb-arm-badge').textContent === 'ARMED',
    'arm badge flips to ARMED on state tick');

  // (f) a command send fails
  const apiF = makeApi();
  // failAfter=4: sends 1-3 are the enable triplet, send 4 = first heartbeat -> rejects.
  apiF.failAfter = 4;
  const hF = loadPanel(apiF);
  const countAtTrigger = () => apiF.commands.length;
  hF.doc.getElementById('mb-enable-on').dispatch('click');
  await until(() => apiF.commands.length >= 4, 3000);
  await sleep(500);
  check(apiF.commands.length === 4,
    'heartbeat send failure stops the heartbeat (total sends stays at ' + countAtTrigger() + ')');
}

// ── 6. Labels / units ──────────────────────────────────────────────────────
function scenarioLabels() {
  console.log('== 6. Labels / units ==');
  const api = makeApi();
  const h = loadPanel(api);
  const html = h.container.innerHTML;
  const needles = ['Enable bench test', 'Motor select (idx 1)', 'CCR (idx 2) — PWM counts',
    '2000 = off', 'Commanded CCR:', 'Heartbeat 10 Hz', 'RPM Feedback (Frame C)'];
  console.log('rendered label snippets:');
  for (const nd of needles) {
    const at = html.indexOf(nd);
    console.log('  |' + (at === -1 ? '(MISSING) ' + nd
      : html.slice(at, Math.min(at + 90, html.length)).replace(/<[^>]*>/g, ' ').trim()));
  }
  check(/CCR \(idx 2\) — PWM counts/.test(html), 'CCR control labelled "PWM counts"');
  check(/2000 = off/.test(html), '"2000 = off" visible on the control itself');
  check(!/0–1000|0-1000/.test(html), 'no 0–1000 scale wording in rendered HTML');
  const src = fs.readFileSync(PANEL, 'utf8');
  check(!/throttle/i.test(src), 'no "throttle" wording survives in the panel source');
  check(!/percent/.test(src), 'no invented percentage scale in the panel source');
}

// ── 7. Bug 6: firmware ack / command lifecycle is surfaced in the UI ────────
async function scenarioAck() {
  console.log('== 7. Firmware ack / command lifecycle feedback (Bug 6) ==');
  const api = makeApi();
  const h = loadPanel(api);
  const doc = h.doc;

  // Before any send: no command rows shown.
  const preArea = doc.getElementById('mb-lifecycle');
  check(preArea !== null &&
    preArea.innerHTML.indexOf('CCR') === -1 && preArea.innerHTML.indexOf('SUBMITTED') === -1,
    'lifecycle area exists and is empty of command rows before any send');

  // A single CCR command (idx 2, val 2600) -> SUBMITTED visible immediately.
  doc.getElementById('mb-ccr').value = '2600';
  doc.getElementById('mb-ccr').dispatch('input');
  doc.getElementById('mb-ccr').dispatch('change');
  await sleep(20);
  let lc = doc.getElementById('mb-lifecycle').innerHTML;
  check(lc.indexOf('SUBMITTED') !== -1 && lc.indexOf('CCR') !== -1,
    'command shows SUBMITTED immediately on the click (visible feedback): ' + lc.replace(/<[^>]*>/g, ' ').trim());
  check(api.lastTxid >= 1, 'panel captured a backend transaction id (' + api.lastTxid + ')');

  // Firmware ACKs (status "ack") -> ACKNOWLEDGED.
  api.stateCb({ streams: { '3': { values: {} } },
    command_results: [{ transaction_id: api.lastTxid, command_id: 22, index: 2,
      status: 'ack', reason: 'NONE', detail: 'queued' }] });
  lc = doc.getElementById('mb-lifecycle').innerHTML;
  check(lc.indexOf('ACKNOWLEDGED') !== -1, 'firmware ACK advances lifecycle to ACKNOWLEDGED');

  // Firmware APPLIES -> APPLIED.
  api.stateCb({ streams: { '3': { values: {} } },
    command_results: [{ transaction_id: api.lastTxid, command_id: 22, index: 2,
      status: 'applied', reason: 'NONE', detail: 'applied' }] });
  lc = doc.getElementById('mb-lifecycle').innerHTML;
  check(lc.indexOf('APPLIED') !== -1 && lc.indexOf('ACKNOWLEDGED') === -1,
    'the FC APPlying advances the single per-tx entry to APPLIED (terminal ack)');

  // A fresh command that the FQ rejects -> REJECTED with the reason surfaced.
  doc.getElementById('mb-ccr').value = '2800';
  doc.getElementById('mb-ccr').dispatch('input');
  doc.getElementById('mb-ccr').dispatch('change');
  await sleep(20);
  const rejTx = api.lastTxid;
  api.stateCb({ streams: { '3': { values: {} } },
    command_results: [{ transaction_id: rejTx, command_id: 22, index: 2,
      status: 'rejected', reason: 'SAFETY_INTERLOCK', detail: 'arm state unknown' }] });
  lc = doc.getElementById('mb-lifecycle').innerHTML;
  check(lc.indexOf('REJECTED') !== -1 && lc.indexOf('SAFETY_INTERLOCK') !== -1,
    'firmware REJECTED produces "REJECTED" with reason surfaced: ' +
    lc.replace(/<[^>]*>/g, ' ').trim());
}

// ── main ───────────────────────────────────────────────────────────────────
(async () => {
  await scenarioSequence();
  await scenarioTiming();
  await scenarioStops();
  scenarioLabels();
  await scenarioAck();
  console.log(failures === 0 ? 'ALL CHECKS PASSED' : failures + ' CHECK(S) FAILED');
  process.exit(failures === 0 ? 0 : 1);
})().catch((e) => { console.error(e); process.exit(1); });
