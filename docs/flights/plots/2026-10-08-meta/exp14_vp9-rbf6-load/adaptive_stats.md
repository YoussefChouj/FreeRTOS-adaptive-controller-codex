# Adaptive-layer stats: `exp14_vp9-rbf6-load`

Measured from the log. Basis rebuilt from logged signals (see the tool docstring). Shaded: blue PID, orange MRAC injected.

## Per MRAC segment and axis

| seg | axis | rebuild r | u_ad RMS | u_nom RMS | ratio | r(u_ad,u_nom) | r(u_ad,x) | band phase u_ad/x (deg) | band phase u_nom/x | Re H u_ad / Re H u_nom | coh | top feature (RMS share) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| F1 MRAC | pitch | 1.00 | 0.0242 | 0.0914 | 0.26 | -0.04 | +0.01 | -28 | -82 | 0.15 | 0.19 | xm 78%, u_nom 22% |
| F1 MRAC | roll | 1.00 | 0.0146 | 0.0881 | 0.17 | -0.03 | -0.02 | -43 | -78 | 1.05 | 0.21 | xm 77%, u_nom 23% |
| F1 MRAC | yaw | 1.00 | 0.0176 | 0.0723 | 0.24 | -0.06 | +0.00 | +57 | +118 | -0.04 | 0.52 | bias 1 100%, u_nom 0% |
| F1 MRAC | z_rate | 0.85 | 0.4378 | 0.9512 | 0.46 | +0.00 | +0.16 | -15 | +171 | -0.92 | 0.53 | bias 1 94%, u_nom 6% |

Phase of a torque against the body rate x in 0.25-0.90 Hz: +/-180 = pure damping, 0 = anti-damping, +/-90 = no energy. Re H u_ad / Re H u_nom > 0: u_ad adds damping like the PID; < 0: removes it.

## What-if: same weights, other omega_u (open-loop replay of the logged raw sum)

Band phase u_ad/x (deg) / Re H ratio, as above. Closed loop would differ (the weights would evolve differently); this isolates the filter lag.

| seg | axis | flown | 10 rad/s | 15 rad/s | 20 rad/s |
|---|---|---|---|---|---|
| F1 MRAC | pitch | -28 / +0.14 | -10 / +0.20 | -4 / +0.22 | -2 / +0.22 |
| F1 MRAC | roll | -43 / +1.03 | -34 / +1.11 | -29 / +1.13 | -27 / +1.14 |

## Weights Theta (start -> end of each MRAC segment)

| seg | axis | Theta[0] bias 1 | Theta[1] x | Theta[2] x tanh x | Theta[3] cross | Theta[4] u_nom | Theta[5] xm | |Theta| growth |
|---|---|---|---|---|---|---|---|---|
| F1 MRAC | pitch | +0.000 -> +0.000 | +0.000 -> +0.000 | +0.000 -> +0.000 | +0.000 -> +0.000 | +0.000 -> +0.003 | +0.000 -> +0.003 | 0.000 -> 0.004 |
| F1 MRAC | roll | +0.000 -> +0.000 | +0.000 -> +0.000 | +0.000 -> +0.000 | +0.000 -> +0.000 | +0.000 -> +0.003 | +0.000 -> +0.003 | 0.000 -> 0.004 |
| F1 MRAC | yaw | +0.000 -> +0.032 | +0.000 -> +0.000 | +0.000 -> +0.001 | +0.000 -> +0.000 | +0.000 -> +0.003 | +0.000 -> +0.002 | 0.000 -> 0.032 |
| F1 MRAC | z_rate | +0.000 -> +0.299 | +0.000 -> +0.000 | +0.000 -> +0.003 | +0.000 -> +0.000 | +0.000 -> +0.141 | +0.000 -> +0.022 | 0.000 -> 0.331 |

## Ext block (vp basis 2, 6 Gaussians per axis, pitch/roll)

