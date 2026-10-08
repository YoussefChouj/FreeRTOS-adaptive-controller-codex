# Adaptive-layer stats: `vp14_g4`

Measured from the log. Basis rebuilt from logged signals (see the tool docstring). Shaded: blue PID, orange MRAC injected.

## Per MRAC segment and axis

| seg | axis | rebuild r | u_ad RMS | u_nom RMS | ratio | r(u_ad,u_nom) | r(u_ad,x) | band phase u_ad/x (deg) | band phase u_nom/x | Re H u_ad / Re H u_nom | coh | top feature (RMS share) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| F1 MRAC | pitch | 1.00 | 0.0822 | 0.0885 | 0.93 | -0.08 | +0.00 | -114 | +100 | 0.51 | 0.16 | xm 99%, u_nom 1% |
| F1 MRAC | roll | 1.00 | 0.0527 | 0.0858 | 0.61 | -0.09 | -0.02 | +80 | +124 | -0.02 | 0.03 | xm 98%, u_nom 2% |
| F1 MRAC | yaw | 1.00 | 0.0399 | 0.0673 | 0.59 | -0.05 | +0.15 | -23 | +116 | -0.07 | 0.63 | bias 1 100%, xm 0% |
| F1 MRAC | z_rate | 0.81 | 0.8871 | 1.4594 | 0.61 | +0.41 | +0.18 | +6 | +116 | -0.89 | 0.39 | bias 1 89%, u_nom 11% |

Phase of a torque against the body rate x in 0.25-0.90 Hz: +/-180 = pure damping, 0 = anti-damping, +/-90 = no energy. Re H u_ad / Re H u_nom > 0: u_ad adds damping like the PID; < 0: removes it.

## What-if: same weights, other omega_u (open-loop replay of the logged raw sum)

Band phase u_ad/x (deg) / Re H ratio, as above. Closed loop would differ (the weights would evolve differently); this isolates the filter lag.

| seg | axis | flown | 10 rad/s | 15 rad/s | 20 rad/s |
|---|---|---|---|---|---|
| F1 MRAC | pitch | -115 / +0.54 | -101 / +0.26 | -97 / +0.18 | -96 / +0.14 |
| F1 MRAC | roll | +83 / -0.02 | +105 / +0.05 | +114 / +0.08 | +120 / +0.10 |

## Weights Theta (start -> end of each MRAC segment)

| seg | axis | Theta[0] bias 1 | Theta[1] x | Theta[2] x tanh x | Theta[3] cross | Theta[4] u_nom | Theta[5] xm | |Theta| growth |
|---|---|---|---|---|---|---|---|---|
| F1 MRAC | pitch | +0.000 -> +0.000 | +0.000 -> +0.000 | +0.000 -> +0.000 | +0.000 -> +0.000 | +0.000 -> +0.003 | +0.000 -> +0.008 | 0.000 -> 0.009 |
| F1 MRAC | roll | +0.000 -> +0.000 | +0.000 -> +0.000 | +0.000 -> +0.000 | +0.000 -> +0.000 | +0.000 -> +0.003 | +0.000 -> +0.006 | 0.000 -> 0.006 |
| F1 MRAC | yaw | +0.000 -> +0.071 | +0.000 -> +0.001 | +0.000 -> +0.000 | +0.000 -> +0.000 | +0.000 -> +0.005 | +0.000 -> +0.010 | 0.000 -> 0.072 |
| F1 MRAC | z_rate | +0.000 -> +0.840 | +0.000 -> +0.001 | +0.000 -> +0.004 | +0.000 -> +0.000 | +0.000 -> +0.191 | +0.000 -> +0.041 | 0.000 -> 0.863 |

## Ext block (vp basis 8, 24 Gaussians per axis, pitch/roll)

Grid inputs in the firmware's normalised units (rate, angle); the Gaussian centres span -1..1 (basis 5: -1.5..1.5 rate, -1..1 angle). Activity = sd / mean of the most varying Gaussian over the segment: near 0 the grid acts as one constant (a bias), not as a function of the state. Ext share = ext RMS^2 / (ext RMS^2 + S6-part RMS^2). Slope = d|Theta_ext|/dt over the last third (> 0: still learning).

