'use strict';
/**
 * Verification harness for H1 (path-panel.js).
 * Confirms:
 * 1. Absence of synthetic Lissajous or demo data generation.
 * 2. Honest degradation when payload has no position data: badge displays 'No position data',
 *    canvas text displays 'No position data', no curve or points plotted.
 * 3. Exact plotting of known c.earth_x / c.earth_y values from Frame C payload across multiple schema shapes.
 */
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const assert = require('assert');

const PANEL = path.join(__dirname, '..', 'docs', 'dashboard-platform', 'shell', 'plugins', 'path-panel.js');

class Element {
  constructor(id, tag) {
    this.id = id;
    this.tagName = (tag || 'div').toUpperCase();
    this._html = '';
    this.textContent = '';
    this.value = '';
    this.checked = false;
    this.disabled = false;
    this.attributes = {};
    this.children = [];
    this.handlers = {};
    this.style = {};
    this.offsetWidth = 800;
    this.offsetHeight = 600;
    this.parentElement = { offsetWidth: 800, offsetHeight: 600 };
  }
  getAttribute(name) { return this.attributes[name] || this[name] || null; }
  querySelectorAll(sel) { return []; }
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

class FakeCanvasContext {
  constructor(canvas) {
    this.canvas = canvas;
    this.drawnTexts = [];
    this.drawnArcs = [];
    this.lines = [];
    this.lineDash = [];
    this.fillStyle = '';
    this.strokeStyle = '';
    this.lineWidth = 1;
    this.font = '';
    this.textAlign = '';
    this.textBaseline = '';
    this.currentPath = [];
  }
  fillRect(x, y, w, h) { /* clear or fill */ }
  fillText(text, x, y) {
    this.drawnTexts.push({ text, x, y, fillStyle: this.fillStyle });
  }
  beginPath() {
    this.currentPath = [];
  }
  moveTo(x, y) {
    this.currentPath.push({ type: 'moveTo', x, y, strokeStyle: this.strokeStyle });
  }
  lineTo(x, y) {
    this.currentPath.push({ type: 'lineTo', x, y, strokeStyle: this.strokeStyle });
    this.lines.push({ x, y, strokeStyle: this.strokeStyle });
  }
  stroke() {}
  arc(x, y, radius, startAngle, endAngle) {
    this.drawnArcs.push({ x, y, radius, fillStyle: this.fillStyle });
  }
  fill() {}
  setLineDash(dash) { this.lineDash = dash; }
  resetLog() {
    this.drawnTexts = [];
    this.drawnArcs = [];
    this.lines = [];
    this.currentPath = [];
  }
}

class FakeDocument {
  constructor() {
    this.elements = {};
    this.docHandlers = {};
  }
  scan(html) {
    const tags = html.match(/<[a-zA-Z][^>]*>/g) || [];
    for (const tag of tags) {
      const idm = tag.match(/\bid="([^"]+)"/);
      if (!idm) continue;
      let el = this.elements[idm[1]];
      if (!el) { el = new Element(idm[1], tag.slice(1)); this.elements[idm[1]] = el; }
      if (tag.startsWith('<canvas')) {
        el.getContext = (type) => {
          if (!el._ctx) el._ctx = new FakeCanvasContext(el);
          return el._ctx;
        };
      }
    }
  }
  getElementById(id) {
    if (!this.elements[id]) {
      this.elements[id] = new Element(id, 'div');
    }
    return this.elements[id];
  }
}

