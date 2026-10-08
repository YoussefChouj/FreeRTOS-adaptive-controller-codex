# Feature identification: nf_experiment (30 logs, 30 segments)

Logs: custom_figure8_L1_100wind.csv, custom_figure8_L1_35wind.csv, custom_figure8_L1_70p20sint.csv, custom_figure8_L1_70wind.csv, custom_figure8_L1_nowind.csv, custom_figure8_NF-C_100wind.csv, custom_figure8_NF-C_35wind.csv, custom_figure8_NF-C_70p20sint.csv, custom_figure8_NF-C_70wind.csv, custom_figure8_NF-C_nowind.csv, custom_figure8_NF-T_100wind.csv, custom_figure8_NF-T_35wind.csv, custom_figure8_NF-T_70p20sint.csv, custom_figure8_NF-T_70wind.csv, custom_figure8_NF-T_nowind.csv, custom_figure8_NF_100wind.csv, custom_figure8_NF_35wind.csv, custom_figure8_NF_70p20sint.csv, custom_figure8_NF_70wind.csv, custom_figure8_NF_nowind.csv, custom_figure8_baseline_100wind.csv, custom_figure8_baseline_35wind.csv, custom_figure8_baseline_70p20sint.csv, custom_figure8_baseline_70wind.csv, custom_figure8_baseline_nowind.csv, custom_figure8_indi_100wind.csv, custom_figure8_indi_35wind.csv, custom_figure8_indi_70p20sint.csv, custom_figure8_indi_70wind.csv, custom_figure8_indi_nowind.csv

Target y = fa, the measured aerodynamic residual force on world axis x/y/z (Neural-Fly data), one segment per file, so LOSO = leave one wind condition out; file means removed. Features: v (m/s), |v|v drag, thr = thrust direction R[:, 2], body rates w, per-motor pwm about the motor mean, T_sp, lags. Resampled to 100 Hz.

## Tracking RMSE |p - p_d| [m] by method (columns) and wind condition (rows), measured from the files

| condition | L1 | NF | NF-C | NF-T | baseline | indi |
|---|---|---|---|---|---|---|
| 100wind | 0.339 | 0.245 | 0.259 | 0.191 | 0.456 | 0.281 |
| 35wind | 0.096 | 0.075 | 0.086 | 0.073 | 0.127 | 0.086 |
| 70p20sint | 0.142 | 0.102 | 0.147 | 0.113 | 0.357 | 0.123 |
| 70wind | 0.197 | 0.149 | 0.150 | 0.122 | 0.243 | 0.169 |
| nowind | 0.056 | 0.038 | 0.053 | 0.047 | 0.111 | 0.080 |

## x (150608 samples, 33 candidates; all-feature R^2 0.84)

Consensus (in the top 5 of at least 3 of the 5 methods): **vx** (forward, sindy, lasso, mi, lag), **thrz** (forward, lasso, mi, lag), **vx@-0.10s** (forward, sindy, lasso, mi), **thrx** (forward, lasso, lag), **T_sp** (forward, sindy, lag), **vx@-0.25s** (sindy, lasso, mi)

| step | forward selection: + feature | LOSO R^2 |
|---|---|---|
| 1 | vx | 0.442 |
| 2 | thrz | 0.628 |
| 3 | vx@-0.10s | 0.679 |
| 4 | thrx | 0.740 |
| 5 | T_sp | 0.804 |
| 6 | pwm_sum | 0.819 |
| 7 | vz@-0.50s | 0.835 |
| 8 | thrx@-0.25s | 0.837 |

| SINDy threshold | terms | LOSO R^2 | largest terms (standardised coef) |
|---|---|---|---|
| 0.01 | 28 | 0.844 | vx@-0.10s -11.86, vx +8.58, vx@-0.25s +3.93, vz@-0.10s +2.01, vz@-0.25s -1.52, T_sp -1.10 |
| 0.02 | 20 | 0.844 | vx@-0.10s -11.78, vx +8.52, vx@-0.25s +3.93, vz@-0.10s +1.98, vz@-0.25s -1.53, T_sp -1.10 |
| 0.05 | 16 | 0.841 | vx@-0.10s -11.79, vx +8.57, vx@-0.25s +3.85, vz@-0.10s +2.03, vz@-0.25s -1.43, T_sp -1.11 |
| 0.10 | 14 | 0.839 | vx@-0.10s -11.77, vx +8.62, vx@-0.25s +3.87, vz@-0.10s +1.97, vz@-0.25s -1.42, T_sp -1.10 |
| 0.20 | 10 | 0.824 | vx@-0.10s -8.16, vx +6.97, vx@-0.25s +1.42, vz +1.40, T_sp -1.21, vz@-0.10s -0.97 |
| 0.30 | 10 | 0.824 | vx@-0.10s -8.16, vx +6.97, vx@-0.25s +1.42, vz +1.40, T_sp -1.21, vz@-0.10s -0.97 |

