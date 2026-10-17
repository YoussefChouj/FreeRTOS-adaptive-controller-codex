# Stream ladder step B: int16 values with a per-variable scale

Goal: halve the bytes per logged variable on the telemetry stream so twice the variables fit the 921600-baud link,
without changing anything for existing float32 subscriptions. This is flight-critical firmware: correctness and
bit-exact tests matter more than speed. Read AGENTS.md first.

Where: firmware `API/subscribe.c`, `API/subscribe.h` (slots, Subscribe_StreamTick, link guard, SUBSCRIBE_SEND_TASK_HZ 100),
harness `API/tests/test_subscribe_harness.c` (run by `python tools/host_tests.py`), host `ground_station/livewatch/stream.py`
(subscribe command builder + frame decoder, STREAM_MAX_BYTES), `ground_station/livewatch/manifest.py`
(compute_multi_slot_budget), `ground_station/livewatch/campaign_capture.py` (log planner, GUARD_SEND_HZ, budgets).
Frame today: `AA BB (09+SLOT) LEN_HI LEN_LO SEQ T_MS(u32 LE) values CRC16-CCITT BE`, every value float32 LE.

Requirements
1. Per-variable wire type chosen by the host in the subscribe command: F32 (today) or I16 with a float32 scale.
   Old host commands and old frames must stay byte-identical (float32-only slot = same bytes as now). Pick a command
   encoding that an old firmware rejects cleanly (error reply), never misparses.
2. Firmware encode, no division in the hot path (store 1/scale): q = round-to-nearest(x * inv_scale), saturate to
   [-32767, 32767]; NaN -> -32768 (sentinel); +/-inf saturate. Source vars may be float32 or 32-bit ints: define what
   I16 means for each and test it. Little-endian int16 on the wire.
3. Link guard and every budget (firmware guard, manifest, campaign_capture planner, STREAM_MAX_BYTES cap) price the
   real per-variable width (4 or 2 B). Keep the 100 Hz send-rate pricing from step A.
4. Host decoder: int16 * scale -> float; -32768 -> NaN; count saturated samples (|q| == 32767) per variable in the
   stream stats. Mixed F32/I16 slots decode correctly.
5. Scale table: new `ground_station/livewatch/var_scales.yaml` (var name or glob -> scale, unit, why), with resolution
   and range chosen from each variable's physics (angles, rates, velocities, positions, PWM, flags). Only variables with
   an entry may go I16. A plan opts in with a `narrow` flag (planner argument and `.fly` header keyword, default OFF),
   so today's flights stay float32 unless a file asks.
6. Tests: C harness (encode rounding incl. .5 cases and negatives, saturation, NaN, int sources, guard pricing with
   mixed widths, old-command compatibility); Python (round trip within scale/2, NaN sentinel, saturation count,
   old float-only frames decode identically, budget/planner with narrow on and off, yaml loads and every entry is sane).
Do not touch USER/, OBJ/, BSP/ or logs/. Run `python tools/host_tests.py` and
`python -m pytest ground_station/livewatch -q` and list the results in the PR.
