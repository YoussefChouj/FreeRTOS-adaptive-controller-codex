# Argon review: PR #PRNUM (j-lab-thesis), read-only

Read-only: no edits, no commits, no git commands that change state. Worktree with the PR checked out: `C:\tmp$id`.
Spec it was built to: `D:\projects\UAV_lab\FreeRTOS-adaptive-controller-codex\.agent-ops\briefs$id.md` plus my answers in `D:\projects\UAV_lab\FreeRTOS-adaptive-controller-codex\.agent-ops\briefs$id-reply1.md`. Base branch overnight-2026-10-08
(`git -C C:\tmp$id diff origin/overnight-2026-10-08...HEAD --stat` to see the change).

Answer, with file:line for every claim, in at most 60 lines:
1. Spec coverage: one row per spec bullet and per reply decision: DONE / PARTIAL / MISSING, one line why.
2. Correctness bugs. Focus: each .fly parses with ground_station/service/fly_file.py and its computed airborne time (<=100 s memory, <=105 s Z); goto legs z-only; memory_premise.py segment timing vs the .fly timing, the A1 last-10-s / A3 first-5-s windows, re-convergence rule, weight drift formula, verdict text verbatim from the spec, INSUFFICIENT n; the log column names it reads exist in the repo logging config (ground_station/platform/telemetry_groups.py).
3. Default safety: is the default (flag/mask/target 0, or no new verb) bit-exact to the base branch? Name any path
   where the default behaviour changes, however small.
4. Code quality: magic numbers not marked PROPOSED, duplication with existing modules, tests that only test mocks.
5. A ranked fix list (most important first), each fix one line, concrete, phrased so I can paste it to the author.
