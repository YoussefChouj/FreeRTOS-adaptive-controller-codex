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
| `traj_id` | write | 1 step, 2 zigzag, 3 circle, 4 figure-8 |
| `traj_go` | write 1 | start `traj_id` with the current `traj_p`; the firmware clears it |
| `traj_stop` | write 1 | end the move now and fly back (status 4) |
| `traj_status` | read | 1 running, 2 done and back, 3 aborted (stick takeover, landing, disarm), 4 stopped and back |
| | | refused, nothing moved: `0xE0` not flying, `0xE1` busy (a GS path or GS authority), `0xE2` bad `traj_id`, `0xEE` a field out of range or too fast, `0xEF` the shape leaves the soft fence |
| `traj_phase` | read | 0 idle, 1 move, 2 return, 3 hold at the start point |
| `traj_active`, `traj_t` | read | running id, seconds since `traj_go` |
| `traj_home_x/y/z/yaw` | read | the start point (cm, cm, m, deg), taken from the setpoint at `traj_go` |

## 2. `traj_p` (edit any field, it is read at `traj_go`)

| field | default | range | meaning |
|---|---|---|---|
| `profile` | 1 | 0, 1 | 0 constant speed with hard corners and hard steps; 1 smooth: cosine ramps of `ramp_s` |
| `axis` | 0 | 0 x, 1 y, 2 z | step and zigzag axis |
| `zz_shape` | 0 | 0, 1 | zigzag 0 triangle, 1 sine |
| `f8_type` | 0 | 0, 1 | figure-8 0 Bernoulli (lies along x), 1 Gerono (lies along y) |
| `move_s` | 18 | >= 5 | preset time, s |
| `ret_s` | 5 | 3-10 | fly back to the start point, s |
| `hold_s` | 2 | 0-5 | hold the start point, s |
| `ramp_s` | 3 | 0.5 to `move_s`/4 | profile 1 ramp, s |
| `step_cm` | 40 | ±100 smooth, ±50 hard, z ±40 | out for `move_s`/2, then back |
| `zz_amp_cm`, `zz_hz` | 30, 0.2 | up to 80 (z 40), up to 1 Hz | zigzag amplitude and frequency |
| `circ_r_cm`, `circ_laps` | 40, 1 | 10-80, up to 3 | circle; the start point is on the circle, centre at -x |
| `f8_a_cm`, `f8_laps` | 50, 1 | 10-80, up to 3 | figure-8 size and laps |

Refused at `traj_go` (status `0xEE`): `move_s + ret_s + hold_s > 25`, a peak reference speed above 80 cm/s, or
a return faster than 80 cm/s. Peak speeds with the defaults (computed): step 21, zigzag 24, circle 17,
figure-8 21 cm/s. Refused with `0xEF`: any part of the shape outside the soft fence (|x| 1.3, |y| 1.7 m, z 0.4-1.4 m).
The circle and the Bernoulli figure-8 reach 2 × size toward -x, so start them at x >= -0.5 m (circle 40) or
x >= -0.3 m (figure-8 50).

## 3. Flight sequence (load on, `kp_id = 5`, `vp_id = 6`, as in the load runbook)

| # | step | check |
|---|---|---|
| 1 | Take off, hover still, ch8 as wanted (off = PID, on = MRAC). Hands off the sticks | |
| 2 | Edit `traj_p`, set `traj_id` | |
| 3 | `traj_go = 1` | `traj_status == 1`, `traj_phase` 1 → 2 → 3 |
| 4 | Wait for `traj_status == 2` (back at the start point, sticks yours again) | |
| 5 | Repeat (`traj_go = 1`), next preset, or land | |

Per preset: once with ch8 off (PID), once with ch8 on (MRAC), same `traj_p`. Do not switch ch8 during a preset.
Watch motor 2: rest in hover or land between runs if it runs hot.

Stop early: `traj_stop = 1` flies back and holds (status 4). Take over: move any stick; the preset ends at once
with no return (status 3) and you fly. Landing or disarming also ends it. Do not hold the ch4 + ch5 AFLY key
combination during a preset. Yaw is held at `traj_home_yaw` the whole time.

## 4. Logging

Same command as the load runbook (`exp8_frames.md`, WiFi `stream_log`). The reference shows in the position
setpoints (`locxPID.Des`, `locyPID.Des`, `Z_posPID.Des`). Optional later: add `traj_active`, `traj_phase`,
`traj_t` to slot 3 so the review splits the runs by itself.
