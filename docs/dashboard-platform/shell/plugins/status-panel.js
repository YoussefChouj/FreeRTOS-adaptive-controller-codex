/**
 * status-panel.js — Flight status and safety panel
 *
 * Reads legacy_status (slot 1) fields and typed_stream (slot 9) values.
 * Shows ARM status, FlyMode, battery voltage, command drop count, stream
 * slot summary, and a pending/applied/rejected indicator per command
 * submission (2 s auto-resolve as a UI affordance).
 */
(function () {
  'use strict';

  var PENDING_TIMEOUT_MS = 2000;

  // ── FlyMode labels (adjust to match your firmware enum) ──────────────────
  var FLY_MODE_LABELS = ['Stabilize', 'AltHold', 'PosHold', 'Auto', 'Manual'];

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
  var _armStatus   = null;   // 0 = disarmed, 1 = armed
  var _flyMode     = null;   // numeric enum
  var _vbat        = null;   // V
  var _dropCount   = null;   // gs_cmd_drop_count
  var _streams     = null;   // live streams snapshot
  var _pendingCmd  = null;   // { cmdId, index, value, ts }
  var _cmdStatus   = 'idle'; // 'idle' | 'pending' | 'applied' | 'rejected'

  // ── DOM refs ────────────────────────────────────────────────────────────
  var _elArm       = null;
  var _elFlyMode   = null;
  var _elVbat      = null;
  var _elDropCount = null;
  var _elStreamTbody = null;
  var _elCmdStatus  = null;
  var _elCmdDetail  = null;

  // ── Helpers ─────────────────────────────────────────────────────────────
  function q(id) { return document.getElementById(id); }

  function armLabel(v) { return v === 1 ? 'ARMED' : 'DISARMED'; }
  function armColor(v) { return v === 1 ? 'var(--green)' : 'var(--red)'; }

  function flyModeLabel(v) {
    return (v != null && FLY_MODE_LABELS[v]) ? FLY_MODE_LABELS[v] : '—';
  }

  function lossClass(pct) {
    if (pct > 5)  return 'loss-critical';
    if (pct > 1)  return 'loss-warn';
    return '';
  }

  function cmdStatusIcon(s) {
    if (s === 'pending')   return '&#8987;';   // hourglass
    if (s === 'applied')   return '&#10003;';  // checkmark
    if (s === 'rejected')  return '&#10007;';  // cross
    return '';
  }

  function cmdStatusColor(s) {
    if (s === 'pending')   return 'var(--amber)';
    if (s === 'applied')   return 'var(--green)';
    if (s === 'rejected')  return 'var(--red)';
    return 'var(--muted)';
  }

  function formatValue(k, v) {
    if (v == null) return '—';
    if (typeof v === 'number') {
      if (k.toLowerCase().includes('vbat')) return v.toFixed(2) + ' V';
      if (k.toLowerCase().includes('volt')) return v.toFixed(2) + ' V';
      if (Math.abs(v) < 100) return v.toFixed(3);
      return v.toFixed(1);
    }
    return String(v);
  }

  // ── Build initial DOM ───────────────────────────────────────────────────
  function buildHTML() {
    return [
      '<style>',
      /* ── Local styles (scoped to this panel) ── */
      '.sp-section { margin-bottom: 14px; }',
      '.sp-section:last-child { margin-bottom: 0; }',
      '.sp-label { font-size: 11px; color: var(--muted); margin-bottom: 2px; }',
      '.sp-row { display: flex; align-items: center; gap: 10px; }',
      '.sp-badge {',
      '  display: inline-flex; align-items: center; gap: 5px;',
      '  padding: 4px 10px; border-radius: 20px; font-size: 12px; font-weight: 700;',
      '}',
      '.sp-badge-armed  { background: rgba(78,204,163,0.15); color: var(--green); }',
      '.sp-badge-disarm { background: rgba(233,69,96,0.15);  color: var(--red); }',
      '.sp-big-value { font-size: 22px; font-weight: 600; font-family: Consolas, monospace; }',
      '.sp-cmd-indicator {',
      '  display: flex; align-items: center; gap: 8px; padding: 6px 10px;',
      '  border-radius: 4px; font-size: 12px; font-weight: 600; background: var(--bg);',
      '}',
      '.sp-cmd-icon { font-size: 16px; }',
      '.sp-cmd-detail { font-size: 11px; color: var(--muted); font-weight: 400; }',
      '.sp-stream-table { width: 100%; border-collapse: collapse; font-size: 12px; }',
      '.sp-stream-table th { text-align: left; font-size: 10px; font-weight: 600;',
      '  color: var(--muted); letter-spacing: 0.06em; text-transform: uppercase;',
      '  padding: 3px 6px; border-bottom: 1px solid var(--border); }',
      '.sp-stream-table td { padding: 4px 6px; font-family: Consolas, monospace; }',
      '.sp-stream-table tr:hover td { background: rgba(255,255,255,0.03); }',
      '</style>',

      /* ARM status */
      '<div class="sp-section">',
      '  <div class="sp-label">ARM Status</div>',
      '  <div id="sp-arm-badge" class="sp-badge sp-badge-disarm">DISARMED</div>',
      '</div>',

      /* FlyMode + Voltage */
      '<div class="sp-section">',
      '  <div class="sp-row">',
      '    <div>',
      '      <div class="sp-label">Fly Mode</div>',
      '      <div id="sp-flymode" class="sp-big-value">—</div>',
      '    </div>',
      '    <div>',
      '      <div class="sp-label">Battery</div>',
      '      <div id="sp-vbat" class="sp-big-value">—</div>',
      '    </div>',
      '    <div>',
      '      <div class="sp-label">Cmd Drops</div>',
      '      <div id="sp-drop" class="sp-big-value" style="font-size:16px">—</div>',
      '    </div>',
      '  </div>',
      '</div>',

      /* Command status indicator */
      '<div class="sp-section">',
      '  <div class="sp-label">Last Command</div>',
      '  <div id="sp-cmd-indicator" class="sp-cmd-indicator">',
      '    <span id="sp-cmd-icon" class="sp-cmd-icon"></span>',
      '    <span id="sp-cmd-text">No command submitted</span>',
      '  </div>',
      '</div>',

      /* Stream slot table */
      '<div class="sp-section">',
      '  <div class="sp-label">Active Streams</div>',
      '  <table class="sp-stream-table">',
      '    <thead><tr><th>#</th><th>Seq</th><th>Loss%</th><th>Samples</th></tr></thead>',
      '    <tbody id="sp-stream-tbody"><tr><td colspan="4" style="color:var(--muted)">—</td></tr></tbody>',
      '  </table>',
      '</div>',
    ].join('');
  }

  // ── Update helpers ───────────────────────────────────────────────────────
  function updateArm(val) {
    var el = q('sp-arm-badge');
    if (!el) return;
    if (val === 1) {
      el.textContent = 'ARMED';
      el.className = 'sp-badge sp-badge-armed';
    } else {
      el.textContent = 'DISARMED';
      el.className = 'sp-badge sp-badge-disarm';
    }
  }

  function updateFlyMode(v) {
    var el = q('sp-flymode');
    if (el) el.textContent = flyModeLabel(v);
  }

  function updateVbat(v) {
    var el = q('sp-vbat');
    if (el) el.textContent = formatValue('vbat', v);
  }

  function updateDropCount(v) {
    var el = q('sp-drop');
    if (el) el.textContent = v != null ? v.toLocaleString() : '—';
  }

  function updateStreams(streams) {
    var el = q('sp-stream-tbody');
    if (!el) return;
    if (!streams || Object.keys(streams).length === 0) {
      el.innerHTML = '<tr><td colspan="4" style="color:var(--muted)">No active streams</td></tr>';
      return;
    }
    var rows = Object.keys(streams).map(function (slot) {
      var s = streams[slot];
      var lc = lossClass(s.loss_pct || 0);
      var total = ((s.received || 0) + (s.dropped || 0));
      return '<tr>' +
        '<td>' + slot + '</td>' +
        '<td>' + (s.sequence != null ? s.sequence : '—') + '</td>' +
        '<td class="' + lc + '">' + (s.loss_pct != null ? s.loss_pct.toFixed(2) + '%' : '—') + '</td>' +
        '<td>' + total.toLocaleString() + '</td>' +
        '</tr>';
    }).join('');
    el.innerHTML = rows;
  }

  function updateCmdStatus() {
    var iconEl = q('sp-cmd-icon');
    var textEl = q('sp-cmd-text');
    var wrapEl = q('sp-cmd-indicator');
    if (!iconEl || !textEl || !wrapEl) return;
    iconEl.innerHTML = cmdStatusIcon(_cmdStatus);
    iconEl.style.color = cmdStatusColor(_cmdStatus);
    wrapEl.style.color = cmdStatusColor(_cmdStatus);
    if (_cmdStatus === 'idle') {
      textEl.textContent = 'No command submitted';
    } else if (_cmdStatus === 'pending') {
      var label = _pendingCmd ? (CMD_LABELS[_pendingCmd.cmdId] || 'Cmd ' + _pendingCmd.cmdId) : '';
      textEl.textContent = 'Sending ' + label + '…';
    } else if (_cmdStatus === 'applied') {
      var label = _pendingCmd ? (CMD_LABELS[_pendingCmd.cmdId] || 'Cmd ' + _pendingCmd.cmdId) : '';
      textEl.textContent = label + ' applied';
    } else if (_cmdStatus === 'rejected') {
      textEl.textContent = 'Command rejected';
    }
  }

  // ── State change handler ─────────────────────────────────────────────────
  function onState(state) {
    if (!state) return;

    // ── Parse legacy_status (slot 1) ──
    var slot1 = state.streams && state.streams['1'];
    if (slot1 && slot1.values) {
      var v = slot1.values;
      _armStatus  = v['DroneStatus.ARM_Status'] != null ? v['DroneStatus.ARM_Status'] : _armStatus;
      _flyMode    = v['DroneStatus.FlyMode']    != null ? v['DroneStatus.FlyMode']    : _flyMode;
      _vbat       = v['DroneStatus.vbat']        != null ? v['DroneStatus.vbat']        : _vbat;
      updateArm(_armStatus);
      updateFlyMode(_flyMode);
      updateVbat(_vbat);
    }

    // ── Parse typed_stream (slot 9) for gs_cmd_drop_count ──
    var slot9 = state.streams && state.streams['9'];
    if (slot9 && slot9.values) {
      var v9 = slot9.values;
      _dropCount = v9['gs_cmd_drop_count'] != null ? v9['gs_cmd_drop_count'] : _dropCount;
      updateDropCount(_dropCount);
    }

    // ── Stream summary ──
    _streams = state.streams;
    updateStreams(_streams);
  }

  // ── Export ───────────────────────────────────────────────────────────────
  export const name = 'Flight Status';

  export function init(api) {
    api.registerPanel('Flight Status', function (container) {
      container.innerHTML = buildHTML();

      _elArm       = q('sp-arm-badge');
      _elFlyMode   = q('sp-flymode');
      _elVbat      = q('sp-vbat');
      _elDropCount = q('sp-drop');
      _elStreamTbody = q('sp-stream-tbody');
      _elCmdStatus  = q('sp-cmd-indicator');

      api.subscribe(onState);

      // Initial render
      updateCmdStatus();
    });
  }

  export function destroy() {
    _armStatus = null; _flyMode = null; _vbat = null;
    _dropCount = null; _streams = null; _pendingCmd = null;
    _cmdStatus = 'idle';
  }

  // Expose submitCommand wrapper so other plugins / the shell can hook in
  // This panel listens passively; the shell command form drives submissions.
})();
