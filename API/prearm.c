#include "prearm.h"
#include <stddef.h>
#include <math.h>

/* Pre-arm thresholds.
   @vbat_min_v     V    [10, 17]    battery at rest must read at least this (real_voltage, 1 Hz ADC)
   @tilt_max_deg   deg  [2, 45]     |roll| and |pitch| on the pad must both be under this
   @stab_fps_min   Hz   [50, 200]   stabilizer task rate over the last second must reach this (200 Hz nominal)
   2026-10-04 WP-40: all PROPOSED; vbat 14.0 = wfb_safety low_v, stab 180 = 90 % of the 200 Hz loop. */
#define PREARM_LIMITS_ROW(vbat_min_v, tilt_max_deg, stab_fps_min)     { (vbat_min_v), (tilt_max_deg), (stab_fps_min) }

static const prearm_limits_t s_limits =
/*                  vbat_min tilt_max stab_fps_min */
    PREARM_LIMITS_ROW(14.0f,  10.0f,   180.0f);  /* PROPOSED */

/* Which checks block arming when they fail (1) or only report (0); bit order of prearm_bit_t.
   @estimator  -  [0, 1]  bit 0 estimator converged (the FSM gates on IMU_EstimatorReady() regardless)
   @vbat       -  [0, 1]  bit 1 battery at rest
   @rc         -  [0, 1]  bit 2 RC link live (blocks a ground-station-only bench arm when set)
   @level      -  [0, 1]  bit 3 level on the pad
   @trip       -  [0, 1]  bit 4 no wfb_safety trip latched
   @stab_fps   -  [0, 1]  bit 5 stabilizer task rate
   2026-10-04 WP-40: all 0 (report only) until the operator enables them after a bench check. */
#define PREARM_ENABLE_ROW(estimator, vbat, rc, level, trip, stab_fps)     (uint16_t)(((unsigned)(estimator) << 0U) | ((unsigned)(vbat) << 1U) | ((unsigned)(rc) << 2U) | ((unsigned)(level) << 3U) | ((unsigned)(trip) << 4U) | ((unsigned)(stab_fps) << 5U))

volatile uint16_t g_prearm_enable_mask =
/*                  est vbat rc level trip stab_fps */
    PREARM_ENABLE_ROW(0,  0,   0, 0,    0,   0);

volatile uint16_t g_prearm_fail_mask  = 0U;
volatile uint16_t g_prearm_block_mask = 0U;
volatile uint8_t  g_prearm_first_fail = PREARM_FIRST_NONE;

void PreArm_DefaultLimits(prearm_limits_t *out)
{
    if (out != NULL) {
        *out = s_limits;
    }
}

/* Each test is written so a NaN input fails it. */
uint16_t PreArm_Evaluate(const prearm_in_t *in)
{
    unsigned fail = 0U;
    uint8_t  first = PREARM_FIRST_NONE;
    uint8_t  b;

    if (in == NULL) {
        fail = (1U << (unsigned)PREARM_BIT_COUNT) - 1U;
    } else {
        if (in->estimator_ready == 0U)                       { fail |= 1U << (unsigned)PREARM_BIT_ESTIMATOR; }
        if (!(in->vbat_v >= s_limits.vbat_min_v))            { fail |= 1U << (unsigned)PREARM_BIT_VBAT; }
        if (in->rc_live == 0U)                               { fail |= 1U << (unsigned)PREARM_BIT_RC; }
        if (!(fabsf(in->roll_deg)  < s_limits.tilt_max_deg) ||
            !(fabsf(in->pitch_deg) < s_limits.tilt_max_deg)) { fail |= 1U << (unsigned)PREARM_BIT_LEVEL; }
        if (in->safety_trip != 0U)                           { fail |= 1U << (unsigned)PREARM_BIT_TRIP; }
        if (!((float)in->stab_fps >= s_limits.stab_fps_min)) { fail |= 1U << (unsigned)PREARM_BIT_STAB_FPS; }
    }
    for (b = 0U; b < (uint8_t)PREARM_BIT_COUNT; b++) {
        if ((fail & (1U << b)) != 0U) {
            first = b;
            break;
        }
    }
    g_prearm_fail_mask  = (uint16_t)fail;
    g_prearm_block_mask = (uint16_t)(fail & (unsigned)g_prearm_enable_mask);
    g_prearm_first_fail = first;
    return (uint16_t)fail;
}

uint8_t PreArm_Allows(void)
{
    return (uint8_t)(g_prearm_block_mask == 0U);
}
