# WP-10 fix ranking: calibrated lateral cascade sim (2026-10-02, CTE)

All sim numbers below come from one run of
`python -m ground_station.research.sim.cascade_rank --logs <FreeRTOS-adaptive-controller-codex>/logs/vofa`
on the laptop (78 s wall); the full output is `.agent-ops/out/wp10-cte-rank.txt`. Log numbers come from the same
run (hover windows of the f17 slot CSVs). WP-9 numbers are labelled WP-9; anything else is labelled PROPOSED.

## Bottom line

For PID only, **F1x + F3 + F5w + F6a** is in the top 4 of all three scenarios and is the only PID set that survives a load step:

| scenario | F0 (3ae4a23) PID | F1x+F3+F5w+F6a PID | same with MRAC |
|---|---|---|---|
| S1 hover: steady / rms / max (cm) | 4.7 / 5.6 / 12.9 | 1.2 / 3.8 / 9.4 | 1.2 / 3.7 / 8.9 |
| S2 load step, worst variant: steady / rms / max | 36.3 / 37.0 / 44.8 | 2.2 / 5.0 / 14.0 | 2.2 / 4.4 / 8.6 |
| S3 square + circle, worst: rms / max / overshoot | 30.9 / 41.1 / 8.2 | 4.3 / 11.1 / 6.9 | 4.0 / 9.6 / 6.8 |

Trajectory tracking is fixed by the trajectory feed-forward (S3 rms about 30 cm without it, whatever the rows), and load
steps are fixed by opening the integral separation (EMin). The WP-9 sizing F1w does not survive a load step for PID (S2 steady 31.5 cm),
because EMin 2 deg/s on the gyro and 3 deg on the angle freezes both integrators while the error is large.

## Model (ground_station/research/sim/cascade.py)

- Firmware rates: 5 ms tick (200 Hz; `CalTrim_Init(.., 2000U) /* 10 s @ 200 Hz */`, StabilizerTask.c ~327). Position and
  velocity loops run every 2nd tick (`cnt_loc >= 2`, ~926), only while armed. The angle and rate loops run every tick (~1055, ~1088).
- Chain per axis: locxPID (cm) -> clamp +-120 (~1346) -> locxsPID (cm/s) -> `accel_to_lean_angles` = clamp(fast_atan(U/g), +-15 deg)
  (~1437) -> rollPID (deg) -> gyroxPID (deg/s) -> torque ticks -> mixer (~1109, ~1125-1143).
- Signs and frames (yaw 0): **x <-> roll**: des_roll = -locxsPID.U, `accel_to_lean_angles(des_pitch, -des_roll)`, so
  rollPID.Des = +atan(locxsPID.U/g); rollPID.FB = imu_rol (~672); mixer u_gyrox = +gyroxPID.U. **y <-> pitch**: des_pitch =
  -locysPID.U, so pitchPID.Des = -atan(locysPID.U/g); pitchPID.FB = -imu_pit; mixer u_gyroy = -gyroyPID.U. Position FB:
  locxPID.FB = earth_x_ture, locyPID.FB = earth_y_ture (~490, ~514); velocity FB locxsPID.FB = fb_dy, locysPID.FB = -fb_dx (~587).
  ComputePID is odd in (Des, FB, state), so the sim runs pitch as roll on y' = -y and flips y back for reporting.
- PID: a batched float32 twin of `ComputePID` AW_LEGACY (`cascade.Pid`). It is golden-tested against the real API/pid.c, compiled as a host
  executable (`_ccore/build.py`, `_ccore/pid_driver.c`) on 8 rows x 600 steps to rtol 1e-6 / atol 1e-5. **The test ran and passed on
  this laptop**. The manager reported WinError 193 for the old ctypes DLL; an exe avoids the 32/64-bit mismatch.
- Plant: torque -> delay -> first-order motor lag -> rate = k * (torque - bias) -> angle; lateral accel = g tan(angle - lean) + slow
  push (Ornstein-Uhlenbeck, tau 2 s); OF velocity = true velocity delayed + white noise; position FB = integral of the OF velocity.
- Rate limit cycle: the logs show a 24.9 Hz gyro limit cycle (13.3 dps rms roll, 6.7 pitch, >5 Hz). The sim does not reproduce it
  dynamically, so it is injected as a 25 Hz dither on the gyro feedback with those rms values. Without the dither the sim charged
  gyro Ui to its cap of 10; the logs show it near 0 (below).
