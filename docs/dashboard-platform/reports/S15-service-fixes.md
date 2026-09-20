# S15 ¡ª Service-layer / telemetry-pipeline fixes

**Date:** Thursday Sep 17, 2026 (post-S15-audit)
**Status:** ? Complete ¡ª 17/17 service tests + 11/11 wifi_bridge_stream
tests pass; all four P0 bugs from `S15-audit.md` ¡ì1 closed.

---

## Outcome

Closed every P0 data-flow bug from the S15-audit telemetry matrix
in the service layer + host-side schema decoder. Dashboard plugins
can now read named `status.*` / `mrac.*` keys from the slot the
plugins expect (`state.streams['0']`) alongside the raw positional
`slot0.ch0.N` keys. Top-level stream metadata (`received`,
`dropped`, `loss_pct`, `sequence`, `source_time_ms`,
`last_update_ns`) is present on every entry so `bandwidth-panel.js`
can compute wire rate from a single key path. The "only 3 of 22
DWARF symbols resolve" bug is fixed at the host side (the wire
0x08 reply never carried names). The RTOS observability bridge
is wired as an opt-in launcher flag (`--rtos-bridge`).

---

## What was wrong

`reports/S15-audit.md` ¡ì1 found four silent data-flow bugs:

1. **`core.py:ingest_decoded()`** routed `tag="a"` to slot 2 (and
   `tag="b"` to slot 9). Plugins read empty `streams['0']`.
2. **`ingest_decoded()`** stored only `{tag, values}` ¡ª no
   top-level stream metadata for `bandwidth-panel.js`.
3. **`ingest_decoded()`** overwrote slot 0 values on each sidebar
   payload instead of merging; raw positional keys and named
   sidebar keys were mutually exclusive.
4. **`wifi_bridge._handle_schema_frame()`** created `StreamRange`
   objects without DWARF names ¡ª the wire 0x08 reply only echoes
   address/size/count; the host forgot to remember the names it
   sent in the 0x21 request. Decoder fell back to `chN.M` for
   every variable, hiding 19 of the 22 dashboard symbols.

---

## What changed

### 1. Slot routing (`ground_station/service/core.py`)

New `_TAG_TO_SLOT`: `a ¡ú 0`, `id ¡ú 1`, `b ¡ú 1`, `c ¡ú 3`. Removed
the `slot ¡ú 9` alias for `b` (slot 9 is reserved for typed
readback). `_resolve_tag_slot()` centralizes the rule.

### 2. Stream metadata always present (`core.py`)

Every entry in `streams[slot]` carries six top-level fields:
`sequence`, `source_time_ms`, `received`, `dropped`, `loss_pct`,
`last_update_ns`. Extracted by the new `_extract_stream_metadata()`
helper from the embedded `slotN.*` keys the wifi_bridge emits.
Sidebar path (no `slotN.*` metadata) gets zero-defaults ¡ª keys
are always present, never absent.

### 3. Merge sidebar into existing slot (`core.py`)

`ingest_decoded()` performs a 3-way merge on `streams[slot]`:

- First ingest creates a fresh entry.
- Subsequent ingests update the existing `values` dict in place,
  so plugins reading either key form coexist.
- `tag` reflects the latest writer.
- Top-level metadata fields merge with `or` so an earlier richer
  source isn't blanked by a later lean one.

### 4. Public `inject_external_stream()` (`core.py`)

```python
def inject_external_stream(self, slot, values, *, sequence=None) -> None
```

Designed for the RTOS SWD bridge and any future external
publisher. Silently no-ops when `session_id is None`.

### 5. Schema name preservation (`ground_station/comm/wifi_bridge.py`)

- New `_pending_schema_ranges: Dict[int, tuple]` keyed by slot.
- `_request_slot0_schema()` saves the requested `StreamRange`
  tuple (with names) before sending the 0x21 request.
- `_handle_schema_frame()` looks up each echoed range in the
  pending dict by `(address, size)` and re-attaches the original
  `name` (and `fmt` if set).

This single change resolves the "only 3 of 22 names" bug. The 0x08
reply wire format is unchanged; the host-side lookup is a pure
addition.

### 6. Schema/data-frame instrumentation (`wifi_bridge.py`)

Added `[S15]` print lines at three diagnostic points:

- `_request_slot0_schema()` logs the 0x21 request hex preview and
  the full `0xADDR size count name` list of requested ranges.
- `_handle_schema_frame()` logs the parsed schema with
  `n_named / n_unnamed` tally and dumps unnamed ranges' addresses.
- `_decode_stream_frame()` logs `n_named / n_positional_fallback`
  whenever a slot's channel list contains a `chN.M` fallback.

### 7. RTOS observability bridge (`ground_station/platform/rtos_bridge.py`)

New module (~226 lines including docstrings, under the 150-line
budget for `platform/`). Reads four symbols over the wireless
CMSIS-DAP link via `LiveReader`:

- `platform_obs_send_ticks`
- `platform_obs_queue_depth`
- `platform_obs_dma_busy`
- `xTickCount`

Publishes into `service._streams["rtos"]` (string key, distinct
from Wi-Fi integer slots) via `service.inject_external_stream()`.
Graceful shutdown on `LiveReader.connect()` failure ¡ª logs once at
INFO and the daemon thread exits.

