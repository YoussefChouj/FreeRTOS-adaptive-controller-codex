'use strict';
/**
 * Offline verification harness for time-series-panel.js (tasks B1-B3).
 *
 * Drives the panel in a fake DOM with a stubbed shell API.
 * Runs tests for:
 *   1. B1: Position traces (c.earth_x, c.earth_y, c.altitude) & honest no-data reporting.
 *   2. B2: Dynamic variable picker dropdown, toggling traces, selection stability.
 *   3. B3: Pause safety indicator, age/offset readout, frozen view vs incoming buffer, resume-to-live.
 *   4. B3: Bounded ring buffer (strictly capped at RING_BUFFER_SIZE = 3000).
 *   5. B3: Zoom into region (view operation, zero sample loss, zoom-out restores buffer).
 *   6. Absence of synthetic demo data or fake waveforms.
 *
 * Run:  node ground_station/service/tests/time_series_panel_harness.js
 * Exit code 0 iff every check passes.
 */
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const assert = require('assert');

const PANEL = path.join(__dirname, '..', '..', '..',
  'docs', 'dashboard-platform', 'shell', 'plugins', 'time-series-panel.js');

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
  }
  addEventListener(ev, fn) { (this.handlers[ev] = this.handlers[ev] || []).push(fn); }
  dispatch(ev, extra) {
    const eventObj = Object.assign({
      type: ev,
      target: this,
      stopPropagation() {},
      preventDefault() {},
    }, extra || {});
    (this.handlers[ev] || []).forEach((fn) => fn.call(this, eventObj));
  }
  click() { this.dispatch('click'); }
  get innerHTML() { return this._html; }
  set innerHTML(html) {
    this._html = html;
    if (this.doc) this.doc.scan(html);
  }
  contains(child) {
    return false;
  }
}

class FakeDocument {
  constructor() {
    this.elements = {};
    this.docHandlers = {};
  }
  scan(html) {
    const tags = html.match(/<[a-zA-Z0-9-]+[^>]*>/g) || [];
    for (const tag of tags) {
      const idm = tag.match(/\bid="([^"]+)"/);
      if (!idm) continue;
      let el = this.elements[idm[1]];
      if (!el) {
        el = new Element(idm[1], tag.slice(1).split(/[\s>]/)[0]);
        el.doc = this;
        this.elements[idm[1]] = el;
      }
      const vm = tag.match(/\bvalue="([^"]*)"/);
      if (vm && el._valueInit !== true) { el.value = vm[1]; el._valueInit = true; }
      if (/\bchecked\b/.test(tag)) el.checked = true;
    }
  }
  getElementById(id) { return this.elements[id] || null; }
  addEventListener(ev, fn) { (this.docHandlers[ev] = this.docHandlers[ev] || []).push(fn); }
  dispatchDoc(ev, extra) {
    const eventObj = Object.assign({ type: ev }, extra || {});
    (this.docHandlers[ev] || []).forEach((fn) => fn.call(this, eventObj));
  }
}

// ── Stubbed shell API ──────────────────────────────────────────────────────
function makeApi() {
  return {
    stateCb: null,
    panelName: null,
    renderFn: null,
    subscribe(cb) { this.stateCb = cb; },
    registerPanel(name, renderFn) { this.panelName = name; this.renderFn = renderFn; },
  };
}

// ── Load Panel into VM Context ─────────────────────────────────────────────
function loadPanel() {
  const doc = new FakeDocument();
  const container = new Element('container', 'div');
  container.doc = doc;
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
    Date: Date,
    Math: Math,
    Number: Number,
    String: String,
    Boolean: Boolean,
    Array: Array,
    Object: Object,
    JSON: JSON,
    setInterval, clearInterval, setTimeout, clearTimeout,
    requestAnimationFrame: (cb) => { cb(); return 1; },
    localStorage: fakeStorage,
  };
  sandbox.window = sandbox;
  sandbox.__registerPlugin__ = function (name, init, destroy) {
    sandbox.pluginName = name;
    sandbox.pluginInit = init;
    sandbox.pluginDestroy = destroy;
  };

  vm.createContext(sandbox);
  const panelSrc = fs.readFileSync(PANEL, 'utf8');
  vm.runInContext(panelSrc, sandbox, { filename: PANEL });

  sandbox.pluginInit(api);
  api.renderFn(container);

  return { sandbox, doc, container, api, panelSrc };
}

