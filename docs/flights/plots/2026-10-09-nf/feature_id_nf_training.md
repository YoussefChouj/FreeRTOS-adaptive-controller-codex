# Feature identification: nf_training (6 logs, 6 segments)

Logs: custom_random3_baseline_10wind.csv, custom_random3_baseline_20wind.csv, custom_random3_baseline_30wind.csv, custom_random3_baseline_40wind.csv, custom_random3_baseline_50wind.csv, custom_random3_baseline_nowind.csv

Target y = fa, the measured aerodynamic residual force on world axis x/y/z (Neural-Fly data), one segment per file, so LOSO = leave one wind condition out; file means removed. Features: v (m/s), |v|v drag, thr = thrust direction R[:, 2], body rates w, per-motor pwm about the motor mean, T_sp, lags. Resampled to 100 Hz.

## Tracking RMSE |p - p_d| [m] by method (columns) and wind condition (rows), measured from the files

| condition | baseline |
|---|---|
| 10wind | 0.133 |
| 20wind | 0.130 |
| 30wind | 0.127 |
| 40wind | 0.132 |
| 50wind | 0.127 |
| nowind | 0.095 |

## x (72364 samples, 33 candidates; all-feature R^2 0.78)

Consensus (in the top 5 of at least 3 of the 5 methods): **vx@-0.10s** (forward, sindy, lasso, mi), **vx** (forward, sindy, mi, lag), **thrx@-0.50s** (forward, lasso, mi), **vx@-0.25s** (forward, lasso, mi)

| step | forward selection: + feature | LOSO R^2 |
|---|---|---|
| 1 | vx@-0.10s | 0.622 |
| 2 | thrx@-0.50s | 0.632 |
| 3 | vx | 0.638 |
| 4 | thrx@-0.10s | 0.749 |
| 5 | vx@-0.25s | 0.766 |
| 6 | pwm2 | 0.771 |
| 7 | vx@-0.50s | 0.774 |
| 8 | pwm0 | 0.777 |

| SINDy threshold | terms | LOSO R^2 | largest terms (standardised coef) |
|---|---|---|---|
| 0.01 | 28 | 0.776 | vx@-0.10s -10.06, vx +9.04, vz@-0.10s +1.66, thrx -1.64, vz -1.28, vx@-0.25s +0.69 |
| 0.02 | 26 | 0.776 | vx@-0.10s -10.05, vx +9.04, vz@-0.10s +1.65, thrx -1.63, vz -1.29, vx@-0.25s +0.69 |
| 0.05 | 17 | 0.773 | vx@-0.10s -10.03, vx +9.01, vz@-0.10s +1.76, thrx -1.39, vz -1.37, thrx@-0.10s -0.81 |
| 0.10 | 11 | 0.770 | vx@-0.10s -9.33, vx +8.55, vz@-0.10s +1.73, vz -1.42, thrx@-0.10s -1.10, thrx -0.98 |
| 0.20 | 6 | 0.759 | vx@-0.10s -8.22, vx +7.86, thrx@-0.10s -1.35, thrx -0.71, T_sp -0.27, pwm_sum +0.23 |
| 0.30 | 4 | 0.756 | vx@-0.10s -8.04, vx +7.68, thrx@-0.10s -1.30, thrx -0.72 |

LASSO entry order: vx@-0.10s, thrx@-0.50s, vx@-0.25s, wq, thrx@-0.25s, vz, pwm2, pwm1, vx@-0.50s, vz@-0.50s, pwm0, |v|vz

| MI rank | feature | MI (bits) |
|---|---|---|
| 1 | vx@-0.10s | 0.728 |
| 2 | vx | 0.625 |
| 3 | |v|vx | 0.620 |
| 4 | vx@-0.25s | 0.601 |
| 5 | thrx@-0.50s | 0.502 |
| 6 | wq | 0.410 |
| 7 | thrx@-0.25s | 0.224 |
| 8 | vx@-0.50s | 0.173 |
| 9 | |v|vz | 0.127 |
| 10 | |v|vy | 0.108 |

