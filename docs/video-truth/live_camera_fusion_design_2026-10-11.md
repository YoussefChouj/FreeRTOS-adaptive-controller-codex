# Live camera fusion: design (option C), 2026-10-11

Status: PROPOSED, sec 3 revised after replay (2026-10-11). No firmware code yet. Numbers marked computed come from R3 replays (`r3_drift_truth_2026-10-11.md`,
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
- Correction (replay-validated, see "Replay results"): an EKF position update, the o5a pattern. The innovation is
  e = z - earth(t_meas), rotated into body axes with the current yaw, and fed to `ekf_of_update_one` on the pos states
  (H = [1,0,0,0], R sd 1 cm). bof and vel move only through the covariance. No separate Ki term.
- Slew clamp: at most 3 cm of position correction per fix, so the loc PID never sees a step.
- Stale: if there is no fix for 3 s, fall back to OF-only silently and set a telemetry flag.
- Telemetry: `ext_pos_e_x/y`, `ext_pos_n_ok`, `ext_pos_n_rej`, `ext_pos_age`.

## Replay results (R3, computed, 2026-10-11; scripts `docs/video-truth/scripts/fuse*.py`)
| Variant | Hover 72-124 s rms / max (cm) | Moves 124 s+ rms / p90 (cm) |
|---|---|---|
| No fusion | 10.9 / 22.8 | 31.0 / 50.1 |
| EKF update, 1 s, R 1 cm | 4.7 / 17.8 | 16.5 / 25.7 |
| EKF update, 1 s, R 5 cm | 6.1 / 22.5 | 20.4 / 31.4 |
| EKF update, 2 s, R 1 cm | 5.3 / 21.1 | 17.5 / 28.0 |
| EKF update, 5 s, R 1 cm | 5.9 / 19.1 | 24.7 / 36.8 |

- Hold-out (camera fused in "on" segments, scored in "off" segments) learns no lasting trim. The median drift in
  off windows stays the same: 20 s windows 1.49 cm/s with no fusion vs 1.43-1.52 fused; hover-only 10 s windows
  0.93 vs 0.90-0.92. The camera-off error in R3 is wander born in moves, not a constant bias. So the 0.17 cm/s
  budget holds only with the camera in the loop, and the "drop the camera" NN has no stationary target in one flight.
- The earth-path PI first proposed here (Kp 0.3, Ki 0.05) is worse. Kp only: rms 19.7, off drift 1.36 cm/s.
  Ki >= 0.05 diverges (rms 40-3000): the integrator winds up behind the 3 cm slew clamp.
- A 40 cm gate changes nothing in R3 (no camera outliers).
- In moves, most of the remaining error was a timestamp offset, not estimator lag. `camlag.py` cross-correlates
  camera body velocity (moves, t >= 124 s) with raw flow of2_fix and with the EKF velocity. Raw flow trails the camera
  by 0.22 s (x) / 0.28 s (y), corr 0.80 / 0.74. The EKF trails it by 0.28 / 0.32 s. The physical flow delay is
  only ~32 ms, so the R3 sync (`video_t = tel_t + 15.05`) is about 0.25 s off (truth at telemetry time t = camera at
  t - 0.25 s), or the telemetry stamps carry a link delay. The EKF itself adds only ~0.05 s on top of raw flow.
- Re-run with the 0.25 s shift (`fuse4.py <o1> 124 0.25`):

| Variant (sync shifted 0.25 s) | Hover rms / max (cm) | Moves rms / p90 / max (cm) |
|---|---|---|
| No fusion | 14.4 / 34.1 | 28.7 / 54.6 / 66.9 |
| EKF update, 1 s, R 1 cm | 4.3 / 17.7 | 11.7 / 18.1 / 36.3 |
| EKF update, 2 s, R 1 cm | 4.8 / 20.9 | 12.0 / 19.0 / 34.6 |
| EKF update, 5 s, R 1 cm | 5.1 / 16.6 | 21.2 / 31.7 / 45.1 |

- Live consequence: the camera-to-firmware delay must be measured, not assumed. R4: one sharp event (a tap on the
  frame or an LED) seen by both the camera and telemetry fixes the sync. WIFI_DELAY_MS and the tracker age follow.

The earlier claim that EKF-pos fusion diverges (rms 47-89 cm) does not hold for this pattern: the innovation is
taken against the buffered earth path at capture time and rotated into body axes.

## 4. Validation order
1. Replay: done (see "Replay results"). The hold-out pass (camera off, <= 0.17 cm/s) FAILS on R3; camera-on passes.
2. Host unit test (gcc golden harness): frame decode, gate, slew, stale fallback.
3. Keil build, review, flash with the operator present (rebuild_and_flash --yes).
4. Bench: drone disarmed, carry by hand under the camera, fusion on. Check the innovations stay < 5 cm.
5. R5 flight: 20 s on / 20 s off segments; the camera scores both.

## Open decisions (operator)
- Alignment: top marker id1 (needs the marker on the drone) or fit at takeoff.
- Whether the firmware may move `earth_x/y` in flight at all, or only log the innovation (shadow mode) for the first flight.
  Recommended: shadow mode first.
