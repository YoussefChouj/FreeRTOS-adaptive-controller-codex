# Adaptive-layer stats: `vp12_flight`

Measured from the log. Basis rebuilt from logged signals (see the tool docstring). Shaded: blue PID, orange MRAC injected.

## Per MRAC segment and axis

| seg | axis | rebuild r | u_ad RMS | u_nom RMS | ratio | r(u_ad,u_nom) | r(u_ad,x) | band phase u_ad/x (deg) | band phase u_nom/x | Re H u_ad / Re H u_nom | coh | top feature (RMS share) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| F1 MRAC | pitch | 1.00 | 0.0559 | 0.0783 | 0.71 | -0.05 | -0.05 | +135 | -161 | 0.44 | 0.20 | bias 1 100%, x 0% |
| F1 MRAC | roll | 1.00 | 0.0166 | 0.0728 | 0.23 | -0.02 | -0.11 | +162 | -20 | -0.36 | 0.26 | bias 1 99%, x 1% |
| F1 MRAC | yaw | 1.00 | 0.0290 | 0.0486 | 0.60 | -0.08 | +0.08 | -10 | +117 | -0.07 | 0.54 | bias 1 100%, xm 0% |
| F1 MRAC | z_rate | 0.80 | 0.5327 | 1.0160 | 0.52 | +0.08 | +0.19 | -48 | +167 | -0.45 | 0.54 | bias 1 94%, u_nom 6% |

Phase of a torque against the body rate x in 0.25-0.90 Hz: +/-180 = pure damping, 0 = anti-damping, +/-90 = no energy. Re H u_ad / Re H u_nom > 0: u_ad adds damping like the PID; < 0: removes it.

## What-if: same weights, other omega_u (open-loop replay of the logged raw sum)

Band phase u_ad/x (deg) / Re H ratio, as above. Closed loop would differ (the weights would evolve differently); this isolates the filter lag.

| seg | axis | flown | 10 rad/s | 15 rad/s | 20 rad/s |
|---|---|---|---|---|---|
| F1 MRAC | pitch | +137 / +0.47 | +163 / +0.99 | +173 / +1.12 | +178 / +1.16 |
| F1 MRAC | roll | +162 / -0.37 | +179 / -0.49 | -173 / -0.52 | -169 / -0.52 |

## Weights Theta (start -> end of each MRAC segment)

| seg | axis | Theta[0] bias 1 | Theta[1] x | Theta[2] x tanh x | Theta[3] cross | Theta[4] u_nom | Theta[5] xm | |Theta| growth |
|---|---|---|---|---|---|---|---|---|
| F1 MRAC | pitch | -0.000 -> +0.013 | -0.000 -> -0.019 | +0.000 -> +0.001 | +0.000 -> +0.000 | +0.000 -> +0.005 | +0.000 -> +0.004 | 0.000 -> 0.024 |
| F1 MRAC | roll | +0.000 -> +0.021 | -0.000 -> -0.021 | +0.000 -> +0.001 | +0.000 -> +0.000 | +0.000 -> +0.006 | +0.000 -> +0.003 | 0.000 -> 0.030 |
| F1 MRAC | yaw | +0.000 -> -0.003 | +0.000 -> +0.000 | +0.000 -> +0.000 | +0.000 -> +0.000 | +0.000 -> +0.003 | +0.000 -> +0.004 | 0.000 -> 0.006 |
| F1 MRAC | z_rate | +0.000 -> +0.120 | +0.000 -> +0.009 | +0.000 -> +0.003 | +0.000 -> +0.000 | +0.000 -> +0.141 | +0.000 -> +0.037 | 0.000 -> 0.189 |

## Static offsets per segment (drift)

Expected static torque of the load at the rope point: pitch 0.112 N m (x 2 cm), roll 0.391 N m (y 7 cm), mg = 5.59 N.

| seg | mean u_nom p / r | mean u_ad p / r | mean Theta[0] p / r | pitch Des - FB (deg) | roll Des - FB | locx Des-FB (cm) | locy Des-FB | locx/y PID U mean | x FB slope (cm/s) | y FB slope | x stick on (%) | x stick vel Des median (cm/s) | x vel FB mean, no stick (cm/s) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| F1 MRAC | -0.0367 / +0.0137 | -0.0548 / +0.0136 | -0.0547 / +0.0135 | +0.03 | -0.08 | -3.0 | -4.1 | -2.8 / -5.2 | +3.37 | -0.37 | 7 | +36 | +0.48 |

## Per segment: attitude, height, motors, battery

