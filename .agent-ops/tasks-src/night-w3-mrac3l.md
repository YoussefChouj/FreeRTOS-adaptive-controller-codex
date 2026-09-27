# Task night-w3: 3-layer frequency-routed MRAC for bench_v1, with ablations (simulation only)

Read first: `sim/bench/CONTROLLER_API.md` (binding interface), `sim/bench/fwpid.py`,
`sim/adaptive_compare/sim_coupled.py` (MRAC class: features, sigma-mod, norm clamp, U low-pass),
`API/mrac.h` (the basis that flies today; read only), and the reference ideas in `sim/bench/prev_ref/`
(README there; PREV code is unverified — reuse ideas, not trust). Python 3 + numpy, work only in `sim/bench/`.
Do NOT edit frozen files (CONTROLLER_API.md list), firmware dirs, `sim/adaptive_compare/`, `prev_ref/`.
Never run `bench.py tune`, `tune2.py` or `--split test` (the supervisor does, with equal budgets).

## The idea (per axis roll/pitch; yaw stays PID) — on top of FwPID via `controller_update(o, u_nom, wd)`
- Layer 1, physics basis: regressors from state, command and an ONLINE reference model. Start from
  sim_coupled S6/S10 features. Reference model: physics-based, driven through the PID gains = simulate
  the nominal closed loop (att PID -> rate PID -> nominal G=B_RP / B_RP*J0[0]/J0[1] -> 50 ms motor lag)
  from the attitude Des at 200 Hz. Error s = (rate - rate_m) + lam (angle - angle_m).
- Layer 2, reactive spectral: band energies of MEASURED signals (e.g. s, gyro) from a small IIR band-pass
  bank (start with 4 bands, e.g. <0.7, 0.7-2, 2-5, 5-12 Hz), energy = low-passed square, normalised.
- Layer 3, predictive spectral: the same bank on what the reference model EXPECTS on the upcoming path:
  run it on the onboard generator's preview (obs['preview'](n), n up to a horizon knob), e.g. the
  preview acceleration pushed through the reference model / tilt mapping. No truth, no disturbance values.
- Routing: gate g_b = softmax(log-energies / T) smoothed by a 1st-order IIR (tau knob). Each Layer-1
  feature i has a band affinity; its adaptation rate is gamma * (sum_b g_b A_bi) (a time-varying diagonal
  Gamma(t) > 0, floor eps). Alternative allowed if clearly better: filter features into bands and gate the
  band copies. Keep sigma-mod + norm clamp (or a convex gate instead of the clamp) for boundedness;
  cite prev_ref/time-varying-adaptation-gain-mrac.md / stability-time-varying-basis.md for the argument.

## Deliverable 1: `sim/bench/ctrl_mrac3l.py`
Base class `MRAC3L(FwPID)` with a class attribute `MODE`, and 4 subclasses sharing IDENTICAL PARAMS:
`MRAC3L_Unrouted` (constant equal gates = same MRAC, same basis, same ref model: the fair control),
`MRAC3L_Reactive` (Layer 2 only), `MRAC3L_Predictive` (Layer 3 only), `MRAC3L_Both` (fused: product or
mean of the two gate vectors, your choice, state it). PARAMS = FwPID's (inherit) + own knobs (gamma,
lam, T, tau, horizon, sigma, ...); list which knobs are dead in which mode.
Pre-allocate all state in __init__; everything (B,)-broadcast; C89-portable (no FFT, no matrix inverse).

## Deliverable 2: `sim/bench/sanity_mrac3l.py` (print each yardstick number and pass/fail)
(a) The gate finds known tones: feed 1 Hz + 4 Hz sinusoids (and single tones) to the bank/softmax;
    argmax gate lands in the right band within 1 s.
(b) Reference model vs plant on a nominal noise-free row (use the bench plant log truth for analysis only):
    rate RMSE as % of the plant rate RMS (target <=10%) and lag via cross-correlation (target <=50 ms).
(c) Boundedness: 20 s payload row, parameter norm bounded for all 4 modes.
(d) Predictive lead: on zigzag_1.0 the Layer-3 gate shifts band BEFORE the corner (report lead in ms).

## Deliverable 3: smoke run
4-row check from CONTROLLER_API.md (seed 7) for FwPID + the 4 modes; also zigzag_0.5 and zigzag_1.0
nominal xtrack (bench.metrics 'xtrack'). Nothing may diverge that FwPID does not.

## Digest (<=30 lines, final message)
Files; design choices and what you changed from the prev_ref sketch and why; PARAMS table and dead knobs
per mode; sanity numbers; smoke + xtrack numbers for all modes; C89 cost (ops/step, floats of state).
Commit your files on your branch with a clear message. Touch nothing else.
