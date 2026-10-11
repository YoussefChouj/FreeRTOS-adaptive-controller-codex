# Live camera fusion: design (option C), 2026-10-11

Status: PROPOSED. No code yet. Numbers marked computed come from R3 replays (`r3_drift_truth_2026-10-11.md`,
`r3_learned_velocity_2026-10-11.md`). All other numbers are design choices and must be validated in replay first.

## Goal
Bound OF/EKF position drift with the fixed phone camera, the way a mocap (Vicon) setup does it. Budget: 0.17 cm/s
hover drift. Replay (computed) with a 1 s fix period, R sd 5 cm, gives 0.17 cm/s; with R sd 1 cm it gives 0.06 cm/s.
Hold-out segments (camera tracked but not fused) keep the score honest. The same logs become the training set for the
later "drop the camera" NN.

## Data path
```
phone camera -> scrcpy -> live tracker (ground station) -> WiFi 0xCC 0xDD frame -> gs_cmd_queue -> Send_Task
  -> Cmd_ExtPos (stores fix) -> StabilizerTask: delayed earth-path correction -> earth_x/y -> loc PID FB
```

## 1. Live tracker (ground station, new `ground_station/analysis/live_tracker.py`)
- Reuse the `video_truth.py` detector and the floor pose (`cam_r3_selfcal_2026-10-11.json`) on live scrcpy frames.
- Output at 30 Hz: pad-frame position (m), capture time (frame pts), detection quality (marker area, reprojection error).
- Frame alignment camera -> EKF earth. R3 fitted this offline (`c/rot/1.094`, rotation +1.9 deg). Live options:
  (a) top marker id1 gives heading in the camera frame, so rot = heading_cam - yaw_imu (preferred; it also settles
  the 6% gz vs attitude yaw-rate question); (b) fit rot over the first 1 m out-and-back of the flight.
  The origin is the camera position at `Reset_World_Origin` (takeoff).
- Send 1 fix per second (replay best trade-off), median of the last 5 detections, and skip it if quality is poor.

## 2. Uplink frame (no new transport)
The existing 9-byte command frame `0xCC 0xDD [CMD][IDX][VALUE f32 LE][XOR]` over WiFi (USART3, same parser as UART5).
- CMD = new `EXT_POS`, using the first free id above `MRAC_ELEM_CMD_LAST` (TASK/send_data.c:1271). It must be added to
  `k_gs_cmds` and `firmware_contract.py` in the same commit, or `CommandSafetyReject` refuses it.
- IDX = fix age in 10 ms units (0-2.55 s): ground-station time from frame capture to send.
- VALUE = 4 raw bytes, int16 x_mm and int16 y_mm in the EKF earth frame (cm-scale of `earth_x/y`). One frame per
  fix keeps it atomic.
- Control: `EXT_POS` with IDX 0xFF and VALUE 0/1 switches fusion off or on. Off at boot.

## 3. Firmware update (StabilizerTask, next to `Of_IntegratePosition`)
- Ring buffer: earth_x/y every 50 ms for 1.6 s (32 x 2 floats = 256 B).
- On a fix: t_meas = now - IDX*10 ms - WIFI_DELAY_MS (fixed, measured once by round trip). Interpolate the buffered
  earth position at t_meas. Innovation e = z - earth(t_meas).
- Gate: |e| > 40 cm counts a reject and the fix is not applied. 3 consistent rejects in a row trigger a re-anchor
  (slewed, see below). This is the R1 #9 "reset after repeated rejections" rule.
- Correction (complementary PI on the earth path, NOT on EKF pos):
  - position: earth += Kp*e, and the whole ring buffer is shifted by the same amount;
  - drift rate: rotate e into body axes and add Ki*e/T_fix to the flow-bias trim (removes the hover slope);
  - Kp 0.3, Ki 0.05, T_fix 1 s are starting values (PROPOSED) to be tuned in replay.
- Slew clamp: at most 3 cm of position correction per fix, so the loc PID never sees a step.
- Stale: if there is no fix for 3 s, fall back to OF-only silently and set a telemetry flag.
- Telemetry: `ext_pos_e_x/y`, `ext_pos_n_ok`, `ext_pos_n_rej`, `ext_pos_age`.

Why not fuse into the EKF pos states: the replay that did so diverged (rms 47-89 cm, computed). EKF pos is a
body-axis integral, while the camera is an earth measurement.

## 4. Validation order
1. Replay: port section 3 exactly (ring buffer, age, gate, PI, slew) into the R3 replay and score with hold-out
   segments (fuse on t < 124 only, score t >= 124, and the reverse). Pass: hover <= 0.17 cm/s in the held-out half.
2. Host unit test (gcc golden harness): frame decode, gate, slew, stale fallback.
3. Keil build, review, flash with the operator present (rebuild_and_flash --yes).
4. Bench: drone disarmed, carry by hand under the camera, fusion on. Check the innovations stay < 5 cm.
5. R5 flight: 20 s on / 20 s off segments; the camera scores both.

## Open decisions (operator)
- Alignment: top marker id1 (needs the marker on the drone) or fit at takeoff.
- Whether the firmware may move `earth_x/y` in flight at all, or only log the innovation (shadow mode) for the first flight.
  Recommended: shadow mode first.