| seg | s | pitch sd | roll sd | rate sd p / r (deg/s) | band PSD p / r | z - z_des | motor at 4000 (%) | max motor spread | V mean | stab CPU % mean / max |
|---|---|---|---|---|---|---|---|---|---|---|
| F1 MRAC | 76 | 1.99 | 1.83 | 9.1 / 8.4 | 20.5 / 11.6 | +0.019 | 0.9 | 1150 | 14.84 | 8.9 / 9.2 |

## Uncertainty estimate: does u_ad match the disturbance?

Plant per axis: xdot = b (u_inj + Delta), u_inj = u_nom + (injected) u_ad. b and the delay tau are fitted on 2-8 Hz content of all airborne segments (above the load band; closed-loop fit, so b is biased: the cancel fraction is also given for b x0.7 and x1.4). Delta_hat = LPF3Hz(xdot)/b - LPF3Hz(u_inj(t - tau)) is everything the nominal model does not explain (load torque and swing, CG offset, motor mismatch, drag), in control units. A perfect adaptive layer gives u_ad = -Delta_hat.

- static: mean(-Delta_hat) is the trim the drone needs; u_ad share = mean(u_ad) / mean(-Delta_hat) (the PID integrator carries the rest)
- dynamic (means removed): cancel = 1 - var(u_ad + Delta_hat) / var(Delta_hat); 1 = cancels all, 0 = no help, < 0 = adds disturbance. Band gain / phase of u_ad against -Delta_hat in 0.25-0.90 Hz (ideal 1 / 0 deg).
- ideal learner: cancel of -LPF_w(Delta_hat), what a perfect estimator behind the firmware u_ad filter at w rad/s could do. PID segments: u_ad is the shadow (computed, not injected).

| axis | b | tau (ms) | r (2-8 Hz) |
|---|---|---|---|
| pitch | 49.9 | 40 | 0.73 |
| roll | 49.0 | 40 | 0.69 |
| yaw | 64.9 | 30 | 0.70 |
| z_rate | 29.8 | 40 | 0.54 |

| seg | axis | -Delta static | u_ad mean | u_nom mean | u_ad share | -Delta dyn RMS | u_ad dyn RMS | band gain / phase / coh | cancel b x0.7 / x1 / x1.4 | ideal w 0.5 / 1 / 2 / 4 / 8 / 16 |
|---|---|---|---|---|---|---|---|---|---|---|
| F1 MRAC | pitch | -0.0914 | -0.0548 | -0.0367 | 0.60 | 0.0278 | 0.0111 | 0.12 / -94 / 0.44 | +0.04 / +0.05 / +0.07 | +0.09 / +0.19 / +0.32 / +0.51 / +0.74 / +0.91 |
| F1 MRAC | roll | +0.0273 | +0.0136 | +0.0137 | 0.50 | 0.0218 | 0.0096 | 0.16 / -86 / 0.50 | +0.11 / +0.15 / +0.17 | -0.05 / +0.16 / +0.33 / +0.52 / +0.73 / +0.90 |
| F1 MRAC | yaw | +0.0407 | +0.0249 | +0.0157 | 0.61 | 0.0399 | 0.0149 | 0.07 / -118 / 0.67 | +0.09 / +0.08 / +0.08 | +0.06 / +0.23 / +0.40 / +0.61 / +0.83 / +0.95 |
| F1 MRAC | z_rate | +1.4791 | +0.5164 | +0.9621 | 0.35 | 0.3134 | 0.1307 | 0.24 / +17 / 0.47 | +0.26 / +0.27 / +0.27 | +0.23 / +0.29 / +0.38 / +0.52 / +0.72 / +0.90 |

## Load swing frequency

Pendulum: L = rope 33 cm + half bottle 10 cm = 0.43 m. Simple sqrt(g/L)/2pi = 0.76 Hz; drone free to move, sqrt(g (M+m) / (M L))/2pi = 0.95 Hz (M 988 g, m 570 g). Measured: peak of the body-rate PSD in 0.15-1.5 Hz.

| seg | pitch peak (Hz) | roll peak (Hz) |
|---|---|---|
| F1 MRAC | 0.88 | 0.49 |

## Outside-force feature: body accel x/y vs the disturbance

Body-frame accel x/y sees only non-thrust forces (rope pull, slosh, wind, walls): thrust is along body z. If the torque is lever arm x force, -Delta_hat = w * a_xy with ONE constant w for any load. Per segment, means removed, both LPF 3 Hz. r = correlation at lag 0; cancel = r^2 = share of the dynamic disturbance a constant weight removes; w in control units per g. Lead = lag (+-300 ms) with the best |r|, positive = accel leads the disturbance. S6 = best single feature of x, x tanh x, cross, xm (u_nom left out: -Delta_hat contains u_nom by construction), then those four together, then together + accel x/y (offline least squares, constant weights). The S6 columns are an UPPER BOUND, partly circular: x and xm together rebuild the PID P term that sits inside -Delta_hat. Accel is an independent sensor, so its columns are clean. Caveat: an IMU off the centre of gravity adds (angular accel x offset), a few mg here.

