# Adaptive-layer stats: `pid_ref_2_circle`

Measured from the log. Basis rebuilt from logged signals (see the tool docstring). Shaded: blue PID, orange MRAC injected.

## Per MRAC segment and axis

| seg | axis | rebuild r | u_ad RMS | u_nom RMS | ratio | r(u_ad,u_nom) | r(u_ad,x) | band phase u_ad/x (deg) | band phase u_nom/x | Re H u_ad / Re H u_nom | coh | top feature (RMS share) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|

Phase of a torque against the body rate x in 0.25-0.90 Hz: +/-180 = pure damping, 0 = anti-damping, +/-90 = no energy. Re H u_ad / Re H u_nom > 0: u_ad adds damping like the PID; < 0: removes it.

## What-if: same weights, other omega_u (open-loop replay of the logged raw sum)

Band phase u_ad/x (deg) / Re H ratio, as above. Closed loop would differ (the weights would evolve differently); this isolates the filter lag.

| seg | axis | flown | 10 rad/s | 15 rad/s | 20 rad/s |
|---|---|---|---|---|---|

## Weights Theta (start -> end of each MRAC segment)

| seg | axis | Theta[0] bias 1 | Theta[1] x | Theta[2] x tanh x | Theta[3] cross | Theta[4] u_nom | Theta[5] xm | |Theta| growth |
|---|---|---|---|---|---|---|---|---|

## Ext block (vp basis 7, 24 Gaussians per axis, pitch/roll)

Grid inputs in the firmware's normalised units (rate, angle); the Gaussian centres span -1..1 (basis 5: -1.5..1.5 rate, -1..1 angle). Activity = sd / mean of the most varying Gaussian over the segment: near 0 the grid acts as one constant (a bias), not as a function of the state. Ext share = ext RMS^2 / (ext RMS^2 + S6-part RMS^2). Slope = d|Theta_ext|/dt over the last third (> 0: still learning).

| seg | axis | rate p1 / p50 / p99 | angle p1 / p50 / p99 | activity | ext RMS | S6 part RMS | ext share | |Theta_ext| start -> end | slope (/s) | top ext (RMS share) |
|---|---|---|---|---|---|---|---|---|---|---|

### Ext ranking (every Gaussian, RMS^2 share of the ext block, cumulative in brackets)

| seg | axis | ranked |
|---|---|---|

## Static offsets per segment (drift)

Expected static torque of the load at the rope point: pitch 0.112 N m (x 2 cm), roll 0.391 N m (y 7 cm), mg = 5.59 N.

| seg | mean u_nom p / r | mean u_ad p / r | mean Theta[0] p / r | pitch Des - FB (deg) | roll Des - FB | locx Des-FB (cm) | locy Des-FB | locx/y PID U mean | x FB slope (cm/s) | y FB slope | x stick on (%) | x stick vel Des median (cm/s) | x vel FB mean, no stick (cm/s) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| F1 PID | -0.0761 / +0.0308 | -0.1799 / +0.0607 | +0.0000 / +0.0000 | -0.08 | +0.27 | +1.0 | -3.7 | +2.2 / -3.4 | -0.68 | -0.82 | 11 | +36 | -1.09 |
| F2 PID | -0.0818 / +0.0212 | -0.1871 / +0.0267 | +0.0000 / +0.0000 | +0.04 | +0.49 | -7.3 | +0.8 | -3.9 / +0.2 | +3.83 | -0.91 | 21 | +60 | -5.41 |

## Per segment: attitude, height, motors, battery

| seg | s | pitch sd | roll sd | rate sd p / r (deg/s) | band PSD p / r | z - z_des | motor at 4000 (%) | max motor spread | V mean | stab CPU % mean / max |
|---|---|---|---|---|---|---|---|---|---|---|
| F1 PID | 58 | 1.98 | 2.63 | 8.5 / 11.6 | 4.2 / 28.7 | -0.013 | 0.2 | 1271 | 15.57 | 9.8 / 10.2 |
| F2 PID | 12 | 3.64 | 4.79 | 15.2 / 17.4 | 15.0 / 138.4 | -0.006 | 0.6 | 1035 | 15.32 | 9.9 / 10.1 |

