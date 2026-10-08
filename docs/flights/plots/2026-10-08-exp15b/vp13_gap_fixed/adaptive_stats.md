# Adaptive-layer stats: `vp13_gap`

Measured from the log. Basis rebuilt from logged signals (see the tool docstring). Shaded: blue PID, orange MRAC injected.

## Per MRAC segment and axis

| seg | axis | rebuild r | u_ad RMS | u_nom RMS | ratio | r(u_ad,u_nom) | r(u_ad,x) | band phase u_ad/x (deg) | band phase u_nom/x | Re H u_ad / Re H u_nom | coh | top feature (RMS share) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| F1 MRAC | pitch | 1.00 | 0.0223 | 0.0857 | 0.26 | -0.04 | +0.03 | +85 | +180 | -0.01 | 0.23 | xm 87%, u_nom 13% |
| F1 MRAC | roll | 1.00 | 0.0075 | 0.0663 | 0.11 | +0.00 | +0.02 | +66 | +166 | -0.04 | 0.39 | xm 62%, u_nom 38% |
| F1 MRAC | yaw | 1.00 | 0.0271 | 0.0473 | 0.57 | -0.10 | +0.09 | -29 | +111 | -0.09 | 0.49 | bias 1 100%, xm 0% |
| F1 MRAC | z_rate | 0.67 | 0.6101 | 1.4462 | 0.42 | +0.16 | -0.00 | -103 | +162 | 0.07 | 0.20 | bias 1 85%, u_nom 15% |

Phase of a torque against the body rate x in 0.25-0.90 Hz: +/-180 = pure damping, 0 = anti-damping, +/-90 = no energy. Re H u_ad / Re H u_nom > 0: u_ad adds damping like the PID; < 0: removes it.

## What-if: same weights, other omega_u (open-loop replay of the logged raw sum)

Band phase u_ad/x (deg) / Re H ratio, as above. Closed loop would differ (the weights would evolve differently); this isolates the filter lag.

| seg | axis | flown | 10 rad/s | 15 rad/s | 20 rad/s |
|---|---|---|---|---|---|
| F1 MRAC | pitch | +88 / -0.01 | +109 / +0.06 | +114 / +0.08 | +117 / +0.09 |
| F1 MRAC | roll | +66 / -0.04 | +80 / -0.02 | +86 / -0.01 | +88 / -0.00 |

## Weights Theta (start -> end of each MRAC segment)

| seg | axis | Theta[0] bias 1 | Theta[1] x | Theta[2] x tanh x | Theta[3] cross | Theta[4] u_nom | Theta[5] xm | |Theta| growth |
|---|---|---|---|---|---|---|---|---|
| F1 MRAC | pitch | +0.000 -> +0.000 | +0.000 -> +0.000 | +0.000 -> +0.000 | +0.000 -> +0.000 | +0.000 -> +0.002 | +0.000 -> +0.004 | 0.000 -> 0.004 |
| F1 MRAC | roll | +0.000 -> +0.000 | +0.000 -> +0.000 | +0.000 -> +0.000 | +0.000 -> +0.000 | +0.000 -> +0.001 | +0.000 -> +0.001 | 0.000 -> 0.002 |
| F1 MRAC | yaw | +0.000 -> -0.007 | +0.000 -> +0.000 | +0.000 -> +0.000 | +0.000 -> +0.000 | +0.000 -> +0.002 | +0.000 -> +0.004 | 0.000 -> 0.009 |
| F1 MRAC | z_rate | +0.000 -> +0.269 | +0.000 -> +0.006 | +0.000 -> +0.004 | +0.000 -> +0.000 | +0.000 -> +0.180 | +0.000 -> +0.051 | 0.000 -> 0.328 |

## Ext block (vp basis 7, 24 Gaussians per axis, pitch/roll)

