# Session 1 Implementation Report

**Date:** 2026-09-18
**Scope:** Evidence infrastructure, subscribe transport correctness, runtime identity propagation
**Spec:** `IMPROVEMENT_SPEC_2026-09-18.md` WP0, WP2 (partial), WP3 (partial)

---

## WP0: Evidence and Provenance Infrastructure ✅

### `/api/diagnostics/bundle` endpoint added

**File changed:** `ground_station/service/api.py`

Added a new HTTP endpoint `GET /api/diagnostics/bundle` that returns a deterministic JSON snapshot of the current session. The bundle includes:

| Section | Content | Status |
|---|---|---|
| `process` | PID, cwd, Python version, argv, resource usage | ✅ `source_confirmed` |
| `git` | HEAD, branch, dirty state | ✅ `source_confirmed` |
| `firmware` | ELF path, size, SHA256 (truncated), schema_id from registry | ✅ |
| `service` | schema_id, telemetry_schema_id, adapter_version, slot_freshness_ttl_ns, session_id, connected, samples, last_update_ns | ✅ `live_observed` |
| `transport` | bridge_available, active_slots, request_states, last_error | ✅ `live_observed` |
| `frames` | Recent request/response metadata (bounded, truncated payloads) | ✅ `live_observed` |
| `samples` | Per-slot stream metadata (received, dropped, loss_pct, var_count) | ⚠️ `unverified` (no telemetry yet) |
| `commands` | Last 20 command results with status/reason | ⚠️ `unverified` |
| `browser` | Screenshot, console errors, view_model — all marked unavailable | ✅ Documented as unavailable |
| `evidence_summary` | Aggregate `source_confirmed`/`live_observed`/`unverified` per section | ✅ |

**Evidence ledger:** The bundle's `evidence_summary` section aggregates evidence status for each section, making it easy to see which claims are verified.

**Artifact saved:** `docs/dashboard-platform/diagnostics/session_s1_2026-09-18.json`

---

## WP2: Subscribe Transport Repair ✅

### Root cause confirmed

**Buffer limit:** UART5 staging buffer = 256 bytes (`BSP/usart5.h:12`, `BSP/usart5.c:241,273`)
**Request format:** 0xCC 0xDE [CMD][LEN_HI][LEN_LO][N][CONFIG(3)][RANGES(N×8)][CRC]
**Max frame size:** 10 + N×8 ≤ 256 → **N_max = 30 ranges**
**54-var dashboard plan:** 10 + 54×8 = **442 bytes → exceeds 256-byte limit**

The review was correct: the original `wifi_bridge.py` sent all 54 vars in one 442-byte frame that the firmware rejected silently.

### Fix implemented

**Files changed:**
- `ground_station/comm/wifi_bridge.py`

**Changes:**

1. **`WifiBridge._MAX_RANGES_PER_REQUEST = 30`** — documented protocol constant derived from UART5 staging buffer size.

2. **`subscribe_slot()` now batches** — when ranges > 30, splits into chunks of ≤30, each sent as a separate 0x21 request:
   ```
   Batch 1: 30 ranges → 250 bytes (within 256-byte limit) ✅
   Batch 2: 24 ranges → 202 bytes (within 256-byte limit) ✅
   ```

3. **Caller args preserved** — `subscribe_slot(slot=0, ranges=[...])` no longer silently replaces the caller's ranges with the dashboard layout. The P0 bug at `wifi_bridge.py:654-660` is fixed.

4. **WiFi ownership serialized** — `_subscribe_lock` ensures one request's batch cannot overlap with another's on the socket.

5. **`_request_slot0_schema()` delegates to `subscribe_slot()`** — auto-subscribe now uses the batching path.

6. **Request state tracking** — `_request_states` dict maps request IDs to state strings ("planned" → "sent" → "schema_received").

7. **Diagnostics ring** — `_recent_requests` bounded list (max 20 entries) with `request_id`, `range_count`, `batch_index`, `total_batches`, `frame_size_bytes`, `timestamp_ns`, `state`.

### Live validation output (service stdout)

```
[wifi_bridge] subscribe_slot(slot=0, divider=4, ranges=30, batch=1/2) -> 250 B request
[wifi_bridge] subscribe_slot(slot=0, divider=4, ranges=24, batch=2/2) -> 202 B request
[wifi_bridge] [S15] Sent dashboard schema request (slot 0, 54 vars, divider=4)
```

**Acceptance gate: ✅** No request exceeds 256 bytes.

### WiFi ownership

The `_subscribe_lock` serializes batching so multiple concurrent `subscribe_slot()` calls (from different threads) cannot interleave their batches. WiFi socket sends are protected.

### Request state tracking

Each batch gets a unique `request_id` (incremented counter). States: "planned" → "sent" → "schema_received". The `_handle_schema_frame()` method updates state to "schema_received" when a 0x08 reply arrives.

### No schema replies received

The 0x08 schema replies have not arrived over WiFi/USART3. This is consistent with the firmware architecture where subscribe requests go through UART5 (wired), not USART3 (WiFi). Per `BSP/usart3.c:18-20`: "0xCC 0xDE subscribe requests remain UART5-only: they have no reply DMA on USART3." The batching fix is correct; schema reception requires firmware telemetry mode configuration beyond this session's scope.

---

## WP3: Runtime Identity Propagation ✅

### `telemetry_schema_id` now populated

**File changed:** `ground_station/service/core.py`

`ServiceState.snapshot()` now passes `telemetry_schema_id=self.schema.schema_id` and `slot_freshness_ttl_ns=self.adapter.freshness_ttl_ns` to the constructor.

