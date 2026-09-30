# Stream wfb - handoff page (overwrite at every task boundary, keep under 3 KB)

Tree: `.worktrees/wfb` | Branch: `workflow-b`. Edit only this page, never another stream's page.

## Goal now
Build workflow B (autonomous tuning flight loop) end to end. Spec: `.agent-ops/grill-autonomous-flight-loop.md`
(main tree). Contract: `docs/workflow-b/interfaces.md`. Plan: `docs/workflow-b/build-plan.md`.

## Facts (measured this session, dated 2026-09-30)
- Done and committed: F1 traj `eb57ebf` (host test PASS 271), F3 prim `d2b2306` (PASS 10), F2 safety `77d31cb`
  (PASS 165), G1 battery model `772b4f2` (33 passed), G2 trajectory pipeline `e36e872` (8 passed).
- In flight: task G3 (`platform/wfb_commands.py`, `service/fake_drone.py`) on VPS worker `wfb-g3`, base `77d31cb`.
- Progress ledger with every ruling: `.superpowers/sdd/build-plan/progress.md` (git-ignored, this tree).
- Worker briefs: `.agent-ops/tasks-src/wfb-*.md` (main tree, untracked). Result branch `vps/<id>`; merge with
  `git checkout vps/<id> -- <paths>`, rerun the acceptance command, then `vps-worker.sh clean <id>`.
- Nothing of workflow B is in the firmware image yet: F1-F3 are pure-C modules with gcc host tests only.

## Next actions (3 lines, most urgent first)
1. Verify and merge `vps/wfb-g3`; then G4 (uploader), G5, G6, G7, G9, G10, G11; then G8, G12; then G13-G16.
2. F4 integration brief (tier-0: `TASK/send_data.c`, `StabilizerTask.c`, `RemoterTask.c`, new `API/wfb_glue.*`,
   uvprojx) on the high model; supervisor reviews line by line, Keil build 0 errors 0 warnings, map RAM check.
3. Final whole-branch review (parked: yaw-rate limit, NaN dt in F3 timers, host double vs float32 edge), then merge.

## Do not
- No flight, arm, idle or flash from this stream during the build. No Claude Code subagents (operator, 14:20).
- Do not trust a worker's `rc=0 status=OK`: check `git diff --stat HEAD...vps/<id>` first (qwen returned an empty branch).
- Every constant marked PROPOSED in interfaces.md needs the operator's review before the first campaign.

<!-- AUTO:BEGIN -->
Refreshed: 2026-09-30 20:08 (mechanical, no LLM)
- Tree: .worktrees/wfb; branch / HEAD: workflow-b @ 77d31cb; unpushed commits: no upstream (never pushed)
- Dirty paths outside OBJ/: 2
  - M .gitignore
  - ?? docs/agent/
- Last commits:
  - 77d31cb wfb F2: firmware safety net module (tilt, fence, ceiling, low-V, heartbeat, airborne cap)
  - e36e872 wfb G2: trajectory pipeline (shapes, tilt, resample, time profile, commit-check mirror, CRC)
  - 772b4f2 wfb G1: battery model and pack registry (pre-flight SoC gate, sag check)
  - d66dafd wfb interfaces: default-limit functions, defined behaviour, yaw range in COMMIT check 3
  - eb57ebf wfb: trajectory buffer and executor (task F1) with host tests
<!-- AUTO:END -->
