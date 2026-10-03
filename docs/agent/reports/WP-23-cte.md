Status: DONE (one CEO step left: install the skill, see Deviations)
Commits: e1a9ace cte(wp23) code + tests + docs; + this report (on wp/23)
Gate: GATE PASS (--base main --max-lines 3000; size 2246/3000, scope 31 files, ruff 23 files clean, pytest 60 passed) on e1a9ace
Verification (laptop, this run):
- python -m pytest -q ground_station/service/tests -> "10 failed, 762 passed, 6 skipped in 359.46s". 9 of the 10 also fail on main
  in this worktree (baseline run before any change: 27 failed): test_streams x7 (worktree OBJ/JX_FLY.axf lacks default-slot symbols,
  "unresolved variable(s)"), test_t5 opencode.json (untracked file, not in the worktree), all_panels_audit (preset-picker.js
  "sandbox.pluginInit is not a function", not workflow B). 10th = test_installed_skill_matches_the_source (CEO step below).
  The other 18 baseline failures are fixed: cp1252 decoding in 5 test files (encoding="utf-8") and the stale e2e test.
- node ground_station/service/tests/campaign_panel_harness.js -> ALL CHECKS PASSED (checks a-p, r).
- campaign_launch hover_ladder --pack TEST-1 -> table + "launch copy: logs/campaigns/launch/hover_ladder_20261003-2301.yaml"
  (deleted after); bad name -> exit 2, "error: unknown campaign 'no_such_campaign'; saved campaigns: example_circle, hover_ladder".
Worker rounds: CTE, effort xhigh (no manager/worker rounds).
Result:
- A: GET /api/campaign/preflight (campaign_preflight.py) -> {ok, instance, checks[10]} with raw fields; pass null = amber
  (rc_link: sbus_lost is not on the core stream). Reuses check_live_ready + streams.preflight_check. MCP tool campaign_preflight.
- #1 arm_state: core.arm_state read status.arm (sidebar alias, also fed by the legacy Frame A decoder) before
  DroneStatus.ARM_Status. Now the firmware variable decides; arm_sources() lists every fresh source; preflight shows them and flags
  SOURCES DISAGREE. Root cause of the live "armed" not proven (no live access); this is the misreport path found in code.
- #2 window.confirm/alert gone from campaign, approval queue, command, path panels: two-click "Confirm <action>?" 5 s; failed
  actions show in-panel. #3 static files sent Cache-Control no-cache. #4 MCP _http: GET retries once, unreachable -> 503 "retry",
  X-GS-Instance change -> "notice: 8081 restarted". #5 GET /api/campaign/vitals. #6 instance_guard: refuse at start before the
  bridge opens + SO_EXCLUSIVEADDRUSE bind (Windows SO_REUSEADDR let two bind). #7 every stop has a reason (below).
- B: python -m ground_station.service.campaign_launch; GET /api/campaign/list for the panel pickers.
- C: panel banner (service-computed state.banner), preflight table, pickers, big Pause/Land/Abort. campaign_state adds banner,
  phase, total_flights. E: docs/workflow-b/failure-modes.md (37 rows, each with its test).
- F review, fixed: (a) first flight waited min_rest_s (60 s PROPOSED) after Go with the drone RC-armed: pack rest counted from
  campaign start; a fresh pack is now rested. (b) a Go for a pack the runner is not waiting for hung forever -> 409. (c) a Go while
  flying was kept as a grant, so after a failed auto-next check the next flight flew without the operator -> 409. (d) arm_refused
  had reason "" -> names the stream check. (e) battery not streaming was a bare "error" -> operator_needed "battery: ...".
  (f) test_go_allow_agent_arm_false still expected arm_refused after 90e1c83 -> updated.
Deviations / open questions:
- Writing .claude/skills/workflow-b/SKILL.md was denied by the harness; not retried. The new skill is docs/skills/workflow-b.md.
  CEO: cp docs/skills/workflow-b.md .claude/skills/workflow-b/SKILL.md && git add -f .claude/skills/workflow-b/SKILL.md
  (test_installed_skill_matches_the_source then passes). test_workflow_b_skill content tests now read docs/skills/workflow-b.md.
- Firmware row cannot read build_id over the radio: it is not in the core log plan (livewatch out of scope). It checks flash
  custody (OBJ/.flashtool-cache) and axf mtime vs 8081 start, and compares build_id only if streamed. Adding build_id[0..3] and
  sbus_lost to campaign_capture STATUS would make rows firmware and rc_link fully green-checkable (PROPOSED).
- Thresholds PROPOSED, not measured: origin tolerance 0.30 m, fresh 2 s, slot stale 1 s.
- Not checked: whether the firmware keeps ARM_Status=1 on the ground after a landing (auto-next needs RC-armed).
