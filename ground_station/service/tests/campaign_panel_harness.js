'use strict';
/**
 * Offline verification harness for campaign-panel.js (checks a-l: Go/checklist/controls; m-r: WP-23 banner,
 * preflight table, pickers, in-panel errors, no window.confirm on workflow-B panels).
 */
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const PLUGINS = path.join(__dirname, '..', '..', '..', 'docs', 'dashboard-platform', 'shell', 'plugins');
const PANEL = path.join(PLUGINS, 'campaign-panel.js');

// ── Fake DOM ───────────────────────────────────────────────────────────────
class Element {
  constructor(id, tag) {
    this.id = id;
    this.tagName = (tag || 'div').toUpperCase();
    this._html = '';
    this.textContent = '';
    this.value = '';
    this.checked = false;
    this.disabled = false;
    this.className = '';
    this.handlers = {};
    this.style = {};
    this.dataset = {};
    this.doc = null;
  }
  addEventListener(ev, fn) { (this.handlers[ev] = this.handlers[ev] || []).push(fn); }
  dispatch(ev) {
    (this.handlers[ev] || []).forEach((fn) => fn.call(this, { type: ev, target: this }));
  }
  get innerHTML() { return this._html; }
  set innerHTML(html) { this._html = html; if (this.doc) this.doc.scan(html); }
  querySelector(sel) {
    if (sel.startsWith('#')) return this.doc.getElementById(sel.slice(1));
    return null;
  }
}

class FakeDocument {
  constructor() {
    this.elements = {};
  }
  scan(html) {
    const tags = html.match(/<[a-zA-Z][^>]*>/g) || [];
    for (const tag of tags) {
      const idm = tag.match(/\bid="([^"]+)"/);
      if (!idm) continue;
      let el = this.elements[idm[1]];
      if (!el) { el = new Element(idm[1], tag.slice(1)); this.elements[idm[1]] = el; el.doc = this; }
      const vm = tag.match(/\bvalue="([^"]*)"/);
      if (vm && el._valueInit !== true) { el.value = vm[1]; el._valueInit = true; }
      if (/\bchecked\b/.test(tag)) el.checked = true;
    }
  }
  getElementById(id) { return this.elements[id] || null; }
}

function makeApi() {
  const api = {
    panelName: '', renderFn: null, gatedCount: 0, submitCount: 0,
    registerPanel(name, renderFn) { api.panelName = name; api.renderFn = renderFn; },
    gatedCommand() { api.gatedCount++; return Promise.resolve({ ok: true }); },
    submitCommand() { api.submitCount++; return Promise.resolve({ ok: true }); }
  };
  return api;
}

function pass(letter, msg) { console.log(letter + ' ' + msg); }
function check(cond, msg) { if (!cond) throw new Error(msg); }
const tick = () => new Promise((r) => setTimeout(r, 20));
const reply = (ok, status, body) => Promise.resolve({ ok, status, json: () => Promise.resolve(body) });

const LIST = {
  saved: [{ name: 'hover_ladder', path: 'ground_station/service/campaigns/hover_ladder.yaml', mode: 'fly' }],
  launch: [{ name: 'hover_ladder_20261003-1821', path: 'logs/campaigns/launch/hover_ladder_20261003-1821.yaml', mode: 'fly' }],
  packs: ['P5300-1', 'P4000-1']
};
const PREFLIGHT = {
  ok: false,
  checks: [
    { name: 'service', value: 'pid 1', pass: true, fix: '' },
    { name: 'arm_state', value: 'armed from DroneStatus.ARM_Status 1 (slot 0, 0.1 s)', pass: false, fix: 'disarm by RC' },
    { name: 'rc_link', value: 'sbus_lost is not on the core stream', pass: null, fix: 'checklist item rc_ready' },
    { name: 'position', value: '<b>x</b>', pass: true, fix: 'never shown' }
  ]
};

