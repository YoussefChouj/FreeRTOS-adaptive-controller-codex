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
3. Verify S2b worktree output (EQUIV + SELFTEST + armcc) before merging.

## Do not
- Do not arm, idle or spin motors outside an operator-opened battery session (AGENTS.md > Authorizations).
- Do not start 8081 while the operator is streaming/flying (VOFA shares UDP 14550).
- Do not run a bare `git status` (about 200 dirty OBJ files).

<!-- AUTO:BEGIN -->
Refreshed: 2026-09-30 14:21 (mechanical, no LLM)
- Branch / HEAD: main @ 3ccf8c0; unpushed commits: 11
- Dirty paths outside OBJ/: 191
  - M .agent-ops/served/estimator-panel.js
  -  M .claude_state.md
  -  M .gitignore
  -  M API/pid.c
  -  M BSP/rpm.c
  -  M TASK/StabilizerTask.c
  -  M USER/JX_FLY.uvguix.Acer
  -  M USER/JX_FLY.uvoptx
  - ?? .agent-ops/out/diag-empty-ui.md
  - ?? .agent-ops/out/e2e-live.json
  - ?? .agent-ops/out/ekf-modes.txt
  - ?? .agent-ops/out/flash-f2b.txt
  - ... +179 more
- Dashboard 8081: DOWN or unreachable
- Last commits:
  - 3ccf8c0 flightlab: WP4 briefs mark the flight16 steps NOT RUN on remote hosts
  - 9270e2c path-panel: session replay keeps desired x/y/z = 0
  - 2e20b6f docs: workflow B confirmed; build-time additions A1-A4
  - 2f35e29 flightlab: WP4a rules and WP4b report/ledger/compare briefs
  - 680634c flightlab: MRAC-AUTH rule and MRAC-DRIFT min_change floor (spec 7 + rules.yaml, HEURISTIC)
<!-- AUTO:END -->
