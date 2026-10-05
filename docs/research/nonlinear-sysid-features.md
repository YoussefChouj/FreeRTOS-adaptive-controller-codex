# Nonlinear system identification for MRAC feature discovery

Goal: find, from flight data, the physical terms that were actually active in a flight, and add them to the
library of candidate features the MRAC adaptation layer can use. There are two kinds of feature:

| Kind | What it is | Example |
|---|---|---|
| Structured | a physics term from a known equation of motion | thrust times the CoM offset, rotor drag linear in body velocity |
| Unstructured | a generic basis that needs no physics model | spectral peak of the residual, band-split signal, RBF grid |

Two scenarios are targeted: symmetric and asymmetric payloads, and dense waypoint following. The lab flies them
combined (`ground_station/service/campaigns/payload_waypoints.yaml`).

Status, 2026-10-05: methods and physics surveyed (this doc). The sim toolkit exists in `sim/bench/sysid/`.
The real-log path, `hscale.run_real_protocol`, is a stub that only counts files. The steps are listed at the end.

## 1. Pipeline

```
flight log (50 Hz)          bench sim (ground truth known)
      |                               |
      v                               v
 target = what the nominal model cannot explain (residual acceleration, per axis)
      |
      v
 candidate library  = structured physics terms (sec. 3)  +  unstructured terms (sec. 4)
      |
      v
 sparse regression  = STLSQ (SINDy) + bootstrap ensemble + FROLS/ERR ranking
      |
      v
 per-flight report: which terms are active, their coefficient, the share of residual variance each one explains
      |
      v
 compare load cases: noload vs sym vs asym -> the terms the load switched on
      |
      v
 shortlist for the MRAC phi vector -> bench A/B (sim) -> firmware variant on a branch -> lab A/B
```

The **target** is the residual, not the raw acceleration. The nominal model (motor map, inertia, hover thrust)
already explains most of the motion; regressing raw acceleration would mostly rediscover it. The residual is what
an adaptive term has to cancel. It is the same quantity MRAC's `u_ad` estimates online.

## 2. Methods

| Method | Idea | Why it fits here | Reference |
|---|---|---|---|
| SINDy | sparse least squares (STLSQ) over a library of candidate terms | the result is a short list of named terms, which maps one-to-one onto MRAC features | Brunton et al. 2016, arXiv:1509.03580 |
| SINDYc | SINDy with control inputs in the library | the motor commands are known inputs | Kaiser et al. 2018, arXiv:1711.05501; tutorial Fasel et al. 2021, arXiv:2108.13404 |
| Ensemble SINDy (E-SINDy) | bootstrap the data and the library; report how often each term is selected | gives an inclusion probability per term: "dominant" becomes a number, not a judgement | Fasel et al. 2022, arXiv:2111.10992 |
| Weak SINDy | fit the integral form of the equation against test functions | no numerical differentiation; our angular acceleration would come from differentiating a 50 Hz gyro log, which amplifies noise | Messenger & Bortz 2021, arXiv:2005.04339 |
| SINDy-PI | implicit form, for rational terms | needed only if a term divides by a state (mass change appears as 1/m) | Kaheman et al. 2020, arXiv:2004.02322 |
| FROLS / ERR | forward orthogonal least squares; each term's error reduction ratio = share of output variance it explains | gives a ranking and a "% explained" per term, which is the per-flight dominant-feature report | Billings 2013, *Nonlinear System Identification: NARMAX Methods* (Wiley); book, not on arXiv |
| Meta-learned basis (Neural-Fly) | learn a basis phi(x) across many conditions offline, adapt only its linear coefficients online | the same structure as MRAC: u_ad = Theta^T phi(x); an unstructured route when physics terms run out | O'Connell et al. 2022, arXiv:2205.06908 |
| Learned residual (Neural Lander) | DNN for the ground-effect residual, inside a stable controller | precedent for a learned residual term in the loop | Shi et al. 2019, arXiv:1811.08027 |

Quadrotor-specific SINDy precedents: SINDy recovery of quadrotor equations of motion (Manaa et al. 2023,
arXiv:2305.16500); SINDy models inside MPC for a multirotor (Lee et al. 2024, arXiv:2412.06388).

