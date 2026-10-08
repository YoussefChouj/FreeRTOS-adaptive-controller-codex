# Adaptive-layer stats: `vp12_fixed`

Measured from the log. Basis rebuilt from logged signals (see the tool docstring). Shaded: blue PID, orange MRAC injected.

## Per MRAC segment and axis

| seg | axis | rebuild r | u_ad RMS | u_nom RMS | ratio | r(u_ad,u_nom) | r(u_ad,x) | band phase u_ad/x (deg) | band phase u_nom/x | Re H u_ad / Re H u_nom | coh | top feature (RMS share) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| F1 MRAC | pitch | 0.30 | 0.0613 | 0.1086 | 0.56 | -0.40 | +0.42 | -67 | +128 | -0.71 | 0.57 | bias 1 100%, xm 0% |
| F1 MRAC | roll | 0.95 | 0.0300 | 0.0751 | 0.40 | -0.11 | -0.17 | +103 | -153 | 0.06 | 0.53 | bias 1 100%, x 0% |
| F1 MRAC | yaw | 1.00 | 0.0113 | 0.0441 | 0.26 | -0.08 | +0.06 | +110 | +126 | 0.05 | 0.88 | bias 1 100%, u_nom 0% |
| F1 MRAC | z_rate | 0.91 | 0.3336 | 1.3817 | 0.24 | -0.12 | +0.11 | +37 | +146 | -0.01 | 0.59 | bias 1 94%, u_nom 6% |

Phase of a torque against the body rate x in 0.25-0.90 Hz: +/-180 = pure damping, 0 = anti-damping, +/-90 = no energy. Re H u_ad / Re H u_nom > 0: u_ad adds damping like the PID; < 0: removes it.

## What-if: same weights, other omega_u (open-loop replay of the logged raw sum)

Band phase u_ad/x (deg) / Re H ratio, as above. Closed loop would differ (the weights would evolve differently); this isolates the filter lag.

| seg | axis | flown | 10 rad/s | 15 rad/s | 20 rad/s |
|---|---|---|---|---|---|
| F1 MRAC | pitch | +157 / +0.02 | -179 / +0.02 | -172 / +0.02 | -168 / +0.02 |
| F1 MRAC | roll | +59 / -0.06 | +74 / -0.03 | +80 / -0.02 | +83 / -0.01 |

## Weights Theta (start -> end of each MRAC segment)

| seg | axis | Theta[0] bias 1 | Theta[1] x | Theta[2] x tanh x | Theta[3] cross | Theta[4] u_nom | Theta[5] xm | |Theta| growth |
|---|---|---|---|---|---|---|---|---|
| F1 MRAC | pitch | -0.000 -> -0.039 | -0.000 -> -0.001 | +0.000 -> +0.000 | +0.000 -> +0.000 | +0.000 -> +0.001 | +0.000 -> +0.001 | 0.000 -> 0.039 |
| F1 MRAC | roll | +0.000 -> +0.037 | -0.000 -> -0.002 | +0.000 -> +0.000 | +0.000 -> +0.000 | +0.000 -> +0.001 | +0.000 -> +0.000 | 0.000 -> 0.037 |
| F1 MRAC | yaw | +0.000 -> +0.020 | +0.000 -> +0.000 | +0.000 -> +0.000 | +0.000 -> +0.000 | +0.000 -> +0.000 | +0.000 -> +0.000 | 0.000 -> 0.020 |
| F1 MRAC | z_rate | +0.000 -> +0.274 | +0.000 -> +0.004 | +0.000 -> +0.002 | +0.000 -> +0.000 | +0.000 -> +0.087 | +0.000 -> +0.027 | 0.000 -> 0.289 |

## Static offsets per segment (drift)

Expected static torque of the load at the rope point: pitch 0.112 N m (x 2 cm), roll 0.391 N m (y 7 cm), mg = 5.59 N.

| seg | mean u_nom p / r | mean u_ad p / r | mean Theta[0] p / r | pitch Des - FB (deg) | roll Des - FB | locx Des-FB (cm) | locy Des-FB | locx/y PID U mean | x FB slope (cm/s) | y FB slope | x stick on (%) | x stick vel Des median (cm/s) | x vel FB mean, no stick (cm/s) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| F1 MRAC | -0.0652 / +0.0316 | -0.0372 / +0.0279 | -0.0374 / +0.0282 | -0.77 | -0.39 | +1.4 | -7.9 | +3.1 / -6.4 | +6.73 | -1.45 | 15 | +69 | +1.24 |

## Per segment: attitude, height, motors, battery

| seg | s | pitch sd | roll sd | rate sd p / r (deg/s) | band PSD p / r | z - z_des | motor at 4000 (%) | max motor spread | V mean |
|---|---|---|---|---|---|---|---|---|---|
| F1 MRAC | 11 | 3.06 | 3.43 | 20.8 / 11.3 | 51.3 / 27.7 | -0.028 | 3.1 | 1258 | 14.88 |

