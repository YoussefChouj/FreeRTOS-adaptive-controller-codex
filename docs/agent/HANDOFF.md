# HANDOFF - current state (overwrite at every task boundary, keep under 3 KB)

Read order: `AGENTS.md` -> this file -> `docs/agent/memory/rules.md` -> `.claude_state.md` (newest entry first).
History: `docs/agent/ledger/` (grep, never read whole). Desktop session is CEO: inline, no subagents.

## Now: 2026-10-15 evening lab ended, 4-way comparison NOT flown (F1 2 attempts, F2-F6 not flown)

Branch `overnight-2026-10-08`, local commits (ad83fa79 = livetune loop=pos + 6-flight run sheet).
Full record: `docs/lab/lab-session-2026-10-15.md` (read it first). Plan: `docs/lab/run-sheet-2026-10-16.md`.
- F1 attempt 1: C3 gains applied, abort L1 position_error at takeoff (26.5 cm drift, camera agrees).
- F1 attempt 2: abort before liftoff, trajectory BEGIN timed out on the host, firmware `applied` arrived late
  -> "begin rejected". Cause PROPOSED: WiFi reply latency vs host command timeout under load.
- Firmware: the 10-14 build, unchanged, no flash. Drone may still hold C3 in RAM until power-cycled.

## Next 3 actions
1. Fix the trajectory upload (late BEGIN ack, upload before arm, find the 445x cmd 26 idx 2 sender);
   host tests; then the takeoff-drift replay of attempt 1 (fix list 1-3 in the session record).
2. Lab: power-cycle, re-write C3 stop-and-wait (operator chat approval), fusion 3, `.\fly lab_c_robust --pack 1 --once`.
3. Then F2-F6 per the run sheet (livetune rate/pos with fusion 2, ES gains chat-approved, LQR, MPC).

## Do not
- Arm/spin outside an operator-opened workflow-B battery session; flash while armed or with `--force`.
- Stage OBJ/ or USER/ or logs/; `git add -A`.
- Start recording until the operator confirms preset, variable list and session name.
- Write unmeasured numbers without PROPOSED/computed.
- Run `lab end` on test or rehearsal state against the real catalog (it rewrites `docs/lab/catalog.yaml`).

<!-- AUTO:BEGIN -->
Refreshed: 2026-10-13 09:50 (mechanical, no LLM)
- Tree: .; branch / HEAD: overnight-2026-10-08 @ e4c275c7; unpushed commits: 183
- Dirty paths outside OBJ/: 458
  -  M .agent-ops/.ark-last-spawn
  -  M .agent-ops/served/estimator-panel.js
  -  M .claude_state.md
  -  M USER/JX_FLY.uvguix.Acer
  -  M USER/JX_FLY.uvoptx
  -  M USER/main.c
  -  M docs/agent/ledger/state-2026-10-05-improvements.md
  -  M docs/flights/ledger.csv
  - ... +450 more
- Dashboard 8081: UP (HTTP 200)
- Last commits:
  - e4c275c7 agent: HANDOFF refresh, nightly 2026-10-13 closed (F3B merged not flashed, next: lab per run sheet)
  - fb228975 nightly: row 7 F3B done (merged, not flown), item 12 drift summary; run sheet: flash 3492cbdb before mode 3
  - 3492cbdb F3B: mode 3 q_pos 1e-6 -> 0.05 (worker 4690fa39, selective), pinned by a host test
  - fb54c546 nightly: item 2 mode-1-only NN variant, no held-out gain over baseline
  - 89a4814d live_tracker: post-lock yaw refit monitor (nightly item 6, not applied), fit --auto prints it; nightly rows 6, 9, 11 table fix
<!-- AUTO:END -->
