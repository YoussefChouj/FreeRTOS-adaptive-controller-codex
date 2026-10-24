# Spec: drift NN v2, a full offline study of learned drift reduction (LOFO / LODO, trained on Kaggle)

Status: SPEC, written 2026-10-24 by the CEO planning session. Built by one agent in D:\Projects\UAV_lab\fc-driftnn
(branch `drift-nn-lofo`). Offline only. It replaces Part B and prompt B of
docs/design/2026-10-24-state-estimation-upgrades.md. The prompt is prompt 4 in docs/agent/2026-10-24-agent-prompts.md.

Scope: any neural network, or any other state-of-the-art method, that lowers the horizontal drift of the
optical-flow EKF. The only limit is what we can implement and train on the Kaggle lane. Nothing from this work
reaches the drone: shadow only, until the win rule (section 2) passes and the CEO decides.

## 1. Where we are (measured)

| Fact | Source |
|---|---|
| The 10-13 TCN: 15.7 cm p90 on its training flights, 260 cm p90 on a held-out flight. A 17x gap: it memorised the training flights | docs/research/2026-10-13-sota-extpos-nn-plot.md:164-165 |
| No leave-one-flight-out (LOFO) result exists | same doc |
| Log loader | ground_station/analysis/nn_io.py |
| Camera truth (truth4): `logs/video_truth/<name>/truth4.json`, made by tools/d5_truth4_batch.py, one lab day per run, dry run unless `--run` | d5_truth4_batch.py:3, :93-94 |
| Kaggle lane: run.py, stage.sh, sweep.py, kernel-metadata.json; the kaggle CLI runs in WSL, free GPU about 30 h per week | tools/kaggle_nnio/ |

## 2. Win rule (pre-registered: copy it into the report before the first result, then do not change it)

- Metrics, per held-out flight, against truth4:
  - p50 and p90 horizontal error (primary: p90);
  - ATE (RMS position error, same start alignment for every method);
  - RTE over 30 s windows (PROPOSED);
  - drift rate in cm/min.
- A method wins only if OF-EKF + method beats OF-EKF alone on all of these:
  - lower p90 on ≥ 2/3 of the LOFO folds (PROPOSED);
  - one-sided Wilcoxon signed-rank test on the per-flight p90, p < 0.05 (PROPOSED);
  - consistent covariance: mean NIS inside the chi-square 95 % interval (PROPOSED);
  - LODO (leave one lab day out): median p90 not worse than OF-EKF alone.
- Many methods get tried. The claim is for ONE method, chosen on the validation folds, then run once on the test
  folds. All the others are descriptive.
- Until a method wins: shadow only.

## 3. Step 0: data inventory (first; it sets the model size)

- One row per flight: date, folder, flight type, minutes airborne, IMU / flow / motor-command rates, truth4 present
  and coverage %, tracker-loss %, the camera delay and where it was measured.
- Find which flights already have truth4: `ls` the logs/video_truth folders only. Do not scan the logs/ tree.
- Do NOT run d5_truth4_batch.py: it writes truth4.json into the main checkout's logs. List the days that lack truth4
  and the exact command (`python tools/d5_truth4_batch.py --day <date>`, dry run, then `--run`). The operator runs
  it after the lab.
- Write the totals (flights, minutes, windows). Choose the model size from them and say why: few flights means a
  small model.

## 4. Harness (E1)

Code under ground_station/analysis/drift_nn_v2/:
- **Splits:** LOFO and LODO. Validation flights are picked inside the training folds (nested). No window crosses a
  flight. Test windows do not overlap.
- **Normalization:** statistics from the training folds only.
- **EKF replay:** an offline Python replay of the OF-EKF.
  - First check: it reproduces the logged firmware estimate. Write the error and your tolerance.
  - If it cannot: report it, use the logged estimate as the F0 baseline, and compare the methods that need the
    replay (F1, F2, F3) against the replay's own baseline.
- **Eval:** the section 2 metrics, per flight and per fold, with plots.
- **Learning curve:** train on 25 / 50 / 75 / 100 % of the training flights.
- **Seeds:** 3 per configuration (PROPOSED). Report the mean and the spread. Never pick the best seed on a test fold.
- **Tests (fast pytest):**
  - no flight on both sides of a split;
  - normalization uses training data only;
  - a synthetic flight with a known drift: the harness recovers it;
  - the replay reproduces a synthetic EKF run.

## 5. Literature (verify each one: title, venue, year, one quoted key result; drop any you cannot verify)

| Work | Idea we take | Family |
|---|---|---|
| Chen et al. 2018, IONet (AAAI) | inertial odometry learned from IMU windows | F2 |
| Herath et al. 2020, RoNIN (ICRA) | heading-agnostic frame, velocity regression | F2 |
| Liu et al. 2020, TLIO (RA-L) | displacement + covariance, fused by an EKF | F2 |
| Brossard, Barrau, Bonnabel 2020, AI-IMU dead reckoning (IEEE T-IV) | the net tunes the EKF noise covariances | F1 |
| Brossard, Bonnabel, Barrau 2020, gyro denoising (RA-L) | a denoiser judged on its own | F5 |
| Rao et al. 2022, CTIN (AAAI) | a transformer for inertial navigation | F2 architecture |
| Zhang et al. 2022, DIDO (RA-L) | quadrotor dynamics + IMU for odometry | F2 / F3 |
| Cioffi et al. 2023, learned inertial odometry for drone racing (RA-L) | thrust / motor commands as inputs | F2 |
| Jayanth et al., EqNIO (venue: verify) | equivariant canonical frame | F2 augmentation |
| Qiu et al., AirIMU (venue: verify) | IMU correction with uncertainty | F5 / F1 |
| Qiu et al. 2025, AirIO | body-frame inputs generalise better on drones | F2 inputs |
| Revach et al. 2022, KalmanNet (IEEE TSP) | a learned Kalman gain | F4 |
| Haarnoja et al. 2016, Backprop KF (NeurIPS) | an end-to-end differentiable filter | F4 |
| Bauersfeld et al. 2021, NeuroBEM (RSS) | residual quadrotor aerodynamics | F3 |
| Torrente et al. 2021, data-driven MPC with GP residuals (RA-L) | a GP on the dynamics residual | F6 |
| Brunton et al. 2016, SINDy (PNAS) | sparse model discovery | F6 |

