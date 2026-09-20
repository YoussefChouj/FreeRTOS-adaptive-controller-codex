/**
 * overview-panel.js — PLC-style HMI overview (task 20260921-044235)
 *
 * One screen that answers, without prior knowledge of the codebase:
 *   1. Is the system healthy?        → alarm banner (worst active condition)
 *   2. What is it doing right now?   → status strip (ARM / mode / battery)
 *                                     + attitude indicator (artificial
 *                                     horizon, Frame C) + pre-flight
 *                                     checklist (read-only verdicts)
 *   3. Where is the problem?         → mimic diagram of the signal chain,
 *                                     left to right: gyro sensor → rate
 *                                     filter → attitude & position estimate
 *                                     → controllers (PID + MRAC) → motors.
 *                                     A dead or stale stage greys out, so the
 *                                     fault localises visually.
 *
 * Colour semantics (strict, used for nothing else — see HMI_DESIGN.md):
 *   green = normal    amber = off-nominal / degraded
 *   red   = alarm     grey  = no data (or boolean OFF — the label says which)
 *
 * Honesty rules (same convention as status-panel.js):
 *   - A key that is not published renders "NOT PUBLISHED" (amber), never 0.
 *   - A stage whose slot has no data renders grey with an explicit "NO DATA"
 *     label — never a stale last-known value, never "—", never 0.
 *   - A value that stops updating shows its age (amber) after 2 s and goes
 *     grey after the slot TTL (default 30 s).
 *   - Nothing synthetic. No demo values, no placeholder waveforms.
 *
 * The shadow EKF box displays s_ekf states for observability only. They are
 * NEVER wired into a control path, setpoint or motor output.
 *
 * Every binding is verified against ground_station/comm/wifi_bridge.py
 * (the decoder is ground truth for what is publishable):
 *   slot 0 ← tag "a"  (Frame A / slot-0 subscribe, _slot0_to_sidebar)
 *   slot 1 ← tags "id" and "b" (Frame ID, Frame B)
 *   slot 3 ← tag "c"  (Frame C, wifi_bridge.py:_decode_frame_c)
 * The full binding table lives in HMI_DESIGN.md and in the harness.
 */
