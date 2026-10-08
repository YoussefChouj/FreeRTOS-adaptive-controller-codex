# Adaptive-layer stats: `vp14_doll2`

Measured from the log. Basis rebuilt from logged signals (see the tool docstring). Shaded: blue PID, orange MRAC injected.

## Per MRAC segment and axis

| seg | axis | rebuild r | u_ad RMS | u_nom RMS | ratio | r(u_ad,u_nom) | r(u_ad,x) | band phase u_ad/x (deg) | band phase u_nom/x | Re H u_ad / Re H u_nom | coh | top feature (RMS share) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| F1 MRAC | pitch | 1.00 | 0.0317 | 0.0913 | 0.35 | -0.03 | -0.02 | +127 | +141 | 0.03 | 0.05 | xm 76%, u_nom 24% |
| F1 MRAC | roll | 1.00 | 0.0110 | 0.0705 | 0.16 | +0.02 | -0.07 | -174 | +163 | 0.05 | 0.12 | xm 90%, u_nom 10% |
| F1 MRAC | yaw | 1.00 | 0.0340 | 0.0541 | 0.63 | -0.05 | +0.12 | -0 | +116 | -0.06 | 0.51 | bias 1 100%, xm 0% |
| F1 MRAC | z_rate | 0.73 | 0.6360 | 1.4339 | 0.44 | +0.15 | +0.10 | -4 | +165 | -0.15 | 0.04 | bias 1 83%, u_nom 17% |

Phase of a torque against the body rate x in 0.25-0.90 Hz: +/-180 = pure damping, 0 = anti-damping, +/-90 = no energy. Re H u_ad / Re H u_nom > 0: u_ad adds damping like the PID; < 0: removes it.

## What-if: same weights, other omega_u (open-loop replay of the logged raw sum)

Band phase u_ad/x (deg) / Re H ratio, as above. Closed loop would differ (the weights would evolve differently); this isolates the filter lag.

| seg | axis | flown | 10 rad/s | 15 rad/s | 20 rad/s |
|---|---|---|---|---|---|
| F1 MRAC | pitch | +132 / +0.03 | +151 / +0.05 | +158 / +0.06 | +161 / +0.06 |
| F1 MRAC | roll | -174 / +0.05 | -160 / +0.06 | -154 / +0.06 | -151 / +0.06 |

## Weights Theta (start -> end of each MRAC segment)

| seg | axis | Theta[0] bias 1 | Theta[1] x | Theta[2] x tanh x | Theta[3] cross | Theta[4] u_nom | Theta[5] xm | |Theta| growth |
|---|---|---|---|---|---|---|---|---|
| F1 MRAC | pitch | +0.000 -> +0.000 | +0.000 -> +0.000 | +0.000 -> +0.000 | +0.000 -> +0.000 | +0.000 -> +0.002 | +0.000 -> +0.002 | 0.000 -> 0.003 |
| F1 MRAC | roll | +0.000 -> +0.000 | +0.000 -> +0.000 | +0.000 -> +0.000 | +0.000 -> +0.000 | +0.000 -> +0.001 | +0.000 -> +0.002 | 0.000 -> 0.002 |
| F1 MRAC | yaw | +0.000 -> -0.002 | +0.000 -> +0.001 | +0.000 -> +0.000 | +0.000 -> +0.000 | +0.000 -> +0.004 | +0.000 -> +0.006 | 0.000 -> 0.008 |
| F1 MRAC | z_rate | +0.000 -> +0.148 | +0.000 -> +0.006 | +0.000 -> +0.004 | +0.000 -> +0.000 | +0.000 -> +0.173 | +0.000 -> +0.046 | 0.000 -> 0.233 |

## Ext block (vp basis 8, 24 Gaussians per axis, pitch/roll)

