# Stream claude/agent-token-2026-10-20 — handoff page

(Overwrite at every task boundary; small.)

Tree: review worktree | Branch: `claude/agent-token-2026-10-20`, off main 2fe3f769. Edit only this page,
never another stream's page.

## Goal

Agents find the operator's GitHub token without asking, and the repo says what it may be used for (operator
2026-10-20: "available to any agents working on my repo and behind the gate").

Allowed-Paths: tools/land.sh, tools/land_ci.py, tools/test_land_ci.py, docs/agent/governance-zones.md, AGENTS.md, docs/agent/streams/claude-agent-token-2026-10-20.md

## Facts

- `tools/land.sh` reads `~/.config/agent-secrets/github.token` (`GITHUB_TOKEN` wins, `GITHUB_TOKEN_FILE` overrides
  the path) and exports it, so `tools/land_ci.py` knows it is authenticated.
- Rules: `docs/agent/governance-zones.md` "GitHub token", plus an AGENTS.md authorization row.
- `tools/test_land_ci.py`: 18 pass; the new token test fails against the old land.sh.

## Next

1. Operator OK, then `bash tools/land.sh claude/agent-token-2026-10-20`.
2. Operator: replace the token with one without Administration (it can switch the ruleset off).

## Do not

- Print the token, or use it on rulesets, settings, secrets, merges or `main`.
