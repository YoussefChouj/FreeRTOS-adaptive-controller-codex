#include <stdio.h>
#include <stdint.h>
#include <stddef.h>
#include <math.h>
#include <string.h>
#include "wfb_safety.h"

static int s_check_count = 0;
static int s_fail_count = 0;

#define CHECK(cond) do { \
    s_check_count++; \
    if (!(cond)) { \
        s_fail_count++; \
        printf("FAIL %s:%d: %s\n", __FILE__, __LINE__, #cond); \
    } \
} while (0)

#define CHECK_FLOAT_EQ(a, b, tol) do { \
    s_check_count++; \
    if (fabsf((a) - (b)) > (tol)) { \
        s_fail_count++; \
        printf("FAIL %s:%d: fabsf(%s - %s) = %f > %f\n", \
               __FILE__, __LINE__, #a, #b, (double)fabsf((a) - (b)), (double)(tol)); \
    } \
} while (0)

static void init_nominal_in(wfb_safety_in_t *in)
{
    in->x_m = 0.0f;
    in->y_m = 0.0f;
    in->z_m = 0.5f;
    in->roll_deg = 0.0f;
    in->pitch_deg = 0.0f;
    in->vbat_v = 15.0f;
    in->hb_age_s = 0.1f;
    in->dt_s = 0.01f;
    in->airborne = 1;
    in->gs_flight_active = 1;
}

/* 1. With airborne == 0 nothing trips, even with every limit violated, and no timer advances. */
static void test_1_airborne_zero(void)
{
    wfb_safety_t s;
    wfb_safety_limits_t lim;
    wfb_safety_in_t in;
    wfb_action_t act;
    int i;

    wfb_safety_init(&s);
    wfb_safety_default_limits(&lim);

    /* Violate every limit with airborne == 0 */
    in.x_m = 10.0f;
    in.y_m = 10.0f;
    in.z_m = 10.0f;
    in.roll_deg = 80.0f;
    in.pitch_deg = 80.0f;
    in.vbat_v = 10.0f;
    in.hb_age_s = 10.0f;
    in.dt_s = 5.0f;
    in.airborne = 0;
    in.gs_flight_active = 1;

    for (i = 0; i < 5; i++) {
        act = wfb_safety_step(&s, &lim, &in);
        CHECK(act == WFB_ACT_NONE);
        CHECK(s.action == (uint8_t)WFB_ACT_NONE);
        CHECK(s.trip == (uint8_t)WFB_TRIP_NONE);
        CHECK_FLOAT_EQ(s.low_v_t, 0.0f, 1e-6f);
        CHECK_FLOAT_EQ(s.tilt_t, 0.0f, 1e-6f);
        CHECK_FLOAT_EQ(s.airborne_t, 0.0f, 1e-6f);
        CHECK_FLOAT_EQ(s.fence_t, 0.0f, 1e-6f);
        CHECK(s.push == 0u);
    }

    /* Negative violations */
    in.x_m = -10.0f;
    in.y_m = -10.0f;
    in.roll_deg = -80.0f;
    in.pitch_deg = -80.0f;
    act = wfb_safety_step(&s, &lim, &in);
    CHECK(act == WFB_ACT_NONE);
    CHECK_FLOAT_EQ(s.low_v_t, 0.0f, 1e-6f);
    CHECK_FLOAT_EQ(s.tilt_t, 0.0f, 1e-6f);
    CHECK_FLOAT_EQ(s.airborne_t, 0.0f, 1e-6f);
}

/* 2. Tilt: |roll| or |pitch| over tilt_deg continuously for tilt_hold_s -> WFB_ACT_KILL, trip TILT.
      A shorter excursion -> NONE, and the timer restarts from zero. */
