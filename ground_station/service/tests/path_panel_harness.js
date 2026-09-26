'use strict';
/**
 * Offline verification harness for path-panel.js (Path Planning),
 * task 20260921-141231. Covers:
 *   1. Honest "No position data" state when no c.earth_x/c.earth_y keys
 *      are published (canvas text + badge, no fake position).
 *   2. Position rendered from Frame C c.earth_x/c.earth_y; trail + metrics.
 *   3. Waypoint add / delete via the real table handlers.
 *   4. Set Home / Set Target markers.
 *   5. Zoom in / out / reset (verified through marker screen coordinates).
 *   6. "Mission payload" guard: fetch is a recording stub that never
 *      touches the network; the current panel source contains no fetch
 *      and the stub must record ZERO sends. Any future mission-send code
 *      is therefore captured here instead of POSTing to a live service.
 *
 * The sandbox is given no `require`, no http modules and only stubbed
 * fetch/XHR, so a real network call is structurally impossible.
 *
 * Run:  node ground_station/service/tests/path_panel_harness.js
 * Exit code 0 iff every check passes.
 */
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const assert = require('assert');

const PANEL = path.join(__dirname, '..', '..', '..',
  'docs', 'dashboard-platform', 'shell', 'plugins', 'path-panel.js');

// ── Recording 2D canvas context ──────────────────────────────────────────
function makeCtx() {
  return {
    fillStyle: '', strokeStyle: '', font: '', textAlign: '',
    textBaseline: '', lineWidth: 1,
    texts: [], arcs: [], strokes: [], dashes: [],
    fillRect() {},
    beginPath() { this._curPath = []; },
    moveTo(x, y) { this._curPath = [{ x, y }]; },
    lineTo(x, y) { if (this._curPath) this._curPath.push({ x, y }); },
    stroke() {
      this.strokes.push({
        path: this._curPath ? this._curPath.slice() : [],
        strokeStyle: this.strokeStyle,
        lineWidth: this.lineWidth,
        dash: this._curDash ? this._curDash.slice() : []
      });
    },
    fill() {},
    setLineDash(d) { this._curDash = d ? d.slice() : []; this.dashes.push(d); },
    fillText(text, x, y) { this.texts.push({ text, x, y }); },
    arc(x, y, r) { this.arcs.push({ x, y, r, fill: this.fillStyle }); },
    reset() { this.texts = []; this.arcs = []; this.strokes = []; this.dashes = []; this._curPath = []; },
  };
}

// ── Fake DOM ──────────────────────────────────────────────────────────────
class Element {
  constructor(id, tag) {
    this.id = id;
    this.tagName = (tag || 'div').toUpperCase();
    this._html = '';
    this.textContent = '';
    this.value = '';
    this.checked = false;
    this.className = '';
    this.handlers = {};
    this.style = {};
    this.dataset = {};
    this.doc = null;
    // Fixed stub size for the canvas wrap (offsetWidth/Height would come
    // from layout in a real browser).
    this.parentElement = { offsetWidth: 600, offsetHeight: 400 };
    this._nodeCache = {};
  }
  addEventListener(ev, fn) {
    (this.handlers[ev] = this.handlers[ev] || []).push(fn);
  }
  getAttribute(name) {
    return this.dataset[name] !== undefined ? this.dataset[name] : null;
  }
  setAttribute(name, val) { this.dataset[name] = String(val); }
  getContext() { return this.doc.ctx; }
  get innerHTML() { return this._html; }
  set innerHTML(html) {
    this._html = html;
    this._nodeCache = {};   // old child nodes are destroyed by replacement
    if (this.doc) this.doc.scan(html);
  }
  /* Parse class-bearing tags out of the current innerHTML and build
   * fresh elements (real innerHTML replacement also builds new nodes). */
  querySelectorAll(cls) {
    const dot = cls.replace(/^\./, '');
    const re = /<[a-zA-Z0-9-]+[^>]*>/g;
    const out = [];
    let m;
    while ((m = re.exec(this._html)) !== null) {
      const tag = m[0];
      if (!new RegExp('class="[^"]*\\b' + dot + '\\b[^"]*"').test(tag)) continue;
      // Stable node identity while the markup is unchanged: the panel binds
      // handlers to elements from its own querySelectorAll parse; later
      // parses from the test must reach those same elements.
      let el = this._nodeCache[tag];
      if (!el) {
        const idm = tag.match(/\bid="([^"]+)"/);
        el = new Element(idm ? idm[1] : null,
          tag.slice(1).split(/[\s>]/)[0]);
        this._nodeCache[tag] = el;
      }
      const dm = tag.match(/\bdata-index="([^"]+)"/);
      if (dm) el.dataset.index = dm[1];
      const dam = tag.match(/\bdata-axis="([^"]+)"/);
      if (dam) el.dataset.axis = dam[1];
      const vm2 = tag.match(/\bvalue="([^"]*)"/);
      if (vm2) el.value = vm2[1];
      out.push(el);
    }
    return out;
  }
}

