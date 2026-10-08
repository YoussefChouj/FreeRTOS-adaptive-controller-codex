# Adaptive-layer stats: `pid_ref_2`

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
| F1 PID | -0.0733 / +0.0553 | +0.0000 / +0.0000 | +0.0000 / +0.0000 | +0.06 | +0.17 | +0.7 | -0.8 | +3.1 / -2.4 | +1.04 | -1.20 | 17 | +36 | +1.08 |

## Per segment: attitude, height, motors, battery

| seg | s | pitch sd | roll sd | rate sd p / r (deg/s) | band PSD p / r | z - z_des | motor at 4000 (%) | max motor spread | V mean | stab CPU % mean / max |
|---|---|---|---|---|---|---|---|---|---|---|
| F1 PID | 52 | 3.10 | 3.63 | 10.6 / 12.9 | 31.8 / 61.5 | -0.259 | 2.7 | 1329 | 14.73 | 8.7 / 8.9 |

## Uncertainty estimate: does u_ad match the disturbance?

Plant per axis: xdot = b (u_inj + Delta), u_inj = u_nom + (injected) u_ad. b and the delay tau are fitted on 2-8 Hz content of all airborne segments (above the load band; closed-loop fit, so b is biased: the cancel fraction is also given for b x0.7 and x1.4). Delta_hat = LPF3Hz(xdot)/b - LPF3Hz(u_inj(t - tau)) is everything the nominal model does not explain (load torque and swing, CG offset, motor mismatch, drag), in control units. A perfect adaptive layer gives u_ad = -Delta_hat.

- static: mean(-Delta_hat) is the trim the drone needs; u_ad share = mean(u_ad) / mean(-Delta_hat) (the PID integrator carries the rest)
- dynamic (means removed): cancel = 1 - var(u_ad + Delta_hat) / var(Delta_hat); 1 = cancels all, 0 = no help, < 0 = adds disturbance. Band gain / phase of u_ad against -Delta_hat in 0.25-0.90 Hz (ideal 1 / 0 deg).
- ideal learner: cancel of -LPF_w(Delta_hat), what a perfect estimator behind the firmware u_ad filter at w rad/s could do. PID segments: u_ad is the shadow (computed, not injected).

| axis | b | tau (ms) | r (2-8 Hz) |
|---|---|---|---|
| pitch | 48.2 | 40 | 0.74 |
| roll | 59.7 | 40 | 0.73 |
| yaw | 84.1 | 40 | 0.73 |
| z_rate | 22.9 | 40 | 0.55 |

| seg | axis | -Delta static | u_ad mean | u_nom mean | u_ad share | -Delta dyn RMS | u_ad dyn RMS | band gain / phase / coh | cancel b x0.7 / x1 / x1.4 | ideal w 0.5 / 1 / 2 / 4 / 8 / 16 |
|---|---|---|---|---|---|---|---|---|---|---|
| F1 PID | pitch | -0.0738 | 0 | -0.0733 | - | 0.0288 | 0 | - | - | -0.08 / +0.12 / +0.30 / +0.50 / +0.73 / +0.90 |
| F1 PID | roll | +0.0552 | 0 | +0.0553 | - | 0.0296 | 0 | - | - | -0.30 / +0.03 / +0.27 / +0.49 / +0.72 / +0.90 |
| F1 PID | yaw | +0.0108 | 0 | +0.0108 | - | 0.0482 | 0 | - | - | +0.14 / +0.21 / +0.33 / +0.53 / +0.79 / +0.94 |
| F1 PID | z_rate | +1.8586 | 0 | +1.8573 | - | 0.3311 | 0 | - | - | +0.31 / +0.39 / +0.49 / +0.62 / +0.78 / +0.92 |

## Load swing frequency

Pendulum: L = rope 33 cm + half bottle 10 cm = 0.43 m. Simple sqrt(g/L)/2pi = 0.76 Hz; drone free to move, sqrt(g (M+m) / (M L))/2pi = 0.95 Hz (M 988 g, m 570 g). Measured: peak of the body-rate PSD in 0.15-1.5 Hz.

| seg | pitch peak (Hz) | roll peak (Hz) |
|---|---|---|
| F1 PID | 0.39 | 0.49 |

## Outside-force feature: body accel x/y vs the disturbance

Body-frame accel x/y sees only non-thrust forces (rope pull, slosh, wind, walls): thrust is along body z. If the torque is lever arm x force, -Delta_hat = w * a_xy with ONE constant w for any load. Per segment, means removed, both LPF 3 Hz. r = correlation at lag 0; cancel = r^2 = share of the dynamic disturbance a constant weight removes; w in control units per g. Lead = lag (+-300 ms) with the best |r|, positive = accel leads the disturbance. S6 = best single feature of x, x tanh x, cross, xm (u_nom left out: -Delta_hat contains u_nom by construction), then those four together, then together + accel x/y (offline least squares, constant weights). The S6 columns are an UPPER BOUND, partly circular: x and xm together rebuild the PID P term that sits inside -Delta_hat. Accel is an independent sensor, so its columns are clean. Caveat: an IMU off the centre of gravity adds (angular accel x offset), a few mg here.

