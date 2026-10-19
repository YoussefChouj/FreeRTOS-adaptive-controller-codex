# Argon review: PR #PRNUM (j-sysid-torque), read-only

Read-only: no edits, no commits, no git commands that change state. Worktree with the PR checked out: `C:\tmp$id`.
Spec it was built to: `D:\projects\UAV_lab\FreeRTOS-adaptive-controller-codex\.agent-ops\briefs$id.md` plus my answers in `D:\projects\UAV_lab\FreeRTOS-adaptive-controller-codex\.agent-ops\briefs$id-reply1.md`. Base branch overnight-2026-10-08
(`git -C C:\tmp$id diff origin/overnight-2026-10-08...HEAD --stat` to see the change).

Answer, with file:line for every claim, in at most 60 lines:
1. Spec coverage: one row per spec bullet and per reply decision: DONE / PARTIAL / MISSING, one line why.
2. Correctness bugs. Focus: d added only on target 1 and only when running AND airborne; zeroed on trip/land; target change refused while running; clamp table units match u_gyro*/Throttle_out; the var ID is unique in TASK/AutoflyTask.c s_var_write and the ground-station catalog; sysid_cdu struct vs telemetry_groups.py field order/size; stream budget guard respected; ISR/task concurrency (volatile, torn reads).
3. Default safety: is the default (flag/mask/target 0, or no new verb) bit-exact to the base branch? Name any path
   where the default behaviour changes, however small.
4. Code quality: magic numbers not marked PROPOSED, duplication with existing modules, tests that only test mocks.
5. A ranked fix list (most important first), each fix one line, concrete, phrased so I can paste it to the author.
