# Adaptive architecture study (2026-10-05)

Status: PROPOSED. Numbers marked "measured" were computed in this study from logs or bench runs; settings are proposals
for post-demo branches, nothing here is flashed or merged into firmware behaviour.

| Item | Question | Status |
|---|---|---|
| A | Equal-budget tune of PR (Yucelen) and RBF layers against PID on payload / cog / zigzag | partial, measured median test rmse: mrac_pr 0.0903, mrac_physrbf 0.1231 vs pid_tuned2 0.0775; rbf48 running |
| B | Per-axis reference model from flight replay (f14, f16) | measured, below |
| C | Public data (NeuroBEM, Neural-Fly) | files and sizes listed below; download waits for the operator's yes |
| D | x/y adaptation: 6 decoupled layers plus coupling terms | planned |
| E | Fully coupled 6-axis MRAC: RAM budget, firmware copy on a branch | planned |
| F | Systematic cascaded-PID tuning | planned |
| G | MRAC derived for (augmenting) the PID loop | planned |
| H | Thesis three-layer design: physics features, frequency gating, task priors | planned; design constraint below |
| I | Position estimate from optical flow + IMU: roam-and-return bias, state of the art | measured, below |

Design constraint for H (operator, 2026-10-05): the three-layer design must need no parameter tuning, by its
architecture. Every gain has to come from a measured or derived quantity: physics features from the plant model and
sysID, frequency gates from measured loop bandwidths (item B), task priors from the campaign definition, and adaptation
rates normalised and bounded by physical limits. The equal-budget bench tune in A is a benchmark against PID, not a
deployment step. Item I applies the same rule to the estimator (scale and bias measured in flight, not tuned).

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
