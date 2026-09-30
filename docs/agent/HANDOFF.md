# HANDOFF - current state (overwrite at every task boundary, keep under 3 KB)

Read order for a new agent: `AGENTS.md` -> this file -> `docs/agent/memory/rules.md` (+ `env.md` if you touch workers/hardware).
History is in `docs/agent/ledger/` (grep it, never read it whole).

This is the page of stream `main` (main tree: Keil build, flash, probe, 8081, merges). Other streams have their own
page in `.worktrees/<stream>/docs/agent/streams/<stream>.md`. Start every session with
`python -m ground_station.agent_handoff start [stream] --as <harness>`; see all streams with `... board`.
Overnight 2026-09-30 (session 013e8be7/14960682, claude-code): queue + results in `.claude_state.md` OVERNIGHT section.
`mrac/next` = d260303: S4 (L2 band gates, L3 feed-forward, variants 5/6) reviewed, gates green incl. armcc ids 0..6;
NOT wired into controller.c yet (S5). S3+ stays OFF main until the demo image is flashed and flown; main's `API/mrac*`
stays at S2b.

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
- Demo protocol written: `docs/lab/demo-pid-vs-mrac.md` (c7a89b4, source-checked). flightlab WP4b landed: compare +
  render 03d633b, ledger a4181fd (flightlab tree 133 passed). Agent classifies CMD 0x20..0x2B tier 0 (02d30b0; 0x1F and
  path cmds still unclassified). Replay desired z is metres by firmware (9d35f82).
- Stale worktrees removed 2026-09-30 (win-p2/p3/p4/paths3d, both MRAC agent trees); branches kept.

## Next actions (most urgent first)
1. Operator in lab: `ah lock hw`, flash da55bce (`rebuild_and_flash --force --yes`), `livewatch verify`, props-off bench
   check: CTRL_SELECT toggles PID<->MRAC, MRAC telemetry streams, circle/figure8 start+stop on ground, simplex fade.
2. Run the demo per `docs/lab/demo-pid-vs-mrac.md`; analyze with `python -m ground_station.analysis.flightlab analyze <stem>`
   and `compare <stem_PID> <stem_MRAC>`.

## Do not
- Do not arm, idle or spin motors outside an operator-opened battery session (AGENTS.md > Authorizations).
- Do not start 8081 while the operator is streaming/flying (VOFA shares UDP 14550).
- Do not run a bare `git status` (about 200 dirty OBJ files).

<!-- AUTO:BEGIN -->
Refreshed: 2026-09-30 22:37 (mechanical, no LLM)
- Tree: .; branch / HEAD: main @ 7c9bccf; unpushed commits: 0
- Dirty paths outside OBJ/: 186
  - M .agent-ops/served/estimator-panel.js
  -  M USER/JX_FLY.uvguix.Acer
  -  M USER/JX_FLY.uvoptx
  - ?? .agent-ops/out/diag-empty-ui.md
  - ?? .agent-ops/out/e2e-live.json
  - ?? .agent-ops/out/ekf-modes.txt
  - ?? .agent-ops/out/flash-f2b.txt
  - ?? .agent-ops/out/flash-p1.txt
  - ... +178 more
- Dashboard 8081: DOWN or unreachable
- Last commits:
  - 7c9bccf state: overnight queue uses ark deepseek (VPS wiring first), agy alongside
  - 65053a8 state: overnight queue 2026-09-30 (P0 demo readiness, MRAC S4/S5, workflow B) + unsaved notes from limit-hit sessions
  - 18ed9a9 handoff: MRAC S3 landed on mrac/next (5432f04); S4 on VPS worker s4
  - 887537a handoff: flightlab WP4a committed (3805b46); WP4b rewrite in progress
  - 3805b46 flightlab WP4a: 20 heuristic rules (health, pid, mrac) + tests; supervisor fixes
<!-- AUTO:END -->
