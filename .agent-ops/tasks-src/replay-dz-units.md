# Task replay-dz-units: prove (or fix) the units of desired z in recording replay / path panel

You are a worker in a git worktree of this repo. Investigation first, then at most a small fix plus tests.
No network, no port 8081, no hardware, no probe, no firmware edits.

EDIT ONLY: `ground_station/service/rec_logs.py`, `ground_station/service/tests/test_rec_logs.py` (create if absent;
if a test file for rec_logs already exists under another name, use that one instead and say so),
`ground_station/service/tests/path_panel_harness.js`, `.agent-ops/out/replay-dz-units.md` (your report).

## The question
`ground_station/service/rec_logs.py:145-173`: `position()` divides x, y by 100 and z_cm by 100; `setpoint()` divides
dx, dy, dz_cm by 100 but takes `dz` raw. Is raw `dz` correct?

## Trace, with `path:line` for every link
1. Which telemetry variable each short key (`x`, `y`, `z_m`, `z_m2`, `z_cm`, `dx`, `dy`, `dz`, `dz_cm`, `dx_m`) comes
   from: find where rec_logs builds `v` and the alias map (grep the key names in `ground_station/service/`,
   `ground_station/livewatch/manifests.yaml`).
2. For each firmware variable behind `dz` and `dx` (likely `Ctrler.Z_posPID.Des`, `Ctrler.locxPID.Des` or similar):
   the unit in firmware, from the code that writes it (`TASK/StabilizerTask.c`, `TASK/AutoflyTask.c`
   `AutoflyTask_CommitRef`, `TASK/send_data.c` CMD 0x0C idx 2 center_z). Supporting hint, not proof:
   `ground_station/analysis/flightlab/config/loops.yaml:22,24` (alt_pos des_hold_tol 0.05 vs pos_x 5.0).
3. Where the path panel draws desired z (grep `setpoint`, `dz` in `.agent-ops/served/` and `ground_station/service/`
   static JS) and whether it rescales again.

## Then
- If raw `dz` is metres: no code change; add a test that pins it (a record with `dz` = 1.2 and `x`/`dx` in cm gives
  setpoint z 1.2 and a 3D error computed in metres), and a comment on the `dz` line citing the firmware source.
- If it is not metres: fix the scaling in rec_logs.py, same tests with the corrected expectation.
- Harness: add one check to `path_panel_harness.js` that a replay frame with known cm x/y and metre z plots desired
  and actual z on the same scale (follow the harness's existing check style; keep its no-network sandbox).

## Acceptance (verbatim tails in the report)
- `python -m pytest ground_station/service/tests -q -k "rec_log or replay"`
- `node ground_station/service/tests/path_panel_harness.js`
Report: the trace table (key -> firmware variable -> unit -> source), the verdict, files changed.
