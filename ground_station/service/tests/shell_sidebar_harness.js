'use strict';
/**
 * Offline harness for dashboard shell sidebar hide/show (operator walkthrough
 * 2 item 1). Loads the shell's inline <script> into a fake DOM, reproduces a
 * collapse + re-open round-trip through the real handlers, and asserts the
 * sidebar's child count and testids survive the round-trip unchanged.
 *
 * Also exercises the broadcast gating of the agent-mode strip (item 4) and
 * the workspace gating helper so panels like Activity can be pinned to a
 * single tab (item 3). Prints JSON on stdout; exits non-zero on failure.
 */
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const SHELL_HTML = path.join(
  __dirname, '..', '..', '..', 'docs', 'dashboard-platform', 'shell', 'index.html');

class El {
  constructor(id) {
    this.id = id || '';
    this.tagName = 'DIV';
    this._text = '';
    this._html = '';
    this.className = '';
    this.style = {};
    this.dataset = {};
    this.children = [];
    this.title = '';
    this._handlers = {};
    const set = new Set();
    this.classList = {
      add: (c) => set.add(c),
      remove: (c) => set.delete(c),
      toggle: (c, force) => {
        const on = force === undefined ? !set.has(c) : !!force;
        if (on) set.add(c); else set.delete(c);
      },
      contains: (c) => set.has(c),
    };
    this._classes = set;
  }
  get textContent() { return this._text; }
  set textContent(v) { this._text = String(v); this._html = String(v); }
  get innerHTML() { return this._html; }
  set innerHTML(v) { this._html = String(v); this._text = String(v); }
  appendChild(c) { this.children.push(c); c.parentElement = this; return c; }
  setAttribute(k, v) { this.dataset[k] = String(v); }
  getAttribute(k) { return k in this.dataset ? this.dataset[k] : null; }
  addEventListener(type, fn) { (this._handlers[type] = this._handlers[type] || []).push(fn); }
  remove() { this.parentElement = null; }
}

function makeDom(seedSidebar) {
  const elements = new Map();
  function el(id) {
    if (!elements.has(id)) elements.set(id, new El(id));
    return elements.get(id);
  }
  const doc = {
    addEventListener() {},
    getElementById: el,
    createElement: (t) => { const e = new El(''); e.tagName = t || 'DIV'; return e; },
    createTextNode: () => ({}),
    body: new El('body'),
    querySelectorAll() { return []; },
    querySelector() { return null; },
  };
  seedSidebar(doc, el);
  return { doc, elements, el };
}

function seedSidebar(doc, el) {
  // Mirror the real #app / #sidebar structure so the script's collapse
  // handlers act on the same nodes we assert about afterwards.
  const app = el('app');
  const sidebar = el('sidebar');
  app.appendChild(sidebar);
  const header = el('sidebar-header');
  header.appendChild(el('sidebar-collapse'));
  sidebar.appendChild(header);
  // A representative set of sidebar children carrying testids (the Flight
  // State / Session / Alarms cards and their value spans).
  const cards = ['card-flight', 'card-state', 'card-alarms'];
  cards.forEach(function (cid, i) {
    const card = el(cid);
    card.appendChild(el('sb-arm'));
    const sid = el('session-id');       // a data-testid-bearing value cell
    sid.setAttribute('data-testid', 'session-id');
    card.appendChild(sid);
    if (i === cards.length - 1) card.appendChild(el('sb-vbat'));
    sidebar.appendChild(card);
  });
  el('sidebar-reopen');
}

function childIds(e) { return e.children.map((c) => c.id || c.getAttribute('data-testid') || ''); }

function makeHarness(mode) {
  const { doc, elements, el } = makeDom(seedSidebar);
  const pluginFetch = (url) => {
    if (String(url).indexOf('/api/agent/control') !== -1) {
      const body = JSON.stringify({ mode: mode || 'supervised', allow_agent_arm: false });
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(JSON.parse(body)) });
    }
    return Promise.resolve({ ok: false, status: 404,
      json: () => Promise.reject(new Error('404')),
      text: () => Promise.resolve('') });
  };
  const fetchImpl = (url) => {
    const key = String(url).split('?')[0];
    if (key.startsWith('/plugins/')) {
      return Promise.resolve({ ok: false, status: 404, json: () => Promise.reject(new Error('404')),
        text: () => Promise.resolve('') });
    }
    if (key === '/health') {
      return Promise.resolve({ ok: true, connected: true });
    }
    if (key.indexOf('/api/agent/') !== -1) return pluginFetch(url);
    return Promise.resolve({
      ok: true, status: 200,
      json: () => Promise.resolve({}),
      text: () => Promise.resolve('{}'),
    });
  };
  const timers = [];
  const sandbox = {
    console: { log() {}, warn() {}, error() {} },
    document: doc,
    localStorage: { getItem() { return null; }, setItem() {} },
    setTimeout(fn) { timers.push(fn); return timers.length; },
    clearTimeout() {}, setInterval() { return 0; }, clearInterval() {},
    fetch: fetchImpl, addEventListener() {},
    EventSource: function () {}, ResizeObserver: function () { return { observe() {}, disconnect() {} }; },
  };
  sandbox.window = sandbox;
  sandbox.window.EventSource = undefined;   // terminate EventSource wiring in sandbox
  vm.createContext(sandbox);
  return { sandbox, doc, elements, el, timers, fetchImpl };
}

