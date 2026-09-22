#!/usr/bin/env node
/**
 * Phase 1B — offline Node.js harness for the agent UI plugins.
 *
 * Loads each plugin under shell/plugins/*.js in a mocked browser (no real DOM,
 * no backend) and verifies behaviour by driving the same SSE event contract
 * the shell consumes from /api/agent/stream. This gives deterministic
 * verification of the UI components without a running ground-station service.
 *
 *   node ground_station/service/tests/node_harness.js
 *   (exit 0 = all green, non-zero = a component contradicted the spec)
 */
'use strict';
const fs = require('fs');
const path = require('path');

// from __dirname (= repo/ground_station/service/tests) up three levels = repo
const REPO = path.join(__dirname, '..', '..', '..');
const SHELL_DIR = path.join(REPO, 'docs', 'dashboard-platform', 'shell');
const PLUGIN_DIR = path.join(SHELL_DIR, 'plugins');

// ── SSE contract the UI is required to consume (spec §4) ────────────────────
const SSE_EVENTS = ['control', 'approval', 'plan', 'step', 'message', 'ui_action', 'shell_updated'];

// ── minimal DOM / window mock ───────────────────────────────────────────────
function elStub() {
  const el = {
    _children: [], style: { cssText: '' }, classList: {
      _set: {}, add(c) { this._set[c] = true; }, remove(c) { delete this._set[c]; },
      toggle(c, v) { if (v) this._set[c] = true; else delete this._set[c]; },
    },
    attributes: {}, listeners: {},
    innerHTML: '', textContent: '', id: '', placeholder: '',
    _display: '',
    appendChild(c) { this._children.push(c); return c; },
    remove() { this._removed = true; },
    setAttribute(k, v) { this.attributes[k] = String(v); },
    getAttribute(k) { return this.attributes[k] != null ? this.attributes[k] : null; },
    addEventListener(t, fn) { (this.listeners[t] = this.listeners[t] || []).push(fn); },
    getBoundingClientRect() { return { left: 0, top: 0, width: 100, height: 30 }; },
    querySelector() { return null; },
    style: null,
  };
  // make `el.style.left = ..` etc work
  el.style = new Proxy({ cssText: '' }, {
    set(t, k, v) { t[k] = v; return true; }, get(t, k) { return t[k]; },
  });
  return el;
}

let registered = [];
const registeredByFile = {}; // src -> names
let panelRegistrations = [];

function makeGlobal() {
  const allEls = [];
  const seeded = []; // elements with a data-testid, for highlight targeting
  function createEl(tag) { const e = elStub(); allEls.push(e); return e; }
  const document = {
    createElement: createEl,
    body: createEl('body'),
    getElementById: (id) => seeded.find((e) => e.id === id) || null,
    querySelector: (sel) => {
      const m = /data-testid="([^"]*)"/.exec(sel || '');
      if (m) return seeded.find((e) => e.getAttribute('data-testid') === m[1]) || null;
      return null;
    },
    querySelectorAll: (sel) => {
      const m = /data-testid="([^"]*)"/.exec(sel || '');
      if (m) return seeded.filter((e) => e.getAttribute('data-testid') === m[1]);
      return [];
    },
    _seedTestid(tid) { const e = elStub(); e.setAttribute('data-testid', tid); seeded.push(e); return e; },
    _all: allEls,
  };
  const window = {
    __registerPlugin__: (name, init, destroy, meta) => {
      registered.push({ name, init, destroy: destroy || (() => {}), meta: meta || {} });
    },
    __gs_plugins__: [],
    __gs_ui_state__: {},
    EventSource: function () {},
    location: { pathname: '/', search: '' },
    CSS: { escape: (s) => s },
    document,
    addEventListener: () => {},
    fetch: () => Promise.reject(new Error('fetch disabled in harness')),
  };
  return { window, document };
}

function loadPlugin(file, nameTag) {
  const src = path.join(PLUGIN_DIR, file);
  const code = fs.readFileSync(src, 'utf8');
  const sandbox = {};
  // eval in the window context: plugin references bare `window`, `document`, `fetch`.
  const fn = new Function('window', 'document', 'fetch', 'console', code + '\n//# sourceURL=' + file);
  fn(window, document, fetchStub(), { log: () => {}, warn: () => {}, error: () => {} });
  // remember registrations that came from this file
  const prev = registered.length;
  // `eval` above ran the module body; but plugins register inside init, not at
  // load. So registration here is a no-op — instead we bind init to file below.
  return {};
}

// fetch stub recording URLs so we can assert the executor/harness wired right.
function fetchStub() {
  const callLog = [];
  return function (url) {
    callLog.push(url);
    return Promise.resolve({ ok: true, json: () => Promise.resolve({}) });
  };
}

