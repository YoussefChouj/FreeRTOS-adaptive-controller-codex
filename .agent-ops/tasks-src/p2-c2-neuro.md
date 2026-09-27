# Task p2-c2-neuro: neuroadaptive MRAC variants (RBF48/96, RBF + physics, DMRAC) on bench_v1 (simulation only)

<!-- Model: local WSL agy gemini-3.1-pro-high. -->

## Goal
New file `sim/bench/ctrl_p2_neuro.py` with 4 controller classes, wrapped in a new `sim/bench/tierP2_c2.py` (using `tierA.fair`). `bench.run_rows(tierP2_c2.<Name>, {}, rows, 7)` runs for each one, and each passes `sim/bench/sanity_p2_neuro.py`.

## Read first
- `sim/bench/CONTROLLER_API.md` (binding)
- `sim/bench/ctrl_mrac.py` (`MRACBase`, `MRAC_S6`, and `MRAC_RBF6/12/24`: how the RBF centres, widths and grid are built)
- `sim/bench/tierA.py` (`fair`)
- `docs/research/phase2_lit_review.md` section 6
- `.agent-ops/research/nb_digest.md` (2-D RBF grid regressors in nb4/nb5)

## Controllers
1. `MRAC_RBF48` and `MRAC_RBF96`: the same construction as `MRAC_RBF24`, with more centres. Pick the grid dimensions and state them. Scale the widths so neighbouring centres overlap about as much as in RBF24.
2. `MRAC_PhysRBF`: the regressor is `[S6 physics terms, RBF24 grid]`, with separate gains `gamma_phys` and `gamma_rbf`, and sigma-mod shared as in `MRAC_S6`.
3. `MRAC_Deep` (DMRAC, Joshi & Chowdhary):
   - Features: a 2-layer tanh MLP of width at most 16 on the same inputs as the RBF grid.
   - Outer layer: the MRAC law on the MLP features, every tick.
   - Inner layers: trained every `N_upd` ticks (default 200) by a few SGD steps on a ring buffer (at most 256 samples) of (input, estimated uncertainty) pairs. The uncertainty target is the outer-layer output at the time of storage, as in DMRAC.
   - Fixed seed initialisation, float32-friendly.
   - Knobs: `gamma`, `lr`, `N_upd`, `sigma`.

For every class:
- at most 4 new knobs, with bounds that contain the defaults
- the defaults are your best hand design
- all math broadcasts over B
- no allocation in `step`
- a docstring with the law and its source URL
Also report a per-tick flop estimate in each docstring (the Cortex-M4 budget is 5 ms).

## Sanity file
Run the 4-row quick check from CONTROLLER_API for FwPID, MRAC_RBF24 and each new class. Print rmse, max |U|, diverged yes/no, and wall seconds per row. Also run `('steps','motor_loss',7)`.

## Deliverables
1. The files above.
2. `.agent-ops/out/p2-c2-neuro.md`, a digest of at most 30 lines: STATUS, each class's law and source, its knobs and bounds, the sanity table copied from the output, the flop estimates, and any deviations.

## Constraints
- Only the 3 new files. Do not edit existing files.
- Do not run `bench.py tune`, `tune2.py`, `--split test` or test2 seeds; the supervisor tunes.
- No firmware, hardware or port 8081. Foreground commands only. Do not commit.

## Verification
- `python sim/bench/sanity_p2_neuro.py` -> exit 0, no divergence on the 4 standard rows.
- `git status --porcelain -- sim | grep -v "^??"` -> empty.
