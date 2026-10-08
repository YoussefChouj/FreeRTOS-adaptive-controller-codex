# Adaptive-layer stats: `vp13_g4`

Measured from the log. Basis rebuilt from logged signals (see the tool docstring). Shaded: blue PID, orange MRAC injected.

## Per MRAC segment and axis

| seg | axis | rebuild r | u_ad RMS | u_nom RMS | ratio | r(u_ad,u_nom) | r(u_ad,x) | band phase u_ad/x (deg) | band phase u_nom/x | Re H u_ad / Re H u_nom | coh | top feature (RMS share) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| F1 MRAC | pitch | 1.00 | 0.0532 | 0.0892 | 0.60 | -0.10 | -0.08 | +123 | -174 | 0.16 | 0.20 | xm 78%, u_nom 22% |
| F1 MRAC | roll | 1.00 | 0.0253 | 0.0716 | 0.35 | -0.04 | -0.09 | +139 | -175 | 0.21 | 0.09 | xm 75%, u_nom 25% |
| F1 MRAC | yaw | 1.00 | 0.0174 | 0.0538 | 0.32 | -0.10 | -0.14 | +34 | +112 | -0.06 | 0.48 | bias 1 100%, xm 0% |
| F1 MRAC | z_rate | 0.78 | 0.6351 | 1.0369 | 0.61 | +0.07 | +0.15 | -60 | -160 | -0.30 | 0.28 | bias 1 93%, u_nom 7% |

Phase of a torque against the body rate x in 0.25-0.90 Hz: +/-180 = pure damping, 0 = anti-damping, +/-90 = no energy. Re H u_ad / Re H u_nom > 0: u_ad adds damping like the PID; < 0: removes it.

## What-if: same weights, other omega_u (open-loop replay of the logged raw sum)

Band phase u_ad/x (deg) / Re H ratio, as above. Closed loop would differ (the weights would evolve differently); this isolates the filter lag.

| seg | axis | flown | 10 rad/s | 15 rad/s | 20 rad/s |
|---|---|---|---|---|---|
| F1 MRAC | pitch | +124 / +0.17 | +139 / +0.32 | +145 / +0.38 | +149 / +0.41 |
| F1 MRAC | roll | +139 / +0.21 | +151 / +0.28 | +155 / +0.30 | +158 / +0.31 |

## Weights Theta (start -> end of each MRAC segment)

| seg | axis | Theta[0] bias 1 | Theta[1] x | Theta[2] x tanh x | Theta[3] cross | Theta[4] u_nom | Theta[5] xm | |Theta| growth |
|---|---|---|---|---|---|---|---|---|
| F1 MRAC | pitch | +0.000 -> +0.000 | +0.000 -> +0.000 | +0.000 -> +0.000 | +0.000 -> +0.000 | +0.000 -> +0.003 | +0.000 -> +0.003 | 0.000 -> 0.004 |
| F1 MRAC | roll | +0.000 -> +0.000 | +0.000 -> +0.000 | +0.000 -> +0.000 | +0.000 -> +0.000 | +0.000 -> +0.002 | +0.000 -> +0.002 | 0.000 -> 0.003 |
| F1 MRAC | yaw | -0.000 -> -0.009 | +0.000 -> +0.001 | +0.000 -> +0.001 | +0.000 -> +0.000 | +0.000 -> +0.003 | +0.000 -> +0.007 | 0.000 -> 0.012 |
| F1 MRAC | z_rate | +0.000 -> +0.173 | +0.000 -> +0.003 | +0.000 -> +0.003 | +0.000 -> +0.000 | +0.000 -> +0.176 | +0.000 -> +0.035 | 0.000 -> 0.249 |

## Ext block (vp basis 7, 24 Gaussians per axis, pitch/roll)