Grid inputs in the firmware's normalised units (rate, angle); the Gaussian centres span -1..1 (basis 5: -1.5..1.5 rate, -1..1 angle). Activity = sd / mean of the most varying Gaussian over the segment: near 0 the grid acts as one constant (a bias), not as a function of the state. Ext share = ext RMS^2 / (ext RMS^2 + S6-part RMS^2). Slope = d|Theta_ext|/dt over the last third (> 0: still learning).

| seg | axis | rate p1 / p50 / p99 | angle p1 / p50 / p99 | activity | ext RMS | S6 part RMS | ext share | |Theta_ext| start -> end | slope (/s) | top ext (RMS share) |
|---|---|---|---|---|---|---|---|---|---|---|
| F1 MRAC | pitch | -0.143 / +0.000 / +0.122 | -0.593 / -0.066 / +0.424 | 0.708 | 0.0223 | 0.0003 | 100% | 0.000 -> 0.008 | +0.0001 | e13 32%, e9 19% |
| F1 MRAC | roll | -0.110 / -0.000 / +0.124 | -0.630 / -0.039 / +0.563 | 0.638 | 0.0075 | 0.0001 | 100% | 0.000 -> 0.006 | +0.0001 | e9 39%, e10 18% |

## Static offsets per segment (drift)

Expected static torque of the load at the rope point: pitch 0.112 N m (x 2 cm), roll 0.391 N m (y 7 cm), mg = 5.59 N.

| seg | mean u_nom p / r | mean u_ad p / r | mean Theta[0] p / r | pitch Des - FB (deg) | roll Des - FB | locx Des-FB (cm) | locy Des-FB | locx/y PID U mean | x FB slope (cm/s) | y FB slope | x stick on (%) | x stick vel Des median (cm/s) | x vel FB mean, no stick (cm/s) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| F1 MRAC | -0.0563 / +0.0241 | -0.0215 / +0.0071 | +0.0000 / +0.0000 | +0.02 | -0.06 | -0.3 | -4.1 | +1.5 / -6.4 | -0.32 | -1.80 | 8 | -21 | +0.48 |

## Per segment: attitude, height, motors, battery

| seg | s | pitch sd | roll sd | rate sd p / r (deg/s) | band PSD p / r | z - z_des | motor at 4000 (%) | max motor spread | V mean |
|---|---|---|---|---|---|---|---|---|---|
| F1 MRAC | 52 | 2.63 | 2.59 | 10.1 / 8.6 | 28.4 / 26.6 | +0.022 | 3.6 | 1509 | 14.39 |

## Uncertainty estimate: does u_ad match the disturbance?

Plant per axis: xdot = b (u_inj + Delta), u_inj = u_nom + (injected) u_ad. b and the delay tau are fitted on 2-8 Hz content of all airborne segments (above the load band; closed-loop fit, so b is biased: the cancel fraction is also given for b x0.7 and x1.4). Delta_hat = LPF3Hz(xdot)/b - LPF3Hz(u_inj(t - tau)) is everything the nominal model does not explain (load torque and swing, CG offset, motor mismatch, drag), in control units. A perfect adaptive layer gives u_ad = -Delta_hat.

- static: mean(-Delta_hat) is the trim the drone needs; u_ad share = mean(u_ad) / mean(-Delta_hat) (the PID integrator carries the rest)
- dynamic (means removed): cancel = 1 - var(u_ad + Delta_hat) / var(Delta_hat); 1 = cancels all, 0 = no help, < 0 = adds disturbance. Band gain / phase of u_ad against -Delta_hat in 0.25-0.90 Hz (ideal 1 / 0 deg).
- ideal learner: cancel of -LPF_w(Delta_hat), what a perfect estimator behind the firmware u_ad filter at w rad/s could do. PID segments: u_ad is the shadow (computed, not injected).

| axis | b | tau (ms) | r (2-8 Hz) |
|---|---|---|---|
| pitch | 49.1 | 40 | 0.75 |
| roll | 46.3 | 40 | 0.70 |
| yaw | 86.3 | 30 | 0.59 |
| z_rate | 26.7 | 40 | 0.42 |