| seg | axis | acc X r / cancel / w | acc Y r / cancel / w | lead X / Y (ms) | best S6 one | S6 four | S6 four + acc |
|---|---|---|---|---|---|---|---|
| F1 MRAC | pitch | -0.39 / 0.15 / -0.138 | -0.07 / 0.00 / -0.028 | +90 (r -0.45) / +170 (r -0.16) | 0.03 | 0.45 | 0.48 |
| F1 MRAC | roll | -0.25 / 0.06 / -0.068 | -0.11 / 0.01 / -0.037 | +60 (r -0.27) / +110 (r -0.21) | 0.02 | 0.31 | 0.32 |

## Cross-coupling: does the other axis help? (vp16 48-bump split)

-Delta_hat (as above, LPF 3 Hz) fitted with constant weights on Gaussian grids, scored on held-out data (interleaved 5 s blocks: fit even, score odd, and back). Inputs in firmware units: rate / 200 deg/s, tilt / 15 deg. own24 = RBF24T (6 rate x 4 tilt, mrac_t_*_c); own48 = 8 rate x 6 tilt; own32 = 8 rate x 4 tilt; cross16 = own tilt x OTHER tilt (4 x 4); lin = other tilt + other rate as two plain features. Fair split test at 48 bumps: own48 vs own32 + cross16. Swing = same fit on 0.25-0.90 Hz band-passed signals. Upper bound for the own grids (they partly rebuild the PID term inside -Delta_hat), so a cross gain here is the conservative signal. Grid centres of own48/own32 are a test choice, not firmware.

| seg | axis | own24 in / held-out | own24 + lin | own48 | own32 + cross16 | swing own48 / own32 + cross16 |
|---|---|---|---|---|---|---|
| F1 MRAC | pitch | 0.21 / 0.08 | 0.08 | 0.08 | -0.20 | -0.16 / -0.06 |
| F1 MRAC | roll | 0.16 / -0.02 | -0.04 | -1.86 | 0.02 | -0.20 / -0.33 |

## vp16 grid inputs: where the drone sits (centre placement, gamma T rule)

Each vp16 grid input in its normalised unit, p1 / p50 / p99 per segment. pitch/roll: rate / 200 deg/s, tilt / 15 deg, cross = other tilt / 15 deg. yaw: rate / 160 deg/s, heading error ~ gyrozPID.Des / 162 (= Des / Kp 6 / 27 deg, yawPID not logged), cross = Z thrust share. Z: climb rate / 1.0 m/s, thrust share u_nom / u_max, cross = tilt size / 15 deg. x/y: velocity / 300 cm/s, position error / 375 cm, cross = other velocity / 300. |e| = MRAC error (x/y: locxs/locys Des - FB, cm/s) against e_sat; > e_sat = share of time the tanh cap saturates.

| seg | axis | in1 p1 / p50 / p99 | in2 p1 / p50 / p99 | cross p1 / p50 / p99 | abs e p50 / p95 / p99 | e_sat | > e_sat (%) |
|---|---|---|---|---|---|---|---|
| F1 MRAC | pitch | -0.11 / -0.00 / +0.11 | -0.44 / -0.08 / +0.27 | -0.52 / -0.07 / +0.26 | 0.072 / 0.200 / 0.259 | 0.5 | 0.0 |
| F1 MRAC | roll | -0.10 / -0.00 / +0.10 | -0.52 / -0.07 / +0.26 | -0.44 / -0.08 / +0.27 | 0.073 / 0.219 / 0.285 | 0.5 | 0.0 |
| F1 MRAC | yaw | -0.09 / +0.00 / +0.10 | -0.12 / -0.00 / +0.16 | -0.03 / +0.07 / +0.17 | 0.071 / 0.210 / 0.288 | 0.7 | 0.0 |
| F1 MRAC | z_rate | -1.59 / +0.00 / +0.73 | -0.03 / +0.07 / +0.17 | +0.02 / +0.15 / +0.64 | 0.042 / 0.545 / 3.036 | 0.4 | 6.8 |
| F1 MRAC | x | -0.08 / +0.00 / +0.19 | -0.16 / -0.00 / +0.03 | -0.20 / -0.00 / +0.14 | 6.346 / 27.741 / 50.492 | 45 | 1.3 |
| F1 MRAC | y | -0.20 / -0.00 / +0.14 | -0.07 / -0.01 / +0.05 | -0.08 / +0.00 / +0.19 | 11.620 / 34.202 / 53.919 | 45 | 1.9 |