| base signal | best lag (s) | r | best causal lag (s) | r causal |
|---|---|---|---|---|
| vx | +0.15 | -0.79 | 0.15 | -0.79 |
| thrx | +0.50 | -0.71 | 0.50 | -0.71 |
| wq | +0.05 | +0.66 | 0.05 | +0.66 |
| pwm0 | -0.30 | +0.51 | 0.50 | -0.43 |
| pwm2 | -0.25 | +0.55 | 0.50 | -0.43 |
| pwm3 | -0.25 | -0.50 | 0.50 | +0.43 |
| pwm1 | -0.25 | -0.54 | 0.50 | +0.41 |
| thrz | +0.50 | +0.30 | 0.50 | +0.30 |
| T_sp | +0.75 | -0.22 | 0.75 | -0.22 |
| pwm_sum | +0.70 | -0.20 | 0.70 | -0.20 |
| vz | +0.55 | -0.07 | 0.55 | -0.07 |
| vy | -0.20 | -0.04 | 0.00 | -0.04 |
| thry | +0.25 | -0.04 | 0.25 | -0.04 |
| wp | +0.70 | +0.03 | 0.70 | +0.03 |
| wr | -1.00 | -0.04 | 1.00 | +0.02 |

## y (72364 samples, 33 candidates; all-feature R^2 0.42)

Consensus (in the top 5 of at least 3 of the 5 methods): **vy** (forward, sindy, lasso, mi, lag), **vy@-0.10s** (forward, sindy, lasso, mi), **wp** (sindy, lasso, mi, lag), **thry** (forward, sindy, lag)

| step | forward selection: + feature | LOSO R^2 |
|---|---|---|
| 1 | vy | 0.292 |
| 2 | thry@-0.25s | 0.311 |
| 3 | vy@-0.50s | 0.366 |
| 4 | thry | 0.378 |
| 5 | vy@-0.10s | 0.408 |
| 6 | wp | 0.412 |
| 7 | vy@-0.25s | 0.422 |
| 8 | T_sp | 0.424 |

| SINDy threshold | terms | LOSO R^2 | largest terms (standardised coef) |
|---|---|---|---|
| 0.01 | 26 | 0.425 | vy +5.24, vy@-0.10s -4.61, thry -2.07, vy@-0.25s -0.88, thry@-0.10s +0.57, wp -0.45 |
| 0.02 | 25 | 0.425 | vy +5.24, vy@-0.10s -4.60, thry -2.07, vy@-0.25s -0.88, thry@-0.10s +0.57, wp -0.45 |
| 0.05 | 12 | 0.425 | vy +5.27, vy@-0.10s -4.71, thry -1.95, vy@-0.25s -0.76, wp -0.44, thry@-0.10s +0.37 |
| 0.10 | 7 | 0.422 | vy +5.23, vy@-0.10s -4.72, thry -2.12, vy@-0.25s -0.74, wp -0.51, thry@-0.10s +0.49 |
| 0.20 | 5 | 0.419 | vy +5.44, vy@-0.10s -5.11, thry -1.77, vy@-0.25s -0.58, wp -0.44 |
| 0.30 | 5 | 0.419 | vy +5.44, vy@-0.10s -5.11, thry -1.77, vy@-0.25s -0.58, wp -0.44 |

LASSO entry order: vy, thry@-0.25s, |v|vy, wp, vy@-0.10s, thry@-0.10s, vy@-0.25s, pwm_sum, vz@-0.50s, vx, vy@-0.50s, thry@-0.50s

| MI rank | feature | MI (bits) |
|---|---|---|
| 1 | vy | 0.241 |
| 2 | |v|vy | 0.238 |
| 3 | vy@-0.10s | 0.219 |
| 4 | thry@-0.50s | 0.175 |
| 5 | wp | 0.175 |
| 6 | thry@-0.25s | 0.172 |
| 7 | vy@-0.25s | 0.139 |
| 8 | thry@-0.10s | 0.095 |
| 9 | thry | 0.048 |
| 10 | |v|vz | 0.033 |

| base signal | best lag (s) | r | best causal lag (s) | r causal |
|---|---|---|---|---|
| vy | +0.00 | -0.54 | 0.00 | -0.54 |
| thry | +0.40 | -0.50 | 0.40 | -0.50 |
| wp | -0.10 | -0.50 | 0.00 | -0.46 |
| wr | +0.80 | -0.11 | 0.80 | -0.11 |
| pwm0 | -0.50 | -0.11 | 0.30 | +0.06 |
| pwm2 | -0.45 | +0.08 | 0.25 | -0.05 |
| pwm3 | -0.40 | -0.14 | 0.50 | +0.05 |
| pwm1 | -0.40 | +0.15 | 0.40 | -0.05 |
| thrz | -0.50 | -0.04 | 0.55 | +0.04 |
| vz | +0.50 | +0.03 | 0.50 | +0.03 |
| vx | +0.80 | -0.02 | 0.80 | -0.02 |
| pwm_sum | +0.10 | -0.02 | 0.10 | -0.02 |
| T_sp | +0.15 | -0.02 | 0.15 | -0.02 |
| thrx | +0.20 | +0.02 | 0.20 | +0.02 |
| wq | +0.55 | +0.01 | 0.55 | +0.01 |

