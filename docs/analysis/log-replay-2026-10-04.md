# Log replay of the new controllers and tuners (WP-34, 2026-10-04)

Every number here was measured this run on the main checkout's logs (gitignored, so each script takes
`--root <main checkout>`). Corpus (`ground_station/analysis/log_corpus.py`): 374 logs = 306 dashboard sessions
(`logs/sessions/*/telemetry.csv`) + 68 VOFA recordings (`logs/vofa/*.meta.json`); `logs/campaigns/` holds only one
launch yaml and `docs/flights/` only the ledger. 34 logs have an airborne span >= 3 s (armed and `flight_phase`
FLYING/LANDING); most sessions are bench or ground runs. Thresholds marked PROPOSED were not measured.

## A. MRAC variants, open-loop replay (`mrac_log_replay.py` + `mrac_log_replay_host.c`)
The firmware law itself runs: `API/mrac*.c` are built with gcc around a driver that feeds each logged tick
(rate Des/FB/U, Z_rate, imu_data.pit/rol, armed, flight_phase; 50-100 Hz logs resampled to MRAC_DT 5 ms) to
`MRAC_Control`. **The plant does not respond**: x, r, u_nom are as flown, so this shows what each law would command
and how its weights move, not the closed loop it would produce. Variants are written through
`MRAC_VariantParamSet` (CMD 0x1D) from the WP-27 campaign presets; PR (kappa_pr 1.0) and 3L (lam_ang 2.2) have no
flight value and are PROPOSED (`*`). All runs as if injected, simplex observe-only (mode 2).
`u_def` (V2 drive) is rebuilt from the flown mixer: `controller.c` deficit on Throttle_out and the PID U.

Fidelity: the OFF replay (injection flag as flown) against the logged `mrac_state.*.u_ad` correlates at median
0.89 / 0.84 / 0.94 / 0.96 (pitch / roll / yaw / z, 26 / 26 / 20 / 26 logs); the 10th percentile is 0.35 / -0.04 /
0.39 / 0.03, the logs flown on older MRAC configs. 38 logs, 1559 s airborne; 37 have learn-gated ticks.

| variant | med rms u_ad / u_nom p / r / y | logs with abort rule p / r / y (of 37) | logs with Theta growth > 5 s p / r / y (max s) | V3 grid p / r: active / mean sum phi |
|---|---|---|---|---|
| OFF (today) | 1.00 / 1.21 / 0.97 | 28 / 30 / 28 | 0 / 2 / 3 (9.4) | - |
| V1 g1 (pid_ref) | 0.96 / 1.01 / 1.01 | 24 / 26 / 28 | 0 / 2 / 4 (9.4) | - |
| V1 g0.25 (v1_refmodel) | 0.38 / 0.51 / 0.90 | 14 / 18 / 27 | 11 / 2 / 13 (16.5) | - |
| V2 (v2_sataware) | identical to V1 g1 | 24 / 26 / 28 | 0 / 2 / 4 | - |
| PR* | 0.96 / 1.02 / 1.01 | 24 / 26 / 28 | 0 / 2 / 4 (9.4) | - |
| 3L* | 1.52 / 1.31 / 1.01 | 31 / 32 / 28 | 3 / 12 / 4 (43.1) | - |
| V3 (v3_rbf12, as built) | 0.28 / 0.48 / 1.01 | 14 / 18 / 28 | 11 / 4 / 4 (29.2) | 6 / 6, 0.66 / 0.38 |
| V3 rad* (angles in rad) | 0.24 / 0.33 / 1.01 | 12 / 17 / 28 | 11 / 12 / 4 (29.2) | 2 / 3, 3.07 / 3.06 |
z is the same in every row (no z knobs in the presets): u_ad / u_nom 0.41-0.46, abort rule in 22-23 logs.
Abort rule = |u_ad| > 0.5 |u_nom| for >= 1 s (docs/workflow-b/mrac-variants.md:51); growth counted after a
PROPOSED 10 s warm-up of learn-gated time.

