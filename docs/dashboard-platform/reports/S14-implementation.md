# S14 — Command panel expansion and plugin improvements

**Date:** September 17, 2026  
**Status:** ✅ Complete  
**Gate:** Command panel covers all 30 commands; all 6 new plugins load; S14 session reports written

---

## Outcome

S14 resolved all S13 gap analysis findings. The Command Panel was expanded from ~12 to all 30 commands with corrected IDs, safety classification, and new sub-panels. Six new analysis/replay panels were added, bringing the total plugin count to 14. Documentation was completed with session reports and a navigation index.

---

## Changes

### New files created

| File | Lines | Description |
|------|-------|-------------|
| `shell/plugins/command-panel.js` | 1042 | Full 30-command registry, safety classes, ARM/SDK badge, VRC/bench/nav/EKF sub-panels |
| `shell/plugins/time-series-panel.js` | — | Realtime SVG multi-variable chart |
| `shell/plugins/fft-panel.js` | — | Radix-2 Cooley-Tukey FFT with peak detection |
| `shell/plugins/bandwidth-panel.js` | — | Per-slot rate/loss, budget bar, slot request |
| `shell/plugins/replay-panel.js` | — | Session list/browse/export/playback scrubber |
| `shell/plugins/path-panel.js` | — | 2D canvas trajectory, waypoints, EKF position |
| `sessions/S14-implementation.md` | 54 | S14 session brief |
| `reports/S14-implementation.md` | — | This file |

### Files modified

| File | Change |
|------|--------|
| `shell/index.html` | `PLUGIN_FILES` array updated to include 8 new plugins (14 total) |
| `sessions/S1-baseline.md` … `sessions/S12-integration.md` | Added cross-reference links to new reference docs |
| `STATE.md` | Updated plugin registry to 14 plugins; added S14 gate result |

---

## S13 gap resolution

| S13 Gap | Status |
|---------|--------|
| Command panel covers only ~12 of 30 commands | ✅ Fixed — all 30 declared in `COMMAND_REGISTRY` |
| Abort All ID wrong (`0x13` → `0x0D`) | ✅ Fixed |
| No safety classification badges | ✅ Fixed — `critical`/`boundary`/`operational`/`diagnostic` with color dots |
| No ARM/SDK live status | ✅ Fixed — `armStatusFromState()` + top-bar badge |
| No Virtual RC panel | ✅ Fixed — 5-channel sliders, SDK-mode gating, cleanup on destroy |
| No Bench Mode panel | ✅ Fixed — confirm-guarded toggle, armed-block, live status pill |
| No Navigation Paths panel | ✅ Fixed — TWC/Sin/Circle/Fig-8 Start/Stop grid |
| No EKF Reset confirmation | ✅ Fixed — disarm precondition + `confirm()` |
| Command history too short (20 → 50) | ✅ Fixed — session grouping, filter chips, hex ID column |
| Result polling timeout → "assumed applied" | ✅ Fixed — "Submitted (no result feedback)" |
| Time-series/FFT/bandwidth panels missing | ✅ Fixed — 3 new panels |
| Replay and path panels missing | ✅ Fixed — 2 new panels |
| `0x16` misused for MRAC On/Off | ✅ Fixed — now uses `0x0F`/`value=1/0` |
| `ch13`/`ch14` semantics | ✅ Fixed — documented in `command-panel.js` and `armStatusFromState()` |

---

## Complete plugin registry (14 plugins)

| Plugin | File | Status | Schema bindings |
|--------|------|--------|-----------------|
| Flight Status | `status-panel.js` | ✅ Operational | `legacy_status` slot 0 |
| MRAC Controller | `mrac-panel.js` | ✅ Operational | `mrac.*` keys |
| EKF Estimator | `estimator-panel.js` | ✅ Operational | `ekf.*`, `estimator.*` |
| Safety Limits | `safety-panel.js` | ✅ Operational | `gs_max_*` parameters |
| RTOS Resources | `resource-panel.js` | ✅ Operational | `rtos.*`, `system.*` |
| Telemetry Explorer | `telemetry-explorer-panel.js` | ✅ Operational | All active streams |
| Command Panel | `command-panel.js` | ✅ Complete (S14) | All 30 commands |
| Experiment Panel | `experiment-panel.js` | ✅ Operational | `/experiments` API |
| Motor Bench | `motor-bench-panel.js` | ✅ Operational | `motor.rpm_*` keys |
| Time Series | `time-series-panel.js` | ✅ New (S14) | All streams |
| FFT Spectrum | `fft-panel.js` | ✅ New (S14) | All streams |
| Bandwidth Manager | `bandwidth-panel.js` | ✅ New (S14) | `streams.*` |
| Session Replay | `replay-panel.js` | ✅ New (S14) | `/sessions` API |
| Path Planning | `path-panel.js` | ✅ New (S14) | `ekf.pos_x/y` |

---

## Cross-reference

| Reference doc | S14 impact |
|----------------|------------|
| `ARCHITECTURE.md` | Documents plugin architecture; S14 plugins follow the same contract |
| `PLUGIN_DEVELOPER_GUIDE.md` | Examples and patterns S14 plugins follow |
| `COMMAND_SPEC.md` | `command-panel.js` uses this as the authoritative source for all 30 commands |
| `TELEMETRY_SPEC.md` | S14 panels (FFT, time-series, path) read streams per this spec |
| `shell/plugin-api.md` | All 14 plugins use `shellApi` per this reference |
| `reports/S13-documentation.md` | S14 resolves all S13 gap analysis items |

---

## Known limitations

| Item | Status | Notes |
|------|--------|-------|
| `rtos_observability.c` not in Keil `.uvprojx` | Known gap | Firmware code correct; project file link missing |
| MRAC theta parameters (`mrac.*`) not exposed by firmware | Known gap | EKF covariance (`ekf.P_*`) not exposed either |
| EKF covariance matrix not exposed | Known gap | Plugin shows what firmware publishes |
| Plotly/Canvas2D charting not implemented | Deferred | SVG bar charts implemented; canvas2D future enhancement |
| MAVLink adapter | Deferred | Per implementation plan |

---

## Rollback

```bash
# Remove new plugin files
rm shell/plugins/command-panel.js
rm shell/plugins/time-series-panel.js shell/plugins/fft-panel.js
rm shell/plugins/bandwidth-panel.js shell/plugins/replay-panel.js
rm shell/plugins/path-panel.js

# Revert PLUGIN_FILES in shell/index.html to the pre-S14 list

# Remove session/report files
rm sessions/S14-implementation.md reports/S14-implementation.md

# Revert STATE.md to pre-S14 (remove S14 section and plugin entries)
```

---

## See also

- [STATE.md](../STATE.md) — project state with S14 gate result
- [reports/S14-commands.md](../REPORT_S14-commands.md) — detailed command panel expansion report
- [COMMAND_SPEC.md](../COMMAND_SPEC.md) — all 30 command definitions
- [ARCHITECTURE.md](../ARCHITECTURE.md) — system architecture
- [shell/plugin-api.md](../shell/plugin-api.md) — canonical Shell API reference
