# Adaptive-layer stats: `vp13_g4_last`

Measured from the log. Basis rebuilt from logged signals (see the tool docstring). Shaded: blue PID, orange MRAC injected.

## Per MRAC segment and axis

| seg | axis | rebuild r | u_ad RMS | u_nom RMS | ratio | r(u_ad,u_nom) | r(u_ad,x) | band phase u_ad/x (deg) | band phase u_nom/x | Re H u_ad / Re H u_nom | coh | top feature (RMS share) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| F1 MRAC | pitch | 1.00 | 0.0461 | 0.0846 | 0.54 | -0.06 | -0.09 | +133 | +153 | 0.29 | 0.39 | xm 77%, u_nom 23% |
| F1 MRAC | roll | 1.00 | 0.0216 | 0.0830 | 0.26 | -0.05 | -0.16 | +117 | +161 | 0.31 | 0.44 | xm 91%, u_nom 9% |
| F1 MRAC | yaw | 1.00 | 0.0154 | 0.0551 | 0.28 | -0.06 | +0.10 | -10 | +116 | -0.08 | 0.61 | bias 1 100%, xm 0% |
| F1 MRAC | z_rate | 0.84 | 0.7167 | 1.4530 | 0.49 | +0.13 | +0.11 | -14 | +129 | -0.71 | 0.24 | bias 1 85%, u_nom 15% |

Phase of a torque against the body rate x in 0.25-0.90 Hz: +/-180 = pure damping, 0 = anti-damping, +/-90 = no energy. Re H u_ad / Re H u_nom > 0: u_ad adds damping like the PID; < 0: removes it.

## What-if: same weights, other omega_u (open-loop replay of the logged raw sum)

Band phase u_ad/x (deg) / Re H ratio, as above. Closed loop would differ (the weights would evolve differently); this isolates the filter lag.

| seg | axis | flown | 10 rad/s | 15 rad/s | 20 rad/s |
|---|---|---|---|---|---|
| F1 MRAC | pitch | +135 / +0.29 | +157 / +0.47 | +163 / +0.50 | +167 / +0.51 |
| F1 MRAC | roll | +118 / +0.32 | +133 / +0.51 | +138 / +0.58 | +141 / +0.61 |

## Weights Theta (start -> end of each MRAC segment)

| seg | axis | Theta[0] bias 1 | Theta[1] x | Theta[2] x tanh x | Theta[3] cross | Theta[4] u_nom | Theta[5] xm | |Theta| growth |
|---|---|---|---|---|---|---|---|---|
| F1 MRAC | pitch | +0.000 -> +0.000 | +0.000 -> +0.000 | +0.000 -> +0.000 | +0.000 -> +0.000 | +0.000 -> +0.003 | +0.000 -> +0.004 | 0.000 -> 0.005 |
| F1 MRAC | roll | +0.000 -> +0.000 | +0.000 -> +0.000 | +0.000 -> +0.000 | +0.000 -> +0.000 | +0.000 -> +0.004 | +0.000 -> +0.005 | 0.000 -> 0.006 |
| F1 MRAC | yaw | +0.000 -> -0.009 | +0.000 -> +0.000 | +0.000 -> +0.001 | +0.000 -> +0.000 | +0.000 -> +0.004 | +0.000 -> +0.004 | 0.000 -> 0.010 |
| F1 MRAC | z_rate | +0.000 -> +0.410 | +0.000 -> +0.004 | +0.000 -> +0.004 | +0.000 -> +0.000 | +0.000 -> +0.178 | +0.000 -> +0.044 | 0.000 -> 0.450 |

## Ext block (vp basis 7, 24 Gaussians per axis, pitch/roll)

Grid inputs in the firmware's normalised units (rate, angle); the Gaussian centres span -1..1 (basis 5: -1.5..1.5 rate, -1..1 angle). Activity = sd / mean of the most varying Gaussian over the segment: near 0 the grid acts as one constant (a bias), not as a function of the state. Ext share = ext RMS^2 / (ext RMS^2 + S6-part RMS^2). Slope = d|Theta_ext|/dt over the last third (> 0: still learning).

