# Figure-8 tracking error, lab day 2026-10-19

This note covers the memory-premise figure-8 flights `mem2`..`mem6` (`logs/flights/2026-10-19/02_*`..`06_*`, u0 and u100 arms). They tripped the GS abort `L1 position_error` about 6.8 s into the move. The note answers three questions: why the error reached 33-52 cm, whether the velocity feed-forward (VFF) was active, and whether camera parallax contributed.

## Summary

- **The VFF was active, and it was applied once.** The flights ran `preset figure8` (Keil preset, `Traj_Tick` in `TASK/AutoflyTask.c`). That path writes `g_traj_vff_x/y` every tick through `Traj_Commit`/`Traj_FFUpdate`. `g_traj_ff_preset` defaults to 1. With `g_xy_ctrl_kind=0`, `Pos_Compute` adds the VFF to the velocity-loop Des exactly once (`TASK/StabilizerTask.c`, `vff_x = vx + g_traj_vff_x`).
  - The earlier HANDOFF claim does not hold for these flights: "no FF, e = v/Kp = 47.5 cm lag at 0.38 m/s".
  - The real peak speed was 66 cm/s. Without FF, the lag would have been 66/0.8 = 82 cm, and the first lobe would already have tripped the abort.
- **The error is a growing overshoot, not lag.** The drone overshoots every x extreme, and the overshoot grows each time. The abort trips on the third x extreme, at θ ≈ 270°, on the y = -60 lobe.
- **No parallax or lens-edge effect.** The pose `(+0.30, -0.60, 0.80)` m is well inside the frame. The parallax sensitivity there (2.01 cm per cm of height error) is lower than at the pad (2.19).
- **Not MRAC.** The u0 and u100 arms show the same pattern and the same size.
- **Firmware change in this commit:** the GS command paths (`circle`, `sinusoid`, `figure8_path`) now also feed the VFF. Before this, they were pure position references with no FF. That gap is where the "no FF for figure-8" finding came from. It applied to the GS `figure8` command, not to the preset these flights used.

## The reference flown

The `.fly` (02) was:
- `set traj_p.move_s=18 traj_p.ramp_s=2 traj_p.ret_s=3 traj_p.hold_s=1 traj_p.yaw_follow=0`
- `preset figure8 f8_type=1 f8_a_cm=60 f8_laps=2`
- `g_xy_ctrl_kind=0 g_traj_ff_shift_s=0`
- takeoff 0.8 m.

The shape is Gerono: x = 0.5·a·sin 2θ, y = a·sin θ.

I fitted the firmware time warp (ramp 2 s, move 18 s) plus Gerono (a = 60 cm) to the logged Des with least squares. The fit rms is 1.8-3.1 cm, so the logged reference is exactly the preset:

| Flight | Arm | s_om (rad/s) | Fit rms (cm) | Peak speed (cm/s) | Move time at abort (s) | θ at abort (°) | Max error (cm) | Des at max (cm) | FB at max (cm) |
|---|---|---|---|---|---|---|---|---|---|
| 02 | u0 | 0.773 | 3.0 | 65.6 | 7.3 | 277 | 46.0 | (+8.6, -59.4) | (+48.4, -82.5) |
| 03 | u100 | 0.774 | 1.8 | 65.6 | 7.1 | 271 | 36.4 | (+9.9, -59.1) | (+42.7, -75.1) |
| 04 | u0 | 0.777 | 3.1 | 66.0 | 7.4 | 283 | 33.4 | (+3.5, -59.9) | (+28.4, -82.1) |
| 05 | u100 | 0.771 | 2.4 | 65.4 | 7.0 | 264 | 36.5 | (+16.3, -57.6) | (+45.7, -79.2) |
| 06 | u0 | 0.775 | 2.3 | 65.8 | 7.1 | 273 | 51.6 | (+13.4, -58.4) | (+63.3, -71.8) |

Notes on the table:
- The model value is s_om = 2π·laps/(move_s − ramp_s) = 0.785 rad/s. The x component runs at 2·s_om ≈ 1.55 rad/s (period 4.1 s). The y component runs at 0.78 rad/s (period 8.1 s).
- The peak speed of 66 cm/s is under the `lim_mode=1` velocity limit (110 cm/s) and under `g_vxy_des_max` (120 cm/s). The VFF is never clamped.
- An earlier estimate gave peaks of 82-95 cm/s. That was an artifact of finite differences on the 5 Hz debrief stream.

## The error pattern

The pattern is the same in all five flights:

| Phase | Feedback vs Des |
|---|---|
| Ramp | FB trails Des by up to 14 cm |
| x extreme at θ ≈ 45° | overshoots by +5..+17 cm |
| x extreme at θ ≈ 135° | overshoots by -17..-25 cm |
| x extreme at θ ≈ 225° | overshoots by +20..+50 cm |
| Top y lobe (y = +60) | overshoots by +14..+20 cm |
| Bottom y lobe (y = -60) | overshoots by -23..-27 cm |

The bottom y lobe and the third x extreme coincide near θ = 270°. That is where the error vector peaks, and where the abort fires.

A lag would show the error at the points of peak speed, and the error would have a constant size. Here the error sits at the turning points, and it grows from extreme to extreme. That is the signature of an underdamped loop driven near its resonance.

## What the linear model explains

Model the inner velocity loop as first order with bandwidth ωv, and close the outer P loop (Kp = 0.8) with velocity FF:

