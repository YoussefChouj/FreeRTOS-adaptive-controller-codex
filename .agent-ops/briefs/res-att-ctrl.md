# Research brief res-att-ctrl: ADRC / LQR / MPC for the attitude + rate loops (shadow first)

Read-only research. No edits, no commits, no builds. 160K token ceiling (GEMINI.md rule 10).
Output: `.agent-ops/out/argon-res-att-ctrl.md`, at most about 2500 words. Quote a source (URL, paper, or repo
file:line) for every claim. Mark anything you could not verify as UNVERIFIED.

## Context (verify in the repo first)

- Quadrotor on an STM32F4 Cortex-M4F under FreeRTOS. Find the exact MCU and clock in the repo.
- Compiler: Keil ARMCC 5.06, C99 subset. No malloc, VLAs, recursion, or doubles in the hot path.
- Today the attitude and rate loops are a cascade PID per axis (roll, pitch, yaw: angle loop, then rate loop
  gyrox/gyroy/gyroz). Find the loop rates in StabilizerTask.c / main.c and quote them.
- `API/xy_ctrl.*` already has PID / ADRC / LQR / MPC for the x and y position+velocity loops, each replacing BOTH
  loops of the cascade. Reuse its patterns, and note its MPC formulation and cost.
- MRAC (`API/mrac.c`) adapts on top of the attitude/rate loops. NN (`API/nn_vel.*`, PR #8) shows the
  OFF / SHADOW / ACTIVE + cap + latch pattern we want to reuse.
- Plan: tonight we build ADRC, LQR and MPC for roll/pitch/yaw angle+rate, in SHADOW mode (computed and logged,
  never applied), plus an authority dial (0-100%) with auto-latch back to PID. Tomorrow: fly shadow, fit an
  attitude model from the logs, recompute gains on the host, write them at runtime (disarmed), fly again.

## Questions

1. **Adopt or build.** Open-source MCU implementations of quadrotor attitude/rate LADRC (LESO), LQR and MPC,
   for example TinyMPC on the Crazyflie STM32F405, PX4, Betaflight, or Crazyflie controller_*.c. For each: license
   (no GPL in firmware), language, solver, horizon, measured rate and CPU time on which MCU. Give a
   recommendation table.
2. **Structure.** Should each controller replace angle+rate as ONE loop per axis (for example LQR state
   [angle, rate, integral]) or keep a cascade? Cover yaw separately: it has weak control authority, wraps at
   +-180 deg, and has a yaw/heading coupling.
3. **Model and identification.** What is the minimal per-axis design model (rate dynamics with control
   effectiveness b, motor lag tau, delay)? Can it be fit from 100 Hz streamed logs, or should we use onboard RLS
   at loop rate? What are the closed-loop identification pitfalls under PID feedback (bias, and direct vs
   indirect methods)? What excitation do flight-test papers use (doublets, chirps, PRBS), at what amplitudes?
4. **Gains from the model.** LADRC tuning rules (omega_o vs omega_c, b0), LQR weights (Bryson's rule), MPC
   horizon and constraints. Estimate the per-call cycle cost on an M4F for each.
5. **Judging a shadow controller from logs.** How do we evaluate a controller whose output was never applied
   (counterfactual replay through the fitted model)? What are the known traps?
6. **Authority dial and blending.** What is the practice for limited-authority adaptive augmentation and for
   blending a new controller with alpha in [0,1]? Cover bumpless transfer and the latch criteria used in
   flight tests (for example NASA adaptive-control flight tests).
   Repo finding to check: `mrac.c:1019` scales the MRAC gradient by inj_alpha, and `mrac.c:1196` scales the
   output by it too. A constant 10% cap would therefore learn about alpha^2 = 100x slower. What does the
   literature do instead: cap the output or the projection bound, and keep the learning rate?
7. **CPU guard.** How do we measure per-call cycles (DWT CYCCNT) and auto-disable a shadow controller that would
   overrun the loop?

## Deliverable shape

1. Bottom line: five lines, starting with the adopt-or-build pick.
2. One section per question.
3. Risks and unknowns.
4. Sources.
