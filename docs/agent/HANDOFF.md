# HANDOFF - current state (overwrite at every task boundary, keep under 3 KB)

Read order for a new agent: `AGENTS.md` -> this file -> `docs/agent/memory/rules.md` (+ `env.md` if you touch workers/hardware).
History is in `docs/agent/ledger/` (grep it, never read it whole).
LAB 2026-10-06 (payload + dense waypoints): read docs/agent/lab-2026-10-06.md first. Step 0 = flash HEAD (never flown), shakedown hover_ladder, then campaign payload_waypoints (noload -> sym -> asym, 9 flights, workflow C debrief each). Payload table PROPOSED: ~12 % of AUW no sag, 40 % saturates Z_rate; HOVER_THR_FREE is compile-time only. WP-43 file commits done (0ac7c6f..a78b403), coding standard + fw_lint gate 794cdfc.
2026-10-05 RAM (CEO, branch `ram-savings`, worktree ../FreeRTOS-ram-savings, NOT merged, NOT flashed): R2 1f43e89 (bmi088 drift fit from running sums, operator sign-off) + R1 3e79c8a (FreeRTOS heap in CCM): SRAM 124,984 -> 22,048 B (Keil map). Do NOT merge or flash it before the 2026-10-06 demo; merge after a bench run (g_rtos_budget stack high-water marks, gyro calibration converges), then regenerate capability_manifest.json. docs/firmware-ram-budget.md on the branch.
2026-10-05 WP-41 (CEO inline, no workers, NOT flashed): 13 API files at the pid.c standard (header + *_ROW tables), each its own commit with a fw_equiv/row-meta proof and CHECK PASS; report docs/agent/reports/WP-41-cte.md. Left: Ano_OF, GPS, bmi088_driver, delay, fw_identity, sys, tf_mini_plus. PROPOSED: delete SINS.c (no firmware caller) after a SINS.h split + Keil build.
2026-10-04 RUN (CEO, lanes on acct B): merged WP-31 (SIL of firmware controllers + scenario matrix), WP-34 (log replay; firmware thrust-estimator fix imu_total = m(a_z + g cos_tilt), NaN acc_z -> 0), WP-33 (ST + LFHG MRAC variants via CMD 0x1D fields 13-18, default OFF; PR Whatf filter fix; Keil 0 err/22 warn, Code=114160, NOT flashed), WP-35 (dashboard design system, flight strip, safety UX); merge fix drops a duplicate mrac_var_id extern in mrac_log_replay_host.c. OPEN BUG (WP-34 replay): MRAC reads imu_data.pit/rol as radians but they are degrees (API/mrac.c:318, 749-750), 510 would-be simplex trips in 1559 s of replay; WP-38 candidate. Merged WP-36 (ba50724: PID guards, named constants, *_ROW @unit metadata, `bash tools/check.sh` one gate = CHECK PASS; Keil 0 err/61 warn (48 of them #1267-D from untouched global_declare.h; once-per-recompiled-unit reading unproven), Code=114300, NOT flashed; run the Keil check from the PowerShell tool, Git Bash hits Errno 22 on USER/JX_FLY.uvoptx). Merged WP-37 (c597da5: firmware refactor to pid.c standard, g_tlm groups, subscribe slots in CCM; CHECK PASS, Keil 0 err/58 warn, Code=116652, NOT flashed). RUN 2026-10-04 DONE: read docs/agent/run-2026-10-04-summary.md; RUN 2026-10-04b (CEO inline, no manager/workers since 20:35 by operator order): merged WP-38 (6501565): MRAC deg/rad fix (replay: 0 would-be simplex trips, was 510), NaN re-arm guard, per-gain CMD 0x01 bounds (Z rate Kp bound 200->800), 0x19/0x1F drift closed, Keil 0 errors 0 warnings (was 58), NOT flashed. Workflow C DONE (041a970): `/workflow-c` skill + ground_station/analysis/flight_debrief.py (per-flight debrief.md, tracking + spectra plots, findings with bounded CMD 0x01 gain steps, history verdicts, next.yaml), thresholds PROPOSED, not flown yet. Merged WP-39 (a325fe3: dashboard alarm registry, preflight report with firmware pre-arm rows, drag-to-plot layouts, Telemetry Slots tabs; leftovers in docs/dashboard-platform ux audit). Merged WP-40 (38c37fd: API/prearm.c pre-arm checks report only (PREARM_ENABLE_ROW all 0), API/fw_health.c IWDG (FW_HEALTH_ROW enable 0) + reset cause + g_rtos_budget, g_tlm hlth group 68 floats, docs/firmware-safety.md; Keil 0E/0W, NOT flashed; uvprojx file-entry hunk committed). WP-42 DONE (555b1a7 P2 SIL fault injection, 5ede4af P3 session_schema block, 15fd8a3 P5 flight_review.py HTML; report docs/agent/reports/WP-42-cte.md; finding: TILT kill 0.22 s after true 60 deg, 45 deg PROPOSED). Next: WP-41/43. Lab: flash + manifest regen.
2026-10-04 NIGHT RUN (CEO, manager.sh cte/research on acct B): merged WP-23 (preflight GET+MCP, campaign_launch CLI, panel banner, failure-modes.md), WP-24 (docs/analysis/controller-roadmap-2026-10-03.md, test plan), WP-26 (ground_station/autotune FRF tuner, `excite` step, autotune_* campaigns), WP-28 (ground_station/livetune CMA-ES, `livetune` step, gain lease in API/pid.c default OFF, Keil 0 err NOT flashed), WP-29 (docs/agent/research/WP-29.md), WP-30 (notebooks digest + sim/bench/notebook_rpy_mrac.py). WP-27 merged a11f7e5 (MRAC variants V1/V2/PR/3L/V3 runtime OFF, CMD 0x1D; Keil 0 err/16 warn, Code=113440). Main NOT flashed (no HOLD_FLASH file exists; flash is step 0 of the lab plan). Lab plan: docs/agent/morning-2026-10-04.md.

Older entries (2026-09-30 .. 2026-10-03, incl. the landing/OF-drift investigation) are in
`docs/agent/ledger/handoff-2026-10-05-archive.md` (grep it).

## Next actions (most urgent first)
1. Lab 2026-10-06: follow `docs/agent/lab-2026-10-06.md` (flash HEAD of main, shakedown, payload_waypoints).
2. After the demo: bench-run branch `ram-savings`, then merge it and regenerate `capability_manifest.json`.
3. After the demo: bench-run branch `o2-build` (Keil -O0 -> -O2: Code 118,300 -> 92,284 B measured, DWT delays;
   speed not measured yet). Plan and numbers: `docs/firmware-compiler-optimisation.md` on that branch.
4. After the demo: bench-run branch `float-math` (cos/sin -> cosf/sinf on the stabilizer tick: Code -2,548 B,
   RO -284 B measured; speed not measured: compare hlth.stab_cpu_pct, loop_max_us). Doc `docs/firmware-float-math.md`.
5. Improvement backlog (CEO inline, one item per commit): `.claude_state.md` last entry.

## Do not
- Do not arm, idle or spin motors outside an operator-opened battery session (AGENTS.md > Authorizations).
- Do not start 8081 while the operator is streaming/flying (VOFA shares UDP 14550).
- Do not run a bare `git status` (about 200 dirty OBJ files).
- Do not run `git stash` (the operator keeps work in stash@{0}); filter OBJ/USER from every `git status`/`git diff`.

<!-- AUTO:BEGIN -->
Refreshed: 2026-10-05 05:33 (mechanical, no LLM)
- Tree: .; branch / HEAD: main @ 37dca9b; unpushed commits: 189
- Dirty paths outside OBJ/: 214
  -  M .agent-ops/manager.sh
  -  M .agent-ops/served/estimator-panel.js
  -  M USER/JX_FLY.uvguix.Acer
  -  M USER/JX_FLY.uvoptx
  -  M docs/agent/HANDOFF.md
  -  M docs/flights/ledger.csv
  -  M ground_station/agent_handoff/__main__.py
  - ?? .agent-ops/out/accept-0404.txt
  - ... +206 more
- Dashboard 8081: UP (HTTP 200)
- Last commits:
  - 37dca9b HANDOFF: ram-savings branch (R1+R2, SRAM 124,984 -> 22,048 B) is unmerged and must not be merged or flashed before the 2026-10-06 demo; state note
  - 31d93d1 Comment text: USER/*.uvprojx read as a nested comment by Keil (#9-D, 3 warnings); lab doc: measured full rebuild
  - 14db1d8 Run summary 2026-10-04/05: demo readiness, commits, PROPOSED RAM savings
  - 20ce10e WP-43 report (6 file commits, 5 PROPOSED findings) + firmware-structure not-yet list
  - c3b4780 docs: firmware RAM budget (map-measured) + PROPOSED savings R1 heap->CCM (DMA audit), R2/R3 calibration arrays (protected)
<!-- AUTO:END -->