| seg | axis | rate p1 / p50 / p99 | angle p1 / p50 / p99 | activity | ext RMS | S6 part RMS | ext share | |Theta_ext| start -> end | slope (/s) | top ext (RMS share) |
|---|---|---|---|---|---|---|---|---|---|---|
| F1 MRAC | pitch | -0.163 / +0.002 / +0.137 | -0.549 / -0.027 / +0.528 | 0.608 | 0.0830 | 0.0011 | 100% | 0.000 -> 0.061 | +0.0006 | e2 16%, e8 15% |
| F1 MRAC | roll | -0.177 / -0.001 / +0.187 | -0.766 / -0.051 / +0.828 | 0.728 | 0.0534 | 0.0009 | 100% | 0.000 -> 0.046 | +0.0007 | e2 27%, e8 23% |

### Ext ranking (every Gaussian, RMS^2 share of the ext block, cumulative in brackets)

| seg | axis | ranked |
|---|---|---|
| F1 MRAC | pitch | e2 15.7% [16%], e8 15.2% [31%], e3 12.7% [44%], e14 11.6% [55%], e9 9.9% [65%], e15 4.7% [70%], e20 4.4% [74%], e16 2.8% [77%], e10 2.6% [79%], e4 2.3% [82%], e22 2.2% [84%], e0 2.0% [86%], e6 2.0% [88%], e5 1.8% [90%], e1 1.7% [92%], e11 1.6% [93%], e12 1.6% [95%], e7 1.3% [96%], e17 1.0% [97%], e21 1.0% [98%], e13 0.7% [99%], e18 0.7% [99%], e23 0.4% [100%], e19 0.2% [100%] |
| F1 MRAC | roll | e2 26.7% [27%], e8 23.1% [50%], e3 11.2% [61%], e6 5.6% [67%], e14 5.2% [72%], e0 4.8% [77%], e9 4.6% [81%], e12 3.1% [84%], e4 2.8% [87%], e20 2.4% [90%], e1 2.1% [92%], e10 1.9% [94%], e7 1.3% [95%], e5 1.1% [96%], e15 1.0% [97%], e22 0.8% [98%], e13 0.5% [98%], e11 0.4% [99%], e21 0.3% [99%], e16 0.3% [99%], e17 0.3% [100%], e18 0.2% [100%], e19 0.1% [100%], e23 0.1% [100%] |
| F1 MRAC | p+r mean | e2 21.2% [21%], e8 19.2% [40%], e3 12.0% [52%], e14 8.4% [61%], e9 7.3% [68%], e6 3.8% [72%], e0 3.4% [75%], e20 3.4% [79%], e15 2.9% [81%], e4 2.6% [84%], e12 2.3% [86%], e10 2.2% [89%], e1 1.9% [90%], e16 1.5% [92%], e22 1.5% [93%], e5 1.5% [95%], e7 1.3% [96%], e11 1.0% [97%], e21 0.7% [98%], e17 0.6% [99%], e13 0.6% [99%], e18 0.4% [100%], e23 0.3% [100%], e19 0.2% [100%] |

## Static offsets per segment (drift)

Expected static torque of the load at the rope point: pitch 0.112 N m (x 2 cm), roll 0.391 N m (y 7 cm), mg = 5.59 N.

| seg | mean u_nom p / r | mean u_ad p / r | mean Theta[0] p / r | pitch Des - FB (deg) | roll Des - FB | locx Des-FB (cm) | locy Des-FB | locx/y PID U mean | x FB slope (cm/s) | y FB slope | x stick on (%) | x stick vel Des median (cm/s) | x vel FB mean, no stick (cm/s) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| F1 MRAC | -0.0440 / +0.0388 | -0.0754 / +0.0455 | +0.0000 / +0.0000 | -0.20 | -0.19 | -2.0 | -2.7 | -0.6 / -6.0 | -0.00 | -0.31 | 16 | +28 | +0.86 |

## Per segment: attitude, height, motors, battery

| seg | s | pitch sd | roll sd | rate sd p / r (deg/s) | band PSD p / r | z - z_des | motor at 4000 (%) | max motor spread | V mean | stab CPU % mean / max |
|---|---|---|---|---|---|---|---|---|---|---|
| F1 MRAC | 61 | 3.23 | 4.59 | 11.9 / 14.3 | 49.0 / 86.1 | -0.015 | 33.1 | 1485 | 14.34 | 10.1 / 10.5 |

## Uncertainty estimate: does u_ad match the disturbance?

