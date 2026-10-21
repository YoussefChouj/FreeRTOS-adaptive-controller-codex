# D9 (Jules) - firmware + ground-station review: bugs and no-behaviour-change improvements (review doc + one PR)

Repo: STM32F4 FreeRTOS quadrotor firmware (TASK/*.c, USER/, controller sources - find them with grep for
"LQR", "MPC", "ADRC", "MRAC") and the Python ground station (ground_station/: service 8081, fly, lab, analysis).
Code fixes are allowed only under the deliverable rules below.

Look for, in this order:
1. Real bugs: wrong units/signs, uninitialised or never-reset state (integrators, ESO, MPC warm start, MRAC
   weights) at controller switch / takeoff / landing, races between FreeRTOS tasks on shared globals without a
   critical section, float/double mixups, array bounds, divide by zero, NaN propagation, saturation without
   anti-windup, dt assumptions vs the real task period.
2. Controller-switch path: how a selectable nominal (PID/LQR/MPC/ADRC) takes over from PID and how the MRAC layer
   wraps it; does the MRAC reference model / adaptation law depend on which nominal is active? cite file:line.
3. Ground station: unhandled exceptions, blocking calls on the ingest path, thread-safety, silent excepts.
4. Improvements that do NOT change flight behaviour (dead code, duplicated logic, clearer tables).

For every finding: file:line, a 1-2 line quote of the code, why it is wrong, a concrete failure scenario, severity
(high/med/low), and a suggested fix. No speculative style nits. Rank most severe first, max 30 findings.
Output: ONE new file docs/agent/reviews/2026-10-22-fw-review.md (create the folder), nothing else in the PR. Last line: `D9 DONE` or `D9 BLOCKED: <why>`.

Firmware sources: TASK/, API/, BSP-free parts only; USER/ is a Keil project (read, never edit).

## Focus areas and deliverable rules (these replace any question you would ask)

Extra focus areas, in priority order:
1. The xy outer loop: `API/xy_ctrl.c` and the code in `TASK/StabilizerTask.c` that picks the kind (VAR 44
   g_xy_ctrl_kind), calls XyCtrl_Track / XyCtrl_Step / XyCtrl_Commit, and switches between PID and the exp
   controllers (bumpless transfer, stale state after a kind change, the delay pipe after XyCtrl_SetDelay).
2. MRAC wrapped around a non-PID nominal (adrc/lqr/mpc + MRAC): is the MRAC reference model and the adaptation
   law consistent with the nominal controller that is active, or does it assume the PID loop?
3. The WiFi variable-write path: a write of VAR index 19 (cmd 0x2D) timed out and blocked the adrc-plus-mrac
   takeoff on 10-21. Trace firmware handler and `ground_station` sender for that index.
4. Thread safety in `ground_station/service` (shared state touched from the rx thread and HTTP handlers).

Rules for the deliverable:
- Write every finding (file:line, what goes wrong, a concrete trigger) to `docs/agent/reviews/2026-10-22-d9.md`.
- In the PR, fix only issues whose fix does not change flight behaviour (your #2 drain fix is fine).
- Your #1 (MRAC weights reset on dangerous stop) and anything else that changes flight behaviour: document only,
  with the proposed patch as a diff in the review doc. Do not edit `API/flight_fsm.c`.
- Every fix gets a test that fails on the old code.
- Open the PR against `nightly-1022`.

Nobody will answer questions on this task. Do not stop to ask: make the conservative choice (document only), note it in the review doc, and finish with the PR.
A previous session found that `API/flight_fsm.c` resets the MRAC weights on FLIGHT_EVENT_DANGEROUS_STOP / DISARM_REQ: document it with a proposed diff, do not change it.
