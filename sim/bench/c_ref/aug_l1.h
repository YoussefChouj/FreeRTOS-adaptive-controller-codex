/*
 * aug_l1.h -- L1 adaptive augmentation, per-axis, C89 (float32).
 *
 * Mirrors ctrl_l1.py L1.controller_update() step-for-step.
 * One AugL1 instance per axis (roll, pitch, yaw).
 *
 * Inputs per tick: gyro_axis [dps], u_nom [firmware U].
 * Output: augmented u = u_nom + u_ad.
 */
#ifndef AUG_L1_H
#define AUG_L1_H

#include <math.h>

typedef struct {
    float b;            /* control effectiveness B_RP or B_RP*J0[0]/J0[1] or rad2deg(B_YAW) [dps^2/U] */
    float exp_Am_Ts;    /* exp(-l1_am * DT_C)  */
    float Phi_Ts;       /* (exp_Am_Ts - 1) / (-l1_am) = (1 - exp_Am_Ts) / l1_am  */
    float alpha;        /* exp(-2*pi*l1_f_hz * DT_C) */
    float clip;         /* l1_clip */
} AugL1Params;

typedef struct {
    float w_hat;        /* predicted gyro [dps] */
    float u_ad;         /* adaptive correction (filtered) */
    int   first;        /* 1 on first call, then 0 */
} AugL1;

/* Initialise state (call once at startup) */
static void aug_l1_init(AugL1 *s, const AugL1Params *p)
{
    (void)p;
    s->w_hat = 0.0f;
    s->u_ad  = 0.0f;
    s->first = 1;
}

/* Reset to PID-only (call when disarming or switching controller) */
static void aug_l1_reset(AugL1 *s)
{
    s->w_hat = 0.0f;
    s->u_ad  = 0.0f;
    s->first = 1;
}

/*
 * One tick of the L1 adaptive augmentation.
 *
 * gyro_axis : measured gyro for this axis [dps], from the bench obs['gyro'][:,axis].
 * u_nom     : PID nominal output for this axis [firmware U].
 *
 * Returns   : augmented u = u_nom + u_ad.
 *
 * The math matches ctrl_l1.py controller_update() line-for-line:
 *   sigma_hat = -(1/b) * (1/Phi_Ts) * exp_Am_Ts * (w_hat - w)
 *   u_ad      = alpha * u_ad + (1 - alpha) * (-sigma_hat)       // low-pass
 *   u_ad      = clip(u_ad, -clip, +clip)
 *   w_hat     = w + exp_Am_Ts * (w_hat - w) + Phi_Ts * b * (u_nom + u_ad + sigma_hat)
 */
static float aug_l1_update(AugL1 *s, const AugL1Params *p,
                           float gyro_axis, float u_nom)
{
    float w_err, sigma_hat, raw, u_ad_new;

    if (s->first) {
        s->w_hat = gyro_axis;
        s->first = 0;
    }

    /* prediction error */
    w_err = s->w_hat - gyro_axis;

    /* disturbance estimate */
    sigma_hat = -(1.0f / p->b) * (1.0f / p->Phi_Ts) * p->exp_Am_Ts * w_err;

    /* low-pass filter the negated disturbance -> adaptive correction */
    raw = -sigma_hat;
    u_ad_new = p->alpha * s->u_ad + (1.0f - p->alpha) * raw;

    /* clip */
    if (u_ad_new > p->clip)  u_ad_new = p->clip;
    if (u_ad_new < -p->clip) u_ad_new = -p->clip;
    s->u_ad = u_ad_new;

    /* update predictor */
    s->w_hat = gyro_axis
             + p->exp_Am_Ts * w_err
             + p->Phi_Ts * p->b * (u_nom + s->u_ad + sigma_hat);

    return u_nom + s->u_ad;
}

#endif /* AUG_L1_H */
