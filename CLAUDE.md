# Claude Code entry point

The project rules are in `AGENTS.md` (single source, shared by all harnesses). Read it, then `docs/agent/HANDOFF.md`.
The global token-budget rules in `~/.claude/CLAUDE.md` still apply on top.

Desktop sessions are the CEO and do all work inline (operator 2026-10-25: no outside workers; Argon, Jules, oc,
ark and VPS dispatches are revoked, see the Authorizations table in `AGENTS.md`). When the workload is huge, the CEO
writes a spec with acceptance tests (`.agent-ops/briefs/<id>.md`) and gives the operator one copy-paste prompt per
independent piece; the operator runs other agents by hand, and the CEO reviews and merges what comes back.
A headless manager (prompt starts with "# Manager rules") does not read AGENTS.md or HANDOFF.md:
`docs/agent/MANAGER.md` is its whole rule set.