Grid inputs in the firmware's normalised units (rate, angle); the Gaussian centres span -1..1 (basis 5: -1.5..1.5 rate, -1..1 angle). Activity = sd / mean of the most varying Gaussian over the segment: near 0 the grid acts as one constant (a bias), not as a function of the state. Ext share = ext RMS^2 / (ext RMS^2 + S6-part RMS^2). Slope = d|Theta_ext|/dt over the last third (> 0: still learning).

| seg | axis | rate p1 / p50 / p99 | angle p1 / p50 / p99 | activity | ext RMS | S6 part RMS | ext share | |Theta_ext| start -> end | slope (/s) | top ext (RMS share) |
|---|---|---|---|---|---|---|---|---|---|---|
| F1 MRAC | pitch | -0.086 / -0.001 / +0.091 | -0.176 / -0.044 / +0.111 | 0.043 | 0.0242 | 0.0004 | 100% | 0.000 -> 0.016 | +0.0001 | e2 41%, e3 38% |
| F1 MRAC | roll | -0.083 / -0.000 / +0.079 | -0.170 / -0.041 / +0.141 | 0.041 | 0.0146 | 0.0003 | 100% | 0.000 -> 0.006 | +0.0000 | e2 39%, e3 39% |

### Ext ranking (every Gaussian, RMS^2 share of the ext block, cumulative in brackets)

| seg | axis | ranked |
|---|---|---|
| F1 MRAC | pitch | e2 41.0% [41%], e3 37.6% [79%], e4 6.2% [85%], e5 5.7% [90%], e0 5.0% [95%], e1 4.6% [100%] |
| F1 MRAC | roll | e2 39.4% [39%], e3 39.1% [78%], e0 6.4% [85%], e1 6.3% [91%], e4 4.4% [96%], e5 4.4% [100%] |
| F1 MRAC | p+r mean | e2 40.2% [40%], e3 38.3% [78%], e0 5.7% [84%], e1 5.4% [90%], e4 5.3% [95%], e5 5.1% [100%] |

## Static offsets per segment (drift)

Expected static torque of the load at the rope point: pitch 0.112 N m (x 2 cm), roll 0.391 N m (y 7 cm), mg = 5.59 N.

| seg | mean u_nom p / r | mean u_ad p / r | mean Theta[0] p / r | pitch Des - FB (deg) | roll Des - FB | locx Des-FB (cm) | locy Des-FB | locx/y PID U mean | x FB slope (cm/s) | y FB slope | x stick on (%) | x stick vel Des median (cm/s) | x vel FB mean, no stick (cm/s) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| F1 MRAC | -0.0542 / +0.0431 | -0.0233 / +0.0140 | +0.0000 / +0.0000 | -0.01 | -0.12 | -0.2 | -9.0 | +0.9 / -10.9 | -2.62 | -0.62 | 4 | -20 | +0.44 |
| F1 PID | -0.0671 / +0.0647 | -0.0276 / +0.0128 | +0.0000 / +0.0000 | +0.85 | -0.11 | +3.9 | -13.9 | +7.9 / -16.3 | +3.17 | +3.47 | 0 | +nan | +4.66 |

## Per segment: attitude, height, motors, battery

| seg | s | pitch sd | roll sd | rate sd p / r (deg/s) | band PSD p / r | z - z_des | motor at 4000 (%) | max motor spread | V mean | stab CPU % mean / max |
|---|---|---|---|---|---|---|---|---|---|---|
| F1 MRAC | 40 | 1.85 | 1.75 | 10.6 / 10.0 | 15.1 / 11.6 | +0.017 | 1.5 | 1263 | 15.20 | 8.3 / 8.6 |
| F1 PID | 6 | 1.80 | 2.25 | 11.3 / 10.3 | 8.7 / 25.3 | +0.027 | 2.1 | 1289 | 15.02 | 7.8 / 8.3 |

## Uncertainty estimate: does u_ad match the disturbance?

Plant per axis: xdot = b (u_inj + Delta), u_inj = u_nom + (injected) u_ad. b and the delay tau are fitted on 2-8 Hz content of all airborne segments (above the load band; closed-loop fit, so b is biased: the cancel fraction is also given for b x0.7 and x1.4). Delta_hat = LPF3Hz(xdot)/b - LPF3Hz(u_inj(t - tau)) is everything the nominal model does not explain (load torque and swing, CG offset, motor mismatch, drag), in control units. A perfect adaptive layer gives u_ad = -Delta_hat.

