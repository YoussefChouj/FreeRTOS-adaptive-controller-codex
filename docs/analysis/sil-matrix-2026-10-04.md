# SIL scenario matrix, 2026-10-04 (WP-31)

`python -m sim.sil.run --matrix --seed 0`: 432 runs, wall 332 s. Generated; do not edit.
**Validation: UNVALIDATED.** No usable session under `logs/sessions` or `logs/campaigns` in this tree (they are local to the lab checkout); `python -m sim.sil.run --matrix` there replays them.

Firmware in the loop: `API/pid.c`, `API/mrac.c`, `API/mrac_math.c`, `API/controller.c` (V2 deficit included), built by `sim/sil/build.py` (32-bit MinGW: an executable on pipes, not ctypes). `Compute_Motor`/`Update_Des`/mixer of `TASK/StabilizerTask.c` (not host-buildable) are ported line-cited in `sim/sil/csrc/sil_server.c`. Plant: `sim/bench/plant.py` + per-axis rate gains of `ground_station/research/sim/constants.py` (`sim/sil/plant.py`). Every disturbance value is PROPOSED (`sim/sil/scenarios.py`); scored after an 8 s hover warmup.

Cells: position RMSE cm (navigation frame) + abort flags (roadmap :189-190): X crash, U 1 s-RMS |u_ad| > 0.5 |u_nom| (axes p r y z), T tilt > 12 deg, C a motor at 4000 > 0.5 s, S simplex trip, P error > 0.5 m.

## Controllers

| name | what | host us/tick |
|---|---|---|
| pid | firmware PID rows (API/pid.c), MRAC in shadow (injection off, firmware default) | 7.3 |
| pid_at | PID rows from ground_station/autotune on the SIL FRF (CMD 0x01) | 6.7 |
| mrac | MRAC baseline: MRAC_Init rows, injection on | 6.7 |
| v1 x.25 | V1 refmodel, mrac_v1.yaml preset v1_refmodel (gamma x0.25) | 6.9 |
| v1 x1 | V1 refmodel, mrac_v1.yaml preset v1_refmodel_g1 (gamma x1) | 6.8 |
| v2 | V2 sataware, mrac_v2.yaml preset v2_sataware (mu_sat 0.85) | 6.8 |
| pr | PR: V1 x0.25 + kappa_pr 0.5, crm_ell 10 on pitch/roll (PROPOSED) | 7.0 |
| 3l | 3L layer 1: V1 x0.25 + lam_ang 4 on pitch/roll (PROPOSED) | 6.8 |
| v3 | V3 RBF12 build (-DMRAC_VARIANT=1), mrac_v3.yaml preset v3_rbf12 (gamma x0.25) | 12.8 |

## Nominal (roadmap H, D, F)

| ctrl | H rmse | D rmse | D overshoot cm | F rmse | max tilt | clamp s | u_ad/u_nom | Theta rise s | aborts |
|---|---|---|---|---|---|---|---|---|---|
| pid | 4.9 | 7.2 | 11.0 | 12.8 | 14.5 | 9.55 | 0.00  | 5 | tilt12 |
| pid_at | 5.9 | 8.1 | 12.1 | 27.2 | 16.6 | 0.00 | 0.00  | 8 | pos05,tilt12 |
| mrac | 4.7 | 6.5 | 8.9 | 11.4 | 14.9 | 11.74 | 1.10 z | 4 | tilt12,uad |
| v1 x.25 | 4.9 | 7.6 | 11.6 | 14.7 | 15.1 | 11.53 | 1.13 z | 6 | tilt12,uad |
| v1 x1 | 5.4 | 8.7 | 12.4 | 14.4 | 14.6 | 12.13 | 1.14 z | 7 | tilt12,uad |
| v2 | 5.3 | 8.7 | 12.3 | 14.3 | 14.5 | 12.19 | 1.09 z | 13 | tilt12,uad |
| pr | 5.1 | 7.7 | 11.3 | 16.1 | 15.2 | 11.37 | 1.15 z | 9 | tilt12,uad |
| 3l | 5.6 | 8.3 | 13.6 | 16.3 | 15.6 | 11.48 | 1.11 z | 6 | pos05,tilt12,uad |
| v3 | 4.9 | 7.5 | 11.8 | 14.8 | 15.0 | 11.52 | 1.17 z | 6 | tilt12,uad |

## Single disturbances on the hover

