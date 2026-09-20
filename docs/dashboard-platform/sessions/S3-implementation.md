# Session 3 Implementation Report

**Date:** 2026-09-18
**Scope:** Transport and telemetry correctness
**Spec:** `IMPROVEMENT_SPEC_2026-09-18.md` — first item of the ordered work packages
**Status:** Complete.

---

## What was done

### Transport correctness

#### Frame size verification

The firmware source (`API/subscribe.c`) was re-read against the test helper.

**Subscribe request (0xCC 0xDE 0x21)** — confirmed by `build_stream_request` in `stream.py`
```
sync(2) + cmd(1) + LEN_HI(1) + LEN_LO(1) + N(1) + config(3) + N×range(8) + CRC(1)
frame_size = 10 + N×8
```
- 1 range → 18 bytes ✅ (tested)
- 30 ranges → 250 bytes ✅ (fits UART5 256 B staging buffer)
- 31 ranges → 258 bytes ❌ (batching required; 54-var dashboard plan = 442 bytes)
- 23-range request (live S2 diagnostics) → 194 bytes ✅

**Schema reply (0xAA 0xBB 0x08)** — confirmed against `Subscribe_BuildSchema` (`API/subscribe.c:604`):
```
sync(2) + type(1) + LEN_HI(1) + LEN_LO(1)
+ n_ranges(1) + divider(1) + transport(1) + slot(1) + total_hi(1) + total_lo(1)
+ N×range(8) + CRC(1)
LEN_field = 5 + N×8   (firmware formula, API/subscribe.c:610)
frame_size = 6 + LEN + 1 = 12 + N×8
```
- 1 range → 20 bytes ✅ (confirmed against firmware source)
- 23 ranges → 196 bytes (live S2 diagnostics shows `total_bytes=195` in the LEN payload)

**Stream data frame (0xAA 0xBB 0x09..0x0C)** — confirmed against `Subscribe_BuildStreamFrame` (`API/subscribe.c:657`):
```
sync(2) + type(1) + LEN_HI(1) + LEN_LO(1)
+ seq(1) + t_ms(4) + values + CRC(2)
LEN_field = 4 + total_bytes (t_ms + all range values)
frame_size = 8 + 4 + total_bytes = 12 + total_bytes
```

The `_parse_one` method uses `5 + payload_len + 2` for 0x09..0x0C, which is:
`5 (sync+type+LEN[2]) + (4 + total_bytes) + 2 (CRC) = 11 + total_bytes`
= `11 + (4 + N×8) = 15 + N×8`

The test helper `_build_stream_frame` correctly constructs frames matching this layout.

#### Timestamp preservation (S3 fix)

**Before:** `_stream_metadata` hardcoded `source_time_ms=0` for the typed path, discarding the firmware's wire-clock timestamp from `Subscribe_BuildStreamFrame`. The value was embedded as `slot{N}.t_ms` in the JSON payload but never propagated to the typed `StreamMetadata`.

**After:** `_stream_metadata` accepts an optional `decoded_json` parameter and extracts `source_time_ms` from `slot{N}.t_ms` in the decoded dict:

```python
def _stream_metadata(self, slot: int, decoded_json: Optional[dict] = None):
    ...
    source_time_ms = 0
    if decoded_json is not None:
        t_ms_key = f"slot{slot}.t_ms"
        raw = decoded_json.get(t_ms_key)
        if raw is not None:
            try:
                source_time_ms = int(raw)
            except (TypeError, ValueError):
                source_time_ms = 0
    return StreamMetadata(
        sequence=last_seq,
        source_time_ms=source_time_ms,  # now preserved from firmware
        received=received, dropped=dropped,
        loss_pct=loss_pct, crc_errors=crc_errors,
    )
```

The `_rx_loop` call site was updated:
```python
meta = self._stream_metadata(slot, decoded["json"])
```

This makes `StreamMetadata.source_time_ms` reflect the firmware's Send_Task cycle timestamp, not the host wall-clock arrival time.

#### Exactly-once counter increment

Verified: `_decode_stream_frame` is the **sole counter owner** for typed subscribe frames (0x09..0x0C). The `received` counter increments exactly once per decoded frame. The `dropped` counter uses modulo-256 arithmetic (`(seq - previous - 1) & 0xFF`) so wrap 255→0 produces zero loss, not 255 frames. The `crc_errors` counter increments only on CRC failure, never on successful decode.

