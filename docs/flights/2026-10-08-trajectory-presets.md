# 2026-10-08 trajectory presets from the Keil watch window (step, zigzag, circle, figure-8)

Code: `TASK/AutoflyTask.c` (block "Keil trajectory presets"). Not built or flown yet: every number below is
PROPOSED. Each preset flies out from the hover point, flies back to it, holds it, and gives the sticks back, so
`traj_go = 1` can repeat it without landing. One preset is at most 25 s in total (move + return + hold) so motor 2
gets a rest between runs.

## 0. Build and flash

Keil: open the project, F7 (build), then Download. Expect 0 errors. If ARMCC complains about `floorf`
(`AutoflyTask.c`, zigzag sine), replace `p -= floorf(p);` with `p -= (float)(int)p;` (p is never negative there).

## 1. Watch window

| variable | write / read | meaning |
|---|---|---|
| `traj_id` | write | 1 step, 2 zigzag, 3 circle, 4 figure-8 (boot default 4 = the standard test path) |
| `traj_go` | write 1 | start `traj_id` with the current `traj_p`; the firmware clears it |
| `traj_stop` | write 1 | end the move now and fly back (status 4) |
| `traj_fence_off` | write | 0 fence on (x/y/z checks); 1 fence off (x/y and ceiling bypassed, trajectory may leave the box; the 0.4 m floor still applies) |
| `traj_status` | read | 1 running, 2 done and back, 3 aborted (stick takeover, landing, disarm), 4 stopped and back |
| | | refused, nothing moved: `0xE0` not flying, `0xE1` busy (a GS path or GS authority), `0xE2` bad `traj_id`, `0xEE` a field out of range or too fast, `0xEF` the shape leaves the soft fence |
| `traj_id_seen` | read | `traj_id` as the firmware read it at the last `traj_go`; if `traj_status` is `0xE2`, this shows why |
| `traj_phase` | read | 0 idle, 1 move, 2 return, 3 hold at the start point |
| `traj_active`, `traj_t` | read | running id, seconds since `traj_go` |
| `traj_home_x/y/z/yaw` | read | the start point (cm, cm, m, deg), taken from the setpoint at `traj_go` |

## 2. `traj_p` (edit any field, it is read at `traj_go`)

| field | default | range | meaning |
|---|---|---|---|
| `profile` | 1 | 0, 1 | 0 constant speed with hard corners and hard steps; 1 smooth: cosine ramps of `ramp_s` |
| `axis` | 0 | 0 x, 1 y, 2 z | step and zigzag axis |
| `zz_shape` | 0 | 0, 1 | zigzag 0 triangle, 1 sine |
| `f8_type` | 1 | 0, 1 | figure-8 0 Bernoulli (lies along x), 1 Gerono (lies along y) |
| `move_s` | 20 | >= 5 | preset time, s |
| `ret_s` | 3 | 3-10 | fly back to the start point, s |
| `hold_s` | 2 | 0-5 | hold the start point, s |
| `ramp_s` | 2 | 0.5 to `move_s`/4 | profile 1 ramp, s |
| `step_cm` | 40 | ±100 smooth, ±50 hard, z ±40 | out for `move_s`/2, then back |
| `zz_amp_cm`, `zz_hz` | 30, 0.2 | up to 80 (z 40), up to 1 Hz | zigzag amplitude and frequency |
| `circ_r_cm`, `circ_laps` | 40, 1 | 10-80, up to 3 | circle; the start point is on the circle, centre at -x |
| `f8_a_cm`, `f8_laps` | 70, 2 | 10-80, up to 3 | figure-8 size and laps (Gerono: any half lap ends at the start point) |
| `f8_tilt_deg` | 0 | ±60, and \|z\| ≤ 40 cm | figure-8 plane tilted about x: the y lobes climb and sink, z = y·sin(tilt), y shrinks by cos(tilt). Gerono 70: up to 34°; 30° gives y ±61 cm, z ±35 cm |
| `yaw_follow` | 1 | 0, 1 | circle and figure-8: 1 the nose faces the direction of travel, 0 hold `traj_home_yaw` |
| `yaw_rate_dps` | 90 | 10-180 | yaw follow slew limit, deg/s; turns sharper than this lag |
| `yaw_nose_deg` | 90 | ±180 | where the nose points in the loc x/y frame at yaw 0 (PROPOSED +90; see the nose check in section 3) |

