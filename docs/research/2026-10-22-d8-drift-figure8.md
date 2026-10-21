# D8: why the drone drifted at high speed and did not recover (2026-10-22)

Question (user, 10-22): on some 10-21 flights the drone drifted a lot and never came back, mostly at high speed.
Flights: speed-ladder-0 / -1 / -2 (sessions 20261021-210612, -211231, -211803), flat Gerono figure-8 a60,
PID xy, camera fusion mode 3. Measured with a scratchpad script (10 s blocks of the session telemetry.csv:
`g_ext_pos.e_x/e_y` = camera minus estimate, `g_ext_pos.n_rej`, `g_ext_pos.stale`, `g_ekf_of_fallback`,
`s_ekf_of.rej_x/rej_y`). The tight vs wide figure-8 part comes later in this doc.

## Bottom line

At speed the optical-flow EKF health check trips and the firmware falls back to FIXED bias mode **for the rest of
the flight**. Camera fusion mode 3 applies a camera fix only while the EKF is the active estimator
(`ekf3 = g_ext_pos_on == 3 && g_of_bias_mode == OF_BIAS_EKF && s_ekf_of_inited`, `TASK/StabilizerTask.c:966`).
So the camera is switched off right when the drift starts. It keeps measuring (that is the growing camera-minus-estimate
error), but nothing applies it. Analogy: the satnav loses GPS in a tunnel and then never listens to GPS again, even
after you drive out.

## Evidence (measured)

| flight | OF EKF trip (`g_ekf_of_fallback` 0 -> 1) | camera minus estimate, median per 10 s block (cm) | after the trip |
|---|---|---|---|
| speed-ladder-0 | never | 2-3 on slow blocks, 6-16 on fast blocks (max 67), back to 2 at the end | recovers each time |
| speed-ladder-1 | 60-70 s block (fast lap) | 2-8 before, then 16, 12, 11 | stays 11-16, no recovery |
| speed-ladder-2 | 20-30 s block (first fast lap) | 2, 2, 5 up to the trip, then 32, 57, 46, 30, 63, 93, 83 | grows to 118 cm max |

- Cleanest proof, speed-ladder-1 after the trip: the error is 11-16 cm, inside the 40 cm gate
  (`EXT_POS_GATE_CM`), `stale` is 0.00 on the 80 s and 90 s blocks and no fix is gated (`n_rej` +0), so the camera
  fixes are arriving and passing the gate. The error still does not shrink: the fixes are scored but not applied.
  On speed-ladder-0 (no trip) the same size of error falls back to 2 cm within one slow block.
- On speed-ladder-2 the gated-fix count `n_rej` climbs by 48-57 per 10 s block on the blocks whose median error is
  above the 40 cm gate (40, 50 and 80 s), and `stale` is 0.74-1.00 there. The re-anchor path (`ext_pos_take`,
  3 agreeing gated fixes, |e| <= 150 cm) would catch this, but `ekf3` is false so it is never called.
- The code says this is the design: `Mode 3 (PROPOSED, needs the OF EKF, else shadow)` (`TASK/StabilizerTask.c:917`).
  The dispatch is `TASK/StabilizerTask.c:982-988`: mode 2 calls `ext_pos_step` with no estimator condition, mode 3
  needs `ekf3`.
- The optical-flow measurement rejections (`s_ekf_of.rej_y`) jump on the fast blocks of every rung: +42 and +59
  on rung 0 (no trip), +30 to +41 then +116 on rung 1 (+116 in the block where it tripped), +70 then +193 on rung 2.
- The trip rule is an absolute innovation threshold, `EKF_OF_HEALTH_THRESH 0.061 m/s` held for 0.5 s
  (`API/ekf_of.h:142`). It was set from **hover** logs (5 x the median innovation sd of 1.22 cm/s). The trip
  happened on the fast laps of rungs 1 and 2 and never on rung 0. Our reading is that innovations grow with speed
  (flow scale error x velocity), but innovation magnitude was not logged on these flights, so that link is
  UNVERIFIED.
- Speed-ladder-2 was then ended by the firmware LOW_V monitor (battery), not by the drift.

## Why the design does this, and the standard

The fallback was made for a diverged EKF in pure optical-flow flight: with no camera, FIXED bias is safer than a
diverged filter. With camera fusion on, the camera is the one sensor that can bound the drift, and the fallback
removes it. Standard practice (PX4 EKF2, Ardupilot EKF3, Kalman-filter texts on innovation gating) is to:

- gate each measurement with the normalised innovation squared (NIS) against a chi-square bound using the filter's
  own innovation covariance S, which grows when the vehicle moves fast or the state is uncertain;
- reset a diverged state from the absolute sensor (camera) rather than drop that sensor.

PX4/Ardupilot behaviour is quoted from memory, so UNVERIFIED until checked against their docs.

## Proposed fixes (flight-behaviour changes: operator decision, none applied)

| # | change | effect | risk |
|---|---|---|---|
| A (no build) | fly fast rungs with camera fusion mode 2 | mode 2 corrects `ano_of.earth_x/y` via `ext_pos_step` whatever `g_of_bias_mode` is, so a trip does not cut the camera | mode 2 has fewer flight hours than mode 3 |
| B (firmware) | in `Ext_Pos_Tick`, when `g_ext_pos_on == 3` but `ekf3` is false, run the mode-2 path (`ext_pos_step` + `ext_pos_spread`) | camera stays in the loop after a trip | small; needs a replay test that fails on the old code |
| C (firmware) | when the camera is fresh, re-init the OF EKF at the camera position and return to EKF mode instead of latching FIXED | removes the latch | larger change, needs the replay harness |
| D (firmware) | NIS gate with S instead of the fixed 0.061 m/s threshold | fewer false trips at speed | retune on the 10-21 logs first |

Suggested order: A today (no flash), B as the next firmware change with a replay test on speed-ladder-2, D after that.