| seg | axis | rate p1 / p50 / p99 | angle p1 / p50 / p99 | activity | ext RMS | S6 part RMS | ext share | |Theta_ext| start -> end | slope (/s) | top ext (RMS share) |
|---|---|---|---|---|---|---|---|---|---|---|
| F1 MRAC | pitch | -0.133 / -0.000 / +0.140 | -0.656 / -0.075 / +0.409 | 0.700 | 0.0464 | 0.0004 | 100% | 0.000 -> 0.031 | +0.0001 | e13 42%, e14 20% |
| F1 MRAC | roll | -0.203 / +0.001 / +0.181 | -0.790 / -0.044 / +0.547 | 0.838 | 0.0225 | 0.0006 | 100% | 0.000 -> 0.034 | +0.0001 | e9 29%, e10 18% |

### Ext ranking (every Gaussian, RMS^2 share of the ext block, cumulative in brackets)

| seg | axis | ranked |
|---|---|---|
| F1 MRAC | pitch | e13 (r+0.1 a-0.2) 42.3% [42%], e14 (r+0.1 a+0.2) 19.7% [62%], e17 (r+0.3 a-0.2) 14.4% [76%], e18 (r+0.3 a+0.2) 6.7% [83%], e12 (r+0.1 a-0.6) 5.1% [88%], e9 (r-0.1 a-0.2) 4.8% [93%], e16 (r+0.3 a-0.6) 1.7% [95%], e10 (r-0.1 a+0.2) 1.5% [96%], e5 (r-0.3 a-0.2) 1.0% [97%], e8 (r-0.1 a-0.6) 0.8% [98%], e15 (r+0.1 a+0.6) 0.6% [99%], e6 (r-0.3 a+0.2) 0.4% [99%], e21 (r+0.7 a-0.2) 0.4% [99%], e19 (r+0.3 a+0.6) 0.2% [100%], e22 (r+0.7 a+0.2) 0.2% [100%], e4 (r-0.3 a-0.6) 0.1% [100%], e11 (r-0.1 a+0.6) 0.1% [100%], e20 (r+0.7 a-0.6) 0.0% [100%], e1 (r-0.7 a-0.2) 0.0% [100%], e2 (r-0.7 a+0.2) 0.0% [100%], e7 (r-0.3 a+0.6) 0.0% [100%], e23 (r+0.7 a+0.6) 0.0% [100%], e0 (r-0.7 a-0.6) 0.0% [100%], e3 (r-0.7 a+0.6) 0.0% [100%] |
| F1 MRAC | roll | e9 (r-0.1 a-0.2) 28.9% [29%], e10 (r-0.1 a+0.2) 17.5% [46%], e5 (r-0.3 a-0.2) 13.2% [60%], e6 (r-0.3 a+0.2) 7.1% [67%], e8 (r-0.1 a-0.6) 7.1% [74%], e13 (r+0.1 a-0.2) 6.5% [80%], e14 (r+0.1 a+0.2) 5.5% [86%], e18 (r+0.3 a+0.2) 3.8% [90%], e17 (r+0.3 a-0.2) 3.8% [93%], e4 (r-0.3 a-0.6) 3.2% [97%], e11 (r-0.1 a+0.6) 0.8% [97%], e12 (r+0.1 a-0.6) 0.5% [98%], e1 (r-0.7 a-0.2) 0.4% [98%], e15 (r+0.1 a+0.6) 0.4% [99%], e7 (r-0.3 a+0.6) 0.3% [99%], e19 (r+0.3 a+0.6) 0.2% [99%], e2 (r-0.7 a+0.2) 0.2% [99%], e22 (r+0.7 a+0.2) 0.2% [100%], e21 (r+0.7 a-0.2) 0.1% [100%], e0 (r-0.7 a-0.6) 0.1% [100%], e16 (r+0.3 a-0.6) 0.1% [100%], e23 (r+0.7 a+0.6) 0.0% [100%], e3 (r-0.7 a+0.6) 0.0% [100%], e20 (r+0.7 a-0.6) 0.0% [100%] |
| F1 MRAC | p+r mean | e13 (r+0.1 a-0.2) 24.4% [24%], e9 (r-0.1 a-0.2) 16.9% [41%], e14 (r+0.1 a+0.2) 12.6% [54%], e10 (r-0.1 a+0.2) 9.5% [63%], e17 (r+0.3 a-0.2) 9.1% [72%], e5 (r-0.3 a-0.2) 7.1% [80%], e18 (r+0.3 a+0.2) 5.3% [85%], e8 (r-0.1 a-0.6) 3.9% [89%], e6 (r-0.3 a+0.2) 3.7% [93%], e12 (r+0.1 a-0.6) 2.8% [95%], e4 (r-0.3 a-0.6) 1.7% [97%], e16 (r+0.3 a-0.6) 0.9% [98%], e15 (r+0.1 a+0.6) 0.5% [98%], e11 (r-0.1 a+0.6) 0.4% [99%], e21 (r+0.7 a-0.2) 0.2% [99%], e19 (r+0.3 a+0.6) 0.2% [99%], e1 (r-0.7 a-0.2) 0.2% [99%], e22 (r+0.7 a+0.2) 0.2% [100%], e7 (r-0.3 a+0.6) 0.2% [100%], e2 (r-0.7 a+0.2) 0.1% [100%], e0 (r-0.7 a-0.6) 0.0% [100%], e20 (r+0.7 a-0.6) 0.0% [100%], e23 (r+0.7 a+0.6) 0.0% [100%], e3 (r-0.7 a+0.6) 0.0% [100%] |

