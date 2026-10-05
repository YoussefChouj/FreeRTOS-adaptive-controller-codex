# Adaptive architecture study (2026-10-05)

Status: PROPOSED. Numbers marked "measured" were computed in this study from logs or bench runs; settings are proposals
for post-demo branches, nothing here is flashed or merged into firmware behaviour.

| Item | Question | Status |
|---|---|---|
| A | Equal-budget tune of PR (Yucelen) and RBF layers against PID on payload / cog / zigzag | done, measured, below |
| B | Per-axis reference model from flight replay (f14, f16) | measured, below |
| C | Public data (NeuroBEM, Neural-Fly) | files and sizes listed below; download waits for the operator's yes |
| D | x/y adaptation: 6 decoupled layers plus coupling terms | measured, below: yaw layer harmful; x, y + z layers best so far (0.0631 vs PID 0.0775) |
| E | Fully coupled 6-axis MRAC: RAM budget, firmware copy on a branch | RAM budget measured, below; branch PROPOSED |
| F | Systematic cascaded-PID tuning | planned |
| G | MRAC derived for (augmenting) the PID loop | planned |
| H | Thesis three-layer design: physics features, frequency gating, task priors | planned; design constraint below |
| I | Position estimate from optical flow + IMU: roam-and-return bias, state of the art | measured, below |

Design constraint for H (operator, 2026-10-05): the three-layer design must need no parameter tuning, by its
architecture. Every gain has to come from a measured or derived quantity: physics features from the plant model and
sysID, frequency gates from measured loop bandwidths (item B), task priors from the campaign definition, and adaptation
rates normalised and bounded by physical limits. The equal-budget bench tune in A is a benchmark against PID, not a
deployment step. Item I applies the same rule to the estimator (scale and bias measured in flight, not tuned).

## A. Equal-budget bench tune (frozen bench; new rows `tune2.py` 128 evaluations each; test split; `heldout.py`)

Median test RMSE, with the difference to pid_tuned2 and its bootstrap 95 % CI:

| Controller | All rows | Diff vs PID [CI] | Held-out families (ground effect, motor loss, unseen combo) | Diff [CI] |
|---|---|---|---|---|
| mrac_sataware | 0.0726 | -0.0049 [-0.0107, -0.0013] | 0.0754 | -0.0059 [-0.0336, 0.0082] |
| mrac3l_unrouted | 0.0729 | -0.0046 [-0.0106, -0.0005] | 0.0709 | -0.0104 [-0.0563, 0.0056] |
| mrac_rbf12 | 0.0742 | -0.0032 [-0.0089, -0.0001] | 0.0740 | -0.0073 [-0.0384, 0.0085] |
| pid_tuned2 | 0.0775 | 0 | 0.0813 | 0 |
| mrac_rbf24 | 0.0784 | +0.0010 [-0.0053, 0.0046] | 0.0798 | -0.0015 [-0.0250, 0.0154] |
| mrac_pr (new) | 0.0903 | +0.0128 [0.0066, 0.0189] | 0.0940 | +0.0127 [-0.0198, 0.0265] |
| mrac_rbf48 (new) | 0.0923 | +0.0148 [0.0098, 0.0191] | 0.1002 | +0.0188 [0.0061, 0.0554] |
| mrac_physrbf (new) | 0.1231 | +0.0456 [0.0344, 0.0589] | 0.1426 | +0.0613 [0.0152, 0.1276] |

Findings:

1. **Small RBF banks beat large ones.** At the same budget, rmse rises from 12 to 24 to 48 centres:
   0.0742, 0.0784, 0.0923. Every extra centre adds a weight that has to adapt from the same error signal.
2. **Neither new layer beats PID.**
   - The Yucelen PR modification is +0.0128 worse than PID.
   - The physics-feature RBF is the worst MRAC: div 12.7 % and sat 5.8 %. Its features (thrust, v, v|v|) are large
     and unnormalised, so one learning rate cannot suit all of them.
3. **On held-out families every CI crosses 0 except two, both worse than PID: rbf48 and physrbf.** The bench cannot
   yet tell the best three apart from PID outside the training families.

Meaning for H (no tuning): the feature library must be small and normalised by physical scale, e.g. thrust / (m g)
and v / v_max. A larger library adds parameters that must adapt, which works against the no-tuning goal.

## B. Per-axis reference model (flight replay)

Tool: `ground_station/analysis/refmodel_replay.py`. It replays the logged rate command r and gyro x through each
candidate reference model and reports the MRAC error e = x - xm the firmware would have adapted on. "bp" is a
zero-phase 0.2-5 Hz band-pass: the band the adaptive layer is meant to act in (the 0.2 Hz floor removes the standing
offset, see below). Both flights flew reference type 0 (passthrough, xm = r) with adaptation in shadow mode.
The replay reproduces the logged error to 3e-8 rad/s on every axis.

Measured rms e in the band (rad/s):

| Axis | Model | f14 | f16 |
|---|---|---|---|
| roll | passthrough (flown) | 0.0424 | 0.0372 |
| roll | firmware 2nd order wn 44, z 0.8 | 0.0361 | 0.0336 |
| roll | fitted 2nd order (f14 wn 29.8, f16 wn 27.3, z 0.4) | 0.0334 | 0.0318 |
| pitch | passthrough (flown) | 0.0333 | 0.0308 |
| pitch | firmware 2nd order wn 44, z 0.8 | 0.0312 | 0.0271 |
| pitch | fitted 2nd order (f14 wn 25.0, f16 wn 22.9, z 0.4) | 0.0298 | 0.0260 |
| yaw | passthrough (flown) | 0.0777 | 0.1079 |
| yaw | firmware 2nd order wn 30, z 0.8 | 0.0717 | 0.0953 |
| yaw | fitted 1st order bw 2.0 (grid floor) | 0.0323 | 0.0339 |

Findings (measured):

1. Roll and pitch follow the rate command closely (band correlation r to x 0.85-0.94). The closed-loop rate response
   fits a 2nd order wn 23-30 rad/s, z 0.4, plus 0-30 ms delay. The firmware's 2nd order wn 44, z 0.8 cuts the band
   error 7-15 % against passthrough. The fitted model cuts a few % more, but on pitch it puts the equivalent drive at
   its bound 17 % of the time (column "at bound eq"; the firmware model also does on f16 pitch, 0 % on roll), so it
   asks for more authority than the layer has.
2. Yaw barely follows its command in the band (correlation 0.13 on f14, 0.51 on f16; passthrough lag 150 ms). The
   1st-order fit lands on the grid floor (2 rad/s) with nrmse 0.83 / 0.47: the "best" yaw model is one so slow it
   nearly ignores r. The firmware yaw model (30 rad/s) asks the adaptive layer to make yaw 15x faster than the
   PID loop delivers.
3. Standing offset: the mean error is identical for every model (roll -0.127, pitch -0.048 / -0.059 rad/s) and makes
   most of the raw rms. On f14 gyrox Des is 7.22 deg/s against FB 0.03 deg/s, and U 36.6 equals Kp 5 x e: the angle
   integrator carries the lean trim as a standing rate setpoint that the rate loop never follows, so the MRAC sees it
   as a permanent tracking error and its integral-like terms wind on it.

PROPOSED settings (post-demo branch, check on the bench before a flight):

| Axis | Reference | Why |
|---|---|---|
| roll, pitch | type 2, wn 44, z 0.8 (current default parameters, switched on) | -7..-15 % band error, no bound hits on roll, 17 % on f16 pitch |
| yaw | type 1, bw 2-5 rad/s, or yaw adaptation off | the yaw loop is ~15x slower than the current model; fix the yaw rate loop first |
| all | remove the DC from e (0.2 Hz high-pass on e, or move the lean trim into the rate integrator) | the standing -0.13 rad/s error dominates and is not a dynamics error |

### x/y drift, before and after F3 (measured)

Airborne mean and sd of the position error (locx/locy FB - Des, cm), `sim.bench.sysid.reallog.load_wide`:

| Flight | Firmware | ex | ey |
|---|---|---|---|
| flight14 | pre-14ce82f | +6.72 +- 2.72 | |
| flight16 | pre-14ce82f | -10.46 +- 4.43 | |
| flight_test_drift_fix_1 (10-03, 50.2 s) | F1x/F3 (14ce82f) | -0.63 +- 3.35 | +0.65 +- 2.96 |

