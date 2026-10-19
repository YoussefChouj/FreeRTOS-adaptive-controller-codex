# Brief lab1018-bias2 (narrow, 15 min budget): why the camera fix is offset on 2026-10-18

Read-only. No edits to tracked files, no commits, never touch 8081/drone/camera. Scratch only under
`.agent-ops/out/scratch-bias2/`. 160K token ceiling. Do NOT decode videos. Output `.agent-ops/out/argon-lab1018-bias2.md`,
max ~900 words, every claim with file:line or a number you computed; guesses marked UNVERIFIED.

Facts: labday passes `CT_FLOOR=logs/video_truth/<day file camera.floor>` to camera_truth.sh fusion (default
r5_marks_1); today's day file used `floor-1018` (a new floor scan at 12:16). Fusion passes `--floor $CT_FLOOR` and
`--align auto` (mode >= 2) to ground_station/analysis/live_tracker.py.

Answer only:
1. Which floor folder did each 10-18 session use (grep `logs/video_truth/{mem1..mem6,z1}-*/tracker.out` or the
   tracker's start line for floor/align/yaw args) and which floor the last good session (newest 10-15..10-17 folder
   with a non-empty fixes.csv) used.
2. Diff the two floor scans' calibration outputs (whatever JSON/npz/csv live_tracker loads from the floor folder:
   homography / pose / marker pixels / scale / yaw). Give the numbers side by side and the implied world offset at
   the takeoff spot (x, y in m).
3. What `--align auto` does (live_tracker.py file:line) and whether it can lock a constant offset at start-up.
4. Any `git log --since=2026-10-14` change to live_tracker.py / camera_truth.sh defaults that shifts the fix
   (hash + one line each).
Bottom line first (3 lines): most likely cause and the one-line fix site.
