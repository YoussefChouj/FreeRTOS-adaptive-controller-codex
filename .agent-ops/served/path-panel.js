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

  // ── Parse Frame C position from state ───────────────────────────────────
  function extractPosition(state) {
    if (!state) return null;

    var x = null, y = null;

    // 1. Check state.streams: Frame C is published under tag 'c', mapped to slot 3
    if (state.streams) {
      var candidateSlots = ['3', 'c', '0'];
      Object.keys(state.streams).forEach(function (k) {
        if (candidateSlots.indexOf(k) === -1) candidateSlots.push(k);
      });

      for (var i = 0; i < candidateSlots.length; i++) {
        var slot = state.streams[candidateSlots[i]];
        if (slot && slot.values) {
          var vals = slot.values;
          if (vals['c.earth_x'] !== undefined && vals['c.earth_y'] !== undefined) {
            x = vals['c.earth_x'];
            y = vals['c.earth_y'];
            break;
          }
          if (vals['earth_x'] !== undefined && vals['earth_y'] !== undefined) {
            x = vals['earth_x'];
            y = vals['earth_y'];
            break;
          }
        }
      }
    }

    // 2. Direct tag object or state properties fallback
    if (x === null && state.c) {
      if (state.c['c.earth_x'] !== undefined && state.c['c.earth_y'] !== undefined) {
        x = state.c['c.earth_x'];
        y = state.c['c.earth_y'];
      } else if (state.c.earth_x !== undefined && state.c.earth_y !== undefined) {
        x = state.c.earth_x;
        y = state.c.earth_y;
      }
    }

    if (x === null && state.values) {
      if (state.values['c.earth_x'] !== undefined && state.values['c.earth_y'] !== undefined) {
        x = state.values['c.earth_x'];
        y = state.values['c.earth_y'];
      }
    }

    if (x === null) {
      if (state['c.earth_x'] !== undefined && state['c.earth_y'] !== undefined) {
        x = state['c.earth_x'];
        y = state['c.earth_y'];
      }
    }

    if (x !== null && y !== null && !isNaN(Number(x)) && !isNaN(Number(y))) {
      return { x: parseFloat(x), y: parseFloat(y) };
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
    _currentPos = { x: pos.x, y: pos.y };

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
    render();
  }

  function zoomOut() {
    _zoom = Math.max(_zoom - ZOOM_STEP, MIN_ZOOM);
    render();
  }

  function resetView() {
    _zoom = 1.0;
    _panX = 0;
    _panY = 0;
    render();
  }

  // ── Home/Target markers ──────────────────────────────────────────────────
  function setHomeHere() {
    if (_currentPos) {
      _homePos = { x: _currentPos.x, y: _currentPos.y };
      render();
    }
  }

  function setTargetHere() {
    if (_currentPos) {
      _targetPos = { x: _currentPos.x, y: _currentPos.y };
      render();
    }
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
      '<button id="pp-set-home" class="pp-action-btn">Set Home</button>',
      '<button id="pp-set-target" class="pp-action-btn">Set Target</button>',
      '</div>',
      '</div>',

      '<div>',
      '<div class="pp-section-label">Waypoints</div>',
      '<div id="pp-wp-table"></div>',
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
      function resizeCanvas() {
        var wrap = _canvas.parentElement;
        _canvas.width = wrap.offsetWidth;
        _canvas.height = Math.max(400, wrap.offsetHeight);
        render();
      }

      // Initial setup
      resizeCanvas();
      window.addEventListener('resize', resizeCanvas);

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
  };

  window.__registerPlugin__('Path Planning', window.__PLUGIN_INIT__, window.__PLUGIN_DESTROY__);

})();
