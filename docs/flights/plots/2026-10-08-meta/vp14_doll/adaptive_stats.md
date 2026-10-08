# Adaptive-layer stats: `vp14_doll`

Measured from the log. Basis rebuilt from logged signals (see the tool docstring). Shaded: blue PID, orange MRAC injected.

## Per MRAC segment and axis

| seg | axis | rebuild r | u_ad RMS | u_nom RMS | ratio | r(u_ad,u_nom) | r(u_ad,x) | band phase u_ad/x (deg) | band phase u_nom/x | Re H u_ad / Re H u_nom | coh | top feature (RMS share) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| F1 MRAC | pitch | 1.00 | 0.0015 | 0.1077 | 0.01 | +0.14 | +0.39 | -21 | +134 | -0.07 | 0.65 | xm 73%, u_nom 27% |
| F1 MRAC | roll | 1.00 | 0.0011 | 0.0839 | 0.01 | +0.01 | +0.63 | -26 | +166 | -0.18 | 0.81 | xm 85%, u_nom 15% |
| F1 MRAC | yaw | 1.00 | 0.0198 | 0.0538 | 0.37 | -0.14 | +0.11 | +2 | +116 | -0.05 | 0.51 | bias 1 100%, xm 0% |
| F1 MRAC | z_rate | 0.78 | 0.6102 | 1.4526 | 0.42 | +0.10 | +0.05 | -63 | +149 | -0.10 | 0.28 | bias 1 87%, u_nom 13% |

Phase of a torque against the body rate x in 0.25-0.90 Hz: +/-180 = pure damping, 0 = anti-damping, +/-90 = no energy. Re H u_ad / Re H u_nom > 0: u_ad adds damping like the PID; < 0: removes it.

## What-if: same weights, other omega_u (open-loop replay of the logged raw sum)

Band phase u_ad/x (deg) / Re H ratio, as above. Closed loop would differ (the weights would evolve differently); this isolates the filter lag.

| seg | axis | flown | 10 rad/s | 15 rad/s | 20 rad/s |
|---|---|---|---|---|---|
| F1 MRAC | pitch | -21 / -0.07 | +2 / -0.10 | +9 / -0.11 | +12 / -0.11 |
| F1 MRAC | roll | -28 / -0.17 | -12 / -0.22 | -6 / -0.23 | -3 / -0.24 |

## Weights Theta (start -> end of each MRAC segment)

| seg | axis | Theta[0] bias 1 | Theta[1] x | Theta[2] x tanh x | Theta[3] cross | Theta[4] u_nom | Theta[5] xm | |Theta| growth |
|---|---|---|---|---|---|---|---|---|
| F1 MRAC | pitch | +0.000 -> +0.000 | +0.000 -> +0.000 | +0.000 -> +0.000 | +0.000 -> +0.000 | +0.000 -> +0.015 | +0.000 -> +0.019 | 0.000 -> 0.024 |
| F1 MRAC | roll | +0.000 -> +0.000 | +0.000 -> +0.000 | +0.000 -> +0.000 | +0.000 -> +0.000 | +0.000 -> +0.010 | +0.000 -> +0.011 | 0.000 -> 0.015 |
| F1 MRAC | yaw | +0.000 -> +0.025 | +0.000 -> +0.000 | +0.000 -> +0.000 | +0.000 -> +0.000 | +0.000 -> +0.003 | +0.000 -> +0.004 | 0.000 -> 0.026 |
| F1 MRAC | z_rate | +0.000 -> +0.283 | +0.000 -> +0.005 | +0.000 -> +0.004 | +0.000 -> +0.000 | +0.000 -> +0.170 | +0.000 -> +0.040 | 0.000 -> 0.332 |

## Ext block (vp basis 8, 24 Gaussians per axis, pitch/roll)

Grid inputs in the firmware's normalised units (rate, angle); the Gaussian centres span -1..1 (basis 5: -1.5..1.5 rate, -1..1 angle). Activity = sd / mean of the most varying Gaussian over the segment: near 0 the grid acts as one constant (a bias), not as a function of the state. Ext share = ext RMS^2 / (ext RMS^2 + S6-part RMS^2). Slope = d|Theta_ext|/dt over the last third (> 0: still learning).