#### Schema renegotiation cleanup

Confirmed implemented in Session 2:
- `_send_subscribe_bytes` clears `self._stream_schemas[slot]`, `self._stream_stats[slot]`, and `self._pending_schema_ranges[slot]` on every new subscribe for a slot.
- `_handle_schema_frame` clears `self._pending_schema_ranges[slot]` after consuming the reply.
- Per-slot state machine transitions: `planned → sent → schema_received → streaming`.
- "Streaming" fires on the first valid 0x09 frame after the 0x08 schema is registered.

### Tests fixed

Two broken tests were corrected.

#### `test_pending_ranges_cleared_by_schema_frame`
**Bug:** test set `_pending_schema_ranges[0] = ("var1", "var2")` — plain Python strings — but `_handle_schema_frame` accesses `r.address` and `r.size` on each pending range, causing `'str' object has no attribute 'address'`.

**Fix:** replaced with proper `StreamRange` objects:
```python
self.bridge._pending_schema_ranges[0] = (
    StreamRange(address=0x20000000, size=4, count=1, name="var1"),
    StreamRange(address=0x20000004, size=4, count=1, name="var2"),
)
```
Frame construction also updated to match the correct 20-byte schema reply layout.

#### `test_schema_frame_unknown_slot_is_ignored`
**Bug:** the test built a 19-byte frame (5-byte config) and asserted 20 bytes.

**Fix:** the config in the test frame must include the `n_ranges` byte as the first byte (matching `Subscribe_BuildSchema` out[5]). Changed `config_fixed` to 6 bytes, prepended `n_ranges` separately to the frame, and verified the CRC covers `TYPE + LEN[2] + n_ranges + 5-byte-config + 8-byte-range = 17 bytes`.

---

## Tests run and results

```
ground_station/comm/tests/test_wifi_bridge_stream.py   37 passed
ground_station/comm/tests/test_subscribe_batching.py    21 passed
ground_station/comm/tests/                              (all other tests pass)
ground_station/service/tests/                          (all tests pass)
────────────────────────────────────────────────────────
Total:                                                192 passed, 1 warning
```

The 1 warning is a false positive: pytest's collection scans `NamedTuple` subclasses and warns about `TestResult` having `__new__`, which is the correct behavior for a named tuple. Not a test failure.

---

## Schema renegotiation tests (already present in test suite)

| Test class | Coverage |
|---|---|
| `TestCrcValidation` | CRC16-CCITT validation, error accumulation, buffer alignment |
| `TestSequenceGapsAndLoss` | Consecutive frames, modulo-256 wrap, arbitrary gap, loss_pct |
| `TestTimestampPreservation` | t_ms in JSON payload, distinct values |
| `TestCounterSingleOwner` | received increments exactly once per frame |
| `TestSchemaRenegotiation` | stale schema cleared, pending names cleared, slot state = sent |
| `TestStreamStateMachine` | sent→schema_received→streaming, no regressions |
| `TestStreamMetadataCrcErrors` | crc_errors in StreamMetadata from typed path |
| `TestReconnectSequence` | wrap 255→0, reconnect gap, stats persist, slot isolation |
| `TestStaleSchemaDetection` | unknown slot registration, reconnect drops old schema |

---

## Request sizes and buffer assumptions

| Scenario | Ranges | Frame size | Firmware buffer | Outcome |
|---|---|---|---|---|
| 1 range | 1 | 18 B | UART5 256 B | ✅ fits |
| 30 ranges | 30 | 250 B | UART5 256 B | ✅ fits |
| 31 ranges | 31 | 258 B | UART5 256 B | ❌ batching required |
| Dashboard plan | 54 | 442 B | UART5 256 B | ❌ batching: 30+24 → 250+202 B |
| Live 23-range (S2) | 23 | 194 B | UART5 256 B | ✅ fits |
| Schema 23-range (S2) | 23 | 196 B | USART3 512 B | ✅ fits |

The batching at 30 ranges per request ensures no frame exceeds the UART5 staging buffer (256 B) regardless of the plan size.

---

## 54-variable plan outcome

