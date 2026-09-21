/**
 * motor-bench-panel.js — Motor bench testing panel (real CMD 0x16 protocol)
 *
 * Speaks the firmware's actual bench-test protocol (TASK/send_data.c, CMD 0x16
 * = decimal 22):
 *   idx 0 = enable / dead-man heartbeat — val >= 0.5 turns the test ON and
 *           every send pets the watchdog
 *   idx 1 = motor select — 1..4 = M1..M4, 0 = none. Firmware casts to uint8_t,
 *           so only integers are ever sent here.
 *   idx 2 = commanded CCR — firmware clamps to [2000, 4000]
 *
 * 2000 = Motor_PWM_ZERO (BSP/pwm.h) = OFF. CCR is raw PWM counts; no derived
 * 0..1000 scale is shown because none is meaningful here.
 *
 * Safety model — the firmware is the authority, this panel is belt-and-braces:
 *   - DISARMED-only: every send, heartbeats included, goes through
 *     api.gatedCommand(..., ['disarmed']); the host service re-checks arm
 *     state for cmd 0x16 on top of that.
 *   - Dead-man heartbeat: while enabled, (22, 0, 1) is sent every 100 ms.
 *     The stabilizer runs at 200 Hz and exits bench mode after
 *     MOTOR_TEST_DEADMAN_TICKS = 100 ticks = 500 ms without a heartbeat, so
 *     100 ms gives 5x margin. If the page dies outright, the firmware zeroes
 *     the motors within 500 ms regardless of any browser cleanup.
 *   - Never auto-enables: mounting/rendering sends zero commands; enabling is
 *     an explicit operator action every time.
 *   - Enable sequence: motor select, CCR = 2000 (off), then enable LAST — the
 *     operator can never enable into an unknown CCR from a previous session.
 *   - Disable sequence: CCR = 2000 first, then enable = 0.
 *   - The heartbeat stops on: toggle OFF, abort, panel teardown, page hidden,
 *     arm state leaving DISARMED, or any command send failing.
 *
 * Reads RPM feedback from Frame C (state.streams['3'], motor.rpm_0..3 keys).
 */
