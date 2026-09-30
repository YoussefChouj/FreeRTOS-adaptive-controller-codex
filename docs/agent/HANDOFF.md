# HANDOFF - current state (overwrite at every task boundary, keep under 3 KB)

Read order for a new agent: `AGENTS.md` -> this file -> `docs/agent/memory/rules.md` (+ `env.md` if you touch workers/hardware).
History is in `docs/agent/ledger/` (grep it, never read it whole).

This is the page of stream `main` (main tree: Keil build, flash, probe, 8081, merges). Other streams have their own
page in `.worktrees/<stream>/docs/agent/streams/<stream>.md`. Start every session with
`python -m ground_station.agent_handoff start [stream] --as <harness>`; see all streams with `... board`.
Session a8a271e7 (MRAC) is live again (2026-09-30 19:40): S3 (RBF/SINDy/hybrid variants) runs as VPS worker `s3`, brief
0329eb6. S3+ lands on branch `mrac/next`, NOT on main, until the demo image is flashed and flown; main's `API/mrac*` stays
at S2b. `wfb` has a live session: leave it.

## Goal now
LAB DEMO (operator priority, 2026-09-30): PID vs PID+MRAC augmentation on (a) trajectory tracking (circle/figure8)
and (b) an asymmetric (off-centre) load. Then workflow B build (wfb stream), flightlab WP4, MRAC GS follow-ups.

## Facts as of 2026-09-30 15:40
- Demo tools in firmware (read in source, not flown by this session): runtime A/B `g_ctrl_select_req` via CMD 0x1F
  CTRL_SELECT (`API/controller.c`, `docs/research-platform/CONTROLLER_INTERFACE.md`); `circle_path`/`figure8_path`
  started by GS commands (`TASK/send_data.c:1670-1713`, `TASK/AutoflyTask.c`); simplex fade (`SIMPLEX.md`);
  record preset `flight_test_adaptive`; flightlab WP1-3 plugins incl. mrac (84 tests).
- NOT found in the ledger: any flight with MRAC injection on, or any flown circle/figure8. Treat both as first flights.
- Firmware at HEAD da55bce = S2b + operator tuning (yaw Ki 0.005, Throttle_th 3100/3050, rpm_dbg_rpm). Keil-built,
  axf matches committed source, NOT flashed. The drone runs an older build (last ledger flash 2026-09-29 08:20).
- No written demo protocol yet. flightlab WP4a (20 rules, 29 tests) committed 3805b46. WP4b (report/ledger/compare):
  worker files untracked in main, session 2a667b8b rewrites them inline (brief 3ccf8c0). Do not touch those files.
- Stale worktrees removed 2026-09-30 (win-p2/p3/p4/paths3d, both MRAC agent trees); branches kept.

## Next actions (most urgent first)
1. Operator in lab: `ah lock hw`, flash da55bce (`rebuild_and_flash --force --yes`), `livewatch verify`, props-off bench
   check: CTRL_SELECT toggles PID<->MRAC, MRAC telemetry streams, circle/figure8 start+stop on ground, simplex fade.
2. Write the demo protocol (per condition: hover baseline, load hover, circle; PID then MRAC, same battery, same preset).
3. flightlab WP4b: in progress in session 2a667b8b (do NOT re-dispatch). Open: `service/agent.py` lacks wide CMD 0x20..0x2B;
   replay desired z not /100.

## Do not
- Do not arm, idle or spin motors outside an operator-opened battery session (AGENTS.md > Authorizations).
- Do not start 8081 while the operator is streaming/flying (VOFA shares UDP 14550).
- Do not run a bare `git status` (about 200 dirty OBJ files).

<!-- AUTO:BEGIN -->
Refreshed: 2026-09-30 15:23 (mechanical, no LLM)
- Tree: .; branch / HEAD: main @ da55bce; unpushed commits: 0
- Dirty paths outside OBJ/: 190
  - M .agent-ops/served/estimator-panel.js
  -  M .claude_state.md
  -  M AGENTS.md
  -  M USER/JX_FLY.uvguix.Acer
  -  M USER/JX_FLY.uvoptx
  -  M docs/agent/HANDOFF.md
  - ?? .agent-ops/out/diag-empty-ui.md
  - ?? .agent-ops/out/e2e-live.json
  - ... +182 more
- Dashboard 8081: DOWN or unreachable
- Last commits:
  - da55bce firmware: operator tuning from flight16 days (yaw Ki, throttle ceiling, rpm logger mirror)
  - 1f4cae3 agent handoff: streams, claims, locks for parallel sessions and account/harness takeover
  - 5f555b9 handoff: MRAC S2b verified (equiv, armcc, pytest, Keil build); not flashed
  - 95e6561 handoff: flag MRAC S2b files swept into 1053086 as unverified
  - 1053086 agent handoff: tool-neutral HANDOFF.md, rules/env memory, Authorizations table, agent_handoff script
<!-- AUTO:END -->
