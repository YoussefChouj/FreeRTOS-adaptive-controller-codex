'use strict';
/**
 * Offline harness for path-panel.js Fly mode (3D panel flight UX spec,
 * docs/dashboard-platform/3d-panel-flight-ux-spec.md, item 1).
 *
 * Covers: error against the firmware setpoint (3D / xy, no fabricated z),
 * time-weighted statistics, threshold colouring, event detection (adaptation,
 * mode, path, notes, findings), the 7-cell strip, mode layout, the keyboard
 * shortcuts and settings persistence. Fake DOM only; fetch is a GET-only
 * recording stub and nothing is ever sent to a service.
 *
 * Run:  node ground_station/service/tests/path_fly_harness.js
 */
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const assert = require('assert');

const PANEL = path.join(__dirname, '..', '..', '..', 'docs', 'dashboard-platform', 'shell', 'plugins', 'path-panel.js');

class Element {
  constructor(id, tag) {
    this.id = id; this.tagName = (tag || 'div').toUpperCase();
    this._html = ''; this.textContent = ''; this.value = ''; this.className = '';
    this.handlers = {}; this.style = {}; this.dataset = {}; this.disabled = false;
    this.clicks = 0; this.focused = false;
    this.parentElement = { offsetWidth: 600, offsetHeight: 400 };
    this.offsetWidth = 600;
  }
  addEventListener(ev, fn) { (this.handlers[ev] = this.handlers[ev] || []).push(fn); }
  getAttribute(n) { return this.dataset[n] !== undefined ? this.dataset[n] : null; }
  setAttribute(n, v) { this.dataset[n] = String(v); }
  getContext() { return this.doc.ctx; }
  click() { this.clicks++; (this.handlers.click || []).forEach((f) => f.call(this)); }
  focus() { this.focused = true; }
  get innerHTML() { return this._html; }
  set innerHTML(h) { this._html = h; if (this.doc) this.doc.scan(h); }
  querySelectorAll() { return []; }
}
class FakeDocument {
  constructor(ctx) { this.elements = {}; this.ctx = ctx; this.keyHandlers = []; this.head = { appendChild() {} }; }
  createElement(tag) { return new Element(null, tag); }
  scan(html) {
    for (const tag of (html.match(/<[a-zA-Z0-9-]+[^>]*>/g) || [])) {
      const idm = tag.match(/\bid="([^"]+)"/); if (!idm) continue;
      const el = new Element(idm[1], tag.slice(1).split(/[\s>]/)[0]);
      el.doc = this; this.elements[idm[1]] = el;
      const v = tag.match(/\bvalue="([^"]*)"/); if (v) el.value = v[1];
      const st = tag.match(/\bstyle="([^"]*)"/);
      if (st && /display:\s*none/.test(st[1])) el.style.display = 'none';
    }
  }
  getElementById(id) { return this.elements[id] || null; }
  addEventListener(ev, fn) { if (ev === 'keydown') this.keyHandlers.push(fn); }
  removeEventListener(ev, fn) { this.keyHandlers = this.keyHandlers.filter((f) => f !== fn); }
}
const ctx2d = () => new Proxy({}, { get: (t, k) => (k in t ? t[k] : () => {}), set: (t, k, v) => { t[k] = v; return true; } });

