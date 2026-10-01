# Workflow B build plan

Spec (binding): `.agent-ops/grill-autonomous-flight-loop.md` (Q1-Q12 decisions, shared-understanding summary,
operator confirmation + additions A1-A4 of 2026-09-30). Facts: `docs/workflow-b/facts-firmware.md`,
`docs/workflow-b/facts-gs.md`. Wire contract: `docs/workflow-b/interfaces.md` (written before Task 1;
any change to it is a plan change, ledgered).

## Global constraints (every task)

- G1 Firmware C is Keil ARMCC V5.06 C89: declarations at block top, no VLAs, no `//`-only C99 constructs
  beyond what the file already uses, match surrounding style. Tunable sets use the `*_ROW` table pattern
  (`docs/firmware-table-pattern.md`, reference `API/pid.c`).
- G2 New firmware logic goes in small pure-C modules (no FreeRTOS/HAL includes) with host unit tests
  compiled by gcc; the RTOS files only call them. Keil build must end with 0 errors, 0 new warnings;
  report RW+ZI of main SRAM and CCM from `OBJ/JX_FLY.map` before and after.
- G3 Never write a number as measured unless measured in this task; thresholds are PROPOSED constants in
  one table with a comment saying so.
- G4 Protected set (Q10b) is changed only by the tasks that are explicitly allowed to (Tasks 1-2); each
  protected region in a mixed file is wrapped in `/* PROTECTED BEGIN <name> */ ... /* PROTECTED END */`.
- G5 No flying, arming, motor spin, flashing, or POSTs to the live service (port 8081) by any worker.
  Tests use fakes. The supervisor alone flashes.
- G6 Python: match the existing package style; pytest for every new module; run only the changed modules'
  tests plus the harnesses they touch (not the full tree). JS panels: extend the existing harness pattern.
- G7 Workers do not commit. They leave their changes in the working tree and write the digest. The
  supervisor reviews, reruns the acceptance command, and commits with explicit paths. Never commit OBJ/
  build products or test binaries.
- G8 Units: firmware position in the units the facts file states; every Python API names units in the
  argument name (`_m`, `_cm`, `_s`, `_deg`).
- G9 Minimum code for the task; nothing speculative; no duplicated logic (reuse existing resample, J,
  metrics, command sender, custody code).

## Lanes

Two lanes with disjoint file sets run in parallel, each strictly sequential inside:
- FW lane (worktree `.worktrees/wfb-fw`, branch `wfb-fw`): Tasks 1-5.
- GS lane (worktree `.worktrees/wfb`, branch `workflow-b`): Tasks 6-16. The FW lane merges into
  `workflow-b` after each reviewed task.

## Tasks

### Task 1: Protected Markers and GS Heartbeat
Goal: Establish the Q10b protected set and Q3 heartbeat-loss auto-land.
Lane: FW
Files: `TASK/StabilizerTask.c`, `TASK/send_data.c`, `API/rc_input.c`, `API/flight_fsm.c`
Interfaces consumed: none
Interfaces produced: none
Behaviour rules: Add `/* PROTECTED BEGIN <name> */` / `/* PROTECTED END <name> */` around the protected
sections in `StabilizerTask.c` and `send_data.c` (RC, arming, landing, bounds checks). In `rc_input.c`,
if heartbeat is lost while GS held authority and phase is FLYING, set `flight_phase = LANDING`
(existing touchdown path).
Tests: gcc host tests for the heartbeat-loss logic against a fake FSM state.
Verification: `grep -n "PROTECTED BEGIN" TASK/StabilizerTask.c`
Protected regions touched: Defines the regions.
Out of scope: Other safety limits (Task 2).

### Task 2: Safety Limits
Goal: Implement Q5 low-V backstop, Q6 fence/ceiling, and Q12 crash detect.
Lane: FW
Files: `TASK/StabilizerTask.c`, `API/safety_limits.c`, `API/safety_limits.h`, `tests/test_safety.c`
Interfaces consumed: none
Interfaces produced: none
Behaviour rules: Create `*_ROW` table with PROPOSED constants: fence +-1.1 x +-1.6 m, ceiling 1.5 m,
low_voltage 14.0 V, tilt 45 deg, timeout 120 s. Write pure-C module evaluating these against state.
In `StabilizerTask.c`, call it. On V/fence breach -> trigger `FLIGHT_PHASE_LANDING`. On crash detect
-> kill (zero motors, phase GROUND_IDLE).
Tests: `test_safety.c` verifies each breach condition triggers the correct safety action.
Verification: `make -C tests test_safety`
Protected regions touched: `/* PROTECTED BEGIN SafetyNet */`
Out of scope: GS-side limits.