| scenario | pid | pid_at | mrac | v1 x.25 | v1 x1 | v2 | pr | 3l | v3 |
|---|---|---|---|---|---|---|---|---|---|
| wind_step | 5.9 | 12.6 | 6.0 U(yz) | 5.9 U(yz) | 7.3 U(yz) | 7.3 U(yz) | 6.0 U(yz) | 7.8 U(yz) | 5.9 U(yz) |
| wind_gust | 7.1 | 7.6 | 6.8 U(yz) | 7.0 U(yz) | 7.3 U(yz) | 7.3 U(yz) | 6.9 U(yz) | 7.5 U(yz) | 6.9 U(yz) |
| ground_effect | 5.0 | 5.9 | 4.4 U(yz) | 4.9 U(yz) | 5.3 U(yz) | 5.4 U(yz) | 5.0 U(yz) | 5.4 U(yz) | 4.9 U(yz) |
| cog | 4.1 | 6.0 | 6.2 U(pyz) | 4.6 U(yz) | 7.0 U(yz) | 6.8 U(yz) | 4.3 U(yz) | 5.3 U(yz) | 4.6 U(yz) |
| payload100 | 4.8 | 6.7 | 4.8 U(yz) | 4.4 U(yz) | 5.9 U(yz) | 5.8 U(yz) | 4.3 U(yz) | 4.9 U(yz) | 4.4 U(yz) |
| pendulum | 4.7 | 5.7 | 5.1 U(yz) | 4.9 U(yz) | 5.5 U(yz) | 5.6 U(yz) | 4.7 U(yz) | 5.2 U(yz) | 4.9 U(yz) |
| mass_p15 | 15.4 | 14.7 | 4.8 U(yz) | 4.5 U(yz) | 5.3 U(yz) | 5.3 U(yz) | 4.6 U(yz) | 5.1 U(yz) | 4.4 U(yz) |
| mass_m15 | 4.5 | 6.2 | 5.3 U(yz) | 5.4 U(yz) | 5.8 U(yz) | 5.9 U(yz) | 5.0 U(yz) | 5.4 U(yz) | 5.0 U(yz) |
| motor80 | 5.1 | 7.5 | 5.3 U(yz) | 5.2 U(yz) | 6.1 U(yz) | 6.1 U(yz) | 5.3 U(yz) | 6.1 U(yz) | 5.1 U(yz) |
| delay5 | 4.6 | 5.9 | 4.8 U(yz) | 7.9 U(ryz)T | 4.6 U(yz) | 6.3 U(yz) | 5.0 U(yz) | 5.0 U(yz) | 7.2 U(yz) |
| delay10 | 5.3 | 5.8 | 4.9 U(yz) | 5.6 U(yz) | 5.0 U(yz) | 5.0 U(yz) | 5.5 U(yz) | 5.1 U(yz) | 5.6 U(yz) |
| battery | 16.3 | 16.3 | 5.0 U(yz) | 5.1 U(yz) | 5.5 U(yz) | 5.5 U(yz) | 5.2 U(yz) | 5.4 U(yz) | 5.1 U(yz) |
| noise2 | 8.3 | 9.2 | 8.3 U(yz) | 8.7 U(yz) | 8.1 U(yz) | 8.2 U(yz) | 8.0 U(yz) | 8.0 U(yz) | 8.8 U(yz) |

## Pairs on the figure-8

