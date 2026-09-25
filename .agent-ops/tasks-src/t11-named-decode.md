# T11: live streams decode positionally ("0 named") — find the root cause and fix it

Do not contact 127.0.0.1:8081, do not touch the probe, and do not flash. Work on your branch with unit tests. Commit, using LF line endings.

## Evidence (measured live by the supervisor on 2026-09-25 at 10:35, service restarted at 10:22)

- The service stdout repeats these lines, about 350 KB in 12 minutes:
  `[wifi_bridge] [S15] slot 0 decoded 48 channels (0 named, 48 positional fallback)`
  `[wifi_bridge] [S15] slot 1 decoded 23 channels (0 named, 23 positional fallback)`
- `GET /state` has `streams` whose keys are `bytes_received`, `ch0.0`, `ch0.1` … (slot 0) and `ch1.0` … (slot 1). There are no variable names. `schema_id` is "r1-s1-9F32E2EA".
- Before this restart, the dashboard reported slot 0 with 164 named vars and slot 1 with 87 named vars.
- `/health/slots` shows slot 0 at loss_pct 86.3 (received 2305, dropped 14463) and slot 1 at loss_pct 88.0.
- The co-pilot (`ground_station/service/api.py` `_COPILOT_KEYS`: status.arm, status.vbat, status.roll_deg …) finds none of its keys, so it answers "no telemetry".

## Tasks

1. Find why the named decode yields 0 names. Start from the `[S15]` log line in `ground_station/comm/wifi_bridge.py` and look at how names are attached: the schema frame, the subscribe request, the name table and the ELF/DWARF resolver. The likely suspects are a schema/name table mismatch after the T6 ELF change, a dropped schema frame, or channel counts that don't match the requested group. Fix the root cause.
2. Explain the slot loss of about 87%. Is "dropped" counted correctly? Is it a symptom of the same mismatch, or a real link budget overrun (the subscribe budget is about 1600 B/s drop-free)?
3. Rate-limit that per-frame log line: log it once per state change, not per frame.
4. Check that the co-pilot keys (`status.*`) actually exist in whatever the named streams produce. If they come from Frame A rather than the subscribe slots, make `_copilot_state` read them from where they live.
5. Add unit tests for each fix, then run the whole tree: `python -m pytest ground_station -q` from Windows python. The flashtool tests are probe-free now.

Write `.agent-ops/out/t11-report.md`: the root cause with file:line, what you changed, and the test counts.