| seg | axis | acc X r / cancel / w | acc Y r / cancel / w | lead X / Y (ms) | best S6 one | S6 four | S6 four + acc |
|---|---|---|---|---|---|---|---|
| F1 PID | pitch | -0.15 / 0.02 / -0.042 | +0.02 / 0.00 / +0.007 | -300 (r +0.22) / +290 (r -0.06) | 0.10 | 0.51 | 0.51 |
| F1 PID | roll | -0.08 / 0.01 / -0.023 | -0.10 / 0.01 / -0.030 | -40 (r -0.08) / -300 (r +0.16) | 0.06 | 0.47 | 0.47 |

## Cross-coupling: does the other axis help? (vp16 48-bump split)

-Delta_hat (as above, LPF 3 Hz) fitted with constant weights on Gaussian grids, scored on held-out data (interleaved 5 s blocks: fit even, score odd, and back). Inputs in firmware units: rate / 200 deg/s, tilt / 15 deg. own24 = RBF24T (6 rate x 4 tilt, mrac_t_*_c); own48 = 8 rate x 6 tilt; own32 = 8 rate x 4 tilt; cross16 = own tilt x OTHER tilt (4 x 4); lin = other tilt + other rate as two plain features. Fair split test at 48 bumps: own48 vs own32 + cross16. Swing = same fit on 0.25-0.90 Hz band-passed signals. Upper bound for the own grids (they partly rebuild the PID term inside -Delta_hat), so a cross gain here is the conservative signal. Grid centres of own48/own32 are a test choice, not firmware.

| seg | axis | own24 in / held-out | own24 + lin | own48 | own32 + cross16 | swing own48 / own32 + cross16 |
|---|---|---|---|---|---|---|
| F1 PID | pitch | 0.35 / 0.23 | 0.22 | -0.04 | 0.12 | 0.15 / 0.19 |
| F1 PID | roll | 0.23 / -0.15 | -0.14 | -0.13 | -0.28 | -0.08 / -0.14 |

## vp16 grid inputs: where the drone sits (centre placement, gamma T rule)

Each vp16 grid input in its normalised unit, p1 / p50 / p99 per segment. pitch/roll: rate / 200 deg/s, tilt / 15 deg, cross = other tilt / 15 deg. yaw: rate / 160 deg/s, heading error ~ gyrozPID.Des / 162 (= Des / Kp 6 / 27 deg, yawPID not logged), cross = Z thrust share. Z: climb rate / 1.0 m/s, thrust share u_nom / u_max, cross = tilt size / 15 deg. x/y: velocity / 300 cm/s, position error / 375 cm, cross = other velocity / 300. |e| = MRAC error (x/y: locxs/locys Des - FB, cm/s) against e_sat; > e_sat = share of time the tanh cap saturates.

| seg | axis | in1 p1 / p50 / p99 | in2 p1 / p50 / p99 | cross p1 / p50 / p99 | abs e p50 / p95 / p99 | e_sat | > e_sat (%) |
|---|---|---|---|---|---|---|---|
| F1 PID | pitch | -0.14 / +0.00 / +0.15 | -0.84 / -0.05 / +0.45 | -0.78 / -0.02 / +0.64 | 0.081 / 0.258 / 0.391 | 0.5 | 0.5 |
| F1 PID | roll | -0.18 / +0.00 / +0.18 | -0.78 / -0.02 / +0.64 | -0.84 / -0.05 / +0.45 | 0.081 / 0.236 / 0.315 | 0.5 | 0.1 |
| F1 PID | yaw | -0.13 / +0.00 / +0.14 | -0.15 / -0.01 / +0.15 | +0.00 / +0.14 / +0.17 | 0.086 / 0.236 / 0.322 | 0.7 | 0.0 |
| F1 PID | z_rate | -0.99 / +0.00 / +0.81 | +0.00 / +0.14 / +0.17 | +0.02 / +0.19 / +0.88 | 0.358 / 0.743 / 1.781 | 0.4 | 36.2 |
| F1 PID | x | -0.30 / +0.00 / +0.30 | -0.13 / +0.00 / +0.18 | -0.30 / -0.00 / +0.30 | 10.327 / 68.198 / 105.458 | 45 | 10.7 |
| F1 PID | y | -0.30 / -0.00 / +0.30 | -0.14 / -0.01 / +0.39 | -0.30 / +0.00 / +0.30 | 10.312 / 50.812 / 98.268 | 45 | 5.8 |

