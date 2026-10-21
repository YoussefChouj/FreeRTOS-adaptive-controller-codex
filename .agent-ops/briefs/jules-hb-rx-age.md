# Heartbeat lockout should trust the radio, not late command replies

Base branch: `nightly-1022`. Open the PR against `nightly-1022` (not main).

## Problem (seen in flight, 2026-10-21)

`LiveWfbClient.heartbeat()` in `ground_station/service/campaign_live.py` (about lines 104-127) locks out when the
oldest unanswered heartbeat is older than `HEARTBEAT_LOCKOUT_S` (2.0 s), or after two `_send_cmd` timeouts
(`_consecutive_timeouts >= 2`). Once locked out, it sends one probe per `HEARTBEAT_PROBE_S` (1.0 s). The firmware
heartbeat timeout is also 1.0 s, so the drone treats the gap as a lost link and lands itself.

In flight the radio link was healthy. Datagrams were arriving (`rx_age_s` 0.0), but the host ingest thread was
behind (`ingest_queue` 453, `ingest_lag_s` 1.78, 0 dropped). Command results therefore came back late, the client
read "late" as "link dead", it locked out, and the flight aborted.

The B13 intent must stay: do not flood heartbeats when the radio is really silent.

## What to change

The bridge already reports radio health. `service.bridge.link_health()` (`ground_station/comm/wifi_bridge.py:592`)
returns a dict with `rx_age_s`: seconds since the last datagram from the drone, or `None` if nothing has arrived yet.
`campaign_live.py` already reads it defensively in two places (search for `link_health`), and should use the same
`getattr` pattern here.

1. Add a small private helper that returns `rx_age_s` as a float, or `None` in any of these cases:
   - there is no `service.bridge`;
   - the bridge has no `link_health`;
   - `link_health()` raises;
   - the value is `None`.
   Call `link_health()` with no arguments, because the bridge has its own clock.
2. Add a module constant next to the other heartbeat constants, in the same comment style:
   `HEARTBEAT_RX_FRESH_S = 1.0   # PROPOSED: firmware hb_timeout_s; a datagram this recent means the radio is alive`
3. In `heartbeat()`, keep the existing staleness logic (oldest pending heartbeat age, `_consecutive_timeouts`) as
   "results are late". Lock out only if results are late AND the radio is not known to be fresh:
   - If `rx_age_s` is not `None` and is less than `HEARTBEAT_RX_FRESH_S`, do not lock out. Keep the normal
     `heartbeat_period_s` cadence and return True.
   - If `rx_age_s` is `None` (no bridge, or nothing received yet), behave exactly as today.
   - If `rx_age_s` is at least `HEARTBEAT_RX_FRESH_S`, behave exactly as today: lockout plus a 1 Hz probe.
4. Make the change edge-triggered. When late results are excused because the radio is fresh, set
   `self.last_error` to a short note once per episode, not every tick. Example note:
   `heartbeat results late (radio fresh, rx_age 0.0 s): host ingest backlog, not a link loss`.
   Do not print.
5. Do not change `_send_cmd`, `_poll_heartbeats`, the constants' values, or any other file except the test file.

## Acceptance tests

Add the tests to `ground_station/service/tests/test_campaign_live.py`. Reuse the existing `FakeService` and the
fake-clock helper. For a fake bridge, see `test_link_snapshot_names_the_stalest_symbol_and_merges_the_bridge_health`.
Make the bridge's `rx_age_s` settable per test.

1. **Late results, fresh radio:** heartbeat results never come back for longer than 2 s, and the fake bridge
   reports `rx_age_s` 0.05. `heartbeat()` keeps returning True, and the number of heartbeats submitted over 3 s
   matches 5 Hz (within one frame).
2. **Late results, stale radio:** the same as test 1, but `rx_age_s` is 3.0. After the lockout `heartbeat()`
   returns False and probes at 1 Hz, as `test_heartbeat_probes_after_lockout_and_resumes_when_link_returns` expects.
3. **Radio age unknown:** the bridge returns `rx_age_s` None, or there is no bridge. The behaviour is the same as
   today. All existing heartbeat tests pass unchanged.
4. **Two command timeouts, fresh radio:** two `_send_cmd` calls time out with no result while `rx_age_s` is 0.05.
   The next `heartbeat()` returns True and submits a heartbeat.
5. **Recovery:** the radio goes stale (lockout), then becomes fresh again while results are still late.
   `heartbeat()` returns True again and the 5 Hz cadence resumes.
6. **Broken bridge:** `link_health()` raises. `heartbeat()` does not raise and falls back to today's behaviour.

## Run before the PR

```
python -m pytest ground_station/service/tests/test_campaign_live.py ground_station/service/tests/test_abort_monitor.py ground_station/service/tests/test_fly_scenario.py -q
```

All of these must pass. In the PR body, list the tests you added and the pass count.
