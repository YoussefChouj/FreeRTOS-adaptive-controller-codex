# Adaptive-layer stats: `exp14_vp10-rbf24-load`

Measured from the log. Basis rebuilt from logged signals (see the tool docstring). Shaded: blue PID, orange MRAC injected.

## Per MRAC segment and axis

| seg | axis | rebuild r | u_ad RMS | u_nom RMS | ratio | r(u_ad,u_nom) | r(u_ad,x) | band phase u_ad/x (deg) | band phase u_nom/x | Re H u_ad / Re H u_nom | coh | top feature (RMS share) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| F1 MRAC | pitch | 1.00 | 0.0049 | 0.1029 | 0.05 | -0.00 | +0.02 | -69 | +112 | -0.09 | 0.31 | xm 70%, u_nom 30% |
| F1 MRAC | roll | 1.00 | 0.0023 | 0.0863 | 0.03 | -0.01 | -0.03 | -93 | -98 | 0.01 | 0.12 | xm 73%, u_nom 27% |
| F1 MRAC | yaw | 1.00 | 0.0179 | 0.0622 | 0.29 | -0.10 | +0.14 | +46 | +117 | -0.05 | 0.47 | bias 1 100%, u_nom 0% |
| F1 MRAC | z_rate | 0.84 | 0.2751 | 1.4019 | 0.20 | +0.18 | +0.17 | -54 | +158 | -0.23 | 0.42 | bias 1 74%, u_nom 26% |

Phase of a torque against the body rate x in 0.25-0.90 Hz: +/-180 = pure damping, 0 = anti-damping, +/-90 = no energy. Re H u_ad / Re H u_nom > 0: u_ad adds damping like the PID; < 0: removes it.

## What-if: same weights, other omega_u (open-loop replay of the logged raw sum)

Band phase u_ad/x (deg) / Re H ratio, as above. Closed loop would differ (the weights would evolve differently); this isolates the filter lag.

| seg | axis | flown | 10 rad/s | 15 rad/s | 20 rad/s |
|---|---|---|---|---|---|
| F1 MRAC | pitch | -69 / -0.09 | -46 / -0.21 | -40 / -0.23 | -37 / -0.24 |
| F1 MRAC | roll | -93 / +0.01 | -78 / -0.06 | -71 / -0.10 | -68 / -0.13 |

## Weights Theta (start -> end of each MRAC segment)

| seg | axis | Theta[0] bias 1 | Theta[1] x | Theta[2] x tanh x | Theta[3] cross | Theta[4] u_nom | Theta[5] xm | |Theta| growth |
|---|---|---|---|---|---|---|---|---|
| F1 MRAC | pitch | +0.000 -> +0.000 | +0.000 -> +0.000 | +0.000 -> +0.000 | +0.000 -> +0.000 | +0.000 -> +0.003 | +0.000 -> +0.003 | 0.000 -> 0.004 |
| F1 MRAC | roll | +0.000 -> +0.000 | +0.000 -> +0.000 | +0.000 -> +0.000 | +0.000 -> +0.000 | +0.000 -> +0.002 | +0.000 -> +0.002 | 0.000 -> 0.003 |
| F1 MRAC | yaw | +0.002 -> -0.022 | +0.000 -> +0.000 | +0.000 -> +0.001 | +0.000 -> +0.000 | +0.000 -> +0.003 | +0.000 -> +0.002 | 0.002 -> 0.023 |
| F1 MRAC | z_rate | +0.011 -> +0.040 | +0.000 -> +0.008 | +0.000 -> +0.003 | +0.000 -> +0.000 | +0.002 -> +0.102 | +0.000 -> +0.029 | 0.011 -> 0.114 |

## Ext block (vp basis 4, 24 Gaussians per axis, pitch/roll)

Grid inputs in the firmware's normalised units (rate, angle); the Gaussian centres span -1..1 (basis 5: -1.5..1.5 rate, -1..1 angle). Activity = sd / mean of the most varying Gaussian over the segment: near 0 the grid acts as one constant (a bias), not as a function of the state. Ext share = ext RMS^2 / (ext RMS^2 + S6-part RMS^2). Slope = d|Theta_ext|/dt over the last third (> 0: still learning).

