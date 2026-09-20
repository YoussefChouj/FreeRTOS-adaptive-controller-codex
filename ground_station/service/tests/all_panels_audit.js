'use strict';
/**
 * Offline validation harness for ALL 17 dashboard panels.
 * Drives each panel in a DOM harness with:
 *   1. Initial render
 *   2. Live render (real/synthetic payload)
 *   3. Empty payload (disconnected / no streams)
 *   4. Missing keys payload (streams present, values empty)
 *
 * Inspects all elements in doc.elements (id -> textContent / innerHTML).
 */

const fs = require('fs');
const path = require('path');
const vm = require('vm');

const PLUGINS_DIR = path.join(__dirname, '..', '..', '..', 'docs', 'dashboard-platform', 'shell', 'plugins');

class Element {
  constructor(id, tag) {
    this.id = id || '';
    this.tagName = (tag || 'div').toUpperCase();
    this._html = '';
    this._text = '';
    this.value = '';
    this.checked = false;
    this.disabled = false;
    this.className = '';
    this.classList = {
      _classes: new Set(),
      add: (c) => this.classList._classes.add(c),
      remove: (c) => this.classList._classes.delete(c),
      contains: (c) => this.classList._classes.has(c),
    };
    this.handlers = {};
    this.style = {};
    this.dataset = {};
    this.doc = null;
    this.children = [];
    this.parentElement = null;
    this.width = 400;
    this.height = 300;
  }
  addEventListener(ev, fn) { (this.handlers[ev] = this.handlers[ev] || []).push(fn); }
  dispatch(ev, extra) {
    const eventObj = Object.assign({ type: ev, target: this, stopPropagation() {}, preventDefault() {} }, extra || {});
    (this.handlers[ev] || []).forEach((fn) => fn.call(this, eventObj));
  }
  click() { this.dispatch('click'); }
  get textContent() { return this._text; }
  set textContent(txt) {
    this._text = String(txt);
    this._html = String(txt);
  }
  get innerHTML() { return this._html; }
  set innerHTML(html) {
    this._html = String(html);
    this._text = this._html.replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').trim();
    if (this.doc) this.doc.scan(html, this);
  }
  getContext(type) {
    return {
      fillRect() {}, fillText() {}, beginPath() {}, moveTo() {}, lineTo() {},
      stroke() {}, fill() {}, arc() {}, strokeRect() {}, measureText() { return { width: 10 }; },
      setTransform() {}, save() {}, restore() {}, setLineDash() {},
      font: '', fillStyle: '', strokeStyle: '', lineWidth: 1, textAlign: 'left', textBaseline: 'top',
    };
  }
  insertBefore(newChild, refChild) {
    newChild.parentElement = this;
    const idx = this.children.indexOf(refChild);
    if (idx >= 0) this.children.splice(idx, 0, newChild);
    else this.children.push(newChild);
    return newChild;
  }
  appendChild(child) {
    child.parentElement = this;
    this.children.push(child);
    return child;
  }
  removeChild(child) {
    const idx = this.children.indexOf(child);
    if (idx >= 0) this.children.splice(idx, 1);
    child.parentElement = null;
    return child;
  }
  querySelector(sel) {
    const all = this.querySelectorAll(sel);
    return all.length > 0 ? all[0] : null;
  }
  querySelectorAll(sel) {
    if (!this.doc) return [];
    return this.doc.querySelectorAll(sel);
  }
  getAttribute(name) { return this.dataset[name] || null; }
  setAttribute(name, val) { this.dataset[name] = String(val); }
  hasAttribute(name) { return name in this.dataset; }
  removeAttribute(name) { delete this.dataset[name]; }
  focus() {}
  blur() {}
}

class FakeDocument {
  constructor() {
    this.elements = {};
    this.body = new Element('body', 'body');
    this.body.doc = this;
    this.elements['body'] = this.body;
    this.hidden = false;
  }
  createElement(tag) {
    const el = new Element('', tag);
    el.doc = this;
    return el;
  }
  scan(html, parent) {
    const tags = html.match(/<[a-zA-Z0-9-]+[^>]*>/g) || [];
    for (const tag of tags) {
      const idm = tag.match(/\bid="([^"]+)"/);
      if (idm) {
        const id = idm[1];
        if (!this.elements[id]) {
          const el = new Element(id, tag.slice(1).split(/[\s>]/)[0]);
          el.doc = this;
          el.parentElement = parent;
          this.elements[id] = el;
        }
      }
    }
  }
  getElementById(id) { return this.elements[id] || null; }
  querySelector(sel) {
    const all = this.querySelectorAll(sel);
    return all.length > 0 ? all[0] : null;
  }
  querySelectorAll(sel) {
    if (sel.startsWith('#')) {
      const el = this.getElementById(sel.slice(1));
      return el ? [el] : [];
    }
    if (sel.startsWith('.')) {
      const cls = sel.slice(1);
      return Object.values(this.elements).filter(e => e.classList.contains(cls) || (e.className && e.className.indexOf(cls) !== -1));
    }
    return [];
  }
}

