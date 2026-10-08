# Adaptive-layer stats: `pid_ref_ARM_LOAD_293g`

Measured from the log. Basis rebuilt from logged signals (see the tool docstring). Shaded: blue PID, orange MRAC injected.

## Per MRAC segment and axis

| seg | axis | rebuild r | u_ad RMS | u_nom RMS | ratio | r(u_ad,u_nom) | r(u_ad,x) | band phase u_ad/x (deg) | band phase u_nom/x | Re H u_ad / Re H u_nom | coh | top feature (RMS share) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|

Phase of a torque against the body rate x in 0.25-0.90 Hz: +/-180 = pure damping, 0 = anti-damping, +/-90 = no energy. Re H u_ad / Re H u_nom > 0: u_ad adds damping like the PID; < 0: removes it.

## What-if: same weights, other omega_u (open-loop replay of the logged raw sum)

Band phase u_ad/x (deg) / Re H ratio, as above. Closed loop would differ (the weights would evolve differently); this isolates the filter lag.

| seg | axis | flown | 10 rad/s | 15 rad/s | 20 rad/s |
|---|---|---|---|---|---|

## Weights Theta (start -> end of each MRAC segment)

| seg | axis | Theta[0] bias 1 | Theta[1] x | Theta[2] x tanh x | Theta[3] cross | Theta[4] u_nom | Theta[5] xm | |Theta| growth |
|---|---|---|---|---|---|---|---|---|

## Ext block (vp basis 7, 24 Gaussians per axis, pitch/roll)

Grid inputs in the firmware's normalised units (rate, angle); the Gaussian centres span -1..1 (basis 5: -1.5..1.5 rate, -1..1 angle). Activity = sd / mean of the most varying Gaussian over the segment: near 0 the grid acts as one constant (a bias), not as a function of the state. Ext share = ext RMS^2 / (ext RMS^2 + S6-part RMS^2). Slope = d|Theta_ext|/dt over the last third (> 0: still learning).

| seg | axis | rate p1 / p50 / p99 | angle p1 / p50 / p99 | activity | ext RMS | S6 part RMS | ext share | |Theta_ext| start -> end | slope (/s) | top ext (RMS share) |
|---|---|---|---|---|---|---|---|---|---|---|

### Ext ranking (every Gaussian, RMS^2 share of the ext block, cumulative in brackets)

| seg | axis | ranked |
|---|---|---|

## Static offsets per segment (drift)

Expected static torque of the load at the rope point: pitch 0.057 N m (x 2 cm), roll 0.201 N m (y 7 cm), mg = 2.87 N.

| seg | mean u_nom p / r | mean u_ad p / r | mean Theta[0] p / r | pitch Des - FB (deg) | roll Des - FB | locx Des-FB (cm) | locy Des-FB | locx/y PID U mean | x FB slope (cm/s) | y FB slope | x stick on (%) | x stick vel Des median (cm/s) | x vel FB mean, no stick (cm/s) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| F1 PID | -0.0812 / -0.0336 | +0.0000 / +0.0000 | +0.0000 / +0.0000 | -0.04 | +0.03 | +0.8 | -8.0 | +1.5 / -10.2 | +0.62 | +3.19 | 23 | +45 | +0.08 |
| F2 PID | -0.0835 / -0.0349 | +0.0000 / +0.0000 | +0.0000 / +0.0000 | +0.15 | -0.18 | -0.1 | +1.8 | +1.1 / -2.5 | -0.36 | +1.49 | 0 | +nan | +1.13 |

## Per segment: attitude, height, motors, battery

| seg | s | pitch sd | roll sd | rate sd p / r (deg/s) | band PSD p / r | z - z_des | motor at 4000 (%) | max motor spread | V mean | stab CPU % mean / max |
|---|---|---|---|---|---|---|---|---|---|---|
| F1 PID | 24 | 2.96 | 4.27 | 11.1 / 14.1 | 58.6 / 86.9 | +0.007 | 0.2 | 1818 | 14.79 | 8.6 / 8.7 |
| F2 PID | 10 | 1.63 | 1.66 | 7.8 / 6.9 | 13.9 / 5.9 | -0.011 | 0.0 | 995 | 14.70 | 8.6 / 8.6 |

