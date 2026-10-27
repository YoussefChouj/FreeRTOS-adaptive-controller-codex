# z-cf-rootcause: Z-axis oscillation after the altitude CF (2026-10-27)

Owner: CEO (spec + review). Builder: one Antigravity orchestrator run by the operator.
Branch: `z-cf-rootcause` from `main`. Never push to main, never flash, never touch 8081 or the SWD probe.
Numbers marked PROPOSED are not measured; never present them as measured.

## Symptom
- FD climb rate (`(h - h_last)/5 ms`, LPF 0.9/0.1) gave spikes in the Z outputs.
- CF (`Alt_UpdateFB`, `TASK/StabilizerTask.c` ~L1310-1450, default since 2026-10-26) removed the spikes,
  but the drone now wobbles in Z: setpoint 0.8 m, height swings about 0.6/0.7 to 0.9 m (operator report).
- Logged hover Z RMS 6.0 / 6.1 / 5.0 / 4.7 cm (`logs/campaigns/z-cf-hover_20261026-231115`, `-231338`,
  `-231601`, `-231738`; legacy / scaled / scaled / legacy). Those streams are 25 Hz only.
- Measured 2026-10-26 (log `logs/sessions/20261026-220648-z-cf-hover-001_f01_z_cf_hover`, 7.5-36.5 s, CEO
  analysis, 25 Hz telemetry): CF vz / d(range)/dt gain 0.54, phase -35..-40 deg (0.14-0.17 s) at 0.2-1.2 Hz;
  CF h / range gain ~1.0, lag 7-13 deg; a_up / d2(range)/dt2 gain 0.96 (accel scale and sign look OK).
  `alt_update_cnt` steps 2-4 per 40 ms frame (~70 Hz) while `of_alt_cm` changes ~9.5 times/s (1 cm steps),
  so the CF probably corrects ~70 Hz, mostly on repeated values, with gains designed for 20 Hz (H6).
  The offline replay did not match the flown filter (corr 0.75), so none of this is attributed yet.
- Existing runtime switch `g_alt_cf_rate_mode` (0 legacy, 1 gains scaled by dt_c/0.05, latched at arm).

## Signal chain as coded (read before changing anything)
200 Hz tick (`STAB_DT_S` 5 ms). ToF sample from `ano_of.of_alt_cm`, new-sample flag = `alt_update_cnt` change.
1. median-of-3 over the last 3 **ticks** (not samples) -> band gate 5..500 cm -> tilt cos -> jump gate.
2. CF predict every tick: `h += vz dt + a dt^2/2; vz += a dt`, `a_up` = gravity-projected `Lin_Acc_*_body` minus `b_az`.
3. CF correct only when `sample_accepted` (count changed AND jump ok): `e = of2_h - h; h += k1 e; vz += k2 e; b_az -= k3 e`.
   Gains per sample: k1 0.21, k2 0.45, k3 0.1, design Ts 0.05 s.
   Continuous equivalent s^3 + 4.2 s^2 + 9 s + 2: complex pair wn ~2.8 rad/s, zeta ~0.7, plus a slow bias
   pole ~ -0.25 rad/s (tau ~4 s). (CEO hand calculation; the builder re-derives it in A4.)
4. CF mode: `Z_posPID.FB = alt_cf.h`, `Z_ratePID.FB = alt_cf.vz`.
5. `API/pid.c` rows: Z_posPID Kp 0.7, Ki 0.001, Kd 0.1 (D on height); Z_ratePID Kp 400, Ki 0.435, Kd 0.

## Root-cause hypotheses, ranked (each needs a test that can prove it false)
H1 **Stale innovation (code bug, high confidence from reading).** The median runs per tick on a held value.
   On the tick a new sample arrives, history = [new, old, old] -> median = old, yet `cnt_changed` = 1, so the
   CF correction uses the PREVIOUS sample. The next tick the median becomes `new` but `cnt_changed` = 0, so it
   is never fused. Effective measurement delay = one ToF period (~50 ms at 20 Hz, PROPOSED) + sensor latency.
   Side bug: a single bad ToF sample is held for many ticks, so the per-tick median passes it through
   after 2 ticks; only the jump gate protects.
H2 **No delayed-measurement alignment.** The ToF module's own latency is not compensated (legacy SINS.c used
   `Pos_History[6]` = 60 ms). A delayed y compared with the current h makes the low-frequency h and vz lag.
   Standard practice: PX4 EKF2 (`EKF2_RNG_DELAY`, delayed fusion horizon + output predictor), ArduPilot EKF3
   (range-finder delay), legacy SINS.c history buffer.
H3 **Impulsive corrections.** h and vz jump by k1 e and k2 e once per sample (20 Hz steps). Z_posPID Kd 0.1 on a
   stepping h re-creates a smaller version of the original spikes.
H4 **Accel path errors.** Group delay of the filter feeding `Lin_Acc_*_body`, scale error, and
   thrust-correlated bias (vibration rectification). The bias state (tau ~4 s) then chases throttle and
   interacts with the Z_posPID integrator: gives a SLOW wobble (seconds).
H5 **Loop gains tuned for the FD path.** Z gains were tuned with the FD+LPF velocity (phase lag ~ LPF tau
   47.5 ms). The CF velocity has a different gain/phase profile, so the margins moved.
H6 **Correction rate does not match the gain design.** `alt_update_cnt` appears to step ~70 Hz while the range
   value changes ~10-25 Hz. Per-sample gains designed for 20 Hz applied at ~70 Hz on repeated values make the
   filter about 3x stiffer to the ToF (more ToF noise and quantization in vz, different phase).
   First find in code who increments `alt_update_cnt`, in which task, and at what rate (the UART frame rate
   is not the sensor rate). A2 must define "new sample" from that finding.

