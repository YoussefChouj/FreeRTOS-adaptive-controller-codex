# T21: /health/slots loss_pct stuck at a preset-load value

Do not contact 127.0.0.1:8081, UDP 14550 or the probe, and do not flash. Work offline. Commit with LF line endings on your branch. Write only inside your worktree.

## Measured by the supervisor (main 1d6e83e, live service with `--preset flight_test_adaptive`)

`GET /health/slots`, first sample shortly after the 21:33 restart and again at 21:40:

| slot | received (21:40) | dropped (both samples) | loss_pct (both samples) |
| --- | --- | --- | --- |
| 0 | 10253 | 220 | 87.302 |
| 1 | 20354 | 43 | 36.134 |
| 2 | 20204 | 0 | 0.0 |
| 3 | 5013 | 0 | 0.0 |

`dropped` did not rise between the two samples, so every drop happened during the preset load. But `loss_pct` also did not change, and 87.302 does not equal 220/(10253+220) (about 2%). A value this high suggests it was computed while a freshly reset decoder had only about 32 received frames, and then never updated.

## Leads (verify, do not assume)

- `ground_station/service/telemetry_adapter.py:479-483` merges with `meta.X or existing.get(...)`. A later 0.0 loses to the old value. Also check which `received`/`dropped` the merge keeps compared with where `loss_pct` came from.
- Producers: `ground_station/comm/wifi_bridge.py:2240-2250` (per-slot decoder counters), `ground_station/service/core.py:574,603,761`, `telemetry_adapter.py:561-573`.
- The preset loader re-registers schemas (T17 retries, commit cd43f9d). Check whether this creates a new decoder or resets counters, while another path keeps the older counters.

## Task

1. Find the root cause: explain which code path produced 87.302 and why it never updates.
2. Fix it so that `loss_pct` shown in `/health/slots` (and anywhere else in `api.py` that reports it) always equals 100*dropped/(received+dropped), computed from the SAME counters as the reported received/dropped. Keep a genuine 0.0 as 0.0.
3. Add a regression test under the matching `tests/` directory that reproduces the stuck value, for example a preset-load or schema reset followed by many clean frames.
4. Run `python -m pytest -q ground_station/service ground_station/comm`, and then the full `python -m pytest -q`. Both must pass. Paste the tail lines.

Touch only what the fix needs. Commit on your branch and report the root cause in 3-5 lines.
