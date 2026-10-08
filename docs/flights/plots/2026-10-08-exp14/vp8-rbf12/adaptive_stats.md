# Adaptive-layer stats: `exp14_vp8-rbf12-load`

Measured from the log. Basis rebuilt from logged signals (see the tool docstring). Shaded: blue PID, orange MRAC injected.

## Per MRAC segment and axis

| seg | axis | rebuild r | u_ad RMS | u_nom RMS | ratio | r(u_ad,u_nom) | r(u_ad,x) | band phase u_ad/x (deg) | band phase u_nom/x | Re H u_ad / Re H u_nom | coh | top feature (RMS share) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| F1 MRAC | pitch | 1.00 | 0.0023 | 0.1033 | 0.02 | +0.17 | -0.11 | -71 | +131 | -0.02 | 0.42 | xm 88%, u_nom 12% |
| F1 MRAC | roll | 1.00 | 0.0017 | 0.0866 | 0.02 | -0.01 | -0.04 | +93 | +126 | 0.01 | 0.68 | xm 94%, u_nom 6% |
| F1 MRAC | yaw | 1.00 | 0.0083 | 0.1064 | 0.08 | -0.13 | +0.30 | -18 | +118 | -0.06 | 0.67 | bias 1 100%, u_nom 0% |
| F1 MRAC | z_rate | 1.00 | 0.0737 | 1.1205 | 0.07 | +0.22 | +0.06 | +60 | +135 | -0.04 | 0.87 | bias 1 94%, u_nom 6% |

Phase of a torque against the body rate x in 0.25-0.90 Hz: +/-180 = pure damping, 0 = anti-damping, +/-90 = no energy. Re H u_ad / Re H u_nom > 0: u_ad adds damping like the PID; < 0: removes it.

## What-if: same weights, other omega_u (open-loop replay of the logged raw sum)

Band phase u_ad/x (deg) / Re H ratio, as above. Closed loop would differ (the weights would evolve differently); this isolates the filter lag.

| seg | axis | flown | 10 rad/s | 15 rad/s | 20 rad/s |
|---|---|---|---|---|---|
| F1 MRAC | pitch | -75 / -0.02 | -58 / -0.03 | -54 / -0.04 | -52 / -0.04 |
| F1 MRAC | roll | +95 / +0.01 | +100 / +0.02 | +102 / +0.03 | +103 / +0.03 |

## Weights Theta (start -> end of each MRAC segment)

| seg | axis | Theta[0] bias 1 | Theta[1] x | Theta[2] x tanh x | Theta[3] cross | Theta[4] u_nom | Theta[5] xm | |Theta| growth |
|---|---|---|---|---|---|---|---|---|
| F1 MRAC | pitch | +0.000 -> +0.000 | +0.000 -> +0.000 | +0.000 -> +0.000 | +0.000 -> +0.000 | +0.000 -> +0.001 | +0.000 -> +0.002 | 0.000 -> 0.002 |
| F1 MRAC | roll | +0.000 -> +0.000 | +0.000 -> +0.000 | +0.000 -> +0.000 | +0.000 -> +0.000 | +0.000 -> +0.001 | +0.000 -> +0.001 | 0.000 -> 0.001 |
| F1 MRAC | yaw | +0.002 -> +0.009 | +0.000 -> +0.000 | +0.000 -> +0.001 | +0.000 -> +0.000 | +0.000 -> +0.002 | +0.000 -> +0.000 | 0.002 -> 0.009 |
| F1 MRAC | z_rate | +0.002 -> +0.025 | +0.000 -> +0.008 | +0.000 -> +0.001 | +0.000 -> +0.000 | +0.000 -> +0.024 | +0.000 -> +0.024 | 0.002 -> 0.043 |

## Ext block (vp basis 3, 12 Gaussians per axis, pitch/roll)

Grid inputs in the firmware's normalised units (rate, angle); the Gaussian centres span -1..1 (basis 5: -1.5..1.5 rate, -1..1 angle). Activity = sd / mean of the most varying Gaussian over the segment: near 0 the grid acts as one constant (a bias), not as a function of the state. Ext share = ext RMS^2 / (ext RMS^2 + S6-part RMS^2). Slope = d|Theta_ext|/dt over the last third (> 0: still learning).

