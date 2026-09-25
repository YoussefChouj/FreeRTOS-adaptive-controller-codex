# F2b — EKF x/y velocity into the control path + 3 bias modes (firmware, tier-0, authorized) — REWORK

A previous attempt (branch worker/f2) was rejected. Do NOT cherry-pick it; start from your base (main) and write a
clean minimal diff. You cannot build (no Keil) and there is no drone. The supervisor builds, flashes and measures.
Operator authorized EKF in the control path (2026-09-26); comments saying "shadow mode only" are superseded.

## Facts already established (verify, do not re-derive at length)
- `s_ekf` (Ekf9_t, static, TASK/send_data.c:60) is 9-state [v_body(3) m/s, b_a(3), b_g(3)], stepped inside
  `Send_Groundstation_Telemetry_UART4` (~100 Hz, measured dt): Predict (~line 733), OF update with debiased
  `(of2_dx_fix - s_of_bias_x) * OF_LSB_MPS` (OF_LSB_MPS = 0.01), Z-rate update with `ano_of.of2_h_f2_v`.
- Controller velocity loops: `TASK/StabilizerTask.c:461-462` feed `Ctrler.locxsPID.FB/locysPID.FB` from
  `ano_of.of2_dy/of2_dx` (raw OF velocity, cm/s units). So EKF v_body[0..1] * 100 is unit-compatible.
- `g_motor_idle_enabled` (API/flight_fsm.h) now exists: ARMED && !g_motor_idle_enabled = motors at PWM 2000.
- "Flying" for gating = `FlightFSM_GetState() == FLIGHT_STATE_ARMED && g_motor_idle_enabled`.
  "Stationary/ground" = DISARMED, or ARMED && !g_motor_idle_enabled.

## Rejected-attempt defects you must avoid
1. Declarations mid-block (C89 violation). All declarations at block top. Compile tests with
   `gcc -std=c89 -pedantic -Wall -Werror`.
2. `isfinite`/`isnan` are C99: use `(v == v) && (v < LIM) && (v > -LIM)`.
3. Q/R overwritten on every telemetry call: set them at init and on mode apply only.
4. `Ekf9_Init` reachable every tick (reset loop): init once; re-init only on an explicit ground-only request.
5. Arbitrary thresholds (P[0] < 10): every threshold is a named `#define` with a one-line justification.
6. Gating on `DroneStatus.ARM_Status`: use the FlightFSM + g_motor_idle_enabled definitions above.
7. Mode/switch changes accepted while flying: store a *requested* value, apply it only on ground.
8. Controller reading `s_ekf` across tasks: keep `s_ekf` static. After each EKF step publish
   `volatile float g_ekf_vx_cms, g_ekf_vy_cms;` and `volatile uint8_t g_ekf_healthy;` from send_data.c;
   StabilizerTask reads only those.
9. No junk: no `patch_*.py`, no compiled binaries, no unrelated registry reformatting.
10. Z stays legacy: do NOT switch `Z_ratePID.FB`. Units match (of2_h is metres, StabilizerTask.c:500, so
    of2_h_f2_v is m/s), but of2_h_f2_v is world-vertical (tilt-compensated) while EKF x[2] is body-z, and
    Ekf9_UpdateZRate feeds the world value into the body state. Report this frame mismatch (severity at tilt) in the
    digest; do not fix it.

## Do
A. Investigate (digest, quote file:line): how ekf.c handles bias states (Q rows, whether gain rows touch b_a/b_g);
   the past EMA mean-pull bug in x/y (`git log -S ema -S EMA --all`, `git log --grep -i -E "ema|drift|mean"`,
   optical-flow and position code): commit(s), cause, fix, and whether any similar pull-to-mean / bias-absorbs-motion
   pattern still exists (e.g. s_of_bias_x/y update logic: when does it adapt, can it absorb real slow motion?).
B. Implement:
   1. `volatile uint8_t g_ekf_bias_mode` (applied) + `g_ekf_bias_mode_req` (requested), default 0.
      0 FIXED_AT_BOOT: bias states frozen after the existing cold calibration (zero process noise and zero gain rows
      for bias states, or whatever ekf.c supports with the least code). 1 ONLINE: bias states update. 2 GATED:
      online only while ground/stationary, frozen otherwise. Use what ekf.c supports; do not invent a new filter.
   2. `volatile uint8_t g_ekf_ctrl_enable` (applied) + `_req`, default 0. When 1 and `g_ekf_healthy`:
      locxs/locysPID.FB use g_ekf_vx_cms/vy_cms in place of of2_dx/of2_dy (same rotation expression). Else legacy.
   3. Health: after each step check x[0..2] finite and bounded, P[0],P[4] finite and bounded, and NIS if ekf.c has
      it. On failure: g_ekf_healthy = 0 (latched until ground re-init request), `volatile uint32_t g_ekf_fallback_count++`.
   4. Ground-station exposure: if the CMD 0x19 param path (grep send_data.c and ground_station for 0x19 / param table)
      can add named params with bounds cheaply, add ekf_bias_mode [0..2] and ekf_ctrl_enable [0..1] writing the *_req
      globals; regenerate `docs/dashboard-platform/capability_manifest.json` with
      `PYTHONUTF8=1 python -m ground_station.platform.capability_manifest`. Otherwise say DWARF writes suffice.
   5. Extend the existing livewatch `group:ekf` (find where groups live: `grep -rn "ekf" ground_station/livewatch/*.yaml`)
      with the new globals + g_ekf_vx_cms/vy_cms next to ano_of.of2_dx/of2_dy.
C. Tests: `tests/firmware_host/test_ekf_gate.c` (+ minimal stubs, follow tests/firmware_host/test_idle_decouple.c style):
   req applied only on ground; NaN -> fallback latched + counter; ctrl source selects legacy when unhealthy.
   Python tests for any host change. Run pytest for touched dirs.

## Deliverable
Commit on your branch. Digest `.agent-ops/out/f2b.md` under 90 lines: Part A findings with quotes, per-file diff
summary, exact DWARF names, gcc + pytest summary lines, and a grounded test procedure (what to log, how long,
decision metric for best bias mode, e.g. EKF v_body drift and b_g stability over 120 s disarmed, per mode).
