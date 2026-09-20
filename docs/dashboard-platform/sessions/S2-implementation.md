# Session 2 Implementation Report

**Date:** 2026-09-18
**Scope:** Restore live Wi-Fi subscribe telemetry and harden the stream decoder.
**Status:** Complete for the live-data blocker; no firmware change required.

## Root cause

The firmware was already emitting valid `0x09` subscribe frames. `API/subscribe.c::Subscribe_BuildStreamFrame` places the sequence byte at `frame[5]`, outside the `payload_len` field. The actual frame size is `2 sync + 1 type + 2 length + 1 sequence + payload_len + 2 CRC = 6 + payload_len + 2`.

`ground_station/comm/wifi_bridge.py::_parse_one` used `5 + payload_len + 2` for the stream family. It removed one byte too early, leaving the final CRC byte in the receive buffer. `_decode_stream_frame` rejected the truncated frame, so no `0x09` sample reached the service and the next frame became misaligned.

## Changes

- Fixed stream framing to `6 + payload_len + 2`.
- Added `_parse_one` integration tests for one complete frame and two back-to-back frames.
- Added CRC16-CCITT validation. Invalid frames are dropped and counted in per-slot `crc_errors`.
- Exposed `crc_errors`, computed loss percentage, and wire `source_time_ms` in diagnostics active-slot records.
- Preserved the firmware timestamp in typed `StreamMetadata` instead of hardcoding zero.
- Removed a second typed-frame `received` increment in the RX loop; `_decode_stream_frame` is now the sole counter owner for typed subscribe frames.

No firmware source, ELF, rebuild, or flash was changed. The existing firmware call path already invokes `Subscribe_StreamTick()` in MIXED mode.

## Validation

- `ground_station/comm/tests/test_wifi_bridge_stream.py`: 26 passed.
- Selected stream/service tests: 69 passed.
- Offline comm/service tests, excluding the hardware-rate test: 181 passed, 1 deselected, 3 subtests passed.
- The full `ground_station` collection reached 497 passing tests; one failure was only a UDP 14550 port conflict while the live service was running. The isolated test passed after stopping the service.
- `test_downlink_real` received 202 valid subscribe frames over 10 seconds (~20 Hz) with divider 4; its fixed >=40 Hz assertion is incompatible with that intentional subscription rate.

## Live evidence

Fresh service process from current source, Wi-Fi `192.168.4.1:14550`:

- `connected: true`
- `telemetry_schema_id: r1-s1-9F32E2EA`
- `slot_freshness_ttl_ns: 30000000000`
- service `samples: 6432` after a short capture
- slot 0 received `3216` typed samples with `244` decoded values per sample
- slot 0 wire timestamp advanced to `1031684 ms`
- external RTOS stream continued receiving samples
- slot 0 request: divider 4, 23 wire ranges
- slot 0 schema response: frame type `0x08`, 23 ranges, 195 data bytes
- active slot loss was 3.25%, `crc_errors: 0`, `last_error: null`

This proves the Wi-Fi path, schema registration, frame framing, decoding, timestamp propagation, and service ingestion are functioning. Nonzero sequence gaps are observed wireless loss and are surfaced as metadata.

## Remaining follow-up

The dashboard still lacks the broader WP4–WP6 work: registry-driven workspace plugins, per-signal freshness/readback state, experiment plans, replay/FFT, browser evidence capture, and diagnostics view-model/events/actions. The service global `samples` count includes both the raw typed envelope and the derived sidebar envelope for each slot-0 frame; consumers should use per-stream `received` for wire-rate metrics until that aggregate is given explicit unique-frame semantics.