```
T(s)   = ωv (s + Kp) / (s² + ωv s + ωv Kp)          ζ = 0.5·sqrt(ωv / Kp)
1-T(s) = s² / (s² + ωv s + ωv Kp)                   (tracking error / reference)
```

With ωv ≈ 3 rad/s (locxsPID Kp 3.0):

| Axis | Frequency (rad/s) | \|T\| | Phase | \|1-T\| | Error for this amplitude |
|---|---|---|---|---|---|
| x | 1.55 | 1.125 | -27° | 0.52 | about 15 cm at 30 cm amplitude |
| y | 0.78 | 1.14 | -8° | 0.20 | about 12 cm at 60 cm amplitude |

So VFF plus P with a slow inner loop gives about 12-15 cm of overshoot per axis. That is the zero at s = -Kp. The observed 33-52 cm, growing over the move, is two to three times that. The remainder is unexplained by this model. The candidates are:

1. **Phase lag in the inner loop.** The real loop has more lag than first order: OF/EKF velocity delay, attitude loop, motor lag. This lowers the damping and peaks |T| near 1.5 rad/s. A growing overshoot over the first cycles is what a lightly damped mode looks like before steady state.
2. **Camera-fusion interaction.** In `mem2`..`mem6`, the live tracker's fixes froze during the move. That was the tracker-center bug fixed in brief `j-1019-tracker-center`. If `g_ext_pos_on >= 2` was set on these flights, the EKF was pulled toward a stale fix. That is a slowly varying position bias, which the P loop then fights.
   - The tracker only sends fixes with `--send udp:PORT` (`ground_station/analysis/live_tracker.py`). `g_ext_pos_on` defaults to 0 (`TASK/StabilizerTask.c`). Whether fusion was on cannot be decided from the cloud copies of the logs. **Open.**

These causes have been ruled out:
- **The position-loop D term.** Kd = 4 per tick at 100 Hz is about a 0.04 s lead.
- **MRAC.** The u0 arm (MRAC off) overshoots as much as u100.

## Parallax

Inputs:
- Calibration: `logs/video_truth/floor-1019/aruco.npz`.
- Camera position: C = (-1.594, -2.124, 2.011) m in the pad frame. It looks at the pad obliquely from the -x/-y corner.
- The tracker corrects heights with xy_true = c_xy + (g_xy − c_xy)·(c_z − z)/c_z. The sensitivity to a height error is |P_xy − C_xy| / (c_z − z).

Results:

| Point | Pixel (1920x1080) | Sensitivity (cm per cm of height error) |
|---|---|---|
| (+0.30, -0.60, 0.80) m | (1377, 480), inside the frame | 2.01 |
| Pad | | 2.19 |

The y = -60 lobe is not near the frame edge, and it is less sensitive to height error than the pad. Parallax and lens distortion cannot explain an error specific to that lobe.

## Changes and recommendations

**Done (this commit):**
- `TASK/AutoflyTask.c`: `RunCircle`, `RunSinusoid` and `RunFigure8` commit through `Traj_Commit`.
- While a GS path runs and no preset is active, `Traj_Tick` calls `Traj_FFUpdate`. The GS paths therefore get the same finite-difference VFF as the presets, still gated by `g_traj_ff_preset` and `airborne`.
- The GS paths get no yaw-rate FF.
- The first path tick produces no FF, because `s_ff_have` is 0, so the jump to the path start is not differentiated.
- A path ending, or FlyMode leaving SDK, clears the FF on the next tick.
- The sinusoid on axis 2 (z) now also gets the Z FF, clamped to `TRAJ_FF_VZ_MAX_MPS`.

**Done earlier:** `AbortLimits.pos_err_m` 0.25 → 0.50 m (`ground_station/service/abort_monitor.py`, operator-approved). The rule trips only after the error stays above the limit for `pos_err_hold_s` = 0.3 s.

This does not guarantee that a two-lap figure-8 finishes:
- The errors in the table were measured up to the abort, and at that point the overshoot was still growing.
- In 06 the error already reached 51.6 cm.

The 0.50 m limit buys margin, not a fix.

**Recommended (operator decision, not changed):**

1. **Fly the 10-14 `tuned_shift` set on the figure-8 as its own A/B arm.** That is `ground_station/service/campaigns/lab_c_tuned.fly`:
   - locx 0.67/0.0014/4.93
   - locxs 4.70/0.0031/0
   - `g_traj_ff_shift_s=0.31`

   Its replay cost in `xy_tune` was 2.0, against 8.1 for the current gains.

   Keep this arm out of the memory-premise u0/u100 pairs. Changing gains inside those pairs would confound the MRAC comparison.

   Note that in the first-order model above, the shift alone does not reduce |1-T| at 1.55 rad/s. Any benefit has to come from the retuned loops (inner Kp 3.0 → 4.70 with Kd 6 → 0, outer Kd 4 → 4.93) together with the lead. `xy_tune` scored this on identified plants, so the lab A/B is the real test.
2. **Log the 100 Hz streams on the next figure-8.** The 5 Hz debrief cannot resolve an inner-loop resonance.
   - `velocity_loops` (locxsPID Des/FB/U)
   - `camera_fusion` (`g_ext_pos_on`, e_x/e_y, n_ok/n_rej)

   Both are already in the 02 `.fly` `log` line. Check that the session CSVs are copied with the flight logs.
3. **Record whether `g_ext_pos_on` was 2 on `mem2`..`mem6`.** This is in the local session telemetry. If it was, re-fly one figure-8 with fusion off (`g_ext_pos_on=0`) to separate cause 2 from cause 1.
