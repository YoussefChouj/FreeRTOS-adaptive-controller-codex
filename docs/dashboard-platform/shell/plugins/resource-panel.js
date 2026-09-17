/**
 * resource-panel.js — RTOS resource panel
 *
 * Reads runtime metrics from the typed_stream values (slot 9).
 * Keys starting with "rtos." or "system." are displayed.
 * Specific metrics from the registry (kind=resource, kind=task):
 *   Scheduler tick count      → rtos.scheduler_tick_count
 *   USART3 TX stats           → rtos.usart3_tx_count / rtos.usart3_tx_bytes
 *   FreeRTOS heap free        → rtos.heap_free_bytes
 *   Command queue depth       → rtos.cmd_queue_depth / rtos.cmd_queue_max
 *   Send task tick counter    → rtos.send_task_ticks
 *   DMA busy flag             → rtos.dma_busy
 *
 * Displays numeric values in a compact grid.
 */
(function () {
  'use strict';

  // ── Metric definitions ─────────────────────────────────────────────────
  var METRIC_GROUPS = [
    {
      label: 'Scheduler',
      items: [
        { key: 'rtos.scheduler_tick_count', label: 'Sched Ticks', unit: '',  fmt: 'int' },
        { key: 'rtos.task_count',           label: 'Tasks',       unit: '',  fmt: 'int' },
        { key: 'rtos.cpu_load_pct',         label: 'CPU Load',    unit: '%', fmt: 'pct' },
      ],
    },
    {
      label: 'Heap & Memory',
      items: [
        { key: 'rtos.heap_free_bytes',      label: 'Heap Free',   unit: 'B', fmt: 'int' },
        { key: 'rtos.heap_used_bytes',      label: 'Heap Used',   unit: 'B', fmt: 'int' },
        { key: 'rtos.stack_watermark_pct',  label: 'Max Stack',  unit: '%', fmt: 'pct' },
      ],
    },
    {
      label: 'Communication',
      items: [
        { key: 'rtos.usart3_tx_count',       label: 'USART3 TX',   unit: 'pkts', fmt: 'int' },
        { key: 'rtos.usart3_tx_bytes',      label: 'USART3 B',    unit: 'B',    fmt: 'int' },
        { key: 'rtos.usart3_errors',        label: 'UART Errors', unit: '',     fmt: 'int' },
      ],
    },
    {
      label: 'Queues & DMA',
      items: [
        { key: 'rtos.cmd_queue_depth',      label: 'Cmd Q Depth', unit: '', fmt: 'int' },
        { key: 'rtos.cmd_queue_max',        label: 'Cmd Q Max',   unit: '', fmt: 'int' },
        { key: 'rtos.dma_busy',             label: 'DMA Busy',    unit: '', fmt: 'bool' },
        { key: 'rtos.send_task_ticks',      label: 'Send Ticks',  unit: '', fmt: 'int' },
      ],
    },
  ];

  // Also scan any key starting with "rtos." or "system." dynamically
  var EXTRA_KEY_PATTERNS = ['rtos.', 'system.'];

  // ── DOM helpers ────────────────────────────────────────────────────────
  function q(id) { return document.getElementById(id); }

  function fmtVal(v, fmt) {
    if (v == null) return '—';
    if (fmt === 'int')  return parseInt(v, 10).toLocaleString();
    if (fmt === 'pct')  return parseFloat(v).toFixed(1) + '%';
    if (fmt === 'bool') return v ? 'YES' : 'NO';
    if (fmt === 'hex')  return '0x' + parseInt(v, 10).toString(16).toUpperCase();
    if (Math.abs(v) < 10) return parseFloat(v).toFixed(4);
    return parseFloat(v).toFixed(2);
  }

  // ── Build panel HTML ───────────────────────────────────────────────────
  function buildHTML() {
    var groupHTML = METRIC_GROUPS.map(function (g) {
      var cells = g.items.map(function (item) {
        return '<div style="display:flex;flex-direction:column;gap:2px;flex:1;min-width:72px">' +
          '<span style="font-size:10px;color:var(--muted)">' + item.label + '</span>' +
          '<span id="res-' + item.key.replace(/\./g, '_') + '" style="font-family:Consolas,monospace;font-size:13px">—</span>' +
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

    // Dynamic extras section
    var extraSection = [
      '<div style="margin-bottom:12px">',
      '  <div style="font-size:11px;color:var(--muted);margin-bottom:6px">Dynamic Metrics</div>',
      '  <div id="res-extra-grid" style="display:flex;gap:12px;flex-wrap:wrap"></div>',
      '</div>',
    ].join('');

    return [
      '<style>',
      '.res-warn { color: var(--amber); }',
      '.res-crit { color: var(--red); }',
      '.res-ok   { color: var(--green); }',
      '.res-extra-item { display: flex; flex-direction: column; gap: 1px; min-width: 80px; }',
      '.res-extra-key  { font-size: 9px; color: var(--muted); word-break: break-all; }',
      '.res-extra-val  { font-family: Consolas, monospace; font-size: 12px; }',
      '.res-no-data { color: var(--muted); font-size: 12px; text-align: center; padding: 16px; }',
      '</style>',

      groupHTML,
      extraSection,
    ].join('');
  }

  // ── Update a metric cell ───────────────────────────────────────────────
  function updateCell(key, value, fmt) {
    var id = 'res-' + key.replace(/\./g, '_');
    var el = q(id);
    if (!el) return;
    el.textContent = fmtVal(value, fmt);

    // Colour-code thresholds
    if (fmt === 'pct') {
      var pct = parseFloat(value);
      el.className = '';
      if (pct > 90)      el.className = 'res-crit';
      else if (pct > 75) el.className = 'res-warn';
    }
    if (key.indexOf('errors') !== -1 || key.indexOf('drop') !== -1) {
      var n = parseInt(value, 10);
      el.className = (n > 0) ? 'res-warn' : '';
    }
    if (fmt === 'bool') {
      el.style.color = value ? 'var(--amber)' : 'var(--green)';
    }
  }

  // ── Update dynamic extra keys ───────────────────────────────────────────
  var _extraKeys = {}; // key → true  (accumulated as new keys appear)

  function updateExtraKeys(vals) {
    EXTRA_KEY_PATTERNS.forEach(function (prefix) {
      Object.keys(vals).forEach(function (k) {
        if (k.indexOf(prefix) !== 0) return;
        if (METRIC_GROUPS.some(function (g) {
          return g.items.some(function (item) { return item.key === k; });
        })) return; // already covered
        _extraKeys[k] = true;
      });
    });

    var grid = q('res-extra-grid');
    if (!grid) return;

    var keys = Object.keys(_extraKeys).sort();
    if (keys.length === 0) {
      grid.innerHTML = '<div class="res-no-data">Waiting for RTOS telemetry…</div>';
      return;
    }

    grid.innerHTML = keys.map(function (k) {
      // Truncate key for display: strip prefix
      var shortLabel = k.replace(/^(rtos|system)\./, '');
      return '<div class="res-extra-item">' +
        '<span class="res-extra-key" title="' + k + '">' + shortLabel + '</span>' +
        '<span class="res-extra-val" id="res-extra-' + k.replace(/\./g, '_') + '">—</span>' +
        '</div>';
    }).join('');

    // Fill values
    keys.forEach(function (k) {
      updateCell('extra-' + k, vals[k], 'auto');
    });
  }

  // ── State handler ───────────────────────────────────────────────────────
  var _hasData = false;

  function onState(state) {
    if (!state || !state.streams) return;
    var slot9 = state.streams['9'];
    if (!slot9 || !slot9.values) return;
    var vals = slot9.values;

    var anyUpdate = false;

    METRIC_GROUPS.forEach(function (g) {
      g.items.forEach(function (item) {
        var v = vals[item.key];
        updateCell(item.key, v, item.fmt);
        if (v != null) anyUpdate = true;
      });
    });

    updateExtraKeys(vals);

    if (anyUpdate) _hasData = true;
  }

  // ── Export ───────────────────────────────────────────────────────────────
  export const name = 'RTOS Resources';

  export function init(api) {
    api.registerPanel('RTOS Resources', function (container) {
      container.innerHTML = buildHTML();
      api.subscribe(onState);
    });
  }

  export function destroy() {
    _extraKeys = {};
    _hasData = false;
  }

})();