Add newer work (2024-2026) that fits, with the same verification. The arXiv and paper-search tools are fine.

## 6. Method families (each one feeds the EKF; none replaces it)

- **F0, baselines.**
  - (a) OF-EKF alone (logged).
  - (b) OF-EKF + a rotor-drag pseudo-measurement, a_xy_body = -k v_xy_body, with k fitted on the training folds
    only. The same idea is estimator item A5; your result informs it.
- **F1, learned noise (AI-IMU style).** A small net outputs the flow / drag measurement noise R (and Q if useful)
  per window. The EKF is otherwise unchanged.
- **F2, learned displacement (TLIO style).**
  - The net outputs Δp over a 1-2 s window (PROPOSED) plus its covariance. The EKF fuses it as a measurement
    behind a Mahalanobis gate.
  - Inputs: body-frame gyro and accel (gravity removed), attitude given separately, motor commands; flow optional.
  - Architectures, on the same parameter budget: ResNet1D, TCN, GRU, small transformer.
  - Augmentation: yaw rotation, or an EqNIO-style canonical frame.
  - Loss: Gaussian NLL, after an MSE warm-up (TLIO does this: verify).
- **F3, residual dynamics (NeuroBEM style).** The net predicts the residual force from motor commands, velocity
  and attitude. It goes into the EKF process model.
- **F4, KalmanNet / differentiable filter.** Only if the inventory shows enough data. Write the threshold you use.
- **F5, gyro denoiser.** Judged alone (yaw vs truth4, noise at rest). Drift is judged with and without it.
- **F6 (optional).** GP residual and SINDy drag: cheap, readable baselines for F3.

## 7. Experiment order and stop rules

E0 inventory → E1 harness + F0 → E2 F1 → E3 F2 small (one architecture: local smoke run, then Kaggle) →
E4 F2 sweep + ablations on Kaggle → E5 F3 → E6 F5 → E7 F4 / F6.

- Stop rule: if F2-small loses to F0 on most folds, skip the sweep. Report the learning curve and the reason.
- Ablations (E4): inputs (IMU / + motor / + flow), window length, model size, data amount, augmentation on / off,
  seeds.
- One commit per experiment. The report grows with each one.

## 8. Compute

- **Kaggle:** private datasets and private kernels only. Budget about 10 GPU-h for this spec (PROPOSED). Log the
  hours of every run. Download metrics and small plots, not checkpoints (unless needed for a shadow run).
- **Colab:** none without the operator's explicit OK in chat. Its compute units are reserved.
- **Local:** data prep, tests, and smoke runs under 2 minutes. No local training: the laptop's power is fragile.
- Never print, log or commit the Kaggle token. Use the existing WSL setup; tools/kaggle_nnio/stage.sh shows how.

## 9. Leakage and pitfalls (each one gets a test or a written check in the report)

| Pitfall | Check |
|---|---|
| The same flight on both sides of a split | split test |
| Normalization from all the data | test |
| Tuning on the test fold | nested validation; test folds used once |
| Overlapping windows across train and test | no window crosses a split |
| Truth that comes from the EKF | truth4 must be camera-only: confirm it in d5_truth4_batch.py |
| Camera delay misaligned | align with the measured delay; report the cross-correlation lag |
| Flights of the same day are alike | LODO |
| Best seed picked on the test folds | seeds averaged |
| Tracker loss | lost segments masked out of the metric; the % reported |

## 10. Deliverables

- Code: ground_station/analysis/drift_nn_v2/ (data, splits, ekf_replay, models, train = the Kaggle entry, eval,
  tests). Import nn_io.py; do not edit it or any other shared file.
- Kaggle: new files only, under tools/kaggle_nnio/ (name them v2_*).
- Report: docs/research/2026-10-24-drift-nn-v2-report.md:
  - the inventory and the win rule;
  - the literature, with quotes;
  - LOFO and LODO tables per method (p50, p90, ATE, RTE, cm/min);
  - ablations, the learning curve, NIS;
  - GPU-h used;
  - go / no-go against section 2, what is left, and a 10-line summary.

## 11. Rules

- Work only in D:\Projects\UAV_lab\fc-driftnn. Read the logs (read-only) from
  D:\Projects\UAV_lab\FreeRTOS-adaptive-controller-codex\logs; never write there.
- No firmware, no 8081 service, no drone, no flash. No push, no merge. One commit per step; stage by path.
- No invented numbers: each comes from a log, a run (with its id), a paper (quoted), or is marked PROPOSED.
