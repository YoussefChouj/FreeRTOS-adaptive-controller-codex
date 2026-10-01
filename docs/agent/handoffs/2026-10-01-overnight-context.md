# Overnight context, 2026-10-01 -> 02 (from CEO session 14960682)

This is context, not orders. The overnight CEO decides what to do, in what order, and how to use the
manager and workers. The operator is asleep and the drone is in the lab: there is no drone, flash, probe or 8081.
You have the logs, simulations, host tests and firmware builds (Keil/armcc build is OK, flashing is not).

## What the operator wants (their words, condensed)
- The hover drift must be fixed as soon as possible. It blocks workflow B (automated flight campaigns) and the real
  goal: a demo comparing **cascaded PID vs PID+MRAC**
  1. with an **asymmetric off-centre load** on the drone;
  2. on **how closely each tracks dense trajectory waypoints**.
- "I need deep thinking here and a faster way forward." Go beyond the fixes already proposed: analyse today's real
  flight data for insights to act on tomorrow.
- Implement the fixes proposed today (velocity-loop I-term plus a higher position I-limit, the two MRAC smoothing
  changes, EKF with tilt/accel input in shadow and active, stream slot reliability). Fix dead log variables and use
  the RPM data productively.
- When the operator wakes up they want:
  - what was learned;
  - what changed (committed, tested, built but not flashed);
  - what to fly tomorrow and what each flight will decide.
- Reply style: plain and visual, bottom line first, tables, about 150 words.

## Hard limits (operator's, unchanged)
- No flash, probe, 8081 POST, arm or motor command tonight.
- Never merge `mrac/next` or `workflow-b` into main.
- Never `git add -A` or bare `git status`.
- One heavy local job at a time: the laptop's power is fragile.
- Never echo or commit API keys.
- No Claude Code subagents: outside workers only (manager.sh, agy, oc).
- Worker DONE / rc=0 is a claim. Check the diffstat against the allow-list, re-run the tests, read the code.
- Commit by explicit pathspec. Never commit `OBJ/*`, `USER/JX_FLY.uvprojx/uvoptx/uvguix.Acer`,
  `.agent-ops/served/estimator-panel.js`, `.agent-ops/manager.sh` or `docs/flights/ledger.csv`: another session
  edits them.

## Repo state at handoff
- `main` @ 1a4248b, pushed.
- The drone runs **3ae4a23**, flashed at about 19:15: workflow B plus angle-loop Ki 0.1 -> 0.02, which removed the
  0.9 Hz sway.
- **5c7fac2** is committed but not flashed: the workflow-B safety net (fence, ceiling, tilt, low-V) is active only in
  GS flights, so it no longer auto-lands RC flights.
- `wp/8` @ 299715e: unmerged accel OF EKF (8 states, ZUPT) plus a replay tool. Brief `docs/agent/briefs/WP-8.md`.
  Its accel input is wrong (see EKF below).
- `mrac/next` and `vps/s5a`: deeper MRAC work, off main.

## Evidence already gathered (read before re-deriving anything)
- `docs/agent/reports/2026-10-01-drift-investigation.md`: five read-only analysts covering drift, mrac, logging, ekf
  and thrust_rpm. Each has findings with numbers, a recommendation and risks.
- Per-flight notes written during the day: `git show <hash> -- .claude_state.md`.
  - 1536a40: shadow; MRAC-AUTH no-go.
  - 8a4373a: active1 + shadow3; OF creep, ground MRAC windup.
  - cc6ebd6: floor-removed logs; shadow5 was not EKF; descent drift unobserved by OF; 0.7 Hz outer-loop sway; trim lean.
  - a452437: active8 vs shadow.
  - 2b4ee5a: shadow14 vs active15.
- Loader: `ground_station.analysis.flightlab.loaders.vofa.load_vofa(meta)` -> `L.signals[name].t/.v`. It raises on
  header-only slot CSVs. The replay in `wp/8` 299715e shows the temp-meta workaround. Slot contents are in the WP-8
  brief "Facts" list.

