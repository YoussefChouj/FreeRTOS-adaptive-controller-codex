# HANDOFF - current state (overwrite at every task boundary, keep under 3 KB)

Read order for a new agent: `AGENTS.md` -> this file -> `docs/agent/memory/rules.md` (+ `env.md` if you touch workers/hardware).
History is in `docs/agent/ledger/` (grep it, never read it whole).

This is the page of stream `main` (main tree: Keil build, flash, probe, 8081, merges). Other streams have their own
page in `.worktrees/<stream>/docs/agent/streams/<stream>.md`. Start every session with
`python -m ground_station.agent_handoff start [stream] --as <harness>`; see all streams with `... board`.
Known overlap 2026-09-30: MRAC (session a8a271e7) and the handoff work both ran in the main tree. New work takes a worktree.

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
Refreshed: 2026-09-30 14:45 (mechanical, no LLM)
- Tree: .; branch / HEAD: main @ 5f555b9; unpushed commits: 0
- Dirty paths outside OBJ/: 195
  - M .agent-ops/served/estimator-panel.js
  -  M .claude_state.md
  -  M AGENTS.md
  -  M API/pid.c
  -  M BSP/rpm.c
  -  M TASK/StabilizerTask.c
  -  M USER/JX_FLY.uvguix.Acer
  -  M USER/JX_FLY.uvoptx
  - ... +187 more
- Dashboard 8081: DOWN or unreachable
- Last commits:
  - 5f555b9 handoff: MRAC S2b verified (equiv, armcc, pytest, Keil build); not flashed
  - 95e6561 handoff: flag MRAC S2b files swept into 1053086 as unverified
  - 1053086 agent handoff: tool-neutral HANDOFF.md, rules/env memory, Authorizations table, agent_handoff script
  - 3ccf8c0 flightlab: WP4 briefs mark the flight16 steps NOT RUN on remote hosts
  - 9270e2c path-panel: session replay keeps desired x/y/z = 0
<!-- AUTO:END -->