| seg | axis | rate p1 / p50 / p99 | angle p1 / p50 / p99 | activity | ext RMS | S6 part RMS | ext share | |Theta_ext| start -> end | slope (/s) | top ext (RMS share) |
|---|---|---|---|---|---|---|---|---|---|---|
| F1 MRAC | pitch | -0.135 / -0.001 / +0.130 | -0.453 / -0.068 / +0.368 | 0.543 | 0.0000 | 0.0019 | 0% | 0.000 -> 0.000 | +0.0000 | e23 0%, e22 0% |
| F1 MRAC | roll | -0.150 / -0.000 / +0.142 | -0.628 / -0.067 / +0.437 | 0.554 | 0.0000 | 0.0014 | 0% | 0.000 -> 0.000 | +0.0000 | e23 0%, e22 0% |

### Ext ranking (every Gaussian, RMS^2 share of the ext block, cumulative in brackets)

| seg | axis | ranked |
|---|---|---|
| F1 MRAC | pitch | e23 0.0% [0%], e22 0.0% [0%], e21 0.0% [0%], e20 0.0% [0%], e19 0.0% [0%], e18 0.0% [0%], e17 0.0% [0%], e16 0.0% [0%], e15 0.0% [0%], e14 0.0% [0%], e13 0.0% [0%], e12 0.0% [0%], e11 0.0% [0%], e10 0.0% [0%], e9 0.0% [0%], e8 0.0% [0%], e7 0.0% [0%], e6 0.0% [0%], e5 0.0% [0%], e4 0.0% [0%], e3 0.0% [0%], e2 0.0% [0%], e1 0.0% [0%], e0 0.0% [0%] |
| F1 MRAC | roll | e23 0.0% [0%], e22 0.0% [0%], e21 0.0% [0%], e20 0.0% [0%], e19 0.0% [0%], e18 0.0% [0%], e17 0.0% [0%], e16 0.0% [0%], e15 0.0% [0%], e14 0.0% [0%], e13 0.0% [0%], e12 0.0% [0%], e11 0.0% [0%], e10 0.0% [0%], e9 0.0% [0%], e8 0.0% [0%], e7 0.0% [0%], e6 0.0% [0%], e5 0.0% [0%], e4 0.0% [0%], e3 0.0% [0%], e2 0.0% [0%], e1 0.0% [0%], e0 0.0% [0%] |
| F1 MRAC | p+r mean | e23 0.0% [0%], e22 0.0% [0%], e21 0.0% [0%], e20 0.0% [0%], e19 0.0% [0%], e18 0.0% [0%], e17 0.0% [0%], e16 0.0% [0%], e15 0.0% [0%], e14 0.0% [0%], e13 0.0% [0%], e12 0.0% [0%], e11 0.0% [0%], e10 0.0% [0%], e9 0.0% [0%], e8 0.0% [0%], e7 0.0% [0%], e6 0.0% [0%], e5 0.0% [0%], e4 0.0% [0%], e3 0.0% [0%], e2 0.0% [0%], e1 0.0% [0%], e0 0.0% [0%] |

## Static offsets per segment (drift)

Expected static torque of the load at the rope point: pitch 0.112 N m (x 2 cm), roll 0.391 N m (y 7 cm), mg = 5.59 N.

| seg | mean u_nom p / r | mean u_ad p / r | mean Theta[0] p / r | pitch Des - FB (deg) | roll Des - FB | locx Des-FB (cm) | locy Des-FB | locx/y PID U mean | x FB slope (cm/s) | y FB slope | x stick on (%) | x stick vel Des median (cm/s) | x vel FB mean, no stick (cm/s) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| F1 MRAC | -0.0822 / +0.0500 | -0.0006 / +0.0003 | +0.0000 / +0.0000 | +0.11 | -0.06 | -5.7 | -5.3 | -5.8 / -6.8 | +8.02 | +1.20 | 21 | +46 | +1.89 |

## Per segment: attitude, height, motors, battery

| seg | s | pitch sd | roll sd | rate sd p / r (deg/s) | band PSD p / r | z - z_des | motor at 4000 (%) | max motor spread | V mean | stab CPU % mean / max |
|---|---|---|---|---|---|---|---|---|---|---|
| F1 MRAC | 49 | 2.54 | 3.03 | 10.5 / 10.8 | 35.2 / 44.7 | +0.004 | 4.9 | 1172 | 14.43 | 10.0 / 10.5 |

## Uncertainty estimate: does u_ad match the disturbance?

