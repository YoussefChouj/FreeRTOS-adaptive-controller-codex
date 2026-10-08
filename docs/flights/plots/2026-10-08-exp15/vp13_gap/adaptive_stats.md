# Adaptive-layer stats: `vp13_gap`

Measured from the log. Basis rebuilt from logged signals (see the tool docstring). Shaded: blue PID, orange MRAC injected.

## Per MRAC segment and axis

| seg | axis | rebuild r | u_ad RMS | u_nom RMS | ratio | r(u_ad,u_nom) | r(u_ad,x) | band phase u_ad/x (deg) | band phase u_nom/x | Re H u_ad / Re H u_nom | coh | top feature (RMS share) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| F1 MRAC | pitch | 1.00 | 0.0016 | 0.1038 | 0.01 | +0.07 | +0.38 | -30 | +133 | -0.09 | 0.61 | xm 66%, u_nom 34% |
| F1 MRAC | roll | 1.00 | 0.0015 | 0.0881 | 0.02 | +0.03 | +0.54 | -18 | +143 | -0.15 | 0.67 | xm 68%, u_nom 32% |
| F1 MRAC | yaw | 1.00 | 0.0205 | 0.0555 | 0.37 | -0.14 | -0.00 | +72 | +106 | -0.04 | 0.24 | bias 1 99%, xm 1% |
| F1 MRAC | z_rate | 0.71 | 0.5965 | 1.4083 | 0.42 | +0.20 | +0.22 | -21 | +149 | -1.26 | 0.25 | bias 1 81%, u_nom 19% |

Phase of a torque against the body rate x in 0.25-0.90 Hz: +/-180 = pure damping, 0 = anti-damping, +/-90 = no energy. Re H u_ad / Re H u_nom > 0: u_ad adds damping like the PID; < 0: removes it.

## What-if: same weights, other omega_u (open-loop replay of the logged raw sum)

Band phase u_ad/x (deg) / Re H ratio, as above. Closed loop would differ (the weights would evolve differently); this isolates the filter lag.

| seg | axis | flown | 10 rad/s | 15 rad/s | 20 rad/s |
|---|---|---|---|---|---|
| F1 MRAC | pitch | -29 / -0.09 | -10 / -0.13 | -4 / -0.14 | -1 / -0.14 |
| F1 MRAC | roll | -19 / -0.14 | -3 / -0.18 | +3 / -0.18 | +7 / -0.18 |

## Weights Theta (start -> end of each MRAC segment)

| seg | axis | Theta[0] bias 1 | Theta[1] x | Theta[2] x tanh x | Theta[3] cross | Theta[4] u_nom | Theta[5] xm | |Theta| growth |
|---|---|---|---|---|---|---|---|---|
| F1 MRAC | pitch | +0.000 -> +0.000 | +0.000 -> +0.000 | +0.000 -> +0.000 | +0.000 -> +0.000 | +0.000 -> +0.020 | +0.000 -> +0.020 | 0.000 -> 0.028 |
| F1 MRAC | roll | +0.000 -> +0.000 | +0.000 -> +0.000 | +0.000 -> +0.000 | +0.000 -> +0.000 | +0.000 -> +0.020 | +0.000 -> +0.019 | 0.000 -> 0.028 |
| F1 MRAC | yaw | +0.000 -> -0.007 | +0.000 -> +0.001 | +0.000 -> +0.001 | +0.000 -> +0.000 | +0.000 -> +0.005 | +0.000 -> +0.011 | 0.000 -> 0.014 |
| F1 MRAC | z_rate | +0.000 -> +0.366 | +0.000 -> +0.008 | +0.000 -> +0.003 | +0.000 -> +0.000 | +0.000 -> +0.181 | +0.000 -> +0.034 | 0.000 -> 0.410 |

## Ext block (vp basis 7, 24 Gaussians per axis, pitch/roll)

