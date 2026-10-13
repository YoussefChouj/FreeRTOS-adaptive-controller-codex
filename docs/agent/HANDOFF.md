# HANDOFF - current state (overwrite at every task boundary, keep under 3 KB)

Read order: `AGENTS.md` -> this file -> `docs/agent/memory/rules.md` -> `.claude_state.md` (newest entry first).
History: `docs/agent/ledger/` (grep, never read whole). Desktop session is CEO: inline, no subagents.

## Now: 2026-10-14 overnight - lab flight workflow `fly` built (inline, operator asleep), nothing flashed or flown

Branch `overnight-2026-10-08`. The drone is still on ELF 6da69207. Commits are local only, and the code + tests are UNVERIFIED on the drone.
- **WP-44 VAR_WRITE** (41013a0e): GS CMD 0x2D writes one row of the 33-row VAR_WRITE_ROW allowlist (AutoflyTask.c).
- **WP-45** (03716d5c): a Keil preset starts from a WiFi hover (HOVER yields to it). Deviation from "fly-up without WFB":
  the WFB takeoff/hover/land is kept, because the code shows the drone probably cannot climb without WFB.
- **`fly`** (`fly.cmd <name|.fly|yaml>`, 03ddcf5b):
  - arm = go, with a 3 s countdown;
  - preflight runs silently;
  - keys s/n/l/p, Ctrl+C lands;
  - 5-line debrief;
  - flights are filed under `logs/flights/<date>/<NN>_<c>/`.
  `.fly` text scripts (a40b99ef); a `preset` step (2ad619c8), plus a `set` step.
- **Camera** (5450c76a): the fusion scrcpy mirror is 1920x1080, off screen and grabbed by PrintWindow, so only the tracker window shows.
- **VOFA+/PJ** (bb41660e):
  - `fly` prints the VOFA+ import paths (C:	mpofa);
  - `p` = PlotForward /api/streams/plot -> PlotJuggler 9870.

## Next 3 actions
1. At the lab, stop 8081, then `python -m ground_station.flashtool.rebuild_and_flash --yes` (WP-44/45 + 3492cbdb).
2. Ground tests, disarmed:
   - a VAR_WRITE round trip (its time is unmeasured);
   - `camera_truth.sh check` should show `ready 1920x1080`;
   - VOFA+ loads both files;
   - `p` opens PJ with live curves.
3. First `fly preset_tour` with the operator; LINK_LOST_S 3 s is PROPOSED.

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