**Recommended combination.** STLSQ for the fit, E-SINDy for the inclusion probability, FROLS/ERR for the ranking.
Use weak-form targets when the angular-acceleration residual is too noisy. A term is called *dominant* in a flight
when both hold: inclusion probability >= 0.8, and it is in the ERR top k. Both thresholds are PROPOSED and need
tuning on the sim recovery test.

## 3. Structured physics features per scenario

The base model is the rigid body the bench plant (`sim/bench/plant.py`) integrates:

```
m a   = T R e3 - m g e3 - D_lin (v - v_wind)                 translational, world frame
J w'  = tau - D_rot w - w x (J w)                            rotational, body frame
T_i   = k(V_bat) * f(motor_i lagged by tau_m) * GE(z)        per-rotor thrust
```

Each scenario below changes this model. The change, moved to the right-hand side, is the residual term a feature
must represent. These are derivations from the model above, not fitted results.

### 3.1 Symmetric load (payload on the CoM)

| Change | Residual term | Candidate feature | Axis |
|---|---|---|---|
| mass m -> m + dm | dz_acc = T (1/(m+dm) - 1/m), about -T dm/m^2 | `thrust` (collective), `bias` | z |
| inertia J -> J + dJ | the control gain per U drops: dw' = -(dJ/J) w'_nom | `u` (scaled command), `u_lag` | roll, pitch, yaw |
| more hover thrust | the motors run at a higher point on the thrust curve: the local gain changes | `thrust * u` | roll, pitch |

The mass effect is mainly a constant in hover and a thrust-proportional term in manoeuvres. MRAC's `bias` slot
absorbs it in hover; `thrust` separates it when the collective moves (waypoint turns, climbs).

### 3.2 Asymmetric load (CoM offset r_c = (r_x, r_y))

