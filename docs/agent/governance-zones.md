# Governance zones (machine-checked)

Where an agent may edit without operator review and where an operator must look at the diff. The mechanical
split is `tools/agent_push_guard.py` (run by `.githooks/pre-push` on every branch push) plus the GitHub ruleset on
`main`; this file is the rule they implement, so humans and agents agree on it.

| Zone | Coverage | Pre-push gate | Override |
| --- | --- | --- | --- |
| **Red** — flight-critical | The files in `tools/tier0.txt` (the only copy of the list) | Refused unless the stream page carries a committed `Tier0-Approved: <file>, <file> - operator <YYYY-MM-DD> "<their words>"` line naming each one. `Allowed-Paths:` does not cover them. | The operator's words, committed in the diff the reviewer reads |
| **Yellow** — core control / firmware / sim | The rest of `API/`, `TASK/`, `USER/`, `sim/bench/`, `ground_station/livewatch/`, `ground_station/comm/`, `ground_station/flashtool/`, `ground_station/analysis/`, `OBJ/JX_FLY.axf` | Diff must match `Allowed-Paths:` on the stream page; `.agent_state/last_check` must record a passing `tools/check.sh` run that covers the routed steps (`tools/check_routes.txt`) on the exact files pushed (`tree=`) | Standard PR review |
| **Green** — sandbox | `docs/context/inbox/`, `docs/context/*.md` (shard edits that do not change a tombstone or invariant), `docs/agent/HANDOFF.md` (stream-page refresh), `.agent_memory/`, `.agent_state/`, `logs/` (gitignored) | `tools/check.sh --changed` (doc-paths, doc-budget) | n/a |
| **main** | Every commit on `origin/main` | `.githooks/pre-push` refuses any push to `main`; the ruleset requires a green `check` CI run | `bash tools/land.sh <branch>`: the operator's call, after QA |

## Why this matters

`tools/agent_push_guard.py` is the *only* thing that knows whether a diff belongs to the current task. A prose rule
("don't touch the EKF") is rubber-stamped under context pressure; a `git diff --name-only origin/main...<sha>`
against `API/ekf.c` is not. An approval that lives in an environment variable is invisible and sticks in a shell;
an approval line in the committed diff is reviewed with the code it unlocks.

## How to extend the Red list

1. Add the path to `tools/tier0.txt` (one path or `*`/`**` glob per line).
2. Note the date and the operator instruction that prompted it in the same commit message. The prose in
   `AGENTS.md` is descriptive; `tools/tier0.txt` and this file are normative.

## GitHub token

The operator keeps a GitHub token for agents outside the repo, at `~/.config/agent-secrets/github.token` (Git Bash;
WSL: `/mnt/c/Users/Acer/.config/agent-secrets/github.token`), so any agent on this repo can use it without asking
(operator 2026-10-20). `tools/land.sh` reads it on its own (`GITHUB_TOKEN` in the environment wins;
`GITHUB_TOKEN_FILE` points elsewhere). Anything else reads it into a variable, never onto the command line:
`GH_TOKEN="$(tr -d '\r\n ' < ~/.config/agent-secrets/github.token)" gh ...`.

- **Use it for** this repo's CI runs and logs, branches, pull requests, issues and comments.
- **Never** print it, commit it, or copy it into a brief, log, prompt, PR or issue.
- **Never use it**, whatever GitHub lets it do, to merge a pull request or move `main` (`land.sh` only, the operator's
  call), or to change rulesets, branch protection, repository settings, secrets, collaborators, webhooks or deploy
  keys, or to delete a branch, tag or release. Those are the operator's. The ruleset is the gate; the token must not
  be the way around it.
- A 401 means it expired or was revoked: tell the operator. Do not look for another credential.