## Static offsets per segment (drift)

Expected static torque of the load at the rope point: pitch 0.112 N m (x 2 cm), roll 0.391 N m (y 7 cm), mg = 5.59 N.

| seg | mean u_nom p / r | mean u_ad p / r | mean Theta[0] p / r | pitch Des - FB (deg) | roll Des - FB | locx Des-FB (cm) | locy Des-FB | locx/y PID U mean | x FB slope (cm/s) | y FB slope | x stick on (%) | x stick vel Des median (cm/s) | x vel FB mean, no stick (cm/s) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| F1 MRAC | -0.0354 / +0.0356 | -0.0448 / +0.0182 | +0.0000 / +0.0000 | +0.16 | +0.04 | -0.4 | -4.5 | +1.9 / -6.9 | -0.42 | -0.32 | 18 | +26 | +0.13 |

## Per segment: attitude, height, motors, battery

| seg | s | pitch sd | roll sd | rate sd p / r (deg/s) | band PSD p / r | z - z_des | motor at 4000 (%) | max motor spread | V mean | stab CPU % mean / max |
|---|---|---|---|---|---|---|---|---|---|---|
| F1 MRAC | 69 | 2.66 | 3.81 | 10.4 / 13.8 | 32.5 / 63.1 | +0.000 | 7.0 | 1262 | 14.54 | 9.7 / 10.1 |

## Uncertainty estimate: does u_ad match the disturbance?

Plant per axis: xdot = b (u_inj + Delta), u_inj = u_nom + (injected) u_ad. b and the delay tau are fitted on 2-8 Hz content of all airborne segments (above the load band; closed-loop fit, so b is biased: the cancel fraction is also given for b x0.7 and x1.4). Delta_hat = LPF3Hz(xdot)/b - LPF3Hz(u_inj(t - tau)) is everything the nominal model does not explain (load torque and swing, CG offset, motor mismatch, drag), in control units. A perfect adaptive layer gives u_ad = -Delta_hat.

- static: mean(-Delta_hat) is the trim the drone needs; u_ad share = mean(u_ad) / mean(-Delta_hat) (the PID integrator carries the rest)
- dynamic (means removed): cancel = 1 - var(u_ad + Delta_hat) / var(Delta_hat); 1 = cancels all, 0 = no help, < 0 = adds disturbance. Band gain / phase of u_ad against -Delta_hat in 0.25-0.90 Hz (ideal 1 / 0 deg).
- ideal learner: cancel of -LPF_w(Delta_hat), what a perfect estimator behind the firmware u_ad filter at w rad/s could do. PID segments: u_ad is the shadow (computed, not injected).

