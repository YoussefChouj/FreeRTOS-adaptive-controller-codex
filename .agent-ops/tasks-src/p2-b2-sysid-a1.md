# Task p2-b2-sysid-a1: implement PREREG2 amendment A1 in the sysID code and re-run H-scale on sim

<!-- Model: VPS agy:gemini-3.8-flash-high, fallback gemini-3.1-pro-high. -->

## Goal
Running `python -m sim.bench.sysid.hscale --sim --amend a1` writes `sim/bench/sysid/results/hscale_sim_a1.{json,md}` and `sim/bench/sysid/library/features_sim_a1.json`, both produced with the A1 protocol. The B1 result files stay byte-identical. `python -m pytest sim/bench/sysid -q` passes.

## Context pointers
- **Binding protocol:**
  - `sim/bench/PREREG2.md` section 1
  - the section "Amendment A1", at the end of the file. Implement exactly its four changes.
- Code you will change (it is phase-2 code, so you may edit it):
  - `sim/bench/sysid/sindy.py`. `cv_threshold` (line ~136) shuffles samples into folds. `bootstrap_ensemble` (line ~206) does `rng.choice(N, ...)` over samples. `stlsq` runs on raw columns.
  - `sim/bench/sysid/hscale.py`. `run_sim` reshapes `theta_*` per row: see lines ~88-200, `n_features`, `th_band.reshape(-1, n_features)`. Row identity is available there, so use it to build the groups.
  - The `--real` path: groups are the airborne segments / log files.
- The data cache in `sim/bench/sysid/cache/` may already exist on your machine. If it does not, `data.py` regenerates it (about 20 min).

## Deliverables
1. `sindy.py`. Add these as options; the defaults keep the B1 behaviour:
   - `groups=` on `cv_threshold` and on `bootstrap_ensemble` (group k-fold / whole-group resampling)
   - `one_se=True` on `cv_threshold`
   - `standardize=True` on `stlsq` and the ensemble: scale columns to unit std, threshold the scaled coefficients, and return de-standardised coefficients
2. `hscale.py`. Add `--amend a1`, which turns on all four A1 changes and writes the `_a1` files. For each axis × band, report `K`, `n_selected`, `selected_over_K` and `non_sparse_flag` (true when selected/K > 0.5). Apply the verdict rules of section 1 unchanged.
3. `test_sysid.py`. Add these tests:
   - Group CV never puts samples of one group in both train and val.
   - Standardised selection is invariant to scaling one column by 1000.
   - On a sparse 3-term system with AR(1)-correlated noise (phi = 0.98) in 6 groups, A1 selects at most 1 spurious term out of 10, while plain shuffled CV selects more. Assert the A1 half; print the shuffled count.
   - The row bootstrap resamples whole groups.
4. `.agent-ops/out/p2-b2-sysid-a1.md`, a digest of at most 30 lines:
   - STATUS
   - test counts
   - a table per axis × band with threshold, n_selected/K and non-sparse flag
   - Jaccard per axis, and the NRMSE ratio per axis on id_val and id_traj
   - the A1 verdict and the universal features
   - runtime
   Copy every number from `hscale_sim_a1.json`.

## Constraints
- Do not change `results/hscale_sim.json`, `results/hscale_sim.md` or `library/features_sim.json`; they are the registered result. Do not edit `PREREG2.md`.
- Do not modify files in `sim/` outside `sim/bench/sysid/`. No firmware, no hardware, no port 8081.
- Numpy only. Keep peak RAM under 2 GB on the 2-vCPU VPS.
- Foreground commands only. Do not commit. Do not commit the cache.
- Never report a number you did not produce with the script.

## Verification
- `python -m pytest sim/bench/sysid -q` -> all pass (5 old + new).
- `python -m sim.bench.sysid.hscale --sim --amend a1` -> exit 0.
- `git diff --stat -- sim/bench/sysid/results/hscale_sim.json sim/bench/sysid/library/features_sim.json` -> empty.
- `git status --porcelain -- sim | grep -v "sim/bench/sysid/"` -> no new lines.

## Out of scope
- Controllers, the real-log run (the supervisor does it), test/test2 rows.