## Uncertainty estimate: does u_ad match the disturbance?

Plant per axis: xdot = b (u_inj + Delta), u_inj = u_nom + (injected) u_ad. b and the delay tau are fitted on 2-8 Hz content of all airborne segments (above the load band; closed-loop fit, so b is biased: the cancel fraction is also given for b x0.7 and x1.4). Delta_hat = LPF3Hz(xdot)/b - LPF3Hz(u_inj(t - tau)) is everything the nominal model does not explain (load torque and swing, CG offset, motor mismatch, drag), in control units. A perfect adaptive layer gives u_ad = -Delta_hat.

- static: mean(-Delta_hat) is the trim the drone needs; u_ad share = mean(u_ad) / mean(-Delta_hat) (the PID integrator carries the rest)
- dynamic (means removed): cancel = 1 - var(u_ad + Delta_hat) / var(Delta_hat); 1 = cancels all, 0 = no help, < 0 = adds disturbance. Band gain / phase of u_ad against -Delta_hat in 0.25-0.90 Hz (ideal 1 / 0 deg).
- ideal learner: cancel of -LPF_w(Delta_hat), what a perfect estimator behind the firmware u_ad filter at w rad/s could do. PID segments: u_ad is the shadow (computed, not injected).

| axis | b | tau (ms) | r (2-8 Hz) |
|---|---|---|---|
| pitch | 47.6 | 40 | 0.72 |
| roll | 57.0 | 40 | 0.73 |
| yaw | 83.6 | 30 | 0.68 |
| z_rate | 32.5 | 40 | 0.56 |

| seg | axis | -Delta static | u_ad mean | u_nom mean | u_ad share | -Delta dyn RMS | u_ad dyn RMS | band gain / phase / coh | cancel b x0.7 / x1 / x1.4 | ideal w 0.5 / 1 / 2 / 4 / 8 / 16 |
|---|---|---|---|---|---|---|---|---|---|---|
| F1 PID | pitch | -0.0759 | -0.1799 | -0.0761 | 2.37 | 0.0270 | 0.0358 | 0.06 / +172 / 0.14 | -1.02 / -1.52 / -1.92 | -2.13 / -1.02 / -0.35 / +0.12 / +0.54 / +0.83 |
| F2 PID | pitch | -0.0800 | -0.1871 | -0.0818 | 2.34 | 0.0455 | 0.0244 | 0.64 / +176 / 0.20 | -0.16 / -0.25 / -0.34 | -1.50 / -0.77 / -0.24 / +0.14 / +0.49 / +0.80 |
| F1 PID | roll | +0.0307 | +0.0607 | +0.0308 | 1.97 | 0.0282 | 0.0180 | 0.06 / -130 / 0.12 | -0.20 / -0.30 / -0.39 | -0.26 / -0.07 / +0.10 / +0.31 / +0.60 / +0.85 |
| F2 PID | roll | +0.0211 | +0.0267 | +0.0212 | 1.27 | 0.0348 | 0.0118 | 0.24 / -96 / 0.62 | -0.18 / -0.25 / -0.31 | -0.39 / -0.13 / +0.12 / +0.35 / +0.61 / +0.85 |
| F1 PID | yaw | +0.0142 | +0.0410 | +0.0141 | 2.89 | 0.0464 | 0.0241 | 0.06 / -117 / 0.70 | -0.26 / -0.23 / -0.21 | +0.06 / +0.13 / +0.26 / +0.48 / +0.76 / +0.93 |
| F2 PID | yaw | +0.0065 | +0.0552 | +0.0059 | 8.47 | 0.0462 | 0.0099 | 0.04 / -122 / 0.69 | -0.04 / -0.03 / -0.03 | +0.06 / +0.16 / +0.32 / +0.55 / +0.80 / +0.94 |
| F1 PID | z_rate | +1.2974 | +0.8455 | +1.2977 | 0.65 | 0.2964 | 0.2213 | 0.28 / -1 / 0.43 | +0.02 / -0.02 / -0.05 | +0.18 / +0.23 / +0.32 / +0.49 / +0.71 / +0.89 |
| F2 PID | z_rate | +1.5222 | +1.0139 | +1.5228 | 0.67 | 0.2343 | 0.2205 | 1.14 / +31 / 0.43 | +0.10 / -0.06 / -0.21 | +0.06 / +0.09 / +0.16 / +0.31 / +0.56 / +0.82 |