| axis | b | tau (ms) | r (2-8 Hz) |
|---|---|---|---|
| pitch | 50.2 | 40 | 0.75 |
| roll | 64.6 | 40 | 0.78 |
| yaw | 82.7 | 40 | 0.73 |
| z_rate | 19.2 | 40 | 0.36 |

| seg | axis | -Delta static | u_ad mean | u_nom mean | u_ad share | -Delta dyn RMS | u_ad dyn RMS | band gain / phase / coh | cancel b x0.7 / x1 / x1.4 | ideal w 0.5 / 1 / 2 / 4 / 8 / 16 |
|---|---|---|---|---|---|---|---|---|---|---|
| F1 MRAC | pitch | -0.0804 | -0.0448 | -0.0354 | 0.56 | 0.0286 | 0.0108 | 0.14 / -90 / 0.26 | -0.06 / -0.04 / -0.02 | -0.32 / -0.02 / +0.20 / +0.43 / +0.69 / +0.88 |
| F1 MRAC | roll | +0.0538 | +0.0182 | +0.0356 | 0.34 | 0.0292 | 0.0116 | 0.16 / -94 / 0.19 | -0.07 / -0.05 / -0.01 | +0.06 / +0.19 / +0.34 / +0.51 / +0.72 / +0.89 |
| F1 MRAC | yaw | +0.0093 | -0.0002 | +0.0095 | -0.03 | 0.0480 | 0.0154 | 0.06 / -114 / 0.73 | +0.08 / +0.07 / +0.07 | +0.14 / +0.22 / +0.36 / +0.58 / +0.82 / +0.95 |
| F1 MRAC | z_rate | +2.0900 | +0.6817 | +1.4078 | 0.33 | 0.4186 | 0.2213 | 0.24 / -0 / 0.44 | +0.32 / +0.37 / +0.40 | +0.22 / +0.27 / +0.34 / +0.48 / +0.68 / +0.86 |

## Load swing frequency

Pendulum: L = rope 33 cm + half bottle 10 cm = 0.43 m. Simple sqrt(g/L)/2pi = 0.76 Hz; drone free to move, sqrt(g (M+m) / (M L))/2pi = 0.95 Hz (M 988 g, m 570 g). Measured: peak of the body-rate PSD in 0.15-1.5 Hz.

| seg | pitch peak (Hz) | roll peak (Hz) |
|---|---|---|
| F1 MRAC | 0.49 | 0.39 |

## Outside-force feature: body accel x/y vs the disturbance

Body-frame accel x/y sees only non-thrust forces (rope pull, slosh, wind, walls): thrust is along body z. If the torque is lever arm x force, -Delta_hat = w * a_xy with ONE constant w for any load. Per segment, means removed, both LPF 3 Hz. r = correlation at lag 0; cancel = r^2 = share of the dynamic disturbance a constant weight removes; w in control units per g. Lead = lag (+-300 ms) with the best |r|, positive = accel leads the disturbance. S6 = best single feature of x, x tanh x, cross, xm (u_nom left out: -Delta_hat contains u_nom by construction), then those four together, then together + accel x/y (offline least squares, constant weights). The S6 columns are an UPPER BOUND, partly circular: x and xm together rebuild the PID P term that sits inside -Delta_hat. Accel is an independent sensor, so its columns are clean. Caveat: an IMU off the centre of gravity adds (angular accel x offset), a few mg here.

| seg | axis | acc X r / cancel / w | acc Y r / cancel / w | lead X / Y (ms) | best S6 one | S6 four | S6 four + acc |
|---|---|---|---|---|---|---|---|
| F1 MRAC | pitch | -0.13 / 0.02 / -0.034 | +0.02 / 0.00 / +0.005 | +130 (r -0.20) / +180 (r -0.12) | 0.10 | 0.46 | 0.46 |
| F1 MRAC | roll | -0.02 / 0.00 / -0.005 | -0.06 / 0.00 / -0.017 | -300 (r +0.06) / -300 (r +0.09) | 0.06 | 0.40 | 0.40 |

