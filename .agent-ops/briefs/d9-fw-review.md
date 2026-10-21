# D9 - firmware + ground-station review: bugs and no-behaviour-change improvements (READ-ONLY; one digest)

Repo: STM32F4 FreeRTOS quadrotor firmware (TASK/*.c, USER/, controller sources - find them with grep for
"LQR", "MPC", "ADRC", "MRAC") and the Python ground station (ground_station/: service 8081, fly, lab, analysis).
Do NOT edit or commit code. Write findings only.

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
Output: .agent-ops/out/d9-fw-review.md. Last line: `D9 DONE` or `D9 BLOCKED: <why>`.
