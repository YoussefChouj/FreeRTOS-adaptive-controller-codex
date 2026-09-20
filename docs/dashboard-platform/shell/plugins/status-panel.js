/**
 * status-panel.js — Flight status and safety panel (A1-A3 overhaul)
 *
 * Reads from slot 0 (which the service-layer S15 fix merges raw positional
 * AND named sidebar keys into a single `values` dict). Reads in priority order:
 *   1) named keys  (status.arm, status.flymode, status.vbat, ...)
 *   2) raw positional keys (slot0.ch0.N / ch0..ch14 fallback for legacy)
 *   3) embedded stream metadata (slot0.seq / received / dropped / loss_pct)
 *
 * Promotes the critical few (ARM status, Flight mode, Battery voltage) to the
 * persistent dashboard sidebar, while consolidating operational status
 * (commands, angular rates, authority, state flags) into this panel.
 * Eliminates the redundant telemetry-stream widget.
 */
(function () {
  'use strict';

  var PENDING_TIMEOUT_MS = 2000;

  // ── FlyMode labels (must match DroneStatus.FlyMode enum in firmware) ───
  var FLY_MODE_LABELS = ['Stabilize', 'AltHold', 'PosHold', 'Auto', 'Manual', 'SDK'];

  // ── Command ID → label map ─────────────────────────────────────────────
  var CMD_LABELS = {
    1: 'PID Gain',
    4: 'Flight Mode',
    6: 'Virtual RC',
    13: 'Abort All',
    14: 'SDK Arm Auth',
    20: 'SysID',
    22: 'Motor Bench',
  };

  // ── State ───────────────────────────────────────────────────────────────
  var _armStatus   = null;   // 0 = disarmed, 1 = armed, null = not published
  var _flyMode     = null;   // numeric enum, null = not published
  var _vbat        = null;   // V, null = not published
  var _dropCount   = null;   // gs_cmd_drop_count
  var _rates       = { roll: null, pitch: null, yaw: null, source: '' };
  var _pendingCmd  = null;   // { cmdId, index, value, ts }
  var _cmdStatus   = 'idle'; // 'idle' | 'pending' | 'applied' | 'rejected'
  var _lastTxId    = null;   // dedup: don't re-react to same transaction_id
  var _cmdClearTimer = null; // auto-clear pending status after a delay

  // Status pill booleans (true = active, false = inactive, null = not published)
  var _pills = {
    twc_arrived: null,
    twc_execute: null,
    rc_authority: null,
    of_hold: null,
    estimator_ready: null,
    sbus: null,
  };

  // ── Helpers ─────────────────────────────────────────────────────────────
  function q(id) {
    return (typeof document !== 'undefined' && document.getElementById) ? document.getElementById(id) : null;
  }

  // ── Sidebar management ──────────────────────────────────────────────────
  function ensureSidebar() {
    if (typeof document === 'undefined' || !document.getElementById) return;
    var sidebar = document.getElementById('sidebar');
    if (!sidebar) return;
    if (document.getElementById('card-flight')) return;

    var card = document.createElement('div');
    card.className = 'card';
    card.id = 'card-flight';
    card.innerHTML = [
      '<div class="card-header">',
      '  <span class="card-title">Flight Status</span>',
      '</div>',
      '<div class="stat-grid">',
      '  <div class="stat-item">',
      '    <span class="stat-label">ARM Status</span>',
      '    <span class="stat-value" id="sb-arm" style="color:var(--amber)">NOT PUBLISHED</span>',
      '  </div>',
      '  <div class="stat-item">',
      '    <span class="stat-label">Flight Mode</span>',
      '    <span class="stat-value" id="sb-flymode" style="color:var(--amber)">NOT PUBLISHED</span>',
      '  </div>',
      '  <div class="stat-item">',
      '    <span class="stat-label">Battery</span>',
      '    <span class="stat-value" id="sb-vbat" style="color:var(--amber)">NOT PUBLISHED</span>',
      '  </div>',
      '</div>',
    ].join('');

    sidebar.insertBefore(card, sidebar.firstChild);
  }

  function hideDuplicatedStreamCard() {
    if (typeof document === 'undefined' || !document.getElementById) return;
    var card = document.getElementById('card-streams');
    if (!card) return;
    if (!card.classList.contains('plugin-card')) {
      card.classList.add('plugin-card');
    }
    if (!card.getAttribute('data-workspaces')) {
      card.setAttribute('data-workspaces', 'telemetry diagnostics');
    }
    var ws = (typeof window !== 'undefined' && window.__gs_workspace__ && window.__gs_workspace__()) || 'overview';
    if (ws === 'overview' || ws === 'control') {
      card.style.display = 'none';
    }
  }

  // ── Build initial DOM ───────────────────────────────────────────────────
  function buildHTML() {
    return [
      '<style>',
      /* ── Local styles (scoped to this panel) ── */
      '.sp-section { margin-bottom: 14px; }',
      '.sp-section:last-child { margin-bottom: 0; }',
      '.sp-label { font-size: 11px; color: var(--muted); margin-bottom: 2px; }',
      '.sp-row { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }',
      '.sp-badge {',
      '  display: inline-flex; align-items: center; gap: 5px;',
      '  padding: 4px 10px; border-radius: 20px; font-size: 12px; font-weight: 700;',
      '}',
      '.sp-badge-armed  { background: rgba(78,204,163,0.15); color: var(--green); }',
      '.sp-badge-disarm { background: rgba(233,69,96,0.15);  color: var(--red); }',
      '.sp-badge-unpub  { background: rgba(245,166,35,0.15); color: var(--amber); }',
      '.sp-big-value { font-size: 22px; font-weight: 600; font-family: Consolas, monospace; }',
      '.sp-rate-value { font-size: 14px; font-weight: 600; font-family: Consolas, monospace; }',
      '.sp-cmd-indicator {',
      '  display: flex; align-items: center; gap: 8px; padding: 6px 10px;',
      '  border-radius: 4px; font-size: 12px; font-weight: 600; background: var(--bg);',
      '}',
      '.sp-cmd-icon { font-size: 16px; }',
      '.sp-cmd-detail { font-size: 11px; color: var(--muted); font-weight: 400; }',
      '.sp-pills { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 4px; }',
      '.sp-pill {',
      '  display: inline-flex; align-items: center; gap: 4px;',
      '  padding: 2px 8px; border-radius: 10px; font-size: 10px; font-weight: 600;',
      '  letter-spacing: 0.03em; text-transform: uppercase;',
      '}',
      '.sp-pill-on    { background: rgba(78,204,163,0.15); color: var(--green); }',
      '.sp-pill-off   { background: rgba(136,136,170,0.1); color: var(--muted); }',
      '.sp-pill-warn  { background: rgba(245,166,35,0.15); color: var(--amber); }',
      '.sp-source-hint { font-size: 10px; color: var(--muted); margin-top: 4px; font-style: italic; }',
      '</style>',

      /* ARM status */
      '<div class="sp-section">',
      '  <div class="sp-label">ARM Status</div>',
      '  <div id="sp-arm-badge" class="sp-badge sp-badge-unpub">NOT PUBLISHED</div>',
      '  <div id="sp-arm-source" class="sp-source-hint">field not published by this build</div>',
      '</div>',

      /* FlyMode + Voltage + Cmd drops */
      '<div class="sp-section">',
      '  <div class="sp-row">',
      '    <div>',
      '      <div class="sp-label">Fly Mode</div>',
      '      <div id="sp-flymode" class="sp-big-value" style="color:var(--amber)">NOT PUBLISHED</div>',
      '    </div>',
      '    <div>',
      '      <div class="sp-label">Battery</div>',
      '      <div id="sp-vbat" class="sp-big-value" style="color:var(--amber)">NOT PUBLISHED</div>',
      '    </div>',
      '    <div>',
      '      <div class="sp-label">Cmd Drops</div>',
      '      <div id="sp-drop" class="sp-big-value" style="font-size:16px">—</div>',
      '    </div>',
      '  </div>',
      '</div>',

      /* Status pills row: Authority & State Flags */
      '<div class="sp-section">',
      '  <div class="sp-label">Authority &amp; State Flags</div>',
      '  <div class="sp-pills" id="sp-pills">',
      '    <span class="sp-pill sp-pill-off" id="sp-pill-twc-arrived">TWC ARRIVED</span>',
      '    <span class="sp-pill sp-pill-off" id="sp-pill-twc-execute">TWC EXECUTE</span>',
      '    <span class="sp-pill sp-pill-off" id="sp-pill-rc-auth">RC AUTH</span>',
      '    <span class="sp-pill sp-pill-off" id="sp-pill-of-hold">OF HOLD</span>',
      '    <span class="sp-pill sp-pill-off" id="sp-pill-est-ready">ESTIMATOR</span>',
      '    <span class="sp-pill sp-pill-off" id="sp-pill-sbus">SBUS</span>',
      '  </div>',
      '  <div id="sp-pills-source" class="sp-source-hint"></div>',
      '</div>',

      /* Angular Rates (Body) */
      '<div class="sp-section">',
      '  <div class="sp-label">Angular Rates (Body)</div>',
      '  <div class="sp-row">',
      '    <div>',
      '      <div class="sp-label">Roll Rate</div>',
      '      <div id="sp-rate-roll" class="sp-rate-value" style="color:var(--amber)">NOT PUBLISHED</div>',
      '    </div>',
      '    <div>',
      '      <div class="sp-label">Pitch Rate</div>',
      '      <div id="sp-rate-pitch" class="sp-rate-value" style="color:var(--amber)">NOT PUBLISHED</div>',
      '    </div>',
      '    <div>',
      '      <div class="sp-label">Yaw Rate</div>',
      '      <div id="sp-rate-yaw" class="sp-rate-value" style="color:var(--amber)">NOT PUBLISHED</div>',
      '    </div>',
      '  </div>',
      '  <div id="sp-rates-source" class="sp-source-hint">rates not published by this build</div>',
      '</div>',

      /* Command status indicator */
      '<div class="sp-section">',
      '  <div class="sp-label">Last Command</div>',
      '  <div id="sp-cmd-indicator" class="sp-cmd-indicator">',
      '    <span id="sp-cmd-icon" class="sp-cmd-icon"></span>',
      '    <span id="sp-cmd-text">No command submitted</span>',
      '  </div>',
      '</div>',
    ].join('');
  }

  // ── Update helpers ───────────────────────────────────────────────────────
  function updateArm(val, source) {
    var el = q('sp-arm-badge');
    var srcEl = q('sp-arm-source');
    if (el) {
      if (val === 1) {
        el.textContent = 'ARMED';
        el.className = 'sp-badge sp-badge-armed';
      } else if (val === 0) {
        el.textContent = 'DISARMED';
        el.className = 'sp-badge sp-badge-disarm';
      } else {
        el.textContent = 'NOT PUBLISHED';
        el.className = 'sp-badge sp-badge-unpub';
      }
    }
    if (srcEl) {
      srcEl.textContent = source ? 'source: ' + source : (val == null ? 'field not published by this build' : '');
    }

    // Update sidebar
    var sbEl = q('sb-arm');
    if (sbEl) {
      if (val === 1) {
        sbEl.textContent = 'ARMED';
        sbEl.style.color = 'var(--green)';
      } else if (val === 0) {
        sbEl.textContent = 'DISARMED';
        sbEl.style.color = 'var(--red)';
      } else {
        sbEl.textContent = 'NOT PUBLISHED';
        sbEl.style.color = 'var(--amber)';
      }
    }
  }

  function updateFlyMode(v) {
    var text, color;
    if (v != null && FLY_MODE_LABELS[v]) {
      text = FLY_MODE_LABELS[v];
      color = '';
    } else if (v != null && typeof v === 'number') {
      text = 'Mode ' + v;
      color = '';
    } else {
      text = 'NOT PUBLISHED';
      color = 'var(--amber)';
    }

    var el = q('sp-flymode');
    if (el) {
      el.textContent = text;
      el.style.color = color;
    }

    var sbEl = q('sb-flymode');
    if (sbEl) {
      sbEl.textContent = text;
      sbEl.style.color = color;
    }
  }

  function updateVbat(v) {
    var text, color;
    if (v != null && !isNaN(v)) {
      text = parseFloat(v).toFixed(2) + ' V';
      color = '';
    } else {
      text = 'NOT PUBLISHED';
      color = 'var(--amber)';
    }

    var el = q('sp-vbat');
    if (el) {
      el.textContent = text;
      el.style.color = color;
    }

    var sbEl = q('sb-vbat');
    if (sbEl) {
      sbEl.textContent = text;
      sbEl.style.color = color;
    }
  }

  function updateRates(rates) {
    rates = rates || {};
    var rEl = q('sp-rate-roll');
    var pEl = q('sp-rate-pitch');
    var yEl = q('sp-rate-yaw');
    var srcEl = q('sp-rates-source');

    function fmtRate(val) {
      if (val == null || isNaN(val)) return { text: 'NOT PUBLISHED', color: 'var(--amber)' };
      var sign = val >= 0 ? '+' : '';
      return { text: sign + parseFloat(val).toFixed(2) + ' rad/s', color: '' };
    }

    if (rEl) {
      var fr = fmtRate(rates.roll);
      rEl.textContent = fr.text;
      rEl.style.color = fr.color;
    }
    if (pEl) {
      var fp = fmtRate(rates.pitch);
      pEl.textContent = fp.text;
      pEl.style.color = fp.color;
    }
    if (yEl) {
      var fy = fmtRate(rates.yaw);
      yEl.textContent = fy.text;
      yEl.style.color = fy.color;
    }
    if (srcEl) {
      var any = rates.roll != null || rates.pitch != null || rates.yaw != null;
      srcEl.textContent = any ? (rates.source ? 'source: ' + rates.source : '') : 'rates not published by this build';
    }
  }

  function updateDropCount(v) {
    var el = q('sp-drop');
    if (el) el.textContent = v != null ? v.toLocaleString() : '—';
  }

  function updatePill(id, on, label) {
    var el = q(id);
    if (!el) return;
    if (on === true) {
      el.className = 'sp-pill sp-pill-on';
      el.textContent = label + ' ✓';
    } else if (on === false) {
      el.className = 'sp-pill sp-pill-off';
      el.textContent = label;
    } else {
      el.className = 'sp-pill sp-pill-warn';
      el.textContent = label + ' ?';
    }
  }

  function updateAllPills() {
    updatePill('sp-pill-twc-arrived',  _pills.twc_arrived,      'TWC ARRIVED');
    updatePill('sp-pill-twc-execute',  _pills.twc_execute,      'TWC EXECUTE');
    updatePill('sp-pill-rc-auth',      _pills.rc_authority,     'RC AUTH');
    updatePill('sp-pill-of-hold',      _pills.of_hold,          'OF HOLD');
    updatePill('sp-pill-est-ready',    _pills.estimator_ready,  'ESTIMATOR');
    updatePill('sp-pill-sbus',         _pills.sbus,             'SBUS');
    var srcEl = q('sp-pills-source');
    if (srcEl) {
      var any = _pills.twc_arrived != null ||
                _pills.twc_execute != null ||
                _pills.rc_authority != null ||
                _pills.of_hold != null ||
                _pills.estimator_ready != null ||
                _pills.sbus != null;
      srcEl.textContent = any ? '' : 'status flags not published by this build';
    }
  }

  function updateCmdStatus() {
    var iconEl = q('sp-cmd-icon');
    var textEl = q('sp-cmd-text');
    var wrapEl = q('sp-cmd-indicator');
    if (!iconEl || !textEl || !wrapEl) return;
    var icon, color;
    if (_cmdStatus === 'pending') { icon = '⌛'; color = 'var(--amber)'; }
    else if (_cmdStatus === 'applied') { icon = '✓'; color = 'var(--green)'; }
    else if (_cmdStatus === 'rejected') { icon = '✗'; color = 'var(--red)'; }
    else { icon = ''; color = 'var(--muted)'; }
    iconEl.innerHTML = icon;
    iconEl.style.color = color;
    wrapEl.style.color = color;
    if (_cmdStatus === 'idle') {
      textEl.textContent = 'No command submitted';
    } else if (_cmdStatus === 'pending') {
      var plabel = _pendingCmd ? (CMD_LABELS[_pendingCmd.cmdId] || 'Cmd ' + _pendingCmd.cmdId) : '';
      textEl.textContent = 'Sending ' + plabel + '…';
    } else if (_cmdStatus === 'applied') {
      var alabel = _pendingCmd ? (CMD_LABELS[_pendingCmd.cmdId] || 'Cmd ' + _pendingCmd.cmdId) : '';
      textEl.textContent = alabel + ' applied';
    } else if (_cmdStatus === 'rejected') {
      textEl.textContent = 'Command rejected';
    }
  }

  // ── Telemetry decoding ──────────────────────────────────────────────────
  function readArm(values) {
    if (!values) return { val: null, source: '' };
    if (values['status.arm'] != null) {
      return { val: values['status.arm'] ? 1 : 0, source: 'status.arm' };
    }
    if (values.arm != null) {
      return { val: values.arm ? 1 : 0, source: 'arm' };
    }
    var sb = null;
    if (values['status.status_bits'] != null) sb = values['status.status_bits'];
    else if (values.status_bits != null) sb = values.status_bits;
    else if (values.ch13 != null) sb = values.ch13;
    if (sb != null) {
      return { val: (sb & 0x01) ? 1 : 0, source: 'status_bits bit 0' };
    }
    return { val: null, source: '' };
  }

  function readFlyMode(values) {
    if (!values) return null;
    if (values['status.flymode'] != null) return values['status.flymode'];
    if (values['status.mode'] != null) return values['status.mode'];
    if (values.flymode != null) return values.flymode;
    var sb = values['status.status_bits'] != null ? values['status.status_bits'] :
             (values.status_bits != null ? values.status_bits :
              (values.ch13 != null ? values.ch13 : null));
    if (sb != null) {
      // Bits 1-3 as FlyMode (3-bit field, range 0-4)
      return (sb >> 1) & 0x07;
    }
    return null;
  }

  function readVbat(values) {
    if (!values) return null;
    if (values['status.vbat'] != null) return values['status.vbat'];
    if (values.vbat != null) return values.vbat;
    if (values.real_voltage != null) return values.real_voltage;
    if (values.ch11 != null) return values.ch11;
    if (values['slot0.ch0.11'] != null) return values['slot0.ch0.11'];
    return null;
  }

  function readRates(state) {
    if (!state || !state.streams) return { roll: null, pitch: null, yaw: null, source: '' };
    var roll = null, pitch = null, yaw = null, source = '';
    var slots = Object.keys(state.streams);
    for (var i = 0; i < slots.length; i++) {
      var s = state.streams[slots[i]];
      var v = s && s.values;
      if (!v) continue;
      if (v['c.gyro_x'] != null) {
        roll = v['c.gyro_x']; pitch = v['c.gyro_y']; yaw = v['c.gyro_z'];
        source = 'Frame C (c.gyro_*)';
        break;
      }
      if (v['gyrox'] != null) {
        roll = v['gyrox']; pitch = v['gyroy']; yaw = v['gyroz'];
        source = 'Frame B (gyro*)';
        break;
      }
      if (v['gyro_x'] != null) {
        roll = v['gyro_x']; pitch = v['gyro_y']; yaw = v['gyro_z'];
        source = 'gyro_*';
        break;
      }
      if (v['rate.roll'] != null) {
        roll = v['rate.roll']; pitch = v['rate.pitch']; yaw = v['rate.yaw'];
        source = 'rate.*';
        break;
      }
    }
    return { roll: roll, pitch: pitch, yaw: yaw, source: source };
  }

  function readPills(values) {
    if (!values) return {};
    return {
      twc_arrived:     values['status.twc_arrived']     != null ? !!values['status.twc_arrived'] : null,
      twc_execute:     values['status.twc_execute']     != null ? !!values['status.twc_execute'] : null,
      rc_authority:    values['status.rc_authority']    != null ? !!values['status.rc_authority'] : null,
      of_hold:         values['status.of_hold']         != null ? !!values['status.of_hold'] : null,
      estimator_ready: values['status.estimator_ready'] != null ? !!values['status.estimator_ready'] : null,
      sbus:            values['status.sbus']            != null ? (values['status.sbus'] === 0) : null,
    };
  }

  function readDropCount(s) {
    if (!s) return null;
    if (s.dropped != null) return s.dropped;
    var vals = s.values || {};
    var slot = s.tag || '';
    if (vals[slot + '.dropped'] != null) return vals[slot + '.dropped'];
    if (vals.dropped != null) return vals.dropped;
    return 0;
  }

  // ── State change handler ─────────────────────────────────────────────────
  function onState(state) {
    ensureSidebar();
    hideDuplicatedStreamCard();

    if (!state) {
      updateArm(null, 'no telemetry');
      updateFlyMode(null);
      updateVbat(null);
      updateDropCount(null);
      updateRates({ roll: null, pitch: null, yaw: null, source: '' });
      _pills = { twc_arrived: null, twc_execute: null, rc_authority: null,
                 of_hold: null, estimator_ready: null, sbus: null };
      updateAllPills();
      return;
    }

    var stream0 = state.streams && state.streams['0'];
    if (stream0 && stream0.values) {
      var vals = stream0.values;
      var armInfo = readArm(vals);
      _armStatus = armInfo.val;
      updateArm(_armStatus, armInfo.source);

      _flyMode = readFlyMode(vals);
      updateFlyMode(_flyMode);

      _vbat = readVbat(vals);
      updateVbat(_vbat);

      _dropCount = readDropCount(stream0);
      updateDropCount(_dropCount);

      var pills = readPills(vals);
      _pills.twc_arrived     = pills.twc_arrived;
      _pills.twc_execute     = pills.twc_execute;
      _pills.rc_authority    = pills.rc_authority;
      _pills.of_hold         = pills.of_hold;
      _pills.estimator_ready = pills.estimator_ready;
      _pills.sbus            = pills.sbus;
      updateAllPills();
    } else {
      updateArm(null, 'no telemetry');
      updateFlyMode(null);
      updateVbat(null);
      updateDropCount(null);
      _pills = { twc_arrived: null, twc_execute: null, rc_authority: null,
                 of_hold: null, estimator_ready: null, sbus: null };
      updateAllPills();
    }

    _rates = readRates(state);
    updateRates(_rates);

    // Wire transaction result feedback → "Last Command" indicator.
    var tr = state.last_transaction_result;
    if (tr && tr.transaction_id != null) {
      if (_lastTxId !== tr.transaction_id) {
        _lastTxId = tr.transaction_id;
        var status = (tr.status || '').toLowerCase();
        if (status === 'applied') _cmdStatus = 'applied';
        else if (status === 'rejected') _cmdStatus = 'rejected';
        else _cmdStatus = 'pending';
        _pendingCmd = {
          cmdId: tr.command_id,
          index: tr.index,
          value: tr.detail != null ? tr.detail : null,
          ts: tr.time_ns || Date.now() * 1e6
        };
        // Auto-clear to idle after a few seconds so the next command can be seen.
        clearTimeout(_cmdClearTimer);
        _cmdClearTimer = setTimeout(function () {
          _cmdStatus = 'idle';
          _pendingCmd = null;
          _lastTxId = null;
          updateCmdStatus();
        }, 4000);
        updateCmdStatus();
      }
    } else if (_cmdStatus !== 'idle') {
      // No transaction in flight; if we lingered on a stale status, settle back to idle.
      if (_cmdStatus === 'applied' || _cmdStatus === 'rejected') {
        clearTimeout(_cmdClearTimer);
        _cmdStatus = 'idle';
        _pendingCmd = null;
        _lastTxId = null;
        updateCmdStatus();
      }
    }
  }

  // ── Export (shell uses window.__registerPlugin__) ────────────────────────
  window.__PLUGIN_INIT__ = function(api) {
    ensureSidebar();
    hideDuplicatedStreamCard();

    api.registerPanel('Flight Status', function (container) {
      container.innerHTML = buildHTML();
      api.subscribe(onState);
      updateCmdStatus();
      updateAllPills();
      updateRates(_rates);
      updateArm(_armStatus);
      updateFlyMode(_flyMode);
      updateVbat(_vbat);
    });
  };

  window.__PLUGIN_DESTROY__ = function() {
    clearTimeout(_cmdClearTimer);
    _armStatus = null; _flyMode = null; _vbat = null;
    _dropCount = null; _pendingCmd = null;
    _cmdStatus = 'idle';
    _lastTxId = null;
    _cmdClearTimer = null;
    _rates = { roll: null, pitch: null, yaw: null, source: '' };
    _pills = { twc_arrived: null, twc_execute: null, rc_authority: null,
               of_hold: null, estimator_ready: null, sbus: null };
  };

  if (typeof window !== 'undefined' && window.__registerPlugin__) {
    window.__registerPlugin__('Flight Status', window.__PLUGIN_INIT__, window.__PLUGIN_DESTROY__);
  }

  if (typeof module !== 'undefined' && module.exports) {
    module.exports = {
      buildHTML: buildHTML,
      readArm: readArm,
      readFlyMode: readFlyMode,
      readVbat: readVbat,
      readRates: readRates,
      readPills: readPills,
      FLY_MODE_LABELS: FLY_MODE_LABELS,
      onState: onState,
      updateArm: updateArm,
      updateFlyMode: updateFlyMode,
      updateVbat: updateVbat,
      updateRates: updateRates,
      ensureSidebar: ensureSidebar,
    };
  }

})();
