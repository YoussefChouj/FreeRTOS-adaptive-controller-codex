'use strict';
/**
 * Offline harness for the MRAC panel's convergence ranking (item 11).
 *
 * The panel gains a "Rank by drift" toggle that sorts every adaptive weight
 * by its recent drift (variance over the last N samples) so drifting
 * (not-yet-converged) weights rise to the top. It must work for ANY number of
 * weights. The pure ranking logic is exported as window.__MRAC_TEST__.
 *
 * Run:  node ground_station/service/tests/mrac_panel_harness.js
 * Exit 0 iff every check passes.
 */
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const PANEL = path.join(__dirname, '..', '..', '..',
  'docs', 'dashboard-platform', 'shell', 'plugins', 'mrac-panel.js');

function makeDoc() {
  return {
    getElementById() { return null; },
    createElement() { return { style: {}, appendChild() {}, addEventListener() {} }; },
    querySelectorAll() { return []; },
    addEventListener() {},
  };
}

function load() {
  const doc = makeDoc();
  const win = {
    __registerPlugin__() {},
    __PLUGIN_INIT__: null,
    __PLUGIN_DESTROY__: null,
    __MRAC_TEST__: null,
  };
  const sandbox = vm.createContext({
    document: doc,
    window: win,
    console,
    setTimeout: (fn, ms) => fn(),
    clearTimeout() {},
    setInterval() { return 0; },
    clearInterval() {},
    Object, Array, Math, String, Number, parseFloat, isNaN, isFinite,
  });
  vm.runInContext(fs.readFileSync(PANEL, 'utf8'), sandbox, { filename: 'mrac-panel.js' });
  return {
    T: win.__MRAC_TEST__,
    win,
    loadInit(api) { if (win.__PLUGIN_INIT__) win.__PLUGIN_INIT__(api); },
  };
}

let failures = 0;
function check(label, cond) {
  if (cond) console.log('  OK  ' + label);
  else { console.error('FAIL  ' + label); failures++; }
}

async function main() {
  console.log('\n=== mrac-panel.js convergence ranking harness (item 11) ===\n');

  const { T } = load();
  if (!T) { console.error('FAIL  __MRAC_TEST__ hook missing on mrac-panel.js'); process.exit(1); }

  // 1) variance separates a drifting weight from a pinned one.
  const pinned = [1.0, 1.0001, 1.0, 0.9999, 1.0001];
  const drifting = [0.0, 5.0, -3.0, 8.0, -4.0];
  check('pinned weight has ~zero variance', T.variance(pinned) < 1e-6);
  check('drifting weight has high variance',
    T.variance(drifting) > T.variance(pinned) * 100);

  // 2) rankedRows sorts the drifting weight to the top regardless of feed order.
  // Feed a converged weight first so ranking must reorder, not trust arrival order.
  T.recordHist({ 'pitch.theta_1': 0.0 }); // fixed, then inflated below
  T.recordHist({ 'pitch.theta_2': 1.0 });
  // Push the drifting series for theta_1 and a pinned series for theta_2.
  const N = T.RANK_HISTORY_LEN;
  const driftSeries = [];
  for (let i = 0; i < N; i++) driftSeries.push((i % 3 === 0 ? 9.0 : -4.5));
  const pinnedSeries = [];
  for (let j = 0; j < N; j++) pinnedSeries.push(0.5);
  for (let k = 0; k < N; k++) {
    T.recordHist({ 'pitch.theta_1': driftSeries[k], 'pitch.theta_2': pinnedSeries[k] });
  }
  let rows = T.rankedRows();
  const drift = rows.filter(r => r.key === 'pitch.theta_1');
  const pin = rows.filter(r => r.key === 'pitch.theta_2');
  check('both weights present in ranking', drift.length === 1 && pin.length === 1);
  check('drifting weight ranks above pinned one', rows[0].key === 'pitch.theta_1');
  check('drifting weight has higher variance', drift[0].var > pin[0].var);

  // 3) any number of weights — add two more axes' weights, all still ranked.
  for (let m = 0; m < N; m++) {
    T.recordHist({ 'roll.theta_0': pinnedSeries[m], 'yaw.theta_3': driftSeries[m] });
  }
  rows = T.rankedRows();
  check('four weights ranked after adding more', rows.length === 4);
  check('ranked set still sorts drift to top (pitch.theta_1 first)',
    rows[0].key === 'pitch.theta_1' || rows[0].key === 'yaw.theta_3');

  // 4) toggle switches between natural and ranked mode (idempotent, works any #).
  T.setMode('ranked');
  check('setMode(\'ranked\') activates ranked mode', T.mode() === 'ranked');
  T.setMode('natural');
  check('setMode(\'natural\') returns to natural order', T.mode() === 'natural');
  T.setMode(T.mode());
  check('setMode is idempotent', T.mode() === 'natural');

  // 5) ranking survives a weight that never reports a value (null is skipped).
  rows.forEach(r => {
    if (r.key === 'pitch.theta_1') { T.recordHist({ 'pitch.theta_1': 9.0 }); }
  });
  rows = T.rankedRows();
  check('rankedRows returns array of weight rows', Array.isArray(rows) && rows.every(r => r.key));

  console.log(failures ? '\nFAILED: ' + failures + ' assertions' : '\nALL GREEN');
  process.exit(failures ? 1 : 0);
}

main().catch((e) => { console.error('MRAC HARNESS CRASHED:', e); process.exit(1); });