# R4 overnight report (2026-10-12): drift and live camera fusion

Goal: `docs/lab/overnight-goal-2026-10-11.md`. The drone was not connected: nothing was flashed, armed or captured.
Every number is measured from the R4 logs unless it says computed, PROPOSED or UNVERIFIED.

## Bottom line

| Question | Answer |
|---|---|
| Is drift solved? | **In replay, yes.** Camera fixes stop the error from growing: on the 10 flights with good truth, EKF 30-146 cm rms becomes 5.1-13.6 cm rms (0.3 s latency, replay). Hover drift rate on b17: 0.29 to 0.03 cm/s. |
| Is the 10 cm / 60 s budget met? | **At 0.15 s latency and 20 fixes per second: yes, on all 4 flights (replay).** Live tracker replay with the retuned firmware rule (gain 0.9, slew 10 cm, spread) and 20 Hz fixes (all PROPOSED), 1 s-mean 60 s drift p90: b17 hover 3.5, b13c 7.3, b5 7.4, b16 traj 7.4 cm (30 Hz: 3.0 / 6.3 / 6.3 / 6.5). At 10 Hz b16 missed (12.1). At 0.3 s latency only the hover passes (moving flights 11.3-12.3 at 20 Hz, 10.3-11.3 at 30 Hz). So two link gates are required: latency 0.15 s or less, and 20 or more fixes received per second. The real firmware C code, run on the same fixes, gives the same result within 0.5 cm (b16 7.9). |
| Can a small NN fix drift with the camera off? | **No.** Best 34 cm (b15, in the training pool), best held-out 47 cm (b7); the b17 hold-out got worse (19 to 28 cm). The camera is required. |
| Lab tomorrow? | **Go, as a fusion bring-up day** ([run sheet R5](../lab/run-sheet-r5-fusion-bringup.md)). Dense trajectory presets and load scenarios only after the closed-loop hover gate passes, the measured latency is 0.15 s or less and the drone receives 20 or more fixes per second; start the dense presets at moderate speed (b16 runs at 38-40 cm/s were the worst, PROPOSED). |

Think of the EKF as a walker counting steps with closed eyes, and the camera as a glance at a landmark twenty times a
second. The glance stops the wandering. What is left is that each glance shows where you were 0.3 s ago.

Caveats that limit the claim:
- **Circular:** the camera is both the fix source and the truth.
- **Open loop:** these are replays; firmware mode 2 is built and host-tested but not flashed or flown.
- **Bad tracks defeat fusion:** b8, b10 and b12 have coral-track jumps; fused there 70-92 cm rms (table B).

Chart: 1 s-mean 60 s drift p90 per flight (log scale; the dashed line is the 10 cm budget).

<!-- chart -->

## 1. Truth (camera)

- Lens (`docs/video-truth/cam_r4_lenswave_2026-10-11.json`): reprojection rms 0.287 px, f 878 / 879 px.
  - Floor markers measured 17.61 / 16.95 / 16.90 cm against 17.2 cm by tape; plane normals within 1-2.5 deg.
  - Camera centre (-1.64, -2.25, 2.01) m. Computed: a 10 cm height error moves the cast 36 cm along the view.
- The drone is tracked by its coral guard rings (`truth4`): 0 frames lost on all 13 flights.
- Static accuracy: pad jitter 0.6-1.1 cm while on the pad (table A).
- Sync: take-off and landing offsets agree within 0.65 s on the take-off-anchored flights.
  - On b5, b6, b8, b9 and b12 they disagree by 6-13 s, because the telemetry lift detection fires 9-12 s
    late; those flights are landing-anchored.
  - b7 and b10 are take-off anchored but unverified (no landing found).
- h_ring caveat: 0.15-0.17 m on b5, b6, b9 and b12, against 0.06-0.09 m elsewhere (pad off id0?), UNVERIFIED.

Table A, truth quality per flight:

