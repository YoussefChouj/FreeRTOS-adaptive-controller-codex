Status: DONE (one gate exception, see Gate)
Commits: bdeb909 extractor + 6 extracts + sim port; 240dae3 digest + firmware diff; 9bedb33 ruff fix; + this report (on wp/30)
Gate: `python -X utf8 .agent-ops/gate.py ...` gives PASS scope (14 files), PASS ruff, SKIP clang-tidy, WARN pytest (none
  mapped), **FAIL size 22617/200**. About 22.2k of those lines are the six generated notebook extracts, which the brief
  requires committing. The hand-written code is about 400 lines plus the 144-line digest. Waiving size is the CEO's call.
  Without `-X utf8` the gate crashes: it decodes the emoji in the extracts as cp1252. The `PYTHONUTF8=1` form was denied.
Verification (laptop, this run):
- `python -m pytest -q ground_station/research/notebooks sim/bench -k notebook` -> `4 passed, 9 deselected in 21.42s`
- `python sim/bench/notebook_rpy_mrac.py` (100 s 'hard'): 13.2 s + 11.0 s wall for the two configs.
Worker rounds: CTE, effort high (no manager or workers, per the brief).
What was built:
- A: `ground_station/research/notebooks/extract.py` (json only) and 2 tests on a synthetic notebook. The extracts
  are in `docs/analysis/notebooks/` (p1, p2, p3, s1, s2, s3) and carry `# ruff: noqa` (verbatim notebook code).
- B: `docs/analysis/notebooks-digest-2026-10-03.md` (144 lines). Every law cites notebook + cell. It has a 17-row
  notebook-vs-firmware table (file:line) and 9 inputs for WP-27.
- C: `sim/bench/notebook_rpy_mrac.py`, a faithful port of P3 cells 1-14 and 24 under the default Config.
  `python-control` is not installed, so P is solved with scipy. Barrier stays off (it is off in the notebook too).
  `--ablation` and `--plot` are optional. Tests: `sim/bench/test_notebook_rpy_mrac.py` (30 s runs).
Measured (100 s, RMS angle error °, pitch/roll/yaw):
- MRAC + perf recovery (notebook default): vs x_m 0.558/0.573/0.923; perf recovery only: 1.464/1.463/1.671.
- MRAC with perf recovery off: vs x_r 0.893/0.890/2.981; nominal K1/K2 only: 93.66/93.66/38.59 (no integral action).
- Caveat: against the *pure* reference model x_r, MRAC + perf recovery (0.470/0.489) is worse on pitch/roll than perf
  recovery alone (0.377/0.374). The L1-like v term does most of the work. The test asserts what holds: x - x_m for the
  notebook pair, and MRAC vs nominal with perf recovery off.
Findings for the CEO (the digest has the details):
- P3 has no saved outputs. Its numbers come from the v1 copy (S3, same code apart from the barrier) or from my runs.
- Firmware gaps against the notebooks: no L1 predictor (firmware only low-passes u_ad; mrac.c:533-539), no PCH hedge,
  LF learning coded but off (mrac.c:735), feedforward stub returns 0 (mrac.c:294-299). Projection bounds are
  500-5000x tighter, and the lower bound is 0 on most features.
- The S2 "heuristic auto-tune" is a 3-preset lookup table, not a search.
Deviations:
- Added `sim/bench/conftest.py`: when `c_ref.so` is missing it skips `c_ref/test_equiv.py`. That file raised at import
  and aborted collection of the acceptance command. This is in scope (`sim/bench/**`).
- The notebook's `USE_CONTROL_RATE_LIMITING=True` is never applied in its loop, so the port does not apply it either.
