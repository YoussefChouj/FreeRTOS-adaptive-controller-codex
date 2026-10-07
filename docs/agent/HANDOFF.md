# HANDOFF - current state (overwrite at every task boundary, keep under 3 KB)

Read order: `AGENTS.md` -> this file -> `docs/agent/memory/rules.md` -> `.claude_state.md` (newest entry last).
History: `docs/agent/ledger/` (grep, never read whole); the previous HANDOFF is `ledger/handoff-2026-10-07-archive.md`.
Working mode: the desktop session is the CEO and does every item inline. No subagents, no workers.

## Now: state estimation ground truth from phone video (2026-10-06/07)
Why: the operator wants proof the drone tracked a dense preset path; the optical-flow estimate drifts.
Tool: `ground_station/analysis/video_truth.py` (`calib`, `floor`, `frame`, `run`, `overlay`), tests
`tests/test_video_truth.py` (7 pass). Commits 2317ade..f255496. Usage is in the module docstring.
Status: the first lens clip was not good enough (rms 2.40 px, lens spread 2.7 cm rms / 6.8 cm max at 4 m).
Cause: board not flat, frame edges not covered. Not shake (it was filmed on a DJI OM5).
Re-shoot plan, one clip: phone fixed, focus and exposure locked, 1x lens, 4K 30, stabilisation off. First ~60 s:
walk a FLAT 9x6 board (`docs/video-truth/lens_board_9x6.png`) through every frame corner and edge, tilted, 1 s still at
each spot. Then fly. Process: `calib --board 9x6 --until <s>`, then `floor` (8x5 sheet, 2.6 cm squares, on the
landing area), `frame`, `run`, `overlay`. Target lens spread max < 2 cm (PROPOSED).

## Next actions
1. Operator re-shoots the clip, then the truth roam recording, but only when the operator asks.
2. Fit flow scale, gyro-bias gain and lag from that roam.
3. Firmware: PX4-style online flow-gyro bias (review, `bash tools/check.sh` CHECK PASS, flash), validation roam.
4. Demo: asym_load_pid vs asym_load_mrac at 250/500 g (`docs/agent/lab-2026-10-06.md`) with overlay videos.
5. Open: preflight gyro sanity check; restart 8081; reader audit `telemetry_adapter.py:278-290`.

## Recording rules (operator, verbatim spirit)
- Start only when the operator asks, after one AskUserQuestion confirming the var list and the rate.
- 200 Hz; include RPM and `Ctrler.locxPID.Des`/`locyPID.Des`. No timer: record until the operator says stop
  (`touch C:/tmp/lw.stop`). WiFi telemetry only, never the SWD probe; 8081 down during livewatch.
- `livewatch watch <vars> --hz 200 --fast --quiet --csv logs/livewatch/<name>.csv --stop-file C:/tmp/lw.stop` (background).

## Do not
- Arm or spin motors; flash with anything but `rebuild_and_flash --yes`; flash the ram-savings, o2-build,
  float-math, static-etag or h0g-port branches before the demo; touch protected files.
- Stage OBJ/ or USER/; `git stash`; commit the operator's landing hunks in StabilizerTask.c without asking.
- Run a bare `git status` (use `-- . ':!OBJ' ':!USER'`).

<!-- AUTO:BEGIN -->
<!-- AUTO:END -->
