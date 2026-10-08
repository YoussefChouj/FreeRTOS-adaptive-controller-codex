# Adaptive-layer stats: `exp14_vp11-s6rbf12-load`

Measured from the log. Basis rebuilt from logged signals (see the tool docstring). Shaded: blue PID, orange MRAC injected.

## Per MRAC segment and axis

| seg | axis | rebuild r | u_ad RMS | u_nom RMS | ratio | r(u_ad,u_nom) | r(u_ad,x) | band phase u_ad/x (deg) | band phase u_nom/x | Re H u_ad / Re H u_nom | coh | top feature (RMS share) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| F1 MRAC | pitch | 1.00 | 0.0406 | 0.0728 | 0.56 | -0.22 | +0.05 | +150 | +101 | 2.02 | 0.15 | bias 1 100%, x 0% |
| F1 MRAC | roll | 1.00 | 0.0293 | 0.0709 | 0.41 | -0.06 | +0.00 | -9 | +167 | -0.12 | 0.30 | bias 1 100%, xm 0% |
| F1 MRAC | yaw | 1.00 | 0.0148 | 0.0526 | 0.28 | -0.17 | +0.10 | -11 | +117 | -0.05 | 0.62 | bias 1 100%, u_nom 0% |
| F1 MRAC | z_rate | 1.00 | 0.4888 | 1.0246 | 0.48 | -0.04 | -0.03 | +42 | +141 | -0.07 | 0.61 | bias 1 98%, u_nom 2% |
| F1 MRAC | pitch | 1.00 | 0.0324 | 0.0944 | 0.34 | -0.05 | +0.07 | -33 | +133 | -0.19 | 0.27 | bias 1 100%, xm 0% |
| F1 MRAC | roll | 1.00 | 0.0188 | 0.0860 | 0.22 | -0.05 | +0.15 | -2 | +148 | -0.18 | 0.33 | bias 1 100%, xm 0% |
| F1 MRAC | yaw | 1.00 | 0.0248 | 0.0584 | 0.43 | -0.09 | +0.10 | -11 | +116 | -0.09 | 0.57 | bias 1 100%, xm 0% |
| F1 MRAC | z_rate | 0.83 | 0.5160 | 1.4030 | 0.37 | +0.01 | +0.01 | -83 | -170 | -0.05 | 0.52 | bias 1 85%, u_nom 15% |

Phase of a torque against the body rate x in 0.25-0.90 Hz: +/-180 = pure damping, 0 = anti-damping, +/-90 = no energy. Re H u_ad / Re H u_nom > 0: u_ad adds damping like the PID; < 0: removes it.

## What-if: same weights, other omega_u (open-loop replay of the logged raw sum)

Band phase u_ad/x (deg) / Re H ratio, as above. Closed loop would differ (the weights would evolve differently); this isolates the filter lag.

| seg | axis | flown | 10 rad/s | 15 rad/s | 20 rad/s |
|---|---|---|---|---|---|
| F1 MRAC | pitch | +150 / +2.03 | +165 / +2.18 | +169 / +2.16 | +170 / +2.15 |
| F1 MRAC | roll | -9 / -0.11 | +5 / -0.13 | +11 / -0.13 | +14 / -0.13 |
| F1 MRAC | pitch | -32 / -0.19 | -11 / -0.27 | -5 / -0.29 | -2 / -0.29 |
| F1 MRAC | roll | -2 / -0.18 | +12 / -0.20 | +18 / -0.20 | +21 / -0.20 |

## Weights Theta (start -> end of each MRAC segment)