function makeApi() {
  return {
    stateCb: null,
    panelName: null,
    renderFn: null,
    meta: null,
    currentState: null,
    subscribe(cb) { this.stateCb = cb; },
    registerPanel(name, renderFn, meta) {
      this.panelName = name;
      this.renderFn = renderFn;
      this.meta = meta;
    },
    getState() { return this.currentState || {}; },
    getGates() { return { connected: true, fresh: true, schema: true, disarmed: true, command: true }; },
    getArmState() { return { armed: false, source: 'test' }; },
    getSignalState() { return 'LIVE'; },
    submitCommand() { return Promise.resolve({ ok: true }); },
    subscribeSlot() { return Promise.resolve({ ok: true, via: 'fake' }); },
    unsubscribeSlot() { return Promise.resolve({ ok: true }); },
  };
}

function createInstance(filename) {
  const filepath = path.join(PLUGINS_DIR, filename);
  const code = fs.readFileSync(filepath, 'utf8');

  const doc = new FakeDocument();
  const container = new Element('container', 'div');
  container.doc = doc;
  const sidebar = new Element('sidebar', 'div');
  sidebar.doc = doc;
  doc.elements['sidebar'] = sidebar;

  const api = makeApi();
  const storageStore = {};
  const fakeStorage = {
    getItem(k) { return storageStore[k] !== undefined ? storageStore[k] : null; },
    setItem(k, v) { storageStore[k] = String(v); },
    removeItem(k) { delete storageStore[k]; },
    clear() { Object.keys(storageStore).forEach(k => delete storageStore[k]); }
  };

  const sandbox = {
    document: doc,
    console: { log() {}, warn() {}, error() {} },
    Date, Math, Number, String, Boolean, Array, Object, JSON, RegExp, Error, Promise, Map, Set,
    Float64Array, Float32Array, Int32Array, Uint8Array,
    setInterval() { return 1; }, clearInterval() {},
    setTimeout() { return 1; }, clearTimeout() {},
    requestAnimationFrame: (cb) => { cb(); return 1; },
    cancelAnimationFrame() {},
    localStorage: fakeStorage,
    fetch: (url) => Promise.resolve({
      ok: true, status: 200,
      json: () => Promise.resolve(url && (url.indexOf('/sessions') !== -1 || url.indexOf('/experiments') !== -1) ? [] : {})
    }),
    confirm: () => true,
    alert: () => {},
    prompt: () => '',
  };
  sandbox.window = sandbox;
  sandbox.addEventListener = (ev, fn) => {};
  sandbox.removeEventListener = (ev, fn) => {};
  sandbox.__registerPlugin__ = function (name, init, destroy) {
    sandbox.pluginName = name;
    sandbox.pluginInit = init;
    sandbox.pluginDestroy = destroy;
  };

  vm.createContext(sandbox);
  vm.runInContext(code, sandbox, { filename: filepath });
  sandbox.pluginInit(api);
  api.renderFn(container, api);

  return { doc, container, api, sandbox };
}

function snapshotDoc(doc) {
  const snap = {};
  for (const [id, el] of Object.entries(doc.elements)) {
    if (id && id !== 'body' && id !== 'sidebar' && id !== 'container') {
      const txt = (el.textContent || '').trim();
      const html = (el.innerHTML || '').trim();
      snap[id] = { text: txt, html: html.length > 200 ? html.slice(0, 200) + '...' : html };
    }
  }
  return snap;
}