static void test_2_tilt(void)
{
    wfb_safety_t s;
    wfb_safety_limits_t lim;
    wfb_safety_in_t in;
    wfb_action_t act;

    wfb_safety_init(&s);
    wfb_safety_default_limits(&lim); /* tilt_deg = 60.0f, tilt_hold_s = 0.2f */
    init_nominal_in(&in);

    /* Shorter excursion than tilt_hold_s */
    in.roll_deg = 65.0f;
    in.dt_s = 0.1f;
    act = wfb_safety_step(&s, &lim, &in);
    CHECK(act == WFB_ACT_NONE);
    CHECK(s.action == (uint8_t)WFB_ACT_NONE);
    CHECK(s.trip == (uint8_t)WFB_TRIP_NONE);
    CHECK_FLOAT_EQ(s.tilt_t, 0.1f, 1e-6f);

    /* Normal input: timer restarts from zero */
    in.roll_deg = 0.0f;
    in.dt_s = 0.1f;
    act = wfb_safety_step(&s, &lim, &in);
    CHECK(act == WFB_ACT_NONE);
    CHECK(s.trip == (uint8_t)WFB_TRIP_NONE);
    CHECK_FLOAT_EQ(s.tilt_t, 0.0f, 1e-6f);

    /* Continuous excursion: 0.1s then 0.1s -> 0.2s reaches tilt_hold_s */
    in.roll_deg = 65.0f;
    in.dt_s = 0.1f;
    act = wfb_safety_step(&s, &lim, &in);
    CHECK(act == WFB_ACT_NONE);
    CHECK_FLOAT_EQ(s.tilt_t, 0.1f, 1e-6f);

    in.dt_s = 0.1f;
    act = wfb_safety_step(&s, &lim, &in);
    CHECK(act == WFB_ACT_KILL);
    CHECK(s.action == (uint8_t)WFB_ACT_KILL);
    CHECK(s.trip == (uint8_t)WFB_TRIP_TILT);
    CHECK_FLOAT_EQ(s.tilt_t, 0.2f, 1e-6f);

    /* Pitch negative excursion */
    wfb_safety_init(&s);
    init_nominal_in(&in);
    in.pitch_deg = -65.0f;
    in.dt_s = 0.1f;
    act = wfb_safety_step(&s, &lim, &in);
    CHECK(act == WFB_ACT_NONE);
    CHECK_FLOAT_EQ(s.tilt_t, 0.1f, 1e-6f);

    in.pitch_deg = 0.0f;
    act = wfb_safety_step(&s, &lim, &in);
    CHECK_FLOAT_EQ(s.tilt_t, 0.0f, 1e-6f);

    in.pitch_deg = -65.0f;
    in.dt_s = 0.2f;
    act = wfb_safety_step(&s, &lim, &in);
    CHECK(act == WFB_ACT_KILL);
    CHECK(s.trip == (uint8_t)WFB_TRIP_TILT);
}

/* 3. Fence and ceiling push-back on a GS flight (fence_x_m 1.6, fence_y_m 2.0, ceiling_m 1.7,
      fence_hold_s 2.0, fence_over_m 0.3):
      - just outside -> NONE, push bit set, fence_t counts;
      - back inside -> push and fence_t clear;
      - outside for fence_hold_s -> LAND_IN_PLACE, trip FENCE (x/y) or CEILING (z), push clears;
      - more than fence_over_m beyond -> LAND_IN_PLACE on the first step;
      - pilot takeover (gs_flight_active 0) -> LAND_IN_PLACE on the first step;
      - exact boundaries do not count as outside. */
