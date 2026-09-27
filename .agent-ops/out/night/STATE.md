# Night run STATE (rewritten each milestone) — 2026-09-27 00:00 CST (09-28)

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

## Running
- VPS workers nw1 (INDI, L1) and nw2 (SE3ESO, MRAC x5) on agy gemini-3.1-pro-high (fallback qwen);
  one background wait -> .agent-ops/out/nw{1,2}.wait.

## Next
1. Dispatch W3 (vps-worker.sh spawn nw3 agy:gemini-3.1-pro-high,qwen ...).
2. Verify worker deliverables (read the code, run sanity + 4-row smoke locally), commit, tune each
   (64 evals), test eval, ledger H2-H5.
3. W3 brief: 3-layer frequency-routed MRAC with ablations (unrouted / reactive / predictive / both).
4. report.py: leaderboard + paired bootstrap CIs, per-family table, constraints; REPORT.md by 08:00.

## Evaluation counts on the test split (report these)
pid_fw 1, pid_tuned 1, pid_tuned2 1