## Load swing frequency

Pendulum: L = rope 33 cm + half bottle 10 cm = 0.43 m. Simple sqrt(g/L)/2pi = 0.76 Hz; drone free to move, sqrt(g (M+m) / (M L))/2pi = 0.95 Hz (M 988 g, m 570 g). Measured: peak of the body-rate PSD in 0.15-1.5 Hz.

| seg | pitch peak (Hz) | roll peak (Hz) |
|---|---|---|
| F1 PID | 0.98 | 0.98 |
| F2 PID | 1.46 | 0.49 |

## Outside-force feature: body accel x/y vs the disturbance

Body-frame accel x/y sees only non-thrust forces (rope pull, slosh, wind, walls): thrust is along body z. If the torque is lever arm x force, -Delta_hat = w * a_xy with ONE constant w for any load. Per segment, means removed, both LPF 3 Hz. r = correlation at lag 0; cancel = r^2 = share of the dynamic disturbance a constant weight removes; w in control units per g. Lead = lag (+-300 ms) with the best |r|, positive = accel leads the disturbance. S6 = best single feature of x, x tanh x, cross, xm (u_nom left out: -Delta_hat contains u_nom by construction), then those four together, then together + accel x/y (offline least squares, constant weights). The S6 columns are an UPPER BOUND, partly circular: x and xm together rebuild the PID P term that sits inside -Delta_hat. Accel is an independent sensor, so its columns are clean. Caveat: an IMU off the centre of gravity adds (angular accel x offset), a few mg here.

| seg | axis | acc X r / cancel / w | acc Y r / cancel / w | lead X / Y (ms) | best S6 one | S6 four | S6 four + acc |
|---|---|---|---|---|---|---|---|
| F1 PID | pitch | -0.30 / 0.09 / -0.081 | -0.12 / 0.01 / -0.031 | +90 (r -0.43) / +150 (r -0.37) | 0.05 | 0.35 | 0.36 |
| F2 PID | pitch | -0.14 / 0.02 / -0.050 | -0.15 / 0.02 / -0.061 | +300 (r +0.22) / -170 (r +0.24) | 0.07 | 0.39 | 0.41 |
| F1 PID | roll | -0.34 / 0.12 / -0.096 | -0.26 / 0.07 / -0.072 | +30 (r -0.36) / +100 (r -0.39) | 0.03 | 0.42 | 0.44 |
| F2 PID | roll | -0.09 / 0.01 / -0.024 | -0.23 / 0.05 / -0.073 | +250 (r +0.26) / -300 (r +0.28) | 0.05 | 0.41 | 0.44 |

## Cross-coupling: does the other axis help? (vp16 48-bump split)

-Delta_hat (as above, LPF 3 Hz) fitted with constant weights on Gaussian grids, scored on held-out data (interleaved 5 s blocks: fit even, score odd, and back). Inputs in firmware units: rate / 200 deg/s, tilt / 15 deg. own24 = RBF24T (6 rate x 4 tilt, mrac_t_*_c); own48 = 8 rate x 6 tilt; own32 = 8 rate x 4 tilt; cross16 = own tilt x OTHER tilt (4 x 4); lin = other tilt + other rate as two plain features. Fair split test at 48 bumps: own48 vs own32 + cross16. Swing = same fit on 0.25-0.90 Hz band-passed signals. Upper bound for the own grids (they partly rebuild the PID term inside -Delta_hat), so a cross gain here is the conservative signal. Grid centres of own48/own32 are a test choice, not firmware.

| seg | axis | own24 in / held-out | own24 + lin | own48 | own32 + cross16 | swing own48 / own32 + cross16 |
|---|---|---|---|---|---|---|
| F1 PID | pitch | 0.28 / 0.11 | -0.14 | 0.10 | -0.72 | -6.01 / -5.67 |
| F2 PID | pitch | 0.70 / 0.03 | -2.18 | -0.85 | -1.94 | -6.90 / -7.61 |
| F1 PID | roll | 0.35 / -0.10 | -0.14 | -0.09 | -0.18 | -0.22 / -0.59 |
| F2 PID | roll | 0.40 / -0.70 | -0.30 | -1.44 | -2.42 | -4.53 / -1.69 |

