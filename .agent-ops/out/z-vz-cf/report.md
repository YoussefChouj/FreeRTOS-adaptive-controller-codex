# Final Verification & Hand-Back Report: Z Height / Climb-Rate Complementary Filter (`z-vz-cf`)

**Spec**: `.agent-ops/briefs/z-vz-complementary-filter.md`  
**Worktree / Branch**: `.worktrees/z-vz-cf` (`z-vz-cf`, branched from `main` at `e5d79e8cc2f3bbfa22ec7a1e24efef86e5cf8026`)  
**Default Mode**: `g_alt_vz_mode = ALT_VZ_MODE_FD = 0` (bit-exact with baseline firmware across both `test_alt_cf.c` U1 and `run_all_loop_equiv.py` 20,000-tick differential trace).  
**Every candidate gain and pass threshold below is labeled `PROPOSED` unless explicitly marked `MEASURED`.**

---

## 1. Branch, Commit List, and Files Changed

### 1.1 Progress Table (`.agent-ops/out/z-vz-cf/progress.md`)

| Piece | Status | Commit | Test Result |
|---|---|---|---|
| 4. Bench/flight procedure (A1-A3, F1-F2) | DONE | `d71ef9e4` | `doc_paths.py` OK, `test_commands_doc.py` 2 passed |
| 1. S1 closed-loop sim (gates gains) | DONE | `6aa93199` | `k1=0.21, k2=0.45, k3=0.10, AccMax=20.0` (`PROPOSED`); `dU` p99 `19.9x` lower, p90 `5.31x` lower, settle `-0.7%` |
| 2. Firmware (`Alt_UpdateFB` CF mode, `ALT_CF_ROW`, `g_alt_vz_mode`, `alt_cf.*`) | DONE | `44873f0d` | `armcc` 0 warnings/errors, `row_meta.py` 0 errors, `test_wfb_var.py` 12 passed |
| 3. Host tests U1-U5 + U6 (`test_alt_cf.c`, `row_meta.py`, `check.sh --changed`) | DONE | `d7a90df2` (+ `TASK/StabilizerTask.c` `Alt_CfReset` Mode-0 isolation fix) | U1-U5 15/15 checks PASS, `host_tests.py` 35/35 PASS, `all-loop-equiv` PASS, `check.sh --changed` PASS |

> **Note on Final Verification Fix (`TASK/StabilizerTask.c`)**: During `bash tools/check.sh --changed`, `all-loop-equiv` (`stab_proof`, 20,000-tick differential trace of `TASK/StabilizerTask.c` with `g_alt_vz_mode == 0`) caught that `Alt_CfReset()` was resetting `s_alt_reject_cnt = 0U` on arm/disarm transitions, which altered the Mode 0 (`ALT_VZ_MODE_FD`) 20-reject resync counter on random arm/disarm edges. Removing `s_alt_reject_cnt = 0U` (and `ano_of.of2_*` writes) from `Alt_CfReset()` made `TASK/StabilizerTask.c` 100% bit-identical to `main` across all 20,000 ticks of both random seeds while keeping all 15 checks of `test_alt_cf.c` (U1–U5) passing.

### 1.2 Commit Log (`git log main..z-vz-cf --oneline`)

```text
d7a90df2 test(alt-cf): add host unit tests U1-U5 for altitude complementary filter
44873f0d fw(alt-cf): add climb-rate complementary filter mode and ALT_CF_ROW table
6aa93199 sim(z-vz-cf): add closed-loop S1 cascade simulation and gain selection
d71ef9e4 docs(lab): add bench A1-A3 and flight F1-F2 run-sheet for z-vz-cf
```

### 1.3 Diff Stat (`git diff --stat main..z-vz-cf`)