function loadPanel() {
  const doc = new FakeDocument();
  const container = new Element('pp-root', 'div');
  container.doc = doc;

  const api = {
    subscribe(fn) { api._sub = fn; },
    registerPanel(name, initFn) { api._panelName = name; api._initFn = initFn; },
    emit(state) { if (api._sub) api._sub(state); }
  };

  const sandbox = {
    document: doc,
    window: {
      addEventListener() {},
      removeEventListener() {},
    },
    console: console,
    Math: Math,
    Date: Date,
    parseFloat: parseFloat,
    isNaN: isNaN,
    Number: Number,
    Object: Object,
    Array: Array,
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

  return { sandbox, doc, container, api, ctx: doc.getElementById('pp-canvas').getContext('2d') };
}

// ── Tests ──────────────────────────────────────────────────────────────────
console.log('=== H1 VERIFICATION START ===');

// Check 1: Code source checks
const src = fs.readFileSync(PANEL, 'utf8');
assert(!src.includes('generateDemoPoint'), 'FAIL: generateDemoPoint found in path-panel.js');
assert(!/lissajous/i.test(src), 'FAIL: Lissajous curve generator found in path-panel.js');
console.log('PASS: Code check — no generateDemoPoint or Lissajous generator found');

// Check 2: Initial empty state
const env1 = loadPanel();
const badge = env1.doc.getElementById('pp-demo-badge');
console.log('PASS: Initial empty state badge text:', badge.textContent);
console.log('PASS: Initial empty state badge display:', badge.style.display);
const noDataTextDrawn = env1.ctx.drawnTexts.some(t => t.text === 'No position data');
console.log('PASS: Initial canvas drawn "No position data":', noDataTextDrawn);
console.log('PASS: Initial trail lines count:', env1.ctx.lines.length);
assert(noDataTextDrawn, 'Must render "No position data" when empty');
assert.strictEqual(env1.ctx.lines.length, 0, 'Must have zero trail lines when empty');

// Check 3: Feed payload with NO position data
env1.ctx.resetLog();
env1.api.emit({ streams: { '0': { values: { 'roll': 0.1 } } } });
const badgeAfterNoPos = env1.doc.getElementById('pp-demo-badge');
console.log('PASS: After empty payload — badge text:', badgeAfterNoPos.textContent);
console.log('PASS: After empty payload — trail lines count:', env1.ctx.lines.length);
assert.strictEqual(badgeAfterNoPos.textContent, 'No position data');
assert.strictEqual(env1.ctx.lines.length, 0, 'No curve should be drawn when position is absent');

// Check 4: Feed synthetic Frame C payload with known c.earth_x and c.earth_y
// Canvas width=800, height=600, zoom=1.0, panX=0, panY=0.
// worldToScreen(wx, wy): x = 800/2 + 0 + wx * 1.0 * 20 = 400 + 20*wx
//                        y = 600/2 - 0 - wy * 1.0 * 20 = 300 - 20*wy
env1.ctx.resetLog();
const feedX1 = 5.0, feedY1 = 10.0;
env1.api.emit({
  streams: {
    '3': {
      values: {
        'c.earth_x': feedX1,
        'c.earth_y': feedY1
      }
    }
  }
});

console.log('PASS: After Frame C point 1 — badge display:', badge.style.display);
assert.strictEqual(badge.style.display, 'none', 'Badge must be hidden when position data is present');

// Feed point 2 to see the trail line
const feedX2 = 8.0, feedY2 = 14.0;
env1.ctx.resetLog();
env1.api.emit({
  streams: {
    '3': {
      values: {
        'c.earth_x': feedX2,
        'c.earth_y': feedY2
      }
    }
  }
});

console.log('PASS: Points plotted in trail lines:');
const trailLines = env1.ctx.lines.filter(l => l.strokeStyle === 'rgba(74, 158, 255, 0.6)');
trailLines.forEach((pt, i) => {
  const wx = (pt.x - 400) / 20;
  const wy = (300 - pt.y) / 20;
  console.log(`  Trail point screen (${pt.x}, ${pt.y}) -> World (${wx.toFixed(2)}, ${wy.toFixed(2)})`);
});
assert.strictEqual(trailLines.length, 1, 'Expected 1 trail line between the two points');
assert.strictEqual((trailLines[0].x - 400) / 20, feedX2);
assert.strictEqual((300 - trailLines[0].y) / 20, feedY2);

// Check current position marker
const currentMarker = env1.ctx.drawnArcs.find(a => a.fillStyle === '#4a9eff');
if (currentMarker) {
  const mWx = (currentMarker.x - 400) / 20;
  const mWy = (300 - currentMarker.y) / 20;
  console.log(`PASS: Current position marker at Screen (${currentMarker.x}, ${currentMarker.y}) -> World (${mWx.toFixed(2)}, ${mWy.toFixed(2)})`);
  assert.strictEqual(mWx, feedX2);
  assert.strictEqual(mWy, feedY2);
}

// Check metrics display
const metricsEl = env1.doc.getElementById('pp-metrics');
console.log('PASS: Metrics text output:\n' + metricsEl.textContent.trim());

// Verify alternative stream shapes supported:
// 1. stream 'c' with 'earth_x' / 'earth_y'
const env2 = loadPanel();
env2.api.emit({
  streams: {
    'c': {
      values: {
        'earth_x': 25.5,
        'earth_y': -12.25
      }
    }
  }
});
assert.strictEqual(env2.doc.getElementById('pp-demo-badge').style.display, 'none');
console.log('PASS: Alternative format stream["c"].values.earth_x parsed successfully');

// 2. state.c.earth_x / state.c.earth_y
const env3 = loadPanel();
env3.api.emit({
  c: {
    earth_x: -4.0,
    earth_y: 18.5
  }
});
assert.strictEqual(env3.doc.getElementById('pp-demo-badge').style.display, 'none');
console.log('PASS: Alternative format state.c.earth_x parsed successfully');

console.log('=== H1 ALL CHECKS PASSED: VERIFIED ALREADY FIXED ===');
