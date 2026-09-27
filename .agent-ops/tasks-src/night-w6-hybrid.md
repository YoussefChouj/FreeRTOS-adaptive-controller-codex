# Task night-w6: axis-split hybrid controller (MRAC_RBF12 roll/pitch + L1 yaw), simulation only

Read first: `sim/bench/CONTROLLER_API.md`, `sim/bench/fwpid.py`, `sim/bench/tierA.py` (the `fair()` wrapper),
`sim/bench/ctrl_l1.py`, `sim/bench/ctrl_mrac.py`, `sim/bench/sanity_l1.py`, `sim/bench/sanity_mrac.py`.
Create only `sim/bench/ctrl_hybrid.py` and `sim/bench/sanity_hybrid.py`. Do NOT edit any other file
(no firmware dirs, no `sim/adaptive_compare/`, no existing `sim/bench/*.py`, no results/).
Never run `bench.py tune`, `tune2.py` or `--split test`.

## Deliverable 1: `ctrl_hybrid.py`
`class Hybrid_RBF12_L1Yaw(tierA.MRAC_RBF12)`:
- PARAMS = `tierA.MRAC_RBF12.PARAMS` plus every `tierA.L1.PARAMS` key that is not already in it (same tuples).
  If an L1-only key collides in name with an MRAC key but means something different, prefix it `l1y_` and map it.
- `__init__(B, params)`: fill defaults from PARAMS, init the MRAC_RBF12 part as usual, and build
  `self.l1 = tierA.L1(B, <FwPID knobs + L1 knobs>)` used ONLY for its adaptive yaw correction.
- `controller_update(o, u_nom, wd)`: `U = MRAC_RBF12 update` (roll/pitch augmented, yaw untouched);
  `Ul = self.l1.controller_update(o, u_nom, wd)`; `U[:, 2] = Ul[:, 2]`; return U. Axis order of u_nom is
  (roll, pitch, yaw) — verify in fwpid.py. If L1.controller_update reads any attribute its own cascade sets
  (e.g. `self.des`, filters updated in `step`), copy it from `self` before the call each tick and say so.
- The L1 roll/pitch estimates keep running (harmless) but their output is discarded; note this in a comment.

## Deliverable 2: `sanity_hybrid.py` (prints PASS/FAIL, < 3 min)
With the knobs in `results/pid_tuned_tune.json` ('params', defaults for the rest), 1 seed each:
(a) row (`hover`, `yaw_imb_hi`, seed 3) or the closest existing yaw-imbalance row: hybrid yaw error RMS within
    10% of tierA.L1's and < 0.8x tierA.MRAC_RBF12's;
(b) row (`zigzag_1.0`, `nominal`, 3): hybrid roll/pitch RMSE within 10% of tierA.MRAC_RBF12's;
(c) 4-row smoke (nominal, wind, payload, yaw_imb_hi on zigzag_0.5): all finite, print median RMSE per class.
Use `bench.run_rows`/`bench.metrics` or `plant.run` as the existing sanity scripts do. If a check fails, find
the cause (report it; do not tune knobs to pass).

## Digest (<=30 lines, final message)
PARAMS count and any renamed keys; what L1 state had to be synced (if any); the sanity numbers and PASS/FAIL;
wall time of one 4-row smoke. Commit your two files on your branch. Touch nothing else.
