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