## vp16 grid inputs: where the drone sits (centre placement, gamma T rule)

Each vp16 grid input in its normalised unit, p1 / p50 / p99 per segment. pitch/roll: rate / 200 deg/s, tilt / 15 deg, cross = other tilt / 15 deg. yaw: rate / 160 deg/s, heading error ~ gyrozPID.Des / 162 (= Des / Kp 6 / 27 deg, yawPID not logged), cross = Z thrust share. Z: climb rate / 1.0 m/s, thrust share u_nom / u_max, cross = tilt size / 15 deg. x/y: velocity / 300 cm/s, position error / 375 cm, cross = other velocity / 300. |e| = MRAC error (x/y: locxs/locys Des - FB, cm/s) against e_sat; > e_sat = share of time the tanh cap saturates.

| seg | axis | in1 p1 / p50 / p99 | in2 p1 / p50 / p99 | cross p1 / p50 / p99 | abs e p50 / p95 / p99 | e_sat | > e_sat (%) |
|---|---|---|---|---|---|---|---|
| F1 PID | pitch | -0.12 / -0.00 / +0.11 | -0.36 / -0.07 / +0.52 | -0.66 / -0.06 / +0.52 | 0.073 / 0.233 / 0.322 | 0.5 | 0.0 |
| F1 PID | roll | -0.18 / +0.00 / +0.18 | -0.66 / -0.06 / +0.52 | -0.36 / -0.07 / +0.52 | 0.063 / 0.200 / 0.279 | 0.5 | 0.0 |
| F1 PID | yaw | -0.15 / -0.00 / +0.15 | -0.19 / +0.00 / +0.15 | -0.00 / +0.10 / +0.17 | 0.075 / 0.245 / 0.369 | 0.7 | 0.0 |
| F1 PID | z_rate | -1.26 / +0.00 / +0.81 | -0.00 / +0.10 / +0.17 | +0.02 / +0.15 / +0.71 | 0.077 / 0.526 / 1.656 | 0.4 | 8.7 |
| F1 PID | x | -0.26 / +0.00 / +0.25 | -0.08 / -0.00 / +0.09 | -0.14 / -0.00 / +0.10 | 7.446 / 65.778 / 104.060 | 45 | 8.9 |
| F1 PID | y | -0.14 / -0.00 / +0.10 | -0.09 / -0.00 / +0.09 | -0.26 / +0.00 / +0.25 | 9.863 / 36.006 / 76.541 | 45 | 2.9 |
| F2 PID | pitch | -0.20 / -0.01 / +0.21 | -0.66 / -0.03 / +0.79 | -0.80 / -0.04 / +0.55 | 0.078 / 0.240 / 0.364 | 0.5 | 0.0 |
| F2 PID | roll | -0.23 / +0.00 / +0.19 | -0.80 / -0.04 / +0.55 | -0.66 / -0.03 / +0.79 | 0.081 / 0.242 / 0.313 | 0.5 | 0.0 |
| F2 PID | yaw | -0.11 / +0.00 / +0.12 | -0.16 / -0.01 / +0.12 | +0.00 / +0.11 / +0.17 | 0.089 / 0.252 / 0.298 | 0.7 | 0.0 |
| F2 PID | z_rate | -3.24 / -0.00 / +1.18 | +0.00 / +0.11 / +0.17 | +0.03 / +0.33 / +0.84 | 0.122 / 0.565 / 3.644 | 0.4 | 7.5 |
| F2 PID | x | -0.20 / -0.02 / +0.30 | -0.16 / -0.01 / +0.04 | -0.38 / -0.01 / +0.14 | 24.760 / 88.586 / 105.152 | 45 | 18.9 |
| F2 PID | y | -0.38 / -0.01 / +0.14 | -0.05 / -0.01 / +0.22 | -0.20 / -0.02 / +0.30 | 16.931 / 68.715 / 99.536 | 45 | 13.4 |

