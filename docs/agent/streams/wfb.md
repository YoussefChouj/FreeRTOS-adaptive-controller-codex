# Stream wfb - handoff page (overwrite at every task boundary, keep under 3 KB)

Tree: `.worktrees/wfb` | Branch: `workflow-b`. Edit only this page, never another stream's page.

## Goal now
Build workflow B (autonomous tuning flight loop) end to end. Spec: `.agent-ops/grill-autonomous-flight-loop.md`
(main tree). Contract: `docs/workflow-b/interfaces.md`. Plan: `docs/workflow-b/build-plan.md`.

## Facts (measured, dated 2026-10-01; branch pushed to origin/workflow-b @ dc928e6)
- Done: F1 `eb57ebf` (PASS 271), F2 `77d31cb` (PASS 165), F3 `d2b2306` (PASS 10), F4 `49f7df4` (glue wired into
  the 200 Hz loop; 16 scenarios PASS 2821; Keil build OK; NOT flashed), G1 `772b4f2`, G2 `e36e872`, G3 `4120682`,
  G4 `f10421e`, G9 `2e2f078`, G10 `33a595e`, G11 `5b8f329`, G7 `0f2732d` (61 passed), G5+G6 `dc928e6` (46 passed).
- F4, G7 and G5+G6 were rewritten inline after the worker output was rejected; records + deviations in the main
  tree `.agent-ops/tasks-src/wfb-{f4-v2,g7-inline,g56-inline}-plan.md` ("DONE <sha>" sections).
- Progress ledger with every ruling: `.superpowers/sdd/build-plan/progress.md` (git-ignored, this tree).
- Worker briefs: `.agent-ops/tasks-src/wfb-*.md` (main tree, untracked). Result branch `vps/<id>`; merge with
  `git checkout vps/<id> -- <paths>`, rerun the acceptance command, then `vps-worker.sh clean <id>`.

## Next actions (3 lines, most urgent first)
1. G8 (nine Q10c steps, refuses the protected set), then G12 (runner with injected deps, e2e on `fake_drone`).
2. G13 (campaign API + MCP + `allow_agent_arm` gate), G14 (panel), G15, G16 (build-plan.md:308-316 binding).
3. Whole-branch review (parked: yaw-rate limit, NaN dt in F3 timers, host double vs float32 edge, trajectory yaw
   sign), then operator: flash F4 image, bench, flights, merge.

## Do not
- No flight, arm, idle or flash from this stream during the build. No Claude Code subagents (operator, 2026-09-30).
- Do not trust a worker's `rc=0 status=OK`: check `git diff --stat HEAD...vps/<id>` first (qwen returned an empty branch).
- Every constant marked PROPOSED in interfaces.md needs the operator's review before the first campaign.

<!-- AUTO:BEGIN -->
Refreshed: 2026-10-01 10:15 (by hand; agent_handoff runs from the main tree only)
- Tree: .worktrees/wfb; branch / HEAD: workflow-b @ dc928e6; pushed to origin/workflow-b
- Last commits:
  - dc928e6 wfb G5+G6: campaign capture plan + flight scoring adapter
  - 0f2732d wfb G7: controller descriptors (pid, mrac) and a controller-agnostic tuner
  - 49f7df4 wfb F4: wire the Workflow B glue into the 200 Hz loop, RC and telemetry
  - c889103 wfb F4 v2: integration glue (API/wfb_glue.[ch]), not yet wired
  - 5b8f329 wfb G11: campaign YAML schema (all problems in one CampaignError) + example circle/figure8 campaign
<!-- AUTO:END -->
