# Adaptive-layer stats: `vp14_g4_FRESH_BATTERY`

Measured from the log. Basis rebuilt from logged signals (see the tool docstring). Shaded: blue PID, orange MRAC injected.

## Per MRAC segment and axis

| seg | axis | rebuild r | u_ad RMS | u_nom RMS | ratio | r(u_ad,u_nom) | r(u_ad,x) | band phase u_ad/x (deg) | band phase u_nom/x | Re H u_ad / Re H u_nom | coh | top feature (RMS share) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| F1 MRAC | pitch | 1.00 | 0.0526 | 0.0901 | 0.58 | -0.08 | -0.15 | +155 | -156 | 0.54 | 0.37 | xm 79%, u_nom 21% |
| F1 MRAC | roll | 1.00 | 0.0318 | 0.0756 | 0.42 | -0.01 | -0.09 | +143 | +144 | 0.18 | 0.09 | xm 83%, u_nom 17% |
| F1 MRAC | yaw | 1.00 | 0.0169 | 0.0473 | 0.36 | -0.08 | +0.10 | +8 | +117 | -0.06 | 0.58 | bias 1 100%, xm 0% |
| F1 MRAC | z_rate | 0.83 | 0.5368 | 0.9334 | 0.58 | +0.16 | -0.01 | -129 | +155 | 0.14 | 0.19 | bias 1 91%, u_nom 9% |

Phase of a torque against the body rate x in 0.25-0.90 Hz: +/-180 = pure damping, 0 = anti-damping, +/-90 = no energy. Re H u_ad / Re H u_nom > 0: u_ad adds damping like the PID; < 0: removes it.

## What-if: same weights, other omega_u (open-loop replay of the logged raw sum)

Band phase u_ad/x (deg) / Re H ratio, as above. Closed loop would differ (the weights would evolve differently); this isolates the filter lag.

| seg | axis | flown | 10 rad/s | 15 rad/s | 20 rad/s |
|---|---|---|---|---|---|
| F1 MRAC | pitch | +157 / +0.55 | +176 / +0.77 | -178 / +0.81 | -175 / +0.82 |
| F1 MRAC | roll | +144 / +0.18 | +155 / +0.22 | +159 / +0.23 | +162 / +0.24 |

## Weights Theta (start -> end of each MRAC segment)

| seg | axis | Theta[0] bias 1 | Theta[1] x | Theta[2] x tanh x | Theta[3] cross | Theta[4] u_nom | Theta[5] xm | |Theta| growth |
|---|---|---|---|---|---|---|---|---|
| F1 MRAC | pitch | +0.000 -> +0.000 | +0.000 -> +0.000 | +0.000 -> +0.000 | +0.000 -> +0.000 | +0.000 -> +0.002 | +0.000 -> +0.002 | 0.000 -> 0.003 |
| F1 MRAC | roll | +0.000 -> +0.000 | +0.000 -> +0.000 | +0.000 -> +0.000 | +0.000 -> +0.000 | +0.000 -> +0.001 | +0.000 -> +0.002 | 0.000 -> 0.002 |
| F1 MRAC | yaw | -0.000 -> +0.014 | +0.000 -> +0.000 | +0.000 -> +0.000 | +0.000 -> +0.000 | +0.000 -> +0.004 | +0.000 -> +0.003 | 0.000 -> 0.015 |
| F1 MRAC | z_rate | +0.000 -> +0.142 | +0.000 -> +0.008 | +0.000 -> +0.005 | +0.000 -> +0.000 | +0.000 -> +0.175 | +0.000 -> +0.034 | 0.000 -> 0.228 |

## Ext block (vp basis 8, 24 Gaussians per axis, pitch/roll)

Grid inputs in the firmware's normalised units (rate, angle); the Gaussian centres span -1..1 (basis 5: -1.5..1.5 rate, -1..1 angle). Activity = sd / mean of the most varying Gaussian over the segment: near 0 the grid acts as one constant (a bias), not as a function of the state. Ext share = ext RMS^2 / (ext RMS^2 + S6-part RMS^2). Slope = d|Theta_ext|/dt over the last third (> 0: still learning).