```text
 .agent-ops/out/z-vz-cf/s1_metrics.md          | 105 +++
 .agent-ops/out/z-vz-cf/s1_sim.png             | Bin 0 -> 331757 bytes
 .agent-ops/research/z-vz-cf/evidence.png      | Bin 0 -> 164617 bytes
 .agent-ops/research/z-vz-cf/sim.png           | Bin 0 -> 211521 bytes
 .agent-ops/research/z-vz-cf/sim.py            | 571 ++++++++++++++++
 .agent-ops/research/z-vz-cf/sim_metrics.txt   |  26 +
 .agent-ops/research/z-vz-cf/zlog.py           |  25 +
 API/tests/test_alt_cf.c                       | 904 ++++++++++++++++++++++++++
 TASK/AutoflyTask.c                            |   4 +-
 TASK/StabilizerTask.c                         | 202 +++++-
 TASK/StabilizerTask.h                         |  39 ++
 docs/lab/run-sheet-z-vz-cf.md                 | 135 ++++
 ground_station/platform/tests/test_wfb_var.py |   2 +-
 tools/host_tests.py                           |   1 +
 14 files changed, 2000 insertions(+), 14 deletions(-)
```

---

## 2. S1 Closed-Loop & Open-Loop Simulation Results

### 2.1 Firmware Z Cascade Architecture & Physical `U`-to-Acceleration Gain $b_u$

- **Control tick**: `STAB_DT_S = 0.005f` s ($200\text{ Hz}$).
- **Outer loop (`Ctrler.Z_posPID`, `API/pid.c:33`)**: runs every `POS_LOOP_DIV = 2` ticks ($100\text{ Hz}$, `TASK/StabilizerTask.c:75,2131`) with `Kp = 0.7`, `Ki = 0.005`, `Kd = 0.1`, `UMax = 1.0` m/s, `UpMax = 0.9` m/s, `UiMax = 0.3` m/s, `UdMax = 0.3` m/s, `SumEMax = 30.0`, `EMin = 0.3` m (`AW_LEGACY`).
- **Inner loop (`Ctrler.Z_ratePID`, `API/pid.c:34`)**: runs every tick ($200\text{ Hz}$, `TASK/StabilizerTask.c:2146-2157`) with `Z_ratePID.Des = Z_posPID.U`, `Kp = 400.0`, `Ki = 0.435`, `Kd = 0.0`, `UMax = 300.0` CCR, `UpMax = 300.0` CCR, `UiMax = 100.0` CCR, `UdMax = 60.0` CCR, `SumEMax = 250.0`, `EMin = 10.0` m/s.
- **Physical `U`-to-vertical-acceleration gain assumed**:
  $$b_u = 0.013868\text{ (m/s}^2\text{) / CCR\_unit}$$
  justified by three independent cross-checks:
  1. **Calibrated SIL / bench quadrotor plant (`sim/bench/plant.py`)**: Each motor has linearized hover thrust slope $\text{DTDX\_H} = 0.004493468\text{ N/CCR}$ and quadrotor mass $\text{MASS} = 1.2961\text{ kg}$:
     $$b_u = \frac{4 \cdot \text{DTDX\_H}}{\text{MASS}} = \frac{4 \times 0.004493468}{1.2961} = 0.01386766\text{ (m/s}^2\text{)/CCR}$$
  2. **Hover-throttle physical scaling (`TASK/StabilizerTask.c:78,2483`)**: Free-flight hover collective is `HOVER_THR_FREE = 3050` CCR (`HOVER_PWM = 3090` CCR in `sim/bench/plant.py`), which is $\Delta\text{PWM}_{\text{hover}} = 1050\text{–}1090\text{ CCR}$ above the `2000` CCR zero-thrust baseline to balance $1g = 9.80665\text{ m/s}^2$. Linear thrust scaling gives $g / 1050 = 0.00934\text{ (m/s}^2\text{)/CCR}$ and quadratic thrust scaling gives $2g / 1050 = 0.01868\text{ (m/s}^2\text{)/CCR}$; the calibrated motor law $T(x) = A_1 x + A_2 x^2$ sits at the midpoint $0.013868\text{ (m/s}^2\text{)/CCR}$.
  3. **Empirical regression on Flight `20261024-213634-lab-d-excite-001` (`MEASURED`)**: Across the $41.1\text{ s}$ airborne segment of `logs/sessions/20261024-213634-lab-d-excite-001_f01_lab_d_excite/telemetry.csv`, regressing 3 Hz low-pass-filtered vertical acceleration $\ddot{h}_t$ against `Ctrler.Z_ratePID.U` yields slope $0.01487\text{ (m/s}^2\text{)/CCR}$ (within $7.2\%$ of $0.013868\text{ (m/s}^2\text{)/CCR}$).