Plant per axis: xdot = b (u_inj + Delta), u_inj = u_nom + (injected) u_ad. b and the delay tau are fitted on 2-8 Hz content of all airborne segments (above the load band; closed-loop fit, so b is biased: the cancel fraction is also given for b x0.7 and x1.4). Delta_hat = LPF3Hz(xdot)/b - LPF3Hz(u_inj(t - tau)) is everything the nominal model does not explain (load torque and swing, CG offset, motor mismatch, drag), in control units. A perfect adaptive layer gives u_ad = -Delta_hat.

- static: mean(-Delta_hat) is the trim the drone needs; u_ad share = mean(u_ad) / mean(-Delta_hat) (the PID integrator carries the rest)
- dynamic (means removed): cancel = 1 - var(u_ad + Delta_hat) / var(Delta_hat); 1 = cancels all, 0 = no help, < 0 = adds disturbance. Band gain / phase of u_ad against -Delta_hat in 0.25-0.90 Hz (ideal 1 / 0 deg).
- ideal learner: cancel of -LPF_w(Delta_hat), what a perfect estimator behind the firmware u_ad filter at w rad/s could do. PID segments: u_ad is the shadow (computed, not injected).

| axis | b | tau (ms) | r (2-8 Hz) |
|---|---|---|---|
| pitch | 62.9 | 50 | 0.79 |
| roll | 47.4 | 50 | 0.69 |
| yaw | 81.9 | 40 | 0.70 |
| z_rate | 23.5 | 30 | 0.46 |

| seg | axis | -Delta static | u_ad mean | u_nom mean | u_ad share | -Delta dyn RMS | u_ad dyn RMS | band gain / phase / coh | cancel b x0.7 / x1 / x1.4 | ideal w 0.5 / 1 / 2 / 4 / 8 / 16 |
|---|---|---|---|---|---|---|---|---|---|---|
| F1 MRAC | pitch | -0.0830 | -0.0006 | -0.0822 | 0.01 | 0.0312 | 0.0013 | 0.01 / -117 / 0.13 | +0.04 / +0.04 / +0.04 | +0.04 / +0.21 / +0.40 / +0.59 / +0.78 / +0.92 |
| F1 MRAC | roll | +0.0499 | +0.0003 | +0.0500 | 0.01 | 0.0265 | 0.0010 | 0.04 / +66 / 0.41 | +0.02 / +0.02 / +0.02 | -0.62 / -0.15 / +0.18 / +0.47 / +0.72 / +0.89 |
| F1 MRAC | yaw | +0.0245 | +0.0123 | +0.0121 | 0.50 | 0.0466 | 0.0155 | 0.05 / -109 / 0.62 | +0.02 / +0.02 / +0.02 | +0.12 / +0.25 / +0.42 / +0.64 / +0.85 / +0.96 |
| F1 MRAC | z_rate | +1.9661 | +0.5723 | +1.3935 | 0.29 | 0.4097 | 0.2116 | 0.28 / -15 / 0.62 | +0.30 / +0.32 / +0.33 | +0.15 / +0.22 / +0.32 / +0.50 / +0.72 / +0.89 |

## Load swing frequency

Pendulum: L = rope 33 cm + half bottle 10 cm = 0.43 m. Simple sqrt(g/L)/2pi = 0.76 Hz; drone free to move, sqrt(g (M+m) / (M L))/2pi = 0.95 Hz (M 988 g, m 570 g). Measured: peak of the body-rate PSD in 0.15-1.5 Hz.

| seg | pitch peak (Hz) | roll peak (Hz) |
|---|---|---|
| F1 MRAC | 0.59 | 0.59 |

## Outside-force feature: body accel x/y vs the disturbance

Body-frame accel x/y sees only non-thrust forces (rope pull, slosh, wind, walls): thrust is along body z. If the torque is lever arm x force, -Delta_hat = w * a_xy with ONE constant w for any load. Per segment, means removed, both LPF 3 Hz. r = correlation at lag 0; cancel = r^2 = share of the dynamic disturbance a constant weight removes; w in control units per g. Lead = lag (+-300 ms) with the best |r|, positive = accel leads the disturbance. S6 = best single feature of x, x tanh x, cross, xm (u_nom left out: -Delta_hat contains u_nom by construction), then those four together, then together + accel x/y (offline least squares, constant weights). The S6 columns are an UPPER BOUND, partly circular: x and xm together rebuild the PID P term that sits inside -Delta_hat. Accel is an independent sensor, so its columns are clean. Caveat: an IMU off the centre of gravity adds (angular accel x offset), a few mg here.

