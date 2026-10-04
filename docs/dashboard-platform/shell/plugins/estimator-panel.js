/**
 * estimator-panel.js — XY / Optical-Flow Bias Estimator Mode Panel
 *
 * Three estimator modes for the OF velocity-bias / XY position path:
 *   Mode 0 FIXED  (default): bias set once at boot, never changes.
 *   Mode 1 EMA:   bias tracked by an EMA with operator-configurable tau.
 *   Mode 2 EKF:   6-state OF position+bias KF (API/ekf_of.c).
 *
 * Mode switch: CMD 0x1E idx=0 (val=0/1/2), DISARMED ONLY.
 *   The mode selector is disabled (with reason) unless arm state is
 *   known-disarmed. Fails closed when arm state is unknown.
 * Tau change:  CMD 0x1E idx=2 (val=seconds), allowed any time.
 * EMA freeze:  CMD 0x1E idx=1 (val=0/1),   allowed any time.
 *
 * Variables read from slot-1 subscribe (DASHBOARD_PANEL_EXTRA_VARS):
 *   g_of_bias_mode        — active mode (0/1/2)
 *   g_of_bias_ema_freeze  — EMA freeze flag
 *   g_of_handheld_test    — handheld test flag (CMD 0x1E idx=3)
 *   g_of_bias_ema_tau_s   — current EMA tau (s)
 *   g_ekf_of_health       — 1=healthy 0=diverged
 *   g_ekf_of_fallback     — 1=fell back to FIXED this flight
 *
 * EKF state read from slot-0 (DASHBOARD_FRAME_A_VARS):
 *   s_ekf.x[0..8]   — 9-state body EKF (vel, accel bias, gyro bias)
 *   s_ekf_of.x[0..5] (NOT subscribed — no position telemetry for OF EKF)
 *
 * Bias estimates (all modes, display only):
 *   s_of_bias_x, s_of_bias_y — current FIXED/EMA bias (streamed via
 *   legacy Frame 0x05 as of.bias_x/y; not in DASHBOARD_FRAME_A_VARS).
 *   If absent, renders "not published" per honesty rules.
 *
 * Raw IMU proxy: always shown so the panel is useful even without EKF.
 */
