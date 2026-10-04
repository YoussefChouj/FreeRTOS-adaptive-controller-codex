Status: DONE (all acceptance commands pass; gate fails on size and on a clang-tidy environment error, see below)
Commits: 01b8638 - host build/step server; 49fbe58 - plant, scenarios, presets, engine, metrics;
  5f661fc - autotune on SIL, replay, CLI, tests; ff3ebc2 - matrix doc (on wp/31)
Gate: GATE FAIL: size,clang-tidy (scope PASS 20 files, ruff PASS 12 files, pytest PASS "18 passed in 3.34s").
  size 2061/200 lines: the brief's deliverables cannot fit 200 lines. clang-tidy: "sil_server.c:20 'io.h' file not found":
  the LLVM here targets MSVC and has no Windows SDK; the file compiles clean under MinGW gcc -Wall -Wextra.
Verification: python -m pytest -q sim/sil -> "10 passed in 290.38s (0:04:50)" (includes run_mrac_equiv: EQUIV OK);
  python -m sim.sil.run --matrix -> "wrote ...sil-matrix-2026-10-04.md (432 runs), wall 332.0 s" (4 workers).
Worker rounds: CTE, effort xhigh (no manager or workers, per the brief).
Deviations / open questions:
- Not ctypes: the only gcc is 32-bit MinGW and Python is 64-bit, so a DLL cannot load. sim/sil/build.py builds the real
  API/pid.c, mrac.c, mrac_math.c, controller.c (V2 deficit via -D__CC_ARM, its only use) into a step server driven over
  binary pipes, one process per row (each row has its own firmware globals). TASK/StabilizerTask.c is not
  host-buildable (FreeRTOS, HAL): its Compute_Motor/Update_Des/mixer/PWM clamp for the FLYING + TWC + OF-hold state is
  ported, line-cited, in csrc/sil_server.c. No python stand-in was needed.
- Presets: mrac_v*.yaml through the GS descriptor loader (same cmd/idx/value as flight). Injection uses CMD 0x0F idx 10.
  PR and 3L have no YAML: they use the WP-27 host-test values on V1 x0.25 (PROPOSED). g_ctrl_axis_mask has no
  send_data.c handler (probe in flight); the SIL takes it as 0x1F idx 1 (firmware_contract.py map).
- Plant: sim/bench/plant.py, with two changes kept in sim/sil (sim/bench untouched):
  (1) The rate gains per U come from constants.py: roll 8.08, pitch 9.06, yaw 1.13 deg/s^2/U. The bench's yaw 7.55
  (sim_coupled Jz = Izz/10) made the firmware yaw loop limit-cycle at about 200 deg/s.
  (2) OF is measured in the body frame and rotated by the yaw estimate. The bench feeds world velocity, which drove a
  growing xy oscillation once the heading drifted.
  Position is scored in the navigation frame because the bench gyro bias turns the heading up to 34 deg in 40 s.
- Measured: firmware ComputePID == fwpid's PID within 1e-4 x UMax; noise-free 0.3 m step, max |x_SIL - x_fwpid|
  0.15 cm (tolerance stated 0.5 cm).
- Matrix results (seed 0, SIL only, unvalidated):
  - The F path (trajectory_pipeline figure-8 at 0.3 m/s) has 1.5 cm radius tips that need 5.8 m/s^2, so pid
    already trips T (14.5 deg).
  - pid sags 15 cm in z at mass +15 % or battery sag: the Z_ratePID Ui cap is 109. MRAC holds 1.5 cm.
  - Every injected MRAC run trips abort U on yaw+z: the bias weights carry the yaw imbalance and the hover thrust offset.
    With Z masked, yaw alone still trips it.
  - Therefore all MRAC variants come out "do not fly" by the rule as written; pid "fly with limits" (F/pairs T).
    PROPOSED: redefine U (p/r only, or the change of u_ad) and slow or round F before battery 2.
- Firmware units bug (firmware read-only, not fixed): mrac.c:318 divides imu_data.pit/.rol (degrees) by
  rbf_ang_scale (rad), so the V3 angle grid saturates at ~0.3 deg; simplex roll/pitch_max are compared in degrees too.
- Autotune on the SIL: rate-loop PM with the firmware rows is 25 deg (roll) and 17 deg (pitch). After 3 rounds:
  rate Kp ~1.9, Kd 21; angle Kp ~2.8. Fewer clamps, but figure-8 RMSE is 2.13 x pid.
- Validation: logs/sessions and logs/campaigns are absent in this worktree, and the main checkout is outside my sandbox.
  So the matrix is UNVALIDATED. sim/sil/validate.py replays them, and a round-trip test covers the code path.
  CEO: run `python -m sim.sil.run --matrix` in the lab checkout.
- Host us/tick (~7, V3 ~13) is host CPU time, not STM32 cost.