Findings
1. **Unit bug, MRAC side (not fixed: firmware edits here are limited to thrust estimation).** `imu_data.pit/rol`
   are degrees (`API/imu_update.c:196-197`) but `API/mrac.c` treats them as radians: the V3 grid divides by
   `rbf_ang_scale` 0.26 "rad" (`mrac.c:318`) and the simplex compares with `pitch_max/roll_max` 3.14 "rad"
   (`mrac.c:749-750`, `mrac.h:389-390`). Replayed with simplex in observe mode, every degree-fed variant counts
   510 would-be trips (reasons 1/2, tilt > 3.14 deg) in 1559 s; fed radians, 0. Enforce mode (1) at default
   limits would therefore freeze MRAC at any tilt over 3.14 deg. Fix: multiply by 0.0174533f where mrac.c reads
   `imu_data.pit/rol` (lines 318, 749, 750, the only reads); the host test driver already feeds radians.
2. **V3 grid is mis-scaled either way.** Fed degrees, the angle Gaussians fire only while |tilt| < ~0.5 deg (mean
   sum phi 0.38-0.66, spiky). Fed radians, hover rates (|x| << 3 rad/s) and tilts (<< 0.26 rad) keep both inputs
   near the grid centre, so sum phi sits at 3.07 (its maximum) and the 12 features are a near-constant, collinear
   with the bias weight. PROPOSED: scales near the logged hover spread (rate ~0.3 rad/s, angle ~0.05 rad) before
   battery 4.
3. **V2 never acts on these flights**: the rebuilt mixer deficit is 0 on every tick of all 32 logs that log
   Throttle_out, so the time the V2 term would act is 0 s and V2 equals V1 numerically. Only a saturating flight
   (battery 3 "H + 100 g offset") can test it.
4. **The hand abort rule fires on most hovers for every variant**, including today's law: hover u_nom is tiny
   (median rms 0.079 roll), so |u_ad| > 0.5 |u_nom| holds 73-85 % of gated time. PROPOSED: add a floor, e.g.
   only when |u_nom| > 0.1 u_max, or compare |u_ad| with u_max instead.
5. **Theta-growth rule mis-fires on slow learners**: gamma x0.25 (V1 g0.25, V3) keeps converging past 10 s, so
   11-13 logs show > 5 s of rising |Theta| without any drift. PROPOSED: judge growth on a slope (|Theta| rising
   faster than x %/s) or after |Theta| settles.
6. **3L at the sim value 2.2 drifts**: authority +58 % pitch / +30 % roll over V1 and roll |Theta| rising for up
   to 43 s (12 logs > 5 s). Do not fly lam_ang 2.2 without a closed-loop sim check. PR at kappa_pr 1.0 moves
   no summary metric by more than 0.01 over V1 (Theta - Whatf not logged here, so the reason is not measured).
7. V1 g1 tracks OFF on pitch and yaw (within 4 %) and lowers roll authority (1.21 -> 1.01). Max |Theta| over all
   variants and axes p/r/y is 0.238; u_ad stayed finite in every run. 7 of the logs flew with injection on, so
   their x already contains the flown u_ad's effect; the other variants are still open loop on them.

## B. Autotune (`autotune_sweep.py`)
**No log has a SysID run**: 0 of 374 carry the 0x03 ID frame (`id.sample_counter`) or `id.sysid_state`.
`python -m ground_station.autotune.cli` (propose, rate loop) ran on every log with rate Des/FB samples (98 logs x
3 axes = 294 runs): 243 refused "no multisine found in Ctrler.*.Des", 48 refused "log shorter than half the
excitation", **3 crashed** (`ValueError: cannot convert float NaN to integer` in `frf.find_start` on a log with
too few Des samples; `autotune/` is outside WP-34, so it is reported, not fixed). No proposal from any log.