Grid inputs in the firmware's normalised units (rate, angle); the Gaussian centres span -1..1 (basis 5: -1.5..1.5 rate, -1..1 angle). Activity = sd / mean of the most varying Gaussian over the segment: near 0 the grid acts as one constant (a bias), not as a function of the state. Ext share = ext RMS^2 / (ext RMS^2 + S6-part RMS^2). Slope = d|Theta_ext|/dt over the last third (> 0: still learning).

| seg | axis | rate p1 / p50 / p99 | angle p1 / p50 / p99 | activity | ext RMS | S6 part RMS | ext share | |Theta_ext| start -> end | slope (/s) | top ext (RMS share) |
|---|---|---|---|---|---|---|---|---|---|---|
| F1 MRAC | pitch | -0.122 / -0.002 / +0.123 | -0.619 / -0.024 / +0.345 | 0.638 | 0.0533 | 0.0003 | 100% | 0.000 -> 0.027 | -0.0002 | e13 36%, e14 20% |
| F1 MRAC | roll | -0.140 / +0.000 / +0.117 | -0.600 / -0.009 / +0.548 | 0.647 | 0.0255 | 0.0002 | 100% | 0.000 -> 0.023 | +0.0002 | e9 34%, e10 31% |

## Static offsets per segment (drift)

Expected static torque of the load at the rope point: pitch 0.112 N m (x 2 cm), roll 0.391 N m (y 7 cm), mg = 5.59 N.

| seg | mean u_nom p / r | mean u_ad p / r | mean Theta[0] p / r | pitch Des - FB (deg) | roll Des - FB | locx Des-FB (cm) | locy Des-FB | locx/y PID U mean | x FB slope (cm/s) | y FB slope | x stick on (%) | x stick vel Des median (cm/s) | x vel FB mean, no stick (cm/s) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| F1 MRAC | -0.0553 / +0.0269 | -0.0515 / +0.0234 | +0.0000 / +0.0000 | +0.02 | -0.01 | +0.1 | -2.6 | +2.0 / -2.5 | +1.00 | -1.25 | 14 | +33 | -0.04 |

## Per segment: attitude, height, motors, battery

| seg | s | pitch sd | roll sd | rate sd p / r (deg/s) | band PSD p / r | z - z_des | motor at 4000 (%) | max motor spread | V mean |
|---|---|---|---|---|---|---|---|---|---|
| F1 MRAC | 65 | 2.68 | 2.79 | 9.8 / 9.4 | 19.8 / 25.6 | -0.002 | 2.3 | 1466 | 15.06 |

## Uncertainty estimate: does u_ad match the disturbance?

Plant per axis: xdot = b (u_inj + Delta), u_inj = u_nom + (injected) u_ad. b and the delay tau are fitted on 2-8 Hz content of all airborne segments (above the load band; closed-loop fit, so b is biased: the cancel fraction is also given for b x0.7 and x1.4). Delta_hat = LPF3Hz(xdot)/b - LPF3Hz(u_inj(t - tau)) is everything the nominal model does not explain (load torque and swing, CG offset, motor mismatch, drag), in control units. A perfect adaptive layer gives u_ad = -Delta_hat.

- static: mean(-Delta_hat) is the trim the drone needs; u_ad share = mean(u_ad) / mean(-Delta_hat) (the PID integrator carries the rest)
- dynamic (means removed): cancel = 1 - var(u_ad + Delta_hat) / var(Delta_hat); 1 = cancels all, 0 = no help, < 0 = adds disturbance. Band gain / phase of u_ad against -Delta_hat in 0.25-0.90 Hz (ideal 1 / 0 deg).
- ideal learner: cancel of -LPF_w(Delta_hat), what a perfect estimator behind the firmware u_ad filter at w rad/s could do. PID segments: u_ad is the shadow (computed, not injected).

| axis | b | tau (ms) | r (2-8 Hz) |
|---|---|---|---|
| pitch | 47.6 | 40 | 0.74 |
| roll | 46.1 | 40 | 0.73 |
| yaw | 41.5 | 20 | 0.78 |
| z_rate | 40.0 | 40 | 0.47 |

