/**
 * mrac-panel.js — MRAC adaptive controller panel (S15 overhaul)
 *
 * Reads MRAC tracking error (e), adaptive output (u_ad), and the full
 * 6-element adaptive weight vector (theta_0..theta_5) per axis from the
 * merged slot 0 values.  After the S15 slot-0 layout update, the dashboard
 * sidebar publishes these keys (mapped from DWARF in wifi_bridge):
 *   mrac.pitch.e, mrac.pitch.u_ad
 *   mrac.pitch.theta_0 … mrac.pitch.theta_5   (4 B float32 each)
 *   (same pattern for roll / yaw / z_rate axes)
 *
 * The sidebar mapping is: mrac_state.<axis>.Theta[N]  →  mrac.<axis>.theta_N
 * via WifiBridge._slot0_to_sidebar (DWARF paths verified 2026-09-17).
 *
 * Named keys read first; if missing (firmware hasn't resolved the symbols
 * yet, or symbol resolution failed), the panel falls back to gyro proxy
 * data with an honest "PROXY" badge so it is clear no real MRAC data
 * is shown.
 *
 * Theta layout per axis (MAX_NUM_BASIS = 6):
 *   theta_0  — bias / feedforward basis (correlates with u_nom)
 *   theta_1  — proportional (correlates with angle)
 *   theta_2  — derivative (correlates with gyro rate)
 *   theta_3  — drag / velocity (INCLUDE_CONTROL_IN_REGRESSOR=1)
 *   theta_4  — extra basis (structured uncertainty, structured)
 *   theta_5  — extra basis (structured uncertainty, unstructured)
 *
 * The +2 slots (theta_3..theta_5) only carry useful information when
 * INCLUDE_CONTROL_IN_REGRESSOR is enabled in firmware; otherwise they stay
 * near zero. All 6 are displayed for completeness.
 */
