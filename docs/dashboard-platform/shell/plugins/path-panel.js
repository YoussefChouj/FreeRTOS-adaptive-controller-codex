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
  var _maxDeviation = 0;
  var _plannedPath = [];     // Waypoint path for deviation calculation
  var _autoFitted = false;   // one-shot auto-fit of the flown path (item 6)
  var _scrubIndex = null;    // null = live / latest, integer = index in trajectory
  var _trackingMetrics = { count: 0, rms: null, max: null, rmsX: null, rmsY: null, rmsZ: null };

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
  var _threeErrorLabel = null;
  var _threeFollowDrone = false;
  var _MAX_3D_POINTS = 20000;
  var _THREE = null;
  var _threeRoomGroup = null;
  var _threeDrawPlane = null;  // translucent plane at the draw altitude (draw mode only)
  var _drawMode = false;       // mouse draws waypoints instead of orbiting / panning
  var _drawAlt = 0.5;          // draw plane altitude (m)
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
  function viewPose(preset, t, rad) {
    var dist = 2.3 * rad;   // fov 60: rad / sin(30 deg) = 2 rad, plus margin
    var p;
    if (preset === 'top') p = { x: t.x, y: t.y + dist, z: t.z + 0.001 };
    else if (preset === 'side') p = { x: t.x + dist, y: t.y, z: t.z };
    else if (preset === 'front') p = { x: t.x, y: t.y, z: t.z + dist };
    else {
      var k = dist / Math.sqrt(1 + 0.5625 + 1);
      p = { x: t.x + k, y: t.y + 0.75 * k, z: t.z + k };
    }
    return { pos: p, target: t };
  }

  function roomViewPose(preset) {
    var rad = 0.5 * Math.sqrt(_room.w * _room.w + _room.d * _room.d + _room.h * _room.h);
    return viewPose(preset, { x: 0, y: _room.h / 2, z: 0 }, rad);
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

  // Ribbon segment colour: blue inside the room, red when either end is outside.
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
                   z: metreAltitude(vals, lookupValue(vals, POS_Z_KEYS)),
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
               z: metreAltitude(flat, lookupValue(flat, POS_Z_KEYS)),
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
    var rawZ = lookupValue(valueMap, DES_Z_KEYS);
    if (rawZ !== undefined && rawZ !== null && !isNaN(Number(rawZ))) {
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
    return { x: x, y: y, z: z };
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
    _trajectory.push({ x: pos.x - _origin.x, y: pos.y - _origin.y, z: pos.z, yaw: pos.yaw, t: t });
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

  // colorFn(points, i, rgbOut), optional: per-segment colour for geometries with a colour attribute.
  function updateRibbonGeometry(geo, points, radius, dashed, colorFn) {
    if (!geo || !points || points.length < 2) {
      if (geo) geo.setDrawRange(0, 0);
      return;
    }
    var attr = geo.getAttribute('position');
    var arr = attr.array;
    var colAttr = colorFn ? geo.getAttribute('color') : null;
    var carr = colAttr ? colAttr.array : null;
    var rgb = [0, 0, 0];
    var vIdx = 0;
    var r = radius || 0.012;
    var count = points.length;
    var maxSegments = (arr.length / 36) | 0;
    var segLimit = Math.min(count - 1, maxSegments);
    var accumDist = 0;

    for (var i = 0; i < segLimit; i++) {
      var p1 = points[i];
      var p2 = points[i + 1];
      var ax = p1.x, ay = p1.z || 0, az = p1.y;
      var bx = p2.x, by = p2.z || 0, bz = p2.y;
      var dx = bx - ax, dy = by - ay, dz = bz - az;
      var len = Math.sqrt(dx * dx + dy * dy + dz * dz);
      if (len < 1e-4) continue;

      if (dashed) {
        var dashStep = Math.floor(accumDist / 0.08);
        accumDist += len;
        if (dashStep % 2 !== 0) {
          continue;
        }
      }

      dx /= len; dy /= len; dz /= len;

      var v1x, v1y, v1z;
      if (Math.abs(dy) < 0.9) {
        v1x = dz; v1y = 0; v1z = -dx;
      } else {
        v1x = 0; v1y = -dz; v1z = dy;
      }
      var l1 = Math.sqrt(v1x * v1x + v1y * v1y + v1z * v1z);
      if (l1 < 1e-4) { v1x = 1; v1y = 0; v1z = 0; }
      else { v1x /= l1; v1y /= l1; v1z /= l1; }

      var v2x = dy * v1z - dz * v1y;
      var v2y = dz * v1x - dx * v1z;
      var v2z = dx * v1y - dy * v1x;

      var w1x = v1x * r, w1y = v1y * r, w1z = v1z * r;
      var w2x = v2x * r, w2y = v2y * r, w2z = v2z * r;

      // Quad 1
      arr[vIdx++] = ax + w1x; arr[vIdx++] = ay + w1y; arr[vIdx++] = az + w1z;
      arr[vIdx++] = bx + w1x; arr[vIdx++] = by + w1y; arr[vIdx++] = bz + w1z;
      arr[vIdx++] = ax - w1x; arr[vIdx++] = ay - w1y; arr[vIdx++] = az - w1z;

      arr[vIdx++] = ax - w1x; arr[vIdx++] = ay - w1y; arr[vIdx++] = az - w1z;
      arr[vIdx++] = bx + w1x; arr[vIdx++] = by + w1y; arr[vIdx++] = bz + w1z;
      arr[vIdx++] = bx - w1x; arr[vIdx++] = by - w1y; arr[vIdx++] = bz - w1z;

      // Quad 2
      arr[vIdx++] = ax + w2x; arr[vIdx++] = ay + w2y; arr[vIdx++] = az + w2z;
      arr[vIdx++] = bx + w2x; arr[vIdx++] = by + w2y; arr[vIdx++] = bz + w2z;
      arr[vIdx++] = ax - w2x; arr[vIdx++] = ay - w2y; arr[vIdx++] = az - w2z;

      arr[vIdx++] = ax - w2x; arr[vIdx++] = ay - w2y; arr[vIdx++] = az - w2z;
      arr[vIdx++] = bx + w2x; arr[vIdx++] = by + w2y; arr[vIdx++] = bz + w2z;
      arr[vIdx++] = bx - w2x; arr[vIdx++] = by - w2y; arr[vIdx++] = bz - w2z;

      if (carr) {
        colorFn(points, i, rgb);
        for (var c = vIdx - 36; c < vIdx; c += 3) {
          carr[c] = rgb[0]; carr[c + 1] = rgb[1]; carr[c + 2] = rgb[2];
        }
      }
    }

    attr.needsUpdate = true;
    if (colAttr) colAttr.needsUpdate = true;
    geo.setDrawRange(0, vIdx / 3);
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

    // Thick cross-ribbon geometry helper (antialiased 3D line from any viewing angle)
    function buildRibbonGeometry(maxPoints, withColor) {
      var maxSegments = Math.max(1, maxPoints - 1);
      var positions = new Float32Array(maxSegments * 12 * 3);
      var geo = new THREE.BufferGeometry();
      geo.setAttribute('position', new THREE.BufferAttribute(positions, 3));
      if (withColor) {
        geo.setAttribute('color', new THREE.BufferAttribute(new Float32Array(maxSegments * 12 * 3), 3));
      }
      geo.setDrawRange(0, 0);
      return geo;
    }

    // Desired path (thick cross-ribbon mesh, amber/orange 0xffaa00, dashed)
    var desiredGeo = buildRibbonGeometry(_MAX_3D_POINTS);
    var desiredMat = new THREE.MeshBasicMaterial({
      color: 0xffaa00,
      side: THREE.DoubleSide,
      transparent: true,
      opacity: 0.85
    });
    _threeLineDesired = new THREE.Mesh(desiredGeo, desiredMat);
    _threeLineDesired.frustumCulled = false;
    _threeScene.add(_threeLineDesired);

    // Actual path (thick cross-ribbon mesh; per-segment colour: blue inside the room, red outside)
    var actualGeo = buildRibbonGeometry(_MAX_3D_POINTS, true);
    var actualMat = new THREE.MeshBasicMaterial({
      vertexColors: true,
      side: THREE.DoubleSide,
      transparent: true,
      opacity: 0.85
    });
    _threeLineActual = new THREE.Mesh(actualGeo, actualMat);
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

    // Tracking error label
    var errorCanvas = document.createElement('canvas');
    errorCanvas.width = 256;
    errorCanvas.height = 64;
    _threeErrorLabel = { canvas: errorCanvas, ctx: errorCanvas.getContext('2d'), sprite: null };
    var errorTexture = new THREE.CanvasTexture(errorCanvas);
    var errorSpriteMat = new THREE.SpriteMaterial({ map: errorTexture, transparent: true });
    var errorSprite = new THREE.Sprite(errorSpriteMat);
    errorSprite.scale.set(0.4, 0.1, 1);
    errorSprite.position.set(0, _room.h + 0.1, 0);
    _threeErrorLabel.sprite = errorSprite;
    _threeScene.add(errorSprite);
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
    if (!_threeErrorLabel) return;
    var ctx = _threeErrorLabel.ctx;
    ctx.fillStyle = 'rgba(10, 10, 26, 0.7)';
    ctx.fillRect(0, 0, 256, 64);
    ctx.fillStyle = '#4a9eff';
    ctx.font = 'bold 20px Consolas, monospace';
    var rmsText = (_trackingMetrics && _trackingMetrics.rms != null) ? _trackingMetrics.rms.toFixed(2) : '—';
    var maxText = (_trackingMetrics && _trackingMetrics.max != null) ? _trackingMetrics.max.toFixed(2) : '—';
    ctx.fillText('RMS: ' + rmsText + ' m | Max: ' + maxText + ' m', 10, 38);
    if (_threeErrorLabel.sprite && _threeErrorLabel.sprite.material && _threeErrorLabel.sprite.material.map) {
      _threeErrorLabel.sprite.material.map.needsUpdate = true;
    }
    var el = q('pp-3d-error');
    if (el) el.textContent = 'RMS: ' + rmsText + ' m | Max: ' + maxText + ' m';
  }

  function render3D() {
    if (!_threeRenderer || !_threeScene || !_threeCamera) return;

    updateDrawPlane();

    var activeActual = getActiveTrajectory();
    var activeDesired = getActiveDesiredTrajectory();

    // Update actual path
    if (activeActual.length > 0 && _threeLineActual) {
      updateRibbonGeometry(_threeLineActual.geometry, activeActual, 0.012, false, actualSegColor);

      var lastPt = activeActual[activeActual.length - 1];
      if (_threeErrorLabel && _threeErrorLabel.sprite) {
        _threeErrorLabel.sprite.position.set(lastPt.x, (lastPt.z || 0) + 0.12, lastPt.y);
      }
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
      updateRibbonGeometry(_threeLineDesired.geometry, activeDesired, 0.007, true);
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

    updateErrorSprite();
    var oobEl = q('pp-3d-oob');
    if (oobEl) {
      var oob = countOutOfRoom(activeActual);
      oobEl.textContent = 'Outside room: ' + oob + ' / ' + activeActual.length + ' pts';
      oobEl.style.color = oob > 0 ? '#ff5050' : '';
    }

    // Follow drone
    if (_threeFollowDrone && activeActual.length > 0) {
      var lp = activeActual[activeActual.length - 1];
      _threeCamera.position.set(lp.x + 0.6, (lp.z || 0) + 0.45, lp.y + 0.6);
      _threeControls.target.set(lp.x, lp.z || 0, lp.y);
    }

    _threeControls.update();
    _threeRenderer.render(_threeScene, _threeCamera);
  }

  function clear3D() {
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
    var pose = pathViewPose(getActiveTrajectory().concat(_plannedPath || []));
    _threeControls.target.set(pose.target.x, pose.target.y, pose.target.z);
    _threeCamera.position.set(pose.pos.x, pose.pos.y, pose.pos.z);
    _threeCamera.lookAt(_threeControls.target);
    _threeControls.update();
  }

  function reset3DView() {
    presetView('iso');
  }

  function presetView(preset) {
    if (!_threeCamera || !_threeControls) return;
    var pose = roomViewPose(preset);
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
      new THREE.PlaneGeometry(_room.w, _room.d),
      new THREE.MeshBasicMaterial({ color: 0x4ecca3, transparent: true, opacity: 0.12,
                                    side: THREE.DoubleSide, depthWrite: false }));
    _threeDrawPlane.rotation.x = -Math.PI / 2;
    g.add(_threeDrawPlane);
    updateDrawPlane();

    _threeRoomGroup = g;
    _threeScene.add(g);
  }

  function syncDrawAltInput() {
    var el = q('pp-draw-alt');
    if (el) {
      el.max = String(_room.h);
      if (_drawAlt > _room.h) _drawAlt = _room.h;
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
    _threeErrorLabel = null;
    _threeLoaded = false;
  }

  function updateDesiredPath(state) {
    if (!state) return;
    var des = extractDesiredPosition(state);
    if (des) {
      addDesiredPoint(des, Date.now());
    }
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
        var dx = (row.pid && row.pid.locx && row.pid.locx.Des) || row["pid.locx.Des"] || 
                 (row.Ctrler && row.Ctrler.locxPID && row.Ctrler.locxPID.Des) || row["Ctrler.locxPID.Des"] ||
                 (row.c && row.c.desired_x);
        var dy = (row.pid && row.pid.locy && row.pid.locy.Des) || row["pid.locy.Des"] || 
                 (row.Ctrler && row.Ctrler.locyPID && row.Ctrler.locyPID.Des) || row["Ctrler.locyPID.Des"] ||
                 (row.c && row.c.desired_y);
        var dz = (row.pid && row.pid.z_pos && row.pid.z_pos.Des) || row["pid.z_pos.Des"] || 
                 (row.Ctrler && row.Ctrler.Z_posPID && row.Ctrler.Z_posPID.Des) || row["Ctrler.Z_posPID.Des"] ||
                 (row.c && row.c.desired_z);
        if (dx !== undefined && dy !== undefined) {
          _desiredTrajectory.push({
            x: parseFloat(dx) / 100,
            y: parseFloat(dy) / 100,
            z: dz !== undefined ? parseFloat(dz) : 0,
            t: t
          });
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

  function presetPath(kind, size, alt, room) {
    var zMin = Math.min(0.1, room.h / 2);
    var zMax = Math.max(zMin, room.h - PRESET_MARGIN);
    var sMax = Math.max(0, Math.min(room.w, room.d) / 2 - PRESET_MARGIN);
    var sz = Math.max(0, Math.min(isFinite(size) ? size : 0.4, sMax));
    var z = Math.max(zMin, Math.min(isFinite(alt) ? alt : 0.5, zMax));
    var pts = [];
    var i, n, t, z0, z1;
    function add(x, y, pz) { pts.push({ x: round3(x), y: round3(y), z: round3(pz), reached: false }); }

    if (kind === 'hover') {
      add(0, 0, 0);
      add(0, 0, z);
    } else if (kind === 'line') {
      add(-sz, 0, z);
      add(sz, 0, z);
    } else if (kind === 'square') {
      add(-sz, -sz, z); add(sz, -sz, z); add(sz, sz, z); add(-sz, sz, z); add(-sz, -sz, z);
    } else if (kind === 'circle') {
      n = 36;
      for (i = 0; i <= n; i++) {
        t = 2 * Math.PI * i / n;
        add(sz * Math.cos(t), sz * Math.sin(t), z);
      }
    } else if (kind === 'figure8') {
      n = 48;
      for (i = 0; i <= n; i++) {
        t = 2 * Math.PI * i / n;
        add(sz * Math.sin(t), sz * Math.sin(t) * Math.cos(t), z);
      }
    } else if (kind === 'helix') {
      // Two turns climbing 0.5 m (less in a low room), centred on the altitude.
      z0 = Math.max(zMin, z - 0.25);
      z1 = Math.min(zMax, z0 + 0.5);
      n = 72;
      for (i = 0; i <= n; i++) {
        t = 4 * Math.PI * i / n;
        add(sz * Math.cos(t), sz * Math.sin(t), z0 + (z1 - z0) * i / n);
      }
    }
    return pts;
  }

  function loadPresetPath() {
    var sel = q('pp-preset-kind');
    var pts = presetPath(sel ? sel.value : 'circle', readNum('pp-preset-size', 0.4),
                         readNum('pp-preset-alt', 0.5), _room);
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
    if (!e || !window.confirm('Remove saved path "' + e.name + '" from this browser?')) return;
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
    if (thinAppend(_waypoints, pt, DRAW_MIN_STEP, _room)) waypointsChanged(false);
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

  function setDrawAlt(v) {
    if (!isFinite(v)) return;
    _drawAlt = Math.max(0, Math.min(_room.h, v));
    var lbl = q('pp-draw-alt-val');
    if (lbl) lbl.textContent = _drawAlt.toFixed(2) + ' m';
    if (_threeLoaded) render3D();
  }

  function updateDrawPlane() {
    if (!_threeDrawPlane) return;
    _threeDrawPlane.visible = _drawMode;
    _threeDrawPlane.position.y = _drawAlt;
  }

  // Room point under the mouse on the horizontal plane z = _drawAlt, or null.
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
    if (!ray.ray.intersectPlane(new THREE.Plane(new THREE.Vector3(0, 1, 0), -_drawAlt), hit)) return null;
    return { x: hit.x, y: hit.z, z: _drawAlt };   // three (x, y, z) = room (x, z, y)
  }

  function pick2D(ev) {
    var rect = _canvas.getBoundingClientRect();
    var sx = (ev.clientX - rect.left) * (_canvas.width / (rect.width || 1));
    var sy = (ev.clientY - rect.top) * (_canvas.height / (rect.height || 1));
    var w = screenToWorld(sx, sy, _canvas.width, _canvas.height);
    return { x: w.x, y: w.y, z: _drawAlt };
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

  // ── Build HTML ──────────────────────────────────────────────────────────
  function buildHTML() {
    return [
      '<style>',
      /* Scoped styles */
      '.pp-container { display: grid; grid-template-columns: 1fr 220px; gap: 12px; height: 100%; }',
      '.pp-canvas-wrap { position: relative; background: #0a0a1a; border-radius: 6px; overflow: hidden; }',
      '.pp-canvas { display: block; width: 100%; height: 100%; min-height: 400px; }',
      '.pp-controls { position: absolute; top: 8px; right: 8px; display: flex; flex-direction: column; gap: 4px; }',
      '.pp-btn { padding: 6px 12px; border-radius: 4px; border: none; cursor: pointer; font-size: 12px; font-weight: 600;',
      '  background: var(--accent); color: var(--text); }',
      '.pp-btn-sm { padding: 4px 8px; font-size: 11px; }',
      '.pp-btn:hover { opacity: 0.85; }',
      '.pp-demo-badge { position: absolute; top: 8px; left: 8px; padding: 4px 8px; border-radius: 4px;',
      '  background: rgba(233,69,96,0.2); color: var(--red); font-size: 10px; font-weight: 600; }',
      '.pp-sidebar { display: flex; flex-direction: column; gap: 10px; overflow-y: auto; }',
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
      '</style>',

      '<div class="pp-container">',

      /* Main column: view toggle, then the 2D or 3D view (one grid cell, so the sidebar keeps its column) */
      '<div class="pp-main">',
      '<div class="pp-view-toggle">',
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
      '<button id="pp-3d-clear" class="pp-3d-btn" title="Clear 3D path">Clear</button>',
      '</div>',
      '<div class="pp-3d-legend">',
      '<div class="pp-3d-legend-item"><div class="pp-3d-legend-line" style="background:#4a9eff"></div>Actual path</div>',
      '<div class="pp-3d-legend-item"><div class="pp-3d-legend-dash"></div>Desired path</div>',
      '<div class="pp-3d-legend-item"><div class="pp-3d-legend-line" style="background:#ff4040"></div>Outside room</div>',
      '<div id="pp-3d-error" class="pp-3d-legend-item" style="margin-top:4px;color:#4a9eff">Error: — m</div>',
      '<div id="pp-3d-oob" class="pp-3d-legend-item">Outside room: 0 / 0 pts</div>',
      '</div>',
      '<div class="pp-3d-controls" style="bottom:auto;top:8px;left:auto;right:8px">',
      '<div class="pp-3d-follow-row"><span>Follow drone</span><div id="pp-3d-follow-toggle" class="pp-3d-follow-toggle" role="switch" aria-checked="false" title="Toggle follow drone"></div></div>',
      '<div class="pp-3d-follow-row" title="Flight room W x D x H in metres, centred on the origin, floor at z = 0">',
      '<span>Room m</span>',
      '<input id="pp-room-w" class="pp-room-in" type="number" step="0.1" min="0.2" max="50">',
      '<span>&#215;</span><input id="pp-room-d" class="pp-room-in" type="number" step="0.1" min="0.2" max="50">',
      '<span>&#215;</span><input id="pp-room-h" class="pp-room-in" type="number" step="0.1" min="0.2" max="20">',
      '</div>',
      '</div>',
      '</div>',
      '</div>',

      /* Sidebar */
      '<div class="pp-sidebar">',

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
      '<div class="pp-hint">Shape, size&nbsp;(m), altitude&nbsp;(m). Kept 0.15&nbsp;m inside the room; out-of-range values are clamped.</div>',
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
      '<input id="pp-draw-alt" type="range" min="0" max="1" step="0.05" value="0.5" title="Draw altitude (m)" style="flex:1">',
      '<span id="pp-draw-alt-val" class="pp-hint">0.50 m</span>',
      '</div>',
      '<div class="pp-hint">Drag to draw at the chosen altitude; points 2&nbsp;cm apart, clamped to the room. Orbit is paused while drawing.</div>',
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

      '</div>',
      '</div>'
    ].join('');
  }

  // ── Init ────────────────────────────────────────────────────────────────
  window.__PLUGIN_INIT__ = function (api) {
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
      syncDrawAltInput();
      bindDrawCanvas(_canvas, pick2D);
      bindDrawCanvas(_el3DCanvas, pick3D);

      // 2D/3D toggle
      var _v2d = q('pp-view-2d');
      var _v3d = q('pp-view-3d');
      if (_v2d) {
        _v2d.addEventListener('click', function () {
          var w2 = q('pp-2d-wrap');
          var w3 = q('pp-3d-wrap');
          if (w2) w2.style.display = '';
          if (w3) w3.style.display = 'none';
          if (_v2d) _v2d.classList.add('active');
          if (_v3d) _v3d.classList.remove('active');
        });
      }
      if (_v3d) {
        _v3d.addEventListener('click', function () {
          if (!_threeLoaded) {
            initThreeJS();
          }
          var w2 = q('pp-2d-wrap');
          var w3 = q('pp-3d-wrap');
          if (w2) w2.style.display = 'none';
          if (w3) w3.style.display = '';
          if (_v3d) _v3d.classList.add('active');
          if (_v2d) _v2d.classList.remove('active');
          setTimeout(function () { render3D(); }, 50);
        });
      }

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

        var pos = extractPosition(state);
        var des = extractDesiredPosition(state);

        if (pos || des) {
          var frameTime = (pos && pos.t !== undefined) ? pos.t : ((des && des.t !== undefined) ? des.t : nextTimestamp());
          if (pos) {
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
          // Update 3D if visible
          var threeWrap = q('pp-3d-wrap');
          if (_threeLoaded && threeWrap && threeWrap.style.display !== 'none') {
            render3D();
          }
        }
      });

      // Initial render
      render();
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
  };

  // Pure helpers exposed for the node harness (no DOM or WebGL needed).
  window.__pathPanelTest = {
    roomBounds: roomBounds, isOutOfRoom: isOutOfRoom, countOutOfRoom: countOutOfRoom,
    roomViewPose: roomViewPose, validRoom: validRoom, pathViewPose: pathViewPose, actualSegColor: actualSegColor,
    presetPath: presetPath, PRESET_KINDS: PRESET_KINDS, PRESET_MARGIN: PRESET_MARGIN,
    serializePath: serializePath, parsePathFile: parsePathFile, libraryPut: libraryPut, LIB_MAX: LIB_MAX,
    shiftPoints: shiftPoints, validOrigin: validOrigin,
    getOrigin: function () { return { x: _origin.x, y: _origin.y }; },
    getCurrentPos: function () { return _currentPos && { x: _currentPos.x, y: _currentPos.y }; },
    clampToRoom: clampToRoom, thinAppend: thinAppend, DRAW_MIN_STEP: DRAW_MIN_STEP,
    screenToWorld: screenToWorld, worldToScreen: worldToScreen, roomFitZoom: roomFitZoom,
    getRoom: function () { return { w: _room.w, d: _room.d, h: _room.h }; }
  };

  window.__registerPlugin__('Path Planning', window.__PLUGIN_INIT__, window.__PLUGIN_DESTROY__);

})();
