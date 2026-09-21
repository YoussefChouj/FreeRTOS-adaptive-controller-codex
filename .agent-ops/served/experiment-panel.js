/**
 * experiment-panel.js — Firmware experiment runtime control panel
 *
 * Controls the experiment runtime via the HTTP API:
 *   GET  /experiments          → list active experiment runs
 *   POST /experiments          → start an experiment
 *   POST /experiments/<name>/abort → abort a running experiment
 *
 * Displays:
 *   - Current state (idle/settling/measuring/complete/aborted)
 *   - Tick counter and sample count
 *   - Settle/measure progress bars (SVG)
 *   - Event log (settled, measuring, complete, aborted)
 *   - Parameter sweep: before/after values
 */
(function () {
  'use strict';

  var POLL_INTERVAL_MS = 500;

  var STATE_LABELS = {
    idle:       'Idle',
    settling:   'Settling…',
    measuring:  'Measuring…',
    complete:   'Complete',
    aborted:   'Aborted',
  };

  var STATE_COLORS = {
    idle:      'var(--muted)',
    settling:  '#4a9eff',
    measuring: 'var(--amber)',
    complete:  'var(--green)',
    aborted:   'var(--red)',
  };

  // ── Experiment Types Registry (sourced from platform/firmware_contract.py) ───
  var DEFAULT_EXPERIMENT_TYPES = [
    'step_response',
    'controls',
    'chirp',
    'frequency_sweep',
    'multisine',
  ];
  var _experimentTypes = DEFAULT_EXPERIMENT_TYPES.slice();

  // ── DOM helpers ─────────────────────────────────────────────────────────
  function q(id) { return document.getElementById(id); }

  function fmt(v) {
    if (v == null) return '—';
    return parseFloat(v).toFixed(4);
  }

  // ── SVG progress bar ─────────────────────────────────────────────────────
  function svgProgressBar(id, pct, color, label) {
    var W = 260, H = 24, PAD_X = 4, PAD_Y = 4;
    var INNER_W = W - PAD_X * 2;
    var INNER_H = H - PAD_Y * 2;
    var barW = Math.max(0, Math.min(INNER_W, INNER_W * pct));
    return [
      '<svg viewBox="0 0 ' + W + ' ' + H + '" width="100%" style="max-width:' + W + 'px;display:block">',
      '  <rect x="' + PAD_X + '" y="' + PAD_Y + '" width="' + INNER_W + '" height="' + INNER_H + '"',
      '    fill="rgba(255,255,255,0.06)" rx="4"/>',
      '  <rect x="' + PAD_X + '" y="' + PAD_Y + '" width="' + barW + '" height="' + INNER_H + '"',
      '    fill="' + color + '" opacity="0.8" rx="4"/>',
      '  <text x="' + (PAD_X + 6) + '" y="' + (H / 2 + 4) + '"',
      '    font-size="10" fill="rgba(255,255,255,0.7)" font-family="Consolas,monospace">',
      label,
      '  </text>',
      '  <text x="' + (W - PAD_X - 6) + '" y="' + (H / 2 + 4) + '" text-anchor="end"',
      '    font-size="10" fill="rgba(255,255,255,0.45)" font-family="Consolas,monospace">',
      Math.round(pct * 100) + '%',
      '  </text>',
      '</svg>',
    ].join('');
  }

  // ── Build initial DOM ───────────────────────────────────────────────────
  function buildHTML() {
    return [
      '<style>',
      '.ep-section { margin-bottom: 14px; }',
      '.ep-section:last-child { margin-bottom: 0; }',
      '.ep-label { font-size: 11px; color: var(--muted); margin-bottom: 4px; }',
      '.ep-row { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }',
      '.ep-state-badge {',
      '  display: inline-flex; align-items: center; gap: 6px;',
      '  padding: 5px 12px; border-radius: 20px; font-size: 13px; font-weight: 700;',
      '}',
      '.ep-big-value { font-size: 20px; font-weight: 600; font-family: Consolas, monospace; }',
      '.ep-small-value { font-size: 13px; font-family: Consolas, monospace; color: var(--muted); }',
      '.ep-form { display: flex; flex-direction: column; gap: 10px; }',
      '.ep-form-row { display: flex; align-items: center; gap: 8px; }',
      '.ep-input {',
      '  background: var(--bg); border: 1px solid var(--border);',
      '  color: var(--text); padding: 5px 8px; border-radius: 4px;',
      '  font-family: Consolas, monospace; font-size: 12px; width: 90px;',
      '}',
      '.ep-input:focus { outline: none; border-color: var(--accent, #4a9eff); }',
      '.ep-select {',
      '  background: var(--bg); border: 1px solid var(--border);',
      '  color: var(--text); padding: 5px 8px; border-radius: 4px;',
      '  font-family: Consolas, monospace; font-size: 12px; min-width: 140px; cursor: pointer;',
      '}',
      '.ep-select:focus { outline: none; border-color: var(--accent, #4a9eff); }',
      '.ep-btn {',
      '  padding: 6px 14px; border-radius: 4px; font-size: 12px; font-weight: 600;',
      '  cursor: pointer; border: none; transition: opacity 0.15s;',
      '}',
      '.ep-btn:hover { opacity: 0.85; }',
      '.ep-btn-primary { background: var(--accent, #4a9eff); color: #fff; }',
      '.ep-btn-danger  { background: var(--red); color: #fff; }',
      '.ep-btn:disabled { opacity: 0.4; cursor: not-allowed; }',
      '.ep-event-log {',
      '  background: var(--bg); border: 1px solid var(--border);',
      '  border-radius: 4px; padding: 6px 8px; max-height: 120px; overflow-y: auto;',
      '  font-family: Consolas, monospace; font-size: 11px;',
      '}',
      '.ep-event { padding: 2px 0; border-bottom: 1px solid rgba(255,255,255,0.05); }',
      '.ep-event:last-child { border-bottom: none; }',
      '.ep-event-tick { color: var(--muted); margin-right: 6px; }',
      '.ep-params-table { width: 100%; border-collapse: collapse; font-size: 11px; }',
      '.ep-params-table th {',
      '  text-align: left; font-size: 10px; font-weight: 600; color: var(--muted);',
      '  letter-spacing: 0.06em; text-transform: uppercase;',
      '  padding: 4px 8px; border-bottom: 1px solid var(--border);',
      '}',
      '.ep-params-table td { padding: 4px 8px; font-family: Consolas, monospace; }',
      '.ep-params-table tr:hover td { background: rgba(255,255,255,0.03); }',
      '.ep-params-table .param-before { color: var(--amber); }',
      '.ep-params-table .param-after  { color: var(--green); }',
      '.ep-params-table .param-same   { color: var(--muted); }',
      '.ep-no-data { color: var(--muted); font-size: 12px; font-style: italic; }',
      '.ep-param-input {',
      '  background: var(--bg); border: 1px solid var(--border);',
      '  color: var(--text); padding: 4px 6px; border-radius: 3px;',
      '  font-family: Consolas, monospace; font-size: 11px; width: 100px;',
      '}',
      '</style>',

      /* State badge + tick counter */
      '<div class="ep-section">',
      '  <div class="ep-label">Experiment State</div>',
      '  <div class="ep-row">',
      '    <div id="ep-state-badge" class="ep-state-badge" style="background:rgba(100,100,100,0.15);color:var(--muted)">Idle</div>',
      '    <div style="flex:1">',
      '      <div id="ep-tick-label" class="ep-small-value">Tick: —</div>',
      '    </div>',
      '    <div id="ep-sample-count" class="ep-big-value" style="font-size:16px">— samples</div>',
      '  </div>',
      '</div>',

      /* Progress bars */
      '<div class="ep-section">',
      '  <div class="ep-label">Settling Progress</div>',
      '  <div id="ep-settle-bar"></div>',
      '</div>',
      '<div class="ep-section">',
      '  <div class="ep-label">Measure Progress</div>',
      '  <div id="ep-measure-bar"></div>',
      '</div>',

      /* Start / Abort controls */
      '<div class="ep-section">',
      '  <div class="ep-label">Controls</div>',
      '  <div class="ep-form">',
      '    <div class="ep-form-row">',
      '      <select id="ep-name" class="ep-input ep-select">',
      _experimentTypes.map(function (t) {
        return '<option value="' + t + '">' + t + '</option>';
      }).join(''),
      '      </select>',
      '      <input id="ep-settle"  class="ep-input"    type="number" placeholder="settle" value="100"/>',
      '      <input id="ep-measure" class="ep-input"    type="number" placeholder="measure" value="200"/>',
      '    </div>',
      '    <div class="ep-form-row">',
      '      <div style="font-size:11px;color:var(--muted)">param:</div>',
      '      <input id="ep-param-key"   class="ep-param-input" type="text"   placeholder="key"   value="safety.gs_max_horizontal_speed_mps"/>',
      '      <input id="ep-param-val"  class="ep-param-input" type="number" placeholder="value" value="5.0"/>',
      '    </div>',
      '    <div class="ep-form-row">',
      '      <button id="ep-start-btn"  class="ep-btn ep-btn-primary">Start Experiment</button>',
      '      <button id="ep-abort-btn"  class="ep-btn ep-btn-danger"  disabled>Abort</button>',
      '    </div>',
      '  </div>',
      '</div>',

      /* Event log */
      '<div class="ep-section">',
      '  <div class="ep-label">Event Log</div>',
      '  <div id="ep-event-log" class="ep-event-log">',
      '    <div class="ep-no-data">No events</div>',
      '  </div>',
      '</div>',

      /* Parameter sweep table */
      '<div class="ep-section">',
      '  <div class="ep-label">Parameter Sweep</div>',
      '  <table class="ep-params-table">',
      '    <thead><tr><th>Parameter</th><th>Before</th><th>After</th></tr></thead>',
      '    <tbody id="ep-params-body">',
      '      <tr><td colspan="3" class="ep-no-data">No sweep data</td></tr>',
      '    </tbody>',
      '  </table>',
      '</div>',
    ].join('');
  }

  // ── State ───────────────────────────────────────────────────────────────
  var _currentRun = null;
  var _pollingTimer = null;
  var _api = null;

  // ── Fetch experiment list ───────────────────────────────────────────────
  function fetchExperiments() {
    return fetch('/experiments')
      .then(function (r) { return r.json(); })
      .catch(function () { return []; });
  }

  // ── Fetch experiment detail ─────────────────────────────────────────────
  function fetchExperimentDetail(name) {
    return fetch('/experiments/' + encodeURIComponent(name))
      .then(function (r) { return r.json(); })
      .catch(function () { return null; });
  }

  // ── Experiment Type Options Management ──────────────────────────────────
  function setExperimentType(typeName) {
    if (!typeName) return;
    var select = q('ep-name');
    if (!select) return;

    var exists = false;
    for (var i = 0; i < select.options.length; i++) {
      if (select.options[i].value === typeName) {
        exists = true;
        break;
      }
    }

    // Preserve legacy or custom experiment type strings not in the predefined registry
    if (!exists) {
      var opt = document.createElement('option');
      opt.value = typeName;
      opt.textContent = typeName + ' (custom)';
      select.appendChild(opt);
    }

    select.value = typeName;
  }

  function updateExperimentTypeOptions(types) {
    if (!Array.isArray(types) || types.length === 0) return;
    _experimentTypes = types.slice();
    var select = q('ep-name');
    if (!select) return;
    var currentVal = select.value;
    select.innerHTML = _experimentTypes.map(function (t) {
      return '<option value="' + t + '">' + t + '</option>';
    }).join('');
    if (currentVal) {
      setExperimentType(currentVal);
    }
  }

  function loadContractExperimentTypes() {
    if (typeof fetch !== 'function') return;
    fetch('/api/contract')
      .then(function (r) {
        if (!r.ok) return null;
        return r.json();
      })
      .then(function (contract) {
        if (contract && Array.isArray(contract.experiment_types)) {
          updateExperimentTypeOptions(contract.experiment_types);
        }
      })
      .catch(function () { /* fallback to default types */ });
  }

  // ── Update state badge ─────────────────────────────────────────────────
  function updateState(run) {
    var badge = q('ep-state-badge');
    var tickLabel = q('ep-tick-label');
    var sampleCount = q('ep-sample-count');
    var startBtn = q('ep-start-btn');
    var abortBtn = q('ep-abort-btn');

    if (!run || !run.state || run.state === 'idle') {
      _currentRun = null;
      if (badge) {
        badge.textContent = 'Idle';
        badge.style.background = 'rgba(100,100,100,0.15)';
        badge.style.color = 'var(--muted)';
      }
      if (tickLabel) tickLabel.textContent = 'Tick: —';
      if (sampleCount) sampleCount.textContent = '— samples';
      if (startBtn) startBtn.disabled = false;
      if (abortBtn) abortBtn.disabled = true;
      updateSettleBar(0, 0);
      updateMeasureBar(0, 0);
      return;
    }

    _currentRun = run;
    if (run.name) {
      setExperimentType(run.name);
    }
    var state = run.state;
    var color = STATE_COLORS[state] || 'var(--muted)';
    var label = STATE_LABELS[state] || state;

    if (badge) {
      badge.textContent = label;
      badge.style.background = color.replace(')', ',0.15)').replace('var(', 'rgba(');
      badge.style.color = color;
    }
    if (tickLabel) tickLabel.textContent = 'Tick: ' + (run.tick != null ? run.tick : '—');
    if (sampleCount) sampleCount.textContent = (run.samples ? run.samples.length : 0) + ' samples';

    if (startBtn) startBtn.disabled = true;
    if (abortBtn) abortBtn.disabled = (state === 'complete' || state === 'aborted');

    // Update progress bars
    var settlePct = 0, measurePct = 0;
    if (run.settle_ticks && run.settle_ticks > 0) {
      settlePct = state === 'measuring' || state === 'complete' || state === 'aborted'
        ? 1.0
        : Math.min(1.0, (run.tick || 0) / run.settle_ticks);
    }
    if (run.measure_ticks && run.measure_ticks > 0) {
      if (state === 'complete' || state === 'aborted') {
        measurePct = 1.0;
      } else if (state === 'measuring') {
        measurePct = Math.min(1.0, Math.max(0, (run.tick || 0) - (run.settle_ticks || 0)) / run.measure_ticks);
      }
    }
    updateSettleBar(settlePct, state === 'settling' ? color : STATE_COLORS.complete);
    updateMeasureBar(measurePct, state === 'measuring' ? color : STATE_COLORS.complete);
  }

  function updateSettleBar(pct, color) {
    var el = q('ep-settle-bar');
    if (el) el.innerHTML = svgProgressBar('ep-settle', pct, color, 'Settling');
  }

  function updateMeasureBar(pct, color) {
    var el = q('ep-measure-bar');
    if (el) el.innerHTML = svgProgressBar('ep-measure', pct, color, 'Measuring');
  }

  // ── Update event log ───────────────────────────────────────────────────
  function updateEventLog(events) {
    var el = q('ep-event-log');
    if (!el) return;
    if (!events || events.length === 0) {
      el.innerHTML = '<div class="ep-no-data">No events</div>';
      return;
    }
    el.innerHTML = events.slice(-20).map(function (e) {
      return '<div class="ep-event">' +
        '<span class="ep-event-tick">[' + e[0] + ']</span>' +
        '<strong>' + e[1] + '</strong>' +
        (e[2] ? ' — ' + e[2] : '') +
        '</div>';
    }).join('');
    el.scrollTop = el.scrollHeight;
  }

  // ── Update param sweep table ──────────────────────────────────────────
  function updateParams(before, after) {
    var tbody = q('ep-params-body');
    if (!tbody) return;
    if (!before && !after) {
      tbody.innerHTML = '<tr><td colspan="3" class="ep-no-data">No sweep data</td></tr>';
      return;
    }
    var keys = Object.keys(Object.assign({}, before || {}, after || {}));
    if (keys.length === 0) {
      tbody.innerHTML = '<tr><td colspan="3" class="ep-no-data">No sweep data</td></tr>';
      return;
    }
    tbody.innerHTML = keys.map(function (k) {
      var bv = before ? before[k] : null;
      var av = after  ? after[k]  : null;
      var bcls = 'param-before';
      var acls = 'param-after';
      if (bv != null && av != null && Math.abs(bv - av) < 1e-9) {
        bcls = 'param-same';
        acls = 'param-same';
      }
      return '<tr>' +
        '<td>' + k + '</td>' +
        '<td class="' + bcls + '">' + fmt(bv) + '</td>' +
        '<td class="' + acls + '">' + fmt(av) + '</td>' +
        '</tr>';
    }).join('');
  }

  // ── Poll for updates ───────────────────────────────────────────────────
  function poll() {
    fetchExperiments().then(function (list) {
      if (list.length > 0) {
        var run = list[0];
        // Fetch detail for full data
        fetchExperimentDetail(run.name).then(function (detail) {
          if (detail) {
            updateState(detail);
            updateEventLog(detail.events);
            updateParams(detail.parameters_before, detail.parameters_after);
          } else {
            updateState(run);
          }
        });
      } else {
        updateState(null);
        updateEventLog([]);
        updateParams(null, null);
      }
    });
  }

  function startPolling() {
    stopPolling();
    poll();
    _pollingTimer = setInterval(poll, POLL_INTERVAL_MS);
  }

  function stopPolling() {
    if (_pollingTimer) {
      clearInterval(_pollingTimer);
      _pollingTimer = null;
    }
  }

  // ── Start experiment ───────────────────────────────────────────────────
  function startExperiment() {
    var name    = q('ep-name')    ? q('ep-name').value    : 'step_response';
    var settle  = parseInt(q('ep-settle')  ? q('ep-settle').value  : '100', 10);
    var measure = parseInt(q('ep-measure') ? q('ep-measure').value : '200', 10);
    var pKey    = q('ep-param-key')  ? q('ep-param-key').value  : '';
    var pVal    = parseFloat(q('ep-param-val') ? q('ep-param-val').value : '0');

    var params = {};
    if (pKey) params[pKey] = pVal;

    var startBtn = q('ep-start-btn');
    if (startBtn) startBtn.disabled = true;

    fetch('/experiments', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        name: name,
        settle_ticks: settle,
        measure_ticks: measure,
        parameters: params,
      }),
    })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        console.log('Experiment started:', data);
        startPolling();
      })
      .catch(function (err) {
        console.error('Failed to start experiment:', err);
        if (startBtn) startBtn.disabled = false;
      });
  }

  // ── Abort experiment ───────────────────────────────────────────────────
  function abortExperiment() {
    if (!_currentRun || !_currentRun.name) return;
    var name = _currentRun.name;
    var abortBtn = q('ep-abort-btn');
    if (abortBtn) abortBtn.disabled = true;

    fetch('/experiments/' + encodeURIComponent(name) + '/abort', {
      method: 'POST',
    })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        console.log('Experiment aborted:', data);
      })
      .catch(function (err) {
        console.error('Failed to abort experiment:', err);
        if (abortBtn) abortBtn.disabled = false;
      });
  }

  // ── Export ─────────────────────────────────────────────────────────────
  window.__PLUGIN_INIT__ = function (api) {
    _api = api;
    api.registerPanel('Experiment Runtime', function (container) {
      container.innerHTML = buildHTML();

      var startBtn = q('ep-start-btn');
      var abortBtn = q('ep-abort-btn');
      if (startBtn) startBtn.addEventListener('click', startExperiment);
      if (abortBtn) abortBtn.addEventListener('click', abortExperiment);

      // Initial state
      updateState(null);
      updateSettleBar(0, STATE_COLORS.idle);
      updateMeasureBar(0, STATE_COLORS.idle);

      // Load contract experiment types into dropdown
      loadContractExperimentTypes();

      // Start polling
      startPolling();
    });
  };

  window.__PLUGIN_DESTROY__ = function () {
    stopPolling();
    _currentRun = null;
    _api = null;
  };

  window.__registerPlugin__('Experiment Runtime', window.__PLUGIN_INIT__, window.__PLUGIN_DESTROY__);

})();