| seg | axis | rate p1 / p50 / p99 | angle p1 / p50 / p99 | activity | ext RMS | S6 part RMS | ext share | |Theta_ext| start -> end | slope (/s) | top ext (RMS share) |
|---|---|---|---|---|---|---|---|---|---|---|
| F1 MRAC | pitch | -0.147 / +0.001 / +0.136 | -0.844 / -0.073 / +0.521 | 0.539 | 0.0528 | 0.0002 | 100% | 0.000 -> 0.022 | +0.0000 | e2 15%, e8 13% |
| F1 MRAC | roll | -0.155 / +0.000 / +0.155 | -0.648 / +0.003 / +0.764 | 0.532 | 0.0320 | 0.0001 | 100% | 0.000 -> 0.021 | +0.0001 | e8 12%, e2 12% |

### Ext ranking (every Gaussian, RMS^2 share of the ext block, cumulative in brackets)

| seg | axis | ranked |
|---|---|---|
| F1 MRAC | pitch | e2 14.8% [15%], e8 13.5% [28%], e3 11.3% [40%], e14 8.4% [48%], e9 8.3% [56%], e22 5.7% [62%], e16 5.4% [67%], e20 4.4% [72%], e10 4.2% [76%], e15 3.9% [80%], e4 3.1% [83%], e11 2.6% [86%], e17 2.6% [88%], e5 2.4% [91%], e23 1.9% [93%], e18 1.6% [94%], e0 1.2% [95%], e21 1.0% [96%], e19 1.0% [97%], e1 0.9% [98%], e6 0.6% [99%], e12 0.5% [99%], e7 0.4% [100%], e13 0.2% [100%] |
| F1 MRAC | roll | e8 12.4% [12%], e2 11.8% [24%], e14 9.2% [33%], e3 7.9% [41%], e12 7.4% [49%], e19 6.4% [55%], e18 6.0% [61%], e9 5.9% [67%], e13 4.9% [72%], e6 4.9% [77%], e15 3.4% [80%], e7 3.2% [83%], e0 3.0% [86%], e21 2.5% [89%], e1 2.2% [91%], e23 2.2% [93%], e20 1.9% [95%], e22 1.5% [97%], e17 1.2% [98%], e4 0.7% [99%], e5 0.5% [99%], e10 0.4% [99%], e11 0.3% [100%], e16 0.3% [100%] |
| F1 MRAC | p+r mean | e2 13.3% [13%], e8 12.9% [26%], e3 9.6% [36%], e14 8.8% [45%], e9 7.1% [52%], e12 4.0% [56%], e18 3.8% [59%], e19 3.7% [63%], e15 3.7% [67%], e22 3.6% [70%], e20 3.2% [74%], e16 2.8% [76%], e6 2.8% [79%], e13 2.6% [82%], e10 2.3% [84%], e0 2.1% [86%], e23 2.0% [88%], e4 1.9% [90%], e17 1.9% [92%], e7 1.8% [94%], e21 1.7% [96%], e1 1.6% [97%], e11 1.5% [99%], e5 1.4% [100%] |

## Static offsets per segment (drift)

Expected static torque of the load at the rope point: pitch 0.112 N m (x 2 cm), roll 0.391 N m (y 7 cm), mg = 5.59 N.

| seg | mean u_nom p / r | mean u_ad p / r | mean Theta[0] p / r | pitch Des - FB (deg) | roll Des - FB | locx Des-FB (cm) | locy Des-FB | locx/y PID U mean | x FB slope (cm/s) | y FB slope | x stick on (%) | x stick vel Des median (cm/s) | x vel FB mean, no stick (cm/s) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| F1 MRAC | -0.0524 / +0.0293 | -0.0512 / +0.0296 | +0.0000 / +0.0000 | +0.03 | -0.08 | +1.2 | -5.8 | +3.6 / -8.0 | +0.04 | -0.43 | 12 | +26 | -0.35 |

## Per segment: attitude, height, motors, battery

| seg | s | pitch sd | roll sd | rate sd p / r (deg/s) | band PSD p / r | z - z_des | motor at 4000 (%) | max motor spread | V mean | stab CPU % mean / max |
|---|---|---|---|---|---|---|---|---|---|---|
| F1 MRAC | 82 | 2.98 | 3.53 | 10.3 / 11.2 | 34.1 / 47.3 | +0.006 | 1.4 | 1294 | 15.40 | 9.9 / 10.3 |

## Uncertainty estimate: does u_ad match the disturbance?

Plant per axis: xdot = b (u_inj + Delta), u_inj = u_nom + (injected) u_ad. b and the delay tau are fitted on 2-8 Hz content of all airborne segments (above the load band; closed-loop fit, so b is biased: the cancel fraction is also given for b x0.7 and x1.4). Delta_hat = LPF3Hz(xdot)/b - LPF3Hz(u_inj(t - tau)) is everything the nominal model does not explain (load torque and swing, CG offset, motor mismatch, drag), in control units. A perfect adaptive layer gives u_ad = -Delta_hat.

