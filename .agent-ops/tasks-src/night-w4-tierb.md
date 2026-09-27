# Task night-w4: Tier B MRAC variants for bench_v1 (simulation only)

Read first: `sim/bench/CONTROLLER_API.md` (binding interface), `sim/bench/fwpid.py`, `sim/bench/ctrl_mrac.py`
(the Tier A MRAC wrappers with the ONLINE reference model; build on them, do not edit them),
`sim/adaptive_compare/sim_coupled.py` (MRAC class; import, never edit). Python 3 + numpy, work only in `sim/bench/`.
Do NOT edit frozen files (CONTROLLER_API.md list), firmware dirs, `sim/adaptive_compare/`, `ctrl_mrac.py`, `prev_ref/`.
Never run `bench.py tune`, `tune2.py` or `--split test` (the supervisor does, with equal budgets).

## Deliverable 1: `sim/bench/ctrl_mrac_b.py` — 4 classes, each subclassing the S6 wrapper in ctrl_mrac.py
Same basis (S6), same reference model, same yaw PID. Each adds exactly one mechanism, per its paper:
- `MRAC_CRM`: closed-loop reference model, Gibson, Annaswamy, Lavretsky, "On adaptive control with
  closed-loop reference models", IEEE TAC 2013: the reference model state is pulled toward the plant,
  xm_dot = Am xm + Bm r + L (x - xm), L a knob (L=0 recovers S6).
- `MRAC_Composite`: composite adaptation, Lavretsky "Combined/composite MRAC", IEEE TAC 2009: the update adds a
  prediction-error term from a low-pass-filtered plant model (rate_dot_hat vs filtered gyro derivative);
  weight knob kc (kc=0 recovers S6). No history stack (that is concurrent learning; say if you add it as a
  labelled extra `MRAC_CL`, Chowdhary & Johnson 2010, fixed-size stack <=8 points, pre-allocated).
- `MRAC_SatAware`: input-saturation-aware MRAC, Lavretsky & Hovakimyan, "Stable adaptation in the presence of
  input constraints", Systems & Control Letters 2007 (positive mu-modification) or the Karason & Annaswamy 1994
  augmented error: the adaptive law sees the deficit between the commanded and the mixer-saturated moment.
  Compute the achieved moment from `obs['mot']` (the measured PWM of the last tick) through the mixer
  inverse; never from truth. Knob mu (mu=0 recovers S6).
- `MRAC_Proj`: parameter projection (Pomet & Praly 1992; Lavretsky & Wise 2013 ch. 11) replacing sigma-mod +
  norm clamp, with a smooth convex set ||theta|| <= th_max, tolerance eps. Optional: gain-scheduled gamma
  vs |rate ref| (say which).
PARAMS: inherit the S6 wrapper's + each class's own knob(s). The default of the new knob must make the class
equal to S6 (so the tuner starts from the same point); list the knobs.

## Deliverable 2: `sim/bench/sanity_mrac_b.py` (pass/fail printed)
- For each class: new knob = 0 reproduces MRAC_S6 on one 4-row check to < 1e-9 max abs difference in rmse.
- CRM: with L>0, the peak |x - xm| early in a steps row falls vs L=0 (the transient-shaping claim).
- Composite: the parameter trajectory is smoother (lower total variation) than S6 on a payload row.
- SatAware: on a saturating row (payload 'steps' seed 1), the parameter norm grows less than S6.
- Proj: the parameter norm never exceeds th_max(1+eps).

## Deliverable 3: smoke run
4-row check from CONTROLLER_API.md (seed 7) for FwPID, MRAC_S6 and the 4 classes; nothing may diverge that
FwPID does not. Put the numbers in the digest.

## Digest (<=30 lines, final message)
Files; per class: the paper equation implemented and any deviation (why); PARAMS; sanity numbers; smoke
numbers; C89 cost (float ops/step, floats of state). Commit your files on your branch. Touch nothing else.