### Task 3: Flight Primitives (Takeoff/Land)
Goal: Add CMD 0x1A takeoff and land paths (R4, A1, A2).
Lane: FW
Files: `TASK/send_data.c`, `TASK/StabilizerTask.c`, `TASK/RemoterTask.c`,
`API/primitives.c`, `API/primitives.h`, `tests/test_primitives.c`
Interfaces consumed: CMD 0x1A format from interfaces.md.
Interfaces produced: none
Behaviour rules: Implement CMD 0x1A parser in `send_data.c`. Takeoff: if armed, set motor idle, set
Z to hover_z (CMD value), wait for settle. Land: set target to origin (x=0, y=0) at hover_z, settle,
then enter `FLIGHT_PHASE_LANDING`.
Tests: Host unit tests verifying state transitions for takeoff and landing sequences.
Verification: `grep -n "0x1A" TASK/send_data.c`
Protected regions touched: `/* PROTECTED BEGIN FlightFSM */`
Out of scope: Trajectory execution.

### Task 4: Trajectory Executor Core
Goal: Buffer and interpolate trajectories in firmware (Q9b).
Lane: FW
Files: `API/trajectory.c`, `API/trajectory.h`, `tests/test_trajectory.c`
Interfaces consumed: none
Interfaces produced: C functions for begin, chunk_write, commit, clear, and evaluate_at_time(t).
Behaviour rules: Allocate `MRAC_CCM` buffer for 1200 points (x, y, z, yaw, t). `commit` performs CRC32
and bounds check against envelope (+-0.8 x +-1.3 m). `evaluate_at_time` linear-interpolates points.
Tests: `test_trajectory.c` verifies capacity limits, CRC failure, bounds rejection, and interpolation.
Verification: `make -C tests test_trajectory`
Protected regions touched: None.
Out of scope: Command integration.

### Task 5: Trajectory Command Wiring
Goal: Expose Trajectory Executor via CMD 0x1B and wire to AutoflyTask (Q9b).
Lane: FW
Files: `TASK/send_data.c`, `TASK/AutoflyTask.c`
Interfaces consumed: CMD 0x1B from interfaces.md, `trajectory.h` API.
Interfaces produced: none
Behaviour rules: In `send_data.c`, parse CMD 0x1B to call trajectory begin/chunk/commit/start/clear.
In `AutoflyTask.c`, if trajectory is active, read `evaluate_at_time(t)` and write to PID Des registers.
Trajectory ends when t > last point t.
Tests: Host tests verifying command dispatch to trajectory API.
Verification: `grep -n "0x1B" TASK/send_data.c`
Protected regions touched: `/* PROTECTED BEGIN Autofly */`
Out of scope: GS-side generator.

### Task 6: Battery and SoC Model
Goal: Pre-flight gate and in-air abort based on battery (Q5a, Q5b).
Lane: GS
Files: `ground_station/analysis/battery_model.py`, `ground_station/analysis/tests/test_battery.py`
Interfaces consumed: Telemetry real_voltage.
Interfaces produced: `predict_soc(resting_V)`, `is_sag_critical(loaded_V)`.
Behaviour rules: 4S LiPo curve to map resting V to SoC. Learn per-pack voltage drop/sag per 120 s
flight. Provide a pre-flight boolean gate (predicts >30% SoC). Provide an in-air trigger for abort.
Tests: Test with fake pack data showing drop learning and gate rejection < 30%.
Verification: `pytest ground_station/analysis/tests/test_battery.py`
Protected regions touched: None.
Out of scope: Hardware integration.

