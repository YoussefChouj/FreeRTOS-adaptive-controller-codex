# Adaptive-layer stats: `pid_ref_ARM_LOAD_293g_2`

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
| F1 PID | -0.1071 / -0.0582 | -0.2214 / -0.1044 | +0.0000 / +0.0000 | +0.16 | +0.20 | +0.8 | -6.3 | +1.8 / -6.2 | -1.26 | -0.25 | 13 | -29 | -0.58 |
| F2 PID | -0.1154 / -0.0616 | -0.2378 / -0.1375 | +0.0000 / +0.0000 | +0.07 | -0.02 | +3.2 | -2.0 | +4.0 / -5.0 | +0.47 | +2.74 | 0 | +nan | +0.79 |

## Per segment: attitude, height, motors, battery

| seg | s | pitch sd | roll sd | rate sd p / r (deg/s) | band PSD p / r | z - z_des | motor at 4000 (%) | max motor spread | V mean | stab CPU % mean / max |
|---|---|---|---|---|---|---|---|---|---|---|
| F1 PID | 34 | 3.22 | 3.77 | 13.8 / 14.8 | 43.0 / 71.9 | -0.004 | 0.0 | 1228 | 14.68 | 9.9 / 10.2 |
| F2 PID | 12 | 2.03 | 1.67 | 10.0 / 9.4 | 20.3 / 10.5 | -0.016 | 0.3 | 1154 | 14.50 | 9.9 / 10.1 |

## Uncertainty estimate: does u_ad match the disturbance?

Plant per axis: xdot = b (u_inj + Delta), u_inj = u_nom + (injected) u_ad. b and the delay tau are fitted on 2-8 Hz content of all airborne segments (above the load band; closed-loop fit, so b is biased: the cancel fraction is also given for b x0.7 and x1.4). Delta_hat = LPF3Hz(xdot)/b - LPF3Hz(u_inj(t - tau)) is everything the nominal model does not explain (load torque and swing, CG offset, motor mismatch, drag), in control units. A perfect adaptive layer gives u_ad = -Delta_hat.

- static: mean(-Delta_hat) is the trim the drone needs; u_ad share = mean(u_ad) / mean(-Delta_hat) (the PID integrator carries the rest)
- dynamic (means removed): cancel = 1 - var(u_ad + Delta_hat) / var(Delta_hat); 1 = cancels all, 0 = no help, < 0 = adds disturbance. Band gain / phase of u_ad against -Delta_hat in 0.25-0.90 Hz (ideal 1 / 0 deg).
- ideal learner: cancel of -LPF_w(Delta_hat), what a perfect estimator behind the firmware u_ad filter at w rad/s could do. PID segments: u_ad is the shadow (computed, not injected).

| axis | b | tau (ms) | r (2-8 Hz) |
|---|---|---|---|
| pitch | 45.8 | 50 | 0.70 |
| roll | 55.7 | 50 | 0.71 |
| yaw | 44.4 | 20 | 0.66 |
| z_rate | 14.1 | 40 | 0.72 |

| seg | axis | -Delta static | u_ad mean | u_nom mean | u_ad share | -Delta dyn RMS | u_ad dyn RMS | band gain / phase / coh | cancel b x0.7 / x1 / x1.4 | ideal w 0.5 / 1 / 2 / 4 / 8 / 16 |
|---|---|---|---|---|---|---|---|---|---|---|
| F1 PID | pitch | -0.1074 | -0.2214 | -0.1071 | 2.06 | 0.0292 | 0.0528 | 0.13 / +36 / 0.04 | -1.94 / -3.13 / -3.89 | -0.04 / +0.06 / +0.18 / +0.37 / +0.62 / +0.85 |
| F2 PID | pitch | -0.1145 | -0.2378 | -0.1154 | 2.08 | 0.0274 | 0.0140 | 0.45 / -179 / 0.44 | -0.44 / -0.60 / -0.69 | -1.56 / -0.82 / -0.26 / +0.16 / +0.53 / +0.83 |
| F1 PID | roll | -0.0586 | -0.1044 | -0.0582 | 1.78 | 0.0286 | 0.0325 | 0.27 / -114 / 0.22 | -0.77 / -1.07 / -1.20 | -0.20 / +0.00 / +0.20 / +0.42 / +0.66 / +0.86 |
| F2 PID | roll | -0.0608 | -0.1375 | -0.0616 | 2.26 | 0.0209 | 0.0095 | 0.31 / -123 / 0.39 | -0.32 / -0.34 / -0.26 | -3.65 / -2.14 / -0.93 / -0.12 / +0.42 / +0.78 |
| F1 PID | yaw | +0.0106 | +0.0141 | +0.0104 | 1.33 | 0.0346 | 0.0129 | 0.06 / -123 / 0.53 | -0.15 / -0.11 / -0.08 | +0.11 / +0.20 / +0.33 / +0.53 / +0.77 / +0.93 |
| F2 PID | yaw | -0.0087 | +0.0391 | -0.0084 | -4.50 | 0.0391 | 0.0340 | 0.11 / -116 / 0.59 | -0.01 / +0.01 / +0.03 | +0.18 / +0.27 / +0.37 / +0.55 / +0.78 / +0.93 |
| F1 PID | z_rate | +0.8997 | +0.8312 | +0.8992 | 0.92 | 0.2258 | 0.2476 | 0.07 / +23 / 0.12 | -0.17 / -0.13 / -0.11 | +0.30 / +0.35 / +0.44 / +0.60 / +0.79 / +0.93 |
| F2 PID | z_rate | +1.0148 | +0.8366 | +1.0191 | 0.82 | 0.2264 | 0.1029 | 0.47 / +67 / 0.79 | -0.01 / -0.05 / -0.08 | +0.00 / +0.12 / +0.26 / +0.45 / +0.67 / +0.86 |

