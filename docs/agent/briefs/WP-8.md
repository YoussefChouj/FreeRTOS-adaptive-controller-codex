
# WP-8 brief (CEO -> Manager), 2026-10-01. OF EKF with accelerometer input + offline tuning.

Base: `main` @ cc6ebd6. Out of scope: Keil build, flash, 8081, arming (the CEO builds and flashes after review).
Default `g_of_bias_mode` stays 0 (FIXED); the operator A/B tests mode 2 in flight.

Goal: the mode-2 OF Kalman filter (`API/ekf_of.c`) uses the body accelerometer as a control input and learns an
accelerometer bias, plus a zero-velocity update on the ground, so slow real drift is tracked as velocity instead of
being absorbed into the OF bias. Tune it on the five floor-removed flight logs with a Python twin of the filter.

Facts the CEO measured on 2026-10-01 (main @ cc6ebd6):
- `API/ekf_of.c` (224 lines): state `[px, vx, bof_x, py, vy, bof_y]`, constant-velocity predict with no input,
  `Q_pos 1e-6, Q_vel 2e-4, Q_bias 5e-5` per second, `R_of 6.16e-4`, scalar Joseph update `ekf_of_update_one`, flat 6x6 P.
  Because nothing tells velocity from bias except their noise ratio, slow drift leaks into `bof`.
- Call site `TASK/StabilizerTask.c` `Update_Data()` (~line 440): every 5 ms tick `EkfOf_Predict(&s_ekf_of, 0.005f)`,
  then `EkfOf_Update(ofx, ofy)` when `of_ok`; `ofx = (of2_dx_fix - s_of_bias_x) * 0.01f` (m/s). Health check on
  innovations -> fallback to mode 0 (keep it). `Of_RebaseKfBias` (~line 197) shifts `x[2], x[5]` (keep it).
  `pos_integrate` (~line 462) is the on-ground gate (0 when disarmed, GROUND_IDLE or LANDED, unless handheld test).
- Accel: `API/imu_update.c:202` `Lin_Acc_X_body / Lin_Acc_Y_body` = gravity-removed body accel in mg, updated at the
  control rate (accel source is a 30 Hz Butterworth LPF, `API/bmi088_driver.c:447`).
- Axis map, measured on all five logs below: correlation between 0.5 s integrated accel and the change of debiased OF
  velocity is OF x <-> +Lin_Acc_X_body (r +0.27..+0.46) and OF y <-> -Lin_Acc_Y_body (r -0.25..-0.50). The other
  pairs are |r| <= 0.23. The accel-integrated velocity has ~12 cm/s rms per 0.5 s versus ~5 cm/s of OF change, so the
  accel is noisy: tune its noise from data, do not assume it is clean. active6 hover mean Lin_Acc_X is -29 mg.
- Logs (`logs/vofa/f17_hover_{shadow3,shadow4,shadow5,active5,active6}_*.meta.json`), loader
  `ground_station.analysis.flightlab.loaders.vofa.load_vofa(meta)` -> `L.signals[name].t/.v`:
  slot1 50 Hz `Acc_X_Real, Acc_Y_Real, flight_phase, ano_of.of_alt_cm`; slot2 50 Hz `Ctrler.rollPID.FB,
  Ctrler.pitchPID.FB` (deg, = IMU attitude); slot3 25 Hz `ano_of.of2_dx_fix, of2_dy_fix, of_quality, s_of_bias_x/y,
  s_ekf_of.x[0..5]`. Rebuild Lin_Acc: `lin_x = Acc_X + 1000 sin(pit)`, `lin_y = Acc_Y - 1000 sin(rol) cos(pit)`.
  active6 `slot0.csv` is header-only and `load_vofa` raises on it: in the replay, write a temp meta with slots 1-3
  only (do not change the loader in this WP). Mode 2 was on in shadow3 and active5 only (old EKF states logged).

CEO decisions (the model):
- 8 states, telemetry-compatible: `x[0..5]` keep their meaning, new `x[6] = ba_x`, `x[7] = ba_y` (m/s^2). The axes are
  independent: store P as two 4x4 blocks (`float P[2][16]`) and map each axis with an index table
  `{0,1,2,6}, {3,4,5,7}`; ONE per-axis predict and ONE per-axis scalar update function, called for both axes.
