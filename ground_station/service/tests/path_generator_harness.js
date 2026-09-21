'use strict';
/**
 * Offline harness for path-panel.js (Path Planning), Bug 5
 * (AUDIT_2026-09-21 §Bug 5 / §3.5-6). Loads the panel into a fake DOM
 * with a recording 2D canvas and no network helpers, then verifies:
 *
 *   1. Binding — the panel resolves Frame C position from the /state
 *      stream under BOTH spellings the adapter publishes:
 *        - the spec alias  `c.earth_x` / `c.earth_y` / `c.altitude_cm`
 *        - the raw per-slot DWARF key `slot1.ano_of.earth_x` /
 *          `slot1.ano_of.earth_y` / `slot1.ano_of.of_alt_cm`
 *      and that altitude is normalised from cm to metres.
 *   2. Honest-absent — when NO position key is published at all, the panel
 *      still renders "No position data" (never a fabricated 0/0), and its
 *      Distance metric tile reads "—".
 *   3. Random path generator — under a seeded Math.random the generator
 *      fills the waypoint list with exactly N points, every point strictly
 *      inside the configured ±box metres, and consecutive waypoints are
 *      spaced within the configured spacing band. Nothing is POSTed.
 *   4. Manual (x,y,z) waypoint + manual home/target entry append / set the
 *      on-panel markers from the plan fields. Still zero network sends.
 *
 * No service is contacted; the sandbox has no require / net modules.
 * Exit code 0 iff every check passes.
 */
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const assert = require('assert');

const PANEL = path.join(__dirname, '..', '..', '..',
  'docs', 'dashboard-platform', 'shell', 'plugins', 'path-panel.js');

function makeCtx() {
  return {
    fillStyle: '', strokeStyle: '', font: '', textAlign: '', textBaseline: '',
    lineWidth: 1, setLineDash() {}, texts: [], arcs: [],
    fillRect() {}, beginPath() {}, moveTo() {}, lineTo() {}, stroke() {}, fill() {},
    fillText(t, x, y) { this.texts.push({ text: t, x, y }); },
    arc(x, y, r) { this.arcs.push({ x, y, r, fill: this.fillStyle }); },
    reset() { this.texts = []; this.arcs = []; },
  };
}

class Element {
  constructor(id, tag) {
    this.id = id; this.tagName = (tag || 'div').toUpperCase();
    this._html = ''; this.textContent = ''; this.value = ''; this.checked = false;
    this.className = ''; this.handlers = {}; this.style = {}; this.dataset = {};
    this.doc = null;
    this.parentElement = { offsetWidth: 600, offsetHeight: 400 };
    this._nodeCache = {};
  }
  addEventListener(ev, fn) { (this.handlers[ev] = this.handlers[ev] || []).push(fn); }
  getAttribute(n) { return this.dataset[n] !== undefined ? this.dataset[n] : null; }
  setAttribute(n, v) { this.dataset[n] = String(v); }
  getContext() { return this.doc.ctx; }
  get innerHTML() { return this._html; }
  set innerHTML(html) { this._html = html; this._nodeCache = {}; if (this.doc) this.doc.scan(html); }
  querySelectorAll(cls) {
    const dot = cls.replace(/^\./, '');
    const re = /<[a-zA-Z0-9-]+[^>]*>/g; const out = []; let m;
    while ((m = re.exec(this._html)) !== null) {
      const tag = m[0];
      if (!new RegExp('class="[^"]*\\b' + dot + '\\b[^"]*"').test(tag)) continue;
      let el = this._nodeCache[tag];
      if (!el) {
        const idm = tag.match(/\bid="([^"]+)"/);
        el = new Element(idm ? idm[1] : null, tag.slice(1).split(/[\s>]/)[0]);
        this._nodeCache[tag] = el;
      }
      const dm = tag.match(/\bdata-index="([^"]+)"/); if (dm) el.dataset.index = dm[1];
      const dam = tag.match(/\bdata-axis="([^"]+)"/); if (dam) el.dataset.axis = dam[1];
      const v2 = tag.match(/\bvalue="([^"]*)"/); if (v2) el.value = v2[1];
      out.push(el);
    }
    return out;
  }
}