| seg | axis | Theta[0] bias 1 | Theta[1] x | Theta[2] x tanh x | Theta[3] cross | Theta[4] u_nom | Theta[5] xm | |Theta| growth |
|---|---|---|---|---|---|---|---|---|
| F1 MRAC | pitch | -0.000 -> -0.048 | +0.000 -> +0.000 | +0.000 -> +0.000 | +0.000 -> +0.000 | +0.000 -> +0.001 | +0.000 -> +0.001 | 0.000 -> 0.048 |
| F1 MRAC | roll | +0.000 -> +0.018 | +0.000 -> +0.000 | +0.000 -> +0.000 | +0.000 -> +0.000 | +0.000 -> +0.000 | +0.000 -> +0.000 | 0.000 -> 0.018 |
| F1 MRAC | yaw | +0.000 -> +0.006 | +0.000 -> +0.000 | +0.000 -> +0.000 | +0.000 -> +0.000 | +0.000 -> +0.001 | +0.000 -> +0.000 | 0.000 -> 0.006 |
| F1 MRAC | z_rate | +0.000 -> +0.578 | +0.000 -> +0.003 | +0.000 -> +0.001 | +0.000 -> +0.000 | +0.000 -> +0.084 | +0.000 -> +0.018 | 0.000 -> 0.585 |
| F1 MRAC | pitch | -0.002 -> +0.030 | +0.000 -> +0.002 | +0.000 -> +0.000 | +0.000 -> +0.000 | +0.000 -> +0.003 | +0.000 -> +0.004 | 0.002 -> 0.031 |
| F1 MRAC | roll | +0.002 -> -0.007 | +0.000 -> +0.002 | +0.000 -> +0.001 | +0.000 -> +0.000 | +0.000 -> +0.003 | +0.000 -> +0.004 | 0.002 -> 0.009 |
| F1 MRAC | yaw | -0.000 -> +0.015 | +0.000 -> +0.000 | +0.000 -> +0.000 | +0.000 -> +0.000 | +0.000 -> +0.004 | +0.000 -> +0.004 | 0.000 -> 0.016 |
| F1 MRAC | z_rate | +0.012 -> +0.094 | +0.000 -> +0.008 | +0.000 -> +0.003 | +0.000 -> +0.000 | +0.002 -> +0.158 | +0.000 -> +0.035 | 0.012 -> 0.187 |

## Ext block (vp basis 5, 12 Gaussians per axis, pitch/roll)

Grid inputs in the firmware's normalised units (rate, angle); the Gaussian centres span -1..1 (basis 5: -1.5..1.5 rate, -1..1 angle). Activity = sd / mean of the most varying Gaussian over the segment: near 0 the grid acts as one constant (a bias), not as a function of the state. Ext share = ext RMS^2 / (ext RMS^2 + S6-part RMS^2). Slope = d|Theta_ext|/dt over the last third (> 0: still learning).

| seg | axis | rate p1 / p50 / p99 | angle p1 / p50 / p99 | activity | ext RMS | S6 part RMS | ext share | |Theta_ext| start -> end | slope (/s) | top ext (RMS share) |
|---|---|---|---|---|---|---|---|---|---|---|
| F1 MRAC | pitch | -0.113 / -0.001 / +0.166 | -0.363 / -0.087 / +0.158 | 0.283 | 0.0039 | 0.0375 | 1% | 0.000 -> 0.004 | +0.0002 | e7 53%, e4 42% |
| F1 MRAC | roll | -0.132 / +0.003 / +0.102 | -0.449 / +0.001 / +0.335 | 0.352 | 0.0028 | 0.0267 | 1% | 0.000 -> 0.002 | -0.0004 | e4 53%, e7 43% |
| F1 MRAC | pitch | -0.131 / -0.001 / +0.148 | -0.548 / -0.055 / +0.331 | 0.382 | 0.0032 | 0.0294 | 1% | 0.000 -> 0.004 | -0.0000 | e7 67%, e4 21% |
| F1 MRAC | roll | -0.207 / +0.002 / +0.186 | -0.913 / -0.044 / +0.583 | 0.557 | 0.0019 | 0.0173 | 1% | 0.000 -> 0.006 | +0.0001 | e4 38%, e3 24% |

## Static offsets per segment (drift)

Expected static torque of the load at the rope point: pitch 0.112 N m (x 2 cm), roll 0.391 N m (y 7 cm), mg = 5.59 N.