Grid inputs in the firmware's normalised units (rate, angle); the Gaussian centres span -1..1 (basis 5: -1.5..1.5 rate, -1..1 angle). Activity = sd / mean of the most varying Gaussian over the segment: near 0 the grid acts as one constant (a bias), not as a function of the state. Ext share = ext RMS^2 / (ext RMS^2 + S6-part RMS^2). Slope = d|Theta_ext|/dt over the last third (> 0: still learning).

| seg | axis | rate p1 / p50 / p99 | angle p1 / p50 / p99 | activity | ext RMS | S6 part RMS | ext share | |Theta_ext| start -> end | slope (/s) | top ext (RMS share) |
|---|---|---|---|---|---|---|---|---|---|---|
| F1 MRAC | pitch | -0.135 / +0.000 / +0.124 | -0.529 / -0.062 / +0.539 | 0.479 | 0.0317 | 0.0002 | 100% | 0.000 -> 0.007 | -0.0000 | e8 13%, e14 13% |
| F1 MRAC | roll | -0.138 / +0.001 / +0.139 | -0.620 / -0.051 / +0.593 | 0.548 | 0.0110 | 0.0002 | 100% | 0.000 -> 0.005 | +0.0001 | e2 16%, e8 16% |

## Static offsets per segment (drift)

Expected static torque of the load at the rope point: pitch 0.112 N m (x 2 cm), roll 0.391 N m (y 7 cm), mg = 5.59 N.

| seg | mean u_nom p / r | mean u_ad p / r | mean Theta[0] p / r | pitch Des - FB (deg) | roll Des - FB | locx Des-FB (cm) | locy Des-FB | locx/y PID U mean | x FB slope (cm/s) | y FB slope | x stick on (%) | x stick vel Des median (cm/s) | x vel FB mean, no stick (cm/s) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| F1 MRAC | -0.0628 / +0.0229 | -0.0310 / +0.0097 | +0.0000 / +0.0000 | +0.08 | -0.03 | +0.4 | -1.4 | -0.1 / -4.5 | -0.84 | -1.25 | 12 | -14 | +0.71 |

## Per segment: attitude, height, motors, battery

| seg | s | pitch sd | roll sd | rate sd p / r (deg/s) | band PSD p / r | z - z_des | motor at 4000 (%) | max motor spread | V mean |
|---|---|---|---|---|---|---|---|---|---|
| F1 MRAC | 78 | 2.43 | 3.15 | 9.4 / 10.2 | 24.6 / 42.0 | +0.005 | 6.8 | 1362 | 13.75 |

## Uncertainty estimate: does u_ad match the disturbance?

Plant per axis: xdot = b (u_inj + Delta), u_inj = u_nom + (injected) u_ad. b and the delay tau are fitted on 2-8 Hz content of all airborne segments (above the load band; closed-loop fit, so b is biased: the cancel fraction is also given for b x0.7 and x1.4). Delta_hat = LPF3Hz(xdot)/b - LPF3Hz(u_inj(t - tau)) is everything the nominal model does not explain (load torque and swing, CG offset, motor mismatch, drag), in control units. A perfect adaptive layer gives u_ad = -Delta_hat.

- static: mean(-Delta_hat) is the trim the drone needs; u_ad share = mean(u_ad) / mean(-Delta_hat) (the PID integrator carries the rest)
- dynamic (means removed): cancel = 1 - var(u_ad + Delta_hat) / var(Delta_hat); 1 = cancels all, 0 = no help, < 0 = adds disturbance. Band gain / phase of u_ad against -Delta_hat in 0.25-0.90 Hz (ideal 1 / 0 deg).
- ideal learner: cancel of -LPF_w(Delta_hat), what a perfect estimator behind the firmware u_ad filter at w rad/s could do. PID segments: u_ad is the shadow (computed, not injected).

| axis | b | tau (ms) | r (2-8 Hz) |
|---|---|---|---|
| pitch | 48.0 | 50 | 0.73 |
| roll | 46.3 | 40 | 0.69 |
| yaw | 72.4 | 40 | 0.74 |
| z_rate | 24.8 | 40 | 0.35 |

