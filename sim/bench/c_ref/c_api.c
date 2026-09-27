/*
 * c_api.c -- ctypes-callable wrappers around aug_l1 and aug_mrac_s6.
 *
 * Compile:
 *   gcc -std=c89 -pedantic -Wall -Wextra -O2 -shared -fPIC -o c_ref.so c_api.c -lm
 *
 * C89 compliant: declarations before statements, no // comments, float32 only.
 */
#include "aug_l1.h"
#include "aug_mrac_s6.h"

/* ---- L1 wrappers ---- */

void c_aug_l1_init(AugL1 *s, const AugL1Params *p)
{
    aug_l1_init(s, p);
}

void c_aug_l1_reset(AugL1 *s)
{
    aug_l1_reset(s);
}

float c_aug_l1_update(AugL1 *s, const AugL1Params *p,
                      float gyro_axis, float u_nom)
{
    return aug_l1_update(s, p, gyro_axis, u_nom);
}

/* ---- MRAC S6 wrappers ---- */

void c_aug_mrac_s6_init(AugMracS6 *s, const AugMracS6Params *p)
{
    aug_mrac_s6_init(s, p);
}

void c_aug_mrac_s6_reset(AugMracS6 *s)
{
    aug_mrac_s6_reset(s);
}

float c_aug_mrac_s6_update(AugMracS6 *s, const AugMracS6Params *p,
                           float pm_meas, float phm_meas,
                           float U_pid, float pmk, float phmk,
                           float q_cross, float r_cross)
{
    return aug_mrac_s6_update(s, p, pm_meas, phm_meas,
                              U_pid, pmk, phmk, q_cross, r_cross);
}

/* ---- struct sizes for ctypes verification ---- */

int c_sizeof_AugL1(void) { return (int)sizeof(AugL1); }
int c_sizeof_AugL1Params(void) { return (int)sizeof(AugL1Params); }
int c_sizeof_AugMracS6(void) { return (int)sizeof(AugMracS6); }
int c_sizeof_AugMracS6Params(void) { return (int)sizeof(AugMracS6Params); }