| flight | flight s | hold / move / yaw s | h_ring m | pad jitter cm | sync anchor | takeoff-landing offset s | coral lost | landed from take-off cm |
|---|---|---|---|---|---|---|---|---|
| drift_rc_r4_b10_v20 | 153 | 41 / 92 / 20 | 0.066 | 0.7 | takeoff-unverified | - | 0.000 | - |
| drift_rc_r4_b11_v24 | 183 | 70 / 88 / 25 | 0.089 | 0.8 | takeoff | 0.65 | 0.000 | 49 |
| drift_rc_r4_b12_v21 | 140 | 48 / 70 / 22 | 0.167 | 1.1 | landing | 8.04 | 0.000 | 18 |
| drift_rc_r4_b13c_v22 | 151 | 40 / 88 / 23 | 0.069 | 0.7 | takeoff | 0.16 | 0.000 | 13 |
| drift_rc_r4_b14_v23_3g | 163 | 42 / 103 / 18 | 0.076 | 0.7 | takeoff | 0.08 | 0.000 | 12 |
| drift_rc_r4_b15_v0_3g | 158 | 45 / 89 / 23 | 0.077 | 0.7 | takeoff | -0.30 | 0.000 | 25 |
| drift_rc_r4_b16_v21_traj_3g | 202 | 0 / 160 / 42 | 0.077 | 0.6 | takeoff | 0.53 | 0.000 | 4 |
| drift_rc_r4_b5_max2 | 161 | 70 / 68 / 22 | 0.165 | 0.9 | landing | 12.16 | 0.000 | 22 |
| drift_rc_r4_b6_v16_5300 | 157 | 55 / 80 / 22 | 0.149 | 0.9 | landing | 9.79 | 0.000 | 15 |
| drift_rc_r4_b7_v19_4000 | 186 | 73 / 88 / 25 | 0.066 | 0.7 | takeoff-unverified | - | 0.000 | - |
| drift_rc_r4_b8_v17 | 199 | 69 / 101 / 29 | 0.073 | 1.1 | landing | 12.93 | 0.000 | - |
| drift_rc_r4_b9_v18 | 170 | 72 / 74 / 24 | 0.161 | 0.6 | landing | 6.29 | 0.000 | 23 |
| hover_r4_b17_v0_3g | 111 | 111 / 0 / 0 | 0.056 | 0.8 | takeoff | -0.12 | 0.000 | 22 |

## 2. Drift scoring (EKF alone vs camera)

Table B, drift per flight (cm). EKF = the firmware `earth_x/y`. The camera replay and live columns use 0.3 s
latency; "1 s-d60" = 1 s-mean 60 s drift p90.

| flight | EKF rms | EKF d60 p90 | EKF 1 s-d60 p90 | hold rate cm/s | move rate cm/s | camera replay rms / 1 s-d60 | live rms / 1 s-d60 | MLP rms / 1 s-d60 |
|---|---|---|---|---|---|---|---|---|
| drift_rc_r4_b10_v20 | 121 | 280 | 278 | 3.96 | 4.8 | 81.0 / 189.0 | 92.0 / 207.9 | 116.6 / 289.4 |
| drift_rc_r4_b11_v24 | 146 | 151 | 151 | 0.55 | 1.5 | 12.9 / 24.1 | - / - | 163.8 / 166.6 |
| drift_rc_r4_b12_v21 | 114 | 207 | 202 | 3.78 | 6.3 | 70.5 / 143.5 | - / - | - / - |
| drift_rc_r4_b13c_v22 | 59 | 60 | 59 | 0.93 | 0.4 | 9.9 / 16.0 | 10.1 / 17.1 | 103.1 / 94.1 |
| drift_rc_r4_b14_v23_3g | 62 | 84 | 84 | 0.81 | 0.3 | 10.8 / 20.2 | - / - | 72.9 / 162.8 |
| drift_rc_r4_b15_v0_3g | 65 | 58 | 59 | 2.41 | 0.3 | 10.3 / 18.5 | - / - | 23.5 / 34.1 |
| drift_rc_r4_b16_v21_traj_3g | 70 | 126 | 126 | 0.00 | 0.8 | 13.6 / 26.9 | - / - | 113.2 / 142.3 |
| drift_rc_r4_b5_max2 | 113 | 136 | 136 | 0.98 | 2.4 | 11.4 / 16.2 | 11.5 / 16.3 | 158.0 / 186.4 |
| drift_rc_r4_b6_v16_5300 | 100 | 107 | 104 | 0.49 | 1.4 | 12.5 / 21.2 | - / - | 38.6 / 58.6 |
| drift_rc_r4_b7_v19_4000 | 135 | 113 | 112 | 1.46 | 1.3 | 12.5 / 21.1 | - / - | 64.3 / 49.2 |
| drift_rc_r4_b8_v17 | 143 | 267 | 264 | 12.60 | 3.0 | 84.1 / 213.0 | - / - | - / - |
| drift_rc_r4_b9_v18 | 133 | 136 | 136 | 0.67 | 2.2 | 11.1 / 19.3 | - / - | 94.3 / 91.9 |
| hover_r4_b17_v0_3g | 30 | 20 | 19 | 0.29 | 12.8 | 5.1 / 10.3 | 5.4 / 11.1 | 19.5 / 27.9 |