Plant per axis: xdot = b (u_inj + Delta), u_inj = u_nom + (injected) u_ad. b and the delay tau are fitted on 2-8 Hz content of all airborne segments (above the load band; closed-loop fit, so b is biased: the cancel fraction is also given for b x0.7 and x1.4). Delta_hat = LPF3Hz(xdot)/b - LPF3Hz(u_inj(t - tau)) is everything the nominal model does not explain (load torque and swing, CG offset, motor mismatch, drag), in control units. A perfect adaptive layer gives u_ad = -Delta_hat.

- static: mean(-Delta_hat) is the trim the drone needs; u_ad share = mean(u_ad) / mean(-Delta_hat) (the PID integrator carries the rest)
- dynamic (means removed): cancel = 1 - var(u_ad + Delta_hat) / var(Delta_hat); 1 = cancels all, 0 = no help, < 0 = adds disturbance. Band gain / phase of u_ad against -Delta_hat in 0.25-0.90 Hz (ideal 1 / 0 deg).
- ideal learner: cancel of -LPF_w(Delta_hat), what a perfect estimator behind the firmware u_ad filter at w rad/s could do. PID segments: u_ad is the shadow (computed, not injected).

| axis | b | tau (ms) | r (2-8 Hz) |
|---|---|---|---|
| pitch | 46.2 | 50 | 0.72 |
| roll | 46.2 | 50 | 0.71 |
| yaw | 74.3 | 40 | 0.65 |
| z_rate | 23.0 | 40 | 0.40 |

| seg | axis | -Delta static | u_ad mean | u_nom mean | u_ad share | -Delta dyn RMS | u_ad dyn RMS | band gain / phase / coh | cancel b x0.7 / x1 / x1.4 | ideal w 0.5 / 1 / 2 / 4 / 8 / 16 |
|---|---|---|---|---|---|---|---|---|---|---|
| F1 MRAC | pitch | -0.1196 | -0.0754 | -0.0440 | 0.63 | 0.0498 | 0.0328 | 0.12 / -122 / 0.18 | +0.29 / +0.31 / +0.31 | +0.29 / +0.44 / +0.57 / +0.71 / +0.84 / +0.94 |
| F1 MRAC | roll | +0.0843 | +0.0455 | +0.0388 | 0.54 | 0.0444 | 0.0267 | 0.09 / -125 / 0.12 | +0.16 / +0.20 / +0.21 | +0.29 / +0.41 / +0.54 / +0.68 / +0.82 / +0.93 |
| F1 MRAC | yaw | +0.0619 | +0.0323 | +0.0296 | 0.52 | 0.0582 | 0.0234 | 0.06 / -126 / 0.69 | +0.14 / +0.13 / +0.13 | +0.10 / +0.34 / +0.55 / +0.75 / +0.90 / +0.97 |
| F1 MRAC | z_rate | +2.2237 | +0.8353 | +1.3895 | 0.38 | 0.6153 | 0.2989 | 0.33 / -12 / 0.68 | +0.49 / +0.54 / +0.56 | +0.42 / +0.46 / +0.53 / +0.63 / +0.78 / +0.91 |

## Load swing frequency

Pendulum: L = rope 33 cm + half bottle 10 cm = 0.43 m. Simple sqrt(g/L)/2pi = 0.76 Hz; drone free to move, sqrt(g (M+m) / (M L))/2pi = 0.95 Hz (M 988 g, m 570 g). Measured: peak of the body-rate PSD in 0.15-1.5 Hz.

| seg | pitch peak (Hz) | roll peak (Hz) |
|---|---|---|
| F1 MRAC | 0.49 | 0.49 |

## Outside-force feature: body accel x/y vs the disturbance

Body-frame accel x/y sees only non-thrust forces (rope pull, slosh, wind, walls): thrust is along body z. If the torque is lever arm x force, -Delta_hat = w * a_xy with ONE constant w for any load. Per segment, means removed, both LPF 3 Hz. r = correlation at lag 0; cancel = r^2 = share of the dynamic disturbance a constant weight removes; w in control units per g. Lead = lag (+-300 ms) with the best |r|, positive = accel leads the disturbance. S6 = best single feature of x, x tanh x, cross, xm (u_nom left out: -Delta_hat contains u_nom by construction), then those four together, then together + accel x/y (offline least squares, constant weights). The S6 columns are an UPPER BOUND, partly circular: x and xm together rebuild the PID P term that sits inside -Delta_hat. Accel is an independent sensor, so its columns are clean. Caveat: an IMU off the centre of gravity adds (angular accel x offset), a few mg here.

