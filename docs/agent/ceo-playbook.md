# CEO playbook (for the operator): getting the best out of the desktop session

Measured 2026-10-01: a fresh desktop (CEO) session starts at about 72k tokens before any work.
The trimmed headless manager starts at 10.7k. So the CEO is the expensive seat: it should think and
check, and the manager and workers should do the bulk work.

## Habits
1. One session = one goal. Open with the outcome and the "done when" test.
   For example: "WP-2: X. Done when `pytest tests/foo` passes and the gate passes."
2. Ask for a brief, not the work: "brief this as WP-2 and send it". The CEO writes the brief, the manager runs it.
3. Batch your asks into one message. Every message re-sends the whole context.
4. Don't interrupt a running manager. Wait for the one notification, then read the report.
5. Answer the CEO's decision questions up front, in the brief. Questions asked mid-run stall the job.
6. Compact at a boundary, with a focus: `/compact keep WP-2 status`. Start a new topic with `/clear`.
   The repo (HANDOFF.md) carries the state, not the chat.
7. Point at files and paths. Don't paste logs.
8. Lab and hardware stay with the CEO plus you: flash, 8081, bench, motors.

## Brief size (one package costs a big share of account B's 5 h window)
- Good package: one goal, at most 3 files, one acceptance command, 1-3 worker rounds.
- Too big: "build feature X end to end". Split it into WP-a, WP-b, and so on.
- Routine work: `manager.sh run <id> medium`. Keep `high` for tricky work.
- Run at most 2 managers in parallel (the VPS runs 2 agy workers at once).

## Keeping the CEO base small (your settings, the agent cannot change them)
- Desktop plugins that never connect or are unused here: design, engineering, productivity (12 OAuth
  connectors), skipper (9 web-stack agents), paper-review-system, browser-use, desktop-commander,
  spanner, duplicate serena. Disable them in the app's plugin settings.
- `remember` plugin: injects `.remember/now.md` (15 KB) at session start. It overlaps with
  `.claude_state.md`, HANDOFF.md and auto-memory, so consider disabling it.
- `.claude_state.md` is 41.7 KB against its own 4 KB rule. The compact-restore hook injects up to
  16 KB of it. Archive old sections to `docs/agent/ledger/` once the wfb stream is done with them.
- Research connectors (paper-search, arxiv, youtube): turn them on only in research sessions.
