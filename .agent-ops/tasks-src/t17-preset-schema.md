# T17: after `--preset` loads, the service never registers the preset's 0x08 schemas

Do not contact 127.0.0.1:8081, UDP 14550 or the probe, and do not flash. Work offline with fake sockets or bridges, like the tests in `ground_station/service/tests/` and `ground_station/comm/tests/`. Commit with LF line endings on your branch.

## Evidence (supervisor, live, merged main fa868e0, 2026-09-25 16:05, measured)

Command: `python -m ground_station.service --preset flight_test_adaptive`. The service stdout, in order:

```
[S15] Sent dashboard schema request (slot 0, 54 vars, divider=4) ...
[S15] Sent dashboard-panel-extras schema request (slot 1, 28 vars, divider=5)
Registered schema for slot 0: 54 ranges (54 named, 0 unnamed) ...
[service] Preset 'flight_test_adaptive': 4 slots, loading...
Registered schema for slot 1: 28 ranges (28 named ...)
[service] Pre-clear drained 350 stale packets (clean=True)
[service]   slot 0 -> dashboard_frame_a @ 40 Hz (divider=2, 93 vars, 36 ranges)
[service]   slot 1 -> inner_loops @ 80 Hz (divider=1, 9 vars, 6 ranges)
[service]   slot 2 -> flight_test_outer @ 50 Hz (divider=1, 24 vars, 11 ranges)
[service]   slot 3 -> flight_test_position @ 20 Hz (divider=4, 9 vars, 5 ranges)
[service] Preset ... loaded.
[S15] slot 0 decoded 74 channels (0 named, 74 positional fallback)
```

- **No** "Registered schema" line appears for any preset slot.
- `GET /health/slots` lists only slot `"0"`: 52 frames/s, loss 0, keys `ch0.0`..`ch0.73` (positional). Slots 1–3 are absent entirely.
- The same preset through `python -m ground_station.livewatch.capture_preset flight_test_adaptive --secs 10` (service stopped) gets all 4 schemas back (`<- schema for slot N`) and streams 46.7/93.5/46.7/18.7 Hz. **So the firmware is fine; the bug is in the service/bridge path.**

## Hypothesis to confirm or refute by reading the code

- `apply_startup_preset` (`ground_station/service/__main__.py`, around line 140) calls `preclear_fc_subscriptions(host, port)` (`ground_station/livewatch/capture_preset.py:121`). That function opens its **own** UDP socket.
- The MicoAir module replies to the last sender.
- So the 0x08 replies to the bridge's following `subscribe_slot` requests may go to the dead pre-clear socket, or be drained. Alternatively, the bridge may drop them because of state that `subscribe_slot` or the pre-clear leaves behind: `_pending_schema_ranges`, `_resubscribe_layout = None`, the watchdog stop, or an exception in `_handle_schema_frame` when building `StreamRange(_names=...)`. That exception path was just added by T16 in `wifi_bridge.py` around line 2037.
- Read `_handle_schema_frame`, the 0x09 data path (how it treats data without a schema), and `subscribe_slot`.

## Fix (minimum)

1. Pre-clear through the bridge's own socket when a bridge is present, **or** make the preset loader robust:
   - after subscribing each slot, wait up to 1.5 s for that slot's schema to register in the bridge;
   - re-send `subscribe_slot` up to 3 times;
   - log `slot N schema registered (k ranges)` or `slot N schema MISSING after 3 tries`.
   - Do both if the root cause is the socket.
2. Any exception inside schema handling must be logged, not silently swallowed.
3. When 0x09 data arrives for a slot that has a pending subscribe but no schema, log that once and trigger a single re-subscribe of that slot (rate-limited).

## Tests (offline)

- A fake bridge/socket where the first 0x08 reply for a slot is lost: the loader retries and all 4 slots end up registered, with exact per-name keys such as `slot2.rpm_dbg_period_cyc[0]`.
- Coalesced ranges (count > 1) through `_handle_schema_frame` produce named keys with no exception.
- Run `python -m pytest ground_station/comm ground_station/service ground_station/livewatch -q`. Then run the whole tree: `python -m pytest ground_station -q`. Note that `test_opencode_json_exists` fails in a worktree only because `opencode.json` is untracked in main; ignore that one.

Report in `.agent-ops/out/t17-report.md`:

- the root cause at file:line, stating whether it was confirmed or is the best hypothesis;
- the fix;
- the verbatim test counts;
- what was NOT done.
