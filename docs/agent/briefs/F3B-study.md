# Task F3B: why mode 3 tracks loosely on flight r6_f3b (study and proposed fix, no flash)

## Goal
In flight r6_f3b_mode3 the camera fusion in mode 3 rejected or re-anchored many fixes (the firmware counters ended at
about n_rej 749 and n_anchor 624, from the operator's notes; check them in the log). The drone drifted and the
operator had to fight it by RC. In r6_f2_mode2 (mode 2) it drifted less. Find out why mode 3 tracks loosely, and
propose a firmware change that you have checked on the host. Do not flash anything. The repo is for a STM32 drone
(FreeRTOS); the fusion code is in `API/ekf_of.c` / `API/ekf_of.h`, with the task glue in `TASK/send_data.c`
(Cmd_ExtPos) and `TASK/StabilizerTask.c` (Ext_Pos_Tick).

## Data (already on this machine, not in git)
`~/data/r6/logs/` holds:
- `r6_f3b_mode3.slot0.csv` (100 Hz IMU), `.slot1.csv` (50 Hz: g_ext_pos_on, Z_posPID.FB, Remoter.* sticks, ...),
  `.slot2.csv` (25 Hz: g_ext_pos.* counters and state). The same three files for `r6_f2_mode2`.
- `video_truth/r6_f3b_mode3/` and `video_truth/r6_f2_mode2/`: `truth4.csv` (camera truth; columns t_tel, truth_x,
  truth_y, z, earth_x, earth_y, ekf_x, ekf_y), `truth4.json`, `fixes.csv` (the live tracker fixes sent to the
  drone: t_send, t_cap, pad_x_m, pad_y_m, earth_x_cm, earth_y_cm, frame_hex), `replay_start_fixes.csv` (the same
  video re-tracked with a better scenery mask), `tracker.out`.
Read the CSV headers first. Use `~/venv/bin/python` (numpy, pytest). Load slots with
`ground_station.analysis.video_truth.read_slots` if it fits; otherwise read the CSVs directly.

## Steps
1. Read `docs/flights/2026-10-12-live-fusion-explained.md` and the fusion code to understand how mode 2 and mode 3
   differ and what the gate, the rejection and the re-anchor do.
2. From the slot2 counters, find when the rejections and re-anchors happen in f3b (and f2), and what the fix innovation
   was at those times. Compare with the truth: were the rejected fixes right (truth agrees with the fix) or wrong?
3. Replay the fusion on the host. `API/tests/ext_pos_replay_host.c` replays the real ekf_of.c fix path (mode 2) and
   is driven by `live_tracker replay --fw` (`ground_station/analysis/live_tracker.py`). Build it as its header says.
   Extend it for mode 3 if mode 3 needs a different path. Replay f3b with the live fixes and with
   `replay_start_fixes.csv`.
4. Propose the smallest firmware change (for example the gate size, the anchor rule, or the mode 3 gain) that makes the
   replayed position follow the truth more closely. Show the replay error vs truth4 before and after (p50, p90 in
   cm), and the n_rej / n_anchor counts before and after.

## Rules
- Do not flash, do not touch any serial port, network service or port 8081. Host builds and tests only.
- Every number you write must come from a run you did. Mark guesses PROPOSED.
- Do not write one-off scripts. Extend the existing modules (`live_tracker.py` replay, `video_truth.py`,
  `ext_pos_replay_host.c`) and add a host test (`API/tests/test_ext_pos.c` or a pytest in
  `ground_station/analysis/tests/`) for any firmware change.
- Keep `API/tests/test_ext_pos.c` passing (build it as its header says).

## Deliverables
- `docs/flights/2026-10-13-f3b-mode3-study.md`: bottom line first; a table of what happened (time, counter, fix vs
  truth); the cause; the proposed change with before/after replay numbers.
- Code changes on your branch (firmware change behind the smallest diff, with its host test).
- `.agent-ops/out/F3B.md`: a short digest, at most 20 lines: bottom line, the numbers, the files changed, the tests run
  and their result.
