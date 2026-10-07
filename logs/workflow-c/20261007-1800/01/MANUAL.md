# Step B: run it offline (2026-10-07)

Both tests log the same variables (core + estimator_truth + optical_flow + velocity_loops + takeoff_gate, incl.
locx/locyPID.Des, RPM x4, of modes/quality). WiFi only, no probe, no internet needed. All numbers PROPOSED.

## Test 1: handheld carry (drone DISARMED, props on or off, never armed)

```
python -m ground_station.service.campaign_fly logs/campaigns/launch/wfc-stepb-steps_20261007-1321.yaml --pack P4000-1 --record-only carry --rate 100
```

1. Phone LANDSCAPE, calibration focus. Start the video first.
2. Run the command; wait for `RECORDING ->` and both slots `fresh`.
3. Sync cue: lift the drone sharply 20 cm off the pad and back down (visible on video and in accel).
4. Carry at 0.5-0.8 m: +x 0.5 m, back; -x; +y; -y; one slow circle. ~30-40 s. Keep it level, do not rotate it.
5. Put it back on the pad, still 3 s. Enter (or Ctrl+C) stops the log. Stop the video.

No command reaches the drone: only stream slots and the recorder.

## Test 2: step-setpoint flight (4 x 0.5 m out-and-back, then land on the pad)

```
python -m ground_station.service.campaign_fly logs/campaigns/launch/wfc-stepb-steps_20261007-1321.yaml --pack P4000-1 --run logs/workflow-c/20261007-1800
```

1. Preflight prints; fix any FAIL and press Enter. Start the video. Arm by RC, type `go`.
2. Flight: take off 0.8 m, 3 s hold, then +x, -x, +y, -y steps (each 0.5 m out, 3 s dwell, back), 3 s hold, land.
   Each step uploads first (~6-9 s hover each). Airborne about 100 s of the 120 s cap.
3. Stops: Ctrl+C once = land, twice = abort; RC ch10 = kill (primary).
4. After landing it runs the debrief into this run folder. Stop the video.

Landing: every goto returns to the hover point above the pad (firmware rule), so it always lands from the pad
hover. Landing offset = video touchdown vs pad; drift = last hold position vs touchdown.

Bring back: both videos + the two `logs/sessions/...` folders the commands print.