| seg | axis | -Delta static | u_ad mean | u_nom mean | u_ad share | -Delta dyn RMS | u_ad dyn RMS | band gain / phase / coh | cancel b x0.7 / x1 / x1.4 | ideal w 0.5 / 1 / 2 / 4 / 8 / 16 |
|---|---|---|---|---|---|---|---|---|---|---|
| F1 MRAC | pitch | -0.0939 | -0.0310 | -0.0628 | 0.33 | 0.0302 | 0.0068 | 0.02 / -125 / 0.03 | +0.01 / +0.02 / +0.02 | +0.12 / +0.21 / +0.34 / +0.52 / +0.74 / +0.91 |
| F1 MRAC | roll | +0.0324 | +0.0097 | +0.0229 | 0.30 | 0.0290 | 0.0051 | 0.01 / -86 / 0.10 | +0.04 / +0.06 / +0.06 | -0.97 / -0.28 / +0.16 / +0.48 / +0.73 / +0.90 |
| F1 MRAC | yaw | +0.0460 | +0.0291 | +0.0169 | 0.63 | 0.0470 | 0.0176 | 0.05 / -109 / 0.61 | +0.12 / +0.11 / +0.10 | +0.21 / +0.32 / +0.47 / +0.67 / +0.86 / +0.96 |
| F1 MRAC | z_rate | +1.9953 | +0.6099 | +1.3844 | 0.31 | 0.4098 | 0.1802 | 0.25 / -16 / 0.50 | +0.25 / +0.30 / +0.33 | +0.17 / +0.23 / +0.33 / +0.49 / +0.69 / +0.87 |

## Load swing frequency

Pendulum: L = rope 33 cm + half bottle 10 cm = 0.43 m. Simple sqrt(g/L)/2pi = 0.76 Hz; drone free to move, sqrt(g (M+m) / (M L))/2pi = 0.95 Hz (M 988 g, m 570 g). Measured: peak of the body-rate PSD in 0.15-1.5 Hz.

| seg | pitch peak (Hz) | roll peak (Hz) |
|---|---|---|
| F1 MRAC | 0.49 | 0.49 |

## Outside-force feature: body accel x/y vs the disturbance

Body-frame accel x/y sees only non-thrust forces (rope pull, slosh, wind, walls): thrust is along body z. If the torque is lever arm x force, -Delta_hat = w * a_xy with ONE constant w for any load. Per segment, means removed, both LPF 3 Hz. r = correlation at lag 0; cancel = r^2 = share of the dynamic disturbance a constant weight removes; w in control units per g. Lead = lag (+-300 ms) with the best |r|, positive = accel leads the disturbance. S6 = best single feature of x, x tanh x, cross, xm (u_nom left out: -Delta_hat contains u_nom by construction), then those four together, then together + accel x/y (offline least squares, constant weights). The S6 columns are an UPPER BOUND, partly circular: x and xm together rebuild the PID P term that sits inside -Delta_hat. Accel is an independent sensor, so its columns are clean. Caveat: an IMU off the centre of gravity adds (angular accel x offset), a few mg here.

| seg | axis | acc X r / cancel / w | acc Y r / cancel / w | lead X / Y (ms) | best S6 one | S6 four | S6 four + acc |
|---|---|---|---|---|---|---|---|
| F1 MRAC | pitch | -0.20 / 0.04 / -0.081 | +0.00 / 0.00 / +0.002 | +110 (r -0.25) / +200 (r -0.03) | 0.06 | 0.45 | 0.46 |
| F1 MRAC | roll | -0.09 / 0.01 / -0.034 | -0.11 / 0.01 / -0.043 | +90 (r -0.13) / +90 (r -0.16) | 0.06 | 0.40 | 0.40 |

## Feature capability (all MRAC segments pooled)