| Change | Residual term | Candidate feature | Axis |
|---|---|---|---|
| thrust acts off the CoM | tau = r_c x (T e3): roll -r_y T, pitch +r_x T (the bench plant's `sp['cog']` convention) | `thrust` per axis | roll, pitch |
| products of inertia | J is no longer diagonal: w x (J w) gains cross terms | `w_i * w_j` | all |
| control cross-coupling | a roll command also produces pitch (J off-diagonal) | cross-axis `u` | roll <-> pitch |
| translational coupling | the IMU / CoM point accelerates by w' x r_c + w x (w x r_c) | `w'_i`, `w_i * w_j` | x, y |

The asymmetric load's signature is **thrust appearing on the roll or pitch residual**. In hover it is a constant
torque that the PID integrator holds; during waypoint turns the collective changes and the term separates from
a plain bias. This is the clearest discriminator between symmetric and asymmetric loads.

### 3.3 Slung load (only if the payload hangs)

A payload on a cable is a pendulum. Its natural frequency is f = (1/2pi) sqrt(g/L): for L = 0.3 m that is
0.91 Hz (computed). It appears as a narrow peak in the horizontal residual. Payload-swing work: Taki & Umemoto
2026, arXiv:2608.18625; sensorless cable-suspended payload: Nascimento et al. 2026, arXiv:2605.03666.
Feature: a quadrature pair sin/cos at the identified peak, or a band-pass of the residual around it.

### 3.4 Dense waypoint following

| Effect | Residual term | Candidate feature | Axis | Source |
|---|---|---|---|---|
| rotor drag | force linear in body-frame velocity | `v_bx`, `v_by`, `v_bz` | x, y, z | Faessler et al. 2018, arXiv:1712.02402 |
| thrust change with speed | c = c_cmd + k_h v_h^2, v_h = v^T (x_B + y_B) (their eq. 5, adopted from an earlier thrust model) | `v_h^2` | z | Faessler et al. 2018 |
| parasitic drag | quadratic in airspeed | `v * abs(v)` | x, y | standard body-drag model |
| motor lag | the response trails the command by tau_m | `u_lag` (first-order filtered u) | all | bench plant tau_m = 1/19.8 s |
| battery sag | the thrust gain falls as V_bat^2 | `vbat`, `vbat * thrust` | z | bench plant gain model |
| ground effect | T_IGE/T_OGE = 1/(1 - (R/4z)^2) | `ge_term` | z | Cheeseman & Bennett 1955 (NACA report, not on arXiv) |
| high-speed aerodynamics | blade-element effects beyond linear drag | learned residual | all | NeuroBEM, Bauersfeld et al. 2021, arXiv:2106.08015 |

The speeds in the demo campaign are low (0.2 and 0.3 m/s). Linear rotor drag and motor lag are therefore the
likely dominant terms; quadratic drag should be at the noise floor. Dense waypoints mainly add frequent
accelerations and attitude changes. Those excite the `u`, `u_lag` and `thrust` terms, which is what makes
the load terms identifiable at all.

### 3.5 Combined scenario (the lab plan)

The load terms (3.1, 3.2) and the waypoint terms (3.4) are additive in the residual, so one library covers
both. The identification problem is separating `bias` from `thrust` and `v_b`. That needs flights where the
collective and the velocity change, which is exactly what dense waypoints provide. Hover-only segments cannot
separate them.

Related adaptive-control work with payloads: adaptive NMPC (Hanover et al. 2021, arXiv:2109.04210); a
comparison of adaptive controllers under unknown payloads (Sankaranarayanan et al. 2021, arXiv:2109.00342).

## 4. Unstructured (frequency) features

| Feature | Construction | Notes |
|---|---|---|
| band split | residual split into L/M/H bands (`sim/bench/sysid/bands.py`, cuts 0.5 and 4 Hz) | the existing toolkit; the FFT split is non-causal, so it is analysis only: a firmware version needs causal IIR band-passes |
| spectral peaks | Welch PSD of the residual; peaks above the noise floor | finds pendulum swing, frame resonance and limit cycles without a model |
| quadrature pair | sin(2 pi f t), cos(2 pi f t) at an identified peak | the linear coefficients set amplitude and phase; MRAC can adapt them |
| RBF grid | Gaussian bumps over (state, rate) | the existing V3 variant `STRUCT6_RBF12` (`API/mrac_variant.h`) |
| meta-learned basis | Neural-Fly style, trained offline on many flights | needs more data than one lab session |

## 5. Mapping to the MRAC phi vector

The firmware's STRUCT6 phi per rate axis (`API/mrac.c:265`) is `[1, x, x*tanh(x), cross, u_nom, xm]`.

| Discovered term | Existing slot | New slot needed | Onboard signal |
|---|---|---|---|
| `bias` | `1` | no | — |
| `u`, `u_lag` | `u_nom` (no lag) | `u_lag` | the U command, filtered |
| `w_i * w_j` | `cross` | no | gyro |
| `thrust` on roll/pitch (asymmetric load) | none | yes | the collective command |
| `thrust` on z (mass) | none on the z-rate axis | yes | the collective command |
| `v_b` (rotor drag) | none | yes | the OF EKF velocity |
| frequency pair | none | yes | a phase accumulator |

**Result of the mapping:** the asymmetric-load and drag terms have no slot today. `thrust` is the cheapest
candidate: one multiply per axis, and the signal already exists in the loop. Adding a slot changes the phi
length (`API/mrac_variant.h`), so it is a firmware variant on a branch, with a bench A/B first.

## 6. Data

### 6.1 Own logs

What the demo flights record (`ground_station/livewatch/campaign_capture.py`): the needed set at 50 Hz
(attitude, gyro FB/Des, Z rate FB/Des, the four motor commands, position FB/Des, status), plus the
`velocity_loops` group (velocity FB/Des, loc U).

| Need | Available | Gap |
|---|---|---|
| angular rate | gyro FB | — |
| angular acceleration | differentiate gyro FB at 50 Hz: noisy | use weak SINDy (no differentiation) |
| thrust per rotor | motor commands | thrust curve and battery gain are model-based |
| translational acceleration | differentiate velocity FB twice: noisy | weak form, or log the accelerometer |
| body velocity | velocity FB + attitude | — |
| battery voltage | not in the needed set | vbat cannot be a feature on these flights |
| MRAC internals | `mrac_shadow` group (optional) | not in the demo log plan |

50 Hz gives a 25 Hz Nyquist. The motor lag corner is 3.2 Hz (tau_m = 1/19.8 s), so the slow physics (bias,
thrust, drag, lag) is in band. Fast rotor dynamics and frame resonances are not.

**Proposed for the lab (PROPOSED, the demo campaign stays as it is):** a separate campaign file for
identification flights. It adds `mrac_shadow` and the accelerometer if the link budget allows. Its rate and
groups have to be checked against the 50 Hz loss-free budget before use.

### 6.2 Public datasets

| Dataset | Contents (per the paper) | Use here |
|---|---|---|
| NeuroBEM (arXiv:2106.08015) | high-speed agile flights, motor speeds, motion capture | which drag and aerodynamic terms dominate at high speed |
| Blackbird (arXiv:1810.01987) | indoor aggressive flights with motor speeds and motion capture | waypoint-like trajectories |
| Neural-Fly (arXiv:2205.06908) | flights under different wind conditions | a wind-invariant basis |
| PX4 Flight Review logs | public PX4 logs | many airframes; quality and metadata unverified |

Public data transfers the *form* of a term (which features matter), not its coefficient: the airframe, the mass and
the motors differ.

## 7. Protocol per flight

1. Segment by experiment phase (hover / lawnmower / zigzag) and by load case.
2. Compute the residual per axis from the nominal model.
3. Build the library: the structured terms of sec. 3 plus the band and peak terms of sec. 4.
4. Fit with STLSQ (`sim/bench/sysid/sindy.py`, `cv_threshold` picks the threshold), bootstrap the inclusion
   probability, rank with ERR.
5. Report per flight: term, coefficient, inclusion probability, ERR %, the % of residual variance explained.
6. Compare the load cases: the terms that turn on in sym or asym and not in noload are the load features.

**Sim before lab:** the bench plant already injects every scenario term with a known value (payload `mass`,
CoM offset `cog`, linear and rotational drag, ground effect, battery sag, wind). A recovery test runs dense
waypoints with each injection and requires the fit to recover it. Pass criteria: the injected
term is selected with inclusion >= 0.8, its coefficient within 20 % of the truth, no spurious term above 5 %
ERR. `sim/bench/sysid/test_loads.py` applies them (not in the gate, run `python -m pytest sim/bench/sysid`).

### 7.1 Bench results (measured 2026-10-05)

`sim/bench/sysid/loads.py` builds the residuals (translational `a - (T b3/m - g)`, rotational
`w_dot - tau/J`) from position, attitude and motor PWM only, which is what the demo logs carry, and ranks
each axis with FROLS + bootstrap. One FwPID run, V0 16.6 V, no sag, 200 Hz, 4 s start transient dropped:

| Row | Term | Recovered | Truth |
|---|---|---|---|
| zigzag 1 m/s, nominal | x / y / z drag `v` | -0.1928 / -0.1928 / -0.1929 | -0.1929 |
| | roll `gyro`, `w` | +0.2943, -0.1099 | +0.2929, -0.1099 |
| | pitch `gyro`, `w` | -0.1676, -0.0985 | -0.1660, -0.0991 |
| steps, payload m x1.10, J x1.05 | x / y / z `thrust` | -0.0907 / -0.0908 / -0.0907 | -0.0909 |
| | roll / pitch `tau_nom` | -0.0472 / -0.0472 | -0.0476 |
| steps, CoM offset (-14.9, -12.9) mm | roll / pitch `thrust` | +0.0129 / -0.0149 | +0.0129 / -0.0149 |

Every non-physical term had inclusion 0.00. What it took, and what carries over to real logs:

1. **Align the thrust with the second difference.** The acceleration from a second difference of
   position sees the in-step force through a triangular kernel over steps k and k+1. A plain two-step
   mean of the thrust is half a sub-step off it, and the attitude loop turns that lag into a fake
   damping term: roll `w` came out -0.44 (truth -0.11), and nominal z drag -0.43 (truth -0.19).
   `motor_thrust` integrates the motor lag on sub-steps and applies the kernel. On real logs, use the
   log rate's own kernel, or differentiate a smoothed spline and keep the same filter on both sides.
2. **Ground effect biases the thrust term below about 0.5 m.** The bench uses
   `1/(1-(R/4z)^2)` (Cheeseman-Bennett): +2.6 % thrust at z = 0.10 m, 0.10 % at 0.5 m (computed). A payload
   row that sagged to about 0.1 m recovered `thrust` -0.179 for a truth of -0.184. Drop the take-off and landing segments, or add
   `thrust (R/4z)^2` to the library.
3. **The bench altitude loop sags under payload** (FwPID, mean z over 4-11 s on the steps reference):

   | Payload k | 1.00 | 1.10 | 1.15 | 1.20 | 1.25 |
   |---|---|---|---|---|---|
   | z (m), V0 16.6 V | 1.02 | 0.80 | 0.61 | 0.43 | 0.32 |
   | z (m), V0 16.0 V | 0.87 | 0.52 | 0.37 | 0.24 | 0.14 |

   None of these runs diverged. A heavy payload on a low battery therefore puts the bench drone into
   ground effect (point 2). The test uses k = 1.10 at 16.6 V.
4. **Thrust variation separates the loads.** At a steady hover a CoM offset looks the same as a constant
   torque bias, and a mass change looks the same as a z-force bias. The z steps (1.0 -> 1.3 m) move
   the thrust enough to tell them apart. The identification campaign needs altitude steps, not only
   level waypoints.

### 7.2 Real logs (measured 2026-10-05)

`sim/bench/sysid/reallog.py` reads the wide per-slot CSVs (`logs/vofa/<stem>.slotN.csv`, device clock,
20/40 ms slots) into the same residuals: position rotated to the plant frame (`fw_x = -plant_y`,
`fw_y = plant_x`), pitch and pitch rate negated (`PITCH_SIGN`; on f17 the fw_y acceleration against
sin(pitch) T/m has gain -0.59, R2 0.46), the gyro as w, the mean balance reported apart by `trim()` and the
target and candidates centred. `python -m sim.bench.sysid.reallog <stem>` prints both.

**Round trip** (`test_reallog.py`: bench flight written as firmware-format CSVs, read back):

| Row | Readout | 20 ms snapshots | 5 ms log | Truth |
|---|---|---|---|---|
| payload | thrust ratio | 1.1022 | 1.1008 | 1.10 |
| CoM offset | cog x / y (mm) | -14.3 / -12.1 | -14.7 / -12.8 | -14.9 / -12.9 |
| CoM offset | yaw torque (N m, plant units) | -0.894 | -0.897 | -0.896 |
| payload | x / y `thrust` | -0.095 / -0.095 | | -0.091 |

Negative probes: no frame rotation, or the reader's pitch sign dropped, fail the translational check.

**Rotational terms need more than 50 Hz snapshots.** Same bench rows, ranked at 2 Hz cutoff:

| Motor log | CoM row roll / pitch `thrust` | roll `tau_nom` |
|---|---|---|
| 5 ms (every control tick) | +0.0129 / -0.0149 (truth) | +0.000 (truth 0) |
| 10 ms snapshots | +0.0095 / -0.0111 | -0.467 |
| 20 ms snapshots | lost | -0.916 |

A snapshot of a 200 Hz motor command aliases the torque, and `tau_nom` near -1 says the
measured w_dot does not follow it. f17_hover_shadow2 shows the same signature: roll / pitch `tau_nom`
-0.89 / -0.86 (seg 0, 36.7 s) and -0.78 / -0.86 (seg 1, 16.8 s). `calib_v1` ARX gave 0.03-0.25 dps2/U
against the replay-calibrated 8, which fits the same cause. 100 Hz alone does not fix it. What would
(PROPOSED, post-demo branch): log the motor or rate-loop U averaged over the slot, then rate dither for
excitation.

**f17 trims** (seg 0 / seg 1): thrust ratio 0.804 / 0.811 (the nominal thrust map and vbat give 20 %
more thrust than MASS g needs: a lighter airframe or a different battery), CoM offset x +1.2 / +1.0 mm,
y +6.5 / +5.0 mm, yaw torque +312 / +386 mN m (sign not checked against the plant). A CoM offset from
one flight mixes motor mismatch and frame asymmetry, so compare a load flight with a baseline flight.

## 8. Roadmap

| Step | Status |
|---|---|
| survey (this doc) | done |
| FROLS/ERR ranking next to STLSQ | done (`frols.py`) |
| translational residual target, load/drag features | done (`loads.py`); frequency features in `bands.py` |
| sim recovery test (sym, asym, drag, dense waypoints) | done (`test_loads.py`, sec. 7.1) |
| real-log path | done (`reallog.py`, `test_reallog.py`, sec. 7.2); rotational needs slot-averaged motor logging |
| identification campaign file and log plan | planned, operator decides |
