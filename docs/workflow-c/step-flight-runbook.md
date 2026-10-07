# Step C flight runbook: manual prompts, flight to research

The default workflow-C test flight is the step C campaign `ground_station/service/campaigns/wfc_step_c.yaml`
(operator 2026-10-07).

Each stage below has a prompt to paste into the desktop Claude session. Paste them in order and fill in the
`<...>` parts. Claude runs the commands; you only arm, film and answer questions.

## The flight (PROPOSED timings)

| # | step | why |
|---|---|---|
| 1 | take off to 0.8 m, hold 3 s | settle |
| 2 | 0.5 m steps +x, -x, +y, -y (3 s dwell, 1 s holds) | estimator scale, lag and frame vs video (same as step B) |
| 3 | diagonals (+0.35, +0.35) and (-0.35, -0.35) | axis sign, yaw offset, x/y cross-coupling |
| 4 | climb to 1.1 m, 3 s dwell | flow scale vs height; k_T during a thrust change |
| 5 | 20 s still hold at 0.8 m | the "drifts when it sits still" observation, against video |
| 6 | land | landing offset vs the pad |

- Every goto returns to the hover point. Each upload costs about 6-9 s of hover (step B).
- Flight time is roughly 2.5 min (PROPOSED, not measured).
- Max |x|, |y| 0.5 m and z 1.1 m, all inside the soft fence (z 1.4, |x| 1.3, |y| 1.7).

The log runs at 50 Hz with 113 variables in 2 slots: 47,600 of 70,042 B/s (68 %), computed by `log_plan_table`.

| group | vars | what for |
|---|---|---|
| core | 41 | motors, attitude, rate loops, loc FB/Des, voltage, WFB, EKF health |
| estimator_truth | 22 | of0/of1/of2 flow, module gyro/acc, RPM x4, FC gyro/acc |
| optical_flow | 5 | flow quality, height |
| velocity_loops | 9 | locxs/locys velocity PIDs |
| takeoff_gate | 2 | takeoff gate |
| thrust_model | 16 | Throttle_out/th, Z pos/rate U, u_gyro x/y/z, g_thrust_est per-motor thrust + imu_total |
| ekf_states | 18 | s_ekf_of.x[0..7], innovations, rejects, fallback, bias mode, P_vv/P_bof |

If trajectory uploads lag, choose 40 Hz at the Q3 question (about 38,100 B/s, 54 %).

## 0. Before you start (bench)

- Charged pack; drone on the pad; dashboard up; WiFi link green.
- Phone in **landscape**, same spot and focus as the ChArUco calibration, whole fence in frame.
- Start filming **before** takeoff (the video is synced on takeoff).
- If the camera moved since step B, say so in prompt 5: the floor marks and survey must be redone.

## 1. Start the session

```text
/workflow-c Step C flight, pack <P4000-1>. Use ground_station/service/campaigns/wfc_step_c.yaml as flight 01.
Show me the log plan table and ask Q3; I am filming in landscape from the calibration spot.
```

At Q3, pick **Approve (Recommended)**, or 40 Hz if the last flight's uploads were slow.

## 2. Preflight, arm, go

Claude runs the preflight and says "Arm by RC when ready, then say go". Arm by RC, then type exactly:

```text
go
```

During the flight, say `land` to land now, or `abort` to abort. Claude never arms or spins the motors.

## 3. After landing: debrief

```text
Landed. Run the flight debrief on this campaign and send me debrief.md and both plots. Bottom line first.
```

## 4. Hand over the video

```text
The video is at <D:/Downloads/VID_2026xxxx_xxxxxx.mp4>. Camera <did not move | moved> since step B.
Run video_truth on it against this flight's telemetry (same cam calib, marks and survey as stepb-flight-1339
if the camera did not move) and send me truth.png and the report numbers.
```

Claude's command (camera not moved):

```text
python -m ground_station.analysis.video_truth run <video> --cam docs/video-truth/cam_charuco_landscape_2026-10-07.json \
  --marks logs/video_truth/stepb-flight-1339/marks.json --csv logs/sessions/<session>/telemetry.csv \
  --out logs/video_truth/stepc-flight-<HHMM> --min-area 8 --hsv-lo 170,120,100 --hsv-hi 6,255,255 \
  --static-t <t1,t2> --survey logs/video_truth/stepb-flight-1339/survey.json
```

## 5. Validate the two-channel EKF against the video

```text
Validate build 0b0c2b0b against the step C video truth:
1. ekf_of_replay truth on this pair at the flashed config (q_bof 1e-5, r_of1 1e-3, arm-var 0) with a plot;
2. compare the logged s_ekf_of.x (px, py) with truth: rms, end error, the 20 s still-hold drift rate (cm/s);
3. settle the axis sign and yaw offset from the diagonals (B gave +90.9 deg, A +108.6 deg), and say what the
   presets need. Every proposed number PROPOSED. Send the plots.
```

Claude's replay command:

```text
python -m ground_station.analysis.ekf_of_replay truth --pair logs/sessions/<session> logs/video_truth/stepc-flight-<HHMM>/truth.csv \
  --q-bof 1e-5 --r-of1 1e-3 --arm-var 0 --plot logs/video_truth/stepc-flight-<HHMM>/ekf_replay.png
```

## 6. Thrust model

```text
Fit the thrust model on the step C log: k_T from m(g+a_z)/cos(tilt) vs sum rpm^2 over the hover, holds and the
climb; how g_thrust_est (empirical, blade element, imu_total) tracks the weight vs real_voltage; and the residual
between the commanded moments (u_gyro x/y/z) and the achieved angular acceleration. Extend thrust_replay rather
than writing a script.
```

## 7. Launch the research trail (before you leave)

```text
Launch the research trail now, before I leave (I will be offline). Use the external agy workers and Ark doubao
search, VPS lane first, maximum effort and context; no Claude subagents. One worker per trail, each must quote its
sources (paper, section, number). Confirm every worker is running (WSL/VPS process check, not the launcher's word)
before I go. Trails:
1. Stationary drift: which EKF states (flow bias, accel bias, velocity) are unobservable at zero velocity; persistent
   excitation; an onboard sub-centimetre dither (amplitude, frequency, axis) that keeps them observable without
   being visible; zero-velocity updates; what the step C 20 s hold shows.
2. Learned inertial odometry from IMU + RPM/PWM: TLIO, RoNIN, AI-IMU, Cioffi 2023 learned inertial odometry for
   drone racing, NeuroBEM, DIDO and newer; accuracy numbers, model size, whether any fits an STM32F4 (or the
   ground station in the loop).
3. Thrust vector and rotor drag: k_T and drag-coefficient estimation; the rotor-drag accelerometer model as a
   velocity observation (Leishman 2014; Martin and Salaun 2010); the commanded vs achieved force/moment residual
   as a disturbance signal.
4. Disturbance observers: MRAC, L1 adaptive, INDI, ESO/ADRC, momentum-based wrench observers; which estimates a
   slow position-drift disturbance best on this hardware and our MRAC code.
5. Anything else that attacks the return-to-pad and preset-trajectory errors: optical-flow scale vs height, gyro
   compensation, floor texture, ArUco/AprilTag absolute fixes at the pad, UWB.
When the findings land, verify the key claims yourself against the sources and our logs, then write
docs/research/2026-10-07-drift-trail.md with a ranked list of experiments we can fly next.
```

## 8. When you are back

```text
I am back. Status of every research worker, then the verified digest and the ranked next experiments.
```
