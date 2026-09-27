# Task night-w5: C89 reference implementations + cost table for the adaptive finalists (simulation only)

Read first: `sim/bench/CONTROLLER_API.md`, `sim/bench/fwpid.py`, `sim/bench/ctrl_l1.py`, `sim/bench/ctrl_mrac.py`,
`sim/bench/tierA.py`, `sim/adaptive_compare/sim_coupled.py` (MRAC class, import only), and READ-ONLY
`API/controller.c`, `API/controller.h`, `TASK/StabilizerTask.c` around line 1040 (the `Controller_Update(axis, u_nom)` hook).
Work only in `sim/bench/c_ref/` (new). Do NOT edit any other file: no firmware dirs, no `sim/adaptive_compare/`,
no existing `sim/bench/*.py`. Never run `bench.py tune`, `tune2.py` or `--split test`. No builds for the MCU, no flashing.

## Deliverable 1: C89 modules (float32, no malloc, no VLAs, declarations at block top, `/* */` comments)
- `c_ref/aug_l1.h/.c`: the per-axis adaptive augmentation of `tierA.L1` (= ctrl_l1.L1): state struct, params struct,
  `void aug_l1_init(AugL1 *s, const AugL1Params *p)`, `void aug_l1_reset(AugL1 *s)`,
  `float aug_l1_update(AugL1 *s, <the per-axis inputs the Python uses>, float u_nom)` returning the augmented u.
- `c_ref/aug_mrac_s6.h/.c`: same for `tierA.MRAC_S6` (online reference model + S6 regressor + normalized law +
  sigma-mod + norm clamp TH_MAX, exactly as the Python). One struct instance per axis (roll, pitch).
- Math must match the Python step for step (same discretisation, same order). Use `expf/sqrtf/tanhf` only if
  the Python uses the equivalent; count them.

## Deliverable 2: `c_ref/test_equiv.py` (equivalence test)
Run one bench row (`zigzag_0.5`, `nominal`, seed 7) through `bench.run_rows` for each class with (a) default
knobs and (b) the tuned knobs in `sim/bench/results/l1_tune.json` / `mrac_s6_tune.json` ('params').
Record the per-tick inputs the adaptive part sees and its output (monkeypatch/wrap `controller_update`,
do not edit the class). Compile the C with `gcc -std=c89 -pedantic -Wall -Wextra -O2 -shared -fPIC -lm`
(must be warning-free), load via ctypes, replay the inputs, report max |u_py - u_c| and max relative error
per axis. Pass: relative error <= 1e-3 of the peak |u| (float32 vs float64). If it fails, find out why and say so.

## Deliverable 3: `c_ref/COST.md` (<= 60 lines)
Table per controller, with FwPID alone (the existing cascade) as the baseline row:
state floats (bytes), param floats, per-tick ops per axis by class (add/sub, mul, div, sqrt, transcendental),
cycles at 168 MHz Cortex-M4F with FPU (assume VADD/VMUL/VMLA 1 cyc, VDIV/VSQRT 14 cyc, expf/tanhf ~100 cyc
newlib; state the assumption), time per 200 Hz tick in us and % of the 5 ms period for 2 axes.
Also per controller, 1-3 lines each: the stability argument (which Lyapunov/L1 result applies, what bounds
the parameters: sigma-mod, clamp, filter bandwidth), saturation behaviour (what the adaptation does when the
mixer clamps), PID fallback (which knob returns it to exactly FwPID, and how to freeze adaptation on the
ground / before liftoff; FLIGHT8 showed ground windup of U=650 before takeoff), and fit to
`Controller_Update(axis, u_nom)`: which extra inputs it needs beyond u_nom and where each already exists in
firmware (file:line, read-only grep), so the integration is a list of reads, not a redesign.

## Digest (<=30 lines, final message)
Files; gcc warnings (must be none); equivalence numbers per class and knob set; the COST table rows;
anything in the Python that could not be ported 1:1 and why. Commit your files on your branch. Touch nothing else.