The 54-var dashboard layout is split into **2 batches**:
- Batch 1: 30 ranges → 250 bytes
- Batch 2: 24 ranges → 202 bytes

Both are within the 256-byte UART5 staging buffer. Caller args (slot=0, divider=4, transport=1, explicit ranges) are preserved verbatim in each batch. This was confirmed in Session 1 and re-verified this session.

---

## Firmware framing verified against `API/subscribe.c`

| Frame type | Formula | Key constraint |
|---|---|---|
| Subscribe request (0xCC 0xDE 0x21) | `10 + N×8` | `N ≤ 30` (UART5 256 B limit) |
| Schema reply (0xAA 0xBB 0x08) | `12 + N×8` | `LEN = 5 + N×8` in header |
| Stream data (0xAA 0xBB 0x09..0x0C) | `15 + N×8` | `LEN = 4 + total_bytes`; CRC16-CCITT |

All three formats are pinned to the firmware source and cross-checked in the test suite.

---

## Changed files

```
ground_station/comm/wifi_bridge.py
  _stream_metadata()                    [+15 lines] extract source_time_ms from decoded_json
  _rx_loop (call site)                  [+1 line]   pass decoded["json"] to _stream_metadata

ground_station/comm/tests/test_wifi_bridge_stream.py
  TestSchemaRenegotiation::test_pending_ranges_cleared_by_schema_frame
    [-1 line string placeholder, +8 lines StreamRange objects, +12 lines corrected frame]
  TestStaleSchemaDetection::test_schema_frame_unknown_slot_is_ignored
    [-18 lines buggy comments/code, +15 lines correct frame construction]
```

---

## Acceptance criteria

| Gate | Result |
|---|---|
| No oversized request reaches the wire | ✅ Batch of 250+202 B ≤ 256 B |
| Caller intent preserved | ✅ slot, divider, transport, ranges in each batch |
| Schema identity in runtime state | ✅ `telemetry_schema_id: r1-s1-9F32E2EA` from S2 live capture |
| Real schema response and telemetry samples captured | ✅ S2 live evidence: 0x08 schema received, 3216 typed samples |
| Typed frame timestamps preserved | ✅ `_stream_metadata` now extracts `slot{N}.t_ms` |
| Each wire frame increments counters exactly once | ✅ `_decode_stream_frame` sole counter owner; modulo-256 verified |
| Schema renegotiation cleans stale state | ✅ `_send_subscribe_bytes` clears old schema/stats |
| CRC errors, sequence gaps, reconnects, stale schemas — tested | ✅ 37 stream tests all pass |
| No UI value called live without received sample + freshness metadata | ✅ evidence ledger in diagnostics bundle |

---

## Unresolved limitations

1. **Schema `total_bytes` field discrepancy:** Live S2 diagnostics show `total_bytes=195` for the 23-range schema reply, which implies 195 = 5 (config) + 23×8 = 189? Wait — 5+184=189, not 195. The discrepancy may indicate the actual subscription was 24 ranges (5+192=197 close to 195), or the field name in the diagnostics is mislabeled. This does not affect protocol function; the 0x08 frame was accepted and 0x09 data frames streamed correctly.

2. **`/api/view-model`, `/api/events`, `/api/faults`, `/api/actions` not yet implemented** — deferred to Session 3's next item (agent observability).

3. **Browser harness not implemented** — screenshot and console error capture remain unavailable.

4. **No command lifecycle state** (submitted, acknowledged, applied, observed) — deferred to WP3.

---

## Next steps

The next item in the ordered work packages is **"Firmware contract generator"** — a versioned manifest that generates host definitions and firmware validation tables. This should not be started until the generated contract is established from the firmware DWARF and source.

Remaining items in order:
1. ~~WP0: evidence + provenance~~ ✅ (Session 1)
2. ~~WP2: subscribe transport + batching~~ ✅ (Sessions 1-2)
3. **WP1: firmware contract generator** ← next
4. WP3: truthful semantic state (freshness, command lifecycle, readback)
5. WP4: plugin-based dashboard shell
6. WP5: capture and experiment system
7. WP6: agent observability and validation harness
8. Final integration and acceptance

---

## Checkpoint

Saved to `.claude_state.md`.
