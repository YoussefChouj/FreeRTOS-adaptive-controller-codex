---
name: camera-truth
description: Start/stop a camera-truth lab session in one command - 8081 service, drift_rc WiFi stream log, phone camera video (scrcpy, R3 pose). Use when the operator is at the lab and says start logging / capturing video / stop.
---

# Camera-truth capture (workflow A + phone video)

One script does everything. Do not grep docs or transcripts for commands.

```
confirm (1 question) -> start <name> (bg) -> "recording, fly" -> operator says stop -> stop <name> -> check
```

1. **Confirm once** (AskUserQuestion): drift_rc preset (slots 1-3 at 100/50/25 Hz, slot 0 default 25 Hz),
   session name `drift_rc_r<N>_b<battery>`. Drone disarmed. Phone on USB, camera in the R3 pose.
2. **Start:** `bash ground_station/lab/camera_truth.sh start <name>`
   (starts the 8081 service if down, applies drift_rc, stream log unlimited, recording start
   requested_by operator, scrcpy camera 4 zoom 0.6 1080p30 -> `logs/video_truth/<name>/cam.mkv`,
   host start time in `scrcpy_start_host_time.txt`). It prints rows per slot and video size after 5 s.
3. Tell the operator "recording, start flying" plus the battery steps from the run sheet
   (`docs/lab/run-sheet-2026-10-11-r4-data.md`): 3 taps, 5 s still, hover, out-backs, yaw, land, 3 taps.
4. **Stop** on the operator's word (landed, disarmed): `bash ground_station/lab/camera_truth.sh stop <name>`.
   `check <name>` any time shows rows and video size.
5. After the last battery: a ChArUco lens wave, `start lens_wave_<date>` / `stop`, then tape measurements.

Phone (Xiaomi 2510ERA8BC, Android 16): turn OFF "Keep screen on while viewing" (screen attention) and set screen timeout to the max. At screen timeout com.xiaomi.aon AonAttentionService opens the camera and evicts scrcpy (R4 b1 drop 19:00:44, logcat measured).

Rules: WiFi only, never the SWD probe; never arm; record until "stop", no timer. Top marker: DICT_4X4_50 id1,
measured black square 77 mm (use 0.077 m). Pad marker id0.