Natural excitation: the same `frf.plant_frf` / `fit_plant` / `design_rate` chain on the airborne spans, with
the rate setpoint r as its own instrument, gains = today's `API/pid.c` (no log's commit resolves to a pid.c with
the PID_ROW table, which dates from 2026-09-29). 30 airborne logs x 3 axes:
| axis | median coherence(r, x) 0.5-15 Hz | coverage gate (>= 5/10 sub-bands) | fit residual <= 0.35 | "proposed" | fitted delay median |
|---|---|---|---|---|---|
| roll | 0.24 (0.14-1.00) | 26 / 30 | 7 / 27 | 6 | 62 ms |
| pitch | 0.20 (0.12-1.00) | 21 / 30 | 6 / 26 | 6 | 59 ms |
| yaw | 0.35 (0.05-1.00) | 28 / 30 | 5 / 28 | 5 | 76 ms |
Coherence is **not enough**: the median bin sits far below COH_MIN 0.6; the coverage gate passes only because a
few bins per sub-band clear it. The fits that pass are biased: delays of 59-62 ms vs the SysID 12-15 ms
(`research/sim/constants.py`), because r is not exogenous in hover (the angle loop closes through x). The 17
"proposals" mostly sit on the +-30 % trust bound (roll/pitch Kp 5 -> 3.5, Kd 10 -> 13; yaw Kp 8 -> 10.4):
**do not apply them**. The tuner needs the excite step (WP-25 ID_EXCITE: multisine 0.5-15 Hz, 60 deg/s, 30 s).

## C. Live-tune cost noise floor (`livetune_floor.py`)
J = J_track + 2 J_sat + J_osc (`livetune/cost.py`, weights and A = 30 deg/s from `LiveTuneConfig`) on 4 s
windows (`excite_s`) of hover with the flown gains; no logged flight has a live-tune excitation, so every window
is hover only. 29 logs; 19 resolve to rate gains Kp 5 / Ki 0.01 / Kd 10 on roll and pitch via the last pid.c
commit before the recording ("inferred": the flashed image may lag the tree), 10 unknown.
| axis | windows (logs) | median J | J_track / J_sat / J_osc median | within-flight CV of J (median, max) | between 17 equal-gain flights CV | windows for a 5 % step at 2 sigma |
|---|---|---|---|---|---|---|
| roll | 332 (24) | 1.10 | 0.45 / 0 / 0.57 | 0.32, 0.63 | 0.17 | ~160 |
| pitch | 332 (24) | 0.53 | 0.25 / 0 / 0.28 | 0.24, 0.51 | 0.24 | ~100 |
Findings: the noise floor is **5-6x the tuner's min_gain 0.05** (CV 0.24-0.32 per window), so one window per
candidate cannot rank gains that differ by 5-10 %. J_osc carries most of it: hover roll error holds a >= 10 Hz
line of ~17 deg/s (J_osc 0.57 x A) with within-flight CV 0.47 (pitch 0.38), against 0.19 / 0.17 for J_track
alone (between-flight J_track CV 0.125 roll). With excitation J_track grows and the relative noise should drop,
but by how much is not measured. PROPOSED before flying the tuner: repeat each candidate >= 3 windows or score on
the median of 3, and either lower w_osc or raise f_c above the vibration line, then re-measure this floor on the
first live-tune flight.

## Reproduce (scripts and tests in `ground_station/analysis/`)
| part | command (`--root <main checkout>`, `--json out.json` optional) | test |
|---|---|---|
| corpus | `python -m ground_station.analysis.log_corpus` | `tests/test_log_corpus.py` |
| A | `python -m ground_station.analysis.mrac_log_replay [--per-log]` | `tests/test_mrac_log_replay.py` (gcc) |
| B | `python -m ground_station.analysis.autotune_sweep` | `tests/test_autotune_sweep.py` |
| C | `python -m ground_station.analysis.livetune_floor` | `tests/test_livetune_floor.py` |
| thrust | `python -m ground_station.analysis.thrust_replay` (see `thrust-estimation-audit.md`) | `tests/test_thrust_replay.py` |
