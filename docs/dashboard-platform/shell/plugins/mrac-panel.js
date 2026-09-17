/**
 * mrac-panel.js — MRAC adaptive controller panel
 *
 * Reads MRAC theta parameters from the typed_stream values (slot 9).
 * Keys are expected to start with "mrac." e.g.:
 *   mrac.pitch.theta_0 … mrac.pitch.theta_5
 *   mrac.pitch.u_nom, mrac.pitch.x_m
 *   mrac.roll.theta_0  … mrac.roll.theta_5
 *   mrac.roll.u_nom, mrac.roll.x_m
 *   mrac.yaw.theta_0   … mrac.yaw.theta_5
 *   mrac.yaw.u_nom, mrac.yaw.x_m
 *
 * Displays a table + inline SVG bar chart per axis.
 * Axes are colour-coded: pitch = blue, roll = green, yaw = amber.
 */
(function () {
  'use strict';

  // ── Axis colour map ─────────────────────────────────────────────────────
  var AXIS_COLORS = {
    pitch: '#4a9eff',
    roll:  '#4ecca3',
    yaw:   '#f5a623',
  };

  var AXIS_LABELS = { pitch: 'Pitch', roll: 'Roll', yaw: 'Yaw' };

  // ── Theta key builders ─────────────────────────────────────────────────
  function thetaKeys(axis) {
    return {
      theta_0: 'mrac.' + axis + '.theta_0',
      theta_1: 'mrac.' + axis + '.theta_1',
      theta_2: 'mrac.' + axis + '.theta_2',
      theta_3: 'mrac.' + axis + '.theta_3',
      theta_4: 'mrac.' + axis + '.theta_4',
      theta_5: 'mrac.' + axis + '.theta_5',
      u_nom:   'mrac.' + axis + '.u_nom',
      x_m:     'mrac.' + axis + '.x_m',
    };
  }

  // ── DOM helpers ────────────────────────────────────────────────────────
  function q(id) { return document.getElementById(id); }

  function fmtNum(v) {
    if (v == null) return '—';
    return parseFloat(v).toFixed(4);
  }

  // ── SVG bar chart (no external lib) ────────────────────────────────────
  // Renders theta_0 … theta_5 as vertical bars for one axis.
  // container: DOM element to fill
  // values: [theta_0 … theta_5] (null for missing)
  // color: CSS color string
  // axisLabel: display name (Pitch / Roll / Yaw)
  function renderAxisBars(container, values, color, axisLabel) {
    var W = 260, H = 80;
    var PAD_LEFT = 28, PAD_RIGHT = 8, PAD_TOP = 14, PAD_BOT = 18;
    var INNER_W = W - PAD_LEFT - PAD_RIGHT;
    var INNER_H = H - PAD_TOP - PAD_BOT;

    // Find max absolute value for scaling
    var maxAbs = 0.01;
    values.forEach(function (v) {
      if (v != null && Math.abs(v) > maxAbs) maxAbs = Math.abs(v);
    });
    var scale = INNER_H / (maxAbs * 2);

    var barW = Math.floor(INNER_W / 7); // 6 bars + 1 gap
    var gap  = Math.floor((INNER_W - barW * 6) / 7);

    // Build SVG
    var svg = '<svg viewBox="0 0 ' + W + ' ' + H + '" width="100%" style="display:block;max-width:' + W + 'px">';

    // Zero line
    var zeroY = PAD_TOP + INNER_H / 2;
    svg += '<line x1="0" y1="' + zeroY + '" x2="' + W + '" y2="' + zeroY + '" stroke="rgba(255,255,255,0.15)" stroke-width="1"/>';

    // Bars
    values.forEach(function (v, i) {
      if (v == null) return;
      var cx    = PAD_LEFT + gap + i * (barW + gap) + barW / 2;
      var halfH = Math.abs(v) * scale;
      var x0    = cx - barW / 2;
      var y0    = v >= 0 ? zeroY - halfH : zeroY;
      svg += '<rect x="' + x0 + '" y="' + y0 + '" width="' + barW + '" height="' + halfH + '" fill="' + color + '" opacity="0.85" rx="2"/>';
    });

    // X-axis labels
    values.forEach(function (v, i) {
      var cx = PAD_LEFT + gap + i * (barW + gap) + barW / 2;
      svg += '<text x="' + cx + '" y="' + (H - 4) + '" text-anchor="middle" font-size="9" fill="rgba(255,255,255,0.4)" font-family="Consolas,monospace">&#952;' + i + '</text>';
    });

    // Axis label
    svg += '<text x="3" y="' + (PAD_TOP + 10) + '" font-size="9" fill="' + color + '" font-weight="600" font-family="Segoe UI,sans-serif">' + axisLabel + '</text>';

    // Max annotation
    svg += '<text x="' + (W - PAD_RIGHT) + '" y="' + (PAD_TOP + 10) + '" text-anchor="end" font-size="9" fill="rgba(255,255,255,0.35)" font-family="Consolas,monospace">&#955;' + fmtNum(maxAbs) + '</text>';

    svg += '</svg>';
    container.innerHTML = svg;
  }

  // ── Table row builder ───────────────────────────────────────────────────
  function buildRow(axis, k, color) {
    return '<tr style="border-bottom:1px solid var(--border)">' +
      '<td style="padding:4px 6px;color:' + color + ';font-weight:600;font-size:12px">' + AXIS_LABELS[axis] + '</td>' +
      '<td style="padding:4px 6px;font-family:Consolas,monospace;font-size:12px" id="mrac-td-' + axis + '-' + k + '">—</td>' +
      '</tr>';
  }

  // ── Build panel HTML ────────────────────────────────────────────────────
  function buildHTML() {
    var axes = ['pitch', 'roll', 'yaw'];
    var thetaNames = ['theta_0','theta_1','theta_2','theta_3','theta_4','theta_5','u_nom','x_m'];
    var cols = thetaNames.map(function (n) {
      return '<th style="padding:4px 6px;text-align:right;font-size:10px">' + n.replace('theta_', '&#952;') + '</th>';
    }).join('');

    var rows = '';
    axes.forEach(function (axis) {
      rows += '<tr style="border-bottom:1px solid var(--border)">';
      rows += '<td style="padding:4px 6px;color:' + AXIS_COLORS[axis] + ';font-weight:600;font-size:12px">' + AXIS_LABELS[axis] + '</td>';
      thetaNames.forEach(function (k) {
        rows += '<td style="padding:4px 6px;text-align:right;font-family:Consolas,monospace;font-size:12px" id="mrac-' + axis + '-' + k + '">—</td>';
      });
      rows += '</tr>';
    });

    var bars = '';
    axes.forEach(function (axis) {
      bars += '<div style="margin-bottom:10px" id="mrac-chart-' + axis + '"></div>';
    });

    return [
      '<style>',
      '.mrac-table { width: 100%; border-collapse: collapse; font-size: 12px; }',
      '.mrac-table th { text-align: right; font-size: 10px; font-weight: 600;',
      '  color: var(--muted); letter-spacing: 0.04em; text-transform: uppercase;',
      '  padding: 4px 6px; border-bottom: 1px solid var(--border); }',
      '.mrac-table td { padding: 4px 6px; }',
      '.mrac-table tr:hover td { background: rgba(255,255,255,0.02); }',
      '.mrac-chart-wrap { margin-top: 8px; }',
      '.mrac-no-data { color: var(--muted); font-size: 12px; text-align: center; padding: 20px; }',
      '</style>',

      '<div style="margin-bottom:10px;font-size:11px;color:var(--muted)">Theta parameters (mrac.&lt;axis&gt;.theta_0…5, u_nom, x_m)</div>',

      '<div class="mrac-chart-wrap" id="mrac-charts"></div>',

      '<table class="mrac-table" id="mrac-table">',
      '  <thead><tr><th style="text-align:left">Axis</th>' + cols + '</tr></thead>',
      '  <tbody>' + rows + '</tbody>',
      '</table>',
    ].join('');
  }

  // ── State handler ────────────────────────────────────────────────────────
  var _axes = ['pitch', 'roll', 'yaw'];
  var _thetaNames = ['theta_0','theta_1','theta_2','theta_3','theta_4','theta_5','u_nom','x_m'];
  var _hasData = false;

  function onState(state) {
    if (!state || !state.streams) return;
    var slot9 = state.streams['9'];
    if (!slot9 || !slot9.values) return;
    var vals = slot9.values;

    var allBars = { pitch: [], roll: [], yaw: [] };
    var updated = false;

    _axes.forEach(function (axis) {
      _thetaNames.forEach(function (k) {
        var fullKey = 'mrac.' + axis + '.' + k;
        var v = vals[fullKey];
        var el = q('mrac-' + axis + '-' + k);
        if (el) el.textContent = fmtNum(v);
        if (k !== 'u_nom' && k !== 'x_m') {
          // Collect for bar chart (theta_0..5 only)
          allBars[axis].push(v);
        }
        if (v != null) updated = true;
      });
    });

    if (updated) _hasData = true;

    // Render bar charts
    var chartsEl = q('mrac-charts');
    if (!chartsEl) return;
    if (!_hasData) {
      chartsEl.innerHTML = '<div class="mrac-no-data">Waiting for MRAC telemetry…</div>';
      return;
    }

    _axes.forEach(function (axis) {
      var chartEl = q('mrac-chart-' + axis);
      if (chartEl) {
        renderAxisBars(chartEl, allBars[axis], AXIS_COLORS[axis], AXIS_LABELS[axis]);
      }
    });
  }

  // ── Export ───────────────────────────────────────────────────────────────
  export const name = 'MRAC Controller';

  export function init(api) {
    api.registerPanel('MRAC Controller', function (container) {
      container.innerHTML = buildHTML();
      api.subscribe(onState);
    });
  }

  export function destroy() {
    _hasData = false;
  }

})();