| seg | mean u_nom p / r | mean u_ad p / r | mean Theta[0] p / r | pitch Des - FB (deg) | roll Des - FB | locx Des-FB (cm) | locy Des-FB | locx/y PID U mean | x FB slope (cm/s) | y FB slope | x stick on (%) | x stick vel Des median (cm/s) | x vel FB mean, no stick (cm/s) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| F1 MRAC | -0.0369 / +0.0229 | -0.0383 / +0.0277 | -0.0356 / +0.0253 | +0.07 | -0.22 | -0.3 | -13.0 | +1.7 / -11.1 | +4.92 | -0.04 | 14 | +41 | -0.15 |
| F1 MRAC | -0.0632 / +0.0438 | -0.0312 / +0.0152 | -0.0280 / +0.0136 | +0.06 | -0.05 | -3.5 | -1.2 | -1.3 / -5.2 | +3.20 | +0.30 | 11 | +65 | +0.91 |

## Per segment: attitude, height, motors, battery

| seg | s | pitch sd | roll sd | rate sd p / r (deg/s) | band PSD p / r | z - z_des | motor at 4000 (%) | max motor spread | V mean |
|---|---|---|---|---|---|---|---|---|---|
| F1 MRAC | 13 | 1.62 | 2.13 | 8.1 / 8.4 | 9.0 / 26.5 | +0.016 | 0.1 | 991 | 14.82 |
| F1 MRAC | 63 | 2.43 | 3.95 | 9.6 / 13.0 | 29.2 / 84.3 | -0.002 | 5.1 | 1184 | 14.34 |

## Uncertainty estimate: does u_ad match the disturbance?

Plant per axis: xdot = b (u_inj + Delta), u_inj = u_nom + (injected) u_ad. b and the delay tau are fitted on 2-8 Hz content of all airborne segments (above the load band; closed-loop fit, so b is biased: the cancel fraction is also given for b x0.7 and x1.4). Delta_hat = LPF3Hz(xdot)/b - LPF3Hz(u_inj(t - tau)) is everything the nominal model does not explain (load torque and swing, CG offset, motor mismatch, drag), in control units. A perfect adaptive layer gives u_ad = -Delta_hat.

- static: mean(-Delta_hat) is the trim the drone needs; u_ad share = mean(u_ad) / mean(-Delta_hat) (the PID integrator carries the rest)
- dynamic (means removed): cancel = 1 - var(u_ad + Delta_hat) / var(Delta_hat); 1 = cancels all, 0 = no help, < 0 = adds disturbance. Band gain / phase of u_ad against -Delta_hat in 0.25-0.90 Hz (ideal 1 / 0 deg).
- ideal learner: cancel of -LPF_w(Delta_hat), what a perfect estimator behind the firmware u_ad filter at w rad/s could do. PID segments: u_ad is the shadow (computed, not injected).

| axis | b | tau (ms) | r (2-8 Hz) |
|---|---|---|---|
| pitch | 60.0 | 50 | 0.80 |
| roll | 57.2 | 50 | 0.78 |
| yaw | 86.6 | 40 | 0.75 |
| z_rate | 26.3 | 30 | 0.45 |

| seg | axis | -Delta static | u_ad mean | u_nom mean | u_ad share | -Delta dyn RMS | u_ad dyn RMS | band gain / phase / coh | cancel b x0.7 / x1 / x1.4 | ideal w 0.5 / 1 / 2 / 4 / 8 / 16 |
|---|---|---|---|---|---|---|---|---|---|---|
| F1 MRAC | pitch | -0.0751 | -0.0383 | -0.0369 | 0.51 | 0.0233 | 0.0136 | 0.04 / -165 / 0.21 | -0.24 / -0.31 / -0.35 | +0.13 / +0.20 / +0.31 / +0.47 / +0.69 / +0.88 |
| F1 MRAC | pitch | -0.0944 | -0.0312 | -0.0632 | 0.33 | 0.0276 | 0.0089 | 0.11 / -140 / 0.26 | +0.03 / +0.03 / +0.03 | +0.15 / +0.24 / +0.37 / +0.55 / +0.76 / +0.91 |
| F1 MRAC | roll | +0.0504 | +0.0277 | +0.0229 | 0.55 | 0.0235 | 0.0095 | 0.04 / -179 / 0.24 | +0.05 / +0.06 / +0.06 | -0.49 / -0.11 / +0.21 / +0.49 / +0.73 / +0.90 |
| F1 MRAC | roll | +0.0590 | +0.0152 | +0.0438 | 0.26 | 0.0246 | 0.0112 | 0.11 / -157 / 0.26 | +0.06 / +0.08 / +0.10 | +0.17 / +0.26 / +0.39 / +0.56 / +0.75 / +0.90 |
| F1 MRAC | yaw | +0.0277 | +0.0132 | +0.0139 | 0.48 | 0.0435 | 0.0068 | 0.04 / -122 / 0.68 | -0.04 / -0.03 / -0.03 | -0.43 / -0.19 / +0.10 / +0.44 / +0.77 / +0.94 |
| F1 MRAC | yaw | +0.0289 | +0.0181 | +0.0107 | 0.63 | 0.0511 | 0.0170 | 0.06 / -113 / 0.69 | +0.06 / +0.06 / +0.05 | +0.13 / +0.23 / +0.37 / +0.59 / +0.82 / +0.95 |
| F1 MRAC | z_rate | +1.4006 | +0.4339 | +0.9668 | 0.31 | 0.3369 | 0.2250 | 0.12 / -32 / 0.69 | +0.39 / +0.39 / +0.38 | +0.27 / +0.36 / +0.47 / +0.64 / +0.82 / +0.94 |
| F1 MRAC | z_rate | +1.8363 | +0.4817 | +1.3541 | 0.26 | 0.3480 | 0.1850 | 0.32 / +7 / 0.65 | +0.22 / +0.24 / +0.24 | +0.10 / +0.21 / +0.32 / +0.48 / +0.70 / +0.89 |

