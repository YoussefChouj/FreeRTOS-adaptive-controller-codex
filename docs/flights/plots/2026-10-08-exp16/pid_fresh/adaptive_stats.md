# Adaptive-layer stats: `pid_ref`

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
| F1 PID | -0.1118 / +0.0577 | +0.0000 / +0.0000 | +0.0000 / +0.0000 | +0.15 | -0.06 | -4.1 | -11.3 | -3.1 / -11.4 | +3.88 | +1.99 | 15 | +65 | -0.83 |

## Per segment: attitude, height, motors, battery

| seg | s | pitch sd | roll sd | rate sd p / r (deg/s) | band PSD p / r | z - z_des | motor at 4000 (%) | max motor spread | V mean | stab CPU % mean / max |
|---|---|---|---|---|---|---|---|---|---|---|
| F1 PID | 65 | 3.07 | 3.70 | 10.5 / 11.6 | 31.2 / 63.4 | -0.079 | 2.4 | 1260 | 14.89 | 8.7 / 9.0 |

## Uncertainty estimate: does u_ad match the disturbance?

Plant per axis: xdot = b (u_inj + Delta), u_inj = u_nom + (injected) u_ad. b and the delay tau are fitted on 2-8 Hz content of all airborne segments (above the load band; closed-loop fit, so b is biased: the cancel fraction is also given for b x0.7 and x1.4). Delta_hat = LPF3Hz(xdot)/b - LPF3Hz(u_inj(t - tau)) is everything the nominal model does not explain (load torque and swing, CG offset, motor mismatch, drag), in control units. A perfect adaptive layer gives u_ad = -Delta_hat.

- static: mean(-Delta_hat) is the trim the drone needs; u_ad share = mean(u_ad) / mean(-Delta_hat) (the PID integrator carries the rest)
- dynamic (means removed): cancel = 1 - var(u_ad + Delta_hat) / var(Delta_hat); 1 = cancels all, 0 = no help, < 0 = adds disturbance. Band gain / phase of u_ad against -Delta_hat in 0.25-0.90 Hz (ideal 1 / 0 deg).
- ideal learner: cancel of -LPF_w(Delta_hat), what a perfect estimator behind the firmware u_ad filter at w rad/s could do. PID segments: u_ad is the shadow (computed, not injected).

| axis | b | tau (ms) | r (2-8 Hz) |
|---|---|---|---|
| pitch | 46.6 | 40 | 0.77 |
| roll | 43.4 | 40 | 0.68 |
| yaw | 93.9 | 40 | 0.81 |
| z_rate | 41.2 | 40 | 0.55 |

| seg | axis | -Delta static | u_ad mean | u_nom mean | u_ad share | -Delta dyn RMS | u_ad dyn RMS | band gain / phase / coh | cancel b x0.7 / x1 / x1.4 | ideal w 0.5 / 1 / 2 / 4 / 8 / 16 |
|---|---|---|---|---|---|---|---|---|---|---|
| F1 PID | pitch | -0.1119 | 0 | -0.1118 | - | 0.0335 | 0 | - | - | -0.09 / +0.14 / +0.34 / +0.54 / +0.75 / +0.91 |
| F1 PID | roll | +0.0582 | 0 | +0.0577 | - | 0.0297 | 0 | - | - | -1.10 / -0.39 / +0.05 / +0.38 / +0.65 / +0.86 |
| F1 PID | yaw | +0.0278 | 0 | +0.0278 | - | 0.0384 | 0 | - | - | +0.04 / +0.19 / +0.36 / +0.59 / +0.83 / +0.95 |
| F1 PID | z_rate | +1.6184 | 0 | +1.6179 | - | 0.3748 | 0 | - | - | +0.23 / +0.31 / +0.41 / +0.55 / +0.74 / +0.90 |

## Load swing frequency

Pendulum: L = rope 33 cm + half bottle 10 cm = 0.43 m. Simple sqrt(g/L)/2pi = 0.76 Hz; drone free to move, sqrt(g (M+m) / (M L))/2pi = 0.95 Hz (M 988 g, m 570 g). Measured: peak of the body-rate PSD in 0.15-1.5 Hz.

| seg | pitch peak (Hz) | roll peak (Hz) |
|---|---|---|
| F1 PID | 0.39 | 0.49 |

## Outside-force feature: body accel x/y vs the disturbance

Body-frame accel x/y sees only non-thrust forces (rope pull, slosh, wind, walls): thrust is along body z. If the torque is lever arm x force, -Delta_hat = w * a_xy with ONE constant w for any load. Per segment, means removed, both LPF 3 Hz. r = correlation at lag 0; cancel = r^2 = share of the dynamic disturbance a constant weight removes; w in control units per g. Lead = lag (+-300 ms) with the best |r|, positive = accel leads the disturbance. S6 = best single feature of x, x tanh x, cross, xm (u_nom left out: -Delta_hat contains u_nom by construction), then those four together, then together + accel x/y (offline least squares, constant weights). The S6 columns are an UPPER BOUND, partly circular: x and xm together rebuild the PID P term that sits inside -Delta_hat. Accel is an independent sensor, so its columns are clean. Caveat: an IMU off the centre of gravity adds (angular accel x offset), a few mg here.

| seg | axis | acc X r / cancel / w | acc Y r / cancel / w | lead X / Y (ms) | best S6 one | S6 four | S6 four + acc |
|---|---|---|---|---|---|---|---|
| F1 PID | pitch | -0.27 / 0.08 / -0.102 | +0.05 / 0.00 / +0.022 | +60 (r -0.30) / +300 (r +0.09) | 0.07 | 0.38 | 0.40 |
| F1 PID | roll | -0.04 / 0.00 / -0.014 | -0.09 / 0.01 / -0.034 | +130 (r -0.18) / -300 (r +0.28) | 0.06 | 0.26 | 0.26 |

## Cross-coupling: does the other axis help? (vp16 48-bump split)

-Delta_hat (as above, LPF 3 Hz) fitted with constant weights on Gaussian grids, scored on held-out data (interleaved 5 s blocks: fit even, score odd, and back). Inputs in firmware units: rate / 200 deg/s, tilt / 15 deg. own24 = RBF24T (6 rate x 4 tilt, mrac_t_*_c); own48 = 8 rate x 6 tilt; own32 = 8 rate x 4 tilt; cross16 = own tilt x OTHER tilt (4 x 4); lin = other tilt + other rate as two plain features. Fair split test at 48 bumps: own48 vs own32 + cross16. Swing = same fit on 0.25-0.90 Hz band-passed signals. Upper bound for the own grids (they partly rebuild the PID term inside -Delta_hat), so a cross gain here is the conservative signal. Grid centres of own48/own32 are a test choice, not firmware.

| seg | axis | own24 in / held-out | own24 + lin | own48 | own32 + cross16 | swing own48 / own32 + cross16 |
|---|---|---|---|---|---|---|
| F1 PID | pitch | 0.24 / 0.15 | 0.12 | 0.08 | 0.07 | 0.03 / 0.03 |
| F1 PID | roll | 0.22 / -0.02 | -0.04 | -0.07 | -0.13 | -0.29 / -0.41 |