Decision rule: the oscillation PERIOD decides the branch. ~0.5-2 Hz -> H1/H2/H5 (delay / phase margin).
Several seconds -> H4 (bias + integrator). Measure it first (B1).

## Work items and acceptance tests

### A. Host-only (no hardware)
A1 Prove H1. In `API/tests/test_alt_cf.c` add a test: ToF at 20 Hz (count increments every 10 ticks) on a
   ramp and a sine; record `of2_h` used at each correction. On current code the test must show the stale
   sample (assert expected failure, or a report-only test committed red-then-green with the fix).
   Accept: test shows correction uses sample k-1 before the fix, sample k after.
A2 Fix H1. Run median-of-3 per NEW SAMPLE (push to history only when `cnt_changed`), so the median rejects
   one bad sample and the newest accepted sample is fused on its arrival tick. Keep band/jump/resync gates.
   FD branch must still work (mode stays selectable, disarmed only).
   Accept: A1 green; existing alt_cf, legacy-parity and all-loop-equivalence tests green; a single
   in-band outlier sample is rejected (new test).
A3 Fix H2. Ring buffer of the CF state at 200 Hz (>= 30 entries, 150 ms). Innovation
   `e = y - h_hist[d]`, `d` = `g_alt_tof_delay_ticks` (runtime variable, default 0 = today's behaviour
   after A2; operator sets the measured value). Apply the correction to the current state AND the buffered
   states (or keep an offset) so the history stays consistent. Accept: host test with a simulated 40 ms
   (PROPOSED test value) latency: with d matching, steady sine tracking phase error of h and vz < 5 deg at
   1 Hz; with d = 0 the lag is reported.
A4 Fix H3 (behind a runtime flag, default off): spread each correction over the ticks until the next
   sample (k e / N per tick) instead of one jump. Accept: max |h[n] - h[n-1]| and |vz[n] - vz[n-1]| at
   hover drop vs step correction in a host test; equal steady state.
A5 Closed-loop sim (`ground_station/` or `tools/`, Python, reuse `.agent-ops/briefs/cf-rate-scaled-gains.cfvz_ref.py`
   if it matches the C code). Plant: mass from the hover throttle, motor lag (PROPOSED range 20-60 ms,
   sweep it), ToF 20 Hz + latency sweep {0, 25, 50, 75, 100 ms}, accel noise, constant bias, thrust-correlated
   bias. Controller = exact pid.c Z rows at 200 Hz. Estimators: FD+LPF, current CF, CF+A2, CF+A2+A3,
   CF+A2+A3+A4, steady-state 3-state Kalman (h, vz, b_az) with delayed fusion. For each: linearised loop
   phase/gain margin, step 0.8 -> 1.0 m overshoot and settle, hover RMS, oscillation period.
   Accept: a table reproducing (or failing to reproduce) a +-0.1..0.15 m wobble, naming which factor does it.
A6 Filter choice. A linear 3-state KF with correct timing equals a CF with optimal gains; an EKF adds nothing
   for Z given attitude. Recommend KF only if A5 shows a margin or RMS gain over the fixed CF. Accept: one
   paragraph with the A5 numbers.

### B. Existing data (no flights)
B1 From the four 2026-10-26 recordings: oscillation period and amplitude (spectrum of ToF height and
   alt_cf.h), cross-correlation lag ToF vs alt_cf.h and d(ToF)/dt vs alt_cf.vz, b_az vs throttle
   correlation. 25 Hz limits: report what it can and cannot show. Accept: `docs/lab/z-cf-rootcause-B1.md`
   with numbers and plots; states which hypothesis branch the period points to.

### C. Lab plan update (doc only, nothing flies without the operator)
C1 Disarmed estimator test: add a runtime flag (default off, never feeds control, cleared on arm) that runs
   CF predict/correct while disarmed. Operator moves the drone by hand, motors off, ~1 Hz up/down
   20-30 cm. This isolates estimator lag from the control loop and from vibration.
C2 Capture list (200 Hz, direct slot 0, existing single-slot path): of_alt_cm, alt_update_cnt, of2_h, alt_cf.h,
   alt_cf.vz, alt_cf.e, alt_cf.b_az, alt_cf.a_up, Lin_Acc_*_body, Z_posPID Des/FB/Out, Z_ratePID Des/FB/Out,
   throttle, arm state. Check the wire budget with the existing calculator; drop camera vars if needed.
C3 Flight order (update `docs/lab/cf-resume-2026-10-27.md` and the day deck card, keep every planned
   flight): ground cadence check -> C1 hand test (old fw) -> hover baseline (old fw) -> flash reviewed fix
   (CEO only) -> C1 hand test (new fw, delay d from C1) -> hover A/B (fixed CF vs old CF via the runtime
   flags) -> Z step 0.8 -> 1.0 m (`lab_z_vz_steps`). Delay d is MEASURED from the C1 cross-correlation.
C4 Analysis script `python -m ground_station.lab.cf_vz_check` extended: lag (ToF vs h, dToF/dt vs vz),
   oscillation period, b_az vs throttle, correction step sizes. Accept: runs on a synthetic log in a test.

## Rules for the builder
- Firmware C: Keil-compatible C99, table pattern for tunables (`docs/firmware-table-pattern.md`), no new
  globals without a header declaration, runtime flags only change while disarmed.
- Tests: scoped (changed files + their harnesses); host C tests via the existing `API/tests` build.
- Stage by path, commit per item to `z-cf-rootcause`, write progress to `.agent-ops/briefs/z-cf-rootcause.progress.md`
  after every item so a stopped run resumes.
- Hand back: branch name, per-item status, test output tails, the A5 table, the B1 doc, open questions.