- static: mean(-Delta_hat) is the trim the drone needs; u_ad share = mean(u_ad) / mean(-Delta_hat) (the PID integrator carries the rest)
- dynamic (means removed): cancel = 1 - var(u_ad + Delta_hat) / var(Delta_hat); 1 = cancels all, 0 = no help, < 0 = adds disturbance. Band gain / phase of u_ad against -Delta_hat in 0.25-0.90 Hz (ideal 1 / 0 deg).
- ideal learner: cancel of -LPF_w(Delta_hat), what a perfect estimator behind the firmware u_ad filter at w rad/s could do. PID segments: u_ad is the shadow (computed, not injected).

| axis | b | tau (ms) | r (2-8 Hz) |
|---|---|---|---|
| pitch | 66.5 | 50 | 0.82 |
| roll | 60.9 | 40 | 0.77 |
| yaw | 45.4 | 20 | 0.60 |
| z_rate | 33.0 | 50 | 0.58 |

| seg | axis | -Delta static | u_ad mean | u_nom mean | u_ad share | -Delta dyn RMS | u_ad dyn RMS | band gain / phase / coh | cancel b x0.7 / x1 / x1.4 | ideal w 0.5 / 1 / 2 / 4 / 8 / 16 |
|---|---|---|---|---|---|---|---|---|---|---|
| F1 MRAC | pitch | -0.0777 | -0.0233 | -0.0542 | 0.30 | 0.0270 | 0.0063 | 0.01 / -120 / 0.13 | +0.01 / +0.01 / +0.01 | -0.28 / -0.07 / +0.11 / +0.36 / +0.67 / +0.89 |
| F1 PID | pitch | -0.0680 | -0.0276 | -0.0671 | 0.41 | 0.0402 | 0.0006 | 0.01 / -12 / 0.92 | +0.03 / +0.03 / +0.03 | +0.17 / +0.41 / +0.61 / +0.75 / +0.87 / +0.95 |
| F1 MRAC | roll | +0.0570 | +0.0140 | +0.0431 | 0.24 | 0.0232 | 0.0042 | 0.02 / -107 / 0.08 | -0.00 / +0.00 / +0.00 | -0.35 / -0.01 / +0.25 / +0.49 / +0.74 / +0.91 |
| F1 PID | roll | +0.0632 | +0.0128 | +0.0647 | 0.20 | 0.0242 | 0.0005 | 0.01 / -51 / 0.23 | +0.02 / +0.02 / +0.02 | -0.70 / -0.56 / -0.22 / +0.16 / +0.56 / +0.84 |
| F1 MRAC | yaw | +0.0319 | +0.0140 | +0.0177 | 0.44 | 0.0513 | 0.0106 | 0.05 / -93 / 0.45 | -0.00 / +0.00 / +0.01 | -0.28 / -0.06 / +0.14 / +0.41 / +0.74 / +0.93 |
| F1 PID | yaw | +0.0544 | +0.0348 | +0.0545 | 0.64 | 0.0674 | 0.0002 | 0.00 / -63 / 0.83 | -0.00 / -0.00 / +0.00 | -0.05 / +0.00 / +0.12 / +0.39 / +0.72 / +0.92 |
| F1 MRAC | z_rate | +1.2822 | +0.4182 | +0.8646 | 0.33 | 0.3401 | 0.1296 | 0.18 / -6 / 0.67 | +0.19 / +0.19 / +0.19 | +0.10 / +0.14 / +0.23 / +0.42 / +0.69 / +0.89 |
| F1 PID | z_rate | +1.1332 | +0.4291 | +1.1278 | 0.38 | 0.4556 | 0.0719 | 0.15 / +3 / 0.99 | +0.29 / +0.29 / +0.28 | +0.40 / +0.58 / +0.72 / +0.81 / +0.89 / +0.96 |

## Load swing frequency

