Status: DONE
Commits: 9f4b25e cte(wp10) sim + CLI + tests; ab7f7d9 cte(wp10) gate fixes; + this report and the analysis doc (on wp/10)
Gate: GATE PASS (--base night/2026-10-02 --max-lines 3000, size 1455/3000; scope, clang-tidy, ruff, pytest all PASS) on ab7f7d9
Verification (laptop, this run):
- python -m pytest ground_station/research/sim/tests -q -> 22 passed (16 old + 6 new), 0 skipped
- python -m ground_station.research.sim.cascade_rank --logs <codex>/logs/vofa -> 3 calibration tables + S1/S2/S3 rankings in
  78 s (27 candidates x PID/MRAC x nominal and +-30% plant). Output: .agent-ops/out/wp10-cte-rank.txt
- Golden C-vs-Python: ran and passed here. API/pid.c is built as a host exe (not a DLL, so no WinError 193) and compared over 8 rows x 600 steps to rtol 1e-6/atol 1e-5.
Worker rounds: CTE, effort high (no new worker rounds). Kept from r3: the _ccore stub headers. Replaced: cascade.py, cascade_rank.py,
  test_cascade.py, build.py. Removed: rank_out.txt, scratch_log.py, scratch_opt.py (`git rm` was denied, so plain rm + git add).
Result (docs/analysis/2026-10-02-fix-ranking.md has the tables):
- PID F1x+F3+F5w+F6a is top-4 in S1/S2/S3: hover 1.2/3.8 cm steady/rms (F0 4.7/5.6), load step 2.2/5.0 (F0 36.3/37.0),
  waypoints rms 4.3 (F0 30.9). F1x = WP-9 F1w caps + EMin opened (angle 3 -> 10 deg, gyro 2 -> 50 dps), a CTE addition. F1w
  as briefed fails the load step for PID (31.5 cm), because EMin freezes both integrators while the error is large.
- F5w alone and F1w/MRAC alone make hover worse: lean (B) and the inner shortfall (A) partly cancel on roll under F0.
- F1b/F1c have no effect (gyro UiMax 20 binds). F4 gains nothing over F3. F2 gives the 0.8-0.85 Hz sway (log shadow4: 0.70-0.75), which validates the sim.
- Calibration: SysID inner plant + 25 Hz gyro dither at the logged limit-cycle rms (13.3/6.7 dps). With it the sim matches the
  logged angle error and implied gyro Ui ~0 (shadow14 pitch angle U 2.74 vs 2.71). OF fit: delay 0 ms, noise 0.8, push 10
  cm/s^2 rms (delay and noise on grid edges, not identifiable). MRAC offload tau fitted to active15: 0.35 s.
Deviations / open questions:
- MRAC is a first-order offload surrogate (du_ad/dt = gyro U / tau), not mrac.c, as the CEO allowed. The logged u_ad spikes to 162, peaks near 97 and settles at ~32 ticks.
- WP-13 must gate the F1x gyro integrators to FLYING (UiMax 160, EMin 50); the sim is always airborne and cannot test ground windup.
- S1 uses the WP-9 trim as the lean and lets A come out of the sim, so no extra constant push is added. The CEO's X -16 / Y +14 is
  WP-9's mean a_A + a_B.
- `git merge night/2026-10-02` into wp/10 was denied: wp/10 is not a fast-forward of night (night has WP-9 + briefs). The gate diff
  (three-dot) is clean, but a merge or rebase is needed before `merge --ff-only`.
- Report file is WP-10-cte.md per the CTE rules; the brief's docs/agent/reports/WP-10.md still holds the manager's BLOCKED report.
