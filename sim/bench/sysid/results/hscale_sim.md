# Multiscale H-scale sysID Protocol Results (Simulation)

**Verdict:** `KILLED`  
**Runtime:** 1275.2 s  

## Summary of Pre-registered Conditions

| Condition | Requirement | Roll | Pitch | Yaw | Result |
|---|---|---|---|---|---|
| (a) Scale Separation | Mean Jaccard < 0.5 | 0.768 | 0.752 | 0.410 | **FAIL** |
| (b) Multiscale Accuracy | Band-sum / Global NRMSE <= 0.90 | 0.475 | 0.507 | 1.312 | **PASS** |

## Jaccard Similarity Matrix (Band Feature Sets)

| Axis | J(L, M) | J(L, H) | J(M, H) | Mean Pairwise Jaccard |
|---|---|---|---|---|
| Roll | 0.848 | 0.697 | 0.759 | 0.768 |
| Pitch | 0.871 | 0.645 | 0.741 | 0.752 |
| Yaw | 0.250 | 0.281 | 0.700 | 0.410 |

## Prediction Accuracy (NRMSE on id_val and id_traj)

| Axis | id_val Global | id_val Band-Sum | id_val Ratio | id_traj Global | id_traj Band-Sum | id_traj Ratio |
|---|---|---|---|---|---|---|
| Roll | 0.1804 | 0.0856 | **0.475** | 0.1868 | 0.0845 | 0.452 |
| Pitch | 0.1924 | 0.0975 | **0.507** | 0.1854 | 0.0938 | 0.506 |
| Yaw | 0.3427 | 0.4495 | **1.312** | 0.4069 | 0.4513 | 1.109 |

## Selected Features (Inclusion Probability >= 0.6) and Universal Features

