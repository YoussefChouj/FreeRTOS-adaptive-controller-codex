'use strict';
/**
 * Offline verification harness for replay-panel.js (Session Replay),
 * task 20260921-145106. Covers:
 *   1. Honest empty states for sessions/detail/records.
 *   2. Session list rendering and select -> detail fetch/render.
 *   3. Detail honesty: missing ended/duration/record-count read dash.
 *   4. Export posts the captured {output_path} body.
 *   5. Play posts /replay/<id>/play; error body surfaces honestly.
 *   6. Paged records fixture: rows + truncated note (never a fake total).
 *   7. Scrub/drag offset navigation (header + playhead), speed switch,
 *      per-slot filter.
 *
 * Fetch is a recording router; the canvas ctx records every fill. The
 * sandbox has no require or net modules, so a real network call is
 * structurally impossible.
 *
 * Run:  node ground_station/service/tests/replay_panel_harness.js
 * Exit code 0 iff every check passes.
 */
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const assert = require('assert');

const PANEL = path.join(__dirname, '..', '..', '..',
  'docs', 'dashboard-platform', 'shell', 'plugins', 'replay-panel.js');

// ── Recording 2D canvas context ──────────────────────────────────────────
function makeCtx() {
  return {
    fillStyle: '',
    rects: [],
    fillRect(x, y, w, h) { this.rects.push({ x, y, w, h, fill: this.fillStyle }); },
  };
}

// ── Fake DOM ──────────────────────────────────────────────────────────────
function tagMatches(tag, tagName, classes) {
  if (tagName && tag.slice(1, 2 + tagName.length).toLowerCase() !== tagName.toLowerCase()) {
    // cheap tag-name check via parsed name instead (unused; see buildNodes)
  }
  return classes.every((c) => new RegExp('class="[^"]*\\b' + c + '\\b[^"]*"').test(tag));
}

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
    this.dataset = {};
    this.doc = null;
    this._cache = {};
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
      toggle(c, f) {
        const has = this.contains(c);
        if (f === undefined) f = !has;
        if (f && !has) this.add(c);
        if (!f && has) this.remove(c);
        return f;
      },
    };
  }
  getAttribute(name) {
    if (this.dataset[name] !== undefined) return this.dataset[name];
    const stripped = name.replace(/^data-/, '');
    if (stripped !== name && this.dataset[stripped] !== undefined) {
      return this.dataset[stripped];
    }
    return null;
  }
  setAttribute(name, v) { this.dataset[name] = String(v); }
  getContext() { return this.doc ? this.doc.ctx : null; }
  get innerHTML() { return this._html; }
  set innerHTML(html) {
    this._html = html;
    this._cache = {};
    if (this.doc) this.doc.scan(html);
  }
  // Selectors supported: tag, .cls, tag.cls(.cls) — no descendants.
  querySelectorAll(sel) {
    if (this._cache[sel]) return this._cache[sel];
    const tm = sel.match(/^[a-zA-Z][\w-]*/);
    const wantTag = tm ? tm[0] : null;
    const classes = (sel.match(/\.[\w-]+/g) || []).map((c) => c.slice(1));
    const re = /<([a-zA-Z][\w-]*)((?:"[^"]*"|'[^']*'|[^'">])*)>/g;
    const out = [];
    let m;
    while ((m = re.exec(this._html)) !== null) {
      if (wantTag && m[1] !== wantTag) continue;
      if (classes.length && !tagMatches(m[0], null, classes)) continue;
      let el = this._cache[m[0]];
      if (!el) {
        el = new Element(null, m[1]);
        el.doc = this.doc;
        const cm = m[2].match(/\bclass="([^"]*)"/);
        if (cm) el.className = cm[1];
        const dm = m[2].match(/\bdata-([\w-]+)="([^"]*)"/);
        if (dm) el.dataset[dm[1]] = dm[2];
        const vm2 = m[2].match(/\bvalue="([^"]*)"/);
        if (vm2) el.value = vm2[1];
        this._cache[m[0]] = el;
      }
      // Live class filter (e.g. a class toggled after markup).
      if (classes.every((c) => el.classList.contains(c))) out.push(el);
    }
    this._cache[sel] = out;
    return out;
  }
  querySelector(sel) { return this.querySelectorAll(sel)[0] || null; }
  appendChild(node) {
    const tag = (node.tagName || 'div').toLowerCase();
    this._html += '<' + tag + (node.className ? ' class="' + node.className + '"' : '') +
      '>' + (node.textContent || '') + '</' + tag + '>';
    this._cache = {};
  }
}

