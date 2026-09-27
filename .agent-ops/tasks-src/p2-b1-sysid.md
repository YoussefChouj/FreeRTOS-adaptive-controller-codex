# Task p2-b1-sysid: numpy SINDy sysID library + multiscale H-scale protocol on the bench

<!-- Model: VPS agy:gemini-3.8-flash-high, fallback gemini-3.1-pro-high. -->

## Goal
`python -m sim.bench.sysid.hscale --sim` runs the pre-registered H-scale protocol end to end. It writes a verdict JSON and a per-axis x band x trajectory-class feature library. `python -m pytest sim/bench/sysid -q` passes.

## Context pointers
- **Read first:** `sim/bench/PREREG2.md`, section 1. It is the binding protocol: splits `id_fit` / `id_val` / `id_traj`, bands L < 0.5 Hz, M 0.5-4 Hz, H > 4 Hz, STLSQ with 5-fold CV threshold, 50-bootstrap ensemble, inclusion at least 0.6, Jaccard < 0.5, band-sum NRMSE at most 0.90 x global. Implement exactly that. Where it is silent, choose, and document the choice in the results markdown.
- Simulator:
  - `sim/bench/plant.py:run` returns the true-state log at 200 Hz; read up to the `return L` around line 242.
  - `sim/bench/scen.py`: `TUNE_FAMS`, `TRAJS`, `SPLITS`, `row_params`, `rows`, `build`.
  - `sim/bench/CONTROLLER_API.md`.
  - Plant parameters (inertia, arm, motor lag, drag, ground effect, battery) are in `plant.py` / `scen.py`.
- **Data-generating controller:** the retuned cascaded PID `pid_tuned2`. Its tuned parameters are in the ledger (`sim/bench/ledger.py`, `ledger.load(tag)`); find how `sim/bench/report.py` or `heldout.py` rebuilds it. If you cannot rebuild it within 20 minutes, use `sim/bench/fwpid.py` defaults and state that in the results.
- Real-flight path: `sim/bench/calib_logs.py` (`load`, `airborne`). The log files are NOT in this checkout. Build the `--real <glob>` code path and test it only on synthetic arrays; the supervisor runs it on the laptop.

## Deliverables (all new files, under `sim/bench/sysid/`)
1. `sindy.py` (numpy only; no pysindy, sklearn or scipy):
   - library matrix builder
   - STLSQ with a ridge option
   - SINDYc: control inputs as library columns
   - bootstrap ensemble returning inclusion probabilities and median coefficients
   - k-fold CV threshold selection
2. `bands.py`: numpy zero-phase FFT band split, with a smooth cosine transition of 0.1 decade and reflect padding. L + M + H must reconstruct the input (test it).
3. `features.py`: the named candidate feature library, built from the estimated states and commands in a sim log, or from real-log arrays.
   - Target: per-axis residual angular acceleration = true angular acceleration minus the nominal rigid-body model (J, commanded torque, gyroscopic term).
   - Candidates at minimum:
     - bias 1
     - body rates w and w_i*w_j (gyroscopic)
     - |w|w (rotational drag)
     - commanded torque, and the command passed through a first-order motor lag (tau from the plant)
     - collective thrust and thrust^2
     - body velocity v_b and |v|v_b (rotor drag / blade flapping)
     - height-based ground-effect term (e.g. 1/(1+(z/R)^2)) where height is available
     - sin/cos of roll and pitch
     - battery or voltage proxy where available
   - Every column has a human-readable name.
4. `data.py`: generates the `id_fit` (seeds 0-1), `id_val` (seeds 10-11) and `id_traj` (non-tune trajectories, seeds 10-11) datasets over `TUNE_FAMS` only.
   - Put a hard assert that no seed in {100, 101, 102, 200, 201, 202} and no family in `TEST_ONLY_FAMS` is ever generated.
   - Cache to `sim/bench/sysid/cache/` (gitignored: add a `.gitignore` there).
5. `hscale.py`, CLI `--sim` / `--real <glob>`:
   - Run the protocol per axis (roll, pitch, yaw) x band (L/M/H/global).
   - Fit per trajectory class (steps / circle / lem / zigzag) as well as pooled. A feature selected in every class for one axis x band is tagged `universal`.
   - Write `sim/bench/sysid/results/hscale_sim.json`: per axis x band, the selected features with inclusion probabilities and coefficients, the Jaccard matrix, NRMSE (band-sum vs global on `id_val` and `id_traj`), and a SUPPORTED/KILLED verdict with the two conditions evaluated separately.
   - Write `sim/bench/sysid/results/hscale_sim.md`: tables of at most 120 lines.
   - Write `sim/bench/sysid/library/features_sim.json`: the reusable feature library, keyed axis -> band -> traj_class -> [{name, coef, p_incl, universal}]. It must be loadable by future controllers through a small `load_library(path)` in `features.py`.
6. `test_sysid.py`:
   - STLSQ recovers a known sparse 3-state system with control input: all true terms have inclusion probability at least 0.9, spurious terms at most 0.2, coefficient error below 5% at low noise.
   - The band split reconstructs its input: max abs error below 1e-9 x signal range.
   - The split-guard assert fires on a test seed.
   - The `--real` path runs on a synthetic log dict.
7. `.agent-ops/out/p2-b1-sysid.md`, a digest of at most 30 lines: STATUS, files, test counts, the verdict numbers (Jaccard per axis, NRMSE ratio per axis), the universal features found, and the runtime.

## Constraints
- Do not modify any existing file in `sim/`. Import only. bench_v1 and its results must not change.
- Do not touch firmware (`API/ TASK/ BSP/ USER/ Global_file/ FreeRTOS/ stm32_lib/ OBJ/`). No hardware, no port 8081.
- The VPS has 2 vCPU and 3.7 GB. Batch rows through `plant.run` and keep peak RAM under 2 GB. If the full `id_fit` would exceed 15 minutes, subsample time (not rows) and document it.
- Foreground commands only. Do not commit (the runner collects your worktree). Do not commit the cache.
- Never report a number you did not produce with the committed script. Copy numbers into the digest from `hscale_sim.json`.

## Verification
- `python -m pytest sim/bench/sysid -q` -> all pass.
- `python -m sim.bench.sysid.hscale --sim` -> exit 0; writes the 3 result files.
- `git status --porcelain -- sim | grep -v "^?? sim/bench/sysid/"` -> empty (no existing file changed).

## Out of scope
- Controllers, CMA-ES tuning, test or test2 rows, real-log execution, editing PREREG2.md.
