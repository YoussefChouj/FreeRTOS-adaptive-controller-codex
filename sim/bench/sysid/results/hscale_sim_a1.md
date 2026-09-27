# Multiscale H-scale sysID Protocol Results (Simulation - Amendment A1)

**Verdict:** `SUPPORTED`  
**Runtime:** 574.1 s  

## Summary of Pre-registered Conditions

| Condition | Requirement | Roll | Pitch | Yaw | Result |
|---|---|---|---|---|---|
| (a) Scale Separation | Mean Jaccard < 0.5 | 0.524 | 0.335 | 0.421 | **PASS** |
| (b) Multiscale Accuracy | Band-sum / Global NRMSE <= 0.90 | 0.446 | 0.437 | 0.152 | **PASS** |

## Jaccard Similarity Matrix (Band Feature Sets)

| Axis | J(L, M) | J(L, H) | J(M, H) | Mean Pairwise Jaccard |
|---|---|---|---|---|
| Roll | 0.571 | 0.500 | 0.500 | 0.524 |
| Pitch | 0.273 | 0.357 | 0.375 | 0.335 |
| Yaw | 0.333 | 0.500 | 0.429 | 0.421 |

## Prediction Accuracy (NRMSE on id_val and id_traj)

| Axis | id_val Global | id_val Band-Sum | id_val Ratio | id_traj Global | id_traj Band-Sum | id_traj Ratio |
|---|---|---|---|---|---|---|
| Roll | 0.1831 | 0.0817 | **0.446** | 0.1924 | 0.0829 | 0.431 |
| Pitch | 0.1947 | 0.0851 | **0.437** | 0.1838 | 0.0892 | 0.485 |
| Yaw | 0.6077 | 0.0924 | **0.152** | 0.5862 | 0.1027 | 0.175 |

## Selected Features (Inclusion Probability >= 0.6) and Band Sparsity

| Axis | Band | Threshold | K | n_selected | selected / K | Non-sparse Flag | Selected Features (Pooled) | Universal Features |
|---|---|---|---|---|---|---|---|---|
| roll | L | 2.2663e-01 | 33 | 7 | 0.212 | False | u_x, u_y, u_z, u_lag_x, u_lag_y, u_lag_z, sin_roll | u_x |
| roll | M | 3.8197e-01 | 33 | 4 | 0.121 | False | u_x, u_y, u_lag_x, u_lag_y | u_lag_x, u_lag_y, u_x |
| roll | H | 1.5644e-01 | 33 | 8 | 0.242 | False | w_x, u_x, u_y, u_lag_x, u_lag_y, v_by, sin_roll, sin_pitch | sin_roll, u_lag_x, u_x |
| roll | global | 1.8892e+00 | 33 | 3 | 0.091 | False | u_x, u_lag_x, u_lag_y | u_lag_x, u_x |
| pitch | L | 4.2100e-02 | 33 | 11 | 0.333 | False | bias, w_x, u_x, u_y, u_z, u_lag_x, u_lag_y, u_lag_z, v_bx, v_by, sin_pitch | u_y |
| pitch | M | 4.5083e-01 | 33 | 3 | 0.091 | False | u_y, u_lag_x, u_lag_y | u_lag_x, u_lag_y, u_y |
| pitch | H | 1.9362e-01 | 33 | 8 | 0.242 | False | w_y, w_z*w_x, u_y, u_lag_x, u_lag_y, v_bx, sin_roll, sin_pitch | sin_pitch, sin_roll, u_lag_x, u_lag_y, u_y, w_z*w_x |
| pitch | global | 6.5825e-01 | 33 | 4 | 0.121 | False | u_x, u_y, u_lag_x, u_lag_y | u_lag_x, u_lag_y, u_y |
| yaw | L | 2.3239e+00 | 33 | 2 | 0.061 | False | u_z, u_lag_z | u_z |
| yaw | M | 3.3725e-01 | 33 | 6 | 0.182 | False | w_z, u_z, u_lag_z, thrust, thrust^2, ge_term | thrust, thrust^2, u_lag_z, u_z |
| yaw | H | 2.8108e-01 | 33 | 4 | 0.121 | False | u_z, u_lag_z, v_bz, ge_term | u_lag_z, u_z |
| yaw | global | 1.9980e+00 | 33 | 7 | 0.212 | False | bias, w_z, u_z, u_lag_z, thrust, thrust^2, v_sq | bias, u_lag_z, u_z, w_z |
