# WP-9 brief (CEO -> Manager), 2026-10-01 night. Hover-drift root cause from the f17 flight logs.

Base: `night/2026-10-02`. Analysis only: no firmware change, Keil build, flash, 8081 or probe.

Goal: confirm or overturn the CEO's two-cause drift model with numbers from every hovering f17 flight, and give the
numbers the firmware fix (WP-13) needs: inner-loop integrator caps, attitude trim, outer-loop integrator sizing.

Logs: on the VPS `/home/agent/data/logs/vofa/f17_*` (75 files; per flight `<name>.meta.json` plus `.slotN.csv`).
Locally they are `logs/vofa/`. Your CLI takes `--logs DIR` (default `logs/vofa`). Read the slot CSVs with pandas
(`t_src_ms` is source time in ms). `load_vofa` raises on header-only CSVs (active6 slot0, active15 slot0, pidonly7
slot3): skip empty slots. Ignore `log_test_*`, `test_loging`, `dash_*`. Flight list and firmware eras:
`docs/agent/handoffs/2026-10-01-overnight-context.md` table "Today's flights" (3ae4a23 = shadow10, active12, shadow13,
shadow14, active15; everything earlier is the old angle Ki 0.1).

Facts the CEO measured tonight (hover = `Z_posPID.FB > 0.6 x p95`, first 15% of the flight skipped):
- Mixer (`TASK/StabilizerTask.c` ~1109-1145): `u_gyrox = gyroxPID.U` (through `Controller_Update`, which adds MRAC
  u_ad in active mode), `u_gyroy = -gyroyPID.U`. M1 = T - u_y - u_x - d*u_z, M2 = T + u_y + u_x - d*u_z,
  M3 = T - u_y + u_x + d*u_z, M4 = T + u_y - u_x + d*u_z (d = g_yaw_mix_dir). So per motor:
  roll = (M2+M3-M1-M4)/4, pitch = (M2+M4-M1-M3)/4, yaw = (M3+M4-M1-M2)/4. In shadow flights this matches
  gyroxPID.U / -gyroyPID.U / -gyrozPID.U exactly. Units: PWM ticks per motor, hover throttle about 3000.
- Hover means (ticks/motor): shadow10 roll 45.0 pitch -20.2 yaw -156.9; shadow14 37.1 / -13.8 / -134.6;
  shadow4 38.8 / -5.5 / -114.9; active15 32.9 / -14.2 / -89.1 with gyroxPID.U 0.2 (MRAC carries roll).
  The big M1/M2 vs M3/M4 split is yaw trim, carried by the yaw I-term (cap 500): not a drift cause by itself.
- PID table `API/pid.c` (Kp Ki Kd UMax UpMax UiMax UdMax SumEMax EMin), Ui cap = min(UiMax, Ki x SumEMax):
  rollPID/pitchPID `3.0 0.02 8 200 200 10 10 120 3` (cap 2.4; before 3ae4a23 Ki was 0.1, cap 10),
  gyroxPID/gyroyPID `5 0.01 10 300 300 20 100 1000 2` (cap 10), locx/y `0.8 0.01 4.0 300 300 20 50 200 30` (cap 2),
  locxs/ys `3.0 0 6.0 600 600 100 100 200 10` (Ki 0). Read the anti-windup code (~153-171) before reconstructing Ui.
- Steady roll Des - FB: shadow10 +1.92 deg, shadow14 +1.81 deg (3ae4a23); shadow2/3/4/5 +0.04/+0.14/+0.07/+0.12.
  rollPID.U in shadow +6.2..+8.7, gyroxPID.U +30.7..+45.6.
- Hover attitude FB roll -0.83..-1.59, pitch -0.65..-1.21 deg in every hovering flight. Hover Acc_X -10..-21 mg,
  Acc_Y -13..-30 mg; on the ground before takeoff X -29..+28, Y -7..+11. yawPID.FB is about 0 (relative to arm).
