/**
 * resource-panel.js — RTOS resource panel (S15 overhaul)
 *
 * S15 fix: previously labeled groups "Scheduler / IMU Rate / Magnetometer /
 * Environment" implying they're RTOS metrics. In reality these are the
 * raw telemetry channels (ch0..ch11) being used as proxies for what would
 * otherwise be `rtos.*` keys.
 *
 * This panel:
 *  - Relabels the ch0..ch11 groups as "Raw IMU Telemetry (via chN)"
 *  - "RTOS Metrics" group reads the SWD bridge at `streams['rtos']`.
 *    Keys the firmware cannot publish (rtos.usart3_tx_bytes) render
 *    n/p — "Not published by this build"; with no bridge at all the
 *    group names the missing --rtos-bridge flag instead.
 *  - Keeps the legacy ch0..ch11 raw telemetry display below it.
 */
(function () {
  'use strict';

  // ── Metric definitions ─────────────────────────────────────────────────
  // Group 1 (renamed): raw IMU channels — these are NOT RTOS metrics, but
  // are commonly used as proxies for estimator/IMU rate display.
  var IMU_GROUPS = [
    {
      label: 'IMU Rate (raw via chN)',
      items: [
        { key: 'ch0', label: 'Gyro X',  unit: 'rad/s', fmt: 'float' },
        { key: 'ch1', label: 'Gyro Y',  unit: 'rad/s', fmt: 'float' },
        { key: 'ch2', label: 'Accel Z', unit: 'm/s²',  fmt: 'float' },
      ],
    },
    {
      label: 'Magnetometer (raw via chN)',
      items: [
        { key: 'ch3', label: 'Mag X', unit: '', fmt: 'float' },
        { key: 'ch4', label: 'Mag Y', unit: '', fmt: 'float' },
        { key: 'ch5', label: 'Mag Z', unit: '', fmt: 'float' },
      ],
    },
    {
      label: 'Environment (raw via chN)',
      items: [
        { key: 'ch6',  label: 'Baro Temp',   unit: '°C',  fmt: 'float' },
        { key: 'ch7',  label: 'Baro Press',  unit: 'hPa', fmt: 'float' },
        { key: 'ch10', label: 'Altitude',    unit: 'm',   fmt: 'float' },
        { key: 'ch11', label: 'Battery',     unit: 'V',   fmt: 'float' },
      ],
    },
  ];

  // Group 2: RTOS metrics from `streams['rtos']` (SWD bridge) — same
  // vocabulary as estimator-panel.js:
  //   n/p     = "Not published by this build"   (firmware limitation)
  //   NO DATA = no value seen yet               (bridge off or first sample)
  var NOT_PUBLISHED = 'n/p';
  var NO_DATA = 'NO DATA';
  var NOT_PUBLISHED_HINT = 'Not published by this build';
  var BRIDGE_OFF_HINT =
    'RTOS bridge is not running — start the ground station with ' +
    '--rtos-bridge --rtos-interval 5 (wireless SWD probe required).';
  var BRIDGE_ON_NOTE =
    'Fields marked n/p are not published by this build of the firmware.';

  var RTOS_HINTS = [
    'rtos.scheduler_tick_count', 'rtos.heap_free_bytes',
    'rtos.usart3_tx_count',      'rtos.usart3_tx_bytes',
    'rtos.cmd_queue_depth',      'rtos.cmd_queue_max',
    'rtos.send_task_ticks',      'rtos.dma_busy',
  ];

  // Display metadata for the eight hint keys. `published: false` means the
  // running firmware has no source; the cell is permanently n/p until a
  // future build publishes it.
  var RTOS_METRICS = [
    { key: 'rtos.scheduler_tick_count', label: 'Scheduler tick',     unit: 'ms',     fmt: 'int' },
    { key: 'rtos.heap_free_bytes',      label: 'Heap free',          unit: 'bytes',  fmt: 'int' },
    { key: 'rtos.usart3_tx_count',      label: 'USART3 TX frames',   unit: 'frames', fmt: 'int',
      title: 'Frames accepted into the USART3 TX ring (UA3TxFrames); frames, not bytes' },
    { key: 'rtos.usart3_tx_bytes',      label: 'USART3 TX bytes',    unit: 'bytes',  fmt: 'int',
      published: false, title: 'No cumulative byte counter exists in this firmware build' },
    { key: 'rtos.cmd_queue_depth',      label: 'Cmd queue depth',    unit: 'cmds',   fmt: 'int' },
    { key: 'rtos.cmd_queue_max',        label: 'Cmd queue capacity', unit: 'cmds',   fmt: 'int' },
    { key: 'rtos.send_task_ticks',      label: 'Send task ticks',    unit: 'ticks',  fmt: 'int' },
    { key: 'rtos.dma_busy',             label: 'DMA busy',           unit: '',        fmt: 'bool' },
  ];

  // Real bridge keys that RTOS_HINTS does not list. Both are operationally
  // useful, so the panel hint list — not the bridge — is extended here.
  var RTOS_EXTRA_METRICS = [
    { key: 'rtos.queue_depth',     label: 'USART3 TX ring depth', unit: 'bytes',  fmt: 'int',
      title: 'USART3 software TX ring occupancy in bytes at sample time' },
    { key: 'rtos.usart3_tx_drops', label: 'USART3 TX drops',      unit: 'frames', fmt: 'int',
      title: 'Frames refused because the TX ring was full (FC-side loss, not air loss)' },
  ];

  var RTOS_ALL_METRICS = RTOS_METRICS.concat(RTOS_EXTRA_METRICS);
  var RTOS_METRIC_BY_KEY = {};
  RTOS_ALL_METRICS.forEach(function (m) { RTOS_METRIC_BY_KEY[m.key] = m; });

  // ── DOM helpers ────────────────────────────────────────────────────────
  function q(id) { return document.getElementById(id); }

  function fmtVal(v, fmt) {
    if (v == null) return NO_DATA;
    if (fmt === 'int')  return parseInt(v, 10).toLocaleString();
    if (fmt === 'pct')  return parseFloat(v).toFixed(2) + '%';
    if (fmt === 'bool') return v ? 'YES' : 'NO';
    if (fmt === 'float') {
      if (Math.abs(v) < 100) return parseFloat(v).toFixed(3);
      return parseFloat(v).toFixed(2);
    }
    if (Math.abs(v) < 10) return parseFloat(v).toFixed(4);
    return parseFloat(v).toFixed(2);
  }

  // Try multiple slot IDs to find a stream containing RTOS/system keys
  function findRtosStream(state) {
    if (!state || !state.streams) return null;
    var candidates = ['rtos', '99', 'system', '9', '10', '11', '12'];
    for (var i = 0; i < candidates.length; i++) {
      var s = state.streams[candidates[i]];
      if (s && s.values) {
        // Check if any key looks like rtos.* or system.*
        var keys = Object.keys(s.values);
        for (var k = 0; k < keys.length; k++) {
          if (/^(rtos|system)\./.test(keys[k])) return s;
        }
      }
    }
    return null;
  }

  // ── Build panel HTML ───────────────────────────────────────────────────
  function buildHTML() {
    var imuGroupHTML = IMU_GROUPS.map(function (g) {
      var cells = g.items.map(function (item) {
        return '<div style="display:flex;flex-direction:column;gap:2px;flex:1;min-width:72px">' +
          '<span style="font-size:10px;color:var(--muted)">' + item.label + '</span>' +
          '<span id="res-' + item.key + '" class="res-no-data" style="font-family:Consolas,monospace;font-size:13px">' + NO_DATA + '</span>' +
          '<span style="font-size:10px;color:var(--muted)">' + item.unit + '</span>' +
          '</div>';
      }).join('');
      return [
        '<div style="margin-bottom:12px">',
        '  <div style="font-size:11px;color:var(--muted);margin-bottom:6px">' + g.label + '</div>',
        '  <div style="display:flex;gap:12px;flex-wrap:wrap">' + cells + '</div>',
        '</div>',
      ].join('');
    }).join('');

    var streamMetaHTML = [
      '<div style="margin-bottom:12px">',
      '  <div style="font-size:11px;color:var(--muted);margin-bottom:6px">Stream Metadata (slot 0)</div>',
      '  <div style="display:flex;gap:12px;flex-wrap:wrap">',
      '    <div style="display:flex;flex-direction:column;gap:2px;flex:1;min-width:72px">',
      '      <span style="font-size:10px;color:var(--muted)">Seq</span>',
      '      <span id="res-seq" class="res-no-data" style="font-family:Consolas,monospace;font-size:13px">' + NO_DATA + '</span>',
      '    </div>',
      '    <div style="display:flex;flex-direction:column;gap:2px;flex:1;min-width:72px">',
      '      <span style="font-size:10px;color:var(--muted)">Loss %</span>',
      '      <span id="res-loss" class="res-no-data" style="font-family:Consolas,monospace;font-size:13px">' + NO_DATA + '</span>',
      '    </div>',
      '    <div style="display:flex;flex-direction:column;gap:2px;flex:1;min-width:72px">',
      '      <span style="font-size:10px;color:var(--muted)">Dropped</span>',
      '      <span id="res-dropped" class="res-no-data" style="font-family:Consolas,monospace;font-size:13px">' + NO_DATA + '</span>',
      '    </div>',
      '    <div style="display:flex;flex-direction:column;gap:2px;flex:1;min-width:72px">',
      '      <span style="font-size:10px;color:var(--muted)">Received</span>',
      '      <span id="res-received" class="res-no-data" style="font-family:Consolas,monospace;font-size:13px">' + NO_DATA + '</span>',
      '    </div>',
      '  </div>',
      '</div>',
    ].join('');

    var rtosCells = RTOS_ALL_METRICS.map(function (m) {
      var init, valColor, noDataCls;
      if (m.published === false) {
        init = NOT_PUBLISHED; valColor = 'color:var(--amber)'; noDataCls = '';
      } else {
        init = NO_DATA; valColor = ''; noDataCls = ' class="res-no-data"';
      }
      var titleAttr = m.title ? ' title="' + m.title + '"' : '';
      return '<div style="display:flex;flex-direction:column;gap:2px;flex:1;min-width:90px">' +
        '<span style="font-size:10px;color:var(--muted)">' + m.label + '</span>' +
        '<span id="res-' + m.key.replace(/\./g, '-') + '"' + titleAttr + noDataCls +
        ' style="font-family:Consolas,monospace;font-size:13px;' + valColor + '">' + init + '</span>' +
        '<span style="font-size:9px;color:var(--muted)">' + m.unit + '</span>' +
        '</div>';
    }).join('');

    var rtosGroupHTML = [
      '<div id="res-rtos-wrap" style="margin-bottom:12px">',
      '  <div style="font-size:11px;color:var(--muted);margin-bottom:6px">',
      '    RTOS Metrics (via SWD)',
      '    <span id="res-rtos-source" style="font-size:9px;color:var(--muted);margin-left:6px"></span>',
      '  </div>',
      '  <div id="res-rtos-grid" style="display:flex;gap:12px;flex-wrap:wrap">' + rtosCells + '</div>',
      '  <div id="res-rtos-hint" style="margin-top:6px;padding:6px 10px;background:rgba(245,166,35,0.10);border:1px dashed var(--amber);border-radius:4px;color:var(--amber);font-size:11px">',
      '    ⓘ ' + BRIDGE_OFF_HINT,
      '  </div>',
      '</div>',
    ].join('');

    return [
      '<style>',
      '.res-warn { color: var(--amber); }',
      '.res-crit { color: var(--red); }',
      '.res-ok   { color: var(--green); }',
      '.res-no-data { color: var(--muted); }',
      '</style>',

      // Disclaimer banner at top
      '<div style="padding:8px 10px;background:rgba(245,166,35,0.10);border:1px solid var(--amber);border-radius:4px;color:var(--amber);font-size:11px;margin-bottom:12px;font-weight:600">',
      '  ⓘ The ch0..ch11 groups below are <strong>raw IMU telemetry channels</strong>, not RTOS metrics. ',
      '  RTOS resource counters live in the group above (SWD bridge); <code>n/p</code> = not published by this build.',
      '</div>',

      // Telemetry-bridge wellness (from /health/slots stream_health). This is
      // deliberately a distinct signal from the RTOS/SWD bridge so the page
      // stops telling the operator the whole link is down when the WiFi
      // telemetry stream is, in fact, flowing (AUDIT_2026-09-21 Diagnostics).
      '<div id="res-bridge-status" class="res-bridge-unknown" ',
      'style="padding:8px 10px;border-radius:4px;font-size:11px;font-weight:600;margin-bottom:12px;color:var(--muted);background:rgba(136,136,170,0.08);border:1px dashed var(--muted)">',
      'Telemetry bridge: …</div>',

      rtosGroupHTML,
      streamMetaHTML,
      imuGroupHTML,
    ].join('');
  }

  // ── Update a metric cell ───────────────────────────────────────────────
  function getChannelVal(values, ch) {
    if (!values) return null;
    // Try slot0.ch0.N, chN, both forms
    if (values['slot0.ch0.' + ch] != null) return values['slot0.ch0.' + ch];
    if (values['ch' + ch] != null) return values['ch' + ch];
    return null;
  }

  function updateCell(id, value, fmt) {
    var el = q(id);
    if (!el) return;
    el.textContent = fmtVal(value, fmt);
    if (value == null) {
      // Absent source: muted NO DATA, never a green/— placeholder.
      el.className = 'res-no-data';
      return;
    }
    if (fmt === 'pct') {
      var pct = parseFloat(value);
      el.className = '';
      if (pct > 5)      el.className = 'res-crit';
      else if (pct > 1) el.className = 'res-warn';
      else el.className = 'res-ok';
    }
  }

  // ── State handler ───────────────────────────────────────────────────────
  var _hasData = false;

  // Bridge wellness comes from GET /health/slots (stream_health). It is the
  // single honest source for "the telemetry bridge is actually flowing", as
  // opposed to the presence of an rtos.* SWD stream, which this build does
  // not subscribe. A single failed poll keeps the last known state rather
  // than flapping to a bare "not running".
  var _bridgeHealth = null;      // last /health/slots payload, or null
  var _bridgePollTimer = null;   // setInterval handle for /health/slots polls

  // Level -> display text for the telemetry-bridge banner. ``telemetry_seen``
  // distinguishes "running + streaming" from an explicit "no frames" state.
  function bridgeStateFromHealth(health) {
    if (!health || !health.stream_health) {
      return { level: 'unknown', text: 'Telemetry bridge: status unavailable' };
    }
    var sh = health.stream_health;
    var ageS = sh.last_frame_age_ns != null ? sh.last_frame_age_ns / 1e9 : null;
    var ago = ageS == null ? null :
      (ageS < 1 ? (ageS * 1000).toFixed(0) + ' ms ago' : ageS.toFixed(1).replace(/\.0$/, '') + ' s ago');
    if (sh.telemetry_seen) {
      if (sh.stalled) {
        return { level: 'stalled',
          text: 'Telemetry stalled — last frame ' + (ago != null ? ago : '(age unknown)') };
      }
      return { level: 'ok',
        text: 'Telemetry bridge running — streaming (last frame ' +
          (ago != null ? ago : 'age unknown') + ')' };
    }
    return { level: 'down',
      text: 'Telemetry bridge not running — no frames received' };
  }

  function renderBridgeHealth() {
    var el = q('res-bridge-status');
    if (!el) return;
    var s = bridgeStateFromHealth(_bridgeHealth);
    el.textContent = '⚠ ' + s.text;
    el.className = 'res-bridge-' + s.level;
    el.style.cssText =
      'padding:8px 10px;border-radius:4px;font-size:11px;font-weight:600;margin-bottom:12px;' +
      (s.level === 'ok' ? 'color:var(--green);background:rgba(78,204,163,0.10);border:1px solid var(--green);' :
       s.level === 'stalled' ? 'color:var(--red);background:rgba(233,69,96,0.12);border:1px solid var(--red);' :
       s.level === 'down' ? 'color:var(--amber);background:rgba(245,166,35,0.10);border:1px solid var(--amber);' :
       'color:var(--muted);background:rgba(136,136,170,0.08);border:1px dashed var(--muted);');
  }

  function pollBridgeHealth() {
    if (typeof fetch !== 'function') return;
    fetch('/health/slots', { cache: 'no-store' })
      .then(function (r) { return r.json(); })
      .then(function (health) { _bridgeHealth = health; })
      .catch(function () { /* keep last known wellness on a transient failure */ })
      .then(function () { renderBridgeHealth(); });
  }

  function onState(state) {
    if (!state || !state.streams) return;
    var stream0 = state.streams['0'];
    var rtosStream = findRtosStream(state);

    // Stream metadata (slot 0)
    if (stream0) {
      var s = stream0;
      var vals = s.values || {};
      var slotPrefix = 'slot0.';
      updateCell('res-seq', s.sequence != null ? s.sequence :
                              (vals[slotPrefix + 'seq'] != null ? vals[slotPrefix + 'seq'] : null));
      updateCell('res-loss', s.loss_pct != null ? s.loss_pct :
                              (vals[slotPrefix + 'loss_pct'] != null ? vals[slotPrefix + 'loss_pct'] : null), 'pct');
      updateCell('res-dropped', s.dropped != null ? s.dropped :
                                (vals[slotPrefix + 'dropped'] != null ? vals[slotPrefix + 'dropped'] : null));
      updateCell('res-received', s.received != null ? s.received :
                                (vals[slotPrefix + 'received'] != null ? vals[slotPrefix + 'received'] : null));
    }

    // IMU channels (slot 0)
    if (stream0 && stream0.values) {
      IMU_GROUPS.forEach(function (g) {
        g.items.forEach(function (item) {
          var ch = parseInt(item.key.replace('ch', ''), 10);
          var v = getChannelVal(stream0.values, ch);
          updateCell('res-' + item.key, v, item.fmt);
        });
      });
    }

    // RTOS stream (any slot containing rtos.* or system.* keys)
    var rtosHint = q('res-rtos-hint');
    var rtosSource = q('res-rtos-source');
    if (rtosStream) {
      var rv = rtosStream.values || {};
      RTOS_ALL_METRICS.forEach(function (m) {
        var id = 'res-' + m.key.replace(/\./g, '-');
        var v = rv[m.key];
        if (m.published === false) {
          // Only a real value from a future build can replace n/p.
          if (v != null) updateCell(id, v, m.fmt);
          else { var npEl = q(id); if (npEl) npEl.textContent = NOT_PUBLISHED; }
        } else {
          updateCell(id, v != null ? v : null, m.fmt);
        }
      });
      // Bridge alive: the remaining n/p cells are firmware limitations.
      if (rtosHint) {
        rtosHint.textContent = 'ⓘ ' + BRIDGE_ON_NOTE;
        rtosHint.style.display = '';
        rtosHint.style.color = 'var(--muted)';
        rtosHint.style.background = 'rgba(136,136,170,0.08)';
        rtosHint.style.border = '1px dashed var(--muted)';
      }
      if (rtosSource && rtosStream.tag) rtosSource.textContent = 'from slot "' + rtosStream.tag + '"';
    } else {
      // Bridge not running: sourced cells show NO DATA (res-no-data),
      // build-unpublished cells stay n/p, and the hint names the action.
      RTOS_ALL_METRICS.forEach(function (m) {
        if (m.published !== false) updateCell('res-' + m.key.replace(/\./g, '-'), null);
      });
      if (rtosHint) {
        rtosHint.textContent = 'ⓘ ' + BRIDGE_OFF_HINT;
        rtosHint.style.display = '';
        rtosHint.style.color = 'var(--amber)';
        rtosHint.style.background = 'rgba(245,166,35,0.10)';
        rtosHint.style.border = '1px dashed var(--amber)';
      }
      if (rtosSource) rtosSource.textContent = '';
    }

    if (stream0) _hasData = true;
  }

  // ── Export (shell uses window.__registerPlugin__) ────────────────────────
  window.__PLUGIN_INIT__ = function(api) {
    api.registerPanel('RTOS Resources', function (container) {
      container.innerHTML = buildHTML();
      api.subscribe(onState);
      // Poll bridge wellness immediately and every 3 s while the panel is
      // visible. A missing /health/slots route renders "status unavailable",
      // never a fabricated "stalled" or "not running".
      if (typeof fetch === 'function') {
        pollBridgeHealth();
        if (_bridgePollTimer == null) _bridgePollTimer = setInterval(pollBridgeHealth, 3000);
      }
    });
  };
  window.__PLUGIN_DESTROY__ = function() {
    _hasData = false;
    if (_bridgePollTimer != null) { clearInterval(_bridgePollTimer); _bridgePollTimer = null; }
    _bridgeHealth = null;
  };
  window.__registerPlugin__('RTOS Resources', window.__PLUGIN_INIT__, window.__PLUGIN_DESTROY__);

})();
