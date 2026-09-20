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

  // ── Configuration ──────────────────────────────────────────────────────
  var MAX_BANDWIDTH_HZ = 80;
  var WARNING_THRESHOLD = 0.8;
  var CRITICAL_THRESHOLD = 0.95;

  // ── State ──────────────────────────────────────────────────────────────
  var streamStates = {};  // slot → {enabled, loss_pct, effective_rate, vars, last_seen}
  var totalRate = 0;
  var rafPending = false;
  var _apiRef = null;     // captured api ref for refresh actions
  var _unsubscribedSlots = {}; // slot → boolean; tracks actively unsubscribed slots

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
    if (!meta || meta.t_ms == null || meta.sequence == null) return 0;
    var now = Date.now();
    var prev = _prevSample[slotId];
    _prevSample[slotId] = { t_ms: meta.t_ms, seq: meta.sequence, ts: now };
    if (!prev) return 0;
    var dSeq = meta.sequence - prev.seq;
    var dT   = (meta.t_ms - prev.t_ms) / 1000.0; // seconds
    if (dT <= 0 || dSeq <= 0) return 0;
    return dSeq / dT;
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
        '" text-anchor="middle" fill="rgba(136,136,170,0.6)" font-size="11">No active streams</text>';
      return;
    }

    var svgContent = '';

    // Background budget indicator
    var budgetBarH = 16;
    var budgetY = PAD.top;
    var budgetW = innerW;
    svgContent += '<rect x="' + PAD.left + '" y="' + budgetY + '" width="' + budgetW +
      '" height="' + budgetBarH + '" fill="rgba(255,255,255,0.05)" rx="3"/>';

    // Budget warning zones
    var warnX = PAD.left + budgetW * WARNING_THRESHOLD;
    var critX = PAD.left + budgetW * CRITICAL_THRESHOLD;
    svgContent += '<rect x="' + warnX + '" y="' + budgetY + '" width="' + (critX - warnX) +
      '" height="' + budgetBarH + '" fill="rgba(245,166,35,0.2)" rx="0"/>';
    svgContent += '<rect x="' + critX + '" y="' + budgetY + '" width="' + (budgetW - (critX - PAD.left)) +
      '" height="' + budgetBarH + '" fill="rgba(233,69,96,0.2)" rx="3"/>';

    // Budget markers
    svgContent += '<line x1="' + warnX + '" y1="' + (budgetY - 2) + '" x2="' + warnX + '" y2="' +
      (budgetY + budgetBarH + 2) + '" stroke="rgba(245,166,35,0.6)" stroke-width="1" stroke-dasharray="2,2"/>';
    svgContent += '<line x1="' + critX + '" y1="' + (budgetY - 2) + '" x2="' + critX + '" y2="' +
      (budgetY + budgetBarH + 2) + '" stroke="rgba(233,69,96,0.6)" stroke-width="1" stroke-dasharray="2,2"/>';

    // Used bandwidth bar
    var usedPct = Math.min(totalRate / MAX_BANDWIDTH_HZ, 1);
    var usedW = innerW * usedPct;
    var usedColor = usedPct >= CRITICAL_THRESHOLD ? '#e94560' :
      usedPct >= WARNING_THRESHOLD ? '#f5a623' : '#4ecca3';
    svgContent += '<rect x="' + PAD.left + '" y="' + budgetY + '" width="' + usedW +
      '" height="' + budgetBarH + '" fill="' + usedColor + '" opacity="0.7" rx="3"/>';

    // Budget label
    svgContent += '<text x="' + (PAD.left + usedW / 2) + '" y="' + (budgetY + 12) +
      '" text-anchor="middle" font-size="10" fill="#fff" font-weight="600" font-family="Segoe UI,sans-serif">' +
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

      var barColor = ss.loss_pct > 5 ? '#e94560' :
        ss.loss_pct > 1 ? '#f5a623' : '#4a9eff';

      svgContent += '<rect x="' + PAD.left + '" y="' + barY + '" width="' + barW +
        '" height="' + barH + '" fill="' + barColor + '" opacity="0.6" rx="2"/>';
      svgContent += '<text x="' + (PAD.left + barW + 4) + '" y="' + (barY + 10) +
        '" font-size="9" fill="rgba(255,255,255,0.5)" font-family="Consolas,monospace">S' + slot + ': ' +
        rate.toFixed(1) + 'Hz</text>';
    });

    // Y-axis labels
    var yLabels = [0, MAX_BANDWIDTH_HZ / 2, MAX_BANDWIDTH_HZ];
    yLabels.forEach(function (val) {
      var yPx = barAreaY + (MAX_BANDWIDTH_HZ - val) * yScale;
      svgContent += '<text x="' + (PAD.left - 4) + '" y="' + (yPx + 4) +
        '" text-anchor="end" font-size="9" fill="rgba(255,255,255,0.35)" font-family="Consolas,monospace">' +
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
    slots.forEach(function (slot) {
      var ss = streamStates[slot];
      var lossClass = ss.loss_pct > 5 ? 'loss-critical' :
        ss.loss_pct > 1 ? 'loss-warn' : '';
      var ageText = ss.last_seen ? ((Date.now() - ss.last_seen) / 1000).toFixed(1) + ' s ago' : '—';

      var isSlot0 = String(slot) === '0';
      var actionCell = isSlot0 ?
        '<td><button class="bw-remove-btn" data-slot="0" disabled title="Slot 0 is the protected boot layout (cannot be deleted)" style="background:rgba(255,255,255,0.05);border:none;color:var(--muted);padding:2px 6px;border-radius:3px;font-size:10px;cursor:not-allowed;opacity:0.4">🔒</button></td>' :
        '<td><button class="bw-remove-btn" data-slot="' + slot + '" title="Unsubscribe slot ' + slot + '" style="background:rgba(233,69,96,0.2);border:none;color:var(--red);padding:2px 6px;border-radius:3px;font-size:10px;cursor:pointer">✕</button></td>';

      html += '<tr>' +
        '<td><input type="checkbox" id="bw-slot-' + slot + '" ' + (ss.enabled ? 'checked' : '') + ' style="accent-color:var(--green)"/></td>' +
        '<td style="font-weight:600">Slot ' + slot + (isSlot0 ? ' <span style="font-size:10px;color:var(--muted)">(boot)</span>' : '') + '</td>' +
        '<td style="font-family:Consolas,monospace">' + (ss.effective_rate || 0).toFixed(2) + ' Hz</td>' +
        '<td class="' + lossClass + '" style="font-family:Consolas,monospace">' + (ss.loss_pct || 0).toFixed(2) + '%</td>' +
        '<td style="font-family:Consolas,monospace">' + (ss.vars != null ? ss.vars : '—') + '</td>' +
        '<td style="font-family:Consolas,monospace;font-size:10px;color:var(--muted)">' + ageText + '</td>' +
        actionCell +
        '</tr>';
    });

    // Add total row
    var totalClass = totalRate >= MAX_BANDWIDTH_HZ * CRITICAL_THRESHOLD ? 'loss-critical' :
      totalRate >= MAX_BANDWIDTH_HZ * WARNING_THRESHOLD ? 'loss-warn' : '';
    html += '<tr style="border-top:1px solid var(--border);background:rgba(255,255,255,0.02)">' +
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
          _unsubscribedSlots[String(slot)] = true;
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
            resEl.innerHTML = '<span style="color:var(--red)">&#10007; Failed to unsubscribe slot ' + slot + ': ' + (err && err.message ? err.message : err) + '</span>';
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
      '  background:rgba(0,0,0,0.2);border-radius:6px; }',
      '.bw-budget-bar { margin-top:8px; }',
      '.bw-svg { display:block;width:100%;max-width:' + CHART_W + 'px;background:rgba(0,0,0,0.15);border-radius:6px; }',
      '.bw-warning { font-size:12px;padding:8px 12px;border-radius:4px;background:rgba(245,166,35,0.1);',
      '  color:var(--amber);display:none; }',
      '.bw-table { width:100%;border-collapse:collapse;font-size:12px; }',
      '.bw-table th { text-align:left;font-size:10px;font-weight:600;color:var(--muted);',
      '  text-transform:uppercase;letter-spacing:0.06em;padding:4px 8px;',
      '  border-bottom:1px solid var(--border); }',
      '.bw-table td { padding:6px 8px; }',
      '.bw-table tr:hover td { background:rgba(255,255,255,0.03); }',
      '.bw-actions { display:flex;gap:8px; }',
      '.bw-btn { padding:6px 12px;border:none;border-radius:4px;font-size:12px;font-weight:600;cursor:pointer; }',
      '.bw-btn-primary { background:var(--accent);color:var(--text); }',
      '.bw-btn-primary:hover { opacity:0.85; }',
      '.bw-btn-secondary { background:rgba(255,255,255,0.1);color:var(--text); }',
      '.bw-btn-secondary:hover { background:rgba(255,255,255,0.15); }',
      '.bw-request-form { display:none;margin-top:12px;padding:12px;background:rgba(0,0,0,0.2);',
      '  border-radius:6px;border:1px solid var(--border); }',
      '.bw-request-form.visible { display:block; }',
      '.bw-request-form input { background:var(--bg);border:1px solid var(--border);color:var(--text);',
      '  padding:4px 8px;border-radius:4px;font-size:12px;width:80px;margin-right:8px; }',
      '.bw-request-form button { background:var(--green);border:none;color:var(--bg);padding:4px 12px;',
      '  border-radius:4px;font-size:12px;font-weight:600;cursor:pointer; }',
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
      '    </div>',
      '    <div class="bw-actions">',
      '      <button id="bw-refresh-btn" class="bw-btn bw-btn-secondary">↻ Refresh</button>',
      '      <button id="bw-request-btn" class="bw-btn bw-btn-primary">+ Request Slot</button>',
      '    </div>',
      '  </div>',

      '  <div id="bw-budget-warning" class="bw-warning"></div>',

      '  <div class="bw-budget-bar">',
      '    <svg id="bw-chart-svg" class="bw-svg" viewBox="0 0 ' + CHART_W + ' ' + CHART_H + '"></svg>',
      '  </div>',

      '  <div class="bw-request-form" id="bw-request-form">',
      '    <div style="font-size:11px;color:var(--muted);margin-bottom:8px">New Slot Request</div>',
      '    <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;">',
      '      <label style="font-size:11px;color:var(--text)">Rate (Hz): <input type="number" id="bw-new-rate" value="10" min="1" max="50" step="1"/></label>',
      '      <label style="font-size:11px;color:var(--text)">Channel: <input type="number" id="bw-new-channel" value="1" min="1" max="3" step="1"/></label>',
      '      <button id="bw-submit-request" style="background:var(--green);border:none;color:var(--bg);padding:4px 12px;border-radius:4px;font-size:12px;font-weight:600;cursor:pointer">Submit</button>',
      '      <button id="bw-cancel-request" style="background:rgba(255,255,255,0.1);border:none;color:var(--text);padding:4px 8px;border-radius:4px;font-size:12px;cursor:pointer">Cancel</button>',
      '    </div>',
      '    <div id="bw-request-result" style="margin-top:8px;font-size:11px;color:var(--muted)"></div>',
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

    Object.keys(state.streams).forEach(function (slot) {
      if (_unsubscribedSlots[String(slot)]) return;
      var stream = state.streams[slot];
      var meta = readMeta(stream, slot);
      var rate = calculateRate(slot, meta);

      if (!streamStates[slot]) {
        streamStates[slot] = { enabled: true };
      }
      streamStates[slot].effective_rate = rate;
      streamStates[slot].loss_pct = (meta && meta.loss_pct != null) ? meta.loss_pct : 0;
      streamStates[slot].vars = countVars(stream.values || {});
      streamStates[slot].last_seen = now;

      if (streamStates[slot].enabled) {
        totalRate += rate;
      }
    });

    // Remove slots that no longer exist or are unsubscribed
    Object.keys(streamStates).forEach(function (slot) {
      if (!state.streams[slot] || _unsubscribedSlots[String(slot)]) {
        delete streamStates[slot];
        delete _prevSample[slot];
      }
    });

    if (!rafPending) {
      rafPending = true;
      requestAnimationFrame(function () {
        rafPending = false;
        renderBandwidthChart();
        renderStreamTable();
        renderBudgetWarning();

        var usedEl = q('bw-used');
        if (usedEl) usedEl.textContent = totalRate.toFixed(1);
      });
    }
  }

  // ── Event bindings ──────────────────────────────────────────────────────
  function bindEvents(api) {
    _apiRef = api;
    var requestBtn = q('bw-request-btn');
    var requestForm = q('bw-request-form');
    if (requestBtn && requestForm) {
      requestBtn.addEventListener('click', function () {
        requestForm.classList.add('visible');
      });
    }

    var cancelBtn = q('bw-cancel-request');
    if (cancelBtn && requestForm) {
      cancelBtn.addEventListener('click', function () {
        requestForm.classList.remove('visible');
        q('bw-request-result').textContent = '';
      });
    }

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

    var submitBtn = q('bw-submit-request');
    if (submitBtn) {
      submitBtn.addEventListener('click', function () {
        var rate = parseFloat(q('bw-new-rate').value) || 10;
        var channel = parseInt(q('bw-new-channel').value, 10) || 0;

        var resultEl = q('bw-request-result');
        resultEl.innerHTML = '<span style="color:var(--amber)">&#8987; Requesting slot…</span>';

        // Translate UI inputs into a real subscribe request:
        //   channel → slot id (1..3 are the typed-stream slots; the firmware
        //     rejects 9..12 with "E:bad slot" since SUBSCRIBE_MAX_SLOTS = 4)
        //   rate    → divider (Send_Task Hz ≈ 200, divider ≈ 200 / rate)
        // Clamp to known slot range and divider bounds.
        var slot = (channel >= 1 && channel <= 3) ? channel : 1;
        delete _unsubscribedSlots[String(slot)];
        var divider = Math.max(1, Math.min(255, Math.round(200 / rate)));
        var apiRef = (typeof window !== 'undefined' && window.__gs_shell_api__) || null;
        if (apiRef && typeof apiRef.subscribeSlot === 'function') {
          apiRef.subscribeSlot(slot, divider, [])
            .then(function (res) {
              resultEl.innerHTML = '<span style="color:var(--green)">&#10003; Subscribed slot ' +
                slot + ' divider=' + divider + ' via ' + (res && res.via ? res.via : '/subscribe') + '</span>';
              if (typeof window.__gs_plugins_refresh__ === 'function') window.__gs_plugins_refresh__();
            })
            .catch(function (err) {
              resultEl.innerHTML = '<span style="color:var(--red)">&#10007; Subscribe failed: ' +
                (err && err.message ? err.message : err) + '</span>';
            });
        } else {
          // Fallback: refresh local view even if /subscribe is unavailable.
          resultEl.innerHTML = '<span style="color:var(--amber)">&#9888; subscribeSlot unavailable; refresh to see live data</span>';
        }

        setTimeout(function () {
          requestForm.classList.remove('visible');
        }, 1500);
      });
    }
  }

  // ── Export ──────────────────────────────────────────────────────────────
  window.__PLUGIN_INIT__ = function (api) {
    api.registerPanel('Bandwidth Manager', function (container) {
      container.innerHTML = buildHTML();
      bindEvents(api);
      api.subscribe(onState);
      setTimeout(function () {
        renderBandwidthChart();
        renderStreamTable();
      }, 100);
    });
  };
  window.__PLUGIN_DESTROY__ = function () {
    streamStates = {};
    totalRate = 0;
    _prevSample = {};
    _unsubscribedSlots = {};
  };
  window.__registerPlugin__('Bandwidth Manager', window.__PLUGIN_INIT__, window.__PLUGIN_DESTROY__);

})();
