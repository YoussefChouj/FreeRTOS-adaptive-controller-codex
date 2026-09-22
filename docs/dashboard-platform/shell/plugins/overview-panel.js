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
 *
 * Follow-ons (task 20260921-070956, HMI_DESIGN.md §7 items 3-5):
 *   - Persistent session alarm history with acknowledge / silence (the PLC
 *     standard) and a dependency-free CSV export. Acknowledge and silence
 *     are OPERATOR ACTIONS ON THE DISPLAY ONLY — they mutate nothing but
 *     this panel's own log entries and send nothing to the aircraft.
 *   - Trend-on-demand: click any value cell in the mimic diagram or the
 *     shadow box → sparkline of that key's session history.
 *   - Battery trend / time-to-empty from the status.vbat session history.
 *
 * Experiment observability (task 20260921-103141):
 *   - Flight FSM view: every state of FlightState_t / FlightPhase_t rendered,
 *     the current one highlighted, with last transition and dwell time. The
 *     state list is read from API/flight_fsm.h; it is never inferred from
 *     status.arm / status.flymode.
 *   - Adaptation (MRAC) view: the six adaptive weights per axis, their
 *     session sparklines, and a converging / drifting / frozen verdict that
 *     displays the numeric evidence it rests on — UNKNOWN until enough
 *     samples were actually received.
 *
 * Session sample history: the buffer machinery follows time-series-panel.js
 * (its `ringBuffers` / `sampleTimestamps` / `onState` ingestion, lines 67-69
 * and 646-664, including its `number | null` gap convention where a sample
 * that was never received is stored as an explicit hole, and the
 * segment-splitting of renderChart lines 321-343 that never interpolates
 * across one). Direct import was not possible: that panel's buffers are
 * closure-private and it exports no symbol, so this panel keeps its own
 * session-scoped history with identical semantics (see the task result for
 * the finding). All history here is session-scoped and lost on reload.
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

  /* ── Follow-on thresholds (task 20260921-070956, HMI_DESIGN.md §7 3-5) ──
   * SPARK_MIN_SAMPLES: below this a sparkline is not drawn at all — an
   *   explicit INSUFFICIENT HISTORY state renders instead. 10 samples at the
   *   shell's ~2 Hz /state poll ≈ 5 s of session: enough that the line shows
   *   a direction rather than two points that any noise could connect.
   * BAT_MIN_SAMPLES / BAT_MIN_SPAN_MS: a time-to-empty the operator plans a
   *   flight around needs more than instantaneous slope — ≥ 10 samples
   *   spanning ≥ 30 s of actually-received vbat values.
   * BAT_FALLING_V_PER_MIN: slopes shallower (or rising) than this give NO
   *   estimate, never a large comforting number. 0.02 V/min is well below
   *   any real 4S discharge (≈ 0.1-0.2 V/min under load) and above the
   *   jitter a 2 Hz poll of a noisy ADC rail produces.
   * ALARM_LOG_MAX: bounded episode log; overflow drops the OLDEST episodes
   *   and the drop is counted and shown, never silently discarded. */
  var SPARK_MIN_SAMPLES  = 10;
  var BAT_MIN_SAMPLES    = 10;
  var BAT_MIN_SPAN_MS    = 30000;
  var BAT_FALLING_V_PER_MIN = 0.02;
  var HIST_MAX           = 1200;  // session history bound (≈10 min at 2 Hz poll)
  var ALARM_LOG_MAX      = 500;   // alarm history episodes

  var FLY_MODE_LABELS = ['Stabilize', 'AltHold', 'PosHold', 'Auto', 'Manual', 'SDK'];

  /* ── Mimic-diagram stage model ─────────────────────────────────────────
   * Every row: plain-language label, the verified key, its unit, decimals.
   * plain = operator label; key = symbol an expert can trace. */
  /* Slot binding (2026-09-21 binding-table task): the WiFi link carries
   * ONLY the typed slot-0 subscribe stream -- the legacy Frame B/C frames
   * never arrive on USART3 in this mode, so every stage that read a Frame
   * B/C key was honestly "NO DATA" while the underlying variables streamed
   * fine. The slot-0 layout now carries the same firmware variables the
   * frame builders pack (send_data.c:1090-1130 gyro/earth/alt, :1196-1198
   * PID), and the service maps them to the same spec keys below. Stages
   * whose data is slot-0-published therefore bind to slot '0'; the motors
   * stage stays on slot 3 -- the firmware has no per-motor RPM scalar a
   * slot can subscribe to (RPM_Get computes on the fly), so it honestly
   * renders NO DATA until firmware exposes one. */
  var STAGES = [
    {
      id: 'imu', title: 'Gyro sensor', hint: 'slot 0 · Gyro_*_Real → c.gyro_* (Frame C source)',
      slot: '0',
      rows: [
        { plain: 'Roll rate',  key: 'c.gyro_x', unit: 'rad/s', dec: 2, signed: true },
        { plain: 'Pitch rate', key: 'c.gyro_y', unit: 'rad/s', dec: 2, signed: true },
        { plain: 'Yaw rate',   key: 'c.gyro_z', unit: 'rad/s', dec: 2, signed: true },
      ],
    },
    {
      id: 'filter', title: 'Rate filter (flown)', hint: 'slot 0 · Ctrler.gyro*PID.FB → pid.gyro*.FB (Frame B source)',
      slot: '0',
      rows: [
        { plain: 'Filtered roll rate',  key: 'pid.gyrox.FB', unit: 'deg/s', dec: 1, signed: true },
        { plain: 'Filtered pitch rate', key: 'pid.gyroy.FB', unit: 'deg/s', dec: 1, signed: true },
        { plain: 'Filtered yaw rate',   key: 'pid.gyroz.FB', unit: 'deg/s', dec: 1, signed: true },
      ],
    },
    {
      id: 'att', title: 'Attitude estimate', hint: 'slot 0 · imu_data.rol/pit/yaw → c.roll / c.pitch / c.yaw (Frame C source)',
      slot: '0',
      rows: [
        { plain: 'Roll',  key: 'c.roll',  unit: 'deg', dec: 1, signed: true, alt: 'status.roll_deg' },
        { plain: 'Pitch', key: 'c.pitch', unit: 'deg', dec: 1, signed: true, alt: 'status.pitch_deg' },
        { plain: 'Yaw',   key: 'c.yaw',   unit: 'deg', dec: 1, signed: true, alt: 'status.yaw_deg' },
      ],
    },
    {
      id: 'pos', title: 'Position estimate', hint: 'slot 0 · ano_of.earth_x/y + of_alt_cm → c.earth_* / c.altitude (Frame C source)',
      slot: '0',
      rows: [
        { plain: 'Position X', key: 'c.earth_x',  unit: 'm', dec: 2, signed: true },
        { plain: 'Position Y', key: 'c.earth_y',  unit: 'm', dec: 2, signed: true },
        { plain: 'Altitude',   key: 'c.altitude', unit: 'm', dec: 2, alt: 'c.altitude_cm', scale: 0.01 },
      ],
    },
    {
      id: 'rctrl', title: 'Rate controllers', hint: 'slot 0 · Ctrler.gyro*PID.U → pid.gyro*.U (Frame B source)',
      slot: '0',
      rows: [
        { plain: 'Roll output',  key: 'pid.gyrox.U', unit: 'cmd', dec: 3, signed: true },
        { plain: 'Pitch output', key: 'pid.gyroy.U', unit: 'cmd', dec: 3, signed: true },
        { plain: 'Yaw output',   key: 'pid.gyroz.U', unit: 'cmd', dec: 3, signed: true },
      ],
    },
    {
      id: 'mrac', title: 'MRAC augmentation', hint: 'slot 0 · mrac.*.u_ad / .e — adaptive add-on',
      slot: '0',
      rows: [
        { plain: 'Roll adaptive',  key: 'mrac.roll.u_ad',  unit: 'cmd',  dec: 3, signed: true },
        { plain: 'Roll error',    key: 'mrac.roll.e',     unit: 'rad/s', dec: 2, signed: true },
        { plain: 'Pitch adaptive', key: 'mrac.pitch.u_ad', unit: 'cmd',  dec: 3, signed: true },
        { plain: 'Pitch error',   key: 'mrac.pitch.e',     unit: 'rad/s', dec: 2, signed: true },
      ],
    },
    {
      id: 'motors', title: 'Motors', hint: 'Frame C · motor.rpm_0..3 — no RPM scalar to subscribe (firmware TODO)',
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
    { plain: 'Roll',  key: 'c.roll',  unit: 'deg', dec: 1, signed: true, alt: 'status.roll_deg' },
    { plain: 'Pitch', key: 'c.pitch', unit: 'deg', dec: 1, signed: true, alt: 'status.pitch_deg' },
    { plain: 'Yaw',   key: 'c.yaw',   unit: 'deg', dec: 1, signed: true, alt: 'status.yaw_deg' },
  ];

  /* Classification stage for the horizon: roll + pitch drive the picture,
   * so they — not yaw — decide whether the instrument is live. Bound to
   * slot 0 since the 2026-09-21 binding fix (attitude streams there as
   * status.roll_deg/pitch_deg/yaw_deg; Frame C never arrives on WiFi). */
  var AI_STAGE = { id: 'ai', slot: '0', rows: [AI_ROWS[0], AI_ROWS[1]] };

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

  /* ── Flight FSM model — task 20260921-103141 ────────────────────────────
   * State lists are copied verbatim from the firmware enums so the panel
   * cannot silently drift from the aircraft:
   *   FlightState_t  — API/flight_fsm.h:6-10
   *     DISARMED = 0, ARMED = 1, EMERGENCY = 2
   *   FlightPhase_t  — API/flight_fsm.h:17-22
   *     GROUND_IDLE = 0, FLYING = 1, LANDING = 2, LANDED = 3
   * Binding (read-only): neither enum is emitted by any frame decoder in
   * wifi_bridge.py. The only honest keys are the firmware's own variables,
   * reachable when a slot is subscribed to their DWARF names
   * (boot_default_layout / slot manager; raw names pass through on slot 0):
   *   FSM_STATE_KEY 's_state'     — static FlightState_t, flight_fsm.c:7
   *   FSM_PHASE_KEY 'flight_phase' — extern volatile, flight_fsm.c:8
   * They are absent from the boot-default layout, so with no custom
   * subscription this widget reads NOT PUBLISHED (see task result finding).
   * The state is NEVER inferred from status.arm / status.flymode: those are
   * synced BY the FSM, and presenting a guess from them as the state would
   * hide exactly the desync the operator needs to see. */
  var FSM_STATE_KEY = 's_state';
  var FSM_PHASE_KEY = 'flight_phase';

  var FLIGHT_STATES = [
    { val: 0, name: 'DISARMED',  plain: 'Disarmed' },
    { val: 1, name: 'ARMED',     plain: 'Armed' },
    { val: 2, name: 'EMERGENCY', plain: 'Emergency stop' },
  ];

  var FLIGHT_PHASES = [
    { val: 0, name: 'GROUND_IDLE', plain: 'Ground idle' },
    { val: 1, name: 'FLYING',      plain: 'Flying' },
    { val: 2, name: 'LANDING',     plain: 'Landing' },
    { val: 3, name: 'LANDED',      plain: 'Landed' },
  ];

  /* ── Adaptation (MRAC) model — task 20260921-103141 ─────────────────────
   * The adaptive weights are mrac_state.<axis>.Theta[0..5] in firmware
   * (API/mrac.c — the field is named Theta, not What; Whatf/What_limit are
   * different objects). Verified published:
   *   Frame B, 20 Hz, TASK/send_data.c:1176-1194 → wifi_bridge.py:1361-1366
   *   decodes mrac.<axis>.theta_0..5 → slot 1.
   * The boot slot-0 subscribe layout also streams the raw DWARF paths
   * mrac_state.<axis>.Theta[N] (boot_default_layout.py:128-152, raw
   * passthrough wifi_bridge.py:1149-1151); both spellings name the same
   * firmware variable, so the raw path is an honest alias, never a proxy.
   * Weight basis meanings: API/mrac.c:373 comment
   *   [bias, proportional, derivative, drag, structured, unstructured]. */
  var ADAPT_AXES = [
    { axis: 'roll',   label: 'Roll' },
    { axis: 'pitch',  label: 'Pitch' },
    { axis: 'yaw',    label: 'Yaw' },
    { axis: 'z_rate', label: 'Z rate' },
  ];

  var WEIGHT_PLAIN = ['bias', 'proportional', 'derivative', 'drag', 'structured', 'unstructured'];

  var ADAPT_WEIGHTS = (function () {
    var out = [];
    ADAPT_AXES.forEach(function (a) {
      for (var n = 0; n < 6; n++) {
        out.push({ axis: a.axis, axisLabel: a.label, n: n,
          key: 'mrac.' + a.axis + '.theta_' + n,
          raw: 'mrac_state.' + a.axis + '.Theta[' + n + ']',
          plain: WEIGHT_PLAIN[n] });
      }
    });
    return out;
  })();

  /* Verdict thresholds. Theta scale comes from the firmware config in
   * API/mrac.c:379-380 (roll/pitch What_limit 0.02-0.20, What_tol
   * 0.005-0.04). Samples are polled at the shell's ~2 Hz /state cadence.
   * ADAPT_WINDOW: snapshots examined, ≈ 10 s at 2 Hz.
   * ADAPT_MIN_SNAPS / ADAPT_MIN_SPAN_MS: below either, the verdict is
   *   UNKNOWN — 10 snapshots spanning ≥ 5 s, matching SPARK_MIN_SAMPLES.
   * ADAPT_MIN_KEYS: an axis must publish at least 4 of 6 weights.
   * FROZEN_STEP: mean |Δv| per polled sample below 5e-4 = 1/10 of the
   *   smallest What_tol (0.005); FROZEN_TRAVEL 1e-3 is the matching bound
   *   on half-window mean travel. Below both, weights are not moving.
   * CONVERGE_RATIO: late-window movement below half the early-window
   *   movement = the adaptation transient has decayed → converging. */
  var ADAPT_WINDOW      = 20;
  var ADAPT_MIN_SNAPS   = 10;
  var ADAPT_MIN_SPAN_MS = 5000;
  var ADAPT_MIN_KEYS    = 4;
  var FROZEN_STEP       = 0.0005;
  var FROZEN_TRAVEL     = 0.001;
  var CONVERGE_RATIO    = 0.5;

  /* ── State ───────────────────────────────────────────────────────────── */
  var _lastState = null;
  var _tickTimer = null;
  var _recentAlarms = [];   // [{id, text, clearedAt}] — cleared but still visible

  /* FSM transition tracking (task 20260921-103141), session-scoped. Per
   * kind: last value observed, the value before it, entry timestamp (firmware
   * ns when the key carries one, else host ms) and the host-clock time of the
   * last change. Absence of the key changes nothing — a NOT PUBLISHED spell
   * must not look like a transition. */
  function freshTrack() {
    return { last: null, prev: null, sinceTs: null, sinceMs: null, atTs: null, atMs: null };
  }
  var _fsmTrans = { state: freshTrack(), phase: freshTrack() };

  /* Follow-on state (task 20260921-070956). All of it is session-scoped:
   * a page reload starts every buffer, the alarm log and the trend back
   * empty. Nothing here is persisted, and nothing here is ever sent to the
   * aircraft — the only writers are telemetry ingestion (read-only display)
   * and the operator's display-local acknowledge / silence / export clicks. */
  var _hist = {};           // key → [{t: ns|null, v: number|null}] — null = gap, never interpolated
  var _trendKey = null;     // key whose sparkline is open, or null
  var _alarmLog = [];       // episodes [{ref, id, sev, text, textEnd, raisedAt, clearedAt, ack, silenced}]
  var _openEpisodes = {};   // id → open episode (alarm currently active)
  var _episodeSeq = 0;      // stable per-episode button reference
  var _alarmLogDropped = 0; // episodes evicted by the ALARM_LOG_MAX bound

  /* Every key a value cell can sparkline: mimic stages + shadow + battery +
   * the adaptation weights (task 20260921-103141). Weight history is stored
   * under its dotted Frame B key; _weightAlias points at the raw DWARF path
   * the slot-0 subscribe stream uses when the dotted key is absent. */
  var _weightAlias = (function () {
    var m = {};
    ADAPT_WEIGHTS.forEach(function (w) { m[w.key] = w.raw; });
    return m;
  })();

  var HISTORY_KEYS = (function () {
    var seen = {}, out = [];
    function add(k) { if (k && !seen[k]) { seen[k] = true; out.push(k); } }
    STAGES.forEach(function (st) { st.rows.forEach(function (r) { add(r.key); }); });
    SHADOW_ROWS.forEach(function (r) { add(r.key); });
    add('status.vbat');
    ADAPT_WEIGHTS.forEach(function (w) { add(w.key); });
    return out;
  })();

  /* History lookup with the slot-0 raw alias. Returns the same shape as
   * findValue ({val, ts, ...}) or null. Both bindings are the same firmware
   * variable, so this is not a proxy — it is another spelling of one value.
   * Stage rows may also declare an `alt` (the slot-0 spec spelling of a
   * legacy Frame B/C key) and a `scale` (unit conversion the legacy frame
   * builder applied, e.g. ano_of.of_alt_cm cm → m) — folded in here so
   * cells and sparklines always show the same unit. */
  var _rowAlias = (function () {
    var m = {};
    STAGES.forEach(function (st) {
      st.rows.forEach(function (r) { if (r.alt) m[r.key] = r.alt; });
    });
    AI_ROWS.forEach(function (r) { if (r.alt) m[r.key] = r.alt; });
    return m;
  })();
  var _keyScale = (function () {
    var m = {};
    STAGES.forEach(function (st) {
      st.rows.forEach(function (r) { if (r.scale) m[r.key] = r.scale; });
    });
    return m;
  })();
  // Merge stage/AI aliases into the weight alias map (single lookup table).
  (function () {
    for (var k in _rowAlias) {
      if (!_weightAlias[k]) _weightAlias[k] = _rowAlias[k];
    }
  })();

  function findHistoryValue(state, key) {
    var f = findValue(state, key);
    if (f && f.val != null) return f;
    var raw = _weightAlias[key];
    if (raw) {
      var g = findValue(state, raw);
      if (g && g.val != null) return g;
    }
    return f;
  }

  /* Row lookup for stage cells: key → alt fallback, then unit scale. */
  function findRowValue(state, row) {
    var f = (row.alt) ? findHistoryValue(state, row.key) : findValue(state, row.key);
    if (f && f.val != null && row.scale) {
      f = { val: Number(f.val) * row.scale, ts: f.ts,
            slotName: f.slotName, loss: f.loss };
    }
    return f;
  }

  /* ── "not published: in preset X" hint (operator walkthrough 2 item 2) ──
   * A mimic block that reads no published-on-this-stream symbol is pure
   * blank/NO DATA today. When that happens we look up which Live-Log
   * preset's slot manifest WOULD publish the block's first symbol and
   * surface it on the block's sub-line so the operator sees the cause
   * instead of an empty box.
   *
   * This panel is READ ONLY — it never sends anything (verified by the
   * read-only harness). The GET /api/preset-for-symbol call is made by the
   * SHELL, which caches the result and exposes it synchronously here via
   * window.__gs_preset_for_symbol(symbol). If the shell has not furnished
   * the helper (or it returns no carrier), we honestly keep the
   * "not published by this build" line unchanged. */
  var _npHint = {};   // stageId -> { state, symbol, text }
  function ensurePresetHint(stage) {
    var c = _npHint[stage.id];
    if (c && (c.state === 'loading' || c.state === 'done' || c.state === 'failed')) return c.text;
    var win = (typeof window !== 'undefined') ? window : null;
    var presetLookup = win && typeof win.__gs_preset_for_symbol === 'function'
      ? win.__gs_preset_for_symbol : null;
    var key = (stage.rows[0] && stage.rows[0].key) || '';
    if (!key || !presetLookup) {
      _npHint[stage.id] = { state: 'failed', symbol: key, text: null };
      return null;
    }
    _npHint[stage.id] = { state: 'loading', symbol: key, text: null };
    var carriers = presetLookup(key);   // null while the shell's fetch is in flight
    if (carriers == null) return null;  // still resolving — keep the honest line
    var text;
    if (Array.isArray(carriers) && carriers.length) {
      var names = [];
      carriers.forEach(function (p) { if (p && p.preset && names.indexOf(p.preset) === -1) names.push(p.preset); });
      text = 'not published: in preset ' + (names.slice(0, 2).join(', ') || '?') +
             ' · carriers ' + key;
    } else {
      text = 'not published by this build · no preset carries ' + key;
    }
    _npHint[stage.id] = { state: 'done', symbol: key, text: text };
    return text;
  }

  /* Value-cell id → key, for the click-to-trend delegation. */
  var _cellKeys = (function () {
    var m = {};
    STAGES.forEach(function (st) {
      st.rows.forEach(function (r, ri) { m['ov-val-' + st.id + '-' + ri] = r.key; });
    });
    SHADOW_ROWS.forEach(function (r, ri) { m['ov-shadow-val-' + ri] = r.key; });
    return m;
  })();

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
      var r = stage.rows[i];
      if (s.values[r.key] != null || (r.alt && s.values[r.alt] != null)) { anyKey = true; break; }
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

  /* ── Session sample history (task 20260921-070956) ─────────────────────
   * One sample per /state snapshot, per key — the same per-snapshot ingestion
   * cadence as time-series-panel.js `onState`. A key that is absent, or whose
   * value is older than the slot TTL, is stored as an explicit GAP ({t,v} both
   * null, or t kept with v null when the sample is merely stale) — the
   * `number | null` convention of time-series-panel.js `ringBuffers`. A gap is
   * never interpolated through: the sparkline breaks the line and greys a band
   * over the hole. */
  function ingestHistory(state) {
    var nowMs = Date.now();
    var ttlMs = ((state && state.slot_freshness_ttl_ns) || 30e9) / 1e6;
    for (var ki = 0; ki < HISTORY_KEYS.length; ki++) {
      var key = HISTORY_KEYS[ki];
      if (!_hist[key]) _hist[key] = [];
      var f = findHistoryValue(state, key);
      var sample = { t: null, v: null };
      if (f && f.val != null && !isNaN(f.val) && f.ts != null) {
        sample.t = f.ts;
        sample.v = (nowMs - f.ts / 1e6) <= ttlMs
          ? Number(f.val) * (_keyScale[key] || 1)  // row unit scale (cm → m etc.)
          : null; // stale → gap
      }
      _hist[key].push(sample);
      if (_hist[key].length > HIST_MAX) _hist[key].shift();
    }
  }

  function historySpanMs(key) {
    var h = _hist[key] || [];
    var first = null, last = null;
    for (var i = 0; i < h.length; i++) {
      if (h[i] && h[i].v != null && h[i].t != null) {
        if (first == null) first = h[i].t;
        last = h[i].t;
      }
    }
    if (first == null) return null;
    return (last - first) / 1e6;
  }

  /* ── Sparkline (trend-on-demand) ───────────────────────────────────────
   * Single series, so no legend — the box title names the key. The line
   * wears the neutral text token, NOT a state colour: HMI_DESIGN.md §1
   * reserves green/amber/red/grey for HMI states, and status colours are
   * never chart colours. Gaps break the polyline (the segment-splitting of
   * time-series-panel.js renderChart) and get a muted band so the hole is
   * visible, not bridged. */
  var SPARK_W = 240, SPARK_H = 44, SPARK_PAD = 5;

  function sparklineData(key) {
    var h = _hist[key] || [];
    var pts = [], gaps = 0, inGap = false;
    for (var i = 0; i < h.length; i++) {
      if (h[i] && h[i].v != null && !isNaN(h[i].v)) {
        pts.push({ i: i, v: h[i].v });
        inGap = false;
      } else if (!inGap) {
        gaps++;
        inGap = true;
      }
    }
    return { total: h.length, pts: pts, gaps: gaps, spanMs: historySpanMs(key) };
  }

  function buildSparklineSvg(key) {
    var d = sparklineData(key);
    if (d.pts.length < SPARK_MIN_SAMPLES || d.pts.length < 2) {
      return { insufficient: true, nValid: d.pts.length, nTotal: d.total,
        minNeeded: SPARK_MIN_SAMPLES, gaps: d.gaps, spanMs: d.spanMs, svg: '' };
    }
    var i0 = d.pts[0].i, i1 = d.pts[d.pts.length - 1].i;
    var vmin = Infinity, vmax = -Infinity;
    d.pts.forEach(function (p) {
      if (p.v < vmin) vmin = p.v;
      if (p.v > vmax) vmax = p.v;
    });
    if (vmax === vmin) { vmin -= 1; vmax += 1; }  // flat data: give the line a lane
    var innerW = SPARK_W - 2 * SPARK_PAD, innerH = SPARK_H - 2 * SPARK_PAD;
    function x(i) { return SPARK_PAD + (i - i0) * (innerW / Math.max(1, i1 - i0)); }
    function y(v) { return SPARK_PAD + innerH - ((v - vmin) / (vmax - vmin)) * innerH; }

    // Gap bands: muted rectangles over runs of missing samples BETWEEN the
    // first and last valid sample, so the hole is a visible hole.
    var gapRects = [];
    var h = _hist[key], runStart = null;
    for (var i = i0; i <= i1; i++) {
      var missing = !(h[i] && h[i].v != null && !isNaN(h[i].v));
      if (missing && runStart == null) runStart = i;
      if ((!missing || i === i1) && runStart != null) {
        var runEnd = missing ? i : i - 1;
        var x0 = x(runStart) - (innerW / Math.max(1, i1 - i0)) / 2;
        var x1 = x(runEnd) + (innerW / Math.max(1, i1 - i0)) / 2;
        gapRects.push('<rect class="ov-spark-gap" x="' + Math.max(0, x0).toFixed(1) +
          '" y="' + SPARK_PAD + '" width="' + Math.max(1, x1 - Math.max(0, x0)).toFixed(1) +
          '" height="' + innerH + '"/>');
        runStart = null;
      }
    }

    // Segments: the polyline BREAKS at every gap — never interpolated through.
    var segments = [], cur = [];
    for (var j = 0; j < d.pts.length; j++) {
      if (cur.length && d.pts[j].i !== d.pts[j - 1].i + 1) { segments.push(cur); cur = []; }
      cur.push(x(d.pts[j].i).toFixed(1) + ',' + y(d.pts[j].v).toFixed(1));
    }
    if (cur.length) segments.push(cur);
    var lines = segments.map(function (seg) {
      return '<polyline class="ov-spark-line" fill="none" points="' + seg.join(' ') + '"/>';
    }).join('');

    var lastPt = d.pts[d.pts.length - 1];
    return {
      insufficient: false, nValid: d.pts.length, nTotal: d.total, minNeeded: SPARK_MIN_SAMPLES,
      gaps: d.gaps, spanMs: d.spanMs, vmin: vmin, vmax: vmax,
      svg: '<svg class="ov-spark" viewBox="0 0 ' + SPARK_W + ' ' + SPARK_H +
        '" preserveAspectRatio="none" role="img" aria-label="session trend sparkline">' +
        lines + gapRects.join('') +
        '<circle class="ov-spark-last" cx="' + x(lastPt.i).toFixed(1) + '" cy="' + y(lastPt.v).toFixed(1) + '" r="2.5">' +
        '<title>last: ' + String(lastPt.v) + '</title></circle>' +
        '</svg>',
    };
  }

  /* ── Battery trend / time-to-empty (HMI_DESIGN.md §7 item 5) ───────────
   * Highest-risk widget in the panel: the number an operator plans a flight
   * around. Slope is least-squares over the status.vbat samples ACTUALLY
   * RECEIVED this session (each timestamped by the firmware, not by the
   * poll). A rising or flat slope (shallower than BAT_FALLING_V_PER_MIN)
   * yields NO estimate — never a large comforting number. The basis (sample
   * count, time span) is always shown next to the estimate. "Empty" is the
   * firmware's own beep threshold VBAT_RED_V (15.0 V), not 0 V: that is the
   * actionable floor this aircraft itself alarms on. */
  function computeBatteryTrend(state) {
    var out = { published: false, n: 0, spanMs: null, slopeVPerMin: null,
      tteMin: null, vLast: null, atOrBelowFloor: false };
    var f = findValue(state, 'status.vbat');
    if (!f || f.val == null || isNaN(f.val)) return out;  // not published by this build
    out.published = true;
    var h = _hist['status.vbat'] || [];
    var xs = [], ys = [];
    for (var i = 0; i < h.length; i++) {
      if (h[i] && h[i].v != null && h[i].t != null && !isNaN(h[i].v)) {
        xs.push(h[i].t); ys.push(h[i].v);
      }
    }
    out.n = xs.length;
    if (out.n === 0) return out;
    out.vLast = ys[ys.length - 1];
    out.atOrBelowFloor = out.vLast <= VBAT_RED_V;
    if (out.n < 2) return out;
    out.spanMs = (xs[xs.length - 1] - xs[0]) / 1e6;
    if (out.n < BAT_MIN_SAMPLES || out.spanMs < BAT_MIN_SPAN_MS) return out;
    // Least squares slope in V per second (normalised to xs[0] to avoid
    // catastrophic cancellation from squaring raw nanoseconds ~1.7e18).
    var n = out.n, sx = 0, sy = 0, sxy = 0, sxx = 0, x0 = xs[0];
    for (var j = 0; j < n; j++) {
      var dtSec = (xs[j] - x0) / 1e9;
      sx += dtSec; sy += ys[j]; sxy += dtSec * ys[j]; sxx += dtSec * dtSec;
    }
    var denom = n * sxx - sx * sx;
    if (denom === 0) return out;
    var slopeVPerSec = (n * sxy - sx * sy) / denom;
    out.slopeVPerMin = slopeVPerSec * 60;   // 1 min = 60 s
    if (out.slopeVPerMin < -BAT_FALLING_V_PER_MIN) {
      out.tteMin = Math.max(0, (out.vLast - VBAT_RED_V) / (-out.slopeVPerMin));
    }
    return out;
  }

  /* ── Flight FSM logic (task 20260921-103141) ─────────────────────────── */

  /* Record observed value changes for one FSM kind. Only a real published
   * value drives it — an absent key does not. */
  function trackFsmKind(kind, f, nowMs) {
    var tr = _fsmTrans[kind];
    if (!f || f.val == null || isNaN(f.val)) return;
    var n = Number(f.val);
    if (tr.last === null) {
      tr.last = n; tr.sinceTs = f.ts; tr.sinceMs = nowMs;
    } else if (n !== tr.last) {
      tr.prev = tr.last;
      tr.last = n;
      tr.atMs = nowMs; tr.atTs = f.ts;
      tr.sinceMs = nowMs; tr.sinceTs = f.ts;
    }
  }

  /* Four honesty states for a raw-key binding, same distinctions as
   * status-panel.js: not published / stale (age shown) / frozen / live. */
  function keyLiveState(f, nowMs, ttlMs) {
    if (!f || f.val == null || isNaN(f.val)) {
      return { cls: 'np', label: 'NOT PUBLISHED', sub: 'not published by this build', age: null };
    }
    var age = (f.ts != null) ? Math.max(0, nowMs - f.ts / 1e6) : null;
    if (age != null && age > ttlMs) {
      return { cls: 'nodata', label: 'NO DATA', sub: 'frozen ' + fmtAge(age), age: age };
    }
    if (age != null && age > STALE_WARN_MS) {
      return { cls: 'warn', label: 'STALE', sub: 'age ' + fmtAge(age), age: age };
    }
    return { cls: 'ok', label: 'LIVE', sub: '', age: age };
  }

  /* Time in the current value. Firmware timestamps when both ends carry
   * them, else host wall clock. */
  function fsmDwell(tr, f, nowMs) {
    if (tr.last === null || !f) return null;
    if (f.ts != null && tr.sinceTs != null) return Math.max(0, (f.ts - tr.sinceTs) / 1e6);
    if (tr.sinceMs != null) return Math.max(0, nowMs - tr.sinceMs);
    return null;
  }

  /* ── Adaptation verdict (task 20260921-103141) ─────────────────────────
   * An INFERENCE over received samples, so every number behind it is
   * returned for display. Window = trailing ADAPT_WINDOW snapshots; per
   * weight the value series is split chronologically, and the mean step
   * |Δv| of the early vs late half says whether the transient decayed. */

  function arrMean(xs) {
    var s = 0;
    for (var i = 0; i < xs.length; i++) s += xs[i];
    return xs.length ? s / xs.length : null;
  }

  function stepMean(pts) {
    if (pts.length < 2) return null;
    var s = 0;
    for (var i = 1; i < pts.length; i++) s += Math.abs(pts[i].v - pts[i - 1].v);
    return s / (pts.length - 1);
  }

  function computeAdaptVerdict(axis, nowMs) {
    var weights = [];
    for (var wi = 0; wi < ADAPT_WEIGHTS.length; wi++) {
      if (ADAPT_WEIGHTS[wi].axis === axis) weights.push(ADAPT_WEIGHTS[wi]);
    }
    var out = { verdict: 'unknown', publishedKeys: 0, nSnaps: 0, spanMs: null,
      samplesMin: null, samplesMax: null, stepEarly: null, stepLate: null,
      travel: null, ratio: null, reason: '' };

    var lenMin = Infinity, ptsByKey = [];
    for (var k = 0; k < weights.length; k++) {
      var h = _hist[weights[k].key] || [];
      if (h.length < lenMin) lenMin = h.length;
      var i0 = Math.max(0, h.length - ADAPT_WINDOW), pts = [];
      for (var i = i0; i < h.length; i++) {
        if (h[i] && h[i].v != null && h[i].t != null && !isNaN(h[i].v)) {
          pts.push({ t: h[i].t, v: h[i].v });
        }
      }
      if (pts.length) out.publishedKeys++;
      ptsByKey.push(pts);
    }
    if (lenMin === Infinity) lenMin = 0;

    // Snapshots in window where ≥ ADAPT_MIN_KEYS weights were present.
    var absStart = Math.max(0, lenMin - ADAPT_WINDOW);
    var nSnaps = 0, tMin = null, tMax = null;
    for (var s = absStart; s < lenMin; s++) {
      var present = 0;
      for (var kk = 0; kk < weights.length; kk++) {
        var hh = _hist[weights[kk].key] || [];
        if (hh[s] && hh[s].v != null && !isNaN(hh[s].v)) {
          present++;
          if (hh[s].t != null) {
            if (tMin == null || hh[s].t < tMin) tMin = hh[s].t;
            if (tMax == null || hh[s].t > tMax) tMax = hh[s].t;
          }
        }
      }
      if (present >= ADAPT_MIN_KEYS) nSnaps++;
    }
    out.nSnaps = nSnaps;
    out.spanMs = (tMin != null && tMax != null) ? (tMax - tMin) / 1e6 : null;
    var counts = ptsByKey.map(function (p) { return p.length; });
    out.samplesMin = counts.length ? Math.min.apply(null, counts) : 0;
    out.samplesMax = counts.length ? Math.max.apply(null, counts) : 0;

    if (out.publishedKeys < ADAPT_MIN_KEYS) {
      out.reason = 'only ' + out.publishedKeys + ' of 6 weights published by this build';
      return out;
    }
    if (nSnaps < ADAPT_MIN_SNAPS || out.spanMs == null || out.spanMs < ADAPT_MIN_SPAN_MS) {
      out.reason = 'need ≥ ' + ADAPT_MIN_SNAPS + ' snapshots spanning ≥ ' +
        fmtAge(ADAPT_MIN_SPAN_MS) + ' — have ' + nSnaps +
        (out.spanMs != null ? ' spanning ' + fmtAge(out.spanMs) : ' with no timestamps');
      return out;
    }

    var stepEarly = -Infinity, stepLate = -Infinity, travel = -Infinity;
    for (var p = 0; p < ptsByKey.length; p++) {
      var pts = ptsByKey[p];
      var half = Math.floor(pts.length / 2);
      var early = pts.slice(0, half), late = pts.slice(half);
      if (!early.length || !late.length) continue;
      var se = stepMean(early), sl = stepMean(late);
      if (se != null && se > stepEarly) stepEarly = se;
      if (sl != null && sl > stepLate) stepLate = sl;
      var tv = Math.abs(arrMean(late.map(function (q2) { return q2.v; })) -
                       arrMean(early.map(function (q2) { return q2.v; })));
      if (tv > travel) travel = tv;
    }
    if (stepEarly === -Infinity || stepLate === -Infinity || travel === -Infinity) {
      out.reason = 'not enough consecutive samples within the window';
      return out;
    }
    out.stepEarly = stepEarly; out.stepLate = stepLate; out.travel = travel;
    out.ratio = (stepEarly > 0) ? stepLate / stepEarly : null;

    if (stepLate < FROZEN_STEP && travel < FROZEN_TRAVEL) out.verdict = 'frozen';
    else if (out.ratio != null && out.ratio < CONVERGE_RATIO) out.verdict = 'converging';
    else out.verdict = 'drifting';
    return out;
  }

  /* ── Alarm history (HMI_DESIGN.md §7 item 3) ───────────────────────────
   * Session-scoped episode log: one episode per raise, closed by its clear.
   * Raise and clear are both events the operator sees, timestamped. The
   * PLC-standard operator actions live here too:
   *   ACKNOWLEDGE — marks the episode as seen; it STAYS in the log and stays
   *     visibly distinct (ACK tag) from one never acknowledged. Touches
   *     nothing but the log entry.
   *   SILENCE — suppresses the visual nag on the display (banner + active
   *     list dim, SILENCED tag); the record is untouched and the condition
   *     keeps logging. Touches nothing but the log entry.
   * Neither action sends, arms or gates anything — see onContainerClick. */
  function updateAlarmLog(active, nowMs) {
    var activeIds = {};
    active.forEach(function (a) {
      if (a.id === 'notelem') return; // initial absent state is not a flight alarm episode
      activeIds[a.id] = a;
    });
    Object.keys(activeIds).forEach(function (id) {
      var a = activeIds[id];
      if (!_openEpisodes[id]) {
        var ep = { ref: ++_episodeSeq, id: id, sev: a.sev, text: a.text, textEnd: a.text,
          raisedAt: nowMs, clearedAt: null, ack: false, silenced: false };
        _alarmLog.push(ep);
        _openEpisodes[id] = ep;
        if (_alarmLog.length > ALARM_LOG_MAX) {
          _alarmLog.shift();
          _alarmLogDropped++;
        }
      } else {
        _openEpisodes[id].textEnd = a.text;  // age-bearing texts keep their latest wording
      }
    });
    Object.keys(_openEpisodes).forEach(function (id) {
      if (!activeIds[id]) {
        _openEpisodes[id].clearedAt = nowMs;
        delete _openEpisodes[id];
      }
    });
  }

  /* Display-local operator action. No api call, no command, no gate. */
  function alarmAction(ref, action) {
    for (var i = 0; i < _alarmLog.length; i++) {
      if (_alarmLog[i].ref === ref) {
        if (action === 'ack') _alarmLog[i].ack = true;
        else if (action === 'silence') _alarmLog[i].silenced = !_alarmLog[i].silenced;
        if (_lastState != null) render(_lastState);
        return;
      }
    }
  }

  /* An active alarm id is silenced when its open episode was silenced. */
  function silencedAlarmIds() {
    var out = {};
    Object.keys(_openEpisodes).forEach(function (id) {
      if (_openEpisodes[id].silenced) out[id] = true;
    });
    return out;
  }

  function fmtClock(ms) {
    var d = new Date(ms);
    function p2(n) { return (n < 10 ? '0' : '') + n; }
    return p2(d.getHours()) + ':' + p2(d.getMinutes()) + ':' + p2(d.getSeconds());
  }

  function iso(ms) { return ms == null ? '' : new Date(ms).toISOString(); }

  function csvField(s) {
    return '"' + String(s == null ? '' : s).replace(/"/g, '""') + '"';
  }

  /* Dependency-free CSV export of the whole session alarm log. */
  function exportAlarmLogCsv() {
    var rows = [['raised_at', 'cleared_at', 'id', 'severity', 'text_raised', 'text_last', 'acknowledged', 'silenced']];
    _alarmLog.forEach(function (ep) {
      rows.push([iso(ep.raisedAt), iso(ep.clearedAt), ep.id, ep.sev,
        ep.text, ep.textEnd, ep.ack ? 'yes' : 'no', ep.silenced ? 'yes' : 'no']);
    });
    return rows.map(function (r) { return r.map(csvField).join(','); }).join('\r\n') + '\r\n';
  }

  /* Browser download of the CSV. Guarded so the offline harness (no Blob /
   * URL / createElement) skips the download and only the string is used. */
  function downloadAlarmCsv() {
    var csv = exportAlarmLogCsv();
    if (typeof Blob === 'undefined' || typeof URL === 'undefined' ||
        !URL.createObjectURL || typeof document === 'undefined' || !document.createElement) {
      return csv;
    }
    var blob = new Blob([csv], { type: 'text/csv' });
    var url = URL.createObjectURL(blob);
    var a = document.createElement('a');
    a.href = url;
    a.download = 'alarm-history-' + new Date().toISOString().replace(/[:.]/g, '-') + '.csv';
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    setTimeout(function () { URL.revokeObjectURL(url); }, 1000);
    return csv;
  }

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
      '  padding:16px 8px 6px 8px; background:var(--card); position:relative; box-sizing:border-box; }',
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
      '  padding:16px 8px 6px 8px; background:var(--card); position:relative; box-sizing:border-box; }',
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
      '.ov-row-val:hover, .ov-shadow .ov-row-val:hover { text-decoration:underline dotted; cursor:pointer; }',
      '.ov-hist { border:1px solid var(--border); border-radius:6px; padding:6px 10px; margin-bottom:10px; background:var(--card); }',
      '.ov-hist-head { display:flex; align-items:center; gap:8px; flex-wrap:wrap; }',
      '.ov-hist-title { font-size:11px; font-weight:700; }',
      '.ov-hist-note { font-size:9px; color:var(--muted); }',
      '.ov-hist-btn { font-size:9px; font-weight:600; padding:1px 7px; border:1px solid var(--border);',
      '  border-radius:3px; background:transparent; color:var(--text); cursor:pointer; }',
      '.ov-hist-btn:hover { border-color:var(--text); }',
      '.ov-hist-row { display:flex; align-items:baseline; gap:8px; padding:2px 0; font-size:10px;',
      '  border-bottom:1px solid var(--border); }',
      '.ov-hist-row:last-child { border-bottom:none; }',
      '.ov-hist-ts { font-family:Consolas,monospace; color:var(--muted); flex:0 0 52px; }',
      '.ov-hist-ev { font-weight:700; flex:0 0 58px; }',
      '.ov-hist-ev-raised-red { color:var(--red); }',
      '.ov-hist-ev-raised-amber { color:var(--amber); }',
      '.ov-hist-ev-cleared { color:var(--muted); }',
      '.ov-hist-text { flex:1 1 auto; }',
      '.ov-hist-ep-acked .ov-hist-text { font-style:italic; opacity:0.75; }',
      '.ov-hist-tag { font-size:8px; font-weight:700; padding:0 4px; border-radius:2px;',
      '  letter-spacing:0.05em; text-transform:uppercase; }',
      '.ov-hist-tag-ack { border:1px solid var(--muted); color:var(--muted); }',
      '.ov-hist-tag-sil { background:rgba(136,136,170,0.15); color:var(--muted); }',
      '.ov-hist-silenced .ov-hist-text { opacity:0.55; }',
      '.ov-trendrow { display:flex; gap:12px; flex-wrap:wrap; align-items:stretch; margin-bottom:10px; }',
      '.ov-trend-box { flex:1 1 300px; border:2px solid var(--border); border-radius:6px;',
      '  padding:6px 10px; background:var(--card); box-sizing:border-box; }',
      /* Fullscreen expand/shrink toggle for the trend plot (item 4) */
      '.ov-trend-box.ov-expanded {',
      '  position: fixed; inset: 0; z-index: 2000; width: 100vw; height: 100vh;',
      '  max-width: none; max-height: none; border: none; border-radius: 0;',
      '  background: var(--card); padding: 24px; overflow: auto;',
      '}',
      '.ov-trend-box.ov-expanded .ov-stage-title { font-size:14px; }',
      '.ov-trend-box.ov-expanded .ov-spark { max-width:none; height: 62vh; }',
      '.ov-trend-box.ov-expanded .ov-sub { font-size:12px; }',
      '.ov-spark { display:block; width:100%; max-width:280px; height:44px; margin-top:4px; }',
      '.ov-spark-line { stroke:var(--text); stroke-width:2; }',
      '.ov-spark-gap { fill:rgba(136,136,170,0.28); }',
      '.ov-spark-last { fill:var(--text); }',
      '.ov-bat-tte { font-family:Consolas,monospace; font-size:14px; font-weight:700; }',
      '.ov-bat-none { font-size:11px; font-weight:600; }',
      /* Flight FSM (task 20260921-103141) */
      '.ov-fsm { border:2px solid var(--border); border-radius:6px; padding:6px 10px;',
      '  margin-bottom:10px; background:var(--card); }',
      '.ov-fsm-chain { display:flex; align-items:center; gap:4px; flex-wrap:wrap; margin:3px 0; }',
      '.ov-fsm-state { padding:3px 10px; border:1px solid var(--border); border-radius:4px;',
      '  font-size:10px; font-weight:700; letter-spacing:0.03em; color:var(--muted);',
      '  background:transparent; white-space:nowrap; }',
      '.ov-fsm-arrow { color:var(--muted); font-size:11px; }',
      '.ov-fsm-cur-ok     { border-color:var(--green); color:var(--green); background:rgba(78,204,163,0.12); }',
      '.ov-fsm-cur-warn   { border-color:var(--amber); color:var(--amber); background:rgba(245,166,35,0.12); }',
      '.ov-fsm-cur-nodata { border-color:var(--muted); color:var(--muted); border-style:dashed; }',
      '.ov-fsm-kindlabel { font-size:10px; color:var(--muted); min-width:46px; }',
      '.ov-fsm-meta { font-size:10px; color:var(--muted); font-family:Consolas,monospace;',
      '  display:flex; gap:14px; flex-wrap:wrap; margin-top:2px; }',
      '.ov-fsm-np { color:var(--amber); font-weight:700; font-size:11px; }',
      '.ov-fsm-line { display:flex; align-items:center; gap:8px; flex-wrap:wrap; margin:2px 0; }',
      /* Adaptation (task 20260921-103141) */
      '.ov-adapt { border:2px solid var(--border); border-radius:6px; padding:6px 10px;',
      '  margin-bottom:10px; background:var(--card); }',
      '.ov-adapt-axes { display:flex; gap:12px; flex-wrap:wrap; }',
      '.ov-adapt-axis { flex:1 1 420px; min-width:340px; border:1px solid var(--border);',
      '  border-radius:5px; padding:6px 8px; }',
      '.ov-adapt-head { display:flex; align-items:baseline; gap:8px; flex-wrap:wrap; }',
      '.ov-adapt-verdict { display:inline-block; min-width:104px; text-align:center; padding:2px 8px;',
      '  border-radius:3px; font-size:10px; font-weight:700; letter-spacing:0.04em; text-transform:uppercase; }',
      '.ov-adapt-converging { background:rgba(78,204,163,0.15); color:var(--green); }',
      '.ov-adapt-drifting   { background:rgba(245,166,35,0.18); color:var(--amber); }',
      '.ov-adapt-frozen     { background:rgba(136,136,170,0.15); color:var(--muted); }',
      '.ov-adapt-unknown    { background:rgba(136,136,170,0.12); color:var(--muted); }',
      '.ov-adapt-evidence { font-size:9px; color:var(--muted); font-family:Consolas,monospace;',
      '  margin:3px 0 5px 0; }',
      '.ov-weight-grid { display:grid; grid-template-columns:repeat(3,minmax(120px,1fr)); gap:6px 10px; }',
      '.ov-weight-cell { border:1px solid var(--border); border-radius:4px; padding:3px 6px; }',
      '.ov-weight-title { font-size:9px; color:var(--muted); display:flex; justify-content:space-between; }',
      '.ov-weight-val { font-family:Consolas,monospace; font-size:11px; font-weight:700; }',
      '.ov-weight-val-np { color:var(--amber); font-weight:600; font-size:9px; }',
      '.ov-weight-spark { display:block; width:100%; height:34px; margin-top:2px; }',
      '.ov-weight-ins { color:var(--muted); font-size:9px; font-family:Consolas,monospace; padding-top:2px; }',
      /* long titles/labels wrap instead of underlapping the top-right flag */
      '.ov-stage-title, .ov-row-plain, .ov-ai-ro-label { overflow-wrap:anywhere; }',
      /* phone: stack stages and AI/checklist full-width so nothing collides */
      '@media (max-width: 768px) {',
      '  .ov-stage { min-width:100%; flex-basis:100%; }',
      '  .ov-ai-box { flex:1 1 100%; max-width:none; }',
      '  .ov-check { flex:1 1 100%; min-width:0; }',
      '  .ov-trend-box { flex:1 1 100%; }',
      '  .ov-arw { display:none; }',
      '}',
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
      '  <!-- Heading vector (item 7): a compass needle pivoting on the centre,',
      '       rotated by yaw. Points to the fixed top (nose) at yaw 0°. -->',
      '  <g id="ov-ai-heading" transform="rotate(0 100 100)" stroke="var(--text)"',
      '     stroke-width="2.5" stroke-linecap="round">',
      '    <line x1="100" y1="100" x2="100" y2="34"/>',
      '    <polygon points="100,22 92,36 108,36" fill="var(--text)" stroke="none"/>',
      '  </g>',
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

    /* ── Alarm history (session log, task 20260921-070956) ──────────────
     * Rows are filled by renderAlarmHistory(). The header note states the
     * retention honestly: the log lives only as long as this page. */
    var alarmHist = [
      '<div class="ov-hist" id="ov-hist">',
      '  <div class="ov-hist-head">',
      '    <span class="ov-hist-title">Alarm history (session)</span>',
      '    <span class="ov-hist-note" id="ov-hist-note">session log — lost on page reload, not persisted</span>',
      '    <button type="button" class="ov-hist-btn" id="ov-export-btn" title="Download the session alarm log as CSV">EXPORT CSV</button>',
      '  </div>',
      '  <div id="ov-hist-rows"></div>',
      '</div>',
    ].join('');

    /* ── Trend-on-demand + battery trend boxes ──────────────────────────
     * Both filled by renderTrend() / renderBatteryTrend(). */
    var trendRow = [
      '<div class="ov-trendrow">',
      '<div class="ov-trend-box" id="ov-trend-box">',
      '  <div style="display:flex;align-items:center;justify-content:space-between;gap:8px;">',
      '    <div class="ov-stage-title">Trend on demand</div>',
      '    <button type="button" class="ov-hist-btn" id="ov-trend-expand"',
      '      title="Expand the trend plot to fullscreen / shrink back">&#x26F6; Expand</button>',
      '  </div>',
      '  <div class="ov-stage-hint">click any value cell above to plot its session history — read-only</div>',
      '  <div id="ov-trend-body"></div>',
      '</div>',
      '<div class="ov-trend-box" id="ov-bat-box">',
      '  <div class="ov-stage-title">Battery trend</div>',
      '  <div class="ov-stage-hint">status.vbat session slope — least squares over received samples</div>',
      '  <div id="ov-bat-body"></div>',
      '</div>',
      '</div>',
    ].join('');

    /* ── Flight FSM view (task 20260921-103141) ────────────────────────
     * State pills for both enums, current one highlighted; meta lines
     * filled by renderFsm(). Read-only: the view sends nothing. */
    function statePills(prefix, list) {
      var pills = ['<div class="ov-fsm-chain">'];
      list.forEach(function (st, idx) {
        if (idx > 0) pills.push('<span class="ov-fsm-arrow">→</span>');
        pills.push('<span class="ov-fsm-state" id="' + prefix + '-state-' + st.val + '">' +
          st.name + '</span>');
      });
      pills.push('</div>');
      return pills.join('');
    }
    var fsmView = [
      '<div class="ov-fsm" id="ov-fsm">',
      '  <div class="ov-stage-title">Flight state machine',
      '    <span class="ov-fsm-np" id="ov-fsm-np"></span></div>',
      '  <div class="ov-stage-hint">states from firmware enums FlightState_t / FlightPhase_t (API/flight_fsm.h:6-22) — read-only, not inferred from other values</div>',
      '  <div class="ov-fsm-line"><span class="ov-fsm-kindlabel">State</span>',
      statePills('ov-fsm', FLIGHT_STATES), '</div>',
      '  <div class="ov-fsm-meta" id="ov-fsm-meta"></div>',
      '  <div class="ov-fsm-line" style="margin-top:5px"><span class="ov-fsm-kindlabel">Phase</span>',
      statePills('ov-fph', FLIGHT_PHASES), '</div>',
      '  <div class="ov-fsm-meta" id="ov-fph-meta"></div>',
      '</div>',
    ].join('');

    /* ── Adaptation (MRAC) view (task 20260921-103141) ───────────────── */
    var adaptAxesHtml = ADAPT_AXES.map(function (a) {
      var cells = ADAPT_WEIGHTS.filter(function (w) { return w.axis === a.axis; })
        .map(function (w) {
          return '<div class="ov-weight-cell">' +
            '<div class="ov-weight-title"><span>θ' + w.n + ' · ' + w.plain + '</span></div>' +
            '<div class="ov-weight-val ov-weight-val-np" id="ov-weight-val-' + w.axis + '-' + w.n + '">NOT PUBLISHED</div>' +
            '<div id="ov-weight-plot-' + w.axis + '-' + w.n + '"></div>' +
            '</div>';
        }).join('');
      return '<div class="ov-adapt-axis">' +
        '<div class="ov-adapt-head"><span class="ov-stage-title">' + a.label +
        ' adaptive weights</span>' +
        '<span class="ov-adapt-verdict ov-adapt-unknown" id="ov-adapt-verdict-' + a.axis + '">UNKNOWN</span></div>' +
        '<div class="ov-adapt-evidence" id="ov-adapt-ev-' + a.axis + '"></div>' +
        '<div class="ov-weight-grid">' + cells + '</div>' +
        '</div>';
    }).join('');
    var adaptView = [
      '<div class="ov-adapt" id="ov-adapt">',
      '  <div class="ov-stage-title">Adaptation (MRAC) — the experiment</div>',
      '  <div class="ov-stage-hint">adaptive weights mrac.<axis>.theta_0..5 (Frame B, 20 Hz — API/mrac.c mrac_state) · verdict is an inference; evidence shown beside it · read-only</div>',
      '  <div class="ov-adapt-axes">' + adaptAxesHtml + '</div>',
      '</div>',
    ].join('');

    return css + '<div class="ov-root">' + strip + banner + alarmHist +
      instruments + fsmView + adaptView + chain.join('') + trendRow + shadow + '</div>';
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

    /* Flight FSM + adaptation views (task 20260921-103141) */
    renderFsm(state, nowMs, ttlMs);
    renderAdaptation(state, nowMs, ttlMs);

    /* Mimic stages */
    STAGES.forEach(function (st) {
      var cs = classifyStage(st, state, nowMs, ttlMs);
      var box = q('ov-stage-' + st.id);
      if (box) box.className = 'ov-stage ov-stage-' + cs.cls;
      var flag = q('ov-flag-' + st.id);
      if (flag) { flag.textContent = cs.label; flag.className = 'ov-stage-flag ov-flag-' + cs.cls; }
      var sub = q('ov-sub-' + st.id);
      if (sub) {
        if (cs.cls === 'nodata') {
          // Ask which preset would publish this block's symbol, and render
          // "not published: in preset X" instead of a bare blank block.
          sub.setAttribute('data-np', '1');
          var hint = ensurePresetHint(st);
          sub.textContent = hint || cs.sub;
        } else {
          if (sub.hasAttribute && sub.hasAttribute('data-np')) sub.removeAttribute('data-np');
          sub.textContent = cs.sub;
        }
      }
      st.rows.forEach(function (r, ri) {
        var el = q('ov-val-' + st.id + '-' + ri);
        if (!el) return;
        var f = (cs.cls === 'nodata') ? null : findRowValue(state, r);
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
    updateAlarmLog(alarms, nowMs);
    alarms.sort(function (a, b) { return sevRank(b.sev) - sevRank(a.sev); });
    // Silenced episodes (operator display action) drop out of the banner's
    // worst-of selection — the nag is suppressed, never the record.
    var silenced = silencedAlarmIds();
    var audible = alarms.filter(function (a) { return !silenced[a.id]; });
    var worst = audible.length ? audible[0].sev : null;
    var banner = q('ov-banner');
    if (banner) {
      if (worst === 'red') {
        banner.className = 'ov-banner ov-banner-alarm';
        banner.textContent = '⚠ ALARM — ' + audible[0].text;
      } else if (worst === 'amber') {
        banner.className = 'ov-banner ov-banner-warn';
        banner.textContent = 'WARNING — ' + audible[0].text;
      } else if (alarms.length) {
        banner.className = 'ov-banner ov-banner-warn';
        banner.textContent = 'ALARMS SILENCED BY OPERATOR — ' + alarms.length +
          ' active, see alarm history';
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
                   (a.sev === 'red' ? '⚠ ' : '') + a.text +
                   (silenced[a.id] ? ' [SILENCED]' : '') + '</div>');
      });
      _recentAlarms.forEach(function (r) {
        if (r.open) return;
        items.push('<div class="ov-alarm-item ov-alarm-clear">RECENT (cleared ' +
                   fmtAge(nowMs - r.clearedAt) + ' ago): ' + r.text + '</div>');
      });
      listEl.innerHTML = items.join('');
    }

    /* Follow-on widgets (task 20260921-070956) */
    renderAlarmHistory(nowMs);
    renderTrend();
    renderBatteryTrend(state);
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
      var roll = dead ? null : findHistoryValue(state, 'c.roll');
      var pitch = dead ? null : findHistoryValue(state, 'c.pitch');
      if (roll && pitch && roll.val != null && pitch.val != null &&
          !isNaN(roll.val) && !isNaN(pitch.val)) {
        horizon.setAttribute('transform',
          'rotate(' + (-Number(roll.val)) + ' 100 100) translate(0 ' +
          (Number(pitch.val) * AI_PITCH_PPD).toFixed(2) + ')');
      }
    }
    // Heading vector (item 7): rotate the compass needle by yaw so the
    // heading visibly tracks the drone's turn, not just the numeric readout.
    var heading = q('ov-ai-heading');
    if (heading) {
      var yaw = dead ? null : findHistoryValue(state, 'c.yaw');
      if (yaw && yaw.val != null && !isNaN(yaw.val)) {
        heading.setAttribute('transform',
          'rotate(' + (-Number(yaw.val)).toFixed(2) + ' 100 100)');
      }
    }
    AI_ROWS.forEach(function (r, ri) {
      var el = q('ov-ai-ro-' + ri);
      if (!el) return;
      var f = dead ? null : findRowValue(state, r);
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

  /* ── Flight FSM render (task 20260921-103141) ────────────────────────── */

  function nameByVal(list, val) {
    for (var i = 0; i < list.length; i++) {
      if (list[i].val === val) return list[i].name;
    }
    return null;
  }

  /* Name when the enum is known, else the numeric value — same fallback
   * as the UNRECOGNIZED VALUE branch, so a transition never reads "null →". */
  function nameOrVal(list, val) {
    var nm = nameByVal(list, val);
    return (nm != null) ? nm : val;
  }

  function renderFsmKind(kind, key, list, prefix, metaId, nowMs, ttlMs) {
    var f = findValue(_lastState, key);
    trackFsmKind(kind, f, nowMs);
    var live = keyLiveState(f, nowMs, ttlMs);
    var tr = _fsmTrans[kind];
    var n = (f && f.val != null && !isNaN(f.val)) ? Number(f.val) : null;
    var recognized = (n != null && nameByVal(list, n) != null);

    list.forEach(function (st) {
      var el = q(prefix + '-state-' + st.val);
      if (!el) return;
      var isCur = recognized && n === st.val;
      var tone = (live.cls === 'ok') ? 'ok' : (live.cls === 'warn') ? 'warn' : 'nodata';
      el.className = 'ov-fsm-state' + (isCur ? ' ov-fsm-cur-' + tone : '');
    });

    var meta = q(metaId);
    if (!meta) return;
    if (live.cls === 'np') {
      meta.innerHTML = '<span class="ov-fsm-np">NOT PUBLISHED — ' + live.sub + '</span>' +
        '<span>key ' + key + ' absent from every stream</span>';
      return;
    }
    var bits = [];
    if (!recognized) {
      bits.push('<span class="ov-fsm-np">UNRECOGNIZED VALUE ' + n + ' — not in API/flight_fsm.h enum</span>');
    }
    bits.push('<span>' + live.label + (live.sub ? ' (' + live.sub + ')' : '') + '</span>');
    if (tr.prev !== null) {
      bits.push('<span>last transition: ' + nameOrVal(list, tr.prev) + ' → ' +
        nameOrVal(list, tr.last) + ' (observed ' + fmtClock(tr.atMs) + ')</span>');
    } else {
      bits.push('<span>no transition observed this session</span>');
    }
    var dwell = fsmDwell(tr, f, nowMs);
    if (dwell != null) {
      bits.push('dwell ' + fmtAge(dwell) + (tr.prev === null ? ' since first packet' : ''));
    }
    meta.innerHTML = bits.map(function (b) { return '<span>' + b + '</span>'; })
      .join('<span> · </span>');
  }

  function renderFsm(state, nowMs, ttlMs) {
    renderFsmKind('state', FSM_STATE_KEY, FLIGHT_STATES, 'ov-fsm', 'ov-fsm-meta', nowMs, ttlMs);
    renderFsmKind('phase', FSM_PHASE_KEY, FLIGHT_PHASES, 'ov-fph', 'ov-fph-meta', nowMs, ttlMs);
  }

  /* ── Adaptation (MRAC) render (task 20260921-103141) ─────────────────── */

  function renderAdaptation(state, nowMs, ttlMs) {
    ADAPT_AXES.forEach(function (a) {
      var r = computeAdaptVerdict(a.axis, nowMs);
      var vEl = q('ov-adapt-verdict-' + a.axis);
      if (vEl) {
        vEl.textContent = r.verdict.toUpperCase();
        vEl.className = 'ov-adapt-verdict ov-adapt-' + r.verdict;
      }
      var eEl = q('ov-adapt-ev-' + a.axis);
      if (eEl) {
        var basis = 'window ' + ADAPT_WINDOW + ' snapshots (' + r.nSnaps + ' used) · ' +
          r.samplesMin + '–' + r.samplesMax + ' samples/weight' +
          (r.spanMs != null ? ' · span ' + fmtAge(r.spanMs) : '');
        if (r.verdict === 'unknown') {
          eEl.textContent = 'UNKNOWN — ' + r.reason + ' · evidence so far: ' + basis;
        } else {
          eEl.textContent = 'evidence: ' + basis + ' · early Δ ' +
            r.stepEarly.toExponential(2) + ' → late Δ ' + r.stepLate.toExponential(2) +
            ' (' + (r.ratio * 100).toFixed(0) + '%) · travel ' + r.travel.toExponential(2);
        }
      }
    });

    ADAPT_WEIGHTS.forEach(function (w) {
      var valEl = q('ov-weight-val-' + w.axis + '-' + w.n);
      var f = findHistoryValue(state, w.key);
      if (valEl) {
        // Same four honesty states the FSM pills distinguish.
        var live = keyLiveState(f, nowMs, ttlMs);
        if (live.cls === 'np') {
          valEl.textContent = 'NOT PUBLISHED';
          valEl.className = 'ov-weight-val ov-weight-val-np';
        } else {
          var v = Number(f.val);
          valEl.textContent = (v >= 0 ? '+' : '') + v.toFixed(4) +
            (live.sub ? ' · ' + live.sub : '');
          // Reuse the FSM pill styling verbatim: warn = stale (amber),
          // nodata = frozen past TTL (muted).
          valEl.className = 'ov-weight-val' +
            (live.cls === 'warn' ? ' ov-fsm-cur-warn' :
             live.cls === 'nodata' ? ' ov-fsm-cur-nodata' : '');
        }
      }
      var plotEl = q('ov-weight-plot-' + w.axis + '-' + w.n);
      if (plotEl) {
        var d = buildSparklineSvg(w.key);
        if (d.insufficient) {
          plotEl.innerHTML = '<div class="ov-weight-ins">' + d.nValid + '/' + d.minNeeded +
            ' samples' + (d.gaps ? ' · ' + d.gaps + ' gap(s)' : '') + '</div>';
        } else {
          // Same SVG the trend-on-demand box draws, sized to the weight cell.
          plotEl.innerHTML = d.svg.replace('class="ov-spark"', 'class="ov-weight-spark"');
        }
      }
    });
  }

  /* ── Alarm history render (task 20260921-070956) ─────────────────────── */

  function renderAlarmHistory(nowMs) {
    var noteEl = q('ov-hist-note');
    if (noteEl) {
      noteEl.textContent = 'session log — lost on page reload, not persisted' +
        (_alarmLogDropped ? ' · ' + _alarmLogDropped + ' oldest episode(s) evicted by the ' + ALARM_LOG_MAX + '-episode bound' : '');
    }
    var rowsEl = q('ov-hist-rows');
    if (!rowsEl) return;
    var rows = [];
    // Newest episode first; each episode is its RAISED event plus, once it
    // clears, its CLEARED event — both timestamped.
    for (var i = _alarmLog.length - 1; i >= 0; i--) {
      var ep = _alarmLog[i];
      var cls = 'ov-hist-row' + (ep.ack ? ' ov-hist-ep-acked' : '') + (ep.silenced ? ' ov-hist-silenced' : '');
      var tags = (ep.ack ? '<span class="ov-hist-tag ov-hist-tag-ack">ACK</span>' : '') +
                 (ep.silenced ? '<span class="ov-hist-tag ov-hist-tag-sil">SILENCED</span>' : '');
      var stillOpen = ep.clearedAt == null;
      rows.push('<div class="' + cls + '">',
        '<span class="ov-hist-ts">' + fmtClock(ep.raisedAt) + '</span>',
        '<span class="ov-hist-ev ov-hist-ev-raised-' + ep.sev + '">RAISED</span>',
        '<span class="ov-hist-text">' + (ep.sev === 'red' ? '⚠ ' : '') + ep.text + ' ' + tags + '</span>',
        '<button type="button" class="ov-hist-btn" id="ov-ack-' + ep.ref + '" title="Acknowledge — display only, stays in the log">' + (ep.ack ? 'ACKED' : 'ACK') + '</button>',
        '<button type="button" class="ov-hist-btn" id="ov-sil-' + ep.ref + '" title="Silence the visual nag — display only, the record stays">' + (ep.silenced ? 'UNSILENCE' : 'SILENCE') + '</button>',
        '</div>');
      if (!stillOpen) {
        rows.push('<div class="' + cls + '">',
          '<span class="ov-hist-ts">' + fmtClock(ep.clearedAt) + '</span>',
          '<span class="ov-hist-ev ov-hist-ev-cleared">CLEARED</span>',
          '<span class="ov-hist-text">' + ep.textEnd + '</span>',
          '</div>');
      }
    }
    if (!rows.length) {
      rows.push('<div class="ov-hist-row"><span class="ov-hist-text" style="color:var(--muted)">' +
        'no alarms this session</span></div>');
    }
    rowsEl.innerHTML = rows.join('');
  }

  /* ── Trend-on-demand render ─────────────────────────────────────────── */

  function renderTrend() {
    var bodyEl = q('ov-trend-body');
    if (!bodyEl) return;
    if (_trendKey == null) {
      bodyEl.innerHTML = '<div class="ov-sub">no cell selected — click a value cell in the diagram above</div>';
      return;
    }
    var row = rowOfKey(_trendKey);
    var head = '<div class="ov-row"><span class="ov-row-plain">' +
      (row ? row.plain : _trendKey) + '</span><span class="ov-row-val" style="font-size:9px">' +
      _trendKey + (row ? ' · ' + row.unit : '') + '</span></div>';
    var d = buildSparklineSvg(_trendKey);
    if (d.insufficient) {
      // Explicit insufficient-history state: never a two-point flat line.
      bodyEl.innerHTML = head +
        '<div class="ov-bat-none" style="color:var(--muted)">INSUFFICIENT HISTORY — ' +
        d.nValid + ' sample(s) received, ' + d.minNeeded + ' needed for a trend</div>' +
        '<div class="ov-sub">keep telemetry flowing; the session buffer fills as samples arrive</div>';
      return;
    }
    bodyEl.innerHTML = head + d.svg +
      '<div class="ov-sub">' + d.nValid + ' samples · ' +
      (d.spanMs != null ? 'span ' + fmtAge(d.spanMs) : 'span unknown — no telemetry timestamps') +
      ' · ' + d.gaps + ' gap(s) shown as holes, never interpolated' +
      ' · range ' + String(d.vmin) + ' … ' + String(d.vmax) + '</div>';
  }

  /* Find the display row (plain label / unit) for a history key. */
  function rowOfKey(key) {
    if (key === 'status.vbat') return { plain: 'Battery voltage', unit: 'V', dec: 2 };
    for (var i = 0; i < STAGES.length; i++) {
      for (var j = 0; j < STAGES[i].rows.length; j++) {
        if (STAGES[i].rows[j].key === key) return STAGES[i].rows[j];
      }
    }
    for (var k = 0; k < SHADOW_ROWS.length; k++) {
      if (SHADOW_ROWS[k].key === key) return SHADOW_ROWS[k];
    }
    return null;
  }

  /* ── Battery trend render ───────────────────────────────────────────── */

  function renderBatteryTrend(state) {
    var bodyEl = q('ov-bat-body');
    if (!bodyEl) return;
    var t = computeBatteryTrend(state);
    if (!t.published) {
      bodyEl.innerHTML = '<div class="ov-bat-none" style="color:var(--amber)">NOT PUBLISHED</div>' +
        '<div class="ov-sub">status.vbat is not published by this build — no nominal voltage is substituted</div>';
      return;
    }
    var basis = null;
    if (t.n > 0 && t.spanMs != null) {
      basis = t.n + ' samples · ' + fmtAge(t.spanMs) + ' span';
    } else if (t.n > 0) {
      basis = t.n + ' samples';
    }
    if (t.slopeVPerMin == null) {
      // Not enough honestly-received history yet.
      bodyEl.innerHTML = '<div class="ov-bat-none" style="color:var(--muted)">INSUFFICIENT HISTORY</div>' +
        '<div class="ov-sub">' + t.n + ' sample(s) received — need ≥ ' + BAT_MIN_SAMPLES +
        ' spanning ≥ ' + fmtAge(BAT_MIN_SPAN_MS) + ' before any slope is shown</div>';
      return;
    }
    var slopeTxt = 'slope ' + (t.slopeVPerMin >= 0 ? '+' : '') +
      (t.slopeVPerMin * 1000).toFixed(1) + ' mV/min';
    if (t.atOrBelowFloor) {
      bodyEl.innerHTML = '<div class="ov-bat-tte" style="color:var(--red)">AT/BELOW ' +
        VBAT_RED_V.toFixed(1) + ' V NOW</div>' +
        '<div class="ov-sub">' + t.vLast.toFixed(2) + ' V — already at the firmware beep threshold; ' + slopeTxt + '</div>';
      return;
    }
    if (t.tteMin == null) {
      bodyEl.innerHTML = '<div class="ov-bat-none" style="color:var(--muted)">NO ESTIMATE — voltage not falling</div>' +
        '<div class="ov-sub">' + slopeTxt + ' (' + t.vLast.toFixed(2) + ' V last). Shallower than −' +
        BAT_FALLING_V_PER_MIN + ' V/min gives no time-to-empty, never a comforting number.</div>';
      return;
    }
    // item 8: an estimate with real received samples also draws a sparkline of
    // the actual status.vbat history, so the trend is a chart not just a number
    // (the slope is never shown bereft of the samples it came from).
    var sp = buildSparklineSvg('status.vbat');
    var chart = (!sp.insufficient && sp.svg) ?
      '<div class="ov-bat-chart">' + sp.svg +
        '<div class="ov-sub-svglabel">last ' + sp.nValid + ' received samples (least-squares basis)</div>' +
      '</div>' : '';
    bodyEl.innerHTML = chart +
      '<div class="ov-bat-tte">TIME TO ' + VBAT_RED_V.toFixed(1) +
      ' V ≈ ' + (t.tteMin >= 1 ? t.tteMin.toFixed(1) + ' min' : Math.round(t.tteMin * 60) + ' s') + '</div>' +
      '<div class="ov-sub">' + slopeTxt + ' · now ' + t.vLast.toFixed(2) + ' V · basis: ' + basis +
      ' (least squares over received samples)</div>';
  }

  /* ── Click delegation (display-local only) ────────────────────────────
   * Every branch here mutates only this panel's own session state and
   * re-renders. No api call, no command submission, no subscribe change, no
   * arming, no gating: trend clicks open a sparkline; ACK / SILENCE flip
   * flags on a log entry; EXPORT builds a CSV in the browser. */
  function onContainerClick(e) {
    var t = e && e.target;
    if (!t || !t.id) return;
    var m;
    if (_cellKeys[t.id] !== undefined) {
      _trendKey = (_trendKey === _cellKeys[t.id]) ? null : _cellKeys[t.id];
      if (_lastState != null) render(_lastState);
      return;
    }
    if ((m = t.id.match(/^ov-ack-(\d+)$/))) { alarmAction(Number(m[1]), 'ack'); return; }
    if ((m = t.id.match(/^ov-sil-(\d+)$/))) { alarmAction(Number(m[1]), 'silence'); return; }
    if (t.id === 'ov-export-btn') { downloadAlarmCsv(); return; }
    if (t.id === 'ov-trend-expand') {
      var box = q('ov-trend-box');
      if (!box) return;
      var expanded = !box.classList.contains('ov-expanded');
      box.classList.toggle('ov-expanded', expanded);
      t.innerHTML = expanded ? '&#x2926; Shrink' : '&#x26F6; Expand';
      t.title = expanded ? 'Shrink back to panel' : 'Expand to fullscreen';
    }
  }

  function onState(state) {
    _lastState = state;
    ingestHistory(state);   // one sample per /state snapshot, per key
    render(state);
  }

  /* ── Export (shell uses window.__registerPlugin__) ───────────────────── */

  window.__PLUGIN_INIT__ = function (api) {
    api.registerPanel('System Overview', function (container) {
      container.innerHTML = buildHTML();
      // One delegated click listener for the whole panel: trend-on-demand
      // cell clicks, alarm ACK / SILENCE and CSV EXPORT. Every branch is
      // display-local (see onContainerClick) — nothing is sent anywhere.
      if (!container.__ovClickWired) {
        container.addEventListener('click', onContainerClick);
        container.__ovClickWired = true;
      }
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
    // Session-scoped follow-on state: a fresh panel starts from empty
    // buffers and an empty log — this is the documented reload behaviour.
    _hist = {};
    _trendKey = null;
    _alarmLog = [];
    _openEpisodes = {};
    _alarmLogDropped = 0;
    _fsmTrans = { state: freshTrack(), phase: freshTrack() };
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
      /* Follow-ons (task 20260921-070956) — exercised by
       * overview_followons_harness.js checks 6-9. */
      ingestHistory: ingestHistory,
      sparklineData: sparklineData,
      buildSparklineSvg: buildSparklineSvg,
      computeBatteryTrend: computeBatteryTrend,
      renderBatteryTrend: renderBatteryTrend,
      renderAlarmHistory: renderAlarmHistory,
      renderTrend: renderTrend,
      alarmAction: alarmAction,
      onContainerClick: onContainerClick,
      exportAlarmLogCsv: exportAlarmLogCsv,
      downloadAlarmCsv: downloadAlarmCsv,
      SPARK_MIN_SAMPLES: SPARK_MIN_SAMPLES,
      BAT_MIN_SAMPLES: BAT_MIN_SAMPLES,
      BAT_MIN_SPAN_MS: BAT_MIN_SPAN_MS,
      BAT_FALLING_V_PER_MIN: BAT_FALLING_V_PER_MIN,
      ALARM_LOG_MAX: ALARM_LOG_MAX,
      /* Experiment observability (task 20260921-103141). */
      FLIGHT_STATES: FLIGHT_STATES,
      FLIGHT_PHASES: FLIGHT_PHASES,
      FSM_STATE_KEY: FSM_STATE_KEY,
      FSM_PHASE_KEY: FSM_PHASE_KEY,
      ADAPT_AXES: ADAPT_AXES,
      ADAPT_WEIGHTS: ADAPT_WEIGHTS,
      ADAPT_WINDOW: ADAPT_WINDOW,
      ADAPT_MIN_SNAPS: ADAPT_MIN_SNAPS,
      ADAPT_MIN_SPAN_MS: ADAPT_MIN_SPAN_MS,
      ADAPT_MIN_KEYS: ADAPT_MIN_KEYS,
      FROZEN_STEP: FROZEN_STEP,
      FROZEN_TRAVEL: FROZEN_TRAVEL,
      CONVERGE_RATIO: CONVERGE_RATIO,
      findHistoryValue: findHistoryValue,
      computeAdaptVerdict: computeAdaptVerdict,
      renderFsm: renderFsm,
      renderAdaptation: renderAdaptation,
      keyLiveState: keyLiveState,
    };
  }

})();
