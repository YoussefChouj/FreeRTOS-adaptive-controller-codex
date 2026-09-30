#include "wfb_safety.h"
#include <stdint.h>
#include <stddef.h>
#include <math.h>

/* Safety limits default configuration table.
   fence_x_m      maximum |x| in world frame, m
   fence_y_m      maximum |y| in world frame, m
   ceiling_m      maximum altitude (ceiling), m
   low_v          low voltage threshold, V
   low_v_hold_s   low voltage continuous hold time, s
   tilt_deg       tilt angle threshold (|roll| or |pitch|), deg
   tilt_hold_s    tilt angle continuous hold time, s
   airborne_cap_s maximum airborne duration, s
   hb_timeout_s   heartbeat timeout, s */
#define WFB_SAFETY_LIMITS_ROW(fence_x_m, fence_y_m, ceiling_m, low_v, low_v_hold_s, \
                              tilt_deg, tilt_hold_s, airborne_cap_s, hb_timeout_s) \
    { (fence_x_m), (fence_y_m), (ceiling_m), (low_v), (low_v_hold_s), \
      (tilt_deg), (tilt_hold_s), (airborne_cap_s), (hb_timeout_s) }

static const wfb_safety_limits_t s_default_limits =
/*                   fence_x fence_y ceiling low_v low_v_hold tilt tilt_hold cap    hb */
    WFB_SAFETY_LIMITS_ROW(1.1f,   1.6f,   1.5f,   14.0f, 3.0f,     60.0f, 0.2f,    120.0f, 1.0f); /* PROPOSED */

void wfb_safety_default_limits(wfb_safety_limits_t *out)
{
    if (out != NULL) {
        *out = s_default_limits;
    }
}

void wfb_safety_init(wfb_safety_t *s)
{
    if (s != NULL) {
        s->low_v_t = 0.0f;
        s->tilt_t = 0.0f;
        s->airborne_t = 0.0f;
        s->trip = (uint8_t)WFB_TRIP_NONE;
        s->action = (uint8_t)WFB_ACT_NONE;
    }
}

wfb_action_t wfb_safety_step(wfb_safety_t *s, const wfb_safety_limits_t *lim, const wfb_safety_in_t *in)
{
    int tilt_active;
    int low_v_active;

    if (s == NULL) {
        return WFB_ACT_NONE;
    }
    if (lim == NULL || in == NULL) {
        return (wfb_action_t)s->action;
    }

    /* With airborne == 0 nothing trips, even with every limit violated, and no timer advances. */
    if (in->airborne == 0) {
        s->tilt_t = 0.0f;
        s->low_v_t = 0.0f;
        return (wfb_action_t)s->action;
    }

    /* airborne_t accumulates dt_s while airborne */
    s->airborne_t += in->dt_s;

    /* Check order: TILT, FENCE, CEILING, LOW_V, HEARTBEAT, AIRBORNE_CAP.
       All comparisons use negated form so NaN trips fail-safe. */

    /* 1. TILT: |roll| or |pitch| over tilt_deg continuously for tilt_hold_s -> KILL, trip TILT. */
    tilt_active = (!(fabsf(in->roll_deg) <= lim->tilt_deg) ||
                   !(fabsf(in->pitch_deg) <= lim->tilt_deg));
    if (tilt_active) {
        s->tilt_t += in->dt_s;
    } else {
        s->tilt_t = 0.0f;
    }
    if (tilt_active && !(s->tilt_t < lim->tilt_hold_s)) {
        if (s->action < (uint8_t)WFB_ACT_KILL) {
            s->action = (uint8_t)WFB_ACT_KILL;
            s->trip = (uint8_t)WFB_TRIP_TILT;
        }
    }

    /* 2. FENCE: |x| > fence_x_m or |y| > fence_y_m -> LAND_IN_PLACE, trip FENCE. */
    if (!(fabsf(in->x_m) <= lim->fence_x_m) || !(fabsf(in->y_m) <= lim->fence_y_m)) {
        if (s->action < (uint8_t)WFB_ACT_LAND_IN_PLACE) {
            s->action = (uint8_t)WFB_ACT_LAND_IN_PLACE;
            s->trip = (uint8_t)WFB_TRIP_FENCE;
        }
    }

    /* 3. CEILING: z > ceiling_m -> LAND_IN_PLACE, trip CEILING. */
    if (!(in->z_m <= lim->ceiling_m)) {
        if (s->action < (uint8_t)WFB_ACT_LAND_IN_PLACE) {
            s->action = (uint8_t)WFB_ACT_LAND_IN_PLACE;
            s->trip = (uint8_t)WFB_TRIP_CEILING;
        }
    }

    /* 4. LOW_V: vbat_v under low_v continuously for low_v_hold_s -> LAND_VIA_HOVER, trip LOW_V. */
    low_v_active = !(in->vbat_v >= lim->low_v);
    if (low_v_active) {
        s->low_v_t += in->dt_s;
    } else {
        s->low_v_t = 0.0f;
    }
    if (low_v_active && !(s->low_v_t < lim->low_v_hold_s)) {
        if (s->action < (uint8_t)WFB_ACT_LAND_VIA_HOVER) {
            s->action = (uint8_t)WFB_ACT_LAND_VIA_HOVER;
            s->trip = (uint8_t)WFB_TRIP_LOW_V;
        }
    }

    /* 5. HEARTBEAT: hb_age_s > hb_timeout_s with gs_flight_active == 1 -> LAND_VIA_HOVER, trip HEARTBEAT. */
    if (in->gs_flight_active != 0 && !(in->hb_age_s <= lim->hb_timeout_s)) {
        if (s->action < (uint8_t)WFB_ACT_LAND_VIA_HOVER) {
            s->action = (uint8_t)WFB_ACT_LAND_VIA_HOVER;
            s->trip = (uint8_t)WFB_TRIP_HEARTBEAT;
        }
    }

    /* 6. AIRBORNE_CAP: airborne_t over airborne_cap_s with gs_flight_active == 1 -> LAND_VIA_HOVER, trip AIRBORNE_CAP. */
    if (in->gs_flight_active != 0 && !(s->airborne_t <= lim->airborne_cap_s)) {
        if (s->action < (uint8_t)WFB_ACT_LAND_VIA_HOVER) {
            s->action = (uint8_t)WFB_ACT_LAND_VIA_HOVER;
            s->trip = (uint8_t)WFB_TRIP_AIRBORNE_CAP;
        }
    }

    return (wfb_action_t)s->action;
}
