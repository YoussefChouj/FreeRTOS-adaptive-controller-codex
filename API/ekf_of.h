/**
 * @file     ekf_of.h
 * @brief    8-state body-frame OF position + velocity-bias + accel-bias Kalman Filter.
 *
 * State vector: [pos_x, vel_x, bof_x, pos_y, vel_y, bof_y, ba_x, ba_y] — 8 states.
 * All matrices stored as flat row-major float arrays.
 *
 * Model:
 *   - Constant-velocity motion model with accelerometer input
 *   - OF bias (bof) and accelerometer bias (ba) are random walks
 *   - Measurements:
 *       - OF: debiased OF body-frame velocity (m/s) per axis
 *       - ZUPT: zero velocity update on the ground
 *
 * Units:
 *   - pos: m
 *   - vel: m/s
 *   - bof: m/s
 *   - ba: m/s^2
 *
 * Pure C99, no malloc. Compatible with Keil ARMCC.
 */
#ifndef __EKF_OF_H__
#define __EKF_OF_H__

#include <stdint.h>

/** 8-state OF position+bias EKF. */
typedef struct {
    /* state — 8 floats: [pos_x, vel_x, bof_x, pos_y, vel_y, bof_y, ba_x, ba_y] */
    float x[8];
    /* covariance — two 4x4 row-major blocks: P[0] for X axis, P[1] for Y axis */
    float P[2][16];
    /* Q diagonals */
    float q_pos;
    float q_acc;
    float q_bof;
    float q_ba;
    /* R */
    float R_of;
    float R_zupt;
    /* Innovation */
    float innov_x;
    float innov_y;
    uint8_t inited;
} EkfOf_t;

/** Call once at boot or after Reset_World_Origin. */
extern void EkfOf_Init(EkfOf_t *e);

/** Predict step.
 *  dt: step size, seconds.
 *  ax, ay: body accel in OF frame, m/s^2. */
extern void EkfOf_Predict(EkfOf_t *e, float dt, float ax, float ay);

/** Optical-flow velocity measurement update (body XY).
 *  of_x, of_y: raw OF body-frame velocity, m/s.
 *  Subtracts the OF bias estimate internally. */
extern void EkfOf_Update(EkfOf_t *e, float of_x, float of_y);

/** Zero velocity update (on ground). */
extern void EkfOf_UpdateZeroVel(EkfOf_t *e);

/** Reset position states to zero (call on Reset_World_Origin).
 *  Keeps vel and bias estimates intact. */
extern void EkfOf_ResetPos(EkfOf_t *e);

#endif /* __EKF_OF_H__ */
