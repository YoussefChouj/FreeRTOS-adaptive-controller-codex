# Task completion checklist

This is a *reference*, not the gate. The gate is mechanical:

1. **`tools/check.sh` returned a pass that covers the diff** — recorded to
   `.agent_state/last_check` as `CHECK PASS scope=full` or
   `CHECK SUBSET PASS scope=<steps>`. The pre-push hook reads this and
   refuses to push when the recorded run does not cover what the diff
   requires: `bash tools/check.sh --changed` runs exactly those steps.
2. **`tools/agent_push_guard.py` returned 0** — the diff against
   `origin/main` is contained in the `Allowed-Paths:` line of the
   current branch's stream page, no tier-0 path slipped in unannounced,
   and the recorded check run covers the diff.
3. **`.githooks/pre-commit` returned 0** — the routed subset of
   `tools/check.sh` ran for the staged files.

When those three pass, the *agent's* job is to confirm — out loud,
before declaring the task done — that the four items below hold. They
do not gate the commit; they gate the task. If they don't hold, the
task is **not done** even if `git push` succeeds.

## What to confirm before declaring a task done

- [ ] **Acceptance criteria from the brief are met, not assumed.** If the
      brief said "compile clean", read the output and the build log, do
      not paraphrase it. If the brief said "no regression on `tests/X`",
      re-run `tests/X` and look at the result.
- [ ] **Every file in the diff is in the `Allowed-Paths:` of the stream
      page.** If not, the agent_push_guard script is the gate that
      refused it; do not silently re-add it via `--no-verify`.
- [ ] **If something out-of-scope was found, it lives in
      `docs/context/inbox/<task-id>.md`, not in this commit.** Read the
      file back; if it doesn't read like "what next agent needs to know",
      rewrite it.
- [ ] **If a durable rule changed, `docs/context/<shard>.md` or
      `AGENTS.md` is updated in the *same* commit.** Tombstone entries
      are append-only; cross-shard invariants live in both `flash.md`
      and `thesis.md`.

## Failure modes this file deliberately cannot prevent

- **Premature completion claims.** Rubber-stamping this checklist is
  still possible. The mechanical gates above bound the damage to "what
  the tools can verify", but the agent's *judgement* of whether the
  task's *purpose* is met is not checkable. When in doubt: state the
  doubt in the stream page; let the operator decide.
- **Conflicts between `flash.md` and `thesis.md`** when only one was
  edited. Each shard has a "cross-cutting invariant" footer; cross-link
  them. If you edited one and the other was not, note in the same
  commit why the edit does not violate the invariant; otherwise the
  invariant is "broken until further notice".
- **Doctored compliance text.** This file is for the next agent. If
  you mark a box without verifying, you are poisoning the next session.

## See also

- `AGENTS.md` > **Conventions** > "Strategic globally, tactical locally"
- `docs/context/README.md` — the router and its rules
- `tools/check.sh doc-paths` — the path verifier that catches
  `AGENTS.md` mentions of files that moved
- `tools/check.sh doc-budget` — the line-cap guard
- `bash tools/check.sh --changed` — run only the steps your diff needs
- `bash tools/check.sh --quiet` — one line per passing step, failures tailed
