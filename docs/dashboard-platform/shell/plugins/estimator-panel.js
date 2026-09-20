/**
 * estimator-panel.js — EKF / estimator panel (S15 overhaul)
 *
 * Reads EKF state variables from slot 0 values via the sidebar mapping
 * (WifiBridge._slot0_to_sidebar). After the S15 DASHBOARD_FRAME_A_VARS
 * update, the sidebar publishes these keys (mapped from DWARF in firmware):
 *   ekf.vel_x/y/z         ← s_ekf.x[0..2]  (body-frame velocity, m/s)
 *   ekf.bias_accel_x/y/z  ← s_ekf.x[3..5]  (accel bias, m/s²)
 *   ekf.bias_gyro_x/y/z  ← s_ekf.x[6..8]  (gyro bias, rad/s)
 *
 * Position keys (ekf.pos_x/y/z) are NOT present in the 9-state EKF —
 * the model has no position state. Likewise estimator.filter_status and
 * estimator.cov_* are not published. These fields render an explicit
 * "not published by this build" state (n/p + note), never a zero.
 *
 * Source: Ekf9_t in TASK/send_data.c (static, DWARF-visible as `s_ekf`).
 * Telemetry layout: v_body[0..2], b_a_body[3..5], b_g_body[6..8].
 *
 * The panel shows raw IMU channels as a proxy under an honest disclaimer
 * when no EKF data has arrived yet.
 */
