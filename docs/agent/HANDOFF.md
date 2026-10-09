# HANDOFF - current state (overwrite at every task boundary, keep under 3 KB)

Read order: `AGENTS.md` -> this file -> `docs/agent/memory/rules.md` -> `.claude_state.md` (newest entry last).
History: `docs/agent/ledger/` (grep, never read whole); the previous HANDOFF is `ledger/handoff-2026-10-07-archive.md`.
Working mode: the desktop session is the CEO and does every item inline. No subagents, no workers.

## Now: 2026-10-09 day = operator flies row 16 (their 3L-v2) + row 17 on the 570 g rope vs PID
Branch `overnight-2026-10-08`. Operator guide (beginner, step by step, all commands, vp table, manual tuning):
`docs/lab/today-2026-10-09.md`. VOFA Studio presets vp00..vp19 (`ground_station/vofa_studio/presets/vp*.json`):
log CSV + live VOFA+, session name `vpNN_rope_01` / `pid_rope_01` = adaptive_review stem `logs/vofa/<name>`.
2 studio tests (budget, merge) stay RED until the 3L flash: the local axf predates f00412a (rows 16-19 vars unresolved).
Green after flash = proof the flash built 3L. Layer 3 (task priors) of the user's design is NOT built.
Campaign alternative: `docs/agent/morning-2026-10-10.md` (rope570_3l.yaml, no VOFA). Final boss = row 17.
Sim: rows 17/19 fix the rope altitude sag (0.24 -> 0.08 m), do not damp the swing; arm: row 19 tilts 32 deg.

## Next actions
1. Operator flies (guide above). Agent: adaptive_review per log, adaptive_meta table, mrac_log_replay --set 3l.
2. Design lead (not flown): IMU-only swing EKF + damping angle (Taki 2026, vp16-research.md R9) as a layer beside row 17.
3. VPS oc-run chains use `agy/<model>` / `ark/<model>` (slash); `ark:` with a colon is refused (rc 2, no .out).

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
