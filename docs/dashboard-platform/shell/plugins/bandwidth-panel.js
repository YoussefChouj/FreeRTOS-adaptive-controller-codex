/**
 * bandwidth-panel.js — Telemetry bandwidth and slot negotiation panel (S15 overhaul)
 *
 * Reads per-slot metadata from the S15-merged stream entries. The audit found
 * that the `received`, `dropped`, `loss_pct`, `sequence` and timestamp fields
 * are now stored EITHER:
 *   1) as top-level stream fields (last_update_ns, sequence, received, dropped, loss_pct)
 *      — populated by MultiStreamDecoder feeds
 *   2) as embedded values inside `values` (slot0.seq, slot0.received, slot0.dropped,
 *      slot0.loss_pct, slot0.t_ms) — populated by typed-slot ingest
 *
 * This panel reads BOTH and falls back to embedded values when top-level is missing.
 *
 * Shows: stream ID, effective rate (samples/sec), loss %, var count, last update ago.
 * Bar chart of bandwidth usage per slot. Total budget (80 Hz max).
 */
(function () {
  'use strict';

  // Service responses and rejection messages reach innerHTML below; escape
  // them rather than trust whatever the service or the network hands back.
  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  // ── Configuration ──────────────────────────────────────────────────────
  var MAX_BANDWIDTH_HZ = 80;
  var WARNING_THRESHOLD = 0.8;
  var CRITICAL_THRESHOLD = 0.95;

  // WiFi link capacity assumption, B/s. Source: docs/telemetry-protocol.md
  // "Wire capacity" — USART3 @ 921600 baud, BRR=0x2E => 913043 baud => 91304
  // B/s wire (10 bits/byte). The MicoAir bridges this to WiFi UDP. This is
  // the denominator for the live-link-budget percentage in this panel.
  var WIFI_LINK_CAPACITY_BPS = 91304;

  // ── State ──────────────────────────────────────────────────────────────
  var streamStates = {};  // slot → {enabled, loss_pct, effective_rate, vars, last_seen}
  var totalRate = 0;
  var usedBytesPerSec = 0; // live measured used link budget in B/s (from actual received bytes)
  var _bytesMeasurable = false; // true once >=2 bridge byte samples exist for an active slot
  var rafPending = false;
  var _apiRef = null;     // captured api ref for refresh actions
  var _unsubscribedSlots = {}; // slot → time (ns) this panel stopped it; hidden until fresh frames arrive
  // frames still in flight after a stop must not re-show the slot; newer than this, it was subscribed again
  // (Streams or Expert subscribe tab). PROPOSED value, not measured.
  var RESUBSCRIBE_GRACE_NS = 2e9;
  var _tickTimer = null;
  // slot → { bytes, ts } — previous cumulative bytes_received for the rolling B/s
  var _prevBytes = {};

  // ── DOM helpers ─────────────────────────────────────────────────────────
  function q(id) { return document.getElementById(id); }

  // ── Read metadata from a stream entry with embedded fallback ───────────
  function readMeta(s, slotId) {
    if (!s) return null;
    var vals = s.values || {};
    var slotPrefix = 'slot' + slotId + '.';
    var recv = s.received != null ? s.received :
               (vals[slotPrefix + 'received'] != null ? vals[slotPrefix + 'received'] : null);
    var drop = s.dropped != null ? s.dropped :
               (vals[slotPrefix + 'dropped'] != null ? vals[slotPrefix + 'dropped'] : null);
    var loss = s.loss_pct != null ? s.loss_pct :
               (vals[slotPrefix + 'loss_pct'] != null ? vals[slotPrefix + 'loss_pct'] : null);
    var seq  = s.sequence != null ? s.sequence :
               (vals[slotPrefix + 'seq'] != null ? vals[slotPrefix + 'seq'] : null);
    var tms  = s.source_time_ms != null ? s.source_time_ms :
               (vals[slotPrefix + 't_ms'] != null ? vals[slotPrefix + 't_ms'] : null);
    return { received: recv, dropped: drop, loss_pct: loss, sequence: seq, t_ms: tms };
  }

  // Count non-metadata keys (variables / channels) in a stream's values dict.
  // The metadata keys we recognise are: ``slotN.*`` (positional channels and
  // the receive/drop/seq/t_ms markers the wifi_bridge embeds per slot).
  // Top-level ``seq``/``received``/etc. without the slot prefix are NOT
  // produced by any current code path -- the regex still keeps them in the
  // exclusion list as a defensive measure (e.g. if a future producer drops
  // the prefix), but the production source-of-truth stays the slot-prefixed
  // form so this branch is normally unreachable. The duplicate
  // ``slot\d+\.`` alternative that appeared in earlier revisions is removed.
  function countVars(values) {
    if (!values) return 0;
    var n = 0;
    var metaRe = /^(slot\d+\.|seq|received|dropped|loss_pct|t_ms|source_time_ms)/;
    Object.keys(values).forEach(function (k) {
      if (!metaRe.test(k)) n++;
    });
    return n;
  }

  // ── Rate calculation ────────────────────────────────────────────────────
  // Track previous t_ms + seq so we can compute a rolling rate per slot.
  var _prevSample = {}; // slot → { t_ms, seq, ts }

  function calculateRate(slotId, meta) {
    // null = not computable yet (no metadata or no previous sample); the
    // table renders that as NO DATA rather than a fake 0.00 Hz.
    if (!meta || meta.t_ms == null || meta.sequence == null) return null;
    var now = Date.now();
    var prev = _prevSample[slotId];
    _prevSample[slotId] = { t_ms: meta.t_ms, seq: meta.sequence, ts: now };
    if (!prev) return null;
    var dSeq = meta.sequence - prev.seq;
    var dT   = (meta.t_ms - prev.t_ms) / 1000.0; // seconds
    if (dT <= 0 || dSeq <= 0) return 0;
    return dSeq / dT;
  }

  // ── Live bytes/s calculation ───────────────────────────────────────────
  // Live link budget, from ACTUAL received telemetry bytes (not the plan).
  // The bridge accumulates ``slotN.bytes_received`` (sum of wire 0x09+slot
  // frame lengths). True even when the subscribed payload size is unknown to
  // this panel: real received frames at the real rate ARE the used budget.
  // Returns B/s or null when not yet measurable (fewer than 2 samples).
  function calculateBytesRate(slotId, bytesReceived) {
    if (bytesReceived == null) return null;
    var now = Date.now();
    var prev = _prevBytes[slotId];
    _prevBytes[slotId] = { bytes: bytesReceived, ts: now };
    if (!prev || prev.bytes == null) return null;
    var dBytes = bytesReceived - prev.bytes;
    var dT = (now - prev.ts) / 1000.0;
    if (dT <= 0 || dBytes <= 0) return 0;
    return dBytes / dT;
  }

  // ── Chart rendering ────────────────────────────────────────────────────
  var CHART_W = 400;
  var CHART_H = 100;
  var PAD = { left: 40, right: 12, top: 10, bottom: 20 };

  function renderBandwidthChart() {
    var svg = q('bw-chart-svg');
    if (!svg) return;

    var innerW = CHART_W - PAD.left - PAD.right;
    var innerH = CHART_H - PAD.top - PAD.bottom;

    var slots = Object.keys(streamStates);
    if (slots.length === 0) {
      svg.innerHTML = '<text x="' + (CHART_W / 2) + '" y="' + (CHART_H / 2) +
        '" text-anchor="middle" style="fill:var(--gs-text-muted)" font-size="11">No active streams</text>';
      return;
    }

    var svgContent = '';

    // Background budget indicator
    var budgetBarH = 16;
    var budgetY = PAD.top;
    var budgetW = innerW;
    svgContent += '<rect x="' + PAD.left + '" y="' + budgetY + '" width="' + budgetW +
      '" height="' + budgetBarH + '" style="fill:var(--gs-hover-bg)" rx="3"/>';

    // Budget warning zones
    var warnX = PAD.left + budgetW * WARNING_THRESHOLD;
    var critX = PAD.left + budgetW * CRITICAL_THRESHOLD;
    svgContent += '<rect x="' + warnX + '" y="' + budgetY + '" width="' + (critX - warnX) +
      '" height="' + budgetBarH + '" style="fill:var(--gs-warn-bg)" rx="0"/>';
    svgContent += '<rect x="' + critX + '" y="' + budgetY + '" width="' + (budgetW - (critX - PAD.left)) +
      '" height="' + budgetBarH + '" style="fill:var(--gs-fail-bg)" rx="3"/>';

    // Budget markers
    svgContent += '<line x1="' + warnX + '" y1="' + (budgetY - 2) + '" x2="' + warnX + '" y2="' +
      (budgetY + budgetBarH + 2) + '" style="stroke:var(--gs-warn)" stroke-opacity="0.6" stroke-width="1" stroke-dasharray="2,2"/>';
    svgContent += '<line x1="' + critX + '" y1="' + (budgetY - 2) + '" x2="' + critX + '" y2="' +
      (budgetY + budgetBarH + 2) + '" style="stroke:var(--gs-fail)" stroke-opacity="0.6" stroke-width="1" stroke-dasharray="2,2"/>';

    // Used bandwidth bar
    var usedPct = Math.min(totalRate / MAX_BANDWIDTH_HZ, 1);
    var usedW = innerW * usedPct;
    var usedColor = usedPct >= CRITICAL_THRESHOLD ? 'var(--gs-fail)' :
      usedPct >= WARNING_THRESHOLD ? 'var(--gs-warn)' : 'var(--gs-ok)';
    svgContent += '<rect x="' + PAD.left + '" y="' + budgetY + '" width="' + usedW +
      '" height="' + budgetBarH + '" style="fill:' + usedColor + '" opacity="0.7" rx="3"/>';

    // Budget label
    svgContent += '<text x="' + (PAD.left + usedW / 2) + '" y="' + (budgetY + 12) +
      '" text-anchor="middle" font-size="10" style="fill:var(--gs-text)" font-weight="600" font-family="Segoe UI,sans-serif">' +
      totalRate.toFixed(1) + ' / ' + MAX_BANDWIDTH_HZ + ' Hz</text>';

    // Per-slot bars
    var barH = 14;
    var barGap = 4;
    var barAreaY = PAD.top + budgetBarH + 12;
    var barAreaH = CHART_H - barAreaY - PAD.bottom;
    var yScale = barAreaH / MAX_BANDWIDTH_HZ;

    slots.forEach(function (slot, i) {
      var ss = streamStates[slot];
      var rate = ss.effective_rate || 0;
      var barW = Math.max(2, (rate / MAX_BANDWIDTH_HZ) * innerW);
      var barY = barAreaY + i * (barH + barGap);

      var barColor = (ss.loss_pct != null && ss.loss_pct > 5) ? 'var(--gs-fail)' :
        ((ss.loss_pct != null && ss.loss_pct > 1) ? 'var(--gs-warn)' : 'var(--gs-info)');

      svgContent += '<rect x="' + PAD.left + '" y="' + barY + '" width="' + barW +
        '" height="' + barH + '" style="fill:' + barColor + '" opacity="0.6" rx="2"/>';
      svgContent += '<text x="' + (PAD.left + barW + 4) + '" y="' + (barY + 10) +
        '" font-size="9" style="fill:var(--gs-text-muted)" font-family="Consolas,monospace">S' + slot + ': ' +
        rate.toFixed(1) + 'Hz</text>';
    });

    // Y-axis labels
    var yLabels = [0, MAX_BANDWIDTH_HZ / 2, MAX_BANDWIDTH_HZ];
    yLabels.forEach(function (val) {
      var yPx = barAreaY + (MAX_BANDWIDTH_HZ - val) * yScale;
      svgContent += '<text x="' + (PAD.left - 4) + '" y="' + (yPx + 4) +
        '" text-anchor="end" font-size="9" style="fill:var(--gs-text-muted)" font-family="Consolas,monospace">' +
        val + '</text>';
    });

    svg.innerHTML = svgContent;
  }

  // ── Stream table rendering ──────────────────────────────────────────────
  function renderStreamTable() {
    var tbody = q('bw-stream-tbody');
    if (!tbody) return;

    var slots = Object.keys(streamStates);
    if (slots.length === 0) {
      tbody.innerHTML = '<tr><td colspan="7" style="color:var(--muted);text-align:center;font-size:12px">No active streams</td></tr>';
      return;
    }

    var html = '';
    var now = Date.now();
    slots.forEach(function (slot) {
      var ss = streamStates[slot];
      var ageMs = ss.last_seen ? (now - ss.last_seen) : null;
      var ageText = '—';
      var lossHtml = '';

      if (ss.last_seen == null) {
        ageText = '—';
        lossHtml = '<span style="color:var(--muted)">AWAITING DATA</span>';
      } else if (ageMs != null && ageMs > 30000) {
        ageText = '<span style="color:var(--muted)">' + (ageMs / 1000).toFixed(1) + ' s ago (degraded)</span>';
        lossHtml = ss.loss_pct != null ?
          '<span style="color:var(--muted)">NO DATA (stale ' + (ageMs / 1000).toFixed(0) + 's)</span>' :
          '<span style="color:var(--muted)">NO DATA</span>';
      } else if (ageMs != null && ageMs > 2000) {
        ageText = '<span style="color:var(--amber)">' + (ageMs / 1000).toFixed(1) + ' s ago (stale)</span>';
        lossHtml = ss.loss_pct != null ?
          '<span style="color:var(--amber)">' + ss.loss_pct.toFixed(2) + '% (stale ' + (ageMs / 1000).toFixed(1) + 's)</span>' :
          '<span style="color:var(--amber)">NOT PUBLISHED (stale)</span>';
      } else {
        ageText = (ageMs / 1000).toFixed(1) + ' s ago';
        if (ss.loss_pct != null) {
          var lossClass = ss.loss_pct > 5 ? 'loss-critical' : (ss.loss_pct > 1 ? 'loss-warn' : '');
          lossHtml = '<span class="' + lossClass + '">' + ss.loss_pct.toFixed(2) + '%</span>';
        } else {
          lossHtml = '<span style="color:var(--amber)">NOT PUBLISHED</span>';
        }
      }

      var isSlot0 = String(slot) === '0';
      var actionCell = isSlot0 ?
        '<td><button class="bw-remove-btn" data-slot="0" disabled title="Slot 0 is the protected boot layout (cannot be deleted)" style="background:var(--gs-hover-bg);border:none;color:var(--muted);padding:2px 6px;border-radius:3px;font-size:10px;cursor:not-allowed;opacity:0.4">🔒</button></td>' :
        '<td><button class="bw-remove-btn" data-slot="' + slot + '" title="Unsubscribe slot ' + slot + '" style="background:var(--gs-fail-bg);border:none;color:var(--red);padding:2px 6px;border-radius:3px;font-size:10px;cursor:pointer">✕</button></td>';

      html += '<tr>' +
        '<td><input type="checkbox" id="bw-slot-' + slot + '" ' + (ss.enabled ? 'checked' : '') + ' style="accent-color:var(--green)"/></td>' +
        '<td style="font-weight:600">Slot ' + slot + (isSlot0 ? ' <span style="font-size:10px;color:var(--muted)">(boot)</span>' : '') + '</td>' +
        '<td style="font-family:Consolas,monospace">' +
          (ss.effective_rate != null ? ss.effective_rate.toFixed(2) + ' Hz' : 'NO DATA') +
          '</td>' +
        '<td style="font-family:Consolas,monospace">' + lossHtml + '</td>' +
        '<td style="font-family:Consolas,monospace">' + (ss.vars != null ? ss.vars : '—') + '</td>' +
        '<td style="font-family:Consolas,monospace;font-size:10px">' + ageText + '</td>' +
        actionCell +
        '</tr>';
    });

    // Add total row
    var totalClass = totalRate >= MAX_BANDWIDTH_HZ * CRITICAL_THRESHOLD ? 'loss-critical' :
      totalRate >= MAX_BANDWIDTH_HZ * WARNING_THRESHOLD ? 'loss-warn' : '';
    html += '<tr style="border-top:1px solid var(--border);background:var(--gs-hover-bg)">' +
      '<td></td>' +
      '<td style="font-weight:600">TOTAL</td>' +
      '<td class="' + totalClass + '" style="font-family:Consolas,monospace;font-weight:600">' + totalRate.toFixed(2) + ' Hz</td>' +
      '<td></td>' +
      '<td></td>' +
      '<td></td>' +
      '<td></td>' +
      '</tr>';

    tbody.innerHTML = html;

    // Bind checkbox events
    slots.forEach(function (slot) {
      var cb = q('bw-slot-' + slot);
      if (cb) {
        cb.addEventListener('change', function () {
          streamStates[slot].enabled = this.checked;
          recalculateTotal();
          renderBandwidthChart();
        });
      }
    });

    // Bind remove buttons
    document.querySelectorAll('.bw-remove-btn').forEach(function (btn) {
      btn.addEventListener('click', function () {
        var slot = this.getAttribute('data-slot');
        if (String(slot) === '0') {
          var warnEl = q('bw-budget-warning') || q('bw-request-result');
          if (warnEl) {
            warnEl.innerHTML = '<span style="color:var(--red)">&#9888; Refused: Slot 0 carries flight telemetry (boot layout) and cannot be deleted.</span>';
            warnEl.style.display = '';
          }
          return;
        }

        var slotNum = parseInt(slot, 10);
        btn.disabled = true;
        btn.textContent = '…';

        var apiRef = _apiRef || (typeof window !== 'undefined' && window.__gs_shell_api__) || null;
        var p;
        if (apiRef && typeof apiRef.unsubscribeSlot === 'function') {
          p = apiRef.unsubscribeSlot(slotNum);
        } else if (typeof fetch === 'function') {
          p = fetch('/subscribe', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ slot: slotNum, divider: 0, ranges: [] })
          }).then(function (r) {
            if (!r.ok) throw new Error('Unsubscribe failed HTTP ' + r.status);
            return r.json();
          });
        } else if (apiRef && typeof apiRef.subscribeSlot === 'function') {
          p = apiRef.subscribeSlot(slotNum, 0, []);
        } else {
          p = Promise.resolve({ ok: true });
        }

        p.then(function () {
          _unsubscribedSlots[String(slot)] = Date.now() * 1e6;   // ns, see RESUBSCRIBE_GRACE_NS
          delete streamStates[slot];
          delete _prevSample[slot];
          recalculateTotal();
          renderBandwidthChart();
          renderStreamTable();
          var resEl = q('bw-request-result');
          if (resEl) {
            resEl.innerHTML = '<span style="color:var(--green)">&#10003; Slot ' + slot + ' unsubscribed (divider=0)</span>';
            setTimeout(function () { if (resEl) resEl.textContent = ''; }, 3000);
          }
          if (typeof window !== 'undefined' && typeof window.__gs_plugins_refresh__ === 'function') {
            window.__gs_plugins_refresh__();
          }
        }).catch(function (err) {
          btn.disabled = false;
          btn.textContent = '✕';
          var resEl = q('bw-request-result');
          if (resEl) {
            resEl.innerHTML = '<span style="color:var(--red)">&#10007; Failed to unsubscribe slot ' + slot + ': ' + escapeHtml(err && err.message ? err.message : err) + '</span>';
          }
        });
      });
    });
  }

  // ── Budget warning rendering ─────────────────────────────────────────────
  function renderBudgetWarning() {
    var warnEl = q('bw-budget-warning');
    if (!warnEl) return;

    var pct = (totalRate / MAX_BANDWIDTH_HZ) * 100;

    if (pct >= 100) {
      warnEl.innerHTML = '<span style="color:var(--red)">&#9888; BUDGET EXCEEDED</span> — ' +
        'Reduce stream rates or remove slots';
      warnEl.style.display = '';
    } else if (pct >= CRITICAL_THRESHOLD * 100) {
      warnEl.innerHTML = '<span style="color:var(--amber)">&#9888; CRITICAL</span> — ' +
        'At ' + pct.toFixed(0) + '% of budget (' + totalRate.toFixed(1) + '/' + MAX_BANDWIDTH_HZ + ' Hz)';
      warnEl.style.display = '';
    } else if (pct >= WARNING_THRESHOLD * 100) {
      warnEl.innerHTML = '<span style="color:var(--amber)">&#9888; WARNING</span> — ' +
        'Approaching budget limit (' + pct.toFixed(0) + '% used)';
      warnEl.style.display = '';
    } else {
      warnEl.style.display = 'none';
    }
  }

  // ── Recalculate total bandwidth ─────────────────────────────────────────
  function recalculateTotal() {
    totalRate = 0;
    Object.keys(streamStates).forEach(function (slot) {
      if (streamStates[slot].enabled) {
        totalRate += streamStates[slot].effective_rate || 0;
      }
    });
  }

  // ── Build panel HTML ────────────────────────────────────────────────────
  function buildHTML() {
    return [
      '<style>',
      '.bw-container { display:flex;flex-direction:column;gap:12px; }',
      '.bw-summary { display:flex;align-items:center;justify-content:space-between;padding:8px 12px;',
      '  background:var(--gs-inset-bg);border-radius:6px; }',
      '.bw-budget-bar { margin-top:8px; }',
      '.bw-svg { display:block;width:100%;max-width:' + CHART_W + 'px;background:var(--gs-inset-bg);border-radius:6px; }',
      '.bw-warning { font-size:12px;padding:8px 12px;border-radius:4px;background:var(--gs-warn-bg);',
      '  color:var(--amber);display:none; }',
      '.bw-table { width:100%;border-collapse:collapse;font-size:12px; }',
      '.bw-table th { text-align:left;font-size:10px;font-weight:600;color:var(--muted);',
      '  text-transform:uppercase;letter-spacing:0.06em;padding:4px 8px;',
      '  border-bottom:1px solid var(--border); }',
      '.bw-table td { padding:6px 8px; }',
      '.bw-table tr:hover td { background:var(--gs-hover-bg); }',
      '.bw-actions { display:flex;gap:8px; }',
      '.bw-btn { padding:6px 12px;border:none;border-radius:4px;font-size:12px;font-weight:600;cursor:pointer; }',
      '.bw-btn-secondary { background:var(--gs-surface-2);color:var(--text); }',
      '.bw-btn-secondary:hover { filter:brightness(1.2); }',
      '.loss-warn { color:var(--amber); }',
      '.loss-critical { color:var(--red); }',
      '</style>',

      '<div class="bw-container">',
      '  <div class="bw-summary">',
      '    <div>',
      '      <div style="font-size:11px;color:var(--muted);margin-bottom:4px">Bandwidth Budget</div>',
      '      <div style="font-size:20px;font-weight:700;font-family:Consolas,monospace">',
      '        <span id="bw-used">' + totalRate.toFixed(1) + '</span>',
      '        <span style="font-size:14px;color:var(--muted)"> / ' + MAX_BANDWIDTH_HZ + ' Hz</span>',
      '      </div>',
      '      <div style="font-size:11px;color:var(--muted);margin-top:6px" title="Measured from actual received telemetry bytes/s against the ' + WIFI_LINK_CAPACITY_BPS + ' B/s wire capacity (docs/telemetry-protocol.md). Refreshes every state poll.">Live link budget:</div>',
      '      <div style="font-size:16px;font-weight:700;font-family:Consolas,monospace">',
      '        <span id="bw-bytes">no data</span>',
      '        <span style="font-size:12px;color:var(--muted)"> / ' + WIFI_LINK_CAPACITY_BPS + ' B/s</span>',
      '        <span style="font-size:13px;color:var(--muted)">(<span id="bw-bytes-pct">—</span>)</span>',
      '      </div>',
      '    </div>',
      '    <div class="bw-actions">',
      '      <button id="bw-refresh-btn" class="bw-btn bw-btn-secondary">↻ Refresh</button>',
      '    </div>',
      '  </div>',
      /* WP-39: this tab is link health only (rate, loss, budget, stop a slot). Its old "Request Slot" form sent a
       * subscribe with no variable ranges, which the bridge refuses for slots 1-3 (wifi_bridge.py "requires explicit
       * ranges"); subscribing lives in the Streams tab (presets) and Expert subscribe (DWARF ranges). */
      '  <div class="gs-label">Link health only. To change what a slot carries use the <b>Streams</b> tab (presets, variables) ' +
        'or <b>Expert subscribe</b> (DWARF ranges, rate ladder).</div>',
      '  <div id="bw-request-result" class="gs-label" role="status"></div>',

      '  <div id="bw-budget-warning" class="bw-warning"></div>',

      '  <div class="bw-budget-bar">',
      '    <svg id="bw-chart-svg" class="bw-svg" viewBox="0 0 ' + CHART_W + ' ' + CHART_H + '"></svg>',
      '  </div>',

      '  <div>',
      '    <div style="font-size:10px;font-weight:600;color:var(--muted);text-transform:uppercase;letter-spacing:0.06em;margin-bottom:8px">Active Streams</div>',
      '    <table class="bw-table">',
      '      <thead>',
      '        <tr>',
      '          <th style="width:30px">On</th>',
      '          <th>Stream</th>',
      '          <th>Rate</th>',
      '          <th>Loss</th>',
      '          <th>Vars</th>',
      '          <th>Last</th>',
      '          <th style="width:30px"></th>',
      '        </tr>',
      '      </thead>',
      '      <tbody id="bw-stream-tbody"></tbody>',
      '    </table>',
      '  </div>',
      '</div>',
    ].join('');
  }

  // ── State handler ───────────────────────────────────────────────────────
  function onState(state) {
    if (!state || !state.streams) return;

    var now = Date.now();
    totalRate = 0;
    usedBytesPerSec = 0;
    _bytesMeasurable = false;

    Object.keys(state.streams).forEach(function (slot) {
      var stoppedAt = _unsubscribedSlots[String(slot)];
      // frames newer than the stop (plus in-flight grace) mean the slot was subscribed again elsewhere
      if (stoppedAt && state.streams[slot] && state.streams[slot].last_update_ns > stoppedAt + RESUBSCRIBE_GRACE_NS) {
        delete _unsubscribedSlots[String(slot)];
      }
      if (_unsubscribedSlots[String(slot)]) return;
      var stream = state.streams[slot];
      var meta = readMeta(stream, slot);
      var rate = calculateRate(slot, meta);
      // Live B/s from the cumulative actual-received bytes the bridge reports.
      var vals = (stream && stream.values) || {};
      var key = 'slot' + slot + '.bytes_received';
      var bytesReceived = vals[key] != null ? vals[key] : null;
      var bytesRate = calculateBytesRate(slot, bytesReceived);

      if (!streamStates[slot]) {
        streamStates[slot] = { enabled: true };
      }
      streamStates[slot].effective_rate = rate;
      streamStates[slot].bytes_per_sec = bytesRate;
      streamStates[slot].loss_pct = (meta && meta.loss_pct != null) ? meta.loss_pct : null;
      streamStates[slot].vars = countVars(stream.values || {});
      streamStates[slot].last_seen = now;

      if (streamStates[slot].enabled) {
        totalRate += rate;
        if (bytesRate != null) {
          usedBytesPerSec += bytesRate;
          _bytesMeasurable = true;
        }
      }
    });

    // Remove slots that no longer exist or are unsubscribed
    Object.keys(streamStates).forEach(function (slot) {
      if (!state.streams[slot] || _unsubscribedSlots[String(slot)]) {
        delete streamStates[slot];
        delete _prevSample[slot];
        delete _prevBytes[slot];
      }
    });

    if (!rafPending) {
      rafPending = true;
      requestAnimationFrame(function () {
        rafPending = false;
        renderBandwidthChart();
        renderStreamTable();
        renderBudgetWarning();
        renderBytesBudget();

        var usedEl = q('bw-used');
        if (usedEl) usedEl.textContent = totalRate.toFixed(1);
      });
    }
  }

  // ── Live link-budget readout ────────────────────────────────────────────
  // x% of the wire capacity, from ACTUAL received bytes (not the plan).
  // "no data" (never a fake 0) until at least two bridge samples arrive per
  // slot, so a cold link reads honestly unmeasured rather than 0 %.
  function renderBytesBudget() {
    var numEl = q('bw-bytes');
    var pctEl = q('bw-bytes-pct');
    if (!_bytesMeasurable) {
      if (numEl) numEl.textContent = 'no data';
      if (pctEl) pctEl.textContent = '—';
      return;
    }
    if (numEl) numEl.textContent = usedBytesPerSec.toFixed(0);
    if (pctEl) pctEl.textContent = (100.0 * usedBytesPerSec / WIFI_LINK_CAPACITY_BPS).toFixed(1) + ' %';
  }

  // ── Event bindings ──────────────────────────────────────────────────────
  function bindEvents(api) {
    _apiRef = api;
    var refreshBtn = q('bw-refresh-btn');
    if (refreshBtn) {
      refreshBtn.addEventListener('click', function () {
        // Force a re-render by re-pulling state
        if (api && api.getState) {
          var st = api.getState();
          if (st) {
            _prevSample = {}; // reset rate calculation
            onState(st);
            var hint = q('bw-request-result');
            if (hint) hint.innerHTML = '<span style="color:var(--green)">&#10003; Refreshed</span>';
            setTimeout(function () { if (hint) hint.textContent = ''; }, 1500);
          }
        }
      });
    }
  }

  // ── Export ──────────────────────────────────────────────────────────────
  window.__PLUGIN_INIT__ = function (api) {
    api.registerPanel('Bandwidth Manager', function (container) {
      container.innerHTML = buildHTML();
      bindEvents(api);
      renderStreamTable();
      api.subscribe(onState);
      if (_tickTimer == null) {
        _tickTimer = setInterval(function () {
          if (Object.keys(streamStates).length > 0) {
            renderStreamTable();
          }
        }, 1000);
      }
      setTimeout(function () {
        renderBandwidthChart();
        renderStreamTable();
      }, 100);
    });
  };
  window.__PLUGIN_DESTROY__ = function () {
    if (_tickTimer != null) {
      clearInterval(_tickTimer);
      _tickTimer = null;
    }
    streamStates = {};
    totalRate = 0;
    _prevSample = {};
    _unsubscribedSlots = {};
  };
  window.__registerPlugin__('Bandwidth Manager', window.__PLUGIN_INIT__, window.__PLUGIN_DESTROY__);

})();
