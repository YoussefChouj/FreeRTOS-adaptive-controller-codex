# Task: Fix Agent Map Step 1 (C Parser Bug)

The previous worker successfully created the `ground_station/agent_map` package but timed out before fixing a bug.
Currently, running `pytest ground_station/agent_map/tests` yields 1 failure:

```
FAILED ground_station/agent_map/tests/test_firmware.py::test_line_numbers_after_comments
```
The C parser in `ground_station/agent_map/firmware.py` (`scan_file` or related logic) is miscalculating line numbers after block comments (expecting line 11 but getting 9).

## Instructions
1. Fix the line number tracking logic in `ground_station/agent_map/firmware.py`.
2. Verify all tests pass (`pytest ground_station/agent_map/tests`).
3. Ensure `python -m ground_station.agent_map build` runs without errors.
4. Ensure `python -m ground_station.agent_map explain s_ekf` returns a correct compact text answer (<= 25 lines) as specified in step 1.
5. Exit cleanly when done.
