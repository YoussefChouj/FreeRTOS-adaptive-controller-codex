# T14: one-click flight-test pipeline (record, analyse, paper-grade report)

Do not contact 127.0.0.1:8081, do not touch the probe, and do not flash. Commit with LF line endings on your branch.

## Context

Today the operator flies:

- PID only;
- PID with the MRAC adaptive layer;
- both again with an asymmetric payload.

They want to:

- press **Record** in the dashboard, tick an **Analyse** checkbox, and fill in `controller` (`pid` | `mrac`), `payload` (`symmetric` | `asymmetric`) and free-text notes;
- press **Stop**, then automatically get an organised folder with the raw logs, metadata, publication-quality plots, metrics, and a written summary;
- compare runs across conditions.

A real sample session recorded at 12:13 today (drone disarmed on the bench, so the signals are nearly flat) is at `logs/sessions/20260925-121351-dry-bench-flight-comprehensive/`.

- It is not in git. The absolute Windows path is `C:\Users\Acer\Desktop\UAV_lab\FreeRTOS-adaptive-controller-codex\logs\sessions\20260925-121351-dry-bench-flight-comprehensive`, and it can be read from your worktree through that path or `/mnt/c/...`.
- Read its file formats before you design anything. Do not copy it into git; build a small synthetic fixture for tests instead.

Existing code to read first:

- Recording routes: `ground_station/service/api.py`, around `/api/recording/start` and `/api/recording/stop` (line 1871 onward), plus `_recorder_status`.
- The recorder class these routes call.
- `ground_station/analysis/` (`session.py`, `runs.py`, `artifacts.py`, `spectrum.py`).
- The shell UI record control: grep `recording/start` under `docs/dashboard-platform/shell/`.

Signal names: another worker is writing `ground_station/analysis/flight_signals.yaml`, which maps roles to telemetry keys, for example:

- `roll`, `pitch`, `yaw`;
- `roll_sp`, `pitch_sp`, `yaw_sp`;
- `gyro_x`, `gyro_x_sp`, …;
- `pid_out_roll`, …;
- `mrac_active`, `theta_*`, `u_ad_*`, `xm_*` (reference model);
- `motor1` to `motor4`, `throttle`;
- `vbat`, `arm`, `flymode`;
- `pos_x`, `pos_y`, `pos_z`, `pos_x_sp`, …

Do not wait for it. Load the yaml if present, and fall back to built-in defaults that match keys you actually find in the sample session. Every plot or metric whose signal is missing must be skipped with a note in the summary, never crash.

## Build

1. `ground_station/analysis/flight_report.py`, runnable as `python -m ground_station.analysis.flight_report <session_dir> [--out DIR]`, and also `--compare dirA dirB ...` for a cross-run comparison.
   - **Segmentation:** use the armed and airborne window only. Airborne means throttle above an idle threshold; when the drone was never armed (as in the bench sample), use the whole log and say so. If the log has an `mrac_active` flag, split by it as well.
   - **Metrics per axis:**
     - RMSE, MAE, max absolute and ITAE of the tracking error (setpoint − measured), for both attitude and rate;
     - steady-state standard deviation;
     - overshoot and settling time (2%) for step-like setpoint changes, detected automatically;
     - control effort (RMS and total variation of the PID / MRAC outputs and of the motors);
     - motor imbalance (the per-motor mean offset, which is how the asymmetric payload shows up);
     - MRAC: parameter convergence (time to reach and stay within 5% of the final value), parameter drift, and the RMS of `u_ad` as a fraction of the total control;
     - vbat sag;
     - telemetry quality: sample rate achieved per slot, gaps, and loss.
   - **Plots** (matplotlib only, no seaborn):
     - Style: a serif font; 3.5 in single-column and 7 in double-column figure sizes; 300 dpi PNG plus vector PDF; labelled axes with units; a colour-blind-safe palette; no chart junk. Use one shared style module.
     - Figures:
       - attitude tracking (measured against setpoint) per axis, with an error subplot;
       - rate tracking;
       - control effort and motor outputs;
       - MRAC parameter evolution and `u_ad`;
       - the error distribution (histogram or box) per condition;
       - a PSD of the attitude error (reuse `spectrum.py`);
       - a 3D trajectory (actual against desired) and its XY / XZ projections, when the position signals exist;
       - a time-series overview.
     - `--compare` produces grouped bar charts of the metrics by condition (pid/mrac × symmetric/asymmetric) and overlaid error CDFs.
   - **Outputs, organised:**
     ```
     <out>/
       metadata.json   condition, notes, session id, times, firmware/ELF hash if available, preset, signal map used, software git commit
       metrics.json
       metrics.csv
       summary.md      human-readable: conditions, a key-results table, which signals were missing, caveats (e.g. t_s is host arrival time)
       plots/*.png, plots/*.pdf
     ```
2. **Organised flight-test folder.** When a recording is started with an `analyse` or flight-test flag, create `logs/flight_tests/<YYYY-MM-DD>/<HHMMSS>_<controller>_<payload>[_<label>]/` containing `raw/` and the analysis outputs.
   - `raw/` holds either a copy of the session files or a `session_path.txt` pointer. Prefer a copy when the session is under 200 MB.
   - Also append a line to `logs/flight_tests/<date>/index.csv` (the run list for comparison).
3. **Service hook.**
   - `/api/recording/start` accepts the optional fields `analyse: bool`, `controller`, `payload`, `notes`. Keep backward compatibility, and update the `/api/routes` description.
   - On `/api/recording/stop` of such a recording, run the report in a **background subprocess** (never in the request thread; the service must stay responsive during live flights).
   - Expose its status (`pending` / `running` / `done` / `failed` plus the output path) in `GET /api/recording` and in a new `GET /api/flight_tests` listing.
4. **UI.** In the shell's existing record control, add:
   - the Analyse checkbox;
   - the controller and payload selectors;
   - a notes field;
   - after stop, a status line with the analysis state and the output folder path.
   - Match the existing plugin style. Read `docs/dashboard-platform/shell/plugin-api.md`.
5. **Tests.**
   - A synthetic session fixture with known sinusoid tracking and a step, where you know the analytic RMSE and overshoot, so you can assert the metrics.
   - Check that missing signals are skipped gracefully.
   - The folder layout.
   - The start/stop hook launches the subprocess (mock it).
   - Also run the report once for real on the bench sample session into a temp dir, and list the produced files in your report.
   - Run `python -m pytest ground_station/analysis ground_station/service -q`, then the whole tree once: `python -m pytest ground_station -q`.

matplotlib/numpy/scipy may or may not be installed. Check with `python -c "import matplotlib, numpy, scipy"`. If any is missing, say so in the report and do not pip install anything.

Report in `.agent-ops/out/t14-report.md`:

- the design;
- the files;
- the sample-session run output list;
- the verbatim test counts;
- what was NOT done.
