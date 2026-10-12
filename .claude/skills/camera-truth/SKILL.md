---
name: camera-truth
description: Start/stop a camera-truth lab session in one command - 8081 service, drift_rc WiFi stream log, phone camera video (scrcpy, R3 pose); also the live camera fusion session (modes 1-3, tracker + live video). Use when the operator is at the lab and says start logging / capturing video / fusion / mode 2 / mode 3 / stop.
---

# Camera-truth capture (workflow A + phone video)

One script does everything. Do not grep docs or transcripts for commands.

```
confirm (1 question) -> start <name> (bg) -> "recording, fly" -> operator says stop -> stop <name> -> check
```

1. **Confirm once** (AskUserQuestion): drift_rc preset (slots 1-3 at 100/50/25 Hz, slot 0 default 25 Hz),
   session name `drift_rc_r<N>_b<battery>`. Drone disarmed. Phone on USB, camera in the R3 pose.
2. **Start:** `bash ground_station/lab/camera_truth.sh start <name> [preset]` (preset = a file in `ground_station/vofa_studio/presets/`, default `drift_rc`; `drift_rc_xy` adds the x/y MRAC layers, `drift_rc_max` logs all 6 MRAC axes at 84600/87552 B/s incl. slot0, every live weight)
   (starts the 8081 service if down, applies drift_rc, stream log unlimited, recording start
   requested_by operator, scrcpy camera 4 zoom 0.6 1080p30 -> `logs/video_truth/<name>/cam.mkv`,
   host start time in `scrcpy_start_host_time.txt`). It prints rows per slot and video size after 5 s.
3. Tell the operator "recording, start flying" plus the battery steps from the run sheet
   (`docs/lab/run-sheet-2026-10-11-r4-data.md`): 3 taps, 5 s still, hover, out-backs, yaw, land, 3 taps.
4. **Stop** on the operator's word (landed, disarmed): `bash ground_station/lab/camera_truth.sh stop <name>`.
   `check <name>` any time shows rows and video size.
5. After the last battery: a ChArUco lens wave, `start lens_wave_<date>` / `stop`, then tape measurements.

Phone (REDMI Note 15 Pro+, HyperOS 3): Settings page "Sleep" -> turn OFF "Screen attention" (secure adaptive_sleep; check `adb shell settings get secure adaptive_sleep` = 0). Shortcut: `adb shell am start -a android.settings.SCREEN_TIMEOUT_SETTINGS` opens the page; the OPERATOR taps the toggle. It opens the front camera (com.xiaomi.aon) and evicts scrcpy for ~18 s: R4 b1 drop 19:00:44, b4 drop 19:20:46 (logcat measured).

Rules: WiFi only, never the SWD probe; never arm; record until "stop", no timer. Top marker: DICT_4X4_50 id1,
measured black square 77 mm (use 0.077 m). Pad marker id0.

## Fusion session (live camera position into the firmware, modes 1-3)

```
stop 8081 -> confirm name + mode (1 question) -> fusion <name> <M> (bg) -> pad wait -> align LOCK -> hands-off -> stop <name>
```

| Mode | What the drone does with the camera fixes |
|---|---|
| 0 | off (no EXT_POS frames) |
| 1 | shadow: logged only, flight unchanged |
| 2 | position offset outside the EKF |
| 3 | fix inside the EKF |

1. **8081 must be down** (the script refuses otherwise: stream_log needs the WiFi downlink).
2. **Start:** `bash ground_station/lab/camera_truth.sh fusion <name> <M> [tracker args]`. One command starts:
   stream_log (`r5_extpos_frames.md`, relay 47800), the scrcpy window `r5cam` (always on top, recorded to
   `logs/video_truth/<name>/cam.mkv`) and `live_tracker --show` (overlay window, fixes to `fixes.csv`, log
   `tracker.out`). Defaults: `--delay-ms 200` (`DELAY_MS=0` reverts), `--align auto` for M >= 2 unless
   `--yaw-deg`/`--align` is passed. Phone as in r5_marks_1 (its floor pose is reused).
3. **Live video is always on**: the scrcpy window and the tracker overlay. If either is missing, `check <name>`
   and read `scrcpy.log` / `tracker.out` before telling the operator to fly.
4. **Mode switch** = new session: the mode is fixed per session (with `--align auto` the drone stays in mode 1
   until `align LOCK`). Land, disarm, `stop <name>`, then `fusion <name2> <M2>`.
5. Flight steps, pass/fail and fallbacks: the latest `docs/lab/run-sheet-*-drift-free.md`.
6. **Stop:** `bash ground_station/lab/camera_truth.sh stop <name>` ends all three cleanly (stop file).
