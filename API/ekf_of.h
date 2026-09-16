/**
 * @file     ekf_of.h
 * @brief    6-state body-frame OF position + velocity-bias Kalman Filter.
 *
 * State vector: [pos_x, vel_x, bias_x, pos_y, vel_y, bias_y] — 6 states.
 * All matrices stored as flat row-major float arrays.
 *
 * Model:
 *   - Constant-velocity motion model with small process noise on velocity
 *   - Bias is a random walk (random-walk process noise)
 *   - Measurement: debiased OF body-frame velocity (m/s) per axis
 *
 * Unlike the 9-state IMU-centric EKF (ekf.h), this KF directly models
 * OF velocity bias as an explicit state, making the position output
 * (which feeds Ctrler.locx/yPID.FB) debiased by construction.
 *
 * Pure C99, no malloc. Compatible with Keil ARMCC.
 */
#ifndef __EKF_OF_H__
#define __EKF_OF_H__

#include "global_declare.h"

/** 6-state OF position+bias EKF. */
typedef struct {
    /* state — 6 floats: [pos_x, vel_x, bias_x, pos_y, vel_y, bias_y] */
    float x[6];
    /* covariance — 6x6 row-major */
    float P[36];
    /* Q diagonals */
    float Q_pos;   /* position process noise (very small — position is driven by vel) */
    float Q_vel;   /* velocity process noise (m/s^2 per sqrt(s)) */
    float Q_bias;  /* bias random-walk process noise (bias/sqrt(s)) */
    /* R */
    float R_of;    /* OF velocity measurement noise (m/s)^2 */
    /* Innovation */
    float innov_x; /* last innovation x-axis */
    float innov_y; /* last innovation y-axis */
    uint8_t inited;
} EkfOf_t;

/** Call once at boot or after Reset_World_Origin. */
extern void EkfOf_Init(EkfOf_t *e);

/** Predict step — constant-velocity model, bias is random walk.
 *  dt: step size, seconds. */
extern void EkfOf_Predict(EkfOf_t *e, float dt);

/** Optical-flow velocity measurement update (body XY).
 *  of_x, of_y: raw OF body-frame velocity, m/s.
 *  Subtracts the bias estimate internally (H selects vel - bias). */
extern void EkfOf_Update(EkfOf_t *e, float of_x, float of_y);

/** Reset position states to zero (call on Reset_World_Origin).
 *  Keeps vel and bias estimates intact — only the accumulated
 *  position jumps to zero. */
extern void EkfOf_ResetPos(EkfOf_t *e);

#endif /* __EKF_OF_H__ */