| seg | axis | acc X r / cancel / w | acc Y r / cancel / w | lead X / Y (ms) | best S6 one | S6 four | S6 four + acc |
|---|---|---|---|---|---|---|---|
| F1 MRAC | pitch | -0.12 / 0.01 / -0.042 | +0.12 / 0.01 / +0.041 | +150 (r -0.23) / -110 (r +0.14) | 0.08 | 0.53 | 0.54 |
| F1 MRAC | roll | -0.09 / 0.01 / -0.025 | -0.06 / 0.00 / -0.018 | -210 (r -0.15) / -300 (r +0.13) | 0.04 | 0.33 | 0.34 |

## Cross-coupling: does the other axis help? (vp16 48-bump split)

-Delta_hat (as above, LPF 3 Hz) fitted with constant weights on Gaussian grids, scored on held-out data (interleaved 5 s blocks: fit even, score odd, and back). Inputs in firmware units: rate / 200 deg/s, tilt / 15 deg. own24 = RBF24T (6 rate x 4 tilt, mrac_t_*_c); own48 = 8 rate x 6 tilt; own32 = 8 rate x 4 tilt; cross16 = own tilt x OTHER tilt (4 x 4); lin = other tilt + other rate as two plain features. Fair split test at 48 bumps: own48 vs own32 + cross16. Swing = same fit on 0.25-0.90 Hz band-passed signals. Upper bound for the own grids (they partly rebuild the PID term inside -Delta_hat), so a cross gain here is the conservative signal. Grid centres of own48/own32 are a test choice, not firmware.

| seg | axis | own24 in / held-out | own24 + lin | own48 | own32 + cross16 | swing own48 / own32 + cross16 |
|---|---|---|---|---|---|---|
| F1 MRAC | pitch | 0.17 / 0.02 | -0.00 | -0.07 | 0.10 | -0.22 / -0.32 |
| F1 MRAC | roll | 0.38 / 0.12 | 0.08 | 0.08 | -0.09 | 0.13 / 0.11 |

## vp16 grid inputs: where the drone sits (centre placement, gamma T rule)

Each vp16 grid input in its normalised unit, p1 / p50 / p99 per segment. pitch/roll: rate / 200 deg/s, tilt / 15 deg, cross = other tilt / 15 deg. yaw: rate / 160 deg/s, heading error ~ gyrozPID.Des / 162 (= Des / Kp 6 / 27 deg, yawPID not logged), cross = Z thrust share. Z: climb rate / 1.0 m/s, thrust share u_nom / u_max, cross = tilt size / 15 deg. x/y: velocity / 300 cm/s, position error / 375 cm, cross = other velocity / 300. |e| = MRAC error (x/y: locxs/locys Des - FB, cm/s) against e_sat; > e_sat = share of time the tanh cap saturates.

| seg | axis | in1 p1 / p50 / p99 | in2 p1 / p50 / p99 | cross p1 / p50 / p99 | abs e p50 / p95 / p99 | e_sat | > e_sat (%) |
|---|---|---|---|---|---|---|---|
| F1 MRAC | pitch | -0.14 / -0.00 / +0.13 | -0.45 / -0.07 / +0.37 | -0.63 / -0.07 / +0.44 | 0.077 / 0.261 / 0.490 | 0.5 | 0.9 |
| F1 MRAC | roll | -0.15 / -0.00 / +0.14 | -0.63 / -0.07 / +0.44 | -0.45 / -0.07 / +0.37 | 0.068 / 0.213 / 0.295 | 0.5 | 0.1 |
| F1 MRAC | yaw | -0.11 / -0.00 / +0.11 | -0.12 / -0.00 / +0.19 | -0.00 / +0.10 / +0.17 | 0.071 / 0.254 / 0.328 | 0.7 | 0.0 |
| F1 MRAC | z_rate | -2.05 / +0.00 / +1.35 | -0.00 / +0.10 / +0.17 | +0.03 / +0.20 / +0.72 | 0.074 / 0.846 / 3.111 | 0.4 | 16.4 |
| F1 MRAC | x | -0.12 / +0.01 / +0.21 | -0.18 / -0.01 / +0.04 | -0.25 / +0.01 / +0.16 | 11.183 / 44.984 / 69.415 | 45 | 5.0 |
| F1 MRAC | y | -0.25 / +0.01 / +0.16 | -0.08 / -0.02 / +0.28 | -0.12 / +0.01 / +0.21 | 13.223 / 42.012 / 51.338 | 45 | 2.9 |