- The large EKF numbers are real estimate drift, not truth errors: earth ends 1-2 m off the pad
  (b5 earth ends at (-1.6, +1.8) m, b11 at (-0.9, +2.0) m, while the truth is back on the pad).
- Earth-vs-camera rotation (fit on the first 60 s): -77 to -101 deg on 11 of 13 flights.
- The hover alone fails the budget: b17 EKF 60 s drift p90 20 cm.
- Variants: b5-b12 are 4-guard, b13c-b17 are 3-guard (the 4-guard vs v0_3g caveat applies). No variant ranking
  is claimed: the spread is dominated by move error and the truth caveats.
- Move-rate values on b17 (12.8 cm/s) come from a 0.02 s "move" class: ignore them.

**b16 per-preset tracking error** (`video_truth presets4`, setpoint runs; ctrl = estimate vs setpoint, i.e.
controller lag; ekf = estimate vs truth; track = truth vs setpoint, what the drone really did; fused = fuse4 replay
at 0.15 s, gain 0.9, slew 10 cm, spread 0.1 s; all rms cm):

| t0 s | kind | dur s | speed cm/s | ctrl | ekf | track | fused |
|---|---|---|---|---|---|---|---|
| 791 | return | 4.0 | 3.8 | 6.4 | 32.3 | 34.6 | 1.9 |
| 797 | traj | 19.9 | 40.0 | 29.9 | 46.5 | 49.5 | 10.4 |
| 830 | traj | 4.4 | 15.5 | 12.6 | 14.5 | 21.0 | 4.0 |
| 838 | return | 4.0 | 9.4 | 6.7 | 13.9 | 19.6 | 3.7 |
| 844 | traj | 20.0 | 22.1 | 11.2 | 46.9 | 51.1 | 2.8 |
| 893 | return | 4.0 | 36.0 | 15.3 | 105.3 | 97.7 | 3.8 |
| 899 | traj | 10.1 | 12.6 | 19.1 | 94.0 | 102.2 | 3.4 |
| 925 | return | 4.0 | 27.2 | 11.9 | 80.7 | 91.2 | 5.2 |
| 952 | return | 4.0 | 34.2 | 17.0 | 70.0 | 78.1 | 8.2 |
| 958 | traj | 5.8 | 37.8 | 29.2 | 73.8 | 74.1 | 11.4 |

- Track error is about ctrl + ekf: the estimate error is most of it (14-105 cm), the controller lag is 6-30 cm.
- Fused (replay) cuts the estimate error to 1.9-11.4 cm; the worst runs are the fastest (38-40 cm/s). With the
  spread off, the same runs read 1.9-10.5 cm, so the spread costs at most 0.9 cm.
- Dense trajectory presets need both: camera fusion for the estimate and a faster controller for the 30 cm lag.

**Pure-yaw drift ranking** (`video_truth yaw4`, EKF error jump per yaw episode, cm per 90 deg of yaw):

| b12 | b7 | b14 | b6 | b13c | b11 | b5 | b9 | b15 |
|---|---|---|---|---|---|---|---|---|
| 9.0 | 8.2 | 6.0 | 4.7 | 4.0 | 3.2 | 2.6 | 2.2 | 2.1 |

- Yaw alone moves the estimate 2-9 cm per 90 deg (b12 truth suspect). That is small next to the move error
  (table B), so yaw is not the main drift source; the camera offset removes it anyway.

## 3. Small NN with the camera off (STM32F4-sized)

Literature check (abstracts verified on arXiv):

