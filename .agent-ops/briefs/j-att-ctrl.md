# j-att-ctrl: ADRC / LQR / MPC for roll, pitch, yaw and height (shadow, dial, latches)

Branch: `overnight-2026-10-08`. Open ONE PR against it. Quality bar: flight-critical firmware. Read first:
`API/xy_ctrl.h`, `API/xy_ctrl.c`, `ground_station/analysis/xy_ctrl.py` (the template you mirror),
`API/controller.h/.c`, `TASK/StabilizerTask.c` (Att_Compute ~2205, Mix_Compute ~2246, the xy hooks ~2178),
`TASK/AutoflyTask.c` (VAR_WRITE allowlist `s_var_write`, row table ~458), `docs/firmware-table-pattern.md`,
`ground_station/livewatch/campaign_capture.py` (stream groups ~191-212), `tools/host_tests.py`.

## Goal

A new module `API/att_ctrl.h/.c` with four controller kinds (PID 0, ADRC 1, LQR 2, MPC 3) for four axes
(ROLL 0, PITCH 1, YAW 2, Z 3). Each kind replaces BOTH loops of that axis's cascade (angle+rate, or
height+climb rate) as ONE loop. Each axis has its own kind, mode and authority dial. With every mode at
OFF or SHADOW and `g_hov_ff_mode == 0` (the defaults) the motor outputs must be bit-identical to today.

## Design model (same structure on every axis)

`y' = w`, `w' = -a*w + b*m`, `m' = (u_d - m)/tau`, `u_d[k] = u[k - D]` (D ticks of pure delay).
For roll/pitch/yaw, y is the angle (rad) and w the gyro rate (rad/s). For Z, y is the height (m) and w the climb
rate (m/s). u is in the same units as the PID output it replaces (`Ctrler.gyroxPID.U`, `gyroyPID.U` before the
pitch sign flip, `gyrozPID.U`, `Z_ratePID.U`). Add an integral state on the tracking error for LQR and MPC.
Discretize with ZOH at the axis rate (attitude 200 Hz; Z at the rate `Z_ratePID` is computed, POS_LOOP_DIV).
Yaw: wrap the angle error to [-pi, pi).

## Kinds

- **PID (0):** passthrough, u_new = u_PID.
- **ADRC (1):** linear ADRC with a 2nd-order ESO on the measured rate (z1 ~ w, z2 = total disturbance), b0 = the
  model's effective gain, observer bandwidth wo, controller bandwidth wc: u = (wc^2*(r - y) - 2*wc*w + w_ff' - z2)/b0.
  The ESO is fed the delayed APPLIED command (from the Commit pipe), like xy_ctrl.
- **LQR (2):** state [integral, e, w, m] with the Smith predictor over D ticks (as xy_ctrl). Weights from Bryson
  maxima (e_max, w_max, i_max, u_max).
- **MPC (3):** condensed QP, horizon N (default 8, ATT_NMAX 10), box bounds on u, fixed-iteration FISTA (default
  15) with a warm start from the shifted plan, as in `xy_ctrl.c` mpc_solve. Z bounds are ASYMMETRIC:
  [u_min - u_hov, u_max - u_hov] from the real mixer/throttle clamps (find them, cite file:line in a comment).

## Runtime-tunable without a reflash (the important design choice)

Tomorrow we fit (b, a, tau, D) per axis from flight logs and write them over VAR_WRITE while disarmed. So the
writable parameters are the MODEL and the DESIGN KNOBS, not raw gain matrices:
per axis b, a, tau, D, wc, wo, the four Bryson maxima, MPC q and r. `AttCtrl_Rebuild(axis)` re-derives
everything onboard: the ZOH model, the ADRC gains (closed form), the LQR gain (fixed-iteration Riccati recursion,
with a convergence check; if it does not converge, keep the old gains and set an error flag), the MPC H, F and step
(1/L with a Gershgorin bound). Build into a staging copy, then publish with one atomic index swap, so a tick never
sees a half-written set. Rebuild runs only while disarmed (VAR_WRITE already enforces disarmed; assert it).
`ground_station/analysis/att_ctrl.py` is the Python reference: the same rebuild, the same arithmetic order, and it
generates `API/att_ctrl_gains.h` (defaults, `*_ROW` macro tables, `python -m ground_station.analysis.att_ctrl`)
plus golden vectors for the C parity test. Default model values are PROPOSED: derive them from
`ground_station/autotune` or `research/sim/cascade.py` defaults where they exist, mark each source in a comment.

## API (mirror XyCtrl)