| seg | axis | rate p1 / p50 / p99 | angle p1 / p50 / p99 | activity | ext RMS | S6 part RMS | ext share | |Theta_ext| start -> end | slope (/s) | top ext (RMS share) |
|---|---|---|---|---|---|---|---|---|---|---|
| F1 MRAC | pitch | -0.093 / -0.001 / +0.100 | -0.239 / -0.023 / +0.115 | 0.121 | 0.0029 | 0.0001 | 100% | 0.000 -> 0.010 | +0.0008 | e4 41%, e7 36% |
| F1 MRAC | roll | -0.093 / -0.001 / +0.088 | -0.180 / -0.037 / +0.145 | 0.138 | 0.0017 | 0.0001 | 99% | 0.000 -> 0.001 | +0.0002 | e7 47%, e4 30% |

## Static offsets per segment (drift)

Expected static torque of the load at the rope point: pitch 0.112 N m (x 2 cm), roll 0.391 N m (y 7 cm), mg = 5.59 N.

| seg | mean u_nom p / r | mean u_ad p / r | mean Theta[0] p / r | pitch Des - FB (deg) | roll Des - FB | locx Des-FB (cm) | locy Des-FB | locx/y PID U mean | x FB slope (cm/s) | y FB slope | x stick on (%) | x stick vel Des median (cm/s) | x vel FB mean, no stick (cm/s) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| F1 PID | -0.0746 / +0.0592 | +0.0000 / +0.0000 | +0.0000 / +0.0000 | +0.08 | -0.30 | -6.2 | -10.6 | -3.7 / -9.0 | +11.82 | +0.18 | 28 | +39 | +0.89 |
| F1 MRAC | -0.0751 / +0.0443 | -0.0002 / -0.0010 | +0.0000 / +0.0000 | +0.10 | +0.07 | -10.7 | -6.8 | -9.5 / -8.1 | +7.65 | +3.08 | 22 | +34 | -0.18 |

## Per segment: attitude, height, motors, battery

| seg | s | pitch sd | roll sd | rate sd p / r (deg/s) | band PSD p / r | z - z_des | motor at 4000 (%) | max motor spread | V mean |
|---|---|---|---|---|---|---|---|---|---|
| F1 PID | 13 | 1.64 | 3.22 | 11.2 / 13.6 | 4.8 / 21.7 | +0.056 | 0.3 | 1166 | 15.49 |
| F1 MRAC | 14 | 2.26 | 2.10 | 11.0 / 12.7 | 45.2 / 27.3 | +0.046 | 1.6 | 1515 | 15.36 |

## Uncertainty estimate: does u_ad match the disturbance?

Plant per axis: xdot = b (u_inj + Delta), u_inj = u_nom + (injected) u_ad. b and the delay tau are fitted on 2-8 Hz content of all airborne segments (above the load band; closed-loop fit, so b is biased: the cancel fraction is also given for b x0.7 and x1.4). Delta_hat = LPF3Hz(xdot)/b - LPF3Hz(u_inj(t - tau)) is everything the nominal model does not explain (load torque and swing, CG offset, motor mismatch, drag), in control units. A perfect adaptive layer gives u_ad = -Delta_hat.

- static: mean(-Delta_hat) is the trim the drone needs; u_ad share = mean(u_ad) / mean(-Delta_hat) (the PID integrator carries the rest)
- dynamic (means removed): cancel = 1 - var(u_ad + Delta_hat) / var(Delta_hat); 1 = cancels all, 0 = no help, < 0 = adds disturbance. Band gain / phase of u_ad against -Delta_hat in 0.25-0.90 Hz (ideal 1 / 0 deg).
- ideal learner: cancel of -LPF_w(Delta_hat), what a perfect estimator behind the firmware u_ad filter at w rad/s could do. PID segments: u_ad is the shadow (computed, not injected).

| axis | b | tau (ms) | r (2-8 Hz) |
|---|---|---|---|
| pitch | 61.1 | 40 | 0.64 |
| roll | 71.2 | 40 | 0.72 |
| yaw | 77.3 | 40 | 0.78 |
| z_rate | 12.0 | 40 | 0.77 |