static void test_3_fence_ceiling(void)
{
    wfb_safety_t s;
    wfb_safety_limits_t lim;
    wfb_safety_in_t in;
    wfb_action_t act;
    int i;

    wfb_safety_default_limits(&lim);

    /* x just outside: push X, no action */
    wfb_safety_init(&s);
    init_nominal_in(&in);
    in.x_m = 1.7f;
    act = wfb_safety_step(&s, &lim, &in);
    CHECK(act == WFB_ACT_NONE);
    CHECK(s.trip == (uint8_t)WFB_TRIP_NONE);
    CHECK(s.push == (uint8_t)WFB_PUSH_X);
    CHECK_FLOAT_EQ(s.fence_t, 0.01f, 1e-6f);

    /* back inside clears push and fence_t */
    in.x_m = 1.5f;
    act = wfb_safety_step(&s, &lim, &in);
    CHECK(act == WFB_ACT_NONE);
    CHECK(s.push == 0u);
    CHECK_FLOAT_EQ(s.fence_t, 0.0f, 1e-6f);

    /* -x, -y and z each set their own bit; all three together set all three */
    wfb_safety_init(&s);
    init_nominal_in(&in);
    in.x_m = -1.7f;
    in.y_m = -2.1f;
    in.z_m = 1.8f;
    act = wfb_safety_step(&s, &lim, &in);
    CHECK(act == WFB_ACT_NONE);
    CHECK(s.push == (uint8_t)(WFB_PUSH_X | WFB_PUSH_Y | WFB_PUSH_Z));

    /* y outside for fence_hold_s -> LAND_IN_PLACE, trip FENCE, push clears */
    wfb_safety_init(&s);
    init_nominal_in(&in);
    in.y_m = 2.1f;
    in.dt_s = 0.5f;
    for (i = 0; i < 3; i++) {
        act = wfb_safety_step(&s, &lim, &in);
        CHECK(act == WFB_ACT_NONE);
        CHECK(s.push == (uint8_t)WFB_PUSH_Y);
    }
    act = wfb_safety_step(&s, &lim, &in);
    CHECK(act == WFB_ACT_LAND_IN_PLACE);
    CHECK(s.trip == (uint8_t)WFB_TRIP_FENCE);
    CHECK(s.push == 0u);

    /* z outside for fence_hold_s -> trip CEILING */
    wfb_safety_init(&s);
    init_nominal_in(&in);
    in.z_m = 1.8f;
    in.dt_s = 2.0f;
    act = wfb_safety_step(&s, &lim, &in);
    CHECK(act == WFB_ACT_LAND_IN_PLACE);
    CHECK(s.trip == (uint8_t)WFB_TRIP_CEILING);

    /* the hold timer restarts after a return inside */
    wfb_safety_init(&s);
    init_nominal_in(&in);
    in.dt_s = 1.5f;
    in.x_m = 1.7f;
    CHECK(wfb_safety_step(&s, &lim, &in) == WFB_ACT_NONE);
    in.x_m = 0.0f;
    CHECK(wfb_safety_step(&s, &lim, &in) == WFB_ACT_NONE);
    in.x_m = 1.7f;
    CHECK(wfb_safety_step(&s, &lim, &in) == WFB_ACT_NONE);
    CHECK_FLOAT_EQ(s.fence_t, 1.5f, 1e-6f);

    /* more than fence_over_m beyond -> LAND_IN_PLACE on the first step, each axis */
    wfb_safety_init(&s);
    init_nominal_in(&in);
    in.x_m = -1.95f;
    act = wfb_safety_step(&s, &lim, &in);
    CHECK(act == WFB_ACT_LAND_IN_PLACE);
    CHECK(s.trip == (uint8_t)WFB_TRIP_FENCE);
    CHECK(s.push == 0u);

    wfb_safety_init(&s);
    init_nominal_in(&in);
    in.y_m = 2.35f;
    CHECK(wfb_safety_step(&s, &lim, &in) == WFB_ACT_LAND_IN_PLACE);
    CHECK(s.trip == (uint8_t)WFB_TRIP_FENCE);

    wfb_safety_init(&s);
    init_nominal_in(&in);
    in.z_m = 2.05f;
    CHECK(wfb_safety_step(&s, &lim, &in) == WFB_ACT_LAND_IN_PLACE);
    CHECK(s.trip == (uint8_t)WFB_TRIP_CEILING);

    /* pilot takeover (no GS setpoint to push with) -> LAND_IN_PLACE on the first step */
    wfb_safety_init(&s);
    init_nominal_in(&in);
    in.gs_flight_active = 0;
    in.x_m = 1.7f;
    CHECK(wfb_safety_step(&s, &lim, &in) == WFB_ACT_LAND_IN_PLACE);
    CHECK(s.trip == (uint8_t)WFB_TRIP_FENCE);
    CHECK(s.push == 0u);

    /* an earlier LAND_VIA_HOVER: no push, still escalates after fence_hold_s */
    wfb_safety_init(&s);
    init_nominal_in(&in);
    in.vbat_v = 12.0f;
    in.dt_s = 3.0f;
    CHECK(wfb_safety_step(&s, &lim, &in) == WFB_ACT_LAND_VIA_HOVER);
    in.vbat_v = 15.0f;
    in.dt_s = 1.0f;
    in.x_m = 1.7f;
    CHECK(wfb_safety_step(&s, &lim, &in) == WFB_ACT_LAND_VIA_HOVER);
    CHECK(s.push == 0u);
    CHECK(wfb_safety_step(&s, &lim, &in) == WFB_ACT_LAND_IN_PLACE);
    CHECK(s.trip == (uint8_t)WFB_TRIP_FENCE);

    /* Exact boundaries are inside */
    wfb_safety_init(&s);
    init_nominal_in(&in);
    in.x_m = 1.6f;
    in.y_m = -2.0f;
    in.z_m = 1.7f;
    act = wfb_safety_step(&s, &lim, &in);
    CHECK(act == WFB_ACT_NONE);
    CHECK(s.trip == (uint8_t)WFB_TRIP_NONE);
    CHECK(s.push == 0u);
}

