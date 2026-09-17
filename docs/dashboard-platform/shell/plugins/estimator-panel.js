/**
 * estimator-panel.js — EKF / estimator panel
 *
 * Reads EKF state variables from the typed_stream values (slot 9).
 * Keys are expected to start with "ekf." or "estimator." e.g.:
 *   ekf.pos_x, ekf.pos_y, ekf.pos_z   (m)
 *   ekf.vel_x, ekf.vel_y, ekf.vel_z  (m/s)
 *   ekf.bias_gyro_x, ekf.bias_gyro_y, ekf.bias_gyro_z
 *   estimator.cov_pxx, estimator.cov_pyy, ...
 *   estimator.filter_status
 *
 * Shows a simple text display grouped by: Position, Velocity, Bias, Covariance.
 */
(function () {
  'use strict';

  // ── Groups ──────────────────────────────────────────────────────────────
  var GROUPS = [
    {
      label: 'Position (m)',
      keys: ['ekf.pos_x','ekf.pos_y','ekf.pos_z',
             'estimator.pos_x','estimator.pos_y','estimator.pos_z'],
      axisLabels: ['X', 'Y', 'Z'],
    },
    {
      label: 'Velocity (m/s)',
      keys: ['ekf.vel_x','ekf.vel_y','ekf.vel_z',
             'estimator.vel_x','estimator.vel_y','estimator.vel_z'],
      axisLabels: ['X', 'Y', 'Z'],
    },
    {
      label: 'Gyro Bias (rad/s)',
      keys: ['ekf.bias_gyro_x','ekf.bias_gyro_y','ekf.bias_gyro_z',
             'estimator.bias_gyro_x','estimator.bias_gyro_y','estimator.bias_gyro_z'],
      axisLabels: ['X', 'Y', 'Z'],
    },
    {
      label: 'Accel Bias (m/s²)',
      keys: ['ekf.bias_acc_x','ekf.bias_acc_y','ekf.bias_acc_z',
             'estimator.bias_acc_x','estimator.bias_acc_y','estimator.bias_acc_z'],
      axisLabels: ['X', 'Y', 'Z'],
    },
  ];

  // Covariance group — derived at render time from any key matching cov pattern
  var COV_KEYS = [
    'ekf.cov_pxx','ekf.cov_pyy','ekf.cov_pzz',
    'ekf.cov_vxx','ekf.cov_vyy','ekf.cov_vzz',
    'estimator.cov_pxx','estimator.cov_pyy','estimator.cov_pzz',
  ];

  var FILTER_STATUS_LABELS = ['Initializing','Active','Degraded','Failed'];

  // ── DOM helpers ─────────────────────────────────────────────────────────
  function q(id) { return document.getElementById(id); }

  function fmtNum(v, dec) {
    if (v == null) return '—';
    dec = dec === undefined ? 4 : dec;
    return parseFloat(v).toFixed(dec);
  }

  function fmtCov(v) {
    if (v == null) return '—';
    return parseFloat(v).toExponential(3);
  }

  // ── Build panel HTML ────────────────────────────────────────────────────
  function buildHTML() {
    var groupHTML = GROUPS.map(function (g, gi) {
      var cells = g.axisLabels.map(function (al, ai) {
        var k = g.keys[ai]; // use first key as representative id
        return '<div style="display:flex;flex-direction:column;align-items:center;flex:1">' +
          '<span style="font-size:10px;color:var(--muted)">' + al + '</span>' +
          '<span id="ekf-' + g.keys[ai] + '" style="font-family:Consolas,monospace;font-size:13px">—</span>' +
          '</div>';
      }).join('');
      // Add estimator variants
      g.axisLabels.forEach(function (al, ai) {
        var ki = g.keys.length / 2 + ai;
        var k = g.keys[ki];
        if (!k) return;
      });

      return '<div style="margin-bottom:12px">' +
        '<div style="font-size:11px;color:var(--muted);margin-bottom:4px">' + g.label + '</div>' +
        '<div style="display:flex;gap:12px">' + cells + '</div>' +
        '</div>';
    }).join('');

    // Covariance row
    var covCells = COV_KEYS.map(function (k) {
      return '<div style="display:flex;flex-direction:column;align-items:center;flex:1">' +
        '<span style="font-size:9px;color:var(--muted)">' + k.split('.').pop() + '</span>' +
        '<span id="ekf-' + k + '" style="font-family:Consolas,monospace;font-size:11px;color:var(--amber)">—</span>' +
        '</div>';
    }).join('');

    return [
      '<style>',
      '.ekf-no-data { color: var(--muted); font-size: 12px; text-align: center; padding: 20px; }',
      '.ekf-filter { display: inline-flex; align-items: center; gap: 6px; padding: 3px 10px;',
      '  border-radius: 12px; font-size: 11px; font-weight: 600; }',
      '.ekf-filter-ok    { background: rgba(78,204,163,0.15); color: var(--green); }',
      '.ekf-filter-warn   { background: rgba(245,166,35,0.15); color: var(--amber); }',
      '.ekf-filter-err    { background: rgba(233,69,96,0.15);  color: var(--red); }',
      '.ekf-filter-unknown { background: rgba(136,136,170,0.1); color: var(--muted); }',
      '</style>',

      '<div id="ekf-filter-wrap" style="margin-bottom:12px">',
      '  <div style="font-size:11px;color:var(--muted);margin-bottom:4px">Filter Status</div>',
      '  <span id="ekf-filter-status" class="ekf-filter ekf-filter-unknown">—</span>',
      '</div>',

      groupHTML,

      '<div style="margin-bottom:12px">',
      '  <div style="font-size:11px;color:var(--muted);margin-bottom:4px">Covariance (P)</div>',
      '  <div style="display:flex;gap:8px;flex-wrap:wrap">',
      COV_KEYS.map(function (k) {
        return '<div style="display:flex;flex-direction:column;align-items:center;flex:1;min-width:52px">' +
          '<span style="font-size:9px;color:var(--muted)">' + k.split('.').pop() + '</span>' +
          '<span id="ekf-' + k + '" style="font-family:Consolas,monospace;font-size:11px;color:var(--amber)">—</span>' +
          '</div>';
      }).join(''),
      '  </div>',
      '</div>',
    ].join('');
  }

  // ── State handler ───────────────────────────────────────────────────────
  var _hasData = false;

  function onState(state) {
    if (!state || !state.streams) return;
    var slot9 = state.streams['9'];
    if (!slot9 || !slot9.values) return;
    var vals = slot9.values;

    var anyUpdate = false;

    // Update each group — use whichever key variant is present
    GROUPS.forEach(function (g) {
      g.axisLabels.forEach(function (al, ai) {
        // Try firmware key first, then estimator key
        var k1 = g.keys[ai];
        var k2 = g.keys[g.keys.length / 2 + ai];
        var v  = vals[k1] != null ? vals[k1] : vals[k2];
        var el = q('ekf-' + k1);
        if (!el) el = q('ekf-' + k2);
        if (el) {
          el.textContent = fmtNum(v, al === 'Z' && g.label.startsWith('Position') ? 3 : 4);
          if (v != null) anyUpdate = true;
        }
      });
    });

    // Covariance
    COV_KEYS.forEach(function (k) {
      var v = vals[k];
      var el = q('ekf-' + k);
      if (el) {
        el.textContent = fmtCov(v);
        if (v != null) anyUpdate = true;
      }
    });

    // Filter status
    var fs = vals['ekf.filter_status'] || vals['estimator.filter_status'];
    var fsEl = q('ekf-filter-status');
    if (fsEl) {
      if (fs != null) {
        var label = FILTER_STATUS_LABELS[fs] || ('Status ' + fs);
        fsEl.textContent = label;
        fsEl.className = 'ekf-filter ' + (fs === 0 ? 'ekf-filter-unknown' : fs === 1 ? 'ekf-filter-ok' : fs === 2 ? 'ekf-filter-warn' : 'ekf-filter-err');
        anyUpdate = true;
      } else {
        fsEl.textContent = '—';
        fsEl.className = 'ekf-filter ekf-filter-unknown';
      }
    }

    if (anyUpdate) _hasData = true;
  }

  // ── Export (shell uses window.__registerPlugin__) ────────────────────────
  window.__PLUGIN_NAME__ = 'EKF Estimator';
  window.__PLUGIN_INIT__ = function(api) {
    api.registerPanel('EKF Estimator', function (container) {
      container.innerHTML = buildHTML();
      api.subscribe(onState);
    });
  };
  window.__PLUGIN_DESTROY__ = function() {
    _hasData = false;
  };
  window.__registerPlugin__('EKF Estimator', window.__PLUGIN_INIT__, window.__PLUGIN_DESTROY__);

})();