- MRAC: not mrac.c. It is a first-order offload, du_ad/dt = gyroPID.U / tau, with tau fitted to the active15 u_ad rise.

## Calibration fit (sim = mean of 3 noise replicates, 60 s; log = hover window)

Plant: SysID constants (constants.py, docs/sysid_results.md): roll k 8.08 deg/s^2/tick, tau_m 51 ms, delay 15 ms; pitch 9.06,
61 ms, 12 ms. A closed-loop least-squares fit on shadow14 gave k 18.2 / 9.8 (r2 0.58 / 0.37). It is biased by feedback and was not used;
in a trial run, k x2 made the sim's rate loop oscillate (shadow14 roll Des-FB 3.0 deg vs log 1.85). Outer grid (120 points on shadow14 + shadow4): OF delay 0 ms, OF noise 0.8 cm/s per 100 Hz
sample, push 10 cm/s^2 rms. The delay and noise sit on grid edges, so they are not identifiable from these targets. MRAC tau fit:
**0.35 s** (grid 0.05-3 s).

| flight / axis | Des-FB deg | angle U | gyro U | gyro Ui (=U-5u) | u_ad | pos e cm | pos rms | sway Hz | sway amp deg | lag ms |
|---|---|---|---|---|---|---|---|---|---|---|
| shadow14 roll (F0) sim / log | 1.82 / 1.85 | 7.88 / 7.95 | 39.27 / 39.22 | -0.11 / -0.55 | 0 / 0 | 4.35 / 3.34 | 3.32 / 4.29 | 0.23 / 0.40 | 0.50 / 0.49 | 333 / 352 |
| shadow14 pitch | 0.18 / 0.22 | 2.74 / 2.71 | 13.62 / 13.62 | -0.06 / 0.06 | 0 / 0 | -3.02 / -3.64 | 3.07 / 3.29 | 0.27 / 0.20 | 0.47 / 0.55 | 298 / 298 |
| shadow4 roll (angle Ki 0.1) | 0.02 / 0.05 | 7.14 / 6.27 | 35.62 / 35.58 | -0.09 / 4.25 | 0 / 0 | -6.36 / -7.90 | 3.22 / 2.99 | 0.80 / 0.70 | 0.70 / 0.86 | 267 / 255 |
| shadow4 pitch | 0.00 / -0.01 | 1.54 / 1.44 | 7.51 / 7.50 | -0.18 / 0.31 | 0 / 0 | -4.49 / -4.44 | 3.05 / 3.00 | 0.85 / 0.75 | 0.68 / 0.95 | 240 / 279 |
| active15 roll (MRAC) | 0.00 / -0.07 | 0.03 / -0.04 | 0.04 / 0.29 | -0.10 / 0.47 | 32.70 / 32.41 | -6.92 / -8.42 | 3.18 / 4.01 | 0.45 / 0.45 | 0.57 / 0.71 | 347 / 360 |
| active15 pitch | 0.00 / -0.01 | -0.01 / 0.00 | 0.00 / 1.06 | 0.07 / 1.04 | 15.91 / 14.85 | -2.53 / -2.92 | 2.66 / 2.55 | 0.27 / 0.25 | 0.46 / 0.43 | 321 / 350 |

Pitch is in the pitchPID frame and position in y' = -y (log-frame Y error = minus the value shown). Torque need (mixer, ticks/motor):
shadow14 39.2 / 13.6, shadow4 35.6 / 7.5, active15 32.7 / 15.9. The steady quantities match to about 0.1 deg and 1.5 cm. Old angle Ki
(F2) gives the sway: sim 0.80-0.85 Hz vs log 0.70-0.75 Hz at similar amplitude, which validates the sim. The active15 u_ad is not
first-order: at injection it jumps to 162 ticks for about 0.1 s, peaks near 97 at 0.5 s, then settles to about 32 within 2-3 s
(50 Hz slot2, read this session).

## Ranking (from the run; columns = worst over the scenario variants, nominal plant)

Robust = worst rms over the nominal plant and +-30% on k and on all lags. All 162 rows (27 candidates x PID/MRAC x 3 scenarios)
stayed stable (error < 300 cm, finite). Settle times (S2 24.8-29 s) are
dominated by the noise floor and do not discriminate.

