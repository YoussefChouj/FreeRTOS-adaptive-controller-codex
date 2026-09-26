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
  var _threeDroneMarker = null;
  var _threeAxes = null;
  var _threeGrid = null;
  var _threeErrorLabel = null;
  var _threeFollowDrone = false;
  var _MAX_3D_POINTS = 20000;
  var _desiredPath = [];
  var _trackingError = 0;


  var MAX_TRAIL = 20000;
  var MIN_ZOOM = 0.2;
  var MAX_ZOOM = 5.0;
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
    _trajectory.push({ x: pos.x, y: pos.y, z: pos.z, yaw: pos.yaw, t: t });
    if (_trajectory.length > MAX_TRAIL) {
      _trajectory.shift();
    }

    // Update current position
    _currentPos = { x: pos.x, y: pos.y, z: pos.z == null ? null : pos.z, yaw: pos.yaw || 0 };

    // Recalculate metrics
    updateMetrics();
  }

  function addDesiredPoint(des, optTime) {
    if (!des || des.x == null || des.y == null) return;
    var t = des.t !== undefined ? des.t : (optTime !== undefined ? optTime : nextTimestamp());
    _desiredTrajectory.push({ x: des.x, y: des.y, z: des.z == null ? 0 : des.z, t: t });
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

    if (!_currentPos) {
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

    // Grid
    drawGrid(W, H);

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

    // Current position
    drawMarker(_currentPos.x, _currentPos.y, '', '#4a9eff', 8);

    // Axes labels
    _ctx.fillStyle = 'rgba(255,255,255,0.3)';
    _ctx.font = '10px Consolas, monospace';
    _ctx.fillText('X', W - 15, H - 8);
    _ctx.fillText('Y', 8, 15);
  }

  function drawGrid(W, H) {
    var gridSize = 50 * _zoom;
    var offsetX = (W / 2 + _panX) % gridSize;
    var offsetY = (H / 2 + _panY) % gridSize;

    _ctx.strokeStyle = 'rgba(255,255,255,0.08)';
    _ctx.lineWidth = 1;

    // Vertical lines
    for (var x = offsetX; x < W; x += gridSize) {
      _ctx.beginPath();
      _ctx.moveTo(x, 0);
      _ctx.lineTo(x, H);
      _ctx.stroke();
    }

    // Horizontal lines
    for (var y = offsetY; y < H; y += gridSize) {
      _ctx.beginPath();
      _ctx.moveTo(0, y);
      _ctx.lineTo(W, y);
      _ctx.stroke();
    }

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

  function initThreeScene(THREE, OrbitControls) {
    var canvas = _el3DCanvas;
    if (!canvas) return;

    var w = canvas.clientWidth || 600;
    var h = canvas.clientHeight || 400;

    _threeScene = new THREE.Scene();
    _threeScene.background = new THREE.Color(0x0a0a1a);

    _threeCamera = new THREE.PerspectiveCamera(60, w / h, 0.1, 10000);
    _threeCamera.position.set(80, 60, 80);
    _threeCamera.lookAt(0, 0, 0);

    _threeRenderer = new THREE.WebGLRenderer({ canvas: canvas, antialias: true });
    _threeRenderer.setSize(w, h);
    _threeRenderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));

    _threeControls = new OrbitControls(_threeCamera, _threeRenderer.domElement);
    _threeControls.enableDamping = true;
    _threeControls.dampingFactor = 0.08;
    _threeControls.screenSpacePanning = true;
    _threeControls.minDistance = 5;
    _threeControls.maxDistance = 2000;

    var ambient = new THREE.AmbientLight(0x404060, 1.5);
    _threeScene.add(ambient);
    var dirLight = new THREE.DirectionalLight(0xffffff, 2);
    dirLight.position.set(50, 100, 50);
    _threeScene.add(dirLight);

    // Ground grid
    _threeGrid = new THREE.GridHelper(200, 20, 0x333355, 0x222244);
    _threeScene.add(_threeGrid);

    // XYZ axes
    _threeAxes = new THREE.AxesHelper(100);
    _threeScene.add(_threeAxes);

    // Thick cross-ribbon geometry helper (antialiased 3D line from any viewing angle)
    function buildRibbonGeometry(maxPoints) {
      var maxSegments = Math.max(1, maxPoints - 1);
      var positions = new Float32Array(maxSegments * 12 * 3);
      var geo = new THREE.BufferGeometry();
      geo.setAttribute('position', new THREE.BufferAttribute(positions, 3));
      geo.setDrawRange(0, 0);
      return geo;
    }

    function updateRibbonGeometry(geo, points, radius, dashed) {
      if (!geo || !points || points.length < 2) {
        if (geo) geo.setDrawRange(0, 0);
        return;
      }
      var attr = geo.getAttribute('position');
      var arr = attr.array;
      var vIdx = 0;
      var r = radius || 0.08;
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
          var dashStep = Math.floor(accumDist / 0.5);
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
      }

      attr.needsUpdate = true;
      geo.setDrawRange(0, vIdx / 3);
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
    _threeScene.add(_threeLineDesired);

    // Actual path (thick cross-ribbon mesh, solid cyan/blue 0x4a9eff)
    var actualGeo = buildRibbonGeometry(_MAX_3D_POINTS);
    var actualMat = new THREE.MeshBasicMaterial({
      color: 0x4a9eff,
      side: THREE.DoubleSide,
      transparent: true,
      opacity: 0.85
    });
    _threeLineActual = new THREE.Mesh(actualGeo, actualMat);
    _threeScene.add(_threeLineActual);

    // Planned path overlay (hand-placed waypoints preview)
    var planPositions = new Float32Array(_MAX_3D_POINTS * 3);
    var planGeo = new THREE.BufferGeometry();
    planGeo.setAttribute('position', new THREE.BufferAttribute(planPositions, 3));
    planGeo.setDrawRange(0, 0);
    var planMat = new THREE.LineDashedMaterial({
      color: 0xf5a623,
      dashSize: 2,
      gapSize: 1,
      transparent: true,
      opacity: 0.5
    });
    _threeLinePlan = new THREE.Line(planGeo, planMat);
    _threeScene.add(_threeLinePlan);

    // Drone marker
    var markerGeo = new THREE.SphereGeometry(1.5, 16, 16);
    var markerMat = new THREE.MeshPhongMaterial({ color: 0x4ecca3, emissive: 0x22aa66 });
    var markerMesh = new THREE.Mesh(markerGeo, markerMat);
    var arrowGeo = new THREE.ConeGeometry(1, 3, 8);
    var arrowMat = new THREE.MeshPhongMaterial({ color: 0x4ecca3 });
    var arrow = new THREE.Mesh(arrowGeo, arrowMat);
    arrow.rotation.x = Math.PI / 2;
    arrow.position.z = 3;
    _threeDroneMarker = new THREE.Group();
    _threeDroneMarker.add(markerMesh);
    _threeDroneMarker.add(arrow);
    _threeScene.add(_threeDroneMarker);

    // Tracking error label
    var errorCanvas = document.createElement('canvas');
    errorCanvas.width = 256;
    errorCanvas.height = 64;
    _threeErrorLabel = { canvas: errorCanvas, ctx: errorCanvas.getContext('2d'), sprite: null };
    var errorTexture = new THREE.CanvasTexture(errorCanvas);
    var errorSpriteMat = new THREE.SpriteMaterial({ map: errorTexture, transparent: true });
    var errorSprite = new THREE.Sprite(errorSpriteMat);
    errorSprite.scale.set(20, 5, 1);
    errorSprite.position.set(0, 15, 0);
    _threeErrorLabel.sprite = errorSprite;
    _threeScene.add(errorSprite);
    updateErrorSprite();

    _threeLoaded = true;
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

    var activeActual = getActiveTrajectory();
    var activeDesired = getActiveDesiredTrajectory();

    // Update actual path
    if (activeActual.length > 0 && _threeLineActual) {
      updateRibbonGeometry(_threeLineActual.geometry, activeActual, 0.08, false);

      if (_threeDroneMarker) {
        var lastPt = activeActual[activeActual.length - 1];
        _threeDroneMarker.position.set(lastPt.x, lastPt.z || 0, lastPt.y);
        _threeDroneMarker.rotation.y = -(lastPt.yaw || 0) * (Math.PI / 180);
      }
    } else if (_threeLineActual) {
      _threeLineActual.geometry.setDrawRange(0, 0);
    }

    // Update desired path
    if (activeDesired.length > 0 && _threeLineDesired) {
      updateRibbonGeometry(_threeLineDesired.geometry, activeDesired, 0.04, true);
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

    // Follow drone
    if (_threeFollowDrone && activeActual.length > 0) {
      var lp = activeActual[activeActual.length - 1];
      _threeCamera.position.set(lp.x + 50, (lp.z || 0) + 50, lp.y + 50);
      _threeControls.target.set(lp.x, lp.z || 0, lp.y);
    }

    _threeControls.update();
    _threeRenderer.render(_threeScene, _threeCamera);
  }

  function clear3D() {
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
    if (_threeDroneMarker) {
      _threeDroneMarker.position.set(0, 0, 0);
    }
  }

  function reset3DView() {
    if (!_threeCamera || !_threeControls) return;
    _threeCamera.position.set(80, 60, 80);
    _threeControls.target.set(0, 0, 0);
    _threeControls.update();
  }

  function presetView(preset) {
    if (!_threeCamera || !_threeControls) return;
    var t = _threeControls.target.clone();
    if (preset === 'top') {
      _threeCamera.position.set(t.x, 200, t.z);
    } else if (preset === 'side') {
      _threeCamera.position.set(t.x + 200, 20, t.z);
    } else if (preset === 'front') {
      _threeCamera.position.set(t.x, 20, t.z + 200);
    } else if (preset === 'iso') {
      _threeCamera.position.set(t.x + 80, 60, t.z + 80);
    }
    _threeCamera.lookAt(t);
    _threeControls.update();
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
    _threeDroneMarker = null;
    _threeAxes = null;
    _threeGrid = null;
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
    var spacing = Math.max(0.1, readNum('pp-rand-spacing', 5));
    var boxM = Math.max(1, readNum('pp-rand-box', 100));

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
      '<button id="pp-3d-reset" class="pp-3d-btn" title="Reset view">Reset</button>',
      '<button id="pp-3d-clear" class="pp-3d-btn" title="Clear 3D path">Clear</button>',
      '</div>',
      '<div class="pp-3d-legend">',
      '<div class="pp-3d-legend-item"><div class="pp-3d-legend-line" style="background:#4a9eff"></div>Actual path</div>',
      '<div class="pp-3d-legend-item"><div class="pp-3d-legend-dash"></div>Desired path</div>',
      '<div id="pp-3d-error" class="pp-3d-legend-item" style="margin-top:4px;color:#4a9eff">Error: — m</div>',
      '</div>',
      '<div class="pp-3d-controls" style="bottom:auto;top:8px;left:auto;right:8px">',
      '<div class="pp-3d-follow-row"><span>Follow drone</span><div id="pp-3d-follow-toggle" class="pp-3d-follow-toggle" role="switch" aria-checked="false" title="Toggle follow drone"></div></div>',
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
      '<div class="pp-section-label">Random path generator</div>',
      '<div class="pp-entry-row">',
      '<input id="pp-rand-n" class="pp-wp-input" value="6" type="number" min="2" step="1" title="Waypoints">',
      '<input id="pp-rand-spacing" class="pp-wp-input" value="5" type="number" min="0.1" step="0.5" title="Spacing (m)">',
      '<input id="pp-rand-box" class="pp-wp-input" value="100" type="number" min="1" step="10" title="Bounding box +/- m">',
      '<input id="pp-rand-z" class="pp-wp-input" value="0" type="number" step="0.1" title="Planned altitude (m)">',
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
      q('pp-set-target').addEventListener('click', setTargetHere);
      q('pp-set-home-manual').addEventListener('click', setHomeManual);
      q('pp-set-target-manual').addEventListener('click', setTargetManual);
      q('pp-add-manual-wp').addEventListener('click', addManualWaypoint);
      q('pp-rand-gen').addEventListener('click', generateRandomPath);

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

  window.__registerPlugin__('Path Planning', window.__PLUGIN_INIT__, window.__PLUGIN_DESTROY__);

})();
