'use strict';
/**
 * Verification harness for C2 (bandwidth-panel.js).
 * Confirms:
 * 1. Slot 0 is protected: attempting to delete slot 0 is refused outright,
 *    leaves slot 0 untouched, and emits no wire unsubscribe.
 * 2. Deleting slot 1 or 2 sends a real unsubscribe request with divider=0.
 * 3. Deleted slot stays deleted across schema/state refresh (onState & getState).
 */
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const assert = require('assert');

const PANEL = path.join(__dirname, '..', 'docs', 'dashboard-platform', 'shell', 'plugins', 'bandwidth-panel.js');

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
    this.attributes = {};
    this.handlers = {};
    this.style = {};
    this.classList = {
      _classes: new Set(),
      add: (c) => this.classList._classes.add(c),
      remove: (c) => this.classList._classes.delete(c),
      contains: (c) => this.classList._classes.has(c),
    };
    this.doc = null;
  }
  getAttribute(name) { return this.attributes[name] || this[name] || null; }
  setAttribute(name, val) { this.attributes[name] = val; }
  querySelectorAll(sel) {
    if (!this.doc) return [];
    return this.doc.querySelectorAll(sel);
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
  scan(html) {
    const tagMatches = html.match(/<[a-zA-Z][^>]*>/g) || [];
    for (const tag of tagMatches) {
      const idm = tag.match(/\bid="([^"]+)"/);
      const classm = tag.match(/\bclass="([^"]+)"/);
      const datam = tag.match(/\bdata-slot="([^"]+)"/);
      const dism = /\bdisabled\b/.test(tag);
      const el = new Element(idm ? idm[1] : '', tag.match(/<([a-zA-Z0-9]+)/)[1]);
      el.doc = this;
      if (idm) {
        el.id = idm[1];
        this.elements[idm[1]] = el;
      }
      if (classm) {
        el.className = classm[1];
        classm[1].split(/\s+/).forEach(c => el.classList.add(c));
      }
      if (datam) el.setAttribute('data-slot', datam[1]);
      if (dism) el.disabled = true;
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
    if (sel.startsWith('.')) {
      const cls = sel.slice(1);
      return this.allElements.filter(e => e.classList.contains(cls));
    }
    return [];
  }
}

