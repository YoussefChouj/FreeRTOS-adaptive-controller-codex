/**
 * safety-panel.js — Safety limits panel (S15 overhaul)
 *
 * Reads safety parameters from the S15-merged slot 0 values, not slot 9
 * (slot 9 was never populated). The parameters are expected to be published
 * by firmware parameter readback under these keys:
 *   gs_max_horizontal_speed_mps   cmdId 9  index 0   (Ground Station Safety Limits, CMD 0x09)
 *   gs_max_vertical_speed_mps     cmdId 9  index 1
 *   gs_max_pitch_deg              cmdId 9  index 2
 *   gs_max_roll_deg               cmdId 9  index 3
 *
 * The cmdId was previously 1 (PID Gain) by mistake — that wrote a PID Kp
 * instead of a safety limit, which silently corrupted the rate controller
 * (see COMMAND_SPEC.md L185 — 0x09 is "Ground Station Safety Limits").
 * Until firmware exposes the parameter readback, values show "—" and a
 * "parameter readback pending" hint.
 *
 * The +/- step buttons submit cmdId=9 indices 0-3 (correct — these write
 * the gs_max_* parameters) and they STILL WORK even when no readback yet.
 */
(function () {
  'use strict';

  // ── Safety parameters definition ───────────────────────────────────────
  var SAFETY_PARAMS = [
    {
      key:      'gs_max_horizontal_speed_mps',
      label:    'Max H-Speed',
      unit:     'm/s',
      cmdId:    9,
      index:    0,
      min:      0.05,
      max:      20,
      step:     0.5,
      display:  'speed',
    },
    {
      key:      'gs_max_vertical_speed_mps',
      label:    'Max V-Speed',
      unit:     'm/s',
      cmdId:    9,
      index:    1,
      min:      0.05,
      max:      10,
      step:     0.2,
      display:  'speed',
    },
    {
      key:      'gs_max_pitch_deg',
      label:    'Max Pitch',
      unit:     'deg',
      cmdId:    9,
      index:    2,
      min:      3,
      max:      60,
      step:     1,
      display:  'angle',
    },
    {
      key:      'gs_max_roll_deg',
      label:    'Max Roll',
      unit:     'deg',
      cmdId:    9,
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
  var _paramState = {}; // key → { value, status, pendingTimer, lastSeen }
  var _readbackSeen = false; // any param got a value at least once
  var _streamReceived = false;
  var _tickTimer = null;

  function getParamState(param) {
    if (!_paramState[param.key]) {
      _paramState[param.key] = { value: null, status: 'idle', lastSeen: null };
    }
    return _paramState[param.key];
  }

  // ── Read param value from slot 0 values (multiple fallback keys) ──────
  function readParamValue(values, param) {
    if (!values) return null;
    // Try all reasonable key shapes
    var candidates = [
      param.key,
      'safety.' + param.key,
      'parameters.' + param.key,
      'gs_max.' + param.key.split('_').slice(1).join('_'),
    ];
    for (var i = 0; i < candidates.length; i++) {
      if (values[candidates[i]] != null) return values[candidates[i]];
    }
    return null;
  }

  // ── Update one param row ────────────────────────────────────────────────
  function updateParamRow(param) {
    var st = getParamState(param);
    var valEl = q('sf-val-' + param.index);
    var badgeEl = q('sf-badge-' + param.index);
    var now = Date.now();

    if (st.value == null) {
      if (!_streamReceived) {
        if (valEl) {
          valEl.textContent = 'AWAITING DATA';
          valEl.style.color = 'var(--muted)';
        }
        if (badgeEl) {
          badgeEl.innerHTML = 'awaiting readback';
          badgeEl.style.color = 'var(--muted)';
          badgeEl.style.background = 'transparent';
        }
      } else {
        if (valEl) {
          valEl.textContent = 'NOT PUBLISHED';
          valEl.style.color = 'var(--amber)';
        }
        if (badgeEl) {
          badgeEl.innerHTML = 'NOT PUBLISHED';
          badgeEl.style.color = 'var(--amber)';
          badgeEl.style.background = 'rgba(245,166,35,0.1)';
        }
      }
    } else {
      var ageMs = st.lastSeen ? (now - st.lastSeen) : 0;
      if (ageMs > 30000) {
        if (valEl) {
          valEl.textContent = 'NO DATA (stale ' + (ageMs / 1000).toFixed(0) + 's)';
          valEl.style.color = 'var(--muted)';
        }
        if (badgeEl) {
          badgeEl.innerHTML = 'degraded';
          badgeEl.style.color = 'var(--muted)';
          badgeEl.style.background = 'rgba(136,136,170,0.1)';
        }
      } else if (ageMs > 2000) {
        if (valEl) {
          valEl.textContent = fmt(st.value, param.display) + ' ' + param.unit + ' (stale ' + (ageMs / 1000).toFixed(1) + 's)';
          valEl.style.color = 'var(--amber)';
        }
        if (badgeEl) {
          badgeEl.innerHTML = 'stale (' + (ageMs / 1000).toFixed(1) + 's)';
          badgeEl.style.color = 'var(--amber)';
          badgeEl.style.background = 'rgba(245,166,35,0.1)';
        }
      } else {
        if (valEl) {
          valEl.textContent = fmt(st.value, param.display) + ' ' + param.unit;
          valEl.style.color = '';
        }
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
        '  <div id="sf-val-' + p.index + '" style="font-family:Consolas,monospace;font-size:14px;font-weight:600;color:var(--muted)">AWAITING DATA</div>',
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
        '  color:var(--muted);background:transparent">',
        'no readback</span>',

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
      '.sf-hint {',
      '  padding: 8px 10px; background: rgba(136,136,170,0.08);',
      '  border: 1px dashed var(--muted); border-radius: 4px;',
      '  color: var(--muted); font-size: 11px; margin-bottom: 10px;',
      '}',
      '</style>',

      // Hint banner when no readback yet
      '<div id="sf-hint" class="sf-hint" style="display:none">',
      '  ⓘ <strong>parameter readback pending</strong> — firmware has not yet streamed ',
      '  <code>gs_max_*</code> values. The +/− buttons below still submit writes to the firmware.',
      '</div>',

      // Safety interlocks summary
      '<div class="sf-section">',
      '  <div class="sf-section-title">Safety Interlocks</div>',
      '  <div id="sf-interlock-list"></div>',
      '</div>',

      // Parameter rows
      '<div class="sf-section">',
      '  <div class="sf-section-title">Limits (cmdId=9, indices 0–3)</div>',
        paramRows,
      '</div>',
    ].join('');
  }

  // ── Render interlocks ───────────────────────────────────────────────────
  function updateInterlocks() {
    var el = q('sf-interlock-list');
    if (!el) return;

    if (!_readbackSeen) {
      if (_streamReceived) {
        el.innerHTML = '<div class="sf-interlock sf-interlock-warn">&#9888; Safety limits not published by this build</div>';
      } else {
        el.innerHTML = '<div class="sf-interlock sf-interlock-ok">&#8505; Awaiting first parameter readback from firmware</div>';
      }
      return;
    }

    var items = SAFETY_PARAMS.map(function (p) {
      var st = getParamState(p);
      var v  = st.value;

      if (v == null) return null;

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

    // Read from the merged slot 0 values (S15 fix). Fall back to slot 9 only
    // for back-compat if anyone has manually injected keys there.
    var stream0 = state.streams['0'];
    var stream9 = state.streams['9'];
    if (stream0 != null || stream9 != null) {
      _streamReceived = true;
    }
    var values = (stream0 && stream0.values) ? stream0.values :
                 (stream9 && stream9.values) ? stream9.values : null;

    var now = Date.now();
    SAFETY_PARAMS.forEach(function (param) {
      var v = readParamValue(values, param);
      var st = getParamState(param);
      if (v != null && st.status === 'idle') {
        st.value = v;
        st.lastSeen = now;
        _readbackSeen = true;
      }
      updateParamRow(param);
    });

    updateInterlocks();

    // Update hint banner
    var hintEl = q('sf-hint');
    if (hintEl) hintEl.style.display = _readbackSeen ? 'none' : '';
  }

  // ── Export (shell uses window.__registerPlugin__) ────────────────────────
  window.__PLUGIN_INIT__ = function(api) {
    api.registerPanel('Safety Limits', function (container) {
      container.innerHTML = buildHTML();

      // Wire up +/- buttons — these submit cmdId=9 indices 0-3 (correct)
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
      // Initial render
      SAFETY_PARAMS.forEach(updateParamRow);
      updateInterlocks();

      if (_tickTimer == null) {
        _tickTimer = setInterval(function () {
          if (_readbackSeen || _streamReceived) {
            SAFETY_PARAMS.forEach(updateParamRow);
            updateInterlocks();
          }
        }, 1000);
      }
    });
  };
  window.__PLUGIN_DESTROY__ = function() {
    if (_tickTimer != null) { clearInterval(_tickTimer); _tickTimer = null; }
    _paramState = {};
    _readbackSeen = false;
    _streamReceived = false;
  };
  window.__registerPlugin__('Safety Limits', window.__PLUGIN_INIT__, window.__PLUGIN_DESTROY__);

})();
