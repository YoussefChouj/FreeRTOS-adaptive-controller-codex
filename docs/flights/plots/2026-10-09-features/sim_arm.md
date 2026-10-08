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