/* 3b. wfb_safety_push_sp: each pushed axis moves to the soft boundary (fence - soft_margin_m) on the
       drone's side; unpushed axes keep the setpoint. */
static void test_3b_push_sp(void)
{
    wfb_safety_t s;
    wfb_safety_limits_t lim;
    wfb_safety_in_t in;
    float x, y, z;

    wfb_safety_default_limits(&lim);
    wfb_safety_init(&s);
    init_nominal_in(&in);

    x = 0.4f; y = 0.5f; z = 0.6f;
    wfb_safety_push_sp(&s, &lim, &in, &x, &y, &z);
    CHECK_FLOAT_EQ(x, 0.4f, 1e-6f);
    CHECK_FLOAT_EQ(y, 0.5f, 1e-6f);
    CHECK_FLOAT_EQ(z, 0.6f, 1e-6f);

    in.x_m = -1.7f;
    in.y_m = 2.1f;
    in.z_m = 1.8f;
    (void)wfb_safety_step(&s, &lim, &in);
    wfb_safety_push_sp(&s, &lim, &in, &x, &y, &z);
    CHECK_FLOAT_EQ(x, -1.3f, 1e-6f);
    CHECK_FLOAT_EQ(y, 1.7f, 1e-6f);
    CHECK_FLOAT_EQ(z, 1.4f, 1e-6f);

    /* only y pushed */
    wfb_safety_init(&s);
    init_nominal_in(&in);
    in.y_m = -2.1f;
    (void)wfb_safety_step(&s, &lim, &in);
    x = 0.4f; y = 0.5f; z = 0.6f;
    wfb_safety_push_sp(&s, &lim, &in, &x, &y, &z);
    CHECK_FLOAT_EQ(x, 0.4f, 1e-6f);
    CHECK_FLOAT_EQ(y, -1.7f, 1e-6f);
    CHECK_FLOAT_EQ(z, 0.6f, 1e-6f);

    /* Null pointers do not crash */
    wfb_safety_push_sp(NULL, &lim, &in, &x, &y, &z);
    wfb_safety_push_sp(&s, &lim, &in, NULL, &y, &z);
}

/* 4. Low voltage: vbat_v under low_v continuously for low_v_hold_s -> LAND_VIA_HOVER, trip LOW_V.
      A shorter dip -> NONE. */
static void test_4_low_v(void)
{
    wfb_safety_t s;
    wfb_safety_limits_t lim;
    wfb_safety_in_t in;
    wfb_action_t act;

    wfb_safety_init(&s);
    wfb_safety_default_limits(&lim); /* low_v = 14.0f, low_v_hold_s = 3.0f */
    init_nominal_in(&in);

    /* Shorter dip */
    in.vbat_v = 13.5f;
    in.dt_s = 1.0f;
    act = wfb_safety_step(&s, &lim, &in);
    CHECK(act == WFB_ACT_NONE);
    CHECK_FLOAT_EQ(s.low_v_t, 1.0f, 1e-6f);

    act = wfb_safety_step(&s, &lim, &in);
    CHECK(act == WFB_ACT_NONE);
    CHECK_FLOAT_EQ(s.low_v_t, 2.0f, 1e-6f);

    /* Voltage recovers: timer resets to zero */
    in.vbat_v = 14.5f;
    act = wfb_safety_step(&s, &lim, &in);
    CHECK(act == WFB_ACT_NONE);
    CHECK_FLOAT_EQ(s.low_v_t, 0.0f, 1e-6f);

    /* Continuous dip for 3.0s */
    in.vbat_v = 13.8f;
    in.dt_s = 1.0f;
    act = wfb_safety_step(&s, &lim, &in);
    CHECK(act == WFB_ACT_NONE);
    CHECK_FLOAT_EQ(s.low_v_t, 1.0f, 1e-6f);

    act = wfb_safety_step(&s, &lim, &in);
    CHECK(act == WFB_ACT_NONE);
    CHECK_FLOAT_EQ(s.low_v_t, 2.0f, 1e-6f);

    act = wfb_safety_step(&s, &lim, &in);
    CHECK(act == WFB_ACT_LAND_VIA_HOVER);
    CHECK(s.action == (uint8_t)WFB_ACT_LAND_VIA_HOVER);
    CHECK(s.trip == (uint8_t)WFB_TRIP_LOW_V);
    CHECK_FLOAT_EQ(s.low_v_t, 3.0f, 1e-6f);
}