| seg | axis | rate p1 / p50 / p99 | angle p1 / p50 / p99 | activity | ext RMS | S6 part RMS | ext share | |Theta_ext| start -> end | slope (/s) | top ext (RMS share) |
|---|---|---|---|---|---|---|---|---|---|---|
| F1 MRAC | pitch | -0.077 / -0.000 / +0.079 | -0.221 / -0.039 / +0.132 | 0.285 | 0.0048 | 0.0003 | 100% | 0.000 -> 0.005 | +0.0001 | e14 40%, e13 35% |
| F1 MRAC | roll | -0.077 / +0.000 / +0.077 | -0.255 / -0.033 / +0.151 | 0.275 | 0.0024 | 0.0002 | 99% | 0.000 -> 0.002 | +0.0000 | e13 38%, e10 24% |

### Ext ranking (every Gaussian, RMS^2 share of the ext block, cumulative in brackets)

| seg | axis | ranked |
|---|---|---|
| F1 MRAC | pitch | e14 40.2% [40%], e13 34.5% [75%], e10 11.8% [87%], e9 7.9% [94%], e18 1.7% [96%], e17 1.7% [98%], e15 0.9% [99%], e12 0.6% [99%], e11 0.3% [100%], e5 0.1% [100%], e8 0.1% [100%], e6 0.1% [100%], e19 0.0% [100%], e16 0.0% [100%], e4 0.0% [100%], e7 0.0% [100%], e21 0.0% [100%], e22 0.0% [100%], e1 0.0% [100%], e2 0.0% [100%], e20 0.0% [100%], e23 0.0% [100%], e0 0.0% [100%], e3 0.0% [100%] |
| F1 MRAC | roll | e13 37.6% [38%], e10 23.8% [61%], e9 15.7% [77%], e14 13.7% [91%], e17 2.2% [93%], e12 1.8% [95%], e6 1.5% [96%], e18 0.9% [97%], e5 0.9% [98%], e11 0.9% [99%], e8 0.5% [100%], e15 0.3% [100%], e16 0.1% [100%], e7 0.0% [100%], e4 0.0% [100%], e19 0.0% [100%], e21 0.0% [100%], e2 0.0% [100%], e1 0.0% [100%], e22 0.0% [100%], e20 0.0% [100%], e3 0.0% [100%], e0 0.0% [100%], e23 0.0% [100%] |
| F1 MRAC | p+r mean | e13 36.1% [36%], e14 27.0% [63%], e10 17.8% [81%], e9 11.8% [93%], e17 2.0% [95%], e18 1.3% [96%], e12 1.2% [97%], e6 0.8% [98%], e11 0.6% [98%], e15 0.6% [99%], e5 0.5% [100%], e8 0.3% [100%], e16 0.1% [100%], e7 0.0% [100%], e19 0.0% [100%], e4 0.0% [100%], e21 0.0% [100%], e22 0.0% [100%], e2 0.0% [100%], e1 0.0% [100%], e20 0.0% [100%], e3 0.0% [100%], e23 0.0% [100%], e0 0.0% [100%] |

## Static offsets per segment (drift)

Expected static torque of the load at the rope point: pitch 0.112 N m (x 2 cm), roll 0.391 N m (y 7 cm), mg = 5.59 N.

| seg | mean u_nom p / r | mean u_ad p / r | mean Theta[0] p / r | pitch Des - FB (deg) | roll Des - FB | locx Des-FB (cm) | locy Des-FB | locx/y PID U mean | x FB slope (cm/s) | y FB slope | x stick on (%) | x stick vel Des median (cm/s) | x vel FB mean, no stick (cm/s) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| F1 PID | -0.0676 / +0.0536 | -0.0252 / +0.0221 | +0.0000 / +0.0000 | -0.29 | -0.36 | +2.3 | -15.8 | +3.4 / -12.8 | +1.57 | +3.37 | 0 | +nan | +2.24 |
| F1 MRAC | -0.0760 / +0.0536 | -0.0044 / -0.0001 | +0.0000 / +0.0000 | +0.05 | -0.10 | -0.6 | -7.1 | +1.0 / -9.6 | +0.94 | -0.15 | 5 | +32 | +0.57 |

## Per segment: attitude, height, motors, battery

