#include "calib.h"
#include <math.h>

/* ADR-0011 Phase 3 (CAL_AIRBORNE_HOVER_TRIM) + Phase 4 (CAL_HOT_HOVER).
 * Firmware port of sim/calibrator.py. Tick rate is 200 Hz (5 ms).
 * Constants are pinned to firmware-parity with sim/calibrator.py tests:
 *   mu              = 0.02   (LS update gain)
 *   settle_mg       = 5.0    (|g_ref - g_meas| < 5 mg)
 *   settle_ticks    = 200    (1 s @ 200 Hz)
 *   still_thresh    = 0.05   rad/s  (~3 deg/s, matches OF_BIAS_STILL_THRESH_RADPS)
 *   still_ticks     = 100    (0.5 s @ 200 Hz, matches OF_BIAS_STILL_TICKS)
 *   acc_ticks       = 400    (2.0 s @ 200 Hz, matches OF_BIAS_CAL_TICKS)
 *   alpha           = 1e-4   (slow, safe commit)
 *   lin_acc_thresh  = 50     mg (translational guard) */

#define CAL_TRIM_MU            0.02f
#define CAL_TRIM_SETTLE_MG     5.0f
#define CAL_TRIM_SETTLE_TICKS  200U
#define CAL_HOT_STILL_THRESH   0.05f
#define CAL_HOT_STILL_TICKS    100U
#define CAL_HOT_ACC_TICKS      400U
#define CAL_HOT_ALPHA          1e-4f
#define CAL_HOT_LIN_ACC_MG     50.0f

void CalTrim_Init(CalTrim_t *c, uint16_t max_ticks)
{
    c->state         = CAL_TRIM_STATE_WAIT_TAKEOFF;
    c->b_a[0]        = 0.0f;
    c->b_a[1]        = 0.0f;
    c->b_a[2]        = 0.0f;
    c->settle_resid  = 1.0e9f;
    c->settled_ticks = 0U;
    c->run_ticks     = 0U;
    c->max_ticks     = max_ticks;
}

void CalHot_Init(CalHot_t *c)
{
    c->state      = CAL_HOT_STATE_WAIT_STILL;
    c->b_g[0]     = 0.0f;
    c->b_g[1]     = 0.0f;
    c->b_g[2]     = 0.0f;
    c->still_tick = 0U;
    c->acc_tick   = 0U;
    c->rejected   = 0U;
    c->cleared    = 1U;
}

void CalTrim_Step(CalTrim_t *c,
                  float gwx, float gwy, float gwz,
                  float gmx, float gmy, float gmz,
                  uint8_t flight_phase_flying)
{
    if (!flight_phase_flying || c->state == CAL_TRIM_STATE_SETTLED ||
        c->state == CAL_TRIM_STATE_DEGRADED) {
        return;
    }

    if (c->state == CAL_TRIM_STATE_WAIT_TAKEOFF) {
        /* The stabilizer task only calls us after flight_phase==FLYING for at least
         * one tick, so the first tick here is the take-over. */
        c->state     = CAL_TRIM_STATE_RUNNING;
        c->run_ticks = 0U;
    }

    /* g_meas is the body-frame gravity-removed reading (mg). World gravity is +Z.
     * In hover: g_meas_world = R(q)*g_meas_body. We approximate g_meas_world with the
     * body-frame reading plus the assumed accel bias, then compute residual to g_ref. */
    float g_meas_w_x = gmx + c->b_a[0];
    float g_meas_w_y = gmy + c->b_a[1];
    float g_meas_w_z = gmz + c->b_a[2];

    float ex = gwx - g_meas_w_x;
    float ey = gwy - g_meas_w_y;
    float ez = gwz - g_meas_w_z;
    c->settle_resid = sqrtf(ex*ex + ey*ey + ez*ez);

    c->b_a[0] += CAL_TRIM_MU * ex;
    c->b_a[1] += CAL_TRIM_MU * ey;
    c->b_a[2] += CAL_TRIM_MU * ez;
    c->run_ticks++;

    if (c->settle_resid < CAL_TRIM_SETTLE_MG) {
        if (++c->settled_ticks >= CAL_TRIM_SETTLE_TICKS) {
            c->state = CAL_TRIM_STATE_SETTLED;
        }
    } else {
        c->settled_ticks = 0U;
    }

    if (c->run_ticks >= c->max_ticks && c->state == CAL_TRIM_STATE_RUNNING) {
        c->state = CAL_TRIM_STATE_DEGRADED;
    }
}

