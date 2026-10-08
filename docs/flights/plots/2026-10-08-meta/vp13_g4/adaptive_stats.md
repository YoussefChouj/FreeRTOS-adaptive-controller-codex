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

### Ext ranking (every Gaussian, RMS^2 share of the ext block, cumulative in brackets)

| seg | axis | ranked |
|---|---|---|
| F1 MRAC | pitch | e13 (r+0.1 a-0.2) 36.1% [36%], e14 (r+0.1 a+0.2) 19.9% [56%], e17 (r+0.3 a-0.2) 11.1% [67%], e9 (r-0.1 a-0.2) 10.7% [78%], e18 (r+0.3 a+0.2) 6.4% [84%], e12 (r+0.1 a-0.6) 4.5% [89%], e10 (r-0.1 a+0.2) 3.4% [92%], e5 (r-0.3 a-0.2) 2.2% [94%], e8 (r-0.1 a-0.6) 1.8% [96%], e16 (r+0.3 a-0.6) 1.3% [97%], e15 (r+0.1 a+0.6) 0.8% [98%], e6 (r-0.3 a+0.2) 0.7% [99%], e4 (r-0.3 a-0.6) 0.4% [99%], e21 (r+0.7 a-0.2) 0.2% [99%], e19 (r+0.3 a+0.6) 0.2% [100%], e22 (r+0.7 a+0.2) 0.1% [100%], e11 (r-0.1 a+0.6) 0.1% [100%], e1 (r-0.7 a-0.2) 0.0% [100%], e20 (r+0.7 a-0.6) 0.0% [100%], e7 (r-0.3 a+0.6) 0.0% [100%], e2 (r-0.7 a+0.2) 0.0% [100%], e23 (r+0.7 a+0.6) 0.0% [100%], e0 (r-0.7 a-0.6) 0.0% [100%], e3 (r-0.7 a+0.6) 0.0% [100%] |
| F1 MRAC | roll | e9 (r-0.1 a-0.2) 33.7% [34%], e10 (r-0.1 a+0.2) 31.0% [65%], e5 (r-0.3 a-0.2) 10.9% [76%], e6 (r-0.3 a+0.2) 9.9% [86%], e13 (r+0.1 a-0.2) 4.2% [90%], e14 (r+0.1 a+0.2) 3.5% [93%], e8 (r-0.1 a-0.6) 1.5% [95%], e18 (r+0.3 a+0.2) 1.2% [96%], e17 (r+0.3 a-0.2) 1.0% [97%], e11 (r-0.1 a+0.6) 0.8% [98%], e4 (r-0.3 a-0.6) 0.5% [98%], e15 (r+0.1 a+0.6) 0.3% [99%], e7 (r-0.3 a+0.6) 0.3% [99%], e1 (r-0.7 a-0.2) 0.3% [99%], e2 (r-0.7 a+0.2) 0.2% [99%], e12 (r+0.1 a-0.6) 0.2% [100%], e19 (r+0.3 a+0.6) 0.2% [100%], e22 (r+0.7 a+0.2) 0.0% [100%], e16 (r+0.3 a-0.6) 0.0% [100%], e21 (r+0.7 a-0.2) 0.0% [100%], e0 (r-0.7 a-0.6) 0.0% [100%], e3 (r-0.7 a+0.6) 0.0% [100%], e23 (r+0.7 a+0.6) 0.0% [100%], e20 (r+0.7 a-0.6) 0.0% [100%] |
| F1 MRAC | p+r mean | e9 (r-0.1 a-0.2) 22.2% [22%], e13 (r+0.1 a-0.2) 20.1% [42%], e10 (r-0.1 a+0.2) 17.2% [60%], e14 (r+0.1 a+0.2) 11.7% [71%], e5 (r-0.3 a-0.2) 6.6% [78%], e17 (r+0.3 a-0.2) 6.1% [84%], e6 (r-0.3 a+0.2) 5.3% [89%], e18 (r+0.3 a+0.2) 3.8% [93%], e12 (r+0.1 a-0.6) 2.3% [95%], e8 (r-0.1 a-0.6) 1.6% [97%], e16 (r+0.3 a-0.6) 0.7% [98%], e15 (r+0.1 a+0.6) 0.5% [98%], e11 (r-0.1 a+0.6) 0.5% [99%], e4 (r-0.3 a-0.6) 0.4% [99%], e19 (r+0.3 a+0.6) 0.2% [99%], e7 (r-0.3 a+0.6) 0.1% [99%], e1 (r-0.7 a-0.2) 0.1% [100%], e21 (r+0.7 a-0.2) 0.1% [100%], e2 (r-0.7 a+0.2) 0.1% [100%], e22 (r+0.7 a+0.2) 0.1% [100%], e20 (r+0.7 a-0.6) 0.0% [100%], e0 (r-0.7 a-0.6) 0.0% [100%], e23 (r+0.7 a+0.6) 0.0% [100%], e3 (r-0.7 a+0.6) 0.0% [100%] |

## Static offsets per segment (drift)

Expected static torque of the load at the rope point: pitch 0.112 N m (x 2 cm), roll 0.391 N m (y 7 cm), mg = 5.59 N.