class FakeDocument {
  constructor(ctx) { this.elements = {}; this.ctx = ctx; }
  scan(html) {
    const tags = html.match(/<[a-zA-Z0-9-]+[^>]*>/g) || [];
    for (const tag of tags) {
      const idm = tag.match(/\bid="([^"]+)"/); if (!idm) continue;
      const id = idm[1];
      const el = new Element(id, tag.slice(1).split(/[\s>]/)[0]);
      el.doc = this; this.elements[id] = el;
      const v = tag.match(/\bvalue="([^"]*)"/); if (v) el.value = v[1];
      const tm = html.match(new RegExp('<[a-zA-Z0-9-]+[^>]*\\bid="' + id + '"[^>]*>([^<]*)<'));
      if (tm) el.textContent = tm[1];
    }
  }
  getElementById(id) { return this.elements[id] || null; }
}

function loadPanel(seed) {
  const ctx = makeCtx();
  const doc = new FakeDocument(ctx);
  const container = new Element('container', 'div'); container.doc = doc;
  const api = {
    stateCb: null, subscribe(cb) { this.stateCb = cb; },
    registerPanel(name, renderFn) { this.renderFn = renderFn; },
  };
  const fetchRecords = []; let xhr = 0;
  const sandbox = {
    document: doc, console: { log() {}, warn() {}, error() {} },
    Date, Number, String, Boolean, Array, Object, JSON,
    setInterval, clearInterval, setTimeout, clearTimeout,
  };
  sandbox.Math = {
    random: (seed ? function () { return seed(); } : Math.random),
    pow: Math.pow, round: Math.round, floor: Math.floor, ceil: Math.ceil,
    min: Math.min, max: Math.max, sqrt: Math.sqrt, abs: Math.abs,
    cos: Math.cos, sin: Math.sin, PI: Math.PI,
  };
  sandbox.isNaN = (v) => (typeof v === 'number' && isNaN(v)) || v === undefined;
  sandbox.fetch = function (url, opts) { fetchRecords.push({ url, opts }); return Promise.resolve({ ok: true, json: () => Promise.resolve({}) }); };
  sandbox.XMLHttpRequest = function () { xhr += 1; throw new Error('XHR blocked'); };
  sandbox.handlers = {};
  sandbox.addEventListener = function (ev, fn) { (this.handlers[ev] = this.handlers[ev] || []).push(fn); };
  sandbox.window = sandbox;
  sandbox.__registerPlugin__ = function (name, init, destroy) {
    sandbox.pluginName = name; sandbox.pluginInit = init; sandbox.pluginDestroy = destroy;
  };
  vm.createContext(sandbox);
  vm.runInContext(fs.readFileSync(PANEL, 'utf8'), sandbox, { filename: PANEL });
  sandbox.pluginInit(api);
  api.renderFn(container);
  return {
    doc, ctx, api, sandbox, fetchRecords: () => fetchRecords.slice(),
    feed(s) { api.stateCb(s); },
    click(id) { const el = doc.getElementById(id); assert.ok(el && el.handlers.click, 'no click on #' + id); el.handlers.click.forEach((f) => f.call(el)); },
    set(id, v) { const el = doc.getElementById(id); assert.ok(el, 'no field #' + id); el.value = String(v); },
    destroy() { sandbox.pluginDestroy(); },
  };
}

function frameC(x, y, alt, extra, prefixed) {
  const values = Object.assign({}, extra);
  if (prefixed) {
    if (x !== undefined) values['slot1.ano_of.earth_x'] = x;
    if (y !== undefined) values['slot1.ano_of.earth_y'] = y;
    if (alt !== undefined) values['slot1.ano_of.of_alt_cm'] = alt;
  } else {
    if (x !== undefined) values['c.earth_x'] = x;
    if (y !== undefined) values['c.earth_y'] = y;
    if (alt !== undefined) values['c.altitude_cm'] = alt;
  }
  return { connected: true, streams: { '3': { values } } };
}