Grid inputs in the firmware's normalised units (rate, angle); the Gaussian centres span -1..1 (basis 5: -1.5..1.5 rate, -1..1 angle). Activity = sd / mean of the most varying Gaussian over the segment: near 0 the grid acts as one constant (a bias), not as a function of the state. Ext share = ext RMS^2 / (ext RMS^2 + S6-part RMS^2). Slope = d|Theta_ext|/dt over the last third (> 0: still learning).

| seg | axis | rate p1 / p50 / p99 | angle p1 / p50 / p99 | activity | ext RMS | S6 part RMS | ext share | |Theta_ext| start -> end | slope (/s) | top ext (RMS share) |
|---|---|---|---|---|---|---|---|---|---|---|
| F1 MRAC | pitch | -0.131 / -0.000 / +0.142 | -0.596 / -0.059 / +0.414 | 0.739 | 0.0000 | 0.0022 | 0% | 0.000 -> 0.000 | +0.0000 | e23 0%, e22 0% |
| F1 MRAC | roll | -0.133 / +0.000 / +0.118 | -0.661 / -0.051 / +0.372 | 0.679 | 0.0000 | 0.0020 | 0% | 0.000 -> 0.000 | +0.0000 | e23 0%, e22 0% |

## Static offsets per segment (drift)

Expected static torque of the load at the rope point: pitch 0.112 N m (x 2 cm), roll 0.391 N m (y 7 cm), mg = 5.59 N.

| seg | mean u_nom p / r | mean u_ad p / r | mean Theta[0] p / r | pitch Des - FB (deg) | roll Des - FB | locx Des-FB (cm) | locy Des-FB | locx/y PID U mean | x FB slope (cm/s) | y FB slope | x stick on (%) | x stick vel Des median (cm/s) | x vel FB mean, no stick (cm/s) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| F1 MRAC | -0.0778 / +0.0542 | -0.0008 / +0.0006 | +0.0000 / +0.0000 | +0.07 | -0.09 | -6.7 | -2.7 | -6.1 / -4.3 | +5.60 | -1.29 | 17 | +45 | +0.51 |

## Per segment: attitude, height, motors, battery

| seg | s | pitch sd | roll sd | rate sd p / r (deg/s) | band PSD p / r | z - z_des | motor at 4000 (%) | max motor spread | V mean |
|---|---|---|---|---|---|---|---|---|---|
| F1 MRAC | 100 | 2.81 | 2.77 | 10.9 / 9.4 | 33.8 / 31.2 | -0.007 | 3.8 | 1606 | 14.50 |

## Uncertainty estimate: does u_ad match the disturbance?

Plant per axis: xdot = b (u_inj + Delta), u_inj = u_nom + (injected) u_ad. b and the delay tau are fitted on 2-8 Hz content of all airborne segments (above the load band; closed-loop fit, so b is biased: the cancel fraction is also given for b x0.7 and x1.4). Delta_hat = LPF3Hz(xdot)/b - LPF3Hz(u_inj(t - tau)) is everything the nominal model does not explain (load torque and swing, CG offset, motor mismatch, drag), in control units. A perfect adaptive layer gives u_ad = -Delta_hat.

- static: mean(-Delta_hat) is the trim the drone needs; u_ad share = mean(u_ad) / mean(-Delta_hat) (the PID integrator carries the rest)
- dynamic (means removed): cancel = 1 - var(u_ad + Delta_hat) / var(Delta_hat); 1 = cancels all, 0 = no help, < 0 = adds disturbance. Band gain / phase of u_ad against -Delta_hat in 0.25-0.90 Hz (ideal 1 / 0 deg).
- ideal learner: cancel of -LPF_w(Delta_hat), what a perfect estimator behind the firmware u_ad filter at w rad/s could do. PID segments: u_ad is the shadow (computed, not injected).

| axis | b | tau (ms) | r (2-8 Hz) |
|---|---|---|---|
| pitch | 65.3 | 50 | 0.80 |
| roll | 53.7 | 50 | 0.73 |
| yaw | 29.8 | 20 | 0.69 |
| z_rate | 28.9 | 40 | 0.42 |

