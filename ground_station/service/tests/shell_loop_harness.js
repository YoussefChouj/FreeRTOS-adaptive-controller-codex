'use strict';
/**
 * Offline harness for the dashboard shell's poll/render loop (Bug 1,
 * AUDIT_2026-09-21 §Bug 1). Loads the shell's inline <script> from
 * docs/dashboard-platform/shell/index.html into a fake DOM + fake fetch
 * and verifies the loop hardening:
 *
 *   1. A plugin whose state callback throws on EVERY poll must not stop
 *      the loop: the sample counter keeps advancing, the status badge
 *      stays connected, and the error is shown inside that plugin's own
 *      card only (no error box in the healthy panel).
 *   2. A failed request (/state rejecting) must not stop the loop; the
 *      next tick recovers and the badge returns to connected.
 *
 * Prints a JSON result on stdout; exits non-zero on failure.
 */

const fs = require('fs');
const path = require('path');
const vm = require('vm');

const SHELL_HTML = path.join(
  __dirname, '..', '..', '..', 'docs', 'dashboard-platform', 'shell', 'index.html');

// ── Minimal DOM stubs ────────────────────────────────────────────────────
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
  }
  get textContent() { return this._text; }
  set textContent(v) { this._text = String(v); this._html = String(v); }
  get innerHTML() { return this._html; }
  set innerHTML(v) { this._html = String(v); this._text = String(v); }
  appendChild(child) { this.children.push(child); child.parentElement = this; return child; }
  setAttribute(name, val) { this.dataset[name] = String(val); }
  getAttribute(name) { return name in this.dataset ? this.dataset[name] : null; }
  addEventListener() {}
}

// ── Harness ──────────────────────────────────────────────────────────────
function makeHarness() {
  const elements = new Map();
  const errors = [];
  const timers = [];

  const doc = {
    addEventListener() {},
    getElementById(id) {
      if (!elements.has(id)) elements.set(id, new El(id));
      return elements.get(id);
    },
    createElement() { return new El(''); },
    querySelectorAll() { return []; },
    querySelector() { return null; },
    body: new El('body'),
  };

  const responses = {};  // url -> {json} | {reject:true}
  let stateFactory = () => ({});
  const fetchImpl = (url) => {
    const key = String(url).split('?')[0];
    if (key.startsWith('/plugins/')) {
      return Promise.resolve({ ok: false, status: 404,
        json: () => Promise.reject(new Error('404')),
        text: () => Promise.resolve('') });
    }
    if (responses[key] && responses[key].reject) {
      return Promise.reject(new Error('network down for ' + key));
    }
    const payload = key === '/health'
      ? { ok: true, connected: true }
      : stateFactory();
    return Promise.resolve({
      ok: true, status: 200,
      json: () => Promise.resolve(payload),
      text: () => Promise.resolve(JSON.stringify(payload)),
    });
  };

  const sandbox = {
    console: {
      log() {},
      warn() {},
      error(...args) { errors.push(args.map(String).join(' ')); },
    },
    document: doc,
    localStorage: { getItem() { return null; }, setItem() {} },
    setTimeout(fn) { timers.push(fn); return timers.length; },
    clearTimeout() {},
    setInterval() { return 0; },
    clearInterval() {},
    fetch: fetchImpl,
    addEventListener() {},
  };
  sandbox.window = sandbox;
  vm.createContext(sandbox);
  return { sandbox, doc, elements, errors, timers, responses,
           setStateFactory(f) { stateFactory = f; } };
}

async function drain() {
  for (let i = 0; i < 6; i++) await new Promise((r) => setImmediate(r));
}

async function fireTimers(harness) {
  const pending = harness.timers.splice(0);
  if (pending.length === 0) throw new Error('poll loop stopped: no timer armed');
  for (const fn of pending) fn();
  await drain();
}

function shellState(samples) {
  return {
    schema_id: 'r1-s1-TEST',
    session_id: 'sess-test',
    connected: true,
    samples: samples,
    last_update_ns: Date.now() * 1e6,
    slot_freshness_ttl_ns: 30e9,
    streams: {
      '0': {
        sequence: samples,
        loss_pct: 0.0,
        received: samples,
        dropped: 0,
        last_update_ns: Date.now() * 1e6,
        values: { 'status.arm': 0 },
      },
    },
    last_transaction_result: null,
    command_results: [],
  };
}