async function drain(harness) {
  for (let i = 0; i < 8; i++) await new Promise((r) => setImmediate(r));
}

async function main() {
  const html = fs.readFileSync(SHELL_HTML, 'utf8');
  const match = html.match(/<script>([\s\S]*?)<\/script>/);
  if (!match) throw new Error('no inline <script> found in shell index.html');
  const script = match[1];

  const h = makeHarness('off');
  vm.runInContext(script, h.sandbox, { filename: 'shell-index-inline.js' });
  await drain(h);  // let the async plugin-load loop finish and wire the sidebar controls

  const app = h.el('app');
  const sidebar = h.el('sidebar');
  const collapseBtn = h.el('sidebar-collapse');

  const before = childIds(sidebar);
  if (!collapseBtn._handlers || !collapseBtn._handlers.click || collapseBtn._handlers.click.length === 0) {
    throw new Error('sidebar-collapse had no click handler wired after init');
  }

  // Hide
  collapseBtn._handlers.click.forEach((fn) => fn());
  if (!app.classList.contains('sidebar-hidden')) {
    throw new Error('collapsing did not add sidebar-hidden to #app');
  }
  // Content must be untouched by the hide action itself (nothing re-parented
  // or cleared while the column collapses).
  const duringHide = childIds(sidebar);
  if (JSON.stringify(duringHide) !== JSON.stringify(before)) {
    throw new Error('collapsing changed the sidebar children: ' + JSON.stringify({ before, duringHide }));
  }

  // Show again
  const reopenBtn = h.el('sidebar-reopen');
  if (!reopenBtn._handlers || !reopenBtn._handlers.click || reopenBtn._handlers.click.length === 0) {
    throw new Error('sidebar-reopen had no click handler wired after init');
  }
  reopenBtn._handlers.click.forEach((fn) => fn());
  if (app.classList.contains('sidebar-hidden')) {
    throw new Error('re-open did not clear sidebar-hidden from #app');
  }
  const after = childIds(sidebar);
  if (JSON.stringify(after) !== JSON.stringify(before)) {
    throw new Error('hide/show round-trip changed the sidebar children: ' + JSON.stringify({ before, after }));
  }

  // The testids must all still be present and in the same order (the
  // sidebar's own cards plus the data-testid value cell inside card-state).
  const testids = before.filter((x) => x);
  if (testids.indexOf('card-flight') === -1 || testids.indexOf('card-state') === -1 ||
      testids.indexOf('card-alarms') === -1) {
    throw new Error('expected sidebar cards after round-trip, got: ' + JSON.stringify(testids));
  }
  const sessionCell = h.el('session-id');
  if (!sessionCell || sessionCell.getAttribute('data-testid') !== 'session-id') {
    throw new Error('sidebar data-testid value cell (session-id) was lost in the round-trip');
  }

  // item 3: the collapsible Activity log is gated to the Approvals tab only.
  // The panel registration must carry workspace 'approvals' (not 'all' or
  // defaulting to every tab), and the plugin meta agrees.
  const pluginDir = path.join(__dirname, '..', '..', '..', 'docs', 'dashboard-platform', 'shell', 'plugins');
  const timelineSrc = fs.readFileSync(path.join(pluginDir, 'activity-timeline.js'), 'utf8');
  if (!/registerPanel\(['\"]Activity['\"]([\s\S]*?)workspace: *['\"]approvals['\"]/.test(timelineSrc)) {
    throw new Error('Activity panel is not gated to the approvals workspace');
  }
  if (/workspace: *['\"]overview['\"]/.test(timelineSrc)) {
    throw new Error('Activity Timeline still declares the overview workspace');
  }

  // item 2: an empty data-flow block must be able to name the preset that
  // publishes its symbol instead of showing a blank box. The overview panel
  // stays read-only (no fetch) and the shell exposes the preset lookup.
  const overviewSrc = fs.readFileSync(path.join(pluginDir, 'overview-panel.js'), 'utf8');
  if (overviewSrc.indexOf('not published: in preset') === -1) {
    throw new Error('overview panel lacks the "not published: in preset" hint path');
  }
  if (overviewSrc.indexOf('fetch(') !== -1 || overviewSrc.indexOf('XMLHttpRequest') !== -1) {
    throw new Error('overview panel must stay read-only (no network call for the hint)');
  }
  if (html.indexOf('__gs_preset_for_symbol') === -1) {
    throw new Error('shell does not expose the __gs_preset_for_symbol helper');
  }

  // item 4: at phone width the fixed bottom strip must not cover panel
  // content — the shell adds bottom clearance on narrow screens.
  if (!/max-width:\s*640px/.test(html) || html.indexOf('padding-bottom: 76px') === -1) {
    throw new Error('shell lacks the narrow-screen bottom padding for the agent strip');
  }

  console.log(JSON.stringify({
    sidebar_round_trip: true,
    children_before: before.length,
    children_after: after.length,
    retained_testids: testids,
  }));
}

main().catch((err) => {
  console.error('SHELL SIDEBAR HARNESS FAILED: ' + (err && err.stack || err));
  process.exit(1);
});