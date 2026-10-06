# Axis-priority mixer, low-throttle authority (airmode) and authority-aware adaptation (OPEN feature, not started)

Status: OPEN, recorded 2026-10-06 (PX4 course Q11/Q12). Nothing built, nothing measured. Measure first (step 0).
Do after the 2026-10-06 demo and after the pending branches (`ram-savings`, `o2-build`, `float-math`, `h0g-port`)
merge: they touch `TASK/StabilizerTask.c` and `API/mrac.c`.

## Problem (facts checked in the code on 2026-10-06)

- `Set_PWM_Motors` (`BSP/pwm.c:269`) clamps each motor alone to [`Motor_PWM_ZERO`, `Motor_PWM_MAX`] = [2000, 4000].
  No axis has priority. When one motor clips, the cut part carried a share of roll, pitch, yaw and thrust
  (its `g_mix` row, `API/controller.c`), so all four axes lose authority together. PX4's control allocator gives
  up yaw first and keeps roll/pitch (a yaw error turns the heading; a roll/pitch error tilts the thrust vector).
- Low throttle: with every motor near 2000, the side that must slow down for a roll/pitch correction cannot go
  below 2000, so only part of the correction is delivered. PX4 fixes this with Airmode (`MC_AIRMODE`): raise all
  motors together so the differential fits. This firmware has no equivalent.
- `Mix_SatDeficit` (`API/controller.c:66`) already computes the cut per axis (`u_def`). Its only consumer is the
  MRAC V2 saturation-aware leakage `mu_sat*|u_def|*Theta` (`API/mrac.c:647`); `mu_sat` defaults to 0 on all axes
  (`API/mrac.c:1012`), so it is OFF unless set over CMD 0x1D.
- `ENABLE_PSEUDO_CONTROL_HEDGING 1` is defined in `API/mrac.h:81` but no `.c` file reads it: dead macro, PCH is
  not implemented.
- How often a motor clips in flight, on which axis, and for how long: NOT measured.

## Proposed design

| Part | Change | Why |
|---|---|---|
| A. Axis-priority desaturation | After the mix: if a motor is outside [2000, 4000], shift the collective within a bound, then scale yaw down until all four fit, then (last resort) scale roll+pitch together | keep the drone level; give up heading first (PX4 order) |
| B. Low-throttle authority (airmode) | When airborne and a motor hits the 2000 floor, raise all four by the amount that lets the roll/pitch differential fit | keep attitude control in fast descents; costs a little descent rate |
| C. Authority-aware adaptation (operator idea) | Per-axis authority fraction `alpha = 1 - |u_def| / max(|u_cmd|, eps)` from the final motor commands; publish it; use it in MRAC (below) | adaptation must not learn from commands the motors could not deliver |

Part C uses of `alpha` (pick per axis, measure each):
1. Reference-model hedging (PCH): slow the reference model by the deficit so the tracking error contains only
   what the actuators could do. Implement it or delete the dead macro.
2. Learning-rate scheduling: scale `gamma` by `alpha` (learn at full rate with full authority, freeze near zero).
   V2 `mu_sat` leakage is the existing cousin of this.
3. Yaw control-effectiveness estimate (research, optional): yaw torque comes from rotor drag, weaker and more
   speed-dependent than thrust; estimate its effectiveness online (indirect adaptive / INDI style).
Do not add `alpha` as a regressor feature: it is a consequence of the controller's own output, not a plant
signal, and it shrinks the very gradient the law learns from.

## Dependencies that must keep working (check each)

1. `Mix_SatDeficit` and MRAC V2 `u_def`: after Part A the cut moves to yaw; recompute the deficit from the final
   motor commands. Host test `API/tests/test_mixer.c`.
2. `g_yaw_mix_dir` sign (`API/controller.c` comment, default -1): desaturation uses the same sign.
3. Motor map: `CONSTRAINT` comment in `Set_PWM_Motors` (`BSP/pwm.c:271`) and `pwm.h` channel mapping.
4. `ThrustEst_Step` reads `mymotor` after the mix, before `Set_PWM_Motors` (`TASK/StabilizerTask.c`): keep the order.
5. Ground and landing: Part B only while `flight_phase` is FLYING (not on the ground, not during
   `Land_ContactStep` contact), or the controller fights the ground and tips the drone. Motor-idle gate unchanged.
6. Altitude: a collective shift is a Z disturbance; check the Z rate loop and `mrac_state.z_rate.u_def`.
7. SIL (WP-31) and log replay (WP-34) call the mixer: update the host builds and tests.
8. Telemetry: per-axis `alpha` and a per-motor clip counter in a `g_tlm` group; regenerate `capability_manifest.json`.
9. Gates: `bash tools/check.sh` CHECK PASS, fw_lint, Keil 0 errors / 0 warnings; bench with props off (motor
   outputs only); then workflow C: hover, a yaw step, and a fast-descent rung.

## Plan

0. Measure: log `mymotor.motor1..4` and the four `u_def` over existing flight types; count clip events per axis.
   If motors never clip inside the normal envelope, keep this ticket low priority.
1. Part A + host test (no behaviour change while no motor clips).
2. Part C: `alpha` telemetry first, then PCH or `gamma` scheduling behind a default-OFF switch.
3. Part B last, gated by flight phase, with a fast-descent test rung.
