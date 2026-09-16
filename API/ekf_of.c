/**
 * @file     ekf_of.c
 * @brief    6-state body-frame OF position + velocity-bias Kalman Filter.
 *
 * State:   x[0]=pos_x  x[1]=vel_x  x[2]=bias_x
 *          x[3]=pos_y  x[4]=vel_y  x[5]=bias_y
 *
 * State transition (discrete, dt seconds):
 *   pos_x  += vel_x  * dt     (bias: random walk, x[2] += 0)
 *   vel_x  += w_vel         (zero-mean white noise, var = Q_vel)
 *   bias_x += w_bias        (random walk, var = Q_bias)
 *   (same for Y axis)
 *
 * Measurement model (two scalar measurements, sequential update):
 *   z_x = of_x = vel_x - bias_x + v_meas   (H_x = [0, 1, -1, 0, 0, 0])
 *   z_y = of_y = vel_y - bias_y + v_meas   (H_y = [0, 0, 0, 0, 1, -1])
 *
 * Innovation:
 *   innov_x = of_x - (vel_x - bias_x)
 *     = (true_vel + true_bias + n) - (vel_est - bias_est)
 *     = vel_innov - bias_innov
 *   At hover (true_vel=0): innov_x ≈ -bias_true → filter learns bias
 *   At motion (true_vel>>bias): innov_x ≈ vel_innov → filter learns velocity
 *   The Kalman gain distributes trust based on P covariance.
 *
 * Pure C99, no malloc. Compatible with Keil ARMCC.
 */

#include "ekf_of.h"

/* Compiler macro: access P[row*6 + col] */
#define P(r,c)  e->P[(r)*6+(c)]

/* ------------------------------------------------------------------ */
/* Init                                                               */
/* ------------------------------------------------------------------ */

void EkfOf_Init(EkfOf_t *e)
{
    int i;
    for (i = 0; i < 6; i++) e->x[i] = 0.0f;

    /* Initial covariance:
     * pos: starts uncertain (1 m^2 — we don't know where we are)
     * vel: medium uncertainty (0.1 (m/s)^2 — ~0.3 m/s RMS)
     * bias: starts confident, will grow as filter learns
     * Cross-axis covariance = 0 */
    for (i = 0; i < 36; i++) e->P[i] = 0.0f;
    P(0,0) = 1.0f;   /* pos_x var = 1 m^2 */
    P(1,1) = 0.1f;   /* vel_x var = 0.1 (m/s)^2 */
    P(2,2) = 0.01f;  /* bias_x var = 0.01 (m/s)^2 */
    P(3,3) = 1.0f;
    P(4,4) = 0.1f;
    P(5,5) = 0.01f;

    /* Process noise per second, discrete added as Q*dt */
    e->Q_pos  = 1e-6f;   /* m^2/s  — position driven by vel only */
    e->Q_vel  = 2e-4f;   /* (m/s)^2/s — ~0.014 m/s/sqrt(s) spectral density */
    e->Q_bias = 5e-5f;   /* (m/s)^2/s — ~0.007 m/s/sqrt(s) spectral density */
    e->R_of   = 6.16e-4f; /* (m/s)^2 — OF velocity noise (~2.5 cm/s RMS) */

    e->innov_x = 0.0f;
    e->innov_y = 0.0f;
    e->inited  = 1U;
}

/* ------------------------------------------------------------------ */
/* Predict                                                            */
/* ------------------------------------------------------------------ */

/* Discrete F and Q:
 *   F = |1 dt 0 0  0  0|
 *       |0  1 0 0  0  0|
 *       |0  0 1 0  0  0|
 *       |0  0 0 1 dt 0|
 *       |0  0 0 0  1 0|
 *       |0  0 0 0  0 1|
 *
 *   Q_diag = [Q_pos*dt, Q_vel*dt, Q_bias*dt, Q_pos*dt, Q_vel*dt, Q_bias*dt]
 */
void EkfOf_Predict(EkfOf_t *e, float dt)
{
    float Qp = e->Q_pos  * dt;
    float Qv = e->Q_vel  * dt;
    float Qb = e->Q_bias * dt;
    float P01, P11, P21;

    /* State prediction */
    e->x[0] += e->x[1] * dt;   /* pos_x += vel_x * dt */
    e->x[3] += e->x[4] * dt;   /* pos_y += vel_y * dt */

    /* Covariance: P_pred = F * P * F' + Q
     * F has: F[0,1]=dt, F[1,1]=1, F[2,2]=1, F[3,4]=dt, F[4,4]=1, F[5,5]=1
     *
     * X-axis (rows 0-2):
     *   P00_new = P00 + 2*dt*P01 + dt^2*P11 + Qp
     *   P01_new = P01 + dt*P11
     *   P02_new = P02 + dt*P12
     *   P11_new = P11 + Qv
     *   P12_new = P12
     *   P22_new = P22 + Qb
     * Y-axis (rows 3-5): same pattern */
    P01 = P(0,1); P11 = P(1,1); P21 = P(2,1);

    P(0,0) = P(0,0) + 2.0f * dt * P01 + dt * dt * P11 + Qp;
    P(0,1) = P01 + dt * P11;
    P(0,2) = P(0,2) + dt * P(1,2);
    P(1,1) = P11 + Qv;
    P(1,2) = P(1,2);
    P(2,2) = P(2,2) + Qb;

    /* Copy X cross-corr to symmetric positions */
    P(1,0) = P(0,1);
    P(2,0) = P(0,2);
    P(2,1) = P(1,2);

    /* Y-axis (rows 3-5) */
    P01 = P(3,4); P11 = P(4,4); P21 = P(5,4);
    P(3,3) = P(3,3) + 2.0f * dt * P01 + dt * dt * P11 + Qp;
    P(3,4) = P01 + dt * P11;
    P(3,5) = P(3,5) + dt * P(4,5);
    P(4,4) = P11 + Qv;
    P(4,5) = P(4,5);
    P(5,5) = P(5,5) + Qb;

    /* Symmetric Y positions */
    P(4,3) = P(3,4);
    P(5,3) = P(3,5);
    P(5,4) = P(4,5);

    /* Cross-axis (X vs Y): no coupling in F, only Q cross-corr = 0 */
    /* All P[0..2][3..5] and P[3..5][0..2] stay zero */
}

