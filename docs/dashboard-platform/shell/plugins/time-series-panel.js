/**
 * time-series-panel.js — Real-time time-series plot panel
 *
 * Configurable real-time telemetry plotting with bounded history buffer,
 * pause, step-back replay, region zoom, and dynamic variable discovery.
 *
 * Requirements (Tasks B1-B3):
 * - B1: Position traces for X, Y, Z (c.earth_x, c.earth_y, c.altitude from Frame C).
 *       No EKF position faking; honest reporting when data is missing.
 * - B2: Configurable variable picker (dropdown with checkboxes). Populated
 *       dynamically from variables actually streaming in state.streams.
 *       Stable selection across reconnects.
 * - B3: Recording controls: Pause, step back / replay, region zoom.
 *       Bounded client-side ring buffer (RING_BUFFER_SIZE = 3000 samples;
 *       covers 30.0s @ 100 Hz, 37.5s @ 80 Hz).
 *       Safety rule: Paused state is continuously, unmistakably obvious.
 *       Resume jumps directly to live.
 * - Truthful: No synthetic placeholder data or demo waveforms.
 */
(function () {
  'use strict';

  // ── Configuration ──────────────────────────────────────────────────────
  var RING_BUFFER_SIZE = 3000;       // Max samples stored in client memory (30s @ 100Hz)
  var DEFAULT_DISPLAY_SAMPLES = 200; // Number of samples visible in live viewport
  var THROTTLE_MS = 40;              // Max 25 Hz update cadence for chart rendering
  var STORAGE_KEY = 'ts_panel_selected_vars_v2';

  var CHART_W = 600;
  var CHART_H = 220;
  var PAD = { left: 56, right: 16, top: 26, bottom: 28 };

  // Distinct color palette for auto-assigning to discovered variables
  var COLOR_PALETTE = [
    '#ff5964', '#35a7ff', '#38b000', '#f5a623', '#9b59b6',
    '#4ecca3', '#e94560', '#ff9f1c', '#2ec4b6', '#4cc9f0',
    '#f72585', '#7209b7', '#4361ee', '#06d6a0', '#ffd166',
    '#a8dadc', '#1d3557', '#e63946', '#457b9d', '#2a9d8f'
  ];

  // Default known flight variables with physical units & display metadata
  var DEFAULT_DEFS = {
    'c.earth_x':       { key: 'c.earth_x',       label: 'Pos X',        unit: 'm',   color: '#ff5964', defaultEnabled: true },
    'c.earth_y':       { key: 'c.earth_y',       label: 'Pos Y',        unit: 'm',   color: '#35a7ff', defaultEnabled: true },
    'c.altitude':      { key: 'c.altitude',      label: 'Pos Z (Alt)',  unit: 'm',   color: '#38b000', defaultEnabled: true },
    'status.roll_deg':  { key: 'status.roll_deg',  label: 'Roll',         unit: 'deg', color: '#4a9eff', defaultEnabled: true },
    'status.pitch_deg': { key: 'status.pitch_deg', label: 'Pitch',        unit: 'deg', color: '#4ecca3', defaultEnabled: true },
    'status.yaw_deg':   { key: 'status.yaw_deg',   label: 'Yaw',          unit: 'deg', color: '#f5a623', defaultEnabled: false },
    'mrac.roll.e':      { key: 'mrac.roll.e',      label: 'Roll Err',     unit: 'rad', color: '#e94560', defaultEnabled: false },
    'status.vbat':      { key: 'status.vbat',      label: 'Battery',      unit: 'V',   color: '#9b59b6', defaultEnabled: false },
  };

  // Known channel aliases for legacy or alternate streaming formats
  var CHANNEL_ALIASES = {
    'c.earth_x': ['ano_of.earth_x', 'earth_x', 'pos_x', 'slot3.c.earth_x'],
    'c.earth_y': ['ano_of.earth_y', 'earth_y', 'pos_y', 'slot3.c.earth_y'],
    'c.altitude': ['ano_of.of_alt_cm_m', 'ano_of.of_alt_cm', 'altitude', 'pos_z', 'slot3.c.altitude'],
    'status.roll_deg': ['imu_data.rol', 'ahrs.rol', 'ch0', 'c.roll'],
    'status.pitch_deg': ['imu_data.pit', 'ahrs.pit', 'ch1', 'c.pitch'],
    'status.yaw_deg': ['imu_data.yaw', 'ahrs.yaw', 'ch2', 'c.yaw'],
    'mrac.roll.e': ['mrac_state.roll.e', 'mrac.roll_gamma', 'ch10'],
    'status.vbat': ['real_voltage', 'ch11'],
  };

  // ── State ──────────────────────────────────────────────────────────────
  var knownVars = {};         // key → { key, label, unit, color, enabled, slot }
  var ringBuffers = {};       // key → Array of (number | null)
  var sampleTimestamps = [];  // Array of Date.now() timestamps for each sample
  var lastIngestTime = 0;

  // Recording & Playback state
  var isPaused = false;
  var pauseSampleIdx = -1;    // View cursor index into sampleTimestamps
  var isReplaying = false;
  var replayInterval = null;

  // View & Zoom state
  var zoomWindow = null;      // { startIdx, endIdx } or null
  var isBoxDragging = false;
  var dragStartX = 0;
  var dragCurrentX = 0;

  // UI state
  var isPickerOpen = false;
  var pickerFilter = '';
  var rafPending = false;
  var selectedKeysCache = null;

  // ── Persistence Helpers ────────────────────────────────────────────────
  function loadSavedSelection() {
    if (selectedKeysCache) return selectedKeysCache;
    try {
      if (typeof localStorage !== 'undefined' && localStorage.getItem) {
        var raw = localStorage.getItem(STORAGE_KEY);
        if (raw) {
          selectedKeysCache = JSON.parse(raw);
          return selectedKeysCache;
        }
      }
    } catch (e) {}
    // Defaults if nothing stored
    selectedKeysCache = {};
    Object.keys(DEFAULT_DEFS).forEach(function (k) {
      if (DEFAULT_DEFS[k].defaultEnabled) {
        selectedKeysCache[k] = true;
      }
    });
    return selectedKeysCache;
  }

  function saveSelection() {
    selectedKeysCache = {};
    Object.keys(knownVars).forEach(function (k) {
      if (knownVars[k].enabled) {
        selectedKeysCache[k] = true;
      }
    });
    try {
      if (typeof localStorage !== 'undefined' && localStorage.setItem) {
        localStorage.setItem(STORAGE_KEY, JSON.stringify(selectedKeysCache));
      }
    } catch (e) {}
  }

  // Initialize known variables with defaults
  function initDefaults() {
    var saved = loadSavedSelection();
    Object.keys(DEFAULT_DEFS).forEach(function (k) {
      var d = DEFAULT_DEFS[k];
      knownVars[k] = {
        key: d.key,
        label: d.label,
        unit: d.unit,
        color: d.color,
        enabled: saved[k] !== undefined ? Boolean(saved[k]) : d.defaultEnabled,
        slot: null,
      };
      if (!ringBuffers[k]) ringBuffers[k] = [];
    });
  }
  initDefaults();

  // ── DOM Helpers ────────────────────────────────────────────────────────
  function q(id) { return document.getElementById(id); }

  function clamp(val, min, max) {
    return Math.max(min, Math.min(max, val));
  }

  // ── Dynamic Variable Discovery & State Value Extraction ────────────────
  // The Telemetry Explorer surfaces each slot's values under their raw
  // DWARF spelling, namespace-prefixed by slot: ``slot0.imu_data.rol``,
  // ``slot0.mrac_state.roll.e``, … (the adapter's spec aliases such as
  // ``status.roll_deg`` are an *additional* spelling). Panels must bind to
  // whichever spelling the stream carries, so every bare key/alias lookup
  // tolerates (and strips) the ``slot<N>.`` namespace prefix. Without this
  // seam the panels honestly render "waiting for data" while the Explorer
  // shows every variable streaming (AUDIT_2026-09-21 Bug 4 "global pattern").
  var SLOT_PREFIX_RE = /^slot\d+\./;

  function slotVal(slotValues, want) {
    if (slotValues == null) return undefined;
    if (slotValues[want] !== undefined) return slotValues[want];
    // Fall back: a key whose slot-prefix-stripped form matches the want.
    var stripped = want.replace(SLOT_PREFIX_RE, '');
    for (var k in slotValues) {
      if (k.replace(SLOT_PREFIX_RE, '') === stripped) return slotValues[k];
    }
    return undefined;
  }

  // ano_of.of_alt_cm is in cm on the wire; convert to meters for Z display.
  function numericSlotValue(slotValues, want) {
    var v = slotVal(slotValues, want);
    if (v === null || v === undefined) return undefined;
    var n = Number(v);
    if (isNaN(n)) return undefined;
    if (want === 'ano_of.of_alt_cm') return n / 100.0;
    return n;
  }

  function getValFromState(state, key) {
    if (!state || !state.streams) return null;
    var slots = Object.keys(state.streams);

    // 1. Direct search across all streaming slots
    for (var i = 0; i < slots.length; i++) {
      var v = numericSlotValue(state.streams[slots[i]].values, key);
      if (v !== undefined) return v;
    }

    // 2. Search aliases
    var alts = CHANNEL_ALIASES[key];
    if (alts) {
      for (var a = 0; a < alts.length; a++) {
        var altKey = alts[a];
        for (var j = 0; j < slots.length; j++) {
          var val = numericSlotValue(state.streams[slots[j]].values, altKey);
          if (val !== undefined) return val;
        }
      }
    }
    return null;
  }

  function discoverStreamingVariables(state) {
    if (!state || !state.streams) return false;
    var saved = loadSavedSelection();
    var newDiscovered = false;
    var slots = Object.keys(state.streams);

    for (var i = 0; i < slots.length; i++) {
      var slotId = slots[i];
      var stream = state.streams[slotId];
      if (!stream || !stream.values) continue;

      var keys = Object.keys(stream.values);
      for (var k = 0; k < keys.length; k++) {
        var key = keys[k];
        var rawVal = stream.values[key];

        // Only register scalar numbers (skip complex objects or array containers)
        if (typeof rawVal !== 'number' && isNaN(Number(rawVal))) continue;
        if (typeof rawVal === 'object' && rawVal !== null) continue;

        if (!knownVars[key]) {
          var def = DEFAULT_DEFS[key];
          var nextColor = COLOR_PALETTE[Object.keys(knownVars).length % COLOR_PALETTE.length];
          knownVars[key] = {
            key: key,
            label: def ? def.label : key,
            unit: def ? def.unit : '',
            color: def ? def.color : nextColor,
            enabled: saved[key] !== undefined ? Boolean(saved[key]) : false,
            slot: slotId,
          };
          if (!ringBuffers[key]) ringBuffers[key] = [];
          newDiscovered = true;
        } else {
          knownVars[key].slot = slotId;
        }
      }
    }
    return newDiscovered;
  }

  // ── Viewport Calculation ───────────────────────────────────────────────
  function getActiveViewRange() {
    var total = sampleTimestamps.length;
    if (total === 0) return { start: 0, end: 0, total: 0 };

    var endIdx, startIdx;
    if (zoomWindow) {
      startIdx = clamp(zoomWindow.startIdx, 0, total - 1);
      endIdx = clamp(zoomWindow.endIdx, startIdx, total - 1);
    } else if (isPaused) {
      endIdx = clamp(pauseSampleIdx, 0, total - 1);
      startIdx = Math.max(0, endIdx - DEFAULT_DISPLAY_SAMPLES + 1);
    } else {
      endIdx = total - 1;
      startIdx = Math.max(0, endIdx - DEFAULT_DISPLAY_SAMPLES + 1);
    }
    return { start: startIdx, end: endIdx, total: total };
  }

  // ── SVG Chart Rendering ────────────────────────────────────────────────
  function renderChart() {
    var svg = q('ts-chart-svg');
    if (!svg) return;

    var rangeInfo = getActiveViewRange();
    var innerW = CHART_W - PAD.left - PAD.right;
    var innerH = CHART_H - PAD.top - PAD.bottom;

    if (rangeInfo.total === 0) {
      svg.innerHTML = '<text x="' + (CHART_W / 2) + '" y="' + (CHART_H / 2) +
        '" text-anchor="middle" fill="rgba(136,136,170,0.6)" font-size="12">Waiting for telemetry data…</text>';
      return;
    }

    var enabledKeys = Object.keys(knownVars).filter(function (k) { return knownVars[k].enabled; });
    if (enabledKeys.length === 0) {
      svg.innerHTML = '<text x="' + (CHART_W / 2) + '" y="' + (CHART_H / 2) +
        '" text-anchor="middle" fill="rgba(136,136,170,0.6)" font-size="12">No variables selected — open Variables menu to choose</text>';
      return;
    }

    // Collect all visible valid numerical values to compute Y scaling
    var allValues = [];
    enabledKeys.forEach(function (k) {
      var buf = ringBuffers[k] || [];
      for (var i = rangeInfo.start; i <= rangeInfo.end; i++) {
        var v = buf[i];
        if (v != null && !isNaN(v)) {
          allValues.push(v);
        }
      }
    });

    if (allValues.length === 0) {
      svg.innerHTML = '<text x="' + (CHART_W / 2) + '" y="' + (CHART_H / 2) +
        '" text-anchor="middle" fill="rgba(136,136,170,0.6)" font-size="12">Waiting for data on selected variables…</text>';
      return;
    }

    var minY = Math.min.apply(null, allValues);
    var maxY = Math.max.apply(null, allValues);
    var ySpan = maxY - minY || 1.0;
    var yPad = ySpan * 0.1;
    minY -= yPad;
    maxY += yPad;
    ySpan = maxY - minY;

    var svgContent = '';

    // Chart Background Grid
    var gridLines = 5;
    for (var g = 0; g <= gridLines; g++) {
      var yPct = g / gridLines;
      var yPx = PAD.top + innerH * (1 - yPct);
      var val = minY + ySpan * yPct;
      svgContent += '<line x1="' + PAD.left + '" y1="' + yPx + '" x2="' +
        (CHART_W - PAD.right) + '" y2="' + yPx + '" stroke="rgba(255,255,255,0.08)" stroke-width="1"/>';
      svgContent += '<text x="' + (PAD.left - 6) + '" y="' + (yPx + 3) +
        '" text-anchor="end" font-size="9" fill="rgba(255,255,255,0.4)" font-family="Consolas,monospace">' +
        val.toFixed(2) + '</text>';
    }

    // Zero reference line
    if (minY < 0 && maxY > 0) {
      var zeroY = PAD.top + innerH * (1 - (0 - minY) / ySpan);
      svgContent += '<line x1="' + PAD.left + '" y1="' + zeroY + '" x2="' +
        (CHART_W - PAD.right) + '" y2="' + zeroY + '" stroke="rgba(255,255,255,0.22)" stroke-width="1" stroke-dasharray="4,3"/>';
    }

    // Plot traces for each enabled variable
    var sampleCount = Math.max(1, rangeInfo.end - rangeInfo.start);
    enabledKeys.forEach(function (k) {
      var meta = knownVars[k];
      var buf = ringBuffers[k] || [];
      var segments = [];
      var curSegment = [];
      var lastValidPt = null;

      for (var i = rangeInfo.start; i <= rangeInfo.end; i++) {
        var val = buf[i];
        if (val != null && !isNaN(val)) {
          var xPct = (i - rangeInfo.start) / sampleCount;
          var yPct = (val - minY) / ySpan;
          var xPx = PAD.left + innerW * xPct;
          var yPx = PAD.top + innerH * (1 - yPct);
          curSegment.push(xPx.toFixed(1) + ',' + yPx.toFixed(1));
          lastValidPt = { x: xPx, y: yPx };
        } else {
          if (curSegment.length > 0) {
            segments.push(curSegment);
            curSegment = [];
          }
        }
      }
      if (curSegment.length > 0) {
        segments.push(curSegment);
      }

      // Draw non-synthetic polyline segments (honest gap handling)
      segments.forEach(function (pts) {
        if (pts.length >= 2) {
          svgContent += '<polyline points="' + pts.join(' ') + '" fill="none" stroke="' +
            meta.color + '" stroke-width="1.8" opacity="0.9"/>';
        } else if (pts.length === 1) {
          var coord = pts[0].split(',');
          svgContent += '<circle cx="' + coord[0] + '" cy="' + coord[1] + '" r="2" fill="' + meta.color + '"/>';
        }
      });

      // Marker on latest visible value
      if (lastValidPt) {
        svgContent += '<circle cx="' + lastValidPt.x.toFixed(1) + '" cy="' + lastValidPt.y.toFixed(1) +
          '" r="3.5" fill="' + meta.color + '"/>';
      }
    });

    // X-Axis tick labels (relative time / sample offset)
    var xTicks = 5;
    var latestT = sampleTimestamps[sampleTimestamps.length - 1] || Date.now();
    for (var t = 0; t <= xTicks; t++) {
      var xPct = t / xTicks;
      var xPx = PAD.left + innerW * xPct;
      var sampleIdx = Math.round(rangeInfo.start + xPct * sampleCount);
      var sampleT = sampleTimestamps[sampleIdx];
      var timeText = '';
      if (sampleT) {
        var secAgo = (latestT - sampleT) / 1000;
        timeText = secAgo <= 0.05 ? '0s' : '-' + secAgo.toFixed(1) + 's';
      } else {
        timeText = '#' + sampleIdx;
      }
      svgContent += '<text x="' + xPx.toFixed(1) + '" y="' + (CHART_H - 8) +
        '" text-anchor="middle" font-size="9" fill="rgba(255,255,255,0.4)" font-family="Consolas,monospace">' +
        timeText + '</text>';
    }

    // Zoom Indicator Header
    if (zoomWindow) {
      svgContent += '<rect x="' + PAD.left + '" y="4" width="' + innerW + '" height="18" fill="rgba(78,204,163,0.15)" rx="3"/>';
      svgContent += '<text x="' + (CHART_W / 2) + '" y="16" text-anchor="middle" font-size="10" fill="#4ecca3" font-family="Segoe UI,sans-serif" font-weight="600">' +
        '🔍 Zoomed Region: ' + (rangeInfo.end - rangeInfo.start + 1) + ' samples (' +
        ((sampleTimestamps[rangeInfo.end] - sampleTimestamps[rangeInfo.start]) / 1000).toFixed(2) + 's)' +
        '</text>';
    }

    // Drag-to-zoom selection rectangle
    if (isBoxDragging) {
      var leftX = Math.max(PAD.left, Math.min(dragStartX, dragCurrentX));
      var rightX = Math.min(CHART_W - PAD.right, Math.max(dragStartX, dragCurrentX));
      var boxW = rightX - leftX;
      if (boxW > 2) {
        svgContent += '<rect x="' + leftX + '" y="' + PAD.top + '" width="' + boxW + '" height="' + innerH +
          '" fill="rgba(78,204,163,0.25)" stroke="#4ecca3" stroke-width="1.5" stroke-dasharray="4,3"/>';
      }
    }

    svg.innerHTML = svgContent;
  }

  // ── Legend Update ──────────────────────────────────────────────────────
  function updateLegend() {
    var legendEl = q('ts-legend');
    if (!legendEl) return;

    var rangeInfo = getActiveViewRange();
    var activeIdx = isPaused ? rangeInfo.end : (sampleTimestamps.length - 1);

    var html = '';
    var enabledKeys = Object.keys(knownVars).filter(function (k) { return knownVars[k].enabled; });

    enabledKeys.forEach(function (k) {
      var v = knownVars[k];
      var buf = ringBuffers[k] || [];
      var val = (activeIdx >= 0 && buf[activeIdx] != null) ? buf[activeIdx] : null;
      var valStr = '— (no data)';
      if (val != null) {
        valStr = val.toFixed(4) + (v.unit ? ' ' + v.unit : '');
      }

      html += '<div class="ts-legend-item">' +
        '<div class="ts-legend-dot" style="background:' + v.color + '"></div>' +
        '<span class="ts-legend-name">' + v.label + '</span>' +
        '<span class="ts-legend-val">' + valStr + '</span>' +
        '</div>';
    });

    if (enabledKeys.length === 0) {
      html = '<span style="color:var(--muted);font-size:11px">No variables selected</span>';
    }
    legendEl.innerHTML = html;
  }

  // ── Variable Picker Dropdown UI ────────────────────────────────────────
  function updatePickerUI() {
    var countEl = q('ts-picker-count');
    var listEl = q('ts-picker-list');
    var menuEl = q('ts-picker-menu');
    if (!countEl || !listEl) return;

    var allKeys = Object.keys(knownVars).sort();
    var enabledCount = allKeys.filter(function (k) { return knownVars[k].enabled; }).length;
    countEl.textContent = enabledCount + '/' + allKeys.length;

    if (menuEl) {
      menuEl.style.display = isPickerOpen ? 'block' : 'none';
    }

    var filter = pickerFilter.toLowerCase().trim();
    var html = '';

    allKeys.forEach(function (k) {
      var v = knownVars[k];
      if (filter && k.toLowerCase().indexOf(filter) === -1 && v.label.toLowerCase().indexOf(filter) === -1) {
        return;
      }
      var buf = ringBuffers[k] || [];
      var lastVal = buf.length > 0 ? buf[buf.length - 1] : null;
      var lastValStr = lastVal != null ? lastVal.toFixed(3) : '—';
      var checked = v.enabled ? 'checked' : '';

      html += '<label class="ts-picker-item">' +
        '<input type="checkbox" data-key="' + k + '" ' + checked + ' style="accent-color:' + v.color + '"/>' +
        '<span class="ts-swatch" style="background:' + v.color + '"></span>' +
        '<span class="ts-picker-label">' + v.label + '</span>' +
        '<span class="ts-picker-key">' + k + '</span>' +
        '<span class="ts-picker-cur">' + lastValStr + '</span>' +
        '</label>';
    });

    if (html === '') {
      html = '<div style="padding:12px;color:var(--muted);font-size:11px;text-align:center">No matching variables</div>';
    }
    listEl.innerHTML = html;
  }

  // ── Paused & Timeline Status UI ────────────────────────────────────────
  function updateControlsUI() {
    var root = q('ts-panel-root');
    var banner = q('ts-paused-banner');
    var bannerAge = q('ts-paused-age');
    var btnPause = q('ts-btn-pause');
    var btnResume = q('ts-btn-resume');
    var playbackGroup = q('ts-playback-controls');
    var liveBadge = q('ts-live-badge');
    var scrubber = q('ts-scrubber');
    var timeStart = q('ts-time-start');
    var timeEnd = q('ts-time-end');

    var total = sampleTimestamps.length;
    var latestT = total > 0 ? sampleTimestamps[total - 1] : Date.now();

    if (isPaused) {
      if (root) root.classList.add('is-paused');
      if (banner) banner.style.display = 'flex';
      if (btnPause) btnPause.style.display = 'none';
      if (btnResume) btnResume.style.display = 'inline-flex';
      if (playbackGroup) playbackGroup.style.display = 'inline-flex';

      var cursorIdx = clamp(pauseSampleIdx, 0, total - 1);
      var viewT = total > 0 ? sampleTimestamps[cursorIdx] : latestT;
      var ageSec = Math.max(0, (latestT - viewT) / 1000).toFixed(1);
      var offset = cursorIdx - (total - 1);

      if (bannerAge) {
        bannerAge.textContent = isReplaying ?
          'REPLAYING (' + ageSec + 's behind live)' :
          ageSec + 's behind live (offset: ' + offset + ' samples)';
      }
      if (liveBadge) {
        liveBadge.className = 'ts-live-badge paused';
        liveBadge.textContent = '⏸ FROZEN (' + ageSec + 's)';
      }
    } else {
      if (root) root.classList.remove('is-paused');
      if (banner) banner.style.display = 'none';
      if (btnPause) btnPause.style.display = 'inline-flex';
      if (btnResume) btnResume.style.display = 'none';
      if (playbackGroup) playbackGroup.style.display = 'none';
      if (liveBadge) {
        liveBadge.className = 'ts-live-badge live';
        liveBadge.textContent = '● LIVE';
      }
    }

    if (scrubber) {
      scrubber.max = Math.max(0, total - 1);
      scrubber.value = isPaused ? clamp(pauseSampleIdx, 0, total - 1) : Math.max(0, total - 1);
    }
    if (timeStart && total > 0) {
      var oldestT = sampleTimestamps[0];
      var totalSec = ((latestT - oldestT) / 1000).toFixed(1);
      timeStart.textContent = '-' + totalSec + 's (' + total + ' samples)';
    }
    if (timeEnd) {
      timeEnd.textContent = isPaused ? 'Cursor: sample #' + pauseSampleIdx : 'Live (0.0s)';
    }
  }

  // ── Recording Control Actions ──────────────────────────────────────────
  function pausePlot() {
    if (isPaused) return;
    isPaused = true;
    pauseSampleIdx = Math.max(0, sampleTimestamps.length - 1);
    updateControlsUI();
    renderChart();
    updateLegend();
  }

  function resumeLive() {
    isPaused = false;
    isReplaying = false;
    if (replayInterval) {
      clearInterval(replayInterval);
      replayInterval = null;
    }
    pauseSampleIdx = Math.max(0, sampleTimestamps.length - 1);
    zoomWindow = null; // Resume returns to live view
    updateControlsUI();
    renderChart();
    updateLegend();
  }

  function stepOffset(delta) {
    if (!isPaused) pausePlot();
    var total = sampleTimestamps.length;
    if (total === 0) return;
    pauseSampleIdx = clamp(pauseSampleIdx + delta, 0, total - 1);
    updateControlsUI();
    renderChart();
    updateLegend();
  }

  function toggleReplay() {
    if (!isPaused) pausePlot();
    if (isReplaying) {
      isReplaying = false;
      if (replayInterval) {
        clearInterval(replayInterval);
        replayInterval = null;
      }
      var playBtn = q('ts-btn-play');
      if (playBtn) playBtn.textContent = '▶ Replay';
      updateControlsUI();
      return;
    }

    isReplaying = true;
    var playBtn2 = q('ts-btn-play');
    if (playBtn2) playBtn2.textContent = '⏸ Pause Replay';

    replayInterval = setInterval(function () {
      var total = sampleTimestamps.length;
      if (pauseSampleIdx >= total - 1) {
        // Reached live end
        toggleReplay();
        return;
      }
      pauseSampleIdx = clamp(pauseSampleIdx + 2, 0, total - 1);
      updateControlsUI();
      renderChart();
      updateLegend();
    }, 40);
  }

  // ── Zoom Actions ───────────────────────────────────────────────────────
  function zoomIn() {
    var range = getActiveViewRange();
    var span = range.end - range.start;
    if (span <= 10) return;
    var newSpan = Math.max(10, Math.round(span * 0.6));
    var mid = Math.round((range.start + range.end) / 2);
    zoomWindow = {
      startIdx: Math.max(0, mid - Math.round(newSpan / 2)),
      endIdx: Math.min(range.total - 1, mid + Math.round(newSpan / 2))
    };
    renderChart();
  }

  function zoomOut() {
    var range = getActiveViewRange();
    var span = range.end - range.start;
    var newSpan = Math.round(span * 1.6);
    var mid = Math.round((range.start + range.end) / 2);
    var newStart = Math.max(0, mid - Math.round(newSpan / 2));
    var newEnd = Math.min(range.total - 1, mid + Math.round(newSpan / 2));
    if (newStart <= 0 && newEnd >= range.total - 1) {
      zoomWindow = null; // Full buffer restored
    } else {
      zoomWindow = { startIdx: newStart, endIdx: newEnd };
    }
    renderChart();
  }

  function resetZoom() {
    zoomWindow = null;
    renderChart();
  }

  // ── State Handler (Ingestion) ──────────────────────────────────────────
  function onState(state) {
    if (!state || !state.streams) return;

    var now = Date.now();
    var newDiscovered = discoverStreamingVariables(state);

    // Ingest sample into bounded ring buffer
    sampleTimestamps.push(now);
    Object.keys(knownVars).forEach(function (k) {
      var val = getValFromState(state, k);
      ringBuffers[k].push(val);
    });

    // Enforce strict ring buffer bound
    if (sampleTimestamps.length > RING_BUFFER_SIZE) {
      sampleTimestamps.shift();
      Object.keys(knownVars).forEach(function (k) {
        ringBuffers[k].shift();
      });
      if (isPaused) {
        pauseSampleIdx = Math.max(0, pauseSampleIdx - 1);
      }
      if (zoomWindow) {
        zoomWindow.startIdx = Math.max(0, zoomWindow.startIdx - 1);
        zoomWindow.endIdx = Math.max(0, zoomWindow.endIdx - 1);
      }
    }

    if (newDiscovered && isPickerOpen) {
      updatePickerUI();
    }

    if (!rafPending) {
      rafPending = true;
      var renderFn = typeof requestAnimationFrame === 'function' ? requestAnimationFrame : function (cb) { cb(); };
      renderFn(function () {
        rafPending = false;
        renderChart();
        updateLegend();
        updateControlsUI();
      });
    }
  }

  // ── HTML Template ──────────────────────────────────────────────────────
  function buildHTML() {
    return [
      '<style>',
      '.ts-panel-root { display:flex;flex-direction:column;gap:10px;font-family:"Segoe UI",system-ui,sans-serif;color:var(--text);position:relative; }',
      '.ts-panel-root.is-paused { border:3px solid #f5a623 !important;border-radius:6px;box-shadow:0 0 22px rgba(245,166,35,0.45) !important; }',
      '.ts-paused-banner { background:repeating-linear-gradient(-45deg,#2a1f00,#2a1f00 12px,#4a3600 12px,#4a3600 24px);border:2px solid #f5a623;border-radius:4px;color:#ffcc00;font-weight:700;font-size:12px;letter-spacing:0.04em;padding:8px 14px;display:flex;justify-content:space-between;align-items:center;animation:ts-pulse 2s infinite ease-in-out; }',
      '@keyframes ts-pulse { 0%,100% { box-shadow:0 0 10px rgba(245,166,35,0.5); } 50% { box-shadow:0 0 25px rgba(245,166,35,0.9); } }',
      '.ts-paused-title { display:flex;align-items:center;gap:6px; }',
      '.ts-paused-age { font-family:Consolas,monospace;background:rgba(0,0,0,0.5);padding:2px 8px;border-radius:3px; }',
      '.ts-top-bar { display:flex;flex-wrap:wrap;align-items:center;justify-content:space-between;gap:8px;padding:4px 0; }',
      '.ts-bar-section { display:flex;align-items:center;gap:6px;flex-wrap:wrap; }',
      '.ts-btn { background:var(--accent,#0f3460);color:var(--text,#e8e8e8);border:1px solid var(--border,#2a2a4a);border-radius:4px;padding:4px 10px;font-size:12px;font-weight:600;cursor:pointer;display:inline-flex;align-items:center;gap:4px;transition:all 0.15s; }',
      '.ts-btn:hover { filter:brightness(1.2); }',
      '.ts-btn-pause { background:#d35400;color:#fff;border-color:#e67e22; }',
      '.ts-btn-resume { background:#27ae60;color:#fff;border-color:#2ecc71;font-weight:700;animation:ts-pulse-green 1.5s infinite; }',
      '@keyframes ts-pulse-green { 0%,100% { box-shadow:0 0 6px rgba(46,204,113,0.4); } 50% { box-shadow:0 0 16px rgba(46,204,113,0.85); } }',
      '.ts-btn-step { font-family:Consolas,monospace;padding:3px 7px;font-size:11px; }',
      '.ts-live-badge { font-size:11px;font-weight:700;padding:2px 8px;border-radius:10px;letter-spacing:0.04em; }',
      '.ts-live-badge.live { background:rgba(78,204,163,0.18);color:#4ecca3;border:1px solid rgba(78,204,163,0.4); }',
      '.ts-live-badge.paused { background:rgba(245,166,35,0.22);color:#f5a623;border:1px solid #f5a623; }',
      '.ts-dropdown-wrap { position:relative; }',
      '.ts-picker-menu { position:absolute;right:0;top:100%;z-index:100;background:var(--card,#16213e);border:1px solid var(--border,#2a2a4a);border-radius:6px;box-shadow:0 8px 24px rgba(0,0,0,0.6);width:320px;max-height:360px;display:flex;flex-direction:column;margin-top:4px; }',
      '.ts-picker-header { padding:8px;border-bottom:1px solid var(--border,#2a2a4a);display:flex;flex-direction:column;gap:6px; }',
      '.ts-picker-search { width:100%;box-sizing:border-box;background:rgba(0,0,0,0.3);border:1px solid var(--border,#2a2a4a);color:var(--text);padding:4px 8px;border-radius:4px;font-size:11px; }',
      '.ts-picker-actions { display:flex;gap:6px;justify-content:flex-end; }',
      '.ts-link-btn { background:none;border:none;color:#4a9eff;font-size:10px;cursor:pointer;padding:0;text-decoration:underline; }',
      '.ts-picker-list { overflow-y:auto;max-height:260px;padding:4px 0;display:flex;flex-direction:column; }',
      '.ts-picker-item { display:flex;align-items:center;gap:6px;padding:5px 8px;cursor:pointer;font-size:11px; }',
      '.ts-picker-item:hover { background:rgba(255,255,255,0.06); }',
      '.ts-swatch { width:10px;height:10px;border-radius:2px;flex-shrink:0; }',
      '.ts-picker-label { font-weight:600;min-width:70px; }',
      '.ts-picker-key { color:var(--muted);font-size:10px;flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap; }',
      '.ts-picker-cur { font-family:Consolas,monospace;color:var(--muted);font-size:10px; }',
      '.ts-scrubber-bar { display:flex;align-items:center;gap:8px;padding:2px 0; }',
      '.ts-scrubber { flex:1;accent-color:#f5a623;cursor:pointer;height:4px; }',
      '.ts-time-label { font-family:Consolas,monospace;font-size:10px;color:var(--muted);white-space:nowrap; }',
      '.ts-chart-wrap { display:flex;flex-direction:column;gap:6px; }',
      '.ts-svg { display:block;width:100%;max-width:' + CHART_W + 'px;background:rgba(0,0,0,0.25);border-radius:6px;cursor:crosshair;user-select:none; }',
      '.ts-legend { display:flex;flex-wrap:wrap;gap:12px;padding:4px 2px; }',
      '.ts-legend-item { display:flex;align-items:center;gap:6px; }',
      '.ts-legend-dot { width:10px;height:3px;border-radius:2px; }',
      '.ts-legend-name { font-size:11px;font-weight:600;color:var(--text); }',
      '.ts-legend-val { font-size:11px;font-family:Consolas,monospace;color:var(--muted); }',
      '</style>',

      '<div class="ts-panel-root" id="ts-panel-root">',
      '  <div id="ts-paused-banner" class="ts-paused-banner" style="display:none;">',
      '    <span class="ts-paused-title">⏸ PAUSED — FROZEN HISTORY — NOT LIVE TELEMETRY</span>',
      '    <span id="ts-paused-age" class="ts-paused-age">0.0s behind live</span>',
      '  </div>',

      '  <div class="ts-top-bar">',
      '    <div class="ts-bar-section">',
      '      <button id="ts-btn-pause" class="ts-btn ts-btn-pause" type="button">⏸ Pause</button>',
      '      <button id="ts-btn-resume" class="ts-btn ts-btn-resume" style="display:none;" type="button">▶ RESUME LIVE</button>',
      '      <div id="ts-playback-controls" class="ts-bar-section" style="display:none;">',
      '        <button id="ts-btn-step-bb" class="ts-btn ts-btn-step" type="button" title="Step back 50 samples">⏪ -50</button>',
      '        <button id="ts-btn-step-b" class="ts-btn ts-btn-step" type="button" title="Step back 5 samples">◀ -5</button>',
      '        <button id="ts-btn-play" class="ts-btn ts-btn-step" type="button" title="Replay buffered history">▶ Replay</button>',
      '        <button id="ts-btn-step-f" class="ts-btn ts-btn-step" type="button" title="Step forward 5 samples">▶ +5</button>',
      '        <button id="ts-btn-step-ff" class="ts-btn ts-btn-step" type="button" title="Step forward 50 samples">⏩ +50</button>',
      '      </div>',
      '      <span id="ts-live-badge" class="ts-live-badge live">● LIVE</span>',
      '    </div>',

      '    <div class="ts-bar-section">',
      '      <button id="ts-btn-zoom-in" class="ts-btn" type="button" title="Zoom In (narrow window)">🔍+</button>',
      '      <button id="ts-btn-zoom-out" class="ts-btn" type="button" title="Zoom Out (widen window)">🔍-</button>',
      '      <button id="ts-btn-zoom-reset" class="ts-btn" type="button" title="Reset Zoom to fit full buffer">↺ Reset</button>',
      '      <div class="ts-dropdown-wrap">',
      '        <button id="ts-picker-toggle" class="ts-btn" type="button">☰ Variables (<span id="ts-picker-count">0</span>) ▾</button>',
      '        <div id="ts-picker-menu" class="ts-picker-menu" style="display:none;">',
      '          <div class="ts-picker-header">',
      '            <input type="text" id="ts-picker-search" class="ts-picker-search" placeholder="Search variables..."/>',
      '            <div class="ts-picker-actions">',
      '              <button id="ts-btn-preset-default" class="ts-link-btn" type="button">Default</button>',
      '              <button id="ts-btn-preset-pos" class="ts-link-btn" type="button">XYZ</button>',
      '              <button id="ts-btn-preset-clear" class="ts-link-btn" type="button">Clear</button>',
      '            </div>',
      '          </div>',
      '          <div id="ts-picker-list" class="ts-picker-list"></div>',
      '        </div>',
      '      </div>',
      '    </div>',
      '  </div>',

      '  <div class="ts-scrubber-bar">',
      '    <span class="ts-time-label" id="ts-time-start">-0.0s</span>',
      '    <input type="range" id="ts-scrubber" class="ts-scrubber" min="0" max="0" value="0"/>',
      '    <span class="ts-time-label" id="ts-time-end">Live (0.0s)</span>',
      '  </div>',

      '  <div class="ts-chart-wrap">',
      '    <svg id="ts-chart-svg" class="ts-svg" viewBox="0 0 ' + CHART_W + ' ' + CHART_H + '"></svg>',
      '    <div id="ts-legend" class="ts-legend"></div>',
      '  </div>',
      '</div>'
    ].join('');
  }

  // ── Event Bindings ─────────────────────────────────────────────────────
  function bindEvents() {
    // Playback & Pause
    var btnPause = q('ts-btn-pause');
    if (btnPause) btnPause.addEventListener('click', pausePlot);

    var btnResume = q('ts-btn-resume');
    if (btnResume) btnResume.addEventListener('click', resumeLive);

    var btnStepBB = q('ts-btn-step-bb');
    if (btnStepBB) btnStepBB.addEventListener('click', function () { stepOffset(-50); });

    var btnStepB = q('ts-btn-step-b');
    if (btnStepB) btnStepB.addEventListener('click', function () { stepOffset(-5); });

    var btnStepF = q('ts-btn-step-f');
    if (btnStepF) btnStepF.addEventListener('click', function () { stepOffset(5); });

    var btnStepFF = q('ts-btn-step-ff');
    if (btnStepFF) btnStepFF.addEventListener('click', function () { stepOffset(50); });

    var btnPlay = q('ts-btn-play');
    if (btnPlay) btnPlay.addEventListener('click', toggleReplay);

    // Scrubber
    var scrubber = q('ts-scrubber');
    if (scrubber) {
      scrubber.addEventListener('input', function () {
        if (!isPaused) pausePlot();
        pauseSampleIdx = Number(this.value);
        updateControlsUI();
        renderChart();
        updateLegend();
      });
    }

    // Zoom Buttons
    var btnZIn = q('ts-btn-zoom-in');
    if (btnZIn) btnZIn.addEventListener('click', zoomIn);

    var btnZOut = q('ts-btn-zoom-out');
    if (btnZOut) btnZOut.addEventListener('click', zoomOut);

    var btnZReset = q('ts-btn-zoom-reset');
    if (btnZReset) btnZReset.addEventListener('click', resetZoom);

    // Dropdown Variable Picker Toggle
    var pickerToggle = q('ts-picker-toggle');
    if (pickerToggle) {
      pickerToggle.addEventListener('click', function (e) {
        if (e && e.stopPropagation) e.stopPropagation();
        isPickerOpen = !isPickerOpen;
        updatePickerUI();
      });
    }

    // Picker Search Filter
    var pickerSearch = q('ts-picker-search');
    if (pickerSearch) {
      pickerSearch.addEventListener('input', function () {
        pickerFilter = this.value;
        updatePickerUI();
      });
    }

    // Dropdown Item Checkboxes (event delegation on list)
    var pickerList = q('ts-picker-list');
    if (pickerList) {
      pickerList.addEventListener('change', function (e) {
        var target = e.target;
        if (target && target.dataset && target.dataset.key) {
          var k = target.dataset.key;
          if (knownVars[k]) {
            knownVars[k].enabled = target.checked;
            saveSelection();
            updatePickerUI();
            renderChart();
            updateLegend();
          }
        }
      });
    }

    // Presets
    var btnPreDef = q('ts-btn-preset-default');
    if (btnPreDef) {
      btnPreDef.addEventListener('click', function () {
        Object.keys(knownVars).forEach(function (k) {
          knownVars[k].enabled = DEFAULT_DEFS[k] ? DEFAULT_DEFS[k].defaultEnabled : false;
        });
        saveSelection();
        updatePickerUI();
        renderChart();
        updateLegend();
      });
    }

    var btnPrePos = q('ts-btn-preset-pos');
    if (btnPrePos) {
      btnPrePos.addEventListener('click', function () {
        Object.keys(knownVars).forEach(function (k) {
          knownVars[k].enabled = (k === 'c.earth_x' || k === 'c.earth_y' || k === 'c.altitude');
        });
        saveSelection();
        updatePickerUI();
        renderChart();
        updateLegend();
      });
    }

    var btnPreClr = q('ts-btn-preset-clear');
    if (btnPreClr) {
      btnPreClr.addEventListener('click', function () {
        Object.keys(knownVars).forEach(function (k) {
          knownVars[k].enabled = false;
        });
        saveSelection();
        updatePickerUI();
        renderChart();
        updateLegend();
      });
    }

    // Close dropdown on outside click
    if (typeof document !== 'undefined' && document.addEventListener) {
      document.addEventListener('click', function (e) {
        if (!isPickerOpen) return;
        var menu = q('ts-picker-menu');
        var toggle = q('ts-picker-toggle');
        if (menu && !menu.contains(e.target) && toggle && !toggle.contains(e.target)) {
          isPickerOpen = false;
          updatePickerUI();
        }
      });
    }

    // SVG Drag-to-zoom
    var svg = q('ts-chart-svg');
    if (svg) {
      svg.addEventListener('mousedown', function (e) {
        if (e.button !== 0) return;
        var rect = svg.getBoundingClientRect ? svg.getBoundingClientRect() : { left: 0, width: CHART_W };
        var clickX = (e.clientX !== undefined) ? (e.clientX - rect.left) * (CHART_W / (rect.width || CHART_W)) : e.offsetX;
        if (clickX >= PAD.left && clickX <= CHART_W - PAD.right) {
          isBoxDragging = true;
          dragStartX = clickX;
          dragCurrentX = clickX;
        }
      });

      svg.addEventListener('mousemove', function (e) {
        if (!isBoxDragging) return;
        var rect = svg.getBoundingClientRect ? svg.getBoundingClientRect() : { left: 0, width: CHART_W };
        var curX = (e.clientX !== undefined) ? (e.clientX - rect.left) * (CHART_W / (rect.width || CHART_W)) : e.offsetX;
        dragCurrentX = clamp(curX, PAD.left, CHART_W - PAD.right);
        renderChart();
      });

      svg.addEventListener('mouseup', function (e) {
        if (!isBoxDragging) return;
        isBoxDragging = false;
        var leftX = Math.min(dragStartX, dragCurrentX);
        var rightX = Math.max(dragStartX, dragCurrentX);
        var boxW = rightX - leftX;

        if (boxW > 12) {
          var range = getActiveViewRange();
          var innerW = CHART_W - PAD.left - PAD.right;
          var sampleCount = Math.max(1, range.end - range.start);
          var selStartPct = clamp((leftX - PAD.left) / innerW, 0, 1);
          var selEndPct = clamp((rightX - PAD.left) / innerW, 0, 1);

          var newStart = Math.round(range.start + selStartPct * sampleCount);
          var newEnd = Math.round(range.start + selEndPct * sampleCount);
          if (newEnd - newStart >= 5) {
            zoomWindow = { startIdx: newStart, endIdx: newEnd };
          }
        }
        renderChart();
      });

      svg.addEventListener('dblclick', function () {
        resetZoom();
      });
    }
  }

  // ── Export Plugin Contract ─────────────────────────────────────────────
  window.__PLUGIN_INIT__ = function (api) {
    api.registerPanel('Time Series', function (container) {
      container.innerHTML = buildHTML();
      bindEvents();
      updatePickerUI();
      updateControlsUI();
      api.subscribe(onState);

      // Initial layout render
      setTimeout(function () {
        renderChart();
        updateLegend();
        updateControlsUI();
        updatePickerUI();
      }, 50);
    }, {
      workspace: 'telemetry',
      description: 'Real-time multi-variable time-series plot with pause, step-back replay, and region zoom'
    });
  };

  window.__PLUGIN_DESTROY__ = function () {
    if (replayInterval) {
      clearInterval(replayInterval);
      replayInterval = null;
    }
    sampleTimestamps = [];
    Object.keys(ringBuffers).forEach(function (k) {
      ringBuffers[k] = [];
    });
    isPaused = false;
    isReplaying = false;
    zoomWindow = null;
  };

  // Register with shell
  if (typeof window.__registerPlugin__ === 'function') {
    window.__registerPlugin__('Time Series', window.__PLUGIN_INIT__, window.__PLUGIN_DESTROY__, {
      workspace: 'telemetry',
      description: 'Real-time multi-variable time-series plot'
    });
  }

})();
