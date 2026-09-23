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
    // ── INNER LOOP (RATE) — the gyro rate controllers ──────────────────────
    rollRate:    { Kp: 5.0,  Ki: 0.01,  Kd: 10.0  },  // gyroxPID  API/pid.c:16
    pitchRate:   { Kp: 5.0,  Ki: 0.01,  Kd: 10.0  },  // gyroyPID  API/pid.c:17
    yawRate:     { Kp: 4.0,  Ki: 0.001, Kd: 2.0   },  // gyrozPID  API/pid.c:18
    // ── OUTER LOOP (ANGLE) — the attitude angle controllers ────────────────
    pitchAngle:  { Kp: 2.6,  Ki: 0.1,   Kd: 9.5   },  // pitchPID  API/pid.c:7
    rollAngle:   { Kp: 2.6,  Ki: 0.1,   Kd: 9.5   },  // rollPID   API/pid.c:9
    yawAngle:    { Kp: 6.5,  Ki: 0.04,  Kd: 1.5   },  // yawPID    API/pid.c:11
    // ── ALTITUDE — Z-position and Z-rate controllers ───────────────────────
    zPos:        { Kp: 0.7,  Ki: 0.005, Kd: 0.1   },  // Z_posPID  API/pid.c:20
    zRate:       { Kp: 400,  Ki: 0.435, Kd: 1.5   },  // Z_ratePID API/pid.c:21
    // ── POSITION / GIMBAL — positioning and gimbal controllers ─────────────
    locx:        { Kp: 0.8,  Ki: 0.01,  Kd: 4.0   },  // locxPID   API/pid.c:25
    locy:        { Kp: 0.8,  Ki: 0.01,  Kd: 4.0   },  // locyPID   API/pid.c:26
    locxs:       { Kp: 3.0,  Ki: 0.0,   Kd: 6.0   },  // locxsPID  API/pid.c:27
    locys:       { Kp: 3.0,  Ki: 0.0,   Kd: 6.0   },  // locysPID  API/pid.c:28
    streeYaw:    { Kp: 1.0,  Ki: 0.0,   Kd: 2.0   },  // stree_yaw_speed  API/pid.c:30
    streePitch:  { Kp: 0.6,  Ki: 0.0,   Kd: 0.0   },  // stree_pitch_speed API/pid.c:31
  };

  /* controller → the firmware member name inside `Ctrler`
   * (Global_file/robot_types.h:45-62). These are matched EXACTLY, not by
   * substring: `locx` is a prefix of `locxs` and `yaw` occurs inside
   * `stree_yaw_speed`, so a tolerant contains() match would happily show the
   * Loc XS gain in the Loc X row and never look wrong while doing it. The
   * names are known exactly, so tolerance buys nothing and costs correctness. */
  var LIVE_KEYS = {
    rollRate:    'gyroxPID',
    pitchRate:   'gyroyPID',
    yawRate:     'gyrozPID',
    pitchAngle:  'pitchPID',
    rollAngle:   'rollPID',
    yawAngle:    'yawPID',
    zPos:        'Z_posPID',
    zRate:       'Z_ratePID',
    locx:        'locxPID',
    locy:        'locyPID',
    locxs:       'locxsPID',
    locys:       'locysPID',
    streeYaw:    'stree_yaw_speed',
    streePitch:  'stree_pitch_speed',
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
  var Z_LOOP = [
    { key: 'zPos',  plain: 'Z position' },
    { key: 'zRate', plain: 'Z rate' },
  ];
  var POS_GIMBAL = [
    { key: 'locx',    plain: 'Loc X' },
    { key: 'locy',    plain: 'Loc Y' },
    { key: 'locxs',   plain: 'Loc XS' },
    { key: 'locys',   plain: 'Loc YS' },
    { key: 'streeYaw',    plain: 'Stree yaw speed' },
    { key: 'streePitch',  plain: 'Stree pitch speed' },
  ];

  function q(id) { return typeof document !== 'undefined' ? document.getElementById(id) : null; }

  function fmt(v) {
    if (v == null) return '<span style="color:var(--amber)">not published</span>';
    return parseFloat(v).toFixed(3);
  }

  /* Exact lookup of Ctrler.<member>.<term>. The subscribe stream prefixes raw
   * DWARF keys with `slot<N>.`, so strip that before comparing. */
  function liveGain(_state, sym, term) {
    if (!_state || !_state.streams || !sym) return undefined;
    var want = 'Ctrler.' + sym + '.' + term;
    var slots = Object.keys(_state.streams);
    for (var i = 0; i < slots.length; i++) {
      var v = _state.streams[slots[i]].values || {};
      for (var k in v) {
        if (k.replace(/^slot\d+\./, '') === want) return v[k];
      }
    }
    return undefined;
  }

  function rowHtml(_state, item) {
    var d = FW_DEFAULTS[item.key];
    var kpVal = liveGain(_state, LIVE_KEYS[item.key], 'Kp');
    var kiVal = liveGain(_state, LIVE_KEYS[item.key], 'Ki');
    var kdVal = liveGain(_state, LIVE_KEYS[item.key], 'Kd');
    return '<div style="display:flex;align-items:center;gap:6px;padding:3px 0;' +
      'border-top:1px solid rgba(255,255,255,0.06);">' +
      '<span style="flex:1;font-size:11px;">' + item.plain + '</span>' +
      '<span style="width:72px;text-align:right;font-family:Consolas,monospace;font-size:11px;">' +
      (kpVal != null ? fmt(kpVal) : '<span style="color:var(--amber)">not published</span>') +
      '</span>' +
      '<span style="width:64px;text-align:right;font-family:Consolas,monospace;font-size:10px;color:var(--muted)">Kp ' + d.Kp + '</span>' +
      '<span style="width:72px;text-align:right;font-family:Consolas,monospace;font-size:11px;">' +
      (kiVal != null ? fmt(kiVal) : '<span style="color:var(--amber)">not published</span>') +
      '</span>' +
      '<span style="width:64px;text-align:right;font-family:Consolas,monospace;font-size:10px;color:var(--muted)">Ki ' + d.Ki + '</span>' +
      '<span style="width:72px;text-align:right;font-family:Consolas,monospace;font-size:11px;">' +
      (kdVal != null ? fmt(kdVal) : '<span style="color:var(--amber)">not published</span>') +
      '</span>' +
      '<span style="width:64px;text-align:right;font-family:Consolas,monospace;font-size:10px;color:var(--muted)">Kd ' + d.Kd + '</span>' +
      '</div>';
  }

  function buildBlock(_state, title, items) {
    return '<div style="margin-bottom:12px;">' +
      '<div style="font-size:10px;color:var(--muted);text-transform:uppercase;letter-spacing:0.05em;margin-bottom:4px;">' + title +
      ' <span style="text-transform:none;color:var(--muted)">— current Kp/Ki/Kd · firmware default</span></div>' +
      items.map(function (it) { return rowHtml(_state, it); }).join('') +
      '</div>';
  }

  var _state = null;

  function render(container) {
    container.innerHTML = [
      '<div style="font-size:10px;color:var(--muted);margin-bottom:8px;">' +
      'Gains are static until subscribed. Click <b>PID gains</b> in Slot Manager to publish Kp, Ki, Kd for all controllers. ' +
      'Until then, "not published" is shown for current values. ' +
      'Firmware defaults cited to <span style="font-family:Consolas,monospace">API/pid.c</span>.</div>',
      buildBlock(_state, 'Inner loop (rate)', INNER),
      buildBlock(_state, 'Outer loop (angle)', OUTER),
      buildBlock(_state, 'Altitude', Z_LOOP),
      buildBlock(_state, 'Position / Gimbal', POS_GIMBAL),
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