| seg | s | pitch sd | roll sd | rate sd p / r (deg/s) | band PSD p / r | z - z_des | motor at 4000 (%) | max motor spread | V mean | stab CPU % mean / max |
|---|---|---|---|---|---|---|---|---|---|---|
| F1 PID | 6 | 1.90 | 0.73 | 12.8 / 7.2 | 22.6 / 1.7 | +0.039 | 0.0 | 1056 | 15.10 | 9.2 / 9.6 |
| F1 MRAC | 63 | 1.91 | 2.08 | 9.5 / 8.6 | 18.5 / 19.9 | +0.004 | 1.4 | 1142 | 14.74 | 9.3 / 9.8 |

## Uncertainty estimate: does u_ad match the disturbance?

Plant per axis: xdot = b (u_inj + Delta), u_inj = u_nom + (injected) u_ad. b and the delay tau are fitted on 2-8 Hz content of all airborne segments (above the load band; closed-loop fit, so b is biased: the cancel fraction is also given for b x0.7 and x1.4). Delta_hat = LPF3Hz(xdot)/b - LPF3Hz(u_inj(t - tau)) is everything the nominal model does not explain (load torque and swing, CG offset, motor mismatch, drag), in control units. A perfect adaptive layer gives u_ad = -Delta_hat.

- static: mean(-Delta_hat) is the trim the drone needs; u_ad share = mean(u_ad) / mean(-Delta_hat) (the PID integrator carries the rest)
- dynamic (means removed): cancel = 1 - var(u_ad + Delta_hat) / var(Delta_hat); 1 = cancels all, 0 = no help, < 0 = adds disturbance. Band gain / phase of u_ad against -Delta_hat in 0.25-0.90 Hz (ideal 1 / 0 deg).
- ideal learner: cancel of -LPF_w(Delta_hat), what a perfect estimator behind the firmware u_ad filter at w rad/s could do. PID segments: u_ad is the shadow (computed, not injected).

| axis | b | tau (ms) | r (2-8 Hz) |
|---|---|---|---|
| pitch | 65.9 | 40 | 0.78 |
| roll | 43.4 | 40 | 0.55 |
| yaw | 48.2 | 30 | 0.62 |
| z_rate | 36.3 | 40 | 0.52 |

| seg | axis | -Delta static | u_ad mean | u_nom mean | u_ad share | -Delta dyn RMS | u_ad dyn RMS | band gain / phase / coh | cancel b x0.7 / x1 / x1.4 | ideal w 0.5 / 1 / 2 / 4 / 8 / 16 |
|---|---|---|---|---|---|---|---|---|---|---|
| F1 PID | pitch | -0.0680 | -0.0252 | -0.0676 | 0.37 | 0.0324 | 0.0121 | 0.15 / -46 / 0.38 | -0.14 / -0.19 / -0.24 | -1.42 / -1.04 / -0.45 / +0.07 / +0.53 / +0.84 |
| F1 MRAC | pitch | -0.0809 | -0.0044 | -0.0760 | 0.05 | 0.0282 | 0.0021 | 0.02 / -119 / 0.35 | -0.00 / +0.00 / +0.00 | -3.09 / -1.39 / -0.44 / +0.16 / +0.61 / +0.87 |
| F1 PID | roll | +0.0559 | +0.0221 | +0.0536 | 0.40 | 0.0342 | 0.0101 | 0.20 / +77 / 0.34 | +0.12 / +0.14 / +0.17 | +0.10 / +0.12 / +0.25 / +0.42 / +0.62 / +0.82 |
| F1 MRAC | roll | +0.0540 | -0.0001 | +0.0536 | -0.00 | 0.0356 | 0.0023 | 0.01 / -101 / 0.17 | +0.00 / +0.00 / +0.01 | -12.51 / -6.00 / -2.56 / -0.74 / +0.25 / +0.74 |
| F1 PID | yaw | +0.0449 | +0.0429 | +0.0453 | 0.96 | 0.0352 | 0.0223 | 0.71 / +129 / 0.28 | -0.53 / -0.40 / -0.33 | -0.66 / -0.48 / -0.12 / +0.31 / +0.71 / +0.92 |
| F1 MRAC | yaw | +0.0125 | -0.0158 | +0.0283 | -1.27 | 0.0407 | 0.0085 | 0.04 / -111 / 0.38 | -0.03 / -0.02 / -0.01 | -0.10 / +0.03 / +0.19 / +0.45 / +0.75 / +0.93 |
| F1 PID | z_rate | +1.2323 | +0.4055 | +1.2327 | 0.33 | 0.3764 | 0.2182 | 0.15 / +10 / 0.72 | +0.03 / +0.03 / +0.04 | +0.07 / +0.14 / +0.28 / +0.50 / +0.75 / +0.92 |
| F1 MRAC | z_rate | +1.6176 | +0.2575 | +1.3591 | 0.16 | 0.3277 | 0.0968 | 0.16 / -8 / 0.49 | +0.20 / +0.21 / +0.22 | +0.17 / +0.24 / +0.32 / +0.47 / +0.69 / +0.88 |