### Task 7: Path Generator and Timing Profile
Goal: GS pipeline for trajectory generation (Q9b).
Lane: GS
Files: `ground_station/service/trajectory_pipeline.py`, `ground_station/service/tests/test_traj.py`
Interfaces consumed: Existing presets or custom points.
Interfaces produced: `generate_trajectory(shape, timing_profile) -> list[Point]`
Behaviour rules: Implement pipeline: shape -> tilt rotation -> resample at fixed arc-length -> timing
profile (constant speed, trapezoid, S-curve) assigning timestamps to points.
Tests: Verify time monotonicity, velocity limits, and arc-length spacing exactly match parameters.
Verification: `pytest ground_station/service/tests/test_traj.py`
Protected regions touched: None.
Out of scope: Upload logic.

### Task 8: Trajectory Uploader
Goal: Validate and upload trajectories to firmware (Q9b).
Lane: GS
Files: `ground_station/platform/trajectory_upload.py`, `ground_station/platform/tests/test_upload.py`
Interfaces consumed: `generate_trajectory` output, CMD 0x1B specification.
Interfaces produced: `upload_trajectory(points) -> bool`
Behaviour rules: Validate points against envelope (+-0.8 x +-1.3 m), ceiling (1.5 m), max 120 s.
Upload points via CMD 0x1B chunk_write one float at a time. Send commit, verify Reject/Ack.
Tests: Fake bridge rejecting on CRC error; verify uploader retries or fails cleanly.
Verification: `pytest ground_station/platform/tests/test_upload.py`
Protected regions touched: None.
Out of scope: Execution.

### Task 9: Telemetry Campaign Manifest
Goal: Session telemetry recording with auto-rate finding (A3, R10).
Lane: GS
Files: `ground_station/livewatch/campaign_capture.py`, `ground_station/livewatch/tests/test_cap.py`
Interfaces consumed: existing `MultiSlotPresetManager`.
Interfaces produced: `start_campaign_capture(pack_id) -> str_path`
Behaviour rules: Define locked "campaign" preset including all shadow-mode/adaptive vars. Probe max
loss-free rate at start. Write a session-level JSON manifest mapping CSV columns for flightlab.
Tests: Verify manifest format matches schema and rate finder scales down on loss.
Verification: `pytest ground_station/livewatch/tests/test_cap.py`
Protected regions touched: None.
Out of scope: flightlab parsing.

### Task 10: Flightlab Adapter and J Score
Goal: Controller-agnostic interface to analysis engine (Q11).
Lane: GS
Files: `ground_station/analysis/workflow_b_adapter.py`, `ground_station/analysis/tests/test_wfb.py`
Interfaces consumed: Flightlab JSON outputs, `controller_descriptor.yaml`.
Interfaces produced: `compute_J(metrics) -> float`, `get_recommendations() -> list`.
Behaviour rules: Parse flightlab `metrics.json` and compute `J = median(rmse) + 0.25*mean(min(rmse,2))
+ 5*max(0, sat - 0.05)`. Load controller descriptors. Expose recommendations as tuner evidence.
Tests: Test math exactness against known sim `J` cases; parse a fake flightlab JSON.
Verification: `pytest ground_station/analysis/tests/test_wfb.py`
Protected regions touched: None.
Out of scope: Editing flightlab code.

### Task 11: Firmware Code Gate
Goal: Implement the 9-step gate for code changes (Q10c).
Lane: GS
Files: `ground_station/flashtool/code_gate.py`, `ground_station/flashtool/tests/test_code_gate.py`
Interfaces consumed: `artifact_custody`, Keil build wrappers.
Interfaces produced: `run_gate(changed_file) -> bool`
Behaviour rules: Parse protected-list diffs. Run Keil build and check `OBJ/JX_FLY.map` RAM limits.
Compile the real changed C file into the python bench (SIL) and run standard scenario. Auto-revert.
Tests: Unit tests passing/failing the SIL step with mock compiler.
Verification: `pytest ground_station/flashtool/tests/test_code_gate.py`
Protected regions touched: None.
Out of scope: Runner logic.

