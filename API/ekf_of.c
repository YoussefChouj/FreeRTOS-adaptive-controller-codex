/**
 * @file     ekf_of.c
 * @brief    8-state body-frame OF position + velocity-bias + accel-bias Kalman Filter.
 *
 * State:
 *   x[0..7] = [px, vx, bof_x, py, vy, bof_y, ba_x, ba_y]
 *
 * Axis index table:
 *   X axis indices: 0, 1, 2, 6
 *   Y axis indices: 3, 4, 5, 7
 *
 * Predict (discrete, dt seconds):
 *   u = a - ba
 *   p += v*dt + 0.5*u*dt^2
 *   v += u*dt
 *   bof, ba: random walks
 *
 * Measurements:
 *   OF:   h = [0, 1, 1, 0], gated at of_gate sigmas (innov^2 > gate^2*S -> skip, rej_x/rej_y++)
 *   ZUPT: h = [0, 1, 0, 0]
 *
 * Units: meters, seconds
 * Pure C99, no malloc. Compatible with Keil ARMCC.
 */

#include "ekf_of.h"

/* axis index table */
static const uint8_t k_axis_idx[2][4] = {{0,1,2,6},{3,4,5,7}};

/* ------------------------------------------------------------------ */
/* Init                                                               */
/* ------------------------------------------------------------------ */

#define EKF_OF_STATE(i, q_field, q, P0)  e->q_field = (q); e->P[0][(i)*5] = (P0); e->P[1][(i)*5] = (P0)

void EkfOf_Init(EkfOf_t *e)
{
    int i, j;
    for (i = 0; i < 8; i++) e->x[i] = 0.0f;

    for (i = 0; i < 2; i++) {
        for (j = 0; j < 16; j++) {
            e->P[i][j] = 0.0f;
        }
    }

    /* q = random-walk/white-noise density per second, units per state; P0 = initial variance */
    EKF_OF_STATE(0, q_pos, 1e-6f, 1.0f);   /* p    position */
    EKF_OF_STATE(1, q_acc, 1e-3f, 0.1f);   /* v    velocity */
    EKF_OF_STATE(2, q_bof, 0.0f,  0.01f);  /* bof  optical flow bias: frozen, see change history */
    EKF_OF_STATE(3, q_ba,  1e-6f, 0.25f);  /* ba   accel bias */

    /* Measurement noise variances */
    e->R_of   = 1e-4f;
    e->R_zupt = 1e-4f;
    e->of_gate = 5.0f;   /* sigmas; armed flights auto_landing_1/hover_7/ekf1 peak at 4.5 sigma */

    /* Change history (newest first)
     * 2026-10-03 flight_test_drift_fix_1: q_bof 0 and bof variance 0 at ARM (EKF_OF_BOF_ARM_VAR),
     *   so bof stays 0 in flight. Its only remaining learning was the first 0.1 s of OF at
     *   lift-off, where the real sideways slide (-4..-6 cm/s) was taken as bias (bof_y -1.6 cm/s
     *   in F4), then flown as zero for the whole hover: the later-flight +x drift.
     * 2026-10-02 WP-21: OF innovation gate 5 sigma (S_ss 1.56e-4 at 50 Hz OF, so ~6.2 cm/s)
     * 2026-10-02 WP-14: tilt-only input; replay CHOSEN q_acc 1e-3, q_bof 1e-6, q_ba 1e-6, R_of 1e-4, R_zupt 1e-4
     * 2026-10-01 WP-8: 8-state model, q_vel->q_acc, ZUPT; values provisional
     */

    e->innov_x = 0.0f;
    e->innov_y = 0.0f;
    e->rej_x   = 0U;
    e->rej_y   = 0U;
    e->inited  = 1U;
}

/* ------------------------------------------------------------------ */
/* Predict                                                            */
/* ------------------------------------------------------------------ */

void EkfOf_Predict(EkfOf_t *e, float dt, float ax, float ay)
{
    if (!e->inited) return;
    
    float a_meas[2] = {ax, ay};
    int axis;

    for (axis = 0; axis < 2; axis++) {
        uint8_t i_p = k_axis_idx[axis][0];
        uint8_t i_v = k_axis_idx[axis][1];
        
        uint8_t i_ba = k_axis_idx[axis][3];

        float u = a_meas[axis] - e->x[i_ba];
        e->x[i_p] += e->x[i_v] * dt + 0.5f * u * dt * dt;
        e->x[i_v] += u * dt;

        float dt2 = dt * dt;

        /* F = 
         * [1, dt, 0, -0.5*dt^2]
         * [0, 1,  0, -dt]
         * [0, 0,  1, 0]
         * [0, 0,  0, 1]
         */
        float F[4][4] = {
            {1.0f, dt, 0.0f, -0.5f * dt2},
            {0.0f, 1.0f, 0.0f, -dt},
            {0.0f, 0.0f, 1.0f, 0.0f},
            {0.0f, 0.0f, 0.0f, 1.0f}
        };
        float FP[4][4];
        float FPFt[4][4];
        int i, j, k;

        for (i = 0; i < 4; i++) {
            for (j = 0; j < 4; j++) {
                FP[i][j] = 0.0f;
                for (k = 0; k < 4; k++) {
                    FP[i][j] += F[i][k] * e->P[axis][k*4 + j];
                }
            }
        }
        for (i = 0; i < 4; i++) {
            for (j = 0; j < 4; j++) {
                FPFt[i][j] = 0.0f;
                for (k = 0; k < 4; k++) {
                    FPFt[i][j] += FP[i][k] * F[j][k]; /* F' means transpose F[j][k] */
                }
            }
        }

        e->P[axis][0]  = FPFt[0][0] + e->q_pos * dt;
        e->P[axis][1]  = FPFt[0][1];
        e->P[axis][2]  = FPFt[0][2];
        e->P[axis][3]  = FPFt[0][3];
        e->P[axis][4]  = FPFt[1][0];
        e->P[axis][5]  = FPFt[1][1] + e->q_acc * dt;
        e->P[axis][6]  = FPFt[1][2];
        e->P[axis][7]  = FPFt[1][3];
        e->P[axis][8]  = FPFt[2][0];
        e->P[axis][9]  = FPFt[2][1];
        e->P[axis][10] = FPFt[2][2] + e->q_bof * dt;
        e->P[axis][11] = FPFt[2][3];
        e->P[axis][12] = FPFt[3][0];
        e->P[axis][13] = FPFt[3][1];
        e->P[axis][14] = FPFt[3][2];
        e->P[axis][15] = FPFt[3][3] + e->q_ba * dt;
    }
}

