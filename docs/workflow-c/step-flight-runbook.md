# Step C flight runbook: manual prompts, flight to research

The default workflow-C test flight is the step C campaign `ground_station/service/campaigns/wfc_step_c.yaml`
(operator 2026-10-07).

You fly it yourself from a terminal: one `campaign_fly` command, the preflight, arm by RC, type `go` (operator
2026-10-07). Claude only prepares the launch copy and the command, then does the analysis. The other stages have a
prompt to paste into the desktop Claude session; fill in the `<...>` parts.

## The flight (PROPOSED timings)

| # | step | why |
|---|---|---|
| 1 | take off to 0.8 m, 3 s settle | settle |
| 2 | 15 s still hold, sticks untouched | the "drifts when it sits still" observation, against video |
| 3 | one stop-and-go path: pad -> c = (0, -0.5), then 0.5 m steps +x, -x, +y, -y around c (3 s dwell, 1 s at c), back to the pad | estimator scale, lag and frame vs video; centred on y = -0.5 because the camera sees only about +0.4-0.5 m of +y from the pad |
| 4 | land | landing offset vs the pad |

- Firmware trajectories start and end at the pad hover, so the steps are one `waypoints` path with `dwell_s`
  (a full stop and a hold on every waypoint): one upload instead of four. Each upload cost about 6-9 s of hover
  in step B. The -y step goes to (0, -1.0): check it is in the video frame; if not, use (0, -0.8).
- Airborne cap: the firmware lands via hover at 120 s. Step B flew 94.3 s against a 73.1 s schedule estimate;
  step C's schedule is 88.7 s (path 39.7 s), so about 94-110 s projected (PROPOSED). Diagonals and a climb did not fit: a
  later flight.
- Max |x| 0.5 m, |y| 1.0 m and z 0.8 m, all inside the soft fence (z 1.4, |x| 1.3, |y| 1.7).

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

## 1. Get the command

```text
Prepare the next step C flight for me to launch by hand, pack <P4000-1>: launch copy, MANUAL.md, the command.
```

Claude runs `campaign_launch` on `wfc_step_c.yaml`, writes `logs/workflow-c/<run>/01/MANUAL.md` and gives you:

```text
python -m ground_station.service.campaign_fly logs/campaigns/launch/wfc-stepc-steps_<stamp>.yaml --pack P4000-1 --run logs/workflow-c/<run>
```

## 2. Fly it (your terminal)

1. Run the command. Preflight prints; fix any FAIL, press Enter.
2. Start the video. Arm by RC. Type `go`.
3. Stops: Ctrl+C once = land, twice = abort; RC ch10 = kill (primary).
4. After landing it runs the debrief into the run folder. Stop the video.

## 3. After landing: debrief

```text
Landed. Read the debrief campaign_fly wrote into logs/workflow-c/<run> and send me debrief.md and both plots.
Bottom line first.
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
2. compare the logged s_ekf_of.x (px, py) with truth: rms, end error, the 15 s still-hold drift rate (cm/s);
3. settle the axis sign and yaw offset from the four steps (B gave +90.9 deg, A +108.6 deg), and say what the
   presets need. Every proposed number PROPOSED. Send the plots.
```

Claude's replay command:

```text
python -m ground_station.analysis.ekf_of_replay truth --pair logs/sessions/<session> logs/video_truth/stepc-flight-<HHMM>/truth.csv \
  --q-bof 1e-5 --r-of1 1e-3 --arm-var 0 --plot logs/video_truth/stepc-flight-<HHMM>/ekf_replay.png
```

## 6. Thrust model

```text
Fit the thrust model on the step C log: k_T from m(g+a_z)/cos(tilt) vs sum rpm^2 over the hover, holds,
takeoff and landing; how g_thrust_est (empirical, blade element, imu_total) tracks the weight vs real_voltage; and the residual
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
   being visible; zero-velocity updates; what the step C 15 s hold shows.
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
