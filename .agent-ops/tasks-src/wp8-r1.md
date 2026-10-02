<guardrails>
1. Edit only the files in the ALLOW-LIST. Every other path is read-only. Never touch `OBJ/`, never run Keil.
2. Write complete implementations. No "...", TODO, `pass` stubs or "rest of code" comments.
3. After each Python edit run `python -m py_compile <file>`; after each C edit run the gcc command below. Fix before going on.
4. Run every command in the foreground and paste its verbatim output into the digest.
   Report a test as passing only when you ran it and saw "passed" in the output.
5. Catch only specific exceptions. Bare `except:` and `except Exception: pass` are forbidden.
6. Python 3.10+, standard library + numpy (+ pandas only through the existing loader). C: C99, no malloc, Keil-ARMCC compatible.
7. If a command or edit fails twice the same way, change approach; never repeat an identical call.
8. Write the digest `.agent-ops/out/wp8-r1.md` BEFORE printing DONE.
9. Output: no preamble, no summary prose.
</guardrails>

<context>
Goal: the mode-2 optical-flow (OF) Kalman filter `API/ekf_of.c` gets the body accelerometer as a control input,
an accelerometer-bias state and a zero-velocity update (ZUPT) on the ground, so slow drift is tracked as velocity
instead of leaking into the OF bias. Plus a Python twin, a C-vs-Python golden test and a log-replay tuning CLI.

Measured facts (base main @ cc6ebd6):
- `API/ekf_of.c` (224 lines) / `API/ekf_of.h`: state `[px, vx, bof_x, py, vy, bof_y]`, flat 6x6 `P[36]`,
  constant-velocity predict without input, `Q_pos 1e-6, Q_vel 2e-4, Q_bias 5e-5` per second, `R_of 6.16e-4`,
  scalar Joseph update `ekf_of_update_one` with H selecting `vel + bias`. `ekf_of.h` includes "global_declare.h"
  only for `uint8_t`; only `API/ekf_of.c` and `TASK/StabilizerTask.c` include `ekf_of.h`. No code outside ekf_of.c
  reads `s_ekf_of.Q_*`, `.R_*` or `.P`; StabilizerTask reads `x[]`, `innov_x`, `innov_y`.
- `TASK/StabilizerTask.c`: mode comment block lines ~85-104; `#define EKF_OF_INNOV_THRESH 2.0f` line 125;
  `#define OF_MIN_QUALITY 50U` line 159; `u8 of_ok = (ano_of.of_quality >= OF_MIN_QUALITY);` line 386.
  Mode-2 block lines 429-458: `EkfOf_Init` once, `ofx = ((float)ano_of.of2_dx_fix - s_of_bias_x) * 0.01f` (m/s),
  `EkfOf_Predict(&s_ekf_of, 0.005f); if (of_ok) EkfOf_Update(&s_ekf_of, ofx, ofy);` then the innovation health
  check -> fallback to mode 0 (KEEP it unchanged). Lines 464-468 compute `pos_integrate` AFTER the EKF block:
  `of_quality ok && ((g_of_handheld_test && alt band) || (DroneStatus.ARM_Status == Armed &&
  (flight_phase == FLIGHT_PHASE_FLYING || flight_phase == FLIGHT_PHASE_LANDING)))`.
  `Of_RebaseKfBias` (~line 200) shifts `x[2], x[5]`; `EkfOf_ResetPos` (~line 226); lines ~1003/1024 zero `x[1], x[4]`.
  Keep all of these.
- `API/flight_fsm.h`: GROUND_IDLE=0, FLYING=1, LANDING=2, LANDED=3.
- OF freshness: `of_ok` does not check for a new frame; `API/Ano_OF.c:140` increments `ano_of.of_update_cnt` (u8)
  per received frame. So today the same OF sample is fed to `EkfOf_Update` on every 5 ms tick until the next frame.
