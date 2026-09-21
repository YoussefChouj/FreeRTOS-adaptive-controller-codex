// Offline panel harness: drives the real slot-manager-panel.js against a
// real ApiServer (passed as argv[2]) through a minimal DOM/event shim.
'use strict';
const fs = require('fs');
const vm = require('vm');

const PORT = process.argv[2];
const PANEL = process.argv[3];

// ---- minimal DOM shim ---------------------------------------------------
class El {
  constructor(id) {
    this.id = id;
    this._h = {};
    this._attrs = {};
    this.style = {};
    this.value = '';
    this.innerHTML = '';
    this.textContent = '';
    this.className = '';
    const set = new Set();
    this.classList = {
      add: (c) => set.add(c),
      remove: (c) => set.delete(c),
      toggle(c, f) { if (f === undefined) f = !set.has(c); f ? set.add(c) : set.delete(c); return f; },
      contains: (c) => set.has(c),
    };
  }
  addEventListener(t, f) { (this._h[t] = this._h[t] || []).push(f); }
  getAttribute(a) { return Object.prototype.hasOwnProperty.call(this._attrs, a) ? this._attrs[a] : null; }
  setAttribute(a, v) { this._attrs[a] = v; }
  fire(t, ev) { for (const f of (this._h[t] || [])) f(ev || {}); }
}

const byId = {};
function makeList(attr, values) {
  return values.map((v) => { const e = new El(attr + ':' + v); e.setAttribute(attr, String(v)); return e; });
}
const lists = {
  '.sm-subscribe-btn': makeList('data-slot', [0, 1, 2, 3]),
  '.sm-preset-btn': makeList('data-preset', ['mrac', 'ekf', 'imu', 'of', 'pid']),
  '.sm-cadence-btn': makeList('data-cadence', ['100', '80.2']),
};
global.document = {
  getElementById(id) { return byId[id] || (byId[id] = new El(id)); },
  querySelector() { return null; },
  querySelectorAll(sel) { return lists[sel] || []; },
};

global.window = global;
const origSetTimeout = global.setTimeout;
global.requestAnimationFrame = (fn) => fn();
global.setInterval = () => 0;
global.clearInterval = () => {};
const sleep = (ms) => new Promise((r) => origSetTimeout(r, ms));

const origFetch = global.fetch.bind(global);
global.fetch = (u, opts) => origFetch('http://127.0.0.1:' + PORT + u, opts);

// ---- load the real panel file ------------------------------------------
global.__registerPlugin__ = () => {};
vm.runInThisContext(fs.readFileSync(PANEL, 'utf8'), { filename: PANEL });

let panelCb = null;
const slotCalls = [];
const api = {
  registerPanel: (name, cb) => { panelCb = cb; },
  subscribe: () => {},
  subscribeSlot(slot, div, ranges) {
    slotCalls.push({ slot, divider: div, ranges: ranges.slice() });
    return Promise.resolve({});
  },
};
const tick = () => new Promise((r) => setImmediate(r));
const namesOf = (el) => (el.innerHTML.match(/title="([^"]+)">[^<]*<\/span>/g) || [])
  .map((s) => s.match(/title="([^"]+)"/)[1]);
function btnTarget(sel, enc) {
  return { closest: (s) => (s === sel ? { getAttribute: () => enc } : null) };
}
function line(label) { console.log('\n=== ' + label + ' ==='); }