| seg | mean u_nom p / r | mean u_ad p / r | mean Theta[0] p / r | pitch Des - FB (deg) | roll Des - FB | locx Des-FB (cm) | locy Des-FB | locx/y PID U mean | x FB slope (cm/s) | y FB slope | x stick on (%) | x stick vel Des median (cm/s) | x vel FB mean, no stick (cm/s) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| F1 MRAC | -0.0553 / +0.0269 | -0.0515 / +0.0234 | +0.0000 / +0.0000 | +0.02 | -0.01 | +0.1 | -2.6 | +2.0 / -2.5 | +1.00 | -1.25 | 14 | +33 | -0.04 |

## Per segment: attitude, height, motors, battery

| seg | s | pitch sd | roll sd | rate sd p / r (deg/s) | band PSD p / r | z - z_des | motor at 4000 (%) | max motor spread | V mean | stab CPU % mean / max |
|---|---|---|---|---|---|---|---|---|---|---|
| F1 MRAC | 65 | 2.68 | 2.79 | 9.8 / 9.4 | 19.8 / 25.6 | -0.002 | 2.3 | 1466 | 15.06 | 9.6 / 10.1 |

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

## Cross-coupling: does the other axis help? (vp16 48-bump split)

-Delta_hat (as above, LPF 3 Hz) fitted with constant weights on Gaussian grids, scored on held-out data (interleaved 5 s blocks: fit even, score odd, and back). Inputs in firmware units: rate / 200 deg/s, tilt / 15 deg. own24 = RBF24T (6 rate x 4 tilt, mrac_t_*_c); own48 = 8 rate x 6 tilt; own32 = 8 rate x 4 tilt; cross16 = own tilt x OTHER tilt (4 x 4); lin = other tilt + other rate as two plain features. Fair split test at 48 bumps: own48 vs own32 + cross16. Swing = same fit on 0.25-0.90 Hz band-passed signals. Upper bound for the own grids (they partly rebuild the PID term inside -Delta_hat), so a cross gain here is the conservative signal. Grid centres of own48/own32 are a test choice, not firmware.

| seg | axis | own24 in / held-out | own24 + lin | own48 | own32 + cross16 | swing own48 / own32 + cross16 |
|---|---|---|---|---|---|---|
| F1 MRAC | pitch | 0.25 / 0.19 | 0.20 | 0.15 | 0.14 | -0.90 / -0.59 |
| F1 MRAC | roll | 0.24 / 0.16 | 0.11 | 0.16 | 0.14 | -0.06 / 0.08 |

## vp16 grid inputs: where the drone sits (centre placement, gamma T rule)

Each vp16 grid input in its normalised unit, p1 / p50 / p99 per segment. pitch/roll: rate / 200 deg/s, tilt / 15 deg, cross = other tilt / 15 deg. yaw: rate / 160 deg/s, heading error ~ gyrozPID.Des / 162 (= Des / Kp 6 / 27 deg, yawPID not logged), cross = Z thrust share. Z: climb rate / 1.0 m/s, thrust share u_nom / u_max, cross = tilt size / 15 deg. x/y: velocity / 300 cm/s, position error / 375 cm, cross = other velocity / 300. |e| = MRAC error (x/y: locxs/locys Des - FB, cm/s) against e_sat; > e_sat = share of time the tanh cap saturates.

| seg | axis | in1 p1 / p50 / p99 | in2 p1 / p50 / p99 | cross p1 / p50 / p99 | abs e p50 / p95 / p99 | e_sat | > e_sat (%) |
|---|---|---|---|---|---|---|---|
| F1 MRAC | pitch | -0.12 / -0.00 / +0.12 | -0.62 / -0.02 / +0.34 | -0.60 / -0.01 / +0.55 | 0.079 / 0.255 / 0.360 | 0.5 | 0.2 |
| F1 MRAC | roll | -0.14 / +0.00 / +0.12 | -0.60 / -0.01 / +0.55 | -0.62 / -0.02 / +0.34 | 0.070 / 0.207 / 0.296 | 0.5 | 0.0 |
| F1 MRAC | yaw | -0.13 / +0.00 / +0.51 | -0.15 / -0.00 / +0.50 | -0.04 / +0.07 / +0.17 | 0.087 / 0.315 / 0.607 | 0.7 | 0.4 |
| F1 MRAC | z_rate | -6.21 / -0.00 / +2.58 | -0.04 / +0.07 / +0.17 | +0.02 / +0.17 / +0.76 | 0.075 / 0.934 / 7.198 | 0.4 | 13.4 |
| F1 MRAC | x | -0.34 / +0.00 / +0.22 | -0.17 / +0.00 / +0.12 | -0.29 / -0.00 / +0.28 | 8.325 / 39.533 / 75.706 | 45 | 3.5 |
| F1 MRAC | y | -0.29 / -0.00 / +0.28 | -0.12 / -0.00 / +0.29 | -0.34 / +0.00 / +0.22 | 11.819 / 41.715 / 76.184 | 45 | 3.3 |

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