/* 5. Heartbeat: hb_age_s > hb_timeout_s with gs_flight_active == 1 -> LAND_VIA_HOVER, trip HEARTBEAT;
      with gs_flight_active == 0 -> NONE. */
static void test_5_heartbeat(void)
{
    wfb_safety_t s;
    wfb_safety_limits_t lim;
    wfb_safety_in_t in;
    wfb_action_t act;

    wfb_safety_init(&s);
    wfb_safety_default_limits(&lim); /* hb_timeout_s = 1.0f */
    init_nominal_in(&in);

    /* With gs_flight_active == 0: never trips */
    in.gs_flight_active = 0;
    in.hb_age_s = 1.5f;
    act = wfb_safety_step(&s, &lim, &in);
    CHECK(act == WFB_ACT_NONE);
    CHECK(s.action == (uint8_t)WFB_ACT_NONE);
    CHECK(s.trip == (uint8_t)WFB_TRIP_NONE);

    in.hb_age_s = 50.0f;
    act = wfb_safety_step(&s, &lim, &in);
    CHECK(act == WFB_ACT_NONE);
    CHECK(s.trip == (uint8_t)WFB_TRIP_NONE);

    /* With gs_flight_active == 1: boundary check */
    in.gs_flight_active = 1;
    in.hb_age_s = 1.0f;
    act = wfb_safety_step(&s, &lim, &in);
    CHECK(act == WFB_ACT_NONE);

    in.hb_age_s = 1.05f;
    act = wfb_safety_step(&s, &lim, &in);
    CHECK(act == WFB_ACT_LAND_VIA_HOVER);
    CHECK(s.action == (uint8_t)WFB_ACT_LAND_VIA_HOVER);
    CHECK(s.trip == (uint8_t)WFB_TRIP_HEARTBEAT);
}

/* 6. Airborne cap: airborne_t accumulates dt_s while airborne; over airborne_cap_s
      with gs_flight_active == 1 -> LAND_VIA_HOVER, trip AIRBORNE_CAP; with 0 -> NONE. */
static void test_6_airborne_cap(void)
{
    wfb_safety_t s;
    wfb_safety_limits_t lim;
    wfb_safety_in_t in;
    wfb_action_t act;

    wfb_safety_init(&s);
    wfb_safety_default_limits(&lim); /* airborne_cap_s = 120.0f */
    init_nominal_in(&in);

    /* gs_flight_active == 0: accumulates dt_s, does not trip over cap */
    in.gs_flight_active = 0;
    in.dt_s = 50.0f;
    act = wfb_safety_step(&s, &lim, &in);
    CHECK_FLOAT_EQ(s.airborne_t, 50.0f, 1e-6f);
    CHECK(act == WFB_ACT_NONE);

    in.dt_s = 80.0f;
    act = wfb_safety_step(&s, &lim, &in);
    CHECK_FLOAT_EQ(s.airborne_t, 130.0f, 1e-6f);
    CHECK(act == WFB_ACT_NONE);
    CHECK(s.trip == (uint8_t)WFB_TRIP_NONE);

    /* gs_flight_active == 1 */
    wfb_safety_init(&s);
    in.gs_flight_active = 1;
    in.dt_s = 60.0f;
    act = wfb_safety_step(&s, &lim, &in);
    CHECK_FLOAT_EQ(s.airborne_t, 60.0f, 1e-6f);
    CHECK(act == WFB_ACT_NONE);

    in.dt_s = 60.0f;
    act = wfb_safety_step(&s, &lim, &in);
    CHECK_FLOAT_EQ(s.airborne_t, 120.0f, 1e-6f);
    CHECK(act == WFB_ACT_NONE);

    in.dt_s = 0.5f;
    act = wfb_safety_step(&s, &lim, &in);
    CHECK_FLOAT_EQ(s.airborne_t, 120.5f, 1e-5f);
    CHECK(act == WFB_ACT_LAND_VIA_HOVER);
    CHECK(s.action == (uint8_t)WFB_ACT_LAND_VIA_HOVER);
    CHECK(s.trip == (uint8_t)WFB_TRIP_AIRBORNE_CAP);
}

/* 7. Latch and escalation: after LAND_VIA_HOVER the action stays when inputs return to normal;
      a later fence breach raises it to LAND_IN_PLACE with trip FENCE; a later tilt raises it
      to KILL with trip TILT; it never goes down; wfb_safety_init clears everything. */