- static: mean(-Delta_hat) is the trim the drone needs; u_ad share = mean(u_ad) / mean(-Delta_hat) (the PID integrator carries the rest)
- dynamic (means removed): cancel = 1 - var(u_ad + Delta_hat) / var(Delta_hat); 1 = cancels all, 0 = no help, < 0 = adds disturbance. Band gain / phase of u_ad against -Delta_hat in 0.25-0.90 Hz (ideal 1 / 0 deg).
- ideal learner: cancel of -LPF_w(Delta_hat), what a perfect estimator behind the firmware u_ad filter at w rad/s could do. PID segments: u_ad is the shadow (computed, not injected).

| axis | b | tau (ms) | r (2-8 Hz) |
|---|---|---|---|
| pitch | 47.4 | 40 | 0.76 |
| roll | 49.3 | 40 | 0.74 |
| yaw | 95.1 | 30 | 0.71 |
| z_rate | 24.5 | 40 | 0.57 |

| seg | axis | -Delta static | u_ad mean | u_nom mean | u_ad share | -Delta dyn RMS | u_ad dyn RMS | band gain / phase / coh | cancel b x0.7 / x1 / x1.4 | ideal w 0.5 / 1 / 2 / 4 / 8 / 16 |
|---|---|---|---|---|---|---|---|---|---|---|
| F1 MRAC | pitch | -0.1035 | -0.0512 | -0.0524 | 0.49 | 0.0310 | 0.0121 | 0.12 / -79 / 0.29 | -0.03 / -0.02 / -0.02 | +0.09 / +0.19 / +0.33 / +0.51 / +0.72 / +0.90 |
| F1 MRAC | roll | +0.0589 | +0.0296 | +0.0293 | 0.50 | 0.0285 | 0.0117 | 0.09 / -87 / 0.20 | +0.08 / +0.12 / +0.15 | +0.04 / +0.19 / +0.34 / +0.53 / +0.74 / +0.90 |
| F1 MRAC | yaw | +0.0196 | +0.0105 | +0.0092 | 0.54 | 0.0413 | 0.0132 | 0.06 / -103 / 0.72 | +0.06 / +0.06 / +0.05 | +0.08 / +0.20 / +0.35 / +0.57 / +0.81 / +0.95 |
| F1 MRAC | z_rate | +1.3751 | +0.5191 | +0.8555 | 0.38 | 0.3333 | 0.1368 | 0.18 / +2 / 0.48 | +0.30 / +0.30 / +0.29 | +0.24 / +0.30 / +0.40 / +0.57 / +0.77 / +0.92 |

## Load swing frequency

Pendulum: L = rope 33 cm + half bottle 10 cm = 0.43 m. Simple sqrt(g/L)/2pi = 0.76 Hz; drone free to move, sqrt(g (M+m) / (M L))/2pi = 0.95 Hz (M 988 g, m 570 g). Measured: peak of the body-rate PSD in 0.15-1.5 Hz.

| seg | pitch peak (Hz) | roll peak (Hz) |
|---|---|---|
| F1 MRAC | 0.39 | 0.39 |

## Outside-force feature: body accel x/y vs the disturbance

Body-frame accel x/y sees only non-thrust forces (rope pull, slosh, wind, walls): thrust is along body z. If the torque is lever arm x force, -Delta_hat = w * a_xy with ONE constant w for any load. Per segment, means removed, both LPF 3 Hz. r = correlation at lag 0; cancel = r^2 = share of the dynamic disturbance a constant weight removes; w in control units per g. Lead = lag (+-300 ms) with the best |r|, positive = accel leads the disturbance. S6 = best single feature of x, x tanh x, cross, xm (u_nom left out: -Delta_hat contains u_nom by construction), then those four together, then together + accel x/y (offline least squares, constant weights). The S6 columns are an UPPER BOUND, partly circular: x and xm together rebuild the PID P term that sits inside -Delta_hat. Accel is an independent sensor, so its columns are clean. Caveat: an IMU off the centre of gravity adds (angular accel x offset), a few mg here.

| seg | axis | acc X r / cancel / w | acc Y r / cancel / w | lead X / Y (ms) | best S6 one | S6 four | S6 four + acc |
|---|---|---|---|---|---|---|---|
| F1 MRAC | pitch | -0.07 / 0.00 / -0.022 | +0.02 / 0.00 / +0.007 | -300 (r +0.23) / +270 (r -0.07) | 0.09 | 0.41 | 0.41 |
| F1 MRAC | roll | +0.02 / 0.00 / +0.005 | -0.18 / 0.03 / -0.054 | -180 (r +0.06) / +70 (r -0.24) | 0.08 | 0.31 | 0.32 |