### 2.2 Chosen Complementary Filter Gains (`PROPOSED`) & Discrete Pole Analysis

- `k1 = 0.21f` (`PROPOSED`) — height correction per accepted ToF sample ($20\text{ Hz}$, $T_{\text{ToF}} = 0.05\text{ s}$).
- `k2 = 0.45f` (`PROPOSED`) — climb-rate correction ($\text{s}^{-1}$) per accepted ToF sample.
- `k3 = 0.10f` (`PROPOSED`) — vertical accelerometer bias correction ($\text{s}^{-2}$) per accepted ToF sample (`b_az -= k3 * e`).
- `AccMax = 20.0f` (`PROPOSED`) — world-up linear acceleration clamp ($\text{m/s}^2$).

For state vector $x = [h, v_z, b_{az}]^T$, integrating over $10$ prediction ticks ($T = 0.05\text{ s}$) and applying the ToF correction $e = h_{\text{tof}} - h^-$ gives the discrete state-transition matrix $M = (I - K C) A$:

| Gain Set `(k1, k2, k3)` | Exact Complex Eigenvalues $z_i$ | Pole Magnitudes $|z_i|$ | Stability Verdict (`|z| < 1`) |
|---|---|---|---|
| `(0.21, 0.45, 0.00)` | `+0.8838 + 0.0948j`, `+0.8838 - 0.0948j`, `+1.0000` | `0.8888, 0.8888, 1.0000` | Marginal ($z_3 = 1.0$ unobservable bias state when $k_3=0$) |
| `(0.21, 0.45, 0.02)` | `+0.8849 + 0.0938j`, `+0.8849 - 0.0938j`, `+0.9977` | `0.8898, 0.8898, 0.9977` | **PASS** ($\max |z_i| = 0.9977 < 1$) |
| `(0.21, 0.45, 0.10)` **[PROPOSED]** | `+0.8899 + 0.0895j`, `+0.8899 - 0.0895j`, `+0.9876` | `0.8944, 0.8944, 0.9876` | **PASS** ($\max |z_i| = 0.9876 < 1$) |

### 2.3 Closed-Loop S1 Cascade Sweep Table

