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
    env.click('pp-set-home');   // no live-position dot; probe the Home marker

    function curDotX() {
      const dots = env.ctx.arcs.filter((a) => a.r === 12);
      assert.ok(dots.length, 'home r=12 marker must be drawn');
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
    // CRLF checkouts (Windows autocrlf) would hide the closing-brace end marker
    // and stretch the body over the rest of the file.
    const src = fs.readFileSync(PANEL, 'utf8').replace(/\r\n/g, '\n');
    const start = src.indexOf('  function render3D()');
    assert.ok(start >= 0, 'render3D not found');
    const end = src.indexOf('\n  }\n', start);
    const body = src.slice(start, end);
    const called = new Set((body.match(/(?<![.\w$])([A-Za-z_]\w*)\s*\(/g) || []).map((m) => m.replace(/\s*\($/, '')));
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

  // 13. Saved paths: serialize/parse round trip, bad files rejected, library capped.
  {
    console.log('\n[CHECK 13: saved path serialize/parse/library]');
    const env = loadPanel();
    const T = env.sandbox.__pathPanelTest;
    const room = { w: 1.4, d: 1.4, h: 1.0 };
    const pts = T.presetPath('circle', 0.4, 0.5, room);
    const ser = T.serializePath('  my   loop ', pts, room, 1000);
    assert.strictEqual(ser.name, 'my loop');
    assert.strictEqual(ser.type, 'custom');
    const back = T.parsePathFile(JSON.stringify(ser));
    assert.ok(back && back.points.length === pts.length, 'round trip length');
    back.points.forEach((q2, i) => {
      assert.ok(q2.x === pts[i].x && q2.y === pts[i].y && q2.z === pts[i].z, 'round trip point ' + i);
    });
    assert.strictEqual(back.room.w, 1.4);
    // path_library.py records without z and without room parse, z = 0.
    const lib = T.parsePathFile({ name: 'srv', points: [{ x: 1, y: 2 }, { x: 3, y: 4 }] });
    assert.ok(lib && lib.points[1].z === 0 && lib.room === null);
    const bad = ['not json', '{}', '{"points":[]}', '{"points":[{"x":"1","y":0}]}',
                 JSON.stringify({ points: [{ x: 0, y: 0, z: null }, { x: 1, y: 0, z: 'a' }] })];
    bad.forEach((b2) => assert.strictEqual(T.parsePathFile(b2), null, 'accepted bad file: ' + b2));
    let L = [];
    for (let i = 0; i < T.LIB_MAX + 5; i++) L = T.libraryPut(L, { name: 'p' + i, points: [] });
    assert.strictEqual(L.length, T.LIB_MAX);
    assert.strictEqual(L[0].name, 'p' + (T.LIB_MAX + 4));
    L = T.libraryPut(L, { name: 'p10', points: [1] });
    assert.strictEqual(L.filter((e) => e.name === 'p10').length, 1, 'same name must replace');
    assert.strictEqual(L[0].name, 'p10');
    console.log('  PASS: ' + pts.length + '-point round trip exact; ' + bad.length + ' bad files rejected; library capped at ' + T.LIB_MAX);
    env.destroy();
  }

  // 14. Measured trace only: no live-position dot, no 2D grid lines.
  {
    console.log('\n[CHECK 14: no position dot, no grid, origin axes kept]');
    const env = loadPanel();
    env.feed(frameC(100, 0));
    env.feed(frameC(120, 40));
    assert.strictEqual(env.ctx.arcs.filter((a) => a.r === 8 || a.r === 11).length, 0, 'position dot drawn');
    assert.ok(env.ctx.texts.some((x) => x.text === 'X') && env.ctx.texts.some((x) => x.text === 'Y'), 'axis labels');
    console.log('  PASS: 0 position-dot arcs; X/Y axis labels drawn');
    env.destroy();
  }

  // 15. World origin: set here -> current reads (0, 0), new samples offset; reset restores.
  {
    console.log('\n[CHECK 15: set/reset world origin (display only)]');
    const env = loadPanel();
    const T = env.sandbox.__pathPanelTest;
    const near = (a, b2) => Math.abs(a - b2) < 1e-9;
    env.feed(frameC(100, 50));
    const raw = T.getCurrentPos();
    env.click('pp-set-origin');
    const o = T.getOrigin();
    assert.ok(near(o.x, raw.x) && near(o.y, raw.y), 'origin = raw current');
    let c = T.getCurrentPos();
    assert.ok(near(c.x, 0) && near(c.y, 0), 'current must read 0,0 after set origin');
    env.feed(frameC(150, 50));
    c = T.getCurrentPos();
    assert.ok(near(c.y, 0) && c.x > 0, 'new sample offset by origin');
    const moved = c.x;
    env.click('pp-clear-origin');
    c = T.getCurrentPos();
    assert.ok(near(c.x, moved + raw.x) && near(c.y, raw.y), 'reset restores firmware frame');
    assert.ok(T.validOrigin({ x: 0, y: 0 }) && !T.validOrigin({ x: NaN, y: 0 }) && !T.validOrigin({ x: '1', y: 0 }));
    const arr = [{ x: 1, y: 2 }];
    T.shiftPoints(arr, 1, 2);
    assert.ok(arr[0].x === 0 && arr[0].y === 0);
    console.log('  PASS: origin (' + raw.x + ', ' + raw.y + ') -> current 0,0; next sample x=' + moved + '; reset restores');
    env.destroy();
  }

  // 16. Fit-to-path pose and trail recency fade.
  {
    console.log('\n[CHECK 16: fit-to-path pose + trail fade]');
    const env = loadPanel();
    const T = env.sandbox.__pathPanelTest;
    const near = (a, b2) => Math.abs(a - b2) < 1e-9;
    const pts = [{ x: 0, y: 0, z: 0.2 }, { x: 0.6, y: 0.4, z: 0.6 }];
    const pose = T.pathViewPose(pts);
    assert.ok(near(pose.target.x, 0.3) && near(pose.target.y, 0.4) && near(pose.target.z, 0.2), 'target = bbox centre (three axes)');
    const d = Math.hypot(pose.pos.x - 0.3, pose.pos.y - 0.4, pose.pos.z - 0.2);
    const rad = 0.5 * Math.hypot(0.6, 0.4, 0.4);
    assert.ok(Math.abs(d - 2.3 * rad) < 1e-9, 'distance = 2.3 * bbox radius');
    const tiny = T.pathViewPose([{ x: 0.1, y: 0.1, z: 0.5 }]);
    const dt = Math.hypot(tiny.pos.x - 0.1, tiny.pos.y - 0.5, tiny.pos.z - 0.1);
    assert.ok(Math.abs(dt - 2.3 * 0.25) < 1e-9, 'min radius 0.25 m');
    const outl = T.pathViewPose([{ x: 0, y: 0, z: 0.5 }, { x: 0, y: 0, z: 50 }]);
    assert.ok(near(outl.target.y, 0.75), 'altitude outlier clamped to room height');
    const empty = T.pathViewPose([]);
    const room = T.roomViewPose('iso');
    assert.ok(near(empty.pos.x, room.pos.x) && near(empty.pos.y, room.pos.y), 'empty -> room iso');
    const line = [{ x: 0, y: 0, z: 0.5 }, { x: 0.1, y: 0, z: 0.5 }, { x: 0.2, y: 0, z: 0.5 }];
    const c0 = [0, 0, 0], c1 = [0, 0, 0];
    T.actualSegColor(line, 0, c0);
    T.actualSegColor(line, 1, c1);
    assert.ok(near(c1[2], 1.0) && near(c0[2], 0.35), 'newest full, oldest 35 %');
    console.log('  PASS: fit target (0.3, 0.4, 0.2), dist ' + d.toFixed(3) + ' m; fade oldest ' + c0[2] + ' newest ' + c1[2]);
    env.destroy();
  }

  // 17. Smoothed thin trail and Clear trail.
  {
    console.log('\n[CHECK 17: Catmull-Rom smoothing + Clear trail empties arrays]');
    const env = loadPanel();
    const T = env.sandbox.__pathPanelTest;
    const raw = [];
    for (let k = 0; k <= 8; k++) raw.push({ x: Math.cos(k * Math.PI / 4) * 0.4, y: Math.sin(k * Math.PI / 4) * 0.4, z: 0.5 });
    const d = T.smoothPolyline(raw);
    assert.ok(d.length > raw.length * 3, 'densified: ' + d.length);
    let maxDev = 0, hits = 0;
    for (const pt of d) maxDev = Math.max(maxDev, Math.abs(Math.hypot(pt.x, pt.y) - 0.4));
    for (const r of raw) if (d.some((pt) => Math.hypot(pt.x - r.x, pt.y - r.y) < 1e-9)) hits++;
    assert.strictEqual(hits, raw.length, 'passes through every raw point');
    const sag = 0.4 * (1 - Math.cos(Math.PI / 8));   // straight-chord error at mid-segment
    assert.ok(maxDev < 0.7 * sag, 'spline beats straight chords: dev ' + maxDev + ' vs chord ' + sag);
    const jit = T.smoothPolyline([{ x: 0, y: 0, z: 0 }, { x: 0.001, y: 0, z: 0 }, { x: 0.1, y: 0, z: 0 }]);
    assert.ok(jit.every((pt) => pt.x >= -1e-9 && pt.x <= 0.1 + 1e-9 && Math.abs(pt.y) < 1e-12), 'jitter merged, no overshoot');
    env.feed(frameC(100, 50));
    env.feed(frameC(120, 60));
    env.feed(frameC(140, 70));
    assert.ok(T.getTrailLengths().actual >= 2, 'trail filled: ' + T.getTrailLengths().actual);
    env.click('pp-3d-clear');
    assert.strictEqual(T.getTrailLengths().actual, 0, 'Clear trail empties actual');
    assert.strictEqual(T.getTrailLengths().desired, 0, 'Clear trail empties desired');
    console.log('  PASS: ' + raw.length + ' raw -> ' + d.length + ' samples through all raw points, circle dev ' + maxDev.toFixed(4) + ' m; clear -> 0');
    env.destroy();
  }

  // 18. Path plane: kind / tilt / spacing for presets and drawing.
  {
    console.log('\n[CHECK 18: path plane (xy/xz/yz, tilt, spacing) for presets + drawing]');
    const env = loadPanel();
    const T = env.sandbox.__pathPanelTest;
    const room = { w: 3, d: 3, h: 2 };
    const inRoom = (p) => Math.abs(p.x) <= room.w / 2 - 0.15 + 1e-3 && Math.abs(p.y) <= room.d / 2 - 0.15 + 1e-3 &&
      p.z >= Math.min(0.1, room.h / 2) - 1e-3 && p.z <= room.h - 0.15 + 1e-3;
    // Default opts keep the old geometry (CHECK 11 relies on it).
    const c0 = T.presetPath('circle', 0.4, 0.5, room);
    const c1 = T.presetPath('circle', 0.4, 0.5, room, { plane: 'xy', tilt: 0 });
    assert.strictEqual(JSON.stringify(c0), JSON.stringify(c1), 'xy/0 == legacy');
    // Vertical xz circle: y constant, radius 0.4 about (0, alt).
    const cz = T.presetPath('circle', 0.4, 1.0, room, { plane: 'xz' });
    assert.ok(cz.every((p) => Math.abs(p.y) < 1e-9), 'xz: y = 0');
    assert.ok(cz.every((p) => Math.abs(Math.hypot(p.x, p.z - 1.0) - 0.4) < 0.002), 'xz: radius 0.4 about z=1');
    const cy = T.presetPath('circle', 0.4, 1.0, room, { plane: 'yz' });
    assert.ok(cy.every((p) => Math.abs(p.x) < 1e-9), 'yz: x = 0');
    // Tilted xy circle: in a plane with normal (0, -sin, cos).
    const ct = T.presetPath('circle', 0.4, 1.0, room, { plane: 'xy', tilt: 30 });
    const s30 = Math.sin(Math.PI / 6), c30 = Math.cos(Math.PI / 6);
    assert.ok(ct.every((p) => Math.abs(-s30 * p.y + c30 * (p.z - 1.0)) < 0.002), 'tilt: points on plane');
    assert.ok(Math.max(...ct.map((p) => p.z)) - Math.min(...ct.map((p) => p.z)) > 0.35, 'tilt: z varies');
    // Too big for the room: uniform scale keeps the shape a circle and inside.
    const big = T.presetPath('circle', 5, 1.0, room, { plane: 'xz' });
    const r0 = Math.hypot(big[0].x, big[0].z - 1.0);
    assert.ok(big.every(inRoom), 'big xz circle inside room');
    assert.ok(big.every((p) => Math.abs(Math.hypot(p.x, p.z - 1.0) - r0) < 0.002), 'big stays circular, r ' + r0);
    for (const kind of T.PRESET_KINDS) for (const plane of ['xy', 'xz', 'yz']) for (const tilt of [-45, 0, 60]) {
      const pts = T.presetPath(kind, 0.8, 1.0, room, { plane: plane, tilt: tilt, spacing: 0.05 });
      assert.ok(pts.length >= 2 && pts.slice(kind === 'hover' ? 1 : 0).every(inRoom), kind + ' ' + plane + ' ' + tilt + ' in room');
    }
    // Spacing: resampled steps <= spacing, square keeps its 4 corners.
    const sq = T.presetPath('square', 0.4, 0.5, room, { spacing: 0.05 });
    for (let i = 1; i < sq.length; i++) {
      const d = Math.hypot(sq[i].x - sq[i - 1].x, sq[i].y - sq[i - 1].y, sq[i].z - sq[i - 1].z);
      assert.ok(d <= 0.05 + 2e-3, 'step ' + d);
    }
    for (const cx of [[0.4, 0.4], [-0.4, 0.4], [-0.4, -0.4], [0.4, -0.4]]) {
      assert.ok(sq.some((p) => Math.abs(p.x - cx[0]) < 1e-3 && Math.abs(p.y - cx[1]) < 1e-3), 'corner ' + cx);
    }
    // Drawing: top-view lift onto a tilted xy plane; vertical planes refuse 2D.
    T.setPlane('xy', 20);
    const lp = T.planeFromTop(0.1, 0.5);
    assert.ok(Math.abs(lp.z - (0.5 + 0.5 * Math.tan(20 * Math.PI / 180))) < 1e-6, 'lift z ' + lp.z);
    T.setPlane('xz', 0);
    assert.strictEqual(T.planeFromTop(0.1, 0.2), null, 'xz refuses top view');
    T.setPlane(null, NaN, 0.07);
    assert.strictEqual(T.getPlane().spacing, 0.07, 'spacing set');
    console.log('  PASS: xy/0 == legacy, xz/yz vertical, tilt on-plane, big scaled uniformly (r ' + r0.toFixed(3) +
      '), ' + T.PRESET_KINDS.length * 9 + ' combos in room, square ' + sq.length + ' pts @0.05 m with corners');
    env.destroy();
  }

  // 19. Live metrics block + hint when setpoint telemetry is missing.
  {
    console.log('\n[CHECK 19: live metrics (rates, error, path length) + missing-Des hint]');
    const env = loadPanel();
    const T = env.sandbox.__pathPanelTest;
    const html0 = env.doc.getElementById('pp-metrics').innerHTML;
    assert.ok(/no position feedback/.test(html0) && /no setpoint/.test(html0), 'hint names missing FB and Des');
    env.feed(frameC(100, 50));
    env.feed(frameC(120, 60));
    env.feed(frameC(140, 70));
    let html = env.doc.getElementById('pp-metrics').innerHTML;
    assert.ok(/ Hz$/.test(metricTile(html, 'FB rate')), 'FB rate filled: ' + metricTile(html, 'FB rate'));
    assert.strictEqual(metricTile(html, 'Des rate'), '—', 'no Des yet');
    assert.ok(/no setpoint/.test(html) && !/no position feedback/.test(html), 'hint now only Des');
    env.feed(frameTelemetry(160, 80, 150, 80));
    env.feed(frameTelemetry(180, 90, 150, 80));
    env.feed(frameTelemetry(200, 100, 150, 80));
    html = env.doc.getElementById('pp-metrics').innerHTML;
    assert.ok(/ m$/.test(metricTile(html, 'Live error')), 'live error: ' + metricTile(html, 'Live error'));
    assert.ok(!/pp-metric-hint/.test(html), 'hint gone once FB and Des arrive');
    assert.strictEqual(T.rxRate([0, 100, 200, 300], 350), 10, 'rate 10 Hz');
    assert.strictEqual(T.rxRate([0, 100, 200], 5000), null, 'stale -> null');
    assert.ok(Math.abs(T.path3DLength([{ x: 0, y: 0, z: 0 }, { x: 0.3, y: 0.4, z: 0 }, { x: 0.3, y: 0.4, z: 1 }]) - 1.5) < 1e-12, '3D length');
    env.click('pp-3d-clear');
    html = env.doc.getElementById('pp-metrics').innerHTML;
    assert.strictEqual(metricTile(html, 'FB rate'), '—', 'clear resets FB rate');
    console.log('  PASS: missing-FB/Des hint, FB rate + live error fill, clear resets rate; rxRate/path3DLength exact');
    env.destroy();
  }

  // 20. Execute / Stop: preset -> firmware path commands, one at a time, SDK only.
  {
    console.log('\n[CHECK 20: execute preset via 0x0A/0x0B/0x0C/0x11, stop, SDK gate, abort on reject]');
    const env = loadPanel();
    const T = env.sandbox.__pathPanelTest;
    const room = { w: 3, d: 3, h: 2 };
    const o = { x: 0.5, y: -0.25 };
    // Units: centre in cm, z in m, amplitude/radius in cm; omega = v/r, f = v/(2 pi A).
    let pl = T.executePlan('circle', 0.4, 0.8, room, o, { speed: 0.2, duration: 15 });
    assert.deepStrictEqual(JSON.parse(JSON.stringify(pl.steps)),
      [[12, 0, 50], [12, 1, -25], [12, 2, 0.8], [12, 3, 40], [12, 4, 0.5], [12, 5, 15], [12, 6, 1]], 'circle steps');
    pl = T.executePlan('line', 0.4, 0.8, room, o, { speed: 0.2 });
    assert.strictEqual(pl.steps[3][2], 40, 'line amp cm');
    assert.strictEqual(pl.steps[4][2], Math.round(0.2 / (2 * Math.PI * 0.4) * 1000) / 1000, 'line freq');
    assert.deepStrictEqual(JSON.parse(JSON.stringify(pl.steps[7])), [11, 7, 1], 'line active last');
    pl = T.executePlan('figure8', 0.4, 0.8, room, o, {});
    assert.deepStrictEqual(JSON.parse(JSON.stringify(pl.steps.slice(-2))), [[17, 6, 1], [17, 7, 1]], 'fig8 type 1 then active');
    assert.ok(/hover would turn/.test(T.executePlan('hover', 0.4, 0.8, room, o, {}).error), 'hover needs yaw');
    pl = T.executePlan('hover', 0.4, 0.8, room, o, { yaw: -12.5 });
    assert.deepStrictEqual(JSON.parse(JSON.stringify(pl.steps)), [[10, 0, 50], [10, 1, -25], [10, 2, 0.8], [10, 3, -12.5], [10, 4, 1]], 'hover steps');
    for (const k of ['square', 'helix', 'drawn']) assert.ok(/waypoint-upload/.test(T.executePlan(k, 0.4, 0.8, room, o, {}).error), k + ' refused');
    assert.ok(/horizontal only/.test(T.executePlan('circle', 0.4, 0.8, room, o, { plane: 'xz' }).error), 'xz refused');
    assert.ok(/horizontal only/.test(T.executePlan('circle', 0.4, 0.8, room, o, { tilt: 10 }).error), 'tilt refused');
    // Big preset is room-fitted: the drone gets the fitted radius, not the typed one.
    pl = T.executePlan('circle', 5, 0.8, room, o, {});
    assert.ok(pl.steps[3][2] < 150 && pl.steps[3][2] > 50, 'fitted radius ' + pl.steps[3][2]);

    // Handler: sync thenable submit stub records calls.
    const sent = [];
    let nextId = 1, confirms = 0;
    env.api.submitCommand = (c, i, v) => { sent.push([c, i, v]); const id = nextId++; return { then(ok) { ok({ transaction_id: id }); } }; };
    env.sandbox.confirm = () => { confirms++; return true; };
    env.doc.getElementById('pp-preset-kind').value = 'circle';
    const status = () => env.doc.getElementById('pp-exec-status').textContent;
    env.click('pp-exec-go');
    assert.strictEqual(sent.length, 0, 'no SDK -> nothing sent');
    assert.ok(/not SDK/.test(status()), 'status: ' + status());
    const sdk = (extra) => Object.assign({ connected: true, status: { rc_authority: 1 }, streams: {} }, extra || {});
    env.feed(sdk());
    assert.strictEqual(T.getSdk(), 1, 'sdk seen');
    env.click('pp-exec-go');
    assert.strictEqual(confirms, 1, 'confirm asked');
    assert.strictEqual(sent.length, 1, 'first step only before its result');
    for (let k = 1; k <= 7; k++) env.feed(sdk({ last_transaction_result: { transaction_id: k, status: 'applied' } }));
    assert.strictEqual(sent.length, 7, 'all 7 sent: ' + sent.length);
    assert.deepStrictEqual(JSON.parse(JSON.stringify(sent[6])), [12, 6, 1], 'active last');
    assert.ok(/all 7 commands applied/.test(status()), status());
    // Reject mid-sequence: active=1 never sent.
    sent.length = 0;
    env.click('pp-exec-go');
    env.feed(sdk({ command_results: [{ transaction_id: 8, status: 'applied' }] }));
    env.feed(sdk({ command_results: [{ transaction_id: 9, status: 'rejected', reason: 'interlock' }] }));
    env.feed(sdk({ command_results: [{ transaction_id: 9, status: 'rejected', reason: 'interlock' }] }));
    assert.strictEqual(sent.length, 2, 'stopped after reject');
    assert.ok(!sent.some((s) => s[1] === 6 && s[2] === 1), 'active never sent');
    assert.ok(/rejected .interlock./.test(status()), status());
    assert.strictEqual(T.getExec(), null, 'idle after abort');
    // Hover uses heading from Ctrler.yawPID.FB, else -imu_data.yaw.
    env.feed(sdk({ streams: { '0': { values: { 'imu_data.yaw': 30 } } } }));
    assert.strictEqual(T.getHoldYaw(), -30, 'hold yaw = -imu yaw');
    // Stop: three deactivations, never 0x0D.
    sent.length = 0;
    env.click('pp-exec-stop');
    for (let k = nextId - 1; k < nextId + 3; k++) env.feed(sdk({ last_transaction_result: { transaction_id: k, status: 'verified' } }));
    assert.deepStrictEqual(sent, [[11, 7, 0], [12, 6, 0], [17, 7, 0]], 'stop steps');
    assert.ok(!sent.some((s) => s[0] === 13), 'no 0x0D');
    console.log('  PASS: units (cm/m), 7-step circle one-at-a-time, SDK gate, abort on reject, hover yaw, stop = 3 deactivations');
    env.destroy();
  }

  // Session replay: a desired setpoint of 0 is real, not missing.
  {
    console.log('\n[CHECK: session replay keeps desired x/y/z = 0]');
    const env = loadPanel();
    const T = env.sandbox.__pathPanelTest;
    const plain = (o) => JSON.parse(JSON.stringify(o));
    // 0 in the first alias must win over a non-zero later alias.
    assert.deepStrictEqual(plain(T.replayDesired({ pid: { locx: { Des: 0 }, locy: { Des: 0 }, z_pos: { Des: 0 } },
      'Ctrler.locxPID.Des': 250, 'Ctrler.locyPID.Des': 300, 'Ctrler.Z_posPID.Des': 1.5 })),
      { x: 0, y: 0, z: 0 }, 'desired 0 must not fall through to a later alias');
    // Flat CSV keys, string cells: '0' kept, '' skipped to the next alias.
    assert.deepStrictEqual(plain(T.replayDesired({ 'pid.locx.Des': '0', 'pid.locy.Des': '',
      'Ctrler.locyPID.Des': '150' })), { x: 0, y: 1.5, z: 0 }, 'flat keys: "0" kept, "" skipped');
    // x or y absent / null -> no desired point (no NaN pushed).
    assert.strictEqual(T.replayDesired({ 'pid.locx.Des': 0 }), null, 'missing y -> null');
    assert.strictEqual(T.replayDesired({ 'pid.locx.Des': 0, 'pid.locy.Des': null }), null, 'null y -> null');
    console.log('  PASS: replay desired 0 kept; "" / null treated as missing');
    env.destroy();
  }

  // 21. Replay frame z scale
  {
    console.log('\n[CHECK 21: replay frame with known cm x/y and metre z plots desired and actual z on the same scale]');
    const env = loadPanel();
    const T = env.sandbox.__pathPanelTest;
    const plain = (o) => JSON.parse(JSON.stringify(o));
    
    const des = T.replayDesired({ 'pid.locx.Des': 120, 'pid.locy.Des': 240, 'pid.z_pos.Des': 1.8 });
    assert.deepStrictEqual(plain(des), { x: 1.2, y: 2.4, z: 1.8 }, 'desired z is raw metres, x/y scaled from cm');

    const state = { streams: { '0': { values: { 'c.earth_x': 100, 'c.earth_y': 200, 'Ctrler.Z_posPID.FB': 1.5, 'pid.locx.Des': 120, 'pid.locy.Des': 240, 'pid.z_pos.Des': 1.8 } } } };
    assert.deepStrictEqual(plain(T.extractPosition(state)), { x: 1, y: 2, z: 1.5, yaw: 0 }, 'actual z is raw metres, x/y scaled from cm');
    assert.deepStrictEqual(plain(T.extractDesiredPosition(state)), { x: 1.2, y: 2.4, z: 1.8, hasZ: true }, 'live desired z is raw metres, x/y scaled from cm');
    
    console.log('  PASS: actual and desired z are plotted on the same scale (metres), x/y are scaled from cm');
    env.destroy();
  }

  console.log('\nALL CHECKS PASSED SUCCESSFULLY.');
}

try { runChecks(); } catch (e) { console.error(e); process.exit(1); }
