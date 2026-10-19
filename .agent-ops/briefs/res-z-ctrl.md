# Research brief res-z-ctrl: ADRC / LQR / MPC for the height loops (shadow first)

Read-only research. No edits, no commits, no builds. 160K token ceiling (GEMINI.md rule 10).
Output: `.agent-ops/out/argon-res-z-ctrl.md`, at most about 1500 words. Quote a source (URL, paper, or repo
file:line) for every claim. Mark anything you could not verify as UNVERIFIED.
Companion brief: `.agent-ops/briefs/res-att-ctrl.md` (same plan for roll/pitch/yaw). Do not repeat its answers.

## Context (verify in the repo first)

- Height today: cascade PID, height (m) -> climb rate -> thrust, run every 2nd tick (`TASK/StabilizerTask.c`,
  POS_LOOP_DIV 2, about line 70). ToF height, LAND is rate mode (land_profile.h). `thrust_estimators.h` exists:
  say what it estimates. MRAC may adapt on z too: check `API/mrac.c`.
- The operator reports the vertical axis is nonlinear: the drone climbs faster than it drops.
- Plan: ADRC, LQR and MPC for height + climb rate in SHADOW (computed, logged, never applied), authority dial
  0-100% with auto-latch to PID, host fit of the model from logs, runtime gain writes while disarmed.

## Questions

1. **Why up and down differ.** Thrust ~ motor speed squared, min-throttle floor, battery sag, ground effect below
   about 2 rotor diameters, descent through own wake (vortex ring state, at what descent rate for a small quad).
   Which matter at our heights and speeds (find the limits in the repo)?
2. **Design model.** Minimal z model for control design: hover throttle (varies with battery), effectiveness b,
   motor lag, asymmetric thrust bounds. Linearize about hover, or use a feedback-linearized thrust (divide by
   cos(roll)cos(pitch), what does the repo do)?
3. **Which controller handles the asymmetry.** MPC with asymmetric input bounds, ADRC lumping it into the total
   disturbance, LQR plus feedforward hover throttle. Cite quadrotor altitude ADRC / MPC papers with flight data.
4. **Gains and ID.** How to fit hover throttle and b from 100 Hz logs under PID feedback; excitation steps
   (climb/descent steps, amplitudes) that stay safe indoors near the ground.
5. **Latches.** Which z-specific limits should latch back to PID (descent rate, height floor, thrust saturation)?

## Deliverable shape

1. Bottom line: five lines.
2. One section per question.
3. Risks and unknowns.
4. Sources.
