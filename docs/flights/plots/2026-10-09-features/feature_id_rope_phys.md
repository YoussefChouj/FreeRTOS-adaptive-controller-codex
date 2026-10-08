# Feature identification: rope_phys (14 logs, 18 segments)

Logs: exp14_vp8-rbf12-load, exp14_vp9-rbf6-load, exp14_vp10-rbf24-load, exp14_vp11-s6rbf12-load, vp12_flight, vp13_gap, vp14_g4, vp13_g4, vp12_fixed, vp14_g4_FRESH_BATTERY, pid_ref, pid_ref_2, vp13_g4_last, pid_ref_2_circle

Target y = -Delta_hat (the ideal u_ad), segment means removed. Score = leave-one-segment-out R^2. Features are causal and normalised (rate /0.3 rad/s, tilt /0.2 rad, acc m/s^2, vel /50 cm/s, _bp = causal 0.25-0.9 Hz band-pass, _bpq = its quadrature (derivative / w0), @-T = delayed by T, _o = the other axis). Method details: module docstring.

## pitch (80954 samples, 41 candidates; all-feature R^2 0.39)

Consensus (in the top 5 of at least 3 of the 5 methods): **acc_bpq** (forward, sindy, lasso, mi, lag), **thrust** (forward, sindy, lasso, lag), **angacc** (forward, lasso, lag), **rate@-0.25s** (forward, lasso, mi)

| step | forward selection: + feature | LOSO R^2 |
|---|---|---|
| 1 | acc_bpq | 0.140 |
| 2 | angacc | 0.199 |
| 3 | thrust | 0.245 |
| 4 | vel_o | 0.275 |
| 5 | rate@-0.25s | 0.316 |
| 6 | swing0.70*fz | 0.327 |
| 7 | rate | 0.344 |
| 8 | tilt | 0.352 |

| SINDy threshold | terms | LOSO R^2 | largest terms (standardised coef) |
|---|---|---|---|
| 0.01 | 39 | 0.390 | tilt_bpq -0.65, tilt -0.28, acc_bpq -0.27, thrust -0.23, rate -0.23, swing0.70q +0.22 |
| 0.02 | 32 | 0.392 | tilt_bpq -0.66, tilt -0.29, acc_bpq -0.28, thrust -0.23, swing0.70q +0.22, rate -0.22 |
| 0.05 | 23 | 0.389 | tilt_bpq -0.65, acc_bpq -0.27, thrust -0.23, tilt -0.22, swing0.70q +0.22, rate -0.21 |
| 0.10 | 8 | 0.354 | tilt_bpq -0.55, rate -0.31, acc_bpq -0.30, swing0.70q +0.26, thrust -0.22, vel_o -0.20 |
| 0.20 | 4 | 0.282 | acc_bpq -0.35, rate -0.33, thrust -0.24, tilt_bpq -0.22 |
| 0.30 | 1 | 0.020 | tilt_bpq -0.16 |

LASSO entry order: acc_bpq, angacc, thrust, rate@-0.25s, tilt@-0.10s, rate, rate|rate|, swing0.70*fz, swing0.70, vel_o, tilt_o, acc_bp

| MI rank | feature | MI (bits) |
|---|---|---|
| 1 | acc_bpq | 0.152 |
| 2 | tilt@-0.10s | 0.100 |
| 3 | rate_bp | 0.082 |
| 4 | rate@-0.25s | 0.079 |
| 5 | tilt | 0.077 |
| 6 | tilt_bpq | 0.069 |
| 7 | tilt|tilt| | 0.068 |
| 8 | thrust | 0.066 |
| 9 | swing0.70*fz | 0.064 |
| 10 | swing0.70 | 0.064 |

| base signal | best lag (s) | r | best causal lag (s) | r causal |
|---|---|---|---|---|
| acc_bpq | +0.05 | -0.41 | 0.05 | -0.41 |
| acc_bp | +0.35 | +0.36 | 0.35 | +0.36 |
| vel_o | -0.30 | -0.46 | 0.75 | -0.33 |
| thrust | +0.05 | -0.31 | 0.05 | -0.31 |
| angacc | +0.00 | -0.28 | 0.00 | -0.28 |
| swing0.70 | -0.95 | +0.33 | 0.60 | -0.27 |
| rate_bp | -1.00 | +0.30 | 1.00 | +0.27 |
| rate | -0.10 | -0.32 | 0.25 | +0.27 |
| tilt | +0.10 | -0.26 | 0.10 | -0.26 |
| swing0.70q | +0.95 | -0.24 | 0.95 | -0.24 |
| tilt_bpq | -1.00 | -0.33 | 1.00 | -0.20 |
| yawrate | +0.70 | +0.17 | 0.70 | +0.17 |
| tilt_bp | -0.70 | +0.23 | 0.85 | -0.14 |
| acc | +0.60 | +0.13 | 0.60 | +0.13 |
| tilt_o | +0.25 | +0.11 | 0.25 | +0.11 |
| swing0.50 | -1.00 | +0.19 | 1.00 | +0.11 |
| swing0.50q | -0.65 | +0.15 | 0.80 | -0.08 |
| vel | +0.50 | -0.08 | 0.50 | -0.08 |
| swing0.35 | -0.45 | -0.16 | 0.15 | +0.08 |
| rate_o | +0.50 | +0.07 | 0.50 | +0.07 |
| swing0.35q | -0.85 | +0.12 | 0.00 | -0.06 |
| vel_bp | -0.40 | +0.07 | 0.50 | +0.05 |
| acc_o | +0.20 | -0.03 | 0.20 | -0.03 |

