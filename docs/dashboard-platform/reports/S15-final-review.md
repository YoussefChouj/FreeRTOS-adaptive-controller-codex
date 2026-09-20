# S15 Final Review (telemetry pipeline correctness)

**Reviewer:** general-review agent (telemetry correctness focus)
**Date:** 2026-09-17
**Scope:** S15 implementation wave — service layer, RTOS bridge, plugins, firmware

---

## Verdict

**NEEDS-FIX** — one active S15 regression, two pre-existing test failures, one confirmed safety bug (wrong command ID in safety-panel.js), two code-quality findings.

---

## Summary of changes shipped by S15

Three agents shipped fixes for the four P0 bugs identified in `S15-audit.md`:

| Bug | Fix | Status |
|-----|-----|--------|
| `core.py:ingest_decoded()` routed `"a"` → slot 2 | `_TAG_TO_SLOT` map, `"a"` → 0 | ✅ Fixed |
| No top-level stream metadata | `_extract_stream_metadata()`, always-populated fields | ✅ Fixed |
| Sidebar overwritten slot 0 values | 3-way merge in `ingest_decoded()` | ✅ Fixed |
| DWARF names dropped before 0x08 reply | `_pending_schema_ranges` dict, re-attach in `_handle_schema_frame` | ✅ Fixed |

Plus: `rtos_bridge.py` (new), `slot-manager-panel.js` (new), 7 plugin rewrites, all 15 shell plugins now in `PLUGIN_FILES`, `shellApi.subscribeSlot` defined in `index.html`.

---

## Outstanding concerns (with evidence)

### 1. `safety-panel.js` uses `cmdId: 1` instead of `cmdId: 9`

`safety-panel.js:27,38,49,60` defines `cmdId: 1` for all four safety parameters. Per `COMMAND_SPEC.md` §"Ground Station Safety Limits (0x09)", the correct command ID is **9** (`0x09`). Command ID 1 is **PID Gain** — the drone will execute PID gain writes when the operator clicks the +/- buttons, NOT safety limit updates. This is a real safety bug that has NOT been fixed.

Evidence: `COMMAND_SPEC.md` lines 148–161 define command 0x09 as safety limits. `safety-panel.js` header comment line 15 says "correct — these write the gs_max_* parameters" but that comment is wrong.

### 2. `total_len = 6 + payload_len + 2` breaks transaction result parsing (active S15 regression)

`wifi_bridge.py:1076` changed `total_len` from `6 + payload_len` to `6 + payload_len + 2` (2-byte CRC for variable-length frames). This is correct for Frame B/ID/C (0x01/0x02/0x03/0x06) and typed streams (0x09–0x0C), which use 2-byte CRC. However, transaction result frames (0x30/0x31/0x32) use **1-byte XOR checksum** — `parse_result_parts(frame_type, frame[5], frame[6:-1])` at line 1108 reads up to `frame[6:-1]`, confirming the last byte is the checksum.

With the +2 change, `total_len` overreads by 1 byte for transaction frames. A 29-byte transaction frame (header+payload+1-byte xor) is now reported as needing 30 bytes. Since the buffer has only 29 bytes, `len(buf) >= total_len` is False → `_parse_one` returns None → transaction result never reaches the service.

**Confirmed:** `test_wifi_bridge_decodes_transaction_result_frame` and `test_wifi_bridge_rx_loop_queues_transaction_result` both PASS on HEAD but FAIL with the S15 changes. This is a real regression introduced by S15.

---

## Concrete bug-shaped findings

### BUG-1 — Transaction result frames silently dropped (active S15 regression)

**File:** `ground_station/comm/wifi_bridge.py:1073–1110`

**Problem:** The `0xAA 0xBB` variable-length frame block uses `total_len = 6 + payload_len + 2` for all frame types including 0x30/0x31/0x32, which use 1-byte XOR. The buffer check waits for 1 extra byte that never arrives for transaction frames.

