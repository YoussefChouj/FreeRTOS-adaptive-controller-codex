# WP-24 CTE report: adaptive-controller audit, flight-log analysis, firmware design

Status: DONE
Worker rounds: CTE, effort xhigh (no manager/workers per brief)

## Deliverables
- `docs/analysis/controller-roadmap-2026-10-03.md` (200 lines): Q1-Q4, WP-27 firmware design (V1-V3), workflow-B plan.
- `ground_station/analysis/refmodel_replay.py` + `tests/test_refmodel_replay.py` (10 tests): offline replay of
  `MRAC_UpdateAxis` (types 0/1/2, firmware or fitted bandwidth, `--drive-norm`) on VOFA flight logs.
- `ground_station/analysis/controller_cost.py` + `tests/test_controller_cost.py` (3 tests): analytic cycle/RAM
  estimate (PROPOSED), calibrated to the night report's L1/S6 numbers.

## Acceptance
`python -m pytest -q ground_station/analysis/tests` -> `290 passed, 5 skipped in 292.97s`
Gate: scope/ruff/pytest PASS; size FAIL 723/200 lines. The brief asks for a 200-line doc plus scripts, so the CEO must waive it.

## Key measured findings (flight16 + flight14, shadow, `<main>/logs/vofa/`)
- Both flights flew passthrough (logged e = x - r to 3e-8). Replay vs logged u_ad corr 0.95-0.99, except f14 roll 0.03 (unexplained).
- Pitch/roll: the ref-model order changes 0.2-5 Hz rate error by only 6-21 %. A 2nd-order model at wn 44 gives the
  lowest band error of the firmware options and closes the plant lag (from 25-55 ms to -5..25 ms).
- Yaw: the ref model at 30 rad/s is >10x too fast (lag >=150 ms). A 1st-order model at bw 2 cuts band error 58-69 %.
- Shadow authority 1.0-3.1 comes from a standing rate-command offset (roll mean r +0.126 rad/s) that holds the
  bias weight at its 0.15 limit.
- Firmware trap: types 1 and 2 shrink the drive 88x / 3872x; scaling gamma cannot fix it (the leak scales too).
  WP-27 must normalize the drive: s = PBe + λ·e_dot.
- Performance recovery is not implemented (LPF only); the sim MRAC_PR was untuned, uncommitted and diverged.
  3L routing was KILLED in sim.
- Fly in WP-27 order: V1 refmodel v2, V2 SatAware leakage, V3 RBF12 block. All switches default OFF.

## Caveats
- Three older logs (git unknown) ran a different law and were not replayed. Hover lacks excitation, so the
  doublet campaign must confirm the order.
- The fitted bandwidths hit grid bounds (yaw bw 2, ζ 0.4). Cycle/RAM numbers are analytic (PROPOSED); measure
  them with `mrac_cyc`.
- Flight logs live untracked in the main checkout; they were read only, never modified.
- `sim/bench/ctrl_p2_yucelen.py` exists only uncommitted in the main checkout.

## Commits
2ebd95e scripts+tests, 8b59ffe --drive-norm, 77418cd roadmap, plus this report.