## Feature capability (all MRAC segments pooled)

Theta_i moves at gamma_i * s * phi_i / (1 + |phi|^2) (API/mrac.c:748, denom :932), so a feature's learning drive is gamma_i * E[phi_i^2 / (1 + |phi|^2)] (speed, shown relative to the bias). Its reach is lim_i * RMS(phi_i): the largest torque it can make with Theta_i at its projection bound. gamma_i, lim_i from the MRAC_BASIS rows (pitch/roll/yaw/z), mrac_g_phi and the preset g assumed 1.

| axis | feature | RMS phi | gamma | drive E[phi^2/(1+|phi|^2)] | speed vs bias | lim | reach lim*RMS phi | actual RMS Theta*phi | Theta0 63% time (s) per seg |
|---|---|---|---|---|---|---|---|---|---|
| pitch | bias 1 | 1 | 1.50 | 0.484 | 1 | 0.150 | 0.15 | 0 | F1 - |
| pitch | x | 0.18 | 0.20 | 0.0142 | 0.0039 | 0.050 | 0.00901 | 0 |  |
| pitch | x tanh x | 0.0666 | 0.05 | 0.00173 | 0.00012 | 0.020 | 0.00133 | 0 |  |
| pitch | cross | 0.0288 | 0.05 | 0.000394 | 2.7e-05 | 0.050 | 0.00144 | 0 |  |
| pitch | u_nom | 0.0901 | 0.10 | 0.00389 | 0.00054 | 0.200 | 0.018 | 9.17e-05 |  |
| pitch | xm | 0.163 | 0.10 | 0.0118 | 0.0016 | 0.150 | 0.0244 | 0.000178 |  |
| roll | bias 1 | 1 | 1.50 | 0.483 | 1 | 0.150 | 0.15 | 0 | F1 - |
| roll | x | 0.195 | 0.20 | 0.0163 | 0.0045 | 0.050 | 0.00976 | 0 |  |
| roll | x tanh x | 0.0775 | 0.05 | 0.00227 | 0.00016 | 0.020 | 0.00155 | 0 |  |
| roll | cross | 0.0219 | 0.05 | 0.000221 | 1.5e-05 | 0.050 | 0.0011 | 0 |  |
| roll | u_nom | 0.0756 | 0.10 | 0.00273 | 0.00038 | 0.200 | 0.0151 | 6.11e-05 |  |
| roll | xm | 0.172 | 0.10 | 0.0128 | 0.0018 | 0.150 | 0.0258 | 0.000136 |  |
| yaw | bias 1 | 1 | 1.00 | 0.494 | 1 | 0.090 | 0.09 | 0.017 | F1 2 |
| yaw | x | 0.12 | 0.10 | 0.00702 | 0.0014 | 0.030 | 0.00361 | 1.36e-05 |  |
| yaw | x tanh x | 0.0251 | 0.05 | 0.000302 | 3.1e-05 | 0.012 | 0.000302 | 6.62e-06 |  |
| yaw | cross | 0 | 0.05 | 0 | 0 | 0.030 | 0 | 0 |  |
| yaw | u_nom | 0.0473 | 0.10 | 0.0011 | 0.00022 | 0.120 | 0.00568 | 9.54e-05 |  |
| yaw | xm | 0.0862 | 0.10 | 0.00364 | 0.00074 | 0.090 | 0.00776 | 0.000169 |  |
| z_rate | bias 1 | 1 | 2.00 | 0.351 | 1 | 1.000 | 1 | 0.421 | F1 2 |
| z_rate | x | 0.486 | 0.50 | 0.0145 | 0.01 | 0.100 | 0.0486 | 0.00107 |  |
| z_rate | x tanh x | 0.458 | 0.10 | 0.00763 | 0.0011 | 0.050 | 0.0229 | 0.00169 |  |
| z_rate | cross | 0 | 0.10 | 0 | 0 | 0.050 | 0 | 0 |  |
| z_rate | u_nom | 0.933 | 0.20 | 0.27 | 0.077 | 0.200 | 0.187 | 0.133 |  |
| z_rate | xm | 0.149 | 0.20 | 0.00581 | 0.0017 | 0.200 | 0.0298 | 0.0023 |  |