const fullPayload = {
  connected: true,
  slot_freshness_ttl_ns: 30e9,
  streams: {
    '0': {
      last_update_ns: Date.now() * 1e6,
      sequence: 100,
      received: 100,
      dropped: 0,
      loss_pct: 0.0,
      values: {
        'status.arm': 1, 'status.flymode': 1, 'status.vbat': 16.2,
        'status.rc_authority': 1, 'status.sbus': 0, 'status.estimator_ready': 1,
        'status.roll_deg': 2.5, 'status.pitch_deg': -1.25, 'status.yaw_deg': 180.0,
        'status.twc_execute': 0, 'status.twc_arrived': 0, 'status.of_hold': 1,
        'mrac.roll.u_ad': 0.0123, 'mrac.roll.e': -0.0456,
        'mrac.pitch.u_ad': -0.021, 'mrac.pitch.e': 0.033,
        'mrac.yaw.u_ad': 0.01, 'mrac.yaw.e': 0.02,
        'mrac.z.u_ad': 0.03, 'mrac.z.e': 0.04,
        'mrac.pitch.theta_0': 0.1, 'mrac.pitch.theta_1': 0.2, 'mrac.pitch.theta_2': 0.3,
        'mrac.pitch.theta_3': 0.0, 'mrac.pitch.theta_4': 0.0, 'mrac.pitch.theta_5': 0.0,
        'mrac.roll.theta_0': 0.1, 'mrac.roll.theta_1': 0.2, 'mrac.roll.theta_2': 0.3,
        'mrac.roll.theta_3': 0.0, 'mrac.roll.theta_4': 0.0, 'mrac.roll.theta_5': 0.0,
        'mrac.yaw.theta_0': 0.1, 'mrac.yaw.theta_1': 0.2, 'mrac.yaw.theta_2': 0.3,
        'mrac.yaw.theta_3': 0.0, 'mrac.yaw.theta_4': 0.0, 'mrac.yaw.theta_5': 0.0,
        'mrac.z.theta_0': 0.1, 'mrac.z.theta_1': 0.2, 'mrac.z.theta_2': 0.3,
        'mrac.z.theta_3': 0.0, 'mrac.z.theta_4': 0.0, 'mrac.z.theta_5': 0.0,
        'ekf.vel_x': 1.5, 'ekf.vel_y': -0.5, 'ekf.vel_z': 0.1,
        'ekf.bias_gyro_x': 0.0001, 'ekf.bias_gyro_y': -0.0002, 'ekf.bias_gyro_z': 0.0003,
        'ekf.bias_accel_x': 0.01, 'ekf.bias_accel_y': -0.02, 'ekf.bias_accel_z': 0.03,
        'gs_max_horizontal_speed_mps': 5.0, 'gs_max_vertical_speed_mps': 2.5,
        'gs_max_pitch_deg': 25.0, 'gs_max_roll_deg': 25.0,
        'slot0.seq': 100, 'slot0.received': 100, 'slot0.dropped': 0, 'slot0.loss_pct': 0.0, 'slot0.t_ms': 5000,
      }
    },
    '1': {
      last_update_ns: Date.now() * 1e6,
      values: {
        'pid.gyrox.FB': 12.3, 'pid.gyroy.FB': -45.6, 'pid.gyroz.FB': 7.8,
        'pid.gyrox.U': 0.123, 'pid.gyroy.U': -0.234, 'pid.gyroz.U': 0.345,
        'slot1.g_of_bias_mode': 1, 'slot1.g_of_bias_ema_freeze': 0,
      }
    },
    '3': {
      last_update_ns: Date.now() * 1e6,
      values: {
        'c.gyro_x': 0.12, 'c.gyro_y': -0.34, 'c.gyro_z': 0.56,
        'c.roll': 2.5, 'c.pitch': -1.25, 'c.yaw': 180.0,
        'c.earth_x': 12.34, 'c.earth_y': -5.67, 'c.altitude': 2.75,
        'motor.rpm_0': 5400, 'motor.rpm_1': 5500, 'motor.rpm_2': 5600, 'motor.rpm_3': 5700,
      }
    },
    'rtos': {
      last_update_ns: Date.now() * 1e6,
      values: {
        'rtos.scheduler_tick_count': 123456, 'rtos.heap_free_bytes': 32768,
        'rtos.usart3_tx_count': 5000, 'rtos.cmd_queue_depth': 0, 'rtos.cmd_queue_max': 16,
        'rtos.send_task_ticks': 5000, 'rtos.dma_busy': 0, 'rtos.queue_depth': 128, 'rtos.usart3_tx_drops': 0,
      }
    }
  }
};

const emptyPayload = {
  connected: false,
  streams: {}
};

const missingValuesPayload = {
  connected: true,
  streams: {
    '0': { values: {} },
    '1': { values: {} },
    '3': { values: {} }
  }
};

const files = fs.readdirSync(PLUGINS_DIR).filter(f => f.endsWith('.js')).sort();
const audit = {};

for (const filename of files) {
  try {
    // 1. Initial snapshot
    const instInitial = createInstance(filename);
    const snapInit = snapshotDoc(instInitial.doc);
    const name = instInitial.api.panelName || instInitial.sandbox.pluginName;
    const hasSub = typeof instInitial.api.stateCb === 'function';

    // 2. Full payload snapshot
    const instFull = createInstance(filename);
    if (instFull.api.stateCb) instFull.api.stateCb(fullPayload);
    const snapFull = snapshotDoc(instFull.doc);

    // 3. Empty payload snapshot
    const instEmpty = createInstance(filename);
    if (instEmpty.api.stateCb) instEmpty.api.stateCb(emptyPayload);
    const snapEmpty = snapshotDoc(instEmpty.doc);

    // 4. Missing values snapshot
    const instMissing = createInstance(filename);
    if (instMissing.api.stateCb) instMissing.api.stateCb(missingValuesPayload);
    const snapMissing = snapshotDoc(instMissing.doc);

    audit[filename] = {
      status: 'OK',
      name,
      hasSub,
      snapInit,
      snapFull,
      snapEmpty,
      snapMissing,
    };
  } catch (err) {
    audit[filename] = {
      status: 'ERROR',
      error: err.message,
      stack: err.stack,
    };
  }
}

console.log(JSON.stringify(audit, null, 2));
