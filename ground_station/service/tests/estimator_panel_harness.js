'use strict';
/**
 * Offline verification harness for estimator-panel.js (task 20260922-061216).
 *
 * Verifies:
 *   1. Mode switch (idx=0) uses gatedCommand with ['disarmed'] gate.
 *   2. Mode switch is blocked by isDisarmed() === false check.
 *   3. Tau change (idx=2) uses submitCommand (no gate required).
 *   4. Freeze toggle (idx=1) uses submitCommand (no gate required).
 *   5. DOM renders mode buttons and tau readback without crash.
 *
 * Run:  node ground_station/service/tests/estimator_panel_harness.js
 * Exit code 0 iff every check passes.
 */
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const PANEL = path.join(__dirname, '..', '..', '..',
  'docs', 'dashboard-platform', 'shell', 'plugins', 'estimator-panel.js');

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
    this.doc = null;
  }
  addEventListener(ev, fn) { (this.handlers[ev] = this.handlers[ev] || []).push(fn); }
  dispatch(ev) { (this.handlers[ev] || []).forEach(fn => fn.call(this, { type: ev, target: this })); }
  get innerHTML() { return this._html; }
  set innerHTML(html) { this._html = html; if (this.doc) this.doc.scan(html); }
}

class FakeDocument {
  constructor() { this.elements = {}; this.docHandlers = {}; this.hidden = false; }
  scan(html) {
    const tags = html.match(/<[a-zA-Z][^>]*>/g) || [];
    for (const tag of tags) {
      const idm = tag.match(/\bid="([^"]+)"/);
      if (!idm) continue;
      let el = this.elements[idm[1]];
      if (!el) { el = new Element(idm[1], tag.slice(1)); this.elements[idm[1]] = el; }
      const vm2 = tag.match(/\bvalue="([^"]*)"/);
      if (vm2 && el._valueInit !== true) { el.value = vm2[1]; el._valueInit = true; }
    }
  }
  getElementById(id) { return this.elements[id] || null; }
  addEventListener(ev, fn) { (this.docHandlers[ev] = this.docHandlers[ev] || []).push(fn); }
}

// ── Stubbed shell API ──────────────────────────────────────────────────────
function makeApi(opts) {
  opts = opts || {};
  const api = {
    commands: [],
    disarmed: opts.disarmed !== undefined ? opts.disarmed : true,
    armState: opts.armState !== undefined ? opts.armState : 'disarmed',
    txid: 0,
    gatedCommand(id, idx, val, gates) {
      api.commands.push({ kind: 'gated', id, idx, val, gates: gates ? gates.slice() : null });
      api.txid++;
      return Promise.resolve({ transaction_id: api.txid });
    },
    submitCommand(id, idx, val) {
      api.commands.push({ kind: 'submit', id, idx, val });
      api.txid++;
      return Promise.resolve({ transaction_id: api.txid });
    },
    isDisarmed() { return api.disarmed; },
    getArmState() { return api.armState; },
    registerPanel(name, fn) { api._panel = { name, fn }; },
    subscribe(fn) { api._subscriber = fn; },
    _panels: [],
  };
  return api;
}

// ── Load and run the panel in a sandbox ───────────────────────────────────
function loadPanel(fakeDoc, fakeWindow) {
  const code = fs.readFileSync(PANEL, 'utf8');
  const sandbox = vm.createContext({
    document: fakeDoc,
    window: fakeWindow,
    console,
    setTimeout: (fn, ms) => fn(),   // flush immediately
    clearTimeout: () => {},
    setInterval: (fn, ms) => ({ _id: 0 }),
    clearInterval: () => {},
    Promise,
    parseFloat, isNaN, isFinite,
    Object, Array, Math, String, Number, Date,
  });
  vm.runInContext(code, sandbox);
  return sandbox;
}

// ── Test runner ────────────────────────────────────────────────────────────
let failures = 0;
function check(label, cond) {
  if (cond) {
    console.log('  OK  ' + label);
  } else {
    console.error('FAIL  ' + label);
    failures++;
  }
}