| seg | axis | acc X r / cancel / w | acc Y r / cancel / w | lead X / Y (ms) | best S6 one | S6 four | S6 four + acc |
|---|---|---|---|---|---|---|---|
| F1 MRAC | pitch | -0.16 / 0.03 / -0.078 | +0.15 / 0.02 / +0.081 | -70 (r -0.17) / +10 (r +0.15) | 0.08 | 0.33 | 0.35 |
| F1 MRAC | roll | -0.11 / 0.01 / -0.047 | -0.01 / 0.00 / -0.007 | -300 (r +0.12) / -300 (r +0.22) | 0.07 | 0.29 | 0.31 |

## Cross-coupling: does the other axis help? (vp16 48-bump split)

-Delta_hat (as above, LPF 3 Hz) fitted with constant weights on Gaussian grids, scored on held-out data (interleaved 5 s blocks: fit even, score odd, and back). Inputs in firmware units: rate / 200 deg/s, tilt / 15 deg. own24 = RBF24T (6 rate x 4 tilt, mrac_t_*_c); own48 = 8 rate x 6 tilt; own32 = 8 rate x 4 tilt; cross16 = own tilt x OTHER tilt (4 x 4); lin = other tilt + other rate as two plain features. Fair split test at 48 bumps: own48 vs own32 + cross16. Swing = same fit on 0.25-0.90 Hz band-passed signals. Upper bound for the own grids (they partly rebuild the PID term inside -Delta_hat), so a cross gain here is the conservative signal. Grid centres of own48/own32 are a test choice, not firmware.

| seg | axis | own24 in / held-out | own24 + lin | own48 | own32 + cross16 | swing own48 / own32 + cross16 |
|---|---|---|---|---|---|---|
| F1 MRAC | pitch | 0.22 / 0.09 | 0.10 | 0.06 | -0.01 | -0.29 / -0.22 |
| F1 MRAC | roll | 0.17 / -0.10 | -0.26 | -0.08 | -0.08 | -0.56 / -0.50 |

## vp16 grid inputs: where the drone sits (centre placement, gamma T rule)

Each vp16 grid input in its normalised unit, p1 / p50 / p99 per segment. pitch/roll: rate / 200 deg/s, tilt / 15 deg, cross = other tilt / 15 deg. yaw: rate / 160 deg/s, heading error ~ gyrozPID.Des / 162 (= Des / Kp 6 / 27 deg, yawPID not logged), cross = Z thrust share. Z: climb rate / 1.0 m/s, thrust share u_nom / u_max, cross = tilt size / 15 deg. x/y: velocity / 300 cm/s, position error / 375 cm, cross = other velocity / 300. |e| = MRAC error (x/y: locxs/locys Des - FB, cm/s) against e_sat; > e_sat = share of time the tanh cap saturates.

| seg | axis | in1 p1 / p50 / p99 | in2 p1 / p50 / p99 | cross p1 / p50 / p99 | abs e p50 / p95 / p99 | e_sat | > e_sat (%) |
|---|---|---|---|---|---|---|---|
| F1 MRAC | pitch | -0.16 / +0.00 / +0.14 | -0.55 / -0.03 / +0.53 | -0.77 / -0.05 / +0.83 | 0.097 / 0.371 / 0.695 | 0.5 | 2.5 |
| F1 MRAC | roll | -0.18 / -0.00 / +0.19 | -0.77 / -0.05 / +0.83 | -0.55 / -0.03 / +0.53 | 0.093 / 0.335 / 0.491 | 0.5 | 0.9 |
| F1 MRAC | yaw | -0.11 / +0.00 / +0.11 | -0.17 / +0.01 / +0.20 | -0.00 / +0.10 / +0.17 | 0.094 / 0.304 / 0.427 | 0.7 | 0.0 |
| F1 MRAC | z_rate | -3.78 / -0.00 / +1.22 | -0.00 / +0.10 / +0.17 | +0.02 / +0.27 / +1.01 | 0.127 / 0.763 / 4.681 | 0.4 | 14.6 |
| F1 MRAC | x | -0.32 / +0.00 / +0.33 | -0.28 / -0.00 / +0.13 | -0.19 / +0.00 / +0.13 | 13.752 / 72.106 / 106.058 | 45 | 15.2 |
| F1 MRAC | y | -0.19 / +0.00 / +0.13 | -0.06 / -0.01 / +0.09 | -0.32 / +0.00 / +0.33 | 14.103 / 47.025 / 70.235 | 45 | 5.9 |

