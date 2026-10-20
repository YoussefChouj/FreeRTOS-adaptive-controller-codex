# Stream claude/review-gate-2026-10-20 — handoff page

(Overwrite at every task boundary; small.)

Tree: review worktree | Branch: `claude/review-gate-2026-10-20`, stacked on the Map PR
(`claude/review-map-2026-10-20`, 8df7fa16). Edit only this page, never another stream's page.

## Goal

The gate half of the 2026-10-20 repo-architecture review (verdict: SPLIT, MERGE AFTER FIXES). It carries the
path-routed `tools/check.sh --changed`, the tracked hooks and the push guard from c461914f, with the review fixes:

| Finding | Fix |
| --- | --- |
| F-1 pre-commit fallback ran a hand-written third list | Router failure now runs every fast step; `--route-list --staged` routes the staged files |
| F-2 guard fail-open when routing breaks | `_required_steps` returns the full gate on any routing error |
| F-4 `AGENT_PUSH_TO_MAIN=1` invisible, sticky escape | pre-push refuses every push to main; `tools/land.sh` lands a CI-green branch with `--no-verify` |
| F-5 tier-0 list copied in two places | `tools/tier0.txt` is the only copy; approval is a committed `Tier0-Approved:` line, not `TIER0_OVERRIDE` |
| N-5 check.sh flag parse | argument loop; unknown flag or step exits 2 |
| N-6 ship.sh background push hid refusals | foreground push, refuses main, prints the log tail |
| stale `last_check` | the record carries `tree=`; the guard requires it to match the pushed files |

Allowed-Paths: docs/agent/streams/claude-review-gate-2026-10-20.md, docs/context/**, docs/agent/**, AGENTS.md, .gitattributes, .githooks/pre-commit, .githooks/pre-push, .agent-ops/ship.sh, tools/check.sh, tools/check_routes.py, tools/check_routes.txt, tools/tier0.txt, tools/land.sh, tools/install-hooks.sh, tools/agent_push_guard.py, tools/doc_paths.py, tools/host_tests.py, tools/test_install_hooks.py, tools/test_check_routes.py, tools/test_push_guard.py, API/tests/run_mrac_equiv.py, sim/sil/build.py

Acceptance-Cmd: bash tools/check.sh

## Next

1. Operator: land the Map PR, then this branch, with `bash tools/land.sh <branch>` (main never takes a plain push).
2. After this lands and the shared checkout is on main: `bash tools/install-hooks.sh --activate`, then add the
   GitHub ruleset on main (require `check`, block force-push and deletion, no bypass).