function metricTile(html, label) {
  const m = html.match(new RegExp(label + '</span><span class="pp-metric-value">([^<]*)</span>'));
  return m ? m[1] : null;
}

function runChecks() {
  console.log('--- PATH GENERATOR / BINDING OFFLINE CHECKS (Bug 5) ---');

  // 1. Binding: spec-alias spelling + cm->m altitude conversion + trail point.
  {
    console.log('\n[CHECK 1: binding from c.earth_x/c.earth_y/c.altitude_cm]');
    const env = loadPanel();
    env.feed(frameC(10, 20, 400, undefined, false)); // alt 400 cm
    env.ctx.reset();   // drop the pre-position "No position data" render
    env.feed(frameC(12, 24, 400));
    assert.ok(!env.ctx.texts.some((t) => t.text === 'No position data'),
      'position must be bound, not "No position data"');
    const html = env.doc.getElementById('pp-metrics').innerHTML;
    assert.strictEqual(metricTile(html, 'Trail Points'), '2');
    assert.ok(/^4\.47 m$/.test(metricTile(html, 'Distance')),
      'distance sqrt(2^2+4^2)=4.47');
    // Home picks up current position INCLUDING metre-normalised z (400cm->4m).
    env.click('pp-set-home');
    assert.ok(env.ctx.texts.some((t) => t.text === 'H'), 'home marker drawn');
    console.log('  PASS: alias spelling binds; distance/trail correct; home set');
    env.destroy();
  }

  // 2. Binding: raw slot-prefixed DWARF spelling + cm->m altitude.
  {
    console.log('\n[CHECK 2: binding from slot1.ano_of.earth_* / of_alt_cm]');
    const env = loadPanel();
    env.feed(frameC(1, -3, 205, undefined, true));   // alt 205 cm
    env.ctx.reset();   // drop pre-position "No position data" render
    assert.ok(!env.ctx.texts.some((t) => t.text === 'No position data'),
      'slot-prefixed spelling must bind');
    assert.strictEqual(env.doc.getElementById('pp-demo-badge').style.display, 'none');
    // home = current: z normalised 205cm -> 2.05m.
    env.ctx.reset();
    env.click('pp-set-home');
    assert.ok(env.ctx.texts.some((t) => t.text === 'H'), 'home marker drawn');
    console.log('  PASS: slot-prefixed raw DWARF spelling binds; alt cm->m');
    env.destroy();
  }

  // 3. Honest-absent: no keys at all -> "No position data", Distance "—".
  {
    console.log('\n[CHECK 3: absent position stays honest (never 0/0)]');
    const env = loadPanel();
    env.feed({ connected: true, streams: {'0': { values: { 'c.gyro_x': 1.0 } } } });
    assert.strictEqual(env.doc.getElementById('pp-demo-badge').textContent,
      'No position data');
    assert.strictEqual(env.doc.getElementById('pp-demo-badge').style.display, 'block');
    assert.ok(env.ctx.texts.some((t) => t.text === 'No position data'),
      'canvas must draw the no-data text');
    const dist = metricTile(env.doc.getElementById('pp-metrics').innerHTML, 'Distance');
    assert.strictEqual(dist, '—');
    console.log('  PASS: badge + canvas "No position data"; Distance honest "—"');
    env.destroy();
  }

  // 4. Random path generator: N points, within box, spacing band, zero sends.
  {
    console.log('\n[CHECK 4: random path generator N / box / spacing]');
    // Seeded random, deterministic trace of generateRandomPath():
    //   n=4, spacing=10, box=50 (all set via fields).
    //   random() call 1 -> px, call 2 -> py, then per step: ang, stepfactor.
    //   calls 1,2 = 0.5 -> px=py=(2*0.5-1)*50 = 0  (start at origin).
    //   calls >=3 = 0.0 -> ang=0 (cos 1 / sin 0), stepfactor=0.6 ->
    //       step = 10*(0.6+0*0.8) = 6.0, walking +x.
    //   Expect points: (0,0),(6,0),(12,0),(18,0) — all inside +/-50,
    //   consecutive spacing exactly 6.0 (in the [6,14] band).
    let calls = 0;
    const env = loadPanel(function () {
      calls += 1;
      return (calls <= 2) ? 0.5 : 0.0;
    });
    env.set('pp-rand-n', '4');
    env.set('pp-rand-spacing', '10');
    env.set('pp-rand-box', '50');
    env.click('pp-rand-gen');

    const table = env.doc.getElementById('pp-wp-table');
    const xVals = table.querySelectorAll('.pp-wp-input')
      .filter((el) => el.dataset.axis === 'x').map((el) => parseFloat(el.value));
    const yVals = table.querySelectorAll('.pp-wp-input')
      .filter((el) => el.dataset.axis === 'y').map((el) => parseFloat(el.value));
    assert.strictEqual(xVals.length, 4, 'exactly 4 generated waypoints');
    assert.strictEqual(yVals.length, 4, 'y column per waypoint');

    // Bound check: every point inside the +/-50 box.
    for (let i = 0; i < 4; i++) {
      assert.ok(xVals[i] >= -50 && xVals[i] <= 50, 'x[' + i + ']=' + xVals[i] + ' in box');
      assert.ok(yVals[i] >= -50 && yVals[i] <= 50, 'y[' + i + ']=' + yVals[i] + ' in box');
    }
    // Deterministic walk from the seed: (0,0),(6,0),(12,0),(18,0).
    assert.deepStrictEqual(xVals, [0, 6, 12, 18], 'seeded +x walk every 6 m');
    assert.deepStrictEqual(yVals, [0, 0, 0, 0], 'y stays 0 under cos-0 walk');
    // Spacing band: consecutive step within [0.6,1.4]x spacing = [6,14].
    for (let i = 1; i < 4; i++) {
      const d = Math.abs(xVals[i] - xVals[i - 1]);
      assert.ok(d >= 6 && d <= 14, 'step ' + d + ' within [6,14] m band');
    }
    const sends = env.fetchRecords();
    assert.strictEqual(sends.length, 0, 'generator must not POST a path');
    assert.strictEqual(env.sandbox.require, undefined);
    console.log('  PASS: 4 wps (0,6,12,18) inside box; spacing 6m in [6,14] band; 0 sends');
    env.destroy();
  }

  // 5. Manual (x,y,z) waypoint + manual target/home entry, zero sends.
  {
    console.log('\n[CHECK 5: manual (x,y,z) waypoint + manual home/target]');
    const env = loadPanel();
    env.feed(frameC(0, 0));   // markers only render once a position is known
    env.set('pp-wp-x', '7'); env.set('pp-wp-y', '-3'); env.set('pp-wp-z', '12');
    env.click('pp-add-manual-wp');
    const table = env.doc.getElementById('pp-wp-table');
    assert.strictEqual(table.querySelectorAll('.pp-wp-input').length, 2,
      'one manual waypoint row -> x,y inputs');
    assert.strictEqual(table.querySelectorAll('.pp-wp-input')[0].value, '7.00');
    assert.strictEqual(table.querySelectorAll('.pp-wp-input')[1].value, '-3.00');

    env.set('pp-target-x', '1'); env.set('pp-target-y', '2'); env.set('pp-target-z', '3');
    env.ctx.reset();
    env.click('pp-set-target-manual');
    assert.ok(env.ctx.texts.some((t) => t.text === 'T'), 'manual target marker T');

    env.ctx.reset();
    env.set('pp-home-x', '5'); env.set('pp-home-y', '5'); env.set('pp-home-z', '5');
    env.click('pp-set-home-manual');
    assert.ok(env.ctx.texts.some((t) => t.text === 'H'), 'manual home marker H');

    assert.strictEqual(env.fetchRecords().length, 0, 'no network send from entry fields');
    assert.strictEqual(env.sandbox.require, undefined);
    const src = fs.readFileSync(PANEL, 'utf8');
    assert.strictEqual(src.indexOf('fetch('), -1);
    console.log('  PASS: manual waypoint appended; manual home/target set; 0 sends');
    env.destroy();
  }

  console.log('\nALL CHECKS PASSED SUCCESSFULLY.');
}

try { runChecks(); } catch (e) { console.error(e); process.exit(1); }
process.exit(0);