Pendulum: L = rope 33 cm + half bottle 10 cm = 0.43 m. Simple sqrt(g/L)/2pi = 0.76 Hz; drone free to move, sqrt(g (M+m) / (M L))/2pi = 0.95 Hz (M 988 g, m 570 g). Measured: peak of the body-rate PSD in 0.15-1.5 Hz.

| seg | pitch peak (Hz) | roll peak (Hz) |
|---|---|---|
| F1 MRAC | 0.88 | 0.88 |
| F1 PID | 0.96 | 0.64 |

## Outside-force feature: body accel x/y vs the disturbance

Body-frame accel x/y sees only non-thrust forces (rope pull, slosh, wind, walls): thrust is along body z. If the torque is lever arm x force, -Delta_hat = w * a_xy with ONE constant w for any load. Per segment, means removed, both LPF 3 Hz. r = correlation at lag 0; cancel = r^2 = share of the dynamic disturbance a constant weight removes; w in control units per g. Lead = lag (+-300 ms) with the best |r|, positive = accel leads the disturbance. S6 = best single feature of x, x tanh x, cross, xm (u_nom left out: -Delta_hat contains u_nom by construction), then those four together, then together + accel x/y (offline least squares, constant weights). The S6 columns are an UPPER BOUND, partly circular: x and xm together rebuild the PID P term that sits inside -Delta_hat. Accel is an independent sensor, so its columns are clean. Caveat: an IMU off the centre of gravity adds (angular accel x offset), a few mg here.

| seg | axis | acc X r / cancel / w | acc Y r / cancel / w | lead X / Y (ms) | best S6 one | S6 four | S6 four + acc |
|---|---|---|---|---|---|---|---|
| F1 MRAC | pitch | -0.60 / 0.35 / -0.150 | -0.12 / 0.01 / -0.039 | +60 (r -0.64) / +150 (r -0.21) | 0.07 | 0.48 | 0.57 |
| F1 PID | pitch | -0.51 / 0.26 / -0.233 | -0.16 / 0.03 / -0.077 | +60 (r -0.52) / +300 (r +0.26) | 0.50 | 0.79 | 0.82 |
| F1 MRAC | roll | -0.12 / 0.01 / -0.025 | -0.41 / 0.17 / -0.117 | -190 (r -0.24) / +80 (r -0.47) | 0.10 | 0.48 | 0.55 |
| F1 PID | roll | -0.43 / 0.18 / -0.119 | -0.58 / 0.33 / -0.166 | +90 (r -0.54) / -60 (r -0.61) | 0.07 | 0.51 | 0.66 |

## Cross-coupling: does the other axis help? (vp16 48-bump split)

-Delta_hat (as above, LPF 3 Hz) fitted with constant weights on Gaussian grids, scored on held-out data (interleaved 5 s blocks: fit even, score odd, and back). Inputs in firmware units: rate / 200 deg/s, tilt / 15 deg. own24 = RBF24T (6 rate x 4 tilt, mrac_t_*_c); own48 = 8 rate x 6 tilt; own32 = 8 rate x 4 tilt; cross16 = own tilt x OTHER tilt (4 x 4); lin = other tilt + other rate as two plain features. Fair split test at 48 bumps: own48 vs own32 + cross16. Swing = same fit on 0.25-0.90 Hz band-passed signals. Upper bound for the own grids (they partly rebuild the PID term inside -Delta_hat), so a cross gain here is the conservative signal. Grid centres of own48/own32 are a test choice, not firmware.

| seg | axis | own24 in / held-out | own24 + lin | own48 | own32 + cross16 | swing own48 / own32 + cross16 |
|---|---|---|---|---|---|---|
| F1 MRAC | pitch | 0.42 / 0.38 | 0.34 | 0.36 | 0.36 | 0.12 / 0.15 |
| F1 PID | pitch | 0.67 / -0.22 | -0.59 | -0.91 | -1.51 | -2.42 / -1.92 |
| F1 MRAC | roll | 0.35 / 0.23 | 0.26 | 0.18 | 0.18 | -0.08 / 0.04 |
| F1 PID | roll | 0.43 / -0.48 | -1.20 | -0.76 | -1.88 | -7.23 / -58.66 |

