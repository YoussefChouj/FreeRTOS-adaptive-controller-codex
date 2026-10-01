# HANDOFF - current state (overwrite at every task boundary, keep under 3 KB)

Read order for a new agent: `AGENTS.md` -> this file -> `docs/agent/memory/rules.md` (+ `env.md` if you touch workers/hardware).
History is in `docs/agent/ledger/` (grep it, never read it whole).

This is the page of stream `main` (main tree: Keil build, flash, probe, 8081, merges). Other streams have their own
page in `.worktrees/<stream>/docs/agent/streams/<stream>.md`. Start every session with
`python -m ground_station.agent_handoff start [stream] --as <harness>`; see all streams with `... board`.
Overnight 2026-09-30 (session 013e8be7/14960682, claude-code): queue + results in `.claude_state.md` OVERNIGHT section.
2026-10-01 10:15: wfb `workflow-b` @ 50ea9e7 pushed: F4, G3-G7, G9-G11 done (G5+G6 dc928e6). Resume at the `.claude_state.md` NEXT line: intake s5a2, then item-3 / S5b / item-7 briefs, then wfb G8, G12.
2026-10-01 late: wfb CODE COMPLETE. `workflow-b` @ 82b0eea pushed: G8 + G12-G16 via WP-2..WP-7 (briefs `docs/agent/briefs/`, reports
on the merged commits). Sim dry run green: `python -m pytest -q -p no:cacheprovider ground_station/service/tests/test_workflow_b_e2e.py`
-> 7 passed in 8.98 s (CEO run); mid-flight land/abort assert traj_stop then land, no kill. Live (non-sim) campaign wiring is
NOT built (go -> 503 by design). Merged to main bfcbb89 (operator approved, pushed); FLASHED 2026-10-01 (operator approved, rebuild_and_flash --yes, JX_FLY_7ef44977.axf, livewatch verify 0 mismatches, stream_log 0 dropped). Trap: Keil open across a merge re-saves its stale uvprojx on close; check `git diff --stat -- USER/JX_FLY.uvprojx` before building. Open notes in
`.claude_state.md` line 28 (J=None tuner wrapper, sim service needs bridge=Mock, tier0_access full also sets allow_agent_arm).
2026-10-01 10:40: CEO -> manager -> worker loop works end to end. Hand a work package to the manager (account B, headless)
with `bash .agent-ops/manager.sh run <id>` (brief `docs/agent/briefs/WP-<id>.md`, rules `docs/agent/MANAGER.md`; report on
branch `wp/<id>`). Pilot WP-1 (gate.py byte-safe shims) is merged at 208cc25: 1 worker round, 229 s, GATE PASS, 15 passed, 1 skipped.
`mrac/next` = b8e4a38: S4 (d260303) + controller.c wiring of `MRAC_GetOutput`/`MRAC_LayerSelectStep` (gates green,
armcc v0..6 0 err). S5a (deep inner layers, variants 7/8) = UNREVIEWED partial `vps/s5a` 948de17 (worker TIMEOUT).
S3+ stays OFF main until the demo image is flashed and flown; main's `API/mrac*` stays at S2b.

2026-10-01 20:45 NEXT CEO: run `docs/agent/briefs/CAMPAIGN-2026-10-01-drift.md` (WP-9 drift, WP-10 MRAC ramp, WP-11 slot retry -> flash bundle with 5c7fac2; then WP-8 rework, WP-12, WP-13 log analysis). Evidence: `docs/agent/reports/2026-10-01-drift-investigation.md`.

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
  path cmds still unclassified). Replay desired z is metres by firmware (9d35f82). Ledger rebuilt 767ff7a (15 flights).
- flight16 tuning input `docs/analysis/flight16-tuning-input.md` (578ccb4): pos_x integrator saturated 98.9 %, roll rate
  e_rms 3x pitch, yaw-pair imbalance -21 %; MRAC shadow authority RMS(u_ad)/RMS(u_nom) 1.09-2.19 (threshold 0.5).
- Stale worktrees removed 2026-09-30 (win-p2/p3/p4/paths3d, both MRAC agent trees); branches kept.

## Next actions (most urgent first)
1. Operator in lab: `ah lock hw`, flash da55bce (`rebuild_and_flash --force --yes`), `livewatch verify`, props-off bench
   check: CTRL_SELECT toggles PID<->MRAC, MRAC telemetry streams, circle/figure8 start+stop on ground, simplex fade.
2. Before any MRAC-injected flight: lower gamma or `mrac_to_mixer` until the shadow authority ratio is < 0.5
   (flight16 had 1.09-2.19; see the tuning-input doc).
3. Run the demo per `docs/lab/demo-pid-vs-mrac.md`; analyze with `python -m ground_station.analysis.flightlab analyze <stem>`
   and `compare <stem_PID> <stem_MRAC>`.

## Do not
- Do not arm, idle or spin motors outside an operator-opened battery session (AGENTS.md > Authorizations).
- Do not start 8081 while the operator is streaming/flying (VOFA shares UDP 14550).
- Do not run a bare `git status` (about 200 dirty OBJ files).

<!-- AUTO:BEGIN -->
Refreshed: 2026-10-01 05:17 (mechanical, no LLM)
- Tree: .; branch / HEAD: main @ 578ccb4; unpushed commits: 1
- Dirty paths outside OBJ/: 181
  - M .agent-ops/served/estimator-panel.js
  -  M .claude_state.md
  -  M USER/JX_FLY.uvguix.Acer
  -  M USER/JX_FLY.uvoptx
  -  M docs/agent/HANDOFF.md
  - ?? .agent-ops/out/diag-empty-ui.md
  - ?? .agent-ops/out/e2e-live.json
  - ?? .agent-ops/out/ekf-modes.txt
  - ... +173 more
- Dashboard 8081: DOWN or unreachable
- Last commits:
  - 578ccb4 analysis: flight16 tuning input (measured numbers from flightlab report + compare vs flight15)
  - eda0d1b state: item 6 wiring b8e4a38, S5a spawned on VPS
  - 9e81c1b state: item 6 wiring + s5a brief in progress
  - 767ff7a flightlab: ledger.csv rebuilt from logs/vofa (15 flights); state: e2e done
  - a53191a flightlab ledger: tolerate motors.airborne/steady = None (rebuild crashed on a real log) + test
<!-- AUTO:END -->