| seg | axis | -Delta static | u_ad mean | u_nom mean | u_ad share | -Delta dyn RMS | u_ad dyn RMS | band gain / phase / coh | cancel b x0.7 / x1 / x1.4 | ideal w 0.5 / 1 / 2 / 4 / 8 / 16 |
|---|---|---|---|---|---|---|---|---|---|---|
| F1 MRAC | pitch | -0.0786 | -0.0008 | -0.0778 | 0.01 | 0.0271 | 0.0013 | 0.02 / -62 / 0.25 | +0.04 / +0.04 / +0.04 | -0.32 / -0.04 / +0.20 / +0.46 / +0.73 / +0.91 |
| F1 MRAC | roll | +0.0549 | +0.0006 | +0.0542 | 0.01 | 0.0230 | 0.0014 | 0.02 / +74 / 0.09 | +0.03 / +0.03 / +0.03 | -0.05 / +0.17 / +0.37 / +0.56 / +0.77 / +0.91 |
| F1 MRAC | yaw | +0.0158 | +0.0110 | +0.0046 | 0.70 | 0.0375 | 0.0173 | 0.09 / -118 / 0.36 | +0.00 / +0.01 / +0.01 | +0.24 / +0.39 / +0.56 / +0.72 / +0.87 / +0.96 |
| F1 MRAC | z_rate | +1.9308 | +0.5692 | +1.3614 | 0.29 | 0.4039 | 0.1784 | 0.29 / -7 / 0.52 | +0.34 / +0.37 / +0.39 | +0.24 / +0.28 / +0.34 / +0.47 / +0.67 / +0.86 |

## Load swing frequency

Pendulum: L = rope 33 cm + half bottle 10 cm = 0.43 m. Simple sqrt(g/L)/2pi = 0.76 Hz; drone free to move, sqrt(g (M+m) / (M L))/2pi = 0.95 Hz (M 988 g, m 570 g). Measured: peak of the body-rate PSD in 0.15-1.5 Hz.

| seg | pitch peak (Hz) | roll peak (Hz) |
|---|---|---|
| F1 MRAC | 0.88 | 0.49 |

## Outside-force feature: body accel x/y vs the disturbance

Body-frame accel x/y sees only non-thrust forces (rope pull, slosh, wind, walls): thrust is along body z. If the torque is lever arm x force, -Delta_hat = w * a_xy with ONE constant w for any load. Per segment, means removed, both LPF 3 Hz. r = correlation at lag 0; cancel = r^2 = share of the dynamic disturbance a constant weight removes; w in control units per g. Lead = lag (+-300 ms) with the best |r|, positive = accel leads the disturbance. S6 = best single feature of x, x tanh x, cross, xm (u_nom left out: -Delta_hat contains u_nom by construction), then those four together, then together + accel x/y (offline least squares, constant weights). The S6 columns are an UPPER BOUND, partly circular: x and xm together rebuild the PID P term that sits inside -Delta_hat. Accel is an independent sensor, so its columns are clean. Caveat: an IMU off the centre of gravity adds (angular accel x offset), a few mg here.

| seg | axis | acc X r / cancel / w | acc Y r / cancel / w | lead X / Y (ms) | best S6 one | S6 four | S6 four + acc |
|---|---|---|---|---|---|---|---|
| F1 MRAC | pitch | -0.29 / 0.09 / -0.079 | +0.15 / 0.02 / +0.048 | +70 (r -0.31) / -70 (r +0.17) | 0.09 | 0.53 | 0.56 |
| F1 MRAC | roll | +0.05 / 0.00 / +0.010 | -0.08 / 0.01 / -0.022 | +220 (r +0.07) / -300 (r +0.13) | 0.07 | 0.49 | 0.50 |

## Feature capability (all MRAC segments pooled)

