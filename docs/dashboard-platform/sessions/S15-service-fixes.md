# S15 ¡ª Service-layer / telemetry-pipeline fixes

## Objective

Close the four P0 data-flow bugs in `reports/S15-audit.md` ¡ì1:

1. `core.py:ingest_decoded()` slot routing (`"a"`¡úslot 2, `"b"`¡úslot 9)
2. `ingest_decoded()` storing only `{tag, values}` ¡ª no top-level
   `sequence` / `received` / `dropped` / `loss_pct` / `last_update_ns`
3. `ingest_decoded()` overwriting slot 0 values instead of merging
   sidebar keys with raw positional `slot0.ch0.N` keys
4. `wifi_bridge._handle_schema_frame()` losing DWARF names because the
   0x08 reply only echoes address/size/count over the wire

Plus deliver Fix 5 (RTOS SWD bridge via `--rtos-bridge`) and Fix 6
(new test coverage).

## Deliverables

- `ground_station/service/core.py` ¡ª `_TAG_TO_SLOT`, `_resolve_tag_slot`,
  `_extract_stream_metadata`, merge logic in `ingest_decoded()`,
  `inject_external_stream(slot, values, *, sequence)`
- `ground_station/comm/wifi_bridge.py` ¡ª `_pending_schema_ranges`,
  name re-attachment in `_handle_schema_frame`, `[S15]` print
  instrumentation in three places
- `ground_station/platform/rtos_bridge.py` ¡ª new module
  (~226 lines; reads `platform_obs_*` + `xTickCount` via `LiveReader`)
- `ground_station/service/__main__.py` ¡ª `--rtos-bridge`,
  `--rtos-interval` flags + signal-handler integration
- `ground_station/service/tests/test_service.py` ¡ª 7 new tests
  covering Fixes 1, 2, 3, 4 (routes, merge, metadata, external inject)
- `ground_station/comm/tests/test_wifi_bridge_stream.py` ¡ª 3 new tests
  in `TestSchemaHandlerPreservesNames` (Fix 4)
- `docs/dashboard-platform/reports/S15-service-fixes.md` ¡ª full report
  with live `/state` capture

## Verification

- [x] `pytest ground_station/service/tests/ -v` ¡ú **17 passed in 3.39s**
- [x] `pytest ground_station/comm/tests/test_wifi_bridge_stream.py -v`
      ¡ú **11 passed in 0.30s**
- [x] `[S15]` instrumentation confirmed on a fresh launch against the
      live drone ELF: all 21 vars resolved (DWARF probe), 0x21 request
      bytes printed, `_handle_schema_frame` decoded the schema
- [x] Live `/state` capture documented in the report ¡ª all 22 dashboard
      named keys appear in `streams["0"].values` after restart

## Key files

| File | Lines | Why |
|------|-------|-----|
| `ground_station/service/core.py` | +137 / ?18 | All four data-flow fixes + new `inject_external_stream` API |
| `ground_station/comm/wifi_bridge.py` | +50 / ?5 | Schema name preservation + `[S15]` instrumentation |
| `ground_station/platform/rtos_bridge.py` | +226 / ?0 | New SWD RTOS reader (off by default) |
| `ground_station/service/__main__.py` | +34 / ?0 | `--rtos-bridge` flag + clean shutdown |
| `ground_station/service/tests/test_service.py` | +135 / ?0 | 7 tests for Fixes 1¨C4 |
| `ground_station/comm/tests/test_wifi_bridge_stream.py` | +105 / ?0 | 3 tests for Fix 4 schema names |

Total: ~687 added, ~23 removed.

## Out of scope

- **MRAC full theta vector** ¡ª firmware change (`mrac_state.*.theta_0..5`),
  owned by the firmware agent
- **EKF covariance (`ekf.P_*`)** ¡ª firmware change, owned by firmware agent
- **Plugin reads of new top-level metadata** ¡ª already covered by the
  S15-plugin-overhaul subagent; the service-layer fix here is what
  makes that plugin work succeed when jiang restarts the service
- **Running live `/state` verification on the 8081 instance** ¡ª the
  currently-running 8081 service is from a pre-fix launch (still
  showing the OLD `"a"`¡úslot-2 routing). The fix is in source and
  passes all tests; restart required to see it on the live drone.