## Uncertainty estimate: does u_ad match the disturbance?

Plant per axis: xdot = b (u_inj + Delta), u_inj = u_nom + (injected) u_ad. b and the delay tau are fitted on 2-8 Hz content of all airborne segments (above the load band; closed-loop fit, so b is biased: the cancel fraction is also given for b x0.7 and x1.4). Delta_hat = LPF3Hz(xdot)/b - LPF3Hz(u_inj(t - tau)) is everything the nominal model does not explain (load torque and swing, CG offset, motor mismatch, drag), in control units. A perfect adaptive layer gives u_ad = -Delta_hat.

- static: mean(-Delta_hat) is the trim the drone needs; u_ad share = mean(u_ad) / mean(-Delta_hat) (the PID integrator carries the rest)
- dynamic (means removed): cancel = 1 - var(u_ad + Delta_hat) / var(Delta_hat); 1 = cancels all, 0 = no help, < 0 = adds disturbance. Band gain / phase of u_ad against -Delta_hat in 0.25-0.90 Hz (ideal 1 / 0 deg).
- ideal learner: cancel of -LPF_w(Delta_hat), what a perfect estimator behind the firmware u_ad filter at w rad/s could do. PID segments: u_ad is the shadow (computed, not injected).

| axis | b | tau (ms) | r (2-8 Hz) |
|---|---|---|---|
| pitch | 38.2 | 40 | 0.70 |
| roll | 49.1 | 50 | 0.69 |
| yaw | 36.3 | 10 | 0.86 |
| z_rate | 42.0 | 50 | 0.60 |

| seg | axis | -Delta static | u_ad mean | u_nom mean | u_ad share | -Delta dyn RMS | u_ad dyn RMS | band gain / phase / coh | cancel b x0.7 / x1 / x1.4 | ideal w 0.5 / 1 / 2 / 4 / 8 / 16 |
|---|---|---|---|---|---|---|---|---|---|---|
| F1 PID | pitch | -0.0810 | 0 | -0.0812 | - | 0.0301 | 0 | - | - | +0.04 / +0.12 / +0.25 / +0.43 / +0.65 / +0.85 |
| F2 PID | pitch | -0.0843 | 0 | -0.0835 | - | 0.0289 | 0 | - | - | -1.93 / -1.14 / -0.47 / +0.06 / +0.51 / +0.81 |
| F1 PID | roll | -0.0337 | 0 | -0.0336 | - | 0.0307 | 0 | - | - | -0.04 / +0.13 / +0.34 / +0.57 / +0.77 / +0.91 |
| F2 PID | roll | -0.0361 | 0 | -0.0349 | - | 0.0246 | 0 | - | - | -1.51 / -1.05 / -0.48 / +0.04 / +0.50 / +0.81 |
| F1 PID | yaw | +0.0028 | 0 | +0.0030 | - | 0.0389 | 0 | - | - | +0.00 / +0.11 / +0.29 / +0.54 / +0.80 / +0.94 |
| F2 PID | yaw | -0.0076 | 0 | -0.0078 | - | 0.0394 | 0 | - | - | +0.20 / +0.30 / +0.42 / +0.60 / +0.82 / +0.95 |
| F1 PID | z_rate | +0.8591 | 0 | +0.8600 | - | 0.1847 | 0 | - | - | +0.11 / +0.21 / +0.33 / +0.50 / +0.72 / +0.89 |
| F2 PID | z_rate | +0.9504 | 0 | +0.9500 | - | 0.3340 | 0 | - | - | -0.01 / +0.06 / +0.18 / +0.40 / +0.68 / +0.89 |

## Load swing frequency

