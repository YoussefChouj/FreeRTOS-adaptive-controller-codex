# Campaign 2026-10-01: drift fix + MRAC smoothing + logging + EKF shadow + flight-log analysis

CEO plan. Each WP below is a seed: the CEO writes it as a full brief `docs/agent/briefs/WP-<id>.md`
(template: `docs/agent/ceo-manager-architecture.md` section 3, example `WP-8.md`), runs it with
`bash .agent-ops/manager.sh run <id> [effort] [base]`, verifies, merges ff-only, and runs `manager.sh clean <id>`.
Evidence for every number below: `docs/agent/reports/2026-10-01-drift-investigation.md` (section in brackets).
Read that report's section before writing each brief, and do not copy a number that is not in it.

## Why (operator goal)
The operator's demo compares cascaded PID vs PID+MRAC on (a) an asymmetric off-centre load and (b) tracking of dense
trajectory waypoints. The demo and workflow B are blocked by a 10-14 cm hover drift. The next flight needs WP-9, WP-10
and WP-11 flashed as one bundle together with the unflashed 5c7fac2 (workflow-B safety net only in GS flights).

## Hard limits (unchanged)
- Workers: no Keil, no flash, no probe, no 8081, never arm or spin motors, never touch `OBJ/`.
- The CEO flashes reviewed and committed main only: `rebuild_and_flash --yes` (no `--force`), stop on non-zero exit,
  then `livewatch verify`, never while armed. Close Keil first, and check `git diff --stat -- USER/JX_FLY.uvprojx`.
- Never merge `mrac/next` or `workflow-b` into main. Never `git add -A` or bare `git status`. Run one heavy local job
  at a time (the laptop power is fragile). Never echo or commit API keys.
- Never commit `OBJ/*`, `USER/JX_FLY.uvprojx/uvoptx/uvguix.Acer`, `.agent-ops/served/estimator-panel.js`,
  `.agent-ops/manager.sh` or `docs/flights/ledger.csv`: another CEO session edits them. Commit by explicit pathspec.

## Work packages, in order (one at a time, base `main` unless stated otherwise)

| WP | What | Base | Blocks the next flight? |
|---|---|---|---|
| 9 | PID drift fix | main | yes |
| 10 | MRAC injection ramp and learning gate | main | yes |
| 11 | GS stream per-slot retry and preflight empty-slot check | main | yes (logging) |
| CEO | Flash the bundle (5c7fac2 + WP-9, 10 and 11) and livewatch verify. The operator flies shadow, then active, without a load. | main | - |
| 8 (rework) | Accel OF EKF: tilt input plus shadow mode | `wp/8` @ 299715e | no |
| 12 | Thrust estimator bug fixes and k_T calibration from logs | main | no |
| 13 | Deep cross-flight analysis of today's 14 flights | main (read-only plus an analysis script) | no; run it whenever the manager is idle |

### WP-9 PID drift fix [drift]
- Root cause: the velocity loop has Ki 0, and the position Ui is capped at 2 cm/s.
  Steady error e = (vFB + Uv/3 - Ui_pos)/0.8, predicted -10.7 cm vs measured -10.73 cm.
- `API/pid.c` rows 30-33 become set C6 (column order Kp Ki Kd UMax UpMax UiMax UdMax SumEMax EMin):
  `locx/locyPID  PID_ROW(0.8, 0.0013, 4.0, 300, 300,   5,  50,  3850, 10)`
  `locxs/locysPID PID_ROW(3.0, 0.008,  6.0, 600, 600, 100, 100, 12500, 10)`
  Fallback C7: velocity Ki 0.005. Add entries to the change history below the table.
- `TASK/StabilizerTask.c`, armed block ~1032-1048: the four loc PIDs integrate only in FLYING.
  - GROUND_IDLE / LANDED: zero SumE and Ui.
  - LANDING: freeze them, using the save-compute-restore pattern of Z_ratePID ~902-921.
  - Keep legacy anti-windup (`aw_mode` 0).
  - Optional: set velocity UMax to 263 (= the 15 deg lean clamp).
- Log `Ctrler.locxsPID.SumE` and `Ctrler.locysPID.SumE` in slot3 (`ground_station/comm/boot_default_layout.py`).
- Acceptance:
  - A Python closed-loop sim (`ground_station/analysis/drift_sim.py` + test) reproduces the report's numbers:
    - the current gains give about -13 cm of steady error;
    - C6 gives steady error < 0.5 cm and step overshoot < 15%.
  - A host gcc test of the FLYING-only gate (pattern `tests/firmware_host/test_wfb_glue.c`, header has the build line).
  - `armcc` is not available to workers; the CEO builds.

### WP-10 MRAC smoothing [mrac]
- Defects:
  - The weights learn on the ground and after disarm, and in shadow they wind up to the projection bounds.
  - Injection then adds the wound-up weights in one tick: 5.6-7.5 deg rms roll error in the first second
    (active15, active8).
  - active12 injected at liftoff and showed a 678-unit motor spread.
- Fix in `API/mrac.c` (adaptation ~368, flags init ~636-648) and the u_ad gate `API/controller.c:17`:
  - Reset the weights on the injection-on edge.
  - Scale both u_ad and the learning step by a 2.5 s smoothstep ramp.
  - Learn only in FLYING or LANDING, plus a 1 s hold after takeoff.
  - On disarm, set the ramp to 0 and reset the weights.
  - Use the table pattern for the constants (`docs/firmware-table-pattern.md`).