| Paper | What it learns | Fit for us |
|---|---|---|
| TLIO 2007.01867 | displacement + covariance into an EKF | headset pedestrians |
| AirIMU 2310.04874 | IMU correction + uncertainty | generic IMU |
| AirIO 2501.15659 | UAV inertial odometry, body-frame input + attitude encoding | closest; our design follows it |
| AI-IMU 1904.06064 | adapts the Kalman noise | cars (KITTI) |
| Gyro denoise 2002.10718 | gyro correction | attitude only (EuRoC) |

None has weights for a BMI088 at 100 Hz plus optical flow, so nothing was downloaded; we trained our own.

Design (`video_truth nn4`):
- Target: body-frame velocity residual, the derivative of (truth − EKF), rotated into the body frame.
- Yaw sign chosen per flight from the EKF's own body velocity (no truth needed, so usable on board).
- Input: 17 channels, 10 block means over a 1 s causal window, so 170 features at 10 Hz.
- The prediction is integrated onto the EKF position and scored like the other columns.

| Model | Params | MAC / step | At 10 Hz (computed) |
|---|---|---|---|
| Ridge | 342 | 340 | 3.4 kMAC/s |
| MLP 2×32 tanh | 6594 | 6528 | 65 kMAC/s, 26 KB float32 weights |

Leave-one-flight-out, 1 s-mean 60 s drift p90 (cm), EKF → ridge / MLP:

| | Flight | EKF | Ridge | MLP |
|---|---|---|---|---|
| held out | b17 | 19 | 65 | 28 (worse) |
| held out | b7 | 112 | 47 | 49 |
| held out | b10 | 278 | 264 | 289 |
| pool | b6 | 104 | 58 | 59 |
| pool | b9 | 136 | 95 | 92 |
| pool | b15 | 60 | 46 | 34 |
| pool | b5 | 136 | 223 | 186 |
| pool | b14 | 84 | 148 | 163 |
| pool | b16 | 126 | 190 | 142 |
| pool | b13c | 59 | 70 | 94 |
| pool | b11 | 151 | 149 | 167 |

- Velocity r² is −0.24 to +0.34 (ridge on b15 excluded): the inputs do not predict the residual well.
- Ridge blew up on b15 (rms 7548 cm, an out-of-range feature), although its 1 s-d60 reads 46.
- The oracle (the true residual integrated the same way) reaches 1-3 cm (b10 8.9), so the pipeline is not the
  problem.
- b12 was skipped (reflected earth fit). b8 and b12 were never trained on (truth suspect).
- **Verdict:** no camera-off fix; the best is 34 cm (pooled; 47 cm held out) against the 10 cm budget. Not worth flashing.

### 3b. Second pass (10-12 day): sequence models, more data, a pretrained net

Tool: `python -m ground_station.analysis.nn_io` (commits 6f61d042, 66c5830b, 44bb6a3a, e46eb859, 36c0bad9, 88c7cc43).
Same metric (1 s-mean 60 s drift p90, cm), same held-out flights; b3 and b4 excluded from training (bad truth).

Is the target even reachable from our data?
- Linear velocity calibration fitted per flight *on its own truth* (a cheating oracle): 31-73 cm on moving
  flights, 13 on b17. A learned correction must do better than this oracle to reach 10 cm.
- Truth time shift scan (±3 s): best shift ≤ 0.2 s on every usable flight, gain < 2 cm. Sync is not the cap.

| Model (held-out b17 / b7 / b10) | b17 | b7 | b10 | Note |
|---|---|---|---|---|
| EKF, no NN | 17.4 | 112.2 | 278.3 | baseline |
| nn4 ridge / MLP (pass 1) | 28 (MLP) | 47 (ridge) | 264 | best honest number so far |
| GRU 32, our 9-flight pool | 62 | 66 | 257 | overfits |
| TCN, b3 b4 excluded | 72-77 | 57-67 | 247 | collapses to one global bias |
| TCN, UTIL pretrain + fine-tune, best checkpoint | 17.7 | 55.6 | 268 | checkpoint picked on these flights: optimistic |
| same, final | 23.2 | 69.2 | 265 | |
| same, 3-seed average | 43.3 | 60.9 | 273.9 | seeds disagree |
| same, 5-seed average, gentler fine-tune (lr 3e-4, 60 it) | 23.2 | 81.3 | 270.8 | under-fits b7 |