(function () {
  'use strict';

  // ── Axis config ─────────────────────────────────────────────────────────
  var AXES = ['pitch', 'roll', 'yaw', 'z'];
  var AXIS_COLORS = {
    pitch: '#4a9eff',
    roll:  '#4ecca3',
    yaw:   '#f5a623',
    z:     '#c39bd3',
  };
  var AXIS_LABELS = { pitch: 'Pitch', roll: 'Roll', yaw: 'Yaw', z: 'Altitude' };
  // Theta element indices per axis (0..5 — MAX_NUM_BASIS = 6)
  var THETA_N = [0, 1, 2, 3, 4, 5];

  // ── Helpers ─────────────────────────────────────────────────────────────
  function q(id) { return document.getElementById(id); }

  function fmtNum(v) {
    if (v == null) return '—';
    return parseFloat(v).toFixed(4);
  }

  // ── Read with fallback ─────────────────────────────────────────────────
  // Returns { value, source } where source is 'named' or 'proxy:<reason>'.
  // S15: real MRAC keys come from the sidebar mapping; gyro proxy kept as
  // fallback so the panel is never blank before firmware symbol resolution.
  function readNamed(values, axis, suffix) {
    var namedKey = 'mrac.' + axis + '.' + suffix;
    if (values && values[namedKey] != null) {
      return { value: values[namedKey], source: 'named' };
    }
    // Proxy fallback: gyro rate as a last resort (honest — shows nothing
    // when drone is disarmed and gyro reads near zero).
    var ch = { pitch: 0, roll: 1, yaw: 10, z: 10 }[axis] || 0;
    var slot0Key = 'slot0.ch0.' + ch;
    if (values && values[slot0Key] != null) {
      return { value: values[slot0Key], source: 'proxy:slot0.ch0.' + ch };
    }
    if (values && values['ch' + ch] != null) {
      return { value: values['ch' + ch], source: 'proxy:ch' + ch };
    }
    return { value: null, source: 'none' };
  }

  // ── Read theta vector elements ──────────────────────────────────────────
  // Reads mrac.<axis>.theta_0 … theta_5. Falls back to null per element.
  function readTheta(values, axis) {
    return THETA_N.map(function (n) {
      var key = 'mrac.' + axis + '.theta_' + n;
      return {
        value:  (values && values[key] != null) ? values[key] : null,
        source: (values && values[key] != null) ? 'named' : 'none',
      };
    });
  }

  // ── SVG bar chart (no external lib) ────────────────────────────────────
  // values: array of numeric values to show as bars.
  // labels: array of same length as values.
  // color: bar fill color.
  // axisLabel: chart title text.
  // subLabel: optional subtitle (e.g. source badge).
  function renderAxisBars(container, values, labels, color, axisLabel, subLabel) {
    var W = 260, H = 100;
    var PAD_LEFT = 28, PAD_RIGHT = 8, PAD_TOP = 14, PAD_BOT = 18;
    var INNER_W = W - PAD_LEFT - PAD_RIGHT;
    var INNER_H = H - PAD_TOP - PAD_BOT;

    // Separate into positive (above zero) and negative (below zero) groups
    var posVals = [], posLbls = [];
    var negVals = [], negLbls = [];
    values.forEach(function (v, i) {
      if (v >= 0) { posVals.push(v); posLbls.push(labels[i]); }
      else        { negVals.push(Math.abs(v)); negLbls.push(labels[i]); }
    });

    var maxPos = 0.001;
    var maxNeg = 0.001;
    posVals.forEach(function (v) { if (v > maxPos) maxPos = v; });
    negVals.forEach(function (v) { if (v > maxNeg) maxNeg = v; });
    var scalePos = INNER_H / 2 / maxPos;
    var scaleNeg = INNER_H / 2 / maxNeg;

    var zeroY = PAD_TOP + INNER_H / 2;
    var nPos = posVals.length || 1;
    var nNeg = negVals.length || 1;
    var barW = Math.floor(Math.min(INNER_W / (nPos + nNeg + 2), 14));
    var gap  = Math.max(2, Math.floor((INNER_W - barW * (nPos + nNeg + 1)) / (nPos + nNeg + 2)));

    var svg = '<svg viewBox="0 0 ' + W + ' ' + H + '" width="100%" style="display:block;max-width:' + W + 'px">';
    svg += '<line x1="0" y1="' + zeroY + '" x2="' + W + '" y2="' + zeroY + '" stroke="rgba(255,255,255,0.15)" stroke-width="1"/>';

    // Positive bars (to the right of zero)
    posVals.forEach(function (v, i) {
      var cx = PAD_LEFT + gap + i * (barW + gap) + barW / 2;
      var barH = Math.max(1, v * scalePos);
      var x0 = cx - barW / 2;
      var y0 = zeroY - barH;
      svg += '<rect x="' + x0 + '" y="' + y0 + '" width="' + barW + '" height="' + barH + '" fill="' + color + '" opacity="0.85" rx="2"/>';
      svg += '<text x="' + cx + '" y="' + (H - 2) + '" text-anchor="middle" font-size="8" fill="rgba(255,255,255,0.45)" font-family="Consolas,monospace">' + posLbls[i] + '</text>';
    });

    // Negative bars (to the left of zero)
    var negStartX = PAD_LEFT + gap + posVals.length * (barW + gap) + gap;
    negVals.forEach(function (v, i) {
      var cx = negStartX + i * (barW + gap) + barW / 2;
      var barH = Math.max(1, v * scaleNeg);
      var x0 = cx - barW / 2;
      var y0 = zeroY;
      svg += '<rect x="' + x0 + '" y="' + y0 + '" width="' + barW + '" height="' + barH + '" fill="' + color + '" opacity="0.5" rx="2"/>';
      svg += '<text x="' + cx + '" y="' + (H - 2) + '" text-anchor="middle" font-size="8" fill="rgba(255,255,255,0.35)" font-family="Consolas,monospace">' + negLbls[i] + '</text>';
    });

    // Axis label + subtitle
    svg += '<text x="3" y="' + (PAD_TOP + 10) + '" font-size="9" fill="' + color + '" font-weight="600" font-family="Segoe UI,sans-serif">' + axisLabel + '</text>';
    if (subLabel) {
      svg += '<text x="3" y="' + (PAD_TOP + 20) + '" font-size="8" fill="rgba(255,255,255,0.35)" font-family="Consolas,monospace">' + subLabel + '</text>';
    }
    svg += '</svg>';
    container.innerHTML = svg;
  }

  // ── State ───────────────────────────────────────────────────────────────
  var _hasData = false;
  var _proxyInUse = false;
  // axis → { e, u_ad, theta: [ {value, source}, ... ] }
  var _axisData = {};

  // ── Build panel HTML ────────────────────────────────────────────────────
  function buildHTML() {
    var axesHTML = AXES.map(function (axis) {
      return [
        '<div style="margin-bottom:14px;padding:8px;background:var(--bg);border:1px solid var(--border);border-radius:5px">',
        '  <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:6px">',
        '    <span style="font-size:11px;color:var(--muted);font-weight:600">' + AXIS_LABELS[axis] + ' axis</span>',
        '    <span id="mrac-' + axis + '-source" style="font-size:9px;color:var(--muted);font-family:Consolas,monospace">—</span>',
        '  </div>',
        // e + u_ad row
        '  <div style="display:flex;align-items:center;gap:10px;margin-bottom:6px">',
        '    <div style="flex:1;min-width:80px">',
        '      <div style="font-size:9px;color:var(--muted)">tracking error (e)</div>',
        '      <div id="mrac-' + axis + '-e-val" style="font-family:Consolas,monospace;font-size:14px;font-weight:600">—</div>',
        '    </div>',
        '    <div style="flex:1;min-width:80px">',
        '      <div style="font-size:9px;color:var(--muted)">adaptive output (u_ad)</div>',
        '      <div id="mrac-' + axis + '-u_ad-val" style="font-family:Consolas,monospace;font-size:14px;font-weight:600">—</div>',
        '    </div>',
        '  </div>',
        // Theta bar chart (shows theta_0..theta_5)
        '  <div style="font-size:9px;color:var(--muted);margin-bottom:2px">theta vector (theta_0..theta_5)</div>',
        '  <div id="mrac-chart-' + axis + '"></div>',
        '</div>',
      ].join('');
    }).join('');

    return [
      '<style>',
      '.mrac-no-data { color: var(--muted); font-size: 12px; text-align: center; padding: 20px; }',
      '.mrac-proxy-banner { padding:8px 10px;background:rgba(245,166,35,0.10);border:1px solid var(--amber);border-radius:4px;color:var(--amber);font-size:11px;margin-bottom:12px;font-weight:600; }',
      '.mrac-proxy-pill { display:inline-block;padding:1px 6px;background:rgba(245,166,35,0.2);color:var(--amber);border-radius:8px;font-size:9px;font-weight:700;letter-spacing:0.04em;margin-left:6px; }',
      '</style>',

      // Proxy-mode banner (hidden when named keys are present)
      '<div id="mrac-proxy-banner" class="mrac-proxy-banner" style="display:none">',
      '  <span style="font-family:Consolas,monospace">[PROXY]</span> Firmware does not yet expose mrac.* keys. Showing raw IMU channels as placeholder.',
      '</div>',

      // Per-axis blocks
      '<div id="mrac-axes">' + axesHTML + '</div>',
    ].join('');
  }

  // ── State handler ───────────────────────────────────────────────────────
  function onState(state) {
    if (!state || !state.streams) return;
    var stream0 = state.streams['0'];
    if (!stream0 || !stream0.values) return;

    var values = stream0.values;
    var anyNamed = false;
    var anyProxy = false;
    var anyUpdate = false;

    AXES.forEach(function (axis) {
      _axisData[axis] = {
        e:     readNamed(values, axis, 'e'),
        u_ad:  readNamed(values, axis, 'u_ad'),
        theta: readTheta(values, axis),
      };

      var eInfo   = _axisData[axis].e;
      var uadInfo = _axisData[axis].u_ad;
      var thetaArr = _axisData[axis].theta;

      if (eInfo.source  === 'named') anyNamed = true;
      if (uadInfo.source === 'named') anyNamed = true;
      thetaArr.forEach(function (t) { if (t.source === 'named') anyNamed = true; });
      if (eInfo.source.indexOf('proxy') === 0)    anyProxy = true;
      if (uadInfo.source.indexOf('proxy') === 0)   anyProxy = true;

      // Render e + u_ad values
      var eEl   = q('mrac-' + axis + '-e-val');
      var uadEl = q('mrac-' + axis + '-u_ad-val');
      var srcEl = q('mrac-' + axis + '-source');
      if (eEl)   eEl.textContent = fmtNum(eInfo.value);
      if (uadEl) uadEl.textContent = fmtNum(uadInfo.value);

      // Source badge
      if (srcEl) {
        var namedCount = (eInfo.source === 'named' ? 1 : 0)
                       + (uadInfo.source === 'named' ? 1 : 0)
                       + thetaArr.filter(function (t) { return t.source === 'named'; }).length;
        if (namedCount >= 6) {
          srcEl.textContent = 'mrac.' + axis + '.*';
          srcEl.style.color = 'var(--green)';
        } else if (eInfo.source.indexOf('proxy') === 0 || uadInfo.source.indexOf('proxy') === 0) {
          srcEl.textContent = '[PROXY] gyro fallback';
          srcEl.style.color = 'var(--amber)';
        } else {
          srcEl.textContent = 'no data';
          srcEl.style.color = 'var(--muted)';
        }
      }

      // Theta bar chart: labels = ['e','uad','t0','t1','t2','t3','t4','t5']
      var thetaVals = [eInfo.value, uadInfo.value].concat(
        thetaArr.map(function (t) { return t.value; })
      );
      var thetaLabels = ['e', 'uad'].concat(
        thetaArr.map(function (_, i) { return 't' + i; })
      );
      var isProxy = eInfo.source.indexOf('proxy') === 0;
      var subLabel = isProxy ? '[PROXY]' : '';
      var chartEl = q('mrac-chart-' + axis);
      if (chartEl) {
        renderAxisBars(chartEl, thetaVals, thetaLabels,
                       AXIS_COLORS[axis], AXIS_LABELS[axis], subLabel);
      }

      if (eInfo.value != null || uadInfo.value != null) anyUpdate = true;
    });

    // Show/hide proxy banner
    var banner = q('mrac-proxy-banner');
    if (banner) {
      banner.style.display = anyProxy && !anyNamed ? '' : 'none';
    }
    _proxyInUse = anyProxy && !anyNamed;

    if (anyUpdate) _hasData = true;
  }

  // ── Export (shell uses window.__registerPlugin__) ────────────────────────
  window.__PLUGIN_INIT__ = function(api) {
    api.registerPanel('MRAC Controller', function (container) {
      container.innerHTML = buildHTML();
      api.subscribe(onState);
    });
  };
  window.__PLUGIN_DESTROY__ = function() {
    _hasData = false;
    _proxyInUse = false;
    _axisData = {};
  };
  window.__registerPlugin__('MRAC Controller', window.__PLUGIN_INIT__, window.__PLUGIN_DESTROY__);

})();