| seg | axis | -Delta static | u_ad mean | u_nom mean | u_ad share | -Delta dyn RMS | u_ad dyn RMS | band gain / phase / coh | cancel b x0.7 / x1 / x1.4 | ideal w 0.5 / 1 / 2 / 4 / 8 / 16 |
|---|---|---|---|---|---|---|---|---|---|---|
| F1 MRAC | pitch | -0.0781 | -0.0215 | -0.0563 | 0.28 | 0.0312 | 0.0058 | 0.05 / -128 / 0.27 | -0.02 / -0.01 / -0.01 | -0.13 / +0.07 / +0.25 / +0.46 / +0.72 / +0.90 |
| F1 MRAC | roll | +0.0312 | +0.0071 | +0.0241 | 0.23 | 0.0217 | 0.0023 | 0.04 / -153 / 0.36 | -0.00 / +0.00 / +0.01 | -0.62 / -0.18 / +0.13 / +0.42 / +0.69 / +0.88 |
| F1 MRAC | yaw | +0.0375 | +0.0212 | +0.0162 | 0.57 | 0.0417 | 0.0169 | 0.06 / -123 / 0.59 | +0.10 / +0.09 / +0.09 | +0.10 / +0.29 / +0.48 / +0.68 / +0.86 / +0.96 |
| F1 MRAC | z_rate | +1.9442 | +0.5687 | +1.3750 | 0.29 | 0.4456 | 0.2208 | 0.24 / -4 / 0.42 | +0.30 / +0.33 / +0.33 | +0.16 / +0.22 / +0.32 / +0.49 / +0.71 / +0.89 |

## Load swing frequency

Pendulum: L = rope 33 cm + half bottle 10 cm = 0.43 m. Simple sqrt(g/L)/2pi = 0.76 Hz; drone free to move, sqrt(g (M+m) / (M L))/2pi = 0.95 Hz (M 988 g, m 570 g). Measured: peak of the body-rate PSD in 0.15-1.5 Hz.

| seg | pitch peak (Hz) | roll peak (Hz) |
|---|---|---|
| F1 MRAC | 0.49 | 0.39 |

## Outside-force feature: body accel x/y vs the disturbance

Body-frame accel x/y sees only non-thrust forces (rope pull, slosh, wind, walls): thrust is along body z. If the torque is lever arm x force, -Delta_hat = w * a_xy with ONE constant w for any load. Per segment, means removed, both LPF 3 Hz. r = correlation at lag 0; cancel = r^2 = share of the dynamic disturbance a constant weight removes; w in control units per g. Lead = lag (+-300 ms) with the best |r|, positive = accel leads the disturbance. S6 = best single feature of x, x tanh x, cross, xm (u_nom left out: -Delta_hat contains u_nom by construction), then those four together, then together + accel x/y (offline least squares, constant weights). The S6 columns are an UPPER BOUND, partly circular: x and xm together rebuild the PID P term that sits inside -Delta_hat. Accel is an independent sensor, so its columns are clean. Caveat: an IMU off the centre of gravity adds (angular accel x offset), a few mg here.

| seg | axis | acc X r / cancel / w | acc Y r / cancel / w | lead X / Y (ms) | best S6 one | S6 four | S6 four + acc |
|---|---|---|---|---|---|---|---|
| F1 MRAC | pitch | -0.24 / 0.06 / -0.091 | +0.17 / 0.03 / +0.078 | +140 (r -0.34) / -100 (r +0.20) | 0.09 | 0.48 | 0.52 |
| F1 MRAC | roll | -0.05 / 0.00 / -0.014 | -0.03 / 0.00 / -0.010 | +90 (r -0.09) / +90 (r -0.07) | 0.14 | 0.46 | 0.46 |

## Feature capability (all MRAC segments pooled)

