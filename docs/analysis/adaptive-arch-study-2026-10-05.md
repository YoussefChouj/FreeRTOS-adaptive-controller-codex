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

Caveats: rigid load (a hung load's swing is not modelled); H_PAD 0.10 m is PROPOSED; the arm CoG shift (up to 56 mm at
500 g) lies outside the cog range the controllers were tuned on; batch noise moves a case's median by 10-20 % between
runs; F1x covers only the angle and rate integrator rows (not locx F3 or the z rows); the measured u_imb comes from 2
flights; the bench hover calibration X2_H assumes U_IMB_NOM 430.
