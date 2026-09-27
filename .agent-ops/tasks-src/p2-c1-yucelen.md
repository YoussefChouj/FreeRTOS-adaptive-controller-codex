# Task p2-c1-yucelen: set-theoretic MRAC and performance-recovery MRAC on bench_v1 (simulation only)

<!-- Model: VPS agy:gemini-3.8-flash-high, fallback gemini-3.1-pro-high. -->

## Goal
New file `sim/bench/ctrl_p2_yucelen.py` with 3 controller classes, and wrappers appended in a new `sim/bench/tierP2.py`, so that `bench.run_rows(tierP2.<Name>, {}, rows, 7)` runs for each one. Each class passes a sanity file `sim/bench/sanity_p2_yucelen.py`.

## Read first
- `sim/bench/CONTROLLER_API.md` (binding)
- `sim/bench/ctrl_mrac.py` (`MRACBase`, `MRAC_S6`)
- `sim/bench/ctrl_mrac_b.py` (how the CRM / SatAware / Proj variants subclass `MRAC_S6`, and `get_params`)
- `sim/bench/tierA.py` (`fair()` keeps the full FwPID knob set; copy that pattern into `tierP2.py` by importing `fair` from `tierA`)
- `.agent-ops/research/nb_digest.md`: nb5 `barrier_gradient_modifier_log`, performance recovery / low-frequency learning, adaptive deadzone. Open the cited cells in `.agent-ops/research/nb/nb5_direct_mrac_ff_proj_v2.txt` with `sed -n` for the exact equations. These files may be missing on your machine; if so, use the lit review.
- `docs/research/phase2_lit_review.md` section 1 (Yucelen) and the Bench Candidates table.

## Controllers (all subclass `MRAC_S6` and reuse its regressor, reference model and FwPID stages)
1. `MRAC_ST`, set-theoretic MRAC. Multiply the adaptation rate by a generalised restricted potential of the tracking error, e.g. `phi(e) = 1 / (eps^2 - ||e||_P^2)` for `||e||_P < eps`, capped at `phi_max`. Knobs: `eps`, `phi_max`, `gamma`. Take the exact form from Arabi/Yucelen or nb5 and cite it in the docstring.
2. `MRAC_ST_Barrier`: `MRAC_ST` plus nb5's log-barrier gradient modifier, as nb5 defines it.
3. `MRAC_PR`, performance recovery / low-frequency learning (Yucelen & Calise). Add a low-pass-filtered copy of the adaptive term, `W_f' = -lambda (W_f - W)`, and use it in the control law or the reference-model modification exactly as the source does. Knobs: `lambda_f`, `kappa`, `gamma`.

For every class:
- at most 4 new knobs, with bounds that contain the defaults
- the defaults are your best hand design
- all math broadcasts over B
- no inverse larger than 3x3
- no allocation in `step`
- a docstring with the adaptation law and its source (paper URL or nb cell)

## Sanity file
Run the 4-row quick check from CONTROLLER_API for FwPID, MRAC_S6 and each new class. Print rmse, max |U| and whether any row diverged. Also run one extra row, `('steps','motor_loss',7)`, to check robustness. Row seed 7 is only for sanity checks.

## Deliverables
1. The files above.
2. `.agent-ops/out/p2-c1-yucelen.md`, a digest of at most 30 lines:
   - STATUS
   - each class's law in one line with its source
   - its knobs and bounds
   - the sanity table (copied from the script's output)
   - any deviation from the source
- The equal-budget tuning belongs to the supervisor. Do not run `bench.py tune`, `tune2.py`, `--split test` or test2 seeds.

## Constraints
- Do not edit any existing file. Only new files: `ctrl_p2_yucelen.py`, `tierP2.py`, `sanity_p2_yucelen.py`. If `tierP2.py` already exists, create `tierP2_c1.py` instead.
- No firmware, hardware or port 8081. Foreground commands only. Do not commit.

## Verification
- `python sim/bench/sanity_p2_yucelen.py` -> exit 0, no diverged rows on the 4 standard rows.
- `git status --porcelain -- sim | grep -v "^??"` -> empty.