LASSO entry order: vx, thrz, vx@-0.10s, thrx, vx@-0.25s, T_sp, vx@-0.50s, thrx@-0.50s, vz@-0.10s, thrx@-0.25s, vz@-0.25s, wq

| MI rank | feature | MI (bits) |
|---|---|---|
| 1 | vx | 0.594 |
| 2 | vx@-0.10s | 0.567 |
| 3 | |v|vx | 0.566 |
| 4 | thrz | 0.520 |
| 5 | vx@-0.25s | 0.498 |
| 6 | thrx@-0.50s | 0.458 |
| 7 | thrx@-0.25s | 0.448 |
| 8 | thrx@-0.10s | 0.426 |
| 9 | thrx | 0.408 |
| 10 | vx@-0.50s | 0.373 |

| base signal | best lag (s) | r | best causal lag (s) | r causal |
|---|---|---|---|---|
| vx | -0.25 | -0.69 | 0.00 | -0.67 |
| thrz | +0.05 | +0.60 | 0.05 | +0.60 |
| thrx | +0.25 | -0.60 | 0.25 | -0.60 |
| T_sp | +1.00 | -0.40 | 1.00 | -0.40 |
| pwm_sum | +1.00 | -0.40 | 1.00 | -0.40 |
| pwm1 | +0.10 | +0.37 | 0.10 | +0.37 |
| pwm2 | +0.05 | -0.36 | 0.05 | -0.36 |
| pwm0 | +0.10 | -0.28 | 0.10 | -0.28 |
| pwm3 | +0.10 | +0.25 | 0.10 | +0.25 |
| vz | +0.65 | -0.18 | 0.65 | -0.18 |
| wr | +0.80 | -0.14 | 0.80 | -0.14 |
| thry | -0.05 | -0.13 | 0.00 | -0.12 |
| wq | -0.60 | +0.45 | 1.00 | -0.10 |
| vy | -1.00 | -0.18 | 1.00 | +0.10 |
| wp | -0.55 | -0.05 | 0.10 | +0.01 |

## y (150608 samples, 33 candidates; all-feature R^2 0.07)

Consensus (in the top 5 of at least 3 of the 5 methods): **T_sp** (forward, sindy, lasso, mi, lag), **thry** (forward, lasso, mi, lag), **vy** (forward, sindy, lasso)

| step | forward selection: + feature | LOSO R^2 |
|---|---|---|
| 1 | thry | 0.016 |
| 2 | T_sp | 0.035 |
| 3 | vy@-0.25s | 0.042 |
| 4 | vy | 0.049 |
| 5 | vy@-0.10s | 0.063 |
| 6 | thry@-0.25s | 0.064 |
| 7 | pwm_sum | 0.065 |
| 8 | pwm3 | 0.066 |

| SINDy threshold | terms | LOSO R^2 | largest terms (standardised coef) |
|---|---|---|---|
| 0.01 | 31 | 0.071 | vx@-0.10s -1.17, vx +0.61, vz@-0.10s +0.61, vx@-0.25s +0.57, thrx +0.44, thrx@-0.10s -0.44 |
| 0.02 | 26 | 0.071 | vx@-0.10s -1.21, vx@-0.25s +0.65, vz@-0.10s +0.63, vx +0.61, thrx@-0.10s -0.55, thrx +0.52 |
| 0.05 | 23 | 0.071 | vx@-0.10s -1.17, vx +0.70, vz@-0.10s +0.61, vx@-0.25s +0.47, thrx@-0.10s -0.42, thrx +0.40 |
| 0.10 | 10 | 0.063 | vx@-0.10s -0.70, vy@-0.10s -0.40, vx +0.40, vy +0.36, T_sp -0.33, vx@-0.25s +0.30 |
| 0.20 | 5 | 0.042 | vy@-0.10s -0.36, vy +0.35, vx@-0.10s -0.34, vx@-0.25s +0.31, thry -0.24 |
| 0.30 | 2 | 0.006 | vx@-0.10s -0.43, vx@-0.25s +0.40 |

LASSO entry order: T_sp, thry, thry@-0.50s, thrx, vy, thry@-0.25s, vy@-0.25s, vy@-0.10s, pwm3, thrx@-0.50s, wq, pwm0

| MI rank | feature | MI (bits) |
|---|---|---|
| 1 | thrz | 0.040 |
| 2 | T_sp | 0.027 |
| 3 | pwm_sum | 0.027 |
| 4 | thry | 0.018 |
| 5 | vz@-0.50s | 0.015 |
| 6 | vx@-0.50s | 0.014 |
| 7 | thry@-0.10s | 0.014 |
| 8 | thrx@-0.50s | 0.013 |
| 9 | thrx@-0.25s | 0.013 |
| 10 | thrx | 0.012 |