(function () {
  'use strict';

  var NUM_MOTORS = 4;
  var CMD_ID_MOTOR_BENCH = 22;   // 0x16
  var CMD_ID_ABORT_ALL   = 13;

  // Firmware command-lifecycle feedback (Bug 6): the backend tracks each
  // transaction through SUBMITTED -> ACKNOWLEDGED / APPLIED / REJECTED
  // and exposes results in the /state snapshot as `command_results` (service
  // core.py::_result_to_entry). We correlate the transaction_id the /commands
  // POST returns with those entries so the operator SEES the firmware ack.
  var LIFECYCLE_MAX = 8;
  var _lifecycleHistory = [];  // newest first, bounded
  var _txByTxid = {};           // transaction_id -> lifecycle entry

  // CMD 0x16 index semantics (firmware contract)
  var IDX_ENABLE = 0;   // 1 = bench test ON (each send pets the dead-man)
  var IDX_SELECT = 1;   // 1..4 = M1..M4 (uint8_t cast — integers only)
  var IDX_CCR    = 2;   // commanded CCR, firmware clamps to [CCR_MIN, CCR_MAX]

  var CCR_MIN = 2000;   // = Motor_PWM_ZERO — this is OFF
  var CCR_MAX = 4000;
  var CCR_OFF = 2000;

  // Firmware dead-man: 100 stabilizer ticks at 200 Hz = 500 ms. Heartbeat at
  // 10 Hz = every 100 ms → 5x margin.
  var HEARTBEAT_MS = 100;

  var MOTOR_COLORS = [
    '#4a9eff',  // Motor 1 - blue
    '#4ecca3',  // Motor 2 - teal
    '#f5a623',  // Motor 3 - amber
    '#e94560',  // Motor 4 - red
  ];

  // ── DOM helpers ─────────────────────────────────────────────────────────
  function q(id) { return document.getElementById(id); }

  function fmt(v, decimals) {
    if (v == null) return '—';
    return parseFloat(v).toFixed(decimals || 0);
  }

  // ── State ───────────────────────────────────────────────────────────────
  var _motorFeedback = [null, null, null, null];
  var _api = null;
  var _enabled = false;
  var _selectedMotor = 1;
  var _ccr = CCR_OFF;
  var _heartbeatTimer = null;

  function idxLabel(idx) {
    return idx === 0 ? 'enable' : (idx === 1 ? 'select' : (idx === 2 ? 'CCR' : 'idx' + idx));
  }
  function statusFromResult(status) {
    if (status === 'ack') return 'ACKNOWLEDGED';
    if (status === 'applied') return 'APPLIED';
    if (status === 'rejected') return 'REJECTED';
    return 'SUBMITTED';
  }
  function updateLifecycleUI() {
    var el = q('mb-lifecycle');
    if (!el) return;
    if (!_lifecycleHistory.length) {
      el.innerHTML = '<div class="mb-waiting">No bench commands sent yet</div>';
      return;
    }
    var rows = _lifecycleHistory.map(function (e) {
      var cls = e.status === 'REJECTED' ? ' mb-lc-rejected'
        : ((e.status === 'ACKNOWLEDGED') || (e.status === 'APPLIED') ? ' mb-lc-ok' : '');
      var extra = (e.status === 'REJECTED' && e.reason) ? ' (' + e.reason + ')' : '';
      return '<div class="mb-lc-row' + cls + '">' +
        '<span>' + idxLabel(e.idx) + ' ' + String(e.val) + '</span>' +
        '<span class="mb-lc-tx">tx ' + (e.txid != null ? e.txid : 'pending') + '</span>' +
        '<span class="mb-lc-state">' + e.status + extra + '</span>' +
        '</div>';
    }).join('');
    el.innerHTML = rows;
  }
  // Record a just-issued command at SUBMITTED (visible immediately on the
  // click — the key "something happened" feedback) and remember its txid once
  // the /commands POST answers, so the next /state tick can show the ack.
  function recordCommand(idx, val, txid) {
    var entry = { idx: idx, val: val, txid: txid, status: 'SUBMITTED',
      reason: '', detail: '', time: Date.now() };
    if (txid != null) _txByTxid[txid] = entry;
    _lifecycleHistory.unshift(entry);
    if (_lifecycleHistory.length > LIFECYCLE_MAX) {
      var evicted = _lifecycleHistory.pop();
      if (evicted.txid != null) delete _txByTxid[evicted.txid];
    }
    updateLifecycleUI();
  }
  // Correlate firmware results (from the /state `command_results` list) with
  // the transactions we issued and advance their lifecycle.
  function applyCommandResults(state) {
    if (!state || !Array.isArray(state.command_results)) return;
    var changed = false;
    for (var i = 0; i < state.command_results.length; i++) {
      var r = state.command_results[i];
      var txid = r == null ? null : r.transaction_id;
      if (txid == null) continue;
      var entry = _txByTxid[txid];
      if (!entry) continue;
      var next = statusFromResult(r.status);
      if (next !== entry.status) {
        entry.status = next;
        entry.reason = r.reason || '';
        entry.detail = r.detail || '';
        changed = true;
      }
    }
    if (changed) updateLifecycleUI();
  }

  // ── Command send (always gated, heartbeats included) ───────────────────
  function sendBench(api, idx, val) {
    if (typeof api.isDisarmed === 'function' && !api.isDisarmed()) {
      var err = new Error('Motor bench commands rejected: drone is not disarmed');
      console.error(err.message);
      return Promise.reject(err);
    }
    var promise;
    if (typeof api.gatedCommand === 'function') {
      promise = api.gatedCommand(CMD_ID_MOTOR_BENCH, idx, val, ['disarmed']);
    } else {
      promise = api.submitCommand(CMD_ID_MOTOR_BENCH, idx, val);
    }
    // Record SUBMITTED immediately; attach the txid when the POST answers.
    recordCommand(idx, val, null);
    promise.then(function (data) {
      var txid = data && (data.transaction_id != null ? data.transaction_id : data.t);
      if (txid != null) {
        for (var i = 0; i < _lifecycleHistory.length; i++) {
          var e = _lifecycleHistory[i];
          if (e.idx === idx && e.val === val && e.status === 'SUBMITTED' && e.txid == null) {
            e.txid = txid;
            _txByTxid[txid] = e;
            break;
          }
        }
        updateLifecycleUI();
      }
    }).catch(function () { /* send failed: SUBMITTED entry simply never resolves */ });
    return promise;
  }

  // ── Heartbeat ───────────────────────────────────────────────────────────
  function stopHeartbeat() {
    if (_heartbeatTimer !== null) {
      clearInterval(_heartbeatTimer);
      _heartbeatTimer = null;
    }
  }

  function startHeartbeat(api) {
    stopHeartbeat();
    _heartbeatTimer = setInterval(function () {
      sendBench(api, IDX_ENABLE, 1).catch(function (err) {
        console.error('Motor bench heartbeat failed — stopping heartbeat:', err);
        stopHeartbeat();
        _enabled = false;
        updateEnableUI();
      });
    }, HEARTBEAT_MS);
  }

  // ── Enable / disable sequences ───────────────────────────────────────────
  function enableBench(api) {
    // Select and CCR = off first, enable LAST, so we can never enable into a
    // stale CCR from a previous session.
    sendBench(api, IDX_SELECT, _selectedMotor)
      .then(function () { return sendBench(api, IDX_CCR, CCR_OFF); })
      .then(function () { return sendBench(api, IDX_ENABLE, 1); })
      .then(function () {
        // The motor comes up at CCR = off; reflect that in the control.
        _ccr = CCR_OFF;
        updateCcrUI();
        _enabled = true;
        updateEnableUI();
        startHeartbeat(api);
      })
      .catch(function (err) {
        console.error('Motor bench enable failed:', err);
        stopHeartbeat();
        _enabled = false;
        updateEnableUI();
      });
  }

  function disableBench(api) {
    // Stop the heartbeat first, then CCR = off, then enable = 0.
    stopHeartbeat();
    _enabled = false;
    updateEnableUI();
    return sendBench(api, IDX_CCR, CCR_OFF)
      .then(function () { return sendBench(api, IDX_ENABLE, 0); })
      .catch(function (err) {
        console.error('Motor bench disable failed:', err);
      });
  }

  function forceShutdown(reason) {
    console.warn('Motor bench forcing shutdown: ' + reason);
    if (_enabled && _api) {
      disableBench(_api);
    } else {
      stopHeartbeat();
      _enabled = false;
      updateEnableUI();
    }
  }

  // ── Emergency stop ────────────────────────────────────────────────────────
  function emergencyStop(api) {
    console.log('EMERGENCY STOP pressed');
    stopHeartbeat();
    _enabled = false;
    updateEnableUI();
    api.submitCommand(CMD_ID_ABORT_ALL, 0, 0)
      .then(function () {
        console.log('Abort all sent');
        alert('Emergency stop executed. All motors zeroed.');
      })
      .catch(function (err) {
        console.error('Emergency stop failed:', err);
        alert('Emergency stop executed (partial). Check system state.');
      });
  }

  // ── UI updates ───────────────────────────────────────────────────────────
  function updateEnableUI() {
    var onBtn = q('mb-enable-on');
    var offBtn = q('mb-enable-off');
    var statusEl = q('mb-hb-status');
    var badge = q('mb-bench-indicator');
    if (onBtn) onBtn.disabled = _enabled;
    if (offBtn) offBtn.disabled = !_enabled;
    if (statusEl) {
      statusEl.textContent = _enabled
        ? 'bench test ON — heartbeat 10 Hz'
        : 'bench test OFF';
    }
    if (badge) badge.className = 'mb-bench-indicator' + (_enabled ? ' mb-bench-on' : '');
  }

  function updateCcrUI() {
    var slider = q('mb-ccr');
    var val = q('mb-ccr-val');
    if (slider) slider.value = _ccr;
    if (val) val.textContent = _ccr;
  }

  function updateArmUI(arm) {
    var el = q('mb-arm-badge');
    if (!el) return;
    if (arm === 'disarmed') {
      el.textContent = 'DISARMED';
      el.className = 'mb-arm mb-arm-ok';
    } else if (arm === 'armed') {
      el.textContent = 'ARMED';
      el.className = 'mb-arm mb-arm-bad';
    } else {
      el.textContent = 'ARM unknown';
      el.className = 'mb-arm mb-arm-unknown';
    }
  }

  function updateFeedbackDisplay() {
    var area = q('mb-feedback-area');
    if (!area) return;

    var hasAny = _motorFeedback.some(function (v) { return v != null; });
    if (!hasAny) {
      area.innerHTML = '<div class="mb-waiting">Waiting for telemetry feedback…</div>';
      return;
    }

    var rows = _motorFeedback.map(function (v, i) {
      return '<div class="mb-feedback-row">' +
        '<span style="color:' + MOTOR_COLORS[i] + '">Motor ' + (i + 1) + '</span>' +
        '<span>' + (v != null ? fmt(v, 0) + ' RPM' : '—') + '</span>' +
        '</div>';
    }).join('');

    area.innerHTML = '<div style="display:flex;gap:12px;flex-wrap:wrap">' + rows + '</div>';
  }

  // ── Build initial DOM ───────────────────────────────────────────────────
  function buildHTML() {
    var radios = [];
    for (var i = 0; i < NUM_MOTORS; i++) {
      var motorNum = i + 1;
      radios.push([
        '<label style="display:inline-flex;align-items:center;gap:4px;',
        '  cursor:pointer;font-size:13px;font-weight:600;',
        '  color:' + MOTOR_COLORS[i] + ';padding:4px 8px">',
        '  <input type="radio" name="mb-motor" id="mb-mot-' + motorNum + '"',
        '    value="' + motorNum + '"' + (motorNum === 1 ? ' checked' : '') +
        '    style="accent-color:' + MOTOR_COLORS[i] + '"/>',
        '  M' + motorNum,
        '</label>',
      ].join(''));
    }

    return [
      '<style>',
      '.mb-section { margin-bottom: 14px; }',
      '.mb-section:last-child { margin-bottom: 0; }',
      '.mb-label { font-size: 11px; color: var(--muted); margin-bottom: 6px; }',
      '.mb-bench-indicator {',
      '  display: inline-flex; align-items: center; gap: 6px;',
      '  padding: 6px 12px; border-radius: 20px; font-size: 12px; font-weight: 700;',
      '  background: rgba(255,255,255,0.06); color: var(--muted);',
      '}',
      '.mb-bench-on { background: rgba(245,166,35,0.15); color: var(--amber); }',
      '.mb-btn { transition: opacity 0.15s; cursor: pointer; }',
      '.mb-btn:hover { opacity: 0.8; }',
      '.mb-btn:disabled { opacity: 0.4; cursor: not-allowed; }',
      '.mb-toggle {',
      '  padding: 5px 16px; border-radius: 4px; font-size: 12px; font-weight: 700;',
      '  border: 1px solid var(--border); background: rgba(255,255,255,0.06);',
      '  color: var(--text);',
      '}',
      '.mb-toggle-on:not(:disabled) {',
      '  background: var(--amber); border-color: var(--amber); color: #fff;',
      '}',
      '.mb-btn-danger {',
      '  padding: 8px 16px; border-radius: 4px; font-size: 13px; font-weight: 700;',
      '  background: var(--red); border: none; color: #fff;',
      '  cursor: pointer; width: 100%; margin-top: 8px;',
      '}',
      '.mb-btn-danger:hover { opacity: 0.85; }',
      '.mb-arm { font-size: 10px; font-weight: 700; padding: 2px 8px; border-radius: 10px; }',
      '.mb-arm-ok { background: rgba(78,204,163,0.15); color: var(--green); }',
      '.mb-arm-bad { background: rgba(233,69,96,0.2); color: var(--red); }',
      '.mb-arm-unknown { background: rgba(255,255,255,0.06); color: var(--muted); }',
      '.mb-ccr-tick {',
      '  font-family: Consolas, monospace; font-size: 11px; color: var(--muted);',
      '}',
      '.mb-ccr-val {',
      '  font-family: Consolas, monospace; font-size: 14px; font-weight: 600;',
      '  color: var(--amber);',
      '}',
      '.mb-warning {',
      '  display: flex; align-items: center; gap: 6px; padding: 6px 10px;',
      '  border-radius: 4px; font-size: 12px; font-weight: 600;',
      '  background: rgba(245,166,35,0.12); color: var(--amber);',
      '  margin-top: 8px;',
      '}',
      '.mb-warning-crit { background: rgba(233,69,96,0.12); color: var(--red); }',
      '.mb-info { font-size: 11px; color: var(--muted); margin-top: 4px; }',
      '.mb-hb-status { font-size: 11px; font-weight: 600; color: var(--muted); }',
      '.mb-lifecycle { font-family: Consolas, monospace; font-size: 11px;',
      '  background: var(--bg); border: 1px solid var(--border); border-radius: 4px;',
      '  padding: 6px 8px; }',
      '.mb-lc-row { display: flex; justify-content: space-between; gap: 10px;',
      '  padding: 2px 0; color: var(--muted); }',
      '.mb-lc-row.mb-lc-ok { color: var(--green); }',
      '.mb-lc-row.mb-lc-rejected { color: var(--red); }',
      '.mb-lc-tx { color: var(--muted); }',
      '.mb-lc-state { font-weight: 600; }',
      '.mb-feedback {',
      '  background: var(--bg); border: 1px solid var(--border);',
      '  border-radius: 4px; padding: 6px 8px;',
      '  font-family: Consolas, monospace; font-size: 11px;',
      '}',
      '.mb-feedback-row { display: flex; justify-content: space-between; padding: 2px 0; }',
      '.mb-waiting {',
      '  display: flex; align-items: center; gap: 8px; padding: 10px;',
      '  border-radius: 4px; background: rgba(255,255,255,0.03);',
      '  font-size: 12px; color: var(--muted);',
      '}',
      'input[type="range"] { cursor: pointer; }',
      '</style>',

      /* Header: arm state badge */
      '<div class="mb-section">',
      '  <div style="display:flex;align-items:center;justify-content:space-between">',
      '    <div class="mb-label">Motor Bench Test</div>',
      '    <span id="mb-arm-badge" class="mb-arm mb-arm-unknown">ARM unknown</span>',
      '  </div>',
      '  <div id="mb-bench-indicator" class="mb-bench-indicator">&#9888; Bench test inactive</div>',
      '  <div class="mb-info">Bench testing only. Do not fly.</div>',
      '</div>',

      /* Safety warning */
      '<div class="mb-section">',
      '  <div class="mb-warning mb-warning-crit">',
      '    &#9888; Propellers must be removed or guarded before testing',
      '  </div>',
      '</div>',

      /* Enable toggle (idx 0) with dead-man heartbeat */
      '<div class="mb-section">',
      '  <div class="mb-label">Enable bench test (idx 0 — dead-man heartbeat)</div>',
      '  <div style="display:flex;align-items:center;gap:8px">',
      '    <button id="mb-enable-off" class="mb-btn mb-toggle" disabled>OFF</button>',
      '    <button id="mb-enable-on" class="mb-btn mb-toggle mb-toggle-on">ON</button>',
      '    <span id="mb-hb-status" class="mb-hb-status">bench test OFF</span>',
      '  </div>',
      '  <div class="mb-info">Heartbeat 10 Hz while ON. Firmware dead-man zeroes motors',
      '    500 ms after the last heartbeat.</div>',
      '</div>',

      /* Firmware ack / command lifecycle (Bug 6) */
      '<div class="mb-section">',
      '  <div class="mb-label">Firmware ack / command lifecycle</div>',
      '  <div id="mb-lifecycle" class="mb-lifecycle">',
      '    <div class="mb-waiting">No bench commands sent yet</div>',
      '  </div>',
      '  <div class="mb-info">Most recent first. SUBMITTED = queued locally;',
      '    ACKNOWLEDGED then APPLIED = the FC worked the command; REJECTED = blocked.',
      '    In DISARMED this confirms the FC is hearing the panel.</div>',
      '</div>',

      /* Motor select (idx 1) */
      '<div class="mb-section">',
      '  <div class="mb-label">Motor select (idx 1)</div>',
      '  <div style="display:flex;gap:4px;flex-wrap:wrap">',
      radios.join(''),
      '  </div>',
      '  <div class="mb-info">Only the selected motor is driven; the other three stay at',
      '    CCR 2000 (off).</div>',
      '</div>',

      /* CCR slider (idx 2) — raw PWM counts */
      '<div class="mb-section">',
      '  <div class="mb-label">CCR (idx 2) — PWM counts</div>',
      '  <div style="display:flex;align-items:center;gap:8px">',
      '    <span class="mb-ccr-tick">2000</span>',
      '    <input id="mb-ccr" type="range" min="2000" max="4000" step="10" value="2000"',
      '      style="flex:1"/>',
      '    <span class="mb-ccr-tick">4000</span>',
      '  </div>',
      '  <div class="mb-info">2000 = off (Motor_PWM_ZERO) &nbsp;·&nbsp; firmware clamps to',
      '    [2000, 4000]</div>',
      '  <div class="mb-info">Commanded CCR: <span id="mb-ccr-val" class="mb-ccr-val">2000</span>',
      '    PWM counts</div>',
      '</div>',

      /* RPM feedback */
      '<div class="mb-section">',
      '  <div class="mb-label">RPM Feedback (Frame C)</div>',
      '  <div id="mb-feedback-area" class="mb-feedback">',
      '    <div class="mb-waiting">Waiting for telemetry feedback…</div>',
      '  </div>',
      '</div>',

      /* Abort */
      '<div class="mb-section">',
      '  <button id="mb-estop" class="mb-btn-danger">&#9632; ABORT ALL</button>',
      '</div>',
    ].join('');
  }

  // ── State handler — RPM feedback + arm-state watch ───────────────────────
  function onState(state) {
    if (!state) return;

    // Bug 6: surface the firmware ack. Correlate /state command_results with
    // the transactions we issued and show SUBMITTED -> ACKNOWLEDGED / APPLIED
    // / REJECTED. stream-feedback parsing below handles the rest.
    applyCommandResults(state);

    if (!state.streams) return;

    // Read RPM feedback from Frame C (tag "c" -> slot 3). The bridge expands
    // the wire rpm[4] array to the motor.rpm_0..3 scalar keys below.
    var slot3 = state.streams['3'];
    if (slot3 && slot3.values) {
      var vals = slot3.values;
      // Order: most-specific (motor.rpm_*) first, fall back to broader patterns.
      var keys = [
        'motor.rpm_0', 'motor.rpm_1', 'motor.rpm_2', 'motor.rpm_3',
        'rpm.mot0', 'rpm.mot1', 'rpm.mot2', 'rpm.mot3',
        'motor.motor_rpm_0', 'motor.motor_rpm_1', 'motor.motor_rpm_2', 'motor.motor_rpm_3',
      ];

      keys.forEach(function (key, ki) {
        var v = vals[key];
        if (v != null) {
          var idx = ki < 4 ? ki : (ki < 8 ? ki - 4 : ki - 8);
          _motorFeedback[idx] = v;
        }
      });

      // If no Frame C RPM keys were found, leave _motorFeedback alone
      // (do NOT fall back to slot0.ch0.20-23 — those channels don't exist
      //  per the telemetry spec; only ch0-ch14 are valid).
    }

    updateFeedbackDisplay();

    // Arm-state watch: if the drone leaves DISARMED while the bench test is
    // enabled, stop the heartbeat and drop to disabled. The firmware
    // independently zeroes motors on leaving DISARMED; this is belt-and-braces.
    var arm = null;
    if (_api && typeof _api.getArmState === 'function') {
      arm = _api.getArmState();
    }
    if (arm == null) {
      var anyArmed = null;
      Object.keys(state.streams).forEach(function (slot) {
        var s = state.streams[slot];
        var v = s && s.values && s.values['status.arm'];
        if (v != null) anyArmed = (v !== 0);
      });
      if (anyArmed !== null) arm = anyArmed ? 'armed' : 'disarmed';
    }
    updateArmUI(arm);
    if (arm === 'armed' && _enabled) {
      forceShutdown('ARM state left DISARMED');
    }
  }

  // ── Export ─────────────────────────────────────────────────────────────
  window.__PLUGIN_INIT__ = function (api) {
    _api = api;
    api.registerPanel('Motor Bench', function (container) {
      container.innerHTML = buildHTML();

      // Enable / disable toggle (idx 0)
      var onBtn = q('mb-enable-on');
      var offBtn = q('mb-enable-off');
      if (onBtn) onBtn.addEventListener('click', function () { enableBench(api); });
      if (offBtn) offBtn.addEventListener('click', function () { disableBench(api); });

      // Motor select (idx 1) — integers only, ever
      for (var i = 1; i <= NUM_MOTORS; i++) {
        (function (motorNum) {
          var radio = q('mb-mot-' + motorNum);
          if (!radio) return;
          radio.addEventListener('change', function () {
            if (!this.checked) return;
            _selectedMotor = parseInt(this.value, 10);
            sendBench(api, IDX_SELECT, _selectedMotor).catch(function (err) {
              console.error('Motor select failed:', err);
            });
          });
        })(i);
      }

      // CCR slider (idx 2) — raw PWM counts
      var ccrSlider = q('mb-ccr');
      if (ccrSlider) {
        ccrSlider.addEventListener('input', function () {
          _ccr = parseInt(this.value, 10);
          updateCcrUI();
        });
        ccrSlider.addEventListener('change', function () {
          sendBench(api, IDX_CCR, _ccr).catch(function (err) {
            console.error('CCR send failed:', err);
          });
        });
      }

      // Abort
      var estopBtn = q('mb-estop');
      if (estopBtn) estopBtn.addEventListener('click', function () { emergencyStop(api); });

      // Subscribe to telemetry for RPM feedback and arm-state watch
      api.subscribe(onState);
    });

    // Page hidden → stop the heartbeat and best-effort disable. Never
    // re-enables on return; the operator must click ON again. The 500 ms
    // firmware dead-man is the actual guarantee if the page never comes back.
    if (typeof document !== 'undefined' && document.addEventListener) {
      document.addEventListener('visibilitychange', function () {
        if (typeof document !== 'undefined' && document.hidden && _enabled) {
          forceShutdown('page hidden');
        }
      });
    }
  };

  window.__PLUGIN_DESTROY__ = function () {
    // Best-effort off: CCR = off first, then enable = 0. Belt-and-braces only —
    // the firmware dead-man zeroes the motors within 500 ms of the last
    // heartbeat regardless of whether this code ever runs.
    var apiAtTearDown = _api;
    if (apiAtTearDown && _enabled) {
      try {
        sendBench(apiAtTearDown, IDX_CCR, CCR_OFF)
          .then(function () { return sendBench(apiAtTearDown, IDX_ENABLE, 0); })
          .catch(function () { /* best-effort only */ });
      } catch (e) { /* best-effort only */ }
    }
    stopHeartbeat();
    _enabled = false;
    _motorFeedback = [null, null, null, null];
    _api = null;
  };

  window.__registerPlugin__('Motor Bench', window.__PLUGIN_INIT__, window.__PLUGIN_DESTROY__);

})();