## Load swing frequency

Pendulum: L = rope 33 cm + half bottle 10 cm = 0.43 m. Simple sqrt(g/L)/2pi = 0.76 Hz; drone free to move, sqrt(g (M+m) / (M L))/2pi = 0.95 Hz (M 988 g, m 570 g). Measured: peak of the body-rate PSD in 0.15-1.5 Hz.

| seg | pitch peak (Hz) | roll peak (Hz) |
|---|---|---|
| F1 PID | 0.96 | 0.64 |
| F1 MRAC | 0.88 | 0.88 |

## Outside-force feature: body accel x/y vs the disturbance

Body-frame accel x/y sees only non-thrust forces (rope pull, slosh, wind, walls): thrust is along body z. If the torque is lever arm x force, -Delta_hat = w * a_xy with ONE constant w for any load. Per segment, means removed, both LPF 3 Hz. r = correlation at lag 0; cancel = r^2 = share of the dynamic disturbance a constant weight removes; w in control units per g. Lead = lag (+-300 ms) with the best |r|, positive = accel leads the disturbance. S6 = best single feature of x, x tanh x, cross, xm (u_nom left out: -Delta_hat contains u_nom by construction), then those four together, then together + accel x/y (offline least squares, constant weights). The S6 columns are an UPPER BOUND, partly circular: x and xm together rebuild the PID P term that sits inside -Delta_hat. Accel is an independent sensor, so its columns are clean. Caveat: an IMU off the centre of gravity adds (angular accel x offset), a few mg here.

| seg | axis | acc X r / cancel / w | acc Y r / cancel / w | lead X / Y (ms) | best S6 one | S6 four | S6 four + acc |
|---|---|---|---|---|---|---|---|
| F1 PID | pitch | -0.53 / 0.28 / -0.163 | -0.52 / 0.27 / -0.236 | +110 (r -0.76) / +60 (r -0.58) | 0.03 | 0.45 | 0.66 |
| F1 MRAC | pitch | -0.44 / 0.20 / -0.130 | +0.05 / 0.00 / +0.017 | +90 (r -0.55) / +300 (r -0.28) | 0.07 | 0.67 | 0.68 |
| F1 PID | roll | -0.07 / 0.01 / -0.024 | +0.05 / 0.00 / +0.023 | -120 (r -0.31) / +100 (r -0.18) | 0.10 | 0.15 | 0.20 |
| F1 MRAC | roll | -0.05 / 0.00 / -0.017 | -0.08 / 0.01 / -0.035 | +300 (r +0.26) / +100 (r -0.24) | 0.01 | 0.16 | 0.17 |

## Cross-coupling: does the other axis help? (vp16 48-bump split)

-Delta_hat (as above, LPF 3 Hz) fitted with constant weights on Gaussian grids, scored on held-out data (interleaved 5 s blocks: fit even, score odd, and back). Inputs in firmware units: rate / 200 deg/s, tilt / 15 deg. own24 = RBF24T (6 rate x 4 tilt, mrac_t_*_c); own48 = 8 rate x 6 tilt; own32 = 8 rate x 4 tilt; cross16 = own tilt x OTHER tilt (4 x 4); lin = other tilt + other rate as two plain features. Fair split test at 48 bumps: own48 vs own32 + cross16. Swing = same fit on 0.25-0.90 Hz band-passed signals. Upper bound for the own grids (they partly rebuild the PID term inside -Delta_hat), so a cross gain here is the conservative signal. Grid centres of own48/own32 are a test choice, not firmware.

