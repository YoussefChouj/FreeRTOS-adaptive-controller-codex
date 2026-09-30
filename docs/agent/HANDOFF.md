# HANDOFF - current state (overwrite at every task boundary, keep under 3 KB)

Read order for a new agent: `AGENTS.md` -> this file -> `docs/agent/memory/rules.md` (+ `env.md` if you touch workers/hardware).
History is in `docs/agent/ledger/` (grep it, never read it whole).

## Goal now
Workflow B (autonomous flight loop) build, plus flightlab (workflow A) WP4 and MRAC firmware work.
Design is confirmed in `.agent-ops/grill-autonomous-flight-loop.md` (Q1-Q12). Specs: `docs/analysis/flightlab-spec.md`,
`3d-panel-flight-ux-spec.md`.

## Facts as of 2026-09-30 14:19 (update these)
- flightlab: WP1, WP2, WP3 merged and verified (84 tests). WP4a (rules) and WP4b (report/ledger/compare) briefs are
  committed (2f35e29, 3ccf8c0); the Sonnet agents for them failed with 429, worktrees were empty. Re-dispatch is open.
- MRAC firmware: S1a, S1b (f86e7cf), S2a (6a28f36) committed and verified. S2b was running in a worktree
  (`.claude/worktrees/agent-a0ed3122bf5e2a28f`), uncommitted at last check: verify before trusting.
- Dashboard: rec_logs and Streams panel fixes committed (5dd6d5a), path-panel replay fix (9270e2c). Some of these are unpushed (see AUTO block).
- Open: replay desired z is not /100 while x/y and actual z are (units unverified).
- Firmware builds are done but NOT flashed: flash only when the operator is in the lab.

## Next actions (write 3 lines, most urgent first)
1. Check the AUTO block below; push unpushed commits if the tree is verified.
2. Re-dispatch WP4a/WP4b (workers, not Claude subagents while the Claude quota is exhausted; see env.md).
3. MRAC S2b files (swept into commit 1053086) are VERIFIED 2026-09-30 14:40: EQUIV OK default/cap16/cap24 + SELFTEST OK,
   armcc mrac.c 4 warn/0 err and send_data.c 1 warn/0 err, FMA 0, scoped pytest 76 + 236 passed (test_telemetry_harness needs the live drone),
   Keil 0 errors. axf built, NOT flashed (flash waits for the operator in the lab). Open: service/agent.py lacks wide CMD 0x20..0x2B.

## Do not
- Do not arm, idle or spin motors outside an operator-opened battery session (AGENTS.md > Authorizations).
- Do not start 8081 while the operator is streaming/flying (VOFA shares UDP 14550).
- Do not run a bare `git status` (about 200 dirty OBJ files).

<!-- AUTO:BEGIN -->
Refreshed: 2026-09-30 14:24 (mechanical, no LLM)
- Branch / HEAD: main @ 1053086; unpushed commits: 0
- Dirty paths outside OBJ/: 187
  - M .agent-ops/served/estimator-panel.js
  -  M API/pid.c
  -  M BSP/rpm.c
  -  M TASK/StabilizerTask.c
  -  M USER/JX_FLY.uvguix.Acer
  -  M USER/JX_FLY.uvoptx
  -  M docs/agent/HANDOFF.md
  - ?? .agent-ops/out/diag-empty-ui.md
  - ?? .agent-ops/out/e2e-live.json
  - ?? .agent-ops/out/ekf-modes.txt
  - ?? .agent-ops/out/flash-f2b.txt
  - ?? .agent-ops/out/flash-p1.txt
  - ... +175 more
- Dashboard 8081: DOWN or unreachable
- Last commits:
  - 1053086 agent handoff: tool-neutral HANDOFF.md, rules/env memory, Authorizations table, agent_handoff script
  - 3ccf8c0 flightlab: WP4 briefs mark the flight16 steps NOT RUN on remote hosts
  - 9270e2c path-panel: session replay keeps desired x/y/z = 0
  - 2e20b6f docs: workflow B confirmed; build-time additions A1-A4
  - 2f35e29 flightlab: WP4a rules and WP4b report/ledger/compare briefs
<!-- AUTO:END -->