function loadPanel(sentRequests) {
  const doc = new FakeDocument();
  const container = new Element('bw-root', 'div');
  container.doc = doc;

  let stateCb = null;
  let currentState = null;

  const api = {
    subscribe(fn) { stateCb = fn; },
    getState() { return currentState; },
    registerPanel(name, initFn) { api._initFn = initFn; },
    unsubscribeSlot(slot) {
      sentRequests.push({ action: 'unsubscribeSlot', slot });
      return Promise.resolve({ ok: true, slot: slot });
    },
    subscribeSlot(slot, divider, ranges) {
      sentRequests.push({ action: 'subscribeSlot', slot, divider, ranges });
      return Promise.resolve({ ok: true, slot: slot, divider: divider });
    }
  };

  const sandbox = {
    document: doc,
    window: {
      __gs_shell_api__: api,
      __gs_plugins_refresh__: () => { sentRequests.push({ action: 'plugins_refresh' }); },
    },
    fetch: (url, opts) => {
      const body = opts && opts.body ? JSON.parse(opts.body) : {};
      sentRequests.push({ action: 'fetch', url, body });
      return Promise.resolve({
        ok: true,
        status: 202,
        json: () => Promise.resolve({ ok: true, slot: body.slot, divider: body.divider })
      });
    },
    requestAnimationFrame: (cb) => { cb(); },
    setTimeout: (cb, ms) => { cb(); return 1; },
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

  sandbox.pluginInit(api);
  api._initFn(container);

  return {
    doc,
    api,
    sandbox,
    emitState(st) {
      currentState = st;
      if (stateCb) stateCb(st);
    }
  };
}

async function run() {
  console.log('=== C2 VERIFICATION START ===');
  const sentRequests = [];
  const env = loadPanel(sentRequests);

  // 1. Ingest state with slot 0 (boot telemetry) and slot 1 (user stream)
  const baseState = {
    streams: {
      '0': {
        received: 100,
        dropped: 0,
        sequence: 100,
        loss_pct: 0.0,
        values: { 'slot0.seq': 100, 'slot0.t_ms': 1000, 'roll': 0.05, 'pitch': -0.02 }
      },
      '1': {
        received: 50,
        dropped: 0,
        sequence: 50,
        loss_pct: 0.0,
        values: { 'slot1.seq': 50, 'slot1.t_ms': 1000, 'motor1': 1200 }
      }
    }
  };

  env.emitState(baseState);

  // Inspect rendered table
  const tbody = env.doc.getElementById('bw-stream-tbody');
  console.log('Initial stream table HTML rendered:\n' + tbody.innerHTML);
  assert(tbody.innerHTML.includes('Slot 0'), 'Slot 0 must be present in table');
  assert(tbody.innerHTML.includes('Slot 1'), 'Slot 1 must be present in table');

  // Check slot 0 action button: must be locked / disabled
  const removeButtons = env.doc.querySelectorAll('.bw-remove-btn');
  const slot0Btn = removeButtons.find(b => b.getAttribute('data-slot') === '0');
  const slot1Btn = removeButtons.find(b => b.getAttribute('data-slot') === '1');

  assert(slot0Btn, 'Slot 0 button must exist');
  assert(slot0Btn.disabled, 'Slot 0 remove button must be disabled');
  assert(slot1Btn, 'Slot 1 button must exist');
  assert(!slot1Btn.disabled, 'Slot 1 remove button must be enabled');
  console.log('PASS: Slot 0 remove button is disabled (locked)');

  // 2. Test safety: attempt to click remove on slot 0
  console.log('Attempting delete on slot 0...');
  slot0Btn.click();
  const warningEl = env.doc.getElementById('bw-budget-warning');
  console.log('Warning element text after slot 0 delete attempt:', warningEl.innerHTML);
  assert(warningEl.innerHTML.includes('Refused: Slot 0 carries flight telemetry'), 'Must show explicit refusal warning for slot 0');
  assert.strictEqual(sentRequests.length, 0, 'Must NOT send any network/unsubscribe request for slot 0');
  console.log('PASS: Attempt to delete slot 0 was refused outright; 0 requests sent');

  // 3. Test deleting slot 1
  console.log('Deleting slot 1...');
  slot1Btn.click();
  // Wait microtask for promise resolution
  await new Promise(r => setTimeout(r, 10));

  console.log('Sent requests after slot 1 delete:', JSON.stringify(sentRequests));
  assert(sentRequests.some(r => (r.action === 'unsubscribeSlot' && r.slot === 1) || (r.action === 'fetch' && r.body.slot === 1 && r.body.divider === 0)),
    'Must send unsubscribe request for slot 1 with divider=0');
  console.log('PASS: Unsubscribe request sent for slot 1');

  console.log('Table after deleting slot 1:\n' + env.doc.getElementById('bw-stream-tbody').innerHTML);
  assert(!env.doc.getElementById('bw-stream-tbody').innerHTML.includes('Slot 1</td>'), 'Slot 1 must be removed from table');

  // 4. Test refresh stability: simulate operator clicking Refresh or receiving state update
  // The server state might still have slot 1 in baseState if stale, but local panel must keep it deleted
  console.log('Testing refresh stability (re-feeding baseState with slot 1 still present)...');
  env.emitState(baseState);
  const tbodyAfterRefresh = env.doc.getElementById('bw-stream-tbody');
  console.log('Table after refresh:\n' + tbodyAfterRefresh.innerHTML);
  assert(!tbodyAfterRefresh.innerHTML.includes('Slot 1</td>'), 'Slot 1 must remain deleted across refresh!');
  assert(tbodyAfterRefresh.innerHTML.includes('Slot 0'), 'Slot 0 must remain present across refresh');
  console.log('PASS: Deleted slot stays deleted across state refresh');

  // 5. Test fetch fallback path (when api.unsubscribeSlot is absent)
  console.log('Testing direct fetch fallback when api.unsubscribeSlot is missing...');
  const sentRequests2 = [];
  const env2 = loadPanel(sentRequests2);
  // delete api.unsubscribeSlot and api.subscribeSlot
  delete env2.api.unsubscribeSlot;
  delete env2.api.subscribeSlot;
  env2.emitState(baseState);
  const removeButtons2 = env2.doc.querySelectorAll('.bw-remove-btn');
  const slot1Btn2 = removeButtons2.find(b => b.getAttribute('data-slot') === '1');
  slot1Btn2.click();
  await new Promise(r => setTimeout(r, 10));
  console.log('Sent requests via fetch:', JSON.stringify(sentRequests2));
  assert(sentRequests2.some(r => r.action === 'fetch' && r.url === '/subscribe' && r.body.slot === 1 && r.body.divider === 0),
    'Fetch fallback must POST to /subscribe with slot=1 and divider=0');
  console.log('PASS: Direct fetch fallback verified');

  console.log('=== C2 ALL CHECKS PASSED ===');
}

run().catch(err => {
  console.error('C2 FAILED:', err);
  process.exit(1);
});