## Today's flights (`logs/vofa/<name>*.meta.json`, all hover; shadow = MRAC computes, no injection)
| Time | Flight | Firmware era | Notes |
|---|---|---|---|
| 16:18 | f17_hover_shadow | before 3ae4a23 | first of the day |
| 17:00-17:19 | shadow2, active1, shadow3_ekf_active | before 3ae4a23 | white floor covering on; EKF mode 2 on in shadow3 |
| 17:55-18:09 | shadow4, shadow5_ekf_active, active5, active6 | before 3ae4a23 | covering removed from here on. shadow5 was NOT EKF despite its name; EKF on in active5; active6 slot0 is empty |
| 18:59-19:03 | active8, pidonly7 | before 3ae4a23 | pidonly7 was renamed from active7; its slot3 is empty |
| 19:29-19:50 | shadow10, active12, shadow13, shadow14, active15 | 3ae4a23 | active12 injected at liftoff (678-unit motor spread); active15 slot0 is empty |

Ignore `log_test_*`, `test_loging` and `dash_*`. Confirm the firmware boundary from the meta files and
`docs/flights/ledger.csv` (read-only).

## What is established (details and numbers in the investigation report)
- **Drift mechanism.** The velocity loop (`locxsPID`/`locysPID`) has Ki 0, and the position loop's Ui is capped at
  2 cm/s. A steady push therefore needs a standing position error:
  e = (vFB + Uv/3 - Ui_pos)/0.8. It predicted -10.7 cm; the log shows -10.73 cm.
  - The inferred push grows from 12 to 28 cm/s² during a flight, which is a 0.7 to 1.7° lean.
  - The loc loops run at 100 Hz (`cnt_loc>=2`), so per-tick Ki = Ki_c * 0.01.
- **Proposed gain set C6.** Simulated by the analyst against a plant fitted from the logs; not flown.
  - Rows (column order Kp Ki Kd UMax UpMax UiMax UdMax SumEMax EMin):
    position `PID_ROW(0.8, 0.0013, 4.0, 300, 300, 5, 50, 3850, 10)`,
    velocity `PID_ROW(3.0, 0.008, 6.0, 600, 600, 100, 100, 12500, 10)`.
  - Integrate only while FLYING; zero on the ground, freeze while LANDING.
  - Simulated steady error 0.1 cm, step overshoot 3-11%, phase margins 38/33° (velocity) and 61/56° (position).
  - Raising only the position limit gives 30-45% overshoot.
  - Fallback: velocity Ki 0.005.
- **MRAC.**
  - Weights learn on the ground and after disarm; in shadow they wind up to their bounds.
  - Injection adds them in one tick: 5.6-7.5° rms roll error in the first second (active15, active8).
  - Proposed fix: reset at injection-on, a 2.5 s smoothstep ramp on u_ad and on the learning step, learn only in
    FLYING/LANDING plus 1 s after takeoff, and ramp to 0 plus reset on disarm.
- **Logging.**
  - An FC reboot drops the RAM stream subscriptions. The GS re-subscribes once with no ack, and the watchdog re-sends
    only when ALL slots are silent. This lost a slot in 3 of about 10 reboots.
  - Dead signals: the thrust estimator's `empirical[]` and `imu_total` are constant, and `of_alt_cm` holds 0xFFFFFFFF.
- **EKF.**
  - Body Lin_Acc explains about 0% of the OF velocity change, and as a filter input it is 4-34x worse than no input.
  - A tilt-only g*sin(att) input explains 34-63%.
  - Filtering the accel does not help: the bad part is below 2 Hz.
  - Innovation lag-1 autocorrelation < 0.3 cannot be met: OF's own noise is colored. Drop that criterion.
  - Shadow mode is about 20 lines. Active mode is not ready.