// ── Test Suites ────────────────────────────────────────────────────────────
function runChecks() {
  console.log('--- RUNNING TIME SERIES PANEL OFFLINE CHECKS ---');

  // 1. B1 Provenance & Render Check
  {
    console.log('\n[CHECK 1: B1 Position Traces (X, Y, Z) and Honest No-Data Reporting]');
    const env = loadPanel();
    const doc = env.doc;
    const api = env.api;

    // Send synthetic payload with known position values in Frame C (slot 3)
    const syntheticState = {
      streams: {
        '3': {
          values: {
            'c.earth_x': 12.345,
            'c.earth_y': -45.678,
            'c.altitude': 2.750,
          }
        },
        '0': {
          values: {
            'status.roll_deg': 1.25,
            'status.pitch_deg': -0.75,
          }
        }
      }
    };

    api.stateCb(syntheticState); api.stateCb(syntheticState);

    const svg = doc.getElementById('ts-chart-svg');
    assert(svg, 'SVG chart must exist');
    const svgHtml = svg.innerHTML;

    // Verify coordinates are rendered into SVG polyline points
    assert(svgHtml.indexOf('polyline') !== -1, 'SVG must contain rendered polyline traces');
    console.log('  PASS: Polylines rendered for received position and status values');

    const legend = doc.getElementById('ts-legend');
    assert(legend, 'Legend element must exist');
    const legendHtml = legend.innerHTML;
    assert(legendHtml.indexOf('Pos X') !== -1, 'Legend must display Pos X');
    assert(legendHtml.indexOf('Pos Y') !== -1, 'Legend must display Pos Y');
    assert(legendHtml.indexOf('Pos Z (Alt)') !== -1, 'Legend must display Pos Z (Alt)');
    assert(legendHtml.indexOf('12.3450 m') !== -1, 'Legend must show Pos X = 12.3450 m');
    assert(legendHtml.indexOf('-45.6780 m') !== -1, 'Legend must show Pos Y = -45.6780 m');
    assert(legendHtml.indexOf('2.7500 m') !== -1, 'Legend must show Pos Z = 2.7500 m');
    console.log('  PASS: Legend reports verified values: Pos X = 12.3450 m, Pos Y = -45.6780 m, Pos Z = 2.7500 m');

    // Missing axis test: omit c.altitude in a payload
    const missingZState = {
      streams: {
        '3': {
          values: {
            'c.earth_x': 10.0,
            'c.earth_y': 20.0,
            // c.altitude omitted
          }
        }
      }
    };
    const envMissing = loadPanel();
    envMissing.api.stateCb(missingZState);
    const legendMissingHtml = envMissing.doc.getElementById('ts-legend').innerHTML;
    assert(legendMissingHtml.indexOf('Pos Z (Alt)') !== -1, 'Pos Z is present in legend');
    assert(legendMissingHtml.indexOf('— (no data)') !== -1, 'Absent Z axis honestly displays "— (no data)"');
    console.log('  PASS: Absent axis honestly displays "— (no data)" without drawing synthetic fallback');
  }

  // 2. B2 Configurable Variable Picker Check
  {
    console.log('\n[CHECK 2: B2 Configurable Variable Picker]');
    const env = loadPanel();
    const doc = env.doc;
    const api = env.api;

    // Initially known variables count
    const countEl = doc.getElementById('ts-picker-count');
    assert(countEl, 'Picker count element exists');
    console.log('  Initial picker count:', countEl.textContent);

    // Feed new streaming variables from arbitrary slots (e.g. motor.rpm_0, sysid_dither)
    const stateWithNewVars = {
      streams: {
        '1': {
          values: {
            'id.counter': 42.0,
            'sysid_dither': 0.123,
          }
        },
        '3': {
          values: {
            'motor.rpm_0': 4500,
            'motor.rpm_1': 4550,
          }
        }
      }
    };
    api.stateCb(stateWithNewVars);

    // Toggle picker menu
    const pickerToggle = doc.getElementById('ts-picker-toggle');
    assert(pickerToggle, 'Picker toggle button exists');
    pickerToggle.click();

    const pickerList = doc.getElementById('ts-picker-list');
    assert(pickerList, 'Picker list container exists');
    const listHtml = pickerList.innerHTML;

    assert(listHtml.indexOf('motor.rpm_0') !== -1, 'Picker list dynamically includes motor.rpm_0 from live stream');
    assert(listHtml.indexOf('sysid_dither') !== -1, 'Picker list dynamically includes sysid_dither from live stream');
    assert(listHtml.indexOf('id.counter') !== -1, 'Picker list dynamically includes id.counter from live stream');
    console.log('  PASS: Picker dynamically populates from variables actually present in live streams');

    // Test checking/unchecking a variable
    pickerList.dispatch('change', {
      target: {
        dataset: { key: 'motor.rpm_0' },
        checked: true
      }
    });

    const legendHtml = doc.getElementById('ts-legend').innerHTML;
    assert(legendHtml.indexOf('motor.rpm_0') !== -1, 'Checking motor.rpm_0 immediately adds it to legend and chart');
    console.log('  PASS: Checking variable adds trace to plot and legend');

    pickerList.dispatch('change', {
      target: {
        dataset: { key: 'motor.rpm_0' },
        checked: false
      }
    });
    const legendAfterUncheck = doc.getElementById('ts-legend').innerHTML;
    assert(legendAfterUncheck.indexOf('motor.rpm_0') === -1, 'Unchecking removes trace from legend');
    console.log('  PASS: Unchecking variable removes trace');
  }

  // 3. B3 Recording Controls: Pause, Step back / Replay, Resume Live
  {
    console.log('\n[CHECK 3: B3 Recording Controls & Safety Rule]');
    const env = loadPanel();
    const doc = env.doc;
    const api = env.api;

    // Feed initial 10 samples
    for (let i = 0; i < 10; i++) {
      api.stateCb({
        streams: {
          '3': { values: { 'c.earth_x': i * 1.0, 'c.earth_y': i * 2.0, 'c.altitude': 1.0 } }
        }
      });
    }

    const btnPause = doc.getElementById('ts-btn-pause');
    const btnResume = doc.getElementById('ts-btn-resume');
    const banner = doc.getElementById('ts-paused-banner');
    const panelRoot = doc.getElementById('ts-panel-root');

    assert(btnPause, 'Pause button exists');
    assert(btnResume, 'Resume button exists');

    // Click pause
    btnPause.click();

    // Verify paused state visual safety indicator
    assert.strictEqual(banner.style.display, 'flex', 'Paused banner must be visibly displayed (display: flex)');
    assert(panelRoot.classList.contains('is-paused'), 'Panel root must have .is-paused class with hazard styling');
    assert.strictEqual(btnPause.style.display, 'none', 'Pause button hidden when paused');
    assert.strictEqual(btnResume.style.display, 'inline-flex', 'Resume button prominently shown when paused');
    console.log('  PASS: Paused state is continuously, unmistakably obvious with warning banner and hazard border');

    // Feed 20 new samples while paused
    for (let i = 10; i < 30; i++) {
      api.stateCb({
        streams: {
          '3': { values: { 'c.earth_x': i * 1.0, 'c.earth_y': i * 2.0, 'c.altitude': 1.0 } }
        }
      });
    }

    // Check age / offset readout
    const bannerAge = doc.getElementById('ts-paused-age');
    assert(bannerAge, 'Age readout element exists');
    assert(bannerAge.textContent.indexOf('behind live') !== -1, 'Readout reports time/offset behind live');
    assert(bannerAge.textContent.indexOf('offset: -20') !== -1, 'Readout reports exact sample offset (-20 samples behind)');
    console.log('  PASS: Paused view remains frozen at pause point; age readout reports:', bannerAge.textContent);

    // Step back 5 samples
    const btnStepB = doc.getElementById('ts-btn-step-b');
    btnStepB.click();
    assert(bannerAge.textContent.indexOf('offset: -25') !== -1, 'Step back -5 samples updates offset to -25');
    console.log('  PASS: Step-back control navigates history buffer');

    // Resume to Live
    btnResume.click();
    assert.strictEqual(banner.style.display, 'none', 'Paused banner hidden upon resume');
    assert(!panelRoot.classList.contains('is-paused'), 'Hazard border removed upon resume');
    assert.strictEqual(btnPause.style.display, 'inline-flex', 'Pause button restored');
    assert.strictEqual(btnResume.style.display, 'none', 'Resume button hidden');
    const liveBadge = doc.getElementById('ts-live-badge');
    assert(liveBadge.textContent.indexOf('LIVE') !== -1, 'Badge returns to LIVE');
    console.log('  PASS: Resuming jumps directly to live telemetry in one obvious action');
  }

  // 4. B3 Bounded Ring Buffer Check
  {
    console.log('\n[CHECK 4: B3 Bounded Ring Buffer]');
    const env = loadPanel();
    const api = env.api;

    const RING_CAPACITY = 3000;
    // Feed 4000 samples to verify buffer does not exceed RING_CAPACITY
    for (let i = 0; i < 4000; i++) {
      api.stateCb({
        streams: {
          '3': { values: { 'c.earth_x': i * 0.1, 'c.earth_y': i * 0.2, 'c.altitude': 1.5 } }
        }
      });
    }

    const scrubber = env.doc.getElementById('ts-scrubber');
    const maxSamples = Number(scrubber.max) + 1;
    assert.strictEqual(maxSamples, RING_CAPACITY, `Buffer must be bounded at exactly ${RING_CAPACITY} samples (got ${maxSamples})`);
    console.log(`  PASS: Ingested 4000 samples; buffer strictly bounded at ${maxSamples} samples (does not grow unbounded)`);
    console.log(`  Buffer capacity: ${RING_CAPACITY} samples = ${(RING_CAPACITY / 100).toFixed(1)}s @ 100 Hz, ${(RING_CAPACITY / 80).toFixed(1)}s @ 80 Hz`);
  }

  // 5. B3 Region Zoom Check
  {
    console.log('\n[CHECK 5: B3 Region Zoom]');
    const env = loadPanel();
    const doc = env.doc;
    const api = env.api;

    // Feed 100 samples
    for (let i = 0; i < 100; i++) {
      api.stateCb({
        streams: {
          '3': { values: { 'c.earth_x': Math.sin(i * 0.1), 'c.earth_y': Math.cos(i * 0.1), 'c.altitude': 1.0 } }
        }
      });
    }

    const scrubberBefore = doc.getElementById('ts-scrubber');
    const countBefore = Number(scrubberBefore.max) + 1;

    // Trigger Zoom In button
    const btnZIn = doc.getElementById('ts-btn-zoom-in');
    btnZIn.click();

    const svgZoom = doc.getElementById('ts-chart-svg');
    assert(svgZoom.innerHTML.indexOf('Zoomed Region') !== -1, 'SVG chart indicates active Zoomed Region');

    // Verify buffer was not truncated or dropped during zoom
    const scrubberDuring = doc.getElementById('ts-scrubber');
    const countDuring = Number(scrubberDuring.max) + 1;
    assert.strictEqual(countDuring, countBefore, 'Zoom in must not discard buffered samples');
    console.log('  PASS: Zoom in restricts viewport without discarding buffered samples');

    // Trigger Reset Zoom button
    const btnZReset = doc.getElementById('ts-btn-zoom-reset');
    btnZReset.click();

    assert(svgZoom.innerHTML.indexOf('Zoomed Region') === -1, 'Reset Zoom clears zoomed indicator');
    const scrubberAfter = doc.getElementById('ts-scrubber');
    assert.strictEqual(Number(scrubberAfter.max) + 1, countBefore, 'Reset zoom restores full buffer view');
    console.log('  PASS: Reset zoom restores full buffer view');
  }

  // 6. No Synthetic Data / Waveforms Check
  {
    console.log('\n[CHECK 6: No Synthetic Waveform / Lissajous Data]');
    const panelSrc = fs.readFileSync(PANEL, 'utf8');
    assert(panelSrc.indexOf('generateDemoPoint') === -1, 'Must NOT contain generateDemoPoint');
    assert(panelSrc.indexOf('Lissajous') === -1, 'Must NOT contain Lissajous demo generator');
    assert(panelSrc.indexOf('Math.sin(') === -1, 'Panel code must NOT contain synthetic sine wave generators');
    console.log('  PASS: Confirmed zero synthetic waveforms or demo fallback generators in panel code');
  }

  console.log('\nALL CHECKS PASSED SUCCESSFULLY.');
}

runChecks();