More data (UTIL, Toronto): 78 Crazyflie 2.1 flights with the same sensors (BMI088 + PMW3901 flow + ToF) and Vicon
truth. On its 39 held-out flights, the plain flow model reaches velocity r² 0.84 but still drifts 87 / 111 cm
(p50 / p90 over 60 s). A TCN trained on UTIL cut the held-out median from 74 to 59 cm, then overfit. Pretraining
on UTIL stops our hover flight from collapsing (b17 18-23 instead of 72-77) but does not reach 10 cm.

Pretrained net (AirIO, Blackbird checkpoint, loaded as plain weights): body axes inferred from its BatchNorm
statistics (FRD). It tracks UTIL velocity direction (corr 0.98 / 0.96 on one flight) but with a bias:

| UTIL held-out (39 flights, 20 Hz) | velocity r² x / y | 60 s drift p50 / p90 (cm) |
|---|---|---|
| flow | 0.84 / 0.84 | 87 / 111 |
| AirIO | 0.14 / 0.15 | 817 / 909 |
| flow + AirIO | 0.84 / 0.85 | 187 / 213 |

AirIO also needs a 100-200 Hz raw IMU; our telemetry has 20-40 Hz block means.

**Verdict (second pass):** still no camera-off model at 10 cm. Even a well-calibrated Crazyflie flow deck drifts
tens of cm per minute, so 10 cm per 60 s without an absolute reference is beyond this sensor class. Our error
is a per-flight flow scale (truth ≈ 0.65-0.95 × EKF velocity) that none of our inputs reveal. What could move it:
log PMW3901 surface quality and shutter at full rate, raw 100+ Hz IMU, and many more flights. The camera-on
fusion (section 4, 3.5-7.9 cm) stays the path to the 10 cm budget.

## 4. Live camera fusion (built, replay-tested, not flashed)

**Firmware EXT_POS mode 2** (e179e95f, retuned in 18c7d964):
- Off at boot; `Cmd 0xFF` value 2 turns it on.
- Each 10 Hz camera fix moves a target offset c: gain 0.9, at most 10 cm per fix (slew), 40 cm gate, re-anchor
  after 3 rejects, 32×50 ms history ring for the delay. All three values are PROPOSED (replay-chosen).
- **Spread (18c7d964):** the applied offset a follows c at most 0.5 cm per 5 ms tick (1 m/s, 20 ticks for a full
  10 cm step) and only that step is added to `earth_x/y`. Without it a 10 cm step lands in one tick, and the
  locx/locy PID has its D term on the error (Kd 4.0, `pid.c:36`): a 40 cm/s kick in the velocity setpoint. The
  ring stores `earth - a` (the raw EKF path); `Reset_World_Origin` zeroes a and c.
- Host gcc test `test_ext_pos.c` passes (new checks: c step at most slew, earth step at most slew/20 per tick,
  a reaches c in 20 ticks). Keil build 0 errors; the first try failed writing `OBJ/*.o` ("Invalid argument",
  Windows Defender holding the files), the retry passed. Defender stayed on.
- Fixed (test only): `test_ekf_of_shadow.c` `test_reset_bias` asserted the 10-03 bias freeze (q_bof 0), but
  41c9dea2 (10-07) learns the bias again (q_bof 1e-5). It now checks a slow learn from 0; 8/8 pass. No firmware change.
- `Reset_World_Origin` zeroes c; sysid start (`send_data.c` case 6) zeroes earth, so neither may run in mode 2.
- **Design deviation:** this is the offset rule that the replay validated, not the delayed EKF state update in
  the design doc. The EKF itself (`s_ekf_of`) still dead-reckons; only the position the controller uses is
  corrected.

**Ground side:**
- `live_tracker` (6e6e0759): coral tracker, jump gate (PROPOSED), 10 Hz fix scheduler stamped with capture time,
  scrcpy window source, and a UDP sender into `stream_log --relay-port`.
- Preset `drift_rc_extpos` (d629deb1): priced, not applied. 85800 of 87552 B/s measured on the flashed ELF;
  86300 computed for the mode-2 layout.

