# S13.2 — Real-Time Plots and Bandwidth Manager Panels

**Date:** September 17, 2026  
**Status:** ✅ Complete  
**Session:** S13 (Documentation Enrichment)

---

## Overview

Implemented three new plugin panels for the UAV ground station dashboard:

1. **Time Series Panel** (`time-series-panel.js`) — Real-time multi-variable plotting
2. **FFT Spectrum Panel** (`fft-panel.js`) — Frequency domain analysis
3. **Bandwidth Manager Panel** (`bandwidth-panel.js`) — Telemetry slot negotiation

---

## 1. Time Series Panel

### Features

| Feature | Implementation |
|---------|----------------|
| Chart Type | SVG-based line chart (no external libs) |
| Variables | Up to 4 simultaneous plotted variables |
| Data Storage | Last 100 samples per variable in memory |
| Y-Axis | Auto-scaling with 10% padding |
| X-Axis | Sample index (0-99) |
| Line Colors | Per-variable color coding |
| Legend | Variable name + current value + unit |
| Variable Selector | Checkbox per variable to show/hide |
| Rendering | `requestAnimationFrame` throttled, only on changes |
| Update Rate | Throttled to 20Hz max for chart performance |

### Variables Available

| Channel | Label | Unit | Color |
|---------|-------|------|-------|
| ch0 | Gyro X | rad/s | #4a9eff |
| ch1 | Gyro Y | rad/s | #4ecca3 |
| ch2 | Accel Z | m/s² | #f5a623 |
| ch10 | Altitude | m | #e94560 |
| ch11 | Battery | V | #9b59b6 |

### Usage

The panel automatically starts collecting data from stream 0 when loaded. Check/uncheck variables in the right panel to show/hide them on the chart. Current values are displayed next to each variable label.

### Data Format

Reads from `state.streams['0'].values['slot0.{ch}']` for each channel.

---

## 2. FFT Spectrum Panel

### Features

| Feature | Implementation |
|---------|----------------|
| Transform | Radix-2 Cooley-Tukey FFT (pure JS) |
| Sample Buffer | 64 samples (auto-collected) |
| Display | Magnitude spectrum bar chart |
| Frequency Axis | Hz labels up to Nyquist |
| Peak Detection | Automatic peak frequency finder |
| Peak Indicator | Red bar + label at peak frequency |
| Variable Selection | Dropdown to choose analysis channel |
| Sample Rate Config | Adjustable Hz input (default 100 Hz) |
| Output | Normalized dB scale (0 to -60 dB) |

### FFT Algorithm

Implemented using iterative Cooley-Tukey radix-2 DIT algorithm:

```javascript
// Bit-reversal permutation for input
// Butterfly operations with twiddle factors
// In-place computation for efficiency
```

### Usage

1. Select variable to analyze from dropdown
2. Wait for 64 samples to accumulate (shown in status)
3. View magnitude spectrum in bar chart
4. Peak frequency displayed above chart
5. Adjust sample rate if known different from 100 Hz

### Limitations

- FFT size must be power of 2 (uses 64 samples directly)
- Windowing not applied (rectangular window)
- DC component (bin 0) excluded from display

---

## 3. Bandwidth Manager Panel

### Features

| Feature | Implementation |
|---------|----------------|
| Stream Monitoring | Reads all active streams from state |
| Rate Calculation | Samples/sec per stream based on sequence |
| Loss Display | Packet loss % per stream |
| Budget Display | Bar chart showing used vs total (80 Hz) |
| Warning Thresholds | 80% warning, 95% critical |
| Stream Toggle | Enable/disable individual streams |
| Stream Removal | Remove streams from active list |
| Request Form | Mock slot request with rate/channel inputs |
| Budget Warning | Banner when approaching/exceeding budget |

### Budget Configuration

| Parameter | Value |
|-----------|-------|
| Max Bandwidth | 80 Hz |
| Warning Threshold | 80% (64 Hz) |
| Critical Threshold | 95% (76 Hz) |

