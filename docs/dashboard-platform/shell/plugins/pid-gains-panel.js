/**
 * pid-gains-panel.js — PID gains panel (operator walkthrough 2026-09-22, item 12)
 *
 * Groups the attitude PID gains into INNER LOOP (RATE) vs OUTER LOOP (ANGLE)
 * and shows — for each controller — its current value (from telemetry when the
 * value happens to be subscribed, otherwise an honest "not published") next to
 * the firmware default. The firmware default VALUES come from API/pid.c
 * `Ctrler` initialiser; each is cited with its file:line so the panel can be
 * re-verified against source. IMPORTANT axis convention (firmware + the
 * inner_loops manifest doc): gyrox = ROLL rate, gyroy = PITCH rate.
 *
 * The current gains are NOT published by any default telemetry preset (the
 * inner_loops / outer_loops manifests stream Des/FB/U per loop, not Kp/Ki/Kd),
 * so the "current" cell reads "not published" unless the operator subscribes a
 * gain symbol — showing the default is therefore always meaningful.
 */
(function () {
  'use strict';

  // ── Software defaults (firmware API/pid.c). Column order per the struct
  //    initialiser comment: des, FB, Kp, Ki, Kd, ... (API/pid.c:5).
  //    PID = { Kp, Ki, Kd } with file:line citation for each controller.
  var FW_DEFAULTS = {
    // ── OUTER LOOP (ANGLE) — the attitude angle controllers ────────────────
    pitchAngle: { Kp: 2.6, Ki: 0.1, Kd: 9.5 },  // pitchPID  API/pid.c:7
    rollAngle:  { Kp: 2.6, Ki: 0.1, Kd: 9.5 },  // rollPID   API/pid.c:9
    yawAngle:   { Kp: 6.5, Ki: 0.04, Kd: 1.5 }, // yawPID    API/pid.c:11
    // ── INNER LOOP (RATE) — the gyro rate controllers ──────────────────────
    rollRate:   { Kp: 5.0, Ki: 0.01, Kd: 10.0 },  // gyroxPID (roll) API/pid.c:16
    pitchRate:  { Kp: 5.0, Ki: 0.01, Kd: 10.0 },  // gyroyPID (pitch) API/pid.c:17
    yawRate:    { Kp: 4.0, Ki: 0.001, Kd: 2.0 },  // gyrozPID (yaw) API/pid.c:18
  };

  // controller → (symbol fragments used to read a live value from telemetry)
  var LIVE_KEYS = {
    pitchAngle: ['pitchPID', 'pitch', 'angle.pitch'],
    rollAngle:  ['rollPID', 'roll', 'angle.roll'],
    yawAngle:   ['yawPID', 'yaw', 'angle.yaw'],
    rollRate:   ['gyroxPID', 'gyrox', 'rate.roll'],
    pitchRate:  ['gyroyPID', 'gyroy', 'rate.pitch'],
    yawRate:    ['gyrozPID', 'gyroz', 'rate.yaw'],
  };

  var INNER = [
    { key: 'rollRate',  plain: 'Roll rate (gyrox)' },
    { key: 'pitchRate', plain: 'Pitch rate (gyroy)' },
    { key: 'yawRate',   plain: 'Yaw rate (gyroz)' },
  ];
  var OUTER = [
    { key: 'pitchAngle', plain: 'Pitch angle' },
    { key: 'rollAngle',  plain: 'Roll angle' },
    { key: 'yawAngle',   plain: 'Yaw angle' },
  ];

  function q(id) { return typeof document !== 'undefined' ? document.getElementById(id) : null; }

  function fmt(v) {
    if (v == null) return '<span style="color:var(--amber)">not published</span>';
    return parseFloat(v).toFixed(3);
  }

  // Tolerant telemetry lookup: find a stream value whose key contains the
  // controller symbol AND the term (Kp|Ki|Kd). Handles slot-prefixed keys.
  function liveGain(_state, symTokens, term) {
    if (!_state || !_state.streams) return undefined;
    var slots = Object.keys(_state.streams);
    for (var i = 0; i < slots.length; i++) {
      var v = _state.streams[slots[i]].values || {};
      for (var k in v) {
        var kk = k.replace(/^slot\d+\./, '');
        var hasCtrl = symTokens.some(function (t) { return kk.indexOf(t) !== -1; });
        if (hasCtrl && kk.indexOf(term) !== -1) return v[k];
      }
    }
    return undefined;
  }

  function rowHtml(_state, item, termLabel) {
    var d = FW_DEFAULTS[item.key];
    return '<div style="display:flex;align-items:center;gap:8px;padding:3px 0;' +
      'border-top:1px solid rgba(255,255,255,0.06);">' +
      '<span style="flex:1;font-size:11px;">' + item.plain + '</span>' +
      '<span style="width:80px;text-align:right;font-family:Consolas,monospace;font-size:11px;">' +
      (liveGain(_state, LIVE_KEYS[item.key], 'Kp') != null ? fmt(liveGain(_state, LIVE_KEYS[item.key], 'Kp')) : '<span style="color:var(--amber)">not published</span>') +
      '</span>' +
      '<span style="width:64px;text-align:right;font-family:Consolas,monospace;font-size:10px;color:var(--muted)">def ' + d.Kp + '</span>' +
      '</div>';
  }

  function buildBlock(_state, title, items, term) {
    return '<div style="margin-bottom:12px;">' +
      '<div style="font-size:10px;color:var(--muted);text-transform:uppercase;letter-spacing:0.05em;margin-bottom:4px;">' + title +
      ' <span style="text-transform:none;color:var(--muted)">— current · firmware default</span></div>' +
      items.map(function (it) { return rowHtml(_state, it, term); }).join('') +
      '</div>';
  }

  var _state = null;

  function render(container) {
    // Header line: legend + axis convention note.
    container.innerHTML = [
      '<div style="font-size:10px;color:var(--muted);margin-bottom:8px;">' +
      'Inner loop = gyro RATE (gyrox=roll, gyroy=pitch); outer loop = ANGLE. ' +
      'Current values are shown when subscribed; otherwise "not published". ' +
      'Defaults cited to <span style="font-family:Consolas,monospace">API/pid.c</span>.</div>',
      buildBlock(_state, 'Inner loop (rate)', INNER, 'Kp'),
      buildBlock(_state, 'Outer loop (angle)', OUTER, 'Kp'),
    ].join('');
  }

  window.__PLUGIN_INIT__ = function (api) {
    api.registerPanel('PID Gains', function (container) {
      render(container);
      api.subscribe(function (state) {
        _state = state;
        render(container);
      });
    });
  };
  window.__PLUGIN_DESTROY__ = function () { _state = null; };
  window.__registerPlugin__('PID Gains', window.__PLUGIN_INIT__, window.__PLUGIN_DESTROY__);
})();