Pendulum: L = rope 33 cm + half bottle 10 cm = 0.43 m. Simple sqrt(g/L)/2pi = 0.76 Hz; drone free to move, sqrt(g (M+m) / (M L))/2pi = 0.87 Hz (M 988 g, m 293 g). Measured: peak of the body-rate PSD in 0.15-1.5 Hz.

| seg | pitch peak (Hz) | roll peak (Hz) |
|---|---|---|
| F1 PID | 0.49 | 0.49 |
| F2 PID | 0.82 | 0.41 |

## Outside-force feature: body accel x/y vs the disturbance

Body-frame accel x/y sees only non-thrust forces (rope pull, slosh, wind, walls): thrust is along body z. If the torque is lever arm x force, -Delta_hat = w * a_xy with ONE constant w for any load. Per segment, means removed, both LPF 3 Hz. r = correlation at lag 0; cancel = r^2 = share of the dynamic disturbance a constant weight removes; w in control units per g. Lead = lag (+-300 ms) with the best |r|, positive = accel leads the disturbance. S6 = best single feature of x, x tanh x, cross, xm (u_nom left out: -Delta_hat contains u_nom by construction), then those four together, then together + accel x/y (offline least squares, constant weights). The S6 columns are an UPPER BOUND, partly circular: x and xm together rebuild the PID P term that sits inside -Delta_hat. Accel is an independent sensor, so its columns are clean. Caveat: an IMU off the centre of gravity adds (angular accel x offset), a few mg here.

| seg | axis | acc X r / cancel / w | acc Y r / cancel / w | lead X / Y (ms) | best S6 one | S6 four | S6 four + acc |
|---|---|---|---|---|---|---|---|
| F1 PID | pitch | -0.06 / 0.00 / -0.026 | +0.04 / 0.00 / +0.016 | -300 (r +0.18) / +120 (r +0.08) | 0.15 | 0.29 | 0.31 |
| F2 PID | pitch | -0.20 / 0.04 / -0.088 | +0.01 / 0.00 / +0.004 | +160 (r -0.38) / -260 (r +0.32) | 0.12 | 0.38 | 0.41 |
| F1 PID | roll | -0.13 / 0.02 / -0.054 | +0.04 / 0.00 / +0.017 | +0 (r -0.13) / -300 (r +0.20) | 0.21 | 0.45 | 0.47 |
| F2 PID | roll | -0.19 / 0.04 / -0.073 | -0.15 / 0.02 / -0.055 | -130 (r -0.29) / -50 (r -0.20) | 0.19 | 0.57 | 0.58 |

## Cross-coupling: does the other axis help? (vp16 48-bump split)

-Delta_hat (as above, LPF 3 Hz) fitted with constant weights on Gaussian grids, scored on held-out data (interleaved 5 s blocks: fit even, score odd, and back). Inputs in firmware units: rate / 200 deg/s, tilt / 15 deg. own24 = RBF24T (6 rate x 4 tilt, mrac_t_*_c); own48 = 8 rate x 6 tilt; own32 = 8 rate x 4 tilt; cross16 = own tilt x OTHER tilt (4 x 4); lin = other tilt + other rate as two plain features. Fair split test at 48 bumps: own48 vs own32 + cross16. Swing = same fit on 0.25-0.90 Hz band-passed signals. Upper bound for the own grids (they partly rebuild the PID term inside -Delta_hat), so a cross gain here is the conservative signal. Grid centres of own48/own32 are a test choice, not firmware.

| seg | axis | own24 in / held-out | own24 + lin | own48 | own32 + cross16 | swing own48 / own32 + cross16 |
|---|---|---|---|---|---|---|
| F1 PID | pitch | 0.44 / 0.27 | 0.19 | 0.15 | 0.26 | -0.70 / -0.25 |
| F2 PID | pitch | 0.64 / -1.64 | -1.60 | -3.00 | -0.26 | 0.15 / 0.58 |
| F1 PID | roll | 0.70 / 0.39 | 0.39 | 0.43 | 0.34 | 0.09 / 0.11 |
| F2 PID | roll | 0.75 / 0.08 | -0.08 | -0.17 | -1.18 | -1.95 / -0.28 |

