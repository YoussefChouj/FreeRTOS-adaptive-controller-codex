TBD-VERDICT (one sentence)

# Night run 2026-09-27/28: dense-trajectory tracking controller search (simulation only)

Bench: `sim/bench/` bench_v1 (frozen 5577b03, 23:45 CST, before any tuning). Every number below comes from a
committed script and a committed `sim/bench/results/<tag>_<split>.json`; commit hashes are in
`.agent-ops/out/night/ledger.jsonl`. **Results are relative only**: the plant replay fit against flight logs is
poor (NRMSE ~1), so absolute metres are not predictions of flight performance.

## Setup (what was held fixed)
- Plant: firmware cascade rates (att/rate/yaw 200 Hz, position 100 Hz), tilt limit +-15 deg, real mixer with
  2000..4000 clamp, throttle = Z_rate.U + 2950, 15 ms delay, motor lag 1/19.8 s, b = 8 dps^2/U
  (`calib_check.py`: only 8 of the near-best set {8, 11, 16} has no 3-8 Hz limit cycle), hover PWM 3090,
  yaw imbalance 430 (FLIGHT8).
- Controllers see only estimated states (firmware sensor chain: noisy, biased gyro/acc; Mahony-lite attitude estimate, no magnetometer) at firmware rates and the
  onboard reference generator's preview; no truth, no disturbance values. Scored on true states.
- Splits: tune = 96 rows (12 families x trajectories, seeds disjoint); test = 495 rows = 15 families x 11
  trajectories x seeds {100,101,102}. Held out of tuning: families ground_effect and motor_loss, the unseen
  combination combo_unseen, and the trajectories not in the tune split.
- Metric: position RMSE on true states (m); a diverged row = inf (counted as failure, kept as worst).
  Medians with paired bootstrap 95% CI (2000 resamples, seed 0) on common random numbers.
- Equal effort (protocol P1): every controller gets 2 x 64 CMA-ES evaluations on the tune split. Controllers
  built on the firmware PID start stage 2 from the shared stage-1 pid_tuned; the PID itself gets the same
  restart (pid_tuned2 = budget control). P2: every FwPID-based controller keeps the full FwPID knob set.
- Constraints vs pid_tuned2: divergence rate <= ref, saturation <= 0.05, every family median <= 1.1 x ref.

TBD-SECTIONS: leaderboard, per-family, 3-layer, hypotheses, deployability, flight-test plan

## Sim-to-real risks
1. Plant fit is poor (NRMSE ~1). Rankings assume the sim's error structure transfers; margins of a few
   percent (e.g. rbf12 vs pid_tuned2, -4%) are inside that uncertainty.
2. Control effectiveness b: 8 was chosen from {8, 11, 16}. At b = 16 every adaptive loop gain doubles;
   adaptive gains tuned at b = 8 can limit-cycle. Flight stage 1 checks this (hover, adaptation frozen).
3. Tuned gains trade robustness: pid_tuned2 and mrac_rbf12 diverge on more than half of combo_unseen rows,
   which the tuner never saw. The flight envelope must stay inside the tuned families until proven.
4. Authority: FLIGHT8 yaw imbalance holds U 450-650 and drives M3/M4 into the 4000 clamp. The sim models
   imbalance 430; if the real one is larger, saturation dominates and adaptive laws can wind up.
5. Ground windup: FLIGHT8 showed U = 650 before liftoff. Any adaptive term must be frozen and reset until
   liftoff (the sim starts airborne, so it does not test this).
6. Estimator: the sim gives noise + bias + lag, not the real EKF/optical-flow drift modes. Controllers that
   use accelerations or regressors on rate derivatives are exposed to vibration the sim does not model.
7. Motor model: first-order lag and fixed 15 ms delay; real ESC/prop nonlinearity and battery sag
   (14.0 V in FLIGHT8) change b in flight (the battery family covers only part of this).

## Reproduce
```bash
cd sim/bench
python bench.py eval fwpid:FwPID --split test --tag pid_fw
python bench.py tune fwpid:FwPID --tag pid_tuned
python tune2.py fwpid:FwPID --start results/pid_tuned_tune.json --tag pid_tuned2
bash run_tierA.sh <tag> ...        # tune2 from pid_tuned + test eval for each tag (C-map inside)
python report.py --split test --ref pid_tuned2 --out ../../.agent-ops/out/night/leaderboard.md
python yard_3l.py mrac3l_unrouted mrac3l_reactive mrac3l_predictive mrac3l_both
python sanity_indi.py; python sanity_l1.py; python sanity_se3.py; python sanity_mrac.py; python sanity_mrac3l.py
```
TBD-REPRO: ledger compare commands, c_ref test, hybrid.