async function main() {
  const html = fs.readFileSync(SHELL_HTML, 'utf8');
  const match = html.match(/<script>([\s\S]*?)<\/script>/);
  if (!match) throw new Error('no inline <script> found in shell index.html');
  const script = match[1];

  const harness = makeHarness();
  let samples = 0;
  harness.setStateFactory(() => shellState(samples));

  vm.runInContext(script, harness.sandbox, { filename: 'shell-index-inline.js' });
  await drain();  // let the initial poll() complete and arm the 500 ms tick

  const api = harness.sandbox.__gs_shell_api__;
  if (!api) throw new Error('shell did not expose __gs_shell_api__');

  // Two panels: one healthy, one whose callback throws on every poll.
  let goodRenders = 0;
  api.registerPanel('Good Panel', function () {
    api.subscribe(function () { goodRenders++; });
  }, { workspace: 'all' });
  api.registerPanel('Bad Panel', function () {
    api.subscribe(function () { throw new Error('bad panel boom'); });
  }, { workspace: 'all' });

  // ── Phase 1: three ticks with a throwing plugin callback ────────────
  for (let i = 0; i < 3; i++) {
    samples++;
    await fireTimers(harness);
    const samplesText = harness.doc.getElementById('samples').textContent;
    if (samplesText !== samples.toLocaleString('en-US') &&
        samplesText !== samples.toLocaleString()) {
      throw new Error('sample counter froze at tick ' + (i + 1) +
        ': got ' + JSON.stringify(samplesText) + ' expected ' + samples);
    }
    const status = harness.doc.getElementById('status').className;
    if (status.indexOf('connected') === -1) {
      throw new Error('status badge not connected at tick ' + (i + 1) +
        ': ' + status);
    }
  }
  if (goodRenders !== 3) {
    throw new Error('healthy plugin callback ran ' + goodRenders +
      ' times, expected 3 — one bad panel killed the others');
  }

  // The throwing plugin must be reported inside its own card only.
  const badErr = harness.elements.get('plugin-err-bad-panel');
  if (!badErr) throw new Error('no error box rendered inside the bad panel');
  if (String(badErr.textContent).indexOf('boom') === -1) {
    throw new Error('bad panel error box lacks the error text: ' +
      JSON.stringify(badErr.textContent));
  }
  if (harness.elements.has('plugin-err-good-panel')) {
    throw new Error('healthy panel got an error box it did not earn');
  }
  if (harness.errors.length === 0) {
    throw new Error('expected the throwing callback to be logged');
  }

  // ── Phase 2: one failed request must not stop the loop ──────────────
  harness.responses['/state'] = { reject: true };
  samples++;
  await fireTimers(harness);   // tick fails: /state rejects
  const errStatus = harness.doc.getElementById('status').className;
  if (errStatus.indexOf('error') === -1) {
    throw new Error('expected error badge on failed request, got: ' + errStatus);
  }
  delete harness.responses['/state'];
  samples++;
  await fireTimers(harness);   // next tick recovers
  const okStatus = harness.doc.getElementById('status').className;
  if (okStatus.indexOf('connected') === -1) {
    throw new Error('loop did not recover after failed request: ' + okStatus);
  }
  const finalSamples = harness.doc.getElementById('samples').textContent;
  if (finalSamples !== samples.toLocaleString('en-US') &&
      finalSamples !== samples.toLocaleString()) {
    throw new Error('sample counter wrong after recovery: ' +
      JSON.stringify(finalSamples) + ' expected ' + samples);
  }
  // 3 phase-1 ticks + the failed tick (no state to dispatch) + recovery.
  if (goodRenders !== 4) {
    throw new Error('healthy plugin callback ran ' + goodRenders +
      ' times, expected 4');
  }

  console.log(JSON.stringify({
    ticks_survived: 5,
    good_panel_renders: goodRenders,
    bad_panel_error_shown_in_panel: true,
    good_panel_untouched: true,
    recovered_from_failed_request: true,
    final_status: 'connected',
    final_samples: finalSamples,
    console_errors_logged: harness.errors.length,
  }));
}

main().catch((err) => {
  console.error('SHELL LOOP HARNESS FAILED: ' + (err && err.stack || err));
  process.exit(1);
});