## Feature capability (all MRAC segments pooled)

Theta_i moves at gamma_i * s * phi_i / (1 + |phi|^2) (API/mrac.c:748, denom :932), so a feature's learning drive is gamma_i * E[phi_i^2 / (1 + |phi|^2)] (speed, shown relative to the bias). Its reach is lim_i * RMS(phi_i): the largest torque it can make with Theta_i at its projection bound. gamma_i, lim_i from the MRAC_BASIS rows (pitch/roll/yaw/z), mrac_g_phi and the preset g assumed 1.

| axis | feature | RMS phi | gamma | drive E[phi^2/(1+|phi|^2)] | speed vs bias | lim | reach lim*RMS phi | actual RMS Theta*phi | Theta0 63% time (s) per seg |
|---|---|---|---|---|---|---|---|---|---|
| pitch | bias 1 | 1 | 1.50 | 0.483 | 1 | 0.150 | 0.15 | 0 | F1 - |
| pitch | x | 0.183 | 0.20 | 0.0148 | 0.0041 | 0.050 | 0.00916 | 0 |  |
| pitch | x tanh x | 0.0673 | 0.05 | 0.00174 | 0.00012 | 0.020 | 0.00135 | 0 |  |
| pitch | cross | 0.0246 | 0.05 | 0.000282 | 1.9e-05 | 0.050 | 0.00123 | 0 |  |
| pitch | u_nom | 0.108 | 0.10 | 0.00551 | 0.00076 | 0.200 | 0.0215 | 0.000969 |  |
| pitch | xm | 0.159 | 0.10 | 0.0116 | 0.0016 | 0.150 | 0.0239 | 0.00158 |  |
| roll | bias 1 | 1 | 1.50 | 0.483 | 1 | 0.150 | 0.15 | 0 | F1 - |
| roll | x | 0.189 | 0.20 | 0.0155 | 0.0043 | 0.050 | 0.00943 | 0 |  |
| roll | x tanh x | 0.07 | 0.05 | 0.00195 | 0.00013 | 0.020 | 0.0014 | 0 |  |
| roll | cross | 0.0245 | 0.05 | 0.000277 | 1.9e-05 | 0.050 | 0.00123 | 0 |  |
| roll | u_nom | 0.0839 | 0.10 | 0.00337 | 0.00046 | 0.200 | 0.0168 | 0.000528 |  |
| roll | xm | 0.17 | 0.10 | 0.0126 | 0.0017 | 0.150 | 0.0255 | 0.00128 |  |
| yaw | bias 1 | 1 | 1.00 | 0.492 | 1 | 0.090 | 0.09 | 0.02 | F1 2 |
| yaw | x | 0.122 | 0.10 | 0.00715 | 0.0015 | 0.030 | 0.00366 | 2.89e-05 |  |
| yaw | x tanh x | 0.0272 | 0.05 | 0.000348 | 3.5e-05 | 0.012 | 0.000326 | 4.22e-06 |  |
| yaw | cross | 0 | 0.05 | 0 | 0 | 0.030 | 0 | 0 |  |
| yaw | u_nom | 0.0538 | 0.10 | 0.00141 | 0.00029 | 0.120 | 0.00646 | 8.09e-05 |  |
| yaw | xm | 0.116 | 0.10 | 0.0065 | 0.0013 | 0.090 | 0.0104 | 0.000276 |  |
| z_rate | bias 1 | 1 | 2.00 | 0.245 | 1 | 1.000 | 1 | 0.458 | F1 3 |
| z_rate | x | 0.7 | 0.50 | 0.0206 | 0.021 | 0.100 | 0.07 | 0.00105 |  |
| z_rate | x tanh x | 0.672 | 0.10 | 0.0128 | 0.0026 | 0.050 | 0.0336 | 0.00162 |  |
| z_rate | cross | 0 | 0.10 | 0 | 0 | 0.050 | 0 | 0 |  |
| z_rate | u_nom | 1.45 | 0.20 | 0.465 | 0.19 | 0.200 | 0.291 | 0.18 |  |
| z_rate | xm | 0.247 | 0.20 | 0.0126 | 0.0052 | 0.200 | 0.0494 | 0.00432 |  |

