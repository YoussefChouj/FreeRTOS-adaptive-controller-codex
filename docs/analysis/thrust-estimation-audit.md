# Thrust-estimation audit (WP-34, 2026-10-04)

Scope: `API/thrust_estimators.c/.h`, its call in `TASK/StabilizerTask.c:1416-1434`, `API/tests/test_thrust_estimators.c`.
Log numbers below were measured this run with `python -m ground_station.analysis.thrust_replay --root <main checkout>`
on the 19 airborne logs that carry `g_thrust_est.*` (all VOFA recordings). Anything else is marked PROPOSED.

## What it does
| item | value |
|---|---|
| call | `ThrustEst_Update(mymotor[4], RPM_Get(0..3), Lin_Acc_Z_body*9.80665/1000, imu_data.pit, imu_data.rol)` in `Compute_Motor`, after the mixer, before `Update_Motor` clamps or zeroes the motors |
| rate | `Stabilizer_Task` 5 ms = 200 Hz (`USER/main.c:421`); the IMU inputs come from the 1 kHz IMU task (latest value, no anti-alias) |
| inputs / units | PWM in 0.5 us ticks (2000 = 1.0 ms, `BSP/pwm.c` prescaler); RPM uint16 = 60 f_cpu / DWT period per rev (`BSP/rpm.c:267`, 0 when stale); acc_z = body-z linear accel in m/s^2 (specific force minus gravity's body-z share, `API/imu_update.c:204`); pitch/roll in **degrees** (`imu_update.c:196-197`) |
| empirical[4] | per-motor bench LUT (ADR-0009 M1 / M4 curves, knots in ticks), clamped to 0 N below 2200 and 12.8/12.9 N above 4000 |
| blade_element[4] | k_T w^2, k_T = 6.80e-6 N s^2/rad^2 (calibrated so f17 hovers balance 0.9885 kg) |
| imu_total | total thrust from the accelerometer, `DRONE_MASS_KG` 0.9885 kg |
| sum_w2, mass_hat, cw_share | 1 s LPFs (alpha 0.005 at 200 Hz, tau 0.9975 s): sum w^2, k_T sum w^2 / specific force, CW-pair share |
| consumers | telemetry only: `g_thrust_est` is referenced nowhere else in the firmware; a bad value cannot reach control |
| init | `ThrustEst_Init` is never called; the static `{0}` initialiser covers it |

## Physics check
The accelerometer's body-z specific force is f_z = acc_z + g cos(pitch) cos(roll), and f_z = T / m for thrust T
along body z (all four props). So T = m f_z and m = k_T sum w^2 / f_z. The code had T = m (acc_z / cos_tilt + g)
= T / cos_tilt and m = k_T sum w^2 / (g + acc_z) = m cos_tilt in a tilted hover. Unit checks pass: ticks vs LUT
knots, RPM -> rad/s (2 pi / 60), mg -> m/s^2, deg -> rad inside the function, k_T in N s^2/rad^2; g is 9.81 here
vs 9.80665 at the call site (0.03 %, left as is).

## Bugs fixed (host test `API/tests/test_thrust_estimators.c` test 7, run by `ground_station/analysis/tests/test_thrust_estimators_host.py`)
| # | bug | fix | proof |
|---|---|---|---|
| 1 | `imu_total = m (acc_z / cos_tilt + g)` = T / cos_tilt | `m (acc_z + g cos_tilt)`; no division, so the 90 deg guard went away | tilted hover pitch 10 / roll 20: old 8.1 % high, now within 0.5 % |
| 2 | `mass_hat = k_T sum_w2 / (g + acc_z)` = m cos_tilt | divide by f_z = acc_z + g cos_tilt (same `> 1` guard, false for NaN) | same test: old 7.5 % low, now within 1 % |
| 3 | NaN acc_z gave `imu_total` = NaN (only the near-90 deg branch wrote 0) | non-finite f_z -> 0, the existing "invalid" value | NaN acc_z and NaN pitch -> 0, mass_hat stays finite |
Before the fix the new test failed 3 checks (39 old checks passed); after it 42/42 pass. Effect on the logged
hovers is small: median tilt 1.2-2.1 deg, so old vs new imu_total differs by 0.02-0.08 %. It matters on tilted
segments (1.5 % at 10 deg). The recomputed old form matches the logged imu_total to 0.22-0.27 N rms on the newer
logs (50 Hz sampling of a 200 Hz value).

## Open findings (not fixed here; outside the allowed files or not provable without the airframe)
| # | finding | evidence (measured) | proposed fix |
|---|---|---|---|
| 4 | `empirical[]` reports the *commanded* `mymotor`, also on ticks where `Update_Motor` writes `Set_Zero_Motors` (disarmed, idle, kill) | median sum of empirical while disarmed: 17.4-20.1 N on the 4 new-LUT logs, 51.4 N on the old ones; 100 % of disarmed samples > 1 N | call-site change in `TASK/StabilizerTask.c`: run the LUT on the applied CCR (M1..M4) after `Update_Motor`, or zero `empirical[]` when the motors are zeroed |
| 5 | the LUT has no battery-voltage term | hover PWM rises with sag: dPWM/dV median -84 ticks/V (18 logs; one 0.14 V-span log excluded); LUT thrust then drifts -0.9 to -2.3 N/V while the weight is constant (4 new-LUT logs) | PROPOSED T(pwm, V) = LUT(pwm) (V / V_bench)^2, V_bench from ADR-0009, or refit on logged hovers with vbat |
| 6 | mass and k_T are not consistent with each other or with the sim | firmware `DRONE_MASS_KG` 0.9885 kg (marked unverified) vs `research/sim/constants.py` AIRFRAME_MASS 1.2961 kg; LUT-implied hover mass 1.49-1.63 kg at 14.5-15.2 V; k_T sum w^2 / g recomputed from the raw RPM periods with today's k_T: 0.87-1.00 kg on 11 of the f17 hovers it was fitted to, 0.77-1.09 kg on 6 other flights | weigh the airframe with battery, then refit k_T on a hover with known mass and log the mass in the descriptor |
| 7 | logs from before the WP-15 LUT fix (f17_*, flight14, flight16) carry a saturated `empirical[]` (12.85 N per motor at hover) | sum = 51.4 N in all 15 of them | ignore `empirical[]` in those logs (thrust_replay does not use it for mass) |
| 8 | two logs read about 2x the blade thrust from the raw periods | f17_hover_shadow4 / shadow5: 1.95 / 2.12 kg implied | not investigated; suspect a period glitch (double edge per rev) |
| 9 | `imu_total` is unfiltered and samples the 1 kHz IMU at 200 Hz | per-sample noise in the logs; mass_hat is filtered, imu_total is not | PROPOSED: publish a 1 s LPF of imu_total like the other derived values |

Related, outside this module: MRAC reads the same `imu_data.pit/rol` as **radians** (see `log-replay-2026-10-04.md`).

## Saturation / NaN behaviour after the fix
pwm NaN or below 2200 -> 0 N, above 4000 -> top knot; rpm saturates at 65535 in `RPM_Compute`, stale -> 0; acc_z
or attitude NaN -> imu_total 0, mass_hat holds; sum_w2 cannot be NaN (uint16 input); cw_share updates only when
sum_w2 > 100. No state can latch a NaN.
