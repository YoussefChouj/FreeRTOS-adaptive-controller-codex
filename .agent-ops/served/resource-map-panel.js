/**
 * Firmware Resource Map Panel — Diagnostics Workspace
 *
 * Reads firmware task, queue, buffer, and memory information via the service API
 * and livewatch probe where available. Displays:
 *   - RTOS task list with stack usage and state
 *   - UART ownership (USART3 / UART4 / UART5)
 *   - Memory regions and heap/stack usage
 *   - Subscribe stream budgets per slot
 *
 * S7: Firmware resource/data-flow mapping (PLANNING_PROMPT §8 WP7)
 * Requires: workspace='diagnostics', gates=[]
 *
 * NOTE: Livewatch reads require the ELF (OBJ/JX_FLY.axf) to be verified first.
 * Use `python -m ground_station.livewatch verify` before probe reads.
 * This panel is read-only — it does not write memory or alter firmware state.
 */
(function () {
  'use strict';

  var _pollTimer = null;
  var _livewatchCache = {};
  var _sessionCache = {};
  var _sessionStats = null;  // from GET /api/view-model?stats=1, fetched on Refresh (full-session scan)
  var _lastFirmwareRead = 0;
  var _FIRMWARE_READ_INTERVAL_MS = 2000;  // poll firmware at 0.5 Hz max

  function init(api) {
    api.registerPanel(
      "Firmware Resource Map",
      function (container, api) {
        render(container, api);
        // Refresh when workspace is active
        _pollTimer = setInterval(function () {
          render(container, api);
        }, 2000);
      },
      {
        workspace: 'diagnostics',
        gates: [],
        description: 'RTOS tasks, UART ownership, memory regions, subscribe budgets',
      }
    );
  }

  function destroy() {
    if (_pollTimer) clearInterval(_pollTimer);
    _pollTimer = null;
  }

  function render(container, api) {
    var state = api.getState();
    var gates = api.getGates();

    // Build HTML
    var html = '';

    // Header with refresh button
    html += '<div style="display:flex;align-items:center;gap:8px;margin-bottom:12px">';
    html += '<span style="font-size:12px;font-weight:700;color:var(--muted);letter-spacing:.06em;text-transform:uppercase">Firmware Resource Map</span>';
    html += '<button id="fw-refresh" style="margin-left:auto;background:none;border:1px solid var(--border);color:var(--muted);font-size:11px;padding:2px 8px;border-radius:4px;cursor:pointer">&#x21BB; Refresh</button>';
    html += '</div>';

    // Gate status row
    html += '<div style="display:flex;flex-wrap:wrap;gap:4px;margin-bottom:12px">';
    html += gateBadge('CONN', gates.connected);
    html += gateBadge('FRESH', gates.fresh);
    html += gateBadge('SCHEMA', gates.schema);
    html += gateBadge('DISARM', gates.disarmed);
    html += gateBadge('CMD', gates.command);
    html += '</div>';

    // Schema and session info
    if (state) {
      html += '<table style="width:100%;font-size:12px;border-collapse:collapse;margin-bottom:12px">';
      html += '<tr><td style="padding:2px 6px;color:var(--muted);width:120px">Schema ID</td><td style="padding:2px 6px;font-family:monospace;font-size:11px">' + escHtml(state.telemetry_schema_id || state.schema_id || '—') + '</td></tr>';
      html += '<tr><td style="padding:2px 6px;color:var(--muted)">Session</td><td style="padding:2px 6px;font-family:monospace;font-size:11px">' + escHtml((state.session_id || '').substring(0, 16) + '…') + '</td></tr>';
      html += '<tr><td style="padding:2px 6px;color:var(--muted)">Samples</td><td style="padding:2px 6px;font-family:monospace">' + (state.samples != null ? state.samples.toLocaleString() : '—') + '</td></tr>';
      html += '<tr><td style="padding:2px 6px;color:var(--muted)">Connected</td><td style="padding:2px 6px">' + (state.connected ? '&#x1F7E2; Yes' : '&#x1F534; No') + '</td></tr>';
      html += '</table>';
    }

    // Stream slots — telemetry budgets
    html += '<div style="font-size:11px;font-weight:700;color:var(--muted);letter-spacing:.06em;text-transform:uppercase;margin-bottom:6px">Telemetry Slots</div>';
    html += '<table style="width:100%;font-size:11px;border-collapse:collapse;margin-bottom:12px">';
    html += '<thead><tr style="border-bottom:1px solid var(--border)">';
    html += '<th style="text-align:left;padding:3px 6px;color:var(--muted)">Slot</th>';
    html += '<th style="text-align:right;padding:3px 6px;color:var(--muted)">Recv</th>';
    html += '<th style="text-align:right;padding:3px 6px;color:var(--muted)">Drop</th>';
    html += '<th style="text-align:right;padding:3px 6px;color:var(--muted)">Loss%</th>';
    html += '<th style="text-align:right;padding:3px 6px;color:var(--muted)">Keys</th>';
    html += '<th style="text-align:left;padding:3px 6px;color:var(--muted)">Status</th>';
    html += '</tr></thead><tbody>';

    var slotData = state && state.streams || {};
    var slots = Object.keys(slotData).sort(function (a, b) { return parseInt(a) - parseInt(b); });
    if (slots.length === 0) {
      html += '<tr><td colspan="6" style="text-align:center;color:var(--muted);padding:8px">No active slots — subscribe first</td></tr>';
    } else {
      slots.forEach(function (slot) {
        var s = slotData[slot];
        var sigState = api.getSignalState(s);
        var sigClass = 'signal-' + sigState;
        var loss = s.loss_pct || 0;
        var lossClass = loss > 5 ? 'color:var(--red)' : loss > 1 ? 'color:var(--amber)' : '';
        html += '<tr style="border-bottom:1px solid rgba(255,255,255,0.05)">';
        html += '<td style="padding:3px 6px;font-family:monospace">' + slot + '</td>';
        html += '<td style="padding:3px 6px;text-align:right;font-family:monospace">' + (s.received || 0).toLocaleString() + '</td>';
        html += '<td style="padding:3px 6px;text-align:right;font-family:monospace">' + (s.dropped || 0).toLocaleString() + '</td>';
        html += '<td style="padding:3px 6px;text-align:right;font-family:monospace;' + lossClass + '">' + loss.toFixed(2) + '%</td>';
        html += '<td style="padding:3px 6px;text-align:right;font-family:monospace">' + Object.keys(s.values || {}).length + '</td>';
        html += '<td style="padding:3px 6px;" class="' + sigClass + '">' + sigState + '</td>';
        html += '</tr>';
      });
    }
    html += '</tbody></table>';

    // Session stats (jitter / effective rate — S6)
    var sessionStats = _sessionStats;
    if (sessionStats && Object.keys(sessionStats).length > 0) {
      html += '<div style="font-size:11px;font-weight:700;color:var(--muted);letter-spacing:.06em;text-transform:uppercase;margin-bottom:6px">Telemetry Quality</div>';
      html += '<table style="width:100%;font-size:11px;border-collapse:collapse;margin-bottom:12px">';
      html += '<thead><tr style="border-bottom:1px solid var(--border)">';
      html += '<th style="text-align:left;padding:3px 6px;color:var(--muted)">Slot</th>';
      html += '<th style="text-align:right;padding:3px 6px;color:var(--muted)">Rate Hz</th>';
      html += '<th style="text-align:right;padding:3px 6px;color:var(--muted)">Eff. Rate Hz</th>';
      html += '<th style="text-align:right;padding:3px 6px;color:var(--muted)">Jitter μs</th>';
      html += '<th style="text-align:right;padding:3px 6px;color:var(--muted)">Jitter Max</th>';
      html += '<th style="text-align:right;padding:3px 6px;color:var(--muted)">Drift ppm</th>';
      html += '<th style="text-align:right;padding:3px 6px;color:var(--muted)">Gaps</th>';
      html += '</tr></thead><tbody>';
      Object.keys(sessionStats).sort(function (a, b) { return parseInt(a) - parseInt(b); }).forEach(function (slot) {
        var st = sessionStats[slot];
        var jitterUs = st.jitter_mean_ns != null ? (st.jitter_mean_ns / 1000).toFixed(2) : '—';
        var jitterMaxUs = st.jitter_max_ns != null ? (st.jitter_max_ns / 1000).toFixed(2) : '—';
        var driftClass = st.source_clock_drift_ppm != null && Math.abs(st.source_clock_drift_ppm) > 1000 ? 'color:var(--red)' : '';
        html += '<tr>';
        html += '<td style="padding:3px 6px;font-family:monospace">' + slot + '</td>';
        html += '<td style="padding:3px 6px;text-align:right;font-family:monospace">' + (st.rate_hz != null ? st.rate_hz.toFixed(1) : '—') + '</td>';
        html += '<td style="padding:3px 6px;text-align:right;font-family:monospace">' + (st.effective_rate_hz != null ? st.effective_rate_hz.toFixed(1) : '—') + '</td>';
        html += '<td style="padding:3px 6px;text-align:right;font-family:monospace">' + jitterUs + '</td>';
        html += '<td style="padding:3px 6px;text-align:right;font-family:monospace">' + jitterMaxUs + '</td>';
        html += '<td style="padding:3px 6px;text-align:right;font-family:monospace;' + driftClass + '">' + (st.source_clock_drift_ppm != null ? st.source_clock_drift_ppm.toFixed(1) : '—') + '</td>';
        html += '<td style="padding:3px 6px;text-align:right;font-family:monospace">' + (st.loss_events || 0) + '</td>';
        html += '</tr>';
      });
      html += '</tbody></table>';
    } else {
      html += '<div style="font-size:11px;color:var(--muted);margin-bottom:12px">Telemetry quality (jitter, effective rate, drift): press Refresh to compute it for the current session.</div>';
    }

    // UART ownership note
    html += '<div style="font-size:11px;font-weight:700;color:var(--muted);letter-spacing:.06em;text-transform:uppercase;margin-bottom:6px">UART Ownership</div>';
    html += '<table style="width:100%;font-size:11px;border-collapse:collapse;margin-bottom:12px">';
    html += '<thead><tr style="border-bottom:1px solid var(--border)">';
    html += '<th style="text-align:left;padding:3px 6px;color:var(--muted)">UART</th>';
    html += '<th style="text-align:left;padding:3px 6px;color:var(--muted)">Role</th>';
    html += '<th style="text-align:left;padding:3px 6px;color:var(--muted)">Transport</th>';
    html += '<th style="text-align:left;padding:3px 6px;color:var(--muted)">Notes</th>';
    html += '</tr></thead><tbody>';
    var uartRows = [
      ['USART3', 'Telemetry TX + Command RX', 'WiFi (UDP 14550)', 'Primary subscribe path'],
      ['UART4', 'Debug mirror / SWO', 'USB-UART bridge', 'RTOS observability'],
      ['UART5', 'Flash + SWD debug', 'MicoAir connector', 'pyOCD + Keil ULINK'],
    ];
    uartRows.forEach(function (row) {
      html += '<tr>';
      row.forEach(function (cell, i) {
        html += '<td style="padding:3px 6px' + (i === 0 ? ';font-family:monospace;font-weight:600' : '') + '">' + escHtml(cell) + '</td>';
      });
      html += '</tr>';
    });
    html += '</tbody></table>';

    // Livewatch ELF note
    html += '<div style="font-size:11px;padding:8px;background:rgba(78,204,163,0.08);border-radius:4px;color:var(--green);margin-bottom:6px">';
    html += '&#x1F4E1; Probe reads require: <code>python -m ground_station.livewatch verify</code>';
    html += '</div>';
    html += '<div style="font-size:11px;color:var(--muted)">';
    html += 'S7: PLC-style task/queue/buffer map, static from the firmware sources. Live per-slot rates come from Refresh (<code>GET /api/view-model?stats=1</code>, full-session scan). RTOS task stacks and heap watermarks are shown by the RTOS Resources panel; raw memory regions need a probe read (<code>ground_station/livewatch/probe.py</code>).';
    html += '</div>';

    container.innerHTML = html;

    // Refresh button
    var btn = document.getElementById('fw-refresh');
    if (btn) {
      btn.addEventListener('click', function () {
        btn.disabled = true;
        btn.textContent = 'Computing…';
        fetch('/api/view-model?stats=1')
          .then(function (r) { return r.json(); })
          .then(function (vm) { _sessionStats = vm.session_stats || {}; })
          .catch(function () {})
          .then(function () { render(container, api); });
      });
    }
  }

  function gateBadge(label, pass) {
    var cls = pass ? 'pass' : 'fail';
    var color = pass ? 'var(--green)' : 'var(--red)';
    var bg = pass ? 'rgba(78,204,163,0.12)' : 'rgba(233,69,96,0.12)';
    return '<span style="font-size:10px;padding:2px 6px;border-radius:3px;background:' + bg + ';color:' + color + ';font-weight:600">' + label + '</span>';
  }

  function escHtml(s) {
    if (s == null) return '';
    return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }

  window.__registerPlugin__('Firmware Resource Map', init, destroy);

}());
