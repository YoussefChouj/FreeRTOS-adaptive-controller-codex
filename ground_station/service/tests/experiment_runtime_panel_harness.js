'use strict';
/**
 * Offline verification harness for experiment-panel.js (Experiment
 * Runtime), task 20260921-145106. Covers:
 *   1. Honest idle state: tick/samples dash, abort disabled.
 *   2. Start posts the exact captured body {name, settle_ticks,
 *      measure_ticks, parameters}.
 *   3. Polled settling run: badge, settle progress width, abort always
 *      reachable while running, start disabled.
 *   4. Abort posts to /experiments/<name>/abort (captured).
 *   5. Measuring then complete: measure progress, abort disabled only
 *      at complete.
 *   6. Aborted run: bars keep the real fraction in the aborted color,
 *      never a green full bar.
 *   7. samples field absent -> dash, never a fake 0; honest empty logs.
 *
 * Fetch is a recording router over canned bodies. The sandbox has no
 * require or net modules, so a real network call is structurally
 * impossible.
 *
 * Run:  node ground_station/service/tests/experiment_runtime_panel_harness.js
 * Exit code 0 iff every check passes.
 */
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const assert = require('assert');

const PANEL = path.join(__dirname, '..', '..', '..',
  'docs', 'dashboard-platform', 'shell', 'plugins', 'experiment-panel.js');

// ── Fake DOM ──────────────────────────────────────────────────────────────
class Element {
  constructor(id, tag) {
    this.id = id;
    this.tagName = (tag || 'div').toUpperCase();
    this._html = '';
    this.textContent = '';
    this.value = '';
    this.disabled = false;
    this.className = '';
    this.handlers = {};
    this.style = {};
    this.doc = null;
  }
  addEventListener(ev, fn) {
    (this.handlers[ev] = this.handlers[ev] || []).push(fn);
  }
  get classList() {
    const el = this;
    return {
      add(c) { if (!el.className.split(/\s+/).includes(c)) el.className =
        (el.className + ' ' + c).trim(); },
      remove(c) { el.className = el.className.split(/\s+/).filter(
        (x) => x !== c && x !== '').join(' '); },
      contains(c) { return el.className.split(/\s+/).includes(c); },
    };
  }
  get innerHTML() { return this._html; }
  set innerHTML(html) { this._html = html; if (this.doc) this.doc.scan(html); }
  get options() {
    const re = /<option[^>]*\bvalue="([^"]*)"[^>]*>([\s\S]*?)<\/option>/g;
    const out = [];
    let m;
    while ((m = re.exec(this._html)) !== null) {
      out.push({ value: m[1], textContent: m[2] });
    }
    return out;
  }
  appendChild(child) {
    this._html += '<' + (child.tagName || 'div').toLowerCase() +
      (child.value !== undefined && child.value !== '' ? ' value="' + child.value + '"' : '') +
      '>' + (child.textContent || '') + '</' +
      (child.tagName || 'div').toLowerCase() + '>';
  }
}

class FakeDocument {
  constructor() { this.elements = {}; }
  _register(tag, idm, content) {
    const el = new Element(idm[1], tag.slice(1).split(/[\s>]/)[0]);
    el.doc = this;
    if (content !== undefined) {
      el._html = content;
      if (content.indexOf('<') === -1) el.textContent = content;
    }
    this.elements[idm[1]] = el;
    const vm2 = tag.match(/\bvalue="([^"]*)"/);
    if (vm2) el.value = vm2[1];
    // Browser semantics: a select without a value picks the first option.
    if (el.tagName === 'SELECT' && !el.value && content) {
      const om = content.match(/<option[^>]*\bvalue="([^"]*)"/);
      if (om) el.value = om[1];
    }
  }
  scan(html) {
    const openRe = /<([a-zA-Z][\w-]*)((?:"[^"]*"|'[^']*'|[^'">])*)>/g;
    let m;
    while ((m = openRe.exec(html)) !== null) {
      const idm = m[2].match(/\bid="([^"]+)"/);
      if (!idm) continue;
      let content;
      const ci = html.indexOf('</' + m[1] + '>', m.index + m[0].length);
      if (ci !== -1) content = html.slice(m.index + m[0].length, ci);
      this._register(m[0], idm, content);
    }
  }
  getElementById(id) { return this.elements[id] || null; }
  createElement(tag) { return new Element(null, tag); }
}