class FakeDocument {
  constructor(ctx) { this.elements = {}; this.ctx = ctx; this.root = null; }
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
  querySelectorAll(sel) { return this.root.querySelectorAll(sel); }
  createElement(tag) { return new Element(null, tag); }
}

// ── Manual timers ─────────────────────────────────────────────────────────
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

// ── Panel loading ─────────────────────────────────────────────────────────
const T_START = 1700000000000000000n;

function rec(slot, ms, values) {
  return { slot, timestamp_ns: Number(T_START) + ms * 1000000, values };
}

const PAGE_RECORDS = [
  rec('1', 0, { 'ekf.pos_x': 1, 'ekf.pos_y': 2 }),
  rec('1', 1000, { 'ekf.pos_x': 1.5 }),
  rec('2', 2000, { 'c.altitude': 0 }),
  rec('2', 3000, { 'c.altitude': 10 }),
  rec('1', 4000, { 'ekf.pos_x': 2 }),
];

function loadPanel() {
  const ctx = makeCtx();
  const doc = new FakeDocument(ctx);
  const container = new Element('container', 'div');
  container.doc = doc;
  doc.root = container;
  const api = {
    registerPanel(name, renderFn) { this.renderFn = renderFn; },
  };
  const records = [];
  const S = {
    list: [],
    details: {},
    playBody: { replayed: 5 },
  };
  function resp(status, body) {
    return {
      ok: status >= 200 && status < 300, status,
      json: () => Promise.resolve(body),
    };
  }
  // Body evaluated when json() is called, not when fetch() runs: tests
  // populate S after loadPanel but before the microtask flush.
  function lazy(status, getBody) {
    return {
      ok: status >= 200 && status < 300, status,
      json: () => Promise.resolve(getBody()),
    };
  }
  const sandbox = {
    document: doc,
    console: { log() {}, warn() {}, error() {} },
    Date, Math, Number, String, Boolean, Array, Object, JSON,
    isNaN, isFinite, parseInt, parseFloat, encodeURIComponent,
    setTimeout: tSet, clearTimeout: tClear,
    setInterval: tInterval, clearInterval: tClear,
    Promise, Set,
    fetch(url, opts) {
      const qi = url.indexOf('?');
      const p = qi === -1 ? url : url.slice(0, qi);
      const query = qi === -1 ? '' : url.slice(qi + 1);
      const method = (opts && opts.method) || 'GET';
      let body = null;
      if (opts && opts.body) body = JSON.parse(opts.body);
      records.push({ method, path: p, query, body });
      if (p === '/sessions' && method === 'GET') {
        return Promise.resolve(lazy(200, () => S.list));
      }
      let mm = p.match(/^\/sessions\/(\d+)$/);
      if (mm && method === 'GET') {
        return Promise.resolve(lazy(200, () => {
          const d = S.details[mm[1]];
          return d || { error: 'no session' };
        }));
      }
      mm = p.match(/^\/sessions\/(\d+)\/records$/);
      if (mm && method === 'GET') {
        return Promise.resolve(lazy(200, () => ({
          records: PAGE_RECORDS, count: 12345, offset: 0,
          limit: 2000, truncated: true,
        })));
      }
      mm = p.match(/^\/sessions\/(\d+)\/export$/);
      if (mm && method === 'POST') {
        return Promise.resolve(lazy(200, () => ({ exported: 'session_' + mm[1] + '_export.csv' })));
      }
      mm = p.match(/^\/replay\/(\d+)\/play$/);
      if (mm && method === 'POST') {
        return Promise.resolve(lazy(200, () => S.playBody));
      }
      return Promise.resolve(resp(404, { error: 'unexpected ' + method + ' ' + p }));
    },
  };
  sandbox.handlers = {};
  sandbox.addEventListener = function (ev, fn) {
    (this.handlers[ev] = this.handlers[ev] || []).push(fn);
  };
  sandbox.window = sandbox;
  sandbox.window.removeEventListener = function () {};
  sandbox.__registerPlugin__ = function (name, init, destroy) {
    sandbox.pluginInit = init;
    sandbox.pluginDestroy = destroy;
  };
  vm.createContext(sandbox);
  vm.runInContext(fs.readFileSync(PANEL, 'utf8'), sandbox, { filename: PANEL });
  sandbox.pluginInit(api);
  api.renderFn(container);
  return {
    doc, ctx, S,
    records: () => records.slice(),
    fire(el, ev) {
      (el.handlers[ev] || []).forEach((fn) => fn.call(el, { target: el }));
    },
    destroy() { sandbox.pluginDestroy(); },
  };
}