| seg | axis | -Delta static | u_ad mean | u_nom mean | u_ad share | -Delta dyn RMS | u_ad dyn RMS | band gain / phase / coh | cancel b x0.7 / x1 / x1.4 | ideal w 0.5 / 1 / 2 / 4 / 8 / 16 |
|---|---|---|---|---|---|---|---|---|---|---|
| F1 MRAC | pitch | -0.1069 | -0.0515 | -0.0553 | 0.48 | 0.0304 | 0.0132 | 0.10 / -83 / 0.22 | -0.06 / -0.06 / -0.05 | -1.29 / -0.50 / -0.02 / +0.34 / +0.66 / +0.87 |
| F1 MRAC | roll | +0.0502 | +0.0234 | +0.0269 | 0.47 | 0.0271 | 0.0097 | 0.07 / -91 / 0.16 | +0.02 / +0.04 / +0.06 | -0.20 / +0.05 / +0.25 / +0.47 / +0.71 / +0.89 |
| F1 MRAC | yaw | +0.0179 | +0.0066 | +0.0110 | 0.37 | 0.0399 | 0.0161 | 0.06 / -120 / 0.43 | +0.04 / +0.05 / +0.04 | +0.15 / +0.30 / +0.47 / +0.66 / +0.85 / +0.96 |
| F1 MRAC | z_rate | +1.5291 | +0.5952 | +0.9333 | 0.39 | 0.4586 | 0.2215 | 0.26 / +12 / 0.53 | +0.28 / +0.30 / +0.31 | +0.14 / +0.18 / +0.26 / +0.42 / +0.65 / +0.86 |

## Load swing frequency

Pendulum: L = rope 33 cm + half bottle 10 cm = 0.43 m. Simple sqrt(g/L)/2pi = 0.76 Hz; drone free to move, sqrt(g (M+m) / (M L))/2pi = 0.95 Hz (M 988 g, m 570 g). Measured: peak of the body-rate PSD in 0.15-1.5 Hz.

| seg | pitch peak (Hz) | roll peak (Hz) |
|---|---|---|
| F1 MRAC | 0.98 | 0.39 |

## Outside-force feature: body accel x/y vs the disturbance

Body-frame accel x/y sees only non-thrust forces (rope pull, slosh, wind, walls): thrust is along body z. If the torque is lever arm x force, -Delta_hat = w * a_xy with ONE constant w for any load. Per segment, means removed, both LPF 3 Hz. r = correlation at lag 0; cancel = r^2 = share of the dynamic disturbance a constant weight removes; w in control units per g. Lead = lag (+-300 ms) with the best |r|, positive = accel leads the disturbance. S6 = best single feature of x, x tanh x, cross, xm (u_nom left out: -Delta_hat contains u_nom by construction), then those four together, then together + accel x/y (offline least squares, constant weights). The S6 columns are an UPPER BOUND, partly circular: x and xm together rebuild the PID P term that sits inside -Delta_hat. Accel is an independent sensor, so its columns are clean. Caveat: an IMU off the centre of gravity adds (angular accel x offset), a few mg here.

| seg | axis | acc X r / cancel / w | acc Y r / cancel / w | lead X / Y (ms) | best S6 one | S6 four | S6 four + acc |
|---|---|---|---|---|---|---|---|
| F1 MRAC | pitch | -0.29 / 0.08 / -0.088 | +0.08 / 0.01 / +0.027 | +80 (r -0.33) / -40 (r +0.08) | 0.10 | 0.40 | 0.43 |
| F1 MRAC | roll | -0.11 / 0.01 / -0.031 | -0.18 / 0.03 / -0.052 | -110 (r -0.12) / +90 (r -0.23) | 0.07 | 0.33 | 0.34 |

## Feature capability (all MRAC segments pooled)