## Uncertainty estimate: does u_ad match the disturbance?

Plant per axis: xdot = b (u_inj + Delta), u_inj = u_nom + (injected) u_ad. b and the delay tau are fitted on 2-8 Hz content of all airborne segments (above the load band; closed-loop fit, so b is biased: the cancel fraction is also given for b x0.7 and x1.4). Delta_hat = LPF3Hz(xdot)/b - LPF3Hz(u_inj(t - tau)) is everything the nominal model does not explain (load torque and swing, CG offset, motor mismatch, drag), in control units. A perfect adaptive layer gives u_ad = -Delta_hat.

- static: mean(-Delta_hat) is the trim the drone needs; u_ad share = mean(u_ad) / mean(-Delta_hat) (the PID integrator carries the rest)
- dynamic (means removed): cancel = 1 - var(u_ad + Delta_hat) / var(Delta_hat); 1 = cancels all, 0 = no help, < 0 = adds disturbance. Band gain / phase of u_ad against -Delta_hat in 0.25-0.90 Hz (ideal 1 / 0 deg).
- ideal learner: cancel of -LPF_w(Delta_hat), what a perfect estimator behind the firmware u_ad filter at w rad/s could do. PID segments: u_ad is the shadow (computed, not injected).

| axis | b | tau (ms) | r (2-8 Hz) |
|---|---|---|---|
| pitch | 50.8 | 40 | 0.76 |
| roll | 47.3 | 40 | 0.69 |
| yaw | 59.0 | 40 | 0.65 |
| z_rate | 29.6 | 40 | 0.45 |

| seg | axis | -Delta static | u_ad mean | u_nom mean | u_ad share | -Delta dyn RMS | u_ad dyn RMS | band gain / phase / coh | cancel b x0.7 / x1 / x1.4 | ideal w 0.5 / 1 / 2 / 4 / 8 / 16 |
|---|---|---|---|---|---|---|---|---|---|---|
| F1 MRAC | pitch | -0.0999 | -0.0372 | -0.0652 | 0.37 | 0.0598 | 0.0487 | 0.35 / +19 / 0.48 | +0.44 / +0.42 / +0.33 | -0.10 / -0.01 / +0.12 / +0.35 / +0.66 / +0.89 |
| F1 MRAC | roll | +0.0584 | +0.0279 | +0.0316 | 0.48 | 0.0347 | 0.0111 | 0.11 / -98 / 0.46 | +0.00 / +0.00 / +0.00 | -6.08 / -3.61 / -1.55 / -0.25 / +0.47 / +0.82 |
| F1 MRAC | yaw | +0.0243 | +0.0098 | +0.0147 | 0.40 | 0.0310 | 0.0058 | 0.10 / -44 / 0.80 | -0.02 / -0.01 / -0.00 | -0.02 / +0.09 / +0.26 / +0.50 / +0.78 / +0.94 |
| F1 MRAC | z_rate | +1.5031 | +0.2783 | +1.2184 | 0.19 | 0.5088 | 0.1839 | 0.03 / -14 / 0.56 | -0.01 / -0.00 / -0.00 | -0.10 / +0.02 / +0.18 / +0.41 / +0.69 / +0.88 |

## Load swing frequency

Pendulum: L = rope 33 cm + half bottle 10 cm = 0.43 m. Simple sqrt(g/L)/2pi = 0.76 Hz; drone free to move, sqrt(g (M+m) / (M L))/2pi = 0.95 Hz (M 988 g, m 570 g). Measured: peak of the body-rate PSD in 0.15-1.5 Hz.

| seg | pitch peak (Hz) | roll peak (Hz) |
|---|---|---|
| F1 MRAC | 0.90 | 0.36 |

## Outside-force feature: body accel x/y vs the disturbance

Body-frame accel x/y sees only non-thrust forces (rope pull, slosh, wind, walls): thrust is along body z. If the torque is lever arm x force, -Delta_hat = w * a_xy with ONE constant w for any load. Per segment, means removed, both LPF 3 Hz. r = correlation at lag 0; cancel = r^2 = share of the dynamic disturbance a constant weight removes; w in control units per g. Lead = lag (+-300 ms) with the best |r|, positive = accel leads the disturbance. S6 = best single feature of x, x tanh x, cross, xm (u_nom left out: -Delta_hat contains u_nom by construction), then those four together, then together + accel x/y (offline least squares, constant weights). The S6 columns are an UPPER BOUND, partly circular: x and xm together rebuild the PID P term that sits inside -Delta_hat. Accel is an independent sensor, so its columns are clean. Caveat: an IMU off the centre of gravity adds (angular accel x offset), a few mg here.