| Configuration | `abs dU` p90 (all / ToF CCR) | Ratio vs FD p90 (all / ToF) | `abs dU` p99 (all / ToF CCR) | Ratio vs FD p99 (all / ToF) | Step Settle 5% (s / vs FD) | Step Settle 2%+LSB (s / vs FD) | Z RMS (step / trk m) | $v_z$ RMS Error (m/s) |
|---|---|---|---|---|---|---|---|---|
| **Mode 0: FD + LPF (baseline)** | `25.81 / 156.24` | `1.00x / 1.0x` | `156.19 / 231.70` | `1.0x / 1.0x` | `6.290s (+0.0%)` | `6.795s (+0.0%)` | `0.1379 / 0.2944` | `0.1228` |
| `CF (k3=0.00, sigma=0.3 m/s^2)` | `3.66 / 4.48` | `7.05x / 34.8x` | `5.53 / 6.01` | `28.2x / 38.5x` | `5.930s (-5.7%)` | `9.940s (+46.3%)` | `0.1449 / 0.3048` | `0.0479` |
| `CF (k3=0.00, sigma=1.0 m/s^2)` | `4.92 / 6.16` | `5.25x / 25.4x` | `7.85 / 9.17` | `19.9x / 25.3x` | `5.930s (-5.7%)` | `9.940s (+46.3%)` | `0.1449 / 0.3076` | `0.0624` |
| `CF (k3=0.00, sigma=3.0 m/s^2)` | `10.84 / 13.76` | `2.38x / 11.4x` | `17.50 / 20.74` | `8.9x / 11.2x` | `5.930s (-5.7%)` | `9.940s (+46.3%)` | `0.1449 / 0.3204` | `0.1352` |
| `CF (k3=0.02, sigma=0.3 m/s^2)` | `3.57 / 3.84` | `7.22x / 40.6x` | `5.37 / 5.23` | `29.1x / 44.3x` | `6.160s (-2.1%)` | `6.770s (-0.4%)` | `0.1436 / 0.3052` | `0.0298` |
| `CF (k3=0.02, sigma=1.0 m/s^2)` | `4.84 / 5.69` | `5.33x / 27.5x` | `7.70 / 8.75` | `20.3x / 26.5x` | `6.160s (-2.1%)` | `6.770s (-0.4%)` | `0.1436 / 0.3081` | `0.0516` |
| `CF (k3=0.02, sigma=3.0 m/s^2)` | `10.82 / 13.56` | `2.39x / 11.5x` | `17.43 / 20.24` | `9.0x / 11.4x` | `6.160s (-2.1%)` | `6.770s (-0.4%)` | `0.1436 / 0.3217` | `0.1334` |
| `CF (k3=0.10, sigma=0.3 m/s^2)` | `3.49 / 3.36` | `7.39x / 46.5x` | `5.40 / 5.26` | `28.9x / 44.0x` | `6.205s (-1.4%)` | `6.750s (-0.7%)` | `0.1373 / 0.3059` | `0.0167` |
| **`CF (k3=0.10, sigma=1.0 m/s^2)` [PROPOSED]** | **`4.86 / 5.58`** | **`5.31x / 28.0x`** | **`7.85 / 8.40`** | **`19.9x / 27.6x`** | **`6.205s (-1.4%)`** | **`6.750s (-0.7%)`** | **`0.1373 / 0.3116`** | **`0.0463`** |
| `CF (k3=0.10, sigma=3.0 m/s^2)` | `10.83 / 13.03` | `2.38x / 12.0x` | `17.35 / 20.38` | `9.0x / 11.4x` | `6.205s (-1.4%)` | `6.750s (-0.7%)` | `0.1373 / 0.3272` | `0.1352` |

### 2.4 Open-Loop Replay on Flight `20261024-213634` (`lab-d-excite`)

| Estimator | $v_z$ RMS Error (m/s) | $v_z$ Mean Error (m/s) | Per-Tick $|\Delta v_z|$ p99 (m/s) | Thrust-Kick Proxy p99 ($400\times$) | Proxy Max | Lag (ms) |
|---|---|---|---|---|---|---|
| `today FD+LPF` | `0.098` | `-0.000` | `0.2004` | `80.1` | `162` | `65` |
| `CF k3=0, IMU noise 0.3 m/s2` | `0.048` | `+0.046` | `0.0230` | `9.2` | `13` | `0` |
| `CF k3=0, IMU noise 1.0 m/s2` | `0.061` | `+0.044` | `0.0253` | `10.1` | `16` | `0` |
| `CF k3=0, IMU noise 3.0 m/s2` | `0.130` | `+0.039` | `0.0446` | `17.9` | `32` | `0` |
| `CF k3=0.02, IMU noise 1` | `0.049` | `+0.020` | `0.0251` | `10.0` | `16` | `0` |
| **`CF k3=0.1, IMU noise 1` [PROPOSED]** | **`0.045`** | **`+0.004`** | **`0.0250`** | **`10.0`** | **`16`** | **`0`** |