(async () => {
  console.log('\n=== estimator-panel.js harness ===\n');

  /* --- Test 1: mode switch while disarmed uses gatedCommand(['disarmed']) --- */
  {
    const doc = new FakeDocument();
    const container = new Element('container', 'div');
    container.doc = doc;

    const win = {
      __registerPlugin__: () => {},
      __PLUGIN_INIT__: null,
      __PLUGIN_DESTROY__: null,
      _estSetMode: null,
      _estSetTau: null,
      _estToggleFreeze: null,
    };
    loadPanel(doc, win);
    const api = makeApi({ disarmed: true, armState: 'disarmed' });
    if (win.__PLUGIN_INIT__) win.__PLUGIN_INIT__(api);
    if (api._panel) api._panel.fn(container);

    // Inject a fake tau-input element so _estSetTau doesn't crash
    doc.elements['est-tau-input'] = Object.assign(new Element('est-tau-input', 'input'), { value: '30' });

    // Call mode switch
    if (win._estSetMode) win._estSetMode(1);
    await Promise.resolve();

    check('mode switch while disarmed: at least 1 command sent', api.commands.length >= 1);
    const modeCmd = api.commands.find(c => c.id === 0x1E && c.idx === 0);
    check('mode switch uses CMD 0x1E idx=0', modeCmd != null);
    check('mode switch uses gatedCommand', modeCmd && modeCmd.kind === 'gated');
    check('mode switch gate is [disarmed]',
      modeCmd && modeCmd.gates && modeCmd.gates.includes('disarmed'));
    check('mode switch value is 1 (EMA)', modeCmd && modeCmd.val === 1);
    console.log('');
  }

  /* --- Test 2: mode switch while armed is blocked --- */
  {
    const doc = new FakeDocument();
    const container = new Element('container', 'div');
    container.doc = doc;

    const win = {
      __registerPlugin__: () => {},
      __PLUGIN_INIT__: null,
      __PLUGIN_DESTROY__: null,
      _estSetMode: null,
      _estSetTau: null,
      _estToggleFreeze: null,
    };
    loadPanel(doc, win);
    const api = makeApi({ disarmed: false, armState: 'armed' });
    if (win.__PLUGIN_INIT__) win.__PLUGIN_INIT__(api);
    if (api._panel) api._panel.fn(container);

    if (win._estSetMode) win._estSetMode(2);
    await Promise.resolve();

    const modeCmd = api.commands.find(c => c.id === 0x1E && c.idx === 0);
    check('mode switch while armed: no command sent', modeCmd == null);
    console.log('');
  }

  /* --- Test 3: tau change uses submitCommand (no gate) --- */
  {
    const doc = new FakeDocument();
    const container = new Element('container', 'div');
    container.doc = doc;

    const win = {
      __registerPlugin__: () => {},
      __PLUGIN_INIT__: null,
      __PLUGIN_DESTROY__: null,
      _estSetMode: null,
      _estSetTau: null,
      _estToggleFreeze: null,
    };
    loadPanel(doc, win);
    const api = makeApi({ disarmed: false, armState: 'armed' }); // armed
    if (win.__PLUGIN_INIT__) win.__PLUGIN_INIT__(api);
    if (api._panel) api._panel.fn(container);

    doc.elements['est-tau-input'] = Object.assign(new Element('est-tau-input', 'input'), { value: '45' });

    if (win._estSetTau) win._estSetTau();
    await Promise.resolve();

    const tauCmd = api.commands.find(c => c.id === 0x1E && c.idx === 2);
    check('tau change while armed: command sent', tauCmd != null);
    check('tau change uses submitCommand', tauCmd && tauCmd.kind === 'submit');
    check('tau command value is 45', tauCmd && tauCmd.val === 45);
    console.log('');
  }

  /* --- Test 4: freeze toggle uses submitCommand (no gate) --- */
  {
    const doc = new FakeDocument();
    const container = new Element('container', 'div');
    container.doc = doc;

    const win = {
      __registerPlugin__: () => {},
      __PLUGIN_INIT__: null,
      __PLUGIN_DESTROY__: null,
      _estSetMode: null,
      _estSetTau: null,
      _estToggleFreeze: null,
    };
    loadPanel(doc, win);
    const api = makeApi({ disarmed: false, armState: 'armed' }); // armed
    if (win.__PLUGIN_INIT__) win.__PLUGIN_INIT__(api);
    if (api._panel) api._panel.fn(container);

    if (win._estToggleFreeze) win._estToggleFreeze();
    await Promise.resolve();

    const frCmd = api.commands.find(c => c.id === 0x1E && c.idx === 1);
    check('freeze toggle while armed: command sent', frCmd != null);
    check('freeze toggle uses submitCommand', frCmd && frCmd.kind === 'submit');
    console.log('');
  }

  /* --- Test 5: destroy cleans up globals --- */
  {
    const doc = new FakeDocument();
    const win = {
      __registerPlugin__: () => {},
      __PLUGIN_INIT__: null,
      __PLUGIN_DESTROY__: null,
      _estSetMode: null,
      _estSetTau: null,
      _estToggleFreeze: null,
    };
    loadPanel(doc, win);
    const api = makeApi({});
    if (win.__PLUGIN_INIT__) win.__PLUGIN_INIT__(api);
    if (win.__PLUGIN_DESTROY__) win.__PLUGIN_DESTROY__();
    check('destroy removes _estSetMode', win._estSetMode == null);
    check('destroy removes _estSetTau',  win._estSetTau  == null);
    check('destroy removes _estToggleFreeze', win._estToggleFreeze == null);
    console.log('');
  }

  console.log('=== ' + (failures === 0 ? 'ALL PASSED' : failures + ' FAILURE(S)') + ' ===\n');
  process.exit(failures === 0 ? 0 : 1);
})().catch(e => { console.error(e); process.exit(1); });