Camera replay (`fuse4`, gain 0.1), EKF → fused, rms / 1 s-d60 (cm):

| Flight | EKF | Fused at 0.15 s | Fused at 0.3 s | Fused at 0.5 s |
|---|---|---|---|---|
| b17 hover | 30 / 19 | 4.7 / 9.2 | 5.1 / 10.3 | 5.6 / 11.5 |
| b13c | 59 / 59 | | 9.9 / 16.0 | |
| b14 | 62 / 84 | | 10.8 / 20.2 | |
| b15 | 65 / 59 | | 10.3 / 18.5 | |
| b16 | 70 / 126 | | 13.6 / 26.9 | |
| b5 | 113 / 136 | | 11.4 / 16.2 | |
| b6 | 100 / 104 | | 12.5 / 21.2 | |
| b7 | 135 / 112 | | 12.5 / 21.1 | |
| b9 | 133 / 136 | | 11.1 / 19.3 | |
| b11 | 146 / 151 | | 12.9 / 24.1 | |

- With noiseless fixes and no latency (gain 0.9): b17 1.3 cm, b5 3.1 cm. So the residual is latency, not gain or
  noise.

Live tracker replay (`live_tracker replay`, real-time, simulated link):

| Flight | Fixes | Gate rejects | Reseeds | Fused rms at 0.15 / 0.3 s | Fused 1 s-d60 | EKF rms / 1 s-d60 |
|---|---|---|---|---|---|---|
| b17 | 3557 | 9 | 0 | 5.0 / 5.4 | 10.0 / 11.1 | 30 / 19 |
| b13c | 1770 | 53 | (not logged) | 9.4 / 10.1 | 15.5 / 17.1 | 59 / 59 |
| b5 | 2004 | 29 | (not logged) | 10.6 / 11.5 | 14.9 / 16.3 | 113 / 136 |
| b10 | (not logged) | 700 | 4 | 92 (0.3 s) | 208 | 121 / 278 |

Retuned rule (gain 0.9, slew 10 cm, spread 0.1 s; `live_tracker replay`, 1 s-mean 60 s drift p90 / rms, cm):

| Flight | 0.15 s, no spread | 0.15 s, spread | 0.3 s, spread | first pass (gain 0.1, 3 cm) at 0.15 s |
|---|---|---|---|---|
| b17 hover | 4.1 / 3.5 | 4.2 / 3.6 | 6.3 / 4.5 | 10.0 / 5.0 |
| b13c | 9.1 / 8.3 | 9.7 / 8.5 | 13.4 / 10.0 | 15.5 / 9.4 |
| b5_max2 | 8.9 / 8.1 | 9.6 / 8.4 | 13.6 / 10.9 | 14.9 / 10.6 |
| b16 traj | 11.2 / 7.8 | 12.1 / 8.1 | 16.5 / 10.0 | - |

- `fuse4 --slew-cm` showed the 3 cm slew was the floor on moving flights: the offset could not keep up. Gain 0.9
  with a 10 cm slew lets each fix close most of the gap.
- The spread (no D-term kick) costs 0.1-0.9 cm. The budget holds at 0.15 s on 3 of 4 flights; b16 (fast
  trajectory, 38-40 cm/s runs) misses by 2 cm. At 0.3 s only the hover passes, so step 2 of the run sheet
  (latency) is a hard gate.
- All of this is replay and circular (camera = fixes and truth). The firmware is built and host-tested, not flown.

Tried, not adopted: an offset-rate term (`--beta`, db259a48, replay only). Between fixes the offset keeps moving
at a learned rate. Live tracker at 0.15 s, 1 s-d60 p90 cm (rms):

| beta | b16 traj | b13c | b5_max2 |
|---|---|---|---|
| 0 (adopted rule) | 12.1 (8.1) | 9.7 (8.5) | 9.6 (8.4) |
| 0.1 | 10.5 (10.0) | 12.5 (10.6) | 13.3 (10.4) |
| 0.2 | 9.0 (9.9) | 10.8 (10.7) | - |
| 0.4 | 8.9 (10.0) | - | - |

It brings b16 inside the budget, but rms rises on every flight and the moderate flights fail, so it stays off.
Synthetic fixes on b16 score 7.8 cm with no rate term, against 12.1 live, so the rest of the b16 gap is tracker
fix error at speed, not the fusion rule.