/* ------------------------------------------------------------------ */
/* Update — scalar sequential (Joseph form, numerically stable)        */
/* ------------------------------------------------------------------ */

/* For a scalar measurement z = H*x + v with H = [0, 1, -1, 0, 0, 0]:
 *   S   = H*P*H' + R = P11 - 2*P12 + P22 + R     (scalar)
 *   K   = P*H' / S                                 (6-vector)
 *   x   = x + K * (z - H*x)
 *   P   = (I - K*H) * P * (I - K*H)' + K*R*K'   (Joseph form)
 *
 * Joseph form for scalar H:
 *   P_new = P - K*[P11-P12, P12-P22, ...]*H*P - H'*[...] + K*R*K'
 *
 * For simplicity and correctness, use the standard symmetric update:
 *   P_new = P - K * H * P - P * H' * K' + K * S * K'
 *   where S = H*P*H' + R
 *
 * Expanded for H = [h0,h1,h2,h3,h4,h5]:
 *   P_new[i][j] = P[i][j] - K[i]*S_ij
 *   where S_ij = h0*P0j + h1*P1j + ... + h5*P5j = sum_k hk * P[k][j]
 *
 * For our H (all zeros except h1=1, h2=-1):
 *   S_ij = 1*P[1][j] - 1*P[2][j]
 *
 * Simplified Joseph update:
 *   P_new[i][j] = P[i][j] - K[i]*(P[1][j] - P[2][j]) - K[j]*(P[1][i] - P[2][i]) + K[i]*K[j]*S
 */

static void ekf_of_update_one(EkfOf_t *e, uint8_t vel_idx, uint8_t bias_idx,
                               float of_meas, float *innov_out)
{
    float PHt[6];
    float S, K[6];
    float y;
    int i, j;

    /* PHt = P * H' where H[vel]=1, H[bias]=-1, rest=0 */
    for (i = 0; i < 6; i++) {
        PHt[i] = e->P[i*6 + vel_idx] - e->P[i*6 + bias_idx];
    }

    /* S = H*PHt + R = (P[vel][vel] - P[vel][bias]) - (P[bias][vel] - P[bias][bias]) + R
     *       = P[vel][vel] - 2*P[vel][bias] + P[bias][bias] + R */
    S = e->P[vel_idx*6 + vel_idx]
      - 2.0f * e->P[vel_idx*6 + bias_idx]
      + e->P[bias_idx*6 + bias_idx]
      + e->R_of;

    if (S < 1e-8f) S = 1e-8f;

    /* Innovation */
    y = of_meas - (e->x[vel_idx] - e->x[bias_idx]);
    if (innov_out) *innov_out = y;

    /* K = PHt / S */
    for (i = 0; i < 6; i++) K[i] = PHt[i] / S;

    /* x += K*y */
    for (i = 0; i < 6; i++) e->x[i] += K[i] * y;

    /* P = (I - K*H) * P * (I - K*H)' + K*R*K'  (Joseph form)
     * Using: P_new = P - K*PHt' - PHt*K' + K*S*K' */
    for (i = 0; i < 6; i++) {
        for (j = i; j < 6; j++) {
            e->P[i*6+j] -= K[i]*PHt[j] + K[j]*PHt[i] - K[i]*K[j]*S;
            e->P[j*6+i]  = e->P[i*6+j];  /* enforce symmetry */
        }
    }
}

void EkfOf_Update(EkfOf_t *e, float of_x, float of_y)
{
    if (!e->inited) return;
    ekf_of_update_one(e, 1, 2, of_x, &e->innov_x);  /* vel_x, bias_x */
    ekf_of_update_one(e, 4, 5, of_y, &e->innov_y);  /* vel_y, bias_y */
}

/* ------------------------------------------------------------------ */
/* Reset position (on Reset_World_Origin)                            */
/* ------------------------------------------------------------------ */

void EkfOf_ResetPos(EkfOf_t *e)
{
    if (!e->inited) return;
    e->x[0] = 0.0f;  /* pos_x */
    e->x[3] = 0.0f;  /* pos_y */
    /* vel and bias states stay — only accumulated position resets */
}

#undef P
