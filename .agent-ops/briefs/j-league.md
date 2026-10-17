# j-league: cross-flight controller league table (host analysis only)

Branch: `overnight-2026-10-08`. Open ONE PR against it. Do not ask before opening the PR.

## Goal

One command that takes the flight directories of a lab day and prints a league table: which controller kind
(PID, ADRC, LQR, MPC) did best on each axis (roll, pitch, yaw, z, xy), with confidence intervals over the
repeat flights. On lab day 2026-10-18 each of ADRC, LQR and MPC flies ACTIVE on each axis (12 active flights),
with the other kinds running in shadow on all flights.

## Extend, never duplicate (read these first)

- `ground_station/analysis/flight_report.py`: `--compare dirA dirB ...` (usage at L6), `plot_compare_metrics`
  (~L1458), `generate_compare_report` (~L1923). Add the league as a new compare mode or a new section there, or
- `ground_station/analysis/flightlab/`: `compare.py`, `metrics.py`, `segments.py`, `registry.py`, `plugins/`.
  A new `plugins/league.py` plus a `compare.py` entry is the preferred shape if it fits the registry.
- Reuse `flightlab/metrics.py` and `segments.py` for error metrics and step/hold segments. Reuse the shadow vs
  active detection in `ground_station/analysis/adaptive_review.py` (~L157).
- Shadow counterfactuals: call the existing replays (`mrac_log_replay.py`, `mrac_xy_replay.py`,
  `refmodel_replay.py`) only where they apply; do not write new replay code.

## Which kind was active (per flight, per axis)

- XY: the latch is in `TASK/StabilizerTask.c` (~L1903-1913, latch ~L2145): `s_xyc_kind`, `g_xy_ctrl_kind`
  (0 PID, 1 ADRC, 2 LQR, 3 MPC), `g_ctrl_pid_fallback_active`, `g_xyc_delay_ticks`. These are logged
  (`campaign_capture.py` ~L191). Use the logged `s_xyc_kind`; a fallback event is a change to PID while
  `g_ctrl_pid_fallback_active` is 1.
- Roll, pitch, yaw, z: search `TASK/` and `API/` for their kind/latch variables. If none is logged, read the
  `set` line of the flight's `.fly` file (copied into the flight directory or named in its metadata) and say in
  the PR body which axes use this fallback. Never guess silently: an axis with no source is "unknown".

## Metrics (per flight, per axis, per segment)

- Error RMS and max; for step segments overshoot, rise time and settle time. The settle band and the rise
  thresholds are named PROPOSED constants.
- Effort RMS and effort slew RMS; saturation fraction (the limits come from the code, cite file:line).
- Latch changes and fallback events with their times.
- Data-quality gate: gap fraction from SEQ (wrap-aware) per flight. A flight above a named PROPOSED threshold
  is shown but marked and left out of the ranking.
- Bootstrap CIs over the repeat flights of one kind and axis (seeded, named PROPOSED resample count).
- Rank per axis by a stated primary metric (error RMS) with the CI; a tie when the CIs overlap.

## Outputs

`league.md` (table per axis, tie marks, excluded flights with reasons), `league.json` (every number, the git
sha, the input dirs), and plots (per axis bar chart with CIs, per kind step overlay) in the `--out` dir.

## Tests (pytest, synthetic data only)

1. Synthetic second-order step responses with known overshoot and settle time: metrics exact within 1%.
2. Latch detection from a synthetic `s_xyc_kind` trace, including a fallback event to PID.
3. Missing columns (no latch variable, no effort column) degrade to "unknown" or a skipped metric, never a crash.
4. A flight above the gap threshold is excluded from the ranking and listed with its reason.
5. Bootstrap CI is deterministic for a fixed seed; overlapping CIs give a tie.
6. End to end: three synthetic flight dirs in the real on-disk layout give a league.md and league.json with the
   expected keys (run the real module, not a mock dict).

## Rules

- Run only scoped tests: pytest on the new and changed test files, `ruff check` on changed .py files, and
  `python tools/host_tests.py` must stay 26/26.
- If a test fails that you cannot fix in one try, list it in the PR body with its error line and go on.
- CRLF line endings in .py like the existing files. No new dependencies.
- Commit and push in small steps.
- Do not edit: `ground_station/livewatch/campaign_capture.py`, `ground_station/livewatch/stream.py`,
  `ground_station/livewatch/var_scales.yaml`, `ground_station/autotune/frf.py`,
  `ground_station/autotune/excitation.py`, `ground_station/analysis/att_ctrl.py`,
  `ground_station/analysis/hover_fit.py`, `ground_station/analysis/att_fit.py`,
  `ground_station/analysis/live_tracker.py` and the other tracker files, anything under `API/`, `TASK/`,
  `USER/`, and `ground_station/service/campaigns/*.fly`. Other sessions own these.
- If a spec point conflicts with the code, follow the code and say so in the PR body.
- In the PR body: each test and its result, the per-axis kind source, and every PROPOSED constant.
