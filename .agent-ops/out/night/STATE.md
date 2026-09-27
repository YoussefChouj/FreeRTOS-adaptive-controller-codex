# Night run STATE (rewritten each milestone) — 2026-09-28 02:40 CST (RUN COMPLETE)

## Done (all committed; hashes in ledger.jsonl)
- bench_v1 frozen 5577b03. Calibration NRMSE ~1 => RELATIVE results only; b=8, hover 3090, yaw imb 430.
- P1: 2 x 64 CMA-ES per controller on tune split; pid_tuned2 = PID with same restart = constraint ref.
- All ledger entries have verdicts (H0..H8, H6 KILLED, H6b KILLED; 10410cd).
- Test leaderboard regenerated (report.py, 20 tags, 1 test eval each) -> .agent-ops/out/night/leaderboard.md.
  vs pid_tuned2 0.0775: sataware 0.073 -0.005 [-0.011,-0.001] FAIL fam(dropout); mrac3l_unrouted 0.073
  -0.005 [-0.011,-0.001] FAIL fam, sat 0.031, xtrack 0.040; rbf12 0.074; ... pid_fw 0.594 (xtrack 0.118).
- heldout_test.md regenerated with 3L tags (results/heldout_test.md).
- 3L: unrouted best; routing loses +17-20% on tune (H6 KILLED); Both-Reactive CI includes 0 (H6b KILLED).
  Yardsticks (unrouted): ref-model RMSE 27% FAIL; lag -2.5 ms PASS; gate tones PASS; RMSE -88% vs pid_fw
  PASS / -5.9% vs pid_tuned2 FAIL; xtrack 0.040 <= 0.07 PASS; xtrack vs pid_fw 0.118 -66% PASS / vs
  pid_tuned2 0.042 -5.7% FAIL. Diagnosis: routing concentrates gain (gamma 2.2-3.0 vs 0.36), ref-model
  mismatch 18-27% dominates s_err, predictive inert on steps; yard_3l stride bug fixed (N-yard3l).
- c_ref C89 L1 + MRAC_S6 (79a27c9) test_equiv PASS.

## Status
All Done-when items true: REPORT.md committed 6f6c2a1 and sent (SendUserFile proactive). No further experiments.