### Slot Request Mock

The "Request Slot" button opens a form for:
- **Rate (Hz):** Desired sample rate (1-50 Hz)
- **Channel:** Telemetry channel index (0-31)

On submit, displays confirmation message. In production, this would call:
- `POST /subscribe` API
- Or `shellApi.submitCommand()` for slot configuration

---

## Implementation Notes

### Rendering Performance

All panels use `requestAnimationFrame` for efficient rendering:

```javascript
if (!rafPending) {
  rafPending = true;
  requestAnimationFrame(function() {
    rafPending = false;
    renderChart();
  });
}
```

This ensures:
- No blocking of main thread
- Smooth 60fps updates
- Automatic frame skipping under load

### SVG Chart Sizing

Charts use `viewBox` attribute for responsive scaling:

```html
<svg viewBox="0 0 580 180" class="ts-svg"></svg>
```

Width/max-width CSS ensures proper scaling within container.

### State Subscription Pattern

All panels follow the same pattern:

```javascript
api.subscribe(function(state) {
  // Extract data from state
  // Update internal buffers
  // Schedule RAF render
});
```

### Dark Theme Compatibility

All colors use CSS variables for theme consistency:

```css
var(--bg)      /* #1a1a2e */
var(--card)    /* #16213e */
var(--text)    /* #e8e8e8 */
var(--muted)   /* #8888aa */
var(--accent)  /* #0f3460 */
var(--green)   /* #4ecca3 */
var(--amber)   /* #f5a623 */
var(--red)     /* #e94560 */
```

---

## Files Created

| File | Lines | Description |
|------|-------|-------------|
| `plugins/time-series-panel.js` | 280 | Real-time time-series plot |
| `plugins/fft-panel.js` | 364 | FFT spectrum analyzer |
| `plugins/bandwidth-panel.js` | 434 | Bandwidth manager |
| `REPORT_S13-plots-bandwidth.md` | — | This documentation |

---

## Integration

### Adding to Shell

The shell (`index.html`) loads plugins from a hardcoded list. To add these panels:

```javascript
const PLUGIN_FILES = [
  '/plugins/status-panel.js',
  '/plugins/mrac-panel.js',
  '/plugins/estimator-panel.js',
  '/plugins/resource-panel.js',
  '/plugins/safety-panel.js',
  '/plugins/telemetry-explorer-panel.js',
  '/plugins/time-series-panel.js',    // NEW
  '/plugins/fft-panel.js',             // NEW
  '/plugins/bandwidth-panel.js',      // NEW
];
```

After adding, restart the service to load the new plugins.

---

## Future Enhancements

1. **Time Series:**
   - Add time window mode (timestamp-based X-axis)
   - Implement scrolling/panning
   - Add cursor readout for precise value inspection
   - Support for multiple Y-axes with different scales

2. **FFT Panel:**
   - Add windowing functions (Hanning, Hamming, Blackman)
   - Implement zoom/pan on frequency axis
   - Add spectrogram/waterfall mode
   - Spectrogram persistence (last N frames)

3. **Bandwidth Manager:**
   - Connect to real slot request API
   - Add rate limiting/prioritization UI
   - Historical bandwidth usage graph
   - Automatic slot optimization suggestions

---

## Testing Checklist

- [ ] Time series renders with live data
- [ ] Variable checkboxes toggle lines
- [ ] Legend values update in real-time
- [ ] FFT accumulates 64 samples
- [ ] FFT displays spectrum for different variables
- [ ] Peak detection marks correct frequency
- [ ] Bandwidth chart shows active streams
- [ ] Stream toggles affect total rate
- [ ] Budget warning appears at threshold
- [ ] Slot request form displays mock confirmation
- [ ] All panels maintain 60fps at 500ms poll rate

---

## References

- Plugin API: `docs/dashboard-platform/shell/plugin-api.md`
- Shell implementation: `docs/dashboard-platform/shell/index.html`
- Existing plugins: `docs/dashboard-platform/shell/plugins/`