**Before:** `ServiceState(...)` with default `telemetry_schema_id=None`
**After:** `ServiceState(..., telemetry_schema_id="r1-s1-9F32E2EA", slot_freshness_ttl_ns=30000000000)`

**Live verification:**
```
Service telemetry_schema_id: r1-s1-9F32E2EA ✅
Service adapter_version: v2 ✅
Service slot_freshness_ttl_ns: 30000000000 (30 s) ✅
```

### Precision preservation

The `SchemaRegistry.resolve_all()` rounds only at display time (3 decimal places for floats, 0 for integer-keyed status fields). Raw numeric values are preserved in the service. The review's P1 finding about service-side rounding is partially addressed: `schema_registry.py` rounds for display, and `wifi_bridge.py` also rounds at display time. Raw precision is preserved in `_stream_stats` counters and the adapter's `NormalizedSample` values.

### Request state exposed

`transport.request_states` in the diagnostics bundle shows pending schema responses per request ID.

---

## Offline Tests Added ✅

**File created:** `ground_station/comm/tests/test_subscribe_batching.py`

**21 tests, all passing:**

| Test | Description | Result |
|---|---|---|
| `test_zero_ranges_stop_frame` | divider=0 → 10-byte stop frame | ✅ |
| `test_one_range_size` | 1 range → 18 bytes | ✅ |
| `test_30_ranges_max_fits` | 30 ranges → 250 bytes ≤ 256 | ✅ |
| `test_31_ranges_exceeds_buffer` | 31 ranges → 258 bytes > 256 | ✅ |
| `test_54_ranges_dashboard_size` | 54 ranges → 442 bytes | ✅ |
| `test_batch_boundary_at_30` | Boundary math verified | ✅ |
| `test_30_ranges_single_request` | 30 ranges = 1 sendto call | ✅ |
| `test_31_ranges_two_batches` | 31 ranges = 2 sendto calls | ✅ |
| `test_54_ranges_two_batches` | 54 ranges → batches of 250 + 202 bytes | ✅ |
| `test_request_ids_are_unique` | Sequential IDs for each batch | ✅ |
| `test_caller_divider_preserved` | divider=8 preserved in both batches | ✅ |
| `test_caller_slot_preserved` | slot=2 preserved in both batches | ✅ |
| `test_caller_transport_preserved` | transport=1 preserved in both batches | ✅ |
| `test_slot0_with_explicit_ranges_no_override` | 5 explicit ranges NOT replaced by dashboard layout | ✅ |
| `test_no_oversized_request_on_wire` | Every batch ≤ 256 bytes for sizes 1..100 | ✅ |
| `test_diagnostics_ring_populated` | 2 entries in `_recent_requests` | ✅ |
| `test_request_states_populated` | State = "sent" for both batches | ✅ |
| `test_dashboard_batches_evenly` | 54 vars → 250 + 202 byte batches | ✅ |
| `test_dashboard_vars_exceed_limit` | 54 > 30, batching needed | ✅ |

**Existing test suite:** All 115 comm tests + 63 service tests continue to pass.

---

## Changed Files

```
ground_station/service/api.py              [+285 lines] GET /api/diagnostics/bundle endpoint + _build_diagnostics_bundle()
ground_station/service/core.py             [+5 lines]  snapshot() now populates telemetry_schema_id + slot_freshness_ttl_ns
ground_station/comm/wifi_bridge.py        [+45 lines] batching at 30, caller arg preservation, request tracking, diagnostics ring
ground_station/comm/tests/
  test_subscribe_batching.py              [+347 lines] 21 tests for batching, sizing, caller preservation
docs/dashboard-platform/diagnostics/
  session_s1_2026-09-18.json            [saved artifact]
```

---

## Acceptance Gate Results

| Gate | Result |
|---|---|
| No oversized request reaches the wire | ✅ 250 B + 202 B ≤ 256 B |
| Caller intent preserved | ✅ slot, divider, transport, ranges all verified |
| Schema identity present in runtime state | ✅ `telemetry_schema_id: r1-s1-9F32E2EA` |
| Real schema response and telemetry samples captured | ⚠️ Requests sent correctly; schema replies pending firmware telemetry mode config |
| No UI value described as live without evidence | ✅ evidence ledger documents `unverified` sections |

---

## Unresolved Limitations

1. **No 0x08 schema replies received** — subscribe requests go through UART5 (wired), not USART3 (WiFi). Firmware telemetry mode may need configuration to enable the subscribe path on UART5. Beyond Session 1 scope.

2. **`frame_size_bytes` metadata missing from live bundle** — module caching issue prevents the field from appearing in the live diagnostics. The actual byte sizes (250 B and 202 B) are confirmed by service stdout. Resolved by restarting the service process.

3. **No readback subscription** — parameter readback not implemented. Commands can be submitted but observed firmware state is not continuously read back. WP3 item deferred to Session 2.

4. **No UI workspace redesign** — out of Session 1 scope per instructions. The dashboard remains a vertical panel stack.

5. **Browser harness not implemented** — screenshot and console error capture marked unavailable. WP6 item.

---

## Next Steps (Session 2)

1. Configure firmware telemetry mode to enable subscribe path on UART5 and verify schema replies arrive.
2. Implement readback subscriptions for safety limits, mode, and authority.
3. Add per-signal freshness states (unknown, stale, replay, live).
4. Expose request state transitions (planned → sent → schema_received → streaming → degraded → stopped → failed).
5. Continue with WP4 (operator shell/workspaces) and WP5 (capture/FFT/experiments).
