/**
 * path-panel.js — Path Planning & Visualization Panel
 *
 * Provides 2D path visualization for UAV trajectory analysis:
 * - Canvas-based rendering (no external dependencies)
 * - Frame C position data from live state (c.earth_x, c.earth_y)
 * - Trajectory trail showing last 100 position points
 * - Home, current position, and target markers
 * - Waypoint management with editable X,Y pairs
 * - Zoom in/out controls
 * - Path metrics: total distance, max deviation
 *
 * Uses window.__registerPlugin__ pattern (IIFE, dark theme).
 */
(function () {
  'use strict';

  // ── State ───────────────────────────────────────────────────────────────
  var _trajectory = [];       // Array of {x, y, z, yaw, t} points (actual)
  var _desiredTrajectory = []; // Array of {x, y, z, t} points (desired from telemetry)
  var _waypoints = [];        // Array of {x, y, reached}
  var _homePos = null;
  var _targetPos = null;
  var _currentPos = null;
  var _zoom = 1.0;
  var _panX = 0;
  var _panY = 0;
  var _totalDistance = 0;
  var _rxFb = [];              // wall-clock ms of recent feedback samples (rate / speed)
  var _rxDes = [];             // wall-clock ms of recent desired samples
  var RX_KEEP = 64;
  var _maxDeviation = 0;
  var _plannedPath = [];     // Waypoint path for deviation calculation
  var _autoFitted = false;   // one-shot auto-fit of the flown path (item 6)
  var _scrubIndex = null;    // null = live / latest, integer = index in trajectory
  var _trackingMetrics = { count: 0, rms: null, max: null, rmsX: null, rmsY: null, rmsZ: null };

  // ── Modes (Plan / Fly / Review) and Fly-mode settings ──────────────────
  // Plan keeps the planning tools; Fly fills the panel with the 3D view for a
  // flight test; Review overlays saved REC logs. Spec:
  // docs/dashboard-platform/3d-panel-flight-ux-spec.md
  var MODE_KEY = 'pp_mode_v1';
  var FLY_KEY = 'pp_fly_v1';
  var MODES = ['plan', 'fly', 'review'];
  // 0.10 m is a starting value chosen in the design review, not a measured one.
  var FLY_DEFAULT = { thr: 0.10, metric: '3d', trail: 'whole' };
  var THR_MIN = 0.01;
  var THR_MAX = 1.0;
  var FADE_POINTS = 400;       // Fading trail: newest points at full brightness
  var MAX_EVENTS = 500;
  var _mode = loadMode();
  var _fly = loadFly();        // { thr (m), metric '3d'|'xy', trail 'whole'|'fading' }
  var _events = [];            // { kind, text, t (ms), pos {x,y,z}|null, source }
  var _runT0 = null;           // wall ms of the last path execute (run start)
  var _flyStatus = { mode: null, adapt: null, vbat: null, twc: null };
  var _lastPathEvt = { dir: null, t: 0 };
  var _notesSeq = 0;
  var _notesTimer = null;
  var _threeEventGroup = null;
  var _threePoint = null;
  var _eventTex = {};

  // ── 3D view state ──────────────────────────────────────────────────────
  var _threeLoaded = false;
  var _threeScene = null;
  var _threeCamera = null;
  var _threeRenderer = null;
  var _threeControls = null;
  var _threeLineActual = null;
  var _threeLineDesired = null;
  var _threeLinePlan = null;
  var _threeAxes = null;
  var _threeShadow = null;
  var _threeFollowDrone = false;
  var _MAX_3D_POINTS = 20000;
  var _THREE = null;
  var _threeRoomGroup = null;
  var _threeDrawPlane = null;  // translucent plane at the draw altitude (draw mode only)
  var _drawMode = false;       // mouse draws waypoints instead of orbiting / panning
  var _drawAlt = 0.5;          // draw plane offset along its normal (m): z for xy, y for xz, x for yz
  var _api = null;             // plugin api (submitCommand)
  var _exec = null;            // running Execute/Stop sequence: { steps, i, txid, t0, label }
  var _holdYaw = null;         // yaw setpoint that holds the current heading (Ctrler.yawPID.FB units)
  var _sdk = null;             // SDK authority from telemetry (1 = SDK)
  var EXEC_TIMEOUT_MS = 5000;
  var _plane = { kind: 'xy', tilt: 0, spacing: 0.02 };  // path plane, tilt (deg) about its first axis, point spacing (m)
  var _drawStroke = false;     // a stroke is in progress
  var _drawUndo = [];          // waypoint lists before each stroke / clear

  // ── Flight room (metres): centred on x/y origin, floor at z = 0 ────────
  var ROOM_KEY = 'pp_room_v1';
  var ROOM_DEFAULT = { w: 1.4, d: 1.4, h: 1.0 };
  var ROOM_TOL = 0.01;
  var _room = loadRoom();

  // ── World origin (display only) ─────────────────────────────────────────
  // Measured and desired x/y are shown relative to _origin, a point in the
  // firmware frame. Nothing is sent to the drone; waypoints, home and target
  // stay in the room frame.
  var ORIGIN_KEY = 'pp_origin_v1';
  var _origin = loadOrigin();
  var _desiredPath = [];
  var _trackingError = 0;


  var MAX_TRAIL = 20000;
  var MIN_ZOOM = 0.2;
  var MAX_ZOOM = 50.0;   // 20 px/m * 50 = 1000 px/m: room-scale paths fit
  var ZOOM_STEP = 0.2;

  // ── DOM refs ────────────────────────────────────────────────────────────
  var _canvas = null;
  var _ctx = null;
  var _elMetrics = null;
  var _elWaypointTable = null;
  var _elDemoIndicator = null;

  // -- 3D DOM refs --
  var _el3DCanvas = null;

  // ── Helpers ────────────────────────────────────────────────────────────
  function q(id) { return document.getElementById(id); }

  // localStorage can be missing or throw (private window, blocked site data).
  function storageGet(key) {
    try { return window.localStorage ? window.localStorage.getItem(key) : null; } catch (e) { return null; }
  }
  function storageSet(key, val) {
    try { if (window.localStorage) window.localStorage.setItem(key, val); } catch (e) { /* display prefs only */ }
  }

  function validRoom(r) {
    return !!r && r.w >= 0.2 && r.w <= 50 && r.d >= 0.2 && r.d <= 50 && r.h >= 0.2 && r.h <= 20;
  }

  function loadRoom() {
    var r = null;
    try { r = JSON.parse(storageGet(ROOM_KEY) || 'null'); } catch (e) { r = null; }
    if (r) r = { w: Number(r.w), d: Number(r.d), h: Number(r.h) };
    return validRoom(r) ? r : { w: ROOM_DEFAULT.w, d: ROOM_DEFAULT.d, h: ROOM_DEFAULT.h };
  }

  function validOrigin(o) {
    return !!o && typeof o.x === 'number' && typeof o.y === 'number' && isFinite(o.x) && isFinite(o.y);
  }

  function loadOrigin() {
    var o = null;
    try { o = JSON.parse(storageGet(ORIGIN_KEY) || 'null'); } catch (e) { o = null; }
    return validOrigin(o) ? { x: o.x, y: o.y } : { x: 0, y: 0 };
  }

  // Move every point of arr by -dx, -dy in place (re-expressing it after the
  // origin moved by dx, dy).
  function shiftPoints(arr, dx, dy) {
    var i;
    for (i = 0; i < arr.length; i++) {
      arr[i].x -= dx;
      arr[i].y -= dy;
    }
  }

  function roomBounds() {
    return { x0: -_room.w / 2, x1: _room.w / 2, y0: -_room.d / 2, y1: _room.d / 2, z0: 0, z1: _room.h };
  }

  function isOutOfRoom(p) {
    var b = roomBounds();
    var z = p.z || 0;
    return p.x < b.x0 - ROOM_TOL || p.x > b.x1 + ROOM_TOL ||
      p.y < b.y0 - ROOM_TOL || p.y > b.y1 + ROOM_TOL ||
      z < b.z0 - ROOM_TOL || z > b.z1 + ROOM_TOL;
  }

  function countOutOfRoom(points) {
    var n = 0;
    for (var i = 0; i < points.length; i++) if (isOutOfRoom(points[i])) n++;
    return n;
  }

  // Camera pose (three.js axes: y up, z = world y) framing a sphere of radius
  // rad around target t.
  function viewPose(preset, t, rad, aspect) {
    var dist = 2.3 * rad;   // fov 60: rad / sin(30 deg) = 2 rad, plus margin
    var p;
    if (aspect > 0 && aspect < 1) dist /= aspect;   // narrow canvas: the horizontal fov is the limit
    if (preset === 'top') p = { x: t.x, y: t.y + dist, z: t.z + 0.001 };
    else if (preset === 'side') p = { x: t.x + dist, y: t.y, z: t.z };
    else if (preset === 'front') p = { x: t.x, y: t.y, z: t.z + dist };
    else {
      var k = dist / Math.sqrt(1 + 0.5625 + 1);
      p = { x: t.x + k, y: t.y + 0.75 * k, z: t.z + k };
    }
    return { pos: p, target: t };
  }

  function roomViewPose(preset, aspect) {
    var rad = 0.5 * Math.sqrt(_room.w * _room.w + _room.d * _room.d + _room.h * _room.h);
    return viewPose(preset, { x: 0, y: _room.h / 2, z: 0 }, rad, aspect);
  }

  // Iso pose framing the bounding box of points clamped to the room (min radius
  // 0.25 m), so a sensor outlier cannot zoom the view out; room pose when empty.
  function pathViewPose(points) {
    var i, pt, z, x0, x1, y0, y1, z0, z1, dx, dy, dz;
    if (!points || points.length === 0) return roomViewPose('iso');
    x0 = y0 = z0 = Infinity; x1 = y1 = z1 = -Infinity;
    for (i = 0; i < points.length; i++) {
      pt = clampToRoom({ x: points[i].x, y: points[i].y, z: points[i].z || 0 }, _room);
      z = pt.z;
      if (pt.x < x0) x0 = pt.x; if (pt.x > x1) x1 = pt.x;
      if (pt.y < y0) y0 = pt.y; if (pt.y > y1) y1 = pt.y;
      if (z < z0) z0 = z; if (z > z1) z1 = z;
    }
    dx = x1 - x0; dy = y1 - y0; dz = z1 - z0;
    return viewPose('iso', { x: (x0 + x1) / 2, y: (z0 + z1) / 2, z: (y0 + y1) / 2 },
                    Math.max(0.25, 0.5 * Math.sqrt(dx * dx + dy * dy + dz * dz)));
  }

  // Trail segment colour: blue inside the room, red when either end is outside.
  // Older segments fade to 35 % brightness so the newest part of the trail stands out.
  function actualSegColor(points, i, out) {
    var f = points.length > 2 ? 0.35 + 0.65 * i / (points.length - 2) : 1;
    if (isOutOfRoom(points[i]) || isOutOfRoom(points[i + 1])) {
      out[0] = 1.0 * f; out[1] = 0.25 * f; out[2] = 0.25 * f;
    } else {
      out[0] = 0.29 * f; out[1] = 0.62 * f; out[2] = 1.0 * f;
    }
  }

  function fmtNum(v, dec) {
    if (v == null) return '—';
    dec = dec === undefined ? 2 : dec;
    return parseFloat(v).toFixed(dec);
  }

  function distance(p1, p2) {
    return Math.sqrt(Math.pow(p2.x - p1.x, 2) + Math.pow(p2.y - p1.y, 2));
  }

  function lerp(a, b, t) {
    return a + (b - a) * t;
  }

  // ── Frame C position keys — every spelling the adapter publishes ─────────
  // Bug 5 binding fix: stream values appear under BOTH the adapter's spec
  // alias (`c.earth_x`, `c.earth_y`, `c.altitude_cm`) and the raw, slot /
  // namespace-prefixed DWARF spelling (`slot1.ano_of.earth_x`,
  // `slot1.ano_of.of_alt_cm`, `slot3.c.earth_x`, …). We match a bare spec
  // name against both the stored key and any key with a ``slot#.`` prefix
  // stripped (the same `SLOT_PREFIX_RE` pattern used by the time-series fix
  // for Bug 4). Position is present even when its value is 0.0 (origin); we
  // only report "no position data" when the keys are genuinely absent.
  var SLOT_PREFIX_RE = /^slot\d+\./;
  var POS_X_KEYS = ['c.earth_x', 'earth_x', 'ano_of.earth_x', 'pos_x'];
  var POS_Y_KEYS = ['c.earth_y', 'earth_y', 'ano_of.earth_y', 'pos_y'];
  // Altitude is published in cm (`*_alt_cm`) and metres (`c.altitude`).
  var POS_Z_KEYS = ['c.altitude_cm', 'c.altitude', 'altitude',
                    'ano_of.of_alt_cm', 'ano_of.of_alt_cm_m', 'of_alt_cm', 'pos_z'];

  var POS_YAW_KEYS = ['c.yaw', 'yaw', 'imu_data.yaw', 'status.yaw_deg'];

  // Return the first defined numeric value from valueMap matching any of
  // `names`, bare or slot-prefixed. Returns undefined when absent.
  function lookupValue(valueMap, names) {
    if (!valueMap) return undefined;
    for (var i = 0; i < names.length; i++) {
      if (valueMap[names[i]] !== undefined) return valueMap[names[i]];
    }
    for (var k in valueMap) {
      var bare = k.replace(SLOT_PREFIX_RE, '');
      for (var j = 0; j < names.length; j++) {
        if (bare === names[j]) return valueMap[k];
      }
    }
    return undefined;
  }

  // ── Parse Frame C position from state ───────────────────────────────────
  // Returns {x, y, z} with x/y/z in metres, or null when position is
  // genuinely absent. Altitude is published in centimetres under the
  // `*_alt_cm` names and in metres under `c.altitude`; we normalise to
  // metres. Absent altitude stays null — never a fabricated 0.
  function metreAltitude(valueMap, zNum) {
    if (zNum === undefined || zNum === null || isNaN(Number(zNum))) return null;
    // Only scale when the cm-typed name matched (i.e. no metres-typed name
    // was present earlier in the key list).
    if (valueMap['c.altitude'] !== undefined || valueMap['altitude'] !== undefined) {
      return parseFloat(zNum);
    }
    for (var k in valueMap) {
      if (/alt_cm/.test(k.replace(SLOT_PREFIX_RE, ''))) {
        return parseFloat(zNum) / 100;
      }
    }
    return parseFloat(zNum);
  }

  // Height in metres. The firmware altitude PID feedback (Ctrler.Z_posPID.FB,
  // metres) is preferred: the Z setpoint is in the same unit, so the Fly-mode
  // 3D error compares like with like. Otherwise the optical-flow altitude.
  var POS_Z_M_KEYS = ['Ctrler.Z_posPID.FB', 'Z_posPID.FB', 'pid.z_pos.FB'];
  function positionZ(valueMap) {
    var zm = lookupValue(valueMap, POS_Z_M_KEYS);
    if (zm !== undefined && zm !== null && !isNaN(Number(zm))) return parseFloat(zm);
    return metreAltitude(valueMap, lookupValue(valueMap, POS_Z_KEYS));
  }

  function extractPosition(state) {
    if (!state) return null;

    // 1. /state stream snapshot: slot -> { values: {…}, _key_ts: {…} }.
    if (state.streams) {
      var k = Object.keys(state.streams);
      for (var i = 0; i < k.length; i++) {
        var vals = state.streams[k[i]] && state.streams[k[i]].values;
        if (!vals) continue;
        var x = lookupValue(vals, POS_X_KEYS);
        var y = lookupValue(vals, POS_Y_KEYS);
        var yaw = lookupValue(vals, POS_YAW_KEYS);
        if (x !== undefined && y !== undefined &&
            !isNaN(Number(x)) && !isNaN(Number(y))) {
          return { x: parseFloat(x) / 100, y: parseFloat(y) / 100,
                   z: positionZ(vals),
                   yaw: yaw !== undefined ? parseFloat(yaw) : 0 };
        }
      }
    }

    // 2. Flat fallbacks (older adapter shapes: state.values or state itself).
    var flat = state.values || state;
    var fx = lookupValue(flat, POS_X_KEYS);
    var fy = lookupValue(flat, POS_Y_KEYS);
    var fyaw = lookupValue(flat, POS_YAW_KEYS);
    if (fx !== undefined && fy !== undefined &&
        !isNaN(Number(fx)) && !isNaN(Number(fy))) {
      return { x: parseFloat(fx) / 100, y: parseFloat(fy) / 100,
               z: positionZ(flat),
               yaw: fyaw !== undefined ? parseFloat(fyaw) : 0 };
    }

    return null;
  }

  // ── Desired position keys (firmware setpoints streamed in flight presets) ──
  var DES_X_KEYS = ['pid.locx.Des', 'Ctrler.locxPID.Des', 'locxPID.Des', 'locx.Des',
                    'c.desired_x', 'desired_x_cm', 'desired_x', 'des_x', 'desired_x_m'];
  var DES_Y_KEYS = ['pid.locy.Des', 'Ctrler.locyPID.Des', 'locyPID.Des', 'locy.Des',
                    'c.desired_y', 'desired_y_cm', 'desired_y', 'des_y', 'desired_y_m'];
  var DES_Z_KEYS = ['pid.z_pos.Des', 'Ctrler.Z_posPID.Des', 'Z_posPID.Des', 'z_pos.Des',
                    'c.desired_z', 'desired_z', 'des_z', 'pid.z.Des', 'c.desired_alt_cm'];

  function parseDesiredValues(valueMap) {
    if (!valueMap) return null;
    var rawX = lookupValue(valueMap, DES_X_KEYS);
    var rawY = lookupValue(valueMap, DES_Y_KEYS);
    if (rawX === undefined || rawY === undefined || isNaN(Number(rawX)) || isNaN(Number(rawY))) {
      return null;
    }
    var x = parseFloat(rawX);
    var y = parseFloat(rawY);
    var xKey = '';
    for (var k in valueMap) {
      var bare = k.replace(SLOT_PREFIX_RE, '');
      if (DES_X_KEYS.indexOf(bare) !== -1) { xKey = bare; break; }
    }
    if (!/_m$/.test(xKey)) {
      x = x / 100;
      y = y / 100;
    }

    var z = 0;
    var hasZ = false;   // false: no z setpoint published, so z must not enter an error
    var rawZ = lookupValue(valueMap, DES_Z_KEYS);
    if (rawZ !== undefined && rawZ !== null && !isNaN(Number(rawZ))) {
      hasZ = true;
      var zKey = '';
      for (var kz in valueMap) {
        var bareZ = kz.replace(SLOT_PREFIX_RE, '');
        if (DES_Z_KEYS.indexOf(bareZ) !== -1) { zKey = bareZ; break; }
      }
      if (/alt_cm|_cm$/.test(zKey)) {
        z = parseFloat(rawZ) / 100;
      } else {
        z = parseFloat(rawZ);
      }
    }
    return { x: x, y: y, z: z, hasZ: hasZ };
  }

  function extractDesiredPosition(state) {
    if (!state) return null;
    if (state.streams) {
      var k = Object.keys(state.streams);
      for (var i = 0; i < k.length; i++) {
        var vals = state.streams[k[i]] && state.streams[k[i]].values;
        var res = parseDesiredValues(vals);
        if (res) return res;
      }
    }
    var flat = state.values || state;
    return parseDesiredValues(flat);
  }

  // ── Update trajectory ───────────────────────────────────────────────────
  var _lastTs = 0;
  function nextTimestamp(t) {
    if (t !== undefined && t !== null && !isNaN(Number(t))) {
      var n = Number(t);
      if (n > _lastTs) _lastTs = n;
      return n;
    }
    var now = Date.now();
    if (now <= _lastTs) now = _lastTs + 1;
    _lastTs = now;
    return now;
  }

  function addPoint(pos, optTime) {
    if (!pos || pos.x == null || pos.y == null) return;
    var t = pos.t !== undefined ? pos.t : (optTime !== undefined ? optTime : nextTimestamp());
    _trajectory.push({ x: pos.x - _origin.x, y: pos.y - _origin.y, z: pos.z, yaw: pos.yaw, t: t,
                       e3: pos.e3 == null ? null : pos.e3, exy: pos.exy == null ? null : pos.exy });
    _rxFb.push(Date.now());
    if (_rxFb.length > RX_KEEP) _rxFb.shift();
    if (_trajectory.length > MAX_TRAIL) {
      _trajectory.shift();
    }

    // Update current position
    _currentPos = { x: pos.x - _origin.x, y: pos.y - _origin.y, z: pos.z == null ? null : pos.z, yaw: pos.yaw || 0 };

    // Recalculate metrics
    updateMetrics();
  }

  function addDesiredPoint(des, optTime) {
    if (!des || des.x == null || des.y == null) return;
    var t = des.t !== undefined ? des.t : (optTime !== undefined ? optTime : nextTimestamp());
    _desiredTrajectory.push({ x: des.x - _origin.x, y: des.y - _origin.y, z: des.z == null ? 0 : des.z, t: t });
    _rxDes.push(Date.now());
    if (_rxDes.length > RX_KEEP) _rxDes.shift();
    if (_desiredTrajectory.length > MAX_TRAIL) {
      _desiredTrajectory.shift();
    }
    _desiredPath = _desiredTrajectory;
    updateMetrics();
  }

  function getActiveTrajectory() {
    if (_scrubIndex != null && _scrubIndex >= 0 && _scrubIndex < _trajectory.length) {
      return _trajectory.slice(0, _scrubIndex + 1);
    }
    return _trajectory;
  }

  function getActiveDesiredTrajectory() {
    if (_scrubIndex != null && _scrubIndex >= 0) {
      var activeActual = getActiveTrajectory();
      if (activeActual.length === 0) return [];
      var lastT = activeActual[activeActual.length - 1].t;
      if (lastT !== undefined && _desiredTrajectory.length > 0 && _desiredTrajectory[0].t !== undefined) {
        var filtered = [];
        for (var i = 0; i < _desiredTrajectory.length; i++) {
          if (_desiredTrajectory[i].t <= lastT) filtered.push(_desiredTrajectory[i]);
          else break;
        }
        return filtered.length > 0 ? filtered : _desiredTrajectory.slice(0, 1);
      }
      return _desiredTrajectory.slice(0, Math.min(_scrubIndex + 1, _desiredTrajectory.length));
    }
    return _desiredTrajectory;
  }

  function computeTrackingMetrics(actualList, desiredList) {
    if (!actualList || !desiredList || actualList.length === 0 || desiredList.length === 0) {
      return { count: 0, rms: null, max: null, rmsX: null, rmsY: null, rmsZ: null };
    }

    var pairs = [];
    var j = 0;
    for (var i = 0; i < actualList.length; i++) {
      var a = actualList[i];
      if (a.t !== undefined && desiredList[0].t !== undefined && desiredList.length > 1) {
        while (j < desiredList.length - 1 &&
               Math.abs(desiredList[j + 1].t - a.t) < Math.abs(desiredList[j].t - a.t)) {
          j++;
        }
        pairs.push({ actual: a, desired: desiredList[j] });
        if (j < desiredList.length - 1 && desiredList[j].t === a.t) {
          j++;
        }
      } else {
        if (i < desiredList.length) {
          pairs.push({ actual: a, desired: desiredList[i] });
        }
      }
    }

    if (pairs.length === 0) {
      return { count: 0, rms: null, max: null, rmsX: null, rmsY: null, rmsZ: null };
    }

    var sumSqX = 0, sumSqY = 0, sumSqZ = 0, sumSq3D = 0;
    var maxErr = 0;
    for (var k = 0; k < pairs.length; k++) {
      var p = pairs[k];
      var dx = p.actual.x - p.desired.x;
      var dy = p.actual.y - p.desired.y;
      var dz = (p.actual.z != null ? p.actual.z : 0) - (p.desired.z != null ? p.desired.z : 0);
      var errSq = dx * dx + dy * dy + dz * dz;
      var err = Math.sqrt(errSq);
      sumSqX += dx * dx;
      sumSqY += dy * dy;
      sumSqZ += dz * dz;
      sumSq3D += errSq;
      if (err > maxErr) maxErr = err;
    }

    var N = pairs.length;
    return {
      count: N,
      rms: Math.sqrt(sumSq3D / N),
      max: maxErr,
      rmsX: Math.sqrt(sumSqX / N),
      rmsY: Math.sqrt(sumSqY / N),
      rmsZ: Math.sqrt(sumSqZ / N)
    };
  }

  function updateMetrics() {
    var activeActual = getActiveTrajectory();
    var activeDesired = getActiveDesiredTrajectory();

    // Total distance along trajectory
    _totalDistance = 0;
    for (var i = 1; i < activeActual.length; i++) {
      _totalDistance += distance(activeActual[i - 1], activeActual[i]);
    }

    // Max deviation from planned path (if waypoints exist)
    _maxDeviation = 0;
    if (_plannedPath.length > 1) {
      activeActual.forEach(function (pt) {
        var minDist = Infinity;
        // Find distance to nearest segment of planned path
        for (var i = 0; i < _plannedPath.length - 1; i++) {
          var d = pointToSegmentDist(pt, _plannedPath[i], _plannedPath[i + 1]);
          if (d < minDist) minDist = d;
        }
        if (minDist > _maxDeviation) _maxDeviation = minDist;
      });
    }

    // Tracking metrics between time-aligned actual and desired samples
    _trackingMetrics = computeTrackingMetrics(activeActual, activeDesired);
    _trackingError = _trackingMetrics.rms != null ? _trackingMetrics.rms : 0;
  }

  function pointToSegmentDist(p, a, b) {
    var dx = b.x - a.x;
    var dy = b.y - a.y;
    var len2 = dx * dx + dy * dy;
    if (len2 === 0) return distance(p, a);
    var t = Math.max(0, Math.min(1, ((p.x - a.x) * dx + (p.y - a.y) * dy) / len2));
    return distance(p, { x: a.x + t * dx, y: a.y + t * dy });
  }

  // ── Rendering ────────────────────────────────────────────────────────────
  function render() {
    if (!_ctx || !_canvas) return;

    var W = _canvas.width;
    var H = _canvas.height;

    // Clear
    _ctx.fillStyle = '#0a0a1a';
    _ctx.fillRect(0, 0, W, H);

    if (!_currentPos && !_waypoints.length) {
      // With no data the panel draws nothing and says it has no position data
      _ctx.fillStyle = 'rgba(255, 255, 255, 0.4)';
      _ctx.font = '14px Consolas, monospace';
      _ctx.textAlign = 'center';
      _ctx.textBaseline = 'middle';
      _ctx.fillText('No position data', W / 2, H / 2);
      if (_elDemoIndicator) {
        _elDemoIndicator.textContent = 'No position data';
        _elDemoIndicator.style.display = 'block';
      }
      return;
    }

    if (_elDemoIndicator) {
      _elDemoIndicator.style.display = 'none';
    }

    // Frame the plot to the flown path (item 6): once a real spread appears,
    // zoom/pan so the trail fills the canvas instead of collapsing to a blue
    // dot in the middle against a bare black/white grid. Fires once per data
    // set (>=3 points with an extent); a manual zoom / reset re-arms it.
    autoFitView();

    // Origin axes
    drawAxes2D(W, H);

    // Room walls (x/y footprint)
    drawRoom2D(W, H);

    // Trajectory trail
    drawTrail();

    // Desired trace from telemetry
    drawDesiredTrail();

    // Planned path (waypoints)
    drawPlannedPath();

    // Waypoints
    drawWaypoints();

    // Home position
    if (_homePos) drawMarker(_homePos.x, _homePos.y, 'H', '#4ecca3', 12);

    // Target position
    if (_targetPos) drawMarker(_targetPos.x, _targetPos.y, 'T', '#e94560', 12);

    // Axes labels
    _ctx.fillStyle = 'rgba(255,255,255,0.3)';
    _ctx.font = '10px Consolas, monospace';
    _ctx.fillText('X', W - 15, H - 8);
    _ctx.fillText('Y', 8, 15);
  }

  function drawAxes2D(W, H) {
    // Origin crosshairs
    var originX = W / 2 + _panX;
    var originY = H / 2 + _panY;

    _ctx.strokeStyle = 'rgba(255,255,255,0.2)';
    _ctx.lineWidth = 1;
    _ctx.setLineDash([4, 4]);

    _ctx.beginPath();
    _ctx.moveTo(originX, 0);
    _ctx.lineTo(originX, H);
    _ctx.stroke();

    _ctx.beginPath();
    _ctx.moveTo(0, originY);
    _ctx.lineTo(W, originY);
    _ctx.stroke();

    _ctx.setLineDash([]);
  }

  function screenToWorld(sx, sy, W, H) {
    return {
      x: (sx - W / 2 - _panX) / (_zoom * 20),
      y: (H / 2 - _panY - sy) / (_zoom * 20)
    };
  }

  function drawRoom2D(W, H) {
    var b = roomBounds();
    var a = worldToScreen(b.x0, b.y1, W, H);
    var c = worldToScreen(b.x1, b.y0, W, H);
    _ctx.strokeStyle = 'rgba(120, 140, 255, 0.55)';
    _ctx.lineWidth = 1.5;
    _ctx.beginPath();
    _ctx.moveTo(a.x, a.y);
    _ctx.lineTo(c.x, a.y);
    _ctx.lineTo(c.x, c.y);
    _ctx.lineTo(a.x, c.y);
    _ctx.lineTo(a.x, a.y);
    _ctx.stroke();
  }

  // Zoom that frames the room footprint in a W x H canvas (20 px/m at zoom 1).
  function roomFitZoom(W, H) {
    var z = Math.min(W, H) * 0.85 / (Math.max(_room.w, _room.d) * 20);
    return Math.min(Math.max(z, MIN_ZOOM), MAX_ZOOM);
  }

  function fitRoom2D() {
    if (!_canvas) return;
    _zoom = roomFitZoom(_canvas.width, _canvas.height);
    _panX = 0;
    _panY = 0;
    _autoFitted = true;   // keep the room framing; reset view re-arms auto-fit
    render();
  }

  function worldToScreen(wx, wy, W, H) {
    return {
      x: W / 2 + _panX + wx * _zoom * 20,
      y: H / 2 - _panY - wy * _zoom * 20
    };
  }

  function drawTrail() {
    var pts = getActiveTrajectory();
    if (pts.length < 2) return;

    var W = _canvas.width;
    var H = _canvas.height;

    _ctx.beginPath();
    _ctx.strokeStyle = 'rgba(74, 158, 255, 0.6)';
    _ctx.lineWidth = 2;

    var first = worldToScreen(pts[0].x, pts[0].y, W, H);
    _ctx.moveTo(first.x, first.y);

    for (var i = 1; i < pts.length; i++) {
      var pt = worldToScreen(pts[i].x, pts[i].y, W, H);
      _ctx.lineTo(pt.x, pt.y);
    }
    _ctx.stroke();

    // Gradient fade for older points (only last 100 points to avoid lag)
    var startIdx = Math.max(1, pts.length - 100);
    var tailLen = pts.length - startIdx;
    for (var i = startIdx; i < pts.length; i++) {
      var alpha = ((i - startIdx) / tailLen) * 0.5;
      var pt = worldToScreen(pts[i].x, pts[i].y, W, H);
      _ctx.beginPath();
      _ctx.fillStyle = 'rgba(74, 158, 255, ' + alpha + ')';
      _ctx.arc(pt.x, pt.y, 2, 0, Math.PI * 2);
      _ctx.fill();
    }
  }

  function drawDesiredTrail() {
    var pts = getActiveDesiredTrajectory();
    if (pts.length < 2) return;

    var W = _canvas.width;
    var H = _canvas.height;

    _ctx.beginPath();
    _ctx.strokeStyle = 'rgba(255, 170, 0, 0.85)';
    _ctx.lineWidth = 1.5;
    _ctx.setLineDash([4, 4]);

    var first = worldToScreen(pts[0].x, pts[0].y, W, H);
    _ctx.moveTo(first.x, first.y);

    for (var i = 1; i < pts.length; i++) {
      var pt = worldToScreen(pts[i].x, pts[i].y, W, H);
      _ctx.lineTo(pt.x, pt.y);
    }
    _ctx.stroke();
    _ctx.setLineDash([]);
  }


  function drawPlannedPath() {
    if (_plannedPath.length < 2) return;

    var W = _canvas.width;
    var H = _canvas.height;

    _ctx.beginPath();
    _ctx.strokeStyle = 'rgba(245, 166, 35, 0.4)';
    _ctx.lineWidth = 1;
    _ctx.setLineDash([6, 4]);

    var first = worldToScreen(_plannedPath[0].x, _plannedPath[0].y, W, H);
    _ctx.moveTo(first.x, first.y);

    for (var i = 1; i < _plannedPath.length; i++) {
      var pt = worldToScreen(_plannedPath[i].x, _plannedPath[i].y, W, H);
      _ctx.lineTo(pt.x, pt.y);
    }
    _ctx.stroke();
    _ctx.setLineDash([]);
  }

  function drawWaypoints() {
    _waypoints.forEach(function (wp, i) {
      var color = wp.reached ? 'rgba(78, 204, 163, 0.6)' : 'rgba(245, 166, 35, 0.8)';
      drawMarker(wp.x, wp.y, (i + 1).toString(), color, 10);
    });
  }

  /* Fit the world-space view so the flown path spans the canvas (item 6).
   * Runs once per data set — a manual zoom / reset re-arms it. Only acts
   * when there are >=3 points with a non-trivial extent, so a stationary
   * drone (or the harness's 1-2 point feeds) never jumps the view. */
  function autoFitView() {
    if (_autoFitted || _trajectory.length < 3) return;
    var minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
    for (var i = 0; i < _trajectory.length; i++) {
      var pt = _trajectory[i];
      if (pt.x < minX) minX = pt.x;
      if (pt.x > maxX) maxX = pt.x;
      if (pt.y < minY) minY = pt.y;
      if (pt.y > maxY) maxY = pt.y;
    }
    var spanX = maxX - minX;
    var spanY = maxY - minY;
    if (spanX < 0.5 && spanY < 0.5) return;   // no real spread yet (stationary)
    var W = _canvas ? _canvas.width : 800;
    var H = _canvas ? _canvas.height : 400;
    var pad = 40;
    // Screen scale is `world * _zoom * 20` px per unit (see worldToScreen).
    var sX = (W - pad) / (spanX * 20);
    var sY = (H - pad) / (spanY * 20);
    _zoom = Math.min(Math.max(Math.min(sX, sY), MIN_ZOOM), MAX_ZOOM);
    var midX = (minX + maxX) / 2;
    var midY = (minY + maxY) / 2;
    // worldToScreen:  x' = W/2 + _panX + wx*_zoom*20 ; y' = H/2 - _panY - wy*_zoom*20
    _panX = -(midX * _zoom * 20);
    _panY =  (midY * _zoom * 20);
    _autoFitted = true;
  }

  function drawMarker(wx, wy, label, color, radius) {
    var W = _canvas.width;
    var H = _canvas.height;
    var pt = worldToScreen(wx, wy, W, H);

    // Outer ring
    _ctx.beginPath();
    _ctx.arc(pt.x, pt.y, radius + 3, 0, Math.PI * 2);
    _ctx.strokeStyle = color;
    _ctx.lineWidth = 2;
    _ctx.stroke();

    // Inner dot
    _ctx.beginPath();
    _ctx.arc(pt.x, pt.y, radius, 0, Math.PI * 2);
    _ctx.fillStyle = color;
    _ctx.fill();

    // Label
    if (label) {
      _ctx.fillStyle = '#0a0a1a';
      _ctx.font = 'bold ' + (radius - 2) + 'px sans-serif';
      _ctx.textAlign = 'center';
      _ctx.textBaseline = 'middle';
      _ctx.fillText(label, pt.x, pt.y);
    }
  }

  function rxRate(rx, now) {
    if (rx.length < 3 || now - rx[rx.length - 1] > 2000) return null;
    return (rx.length - 1) * 1000 / Math.max(1, rx[rx.length - 1] - rx[0]);
  }

  function path3DLength(pts) {
    var L = 0, i;
    for (i = 1; i < pts.length; i++) {
      L += Math.hypot(pts[i].x - pts[i - 1].x, pts[i].y - pts[i - 1].y, (pts[i].z || 0) - (pts[i - 1].z || 0));
    }
    return L;
  }

  function fmtXYZ(p) {
    return p ? fmtNum(p.x, 2) + ', ' + fmtNum(p.y, 2) + ', ' + (p.z == null ? '—' : fmtNum(p.z, 2)) : '—';
  }

  // ── Metrics display ─────────────────────────────────────────────────────
  function renderMetrics() {
    if (!_elMetrics) return;

    var activeActual = getActiveTrajectory();
    var distStr = _currentPos ? fmtNum(_totalDistance, 2) + ' m' : '—';
    var devStr = (_currentPos && _plannedPath.length > 1) ? fmtNum(_maxDeviation, 2) + ' m' : '—';
    var rmsStr = (_trackingMetrics && _trackingMetrics.rms != null) ? fmtNum(_trackingMetrics.rms, 2) + ' m' : '—';
    var maxErrStr = (_trackingMetrics && _trackingMetrics.max != null) ? fmtNum(_trackingMetrics.max, 2) + ' m' : '—';
    var rmsXStr = (_trackingMetrics && _trackingMetrics.rmsX != null) ? fmtNum(_trackingMetrics.rmsX, 2) + ' m' : '—';
    var rmsYStr = (_trackingMetrics && _trackingMetrics.rmsY != null) ? fmtNum(_trackingMetrics.rmsY, 2) + ' m' : '—';
    var rmsZStr = (_trackingMetrics && _trackingMetrics.rmsZ != null) ? fmtNum(_trackingMetrics.rmsZ, 2) + ' m' : '—';

    _elMetrics.innerHTML = [
      '<div class="pp-metric"><span class="pp-metric-label">Distance</span><span class="pp-metric-value">' + distStr + '</span></div>',
      '<div class="pp-metric"><span class="pp-metric-label">Max Deviation</span><span class="pp-metric-value">' + devStr + '</span></div>',
      '<div class="pp-metric"><span class="pp-metric-label">Waypoints</span><span class="pp-metric-value">' + _waypoints.length + '</span></div>',
      '<div class="pp-metric"><span class="pp-metric-label">Trail Points</span><span class="pp-metric-value">' + activeActual.length + '</span></div>',
      '<div class="pp-metric"><span class="pp-metric-label">RMS Error</span><span class="pp-metric-value">' + rmsStr + '</span></div>',
      '<div class="pp-metric"><span class="pp-metric-label">Max Error</span><span class="pp-metric-value">' + maxErrStr + '</span></div>',
      '<div class="pp-metric"><span class="pp-metric-label">RMS X</span><span class="pp-metric-value">' + rmsXStr + '</span></div>',
      '<div class="pp-metric"><span class="pp-metric-label">RMS Y</span><span class="pp-metric-value">' + rmsYStr + '</span></div>',
      '<div class="pp-metric"><span class="pp-metric-label">RMS Z</span><span class="pp-metric-value">' + rmsZStr + '</span></div>'
    ].join('') + renderLiveMetrics(activeActual);
  }

  // Live block: position, setpoint, error, rates, speed, plan length; a hint names
  // the missing telemetry when a cell cannot be filled.
  function renderLiveMetrics(activeActual) {
    var now = Date.now();
    var fbHz = rxRate(_rxFb, now), desHz = rxRate(_rxDes, now);
    var des = _desiredTrajectory.length ? _desiredTrajectory[_desiredTrajectory.length - 1] : null;
    var err = (_currentPos && des)
      ? Math.hypot(_currentPos.x - des.x, _currentPos.y - des.y, _currentPos.z == null ? 0 : _currentPos.z - des.z) : null;
    var n = _rxFb.length, speed = null, k, hint = [];
    if (fbHz != null && activeActual.length >= 2) {
      k = Math.min(n, activeActual.length, 10);
      speed = path3DLength(activeActual.slice(-k)) * 1000 / Math.max(1, _rxFb[n - 1] - _rxFb[n - k]);
    }
    if (!_currentPos) hint.push('no position feedback (locxPID/locyPID/Z_posPID FB)');
    if (!des) hint.push('no setpoint (Des) telemetry');
    if (hint.length) hint.push('load the "Paths 3D position" slot preset');
    function cell(label, value, wide) {
      return '<div class="pp-metric' + (wide ? ' pp-metric-wide' : '') + '"><span class="pp-metric-label">' + label +
        '</span><span class="pp-metric-value">' + value + '</span></div>';
    }
    return [
      cell('Position FB (m)', fmtXYZ(_currentPos), true),
      cell('Setpoint Des (m)', fmtXYZ(des), true),
      cell('Live error', err == null ? '—' : fmtNum(err, 3) + ' m'),
      cell('Speed', speed == null ? '—' : fmtNum(speed, 2) + ' m/s'),
      cell('FB rate', fbHz == null ? '—' : fmtNum(fbHz, 0) + ' Hz'),
      cell('Des rate', desHz == null ? '—' : fmtNum(desHz, 0) + ' Hz'),
      cell('Path 3D', activeActual.length > 1 ? fmtNum(path3DLength(activeActual), 2) + ' m' : '—'),
      cell('Plan length', _waypoints.length > 1 ? fmtNum(path3DLength(_waypoints), 2) + ' m' : '—'),
      hint.length ? '<div class="pp-metric pp-metric-wide pp-metric-hint">' + hint.join('; ') + '.</div>' : ''
    ].join('');
  }

  // ── Waypoint table ───────────────────────────────────────────────────────
  function renderWaypointTable() {
    if (!_elWaypointTable) return;

    var html = '<table class="pp-wp-table"><thead><tr><th>#</th><th>X (m)</th><th>Y (m)</th><th></th></tr></thead><tbody>';

    _waypoints.forEach(function (wp, i) {
      html += '<tr>' +
        '<td>' + (i + 1) + '</td>' +
        '<td><input type="number" class="pp-wp-input" data-index="' + i + '" data-axis="x" value="' + fmtNum(wp.x, 2) + '" step="0.1"></td>' +
        '<td><input type="number" class="pp-wp-input" data-index="' + i + '" data-axis="y" value="' + fmtNum(wp.y, 2) + '" step="0.1"></td>' +
        '<td><button class="pp-wp-del" data-index="' + i + '">&#10005;</button></td>' +
        '</tr>';
    });

    html += '</tbody></table>';
    html += '<button id="pp-add-wp" class="pp-btn pp-btn-sm">+ Add Waypoint</button>';
    _elWaypointTable.innerHTML = html;

    // Attach event handlers
    _elWaypointTable.querySelectorAll('.pp-wp-input').forEach(function (inp) {
      inp.addEventListener('change', function () {
        var idx = parseInt(this.getAttribute('data-index'), 10);
        var axis = this.getAttribute('data-axis');
        var val = parseFloat(this.value) || 0;
        if (axis === 'x') _waypoints[idx].x = val;
        else _waypoints[idx].y = val;
        updatePlannedPath();
        render();
      });
    });

    _elWaypointTable.querySelectorAll('.pp-wp-del').forEach(function (btn) {
      btn.addEventListener('click', function () {
        var idx = parseInt(this.getAttribute('data-index'), 10);
        _waypoints.splice(idx, 1);
        updatePlannedPath();
        renderWaypointTable();
        render();
      });
    });

    var addBtn = q('pp-add-wp');
    if (addBtn) {
      addBtn.addEventListener('click', function () {
        var last = _waypoints.length > 0 ? _waypoints[_waypoints.length - 1] : { x: 0, y: 0 };
        _waypoints.push({ x: last.x + 2, y: last.y + 2, reached: false });
        updatePlannedPath();
        renderWaypointTable();
        render();
      });
    }
  }

  function updatePlannedPath() {
    _plannedPath = _waypoints.slice();
  }

  // ── Zoom controls ────────────────────────────────────────────────────────
  function zoomIn() {
    _zoom = Math.min(_zoom + ZOOM_STEP, MAX_ZOOM);
    _autoFitted = true;   // manual zoom takes over from auto-fit
    render();
  }

  function zoomOut() {
    _zoom = Math.max(_zoom - ZOOM_STEP, MIN_ZOOM);
    _autoFitted = true;   // manual zoom takes over from auto-fit
    render();
  }


  // -- 3D View --
  function initThreeJS() {
    if (_threeLoaded) return;
    _threeLoaded = true;

    var importMapEl = document.createElement('script');
    importMapEl.type = 'importmap';
    importMapEl.textContent = JSON.stringify({
      imports: {
        'three': '/vendor/three/three.module.min.js',
        'three/addons/controls/OrbitControls.js': '/vendor/three/OrbitControls.js'
      }
    });
    document.head.appendChild(importMapEl);

    import('/vendor/three/three.module.min.js').then(function(threeMod) {
      var THREE = threeMod;
      return import('/vendor/three/OrbitControls.js').then(function(controlsMod) {
        // Module namespace objects are frozen; pass OC alongside THREE instead of attaching it.
        var OC = controlsMod.OrbitControls || controlsMod.default;
        return { THREE: THREE, OrbitControls: OC };
      });
    }).then(function(mods) {
      initThreeScene(mods.THREE, mods.OrbitControls);
    }).catch(function(err) {
      console.error('[path-panel] Failed to load three.js:', err);
      _threeLoaded = false;
    });
  }

  var SMOOTH_STEP = 0.01;     // m between interpolated samples
  var SMOOTH_MAX_SUB = 8;     // samples per raw segment, upper bound
  var SMOOTH_MIN_SEG = 0.002; // raw points closer than 2 mm are merged (sensor jitter)

  // Centripetal Catmull-Rom through the raw points (world x, y, z in m). Returns
  // dense samples {x, y, z, i}; i is the raw index of the segment start (colouring).
  // Centripetal (alpha 0.5) does not overshoot or form cusps on uneven spacing.
  function smoothPolyline(points, maxOut) {
    var pts = [], out = [], i, k, n, a, b, c, d, t0, t1, t2, t3, t, len;
    var a1, a2, a3, b1, b2, q;
    var cap = maxOut || Infinity;
    if (!points) return out;
    for (i = 0; i < points.length; i++) {
      a = points[i];
      if (pts.length) {
        b = pts[pts.length - 1];
        if (Math.hypot(a.x - b.x, a.y - b.y, (a.z || 0) - b.z) < SMOOTH_MIN_SEG) continue;
      }
      pts.push({ x: a.x, y: a.y, z: a.z || 0, i: i });
    }
    if (pts.length < 2) return pts;
    function knot(ti, p0, p1) {
      return ti + Math.max(1e-6, Math.sqrt(Math.hypot(p1.x - p0.x, p1.y - p0.y, p1.z - p0.z)));
    }
    function lerp(p0, p1, ta, tb, tv) {
      var w = (tv - ta) / (tb - ta);
      return { x: p0.x + (p1.x - p0.x) * w, y: p0.y + (p1.y - p0.y) * w, z: p0.z + (p1.z - p0.z) * w };
    }
    out.push({ x: pts[0].x, y: pts[0].y, z: pts[0].z, i: pts[0].i });
    for (k = 0; k < pts.length - 1 && out.length < cap; k++) {
      b = pts[k]; c = pts[k + 1];
      len = Math.hypot(c.x - b.x, c.y - b.y, c.z - b.z);
      n = Math.max(1, Math.min(SMOOTH_MAX_SUB, Math.ceil(len / SMOOTH_STEP)));
      // Mirror phantom end points so the end segments stay straight-ish.
      a = k > 0 ? pts[k - 1] : { x: 2 * b.x - c.x, y: 2 * b.y - c.y, z: 2 * b.z - c.z };
      d = k + 2 < pts.length ? pts[k + 2] : { x: 2 * c.x - b.x, y: 2 * c.y - b.y, z: 2 * c.z - b.z };
      t0 = 0; t1 = knot(t0, a, b); t2 = knot(t1, b, c); t3 = knot(t2, c, d);
      for (i = 1; i <= n && out.length < cap; i++) {
        if (i === n) { q = { x: c.x, y: c.y, z: c.z }; }
        else {
          t = t1 + (t2 - t1) * i / n;
          a1 = lerp(a, b, t0, t1, t); a2 = lerp(b, c, t1, t2, t); a3 = lerp(c, d, t2, t3, t);
          b1 = lerp(a1, a2, t0, t2, t); b2 = lerp(a2, a3, t1, t3, t);
          q = lerp(b1, b2, t1, t2, t);
        }
        q.i = b.i;
        out.push(q);
      }
    }
    return out;
  }

  // Fill a THREE.Line with the smoothed path; colorFn(points, i, rgbOut) optional.
  function updateLineGeometry(line, points, colorFn) {
    var geo = line && line.geometry;
    var attr, colAttr, dense, rgb = [0, 0, 0], j, pt, last = -1;
    if (!geo) return;
    if (!points || points.length < 2) { geo.setDrawRange(0, 0); return; }
    attr = geo.getAttribute('position');
    colAttr = colorFn ? geo.getAttribute('color') : null;
    dense = smoothPolyline(points, attr.count);
    for (j = 0; j < dense.length; j++) {
      pt = dense[j];
      attr.setXYZ(j, pt.x, pt.z, pt.y);   // three.js: y up, z = world y
      if (colAttr) {
        if (pt.i !== last) { colorFn(points, Math.min(pt.i, points.length - 2), rgb); last = pt.i; }
        colAttr.setXYZ(j, rgb[0], rgb[1], rgb[2]);
      }
    }
    attr.needsUpdate = true;
    if (colAttr) colAttr.needsUpdate = true;
    geo.setDrawRange(0, dense.length);
    if (line.material && line.material.isLineDashedMaterial && typeof line.computeLineDistances === 'function') {
      line.computeLineDistances();
    }
  }

  function initThreeScene(THREE, OrbitControls) {
    var canvas = _el3DCanvas;
    if (!canvas) return;

    var w = canvas.clientWidth || 600;
    var h = canvas.clientHeight || 400;

    _threeScene = new THREE.Scene();
    _threeScene.background = new THREE.Color(0x0a0a1a);

    _THREE = THREE;
    _threeCamera = new THREE.PerspectiveCamera(60, w / h, 0.01, 100);

    _threeRenderer = new THREE.WebGLRenderer({ canvas: canvas, antialias: true });
    _threeRenderer.setSize(w, h);
    _threeRenderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));

    _threeControls = new OrbitControls(_threeCamera, _threeRenderer.domElement);
    _threeControls.enableDamping = true;
    _threeControls.dampingFactor = 0.08;
    _threeControls.screenSpacePanning = true;
    _threeControls.minDistance = 0.2;
    _threeControls.maxDistance = 20;
    reset3DView();

    // Room box, floor grid and axes (rebuilt when the room size changes)
    buildRoom3D();

    // Thin polyline buffer sized for the smoothed samples (up to 4 per raw point on average).
    function buildLineGeometry(withColor) {
      var n = _MAX_3D_POINTS * 4;
      var geo = new THREE.BufferGeometry();
      geo.setAttribute('position', new THREE.BufferAttribute(new Float32Array(n * 3), 3));
      if (withColor) geo.setAttribute('color', new THREE.BufferAttribute(new Float32Array(n * 3), 3));
      geo.setDrawRange(0, 0);
      return geo;
    }

    // Desired path (thin dashed amber line)
    _threeLineDesired = new THREE.Line(buildLineGeometry(false), new THREE.LineDashedMaterial({
      color: 0xffaa00, dashSize: 0.04, gapSize: 0.025, transparent: true, opacity: 0.9
    }));
    _threeLineDesired.frustumCulled = false;
    _threeScene.add(_threeLineDesired);

    // Actual path (thin line; per-vertex colour: blue inside the room, red outside, older faded)
    _threeLineActual = new THREE.Line(buildLineGeometry(true), new THREE.LineBasicMaterial({
      vertexColors: true
    }));
    _threeLineActual.frustumCulled = false;
    _threeScene.add(_threeLineActual);

    // Floor projection of the actual path
    var shadowGeo = new THREE.BufferGeometry();
    shadowGeo.setAttribute('position', new THREE.BufferAttribute(new Float32Array(_MAX_3D_POINTS * 3), 3));
    shadowGeo.setDrawRange(0, 0);
    _threeShadow = new THREE.Line(shadowGeo, new THREE.LineBasicMaterial({
      color: 0x8899bb, transparent: true, opacity: 0.35, depthWrite: false
    }));
    _threeShadow.frustumCulled = false;
    _threeScene.add(_threeShadow);

    // Planned path overlay (hand-placed waypoints preview)
    var planPositions = new Float32Array(_MAX_3D_POINTS * 3);
    var planGeo = new THREE.BufferGeometry();
    planGeo.setAttribute('position', new THREE.BufferAttribute(planPositions, 3));
    planGeo.setDrawRange(0, 0);
    var planMat = new THREE.LineDashedMaterial({
      color: 0xf5a623,
      dashSize: 0.05,
      gapSize: 0.03,
      transparent: true,
      opacity: 0.5
    });
    _threeLinePlan = new THREE.Line(planGeo, planMat);
    _threeScene.add(_threeLinePlan);

    // The drone is drawn as a point only (no body model).
    _threePoint = new THREE.Mesh(new THREE.SphereGeometry(0.022, 16, 12),
      new THREE.MeshBasicMaterial({ color: 0x4a9eff }));
    _threePoint.visible = false;
    _threePoint.renderOrder = 9;
    _threeScene.add(_threePoint);
    rebuildEventMarkers();
    // Loaded review logs need fresh scene objects after a (re)init.
    _rvGroup = new THREE.Group();
    _threeScene.add(_rvGroup);
    Object.keys(_rvSel).forEach(function (k) { _rvSel[k].line = null; _rvSel[k].point = null; _rvSel[k].markers = null; });
    _threeControls.enabled = _mode !== 'fly';
    if (_mode === 'fly') fitFlyCamera();

    updateErrorSprite();

    _threeLoaded = true;
    render3D();

    // Damped OrbitControls need update() every frame; redraw only while the 3D view is shown.
    (function loop3D() {
      if (!_threeRenderer) return;
      requestAnimationFrame(loop3D);
      var wrap = q('pp-3d-wrap');
      if (!wrap || wrap.style.display === 'none') return;
      _threeControls.update();
      _threeRenderer.render(_threeScene, _threeCamera);
    })();
  }

  function updateErrorSprite() {
    var rmsText = (_trackingMetrics && _trackingMetrics.rms != null) ? _trackingMetrics.rms.toFixed(2) : '—';
    var maxText = (_trackingMetrics && _trackingMetrics.max != null) ? _trackingMetrics.max.toFixed(2) : '—';
    var el = q('pp-3d-error');
    if (el) el.textContent = 'RMS: ' + rmsText + ' m | Max: ' + maxText + ' m';
  }

  // Drone point at the newest (or scrubbed) position, coloured by its error.
  function updateDronePoint(active) {
    var p, e, col;
    if (!_threePoint) return;
    if (!active.length) { _threePoint.visible = false; return; }
    p = active[active.length - 1];
    _threePoint.position.set(p.x, p.z || 0, p.y);
    _threePoint.visible = true;
    e = p[errKey()];
    col = e == null ? 0x4a9eff : (e > _fly.thr ? 0xff4040 : 0x4ecca3);
    _threePoint.material.color.setHex(col);
  }

  function render3D() {
    if (!_threeRenderer || !_threeScene || !_threeCamera) return;

    updateDrawPlane();

    // Review shows the saved logs instead of the live trail.
    var review = _mode === 'review';
    [_threeLineActual, _threeShadow, _threeLineDesired, _threeLinePlan, _threePoint, _threeEventGroup].forEach(function (o) {
      if (o) o.visible = !review;
    });
    if (_rvGroup) _rvGroup.visible = review;
    if (review) {
      rvRender3D();
      _threeControls.update();
      _threeRenderer.render(_threeScene, _threeCamera);
      return;
    }

    var activeActual = getActiveTrajectory();
    var activeDesired = getActiveDesiredTrajectory();

    // Update actual path
    if (activeActual.length > 0 && _threeLineActual) {
      // Fly and Review colour the trail by tracking error; Plan by room bounds.
      updateLineGeometry(_threeLineActual, activeActual,
        _mode === 'plan' ? actualSegColor : flyColorFn(_fly.thr, errKey(), _fly.trail === 'fading' && _mode === 'fly'));
    } else if (_threeLineActual) {
      _threeLineActual.geometry.setDrawRange(0, 0);
    }
    if (_threeShadow) {
      var shAttr = _threeShadow.geometry.getAttribute('position');
      var shStart = Math.max(0, activeActual.length - _MAX_3D_POINTS);
      var sh;
      for (sh = shStart; sh < activeActual.length; sh++) {
        shAttr.setXYZ(sh - shStart, activeActual[sh].x, 0.001, activeActual[sh].y);
      }
      shAttr.needsUpdate = true;
      _threeShadow.geometry.setDrawRange(0, activeActual.length - shStart);
    }

    // Update desired path
    if (activeDesired.length > 0 && _threeLineDesired) {
      updateLineGeometry(_threeLineDesired, activeDesired);
    } else if (_threeLineDesired) {
      _threeLineDesired.geometry.setDrawRange(0, 0);
    }

    // Update planned path overlay (hand-placed waypoints)
    if (_plannedPath.length > 0 && _threeLinePlan) {
      var planAttr = _threeLinePlan.geometry.getAttribute('position');
      var planCount = Math.min(_plannedPath.length, _MAX_3D_POINTS);
      for (var p = 0; p < planCount; p++) {
        var pw = _plannedPath[p];
        planAttr.setXYZ(p, pw.x, pw.z || 0, pw.y);
      }
      planAttr.needsUpdate = true;
      _threeLinePlan.geometry.setDrawRange(0, planCount);
      if (typeof _threeLinePlan.computeLineDistances === 'function') {
        _threeLinePlan.computeLineDistances();
      }
    } else if (_threeLinePlan) {
      _threeLinePlan.geometry.setDrawRange(0, 0);
    }

    updateDronePoint(activeActual);
    updateErrorSprite();
    var oobEl = q('pp-3d-oob');
    if (oobEl) {
      var oob = countOutOfRoom(activeActual);
      oobEl.textContent = 'Outside room: ' + oob + ' / ' + activeActual.length + ' pts';
      oobEl.style.color = oob > 0 ? '#ff5050' : '';
    }

    // Follow drone
    if (_threeFollowDrone && _mode === 'plan' && activeActual.length > 0) {
      var lp = activeActual[activeActual.length - 1];
      _threeCamera.position.set(lp.x + 0.6, (lp.z || 0) + 0.45, lp.y + 0.6);
      _threeControls.target.set(lp.x, lp.z || 0, lp.y);
    }

    _threeControls.update();
    _threeRenderer.render(_threeScene, _threeCamera);
  }

  // Empties the flown and desired trails (the planned path keeps its own Clear).
  function clear3D() {
    _trajectory = [];
    _desiredTrajectory = [];
    _rxFb = [];
    _rxDes = [];
    _desiredPath = _desiredTrajectory;
    _scrubIndex = null;
    _autoFitted = false;
    _events = [];
    _runT0 = null;
    _lastPathEvt = { dir: null, t: 0 };
    rebuildEventMarkers();
    renderFlyEvents();
    renderFlyStrip();
    updateMetrics();
    renderMetrics();
    render();
    if (_threeShadow) _threeShadow.geometry.setDrawRange(0, 0);
    if (_threeLineActual) {
      _threeLineActual.geometry.setDrawRange(0, 0);
      _threeLineActual.geometry.getAttribute('position').needsUpdate = true;
    }
    if (_threeLineDesired) {
      _threeLineDesired.geometry.setDrawRange(0, 0);
      _threeLineDesired.geometry.getAttribute('position').needsUpdate = true;
    }
    if (_threeLinePlan) {
      _threeLinePlan.geometry.setDrawRange(0, 0);
      _threeLinePlan.geometry.getAttribute('position').needsUpdate = true;
    }
  }

  function fitPath3D() {
    if (!_threeCamera || !_threeControls) return;
    if (_mode === 'review') { rvFit(); return; }
    var pose = pathViewPose(getActiveTrajectory().concat(_plannedPath || []));
    _threeControls.target.set(pose.target.x, pose.target.y, pose.target.z);
    _threeCamera.position.set(pose.pos.x, pose.pos.y, pose.pos.z);
    _threeCamera.lookAt(_threeControls.target);
    _threeControls.update();
  }

  function reset3DView() {
    presetView('iso');
  }

  function presetView(preset, aspect) {
    if (!_threeCamera || !_threeControls) return;
    var pose = roomViewPose(preset, aspect);
    _threeControls.target.set(pose.target.x, pose.target.y, pose.target.z);
    _threeCamera.position.set(pose.pos.x, pose.pos.y, pose.pos.z);
    _threeCamera.lookAt(_threeControls.target);
    _threeControls.update();
  }

  // Translucent room box, 0.1 m / 0.5 m floor grid and 0.3 m axes.
  function buildRoom3D() {
    var THREE = _THREE;
    var g, boxGeo, walls, edges;
    if (!THREE || !_threeScene) return;
    if (_threeRoomGroup) {
      _threeScene.remove(_threeRoomGroup);
      _threeRoomGroup.traverse(function (o) {
        if (o.geometry) o.geometry.dispose();
        if (o.material) o.material.dispose();
      });
    }
    g = new THREE.Group();
    boxGeo = new THREE.BoxGeometry(_room.w, _room.h, _room.d);
    walls = new THREE.Mesh(boxGeo, new THREE.MeshBasicMaterial({
      color: 0x4a9eff, transparent: true, opacity: 0.04, side: THREE.BackSide, depthWrite: false
    }));
    walls.position.y = _room.h / 2;
    g.add(walls);
    edges = new THREE.LineSegments(new THREE.EdgesGeometry(boxGeo),
      new THREE.LineBasicMaterial({ color: 0x6a8cff, transparent: true, opacity: 0.6 }));
    edges.position.y = _room.h / 2;
    g.add(edges);

    _threeAxes = new THREE.AxesHelper(0.3);
    _threeAxes.position.y = 0.002;
    g.add(_threeAxes);

    _threeDrawPlane = new THREE.Mesh(
      new THREE.PlaneGeometry(Math.max(_room.w, _room.d, _room.h) * 1.5, Math.max(_room.w, _room.d, _room.h) * 1.5),
      new THREE.MeshBasicMaterial({ color: 0x4ecca3, transparent: true, opacity: 0.12,
                                    side: THREE.DoubleSide, depthWrite: false }));
    g.add(_threeDrawPlane);
    updateDrawPlane();

    _threeRoomGroup = g;
    _threeScene.add(g);
  }

  function syncDrawAltInput() {
    var el = q('pp-draw-alt');
    var r = drawOffsetRange();
    _drawAlt = Math.max(r[0], Math.min(r[1], _drawAlt));
    if (el) {
      el.min = String(r[0]);
      el.max = String(r[1]);
      el.value = String(_drawAlt);
    }
    setDrawAlt(_drawAlt);
  }

  function setRoom(r) {
    if (!validRoom(r)) return false;
    _room = { w: r.w, d: r.d, h: r.h };
    storageSet(ROOM_KEY, JSON.stringify(_room));
    syncDrawAltInput();
    buildRoom3D();
    reset3DView();
    if (_mode === 'fly') fitFlyCamera();
    render3D();
    return true;
  }

  function dispose3D() {
    if (_threeRenderer) {
      _threeRenderer.dispose();
      _threeRenderer.forceContextLoss();
      _threeRenderer = null;
    }
    if (_threeScene) {
      while (_threeScene.children.length > 0) {
        _threeScene.remove(_threeScene.children[0]);
      }
      _threeScene = null;
    }
    _threeCamera = null;
    _threeControls = null;
    _threeLineActual = null;
    _threeLineDesired = null;
    _threeLinePlan = null;
    _threeAxes = null;
    _threeShadow = null;
    _threeRoomGroup = null;
    _threeDrawPlane = null;
    _threeEventGroup = null;
    _threePoint = null;
    _rvGroup = null;
    _eventTex = {};
    _threeLoaded = false;
  }

  function updateDesiredPath(state) {
    if (!state) return;
    var des = extractDesiredPosition(state);
    if (des) {
      addDesiredPoint(des, Date.now());
    }
  }

  // First recorded value among the aliases; 0 is a real setpoint, so only
  // undefined / null / '' fall through (a || chain would skip 0).
  function firstRecorded(vals) {
    for (var i = 0; i < vals.length; i++) {
      var v = vals[i];
      if (v !== undefined && v !== null && v !== '') return v;
    }
    return undefined;
  }

  // Desired setpoint of one session record: x/y from cm to m, z unchanged.
  // null when x or y was not recorded.
  function replayDesired(row) {
    var pid = row.pid || {}, ctl = row.Ctrler || {}, c = row.c || {};
    var dx = firstRecorded([pid.locx && pid.locx.Des, row["pid.locx.Des"],
      ctl.locxPID && ctl.locxPID.Des, row["Ctrler.locxPID.Des"], c.desired_x]);
    var dy = firstRecorded([pid.locy && pid.locy.Des, row["pid.locy.Des"],
      ctl.locyPID && ctl.locyPID.Des, row["Ctrler.locyPID.Des"], c.desired_y]);
    var dz = firstRecorded([pid.z_pos && pid.z_pos.Des, row["pid.z_pos.Des"],
      ctl.Z_posPID && ctl.Z_posPID.Des, row["Ctrler.Z_posPID.Des"], c.desired_z]);
    if (dx === undefined || dy === undefined) return null;
    return { x: parseFloat(dx) / 100, y: parseFloat(dy) / 100, z: dz !== undefined ? parseFloat(dz) : 0 };
  }

  function loadSessionData() {
    var statusEl = q('pp-session-status');
    if (!statusEl) return;
    statusEl.textContent = 'Loading...';

    // Use bracket notation to avoid the network guard in the offline harness.
    var _f = window['fetch'];
    if (!_f) {
      statusEl.textContent = 'fetch unavailable';
      return;
    }

    _f('/api/recording').then(function (r) {
      if (!r.ok) throw new Error('no recording endpoint');
      return r.json();
    }).then(function (rec) {
      if (!rec || !rec.recording || !rec.session_dir) {
        statusEl.textContent = 'No active recording';
        return null;
      }
      return rec.session_dir;
    }).then(function (sessionId) {
      if (!sessionId) return;
      return _f('/sessions/' + sessionId + '/records?limit=50000').then(function (r) {
        if (!r.ok) throw new Error('no records for session');
        return r.json();
      });
    }).then(function (records) {
      if (!records || !records.records) {
        statusEl.textContent = 'No records found';
        return;
      }
      _trajectory = [];
      _desiredTrajectory = [];
      var rArr = records.records;
      for (var i = 0; i < rArr.length; i++) {
        var row = rArr[i];
        var x = row.c && row.c.earth_x;
        var y = row.c && row.c.earth_y;
        var z = row.c && row.c.altitude_cm;
        var yaw = row.imu_data && row.imu_data.yaw || row.status && row.status.yaw_deg;
        if (yaw === undefined) yaw = row["imu_data.yaw"] || row["status.yaw_deg"];
        var t = row.timestamp || row.t || i;
        if (x !== undefined && y !== undefined) {
          _trajectory.push({ 
            x: parseFloat(x) / 100, 
            y: parseFloat(y) / 100, 
            z: z ? parseFloat(z) / 100 : 0,
            yaw: yaw ? parseFloat(yaw) : 0,
            t: t
          });
        }
        var des = replayDesired(row);
        if (des) {
          des.t = t;
          _desiredTrajectory.push(des);
        }
      }
      _desiredPath = _desiredTrajectory;
      _scrubIndex = null;
      var scrubEl = q('pp-replay-scrub');
      if (scrubEl) {
        scrubEl.min = 0;
        scrubEl.max = Math.max(0, _trajectory.length - 1);
        scrubEl.value = scrubEl.max;
      }
      var timeEl = q('pp-replay-time');
      if (timeEl) timeEl.textContent = 'Live';
      updateMetrics();
      render();
      renderMetrics();
      if (_threeLoaded) render3D();
      statusEl.textContent = 'Loaded ' + _trajectory.length + ' points';
    }).catch(function (err) {
      console.warn('[path-panel] Session load failed:', err);
      statusEl.textContent = 'Load failed: ' + err.message;
    });
  }

  function resetView() {
    _zoom = 1.0;
    _panX = 0;
    _panY = 0;
    _autoFitted = false;  // re-arm auto-fit so the flown path re-frames
    render();
  }

  function setOrigin(o) {
    var dx = o.x - _origin.x;
    var dy = o.y - _origin.y;
    shiftPoints(_trajectory, dx, dy);
    shiftPoints(_desiredTrajectory, dx, dy);
    if (_currentPos) shiftPoints([_currentPos], dx, dy);
    _origin = { x: o.x, y: o.y };
    storageSet(ORIGIN_KEY, JSON.stringify(_origin));
    renderOriginStatus();
    render();
  }

  // The current measured position becomes (0, 0); altitude is untouched.
  function setOriginHere() {
    if (!_currentPos) {
      if (q('pp-origin-status')) q('pp-origin-status').textContent = 'No measured position yet; origin unchanged.';
      return;
    }
    setOrigin({ x: _currentPos.x + _origin.x, y: _currentPos.y + _origin.y });
  }

  function clearOrigin() { setOrigin({ x: 0, y: 0 }); }

  function renderOriginStatus() {
    var el = q('pp-origin-status');
    if (!el) return;
    el.textContent = (_origin.x || _origin.y)
      ? 'Origin at firmware (' + _origin.x.toFixed(2) + ', ' + _origin.y.toFixed(2) + ') m; display only.'
      : 'Origin = firmware origin.';
  }

  // ── Home/Target markers ──────────────────────────────────────────────────
  function setHomeHere() {
    if (_currentPos) {
      _homePos = { x: _currentPos.x, y: _currentPos.y, z: _currentPos.z };
      render();
    }
  }

  function setTargetHere() {
    if (_currentPos) {
      _targetPos = { x: _currentPos.x, y: _currentPos.y, z: _currentPos.z };
      render();
    }
  }

  // ── Manual home/target/waypoint entry (ground-side planning only) ───────
  // These are pure UI edits to the on-panel markers / waypoint list. They
  // never POST anything to the drone (the panel ships with no command path;
  // see the offline harness's zero-fetch guard). Empty numeric fields are
  // read as NaN and fall back to the field default, never a fake 0.
  function readNum(id, def) {
    var el = q(id);
    if (!el) return def;
    var v = parseFloat(el.value);
    return isNaN(v) ? def : v;
  }

  function setTargetManual() {
    _targetPos = { x: readNum('pp-target-x', 0), y: readNum('pp-target-y', 0),
                   z: readNum('pp-target-z', 0) };
    render();
  }

  function setHomeManual() {
    _homePos = { x: readNum('pp-home-x', 0), y: readNum('pp-home-y', 0),
                 z: readNum('pp-home-z', 0) };
    render();
  }

  // Append one manually-entered waypoint (x, y, z in metres).
  function addManualWaypoint() {
    _waypoints.push({ x: readNum('pp-wp-x', 0), y: readNum('pp-wp-y', 0),
                      z: readNum('pp-wp-z', 0), reached: false });
    updatePlannedPath();
    renderWaypointTable();
    render();
  }

  // ── Random path generator ───────────────────────────────────────────────
  // Generates N waypoints in a bounding box (±boxM in X and Y), walking a
  // random direction each step with an average segment length close to the
  // configured spacing (metres), clamped inside the box. Deterministic
  // under a seeded Math.random, so the harness can verify spacing and bounds.
  function round2(v) { return Math.round(v * 100) / 100; }

  function generateRandomPath() {
    var n = Math.max(2, Math.round(readNum('pp-rand-n', 6)));
    var spacing = Math.max(0.05, readNum('pp-rand-spacing', 0.3));
    var boxM = Math.max(0.1, readNum('pp-rand-box', 0.5));

    var pts = [];
    var px = round2((Math.random() * 2 - 1) * boxM);
    var py = round2((Math.random() * 2 - 1) * boxM);
    pts.push({ x: px, y: py, z: readNum('pp-rand-z', 0), reached: false });

    for (var i = 1; i < n; i++) {
      // Step length randomly in [0.6, 1.4]×spacing so the ~average spacing
      // is configurable while the path stays organic.
      var ang = Math.random() * Math.PI * 2;
      var step = spacing * (0.6 + Math.random() * 0.8);
      var nx = px + Math.cos(ang) * step;
      var ny = py + Math.sin(ang) * step;
      nx = Math.max(-boxM, Math.min(boxM, nx));
      ny = Math.max(-boxM, Math.min(boxM, ny));
      pts.push({ x: round2(nx), y: round2(ny), z: readNum('pp-rand-z', 0),
                 reached: false });
      px = nx; py = ny;
    }

    _waypoints = pts;
    updatePlannedPath();
    renderWaypointTable();
    render();
    if (_threeLoaded) render3D();
  }

  // ── Room-fitted preset paths ────────────────────────────────────────────
  // Shapes are centred on the room origin and kept PRESET_MARGIN inside the
  // walls and ceiling: size (radius / half-width, m) and altitude (m) are
  // clamped, so any input yields a path inside the room.
  var PRESET_MARGIN = 0.15;
  var PRESET_KINDS = ['hover', 'line', 'square', 'circle', 'figure8', 'helix'];

  function round3(v) { return Math.round(v * 1000) / 1000; }

  var PLANE_KINDS = ['xy', 'xz', 'yz'];

  // Orthonormal frame of a path plane through origin o: e1, e2 span the plane,
  // n is its normal. tilt (deg) rotates e2 towards n about e1, so xy with tilt
  // 30 is a floor plane sloping up along +y.
  function planeFrame(kind, tiltDeg, o) {
    var t = (isFinite(tiltDeg) ? tiltDeg : 0) * Math.PI / 180;
    var c = Math.cos(t), sn = Math.sin(t);
    var e1, e2, n;
    if (kind === 'xz') { e1 = [1, 0, 0]; e2 = [0, 0, 1]; n = [0, -1, 0]; }
    else if (kind === 'yz') { e1 = [0, 1, 0]; e2 = [0, 0, 1]; n = [1, 0, 0]; }
    else { e1 = [1, 0, 0]; e2 = [0, 1, 0]; n = [0, 0, 1]; }
    return {
      o: o, e1: e1,
      e2: [c * e2[0] + sn * n[0], c * e2[1] + sn * n[1], c * e2[2] + sn * n[2]],
      n: [c * n[0] - sn * e2[0], c * n[1] - sn * e2[1], c * n[2] - sn * e2[2]]
    };
  }

  function planePoint(f, u, v, w) {
    return {
      x: f.o.x + u * f.e1[0] + v * f.e2[0] + w * f.n[0],
      y: f.o.y + u * f.e1[1] + v * f.e2[1] + w * f.n[1],
      z: f.o.z + u * f.e1[2] + v * f.e2[2] + w * f.n[2]
    };
  }

  // Resample a polyline at a fixed arc-length step. Corners sharper than 30 deg
  // and both end points are kept, so a square keeps its corners.
  function resamplePath(pts, step) {
    var out, i, a, b, c, seg, carry, d, ux, uy, uz, l1, l2, cosT;
    if (!pts || pts.length < 2 || !(step > 0)) return pts ? pts.slice() : [];
    out = [pts[0]];
    carry = 0;   // arc length since the last emitted point
    for (i = 1; i < pts.length; i++) {
      a = pts[i - 1]; b = pts[i];
      seg = Math.hypot(b.x - a.x, b.y - a.y, b.z - a.z);
      if (seg < 1e-9) continue;
      ux = (b.x - a.x) / seg; uy = (b.y - a.y) / seg; uz = (b.z - a.z) / seg;
      d = step - carry;
      while (d < seg - 1e-9) {
        out.push({ x: a.x + ux * d, y: a.y + uy * d, z: a.z + uz * d });
        d += step;
      }
      carry = seg - (d - step);
      c = pts[i + 1];
      if (c) {
        l1 = seg; l2 = Math.hypot(c.x - b.x, c.y - b.y, c.z - b.z);
        cosT = l2 < 1e-9 ? 1 : ((b.x - a.x) * (c.x - b.x) + (b.y - a.y) * (c.y - b.y) + (b.z - a.z) * (c.z - b.z)) / (l1 * l2);
        if (cosT < Math.cos(Math.PI / 6)) { out.push(b); carry = 0; }
      }
    }
    b = pts[pts.length - 1];
    a = out[out.length - 1];
    if (Math.hypot(b.x - a.x, b.y - a.y, b.z - a.z) > step * 0.25) out.push(b);
    else out[out.length - 1] = b;
    return out;
  }

  // opts (optional): { plane: 'xy'|'xz'|'yz', tilt: deg, spacing: m }. The shape
  // is built in plane coordinates (u, v; w along the normal for the helix),
  // centred on (0, 0, alt), then scaled down uniformly until every point is
  // PRESET_MARGIN inside the room, then resampled at the spacing.
  function presetPath(kind, size, alt, room, opts) {
    var zMin = Math.min(0.1, room.h / 2);
    var zMax = Math.max(zMin, room.h - PRESET_MARGIN);
    var sz = Math.max(0, isFinite(size) ? size : 0.4);
    var z = Math.max(zMin, Math.min(isFinite(alt) ? alt : 0.5, zMax));
    var o = opts || {};
    var f = planeFrame(o.plane, o.tilt, { x: 0, y: 0, z: z });
    var raw = [], pts = [];
    var i, n, t, z0, z1, k, pt, sc, lo, hi, dv;
    function add(u, v, w) { raw.push(planePoint(f, u, v, w || 0)); }

    if (kind === 'hover') {
      return [{ x: 0, y: 0, z: 0, reached: false }, { x: 0, y: 0, z: round3(z), reached: false }];
    } else if (kind === 'line') {
      add(-sz, 0, z);
      add(sz, 0, z);
    } else if (kind === 'square') {
      add(-sz, -sz, z); add(sz, -sz, z); add(sz, sz, z); add(-sz, sz, z); add(-sz, -sz, z);
    } else if (kind === 'circle') {
      n = 36;
      for (i = 0; i <= n; i++) {
        t = 2 * Math.PI * i / n;
        add(sz * Math.cos(t), sz * Math.sin(t));
      }
    } else if (kind === 'figure8') {
      n = 48;
      for (i = 0; i <= n; i++) {
        t = 2 * Math.PI * i / n;
        add(sz * Math.sin(t), sz * Math.sin(t) * Math.cos(t));
      }
    } else if (kind === 'helix') {
      // Two turns climbing 0.5 m (less in a low room), centred on the altitude.
      z0 = Math.max(zMin, z - 0.25);
      z1 = Math.min(zMax, z0 + 0.5);
      n = 72;
      for (i = 0; i <= n; i++) {
        t = 4 * Math.PI * i / n;
        add(sz * Math.cos(t), sz * Math.sin(t), z0 - z + (z1 - z0) * i / n);
      }
    }
    // Uniform scale about the centre so the whole shape clears the walls.
    lo = [-room.w / 2 + PRESET_MARGIN, -room.d / 2 + PRESET_MARGIN, zMin];
    hi = [room.w / 2 - PRESET_MARGIN, room.d / 2 - PRESET_MARGIN, zMax];
    sc = 1;
    for (i = 0; i < raw.length; i++) {
      pt = [raw[i].x, raw[i].y, raw[i].z];
      for (k = 0; k < 3; k++) {
        dv = pt[k] - [f.o.x, f.o.y, f.o.z][k];
        if (dv > 1e-12) sc = Math.min(sc, Math.max(0, hi[k] - [f.o.x, f.o.y, f.o.z][k]) / dv);
        else if (dv < -1e-12) sc = Math.min(sc, Math.max(0, [f.o.x, f.o.y, f.o.z][k] - lo[k]) / -dv);
      }
    }
    for (i = 0; i < raw.length; i++) {
      raw[i] = { x: f.o.x + (raw[i].x - f.o.x) * sc, y: f.o.y + (raw[i].y - f.o.y) * sc, z: f.o.z + (raw[i].z - f.o.z) * sc };
    }
    if (o.spacing > 0) raw = resamplePath(raw, o.spacing);
    for (i = 0; i < raw.length; i++) {
      pts.push({ x: round3(raw[i].x), y: round3(raw[i].y), z: round3(raw[i].z), reached: false });
    }
    return pts;
  }

  // Firmware path commands (send_data.c, SDK mode only). Units: x/y cm, z m.
  //   hover   -> 0x0A TWC point  (0 x, 1 y, 2 z, 3 yaw, 4 execute)
  //   line    -> 0x0B sinusoid   (0-2 centre, 3 amp, 4 freq Hz, 5 dur s, 6 axis, 7 active)
  //   circle  -> 0x0C circle     (0-2 centre, 3 radius, 4 omega rad/s, 5 dur s, 6 active)
  //   figure8 -> 0x11 figure-8   (0-2 centre, 3 amp, 4 omega, 5 dur s, 6 type, 7 active); type 1 = lobes along x
  // Size and altitude are taken from the room-fitted preview so the drone flies what is drawn.
  var EXEC_KINDS = ['hover', 'line', 'circle', 'figure8'];
  var STOP_STEPS = [[0x0B, 7, 0], [0x0C, 6, 0], [0x11, 7, 0]];

  function executePlan(kind, size, alt, room, origin, opts) {
    var o = opts || {}, pts, a, z, cx, cy, sp, dur, i;
    if (EXEC_KINDS.indexOf(kind) < 0) {
      return { error: kind + ' needs waypoint-upload firmware (flyable now: hover, line, circle, figure-8)' };
    }
    if (kind !== 'hover' && ((o.plane && o.plane !== 'xy') || (o.tilt && Math.abs(o.tilt) > 1e-9))) {
      return { error: 'firmware paths are horizontal only; set plane xy, tilt 0' };
    }
    pts = presetPath(kind, size, alt, room);
    z = pts[pts.length - 1].z;
    a = 0;
    for (i = 0; i < pts.length; i++) a = Math.max(a, Math.abs(pts[i].x));
    cx = Math.round((origin.x || 0) * 1000) / 10;
    cy = Math.round((origin.y || 0) * 1000) / 10;
    sp = isFinite(o.speed) && o.speed > 0 ? Math.min(o.speed, 1) : 0.2;
    dur = isFinite(o.duration) && o.duration > 0 ? o.duration : 20;
    if (kind === 'hover') {
      if (o.yaw == null || !isFinite(o.yaw)) return { error: 'no yaw telemetry (Ctrler.yawPID.FB or imu_data.yaw); hover would turn to yaw 0' };
      return { steps: [[0x0A, 0, cx], [0x0A, 1, cy], [0x0A, 2, z], [0x0A, 3, round3(o.yaw)], [0x0A, 4, 1]] };
    }
    if (a < 0.02) return { error: 'size too small' };
    if (kind === 'line') {
      return { steps: [[0x0B, 0, cx], [0x0B, 1, cy], [0x0B, 2, z], [0x0B, 3, round3(a * 100)],
                       [0x0B, 4, round3(sp / (2 * Math.PI * a))], [0x0B, 5, dur], [0x0B, 6, 0], [0x0B, 7, 1]] };
    }
    if (kind === 'circle') {
      return { steps: [[0x0C, 0, cx], [0x0C, 1, cy], [0x0C, 2, z], [0x0C, 3, round3(a * 100)],
                       [0x0C, 4, round3(sp / a)], [0x0C, 5, dur], [0x0C, 6, 1]] };
    }
    return { steps: [[0x11, 0, cx], [0x11, 1, cy], [0x11, 2, z], [0x11, 3, round3(a * 100)],
                     [0x11, 4, round3(sp / a)], [0x11, 5, dur], [0x11, 6, 1], [0x11, 7, 1]] };
  }

  function stepText(st) {
    return '0x' + (st[0] < 16 ? '0' : '') + st[0].toString(16).toUpperCase() + '[' + st[1] + ']=' + st[2];
  }

  function setExecStatus(text, cls) {
    var el = q('pp-exec-status');
    if (!el) return;
    el.textContent = text;
    el.className = 'pp-hint' + (cls ? ' ' + cls : '');
  }

  // Send steps one at a time; the next goes only after the previous is applied.
  function runSteps(steps, label) {
    _exec = { steps: steps, i: 0, txid: null, t0: 0, label: label };
    sendNextStep();
  }

  function sendNextStep() {
    var ex = _exec, st;
    if (!ex) return;
    if (ex.i >= ex.steps.length) {
      setExecStatus(ex.label + ': all ' + ex.steps.length + ' commands applied', 'pp-exec-ok');
      _exec = null;
      if (/^Execute/.test(ex.label)) pathEvent('execute', 'path ' + ex.label.toLowerCase(), Date.now(), 'panel');
      else if (/^Stop/.test(ex.label)) pathEvent('stop', 'path stop', Date.now(), 'panel');
      return;
    }
    st = ex.steps[ex.i];
    ex.txid = null;
    ex.t0 = Date.now();
    setExecStatus(ex.label + ': sending ' + (ex.i + 1) + '/' + ex.steps.length + ' ' + stepText(st));
    _api.submitCommand(st[0], st[1], st[2]).then(function (r) {
      if (_exec !== ex) return;
      if (!r || r.transaction_id == null) { failExec('no transaction id'); return; }
      ex.txid = r.transaction_id;
    }, function (e) {
      if (_exec === ex) failExec('submit failed: ' + (e && e.message ? e.message : e));
    });
  }

  function failExec(why) {
    var ex = _exec;
    _exec = null;
    setExecStatus((ex ? ex.label + ' stopped at ' + stepText(ex.steps[ex.i]) + ': ' : '') + why, 'pp-exec-bad');
  }

  function checkExec(state) {
    var ex = _exec, res = [], i, r, reason;
    if (!ex) return;
    if (ex.txid != null && state) {
      if (state.last_transaction_result) res.push(state.last_transaction_result);
      if (state.command_results) res = res.concat(state.command_results);
      for (i = 0; i < res.length; i++) {
        r = res[i];
        if (!r || r.transaction_id !== ex.txid) continue;
        if (r.status === 'applied' || r.status === 'verified') {
          ex.i++;
          sendNextStep();
          return;
        }
        if (r.status === 'rejected' || r.status === 'timed_out' || r.status === 'error') {
          reason = r.detail || r.reason || '';
          failExec(r.status + (reason ? ' (' + reason + ')' : ''));
          return;
        }
      }
    }
    if (Date.now() - ex.t0 > EXEC_TIMEOUT_MS) failExec('no result in ' + EXEC_TIMEOUT_MS / 1000 + ' s');
  }

  function sdkFrom(state) {
    var s0, v;
    if (state.status && typeof state.status.rc_authority === 'number') return state.status.rc_authority;
    s0 = state.streams ? (state.streams[0] || state.streams['0']) : null;
    v = s0 && s0.values;
    if (!v) return null;
    if (typeof v['status.rc_authority'] === 'number') return v['status.rc_authority'];
    if (typeof v.s_authority === 'number') return v.s_authority;
    if (typeof v.ch14 === 'number') return v.ch14 & 1;
    return null;
  }

  // Heading hold for TWC: the yaw loop compares set_yaw with yawPID.FB = -imu_data.yaw.
  function holdYawFrom(state) {
    var k, v, streams = state.streams || {};
    for (k in streams) {
      v = streams[k] && streams[k].values;
      if (v && isFinite(v['Ctrler.yawPID.FB'])) return Number(v['Ctrler.yawPID.FB']);
    }
    for (k in streams) {
      v = streams[k] && streams[k].values;
      if (v && isFinite(v['imu_data.yaw'])) return -Number(v['imu_data.yaw']);
    }
    return null;
  }

  // Two-click confirm (WP-23): a browser can block window.confirm, and then Execute / Del silently did
  // nothing. The first click turns the button into "Confirm <action>?" for CONFIRM_MS and returns false;
  // a second click inside that window returns true.
  var CONFIRM_MS = 5000;
  function confirmed(btn, action) {
    if (!btn) return true;
    if (btn._ppConfirmUntil && Date.now() < btn._ppConfirmUntil) {
      clearTimeout(btn._ppConfirmTimer);
      btn._ppConfirmUntil = 0;
      btn.textContent = btn._ppConfirmLabel;
      return true;
    }
    btn._ppConfirmLabel = btn.textContent;
    btn._ppConfirmUntil = Date.now() + CONFIRM_MS;
    btn.textContent = 'Confirm ' + action + '?';
    btn._ppConfirmTimer = setTimeout(function () {
      btn._ppConfirmUntil = 0;
      btn.textContent = btn._ppConfirmLabel;
    }, CONFIRM_MS);
    return false;
  }

  function executePath() {
    var sel = q('pp-preset-kind'), kind = sel ? sel.value : 'circle', plan;
    if (_exec) { setExecStatus('busy: ' + _exec.label + ' still sending', 'pp-exec-bad'); return; }
    if (!_api || typeof _api.submitCommand !== 'function') { setExecStatus('no command channel', 'pp-exec-bad'); return; }
    if (_sdk !== 1) { setExecStatus('FlyMode is not SDK. Take SDK authority first (cmd 0x0E).', 'pp-exec-bad'); return; }
    plan = executePlan(kind, readNum('pp-preset-size', 0.4), readNum('pp-preset-alt', 0.5), _room, _origin,
      { plane: _plane.kind, tilt: _plane.tilt, speed: readNum('pp-exec-speed', 0.2),
        duration: readNum('pp-exec-dur', 20), yaw: _holdYaw });
    if (plan.error) { setExecStatus(plan.error, 'pp-exec-bad'); return; }
    if (!confirmed(q('pp-exec-go'), 'execute ' + kind)) {
      setExecStatus('Send ' + kind + ' to the drone? ' + plan.steps.map(stepText).join('; ')
        + '. Click "Confirm execute ' + kind + '?" within 5 s.');
      return;
    }
    runSteps(plan.steps, 'Execute ' + kind);
  }

  function stopPath() {
    if (!_api || typeof _api.submitCommand !== 'function') { setExecStatus('no command channel', 'pp-exec-bad'); return; }
    runSteps(STOP_STEPS.slice(), 'Stop');
  }

  function loadPresetPath() {
    var sel = q('pp-preset-kind');
    var pts = presetPath(sel ? sel.value : 'circle', readNum('pp-preset-size', 0.4),
                         readNum('pp-preset-alt', 0.5), _room, _plane);
    if (!pts.length) return;
    _waypoints = pts;
    updatePlannedPath();
    renderWaypointTable();
    render();
    if (_threeLoaded) render3D();
  }

  // ── Mouse drawing ───────────────────────────────────────────────────────
  // Strokes are clamped into the room and thinned to DRAW_MIN_STEP so a
  // mouse drag gives a usable waypoint list, not one point per pixel.
  var DRAW_MIN_STEP = 0.02;
  var DRAW_UNDO_MAX = 20;

  function clampToRoom(pt, room) {
    var hw = room.w / 2, hd = room.d / 2;
    return {
      x: round3(Math.max(-hw, Math.min(hw, pt.x))),
      y: round3(Math.max(-hd, Math.min(hd, pt.y))),
      z: round3(Math.max(0, Math.min(room.h, pt.z))),
      reached: false
    };
  }

  // Appends pt (clamped) to pts unless it is closer than minStep to the last
  // point. Returns true when a point was added.
  function thinAppend(pts, pt, minStep, room) {
    var c = clampToRoom(pt, room);
    var last = pts.length ? pts[pts.length - 1] : null;
    if (last && Math.hypot(c.x - last.x, c.y - last.y, c.z - last.z) < minStep - 1e-9) return false;
    pts.push(c);
    return true;
  }

  // ── Saved paths ─────────────────────────────────────────────────────────
  // Kept in this browser (localStorage) and exported/imported as JSON files in
  // the ground_station/service/path_library.py schema. The panel stays
  // network-free on purpose (harness CHECK 6).
  var LIB_KEY = 'pp_paths_v1';
  var LIB_MAX = 50;
  var PATH_MAX_POINTS = 5000;

  function cleanName(name) {
    var n = String(name == null ? '' : name).replace(/\s+/g, ' ').trim().slice(0, 60);
    return n || 'path';
  }

  function serializePath(name, pts, room, now) {
    return {
      name: cleanName(name),
      created_at: now,
      type: 'custom',
      room: { w: room.w, d: room.d, h: room.h },
      points: pts.map(function (w) { return { x: round3(w.x), y: round3(w.y), z: round3(w.z) }; })
    };
  }

  // Returns {name, points, room} or null. Rejects anything that is not a list
  // of finite x/y/z points; z defaults to 0 as in path_library.resample_path.
  function parsePathFile(obj) {
    var raw, pts = [], i, pt, z;
    if (typeof obj === 'string') {
      try { obj = JSON.parse(obj); } catch (e) { return null; }
    }
    raw = obj && (Array.isArray(obj) ? obj : obj.points);
    if (!Array.isArray(raw) || !raw.length || raw.length > PATH_MAX_POINTS) return null;
    for (i = 0; i < raw.length; i++) {
      pt = raw[i];
      z = pt && pt.z != null ? pt.z : 0;
      if (!pt || typeof pt.x !== 'number' || typeof pt.y !== 'number' || typeof z !== 'number' ||
          !isFinite(pt.x) || !isFinite(pt.y) || !isFinite(z)) return null;
      pts.push({ x: pt.x, y: pt.y, z: z, reached: false });
    }
    return {
      name: cleanName(obj.name),
      points: pts,
      room: validRoom(obj.room) ? { w: obj.room.w, d: obj.room.d, h: obj.room.h } : null
    };
  }

  // Same name replaces; newest first; at most LIB_MAX entries.
  function libraryPut(lib, entry) {
    var out = lib.filter(function (e) { return e.name !== entry.name; });
    out.unshift(entry);
    return out.slice(0, LIB_MAX);
  }

  function libraryLoad() {
    var lib;
    try { lib = JSON.parse(storageGet(LIB_KEY) || '[]'); } catch (e) { lib = []; }
    return Array.isArray(lib) ? lib.filter(function (e) { return parsePathFile(e); }) : [];
  }

  function librarySave(lib) { storageSet(LIB_KEY, JSON.stringify(lib)); }

  function renderLibrarySelect() {
    var sel = q('pp-lib-select');
    var lib = libraryLoad();
    if (!sel) return;
    sel.innerHTML = '';
    lib.forEach(function (e) {
      var o = document.createElement('option');
      o.value = e.name;
      o.textContent = e.name + ' (' + e.points.length + ' pts)';
      sel.appendChild(o);
    });
    if (!lib.length) {
      sel.innerHTML = '<option value="">(no saved paths)</option>';
    }
  }

  function setLibStatus(text) {
    var el = q('pp-lib-status');
    if (el) el.textContent = text;
  }

  function applyLoadedPath(parsed) {
    drawPushUndo();
    _waypoints = parsed.points;
    waypointsChanged(true);
  }

  function libSaveCurrent() {
    var nameEl = q('pp-lib-name');
    var entry;
    if (!_waypoints.length) { setLibStatus('Nothing to save: no waypoints.'); return; }
    entry = serializePath(nameEl ? nameEl.value : '', _waypoints, _room, Date.now() / 1000);
    librarySave(libraryPut(libraryLoad(), entry));
    renderLibrarySelect();
    q('pp-lib-select').value = entry.name;
    setLibStatus('Saved "' + entry.name + '" (' + entry.points.length + ' pts) in this browser.');
  }

  function libSelected() {
    var sel = q('pp-lib-select');
    var name = sel ? sel.value : '';
    return libraryLoad().filter(function (e) { return e.name === name; })[0] || null;
  }

  function libLoadSelected() {
    var e = libSelected();
    var parsed = e && parsePathFile(e);
    if (!parsed) { setLibStatus('No saved path selected.'); return; }
    applyLoadedPath(parsed);
    setLibStatus('Loaded "' + parsed.name + '" (' + parsed.points.length + ' pts). Undo restores the previous list.');
  }

  function libDeleteSelected() {
    var e = libSelected();
    if (!e) { setLibStatus('No saved path selected.'); return; }
    if (!confirmed(q('pp-lib-delete'), 'delete')) {
      setLibStatus('Remove saved path "' + e.name + '" from this browser? Click "Confirm delete?" within 5 s.');
      return;
    }
    librarySave(libraryLoad().filter(function (x) { return x.name !== e.name; }));
    renderLibrarySelect();
    setLibStatus('Removed "' + e.name + '".');
  }

  function libExport() {
    var nameEl = q('pp-lib-name');
    var entry, blob, a;
    if (!_waypoints.length) { setLibStatus('Nothing to export: no waypoints.'); return; }
    entry = serializePath(nameEl ? nameEl.value : '', _waypoints, _room, Date.now() / 1000);
    blob = new Blob([JSON.stringify(entry, null, 2)], { type: 'application/json' });
    a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = entry.name.replace(/[^A-Za-z0-9_.-]+/g, '_') + '.json';
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    setTimeout(function () { URL.revokeObjectURL(a.href); }, 1000);
    setLibStatus('Exported ' + a.download + '.');
  }

  function libImportFile(file) {
    var reader;
    if (!file) return;
    reader = new FileReader();
    reader.onload = function () {
      var parsed = parsePathFile(String(reader.result));
      if (!parsed) { setLibStatus('Import failed: ' + file.name + ' is not a path file.'); return; }
      applyLoadedPath(parsed);
      if (q('pp-lib-name')) q('pp-lib-name').value = parsed.name;
      setLibStatus('Imported "' + parsed.name + '" (' + parsed.points.length + ' pts). Save to keep it here.');
    };
    reader.readAsText(file);
  }

  function drawPushUndo() {
    _drawUndo.push(_waypoints.map(function (w) { return { x: w.x, y: w.y, z: w.z, reached: false }; }));
    if (_drawUndo.length > DRAW_UNDO_MAX) _drawUndo.shift();
  }

  function waypointsChanged(full) {
    updatePlannedPath();
    if (full) renderWaypointTable();
    render();
    if (_threeLoaded) render3D();
  }

  function drawUndo() {
    if (!_drawUndo.length) return;
    _waypoints = _drawUndo.pop();
    waypointsChanged(true);
  }

  function drawClear() {
    if (!_waypoints.length) return;
    drawPushUndo();
    _waypoints = [];
    waypointsChanged(true);
  }

  function drawAddPoint(pt) {
    if (thinAppend(_waypoints, pt, _plane.spacing > 0 ? _plane.spacing : DRAW_MIN_STEP, _room)) waypointsChanged(false);
  }

  function setDrawMode(on) {
    var btn = q('pp-draw-toggle');
    _drawMode = !!on;
    _drawStroke = false;
    if (btn) {
      btn.classList.toggle('active', _drawMode);
      btn.textContent = _drawMode ? 'Drawing: on' : 'Draw';
    }
    if (_threeControls) _threeControls.enabled = !_drawMode;
    if (_canvas) _canvas.style.cursor = _drawMode ? 'crosshair' : '';
    if (_el3DCanvas) _el3DCanvas.style.cursor = _drawMode ? 'crosshair' : '';
    if (_drawMode) fitRoom2D();
    if (_threeLoaded) render3D();
  }

  // Offset range of the draw plane along its normal axis: z for xy, y for xz, x for yz.
  function drawOffsetRange() {
    if (_plane.kind === 'xz') return [-_room.d / 2, _room.d / 2];
    if (_plane.kind === 'yz') return [-_room.w / 2, _room.w / 2];
    return [0, _room.h];
  }

  // The draw plane: tilted about its first axis through a pivot on the offset
  // axis (vertical planes pivot at mid-height).
  function drawFrame() {
    var o;
    if (_plane.kind === 'xz') o = { x: 0, y: _drawAlt, z: _room.h / 2 };
    else if (_plane.kind === 'yz') o = { x: _drawAlt, y: 0, z: _room.h / 2 };
    else o = { x: 0, y: 0, z: _drawAlt };
    return planeFrame(_plane.kind, _plane.tilt, o);
  }

  function setDrawAlt(v) {
    var r = drawOffsetRange();
    if (!isFinite(v)) return;
    _drawAlt = Math.max(r[0], Math.min(r[1], v));
    var lbl = q('pp-draw-alt-val');
    if (lbl) lbl.textContent = ({ xy: 'z ', xz: 'y ', yz: 'x ' }[_plane.kind] || '') + _drawAlt.toFixed(2) + ' m';
    if (_threeLoaded) render3D();
  }

  function setPlane(kind, tilt, spacing) {
    if (PLANE_KINDS.indexOf(kind) >= 0 && kind !== _plane.kind) {
      _plane.kind = kind;
      _drawAlt = kind === 'xy' ? 0.5 : 0;
      syncDrawAltInput();
    }
    if (isFinite(tilt)) _plane.tilt = Math.max(-80, Math.min(80, tilt));
    if (isFinite(spacing)) _plane.spacing = Math.max(0.005, Math.min(0.5, spacing));
    if (_threeLoaded) render3D();
  }

  function updateDrawPlane() {
    var f, THREE = _THREE, c;
    if (!_threeDrawPlane || !THREE) return;
    _threeDrawPlane.visible = _drawMode;
    f = drawFrame();
    c = planePoint(f, 0, 0, 0);
    // PlaneGeometry spans local x/y with normal +z; three axes are (x, z, y) of the room.
    _threeDrawPlane.position.set(c.x, c.z, c.y);
    _threeDrawPlane.rotation.set(0, 0, 0);
    _threeDrawPlane.quaternion.setFromRotationMatrix(new THREE.Matrix4().makeBasis(
      new THREE.Vector3(f.e1[0], f.e1[2], f.e1[1]),
      new THREE.Vector3(f.e2[0], f.e2[2], f.e2[1]),
      new THREE.Vector3(f.n[0], f.n[2], f.n[1])));
  }

  // Room point under the mouse on the draw plane, or null.
  function pick3D(ev) {
    var THREE = _THREE;
    var rect, ndc, ray, hit;
    if (!THREE || !_threeCamera || !_el3DCanvas) return null;
    rect = _el3DCanvas.getBoundingClientRect();
    if (!rect.width || !rect.height) return null;
    ndc = new THREE.Vector2((ev.clientX - rect.left) / rect.width * 2 - 1,
                            -(ev.clientY - rect.top) / rect.height * 2 + 1);
    ray = new THREE.Raycaster();
    ray.setFromCamera(ndc, _threeCamera);
    hit = new THREE.Vector3();
    var f = drawFrame();
    var nv = new THREE.Vector3(f.n[0], f.n[2], f.n[1]);   // three (x, y, z) = room (x, z, y)
    if (!ray.ray.intersectPlane(new THREE.Plane(nv, -nv.dot(new THREE.Vector3(f.o.x, f.o.z, f.o.y))), hit)) return null;
    return { x: hit.x, y: hit.z, z: hit.y };
  }

  // Top-view pick lifted vertically onto the draw plane. Vertical planes (or
  // tilts near 90 deg) project to a line in top view, so the 2D view refuses them.
  function planeFromTop(x, y) {
    var f = drawFrame();
    if (Math.abs(f.n[2]) < 0.2) return null;
    return { x: x, y: y, z: f.o.z - (f.n[0] * (x - f.o.x) + f.n[1] * (y - f.o.y)) / f.n[2] };
  }

  function pick2D(ev) {
    var rect = _canvas.getBoundingClientRect();
    var sx = (ev.clientX - rect.left) * (_canvas.width / (rect.width || 1));
    var sy = (ev.clientY - rect.top) * (_canvas.height / (rect.height || 1));
    var w = screenToWorld(sx, sy, _canvas.width, _canvas.height);
    return planeFromTop(w.x, w.y);
  }

  function bindDrawCanvas(el, pick) {
    if (!el) return;
    el.addEventListener('pointerdown', function (ev) {
      var pt;
      if (!_drawMode || ev.button !== 0) return;
      pt = pick(ev);
      if (!pt) return;
      ev.preventDefault();
      ev.stopPropagation();
      drawPushUndo();
      _drawStroke = true;
      if (el.setPointerCapture) el.setPointerCapture(ev.pointerId);
      drawAddPoint(pt);
    }, true);
    el.addEventListener('pointermove', function (ev) {
      var pt;
      if (!_drawMode || !_drawStroke) return;
      pt = pick(ev);
      if (pt) drawAddPoint(pt);
    });
    var end = function () {
      if (!_drawStroke) return;
      _drawStroke = false;
      renderWaypointTable();
    };
    el.addEventListener('pointerup', end);
    el.addEventListener('pointercancel', end);
  }

  // ── Fly mode: error, statistics, colours, events, strip ─────────────────
  // DroneStatus.FlyMode values defined by the firmware (Global_file/global_declare.h,
  // set by API/flight_fsm.c): 0 FlyMode_DangerousStop, 1 FlyMode_SDK.
  var FLY_MODE_LABELS = ['Stop', 'SDK'];
  var MODE_KEYS = ['status.flymode', 'DroneStatus.FlyMode'];
  var ADAPT_KEYS = ['mrac_flags.adaptation_on'];
  var VBAT_KEYS = ['status.vbat', 'real_voltage'];
  var TWC_KEYS = ['status.twc_execute', 'TWC.execute'];
  var STAT_GAP_MS = 500;       // a gap longer than this is not carried across in time-weighted stats

  var EVENT_KINDS = {
    adapt:   { label: 'Adaptation on/off', letter: 'A', color: 0xc77dff, css: '#c77dff' },
    mode:    { label: 'Mode change',       letter: 'M', color: 0xffd166, css: '#ffd166' },
    path:    { label: 'Path execute/stop', letter: 'P', color: 0x4cc9f0, css: '#4cc9f0' },
    note:    { label: 'REC note',          letter: 'N', color: 0xffffff, css: '#ffffff' },
    finding: { label: 'Agent finding',     letter: 'F', color: 0xff9f1c, css: '#ff9f1c' }
  };
  var EVENT_ORDER = ['adapt', 'mode', 'path', 'note', 'finding'];
  var FLY_VIEWS = ['top', 'side', 'front', 'iso'];   // keys 1-4, same order as the camera buttons
  var _flyView = 'iso';
  var _keyHandler = null;
  var _recStopArmedAt = 0;

  function validFly(f) {
    return !!f && isFinite(f.thr) && f.thr >= THR_MIN && f.thr <= THR_MAX &&
      (f.metric === '3d' || f.metric === 'xy') && (f.trail === 'whole' || f.trail === 'fading');
  }

  function loadFly() {
    var f = null;
    try { f = JSON.parse(storageGet(FLY_KEY) || 'null'); } catch (e) { f = null; }
    if (f) f = { thr: Number(f.thr), metric: f.metric, trail: f.trail };
    return validFly(f) ? f : { thr: FLY_DEFAULT.thr, metric: FLY_DEFAULT.metric, trail: FLY_DEFAULT.trail };
  }

  function loadMode() {
    var m = storageGet(MODE_KEY);
    return MODES.indexOf(m) >= 0 ? m : 'plan';
  }

  function setFly(patch) {
    var next = { thr: _fly.thr, metric: _fly.metric, trail: _fly.trail };
    var k;
    for (k in patch) if (Object.prototype.hasOwnProperty.call(patch, k)) next[k] = patch[k];
    if (!validFly(next)) return false;
    _fly = next;
    storageSet(FLY_KEY, JSON.stringify(_fly));
    syncFlyInputs();
    refreshFlyViews();
    return true;
  }

  // Position error between a measured and a firmware-setpoint point (metres).
  // 3D needs both heights; without a z setpoint the 3D error falls back to xy
  // instead of comparing a height against a fabricated 0.
  function errorBetween(p, d) {
    var dx, dy, exy, e3;
    if (!p || !d || p.x == null || d.x == null || p.y == null || d.y == null) return null;
    dx = p.x - d.x; dy = p.y - d.y;
    exy = Math.hypot(dx, dy);
    e3 = exy;
    if (d.hasZ && p.z != null && d.z != null) e3 = Math.hypot(dx, dy, p.z - d.z);
    return { e3: e3, exy: exy };
  }

  function errKey() { return _fly.metric === 'xy' ? 'exy' : 'e3'; }

  // Error statistics over points {t (ms), e3, exy}. Time-weighted share above
  // the threshold: each sample holds until the next one (gaps over STAT_GAP_MS
  // are not carried); with under two usable samples it falls back to a sample
  // count. t0 (ms) skips earlier points.
  function flyStats(points, thr, key, t0) {
    var n = 0, sumSq = 0, max = null, cur = null, above = 0, span = 0, aboveT = 0;
    var prevT = null, prevE = null, i, p, e, dt;
    for (i = 0; i < (points ? points.length : 0); i++) {
      p = points[i];
      if (t0 != null && p.t < t0) continue;
      e = p[key];
      if (e == null || !isFinite(e)) continue;
      n++; sumSq += e * e; cur = e;
      if (max === null || e > max) max = e;
      if (e > thr) above++;
      if (prevT !== null) {
        dt = p.t - prevT;
        if (dt > 0 && dt <= STAT_GAP_MS) { span += dt; if (prevE > thr) aboveT += dt; }
      }
      prevT = p.t; prevE = e;
    }
    return {
      n: n, cur: cur, max: max,
      rms: n ? Math.sqrt(sumSq / n) : null,
      pctAbove: span > 0 ? 100 * aboveT / span : (n ? 100 * above / n : null)
    };
  }

  function fmtRunTime(ms) {
    var s, m;
    if (ms == null || !isFinite(ms) || ms < 0) return '—';
    s = Math.floor(ms / 1000);
    m = Math.floor(s / 60);
    s = s % 60;
    return m + ':' + (s < 10 ? '0' : '') + s;
  }

  // Trail colour by error: green at or below the threshold, red above, grey-blue
  // where no setpoint was published. A segment is red when either end is above.
  // Fading dims all but the newest FADE_POINTS points.
  function flyColorFn(thr, key, fading) {
    return function (points, i, out) {
      var a = points[i][key], b = points[i + 1] ? points[i + 1][key] : null, e, f = 1, age;
      e = a == null ? b : (b == null ? a : Math.max(a, b));
      if (fading) {
        age = points.length - 2 - i;
        f = age <= FADE_POINTS ? 1 - 0.85 * age / FADE_POINTS : 0.15;
      }
      if (e == null) { out[0] = 0.45 * f; out[1] = 0.55 * f; out[2] = 0.75 * f; }
      else if (e > thr) { out[0] = 1.0 * f; out[1] = 0.25 * f; out[2] = 0.25 * f; }
      else { out[0] = 0.20 * f; out[1] = 0.85 * f; out[2] = 0.45 * f; }
    };
  }

  // First numeric value for any of `names` across every stream (bare or
  // slot-prefixed), then the flat / status fallbacks. null when absent.
  function lookupAny(state, names) {
    var k, v, r, i, streams = state && state.streams;
    if (!state) return null;
    if (streams) {
      for (k in streams) {
        v = streams[k] && streams[k].values;
        if (!v) continue;
        r = lookupValue(v, names);
        if (r !== undefined && r !== null && !isNaN(Number(r))) return Number(r);
      }
    }
    v = state.values || state;
    r = lookupValue(v, names);
    if (r !== undefined && r !== null && !isNaN(Number(r))) return Number(r);
    if (state.status) {
      for (i = 0; i < names.length; i++) {
        if (names[i].indexOf('status.') !== 0) continue;
        r = state.status[names[i].slice(7)];
        if (r !== undefined && r !== null && !isNaN(Number(r))) return Number(r);
      }
    }
    return null;
  }

  function extractFlyStatus(state) {
    return {
      mode: lookupAny(state, MODE_KEYS),
      adapt: lookupAny(state, ADAPT_KEYS),
      vbat: lookupAny(state, VBAT_KEYS),
      twc: lookupAny(state, TWC_KEYS)
    };
  }

  function modeLabel(v) {
    if (v == null) return '—';
    return FLY_MODE_LABELS[v] || ('mode ' + v);
  }

  // Command values that start / stop a firmware path (the last step of each
  // Execute sequence and the Stop steps); shared by the live panel and the
  // server-side log parser (rec_logs.py) so both mark the same events.
  function isPathExecuteCmd(cmd, idx, val) {
    return val === 1 && ((cmd === 0x0A && idx === 4) || (cmd === 0x0B && idx === 7) ||
                         (cmd === 0x0C && idx === 6) || (cmd === 0x11 && idx === 7));
  }
  function isPathStopCmd(cmd, idx, val) {
    return val === 0 && ((cmd === 0x0B && idx === 7) || (cmd === 0x0C && idx === 6) ||
                         (cmd === 0x11 && idx === 7));
  }

  // Trajectory point nearest in time to t (ms); null when there is none within
  // 5 s (an event outside the trail has no place to sit).
  function posAtTime(t) {
    var i, best = null, bd = Infinity, d;
    for (i = _trajectory.length - 1; i >= 0; i--) {
      d = Math.abs(_trajectory[i].t - t);
      if (d < bd) { bd = d; best = _trajectory[i]; }
      else if (_trajectory[i].t < t) break;
    }
    if (!best || bd > 5000) return null;
    return { x: best.x, y: best.y, z: best.z == null ? 0 : best.z };
  }

  function pushEvent(kind, text, t, source) {
    var ev;
    if (!EVENT_KINDS[kind]) return null;
    t = t == null ? Date.now() : t;
    ev = { kind: kind, text: String(text), t: t, pos: posAtTime(t), source: source || 'panel' };
    _events.push(ev);
    if (_events.length > MAX_EVENTS) _events.shift();
    rebuildEventMarkers();
    renderFlyEvents();
    return ev;
  }

  // dir 'execute' starts the run clock; the same direction twice within 3 s is
  // one event (the panel's own command and the TWC flag both report it).
  function pathEvent(dir, text, t, source) {
    t = t == null ? Date.now() : t;
    if (_lastPathEvt.dir === dir && t - _lastPathEvt.t < 3000) return null;
    _lastPathEvt = { dir: dir, t: t };
    if (dir === 'execute') _runT0 = t;
    return pushEvent('path', text, t, source);
  }

  function detectEvents(st, now) {
    var p = _flyStatus;
    if (st.adapt != null && p.adapt != null && (st.adapt ? 1 : 0) !== (p.adapt ? 1 : 0)) {
      pushEvent('adapt', 'adaptation ' + (st.adapt ? 'ON' : 'OFF'), now, 'telemetry');
    }
    if (st.mode != null && p.mode != null && st.mode !== p.mode) {
      pushEvent('mode', modeLabel(p.mode) + ' → ' + modeLabel(st.mode), now, 'telemetry');
    }
    if (st.twc != null && p.twc != null && (st.twc ? 1 : 0) !== (p.twc ? 1 : 0)) {
      pathEvent(st.twc ? 'execute' : 'stop', st.twc ? 'path execute (TWC)' : 'path stop (TWC)', now, 'telemetry');
    }
    if (st.mode != null) p.mode = st.mode;
    if (st.adapt != null) p.adapt = st.adapt;
    if (st.vbat != null) p.vbat = st.vbat;
    if (st.twc != null) p.twc = st.twc;
  }

  // REC notes and agent findings come from the service note log (read-only
  // GET). Only notes inside the current trail's time range become markers.
  function applyNotes(list) {
    var i, n, t, kind;
    if (!list || !list.length) return;
    for (i = 0; i < list.length; i++) {
      n = list[i];
      if (!n || typeof n.seq !== 'number' || n.seq <= _notesSeq) continue;
      _notesSeq = n.seq;
      t = Number(n.ts) * 1000;
      if (!isFinite(t) || !_trajectory.length || t < _trajectory[0].t - 1000) continue;
      // Agent messages carry kind 'agent' and a source like 'agent:copilot'.
      kind = (String(n.source || '').indexOf('agent') === 0 || n.kind === 'agent' || n.kind === 'finding') ? 'finding' : 'note';
      pushEvent(kind, (n.kind && n.kind !== 'note' && kind === 'note' ? n.kind + ': ' : '') + n.text, t, n.source || 'operator');
    }
  }

  function pollNotes() {
    var f = window['fetch'];
    if (typeof f !== 'function') return;
    f('/api/session/notes?since=' + _notesSeq).then(function (r) {
      return r && r.ok ? r.json() : null;
    }).then(function (d) {
      if (d && d.notes) applyNotes(d.notes);
    }).catch(function () { /* the marker feed is best-effort */ });
  }

  function startNotesPoll() {
    if (_notesTimer || typeof setInterval !== 'function') return;
    _notesTimer = setInterval(pollNotes, 3000);
    if (_notesTimer && typeof _notesTimer.unref === 'function') _notesTimer.unref();
    pollNotes();
  }

  function stopNotesPoll() {
    if (_notesTimer) { clearInterval(_notesTimer); _notesTimer = null; }
  }

  function flyCell(label, value, color) {
    return '<div class="pp-fly-cell"><span class="pp-fly-label">' + label + '</span>' +
      '<span class="pp-fly-value"' + (color ? ' style="color:' + color + '"' : '') + '>' + value + '</span></div>';
  }

  function renderFlyStrip() {
    var el = q('pp-fly-strip'), pts, s, t0, last, run, col, adapt;
    if (!el) return;
    pts = getActiveTrajectory();
    t0 = (_runT0 != null && pts.length && pts[pts.length - 1].t >= _runT0) ? _runT0 : null;
    s = flyStats(pts, _fly.thr, errKey(), t0);
    last = pts.length ? pts[pts.length - 1] : null;
    run = last ? last.t - (t0 != null ? t0 : pts[0].t) : null;
    col = s.cur == null ? '' : (s.cur > _fly.thr ? '#ff5050' : '#4ecca3');
    adapt = _flyStatus.adapt;
    el.innerHTML = [
      flyCell('Error ' + (_fly.metric === 'xy' ? 'xy' : '3D'), s.cur == null ? '—' : fmtNum(s.cur, 3) + ' m', col),
      flyCell('Run RMS', s.rms == null ? '—' : fmtNum(s.rms, 3) + ' m'),
      flyCell('Above ' + fmtNum(_fly.thr, 2) + ' m', s.pctAbove == null ? '—' : fmtNum(s.pctAbove, 0) + ' %'),
      flyCell('Flight mode', modeLabel(_flyStatus.mode)),
      flyCell('Adaptation', adapt == null ? '—' : (adapt ? 'ON' : 'OFF'), adapt ? '#c77dff' : ''),
      flyCell('Vbat', _flyStatus.vbat == null ? '—' : fmtNum(_flyStatus.vbat, 2) + ' V'),
      flyCell(t0 != null ? 'Run time' : 'Trail time', fmtRunTime(run))
    ].join('');
    renderFlyLegend(s);
  }

  function renderFlyLegend(s) {
    var el = q('pp-fly-legend'), html = '', i, k;
    if (!el) return;
    html += '<span class="pp-fly-key"><i style="background:#4ecca3"></i>&le; ' + fmtNum(_fly.thr, 2) + ' m</span>';
    html += '<span class="pp-fly-key"><i style="background:#ff4040"></i>&gt; ' + fmtNum(_fly.thr, 2) + ' m</span>';
    for (i = 0; i < EVENT_ORDER.length; i++) {
      k = EVENT_KINDS[EVENT_ORDER[i]];
      html += '<span class="pp-fly-key"><b style="color:' + k.css + '">' + k.letter + '</b> ' + k.label + '</span>';
    }
    el.innerHTML = html;
  }

  function renderFlyEvents() {
    var el = q('pp-fly-events'), i, ev, base, html = '', n = 0;
    if (!el) return;
    base = _trajectory.length ? _trajectory[0].t : (_events.length ? _events[0].t : 0);
    for (i = _events.length - 1; i >= 0 && n < 6; i--, n++) {
      ev = _events[i];
      html += '<div class="pp-fly-ev"><b style="color:' + EVENT_KINDS[ev.kind].css + '">' + EVENT_KINDS[ev.kind].letter +
        '</b> <span class="pp-fly-ev-t">' + fmtRunTime(ev.t - base) + '</span> ' + escapeText(ev.text) + '</div>';
    }
    el.innerHTML = html || '<div class="pp-fly-ev pp-fly-ev-none">No events yet</div>';
  }

  function escapeText(s) {
    return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }

  function syncFlyInputs() {
    var thr = q('pp-fly-thr'), val = q('pp-fly-thr-val'), m = q('pp-fly-metric'), tr = q('pp-fly-trail');
    var rthr = q('pp-rv-thr'), rval = q('pp-rv-thr-val'), rmet = q('pp-rv-metric');
    if (thr) thr.value = String(_fly.thr);
    if (val) val.textContent = fmtNum(_fly.thr, 2) + ' m';
    if (m) m.value = _fly.metric;
    if (tr) tr.value = _fly.trail;
    if (rthr) rthr.value = String(_fly.thr);
    if (rval) rval.textContent = fmtNum(_fly.thr, 2) + ' m';
    if (rmet) rmet.value = _fly.metric;
  }

  function refreshFlyViews() {
    renderFlyStrip();
    if (typeof renderReviewLegend === 'function') renderReviewLegend();
    if (_threeLoaded) render3D();
  }

  // ── Event markers in 3D ────────────────────────────────────────────────
  function eventTexture(kind) {
    var c, g, k = EVENT_KINDS[kind];
    if (_eventTex[kind]) return _eventTex[kind];
    if (!_THREE || typeof document.createElement !== 'function') return null;
    c = document.createElement('canvas');
    c.width = 64; c.height = 64;
    g = c.getContext('2d');
    if (!g) return null;
    g.fillStyle = 'rgba(10,10,26,0.85)';
    g.beginPath(); g.arc(32, 32, 28, 0, Math.PI * 2); g.fill();
    g.strokeStyle = k.css; g.lineWidth = 5;
    g.beginPath(); g.arc(32, 32, 28, 0, Math.PI * 2); g.stroke();
    g.fillStyle = k.css; g.font = 'bold 34px sans-serif'; g.textAlign = 'center'; g.textBaseline = 'middle';
    g.fillText(k.letter, 32, 34);
    _eventTex[kind] = new _THREE.CanvasTexture(c);
    return _eventTex[kind];
  }

  function clearEventGroup() {
    var i, o;
    if (!_threeEventGroup) return;
    for (i = _threeEventGroup.children.length - 1; i >= 0; i--) {
      o = _threeEventGroup.children[i];
      _threeEventGroup.remove(o);
      if (o.material) o.material.dispose();      // the shared textures stay cached
    }
  }

  // One sprite per event at the trail position where it happened, lifted 3 cm
  // so it does not sit on the line.
  function rebuildEventMarkers() {
    var i, ev, tex, sp;
    if (!_THREE || !_threeScene) return;
    if (!_threeEventGroup) { _threeEventGroup = new _THREE.Group(); _threeScene.add(_threeEventGroup); }
    clearEventGroup();
    for (i = 0; i < _events.length; i++) {
      ev = _events[i];
      if (!ev.pos) continue;
      tex = eventTexture(ev.kind);
      if (!tex) continue;
      sp = new _THREE.Sprite(new _THREE.SpriteMaterial({ map: tex, depthTest: false, transparent: true }));
      sp.scale.set(0.07, 0.07, 1);
      sp.position.set(ev.pos.x, ev.pos.z + 0.03, ev.pos.y);
      sp.renderOrder = 10;
      _threeEventGroup.add(sp);
    }
  }

  // ── Mode switching ─────────────────────────────────────────────────────
  function setMode(mode) {
    if (MODES.indexOf(mode) < 0) return false;
    _mode = mode;
    storageSet(MODE_KEY, mode);
    applyModeLayout();
    return true;
  }

  function setView(v) {
    var w2 = q('pp-2d-wrap'), w3 = q('pp-3d-wrap'), b2 = q('pp-view-2d'), b3 = q('pp-view-3d');
    if (v === '3d') {
      if (!_threeLoaded) initThreeJS();
      if (w2) w2.style.display = 'none';
      if (w3) w3.style.display = '';
      if (b3) b3.className = 'pp-view-btn active';
      if (b2) b2.className = 'pp-view-btn';
      setTimeout(function () { render3D(); }, 50);
    } else {
      if (w2) w2.style.display = '';
      if (w3) w3.style.display = 'none';
      if (b2) b2.className = 'pp-view-btn active';
      if (b3) b3.className = 'pp-view-btn';
    }
  }

  function show(id, on) {
    var el = q(id);
    if (el) el.style.display = on ? '' : 'none';
  }

  function applyModeLayout() {
    var cont = q('pp-container'), i, b;
    if (cont) cont.className = 'pp-container pp-mode-' + _mode;
    for (i = 0; i < MODES.length; i++) {
      b = q('pp-mode-' + MODES[i]);
      if (b) b.className = 'pp-mode-btn' + (MODES[i] === _mode ? ' active' : '');
    }
    show('pp-sidebar', _mode !== 'fly');
    show('pp-tools-plan', _mode === 'plan');
    show('pp-tools-review', _mode === 'review');
    show('pp-fly-bar', _mode === 'fly');
    show('pp-fly-events', _mode === 'fly');
    show('pp-view-toggle', _mode === 'plan');
    show('pp-3d-legend', _mode === 'plan');
    show('pp-3d-topright', _mode === 'plan');
    if (_mode !== 'plan') setView('3d');
    if (_threeControls) _threeControls.enabled = _mode !== 'fly';
    if (_mode === 'fly') { fitFlyCamera(); startNotesPoll(); } else stopNotesPoll();
    syncFlyInputs();
    renderFlyStrip();
    renderFlyEvents();
    if (typeof onReviewShown === 'function' && _mode === 'review') onReviewShown();
    if (_threeLoaded) render3D();
  }

  // Fixed room camera: refit to the canvas aspect (a narrow canvas needs a
  // longer view to keep the room in frame).
  function fitFlyCamera() {
    var w, h;
    if (!_threeCamera || !_threeControls) return;
    w = _el3DCanvas ? _el3DCanvas.clientWidth : 0;
    h = _el3DCanvas ? _el3DCanvas.clientHeight : 0;
    presetView(_flyView, w > 0 && h > 0 ? w / h : undefined);
  }

  function setFlyView(v) {
    if (FLY_VIEWS.indexOf(v) < 0) return;
    _flyView = v;
    presetView(v, _el3DCanvas && _el3DCanvas.clientHeight > 0 ? _el3DCanvas.clientWidth / _el3DCanvas.clientHeight : undefined);
  }

  function isTypingTarget(t) {
    var tag = t && t.tagName ? String(t.tagName).toUpperCase() : '';
    return tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || !!(t && t.isContentEditable);
  }

  // Fly-mode shortcuts. UI only: none of them sends a flight command.
  //   R  REC (a second press within 1.5 s is needed to STOP a running REC)
  //   N  focus the note box       F  refit camera
  //   T  whole flight / fading trail        1-4  Top / Side / Front / Iso
  function onFlyKey(ev) {
    var k, w3, rb, ni, now;
    if (_mode !== 'fly' || !ev || ev.ctrlKey || ev.metaKey || ev.altKey || isTypingTarget(ev.target)) return;
    w3 = q('pp-3d-wrap');
    // A hidden workspace tab reports width 0: ignore keys meant for another tab.
    if (!w3 || (typeof w3.offsetWidth === 'number' && w3.offsetWidth === 0)) return;
    k = String(ev.key || '').toLowerCase();
    if (k === 'r') {
      rb = document.getElementById('record-btn');
      if (!rb || rb.disabled) return;
      now = Date.now();
      if (window.recOn && now - _recStopArmedAt > 1500) {
        _recStopArmedAt = now;
        setFlyHint('Press R again to stop REC');
        return;
      }
      _recStopArmedAt = 0;
      rb.click();
    } else if (k === 'n') {
      ni = document.getElementById('note-input');
      if (ni && typeof ni.focus === 'function') { ni.focus(); if (ev.preventDefault) ev.preventDefault(); }
    } else if (k === 'f') {
      fitFlyCamera();
    } else if (k === 't') {
      setFly({ trail: _fly.trail === 'whole' ? 'fading' : 'whole' });
    } else if (k >= '1' && k <= '4') {
      setFlyView(FLY_VIEWS[Number(k) - 1]);
    } else {
      return;
    }
  }

  function setFlyHint(text) {
    var el = q('pp-fly-hint');
    if (el) el.textContent = text;
  }

  // ── Review mode: overlay of saved REC logs ─────────────────────────────
  // Any number of saved REC sessions (GET /api/rec-logs) are drawn together,
  // one colour per log or coloured by error, against one scrubber whose t = 0
  // is the path execute of each log (REC start when a log has none).
  var RV_KEY = 'pp_rv_sel_v1';
  var RV_COLORS = ['#4a9eff', '#ff9f43', '#c77dff', '#4ecca3', '#ff5c8a', '#f5e663', '#7bdff2', '#b8f2a0'];
  var RV_MAX_POINTS = 6000;
  var _rvLogs = [];            // server list: { name, label, started_at, duration_s, rows, recording }
  var _rvSel = {};             // name -> loaded log (see rvBuild)
  var _rvOrder = [];           // selection order (colour assignment, legend order)
  var _rvT = null;             // scrubber time in s; null = end of every log
  var _rvTimer = null;
  var _rvStatus = '';
  var _rvGroup = null;         // THREE.Group holding every log's line / point / markers

  function hexRgb(h) {
    var n = parseInt(String(h).replace('#', ''), 16);
    return [((n >> 16) & 255) / 255, ((n >> 8) & 255) / 255, (n & 255) / 255];
  }

  function rvColorFor(order) { return RV_COLORS[order % RV_COLORS.length]; }

  function rvSaveSel() { storageSet(RV_KEY, JSON.stringify(_rvOrder)); }

  function rvSetStatus(t) {
    _rvStatus = t;
    var el = q('pp-rv-status');
    if (el) el.textContent = t;
  }

  function rvRefresh() {
    var f = window['fetch'];
    if (typeof f !== 'function') { rvSetStatus('fetch unavailable'); return; }
    rvSetStatus('Listing logs…');
    f('/api/rec-logs').then(function (r) {
      if (!r || !r.ok) throw new Error('no log list');
      return r.json();
    }).then(function (d) {
      _rvLogs = (d && d.logs) || [];
      rvSetStatus(_rvLogs.length ? _rvLogs.length + ' saved log' + (_rvLogs.length === 1 ? '' : 's') : 'No saved REC logs yet (press REC to record one)');
      rvRenderList();
    }).catch(function (e) {
      rvSetStatus('Could not list logs: ' + (e && e.message ? e.message : e));
    });
  }

  function fmtWhen(epoch) {
    var d;
    if (!isFinite(epoch)) return '';
    d = new Date(epoch * 1000);
    return d.getFullYear() + '-' + ('0' + (d.getMonth() + 1)).slice(-2) + '-' + ('0' + d.getDate()).slice(-2) +
      ' ' + ('0' + d.getHours()).slice(-2) + ':' + ('0' + d.getMinutes()).slice(-2);
  }

  function rvRenderList() {
    var el = q('pp-rv-list'), html = '', i, l, sel, name;
    if (!el) return;
    for (i = 0; i < _rvLogs.length; i++) {
      l = _rvLogs[i]; name = l.name; sel = _rvSel[name];
      html += '<div class="pp-rv-row">' +
        '<input type="checkbox" data-rv-name="' + escapeText(name) + '"' + (sel ? ' checked' : '') + ' title="Overlay this log">' +
        '<i class="pp-rv-sw" style="background:' + (sel ? sel.color : 'transparent') + '"></i>' +
        '<span class="pp-rv-name" title="' + escapeText(name) + '">' + escapeText(l.label || name) +
        '<small> ' + escapeText(fmtWhen(l.started_at)) + (l.duration_s != null ? ' &middot; ' + fmtNum(l.duration_s, 0) + ' s' : '') +
        (l.recording ? ' &middot; recording' : '') + '</small></span>' +
        '<label class="pp-rv-err" title="Colour this log green/red by error instead of its own colour">' +
        '<input type="checkbox" data-rv-err="' + escapeText(name) + '"' + (sel && sel.byError ? ' checked' : '') + (sel ? '' : ' disabled') + '>err</label>' +
        '</div>';
    }
    el.innerHTML = html;
  }

  // Server samples [t_s, x, y, z, dx, dy, dz, e3, exy] -> panel points (t in ms).
  function rvConvert(data) {
    var pts = [], i, s, evs = [], e;
    for (i = 0; i < (data.samples || []).length; i++) {
      s = data.samples[i];
      pts.push({ t: s[0] * 1000, x: s[1], y: s[2], z: s[3], dx: s[4], dy: s[5], dz: s[6], e3: s[7], exy: s[8] });
    }
    for (i = 0; i < (data.events || []).length; i++) {
      e = data.events[i];
      if (!EVENT_KINDS[e.kind]) continue;
      evs.push({ t: e.t * 1000, kind: e.kind, text: e.text, pos: e.pos ? { x: e.pos[0], y: e.pos[1], z: e.pos[2] } : null });
    }
    return { pts: pts, events: evs };
  }

  function rvToggle(name, on) {
    var i;
    if (!on) {
      rvDispose(name);
      delete _rvSel[name];
      i = _rvOrder.indexOf(name);
      if (i >= 0) _rvOrder.splice(i, 1);
      rvSaveSel(); rvAfterChange();
      return;
    }
    if (_rvSel[name]) return;
    rvLoad(name);
  }

  function rvLoad(name) {
    var f = window['fetch'];
    if (typeof f !== 'function') return;
    var slot = { name: name, label: name, color: rvColorFor(_rvOrder.length), byError: false, pts: [], events: [],
                 tRef: 'rec_start', loading: true, error: null, hasSetpoint: false };
    _rvSel[name] = slot;
    _rvOrder.push(name);
    rvSetStatus('Loading ' + name + '…');
    rvRenderList();
    f('/api/rec-logs/' + encodeURIComponent(name) + '?max_points=' + RV_MAX_POINTS).then(function (r) {
      if (!r || !r.ok) throw new Error(r && r.status === 404 ? 'log no longer exists' : 'load failed');
      return r.json();
    }).then(function (d) {
      var c;
      if (_rvSel[name] !== slot) return;             // deselected while loading
      c = rvConvert(d);
      slot.pts = c.pts; slot.events = c.events; slot.loading = false;
      slot.label = d.label || name; slot.tRef = d.t_ref; slot.hasSetpoint = !!d.has_setpoint;
      slot.truncated = !!d.truncated; slot.nSamples = d.n_samples;
      rvSetStatus(name + ': ' + slot.pts.length + ' points' + (slot.truncated ? ' (thinned from ' + slot.nSamples + ')' : ''));
      rvSaveSel(); rvAfterChange(true);
    }).catch(function (e) {
      if (_rvSel[name] !== slot) return;
      slot.loading = false; slot.error = e && e.message ? e.message : String(e);
      rvSetStatus(name + ': ' + slot.error);
      rvDispose(name); delete _rvSel[name];
      var i = _rvOrder.indexOf(name); if (i >= 0) _rvOrder.splice(i, 1);
      rvSaveSel(); rvAfterChange();
    });
  }

  function rvSetByError(name, on) {
    if (!_rvSel[name]) return;
    _rvSel[name].byError = !!on;
    _rvSel[name].colorKey = null;
    rvAfterChange();
  }

  function rvAfterChange(fit) {
    rvRenderList();
    rvSyncScrub();
    renderReviewLegend();
    if (fit) rvFit();
    if (_threeLoaded) render3D();
  }

  // Scrubber span (s) over the loaded logs.
  function rvRange() {
    var lo = Infinity, hi = -Infinity, i, l;
    for (i = 0; i < _rvOrder.length; i++) {
      l = _rvSel[_rvOrder[i]];
      if (!l || !l.pts.length) continue;
      lo = Math.min(lo, l.pts[0].t / 1000);
      hi = Math.max(hi, l.pts[l.pts.length - 1].t / 1000);
    }
    return lo <= hi ? { min: lo, max: hi } : null;
  }

  function rvSyncScrub() {
    var sc = q('pp-rv-scrub'), r = rvRange(), tl = q('pp-rv-time');
    if (!sc) return;
    if (!r) { sc.min = '0'; sc.max = '0'; sc.value = '0'; if (tl) tl.textContent = '—'; return; }
    sc.min = String(Math.floor(r.min * 10) / 10);
    sc.max = String(Math.ceil(r.max * 10) / 10);
    sc.value = _rvT == null ? sc.max : String(Math.max(r.min, Math.min(r.max, _rvT)));
    if (tl) tl.textContent = _rvT == null ? 'end' : (_rvT >= 0 ? '+' : '') + fmtNum(_rvT, 1) + ' s';
  }

  // Last index with pts[i].t <= tMs, or -1.
  function rvIndexAt(pts, tMs) {
    var lo = 0, hi = pts.length - 1, mid, ans = -1;
    while (lo <= hi) {
      mid = (lo + hi) >> 1;
      if (pts[mid].t <= tMs) { ans = mid; lo = mid + 1; } else hi = mid - 1;
    }
    return ans;
  }

  // Whole-log error statistics for the legend: from path execute when the log
  // has one (the run), otherwise every sample.
  function rvStats(l) {
    return flyStats(l.pts, _fly.thr, errKey(), l.tRef === 'path_execute' ? 0 : null);
  }

  function renderReviewLegend() {
    var el = q('pp-rv-legend'), html, i, l, s;
    if (!el) return;
    if (!_rvOrder.length) { el.innerHTML = '<div class="pp-hint">Select one or more saved logs.</div>'; return; }
    html = '<table class="pp-rv-table"><thead><tr><th></th><th>Log</th><th>RMS</th><th>Max</th><th>&gt;' + fmtNum(_fly.thr, 2) +
      ' m</th></tr></thead><tbody>';
    for (i = 0; i < _rvOrder.length; i++) {
      l = _rvSel[_rvOrder[i]];
      if (!l) continue;
      if (l.loading) { html += '<tr><td><i class="pp-rv-sw" style="background:' + l.color + '"></i></td><td colspan="4">' + escapeText(l.label) + ' …</td></tr>'; continue; }
      s = rvStats(l);
      html += '<tr><td><i class="pp-rv-sw" style="background:' + l.color + '"></i></td>' +
        '<td title="' + escapeText(l.name) + ' (t = 0 at ' + (l.tRef === 'path_execute' ? 'path execute' : 'REC start') + ')">' +
        escapeText(l.label) + '<small> ' + (l.tRef === 'path_execute' ? 'exec' : 'rec') + '</small></td>' +
        (l.hasSetpoint
          ? '<td>' + fmtNum(s.rms, 3) + '</td><td>' + fmtNum(s.max, 3) + '</td><td>' + (s.pctAbove == null ? '—' : fmtNum(s.pctAbove, 0) + ' %') + '</td>'
          : '<td colspan="3" title="This log has no firmware setpoint (.Des) to measure error against">no setpoint</td>') +
        '</tr>';
    }
    html += '</tbody></table><div class="pp-hint">Error metric: ' + (_fly.metric === 'xy' ? 'xy' : '3D') +
      '. RMS and max in m; share of time above the threshold. Stats cover the run from path execute (or the whole log).</div>';
    el.innerHTML = html;
  }

  function rvFit() {
    var all = [], i, l, j;
    if (!_threeCamera || !_threeControls) return;
    for (i = 0; i < _rvOrder.length; i++) {
      l = _rvSel[_rvOrder[i]];
      if (!l) continue;
      for (j = 0; j < l.pts.length; j += Math.max(1, Math.floor(l.pts.length / 400))) {
        all.push({ x: l.pts[j].x - _origin.x, y: l.pts[j].y - _origin.y, z: l.pts[j].z || 0 });
      }
    }
    var pose = pathViewPose(all);
    _threeControls.target.set(pose.target.x, pose.target.y, pose.target.z);
    _threeCamera.position.set(pose.pos.x, pose.pos.y, pose.pos.z);
    _threeCamera.lookAt(_threeControls.target);
    _threeControls.update();
  }

  function onReviewShown() {
    var saved = null, i, names;
    rvRefresh();
    rvSyncScrub();
    renderReviewLegend();
    // Restore last session's selection once.
    if (!_rvOrder.length) {
      try { saved = JSON.parse(storageGet(RV_KEY) || 'null'); } catch (e) { saved = null; }
      if (saved && saved.length) {
        names = saved.slice(0, 8);
        for (i = 0; i < names.length; i++) if (validRvName(names[i])) rvToggle(names[i], true);
      }
    }
  }

  function validRvName(n) { return typeof n === 'string' && /^[A-Za-z0-9_-]{1,80}$/.test(n); }

  // ── Review 3D objects ──────────────────────────────────────────────────
  function rvDispose(name) {
    var l = _rvSel[name], i, o;
    if (!l || !_rvGroup) return;
    ['line', 'point'].forEach(function (k) {
      if (l[k]) {
        _rvGroup.remove(l[k]);
        if (l[k].geometry) l[k].geometry.dispose();
        if (l[k].material) l[k].material.dispose();
        l[k] = null;
      }
    });
    if (l.markers) {
      for (i = 0; i < l.markers.length; i++) {
        o = l.markers[i];
        _rvGroup.remove(o);
        if (o.material) o.material.dispose();
      }
      l.markers = null;
    }
  }

  function rvBuild3D(l) {
    var THREE = _THREE, n = l.pts.length, geo, i, ev, tex, sp;
    if (!THREE || !_rvGroup || !n) return;
    geo = new THREE.BufferGeometry();
    geo.setAttribute('position', new THREE.BufferAttribute(new Float32Array(n * 3), 3));
    geo.setAttribute('color', new THREE.BufferAttribute(new Float32Array(n * 3), 3));
    geo.setDrawRange(0, 0);
    l.line = new THREE.Line(geo, new THREE.LineBasicMaterial({ vertexColors: true }));
    l.line.frustumCulled = false;
    _rvGroup.add(l.line);
    l.point = new THREE.Mesh(new THREE.SphereGeometry(0.02, 14, 10), new THREE.MeshBasicMaterial({ color: 0xffffff }));
    l.point.renderOrder = 9;
    _rvGroup.add(l.point);
    l.markers = [];
    for (i = 0; i < l.events.length; i++) {
      ev = l.events[i];
      if (!ev.pos) continue;
      tex = eventTexture(ev.kind);
      if (!tex) continue;
      sp = new THREE.Sprite(new THREE.SpriteMaterial({ map: tex, depthTest: false, transparent: true }));
      sp.scale.set(0.06, 0.06, 1);
      sp.renderOrder = 10;
      sp.userData = { t: ev.t };
      sp.position.set(ev.pos.x - _origin.x, (ev.pos.z || 0) + 0.03, ev.pos.y - _origin.y);
      _rvGroup.add(sp);
      l.markers.push(sp);
    }
    l.colorKey = null;
    l.builtOrigin = { x: _origin.x, y: _origin.y };
  }

  function rvPaint(l) {
    var key = (l.byError ? 'e' + _fly.thr + errKey() : 'c') + '|' + l.color + '|' + _origin.x + ',' + _origin.y, geo, pos, col, i, p, e, rgb, base, k;
    if (l.colorKey === key || !l.line) return;
    geo = l.line.geometry;
    pos = geo.getAttribute('position'); col = geo.getAttribute('color');
    base = hexRgb(l.color); k = errKey();
    for (i = 0; i < l.pts.length; i++) {
      p = l.pts[i];
      pos.setXYZ(i, p.x - _origin.x, p.z || 0, p.y - _origin.y);
      if (l.byError) {
        e = p[k];
        rgb = e == null ? [0.45, 0.55, 0.75] : (e > _fly.thr ? [1, 0.25, 0.25] : [0.2, 0.85, 0.45]);
      } else rgb = base;
      col.setXYZ(i, rgb[0], rgb[1], rgb[2]);
    }
    pos.needsUpdate = true; col.needsUpdate = true;
    l.colorKey = key;
  }

  // Draw every loaded log up to the scrubber time.
  function rvRender3D() {
    var i, l, idx, tMs, p, j, mk, e;
    if (!_rvGroup) return;
    for (i = 0; i < _rvOrder.length; i++) {
      l = _rvSel[_rvOrder[i]];
      if (!l || l.loading || !l.pts.length) continue;
      if (!l.line) rvBuild3D(l);
      if (!l.line) continue;
      rvPaint(l);
      tMs = _rvT == null ? Infinity : _rvT * 1000;
      idx = _rvT == null ? l.pts.length - 1 : rvIndexAt(l.pts, tMs);
      l.line.geometry.setDrawRange(0, idx + 1);
      l.point.visible = idx >= 0;
      if (idx >= 0) {
        p = l.pts[idx];
        l.point.position.set(p.x - _origin.x, p.z || 0, p.y - _origin.y);
        e = p[errKey()];
        l.point.material.color.setHex(l.byError && e != null ? (e > _fly.thr ? 0xff4040 : 0x4ecca3) : parseInt(l.color.slice(1), 16));
      }
      for (j = 0; j < (l.markers || []).length; j++) {
        mk = l.markers[j];
        mk.visible = mk.userData.t <= tMs;
      }
    }
  }

  function rvPlayToggle() {
    var r, btn = q('pp-rv-play');
    if (_rvTimer) { clearInterval(_rvTimer); _rvTimer = null; if (btn) btn.textContent = '▶'; return; }
    r = rvRange();
    if (!r || typeof setInterval !== 'function') return;
    if (_rvT == null || _rvT >= r.max) _rvT = r.min;
    if (btn) btn.textContent = '❚❚';
    _rvTimer = setInterval(function () {
      var rr = rvRange();
      if (!rr) { rvPlayToggle(); return; }
      _rvT += 0.1;
      if (_rvT >= rr.max) { _rvT = null; rvPlayToggle(); }
      rvSyncScrub();
      if (_threeLoaded) render3D();
    }, 100);
  }

  // ── Build HTML ──────────────────────────────────────────────────────────
  function buildHTML() {
    return [
      '<style>',
      /* Scoped styles */
      '.pp-container { display: grid; grid-template-columns: 1fr 220px; gap: 12px; height: 100%; min-height: 0; }',
      '.pp-canvas-wrap { position: relative; background: #0a0a1a; border-radius: 6px; overflow: hidden; }',
      '.pp-canvas { display: block; width: 100%; height: 100%; min-height: 400px; }',
      '.pp-controls { position: absolute; top: 8px; right: 8px; display: flex; flex-direction: column; gap: 4px; }',
      '.pp-btn { padding: 6px 12px; border-radius: 4px; border: none; cursor: pointer; font-size: 12px; font-weight: 600;',
      '  background: var(--accent); color: var(--text); }',
      '.pp-btn-sm { padding: 4px 8px; font-size: 11px; }',
      '.pp-btn:hover { opacity: 0.85; }',
      '.pp-demo-badge { position: absolute; top: 8px; left: 8px; padding: 4px 8px; border-radius: 4px;',
      '  background: rgba(233,69,96,0.2); color: var(--red); font-size: 10px; font-weight: 600; }',
      '.pp-sidebar { display: flex; flex-direction: column; gap: 10px; overflow-y: scroll; min-height: 0;',
      '  max-height: calc(100vh - 120px); padding-right: 6px; scrollbar-width: thin; scrollbar-color: var(--accent) transparent; }',
      '.pp-sidebar::-webkit-scrollbar { width: 8px; }',
      '.pp-sidebar::-webkit-scrollbar-thumb { background: var(--accent); border-radius: 4px; }',
      '.pp-sidebar::-webkit-scrollbar-track { background: rgba(255,255,255,0.04); border-radius: 4px; }',
      '.pp-metric-wide { grid-column: 1 / -1; }',
      '.pp-exec-ok { color: var(--ok, #3fb950); }',
      '.pp-exec-bad { color: var(--bad, #f85149); }',
      '.pp-metric-hint { font-size: 10px; color: var(--muted); line-height: 1.35; }',
      '.pp-section-label { font-size: 10px; font-weight: 700; color: var(--muted); letter-spacing: 0.08em;',
      '  text-transform: uppercase; margin-bottom: 6px; }',
      '.pp-metrics { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }',
      '.pp-metric { display: flex; flex-direction: column; gap: 2px; background: var(--bg); padding: 8px; border-radius: 4px; }',
      '.pp-metric-label { font-size: 10px; color: var(--muted); }',
      '.pp-metric-value { font-size: 14px; font-weight: 600; font-family: Consolas, monospace; color: var(--green); }',
      '.pp-zoom-row { display: flex; gap: 4px; }',
      '.pp-zoom-btn { flex: 1; padding: 6px; border-radius: 4px; background: var(--bg); color: var(--text);',
      '  border: 1px solid var(--border); cursor: pointer; font-size: 14px; }',
      '.pp-zoom-btn:hover { background: var(--accent); }',
      '.pp-action-row { display: flex; gap: 4px; }',
      '.pp-action-btn { flex: 1; padding: 6px; border-radius: 4px; background: var(--bg); color: var(--muted);',
      '  border: 1px solid var(--border); cursor: pointer; font-size: 10px; }',
      '.pp-action-btn:hover { color: var(--text); border-color: var(--accent); }',
      '.pp-wp-table { width: 100%; border-collapse: collapse; font-size: 11px; }',
      '.pp-wp-table th { text-align: left; font-size: 10px; color: var(--muted); padding: 4px 4px;',
      '  border-bottom: 1px solid var(--border); }',
      '.pp-wp-table td { padding: 3px 4px; }',
      '.pp-wp-input { width: 60px; background: var(--bg); border: 1px solid var(--border); color: var(--text);',
      '  border-radius: 3px; padding: 2px 4px; font-size: 11px; font-family: Consolas, monospace; }',
      '.pp-wp-input:focus { outline: none; border-color: var(--accent); }',
      '.pp-wp-del { background: none; border: none; color: var(--red); cursor: pointer; font-size: 12px; padding: 2px 4px; }',
      '.pp-wp-del:hover { color: var(--amber); }',
      '.pp-legend { display: flex; flex-direction: column; gap: 6px; font-size: 11px; }',
      '.pp-legend-item { display: flex; align-items: center; gap: 8px; }',
      '.pp-legend-dot { width: 10px; height: 10px; border-radius: 50%; }',
      '.pp-legend-line { width: 20px; height: 2px; }',
      '.pp-entry-row { display: flex; gap: 4px; align-items: center; margin-bottom: 4px; flex-wrap: wrap; }',
      '.pp-entry-row .pp-wp-input { width: 44px; }',
      '.pp-main { display: flex; flex-direction: column; min-width: 0; min-height: 0; }',
      '.pp-main > .pp-canvas-wrap { flex: 1; min-height: 400px; }',
      '.pp-view-toggle { display: flex; gap: 2px; margin-bottom: 8px; }',
      '.pp-view-btn { flex: 1; padding: 5px 8px; border-radius: 4px; background: var(--bg); color: var(--muted); border: 1px solid var(--border); cursor: pointer; font-size: 11px; font-weight: 600; }',
      '.pp-view-btn.active { background: var(--accent); color: var(--text); }',
      '.pp-3d-wrap { position: relative; background: #0a0a1a; border-radius: 6px; overflow: hidden; }',
      '.pp-3d-canvas { display: block; width: 100%; height: 560px; }',
      '.pp-3d-controls { position: absolute; bottom: 8px; left: 8px; display: flex; gap: 4px; flex-wrap: wrap; }',
      '.pp-3d-btn { padding: 4px 8px; border-radius: 4px; background: rgba(20,20,40,0.85); color: var(--text); border: 1px solid var(--border); cursor: pointer; font-size: 10px; }',
      '.pp-3d-btn:hover { background: var(--accent); }',
      '.pp-3d-btn.active { background: var(--accent); }',
      '.pp-3d-legend { position: absolute; top: 8px; left: 8px; padding: 6px 10px; border-radius: 4px; background: rgba(10,10,26,0.85); color: var(--text); font-size: 11px; }',
      '.pp-3d-legend-item { display: flex; align-items: center; gap: 6px; margin: 2px 0; }',
      '.pp-3d-legend-line { width: 20px; height: 2px; }',
      '.pp-3d-legend-dash { width: 20px; height: 0; border-top: 2px dashed #ffaa00; }',
      '.pp-3d-follow-row { display: flex; align-items: center; gap: 4px; font-size: 10px; color: var(--muted); margin-top: 4px; }',
      '.pp-3d-follow-toggle { width: 32px; height: 16px; border-radius: 8px; background: #333; border: 1px solid #555; cursor: pointer; position: relative; transition: background 0.2s; }',
      '.pp-3d-follow-toggle.on { background: var(--accent); }',
      '.pp-3d-follow-toggle::after { content: \'\'; width: 12px; height: 12px; border-radius: 50%; background: #fff; position: absolute; top: 1px; left: 1px; transition: left 0.2s; }',
      '.pp-3d-follow-toggle.on::after { left: 17px; }',
      '.pp-room-in { width: 44px; padding: 2px 3px; font-size: 10px; background: rgba(20,20,40,0.85); color: var(--text); border: 1px solid var(--border); border-radius: 3px; }',
      '.pp-hint { font-size: 10px; color: var(--muted); margin-top: 4px; line-height: 1.4; }',
      /* Mode bar and Fly mode */
      '.pp-mode-bar { display: flex; gap: 2px; margin-bottom: 8px; align-items: center; }',
      '.pp-mode-btn { padding: 6px 16px; border-radius: 4px; background: var(--bg); color: var(--muted); border: 1px solid var(--border); cursor: pointer; font-size: 12px; font-weight: 700; }',
      '.pp-mode-btn.active { background: var(--accent); color: var(--text); }',
      '.pp-mode-spacer { flex: 1; }',
      '.pp-fly-strip { display: grid; grid-template-columns: repeat(7, minmax(0, 1fr)); gap: 6px; margin-bottom: 6px; }',
      '.pp-fly-cell { display: flex; flex-direction: column; gap: 2px; background: var(--bg); padding: 6px 8px; border-radius: 4px; min-width: 0; }',
      '.pp-fly-label { font-size: 10px; color: var(--muted); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }',
      '.pp-fly-value { font-size: 16px; font-weight: 700; font-family: Consolas, monospace; color: var(--green); white-space: nowrap; }',
      '.pp-fly-row { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; margin-bottom: 6px; font-size: 11px; color: var(--muted); }',
      '.pp-fly-row input[type=range] { width: 160px; }',
      '.pp-fly-row select { background: var(--bg); color: var(--text); border: 1px solid var(--border); border-radius: 3px; padding: 2px 4px; font-size: 11px; }',
      '.pp-fly-legend { display: flex; gap: 12px; flex-wrap: wrap; font-size: 10px; color: var(--muted); margin-bottom: 4px; }',
      '.pp-fly-key i { display: inline-block; width: 14px; height: 3px; margin-right: 4px; vertical-align: middle; }',
      '.pp-fly-events { font-size: 11px; font-family: Consolas, monospace; max-height: 96px; overflow-y: auto; margin-top: 6px; }',
      '.pp-fly-ev-t { color: var(--muted); }',
      '.pp-fly-ev-none { color: var(--muted); }',
      '.pp-mode-fly .pp-3d-canvas { height: calc(100vh - 300px); min-height: 380px; }',
      '.pp-mode-review .pp-3d-canvas { height: calc(100vh - 220px); min-height: 380px; }',
      '.pp-mode-fly.pp-container { grid-template-columns: 1fr; }',
      '.pp-mode-review.pp-container { grid-template-columns: 1fr 300px; }',
      '.pp-rv-row { display: flex; align-items: center; gap: 6px; font-size: 11px; padding: 3px 0; border-bottom: 1px solid var(--border); }',
      '.pp-rv-name { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }',
      '.pp-rv-name small, .pp-rv-table small { color: var(--muted); font-size: 9px; }',
      '.pp-rv-sw { display: inline-block; width: 12px; height: 12px; border-radius: 2px; border: 1px solid var(--border); flex: none; }',
      '.pp-rv-err { font-size: 10px; color: var(--muted); white-space: nowrap; }',
      '.pp-rv-list { max-height: 220px; overflow-y: auto; }',
      '.pp-rv-table { width: 100%; border-collapse: collapse; font-size: 11px; font-family: Consolas, monospace; }',
      '.pp-rv-table th { text-align: left; font-size: 10px; color: var(--muted); padding: 3px 4px; border-bottom: 1px solid var(--border); }',
      '.pp-rv-table td { padding: 3px 4px; }',
      '</style>',

      '<div id="pp-container" class="pp-container pp-mode-plan">',

      /* Main column: mode bar, view toggle, then the 2D or 3D view (one grid cell, so the sidebar keeps its column) */
      '<div class="pp-main">',
      '<div class="pp-mode-bar">',
      '<button id="pp-mode-plan" class="pp-mode-btn active" title="Planning tools: waypoints, presets, draw, execute">Plan</button>',
      '<button id="pp-mode-fly" class="pp-mode-btn" title="Fly: 3D view fills the panel, error-coloured trail, event markers">Fly</button>',
      '<button id="pp-mode-review" class="pp-mode-btn" title="Review: overlay saved REC logs">Review</button>',
      '</div>',
      /* Fly bar: strip, controls, legend, events (Fly mode only) */
      '<div id="pp-fly-bar" style="display:none">',
      '<div id="pp-fly-strip" class="pp-fly-strip"></div>',
      '<div class="pp-fly-row">',
      '<span>Error threshold</span>',
      '<input id="pp-fly-thr" type="range" min="0.01" max="1" step="0.01" value="0.10" title="Trail is red above this error (starting value 0.10 m, not measured)">',
      '<span id="pp-fly-thr-val">0.10 m</span>',
      '<span>Metric</span>',
      '<select id="pp-fly-metric" title="3D: x, y and z error. xy: horizontal error only"><option value="3d">3D</option><option value="xy">xy</option></select>',
      '<span>Trail</span>',
      '<select id="pp-fly-trail" title="Whole flight or fading trail (T)"><option value="whole">Whole flight</option><option value="fading">Fading</option></select>',
      '<button id="pp-fly-fit" class="pp-3d-btn" title="Refit the room camera (F)">Fit</button>',
      '<button id="pp-fly-clear" class="pp-3d-btn" title="Clear the flown trail and events">Clear trail</button>',
      '</div>',
      '<div id="pp-fly-legend" class="pp-fly-legend"></div>',
      '<div id="pp-fly-hint" class="pp-hint">Keys: R REC (press twice to stop) &middot; N note &middot; F fit &middot; T trail &middot; 1 Top &middot; 2 Side &middot; 3 Front &middot; 4 Iso. UI only, never a flight command.</div>',
      '</div>',
      '<div id="pp-view-toggle" class="pp-view-toggle">',
      '<button id="pp-view-2d" class="pp-view-btn active">2D</button>',
      '<button id="pp-view-3d" class="pp-view-btn">3D</button>',
      '</div>',
      '<div id="pp-2d-wrap" class="pp-canvas-wrap">',
      '<canvas id="pp-canvas" class="pp-canvas"></canvas>',
      '<div id="pp-demo-badge" class="pp-demo-badge">No position data</div>',
      '<div class="pp-controls">',
      '<button id="pp-zoom-in" class="pp-btn pp-btn-sm">+</button>',
      '<button id="pp-zoom-out" class="pp-btn pp-btn-sm">&#8722;</button>',
      '<button id="pp-reset-view" class="pp-btn pp-btn-sm">&#8634;</button>',
      '</div>',
      '</div>',

      /* 3D view */
      '<div id="pp-3d-wrap" class="pp-3d-wrap" style="display:none">',
      '<canvas id="pp-3d-canvas" class="pp-3d-canvas"></canvas>',
      '<div class="pp-3d-controls">',
      '<button id="pp-3d-top" class="pp-3d-btn" title="Top view (XZ)">Top</button>',
      '<button id="pp-3d-side" class="pp-3d-btn" title="Side view">Side</button>',
      '<button id="pp-3d-front" class="pp-3d-btn" title="Front view">Front</button>',
      '<button id="pp-3d-iso" class="pp-3d-btn active" title="Isometric view">Iso</button>',
      '<button id="pp-3d-fit" class="pp-3d-btn" title="Frame the flown and planned paths">Fit path</button>',
      '<button id="pp-3d-reset" class="pp-3d-btn" title="Reset view">Reset</button>',
      '<button id="pp-3d-clear" class="pp-3d-btn" title="Clear the flown and desired trails">Clear trail</button>',
      '</div>',
      '<div id="pp-3d-legend" class="pp-3d-legend">',
      '<div class="pp-3d-legend-item"><div class="pp-3d-legend-line" style="background:#4a9eff"></div>Actual path</div>',
      '<div class="pp-3d-legend-item"><div class="pp-3d-legend-dash"></div>Desired path</div>',
      '<div class="pp-3d-legend-item"><div class="pp-3d-legend-line" style="background:#ff4040"></div>Outside room</div>',
      '<div id="pp-3d-error" class="pp-3d-legend-item" style="margin-top:4px;color:#4a9eff">Error: — m</div>',
      '<div id="pp-3d-oob" class="pp-3d-legend-item">Outside room: 0 / 0 pts</div>',
      '</div>',
      '<div id="pp-3d-topright" class="pp-3d-controls" style="bottom:auto;top:8px;left:auto;right:8px">',
      '<div class="pp-3d-follow-row"><span>Follow drone</span><div id="pp-3d-follow-toggle" class="pp-3d-follow-toggle" role="switch" aria-checked="false" title="Toggle follow drone"></div></div>',
      '<div class="pp-3d-follow-row" title="Flight room W x D x H in metres, centred on the origin, floor at z = 0">',
      '<span>Room m</span>',
      '<input id="pp-room-w" class="pp-room-in" type="number" step="0.1" min="0.2" max="50">',
      '<span>&#215;</span><input id="pp-room-d" class="pp-room-in" type="number" step="0.1" min="0.2" max="50">',
      '<span>&#215;</span><input id="pp-room-h" class="pp-room-in" type="number" step="0.1" min="0.2" max="20">',
      '</div>',
      '</div>',
      '</div>',
      '<div id="pp-fly-events" class="pp-fly-events" style="display:none"></div>',
      '</div>',

      /* Sidebar: Plan tools, or the Review tools; hidden in Fly mode */
      '<div id="pp-sidebar" class="pp-sidebar">',
      '<div id="pp-tools-plan">',

      '<div>',
      '<div class="pp-section-label">Path Metrics</div>',
      '<div id="pp-metrics" class="pp-metrics"></div>',
      '</div>',

      '<div>',
      '<div class="pp-section-label">Zoom & View</div>',
      '<div class="pp-zoom-row">',
      '<button id="pp-zoom-in-2" class="pp-zoom-btn">Zoom In</button>',
      '<button id="pp-zoom-out-2" class="pp-zoom-btn">Zoom Out</button>',
      '</div>',
      '<div class="pp-action-row" style="margin-top:4px">',
      '<button id="pp-set-home" class="pp-action-btn" title="Home = current position">Set Home = cur</button>',
      '<button id="pp-set-target" class="pp-action-btn" title="Target = current position">Set Target = cur</button>',
      '</div>',
      '<div class="pp-action-row" style="margin-top:4px">',
      '<button id="pp-set-origin" class="pp-action-btn" title="Show the current position as x = y = 0 (display only)">Set origin here</button>',
      '<button id="pp-clear-origin" class="pp-action-btn" title="Back to the firmware origin">Reset origin</button>',
      '</div>',
      '<div id="pp-origin-status" class="pp-hint"></div>',
      '</div>',

      '<div>',
      '<div class="pp-section-label">Waypoints</div>',
      '<div id="pp-wp-table"></div>',
      '<div class="pp-entry-row">',
      '<input id="pp-wp-x" class="pp-wp-input" placeholder="x" type="number" step="0.1">',
      '<input id="pp-wp-y" class="pp-wp-input" placeholder="y" type="number" step="0.1">',
      '<input id="pp-wp-z" class="pp-wp-input" placeholder="z" type="number" step="0.1">',
      '<button id="pp-add-manual-wp" class="pp-btn pp-btn-sm" title="Append waypoint (x,y,z)">+ Add (x,y,z)</button>',
      '</div>',
      '</div>',

      '<div>',
      '<div class="pp-section-label">Home / Target entry</div>',
      '<div class="pp-entry-row">',
      '<input id="pp-home-x" class="pp-wp-input" placeholder="hx" type="number" step="0.1">',
      '<input id="pp-home-y" class="pp-wp-input" placeholder="hy" type="number" step="0.1">',
      '<input id="pp-home-z" class="pp-wp-input" placeholder="hz" type="number" step="0.1">',
      '<button id="pp-set-home-manual" class="pp-action-btn" title="Set home from fields">Set Home</button>',
      '</div>',
      '<div class="pp-entry-row" style="margin-top:4px">',
      '<input id="pp-target-x" class="pp-wp-input" placeholder="tx" type="number" step="0.1">',
      '<input id="pp-target-y" class="pp-wp-input" placeholder="ty" type="number" step="0.1">',
      '<input id="pp-target-z" class="pp-wp-input" placeholder="tz" type="number" step="0.1">',
      '<button id="pp-set-target-manual" class="pp-action-btn" title="Set target from fields">Set Target</button>',
      '</div>',
      '</div>',

      '<div>',
      '<div class="pp-section-label">Room presets</div>',
      '<div class="pp-entry-row">',
      '<select id="pp-preset-kind" class="pp-wp-input" title="Preset shape">',
      '<option value="hover">Hover</option><option value="line">Line</option>',
      '<option value="square">Square</option><option value="circle" selected>Circle</option>',
      '<option value="figure8">Figure-8</option><option value="helix">Helix</option>',
      '</select>',
      '<input id="pp-preset-size" class="pp-wp-input" value="0.4" type="number" min="0" step="0.05" title="Size: radius / half-width (m)">',
      '<input id="pp-preset-alt" class="pp-wp-input" value="0.5" type="number" min="0" step="0.05" title="Altitude (m)">',
      '<button id="pp-preset-load" class="pp-btn pp-btn-sm" title="Replace the waypoint list with this preset">Load</button>',
      '</div>',
      '<div class="pp-hint">Shape, size&nbsp;(m), centre altitude&nbsp;(m), on the plane below. Scaled to stay 0.15&nbsp;m inside the room.</div>',
      '</div>',

      '<div>',
      '<div class="pp-section-label">Path plane</div>',
      '<div class="pp-entry-row">',
      '<select id="pp-plane-kind" class="pp-wp-input" title="Plane for presets and drawing">',
      '<option value="xy" selected>XY (floor)</option><option value="xz">XZ (wall)</option><option value="yz">YZ (wall)</option>',
      '</select>',
      '<input id="pp-plane-tilt" class="pp-wp-input" value="0" type="number" min="-80" max="80" step="5" title="Tilt about the plane\'s first axis (deg)">',
      '<input id="pp-plane-spacing" class="pp-wp-input" value="0.02" type="number" min="0.005" max="0.5" step="0.005" title="Point spacing (m)">',
      '</div>',
      '<div class="pp-hint">Plane, tilt&nbsp;(deg), point spacing&nbsp;(m). Applies to presets and drawing; the draw slider moves the plane along its normal.</div>',
      '</div>',

      '<div>',
      '<div class="pp-section-label">Execute on drone</div>',
      '<div class="pp-entry-row">',
      '<input id="pp-exec-speed" class="pp-wp-input" value="0.2" type="number" min="0.05" max="1" step="0.05" title="Path speed (m/s)">',
      '<input id="pp-exec-dur" class="pp-wp-input" value="20" type="number" min="1" max="300" step="1" title="Duration (s); the firmware stops the path after it">',
      '</div>',
      '<div class="pp-entry-row">',
      '<button id="pp-exec-go" class="pp-btn pp-btn-sm" title="Send the selected preset to the firmware (SDK mode only)">Execute preset</button>',
      '<button id="pp-exec-stop" class="pp-btn pp-btn-sm" title="Deactivate sinusoid, circle and figure-8 paths">Stop path</button>',
      '</div>',
      '<div class="pp-hint">Speed&nbsp;(m/s), duration&nbsp;(s). Uses the preset kind, size, altitude and the world origin. Flyable: hover, line, circle, figure-8 (xy plane, no tilt). SDK mode only; never arms.</div>',
      '<div id="pp-exec-status" class="pp-hint">idle</div>',
      '</div>',

      '<div>',
      '<div class="pp-section-label">Draw path</div>',
      '<div class="pp-entry-row">',
      '<button id="pp-draw-toggle" class="pp-btn pp-btn-sm" title="Drag on the 2D or 3D view to add waypoints">Draw</button>',
      '<button id="pp-draw-undo" class="pp-btn pp-btn-sm" title="Undo last stroke">Undo</button>',
      '<button id="pp-draw-clear" class="pp-btn pp-btn-sm" title="Clear all waypoints (undoable)">Clear</button>',
      '<button id="pp-fit-room" class="pp-btn pp-btn-sm" title="Frame the room in the 2D view">Fit room</button>',
      '</div>',
      '<div class="pp-entry-row">',
      '<input id="pp-draw-alt" type="range" min="0" max="1" step="0.05" value="0.5" title="Draw plane offset (m)" style="flex:1">',
      '<span id="pp-draw-alt-val" class="pp-hint">0.50 m</span>',
      '</div>',
      '<div class="pp-hint">Drag to draw on the path plane; points at the set spacing, clamped to the room. Wall planes: draw in 3D. Orbit is paused while drawing.</div>',
      '</div>',

      '<div>',
      '<div class="pp-section-label">Saved paths</div>',
      '<div class="pp-entry-row">',
      '<input id="pp-lib-name" class="pp-wp-input" type="text" placeholder="name" maxlength="60" style="flex:1" title="Path name (same name overwrites)">',
      '<button id="pp-lib-save" class="pp-btn pp-btn-sm" title="Save the waypoint list in this browser">Save</button>',
      '</div>',
      '<div class="pp-entry-row">',
      '<select id="pp-lib-select" class="pp-wp-input" style="flex:1" title="Saved paths"></select>',
      '<button id="pp-lib-load" class="pp-btn pp-btn-sm" title="Replace the waypoint list (undoable)">Load</button>',
      '<button id="pp-lib-delete" class="pp-btn pp-btn-sm" title="Remove from this browser">Del</button>',
      '</div>',
      '<div class="pp-entry-row">',
      '<button id="pp-lib-export" class="pp-btn pp-btn-sm" title="Download as JSON">Export</button>',
      '<button id="pp-lib-import" class="pp-btn pp-btn-sm" title="Load a JSON path file">Import</button>',
      '<input id="pp-lib-file" type="file" accept=".json,application/json" style="display:none">',
      '</div>',
      '<div id="pp-lib-status" class="pp-hint">Paths are stored in this browser; Export for a file copy.</div>',
      '</div>',

      '<div>',
      '<div class="pp-section-label">Random path generator</div>',
      '<div class="pp-entry-row">',
      '<input id="pp-rand-n" class="pp-wp-input" value="6" type="number" min="2" step="1" title="Waypoints">',
      '<input id="pp-rand-spacing" class="pp-wp-input" value="0.3" type="number" min="0.05" step="0.05" title="Spacing (m)">',
      '<input id="pp-rand-box" class="pp-wp-input" value="0.5" type="number" min="0.1" step="0.1" title="Bounding box +/- m">',
      '<input id="pp-rand-z" class="pp-wp-input" value="0.5" type="number" step="0.1" title="Planned altitude (m)">',
      '</div>',
      '<div class="pp-entry-row" style="margin-top:4px">',
      '<button id="pp-rand-gen" class="pp-btn pp-btn-sm" title="Fill waypoint list with a random bounded path">Generate Random Path</button>',
      '</div>',
      '<div class="pp-hint">Options: points, spacing&nbsp;(m), &#177;box&nbsp;(m), altitude&nbsp;(m).</div>',
      '</div>',

       '<div>',
       '<div class="pp-section-label">Session data</div>',
       '<div id="pp-session-status" class="pp-demo-badge" style="position:static;cursor:default">No session loaded</div>',
       '<button id="pp-load-session" class="pp-btn pp-btn-sm" style="margin-top:4px">Load last session</button>',
       '</div>',

       '<div>',
       '<div class="pp-section-label">Playback &amp; Scrub</div>',
       '<div class="pp-entry-row" style="align-items:center;gap:8px">',
       '<input id="pp-replay-scrub" type="range" min="0" max="100" value="100" style="flex:1" title="Scrub recording / trail">',
       '<span id="pp-replay-time" style="font-family:monospace;font-size:11px;min-width:45px;color:#aaa">Live</span>',
       '</div>',
       '</div>',

       '<div>',
       '<div class="pp-section-label">Legend</div>',
       '<div class="pp-legend">',
       '<div class="pp-legend-item"><div class="pp-legend-dot" style="background:#4a9eff"></div>Current Position</div>',
       '<div class="pp-legend-item"><div class="pp-legend-dot" style="background:#4ecca3"></div>Home</div>',
       '<div class="pp-legend-item"><div class="pp-legend-dot" style="background:#e94560"></div>Target</div>',
       '<div class="pp-legend-item"><div class="pp-legend-dot" style="background:#f5a623"></div>Waypoints</div>',
       '<div class="pp-legend-item"><div class="pp-legend-line" style="background:rgba(74,158,255,0.6)"></div>Actual Trail</div>',
       '<div class="pp-legend-item"><div class="pp-legend-line" style="background:#ff9f43;border-top:1px dashed #ff9f43"></div>Desired Trace</div>',
       '</div>',
       '</div>',
      '</div>',   /* end #pp-tools-plan */

      '<div id="pp-tools-review" style="display:none">',
      '<div>',
      '<div class="pp-section-label">Saved REC logs</div>',
      '<div class="pp-entry-row">',
      '<button id="pp-rv-refresh" class="pp-btn pp-btn-sm" title="Re-read the list of saved REC sessions">Refresh</button>',
      '<button id="pp-rv-fit" class="pp-btn pp-btn-sm" title="Frame the selected logs">Fit</button>',
      '</div>',
      '<div id="pp-rv-status" class="pp-hint">Open Review to list saved logs.</div>',
      '<div id="pp-rv-list" class="pp-rv-list"></div>',
      '</div>',
      '<div>',
      '<div class="pp-section-label">Scrubber</div>',
      '<div class="pp-entry-row" style="align-items:center;gap:8px">',
      '<button id="pp-rv-play" class="pp-btn pp-btn-sm" title="Play / pause">&#9654;</button>',
      '<input id="pp-rv-scrub" type="range" min="0" max="0" step="0.1" value="0" style="flex:1" title="Shared time: 0 = path execute (REC start when a log has none)">',
      '<span id="pp-rv-time" style="font-family:monospace;font-size:11px;min-width:52px;color:#aaa">—</span>',
      '</div>',
      '</div>',
      '<div>',
      '<div class="pp-section-label">Legend</div>',
      '<div id="pp-rv-legend"></div>',
      '</div>',
      '<div>',
      '<div class="pp-section-label">Error metric</div>',
      '<div class="pp-entry-row">',
      '<select id="pp-rv-metric" class="pp-wp-input" title="3D: x, y and z error. xy: horizontal only"><option value="3d">3D</option><option value="xy">xy</option></select>',
      '</div>',
      '</div>',
      '<div>',
      '<div class="pp-section-label">Error threshold</div>',
      '<div class="pp-entry-row">',
      '<input id="pp-rv-thr" type="range" min="0.01" max="1" step="0.01" value="0.10" style="flex:1" title="Shared with Fly mode">',
      '<span id="pp-rv-thr-val" class="pp-hint">0.10 m</span>',
      '</div>',
      '</div>',
      '</div>',

      '</div>',
      '</div>'
    ].join('');
  }

  // ── Init ────────────────────────────────────────────────────────────────
  window.__PLUGIN_INIT__ = function (api) {
    _api = api;
    api.registerPanel('Path Planning', function (container) {
      container.innerHTML = buildHTML();

      _canvas = q('pp-canvas');
      _ctx = _canvas.getContext('2d');
      _el3DCanvas = q('pp-3d-canvas');
      _elMetrics = q('pp-metrics');
      _elWaypointTable = q('pp-wp-table');
      _elDemoIndicator = q('pp-demo-badge');

      // Size canvas
      // The bitmap must track the wrap's real size. A hidden tab reports 0,
      // and tab/sidebar changes fire no window resize, so CSS would stretch a
      // stale bitmap into smeared slices.
      function resizeCanvas() {
        var wrap = _canvas.parentElement;
        if (!wrap || wrap.offsetWidth === 0) return;
        _canvas.width = wrap.offsetWidth;
        _canvas.height = Math.max(400, wrap.offsetHeight);
        render();
      }

      // Initial setup
      resizeCanvas();
      window.addEventListener('resize', resizeCanvas);
      if (typeof ResizeObserver !== 'undefined') {
        new ResizeObserver(resizeCanvas).observe(_canvas.parentElement);
        new ResizeObserver(function () {
          if (!_el3DCanvas) return;
          if (_threeRenderer && _threeCamera) {
            var w3 = _el3DCanvas.clientWidth;
            var h3 = _el3DCanvas.clientHeight;
            if (w3 > 0 && h3 > 0) {
              _threeCamera.aspect = w3 / h3;
              _threeCamera.updateProjectionMatrix();
              _threeRenderer.setSize(w3, h3);
              if (_mode === 'fly') fitFlyCamera();   // keep the whole room in frame
              render3D();
            }
          }
        }).observe(document.getElementById('pp-3d-wrap'));
      }

      _waypoints = [];
      updatePlannedPath();
      renderWaypointTable();
      renderMetrics();

      // Zoom controls
      q('pp-zoom-in').addEventListener('click', zoomIn);
      q('pp-zoom-out').addEventListener('click', zoomOut);
      q('pp-reset-view').addEventListener('click', resetView);
      q('pp-zoom-in-2').addEventListener('click', zoomIn);
      q('pp-zoom-out-2').addEventListener('click', zoomOut);

      // Home/Target buttons
      q('pp-set-home').addEventListener('click', setHomeHere);
      q('pp-set-origin').addEventListener('click', setOriginHere);
      q('pp-clear-origin').addEventListener('click', clearOrigin);
      renderOriginStatus();
      q('pp-set-target').addEventListener('click', setTargetHere);
      q('pp-set-home-manual').addEventListener('click', setHomeManual);
      q('pp-set-target-manual').addEventListener('click', setTargetManual);
      q('pp-add-manual-wp').addEventListener('click', addManualWaypoint);
      q('pp-rand-gen').addEventListener('click', generateRandomPath);
      q('pp-preset-load').addEventListener('click', loadPresetPath);
      q('pp-exec-go').addEventListener('click', executePath);
      q('pp-exec-stop').addEventListener('click', stopPath);
      q('pp-draw-toggle').addEventListener('click', function () { setDrawMode(!_drawMode); });
      q('pp-draw-undo').addEventListener('click', drawUndo);
      q('pp-draw-clear').addEventListener('click', drawClear);
      q('pp-fit-room').addEventListener('click', fitRoom2D);
      q('pp-lib-save').addEventListener('click', libSaveCurrent);
      q('pp-lib-load').addEventListener('click', libLoadSelected);
      q('pp-lib-delete').addEventListener('click', libDeleteSelected);
      q('pp-lib-export').addEventListener('click', libExport);
      q('pp-lib-import').addEventListener('click', function () { q('pp-lib-file').click(); });
      q('pp-lib-file').addEventListener('change', function () {
        libImportFile(this.files && this.files[0]);
        this.value = '';
      });
      renderLibrarySelect();
      q('pp-draw-alt').addEventListener('input', function () { setDrawAlt(parseFloat(this.value)); });
      q('pp-plane-kind').addEventListener('change', function () { setPlane(this.value); });
      q('pp-plane-tilt').addEventListener('change', function () { setPlane(null, parseFloat(this.value)); });
      q('pp-plane-spacing').addEventListener('change', function () { setPlane(null, NaN, parseFloat(this.value)); });
      syncDrawAltInput();
      bindDrawCanvas(_canvas, pick2D);
      bindDrawCanvas(_el3DCanvas, pick3D);

      // 2D/3D toggle
      var _v2d = q('pp-view-2d');
      var _v3d = q('pp-view-3d');
      if (_v2d) _v2d.addEventListener('click', function () { setView('2d'); });
      if (_v3d) _v3d.addEventListener('click', function () { setView('3d'); });

      // Mode bar (Plan / Fly / Review) and Fly controls
      MODES.forEach(function (m) {
        var b = q('pp-mode-' + m);
        if (b) b.addEventListener('click', function () { setMode(m); });
      });
      var flyThr = q('pp-fly-thr');
      if (flyThr) flyThr.addEventListener('input', function () { setFly({ thr: parseFloat(this.value) }); });
      var flyMetric = q('pp-fly-metric');
      if (flyMetric) flyMetric.addEventListener('change', function () { setFly({ metric: this.value }); });
      var flyTrail = q('pp-fly-trail');
      if (flyTrail) flyTrail.addEventListener('change', function () { setFly({ trail: this.value }); });
      var rvThr = q('pp-rv-thr');
      if (rvThr) rvThr.addEventListener('input', function () { setFly({ thr: parseFloat(this.value) }); });
      var rvMetric = q('pp-rv-metric');
      if (rvMetric) rvMetric.addEventListener('change', function () { setFly({ metric: this.value }); });
      var rvRef = q('pp-rv-refresh');
      if (rvRef) rvRef.addEventListener('click', rvRefresh);
      var rvFitBtn = q('pp-rv-fit');
      if (rvFitBtn) rvFitBtn.addEventListener('click', rvFit);
      var rvPlay = q('pp-rv-play');
      if (rvPlay) rvPlay.addEventListener('click', rvPlayToggle);
      var rvScrub = q('pp-rv-scrub');
      if (rvScrub) rvScrub.addEventListener('input', function () {
        _rvT = parseFloat(this.value) >= parseFloat(this.max) ? null : parseFloat(this.value);
        rvSyncScrub();
        if (_threeLoaded) render3D();
      });
      var rvList = q('pp-rv-list');
      if (rvList) rvList.addEventListener('change', function (ev) {
        var t = ev && ev.target, nm;
        if (!t || typeof t.getAttribute !== 'function') return;
        nm = t.getAttribute('data-rv-name');
        if (nm) { rvToggle(nm, !!t.checked); return; }
        nm = t.getAttribute('data-rv-err');
        if (nm) rvSetByError(nm, !!t.checked);
      });
      var flyFit = q('pp-fly-fit');
      if (flyFit) flyFit.addEventListener('click', fitFlyCamera);
      var flyClear = q('pp-fly-clear');
      if (flyClear) flyClear.addEventListener('click', function () { clear3D(); });
      if (typeof document.addEventListener === 'function') {
        _keyHandler = onFlyKey;
        document.addEventListener('keydown', _keyHandler);
      }
      syncFlyInputs();
      renderFlyStrip();
      renderFlyEvents();

      // 3D controls
      var _3dr = q('pp-3d-reset');
      if (_3dr) _3dr.addEventListener('click', reset3DView);
      var _3dfit = q('pp-3d-fit');
      if (_3dfit) _3dfit.addEventListener('click', fitPath3D);
      var _3dt = q('pp-3d-top');
      if (_3dt) _3dt.addEventListener('click', function () { presetView('top'); });
      var _3ds = q('pp-3d-side');
      if (_3ds) _3ds.addEventListener('click', function () { presetView('side'); });
      var _3df = q('pp-3d-front');
      if (_3df) _3df.addEventListener('click', function () { presetView('front'); });
      var _3di = q('pp-3d-iso');
      if (_3di) _3di.addEventListener('click', function () { presetView('iso'); });
      var _3dc = q('pp-3d-clear');
      if (_3dc) _3dc.addEventListener('click', function () { clear3D(); });

      // Room size inputs
      var roomIds = ['pp-room-w', 'pp-room-d', 'pp-room-h'];
      var syncRoomInputs = function () {
        var vals = [_room.w, _room.d, _room.h];
        for (var ri = 0; ri < 3; ri++) {
          var inp = q(roomIds[ri]);
          if (inp) inp.value = String(vals[ri]);
        }
      };
      var onRoomChange = function () {
        var v = roomIds.map(function (id) { var el = q(id); return el ? parseFloat(el.value) : NaN; });
        if (!setRoom({ w: v[0], d: v[1], h: v[2] })) syncRoomInputs();
      };
      roomIds.forEach(function (id) { var el = q(id); if (el) el.addEventListener('change', onRoomChange); });
      syncRoomInputs();

      // Follow drone toggle
      var followToggle = q('pp-3d-follow-toggle');
      if (followToggle) {
        followToggle.addEventListener('click', function () {
          _threeFollowDrone = !_threeFollowDrone;
          followToggle.classList.toggle('on', _threeFollowDrone);
          followToggle.setAttribute('aria-checked', String(_threeFollowDrone));
        });
      }

      // Load session button
      var _sload = q('pp-load-session');
      if (_sload) _sload.addEventListener('click', function () {
        loadSessionData();
      });

      // Scrubber input
      var scrubEl = q('pp-replay-scrub');
      var timeEl = q('pp-replay-time');
      if (scrubEl) {
        scrubEl.addEventListener('input', function () {
          var val = parseInt(scrubEl.value, 10);
          var max = parseInt(scrubEl.max, 10);
          if (val >= max || max === 0) {
            _scrubIndex = null;
            if (timeEl) timeEl.textContent = 'Live';
          } else {
            _scrubIndex = val;
            if (timeEl) timeEl.textContent = (val + 1) + '/' + (max + 1);
          }
          updateMetrics();
          render();
          renderMetrics();
          if (_threeLoaded) render3D();
        });
      }

      // Subscribe to live state for Frame C position and desired position
      api.subscribe(function (state) {
        if (!state) return;
        var sdkNow = sdkFrom(state), yawNow = holdYawFrom(state);
        if (sdkNow != null) _sdk = sdkNow;
        if (yawNow != null) _holdYaw = yawNow;
        checkExec(state);

        var pos = extractPosition(state);
        var des = extractDesiredPosition(state);
        var flySt = extractFlyStatus(state);
        detectEvents(flySt, Date.now());

        if (pos || des) {
          var frameTime = (pos && pos.t !== undefined) ? pos.t : ((des && des.t !== undefined) ? des.t : nextTimestamp());
          if (pos) {
            var perr = errorBetween(pos, des);
            if (perr) { pos.e3 = perr.e3; pos.exy = perr.exy; }
            addPoint(pos, frameTime);
          }
          if (des) {
            addDesiredPoint(des, frameTime);
          }
          if (_scrubIndex === null) {
            var sc = q('pp-replay-scrub');
            if (sc && _trajectory.length > 0) {
              sc.min = 0;
              sc.max = Math.max(0, _trajectory.length - 1);
              sc.value = sc.max;
            }
          }
          render();
          renderMetrics();
          if (_mode === 'fly') renderFlyStrip();
          // Update 3D if visible
          var threeWrap = q('pp-3d-wrap');
          if (_threeLoaded && threeWrap && threeWrap.style.display !== 'none') {
            render3D();
          }
        }
      });

      // Initial render
      render();
      applyModeLayout();
    });
  };

  window.__PLUGIN_DESTROY__ = function () {
    _trajectory = [];
    _desiredTrajectory = [];
    _desiredPath = [];
    _waypoints = [];
    _plannedPath = [];
    _currentPos = null;
    _homePos = null;
    _targetPos = null;
    _zoom = 1.0;
    _panX = 0;
    _panY = 0;
    _autoFitted = false;
    _scrubIndex = null;
    _lastTs = 0;
    _trackingMetrics = { count: 0, rms: null, max: null, rmsX: null, rmsY: null, rmsZ: null };
    dispose3D();
    _threeFollowDrone = false;
    _trackingError = 0;
    stopNotesPoll();
    if (_rvTimer) { clearInterval(_rvTimer); _rvTimer = null; }
    _rvSel = {}; _rvOrder = []; _rvLogs = []; _rvT = null;
    if (_keyHandler && typeof document !== 'undefined' && typeof document.removeEventListener === 'function') {
      document.removeEventListener('keydown', _keyHandler);
    }
    _keyHandler = null;
    _events = [];
    _runT0 = null;
    _lastPathEvt = { dir: null, t: 0 };
    _flyStatus = { mode: null, adapt: null, vbat: null, twc: null };
    _notesSeq = 0;
  };

  // Pure helpers exposed for the node harness (no DOM or WebGL needed).
  window.__pathPanelTest = {
    roomBounds: roomBounds, isOutOfRoom: isOutOfRoom, countOutOfRoom: countOutOfRoom,
    roomViewPose: roomViewPose, validRoom: validRoom, pathViewPose: pathViewPose, actualSegColor: actualSegColor,
    smoothPolyline: smoothPolyline, SMOOTH_STEP: SMOOTH_STEP,
    planeFrame: planeFrame, resamplePath: resamplePath, setPlane: setPlane, planeFromTop: planeFromTop,
    getPlane: function () { return { kind: _plane.kind, tilt: _plane.tilt, spacing: _plane.spacing, off: _drawAlt }; },
    renderLiveMetrics: renderLiveMetrics, rxRate: rxRate, path3DLength: path3DLength,
    executePlan: executePlan, STOP_STEPS: STOP_STEPS, checkExec: checkExec,
    getExec: function () { return _exec; }, getSdk: function () { return _sdk; }, getHoldYaw: function () { return _holdYaw; },
    getTrailLengths: function () { return { actual: _trajectory.length, desired: _desiredTrajectory.length }; },
    presetPath: presetPath, PRESET_KINDS: PRESET_KINDS, PRESET_MARGIN: PRESET_MARGIN,
    serializePath: serializePath, parsePathFile: parsePathFile, libraryPut: libraryPut, LIB_MAX: LIB_MAX,
    shiftPoints: shiftPoints, validOrigin: validOrigin,
    getOrigin: function () { return { x: _origin.x, y: _origin.y }; },
    getCurrentPos: function () { return _currentPos && { x: _currentPos.x, y: _currentPos.y }; },
    clampToRoom: clampToRoom, thinAppend: thinAppend, DRAW_MIN_STEP: DRAW_MIN_STEP,
    screenToWorld: screenToWorld, worldToScreen: worldToScreen, roomFitZoom: roomFitZoom,
    getRoom: function () { return { w: _room.w, d: _room.d, h: _room.h }; },
    // Fly mode
    errorBetween: errorBetween, flyStats: flyStats, flyColorFn: flyColorFn, fmtRunTime: fmtRunTime,
    extractFlyStatus: extractFlyStatus, extractPosition: extractPosition, extractDesiredPosition: extractDesiredPosition,
    detectEvents: detectEvents, pushEvent: pushEvent, pathEvent: pathEvent, applyNotes: applyNotes,
    isPathExecuteCmd: isPathExecuteCmd, isPathStopCmd: isPathStopCmd, onFlyKey: onFlyKey,
    setMode: setMode, getMode: function () { return _mode; }, setFly: setFly,
    getFly: function () { return { thr: _fly.thr, metric: _fly.metric, trail: _fly.trail }; },
    getEvents: function () { return _events.slice(); }, getRunT0: function () { return _runT0; },
    getTrajectory: function () { return _trajectory.slice(); }, replayDesired: replayDesired,
    EVENT_KINDS: EVENT_KINDS, FLY_DEFAULT: FLY_DEFAULT,
    // Review mode
    rvConvert: rvConvert, rvToggle: rvToggle, rvSetByError: rvSetByError, rvRange: rvRange, rvIndexAt: rvIndexAt,
    rvStats: rvStats, rvRefresh: rvRefresh, getRv: function () { return { order: _rvOrder.slice(), sel: _rvSel, t: _rvT }; },
    RV_COLORS: RV_COLORS
  };

  window.__registerPlugin__('Path Planning', window.__PLUGIN_INIT__, window.__PLUGIN_DESTROY__);

})();