| seg | axis | own24 in / held-out | own24 + lin | own48 | own32 + cross16 | swing own48 / own32 + cross16 |
|---|---|---|---|---|---|---|
| F1 PID | pitch | 0.79 / 0.01 | -0.01 | -0.03 | -0.19 | -0.10 / -0.04 |
| F1 MRAC | pitch | 0.26 / 0.06 | 0.07 | 0.06 | 0.17 | -0.48 / -1.20 |
| F1 PID | roll | 0.87 / -14.36 | -7.38 | -20.06 | -14.07 | -12.00 / -12.51 |
| F1 MRAC | roll | 0.17 / 0.19 | 0.24 | 0.18 | 0.03 | -6.65 / -10.98 |

## vp16 grid inputs: where the drone sits (centre placement, gamma T rule)

Each vp16 grid input in its normalised unit, p1 / p50 / p99 per segment. pitch/roll: rate / 200 deg/s, tilt / 15 deg, cross = other tilt / 15 deg. yaw: rate / 160 deg/s, heading error ~ gyrozPID.Des / 162 (= Des / Kp 6 / 27 deg, yawPID not logged), cross = Z thrust share. Z: climb rate / 1.0 m/s, thrust share u_nom / u_max, cross = tilt size / 15 deg. x/y: velocity / 300 cm/s, position error / 375 cm, cross = other velocity / 300. |e| = MRAC error (x/y: locxs/locys Des - FB, cm/s) against e_sat; > e_sat = share of time the tanh cap saturates.

| seg | axis | in1 p1 / p50 / p99 | in2 p1 / p50 / p99 | cross p1 / p50 / p99 | abs e p50 / p95 / p99 | e_sat | > e_sat (%) |
|---|---|---|---|---|---|---|---|
| F1 PID | pitch | -0.12 / -0.01 / +0.13 | -0.33 / -0.13 / +0.18 | -0.10 / -0.04 / +0.12 | 0.127 / 0.345 / 0.419 | 0.5 | 0.0 |
| F1 PID | roll | -0.09 / +0.00 / +0.08 | -0.10 / -0.04 / +0.12 | -0.33 / -0.13 / +0.18 | 0.104 / 0.249 / 0.306 | 0.5 | 0.0 |
| F1 PID | yaw | -0.09 / +0.01 / +0.09 | -0.05 / +0.04 / +0.15 | -0.04 / +0.10 / +0.14 | 0.101 / 0.273 / 0.315 | 0.7 | 0.0 |
| F1 PID | z_rate | -0.82 / +0.09 / +0.64 | -0.04 / +0.10 / +0.14 | +0.04 / +0.15 / +0.34 | 0.363 / 0.746 / 0.880 | 0.4 | 43.7 |
| F1 PID | x | -0.04 / +0.00 / +0.06 | -0.01 / +0.00 / +0.03 | -0.12 / -0.00 / +0.19 | 5.148 / 18.210 / 19.598 | 45 | 0.0 |
| F1 PID | y | -0.12 / -0.00 / +0.19 | -0.08 / -0.05 / +0.01 | -0.04 / +0.00 / +0.06 | 18.955 / 58.245 / 70.693 | 45 | 13.8 |
| F1 MRAC | pitch | -0.11 / -0.00 / +0.11 | -0.42 / -0.07 / +0.25 | -0.49 / -0.06 / +0.29 | 0.072 / 0.225 / 0.320 | 0.5 | 0.1 |
| F1 MRAC | roll | -0.11 / +0.00 / +0.11 | -0.49 / -0.06 / +0.29 | -0.42 / -0.07 / +0.25 | 0.067 / 0.202 / 0.269 | 0.5 | 0.0 |
| F1 MRAC | yaw | -0.14 / -0.00 / +0.14 | -0.14 / +0.00 / +0.18 | -0.00 / +0.10 / +0.17 | 0.078 / 0.236 / 0.326 | 0.7 | 0.1 |
| F1 MRAC | z_rate | -2.58 / -0.00 / +0.89 | -0.00 / +0.10 / +0.17 | +0.02 / +0.17 / +0.61 | 0.050 / 0.565 / 3.415 | 0.4 | 7.4 |
| F1 MRAC | x | -0.16 / +0.01 / +0.16 | -0.11 / +0.00 / +0.04 | -0.13 / -0.00 / +0.14 | 8.441 / 38.376 / 52.784 | 45 | 2.7 |
| F1 MRAC | y | -0.13 / -0.00 / +0.14 | -0.07 / -0.02 / +0.02 | -0.16 / +0.01 / +0.16 | 15.016 / 39.463 / 50.071 | 45 | 1.7 |