| scenario | pid | pid_at | mrac | v1 x.25 | v1 x1 | v2 | pr | 3l | v3 |
|---|---|---|---|---|---|---|---|---|---|
| wind_gust+cog | 12.6 T | 23.4 TP | 11.8 U(yz)T | 14.3 U(yz)T | 13.8 U(yz)T | 14.2 U(yz)T | 14.8 U(yz)T | 16.3 U(yz)T | 14.1 U(yz)T |
| wind_gust+pendulum | 13.4 T | 25.6 TP | 11.8 U(yz)T | 15.6 U(yz)T | 16.0 U(yz)T | 15.6 U(yz)T | 15.4 U(yz)T | 16.2 U(yz)T | 15.3 U(yz)T |
| wind_gust+motor80 | 13.1 T | 26.4 TP | 11.9 U(yz)T | 15.3 U(yz)T | 15.3 U(yz)T | 15.4 U(yz)T | 16.0 U(yz)T | 17.6 U(yz)T | 15.4 U(yz)T |
| wind_gust+delay10 | 15.7 T | 23.5 TP | 14.9 U(yz)T | 14.8 U(yz)T | 14.7 U(yz)T | 14.7 U(yz)T | 16.4 U(yz)T | 14.7 U(yz)T | 14.8 U(yz)T |
| cog+pendulum | 13.5 T | 26.4 TP | 9.9 U(pyz)T | 15.7 U(yz)T | 12.8 U(yz)T | 12.9 U(yz)T | 17.5 U(yz)T | 16.6 U(yz)T | 15.8 U(yz)T |
| cog+motor80 | 13.7 T | 27.7 TP | 12.9 U(pyz)T | 16.6 U(yz)T | 15.7 U(yz)T | 15.5 U(yz)T | 16.8 U(yz)T | 17.7 U(yz)T | 16.7 U(yz)T |
| cog+delay10 | 24.3 TP | 28.1 TP | 12.4 U(yz)T | 12.1 U(yz)T | 15.8 U(yz)T | 13.4 U(yz)T | 17.3 U(yz)T | 12.3 U(yz)T | 12.2 U(yz)T |
| pendulum+motor80 | 16.3 T | 28.1 TP | 11.5 U(yz)T | 18.1 U(yz)T | 15.8 U(yz)T | 15.7 U(yz)T | 18.5 U(yz)T | 19.8 U(yz)TP | 17.9 U(yz)T |
| pendulum+delay10 | 15.5 T | 27.0 TP | 14.9 U(yz)T | 15.5 U(yz)T | 16.4 U(yz)T | 16.4 U(yz)T | 18.0 U(yz)T | 15.9 U(yz)T | 15.4 U(yz)T |
| motor80+delay10 | 18.5 TP | 27.9 TP | 15.1 U(yz)T | 16.6 U(yz)TP | 15.8 U(yz)T | 15.5 U(yz)T | 16.9 U(yz)TP | 17.0 U(yz)TP | 16.8 U(yz)TP |

## Worst plausible stack on the figure-8

| scenario | pid | pid_at | mrac | v1 x.25 | v1 x1 | v2 | pr | 3l | v3 |
|---|---|---|---|---|---|---|---|---|---|
| wind_gust+cog+pendulum+motor80+delay10+noise2 | 33.8 TP | 23.1 TP | 19.3 U(yz)T | 16.4 U(yz)T | 19.6 U(yz)T | 15.3 U(yz)T | 19.2 U(yz)T | 18.1 U(pyz)T | 15.4 U(yz)T |

## Altitude under thrust changes (z RMSE cm)

| scenario | pid | pid_at | mrac | v1 x.25 | v1 x1 | v2 | pr | 3l | v3 |
|---|---|---|---|---|---|---|---|---|---|
| hover | 0.5 | 0.5 | 0.6 | 0.6 | 0.6 | 0.6 | 0.6 | 0.6 | 0.6 |
| mass_p15 | 14.8 | 13.5 | 1.5 | 1.5 | 1.5 | 1.5 | 1.5 | 1.5 | 1.5 |
| mass_m15 | 0.7 | 0.6 | 0.7 | 0.7 | 0.7 | 0.7 | 0.7 | 0.7 | 0.7 |
| battery | 15.4 | 15.0 | 0.6 | 0.6 | 0.6 | 0.6 | 0.6 | 0.6 | 0.6 |
| motor80 | 1.3 | 1.5 | 1.1 | 1.2 | 1.1 | 1.1 | 1.2 | 1.2 | 1.2 |

## Worst case per controller (all scenarios)

| ctrl | max tilt | max clamp s | max u_ad/u_nom | max Theta rise s | runs with aborts | crashes |
|---|---|---|---|---|---|---|
| pid | 19.3 | 24.00 | 0.00 | 23 | 12/27 | 0 |
| pid_at | 16.9 | 1.40 | 0.00 | 34 | 12/27 | 0 |
| mrac | 19.4 | 24.20 | 1.50 | 18 | 27/27 | 0 |
| v1 x.25 | 19.2 | 24.03 | 1.49 | 21 | 27/27 | 0 |
| v1 x1 | 23.4 | 24.16 | 1.50 | 21 | 27/27 | 0 |
| v2 | 20.1 | 24.23 | 1.50 | 21 | 27/27 | 0 |
| pr | 21.6 | 24.03 | 1.49 | 21 | 27/27 | 0 |
| 3l | 25.4 | 24.00 | 1.58 | 23 | 27/27 | 0 |
| v3 | 19.2 | 24.02 | 1.50 | 22 | 27/27 | 0 |

## Verdicts (rules PROPOSED: `sim/sil/run.py` verdict)