**S1 hover 60 s** (lean = WP-9 trim -1.24 / -0.90 deg, shadow14 torque need):

| # | candidate | ctrl | steady cm | rms | max | peak lean deg | osc cm @ Hz | robust rms |
|---|---|---|---|---|---|---|---|---|
| 1 | F1w+F5w | PID | 0.5 | 3.5 | 8.5 | 3.8 | 1.8 @ 0.50 | 3.5 |
| 2-4 | F1x+F3+F5w (+F6, +F6a) | PID | 1.2 | 3.8 | 9.4 | 3.9 | 1.9 @ 0.50 | 3.8 |
| 10 | F1x+F3+F5w | MRAC | 1.2 | 3.7 | 8.9 | 3.8 | 1.8 @ 0.50 | 3.9 |
| 16 | F5w | MRAC | 0.5 | 3.5 | 8.4 | 3.7 | 1.7 @ 0.50 | 4.3 |
| 17 | F3 | PID | 1.2 | 4.4 | 10.2 | 3.7 | 1.6 @ 0.20 | 4.5 |
| 34-38 | F0 = F1b = F1c = F6 = F6a | PID | 4.7 | 5.6 | 12.9 | 3.7 | 1.5 @ 0.20 | 5.6 |
| 39 | F2 (angle Ki 0.1) | PID | 6.7 | 8.7 | 17.3 | 4.4 | 0.8 @ 0.85 | 8.7 |
| 40-42 | F1w = F1a = F1 | PID | 7.1 | 8.9 | 18.3 | 3.9 | 1.6 @ 0.50 | 8.9 |
| 46 | F0 | MRAC | 7.0 | 8.9 | 18.2 | 3.8 | 1.5 @ 0.50 | 10.4 |
| 54 | F5w | PID | 11.6 | 10.9 | 18.1 | 3.7 | 1.4 @ 0.20 | 10.9 |

The lean (B) and the inner-loop shortfall (A) partly cancel on roll under F0, so fixing only one of them makes hover worse. F1w or
MRAC alone (A removed) gives 7 cm; F5w alone (B removed) gives 11.6 cm. F1b/F1c change nothing, because gyro UiMax 20 and EMin 2 bind first.

**S2 asymmetric load** (torque bias x2, x3, x-1 on roll, x3 on pitch, stepping in at 10 s; worst variant shown):

| # | candidate | ctrl | steady cm | rms | max | robust rms |
|---|---|---|---|---|---|---|
| 1-3 | F1x+F3+F5w (+F6, +F6a) | MRAC | 2.2 | 4.4 | 8.6 | 4.6 |
| 4-6 | F1x+F3+F5w (+F6, +F6a) | PID | 2.2 | 5.0 | 14.0 | 5.0 |
| 7 | F3+F5w | MRAC | 2.2 | 4.6 | 9.7 | 5.5 |
| 21 | F1x | PID | 11.3 | 10.7 | 17.0 | 10.8 |
| 24 | F0 | MRAC | 11.3 | 10.3 | 16.7 | 13.1 |
| 32 | F2 | PID | 18.7 | 21.5 | 28.7 | 24.6 |
| 33 | F1w | PID | 24.1 | 25.3 | 41.8 | 25.6 |
| 40 | F1w+F3+F5w | PID | 31.5 | 32.3 | 40.0 | 32.7 |
| 49 | F0 | PID | 36.3 | 37.0 | 44.8 | 37.4 |
| 54 | F5w | PID | 45.1 | 45.8 | 53.6 | 46.2 |

**S3 dense waypoints** (1 m square at 0.2 m/s and r 0.5 m circle at 0.3 m/s, a point every 0.1 m, interpolated in time like
API/wfb_traj.c; worst of the two):