// ── Manual timers + fixed clock ───────────────────────────────────────────
let _now = 0;
const _tq = [];
let _tid = 1;
function tSet(fn, ms) { const id = _tid++; _tq.push({ id, due: _now + (ms || 0), fn }); return id; }
function tClear(id) {
  const i = _tq.findIndex((t) => t.id === id);
  if (i >= 0) _tq.splice(i, 1);
}
function tInterval(fn, ms) {
  const id = _tid++;
  _tq.push({ id, due: _now + (ms || 0), fn, repeat: ms || 0 });
  return id;
}
function advance(ms) {
  const end = _now + ms;
  for (;;) {
    const t = _tq.filter((x) => x.due <= end).sort((a, b) => a.due - b.due)[0];
    if (!t) break;
    _now = t.due;
    if (t.repeat !== undefined) t.due = _now + t.repeat;
    else _tq.splice(_tq.indexOf(t), 1);
    t.fn();
  }
  _now = end;
}
const micro = () => new Promise((r) => setImmediate(r));

// ── Panel loading with recording fetch router ─────────────────────────────
function loadPanel() {
  const doc = new FakeDocument();
  const container = new Element('container', 'div');
  container.doc = doc;
  const api = {
    registerPanel(name, renderFn) { this.renderFn = renderFn; },
  };
  const records = [];
  const S = {
    experiments: [],
    detail: null,
    contract: { experiment_types: ['step_response', 'controls', 'chirp'] },
  };
  function resp(status, body) {
    return {
      ok: status >= 200 && status < 300, status,
      json: () => Promise.resolve(body),
    };
  }
  const sandbox = {
    document: doc,
    console: { log() {}, warn() {}, error() {} },
    Date, Math, Number, String, Boolean, Array, Object, JSON,
    isNaN, isFinite, parseInt, parseFloat, encodeURIComponent,
    setTimeout: tSet, clearTimeout: tClear,
    setInterval: tInterval, clearInterval: tClear,
    Promise,
    fetch(url, opts) {
      const qi = url.indexOf('?');
      const p = qi === -1 ? url : url.slice(0, qi);
      const method = (opts && opts.method) || 'GET';
      let body = null;
      if (opts && opts.body) body = JSON.parse(opts.body);
      records.push({ method, path: p, body });
      if (p === '/experiments' && method === 'GET') return Promise.resolve(resp(200, S.experiments));
      if (p === '/api/contract') return Promise.resolve(resp(200, S.contract));
      if (p === '/experiments' && method === 'POST') return Promise.resolve(resp(200, { started: true }));
      const dm = p.match(/^\/experiments\/([^/]+)$/);
      if (dm && method === 'GET') {
        return S.detail ? Promise.resolve(resp(200, S.detail))
          : Promise.resolve(resp(404, { error: 'no run' }));
      }
      const da = p.match(/^\/experiments\/([^/]+)\/abort$/);
      if (da && method === 'POST') return Promise.resolve(resp(200, { aborted: true }));
      return Promise.resolve(resp(404, { error: 'unexpected ' + method + ' ' + p }));
    },
  };
  sandbox.window = sandbox;
  sandbox.__registerPlugin__ = function (name, init, destroy) {
    sandbox.pluginInit = init;
    sandbox.pluginDestroy = destroy;
  };
  vm.createContext(sandbox);
  vm.runInContext(fs.readFileSync(PANEL, 'utf8'), sandbox, { filename: PANEL });
  sandbox.pluginInit(api);
  api.renderFn(container);
  return {
    doc, S,
    records: () => records.slice(),
    async click(id) {
      const el = doc.getElementById(id);
      assert.ok(el, 'no #' + id);
      (el.handlers.click || []).forEach((fn) => fn.call(el));
      await micro();
    },
    async poll(ms) { advance(ms); await micro(); },
    destroy() { sandbox.pluginDestroy(); },
  };
}