| seg | axis | -Delta static | u_ad mean | u_nom mean | u_ad share | -Delta dyn RMS | u_ad dyn RMS | band gain / phase / coh | cancel b x0.7 / x1 / x1.4 | ideal w 0.5 / 1 / 2 / 4 / 8 / 16 |
|---|---|---|---|---|---|---|---|---|---|---|
| F1 PID | pitch | -0.0746 | 0 | -0.0746 | - | 0.0174 | 0 | - | - | -1.57 / -0.82 / -0.18 / +0.32 / +0.68 / +0.89 |
| F1 MRAC | pitch | -0.0769 | -0.0002 | -0.0751 | 0.00 | 0.0370 | 0.0023 | 0.00 / +4 / 0.23 | +0.03 / +0.05 / +0.06 | -7.01 / -3.87 / -1.63 / -0.31 / +0.42 / +0.80 |
| F1 PID | roll | +0.0597 | 0 | +0.0592 | - | 0.0161 | 0 | - | - | -0.89 / -0.25 / +0.20 / +0.53 / +0.75 / +0.90 |
| F1 MRAC | roll | +0.0443 | -0.0010 | +0.0443 | -0.02 | 0.0265 | 0.0014 | 0.03 / -112 / 0.76 | +0.00 / +0.01 / +0.01 | -10.83 / -6.21 / -2.93 / -0.98 / +0.13 / +0.70 |
| F1 PID | yaw | +0.0300 | 0 | +0.0300 | - | 0.0323 | 0 | - | - | +0.06 / +0.15 / +0.29 / +0.53 / +0.79 / +0.94 |
| F1 MRAC | yaw | +0.0334 | +0.0050 | +0.0281 | 0.15 | 0.0838 | 0.0066 | 0.05 / -129 / 0.78 | -0.02 / -0.01 / -0.01 | -0.03 / +0.03 / +0.15 / +0.40 / +0.73 / +0.93 |
| F1 PID | z_rate | +1.0864 | 0 | +1.0839 | - | 0.1472 | 0 | - | - | -0.84 / -0.18 / +0.33 / +0.64 / +0.82 / +0.93 |
| F1 MRAC | z_rate | +1.1163 | +0.0674 | +1.0447 | 0.06 | 0.3386 | 0.0296 | 0.06 / -73 / 0.91 | +0.05 / +0.05 / +0.05 | +0.31 / +0.42 / +0.54 / +0.68 / +0.83 / +0.94 |

## Load swing frequency

Pendulum: L = rope 33 cm + half bottle 10 cm = 0.43 m. Simple sqrt(g/L)/2pi = 0.76 Hz; drone free to move, sqrt(g (M+m) / (M L))/2pi = 0.95 Hz (M 988 g, m 570 g). Measured: peak of the body-rate PSD in 0.15-1.5 Hz.

| seg | pitch peak (Hz) | roll peak (Hz) |
|---|---|---|
| F1 PID | 0.31 | 0.31 |
| F1 MRAC | 0.57 | 0.86 |

## Outside-force feature: body accel x/y vs the disturbance

Body-frame accel x/y sees only non-thrust forces (rope pull, slosh, wind, walls): thrust is along body z. If the torque is lever arm x force, -Delta_hat = w * a_xy with ONE constant w for any load. Per segment, means removed, both LPF 3 Hz. r = correlation at lag 0; cancel = r^2 = share of the dynamic disturbance a constant weight removes; w in control units per g. Lead = lag (+-300 ms) with the best |r|, positive = accel leads the disturbance. S6 = best single feature of x, x tanh x, cross, xm (u_nom left out: -Delta_hat contains u_nom by construction), then those four together, then together + accel x/y (offline least squares, constant weights). The S6 columns are an UPPER BOUND, partly circular: x and xm together rebuild the PID P term that sits inside -Delta_hat. Accel is an independent sensor, so its columns are clean. Caveat: an IMU off the centre of gravity adds (angular accel x offset), a few mg here.

| seg | axis | acc X r / cancel / w | acc Y r / cancel / w | lead X / Y (ms) | best S6 one | S6 four | S6 four + acc |
|---|---|---|---|---|---|---|---|
| F1 PID | pitch | -0.28 / 0.08 / -0.085 | -0.17 / 0.03 / -0.052 | +100 (r -0.32) / -120 (r -0.20) | 0.13 | 0.29 | 0.35 |
| F1 MRAC | pitch | -0.06 / 0.00 / -0.026 | -0.19 / 0.04 / -0.067 | -120 (r -0.15) / +50 (r -0.23) | 0.05 | 0.42 | 0.42 |
| F1 PID | roll | -0.38 / 0.15 / -0.109 | -0.09 / 0.01 / -0.025 | +90 (r -0.42) / -300 (r +0.23) | 0.08 | 0.21 | 0.30 |
| F1 MRAC | roll | -0.24 / 0.06 / -0.079 | -0.42 / 0.18 / -0.105 | +50 (r -0.29) / +70 (r -0.62) | 0.04 | 0.06 | 0.18 |

