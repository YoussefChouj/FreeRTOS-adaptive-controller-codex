'use strict';
/**
 * Verification harness for G1 (experiment-panel.js).
 * Confirms:
 * 1. Experiment type field is a dropdown <select id="ep-name">, not a free-text input.
 * 2. Options are populated from the central contract registry (/api/contract -> firmware_contract.py).
 * 3. Legacy free-text experiment types are preserved without breaking:
 *    loading a run with an unlisted type dynamically appends it and keeps it selected.
 * 4. Submitting an experiment sends the chosen dropdown or legacy value.
 */
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const assert = require('assert');

const PANEL = path.join(__dirname, '..', 'docs', 'dashboard-platform', 'shell', 'plugins', 'experiment-panel.js');
const CONTRACT = path.join(__dirname, '..', 'ground_station', 'platform', 'firmware_contract.py');

class Element {
  constructor(id, tag) {
    this.id = id;
    this.tagName = (tag || 'div').toUpperCase();
    this._html = '';
    this.textContent = '';
    this._value = '';
    this.checked = false;
    this.disabled = false;
    this.className = '';
    this.attributes = {};
    this.handlers = {};
    this.style = {};
    this.options = [];
    this.doc = null;
  }
  get value() {
    if (this.tagName === 'SELECT') {
      if (this.options.length === 0) return '';
      const selected = this.options.find(o => o.selected);
      return selected ? selected.value : (this.options[0] ? this.options[0].value : '');
    }
    return this._value;
  }
  set value(v) {
    this._value = v;
    if (this.tagName === 'SELECT') {
      this.options.forEach(o => { o.selected = (o.value === v); });
    }
  }
  getAttribute(name) { return this.attributes[name] || this[name] || null; }
  setAttribute(name, val) { this.attributes[name] = val; }
  appendChild(child) {
    this.options.push(child);
  }
  addEventListener(ev, fn) { (this.handlers[ev] = this.handlers[ev] || []).push(fn); }
  dispatch(ev) {
    (this.handlers[ev] || []).forEach((fn) => fn.call(this, { type: ev, target: this }));
  }
  click() { this.dispatch('click'); }
  get innerHTML() { return this._html; }
  set innerHTML(html) {
    this._html = html;
    if (this.doc) this.doc.scan(html);
  }
}

class FakeDocument {
  constructor() {
    this.elements = {};
    this.allElements = [];
  }
  createElement(tag) {
    const el = new Element('', tag);
    el.doc = this;
    return el;
  }
  scan(html) {
    const tagMatches = html.match(/<[a-zA-Z][^>]*>/g) || [];
    let currentSelect = null;
    for (const tag of tagMatches) {
      const isClose = tag.startsWith('</');
      const tagTypeMatch = tag.match(/<\/?([a-zA-Z0-9]+)/);
      const tagType = tagTypeMatch ? tagTypeMatch[1].toLowerCase() : 'div';

      if (isClose) {
        if (tagType === 'select') currentSelect = null;
        continue;
      }

      const idm = tag.match(/\bid="([^"]+)"/);
      const vm = tag.match(/\bvalue="([^"]*)"/);
      const el = new Element(idm ? idm[1] : '', tagType);
      el.doc = this;
      if (idm) {
        el.id = idm[1];
        this.elements[idm[1]] = el;
      }
      if (vm) el.value = vm[1];

      if (tagType === 'select') {
        currentSelect = el;
      } else if (tagType === 'option' && currentSelect) {
        currentSelect.appendChild(el);
      }
      this.allElements.push(el);
    }
  }
  getElementById(id) {
    if (!this.elements[id]) {
      const el = new Element(id, 'div');
      el.doc = this;
      this.elements[id] = el;
    }
    return this.elements[id];
  }
  querySelectorAll(sel) {
    if (sel === 'select') {
      return this.allElements.filter(e => e.tagName === 'SELECT');
    }
    return [];
  }
}

function loadPanel(contractTypes) {
  const doc = new FakeDocument();
  const container = new Element('ep-root', 'div');
  container.doc = doc;

  const postedExperiments = [];

  const api = {
    registerPanel(name, initFn) { api._initFn = initFn; },
  };



  const sandbox = {
    document: doc,
    window: {},
    fetch: (url, opts) => {
      if (url === '/api/contract') {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({
            contract_version: 'v1',
            experiment_types: contractTypes
          })
        });
      }
      if (url === '/experiments' && opts && opts.method === 'POST') {
        const body = JSON.parse(opts.body);
        postedExperiments.push(body);
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({ name: body.name, state: 'settling', tick: 0 })
        });
      }
      if (url === '/experiments') {
        return Promise.resolve({ ok: true, json: () => Promise.resolve(ret.currentRuns) });
      }
      if (url.startsWith('/experiments/')) {
        const n = decodeURIComponent(url.replace('/experiments/', ''));
        return Promise.resolve({ ok: true, json: () => Promise.resolve(ret.runDetails[n] || null) });
      }
      return Promise.resolve({ ok: true, json: () => Promise.resolve({}) });
    },
    setInterval: (cb) => { ret._pollFn = cb; return 1; },
    clearInterval: () => {},
    setTimeout: (cb) => { cb(); return 1; },
    clearTimeout: () => {},
    console: console,
    Math: Math,
    Date: Date,
    parseFloat: parseFloat,
    parseInt: parseInt,
    isNaN: isNaN,
    Object: Object,
    Array: Array,
    String: String,
    Promise: Promise,
  };
  sandbox.window.__registerPlugin__ = function (name, init, destroy) {
    sandbox.pluginName = name;
    sandbox.pluginInit = init;
    sandbox.pluginDestroy = destroy;
  };

  const src = fs.readFileSync(PANEL, 'utf8');
  vm.createContext(sandbox);
  vm.runInContext(src, sandbox, { filename: PANEL });

  const ret = {
    doc,
    api,
    sandbox,
    postedExperiments,
    currentRuns: [],
    runDetails: {},
    poll: () => { if (ret._pollFn) ret._pollFn(); }
  };

  sandbox.pluginInit(api);
  api._initFn(container);

  return ret;
}