## Feature capability (all MRAC segments pooled)

Theta_i moves at gamma_i * s * phi_i / (1 + |phi|^2) (API/mrac.c:748, denom :932), so a feature's learning drive is gamma_i * E[phi_i^2 / (1 + |phi|^2)] (speed, shown relative to the bias). Its reach is lim_i * RMS(phi_i): the largest torque it can make with Theta_i at its projection bound. gamma_i, lim_i from the MRAC_BASIS rows (pitch/roll/yaw/z), mrac_g_phi and the preset g assumed 1.

| axis | feature | RMS phi | gamma | drive E[phi^2/(1+|phi|^2)] | speed vs bias | lim | reach lim*RMS phi | actual RMS Theta*phi | Theta0 63% time (s) per seg |
|---|---|---|---|---|---|---|---|---|---|
| pitch | bias 1 | 1 | 1.50 | 0.476 | 1 | 0.150 | 0.15 | 0 | F1 - |
| pitch | x | 0.208 | 0.20 | 0.0188 | 0.0053 | 0.050 | 0.0104 | 0 |  |
| pitch | x tanh x | 0.0779 | 0.05 | 0.0024 | 0.00017 | 0.020 | 0.00156 | 0 |  |
| pitch | cross | 0.0363 | 0.05 | 0.000589 | 4.1e-05 | 0.050 | 0.00182 | 0 |  |
| pitch | u_nom | 0.0885 | 0.10 | 0.00361 | 0.00051 | 0.200 | 0.0177 | 0.000121 |  |
| pitch | xm | 0.232 | 0.10 | 0.023 | 0.0032 | 0.150 | 0.0348 | 0.00101 |  |
| roll | bias 1 | 1 | 1.50 | 0.47 | 1 | 0.150 | 0.15 | 0 | F1 - |
| roll | x | 0.249 | 0.20 | 0.0255 | 0.0072 | 0.050 | 0.0125 | 0 |  |
| roll | x tanh x | 0.109 | 0.05 | 0.00437 | 0.00031 | 0.020 | 0.00217 | 0 |  |
| roll | cross | 0.0356 | 0.05 | 0.000563 | 4e-05 | 0.050 | 0.00178 | 0 |  |
| roll | u_nom | 0.0858 | 0.10 | 0.00337 | 0.00048 | 0.200 | 0.0172 | 0.000126 |  |
| roll | xm | 0.251 | 0.10 | 0.0265 | 0.0038 | 0.150 | 0.0377 | 0.000866 |  |
| yaw | bias 1 | 1 | 1.00 | 0.489 | 1 | 0.090 | 0.09 | 0.0401 | F1 13 |
| yaw | x | 0.126 | 0.10 | 0.00762 | 0.0016 | 0.030 | 0.00379 | 8.69e-05 |  |
| yaw | x tanh x | 0.0284 | 0.05 | 0.000378 | 3.9e-05 | 0.012 | 0.000341 | 7.91e-06 |  |
| yaw | cross | 0 | 0.05 | 0 | 0 | 0.030 | 0 | 0 |  |
| yaw | u_nom | 0.0673 | 0.10 | 0.00218 | 0.00044 | 0.120 | 0.00808 | 0.000215 |  |
| yaw | xm | 0.153 | 0.10 | 0.0111 | 0.0023 | 0.090 | 0.0137 | 0.000953 |  |
| z_rate | bias 1 | 1 | 2.00 | 0.249 | 1 | 1.000 | 1 | 0.685 | F1 7 |
| z_rate | x | 0.822 | 0.50 | 0.0237 | 0.024 | 0.100 | 0.0822 | 0.00164 |  |
| z_rate | x tanh x | 0.796 | 0.10 | 0.0144 | 0.0029 | 0.050 | 0.0398 | 0.00172 |  |
| z_rate | cross | 0 | 0.10 | 0 | 0 | 0.050 | 0 | 0 |  |
| z_rate | u_nom | 1.46 | 0.20 | 0.455 | 0.18 | 0.200 | 0.292 | 0.236 |  |
| z_rate | xm | 0.213 | 0.20 | 0.00912 | 0.0037 | 0.200 | 0.0426 | 0.00515 |  |