## Feature capability (all MRAC segments pooled)

Theta_i moves at gamma_i * s * phi_i / (1 + |phi|^2) (API/mrac.c:748, denom :932), so a feature's learning drive is gamma_i * E[phi_i^2 / (1 + |phi|^2)] (speed, shown relative to the bias). Its reach is lim_i * RMS(phi_i): the largest torque it can make with Theta_i at its projection bound. gamma_i, lim_i from the MRAC_BASIS rows (pitch/roll/yaw/z), mrac_g_phi and the preset g assumed 1.

| axis | feature | RMS phi | gamma | drive E[phi^2/(1+|phi|^2)] | speed vs bias | lim | reach lim*RMS phi | actual RMS Theta*phi | Theta0 63% time (s) per seg |
|---|---|---|---|---|---|---|---|---|---|
| pitch | bias 1 | 1 | 1.50 | 0.481 | 1 | 0.150 | 0.15 | 0 | F1 - |
| pitch | x | 0.191 | 0.20 | 0.0165 | 0.0046 | 0.050 | 0.00957 | 0 |  |
| pitch | x tanh x | 0.0644 | 0.05 | 0.00175 | 0.00012 | 0.020 | 0.00129 | 0 |  |
| pitch | cross | 0.0655 | 0.05 | 0.00202 | 0.00014 | 0.050 | 0.00328 | 0 |  |
| pitch | u_nom | 0.103 | 0.10 | 0.0051 | 0.00071 | 0.200 | 0.0207 | 4.26e-05 |  |
| pitch | xm | 0.168 | 0.10 | 0.0127 | 0.0018 | 0.150 | 0.0252 | 0.000114 |  |
| roll | bias 1 | 1 | 1.50 | 0.474 | 1 | 0.150 | 0.15 | 0 | F1 - |
| roll | x | 0.222 | 0.20 | 0.0223 | 0.0063 | 0.050 | 0.0111 | 0 |  |
| roll | x tanh x | 0.0704 | 0.05 | 0.00219 | 0.00015 | 0.020 | 0.00141 | 0 |  |
| roll | cross | 0.0594 | 0.05 | 0.00163 | 0.00011 | 0.050 | 0.00297 | 0 |  |
| roll | u_nom | 0.0866 | 0.10 | 0.00354 | 0.0005 | 0.200 | 0.0173 | 3.49e-05 |  |
| roll | xm | 0.215 | 0.10 | 0.0213 | 0.003 | 0.150 | 0.0323 | 0.000143 |  |
| yaw | bias 1 | 1 | 1.00 | 0.473 | 1 | 0.090 | 0.09 | 0.00894 | F1 1 |
| yaw | x | 0.283 | 0.10 | 0.0363 | 0.0077 | 0.030 | 0.0085 | 3.03e-06 |  |
| yaw | x tanh x | 0.104 | 0.05 | 0.00474 | 0.0005 | 0.012 | 0.00124 | 4.12e-05 |  |
| yaw | cross | 0 | 0.05 | 0 | 0 | 0.030 | 0 | 0 |  |
| yaw | u_nom | 0.106 | 0.10 | 0.00529 | 0.0011 | 0.120 | 0.0128 | 0.000157 |  |
| yaw | xm | 0.123 | 0.10 | 0.00688 | 0.0015 | 0.090 | 0.0111 | 2.02e-05 |  |
| z_rate | bias 1 | 1 | 2.00 | 0.308 | 1 | 1.000 | 1 | 0.0608 | F1 1 |
| z_rate | x | 0.231 | 0.50 | 0.0122 | 0.0099 | 0.100 | 0.0231 | 0.00101 |  |
| z_rate | x tanh x | 0.133 | 0.10 | 0.00317 | 0.00052 | 0.050 | 0.00665 | 0.000124 |  |
| z_rate | cross | 0 | 0.10 | 0 | 0 | 0.050 | 0 | 0 |  |
| z_rate | u_nom | 1.12 | 0.20 | 0.349 | 0.11 | 0.200 | 0.224 | 0.0152 |  |
| z_rate | xm | 0.247 | 0.20 | 0.0203 | 0.0066 | 0.200 | 0.0494 | 0.00335 |  |