Theta_i moves at gamma_i * s * phi_i / (1 + |phi|^2) (API/mrac.c:748, denom :932), so a feature's learning drive is gamma_i * E[phi_i^2 / (1 + |phi|^2)] (speed, shown relative to the bias). Its reach is lim_i * RMS(phi_i): the largest torque it can make with Theta_i at its projection bound. gamma_i, lim_i from the MRAC_BASIS rows (pitch/roll/yaw/z), mrac_g_phi and the preset g assumed 1.

| axis | feature | RMS phi | gamma | drive E[phi^2/(1+|phi|^2)] | speed vs bias | lim | reach lim*RMS phi | actual RMS Theta*phi | Theta0 63% time (s) per seg |
|---|---|---|---|---|---|---|---|---|---|
| pitch | bias 1 | 1 | 1.50 | 0.485 | 1 | 0.150 | 0.15 | 0 | F1 - |
| pitch | x | 0.171 | 0.20 | 0.0132 | 0.0036 | 0.050 | 0.00857 | 0 |  |
| pitch | x tanh x | 0.0576 | 0.05 | 0.00134 | 9.2e-05 | 0.020 | 0.00115 | 0 |  |
| pitch | cross | 0.0541 | 0.05 | 0.00119 | 8.2e-05 | 0.050 | 0.0027 | 0 |  |
| pitch | u_nom | 0.0892 | 0.10 | 0.00381 | 0.00052 | 0.200 | 0.0178 | 0.000146 |  |
| pitch | xm | 0.149 | 0.10 | 0.01 | 0.0014 | 0.150 | 0.0223 | 0.000276 |  |
| roll | bias 1 | 1 | 1.50 | 0.487 | 1 | 0.150 | 0.15 | 0 | F1 - |
| roll | x | 0.165 | 0.20 | 0.0121 | 0.0033 | 0.050 | 0.00823 | 0 |  |
| roll | x tanh x | 0.0551 | 0.05 | 0.00123 | 8.4e-05 | 0.020 | 0.0011 | 0 |  |
| roll | cross | 0.0637 | 0.05 | 0.00159 | 0.00011 | 0.050 | 0.00318 | 0 |  |
| roll | u_nom | 0.0716 | 0.10 | 0.00247 | 0.00034 | 0.200 | 0.0143 | 8.74e-05 |  |
| roll | xm | 0.14 | 0.10 | 0.00888 | 0.0012 | 0.150 | 0.0211 | 0.000151 |  |
| yaw | bias 1 | 1 | 1.00 | 0.482 | 1 | 0.090 | 0.09 | 0.0179 | F1 1 |
| yaw | x | 0.261 | 0.10 | 0.0165 | 0.0034 | 0.030 | 0.00784 | 8.05e-05 |  |
| yaw | x tanh x | 0.2 | 0.05 | 0.0065 | 0.00068 | 0.012 | 0.0024 | 7.08e-05 |  |
| yaw | cross | 0 | 0.05 | 0 | 0 | 0.030 | 0 | 0 |  |
| yaw | u_nom | 0.0538 | 0.10 | 0.00135 | 0.00028 | 0.120 | 0.00646 | 9.87e-05 |  |
| yaw | xm | 0.215 | 0.10 | 0.0125 | 0.0026 | 0.090 | 0.0193 | 0.00103 |  |
| z_rate | bias 1 | 1 | 2.00 | 0.326 | 1 | 1.000 | 1 | 0.522 | F1 2 |
| z_rate | x | 1.26 | 0.50 | 0.0283 | 0.022 | 0.100 | 0.126 | 0.0012 |  |
| z_rate | x tanh x | 1.25 | 0.10 | 0.02 | 0.0031 | 0.050 | 0.0623 | 0.00219 |  |
| z_rate | cross | 0 | 0.10 | 0 | 0 | 0.050 | 0 | 0 |  |
| z_rate | u_nom | 1.04 | 0.20 | 0.291 | 0.089 | 0.200 | 0.207 | 0.143 |  |
| z_rate | xm | 0.186 | 0.20 | 0.00764 | 0.0023 | 0.200 | 0.0371 | 0.00287 |  |

