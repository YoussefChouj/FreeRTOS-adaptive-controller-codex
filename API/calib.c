/**
 * @module     calib.c
 * @subsystem  sensors
 * @owner      Stabilizer_Task (TASK/StabilizerTask.c): CalTrim_Init/CalHot_Init once at boot, CalTrim_Step/CalHot_Step
 *             each 200 Hz control tick from Update_Data. Send_Task (send_data.c) reads s_cal_trim/s_cal_hot for telemetry.
 * @purpose    In-flight calibration (ADR-0011), the firmware port of sim/calibrator.py. Phase 3 CAL_AIRBORNE_HOVER_TRIM:
 *             LMS estimate of the accelerometer bias during hover. Phase 4 CAL_HOT_HOVER: gyro bias from still windows,
 *             committed by a slow alpha blend.
 * @inputs     reference and measured gravity [mg] (trim), body rates [rad/s] and horizontal linear accel [mg] (hot),
 *             flight-phase FLYING flag, RC-quiescent flag.
 * @outputs    CalTrim_t (b_a [mg], state, settle residual), CalHot_t (b_g [rad/s], state, rejected/cleared latches).
 */

#include "calib.h"
#include <math.h>

/* ------------------------------------------------------------------
 * Private constants  (firmware parity with the sim/calibrator.py tests; tick = 5 ms at 200 Hz)
 * ------------------------------------------------------------------ */

/* Phase 3 hover-trim tunables.
   @mu            -      [0.001, 0.1]    LMS update gain on the gravity residual
   @settle_mg     mg     [1, 50]         residual |g_ref - g_meas| below which a tick counts as settled
   @settle_ticks  ticks  [20, 2000]      consecutive settled ticks to declare SETTLED (200 = 1 s) */
typedef struct {
    float    mu, settle_mg;
    uint32_t settle_ticks;
} cal_trim_tune_t;
#define CAL_TRIM_ROW(mu, settle_mg, settle_ticks)     { (mu), (settle_mg), (settle_ticks) }

static const cal_trim_tune_t s_trim =
/*            mu     settle_mg settle_ticks */
    CAL_TRIM_ROW(0.02f, 5.0f,     200U);

/* Phase 4 hot-hover tunables.
   @still_thresh  rad/s  [0.01, 0.2]     per-axis rate below which the vehicle counts as still (~3 deg/s,
                                         matches OF_BIAS_STILL_THRESH_RADPS)
   @still_ticks   ticks  [20, 1000]      still ticks before accumulating (100 = 0.5 s, matches OF_BIAS_STILL_TICKS)
   @acc_ticks     ticks  [100, 2000]     samples averaged per commit (400 = 2 s, matches OF_BIAS_CAL_TICKS)
   @alpha         -      [1e-6, 1e-2]    blend weight of one window mean into the running b_g (slow, safe commit)
   @lin_acc_mg    mg     [10, 200]       |lin_acc_x| + |lin_acc_y| above which the window is rejected (translation) */
typedef struct {
    float    still_thresh;
    uint32_t still_ticks, acc_ticks;
    float    alpha, lin_acc_mg;
} cal_hot_tune_t;
#define CAL_HOT_ROW(still_thresh, still_ticks, acc_ticks, alpha, lin_acc_mg)     { (still_thresh), (still_ticks), (acc_ticks), (alpha), (lin_acc_mg) }

static const cal_hot_tune_t s_hot =
/*           still_thresh still_ticks acc_ticks alpha  lin_acc_mg */
    CAL_HOT_ROW(0.05f,    100U,       400U,     1e-4f, 50.0f);

/* ------------------------------------------------------------------
 * Module state
 * ------------------------------------------------------------------ */

/* Phase 4 running gyro-bias estimate [rad/s]; each committed window is alpha-blended into it. */
static float s_bg_running[3] = {0.0f, 0.0f, 0.0f};

/* ------------------------------------------------------------------
 * Private helpers
 * ------------------------------------------------------------------ */

/* Abort the current still window: back to WAIT_STILL and raise the rejected latch. */
static void cal_hot_reject(CalHot_t *c)
{
    c->state      = CAL_HOT_STATE_WAIT_STILL;
    c->still_tick = 0U;
    c->acc_tick   = 0U;
    c->rejected   = 1U;
    c->cleared    = 0U;
}

/* COMMIT a full window: alpha-blend its mean into the running b_g estimate, publish it, restart the still wait.
   The accel bias is not touched here (Phase 3 owns it). */
static void cal_hot_commit(CalHot_t *c)
{
    s_bg_running[0] = (1.0f - s_hot.alpha) * s_bg_running[0] + s_hot.alpha * c->b_g[0];
    s_bg_running[1] = (1.0f - s_hot.alpha) * s_bg_running[1] + s_hot.alpha * c->b_g[1];
    s_bg_running[2] = (1.0f - s_hot.alpha) * s_bg_running[2] + s_hot.alpha * c->b_g[2];
    c->b_g[0] = s_bg_running[0];
    c->b_g[1] = s_bg_running[1];
    c->b_g[2] = s_bg_running[2];
    c->state    = CAL_HOT_STATE_WAIT_STILL;   /* reset, refresh pattern */
    c->still_tick = 0U;
    c->acc_tick   = 0U;
}

/* ------------------------------------------------------------------
 * Public API
 * ------------------------------------------------------------------ */

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

    c->b_a[0] += s_trim.mu * ex;
    c->b_a[1] += s_trim.mu * ey;
    c->b_a[2] += s_trim.mu * ez;
    c->run_ticks++;

    if (c->settle_resid < s_trim.settle_mg) {
        if (++c->settled_ticks >= s_trim.settle_ticks) {
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
        (fabsf(lin_acc_x_mg) + fabsf(lin_acc_y_mg)) > s_hot.lin_acc_mg) {
        cal_hot_reject(c);
        return;
    }

    uint8_t still = (fabsf(gx) < s_hot.still_thresh) &&
                    (fabsf(gy) < s_hot.still_thresh) &&
                    (fabsf(gz) < s_hot.still_thresh);

    switch (c->state) {
    case CAL_HOT_STATE_WAIT_STILL:
        if (still) {
            if (++c->still_tick >= s_hot.still_ticks) {
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
            if (++c->acc_tick >= s_hot.acc_ticks) {
                cal_hot_commit(c);
            }
        } else {
            cal_hot_reject(c);
        }
        break;

    default:
        c->state = CAL_HOT_STATE_WAIT_STILL;
        break;
    }
}