Fix rate (adopted, PROPOSED). The b16 gap was fix error at speed, so more fixes, each a median of fewer frames,
should help. `live_tracker replay --period/--n-med/--window-s`, retuned rule, 1 s-d60 p90 / rms cm:

| Flight | 10 Hz (median 3, 0.2 s), 0.15 s | 20 Hz (median 2, 0.1 s), 0.15 s | 30 Hz (no median), 0.15 s | 20 Hz, 0.3 s | 30 Hz, 0.3 s |
|---|---|---|---|---|---|
| b17 hover | 4.2 / 3.6 | 3.5 / 3.2 | 3.0 / 3.0 | 5.6 / 4.3 | 5.2 / 4.1 |
| b13c | 9.7 / 8.5 | 7.3 / 7.2 | 6.3 / 6.6 | 11.3 / 9.0 | 10.3 / 8.6 |
| b5_max2 | 9.6 / 8.4 | 7.4 / 7.2 | 6.3 / 6.3 | 11.8 / 10.0 | 10.8 / 9.3 |
| b16 traj | 12.1 / 8.1 | 7.4 / 6.5 | 6.5 / 5.8 | 12.3 / 8.7 | 11.3 / 8.1 |

- 20 Hz brings every flight inside the budget at 0.15 s; 30 Hz is 0.6-1.0 cm better again but uses one frame per
  fix (no median), so a single bad detection goes straight to the firmware. 20 Hz is the PROPOSED default
  (`live_tracker` defaults changed; score files carry `_p050n2w100`, files without it are 10 Hz).
- Fix counts on b16 at 20 Hz: 4007 used, 3 gated, 1 re-anchor, 561 "old" (12.3 %, same share at 30 Hz). "Old"
  fixes were captured before the firmware ring starts at the log start, so it is a start-of-log effect, not a rate
  effect.
- Firmware can take 20-30 Hz (read from the code, not bench-tested): the ground-station command queue is 16 deep
  and drained every 10 ms (`USER/main.c:350`); `Cmd_ExtPos` writes a single-slot mailbox and `Ext_Pos_Tick`
  (`TASK/StabilizerTask.c:872`) uses the newest fix each 5 ms tick. A burst loses old fixes, not new ones. Frames
  have no ACK, so the received rate must be measured on the bench (run sheet step 1: growth of `g_ext_pos.n_ok + n_rej + n_old`).
- Link load: one fix frame is 9 bytes, so 20 Hz is 180 B/s and 30 Hz 270 B/s before UDP/WiFi overhead (computed).
- At 0.3 s latency no rate saves the moving flights (10.3-12.3 cm), so latency stays the first gate.
- Delay setting off by 50 ms (`--delay-err`, 20 Hz, 0.15 s link), 1 s-d60 p90 / rms cm: b16 7.4 becomes 6.8 / 6.5
  (+50 ms) and 9.5 / 7.0 (-50 ms); b13c 7.3 becomes 9.4 / 7.7 (+50 ms) and 7.4 / 7.1 (-50 ms). Still inside the
  budget, with 0.5-0.6 cm left at the worst case, so the latency event must pin `--delay-ms` to within 50 ms
  (PROPOSED).

Firmware in the loop. The numbers above come from a Python copy of the fusion rule. `live_tracker replay --fw`
feeds the same live fixes to the real C code that will be flashed: `API/ekf_of.c` (decode, ring, gate, step,
spread; float32) built with gcc around `API/tests/ext_pos_replay_host.c`, which copies the `Cmd_ExtPos` dating
(`now - (idx * 10 + delay_ms)`, newest wins) and `Ext_Pos_Tick` (5 ms ticks), with fixes drained on the 10 ms
command loop. 20 Hz, 1 s-d60 p90 / rms cm, Python copy -> firmware C:

| Flight | 0.15 s | 0.3 s |
|---|---|---|
| b17 hover | 3.5 -> 3.5 / 3.2 | 5.6 -> 5.7 / 4.3 |
| b13c | 7.3 -> 7.4 / 7.2 | 11.3 -> 11.4 / 9.1 |
| b5_max2 | 7.4 -> 7.5 / 7.3 | 11.8 -> 12.0 / 10.0 |
| b16 traj | 7.4 -> 7.9 / 6.7 | 12.3 -> 12.8 / 8.8 |

