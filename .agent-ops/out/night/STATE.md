# Night run STATE (rewrite at each milestone; <=80 lines)
Updated 23:32 CST 2026-09-27. Phase 0 (target end ~00:30).

## Done (commits)
- d25d9e0 brief; STATE+ledger; c3b020d fw_inventory.md + prev_digest.md (verified).
- sim/bench/calib_logs.py -> calib_v1.json (ARX fit; see findings).

## Firmware facts (fw_inventory, spot-checked StabilizerTask.c)
- Rates: att+rate+yaw 200 Hz; Z_pos + locx/locxs 100 Hz (cnt_h/cnt_loc>=2); Z_rate 200 Hz.
- XY loop EXISTS (not a gap): locx pos[cm] Kp.8 Ki.01 Kd4 U300 SumE200 EMin30 -> clamp +-120 cm/s
  -> locxs vel[cm/s] Kp3 Ki0 Kd6 U600 SumE200 EMin10 -> accel cm/s2 -> world->body by yaw
  -> atan(a*cos/981) clamp 15 deg (gs_max_pitch/roll_deg=15) -> roll/pitch Des.
- target_z rate-limited 0.005 m/cycle. Throttle = Z_rate.U + 2950, clamp 2000..4000 (min/max pct 0/1).
- Mixer (dir=-1): M1=T-gy-gx-gz, M2=T+gy+gx-gz, M3=T-gy+gx+gz, M4=T+gy-gx+gz. yaw Des clamp +-60 dps.
- Filters: gyro 3rd-order BW 50 Hz @1k; accel BW 30 Hz; Mahony Kp .5 Ki .001; rate filt off.
- EKF: Ekf9 v_body/b_a/b_g (no acc update); EkfOf [pos,vel,bias]x2 used for XY pos (mode 2).
- Sim frame: standard ZYX, body z up; XY loop modelled structurally (same gains/clamps/rates),
  signs made self-consistent instead of copying firmware frame reflection.

## PREV priors (prev_digest) and conflicts
- sysid roll K165 pole19.8 d15ms; pitch K185 pole16-18 d12ms; yaw integrator K~37 ("lumped gain").
- mujoco mass 1.2961 kg, I=.00839/.0093/.01485, arm .2 m, motor tau .025 s; thrust poly; max 8.37 N/motor.
- sim_coupled used m1.5, J .0023/.0023/.0015, b_roll=7.88 dps2/U, b_yaw=7.55 dps2/U.
- Yaw: PREV 0.0134 Nm/U -> 52 dps2/U: unstable with gyroz Kp4 + 15ms -> rejected; keep 7.55.
- Spin-dir conflict irrelevant: sim uses flight-validated firmware mixer sign.

## Log findings (calib_v1.json)
- ARX rate fit R2~0.02 (50 Hz logs too coarse) -> b not identifiable that way.
- Hover PWM 2950..3164 (rises flight1->8, battery/aging). Nominal 3090.
- Consistent yaw imbalance: M1,M2 -330..-540, M3,M4 +300..+550 => U_yaw ~350..510 in hover.
- ToF alt noise ~0.7 cm; f2,f3 have spike glitches (1e8) -> dropout/spike family.
- OF dx std 0.2..1.7 (10 Hz log).

## Next action
1. calib replay: roll/pitch closed-loop replay of rollPID.Des -> FB over b grid; pick b, report NRMSE.
2. sim/bench/plant.py (6-DOF vectorized, 1 kHz plant/200 Hz ctrl, sensors+EKF approx, fw cascade).
3. bench_v1 freeze (scenarios/families/splits/metrics) + commit, then Tier A controllers via VPS worker.