function loadPanel(opts) {
  opts = opts || {};
  const doc = new FakeDocument(ctx2d());
  const container = new Element('container', 'div'); container.doc = doc;
  const api = { subscribe(cb) { this.stateCb = cb; }, registerPanel(n, fn) { this.renderFn = fn; } };
  const gets = [];
  const store = Object.assign({}, opts.store || {});
  const sandbox = {
    document: doc, console: { log() {}, warn() {}, error() {} },
    Date, Math, Number, String, Boolean, Array, Object, JSON, isNaN, setInterval, clearInterval, setTimeout, clearTimeout,
    fetch(url, o) {
      gets.push({ url, opts: o });
      const hit = opts.fetchMap && Object.keys(opts.fetchMap).find((k) => url === k || (k.slice(-1) === '?' && url.indexOf(k) === 0));
      if (hit) return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(opts.fetchMap[hit]) });
      if (opts.fetchMap && url.indexOf('/api/rec-logs/') === 0) return Promise.resolve({ ok: false, status: 404, json: () => Promise.resolve({}) });
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(opts.notes ? { notes: opts.notes } : {}) });
    },
    localStorage: { getItem: (k) => (k in store ? store[k] : null), setItem: (k, v) => { store[k] = String(v); } },
  };
  sandbox.handlers = {}; sandbox.addEventListener = () => {}; sandbox.window = sandbox;
  sandbox.__registerPlugin__ = (n, i, d) => { sandbox.pluginInit = i; sandbox.pluginDestroy = d; };
  vm.createContext(sandbox);
  vm.runInContext(fs.readFileSync(PANEL, 'utf8'), sandbox, { filename: PANEL });
  sandbox.pluginInit(api);
  api.renderFn(container);
  const rec = new Element('record-btn', 'button'); rec.doc = doc; doc.elements['record-btn'] = rec;
  const note = new Element('note-input', 'input'); note.doc = doc; doc.elements['note-input'] = note;
  return { doc, api, sandbox, store, gets, T: sandbox.__pathPanelTest, feed: (s) => api.stateCb(s), rec, note,
           key(k, extra) { const ev = Object.assign({ key: k, target: { tagName: 'BODY' } }, extra || {}); doc.keyHandlers.forEach((f) => f(ev)); },
           el: (id) => doc.getElementById(id), destroy: () => sandbox.pluginDestroy() };
}

// Slot-0 style frame: x/y in cm, Z in metres, Des in cm (x/y) and metres (z).
function frame(px, py, pz, dx, dy, dz, extra) {
  const v = Object.assign({}, extra);
  v['c.earth_x'] = px; v['c.earth_y'] = py; v['Ctrler.Z_posPID.FB'] = pz;
  if (dx !== null) { v['Ctrler.locxPID.Des'] = dx; v['Ctrler.locyPID.Des'] = dy; }
  if (dz !== null) v['Ctrler.Z_posPID.Des'] = dz;
  return { connected: true, streams: { '0': { values: v } } };
}
// Objects built inside the vm context have another prototype: compare as JSON.
const deq = (a, b, m) => assert.deepStrictEqual(JSON.parse(JSON.stringify(a)), JSON.parse(JSON.stringify(b)), m);
const near = (a, b, e) => Math.abs(a - b) <= (e || 1e-9);

