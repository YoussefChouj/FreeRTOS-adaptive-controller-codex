# Report S14 — Telemetry Key Format Fix

**Date:** 2026-09-17  
**Goal:** Fix all dashboard plugins that incorrectly read telemetry values using the key format `slot0.ch0.X` instead of the correct format `chX`.

## Root Cause

Per `TELEMETRY_SPEC.md`, `state.streams['0'].values` uses keys like `ch0`, `ch1`, `ch11`, etc. — not `slot0.ch0.0`, `slot0.ch0.1`, `slot0.ch0.11`. The slot number lives in the parent object key (`state.streams['0']`), not in each value key.

## Files Modified

| File | Key Access Points Fixed | Change |
|------|------------------------|--------|
| `status-panel.js` | 3 (lines 243–247) | `getChannelVal()` built key as `'slot' + slot.tag + '.ch0.' + ch` → now uses `'ch' + ch` |
| `mrac-panel.js` | 1 (line 28) | `getChannelVal()` used `'slot0.ch0.' + ch` → now uses `'ch' + ch` |
| `estimator-panel.js` | 1 (line 127) | `getChannelVal()` used `'slot0.ch0.' + ch` → now uses `ch` directly (keys already in `chN` format) |
| `resource-panel.js` | 2 (lines 108, 150) | `getChannelVal()` used `'slot0.' + ch` → now uses `ch` directly; inline access `stream0.values['slot0.' + item.key]` → now uses `item.key` |
| `motor-bench-panel.js` | 3 (lines 359–362) | Removed `slot0.ch0.20-23` fallback block entirely; reordered RPM key priority; now tries typed stream keys only |
| `fft-panel.js` | 1 (line 299) | `getChannelVal()` used `'slot0.' + ch` → now uses `ch` directly |
| `time-series-panel.js` | 1 (line 208) | `getChannelVal()` used `'slot0.' + ch` → now uses `ch` directly |
| `replay-panel.js` | 3 (lines 64–70, 189–198, 214–223, 313–328) | `stripSlotPrefix()` made back-compat aware; `applyFilter()` added `record.slot` fallback; grouping/stats logic updated |

**Total key access points fixed: 15** across 8 files.

## Before/After Examples

### status-panel.js (`getChannelVal` — `ch` is a number)

```javascript
// Before (broken)
var key = 'slot' + slot.tag + '.ch0.' + ch;
// → 'slot0.ch0.11' for ch=11 (never found in values)

// After (correct)
var key = 'ch' + ch;
// → 'ch11' ✓
```

### mrac-panel.js (`getChannelVal` — `ch` is a number)

```javascript
// Before (broken)
return slot0.values['slot0.ch0.' + ch];  // → 'slot0.ch0.0' (undefined)

// After (correct)
return slot0.values['ch' + ch];           // → 'ch0' ✓
```

### estimator-panel.js (`getChannelVal` — `ch` is already a string like `'ch0'`)

```javascript
// Before (broken)
return slot0.values['slot0.ch0.' + ch];  // → 'slot0.ch0.ch0' (undefined)

// After (correct)
return slot0.values[ch];                  // → 'ch0' ✓
```

### resource-panel.js (inline access)

```javascript
// Before (broken)
v = stream0.values['slot0.' + item.key];  // → 'slot0.ch0' (undefined)

// After (correct)
v = stream0.values[item.key];              // → 'ch0' ✓
```

### motor-bench-panel.js (RPM fallback removed)

```javascript
// Before (broken — ch20-23 don't exist per spec)
var rpmKey = 'slot0.ch0.' + (20 + i);  // ch20, ch21, ch22, ch23
var rpm = slot0.values[rpmKey];

// After (correct — tries typed stream keys only, no ch20-23 fallback)
var keys = [
  'motor.rpm_0', 'motor.rpm_1', 'motor.rpm_2', 'motor.rpm_3',  // typed stream first
  'rpm.mot0', 'rpm.mot1', 'rpm.mot2', 'rpm.mot3',              // alt form
  'motor.motor_rpm_0', ...                                     // third form
];
// No slot0.ch0.20-23 fallback.
// If none found, _motorFeedback[i] stays null (no fake values).
```

## Plugins Already Correct (Not Modified)

- **`safety-panel.js`** — reads typed stream (slot 9) keys directly as `vals[param.key]` (e.g., `'safety.gs_max_horizontal_speed_mps'`). ✓
- **`telemetry-explorer-panel.js`** — auto-discovers keys via `Object.keys(s.values)`, no hardcoded key format. ✓

## Plugins Also Fixed (bonus — found by grep)

- **`fft-panel.js`** — same `getChannelVal(slot0, ch)` pattern, fixed `return slot0.values[ch]`
- **`time-series-panel.js`** — same pattern, fixed `return slot0.values[ch]`

## Backward Compatibility

`replay-panel.js` retains a legacy fallback: `applyFilter()` and the grouping/stats logic will still detect `slot0.*` key prefixes if older session recordings happen to use them. This is harmless — it simply makes the panel work with both old and new key formats.

## Verification

- Grep for `'slot0.ch0'` across all plugins: **0 code occurrences** (only 2 explanatory comments remain)
- `ReadLints` on all 8 modified files: **0 diagnostics**