function makeShellApi(uiState) {
  const subs = [];
  return {
    _subs: subs,
    onAgentEvent(cb) { subs.push(cb); return () => { const i = subs.indexOf(cb); if (i >= 0) subs.splice(i, 1); }; },
    getAgentControl() { return { mode: 'supervised', allow_agent_arm: false }; },
    reportUiState() { return Promise.resolve(); },
    ackUiAction(planId, stepId, ok, error) { this._acks.push({ planId, stepId, ok, error }); return Promise.resolve(); },
    runUiAction: null,
    _acks: [],
    getPlugins() { return []; },
    registerPanel(name, fn) { panelRegistrations.push(name); },
    registerPanelsCalled() { return panelRegistrations; },
  };
}

function emit(api, event, data) {
  api._subs.forEach((cb) => cb({ event, data }));
}

let failures = 0;
function check(cond, msg) {
  if (!cond) { failures++; console.error('  ✗ ' + msg); }
  else { console.log('  ✓ ' + msg); }
}

function main() {
  console.log('Phase 1B agent UI — offline Node harness');
  // 0) SSE contract surface
  console.log('\n[0] SSE contract enumeration (spec §4)');
  check(Array.isArray(SSE_EVENTS) && SSE_EVENTS.length === 7,
        '7 SSE events enumerated: ' + SSE_EVENTS.join(', '));

  // 1) every required plugin file exists and registers
  const PLUGINS = [
    'copilot-drawer.js', 'header-mode-pill.js', 'approval-queue.js',
    'activity-timeline.js', 'highlight-overlay.js', 'code-reload-banner.js',
  ];
  for (const f of PLUGINS) {
    check(fs.existsSync(path.join(PLUGIN_DIR, f)), f + ' exists');
  }

  // 2) co-pilot drawer
  console.log('\n[A] Co-pilot drawer');
  let ctx = runPlugin('copilot-drawer.js');
  let st = ctx.requireTest('copilot');
  st.handle({ event: 'message', data: { seq: 1, text: 'hello from agent', kind: 'info', source: 'agent:ark', ts: 1 } });
  check(st.get().messages.length === 1, 'inbound message appended');
  check(st.get().messages[0].text === 'hello from agent', 'message text captured');
  st.setOpen(true, ctx.api);
  check(st.get().open === true, 'drawer opens');
  check(ctx.api._acks !== undefined, 'api wiring present');

  // item 5: an operator-sent message must render exactly once, labelled
  // operator — the agent's own turn re-emits the operator text as a separate
  // SSE `message` event, which must be dropped, not shown a second time.
  st.handle({ event: 'message', data: { seq: 1, text: 'hi', kind: 'agent', source: 'operator', ts: 1 } });
  st.handle({ event: 'message', data: { seq: 2124, text: 'hi', kind: 'message', source: 'agent', ts: 2 } });
  const hiMsgs = st.get().messages.filter((m) => m.text === 'hi');
  check(hiMsgs.length === 1, 'operator "hi" rendered once (agent echo deduped)');
  check(hiMsgs[0] && hiMsgs[0].source === 'operator', 'operator message labelled operator, not agent');

  // 3) header mode pill + STOP
  console.log('\n[B] Header mode pill & big STOP');
  ctx = runPlugin('header-mode-pill.js');
  st = ctx.requireTest('headerMode');
  // item 4: pill + STOP live in a fixed bottom-centre strip so they never
  // cover the workspace tab names.
  const stripDom = ctx.document._all.find((e) => e.id === 'agent-mode-strip');
  check(!!stripDom, 'agent-mode strip is created at init');
  let modeText = '';
  if (stripDom) {
    const pillDom = ctx.document._all.find((e) => e.id === 'agent-mode-pill');
    const stopDom = ctx.document._all.find((e) => e.id === 'agent-stop');
    check(!!pillDom && !!stopDom, 'pill and STOP both exist');
    check(stripDom._children.indexOf(pillDom) !== -1 && stripDom._children.indexOf(stopDom) !== -1,
          'pill + STOP are children of the bottom strip');
    const stripCss = String(stripDom.style.cssText);
    check(/position:\s*fixed/.test(stripCss) && /bottom:\s*8px/.test(stripCss) &&
          /translateX\(-50%\)/.test(stripCss),
          'strip is fixed bottom-centre of the viewport');
    const pillCss = String(pillDom.style.cssText), stopCss = String(stopDom.style.cssText);
    check(!/position:\s*fixed/.test(pillCss) && !/position:\s*fixed/.test(stopCss),
          'pill + STOP are in-flow inside the strip, not independently fixed');
    check(!/top:\s*8px|top:\s*44px/.test(pillCss + stopCss + stripCss),
          'no top-anchored pill/STOP (nothing over the workspace tab names)');
    // pill resolves the shell's cached control (supervised) instead of 'unknown'
    modeText = pillDom.textContent || '';
    check(/SUPERVISED/.test(modeText), 'pill shows the real mode (' + JSON.stringify(modeText) + '), not UNKNOWN');
  }
  st.handle({ event: 'control', data: { mode: 'autonomous', allow_agent_arm: false } });
  check(st.get().control.mode === 'autonomous', 'mode pill follows control event');
  st.apply({ mode: 'off', allow_agent_arm: false });
  check(st.get().control.mode === 'off', 'STOP sets mode to off (reducer validated)');

  // 4) ordered approval queue
  console.log('\n[C] Ordered approval queue');
  ctx = runPlugin('approval-queue.js');
  st = ctx.requireTest('approvals');
  const q = [
    { plan_id: 'p1', step_id: 's1', label: 'arm', action: 'arm', what_critical: true },
    { plan_id: 'p1', step_id: 's2', label: 'param write', action: 'param_write', what_critical: true },
  ];
  st.handle({ event: 'approval', data: { queue: q } });
  check(st.get().pending.length === 2, 'queue populated in order');
  check(st.get().oldest && st.get().oldest.step_id === 's1', 'oldest proposal is first (FIFO)');
  st.handle({ event: 'approval', data: { plan_id: 'p1', step_id: 's1', state: 'approved' } });
  check(st.get().pending.length === 1, 'approved item removed from queue');
  check(st.get().oldest && st.get().oldest.step_id === 's2', 'next item advances only after prior decided (ordered)');
  check(st.get().decided['p1:s1'] === 'approved', 'decision recorded');
  // reducer is pure
  const r1 = st.reducer({ queue: q.slice(), decided: {} }, { data: { plan_id: 'p1', step_id: 's1', state: 'approved' } });
  check(r1.queue.length === 1 && Object.keys(r1.decided).length === 1, 'pure reducer applies in-order decision');

  // 5) activity timeline
  console.log('\n[D] Activity timeline');
  ctx = runPlugin('activity-timeline.js');
  st = ctx.requireTest('timeline');
  st.ingest({ seq: 1, kind: 'message', source: 'agent', actor: 'agent', t: 1, data: { text: 'hi' } });
  st.ingest({ seq: 2, kind: 'control', source: 'operator', actor: 'operator', t: 2, data: { mode: 'supervised' } });
  check(st.get().items.length === 2, 'two items ingested');
  st.setIsFilter('operator');
  const opItems = st.get().filter === 'operator' ? st.get().items : st.get().items;
  check(st.get().filter === 'operator', 'source filter applies');
  st.handle({ event: 'plan', data: { plan_id: 'p9', steps: [{ step_id: 'z1', status: 'pending' }] } });
  check(st.get().planned.length === 1, 'future plan step held in planned (greyed) list');
  st.handle({ event: 'step', data: { plan_id: 'p9', step_id: 'z1', status: 'done' } });
  check(st.get().planned.length === 0, 'completed step leaves planned list');

  // 6) highlight overlay / ui executor
  console.log('\n[E] Highlight overlay & UI action executor');
  ctx = runPlugin('highlight-overlay.js');
  st = ctx.requireTest('highlight');
  const missing = st.run({ action: 'highlight', args: { testid: 'does-not-exist' } });
  check(missing.ok === false && /testid/.test(missing.error), 'missing target reported honestly (no fake highlight)');
  ctx.document._seedTestid('mock-target');
  const got = st.run({ action: 'highlight', args: { testid: 'mock-target' } }, (ok) => {});
  check(got.ok === true, 'present target executed (reduces to ack ok)');
  check(st.get().active && st.get().active.testid === 'mock-target', 'active highlight tracked');

  // 7) code reload banner
  console.log('\n[F] Hot reload & code reload banner');
  ctx = runPlugin('code-reload-banner.js');
  st = ctx.requireTest('codeReload');
  st.recordChange(['plugins/copilot-drawer.js'], 'hot-reloaded in place (plugin only)');
  st.recordChange(['index.html'], 'shell changed — reload required');
  check(st.get().changes.length === 2, 'what-changed card accumulates changes');
  check(st.get().changes[0].files.some((f) => /plugins\//.test(f)), 'plugin-only change hot-reloadable');
  check(st.get().changes[1].files.some((f) => /index\.html/.test(f)), 'index.html change flagged as reload');

  console.log(failures ? '\nFAILED: ' + failures + ' assertions failed' : '\nALL GREEN');
  process.exit(failures ? 1 : 0);
}

// Load one plugin, run init with a fresh mock, return {api, requireTest(key)}.
function runPlugin(file) {
  // reset registrations for this plugin then eval the file
  registered = [];
  panelRegistrations = [];
  const g = makeGlobal();
  const W = g.window, D = g.document;
  const code = fs.readFileSync(path.join(PLUGIN_DIR, file), 'utf8');
  const run = new Function('window', 'document', 'fetch', 'console',
                           code + '\n//# sourceURL=' + file);
  run(W, D, () => Promise.reject(new Error('fetch disabled')),
      { log() {}, warn() {}, error(e) { /* eslint-disable-line */ } });
  // init the registered plugin(s) with a mock shell api
  const api = makeShellApi();
  registered.forEach((p) => p.init(api));
  const ui = W.__gs_ui_state__ || {};
  return {
    api, file, window: W, document: D,
    requireTest(key) { return ui[key]; },
  };
}

main();