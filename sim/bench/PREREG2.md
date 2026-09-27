# Phase 2 pre-registration (2026-09-28 06:50 CST, written before any phase-2 result exists)

bench_v1 and its splits (`scen.SPLITS`: tune seeds 0-1, test seeds 100-102) stay frozen. Phase 2 adds:

| Split | Rows | Use |
|---|---|---|
| `id_fit` | TUNE_FAMS x tune trajs, seeds 0-1 | fitting sysID models only |
| `id_val` | TUNE_FAMS x tune trajs, seeds 10-11 | held-out seeds for sysID scoring |
| `id_traj` | TUNE_FAMS x (TRAJS minus tune trajs), seeds 10-11 | trajectory generalisation of the feature library |
| `test2` | FAMS x TRAJS, seeds 200-202 | confirmation, only for controllers listed in section 3 before their first test2 eval |

SysID never reads `test` or `test2` rows. The held-out families (ground_effect, motor_loss, combo_unseen) never enter `id_fit`.

## 1. H-scale: different physical features dominate at different time scales

- **Target:** per-axis (roll, pitch, yaw) residual angular acceleration. This is the true angular acceleration minus the nominal rigid-body model driven by the commanded torque, i.e. what an adaptive law must learn.
- **Candidate library:** physical terms and low-order polynomials of the estimated states and commands. The sysID code fixes the exact list before any fit.
- **Bands:** zero-phase filters split target and features into
  - L: below 0.5 Hz
  - M: 0.5-4 Hz
  - H: above 4 Hz
- **Selection:** STLSQ. The threshold comes from 5-fold CV on `id_fit` only. A feature counts as selected in a band if its ensemble (50 bootstrap fits) inclusion probability is at least 0.6.
- **SUPPORTED** only if both of these hold:
  - (a) the mean pairwise Jaccard index of the band feature sets is below 0.5 on at least 2 of 3 axes;
  - (b) the band-sum model's NRMSE on `id_val` is at most 0.90 x the global (unfiltered) model's NRMSE on at least 2 of 3 axes.
- **KILLED** otherwise. The result is reported either way.
- **Real-log check:** the same protocol runs on the airborne segments of `logs/flight_tests` (via `calib_logs.py`), holding out one log file. The real-log result alone decides the claim about the real drone. A positive sim-only result says only that the simulator contains scale structure, which it does by construction.

## 2. H-ctrl: identified per-scale features make the 3-layer MRAC the best controller

3L-v2 is the 3-layer MRAC with SINDy-selected per-band regressors, predictive feedforward, and no gain routing.

- **SUPPORTED** only if all of these hold:
  - 3L-v2 beats mrac3l_unrouted and pid_tuned2 on `test2` (paired bootstrap 95% CI of the median difference below 0);
  - the same holds on the held-out-family subset;
  - it has no more diverged runs than pid_tuned2.
- **KILLED** otherwise.
- **Ablations:** 3L-v2 without L1 features, without L2 band features, and without L3 FF. They are tuned with the same budget and reported on `tune` and `test` only.

## 3. Confirmation candidates (to be completed before the first test2 eval; append-only)

Every controller is tuned with P1 (2x64 CMA-ES on `tune`). Each gets one line with its falsifiable prediction versus pid_tuned2 on `test2`. The `test2` eval count is reported per controller.

- (filled after the literature review and the tune-split results; a line added after the controller's first test2 eval is invalid)