- Drift relation from `docs/agent/reports/2026-10-01-drift-investigation.md`: e = (vFB + Uv/3 - Ui_pos)/0.8.
- pidonly7 and shadow13 did not pass the hover filter. Find out why (no takeoff, different signal, short).

The model to test (do not assume it):
- A. Roll needs about 40 ticks/motor of torque. PID-only: gyro Ui caps at 10, so the rate P term must give 30, which
  needs about 6 deg/s from the angle loop, whose Ui caps at 2.4, so the angle P term needs about 1.8 deg of error.
  Old Ki 0.1 had room (cap 10) and showed no error. The outer loops see the 1.8 deg as a push.
- B. Even with no attitude error the drone hovers at about -1.2 roll / -0.9 pitch deg (IMU), which needs a standing
  position error because velocity Ki is 0 and the position Ui cap is 2.
- C. Growth of the push during a flight (12 -> 28 cm/s^2) may be battery sag: more ticks needed for the same
  roll torque -> more capped shortfall -> more error. Test against voltage and time.

Wanted:
1. `ground_station/analysis/drift_rootcause.py` (CLI `python -m ground_station.analysis.drift_rootcause --logs DIR`)
   printing, per hovering flight:
   - T1 inner loop per axis: torque need (mixer, ticks/motor), angle and gyro U means, reconstructed Ui
     (mean U - Kp x mean e; D mean about 0, check it), Ui caps, share of hover time at the cap, Des - FB mean;
     in active flights the MRAC share (need minus PID U).
   - T2 lean and push: hover attitude FB and Des, Acc_X/Y, Lin_Acc rebuilt (`lin_x = Acc_X + 1000 sin(pit)`,
     `lin_y = Acc_Y - 1000 sin(rol) cos(pit)`), velocity-loop U, position-loop U and Ui, position error mean.
   - T3 thirds of the hover: real_voltage, roll need (ticks), roll error, inferred push; slopes vs V and vs t.
   - T4 position-error budget: predicted standing error from A and from B (use the relation above) vs measured.
   - T5 estimator: read `API/imu_update.c`, say whether the attitude filter levels on the accelerometer and with
     what gain, so whether "Lin_Acc about 0" is evidence or a tautology; explain the 1.7 deg pitch vs accel gap.
2. Tests `ground_station/analysis/tests/test_drift_rootcause.py` on synthetic data: hover segmentation, the mixer
   decomposition identity, Ui reconstruction against a Python copy of the PID update.
3. `docs/analysis/2026-10-02-drift-rootcause.md`: verdict on A, B and C with the numbers, then the numbers WP-13
   needs: angle and gyro SumEMax (or Ki) so the Ui caps cover the worst roll and pitch need with x3 headroom for the
   asymmetric load; attitude trim (deg) with its scatter across flights; the push the outer integrators must still
   absorb. Anything that contradicts the facts above goes first.
4. Report `docs/agent/reports/WP-9.md` (40 lines max): the summary tables and verdicts.

Acceptance (the CEO re-runs these):
- `PYTHONPATH=. python -m pytest ground_station/analysis/tests/test_drift_rootcause.py -q` green.
- `PYTHONPATH=. python -m ground_station.analysis.drift_rootcause --logs logs/vofa` prints T1-T5 for every hovering
  flight (at least shadow2/3/4/5/10/14, active1/5/6/8/12/15) in under 2 minutes.
- `git diff --stat night/2026-10-02` lists only allow-listed paths.

Allow: `ground_station/analysis/drift_rootcause.py`, `ground_station/analysis/tests/test_drift_rootcause.py`,
`docs/analysis/2026-10-02-drift-rootcause.md`, `docs/agent/reports/WP-9.md`, `.agent-ops/out/*`.
Forbidden: firmware sources, `OBJ/`, Keil, flash, 8081, probe, `logs/` (read only), `docs/flights/ledger.csv`.

Worker lane: `agy:gemini-3.1-pro-high,agy:gemini-3.8-flash-high`. Max worker rounds: 3. Effort: high.
