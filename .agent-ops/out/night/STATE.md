# Night run STATE (rewritten each milestone) — 2026-09-28 00:40 CST

## Done
- bench_v1 frozen 5577b03 (23:45 CST, before any tuning); label fix + API doc + ledger prereg 998c89c.
  - Calibration: replay fit poor (NRMSE ~1) => results RELATIVE only. b_roll = 8 dps2/U (near-best set
    {8,11,16}; only 8 has no 3-8 Hz limit cycle, calib_check.py). Hover PWM 3090, yaw imbalance 430.
  - Known limit: Z authority (3250 cap) => payload 1.3x rows fall at t~1.3 s under firmware PID.
- Ledger H0-H6 pre-registered (.agent-ops/out/night/ledger.jsonl).
- pid_fw test split (495 rows): median 0.594 m, div 9.1%, sat 4.0%, zigzag xtrack 0.118 m.
  Worst families: payload 1.00, combo_wind_payload 0.98, combo_unseen 0.96, battery 0.94.
- Worker briefs committed (aafc9ff): W1 INDI+L1, W2 SE3+ESO + MRAC S6/S10/RBF6/12/24.
- H0 CONFIRMED, H1 CONFIRMED: pid_tuned test median 0.121 m, paired diff -0.472 [-0.524,-0.412] (-80%),
  div 5.1%, xtrack 0.068 m; combo_unseen still 0.845 m. (326692a)
- Protocol P1 (ledger): every controller 2x64 CMA-ES evals; stage 2 = tune2.py restart from stage 1;
  FwPID-based controllers use pid_tuned as stage 1; pid_tuned2 = budget control. H1b prereg.
- H1b KILLED: pid_tuned2 (128 evals) test median 0.0775 m, -36% vs pid_tuned [-0.048,-0.037];
  64 evals was not converged => P1 equal budget is decisive. div 6.7%; combo_unseen median = inf
  (>half diverge; tuned gains trade robustness on the unseen combo). pid_tuned2 = constraint reference.
- W4 Tier B brief + H6b/H7 prereg committed 24fdb53.
- W3 brief (3-layer MRAC) + prev_ref/ copies committed 326692a; dispatch when a worker slot frees.

## Runni- nw1/nw2 verified + committed 9fa65bb: sanity_{indi,l1,se3,mrac} PASS, 4-row smoke finite for all 8.
  P2 (ledger): tierA.py fair variants keep full FwPID knobs incl. ff_v/ff_a (workers had fixed them at 0);
  INDI drops rate_kp/kd (dead). With pid_tuned knobs they start near pid_tuned (smoke 0.06-0.27 m).
- MRAC sanity weak: 1-axis 50% LOE error -1.3% at gamma 1, best -3.4% at gamma 100, Th hits TH_MAX=3
  (sim_coupled normalized law + sigma 0.01 + norm clamp; b7d65dc). Metric is vs the step, not vs the
  reference model, so inconclusive; the bench tune decides. MRAC_Proj (Tier B) frees th_max.

## Running
- run_tierA.sh (local, 2 at a time): se3_eso (bench tune + tune2), indi, l1, mrac_s6/s10/rbf6/12/24
  (tune2 from pid_tuned) then test eval each. Logs sim/bench/results/logs/<tag>.log.
- VPS nw3 (3-layer MRAC, W3) and nw4 (Tier B, base 9fa65bb). Waits: .agent-ops/out/nw{3,4}.wait.

## Next
1. When run_tierA ends: ledger result H2-H5 vs pid_tuned2 (preds were vs pid_tuned; report both), commit.
2. nw3: verify (sanity + smoke), tune all 4 modes (P1/P2), H6 on tune split, H6b on test, yardsticks.
3. nw4: verify, tune, H7. Then report.py, deployability, REPORT.md by 08:00.

ation counts on the test split (report these)
pid_fw 1, pid_tuned 1, pid_tuned2 1