## z (72364 samples, 33 candidates; all-feature R^2 0.78)

Consensus (in the top 5 of at least 3 of the 5 methods): **T_sp** (forward, sindy, mi, lag), **vz@-0.50s** (forward, lasso, mi), **vz** (forward, sindy, lag), **vz@-0.25s** (forward, sindy, mi), **thrz** (forward, sindy, lag), **pwm_sum** (lasso, mi, lag), **wq** (lasso, mi, lag)

| step | forward selection: + feature | LOSO R^2 |
|---|---|---|
| 1 | vz@-0.50s | 0.376 |
| 2 | vz | 0.466 |
| 3 | vz@-0.25s | 0.608 |
| 4 | T_sp | 0.687 |
| 5 | thrz | 0.733 |
| 6 | vz@-0.10s | 0.776 |
| 7 | wq | 0.788 |
| 8 | vx@-0.50s | 0.789 |

| SINDy threshold | terms | LOSO R^2 | largest terms (standardised coef) |
|---|---|---|---|
| 0.01 | 25 | 0.783 | vz@-0.10s -8.92, vz +7.96, T_sp -1.14, vz@-0.25s +0.88, thrx@-0.10s -0.62, thrx +0.58 |
| 0.02 | 20 | 0.773 | vz@-0.10s -8.89, vz +7.94, T_sp -1.13, vz@-0.25s +0.84, thrx@-0.10s -0.58, thrx +0.51 |
| 0.05 | 13 | 0.774 | vz@-0.10s -8.91, vz +7.95, T_sp -1.13, vz@-0.25s +0.85, thrx@-0.10s -0.80, thrx +0.66 |
| 0.10 | 8 | 0.781 | vz@-0.10s -9.00, vz +8.06, T_sp -1.08, vz@-0.25s +0.84, thrx@-0.10s -0.55, thrx +0.49 |
| 0.20 | 8 | 0.781 | vz@-0.10s -9.00, vz +8.06, T_sp -1.08, vz@-0.25s +0.84, thrx@-0.10s -0.55, thrx +0.49 |
| 0.30 | 5 | 0.770 | vz@-0.10s -9.86, vz +8.71, T_sp -1.19, vz@-0.25s +1.05, thrz -0.48 |

LASSO entry order: vz@-0.50s, pwm_sum, wq, pwm0, |v|vz, vz, pwm2, pwm3, vx@-0.25s, vx@-0.50s, vz@-0.25s, thrz

| MI rank | feature | MI (bits) |
|---|---|---|
| 1 | vz@-0.50s | 0.358 |
| 2 | pwm_sum | 0.337 |
| 3 | T_sp | 0.257 |
| 4 | vz@-0.25s | 0.185 |
| 5 | wq | 0.081 |
| 6 | vz@-0.10s | 0.062 |
| 7 | vx@-0.25s | 0.055 |
| 8 | thrx@-0.50s | 0.054 |
| 9 | vx@-0.10s | 0.051 |
| 10 | |v|vx | 0.049 |

| base signal | best lag (s) | r | best causal lag (s) | r causal |
|---|---|---|---|---|
| pwm_sum | +0.10 | +0.65 | 0.10 | +0.65 |
| T_sp | +0.15 | +0.65 | 0.15 | +0.65 |
| vz | +0.50 | -0.62 | 0.50 | -0.62 |
| thrz | +0.70 | -0.38 | 0.70 | -0.38 |
| wq | +0.05 | -0.26 | 0.05 | -0.26 |
| pwm0 | -0.05 | -0.24 | 0.00 | -0.23 |
| pwm1 | -0.05 | +0.22 | 0.00 | +0.21 |
| thrx | -0.35 | -0.22 | 0.45 | +0.20 |
| pwm3 | -0.10 | +0.21 | 0.00 | +0.19 |
| pwm2 | -0.10 | -0.20 | 0.00 | -0.18 |
| vx | +0.10 | +0.18 | 0.10 | +0.18 |
| wp | +0.60 | -0.04 | 0.60 | -0.04 |
| thry | +0.25 | +0.04 | 0.25 | +0.04 |
| wr | +0.30 | +0.03 | 0.30 | +0.03 |
| vy | -0.20 | +0.03 | 0.70 | -0.03 |