| # | candidate | ctrl | rms cm | max | overshoot | peak lean deg | robust rms |
|---|---|---|---|---|---|---|---|
| 1 | F1x+F3+F5w+F6a | MRAC | 4.0 | 9.6 | 6.8 | 7.0 | 4.3 |
| 2 | F1x+F3+F5w+F6a | PID | 4.3 | 11.1 | 6.9 | 6.5 | 4.4 |
| 4 | F1w+F3+F5w+F6a | PID | 5.3 | 12.9 | 7.8 | 6.4 | 5.5 |
| 5 | F6a | PID | 6.3 | 14.5 | 8.7 | 7.0 | 6.4 |
| 6 | F1x+F3+F5w+F6 | PID | 6.9 | 13.5 | 11.9 | 5.5 | 6.9 |
| 11 | F6 | PID | 8.3 | 16.7 | 10.7 | 5.1 | 8.3 |
| 26 | F0 | PID | 30.9 | 41.1 | 8.2 | 3.8 | 30.9 |
| 30 | F1x+F3+F5w | PID | 31.2 | 35.3 | 3.8 | 4.7 | 31.2 |
| 42 | F0 | MRAC | 34.4 | 46.7 | 13.9 | 4.7 | 34.9 |

Without feed-forward, a P position loop (Kp 0.8) trails a 20-30 cm/s setpoint by v/Kp = 25-38 cm. Accel FF (F6a) roughly halves the
end overshoot of velocity FF alone (6.9 vs 11.9 cm).

## Recommended for WP-13 (PROPOSED rows, `API/pid.c` column order Kp Ki Kd UMax UpMax UiMax UdMax SumEMax EMin)

| member | row | from |
|---|---|---|
| pitchPID, rollPID | `3.0, 0.02, 8, 200, 200, 26, 10, 1300, 10` | F1x (WP-9 Ui 25.4 sizing, EMin 3 -> 10) |
| gyroxPID, gyroyPID | `5, 0.01, 10, 300, 300, 160, 100, 16000, 50` | F1x (WP-9 Ui 157 sizing, EMin 2 -> 50) |
| locxPID, locyPID | `0.8, 0.0013, 4.0, 300, 300, 5, 50, 3850, 10` | F3 (C6) |
| locxsPID, locysPID | `3.0, 0.008, 6.0, 600, 600, 100, 100, 12500, 10` | F3 (C6) |

- Attitude trim feed-forward (F5w): add -1.24 deg to rollPID.Des and -0.90 deg to pitchPID.Des after `accel_to_lean_angles`
  (pitchPID frame = -imu_pit). Only together with F1x/F3; alone it is the worst hover row (S1 #54).
- Trajectory feed-forward (F6a): locxsPID.Des = clamp(locxPID.U, +-120) + segment velocity of the wfb_traj interpolation (per axis,
  firmware frame), and locxsPID.U += d/dt of that velocity low-passed with tau 0.2 s.
- The EMin 50 / UiMax 160 gyro rows need an "integrate only while FLYING" gate, holding SumE at 0 like gyrozPID at ~1081. The sim is
  always airborne and cannot test ground windup. WP-13 must add the gate.
- Not backed by the run, so not recommended: F4 (velocity Ki 0.005; S1 rms 4.5 vs F3 4.4), F1b/F1c (no effect), F2 (sway), F1w for
  PID (fails S2).

## What a fair PID-vs-MRAC demo needs

- Same outer rows (F3), same trim and trajectory FF on both arms. With F0 rows, MRAC wins S2 only because the PID integrators are capped and
  separated (S2 rms 10.3 vs 37.0). With F1x the PID is close (S2 5.0 vs 4.4, S3 4.3 vs 4.0), so the demo then measures adaptation
  rather than integrator caps.
- The discriminating test is the load step (S2): the max deviation after the step is the clearest gap (PID 14.0 vs MRAC 8.6 cm). Hover (S1)
  does not separate them (3.8 vs 3.7 cm rms).
- Fly the real mrac.c: the sim's MRAC is a fitted surrogate, and the logged u_ad overshoots (spike 162, peak ~97, final ~32 ticks).
  Report the PID rows and the MRAC flags with each flight.

## Limitations

The inner plant comes from SysID, not this fit. The 25 Hz limit cycle is injected, not simulated. shadow4 roll gyro Ui was partly
charged in flight (log 4.25) and the sim gives about 0. OF delay and noise are not identifiable here (grid-edge fit). There is no
yaw or cross-axis coupling, no cos(other angle) term in `accel_to_lean_angles`, and no ground or takeoff phase. Positions are true
positions; the OF-integrated FB drifts by a noise random walk. Neighbouring rows often differ by less than 0.3 cm in the rank key,
which is within the noise. Read the groups, not the exact order.
