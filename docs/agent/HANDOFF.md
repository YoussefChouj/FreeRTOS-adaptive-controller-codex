# HANDOFF - current state (overwrite at every task boundary, keep under 3 KB)

Read order: `AGENTS.md` -> this file -> `docs/agent/memory/rules.md` -> `.claude_state.md` (newest entry last).
History: `docs/agent/ledger/` (grep, never read whole); the previous HANDOFF is `ledger/handoff-2026-10-07-archive.md`.
Working mode: the desktop session is the CEO and does every item inline. No subagents, no workers.

## Now: overnight 2026-10-09 run done through item 6; morning = 3-layer flight on the 570 g rope
Branch `overnight-2026-10-08`. Checklist `docs/agent/overnight-2026-10-09.md` (A-E, 1-6 ticked).
Morning pack: `docs/agent/morning-2026-10-10.md` (flash, vp 17 -> 19 -> 16 on rope570_3l.yaml, debrief commands).
3L-v2 law in API/mrac.c (f00412a), vp rows 16-19 (19 = L2 g20 + D, 30a2a3f). Local axf predates f00412a: flash first.
Sim (sim_rope.md): rows 17/19 fix altitude sag on the rope, do not damp the swing, no gain on a load drop. PROPOSED.

## Next actions
1. Operator: flash, PID rope hover, then the rope570_3l campaign. Agent: flight_debrief + adaptive_review.
2. Open items 7 (research meta), 8 (Neural-Fly dataset), 9 (final boss variant) in the overnight checklist.
3. Arm-offset (293 g) sim not run yet: demo_loads arm cases exist, rows 17/19 not wired there.

## Recording rules (operator, verbatim spirit)
- Start only when the operator asks, after one AskUserQuestion confirming the var list and the rate.
- No timer: record until the operator says stop. WiFi telemetry only, never the SWD probe.
- Confirmations are QA in chat; no dashboard approvals (operator 2026-10-07).

## Do not
- Arm or spin motors; flash with anything but `rebuild_and_flash --yes`; flash the ram-savings, o2-build,
  float-math, static-etag or h0g-port branches before the demo; touch protected files.
- Stage OBJ/ or USER/; `git stash`; commit the operator's landing hunks in StabilizerTask.c without asking.
- Run a bare `git status` (use `-- . ':!OBJ' ':!USER'`).

<!-- AUTO:BEGIN -->
<!-- AUTO:END -->
