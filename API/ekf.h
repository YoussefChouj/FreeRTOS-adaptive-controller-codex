/**
 * @file     ekf.h
 * @brief    9-state body-frame Extended Kalman Filter — ADR-0011 parallel estimator.
 *
 * State vector: [v_body[3], b_a_body[3], b_g_body[3]] — 9 states.
 * All matrices stored as flat row-major float arrays.
 *
 * Reference: sim/ekf.py (golden implementation, 108 tests pass).
 * Design: ADR-0011 §"Parallel estimator — 9-state body-frame EKF".
 *
 * Pure C99, no malloc. Compatible with Keil ARMCC.
 */
#ifndef __EKF_H__
#define __EKF_H__

#include "global_declare.h"

/** 9-state body-frame EKF. */
typedef struct {
    /* state — 9 floats; layout: [v_body(3), b_a_body(3), b_g_body(3)] */
    float x[9];
    /* covariance — 9x9 row-major; diagonals dominate in this plant */
    float P[81];
    /* Q diagonals: [Q_v(3), Q_ba(3), Q_bg(3)] */
    float Q_diag[9];
    float R_of;       /* 6.16e-4  m^2/s^2 — OF velocity measurement */
    float R_acc;      /* 0.005    m^2/s^4 — body-lin-acc measurement */
    float R_z;        /* 0.04     m^2/s^2 — Z-rate measurement */
    float nis;        /* last Normalized Innovation Squared */
    float k_last[3];  /* last K[0..2] (first column, x-axis gains) */
    uint8_t active;   /* 0=no-op, 1=run (Ekf9_Init sets from param) */
    /* working buffers (private) */
    float S[9];       /* HPH^T: 2x2 (OF/acc) or 1x1 (Z-rate) */
    float K[27];      /* Kalman gain: 9x3 max (col 0=OF-x, col 1=OF-y, col 2=Z) */
} Ekf9_t;

/** Zero-init and set Q/R/defaults. active=0 means every function is a no-op. */
extern void Ekf9_Init(Ekf9_t *e, uint8_t active);

/** Predict step — constant-velocity model, biases are random-walk.
 *  a_body_* : specific force in body frame, mg (divide by 1000 for m/s^2)
 *  gyro_*   : angular rate in body frame, rad/s
 *  dt       : step size, seconds */
extern void Ekf9_Predict(Ekf9_t *e,
                         float a_body_x, float a_body_y, float a_body_z,
                         float gyro_x,   float gyro_y,   float gyro_z,
                         float dt);

/** Optical-flow velocity measurement update (body XY).
 *  of_x, of_y: body-frame velocity, m/s */
extern void Ekf9_UpdateOf(Ekf9_t *e, float of_x, float of_y);

/** Body-XY VELOCITY measurement update (m/s) — MISNAMED, see warning.
 *
 *  WARNING: do NOT pass linear acceleration. H selects v_body[0..1], so the
 *  arguments must be a VELOCITY in m/s. Passing lin_acc makes the innovation
 *  (acceleration - velocity), a unit error, and double-counts the accelerometer
 *  that already drives Ekf9_Predict — measured on hardware 2026-07-26, that let
 *  b_a converge onto raw Acc_*_Real (ratio 1.000) so the predict step contributed
 *  nothing. The only valid use is (0,0): a zero-velocity update when stationary.
 *  No production call site uses this; TASK/send_data.c deliberately does not. */
extern void Ekf9_UpdateAccXY(Ekf9_t *e, float lin_acc_x, float lin_acc_y);

/** Z-rate (altitude derivative) measurement update (scalar).
 *  z_rate: vertical velocity, m/s (positive = climbing) */
extern void Ekf9_UpdateZRate(Ekf9_t *e, float z_rate);

#endif /* __EKF_H__ */