### 2.5 Simulation Artifacts & Figures

- Closed-loop S1 + open-loop 4-panel figure: `.agent-ops/out/z-vz-cf/s1_sim.png`
- Detailed S1 metrics document: `.agent-ops/out/z-vz-cf/s1_metrics.md`
- Open-loop 3-panel replay figure: `.agent-ops/research/z-vz-cf/sim.png`
- Combined S1 + open-loop simulation script: `.agent-ops/research/z-vz-cf/sim.py`

---

## 3. Raw Output Tails of U1–U6 Runs

### 3.1 Direct Execution of `API/tests/test_alt_cf.c` (U1–U5)

```text
$ gcc -std=c99 -Wall -Wextra -Werror -Wdeclaration-after-statement -Wvla -ITASK API/tests/test_alt_cf.c -lm -o test_alt_cf && ./test_alt_cf
U1 PASS: mode FD bit-exact on 2000-tick 213634 trace (bit_mismatches=0, csv_max_diff=1.34e-005 m/s)
U2 PASS: climb 0.30 m/s, 20 Hz 1 cm ToF -> CF vz_err_max=0.0043 m/s (<=0.02), CF |dvz|_max=0.0016 m/s (<0.01), FD |dvz|_max=0.3850 m/s (>=0.18)
U3 PASS: accel-only step a_up=1.0 m/s^2 for 0.5 s (ToF frozen) -> dvz=0.500000 m/s (0.50+-0.01), dh=0.125000 m
U4 PASS: bias 0.20 m/s^2 -> k3=0.10 converged within 10% at t=9.70 s (<10 s, b_az=0.1815); k3=0 |vz|_max=0.0973 <= bound 0.0983 m/s
U5 PASS: 20 consecutive jump rejects predicted-only (n_corr=1 held), then resync set h=1.50 m and preserved vz=0.010999 m/s
ALL 5 TESTS PASSED (U1-U5) (15 checks)

$ python tools/host_tests.py alt_cf
PASS alt_cf: ALL 5 TESTS PASSED (U1-U5) (15 checks)
host tests: 1/1 passed (1 built, 0 cached)
```

### 3.2 Full Host Test Suite (`python tools/host_tests.py`)

```text
PASS drift_fix: PASS: TrajFF going inactive
PASS pid_guards: 700171 checks, 0 failure(s)
PASS ekf_of_shadow: 8 passed, 0 failed
PASS rpm_median: Results: 5 passed, 0 failed
PASS thrust_estimators: Results: 42 passed, 0 failed
PASS mrac_sigma_prior: 416 checks, 0 failure(s)
PASS mrac_prior_l3: PASS: 22/22 checks passed
PASS mrac_xy_ref_row: PASS  66/66 checks
PASS mrac_inputs: mrac_inputs (variant 2): 68 checks, 0 failure(s)
PASS mrac_inputs_rbf: mrac_inputs (variant 1): 42 checks, 0 failure(s)
PASS mrac_inputs_multi: mrac_inputs (variant 2): 68 checks, 0 failure(s)
PASS mrac_z_feat: 51 checks, 0 failure(s)
PASS mrac_l2_gate: 26 checks, 0 failure(s)
PASS mrac_pos_layer: PASS: 38/38 checks
PASS mrac_group_layers: mrac_group_layers: 45 checks, 0 fails
PASS mrac_xtlm: 17 checks, 0 failure(s)
PASS mrac_sizeof: mrac_var_id[AXES] (16-bit since WP-33)            12 B
PASS fw_controller: test_controller: all passed
PASS mixer: 4800000 checks, 0 failure(s)
PASS subscribe_8_slots: 52 checks, 0 failure(s)
PASS fw_ekf_gate: ALL PASS
PASS fw_idle_decouple: Tests passed.
PASS fw_wfb_traj: PASS 271
PASS fw_wfb_safety: PASS 326
PASS fw_wfb_prim: PASS 11
PASS fw_land_xy_gate: land_xy_gate: 3 tests passed
PASS fw_wfb_glue: PASS 6865
PASS fw_wfb_prog: PASS: 233 checks, 0 failures
PASS fw_prearm: test_prearm: 28 checks, 0 failed
PASS fw_health: test_fw_health: 25 checks, 0 failed
PASS sysid_torque: test_sysid_torque: all passed
PASS fw_pid_fallback: test_pid_fallback: all passed
PASS fw_exp_row: fw_exp_row: all checks passed
PASS mrac_xy_nofight: mrac_xy_nofight: all 4 kinds passed no-fight and disturbance rejection
PASS alt_cf: ALL 5 TESTS PASSED (U1-U5) (15 checks)
host tests: 35/35 passed (34 built, 1 cached)
```