class FakeDocument {
  constructor(ctx) {
    this.elements = {};
    this.ctx = ctx;
  }
  scan(html) {
    const tags = html.match(/<[a-zA-Z0-9-]+[^>]*>/g) || [];
    for (const tag of tags) {
      const idm = tag.match(/\bid="([^"]+)"/);
      if (!idm) continue;
      const id = idm[1];
      // innerHTML replacement creates new nodes in a real DOM; replace so
      // handlers rebound by the panel don't stack on a stale element.
      const el = new Element(id, tag.slice(1).split(/[\s>]/)[0]);
      el.doc = this;
      this.elements[id] = el;
      const textMatch = html.match(
        new RegExp('<[a-zA-Z0-9-]+[^>]*\\bid="' + id + '"[^>]*>([^<]*)<'));
      if (textMatch) el.textContent = textMatch[1];
    }
  }
  getElementById(id) { return this.elements[id] || null; }
}

// ── Panel loading ─────────────────────────────────────────────────────────
function loadPanel() {
  const ctx = makeCtx();
  const doc = new FakeDocument(ctx);
  const container = new Element('container', 'div');
  container.doc = doc;
  const api = {
    stateCb: null,
    subscribe(cb) { this.stateCb = cb; },
    registerPanel(name, renderFn) { this.renderFn = renderFn; },
  };
  const fetchRecords = [];
  let xhrBuilt = 0;
  const sandbox = {
    document: doc,
    console: { log() {}, warn() {}, error() {} },
    Date, Math, Number, String, Boolean, Array, Object, JSON, isNaN,
    setInterval, clearInterval, setTimeout, clearTimeout,
    // fetch: recording stub; resolves without touching any socket.
    fetch(url, opts) {
      fetchRecords.push({ url, opts });
      return Promise.resolve({
        ok: true, status: 200,
        json: () => Promise.resolve({}),
        text: () => Promise.resolve(''),
      });
    },
    XMLHttpRequest: function () {
      xhrBuilt += 1;
      throw new Error('XMLHttpRequest is blocked in this harness');
    },
  };
  sandbox.handlers = {};
  sandbox.addEventListener = function (ev, fn) {
    (this.handlers[ev] = this.handlers[ev] || []).push(fn);
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
    doc, ctx, api, sandbox,
    fetchRecords: () => fetchRecords.slice(),
    xhrCount: () => xhrBuilt,
    feed(state) { api.stateCb(state); },
    click(id) {
      const el = doc.getElementById(id);
      assert.ok(el && el.handlers.click, 'no click handler on #' + id);
      el.handlers.click.forEach((fn) => fn.call(el));
    },
    input(id, val) {
      const el = doc.getElementById(id);
      assert.ok(el && el.handlers.input, 'no input handler on #' + id);
      el.value = String(val);
      el.handlers.input.forEach((fn) => fn.call(el));
    },
    destroy() { sandbox.pluginDestroy(); },
  };
}

function frameC(x, y, extra) {
  const values = Object.assign({}, extra);
  if (x !== undefined) values['c.earth_x'] = x;
  if (y !== undefined) values['c.earth_y'] = y;
  return { connected: true, streams: { '3': { values } } };
}

function frameTelemetry(actualX, actualY, desiredX, desiredY, extra) {
  const values = Object.assign({}, extra);
  if (actualX !== undefined) values['c.earth_x'] = actualX;
  if (actualY !== undefined) values['c.earth_y'] = actualY;
  if (desiredX !== undefined) values['pid.locx.Des'] = desiredX;
  if (desiredY !== undefined) values['pid.locy.Des'] = desiredY;
  return { connected: true, streams: { '3': { values } } };
}