- Acceptance:
  - Host tests extend `API/tests/test_mrac_equiv.c`:
    - u_ad is continuous at injection-on (max one-tick step bounded);
    - no learning while on the ground;
    - weights are zero after disarm.
  - Main's `API/mrac*` stays on its current lineage. Do not pull code from `mrac/next`.

### WP-11 Stream slot reliability [logging]
- Defect: an FC reboot (battery swap) drops the RAM stream subscriptions. The GS re-sends the 4 slot requests once
  with no ack or retry.
  - The bridge watchdog re-sends only when all slots are silent for 3 s, and `/api/streams` still says "streaming".
  - Slots were lost in 3 of about 10 reboots: active6 slot0, pidonly7 slot3, active15 slot0.
- Fix:
  - The watchdog checks each slot: a slot silent for more than 3 s gets its own subscription re-sent, with retry.
  - `/api/streams` reports each slot's real liveness.
  - Add a preflight check that refuses (or loudly warns) when any subscribed slot is empty.
  - Python only. Test against `ground_station/service/fake_drone.py`.
- Also note in the report, as firmware follow-ups for WP-12:
  - the thrust estimator's `empirical[]` and `imu_total` are constant;
  - `of_alt_cm` logs the 0xFFFFFFFF sentinel.

### WP-8 rework (EKF) [ekf]
- Base `wp/8` @ 299715e (unmerged).
- Findings:
  - The innovation lag-1 autocorrelation < 0.3 criterion cannot be met: OF-only random walk scores 0.78-0.94. Drop it.
  - The `Lin_Acc` input is 4-34x worse than no input. Filtering it does not help, because the bad content is below 2 Hz.
- Fix:
  - Make the input tilt-only, g*sin(att).
  - Set R_of 1e-4.
  - Fix the `Of_RebaseKfBias` sign.
  - Tighten the health gate.
  - Add a shadow mode: the EKF runs every tick in all modes and is logged; only mode 2 feeds control. That is about
    20 lines.
- Then the CEO merges to main. Active mode is not ready: the operator A/B tests it only after the shadow logs look right.

### WP-12 Thrust and RPM [thrust_rpm]
- Fix the three estimators. They are telemetry only, so there is no control impact. Defects:
  - a unit bug;
  - an input that is never written;
  - a placeholder k_T that is 2.2x too high;
  - wrong RPM-to-motor pairing.
- Calibrate k_T offline from existing hover logs. Hover sum of omega^2 stayed within +-0.4% across 6 flights.
- Deliver a payload mass and CG estimator. It gives the ground truth for the asymmetric-load demo.
- Battery-sag thrust feed-forward is a later WP: write it as a proposal only.

### WP-13 Deep analysis and pattern recognition over today's flights (operator request)
- Logs, all `logs/vofa/*.meta.json` written 2026-10-01, load with
  `ground_station.analysis.flightlab.loaders.vofa.load_vofa`. Skip header-only slots with a temp meta; the replay in
  `wp/8` 299715e shows how.
  - Morning: `f17_hover_shadow`, `shadow2`, `active1`, `shadow3_ekf_active`.
  - Floor covering removed: `shadow4`, `shadow5_ekf_active`, `active5`, `active6`, `active8`, `pidonly7`,
    `shadow10`, `active12`, `shadow13`, `shadow14`, `active15`.
  - Ignore `log_test_*`, `test_loging`, `dash_*`.
- Deliver a per-flight table, one row per flight, with these columns:
  - mode;
  - firmware commit (from the ledger or meta);
  - hover duration;
  - position error mean and rms per axis;
  - estimated disturbance acceleration and how it trends over time;
  - attitude steady error;
  - fitted attitude lag;
  - MRAC weight norm over time;
  - battery V and hover PWM;
  - OF quality;
  - dead slots or signals.
- Then cross-flight patterns:
  - drift direction and size vs time-in-flight, battery, room position, floor covering and controller mode;
  - whether the disturbance looks like a wall or ground effect (does it depend on position?) or a trim/CG bias
    (constant in the body frame);
  - recurring oscillation frequencies;
  - takeoff and landing transients;
  - shadow vs active differences.
- Explicitly test the demo caveat: shadow14 roll held about 2 deg of steady error with angle Ui <= 2.4 and
  gyro Ui <= 10. Do the attitude-loop integrator limits make the PID baseline unfair for the asymmetric-load demo?
  Propose numbers, backed by a sim.
- Output:
  - `docs/agent/reports/WP-13.md` with the tables;
  - ranked hypotheses, each with its evidence and confidence;
  - the next 3 flight experiments that would separate them;
  - a reusable `ground_station/analysis/flight_patterns.py` (+ test on a synthetic log).
- Read-only on firmware.

## CEO checklist per WP
- Re-run the acceptance commands yourself; worker DONE is a claim.
- Run `git diff --stat main..wp/<id>` and check it against the allow-list.
- Read the diff, and check that the scope commits are `worker ...` commits.
- Merge with `git merge --ff-only`, push, and update `docs/agent/HANDOFF.md`.
- Before the flash: the operator must close Keil and the drone must be disarmed. Restart the GS stream so all 4 slots
  are live. Slot0 was dead in the 2026-10-01 evening session.