## Feature capability (all MRAC segments pooled)

Theta_i moves at gamma_i * s * phi_i / (1 + |phi|^2) (API/mrac.c:748, denom :932), so a feature's learning drive is gamma_i * E[phi_i^2 / (1 + |phi|^2)] (speed, shown relative to the bias). Its reach is lim_i * RMS(phi_i): the largest torque it can make with Theta_i at its projection bound. gamma_i, lim_i from the MRAC_BASIS rows (pitch/roll/yaw/z), mrac_g_phi and the preset g assumed 1.

| axis | feature | RMS phi | gamma | drive E[phi^2/(1+|phi|^2)] | speed vs bias | lim | reach lim*RMS phi | actual RMS Theta*phi | Theta0 63% time (s) per seg |
|---|---|---|---|---|---|---|---|---|---|
| pitch | bias 1 | 1 | 1.50 | 0.488 | 1 | 0.150 | 0.15 | 0.0558 | F1 1 |
| pitch | x | 0.158 | 0.20 | 0.0116 | 0.0032 | 0.050 | 0.0079 | 0.00182 |  |
| pitch | x tanh x | 0.0457 | 0.05 | 0.000894 | 6.1e-05 | 0.020 | 0.000914 | 1.3e-05 |  |
| pitch | cross | 0.0183 | 0.05 | 0.00016 | 1.1e-05 | 0.050 | 0.000913 | 2.47e-06 |  |
| pitch | u_nom | 0.0783 | 0.10 | 0.00297 | 0.00041 | 0.200 | 0.0157 | 0.000257 |  |
| pitch | xm | 0.131 | 0.10 | 0.00794 | 0.0011 | 0.150 | 0.0196 | 0.000296 |  |
| roll | bias 1 | 1 | 1.50 | 0.491 | 1 | 0.150 | 0.15 | 0.0166 | F1 2 |
| roll | x | 0.147 | 0.20 | 0.0101 | 0.0027 | 0.050 | 0.00733 | 0.00199 |  |
| roll | x tanh x | 0.0388 | 0.05 | 0.000658 | 4.5e-05 | 0.020 | 0.000775 | 1.48e-05 |  |
| roll | cross | 0.0189 | 0.05 | 0.000172 | 1.2e-05 | 0.050 | 0.000945 | 2.87e-06 |  |
| roll | u_nom | 0.0728 | 0.10 | 0.00258 | 0.00035 | 0.200 | 0.0146 | 0.000253 |  |
| roll | xm | 0.102 | 0.10 | 0.00487 | 0.00066 | 0.150 | 0.0153 | 0.000185 |  |
| yaw | bias 1 | 1 | 1.00 | 0.494 | 1 | 0.090 | 0.09 | 0.029 | F1 1 |
| yaw | x | 0.117 | 0.10 | 0.0066 | 0.0013 | 0.030 | 0.00351 | 1.87e-05 |  |
| yaw | x tanh x | 0.0248 | 0.05 | 0.000288 | 2.9e-05 | 0.012 | 0.000298 | 4.58e-06 |  |
| yaw | cross | 0 | 0.05 | 0 | 0 | 0.030 | 0 | 0 |  |
| yaw | u_nom | 0.0486 | 0.10 | 0.00116 | 0.00023 | 0.120 | 0.00584 | 9.54e-05 |  |
| yaw | xm | 0.0926 | 0.10 | 0.00417 | 0.00084 | 0.090 | 0.00833 | 0.000202 |  |
| z_rate | bias 1 | 1 | 2.00 | 0.332 | 1 | 1.000 | 1 | 0.442 | F1 2 |
| z_rate | x | 0.618 | 0.50 | 0.0127 | 0.0096 | 0.100 | 0.0618 | 0.000803 |  |
| z_rate | x tanh x | 0.602 | 0.10 | 0.00774 | 0.0012 | 0.050 | 0.0301 | 0.000866 |  |
| z_rate | cross | 0 | 0.10 | 0 | 0 | 0.050 | 0 | 0 |  |
| z_rate | u_nom | 1.02 | 0.20 | 0.309 | 0.093 | 0.200 | 0.203 | 0.108 |  |
| z_rate | xm | 0.165 | 0.20 | 0.00728 | 0.0022 | 0.200 | 0.033 | 0.00283 |  |