function metricTile(html, label) {
  const m = html.match(new RegExp(
    label + '</span><span class="pp-metric-value">([^<]*)</span>'));
  return m ? m[1] : null;
}

function clickEl(el) {
  el.handlers.click.forEach((fn) => fn.call(el));
}

function runChecks() {
  console.log('--- PATH PANEL OFFLINE CHECKS ---');

  // 1. No position keys: honest "No position data"
  {
    console.log('\n[CHECK 1: no keys -> "No position data", badge visible]');
    const env = loadPanel();
    env.feed(frameC(undefined, undefined, { 'c.altitude': 1.5 }));
    const badge = env.doc.getElementById('pp-demo-badge');
    assert.strictEqual(badge.textContent, 'No position data');
    assert.strictEqual(badge.style.display, 'block');
    assert.ok(env.ctx.texts.some((t) => t.text === 'No position data'),
      'canvas must draw the no-data text');
    const dist = metricTile(env.doc.getElementById('pp-metrics').innerHTML,
      'Distance');
    assert.strictEqual(dist, '—');   // no fake distance without a position
    console.log('  PASS: canvas + badge read "No position data"; distance tile honest');
    env.destroy();
  }

  // 2. Position from c.earth_x / c.earth_y
  {
    console.log('\n[CHECK 2: position rendered from c.earth_x/c.earth_y]');
    const env = loadPanel();
    env.feed(frameC(1000, 2000));
    env.ctx.reset();            // ignore pre-position no-data renders
    env.feed(frameC(1200, 2400));
    assert.ok(!env.ctx.texts.some((t) => t.text === 'No position data'),
      'no-data text must be gone once position arrives');
    assert.strictEqual(env.doc.getElementById('pp-demo-badge').style.display,
      'none');
    let html = env.doc.getElementById('pp-metrics').innerHTML;
    assert.strictEqual(metricTile(html, 'Trail Points'), '2');
    const dist = metricTile(html, 'Distance');
    assert.ok(/^4\.47 m$/.test(dist), 'distance sqrt(2^2+4^2)=4.47, got ' + dist);
    console.log('  PASS: trail 2 points, distance ' + dist + ', badge hidden');
    env.destroy();
  }

  // 3. Waypoint add / delete
  {
    console.log('\n[CHECK 3: waypoint add / delete]');
    const env = loadPanel();
    const table = env.doc.getElementById('pp-wp-table');

    env.click('pp-add-wp');
    let inputs = table.querySelectorAll('.pp-wp-input');
    assert.strictEqual(inputs.length, 2, 'one wp -> x and y inputs');
    assert.strictEqual(inputs[0].value, '2.00');
    assert.strictEqual(inputs[1].value, '2.00');
    assert.strictEqual(table.querySelectorAll('.pp-wp-del').length, 1);
    console.log('  PASS: add -> waypoint (2.00, 2.00) with delete button');

    env.click('pp-add-wp');
    inputs = table.querySelectorAll('.pp-wp-input');
    assert.strictEqual(inputs.length, 4, 'two wps -> four inputs');
    assert.strictEqual(inputs[2].value, '4.00');
    console.log('  PASS: second add -> waypoint (4.00, 4.00)');

    // Delete the first row; (4,4) must remain.
    const del0 = table.querySelectorAll('.pp-wp-del')[0];
    clickEl(del0);
    inputs = table.querySelectorAll('.pp-wp-input');
    assert.strictEqual(inputs.length, 2);
    assert.strictEqual(inputs[0].value, '4.00');
    assert.strictEqual(inputs[1].value, '4.00');
    console.log('  PASS: delete row 1 -> only (4.00, 4.00) remains');

    // The Waypoints metric tile refreshes on the next telemetry sample
    // (the panel does not call renderMetrics from the table handlers).
    env.feed(frameC(0, 0));
    const tile = metricTile(
      env.doc.getElementById('pp-metrics').innerHTML, 'Waypoints');
    assert.strictEqual(tile, '1');
    console.log('  PASS: Waypoints tile shows 1 after next telemetry sample');
    env.destroy();
  }

  // 4. Set Home / Set Target
  {
    console.log('\n[CHECK 4: Set Home / Set Target markers]');
    const env = loadPanel();
    env.feed(frameC(100, 0));

    env.ctx.reset();
    env.click('pp-set-home');
    assert.ok(env.ctx.texts.some((t) => t.text === 'H'),
      'home marker label H drawn');
    console.log('  PASS: Set Home draws the H marker at current position');

    env.ctx.reset();
    env.click('pp-set-target');
    assert.ok(env.ctx.texts.some((t) => t.text === 'T'),
      'target marker label T drawn');
    assert.ok(!env.ctx.texts.some((t) => t.text === 'H') === false,
      'sanity: render redraws both markers');
    console.log('  PASS: Set Target draws the T marker; render redraws home too');
    env.destroy();
  }

  // 5. Zoom in / out / reset
  {
    console.log('\n[CHECK 5: zoom in / out / reset via marker screen x]');
    const env = loadPanel();
    env.feed(frameC(100, 0));   // screen x at zoom 1: 300 + 1*20 = 320

    function curDotX() {
      const dots = env.ctx.arcs.filter((a) => a.r === 8);
      assert.ok(dots.length, 'current-position r=8 dot must be drawn');
      return dots[dots.length - 1].x;
    }
    env.ctx.reset();
    env.click('pp-zoom-in');
    assert.strictEqual(curDotX(), 324);     // zoom 1.2
    console.log('  PASS: zoom in  -> zoom 1.2, dot x 324');

    env.ctx.reset();
    env.click('pp-zoom-out');
    assert.strictEqual(curDotX(), 320);     // back to 1.0
    env.ctx.reset();
    env.click('pp-zoom-out');
    assert.strictEqual(curDotX(), 316);     // 0.8
    console.log('  PASS: zoom out -> 1.0 then 0.8, dot x 320 then 316');

    env.ctx.reset();
    env.click('pp-reset-view');
    assert.strictEqual(curDotX(), 320);
    console.log('  PASS: reset view -> zoom 1.0, dot x 320');
    env.destroy();
  }

  // 6. Mission payload guard — fetch stub records every send
  {
    console.log('\n[CHECK 6: mission payload captured by fetch stub, zero sends]');
    const env = loadPanel();
    env.feed(frameC(100, 0));
    env.click('pp-add-wp');
    env.click('pp-set-home');
    env.click('pp-set-target');
    env.click('pp-zoom-in');
    const records = env.fetchRecords();
    assert.strictEqual(records.length, 0,
      'panel must not send a mission; recorded: ' + JSON.stringify(records));
    assert.strictEqual(env.xhrCount(), 0, 'no XMLHttpRequest may be built');
    // Structural guarantee: the sandbox has no require / net modules.
    assert.strictEqual(env.sandbox.require, undefined);
    // And the shipped source currently contains no fetch call at all.
    const src = fs.readFileSync(PANEL, 'utf8');
    assert.strictEqual(src.indexOf('fetch('), -1);
    console.log('  PASS: zero fetch/XHR sends after every interaction; no fetch in source;');
    console.log('        sandbox has no require — a real network call is structurally impossible');
    env.destroy();
  }

  // 7. Desired trace from telemetry (normalized cm->m, distinct dashed line, waypoints separate)
  {
    console.log('\n[CHECK 7: desired trace from telemetry, normalized cm->m, waypoints separate plan overlay]');
    const env = loadPanel();
    // Feed 2 samples with actual and desired (in cm)
    env.feed(frameTelemetry(1000, 2000, 1200, 2400, { 'c.altitude': 2.0, 'pid.z_pos.Des': 2.5 }));
    env.feed(frameTelemetry(1100, 2200, 1300, 2600, { 'c.altitude': 2.0, 'pid.z_pos.Des': 2.5 }));

    // Verify desired dashed stroke was rendered
    const hasDesiredStroke = env.ctx.strokes.some((s) =>
      s.strokeStyle.indexOf('255, 170, 0') !== -1 && s.dash && s.dash.length === 2 && s.dash[0] === 4
    );
    assert.ok(hasDesiredStroke, 'desired trace must be drawn with dashed orange line');

    // Verify actual stroke was rendered in blue
    const hasActualStroke = env.ctx.strokes.some((s) =>
      s.strokeStyle.indexOf('74, 158, 255') !== -1
    );
    assert.ok(hasActualStroke, 'actual trail must be drawn with solid blue line');

    // Add hand-placed waypoint -> should NOT affect desired trace or actual trail points
    env.click('pp-add-wp');
    const table = env.doc.getElementById('pp-wp-table');
    assert.strictEqual(table.querySelectorAll('.pp-wp-input').length, 2, 'one hand-placed waypoint in table');
    env.feed(frameTelemetry(1100, 2200, 1300, 2600, { 'c.altitude': 2.0, 'pid.z_pos.Des': 2.5 }));
    const html = env.doc.getElementById('pp-metrics').innerHTML;
    assert.strictEqual(metricTile(html, 'Waypoints'), '1', 'hand-placed waypoints remain separate plan overlay');
    assert.strictEqual(metricTile(html, 'Trail Points'), '3', 'actual trail points count');

    console.log('  PASS: desired trace from telemetry rendered in distinct dashed style; waypoints remain separate plan overlay');
    env.destroy();
  }

  // 8. Tracking metrics (RMS, max, per-axis RMS) and scrubbing recording
  {
    console.log('\n[CHECK 8: tracking metrics RMS/max/per-axis and scrubbing recording]');
    const env = loadPanel();
    // Known values:
    // Sample 1: actual = (10, 20, 1) m, desired = (7, 20, 1) m -> dx=3, dy=0, dz=0. 3D error = 3 m.
    // Sample 2: actual = (10, 20, 1) m, desired = (10, 16, 1) m -> dx=0, dy=4, dz=0. 3D error = 4 m.
    // N=2:
    // RMS X = sqrt((9+0)/2) = 2.12 m
    // RMS Y = sqrt((0+16)/2) = 2.83 m
    // RMS Z = 0.00 m
    // Max Error = 4.00 m
    // RMS Error = sqrt((9+16)/2) = sqrt(12.5) = 3.54 m
    env.feed(frameTelemetry(1000, 2000, 700, 2000, { 'c.altitude': 1.0, 'pid.z_pos.Des': 1.0 }));
    env.feed(frameTelemetry(1000, 2000, 1000, 1600, { 'c.altitude': 1.0, 'pid.z_pos.Des': 1.0 }));

    let html = env.doc.getElementById('pp-metrics').innerHTML;
    assert.strictEqual(metricTile(html, 'RMS Error'), '3.54 m', 'RMS error must be 3.54 m');
    assert.strictEqual(metricTile(html, 'Max Error'), '4.00 m', 'Max error must be 4.00 m');
    assert.strictEqual(metricTile(html, 'RMS X'), '2.12 m', 'RMS X must be 2.12 m');
    assert.strictEqual(metricTile(html, 'RMS Y'), '2.83 m', 'RMS Y must be 2.83 m');
    assert.strictEqual(metricTile(html, 'RMS Z'), '0.00 m', 'RMS Z must be 0.00 m');
    console.log('  PASS: tracking metrics RMS/max/per-axis match known answers: RMS=3.54, Max=4.00, X=2.12, Y=2.83, Z=0.00');

    // Test scrubbing to index 0: only sample 1 is evaluated
    env.input('pp-replay-scrub', 0);
    html = env.doc.getElementById('pp-metrics').innerHTML;
    assert.strictEqual(metricTile(html, 'RMS Error'), '3.00 m', 'Scrubbed to sample 0 RMS error must be 3.00 m');
    assert.strictEqual(metricTile(html, 'Max Error'), '3.00 m', 'Scrubbed to sample 0 Max error must be 3.00 m');
    assert.strictEqual(metricTile(html, 'RMS X'), '3.00 m', 'Scrubbed to sample 0 RMS X must be 3.00 m');
    assert.strictEqual(metricTile(html, 'RMS Y'), '0.00 m', 'Scrubbed to sample 0 RMS Y must be 0.00 m');
    assert.strictEqual(metricTile(html, 'RMS Z'), '0.00 m', 'Scrubbed to sample 0 RMS Z must be 0.00 m');
    assert.strictEqual(metricTile(html, 'Trail Points'), '1', 'Scrubbed trail points must be 1');
    console.log('  PASS: scrubbing recording updates metrics correctly on prefix of data');

    // Scrub back to sample 1
    env.input('pp-replay-scrub', 1);
    html = env.doc.getElementById('pp-metrics').innerHTML;
    assert.strictEqual(metricTile(html, 'RMS Error'), '3.54 m');
    assert.strictEqual(metricTile(html, 'Max Error'), '4.00 m');
    assert.strictEqual(metricTile(html, 'Trail Points'), '2');
    console.log('  PASS: scrub restore recovers full dataset metrics');

    env.destroy();
  }

  // 9. render3D only calls helpers visible from its own scope. The harness has no WebGL,
  // so render3D never runs here; a helper nested inside initThreeScene threw a
  // ReferenceError on every frame in the browser (blank 3D view).
  {
    console.log('\n[CHECK 9: render3D helpers declared at plugin scope]');
    const src = fs.readFileSync(PANEL, 'utf8');
    const start = src.indexOf('  function render3D()');
    assert.ok(start >= 0, 'render3D not found');
    const end = src.indexOf('\n  }\n', start);
    const body = src.slice(start, end);
    const called = new Set((body.match(/\b([A-Za-z_]\w*)\s*\(/g) || []).map((m) => m.replace(/\s*\($/, '')));
    const decl = /^( *)function ([A-Za-z_]\w*)\s*\(/gm;
    let m;
    while ((m = decl.exec(src)) !== null) {
      if (called.has(m[2]) && m[2] !== 'render3D') {
        assert.strictEqual(m[1].length, 2,
          'render3D calls ' + m[2] + ' but it is declared at indent ' + m[1].length + ' (not plugin scope)');
      }
    }
    console.log('  PASS: every local helper render3D calls is declared at plugin scope');
  }

  // 10. Room model: default size, out-of-room count, camera presets aimed inside the room.
  {
    console.log('\n[CHECK 10: room bounds, out-of-room count, view presets]');
    const env = loadPanel();
    const T = env.sandbox.__pathPanelTest;
    assert.ok(T, 'window.__pathPanelTest not exposed');
    const r = T.getRoom();
    assert.deepStrictEqual([r.w, r.d, r.h], [1.4, 1.4, 1.0], 'default room is not 1.4 x 1.4 x 1.0');
    const b = T.roomBounds();
    assert.deepStrictEqual([b.x0, b.x1, b.y0, b.y1, b.z0, b.z1], [-0.7, 0.7, -0.7, 0.7, 0, 1.0]);
    const pts = [
      { x: 0, y: 0, z: 0.5 },      // centre
      { x: 0.705, y: 0, z: 0.5 },  // inside the 1 cm tolerance
      { x: 0.8, y: 0, z: 0.5 },    // past +x wall
      { x: 0, y: -0.9, z: 0.5 },   // past -y wall
      { x: 0, y: 0, z: 1.2 },      // above ceiling
      { x: 0, y: 0, z: -0.1 },     // below floor
    ];
    assert.strictEqual(T.countOutOfRoom(pts), 4, 'out-of-room count');
    console.log('  PASS: default room 1.4 x 1.4 x 1.0 m, 4/6 test points outside');
    ['top', 'side', 'front', 'iso'].forEach((name) => {
      const pose = T.roomViewPose(name);
      const tgt = { x: pose.target.x, y: pose.target.z, z: pose.target.y };  // three -> room axes
      assert.ok(!T.isOutOfRoom(tgt), name + ' target outside room');
      const dist = Math.hypot(pose.pos.x - pose.target.x, pose.pos.y - pose.target.y, pose.pos.z - pose.target.z);
      assert.ok(dist > 0.2 && dist < 20, name + ' camera distance ' + dist + ' outside controls range');
    });
    assert.ok(!T.validRoom({ w: 0.1, d: 1, h: 1 }) && !T.validRoom({ w: NaN, d: 1, h: 1 }), 'invalid room accepted');
    console.log('  PASS: top/side/front/iso aim inside the room within 0.2..20 m');
    env.destroy();
  }

  // 11. Room presets stay inside the room (with margin) for any size/altitude input.
  {
    console.log('\n[CHECK 11: room-fitted presets]');
    const env = loadPanel();
    const T = env.sandbox.__pathPanelTest;
    const rooms = [{ w: 1.4, d: 1.4, h: 1.0 }, { w: 2.0, d: 1.2, h: 0.6 }];
    const inputs = [[0.4, 0.5], [5, 5], [-1, -1], [NaN, NaN]];
    let n = 0;
    rooms.forEach((room) => {
      T.PRESET_KINDS.forEach((kind) => {
        inputs.forEach(([size, alt]) => {
          const pts = T.presetPath(kind, size, alt, room);
          assert.ok(pts.length >= 2, kind + ' has < 2 points');
          pts.forEach((pt) => {
            const lim = (v, half) => Math.abs(v) <= half - T.PRESET_MARGIN + 1e-9;
            assert.ok(lim(pt.x, room.w / 2) && lim(pt.y, room.d / 2), kind + ' x/y outside margin: ' + JSON.stringify(pt));
            assert.ok(pt.z >= 0 && pt.z <= room.h - T.PRESET_MARGIN + 1e-9, kind + ' z outside room: ' + JSON.stringify(pt));
            n++;
          });
        });
      });
    });
    const c = T.presetPath('circle', 0.4, 0.5, rooms[0]);
    assert.ok(Math.abs(Math.hypot(c[9].x, c[9].y) - 0.4) < 0.002 && c[9].z === 0.5, 'circle radius/altitude');
    const big = T.presetPath('square', 5, 0.5, rooms[0]);
    assert.strictEqual(big[1].x, 0.55, 'oversize square not clamped to 0.7 - 0.15');
    console.log('  PASS: ' + T.PRESET_KINDS.length + ' presets x 2 rooms x 4 inputs, ' + n + ' points all inside the margin');
    env.destroy();
  }

  // 12. Mouse drawing: clamping into the room, 2 cm thinning, screen <-> world inverse.
  {
    console.log('\n[CHECK 12: draw clamping, thinning, screen/world inverse]');
    const env = loadPanel();
    const T = env.sandbox.__pathPanelTest;
    const room = { w: 1.4, d: 1.4, h: 1.0 };
    const c = T.clampToRoom({ x: 2, y: -3, z: 1.5 }, room);
    assert.deepStrictEqual([c.x, c.y, c.z], [0.7, -0.7, 1.0], 'clamp to walls/ceiling');
    assert.strictEqual(T.clampToRoom({ x: 0, y: 0, z: -0.2 }, room).z, 0, 'clamp to floor');
    const pts = [];
    let added = 0;
    for (let i = 0; i <= 100; i++) {           // 1 mm steps along x: 0 .. 0.1 m
      if (T.thinAppend(pts, { x: i * 0.001, y: 0, z: 0.5 }, T.DRAW_MIN_STEP, room)) added++;
    }
    assert.strictEqual(added, pts.length);
    assert.strictEqual(pts.length, 6, 'expected 0,2,4,6,8,10 cm; got ' + JSON.stringify(pts.map(q => q.x)));
    for (let i = 1; i < pts.length; i++) {
      assert.ok(Math.hypot(pts[i].x - pts[i - 1].x, pts[i].y - pts[i - 1].y) >= T.DRAW_MIN_STEP - 1e-9, 'spacing');
    }
    const out = [];
    T.thinAppend(out, { x: 5, y: 0, z: 0.5 }, T.DRAW_MIN_STEP, room);
    assert.strictEqual(T.thinAppend(out, { x: 6, y: 0, z: 0.5 }, T.DRAW_MIN_STEP, room), false,
      'two far-outside points clamp to the same wall point and must thin to one');
    [[0.3, -0.2], [-0.7, 0.7], [0, 0]].forEach(([x, y]) => {
      const sc = T.worldToScreen(x, y, 800, 600);
      const w = T.screenToWorld(sc.x, sc.y, 800, 600);
      assert.ok(Math.abs(w.x - x) < 1e-9 && Math.abs(w.y - y) < 1e-9, 'screen/world inverse at ' + x + ',' + y);
    });
    const z = T.roomFitZoom(800, 600);
    assert.ok(Math.abs(1.4 * 20 * z - 600 * 0.85) < 1e-6, 'room fit zoom frames 85% of the short side');
    console.log('  PASS: clamp to 1.4 x 1.4 x 1.0 room, 101 mm-steps thin to 6 pts at 2 cm, inverse exact, fit zoom ' + z.toFixed(2));
    env.destroy();
  }

  console.log('\nALL CHECKS PASSED SUCCESSFULLY.');
}

try { runChecks(); } catch (e) { console.error(e); process.exit(1); }