## vp16 grid inputs: where the drone sits (centre placement, gamma T rule)

Each vp16 grid input in its normalised unit, p1 / p50 / p99 per segment. pitch/roll: rate / 200 deg/s, tilt / 15 deg, cross = other tilt / 15 deg. yaw: rate / 160 deg/s, heading error ~ gyrozPID.Des / 162 (= Des / Kp 6 / 27 deg, yawPID not logged), cross = Z thrust share. Z: climb rate / 1.0 m/s, thrust share u_nom / u_max, cross = tilt size / 15 deg. x/y: velocity / 300 cm/s, position error / 375 cm, cross = other velocity / 300. |e| = MRAC error (x/y: locxs/locys Des - FB, cm/s) against e_sat; > e_sat = share of time the tanh cap saturates.

| seg | axis | in1 p1 / p50 / p99 | in2 p1 / p50 / p99 | cross p1 / p50 / p99 | abs e p50 / p95 / p99 | e_sat | > e_sat (%) |
|---|---|---|---|---|---|---|---|
| F1 MRAC | pitch | -0.12 / -0.00 / +0.13 | -0.34 / -0.08 / +0.21 | -0.32 / -0.08 / +0.27 | 0.093 / 0.295 / 0.371 | 0.5 | 0.1 |
| F1 MRAC | roll | -0.12 / -0.00 / +0.11 | -0.32 / -0.08 / +0.27 | -0.34 / -0.08 / +0.21 | 0.095 / 0.266 / 0.343 | 0.5 | 0.0 |
| F1 MRAC | yaw | -0.33 / +0.00 / +0.17 | -0.36 / +0.00 / +0.15 | -0.03 / +0.06 / +0.16 | 0.107 / 0.285 / 0.468 | 0.7 | 0.0 |
| F1 MRAC | z_rate | -1.09 / -0.00 / +0.86 | -0.03 / +0.06 / +0.16 | +0.03 / +0.17 / +0.37 | 0.086 / 0.597 / 3.602 | 0.4 | 10.9 |
| F1 MRAC | x | -0.15 / -0.00 / +0.09 | -0.06 / +0.00 / +0.10 | -0.18 / -0.00 / +0.15 | 11.831 / 28.910 / 41.898 | 45 | 0.5 |
| F1 MRAC | y | -0.18 / -0.00 / +0.15 | -0.06 / -0.02 / +0.01 | -0.15 / -0.00 / +0.09 | 17.708 / 50.341 / 58.552 | 45 | 8.8 |
| F1 PID | pitch | -0.16 / +0.00 / +0.13 | -0.40 / -0.15 / +0.09 | -0.29 / -0.01 / +0.22 | 0.120 / 0.396 / 0.620 | 0.5 | 3.4 |
| F1 PID | roll | -0.12 / +0.00 / +0.11 | -0.29 / -0.01 / +0.22 | -0.40 / -0.15 / +0.09 | 0.073 / 0.240 / 0.296 | 0.5 | 0.2 |
| F1 PID | yaw | -0.16 / +0.01 / +0.15 | -0.16 / +0.02 / +0.20 | -0.02 / +0.10 / +0.16 | 0.121 / 0.348 / 0.393 | 0.7 | 0.0 |
| F1 PID | z_rate | -0.84 / -0.01 / +0.41 | -0.02 / +0.10 / +0.16 | +0.03 / +0.23 / +0.41 | 0.188 / 0.593 / 0.730 | 0.4 | 17.0 |
| F1 PID | x | -0.10 / +0.01 / +0.11 | -0.02 / +0.01 / +0.06 | -0.11 / +0.02 / +0.16 | 15.575 / 32.997 / 36.903 | 45 | 0.0 |
| F1 PID | y | -0.11 / +0.02 / +0.16 | -0.07 / -0.04 / +0.01 | -0.10 / +0.01 / +0.11 | 22.016 / 57.154 / 63.633 | 45 | 20.8 |

## Feature capability (all MRAC segments pooled)