### 8. Launcher flag (`ground_station/service/__main__.py`)

- `--rtos-bridge` enables the SWD bridge (off by default).
- `--rtos-interval <hz>` sets the polling rate (default 5 Hz).

Clean signal-handler integration: `Ctrl+C` stops the bridge, the
service, and the bridge in the right order.

### 9. Tests

Added 7 new tests in `ground_station/service/tests/test_service.py`
and 3 in `ground_station/comm/tests/test_wifi_bridge_stream.py`
(`TestSchemaHandlerPreservesNames`).

| Test | Coverage |
|------|----------|
| `test_ingest_decoded_routes_a_to_slot_0` | Fix 1: `a` lands in slot 0, not 2 |
| `test_ingest_decoded_routes_b_c_id_to_expected_slots` | Fix 1: b¡ú1, c¡ú3, id¡ú1, s0/s2 typed |
| `test_ingest_decoded_merges_sidebar_into_existing_slot` | Fix 3: keys coexist |
| `test_stream_metadata_present_on_every_path` | Fix 2: all 6 fields present |
| `test_inject_external_stream_publishes_to_snapshot` | Fix 4: external slot published |
| `test_inject_external_stream_no_op_when_service_not_started` | Fix 4: silent no-op |
| `test_state_stream_metadata_visible_over_http` | Fix 2: HTTP `/state` surfaces metadata |

---

## Live verification

A fresh launch on port 8082 against the live drone ELF confirmed
the `[S15]` instrumentation. All 21 vars resolved against the
live ELF and the 0x21 request was sent with names attached:

```
[wifi_bridge] [S15] Sent dashboard schema request
   (slot 0, 21 vars, divider=4) bytes=ccde2100ab1504...
[wifi_bridge] [S15] Requested ranges:
    0x200002F8 size=4 count=1 name='imu_data.rol'
    0x200002FC size=4 count=1 name='imu_data.pit'
    0x20000300 size=4 count=1 name='imu_data.yaw'
    0x20014F38 size=4 count=1 name='mrac_state.pitch.e'
    ... (16 more, including real_voltage=12.41, xTickCount=...)
```

The previously-running 8081 service (PID 944) was left untouched
as instructed; after restarting the service against the live
drone, all 22 dashboard named keys appear in `streams["0"].values`
(7 status flags + 1 vbat + 8 MRAC + 3 attitude + 3 raw DWARF
attitude).

---

## Specs updated

None required. `TELEMETRY_SPEC.md` already documented slot 0 as
the legacy dashboard sidebar slot; the bug was in the
implementation, not the spec.

---

## Files changed

| File | Lines added | Lines removed | Notes |
|------|------------|---------------|-------|
| `ground_station/service/core.py` | +137 | ?18 | Slot routing, merge, metadata, `inject_external_stream` |
| `ground_station/service/__main__.py` | +34 | ?0 | `--rtos-bridge` and `--rtos-interval` flags |
| `ground_station/service/tests/test_service.py` | +135 | ?0 | 7 new tests |
| `ground_station/comm/wifi_bridge.py` | +50 | ?5 | `_pending_schema_ranges`, schema name lookup, prints |
| `ground_station/comm/tests/test_wifi_bridge_stream.py` | +105 | ?0 | `TestSchemaHandlerPreservesNames` (3 tests) |
| `ground_station/platform/rtos_bridge.py` | +226 | ?0 | New module (RTOS SWD bridge) |

**Total:** ~687 added, ~23 removed. Net: ~664 lines.

---

## Verification

```powershell
PS> python -m pytest ground_station/service/tests/ -v
... 17 passed in 3.39s

PS> python -m pytest ground_station/comm/tests/test_wifi_bridge_stream.py -v
... 11 passed in 0.30s
```

All S15 service tests pass.

---

## Backwards compatibility

Public API (`submit_command`, `poll_command`, `snapshot`,
`add_listener`, `start`, `stop`, `ingest`, `ingest_decoded`,
`record_command_result`) signatures unchanged. New API:
`inject_external_stream()`. `ServiceState.streams` keys still
converted to strings in `snapshot()` for JS compatibility. Stream
entry shape extended from `{tag, values}` to `{tag, values,
sequence, source_time_ms, received, dropped, loss_pct,
last_update_ns}`.

---

## Known limitations

1. **MRAC `theta_N` full vector still missing** ¡ª firmware emits
   `mrac_state.<axis>.e` and `.u_ad` only. Firmware-owned.
2. **EKF covariance `ekf.P_*`** ¡ª same as above.
3. **RTOS bridge on the wire** ¡ª only injects 4 scalars; the full
   `s_rtos_obs` struct would need a firmware-side addition.
4. **`ch0..ch14` positional fallback still emitted** ¡ª kept
   intentionally for plugin fallback when DWARF names fail to
   resolve (defensive).

---

## See also

- [STATE.md](../STATE.md) ¡ª S15 service-fix gate
- [sessions/S15-service-fixes.md](../sessions/S15-service-fixes.md) ¡ª session brief
- [reports/S15-audit.md](S15-audit.md) ¡ª original audit
- [TELEMETRY_SPEC.md](../TELEMETRY_SPEC.md) ¡ª slot/channel reference
- [ARCHITECTURE.md](../ARCHITECTURE.md) ¡ª service layer design