On f14 locx U 7.38 = 0.8 x 6.71 + 2: the old position integrator sat at its cap of 2, so the bias could not be
trimmed out. WP-13 (14ce82f: F1x angle I carries the lean, F3 position I-limit, velocity I, trim, FF) removed the
standing offset; what remains is about
3 cm sd of wander. This shapes item D: an x/y adaptive layer now has a variance and disturbance problem to solve,
not a bias.

### Bench fidelity gap (found while doing B)

`sim/adaptive_compare/sim_coupled.py` (frozen) and `sim/bench/fwpid.py` (frozen) still carry the pre-F1x/F3 gains
(angle Kp 2.6, rate Ki 0.01 / SumEmax 1000, locx Ki 0.01 / SumEmax 200, locxs Ki 0). HEAD `API/pid.c` has angle
Kp 3.0 Ki 0.02, locx Ki 0.0013 SumEmax 3850, locxs Kp 3.0 Ki 0.008. So the bench "pid_fw" row is the old firmware.
Items D and F state results against both, or add a non-frozen `ctrl_fwpid_f3.py` with the HEAD table.

## C. Public datasets (sizes read 2026-10-05 from HTTP headers and the GitHub tree API, nothing downloaded)

| Dataset | File | Source | Size | Use here |
|---|---|---|---|---|
| NeuroBEM (Bauersfeld et al., RSS 2021) | processed_data.zip | https://download.ifi.uzh.ch/rpg/NeuroBEM/ | 657,316,788 B | 96 flights, 1 h 15 min; motor speeds, IMU, mocap; aggressive flight: rotor drag, vh^2 thrust term |
| NeuroBEM | raw_data.zip (optional) | same | 455,214,377 B | only if processed_data lacks a channel |
| NeuroBEM | predictions.tar.xz (optional) | same | 235,443,588 B | their BEM / NeuroBEM residual predictions, a reference for our FROLS ranking |
| Neural-Fly (O'Connell et al., Sci. Robot. 2022) | data/training, data/training-transfer, data/experiment | https://github.com/aerorobotics/neural-fly | 41 files, 141.6 MB | wind-tunnel flights at several wind speeds: the disturbance-adaptation case for items D and H |

The Neural-Fly repository has no license file (GitHub API license: null); use it for research comparison only and cite
the paper. Minimum useful set: NeuroBEM processed_data.zip plus the Neural-Fly data folders, about 800 MB.

## D. Adaptive layers on x, y, z and yaw (`sim/bench/ctrl_mrac6.py`; frozen bench; same protocol as A)

The bench MRAC adapts roll and pitch only; x/y velocity, z velocity and yaw rate stay PID. `MRAC6_Dec` keeps
`MRAC_SatAware` and adds one normalised layer per remaining loop, all with the same law in dimensionless units so a
single knob `gamma_o` drives them:

| Layer | Add-on | Regressor phi (divided by the loop's command limit) | Reference model tau | Bound |
|---|---|---|---|---|
| x, y velocity | acceleration, earth frame, before the yaw rotation | [1, v_i, v_i abs(v_i)]; `MRAC6_Cpl` adds v_j (x/y coupling) | (1 + Kd DT) / Kp of LOCXS | tan(15 deg) g |
| z velocity | throttle | [1, vz] | 1 / loop gain of RATE_Z at hover | 0.3 g |
| yaw rate | U_yaw | [1, r] | 0.5 s (yaw bandwidth from B) | 0.3 of the RATE_Y range |

Update th' = gamma_o phi e / (1 + phi'phi), e = v - v_ref, clipped to the bound. Tuned with `tune2.py`, starting from
the sat-aware stage 1 (64 + 64 = 128 evaluations).

### Full layer set (test split, median RMSE, diff vs pid_tuned2 with bootstrap 95 % CI)

| Controller | All rows | Diverged | Diff [CI] | Held-out families | Diverged | Diff [CI] |
|---|---|---|---|---|---|---|
| mrac_sataware | 0.0726 | 5.5 % | -0.0049 [-0.0107, -0.0013] | 0.0754 | 16.2 % | -0.0059 [-0.0336, 0.0082] |
| pid_tuned2 | 0.0775 | 6.7 % | 0 | 0.0813 | 18.2 % | 0 |
| mrac6_dec | 0.0945 | 2.0 % | +0.0171 [0.0103, 0.0236] | 0.1086 | 10.1 % | +0.0273 [0.0035, 0.0860] |
| mrac6_cpl | 0.0952 | 2.0 % | +0.0177 [0.0104, 0.0237] | 0.1086 | 10.1 % | +0.0272 [0.0040, 0.0865] |

The tune drove gamma_o to its floor (0.01) and the tune J stayed at 0.141 against 0.1012 for sat-aware: the layers
lowered divergence but cost accuracy, and the x/y coupling term changed nothing.

### Ablation (tune split J, sat-aware tuned parameters, one layer group at a time)

| Layers | gamma_o 0.01 | 0.03 | 0.1 | 0.3 | 1.0 | 3.0 |
|---|---|---|---|---|---|---|
| none (sat-aware) | 0.1012 | | | | | |
| x, y, z, yaw | 0.1188 | 0.2601 | 0.9873 | 1.4072 | | |
| yaw only | | 0.2611 | | 1.4201 | | |
| yaw only, add-on sign flipped | | 0.1743 | | | | |
| z only | | 0.1009 | | 0.1000 | | |
| x, y only | | 0.0966 | | 0.0931 | 0.1096 | 0.1878 |
| x, y, z | | | | 0.0910 | 0.1080 | |

The yaw layer is what broke the full set; neither sign works. Likely cause (PROPOSED, not tested): its bias term
is a second integrator in parallel with the RATE_Y integral, and the 0.5 s reference model is the flight yaw
bandwidth, not the bench rate loop's, so the layer drags every fast yaw response back toward a slower model.
The x, y layer alone gives -8 %, and x, y + z gives -10 % at gamma_o 0.3. `MRAC5_XYZ` keeps those two and drops yaw.

### x, y + z only (`MRAC5_XYZ`, same 128-evaluation protocol, test split)

Tune J 0.0905 (start 0.0910 at gamma_o 0.3; tuned gamma_o 0.2765, attitude gamma 0.0316).

| Controller | All rows | Diverged | Diff vs PID [CI] | Held-out families | Diverged | Diff [CI] | Held-out traj | Diff [CI] |
|---|---|---|---|---|---|---|---|---|
| mrac5_xyz | 0.0631 | 5.1 % | -0.0143 [-0.0201, -0.0105] | 0.0654 | 15.2 % | -0.0159 [-0.0643, -0.0005] | 0.0587 | -0.0093 [-0.0181, -0.0063] |
| mrac_sataware | 0.0726 | 5.5 % | -0.0049 [-0.0107, -0.0013] | 0.0754 | 16.2 % | -0.0059 [-0.0336, 0.0082] | 0.0659 | -0.0021 [-0.0096, 0.0020] |
| pid_tuned2 | 0.0775 | 6.7 % | 0 | 0.0813 | 18.2 % | 0 | 0.0680 | 0 |

`MRAC5_XYZ` is the best bench controller so far: -18 % median RMSE against tuned PID on all rows, and the first one
whose held-out-family CI clears zero (just: upper bound -0.0005). It still carries the tuned knobs of the sat-aware
base, so it is not yet the no-tuning design H asks for.

Implication for H: adapt at the velocity level where PID leaves a steady disturbance (drag, payload, CoM offset),
not inside a loop that already integrates; take tau from the loop that is wrapped, measured on the same plant.

## E. 6-axis MRAC in firmware: RAM budget (sizes from `OBJ/JX_FLY.map`, HEAD build)

The firmware MRAC already runs 4 axes (`API/mrac.h:104`, `AXES 4`: pitch, roll, yaw, z). Going to 6 adds x and y
velocity. Every per-axis object is sized by `AXES`:

| Object | Region | Size now (4 axes) | Per axis |
|---|---|---|---|
| mrac_state | CCM | 624 B | 156 B |
| mrac_config_{pitch,roll,yaw,z} | CCM | 4 x 232 B | 232 B |
| mrac_bus | CCM | 128 B | 32 B |
| mrac_g_gamma, g_sigma, g_phi ([AXES][6] float) | CCM | 3 x 96 B | 72 B |
| mrac_u_ff | CCM | 16 B | 4 B |
| mrac_cyc (cycle profile, last + max per axis) | SRAM .bss | 136 B | 32 B (2 x 16 B MRAC_CycSet_t; l2_last, l2_max 8 B fixed) |
| mrac_var_id, mrac_ref_type_eff | SRAM .data | 8 + 4 B | 3 B |

Computed: two more axes cost about 2 x 496 = 992 B of CCM and about 70 B of SRAM. CCM use is 14,288 of 65,536 B
(RW_IRAM2 0x37d0), so about 51 KB stays free. SRAM is the tight one at HEAD: 125,000 of 131,072 B (RW_IRAM1 0x1e838),
6,072 B free, which covers the 70 B; the ram-savings branch moves SRAM to 22,048 B. RAM does not block 6 axes.

Not measured: CPU cost per axis (read `mrac_cyc` on hardware), and the telemetry slots for the two new axes (the MRAC
frame carries 6 fixed values, `API/mrac.h:55`). PROPOSED branch (post-demo, not merged or flashed): add
MRAC_AXIS_X and MRAC_AXIS_Y as velocity-level add-ons to the LOCXS output, the `MRAC5_XYZ` structure from D, with
the yaw axis bias term off.

## H. No-tuning adaptation, step 1 (`sim/bench/ctrl_h0.py`; frozen bench; test split; `heldout.py`)

Tuned PID (pid_tuned2 parameters, unchanged) plus the x, y, z layers of `MRAC5_XYZ`; attitude stays on the PID. The
one tuned knob, gamma_o, is replaced by a rate read off the wrapped loop: gamma_i = 1 / (SEP tau_i), tau_v 0.139 s
(x, y velocity loop), tau_z 0.055 s (z rate loop), computed from the pid_tuned2 gains. No parameter is tuned.

| Controller | gamma xy / z [1/s] | All rows | Diverged | Diff vs PID [CI] | Held-out fam | Fam diff [CI] | Held-out traj diff [CI] |
|---|---|---|---|---|---|---|---|
| h0 (SEP 1) | 7.19 / 18.3 | 0.3459 | 29.7 % | +0.2685 [+0.2484, +0.2855] | 0.4059 | +0.3246 [+0.2606, +0.5834] | +0.2365 [+0.2227, +0.2570] |
| h0_sep (SEP 10) | 0.72 / 1.83 | 0.0819 | 6.7 % | +0.0045 [-0.0009, +0.0082] | 0.0873 | +0.0060 [-0.0201, +0.0228] | +0.0043 [-0.0030, +0.0084] |
| mrac5_xyz (tuned) | 0.28 / 0.28 | 0.0631 | 5.1 % | -0.0143 [-0.0201, -0.0105] | 0.0654 | -0.0159 [-0.0643, -0.0005] | -0.0093 [-0.0181, -0.0063] |

Measured: adapting at the loop's own bandwidth destabilises (30 % of rows diverge); the textbook decade of time-scale
separation is safe but buys nothing over PID (CIs span zero). The tuned rate sits 26x (xy) and 66x (z) below 1/tau,
and MRAC5_XYZ also keeps the sat-aware attitude base (alone -0.0049 in sec D). So "tau of the wrapped loop" does not
by itself fix the rate. Next candidates (not run): gamma from the excitation bound (normalised-gradient stability
margin, gamma dt < 2 per step is far looser than this), or from the slowest loop in the cascade (position, tau of
LOCX); and the h0_sep base swapped to the sat-aware attitude loop to split the two sources of MRAC5's gain.

## I. Position from optical flow + IMU: the roam-and-return bias

Today's x/y position is dead reckoning. The ANO module sends `of2_dx_fix/of2_dy_fix` (body velocity, cm/s, already
fused with the module's own IMU, mode 2). `API/ekf_of.c` filters it with a tilt-driven constant-velocity model, and
`Of_IntegratePosition` (TASK/StabilizerTask.c) scales the increments by `g_of_scale` 1.094, rotates them by yaw and
sums them. Nothing absolute ever corrects the sum. So every velocity error that does not average to zero ends up in
the position.

Why a loop is a good test: if the drone lands where it took off, any constant error cancels: a constant scale,
a constant mounting rotation, or a constant tilt. What is left at the landing spot (the closure) is only the error
that changes over time or with direction.

### Measured (10-03 flights, same firmware as drift_fix_1; airborne mask z > 0.5 max)

| Flight | Airborne | Path | Closure fw-x | Closure fw-y | 1.094 x integral of raw body-y flow | Body-y bias = raw integral / time |
|---|---|---|---|---|---|---|
| roaming_and_landing_1 | 143 s | 4794 cm | -200 cm | -48 cm | -211 cm | -1.35 cm/s |
| roaming_and_landing_2 | 110 s | 5012 cm | -110 cm | -4 cm | -128 cm | -1.06 cm/s |
| roaming_and_landing_3 | 128 s | 4887 cm | -147 cm | -4 cm | -151 cm | -1.08 cm/s |
| roaming_and_landing_4 | 114 s | 4507 cm | -85 cm | +3 cm | -90 cm | -0.72 cm/s |
| drift_fix_1 (hover) | 122 s | hover | | | -40 | -0.30 cm/s |

Body-x bias on the same flights: -0.16 to +0.18 cm/s.

Findings:

1. **The error comes out of the sensor.** Integrating the raw body-y flow reproduces the fw-x closure to within 4-18 cm.
   The firmware bias correction `s_of_bias` was 0 on all four flights. The yaw range was only 6-15 deg.
   So neither the KF, nor the yaw rotation, nor the bias logic creates it.
2. **These causes are ruled out.**
   - Dropouts: flow quality never went below 245; the integration gate is 50.
   - A constant scale: it cancels on a loop.
   - A direction-dependent scale. I regressed the flow acceleration on g*tan(tilt), separately for + and - motion
     (roll for body y, pitch for body x). The gain per direction was:
     - body y: 0.878/0.878, 0.930/0.896, 0.937/0.949, 0.905/0.884;
     - body x: up to 7 % apart, yet body x closes to within a few cm.

     The closure would need a 7-18 % asymmetry, consistently in one direction.
   - Speed nonlinearity. A v|v| term large enough to explain the closure needs 6.2-8.8e-3 s/cm on the roaming
     flights, but 25.6e-3 in hover; a real speed law would give one value. That is a 31-44 % over-read at 50 cm/s
     (computed). The tilt fit puts the gain at about 50 cm/s within 2-8 % of the gain near zero speed.
3. **The data matches a slow velocity bias on body y.** It is -0.7 to -1.35 cm/s while roaming and -0.30 cm/s in the
   hover flight. At -0.30 cm/s the hover estimate drifts by about 18 cm/min (computed), small enough to go unseen. The
   bias while roaming is 3-4x larger, so it grows with manoeuvring.
4. **The scale can be calibrated without the wall test.** The same tilt fit gives a flow gain of 0.88-0.95 on both
   axes, i.e. a scale of 1.05-1.14, consistent with the wall-calibrated 1.094. This is a self-calibration that needs no
   tuned constant.

Likely mechanism (PROPOSED, not measured). The module's own gyro compensates the flow for rotation. Its bias b
appears as a velocity error of b x h; at h = 1 m, 1 cm/s takes only 0.01 rad/s (0.57 deg/s, computed). Roll rate
compensates body y, so a roll-gyro bias in the module that drifts with temperature and vibration fits findings 3 and 4.

One flight decides it. `API/Ano_OF.c` already parses:

| Frame | Contents |
|---|---|
| 0x51 mode 0 | raw flow `of1_dx/dy` |
| 0x51 mode 2 | `of2_dx/dy`, height-fused but without the module's inertial fusion |
| 0x01 | the module's own IMU `gyr_data_*` and `acc_data_*` |
| 0x04 | quaternion |

None of these is logged. Log them next to the FC gyro on one roam-and-return flight.

### State of the art with only optical flow and IMU

| Technique | What it fixes | Where it is used | Applies here |
|---|---|---|---|
| Fuse flow as angular rate (rad/s) with height inside the filter, not as a pre-multiplied velocity | height and scale errors become filter states with uncertainty | PX4 EKF2, ArduPilot EKF3 | yes, with raw `of1`/`of2` |
| Re-do gyro compensation with the autopilot gyro (FC BMI088) instead of the sensor's gyro | removes the module's gyro bias, the likely cause above | PX4 EKF2 selects the flow-compensation gyro source (sensor or autopilot) | yes, P2 |
| Two IMUs as a bias monitor: module gyro minus FC gyro, after estimating the fixed mounting rotation | the module's gyro bias becomes a measured quantity, not a tuned one | PX4 runs one EKF per IMU and selects the healthiest | yes, P3 |
| Noise scaled by quality and angular rate, chi-square innovation gate, robust (Huber) update | outliers, glare, fast rotation | PX4 EKF2 flow noise and gate parameters | partly done (health gate) |
| Flow-to-gyro time alignment by cross-correlation | rotation residual during fast attitude changes | standard VIO calibration (Kalibr-style time offset) | yes, offline from logs |
| Rotor and momentum drag aiding from body accel x/y, optionally scaled by RPM | gives velocity relative to the air without flow (Leishman et al., IEEE CSM 2014) | PX4 EKF2 drag fusion | flow-loss fallback only: accel bias limits it to several cm/s, so it cannot see a 1 cm/s bias. Needs accel x/y logging to measure the drag coefficient |
| Learned inertial odometry (TLIO; learned IO for drone racing, Cioffi 2023) | displacement from the IMU alone | research | needs mocap training data and is tuned by data, against the no-tuning goal |
| An absolute anchor: floor marker or AprilTag at the pad, UWB, or a known landing pad (ZUPT plus reset on landing) | the only way to bound dead-reckoning drift | everywhere | yes, P5. No flow+IMU method can observe a slow velocity bias |

Data cleaning that applies offline today:

- an RTS smoother over the logged KF (better trajectories for sysID);
- gyro-compensation residual checks;
- the tilt-based scale fit above;
- per-flight closure as a bias label.

### PROPOSED upgrades (post-demo branch, in order)

| # | Change | Firmware behaviour | Tuned constants removed |
|---|---|---|---|
| P1 | Log `of1` raw, `of2_dx/dy`, module `gyr_data_x/y`, FC gyro; one roam-and-return flight | none (logging) | - |
| P2 | Own rotation compensation: v = (flow rate - FC gyro) x h from raw flow | changes the velocity input | the module's internal fusion |
| P3 | Online module-gyro bias = module gyro - rotated FC gyro, applied to the flow | estimator | `s_of_bias` seed and mode |
| P4 | Online scale from the tilt fit (flow acceleration vs g tan tilt) | estimator | `g_of_scale` 1.094 |
| P5 | ZUPT and position reset when landed on the known pad, or an operator "at origin" event | estimator | - |
| P6 | Drag and RPM aiding as a fallback when flow is lost (after accel x/y logging) | estimator | - |

## J. Asymmetric-load demo prediction (`sim/bench/demo_loads.py`)

Bench prediction for the 10-06 campaigns `asym_load_pid` / `asym_load_mrac`. Nominal family, 10 seeds, rigid load (no
pendulum swing), controllers with the flashed F1x angle/rate integrator rows. `results/demo_loads.json` (yaw imbalance
as measured on 10-03, default) and `results/demo_loads_yaw430.json` (the bench's old draw). Reprint with
`python demo_loads.py --tables results/demo_loads.json`.

**Yaw imbalance prior was stale.** The bench draws u_imb 350-500 U (09-27 spin flights): one diagonal pair runs that
much hotter, which leaves bench m0+m1 near the PWM floor. The 10-03 flights on the flashed mixer (logs/vofa
`*drift_fix*_1.slot2.csv`, airborne rows, Throttle_out > 2600) measure (M1+M2-M3-M4)/4 = +66 U (drift_fix_1) and +1 U
(roaming_and_returning_1). Bench k = firmware M(k+1) for roll and pitch (`API/controller.c` g_mix). This is a caveat on
every bench result in sections A-I: they were tuned and compared under a yaw imbalance the flashed drone no longer has.

Median RMSE [m] over non-diverged seeds (diverged seeds of 10 in brackets):

| case | traj | PID+F1x | MRAC V2 sat-aware+F1x | MRAC5_XYZ (bench only) |
|---|---|---|---|---|
| no load | hover | 0.0289 | 0.0288 | 0.0299 |
| pads 250 g | hover | 0.0335 | 0.0335 | 0.0282 |
| pads 500 g | hover | 0.0421 (3) | 0.0556 | 0.0391 |
| pads 500 g | zigzag 0.2 | 0.0593 (3) | 0.0696 | 0.0444 |
| arm 250 g, each of 4 motors | hover | 0.034-0.044 | 0.035-0.046 | 0.032-0.044 |
| arm 500 g, m0 or m2 | both | (10) | (10) | (10) |

With the old u_imb 430 draw, arm 250 g next to bench m0 or m1 (the cool pair) diverges 6-10/10 for every controller and
next to m2/m3 holds: the corner effect was the stale imbalance, not the load.

Paired difference vs PID+F1x (95 % bootstrap CI, seeds where neither diverged): MRAC V2 sat-aware has no case whose CI
excludes zero in its favour; pads 500 g hover it is worse, +0.0048 [+0.0038, +0.0091]. MRAC5_XYZ is better on pads
500 g: hover -0.0036 [-0.0085, -0.0026], zigzag -0.0150 [-0.0423, -0.0031].

**Pads 500 g fails on altitude, not attitude.** The 3 PID seeds flagged diverged (2000, 2002, 2008) have tilt <= 1.4 deg
and rmse_z 0.99 m: the drone cannot hold height. MRAC V2 sat-aware on the same 3 seeds is not flagged but has rmse_z
0.73-0.80 m, so it fails the same way. Only MRAC5_XYZ, which adapts z, holds (rmse_z <= 0.016). Throttle authority in
the firmware (`TASK/StabilizerTask.c` Mix_Compute): HOVER_THR_FREE 3050 + Z_ratePID U (Umax 300, Uimax 100). Computed,
not measured: scaling the bench thrust curve to the 10-03 hover (motor mean about 3035), +250 g needs about 3147 PWM
and +500 g about 3251, i.e. +100 and +200 above the base, against an integrator room of 100 in Z_rate.

Flight reading (PROPOSED, for the operator):
- 250 g on the pads or on any arm: the sim predicts it flies with every controller and no measurable PID/MRAC
  difference. It is a safe first step, but not a demonstration of the adaptive advantage.
- 500 g on the pads: expect altitude sag with PID. This is the case where adaptation can show, but only z
  adaptation: the bench sat-aware model adapts attitude only and sags like PID. The firmware has a z path
  (`API/controller.c`, CTRL_AXIS_Z adds mrac_state.z_rate.u_ad); confirm it is adapting (z_rate u_ad moving in the
  telemetry of the no-load hover) before reading the 500 g result as a controller comparison. RC hand on land.
- 500 g on an arm: every controller diverges in the sim (near motor at about 82 % of T_MAX static). Do not fly it.
- Arm-mass sweep (`demo_loads.py --sweep`, results/demo_loads_sweep.json, 10 seeds, motors m0 and m2): 300 and
  350 g fly for all three (one MRAC5 zigzag seed diverged at 350 g m0; paired CIs span 0). At 400 g the adaptive
  variants fail first: diverged seeds on m0 hover PID 1, SatAware 7, MRAC5 9; m2 hover 0 / 1 / 4. At 450 g
  PID 5-9, SatAware 9-10, MRAC5 10. So on an arm, keep the load at or below 350 g, and expect no adaptive
  advantage there in the sim: the arm case is a PID-vs-MRAC safety check, the pads 500 g case is the comparison.

Caveats: rigid load (a hung load's swing is not modelled); H_PAD 0.10 m is PROPOSED; the arm CoG shift (up to 56 mm at
500 g) lies outside the cog range the controllers were tuned on; batch noise moves a case's median by 10-20 % between
runs; F1x covers only the angle and rate integrator rows (not locx F3 or the z rows); the measured u_imb comes from 2
flights; the bench hover calibration X2_H assumes U_IMB_NOM 430.

**J addendum: the firmware's altitude adaptation (read from the code, not flown).** The bench's `mrac_sataware`
adapts attitude only, but the firmware's MRAC also runs a z-rate axis on every tick (API/mrac.c MRAC_Control: z has
no axis-enable flag) and adds it to the throttle under `CTRL_MRAC` (API/controller.c `mrac_correction`, times fade and
inj_alpha). The `v2_sataware` preset does not touch z, so z flies on the MRAC_Init rows:

| Row (API/mrac.c) | Value | Consequence |
|---|---|---|
| e = x - xm (z velocity minus reference model), grad = -e phi | load sinks the drone -> e < 0 -> bias weight grows | right sign: adds thrust |
| bias basis gamma / limit / lower | 2.0 / 1.0 / 0.0 | bias weight in [0, 1]: thrust can only be added |
| mrac_to_mixer (PAYLOAD_LIGHT) | 222 mixer units per unit u_ad | bias alone reaches +222 units (computed) |
| u_max | 13.48 | not the binding limit (13.48 x 222 >> 222) |
| e_deadzone | 0.05 m/s | no learning while the z-velocity error is under 5 cm/s |

Computed reading: +500 g on the pads needs about +200 PWM over the 3050 base (sec J), the PID's Z_rate integrator gives
at most 100, and the z bias weight can give up to +222 more. So the flight test of "MRAC handles the 500 g pad load"
does exercise an adaptive path that exists in the firmware, unlike the bench's attitude-only model. The 5 cm/s deadzone
means it learns during the sag transient only: expect a dip on load-up, then `mrac_state.z_rate.u_ad` settles and
stays. Check on the no-load MRAC hover that `u_ad_z` (flight_signals.yaml) is near 0 and finite before loading. Not
verified on the drone. Both asym_load twins log it: their `mrac_shadow` group (ground_station/livewatch/
campaign_capture.py, MRAC_AXES includes z_rate) carries z u_ad, u_nom, u_def, e and the Theta/Whatf weights at 50 Hz.

## K. Stress ladder: disturbances, model mismatch and speed from moderate to extreme (`sim/bench/stress.py`)

Why (user, 2026-10-05 evening): the comparisons in A, D, H and J were made on a bench with assumptions that favour
the PID. Audit of `scen.py` / `plant.py` against that critique:

| Flaw in the frozen bench | Effect on the verdicts above |
|---|---|
| each disturbance family exists at ONE moderate level (wind 2-4 m/s, payload +15-30 %, motor loss 15-25 %) | nobody is pushed to where controllers come apart; differences are a few mm |
| payload is a mass/inertia change present from t=0 | no load pickup, no swinging load, no off-centre pull; the asymmetric-load demo is not modelled |
| `pid_tuned2` was CMA-tuned on the same families it is scored on | the baseline is an oracle for the test disturbances |
| headline = median RMSE | rows that diverge barely move a median |
| the adaptive laws use the plant's exact constants (mass, tau, hover PWM) | no structural model mismatch |
| motor tau, command delay and thrust coefficient are exact | the classic adaptive-control failure modes (unmodelled lag) are never exercised |
| trajectories <= 1.5 m/s | no aggressive flight |
| the nominal yaw mixer imbalance is the 09-27 one (350-500 U); the flashed 10-03 mixer measures 1-66 U | any case that needs a motor differential (arm load, motor loss) starts with the yaw channel half spent (found by this ladder, sec K.3) |

So the verdicts of A, D, H0 and J are conditional on that bench: true for mild disturbances against a PID tuned for
them, silent about the rest.

Protocol here (state-of-the-art practice, e.g. Neural-Fly O'Connell 2022 winds to ~12 m/s, Sun 2022 T-RO
NMPC vs INDI at speed, L1-adaptive payload studies; figures from memory, unverified):
- every axis is a ladder L0 nominal, L1 moderate .. L4 extreme; one axis at a time, plus a combo axis with four at once;
- loads are a separate body (point mass on a stiff spring, rigid or on a cable): picked up mid-flight, swinging under
  the pads, or hanging off one arm tip; nothing in any controller knows about them;
- model mismatch: thrust coefficient +-10..40 %, motor time constant x1.5..4 with delay 4..10 ticks, inertia x1.2..2;
- speed: lemniscate 1.5 m half-width at 1.0 .. 3.0 m/s peak;
- rows: 4 trajectories (hover, circle_1.0, zigzag_1.0, lemniscate A=1.5 m at 1.5 m/s) x 3 seeds per cell, the same rows
  and noise for every controller (paired); 459 rows per controller;
- headline: divergence rate per level and the break point (first level with >= 25 % of rows diverged), then p90 RMSE;
- baselines: `fw_pid` = the flashed firmware gains, untuned; `pid_tuned2` = the oracle PID; adaptive = `mrac_sataware`,
  `mrac5_xyz`, `h0_sep_fw` (H0_Sep on the firmware gains: no tuned parameter anywhere).

`python stress.py check` proves `run_ext` (the extended plant) equals the frozen `plant.run` bit for bit when no
extension key is set (PASS, 2026-10-05).

| Axis | L1 | L2 | L3 | L4 |
|---|---|---|---|---|
| wind_gust: mean / Dryden sigma / 2 gusts [m/s] | 2 / 0.5 / 3 | 4 / 1 / 5 | 6 / 1.5 / 7 | 8 / 2 / 10 |
| mass_step: rigid load picked up at t = 8 s, 5 cm below CoG [kg] | 0.15 | 0.30 | 0.50 | 0.70 |
| swing_load: 0.3 m cable from the pads, from t = 0 [kg] | 0.10 | 0.25 | 0.40 | 0.55 |
| arm_load: 0.1 m cable from motor 0's arm tip [kg] | 0.10 | 0.20 | 0.30 | 0.40 |
| motor_loss: one motor's efficiency from t = 8 s | 0.85 | 0.70 | 0.60 | 0.50 |
| kt_mismatch: all motors, random sign | +-10 % | +-20 % | +-30 % | +-40 % |
| actuator: motor tau x / delay [5 ms ticks] (nominal 3) | 1.5 / 4 | 2 / 6 | 3 / 8 | 4 / 10 |
| inertia: J x | 1.2 | 1.4 | 1.7 | 2.0 |
| combo: wind_gust + arm_load + kt_mismatch + actuator at the same level | | | | |
| speed: lemniscate peak [m/s] (L0 = 1.0) | 1.5 | 2.0 | 2.5 | 3.0 |

### K.1 Results (measured 2026-10-05, `results/stress_<tag>.json`, `python stress.py report fw_pid pid_tuned2 mrac_sataware mrac5_xyz h0_sep_fw`)

Yaw imbalance drawn in the measured 10-03 range (`YAW_IMB = '1003'`, sec K.3). Overall share of the 459 rows that
diverged: fw_pid 47.1 %, pid_tuned2 34.6 %, mrac_sataware 31.6 %, mrac5_xyz 25.1 %, h0_sep_fw 27.0 %. (The first
run, with the 09-27 imbalance: 41.6 / 34.4 / 31.8 / 27.9 / 29.0 %; the per-axis picture moved more than the totals.)

Divergence % per level (rows: 4 trajs x 3 seeds = 12 per cell; speed: 3 per cell); break = first level with >= 25 % diverged.

| axis | fw_pid L0..L4 div % | fw_pid break | pid_tuned2 L0..L4 div % | pid_tuned2 break | mrac_sataware L0..L4 div % | mrac_sataware break | mrac5_xyz L0..L4 div % | mrac5_xyz break | h0_sep_fw L0..L4 div % | h0_sep_fw break |
|---|---|---|---|---|---|---|---|---|---|---|
| wind_gust | 0 0 0 17 50 | 4 | 0 0 0 8 25 | 4 | 0 0 0 0 17 | >4 | 0 0 0 0 17 | >4 | 0 0 0 0 0 | >4 |
| mass_step | 0 0 50 100 100 | 2 | 0 0 0 67 100 | 3 | 0 0 0 17 92 | 4 | 0 0 0 0 8 | >4 | 0 0 0 8 83 | 4 |
| swing_load | 0 8 67 100 100 | 2 | 0 0 0 8 67 | 4 | 0 0 0 0 33 | 4 | 0 0 0 0 0 | >4 | 0 0 0 8 67 | 4 |
| arm_load | 0 0 33 75 100 | 2 | 0 0 0 50 100 | 3 | 0 0 0 92 100 | 3 | 0 0 0 58 100 | 3 | 0 0 0 0 42 | 4 |
| motor_loss | 0 0 0 17 75 | 4 | 0 0 0 0 0 | >4 | 0 0 0 0 8 | >4 | 0 0 0 0 17 | >4 | 0 0 0 0 8 | >4 |
| kt_mismatch | 0 0 58 67 100 | 2 | 0 0 0 33 100 | 3 | 0 0 0 8 100 | 4 | 0 0 0 0 17 | >4 | 0 0 0 33 100 | 3 |
| actuator | 0 0 83 100 100 | 2 | 0 0 100 100 100 | 2 | 0 0 83 100 100 | 2 | 0 0 67 100 100 | 2 | 0 8 75 100 100 | 2 |
| inertia | 0 0 0 0 0 | >4 | 0 0 0 0 0 | >4 | 0 0 0 0 0 | >4 | 0 0 0 0 0 | >4 | 0 0 0 0 0 | >4 |
| combo | 0 42 100 100 100 | 1 | 0 92 100 100 100 | 1 | 0 92 100 100 100 | 1 | 0 100 100 100 100 | 1 | 0 50 100 100 100 | 1 |
| speed | 0 0 33 100 100 | 2 | 0 0 100 100 100 | 2 | 0 0 67 100 100 | 2 | 0 0 100 100 100 | 2 | 0 0 0 100 100 | 3 |

p90 RMSE [m] per level, nearest rank (div = the p90 row diverged)

| axis | fw_pid | pid_tuned2 | mrac_sataware | mrac5_xyz | h0_sep_fw |
|---|---|---|---|---|---|
| wind_gust | 0.991 1.024 1.018 div div | 0.092 0.128 0.124 0.512 div | 0.081 0.101 0.127 0.284 div | 0.095 0.095 0.099 0.233 div | 0.818 0.872 0.849 0.886 0.982 |
| mass_step | 0.991 1.117 div div div | 0.092 0.090 0.085 div div | 0.081 0.078 0.080 div div | 0.095 0.088 0.085 0.093 0.301 | 0.818 0.887 0.942 1.094 div |
| swing_load | 0.991 1.114 div div div | 0.092 0.085 0.080 0.696 div | 0.081 0.085 0.077 0.113 div | 0.095 0.092 0.107 0.130 0.143 | 0.818 0.835 1.010 1.180 div |
| arm_load | 0.991 1.112 div div div | 0.092 0.085 0.217 div div | 0.081 0.081 0.228 div div | 0.095 0.090 0.188 div div | 0.818 0.890 1.063 1.224 div |
| motor_loss | 0.991 1.058 1.137 div div | 0.092 0.096 0.085 0.145 0.212 | 0.081 0.073 0.076 0.122 0.470 | 0.095 0.094 0.097 0.285 div | 0.818 0.843 0.875 0.945 1.030 |
| kt_mismatch | 0.991 0.819 div div div | 0.092 0.083 0.079 div div | 0.081 0.072 0.072 0.897 div | 0.095 0.084 0.082 0.088 div | 0.818 0.830 1.044 div div |
| actuator | 0.991 0.992 div div div | 0.092 0.090 div div div | 0.081 0.091 div div div | 0.095 0.094 div div div | 0.818 0.845 div div div |
| inertia | 0.991 1.074 1.053 1.064 1.062 | 0.092 0.099 0.097 0.197 0.245 | 0.081 0.074 0.076 0.091 0.094 | 0.095 0.082 0.087 0.084 0.096 | 0.818 0.852 0.832 0.870 0.858 |
| combo | 0.991 div div div div | 0.092 div div div div | 0.081 div div div div | 0.095 div div div div | 0.818 div div div div |
| speed | 0.969 1.079 div div div | 0.079 0.085 div div div | 0.086 0.085 div div div | 0.079 0.088 div div div | 0.670 0.839 1.121 div div |

### K.2 What the ladder shows that the old bench hid

1. Every controller breaks somewhere: actuator lag at L2 and the combo at L1 break all five. The question is which
   axis breaks first and why.
2. Loads separate the controllers, as the user expected. `mrac5_xyz` never diverges on the swinging load (up to
   0.55 kg on a 0.3 m cable, p90 0.09-0.14 m) and holds the mid-flight pickup to L3 (0.5 kg, 0 %; 8 % at 0.7 kg,
   p90 0.30 m). The oracle PID breaks at L3 on the pickup (67 % at 0.5 kg) and at L4 on the swing (67 %);
   `mrac_sataware` breaks at L4 on both. The old bench (payload +15-30 %, present from t = 0) put them all within a
   few mm of each other.
3. Inertia mismatch (J x 2) diverges nobody, but the tuned PID's p90 grows 0.092 -> 0.245 m while `mrac5_xyz` stays
   0.095 -> 0.096 m and `mrac_sataware` 0.081 -> 0.094 m.
4. Thrust-coefficient mismatch: the tuned PID breaks at +-30 % (33 %), `mrac_sataware` at +-40 %, `mrac5_xyz` holds
   to +-40 % (17 % there).
5. Motor loss (one motor down to 50 % from t = 8 s) is survivable for the tuned controllers (pid_tuned2 0 %,
   sataware 8 %, mrac5_xyz 17 % at L4). In the first run it broke everyone at L3: that was the yaw imbalance (K.3).
6. Actuator lag is the binding constraint for everyone: motor tau x 2 with a 30 ms command delay (L2) diverges 67 %
   (mrac5_xyz) to 100 % (pid_tuned2) of rows. The untuned firmware gains do no better here (fw_pid 83 %, h0_sep_fw
   75 %) even though they fly with p90 0.82-0.99 m at L0 against 0.08-0.10 m for the tuned ones. So the real motor
   lag must be measured before any gain goes up, and it is not an adaptive-versus-PID question.
7. Combo (wind + arm load + kT + lag, all at L1) breaks every controller (42-100 %) although each ingredient alone at
   L1 diverges at most 8 % (h0_sep_fw on actuator). Open question, next on the list: which pair interacts.
8. Speed: only `h0_sep_fw` holds the 2.0 m/s lemniscate (0 %; it breaks at 2.5); `mrac_sataware` loses 67 %, the
   tuned PID and `mrac5_xyz` all rows there.
9. Arm load: the tuned controllers break at L3 (0.3 kg), the slow h0_sep_fw only at L4 (42 % at 0.4 kg). See K.3.

### K.3 Arm load: why 0.3 kg on one arm broke everyone in the first run, and what it means for 10-06

The first ladder run (089ad55, scen's yaw imbalance) had every controller diverging on 12 of 12 rows at arm_load L3
(0.3 kg on a 0.1 m cable from motor 0's tip), about 1.1-1.2 s into the flight, with yaw running away (max |yaw error|
79-95 deg). That contradicted sec J (demo_loads: arm loads up to 350 g fly for all controllers). Diagnosis on those
12 rows (pid_tuned2 and mrac5_xyz, measured 2026-10-05, scratchpad scripts):

| Variant on the 12 L3 rows | pid_tuned2 diverged | mrac5_xyz diverged |
|---|---|---|
| as run (cable, attached at t = 0) | 12 | 12 |
| rigid mount instead of cable | 11 | 12 |
| load attached at t = 2 s (cable or rigid) | 12 | 12 |
| demo_loads' own model (CoG + inertia shift, no load body) | 11 | 12 |
| as run, yaw imbalance redrawn in the measured 10-03 range (1-66 U) | 6 (at ~5.7 s) | 8 (at ~7.4 s) |

So the load model was not the cause. The cause was a baked-in assumption the bench inherited: scen draws the yaw
mixer imbalance at 350-500 U, the value from the 09-27 spin flights. The flashed 10-03 mixer measures 1-66 U, and
demo_loads already used that. An arm load needs a large roll + pitch differential (computed static balance at
0.3 kg: motor thrusts 5.38 / 2.44 / 3.91 / 3.91 N, PWM 1566 / 993 / 1306 / 1306); because thrust is convex in PWM
that differential also makes a yaw torque, and on top of a 350-500 U imbalance the yaw channel runs out of authority.
Like a car that already pulls hard to one side and is then loaded on one corner: the steering runs out.

With the measured imbalance the remaining L3 failures are mostly the fast trajectories:

| 0.3 kg arm, 10-03 imbalance (diag draw) | hover | circle 1.0 | zigzag 1.0 | lemniscate 1.5 |
|---|---|---|---|---|
| pid_tuned2 diverged / 3 | 1 | 1 | 2 | 2 |
| mrac5_xyz diverged / 3 | 0 | 3 | 2 | 3 |

`stress.py` now uses the 10-03 imbalance by default (`YAW_IMB = '1003'`); the tables in K.1 are the rerun with it.
The first run is kept only as this finding.

What it means for the 10-06 arm-load runs (250 g):
- Fly the arm load in hover and at slow speed first (sec J's 0.2 m/s zigzag); 0.3 kg on one arm at 1.0-1.5 m/s
  diverges in the sim for both controllers.
- Before the arm-load runs, check that the yaw trim is the 10-03 one (pairs within 1-66 U). A 09-27-sized imbalance
  plus a one-arm load is the measured failure above.
- Expect a steady tilt of about 5-6 deg with the f1x gains (computed: the rate / angle integrator caps 20 / 10 leave the
  steady roll + pitch effort, about 143 PWM units each at 0.3 kg, to the P terms). That is not a fault.
- Signature to watch live: a growing yaw error. Over the 12 diag rows the largest |yaw error| was 20-29 deg with the
  10-03 imbalance against 79-95 deg with the old one.

### K.4 What changes in the verdicts above

- A, D, H0, J: their numbers stand, but only as statements about mild disturbances against a PID tuned for them.
- The adaptive case is strongest exactly where the user tested it in flight: loads that change the plant (pickup,
  swing, inertia, thrust coefficient). It does not buy margin on unmodelled actuator lag, which the adaptive
  literature also names as its classic failure mode (Rohrs-type unmodelled dynamics; from memory, unverified).
- Next (one item per commit): a fair re-tune of PID and MRAC on nominal rows only (no oracle, `tune_nominal.py`),
  then the ladder again; the combo-L1 interaction; measure motor tau and delay from the 10-03 / 10-06 logs and set
  the actuator ladder's L0 from the measurement.

### K.5 Fair re-tune (no oracle) and the combo-L1 interaction (measured 2026-10-05 20:3x)

**Fair re-tune.** `tune_nominal.py` gives pid_tuned2, mrac_sataware and mrac5_xyz the same CMA budget as `bench.tune`
(2 stages x 64 evals, from each class's defaults) on scen 'nominal' rows only (seeds 0-3), so no controller has seen
any stress axis. Tune objective J: PID 0.0775, sataware 0.1277, MRAC5 0.0687 (both MRAC classes' first evaluation, the starting point, scored inf;
PID's 0.626). Then the same 459-row ladder:

| Controller | oracle tune (sec K.1) div % | nominal-only tune div % |
|---|---|---|
| PID (pid_tuned2 / pid_nom) | 34.6 | 33.1 |
| mrac_sataware / _nom | 31.6 | 52.9 |
| mrac5_xyz / _nom | 25.1 | 30.7 |

| axis, nominal-only tunes | pid_nom L0..L4 [break] | mrac_sataware_nom | mrac5_xyz_nom |
|---|---|---|---|
| wind_gust | 0 0 0 0 8 [>4] | 8 8 8 25 33 [3] | 0 0 0 0 25 [4] |
| mass_step | 0 0 0 67 100 [3] | 8 25 42 92 100 [1] | 0 0 0 0 0 [>4] |
| swing_load | 0 0 0 17 75 [4] | 8 25 25 92 100 [1] | 0 0 0 0 0 [>4] |
| arm_load | 0 0 0 25 100 [3] | 8 25 83 100 100 [1] | 0 0 50 100 100 [2] |
| motor_loss | all 0 [>4] | 8 8 8 17 83 [4] | 0 0 0 42 83 [3] |
| kt_mismatch | 0 0 0 50 100 [3] | 8 25 8 75 100 [1] | 0 0 0 0 8 [>4] |
| actuator | 0 0 92 100 100 [2] | 8 42 100 100 100 [1] | 0 0 100 100 100 [2] |
| inertia | all 0 | 8 0 0 0 0 | all 0 |
| combo | 0 75 100 100 100 [1] | 8 100 ... [1] | 0 100 ... [1] |
| speed | 0 0 33 100 100 [2] | 0 0 67 100 100 [2] | 0 0 67 100 100 [2] |

Findings:
1. The oracle was worth little to PID (34.6 -> 33.1 %, within one or two rows per cell). The sec K.1 PID numbers are
   a fair baseline after all.
2. MRAC5 keeps its load advantage without the oracle: mass pickup and swinging load diverge 0 % at every level (PID
   67 / 17 % at L3), kT mismatch 0 % to L3 (PID 50 %). It loses where PID's fixed gains are stiffer: the arm load
   (MRAC5_nom 50 % at 0.2 kg on the arm-tip cable, PID 0 %) and motor loss L3 (42 % vs 0 %). Its p90 RMSE on
   nominal rows is 0.082 m vs PID 0.086 m.
3. mrac_sataware_nom is under-tuned (J 0.128, 1 of 12 nominal rows diverged, p90 0.170 m); 128 evals from diverging
   defaults were not enough. Its row says nothing about the architecture.

**Combo L1 interaction** (`combo_ablate.py 1`, `results/combo_ablate_L1.json`): every subset of the four L1 ingredients
on the same 12 rows (W wind 2 m/s + gusts, A 0.1 kg on a 0.1 m cable at motor 0's arm tip, K kT +-10 %, D motor
tau 1.5x and 4-tick delay), diverged rows of 12, pid_tuned2 / mrac_sataware / mrac5_xyz:

| subsets | diverged |
|---|---|
| W, A, K, D alone; WA, WK, WD, AK, KD, WAK, WKD | 0 / 0 / 0 for every one |
| AD | 10 / 12 / 12 |
| WAD, AKD, WAKD | 10-11 / 10-11 / 9-12 |

So the whole combo effect is one pair: the arm-tip load and the slower actuator. `combo_ad_diag.py` splits it
(pid_tuned2 / mrac5_xyz, diverged of 12): AD on the cable 9 / 11 (median divergence at 10.7 / 11.8 s, hover included);
the same 0.1 kg rigidly mounted 0 / 0; the cable load at the body centre 0 / 0; tau 1.5x alone with the arm load 0 / 1;
delay 4 alone with it 0 / 0; 0.05 kg on the arm-tip cable 0 / 0. It is a slow-growing oscillation (about 11 s to
diverge): the arm-tip pendulum (0.1 m cable, about 1.6 Hz, computed) couples into roll / pitch, and the extra lag
removes the phase margin that held it. Caveat: the sim damps the swing only by air drag (`LOAD_DRAG` 0.02 N/(m/s),
about 1 % of critical at 0.1 kg, computed), so a real string with knot friction is likely better damped; this is a
worst case.

What it means for 10-06:
- Mount the arm load rigidly (tape or a bolt to the arm), not hanging on a string, unless the swinging load is the
  test itself. Rigid 0.1 kg + the slower actuator: 0 of 12 diverged; hanging: 9-11 of 12.
- If it hangs, watch for a roll / pitch oscillation that grows over about 10 s and land early. That is this mode.
- The motor lag is now the deciding unknown for every controller (K.2, and here). Measure it from the 10-03 / 10-06
  step logs before any gain change.

### K.6 Motor lag from the flight logs: not measurable yet, and the RPM sensors drop out in flight

The actuator ladder's L0 is the bench's motor model, a 3-tick (15 ms) dead time plus a first-order lag of 1/19.8 s
(about 50 ms, `sim/bench/plant.py:36`). Neither number has a recorded measurement, and K.5 found actuator lag is half of
the one combination that breaks every controller. So I tried to measure it from the slot-2 logs, which carry the motor
command (`mymotor.motor1..4`) and the raw per-revolution RPM period (`rpm_dbg_period_cyc[i]`, `rpm_dbg_edges[i]`) in the
same 50 Hz frame. Tool: `ground_station/analysis/motor_lag.py` (instrumental-variable ARX fit; unit test recovers a
known 50 ms lag under RPM noise). Measured 2026-10-05:

| Check | Result |
|---|---|
| 10-03 logs (drift_fix_1, landing_x_drift_1, roaming-return) | all four RPM channels freeze **together** while `mymotor` keeps updating: channels agree on fresh/frozen in 99 % of frames; fresh edges in 48 % / 30 % / 0 % of airborne frames; longest frozen run 1738 and 4609 frames (35 s, 92 s) |
| Logs with live RPM (roaming_and_landing_1, hover_3, hover_active_1) | hover RPM about 5100 (ch1, ch3) and 6050-6100 (ch2, ch4; ch4 only where live) at mean commands within 10 % of each other |
| ARX fit (roaming_and_landing_1/3, hover_3) | tau 47-760 ms depending on motor, dead time (0-3 frames) and estimator (OLS vs IV): no consistent answer |
| Cross-spectrum command -> RPM, 0.4-8 Hz | coherence mostly < 0.5; at 3.1 Hz ch3 has RPM *leading* the command by 92-108 deg in 4 flights (roaming 1/2/3, hover_3; coherence 0.40-0.67), which a causal lag cannot produce |
| Band-passed (0.2-2 Hz) correlation, command i vs RPM j | no diagonal: the largest entries (0.42, 0.44) are command 3 vs RPM channel 1 |

Reading: the existing logs cannot give the motor lag. A 50 Hz snapshot of a 200 Hz command aliases the command noise, and
the RPM-leads-command phase says the closed loop (RPM -> attitude -> gyro -> command) dominates the correlation (both
likely, not proven). Two findings come out of it anyway:
- **RPM dropout.** All four channels stop counting edges at the same moment, for tens of seconds, while the rest of
  the frame is live. One shared cause, either the four EXTI interrupts stop or the shared sensor connector loses
  contact in flight. Logs can't tell which. Any RPM-based k_T or thrust estimate from a 10-03 flight is built on frozen
  values.
- **Channel mapping is unconfirmed.** ch2/ch4 read about 19 % more RPM than ch1/ch3 at similar commands, and the
  correlation matrix doesn't pick out motor i <-> channel i. That's a prop/motor difference, a sensor-mark
  difference, or a swapped mapping; it needs a one-motor-at-a-time check.
- **Operator report (2026-10-05, after this analysis):** one RPM sensor is not mounted well and will be fixed on
  10-06. Until then RPM data from every existing log is ignored (no k_T, thrust or lag estimate from it). Re-run the
  steps below after the fix.

For 10-06 (operator steps; agents never spin motors):
1. Before flight, with props off and motors spun by the operator one at a time: confirm `rpm_dbg_edges[i]` counts on
   channel i only (fixes the mapping) and keeps counting through a 30 s run (connector check).
2. During the first hover, watch `rpm_dbg_edges` on the dashboard. If all four stop together, note the time: that's
   the dropout above.
3. To measure the lag: one 30 s hover with slot 2 at 100 Hz (`mymotor` + `rpm_dbg_*`), a few small roll/pitch stick
   doublets, then `python -m ground_station.analysis.motor_lag <log prefix>`. Until then the actuator L0 stays the
   unmeasured 1/19.8 s + 15 ms, and K.5's advice holds: rigid arm mount, watch for a slowly growing roll/pitch oscillation.

## L. Coupled RBF and 2-layer NN features on MRAC5_XYZ's x/y layers (`sim/bench/ctrl_nn2.py`, item 7)

**Question.** MRAC5_XYZ adapts x and y separately on [1, v_i, v_i|v_i|]. A load on one arm tilts both axes, and
drag can depend on the other axis's speed. Does a richer basis that couples x and y learn that, and so hold where
MRAC5 breaks (arm load, motor loss)?

**Design.** Both keep MRAC5_XYZ's z layer, reference model (pole from the wrapped velocity loop), normalised update and
box projection |th| <= tan(TILT_MAX). Both add e-modification (weights leak in proportion to |e|, rate kappa). The plan
said "+PR"; it was read as projection, so both use projection and e-modification together.
- **RBF2_XYZ**: [1, v_i, v_i|v_i|] plus 9 Gaussians on a 3x3 grid over (v_x, v_y)/VXY_MAX, width rbf_w. Linear in the
  weights. With no Gaussians and kappa = 0 it reproduces MRAC5_XYZ to < 1e-4 m (unit test).
- **NN2_XYZ**: one hidden layer, 8 tanh units on [1, v_x, v_y, vd_x, vd_y]/VXY_MAX, outputs x and y. Outer weights by
  the same normalised law; inner weights by the back-propagated term, the two-layer NN tuning law of Lewis, Yesildirek
  and Liu (IEEE TNN 1996, cited from memory, unverified). Inner weights start at a fixed random draw (seed 0), leak back
  toward it, and are boxed to +-3 around it.

**Protocol.** Same as K.5: `tune_nominal.py` (2 x 64 CMA evals on nominal rows only, from class defaults), then the same
459-row ladder. Measured 2026-10-05 21:xx.

| Controller | tune J (nominal) | ladder div % | nominal-row (L0) p90 RMSE [m] |
|---|---|---|---|
| pid_nom (K.5) | 0.0775 | 33.1 | 0.086 |
| mrac5_xyz_nom (K.5) | 0.0687 | 30.7 | 0.082 |
| rbf2_xyz_nom | 0.0798 | 37.7 | 0.189 |
| nn2_xyz_nom | 0.1215 | 55.1 | 0.308 |

| axis, div % L0..L4 [break] | mrac5_xyz_nom | rbf2_xyz_nom | nn2_xyz_nom |
|---|---|---|---|
| wind_gust | 0 0 0 0 25 [4] | 8 0 17 17 25 [4] | 0 33 8 33 33 [1] |
| mass_step | 0 0 0 0 0 [>4] | 8 17 8 17 75 [4] | 0 33 42 92 100 [1] |
| swing_load | 0 0 0 0 0 [>4] | 8 17 0 17 17 [>4] | 0 25 50 92 100 [1] |
| arm_load | 0 0 50 100 100 [2] | 8 0 50 100 100 [2] | 0 17 92 100 100 [2] |
| motor_loss | 0 0 0 42 83 [3] | 8 0 0 33 67 [3] | 0 8 17 58 75 [3] |
| kt_mismatch | 0 0 0 0 8 [>4] | 8 0 0 0 67 [4] | 0 8 33 67 100 [2] |
| actuator | 0 0 100 100 100 [2] | 8 17 100 100 100 [2] | 0 25 100 100 100 [1] |
| inertia | all 0 | 8 0 0 0 0 | all 0 |
| combo | 0 100 ... [1] | 8 100 ... [1] | 0 92 100 100 100 [1] |
| speed | 0 0 67 100 100 [2] | 0 0 100 100 100 [2] | 0 0 100 100 100 [2] |

(L0 is the nominal row of every axis, so RBF2's "8" there is the same 1 of 12 nominal rows diverging each time.)

Findings:
1. **Neither richer basis helps.** RBF2 is worse than MRAC5 overall (37.7 vs 30.7 %) and loses MRAC5's clean load
   record (mass_step and swing_load go from 0 % at every level to 17 % at L3). The arm load, the case the coupling was
   meant for, breaks at the same level (L2, 50 %). Motor loss improves by 1 and 2 rows of 12 at L3 and L4 (33 / 67 vs 42 / 83 %).
   NN2 is the worst controller measured on the ladder (55.1 %), breaking at L1 on wind, mass and swing.
2. **The failures are not missing features.** The cells that break everyone (actuator L2, combo L1, speed L2, arm-tip
   cable L2) are phase or lag problems (K.3, K.5). A velocity-loop add-on of any shape cannot fix them, and a richer one
   adds its own lag and drift. Nominal p90 RMSE rises with basis size: 0.082 -> 0.189 -> 0.308 m.
3. **Tuning caveat.** Same 128-eval budget as K.5, and RBF2 and NN2 have 2 more parameters each. RBF2's J (0.0798) did
   not reach MRAC5's (0.0687), although the search space nearly contains MRAC5, so RBF2 may be under-tuned. This cannot
   rescue NN2 (J 0.1215), and it would not change finding 2.

**Verdict.** Drop richer x/y bases from the roadmap. MRAC5_XYZ's 3-term basis stays the adaptive candidate. The next
gains are in the inner loop and in the actuator: items F (PID tune), G (PID-specific MRAC) and H (no-tuning
architecture), and measuring motor lag after the 10-06 RPM sensor fix (K.6).