Theta_i moves at gamma_i * s * phi_i / (1 + |phi|^2) (API/mrac.c:748, denom :932), so a feature's learning drive is gamma_i * E[phi_i^2 / (1 + |phi|^2)] (speed, shown relative to the bias). Its reach is lim_i * RMS(phi_i): the largest torque it can make with Theta_i at its projection bound. gamma_i, lim_i from the MRAC_BASIS rows (pitch/roll/yaw/z), mrac_g_phi and the preset g assumed 1.

| axis | feature | RMS phi | gamma | drive E[phi^2/(1+|phi|^2)] | speed vs bias | lim | reach lim*RMS phi | actual RMS Theta*phi | Theta0 63% time (s) per seg |
|---|---|---|---|---|---|---|---|---|---|
| pitch | bias 1 | 1 | 1.50 | 0.486 | 1 | 0.150 | 0.15 | 0 | F1 - |
| pitch | x | 0.164 | 0.20 | 0.012 | 0.0033 | 0.050 | 0.00822 | 0 |  |
| pitch | x tanh x | 0.0582 | 0.05 | 0.00132 | 9.1e-05 | 0.020 | 0.00116 | 0 |  |
| pitch | cross | 0.0252 | 0.05 | 0.000311 | 2.1e-05 | 0.050 | 0.00126 | 0 |  |
| pitch | u_nom | 0.0913 | 0.10 | 0.00401 | 0.00055 | 0.200 | 0.0183 | 8.15e-05 |  |
| pitch | xm | 0.147 | 0.10 | 0.00982 | 0.0013 | 0.150 | 0.0221 | 0.000146 |  |
| roll | bias 1 | 1 | 1.50 | 0.485 | 1 | 0.150 | 0.15 | 0 | F1 - |
| roll | x | 0.178 | 0.20 | 0.014 | 0.0039 | 0.050 | 0.00888 | 0 |  |
| roll | x tanh x | 0.0626 | 0.05 | 0.0016 | 0.00011 | 0.020 | 0.00125 | 0 |  |
| roll | cross | 0.0196 | 0.05 | 0.000185 | 1.3e-05 | 0.050 | 0.00098 | 0 |  |
| roll | u_nom | 0.0705 | 0.10 | 0.00239 | 0.00033 | 0.200 | 0.0141 | 5.61e-05 |  |
| roll | xm | 0.158 | 0.10 | 0.0113 | 0.0016 | 0.150 | 0.0237 | 0.000166 |  |
| yaw | bias 1 | 1 | 1.00 | 0.493 | 1 | 0.090 | 0.09 | 0.034 | F1 1 |
| yaw | x | 0.12 | 0.10 | 0.00689 | 0.0014 | 0.030 | 0.00359 | 3.62e-05 |  |
| yaw | x tanh x | 0.0254 | 0.05 | 0.000303 | 3.1e-05 | 0.012 | 0.000304 | 5.22e-06 |  |
| yaw | cross | 0 | 0.05 | 0 | 0 | 0.030 | 0 | 0 |  |
| yaw | u_nom | 0.0541 | 0.10 | 0.00143 | 0.00029 | 0.120 | 0.0065 | 0.000133 |  |
| yaw | xm | 0.11 | 0.10 | 0.00594 | 0.0012 | 0.090 | 0.00994 | 0.000384 |  |
| z_rate | bias 1 | 1 | 2.00 | 0.249 | 1 | 1.000 | 1 | 0.457 | F1 2 |
| z_rate | x | 0.85 | 0.50 | 0.0177 | 0.018 | 0.100 | 0.085 | 0.00214 |  |
| z_rate | x tanh x | 0.832 | 0.10 | 0.0108 | 0.0022 | 0.050 | 0.0416 | 0.00197 |  |
| z_rate | cross | 0 | 0.10 | 0 | 0 | 0.050 | 0 | 0 |  |
| z_rate | u_nom | 1.43 | 0.20 | 0.465 | 0.19 | 0.200 | 0.287 | 0.204 |  |
| z_rate | xm | 0.192 | 0.20 | 0.00839 | 0.0034 | 0.200 | 0.0383 | 0.00372 |  |