function posts(env, part) {
  return env.records().filter((r) => r.method === 'POST' && r.path.indexOf(part) !== -1);
}

async function selectSession(env, id) {
  const item = env.doc.getElementById('rp-session-list')
    .querySelectorAll('.rp-session-item')
    .filter((el) => el.getAttribute('session-id') === String(id))[0];
  assert.ok(item, 'session row ' + id);
  env.fire(item, 'click');
  await micro();
}

function detailLabel(html, label) {
  const m = html.match(new RegExp(label + '</span><span class="rp-detail-value">([^<]*)</span>'));
  return m ? m[1] : null;
}

async function runChecks() {
  console.log('--- REPLAY PANEL OFFLINE CHECKS ---');

  // 1. Empty states
  {
    console.log('\n[CHECK 1: empty sessions, honest placeholder states]');
    const env = loadPanel();
    await micro();
    assert.ok(env.doc.getElementById('rp-session-list').innerHTML.indexOf('No sessions recorded') !== -1);
    assert.ok(env.doc.getElementById('rp-session-detail').innerHTML.indexOf('Select a session to view details') !== -1);
    assert.ok(env.doc.getElementById('rp-record-list').innerHTML.indexOf('Load records to begin replay') !== -1);
    assert.strictEqual(env.doc.getElementById('rp-playback-info').textContent, 'No playback data');
    console.log('  PASS: No sessions recorded; select/load placeholders; No playback data');
    env.destroy();
  }

  // 2. List + select detail
  {
    console.log('\n[CHECK 2: session list and selected detail render]');
    const env = loadPanel();
    env.S.list = [
      { id: 7, schema_id: 'sch-1', started_ns: Number(T_START), source: 'wifi' },
      { id: 8, schema_id: null, started_ns: null, source: null },
    ];
    env.S.details[7] = {
      id: 7, schema_id: 'sch-1', started_ns: Number(T_START),
      ended_ns: Number(T_START) + 5000000000, record_count: 12345,
    };
    await micro();
    assert.ok(env.doc.getElementById('rp-session-list').innerHTML.indexOf('Session #7') !== -1);
    assert.ok(env.doc.getElementById('rp-session-list').innerHTML.indexOf('Source: unknown') !== -1);
    await selectSession(env, 7);
    const html = env.doc.getElementById('rp-session-detail').innerHTML;
    assert.strictEqual(detailLabel(html, 'Session ID'), '7');
    assert.strictEqual(detailLabel(html, 'Record Count'), '12345');
    assert.ok(html.indexOf('id="rp-export-btn"') !== -1);
    assert.ok(html.indexOf('data-testid="replay-play"') !== -1);
    console.log('  PASS: list renders; selecting #7 shows record count 12,345 and both buttons');
    env.destroy();
  }

  // 3. Missing fields honest
  {
    console.log('\n[CHECK 3: ended/count missing render dash]');
    const env = loadPanel();
    env.S.list = [{ id: 8, schema_id: null, started_ns: Number(T_START), source: 'wifi' }];
    env.S.details[8] = { id: 8, started_ns: Number(T_START), ended_ns: null, record_count: null };
    await micro();
    await selectSession(env, 8);
    const html = env.doc.getElementById('rp-session-detail').innerHTML;
    assert.strictEqual(detailLabel(html, 'Ended'), '—');
    assert.strictEqual(detailLabel(html, 'Duration'), '—');
    assert.strictEqual(detailLabel(html, 'Record Count'), '—');
    assert.strictEqual(detailLabel(html, 'Schema ID'), '—');
    console.log('  PASS: Ended, Duration, Record Count, Schema all dash — no fake 0');
    env.destroy();
  }

  // 4. Export body captured
  {
    console.log('\n[CHECK 4: export posts captured request body]');
    const env = loadPanel();
    env.S.list = [{ id: 7, started_ns: Number(T_START) }];
    env.S.details[7] = { id: 7, started_ns: Number(T_START) };
    await micro();
    await selectSession(env, 7);
    env.fire(env.doc.getElementById('rp-export-btn'), 'click');
    await micro();
    const p = posts(env, '/export');
    assert.strictEqual(p.length, 1);
    assert.strictEqual(p[0].path, '/sessions/7/export');
    assert.strictEqual(p[0].body.output_path, 'session_7_export.csv');
    assert.strictEqual(env.doc.getElementById('rp-export-status').textContent,
      'Exported: session_7_export.csv');
    console.log('  PASS: POST /sessions/7/export body ' + JSON.stringify(p[0].body));
    env.destroy();
  }

  // 5. Play body captured + error honesty
  {
    console.log('\n[CHECK 5: play posts captured; error response surfaced]');
    const env = loadPanel();
    env.S.list = [{ id: 7, started_ns: Number(T_START) }];
    env.S.details[7] = { id: 7, started_ns: Number(T_START) };
    await micro();
    await selectSession(env, 7);
    env.fire(env.doc.getElementById('rp-bus-play-btn'), 'click');
    await micro();
    const p = posts(env, '/play');
    assert.strictEqual(p.length, 1);
    assert.strictEqual(p[0].path, '/replay/7/play');
    assert.strictEqual(p[0].body, null);
    assert.strictEqual(env.doc.getElementById('rp-export-status').textContent,
      'Replayed 5 records to live view');
    env.S.playBody = { error: 'playback busy' };
    env.fire(env.doc.getElementById('rp-bus-play-btn'), 'click');
    await micro();
    assert.strictEqual(env.doc.getElementById('rp-export-status').textContent,
      'Replay failed: playback busy');
    console.log('  PASS: POST /replay/7/play; success then honest error status');
    env.destroy();
  }

  // 6. Paged records + truncation note
  {
    console.log('\n[CHECK 6: paged records render with truncation note]');
    const env = loadPanel();
    env.S.list = [{ id: 7, started_ns: Number(T_START) }];
    env.S.details[7] = { id: 7, started_ns: Number(T_START) };
    await micro();
    await selectSession(env, 7);
    env.fire(env.doc.getElementById('rp-load-records-btn'), 'click');
    await micro();
    const get = env.records().filter((r) => r.path.indexOf('/records') !== -1 &&
      r.method === 'GET')[0];
    assert.ok(get.query.indexOf('limit=2000') !== -1);
    const html = env.doc.getElementById('rp-record-list').innerHTML;
    assert.ok(html.indexOf('Record #0 / 4') !== -1);
    assert.ok(html.indexOf('ekf.pos_x') !== -1 && html.indexOf('1.0000') !== -1);
    assert.ok(html.indexOf('Showing the first 5 records of this session') !== -1,
      'truncated page must say so, never fake a full load');
    assert.ok(env.doc.getElementById('rp-stream-stats').innerHTML.indexOf('rp-stats-table') !== -1);
    console.log('  PASS: page 5/12,345 requested with limit=2000; detail + truncation note render');
    env.destroy();
  }

  // 7. Scrub/drag, speed, slot filter
  {
    console.log('\n[CHECK 7: scrub offset navigation, speed switch, slot filter]');
    const env = loadPanel();
    env.S.list = [{ id: 7, started_ns: Number(T_START) }];
    env.S.details[7] = { id: 7, started_ns: Number(T_START) };
    await micro();
    await selectSession(env, 7);
    env.fire(env.doc.getElementById('rp-load-records-btn'), 'click');
    await micro();

    // Drag scrubber to 50% -> offset 2 of 4.
    const scrubber = env.doc.getElementById('rp-scrubber');
    scrubber.value = '50';
    env.fire(scrubber, 'input');
    let html = env.doc.getElementById('rp-record-list').innerHTML;
    assert.ok(html.indexOf('Record #2 / 4') !== -1);
    assert.strictEqual(env.doc.getElementById('rp-playback-info').textContent.indexOf(
      'Index: 2 / 4'), 0);
    let head = env.ctx.rects.filter((r) => r.fill === '#e94560').pop();
    assert.strictEqual(head.x, 299, 'playhead 2/4*600-1 = 299, got ' + head.x);
    assert.strictEqual(head.w, 2);

    // Speed 2x.
    const speed2 = env.doc.querySelectorAll('.rp-speed-btn')
      .filter((b) => b.getAttribute('speed') === '2')[0];
    env.fire(speed2, 'click');
    assert.ok(speed2.classList.contains('rp-active'));

    // Filter slot 2 -> index range resets.
    const filterSel = env.doc.getElementById('rp-filter-select');
    filterSel.value = '2';
    env.fire(filterSel, 'change');
    html = env.doc.getElementById('rp-record-list').innerHTML;
    assert.ok(html.indexOf('Record #0 / 1') !== -1);
    assert.ok(html.indexOf('c.altitude') !== -1);
    assert.strictEqual(html.indexOf('ekf.'), -1);
    console.log('  PASS: scrub offset 2/4 with playhead 299; speed 2x active; slot 2 filter resets index');
    env.destroy();
  }

  console.log('\nALL CHECKS PASSED SUCCESSFULLY.');
}

(async function () {
  try { await runChecks(); } catch (e) { console.error(e); process.exit(1); }
}());
