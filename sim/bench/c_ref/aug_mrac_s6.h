/*
 * aug_mrac_s6.h -- MRAC S6 adaptive augmentation, per-axis, C89 (float32).
 *
 * Mirrors sim_coupled.MRAC (ctrl='S6') step-for-step, as used by
 * ctrl_mrac.MRACBaseController.controller_update() for roll and pitch.
 *
 * Constants from sim_coupled.py:
 *   LAM     = 4.0
 *   SIGMA   = 0.01
 *   OMEGA_U = 25.0
 *   TH_MAX  = 3.0
 *   U_SCALE = 300.0
 *   P_N=5, PHI_N=0.5, UN_N=300, ACC_N=50
 *   DT_C    = 0.005
 *   10 Hz acc_f cutoff (2*pi*10 rad/s)
 *
 * S6 features (normalised):
 *   phi[0] = 1
 *   phi[1] = pm / P_N
 *   phi[2] = (pm/P_N) * tanhf(pm/P_N)
 *   phi[3] = q*r / 0.2             (cross-coupling)
 *   phi[4] = U_pid / UN_N
 *   phi[5] = pmk / P_N             (reference model rate, scalar per tick)
 */
#ifndef AUG_MRAC_S6_H
#define AUG_MRAC_S6_H

#include <math.h>

#define MRAC_S6_NF 6

/* Compile-time constants matching sim_coupled.py */
#define MRAC_LAM      4.0f
#define MRAC_SIGMA    0.01f
#define MRAC_OMEGA_U  25.0f
#define MRAC_TH_MAX   3.0f
#define MRAC_U_SCALE  300.0f
#define MRAC_P_N      5.0f
#define MRAC_PHI_N    0.5f
#define MRAC_UN_N     300.0f
#define MRAC_ACC_N    50.0f
#define MRAC_DT_C     0.005f
#define MRAC_ACC_WC   62.83185307179587f   /* 2*pi*10 */

typedef struct {
    float gamma;     /* adaptation rate */
} AugMracS6Params;

typedef struct {
    float Th[MRAC_S6_NF]; /* parameter vector */
    float u_ad;            /* adaptive correction (filtered) */
    float acc_f;           /* filtered angular acceleration */
    float p_prev;          /* previous rate measurement [rad/s] */
} AugMracS6;

/* Initialise state */
static void aug_mrac_s6_init(AugMracS6 *s, const AugMracS6Params *p)
{
    int i;
    (void)p;
    for (i = 0; i < MRAC_S6_NF; i++) s->Th[i] = 0.0f;
    s->u_ad  = 0.0f;
    s->acc_f = 0.0f;
    s->p_prev = 0.0f;
}

/* Reset */
static void aug_mrac_s6_reset(AugMracS6 *s)
{
    int i;
    for (i = 0; i < MRAC_S6_NF; i++) s->Th[i] = 0.0f;
    s->u_ad  = 0.0f;
    s->acc_f = 0.0f;
    s->p_prev = 0.0f;
}

/*
 * One tick of the MRAC S6 adaptive augmentation.
 *
 * pm_meas  : measured rate for this axis [rad/s]
 * phm_meas : measured angle for this axis [rad]
 * U_pid    : PID nominal output for this axis [firmware U] (u_nom[:,axis])
 * pmk      : reference model rate [rad/s]
 * phmk     : reference model angle [rad]
 * q_cross  : cross-coupling gyro [rad/s] — for roll axis this is pitch rate,
 *            for pitch axis this is roll rate
 * r_cross  : cross-coupling gyro [rad/s] — yaw rate for both axes
 *
 * Returns  : adaptive correction u_ad (caller adds to u_nom).
 *
 * The bench Python adds u_ad to u_nom[:,axis] in controller_update.
 * This function returns only u_ad, not the total.
 */
static float aug_mrac_s6_update(AugMracS6 *s, const AugMracS6Params *p,
                                float pm_meas, float phm_meas,
                                float U_pid, float pmk, float phmk,
                                float q_cross, float r_cross)
{
    float pr, un, pmr, qr;
    float Phi[MRAC_S6_NF];
    float sliding, den, Phi_sq, raw, nrm, scale;
    float th_dot;
    int i;

    /* 1. Update filtered angular acceleration (1st-order LPF, 10 Hz cutoff) */
    /*    acc_f += DT_C * 2*pi*10 * ((pm - p_prev)/DT_C - acc_f)            */
    s->acc_f += MRAC_DT_C * MRAC_ACC_WC
              * ((pm_meas - s->p_prev) / MRAC_DT_C - s->acc_f);

    /* 2. Normalised feature inputs (phr not used by S6, only S10) */
    pr  = pm_meas / MRAC_P_N;
    un  = U_pid / MRAC_UN_N;
    pmr = pmk / MRAC_P_N;
    qr  = q_cross * r_cross / 0.2f;

    /* 3. S6 feature vector */
    Phi[0] = 1.0f;
    Phi[1] = pr;
    Phi[2] = pr * (float)tanh((double)pr);
    Phi[3] = qr;
    Phi[4] = un;
    Phi[5] = pmr;

    /* 4. Sliding surface s = (pm - pmk) + LAM * (phm - phmk) */
    sliding = (pm_meas - pmk) + MRAC_LAM * (phm_meas - phmk);

    /* 5. Normalisation denominator: 1 + sum(Phi^2) */
    Phi_sq = 0.0f;
    for (i = 0; i < MRAC_S6_NF; i++) Phi_sq += Phi[i] * Phi[i];
    den = 1.0f + Phi_sq;

    /* 6. Gradient update: Th += DT * (gamma * Phi * s/den - sigma * Th) */
    for (i = 0; i < MRAC_S6_NF; i++) {
        th_dot = p->gamma * Phi[i] * (sliding / den) - MRAC_SIGMA * s->Th[i];
        s->Th[i] += MRAC_DT_C * th_dot;
    }

    /* 7. Norm clamp: if ||Th|| > TH_MAX, scale down */
    nrm = 0.0f;
    for (i = 0; i < MRAC_S6_NF; i++) nrm += s->Th[i] * s->Th[i];
    nrm = (float)sqrt((double)nrm);
    if (nrm > MRAC_TH_MAX) {
        scale = MRAC_TH_MAX / (nrm + 1e-12f);
        for (i = 0; i < MRAC_S6_NF; i++) s->Th[i] *= scale;
    }

    /* 8. Raw adaptive output: -U_SCALE * dot(Th, Phi) */
    raw = 0.0f;
    for (i = 0; i < MRAC_S6_NF; i++) raw += s->Th[i] * Phi[i];
    raw = -MRAC_U_SCALE * raw;

    /* 9. First-order low-pass on u_ad: u_ad += DT * OMEGA_U * (raw - u_ad) */
    s->u_ad += MRAC_DT_C * MRAC_OMEGA_U * (raw - s->u_ad);

    /* 10. Store previous rate for next tick's acceleration estimate */
    s->p_prev = pm_meas;

    return s->u_ad;
}

#endif /* AUG_MRAC_S6_H */
