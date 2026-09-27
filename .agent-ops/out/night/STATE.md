# Night run STATE (rewritten each milestone) — 2026-09-28 01:52 CST

## Done (all committed; hashes in ledger.jsonl)
- bench_v1 frozen 5577b03 (23:45, before tuning). Calibration: replay NRMSE ~1 => results RELATIVE only;
  b = 8 dps2/U (only one of {8,11,16} without 3-8 Hz limit cycle), hover 3090, yaw imbalance 430.
- Protocols: P1 every controller 2 x 64 CMA-ES on tune split (stage 2 = tune2.py restart; FwPID-based start
  from pid_tuned; pid_tuned2 = PID with the same restart = constraint ref). P2 full FwPID knobs for all.
- Verdicts: H0 CONFIRMED, H1 CONFIRMED, H1b KILLED, H2 SURVIVES vs pid_tuned / loses to pid_tuned2,
  H3 SURVIVES (same), H4 KILLED, H5 SURVIVES (only rbf12 marginal vs pid_tuned2), H7 crm/sataware/proj
  SURVIVE vs s6, composite KILLED, H8 KILLED (db1aafd).
- Test leaderboard vs pid_tuned2 (paired, all 495 rows): sataware 0.0726 -0.0049 [-0.0107,-0.0013];
  rbf12 0.0742 -0.0032 [-0.0089,-0.0001]; pid_tuned2 0.0775; rbf24 0.078; crm 0.084; l1 0.084 +0.007;
  rbf6 0.085; proj 0.086; s6 0.099; s10 0.101; hybrid 0.103; indi 0.109; composite 0.121; pid_tuned
  0.121; se3_eso 0.228; pid_fw 0.594.
- heldout.py (db1aafd) -> results/heldout_test.md: on held-out FAMILIES no tag's CI vs pid_tuned2 is < 0
  (rbf12 -0.0073 [-0.0384,+0.0085], sataware -0.0059 [-0.0336,+0.0082]); held-out TRAJ rbf12 still < 0.
  Held-out-family divergence: hybrid 11.1%, l1 12.1%, sataware 16.2%, rbf12 18.2%, pid_tuned2 18.2%.
- c_ref (79a27c9): C89 L1 + MRAC_S6, no malloc, float32, test_equiv ALL PASS (max rel err 1.45e-6),
  COST.md: L1 0.49 us / MRAC_S6 2.27 us per 5 ms tick for 2 axes (analytic cycle counts).
- 3L crash 2 fixed c0e1ff2 (per-row T/tau/gamma/sigma), ledger N-3Lcrash2 e33acbd.

## Running
- bhjyhob7y: run_tierA.sh 4 x mrac3l (2 parallel) -> *_tn tune-split evals -> yard_3l.py.

## Next
1. On bhjyhob7y: read tails; H6 = ledger result H6 mrac3l_{both,reactive,predictive}_tn --vs
   mrac3l_unrouted_tn --split tune; H6b = both vs reactive on test (+ zigzag_1.0 xtrack, predictive vs
   unrouted on zigzag/steps). Yardsticks from results/yard_3l.json + xtrack vs pid_tuned2. Commit.
2. report.py leaderboard + per-family; fill REPORT.md (verdict, leaderboard, heldout, per-family, 3L,
   hypotheses, deployability, flight-test plan for sataware + l1, eval counts, repro). Commit, SendUserFile.

## Test-split evaluation counts (1 each)
pid_fw pid_tuned pid_tuned2 indi l1 se3_eso mrac_s6 mrac_s10 mrac_rbf6 mrac_rbf12 mrac_rbf24 mrac_crm
mrac_sataware mrac_composite mrac_proj hybrid; + 4 mrac3l pending.
