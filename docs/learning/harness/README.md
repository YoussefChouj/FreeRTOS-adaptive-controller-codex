# /learn harness: moved to a global install (2026-10-25)

The `/learn` skill now lives outside this repo so every project and every Claude Code desktop session can use it.

| What | Where |
|---|---|
| Skill (SKILL.md, cards, job briefs, PARITY.md) | `~/.claude/skills/learn/` (local git repo) |
| CLI + viewer + tests | `~/.claude/skills/learn/tools/` (`learn.py`, `viewer/`, `tests/`) |
| Learning data (sessions, graph, practice, demos) | `~/.claude/learn-data/` (local git repo; override with `LH_DATA_DIR`) |
| Project links (internship course, progress, briefs) | `~/.claude/learn-data/settings.json` -> `paths` |

This repo still holds the course material the harness reads: `docs/learning/internship/` (lessons, `data/progress.json`).

## Use
- In any desktop session: `/learn <topic>` or `/learn continue`.
- Viewer: `python "$HOME/.claude/skills/learn/tools/learn.py" serve --port 8770`, then open http://127.0.0.1:8770
- Tests: `python -m pytest ~/.claude/skills/learn/tools/tests -q`

History: built on branch `learn-harness` (commits 65778f56..1a17b6d6), moved out in the commit that added this file.
