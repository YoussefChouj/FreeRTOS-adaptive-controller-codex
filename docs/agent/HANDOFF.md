# HANDOFF - current state (overwrite at every task boundary, keep under 3 KB)

Read order for a new agent: `AGENTS.md` -> this file -> `docs/agent/memory/rules.md` (+ `env.md` if you touch workers/hardware).
History is in `docs/agent/ledger/` (grep it, never read it whole).
2026-10-04 RUN (CEO, lanes on acct B): merged WP-31 (SIL of firmware controllers + scenario matrix), WP-34 (log replay; firmware thrust-estimator fix imu_total = m(a_z + g cos_tilt), NaN acc_z -> 0), WP-33 (ST + LFHG MRAC variants via CMD 0x1D fields 13-18, default OFF; PR Whatf filter fix; Keil 0 err/22 warn, Code=114160, NOT flashed), WP-35 (dashboard design system, flight strip, safety UX); merge fix drops a duplicate mrac_var_id extern in mrac_log_replay_host.c. OPEN BUG (WP-34 replay): MRAC reads imu_data.pit/rol as radians but they are degrees (API/mrac.c:318, 749-750), 510 would-be simplex trips in 1559 s of replay; WP-38 candidate. Merged WP-36 (ba50724: PID guards, named constants, *_ROW @unit metadata, `bash tools/check.sh` one gate = CHECK PASS; Keil 0 err/61 warn (48 of them #1267-D from untouched global_declare.h; once-per-recompiled-unit reading unproven), Code=114300, NOT flashed; run the Keil check from the PowerShell tool, Git Bash hits Errno 22 on USER/JX_FLY.uvoptx). Merged WP-37 (c597da5: firmware refactor to pid.c standard, g_tlm groups, subscribe slots in CCM; CHECK PASS, Keil 0 err/58 warn, Code=116652, NOT flashed). RUN 2026-10-04 DONE: read docs/agent/run-2026-10-04-summary.md; RUN 2026-10-04b (CEO inline, no manager/workers since 20:35 by operator order): merged WP-38 (6501565): MRAC deg/rad fix (replay: 0 would-be simplex trips, was 510), NaN re-arm guard, per-gain CMD 0x01 bounds (Z rate Kp bound 200->800), 0x19/0x1F drift closed, Keil 0 errors 0 warnings (was 58), NOT flashed. Workflow C DONE (041a970): `/workflow-c` skill + ground_station/analysis/flight_debrief.py (per-flight debrief.md, tracking + spectra plots, findings with bounded CMD 0x01 gain steps, history verdicts, next.yaml), thresholds PROPOSED, not flown yet. Merged WP-39 (a325fe3: dashboard alarm registry, preflight report with firmware pre-arm rows, drag-to-plot layouts, Telemetry Slots tabs; leftovers in docs/dashboard-platform ux audit). Merged WP-40 (38c37fd: API/prearm.c pre-arm checks report only (PREARM_ENABLE_ROW all 0), API/fw_health.c IWDG (FW_HEALTH_ROW enable 0) + reset cause + g_rtos_budget, g_tlm hlth group 68 floats, docs/firmware-safety.md; Keil 0E/0W, NOT flashed; uvprojx file-entry hunk committed). WP-42 DONE (555b1a7 P2 SIL fault injection, 5ede4af P3 session_schema block, 15fd8a3 P5 flight_review.py HTML; report docs/agent/reports/WP-42-cte.md; finding: TILT kill 0.22 s after true 60 deg, 45 deg PROPOSED). Next: WP-41/43. Lab: flash + manifest regen.
2026-10-04 NIGHT RUN (CEO, manager.sh cte/research on acct B): merged WP-23 (preflight GET+MCP, campaign_launch CLI, panel banner, failure-modes.md), WP-24 (docs/analysis/controller-roadmap-2026-10-03.md, test plan), WP-26 (ground_station/autotune FRF tuner, `excite` step, autotune_* campaigns), WP-28 (ground_station/livetune CMA-ES, `livetune` step, gain lease in API/pid.c default OFF, Keil 0 err NOT flashed), WP-29 (docs/agent/research/WP-29.md), WP-30 (notebooks digest + sim/bench/notebook_rpy_mrac.py). WP-27 merged a11f7e5 (MRAC variants V1/V2/PR/3L/V3 runtime OFF, CMD 0x1D; Keil 0 err/16 warn, Code=113440). Main NOT flashed (no HOLD_FLASH file exists; flash is step 0 of the lab plan). Lab plan: docs/agent/morning-2026-10-04.md.
2026-10-03 WP-22 DONE (report docs/agent/reports/WP-22.md): a reflash/FC reboot no longer needs an 8081 restart. Bridge re-subscribes by name on FC evidence (unnamed 0x08 / stale silent slot), axf not locked, one shared resolver, campaign begin refuses "stream not named", get_state.stream_naming_faults. Bench proof pending: reflash with 8081 up.
2026-10-03 wfb fly-mode DONE through g rung 2 (b9a34c1 skill, 7c6ccdc e2e, 20ef191 arm-permission preflight): /workflow-b skill at .claude/skills/workflow-b/SKILL.md, hover ladder e2e green on FakeDrone, 8081 restarted on the new code. Next is operator-only: props-off bench, then live ladder via /workflow-b (operator ticks Allow agent arm after each 8081 restart, arms by RC).
2026-10-03 WP-21 review (main, NOT flashed; HOLD_FLASH on): boot defaults now OF_BIAS_MODE_DEFAULT=2 (EKF) + g_ekf_of_vel_fb=1; ComputePID_Hold fix (U rebuilt from held Ui, finite guard); CMD 0x1E idx=4 g_of_full_tilt, idx=5 g_ekf_of_vel_fb (disarmed only); manifest regenerated; position_hold_of preset +15 WP-21 vars (s_ekf_of x/v/bias, rej, health, flags, of2_h_f2_v). Q/R unchanged (firmware = replay CHOSEN). Keil 0 errors in wt-night. Next: operator flash, handheld full-tilt sign test (idx=4), flight test.
2026-10-02 WP-21 (main, NOT flashed; HOLD_FLASH on): dd77482 EKF-OF 5-sigma OF innovation gate; 97b5a72 g_of_full_tilt (default 0, sign unconfirmed: handheld tilt+vertical test before enabling) + LANDING ground-contact xy I-hold (LAND_CONTACT_*). Keil 0 errors in wt-night. D (liftoff slide) refuted: <=1.5 cm.

This is the page of stream `main` (main tree: Keil build, flash, probe, 8081, merges). Other streams have their own
page in `.worktrees/<stream>/docs/agent/streams/<stream>.md`. Start every session with
`python -m ground_station.agent_handoff start [stream] --as <harness>`; see all streams with `... board`.
Overnight 2026-09-30 (session 013e8be7/14960682, claude-code): queue + results in `.claude_state.md` OVERNIGHT section.
2026-10-01 10:15: wfb `workflow-b` @ 50ea9e7 pushed: F4, G3-G7, G9-G11 done (G5+G6 dc928e6). Resume at the `.claude_state.md` NEXT line: intake s5a2, then item-3 / S5b / item-7 briefs, then wfb G8, G12.
2026-10-01 late: wfb CODE COMPLETE. `workflow-b` @ 82b0eea pushed: G8 + G12-G16 via WP-2..WP-7 (briefs `docs/agent/briefs/`, reports
on the merged commits). Sim dry run green: `python -m pytest -q -p no:cacheprovider ground_station/service/tests/test_workflow_b_e2e.py`
-> 7 passed in 8.98 s (CEO run); mid-flight land/abort assert traj_stop then land, no kill. Live (non-sim) campaign wiring is
NOT built (go -> 503 by design). Merged to main bfcbb89 (operator approved, pushed); FLASHED 2026-10-01 (operator approved, rebuild_and_flash --yes, JX_FLY_7ef44977.axf, livewatch verify 0 mismatches, stream_log 0 dropped). Trap: Keil open across a merge re-saves its stale uvprojx on close; check `git diff --stat -- USER/JX_FLY.uvprojx` before building. Open notes in
`.claude_state.md` line 28 (J=None tuner wrapper, sim service needs bridge=Mock, tier0_access full also sets allow_agent_arm).
2026-10-01 10:40: CEO -> manager -> worker loop works end to end. Hand a work package to the manager (account B, headless)
with `bash .agent-ops/manager.sh run <id>` (brief `docs/agent/briefs/WP-<id>.md`, rules `docs/agent/MANAGER.md`; report on
branch `wp/<id>`). Pilot WP-1 (gate.py byte-safe shims) is merged at 208cc25: 1 worker round, 229 s, GATE PASS, 15 passed, 1 skipped.
`mrac/next` = b8e4a38: S4 (d260303) + controller.c wiring of `MRAC_GetOutput`/`MRAC_LayerSelectStep` (gates green,
armcc v0..6 0 err). S5a (deep inner layers, variants 7/8) = UNREVIEWED partial `vps/s5a` 948de17 (worker TIMEOUT).
S3+ stays OFF main until the demo image is flashed and flown; main's `API/mrac*` stays at S2b.

2026-10-02 16:30 LAB DAY (WP-18): `night/2026-10-02` merged into main twice (236ba13 = WP-12..16, 1acb811 = +WP-14).
236ba13 is FLASHED (verify OK, 0 dropped). WP-14 (OF EKF tilt input k=0.242, shadow on, active path off) is on main but
NOT flashed and NOT Keil-built: HOLD_FLASH (the operator runs `rebuild_and_flash --yes` from main).
Read `docs/agent/reports/WP-18.md`, then `docs/agent/reports/2026-10-02-morning.md` (flights + what each decides).

2026-10-02 night, WP-20 REVERTED: hover_4 showed the vertical gate zeroed the position/velocity output 29% of
airborne time (takeoff 100%, landing 79-100%), so the drone flew open-loop and drifted. Main now has the pre-WP-20
StabilizerTask.c (= the hover_2 firmware, incl. bb2040c), built in wt-night 0 err / 5 warn, NOT flashed (operator flashes).
Lift-off hypothesis TESTED and REJECTED (2026-10-02 late): the unintegrated z 0.03->0.2 m window moved only
+0.4/-2.3 cm (hover_2, 0.16 s) and +0.9/-2.9 cm (hover_4, 0.28 s) by OF, vs a 20/30 cm tape offset in hover_2.
No firmware change shipped (it would fix about 3 cm and add ground-OF risk). The gap is OF-estimate error vs truth
(OF said about 5 cm, tape 20/30 cm). Measured candidate: vertical motion leaks 5-15 cm into OF position (handheld
test 2 table in WP-20.md). NEXT: ground truth first (top-down phone video of one hover, tape the landing), then
fix the phase where OF and video disagree. Main StabilizerTask.c = hover_2 firmware, unchanged.
hover_5 (2026-10-02, slot0/slot2 recorded 0 rows again, so no Z/yaw): tape 50 cm y. OF raw dy at rest (motors idle,
8-18 s) 0.00; during ground spool-up 18.5-21.5 s -0.5..-1.0 cm/s; s_of_bias_y locked -0.66; airborne raw dy mean
-0.61 for about 47 s, so the hold flew about 0.6 cm/s x 47 s = about 30 cm of fake y. Landing: OF saw only +8.5/+1.4 cm.
After landing at rest raw reads -1.6/-3.1 cm/s (ground OF unreliable). hover_2 bias was 0 and still 20/30 cm.
FIX A DONE (2026-10-02, StabilizerTask.c ~1056): the OF rest average and the Mode 0 bias stop at
`g_motor_idle_enabled` (props spinning), not at takeoff. Built in wt-night: 0 err / 5 warn, Code 108636. NOT flashed.
Open: B short fast final descent with control ON, C textured mat; true zero needs an absolute reference (marker).
LANDING FIX (2026-10-02 late, operator: no more flight tests, so this is log-justified only, NOT flown, NOT flashed):
(1) auto-land disarmed in mid-air on the ground-effect cushion (hover_7 at z 0.14 vs rest 0.10, unique_position1 0.13 vs 0.05),
so the motors cut 4-8 cm up and it dropped. Touchdown now also needs `Z_posPID.Des <= 0.01` (sink bias pushes through).
(2) near-ground vz spiked -0.65/-1.05 on a -0.30 command, and the operator saw less drift at slower descent. Two-stage ramp:
0.15 m/s below `LAND_SLOW_ALT` 0.40 m. `LAND_MAX_TICKS` is now 3000 (15 s). wt-night build: 0 err / 5 warn, Code 108692.
REJECTED F1 (leak feed-forward): handheld leak not deterministic (UP +10.5 vs DOWN -7.3 cm, ratio 2.5-10%).
FIRST FLIGHT WATCH: landing under 15 s, no cut above the ground, soft touchdown, skid during the powered ground phase (<= ~0.7 s).
auto_landing_1 (21:07, db8c16f FLASHED 21:03, 6 flights, F6 = stick flight): Des slope 0.300 above, ~0.17-0.21 in the
0.05-0.38 band, so the two-stage ramp is live. Disarm gate STILL fires in the air (F2-F5): Des leads FB by 13-19 cm, so
Des<=0.01 already at FB 0.16-0.18 and disarm at FB 0.11-0.14 vs rest 0.05-0.10. Next fix: gate on FB near the pre-arm rest z
(or thrust-low + no vertical motion, PX4-style), not on Des. Powered sink still spikes vz -0.64..-0.79 below 0.4 m (cause open).
Truth by hand-carry back to the takeoff mark (OF at ~5 cm height, scale unverified): F2 26, F3 10, F4 13, F5 18 cm (F1 47, carried
up to 0.84 m). Estimate at disarm was within 5 cm of 0 every time, so this is ESTIMATE error. Same side every flight: true
landing = ly -6..-20, lx 0..+16 -> systematic. Voltage 15.7->14.7 V, hover motor mean 3029->3074, p99 max 3450-3634 (log max
3938): no authority loss; F6 voltage = F5. Candidate upgrade: full roll/pitch/yaw rotation of OF (EKF2-style flow model);
earlier tilt test removed most of the locy leak (k 0.029 -> 0.001).
2026-10-02b (NOT flashed, wt-night 0 err / 5 warn, Code 108812): touchdown now also needs ground evidence, PX4 land-detector
style: FB <= rest z + 0.03 (rest sampled in GROUND_IDLE while idling, FB < 0.15) OR sink bias saturated at 0.40 (~2 s of
commanded descent not achieved). Measured rest pre/post: 0.00/0.06, 0.05/0.05, 0.06/0.10, 0.07/0.048, 0.05/0.06; the mid-air
cuts at 0.11-0.14 fail (a). (b) covers a shifted rest reading. REJECTED full-tilt OF rotation: replay of int(vz*sin(att))dt
per flight = -1.8..+1.0 cm (climb and descent cancel), so it cannot explain 10-26 cm; the earlier "tilt test" was confounded
by a constant -2 deg pitch. WATCH: powered ground phase may last ~2 s if rest z shifts -> skid with xy hold on ground OF.
Next candidate: zero xy correction on ground contact (PX4 does this; verify in PX4 source first).
Refs: docs.px4.io/main/en/advanced_config/land_detector.html, docs.px4.io/main/en/advanced_config/tuning_the_ecl_ekf.html
RECORDER 0-ROW ROOT CAUSE: 8081 kept running across the reflash. The new axf moved the RAM variables by 12 B, and the bridge
re-registered the default slot 0/2 echoes with a stale name table ("0 named, N unnamed"). Positional names never matched the
recorder's var names, so every row was empty and dropped. Slots 1/3 (names in the preset) were fine. WORKAROUND: restart 8081
(or re-apply the layout) after EVERY flash. Proper fix (worker): name echoes from the requested names or a resolver reloaded
on axf mtime, and raise a visible fault on unnamed ranges.
hover_6/7, hover_adaptation1, hover_ekf1, hover_ekf_adaptation_1 (fix A flashed; s_of_bias 0.00/0.00 in all): hold is
good (mean locxPID.U ~0); the error is born in the DESCENT. Per descent segment (|vz|>0.12): OF dEx/dEy = h7 +1.2/+13.9,
adapt1 -1.2/-0.5, ekf1 -1.5/-1.5, ekf_adapt +0.7/+5.3 cm, while mean locxPID.U = -8.6/-5.6/-4.9/-7.1 in every descent.
ekf1 (bias mode 2, q_ekf_of_vel_fb 1) = best (tape -30 cm x, landing only); OF ends within ~2 cm of start, so the
-30 cm is estimate error (fake +locx velocity during descent, consistent with -x truth if user x = locx; mapping unverified).
Mature-stack answer (PX4 EKF2_OF_POS + precland, ArduPilot FLOW_FXSCALER + PLND, DJI takeoff-imagery match): calibrate
flow, then an absolute marker for true zero. NEXT: (1) top-down video ground truth, (2) handheld vertical leak calibration
over the landing spot (raw flow vs vz and height), (3) textured mat, (4) downward camera + AprilTag pad.
2026-10-02 last lab logs (unique_position1, hanheld_takeof_landing_1, rc_sticks_3): handheld leak fit (dz/dt, z>0.12,
n=2225): fake locx vel = +0.058*vz (r 0.72), fake locy = +0.029*vz (r 0.43); EKF vel carries the same (0.056), so EKF
does not remove it. Equals a ~3.3/1.7 deg OF tilt OR the hand arcing (confound; repeat wall-guided). A linear leak cancels
over a symmetric climb+descent, so it cannot alone explain -30 cm at landing. RC log: operator used sticks only 0-16% of
airborne time; raw RolCtrler>3000 -> locxsPID.Des<0 (corr -0.74) and rollDes<0; descent rollDes mean -0.5..-1.1 deg in
all 7 landings; touchdown sink 0.4-0.9 m/s, z floor 0.05; ep4 operator kicked rollDes -15 deg at z 0.10. OF is blind and
earth pos frozen at touchdown, so skid is unobserved; operator: faster descent = more drift (fits touchdown/wake, not leak).
LANDING ramp = LAND_DES_STEP 0.30 m/s + sink bias up to 0.40 (StabilizerTask.c ~1356-1418); stick descent = THR*1.0 m/s.
NEXT lab: T1 wall-guided handheld (leak real?), T2 A/B landing normal vs slow final 0.3 m (tape+video), T3 roll-right in
hover -> user direction. Firmware after: F1 leak feed-forward of_v -= k*vz (GS-tunable), F2 two-stage land speed.

## Goal now
LAB DEMO (operator priority, 2026-09-30): PID vs PID+MRAC augmentation on (a) trajectory tracking (circle/figure8)
and (b) an asymmetric (off-centre) load. Then workflow B build (wfb stream), flightlab WP4, MRAC GS follow-ups.

## Facts as of 2026-09-30 15:40
- Demo tools in firmware (read in source, not flown by this session): runtime A/B `g_ctrl_select_req` via CMD 0x1F
  CTRL_SELECT (`API/controller.c`, `docs/research-platform/CONTROLLER_INTERFACE.md`); `circle_path`/`figure8_path`
  started by GS commands (`TASK/send_data.c:1670-1713`, `TASK/AutoflyTask.c`); simplex fade (`SIMPLEX.md`);
  record preset `flight_test_adaptive`; flightlab WP1-3 plugins incl. mrac (84 tests).
- NOT found in the ledger: any flight with MRAC injection on, or any flown circle/figure8. Treat both as first flights.
- Firmware at HEAD da55bce = S2b + operator tuning (yaw Ki 0.005, Throttle_th 3100/3050, rpm_dbg_rpm). Keil-built,
  axf matches committed source, NOT flashed. The drone runs an older build (last ledger flash 2026-09-29 08:20).
- Demo protocol written: `docs/lab/demo-pid-vs-mrac.md` (c7a89b4, source-checked). flightlab WP4b landed: compare +
  render 03d633b, ledger a4181fd (flightlab tree 133 passed). Agent classifies CMD 0x20..0x2B tier 0 (02d30b0; 0x1F and
  path cmds still unclassified). Replay desired z is metres by firmware (9d35f82). Ledger rebuilt 767ff7a (15 flights).
- flight16 tuning input `docs/analysis/flight16-tuning-input.md` (578ccb4): pos_x integrator saturated 98.9 %, roll rate
  e_rms 3x pitch, yaw-pair imbalance -21 %; MRAC shadow authority RMS(u_ad)/RMS(u_nom) 1.09-2.19 (threshold 0.5).
- Stale worktrees removed 2026-09-30 (win-p2/p3/p4/paths3d, both MRAC agent trees); branches kept.

## Next actions (most urgent first)
1. Operator in lab: `ah lock hw`, flash da55bce (`rebuild_and_flash --force --yes`), `livewatch verify`, props-off bench
   check: CTRL_SELECT toggles PID<->MRAC, MRAC telemetry streams, circle/figure8 start+stop on ground, simplex fade.
2. Before any MRAC-injected flight: lower gamma or `mrac_to_mixer` until the shadow authority ratio is < 0.5
   (flight16 had 1.09-2.19; see the tuning-input doc).
3. Run the demo per `docs/lab/demo-pid-vs-mrac.md`; analyze with `python -m ground_station.analysis.flightlab analyze <stem>`
   and `compare <stem_PID> <stem_MRAC>`.

## Do not
- Do not arm, idle or spin motors outside an operator-opened battery session (AGENTS.md > Authorizations).
- Do not start 8081 while the operator is streaming/flying (VOFA shares UDP 14550).
- Do not run a bare `git status` (about 200 dirty OBJ files).

<!-- AUTO:BEGIN -->
Refreshed: 2026-10-01 05:17 (mechanical, no LLM)
- Tree: .; branch / HEAD: main @ 578ccb4; unpushed commits: 1
- Dirty paths outside OBJ/: 181
  - M .agent-ops/served/estimator-panel.js
  -  M .claude_state.md
  -  M USER/JX_FLY.uvguix.Acer
  -  M USER/JX_FLY.uvoptx
  -  M docs/agent/HANDOFF.md
  - ?? .agent-ops/out/diag-empty-ui.md
  - ?? .agent-ops/out/e2e-live.json
  - ?? .agent-ops/out/ekf-modes.txt
  - ... +173 more
- Dashboard 8081: DOWN or unreachable
- Last commits:
  - 578ccb4 analysis: flight16 tuning input (measured numbers from flightlab report + compare vs flight15)
  - eda0d1b state: item 6 wiring b8e4a38, S5a spawned on VPS
  - 9e81c1b state: item 6 wiring + s5a brief in progress
  - 767ff7a flightlab: ledger.csv rebuilt from logs/vofa (15 flights); state: e2e done
  - a53191a flightlab ledger: tolerate motors.airborne/steady = None (rebuild crashed on a real log) + test
<!-- AUTO:END -->
