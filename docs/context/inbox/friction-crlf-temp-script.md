# Friction: CRLF in a temp shell script cost a whole commit

**Trigger:** A commit helper written from a Windows working directory arrived
with CRLF line endings. Bash read the `\r` as part of the shebang, then ran
each following line as a command:

```
.agent-ops/_this-commit.sh: line 3: set: -: invalid option
AGENTS.md: command not found
docs/agent/HANDOFF.md: line 3: syntax error near unexpected token `('
```

Exit 127. No commit was made; the arguments (doc filenames) were executed as
shell commands instead. Cost: 2 wasted tool calls plus a large confusing log.

**Token/time cost:** ~2 calls, ~4k tokens of noise, one failed commit.

**Mechanical cure:** `*.sh text eol=lf` in `.gitattributes` (pins LF in every
checkout and worktree), plus `test_tracked_bash_scripts_are_lf` in
`tools/test_check_routes.py`, which fails the gate the moment a CRLF shell
script is committed.

**Not cured, and deliberately so:** the deeper habit. Never write a temporary
`.sh` just to run `git commit`; run `git commit -m "..."` directly, or
`bash .agent-ops/ship.sh "<msg>" <paths>`, which is LF-checked and
paths-scoped. A temp script costs calls and leaves untracked-file noise.

**Cure status:** done — `.gitattributes`, the gate test, and this note
(2026-10-20).