(function () {
  'use strict';

  // ── EKF named key groups (when firmware exposes them) ────────────────
  var EKF_GROUPS = [
    {
      label: 'Position (m)',
      keys: ['ekf.pos_x', 'ekf.pos_y', 'ekf.pos_z'],
      axisLabels: ['X', 'Y', 'Z'],
      unit: 'm',
      // 9-state EKF: no position states exist in this build.
      unpublished: true,
      unpublishedNote: 'Not published by this build — this 9-state EKF has no position states.',
    },
    {
      label: 'Velocity (m/s)',
      keys: ['ekf.vel_x', 'ekf.vel_y', 'ekf.vel_z'],
      axisLabels: ['X', 'Y', 'Z'],
      unit: 'm/s',
    },
    {
      label: 'Gyro Bias (rad/s)',
      keys: ['ekf.bias_gyro_x', 'ekf.bias_gyro_y', 'ekf.bias_gyro_z'],
      axisLabels: ['X', 'Y', 'Z'],
      unit: 'rad/s',
    },
    {
      label: 'Accel Bias (m/s²)',
      keys: ['ekf.bias_accel_x', 'ekf.bias_accel_y', 'ekf.bias_accel_z'],
      axisLabels: ['X', 'Y', 'Z'],
      unit: 'm/s²',
    },
  ];

  // ── Raw IMU proxy groups (used until firmware exposes EKF) ────────────
  // Honest labeling per S15 brief: rename to "Raw IMU" not "EKF"
  var RAW_GROUPS = [
    {
      label: 'Raw IMU — Gyro (rad/s)',
      keys: ['slot0.ch0.0', 'slot0.ch0.1'],
      axisLabels: ['X', 'Y'],
      fallback: ['ch0', 'ch1'],
    },
    {
      label: 'Raw IMU — Accel (m/s²)',
      keys: ['slot0.ch0.2'],
      axisLabels: ['Z'],
      fallback: ['ch2'],
    },
    {
      label: 'Raw IMU — Baro Alt (m)',
      keys: ['slot0.ch0.10'],
      axisLabels: ['ALT'],
      fallback: ['ch10'],
    },
  ];

  // Covariance keys — do NOT exist in this build (no covariance telemetry).
  var COV_KEYS = ['estimator.cov_pxx', 'estimator.cov_pyy', 'estimator.cov_pzz',
                  'estimator.cov_vxvx', 'estimator.cov_vyvy', 'estimator.cov_vzvz'];

  // Filter status enum labels (estimator.filter_status is not published by
  // this build; the labels only apply if a future build adds the key).
  var FILTER_STATUS_LABELS = ['Initializing', 'Active', 'Degraded', 'Failed'];

  // Explicit marker for fields this build does not publish. Short cell text
  // plus a prose note per group — never a bare em-dash and never a zero.
  var NOT_PUBLISHED = 'n/p';
  var NOT_PUBLISHED_HINT = 'Not published by this build';

  // ── DOM helpers ─────────────────────────────────────────────────────────
  function q(id) { return document.getElementById(id); }

  function fmtNum(v, dec) {
    if (v == null) return '—';
    dec = (dec === undefined) ? 4 : dec;
    return parseFloat(v).toFixed(dec);
  }

  function fmtCov(v) {
    if (v == null) return '—';
    return parseFloat(v).toExponential(3);
  }

  function getValue(values, keys, fallback) {
    if (!values) return null;
    for (var i = 0; i < keys.length; i++) {
      if (values[keys[i]] != null) return values[keys[i]];
    }
    if (fallback) {
      for (var j = 0; j < fallback.length; j++) {
        if (values[fallback[j]] != null) return values[fallback[j]];
      }
    }
    return null;
  }

  // ── Build panel HTML ────────────────────────────────────────────────────
  function buildHTML() {
    // Build EKF group rows. Published groups show values when s_ekf frames
    // arrive; structurally absent groups (position) render n/p + a note.
    var ekfHTML = EKF_GROUPS.map(function (g) {
      var cells = g.axisLabels.map(function (al, ai) {
        var initText = g.unpublished ? NOT_PUBLISHED : 'AWAITING DATA';
        var initColor = g.unpublished ? 'var(--amber)' : 'var(--muted)';
        return '<div style="display:flex;flex-direction:column;align-items:center;flex:1;min-width:54px">' +
          '<span style="font-size:10px;color:var(--muted)">' + al + '</span>' +
          '<span id="ekf-' + g.keys[ai].replace(/\./g, '-') + '" style="font-family:Consolas,monospace;font-size:13px;color:' + initColor + '">' + initText + '</span>' +
          '<span style="font-size:9px;color:var(--muted)">' + g.unit + '</span>' +
          '</div>';
      }).join('');
      var note = g.unpublished
        ? '<div id="ekf-note-' + g.keys[0].split('.')[1] + '" class="ekf-unpublished-note">' + g.unpublishedNote + '</div>'
        : '';
      return '<div style="margin-bottom:12px">' +
        '<div style="font-size:11px;color:var(--muted);margin-bottom:4px">' + g.label + '</div>' +
        '<div style="display:flex;gap:8px">' + cells + '</div>' + note +
        '</div>';
    }).join('');

    // Build Raw IMU proxy rows
    var rawHTML = RAW_GROUPS.map(function (g) {
      var cells = g.axisLabels.map(function (al, ai) {
        var key = g.keys[ai].replace(/\./g, '-');
        return '<div style="display:flex;flex-direction:column;align-items:center;flex:1;min-width:54px">' +
          '<span style="font-size:10px;color:var(--muted)">' + al + '</span>' +
          '<span id="raw-' + key + '" style="font-family:Consolas,monospace;font-size:13px;color:var(--muted)">AWAITING DATA</span>' +
          '</div>';
      }).join('');
      return '<div style="margin-bottom:10px">' +
        '<div style="font-size:11px;color:var(--muted);margin-bottom:4px">' + g.label + '</div>' +
        '<div style="display:flex;gap:8px">' + cells + '</div>' +
        '</div>';
    }).join('');

    // Covariance cells — structurally absent in this build, start at n/p.
    var covCells = COV_KEYS.map(function (k) {
      var key = k.replace(/\./g, '-');
      return '<div style="display:flex;flex-direction:column;align-items:center;flex:1;min-width:64px">' +
        '<span style="font-size:9px;color:var(--muted)">' + k.split('.').pop() + '</span>' +
        '<span id="cov-' + key + '" style="font-family:Consolas,monospace;font-size:10px;color:var(--amber)">' + NOT_PUBLISHED + '</span>' +
        '</div>';
    }).join('');

    return [
      '<style>',
      '.ekf-no-data { color: var(--muted); font-size: 12px; text-align: center; padding: 20px; }',
      '.ekf-filter { display: inline-flex; align-items: center; gap: 6px; padding: 3px 10px;',
      '  border-radius: 12px; font-size: 11px; font-weight: 600; }',
      '.ekf-filter-ok    { background: rgba(78,204,163,0.15); color: var(--green); }',
      '.ekf-filter-warn  { background: rgba(245,166,35,0.15); color: var(--amber); }',
      '.ekf-filter-err   { background: rgba(233,69,96,0.15);  color: var(--red); }',
      '.ekf-filter-unknown { background: rgba(136,136,170,0.1); color: var(--muted); }',
      '.ekf-disclaimer {',
      '  padding: 10px 12px; background: rgba(245,166,35,0.10);',
      '  border: 1px solid var(--amber); border-radius: 4px;',
      '  color: var(--amber); font-size: 11px; margin-bottom: 12px; font-weight: 600;',
      '}',
      '.ekf-unpublished-note {',
      '  margin-top: 4px; font-size: 10px; color: var(--amber); font-style: italic;',
      '}',
      '.ekf-section-title {',
      '  font-size: 10px; font-weight: 700; color: var(--muted);',
      '  letter-spacing: 0.06em; text-transform: uppercase;',
      '  margin: 14px 0 8px 0; padding-bottom: 4px;',
      '  border-bottom: 1px solid var(--border);',
      '}',
      '</style>',

      // ── Filter status + disclaimer banner ──
      '<div id="ekf-filter-wrap" style="margin-bottom:12px;display:flex;align-items:center;gap:10px">',
      '  <div>',
      '    <div style="font-size:11px;color:var(--muted);margin-bottom:4px">Filter Status</div>',
      '    <span id="ekf-filter-status" class="ekf-filter ekf-filter-unknown">' + NOT_PUBLISHED_HINT + '</span>',
      '  </div>',
      '</div>',

      // Honest disclaimer — shown only until the first s_ekf frame arrives.
      '<div id="ekf-disclaimer" class="ekf-disclaimer">',
      '  ⚠ <strong>EKF telemetry not received yet</strong> — the firmware publishes ',
      '  <code>s_ekf</code> in slot 0; below is RAW IMU telemetry (gyro, accel, baro alt) ',
      '  until those frames arrive. Fields marked <code>n/p</code> are not published by this build.',
      '</div>',

      // ── EKF section (always visible: published cells '—' until data,
      //    structurally absent groups n/p with an explanatory note) ──
      '<div id="ekf-ekf-section">',
      '  <div class="ekf-section-title">Estimator State (EKF, shadow mode — display only)</div>',
      ekfHTML,
      '</div>',

      // ── Raw IMU section (always shown) ──
      '<div class="ekf-section-title">Raw IMU (proxy until EKF available)</div>',
      rawHTML,

      // ── Covariance section ──
      '<div style="margin-bottom:12px">',
      '  <div style="font-size:11px;color:var(--muted);margin-bottom:4px">Covariance — <em>' + NOT_PUBLISHED_HINT + ' — no covariance telemetry is streamed in this build</em></div>',
      '  <div style="display:flex;gap:8px;flex-wrap:wrap">', covCells, '</div>',
      '</div>',
    ].join('');
  }

  // ── State handler ───────────────────────────────────────────────────────
  var _hasData = false;
  var _lastState = null;
  var _keyLastSeen = {};
  var _tickTimer = null;

  function renderEstimator() {
    if (!_lastState || !_lastState.streams) return;
    var stream0 = _lastState.streams['0'];
    var streamReceived = (stream0 && stream0.values) ? true : false;
    var values = streamReceived ? stream0.values : null;
    var now = Date.now();
    var anyUpdate = false;
    var ekfAvailable = false;

    // ── EKF groups ──
    EKF_GROUPS.forEach(function (g) {
      g.axisLabels.forEach(function (al, ai) {
        var k = g.keys[ai];
        var el = q('ekf-' + k.replace(/\./g, '-'));
        if (g.unpublished) {
          if (el) {
            el.textContent = NOT_PUBLISHED;
            el.style.color = 'var(--amber)';
          }
          return;
        }
        if (!el) return;
        if (!streamReceived) {
          el.textContent = 'AWAITING DATA';
          el.style.color = 'var(--muted)';
          return;
        }
        var v = values ? values[k] : null;
        if (v != null) {
          ekfAvailable = true;
          anyUpdate = true;
          var ageMs = _keyLastSeen[k] ? (now - _keyLastSeen[k]) : 0;
          if (ageMs > 30000) {
            el.textContent = 'NO DATA (stale ' + (ageMs / 1000).toFixed(0) + 's)';
            el.style.color = 'var(--muted)';
          } else if (ageMs > 2000) {
            el.textContent = fmtNum(v, 4) + ' (stale ' + (ageMs / 1000).toFixed(1) + 's)';
            el.style.color = 'var(--amber)';
          } else {
            el.textContent = fmtNum(v, 4);
            el.style.color = '';
          }
        } else {
          if (_keyLastSeen[k]) {
            var ageMs = now - _keyLastSeen[k];
            if (ageMs > 30000) {
              el.textContent = 'NO DATA (stale ' + (ageMs / 1000).toFixed(0) + 's)';
              el.style.color = 'var(--muted)';
            } else {
              el.textContent = 'STALE (' + (ageMs / 1000).toFixed(1) + 's)';
              el.style.color = 'var(--amber)';
            }
          } else {
            el.textContent = 'NOT PUBLISHED';
            el.style.color = 'var(--amber)';
          }
        }
      });
    });

    var disclaimer = q('ekf-disclaimer');
    if (disclaimer) disclaimer.style.display = ekfAvailable ? 'none' : '';

    // ── Raw IMU groups (always shown) ──
    RAW_GROUPS.forEach(function (g) {
      g.axisLabels.forEach(function (al, ai) {
        var slot0Key = g.keys[ai];
        var idKey = 'raw-' + slot0Key.replace(/\./g, '-');
        var el = q(idKey);
        if (!el) return;
        if (!streamReceived) {
          el.textContent = 'AWAITING DATA';
          el.style.color = 'var(--muted)';
          return;
        }
        var v = getValue(values, [slot0Key], g.fallback);
        if (v != null) {
          anyUpdate = true;
          var ageMs = _keyLastSeen[slot0Key] ? (now - _keyLastSeen[slot0Key]) : 0;
          if (ageMs > 30000) {
            el.textContent = 'NO DATA (stale ' + (ageMs / 1000).toFixed(0) + 's)';
            el.style.color = 'var(--muted)';
          } else if (ageMs > 2000) {
            el.textContent = fmtNum(v, 4) + ' (stale ' + (ageMs / 1000).toFixed(1) + 's)';
            el.style.color = 'var(--amber)';
          } else {
            el.textContent = fmtNum(v, 4);
            el.style.color = '';
          }
        } else {
          if (_keyLastSeen[slot0Key]) {
            var ageMs = now - _keyLastSeen[slot0Key];
            if (ageMs > 30000) {
              el.textContent = 'NO DATA (stale ' + (ageMs / 1000).toFixed(0) + 's)';
              el.style.color = 'var(--muted)';
            } else {
              el.textContent = 'STALE (' + (ageMs / 1000).toFixed(1) + 's)';
              el.style.color = 'var(--amber)';
            }
          } else {
            el.textContent = 'NOT PUBLISHED';
            el.style.color = 'var(--amber)';
          }
        }
      });
    });

    // ── Covariance keys — not published by this build ──
    COV_KEYS.forEach(function (k) {
      var v = values ? values[k] : null;
      var el = q('cov-' + k.replace(/\./g, '-'));
      if (el) {
        el.textContent = (v != null) ? fmtCov(v) : NOT_PUBLISHED;
        el.style.color = (v != null) ? '' : 'var(--amber)';
        if (v != null) anyUpdate = true;
      }
    });

    // ── Filter status — not published by this build ──
    var fsEl = q('ekf-filter-status');
    if (fsEl) {
      var fsVal = values ? values['estimator.filter_status'] : null;
      if (fsVal != null) {
        var label = FILTER_STATUS_LABELS[Math.floor(fsVal)] || ('Status ' + fsVal);
        if (fsVal === 1) {
          fsEl.textContent = label; fsEl.className = 'ekf-filter ekf-filter-ok';
        } else if (fsVal === 2) {
          fsEl.textContent = label; fsEl.className = 'ekf-filter ekf-filter-warn';
        } else if (fsVal === 3) {
          fsEl.textContent = label; fsEl.className = 'ekf-filter ekf-filter-err';
        } else {
          fsEl.textContent = label; fsEl.className = 'ekf-filter ekf-filter-unknown';
        }
        anyUpdate = true;
      } else {
        fsEl.textContent = NOT_PUBLISHED_HINT;
        fsEl.className = 'ekf-filter ekf-filter-unknown';
      }
    }

    if (anyUpdate) _hasData = true;
  }

  function onState(state) {
    if (!state) return;
    _lastState = state;
    var now = Date.now();
    var stream0 = state.streams ? state.streams['0'] : null;
    var values = (stream0 && stream0.values) ? stream0.values : null;
    if (values) {
      EKF_GROUPS.forEach(function (g) {
        g.keys.forEach(function (k) {
          if (values[k] != null) _keyLastSeen[k] = now;
        });
      });
      RAW_GROUPS.forEach(function (g) {
        g.keys.forEach(function (k) {
          if (getValue(values, [k], g.fallback) != null) _keyLastSeen[k] = now;
        });
      });
    }
    renderEstimator();
  }

  // ── Export (shell uses window.__registerPlugin__) ────────────────────────
  window.__PLUGIN_INIT__ = function(api) {
    api.registerPanel('EKF Estimator', function (container) {
      container.innerHTML = buildHTML();
      renderEstimator();
      api.subscribe(onState);
      if (_tickTimer == null) {
        _tickTimer = setInterval(function () {
          if (_lastState != null) renderEstimator();
        }, 1000);
      }
    });
  };
  window.__PLUGIN_DESTROY__ = function() {
    if (_tickTimer != null) { clearInterval(_tickTimer); _tickTimer = null; }
    _hasData = false;
    _lastState = null;
    _keyLastSeen = {};
  };
  window.__registerPlugin__('EKF Estimator', window.__PLUGIN_INIT__, window.__PLUGIN_DESTROY__);

})();