Refused at `traj_go` (status `0xEE`): `move_s + ret_s + hold_s > 25`, a peak reference speed above 80 cm/s, or
a return faster than 80 cm/s. Peak speeds with the defaults (computed): step 31, zigzag 24, circle 14,
figure-8 69 cm/s (Gerono 70 cm, 2 laps, x ±35 cm, y ±70 cm: the standard test path, 2026-10-09). Refused with `0xEF`: any part of the shape outside the soft fence (|x| 1.3 m, |y| 1.7 m, z 0.4-1.4 m),
unless `traj_fence_off = 1` (then only the range/peak-speed checks apply). The floor (start height + lowest z
≥ 0.4 m, else `0xEF`) holds even with the fence off. Tilt 30 with Gerono 70: hover at 0.75-1.05 m before `traj_go`.
Also `0xEE`: tilt outside ±60 or |z| over 40 cm, `yaw_rate_dps` outside 10-180, or `yaw_follow = 1` with
`ret_s + hold_s` shorter than 180 / `yaw_rate_dps` (the nose must be able to swing back before the end).

Speed ladder (Gerono 70, `move_s` 20, `ramp_s` 2; computed 2026-10-10, tilt keeps the speed):

| `f8_laps` | peak speed | peak heading rate flat / tilt 30 | nose with `yaw_rate_dps` 90 |
|---|---|---|---|
| 1 | 35 cm/s | 64 / 71 deg/s | follows |
| 1.5 | 52 cm/s | 95 / 107 deg/s | lags a little at the lobe tips |
| 2 | 69 cm/s | 127 / 143 deg/s | lags at the lobe tips; set 150 to follow |
The circle and the Bernoulli figure-8 reach 2 × size toward -x, so start them at x >= -0.5 m (circle 40) or
x >= -0.3 m (figure-8 50).

## 3. Flight sequence (load on, `kp_id = 5`, `vp_id = 6`, as in the load runbook)

| # | step | check |
|---|---|---|
| 1 | Take off, hover still, ch8 as wanted (off = PID, on = MRAC). Hands off the sticks | |
| 2 | Edit `traj_p`, set `traj_id` (type the number 1-4, not the character '1') | the watch shows `traj_id` 1-4 before you write `traj_go` |
| 3 | `traj_go = 1` | `traj_status == 1`, `traj_phase` 1 → 2 → 3 |
| 4 | Wait for `traj_status == 2` (back at the start point, sticks yours again) | |
| 5 | Repeat (`traj_go = 1`), next preset, or land | |

Per preset: once with ch8 off (PID), once with ch8 on (MRAC), same `traj_p`. Do not switch ch8 during a preset.
Watch motor 2: rest in hover or land between runs if it runs hot.

Stop early: `traj_stop = 1` flies back and holds (status 4). Take over: move any stick; the preset ends at once
with no return (status 3) and you fly. Landing or disarming also ends it. Do not hold the ch4 + ch5 AFLY key
combination during a preset. Yaw: steps and zigzags hold `traj_home_yaw`; circles and figure-8s with
`yaw_follow = 1` turn the nose along the path during the move, then swing back to `traj_home_yaw` for the
return and hold. The Gerono path leaves at 45° in x/y, so expect a yaw turn as it starts (slewed).

Nose check (first circle or figure-8 of the day, `f8_laps` 1, no load): the nose must lead. Tail leads: set
`yaw_nose_deg = -90`. A side leads: the roll-axis assumption is wrong, try 0 or 180. Write the value that
worked in the flight log.

## 4. Logging

Same command as the load runbook (`exp8_frames.md`, WiFi `stream_log`). The reference shows in the position
setpoints (`locxPID.Des`, `locyPID.Des`, `Z_posPID.Des`) and the yaw reference in `yawPID.Des`. Optional later: add `traj_active`, `traj_phase`,
`traj_t` to slot 3 so the review splits the runs by itself.