- Accel: `Lin_Acc_X_body / Lin_Acc_Y_body` (`API/imu_update.c:202`) = gravity-removed body accel in mg, control rate,
  already used in StabilizerTask (~line 531). Axis map measured on 5 logs: OF x <-> +Lin_Acc_X_body (r +0.27..+0.46),
  OF y <-> -Lin_Acc_Y_body (r -0.25..-0.50); other pairs |r| <= 0.23. The accel is noisy (~12 cm/s rms per 0.5 s
  integrated vs ~5 cm/s of OF change). active6 hover mean Lin_Acc_X is -29 mg.
- Repo firmware convention: tunables as aligned `*_ROW` macro tables, spec `docs/firmware-table-pattern.md` (read it).
- Logs: the five flight logs are NOT in your checkout (untracked, they live only on the supervisor's laptop).
  You cannot run the replay on real data. Build and unit-test it on synthetic arrays; the supervisor runs it on
  the real logs. Loader: `ground_station.analysis.flightlab.loaders.vofa.load_vofa(meta_path)` -> `L.signals[name].t/.v`
  (read the loader for the meta JSON shape). Log files: `logs/vofa/f17_hover_{shadow3,shadow4,shadow5,active5,active6}_*.meta.json`.
  Signals: slot1 50 Hz `Acc_X_Real, Acc_Y_Real, flight_phase, ano_of.of_alt_cm`; slot2 50 Hz `Ctrler.rollPID.FB,
  Ctrler.pitchPID.FB` (deg); slot3 25 Hz `ano_of.of2_dx_fix, ano_of.of2_dy_fix, ano_of.of_quality, s_of_bias_x,
  s_of_bias_y, s_ekf_of.x[0]..x[5]`. active6 `slot0.csv` is header-only and `load_vofa` raises on it.
</context>

<allow-list>
API/ekf_of.c
API/ekf_of.h
TASK/StabilizerTask.c            (mode-2 block, ZUPT call, accel macros, mode-2 comment only)
ground_station/comm/boot_default_layout.py
ground_station/analysis/ekf_of_model.py          (new)
ground_station/analysis/ekf_of_replay.py         (new)
ground_station/analysis/tests/test_ekf_of_model.py   (new)
.agent-ops/out/wp8-r1.md         (digest)
</allow-list>

<spec>
A. Firmware model (per axis, axes independent). States `x[8] = [px, vx, bof_x, py, vy, bof_y, ba_x, ba_y]`
   (x[0..5] keep their meaning; ba in m/s^2). Covariance `float P[2][16]`: two 4x4 row-major blocks, per-axis
   order [p, v, bof, ba]; axis index table `static const uint8_t k_axis_idx[2][4] = {{0,1,2,6},{3,4,5,7}};`.
   - `ekf_of.h`: include `<stdint.h>` instead of "global_declare.h". Struct fields in this order:
     `float x[8]; float P[2][16]; float q_pos, q_acc, q_bof, q_ba; float R_of, R_zupt; float innov_x, innov_y; uint8_t inited;`
     Rewrite the model comment (states, predict, measurements, units).
   - API: `EkfOf_Init(e)`, `EkfOf_Predict(e, dt, ax, ay)` (ax, ay in m/s^2 in the OF frame), `EkfOf_Update(e, of_x, of_y)`,
     `EkfOf_UpdateZeroVel(e)`, `EkfOf_ResetPos(e)`. The filter reads no globals.
   - ONE static per-axis predict: `u = a - ba; p += v dt + 0.5 u dt^2; v += u dt;` bof, ba random walks;
     `F = [[1,dt,0,-dt^2/2],[0,1,0,-dt],[0,0,1,0],[0,0,0,1]]`, `P = F P F' + diag(q_pos, q_acc, q_bof, q_ba) * dt`.
   - ONE static per-axis scalar Joseph update `(e, axis, const float h[4], z, R, float *innov)`.
     OF: h = [0,1,1,0], z = of, stores the innovation in innov_x / innov_y with the SAME semantics as today.
     ZUPT: h = [0,1,0,0], z = 0, R = R_zupt, both axes, passes a local dummy (does NOT touch innov_x/innov_y).
   - Noise and initial-P defaults as one aligned `EKF_OF_NOISE_ROW`-style table per docs/firmware-table-pattern.md:
     q_pos 1e-6, q_acc 1e-2, q_bof 1e-6, q_ba 1e-5, R_of 6.16e-4, R_zupt 1e-4 (provisional, the supervisor re-tunes),
     P0 for p, v, bof as today's Init, P0_ba = 0.25. Top-of-file comment describes the model.
   - Must pass: `gcc -std=c99 -Wall -Wextra -Werror -c API/ekf_of.c -o /tmp/ekf_of.o` with NO -I flags.
B. `TASK/StabilizerTask.c` (only these changes):
   - Next to EKF_OF_INNOV_THRESH: `#define EKF_OF_ACC_SIGN_X (+1.0f)`, `#define EKF_OF_ACC_SIGN_Y (-1.0f)`,
     `#define EKF_OF_MG_TO_MPS2 (9.80665e-3f)`, comment citing the r values above;
     `#define EKF_OF_UPDATE_ON_NEW_FRAME 1U` with comment: 1 = feed each OF frame once (detected by
     `ano_of.of_update_cnt` changing), 0 = legacy every tick.
   - Mode-2 block: `on_ground = !g_of_handheld_test && !(DroneStatus.ARM_Status == Armed && (flight_phase ==
     FLIGHT_PHASE_FLYING || flight_phase == FLIGHT_PHASE_LANDING))`; then
     `EkfOf_Predict(&s_ekf_of, 0.005f, SIGN_X * Lin_Acc_X_body * MG_TO_MPS2, SIGN_Y * Lin_Acc_Y_body * MG_TO_MPS2)`;
     `if (on_ground) EkfOf_UpdateZeroVel(&s_ekf_of);` then the OF update when `of_ok` (and a new frame if the macro
     is 1; keep a `static u8` last count); then the unchanged health check.
   - Update the mode-2 comments (~85-104 and the "6-state" mentions incl. the include comment line 2) to the 8-state model.
C. `ground_station/comm/boot_default_layout.py`: add `"s_ekf_of.x[6]", "s_ekf_of.x[7]",` as a new line after line 334.
D. `ground_station/analysis/ekf_of_model.py`: Python twin, same equations and parameter names, vectorised over a
   leading batch axis B (x shape (B,8), P (B,2,4,4), each parameter scalar or shape (B,)), `dtype` argument
   (default float64; float32 allowed for the golden comparison). `DEFAULTS` dict = the C table values.
   Methods `predict(dt, ax, ay)`, `update_of(of_x, of_y, mask=None)` (returns innovations and S per axis for NIS),
   `update_zero_vel(mask=None)`, `reset_pos()`. Also `OldEkfOf6`: the old 6-state model (old defaults, same batch API,
   predict ignores accel, no ZUPT), for comparison in the replay.
E. `ground_station/analysis/ekf_of_replay.py`, run as `python -m ground_station.analysis.ekf_of_replay`:
   - Log dir: `--logs-dir`; default `<repo>/logs/vofa` if it exists, else `<parent of git common dir>/logs/vofa`
     (`git rev-parse --path-format=absolute --git-common-dir`); none found -> print a message, exit 2.
   - Loading: for every log, write a temp meta JSON with slots 1-3 only and load that (do not change the loader).
     `lin_x = Acc_X + 1000 sin(pit)`, `lin_y = Acc_Y - 1000 sin(rol) cos(pit)` (slot2 interpolated to slot1 times);
     `ax = +lin_x * 9.80665e-3`, `ay = -lin_y * 9.80665e-3`; `ofx = (of2_dx_fix - s_of_bias_x) * 0.01` (m/s).
   - Event loop per log (all grid combos at once via the batch axis): predict at each slot1 sample (dt from
     timestamps), ZUPT at that sample when flight_phase in {0,3}, OF update at each slot3 sample (after the last
     predict <= its time) when of_quality >= 50. Old model: same events, no ZUPT. FIXED: debiased OF integrated
     during phases 1/2. Model positions are integrated from deltas of x[0]/x[3] only during phases 1/2.
   - Metrics per log over phase 1 starting 2 s after takeoff: pos diff rms old-vs-FIXED and new-vs-FIXED (cm, x/y);
     NIS mean (OF innovations, both axes pooled); lag-1 autocorrelation of OF innovations (max over axes);
     bof drift = max |bof - bof_at_takeoff| (cm/s, max over axes); ba settle time = first time from log start after
     which ba stays within 0.05 m/s^2 of its median over the last half of flight (max over axes); final ba_x, ba_y (mg).
     Criteria: NIS 0.5-2, autocorr < 0.3, bof drift < 1 cm/s, settle <= 10 s. For shadow3 and active5 also print
     informational rms of replayed-old vs logged `s_ekf_of.x[0]/x[3]` (cm).
   - Grid: q_acc {1e-3,3e-3,1e-2,3e-2,1e-1} x q_bof {1e-7,1e-6,1e-5} x q_ba {1e-6,1e-5,1e-4} x R_of {3e-4,6.16e-4,1.2e-3,2.5e-3};
     score = number of passed (log, criterion) pairs over all logs; tie-break smallest mean |ln(NIS mean)|.
     Print the top 5 combos, then one table per log (old/new/FIXED pos diff, ba_x, ba_y, NIS, autocorr, bof drift,
     settle, PASS/FAIL per criterion) for the chosen combo, then exactly one line
     `CHOSEN q_acc=<v> q_bof=<v> q_ba=<v> R_of=<v>` and `DEFAULTS_MATCH yes|no` (chosen vs DEFAULTS).
     `--no-grid` runs DEFAULTS only. Keep the core as a function on plain arrays (`replay_arrays(...)`) so it is testable.
   - Find out, from the code only, the OF frame rate (Ano_OF.c / its UART config) and whether `of_update_cnt`
     appears in any log layout; report in the digest with file:line. Do not change those files.
</spec>

<tests>
`ground_station/analysis/tests/test_ekf_of_model.py`:
  1. Synthetic truth, dt 0.005, seeded RNG: 5 s ground (v=0, ZUPT every tick, OF bias bof=0.05 m/s, accel bias
     ba=0.2 m/s^2) then 30 s flight with true accel 0.5*sin(2*pi*0.7*t) m/s^2. Accel measurement = true + ba +
     N(0, 0.2) per tick; OF measurement every 8th tick = v + bof + N(0, sqrt(6.16e-4)). Filter params for this
     test: q_pos 1e-6, q_acc 2e-4 (= 0.2^2 * dt), q_bof 1e-6, q_ba 1e-5, R_of 6.16e-4, R_zupt 1e-4.
     Assert on the Python model: |bof - 0.05| < 0.005 at t=5 s; |ba - 0.2| < 0.02 at t=10 s (both axes);
     velocity rms error over the flight < 0.02 m/s.
  2. Golden: compile API/ekf_of.c with gcc into a shared library in tmp_path (`.dll` on Windows, else `.so`;
     `pytest.skip` if gcc is not on PATH), ctypes Structure mirroring EkfOf_t, run the same sequence as test 1
     (set the struct params after EkfOf_Init), compare x and innov with the Python model (dtype float32) each
     OF step: `np.testing.assert_allclose(rtol=1e-4, atol=1e-6)`.
  3. C defaults == Python DEFAULTS (struct fields after EkfOf_Init, also P0), skipped without gcc.
  4. `replay_arrays` on a 20 s synthetic log (arrays like the loader output, 50/25 Hz, phases 0 -> 1 -> 3) with a
     2-combo batch returns finite metrics for both combos.
  5. boot layout contains "s_ekf_of.x[6]" and "s_ekf_of.x[7]".
Run: `PYTHONPATH=. python -m pytest ground_station/analysis/tests/test_ekf_of_model.py ground_station/comm -q`
and `gcc -std=c99 -Wall -Wextra -Werror -c API/ekf_of.c -o /tmp/ekf_of.o`, and
`PYTHONPATH=. python -m ground_station.analysis.ekf_of_replay --logs-dir /nonexistent` (expect exit 2 message).
</tests>

<digest>
`.agent-ops/out/wp8-r1.md`, at most 30 lines: files changed, each command + its last 3 output lines, the OF frame
rate finding with file:line, deviations from this spec, open risks.
</digest>