`AttCtrl_Step(axis, in)` -> u_new without committing; `AttCtrl_Track(axis, in)` observer-only tick (ESO runs,
integral and plan cleared); `AttCtrl_Commit(axis, u_applied)`; `AttCtrl_Reset(axis)`; `AttCtrl_Rebuild(axis)`.
The input struct carries r (angle or height setpoint, from the existing PID Des), an optional rate feedforward
(the SysID rate dither `SysID_GetRateSetpoint(axis)`, 0 otherwise), y and w.

## Modes, dial, bumpless transfer

`g_att_mode[4]`: 0 OFF (not computed), 1 SHADOW (default: Step + Commit(u_PID); computed, logged, never applied),
2 ACTIVE. `g_att_alpha[4]` in [0,1]: u_applied = (1 - a_eff)*u_PID + a_eff*u_new, then Commit(u_applied).
a_eff ramps toward alpha at most 2.0 per second (PROPOSED) on entry to ACTIVE; a latch sets a_eff = 0 at once.
Kind or mode changes are latched at transition edges and reset the axis state (as `s_xyc_kind` does).
Reset all axes and clear latches on disarm and on the ground.

## Latches (back to PID, sticky until disarm)

`g_att_latch[4]` = first reason code (enum, documented): NaN/Inf in any state or output; |roll| or |pitch|
> 35 deg; |rate| > 300 deg/s; yaw error > 45 deg; |u_new - u_PID| > du_max[axis] for > 20 consecutive ticks;
`g_ctrl_pid_fallback_active`; CPU: DWT cycles of all AttCtrl work in one tick > 50400 (CYCCNT already runs,
see `API/mrac.c:24`). Z only: altitude not valid (`s_alt_valid_tick`), z < 0.15 m, LANDING state, vz < -0.5 m/s,
|e_z| > 0.25 m, u saturated > 20 ticks. All thresholds are PROPOSED: VAR_WRITE rows. In SHADOW the same checks
run and count `g_att_would_latch[4]` without changing anything.

## Hover feedforward (applies to the baseline PID too)

`g_hov_ff_mode` 0 = today (`HOVER_THR_FREE`, StabilizerTask.c ~2253), 1 = u_hov = (c0 + c1*(V0 - V_filt)) /
max(cosR*cosP, 0.5). V_filt = a 1st-order filtered battery voltage (find `real_voltage`, tau ~2 s PROPOSED).
Defaults c0 = HOVER_THR_FREE, c1 = 0. Bench mode keeps `HOVER_THR_BENCH`.

## Onboard RLS (logged only, never used by a controller)

Per axis an ARX(1) RLS on w[k+1] = p1*w[k] + p2*u_applied[k-D] + p3, forgetting 0.995, covariance trace clamp,
updates only while flying and not latched. Log p1..p3 per axis.

## Hook

In `Mix_Compute`, before each `Controller_Update(axis, u_nom)`, replace u_nom by u_applied for that axis.
The Z channel runs where `Z_ratePID` is computed and holds its output between z ticks. Keep MRAC, NN and the
xy hooks unchanged.

## VAR_WRITE and streaming

New `VAR_WRITE_ROW`s from id 54 upward, contiguous, one table: kinds, modes, alphas, hover ff, per-axis model and
knobs, thresholds. Each float row has a safe [min, max]. A write to a model or knob row triggers
`AttCtrl_Rebuild`. Add an `att_ctrl` group to campaign_capture.py: per axis u_new, a_eff, latch, would_latch,
ESO z2, RLS p1..p3, plus the max cycle count and u_hov. Price it with the existing planner and state its B/s.

## Tests (must pass; list each with its result in the PR)

`API/tests/test_att_ctrl.c` (picked up by `python tools/host_tests.py`): PID passthrough bit-exact; OFF/SHADOW
leave the output bit-exact; each kind stabilizes the discrete model in a closed-loop sim (settle and overshoot
bounds you state); every latch reason fires and pins a_eff = 0; the alpha ramp rate; NaN input -> latch;
Riccati non-convergence keeps the old gains; Rebuild parity vs the Python golden vectors (1e-5 relative).
`ground_station/analysis/tests/test_att_ctrl.py`: the LQR matches `scipy.linalg.solve_discrete_are`; the generated
header is up to date. Compile the harness with gcc `-std=c99 -Wall -Wextra -Werror -Wdouble-promotion -Wvla
-Wdeclaration-after-statement`.

## Rules

ARMCC 5.06 C99 subset: locals at the top of a block, float literals with `f`, no double, malloc, VLA or recursion.
Add `att_ctrl.c` to `USER/JX_FLY.uvprojx` next to `xy_ctrl.c`; do not touch any other USER/ file. RAM for the
module <= 6 KB (state it). CRLF line endings in .c/.h/.py, like the existing files. Do not change MRAC, sysid,
subscribe or the stream frames. If a spec point conflicts with the code, follow the code, say so in the PR.
