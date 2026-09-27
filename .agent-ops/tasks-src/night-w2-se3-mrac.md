# Task night-w2: SE(3)+ESO and MRAC S6/S10/RBF controllers for bench_v1 (simulation only)

Read first: `sim/bench/CONTROLLER_API.md` (the interface; binding), then `sim/bench/fwpid.py`, `sim/bench/plant.py`
(constants only), `sim/adaptive_compare/sim_coupled.py` (the MRAC designs; import, never edit).
Python 3 + numpy. Work only in `sim/bench/`. Do NOT edit any frozen file listed in CONTROLLER_API.md,
anything in `API/ TASK/ BSP/ USER/ Global_file/ FreeRTOS/ stm32_lib/ OBJ/` or `sim/adaptive_compare/`.
Never run `bench.py tune` or `--split test` (the supervisor does, with equal budgets for all controllers).

## Deliverable 1: `sim/bench/ctrl_se3.py` — class `SE3ESO(Controller)`
Geometric tracking per Lee, Leok, McClamroch, "Geometric tracking control of a quadrotor UAV on SE(3)",
CDC 2010, with an extended state observer (Han, ADRC 2009; linear ESO per Gao 2003 bandwidth tuning):
- Position: F = -kx ex - kv ev + m g e3 + m a_ref - m d_hat ; d_hat from a 3-axis linear ESO on the
  translational dynamics (states p, v, d; observer bandwidth wo; input = the commanded specific force).
- Attitude on SO(3): eR = 0.5 vee(Rd^T R - R^T Rd), eW; the desired moment -> firmware U via the nominal
  G (B_RP, B_RP*J0[0]/J0[1], B_YAW). A rate inner loop may stay (firmware-rate constraint: 200 Hz).
- Thrust -> PWM through the nominal A1/A2 curve and vbat (see CONTROLLER_API.md). Position at 100 Hz.
- R comes from the estimated rpy (ZYX), never truth. Feed-forward from ref v/a/yaw only (no preview needed).
- PARAMS ≤14 (kx, kv, kR, kW, wo, yaw gains, ...). Defaults = sane hand design.

## Deliverable 2: `sim/bench/ctrl_mrac.py` — classes `MRAC_S6, MRAC_S10, MRAC_RBF6, MRAC_RBF12, MRAC_RBF24`
All subclass `FwPID` and override only `controller_update(o, u_nom, wd)` = firmware Controller_Update hook.
- Reuse `sim_coupled.MRAC` (import it; same features, sigma-mod, norm clamp, U low-pass) for roll and pitch;
  yaw stays PID. Inputs per axis: pm_meas = gyro rate, phm_meas = rpy angle (convert units exactly as
  sim_coupled does: read its P_N/PHI_N/UN_N normalisation and units), U_pid = u_nom.
- sim_coupled precomputes its reference model offline; here it must run ONLINE: implement its reference
  model as a causal filter driven by the attitude Des (the firmware att_loop target) at 200 Hz, with the
  same dynamics as `sim_coupled.reference_model` (read it and match it; state what you matched).
- PARAMS: FwPID defaults (inherit) + `gamma` (log, 10^-1.5..10^2, default = the value sim_coupled's tuning
  picks for that variant if recorded in `results_coupled.json`, else 1.0). Keep the total ≤14 by
  dropping FwPID knobs only if needed (say which).

## Deliverable 3: sanity scripts (pass/fail printed)
- `sim/bench/sanity_se3.py`: (a) a large-angle recovery (e.g. start at 60 deg roll) converges, the Lee 2010
  almost-global property; (b) with a constant force disturbance the ESO removes the steady-state position error.
- `sim/bench/sanity_mrac.py`: the online reference model reproduces `sim_coupled.reference_model` on the same
  command (max abs diff < 1% of peak); with a payload row the MRAC parameter norm stays bounded and the
  tracking error falls vs gamma=0 (the Lyapunov property).
Build a tiny 1-axis or noise-free test inside the script if the bench cannot inject the case; state which.

## Deliverable 4: smoke run
Run the 4-row check from CONTROLLER_API.md for FwPID, SE3ESO and all 5 MRAC classes (seed 7). Nothing may
diverge that FwPID does not. Put the numbers in the digest.

## Digest (≤30 lines, in your final message)
Files written; design choices vs the papers/sim_coupled; PARAMS per class; sanity pass/fail with numbers;
smoke numbers; C89 portability notes (float32 ops per step, state size); anything you could not do.
Commit your files on your branch with a clear message. Do not touch other files.
