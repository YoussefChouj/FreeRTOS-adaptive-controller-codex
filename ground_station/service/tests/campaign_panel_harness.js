'use strict';
/**
 * Offline verification harness for campaign-panel.js
 */
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const PANEL = path.join(__dirname, '..', '..', '..',
  'docs', 'dashboard-platform', 'shell', 'plugins', 'campaign-panel.js');

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
    this.docHandlers = {};
    this.hidden = false;
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
  addEventListener(ev, fn) { (this.docHandlers[ev] = this.docHandlers[ev] || []).push(fn); }
  dispatchDoc(ev) {
    (this.docHandlers[ev] || []).forEach((fn) => fn.call(this, { type: ev }));
  }
}

function makeApi() {
  const api = {
    panelName: '',
    renderFn: null,
    gatedCount: 0,
    submitCount: 0,
    registerPanel(name, renderFn) { api.panelName = name; api.renderFn = renderFn; },
    gatedCommand() { api.gatedCount++; return Promise.resolve({ ok: true }); },
    submitCommand() { api.submitCount++; return Promise.resolve({ ok: true }); }
  };
  return api;
}

const CHECK_PREFIX = 'a b c d e f g h i j k l'.split(' ');
let checkIndex = 0;
function passCheck(letter, msg) {
  console.log(letter + ' ' + msg);
}