(function () {
  'use strict';

  /* ── Thresholds (justified in HMI_DESIGN.md) ────────────────────────── */
  var STALE_WARN_MS  = 2000;   // amber + age readout after 2 s without updates
  var RECENT_MS      = 60000;  // a cleared alarm stays visible for 60 s
  var VBAT_RED_V     = 15.0;   // firmware beep threshold, StabilizerTask.c:1329
  var VBAT_AMBER_V   = 15.5;   // dashboard-only early warning (0.5 V above beep)
  var LOSS_AMBER_PCT = 1.0;    // shell convention (plugin-api.md loss table)
  var LOSS_RED_PCT   = 5.0;

  var SLOT_ORDER = ['0', '1', '3', '2'];

  var FLY_MODE_LABELS = ['Stabilize', 'AltHold', 'PosHold', 'Auto', 'Manual', 'SDK'];

  /* ── Mimic-diagram stage model ─────────────────────────────────────────
   * Every row: plain-language label, the verified key, its unit, decimals.
   * plain = operator label; key = symbol an expert can trace. */
  var STAGES = [
    {
      id: 'imu', title: 'Gyro sensor', hint: 'Frame C · c.gyro_*',
      slot: '3',
      rows: [
        { plain: 'Roll rate',  key: 'c.gyro_x', unit: 'rad/s', dec: 2, signed: true },
        { plain: 'Pitch rate', key: 'c.gyro_y', unit: 'rad/s', dec: 2, signed: true },
        { plain: 'Yaw rate',   key: 'c.gyro_z', unit: 'rad/s', dec: 2, signed: true },
      ],
    },
    {
      id: 'filter', title: 'Rate filter (flown)', hint: 'Frame B · pid.gyro*.FB — GyroFilter output',
      slot: '1',
      rows: [
        { plain: 'Filtered roll rate',  key: 'pid.gyrox.FB', unit: 'deg/s', dec: 1, signed: true },
        { plain: 'Filtered pitch rate', key: 'pid.gyroy.FB', unit: 'deg/s', dec: 1, signed: true },
        { plain: 'Filtered yaw rate',   key: 'pid.gyroz.FB', unit: 'deg/s', dec: 1, signed: true },
      ],
    },
    {
      id: 'att', title: 'Attitude estimate', hint: 'Frame C · c.roll / c.pitch / c.yaw',
      slot: '3',
      rows: [
        { plain: 'Roll',  key: 'c.roll',  unit: 'deg', dec: 1, signed: true },
        { plain: 'Pitch', key: 'c.pitch', unit: 'deg', dec: 1, signed: true },
        { plain: 'Yaw',   key: 'c.yaw',   unit: 'deg', dec: 1, signed: true },
      ],
    },
    {
      id: 'pos', title: 'Position estimate', hint: 'Frame C · c.earth_x / c.earth_y / c.altitude',
      slot: '3',
      rows: [
        { plain: 'Position X', key: 'c.earth_x',  unit: 'm', dec: 2, signed: true },
        { plain: 'Position Y', key: 'c.earth_y',  unit: 'm', dec: 2, signed: true },
        { plain: 'Altitude',   key: 'c.altitude', unit: 'm', dec: 2 },
      ],
    },
    {
      id: 'rctrl', title: 'Rate controllers', hint: 'Frame B · pid.gyro*.U — normalised mixer command',
      slot: '1',
      rows: [
        { plain: 'Roll output',  key: 'pid.gyrox.U', unit: 'cmd', dec: 3, signed: true },
        { plain: 'Pitch output', key: 'pid.gyroy.U', unit: 'cmd', dec: 3, signed: true },
        { plain: 'Yaw output',   key: 'pid.gyroz.U', unit: 'cmd', dec: 3, signed: true },
      ],
    },
    {
      id: 'mrac', title: 'MRAC augmentation', hint: 'Frame A · mrac.*.u_ad / .e — adaptive add-on',
      slot: '0',
      rows: [
        { plain: 'Roll adaptive',  key: 'mrac.roll.u_ad',  unit: 'cmd',  dec: 3, signed: true },
        { plain: 'Roll error',    key: 'mrac.roll.e',     unit: 'rad/s', dec: 2, signed: true },
        { plain: 'Pitch adaptive', key: 'mrac.pitch.u_ad', unit: 'cmd',  dec: 3, signed: true },
        { plain: 'Pitch error',   key: 'mrac.pitch.e',     unit: 'rad/s', dec: 2, signed: true },
      ],
    },
    {
      id: 'motors', title: 'Motors', hint: 'Frame C · motor.rpm_0..3',
      slot: '3',
      rows: [
        { plain: 'Motor 1', key: 'motor.rpm_0', unit: 'rpm', dec: 0 },
        { plain: 'Motor 2', key: 'motor.rpm_1', unit: 'rpm', dec: 0 },
        { plain: 'Motor 3', key: 'motor.rpm_2', unit: 'rpm', dec: 0 },
        { plain: 'Motor 4', key: 'motor.rpm_3', unit: 'rpm', dec: 0 },
      ],
    },
  ];

  /* Shadow EKF: 9 states, no position states. Display-only. */
  var SHADOW_ROWS = [
    { plain: 'Velocity X', key: 'ekf.vel_x', unit: 'm/s', dec: 2, signed: true },
    { plain: 'Velocity Y', key: 'ekf.vel_y', unit: 'm/s', dec: 2, signed: true },
    { plain: 'Velocity Z', key: 'ekf.vel_z', unit: 'm/s', dec: 2, signed: true },
    { plain: 'Gyro bias X', key: 'ekf.bias_gyro_x', unit: 'rad/s', dec: 4, signed: true },
    { plain: 'Gyro bias Y', key: 'ekf.bias_gyro_y', unit: 'rad/s', dec: 4, signed: true },
    { plain: 'Gyro bias Z', key: 'ekf.bias_gyro_z', unit: 'rad/s', dec: 4, signed: true },
  ];

  /* ── Attitude indicator (artificial horizon) — task 20260921-065323 ─────
   * Inline SVG, no library. Same source and same staleness rules as the
   * attitude stage above: c.roll / c.pitch / c.yaw from Frame C (slot 3).
   * AI_PITCH_PPD is pixels of horizon travel per degree of pitch inside
   * the 200-unit viewBox. The sky/ground fills are the instrument's own
   * convention (every artificial horizon has them); they are deliberately
   * NOT the HMI state colours of HMI_DESIGN.md §1. When roll/pitch are
   * absent or stale the world is hidden and a grey NO DATA overlay shown —
   * a level horizon must never be mistaken for a healthy flat aircraft. */
  var AI_PITCH_PPD = 2.0;

  var AI_ROWS = [
    { plain: 'Roll',  key: 'c.roll',  unit: 'deg', dec: 1, signed: true },
    { plain: 'Pitch', key: 'c.pitch', unit: 'deg', dec: 1, signed: true },
    { plain: 'Yaw',   key: 'c.yaw',   unit: 'deg', dec: 1, signed: true },
  ];

  /* Classification stage for the horizon: roll + pitch drive the picture,
   * so they — not yaw — decide whether the instrument is live. */
  var AI_STAGE = { id: 'ai', slot: '3', rows: [AI_ROWS[0], AI_ROWS[1]] };

  /* ── Pre-flight checklist — task 20260921-065323 ────────────────────────
   * One row per condition that is genuinely observable in telemetry today.
   * Verdicts: PASS (green, verified live) / FAIL (red, verified violated)
   * / UNKNOWN (grey, cannot be determined). UNKNOWN is a first-class
   * verdict: a row with no honest evidence is never rendered as PASS.
   * This widget is read-only — it displays state, sends no commands and
   * gates nothing. */
  var CHECKLIST = [
    { id: 'link',      plain: 'Telemetry link alive and recent', hint: 'any stream · last_update_ns',
      eval: evalLink },
    { id: 'disarm',    plain: 'Aircraft disarmed', hint: 'Frame A/ID · status.arm = 0',
      eval: evalDisarm },
    { id: 'battery',   plain: 'Battery voltage in range', hint: 'Frame A · status.vbat ≥ 15.5 V early warning',
      eval: evalBattery },
    { id: 'imu',       plain: 'IMU publishing', hint: 'Frame C · c.gyro_x/y/z @ 50 Hz',
      eval: evalImu },
    { id: 'attitude',  plain: 'Attitude estimate available', hint: 'Frame C · c.roll / c.pitch',
      eval: evalAttitude },
    { id: 'rc',        plain: 'RC receiver link up', hint: 'Frame A · status.sbus = 0',
      eval: evalRc },
    { id: 'estimator', plain: 'Estimator ready', hint: 'Frame A/ID · status.estimator_ready = 1',
      eval: evalEstimator },
    { id: 'faults',    plain: 'No active fault', hint: 'alarm engine — worst of the banner conditions',
      eval: evalFaults },
  ];

  /* ── State ───────────────────────────────────────────────────────────── */
  var _lastState = null;
  var _tickTimer = null;
  var _recentAlarms = [];   // [{id, text, clearedAt}] — cleared but still visible

  function q(id) {
    return (typeof document !== 'undefined' && document.getElementById) ? document.getElementById(id) : null;
  }

  /* ── Data access ─────────────────────────────────────────────────────── */

  function findValue(state, key) {
    if (!state || !state.streams) return null;
    for (var i = 0; i < SLOT_ORDER.length; i++) {
      var s = state.streams[SLOT_ORDER[i]];
      if (s && s.values && s.values[key] != null) {
        var ts = (s._key_ts && s._key_ts[key] != null) ? s._key_ts[key] : (s.last_update_ns || null);
        return { val: s.values[key], slotName: SLOT_ORDER[i], ts: ts, loss: s.loss_pct || 0 };
      }
    }
    return null;
  }

  function slotOf(state, name) {
    return (state && state.streams) ? state.streams[name] : null;
  }

  function stageAgeMs(stage, state, nowMs) {
    // Age of the freshest row of the stage, falling back to slot update time.
    var newest = null;
    for (var i = 0; i < stage.rows.length; i++) {
      var f = findValue(state, stage.rows[i].key);
      if (f && f.ts != null && (newest == null || f.ts > newest)) newest = f.ts;
    }
    if (newest == null) {
      var s = slotOf(state, stage.slot);
      if (s && s.last_update_ns) newest = s.last_update_ns;
    }
    if (newest == null) return null;
    return Math.max(0, nowMs - newest / 1e6);
  }

  /* Stage state for the mimic box: 'ok' | 'warn' | 'alarm' | 'nodata'. */
  function classifyStage(stage, state, nowMs, ttlMs) {
    var s = slotOf(state, stage.slot);
    if (!s || !s.values) {
      return { cls: 'nodata', label: 'NO DATA', sub: 'stream ' + stage.slot + ' not received' };
    }
    var anyKey = false;
    for (var i = 0; i < stage.rows.length; i++) {
      if (s.values[stage.rows[i].key] != null) { anyKey = true; break; }
    }
    if (!anyKey) {
      return { cls: 'nodata', label: 'NO DATA', sub: 'not published by this build' };
    }
    var age = stageAgeMs(stage, state, nowMs);
    if (age != null && age > ttlMs) {
      return { cls: 'nodata', label: 'NO DATA', sub: 'stale ' + fmtAge(age) + ' — frozen' };
    }
    if (age != null && age > STALE_WARN_MS) {
      return { cls: 'warn', label: 'STALE', sub: 'age ' + fmtAge(age) };
    }
    var loss = s.loss_pct || 0;
    if (loss > LOSS_RED_PCT)   return { cls: 'alarm', label: 'LOSS ' + loss.toFixed(1) + '%', sub: 'critical packet loss' };
    if (loss > LOSS_AMBER_PCT) return { cls: 'warn',  label: 'LOSS ' + loss.toFixed(1) + '%', sub: 'degraded link' };
    return { cls: 'ok', label: 'LIVE', sub: '' };
  }

  function fmtAge(ms) {
    if (ms < 1000) return Math.round(ms) + ' ms';
    if (ms < 60000) return (ms / 1000).toFixed(1) + ' s';
    return (ms / 60000).toFixed(1) + ' min';
  }

  function fmtVal(row, v) {
    var sign = (row.signed && v >= 0) ? '+' : '';
    return sign + Number(v).toFixed(row.dec) + ' ' + row.unit;
  }

  /* ── Pre-flight checklist evaluation ────────────────────────────────── */

  /* Verdict helper: { v: 'pass'|'fail'|'unknown', detail: '…' }. */
  function V(v, detail) { return { v: v, detail: detail }; }

  function hasStreams(state) {
    return !!(state && state.streams && Object.keys(state.streams).length > 0);
  }

  /* Is any stream OTHER than `exceptSlot` delivering fresh packets? Used to
   * separate "Frame C is not arriving although the link is up" (FAIL) from
   * total silence, where nothing can be judged (UNKNOWN). */
  function anyOtherStreamAlive(state, exceptSlot, nowMs) {
    if (!hasStreams(state)) return false;
    var names = Object.keys(state.streams);
    for (var i = 0; i < names.length; i++) {
      if (names[i] === exceptSlot) continue;
      var s = state.streams[names[i]];
      if (s && s.last_update_ns && (nowMs - s.last_update_ns / 1e6) < STALE_WARN_MS) return true;
    }
    return false;
  }

  /* Shared verdict for a set of Frame C keys (IMU row, attitude row). */
  function keyRowVerdict(state, nowMs, ttlMs, keys, passDetail) {
    if (!hasStreams(state)) return V('unknown', 'no telemetry received — cannot judge');
    var found = false, newest = null;
    for (var i = 0; i < keys.length; i++) {
      var f = findValue(state, keys[i]);
      if (f) { found = true; if (f.ts != null && (newest == null || f.ts > newest)) newest = f.ts; }
    }
    if (!found) {
      var s3 = slotOf(state, '3');
      if (s3 && s3.values) return V('unknown', 'keys not published by this build');
      if (anyOtherStreamAlive(state, '3', nowMs)) return V('fail', 'Frame C (slot 3) not received — link is up');
      return V('unknown', 'Frame C (slot 3) not received');
    }
    if (newest == null) return V('unknown', 'subscribed but no packet yet');
    var age = Math.max(0, nowMs - newest / 1e6);
    if (age > ttlMs)          return V('fail', 'frozen ' + fmtAge(age) + ' — no fresh data');
    if (age > STALE_WARN_MS)  return V('fail', 'age ' + fmtAge(age) + ' — expected 50 Hz');
    return V('pass', passDetail + ' (' + fmtAge(age) + ' ago)');
  }

  function evalLink(state, nowMs, ttlMs) {
    if (!hasStreams(state)) return V('unknown', 'no telemetry received — cannot judge');
    var names = Object.keys(state.streams), newest = null;
    for (var i = 0; i < names.length; i++) {
      var ts = state.streams[names[i]].last_update_ns;
      if (ts != null && (newest == null || ts > newest)) newest = ts;
    }
    if (newest == null) return V('unknown', 'subscribed but no packet yet');
    var age = Math.max(0, nowMs - newest / 1e6);
    if (age > ttlMs)         return V('fail', 'frozen — no packet for ' + fmtAge(age));
    if (age > STALE_WARN_MS) return V('fail', 'last packet ' + fmtAge(age) + ' ago — link slow');
    return V('pass', 'last packet ' + fmtAge(age) + ' ago');
  }

  function evalDisarm(state, nowMs, ttlMs) {
    if (!hasStreams(state)) return V('unknown', 'no telemetry received — cannot judge');
    var a = findValue(state, 'status.arm');
    if (a == null || a.val == null || isNaN(a.val)) return V('unknown', 'status.arm not published');
    if (a.ts != null) {
      var age = Math.max(0, nowMs - a.ts / 1e6);
      if (age > ttlMs) return V('unknown', 'status.arm frozen ' + fmtAge(age) + ' ago');
    }
    var n = Number(a.val);
    if (n === 0) return V('pass', 'status.arm = 0 — disarmed');
    if (n === 1) return V('fail', 'status.arm = 1 — AIRCRAFT IS ARMED');
    return V('unknown', 'status.arm = ' + a.val + ' — unrecognised value');
  }

  function evalBattery(state, nowMs, ttlMs) {
    if (!hasStreams(state)) return V('unknown', 'no telemetry received — cannot judge');
    var b = findValue(state, 'status.vbat');
    if (b == null || b.val == null || isNaN(b.val)) return V('unknown', 'status.vbat not published');
    if (b.ts != null) {
      var age = Math.max(0, nowMs - b.ts / 1e6);
      if (age > ttlMs) return V('unknown', 'status.vbat frozen ' + fmtAge(age) + ' ago');
    }
    var v = Number(b.val);
    if (v < VBAT_RED_V)   return V('fail', v.toFixed(2) + ' V — below firmware beep threshold ' + VBAT_RED_V.toFixed(1) + ' V');
    if (v < VBAT_AMBER_V) return V('fail', v.toFixed(2) + ' V — below early warning ' + VBAT_AMBER_V.toFixed(1) + ' V');
    return V('pass', v.toFixed(2) + ' V');
  }

  function evalImu(state, nowMs, ttlMs) {
    return keyRowVerdict(state, nowMs, ttlMs, ['c.gyro_x', 'c.gyro_y', 'c.gyro_z'], 'c.gyro_x/y/z live');
  }

  function evalAttitude(state, nowMs, ttlMs) {
    return keyRowVerdict(state, nowMs, ttlMs, ['c.roll', 'c.pitch'], 'c.roll / c.pitch live');
  }

  function evalRc(state, nowMs, ttlMs) {
    if (!hasStreams(state)) return V('unknown', 'no telemetry received — cannot judge');
    var r = findValue(state, 'status.sbus');
    if (r == null || r.val == null || isNaN(r.val)) return V('unknown', 'status.sbus not published');
    if (r.ts != null) {
      var age = Math.max(0, nowMs - r.ts / 1e6);
      if (age > ttlMs) return V('unknown', 'status.sbus frozen ' + fmtAge(age) + ' ago');
    }
    if (Number(r.val) === 0) return V('pass', 'status.sbus = 0 — receiver link up');
    return V('fail', 'RC RECEIVER LINK LOST (status.sbus = ' + Number(r.val) + ')');
  }

  function evalEstimator(state, nowMs, ttlMs) {
    if (!hasStreams(state)) return V('unknown', 'no telemetry received — cannot judge');
    var e = findValue(state, 'status.estimator_ready');
    if (e == null || e.val == null || isNaN(e.val)) return V('unknown', 'status.estimator_ready not published');
    if (e.ts != null) {
      var age = Math.max(0, nowMs - e.ts / 1e6);
      if (age > ttlMs) return V('unknown', 'status.estimator_ready frozen ' + fmtAge(age) + ' ago');
    }
    if (Number(e.val) !== 0) return V('pass', 'status.estimator_ready = 1');
    return V('fail', 'status.estimator_ready = 0 — estimator not ready');
  }

  function evalFaults(state, nowMs, ttlMs) {
    if (!hasStreams(state)) return V('unknown', 'no telemetry received — aircraft state unknown');
    // "No active alarms" is only decidable once at least one packet arrived:
    // with zero packets ever, the absence of alarms is absence of evidence.
    var anyPacket = false;
    var names = Object.keys(state.streams);
    for (var i = 0; i < names.length; i++) {
      if (state.streams[names[i]].last_update_ns != null) { anyPacket = true; break; }
    }
    if (!anyPacket) return V('unknown', 'subscribed but no packet yet');
    var alarms = computeAlarms(state, nowMs, ttlMs);
    if (!alarms.length) return V('pass', 'no active alarms');
    var worst = alarms.slice().sort(function (a, b) { return sevRank(b.sev) - sevRank(a.sev); })[0];
    return V('fail', worst.sev.toUpperCase() + ': ' + worst.text);
  }

  /* ── Alarm engine ────────────────────────────────────────────────────── */

  /* Returns [{id, sev: 'red'|'amber', text}] for the active conditions. */
  function computeAlarms(state, nowMs, ttlMs) {
    var out = [];
    if (!state || !state.streams || Object.keys(state.streams).length === 0) {
      out.push({ id: 'notelem', sev: 'amber', text: 'No telemetry received — aircraft state unknown' });
      return out;
    }

    // Battery (status.vbat, slot 0 or 1)
    var vb = findValue(state, 'status.vbat');
    if (vb && vb.val != null && !isNaN(vb.val)) {
      var v = Number(vb.val);
      if (v < VBAT_RED_V) {
        out.push({ id: 'vbat-low', sev: 'red',
          text: 'BATTERY LOW — ' + v.toFixed(2) + ' V (firmware beep threshold ' + VBAT_RED_V.toFixed(1) + ' V)' });
      } else if (v < VBAT_AMBER_V) {
        out.push({ id: 'vbat-warn', sev: 'amber',
          text: 'Battery ' + v.toFixed(2) + ' V — below early-warning ' + VBAT_AMBER_V.toFixed(1) + ' V' });
      }
    }

    // Per-slot staleness + loss, localised to the affected stages.
    var titlesBySlot = {};
    STAGES.forEach(function (st) {
      if (!titlesBySlot[st.slot]) titlesBySlot[st.slot] = [];
      titlesBySlot[st.slot].push(st.title);
    });
    Object.keys(titlesBySlot).forEach(function (slotName) {
      var s = slotOf(state, slotName);
      if (!s || !s.last_update_ns) return;
      var names = titlesBySlot[slotName].join(', ');
      var age = nowMs - s.last_update_ns / 1e6;
      if (age > ttlMs) {
        out.push({ id: 'stale-' + slotName, sev: 'amber',
          text: 'Telemetry stale (' + fmtAge(age) + '): ' + names + ' — check link' });
      } else if (age > STALE_WARN_MS) {
        out.push({ id: 'stale-' + slotName, sev: 'amber',
          text: 'Telemetry slow (' + fmtAge(age) + '): ' + names });
      }
      var loss = s.loss_pct || 0;
      if (loss > LOSS_RED_PCT) {
        out.push({ id: 'loss-' + slotName, sev: 'red',
          text: 'Packet loss ' + loss.toFixed(1) + '% on stream ' + slotName + ' (' + names + ') — check antenna / range' });
      } else if (loss > LOSS_AMBER_PCT) {
        out.push({ id: 'loss-' + slotName, sev: 'amber',
          text: 'Packet loss ' + loss.toFixed(1) + '% on stream ' + slotName + ' (' + names + ')' });
      }
    });

    // Status flags
    var est = findValue(state, 'status.estimator_ready');
    if (est && Number(est.val) === 0) {
      out.push({ id: 'estimator', sev: 'amber', text: 'Estimator not ready (status.estimator_ready = 0)' });
    }
    var sbus = findValue(state, 'status.sbus');
    if (sbus && Number(sbus.val) !== 0) {
      out.push({ id: 'sbus', sev: 'red', text: 'RC RECEIVER LINK LOST (status.sbus = ' + Number(sbus.val) + ')' });
    }
    return out;
  }

  function updateRecentAlarms(active, nowMs) {
    var tracked = {};
    _recentAlarms.forEach(function (r) { tracked[r.id] = r; });
    var activeIds = {};
    active.forEach(function (a) {
      activeIds[a.id] = true;
      var t = tracked[a.id];
      if (t) {
        // Re-activated after a clear: reopen instead of duplicating.
        t.open = true;
        t.text = a.text;
        t.clearedAt = null;
      } else {
        var entry = { id: a.id, text: a.text, clearedAt: null, open: true };
        _recentAlarms.push(entry);
        tracked[a.id] = entry;
      }
    });
    // No longer active → mark cleared with the clear time.
    _recentAlarms.forEach(function (r) {
      if (r.open && !activeIds[r.id]) { r.open = false; r.clearedAt = nowMs; }
    });
    // Keep open alarms and clears newer than RECENT_MS; drop the rest.
    _recentAlarms = _recentAlarms.filter(function (r) {
      return r.open || (nowMs - r.clearedAt) < RECENT_MS;
    });
  }

  function sevRank(s) { return s === 'red' ? 2 : 1; }

  /* ── HTML ────────────────────────────────────────────────────────────── */

  function buildHTML() {
    var css = [
      '<style>',
      '.ov-root { font-size: 12px; }',
      '.ov-strip { display:flex; align-items:center; gap:14px; flex-wrap:wrap; margin-bottom:10px; }',
      '.ov-strip-item { min-width:70px; }',
      '.ov-strip-label { font-size:10px; color:var(--muted); }',
      '.ov-strip-value { font-size:15px; font-weight:700; font-family:Consolas,monospace; }',
      '.ov-pill { display:inline-flex; align-items:center; gap:4px; padding:2px 8px;',
      '  border-radius:10px; font-size:10px; font-weight:600; letter-spacing:0.03em; text-transform:uppercase; }',
      '.ov-pill-on  { background:rgba(78,204,163,0.15); color:var(--green); }',
      '.ov-pill-off { background:rgba(136,136,170,0.10); color:var(--muted); }',
      '.ov-pill-np  { background:rgba(245,166,35,0.15); color:var(--amber); }',
      '.ov-banner { padding:8px 12px; border-radius:4px; font-size:13px; font-weight:700; margin-bottom:6px; }',
      '.ov-banner-ok    { background:rgba(78,204,163,0.15); color:var(--green); }',
      '.ov-banner-warn  { background:rgba(245,166,35,0.18); color:var(--amber); }',
      '.ov-banner-alarm { background:rgba(233,69,96,0.20);  color:var(--red); }',
      '.ov-alarm-list { margin:0 0 10px 0; }',
      '.ov-alarm-item { padding:3px 0 3px 12px; border-left:3px solid; margin:2px 0; font-size:11px; }',
      '.ov-alarm-red   { border-color:var(--red);   color:var(--red); }',
      '.ov-alarm-amber { border-color:var(--amber); color:var(--amber); }',
      '.ov-alarm-clear { border-color:var(--muted); color:var(--muted); font-style:italic; }',
      '.ov-chain { display:flex; align-items:stretch; gap:0; flex-wrap:wrap; margin-bottom:10px; }',
      '.ov-stage { flex:1 1 120px; min-width:120px; border:2px solid var(--muted); border-radius:6px;',
      '  padding:6px 8px; background:var(--card); position:relative; }',
      '.ov-arrow { align-self:center; color:var(--muted); font-size:18px; padding:0 4px; font-weight:700; }',
      '.ov-stage-ok     { border-color:var(--green); }',
      '.ov-stage-warn   { border-color:var(--amber); }',
      '.ov-stage-alarm  { border-color:var(--red); }',
      '.ov-stage-nodata { border-color:var(--muted); opacity:0.55; }',
      '.ov-stage-title { font-size:11px; font-weight:700; }',
      '.ov-stage-hint  { font-size:9px; color:var(--muted); margin-bottom:4px; min-height:10px; }',
      '.ov-stage-flag  { position:absolute; top:4px; right:6px; font-size:9px; font-weight:700; }',
      '.ov-flag-ok     { color:var(--green); }',
      '.ov-flag-warn   { color:var(--amber); }',
      '.ov-flag-alarm  { color:var(--red); }',
      '.ov-flag-nodata { color:var(--muted); }',
      '.ov-sub { font-size:9px; color:var(--muted); margin-top:3px; }',
      '.ov-row { display:flex; justify-content:space-between; gap:6px; }',
      '.ov-row-plain { color:var(--muted); font-size:10px; }',
      '.ov-row-val { font-family:Consolas,monospace; font-size:11px; font-weight:600; }',
      '.ov-row-val-nodata { color:var(--muted); font-weight:400; font-size:10px; }',
      '.ov-shadow { border:1px dashed var(--muted); border-radius:6px; padding:6px 10px; }',
      '.ov-shadow-badge { display:inline-block; padding:1px 6px; border-radius:3px; font-size:9px;',
      '  font-weight:700; letter-spacing:0.05em; background:rgba(136,136,170,0.15); color:var(--muted); }',
      '.ov-grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(110px,1fr)); gap:2px 14px; margin-top:4px; }',
      '.ov-instrow { display:flex; gap:12px; flex-wrap:wrap; align-items:flex-start; margin-bottom:10px; }',
      '.ov-ai-box { flex:0 1 210px; border:2px solid var(--muted); border-radius:6px;',
      '  padding:6px 8px; background:var(--card); position:relative; }',
      '.ov-ai-box-ok     { border-color:var(--green); }',
      '.ov-ai-box-warn   { border-color:var(--amber); }',
      '.ov-ai-box-alarm  { border-color:var(--red); }',
      '.ov-ai-box-nodata { border-color:var(--muted); }',
      '.ov-ai-svg { display:block; margin:2px auto 0 auto; max-width:100%; height:auto; }',
      '.ov-ai-readouts { display:flex; justify-content:space-around; gap:6px; margin-top:4px; flex-wrap:wrap; }',
      '.ov-ai-ro { text-align:center; min-width:56px; }',
      '.ov-ai-ro-label { font-size:10px; color:var(--muted); }',
      '.ov-ai-ro-val { font-family:Consolas,monospace; font-size:13px; font-weight:700; }',
      '.ov-ai-ro-val-nodata { color:var(--muted); font-weight:400; font-size:11px; }',
      '.ov-ai-ro-val-warn   { color:var(--amber); }',
      '.ov-check { flex:1 1 300px; min-width:280px; border:2px solid var(--border); border-radius:6px;',
      '  padding:6px 10px; background:var(--card); }',
      '.ov-check-row { padding:3px 0 2px 0; border-bottom:1px solid var(--border); }',
      '.ov-check-row:last-child { border-bottom:none; }',
      '.ov-check-line { display:flex; align-items:center; gap:8px; }',
      '.ov-chk-verdict { display:inline-block; min-width:70px; text-align:center; padding:1px 6px;',
      '  border-radius:3px; font-size:9px; font-weight:700; letter-spacing:0.05em; text-transform:uppercase; }',
      '.ov-chk-pass    { background:rgba(78,204,163,0.15); color:var(--green); }',
      '.ov-chk-fail    { background:rgba(233,69,96,0.20);  color:var(--red); }',
      '.ov-chk-unknown { background:rgba(136,136,170,0.12); color:var(--muted); }',
      '.ov-chk-plain { font-size:11px; font-weight:600; }',
      '.ov-chk-hint  { font-size:9px; color:var(--muted); }',
      '.ov-chk-detail { font-size:10px; color:var(--muted); font-family:Consolas,monospace; padding-left:78px; }',
      '</style>',
    ].join('');

    var strip = [
      '<div class="ov-strip">',
      '  <div class="ov-strip-item"><div class="ov-strip-label">ARM</div>',
      '    <div id="ov-arm" class="ov-strip-value" style="color:var(--amber)">NOT PUBLISHED</div></div>',
      '  <div class="ov-strip-item"><div class="ov-strip-label">Flight mode</div>',
      '    <div id="ov-flymode" class="ov-strip-value" style="color:var(--amber)">NOT PUBLISHED</div></div>',
      '  <div class="ov-strip-item"><div class="ov-strip-label">Battery</div>',
      '    <div id="ov-vbat" class="ov-strip-value" style="color:var(--amber)">NOT PUBLISHED</div></div>',
      '  <div class="ov-strip-item"><div class="ov-strip-label">RC link</div>',
      '    <div id="ov-pills"><span id="ov-pill-rc" class="ov-pill ov-pill-np">RC AUTH ?</span>',
      '    <span id="ov-pill-sbus" class="ov-pill ov-pill-np">SBUS ?</span>',
      '    <span id="ov-pill-est" class="ov-pill ov-pill-np">ESTIMATOR ?</span></div></div>',
      '</div>',
    ].join('');

    var banner = [
      '<div id="ov-banner" class="ov-banner ov-banner-ok">SYSTEM NORMAL — no active alarms</div>',
      '<div id="ov-alarm-list" class="ov-alarm-list"></div>',
    ].join('');

    /* ── Attitude indicator (inline SVG, no library) ────────────────────
     * The rotating group carries the horizon; pitch shifts it along the
     * rotated y-axis. ov-ai-dead covers the whole instrument when there is
     * no trustworthy attitude, so a missing signal can never look level. */
    var aiSvg = [
      '<svg id="ov-ai-svg" class="ov-ai-svg" viewBox="0 0 200 200" width="186" height="186"',
      '  role="img" aria-label="Attitude indicator (artificial horizon)">',
      '<defs><clipPath id="ov-ai-clip"><circle cx="100" cy="100" r="84"/></clipPath></defs>',
      '<g id="ov-ai-world" clip-path="url(#ov-ai-clip)">',
      '  <g id="ov-ai-horizon" transform="rotate(0 100 100) translate(0 0)">',
      '    <rect x="-140" y="-520" width="480" height="620" fill="#4a7ba6"/><!-- sky -->',
      '    <rect x="-140" y="100"  width="480" height="620" fill="#7a6a50"/><!-- ground -->',
      '    <line x1="-140" y1="100" x2="340" y2="100" stroke="#ffffff" stroke-width="2.5"/>',
      '    <line x1="80" y1="80"  x2="120" y2="80"  stroke="#ffffff" stroke-width="1"/><!-- +10° -->',
      '    <line x1="86" y1="60"  x2="114" y2="60"  stroke="#ffffff" stroke-width="1"/><!-- +20° -->',
      '    <line x1="80" y1="120" x2="120" y2="120" stroke="#ffffff" stroke-width="1"/><!-- -10° -->',
      '    <line x1="86" y1="140" x2="114" y2="140" stroke="#ffffff" stroke-width="1"/><!-- -20° -->',
      '  </g>',
      '</g>',
      '<g id="ov-ai-fixed">',
      '  <circle cx="100" cy="100" r="84" fill="none" stroke="var(--border)" stroke-width="2"/>',
      '  <line x1="100" y1="12" x2="100" y2="22" stroke="var(--text)" stroke-width="2"/><!-- 0° roll ref -->',
      '  <line x1="60"  y1="100" x2="88"  y2="100" stroke="var(--text)" stroke-width="3"/><!-- wings -->',
      '  <line x1="112" y1="100" x2="140" y2="100" stroke="var(--text)" stroke-width="3"/>',
      '  <circle cx="100" cy="100" r="3.5" fill="var(--text)"/>',
      '</g>',
      '<g id="ov-ai-dead" style="display:none">',
      '  <circle cx="100" cy="100" r="84" fill="var(--card)" stroke="var(--muted)" stroke-width="2"/>',
      '  <text id="ov-ai-deadtxt" x="100" y="96" text-anchor="middle" font-size="17"',
      '    font-weight="700" fill="var(--muted)">NO DATA</text>',
      '  <text id="ov-ai-deadsub" x="100" y="112" text-anchor="middle" font-size="8"',
      '    fill="var(--muted)"></text>',
      '</g>',
      '</svg>',
    ].join('');
    var aiReadouts = AI_ROWS.map(function (r, ri) {
      return '<div class="ov-ai-ro"><div class="ov-ai-ro-label">' + r.plain + '</div>' +
             '<div class="ov-ai-ro-val ov-ai-ro-val-nodata" id="ov-ai-ro-' + ri + '">NO DATA</div></div>';
    }).join('');
    var attitude = [
      '<div class="ov-ai-box ov-ai-box-nodata" id="ov-ai-box">',
      '  <div class="ov-stage-flag ov-flag-nodata" id="ov-ai-flag">NO DATA</div>',
      '  <div class="ov-stage-title">Attitude</div>',
      '  <div class="ov-stage-hint">Frame C · c.roll / c.pitch — artificial horizon</div>',
      aiSvg,
      '  <div class="ov-ai-readouts">' + aiReadouts + '</div>',
      '  <div class="ov-sub" id="ov-ai-sub"></div>',
      '</div>',
    ].join('');

    /* ── Pre-flight checklist (read-only) ─────────────────────────────── */
    var checkRows = CHECKLIST.map(function (row, i) {
      return '<div class="ov-check-row">' +
        '<div class="ov-check-line"><span class="ov-chk-verdict ov-chk-unknown" id="ov-chk-v-' + i + '">UNKNOWN</span>' +
        '<span class="ov-chk-plain">' + row.plain + '</span></div>' +
        '<div class="ov-chk-hint">' + row.hint + '</div>' +
        '<div class="ov-chk-detail" id="ov-chk-d-' + i + '"></div>' +
        '</div>';
    }).join('');
    var checklist = [
      '<div class="ov-check">',
      '  <div class="ov-stage-title">Pre-flight checklist</div>',
      '  <div class="ov-stage-hint">read top to bottom before every flight — read-only, sends no commands</div>',
      checkRows,
      '</div>',
    ].join('');

    var instruments = '<div class="ov-instrow">' + attitude + checklist + '</div>';

    var chain = ['<div class="ov-chain">'];
    STAGES.forEach(function (st, idx) {
      if (idx > 0) chain.push('<div class="ov-arrow">→</div>');
      var rows = st.rows.map(function (r, ri) {
        return '<div class="ov-row"><span class="ov-row-plain">' + r.plain + '</span>' +
               '<span class="ov-row-val ov-row-val-nodata" id="ov-val-' + st.id + '-' + ri + '">NO DATA</span></div>';
      }).join('');
      chain.push(
        '<div class="ov-stage ov-stage-nodata" id="ov-stage-' + st.id + '">' +
        '<div class="ov-stage-flag ov-flag-nodata" id="ov-flag-' + st.id + '">NO DATA</div>' +
        '<div class="ov-stage-title">' + st.title + '</div>' +
        '<div class="ov-stage-hint">' + st.hint + '</div>' +
        rows +
        '<div class="ov-sub" id="ov-sub-' + st.id + '"></div>' +
        '</div>');
    });
    chain.push('</div>');

    var shadowRows = SHADOW_ROWS.map(function (r, ri) {
      return '<div class="ov-row"><span class="ov-row-plain">' + r.plain + '</span>' +
             '<span class="ov-row-val ov-row-val-nodata" id="ov-shadow-val-' + ri + '">NO DATA</span></div>';
    }).join('');
    var shadow = [
      '<div class="ov-shadow">',
      '  <span class="ov-shadow-badge">SHADOW — display only, not in any control path</span>',
      '  <div class="ov-stage-hint">EKF (9 states, no position states) · ekf.vel_* / ekf.bias_* — slot 0</div>',
      '  <div class="ov-grid">' + shadowRows + '</div>',
      '</div>',
    ].join('');

    return css + '<div class="ov-root">' + strip + banner + instruments + chain.join('') + shadow + '</div>';
  }

  /* ── Render ──────────────────────────────────────────────────────────── */

  function setPill(id, label, on) {
    var el = q(id);
    if (!el) return;
    if (on === true)       { el.className = 'ov-pill ov-pill-on';  el.textContent = label; }
    else if (on === false) { el.className = 'ov-pill ov-pill-off'; el.textContent = label + ' OFF'; }
    else                   { el.className = 'ov-pill ov-pill-np';  el.textContent = label + ' ?'; }
  }

  function render(state) {
    var nowMs = Date.now();
    var ttlMs = ((state && state.slot_freshness_ttl_ns) || 30e9) / 1e6;

    /* Status strip */
    var arm = findValue(state, 'status.arm');
    var armEl = q('ov-arm');
    if (armEl) {
      if (arm == null) { armEl.textContent = 'NOT PUBLISHED'; armEl.style.color = 'var(--amber)'; }
      else if (Number(arm.val) === 1) { armEl.textContent = 'ARMED'; armEl.style.color = 'var(--red)'; }
      else { armEl.textContent = 'DISARMED'; armEl.style.color = 'var(--green)'; }
    }
    var fm = findValue(state, 'status.flymode');
    var fmEl = q('ov-flymode');
    if (fmEl) {
      if (fm == null) { fmEl.textContent = 'NOT PUBLISHED'; fmEl.style.color = 'var(--amber)'; }
      else {
        var n = Math.round(Number(fm.val));
        fmEl.textContent = FLY_MODE_LABELS[n] || ('Mode ' + n);
        fmEl.style.color = '';
      }
    }
    var vb = findValue(state, 'status.vbat');
    var vbEl = q('ov-vbat');
    if (vbEl) {
      if (vb == null) { vbEl.textContent = 'NOT PUBLISHED'; vbEl.style.color = 'var(--amber)'; }
      else {
        var v = Number(vb.val);
        vbEl.textContent = v.toFixed(2) + ' V';
        vbEl.style.color = (v < VBAT_RED_V) ? 'var(--red)'
                         : (v < VBAT_AMBER_V) ? 'var(--amber)' : 'var(--green)';
      }
    }
    var rcA = findValue(state, 'status.rc_authority');
    setPill('ov-pill-rc', 'RC AUTH', rcA == null ? null : Number(rcA.val) !== 0);
    var sb = findValue(state, 'status.sbus');
    setPill('ov-pill-sbus', 'SBUS', sb == null ? null : Number(sb.val) === 0);
    var es = findValue(state, 'status.estimator_ready');
    setPill('ov-pill-est', 'ESTIMATOR', es == null ? null : Number(es.val) !== 0);

    /* Attitude indicator + pre-flight checklist (task 20260921-065323) */
    renderAttitude(state, nowMs, ttlMs);
    renderChecklist(state, nowMs, ttlMs);

    /* Mimic stages */
    STAGES.forEach(function (st) {
      var cs = classifyStage(st, state, nowMs, ttlMs);
      var box = q('ov-stage-' + st.id);
      if (box) box.className = 'ov-stage ov-stage-' + cs.cls;
      var flag = q('ov-flag-' + st.id);
      if (flag) { flag.textContent = cs.label; flag.className = 'ov-stage-flag ov-flag-' + cs.cls; }
      var sub = q('ov-sub-' + st.id);
      if (sub) sub.textContent = cs.sub;
      st.rows.forEach(function (r, ri) {
        var el = q('ov-val-' + st.id + '-' + ri);
        if (!el) return;
        var f = (cs.cls === 'nodata') ? null : findValue(state, r.key);
        if (f == null || f.val == null || isNaN(f.val)) {
          el.textContent = (cs.cls === 'nodata') ? 'NO DATA' : 'NOT PUBLISHED';
          el.className = 'ov-row-val ov-row-val-nodata';
        } else {
          el.textContent = fmtVal(r, f.val);
          el.className = 'ov-row-val';
        }
      });
    });

    /* Shadow EKF (display only) */
    SHADOW_ROWS.forEach(function (r, ri) {
      var el = q('ov-shadow-val-' + ri);
      if (!el) return;
      var f = findValue(state, r.key);
      if (f == null || f.val == null || isNaN(f.val)) {
        el.textContent = 'NOT PUBLISHED';
        el.className = 'ov-row-val ov-row-val-nodata';
      } else {
        el.textContent = fmtVal(r, f.val);
        el.className = 'ov-row-val';
      }
    });

    /* Alarm banner + list */
    var alarms = computeAlarms(state, nowMs, ttlMs);
    updateRecentAlarms(alarms, nowMs);
    alarms.sort(function (a, b) { return sevRank(b.sev) - sevRank(a.sev); });
    var worst = alarms.length ? alarms[0].sev : null;
    var banner = q('ov-banner');
    if (banner) {
      if (worst === 'red') {
        banner.className = 'ov-banner ov-banner-alarm';
        banner.textContent = '⚠ ALARM — ' + alarms[0].text;
      } else if (worst === 'amber') {
        banner.className = 'ov-banner ov-banner-warn';
        banner.textContent = 'WARNING — ' + alarms[0].text;
      } else {
        banner.className = 'ov-banner ov-banner-ok';
        banner.textContent = 'SYSTEM NORMAL — no active alarms';
      }
    }
    var listEl = q('ov-alarm-list');
    if (listEl) {
      var items = [];
      alarms.forEach(function (a) {
        items.push('<div class="ov-alarm-item ov-alarm-' + a.sev + '">' +
                   (a.sev === 'red' ? '⚠ ' : '') + a.text + '</div>');
      });
      _recentAlarms.forEach(function (r) {
        if (r.open) return;
        items.push('<div class="ov-alarm-item ov-alarm-clear">RECENT (cleared ' +
                   fmtAge(nowMs - r.clearedAt) + ' ago): ' + r.text + '</div>');
      });
      listEl.innerHTML = items.join('');
    }
  }

  /* ── Attitude indicator render ───────────────────────────────────────── */

  function renderAttitude(state, nowMs, ttlMs) {
    var cs = classifyStage(AI_STAGE, state, nowMs, ttlMs);
    var dead = (cs.cls === 'nodata');
    var box = q('ov-ai-box');
    if (box) box.className = 'ov-ai-box ov-ai-box-' + cs.cls;
    var world = q('ov-ai-world');
    if (world) world.style.display = dead ? 'none' : '';
    var deadG = q('ov-ai-dead');
    if (deadG) deadG.style.display = dead ? '' : 'none';
    var deadSub = q('ov-ai-deadsub');
    if (deadSub) deadSub.textContent = cs.sub;
    var flag = q('ov-ai-flag');
    if (flag) {
      flag.textContent = cs.label;
      flag.className = 'ov-stage-flag ov-flag-' + cs.cls;
    }
    var horizon = q('ov-ai-horizon');
    if (horizon) {
      var roll = dead ? null : findValue(state, 'c.roll');
      var pitch = dead ? null : findValue(state, 'c.pitch');
      if (roll && pitch && roll.val != null && pitch.val != null &&
          !isNaN(roll.val) && !isNaN(pitch.val)) {
        horizon.setAttribute('transform',
          'rotate(' + (-Number(roll.val)) + ' 100 100) translate(0 ' +
          (Number(pitch.val) * AI_PITCH_PPD).toFixed(2) + ')');
      }
    }
    AI_ROWS.forEach(function (r, ri) {
      var el = q('ov-ai-ro-' + ri);
      if (!el) return;
      var f = dead ? null : findValue(state, r.key);
      if (f == null || f.val == null || isNaN(f.val)) {
        el.textContent = (cs.cls === 'nodata') ? 'NO DATA' : 'NOT PUBLISHED';
        el.className = 'ov-ai-ro-val ov-ai-ro-val-nodata';
      } else {
        el.textContent = fmtVal(r, f.val);
        el.className = 'ov-ai-ro-val' + (cs.cls === 'warn' ? ' ov-ai-ro-val-warn' : '');
      }
    });
    var sub = q('ov-ai-sub');
    if (sub) sub.textContent = dead ? '' : cs.sub;
  }

  /* ── Pre-flight checklist render ─────────────────────────────────────── */

  function renderChecklist(state, nowMs, ttlMs) {
    CHECKLIST.forEach(function (row, i) {
      var verdict = row.eval(state, nowMs, ttlMs);
      var vEl = q('ov-chk-v-' + i);
      if (vEl) { vEl.textContent = verdict.v.toUpperCase(); vEl.className = 'ov-chk-verdict ov-chk-' + verdict.v; }
      var dEl = q('ov-chk-d-' + i);
      if (dEl) dEl.textContent = verdict.detail;
    });
  }

  function onState(state) {
    _lastState = state;
    render(state);
  }

  /* ── Export (shell uses window.__registerPlugin__) ───────────────────── */

  window.__PLUGIN_INIT__ = function (api) {
    api.registerPanel('System Overview', function (container) {
      container.innerHTML = buildHTML();
      api.subscribe(onState);
      render(_lastState);
      // Age readouts must keep advancing even if the /state poll itself dies,
      // so a frozen number never looks live. Cleared in __PLUGIN_DESTROY__.
      if (_tickTimer == null) {
        _tickTimer = setInterval(function () {
          if (_lastState != null) render(_lastState);
        }, 1000);
      }
    }, { workspace: 'overview', description: 'PLC-style HMI overview: alarm banner + signal-chain mimic diagram' });
  };

  window.__PLUGIN_DESTROY__ = function () {
    if (_tickTimer != null) { clearInterval(_tickTimer); _tickTimer = null; }
    _lastState = null;
    _recentAlarms = [];
  };

  if (typeof window !== 'undefined' && window.__registerPlugin__) {
    window.__registerPlugin__('System Overview', window.__PLUGIN_INIT__, window.__PLUGIN_DESTROY__,
      { workspace: 'overview', description: 'PLC-style HMI overview: alarm banner + signal-chain mimic diagram' });
  }

  if (typeof module !== 'undefined' && module.exports) {
    module.exports = {
      buildHTML: buildHTML,
      onState: onState,
      render: render,
      findValue: findValue,
      classifyStage: classifyStage,
      computeAlarms: computeAlarms,
      fmtVal: fmtVal,
      fmtAge: fmtAge,
      STAGES: STAGES,
      SHADOW_ROWS: SHADOW_ROWS,
      CHECKLIST: CHECKLIST,
      AI_ROWS: AI_ROWS,
      AI_PITCH_PPD: AI_PITCH_PPD,
      renderAttitude: renderAttitude,
      renderChecklist: renderChecklist,
      STALE_WARN_MS: STALE_WARN_MS,
      VBAT_RED_V: VBAT_RED_V,
      VBAT_AMBER_V: VBAT_AMBER_V,
    };
  }

})();
