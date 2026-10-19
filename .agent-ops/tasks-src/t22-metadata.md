# T22: flight-test metadata.json leaves session_id, preset and signal_map_used empty

Do not contact 127.0.0.1:8081, UDP 14550 or the probe, and do not flash. Work offline. Commit with LF line endings on your branch. Write only inside your worktree.

## Measured by the supervisor (main 1d6e83e)

A dashboard REC + Analyse run with the service started as `--preset flight_test_adaptive` wrote `logs/flight_tests/2026-09-25/214340_mrac_asymmetric/report/metadata.json` (gitignored, so read it by absolute path under `/mnt/c/Users/Acer/Desktop/UAV_lab/FreeRTOS-adaptive-controller-codex/`). It contains:

- `session_id: None`, although the session folder is `logs/sessions/20260925-214330-*` (see `raw/` and `index.csv`).
- `preset: ''`, although the service ran the `flight_test_adaptive` preset.
- `signal_map_used: {}`, although the same report found every mapped signal (no Missing Signals, RMSE computed).

The controller, payload, notes, start/stop times, ELF hash and git commit are correct.

## Task

1. In `ground_station/analysis/flight_report.py` (the metadata writer) and wherever the service passes recording info (`ground_station/analysis/flight_test_folder.py`, and the analyse hook in `ground_station/service/api.py` / `core.py`), fill in:
   - `session_id` = the session folder name.
   - `preset` = the active preset name. Take it from the session's own files if they record it (check what `raw/` contains, e.g. a session meta JSON), otherwise from the service. If it is genuinely unavailable, write null, not ''.
   - `signal_map_used` = the resolved logical-name -> key map the report actually used.
   Pass values through the JSON argv used in `_run_analysis_subprocess`. Do not splice them into code.
2. Add regression tests under `ground_station/analysis/tests/`.
3. Run `python -m pytest -q ground_station/analysis`, then the full `python -m pytest -q`. Both must pass. Paste the tail lines.
4. Regenerate the report offline for the session above into a temporary dir, and paste the resulting metadata.json.

Touch only what the fix needs. Commit on your branch.
