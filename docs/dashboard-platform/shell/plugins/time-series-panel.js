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
  // named plot layouts, per viewer (this browser's localStorage): { name: { keys, viewMode, savedAt } }
  var LAYOUTS_KEY = 'gs_ts_layouts_v1';
  var LAYOUT_NAME_MAX = 40;

  var CHART_W = 600;
  var CHART_H = 220;
  var PAD = { left: 56, right: 16, top: 26, bottom: 28 };

  // Trace colours, assigned in order to discovered variables (tokens.css --gs-trace-*)
  var COLOR_PALETTE = [];
  for (var ci = 1; ci <= 12; ci++) COLOR_PALETTE.push('var(--gs-trace-' + ci + ')');

  // Default known flight variables with physical units & display metadata
  var DEFAULT_DEFS = {
    'c.earth_x':       { key: 'c.earth_x',       label: 'Pos X',        unit: 'cm',  color: COLOR_PALETTE[0], defaultEnabled: true },
    'c.earth_y':       { key: 'c.earth_y',       label: 'Pos Y',        unit: 'cm',  color: COLOR_PALETTE[1], defaultEnabled: true },
    'c.altitude':      { key: 'c.altitude',      label: 'Pos Z (Alt)',  unit: 'm',   color: COLOR_PALETTE[2], defaultEnabled: true },
    'status.roll_deg':  { key: 'status.roll_deg',  label: 'Roll',         unit: 'deg', color: COLOR_PALETTE[3], defaultEnabled: true },
    'status.pitch_deg': { key: 'status.pitch_deg', label: 'Pitch',        unit: 'deg', color: COLOR_PALETTE[4], defaultEnabled: true },
    'status.yaw_deg':   { key: 'status.yaw_deg',   label: 'Yaw',          unit: 'deg', color: COLOR_PALETTE[5], defaultEnabled: false },
    'mrac.roll.e':      { key: 'mrac.roll.e',      label: 'Roll Err',     unit: 'rad', color: COLOR_PALETTE[6], defaultEnabled: false },
    'status.vbat':      { key: 'status.vbat',      label: 'Battery',      unit: 'V',   color: COLOR_PALETTE[7], defaultEnabled: false },
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

  // View mode: 'separate' = small multiples (default), 'overlay' = shared Y axis
  var viewMode = 'separate';

  // ── Persistence Helpers ────────────────────────────────────────────────
  // a blocked or corrupt localStorage is reported once (console, no toast) and the defaults are used
  function storageWarn(where, e) {
    if (typeof window !== 'undefined' && window.GSUI) window.GSUI.report(where, e, { toast: false, once: 'ts ' + where });
    else if (typeof console !== 'undefined' && console.warn) console.warn('[gs] ' + where + ': ' + (e && e.message));
  }

  function storageGet(key) {
    try {
      return (typeof localStorage !== 'undefined' && localStorage.getItem) ? localStorage.getItem(key) : null;
    } catch (e) { storageWarn('time series storage', e); return null; }
  }

  function storageSet(key, value) {
    try {
      if (typeof localStorage !== 'undefined' && localStorage.setItem) { localStorage.setItem(key, value); return true; }
    } catch (e) { storageWarn('time series storage', e); }
    return false;
  }

  function loadSavedSelection() {
    if (selectedKeysCache) return selectedKeysCache;
    try {
      var raw = storageGet(STORAGE_KEY);
      if (raw) {
        selectedKeysCache = JSON.parse(raw);
        return selectedKeysCache;
      }
    } catch (e) { storageWarn('saved selection', e); }
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
    storageSet(STORAGE_KEY, JSON.stringify(selectedKeysCache));
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

  /* A key that joins mid-session gets a buffer padded with gaps (null = never
   * received) up to the current sample count, so its index i stays the
   * sample at sampleTimestamps[i]; an empty buffer would draw its first value
   * at the oldest timestamp. */
  function ensureVar(key, enabled) {
    if (!knownVars[key]) {
      var def = DEFAULT_DEFS[key];
      knownVars[key] = {
        key: key,
        label: def ? def.label : key,
        unit: def ? def.unit : '',
        color: def ? def.color : COLOR_PALETTE[Object.keys(knownVars).length % COLOR_PALETTE.length],
        enabled: !!enabled,
        slot: null,
      };
    }
    if (!ringBuffers[key]) {
      ringBuffers[key] = [];
      for (var i = 0; i < sampleTimestamps.length; i++) ringBuffers[key].push(null);
    }
    return knownVars[key];
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
          ensureVar(key, saved[key] !== undefined ? Boolean(saved[key]) : false).slot = slotId;
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
    if (viewMode === 'overlay') {
      renderOverlayChart();
      return;
    }
    // Separate mode: small multiples - one SVG row per variable
    var rowsEl = q('ts-variable-rows');
    var xaxisEl = q('ts-vrow-xaxis');
    if (!rowsEl) return;
    if (!xaxisEl) return;

    var rangeInfo = getActiveViewRange();
    var innerW = CHART_W - PAD.left - PAD.right;
    /* Derived here, not shared: enabledKeys is a local of renderOverlayChart(),
     * so referencing it from this function threw ReferenceError on every frame. */
    var enabledKeys = Object.keys(knownVars).filter(function (k) { return knownVars[k].enabled; });

    if (rangeInfo.total === 0) {
      rowsEl.innerHTML = '';
      xaxisEl.style.display = 'none';
      return;
    }

    if (enabledKeys.length === 0) {
      rowsEl.innerHTML = '';
      xaxisEl.style.display = 'none';
      return;
    }

    /* Row geometry is local to the small multiples and must NOT reuse PAD.top.
     * The variable name and its latest value sit in an HTML header above each
     * SVG, so a row SVG contains nothing but the plot. PAD.top is 26px, sized
     * for the overlay chart's title row; using it here put the bottom gridline
     * at y = 26 + plotH, outside a 60px viewBox, and clipped every trace. */
    var ROW_PAD_TOP = 5;
    var ROW_PAD_BOTTOM = 7;
    var rowH = 64;
    var plotH = rowH - ROW_PAD_TOP - ROW_PAD_BOTTOM;
    var xaxisH = 20;

    // Shared X axis time reference
    var latestT = sampleTimestamps[sampleTimestamps.length - 1] || Date.now();
    var sampleCount = Math.max(1, rangeInfo.end - rangeInfo.start);

    // X-axis labels (shared across all rows)
    var xLabelHtml = '';
    var xTicks = 5;
    for (var xt = 0; xt <= xTicks; xt++) {
      var xPct = xt / xTicks;
      var xIdx = Math.round(rangeInfo.start + xPct * sampleCount);
      var sampleT = sampleTimestamps[xIdx];
      var timeText = '';
      if (sampleT) {
        var secAgo = (latestT - sampleT) / 1000;
        timeText = secAgo <= 0.05 ? '0s' : '-' + secAgo.toFixed(1) + 's';
      } else {
        timeText = '#' + xIdx;
      }
      xLabelHtml += '<span style="position:absolute;left:' + (PAD.left + innerW * xPct).toFixed(0) + 'px">' + timeText + '</span>';
    }
    xaxisEl.innerHTML = xLabelHtml;
    xaxisEl.style.display = 'block';
    xaxisEl.style.position = 'relative';
    xaxisEl.style.height = xaxisH + 'px';

    var rowsHtml = '';
    enabledKeys.forEach(function (k) {
      var meta = knownVars[k];
      var buf = ringBuffers[k] || [];

      // Per-variable Y scaling
      var vMin = Infinity;
      var vMax = -Infinity;
      var hasData = false;
      /* The header prints the newest sample that exists, not buf[rangeInfo.end]:
       * hasData is true when ANY sample in the range is non-null, so a gap at
       * the end of the range would otherwise call .toFixed() on null and throw. */
      var lastVal = null;
      for (var si = rangeInfo.start; si <= rangeInfo.end; si++) {
        var sv = buf[si];
        if (sv != null && !isNaN(sv)) {
          hasData = true;
          lastVal = sv;
          if (sv < vMin) vMin = sv;
          if (sv > vMax) vMax = sv;
        }
      }

      if (!hasData) {
        vMin = 0;
        vMax = 1;
      }
      var vSpan = vMax - vMin || 1.0;
      var vPad = vSpan * 0.1;
      vMin -= vPad;
      vMax += vPad;
      vSpan = vMax - vMin;

      var svgW = CHART_W;
      var svgH = rowH;

      var svgRow = '';

      // Grid lines
      var gridLines = 4;
      for (var g = 0; g <= gridLines; g++) {
        var yPct = g / gridLines;
        var yPx = ROW_PAD_TOP + plotH * (1 - yPct);
        var gVal = vMin + vSpan * yPct;
        svgRow += '<line x1="' + PAD.left + '" y1="' + yPx + '" x2="' + (CHART_W - PAD.right) + '" y2="' + yPx + '" style="stroke:var(--gs-grid)" stroke-width="1"/>';
        svgRow += '<text x="' + (PAD.left - 6) + '" y="' + (yPx + 3) + '" text-anchor="end" font-size="8" style="fill:var(--gs-text-muted)" font-family="Consolas,monospace">' + gVal.toFixed(2) + '</text>';
      }

      // Zero reference
      if (vMin < 0 && vMax > 0) {
        var zeroY = ROW_PAD_TOP + plotH * (1 - (0 - vMin) / vSpan);
        svgRow += '<line x1="' + PAD.left + '" y1="' + zeroY + '" x2="' + (CHART_W - PAD.right) + '" y2="' + zeroY + '" style="stroke:var(--gs-grid-strong)" stroke-width="1" stroke-dasharray="4,3"/>';
      }

      // Trace segments
      var segments = [];
      var curSeg = [];
      var lastPt = null;
      for (var i = rangeInfo.start; i <= rangeInfo.end; i++) {
        var val = buf[i];
        if (val != null && !isNaN(val)) {
          var xPct = (i - rangeInfo.start) / sampleCount;
          var yPct = (val - vMin) / vSpan;
          var xPx = PAD.left + innerW * xPct;
          var yPx = ROW_PAD_TOP + plotH * (1 - yPct);
          curSeg.push(xPx.toFixed(1) + ',' + yPx.toFixed(1));
          lastPt = { x: xPx, y: yPx };
        } else {
          if (curSeg.length > 0) { segments.push(curSeg); curSeg = []; }
        }
      }
      if (curSeg.length > 0) segments.push(curSeg);

      segments.forEach(function (pts) {
        if (pts.length >= 2) {
          svgRow += '<polyline points="' + pts.join(' ') + '" fill="none" style="stroke:' + meta.color + '" stroke-width="1.8" opacity="0.9"/>';
        } else if (pts.length === 1) {
          var coord = pts[0].split(',');
          svgRow += '<circle cx="' + coord[0] + '" cy="' + coord[1] + '" r="2" style="fill:' + meta.color + '"/>';
        }
      });

      // Latest marker
      if (lastPt) {
        svgRow += '<circle cx="' + lastPt.x.toFixed(1) + '" cy="' + lastPt.y.toFixed(1) + '" r="3.5" style="fill:' + meta.color + '"/>';
      }

      /* No zoom-region overlay here. In separate mode every row is already
       * drawn over getActiveViewRange(), so the zoom window IS the row: there
       * is no surrounding context to shade. The overlay chart shades it because
       * it draws the full buffer. */

      var svgHtml = '<svg class="ts-vrow-svg" viewBox="0 0 ' + svgW + ' ' + svgH + '" preserveAspectRatio="none">' + svgRow + '</svg>';

      rowsHtml += '<div class="ts-vrow">' +
        '<div class="ts-vrow-header">' +
        '<span class="ts-vrow-label" style="color:' + meta.color + '">' + meta.label + '</span>' +
        '<span class="ts-vrow-value">' + (lastVal != null ? lastVal.toFixed(4) + (meta.unit ? ' ' + meta.unit : '') : '\u2014 (no data)') + '</span>' +
        '</div>' +
        svgHtml +
        '</div>';
    });

    rowsEl.innerHTML = rowsHtml;
  }

  // ── Overlay Chart (shared Y axis, original behaviour) ──────────────────
  function renderOverlayChart() {
    var svg = q('ts-chart-svg');
    if (!svg) return;

    var rangeInfo = getActiveViewRange();
    var innerW = CHART_W - PAD.left - PAD.right;
    var innerH = CHART_H - PAD.top - PAD.bottom;

    if (rangeInfo.total === 0) {
      svg.innerHTML = '<text x="' + (CHART_W / 2) + '" y="' + (CHART_H / 2) +
        '" text-anchor="middle" style="fill:var(--gs-text-muted)" font-size="12">Waiting for telemetry data…</text>';
      return;
    }

    var enabledKeys = Object.keys(knownVars).filter(function (k) { return knownVars[k].enabled; });
    if (enabledKeys.length === 0) {
      svg.innerHTML = '<text x="' + (CHART_W / 2) + '" y="' + (CHART_H / 2) +
        '" text-anchor="middle" style="fill:var(--gs-text-muted)" font-size="12">No variables selected — open Variables menu to choose</text>';
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
        '" text-anchor="middle" style="fill:var(--gs-text-muted)" font-size="12">Waiting for data on selected variables…</text>';
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
        (CHART_W - PAD.right) + '" y2="' + yPx + '" style="stroke:var(--gs-grid)" stroke-width="1"/>';
      svgContent += '<text x="' + (PAD.left - 6) + '" y="' + (yPx + 3) +
        '" text-anchor="end" font-size="9" style="fill:var(--gs-text-muted)" font-family="Consolas,monospace">' +
        val.toFixed(2) + '</text>';
    }

    // Zero reference line
    if (minY < 0 && maxY > 0) {
      var zeroY = PAD.top + innerH * (1 - (0 - minY) / ySpan);
      svgContent += '<line x1="' + PAD.left + '" y1="' + zeroY + '" x2="' +
        (CHART_W - PAD.right) + '" y2="' + zeroY + '" style="stroke:var(--gs-grid-strong)" stroke-width="1" stroke-dasharray="4,3"/>';
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
          svgContent += '<polyline points="' + pts.join(' ') + '" fill="none" style="stroke:' +
            meta.color + '" stroke-width="1.8" opacity="0.9"/>';
        } else if (pts.length === 1) {
          var coord = pts[0].split(',');
          svgContent += '<circle cx="' + coord[0] + '" cy="' + coord[1] + '" r="2" style="fill:' + meta.color + '"/>';
        }
      });

      // Marker on latest visible value
      if (lastValidPt) {
        svgContent += '<circle cx="' + lastValidPt.x.toFixed(1) + '" cy="' + lastValidPt.y.toFixed(1) +
          '" r="3.5" style="fill:' + meta.color + '"/>';
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
        '" text-anchor="middle" font-size="9" style="fill:var(--gs-text-muted)" font-family="Consolas,monospace">' +
        timeText + '</text>';
    }

    // Zoom Indicator Header
    if (zoomWindow) {
      svgContent += '<rect x="' + PAD.left + '" y="4" width="' + innerW + '" height="18" style="fill:var(--gs-ok-bg)" rx="3"/>';
      svgContent += '<text x="' + (CHART_W / 2) + '" y="16" text-anchor="middle" font-size="10" style="fill:var(--gs-ok)" font-family="Segoe UI,sans-serif" font-weight="600">' +
        'Zoomed Region: ' + (rangeInfo.end - rangeInfo.start + 1) + ' samples (' +
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
          '" style="fill:var(--gs-ok-bg);stroke:var(--gs-ok)" stroke-width="1.5" stroke-dasharray="4,3"/>';
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
      '.ts-panel-root.is-paused { border:3px solid var(--gs-warn) !important;border-radius:6px;box-shadow:0 0 22px var(--gs-warn-bg) !important; }',
      '.ts-paused-banner { background:repeating-linear-gradient(-45deg,var(--gs-warn-bg),var(--gs-warn-bg) 12px,transparent 12px,transparent 24px);border:2px solid var(--gs-warn);border-radius:4px;color:var(--gs-warn);font-weight:700;font-size:12px;letter-spacing:0.04em;padding:8px 14px;display:flex;justify-content:space-between;align-items:center;animation:ts-pulse 2s infinite ease-in-out; }',
      '@keyframes ts-pulse { 0%,100% { box-shadow:0 0 10px var(--gs-warn-bg); } 50% { box-shadow:0 0 25px var(--gs-warn); } }',
      '.ts-paused-title { display:flex;align-items:center;gap:6px; }',
      '.ts-paused-age { font-family:Consolas,monospace;background:var(--gs-bg);padding:2px 8px;border-radius:3px; }',
      '.ts-top-bar { display:flex;flex-wrap:wrap;align-items:center;justify-content:space-between;gap:8px;padding:4px 0; }',
      '.ts-bar-section { display:flex;align-items:center;gap:6px;flex-wrap:wrap; }',
      '.ts-btn { background:var(--accent);color:var(--text);border:1px solid var(--border);border-radius:4px;padding:4px 10px;font-size:12px;font-weight:600;cursor:pointer;display:inline-flex;align-items:center;gap:4px;transition:all 0.15s; }',
      '.ts-btn:hover { filter:brightness(1.2); }',
      '.ts-btn-pause { background:var(--gs-warn);color:var(--gs-text-on-status);border-color:var(--gs-warn); }',
      '.ts-btn-resume { background:var(--gs-ok);color:var(--gs-text-on-status);border-color:var(--gs-ok);font-weight:700;animation:ts-pulse-green 1.5s infinite; }',
      '@keyframes ts-pulse-green { 0%,100% { box-shadow:0 0 6px var(--gs-ok-bg); } 50% { box-shadow:0 0 16px var(--gs-ok); } }',
      '.ts-btn-step { font-family:Consolas,monospace;padding:3px 7px;font-size:11px; }',
      '.ts-live-badge { font-size:11px;font-weight:700;padding:2px 8px;border-radius:10px;letter-spacing:0.04em; }',
      '.ts-live-badge.live { background:var(--gs-ok-bg);color:var(--gs-ok);border:1px solid var(--gs-ok); }',
      '.ts-live-badge.paused { background:var(--gs-warn-bg);color:var(--gs-warn);border:1px solid var(--gs-warn); }',
      '.ts-dropdown-wrap { position:relative; }',
      '.ts-picker-menu { position:absolute;right:0;top:100%;z-index:100;background:var(--card);border:1px solid var(--border);border-radius:6px;box-shadow:var(--gs-shadow);width:320px;max-height:360px;display:flex;flex-direction:column;margin-top:4px; }',
      '.ts-picker-header { padding:8px;border-bottom:1px solid var(--border);display:flex;flex-direction:column;gap:6px; }',
      '.ts-picker-search { width:100%;box-sizing:border-box;background:var(--gs-bg);border:1px solid var(--border);color:var(--text);padding:4px 8px;border-radius:4px;font-size:11px; }',
      '.ts-picker-actions { display:flex;gap:6px;justify-content:flex-end; }',
      '.ts-link-btn { background:none;border:none;color:var(--gs-info);font-size:10px;cursor:pointer;padding:0;text-decoration:underline; }',
      '.ts-picker-list { overflow-y:auto;max-height:260px;padding:4px 0;display:flex;flex-direction:column; }',
      '.ts-picker-item { display:flex;align-items:center;gap:6px;padding:5px 8px;cursor:pointer;font-size:11px; }',
      '.ts-picker-item:hover { background:var(--gs-hover-bg); }',
      '.ts-swatch { width:10px;height:10px;border-radius:2px;flex-shrink:0; }',
      '.ts-picker-label { font-weight:600;min-width:70px; }',
      '.ts-picker-key { color:var(--muted);font-size:10px;flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap; }',
      '.ts-picker-cur { font-family:Consolas,monospace;color:var(--muted);font-size:10px; }',
      '.ts-scrubber-bar { display:flex;align-items:center;gap:8px;padding:2px 0; }',
      '.ts-scrubber { flex:1;accent-color:var(--gs-warn);cursor:pointer;height:4px; }',
      '.ts-time-label { font-family:Consolas,monospace;font-size:10px;color:var(--muted);white-space:nowrap; }',
      '.ts-chart-wrap { display:flex;flex-direction:column;gap:6px;border-radius:6px; }',
      /* drop target for telemetry keys dragged from the Telemetry Explorer (WP-32 D5) */
      '.ts-chart-wrap.ts-drop-active { outline:2px dashed var(--gs-info);outline-offset:2px;background:var(--gs-info-bg); }',
      '.ts-svg { display:block;width:100%;max-width:' + CHART_W + 'px;background:var(--gs-inset-bg);border-radius:6px;cursor:crosshair;user-select:none; }',
      '.ts-legend { display:flex;flex-wrap:wrap;gap:12px;padding:4px 2px; }',
      '.ts-legend-item { display:flex;align-items:center;gap:6px; }',
      '.ts-legend-dot { width:10px;height:3px;border-radius:2px; }',
      '.ts-legend-name { font-size:11px;font-weight:600;color:var(--text); }',
      '.ts-legend-val { font-size:11px;font-family:Consolas,monospace;color:var(--muted); }',
      /* Small multiples: one row per variable with independent Y scale */
      '.ts-mode-toggle { display:inline-flex;border:1px solid var(--border);border-radius:4px;overflow:hidden; }',
      '.ts-mode-btn { background:var(--card);color:var(--text);border:none;padding:3px 10px;font-size:11px;cursor:pointer;transition:all 0.15s; }',
      '.ts-mode-btn:hover { filter:brightness(1.2); }',
      '.ts-mode-btn.ts-active { background:var(--accent);color:var(--text);font-weight:700; }',
      '.ts-variable-rows { display:flex;flex-direction:column;gap:2px; }',
      '.ts-vrow { display:flex;flex-direction:column;background:var(--gs-inset-bg);border-radius:4px;overflow:hidden; }',
      '.ts-vrow-header { display:flex;align-items:baseline;gap:8px;padding:3px 8px; }',
      '.ts-vrow-label { font-size:11px;font-weight:700; }',
      '.ts-vrow-value { font-size:11px;font-family:Consolas,monospace;color:var(--muted); }',
      '.ts-vrow-svg { display:block;width:100%; }',
      '.ts-vrow-xaxis { padding:0 8px 0 56px;font-size:9px;color:var(--gs-text-muted);font-family:Consolas,monospace;min-height:16px; }',
      '.ts-layout-name { width:140px; }',
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

      '  <div class="ts-chart-wrap" id="ts-chart-wrap">',
      '    <div class="ts-bar-section">',
      '      <span style="font-size:11px;color:var(--muted);margin-right:4px;">View:</span>',
      '      <span class="ts-mode-toggle">',
      '        <button id="ts-mode-overlay" class="ts-mode-btn" type="button" title="Overlay: all variables on shared Y axis">Overlay</button>',
      '        <button id="ts-mode-separate" class="ts-mode-btn ts-active" type="button" title="Separate: each variable on its own Y axis">Separate</button>',
      '      </span>',
      '      <span class="gs-label">Layout</span>',
      '      <select id="ts-layout-select" class="gs-input" title="Restore a saved layout (this browser only)"></select>',
      '      <input id="ts-layout-name" class="gs-input ts-layout-name" type="text" maxlength="' + LAYOUT_NAME_MAX + '" placeholder="layout name"/>',
      '      <button id="ts-layout-save" class="ts-btn" type="button" title="Save the plotted keys and the view mode under this name, in this browser">Save</button>',
      '      <button id="ts-layout-delete" class="ts-btn" type="button" title="Delete the selected layout (click twice)">Delete</button>',
      '      <span id="ts-layout-msg" class="gs-label" role="status"></span>',
      '    </div>',
      '    <div class="gs-label">Drop a key here from the Telemetry Explorer to plot it.</div>',
      '    <div id="ts-variable-rows" class="ts-variable-rows"></div>',
      '    <div id="ts-vrow-xaxis" class="ts-vrow-xaxis" style="display:none;"></div>',
      '    <svg id="ts-chart-svg" class="ts-svg" style="display:none;" viewBox="0 0 ' + CHART_W + ' ' + CHART_H + '"></svg>',
      '    <div id="ts-legend" class="ts-legend"></div>',
      '  </div>',
      '</div>'
    ].join('');
  }

  // ── View mode ──────────────────────────────────────────────────────────
  function setViewMode(mode) {
    viewMode = mode === 'overlay' ? 'overlay' : 'separate';
    var overlay = viewMode === 'overlay';
    var btnOverlay = q('ts-mode-overlay');
    var btnSeparate = q('ts-mode-separate');
    if (btnOverlay) btnOverlay.className = 'ts-mode-btn' + (overlay ? ' ts-active' : '');
    if (btnSeparate) btnSeparate.className = 'ts-mode-btn' + (overlay ? '' : ' ts-active');
    var overlaySvg = q('ts-chart-svg');
    if (overlaySvg) overlaySvg.style.display = overlay ? 'block' : 'none';
    /* '' restores the stylesheet rule, which is display:flex/column. Setting
     * 'block' here would leave the rows stacked without the 2px gap and
     * silently override .ts-variable-rows for the rest of the session.
     * renderChart() re-shows the shared x-axis, which overlay mode hid. */
    var rowsEl = q('ts-variable-rows');
    if (rowsEl) rowsEl.style.display = overlay ? 'none' : '';
    var xaxisEl = q('ts-vrow-xaxis');
    if (xaxisEl && overlay) xaxisEl.style.display = 'none';
    renderChart();
  }

  function refreshAll() {
    updatePickerUI();
    renderChart();
    updateLegend();
  }

  function showLayoutMsg(text, ok) {
    var el = q('ts-layout-msg');
    if (!el) return;
    el.textContent = text;
    el.className = ok ? 'gs-label' : 'gs-reason';
  }

  // ── Drag a telemetry key onto the plot (WP-32 D5) ──────────────────────
  // addKey(key) -> 'added' | 'already' | 'invalid'. A key that is not streaming
  // yet is plotted as a gap ("no data") until it arrives; nothing is invented.
  function addKey(key) {
    if (!key) return 'invalid';
    var v = ensureVar(key, false);
    if (v.enabled) return 'already';
    v.enabled = true;
    saveSelection();
    refreshAll();
    return 'added';
  }

  function bindDrop() {
    var wrap = q('ts-chart-wrap');
    var UI = typeof window !== 'undefined' ? window.GSUI : null;
    if (!wrap || !wrap.addEventListener || !UI) return;
    var depth = 0;   // dragenter / dragleave also fire over child elements
    function setActive(on) { wrap.className = 'ts-chart-wrap' + (on ? ' ts-drop-active' : ''); }
    wrap.addEventListener('dragenter', function (e) { if (UI.carriesKey(e)) { depth++; setActive(true); } });
    wrap.addEventListener('dragleave', function () { depth = Math.max(0, depth - 1); if (!depth) setActive(false); });
    wrap.addEventListener('dragover', function (e) {
      if (!UI.carriesKey(e)) return;
      e.preventDefault();   // allows the drop
      if (e.dataTransfer) e.dataTransfer.dropEffect = 'copy';
    });
    wrap.addEventListener('drop', function (e) {
      depth = 0;
      setActive(false);
      if (e.preventDefault) e.preventDefault();
      var key = UI.droppedKey(e);
      var r = addKey(key);
      showLayoutMsg(r === 'added' ? 'plotting ' + key : (r === 'already' ? key + ' is already plotted' :
        'not a telemetry key: drag a row from the Telemetry Explorer'), r !== 'invalid');
    });
  }

  // ── Named layouts (WP-32 D5): plotted keys + view mode, per viewer ─────
  // Stored in this browser's localStorage under LAYOUTS_KEY; nothing goes to
  // the service, so each operator machine keeps its own set.
  function readLayouts() {
    var raw = storageGet(LAYOUTS_KEY);
    if (!raw) return {};
    try {
      var all = JSON.parse(raw);
      return all && typeof all === 'object' && !Array.isArray(all) ? all : {};
    } catch (e) { storageWarn('saved layouts', e); return {}; }
  }

  function enabledKeys() {
    return Object.keys(knownVars).filter(function (k) { return knownVars[k].enabled; }).sort();
  }

  // saveLayout / restoreLayout / deleteLayout -> { ok, msg }
  function saveLayout(name) {
    name = String(name || '').trim();
    if (!name) return { ok: false, msg: 'type a layout name first' };
    if (name.length > LAYOUT_NAME_MAX) return { ok: false, msg: 'name longer than ' + LAYOUT_NAME_MAX + ' characters' };
    var keys = enabledKeys();
    if (!keys.length) return { ok: false, msg: 'nothing plotted: add a key first' };
    var all = readLayouts();
    var existed = Object.prototype.hasOwnProperty.call(all, name);
    all[name] = { keys: keys, viewMode: viewMode, savedAt: new Date().toISOString() };
    if (!storageSet(LAYOUTS_KEY, JSON.stringify(all))) return { ok: false, msg: 'browser storage refused the layout' };
    return { ok: true, msg: (existed ? 'updated "' : 'saved "') + name + '": ' + keys.length + ' keys, ' + viewMode };
  }

  function restoreLayout(name) {
    var all = readLayouts();
    var lay = Object.prototype.hasOwnProperty.call(all, name) ? all[name] : null;
    if (!lay || !Array.isArray(lay.keys)) return { ok: false, msg: 'no layout named "' + name + '"' };
    var want = lay.keys.filter(function (k) { return typeof k === 'string' && k.length > 0 && k.length <= 128; });
    Object.keys(knownVars).forEach(function (k) { knownVars[k].enabled = false; });
    want.forEach(function (k) { ensureVar(k, true).enabled = true; });
    var waiting = want.filter(function (k) { return knownVars[k].slot === null; }).length;
    saveSelection();
    setViewMode(lay.viewMode);
    refreshAll();
    return { ok: true, msg: 'restored "' + name + '": ' + want.length + ' keys' +
      (waiting ? ', ' + waiting + ' not streaming yet (shown as no data)' : '') };
  }

  function deleteLayout(name) {
    var all = readLayouts();
    if (!Object.prototype.hasOwnProperty.call(all, name)) return { ok: false, msg: 'no layout named "' + name + '"' };
    delete all[name];
    if (!storageSet(LAYOUTS_KEY, JSON.stringify(all))) return { ok: false, msg: 'browser storage refused the change' };
    return { ok: true, msg: 'deleted "' + name + '"' };
  }

  function renderLayoutSelect(selected) {
    var sel = q('ts-layout-select');
    if (!sel) return;
    var names = Object.keys(readLayouts()).sort();
    var esc = function (s) {
      return String(s).replace(/[&<>"']/g, function (c) {
        return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
      });
    };
    sel.innerHTML = '<option value="">' + (names.length ? '-- ' + names.length + ' saved layout(s) --' : '-- no saved layouts --') +
      '</option>' + names.map(function (n) { return '<option value="' + esc(n) + '">' + esc(n) + '</option>'; }).join('');
    sel.value = selected && names.indexOf(selected) >= 0 ? selected : '';
  }

  function bindLayouts() {
    renderLayoutSelect('');
    var sel = q('ts-layout-select');
    var nameEl = q('ts-layout-name');
    var saveBtn = q('ts-layout-save');
    var delBtn = q('ts-layout-delete');
    if (saveBtn) saveBtn.addEventListener('click', function () {
      var name = nameEl ? nameEl.value : '';
      var r = saveLayout(name);
      showLayoutMsg(r.msg, r.ok);
      if (r.ok) renderLayoutSelect(String(name).trim());
    });
    if (sel) sel.addEventListener('change', function () {
      if (!sel.value) return;
      var r = restoreLayout(sel.value);
      if (nameEl && r.ok) nameEl.value = sel.value;
      showLayoutMsg(r.msg, r.ok);
    });
    if (delBtn) delBtn.addEventListener('click', function () {
      var name = sel ? sel.value : '';
      if (!name) { showLayoutMsg('pick a saved layout to delete', false); return; }
      // deleting loses the operator's saved set: second click inside 5 s confirms
      if (window.GSUI && !window.GSUI.confirmClick(delBtn, { armedLabel: 'Delete "' + name + '"?' })) return;
      var r = deleteLayout(name);
      showLayoutMsg(r.msg, r.ok);
      renderLayoutSelect('');
    });
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

    // Mode Toggle: Separate vs Overlay
    var btnOverlay = q('ts-mode-overlay');
    var btnSeparate = q('ts-mode-separate');
    if (btnOverlay) btnOverlay.addEventListener('click', function () { setViewMode('overlay'); });
    if (btnSeparate) btnSeparate.addEventListener('click', function () { setViewMode('separate'); });

    // Drop a telemetry key onto the plot; named layouts
    bindDrop();
    bindLayouts();

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

  // drag-to-plot and named layouts, for the offline harness
  window.__gs_ui_state__ = window.__gs_ui_state__ || {};
  window.__gs_ui_state__.timeSeries = {
    addKey: addKey, saveLayout: saveLayout, restoreLayout: restoreLayout, deleteLayout: deleteLayout,
    readLayouts: readLayouts, enabledKeys: enabledKeys, viewMode: function () { return viewMode; },
    bufferLength: function (k) { return (ringBuffers[k] || []).length; }, sampleCount: function () { return sampleTimestamps.length; }
  };

})();