### 3.3 U6 Static & Equivalence Checks (`row_meta.py`, `test_wfb_var.py`, `armcc`, `check.sh --changed`)

```text
$ python tools/row_meta.py
row-meta: 38 tables, 1447 cells in range, 0 error(s)

$ python tools/row_meta.py TASK/StabilizerTask.c TASK/AutoflyTask.c
row-meta: 8 tables, 561 cells in range, 0 error(s)

$ python -m pytest -q -p no:cacheprovider ground_station/platform/tests/test_wfb_var.py
............                                                             [100%]
12 passed in 0.77s

$ C:\Keil_v5\ARM\ARMCC\bin\armcc.exe --c99 -c --cpu Cortex-M4.fp -g -O0 --apcs=interwork --split_sections -I.\USER -I.\stm32_lib -I.\TASK -I.\Global_file -I.\BSP -I.\API -I.\firmware -I.\NN -I.\FreeRTOS\include -I.\FreeRTOS\portable\RVDS\ARM_CM4F -D__UVISION_VERSION="525" -DSTM32F40_41xxx -DUSE_STDPERIPH_DRIVER TASK/StabilizerTask.c -o $TEMP\stabilizertask_chk.o
armcc StabilizerTask.c exit=0

$ C:\Keil_v5\ARM\ARMCC\bin\armcc.exe --c99 -c --cpu Cortex-M4.fp -g -O0 --apcs=interwork --split_sections -I.\USER -I.\stm32_lib -I.\TASK -I.\Global_file -I.\BSP -I.\API -I.\firmware -I.\NN -I.\FreeRTOS\include -I.\FreeRTOS\portable\RVDS\ARM_CM4F -D__UVISION_VERSION="525" -DSTM32F40_41xxx -DUSE_STDPERIPH_DRIVER TASK/AutoflyTask.c -o $TEMP\autoflytask_chk.o
armcc AutoflyTask.c exit=0

$ bash tools/check.sh --changed --quiet
check_routes: no row for 1 file(s) (first: .cursorindexingignore); doc steps only
== routed by changed files: host-tests mrac-equiv all-loop-equiv c-pytest clang-tidy float row-meta fw-lint doc-paths doc-budget
== host-tests
-- host-tests OK (6 s)
== mrac-equiv
-- mrac-equiv OK (65 s)
== all-loop-equiv
-- all-loop-equiv OK (95 s)
== c-pytest
-- c-pytest OK (42 s)
== clang-tidy
-- clang-tidy OK (4 s)
== float
-- float OK (3 s)
== row-meta
-- row-meta OK (1 s)
== fw-lint
-- fw-lint OK (1 s)
== doc-paths
-- doc-paths OK (1 s)
== doc-budget
-- doc-budget OK (0 s)
CHECK SUBSET PASS: host-tests mrac-equiv all-loop-equiv c-pytest clang-tidy float row-meta fw-lint doc-paths doc-budget (not the full gate)
```

---

## 4. Bench & Flight Procedure Summary (`docs/lab/run-sheet-z-vz-cf.md`)