| seg | axis | acc X r / cancel / w | acc Y r / cancel / w | lead X / Y (ms) | best S6 one | S6 four | S6 four + acc |
|---|---|---|---|---|---|---|---|
| F1 MRAC | pitch | -0.67 / 0.45 / -0.264 | -0.00 / 0.00 / -0.004 | +70 (r -0.72) / +290 (r +0.33) | 0.09 | 0.13 | 0.68 |
| F1 MRAC | roll | +0.16 / 0.03 / +0.037 | -0.16 / 0.03 / -0.080 | -80 (r +0.20) / +70 (r -0.25) | 0.12 | 0.20 | 0.26 |

## Feature capability (all MRAC segments pooled)

Theta_i moves at gamma_i * s * phi_i / (1 + |phi|^2) (API/mrac.c:748, denom :932), so a feature's learning drive is gamma_i * E[phi_i^2 / (1 + |phi|^2)] (speed, shown relative to the bias). Its reach is lim_i * RMS(phi_i): the largest torque it can make with Theta_i at its projection bound. gamma_i, lim_i from the MRAC_BASIS rows (pitch/roll/yaw/z), mrac_g_phi and the preset g assumed 1.

| axis | feature | RMS phi | gamma | drive E[phi^2/(1+|phi|^2)] | speed vs bias | lim | reach lim*RMS phi | actual RMS Theta*phi | Theta0 63% time (s) per seg |
|---|---|---|---|---|---|---|---|---|---|
| pitch | bias 1 | 1 | 1.50 | 0.447 | 1 | 0.150 | 0.15 | 0.0399 | F1 2 |
| pitch | x | 0.364 | 0.20 | 0.0445 | 0.013 | 0.050 | 0.0182 | 0.00019 |  |
| pitch | x tanh x | 0.218 | 0.05 | 0.0137 | 0.001 | 0.020 | 0.00437 | 1.13e-05 |  |
| pitch | cross | 0.0235 | 0.05 | 0.000235 | 1.7e-05 | 0.050 | 0.00118 | 3.49e-07 |  |
| pitch | u_nom | 0.109 | 0.10 | 0.00513 | 0.00076 | 0.200 | 0.0217 | 5.06e-05 |  |
| pitch | xm | 0.338 | 0.10 | 0.0425 | 0.0063 | 0.150 | 0.0506 | 0.000257 |  |
| roll | bias 1 | 1 | 1.50 | 0.482 | 1 | 0.150 | 0.15 | 0.0299 | F1 2 |
| roll | x | 0.196 | 0.20 | 0.0176 | 0.0049 | 0.050 | 0.00982 | 0.000287 |  |
| roll | x tanh x | 0.0605 | 0.05 | 0.00162 | 0.00011 | 0.020 | 0.00121 | 4.21e-06 |  |
| roll | cross | 0.0431 | 0.05 | 0.000874 | 6e-05 | 0.050 | 0.00216 | 1.4e-06 |  |
| roll | u_nom | 0.0751 | 0.10 | 0.0027 | 0.00037 | 0.200 | 0.015 | 2.69e-05 |  |
| roll | xm | 0.171 | 0.10 | 0.0134 | 0.0019 | 0.150 | 0.0256 | 5.29e-05 |  |
| yaw | bias 1 | 1 | 1.00 | 0.495 | 1 | 0.090 | 0.09 | 0.0118 | F1 3 |
| yaw | x | 0.118 | 0.10 | 0.00681 | 0.0014 | 0.030 | 0.00354 | 4.86e-07 |  |
| yaw | x tanh x | 0.0212 | 0.05 | 0.000219 | 2.2e-05 | 0.012 | 0.000255 | 5.63e-07 |  |
| yaw | cross | 0 | 0.05 | 0 | 0 | 0.030 | 0 | 0 |  |
| yaw | u_nom | 0.0441 | 0.10 | 0.000957 | 0.00019 | 0.120 | 0.00529 | 8.36e-06 |  |
| yaw | xm | 0.0683 | 0.10 | 0.00229 | 0.00046 | 0.090 | 0.00615 | 5.64e-06 |  |
| z_rate | bias 1 | 1 | 2.00 | 0.249 | 1 | 1.000 | 1 | 0.281 | F1 4 |
| z_rate | x | 1.19 | 0.50 | 0.0479 | 0.048 | 0.100 | 0.119 | 0.00376 |  |
| z_rate | x tanh x | 1.15 | 0.10 | 0.0302 | 0.0061 | 0.050 | 0.0576 | 0.000804 |  |
| z_rate | cross | 0 | 0.10 | 0 | 0 | 0.050 | 0 | 0 |  |
| z_rate | u_nom | 1.38 | 0.20 | 0.379 | 0.15 | 0.200 | 0.276 | 0.0733 |  |
| z_rate | xm | 0.468 | 0.20 | 0.0444 | 0.018 | 0.200 | 0.0936 | 0.00536 |  |

