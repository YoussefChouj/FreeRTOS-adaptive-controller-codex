# Forensics brief lab1018-bias4: why the tracked dot sits off the drone body (lab 2026-10-18)

Read-only. No edits to tracked files, no commits, never POST to 8081 or touch the drone/probe/camera.
Scratch only under `.agent-ops/out/scratch-bias4/`. Output: `.agent-ops/out/argon-lab1018-bias4.md`, at most
1500 words. 160K token ceiling.

**HARD RULE: do NOT spawn subagents, background agents or parallel tasks. Do every step yourself, in this one
run, sequentially. Two earlier runs died waiting on a subagent and produced nothing.** If a step is slow, cut its
sample size instead. Write the output file early (a skeleton) and fill it in as you go, so a cut-off run still
leaves results.

## Symptom

Operator, 10-18: in the tracker window/video the tracked dot sits offset from the drone with a steady bias, and the
fixes pull the drone the wrong way. On 10-15 (sessions 20261015-22*, video_truth dirs from that evening, e.g. the
f1/f1b robust flights) it was fine. Telemetry already shows: per flight, fix minus raw optical-flow position has a
steady offset of about x -30 cm, y +30 cm, present at lift-off, residual yaw only -3..-14 deg, scale 1.0-1.2
(`.agent-ops/research/nb/bias3/summary_b.txt`). Each 10-18 run has its own tracker process and pad origin
(pad_xy about [-0.22, -0.05] vs [-0.07, 0.005] on 10-15), so a frozen origin across flights is ruled out.

## Do, in order

1. **Pixel offset (the main number).** For 3 of today's runs (`logs/video_truth/m1-u100`, two of `mem*-lab-m-memory-*`)
   and 1 good 10-15 run: sample ~10 frames each from tracker.mp4 (or cam*.mkv) on the ground before take-off and
   ~10 in hover. Find the drone body centre yourself (describe the method: e.g. the frame difference/blob of the
   drone body, or the centre of the propeller disk) and the tracker's dot (its drawn ring or its logged uv in the
   run's fix/tracker log). Report dot minus body in pixels and in metres, ground vs hover. Constant in px, constant
   in metres, height-dependent, or grows with time?
2. **What the detector locks on.** `ground_station/analysis/live_tracker.py` + `video_truth.py`: which colour/marker
   it tracks, the "coral static learned N px" mask (29341 px today vs ~21.7k on 10-15), skip counts. Does today's
   scene have a second coral/marker-coloured object (tape, pad, cable, clothing) near the drone that pulls the
   centroid? Show one frame crop per run with the mask (save PNGs in scratch, give paths and numbers only).
3. **Geometry.** Which floor pose / calibration each run used (camera_truth.sh CT_FLOOR, floor-1018 scan at 12:16
   vs r5_marks_1), the plane height used in pixel_to_plane for an airborne drone, and any touchdown offset. Did the
   camera move between 10-15 and 10-18 (compare marker pixel positions)? Would a wrong floor pose give a steady
   ~40 cm offset at the pad?
4. **Code changes** since 2026-10-14 in live_tracker.py, video_truth.py, camera_truth.sh (`git log -p --since=...`):
   list each change that touches detection, masking, plane height, pose, origin or alignment.

## Output

A ranked list of causes with the numbers that support each, the exact code/config site, and the smallest fix plus a
replay check that proves it (headless tracker re-run on a 10-18 mkv, dot within N px of the body). Mark guesses
UNVERIFIED.