### Task 12: ADB Phone Recorder
Goal: Control Android phone recording over ADB (Q7b).
Lane: GS
Files: `ground_station/service/adb_recorder.py`, `ground_station/service/tests/test_adb.py`
Interfaces consumed: ADB CLI.
Interfaces produced: `start_recording()`, `stop_recording_and_pull()`.
Behaviour rules: Start scrcpy or intent recording at takeoff. Stop at landing. Pull file with PC clock
timestamp. Degrade cleanly (no-op with warning) if ADB is missing.
Tests: Test with mocked `subprocess.run` verifying ADB commands.
Verification: `pytest ground_station/service/tests/test_adb.py`
Protected regions touched: None.
Out of scope: Offline vision processing.

### Task 13: Campaign Runner
Goal: The deterministic B loop state machine (R13, Q12, Q8).
Lane: GS
Files: `ground_station/service/campaign_runner.py`, `ground_station/service/tests/test_runner.py`
Interfaces consumed: Gate (T11), Battery (T6), Uploader (T8), Adapter (T10).
Interfaces produced: `run_campaign(yaml_path)`
Behaviour rules: Enforce cooldown wait. Flash -> stream -> arm -> takeoff -> maneuver -> return ->
land -> analyze. Manage 3 abort levels: 1) in-flight -> 0x0D + origin land, 2) flight fail -> revert,
3) stop -> operator needed.
Tests: End-to-end unit test with mocks for hardware interactions.
Verification: `pytest ground_station/service/tests/test_runner.py`
Protected regions touched: None.
Out of scope: UI panels.

### Task 14: Campaign Dashboard Panel
Goal: Operator control surface (R12, A4).
Lane: GS
Files: `docs/dashboard-platform/shell/plugins/campaign-panel.js`
Interfaces consumed: dashboard plugin-api.
Interfaces produced: UI component.
Behaviour rules: Provide pack ID entry, checklist, "go" button. Toggle for `allow_agent_arm`. Display
Pause, Land, Abort buttons always. Show campaign queue progress.
Tests: JS harness `campaign_panel_harness.js`.
Verification: `node docs/dashboard-platform/shell/plugins/campaign_panel_harness.js`
Protected regions touched: None.
Out of scope: Backend routes.

### Task 15: Flight Campaign Claude Skill
Goal: Skill to clarify objectives and write YAML (A4).
Lane: GS
Files: `docs/skills/flight-campaign.md`
Interfaces consumed: None.
Interfaces produced: Skill instructions.
Behaviour rules: Write YAML frontmatter (name, description). Instruct Claude to interview operator,
validate objectives, write the campaign YAML, and remind operator to use the panel "go".
Tests: None (markdown only).
Verification: `grep -q "name: flight-campaign" docs/skills/flight-campaign.md`
Protected regions touched: None.
Out of scope: Implementation.

### Task 16: End-to-end Dry Run and Runbook
Goal: Validate the full B workflow against a fake drone.
Lane: GS
Files: `docs/workflow-b/RUNBOOK.md`, `ground_station/service/tests/test_workflow_b_e2e.py`
Interfaces consumed: `campaign_runner.py`.
Interfaces produced: Runbook doc.
Behaviour rules: Write operator steps (adb install, bench-check yaw, clamp phone, end wall mark,
pack label, crash threshold review, allow_agent_arm). E2E test runner vs mock bridge/fake drone.
Tests: E2E test verifying states progress correctly.
Verification: `pytest ground_station/service/tests/test_workflow_b_e2e.py`
Protected regions touched: None.
Out of scope: Hardware execution.

## Coverage

| Spec | Task |
| --- | --- |
| Q1 (Focus on B) | All |
| Q2 (Battery gating) | Task 13, 14 |
| Q3 (Link loss) | Task 1 |
| Q4 (Origin/Geofence) | Task 2, 3 |
| Q5 (Battery stop/gate) | Task 2, 6 |
| Q6 (Fence) | Task 2, 8 |
| Q7 (Phone camera) | Task 12 |
| Q8 (Cooldown) | Task 13 |
| Q9 (Trajectory YAML) | Task 4, 5, 7, 8 |
| Q10 (Gains/Autonomy) | Task 1, 11 |
| Q11 (Flight score) | Task 10 |
| Q12 (Abort criteria) | Task 2, 13 |
| A1 (Takeoff path) | Task 3 |
| A2 (Land path) | Task 3 |
| A3 (Telemetry preset) | Task 9 |
| A4 (Go surface) | Task 14, 15 |

