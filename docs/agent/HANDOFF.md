# HANDOFF - current state (overwrite at every task boundary, keep under 3 KB)

Read order: `AGENTS.md` -> this file -> `docs/agent/memory/rules.md` -> `.claude_state.md` (newest entry first).
History: `docs/agent/ledger/` (grep, never read whole). Desktop session is CEO: inline, no subagents.

## Now: 2026-10-13 nightly run - tracker `docs/agent/nightly-2026-10-13.md` (one row per item, with commits)

Branch `overnight-2026-10-08`. The drone is on ELF 6da69207 (f6424fa1). All 13 items are closed (item 8 docs ongoing).
- **Mode 2:** tracker-side fixes only, no reflash, all measured on r6 replays: `--align auto` (r6 flew yaw 0 vs ~90 deg),
  coral mask learned at start (stuck fixes; r6_f4 replay p50 272 -> 7.6 cm), `--delay-ms 200` (lag 0.21 s).
- **Mode 3:** also q_pos 1e-6 -> 0.05 in `API/ekf_of.c` (3492cbdb, F3B), pinned by a host test, NOT flashed, not flown.
  The replay shows no gain vs truth (not a valid check for mode 3). The gate stays at 5 sigma.
- **NN:** no gain from the r6 data (row 2). It halves b7 vs earth and does nothing for b17/b10.
- **Drift summary:** nightly doc, item 12 section. **Live video:** `camera_truth.sh fusion` or `video <name>`.
- **Audit (2026-10-13 am):** `docs/agent/audit-2026-10-13-nightly.md`. Decided: PlotJuggler replaces live_plot (removed).
  Installed at D:\PlotJuggler, UDP Server json port 9870, field `t`; seen working with synthetic rows, not yet with the
  drone. `camera_truth.sh fusion` sends to it by default. Setup: stream-log skill, "Live plot".
- **Open:** fixes vs truth4 offset 40-95 cm per flight (unexplained), the r6_f2 replay p90 tail, NN seed spread.

## Next 3 actions
1. Lab per `docs/lab/run-sheet-2026-10-13-drift-free.md`: mode 2 first, wait for `align LOCK`, 60 s hands-off.
   Pass mark PROPOSED: under 20 cm.
2. Before mode 3: stop 8081, `python -m ground_station.flashtool.rebuild_and_flash --yes` (3492cbdb), then fly mode 3.
3. NN: next lever is more mode 1 flights with camera truth, not a bigger model (PROPOSED).

## Do not
- Arm/spin outside an operator-opened workflow-B battery session; flash while armed or with `--force`.
- Stage OBJ/ or USER/ or logs/; `git add -A`.
- Start recording until the operator confirms preset, variable list and session name.
- Write unmeasured numbers without PROPOSED/computed.

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