## Feature capability (all MRAC segments pooled)

Theta_i moves at gamma_i * s * phi_i / (1 + |phi|^2) (API/mrac.c:748, denom :932), so a feature's learning drive is gamma_i * E[phi_i^2 / (1 + |phi|^2)] (speed, shown relative to the bias). Its reach is lim_i * RMS(phi_i): the largest torque it can make with Theta_i at its projection bound. gamma_i, lim_i from the MRAC_BASIS rows (pitch/roll/yaw/z), mrac_g_phi and the preset g assumed 1.

| axis | feature | RMS phi | gamma | drive E[phi^2/(1+|phi|^2)] | speed vs bias | lim | reach lim*RMS phi | actual RMS Theta*phi | Theta0 63% time (s) per seg |
|---|---|---|---|---|---|---|---|---|---|
| pitch | bias 1 | 1 | 1.50 | 0.485 | 1 | 0.150 | 0.15 | 0 | F1 - |
| pitch | x | 0.166 | 0.20 | 0.0129 | 0.0035 | 0.050 | 0.00831 | 0 |  |
| pitch | x tanh x | 0.0465 | 0.05 | 0.00097 | 6.7e-05 | 0.020 | 0.000931 | 0 |  |
| pitch | cross | 0.0245 | 0.05 | 0.000286 | 2e-05 | 0.050 | 0.00122 | 0 |  |
| pitch | u_nom | 0.103 | 0.10 | 0.0051 | 0.0007 | 0.200 | 0.0206 | 0.000155 |  |
| pitch | xm | 0.15 | 0.10 | 0.0106 | 0.0015 | 0.150 | 0.0225 | 0.000234 |  |
| roll | bias 1 | 1 | 1.50 | 0.488 | 1 | 0.150 | 0.15 | 0 | F1 - |
| roll | x | 0.15 | 0.20 | 0.0103 | 0.0028 | 0.050 | 0.0075 | 0 |  |
| roll | x tanh x | 0.0448 | 0.05 | 0.000853 | 5.8e-05 | 0.020 | 0.000897 | 0 |  |
| roll | cross | 0.0319 | 0.05 | 0.000495 | 3.4e-05 | 0.050 | 0.0016 | 0 |  |
| roll | u_nom | 0.0863 | 0.10 | 0.00361 | 0.00049 | 0.200 | 0.0173 | 0.000118 |  |
| roll | xm | 0.135 | 0.10 | 0.0083 | 0.0011 | 0.150 | 0.0202 | 0.000192 |  |
| yaw | bias 1 | 1 | 1.00 | 0.49 | 1 | 0.090 | 0.09 | 0.0182 | F1 4 |
| yaw | x | 0.166 | 0.10 | 0.0124 | 0.0025 | 0.030 | 0.00498 | 9e-06 |  |
| yaw | x tanh x | 0.062 | 0.05 | 0.00143 | 0.00015 | 0.012 | 0.000745 | 2.8e-05 |  |
| yaw | cross | 0 | 0.05 | 0 | 0 | 0.030 | 0 | 0 |  |
| yaw | u_nom | 0.0622 | 0.10 | 0.00187 | 0.00038 | 0.120 | 0.00746 | 0.000125 |  |
| yaw | xm | 0.0899 | 0.10 | 0.00377 | 0.00077 | 0.090 | 0.0081 | 7.86e-05 |  |
| z_rate | bias 1 | 1 | 2.00 | 0.254 | 1 | 1.000 | 1 | 0.179 | F1 1 |
| z_rate | x | 0.754 | 0.50 | 0.0161 | 0.016 | 0.100 | 0.0754 | 0.000436 |  |
| z_rate | x tanh x | 0.738 | 0.10 | 0.0108 | 0.0021 | 0.050 | 0.0369 | 0.00102 |  |
| z_rate | cross | 0 | 0.10 | 0 | 0 | 0.050 | 0 | 0 |  |
| z_rate | u_nom | 1.4 | 0.20 | 0.46 | 0.18 | 0.200 | 0.28 | 0.106 |  |
| z_rate | xm | 0.131 | 0.20 | 0.00473 | 0.0019 | 0.200 | 0.0261 | 0.00199 |  |

