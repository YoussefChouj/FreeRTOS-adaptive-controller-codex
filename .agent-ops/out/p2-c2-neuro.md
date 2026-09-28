# p2-c2-neuro digest (worker: local WSL agy gemini-3.1-pro-high; supervisor-fixed and rewritten)

STATUS: DONE. `python sim/bench/sanity_p2_neuro.py` exits 0 with no divergence on any row. Not tuned yet.

## Classes (`sim/bench/ctrl_p2_neuro.py`, wrapped by `tierP2_c2.py` via `tierA.fair`)
- `CustomMRAC` copies the `sim_coupled.MRAC` math. For RBF24 it is bit-identical to `sim_coupled` (supervisor check), so any change in RBF24 → RBF48 → RBF96 comes from grid size alone.
- **MRAC_RBF48**: 8×6 grid on (pr, phr), with widths scaled to keep RBF24's overlap. Knob `gamma` (0.1, 10^-1.5 to 10^2, log). About 3k flop/tick.
- **MRAC_RBF96**: 12×8 grid. Same knob as RBF48. About 6k flop/tick.
- **MRAC_PhysRBF**: regressor [6 S6 physics terms, 24 RBF], each group with its own gain. Knobs `gamma_phys` (0.3) and `gamma_rbf` (0.1), both 10^-1.5 to 10^2, log. About 1.7k flop/tick.
- **MRAC_Deep**, after DMRAC (Joshi & Chowdhary 2019, arXiv:1909.08602):
  - Features: a 2→16→16 tanh MLP plus [un, pmr]. The outer layer is adapted by the MRAC law every tick.
  - Inner layers: 3 SGD steps every N_upd ticks on a 256-sample ring buffer, with labels = the outer-layer output at storage time.
  - Knobs: `gamma` (0.03, 1e-3 to 10), `lr` (0.1, 1e-4 to 10), `N_upd` (200, 10 to 1000, lin), `sigma` (0.01, 1e-4 to 1).
  - Cost: about 1.2k flop/tick plus about 1.2M flop per SGD burst (about 6k/tick amortised at N_upd = 200).

## Sanity (seed 7; rows: zigzag_0.5/nominal, circle_1.0/wind, steps/payload, lem_1.0/cog; then steps/motor_loss)
| ctrl | rmse (4 rows) | diverged | s/4 rows | motor_loss rmse |
|---|---|---|---|---|
| FwPID | 0.732 0.707 0.825 0.569 | none | 20.6 | 0.347 |
| MRAC_RBF24 | 0.726 0.705 0.849 0.541 | none | 29.4 | 0.364 |
| MRAC_RBF48 | 0.729 0.739 0.849 0.556 | none | 25.7 | 0.379 |
| MRAC_RBF96 | 0.728 0.758 0.872 0.596 | none | 25.5 | 0.371 |
| MRAC_PhysRBF | 0.725 0.676 0.871 0.531 | none | 28.4 | 0.406 |
| MRAC_Deep | 0.723 0.696 0.835 0.563 | none | 25.2 | 0.363 |
Every controller here runs at default knobs, so the table says nothing about performance. The ranking comes only from the equal-budget P1 tune.

## Deviations and findings
1. The worker's Deep had batch members sharing weights, and it averaged lr and N_upd over B. The supervisor fixed both: weights are per member, and the mask and lr are per B. Batch-independence test diff = 0.
2. At init scale 0.1 the Deep features were ≈ 0. The init is now 0.5/0.25, and gamma was lowered from 0.3 (diverged) to 0.03.
3. **The DMRAC inner SGD has no measurable effect at defaults**: rmse is identical for lr from 1e-12 to 3. The labels are the network's own outer output, so they differ from the current prediction only by the drift in Th. The tune will show whether lr matters.
4. The sanity script prints max|U| = 0 because it reads `d['mot']`; that is a cosmetic bug and has not been fixed. The worker's digest also had wrong flop counts and omitted the motor_loss row; this rewrite replaces it.
5. The RBF48/96 and PhysRBF docstrings cite the lit review, not a paper URL.
