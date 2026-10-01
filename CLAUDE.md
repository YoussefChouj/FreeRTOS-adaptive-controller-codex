# Claude Code entry point

The project rules are in `AGENTS.md` (single source, shared by all harnesses). Read it, then `docs/agent/HANDOFF.md`.
The global token-budget rules in `~/.claude/CLAUDE.md` still apply on top.

Desktop sessions are the CEO: follow the "Default workflow" section of the global CLAUDE.md and
`docs/agent/ceo-manager-architecture.md`. A headless manager (prompt starts with "# Manager rules") does
not read AGENTS.md or HANDOFF.md: `docs/agent/MANAGER.md` is its whole rule set.
