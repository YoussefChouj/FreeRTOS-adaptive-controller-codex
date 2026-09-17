# S4 — Typed telemetry and transport hardening (in progress)

The first S4 slice adds transport-level sequence metadata and loss accounting
to the existing typed stream decoder. `WifiBridge._decode_stream_frame()` now
tracks received and dropped sequence numbers independently per subscription
slot, including modulo-256 wrap, and publishes `slotN.received`,
`slotN.dropped`, and `slotN.loss_pct` alongside the source `seq` and `t_ms`.
The counters are reset whenever a schema is applied, so a new subscription
cannot inherit stale loss history.

Host verification:

```text
python -m pytest ground_station/comm/tests/test_wifi_bridge_stream.py \
  ground_station/platform/tests \
  ground_station/comm/tests/test_transaction_events.py -q
19 passed
```

Generated schema metadata is now emitted at
`ground_station/generated/telemetry_schema.json` and loaded through
`ground_station.platform.telemetry`. It carries the registry CRC and a stable
schema ID (`r1-s1-9F32E2EA`); loading rejects registry drift before a decoder is
used. Existing bandwidth negotiation in `build_stream_request()` remains the
authoritative guard and the generated schema identifies the accepted typed
stream (`frame_0x09`).

The powered-drone multi-slot gate used the `flight_comprehensive` preset over
MicoAir UDP 14550 after a five-round pre-clear. All four 0x08 schemas were
accepted and 5 seconds of 0x09..0x0C data were decoded into CSV replay files:

```text
slot 0: 101 samples, 20.2 Hz (dashboard_frame_a)
slot 1: 403 samples, 80.6 Hz (inner_loops)
slot 2: 403 samples, 80.6 Hz (mrac_weights)
slot 3: 403 samples, 80.6 Hz (ekf_all)
```

No decoder CRC or sequence-gap failure occurred during the capture. The
capture tool now imports `MultiStreamDecoder` directly (the missing import had
made the first run fail after schema negotiation), and its generated CSVs are
replayable by the existing stream analysis tools. Host verification is 61
stream/transport tests plus 19 platform/schema tests, and registry generation
passes `--check` with CRC `0x9F32E2EA`.