## Supervisor amendments (2026-09-30, binding; override the task text above where they differ)

The task list above is a skeleton from the plan worker. `interfaces.md` v2 is the contract. Per-task detail
(signatures, test cases, source hooks) is in each worker brief, `.agent-ops/tasks-src/wfb-<id>.md`, written
just before dispatch.

### Firmware lane, restructured

| Id | Replaces | Work | Files (only these) | Acceptance |
|---|---|---|---|---|
| F1 | 4 | `wfb_traj`: buffer, CRC32, commit checks, time interpolation | `API/wfb_types.h`, `API/wfb_traj.c/.h`, `tests/firmware_host/test_wfb_traj.c` | gcc host test, `interfaces.md` section 3 |
| F2 | 2 | `wfb_safety`: evaluators + `*_ROW` limits table | `API/wfb_safety.c/.h`, `tests/firmware_host/test_wfb_safety.c` | gcc host test |
| F3 | 3 | `wfb_prim`: takeoff / return / settle / descend sequencer | `API/wfb_prim.c/.h`, `tests/firmware_host/test_wfb_prim.c` | gcc host test |
| F4 | 1, 5 | Integration: CMD 0x1A/0x1B handlers, `g_wfb_status`, 200 Hz hook, RC ch5 shared land, PROTECTED markers, Keil project entries | `TASK/send_data.c`, `TASK/StabilizerTask.c`, `TASK/RemoterTask.c`, `API/wfb_glue.c/.h`, `USER/JX_FLY.uvprojx` | Keil build 0 errors 0 warnings; map: main SRAM and CCM growth reported; supervisor review line by line |

F1, F2, F3 run in parallel (disjoint files; F2 and F3 include `wfb_types.h` from F1's brief verbatim).
F4 starts after all three merge. F4 is tier-0 work: highest model, no flashing by the worker.
Thresholds in `interfaces.md` stay PROPOSED; the worker copies them and marks each row `/* PROPOSED */`.

### Ground-station lane, corrected and extended

| Id | Skeleton task | Correction |
|---|---|---|
| G1 | 6 | per-pack registry (3 packs, spec line 68); resting-V -> SoC table is a PROPOSED curve, recalibrated from flights |
| G2 | 7 | output is `TrajPoint` with t; `validate` mirrors the five COMMIT checks; reuses `path_library` |
| G3 | new | `platform/wfb_commands.py` + `service/fake_drone.py` (needed by every later GS task) |
| G4 | 8 | uses `WfbClient`; CRC sent as two halves; retry from BEGIN |
| G5 | 9 | locked set includes shadow outputs and adaptive weights/features; rate from `probe_max_rate`, not a constant |
| G6 | 10 | J by `bench.objective` import; the copied formula in the skeleton is void |
| G7 | new | `controller_descriptor.py` + `tuner.py` + `controllers/pid.yaml`, `controllers/mrac.yaml` |
| G8 | 11 | nine Q10c steps; refuses files in the protected set |
| G9 | 12 | unchanged |
| G10 | new | `abort_monitor.py` |
| G11 | new | `campaign_schema.py` + example campaign YAML |
| G12 | 13 | runner takes injected dependencies; e2e against `fake_drone` |
| G13 | new | campaign API endpoints + MCP tools + `allow_agent_arm` gate |
| G14 | 14 | panel: per-battery go (pack ID + checklist), Pause / Land / Abort always visible |
| G15 | 15 | unchanged |
| G16 | 16 | unchanged |

Order: G1, G2, G3 first (independent). Then G4, G5, G6, G7, G9, G10, G11. Then G8, G12. Then G13, G14, G15, G16.

### Lanes

One worktree (`.worktrees/wfb`, branch `workflow-b`). Each worker runs on the VPS on its own branch
`vps/<id>`; the supervisor merges only the listed files. No `wfb-fw` worktree.

### Coverage changes

Q12 -> F2, G10, G12. Q10 -> F4, G8. Q9 -> F1, F4, G2, G4. A1, A2 -> F3, F4. A4 -> G11, G13, G14, G15.
Controller-agnostic tuning (Q11) -> G6, G7.
