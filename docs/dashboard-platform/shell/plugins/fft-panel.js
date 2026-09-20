/**
 * fft-panel.js — FFT spectrum analyzer panel
 *
 * Takes last 64 samples of a selected variable, computes a basic DFT (radix-2 FFT),
 * and displays the magnitude spectrum as a bar chart (no external libs).
 * Shows frequency axis labels and peak frequency indicator.
 * Variable selector to choose which channel to analyze.
 */
(function () {
  'use strict';

  // ── Configuration ──────────────────────────────────────────────────────
  var FFT_SIZE = 64;
  var SAMPLE_RATE_HZ = 100;  // Assumed sample rate (configurable)

  // Variable definitions for analysis
  var ANALYZE_VARS = [
    { key: 'status.roll_deg',  label: 'Roll',     unit: 'deg' },
    { key: 'status.pitch_deg', label: 'Pitch',    unit: 'deg' },
    { key: 'status.yaw_deg',   label: 'Yaw',      unit: 'deg' },
    { key: 'mrac.roll.e',      label: 'Roll Err', unit: 'rad' },
  ];

  // ── State ──────────────────────────────────────────────────────────────
  var selectedVar = 'status.roll_deg';
  var sampleBuffer = [];  // Circular buffer of last FFT_SIZE samples
  var rafPending = false;

  // ── DOM helpers ─────────────────────────────────────────────────────────
  function q(id) { return document.getElementById(id); }

  // ── FFT Implementation (Cooley-Tukey radix-2) ──────────────────────────
  function isPowerOf2(n) {
    return n > 0 && (n & (n - 1)) === 0;
  }

  function log2(n) {
    return Math.log(n) / Math.log(2);
  }

  // Bit-reversal permutation
  function bitReverseIndex(index, bits) {
    var rev = 0;
    for (var i = 0; i < bits; i++) {
      rev = (rev << 1) | (index & 1);
      index >>= 1;
    }
    return rev;
  }

  // Radix-2 Cooley-Tukey FFT (in-place)
  // Returns {real: Float64Array, imag: Float64Array}
  function fft(input) {
    var n = input.length;
    if (!isPowerOf2(n)) {
      throw new Error('FFT size must be power of 2');
    }

    var bits = log2(n);
    var real = new Float64Array(n);
    var imag = new Float64Array(n);

    // Bit-reversal
    for (var i = 0; i < n; i++) {
      var rev = bitReverseIndex(i, bits);
      real[i] = input[rev] || 0;
      imag[i] = 0;
    }

    // Cooley-Tukey iterative FFT
    for (var size = 2; size <= n; size *= 2) {
      var halfSize = size / 2;
      var angleStep = -2 * Math.PI / size;

      for (var i = 0; i < n; i += size) {
        for (var j = 0; j < halfSize; j++) {
          var theta = angleStep * j;
          var cosTheta = Math.cos(theta);
          var sinTheta = Math.sin(theta);

          var evenIdx = i + j;
          var oddIdx = i + j + halfSize;

          var tr = real[oddIdx] * cosTheta - imag[oddIdx] * sinTheta;
          var ti = real[oddIdx] * sinTheta + imag[oddIdx] * cosTheta;

          real[oddIdx] = real[evenIdx] - tr;
          imag[oddIdx] = imag[evenIdx] - ti;
          real[evenIdx] = real[evenIdx] + tr;
          imag[evenIdx] = imag[evenIdx] + ti;
        }
      }
    }

    return { real: real, imag: imag };
  }

  // Compute magnitude spectrum (dB scale, normalized)
  function computeMagnitudes(fftResult) {
    var n = fftResult.real.length;
    var halfN = n / 2;
    var mags = new Float64Array(halfN);
    var maxMag = 0;

    // Compute magnitude for first half (DC to Nyquist)
    for (var i = 0; i < halfN; i++) {
      var r = fftResult.real[i];
      var im = fftResult.imag[i];
      mags[i] = Math.sqrt(r * r + im * im);
      if (mags[i] > maxMag) maxMag = mags[i];
    }

    // Normalize to dB (0 to -60 dB range)
    var dbMags = new Float64Array(halfN);
    var minDb = -60;
    for (var i = 0; i < halfN; i++) {
      if (maxMag > 0) {
        var norm = mags[i] / maxMag;
        dbMags[i] = norm > 0 ? 20 * Math.log10(norm) : minDb;
        if (dbMags[i] < minDb) dbMags[i] = minDb;
      } else {
        dbMags[i] = minDb;
      }
    }

    return { linear: mags, db: dbMags, maxVal: maxMag };
  }

  // Find peak frequency index
  function findPeak(mags, fftSize, sampleRate) {
    var halfN = mags.length;
    var peakIdx = 0;
    var peakVal = 0;
    var freqResolution = sampleRate / fftSize;

    for (var i = 1; i < halfN; i++) {  // Skip DC component
      if (mags[i] > peakVal) {
        peakVal = mags[i];
        peakIdx = i;
      }
    }

    return {
      index: peakIdx,
      frequency: peakIdx * freqResolution,
      magnitude: peakVal
    };
  }

  // ── Chart rendering ────────────────────────────────────────────────────
  var CHART_W = 480;
  var CHART_H = 160;
  var PAD = { left: 52, right: 12, top: 14, bottom: 28 };

  function renderSpectrum() {
    var svg = q('fft-chart-svg');
    if (!svg) return;

    var innerW = CHART_W - PAD.left - PAD.right;
    var innerH = CHART_H - PAD.top - PAD.bottom;

    // Check if we have enough samples
    if (sampleBuffer.length < FFT_SIZE) {
      svg.innerHTML = '<text x="' + (CHART_W / 2) + '" y="' + (CHART_H / 2) +
        '" text-anchor="middle" fill="rgba(136,136,170,0.6)" font-size="12">' +
        'Collecting samples… (' + sampleBuffer.length + '/' + FFT_SIZE + ')</text>';
      return;
    }

    try {
      // Pad to power of-2 if needed
      var fftSize = FFT_SIZE;
      while (!isPowerOf2(fftSize) && fftSize > 4) fftSize--;
      var paddedData = sampleBuffer.slice(-fftSize);

      // Compute FFT
      var fftResult = fft(paddedData);
      var mags = computeMagnitudes(fftResult);
      var peak = findPeak(mags.linear, fftSize, SAMPLE_RATE_HZ);

      var svgContent = '';
      var halfN = fftSize / 2;

      // Grid lines
      var gridLines = 4;
      for (var i = 0; i <= gridLines; i++) {
        var yPct = i / gridLines;
        var yPx = PAD.top + innerH * (1 - yPct);
        var dbVal = 0 - yPct * 60;  // 0 to -60 dB
        svgContent += '<line x1="' + PAD.left + '" y1="' + yPx + '" x2="' +
          (CHART_W - PAD.right) + '" y2="' + yPx + '" stroke="rgba(255,255,255,0.06)" stroke-width="1"/>';
        svgContent += '<text x="' + (PAD.left - 4) + '" y="' + (yPx + 4) +
          '" text-anchor="end" font-size="9" fill="rgba(255,255,255,0.35)" font-family="Consolas,monospace">' +
          dbVal.toFixed(0) + 'dB</text>';
      }

      // Y=0 line (reference)
      var zeroY = PAD.top;
      svgContent += '<line x1="' + PAD.left + '" y1="' + zeroY + '" x2="' +
        (CHART_W - PAD.right) + '" y2="' + zeroY + '" stroke="rgba(78,204,163,0.4)" stroke-width="1" stroke-dasharray="3,2"/>';

      // Frequency resolution
      var freqResolution = SAMPLE_RATE_HZ / fftSize;
      var nyquist = SAMPLE_RATE_HZ / 2;

      // Bar chart for spectrum
      var numBars = Math.min(halfN - 1, 32);  // Limit displayed bins
      var barWidth = innerW / numBars - 1;

      for (var i = 0; i < numBars; i++) {
        var xPct = i / numBars;
        var xPx = PAD.left + innerW * xPct;
        var dbVal = mags.db[i + 1];  // Skip DC
        var barHeight = innerH * (1 - (dbVal / -60));

        var barColor = i === peak.index - 1 ? '#e94560' : '#4a9eff';
        svgContent += '<rect x="' + xPx + '" y="' + (PAD.top + innerH - barHeight) +
          '" width="' + barWidth + '" height="' + barHeight + '" fill="' + barColor + '" opacity="0.7" rx="1"/>';
      }

      // Peak indicator
      if (peak.frequency > 0) {
        var peakXPct = (peak.index - 1) / numBars;
        var peakXPx = PAD.left + innerW * peakXPct;
        svgContent += '<text x="' + peakXPx + '" y="' + (PAD.top - 2) +
          '" text-anchor="middle" font-size="9" fill="#e94560" font-weight="600" font-family="Consolas,monospace">' +
          peak.frequency.toFixed(1) + 'Hz</text>';
      }

      // X-axis labels
      var freqLabels = [0, nyquist / 4, nyquist / 2];
      freqLabels.forEach(function (freq) {
        var xPct = freq / nyquist;
        var xPx = PAD.left + innerW * xPct;
        svgContent += '<text x="' + xPx + '" y="' + (CHART_H - 6) +
          '" text-anchor="middle" font-size="9" fill="rgba(255,255,255,0.4)" font-family="Consolas,monospace">' +
          freq.toFixed(0) + 'Hz</text>';
      });
      svgContent += '<text x="' + (CHART_W / 2) + '" y="' + (CHART_H - 6) +
        '" text-anchor="middle" font-size="9" fill="rgba(255,255,255,0.25)" font-family="Segoe UI,sans-serif">' +
        'Frequency (fs=' + SAMPLE_RATE_HZ + 'Hz, N=' + fftSize + ')</text>';

      // Update peak info
      var peakInfo = q('fft-peak-info');
      if (peakInfo) {
        peakInfo.innerHTML = 'Peak: <strong style="color:#e94560">' + peak.frequency.toFixed(1) + ' Hz</strong> | ' +
          'Mag: <strong>' + (20 * Math.log10(peak.magnitude + 1e-10)).toFixed(1) + ' dB</strong>';
      }

      svg.innerHTML = svgContent;

    } catch (e) {
      svg.innerHTML = '<text x="' + (CHART_W / 2) + '" y="' + (CHART_H / 2) +
        '" text-anchor="middle" fill="#e94560" font-size="11">FFT Error: ' + e.message + '</text>';
    }
  }

  // ── Build panel HTML ────────────────────────────────────────────────────
  function buildHTML() {
    var varOptions = ANALYZE_VARS.map(function (v) {
      return '<option value="' + v.key + '">' + v.label + ' (' + v.unit + ')</option>';
    }).join('');

    return [
      '<style>',
      '.fft-container { display:flex;flex-direction:column;gap:12px; }',
      '.fft-controls { display:flex;align-items:center;gap:12px; }',
      '.fft-svg { display:block;width:100%;max-width:' + CHART_W + 'px;background:rgba(0,0,0,0.2);border-radius:6px; }',
      '.fft-var-select { background:var(--bg);border:1px solid var(--border);color:var(--text);',
      '  padding:4px 8px;border-radius:4px;font-size:12px; }',
      '.fft-peak-info { font-size:12px;color:var(--muted);font-family:Consolas,monospace; }',
      '.fft-no-data { color:var(--muted);font-size:12px;text-align:center;padding:40px; }',
      '.fft-sr-config { display:flex;align-items:center;gap:8px;font-size:11px;color:var(--muted); }',
      '.fft-sr-input { width:60px;background:var(--bg);border:1px solid var(--border);',
      '  color:var(--text);padding:2px 6px;border-radius:3px;font-family:Consolas,monospace;font-size:11px; }',
      '</style>',

      '<div class="fft-container">',
      '  <div class="fft-controls">',
      '    <div class="fft-sr-config">',
      '      <label for="fft-var">Analyze:</label>',
      '      <select id="fft-var" class="fft-var-select">' + varOptions + '</select>',
      '    </div>',
      '    <div class="fft-sr-config">',
      '      <label for="fft-sr">Sample Rate:</label>',
      '      <input type="number" id="fft-sr" class="fft-sr-input" value="' + SAMPLE_RATE_HZ + '" min="1" max="1000"/>',
      '      <span>Hz</span>',
      '    </div>',
      '    <div id="fft-peak-info" class="fft-peak-info">Peak: —</div>',
      '  </div>',
      '  <svg id="fft-chart-svg" class="fft-svg" viewBox="0 0 ' + CHART_W + ' ' + CHART_H + '"></svg>',
      '</div>',
    ].join('');
  }

  // ── Data extraction from state ──────────────────────────────────────────
  function getChannelVal(slot0, ch) {
    if (!slot0 || !slot0.values) return null;
    if (slot0.values[ch] !== undefined) return slot0.values[ch];
    var ALIASES = {
      'status.roll_deg': ['imu_data.rol', 'ahrs.rol', 'ch0'],
      'status.pitch_deg': ['imu_data.pit', 'ahrs.pit', 'ch1'],
      'status.yaw_deg': ['imu_data.yaw', 'ahrs.yaw', 'ch2'],
      'mrac.roll.e': ['mrac_state.roll.e', 'mrac.roll_gamma', 'ch10'],
    };
    var alts = ALIASES[ch];
    if (alts) {
      for (var i = 0; i < alts.length; i++) {
        if (slot0.values[alts[i]] !== undefined) return slot0.values[alts[i]];
      }
    }
    return null;
  }

  // ── State handler ───────────────────────────────────────────────────────
  function onState(state) {
    if (!state || !state.streams) return;
    var stream0 = state.streams['0'];
    if (!stream0 || !stream0.values) return;

    var val = getChannelVal(stream0, selectedVar);
    if (val != null) {
      sampleBuffer.push(val);
      if (sampleBuffer.length > FFT_SIZE * 2) {
        sampleBuffer.shift();
      }
    }

    if (!rafPending) {
      rafPending = true;
      requestAnimationFrame(function () {
        rafPending = false;
        renderSpectrum();
      });
    }
  }

  // ── Event bindings ──────────────────────────────────────────────────────
  function bindEvents() {
    var varSelect = q('fft-var');
    if (varSelect) {
      varSelect.addEventListener('change', function () {
        selectedVar = this.value;
        sampleBuffer = [];  // Clear buffer when switching variables
        renderSpectrum();
      });
    }

    var srInput = q('fft-sr');
    if (srInput) {
      srInput.addEventListener('change', function () {
        var newRate = parseInt(this.value, 10);
        if (newRate > 0 && newRate <= 1000) {
          SAMPLE_RATE_HZ = newRate;
          renderSpectrum();
        }
      });
    }
  }

  // ── Export ──────────────────────────────────────────────────────────────
  window.__PLUGIN_INIT__ = function (api) {
    api.registerPanel('FFT Spectrum', function (container) {
      container.innerHTML = buildHTML();
      bindEvents();
      api.subscribe(onState);
      setTimeout(function () { renderSpectrum(); }, 100);
    });
  };
  window.__PLUGIN_DESTROY__ = function () {
    sampleBuffer = [];
  };
  window.__registerPlugin__('FFT Spectrum', window.__PLUGIN_INIT__, window.__PLUGIN_DESTROY__);

})();