## vp16 grid inputs: where the drone sits (centre placement, gamma T rule)

Each vp16 grid input in its normalised unit, p1 / p50 / p99 per segment. pitch/roll: rate / 200 deg/s, tilt / 15 deg, cross = other tilt / 15 deg. yaw: rate / 160 deg/s, heading error ~ gyrozPID.Des / 162 (= Des / Kp 6 / 27 deg, yawPID not logged), cross = Z thrust share. Z: climb rate / 1.0 m/s, thrust share u_nom / u_max, cross = tilt size / 15 deg. x/y: velocity / 300 cm/s, position error / 375 cm, cross = other velocity / 300. |e| = MRAC error (x/y: locxs/locys Des - FB, cm/s) against e_sat; > e_sat = share of time the tanh cap saturates.

| seg | axis | in1 p1 / p50 / p99 | in2 p1 / p50 / p99 | cross p1 / p50 / p99 | abs e p50 / p95 / p99 | e_sat | > e_sat (%) |
|---|---|---|---|---|---|---|---|
| F1 PID | pitch | -0.15 / -0.00 / +0.16 | -0.86 / -0.07 / +0.47 | -0.81 / -0.05 / +0.75 | 0.071 / 0.218 / 0.316 | 0.5 | 0.0 |
| F1 PID | roll | -0.17 / -0.00 / +0.17 | -0.81 / -0.05 / +0.75 | -0.86 / -0.07 / +0.47 | 0.069 / 0.226 / 0.333 | 0.5 | 0.0 |
| F1 PID | yaw | -0.47 / +0.00 / +0.53 | -0.55 / -0.01 / +0.60 | +0.02 / +0.06 / +0.12 | 0.084 / 0.595 / 0.810 | 0.7 | 2.3 |
| F1 PID | z_rate | -0.41 / +0.00 / +0.26 | +0.02 / +0.06 / +0.12 | +0.04 / +0.24 / +0.87 | 0.078 / 0.258 / 0.471 | 0.4 | 1.5 |
| F1 PID | x | -0.31 / +0.01 / +0.31 | -0.16 / +0.01 / +0.14 | -0.11 / +0.00 / +0.32 | 12.071 / 73.385 / 103.889 | 45 | 11.6 |
| F1 PID | y | -0.11 / +0.00 / +0.32 | -0.14 / -0.02 / +0.02 | -0.31 / +0.01 / +0.31 | 10.824 / 34.666 / 88.026 | 45 | 4.1 |
| F2 PID | pitch | -0.07 / +0.00 / +0.10 | -0.33 / -0.10 / +0.36 | -0.22 / -0.07 / +0.36 | 0.060 / 0.190 / 0.337 | 0.5 | 0.0 |
| F2 PID | roll | -0.09 / -0.00 / +0.08 | -0.22 / -0.07 / +0.36 | -0.33 / -0.10 / +0.36 | 0.059 / 0.183 / 0.246 | 0.5 | 0.0 |
| F2 PID | yaw | -0.10 / +0.00 / +0.10 | -0.14 / +0.01 / +0.12 | -0.03 / +0.07 / +0.17 | 0.088 / 0.209 / 0.248 | 0.7 | 0.0 |
| F2 PID | z_rate | -4.85 / +0.00 / +0.85 | -0.03 / +0.07 / +0.17 | +0.01 / +0.17 / +0.51 | 0.060 / 0.664 / 5.183 | 0.4 | 9.2 |
| F2 PID | x | -0.07 / +0.00 / +0.06 | -0.03 / -0.00 / +0.03 | -0.05 / +0.00 / +0.10 | 5.647 / 21.641 / 24.264 | 45 | 0.0 |
| F2 PID | y | -0.05 / +0.00 / +0.10 | -0.02 / -0.00 / +0.08 | -0.07 / +0.00 / +0.06 | 5.900 / 17.557 / 27.957 | 45 | 0.0 |