async function runHarness() {
  const code = fs.readFileSync(PANEL, 'utf8');
  const doc = new FakeDocument();
  let fetchCalls = [];
  const ctx = {
    document: doc,
    window: { __PLUGIN_INIT__: null, __PLUGIN_DESTROY__: null },
    console,
    setTimeout,
    clearTimeout,
    setInterval: (fn) => { ctx._timerFn = fn; return 999; },
    clearInterval: (id) => { if (id === 999) ctx._timerFn = null; },
    fetch: (url, opts) => {
      fetchCalls.push({ url, opts });
      if (url === '/api/campaign/state') {
        if (ctx._stateFail) return reply(false, 500, { error: 'Poll fail' });
        return reply(true, 200, ctx._fakeState || { status: 'idle', banner: 'idle' });
      }
      if (url === '/api/campaign/list') return ctx._listFail ? reply(false, 503, { error: 'list down' }) : reply(true, 200, LIST);
      if (url.startsWith('/api/campaign/preflight?')) {
        return ctx._preflightFail ? reply(false, 500, { error: 'preflight failed: boom' }) : reply(true, 200, PREFLIGHT);
      }
      if (url === '/api/agent/control') return reply(true, 200, { allow_agent_arm: false });
      if (url === '/api/campaign/go') {
        if (ctx._goFail === 409) return reply(false, 409, { error: 'Conflict 409' });
        if (ctx._goFail === 503) return reply(false, 503, { error: 'Deps 503' });
        return reply(true, 200, { status: 'running' });
      }
      if (['pause', 'land', 'abort'].some((c) => url.endsWith(c))) {
        return ctx._cmdFail ? reply(false, 409, { error: 'no active run' }) : reply(true, 200, { ok: true });
      }
      return reply(false, 404, { error: 'not found' });
    }
  };
  vm.createContext(ctx);
  vm.runInContext(code, ctx);

  const api = makeApi();
  ctx.window.__PLUGIN_INIT__(api);

  // a. registers as `Campaign`
  check(api.panelName === 'Campaign', 'Wrong panel name: ' + api.panelName);
  pass('a', 'registers as Campaign');

  const container = new Element('container', 'div');
  container.doc = doc;
  api.renderFn(container);
  await tick();
  const el = (id) => doc.getElementById(id);
  const qPath = el('cp-path'), qPack = el('cp-pack-id'), qGo = el('cp-go-btn');
  const checkIds = ['pack_swapped', 'drone_on_pad', 'powered_in_place', 'rc_ready', 'phone_recording', 'operator_present'];
  const tickAll = (v) => checkIds.forEach((id) => { el('cp-chk-' + id).checked = v; el('cp-chk-' + id).dispatch('change'); });

  // b. Go disabled with an empty pack ID, and with each one of the 6 boxes unticked in turn
  qPath.value = 'path/to/camp'; qPath.dispatch('input');
  qPack.value = ''; qPack.dispatch('input');
  check(qGo.disabled, 'Go should be disabled on empty pack');
  qPack.value = 'p123'; qPack.dispatch('input');
  tickAll(true);
  check(!qGo.disabled, 'Go should be enabled');
  checkIds.forEach((id) => {
    el('cp-chk-' + id).checked = false; el('cp-chk-' + id).dispatch('change');
    check(qGo.disabled, 'Go enabled with ' + id + ' unticked');
    el('cp-chk-' + id).checked = true; el('cp-chk-' + id).dispatch('change');
  });
  pass('b', 'Go disabled properly');

  // c. all set -> exactly one POST /api/campaign/go with the exact body
  fetchCalls = [];
  qGo.dispatch('click');
  await tick();
  const goes = fetchCalls.filter((f) => f.url === '/api/campaign/go');
  check(goes.length === 1 && goes[0].opts.method === 'POST', 'Bad go fetch');
  const b = JSON.parse(goes[0].opts.body);
  const expectedChecklist = { pack_swapped: true, drone_on_pad: true, powered_in_place: true, rc_ready: true, phone_recording: true, operator_present: true };
  check(b.campaign_path === 'path/to/camp' && b.pack_id === 'p123' && b.source === 'operator' &&
        JSON.stringify(b.checklist) === JSON.stringify(expectedChecklist), 'Bad go body: ' + goes[0].opts.body);
  pass('c', 'Go exact POST');

  // l. ticks reset after a successful go
  check(checkIds.every((id) => !el('cp-chk-' + id).checked), 'ticks not reset');
  pass('l', 'ticks reset');

  // e. each of Pause/Land/Abort -> one POST to its own route with source operator, single click (never delayed)
  fetchCalls = [];
  el('cp-pause-btn').dispatch('click');
  el('cp-land-btn').dispatch('click');
  el('cp-abort-btn').dispatch('click');
  await tick();
  const posts = fetchCalls.filter((f) => f.opts && f.opts.method === 'POST');
  check(posts.length === 3 && ['pause', 'land', 'abort'].every((c) =>
    posts.filter((f) => f.url === '/api/campaign/' + c && JSON.parse(f.opts.body).source === 'operator').length === 1),
    'Commands missing');
  pass('e', 'Commands sent');

  // d. Pause/Land/Abort present and visible in every runner state
  for (const status of ['idle', 'running', 'waiting_for_go', 'operator_needed', 'error']) {
    ctx._fakeState = { status };
    ctx._timerFn();
    await tick();
    check(el('cp-pause-btn') && el('cp-land-btn') && el('cp-abort-btn') && el('cp-pause-btn').style.display !== 'none',
      'Buttons not present in ' + status);
  }
  pass('d', 'Buttons present');

  // f. waiting_for_go shows the pack and prefills the pack ID
  ctx._fakeState = { status: 'idle' }; ctx._timerFn(); await tick();
  ctx._fakeState = { status: 'waiting_for_go', waiting_pack: 'wp789' }; ctx._timerFn(); await tick();
  check(qPack.value === 'wp789' && el('cp-wait-msg').textContent.includes('wp789'), 'Waiting prefill failed');
  pass('f', 'Waiting prefilled');

  // g. flights render one row each, HTML escaped
  ctx._fakeState = {
    status: 'running',
    flights: [
      { flight_id: 'f1', pack_id: 'p1', experiment: 'e1', j: 1, decision: 'go', abort_level: 'none', abort_reason: '<b>x</b>', hover_only: false },
      { flight_id: 'f2', hover_only: true }
    ]
  };
  ctx._timerFn(); await tick();
  const fb = el('cp-flights-body').innerHTML;
  check(fb.includes('f1') && fb.includes('f2') && fb.includes('&lt;b&gt;x&lt;/b&gt;') && !fb.includes('<b>x</b>'),
    'Flights missing or not escaped properly');
  pass('g', 'Flights rendered');

  // h. 409 and 503 error text shown; a poll never clears an action error; poll errors are separate
  qPath.value = 'a'; qPack.value = 'b'; tickAll(true); qPath.dispatch('input');
  ctx._goFail = 409; qGo.dispatch('click'); await tick();
  check(el('cp-error').style.display === 'block' && el('cp-error').textContent === 'Go: Conflict 409', 'Error not shown 409');
  ctx._goFail = 503; qGo.dispatch('click'); await tick();
  check(el('cp-error').style.display === 'block' && el('cp-error').textContent === 'Go: Deps 503', 'Error not shown 503');
  ctx._goFail = 0;
  ctx._fakeState = { status: 'idle' }; ctx._timerFn(); await tick();
  check(el('cp-error').textContent === 'Go: Deps 503', 'Action error cleared by poll');
  ctx._stateFail = true; ctx._timerFn(); await tick();
  check(el('cp-poll-error').style.display === 'block' && el('cp-poll-error').textContent === 'Poll fail', 'Poll error not shown');
  ctx._stateFail = false; ctx._timerFn(); await tick();
  check(el('cp-poll-error').style.display === 'none', 'Poll error not cleared');
  pass('h', 'Errors shown and separated');

  // i. Go is the arm consent: no allow-arm toggle, a consent note, no /api/agent/control call
  check(!el('cp-allow-arm'), 'allow-arm toggle should be gone');
  check(el('cp-consent-note'), 'consent note missing');
  check(!fetchCalls.some((f) => f.url === '/api/agent/control'), 'panel must not call /api/agent/control');
  pass('i', 'Go is the arm consent');

  // m. the banner shows the service's one-line banner, coloured by status
  const banner = el('cp-banner');
  const cases = [
    [{ status: 'idle', banner: 'idle' }, 'idle'],
    [{ status: 'waiting_for_go', waiting_pack: 'P4000-1', banner: 'waiting for go: pack P4000-1, flight 1/3' }, 'waiting for go: pack P4000-1, flight 1/3'],
    [{ status: 'running', banner: 'flying flight 2/3: hover_z070' }, 'flying flight 2/3: hover_z070'],
    [{ status: 'operator_needed', banner: 'paused (operator_needed): battery: real_voltage not streaming' }, 'paused (operator_needed): battery: real_voltage not streaming'],
    [{ status: 'complete', banner: 'done: logs/campaigns/hover_ladder_x' }, 'done: logs/campaigns/hover_ladder_x']
  ];
  const colors = new Set();
  for (const [st, text] of cases) {
    ctx._fakeState = st; ctx._timerFn(); await tick();
    check(banner.textContent === text && banner.dataset.status === st.status, 'banner ' + banner.textContent);
    colors.add(banner.style.background);
  }
  check(colors.size === 5, 'each status needs its own banner colour');
  pass('m', 'Banner follows the runner');

  // o. pickers: launch copies first, then templates; picking fills the path / pack inputs
  const campOpts = el('cp-campaign-select').innerHTML;
  check(campOpts.indexOf('launch: hover_ladder_20261003-1821') < campOpts.indexOf('template: hover_ladder (fly)') &&
        campOpts.indexOf('launch:') > 0, 'campaign picker order: ' + campOpts);
  check(el('cp-pack-select').innerHTML.includes('value="P4000-1"'), 'pack picker');
  el('cp-campaign-select').value = LIST.launch[0].path; el('cp-campaign-select').dispatch('change');
  el('cp-pack-select').value = 'P4000-1'; el('cp-pack-select').dispatch('change');
  check(qPath.value === LIST.launch[0].path && qPack.value === 'P4000-1', 'picker did not fill the inputs');
  tickAll(true);
  check(!qGo.disabled, 'Go should be enabled after picking');
  pass('o', 'Campaign and pack pickers');

  // n. preflight: one GET with the encoded campaign and pack; red rows show their fix, green rows never do
  fetchCalls = [];
  el('cp-preflight-btn').dispatch('click');
  await tick();
  const pf = fetchCalls.filter((f) => f.url.startsWith('/api/campaign/preflight?'));
  check(pf.length === 1 && pf[0].url === '/api/campaign/preflight?campaign=' + encodeURIComponent(LIST.launch[0].path) + '&pack=P4000-1',
    'preflight url ' + (pf[0] && pf[0].url));
  const rows = el('cp-preflight-body').innerHTML;
  check((rows.match(/<tr /g) || []).length === 4, 'one row per check');
  check(rows.includes('cp-pf-fail') && rows.includes('disarm by RC') && rows.includes('cp-pf-unknown') &&
        rows.includes('checklist item rc_ready'), 'red / amber rows need their fix');
  check(!rows.includes('never shown') && rows.includes('&lt;b&gt;x&lt;/b&gt;'), 'green rows hide fix; values escaped');
  check(el('cp-preflight-summary').textContent.includes('1 red row'), 'summary: ' + el('cp-preflight-summary').textContent);
  pass('n', 'Preflight table');

  // p. every failed action shows its error in the panel
  ctx._preflightFail = true; el('cp-preflight-btn').dispatch('click'); await tick();
  check(el('cp-error').textContent === 'preflight: preflight failed: boom', 'preflight error: ' + el('cp-error').textContent);
  ctx._cmdFail = true; el('cp-land-btn').dispatch('click'); await tick();
  check(el('cp-error').textContent === 'land: no active run', 'command error: ' + el('cp-error').textContent);
  ctx._listFail = true; el('cp-list-btn').dispatch('click'); await tick();
  check(el('cp-error').textContent === 'campaign list: list down', 'list error: ' + el('cp-error').textContent);
  pass('p', 'Failed actions show their error');

  // r. no window.confirm / alert on the workflow-B panels (a browser can block them: the action silently did nothing)
  for (const f of ['campaign-panel.js', 'approval-queue.js', 'command-panel.js', 'path-panel.js']) {
    const src = fs.readFileSync(path.join(PLUGINS, f), 'utf8');
    check(!/\b(window\.)?(confirm|alert)\(/.test(src), f + ' still calls confirm/alert');
  }
  pass('r', 'No browser dialogs on workflow-B panels');

  // k. zero submitCommand/gatedCommand calls over the whole run
  check(api.submitCount === 0 && api.gatedCount === 0, 'Called old API');
  pass('k', 'Zero old API calls');

  // j. after teardown, advancing timers causes no further fetch
  const captured = ctx._timerFn;
  ctx.window.__PLUGIN_DESTROY__();
  fetchCalls = [];
  check(ctx._timerFn === null, 'Timer not cleared');
  try { captured(); } catch (e) { /* destroyed */ }
  await tick();
  check(fetchCalls.length === 0, 'Fetched after teardown');
  pass('j', 'No timer after teardown');

  console.log('ALL CHECKS PASSED');
}

runHarness().catch((e) => { console.error('FAIL: ' + e.message); process.exit(1); });
