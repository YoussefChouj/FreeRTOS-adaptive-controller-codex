# Small neural networks for IMU denoising: feasibility for this drone (2026-10-06)

Status: study only. Nothing here is built, trained or flown. Every number below is from a cited paper or is a
labelled arithmetic estimate (computed, not measured on this board).

## Bottom line

It is real, and a network small enough for the STM32F4 exists (hundreds of weights). But a network cannot invent
information. It removes bias and noise only where its training data had a reference for the truth:
- roll and pitch: gravity from the accelerometer gives a reference, so a learned correction can stay unbiased;
- yaw: no reference unless a heading source is fused (not checked here), so yaw bias can still drift;
- outside its training conditions (new props, new vibration from an arm load, other temperatures) it can be worse
  than no network.
So: try it offline on our own logs first. Put it on the board only if it beats a classical calibration baseline
on a held-out flight.

## What the papers show (arXiv, read as abstracts 2026-10-06)

| Paper | Network | Needs for training | Relevance here |
|---|---|---|---|
| Brossard, Bonnabel, Barrau 2020, arXiv 2002.10718 | dilated 1-D CNN, no RNN | ground-truth orientation (EuRoC, TUM-VI: motion capture) | best-known result: gyro-only attitude beats visual-inertial odometry on those datasets; open-source code; we have no motion capture |
| "TinyGC-Net" 2024, arXiv 2403.02618 | CNN with hundreds of parameters, trained on a GPU, run on an MCU | raw gyro only, calibration and denoising handled separately | the size class that fits our board |
| Cioffi, Bauersfeld, Kaufmann, Scaramuzza 2022, arXiv 2210.15287 | model-based filter + learned module fed with thrust | flight logs with a pose reference | quadrotor-specific: thrust as an input helps; we log motor commands |
| "DIDO" 2022, arXiv 2203.03149; "AI-IO" 2026, arXiv 2603.00597 | learning + quadrotor dynamics | flight logs with a pose reference | same family: dynamics-informed inertial odometry |

## What the board already does (no network)

- `API/gyro_filter.c`: a biquad low-pass on each gyro axis, default cutoff 40 Hz (`GYRO_TUNE_ROW`).
- `API/bmi088_driver.c` (protected, stays byte-identical): bias calibration at rest, plus z-bias tracking
  (`g_gyro_z_bias_track`, `g_gyro_z_bias_blocks` in `API/bmi088_driver.h`).
- The OF EKF estimates optical-flow biases (`s_of_bias_x`, `s_of_bias_y`, subscribable in the estimator panel).
A network would sit between the driver output and the filter, so the protected driver does not change.

## Plan to try it (lowest risk first)

1. **Record (in the lab, no extra flights).** Two minutes still on the bench, props off, at the start and at the
   end of the day, plus the hover flights already planned. Log raw gyro and accelerometer at the full rate the link
   allows. The symbols exist: `Gyro_X_Real..Gyro_Z_Real` and `Acc_X_Real..Acc_Z_Real` (subscribe slot 1 of the
   data-flow and estimator panels, `ground_station/service/streams.py`). NOT checked: their slot rate, whether they
   are taken before or after the biquad, and whether the campaign log plans record them.
2. **Classical baseline, offline.** Fit a bias + temperature slope + scale per axis on the still segments. Score it
   on a held-out flight with three checks that need no motion capture:
   - still segments: mean rate (should be 0) and Allan deviation;
   - hover: roll and pitch from integrated gyro against the accelerometer's gravity direction;
   - hover: yaw drift per minute.
3. **Tiny network, offline.** TinyGC-Net-sized 1-D CNN on a short causal window of gyro (+ accel, + motor
   commands as in Cioffi et al.), trained on the self-supervised targets above, scored the same way. Keep it only
   if it beats step 2 on the held-out flight.
4. **Board, only after step 3 wins.** Fixed weights in a C table (pid.c `*_ROW` style), behind a switch that
   defaults off, on a branch, like `h0g-port`. Bound the correction (clip) and fall back to the raw value on NaN.

## Cost and risk (estimates, not measured)

- Compute: about (weights) multiply-adds per gyro sample. Example, computed: 500 MAC x 1000 samples/s =
  0.5 M MAC/s, against a Cortex-M4F at 168 MHz. That is a small share of the CPU, but measure it with the
  bench counters (`hlth.stab_cpu_pct`, `loop_max_us`) before flying.
- Delay: a causal window adds group delay inside the rate loop, which costs phase margin. Keep the window short
  and compare against the 40 Hz biquad, which already adds delay.
- Distribution shift: the asymmetric arm load changes the vibration spectrum. Train with and without the load,
  or the network may make things worse exactly in the demo case.

## Downloads (need the user's yes first)

Brossard's code (GitHub `mbrossar/denoise-imu-gyro`) and the EuRoC / TUM-VI datasets are only needed to
reproduce the paper. They are not needed for steps 1-3 on our own logs.
