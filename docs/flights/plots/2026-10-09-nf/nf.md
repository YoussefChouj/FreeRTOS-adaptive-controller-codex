# Neural-Fly public dataset: tracking by method and feature ranking (overnight item 8)

Data: github.com/aerorobotics/neural-fly (`data/training`, 6 files; `data/experiment`, 30 files). The repo has no
licence file, so the CSVs stay outside the repo (`C:/tmp/nf_data`) and only these derived tables are committed.
Tool: `python -m ground_station.analysis.feature_id --nf <csv...>` (loader `nf_load`, target `fa` = measured
aerodynamic residual force, world x/y/z; one segment per file, LOSO = leave one file out).

Full outputs: [feature_id_nf_training.md](feature_id_nf_training.md), [feature_id_nf_experiment.md](feature_id_nf_experiment.md) (+ `.json`).

## 1. Tracking RMSE |p - p_d| [m], measured from the 30 experiment files

Pooled over the vehicles and trajectories in each method x wind cell (file names `<vehicle>_<traj>_<method>_<wind>`).

| wind | baseline (PID) | INDI | L1 adaptive | NF | NF-C | NF-T |
|---|---|---|---|---|---|---|
| none | 0.111 | 0.080 | 0.056 | **0.038** | 0.053 | 0.047 |
| 35 | 0.127 | 0.086 | 0.096 | 0.075 | 0.086 | **0.073** |
| 70 | 0.243 | 0.169 | 0.197 | 0.149 | 0.150 | **0.122** |
| 70 + 20 sin | 0.357 | 0.123 | 0.142 | **0.102** | 0.147 | 0.113 |
| 100 | 0.456 | 0.281 | 0.339 | 0.245 | 0.259 | **0.191** |

Every adaptive or learned method beats the baseline in every wind. A plain adaptive law (L1) already gives
-19..-60 %. The learned basis (NF-T) does best in strong wind (0.191 vs 0.456 m at 100). The gap between L1 and NF
is the value of a basis that matches the physics.

## 2. Which signals explain the residual force (LOSO R^2, 33 candidates)

| axis | training (6 wind levels) | experiment (30 files) | consensus top features |
|---|---|---|---|
| x | 0.78 | 0.84 | vx, vx 0.1 s ago, thrust direction thrx/thrz, T_sp |
| y | 0.42 | 0.07 | vy, thry, T_sp (weak, not explained by these signals) |
| z | 0.78 | 0.80 | vz 0.5 s ago, T_sp, motor pwm spread |

Velocity delayed by 0.1-0.5 s ranks as high as the current velocity on x and z. The same lag lesson came out of
our own replay ([meta.md](../2026-10-08-meta/meta.md)): the useful state is slightly in the past.

## 3. What transfers to our controller (and what does not)

| lesson | for row 17 / the rope |
|---|---|
| Adaptation beats PID in every disturbance level, even a generic law | consistent with the sim (row 17 cuts the rope sag 0.24 -> 0.08 m); not flown yet |
| A physics-matched basis is worth 24-44 % (best NF variant per wind) over a generic adaptive law | our matched basis (measured swing acc) diverged on injected logs (item B), so it is not ready; row 17 keeps the generic L2 law |
| Lagged states carry as much as current ones | supports the prediction-error (L2) path with its delay, over tracking-error learning |
| The data is a translational force from wind, no payload | it cannot rank torque features or test slung-load swing; no Neural-Fly number applies to the rope directly |

Not done: replaying our vp rows on this data. Our laws adapt attitude torque from the FC's own logs, and the
dataset holds neither our states nor torque, so a replay would not measure anything meaningful.
