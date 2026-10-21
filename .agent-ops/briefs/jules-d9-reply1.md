Good findings, keep going. No more questions after this one; finish without waiting on me.

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