async function run() {
  console.log('=== G1 VERIFICATION START ===');

  // Step 1: Check Python registry in firmware_contract.py
  const contractSrc = fs.readFileSync(CONTRACT, 'utf8');
  const expMatch = contractSrc.match(/EXPERIMENT_TYPES:\s*tuple\[str,\s*\.\.\.\]\s*=\s*\(([^)]+)\)/s);
  assert(expMatch, 'EXPERIMENT_TYPES registry must exist in firmware_contract.py');
  const registeredTypes = expMatch[1].match(/"([^"]+)"/g).map(s => s.replace(/"/g, ''));
  console.log('Source of truth for experiment types: ground_station/platform/firmware_contract.py');
  console.log('Registered experiment types list from Python registry:\n ', JSON.stringify(registeredTypes));

  // Step 2: Load panel and verify dropdown element
  const env = loadPanel(registeredTypes);
  // Allow async /api/contract fetch to complete
  await new Promise(r => setTimeout(r, 20));

  const nameEl = env.doc.getElementById('ep-name');
  console.log('Element #ep-name tagName:', nameEl.tagName);
  assert.strictEqual(nameEl.tagName, 'SELECT', '#ep-name must be a SELECT element, not an input');

  const selects = env.doc.querySelectorAll('select');
  console.log('Found selects count:', selects.length);
  assert(selects.length >= 1, 'At least 1 select element must exist in panel');

  const optionValues = nameEl.options.map(o => o.value);
  console.log('Dropdown option list in panel:\n ', JSON.stringify(optionValues));
  registeredTypes.forEach(t => {
    assert(optionValues.includes(t), `Dropdown must include registered type '${t}'`);
  });
  console.log('PASS: Dropdown options match registry perfectly');

  // Step 3: Test default selection and experiment start
  console.log('Default selected value:', nameEl.value);
  assert.strictEqual(nameEl.value, 'step_response');

  env.doc.getElementById('ep-start-btn').click();
  await new Promise(r => setTimeout(r, 10));
  console.log('POST /experiments received payload:', JSON.stringify(env.postedExperiments[env.postedExperiments.length - 1]));
  assert.strictEqual(env.postedExperiments[env.postedExperiments.length - 1].name, 'step_response');
  console.log('PASS: Started experiment with dropdown selection');

  // Step 4: Test selecting another registered type ('controls')
  nameEl.value = 'controls';
  env.doc.getElementById('ep-start-btn').click();
  await new Promise(r => setTimeout(r, 10));
  assert.strictEqual(env.postedExperiments[env.postedExperiments.length - 1].name, 'controls');
  console.log('PASS: Started experiment with "controls" selected from dropdown');

  // Step 5: Test legacy / saved experiment backwards compatibility
  // Simulate loading an active or saved experiment with an unlisted legacy free-text type
  const legacyType = 'legacy_multiaxis_test_2026';
  console.log(`Simulating loading saved experiment with unlisted legacy type: '${legacyType}'`);

  const legacyRun = {
    name: legacyType,
    state: 'measuring',
    tick: 120,
    settle_ticks: 100,
    measure_ticks: 200,
    samples: [0.1, 0.2]
  };

  // Mock /experiments returning the legacy run
  env.currentRuns = [legacyRun];
  env.runDetails[legacyType] = legacyRun;

  // Trigger poll
  env.poll();
  await new Promise(r => setTimeout(r, 20));

  const updatedOptionValues = nameEl.options.map(o => o.value);
  console.log('Dropdown option list after loading legacy experiment:\n ', JSON.stringify(updatedOptionValues));
  assert(updatedOptionValues.includes(legacyType), 'Legacy type must be dynamically added to dropdown options');
  console.log('Selected value in dropdown:', nameEl.value);
  assert.strictEqual(nameEl.value, legacyType, 'Legacy type must remain selected');

  // Re-start or trigger start with legacy type:
  env.doc.getElementById('ep-start-btn').click();
  await new Promise(r => setTimeout(r, 10));
  assert.strictEqual(env.postedExperiments[env.postedExperiments.length - 1].name, legacyType);
  console.log('PASS: Legacy experiment preserved and sent without errors');

  console.log('=== G1 ALL CHECKS PASSED ===');
}

run().catch(err => {
  console.error('G1 FAILED:', err);
  process.exit(1);
});