**200 Hz (5 ms tick) Telemetry Variables Required for All Captures (`A1`–`A3`, `F1`–`F2`)**:
- **ToF & legacy FD path**: `ano_of.alt_update_cnt`, `of_alt_cm`, `of2_raw_h`, `of2_h`, `of2_h_v`, `of2_h_f2_v`
- **Mode selector**: `g_alt_vz_mode` (`0` = `ALT_VZ_MODE_FD`, `1` = `ALT_VZ_MODE_CF`)
- **Complementary filter (`alt_cf`)**: `alt_cf.h`, `alt_cf.vz`, `alt_cf.b_az`, `alt_cf.e`, `alt_cf.a_up`, `alt_cf.n_corr`
- **Z PID cascade**: `Ctrler.Z_posPID.Des`, `Ctrler.Z_posPID.FB`, `Ctrler.Z_ratePID.Des`, `Ctrler.Z_ratePID.FB`, `Ctrler.Z_ratePID.U`

| Test ID | Drone State | Operator Role | Agent / Supervisor Role | Pass Line (`PROPOSED`) |
|---|---|---|---|---|
| **A1** (ToF frame rate vs. value-change rate) | Disarmed, props OFF, on bench | Holds drone still 30 s, then moves smoothly up/down by hand (~0.2–0.8 m) for 30 s | Logs 200 Hz telemetry; computes `Delta(ano_of.alt_update_cnt)/s`, value-change rate, `Delta(alt_cf.n_corr)/s`, and still `alt_cf.a_up` std | `ano_of.alt_update_cnt` increments at **`>= 20 Hz`**; `alt_cf.n_corr` increments on every accepted frame including repeat-value ticks |
| **A2** (`a_up` sign & stationary bias) | Disarmed, props OFF, level on bench | Leaves drone still 30 s, then lifts briskly upward by hand (`~0.5 m`) | Logs 200 Hz telemetry; checks 30 s still mean of `alt_cf.a_up` and initial sign of `alt_cf.a_up` & `alt_cf.vz` on lift | 30 s still mean `alt_cf.a_up` in **`[-0.1, +0.1] m/s^2`**; upward lift drives `alt_cf.a_up` **positive first** and `alt_cf.vz` **positive** (else flip `ALT_CF_ACC_SIGN`) |
| **A3** (CF `vz` vs. FD `vz` on hand motion) | Disarmed, props OFF, on bench | Moves drone smoothly up/down by hand (~0.2–0.8 m) for 30 s | Writes `g_alt_vz_mode = 1` while disarmed; logs `alt_cf.vz` and `of2_h_f2_v` at 200 Hz; compares per-tick `|dvz|` | Repeat-tick share of `|dvz| > 0.05 m/s` is **`< 1%`** in CF; `alt_cf.vz` `|dvz|` p90 is **`>= 5x` lower** than FD `of2_h_f2_v` |
| **F1** (60 s Hover A/B: `z-vz-hover-fd` vs. `z-vz-hover-cf`) | Flight pad, resting pack `>= 15.8 V` | Opens battery session (`allow_agent_arm` on, says `"go"`, RC TX on with `ch10` hard-kill); approves disarmed mode switch in chat | Flies 60 s hover in Mode 0 (`FD`), lands/disarms, switches `g_alt_vz_mode = 1` (`CF`) while disarmed after operator OK, flies 60 s hover at identical PID gains | Per-tick `|dU|` p90 (`U = Ctrler.Z_ratePID.U`) in CF is **`>= 3x` lower** than FD; Z position RMS error in CF is **`<= 1.0x` FD** |
| **F2** (Climb-rate steps in CF: `z-vz-steps` / `lab_z_vz_steps.fly`) | Flight pad, resting pack `>= 15.8 V`, after F1 pass | Approves `g_alt_vz_mode = 1` while disarmed; says `"go"` with RC TX `ch10` active | Runs `.\fly lab_z_vz_steps --dry-run` then `--once` (`1.0 -> 1.4 -> 0.6 m` at `0.2 m/s` and `0.4 m/s`) with 200 Hz logging | **No new oscillation** in hover holds or step transitions; step overshoot, settling time, and `vz` RMS error **not worse** than FD baseline |

