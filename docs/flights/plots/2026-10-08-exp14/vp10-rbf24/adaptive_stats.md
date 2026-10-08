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

## Static offsets per segment (drift)

Expected static torque of the load at the rope point: pitch 0.112 N m (x 2 cm), roll 0.391 N m (y 7 cm), mg = 5.59 N.

| seg | mean u_nom p / r | mean u_ad p / r | mean Theta[0] p / r | pitch Des - FB (deg) | roll Des - FB | locx Des-FB (cm) | locy Des-FB | locx/y PID U mean | x FB slope (cm/s) | y FB slope | x stick on (%) | x stick vel Des median (cm/s) | x vel FB mean, no stick (cm/s) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| F1 PID | -0.0676 / +0.0536 | -0.0252 / +0.0221 | +0.0000 / +0.0000 | -0.29 | -0.36 | +2.3 | -15.8 | +3.4 / -12.8 | +1.57 | +3.37 | 0 | +nan | +2.24 |
| F1 MRAC | -0.0760 / +0.0536 | -0.0044 / -0.0001 | +0.0000 / +0.0000 | +0.05 | -0.10 | -0.6 | -7.1 | +1.0 / -9.6 | +0.94 | -0.15 | 5 | +32 | +0.57 |

## Per segment: attitude, height, motors, battery

| seg | s | pitch sd | roll sd | rate sd p / r (deg/s) | band PSD p / r | z - z_des | motor at 4000 (%) | max motor spread | V mean |
|---|---|---|---|---|---|---|---|---|---|
| F1 PID | 6 | 1.90 | 0.73 | 12.8 / 7.2 | 22.6 / 1.7 | +0.039 | 0.0 | 1056 | 15.10 |
| F1 MRAC | 63 | 1.91 | 2.08 | 9.5 / 8.6 | 18.5 / 19.9 | +0.004 | 1.4 | 1142 | 14.74 |

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

