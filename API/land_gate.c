#include "land_gate.h"
#include <stddef.h>

/* Auto-land thresholds.
   @land_v         V    [13, 16]      pack under load below this ...
   @land_v_hold_s  s    [0, 10]       ... continuously this long; 1.5 s = two consecutive 1 Hz readings
   @vbat_valid_v   V    [5, 13]       readings under this mean no ADC sample yet, so no low-V action
   @sat_ccr        CCR  [3500, 4000]  a motor command at or over this counts as pinned (cap 4000)
   @sat_hold_s     s    [0.05, 2]     one motor pinned continuously this long
   2026-10-10 from vp15 arm300 (logs/vofa/vp15, 100 Hz): 14.3 V x2 readings would have fired at t 19 s, 24 s
   before the flip; pinned runs were 210 ms at takeoff and at most 241 ms above 14.2 V, then 340-1060 ms
   from 14.0 V down, so 0.35 s fires at t 20.4 s. PROPOSED, not yet flown.
   sat_ccr/sat_hold_s now only define what g_land_gate_sat_n counts; pinning never lands (2026-10-10). */
#define LAND_GATE_LIMITS_ROW(land_v, land_v_hold_s, vbat_valid_v, sat_ccr, sat_hold_s)     { (land_v), (land_v_hold_s), (vbat_valid_v), (sat_ccr), (sat_hold_s) }

static const land_gate_limits_t s_limits =
/*                     land_v land_v_hold vbat_valid sat_ccr  sat_hold */
    LAND_GATE_LIMITS_ROW(14.3f,  1.5f,       10.0f,     3990.0f, 0.35f);  /* PROPOSED */

volatile uint8_t g_land_gate_enable = LAND_GATE_EN_SAT;   /* LOW_V off by default (2026-10-10 operator: pack sags under traj load); write 3 to re-arm */
volatile uint8_t g_land_gate_trip   = LAND_GATE_NONE;
volatile uint8_t g_land_gate_sat_n  = 0U;
volatile float   g_land_gate_sat_max_s = 0.0f;

void LandGate_DefaultLimits(land_gate_limits_t *out)
{
    if (out != NULL) {
        *out = s_limits;
    }
}

void LandGate_Reset(land_gate_state_t *s)
{
    if (s != NULL) {
        s->low_v_t = 0.0f;
        s->sat_t = 0.0f;
        s->was_flying = 0U;
        s->fired = 0U;
        s->sat_counted = 0U;
    }
}

uint8_t LandGate_Step(land_gate_state_t *s, const land_gate_limits_t *lim, uint8_t enable, const land_gate_in_t *in)
{
    uint8_t trip = LAND_GATE_NONE;
    int low_v;

    if (s == NULL || lim == NULL || in == NULL) {
        return LAND_GATE_NONE;
    }
    if (!in->flying) {
        s->low_v_t = 0.0f;
        s->sat_t = 0.0f;
        s->was_flying = 0U;
        return LAND_GATE_NONE;
    }
    if (!s->was_flying) {   /* takeoff: new flight, forget the last trip */
        s->was_flying = 1U;
        s->fired = 0U;
        s->sat_counted = 0U;
        g_land_gate_trip = LAND_GATE_NONE;
        g_land_gate_sat_n = 0U;
        g_land_gate_sat_max_s = 0.0f;
    }

    /* A NaN reading counts as low: (v >= valid) is false for the dead-ADC test, so NaN is ignored there,
       then !(v >= land_v) is true. Only a reading under vbat_valid_v is skipped. */
    low_v = (in->vbat_v >= lim->vbat_valid_v || in->vbat_v != in->vbat_v) && !(in->vbat_v >= lim->land_v);
    s->low_v_t = low_v ? (s->low_v_t + in->dt_s) : 0.0f;
    s->sat_t = !(in->motor_max < lim->sat_ccr) ? (s->sat_t + in->dt_s) : 0.0f;

    /* motor pinning: log only, also after the low-V gate has fired */
    if (s->sat_t > g_land_gate_sat_max_s) {
        g_land_gate_sat_max_s = s->sat_t;
    }
    if (s->sat_t == 0.0f) {
        s->sat_counted = 0U;
    } else if ((enable & LAND_GATE_EN_SAT) != 0U && !s->sat_counted && !(s->sat_t < lim->sat_hold_s)) {
        s->sat_counted = 1U;
        if (g_land_gate_sat_n < 255U) {
            g_land_gate_sat_n++;
        }
    }

    if (s->fired) {
        return LAND_GATE_NONE;
    }
    if ((enable & LAND_GATE_EN_LOW_V) != 0U && !(s->low_v_t < lim->land_v_hold_s)) {
        trip = LAND_GATE_LOW_V;
    }
    if (trip != LAND_GATE_NONE) {
        s->fired = 1U;
        g_land_gate_trip = trip;
    }
    return trip;
}