static void test_7_latch_and_escalation(void)
{
    wfb_safety_t s;
    wfb_safety_limits_t lim;
    wfb_safety_in_t in;
    wfb_action_t act;

    wfb_safety_init(&s);
    wfb_safety_default_limits(&lim);
    init_nominal_in(&in);

    /* Trip LOW_V */
    in.vbat_v = 13.0f;
    in.dt_s = 3.0f;
    act = wfb_safety_step(&s, &lim, &in);
    CHECK(act == WFB_ACT_LAND_VIA_HOVER);
    CHECK(s.trip == (uint8_t)WFB_TRIP_LOW_V);

    /* Return to normal: action stays */
    in.vbat_v = 15.0f;
    in.dt_s = 0.1f;
    act = wfb_safety_step(&s, &lim, &in);
    CHECK(act == WFB_ACT_LAND_VIA_HOVER);
    CHECK(s.trip == (uint8_t)WFB_TRIP_LOW_V);

    /* Later fence breach raises to LAND_IN_PLACE with trip FENCE */
    in.x_m = 2.0f;
    act = wfb_safety_step(&s, &lim, &in);
    CHECK(act == WFB_ACT_LAND_IN_PLACE);
    CHECK(s.action == (uint8_t)WFB_ACT_LAND_IN_PLACE);
    CHECK(s.trip == (uint8_t)WFB_TRIP_FENCE);

    /* Return fence to normal: action stays */
    in.x_m = 0.0f;
    act = wfb_safety_step(&s, &lim, &in);
    CHECK(act == WFB_ACT_LAND_IN_PLACE);
    CHECK(s.trip == (uint8_t)WFB_TRIP_FENCE);

    /* Later tilt raises to KILL with trip TILT */
    in.roll_deg = 70.0f;
    in.dt_s = 0.2f;
    act = wfb_safety_step(&s, &lim, &in);
    CHECK(act == WFB_ACT_KILL);
    CHECK(s.action == (uint8_t)WFB_ACT_KILL);
    CHECK(s.trip == (uint8_t)WFB_TRIP_TILT);

    /* Never goes down even if inputs return to normal or lower checks fire */
    in.roll_deg = 0.0f;
    in.x_m = 5.0f;
    in.vbat_v = 10.0f;
    act = wfb_safety_step(&s, &lim, &in);
    CHECK(act == WFB_ACT_KILL);
    CHECK(s.action == (uint8_t)WFB_ACT_KILL);
    CHECK(s.trip == (uint8_t)WFB_TRIP_TILT);

    /* wfb_safety_init clears everything */
    wfb_safety_init(&s);
    CHECK(s.action == (uint8_t)WFB_ACT_NONE);
    CHECK(s.trip == (uint8_t)WFB_TRIP_NONE);
    CHECK_FLOAT_EQ(s.low_v_t, 0.0f, 1e-6f);
    CHECK_FLOAT_EQ(s.tilt_t, 0.0f, 1e-6f);
    CHECK_FLOAT_EQ(s.airborne_t, 0.0f, 1e-6f);
}

/* 8. wfb_safety_default_limits returns the interfaces.md values
      (1.6, 2.0, 1.7, 14.0, 3.0, 60.0, 0.2, 120.0, 1.0, 2.0, 0.3, 0.3). */
static void test_8_default_limits(void)
{
    wfb_safety_limits_t lim;

    memset(&lim, 0, sizeof(lim));
    wfb_safety_default_limits(&lim);

    CHECK_FLOAT_EQ(lim.fence_x_m, 1.6f, 1e-6f);
    CHECK_FLOAT_EQ(lim.fence_y_m, 2.0f, 1e-6f);
    CHECK_FLOAT_EQ(lim.ceiling_m, 1.7f, 1e-6f);
    CHECK_FLOAT_EQ(lim.low_v, 14.0f, 1e-6f);
    CHECK_FLOAT_EQ(lim.low_v_hold_s, 3.0f, 1e-6f);
    CHECK_FLOAT_EQ(lim.tilt_deg, 60.0f, 1e-6f);
    CHECK_FLOAT_EQ(lim.tilt_hold_s, 0.2f, 1e-6f);
    CHECK_FLOAT_EQ(lim.airborne_cap_s, 120.0f, 1e-6f);
    CHECK_FLOAT_EQ(lim.hb_timeout_s, 1.0f, 1e-6f);
    CHECK_FLOAT_EQ(lim.fence_hold_s, 2.0f, 1e-6f);
    CHECK_FLOAT_EQ(lim.fence_over_m, 0.3f, 1e-6f);
    CHECK_FLOAT_EQ(lim.soft_margin_m, 0.3f, 1e-6f);

    /* Null pointer check does not crash */
    wfb_safety_default_limits(NULL);
}

