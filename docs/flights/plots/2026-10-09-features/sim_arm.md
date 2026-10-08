# Sim: 293 g arm offset, PID vs vp rows 17 / 19 (2026-10-09, sim only, PROPOSED)

`cd sim/bench; python demo_loads.py --arm --seeds 5 --out results/demo_arm.json`. Same harness as
[sim_rope.md](sim_rope.md): flashed PID (pid_tuned2 + f1x), with the firmware MRAC driver on top (inject on).
The arm's mount motor is not known, so the 293 g point mass sits at each of the 4 motor mounts. Hover 20 s, 5 seeds.

| mount | PID RMSE (m) | row 17 | row 19 | row 19 vs PID, 95% CI (m) | max tilt PID / 17 / 19 (deg) |
|---|---|---|---|---|---|
| m0 | 0.037 | 0.045 | 0.081 | +0.045 [+0.014, +0.198] | 11.3 / 14.4 / 31.6 |
| m1 | 0.037 | 0.035 | 0.088 | +0.052 [+0.000, +0.095] | 12.1 / 6.4 / 28.3 |
| m2 | 0.039 | 0.040 | 0.051 | +0.012 [-0.003, +0.041] | 12.9 / 6.0 / 18.2 |
| m3 | 0.040 | 0.041 | 0.060 | +0.020 [+0.003, +0.069] | 11.0 / 6.2 / 26.0 |

What it says:
- PID already holds the rigid 293 g offset (RMSE 0.04 m, no altitude sag, saturation < 1%). There is little for the
  adaptive layer to win here in the sim.
- Row 17 is neutral (all CIs cross zero) and halves the max tilt on 3 of 4 mounts.
- Row 19 (D self-tuning gain) is worse than PID on 3 of 4 mounts with the CI clear of zero, and tilts 18-32 deg.
  **Do not fly row 19 with the arm.** Row 17 is the arm candidate.
- Sim only: the real arm also flexes and the rope run showed the sim misses the swing, so treat this as a ranking
  hint, not a number to expect.

## Q20: faster u_ad filter (omega_u 16 rad/s on pitch/roll, row 17 otherwise)

`python demo_loads.py --arm --seeds 5 --wu 16` (and `--rope`). The firmware filter is 4 / 5 rad/s (pitch / roll).

| case | row 17 RMSE (m) | row 17 wu16 | wu16 vs PID, 95% CI (m) | max tilt row 17 / wu16 (deg) |
|---|---|---|---|---|
| rope570 | 0.076 | 0.272 | +0.031 [-0.277, +0.254] | 21.8 / 27.0 |
| rope570_drop | 0.086 | 0.182 | +0.099 [-0.147, +0.104] | 14.6 / 23.3 |
| arm293_m0 | 0.045 | 0.126 | +0.089 [+0.042, +0.390] | 14.4 / 37.9 |
| arm293_m1 | 0.035 | 0.123 | +0.087 [+0.022, +0.096] | 6.4 / 26.1 |
| arm293_m2 | 0.040 | 0.090 | +0.051 [+0.023, +0.100] | 6.0 / 26.4 |
| arm293_m3 | 0.041 | 0.095 | +0.056 [+0.024, +0.084] | 6.2 / 27.8 |

Worse everywhere, CI clear of zero on all 4 arm mounts. The lag in the replay is real, but removing it at the output
filter lets the estimate's noise through. **Q20: keep omega_u 4 / 5.**
