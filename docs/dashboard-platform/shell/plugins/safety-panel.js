/**
 * safety-panel.js — Safety limits panel
 *
 * Reads safety parameters from the typed_stream values (slot 9).
 * Parameters (registry, kind=parameter):
 *   gs_max_horizontal_speed_mps   cmdId 1  index 0
 *   gs_max_vertical_speed_mps    cmdId 1  index 1
 *   gs_max_pitch_deg             cmdId 1  index 2
 *   gs_max_roll_deg              cmdId 1  index 3
 *
 * Each parameter shows: current value, pending/applied/rejected badge,
 * and a +/– step button that submits a shellApi.submitCommand.
 */
(function () {
  'use strict';

  // ── Safety parameters definition ───────────────────────────────────────
  var SAFETY_PARAMS = [
    {
      key:      'safety.gs_max_horizontal_speed_mps',
      label:    'Max H-Speed',
      unit:     'm/s',
      cmdId:    1,
      index:    0,
      min:      0.05,
      max:      20,
      step:     0.5,
      display:  'speed',
    },
    {
      key:      'safety.gs_max_vertical_speed_mps',
      label:    'Max V-Speed',
      unit:     'm/s',
      cmdId:    1,
      index:    1,
      min:      0.05,
      max:      10,
      step:     0.2,
      display:  'speed',
    },
    {
      key:      'safety.gs_max_pitch_deg',
      label:    'Max Pitch',
      unit:     'deg',
      cmdId:    1,
      index:    2,
      min:      3,
      max:      60,
      step:     1,
      display:  'angle',
    },
    {
      key:      'safety.gs_max_roll_deg',
      label:    'Max Roll',
      unit:     'deg',
      cmdId:    1,
      index:    3,
      min:      3,
      max:      60,
      step:     1,
      display:  'angle',
    },
  ];

  var PENDING_TIMEOUT_MS = 2000;

  // ── DOM helpers ─────────────────────────────────────────────────────────
  function q(id) { return document.getElementById(id); }

  function fmt(v, display) {
    if (v == null) return '—';
    if (display === 'speed') return v.toFixed(2);
    return v.toFixed(1);
  }

  // ── State for each param ────────────────────────────────────────────────
  var _paramState = {}; // key → { value, status, pendingTimer }

  function getParamState(param) {
    if (!_paramState[param.key]) {
      _paramState[param.key] = { value: null, status: 'idle' };
    }
    return _paramState[param.key];
  }

  // ── Update one param row ────────────────────────────────────────────────
  function updateParamRow(param) {
    var st = getParamState(param);
    var valEl = q('sf-val-' + param.index);
    var badgeEl = q('sf-badge-' + param.index);
    if (valEl) valEl.textContent = fmt(st.value, param.display) + ' ' + param.unit;
    if (badgeEl) {
      if (st.status === 'idle') {
        badgeEl.innerHTML = '&#10003; current';
        badgeEl.style.color = 'var(--green)';
        badgeEl.style.background = 'rgba(78,204,163,0.1)';
      } else if (st.status === 'pending') {
        badgeEl.innerHTML = '&#8987; pending';
        badgeEl.style.color = 'var(--amber)';
        badgeEl.style.background = 'rgba(245,166,35,0.1)';
      } else if (st.status === 'applied') {
        badgeEl.innerHTML = '&#10003; applied';
        badgeEl.style.color = 'var(--green)';
        badgeEl.style.background = 'rgba(78,204,163,0.1)';
      } else if (st.status === 'rejected') {
        badgeEl.innerHTML = '&#10007; rejected';
        badgeEl.style.color = 'var(--red)';
        badgeEl.style.background = 'rgba(233,69,96,0.1)';
      }
    }
  }

  // ── Submit a new value ──────────────────────────────────────────────────
  function submitParam(api, param, newValue) {
    var st = getParamState(param);

    // Clear any existing timer
    if (st.pendingTimer) { clearTimeout(st.pendingTimer); st.pendingTimer = null; }

    // Set pending
    st.status = 'pending';
    updateParamRow(param);

    api.submitCommand(param.cmdId, param.index, newValue)
      .then(function () {
        st.status = 'applied';
        st.value  = newValue;
        updateParamRow(param);
        // Auto-resolve to idle after 1 s
        st.pendingTimer = setTimeout(function () {
          st.status = 'idle';
          updateParamRow(param);
        }, 1000);
      })
      .catch(function () {
        st.status = 'rejected';
        updateParamRow(param);
        st.pendingTimer = setTimeout(function () {
          st.status = 'idle';
          updateParamRow(param);
        }, PENDING_TIMEOUT_MS);
      });
  }

  // ── Build HTML ──────────────────────────────────────────────────────────
  function buildHTML() {
    var paramRows = SAFETY_PARAMS.map(function (p) {
      return [
        '<div style="display:flex;align-items:center;gap:10px;padding:8px 0;',
        '  border-bottom:1px solid var(--border);flex-wrap:wrap">',

        // Label
        '<div style="min-width:90px">',
        '  <div style="font-size:11px;color:var(--muted)">' + p.label + '</div>',
        '  <div id="sf-val-' + p.index + '" style="font-family:Consolas,monospace;font-size:14px;font-weight:600">— ' + p.unit + '</div>',
        '</div>',

        // Minus button
        '<button id="sf-dec-' + p.index + '" style="',
        '  background:var(--bg);border:1px solid var(--border);',
        '  color:var(--text);border-radius:4px;width:28px;height:28px;',
        '  cursor:pointer;font-size:14px;line-height:1">−</button>',

        // Plus button
        '<button id="sf-inc-' + p.index + '" style="',
        '  background:var(--bg);border:1px solid var(--border);',
        '  color:var(--text);border-radius:4px;width:28px;height:28px;',
        '  cursor:pointer;font-size:14px;line-height:1">+</button>',

        // Status badge
        '<span id="sf-badge-' + p.index + '" style="',
        '  display:inline-flex;align-items:center;gap:4px;padding:2px 8px;',
        '  border-radius:10px;font-size:11px;font-weight:600;',
        '  color:var(--green);background:rgba(78,204,163,0.1)">',
        '&#10003; current</span>',

        '</div>',
      ].join('');
    }).join('');

    return [
      '<style>',
      '.sf-section { margin-bottom: 12px; }',
      '.sf-section-title { font-size: 11px; color: var(--muted); margin-bottom: 8px; }',
      '.sf-interlock { display: flex; align-items: center; gap: 8px; padding: 6px 10px;',
      '  border-radius: 4px; font-size: 12px; font-weight: 600; }',
      '.sf-interlock-warn { background: rgba(245,166,35,0.12); color: var(--amber); }',
      '.sf-interlock-crit { background: rgba(233,69,96,0.12);  color: var(--red); }',
      '.sf-interlock-ok   { background: rgba(78,204,163,0.1);  color: var(--green); }',
      '</style>',

      // Safety interlocks summary
      '<div class="sf-section">',
      '  <div class="sf-section-title">Safety Interlocks</div>',
      '  <div id="sf-interlock-list"></div>',
      '</div>',

      // Parameter rows
      '<div class="sf-section">',
      '  <div class="sf-section-title">Limits</div>',
        paramRows,
      '</div>',
    ].join('');
  }

  // ── Render interlocks ───────────────────────────────────────────────────
  function updateInterlocks() {
    var el = q('sf-interlock-list');
    if (!el) return;

    var items = SAFETY_PARAMS.map(function (p) {
      var st = getParamState(p);
      var v  = st.value;

      if (v == null) return null;

      // Build interlocks
      if (p.display === 'angle') {
        if (v > 45) return { cls: 'crit', text: p.label + ' exceeds 45° (' + v.toFixed(1) + '°)' };
        if (v > 35) return { cls: 'warn', text: p.label + ' > 35° — use caution' };
      } else if (p.display === 'speed') {
        if (v > 10) return { cls: 'warn', text: p.label + ' > 10 m/s — high speed enabled' };
      }
      return null;
    }).filter(Boolean);

    if (items.length === 0) {
      el.innerHTML = '<div class="sf-interlock sf-interlock-ok">&#10003; All limits within safe range</div>';
      return;
    }
    el.innerHTML = items.map(function (item) {
      return '<div class="sf-interlock ' + (item.cls === 'crit' ? 'sf-interlock-crit' : 'sf-interlock-warn') + '">&#9888; ' + item.text + '</div>';
    }).join('');
  }

  // ── State handler ───────────────────────────────────────────────────────
  function onState(state) {
    if (!state || !state.streams) return;
    var slot9 = state.streams['9'];
    if (!slot9 || !slot9.values) return;
    var vals = slot9.values;

    SAFETY_PARAMS.forEach(function (param) {
      var v = vals[param.key];
      var st = getParamState(param);
      if (v != null && st.status === 'idle') {
        st.value = v;
      }
      updateParamRow(param);
    });

    updateInterlocks();
  }

  // ── Export ───────────────────────────────────────────────────────────────
  export const name = 'Safety Limits';

  export function init(api) {
    api.registerPanel('Safety Limits', function (container) {
      container.innerHTML = buildHTML();

      // Wire up +/- buttons
      SAFETY_PARAMS.forEach(function (param) {
        var decBtn = q('sf-dec-' + param.index);
        var incBtn = q('sf-inc-' + param.index);
        if (!decBtn || !incBtn) return;

        decBtn.addEventListener('click', function () {
          var st  = getParamState(param);
          var cur = st.value != null ? st.value : param.min;
          var nv  = Math.max(param.min, cur - param.step);
          submitParam(api, param, nv);
        });

        incBtn.addEventListener('click', function () {
          var st  = getParamState(param);
          var cur = st.value != null ? st.value : param.min;
          var nv  = Math.min(param.max, cur + param.step);
          submitParam(api, param, nv);
        });
      });

      api.subscribe(onState);
    });
  }

  export function destroy() {
    _paramState = {};
  }

})();