## Cross-coupling: does the other axis help? (vp16 48-bump split)

-Delta_hat (as above, LPF 3 Hz) fitted with constant weights on Gaussian grids, scored on held-out data (interleaved 5 s blocks: fit even, score odd, and back). Inputs in firmware units: rate / 200 deg/s, tilt / 15 deg. own24 = RBF24T (6 rate x 4 tilt, mrac_t_*_c); own48 = 8 rate x 6 tilt; own32 = 8 rate x 4 tilt; cross16 = own tilt x OTHER tilt (4 x 4); lin = other tilt + other rate as two plain features. Fair split test at 48 bumps: own48 vs own32 + cross16. Swing = same fit on 0.25-0.90 Hz band-passed signals. Upper bound for the own grids (they partly rebuild the PID term inside -Delta_hat), so a cross gain here is the conservative signal. Grid centres of own48/own32 are a test choice, not firmware.

| seg | axis | own24 in / held-out | own24 + lin | own48 | own32 + cross16 | swing own48 / own32 + cross16 |
|---|---|---|---|---|---|---|
| F1 MRAC | pitch | 0.19 / 0.04 | 0.04 | 0.03 | 0.02 | -0.06 / -0.14 |
| F1 MRAC | roll | 0.27 / 0.05 | 0.06 | 0.03 | 0.07 | 0.02 / -0.13 |

## vp16 grid inputs: where the drone sits (centre placement, gamma T rule)

Each vp16 grid input in its normalised unit, p1 / p50 / p99 per segment. pitch/roll: rate / 200 deg/s, tilt / 15 deg, cross = other tilt / 15 deg. yaw: rate / 160 deg/s, heading error ~ gyrozPID.Des / 162 (= Des / Kp 6 / 27 deg, yawPID not logged), cross = Z thrust share. Z: climb rate / 1.0 m/s, thrust share u_nom / u_max, cross = tilt size / 15 deg. x/y: velocity / 300 cm/s, position error / 375 cm, cross = other velocity / 300. |e| = MRAC error (x/y: locxs/locys Des - FB, cm/s) against e_sat; > e_sat = share of time the tanh cap saturates.

| seg | axis | in1 p1 / p50 / p99 | in2 p1 / p50 / p99 | cross p1 / p50 / p99 | abs e p50 / p95 / p99 | e_sat | > e_sat (%) |
|---|---|---|---|---|---|---|---|
| F1 MRAC | pitch | -0.13 / -0.00 / +0.14 | -0.66 / -0.07 / +0.41 | -0.79 / -0.04 / +0.55 | 0.088 / 0.271 / 0.357 | 0.5 | 0.1 |
| F1 MRAC | roll | -0.20 / +0.00 / +0.18 | -0.79 / -0.04 / +0.55 | -0.66 / -0.07 / +0.41 | 0.088 / 0.264 / 0.378 | 0.5 | 0.2 |
| F1 MRAC | yaw | -0.14 / +0.00 / +0.14 | -0.16 / +0.00 / +0.17 | -0.00 / +0.10 / +0.17 | 0.069 / 0.250 / 0.340 | 0.7 | 0.0 |
| F1 MRAC | z_rate | -1.33 / +0.00 / +0.84 | -0.00 / +0.10 / +0.17 | +0.04 / +0.22 / +0.80 | 0.084 / 0.700 / 2.211 | 0.4 | 11.7 |
| F1 MRAC | x | -0.32 / +0.00 / +0.27 | -0.15 / +0.00 / +0.22 | -0.28 / -0.00 / +0.25 | 13.263 / 72.239 / 103.847 | 45 | 13.1 |
| F1 MRAC | y | -0.28 / -0.00 / +0.25 | -0.09 / -0.02 / +0.31 | -0.32 / +0.00 / +0.27 | 13.616 / 41.905 / 76.083 | 45 | 4.2 |

## Feature capability (all MRAC segments pooled)

