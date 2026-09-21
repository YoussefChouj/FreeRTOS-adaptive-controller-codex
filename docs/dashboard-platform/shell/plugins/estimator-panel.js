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
      label: 'Raw IMU — Accel (m/s²)',
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

  /* ── Build panel HTML ─────────────────────────────────────────────── */
  function buildHTML() {
    /* EKF group rows */
    var ekfHTML = EKF_GROUPS.map(function (g) {
      var cells = g.axisLabels.map(function (al, ai) {
        var initText = g.unpublished ? NOT_PUBLISHED : 'AWAITING DATA';
        var initColor = g.unpublished ? 'var(--amber)' : 'var(--muted)';
        return '<div style="display:flex;flex-direction:column;align-items:center;flex:1;min-width:54px">' +
          '<span style="font-size:10px;color:var(--muted)">' + al + '</span>' +
          '<span id="ekf-' + g.keys[ai].replace(/\./g, '-') + '" style="font-family:Consolas,monospace;font-size:13px;color:' + initColor + '">' + initText + '</span>' +
          '<span style="font-size:9px;color:var(--muted)">' + g.unit + '</span>' +
          '</div>';
      }).join('');
      var note = g.unpublished
        ? '<div id="ekf-note-' + g.keys[0].split('.')[1] + '" class="ekf-unpublished-note">' + g.unpublishedNote + '</div>'
        : '';
      return '<div style="margin-bottom:12px">' +
        '<div style="font-size:11px;color:var(--muted);margin-bottom:4px">' + g.label + '</div>' +
        '<div style="display:flex;gap:8px">' + cells + '</div>' + note +
        '</div>';
    }).join('');

    /* Raw IMU proxy rows */
    var rawHTML = RAW_GROUPS.map(function (g) {
      var cells = g.axisLabels.map(function (al, ai) {
        var key = g.keys[ai].replace(/\./g, '-');
        return '<div style="display:flex;flex-direction:column;align-items:center;flex:1;min-width:54px">' +
          '<span style="font-size:10px;color:var(--muted)">' + al + '</span>' +
          '<span id="raw-' + key + '" style="font-family:Consolas,monospace;font-size:13px;color:var(--muted)">AWAITING DATA</span>' +
          '</div>';
      }).join('');
      return '<div style="margin-bottom:10px">' +
        '<div style="font-size:11px;color:var(--muted);margin-bottom:4px">' + g.label + '</div>' +
        '<div style="display:flex;gap:8px">' + cells + '</div>' +
        '</div>';
    }).join('');

    /* Covariance cells */
    var covCells = COV_KEYS.map(function (k) {
      var key = k.replace(/\./g, '-');
      return '<div style="display:flex;flex-direction:column;align-items:center;flex:1;min-width:64px">' +
        '<span style="font-size:9px;color:var(--muted)">' + k.split('.').pop() + '</span>' +
        '<span id="cov-' + key + '" style="font-family:Consolas,monospace;font-size:10px;color:var(--amber)">' + NOT_PUBLISHED + '</span>' +
        '</div>';
    }).join('');

    return [
      '<style>',
      '.est-mode-btn { padding:4px 14px; border-radius:4px; border:1px solid var(--border);',
      '  background:transparent; color:var(--muted); cursor:pointer; font-size:11px;',
      '  font-weight:600; letter-spacing:0.04em; transition:all 0.15s; }',
      '.est-mode-btn:hover:not(:disabled) { border-color:var(--accent); color:var(--accent); }',
      '.est-mode-btn.active { background:var(--accent); color:#fff; border-color:var(--accent); }',
      '.est-mode-btn:disabled { opacity:0.45; cursor:not-allowed; }',
      '.est-health-ok  { color:var(--green);  font-weight:700; }',
      '.est-health-bad { color:var(--red);    font-weight:700; }',
      '.est-health-unk { color:var(--amber);  font-weight:700; }',
      '.ekf-no-data { color: var(--muted); font-size: 12px; text-align: center; padding: 20px; }',
      '.ekf-filter { display: inline-flex; align-items: center; gap: 6px; padding: 3px 10px;',
      '  border-radius: 12px; font-size: 11px; font-weight: 600; }',
      '.ekf-filter-ok    { background: rgba(78,204,163,0.15); color: var(--green); }',
      '.ekf-filter-warn  { background: rgba(245,166,35,0.15); color: var(--amber); }',
      '.ekf-filter-err   { background: rgba(233,69,96,0.15);  color: var(--red); }',
      '.ekf-filter-unknown { background: rgba(136,136,170,0.1); color: var(--muted); }',
      '.ekf-disclaimer {',
      '  padding: 10px 12px; background: rgba(245,166,35,0.10);',
      '  border: 1px solid var(--amber); border-radius: 4px;',
      '  color: var(--amber); font-size: 11px; margin-bottom: 12px; font-weight: 600;',
      '}',
      '.ekf-unpublished-note {',
      '  margin-top: 4px; font-size: 10px; color: var(--amber); font-style: italic;',
      '}',
      '.ekf-section-title {',
      '  font-size: 10px; font-weight: 700; color: var(--muted);',
      '  letter-spacing: 0.06em; text-transform: uppercase;',
      '  margin: 14px 0 8px 0; padding-bottom: 4px;',
      '  border-bottom: 1px solid var(--border);',
      '}',
      '.est-tau-input { width:70px; padding:3px 6px; border:1px solid var(--border);',
      '  border-radius:4px; background:var(--bg); color:var(--text); font-size:12px; }',
      '</style>',

      /* ── Estimator mode selector ── */
      '<div class="ekf-section-title">XY Bias Estimator Mode</div>',
      '<div style="margin-bottom:10px">',
      '  <div id="est-arm-warn" style="display:none;margin-bottom:6px;padding:6px 10px;',
      '       background:rgba(233,69,96,0.10);border:1px solid var(--red);border-radius:4px;',
      '       font-size:11px;color:var(--red);font-weight:600">',
      '    ⚠ Mode switch requires drone to be DISARMED',
      '  </div>',
      '  <div id="est-unknown-warn" style="display:none;margin-bottom:6px;padding:6px 10px;',
      '       background:rgba(245,166,35,0.10);border:1px solid var(--amber);border-radius:4px;',
      '       font-size:11px;color:var(--amber);font-weight:600">',
      '    ⚠ Arm state unknown — mode switch disabled (fail-closed)',
      '  </div>',
      '  <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">',
      '    <button id="est-btn-fixed" class="est-mode-btn" onclick="window._estSetMode(0)" title="Bias captured at boot, held fixed for entire flight">FIXED</button>',
      '    <button id="est-btn-ema"   class="est-mode-btn" onclick="window._estSetMode(1)" title="Bias tracked by EMA — configurable tau">EMA</button>',
      '    <button id="est-btn-ekf"   class="est-mode-btn" onclick="window._estSetMode(2)" title="6-state KF: joint position+bias estimation. Active mode.">EKF</button>',
      '    <span style="font-size:11px;color:var(--muted);margin-left:8px">Active:',
      '      <span id="est-mode-readback" style="font-family:Consolas,monospace;font-weight:600">?</span>',
      '    </span>',
      '  </div>',
      '</div>',

      /* ── EMA tau input ── */
      '<div style="margin-bottom:12px;display:flex;align-items:center;gap:10px;flex-wrap:wrap">',
      '  <div>',
      '    <div style="font-size:11px;color:var(--muted);margin-bottom:4px">EMA tau (s)',
      '      <span style="font-size:10px;font-style:italic">(larger = slower tracking, less XY pull-back; allowed any time)</span>',
      '    </div>',
      '    <div style="display:flex;gap:6px;align-items:center">',
      '      <input id="est-tau-input" class="est-tau-input" type="number" min="1" max="300" step="1" value="20" title="EMA time constant (seconds). Firmware range: [1, 300] s." />',
      '      <button style="padding:3px 10px;border-radius:4px;border:1px solid var(--border);',
      '              background:transparent;color:var(--muted);cursor:pointer;font-size:11px"',
      '              onclick="window._estSetTau()" title="Send tau to firmware">Set</button>',
      '      <span style="font-size:11px;color:var(--muted)">Firmware: <span id="est-tau-readback" style="font-family:Consolas,monospace">?</span> s</span>',
      '    </div>',
      '  </div>',
      '  <div style="margin-left:auto">',
      '    <div style="font-size:11px;color:var(--muted);margin-bottom:4px">EMA Freeze</div>',
      '    <div style="display:flex;gap:6px;align-items:center">',
      '      <button id="est-btn-freeze" style="padding:3px 10px;border-radius:4px;border:1px solid var(--border);',
      '              background:transparent;color:var(--muted);cursor:pointer;font-size:11px;font-weight:600"',
      '              onclick="window._estToggleFreeze()" title="Toggle EMA freeze (locks bias estimate before maneuvers)">',
      '        <span id="est-freeze-label">UNFREEZE</span>',
      '      </button>',
      '      <span id="est-freeze-state" class="est-health-unk">?</span>',
      '    </div>',
      '  </div>',
      '</div>',

      /* ── EKF-OF health section ── */
      '<div style="margin-bottom:12px;display:flex;gap:12px;flex-wrap:wrap;align-items:flex-start">',
      '  <div>',
      '    <div style="font-size:11px;color:var(--muted);margin-bottom:4px">EKF-OF Health</div>',
      '    <span id="est-ekf-of-health" class="ekf-filter ekf-filter-unknown">NOT PUBLISHED</span>',
      '  </div>',
      '  <div>',
      '    <div style="font-size:11px;color:var(--muted);margin-bottom:4px">EKF Fallback</div>',
      '    <span id="est-ekf-of-fallback" class="ekf-filter ekf-filter-unknown">NOT PUBLISHED</span>',
      '  </div>',
      '</div>',

      /* ── Bias estimates for all three modes ── */
      '<div class="ekf-section-title">OF Bias Estimates (all modes, display only)</div>',
      '<div style="margin-bottom:12px">',
      '  <div style="font-size:11px;color:var(--muted);margin-bottom:6px">',
      '    Current bias s_of_bias_x/y — active in Modes 0 (FIXED) and 1 (EMA).',
      '    Not streamed in DASHBOARD_FRAME_A layout — shows NOT PUBLISHED if not received.',
      '  </div>',
      '  <div style="display:flex;gap:8px">',
      '    <div style="display:flex;flex-direction:column;align-items:center;flex:1;min-width:54px">',
      '      <span style="font-size:10px;color:var(--muted)">bias_x (raw)</span>',
      '      <span id="est-bias-x" style="font-family:Consolas,monospace;font-size:13px;color:var(--amber)">NOT PUBLISHED</span>',
      '    </div>',
      '    <div style="display:flex;flex-direction:column;align-items:center;flex:1;min-width:54px">',
      '      <span style="font-size:10px;color:var(--muted)">bias_y (raw)</span>',
      '      <span id="est-bias-y" style="font-family:Consolas,monospace;font-size:13px;color:var(--amber)">NOT PUBLISHED</span>',
      '    </div>',
      '  </div>',
      '</div>',

      /* ── 9-state body EKF section (shadow mode) ── */
      '<div class="ekf-section-title">9-State Body EKF (s_ekf — shadow display only)</div>',

      /* Filter status */
      '<div id="ekf-filter-wrap" style="margin-bottom:12px;display:flex;align-items:center;gap:10px">',
      '  <div>',
      '    <div style="font-size:11px;color:var(--muted);margin-bottom:4px">Filter Status</div>',
      '    <span id="ekf-filter-status" class="ekf-filter ekf-filter-unknown">' + NOT_PUBLISHED_HINT + '</span>',
      '  </div>',
      '</div>',

      /* Honest disclaimer */
      '<div id="ekf-disclaimer" class="ekf-disclaimer">',
      '  ⚠ <strong>EKF telemetry not received yet</strong> — the firmware publishes ',
      '  <code>s_ekf</code> in slot 0; below is RAW IMU telemetry (gyro, accel, baro alt) ',
      '  until those frames arrive. Fields marked <code>n/p</code> are not published by this build.',
      '</div>',

      '<div id="ekf-ekf-section">',
      '  <div class="ekf-section-title">Body EKF State (s_ekf, mode 2 = EKF is ACTIVE)</div>',
      ekfHTML,
      '</div>',

      /* Raw IMU section */
      '<div class="ekf-section-title">Raw IMU (proxy until EKF available)</div>',
      rawHTML,

      /* Covariance */
      '<div style="margin-bottom:12px">',
      '  <div style="font-size:11px;color:var(--muted);margin-bottom:4px">Covariance — <em>' + NOT_PUBLISHED_HINT + ' — no covariance telemetry is streamed in this build</em></div>',
      '  <div style="display:flex;gap:8px;flex-wrap:wrap">', covCells, '</div>',
      '</div>',
    ].join('');
  }

  /* ── State ───────────────────────────────────────────────────────────── */
  var _api = null;
  var _hasData = false;
  var _lastState = null;
  var _keyLastSeen = {};
  var _tickTimer = null;
  var _currentMode = null;    /* last known mode from telemetry */
  var _freezeState = null;    /* last known freeze from telemetry */

  /* Expose mode/tau/freeze send functions on window so onclick="" works */
  window._estSetMode = function (mode) {
    if (!_api) return;
    var arm = typeof _api.getArmState === 'function' ? _api.getArmState() : 'unknown';
    if (arm !== 'disarmed') {
      console.warn('Estimator mode switch rejected: arm state is ' + arm);
      return;
    }
    var fn = typeof _api.gatedCommand === 'function'
      ? function () { return _api.gatedCommand(CMD_ESTIMATOR, 0, mode, ['disarmed']); }
      : function () { return _api.submitCommand(CMD_ESTIMATOR, 0, mode); };
    fn().catch(function (e) { console.error('mode switch rejected:', e); });
  };

  window._estSetTau = function () {
    if (!_api) return;
    var el = q('est-tau-input');
    if (!el) return;
    var tau = parseFloat(el.value);
    if (isNaN(tau) || tau < 1) tau = 1;
    if (tau > 300) tau = 300;
    el.value = tau;
    var fn = typeof _api.submitCommand === 'function'
      ? function () { return _api.submitCommand(CMD_ESTIMATOR, 2, tau); }
      : null;
    if (fn) fn().catch(function (e) { console.error('tau set rejected:', e); });
  };

  window._estToggleFreeze = function () {
    if (!_api) return;
    var newFreeze = (_freezeState === 1) ? 0 : 1;
    var fn = typeof _api.submitCommand === 'function'
      ? function () { return _api.submitCommand(CMD_ESTIMATOR, 1, newFreeze); }
      : null;
    if (fn) fn().catch(function (e) { console.error('freeze toggle rejected:', e); });
  };

  /* ── Render helpers ─────────────────────────────────────────────────── */
  function updateModeButtons(mode, armState) {
    var disabled = (armState !== 'disarmed');
    var armWarn = q('est-arm-warn');
    var unkWarn = q('est-unknown-warn');
    if (armWarn) armWarn.style.display = (armState === 'armed') ? '' : 'none';
    if (unkWarn) unkWarn.style.display = (armState === 'unknown') ? '' : 'none';

    var names = ['fixed', 'ema', 'ekf'];
    var labels = ['FIXED (0)', 'EMA (1)', 'EKF (2)'];
    for (var i = 0; i < names.length; i++) {
      var btn = q('est-btn-' + names[i]);
      if (!btn) continue;
      btn.disabled = disabled;
      btn.className = 'est-mode-btn' + (mode === i ? ' active' : '');
    }
    var rb = q('est-mode-readback');
    if (rb) {
      if (mode === null) { rb.textContent = 'NOT PUBLISHED'; rb.style.color = 'var(--amber)'; }
      else { rb.textContent = ['FIXED', 'EMA', 'EKF'][mode] || ('Mode ' + mode); rb.style.color = ''; }
    }
  }

  function updateFreezeState(freeze) {
    _freezeState = freeze;
    var lbl = q('est-freeze-label');
    var st  = q('est-freeze-state');
    if (freeze === null) {
      if (lbl) lbl.textContent = 'FREEZE';
      if (st)  { st.textContent = 'NOT PUBLISHED'; st.className = 'est-health-unk'; }
    } else if (freeze === 1) {
      if (lbl) lbl.textContent = 'UNFREEZE';
      if (st)  { st.textContent = 'FROZEN'; st.className = 'est-health-bad'; }
    } else {
      if (lbl) lbl.textContent = 'FREEZE';
      if (st)  { st.textContent = 'RUNNING'; st.className = 'est-health-ok'; }
    }
  }

  function updateTauReadback(tau) {
    var el = q('est-tau-readback');
    if (!el) return;
    if (tau === null) { el.textContent = 'NOT PUBLISHED'; el.style.color = 'var(--amber)'; }
    else              { el.textContent = parseFloat(tau).toFixed(1); el.style.color = ''; }
  }

  function updateEkfOfHealth(health, fallback) {
    var hEl = q('est-ekf-of-health');
    var fEl = q('est-ekf-of-fallback');
    if (hEl) {
      if (health === null) {
        hEl.textContent = 'NOT PUBLISHED'; hEl.className = 'ekf-filter ekf-filter-unknown';
      } else if (health >= 0.5) {
        hEl.textContent = '✓ HEALTHY'; hEl.className = 'ekf-filter ekf-filter-ok';
      } else {
        hEl.textContent = '✗ DIVERGED'; hEl.className = 'ekf-filter ekf-filter-err';
      }
    }
    if (fEl) {
      if (fallback === null) {
        fEl.textContent = 'NOT PUBLISHED'; fEl.className = 'ekf-filter ekf-filter-unknown';
      } else if (fallback >= 0.5) {
        fEl.textContent = '⚠ FELL BACK TO FIXED'; fEl.className = 'ekf-filter ekf-filter-warn';
      } else {
        fEl.textContent = '— none'; fEl.className = 'ekf-filter ekf-filter-ok';
      }
    }
  }

  function ageColor(ageMs) {
    if (ageMs > 30000) return 'var(--muted)';
    if (ageMs > 2000)  return 'var(--amber)';
    return '';
  }

  function renderValue(el, v, key, now) {
    if (!el) return;
    var seen = _keyLastSeen[key];
    if (v != null) {
      _keyLastSeen[key] = now;
      el.textContent = fmtNum(v, 4);
      el.style.color = '';
    } else if (seen) {
      var age = now - seen;
      el.textContent = age > 30000
        ? 'NO DATA (stale ' + (age / 1000).toFixed(0) + 's)'
        : 'STALE (' + (age / 1000).toFixed(1) + 's)';
      el.style.color = ageColor(age);
    } else {
      el.textContent = 'NOT PUBLISHED';
      el.style.color = 'var(--amber)';
    }
  }

  /* ── Main render ────────────────────────────────────────────────────── */
  function renderEstimator() {
    if (!_lastState || !_lastState.streams) return;
    var stream0 = _lastState.streams['0'];
    var streamReceived = (stream0 && stream0.values) ? true : false;
    var values = streamReceived ? stream0.values : null;
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
    var healthV  = values ? slotLookup(values, 'g_ekf_of_health')      : null;
    var fallbkV  = values ? slotLookup(values, 'g_ekf_of_fallback')    : null;

    var mode = (modeV != null) ? Math.round(parseFloat(modeV)) : null;
    _currentMode = mode;

    updateModeButtons(mode, armState);
    updateFreezeState((freezeV != null) ? Math.round(parseFloat(freezeV)) : null);
    updateTauReadback((tauV != null) ? parseFloat(tauV) : null);
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
    var bxEl = q('est-bias-x');
    var byEl = q('est-bias-y');
    if (biasX != null && bxEl) { bxEl.textContent = fmtNum(biasX, 2); bxEl.style.color = ''; anyUpdate = true; }
    if (biasY != null && byEl) { byEl.textContent = fmtNum(biasY, 2); byEl.style.color = ''; anyUpdate = true; }

    /* EKF groups */
    EKF_GROUPS.forEach(function (g) {
      g.axisLabels.forEach(function (al, ai) {
        var k = g.keys[ai];
        var el = q('ekf-' + k.replace(/\./g, '-'));
        if (g.unpublished) {
          if (el) { el.textContent = NOT_PUBLISHED; el.style.color = 'var(--amber)'; }
          return;
        }
        if (!el) return;
        if (!streamReceived) {
          el.textContent = 'AWAITING DATA'; el.style.color = 'var(--muted)'; return;
        }
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
        var idKey = 'raw-' + slot0Key.replace(/\./g, '-');
        var el = q(idKey);
        if (!el) return;
        if (!streamReceived) {
          el.textContent = 'AWAITING DATA'; el.style.color = 'var(--muted)'; return;
        }
        var v = getValue(values, [slot0Key], g.fallback);
        if (v != null && g.scale) v = Number(v) * g.scale;
        if (v != null) anyUpdate = true;
        renderValue(el, v, slot0Key, now);
      });
    });

    /* Covariance */
    COV_KEYS.forEach(function (k) {
      var v = values ? slotLookup(values, k) : null;
      var el = q('cov-' + k.replace(/\./g, '-'));
      if (el) {
        el.textContent = (v != null) ? fmtCov(v) : NOT_PUBLISHED;
        el.style.color = (v != null) ? '' : 'var(--amber)';
        if (v != null) anyUpdate = true;
      }
    });

    /* Filter status */
    var fsEl = q('ekf-filter-status');
    if (fsEl) {
      var fsVal = values ? slotLookup(values, 'estimator.filter_status') : null;
      if (fsVal != null) {
        var label = FILTER_STATUS_LABELS[Math.floor(fsVal)] || ('Status ' + fsVal);
        if (fsVal === 1) {
          fsEl.textContent = label; fsEl.className = 'ekf-filter ekf-filter-ok';
        } else if (fsVal === 2) {
          fsEl.textContent = label; fsEl.className = 'ekf-filter ekf-filter-warn';
        } else if (fsVal === 3) {
          fsEl.textContent = label; fsEl.className = 'ekf-filter ekf-filter-err';
        } else {
          fsEl.textContent = label; fsEl.className = 'ekf-filter ekf-filter-unknown';
        }
        anyUpdate = true;
      } else {
        fsEl.textContent = NOT_PUBLISHED_HINT;
        fsEl.className = 'ekf-filter ekf-filter-unknown';
      }
    }

    if (anyUpdate) _hasData = true;
  }

  function onState(state) {
    if (!state) return;
    _lastState = state;
    var now = Date.now();
    var stream0 = state.streams ? state.streams['0'] : null;
    var values = (stream0 && stream0.values) ? stream0.values : null;
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
    _api = api;
    api.registerPanel('EKF Estimator', function (container) {
      container.innerHTML = buildHTML();
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
  };
  window.__registerPlugin__('EKF Estimator', window.__PLUGIN_INIT__, window.__PLUGIN_DESTROY__);

})();
