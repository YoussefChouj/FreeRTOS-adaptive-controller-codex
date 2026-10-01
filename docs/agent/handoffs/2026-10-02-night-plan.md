# Night plan 2026-10-01 -> 02 (overnight CEO, desktop acct A)

Read this after a compaction. Hard limits: `2026-10-01-overnight-context.md` (no flash/probe/8081/arm tonight,
building is OK, outside workers only, explicit-pathspec commits, never commit OBJ/* or the other session's files).

## Infra
- Integration branch `night/2026-10-02` (from main 5d9d83f), worktree `../wt-night`. Every WP uses Base = that branch.
  Merge `wp/<id>` into it after verification. Do NOT build in the main tree: main-tree `OBJ/JX_FLY.axf` must keep
  matching the flashed 3ae4a23 image for the operator's livewatch/stream_log. Build in `../wt-night`.
- VPS workers do not get untracked logs through git. The f17 logs were copied to the VPS:
  `/home/agent/data/logs/vofa/f17_*` (75 files, 106 MB, checked). Local path is `logs/vofa/` in the main tree.

## Measured this session (scratch scripts h1.py/h2.py; hover = Z_posPID.FB > 0.6 x p95, first 15% skipped)
- Motor means in hover: motor1/motor2 3070-3267, motor3/motor4 2744-2987. CORRECTED by the mixer
  (~1109-1145, u_gyroy = -gyroyPID.U): per motor roll=(M2+M3-M1-M4)/4, pitch=(M2+M4-M1-M3)/4, yaw=(M3+M4-M1-M2)/4.
  The M1/M2 vs M3/M4 split is YAW trim (115-157 ticks, yaw I cap 500, fine). Roll need 37-45 ticks/motor,
  pitch 5-20 (shadow10/14/4, active15). Exact match to gyroxPID.U / -gyroyPID.U / -gyrozPID.U in shadow.
  Model A then follows: gyro Ui cap 10 -> P term 30 -> 6 deg/s rate error -> angle Ui cap 2.4 -> 1.8 deg error.
- Shadow flights (PID drives the motors): rollPID.U +6.2..+8.7 deg/s, gyroxPID.U +30.7..+45.6;
  pitchPID.U +1.3..+4.3, gyroyPID.U +6.1..+21.0.
  Active flights (MRAC injected): rollPID.U 0.0..+0.8, gyroxPID.U -0.8..+2.9. MRAC carries the torque bias.
- Steady roll error (Des - FB): shadow10 +1.92 deg, shadow14 +1.81 deg on 3ae4a23, where the angle Ui cap is
  Ki 0.02 x SumEMax 120 = 2.4. Before 3ae4a23 (Ki 0.1, cap 12), shadow2/3/4/5 show +0.04/+0.14/+0.07/+0.12 deg.
  The rate-loop cap is 0.01 x 1000 = 10, against about 40 needed.
- Hover attitude FB in every hovering flight: roll -0.83..-1.59, pitch -0.65..-1.21 deg.
  Active des: roll -1.05..-1.49, pitch -0.64..-1.25.
- Hover accel means: Acc_X -10..-21 mg, Acc_Y -13..-30 mg. Ground before takeoff: X -29..+28, Y -7..+11.
  - Roll agrees with the accel (Lin_Acc_Y about 0).
  - Pitch disagrees by about 1.7 deg (Lin_Acc_X about -30 mg).
- yawPID.FB is about 0 in every flight (relative to the arm heading), so the logs cannot tell a body-fixed cause from
  a room-fixed one.
- pidonly7 and shadow13 never passed the hover filter (z about 0). Treat them as non-hovers until a worker shows otherwise.

## Working root-cause model (to be proven or overturned by WP-9/WP-10)
- **A. PID inner integrator caps are too small for the roll torque bias.** This gives a 1.9 deg steady attitude error
  in PID-only flight.
  - It is a regression from 3ae4a23: Ki was cut 5x and SumEMax was not raised.
  - It makes the PID-vs-MRAC demo unfair, because MRAC simply supplies the missing integral.
  - The asymmetric load is the same kind of torque bias, only larger.
- **B. Every flight needs a body-fixed lean of about -1.3 deg roll and -0.9 deg pitch to hold position.**
  - The outer loops supply it from a standing position error, because velocity Ki is 0 and the position Ui cap is
    2 cm/s.
  - Candidates: thrust-axis/IMU misalignment, level calibration, or an estimator pitch bias.
  - Fixes: attitude trim feed-forward plus outer integrators (C6) to clean up the rest.
- Tomorrow, take off at headings rotated 90 deg and 180 deg. If the lean rotates with the body, it is trim; if it
  stays fixed in the room, it is airflow.

## WP queue (at most 2 managers at once; all on the VPS lane, Gemini first)
| WP | What | Depends |
|---|---|---|
| 9 | Log analysis: quantify A and B per flight, motor-position map from the mixer, cap/trim numbers, push vs V/time, position-error budget | none |
| 10 | Calibrated cascade sim (real caps, rates, torque bias, lean bias); rank fixes for hover, asymmetric load and dense waypoints (velocity FF) | uses WP-9 facts (brief carries them) |
| 11 | MRAC smoothing in firmware: reset at injection-on, 2.5 s ramp, learn gate, disarm ramp; host test | none |
| 12 | Stream-slot reliability (GS): per-slot watchdog + stale state, replay wait/retry, logger faults, pre-arm slot gate, loader masks | none |
| 13 | Drift fix in firmware as WP-9/10 decide: inner caps, outer integrators gated to FLYING, trim, velocity SumE logged | 9, 10 |
| 14 | EKF tilt input: shadow + guarded active path (wp/8 merged at e7b6b48; brief committed) | none |
| 15 | Thrust/RPM telemetry fixes (CCR unit, k_T, imu_total input, rpm median-of-5) + `hover_thrust_id` k_T/mass/payload-torque analysis | none |

## Status log (append one line per event)
- 2026-10-01 ~21:30 plan written; logs on VPS; next: night branch + WP-9/WP-10 briefs.
- ~22:10 night branch + ../wt-night @ 5d9d83f; mixer decomposition (yaw split, roll 40 ticks); WP-9/10 briefs committed;
  managers launched from ../wt-night (`bash .agent-ops/manager.sh run <id> high night/2026-10-02`).
- 2026-10-01 ~22:40 briefs WP-11 (MRAC smoothing), WP-12 (stream slots, GS), WP-15 (thrust/RPM) committed; launch order when a slot frees: 11, 12, 15; WP-13 after 9+10.
- ~00:30 WP-9 PARTIAL -> CEO re-ran tests (6 pass) + CLI (12 hovering flights, 12.5 s) -> merged f63360d.
  Verdicts (CEO read of T3-T5): B dominates the standing position error (X -6.5 / Y +4.3 cm measured, A+B -6.3 / +4.5
  predicted; A alone small, opposite sign). A true but minor at today's need (worst 52 ticks). C NOT supported (T3 slopes
  flip sign across flights). Lean = accel tilt in every flight (Mahony levels on accel, so Lin_Acc~0 is a tautology; the
  1.7 deg pitch gap was a sign slip). Body-fixed vs room-fixed still open -> rotated-takeoff flight decides.
  WP-13 sizing: gyro Ui 157 (UiMax 160, SumEMax 16000), angle Ui 25.4 (UiMax 26, SumEMax 1300), trim roll -1.24
  (std 0.23) / pitch -0.90 (std 0.18). shadow13 skipped (p95 alt 0.1 m, brief hop only).
- ~00:30 WP-10 BLOCKED (ranking never ran; scratch files; ruff) -> CTE round launched with decisions 0b6edc9.
  WP-11 running. WP-12 pre-empted before any commit (slot given to WP-10 CTE); relaunch after 11 or 10.
- 2026-10-02 ~01:00 wp/8 (8-state OF EKF, code complete) merged into night at e7b6b48 as the WP-14 base; WP-14 brief written (tilt-only input, real shadow, rebase sign, persistence gate, KF velocity FB behind g_ekf_of_vel_fb=0). Queue: 12, 15, 14.

2026-10-02 ~02:00 WP-11 merged 17f08dc (MRAC smoothstep injection T_up 2.5 s, T_dn 0.5 s then freeze, learn gate
armed+FLYING/LANDING+1 s, weights reset on disarm/injection edge). CEO re-ran: EQUIV OK 3728208+4120208 lines, test_controller
pass, sigma-prior 56/0, mrac.c warnings = base, comm 258 pass. Known: slot0 real-ELF test fails until the Keil rebuild (mrac_inj
not in the old axf). Risks: a 1-tick phase flicker out of FLYING zeroes alpha at once (no alpha hysteresis); panel bug
command-panel.js:227-228 sends idx 1, should be 10 (not fixed, out of scope).
2026-10-02 ~02:20 WP-10 merged a341125 (CTE round). CEO re-ran: sim tests 22 pass, cascade_rank 87 s, tables match
docs/analysis/2026-10-02-fix-ranking.md. Verdict: PID F1x+F3+F5w+F6a beats F0 in all three scenarios (S1 steady/rms 1.2/3.8
vs 4.7/5.6 cm; S2 load step 2.2/5.0 vs 36.3/37.0; S3 rms 4.3 vs 30.9). Overturned: WP-9's F1w caps alone fail the load step
(EMin freezes the integrators), trim alone worsens hover, velocity Ki 0.005 (F4) adds nothing over F3. WP-13 brief 970756f
launched (bqt3qh7io) beside WP-12. Queue: 14, 15.