function run(name, state, tick, extra) {
  return Object.assign({
    name, state, tick, settle_ticks: 100, measure_ticks: 200,
    samples: [{}, {}, {}], events: [], parameters_before: null, parameters_after: null,
  }, extra);
}

function posts(env, pathPart) {
  return env.records().filter((r) => r.method === 'POST' && r.path.indexOf(pathPart) !== -1);
}

function barWidth(html) {
  // Two rects carry width/height: background (252) then the fill.
  const re = /width="(\d+(?:\.\d+)?)" height="16"/g;
  const out = [];
  let m;
  while ((m = re.exec(html)) !== null) out.push(m[1]);
  return out.length > 1 ? out[1] : (out[0] || null);
}

async function runChecks() {
  console.log('--- EXPERIMENT RUNTIME OFFLINE CHECKS ---');

  // 1. Honest idle
  {
    console.log('\n[CHECK 1: idle state honest, abort disabled]');
    const env = loadPanel();
    await micro();
    assert.strictEqual(env.doc.getElementById('ep-state-badge').textContent, 'Idle');
    assert.strictEqual(env.doc.getElementById('ep-tick-label').textContent, 'Tick: —');
    assert.strictEqual(env.doc.getElementById('ep-sample-count').textContent, '— samples');
    assert.strictEqual(env.doc.getElementById('ep-abort-btn').disabled, true);
    assert.strictEqual(env.doc.getElementById('ep-start-btn').disabled, false);
    console.log('  PASS: Idle badge; tick and samples dash; abort disabled');
    env.destroy();
  }

  // 2. Start posts exact body
  {
    console.log('\n[CHECK 2: start posts captured request body]');
    const env = loadPanel();
    await env.click('ep-start-btn');
    const p = posts(env, '/experiments');
    assert.strictEqual(p.length, 1);
    assert.strictEqual(p[0].path, '/experiments');
    assert.strictEqual(p[0].body.name, 'step_response');
    assert.strictEqual(p[0].body.settle_ticks, 100);
    assert.strictEqual(p[0].body.measure_ticks, 200);
    assert.strictEqual(p[0].body.parameters['safety.gs_max_horizontal_speed_mps'], 5);
    console.log('  PASS: POST /experiments body ' + JSON.stringify(p[0].body));
    env.destroy();
  }

  // 3. Settling poll: progress + abort reachable
  {
    console.log('\n[CHECK 3: settling poll renders progress, abort reachable]');
    const env = loadPanel();
    await micro();
    env.S.experiments = [{ name: 'step_response' }];
    env.S.detail = run('step_response', 'settling', 50);
    await env.poll(500);
    assert.strictEqual(env.doc.getElementById('ep-state-badge').textContent, 'Settling…');
    assert.strictEqual(env.doc.getElementById('ep-sample-count').textContent, '3 samples');
    assert.strictEqual(env.doc.getElementById('ep-start-btn').disabled, true);
    assert.strictEqual(env.doc.getElementById('ep-abort-btn').disabled, false,
      'abort must always be reachable while running');
    const w = barWidth(env.doc.getElementById('ep-settle-bar').innerHTML)
    assert.strictEqual(w, '126', '50% of 252 = 126, got ' + w);
    console.log('  PASS: Settling badge, 3 samples, bar 126/252, abort enabled');
    env.destroy();
  }

  // 4. Abort posts
  {
    console.log('\n[CHECK 4: abort posts captured request]');
    const env = loadPanel();
    await micro();
    env.S.experiments = [{ name: 'step_response' }];
    env.S.detail = run('step_response', 'settling', 50);
    await env.poll(500);
    await env.click('ep-abort-btn');
    const p = posts(env, '/abort');
    assert.strictEqual(p.length, 1);
    assert.strictEqual(p[0].path, '/experiments/step_response/abort');
    assert.strictEqual(p[0].body, null);
    console.log('  PASS: POST /experiments/step_response/abort captured');
    env.destroy();
  }

  // 5. Measuring then complete
  {
    console.log('\n[CHECK 5: measuring progress; complete disables abort]');
    const env = loadPanel();
    await micro();
    env.S.experiments = [{ name: 'step_response' }];
    env.S.detail = run('step_response', 'measuring', 200);
    await env.poll(500);
    assert.strictEqual(env.doc.getElementById('ep-state-badge').textContent, 'Measuring…');
    let w = barWidth(env.doc.getElementById('ep-measure-bar').innerHTML)
    assert.strictEqual(w, '126', 'measure (200-100)/200=50%, got ' + w);
    assert.strictEqual(env.doc.getElementById('ep-abort-btn').disabled, false);
    env.S.detail = run('step_response', 'complete', 300);
    await env.poll(500);
    assert.strictEqual(env.doc.getElementById('ep-state-badge').textContent, 'Complete');
    w = barWidth(env.doc.getElementById('ep-measure-bar').innerHTML)
    assert.strictEqual(w, '252', 'complete 100% = 252, got ' + w);
    assert.strictEqual(env.doc.getElementById('ep-abort-btn').disabled, true);
    console.log('  PASS: measure bar 50% then 100%; abort enabled then disabled at complete');
    env.destroy();
  }

  // 6. Aborted run honesty
  {
    console.log('\n[CHECK 6: aborted bars keep real fraction in aborted color]');
    const env = loadPanel();
    await micro();
    env.S.experiments = [{ name: 'step_response' }];
    env.S.detail = run('step_response', 'aborted', 50);
    await env.poll(500);
    assert.strictEqual(env.doc.getElementById('ep-state-badge').textContent, 'Aborted');
    const settle = env.doc.getElementById('ep-settle-bar').innerHTML;
    const measure = env.doc.getElementById('ep-measure-bar').innerHTML;
    assert.strictEqual(barWidth(settle), '126', '50/100 settle = 50% width 126');
    assert.strictEqual(barWidth(measure), '0', 'never measured, width 0');
    assert.ok(settle.indexOf('fill="var(--red)"') !== -1,
      'aborted settle bar must use red, not green');
    assert.strictEqual(env.doc.getElementById('ep-abort-btn').disabled, true);
    console.log('  PASS: aborted settle 50% red, measure 0; no fake completion');
    env.destroy();
  }

  // 7. Absent samples honest; empty log/sweep states
  {
    console.log('\n[CHECK 7: missing samples dash; honest empty log and sweep]');
    const env = loadPanel();
    await micro();
    env.S.experiments = [{ name: 'step_response' }];
    env.S.detail = run('step_response', 'measuring', 120,
      { samples: undefined, events: null });
    await env.poll(500);
    assert.strictEqual(env.doc.getElementById('ep-sample-count').textContent, '— samples',
      'absent samples must read dash, never 0');
    assert.ok(env.doc.getElementById('ep-event-log').innerHTML.indexOf('No events') !== -1);
    assert.ok(env.doc.getElementById('ep-params-body').innerHTML.indexOf('No sweep data') !== -1);
    console.log('  PASS: absent samples dash; No events; No sweep data');
    env.destroy();
  }

  console.log('\nALL CHECKS PASSED SUCCESSFULLY.');
}

(async function () {
  try { await runChecks(); } catch (e) { console.error(e); process.exit(1); }
}());
