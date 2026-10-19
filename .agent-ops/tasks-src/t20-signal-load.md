# T20: flight_report reports mapped signals as missing although they are in telemetry.csv

Do not contact 127.0.0.1:8081, UDP 14550 or the probe, and do not flash. Work offline. Commit with LF line endings on your branch.

## Reproduction (measured by the supervisor on main d113aa5)

- Real disarmed session (gitignored, so use the absolute path from your worktree): `/mnt/c/Users/Acer/Desktop/UAV_lab/FreeRTOS-adaptive-controller-codex/logs/sessions/20260925-185617/`. Read only; do not write there (telemetry.csv is long format: `received_ns,slot,key,value`; 190116 rows, 10 s).
- Run: `python -m ground_station.analysis.flight_report <that dir> --out <tmp>`. Check the CLI in `flight_report.py`. `flight_test_folder._run_analysis_subprocess` shows the exact invocation the service uses.
- Result in `summary.md` / `metrics.json`:
  1. "Missing Signals" lists roll_sp, pitch_sp, yaw_sp, gyro_x_sp..gyro_z_sp, mrac_active, xm_roll/pitch/yaw, motor1..4, pos_x_sp..pos_z_sp. As a result, the attitude RMSE/MAE/max/overshoot are all N/A.
     BUT `ground_station/analysis/flight_signals.yaml` maps e.g. `roll_sp: slot2.Ctrler.rollPID.Des`, `motor1: slot2.mymotor.motor1`, `mrac_active: slot2.mrac_flags.output_injection_on`. Those EXACT keys ARE present in telemetry.csv (grep them). So the loader or the missing-signal check loses them.
  2. Telemetry Quality: "Estimated rate: None Hz", "Median dt: 0 ns", and a gap count of 2777. This is wrong because many rows share one received_ns (one frame = many keys). Rate/dt/gaps must be computed per slot over unique frame timestamps. Report each slot's rate.
  3. The "### Motor RPM" section in summary.md is EMPTY, although metrics.json has `rpm` (motors are stopped, so stale_fraction is 1.0). It must always print per-motor lines. When every sample is stale, say "motors not spinning (all samples stale)".

## Task

1. Find the root cause of item 1. Candidates: key-discovery reading only the first N rows; slot prefix handling; the pivot dropping columns; the missing-list computed against the wrong set. Fix it. Also check xm_roll/xm_pitch/xm_yaw and gyro_*_sp: if the yaml has no mapping, or the key is genuinely not recorded, keep them listed as missing, and name in your report which keys the preset lacks.
2. Fix items 2 and 3.
3. Add regression tests under `ground_station/analysis/tests/` using a small synthetic long-format CSV that mirrors the real key names, with several keys per received_ns and keys that first appear late in the file. Do not commit the 9 MB session.
4. Run `python -m pytest -q ground_station/analysis` (must pass). Then run the real session end to end and paste the resulting Key Results table, Telemetry Quality and Missing Signals sections into your final report.

Touch only `ground_station/analysis/`. Commit on your branch.
