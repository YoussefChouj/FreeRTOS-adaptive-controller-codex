# Claude Code entry point

The project rules are in `AGENTS.md` (single source, shared by all harnesses). Read it, then `docs/agent/HANDOFF.md`.
The global token-budget rules in `~/.claude/CLAUDE.md` still apply on top.

Desktop sessions are the CEO and build everything inline (Claude manager DISABLED 2026-10-17; research and second
opinions go to Gemini 4 Argon High via `.agent-ops/argon.ps1`, 160K cap). See the "Default workflow" section of the global CLAUDE.md and
`docs/agent/ceo-manager-architecture.md`. A headless manager (prompt starts with "# Manager rules") does
not read AGENTS.md or HANDOFF.md: `docs/agent/MANAGER.md` is its whole rule set.