## Load swing frequency

Pendulum: L = rope 33 cm + half bottle 10 cm = 0.43 m. Simple sqrt(g/L)/2pi = 0.76 Hz; drone free to move, sqrt(g (M+m) / (M L))/2pi = 0.95 Hz (M 988 g, m 570 g). Measured: peak of the body-rate PSD in 0.15-1.5 Hz.

| seg | pitch peak (Hz) | roll peak (Hz) |
|---|---|---|
| F1 PID | 0.98 | 0.49 |
| F2 PID | 1.03 | 1.03 |

## Outside-force feature: body accel x/y vs the disturbance

Body-frame accel x/y sees only non-thrust forces (rope pull, slosh, wind, walls): thrust is along body z. If the torque is lever arm x force, -Delta_hat = w * a_xy with ONE constant w for any load. Per segment, means removed, both LPF 3 Hz. r = correlation at lag 0; cancel = r^2 = share of the dynamic disturbance a constant weight removes; w in control units per g. Lead = lag (+-300 ms) with the best |r|, positive = accel leads the disturbance. S6 = best single feature of x, x tanh x, cross, xm (u_nom left out: -Delta_hat contains u_nom by construction), then those four together, then together + accel x/y (offline least squares, constant weights). The S6 columns are an UPPER BOUND, partly circular: x and xm together rebuild the PID P term that sits inside -Delta_hat. Accel is an independent sensor, so its columns are clean. Caveat: an IMU off the centre of gravity adds (angular accel x offset), a few mg here.

| seg | axis | acc X r / cancel / w | acc Y r / cancel / w | lead X / Y (ms) | best S6 one | S6 four | S6 four + acc |
|---|---|---|---|---|---|---|---|
| F1 PID | pitch | +0.02 / 0.00 / +0.010 | -0.15 / 0.02 / -0.063 | -300 (r +0.22) / +180 (r -0.38) | 0.26 | 0.33 | 0.42 |
| F2 PID | pitch | +0.02 / 0.00 / +0.007 | -0.18 / 0.03 / -0.062 | +180 (r +0.07) / +0 (r -0.18) | 0.20 | 0.33 | 0.44 |
| F1 PID | roll | +0.01 / 0.00 / +0.004 | -0.02 / 0.00 / -0.010 | +190 (r -0.16) / +190 (r -0.29) | 0.30 | 0.39 | 0.45 |
| F2 PID | roll | -0.08 / 0.01 / -0.018 | -0.26 / 0.07 / -0.069 | +280 (r +0.23) / +0 (r -0.26) | 0.29 | 0.34 | 0.50 |

## Cross-coupling: does the other axis help? (vp16 48-bump split)

-Delta_hat (as above, LPF 3 Hz) fitted with constant weights on Gaussian grids, scored on held-out data (interleaved 5 s blocks: fit even, score odd, and back). Inputs in firmware units: rate / 200 deg/s, tilt / 15 deg. own24 = RBF24T (6 rate x 4 tilt, mrac_t_*_c); own48 = 8 rate x 6 tilt; own32 = 8 rate x 4 tilt; cross16 = own tilt x OTHER tilt (4 x 4); lin = other tilt + other rate as two plain features. Fair split test at 48 bumps: own48 vs own32 + cross16. Swing = same fit on 0.25-0.90 Hz band-passed signals. Upper bound for the own grids (they partly rebuild the PID term inside -Delta_hat), so a cross gain here is the conservative signal. Grid centres of own48/own32 are a test choice, not firmware.

| seg | axis | own24 in / held-out | own24 + lin | own48 | own32 + cross16 | swing own48 / own32 + cross16 |
|---|---|---|---|---|---|---|
| F1 PID | pitch | 0.57 / 0.50 | 0.52 | 0.40 | 0.44 | 0.60 / 0.39 |
| F2 PID | pitch | 0.65 / 0.34 | 0.40 | 0.17 | 0.43 | -0.68 / -0.44 |
| F1 PID | roll | 0.48 / 0.32 | 0.33 | 0.28 | 0.32 | 0.32 / 0.51 |
| F2 PID | roll | 0.63 / 0.20 | 0.12 | 0.18 | -0.17 | -0.96 / -2.51 |