(function () {
  'use strict';

  /* Command IDs */
  var CMD_ESTIMATOR = 0x1E;   /* OF bias mode / freeze / tau */

  /* Slot-prefix tolerant key lookup — mirrors time-series-panel.js */
  var SLOT_PREFIX_RE = /^slot\d+\./;

  function slotLookup(values, key) {
    if (!values) return null;
    if (values[key] != null) return values[key];
    /* Try without slot prefix */
    var bare = key.replace(SLOT_PREFIX_RE, '');
    if (bare !== key && values[bare] != null) return values[bare];
    /* Try with a slot prefix */
    var ks = Object.keys(values);
    for (var i = 0; i < ks.length; i++) {
      if (ks[i].replace(SLOT_PREFIX_RE, '') === bare) return values[ks[i]];
    }
    return null;
  }

  /* EKF 9-state groups (body-frame IMU EKF from s_ekf) */
  var EKF_GROUPS = [
    {
      label: 'Position (m)',
      keys: ['ekf.pos_x', 'ekf.pos_y', 'ekf.pos_z'],
      axisLabels: ['X', 'Y', 'Z'],
      unit: 'm',
      unpublished: true,
      unpublishedNote: 'Not published — the 9-state body EKF has no position states.',
    },
    {
      label: 'Velocity (m/s)',
      keys: ['ekf.vel_x', 'ekf.vel_y', 'ekf.vel_z'],
      axisLabels: ['X', 'Y', 'Z'],
      unit: 'm/s',
    },
    {
      label: 'Gyro Bias (rad/s)',
      keys: ['ekf.bias_gyro_x', 'ekf.bias_gyro_y', 'ekf.bias_gyro_z'],
      axisLabels: ['X', 'Y', 'Z'],
      unit: 'rad/s',
    },
    {
      label: 'Accel Bias (m/s²)',
      keys: ['ekf.bias_accel_x', 'ekf.bias_accel_y', 'ekf.bias_accel_z'],
      axisLabels: ['X', 'Y', 'Z'],
      unit: 'm/s²',
    },
  ];

  /* Raw IMU proxy groups */
  var RAW_GROUPS = [
    {
      label: 'Raw IMU — Gyro (rad/s)',
      keys: ['c.gyro_x', 'c.gyro_y', 'c.gyro_z'],
      axisLabels: ['X', 'Y', 'Z'],
      fallback: ['Gyro_X_Real', 'Gyro_Y_Real', 'Gyro_Z_Real'],
    },
    {
      /* mg, not m/s²: Acc_*_Real is declared in milli-g at
       * API/bmi088_driver.c:27, and reads ~1012 on Z with the drone level. */
      label: 'Raw IMU — Accel (mg)',
      keys: ['imu.acc_x', 'imu.acc_y', 'imu.acc_z'],
      axisLabels: ['X', 'Y', 'Z'],
      fallback: ['Acc_X_Real', 'Acc_Y_Real', 'Acc_Z_Real'],
    },
    {
      label: 'Raw IMU — ANO Alt (m)',
      keys: ['c.altitude_cm'],
      axisLabels: ['ALT'],
      fallback: ['ano_of.of_alt_cm'],
      scale: 0.01,
    },
  ];

  var COV_KEYS = ['estimator.cov_pxx', 'estimator.cov_pyy', 'estimator.cov_pzz',
                  'estimator.cov_vxvx', 'estimator.cov_vyvy', 'estimator.cov_vzvz'];

  var FILTER_STATUS_LABELS = ['Initializing', 'Active', 'Degraded', 'Failed'];
  var NOT_PUBLISHED = 'n/p';
  var NOT_PUBLISHED_HINT = 'Not published by this build';

  /* ── DOM helpers ──────────────────────────────────────────────────── */
  var UI = null;   /* window.GSUI (ui/ui-kit.js), set at init */
  function q(id) { return document.getElementById(id); }
  function fmtNum(v, dec) {
    if (v == null) return '—';
    dec = (dec === undefined) ? 4 : dec;
    return parseFloat(v).toFixed(dec);
  }
  function fmtCov(v) {
    if (v == null) return '—';
    return parseFloat(v).toExponential(3);
  }
  function getValue(values, keys, fallback) {
    if (!values) return null;
    for (var i = 0; i < keys.length; i++) {
      var v = slotLookup(values, keys[i]);
      if (v != null) return v;
    }
    if (fallback) {
      for (var j = 0; j < fallback.length; j++) {
        var fv = slotLookup(values, fallback[j]);
        if (fv != null) return fv;
      }
    }
    return null;
  }
  /* value cell text + one modifier class (gs-value--np / --warn / --muted), never an inline colour */
  function setValue(el, text, mod) {
    if (!el) return;
    el.textContent = text;
    el.className = 'gs-metric__value' + (mod ? ' ' + mod : '');
  }

  /* ── Build panel HTML (classes from ui/components.css) ────────────── */
  function metric(label, id, text, mod, unit) {
    return '<div class="gs-metric"><span class="gs-metric__label">' + label + '</span>' +
      '<span id="' + id + '" class="gs-metric__value' + (mod ? ' ' + mod : '') + '">' + text + '</span>' +
      (unit ? '<span class="gs-metric__unit">' + unit + '</span>' : '') + '</div>';
  }
  function group(label, cells, extra) {
    return '<div class="est-group"><div class="gs-label">' + label + '</div>' +
      '<div class="gs-metrics">' + cells + '</div>' + (extra || '') + '</div>';
  }
  function control(label, inner) {
    return '<div><div class="gs-label">' + label + '</div><div class="gs-row">' + inner + '</div></div>';
  }

  function buildHTML() {
    var pill = function (id, text) { return UI.pill('stale', text, '', id); };

    var ekfHTML = EKF_GROUPS.map(function (g) {
      var cells = g.axisLabels.map(function (al, ai) {
        return metric(al, 'ekf-' + g.keys[ai].replace(/\./g, '-'), g.unpublished ? NOT_PUBLISHED : 'AWAITING DATA',
          g.unpublished ? 'gs-value--np' : 'gs-value--muted', g.unit);
      }).join('');
      var note = g.unpublished
        ? '<div id="ekf-note-' + g.keys[0].split('.')[1] + '" class="gs-reason">' + g.unpublishedNote + '</div>'
        : '';
      return group(g.label, cells, note);
    }).join('');

    var rawHTML = RAW_GROUPS.map(function (g) {
      return group(g.label, g.axisLabels.map(function (al, ai) {
        return metric(al, 'raw-' + g.keys[ai].replace(/\./g, '-'), 'AWAITING DATA', 'gs-value--muted');
      }).join(''));
    }).join('');

    var covCells = COV_KEYS.map(function (k) {
      return metric(k.split('.').pop(), 'cov-' + k.replace(/\./g, '-'), NOT_PUBLISHED, 'gs-value--np');
    }).join('');

    return [
      '<style>.est-group { margin-bottom: var(--gs-space-3); } .est-tau { width: 70px; }</style>',

      /* ── Estimator mode selector: disabled unless known-disarmed, the reason is shown beside it ── */
      '<div class="gs-section-title">XY Bias Estimator Mode</div>',
      '<div id="est-error" class="gs-error" style="display:none"></div>',
      '<div class="gs-row est-group">',
      '  <button id="est-btn-fixed" class="gs-btn" title="Bias captured at boot, held fixed for entire flight">FIXED</button>',
      '  <button id="est-btn-ema" class="gs-btn" title="Bias tracked by EMA — configurable tau">EMA</button>',
      '  <button id="est-btn-ekf" class="gs-btn" title="6-state KF: joint position+bias estimation. Active mode.">EKF</button>',
      '  <span class="gs-label">Active: <span id="est-mode-readback" class="gs-metric__value">?</span></span>',
      '  <span id="est-mode-reason" class="gs-reason"></span>',
      '</div>',

      /* ── EMA tau, freeze, handheld test (allowed any time) ── */
      '<div class="gs-row est-group">',
      control('EMA tau (s) <i>(larger = slower tracking, less XY pull-back; allowed any time)</i>',
        '<input id="est-tau-input" class="gs-input est-tau" type="number" min="1" max="300" step="1" value="20" ' +
        'title="EMA time constant (seconds). Firmware range: [1, 300] s." />' +
        '<button id="est-btn-tau" class="gs-btn gs-btn--ghost" title="Send tau to firmware">Set</button>' +
        '<span class="gs-label">Firmware: <span id="est-tau-readback" class="gs-metric__value">?</span> s</span>'),
      control('EMA Freeze',
        '<button id="est-btn-freeze" class="gs-btn gs-btn--ghost" title="Toggle EMA freeze (locks bias estimate before maneuvers)">' +
        '<span id="est-freeze-label">UNFREEZE</span></button>' + pill('est-freeze-state', '?')),
      control('Handheld Test',
        '<button id="est-btn-handheld" class="gs-btn gs-btn--ghost" title="Integrate OF position on the ground so the drone can be moved ' +
        'by hand (modes 0 and 2). Hold still when enabling: position is zeroed and the bias frozen. Firmware clears it in flight.">' +
        '<span id="est-handheld-label">ENABLE</span></button>' + pill('est-handheld-state', '?')),
      '</div>',

      /* ── EKF-OF health ── */
      '<div class="gs-row est-group">',
      control('EKF-OF Health', pill('est-ekf-of-health', 'NOT PUBLISHED')),
      control('EKF Fallback', pill('est-ekf-of-fallback', 'NOT PUBLISHED')),
      '</div>',

      /* ── Bias estimates for all three modes ── */
      '<div class="gs-section-title">OF Bias Estimates (all modes, display only)</div>',
      '<div class="est-group"><div class="gs-label">Current bias s_of_bias_x/y — active in Modes 0 (FIXED) and 1 (EMA). ',
      'Not streamed in DASHBOARD_FRAME_A layout — shows NOT PUBLISHED if not received.</div>',
      '<div class="gs-metrics">',
      metric('bias_x (raw)', 'est-bias-x', 'NOT PUBLISHED', 'gs-value--np'),
      metric('bias_y (raw)', 'est-bias-y', 'NOT PUBLISHED', 'gs-value--np'),
      '</div></div>',

      /* ── 9-state body EKF section (shadow mode) ── */
      '<div class="gs-section-title">9-State Body EKF (s_ekf — shadow display only)</div>',
      '<div id="ekf-filter-wrap" class="gs-row est-group">', control('Filter Status', pill('ekf-filter-status', NOT_PUBLISHED_HINT)), '</div>',

      /* Honest disclaimer */
      '<div id="ekf-disclaimer" class="gs-note est-group">',
      '  ⚠ <strong>EKF telemetry not received yet</strong> — the firmware publishes ',
      '  <code>s_ekf</code> in slot 0; below is RAW IMU telemetry (gyro, accel, baro alt) ',
      '  until those frames arrive. Fields marked <code>n/p</code> are not published by this build.',
      '</div>',

      '<div id="ekf-ekf-section">',
      '  <div class="gs-section-title">Body EKF State (s_ekf, mode 2 = EKF is ACTIVE)</div>',
      ekfHTML,
      '</div>',

      /* Raw IMU section */
      '<div class="gs-section-title">Raw IMU (proxy until EKF available)</div>',
      rawHTML,

      /* Covariance */
      '<div class="est-group"><div class="gs-label">Covariance — <em>' + NOT_PUBLISHED_HINT +
      ' — no covariance telemetry is streamed in this build</em></div>',
      '<div class="gs-metrics">', covCells, '</div></div>',
    ].join('');
  }

  /* ── State ───────────────────────────────────────────────────────────── */
  var _api = null;
  var _hasData = false;
  var _lastState = null;
  var _keyLastSeen = {};
  var _tickTimer = null;
  var _currentMode = null;    /* last known mode from telemetry */
  var _freezeState = null;
  var _handheldState = null;    /* last known freeze from telemetry */

  /* ── Commands: every outcome is shown in #est-error (and a toast on failure), never only in the console ── */
  function showCmdError(text) {
    var el = q('est-error');
    if (!el) return;
    el.textContent = text;
    el.style.display = text ? 'block' : 'none';
  }
  function sendCmd(what, fn) {
    fn().then(function (res) {
      if (res && res.error) throw new Error(res.error);
      showCmdError('');
    }).catch(function (e) { showCmdError(UI ? UI.report(what, e) : what + ': ' + e.message); });
  }

  /* Exposed on window: the harness and the agent drive these; the buttons are bound at render */
  window._estSetMode = function (mode) {
    if (!_api) return;
    var arm = typeof _api.getArmState === 'function' ? _api.getArmState() : 'unknown';
    if (arm !== 'disarmed') {
      showCmdError('mode switch not sent: arm state is ' + (typeof arm === 'string' ? arm : 'unknown'));
      return;
    }
    sendCmd('mode switch', typeof _api.gatedCommand === 'function'
      ? function () { return _api.gatedCommand(CMD_ESTIMATOR, 0, mode, ['disarmed']); }
      : function () { return _api.submitCommand(CMD_ESTIMATOR, 0, mode); });
  };

  window._estSetTau = function () {
    if (!_api) return;
    var el = q('est-tau-input');
    if (!el) return;
    var tau = parseFloat(el.value);
    if (isNaN(tau) || tau < 1) tau = 1;
    if (tau > 300) tau = 300;
    el.value = tau;
    if (typeof _api.submitCommand === 'function') {
      sendCmd('tau set', function () { return _api.submitCommand(CMD_ESTIMATOR, 2, tau); });
    }
  };

  window._estToggleFreeze = function () {
    if (!_api) return;
    var newFreeze = (_freezeState === 1) ? 0 : 1;
    if (typeof _api.submitCommand === 'function') {
      sendCmd('freeze toggle', function () { return _api.submitCommand(CMD_ESTIMATOR, 1, newFreeze); });
    }
  };

  window._estToggleHandheld = function () {
    if (!_api) return;
    var v = (_handheldState === 1) ? 0 : 1;
    if (typeof _api.submitCommand === 'function') {
      sendCmd('handheld toggle', function () { return _api.submitCommand(CMD_ESTIMATOR, 3, v); });
    }
  };

  function bindControls() {
    var binds = {
      'est-btn-fixed': function () { window._estSetMode(0); },
      'est-btn-ema': function () { window._estSetMode(1); },
      'est-btn-ekf': function () { window._estSetMode(2); },
      'est-btn-tau': function () { window._estSetTau(); },
      'est-btn-freeze': function () { window._estToggleFreeze(); },
      'est-btn-handheld': function () { window._estToggleHandheld(); }
    };
    Object.keys(binds).forEach(function (id) {
      var el = q(id);
      if (el && typeof el.addEventListener === 'function') el.addEventListener('click', binds[id]);
    });
  }

  /* ── Render helpers ─────────────────────────────────────────────────── */
  function updateModeButtons(mode, armState) {
    /* the shell returns null while the arm state is unknown: fail closed and say so */
    if (armState !== 'armed' && armState !== 'disarmed') armState = 'unknown';
    var reason = armState === 'armed' ? 'Mode switch requires drone to be DISARMED'
      : (armState === 'unknown' ? 'Arm state unknown — mode switch disabled (fail-closed)' : '');

    var names = ['fixed', 'ema', 'ekf'];
    for (var i = 0; i < names.length; i++) {
      var btn = q('est-btn-' + names[i]);
      if (!btn) continue;
      UI.setDisabled(btn, reason);
      btn.className = 'gs-btn' + (mode === i ? ' is-active' : '');
    }
    var why = q('est-mode-reason');
    if (why) why.textContent = reason;
    var rb = q('est-mode-readback');
    if (rb) {
      if (mode === null) setValue(rb, 'NOT PUBLISHED', 'gs-value--np');
      else setValue(rb, ['FIXED', 'EMA', 'EKF'][mode] || ('Mode ' + mode), '');
    }
  }

  function updateFreezeState(freeze) {
    _freezeState = freeze;
    var lbl = q('est-freeze-label');
    if (lbl) lbl.textContent = freeze === 1 ? 'UNFREEZE' : 'FREEZE';
    UI.setPill(q('est-freeze-state'), freeze === null ? 'stale' : (freeze === 1 ? 'warn' : 'ok'),
      freeze === null ? 'NOT PUBLISHED' : (freeze === 1 ? 'FROZEN' : 'RUNNING'));
  }

  function updateHandheldState(h) {
    _handheldState = h;
    var lbl = q('est-handheld-label');
    if (lbl) lbl.textContent = h === 1 ? 'DISABLE' : 'ENABLE';
    UI.setPill(q('est-handheld-state'), h === null ? 'stale' : (h === 1 ? 'warn' : 'ok'),
      h === null ? 'NOT PUBLISHED' : (h === 1 ? 'ACTIVE' : 'OFF'));
  }

  function updateTauReadback(tau) {
    var el = q('est-tau-readback');
    if (tau === null) setValue(el, 'NOT PUBLISHED', 'gs-value--np');
    else setValue(el, parseFloat(tau).toFixed(1), '');
  }

  function updateEkfOfHealth(health, fallback) {
    UI.setPill(q('est-ekf-of-health'), health === null ? 'stale' : (health >= 0.5 ? 'ok' : 'fail'),
      health === null ? 'NOT PUBLISHED' : (health >= 0.5 ? '✓ HEALTHY' : '✗ DIVERGED'));
    UI.setPill(q('est-ekf-of-fallback'), fallback === null ? 'stale' : (fallback >= 0.5 ? 'warn' : 'ok'),
      fallback === null ? 'NOT PUBLISHED' : (fallback >= 0.5 ? '⚠ FELL BACK TO FIXED' : '— none'));
  }

  function ageMod(ageMs) {
    if (ageMs > 30000) return 'gs-value--muted';
    if (ageMs > 2000)  return 'gs-value--warn';
    return '';
  }

  function renderValue(el, v, key, now) {
    if (!el) return;
    var seen = _keyLastSeen[key];
    if (v != null) {
      _keyLastSeen[key] = now;
      setValue(el, fmtNum(v, 4), '');
    } else if (seen) {
      var age = now - seen;
      setValue(el, age > 30000
        ? 'NO DATA (stale ' + (age / 1000).toFixed(0) + 's)'
        : 'STALE (' + (age / 1000).toFixed(1) + 's)', ageMod(age));
    } else {
      setValue(el, 'NOT PUBLISHED', 'gs-value--np');
    }
  }

  /* Estimator symbols span several slots (mode/tau/health live in slot 1),
   * so merge every stream's values; null when no stream has values yet. */
  function mergedValues(state) {
    var streams = state && state.streams;
    var out = null;
    if (!streams) return null;
    Object.keys(streams).forEach(function (sid) {
      var v = streams[sid] && streams[sid].values;
      if (!v) return;
      if (!out) out = {};
      Object.keys(v).forEach(function (k) { out[k] = v[k]; });
    });
    return out;
  }

  /* ── Main render ────────────────────────────────────────────────────── */
  function renderEstimator() {
    if (!_lastState || !_lastState.streams) return;
    var values = mergedValues(_lastState);
    var streamReceived = values ? true : false;
    var now = Date.now();
    var anyUpdate = false;
    var ekfAvailable = false;

    /* Arm state for mode-button enable/disable */
    var armState = 'unknown';
    if (typeof _api === 'object' && _api && typeof _api.getArmState === 'function') {
      armState = _api.getArmState();
    } else if (values) {
      var armV = slotLookup(values, 'DroneStatus.ARM_Status');
      if (armV != null) armState = (parseFloat(armV) === 0) ? 'disarmed' : 'armed';
    }

    /* Mode, freeze, tau from slot-1 (streamed via DASHBOARD_PANEL_EXTRA_VARS) */
    var modeV    = values ? slotLookup(values, 'g_of_bias_mode')       : null;
    var freezeV  = values ? slotLookup(values, 'g_of_bias_ema_freeze') : null;
    var tauV     = values ? slotLookup(values, 'g_of_bias_ema_tau_s')  : null;
    var handV    = values ? slotLookup(values, 'g_of_handheld_test')   : null;
    var healthV  = values ? slotLookup(values, 'g_ekf_of_health')      : null;
    var fallbkV  = values ? slotLookup(values, 'g_ekf_of_fallback')    : null;

    var mode = (modeV != null) ? Math.round(parseFloat(modeV)) : null;
    _currentMode = mode;

    updateModeButtons(mode, armState);
    updateFreezeState((freezeV != null) ? Math.round(parseFloat(freezeV)) : null);
    updateTauReadback((tauV != null) ? parseFloat(tauV) : null);
    updateHandheldState((handV != null) ? Math.round(parseFloat(handV)) : null);
    updateEkfOfHealth(
      (healthV != null) ? parseFloat(healthV) : null,
      (fallbkV != null) ? parseFloat(fallbkV) : null
    );

    /* Bias estimate s_of_bias_x/y — not in dashboard subscribe, show if received */
    var biasX = values ? slotLookup(values, 'of.bias_x') : null;
    var biasY = values ? slotLookup(values, 'of.bias_y') : null;
    /* fallback to legacy frame keys */
    if (biasX == null && values) biasX = slotLookup(values, 's_of_bias_x');
    if (biasY == null && values) biasY = slotLookup(values, 's_of_bias_y');
    if (biasX != null) { setValue(q('est-bias-x'), fmtNum(biasX, 2), ''); anyUpdate = true; }
    if (biasY != null) { setValue(q('est-bias-y'), fmtNum(biasY, 2), ''); anyUpdate = true; }

    /* EKF groups */
    EKF_GROUPS.forEach(function (g) {
      g.axisLabels.forEach(function (al, ai) {
        var k = g.keys[ai];
        var el = q('ekf-' + k.replace(/\./g, '-'));
        if (g.unpublished) { setValue(el, NOT_PUBLISHED, 'gs-value--np'); return; }
        if (!el) return;
        if (!streamReceived) { setValue(el, 'AWAITING DATA', 'gs-value--muted'); return; }
        var v = values ? slotLookup(values, k) : null;
        if (v != null) { ekfAvailable = true; anyUpdate = true; }
        renderValue(el, v, k, now);
      });
    });

    var disclaimer = q('ekf-disclaimer');
    if (disclaimer) disclaimer.style.display = ekfAvailable ? 'none' : '';

    /* Raw IMU groups */
    RAW_GROUPS.forEach(function (g) {
      g.axisLabels.forEach(function (al, ai) {
        var slot0Key = g.keys[ai];
        var el = q('raw-' + slot0Key.replace(/\./g, '-'));
        if (!el) return;
        if (!streamReceived) { setValue(el, 'AWAITING DATA', 'gs-value--muted'); return; }
        var v = getValue(values, [slot0Key], g.fallback);
        if (v != null && g.scale) v = Number(v) * g.scale;
        if (v != null) anyUpdate = true;
        renderValue(el, v, slot0Key, now);
      });
    });

    /* Covariance */
    COV_KEYS.forEach(function (k) {
      var v = values ? slotLookup(values, k) : null;
      setValue(q('cov-' + k.replace(/\./g, '-')), (v != null) ? fmtCov(v) : NOT_PUBLISHED, (v != null) ? '' : 'gs-value--np');
      if (v != null) anyUpdate = true;
    });

    /* Filter status: 1 Active ok, 2 Degraded warn, 3 Failed fail, anything else stale */
    var fsVal = values ? slotLookup(values, 'estimator.filter_status') : null;
    if (fsVal != null) {
      UI.setPill(q('ekf-filter-status'), ({ 1: 'ok', 2: 'warn', 3: 'fail' })[fsVal] || 'stale',
        FILTER_STATUS_LABELS[Math.floor(fsVal)] || ('Status ' + fsVal));
      anyUpdate = true;
    } else {
      UI.setPill(q('ekf-filter-status'), 'stale', NOT_PUBLISHED_HINT);
    }

    if (anyUpdate) _hasData = true;
  }

  function onState(state) {
    if (!state) return;
    _lastState = state;
    var now = Date.now();
    var values = mergedValues(state);
    if (values) {
      EKF_GROUPS.forEach(function (g) {
        g.keys.forEach(function (k) {
          if (slotLookup(values, k) != null) _keyLastSeen[k] = now;
        });
      });
      RAW_GROUPS.forEach(function (g) {
        g.keys.forEach(function (k) {
          if (getValue(values, [k], g.fallback) != null) _keyLastSeen[k] = now;
        });
      });
    }
    renderEstimator();
  }

  /* ── Export ──────────────────────────────────────────────────────────── */
  window.__PLUGIN_INIT__ = function (api) {
    UI = window.GSUI;
    _api = api;
    api.registerPanel('EKF Estimator', function (container) {
      container.innerHTML = buildHTML();
      bindControls();
      renderEstimator();
      api.subscribe(onState);
      if (_tickTimer == null) {
        _tickTimer = setInterval(function () {
          if (_lastState != null) renderEstimator();
        }, 1000);
      }
    });
  };
  window.__PLUGIN_DESTROY__ = function () {
    if (_tickTimer != null) { clearInterval(_tickTimer); _tickTimer = null; }
    _hasData = false;
    _lastState = null;
    _keyLastSeen = {};
    _currentMode = null;
    _freezeState = null;
    _api = null;
    /* Clean up globals */
    delete window._estSetMode;
    delete window._estSetTau;
    delete window._estToggleFreeze;
    delete window._estToggleHandheld;
  };
  window.__registerPlugin__('EKF Estimator', window.__PLUGIN_INIT__, window.__PLUGIN_DESTROY__);

})();