---

## 5. Assumptions, Non-Executed Hardware Items, and Open Questions

### 5.1 Non-Executed Hardware Items (Per Hard Rules)
- **No Keil full project build (`UV4`) or flash (`rebuild_and_flash`) was executed**; `OBJ/` was not touched.
- **No connection to port `8081`, SWD probe (`livewatch`), or WiFi AP (`192.168.4.1`) was made.**
- **No bench tests (`A1`–`A3`) or flight tests (`F1`–`F2`) were executed**; they are fully specified in `docs/lab/run-sheet-z-vz-cf.md` for supervisor/operator execution after branch review and merge.

### 5.2 Key Engineering Assumptions (To Be Verified in Bench `A1`–`A3`)
1. **World-up acceleration sign (`ALT_CF_ACC_SIGN = +1.0f`, `PROPOSED`)**: Derived from `imu_update.c` where stationary `Acc_Z_Real ≈ +998 mg` and `Gravity_Body_Z ≈ +1.0` so `Lin_Acc_Z_body = Acc_Z_Real - 998.0f * Gravity_Body_Z` is positive when accelerating upward. Must be verified on hardware in **Bench A2** before any flight.
2. **ToF frame arrival rate (`20 Hz` nominal, `Ts = 0.05 s`)**: Inferred from the `10`-tick (`50 ms`) mode run-length between height value changes during motion in flight `20261024-213634` (`MEASURED`), since `ano_of.alt_update_cnt` was not in the default subscribe set. Must be directly measured in **Bench A1** by logging `ano_of.alt_update_cnt` at 200 Hz.
3. **In-flight vertical accelerometer noise (`sigma ≈ 1.0 m/s^2`) and stationary bias (`~0.1 m/s^2`)**: Taken from 2026-10-26 hover logs where `Acc_Z_Real` (`>5 Hz`) std was `0.73–0.94 m/s^2` (`MEASURED`) and stationary offset was `+0.08 m/s^2`. To be checked on `alt_cf.a_up` in **Bench A1/A2** and **Flight F1**.
4. **Linearized vertical control sensitivity (`b_u = 0.013868 (m/s^2)/CCR_unit`)**: Derived from `sim/bench/plant.py` (`4 * DTDX_H / MASS`) and cross-checked against `HOVER_THR_FREE` (`3050 CCR`) and flight `20261024-213634` regression (`0.01487 (m/s^2)/CCR`).

### 5.3 Open Questions for the Operator Before Reflash & Bench `A1`–`A3`
1. **Bias integrator gain `k3` for initial bench/hover bring-up**: Both `k3 = 0.0f` (2-state CF) and `k3 = 0.10f` (3-state CF with accelerometer bias estimation, currently set in `ALT_CF_ROW` as `PROPOSED`) pass the `abs(dU)` kick reduction gate (`>= 5x`), and `k3 = 0.10f` eliminates the steady-state velocity offset under a `0.1 m/s^2` accelerometer bias so 2% step settling matches FD (`-0.7%`). Do you want to keep `k3 = 0.10f` (`PROPOSED`) as the compiled row for `A1`–`A3` and `F1`, or start `F1` with `k3 = 0.0f` if `A2` measures stationary `|a_up| <= 0.05 m/s^2`?
2. **Scheduling Bench `A1`–`A3`**: Once `z-vz-cf` is merged to `main` and flashed by the supervisor while disarmed (props off), are you ready to run the 60 s bench capture (`A1`–`A3`: 30 s stationary + 30 s hand lift/motion) to verify `ano_of.alt_update_cnt` rate and `ALT_CF_ACC_SIGN`?