Theta_i moves at gamma_i * s * phi_i / (1 + |phi|^2) (API/mrac.c:748, denom :932), so a feature's learning drive is gamma_i * E[phi_i^2 / (1 + |phi|^2)] (speed, shown relative to the bias). Its reach is lim_i * RMS(phi_i): the largest torque it can make with Theta_i at its projection bound. gamma_i, lim_i from the MRAC_BASIS rows (pitch/roll/yaw/z), mrac_g_phi and the preset g assumed 1.

| axis | feature | RMS phi | gamma | drive E[phi^2/(1+|phi|^2)] | speed vs bias | lim | reach lim*RMS phi | actual RMS Theta*phi | Theta0 63% time (s) per seg |
|---|---|---|---|---|---|---|---|---|---|
| pitch | bias 1 | 1 | 1.50 | 0.485 | 1 | 0.150 | 0.15 | 0 | F1 - |
| pitch | x | 0.181 | 0.20 | 0.0147 | 0.004 | 0.050 | 0.00906 | 0 |  |
| pitch | x tanh x | 0.063 | 0.05 | 0.0016 | 0.00011 | 0.020 | 0.00126 | 0 |  |
| pitch | cross | 0.0402 | 0.05 | 0.000775 | 5.3e-05 | 0.050 | 0.00201 | 0 |  |
| pitch | u_nom | 0.0846 | 0.10 | 0.00343 | 0.00047 | 0.200 | 0.0169 | 0.000169 |  |
| pitch | xm | 0.151 | 0.10 | 0.0101 | 0.0014 | 0.150 | 0.0226 | 0.000308 |  |
| roll | bias 1 | 1 | 1.50 | 0.475 | 1 | 0.150 | 0.15 | 0 | F1 - |
| roll | x | 0.242 | 0.20 | 0.0232 | 0.0065 | 0.050 | 0.0121 | 0 |  |
| roll | x tanh x | 0.114 | 0.05 | 0.00433 | 0.0003 | 0.020 | 0.00227 | 0 |  |
| roll | cross | 0.0269 | 0.05 | 0.000338 | 2.4e-05 | 0.050 | 0.00134 | 0 |  |
| roll | u_nom | 0.083 | 0.10 | 0.00321 | 0.00045 | 0.200 | 0.0166 | 0.00018 |  |
| roll | xm | 0.216 | 0.10 | 0.0186 | 0.0026 | 0.150 | 0.0324 | 0.000572 |  |
| yaw | bias 1 | 1 | 1.00 | 0.492 | 1 | 0.090 | 0.09 | 0.0156 | F1 2 |
| yaw | x | 0.14 | 0.10 | 0.00904 | 0.0018 | 0.030 | 0.00419 | 2.82e-05 |  |
| yaw | x tanh x | 0.0444 | 0.05 | 0.000851 | 8.6e-05 | 0.012 | 0.000532 | 1.52e-05 |  |
| yaw | cross | 0 | 0.05 | 0 | 0 | 0.030 | 0 | 0 |  |
| yaw | u_nom | 0.0551 | 0.10 | 0.00145 | 0.0003 | 0.120 | 0.00661 | 0.000125 |  |
| yaw | xm | 0.1 | 0.10 | 0.00482 | 0.00098 | 0.090 | 0.00901 | 0.00022 |  |
| z_rate | bias 1 | 1 | 2.00 | 0.246 | 1 | 1.000 | 1 | 0.517 | F1 4 |
| z_rate | x | 0.614 | 0.50 | 0.0153 | 0.016 | 0.100 | 0.0614 | 0.00118 |  |
| z_rate | x tanh x | 0.587 | 0.10 | 0.00833 | 0.0017 | 0.050 | 0.0293 | 0.00191 |  |
| z_rate | cross | 0 | 0.10 | 0 | 0 | 0.050 | 0 | 0 |  |
| z_rate | u_nom | 1.45 | 0.20 | 0.474 | 0.19 | 0.200 | 0.291 | 0.218 |  |
| z_rate | xm | 0.21 | 0.20 | 0.00985 | 0.004 | 0.200 | 0.0421 | 0.00428 |  |

