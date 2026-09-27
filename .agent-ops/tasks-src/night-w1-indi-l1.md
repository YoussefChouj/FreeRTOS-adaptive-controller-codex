# Task night-w1: INDI and L1 adaptive controllers for bench_v1 (simulation only)

Read first: `sim/bench/CONTROLLER_API.md` (the interface; binding), then `sim/bench/fwpid.py`, `sim/bench/plant.py` (constants only).
Python 3 + numpy. Work only in `sim/bench/`. Do NOT edit any frozen file listed in CONTROLLER_API.md,
anything in `API/ TASK/ BSP/ USER/ Global_file/ FreeRTOS/ stm32_lib/ OBJ/` or `sim/adaptive_compare/`.
Never run `bench.py tune` or `--split test` (the supervisor does, with equal budgets for all controllers).

## Deliverable 1: `sim/bench/ctrl_indi.py` — class `INDI(FwPID)`
Incremental nonlinear dynamic inversion of the inner loop, per Smeur, de Croon, Chu, "Adaptive Incremental
Nonlinear Dynamic Inversion for Attitude Control of Micro Air Vehicles", JGCD 2016:
- Keep FwPID's xy_loop, z loop and att_loop (outer loops). Replace rate_loop: the virtual control is the
  angular acceleration  nu = K_rate * (wd - gyro)  [dps^2].
- Angular acceleration estimate = derivative of the filtered gyro; filter the gyro derivative AND the
  applied actuator U with the SAME second-order low-pass (the paper's key synchronisation property).
- Increment: U = U_f + G^-1 (nu - wdot_f), with G = diag(B_RP, B_RP*J0[0]/J0[1], B_YAW in dps^2/U)
  (nominal constants only). Account for the ~15 ms output delay + 50 ms motor lag via the filter/delay
  on U_f (the paper models the actuator dynamics A(z) on the U path).
- Optional: the paper's LMS adaptation of G (keep it switchable with a param; default whichever is better
  on the 4-row smoke).
- PARAMS: inherit FwPID's outer-loop knobs you still use + INDI knobs (K_rate per axis group, filter
  cutoff, G scale, LMS rate if used). Max 14 total. Defaults must be a sane hand design.

## Deliverable 2: `sim/bench/ctrl_l1.py` — class `L1(FwPID)`
L1 adaptive augmentation of the rate loop, per Hovakimyan & Cao, "L1 Adaptive Control Theory" (SIAM 2010),
piecewise-constant adaptation law (Ch. 3/4 of the book; the variant used on quadrotors):
- State predictor per axis on the body rate: w_hat' = Am (w_hat - w) + b (u_pid + u_ad + sigma_hat),
  with b = the nominal G above.
- Piecewise-constant adaptation: sigma_hat = -b^-1 Phi(Ts)^-1 e^{Am Ts} (w_hat - w) with Ts = DT_C.
- u_ad = -C(s) sigma_hat, with C(s) a first-order low-pass (bandwidth = a knob).
- Output via `controller_update(o, u_nom, wd)` only: u_nom + u_ad (this is the firmware hook).
- PARAMS: FwPID's defaults + Am, C(s) bandwidth, maybe a u_ad clip. Max 14 total.

## Deliverable 3: sanity scripts (the paper's property, pass/fail printed)
- `sim/bench/sanity_indi.py`: on a hover row with a constant roll-moment disturbance, INDI rejects it with no
  steady-state error and no integrator (show the rate error goes to about 0 in <0.3 s). Also show that removing
  the filter synchronisation (filter only gyro, not U) degrades it.
- `sim/bench/sanity_l1.py`: increasing Am/the adaptation rate does not make the transient worse (L1's
  decoupling of adaptation and robustness); the C(s) bandwidth sets the robustness/performance trade.
You may create your own tiny test plant inside the sanity script (a 1-axis model) if the bench plant
cannot inject the needed disturbance; state which you used.

## Deliverable 4: smoke run
Run the 4-row check from CONTROLLER_API.md for FwPID, INDI and L1 (seed 7). Nothing may diverge that
FwPID does not. Put the numbers in the digest.

## Digest (≤30 lines, in your final message)
Files written; design choices vs the paper; PARAMS table; sanity pass/fail with numbers; smoke numbers;
C89 portability notes (float32 ops per step, state size); anything you could not do.
Commit your files on your branch with a clear message. Do not touch other files.