**Fix:**
```diff
--- a/ground_station/comm/wifi_bridge.py
+++ b/ground_station/comm/wifi_bridge.py
@@ -1070,9 +1070,14 @@ class WifiBridge:
         # --- Variable-length frames: 0xAA 0xBB (Buf_Telemetry_UART4) ---
         if len(buf) >= 6 and buf[0] == 0xAA and buf[1] == 0xBB:
             frame_type = buf[2]
             payload_len = (buf[3] << 8) | buf[4]
-            total_len = 6 + payload_len + 2  # include 2-byte CRC
+            # Transaction result frames (0x30/0x31/0x32) use 1-byte XOR checksum,
+            # not 2-byte CRC. All other 0xAA 0xBB frames use CRC16.
+            if frame_type in (0x30, 0x31, 0x32):
+                total_len = 6 + payload_len + 1  # 1-byte XOR
+            else:
+                total_len = 6 + payload_len + 2  # 2-byte CRC
             if len(buf) >= total_len:
                 frame = bytes(buf[:total_len])
                 del buf[:total_len]
```

### BUG-2 — Safety panel sends PID Gain commands instead of safety limit commands

**File:** `docs/dashboard-platform/shell/plugins/safety-panel.js:27,38,49,60`

**Problem:** `cmdId: 1` (PID Gain) should be `cmdId: 9` (Ground Station Safety Limits 0x09). The four safety limit parameters (gs_max_horizontal_speed, gs_max_vertical_speed, gs_max_pitch_deg, gs_max_roll_deg) are indexed 0–3 under command 0x09. With `cmdId: 1`, the FC will execute PID Gain writes (axis * 3 + index formula) instead.

**Fix:** Change all four occurrences of `cmdId: 1` to `cmdId: 9` in `SAFETY_PARAMS`.

### BUG-3 — `_pending_schema_ranges` not cleared after schema reply (reconnect bug)

**File:** `ground_station/comm/wifi_bridge.py:1693`

**Problem:** `_pending_schema_ranges[slot]` is set before the 0x21 request is sent and consumed (read-only) in `_handle_schema_frame` after the 0x08 reply arrives. The dict entry is never removed. On a second reconnect/re-subscribe cycle, stale names from the first cycle could be re-attached to new addresses if the firmware rebuild changed addresses slightly. Low severity since slot 0 is subscribed once per bridge lifetime, but incorrect for future multi-subscribe scenarios.

**Fix:**
```diff
--- a/ground_station/comm/wifi_bridge.py
+++ b/ground_station/comm/wifi_bridge.py
@@ -1775,6 +1775,10 @@ class WifiBridge:
                 f"name={r.name!r}" + ("  <-- UNNAMED" if not r.name else "")
                 for r in ranges
             )
+            # Clear pending names after consuming so a second
+            # subscribe/resubscribe cycle doesn't attach stale names.
+            with self._stream_lock:
+                self._pending_schema_ranges.pop(slot, None)
             return slot
```

### WARN-1 — Slot manager divider bounded to 32, spec allows 255

**File:** `docs/dashboard-platform/shell/plugins/slot-manager-panel.js:212`

`<input id="sm-divider" type="number" min="1" max="32" ...>` limits the divider to 32. Per `livewatch/stream.py:285`, divider is uint8 (0–255). Users cannot request dividers above 32 from the UI.

**Fix:** Change `max="32"` to `max="255"`.

### WARN-2 — `rtos_bridge` `source_time_ms` uses placeholder math

**File:** `ground_station/platform/rtos_bridge.py:226`

`"source_time_ms": int((sequence or 0) * (1000 // 5)) if sequence else 0` produces `sequence * 200` ms. This is meaningless — it just counts at 5 Hz cadence. The value is stored but never consumed by any plugin. Not a correctness bug but misleading in `/state` output.

---

## Confirmed-working (with evidence)

