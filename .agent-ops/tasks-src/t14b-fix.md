# T14b: finish T14 (flight-test pipeline) — two defects

Do not contact 127.0.0.1:8081, do not touch the probe, and do not flash. Work in your worktree and commit with LF line endings. Your worktree is a new branch off `main`. Merge `worker/20260925-121441` into it first; that branch holds T14 (commit 5eb12fd).

1. **Test leak.**
   - `ground_station/analysis/tests/test_flight_report.py` and `test_flight_service_hooks.py` create 4 real folders under the repo's `logs/sessions/` on each run. Measured: 0 folders before, 4 after. Folder names end in `-test`, `-analysis-test`, `-normal-record` and `-test-flight`.
   - Make every recorder, session and flight_tests path in these tests use `tmp_path` or monkeypatch. After the fix, a run must leave `logs/` untouched: assert it by counting folders before and after in the test module teardown, or by checking manually.
   - Also check `ground_station/service/tests` for the same leak.
2. **The UI panel is not wired.**
   - `docs/dashboard-platform/shell/plugins/flight-test-panel.js` is referenced nowhere in the shell.
   - Read `docs/dashboard-platform/shell/plugin-api.md` and see how the other plugins are registered (index.html, a plugin list or a manifest).
   - Register the panel so that it appears next to the existing record control. Either move its controls into the existing record control, or make it a panel that is visible by default in the main layout. The controls are the Analyse checkbox, controller pid|mrac, payload symmetric|asymmetric, notes, and a status line after stop that shows the state and the output folder.
   - Record must send `analyse`, `controller`, `payload` and `notes` in the `/api/recording/start` body.
   - Default the preset hint to `flight_test_adaptive`.
   - Add a python test that the shell's HTML or plugin list references the plugin.
3. **Run:** `python -m pytest ground_station/analysis ground_station/service -q`. Then run `python -m ground_station.analysis.flight_report "C:\Users\Acer\Desktop\UAV_lab\FreeRTOS-adaptive-controller-codex\logs\sessions\20260925-121351-dry-bench-flight-comprehensive" --out <tmpdir>` (Windows python) and list the produced files.

Report in `.agent-ops/out/t14b-report.md`: the files changed, the verbatim test counts, the output file list, and how the panel is registered.
