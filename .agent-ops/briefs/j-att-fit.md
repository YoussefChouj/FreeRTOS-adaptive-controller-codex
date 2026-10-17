# j-att-fit: host fit of the hover model and the per-axis design model (b, a, tau, D)

Branch: `overnight-2026-10-08`. Open ONE PR against it. Host Python only: no firmware, no USER/ files.
Two parallel Jules sessions are open: j-att-ctrl (adds `ground_station/analysis/att_ctrl.py`, firmware
`API/att_ctrl.*`) and j-sysid-multi (adds `direct_iv` to `ground_station/autotune/frf.py`). Do not edit those
files; this PR adds new modules only, plus tests and one CLI entry. Read first:
`ground_station/analysis/thrust_replay.py` and the log_corpus module it uses (how airborne logs are found and cut),
`ground_station/analysis/battery_model.py`, `ground_station/analysis/motor_lag.py`, `research/sim/cascade.py`
(fit_rate_plant), `ground_station/autotune/frf.py` and `synth.py`, `TASK/StabilizerTask.c` (HOVER_THR_FREE ~2253,
Z_ratePID, POS_LOOP_DIV, `real_voltage`), `API/pid.c` (output clamps).

## Why

Tomorrow's lab day flies one hover-to-empty flight, three 4-axis SysID flights and about 20 trajectory flights.
Every trajectory flight starts with a 5-10 s position hold at the hover point. We fit two things from these logs,
then write them to the drone over VAR_WRITE while disarmed:
1. the hover throttle model u_hov(V) = c0 + c1*(V0 - V), so height control does not drift as the pack sags;
2. per axis (roll, pitch, yaw, z) the design model
   `y' = w, w' = -a*w + b*m, m' = (u_d - m)/tau, u_d(t) = u(t - D)` (u in PID output units, see j-att-ctrl:
   `Ctrler.gyroxPID.U`, `gyroyPID.U` before the pitch sign flip, `gyrozPID.U`, `Z_ratePID.U`; w in rad/s or m/s).
The user prefers one global linear parametric model per axis that holds for all flight scenarios.

## Module 1: `ground_station/analysis/hover_fit.py`

- Segments: (a) the hover-to-empty flight, all of its steady hover; (b) every hold segment of every flight
  (detect them: |vz| and |vxy| small, height near the target, no SysID active, airborne rule from log_corpus;
  state the thresholds as constants).
- Per segment window (about 1 s): mean thrust command at the point the hover throttle enters
  (find the exact symbol: the Z loop output plus the hover term), tilt correction cosR*cosP, mean battery voltage
  (filtered `real_voltage`), height (flag ground effect below 0.40 m, PROPOSED).
- Fit u_hov*cosR*cosP = c0 + c1*(V0 - V) by weighted least squares (V0 = a fixed reference you state, e.g. the
  pack's full-charge voltage from battery_model). Report c0, c1, their 95% bounds, residual RMS, and a leave-one-
  flight-out check: the fit without flight j predicts flight j's hover throttle within what error.
- Option: a quadratic term in (V0 - V) when it lowers the leave-one-out error by more than 20% (report both).

## Module 2: `ground_station/analysis/att_fit.py`

- Inputs: one or more sessions (the same CLI style as `autotune/cli.py`), axis list, optional segment filter.
- Time-domain output-error fit of (b, a, tau, D) per axis, from u (applied plant input: the PID output plus the
  MRAC u_ad when MRAC injects, see `mrac_state`) to w (gyro rate, or climb rate for z): simulate the model with
  ZOH at the log rate from the logged u, minimize the w prediction error over (b, a, tau) for each integer delay
  D on a grid (0-12 ticks at 200 Hz; the log may be 100 Hz, handle both), pick D by the lowest error. Estimate a
  constant input bias per segment (trim), not shared across segments.
- Segment the data so the fit never integrates across a gap (stream drops are counted by SEQ: split there).
- Initial guess from `research/sim/cascade.py` / `autotune` defaults; bounds b > 0, a >= 0, 0.005 <= tau <= 0.2 s.
- Validation (the gate the run sheet uses): hold-out flights; report per axis the one-step and the 0.5 s-ahead
  prediction error, the Theil inequality coefficient TIC (pass < 0.25, PROPOSED), VAF %, and the fit on the
  SysID flights vs the trajectory flights. If j-sysid-multi's `direct_iv` exists at run time, also compare its
  frequency-domain (b, a, tau, D) to this one and print both (soft import; skip with a note if absent).
- Output: `att_model_<date>.json` with, per axis, b, a, tau, D (ticks at 200 Hz and seconds), bounds, the
  validation numbers, the sessions used, and the git sha. Keep the names b, a, tau, D: j-att-ctrl's VAR rows
  use them. Also the hover c0, c1 from module 1 in the same file.

## Tests (`ground_station/analysis/tests/`)

- hover_fit recovers c0, c1 within 2% from a synthetic sag log (noise, tilt, a few holds per flight).
- att_fit recovers b, a, tau, D (D exact, the rest within 5%) per axis from a synthetic closed-loop log
  (PID feedback, process and sensor noise, 100 Hz sampling of a 200 Hz sim, a few SEQ gaps).
- The fit never runs across a SEQ gap; a segment shorter than the minimum is skipped with a reason.
- TIC and VAF match hand-computed values on a tiny example.
- The JSON schema is stable (a golden key list).
Run scoped: `python -m pytest ground_station/analysis/tests/test_hover_fit.py ground_station/analysis/tests/test_att_fit.py`
and `ruff check` on the new files. List each test and its result in the PR.

## Rules

Python 3.11, numpy/scipy/pandas as already used in the repo; no new dependencies. CRLF line endings like the
existing .py files. No numbers from flights you did not see: every threshold is a named constant marked
PROPOSED. If a spec point conflicts with the code, follow the code, say so in the PR.
