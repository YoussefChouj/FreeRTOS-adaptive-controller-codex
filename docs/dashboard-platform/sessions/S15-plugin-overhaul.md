# S15 ¡ª Plugin overhaul

Subagent for the S15 implementation wave: rewrite the 5 broken dashboard plugins (status, MRAC, estimator, safety, bandwidth) and `resource-panel.js` (relabel raw IMU proxy) to read the S15-merged-key telemetry surface, plus ship a new `slot-manager-panel.js` to give jiang slot observability.

## Objective

Make every plugin correctly read the new merged-key telemetry surface, fix the broken panels called out in `S15-audit.md`, and add slot observability. All plugins must remain robust to both named-key surface AND raw positional fallback (the service-layer fix may not be deployed when this work lands).

## Deliverables

1. Rewritten plugins: status, mrac, estimator, safety, bandwidth, resource (6 files)
2. New panel: `slot-manager-panel.js`
3. `index.html` updated: PLUGIN_FILES array + `shellApi.subscribeSlot` helper
4. Report: `reports/S15-plugin-overhaul.md`
5. Updated STATE.md plugin registry table (15 entries; add Slot Manager row)

## Verification

- [x] All 7 plugin JS files parse via `node new Function(code)`
- [x] index.html inline script parses
- [x] `pytest ground_station/service/tests/ -v` ¡ú 17 passed
- [ ] Manual visual check at `http://localhost:8081/` (jiang to verify)

## Out of scope

- Python service code (`core.py`, `api.py`, `gateway.py`)
- Firmware (`*.c`, `boot_default_layout.py`)
- `wifi_bridge.py`
- Plugins that were already working (command, experiment, motor-bench, time-series, fft, replay, path, telemetry-explorer)