void CalHot_Step(CalHot_t *c,
                 float gx, float gy, float gz,
                 float lin_acc_x_mg, float lin_acc_y_mg,
                 uint8_t flight_phase_flying,
                 uint8_t rc_quiescent)
{
    c->rejected = 0U;   /* one-shot latch: cleared each tick unless a guard fires */

    if (!flight_phase_flying || !rc_quiescent ||
        (fabsf(lin_acc_x_mg) + fabsf(lin_acc_y_mg)) > CAL_HOT_LIN_ACC_MG) {
        c->state      = CAL_HOT_STATE_WAIT_STILL;
        c->still_tick = 0U;
        c->acc_tick   = 0U;
        c->rejected   = 1U;
        c->cleared    = 0U;
        return;
    }

    uint8_t still = (fabsf(gx) < CAL_HOT_STILL_THRESH) &&
                    (fabsf(gy) < CAL_HOT_STILL_THRESH) &&
                    (fabsf(gz) < CAL_HOT_STILL_THRESH);

    switch (c->state) {
    case CAL_HOT_STATE_WAIT_STILL:
        if (still) {
            if (++c->still_tick >= CAL_HOT_STILL_TICKS) {
                c->state      = CAL_HOT_STATE_ACCUM;
                c->acc_tick   = 0U;
                /* running sample mean */
                c->b_g[0]     = 0.0f;
                c->b_g[1]     = 0.0f;
                c->b_g[2]     = 0.0f;
            }
        } else {
            c->still_tick = 0U;
        }
        break;

    case CAL_HOT_STATE_ACCUM:
        if (still) {
            /* running mean, re-centered each entry — we only COMMIT a single
             * alpha-blended update at the end, so the inner sum is just the mean. */
            float inv_n = 1.0f / (float)(c->acc_tick + 1U);
            c->b_g[0] += (gx - c->b_g[0]) * inv_n;
            c->b_g[1] += (gy - c->b_g[1]) * inv_n;
            c->b_g[2] += (gz - c->b_g[2]) * inv_n;
            if (++c->acc_tick >= CAL_HOT_ACC_TICKS) {
                /* COMMIT: alpha-blend into the running b_g estimate.
                 * NOTE: do not touch accel bias here (Phase 3 owns that). */
                static float s_bg_running[3] = {0.0f, 0.0f, 0.0f};
                s_bg_running[0] = (1.0f - CAL_HOT_ALPHA) * s_bg_running[0] + CAL_HOT_ALPHA * c->b_g[0];
                s_bg_running[1] = (1.0f - CAL_HOT_ALPHA) * s_bg_running[1] + CAL_HOT_ALPHA * c->b_g[1];
                s_bg_running[2] = (1.0f - CAL_HOT_ALPHA) * s_bg_running[2] + CAL_HOT_ALPHA * c->b_g[2];
                c->b_g[0] = s_bg_running[0];
                c->b_g[1] = s_bg_running[1];
                c->b_g[2] = s_bg_running[2];
                c->state    = CAL_HOT_STATE_WAIT_STILL;   /* reset, refresh pattern */
                c->still_tick = 0U;
                c->acc_tick   = 0U;
            }
        } else {
            c->state      = CAL_HOT_STATE_WAIT_STILL;
            c->still_tick = 0U;
            c->acc_tick   = 0U;
            c->rejected   = 1U;
            c->cleared    = 0U;
        }
        break;

    default:
        c->state = CAL_HOT_STATE_WAIT_STILL;
        break;
    }
}
