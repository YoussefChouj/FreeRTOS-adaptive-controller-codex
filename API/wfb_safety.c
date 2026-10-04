#include "wfb_safety.h"
#include <stdint.h>
#include <stddef.h>
#include <math.h>

/* Safety limits default configuration table.
   @fence_x_m       m    [0.5, 10]    maximum |x| in world frame
   @fence_y_m       m    [0.5, 10]    maximum |y| in world frame
   @ceiling_m       m    [0.5, 5]     maximum altitude (ceiling)
   @low_v           V    [10, 17]     low voltage threshold
   @low_v_hold_s    s    [0, 10]      low voltage continuous hold time
   @tilt_deg        deg  [10, 90]     tilt angle threshold (|roll| or |pitch|)
   @tilt_hold_s     s    [0, 2]       tilt angle continuous hold time
   @airborne_cap_s  s    [10, 900]    maximum airborne duration
   @hb_timeout_s    s    [0.2, 10]    heartbeat timeout
   @fence_hold_s    s    [0, 10]      longest push-back outside the fence or ceiling before LAND_IN_PLACE
   @fence_over_m    m    [0, 2]       this far beyond the fence or ceiling -> LAND_IN_PLACE at once
   @soft_margin_m   m    [0, 1]       push-back target this far inside the fence and ceiling
   2026-10-03 workflow-B launch grill: fence 1.6/2.0 and ceiling 1.7 around the ground-centre origin (operator);
   push-back instead of an immediate landing (operator: "too conservative"); hold/over/margin PROPOSED. */
#define WFB_SAFETY_LIMITS_ROW(fence_x_m, fence_y_m, ceiling_m, low_v, low_v_hold_s,                               tilt_deg, tilt_hold_s, airborne_cap_s, hb_timeout_s,                               fence_hold_s, fence_over_m, soft_margin_m)     { (fence_x_m), (fence_y_m), (ceiling_m), (low_v), (low_v_hold_s),       (tilt_deg), (tilt_hold_s), (airborne_cap_s), (hb_timeout_s),       (fence_hold_s), (fence_over_m), (soft_margin_m) }

static const wfb_safety_limits_t s_default_limits =
/*                   fence_x fence_y ceiling low_v low_v_hold tilt tilt_hold cap    hb    f_hold f_over soft */
    WFB_SAFETY_LIMITS_ROW(1.6f,   2.0f,   1.7f,   14.0f, 3.0f,     60.0f, 0.2f,    120.0f, 1.0f, 2.0f,  0.3f,  0.3f); /* PROPOSED */

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
        s->fence_t = 0.0f;
        s->trip = (uint8_t)WFB_TRIP_NONE;
        s->action = (uint8_t)WFB_ACT_NONE;
        s->push = 0u;
    }
}

void wfb_safety_push_sp(const wfb_safety_t *s, const wfb_safety_limits_t *lim, const wfb_safety_in_t *in,
                        float *x_sp_m, float *y_sp_m, float *z_sp_m)
{
    float soft;

    if (s == NULL || lim == NULL || in == NULL || x_sp_m == NULL || y_sp_m == NULL || z_sp_m == NULL) {
        return;
    }
    if ((s->push & WFB_PUSH_X) != 0u) {
        soft = lim->fence_x_m - lim->soft_margin_m;
        *x_sp_m = (in->x_m < 0.0f) ? -soft : soft;
    }
    if ((s->push & WFB_PUSH_Y) != 0u) {
        soft = lim->fence_y_m - lim->soft_margin_m;
        *y_sp_m = (in->y_m < 0.0f) ? -soft : soft;
    }
    if ((s->push & WFB_PUSH_Z) != 0u) {
        *z_sp_m = lim->ceiling_m - lim->soft_margin_m;
    }
}

wfb_action_t wfb_safety_step(wfb_safety_t *s, const wfb_safety_limits_t *lim, const wfb_safety_in_t *in)
{
    int tilt_active;
    int low_v_active;
    int land_now;
    uint8_t out, far;

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
        s->fence_t = 0.0f;
        s->push = 0u;
        return (wfb_action_t)s->action;
    }

    /* airborne_t accumulates dt_s while airborne */
    s->airborne_t += in->dt_s;

    /* Check order: TILT, FENCE/CEILING, LOW_V, HEARTBEAT, AIRBORNE_CAP.
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

    /* 2-3. FENCE / CEILING. Outside |x| fence_x_m, |y| fence_y_m or z ceiling_m on a GS flight with no action:
       push back (s->push, see wfb_safety_push_sp) while fence_t counts. Still out after fence_hold_s, more than
       fence_over_m beyond, or no GS setpoint to push with (pilot takeover) -> LAND_IN_PLACE, trip FENCE for x/y
       else CEILING. Back inside clears fence_t and the push. */
    out = 0u;
    far = 0u;
    if (!(fabsf(in->x_m) <= lim->fence_x_m)) { out |= WFB_PUSH_X; }
    if (!(fabsf(in->y_m) <= lim->fence_y_m)) { out |= WFB_PUSH_Y; }
    if (!(in->z_m <= lim->ceiling_m))        { out |= WFB_PUSH_Z; }
    if (!(fabsf(in->x_m) <= lim->fence_x_m + lim->fence_over_m)) { far |= WFB_PUSH_X; }
    if (!(fabsf(in->y_m) <= lim->fence_y_m + lim->fence_over_m)) { far |= WFB_PUSH_Y; }
    if (!(in->z_m <= lim->ceiling_m + lim->fence_over_m))        { far |= WFB_PUSH_Z; }
    if (out != 0u) {
        s->fence_t += in->dt_s;
    } else {
        s->fence_t = 0.0f;
    }
    land_now = (out != 0u) && (in->gs_flight_active == 0 || far != 0u || !(s->fence_t < lim->fence_hold_s));
    if (land_now && s->action < (uint8_t)WFB_ACT_LAND_IN_PLACE) {
        s->action = (uint8_t)WFB_ACT_LAND_IN_PLACE;
        s->trip = (uint8_t)(((out & (WFB_PUSH_X | WFB_PUSH_Y)) != 0u) ? WFB_TRIP_FENCE : WFB_TRIP_CEILING);
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

    s->push = (s->action == (uint8_t)WFB_ACT_NONE) ? out : 0u;
    return (wfb_action_t)s->action;
}