## Load swing frequency

Pendulum: L = rope 33 cm + half bottle 10 cm = 0.43 m. Simple sqrt(g/L)/2pi = 0.76 Hz; drone free to move, sqrt(g (M+m) / (M L))/2pi = 0.95 Hz (M 988 g, m 570 g). Measured: peak of the body-rate PSD in 0.15-1.5 Hz.

| seg | pitch peak (Hz) | roll peak (Hz) |
|---|---|---|
| F1 MRAC | 0.46 | 0.46 |
| F1 MRAC | 0.49 | 0.49 |

## Outside-force feature: body accel x/y vs the disturbance

Body-frame accel x/y sees only non-thrust forces (rope pull, slosh, wind, walls): thrust is along body z. If the torque is lever arm x force, -Delta_hat = w * a_xy with ONE constant w for any load. Per segment, means removed, both LPF 3 Hz. r = correlation at lag 0; cancel = r^2 = share of the dynamic disturbance a constant weight removes; w in control units per g. Lead = lag (+-300 ms) with the best |r|, positive = accel leads the disturbance. S6 = best single feature of x, x tanh x, cross, xm (u_nom left out: -Delta_hat contains u_nom by construction), then those four together, then together + accel x/y (offline least squares, constant weights). The S6 columns are an UPPER BOUND, partly circular: x and xm together rebuild the PID P term that sits inside -Delta_hat. Accel is an independent sensor, so its columns are clean. Caveat: an IMU off the centre of gravity adds (angular accel x offset), a few mg here.

| seg | axis | acc X r / cancel / w | acc Y r / cancel / w | lead X / Y (ms) | best S6 one | S6 four | S6 four + acc |
|---|---|---|---|---|---|---|---|
| F1 MRAC | pitch | -0.25 / 0.06 / -0.080 | -0.13 / 0.02 / -0.044 | +120 (r -0.35) / -90 (r -0.14) | 0.04 | 0.30 | 0.31 |
| F1 MRAC | pitch | -0.25 / 0.06 / -0.089 | -0.05 / 0.00 / -0.020 | +60 (r -0.27) / +300 (r +0.06) | 0.06 | 0.54 | 0.54 |
| F1 MRAC | roll | -0.31 / 0.10 / -0.100 | -0.22 / 0.05 / -0.079 | -110 (r -0.36) / +90 (r -0.34) | 0.14 | 0.29 | 0.37 |
| F1 MRAC | roll | -0.05 / 0.00 / -0.016 | -0.16 / 0.03 / -0.057 | +90 (r -0.07) / +70 (r -0.20) | 0.12 | 0.31 | 0.33 |

## Feature capability (all MRAC segments pooled)

