# HANDOFF - current state (overwrite at every task boundary, keep under 3 KB)

Read order: `AGENTS.md` -> this file -> `docs/agent/memory/rules.md` -> `.claude_state.md` (newest entry last).
History: `docs/agent/ledger/` (grep, never read whole); the previous HANDOFF is `ledger/handoff-2026-10-07-archive.md`.
Working mode: the desktop session is the CEO and does every item inline. No subagents, no workers.

## Now: load-swing lab day, manual mode only (2026-10-08)
Read `docs/flights/2026-10-08-morning-brief.md` first (presets, lab order, review findings, lessons).
Branch `overnight-2026-10-08` (8daf95c), built 0 err / 0 warn, NOT flashed. Operator flashes with
`python -m ground_station.flashtool.rebuild_and_flash --yes`. Fallback: reboot = preset 0 (as flown), tag
`fw-flown-2026-10-07`, or `OBJ/archive/JX_FLY_834c8564.axf`.
Keil presets: set `kp_id`, then `kp_go = 1`, disarmed; `kp_active` reads back. 1 = of1 off, 3 = of1 off + MRAC
z only, 5/6 = 1/3 + Z_ratePID 500/300/700. Bench (PROPOSED): of1 leak drives the MRAC swing; Z_ratePID clamps
cause the PID sink. Analysis: `docs/flights/2026-10-07-load-swing-analysis.md`.
Flights: `/workflow-a` (manual RC flight, WiFi `stream_log --frames ground_station/livewatch/exp1_frames.md`,
`flight_review <stem>.slot0.csv`). Name logs `exp<N>_p<preset>-<load>`.
Video truth (paused): `ground_station/analysis/video_truth.py`; ChArUco landscape calib saved in
`docs/video-truth/`; re-shoot plan in `ledger/handoff-2026-10-07-archive.md`.

## Next actions
1. Operator flashes, flies preset 1 then 3, 5, 6 with the bottle; agent logs and reviews (workflow A).
2. Open (behind flags, default = flown): of2_h outlier gate; RPM ch2/ch0 low readings (`BSP/rpm.c`);
   gyro-compensated of1 (the real leak fix); Keil-startable demo programs.
3. Later: video-truth roam, flow scale / gyro-bias fit, asym_load demos with overlay videos.

## Recording rules (operator, verbatim spirit)
- Start only when the operator asks, after one AskUserQuestion confirming the var list and the rate.
- No timer: record until the operator says stop. WiFi telemetry only, never the SWD probe.
- Confirmations are QA in chat; no dashboard approvals (operator 2026-10-07).

## Do not
- Arm or spin motors; flash with anything but `rebuild_and_flash --yes`; flash the ram-savings, o2-build,
  float-math, static-etag or h0g-port branches before the demo; touch protected files.
- Stage OBJ/ or USER/; `git stash`; commit the operator's landing hunks in StabilizerTask.c without asking.
- Run a bare `git status` (use `-- . ':!OBJ' ':!USER'`).

<!-- AUTO:BEGIN -->
<!-- AUTO:END -->