Theta_i moves at gamma_i * s * phi_i / (1 + |phi|^2) (API/mrac.c:748, denom :932), so a feature's learning drive is gamma_i * E[phi_i^2 / (1 + |phi|^2)] (speed, shown relative to the bias). Its reach is lim_i * RMS(phi_i): the largest torque it can make with Theta_i at its projection bound. gamma_i, lim_i from the MRAC_BASIS rows (pitch/roll/yaw/z), mrac_g_phi and the preset g assumed 1.

| axis | feature | RMS phi | gamma | drive E[phi^2/(1+|phi|^2)] | speed vs bias | lim | reach lim*RMS phi | actual RMS Theta*phi | Theta0 63% time (s) per seg |
|---|---|---|---|---|---|---|---|---|---|
| pitch | bias 1 | 1 | 1.50 | 0.482 | 1 | 0.150 | 0.15 | 0 | F1 - |
| pitch | x | 0.19 | 0.20 | 0.0158 | 0.0044 | 0.050 | 0.00951 | 0 |  |
| pitch | x tanh x | 0.0725 | 0.05 | 0.00198 | 0.00014 | 0.020 | 0.00145 | 0 |  |
| pitch | cross | 0.0688 | 0.05 | 0.00195 | 0.00013 | 0.050 | 0.00344 | 0 |  |
| pitch | u_nom | 0.104 | 0.10 | 0.00511 | 0.00071 | 0.200 | 0.0208 | 0.00129 |  |
| pitch | xm | 0.159 | 0.10 | 0.0115 | 0.0016 | 0.150 | 0.0238 | 0.00179 |  |
| roll | bias 1 | 1 | 1.50 | 0.486 | 1 | 0.150 | 0.15 | 0 | F1 - |
| roll | x | 0.165 | 0.20 | 0.0121 | 0.0033 | 0.050 | 0.00824 | 0 |  |
| roll | x tanh x | 0.0578 | 0.05 | 0.0013 | 8.9e-05 | 0.020 | 0.00116 | 0 |  |
| roll | cross | 0.0679 | 0.05 | 0.00194 | 0.00013 | 0.050 | 0.0034 | 0 |  |
| roll | u_nom | 0.0881 | 0.10 | 0.00372 | 0.00051 | 0.200 | 0.0176 | 0.00111 |  |
| roll | xm | 0.139 | 0.10 | 0.00859 | 0.0012 | 0.150 | 0.0208 | 0.00164 |  |
| yaw | bias 1 | 1 | 1.00 | 0.477 | 1 | 0.090 | 0.09 | 0.0207 | F1 1 |
| yaw | x | 0.326 | 0.10 | 0.0178 | 0.0037 | 0.030 | 0.00978 | 0.000255 |  |
| yaw | x tanh x | 0.279 | 0.05 | 0.00982 | 0.001 | 0.012 | 0.00334 | 8.1e-05 |  |
| yaw | cross | 0 | 0.05 | 0 | 0 | 0.030 | 0 | 0 |  |
| yaw | u_nom | 0.0555 | 0.10 | 0.00133 | 0.00028 | 0.120 | 0.00666 | 0.000147 |  |
| yaw | xm | 0.302 | 0.10 | 0.0163 | 0.0034 | 0.090 | 0.0271 | 0.00163 |  |
| z_rate | bias 1 | 1 | 2.00 | 0.252 | 1 | 1.000 | 1 | 0.417 | F1 4 |
| z_rate | x | 0.841 | 0.50 | 0.0197 | 0.02 | 0.100 | 0.0841 | 0.00106 |  |
| z_rate | x tanh x | 0.825 | 0.10 | 0.0136 | 0.0027 | 0.050 | 0.0412 | 0.00196 |  |
| z_rate | cross | 0 | 0.10 | 0 | 0 | 0.050 | 0 | 0 |  |
| z_rate | u_nom | 1.41 | 0.20 | 0.457 | 0.18 | 0.200 | 0.282 | 0.205 |  |
| z_rate | xm | 0.161 | 0.20 | 0.00569 | 0.0023 | 0.200 | 0.0323 | 0.00267 |  |

