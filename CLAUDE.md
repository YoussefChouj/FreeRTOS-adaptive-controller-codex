# Claude Code entry point

The project rules are in `AGENTS.md` (single source, shared by all harnesses). Read it, then `docs/agent/HANDOFF.md`.
The global token-budget rules in `~/.claude/CLAUDE.md` still apply on top.

Desktop sessions are the CEO and do all work inline (operator 2026-10-25: no outside workers; Argon, Jules, oc,
ark and VPS dispatches are revoked, see the Authorizations table in `AGENTS.md`). The operator joins the planning phase only;
after it the CEO does everything. Operator available and the load is large: the CEO gives ONE copy-paste prompt for
ONE orchestrator agent the operator runs by hand (e.g. Antigravity), which launches its own sub-agents from a spec with
acceptance tests (`.agent-ops/briefs/<id>.md`); the CEO reviews and merges what comes back. Autonomous run: the CEO does all
work inline.
A headless manager (prompt starts with "# Manager rules") does not read AGENTS.md or HANDOFF.md:
`docs/agent/MANAGER.md` is its whole rule set.
