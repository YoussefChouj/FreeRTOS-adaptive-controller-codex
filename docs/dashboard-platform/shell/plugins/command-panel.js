/**
 * command-panel.js — Enhanced Command Panel (S14)
 *
 * Features:
 * 1. Full COMMAND_REGISTRY of all 30 commands from COMMAND_SPEC.md
 *    (with corrected IDs: Abort All=0x0D, Reference Model=0x13, etc.)
 * 2. Safety class badges (critical=red, boundary=amber, operational=blue,
 *    diagnostic=green) on every dropdown row, plus precondition tooltips
 * 3. ARM/DISARM live badge driven by status.arm (or DroneStatus.ARM_Status)
 * 4. Quick commands: Arm/Disarm, Flight Modes, SysID, MRAC On/Off, Abort
 * 5. Virtual RC panel (cmd 0x06): 5 sliders, enable/disable, center-all,
 *    auto-shown only when FlyMode indicates SDK
 * 6. Bench Mode toggle (cmd 0x07) with confirmation prompt
 * 7. Navigation paths (0x0A TWC, 0x0B Sinusoid, 0x0C Circle, 0x11 Figure-8)
 *    with grouped Start/Stop buttons
 * 8. EKF Reset button (0x18) with disarmed precondition + confirmation
 * 9. Session-grouped command history with status filter chips
 *    (applied / rejected / error), persisted to localStorage
 * 10. Result polling with graceful fallback ("submitted (no result feedback)")
 * 11. Per-command safety interlock (block 0x04, 0x06, 0x0D, 0x0E when armed)
 *
 * Constraints:
 *   - Stay under 2500 lines, no external libs
 *   - Use shell CSS variables (var(--green), var(--red), var(--amber), ...)
 *   - IIFE pattern with __registerPlugin__
 *   - Proper destroy() cleanup
 */