| Axis | Band | Selected Features (Pooled) | Universal Features |
|---|---|---|---|
| roll | L | bias, w_x, w_y, w_z, w_x*w_y, w_y*w_z, w_z*w_x, w_x^2, w_y^2, w_z^2, |w_x|w_x, |w_y|w_y, |w_z|w_z, u_x, u_y, u_z, u_lag_x, u_lag_y, u_lag_z, thrust, thrust^2, v_bx, v_by, v_bz, |v_bx|v_bx, |v_by|v_by, |v_bz|v_bz, ge_term, sin_roll, cos_roll, sin_pitch, cos_pitch, v_sq | bias, cos_pitch, cos_roll, ge_term, sin_pitch, sin_roll, thrust, thrust^2, u_lag_x, u_lag_y, u_x, u_y, v_bx, v_by, v_bz, v_sq, w_x, w_x*w_y, w_x^2, w_y, w_y*w_z, w_y^2, w_z, w_z*w_x, |v_bx|v_bx, |v_by|v_by, |v_bz|v_bz, |w_x|w_x, |w_y|w_y |
| roll | M | w_x, w_y, w_z, w_y*w_z, w_z*w_x, w_x^2, w_y^2, |w_x|w_x, |w_y|w_y, |w_z|w_z, u_x, u_y, u_lag_x, u_lag_y, thrust, thrust^2, v_bx, v_by, v_bz, |v_bx|v_bx, |v_by|v_by, |v_bz|v_bz, ge_term, sin_roll, cos_roll, sin_pitch, cos_pitch, v_sq | cos_pitch, cos_roll, ge_term, sin_pitch, sin_roll, thrust, thrust^2, u_lag_x, u_lag_y, u_x, v_bx, v_by, v_bz, v_sq, w_x, w_x^2, w_y, w_y*w_z, w_y^2, w_z, |v_bx|v_bx, |v_by|v_by, |v_bz|v_bz |
| roll | H | w_x, w_y, w_z, w_x*w_y, w_z*w_x, w_y^2, |w_x|w_x, |w_y|w_y, u_x, u_lag_x, thrust, thrust^2, v_bx, v_by, v_bz, |v_bx|v_bx, |v_by|v_by, ge_term, sin_roll, cos_roll, sin_pitch, cos_pitch, v_sq | cos_pitch, cos_roll, ge_term, sin_pitch, sin_roll, thrust, thrust^2, u_x, v_bx, v_by, v_bz, v_sq, w_x, w_y, w_z*w_x, |v_bx|v_bx, |v_by|v_by, |w_x|w_x |
| roll | global | bias, w_x, w_y, w_z, w_x*w_y, w_y*w_z, w_z*w_x, w_x^2, w_y^2, w_z^2, |w_x|w_x, |w_y|w_y, |w_z|w_z, u_x, u_lag_x, u_lag_y, thrust, thrust^2, v_bx, v_by, v_bz, |v_bx|v_bx, |v_by|v_by, |v_bz|v_bz, ge_term, sin_roll, cos_roll, sin_pitch, cos_pitch, v_sq | bias, cos_pitch, cos_roll, ge_term, sin_pitch, sin_roll, thrust, thrust^2, u_x, v_bx, v_by, v_bz, v_sq, w_x, w_y, w_y*w_z, |v_by|v_by, |w_x|w_x |
| pitch | L | bias, w_x, w_y, w_z, w_x*w_y, w_y*w_z, w_z*w_x, w_x^2, w_y^2, w_z^2, |w_x|w_x, |w_y|w_y, |w_z|w_z, u_x, u_y, u_lag_x, u_lag_y, thrust, thrust^2, v_bx, v_by, v_bz, |v_bx|v_bx, |v_by|v_by, |v_bz|v_bz, ge_term, sin_roll, cos_roll, sin_pitch, cos_pitch, v_sq | bias, cos_pitch, cos_roll, ge_term, sin_pitch, sin_roll, thrust, thrust^2, u_lag_y, u_y, v_bx, v_by, v_bz, v_sq, w_x, w_x*w_y, w_x^2, w_y, w_y*w_z, w_y^2, w_z, w_z*w_x, |v_bx|v_bx, |v_by|v_by, |v_bz|v_bz, |w_x|w_x, |w_y|w_y, |w_z|w_z |
| pitch | M | w_x, w_y, w_z, w_y*w_z, w_z*w_x, w_y^2, w_z^2, |w_x|w_x, |w_y|w_y, |w_z|w_z, u_y, u_lag_x, u_lag_y, thrust, thrust^2, v_bx, v_by, v_bz, |v_bx|v_bx, |v_by|v_by, |v_bz|v_bz, ge_term, sin_roll, cos_roll, sin_pitch, cos_pitch, v_sq | cos_pitch, cos_roll, ge_term, sin_pitch, sin_roll, thrust, thrust^2, u_lag_y, u_y, v_bx, v_by, v_bz, v_sq, w_x, w_y, |v_bx|v_bx, |v_by|v_by, |v_bz|v_bz, |w_y|w_y |
| pitch | H | w_x, w_y, w_y*w_z, w_z*w_x, |w_x|w_x, |w_y|w_y, u_y, u_lag_y, thrust, thrust^2, v_bx, v_by, v_bz, |v_bx|v_bx, ge_term, sin_roll, cos_roll, sin_pitch, cos_pitch, v_sq | cos_pitch, cos_roll, ge_term, sin_pitch, sin_roll, thrust, thrust^2, u_y, v_bx, v_by, v_bz, v_sq, w_y, w_y*w_z, w_z*w_x, |v_bx|v_bx, |v_by|v_by |
| pitch | global | bias, w_x, w_y, w_x*w_y, w_y*w_z, w_z*w_x, w_x^2, w_y^2, w_z^2, |w_x|w_x, |w_y|w_y, u_y, u_lag_x, u_lag_y, thrust, thrust^2, v_bx, v_by, v_bz, |v_by|v_by, |v_bz|v_bz, ge_term, sin_roll, cos_roll, sin_pitch, cos_pitch, v_sq | bias, cos_pitch, cos_roll, ge_term, sin_pitch, thrust, thrust^2, u_lag_y, u_y, v_bx, v_by, v_bz, v_sq, w_x, w_x*w_y, w_x^2, w_y, w_y^2, w_z, w_z*w_x, |v_bx|v_bx, |v_by|v_by, |v_bz|v_bz |
| yaw | L | bias, w_x, w_y, w_z, w_x*w_y, w_y*w_z, w_z*w_x, w_x^2, w_y^2, w_z^2, |w_x|w_x, |w_y|w_y, |w_z|w_z, u_x, u_y, u_z, u_lag_x, u_lag_y, u_lag_z, thrust, thrust^2, v_bx, v_by, v_bz, |v_bx|v_bx, |v_bz|v_bz, ge_term, sin_roll, cos_roll, sin_pitch, cos_pitch, v_sq | bias, cos_pitch, cos_roll, ge_term, sin_pitch, thrust, thrust^2, u_z, v_bx, v_by, v_bz, w_x, w_x^2, w_y, w_y*w_z, w_y^2, w_z, w_z*w_x, |v_bx|v_bx, |v_by|v_by, |v_bz|v_bz, |w_y|w_y |
| yaw | M | w_z, thrust, thrust^2, v_bz, ge_term, cos_roll, cos_pitch, v_sq | cos_pitch, cos_roll, ge_term, thrust, thrust^2, v_bz, v_sq |
| yaw | H | w_z, thrust^2, v_bz, |v_bz|v_bz, ge_term, cos_roll, sin_pitch, cos_pitch, v_sq | cos_pitch, cos_roll, ge_term, sin_pitch, sin_roll, v_bz, v_sq, w_z |
| yaw | global | bias, w_z, w_x*w_y, w_y*w_z, w_x^2, w_y^2, w_z^2, |w_z|w_z, u_z, u_lag_z, thrust, thrust^2, v_bx, v_by, v_bz, |v_bx|v_bx, |v_by|v_by, |v_bz|v_bz, ge_term, sin_roll, cos_roll, sin_pitch, cos_pitch, v_sq | bias, cos_pitch, cos_roll, ge_term, sin_pitch, sin_roll, thrust, thrust^2, v_bz, v_sq, w_x, w_x^2, w_y^2, w_z |