Theta_i moves at gamma_i * s * phi_i / (1 + |phi|^2) (API/mrac.c:748, denom :932), so a feature's learning drive is gamma_i * E[phi_i^2 / (1 + |phi|^2)] (speed, shown relative to the bias). Its reach is lim_i * RMS(phi_i): the largest torque it can make with Theta_i at its projection bound. gamma_i, lim_i from the MRAC_BASIS rows (pitch/roll/yaw/z), mrac_g_phi and the preset g assumed 1.

| axis | feature | RMS phi | gamma | drive E[phi^2/(1+|phi|^2)] | speed vs bias | lim | reach lim*RMS phi | actual RMS Theta*phi | Theta0 63% time (s) per seg |
|---|---|---|---|---|---|---|---|---|---|
| pitch | bias 1 | 1 | 1.50 | 0.485 | 1 | 0.150 | 0.15 | 0 | F1 - |
| pitch | x | 0.176 | 0.20 | 0.0137 | 0.0038 | 0.050 | 0.0088 | 0 |  |
| pitch | x tanh x | 0.063 | 0.05 | 0.00158 | 0.00011 | 0.020 | 0.00126 | 0 |  |
| pitch | cross | 0.0177 | 0.05 | 0.000142 | 9.7e-06 | 0.050 | 0.000885 | 0 |  |
| pitch | u_nom | 0.0857 | 0.10 | 0.00352 | 0.00048 | 0.200 | 0.0171 | 0.000115 |  |
| pitch | xm | 0.161 | 0.10 | 0.0117 | 0.0016 | 0.150 | 0.0242 | 0.000299 |  |
| roll | bias 1 | 1 | 1.50 | 0.49 | 1 | 0.150 | 0.15 | 0 | F1 - |
| roll | x | 0.149 | 0.20 | 0.0102 | 0.0028 | 0.050 | 0.00747 | 0 |  |
| roll | x tanh x | 0.0478 | 0.05 | 0.000969 | 6.6e-05 | 0.020 | 0.000956 | 0 |  |
| roll | cross | 0.024 | 0.05 | 0.000273 | 1.9e-05 | 0.050 | 0.0012 | 0 |  |
| roll | u_nom | 0.0663 | 0.10 | 0.00213 | 0.00029 | 0.200 | 0.0133 | 5.38e-05 |  |
| roll | xm | 0.116 | 0.10 | 0.00629 | 0.00086 | 0.150 | 0.0174 | 6.82e-05 |  |
| yaw | bias 1 | 1 | 1.00 | 0.494 | 1 | 0.090 | 0.09 | 0.0272 | F1 1 |
| yaw | x | 0.0999 | 0.10 | 0.00485 | 0.00098 | 0.030 | 0.003 | 2.85e-05 |  |
| yaw | x tanh x | 0.0176 | 0.05 | 0.000147 | 1.5e-05 | 0.012 | 0.000211 | 2.15e-06 |  |
| yaw | cross | 0 | 0.05 | 0 | 0 | 0.030 | 0 | 0 |  |
| yaw | u_nom | 0.0473 | 0.10 | 0.00109 | 0.00022 | 0.120 | 0.00568 | 6.41e-05 |  |
| yaw | xm | 0.106 | 0.10 | 0.00549 | 0.0011 | 0.090 | 0.00958 | 0.000256 |  |
| z_rate | bias 1 | 1 | 2.00 | 0.245 | 1 | 1.000 | 1 | 0.458 | F1 3 |
| z_rate | x | 0.927 | 0.50 | 0.0262 | 0.027 | 0.100 | 0.0927 | 0.00291 |  |
| z_rate | x tanh x | 0.904 | 0.10 | 0.0175 | 0.0036 | 0.050 | 0.0452 | 0.00243 |  |
| z_rate | cross | 0 | 0.10 | 0 | 0 | 0.050 | 0 | 0 |  |
| z_rate | u_nom | 1.45 | 0.20 | 0.452 | 0.18 | 0.200 | 0.289 | 0.193 |  |
| z_rate | xm | 0.249 | 0.20 | 0.0138 | 0.0056 | 0.200 | 0.0499 | 0.00579 |  |