(function () {
  'use strict';

  // ── Command Registry (all 30 commands from COMMAND_SPEC.md) ─────────────
  // Each entry: { name, min, max, unit, safetyClass, precondition,
  //               indexDesc, indexMax }
  var COMMAND_REGISTRY = {
    0x00: { name: 'NOP',                    min: 0,        max: 0,        unit: '',          safetyClass: 'diagnostic',  precondition: 'none',                     indexDesc: 'no parameter',                              indexMax: 0  },
    0x01: { name: 'PID Gain',               min: 0,        max: 200,      unit: 'gain',      safetyClass: 'boundary',    precondition: 'SDK auth + Disarmed',     indexDesc: 'axis*3 + 0=Kp, +1=Ki, +2=Kd (axis: 0=pitch,1=roll,2=yaw,3=alt,4=latlon)', indexMax: 17 },
    0x02: { name: 'MRAC Gamma',             min: 0,        max: 50,       unit: 'rate',      safetyClass: 'operational', precondition: 'SDK auth',                indexDesc: 'high=axis (0–3), low=basis (<MAX_NUM_BASIS)',            indexMax: 63 },
    0x03: { name: 'Mixer & Throttle',       min: 0,        max: 1,        unit: 'ratio',     safetyClass: 'boundary',    precondition: 'Disarmed',                indexDesc: '0–3=mixer, 4–7=u_max, 8=throttle min, 9=throttle max', indexMax: 9  },
    0x04: { name: 'Flight Mode / Abort',    min: 0,        max: 1,        unit: 'event',     safetyClass: 'critical',    precondition: 'none (changes FSM state)',indexDesc: '0=abort all paths, 1=recover SDK auth',                indexMax: 1  },
    0x05: { name: 'MRAC What_limit',        min: 0,        max: 100,      unit: 'limit',     safetyClass: 'operational', precondition: 'SDK auth',                indexDesc: 'high=axis (0–3), low=basis',                            indexMax: 63 },
    0x06: { name: 'Virtual RC',             min: -1,       max: 1,        unit: 'stick',     safetyClass: 'critical',    precondition: 'SDK mode only',           indexDesc: '0–4 = stick channel (ch0..ch4)',                        indexMax: 4  },
    0x07: { name: 'Bench Mode',             min: 0,        max: 1,        unit: 'on/off',    safetyClass: 'critical',    precondition: 'Bench state + Disarmed',  indexDesc: '0=disable, 1=enable bench mode',                        indexMax: 0  },
    0x08: { name: 'MRAC What_tol',          min: 0,        max: 10,       unit: 'tol',       safetyClass: 'operational', precondition: 'SDK auth',                indexDesc: 'high=axis (0–3), low=basis',                            indexMax: 63 },
    0x09: { name: 'GS Safety Limits',       min: 0,        max: 60,       unit: 'm/s|deg',   safetyClass: 'boundary',    precondition: 'none',                    indexDesc: '0=max h-speed, 1=max v-speed, 2=max pitch, 3=max roll', indexMax: 3  },
    0x0A: { name: 'TWC Target',             min: -1000,     max: 1000,     unit: 'mixed',     safetyClass: 'critical',    precondition: 'SDK auth',                indexDesc: '0–4 = TWC parameters (lat/lon/alt/…)',                  indexMax: 4  },
    0x0B: { name: 'Sinusoid Path',          min: 0,        max: 1,        unit: 'path',      safetyClass: 'critical',    precondition: 'SDK auth',                indexDesc: '0–6=freq/amp/phase/start/stop, 7=trigger',             indexMax: 7  },
    0x0C: { name: 'Circle Path',            min: 0,        max: 1,        unit: 'path',      safetyClass: 'critical',    precondition: 'SDK auth',                indexDesc: '0–5=radius/center/start/stop, 6=trigger',              indexMax: 6  },
    0x0D: { name: 'Abort All',              min: 0,        max: 0,        unit: 'estop',     safetyClass: 'critical',    precondition: 'none (always allowed)',   indexDesc: 'index=0 triggers; nonzero=no-op',                       indexMax: 0  },
    0x0E: { name: 'SDK Arm Authority',      min: 0,        max: 1,        unit: 'auth',      safetyClass: 'critical',    precondition: 'none (controls authority)',indexDesc: 'val=0 release, nonzero=request',                       indexMax: 0  },
    0x0F: { name: 'Runtime Flags',          min: 0,        max: 102,      unit: 'mode',      safetyClass: 'operational', precondition: 'SDK auth for mode change',indexDesc: '0–12=MRAC flags, 100=legacy, 101=mixed, 102=sub-only', indexMax: 102 },
    0x10: { name: 'Reset Optical Flow',     min: 0,        max: 0,        unit: 'reset',     safetyClass: 'operational', precondition: 'Disarmed',                indexDesc: 'index=0 triggers; nonzero=no-op',                       indexMax: 0  },
    0x11: { name: 'Figure-8 Path',          min: 0,        max: 1,        unit: 'path',      safetyClass: 'critical',    precondition: 'SDK auth',                indexDesc: '0–6=params/start/stop, 7=trigger',                     indexMax: 7  },
    0x12: { name: 'Waypoint Spacing',       min: 0,        max: 100,      unit: 'm',         safetyClass: 'operational', precondition: 'none',                    indexDesc: 'val = spacing (negative clamped to 0)',                 indexMax: 0  },
    0x13: { name: 'Reference Model Switch', min: 0,        max: 2,        unit: 'selector',  safetyClass: 'operational', precondition: 'SDK auth',                indexDesc: 'val=0..2 reference model selector',                     indexMax: 0  },
    0x14: { name: 'SysID / Geofence',       min: 0,        max: 1,        unit: 'mode',      safetyClass: 'operational', precondition: 'SDK auth',                indexDesc: '0–5=SysID params, 6=start/abort SysID, 7=geofence on/off', indexMax: 7 },
    0x15: { name: 'Gyro Filter',            min: 0,        max: 200,      unit: 'Hz',        safetyClass: 'operational', precondition: 'SDK auth',                indexDesc: '0=enable (0/1), 1=cutoff freq (Hz)',                    indexMax: 1  },
    0x16: { name: 'Motor Bench Output',     min: 0,        max: 4000,     unit: 'CCR',       safetyClass: 'critical',    precondition: 'Bench state + Disarmed',  indexDesc: '0=heartbeat, 1=motor idx (0–4), 2=CCR (2000–4000)',    indexMax: 2  },
    0x17: { name: 'OF Bias Capture',        min: 0,        max: 0,        unit: 'capture',   safetyClass: 'diagnostic',  precondition: 'none',                    indexDesc: 'index=0 triggers; nonzero=no-op',                       indexMax: 0  },
    0x18: { name: 'EKF Reset',              min: 0,        max: 0,        unit: 'reset',     safetyClass: 'diagnostic',  precondition: 'GROUND_IDLE or DisArmed', indexDesc: 'index=0 triggers; nonzero=no-op',                       indexMax: 0  },
    0x1E: { name: 'OF Bias Estimator Mode', min: 0,        max: 2,        unit: 'mode',      safetyClass: 'operational', precondition: 'SDK auth',                indexDesc: '0=mode (0=FIXED,1=EMA,2=EKF), 1=freeze (≥0.5 freezes)', indexMax: 1 },
    // Legacy / extra commands not in spec but observed in current panel — kept for backward compat
    0x1A: { name: 'Filter Cutoff (legacy)', min: 1,        max: 200,      unit: 'Hz',        safetyClass: 'operational', precondition: 'SDK auth',                indexDesc: 'cutoff Hz',                                                indexMax: 0  },
    0x1C: { name: 'Calibration (legacy)',   min: 0,        max: 10,       unit: 'mode',      safetyClass: 'operational', precondition: 'Disarmed',                indexDesc: 'calibration mode index',                                  indexMax: 10 },
    0x20: { name: 'SysID Chirp (legacy)',   min: 0,        max: 3,        unit: 'mode',      safetyClass: 'operational', precondition: 'Disarmed',                indexDesc: 'chirp mode selector',                                      indexMax: 3  },
    0xFE: { name: 'Firmware Info',          min: 0,        max: 0,        unit: '',          safetyClass: 'diagnostic',  precondition: 'none',                    indexDesc: 'no parameter',                                            indexMax: 0  },
  };

  // ── Command Parameters Schema (from firmware_contract.py COMMAND_TABLE) ───
  var COMMAND_PARAMS_REGISTRY = {
    0x01: [
      { index: 0, name: 'axis', unit: 'axis', min_val: 0, max_val: 6 },
      { index: 1, name: 'gain_type', unit: 'enum', min_val: 0, max_val: 2 },
      { index: 2, name: 'Kp_or_Ki_or_Kd', unit: 'gain', min_val: 0.0, max_val: 200.0 }
    ],
    0x02: [
      { index: 0, name: 'axis', unit: 'axis', min_val: 0, max_val: 3 },
      { index: 1, name: 'elem', unit: 'index', min_val: 0, max_val: 5 },
      { index: 2, name: 'gamma', unit: '1/s', min_val: 0.0, max_val: null }
    ],
    0x03: [
      { index: 0, name: 'param', unit: 'enum', min_val: 0, max_val: 9 },
      { index: 1, name: 'value', unit: 'float', min_val: 0.0, max_val: 1.0 }
    ],
    0x04: [
      { index: 0, name: 'abort', unit: 'enum', min_val: 0, max_val: 0 },
      { index: 1, name: 'recover_sdk', unit: 'enum', min_val: 1, max_val: 1 }
    ],
    0x05: [
      { index: 0, name: 'axis', unit: 'axis', min_val: 0, max_val: 3 },
      { index: 1, name: 'elem', unit: 'index', min_val: 0, max_val: 5 },
      { index: 2, name: 'What_limit', unit: 'float', min_val: 0.0, max_val: null }
    ],
    0x06: [
      { index: 0, name: 'axis', unit: 'axis', min_val: 0, max_val: 3 },
      { index: 1, name: 'value', unit: 'norm', min_val: -1.0, max_val: 1.0 }
    ],
    0x07: [
      { index: 0, name: 'enable', unit: 'bool', min_val: 0, max_val: 1 }
    ],
    0x08: [
      { index: 0, name: 'axis', unit: 'axis', min_val: 0, max_val: 3 },
      { index: 1, name: 'elem', unit: 'index', min_val: 0, max_val: 5 },
      { index: 2, name: 'What_tol', unit: 'float', min_val: 0.0, max_val: null }
    ],
    0x09: [
      { index: 0, name: 'param', unit: 'enum', min_val: 0, max_val: 3 },
      { index: 1, name: 'value', unit: 'float', min_val: null, max_val: null }
    ],
    0x0A: [
      { index: 0, name: 'target_x', unit: 'm', min_val: null, max_val: null },
      { index: 1, name: 'target_y', unit: 'm', min_val: null, max_val: null },
      { index: 2, name: 'target_z', unit: 'm', min_val: null, max_val: null },
      { index: 3, name: 'set_yaw', unit: 'deg', min_val: null, max_val: null },
      { index: 4, name: 'execute', unit: 'bool', min_val: 0, max_val: 1 }
    ],
    0x0B: [
      { index: 0, name: 'center_x', unit: 'm', min_val: null, max_val: null },
      { index: 1, name: 'center_y', unit: 'm', min_val: null, max_val: null },
      { index: 2, name: 'center_z', unit: 'm', min_val: null, max_val: null },
      { index: 3, name: 'amplitude', unit: 'm', min_val: null, max_val: null },
      { index: 4, name: 'frequency', unit: 'Hz', min_val: null, max_val: null },
      { index: 5, name: 'duration', unit: 's', min_val: null, max_val: null },
      { index: 6, name: 'axis', unit: 'enum', min_val: 0, max_val: 2 },
      { index: 7, name: 'active', unit: 'bool', min_val: 0, max_val: 1 }
    ],
    0x0C: [
      { index: 0, name: 'center_x', unit: 'm', min_val: null, max_val: null },
      { index: 1, name: 'center_y', unit: 'm', min_val: null, max_val: null },
      { index: 2, name: 'center_z', unit: 'm', min_val: null, max_val: null },
      { index: 3, name: 'radius', unit: 'm', min_val: null, max_val: null },
      { index: 4, name: 'angular_speed', unit: 'rad/s', min_val: null, max_val: null },
      { index: 5, name: 'duration', unit: 's', min_val: null, max_val: null },
      { index: 6, name: 'active', unit: 'bool', min_val: 0, max_val: 1 }
    ],
    0x0D: [
      { index: 0, name: 'abort', unit: 'enum', min_val: 0, max_val: 0 }
    ],
    0x0F: [
      { index: 0, name: 'adaptation_on', unit: 'bool', min_val: 0, max_val: 1 },
      { index: 1, name: 'projection_on', unit: 'bool', min_val: 0, max_val: 1 },
      { index: 2, name: 'deadzone_on', unit: 'bool', min_val: 0, max_val: 1 },
      { index: 3, name: 'hard_freeze_on', unit: 'bool', min_val: 0, max_val: 1 },
      { index: 4, name: 'tanh_saturation_on', unit: 'bool', min_val: 0, max_val: 1 },
      { index: 5, name: 'e_modification_on', unit: 'bool', min_val: 0, max_val: 1 },
      { index: 6, name: 'l1_filtering_on', unit: 'bool', min_val: 0, max_val: 1 },
      { index: 7, name: 'axis_enable_pitch', unit: 'bool', min_val: 0, max_val: 1 },
      { index: 8, name: 'axis_enable_roll', unit: 'bool', min_val: 0, max_val: 1 },
      { index: 9, name: 'axis_enable_yaw', unit: 'bool', min_val: 0, max_val: 1 },
      { index: 10, name: 'output_injection_on', unit: 'bool', min_val: 0, max_val: 1 },
      { index: 11, name: 'id_frame_on', unit: 'bool', min_val: 0, max_val: 1 },
      { index: 12, name: 'of_frame_on', unit: 'bool', min_val: 0, max_val: 1 },
      { index: 100, name: 'telemetry_legacy', unit: 'enum', min_val: 0, max_val: 0 },
      { index: 101, name: 'telemetry_mixed', unit: 'enum', min_val: 0, max_val: 0 },
      { index: 102, name: 'telemetry_subscribe_only', unit: 'enum', min_val: 0, max_val: 0 }
    ],
    0x10: [
      { index: 0, name: 'reset', unit: 'enum', min_val: 0, max_val: 0 }
    ],
    0x11: [
      { index: 0, name: 'center_x', unit: 'm', min_val: null, max_val: null },
      { index: 1, name: 'center_y', unit: 'm', min_val: null, max_val: null },
      { index: 2, name: 'center_z', unit: 'm', min_val: null, max_val: null },
      { index: 3, name: 'amplitude', unit: 'm', min_val: null, max_val: null },
      { index: 4, name: 'angular_speed', unit: 'rad/s', min_val: null, max_val: null },
      { index: 5, name: 'duration', unit: 's', min_val: null, max_val: null },
      { index: 6, name: 'type', unit: 'enum', min_val: 0, max_val: 1 },
      { index: 7, name: 'active', unit: 'bool', min_val: 0, max_val: 1 }
    ],
    0x12: [
      { index: 0, name: 'spacing_cm', unit: 'cm', min_val: 0.0, max_val: null }
    ],
    0x13: [
      { index: 0, name: 'type', unit: 'enum', min_val: 0, max_val: 2 }
    ],
    0x14: [
      { index: 0, name: 'axis', unit: 'enum', min_val: 0, max_val: 3 },
      { index: 1, name: 'signal', unit: 'enum', min_val: 0, max_val: 1 },
      { index: 2, name: 'f0_Hz', unit: 'Hz', min_val: null, max_val: null },
      { index: 3, name: 'f1_Hz', unit: 'Hz', min_val: null, max_val: null },
      { index: 4, name: 'amplitude', unit: 'deg/s', min_val: null, max_val: null },
      { index: 5, name: 'duration_s', unit: 's', min_val: null, max_val: null },
      { index: 6, name: 'start', unit: 'bool', min_val: 0, max_val: 1 },
      { index: 7, name: 'geofence_enable', unit: 'bool', min_val: 0, max_val: 1 }
    ],
    0x15: [
      { index: 0, name: 'enable', unit: 'bool', min_val: 0, max_val: 1 },
      { index: 1, name: 'cutoff_Hz', unit: 'Hz', min_val: null, max_val: null }
    ],
    0x16: [
      { index: 0, name: 'motor_id', unit: 'enum', min_val: 0, max_val: 3 },
      { index: 1, name: 'ccr', unit: 'counts', min_val: 0.0, max_val: 2000.0 }
    ],
    0x17: [
      { index: 0, name: 'capture', unit: 'trigger', min_val: 0, max_val: 0 }
    ],
    0x18: [
      { index: 0, name: 'recalibrate', unit: 'trigger', min_val: 0, max_val: 0 }
    ],
    0x1E: [
      { index: 0, name: 'bias_mode', unit: 'enum', min_val: 0, max_val: 2, symbol: 'g_of_bias_mode' },
      { index: 1, name: 'ema_freeze', unit: 'bool', min_val: 0, max_val: 1, symbol: 'g_of_bias_ema_freeze' }
    ]
  };

  // ── Safety class CSS map ────────────────────────────────────────────────
  var SAFETY_CLASS_COLORS = {
    critical:    'var(--red)',
    boundary:    'var(--amber)',
    operational: '#4a9eff',  // blue (not in current palette — use accent)
    diagnostic:  'var(--green)'
  };
  var SAFETY_CLASS_LABELS = {
    critical:    'CRIT',
    boundary:    'BND',
    operational: 'OPS',
    diagnostic:  'DIAG'
  };

  // ── Quick Commands (basic preset buttons) ───────────────────────────────
  var QUICK_COMMANDS = [
    { id: 'arm',         label: 'Arm Auth',    icon: '\u26A1', cmdId: 0x0E, index: 0, value: 1,   safetyClass: 'critical' },
    { id: 'disarm',      label: 'Disarm',      icon: '\u25A1', cmdId: 0x0E, index: 0, value: 0,   safetyClass: 'critical' },
    { id: 'mode_abort',  label: 'Abort Paths', icon: '\u25A0', cmdId: 0x04, index: 0, value: 0,   safetyClass: 'critical' },
    { id: 'recover_sdk', label: 'Recover SDK', icon: '\u270E', cmdId: 0x04, index: 1, value: 1,   safetyClass: 'critical' },
    { id: 'sysid',       label: 'SysID',       icon: '\u25B6', cmdId: 0x14, index: 6, value: 1,   safetyClass: 'operational' },
    { id: 'abort',    label: 'Abort All',    icon: '\u25A0', cmdId: 0x0D, index: 0, value: 0,   safetyClass: 'critical' },
    { id: 'mrac_on',  label: 'MRAC On',      icon: '\u2605', cmdId: 0x0F, index: 1, value: 1,   safetyClass: 'operational' },
    { id: 'mrac_off', label: 'MRAC Off',     icon: '\u2606', cmdId: 0x0F, index: 1, value: 0,   safetyClass: 'operational' },
    { id: 'ekf_reset', label: 'EKF Reset',   icon: '\u21BB', cmdId: 0x18, index: 0, value: 0,   safetyClass: 'diagnostic', needsDisarm: true },
  ];

  // ── Navigation paths (0x0A, 0x0B, 0x0C, 0x11) ───────────────────────────
  var NAV_PATHS = [
    { id: 'twc',     cmdId: 0x0A, name: 'TWC',       startIndex: 4,  stopIndex: 4, startValue: 1, stopValue: 0 },
    { id: 'sin',     cmdId: 0x0B, name: 'Sinusoid',  startIndex: 7,  stopIndex: 7, startValue: 1, stopValue: 0 },
    { id: 'circle',  cmdId: 0x0C, name: 'Circle',    startIndex: 6,  stopIndex: 6, startValue: 1, stopValue: 0 },
    { id: 'fig8',    cmdId: 0x11, name: 'Figure-8',  startIndex: 7,  stopIndex: 7, startValue: 1, stopValue: 0 },
  ];

  // ── Constants ────────────────────────────────────────────────────────────
  var HISTORY_KEY = 'gs_cmd_history';
  var MAX_HISTORY = 50;
  var PENDING_TIMEOUT_MS = 5000;
  var POLL_RESULT_INTERVAL_MS = 250;
  var HISTORY_FILTER_KEY = 'gs_cmd_hist_filter';

  // ── State ────────────────────────────────────────────────────────────────
  var _history = [];
  var _pendingTx = null;
  var _pollTimer = null;
  var _api = null;
  var _state = null;
  var _historyFilter = 'all';
  var _stateUnsub = null;
  var _commandSymbols = {
    0x1E: {
      0: 'g_of_bias_mode',
      1: 'g_of_bias_ema_freeze'
    }
  };
  var _rawModeForced = false;
  var _currentSymbol = null;
  var _lastSubscribeNs = 0;

  // Virtual RC slider values (used by the SDK override panel)
  var _vrcValues = [0, 0, 0, 0, 0];
  var _vrcEnabled = false;

  // ── DOM refs ────────────────────────────────────────────────────────────
  var _els = {};

  // ── Helpers ──────────────────────────────────────────────────────────────
  function q(id) { return document.getElementById(id); }

  function getRegistryEntry(cmdId) {
    return COMMAND_REGISTRY[cmdId] || {
      name: 'Unknown (0x' + cmdId.toString(16).toUpperCase() + ')',
      min: 0, max: 0, unit: '',
      safetyClass: 'diagnostic',
      precondition: 'unknown',
      indexDesc: '—',
      indexMax: 0
    };
  }

  function getCommandName(cmdId) {
    return getRegistryEntry(cmdId).name;
  }

  function fmtTime() {
    var d = new Date();
    return d.toLocaleTimeString();
  }

  function hexId(n) {
    return '0x' + (n & 0xFF).toString(16).toUpperCase().padStart(2, '0');
  }

  function escapeHtml(str) {
    return String(str).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }

  // ── ARM detection ───────────────────────────────────────────────────────
  // Reads status.arm / DroneStatus.ARM_Status or api.getArmState()
  function armStatusFromState(state) {
    if (!state) return { armed: false, sdk: null, label: 'UNKNOWN' };
    var armedVal = null;
    var sdkVal = null;

    if (state.status && typeof state.status.arm === 'number') {
      armedVal = state.status.arm;
    }
    if (state.status && typeof state.status.rc_authority === 'number') {
      sdkVal = state.status.rc_authority;
    }

    var s0 = state.streams ? (state.streams[0] || state.streams['0']) : null;
    if (s0 && s0.values) {
      var v = s0.values;
      if (armedVal == null) {
        if (typeof v['status.arm'] === 'number') armedVal = v['status.arm'];
        else if (typeof v['DroneStatus.ARM_Status'] === 'number') armedVal = v['DroneStatus.ARM_Status'];
        else if (typeof v.ch13 === 'number') armedVal = v.ch13 & 0x01;
      }
      if (sdkVal == null) {
        if (typeof v['status.rc_authority'] === 'number') sdkVal = v['status.rc_authority'];
        else if (typeof v.s_authority === 'number') sdkVal = v.s_authority;
        else if (typeof v.ch14 === 'number') sdkVal = v.ch14 & 0x01;
      }
    }

    if (armedVal == null && typeof api !== 'undefined' && typeof api.getArmState === 'function') {
      var apiArm = api.getArmState();
      if (apiArm && typeof apiArm.armed === 'boolean') {
        armedVal = apiArm.armed ? 1 : 0;
      }
    }

    if (armedVal == null) return { armed: false, sdk: null, label: 'UNKNOWN' };

    var armed = (armedVal !== 0);
    var sdk = (sdkVal != null) ? (sdkVal !== 0) : null;
    var label;
    if (armed && sdk === true) label = 'ARMED / SDK';
    else if (armed) label = 'ARMED';
    else if (sdk === true) label = 'SDK';
    else label = 'DISARMED';
    return { armed: armed, sdk: sdk, label: label, ch13: armedVal };
  }

  function canIssueCriticalCommand(cmdId) {
    var status = armStatusFromState(_state);
    // Block 0x04 (Flight Mode), 0x0D (Abort - actually 0x0D allowed always),
    // 0x0E (auth), 0x06 (VRC) — but Abort (0x0D) is intentionally always
    // allowed per spec. Only block if spec says "Disarmed" needed.
    var reg = getRegistryEntry(cmdId);
    var pc = (reg.precondition || '').toLowerCase();
    if (pc.indexOf('disarmed') !== -1 && status.armed) return false;
    if (pc.indexOf('sdk mode only') !== -1 && !status.sdk) return false;
    return true;
  }

  // ── LocalStorage History ─────────────────────────────────────────────────
  function loadHistory() {
    try {
      var stored = localStorage.getItem(HISTORY_KEY);
      return stored ? JSON.parse(stored) : [];
    } catch (_) { return []; }
  }

  function saveHistory() {
    try {
      localStorage.setItem(HISTORY_KEY, JSON.stringify(_history.slice(0, MAX_HISTORY)));
    } catch (_) {}
  }

  function addToHistory(entry) {
    // entry includes session_id and friendly hex id
    entry.id = hexId(entry.cmdId);
    if (!entry.session_id) {
      entry.session_id = (_state && _state.session_id) || 'no-session';
    }
    _history.unshift(entry);
    if (_history.length > MAX_HISTORY) _history.pop();
    saveHistory();
    renderHistory();
  }

  // ── Result Status Display ────────────────────────────────────────────────
  function setResultStatus(status, detail) {
    var el = _els.resultBox;
    if (!el) return;
    var color, bg, icon, text;
    if (status === 'pending') {
      color = 'var(--amber)'; bg = 'rgba(245,166,35,0.1)';
      icon = '\u23F3'; text = 'Pending ACK…';
    } else if (status === 'submitted') {
      // Graceful fallback when drone never reports back
      color = 'var(--muted)'; bg = 'rgba(136,136,170,0.1)';
      icon = '\u21AA'; text = 'Submitted (no result feedback)';
    } else if (status === 'applied') {
      color = 'var(--green)'; bg = 'rgba(78,204,163,0.1)';
      icon = '\u2713'; text = 'APPLIED';
    } else if (status === 'rejected') {
      color = 'var(--red)'; bg = 'rgba(233,69,96,0.1)';
      icon = '\u2717'; text = 'REJECTED';
      if (detail) text += ': ' + detail;
    } else if (status === 'safety_interlock') {
      color = 'var(--red)'; bg = 'rgba(233,69,96,0.15)';
      icon = '\u26A0'; text = 'SAFETY INTERLOCK';
      if (detail) text += ': ' + detail;
    } else {
      color = 'var(--muted)'; bg = 'var(--bg)';
      icon = ''; text = '—';
    }
    el.innerHTML = '<span style="color:' + color + '">' + icon + ' ' + escapeHtml(text) + '</span>';
    el.style.background = bg;
    el.style.borderLeftColor = color;
    el.style.display = 'block';

    var warnEl = _els.safetyWarn;
    if (warnEl) {
      if (status === 'safety_interlock') {
        warnEl.innerHTML = [
          '<div style="padding:10px 12px;background:rgba(233,69,96,0.15);',
          '  border:1px solid var(--red);border-radius:4px;margin-top:8px;">',
          '  <div style="color:var(--red);font-weight:700;font-size:12px;margin-bottom:4px;">',
          '    \u26A0 SAFETY INTERLOCK ACTIVE</div>',
          '  <div style="color:var(--text);font-size:11px;">',
          '    This command was rejected for safety reasons.',
          '    Check arm status and safety limits before retrying.',
          '  </div>',
          '  <button id="cp-safety-link" style="',
          '    margin-top:8px;background:var(--red);color:white;border:none;',
          '    padding:4px 10px;border-radius:4px;cursor:pointer;font-size:11px;font-weight:600;">',
          '    Open Safety Panel</button>',
          '</div>'
        ].join('');
        var link = q('cp-safety-link');
        if (link) link.addEventListener('click', function () {
          var safetyCard = document.getElementById('card-alarms');
          if (safetyCard) safetyCard.style.display = '';
          var toggleBtn = document.querySelector('[data-panel="alarms"]');
          if (toggleBtn) toggleBtn.textContent = 'Hide';
        });
      } else if (status === 'submitted' || status === 'applied' || status === 'rejected' || status === 'pending') {
        warnEl.innerHTML = '';
      }
    }
  }

  // ── Command Submission ───────────────────────────────────────────────────
  function submitCommand(cmdId, index, value, opts) {
    opts = opts || {};
    if (!canIssueCriticalCommand(cmdId) && !opts.bypass) {
      setResultStatus('safety_interlock', 'precondition not met (drone armed / wrong mode)');
      return;
    }
    var resultEl = _els.resultBox;
    if (resultEl) {
      resultEl.innerHTML = '<span style="color:var(--amber)">\u23F3 Submitting…</span>';
      resultEl.style.display = 'block';
    }

    if (!_api) return;

    _api.submitCommand(cmdId, index, value).then(function (data) {
      var txid = data.transaction_id;
      _pendingTx = { txid: txid, cmdId: cmdId, index: index, value: value, ts: Date.now() };
      setResultStatus('pending');
      startResultPolling(txid, cmdId, index, value);
    }).catch(function (err) {
      setResultStatus('rejected', err.message || 'Network error');
      addToHistory({
        ts: fmtTime(),
        cmdId: cmdId,
        cmdName: getCommandName(cmdId),
        index: index,
        value: value,
        status: 'error',
        detail: err.message,
        bypass: !!opts.bypass
      });
    });
  }

  // ── Result Polling ──────────────────────────────────────────────────────
  function startResultPolling(txid, cmdId, index, value) {
    if (_pollTimer) clearInterval(_pollTimer);

    var pollCount = 0;
    var maxPolls = Math.ceil(PENDING_TIMEOUT_MS / POLL_RESULT_INTERVAL_MS);

    _pollTimer = setInterval(function () {
      pollCount++;
      checkTransactionResult(txid, cmdId, index, value);
      if (pollCount >= maxPolls) {
        clearInterval(_pollTimer);
        _pollTimer = null;
        if (_pendingTx && _pendingTx.txid === txid) {
          // Graceful fallback: ACKed but never got APPLIED/REJECTED
          setResultStatus('submitted', 'timeout — no APPLIED/REJECTED within ' + PENDING_TIMEOUT_MS + 'ms');
          addToHistory({
            ts: fmtTime(),
            cmdId: cmdId,
            cmdName: getCommandName(cmdId),
            index: index,
            value: value,
            status: 'submitted',
            detail: 'polling timeout'
          });
          _pendingTx = null;
        }
      }
    }, POLL_RESULT_INTERVAL_MS);
  }

  function checkTransactionResult(txid, cmdId, index, value) {
    fetch('/state').then(function (r) { return r.json(); }).then(function (state) {
      var found = false;

      if (state && state.last_transaction_result) {
        var result = state.last_transaction_result;
        if (result.transaction_id === txid) {
          finalizeResult(txid, cmdId, index, value, result);
          found = true;
          return;
        }
      }
      if (state && state.command_results) {
        var results = state.command_results;
        for (var i = 0; i < results.length; i++) {
          if (results[i].transaction_id === txid) {
            finalizeResult(txid, cmdId, index, value, results[i]);
            found = true;
            break;
          }
        }
      }
      if (!found && _pendingTx && _pendingTx.txid === txid && _state) {
        // try matching by command_id as a last resort (if state has recent
        // results inline at the top level keyed by tx, we'll catch it above)
      }
    }).catch(function () { /* ignore transient polling errors */ });
  }

  function finalizeResult(txid, cmdId, index, value, result) {
    clearInterval(_pollTimer);
    _pollTimer = null;

    var status = result.status;
    var detail = result.detail || result.reason || '';

    if (detail.toLowerCase().indexOf('safety') !== -1 || result.reason === 'SAFETY_INTERLOCK') {
      status = 'safety_interlock';
    }

    setResultStatus(status, detail);

    addToHistory({
      ts: fmtTime(),
      cmdId: cmdId,
      cmdName: getCommandName(cmdId),
      index: index,
      value: value,
      status: status === 'safety_interlock' ? 'rejected' : status,
      detail: detail
    });

    _pendingTx = null;
  }

  // ── Manual Command Form & Branching Parameter View ────────────────────────
  function refuseCommand(cmdId, index, value, reason) {
    var resultEl = _els.resultBox;
    if (resultEl) {
      resultEl.innerHTML = '<span style="color:var(--red)">\u2717 Refused: ' + escapeHtml(reason) + '</span>';
      resultEl.style.display = 'block';
    }
    addToHistory({
      ts: fmtTime(),
      cmdId: cmdId,
      cmdName: getCommandName(cmdId),
      index: index,
      value: isNaN(value) ? 0 : value,
      status: 'rejected',
      detail: reason
    });
  }

  function submitParam(cmdId, paramIndex) {
    var params = COMMAND_PARAMS_REGISTRY[cmdId] || [];
    var p = null;
    for (var i = 0; i < params.length; i++) {
      if (params[i].index === paramIndex) {
        p = params[i];
        break;
      }
    }
    var input = q('cp-param-input-' + paramIndex);
    var val = input ? parseFloat(input.value) : 0;
    if (isNaN(val)) {
      refuseCommand(cmdId, paramIndex, val, 'Invalid numeric value');
      return;
    }

    if (p) {
      if (p.min_val != null && val < p.min_val) {
        refuseCommand(cmdId, paramIndex, val, 'Value ' + val + ' is below minimum ' + p.min_val + ' for ' + p.name);
        return;
      }
      if (p.max_val != null && val > p.max_val) {
        refuseCommand(cmdId, paramIndex, val, 'Value ' + val + ' exceeds maximum ' + p.max_val + ' for ' + p.name);
        return;
      }
    }

    if (_els.cmdIndex) _els.cmdIndex.value = paramIndex;
    if (_els.cmdValue) _els.cmdValue.value = val;
    submitCommand(cmdId, paramIndex, val);
  }

  function onManualSubmit(e) {
    e.preventDefault();
    var cmdId = parseInt(_els.cmdIdSelect.value, 10) || 0;
    var index = parseInt(_els.cmdIndex.value, 10) || 0;
    var value = parseFloat(_els.cmdValue.value);
    if (isNaN(value)) {
      refuseCommand(cmdId, index, value, 'Invalid numeric value');
      return;
    }

    // Enforce range if modelled in parameter schema or command registry
    var params = COMMAND_PARAMS_REGISTRY[cmdId] || [];
    var p = null;
    for (var i = 0; i < params.length; i++) {
      if (params[i].index === index) {
        p = params[i];
        break;
      }
    }
    var reg = getRegistryEntry(cmdId);
    var minVal = (p && p.min_val != null) ? p.min_val : reg.min;
    var maxVal = (p && p.max_val != null) ? p.max_val : reg.max;
    var pName = p ? p.name : ('index ' + index);

    if (minVal != null && value < minVal) {
      refuseCommand(cmdId, index, value, 'Value ' + value + ' is below minimum ' + minVal + ' for ' + pName);
      return;
    }
    if (maxVal != null && value > maxVal) {
      refuseCommand(cmdId, index, value, 'Value ' + value + ' exceeds maximum ' + maxVal + ' for ' + pName);
      return;
    }

    submitCommand(cmdId, index, value);
  }

  function toggleRawMode() {
    _rawModeForced = !_rawModeForced;
    renderBranchingForm();
  }

  function renderBranchingForm() {
    var branchEl = q('cp-branching-container');
    var rawEl = q('cp-raw-container');
    var toggleBtn = q('cp-mode-toggle');
    if (!branchEl || !rawEl) return;

    var cmdId = parseInt(_els.cmdIdSelect ? _els.cmdIdSelect.value : 0, 10) || 0;
    var params = COMMAND_PARAMS_REGISTRY[cmdId];

    if (!params || params.length === 0) {
      // Unmodelled command -> forced to raw mode
      branchEl.style.display = 'none';
      rawEl.style.display = '';
      if (toggleBtn) {
        toggleBtn.style.display = 'none';
      }
      return;
    }

    if (toggleBtn) {
      toggleBtn.style.display = '';
    }

    if (_rawModeForced) {
      branchEl.style.display = 'none';
      rawEl.style.display = '';
      if (toggleBtn) {
        toggleBtn.textContent = 'Switch to Parameter Form (Default)';
      }
      return;
    }

    // Branching mode
    branchEl.style.display = '';
    rawEl.style.display = 'none';
    if (toggleBtn) {
      toggleBtn.textContent = 'Switch to Raw Mode (Fallback)';
    }

    var html = params.map(function(p) {
      var minTxt = (p.min_val != null) ? p.min_val : '-\u221E';
      var maxTxt = (p.max_val != null) ? p.max_val : '+\u221E';
      var unitTxt = p.unit ? ' ' + escapeHtml(p.unit) : '';
      var rangeStr = 'Range: ' + minTxt + ' .. ' + maxTxt + unitTxt;
      var stepVal = (p.unit === 'bool' || p.unit === 'enum' || p.unit === 'index' || p.unit === 'trigger') ? '1' : 'any';
      var initVal = (p.min_val != null && p.min_val > 0) ? p.min_val : 0;
      var sym = p.symbol || (_commandSymbols[cmdId] ? _commandSymbols[cmdId][p.index] : null);

      return [
        '<div class="cp-param-card" data-param-idx="' + p.index + '">',
        '  <div style="display:flex;justify-content:space-between;align-items:baseline;margin-bottom:4px;">',
        '    <label for="cp-param-input-' + p.index + '" style="font-weight:600;font-size:12px;color:var(--text)">',
        '      ' + escapeHtml(p.name) + (p.unit ? ' <span style="font-size:10px;color:var(--muted);font-weight:normal">(' + escapeHtml(p.unit) + ')</span>' : ''),
        '    </label>',
        '    <span class="cp-param-range">' + rangeStr + '</span>',
        '  </div>',
        '  <div style="display:flex;gap:6px;align-items:center;">',
        '    <input type="number"',
        '           id="cp-param-input-' + p.index + '"',
        '           class="cp-param-input"',
        '           data-param-idx="' + p.index + '"',
        (p.min_val != null ? ' min="' + p.min_val + '"' : ''),
        (p.max_val != null ? ' max="' + p.max_val + '"' : ''),
        '           step="' + stepVal + '"',
        '           value="' + initVal + '"',
        '           style="flex:1;" />',
        '    <button type="button"',
        '            id="cp-param-send-btn-' + p.index + '"',
        '            class="cp-submit cp-param-send-btn"',
        '            data-param-idx="' + p.index + '"',
        '            style="width:auto;margin:0;padding:5px 12px;font-size:11px;font-weight:600;cursor:pointer;white-space:nowrap;">',
        '      Send ' + escapeHtml(p.name),
        '    </button>',
        '  </div>',
        (sym ? '  <div class="cp-param-readback" id="cp-param-readback-' + p.index + '" style="font-size:11px;margin-top:4px"><span style="color:var(--muted)">Readback (' + escapeHtml(sym) + '): </span><span id="cp-param-readback-val-' + p.index + '"><span style="color:var(--amber)">mapped, but not currently being read</span></span></div>' : ''),
        '</div>'
      ].join('');
    }).join('');

    branchEl.innerHTML = html;

    // Attach listeners
    params.forEach(function(p) {
      var input = q('cp-param-input-' + p.index);
      var btn = q('cp-param-send-btn-' + p.index);

      if (input) {
        input.addEventListener('focus', function() {
          if (_els.cmdIndex) _els.cmdIndex.value = p.index;
          if (_els.cmdValue) _els.cmdValue.value = input.value;
          updateReadbackState();
        });
        input.addEventListener('input', function() {
          if (_els.cmdIndex) _els.cmdIndex.value = p.index;
          if (_els.cmdValue) _els.cmdValue.value = input.value;
        });
        input.addEventListener('keydown', function(ev) {
          if (ev.key === 'Enter') {
            ev.preventDefault();
            submitParam(cmdId, p.index);
          }
        });
      }

      if (btn) {
        btn.addEventListener('click', function() {
          submitParam(cmdId, p.index);
        });
      }
    });

    if (params.length > 0 && _els.cmdIndex) {
      _els.cmdIndex.value = params[0].index;
    }
  }

  function updateRangeDisplay() {
    var cmdId = parseInt(_els.cmdIdSelect.value, 10) || 0;
    var reg = getRegistryEntry(cmdId);
    var idx = _els.cmdIndex;
    if (idx) {
      idx.max = reg.indexMax || 0;
    }
    if (_els.rangeDisplay) {
      _els.rangeDisplay.innerHTML =
        '<strong>' + reg.name + '</strong> \u00B7 ' + hexId(cmdId) +
        ' \u00B7 range ' + reg.min + ' \u2013 ' + reg.max + (reg.unit ? ' ' + reg.unit : '') +
        '<br><span style="color:' + (SAFETY_CLASS_COLORS[reg.safetyClass] || 'var(--muted)') + '">' +
        SAFETY_CLASS_LABELS[reg.safetyClass] + '</span> \u00B7 ' +
        escapeHtml(reg.precondition) +
        ' \u00B7 index: ' + escapeHtml(reg.indexDesc);
    }
    if (_els.cmdValue) {
      _els.cmdValue.min = reg.min;
      _els.cmdValue.max = reg.max;
    }
    renderBranchingForm();
    updateReadbackState();
  }

  // ── Quick Command Buttons ───────────────────────────────────────────────
  function safetyClassClass(cls) {
    return 'cp-qc-' + (cls || 'default');
  }

  function buildQuickCommands() {
    return QUICK_COMMANDS.map(function (qc) {
      var colorClass = safetyClassClass(qc.safetyClass);
      var dot = '<span class="cp-safety-dot" style="background:' +
                (SAFETY_CLASS_COLORS[qc.safetyClass] || 'var(--muted)') +
                '"></span>';
      return '<button class="cp-qc-btn ' + colorClass + '" ' +
             'data-cmdid="' + qc.cmdId + '" data-idx="' + qc.index + '" data-val="' + qc.value + '" ' +
             'data-cp-id="' + qc.id + '" ' +
             'title="' + escapeHtml(getCommandName(qc.cmdId) + ' = ' + qc.value) + '">' +
             dot + qc.icon + '<span>' + qc.label + '</span></button>';
    }).join('');
  }

  function wireQuickCommands() {
    document.querySelectorAll('.cp-qc-btn').forEach(function (btn) {
      btn.addEventListener('click', function () {
        var cmdId = parseInt(btn.getAttribute('data-cmdid'), 10);
        var idx = parseInt(btn.getAttribute('data-idx'), 10);
        var val = parseFloat(btn.getAttribute('data-val'));
        var cpId = btn.getAttribute('data-cp-id');
        var def = QUICK_COMMANDS.filter(function (q) { return q.id === cpId; })[0];

        // EKF Reset needs confirmation + disarm
        if (def && def.needsDisarm) {
          var arm = armStatusFromState(_state);
          if (arm.armed) {
            alert('Cannot reset EKF while drone is ARMED. Disarm first.');
            return;
          }
          if (!confirm('Reset EKF now? This will reinitialize attitude and position estimates.')) return;
        }
        submitCommand(cmdId, idx, val);
      });
    });
  }

  // ── Virtual RC Panel (0x06) ────────────────────────────────────────────
  function buildVirtualRCPanel() {
    return [
      '<div id="cp-vrc-panel" class="cp-vrc-panel" style="display:none">',
      '  <div style="font-size:10px;color:var(--muted);margin-bottom:6px;text-transform:uppercase;letter-spacing:0.05em;">Virtual RC (SDK only)</div>',
      '  <div id="cp-vrc-warning" class="cp-vrc-warn"></div>',
      '  <div class="cp-vrc-sliders">',
      '    <div class="cp-vrc-slider-row"><label>CH0</label><input type="range" min="-1" max="1" step="0.01" value="0" id="cp-vrc-0" /><span id="cp-vrc-val-0">0.00</span></div>',
      '    <div class="cp-vrc-slider-row"><label>CH1</label><input type="range" min="-1" max="1" step="0.01" value="0" id="cp-vrc-1" /><span id="cp-vrc-val-1">0.00</span></div>',
      '    <div class="cp-vrc-slider-row"><label>CH2</label><input type="range" min="-1" max="1" step="0.01" value="0" id="cp-vrc-2" /><span id="cp-vrc-val-2">0.00</span></div>',
      '    <div class="cp-vrc-slider-row"><label>CH3</label><input type="range" min="-1" max="1" step="0.01" value="0" id="cp-vrc-3" /><span id="cp-vrc-val-3">0.00</span></div>',
      '    <div class="cp-vrc-slider-row"><label>CH4</label><input type="range" min="-1" max="1" step="0.01" value="0" id="cp-vrc-4" /><span id="cp-vrc-val-4">0.00</span></div>',
      '  </div>',
      '  <div class="cp-vrc-actions">',
      '    <button id="cp-vrc-enable" class="cp-btn-ok">Enable Virtual RC</button>',
      '    <button id="cp-vrc-disable" class="cp-btn-warn">Disable Virtual RC</button>',
      '    <button id="cp-vrc-center">Center All</button>',
      '  </div>',
      '  <div style="font-size:10px;color:var(--muted);margin-top:6px">',
      '    Note: stick injection only takes effect while drone is in FlyMode=SDK.',
      '    Any movement re-submits the value for the channel touched.',
      '  </div>',
      '</div>'
    ].join('');
  }

  function wireVirtualRC() {
    var panel = q('cp-vrc-panel');
    var warn = q('cp-vrc-warning');
    var enableBtn = q('cp-vrc-enable');
    var disableBtn = q('cp-vrc-disable');
    var centerBtn = q('cp-vrc-center');
    if (!panel || !enableBtn) return;

    // Debounced send per channel
    var sendTimer = {};
    function scheduleSend(chIdx) {
      clearTimeout(sendTimer[chIdx]);
      sendTimer[chIdx] = setTimeout(function () {
        if (!_vrcEnabled) return;
        submitCommand(0x06, chIdx, _vrcValues[chIdx]);
      }, 80);
    }
    for (var i = 0; i < 5; i++) {
      (function (ch) {
        var slider = q('cp-vrc-' + ch);
        var out = q('cp-vrc-val-' + ch);
        if (!slider) return;
        slider.addEventListener('input', function () {
          var v = parseFloat(slider.value);
          _vrcValues[ch] = v;
          out.textContent = v.toFixed(2);
          scheduleSend(ch);
        });
      })(i);
    }

    enableBtn.addEventListener('click', function () {
      var arm = armStatusFromState(_state);
      if (!arm.sdk) {
        warn.innerHTML = '<span style="color:var(--red)">\u26A0 FlyMode is not SDK. Take SDK authority first (cmd 0x0E).</span>';
        warn.style.display = 'block';
        return;
      }
      if (arm.armed) {
        warn.innerHTML = '<span style="color:var(--amber)">\u26A0 Warning: issuing sticks while ARMED will move the drone.</span>';
        warn.style.display = 'block';
      } else {
        warn.innerHTML = '';
        warn.style.display = 'none';
      }
      _vrcEnabled = true;
      // Submit zero for all channels first to initialize
      for (var ch = 0; ch < 5; ch++) {
        submitCommand(0x06, ch, _vrcValues[ch] || 0);
      }
    });

    disableBtn.addEventListener('click', function () {
      _vrcEnabled = false;
      warn.innerHTML = '<span style="color:var(--muted)">Virtual RC disabled. RC sticks (or 0x04 recover) take over.</span>';
      warn.style.display = 'block';
      // Send neutral sticks one more time before letting go
      for (var ch2 = 0; ch2 < 5; ch2++) {
        submitCommand(0x06, ch2, 0);
      }
    });

    centerBtn.addEventListener('click', function () {
      for (var c = 0; c < 5; c++) {
        _vrcValues[c] = 0;
        var s = q('cp-vrc-' + c);
        var v = q('cp-vrc-val-' + c);
        if (s) s.value = 0;
        if (v) v.textContent = '0.00';
        if (_vrcEnabled) submitCommand(0x06, c, 0);
      }
    });
  }

  function refreshVirtualRCPanel() {
    var panel = q('cp-vrc-panel');
    if (!panel) return;
    var arm = armStatusFromState(_state);
    // Show the panel only when SDK mode is active (or when the user toggles "show")
    var storedVis = (function () {
      try { return localStorage.getItem('gs_vrc_visible'); } catch (_) { return null; }
    })();
    var showOverride = storedVis === '1';
    var show = arm.sdk || showOverride;
    panel.style.display = show ? '' : 'none';
    // update inline warning if SDK turned off
    var warn = q('cp-vrc-warning');
    if (warn && !arm.sdk && !showOverride) {
      warn.innerHTML = '<span style="color:var(--muted)">Hidden: SDK mode inactive.</span>';
    }
  }

  // ── Bench Mode toggle (0x07) ───────────────────────────────────────────
  function buildBenchModePanel() {
    return [
      '<div class="cp-section">',
      '  <div style="font-size:10px;color:var(--muted);margin-bottom:6px;text-transform:uppercase;letter-spacing:0.05em;">Bench Mode</div>',
      '  <div style="display:flex;gap:8px;flex-wrap:wrap">',
      '    <button id="cp-bench-on" class="cp-btn-ok">Bench Mode ON</button>',
      '    <button id="cp-bench-off" class="cp-btn-warn">Bench Mode OFF</button>',
      '    <span id="cp-bench-state" class="cp-bench-pill"></span>',
      '  </div>',
      '  <div style="font-size:10px;color:var(--muted);margin-top:4px">',
      '    Required for motor bench output (0x16). Drone must be disarmed.',
      '  </div>',
      '</div>'
    ].join('');
  }

  function wireBenchMode() {
    var onBtn = q('cp-bench-on');
    var offBtn = q('cp-bench-off');
    if (!onBtn) return;
    onBtn.addEventListener('click', function () {
      if (!confirm('Enable Bench Mode?\n\nMotors will become controllable from the dashboard.\nMake sure propellers are REMOVED.')) return;
      var arm = armStatusFromState(_state);
      if (arm.armed) {
        alert('Cannot enable Bench Mode while ARMED.');
        return;
      }
      submitCommand(0x07, 0, 1);
      var pill = q('cp-bench-state');
      if (pill) pill.innerHTML = '<span style="color:var(--amber)">\u25CF Bench Mode: REQUEST ON</span>';
    });
    offBtn.addEventListener('click', function () {
      submitCommand(0x07, 0, 0);
      var pill = q('cp-bench-state');
      if (pill) pill.innerHTML = '<span style="color:var(--muted)">\u25CB Bench Mode: REQUEST OFF</span>';
    });
  }

  function refreshBenchModePill() {
    var pill = q('cp-bench-state');
    if (!pill) return;
    // Heuristic: try reading bench flag from streams if present
    var benchOn = null;
    if (_state && _state.streams) {
      var s0 = _state.streams[0] || _state.streams['0'];
      if (s0 && s0.values) {
        if (typeof s0.values.bench_mode === 'number') benchOn = s0.values.bench_mode !== 0;
        else if (typeof s0.values.ch15 === 'number') benchOn = (s0.values.ch15 & 0x01) !== 0;
      }
    }
    if (benchOn === true) {
      pill.innerHTML = '<span style="color:var(--green)">\u25CF Bench Mode: ACTIVE</span>';
    } else if (benchOn === false) {
      pill.innerHTML = '<span style="color:var(--muted)">\u25CB Bench Mode: inactive</span>';
    } else {
      pill.innerHTML = '<span style="color:var(--amber)">\u25CB Bench Mode: NOT PUBLISHED</span>';
    }
  }

  // ── Navigation Paths (0x0A, 0x0B, 0x0C, 0x11) ──────────────────────────
  function buildNavPaths() {
    var rows = NAV_PATHS.map(function (p) {
      return '<div class="cp-nav-row">' +
             '  <span class="cp-nav-name">' + p.name + '</span>' +
             '  <button data-nav-id="' + p.id + '" data-act="start" class="cp-btn-ok cp-nav-start">Start</button>' +
             '  <button data-nav-id="' + p.id + '" data-act="stop" class="cp-btn-warn cp-nav-stop">Stop</button>' +
             '  <span class="cp-nav-id">' + hexId(p.cmdId) + '</span>' +
             '</div>';
    }).join('');
    return [
      '<div class="cp-section">',
      '  <div style="font-size:10px;color:var(--muted);margin-bottom:6px;text-transform:uppercase;letter-spacing:0.05em;">Navigation Paths</div>',
      '  <div class="cp-nav-help" style="font-size:10px;color:var(--muted);margin-bottom:6px">',
      '    Requires SDK authority. Uses cmd 0x0A (TWC), 0x0B (Sin), 0x0C (Circle), 0x11 (Fig-8).',
      '  </div>',
      '  <div class="cp-nav-grid">' + rows + '</div>',
      '</div>'
    ].join('');
  }

  function wireNavPaths() {
    document.querySelectorAll('[data-nav-id]').forEach(function (btn) {
      btn.addEventListener('click', function () {
        var id = btn.getAttribute('data-nav-id');
        var act = btn.getAttribute('data-act');
        var def = NAV_PATHS.filter(function (p) { return p.id === id; })[0];
        if (!def) return;
        var arm = armStatusFromState(_state);
        if (!arm.sdk) {
          if (!confirm('Drone is not in SDK mode. Issue this navigation command anyway?')) return;
        }
        var idx = (act === 'start') ? def.startIndex : def.stopIndex;
        var val = (act === 'start') ? def.startValue : def.stopValue;
        submitCommand(def.cmdId, idx, val);
      });
    });
  }

  // ── ARM Status display ──────────────────────────────────────────────────
  function buildArmBadge() {
    return [
      '<div class="cp-arm-bar">',
      '  <span id="cp-arm-badge" class="cp-arm-badge cp-arm-unknown">ARM: ?</span>',
      '  <span id="cp-sdk-badge"  class="cp-sdk-badge">SDK: ?</span>',
      '  <span id="cp-bench-mini" class="cp-bench-mini"></span>',
      '  <span style="flex:1"></span>',
      '  <span id="cp-session-mini" style="color:var(--muted);font-size:10px"></span>',
      '</div>'
    ].join('');
  }

  function refreshArmBadge() {
    var el = q('cp-arm-badge');
    if (!el) return;
    var s = armStatusFromState(_state);
    var label = s.label;
    var cls = 'cp-arm-unknown';
    if (label.indexOf('ARMED') !== -1) cls = 'cp-arm-armed';
    else if (label === 'SDK') cls = 'cp-arm-sdk';
    else if (label === 'DISARMED') cls = 'cp-arm-disarmed';
    el.className = 'cp-arm-badge ' + cls;
    el.innerHTML = label;

    var sdk = q('cp-sdk-badge');
    if (sdk) {
      if (s.sdk === null) {
        sdk.className = 'cp-sdk-badge cp-sdk-unknown';
        sdk.innerHTML = 'SDK: ?';
        sdk.style.color = 'var(--amber)';
      } else {
        sdk.className = 'cp-sdk-badge ' + (s.sdk ? 'cp-sdk-on' : 'cp-sdk-off');
        sdk.innerHTML = 'SDK: ' + (s.sdk ? '\u2713' : '\u2717');
        sdk.style.color = '';
      }
    }
    var sessMini = q('cp-session-mini');
    if (sessMini) {
      sessMini.textContent = _state && _state.session_id ? 'session: ' + _state.session_id.slice(0, 8) : '';
    }
    // Block critical qc buttons when armed
    refreshAllBlockedButtons();
  }

  function refreshAllBlockedButtons() {
    document.querySelectorAll('.cp-qc-btn').forEach(function (btn) {
      var cpId = btn.getAttribute('data-cp-id');
      var def = QUICK_COMMANDS.filter(function (q) { return q.id === cpId; })[0];
      if (!def) return;
      // Skip 0x0D (Abort All) — always allowed
      if (def.cmdId === 0x0D) return;
      var blocked = !canIssueCriticalCommand(def.cmdId);
      btn.disabled = blocked;
      btn.style.opacity = blocked ? '0.45' : '';
      btn.style.cursor = blocked ? 'not-allowed' : 'pointer';
      btn.title = blocked ? 'Blocked: drone ARMED or wrong mode' : (getCommandName(def.cmdId) + ' = ' + def.value);
    });
  }

  function wireOfBias() {
    var btnFixed = q('cp-of-fixed');
    var btnEma = q('cp-of-ema');
    var btnEkf = q('cp-of-ekf');
    var btnFreeze = q('cp-of-freeze');
    
    if (btnFixed) btnFixed.addEventListener('click', function() { submitCommand(0x1E, 0, 0); });
    if (btnEma) btnEma.addEventListener('click', function() { submitCommand(0x1E, 0, 1); });
    if (btnEkf) btnEkf.addEventListener('click', function() { submitCommand(0x1E, 0, 2); });
    
    if (btnFreeze) btnFreeze.addEventListener('click', function() {
      // Toggle freeze: read current from state if available, else 1
      var val = 1;
      var s1 = _state && _state.streams ? (_state.streams[1] || _state.streams['1']) : null;
      if (s1 && s1.values && ('slot1.g_of_bias_ema_freeze' in s1.values)) {
        val = s1.values['slot1.g_of_bias_ema_freeze'] >= 0.5 ? 0 : 1;
      }
      submitCommand(0x1E, 1, val);
    });
  }

  // ── History Rendering ───────────────────────────────────────────────────
  function statusIcon(s) {
    if (s === 'applied')   return '<span style="color:var(--green)">\u2713</span>';
    if (s === 'rejected')  return '<span style="color:var(--red)">\u2717</span>';
    if (s === 'submitted') return '<span style="color:var(--muted)">\u21AA</span>';
    if (s === 'error')     return '<span style="color:var(--red)">!</span>';
    return '<span style="color:var(--muted)">\u2014</span>';
  }

  function statusRowClass(s) {
    if (s === 'applied')   return 'cp-hist-applied';
    if (s === 'rejected')  return 'cp-hist-rejected';
    if (s === 'submitted') return 'cp-hist-submitted';
    if (s === 'error')     return 'cp-hist-rejected';
    return '';
  }

  function renderHistory() {
    var el = _els.historyList;
    if (!el) return;

    var visible = _history.filter(function (h) {
      if (_historyFilter === 'all') return true;
      return h.status === _historyFilter;
    });

    if (visible.length === 0) {
      el.innerHTML = '<div class="cp-no-history">' +
        (_history.length === 0 ? 'No commands yet' : 'No matching commands (filter: ' + _historyFilter + ')') +
        '</div>';
      return;
    }

    // Group by session_id
    var bySession = {};
    var sessionOrder = [];
    visible.forEach(function (h) {
      var sid = h.session_id || 'unknown';
      if (!bySession[sid]) {
        bySession[sid] = [];
        sessionOrder.push(sid);
      }
      bySession[sid].push(h);
    });

    var html = sessionOrder.map(function (sid) {
      var rows = bySession[sid].map(function (h) {
        return '<div class="cp-hist-row ' + statusRowClass(h.status) + '" title="' +
               escapeHtml((h.detail || '') + ' @ ' + (h.ts || '')) + '">' +
               '<span class="cp-hist-time">' + escapeHtml(h.ts || '') + '</span>' +
               '<span class="cp-hist-name">' + escapeHtml(h.cmdName || '') + '</span>' +
               '<span class="cp-hist-id">' + escapeHtml(h.id || '') + '</span>' +
               '<span class="cp-hist-idx">[' + (h.index != null ? h.index : 0) + ']</span>' +
               '<span class="cp-hist-val">' + Number(h.value).toFixed(2) + '</span>' +
               '<span class="cp-hist-status">' + statusIcon(h.status) + '</span>' +
               '</div>';
      }).join('');
      return '<div class="cp-hist-session">' +
             '<div class="cp-hist-session-hdr">session ' + escapeHtml(sid.slice(0, 12)) + '</div>' +
             rows +
             '</div>';
    }).join('');

    el.innerHTML = html;
  }

  function setHistoryFilter(f) {
    _historyFilter = f;
    try { localStorage.setItem(HISTORY_FILTER_KEY, f); } catch (_) {}
    // Update chip styles
    document.querySelectorAll('.cp-hist-filter').forEach(function (chip) {
      var v = chip.getAttribute('data-filter');
      chip.classList.toggle('cp-hist-filter-on', v === f);
    });
    renderHistory();
  }

  function wireHistoryFilters() {
    document.querySelectorAll('.cp-hist-filter').forEach(function (chip) {
      chip.addEventListener('click', function () {
        var v = chip.getAttribute('data-filter');
        setHistoryFilter(v);
      });
    });
  }

  function toggleHistory() {
    var el = _els.historyPanel;
    if (!el) return;
    var isHidden = el.style.display === 'none';
    el.style.display = isHidden ? '' : 'none';
    _els.historyToggle.textContent = isHidden ? 'Hide History' : 'Show History';
    try { localStorage.setItem('gs_cmd_hist_visible', isHidden ? '1' : '0'); } catch (_) {}
  }

  function clearHistory() {
    if (!confirm('Clear command history?')) return;
    _history = [];
    saveHistory();
    renderHistory();
  }

  // ── Build HTML ───────────────────────────────────────────────────────────
  function buildHTML() {
    return [
      '<style>',
      /* ARM badge bar */
      '.cp-arm-bar { display:flex; align-items:center; gap:8px; margin-bottom:10px; padding:6px 8px; background:var(--bg); border:1px solid var(--border); border-radius:5px; }',
      '.cp-arm-badge, .cp-sdk-badge, .cp-bench-mini { display:inline-flex; align-items:center; gap:4px; padding:3px 9px; border-radius:12px; font-size:11px; font-weight:700; letter-spacing:0.04em; }',
      '.cp-arm-armed    { background:rgba(233,69,96,0.18); color:var(--red); }',
      '.cp-arm-disarmed { background:rgba(78,204,163,0.15); color:var(--green); }',
      '.cp-arm-sdk      { background:rgba(74,158,255,0.18); color:#4a9eff; }',
      '.cp-arm-unknown  { background:rgba(136,136,170,0.15); color:var(--muted); }',
      '.cp-sdk-on  { background:rgba(74,158,255,0.2); color:#4a9eff; }',
      '.cp-sdk-off { background:rgba(136,136,170,0.15); color:var(--muted); }',
      '.cp-bench-mini { background:rgba(245,166,35,0.15); color:var(--amber); }',
      /* Quick Commands */
      '.cp-qc-grid { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 14px; }',
      '.cp-qc-btn {',
      '  display: inline-flex; align-items: center; gap: 5px;',
      '  padding: 5px 10px; border-radius: 4px; border: 1px solid var(--border);',
      '  background: var(--bg); color: var(--text); font-size: 11px; font-weight: 600;',
      '  cursor: pointer; transition: all 0.15s;',
      '}',
      '.cp-qc-btn:hover:not(:disabled) { border-color: var(--accent); }',
      '.cp-qc-btn:active:not(:disabled) { transform: scale(0.97); }',
      '.cp-qc-critical    { border-color: var(--red); }',
      '.cp-qc-critical:hover:not(:disabled) { background: var(--red); color: var(--bg); }',
      '.cp-qc-boundary    { border-color: var(--amber); }',
      '.cp-qc-boundary:hover:not(:disabled) { background: var(--amber); color: var(--bg); }',
      '.cp-qc-operational { border-color: #4a9eff; }',
      '.cp-qc-operational:hover:not(:disabled) { background: #4a9eff; color: white; }',
      '.cp-qc-diagnostic  { border-color: var(--green); }',
      '.cp-qc-diagnostic:hover:not(:disabled) { background: var(--green); color: var(--bg); }',
      '.cp-qc-default:hover:not(:disabled) { background: var(--accent); }',
      '.cp-safety-dot { display:inline-block; width:8px; height:8px; border-radius:50%; margin-right:1px; }',
      /* Manual Form */
      '.cp-form { display: grid; grid-template-columns: 70px 1fr; gap: 6px 10px; align-items: center; }',
      '.cp-form label { font-size: 11px; color: var(--muted); }',
      '.cp-form select, .cp-form input {',
      '  background: var(--bg); border: 1px solid var(--border); color: var(--text);',
      '  border-radius: 4px; padding: 5px 8px; font-family: Consolas, monospace; font-size: 12px;',
      '}',
      '.cp-form select:focus, .cp-form input:focus { outline: none; border-color: var(--accent); }',
      '.cp-form select { cursor: pointer; }',
      '.cp-form select option.cp-opt-critical    { color: var(--red); }',
      '.cp-form select option.cp-opt-boundary    { color: var(--amber); }',
      '.cp-form select option.cp-opt-operational { color: #4a9eff; }',
      '.cp-form select option.cp-opt-diagnostic  { color: var(--green); }',
      '.cp-range { font-size: 10px; color: var(--muted); grid-column: 1 / -1; margin-top: -4px; line-height:1.35 }',
      '.cp-submit { grid-column: 1 / -1; background: var(--accent); color: var(--text);',
      '  border: none; border-radius: 4px; padding: 7px; font-size: 12px; font-weight: 600;',
      '  cursor: pointer; transition: opacity 0.15s; }',
      /* Param branching card styles */
      '.cp-param-card { border: 1px solid var(--border); border-radius: 4px; padding: 8px 10px; background: rgba(255,255,255,0.02); margin-bottom: 8px; }',
      '.cp-param-card:hover { border-color: rgba(255,255,255,0.12); }',
      '.cp-param-range { font-size: 10px; color: var(--muted); font-family: Consolas, monospace; }',
      '.cp-param-input { background: var(--bg); border: 1px solid var(--border); color: var(--text); border-radius: 4px; padding: 5px 8px; font-family: Consolas, monospace; font-size: 12px; }',
      '.cp-param-input:focus { outline: none; border-color: var(--accent); }',
      '.cp-raw-banner { font-size: 10px; color: var(--amber); margin-bottom: 8px; padding: 4px 8px; background: rgba(245,166,35,0.1); border-radius: 3px; border-left: 2px solid var(--amber); }',
      /* Result Box */
      '.cp-result-box {',
      '  margin-top: 10px; padding: 8px 10px; border-radius: 4px;',
      '  background: var(--bg); border: 1px solid var(--border);',
      '  border-left: 3px solid var(--muted); font-size: 12px;',
      '  display: none;',
      '}',
      /* Generic buttons */
      '.cp-btn-ok, .cp-btn-warn, button.cp-btn, .cp-vrc-actions button { background: var(--bg); color: var(--text);',
      '  border:1px solid var(--border); padding:5px 11px; border-radius:4px; font-size:11px; font-weight:600; cursor:pointer; }',
      '.cp-btn-ok { border-color: var(--green); color: var(--green); }',
      '.cp-btn-ok:hover:not(:disabled) { background: var(--green); color: var(--bg); }',
      '.cp-btn-warn { border-color: var(--amber); color: var(--amber); }',
      '.cp-btn-warn:hover:not(:disabled) { background: var(--amber); color: var(--bg); }',
      '.cp-btn-danger { border-color: var(--red); color: var(--red); }',
      '.cp-btn-danger:hover:not(:disabled) { background: var(--red); color: var(--bg); }',
      /* Virtual RC panel */
      '.cp-vrc-panel { padding: 8px 8px 10px; background: rgba(74,158,255,0.05); border:1px dashed #4a9eff; border-radius:5px; margin-top:10px; }',
      '.cp-vrc-warn { font-size:11px; margin-bottom:6px; display:none }',
      '.cp-vrc-sliders { display: flex; flex-direction: column; gap: 6px; margin-bottom: 8px; }',
      '.cp-vrc-slider-row { display:grid; grid-template-columns: 32px 1fr 50px; gap:8px; align-items:center; font-size:11px; }',
      '.cp-vrc-slider-row label { color:var(--muted); font-family:Consolas, monospace; }',
      '.cp-vrc-slider-row input[type=range] { accent-color: #4a9eff; }',
      '.cp-vrc-slider-row span { font-family:Consolas, monospace; color: var(--green); }',
      '.cp-vrc-actions { display:flex; gap:6px; flex-wrap:wrap }',
      /* Bench pill */
      '.cp-bench-pill { font-size:11px; padding: 4px 8px; }',
      /* Nav grid */
      '.cp-nav-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(180px, 1fr)); gap: 6px; }',
      '.cp-nav-row { display:flex; align-items:center; gap:6px; padding:5px 8px; background:var(--bg); border:1px solid var(--border); border-radius:4px; font-size:11px; }',
      '.cp-nav-name { flex:1; font-weight:600; }',
      '.cp-nav-id { color: var(--muted); font-family:Consolas, monospace; font-size:10px; }',
      /* History */
      '.cp-history-toggle {',
      '  display: flex; align-items: center; gap: 6px;',
      '  margin-top: 12px; padding: 6px 0; border-top: 1px solid var(--border);',
      '  cursor: pointer; color: var(--muted); font-size: 11px;',
      '}',
      '.cp-history-toggle:hover { color: var(--text); }',
      '.cp-history-toggle::before { content: "\\25B6"; font-size: 10px; }',
      '.cp-history-panel { display: none; }',
      '.cp-hist-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px; gap:6px; flex-wrap:wrap }',
      '.cp-hist-title { font-size: 10px; color: var(--muted); text-transform: uppercase; letter-spacing: 0.05em; }',
      '.cp-hist-clear { background: none; border: 1px solid var(--border); color: var(--muted);',
      '  font-size: 10px; padding: 2px 6px; border-radius: 3px; cursor: pointer; }',
      '.cp-hist-clear:hover { border-color: var(--red); color: var(--red); }',
      '.cp-hist-filters { display: inline-flex; gap: 4px; }',
      '.cp-hist-filter { cursor:pointer; padding:2px 8px; border:1px solid var(--border); border-radius:10px; font-size:10px; color:var(--muted); background:var(--bg); }',
      '.cp-hist-filter-on { color: var(--accent); border-color: var(--accent); background: rgba(15,52,96,0.4); }',
      '.cp-hist-list { max-height: 240px; overflow-y: auto; }',
      '.cp-hist-session { margin-bottom: 6px; }',
      '.cp-hist-session-hdr { font-size:9px; color:var(--muted); padding:3px 6px; background: rgba(255,255,255,0.03); border-radius:3px; letter-spacing:0.06em; text-transform:uppercase; margin-bottom:2px; }',
      '.cp-hist-row { display: grid; grid-template-columns: 60px 1fr 50px 26px 50px 18px; gap: 4px;',
      '  padding: 4px 6px; border-radius: 3px; font-size: 11px; font-family: Consolas, monospace;',
      '  align-items: center; }',
      '.cp-hist-row:hover { background: rgba(255,255,255,0.03); }',
      '.cp-hist-applied { border-left: 2px solid var(--green); }',
      '.cp-hist-rejected { border-left: 2px solid var(--red); }',
      '.cp-hist-submitted { border-left: 2px dashed var(--muted); }',
      '.cp-hist-time { color: var(--muted); font-size: 10px; }',
      '.cp-hist-name { font-weight: 600; }',
      '.cp-hist-id { color: var(--muted); font-size:10px; }',
      '.cp-hist-idx { color: var(--muted); }',
      '.cp-hist-val { color: var(--amber); }',
      '.cp-hist-status { text-align: center; }',
      '.cp-no-history { color: var(--muted); font-size: 11px; text-align: center; padding: 10px; }',
      '</style>',

      // ARM/SDK bar
      buildArmBadge(),

      // Quick Commands
      '<div class="cp-section">',
      '  <div style="font-size:10px;color:var(--muted);margin-bottom:6px;text-transform:uppercase;letter-spacing:0.05em;">Quick Commands</div>',
      '  <div class="cp-qc-grid" id="cp-qc-grid">' + buildQuickCommands() + '</div>',
      '</div>',

      // Bench Mode
      buildBenchModePanel(),

      // Virtual RC
      buildVirtualRCPanel(),

      // Navigation Paths
      buildNavPaths(),

      // OF Bias Panel
      '<div class="cp-section">',
      '  <div style="font-size:10px;color:var(--muted);margin-bottom:6px;text-transform:uppercase;letter-spacing:0.05em;">OF Bias Estimator (E2)</div>',
      '  <div style="display:flex;gap:4px;margin-bottom:6px;">',
      '    <button class="cp-btn" id="cp-of-fixed">FIXED (0)</button>',
      '    <button class="cp-btn" id="cp-of-ema">EMA (1)</button>',
      '    <button class="cp-btn cp-btn-warn" id="cp-of-ekf">EKF (2)</button>',
      '    <button class="cp-btn" id="cp-of-freeze" style="margin-left:auto">Toggle Freeze</button>',
      '  </div>',
      '  <div style="font-size:11px;color:var(--muted);margin-top:4px;">',
      '    <span>Mode: </span><span id="cp-of-readback-mode" style="font-family:Consolas,monospace;font-weight:600;"><span style="color:var(--amber)">NOT PUBLISHED</span></span>',
      '    <span style="margin-left:12px;">Freeze: </span><span id="cp-of-readback-freeze" style="font-family:Consolas,monospace;font-weight:600;"><span style="color:var(--amber)">NOT PUBLISHED</span></span>',
      '  </div>',
      '</div>',

      // Parameter & Flag Widgets (AUDIT §3.1–3.2)
      buildWidgetsSection(),

      // Manual Form
      '<div class="cp-section">',
      '  <div style="font-size:10px;color:var(--muted);margin-bottom:6px;text-transform:uppercase;letter-spacing:0.05em;">Manual Command</div>',
      '  <div style="display:flex;flex-direction:column;gap:4px;margin-bottom:8px;">',
      '    <label for="cp-cmd-id" style="font-size:11px;color:var(--muted)">Command</label>',
      '    <select id="cp-cmd-id" style="background:var(--bg);border:1px solid var(--border);color:var(--text);border-radius:4px;padding:5px 8px;font-family:Consolas,monospace;font-size:12px;cursor:pointer;"></select>',
      '  </div>',
      '  <div id="cp-branching-container"></div>',
      '  <div id="cp-raw-container" style="display:none;">',
      '    <div class="cp-raw-banner">Raw index/value fallback mode</div>',
      '    <form class="cp-form" id="cp-form">',
      '      <label for="cp-cmd-idx">Index</label>',
      '      <input type="number" id="cp-cmd-idx" value="0" min="0" step="1" />',
      '      <label for="cp-cmd-val">Value</label>',
      '      <input type="number" id="cp-cmd-val" value="0" step="any" />',
      '      <div class="cp-range" id="cp-range-display">Valid range: \u2014</div>',
      '      <button type="submit" class="cp-submit" id="cp-submit">Submit Command (Raw)</button>',
      '    </form>',
      '  </div>',
      '  <div style="margin-top:6px;display:flex;justify-content:flex-end;">',
      '    <button type="button" id="cp-mode-toggle" style="background:none;border:none;color:var(--muted);font-size:10px;text-decoration:underline;cursor:pointer;">Switch to Raw Mode (Fallback)</button>',
      '  </div>',
      '  <div class="cp-readback" id="cp-readback-display" style="font-size:12px;margin-top:6px;padding:4px;border-radius:4px;background:var(--bg-lighter);">',
      '    <span style="color:var(--muted)">Readback: </span><span id="cp-readback-val">no readback mapping for this command</span>',
      '  </div>',
      '  <div class="cp-result-box" id="cp-result-box"></div>',
      '  <div id="cp-safety-warn"></div>',
      '</div>',

      // History
      '<div class="cp-history-toggle" id="cp-hist-toggle">Show History</div>',
      '<div class="cp-history-panel" id="cp-hist-panel">',
      '  <div class="cp-hist-header">',
      '    <span class="cp-hist-title">Command History</span>',
      '    <div class="cp-hist-filters">',
      '      <span class="cp-hist-filter" data-filter="all">all</span>',
      '      <span class="cp-hist-filter" data-filter="applied">applied</span>',
      '      <span class="cp-hist-filter" data-filter="rejected">rejected</span>',
      '      <span class="cp-hist-filter" data-filter="submitted">submitted</span>',
      '      <span class="cp-hist-filter" data-filter="error">error</span>',
      '    </div>',
      '    <button class="cp-hist-clear" id="cp-hist-clear">Clear</button>',
      '  </div>',
      '  <div class="cp-hist-list" id="cp-hist-list"></div>',
      '</div>',
    ].join('');
  }

  function wireOfBias() {
    var btnFixed = q('cp-of-fixed');
    var btnEma = q('cp-of-ema');
    var btnEkf = q('cp-of-ekf');
    var btnFreeze = q('cp-of-freeze');
    
    function setCmdAndSub(idx, val) {
      if (_els.cmdIdSelect) _els.cmdIdSelect.value = 0x1E;
      if (_els.cmdIndex) _els.cmdIndex.value = idx;
      if (_els.cmdValue) _els.cmdValue.value = val;
      updateRangeDisplay();
      submitCommand(0x1E, idx, val);
    }

    if (btnFixed) btnFixed.addEventListener('click', function() { setCmdAndSub(0, 0); });
    if (btnEma) btnEma.addEventListener('click', function() { setCmdAndSub(0, 1); });
    if (btnEkf) btnEkf.addEventListener('click', function() { setCmdAndSub(0, 2); });
    
    if (btnFreeze) btnFreeze.addEventListener('click', function() {
      // Toggle freeze: read current from state if available, else 1
      var val = 1;
      var s1 = _state && _state.streams ? (_state.streams[1] || _state.streams['1']) : null;
      if (s1 && s1.values) {
        var fVal = s1.values['slot1.g_of_bias_ema_freeze'];
        if (fVal === undefined) fVal = s1.values['g_of_bias_ema_freeze'];
        if (fVal !== undefined) val = fVal >= 0.5 ? 0 : 1;
      }
      setCmdAndSub(1, val);
    });
  }

  // ── History Rendering ───────────────────────────────────────────────────
  function statusIcon(s) {
    if (s === 'applied')   return '<span style="color:var(--green)">\u2713</span>';
    if (s === 'rejected')  return '<span style="color:var(--red)">\u2717</span>';
    if (s === 'error')     return '<span style="color:var(--red)">!</span>';
    if (s === 'pending')   return '<span style="color:var(--amber)">\u23F3</span>';
    return '<span style="color:var(--muted)">?</span>';
  }

  function renderHistory() {
    if (!_els.historyList) return;
    if (!_history.length) {
      _els.historyList.innerHTML = '<div class="cp-no-history">No commands issued in this session.</div>';
      return;
    }
    
    var filtered = _history.filter(function (e) {
      if (_historyFilter === 'all') return true;
      return e.status === _historyFilter;
    });

    // Group by session
    var groups = [];
    var cur = null;
    filtered.forEach(function (e) {
      if (!cur || cur.id !== e.session_id) {
        cur = { id: e.session_id, items: [] };
        groups.push(cur);
      }
      cur.items.push(e);
    });

    _els.historyList.innerHTML = groups.map(function (g) {
      var isCurrent = (_state && g.id === _state.session_id);
      var hdr = isCurrent ? 'Current Session' : 'Session ' + escapeHtml(g.id.substring(0, 8));
      var items = g.items.map(function (e) {
        var cls = 'cp-hist-row cp-hist-' + e.status;
        return '<div class="' + cls + '">' +
               '  <div class="cp-hist-time">' + (e.timeStr || '') + '</div>' +
               '  <div class="cp-hist-name" title="' + escapeHtml(e.error || e.status) + '">' +
                  escapeHtml(e.name) + '</div>' +
               '  <div class="cp-hist-id">' + escapeHtml(e.id) + '</div>' +
               '  <div class="cp-hist-idx">i=' + e.index + '</div>' +
               '  <div class="cp-hist-val">v=' + e.value + '</div>' +
               '  <div class="cp-hist-status">' + statusIcon(e.status) + '</div>' +
               '</div>';
      }).join('');
      return '<div class="cp-hist-session">' +
             '  <div class="cp-hist-session-hdr">' + escapeHtml(hdr) + '</div>' +
             items +
             '</div>';
    }).join('');
  }

  function toggleHistory() {
    if (!_els.historyPanel) return;
    var isHidden = _els.historyPanel.style.display === 'none' || _els.historyPanel.style.display === '';
    if (isHidden) {
      _els.historyPanel.style.display = 'block';
      _els.historyToggle.textContent = 'Hide History';
      try { localStorage.setItem('gs_cmd_hist_visible', '1'); } catch (_) {}
      renderHistory();
    } else {
      _els.historyPanel.style.display = 'none';
      _els.historyToggle.textContent = 'Show History';
      try { localStorage.setItem('gs_cmd_hist_visible', '0'); } catch (_) {}
    }
  }

  function clearHistory() {
    _history = [];
    saveHistory();
    renderHistory();
  }

  function setHistoryFilter(f) {
    _historyFilter = f;
    try { localStorage.setItem(HISTORY_FILTER_KEY, f); } catch (_) {}
    document.querySelectorAll('.cp-hist-filter').forEach(function (el) {
      if (el.getAttribute('data-filter') === f) {
        el.classList.add('cp-hist-filter-on');
      } else {
        el.classList.remove('cp-hist-filter-on');
      }
    });
    renderHistory();
  }

  function wireHistoryFilters() {
    document.querySelectorAll('.cp-hist-filter').forEach(function (el) {
      el.addEventListener('click', function () {
        setHistoryFilter(el.getAttribute('data-filter'));
      });
    });
  }

  // ── Populate Command Select (all 30) ───────────────────────────────────
  function populateCommandSelect() {
    var select = _els.cmdIdSelect;
    if (!select) return;
    var current = select.value;
    select.innerHTML = Object.keys(COMMAND_REGISTRY)
      .map(function (k) { return parseInt(k, 10); })
      .sort(function (a, b) { return a - b; })
      .map(function (key) {
        var reg = COMMAND_REGISTRY[key];
        var cls = 'cp-opt-' + (reg.safetyClass || 'diagnostic');
        var dot = '\u25CF';  // small filled circle, color comes from CSS class
        return '<option class="' + cls + '" value="' + key + '" ' +
               'title="[' + SAFETY_CLASS_LABELS[reg.safetyClass] + '] ' +
               escapeHtml(reg.precondition) + ' | index: ' + escapeHtml(reg.indexDesc) + '">' +
               hexId(key) + ' \u2013 ' + reg.name + '</option>';
      }).join('');
    select.innerHTML += '<option value="custom">Custom\u2026</option>';
    if (current) select.value = current;
  }

  function updateReadbackState() {
    if (!_els.readbackVal) return;
    var cmdId = parseInt(_els.cmdIdSelect.value, 10) || 0;
    var index = parseInt(_els.cmdIndex.value, 10) || 0;
    
    var symbol = null;
    if (_commandSymbols[cmdId] && _commandSymbols[cmdId][index]) {
      symbol = _commandSymbols[cmdId][index];
    }

    if (!symbol) {
      _currentSymbol = null;
      _els.readbackVal.innerHTML = '<span style="color:var(--muted)">no readback mapping for this command</span>';
      return;
    }

    if (_currentSymbol !== symbol) {
      _currentSymbol = symbol;
      _lastSubscribeNs = 0;
      fetch('/subscribe', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({slot: 1, divider: 100, ranges: [symbol]})
      }).catch(function(e) { console.warn('Readback subscribe err', e); });
    }

    renderReadbackValue();
  }

  function renderReadbackValue() {
    var s1 = _state && _state.streams ? (_state.streams[1] || _state.streams['1']) : null;
    // 2026-09-21 binding fix: g_of_bias_mode / g_of_bias_ema_freeze now
    // stream on slot 0 (auto-subscribed dashboard layout, service maps
    // them to their unprefixed spellings); slot 1 only carries them when
    // a preset explicitly subscribed the bias frame there.
    var s0 = _state && _state.streams ? (_state.streams[0] || _state.streams['0']) : null;

    // OF Bias specific readback
    var ofModeSpan = q('cp-of-readback-mode');
    var ofFreezeSpan = q('cp-of-readback-freeze');
    if (ofModeSpan && ofFreezeSpan) {
      var mVal, fVal;
      if (s1 && s1.values) {
        mVal = s1.values['slot1.g_of_bias_mode'] !== undefined ? s1.values['slot1.g_of_bias_mode'] : s1.values['g_of_bias_mode'];
        fVal = s1.values['slot1.g_of_bias_ema_freeze'] !== undefined ? s1.values['slot1.g_of_bias_ema_freeze'] : s1.values['g_of_bias_ema_freeze'];
      }
      if (mVal === undefined && s0 && s0.values) {
        mVal = s0.values['g_of_bias_mode'] !== undefined ? s0.values['g_of_bias_mode'] : s0.values['slot0.g_of_bias_mode'];
      }
      if (fVal === undefined && s0 && s0.values) {
        fVal = s0.values['g_of_bias_ema_freeze'] !== undefined ? s0.values['g_of_bias_ema_freeze'] : s0.values['slot0.g_of_bias_ema_freeze'];
      }
      if (mVal === undefined && !(s1 && s1.values) && !(s0 && s0.values)) {
        ofModeSpan.innerHTML = '<span style="color:var(--amber)">NOT PUBLISHED</span>';
        ofFreezeSpan.innerHTML = '<span style="color:var(--amber)">NOT PUBLISHED</span>';
      } else {
        ofModeSpan.innerHTML = mVal !== undefined ? (mVal===0?'FIXED':mVal===1?'EMA':mVal===2?'EKF':String(mVal)) : '<span style="color:var(--amber)">NOT PUBLISHED</span>';
        ofFreezeSpan.innerHTML = fVal !== undefined ? String(fVal) : '<span style="color:var(--amber)">NOT PUBLISHED</span>';
      }
    }

    // Update per-param readback indicators in the branching form
    var cmdId = parseInt(_els.cmdIdSelect ? _els.cmdIdSelect.value : 0, 10) || 0;
    var params = COMMAND_PARAMS_REGISTRY[cmdId] || [];
    params.forEach(function(p) {
      var sym = p.symbol || (_commandSymbols[cmdId] ? _commandSymbols[cmdId][p.index] : null);
      if (!sym) return;
      var el = q('cp-param-readback-val-' + p.index);
      if (!el) return;
      
      if (!s1 || !s1.values || (!(('slot1.' + sym) in s1.values) && !(sym in s1.values))) {
        el.innerHTML = '<span style="color:var(--amber)">mapped, but not currently being read</span>';
        return;
      }
      var pVal = s1.values['slot1.' + sym];
      if (pVal === undefined) pVal = s1.values[sym];
      
      var isStale = false;
      var ageStr = '';
      if (s1.last_update_ns && _state.now_ns) {
        var ageS = (_state.now_ns - s1.last_update_ns) / 1e9;
        if (ageS > 2.0) {
          isStale = true;
          ageStr = ' (read ' + ageS.toFixed(1) + 's ago)';
        }
      }
      if (isStale) {
        el.innerHTML = '<span style="color:var(--amber)">' + pVal + ageStr + '</span>';
      } else {
        el.innerHTML = '<span style="color:var(--green);font-weight:bold;">' + pVal + '</span>';
      }
    });

    if (!_els.readbackVal || !_currentSymbol) return;
    
    var rawName = _currentSymbol; 
    
    if (!s1 || !s1.values || (!(('slot1.' + _currentSymbol) in s1.values) && !(rawName in s1.values))) {
      _els.readbackVal.innerHTML = '<span style="color:var(--amber)">mapped, but not currently being read</span>';
      return;
    }

    var val = s1.values['slot1.' + _currentSymbol];
    if (val === undefined) val = s1.values[rawName];

    var ageStr = '';
    var isStale = false;
    if (s1.last_update_ns && _state.now_ns) {
      var ageS = (_state.now_ns - s1.last_update_ns) / 1e9;
      if (ageS > 2.0) {
        isStale = true;
        ageStr = ' (read ' + ageS.toFixed(1) + 's ago)';
      }
    }
    
    if (isStale) {
      _els.readbackVal.innerHTML = '<span style="color:var(--amber)">' + val + ageStr + '</span>';
    } else {
      _els.readbackVal.innerHTML = '<span style="color:var(--green);font-weight:bold;">' + val + '</span>';
    }
  }

  // ── Parameter & Flag Widgets (AUDIT_2026-09-21 §3.1–3.2) ────────────────
  // Value-parameter commands are surfaced as permanent widgets (numeric input,
  // plus a slider when the catalog defines a finite range) that show the
  // CURRENT value (from telemetry, tolerant slot-prefixed lookup) and the
  // value to SET, with an explicit Send. Flag commands are surfaced as
  // checkboxes/toggles whose state follows telemetry (never optimistic).
  //
  // Every send routes through submitCommand(), so the existing arm/precondition
  // gates fail closed exactly as before. The render/poll loop is untouched:
  // a POST that rejects or drops the link surfaces as a result status and the
  // loop keeps running (Bug 1, commit 7dea39f, must not regress).
  var WIDGET_VALUE_COMMANDS = [0x01, 0x02, 0x05, 0x08, 0x09, 0x12, 0x15];
  var WIDGET_FLAG_COMMANDS  = [0x0E, 0x0F];
  // Best-effort readback symbol per (cmd, param index) for the current-value
  // display. Absent mapping -> — / not published, never 0. Refined by the
  // /api/contract fetch at mount (which fills _commandSymbols).
  var _widgetReadback = {
    0x0E: { 0: 'status.rc_authority' }
  };
  // Tolerant slot-prefix matcher: matches ``slot<N>.<key>``.
  var SLOT_PREFIX_RE = /^slot\d+\.\s*(.+)$/;

  function findParam(cmdId, paramIndex) {
    var params = COMMAND_PARAMS_REGISTRY[cmdId] || [];
    for (var i = 0; i < params.length; i++) {
      if (params[i].index === paramIndex) return params[i];
    }
    return null;
  }

  // Params for a command; commands not modelled in the catalog fall back to a
  // single synthesized index-0 setter so the widget still renders.
  function widgetParams(cmdId) {
    var ps = COMMAND_PARAMS_REGISTRY[cmdId];
    if (ps && ps.length) return ps;
    return [{ index: 0, name: 'set', unit: 'bool', min_val: 0, max_val: 1 }];
  }

  function widgetSymbol(cmdId, p) {
    if (p && p.symbol) return p.symbol;
    if (_commandSymbols[cmdId] && _commandSymbols[cmdId][p.index]) return _commandSymbols[cmdId][p.index];
    if (_widgetReadback[cmdId] && _widgetReadback[cmdId][p.index]) return _widgetReadback[cmdId][p.index];
    return null;
  }

  // Tolerant, slot-prefix-aware telemetry lookup (matches time-series-panel's
  // approach): a bare key or any ``slot<N>.<key>`` spelling is found.
  function streamValue(key) {
    var state = _state;
    if (!state || !state.streams) return undefined;
    var slotKeys = Object.keys(state.streams);
    for (var i = 0; i < slotKeys.length; i++) {
      var s = state.streams[slotKeys[i]];
      if (!s || !s.values) continue;
      var v = s.values;
      if (key in v) return v[key];
      for (var k in v) {
        var m = SLOT_PREFIX_RE.exec(k);
        if (m && m[1] === key) return v[k];
      }
    }
    return undefined;
  }

  function isSelectorParam(p) {
    return (p.unit === 'enum' || p.unit === 'index' || p.unit === 'axis' ||
            p.unit === 'param' || p.unit === 'type' || p.unit === 'selector') &&
           p.min_val != null && p.max_val != null && (p.max_val - p.min_val <= 20);
  }

  function buildParamReadbackSpan(cmdId, p, sym) {
    if (!sym) {
      return '<div style="font-size:11px;margin-top:4px;"><span style="color:var(--muted)">Current: </span>' +
             '<span style="color:var(--amber)">— / not published</span></div>';
    }
    return '<div style="font-size:11px;margin-top:4px;"><span style="color:var(--muted)">Current (' +
           escapeHtml(sym) + '): </span><span class="cp-widget-current" id="cp-widget-current-' +
           cmdId + '-' + p.index + '"><span style="color:var(--amber)">— / not published</span></span></div>';
  }

  function buildParamControlHtml(cmdId, p) {
    var uid = cmdId + '-' + p.index;
    var label = escapeHtml(p.name) + (p.unit ?
      ' <span style="font-size:10px;color:var(--muted);font-weight:normal">(' + escapeHtml(p.unit) + ')</span>' : '');
    var readback = buildParamReadbackSpan(cmdId, p, widgetSymbol(cmdId, p));
    if (isSelectorParam(p)) {
      var opts = [];
      for (var oi = p.min_val; oi <= p.max_val; oi++) opts.push('<option value="' + oi + '">' + oi + '</option>');
      return [
        '<div class="cp-widget-p" data-cmd="' + cmdId + '" data-param="' + p.index + '">',
        '  <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;">',
        '    <label for="cp-widget-input-' + uid + '" style="font-weight:600;font-size:12px;min-width:130px;">' + label + '</label>',
        '    <select id="cp-widget-input-' + uid + '" class="cp-param-input" style="flex:1;">' + opts.join('') + '</select>',
        '    <button type="button" id="cp-widget-send-' + uid + '" class="cp-submit"',
        '      style="width:auto;margin:0;padding:5px 12px;font-size:11px;font-weight:600;cursor:pointer;">Send</button>',
        '  </div>',
        readback,
        '</div>'
      ].join('');
    }
    // Numeric value param.
    var hasRange = (p.min_val != null && p.max_val != null);
    var minT = (p.min_val != null) ? p.min_val : '-∞';
    var maxT = (p.max_val != null) ? p.max_val : '+∞';
    var rangeStr = 'Range: ' + minT + ' .. ' + maxT;
    var initVal = (p.min_val != null) ? p.min_val : 0;
    return [
      '<div class="cp-widget-p" data-cmd="' + cmdId + '" data-param="' + p.index + '">',
      '  <div style="display:flex;justify-content:space-between;align-items:baseline;margin-bottom:3px;">',
      '    <label for="cp-widget-input-' + uid + '" style="font-weight:600;font-size:12px;">' + label + '</label>',
      '    <span class="cp-param-range">' + rangeStr + '</span>',
      '  </div>',
      '  <div style="display:flex;gap:6px;align-items:center;">',
      hasRange ? '<input type="range" id="cp-widget-slider-' + uid + '" min="' + p.min_val + '" max="' + p.max_val +
                 '" step="1" value="' + initVal + '" style="flex:1;" />' : '',
      '    <input type="number" id="cp-widget-input-' + uid + '" class="cp-param-input"',
      (p.min_val != null ? ' min="' + p.min_val + '"' : ''),
      (p.max_val != null ? ' max="' + p.max_val + '"' : ''),
      ' step="any" value="' + initVal + '" style="width:110px;" />',
      '    <button type="button" id="cp-widget-send-' + uid + '" class="cp-submit"',
      '      style="width:auto;margin:0;padding:5px 12px;font-size:11px;font-weight:600;cursor:pointer;">Send</button>',
      '  </div>',
      '  <div style="font-size:11px;color:var(--red);" id="cp-widget-warn-' + uid + '"></div>',
      readback,
      '</div>'
    ].join('');
  }

  function buildWidgetValueHtml(cmdId) {
    var reg = getRegistryEntry(cmdId);
    var inner = widgetParams(cmdId).map(function (p) { return buildParamControlHtml(cmdId, p); }).join('');
    return [
      '<div class="cp-param-card" data-widget="value" data-cmd="' + hexId(cmdId) + '">',
      '  <div style="display:flex;justify-content:space-between;align-items:baseline;margin-bottom:6px;">',
      '    <span style="font-weight:700;font-size:12px;">' + hexId(cmdId) + ' – ' + escapeHtml(reg.name) + '</span>',
      '    <span style="font-size:10px;color:' + (SAFETY_CLASS_COLORS[reg.safetyClass] || 'var(--muted)') + ';">' +
      '      ' + escapeHtml(reg.precondition) + '</span>',
      '  </div>',
      inner,
      '</div>'
    ].join('');
  }

  function buildFlagToggleHtml(cmdId) {
    var reg = getRegistryEntry(cmdId);
    var rows = widgetParams(cmdId)
      .filter(function (p) { return p.unit === 'bool'; })
      .map(function (p) {
        var sym = widgetSymbol(cmdId, p);
        var stateFrag = sym
          ? '<span class="cp-flag-state" id="cp-flag-state-' + cmdId + '-' + p.index + '" style="font-size:10px;">' +
            '<span style="color:var(--amber)">not published</span></span>'
          : '<span class="cp-flag-state" id="cp-flag-state-' + cmdId + '-' + p.index + '" style="font-size:10px;">' +
            '<span style="color:var(--amber)">not published</span></span>';
        return [
          '<label class="cp-flag-row" data-cmd="' + cmdId + '" data-param="' + p.index + '"',
          '  style="display:flex;align-items:center;gap:8px;font-size:12px;padding:3px 0;cursor:pointer;">',
          '  <input type="checkbox" id="cp-flag-' + cmdId + '-' + p.index + '" />',
          '  <span style="flex:1;font-weight:600;">' + escapeHtml(p.name) + '</span>',
          stateFrag,
          '</label>'
        ].join('');
      }).join('');
    return [
      '<div class="cp-param-card" data-widget="flag" data-cmd="' + hexId(cmdId) + '">',
      '  <div style="font-weight:700;font-size:12px;margin-bottom:4px;">' + hexId(cmdId) + ' – ' +
      escapeHtml(reg.name) + '</div>',
      rows,
      '</div>'
    ].join('');
  }

  function buildWidgetsSection() {
    var valueHtml = WIDGET_VALUE_COMMANDS.map(function (c) {
      var ps = widgetParams(c);
      return (ps && ps.length) ? buildWidgetValueHtml(c) : '';
    }).join('');
    var flagHtml = WIDGET_FLAG_COMMANDS.map(function (c) {
      var bools = widgetParams(c).filter(function (p) { return p.unit === 'bool'; });
      return bools.length ? buildFlagToggleHtml(c) : '';
    }).join('');
    return [
      '<div class="cp-section" data-testid="cp-param-widgets">',
      '  <div style="font-size:10px;color:var(--muted);margin-bottom:6px;text-transform:uppercase;letter-spacing:0.05em;">Parameter Widgets</div>',
      valueHtml,
      '  <div style="font-size:10px;color:var(--muted);margin:12px 0 6px;text-transform:uppercase;letter-spacing:0.05em;">Flag Toggles</div>',
      flagHtml,
      '</div>'
    ].join('');
  }

  function currentFlagState(cmdId, p) {
    var sym = widgetSymbol(cmdId, p);
    if (!sym) return null;
    var v = streamValue(sym);
    if (v === undefined || v === null) return null;
    return Number(v) !== 0;
  }

  function refreshFlagToggles() {
    WIDGET_FLAG_COMMANDS.forEach(function (cmdId) {
      widgetParams(cmdId).forEach(function (p) {
        if (p.unit !== 'bool') return;
        var cb = q('cp-flag-' + cmdId + '-' + p.index);
        if (!cb) return;
        var st = currentFlagState(cmdId, p);
        // State follows telemetry, never optimistic.
        cb.checked = (st === true);
        cb.indeterminate = (st === null);
        var badge = q('cp-flag-state-' + cmdId + '-' + p.index);
        if (badge) {
          if (st === null) badge.innerHTML = '<span style="color:var(--amber)">not published</span>';
          else badge.textContent = st ? 'ON' : 'OFF';
        }
      });
    });
  }

  function refreshWidgetReadbacks() {
    WIDGET_VALUE_COMMANDS.forEach(function (cmdId) {
      widgetParams(cmdId).forEach(function (p) {
        var sym = widgetSymbol(cmdId, p);
        if (!sym) return;
        var el = q('cp-widget-current-' + cmdId + '-' + p.index);
        if (!el) return;
        var v = streamValue(sym);
        if (v === undefined || v === null) {
          el.innerHTML = '<span style="color:var(--amber)">— / not published</span>';
        } else {
          el.innerHTML = '<span style="color:var(--green);font-weight:600;">' + v + '</span>';
        }
      });
    });
  }

  function widgetParamValue(cmdId, p) {
    var el = q('cp-widget-input-' + cmdId + '-' + p.index);
    if (!el) return undefined;
    var v = parseFloat(el.value);
    return isNaN(v) ? undefined : v;
  }

  function validateWidgetNumeric(cmdId, p) {
    var input = q('cp-widget-input-' + cmdId + '-' + p.index);
    var send = q('cp-widget-send-' + cmdId + '-' + p.index);
    var warn = q('cp-widget-warn-' + cmdId + '-' + p.index);
    if (!input) return true;
    var raw = String(input.value).trim();
    var val = parseFloat(raw);
    var reason = '';
    if (raw === '' || isNaN(val)) reason = 'enter a numeric value';
    else if (p.min_val != null && val < p.min_val) reason = 'value ' + val + ' is below min ' + p.min_val;
    else if (p.max_val != null && val > p.max_val) reason = 'value ' + val + ' exceeds max ' + p.max_val;
    if (send) send.disabled = (reason !== '');
    if (warn) warn.innerHTML = reason ? '<span style="color:var(--red)">' + escapeHtml(reason) + '</span>' : '';
    return reason === '';
  }

  function submitParamWidget(cmdId, p, val) {
    if (p.unit !== 'bool' &&
        ((p.min_val != null && val < p.min_val) || (p.max_val != null && val > p.max_val))) {
      setResultStatus('rejected', 'value ' + val + ' out of range for ' + p.name);
      return;
    }
    submitCommand(cmdId, p.index, val);
  }

  function wireWidgets() {
    WIDGET_VALUE_COMMANDS.forEach(function (cmdId) {
      (COMMAND_PARAMS_REGISTRY[cmdId] || []).forEach(function (p) {
        var input = q('cp-widget-input-' + cmdId + '-' + p.index);
        var slider = q('cp-widget-slider-' + cmdId + '-' + p.index);
        var sendBtn = q('cp-widget-send-' + cmdId + '-' + p.index);
        if (slider && input) {
          slider.addEventListener('input', function () { input.value = slider.value; validateWidgetNumeric(cmdId, p); });
          input.addEventListener('input', function () { slider.value = input.value; validateWidgetNumeric(cmdId, p); });
        } else if (input) {
          input.addEventListener('input', function () { validateWidgetNumeric(cmdId, p); });
        }
        if (sendBtn) {
          sendBtn.addEventListener('click', function () {
            var v = widgetParamValue(cmdId, p);
            if (v === undefined) return;
            submitParamWidget(cmdId, p, v);
          });
        }
        if (input) validateWidgetNumeric(cmdId, p);
      });
    });
    WIDGET_FLAG_COMMANDS.forEach(function (cmdId) {
      widgetParams(cmdId).forEach(function (p) {
        if (p.unit !== 'bool') return;
        var cb = q('cp-flag-' + cmdId + '-' + p.index);
        if (!cb) return;
        cb.addEventListener('change', function () {
          submitParamWidget(cmdId, p, cb.checked ? 1 : 0);
        });
      });
    });
  }

  // ── Export ──────────────────────────────────────────────────────────────
  window.__PLUGIN_INIT__ = function (api) {
    _api = api;

    api.registerPanel('Command Panel', function (container) {
      container.innerHTML = buildHTML();

      // Cache DOM refs
      _els.cmdIdSelect    = q('cp-cmd-id');
      _els.cmdIndex       = q('cp-cmd-idx');
      _els.cmdValue       = q('cp-cmd-val');
      _els.rangeDisplay   = q('cp-range-display');
      _els.readbackDisplay= q('cp-readback-display');
      _els.readbackVal    = q('cp-readback-val');
      _els.submitBtn      = q('cp-submit');

      fetch('/api/contract').then(function(r) { return r.json(); }).then(function(contract) {
        if (contract && contract.commands) {
          for (var cmdHex in contract.commands) {
            var cmd = contract.commands[cmdHex];
            var id = parseInt(cmdHex, 16);
            if (!_commandSymbols[id]) _commandSymbols[id] = {};
            if (cmd.params) {
              COMMAND_PARAMS_REGISTRY[id] = cmd.params;
              cmd.params.forEach(function(p) {
                if (p.symbol) {
                  _commandSymbols[id][p.index] = p.symbol;
                }
              });
            }
          }
          renderBranchingForm();
          updateReadbackState();
        }
      }).catch(function(e) { console.warn('Failed to fetch contract symbols', e); });
      _els.resultBox      = q('cp-result-box');
      _els.safetyWarn     = q('cp-safety-warn');
      _els.historyPanel   = q('cp-hist-panel');
      _els.historyList    = q('cp-hist-list');
      _els.historyToggle  = q('cp-hist-toggle');
      _els.historyClear   = q('cp-hist-clear');

      // Populate command dropdown
      populateCommandSelect();
      updateRangeDisplay();

      // Wire event handlers
      if (_els.cmdIdSelect) _els.cmdIdSelect.addEventListener('change', updateRangeDisplay);
      if (_els.cmdIndex) _els.cmdIndex.addEventListener('input', updateReadbackState);
      var form = q('cp-form');
      if (form) form.addEventListener('submit', onManualSubmit);
      var modeToggle = q('cp-mode-toggle');
      if (modeToggle) modeToggle.addEventListener('click', toggleRawMode);
      if (_els.historyToggle) _els.historyToggle.addEventListener('click', toggleHistory);
      if (_els.historyClear) _els.historyClear.addEventListener('click', clearHistory);
      wireQuickCommands();
      wireBenchMode();
      wireVirtualRC();
      wireNavPaths();
      wireOfBias();
      wireWidgets();
      wireHistoryFilters();

      // Load and render history
      _history = loadHistory();
      // restore filter & visibility
      try {
        var f = localStorage.getItem(HISTORY_FILTER_KEY);
        if (f) _historyFilter = f;
        if (localStorage.getItem('gs_cmd_hist_visible') === '1') {
          _els.historyPanel.style.display = '';
          _els.historyToggle.textContent = 'Hide History';
        }
      } catch (_) {}
      setHistoryFilter(_historyFilter);

      // Subscribe to state for ARM/SDK/bench updates
      if (api.subscribe) {
        api.subscribe(function (state) {
          _state = state;
          refreshArmBadge();
          refreshBenchModePill();
          refreshVirtualRCPanel();
          renderReadbackValue();
          refreshFlagToggles();
          refreshWidgetReadbacks();
        });
      }
      // Initial state fetch in case subscribe fires only on changes
      _state = api.getState ? api.getState() : null;
      refreshArmBadge();
      refreshBenchModePill();
      refreshVirtualRCPanel();
      renderReadbackValue();
      refreshFlagToggles();
      refreshWidgetReadbacks();
    });
  };

  window.__PLUGIN_DESTROY__ = function () {
    if (_pollTimer) { clearInterval(_pollTimer); _pollTimer = null; }
    // Disable virtual RC if we issued any nonzero
    if (_api && _vrcEnabled) {
      for (var ch = 0; ch < 5; ch++) {
        _api.submitCommand(0x06, ch, 0).catch(function () {});
      }
    }
    _vrcEnabled = false;
    _rawModeForced = false;
    _vrcValues = [0, 0, 0, 0, 0];
    _pendingTx = null;
    _history = [];
    _state = null;
    _api = null;
  };

  window.__registerPlugin__('Command Panel', window.__PLUGIN_INIT__, window.__PLUGIN_DESTROY__);

})();
