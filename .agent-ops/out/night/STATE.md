# Night run STATE (rewritten each milestone) — 2026-09-28 01:25 CST

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
- nw1/nw2 verified + committed 9fa65bb: sanity_{indi,l1,se3,mrac} PASS, 4-row smoke finite for all 8.
  P2 (ledger): tierA.py fair variants keep full FwPID knobs incl. ff_v/ff_a (workers had fixed them at 0);
  INDI drops rate_kp/kd (dead). With pid_tuned knobs they start near pid_tuned (smoke 0.06-0.27 m).
- MRAC sanity weak: 1-axis 50% LOE error -1.3% at gamma 1, best -3.4% at gamma 100, Th hits TH_MAX=3
  (b7d65dc). Metric is vs the step, not the reference model => inconclusive. MRAC_Proj keeps th_max 3.0.
- 1985aea: test results vs pid_tuned2 (paired): l1 0.084 +0.007 [+0.001,+0.010]; indi 0.109 +0.031;
  mrac_s6 0.099; mrac_s10 0.101; se3_eso 0.228 +0.151 (div 21.8%). H2 SURVIVES vs pid_tuned but loses to
  pid_tuned2; H3 SURVIVES (-30% vs pid_tuned), yaw_imb_hi 0.104 vs 0.219, combo_unseen 0.455 vs inf;
  H4 KILLED. 3L (nw3) + Tier B (nw4) verified (verify_mrac3l.txt, verify_mrac_b.txt).

- Test (vs pid_tuned): mrac_rbf12 0.074 -0.047 [-0.053,-0.041] (best median; div 7.1% > pid_tuned2 6.7%,
  combo_unseen inf, yaw_imb_hi 0.206); mrac_rbf6 0.085. rbf24 running. H5 result after rbf24.
- yard_3l.py (75b2315): refmodel with pid_tuned knobs 16.4% RMSE / -2.5 ms lag (was 46.6% with fw defaults);
  u_ad/u_nom RMS 0.15 (adaptation not negligible). Run per 3L tag after batch 2.
- H8 prereg (Tier C, 75b2315): Hybrid_RBF12_L1Yaw (rbf12 roll/pitch + L1 yaw). Leak disclosed (idea from test
  results) => claim judged on held-out rows only vs best Tier A. nw6 implements (VPS, bg wait bs2vsrae0).
- nw5 (c_ref C89 L1 + MRAC_S6 + COST.md) on VPS, bg wait b5l6ddske.

## Running
- run_tierA.sh: mrac_rbf6/12/24 (bf3uf9bbt). Then bgtf2b929 swaps in run_tierA.sh.new (3L + Tier B tags)
  and runs mrac3l_{both,unrouted,reactive,predictive}, mrac_{crm,sataware,composite,proj} (-> ~02:40).

## Next
1. H5 result (mrac_* vs pid_tuned); commit run_tierA.sh + results.
2. H6: bench.py eval 3L modes --split tune with tag suffix _tn (tune2 owns <tag>_tune.json); paired CI
   both/reactive/predictive vs unrouted. H6b on test. Yardsticks (refmodel with tuned knobs, xtrack).
3. H7 Tier B vs mrac_s6. H8: verify nw6, tune2 ctrl_hybrid:Hybrid_RBF12_L1Yaw from pid_tuned (P1),
   test eval, ablation vs rbf12 and l1. Maybe extend c_ref to rbf12/hybrid if finalist.
4. report.py, deployability, REPORT.md by 08:00.

## Test-split evaluation counts (report these)
pid_fw 1, pid_tuned 1, pid_tuned2 1, indi 1, l1 1, se3_eso 1, mrac_s6 1, mrac_s10 1, mrac_rbf6 1, mrac_rbf12 1