- **Thrust/RPM.**
  - The three thrust estimators are telemetry only and all wrong: a unit bug, an unwritten input, a placeholder k_T
    2.2x too high, and wrong RPM-motor pairing.
  - RPM ch0-2 work. Hover Σω² stayed within ±0.4% across 6 flights while hover PWM shifted about -70 ticks/V, so k_T
    can be calibrated from the logs.
  - RPM will not fix drift. It can give payload mass/CG ground truth for the demo and battery-sag thrust feed-forward.

## Open questions and unverified hypotheses (session-end thinking; test them, do not trust them)
1. **Is the "wind" really an attitude-loop error?**
   - shadow14 held about 2° of steady roll error. The angle loop's Ui is capped at about 2.4 and the gyro loop's at
     about 10, and angle Ki was just cut 5x.
   - A 2° lean error is 981*tan(2°) ≈ 34 cm/s², the same size as the inferred 12-28 cm/s² push.
   - If the attitude loop cannot hold the commanded lean against a CG or motor torque bias, the outer loops see that
     shortfall as a push.
   - Then the right fix is at the inner loop (rate-loop I-limit and the torque trim), and the outer I-terms only mop up.
   - Test: compare des_roll/des_pitch with the attitude FB over the hover, check whether the rate/angle Ui sit at their
     caps, and look at the mean motor differential (the trim the controller is actually applying).
   - This decides whether the asymmetric-load PID baseline is fair. An off-centre load is exactly a torque bias.
2. **Level/trim bias.**
   - active6's hover mean Lin_Acc_X was -29 mg ≈ 1.7°, and cc6ebd6 noted a "trim lean".
   - An accelerometer level-calibration offset makes "level" mean tilted, which is a constant body-frame push.
   - Test: does the push rotate with yaw (body-fixed: trim, CG or IMU) or stay fixed in the room (airflow, wall)?
     Compare flights with different takeoff yaw or position.
3. **Why does the push grow during a flight?**
   - Candidates: battery sag changing motor asymmetry, motor or IMU heating (gyro bias), room recirculation, or a
     position-dependent wall or ground effect as the drone wanders.
   - Correlate the push with battery V, time and position across all flights.
4. **Dense-trajectory tracking may be dominated by lag, not drift.**
   - Position Kp 0.8 1/s with no velocity feed-forward gives a ramp lag of v/0.8. That is about 25 cm at 20 cm/s,
     and neither the I-terms nor MRAC remove it.
   - Check whether the trajectory path (`SDK_Set_V_Loc`, workflow-B trajectory upload) feeds velocity or acceleration
     feed-forward. If not, give both controllers the same feed-forward, or the demo compares two lags.
5. **Demo fairness.**
   - MRAC acts on the attitude loop, where a CG offset bites, so PID with capped inner integrators is a strawman.
   - Consider showing both: PID as flown today and PID with tuned integrators.
6. **Sim fidelity.**
   - The analyst's plant: attitude lag 60-140 ms delay plus τ 0.21-0.46 s, and a 0.5-1.2 cm/s raw OF velocity bias.
   - A sim calibrated against several flights, including a torque-bias input, would let tonight's work rank fixes
     and pre-tune the asymmetric-load case before tomorrow.

## Useful code anchors (line numbers approximate)
- `API/pid.c`:
  - loc rows 30-33, with a change history below the table;
  - the legacy anti-windup branch around 153-171;
  - `Clear_Structure` around 290-316.
- `TASK/StabilizerTask.c`:
  - Z_ratePID save/compute/restore pattern around 902-921;
  - `cnt_loc` divider around 926;
  - SumE zeroed at the arm edge around 975-981;
  - armed loc block around 1032-1048;
  - velocity setpoint clamp ±120 around 1333-1351;
  - `accel_to_lean_angles`, ±15° lean, around 1355-1390.
- `API/mrac.c`: adaptation around 368, flags init around 636-648. `API/controller.c:17` gates u_ad.
- Host C tests: `tests/firmware_host/` (the build line is in each file's header) and `API/tests/test_mrac_equiv.c`.
- Tunables follow `docs/firmware-table-pattern.md`.
