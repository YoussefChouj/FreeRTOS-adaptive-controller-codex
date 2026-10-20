# Stream claude/land-sh-hardening-2026-10-20 — handoff page

(Overwrite at every task boundary; small.)

Tree: review worktree | Branch: `claude/land-sh-hardening-2026-10-20`, off main 15f5a5b7. Edit only this page,
never another stream's page.

## Goal

`tools/land.sh` said nothing while it waited, so on 2026-10-20 three different faults all looked like a slow CI:
a parser SyntaxError (fixed in 15f5a5b7), an exhausted unauthenticated rate limit, and (untested until now) a
refused token. The reply is now classified by `tools/land_ci.py`, which is unit-tested:

| GitHub reply | land.sh |
| --- | --- |
| 200, `check` run found | prints each new state (`queued`, `in_progress`), lands on `completed success` |
| no reply, 5xx, rate limit | keeps waiting, says why; rate limit says until when and to set `GITHUB_TOKEN` |
| 401, 403 (not rate limit), 404, other 4xx | stops at once with the fix (renew token, Actions: read, LAND_REPO) |
| parser crash | stops (was: "unknown" forever) |

New: `bash tools/land.sh --status <branch>` prints the CI state of the branch's commit, read-only.

Allowed-Paths: docs/agent/streams/claude-land-sh-hardening-2026-10-20.md, tools/land.sh, tools/land_ci.py, tools/test_land_ci.py, tools/check.sh

Acceptance-Cmd: bash tools/check.sh --changed

## Next

1. Operator: land with `bash tools/land.sh claude/land-sh-hardening-2026-10-20` (needs the operator's OK).
