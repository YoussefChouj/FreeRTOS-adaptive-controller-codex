# Forensics brief lab1018-tracker-bias: camera-tracker position bias regression (lab 2026-10-18)

Read-only. No edits to tracked files, no commits, never POST to 8081 or touch the drone/probe/camera.
You may run read-only Python (OpenCV is installed) over videos and logs; scratch only under
`.agent-ops/out/scratch-lab1018-cam/`. 160K token ceiling (GEMINI.md rule 10). Do not decode whole videos
frame by frame into your context: sample frames, print numbers. Output: `.agent-ops/out/argon-lab1018-tracker-bias.md`,
at most ~2000 words. Every claim cites a file:line, log line or a number you computed. Mark guesses UNVERIFIED.

## Symptom

Operator: 2-3 days ago video tracking and position correction were fine; on 2026-10-18 the tracked dot sits
offset from the drone with a steady bias, the drone drifts on lift-off (mem1 0.31 m sideways), and fixes pull it
the wrong way. The landing zoom (632c6a32, land_zoom 2.0) was turned off today (d2879e1d) after it lost the
drone at mem2 lift-off; the bias was still there after that.

## Evidence

- Today: `logs/video_truth/{floor-1018,m1-u100,mem1..mem6-lab-m-memory-*,z1-lab-z-ground-effect}/`
  (tracker.mp4 / cam*.mkv / tracker logs / fixes). Note floor-1018 = a new floor scan at 12:16 today.
- Last good: find the newest session folder under `logs/video_truth/` from 2026-10-15..10-17 where fixes were
  sent and flights tracked well (check its fix log / docs/lab or HANDOFF notes). Say which one you picked and why.
- Code: ground_station/analysis/live_tracker.py, ground_station/lab/camera_truth.sh (CT_FLOOR floor pose,
  default r5_marks_1), ground_station/comm/ext_pos_encoder.py, floor/marker calibration files it loads.
- History: `git log --since=2026-10-14 -p -- ground_station/analysis/live_tracker.py ground_station/lab/camera_truth.sh
  ground_station/comm/ext_pos_encoder.py` and any calibration/floor file. Commits to inspect first: 632c6a32,
  a83b9a35, 81fbebd1 (Jules PR #11, A/B replay + zoom flags), 281f60aa, d2879e1d, 58c76c23 (cam touchdown offset D2/B2).

## Questions

1. **What changed** between the last good session and today, in code, defaults, flags passed by camera_truth.sh,
   floor pose / calibration file, and camera placement (compare the floor scans: marker pixel positions, solved
   pose). Rank by how well each explains a steady offset.
2. **Measure the bias.** On a few hover segments today vs the good session: tracked pixel vs the drone's visible
   body centre (sample frames, describe how you located it), and the world-frame offset implied. Is it constant
   in pixels, constant in metres, height-dependent (parallax: wrong assumed height plane / touchdown offset),
   or direction-dependent (yaw / axis sign)?
3. **Fix path.** For each candidate cause, the exact code site and a replay test that proves the fix: re-run the
   tracker headless (the A/B replay flags from PR #11) on a today cam mkv with the old vs new setting and compare
   the dot to the body centre.
4. **Fixes not sent / tracker restarts.** Why some sessions show tracker "restarts 3" and 0-byte cam_N.mkv files.

## Deliverable shape

1. Bottom line: 5 lines max. 2. Change table (old vs today, evidence, verdict). 3. Bias numbers. 4. Fix plan with
replay acceptance test. 5. UNVERIFIED items.