| ctrl | verdict | reason |
|---|---|---|
| pid | fly with limits | 12 runs with new aborts, e.g. figure8 (tilt12), f8+wind_gust+cog (tilt12), f8+wind_gust+pendulum (tilt12), f8+wind_gust+motor80 (tilt12) |
| pid_at | do not fly | figure-8 RMSE 2.13 x pid |
| mrac | do not fly | new aborts in hover (uad:yz), doublet (uad:yz) |
| v1 x.25 | do not fly | new aborts in hover (uad:yz), doublet (uad:yz) |
| v1 x1 | do not fly | new aborts in hover (uad:yz), doublet (uad:yz) |
| v2 | do not fly | new aborts in hover (uad:yz), doublet (uad:yz) |
| pr | do not fly | new aborts in hover (uad:yz), doublet (uad:yz) |
| 3l | do not fly | new aborts in hover (uad:yz), doublet (uad:yz) |
| v3 | do not fly | new aborts in hover (uad:yz), doublet (uad:yz) |

## Same MRAC laws with Z not injected (g_ctrl_axis_mask 0x07)

| ctrl | H / D / F rmse | max u_ad/u_nom | runs with aborts | verdict | reason |
|---|---|---|---|---|---|
| mrac noZ | 4.6 / 7.0 / 9.9 | 0.86 | 27/27 | do not fly | new aborts in hover (uad:y), doublet (uad:y) |
| v1 x.25 noZ | 5.0 / 7.6 / 15.5 | 0.84 | 27/27 | do not fly | new aborts in hover (uad:y), doublet (uad:y) |
| v1 x1 noZ | 5.2 / 8.7 / 14.5 | 0.88 | 27/27 | do not fly | new aborts in hover (uad:y), doublet (uad:y) |
| v2 noZ | 5.3 / 8.7 / 14.3 | 0.88 | 27/27 | do not fly | new aborts in hover (uad:y), doublet (uad:y) |
| pr noZ | 5.1 / 7.6 / 16.1 | 0.84 | 27/27 | do not fly | new aborts in hover (uad:y), doublet (uad:y) |
| 3l noZ | 5.5 / 8.3 / 16.3 | 0.84 | 27/27 | do not fly | new aborts in hover (uad:y), doublet (uad:y) |
| v3 noZ | 4.9 / 7.5 / 14.6 | 0.88 | 27/27 | do not fly | new aborts in hover (uad:y), doublet (uad:y) |

## Findings

- F path: the trajectory_pipeline figure-8 (1.0 x 0.5 m) has a 1.5 cm tip radius; at 0.3 m/s that needs 5.8 m/s^2 (31 deg) against the 15 deg lean limit (gs_max_pitch_deg), so pid already tilts 14.5 deg (abort T). PROPOSED: fly F slower (tilt ~ v^2) or round the tips before using T on F.
- Z authority: pid z RMSE 14.8 cm at mass +15 %, 15.4 cm under battery sag (Z_ratePID Ui cap Ki x SumEMax = 0.435 x 250 = 109 < the extra hover PWM); mrac 1.5 / 0.6 cm because its z u_ad carries it.
- Abort U trips in 189 injected runs, by axes: yz 184, pyz 4, ryz 1. Yaw and z: the bias weights carry the hover yaw imbalance (plant u_imb 350-500 U, logged ~430) and the hover thrust offset that Z_ratePID.U otherwise holds, so u_ad stays above 0.5 u_nom in steady hover by design. With Z masked (table above) yaw alone still trips it. PROPOSED: define U on p/r only, or on the change of u_ad, before the injected flights.
- Rate-loop margins on the SIL (autotune round 1, firmware rows): PM roll 25.037 deg, pitch 17.44 deg (Spec 45+5). pid_at = 3 autotune rounds (+-30 % per round): lower rate Kp, higher Kd; fewer clamp seconds but slower position tracking (table above).
- Heading: the plant's gyro bias (sim/bench scen.py, 0.3 deg/s/axis) turns the true heading up to 34 deg in 40 s with no magnetometer; positions are scored in the navigation frame (sim/sil/plant.py p_nav).
- Firmware units (not changed here, firmware read-only): API/mrac.c:318 divides imu_data.pit/.rol (degrees: StabilizerTask.c:1715, wfb_safety pitch_deg) by rbf_ang_scale in rad (0.26 = 15 deg, mrac.c:905), so the V3 angle grid saturates at ~0.3 deg; simplex roll_max/pitch_max (3.14) are compared in degrees too (mrac.c:749-750).
- Plant vs sim/bench: yaw effectiveness 1.13 deg/s^2/U (constants.py) not 7.55 (gyroz Kp 8 limit-cycles at 7.55); OF measured in the body frame and rotated by the yaw estimate (plant.run feeds world velocity).
- Host us/tick is host CPU time, not STM32 cost; on target read mrac_cyc (DWT cycles).

## Replay of logged flights (F)

None found: the matrix is unvalidated against flight data.
