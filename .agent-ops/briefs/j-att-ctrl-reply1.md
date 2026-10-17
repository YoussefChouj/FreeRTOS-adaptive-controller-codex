Yes, a wrapper, with this exact shape. StabilizerTask.c stays thin: one call per axis, no controller logic.

1. In `att_ctrl.c`: `float AttCtrl_Apply(uint8_t axis, float u_pid, const AttCtrlIn *in)`. It owns everything:
   kind/mode edge detection and reset, Step, the latch checks (and would_latch in SHADOW), the a_eff ramp, the
   blend, `Commit(u_applied)`, and the RLS update. It returns u_applied.
2. Bit-exactness: in OFF, return `u_pid` at once (no Step). In SHADOW, run Step + checks + `Commit(u_pid)` and
   return `u_pid` itself, not `(1-0)*u_pid + 0*u_new`. In ACTIVE with a_eff == 0.0f, also return `u_pid` itself.
3. Roll/pitch/yaw: in `Mix_Compute`, right before each `Controller_Update(axis, u_nom)`:
   `u_nom = AttCtrl_Apply(axis, u_nom, &in);`. Pitch: the model's u is `gyroyPID.U` BEFORE the sign flip, so apply
   the wrapper on that side of the flip; find the flip and cite file:line in a comment.
4. Z: call `AttCtrl_Apply(ATT_Z, Z_ratePID.U, &in)` right after `Z_ratePID` is computed, on z ticks only, and store
   the result where the mixer already reads the Z output, so it holds between z ticks with no new state.
5. CPU budget: `AttCtrl_TickBegin()` once at the top of the stabilizer tick zeroes a cycle accumulator; each Apply
   adds its DWT CYCCNT delta; if the total exceeds the budget row, latch every non-OFF axis (reason CPU). In the
   host harness the cycle counter is a stub (macro or function pointer) so the test can force an overrun.
6. Inputs: r = the angle (or height) setpoint the angle (or height) PID already uses, y = the attitude (or height)
   estimate, w = gyro (or climb rate), all in rad, rad/s, m, m/s; cite the source variable of each in a comment.
7. `AttCtrl_ResetAll()` from the existing disarm/landed path; it clears latches and a_eff.

Then finish the remaining work (VAR rows from 54, the `att_ctrl` stream group with its B/s, the uvprojx line, the
C and pytest tests) and open ONE PR against `overnight-2026-10-08` without waiting for another confirmation.
`python tools/host_tests.py` fully green; a failing test is a blocker. In the PR list each test and its result,
the RAM used, and any point where you followed the code instead of the brief.