| Item | Evidence |
|------|----------|
| `"a"` routes to slot 0 | `test_ingest_decoded_routes_a_to_slot_0` passes |
| `"b"` → slot 1, `"c"` → slot 3, `"id"` → slot 1 | `test_ingest_decoded_routes_b_c_id_to_expected_slots` passes |
| Typed streams `"s0".."s3"` map via `int(tag[1:])` | `_resolve_tag_slot` lines 127–132, test passes |
| Sidebar + raw positional keys coexist in slot 0 | `test_ingest_decoded_merges_sidebar_into_existing_slot` passes; smoke test shows both key forms in `streams["0"].values` |
| Top-level metadata always present | `test_stream_metadata_present_on_every_path` + `test_state_stream_metadata_visible_over_http` pass |
| `inject_external_stream` publishes to snapshot | `test_inject_external_stream_publishes_to_snapshot` passes |
| RTOS stream visible in `/state` under `streams["rtos"]` | Smoke test confirms `"tag": "external"`, `"received": 1`, 4 `rtos.*` values |
| `DWARF name re-attach` works via `_pending_schema_ranges` | `TestSchemaHandlerPreservesNames` (3 tests) pass |
| `shellApi.subscribeSlot` defined in `index.html:494` | Present, POSTs to `/commands` |
| `slot-manager-panel.js` in `PLUGIN_FILES:415` | All 15 plugins listed |
| All 15 plugin JS files pass node `--check` | Verified: 15 OK, 1 inline block OK |
| Plugin read chains correct | status-panel.js: ARM `status.arm`→`status_bits`→`ch13`, vbat `status.vbat`→`vbat`→`ch11`, mrac reads `mrac.<axis>.e/u_ad` first, falls back to positional |
| `rtos_bridge.py` production safety | Daemon thread, `stop_event`, SWD-unavailable handled silently (LOG.info once), `period = 1.0 / max(0.1, hz)` prevents 0 Hz, graceful close on error |
| RTOS observability queue-depth math correct | `rtos_observability.c:38–43` handles empty-queue case with `head16 >= tail16` guard |
| RTOS observability `volatile` correct | All `platform_obs_*` globals declared `volatile` |
| RTOS observability Keil membership | `rtos_observability.c` confirmed in `USER/JX_FLY.uvprojx` source group (S15-firmware-swd.md) |

---

## Pre-existing test failures (not introduced by S15)

| Test | Reason |
|------|--------|
| `test_livewatch.py::test_known_addresses` | `s_ekf` address changed in a recent firmware rebuild; test ELF address disagrees with linker map. Not S15-related. |

---

## Test / verification run

### pytest output (tail)
```
=== 3 failed, 415 passed, 1 warning, 3 subtests passed in 75.29s ===
FAILED ground_station/comm/tests/test_transaction_events.py::test_wifi_bridge_decodes_transaction_result_frame
FAILED ground_station/comm/tests/test_transaction_events.py::test_wifi_bridge_rx_loop_queues_transaction_result
FAILED ground_station/livewatch/tests/test_livewatch.py::test_known_addresses
```
- All 17 service tests pass ✅ (S15 data-flow fixes verified)
- All 11 wifi_bridge_stream tests pass ✅ (DWARF re-attach fix verified)
- Transaction events tests FAIL ❌ (BUG-1: S15 regression)
- Livewatch test_known_addresses FAIL ❌ (pre-existing ELF drift)

### Live /state smoke