(async () => {
  // Warm undici's connection pool before activating the panel, as a browser
  // would for already-open connections.
  await global.fetch('/health').then((r) => r.text());

  global.__PLUGIN_INIT__(api);
  panelCb(new El('container'));

  // Seed form defaults the browser would parse from the value= attributes
  // (the shim stores innerHTML as an opaque string, so it cannot parse them).
  byId['sm-hz'].value = '100';
  byId['sm-cadence'].value = '100';
  await sleep(1500); await tick();

  line('initial root picker (real /api/symbols, unfiltered)');
  console.log('status: ' + document.getElementById('sm-picker-status').textContent);
  console.log('first 8 rows: ' + namesOf(document.getElementById('sm-picker-results')).slice(0, 8).join(', '));

  line('type-to-filter: prefix "mrac"');
  byId['sm-picker-filter'].value = 'mrac';
  byId['sm-picker-filter'].fire('input');
  await sleep(300); await tick();
  console.log('status: ' + document.getElementById('sm-picker-status').textContent);
  console.log('rows: ' + namesOf(document.getElementById('sm-picker-results')).join(', '));

  line('drill into mrac_state');
  document.getElementById('sm-picker-results').fire('click',
    { target: btnTarget('.sm-picker-drill', encodeURIComponent('mrac_state')) });
  await sleep(200); await tick();
  console.log('status: ' + document.getElementById('sm-picker-status').textContent);
  console.log('rows: ' + namesOf(document.getElementById('sm-picker-results')).join(', '));

  line('drill into mrac_state.roll, then add mrac_state.roll.e via +');
  document.getElementById('sm-picker-results').fire('click',
    { target: btnTarget('.sm-picker-drill', encodeURIComponent('mrac_state.roll')) });
  await sleep(200); await tick();
  console.log('rows: ' + namesOf(document.getElementById('sm-picker-results')).join(', '));
  document.getElementById('sm-picker-results').fire('click',
    { target: btnTarget('.sm-picker-add', encodeURIComponent('mrac_state.roll.e')) });
  console.log('textarea after add: ' + byId['sm-ranges'].value);

  line('breadcrumb "base" returns to root view');
  byId['sm-picker-breadcrumb'].fire('click',
    { target: { closest: (s) => (s === '.sm-crumb' ? { getAttribute: () => '' } : null) } });
  await sleep(200); await tick();
  console.log('status: ' + document.getElementById('sm-picker-status').textContent);

  line('five presets quick-fill');
  lists['.sm-preset-btn'].forEach((btn) => {
    btn.fire('click');
    const n = byId['sm-ranges'].value.split(/[,\s]+/).filter(Boolean).length;
    console.log(btn.getAttribute('data-preset') + ': ' + n + ' ranges loaded');
  });

  line('free-text textarea + slot 1 subscribe (30 Hz @ cadence 100)');
  byId['sm-hz'].value = '30';
  byId['sm-hz'].fire('input');
  byId['sm-ranges'].value = 'Ctrler.rollPID.Des, ano_of.of_quality, not_a_real_name';
  lists['.sm-subscribe-btn'][1].fire('click');
  await sleep(200); await tick(); await tick();
  console.log('readout: ' + document.getElementById('sm-hz-readout').textContent);
  console.log('subscribeSlot call: ' + JSON.stringify(slotCalls.pop()));

  line('C3 cadence honesty: 30 Hz at 100 vs 80.2');
  byId['sm-cadence'].value = '100';
  byId['sm-cadence'].fire('input');
  console.log('cadence 100.0 -> ' + document.getElementById('sm-hz-readout').textContent);
  byId['sm-cadence'].value = '80.2';
  byId['sm-cadence'].fire('input');
  console.log('cadence 80.2  -> ' + document.getElementById('sm-hz-readout').textContent);
  lists['.sm-subscribe-btn'][1].fire('click');
  await sleep(200); await tick(); await tick();
  console.log('subscribeSlot call at 80.2: ' + JSON.stringify(slotCalls.pop()));

  line('more readout examples at cadence 100');
  byId['sm-cadence'].value = '100';
  ['50', '25', '100'].forEach((h) => {
    byId['sm-hz'].value = h;
    byId['sm-hz'].fire('input');
    console.log(document.getElementById('sm-hz-readout').textContent);
  });
})().catch((e) => { console.error('HARNESS FAIL', e); process.exit(1); });
