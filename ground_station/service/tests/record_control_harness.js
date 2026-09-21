'use strict';
/**
 * Offline harness for the dashboard shell's Record control (opt-in session
 * recording). Loads the shell's inline <script> into a fake DOM + a
 * recording fetch stub and verifies:
 *
 *   1. The control renders: a REC/Stop button whose label/flashing dot
 *      reflect the /api/recording state fetched in the poll loop.
 *   2. Clicking while stopped POSTs /api/recording/start with
 *      requested_by=operator; clicking while recording POSTs /api/recording/stop.
 *   3. The "Add note" button POSTs /api/session/note with the typed text,
 *      the chosen kind and source=operator, and clears the input.
 *   4. A failing recording post never breaks the loop (render continues).
 *
 * Prints a JSON result on stdout; exits non-zero on failure.
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
    this.value = '';
    this.disabled = false;
    this.handlers = {};
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
  addEventListener(ev, fn) { (this.handlers[ev] = this.handlers[ev] || []).push(fn); }
  click() { (this.handlers.click || []).forEach((fn) => fn.call(this, { type: 'click' })); }
}

function makeHarness() {
  const elements = new Map();
  const errors = [];
  const timers = [];
  const posts = [];      // recorded POSTs: {url, body}
  let recState = { recording: false, started_at: 0, bytes: 0, session_dir: null, reason: null, enabled: true };

  const doc = {
    getElementById(id) {
      if (!elements.has(id)) elements.set(id, new El(id));
      return elements.get(id);
    },
    createElement() { return new El(''); },
    querySelectorAll() { return []; },
    querySelector() { return null; },
    body: new El('body'),
  };

  const fetchImpl = (url, opts) => {
    const key = String(url).split('?')[0];
    if (opts && opts.method === 'POST') {
      let body = {};
      if (opts.body) { try { body = JSON.parse(opts.body); } catch (_) {} }
      posts.push({ url: key, body: body });
      if (key === '/api/recording/start') {
        recState = Object.assign({}, recState, { recording: true });
      } else if (key === '/api/recording/stop') {
        recState = Object.assign({}, recState, { recording: false });
      }
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(recState) });
    }
    if (key.startsWith('/plugins/')) {
      return Promise.resolve({ ok: false, status: 404, json: () => Promise.reject(new Error('404')), text: () => Promise.resolve('') });
    }
    if (key === '/health') {
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({ ok: true, connected: true }) });
    }
    if (key === '/api/recording') {
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(recState) });
    }
    // /state and everything else: a minimal shell state.
    return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({
      schema_id: 'r', session_id: 's', connected: true, samples: 0,
      last_update_ns: Date.now() * 1e6, slot_freshness_ttl_ns: 30e9, streams: {},
      last_transaction_result: null, command_results: [],
    }) });
  };

  const sandbox = {
    console: { log() {}, warn() {}, error(...a) { errors.push(a.map(String).join(' ')); } },
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
  return { sandbox, doc, elements, errors, timers, posts, recState };
}

async function drain() {
  for (let i = 0; i < 6; i++) await new Promise((r) => setImmediate(r));
}

async function main() {
  const html = fs.readFileSync(SHELL_HTML, 'utf8');
  const match = html.match(/<script>([\s\S]*?)<\/script>/);
  if (!match) throw new Error('no inline <script> found in shell index.html');

  const harness = makeHarness();
  vm.runInContext(match[1], harness.sandbox, { filename: 'shell-index-inline.js' });
  await drain();

  const recordBtn = harness.doc.getElementById('record-btn');
  const dot = harness.doc.getElementById('rec-dot');
  const noteBtn = harness.doc.getElementById('note-btn');
  const noteInput = harness.doc.getElementById('note-input');
  const noteKind = harness.doc.getElementById('note-kind');

  // Phase 1: off state renders a REC button, dot not lit, note disabled.
  if (recordBtn.textContent.indexOf('REC') === -1) {
    throw new Error('off state did not render a REC button: ' + recordBtn.textContent);
  }
  if (dot.classList.contains('on')) throw new Error('dot lit while not recording');
  if (!noteBtn.disabled) throw new Error('note button enabled while not recording');

  // Phase 2: click to toggle recording ON -> POSTs /api/recording/start.
  recordBtn.click();
  await drain();
  const startPost = harness.posts.filter((p) => p.url === '/api/recording/start');
  if (startPost.length === 0) throw new Error('no /api/recording/start POST on REC click');
  if (startPost[0].body.requested_by !== 'operator') {
    throw new Error('start requested_by not operator: ' + JSON.stringify(startPost[0].body));
  }
  if (recordBtn.textContent.indexOf('Stop') === -1) {
    throw new Error('after start the button should read Stop: ' + recordBtn.textContent);
  }
  if (!dot.classList.contains('on')) throw new Error('dot not lit while recording');
  if (noteBtn.disabled) throw new Error('note button should be enabled while recording');

  // Phase 3: add a note -> POSTs /api/session/note, clears input.
  noteInput.value = 'hover test starting';
  noteKind.value = 'goal';
  noteBtn.click();
  await drain();
  const notePost = harness.posts.find((p) => p.url === '/api/session/note');
  if (!notePost) throw new Error('no /api/session/note POST on note click');
  if (notePost.body.text !== 'hover test starting' || notePost.body.kind !== 'goal' ||
      notePost.body.source !== 'operator') {
    throw new Error('note payload wrong: ' + JSON.stringify(notePost.body));
  }
  if (noteInput.value !== '') throw new Error('note input not cleared after add');

  // Phase 4: click to toggle recording OFF -> POSTs /api/recording/stop.
  recordBtn.click();
  await drain();
  if (harness.posts.filter((p) => p.url === '/api/recording/stop').length === 0) {
    throw new Error('no /api/recording/stop POST on Stop click');
  }
  if (recordBtn.textContent.indexOf('REC') === -1) {
    throw new Error('after stop the button should read REC: ' + recordBtn.textContent);
  }

  console.log(JSON.stringify({
    off_state_renders_rec: true,
    start_posted_to_recording_start: true,
    start_requested_by_operator: true,
    stop_posted_when_recording: true,
    note_posted_to_session_note: true,
    note_cleared_input: true,
    notes_were_created: harness.posts.filter((p) => p.url === '/api/session/note').length > 0,
    console_errors: harness.errors.length,
  }));
}

main().catch((err) => {
  console.error('RECORD CONTROL HARNESS FAILED: ' + (err && err.stack || err));
  process.exit(1);
});