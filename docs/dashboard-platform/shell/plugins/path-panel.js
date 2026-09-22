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
  var _trajectory = [];       // Array of {x, y, t} points
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

  var MAX_TRAIL = 100;
  var MIN_ZOOM = 0.2;
  var MAX_ZOOM = 5.0;
  var ZOOM_STEP = 0.2;

  // ── DOM refs ────────────────────────────────────────────────────────────
  var _canvas = null;
  var _ctx = null;
  var _elMetrics = null;
  var _elWaypointTable = null;
  var _elDemoIndicator = null;

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
        if (x !== undefined && y !== undefined &&
            !isNaN(Number(x)) && !isNaN(Number(y))) {
          return { x: parseFloat(x), y: parseFloat(y),
                   z: metreAltitude(vals, lookupValue(vals, POS_Z_KEYS)) };
        }
      }
    }

    // 2. Flat fallbacks (older adapter shapes: state.values or state itself).
    var flat = state.values || state;
    var fx = lookupValue(flat, POS_X_KEYS);
    var fy = lookupValue(flat, POS_Y_KEYS);
    if (fx !== undefined && fy !== undefined &&
        !isNaN(Number(fx)) && !isNaN(Number(fy))) {
      return { x: parseFloat(fx), y: parseFloat(fy),
               z: metreAltitude(flat, lookupValue(flat, POS_Z_KEYS)) };
    }

    return null;
  }

  // ── Update trajectory ───────────────────────────────────────────────────
  function addPoint(pos) {
    if (!pos || pos.x == null || pos.y == null) return;

    _trajectory.push({ x: pos.x, y: pos.y, t: Date.now() });
    if (_trajectory.length > MAX_TRAIL) {
      _trajectory.shift();
    }

    // Update current position
    _currentPos = { x: pos.x, y: pos.y, z: pos.z == null ? null : pos.z };

    // Recalculate metrics
    updateMetrics();
  }

  function updateMetrics() {
    // Total distance along trajectory
    _totalDistance = 0;
    for (var i = 1; i < _trajectory.length; i++) {
      _totalDistance += distance(_trajectory[i - 1], _trajectory[i]);
    }

    // Max deviation from planned path (if waypoints exist)
    _maxDeviation = 0;
    if (_plannedPath.length > 1) {
      _trajectory.forEach(function (pt) {
        var minDist = Infinity;
        // Find distance to nearest segment of planned path
        for (var i = 0; i < _plannedPath.length - 1; i++) {
          var d = pointToSegmentDist(pt, _plannedPath[i], _plannedPath[i + 1]);
          if (d < minDist) minDist = d;
        }
        if (minDist > _maxDeviation) _maxDeviation = minDist;
      });
    }
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
    if (_trajectory.length < 2) return;

    var W = _canvas.width;
    var H = _canvas.height;

    _ctx.beginPath();
    _ctx.strokeStyle = 'rgba(74, 158, 255, 0.6)';
    _ctx.lineWidth = 2;

    var first = worldToScreen(_trajectory[0].x, _trajectory[0].y, W, H);
    _ctx.moveTo(first.x, first.y);

    for (var i = 1; i < _trajectory.length; i++) {
      var pt = worldToScreen(_trajectory[i].x, _trajectory[i].y, W, H);
      _ctx.lineTo(pt.x, pt.y);
    }
    _ctx.stroke();

    // Gradient fade for older points
    for (var i = 1; i < _trajectory.length; i++) {
      var alpha = (i / _trajectory.length) * 0.5;
      var pt = worldToScreen(_trajectory[i].x, _trajectory[i].y, W, H);
      _ctx.beginPath();
      _ctx.fillStyle = 'rgba(74, 158, 255, ' + alpha + ')';
      _ctx.arc(pt.x, pt.y, 2, 0, Math.PI * 2);
      _ctx.fill();
    }
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

    var distStr = _currentPos ? fmtNum(_totalDistance, 2) + ' m' : '—';
    var devStr = (_currentPos && _plannedPath.length > 1) ? fmtNum(_maxDeviation, 2) + ' m' : '—';

    _elMetrics.innerHTML = [
      '<div class="pp-metric"><span class="pp-metric-label">Distance</span><span class="pp-metric-value">' + distStr + '</span></div>',
      '<div class="pp-metric"><span class="pp-metric-label">Max Deviation</span><span class="pp-metric-value">' + devStr + '</span></div>',
      '<div class="pp-metric"><span class="pp-metric-label">Waypoints</span><span class="pp-metric-value">' + _waypoints.length + '</span></div>',
      '<div class="pp-metric"><span class="pp-metric-label">Trail Points</span><span class="pp-metric-value">' + _trajectory.length + '</span></div>'
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
      '.pp-hint { font-size: 10px; color: var(--muted); margin-top: 4px; line-height: 1.4; }',
      '</style>',

      '<div class="pp-container">',

      /* Canvas area */
      '<div class="pp-canvas-wrap">',
      '<canvas id="pp-canvas" class="pp-canvas"></canvas>',
      '<div id="pp-demo-badge" class="pp-demo-badge">No position data</div>',
      '<div class="pp-controls">',
      '<button id="pp-zoom-in" class="pp-btn pp-btn-sm">+</button>',
      '<button id="pp-zoom-out" class="pp-btn pp-btn-sm">&#8722;</button>',
      '<button id="pp-reset-view" class="pp-btn pp-btn-sm">&#8634;</button>',
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
      '<div class="pp-section-label">Legend</div>',
      '<div class="pp-legend">',
      '<div class="pp-legend-item"><div class="pp-legend-dot" style="background:#4a9eff"></div>Current Position</div>',
      '<div class="pp-legend-item"><div class="pp-legend-dot" style="background:#4ecca3"></div>Home</div>',
      '<div class="pp-legend-item"><div class="pp-legend-dot" style="background:#e94560"></div>Target</div>',
      '<div class="pp-legend-item"><div class="pp-legend-dot" style="background:#f5a623"></div>Waypoints</div>',
      '<div class="pp-legend-item"><div class="pp-legend-line" style="background:rgba(74,158,255,0.6)"></div>Trajectory</div>',
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

      // Subscribe to live state for Frame C position
      api.subscribe(function (state) {
        if (!state) return;

        var pos = extractPosition(state);
        if (pos) {
          addPoint(pos);
          render();
          renderMetrics();
        }
      });

      // Initial render
      render();
    });
  };

  window.__PLUGIN_DESTROY__ = function () {
    _trajectory = [];
    _waypoints = [];
    _plannedPath = [];
    _currentPos = null;
    _homePos = null;
    _targetPos = null;
    _zoom = 1.0;
    _panX = 0;
    _panY = 0;
    _autoFitted = false;
  };

  window.__registerPlugin__('Path Planning', window.__PLUGIN_INIT__, window.__PLUGIN_DESTROY__);

})();