Theta_i moves at gamma_i * s * phi_i / (1 + |phi|^2) (API/mrac.c:748, denom :932), so a feature's learning drive is gamma_i * E[phi_i^2 / (1 + |phi|^2)] (speed, shown relative to the bias). Its reach is lim_i * RMS(phi_i): the largest torque it can make with Theta_i at its projection bound. gamma_i, lim_i from the MRAC_BASIS rows (pitch/roll/yaw/z), mrac_g_phi and the preset g assumed 1.

| axis | feature | RMS phi | gamma | drive E[phi^2/(1+|phi|^2)] | speed vs bias | lim | reach lim*RMS phi | actual RMS Theta*phi | Theta0 63% time (s) per seg |
|---|---|---|---|---|---|---|---|---|---|
| pitch | bias 1 | 1 | 1.50 | 0.486 | 1 | 0.150 | 0.15 | 0.0309 | F1 2, F1 2 |
| pitch | x | 0.163 | 0.20 | 0.0119 | 0.0033 | 0.050 | 0.00815 | 0.000134 |  |
| pitch | x tanh x | 0.0566 | 0.05 | 0.00127 | 8.7e-05 | 0.020 | 0.00113 | 8.14e-06 |  |
| pitch | cross | 0.0334 | 0.05 | 0.000532 | 3.6e-05 | 0.050 | 0.00167 | 3.79e-06 |  |
| pitch | u_nom | 0.0911 | 0.10 | 0.00398 | 0.00055 | 0.200 | 0.0182 | 0.000154 |  |
| pitch | xm | 0.144 | 0.10 | 0.00949 | 0.0013 | 0.150 | 0.0216 | 0.000286 |  |
| roll | bias 1 | 1 | 1.50 | 0.48 | 1 | 0.150 | 0.15 | 0.0191 | F1 1, F1 1 |
| roll | x | 0.215 | 0.20 | 0.0198 | 0.0055 | 0.050 | 0.0107 | 0.000225 |  |
| roll | x tanh x | 0.0874 | 0.05 | 0.00294 | 0.0002 | 0.020 | 0.00175 | 2.89e-05 |  |
| roll | cross | 0.0242 | 0.05 | 0.000273 | 1.9e-05 | 0.050 | 0.00121 | 2.76e-06 |  |
| roll | u_nom | 0.0836 | 0.10 | 0.00331 | 0.00046 | 0.200 | 0.0167 | 0.000146 |  |
| roll | xm | 0.175 | 0.10 | 0.0132 | 0.0018 | 0.150 | 0.0262 | 0.000291 |  |
| yaw | bias 1 | 1 | 1.00 | 0.491 | 1 | 0.090 | 0.09 | 0.0236 | F1 1, F1 5 |
| yaw | x | 0.14 | 0.10 | 0.00948 | 0.0019 | 0.030 | 0.00421 | 3.11e-05 |  |
| yaw | x tanh x | 0.0304 | 0.05 | 0.00044 | 4.5e-05 | 0.012 | 0.000365 | 7.42e-06 |  |
| yaw | cross | 0 | 0.05 | 0 | 0 | 0.030 | 0 | 0 |  |
| yaw | u_nom | 0.0574 | 0.10 | 0.00161 | 0.00033 | 0.120 | 0.00689 | 0.000126 |  |
| yaw | xm | 0.107 | 0.10 | 0.00554 | 0.0011 | 0.090 | 0.00964 | 0.000265 |  |
| z_rate | bias 1 | 1 | 2.00 | 0.267 | 1 | 1.000 | 1 | 0.389 | F1 4, F1 1 |
| z_rate | x | 0.641 | 0.50 | 0.015 | 0.014 | 0.100 | 0.0641 | 0.000567 |  |
| z_rate | x tanh x | 0.62 | 0.10 | 0.00907 | 0.0017 | 0.050 | 0.031 | 0.00104 |  |
| z_rate | cross | 0 | 0.10 | 0 | 0 | 0.050 | 0 | 0 |  |
| z_rate | u_nom | 1.35 | 0.20 | 0.434 | 0.16 | 0.200 | 0.269 | 0.145 |  |
| z_rate | xm | 0.182 | 0.20 | 0.00847 | 0.0032 | 0.200 | 0.0363 | 0.00272 |  |

