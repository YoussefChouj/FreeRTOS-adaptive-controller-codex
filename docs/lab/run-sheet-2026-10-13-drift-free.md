# Run sheet 2026-10-13: drift-free mode 2 and mode 3

Goal: hold position hands-off in modes 2 and 3 with the camera fusion. Each flight gets a pass or fail from the
camera truth. Numbers marked PROPOSED have not been measured yet. Measured inputs: `docs/agent/nightly-2026-10-13.md`.

## What changed since r6 (tracker only, no new firmware)

| Change | Why | Measured on |
|---|---|---|
| Coral scenery mask learned at start (`--coral-static auto` = start when live) | Live fixes stuck on static coral spots | r6_f4 replay error p50 272 -> 7.6 cm |
| `--delay-ms 200` sent by camera_truth.sh | Camera lag 0.21 s | r6_f4, r6_f4c (`video_truth delay4`) |
| `--align auto` in modes 2/3 | r6 flew `--yaw-deg 0` against a ~90 deg yaw | r6 truth4 fits |

## Before the session

1. Stop the 8081 service: stream_log needs the WiFi downlink.
2. Put the phone where r5_marks_1 had it, because its floor pose is reused.
3. Drone powered, disarmed, on the pad, in view.

## Each flight (mode M = 2 or 3)

1. **Start the session.** Drone on the pad, disarmed. Confirm the name and mode with the operator in chat.
   ```bash
   bash ground_station/lab/camera_truth.sh fusion r7_f<k>_mode<M> <M>
   ```
2. **Wait for the mask.** Keep the drone on the pad for at least 8 s after the start (PROPOSED: the mask needs
   about 5 s of frames). `tracker.out` prints `coral static learned: N px`. If it does not, stop and restart.
3. **Take off and do the align manoeuvre.** Hover at about 1 m, then fly slow lines over at least 80 cm for up to
   30 s. Wait until the tracker shows `align LOCK <deg> deg -> mode M`. Expect about 90 deg (r6). Before the lock
   the fusion stays in mode 1.
4. **Hands-off test.** Centre the sticks and let go for 60 s. Note the start time.
5. **Land, disarm**, then run:
   ```bash
   bash ground_station/lab/camera_truth.sh stop r7_f<k>_mode<M>
   ```

If the drone drifts away during the hands-off, take over by RC, land and stop. A fight with the sticks is a fail,
and the log still counts as data.

## Pass or fail (after landing)

```bash
python -m ground_station.analysis.video_truth truth4 r7_f<k>_mode<M> --stem logs/r7_f<k>_mode<M> --floor logs/video_truth/r5_marks_1 --fixes --no-reflect
```

```bash
python -m ground_station.analysis.video_truth handsoff4 r7_f<k>_mode<M> --min-s 10
```

| Result | Mark (PROPOSED) | r6 baseline (measured) |
|---|---|---|
| Pass | Truth displacement < 20 cm over a 60 s hands-off window | f2 mode 2: 60 cm / 36.7 s; f3b mode 3: 50 cm / 10.4 s |
| Fail | >= 20 cm, or a hands-off window shorter than 60 s because the operator had to step in | |

## If it fails

| Symptom | Check | Fallback |
|---|---|---|
| Fixes frozen while the drone moves | Stuck 5 s windows in fixes.csv (nightly item 1) | Restart with the drone on the pad longer |
| Slow circular drift | Lock angle vs about 90 deg | Pass the angle by hand: `--yaw-deg <deg>` |
| Lag or oscillation in mode 2/3 | | `DELAY_MS=0 bash ground_station/lab/camera_truth.sh fusion ...` |