## roll (80954 samples, 41 candidates; all-feature R^2 0.31)

Consensus (in the top 5 of at least 3 of the 5 methods): **yawrate** (forward, lasso, mi, lag), **acc_bpq** (forward, lasso, mi, lag), **angacc** (forward, lasso, lag), **vel_o** (forward, sindy, lasso), **tilt** (sindy, mi, lag)

| step | forward selection: + feature | LOSO R^2 |
|---|---|---|
| 1 | yawrate | 0.090 |
| 2 | angacc | 0.146 |
| 3 | vel_o | 0.194 |
| 4 | rate@-0.25s | 0.237 |
| 5 | acc_bpq | 0.260 |
| 6 | thrust | 0.272 |
| 7 | swing0.70 | 0.280 |
| 8 | tilt | 0.287 |

| SINDy threshold | terms | LOSO R^2 | largest terms (standardised coef) |
|---|---|---|---|
| 0.01 | 33 | 0.314 | tilt +0.91, tilt@-0.25s -0.47, tilt_bpq +0.40, vel_o +0.34, rate -0.32, rate_bp -0.23 |
| 0.02 | 25 | 0.317 | tilt +0.92, tilt@-0.25s -0.47, tilt_bpq +0.38, vel_o +0.34, rate -0.32, rate_bp -0.24 |
| 0.05 | 18 | 0.316 | tilt +0.99, tilt@-0.25s -0.52, rate -0.37, vel_o +0.34, tilt_bpq +0.33, rate_bp -0.22 |
| 0.10 | 10 | 0.302 | tilt +0.80, tilt@-0.25s -0.64, vel_o +0.35, rate -0.34, tilt_bpq +0.33, rate_bp -0.32 |
| 0.20 | 5 | 0.259 | tilt +0.85, tilt@-0.25s -0.74, vel_o +0.37, rate -0.36, acc_bpq -0.31 |
| 0.30 | 4 | 0.163 | tilt +0.77, tilt@-0.25s -0.64, rate -0.35, vel_o +0.32 |

LASSO entry order: yawrate, angacc, acc_bpq, tilt@-0.10s, vel_o, rate@-0.25s, thrust, swing0.70, acc_bp, rate|rate|, swing0.70*fz, vel

| MI rank | feature | MI (bits) |
|---|---|---|
| 1 | yawrate | 0.133 |
| 2 | tilt@-0.10s | 0.095 |
| 3 | acc_bpq | 0.085 |
| 4 | tilt | 0.085 |
| 5 | tilt|tilt| | 0.075 |
| 6 | tilt@-0.25s | 0.070 |
| 7 | angacc | 0.052 |
| 8 | vel_o | 0.049 |
| 9 | rate_bp | 0.045 |
| 10 | rate@-0.25s | 0.044 |

| base signal | best lag (s) | r | best causal lag (s) | r causal |
|---|---|---|---|---|
| yawrate | +0.05 | -0.34 | 0.05 | -0.34 |
| acc_bpq | +0.05 | -0.30 | 0.05 | -0.30 |
| angacc | +0.00 | -0.28 | 0.00 | -0.28 |
| acc_bp | +0.35 | +0.25 | 0.35 | +0.25 |
| tilt | +0.10 | +0.24 | 0.10 | +0.24 |
| vel_o | -0.25 | +0.39 | 0.00 | +0.21 |
| swing0.70 | -0.95 | -0.26 | 0.55 | +0.20 |
| swing0.70q | +0.90 | +0.20 | 0.90 | +0.20 |
| rate | -0.10 | -0.34 | 0.00 | -0.19 |
| rate_bp | -1.00 | +0.25 | 1.00 | +0.19 |
| thrust | +0.05 | +0.18 | 0.05 | +0.18 |
| vel | +0.65 | -0.16 | 0.65 | -0.16 |
| tilt_bpq | -1.00 | +0.27 | 1.00 | +0.15 |
| tilt_bp | -0.70 | -0.23 | 0.75 | +0.12 |
| rate_o | -0.40 | -0.14 | 0.65 | -0.11 |
| vel_bp | -0.20 | +0.11 | 0.40 | -0.10 |
| tilt_o | -0.60 | +0.15 | 0.35 | +0.10 |
| swing0.50 | -1.00 | -0.16 | 1.00 | -0.09 |
| swing0.50q | -0.65 | -0.16 | 0.65 | +0.08 |
| acc | +0.10 | -0.08 | 0.10 | -0.08 |
| swing0.35q | -0.90 | -0.15 | 0.05 | +0.07 |
| swing0.35 | -0.45 | +0.17 | 0.95 | -0.06 |
| acc_o | +0.05 | -0.04 | 0.05 | -0.04 |

