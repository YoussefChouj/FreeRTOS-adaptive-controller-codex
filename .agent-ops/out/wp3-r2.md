STATUS: done

Files changed:
- run_test_debug.py, sim_debug.py: Deleted via git rm.
- ground_station/service/campaign_runner.py (255 lines): Added tick-count timeout bounds `max_ticks = ceil(flight_timeout_s / dt_s)` to all wait loops. Implemented timeout abort mapping to level 1 for in-flight and `operator_needed` for landing. 
- ground_station/service/tests/test_runner.py (273 lines): Renamed `TestClock` to `FakeClock`. Increased `flight_timeout_s` to 120.0s to allow test A's 11s trajectory to complete. Added test I to verify takeoff timeout behavior.

Root cause of A/B hang:
In round 1, loops lacked timeout bounds. Test A hung because its trajectory takes ~11s but `flight_timeout_s` was 3.0s, which normally would abort, but lack of timeout bounds meant it just kept waiting forever if it never reached DONE. Test B hung because the first flight's landing timed out in the loop condition (exceeding 3.0s), leaving the drone airborne. The next flight's `takeoff()` was rejected by `FakeDrone`, so it never entered CLIMB, and the unbounded `run_loop_until(HOVER)` hung forever.

Verification:
- `python -m pytest -q -p no:cacheprovider ground_station/service/tests/test_runner.py` -> 10 passed in 1.85s.
- `python -m pytest -q -p no:cacheprovider ground_station/service/tests/test_fake_drone.py ground_station/service/tests/test_abort_monitor.py ground_station/service/tests/test_campaign_schema.py ground_station/flashtool/tests/test_code_gate.py` -> 172 passed in 0.74s.

Open risks: None verified.
SUBSTITUTIONS: none