/* ------------------------------------------------------------------ */
/* Update — scalar sequential                                         */
/* ------------------------------------------------------------------ */

/* gate: sigmas, 0 = off. Returns 1 when the sample was rejected (state and P untouched). */
static uint8_t ekf_of_update_one(EkfOf_t *e, int axis, const float h[4], float z, float R, float gate, float *innov_out)
{
    float PHt[4];
    float S = R;
    float K[4];
    float y;
    int i, j;

    uint8_t idx[4];
    idx[0] = k_axis_idx[axis][0];
    idx[1] = k_axis_idx[axis][1];
    idx[2] = k_axis_idx[axis][2];
    idx[3] = k_axis_idx[axis][3];

    for (i = 0; i < 4; i++) {
        PHt[i] = 0.0f;
        for (j = 0; j < 4; j++) {
            PHt[i] += e->P[axis][i*4 + j] * h[j];
        }
    }

    for (i = 0; i < 4; i++) {
        S += h[i] * PHt[i];
    }
    if (S < 1e-8f) S = 1e-8f;

    float hx = 0.0f;
    for (i = 0; i < 4; i++) {
        hx += h[i] * e->x[idx[i]];
    }
    
    y = z - hx;
    if (innov_out) {
        *innov_out = y;   /* raw innovation, logged even when gated so the health monitor still sees it */
    }
    if (gate > 0.0f && y * y > gate * gate * S) return 1U;

    for (i = 0; i < 4; i++) K[i] = PHt[i] / S;

    for (i = 0; i < 4; i++) e->x[idx[i]] += K[i] * y;

    /* P = P - K*PHt' - PHt*K' + K*S*K' */
    for (i = 0; i < 4; i++) {
        for (j = i; j < 4; j++) {
            e->P[axis][i*4+j] -= K[i]*PHt[j] + K[j]*PHt[i] - K[i]*K[j]*S;
            e->P[axis][j*4+i] = e->P[axis][i*4+j];  /* enforce symmetry */
        }
    }
    return 0U;
}

void EkfOf_Update(EkfOf_t *e, float of_x, float of_y)
{
    if (!e->inited) return;
    const float h[4] = {0.0f, 1.0f, 1.0f, 0.0f};
    if (ekf_of_update_one(e, 0, h, of_x, e->R_of, e->of_gate, &e->innov_x)) e->rej_x++;
    if (ekf_of_update_one(e, 1, h, of_y, e->R_of, e->of_gate, &e->innov_y)) e->rej_y++;
}

void EkfOf_UpdateZeroVel(EkfOf_t *e)
{
    if (!e->inited) return;
    const float h[4] = {0.0f, 1.0f, 0.0f, 0.0f};
    (void)ekf_of_update_one(e, 0, h, 0.0f, e->R_zupt, 0.0f, 0);
    (void)ekf_of_update_one(e, 1, h, 0.0f, e->R_zupt, 0.0f, 0);
}

/* ------------------------------------------------------------------ */
/* Reset OF bias to zero with variance var (on ARM / handheld edge)   */
/* ------------------------------------------------------------------ */

void EkfOf_ResetBias(EkfOf_t *e, float var)
{
    int axis, k;
    if (!e->inited) return;
    for (axis = 0; axis < 2; axis++) {
        e->x[k_axis_idx[axis][2]] = 0.0f;
        for (k = 0; k < 4; k++) {          /* local index 2 = bof: clear its row and column */
            e->P[axis][2*4 + k] = 0.0f;
            e->P[axis][k*4 + 2] = 0.0f;
        }
        e->P[axis][2*4 + 2] = var;
    }
}

/* ------------------------------------------------------------------ */
/* Reset position (on Reset_World_Origin)                            */
/* ------------------------------------------------------------------ */

void EkfOf_ResetPos(EkfOf_t *e)
{
    if (!e->inited) return;
    e->x[k_axis_idx[0][0]] = 0.0f;  /* pos_x */
    e->x[k_axis_idx[1][0]] = 0.0f;  /* pos_y */
}