function runHarness() {
  const code = fs.readFileSync(PANEL, 'utf8');
  
  const doc = new FakeDocument();
  let fetchCalls = [];
  let confirmResult = false;
  let confirmCalls = 0;
  
  const ctx = {
    document: doc,
    window: {
      __PLUGIN_INIT__: null,
      __PLUGIN_DESTROY__: null,
      confirm: (msg) => { confirmCalls++; return confirmResult; }
    },
    console: console,
    setTimeout: setTimeout,
    clearTimeout: clearTimeout,
    setInterval: (fn, t) => { ctx._timerFn = fn; return 999; },
    clearInterval: (id) => { if (id === 999) ctx._timerFn = null; },
    fetch: (url, opts) => {
      fetchCalls.push({ url, opts });
      
      if (url === '/api/campaign/state') {
        if (ctx._stateFail) {
          return Promise.resolve({ ok: false, status: 500, json: () => Promise.resolve({ error: 'Poll fail' }) });
        }
        return Promise.resolve({
          ok: true, json: () => Promise.resolve(ctx._fakeState || { status: 'idle' })
        });
      }
      if (url === '/api/agent/control') {
        if (opts && opts.method === 'POST') {
          if (ctx._armFail) return Promise.resolve({ ok: false, status: 500, json: () => Promise.resolve({ error: 'Fail' }) });
          return Promise.resolve({ ok: true, json: () => Promise.resolve({}) });
        }
        return Promise.resolve({
          ok: true, json: () => Promise.resolve({ allow_agent_arm: false })
        });
      }
      if (url === '/api/campaign/go') {
        if (ctx._goFail === 409) return Promise.resolve({ ok: false, status: 409, json: () => Promise.resolve({ error: 'Conflict 409' }) });
        if (ctx._goFail === 503) return Promise.resolve({ ok: false, status: 503, json: () => Promise.resolve({ error: 'Deps 503' }) });
        return Promise.resolve({ ok: true, json: () => Promise.resolve({ status: 'running' }) });
      }
      if (['pause', 'land', 'abort'].some(c => url.endsWith(c))) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve({ ok: true }) });
      }
      return Promise.resolve({ ok: false, status: 404 });
    }
  };
  
  vm.createContext(ctx);
  vm.runInContext(code, ctx);
  
  const api = makeApi();
  ctx.window.__PLUGIN_INIT__(api);
  
  // a. registers as `Campaign`
  if (api.panelName === 'Campaign') passCheck('a', 'registers as Campaign');
  else throw new Error('Wrong panel name: ' + api.panelName);
  
  const container = new Element('container', 'div');
  container.doc = doc;
  api.renderFn(container);
  
  function getEl(id) { return doc.getElementById(id); }
  
  const qPath = getEl('cp-path');
  const qPack = getEl('cp-pack-id');
  const qGoBtn = getEl('cp-go-btn');
  
  // b. go disabled with empty pack ID, and with each one of the 6 boxes unticked in turn
  qPath.value = 'path/to/camp';
  qPath.dispatch('input');
  qPack.value = '';
  qPack.dispatch('input');
  if (!qGoBtn.disabled) throw new Error('Go should be disabled on empty pack');
  
  qPack.value = 'p123';
  qPack.dispatch('input');
  const checkIds = ['pack_swapped', 'drone_on_pad', 'powered_in_place', 'rc_ready', 'phone_recording', 'operator_present'];
  
  // check all
  checkIds.forEach(id => {
    getEl('cp-chk-' + id).checked = true;
    getEl('cp-chk-' + id).dispatch('change');
  });
  if (qGoBtn.disabled) throw new Error('Go should be enabled');
  
  let bPass = true;
  checkIds.forEach(id => {
    getEl('cp-chk-' + id).checked = false;
    getEl('cp-chk-' + id).dispatch('change');
    if (!qGoBtn.disabled) bPass = false;
    getEl('cp-chk-' + id).checked = true;
    getEl('cp-chk-' + id).dispatch('change');
  });
  if (bPass) passCheck('b', 'Go disabled properly');
  else throw new Error('Go disabled check failed');
  
  // c. all set -> exactly one POST /api/campaign/go with the exact body
  fetchCalls = [];
  qGoBtn.dispatch('click');
  setTimeout(() => {
    const goes = fetchCalls.filter(f => f.url === '/api/campaign/go');
    if (goes.length === 1 && goes[0].opts.method === 'POST') {
      const b = JSON.parse(goes[0].opts.body);
      const expectedChecklist = { pack_swapped: true, drone_on_pad: true, powered_in_place: true, rc_ready: true, phone_recording: true, operator_present: true };
      if (b.campaign_path === 'path/to/camp' && b.pack_id === 'p123' && b.source === 'operator' && JSON.stringify(b.checklist) === JSON.stringify(expectedChecklist) && fetchCalls.filter(f => f.url === '/api/campaign/go').length === 1) {
        passCheck('c', 'Go exact POST');
      } else throw new Error('Bad go body: ' + goes[0].opts.body);
    } else throw new Error('Bad go fetch');
    
    // l. ticks reset after a successful go
    if (checkIds.every(id => !getEl('cp-chk-' + id).checked)) passCheck('l', 'ticks reset');
    else throw new Error('ticks not reset');
    
    // e. each of Pause/Land/Abort -> one POST to its own route with source operator
    fetchCalls = [];
    getEl('cp-pause-btn').dispatch('click');
    getEl('cp-land-btn').dispatch('click');
    getEl('cp-abort-btn').dispatch('click');
    
    setTimeout(() => {
      const posts = fetchCalls.filter(f => f.opts && f.opts.method === 'POST');
      if (posts.length === 3 &&
          posts.filter(f => f.url === '/api/campaign/pause' && JSON.parse(f.opts.body).source === 'operator').length === 1 &&
          posts.filter(f => f.url === '/api/campaign/land' && JSON.parse(f.opts.body).source === 'operator').length === 1 &&
          posts.filter(f => f.url === '/api/campaign/abort' && JSON.parse(f.opts.body).source === 'operator').length === 1) {
        passCheck('e', 'Commands sent');
      } else throw new Error('Commands missing');
      
      // d. Pause/Land/Abort present in idle, running, waiting_for_go, operator_needed and error states
      const statuses = ['idle', 'running', 'waiting_for_go', 'operator_needed', 'error'];
      let dPass = true;
      let dIndex = 0;
      function nextStatus() {
        if (dIndex >= statuses.length) {
          if (dPass) passCheck('d', 'Buttons present');
          else throw new Error('Buttons not present');
          
          // f. waiting_for_go shows the pack and prefills the pack ID
          ctx._fakeState = { status: 'idle' };
          ctx._timerFn();
          setTimeout(() => {
            ctx._fakeState = { status: 'waiting_for_go', waiting_pack: 'wp789' };
            ctx._timerFn();
            setTimeout(() => {
              if (qPack.value === 'wp789' && getEl('cp-wait-msg').textContent.includes('wp789')) passCheck('f', 'Waiting prefilled');
              else throw new Error('Waiting prefill failed');
              
              // g. flights render one row each, P1 check: HTML escaping
              ctx._fakeState = {
                status: 'running',
                flights: [
                  { flight_id: 'f1', pack_id: 'p1', experiment: 'e1', j: 1, decision: 'go', abort_level: 'none', abort_reason: '<b>x</b>', hover_only: false },
                  { flight_id: 'f2', hover_only: true }
                ]
              };
              ctx._timerFn();
              setTimeout(() => {
                const fb = getEl('cp-flights-body').innerHTML;
                if (fb.includes('f1') && fb.includes('f2') && fb.includes('&lt;b&gt;x&lt;/b&gt;') && !fb.includes('<b>x</b>')) passCheck('g', 'Flights rendered');
                else throw new Error('Flights missing or not escaped properly');
                
                // h. 409 and 503 error text shown
                qPath.value = 'a'; qPack.value = 'b';
                checkIds.forEach(id => getEl('cp-chk-' + id).checked = true);
                qPath.dispatch('input'); // re-enable go
                ctx._goFail = 409;
                qGoBtn.dispatch('click');
                setTimeout(() => {
                  if (getEl('cp-error').style.display === 'block' && getEl('cp-error').textContent === 'Conflict 409') {
                    ctx._goFail = 503;
                    qGoBtn.dispatch('click');
                    setTimeout(() => {
                      if (getEl('cp-error').style.display === 'block' && getEl('cp-error').textContent === 'Deps 503') {
                        ctx._fakeState = { status: 'idle' };
                        ctx._timerFn();
                        setTimeout(() => {
                          if (getEl('cp-error').style.display === 'block' && getEl('cp-error').textContent === 'Deps 503') {
                            ctx._stateFail = true;
                            ctx._timerFn();
                            setTimeout(() => {
                              if (getEl('cp-poll-error') && getEl('cp-poll-error').style.display === 'block' && getEl('cp-poll-error').textContent === 'Poll fail') {
                                ctx._stateFail = false;
                                ctx._timerFn();
                                setTimeout(() => {
                                  if (getEl('cp-poll-error').style.display === 'none') {
                                    passCheck('h', 'Errors shown and separated');
                                    
                                    // i. Go is the arm consent: no allow-arm toggle, a consent note, no /api/agent/control call
                                    fetchCalls = [];
                      if (getEl('cp-allow-arm')) throw new Error('allow-arm toggle should be gone');
                      if (!getEl('cp-consent-note')) throw new Error('consent note missing');
                      setTimeout(() => {
                        if (fetchCalls.some(f => f.url === '/api/agent/control')) throw new Error('panel must not call /api/agent/control');
                        setTimeout(() => {
                          passCheck('i', 'Go is the arm consent');
                          
                          // k. zero submitCommand/gatedCommand calls over the whole run
                          if (api.submitCount === 0 && api.gatedCount === 0) passCheck('k', 'Zero old API calls');
                          else throw new Error('Called old API');
                          
                          // j. after teardown, advancing timers causes no further fetch
                          const capturedTimer = ctx._timerFn;
                          ctx.window.__PLUGIN_DESTROY__();
                          fetchCalls = [];
                          if (ctx._timerFn !== null) throw new Error('Timer not cleared');
                          
                          if (capturedTimer) {
                            try { capturedTimer(); } catch(e) {}
                          }
                          setTimeout(() => {
                            if (fetchCalls.length !== 0) throw new Error('Fetched after teardown');
                            passCheck('j', 'No timer after teardown');
                            
                            console.log('ALL CHECKS PASSED');
                            process.exit(0);
                          }, 50);
                        }, 50);
                      }, 50);
                                  } else throw new Error('Poll error not cleared');
                                }, 50);
                              } else throw new Error('Poll error not shown');
                            }, 50);
                          } else throw new Error('Action error cleared by poll');
                        }, 50);
                      } else throw new Error('Error not shown 503');
                    }, 50);
                  } else throw new Error('Error not shown 409');
                }, 50);
              }, 50);
            }, 50);
          }, 50);
        } else {
          ctx._fakeState = { status: statuses[dIndex++] };
          ctx._timerFn();
          setTimeout(() => {
            if (!getEl('cp-pause-btn') || !getEl('cp-land-btn') || !getEl('cp-abort-btn')) dPass = false;
            if (getEl('cp-pause-btn').style.display === 'none') dPass = false;
            nextStatus();
          }, 10);
        }
      }
      nextStatus();
    }, 50);
  }, 50);
}

runHarness();