- The flashable code holds the budget at 0.15 s on all 4 flights, 0-0.5 cm worse than the copy; b16 has 2.1 cm
  left. Likely causes, not separated: the 10 ms drain dates a fix up to 10 ms late (up to 0.4 cm at 40 cm/s,
  computed), the 0.5 cm wire quantum and float32.
- Fix counts match the copy except "old": fixes drained before the first control tick are not fed (b16: 3 old
  instead of 561), the start-of-log effect noted above.
- `test_firmware_path_matches_the_python_mirror` (synthetic drift, 20 Hz, one wild fix) checks the C path against
  the copy: same counts, within 1 cm. It runs wherever gcc is on the path.

- b10 is a bad track: the firmware gated 608 fixes and re-anchored 557 times. Track quality is a go/no-go check.
- Tracker cost: frame read 9-12 ms + detect 2-3 ms per frame.

## 5. Risks for the lab

| Risk | Seen in | Mitigation |
|---|---|---|
| Coral track jumps | b8, b10, b12 | run sheet step 3: track check before every flight |
| Latency above 0.15 s | b17 sweep | step 2 measures it; set `--delay-ms` |
| Fewer than 20 fixes/s received | 10 Hz replay (b16 12.1 cm) | run sheet step 1 counts fixes received per second (PROPOSED gate) |
| Circular truth | all | tape-measured landing vs take-off mark (step 5) |
| h_ring 0.15-0.17 m | b5, b6, b9, b12 | check the pad sits on id0 |
| Mode 2 never flown | firmware | shadow (mode 1) first, slew kick on the bench |
| Offset step kicks the D term | `pid.c:36` (Kd 4.0) | spread 0.5 cm/tick (18c7d964); bench check step 1.3 |

## 6. Next lab

[Run sheet R5](../lab/run-sheet-r5-fusion-bringup.md):
1. Flash.
2. Bench link and slew kick; count fixes received per second (20 or more, PROPOSED).
3. Latency event.
4. Track check.
5. Shadow out-and-back.
6. Closed-loop hover gate: PROPOSED 10 cm 1 s-mean 60 s drift p90, plus a tape-measured landing within 10 cm.
7. Only after a pass: dense trajectory presets, then the load scenarios.

## Installs, downloads, commits

- pip installs (pre-approved block): `mss` 10.2.0 (scrcpy window capture), `markdown` 3.11 (this HTML page).
- No PyTorch (the MLP is numpy), no model weights or paper code downloaded.
- Commits:
  - d1825154: landing-anchored sync + earth baseline.
  - 006d2c13: fuse4 replay.
  - e179e95f: firmware mode 2.
  - 23cfb5c2: GS EXT_POS frames + relay.
  - 6e6e0759: live_tracker.
  - d629deb1: preset.
  - 3802526d: nn4.
  - 91c7a22d: this report (first pass), run sheet, `video_truth report4`.
  - 6de83927: fuse4 `--slew-cm` (found the 3 cm slew floor).
  - a897f212: `presets4`, `yaw4`, `live_tracker replay --gain/--slew-cm`.
  - 89d40514: replay spread + explicit `_gNNN_sNNN_spNNN` score tags (untagged files are the old gain 0.1 / 3 cm).
  - 18c7d964: firmware gain 0.9 / slew 10 cm / spread (NOT flashed).
  - db259a48: offset-rate term `--beta` (replay only, not adopted).
  - de2e61d1: `live_tracker` 20 Hz fix-rate default and `_pNNNnNwNNN` tags, fix-rate table, run sheet gate.
  - 1302f1e1: `_depNNN/_demNNN` delay-error tags, delay-error check.
  - 5f32ec4c: firmware-in-the-loop replay (`--fw`, `API/tests/ext_pos_replay_host.c`, `_fw` tags).
  - (this commit): stale `test_reset_bias` assertion updated (all firmware host tests pass).
- Rebuild this page:
  `python -m ground_station.analysis.video_truth report4 docs/flights/2026-10-12-r4-overnight-report.md --out docs/flights/2026-10-12-r4-overnight-report.html`
  (`report4 -` prints tables A and B).
