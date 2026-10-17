# Research brief res-sysid-sota: is our quadrotor identification state of the art, and what model do we need?

Read-only research. No edits, no commits, no builds. 160K token ceiling (GEMINI.md rule 10).
Output: `.agent-ops/out/argon-res-sysid-sota.md`, at most about 2000 words. Quote a source (URL, paper with
year, or repo file:line) for every claim. Mark anything you could not verify as UNVERIFIED. Prefer papers with
real flight data over simulation-only papers.

## What we already have (verify in the repo first, quote file:line)

- Firmware `API/sysid.c/.h`: a chirp or multisine added to ONE axis's rate setpoint (pitch/roll/yaw/z) at 200 Hz,
  with a safety FSM, a geofence and an RC dead-man. The raw dither is logged in the 0x03 ID frame as the instrument.
- Host `ground_station/autotune/` (frf.py, excitation.py, design.py, synth.py): an IV frequency response
  T = Phi_xd/Phi_rd, the plant P = T/(C(1-T)), then a fit of integrator + lag + delay (b, tau, delay).
  Also check `research/sim/cascade.py` (fit_rate_plant), `ground_station/analysis/motor_lag.py` and
  `refmodel_replay.py`.
- Where the model is used: gain design for the new ADRC/LQR/MPC attitude and height controllers (shadow mode),
  MRAC reference models (`API/mrac.c`), counterfactual replay of shadow controllers, and simulation.

## Questions

1. **Grade what we have.** Compare it against the state of the art for small multirotors: NASA (Morelli,
   orthogonal phase-optimized multisines, real-time frequency-domain ID), CIFER-style frequency-domain ID with
   coherence weighting, and the TU Delft / ETH / UZH quadrotor ID papers. What is missing or weak? Consider
   excitation design, single-axis vs simultaneous multi-axis, coherence, record length, and the fit method.
2. **Excitation.** Best signal for a 200 Hz indoor quad: Schroeder or orthogonal multisine vs chirp vs PRBS vs
   3-2-1-1. Give the band, amplitude (deg/s), duration and crest factor, and say whether several axes can be
   excited at once with orthogonal frequency sets.
3. **Nonlinear and gray-box models.** For each candidate, say what flight-data papers show it buys over a
   linear rate model and what it costs: thrust ~ omega^2 with a motor model, rotor drag / blade flapping, battery
   voltage, ground effect, NARX, SINDy, Koopman / DMD, Gaussian-process or NN residual on a physics core (e.g.
   NeuroBEM, "Neural-Fly", KNODE). Which fit 100-200 Hz logs of an IMU + ToF + optical flow + external camera,
   with no RPM sensor? Check the repo for RPM logging (`ground_station/analysis/` t19-rpm work).
4. **One model or several.** What do practitioners do: a low-order linear model per axis for control design
   plus a richer nonlinear model for simulation and replay? How do you validate each (hold-out flights,
   multi-step-ahead prediction error, closed-loop replay)?
5. **Recommendation.** A ranked list of three to five upgrades, each with what it changes in firmware and host
   code, the effort, and what tomorrow's lab flights must log to support it.

## Deliverable shape

1. Bottom line: five lines, starting with the grade of what we have.
2. One section per question.
3. Risks and unknowns.
4. Sources.
