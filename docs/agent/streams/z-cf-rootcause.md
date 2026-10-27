# Stream z-cf-rootcause

Tree: `.worktrees/z-cf-rootcause` | Branch: `z-cf-rootcause` | Base: `ef56fe57`.
Allowed-Paths: TASK/StabilizerTask.c, TASK/StabilizerTask.h, TASK/AutoflyTask.c, API/tests/test_alt_cf.c, ground_station/lab/**, ground_station/livewatch/campaign_capture.py, ground_station/livewatch/tests/test_campaign_capture.py, ground_station/platform/tests/test_wfb_var.py, ground_station/platform/tests/test_alt_cf_var_guard.py, ground_station/service/campaigns/lab_z_cf_hover.fly, ground_station/service/campaigns/lab_z_cf_hover_legacy.fly, ground_station/service/campaign_live.py, ground_station/service/tests/test_campaign_outputs.py, tools/analyze_z_cf_b1.py, tools/test_analyze_z_cf_b1.py, tools/fw_trace/stab_trace.c, docs/lab/**, docs/agent/streams/z-cf-rootcause.md, .agent-ops/briefs/z-cf-rootcause*
Tier0-Approved: TASK/StabilizerTask.c, TASK/StabilizerTask.h, TASK/AutoflyTask.c - operator 2026-10-27 "Read .agent-ops/briefs/z-cf-rootcause-prompt.md in D:\Projects\UAV_lab\FreeRTOS-adaptive-controller-codex and follow it exactly. Work on branch z-cf-rootcause."

## Goal
Offline A1-A6, B1, C1-C4 complete; all five GPT-6.1 medium subagents finished and integrated. Hardware acceptance remains future operator work.

## Facts
H1 stale frame proven/fixed; timing history and optional spreading tested. Physical slow wobble remains unidentified: B1 points to H4 investigation; A5 proposed model fails reproduction.
Host strict C9924448checks PASS; default all-loop two20000tick seeds PASS; scoped pytest244passed/1skip (built AXF absent). Row metadata201cells0errors; compile DB113entries0missing. All39 planned flights preserved.
A2 default newest inlier; legacy sample mode available. Delay default0, spread/test defaultoff. Runtime settings apply disarmed; diagnostic never feeds control and clears on arm.
C2 wire calculation65800B/s with provisional new addresses; actual new image layout/cadence and target execution cost UNVERIFIED. No Keil/build/flash/probe/8081/drone operation.
Progress, per-item commits/test tails, full A5 table, findings and CEO questions: `.agent-ops/briefs/z-cf-rootcause.progress.md`.
Original main dirty edits preserved; runtime Ki0.001 versus committed default0.005 documented. One task navigation-friction log and supplied reference file remain unstaged.

Publication: normal feature push rejected by tier-0 guard on five files inherited from local-main base (196 commits ahead of origin/main); task did not edit them. Branch remains local. No bypass or broader approval. Raw feature-push.log and details in progress.

## Next actions
1. CEO review offline reports and resolve old-image hand-test and0.8->1.0m campaign prerequisites.
2. Authorized main session alone: reviewed build/identity/capture verification and target cost measurement.
3. Identify real latency/noise and H4 bias/vibration with saved motors-off/flight evidence; do not infer sensor rate from unchanged centimetres.

## Do not
No flash, probe,8081,drone,Keil,main merge/main push in this stream. No unreviewed flight or campaign amendment. No broad tests or push-guard bypass. Stage explicit paths only.

<!-- AUTO:BEGIN -->
Refreshed: 2026-10-27 13:14 (mechanical, no LLM)
- Tree: .worktrees/z-cf-rootcause; branch / HEAD: z-cf-rootcause @ 7976f9bb; unpushed commits: no upstream (never pushed)
- Dirty paths outside OBJ/: 4
  -  M .agent-ops/briefs/z-cf-rootcause.progress.md
  -  M .agent_memory/frictions.jsonl
  -  M docs/agent/streams/z-cf-rootcause.md
  - ?? .agent-ops/briefs/cf-rate-scaled-gains.cfvz_ref.py
- Last commits:
  - 7976f9bb docs(altitude): record root-cause evidence and completed agent handoff
  - 660303b4 test(lab): validate quoted flags and labelled ABBA commands
  - 1abbb602 docs(altitude): C3 order estimator bench and flight comparisons with explicit prerequisites
  - df7199a5 feat(capture): C2 budget coherent root-cause telemetry and preserve old-image capture
  - 43242837 fix(analysis): require effective timing evidence before certifying replay
<!-- AUTO:END -->
