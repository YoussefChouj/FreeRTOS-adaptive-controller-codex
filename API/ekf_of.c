/**
 * @file     ekf_of.c
 * @brief    8-state body-frame OF position + velocity-bias + accel-bias Kalman Filter.
 *
 * State:   x[0..3] = X axis: [pos_x, vel_x, bof_x, ba_x]
 *          x[4..7] = Y axis: [pos_y, vel_y, bof_y, ba_y]
 *          Actually, the struct uses:
 *          x[0..7] = [px, vx, bof_x, py, vy, bof_y, ba_x, ba_y]
 *          X axis indices: 0, 1, 2, 6
 *          Y axis indices: 3, 4, 5, 7
 *
 * State transition (discrete, dt seconds):
 *   u = a - ba
 *   p += v*dt + 0.5*u*dt^2
 *   v += u*dt
 *   bof, ba: random walks
 *
 * Pure C99, no malloc. Compatible with Keil ARMCC.
 */

#include "ekf_of.h"

/* axis index table */
static const uint8_t k_axis_idx[2][4] = {{0,1,2,6},{3,4,5,7}};

/* ------------------------------------------------------------------ */
/* Init                                                               */
/* ------------------------------------------------------------------ */

#define EKF_OF_NOISE_ROW(qp, qa, qbof, qba, rof, rzupt) \
    do { \
        e->q_pos  = (qp); \
        e->q_acc  = (qa); \
        e->q_bof  = (qbof); \
        e->q_ba   = (qba); \
        e->R_of   = (rof); \
        e->R_zupt = (rzupt); \
    } while(0)

void EkfOf_Init(EkfOf_t *e)
{
    int i, j;
    for (i = 0; i < 8; i++) e->x[i] = 0.0f;

    for (i = 0; i < 2; i++) {
        for (j = 0; j < 16; j++) {
            e->P[i][j] = 0.0f;
        }
        e->P[i][0*4+0] = 1.0f;    /* pos */
        e->P[i][1*4+1] = 0.1f;    /* vel */
        e->P[i][2*4+2] = 0.01f;   /* bof */
        e->P[i][3*4+3] = 0.25f;   /* ba */
    }

    EKF_OF_NOISE_ROW(1e-6f, 1e-2f, 1e-6f, 1e-5f, 6.16e-4f, 1e-4f);

    e->innov_x = 0.0f;
    e->innov_y = 0.0f;
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

        float P00 = e->P[axis][0], P01 = e->P[axis][1], P02 = e->P[axis][2], P03 = e->P[axis][3];
        float P10 = e->P[axis][4], P11 = e->P[axis][5], P12 = e->P[axis][6], P13 = e->P[axis][7];
        float P20 = e->P[axis][8], P21 = e->P[axis][9], P22 = e->P[axis][10], P23 = e->P[axis][11];
        float P30 = e->P[axis][12], P31 = e->P[axis][13], P32 = e->P[axis][14], P33 = e->P[axis][15];

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
        float P[4][4] = {
            {P00, P01, P02, P03},
            {P10, P11, P12, P13},
            {P20, P21, P22, P23},
            {P30, P31, P32, P33}
        };
        float FP[4][4];
        float FPFt[4][4];
        int i, j, k;

        for (i = 0; i < 4; i++) {
            for (j = 0; j < 4; j++) {
                FP[i][j] = 0.0f;
                for (k = 0; k < 4; k++) {
                    FP[i][j] += F[i][k] * P[k][j];
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

static void ekf_of_update_one(EkfOf_t *e, int axis, const float h[4], float z, float R, float *innov_out)
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
        *innov_out = y;
    }

    for (i = 0; i < 4; i++) K[i] = PHt[i] / S;

    for (i = 0; i < 4; i++) e->x[idx[i]] += K[i] * y;

    /* P = P - K*PHt' - PHt*K' + K*S*K' */
    for (i = 0; i < 4; i++) {
        for (j = i; j < 4; j++) {
            e->P[axis][i*4+j] -= K[i]*PHt[j] + K[j]*PHt[i] - K[i]*K[j]*S;
            e->P[axis][j*4+i] = e->P[axis][i*4+j];  /* enforce symmetry */
        }
    }
}

void EkfOf_Update(EkfOf_t *e, float of_x, float of_y)
{
    if (!e->inited) return;
    const float h[4] = {0.0f, 1.0f, 1.0f, 0.0f};
    ekf_of_update_one(e, 0, h, of_x, e->R_of, &e->innov_x);
    ekf_of_update_one(e, 1, h, of_y, e->R_of, &e->innov_y);
}

void EkfOf_UpdateZeroVel(EkfOf_t *e)
{
    if (!e->inited) return;
    const float h[4] = {0.0f, 1.0f, 0.0f, 0.0f};
    ekf_of_update_one(e, 0, h, 0.0f, e->R_zupt, 0);
    ekf_of_update_one(e, 1, h, 0.0f, e->R_zupt, 0);
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