Theta_i moves at gamma_i * s * phi_i / (1 + |phi|^2) (API/mrac.c:748, denom :932), so a feature's learning drive is gamma_i * E[phi_i^2 / (1 + |phi|^2)] (speed, shown relative to the bias). Its reach is lim_i * RMS(phi_i): the largest torque it can make with Theta_i at its projection bound. gamma_i, lim_i from the MRAC_BASIS rows (pitch/roll/yaw/z), mrac_g_phi and the preset g assumed 1.

| axis | feature | RMS phi | gamma | drive E[phi^2/(1+|phi|^2)] | speed vs bias | lim | reach lim*RMS phi | actual RMS Theta*phi | Theta0 63% time (s) per seg |
|---|---|---|---|---|---|---|---|---|---|
| pitch | bias 1 | 1 | 1.50 | 0.483 | 1 | 0.150 | 0.15 | 0 | F1 - |
| pitch | x | 0.185 | 0.20 | 0.0157 | 0.0043 | 0.050 | 0.00925 | 0 |  |
| pitch | x tanh x | 0.0588 | 0.05 | 0.00151 | 0.0001 | 0.020 | 0.00118 | 0 |  |
| pitch | cross | 0.042 | 0.05 | 0.000845 | 5.8e-05 | 0.050 | 0.0021 | 0 |  |
| pitch | u_nom | 0.0914 | 0.10 | 0.00399 | 0.00055 | 0.200 | 0.0183 | 0.000167 |  |
| pitch | xm | 0.161 | 0.10 | 0.0121 | 0.0017 | 0.150 | 0.0242 | 0.000318 |  |
| roll | bias 1 | 1 | 1.50 | 0.485 | 1 | 0.150 | 0.15 | 0 | F1 - |
| roll | x | 0.175 | 0.20 | 0.0143 | 0.0039 | 0.050 | 0.00875 | 0 |  |
| roll | x tanh x | 0.0502 | 0.05 | 0.00113 | 7.8e-05 | 0.020 | 0.001 | 0 |  |
| roll | cross | 0.0388 | 0.05 | 0.000726 | 5e-05 | 0.050 | 0.00194 | 0 |  |
| roll | u_nom | 0.0881 | 0.10 | 0.00373 | 0.00051 | 0.200 | 0.0176 | 0.000153 |  |
| roll | xm | 0.145 | 0.10 | 0.00996 | 0.0014 | 0.150 | 0.0218 | 0.000278 |  |
| yaw | bias 1 | 1 | 1.00 | 0.481 | 1 | 0.090 | 0.09 | 0.0179 | F1 2 |
| yaw | x | 0.241 | 0.10 | 0.0225 | 0.0047 | 0.030 | 0.00723 | 2.29e-05 |  |
| yaw | x tanh x | 0.125 | 0.05 | 0.00426 | 0.00044 | 0.012 | 0.0015 | 7.36e-05 |  |
| yaw | cross | 0 | 0.05 | 0 | 0 | 0.030 | 0 | 0 |  |
| yaw | u_nom | 0.0723 | 0.10 | 0.00248 | 0.00052 | 0.120 | 0.00867 | 0.000126 |  |
| yaw | xm | 0.156 | 0.10 | 0.00934 | 0.0019 | 0.090 | 0.014 | 0.000126 |  |
| z_rate | bias 1 | 1 | 2.00 | 0.35 | 1 | 1.000 | 1 | 0.37 | F1 3 |
| z_rate | x | 0.705 | 0.50 | 0.0188 | 0.013 | 0.100 | 0.0705 | 0.0006 |  |
| z_rate | x tanh x | 0.68 | 0.10 | 0.00913 | 0.0013 | 0.050 | 0.034 | 0.00181 |  |
| z_rate | cross | 0 | 0.10 | 0 | 0 | 0.050 | 0 | 0 |  |
| z_rate | u_nom | 0.951 | 0.20 | 0.264 | 0.075 | 0.200 | 0.19 | 0.0909 |  |
| z_rate | xm | 0.179 | 0.20 | 0.00904 | 0.0026 | 0.200 | 0.0359 | 0.00176 |  |

