# 2026-10-08 load test runbook: 500 g, PID then MRAC vp 6 (then vp 3)

Why vp 6: lowest attitude sd with MRAC on in exp8 (1.6 / 2.0 deg) and u_ad uncorrelated with the rate
(+0.02 / +0.01), the property that failed under load on 10-07. vp 3 is second (it landed on the pad, but that was
the operator's trim). Numbers: `2026-10-08-exp8-analysis.md`. Thresholds below are PROPOSED.

## 0. Firmware

The in-flight preset (`vp_user`, `vp_user_go`) needs one build + flash of `overnight-2026-10-08` (Keil F7, then
download). Without it everything below still works except section 4: the flashed exp8 firmware accepts `vp_id`
on the ground only.

## 1. The one command (start before arming, Ctrl+C after the last landing)

```
python -m ground_station.livewatch.stream_log --frames ground_station/livewatch/exp8_frames.md --seconds 3600 --out logs/exp9_vp6-500g.csv
```

Same frames as exp8 (100/50/50/10 Hz nominal, 85,200 of 87,552 B/s, 0 drops in exp8). Slot 3 logs `vp_active`,
`kp_active`, `g_ekf_of1_on`, so the review splits the segments by itself. Review afterwards:

```
python -m ground_station.analysis.flight_review logs/exp9_vp6-500g.slot0.csv --out docs/flights/plots/2026-10-08-exp9.review.html
```

## 2. Keil, after every reboot or pack swap (drone on the ground, disarmed)

| write | check |
|---|---|
| `kp_id = 5` (`kp_go = 1` if it was already 5) | `kp_active == 5` (of1 off, Z clamps 500/300/700) |
| `vp_id = 6` | `vp_active == 6` |

## 3. Flight

| # | step | pass / abort |
|---|---|---|
| 1 | Load on, rope under the CG, fresh pack | |
| 2 | ch8 off, take off, PID hover 20 s, small x/y moves | the "struggle" clip |
| 3 | ch8 on, hover 20 s | swing not growing |
| 4 | move around the room 20-40 s inside the soft fence | |
| 5 | ch8 off, land, disarm | |
| 6 | `vp_id = 3` on the ground, repeat 2-5 | second variant |

Abort (ch8 off at once, PID flies): pitch/roll swing growing over 3 cycles, a motor pegged for more than ~2 s,
or the soft fence (z 1.4, |x| 1.3, |y| 1.7 m). Then land.

Drift: with of1 off the estimate walks a few cm/s toward +x / -y (exp8: 2.4-6.5 cm/s). Trim with the stick as in
exp8. Optional, one Keil write on the ground, after the main test: `g_ekf_of1_on = 1` and
`s_ekf_of.R_of1 = 0.01` (default 0.001; weaker of1 fusion, slower drift correction, less swing leak). Untested
in flight: watch for the 10-07 swing.

## 4. In-flight preset (needs the new firmware)

`vp_user` is a struct in the watch window. It starts as vp 6. Edit any field, then `vp_user_go = 1`.

| field | meaning | vp 6 value |
|---|---|---|
| `ref_pr`, `ref_y` | reference model type p/r, y (-1 global as flown, 0 pass, 1 first, 2 second order) | 2, 1 |
| `dn` | normalized drive (0/1) | 1 |
| `lam_edot` | e_dot weight p/r | 0.0018 |
| `bw_y` | yaw reference bandwidth | 2 |
| `mu_sat` | saturation-aware gain p/r/y (0 off) | 0 |
| `kappa`, `crm_ell` | PR add-on p/r (0 off) | 0, 0 |
| `st_eps` | ST add-on p/r (0 off) | 0 |
| `lam_ang` | 3L add-on p/r (0 off) | 4 |
| `g` | gamma scale p/r/y | 0.25 |
| `basis` | feature set p/r: 0 S6, 1 S10, 2 RBF6, 3 RBF12, 4 RBF24, 5 S6+RBF12 (1-5 need the `MRAC_VARIANT=2` build) | 0 |

It applies only with ch8 off: on the ground, or in hover while PID flies (MRAC in shadow). With ch8 on the
request waits until ch8 goes off. Applying resets the weights; ch8 on after that starts from zero weights.
`vp_active == 100` = the user row is live; `0xEE` = a field was refused (check the value range). `vp_id = N`
goes back to a table row (same ch8-off rule).

In-flight sequence: hover with ch8 on → ch8 off (PID) → edit `vp_user` → `vp_user_go = 1` → `vp_active == 100`
→ hover 5-10 s in shadow → ch8 on.