| base signal | best lag (s) | r | best causal lag (s) | r causal |
|---|---|---|---|---|
| thry | -0.35 | -0.17 | 0.00 | -0.13 |
| T_sp | -0.05 | -0.12 | 0.00 | -0.12 |
| pwm_sum | -0.10 | -0.12 | 0.00 | -0.11 |
| thrx | -0.20 | -0.07 | 0.00 | -0.07 |
| vz | -0.60 | -0.07 | 1.00 | +0.07 |
| thrz | +1.00 | +0.06 | 1.00 | +0.06 |
| pwm3 | -0.20 | +0.08 | 0.00 | +0.06 |
| wp | -0.15 | +0.10 | 0.05 | +0.05 |
| vx | -0.95 | -0.09 | 0.00 | -0.05 |
| vy | -0.70 | -0.10 | 0.00 | +0.05 |
| pwm0 | -0.05 | -0.11 | 0.15 | -0.04 |
| pwm2 | -0.90 | -0.06 | 0.00 | -0.03 |
| wr | -0.05 | +0.07 | 0.20 | +0.03 |
| wq | -0.35 | +0.02 | 0.00 | -0.02 |
| pwm1 | -0.05 | +0.08 | 1.00 | +0.01 |

## z (150608 samples, 33 candidates; all-feature R^2 0.80)

Consensus (in the top 5 of at least 3 of the 5 methods): **vz@-0.50s** (forward, lasso, mi), **pwm1** (lasso, mi, lag), **pwm2** (lasso, mi, lag)

| step | forward selection: + feature | LOSO R^2 |
|---|---|---|
| 1 | vz@-0.50s | 0.457 |
| 2 | thrz | 0.558 |
| 3 | wq | 0.596 |
| 4 | vx@-0.10s | 0.612 |
| 5 | |v|vz | 0.616 |
| 6 | T_sp | 0.680 |
| 7 | pwm_sum | 0.733 |
| 8 | vz | 0.748 |

| SINDy threshold | terms | LOSO R^2 | largest terms (standardised coef) |
|---|---|---|---|
| 0.01 | 26 | 0.795 | vz@-0.10s -7.42, vz +6.88, vx@-0.10s -4.11, vx +2.47, vx@-0.25s +1.66, T_sp -1.33 |
| 0.02 | 20 | 0.795 | vz@-0.10s -7.40, vz +6.87, vx@-0.10s -4.02, vx +2.41, vx@-0.25s +1.63, T_sp -1.33 |
| 0.05 | 16 | 0.792 | vz@-0.10s -7.82, vz +7.12, vx@-0.10s -3.61, vx +2.16, vx@-0.25s +1.54, T_sp -1.30 |
| 0.10 | 14 | 0.790 | vz@-0.10s -7.65, vz +7.14, vx@-0.10s -4.05, vx +2.21, vx@-0.25s +2.18, T_sp -1.31 |
| 0.20 | 11 | 0.786 | vz@-0.10s -6.86, vz +6.25, vx@-0.10s -4.40, vx +2.66, vx@-0.25s +1.71, T_sp -1.30 |
| 0.30 | 9 | 0.790 | vz@-0.10s -6.42, vz +6.16, vx@-0.10s -4.93, vx +2.97, vx@-0.25s +1.93, T_sp -1.28 |

LASSO entry order: vz@-0.50s, pwm1, thrz, pwm2, wq, thrx, vx@-0.10s, T_sp, |v|vz, vz, pwm_sum, vy

| MI rank | feature | MI (bits) |
|---|---|---|
| 1 | vz@-0.50s | 0.485 |
| 2 | pwm2 | 0.337 |
| 3 | pwm1 | 0.335 |
| 4 | vx@-0.50s | 0.325 |
| 5 | vz@-0.25s | 0.309 |
| 6 | vx@-0.25s | 0.258 |
| 7 | thrz | 0.241 |
| 8 | thrx | 0.224 |
| 9 | vx@-0.10s | 0.203 |
| 10 | |v|vx | 0.184 |

| base signal | best lag (s) | r | best causal lag (s) | r causal |
|---|---|---|---|---|
| vz | +0.55 | -0.69 | 0.55 | -0.69 |
| pwm1 | -0.10 | +0.59 | 0.00 | +0.59 |
| pwm2 | +0.00 | -0.58 | 0.00 | -0.58 |
| pwm_sum | +1.00 | -0.56 | 1.00 | -0.56 |
| T_sp | +1.00 | -0.55 | 1.00 | -0.55 |
| thrz | -0.25 | +0.59 | 0.00 | +0.49 |
| pwm0 | +0.05 | -0.47 | 0.05 | -0.47 |
| thrx | -0.20 | -0.53 | 0.00 | -0.46 |
| pwm3 | +0.05 | +0.44 | 0.05 | +0.44 |
| wq | +0.25 | -0.37 | 0.25 | -0.37 |
| wr | +0.45 | -0.21 | 0.45 | -0.21 |
| vx | -0.65 | -0.26 | 0.00 | -0.15 |
| thry | -0.35 | -0.17 | 0.00 | -0.13 |
| vy | -1.00 | -0.10 | 1.00 | +0.08 |
| wp | -0.55 | -0.05 | 0.45 | +0.04 |