async function run() {
  console.log('--- PATH PANEL FLY MODE OFFLINE CHECKS ---');

  {
    console.log('\n[CHECK 1: error = position - firmware Des; 3D needs a z setpoint]');
    const env = loadPanel(); const T = env.T;
    const e = T.errorBetween({ x: 0.1, y: 0, z: 0.5 }, { x: 0, y: 0, z: 0.4, hasZ: true });
    assert.ok(near(e.exy, 0.1) && near(e.e3, Math.hypot(0.1, 0.1)), JSON.stringify(e));
    const nz = T.errorBetween({ x: 0.1, y: 0, z: 0.5 }, { x: 0, y: 0, z: 0, hasZ: false });
    assert.ok(near(nz.e3, 0.1) && near(nz.exy, 0.1), 'no z setpoint: 3D falls back to xy, not a fabricated 0');
    assert.strictEqual(T.errorBetween({ x: 1, y: 1 }, null), null);
    // Through the state path: Des from slot 0 in cm, z in m
    env.feed(frame(10, 0, 0.5, 0, 0, 0.4));
    const pt = T.getTrajectory().pop();
    assert.ok(near(pt.exy, 0.1) && near(pt.e3, Math.hypot(0.1, 0.1), 1e-6), 'trajectory point carries e3/exy: ' + JSON.stringify(pt));
    assert.ok(near(pt.z, 0.5), 'z comes from Ctrler.Z_posPID.FB in metres');
    console.log('  PASS'); env.destroy();
  }

  {
    console.log('\n[CHECK 2: time-weighted stats, threshold, gap handling]');
    const T = loadPanel().T;
    // 0.05 m for 1 s, 0.20 m for 1 s (samples 100 ms apart)
    const pts = [];
    for (let i = 0; i <= 20; i++) pts.push({ t: i * 100, e3: i < 10 ? 0.05 : 0.20, exy: 0.01 });
    const s = T.flyStats(pts, 0.10, 'e3');
    assert.strictEqual(s.n, 21);
    assert.ok(near(s.max, 0.20) && near(s.cur, 0.20));
    assert.ok(near(s.pctAbove, 50, 1e-6), 'pct above ' + s.pctAbove);
    assert.ok(near(s.rms, Math.sqrt((10 * 0.0025 + 11 * 0.04) / 21), 1e-9));
    const sxy = T.flyStats(pts, 0.10, 'exy');
    assert.strictEqual(sxy.pctAbove, 0);
    // a 10 s dropout is not carried as "above"
    const gap = [{ t: 0, e3: 0.5 }, { t: 100, e3: 0.5 }, { t: 10100, e3: 0.01 }, { t: 10200, e3: 0.01 }];
    assert.ok(near(T.flyStats(gap, 0.1, 'e3').pctAbove, 50, 1e-6), 'gap skipped: ' + T.flyStats(gap, 0.1, 'e3').pctAbove);
    assert.strictEqual(T.flyStats([], 0.1, 'e3').rms, null);
    assert.strictEqual(T.flyStats([{ t: 0, e3: null }], 0.1, 'e3').n, 0);
    assert.strictEqual(T.flyStats(pts, 0.10, 'e3', 1000).n, 11, 't0 skips earlier points');
    assert.strictEqual(T.fmtRunTime(65000), '1:05');
    console.log('  PASS');
  }

  {
    console.log('\n[CHECK 3: trail colour: green <= thr, red > thr, grey without setpoint]');
    const T = loadPanel().T; const out = [0, 0, 0];
    const pts = [{ e3: 0.05 }, { e3: 0.05 }, { e3: 0.30 }, { e3: null }];
    const f = T.flyColorFn(0.10, 'e3', false);
    f(pts, 0, out); assert.ok(out[1] > out[0] && out[1] > 0.5, 'green ' + out);
    f(pts, 1, out); assert.ok(out[0] > 0.9 && out[1] < 0.3, 'red (either end above) ' + out);
    f(pts, 2, out); assert.ok(out[0] > 0.9, 'segment with one bad end and one null end is red ' + out);
    f([{ e3: null }, { e3: null }], 0, out); assert.ok(out[2] > out[0], 'grey-blue ' + out);
    const many = []; for (let i = 0; i < 1000; i++) many.push({ e3: 0.05 });
    const ff = T.flyColorFn(0.10, 'e3', true);
    ff(many, 0, out); const old = out[1]; ff(many, 997, out);
    assert.ok(out[1] > old * 3, 'fading dims old points');
    console.log('  PASS');
  }

  {
    console.log('\n[CHECK 4: threshold / metric / trail settings validate and persist]');
    const env = loadPanel(); const T = env.T;
    deq(T.getFly(), { thr: 0.10, metric: '3d', trail: 'whole' }, 'defaults');
    assert.strictEqual(T.setFly({ thr: 0.25 }), true);
    assert.strictEqual(T.setFly({ thr: 5 }), false, 'out of range refused');
    assert.strictEqual(T.setFly({ metric: 'nope' }), false);
    assert.strictEqual(T.getFly().thr, 0.25);
    T.setFly({ metric: 'xy', trail: 'fading' });
    const env2 = loadPanel({ store: env.store });
    deq(env2.T.getFly(), { thr: 0.25, metric: 'xy', trail: 'fading' }, 'restored from storage');
    // slider element drives it
    env.el('pp-fly-thr').value = '0.4'; env.el('pp-fly-thr').handlers.input.forEach((f) => f.call(env.el('pp-fly-thr')));
    assert.strictEqual(T.getFly().thr, 0.4);
    console.log('  PASS'); env.destroy(); env2.destroy();
  }

  {
    console.log('\n[CHECK 5: events: adaptation, mode, path (deduped, starts run clock), notes, findings]');
    const env = loadPanel(); const T = env.T;
    env.feed(frame(0, 0, 0.5, 0, 0, 0.5, { 'mrac_flags.adaptation_on': 0, 'DroneStatus.FlyMode': 0, real_voltage: 15.5, 'TWC.execute': 0 }));
    assert.strictEqual(T.getEvents().length, 0, 'first sample only seeds the state');
    env.feed(frame(1, 0, 0.5, 0, 0, 0.5, { 'mrac_flags.adaptation_on': 1, 'DroneStatus.FlyMode': 0, real_voltage: 15.5, 'TWC.execute': 0 }));
    env.feed(frame(2, 0, 0.5, 0, 0, 0.5, { 'mrac_flags.adaptation_on': 1, 'DroneStatus.FlyMode': 1, real_voltage: 15.4, 'TWC.execute': 1 }));
    const ev = T.getEvents();
    deq(ev.map((e) => e.kind), ['adapt', 'mode', 'path'], JSON.stringify(ev.map((e) => e.kind)));
    assert.ok(/ON/.test(ev[0].text) && /Stop → SDK/.test(ev[1].text), ev[1].text);
    assert.ok(ev[0].pos && near(ev[0].pos.z, 0.5), 'event sits at a trail position');
    assert.ok(T.getRunT0() != null, 'path execute starts the run clock');
    T.pathEvent('execute', 'path execute circle', Date.now(), 'panel');
    assert.strictEqual(T.getEvents().length, 3, 'own command + TWC flag within 3 s is one event');
    // notes + findings, only those inside the trail's time range
    const now = Date.now() / 1000;
    T.applyNotes([{ seq: 1, ts: now, text: 'gust', kind: 'marker', source: 'operator' },
                  { seq: 2, ts: now, text: 'roll ringing', kind: 'note', source: 'agent' },
                  { seq: 3, ts: now - 3600, text: 'old', kind: 'note', source: 'operator' },
                  { seq: 1, ts: now, text: 'dup', kind: 'note', source: 'operator' }]);
    const k = T.getEvents().map((e) => e.kind);
    deq(k.slice(3), ['note', 'finding'], 'old + duplicate notes skipped: ' + k);
    assert.ok(/marker: gust/.test(T.getEvents()[3].text));
    // strip cells
    env.feed(frame(3, 0, 0.5, 0, 0, 0.5, { 'mrac_flags.adaptation_on': 1, 'DroneStatus.FlyMode': 1, real_voltage: 15.4 }));
    T.setMode('fly');
    const strip = env.el('pp-fly-strip').innerHTML;
    for (const label of ['Error 3D', 'Run RMS', 'Above 0.10 m', 'Flight mode', 'Adaptation', 'Vbat', 'Run time']) {
      assert.ok(strip.indexOf(label) >= 0, 'strip has ' + label);
    }
    assert.strictEqual((strip.match(/pp-fly-cell/g) || []).length, 7, 'seven metrics');
    assert.ok(/15\.40 V/.test(strip) && />ON</.test(strip), strip);
    assert.ok(/>SDK</.test(strip), 'flight mode named');
    console.log('  PASS'); env.destroy();
  }

  {
    console.log('\n[CHECK 6: command classification for path execute / stop]');
    const T = loadPanel().T;
    assert.ok(T.isPathExecuteCmd(0x0C, 6, 1) && T.isPathExecuteCmd(0x0B, 7, 1) && T.isPathExecuteCmd(0x11, 7, 1) && T.isPathExecuteCmd(0x0A, 4, 1));
    assert.ok(!T.isPathExecuteCmd(0x0C, 5, 20) && !T.isPathExecuteCmd(0x0C, 6, 0));
    assert.ok(T.isPathStopCmd(0x0C, 6, 0) && T.isPathStopCmd(0x0B, 7, 0) && T.isPathStopCmd(0x11, 7, 0));
    assert.ok(!T.isPathStopCmd(0x0A, 4, 0));
    console.log('  PASS');
  }

  {
    console.log('\n[CHECK 7: mode layout: Plan tools / Fly fills the panel / Review]');
    const env = loadPanel(); const T = env.T;
    assert.strictEqual(T.getMode(), 'plan');
    assert.strictEqual(env.el('pp-sidebar').style.display, '');
    assert.strictEqual(env.el('pp-fly-bar').style.display, 'none');
    T.setMode('fly');
    assert.strictEqual(T.getMode(), 'fly');
    assert.strictEqual(env.el('pp-sidebar').style.display, 'none', 'Plan tools hidden in Fly');
    assert.strictEqual(env.el('pp-fly-bar').style.display, '');
    assert.strictEqual(env.el('pp-3d-wrap').style.display, '', 'Fly opens the 3D view, not 2D');
    assert.strictEqual(env.el('pp-2d-wrap').style.display, 'none');
    assert.ok(/pp-mode-fly/.test(env.el('pp-container').className));
    assert.strictEqual(env.store['pp_mode_v1'], 'fly', 'mode persists');
    T.setMode('review');
    assert.strictEqual(env.el('pp-tools-review').style.display, '');
    assert.strictEqual(env.el('pp-tools-plan').style.display, 'none');
    assert.strictEqual(env.el('pp-fly-bar').style.display, 'none');
    T.setMode('plan');
    assert.strictEqual(env.el('pp-tools-plan').style.display, '');
    assert.strictEqual(T.setMode('nope'), false);
    console.log('  PASS'); env.destroy();
  }

  {
    console.log('\n[CHECK 8: keys: R REC (twice to stop), N note, T trail, only in Fly, never in a text field]');
    const env = loadPanel(); const T = env.T;
    env.key('t');
    assert.strictEqual(T.getFly().trail, 'whole', 'keys ignored outside Fly');
    T.setMode('fly');
    env.key('t'); assert.strictEqual(T.getFly().trail, 'fading');
    env.key('T'); assert.strictEqual(T.getFly().trail, 'whole');
    env.key('r'); assert.strictEqual(env.rec.clicks, 1, 'R starts REC');
    env.sandbox.recOn = true;
    env.key('r'); assert.strictEqual(env.rec.clicks, 1, 'first R while recording only arms the stop');
    assert.ok(/again to stop/.test(env.el('pp-fly-hint').textContent));
    env.key('r'); assert.strictEqual(env.rec.clicks, 2, 'second R stops');
    env.key('n'); assert.strictEqual(env.note.focused, true);
    const before = T.getFly().trail;
    env.key('t', { target: { tagName: 'INPUT' } }); assert.strictEqual(T.getFly().trail, before, 'typing in a field is not a shortcut');
    env.key('t', { ctrlKey: true }); assert.strictEqual(T.getFly().trail, before);
    env.key('1'); env.key('4'); env.key('f');
    const sent = env.gets.filter((g) => g.opts && g.opts.method && g.opts.method !== 'GET');
    assert.strictEqual(sent.length, 0, 'no POST from any shortcut');
    console.log('  PASS'); env.destroy();
  }

  {
    console.log('\n[CHECK 9: destroy removes the key handler and stops polling]');
    const env = loadPanel(); env.T.setMode('fly');
    assert.strictEqual(env.doc.keyHandlers.length, 1);
    env.destroy();
    assert.strictEqual(env.doc.keyHandlers.length, 0);
    console.log('  PASS');
  }

  // ── Review mode ───────────────────────────────────────────────────────
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  // 10 s log at 10 Hz, t = 0 at path execute; constant 3D error `e`.
  const mkLog = (e, extra) => Object.assign({
    name: 'x', label: 'x', t_ref: 'path_execute', has_setpoint: true, has_z: true, truncated: false, n_samples: 101,
    samples: Array.from({ length: 101 }, (_, i) => [i / 10 - 2, i * 0.01, 0, 0.5, 0, 0, 0.5, e, e]),
    events: [{ t: 1, kind: 'adapt', text: 'adaptation ON', pos: [0.1, 0, 0.5] }, { t: 3, kind: 'bogus', text: 'x', pos: null }],
  }, extra || {});
  const RVMAP = {
    '/api/rec-logs/A?': mkLog(0.05, { label: 'hover_pid' }),
    '/api/rec-logs/B?': mkLog(0.20, { label: 'circle_mrac', t_ref: 'rec_start' }),
    '/api/rec-logs/C?': mkLog(null, { label: 'nosp', has_setpoint: false }),
    '/api/rec-logs/D?': mkLog(0.10, { label: 'fourth' }),
    '/api/rec-logs': { logs: [{ name: 'A', label: 'hover_pid', started_at: 1.7e9, duration_s: 10 }, { name: 'B', label: 'circle_mrac', started_at: 1.7e9 + 100, duration_s: 10 }, { name: 'C', label: 'nosp', started_at: 1.7e9 + 200 }, { name: 'D', label: 'fourth', started_at: 1.7e9 + 300 }] },
  };

  {
    console.log('\n[CHECK 10: server samples -> panel points (ms), unknown event kinds dropped]');
    const T = loadPanel().T;
    const c = T.rvConvert(mkLog(0.05));
    assert.strictEqual(c.pts.length, 101);
    assert.ok(near(c.pts[0].t, -2000) && near(c.pts[100].t, 8000), 'seconds -> ms, t = 0 at path execute');
    assert.ok(near(c.pts[5].e3, 0.05) && near(c.pts[5].z, 0.5));
    assert.strictEqual(c.events.length, 1, 'unknown kind dropped');
    assert.ok(near(c.events[0].t, 1000) && near(c.events[0].pos.z, 0.5));
    console.log('  PASS');
  }

  {
    console.log('\n[CHECK 11: pick N logs (4), one colour each, legend RMS / max / % above, no-setpoint honest]');
    const env = loadPanel({ fetchMap: RVMAP }); const T = env.T;
    T.setMode('review');
    await sleep(20);
    assert.ok(/4 saved logs/.test(env.el('pp-rv-status').textContent), env.el('pp-rv-status').textContent);
    const list = env.el('pp-rv-list').innerHTML;
    for (const n of ['A', 'B', 'C', 'D']) assert.ok(list.indexOf('data-rv-name="' + n + '"') >= 0, 'row ' + n);
    ['A', 'B', 'C', 'D'].forEach((n) => T.rvToggle(n, true));
    await sleep(30);
    const rv = T.getRv();
    deq(rv.order, ['A', 'B', 'C', 'D']);
    const colors = rv.order.map((n) => rv.sel[n].color);
    assert.strictEqual(new Set(colors).size, 4, 'one colour per log: ' + colors);
    const leg = env.el('pp-rv-legend').innerHTML;
    assert.ok(/hover_pid/.test(leg) && /circle_mrac/.test(leg));
    // A: 0.05 always below 0.10 -> 0 % ; B: 0.20 always above -> 100 %
    const rowOf = (label) => leg.split('<tr>').find((r) => r.indexOf(label) >= 0);
    assert.ok(/0\.050<\/td><td>0\.050<\/td><td>0 %/.test(rowOf('hover_pid')), rowOf('hover_pid'));
    assert.ok(/0\.200<\/td><td>0\.200<\/td><td>100 %/.test(rowOf('circle_mrac')), rowOf('circle_mrac'));
    assert.ok(/no setpoint/.test(rowOf('nosp')) && !/0\.000/.test(rowOf('nosp')), 'no fabricated zero error');
    assert.ok(/exec/.test(rowOf('hover_pid')) && /rec/.test(rowOf('circle_mrac')), 't = 0 reference shown per log');
    // threshold moves the legend: at 0.25 B is under, at 0.03 A is over
    T.setFly({ thr: 0.25 });
    assert.ok(/0\.200<\/td><td>0\.200<\/td><td>0 %/.test(env.el('pp-rv-legend').innerHTML.split('<tr>').find((r) => r.indexOf('circle_mrac') >= 0)));
    T.setFly({ thr: 0.03 });
    assert.ok(/100 %/.test(env.el('pp-rv-legend').innerHTML.split('<tr>').find((r) => r.indexOf('hover_pid') >= 0)));
    // per-log colour-by-error toggle
    T.rvSetByError('A', true);
    assert.strictEqual(T.getRv().sel.A.byError, true);
    assert.strictEqual(T.getRv().sel.B.byError, false);
    assert.ok(/data-rv-err="A" checked/.test(env.el('pp-rv-list').innerHTML));
    // selection persists
    assert.deepStrictEqual(JSON.parse(env.store['pp_rv_sel_v1']), ['A', 'B', 'C', 'D']);
    T.rvToggle('C', false);
    deq(T.getRv().order, ['A', 'B', 'D']);
    deq(JSON.parse(env.store['pp_rv_sel_v1']), ['A', 'B', 'D']);
    console.log('  PASS'); env.destroy();
  }

  {
    console.log('\n[CHECK 12: shared scrubber range and per-log index; missing log is dropped]');
    const env = loadPanel({ fetchMap: RVMAP }); const T = env.T;
    T.setMode('review'); await sleep(20);
    T.rvToggle('nope', true);
    await sleep(30);
    deq(T.getRv().order, [], 'a 404 log leaves the selection');
    assert.ok(/no longer exists/.test(env.el('pp-rv-status').textContent), env.el('pp-rv-status').textContent);
    T.rvToggle('A', true); T.rvToggle('B', true);
    await sleep(30);
    deq(T.getRv().order, ['A', 'B']);
    const r = T.rvRange();
    assert.ok(near(r.min, -2) && near(r.max, 8), JSON.stringify(r));
    const sc = env.el('pp-rv-scrub');
    assert.ok(parseFloat(sc.min) <= -2 && parseFloat(sc.max) >= 8);
    const pts = T.rvConvert(mkLog(0.05)).pts;
    assert.strictEqual(T.rvIndexAt(pts, -3000), -1);
    assert.strictEqual(T.rvIndexAt(pts, 0), 20);
    assert.strictEqual(T.rvIndexAt(pts, 99999), 100);
    // scrubber input sets the shared time; the end restores "whole log"
    sc.value = '1.5'; sc.handlers.input.forEach((f) => f.call(sc));
    assert.strictEqual(T.getRv().t, 1.5);
    assert.ok(/\+1\.5 s/.test(env.el('pp-rv-time').textContent));
    sc.value = sc.max; sc.handlers.input.forEach((f) => f.call(sc));
    assert.strictEqual(T.getRv().t, null);
    // GET only
    assert.strictEqual(env.gets.filter((g) => g.opts && g.opts.method && g.opts.method !== 'GET').length, 0);
    console.log('  PASS'); env.destroy();
  }

  console.log('\nALL CHECKS PASSED SUCCESSFULLY.');
}
run().catch((e) => { console.error(e); process.exit(1); });
