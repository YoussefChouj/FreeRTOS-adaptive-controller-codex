# HANDOFF - current state (overwrite at every task boundary, keep under 3 KB)

Read order: `AGENTS.md` -> this file -> `docs/agent/memory/rules.md` -> `.claude_state.md` (newest entry last).
History: `docs/agent/ledger/` (grep, never read whole); the previous HANDOFF is `ledger/handoff-2026-10-07-archive.md`.
Working mode: the desktop session is the CEO and does every item inline. No subagents, no workers.

## Now: overnight 2026-10-09 run done except item 7 (R9 research on the VPS); morning = row 17 on the 570 g rope
Branch `overnight-2026-10-08`. Checklist `docs/agent/overnight-2026-10-09.md` (A-E, 1-6, 8-10 ticked).
Morning pack: `docs/agent/morning-2026-10-10.md` (flash, PID rope hover, vp 17 -> 19 -> 16 on rope570_3l.yaml, debrief).
Final boss = row 17 (L2 only g8, te_off): `docs/design/final-boss-2026-10-10.md`. Row 19 = rope hover only, never the arm.
Sim: rows 17/19 fix the rope altitude sag (0.24 -> 0.08 m), do not damp the swing; arm: PID holds, row 19 tilts 32 deg.
Neural-Fly (nf.md): every adaptive law beats PID in every wind; data is force, no payload. All numbers PROPOSED.

## Next actions
1. Operator: flash, PID rope hover, then the rope570_3l campaign. Agent: flight_debrief + adaptive_review.
2. Item 7: when R9 lands (`git show vps/R9:docs/research/vp16/R9.md`), URL-gate, add rows to docs/design/vp16-research.md.
3. VPS oc-run chains use `agy/<model>` / `ark/<model>` (slash); `ark:` with a colon is refused (rc 2, no .out).

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
