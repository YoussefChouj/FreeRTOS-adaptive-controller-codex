/**
 * telemetry-explorer-panel.js — Live telemetry explorer
 *
 * Auto-discovers all available keys across all active stream slots.
 * Renders a searchable, scrollable key-value table that updates every poll.
 * Shows schema_id, active slots, and a live key-count indicator.
 */
(function () {
  'use strict';

  var FILTER_DEBOUNCE_MS = 150;

  // ── DOM helpers ─────────────────────────────────────────────────────────
  function q(id) { return document.getElementById(id); }

  function fmtVal(v) {
    if (v == null)      return 'null';
    if (typeof v === 'boolean') return v ? 'true' : 'false';
    if (typeof v === 'number') {
      if (Number.isInteger(v)) return v.toLocaleString();
      return parseFloat(v).toFixed(4);
    }
    if (typeof v === 'string') return '"' + v + '"';
    return JSON.stringify(v);
  }

  function shortKey(k) {
    // Strip common namespace prefixes for readability
    return k.replace(/^(rtos|system|ekf|estimator|safety|mrac|legacy)\./, '');
  }

  // Telemetry keys are DWARF symbol names carried up from the firmware, so
  // they are not ours to trust in an HTML string -- quotes included, since
  // one of the two sinks below is a title="" attribute.
  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  // ── State ───────────────────────────────────────────────────────────────
  var _allKeys    = {};    // key → true   (accumulated set)
  var _filter    = '';    // current filter string
  var _debounceTimer = null;
  var _schemaId  = null;
  var _activeSlots = [];

  // ── Build panel HTML ────────────────────────────────────────────────────
  function buildHTML() {
    return [
      '<style>',
      '.te-header { display: flex; align-items: center; gap: 10px; margin-bottom: 10px; flex-wrap: wrap; }',
      '.te-meta { display: flex; gap: 10px; flex-wrap: wrap; }',
      '.te-meta-item { display: flex; flex-direction: column; gap: 1px; }',
      '.te-meta-label { font-size: 10px; color: var(--muted); }',
      '.te-meta-value { font-family: Consolas, monospace; font-size: 12px; }',
      '.te-filter-wrap { flex: 1; min-width: 180px; }',
      '.te-filter {',
      '  width: 100%; background: var(--bg); border: 1px solid var(--border);',
      '  color: var(--text); border-radius: 4px; padding: 6px 10px;',
      '  font-size: 13px; font-family: Consolas, monospace;',
      '}',
      '.te-filter:focus { outline: none; border-color: var(--accent); }',
      '.te-count { font-size: 11px; color: var(--muted); white-space: nowrap; }',
      '.te-count strong { color: var(--green); }',
      '.te-table-wrap {',
      '  max-height: 360px; overflow-y: auto;',
      '  border: 1px solid var(--border); border-radius: 4px;',
      '}',
      '.te-table { width: 100%; border-collapse: collapse; font-size: 12px; }',
      '.te-table th {',
      '  position: sticky; top: 0; background: var(--card);',
      '  text-align: left; font-size: 10px; font-weight: 600; color: var(--muted);',
      '  letter-spacing: 0.06em; text-transform: uppercase;',
      '  padding: 5px 8px; border-bottom: 1px solid var(--border);',
      '}',
      '.te-table td { padding: 4px 8px; border-bottom: 1px solid rgba(42,42,74,0.5); }',
      '.te-table tr:hover td { background: rgba(255,255,255,0.03); }',
      '.te-key { font-family: Consolas, monospace; font-size: 11px; color: var(--amber); word-break: break-all; }',
      '.te-value { font-family: Consolas, monospace; font-size: 11px; color: var(--green); }',
      '.te-slot { font-family: Consolas, monospace; font-size: 10px; color: var(--muted); }',
      '.te-type { font-size: 10px; color: var(--muted); }',
      '.te-no-data { color: var(--muted); font-size: 12px; text-align: center; padding: 30px; }',
      '.te-highlight { background: rgba(245,166,35,0.08); }',
      '.te-scroll-hint { font-size: 10px; color: var(--muted); margin-top: 4px; text-align: right; }',
      '</style>',

      // Header: meta + filter
      '<div class="te-header">',
        '<div class="te-meta">',
          '<div class="te-meta-item">',
            '<span class="te-meta-label">Schema</span>',
            '<span class="te-meta-value" id="te-schema">—</span>',
          '</div>',
          '<div class="te-meta-item">',
            '<span class="te-meta-label">Slots</span>',
            '<span class="te-meta-value" id="te-slots">—</span>',
          '</div>',
          '<div class="te-meta-item">',
            '<span class="te-meta-label">Count</span>',
            '<span class="te-meta-value"><span id="te-count" class="te-count">—</span></span>',
          '</div>',
        '</div>',
        '<div class="te-filter-wrap">',
          '<input class="te-filter" type="text" id="te-filter" placeholder="Filter keys…" autocomplete="off" spellcheck="false"/>',
        '</div>',
      '</div>',

      // Table
      '<div class="te-table-wrap" id="te-table-wrap">',
        '<table class="te-table">',
          '<thead>',
            '<tr><th>Key</th><th>Value</th><th>Slot</th><th>Type</th></tr>',
          '</thead>',
          '<tbody id="te-tbody"><tr><td colspan="4" class="te-no-data">Waiting for telemetry…</td></tr></tbody>',
        '</table>',
      '</div>',
      '<div class="te-scroll-hint">Scroll to explore</div>',
    ].join('');
  }

  // ── Collect all key-value entries from state ────────────────────────────
  function collectEntries(state) {
    if (!state) return [];
    var entries = []; // [{ key, value, slot }]
    _schemaId = state.schema_id || null;

    var slots = [];
    if (state.streams) {
      Object.keys(state.streams).forEach(function (slot) {
        slots.push(slot);
        var s = state.streams[slot];
        if (!s || !s.values) return;
        Object.keys(s.values).forEach(function (k) {
          // Accumulate key set
          _allKeys[k] = true;
          entries.push({ key: k, value: s.values[k], slot: slot });
        });
      });
    }
    _activeSlots = slots.sort(function (a, b) { return Number(a) - Number(b); });
    return entries;
  }

  // ── Render the table ────────────────────────────────────────────────────
  function renderTable(entries) {
    var filter = _filter.toLowerCase();

    // Sort: first exact prefix matches, then alphabetical
    entries = entries
      .filter(function (e) {
        if (!filter) return true;
        return e.key.toLowerCase().indexOf(filter) !== -1;
      })
      .sort(function (a, b) {
        // Put exact-prefix matches first
        var aExact = a.key.toLowerCase().startsWith(filter);
        var bExact = b.key.toLowerCase().startsWith(filter);
        if (aExact && !bExact) return -1;
        if (!aExact && bExact) return 1;
        return a.key.localeCompare(b.key);
      });

    var tbody = q('te-tbody');
    var countEl = q('te-count');
    if (!tbody) return;

    if (entries.length === 0) {
      tbody.innerHTML = '<tr><td colspan="4" class="te-no-data">' +
        (_filter ? 'No keys match "' + _filter + '"' : 'Waiting for telemetry…') +
        '</td></tr>';
      if (countEl) countEl.innerHTML = '0';
      return;
    }

    tbody.innerHTML = entries.map(function (e) {
      var type = typeof e.value;
      return '<tr>' +
        '<td class="te-key" title="' + escapeHtml(e.key) + '">' +
          escapeHtml(shortKey(e.key)) + '</td>' +
        '<td class="te-value">' + fmtVal(e.value) + '</td>' +
        '<td class="te-slot">' + e.slot + '</td>' +
        '<td class="te-type">' + type + '</td>' +
        '</tr>';
    }).join('');

    if (countEl) {
      countEl.innerHTML = '<strong>' + entries.length + '</strong> / ' + Object.keys(_allKeys).length;
    }
  }

  // ── Update meta info ─────────────────────────────────────────────────────
  function updateMeta() {
    var schemaEl = q('te-schema');
    var slotsEl  = q('te-slots');
    if (schemaEl) schemaEl.textContent = _schemaId || '—';
    if (slotsEl)  slotsEl.textContent  = _activeSlots.length > 0 ? _activeSlots.join(', ') : '—';
  }

  // ── State handler ───────────────────────────────────────────────────────
  function onState(state) {
    if (!state) return;
    var entries = collectEntries(state);
    renderTable(entries);
    updateMeta();
  }

  // ── Wire up filter input ────────────────────────────────────────────────
  function wireFilter() {
    var input = q('te-filter');
    if (!input) return;
    input.addEventListener('input', function () {
      if (_debounceTimer) clearTimeout(_debounceTimer);
      _debounceTimer = setTimeout(function () {
        _filter = input.value.trim();
        // Re-render with current filter (need current state)
        var state = window.__te_last_state;
        if (state) onState(state);
      }, FILTER_DEBOUNCE_MS);
    });
  }

  // ── Export (shell uses window.__registerPlugin__) ────────────────────────
  window.__PLUGIN_INIT__ = function(api) {
    api.registerPanel('Telemetry Explorer', function (container) {
      container.innerHTML = buildHTML();
      wireFilter();
      api.subscribe(function (state) {
        window.__te_last_state = state; // for re-render on filter change
        onState(state);
      });
    });
  };
  window.__PLUGIN_DESTROY__ = function() {
    _allKeys = {}; _filter = ''; _schemaId = null; _activeSlots = [];
    if (_debounceTimer) clearTimeout(_debounceTimer);
  };
  window.__registerPlugin__('Telemetry Explorer', window.__PLUGIN_INIT__, window.__PLUGIN_DESTROY__);

})();