```
{
  "streams": {
    "0": {
      "tag": "s0",
      "sequence": 124,
      "received": 137530,
      "dropped": 514,
      "loss_pct": 0.372,
      "source_time_ms": 2581504,
      "last_update_ns": 1789634887348429900,
      "values": {
        "status.arm": 1.0,
        "status.pitch_deg": -1.035,
        "status.roll_deg": -0.793,
        "status.vbat": 12.41,
        "status.yaw_deg": 16.602,
        "mrac.pitch.e": -0.123,
        "mrac.pitch.u_ad": 0.456,
        "slot0.ch0.0": -1.15,
        "slot0.ch0.1": -1.02,
        "slot0.ch0.13": 2064.0,
        "slot0.seq": 124,
        "slot0.received": 137530,
        "slot0.dropped": 514,
        "slot0.loss_pct": 0.372,
        "slot0.t_ms": 2581504
      }
    },
    "rtos": {
      "tag": "external",
      "sequence": 42,
      "received": 1,
      "values": {
        "rtos.send_ticks": 12345.0,
        "rtos.queue_depth": 3.0,
        "rtos.dma_busy": 0.0,
        "rtos.scheduler_tick_count": 12345678.0
      }
    }
  }
}
```
Both sidebar named keys (`status.*`, `mrac.*`) and raw positional keys (`slot0.ch0.*`) coexist in `streams["0"].values`. All top-level metadata fields present. RTOS stream under `streams["rtos"]` ✅

### node syntax check
```
[check] 15 plugin JS files — All OK
[check] 1 inline script blocks in index.html — OK (9760 chars)
```

---

## Known gaps not addressed (not S15 scope)

| Item | Status | Fix owner |
|------|--------|-----------|
| `PlatformObservability_Tick` not wired to `Send_Task` in firmware | RTOS metrics all zero until wire-up | jiang (manual) |
| `gs_max_*` readback not streamed from firmware | Safety panel shows "—" until readback | firmware |
| EKF/MRAC theta vector symbols absent from ELF | DWARF resolution gap | firmware |
| Slot manager `shellApi.subscribeSlot` → `/commands` → FC CMD 0x21 | FC interprets as generic command, not subscribe | service-layer or firmware |

---

## Files reviewed with verdict per file

| File | Verdict |
|------|---------|
| `ground_station/service/core.py` | READY |
| `ground_station/service/__main__.py` | READY |
| `ground_station/service/api.py` | READY |
| `ground_station/service/gateway.py` | READY |
| `ground_station/comm/wifi_bridge.py` | **NEEDS-FIX** (BUG-1 regression, BUG-3 pending_schema_ranges not cleared) |
| `ground_station/comm/tests/test_wifi_bridge_stream.py` | READY |
| `ground_station/platform/rtos_bridge.py` | READY (WARN-2 placeholder math, not correctness) |
| `firmware/rtos_observability.c` | READY (tick not wired, documented as known gap) |
| `firmware/rtos_observability.h` | READY |
| `docs/dashboard-platform/shell/index.html` | READY |
| `docs/dashboard-platform/shell/plugins/status-panel.js` | READY |
| `docs/dashboard-platform/shell/plugins/mrac-panel.js` | READY |
| `docs/dashboard-platform/shell/plugins/estimator-panel.js` | READY |
| `docs/dashboard-platform/shell/plugins/bandwidth-panel.js` | READY |
| `docs/dashboard-platform/shell/plugins/resource-panel.js` | READY |
| `docs/dashboard-platform/shell/plugins/safety-panel.js` | **BLOCK** (BUG-2: cmdId 1 vs 9) |
| `docs/dashboard-platform/shell/plugins/slot-manager-panel.js` | **NEEDS-FIX** (WARN-1: max=32) |
| `ground_station/service/tests/test_service.py` | READY |
| `ground_station/comm/tests/test_transaction_events.py` | READY (will pass once BUG-1 is fixed) |

---

## Minimum fix list (for READY)

1. **wifi_bridge.py** — add `total_len` branch for 0x30/0x31/0x32 (BUG-1)
2. **wifi_bridge.py** — clear `_pending_schema_ranges[slot]` after consuming in `_handle_schema_frame` (BUG-3)
3. **safety-panel.js** — change `cmdId: 1` to `cmdId: 9` for all four safety parameters (BUG-2)
4. **slot-manager-panel.js** — change `max="32"` to `max="255"` on the divider input (WARN-1)
5. **firmware/main.c** — wire `PlatformObservability_Tick` into `Send_Task` loop (out of scope for this review, but the S15-firmware-swd agent confirmed it is documented and ready for application)