- Predict, per axis, input `a` (m/s^2, OF frame), `u = a - ba`:
  `p += v dt + 0.5 u dt^2; v += u dt; bof, ba` random walks. `F = [[1,dt,0,-dt^2/2],[0,1,0,-dt],[0,0,1,0],[0,0,0,1]]`,
  `Q = diag(q_pos, q_acc, q_bof, q_ba) * dt` (q_acc = accel white-noise density driving v).
- API: `EkfOf_Predict(e, dt, ax, ay)`; the filter never reads globals. Accel mapping lives in StabilizerTask as named
  macros (`EKF_OF_ACC_SIGN_X +1`, `EKF_OF_ACC_SIGN_Y -1`, mg -> m/s^2 `9.80665e-3f`) with a comment citing the r values.
- Zero-velocity update: `EkfOf_UpdateZeroVel(e)`, measurement `v = 0` with `R_zupt` (default 1e-4), both axes,
  called every tick while on the ground (same ground condition as `pos_integrate == 0`, not in handheld test).
  OF updates keep running on the ground when `of_ok`. On the ground the filter thus learns `bof` like FIXED mode.
- Keep Joseph form, innovation health fallback, `Of_RebaseKfBias`, `EkfOf_ResetPos`, the 200 Hz tick.
- Readability: follow the repo's firmware table pattern (`docs/firmware-table-pattern.md`) for the noise defaults;
  update the model comments in `ekf_of.h`, the top of `ekf_of.c`, and the mode-2 comment in StabilizerTask (~85-104).

Wanted:
1. Firmware as above. Host check: `gcc -std=c99 -Wall -Wextra -Werror -c API/ekf_of.c` (stub include dir if needed).
2. `ground_station/analysis/ekf_of_model.py`: Python twin, same equations and parameter names, plus a C-vs-Python
   golden test: compile `API/ekf_of.c` with gcc into a shared lib (skip the test if gcc is missing) and compare both on
   a synthetic run to 1e-4 relative. Synthetic truth: 5 s ground (ZUPT, OF bias 0.05 m/s, accel bias 0.2 m/s^2), then
   30 s flight with 0.7 Hz sinusoidal accel. Assert `bof` within 0.005 of truth after the ground phase, `ba` within 0.02
   of truth by 10 s, velocity rms error < 0.02 m/s. Tests in `ground_station/analysis/tests/test_ekf_of_model.py`.
3. `ground_station/analysis/ekf_of_replay.py` (CLI, `python -m ...`): replays the five logs (predict on 50 Hz accel
   samples, OF update at 25 Hz, ZUPT while flight_phase is 0 or 3) for the old 6-state model, the new model and the
   FIXED integration (debiased OF integrated), then picks `q_acc, q_bof, q_ba, R_of` by a small grid search.
   Criteria: hover NIS mean 0.5-2, innovation lag-1 autocorrelation < 0.3, in-flight `bof` moves < 1 cm/s from its
   takeoff value, `ba` settles within 10 s. Print one table per log: old/new/FIXED position difference rms (cm),
   final `ba_x, ba_y` (mg), NIS mean, lag-1 autocorr. Put the chosen numbers into the firmware defaults.
4. `ground_station/comm/boot_default_layout.py`: add `"s_ekf_of.x[6]", "s_ekf_of.x[7]"` after line 334.
5. Report `docs/agent/reports/WP-8.md`: the tables, chosen numbers and why, the ba estimates (they measure the
   accel level offset), and anything that contradicts the facts above.

Allow-list: `API/ekf_of.c`, `API/ekf_of.h`, `TASK/StabilizerTask.c` (mode-2 block, ZUPT call, accel macros only),
`ground_station/comm/boot_default_layout.py`, new `ground_station/analysis/ekf_of_model.py`,
`ground_station/analysis/ekf_of_replay.py`, `ground_station/analysis/tests/test_ekf_of_model.py`,
`docs/agent/reports/WP-8.md`. Never touch `OBJ/`, run Keil, flash, or call 8081.

Acceptance (the CEO re-runs these):
- `PYTHONPATH=. python -m pytest ground_station/analysis/tests/test_ekf_of_model.py ground_station/comm -q` green.
- `PYTHONPATH=. python -m ground_station.analysis.ekf_of_replay` prints the five tables with the criteria met or
  a stated reason per log.
- `gcc -std=c99 -Wall -Wextra -Werror -c API/ekf_of.c` clean; `git diff --stat main` lists only allow-listed paths.