/* 9. Fail-safe on bad numbers: a NaN in x_m or y_m trips FENCE; a NaN in z_m trips CEILING;
      a NaN roll or pitch counts as over the tilt limit; a NaN vbat_v counts as under low_v;
      a NaN hb_age_s counts as timed out. */
static void test_9_fail_safe_on_bad_numbers(void)
{
    wfb_safety_t s;
    wfb_safety_limits_t lim;
    wfb_safety_in_t in;
    wfb_action_t act;
    float nan_val;

    nan_val = (float)NAN;
    CHECK(isnan(nan_val));
    wfb_safety_default_limits(&lim);

    /* NaN in x_m trips FENCE on first step */
    wfb_safety_init(&s);
    init_nominal_in(&in);
    in.x_m = nan_val;
    act = wfb_safety_step(&s, &lim, &in);
    CHECK(act == WFB_ACT_LAND_IN_PLACE);
    CHECK(s.trip == (uint8_t)WFB_TRIP_FENCE);

    /* NaN in y_m trips FENCE on first step */
    wfb_safety_init(&s);
    init_nominal_in(&in);
    in.y_m = nan_val;
    act = wfb_safety_step(&s, &lim, &in);
    CHECK(act == WFB_ACT_LAND_IN_PLACE);
    CHECK(s.trip == (uint8_t)WFB_TRIP_FENCE);

    /* NaN in z_m trips CEILING on first step */
    wfb_safety_init(&s);
    init_nominal_in(&in);
    in.z_m = nan_val;
    act = wfb_safety_step(&s, &lim, &in);
    CHECK(act == WFB_ACT_LAND_IN_PLACE);
    CHECK(s.trip == (uint8_t)WFB_TRIP_CEILING);

    /* NaN roll counts as over tilt limit */
    wfb_safety_init(&s);
    init_nominal_in(&in);
    in.roll_deg = nan_val;
    in.dt_s = 0.1f;
    act = wfb_safety_step(&s, &lim, &in);
    CHECK(act == WFB_ACT_NONE);
    CHECK_FLOAT_EQ(s.tilt_t, 0.1f, 1e-6f);

    act = wfb_safety_step(&s, &lim, &in);
    CHECK(act == WFB_ACT_KILL);
    CHECK(s.trip == (uint8_t)WFB_TRIP_TILT);

    /* NaN pitch counts as over tilt limit */
    wfb_safety_init(&s);
    init_nominal_in(&in);
    in.pitch_deg = nan_val;
    in.dt_s = 0.2f;
    act = wfb_safety_step(&s, &lim, &in);
    CHECK(act == WFB_ACT_KILL);
    CHECK(s.trip == (uint8_t)WFB_TRIP_TILT);

    /* NaN vbat_v counts as under low_v */
    wfb_safety_init(&s);
    init_nominal_in(&in);
    in.vbat_v = nan_val;
    in.dt_s = 1.5f;
    act = wfb_safety_step(&s, &lim, &in);
    CHECK(act == WFB_ACT_NONE);
    CHECK_FLOAT_EQ(s.low_v_t, 1.5f, 1e-6f);

    act = wfb_safety_step(&s, &lim, &in);
    CHECK(act == WFB_ACT_LAND_VIA_HOVER);
    CHECK(s.trip == (uint8_t)WFB_TRIP_LOW_V);

    /* NaN hb_age_s counts as timed out */
    wfb_safety_init(&s);
    init_nominal_in(&in);
    in.hb_age_s = nan_val;
    in.gs_flight_active = 1;
    act = wfb_safety_step(&s, &lim, &in);
    CHECK(act == WFB_ACT_LAND_VIA_HOVER);
    CHECK(s.trip == (uint8_t)WFB_TRIP_HEARTBEAT);
}

/* 10. Same-step ties: checks run in the order TILT, FENCE/CEILING, LOW_V, HEARTBEAT, AIRBORNE_CAP.
       When two causes of the same action level first trip on the same step, trip records the earlier one. */
