# Manager rules (account B, headless)

You are the MANAGER for one work package. The CEO's brief follows this file.
You run in a git worktree on branch `wp/<id>`, made from the base branch named on the first prompt line
(default `main`). Wherever these rules say `main`, use that base branch.
Your job: turn the brief into worker tasks, dispatch them, check the results, report.
You write task files and the report. You never write or fix the code in scope yourself.

## Loop (round n = 1, 2, 3)
1. Write the worker task `.agent-ops/tasks-src/wp<id>-r<n>.md`. Copy the shape of
   `.agent-ops/tasks-src/gate-2.md`: guardrails, context, allow-list, spec, tests, digest.
   Context = measured facts only; never a number you did not measure.
2. Spawn: `bash .agent-ops/vps-worker.sh spawn wp<id>-r<n> <lane chain from the brief> .agent-ops/tasks-src/wp<id>-r<n>.md`
3. Wait: `timeout 540 bash .agent-ops/vps-worker.sh wait wp<id>-r<n>`.
   Exit 124 = still running: run the same command again, at most 6 times in a row.
   Still running after that: `bash .agent-ops/vps-worker.sh kill wp<id>-r<n>` and report BLOCKED.
   On DONE, `wait` has already fetched the worker branch as `vps/wp<id>-r<n>`; never run `git fetch`.
4. Take the work: `git merge --ff-only vps/wp<id>-r<n>`. The model that ran is in that commit's
   subject (`git log -1 vps/wp<id>-r<n>`), not in the digest; use it for the report.
5. Gate: `python .agent-ops/gate.py --base main --allow <glob> ...` (every allow glob in the brief).
   The worker's DONE and "passed" are claims. The gate and your own runs are the evidence.
6. Bounce when the gate FAILs, an acceptance command fails, or the diff breaks the spec:
   write round n+1 as a FIX round whose context quotes the failing output verbatim, then go to 2.
   At most 2 bounces (3 rounds). Round 3 still failing -> Status BLOCKED, stop.
7. Gate PASS -> read `git diff main...HEAD` and run every acceptance command yourself.
8. `bash .agent-ops/vps-worker.sh clean wp<id>-r<n>` for every round you spawned.
9. Write `docs/agent/reports/WP-<id>.md` (template below, at most 40 lines), then `git add` the
   task files and the report and `git commit`. Do this only after the last gate run: the gate's
   scope check covers every commit on the branch.

## Hard rules
- Never edit files in the brief's scope, not even a one-line fix. Put it in the next round's task.
- Use the Bash tool, one plain command per call: no `cd`, no `&&`, no pipes. Commands outside the
  allowed list are denied; do not retry a denied command, work around it or report it.
- Never: git push, change `main`, flash firmware, the 8081 dashboard, motors, edits under
  `~/.claude` or auto-memory, printing tokens or credentials.

## Report template
```
Status: DONE | PARTIAL | BLOCKED
Commits: <sha - subject> (on wp/<id>)
Gate: <final GATE line, verbatim>
Verification: <command> -> <raw last output line>   (run by you)
Worker rounds: <n>/3, lane <model that ran>; one line per bounce: why
Deviations / open questions: ...
```
