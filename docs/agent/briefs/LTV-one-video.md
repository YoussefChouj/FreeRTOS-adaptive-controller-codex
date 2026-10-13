# LTV: one live video with the tracking overlay

Operator request (2026-10-13): "now there are two videos, one pure video and one small window with tracking that I can
expand. Make it one video stream from now on with the overlay of the tracking position and other interesting metrics
that I can expand or unexpand."

## Context

- `ground_station/lab/camera_truth.sh fusion` starts scrcpy (`--window-title=r5cam --always-on-top --record`) and
  `ground_station/analysis/live_tracker.py --source window:r5cam ... --show`.
- `WindowSource` (live_tracker.py ~line 205) grabs the scrcpy window's screen pixels with mss, so the scrcpy window
  must stay visible and uncovered. `Preview` (~line 388) is a second, small cv2 window (WINDOW_AUTOSIZE) placed beside it.
- Measured on r7_f3_mode2: read_ms 15.9, detect_ms 1.1, show_ms 13.4 per frame.

## Step 1: research first (quote sources: repo file/line, docs or issue URL)

How can live_tracker get the phone frames on Windows without a visible scrcpy window, so only one window is shown?
Compare at least: scrcpy `--no-window` with recording (scrcpy 2.x/3.x docs), decoding the scrcpy server stream in
Python (py-scrcpy-client / PyAV), `adb exec-out screenrecord` piped to a decoder, win32 PrintWindow on a covered window.
For each: latency vs the current mss grab, whether `cam.mkv` is still recorded for `video_truth truth4`, extra deps.
Recommend one. Mark anything not measured UNVERIFIED.

## Step 2: build (extend live_tracker.py and camera_truth.sh, no one-off scripts)

- One resizable window (cv2 WINDOW_NORMAL or equivalent) showing the full video with the overlay: tracked ring, pad-frame
  xy, mode, height, fixes sent, detections, gate rejects, align state.
- A key toggles the metrics panel between compact and expanded. Keep the window always on top if the source allows.
- camera_truth.sh `fusion`: no second video window. `video` and `start` modes keep working as today.
- Keep `--source window:` working (fallback). Scoped pytest for the new source/overlay logic with fakes; no phone.

## Rules

- Do not touch port 8081, do not flash, do not use the SWD probe, never arm.
- Never write a number you did not measure. Mark guesses PROPOSED or UNVERIFIED.
- Commit only your files; never `git add -A`; no logs or binaries.

## Report

`.agent-ops/out/LTV.md`: research answers with quotes and the recommendation, files changed, test output, how to run it.