static void test_10_same_step_ties(void)
{
    wfb_safety_t s;
    wfb_safety_limits_t lim;
    wfb_safety_in_t in;
    wfb_action_t act;

    wfb_safety_default_limits(&lim);

    /* Same action level: FENCE vs CEILING (both LAND_IN_PLACE) -> FENCE wins */
    wfb_safety_init(&s);
    init_nominal_in(&in);
    in.x_m = 2.0f;
    in.z_m = 2.0f;
    act = wfb_safety_step(&s, &lim, &in);
    CHECK(act == WFB_ACT_LAND_IN_PLACE);
    CHECK(s.trip == (uint8_t)WFB_TRIP_FENCE);

    /* Same action level: LOW_V vs HEARTBEAT (both LAND_VIA_HOVER) -> LOW_V wins */
    wfb_safety_init(&s);
    init_nominal_in(&in);
    in.vbat_v = 12.0f;
    in.dt_s = 3.0f;
    in.hb_age_s = 2.0f;
    in.gs_flight_active = 1;
    act = wfb_safety_step(&s, &lim, &in);
    CHECK(act == WFB_ACT_LAND_VIA_HOVER);
    CHECK(s.trip == (uint8_t)WFB_TRIP_LOW_V);

    /* Same action level: HEARTBEAT vs AIRBORNE_CAP (both LAND_VIA_HOVER) -> HEARTBEAT wins */
    wfb_safety_init(&s);
    init_nominal_in(&in);
    in.hb_age_s = 2.0f;
    in.dt_s = 125.0f;
    in.gs_flight_active = 1;
    act = wfb_safety_step(&s, &lim, &in);
    CHECK(act == WFB_ACT_LAND_VIA_HOVER);
    CHECK(s.trip == (uint8_t)WFB_TRIP_HEARTBEAT);

    /* Same action level: LOW_V vs AIRBORNE_CAP (both LAND_VIA_HOVER) -> LOW_V wins */
    wfb_safety_init(&s);
    init_nominal_in(&in);
    in.vbat_v = 12.0f;
    in.dt_s = 125.0f;
    in.hb_age_s = 0.1f;
    in.gs_flight_active = 1;
    act = wfb_safety_step(&s, &lim, &in);
    CHECK(act == WFB_ACT_LAND_VIA_HOVER);
    CHECK(s.trip == (uint8_t)WFB_TRIP_LOW_V);

    /* Different action levels: TILT vs FENCE -> TILT wins (KILL) */
    wfb_safety_init(&s);
    init_nominal_in(&in);
    in.roll_deg = 70.0f;
    in.x_m = 2.0f;
    in.dt_s = 0.2f;
    act = wfb_safety_step(&s, &lim, &in);
    CHECK(act == WFB_ACT_KILL);
    CHECK(s.trip == (uint8_t)WFB_TRIP_TILT);

    /* Different action levels: CEILING vs LOW_V -> CEILING wins (LAND_IN_PLACE) */
    wfb_safety_init(&s);
    init_nominal_in(&in);
    in.z_m = 2.0f;
    in.vbat_v = 12.0f;
    in.dt_s = 3.0f;
    in.gs_flight_active = 1;
    act = wfb_safety_step(&s, &lim, &in);
    CHECK(act == WFB_ACT_LAND_IN_PLACE);
    CHECK(s.trip == (uint8_t)WFB_TRIP_CEILING);

    /* All 6 violations simultaneously on first step -> TILT wins (KILL) */
    wfb_safety_init(&s);
    init_nominal_in(&in);
    in.roll_deg = 70.0f;
    in.x_m = 2.0f;
    in.z_m = 2.0f;
    in.vbat_v = 12.0f;
    in.hb_age_s = 2.0f;
    in.dt_s = 130.0f;
    in.gs_flight_active = 1;
    act = wfb_safety_step(&s, &lim, &in);
    CHECK(act == WFB_ACT_KILL);
    CHECK(s.trip == (uint8_t)WFB_TRIP_TILT);
}

int main(void)
{
    test_1_airborne_zero();
    test_2_tilt();
    test_3_fence_ceiling();
    test_3b_push_sp();
    test_4_low_v();
    test_5_heartbeat();
    test_6_airborne_cap();
    test_7_latch_and_escalation();
    test_8_default_limits();
    test_9_fail_safe_on_bad_numbers();
    test_10_same_step_ties();

    if (s_fail_count == 0) {
        printf("PASS %d\n", s_check_count);
        return 0;
    } else {
        printf("FAIL: %d checks failed out of %d\n", s_fail_count, s_check_count);
        return 1;
    }
}
