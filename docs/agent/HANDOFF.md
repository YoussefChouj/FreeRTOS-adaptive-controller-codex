# HANDOFF - current state (overwrite at every task boundary, keep under 3 KB)

Read order: `AGENTS.md` -> this file -> `docs/agent/memory/rules.md` -> `.claude_state.md` (newest entry first).
History: `docs/agent/ledger/` (grep, never read whole). Desktop session is CEO: inline, no subagents.

## Now: 2026-10-15 lab day on the new pipeline (cards + queue); night rehearsal PASSED

Branch `overnight-2026-10-08`, local commits. Design and decisions: `docs/lab/pipeline-design.md` (e3ee0e83).
- A card = one hypothesis, one `.fly` path, variants switched on the ground (`docs/lab/cards/`). Stages: list,
  screen (1 flight per variant, baseline first and last), confirm (ABBA; the winner beats every baseline flight).
- Queue `docs/lab/queue-2026-10-15.txt`: fusion-hover 2, pad-landing 2, xy-ctrl-screen 5, xy-ctrl-confirm 6 = 15
  flights (counted from the cards). Early-end tags: c = controller fail (counts), x = outside cause (flown again;
  a 2nd x puts the card on the fix list).
- Rehearsal (fake drone, no camera): 15/15 flights in 61 min, 7 swaps, a veto, 0 early ends, 0 skips; its
  confirm verdict means nothing (fake metrics are 0). Re-run after the pack change: 4/4 flights in 12 min, one
  typed swap (type 1 -> type 2), rc 0. A power cycle and an x re-fly were not rehearsed (unit tests only).
  Their logs: logs/discarded/rehearsal-2026-10-15/.
- Packs: two types, type 1 and type 2 (`--pack 1|2`, a swap asks the type); swap below 15.0 V or after 2
  flights (a swap reboots the drone); the charger is the operator's.
- Firmware: the 10-14 build is on the drone (C:/tmp/reflash2.log, Verify OK); no firmware commit since: no flash.
- Catalog `docs/lab/catalog.yaml` (`.\lab catalog`): the queue flies only listed cards; `lab end` moves them forward.

## Next 3 actions
1. Done 01:37: fake drone quit, 8081 restarted for the real drone (WiFi 192.168.4.1), a dry `lab up` READY
   (only the drone-off WAIT rows). Packs:
   type 1 = the big 5300 mAh pack (operator confirmed); type 2 = the small packs, 4000 mAh (operator confirmed 2026-10-15).
2. Lab, PowerShell in the repo: `.\lab up --pack 1` (the type in the drone): one check table (FAIL stops,
   WARN asks one Enter), then the queue flies card by card with a debrief after each landing (16 s measured
   in rehearsal).
3. Day end: `.\lab end`: `logs/lab/<day>/day_report.md`, then videos to `G:/My Drive/UAV_lab/<day>/` after one
   question (copied, size-checked, then removed from D:).

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
