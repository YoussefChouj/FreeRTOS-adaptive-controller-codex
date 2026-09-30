#ifndef WFB_SAFETY_H
#define WFB_SAFETY_H

/* Workflow B safety module. Contract: docs/workflow-b/interfaces.md section 3. */

#include <stdint.h>
#include <stddef.h>

typedef enum {
    WFB_ACT_NONE = 0,
    WFB_ACT_LAND_VIA_HOVER,
    WFB_ACT_LAND_IN_PLACE,
    WFB_ACT_KILL
} wfb_action_t;

typedef enum {
    WFB_TRIP_NONE = 0,
    WFB_TRIP_HEARTBEAT,
    WFB_TRIP_LOW_V,
    WFB_TRIP_AIRBORNE_CAP,
    WFB_TRIP_FENCE,
    WFB_TRIP_CEILING,
    WFB_TRIP_TILT
} wfb_trip_t;

typedef struct {
    float fence_x_m;
    float fence_y_m;
    float ceiling_m;
    float low_v;
    float low_v_hold_s;
    float tilt_deg;
    float tilt_hold_s;
    float airborne_cap_s;
    float hb_timeout_s;
} wfb_safety_limits_t;

typedef struct {
    float x_m;
    float y_m;
    float z_m;
    float roll_deg;
    float pitch_deg;
    float vbat_v;
    float hb_age_s;
    float dt_s;
    uint8_t airborne;
    uint8_t gs_flight_active;
} wfb_safety_in_t;

typedef struct {
    float low_v_t;
    float tilt_t;
    float airborne_t;
    uint8_t trip;
    uint8_t action;
} wfb_safety_t;

void         wfb_safety_init(wfb_safety_t *s);
wfb_action_t wfb_safety_step(wfb_safety_t *s, const wfb_safety_limits_t *lim, const wfb_safety_in_t *in);
void         wfb_safety_default_limits(wfb_safety_limits_t *out);

#endif /* WFB_SAFETY_H */
