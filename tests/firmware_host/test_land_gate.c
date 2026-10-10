/* Auto-land gate: low-V hold over 1 Hz readings, motor-pin hold, dead ADC, NaN, enable bits, once per flight.
 *   gcc -std=c99 -Wall -Werror -I../../API test_land_gate.c ../../API/land_gate.c -o t && ./t */
#include <stdio.h>
#include <stdint.h>
#include <math.h>
#include "land_gate.h"

static int s_check_count = 0;
static int s_fail_count = 0;

#define CHECK(cond) do { \
    s_check_count++; \
    if (!(cond)) { \
        s_fail_count++; \
        printf("FAIL %s:%d: %s\n", __FILE__, __LINE__, #cond); \
    } \
} while (0)

#define DT 0.005f
/* hold timers sum float dt, so the trip tick may land one tick either side */
#define NEAR_TICK(a, b) ((a) >= (b) - 1 && (a) <= (b) + 1)

static land_gate_limits_t s_lim;

/* Runs n ticks at 200 Hz; returns the first trip seen and the tick it came on. */
static uint8_t run(land_gate_state_t *s, uint8_t en, float v, float m, uint8_t flying, int n, int *at)
{
    land_gate_in_t in;
    int i;
    in.vbat_v = v;
    in.motor_max = m;
    in.dt_s = DT;
    in.flying = flying;
    for (i = 0; i < n; i++) {
        uint8_t t = LandGate_Step(s, &s_lim, en, &in);
        if (t != LAND_GATE_NONE) {
            if (at != NULL) { *at = i; }
            return t;
        }
    }
    return LAND_GATE_NONE;
}

int main(void)
{
    land_gate_state_t s;
    uint8_t all = LAND_GATE_EN_LOW_V | LAND_GATE_EN_SAT;
    int at = -1;

    LandGate_DefaultLimits(&s_lim);
    CHECK(g_land_gate_enable == all);   /* on by default */

    /* healthy flight: nothing for a minute */
    LandGate_Reset(&s);
    CHECK(run(&s, all, 15.0f, 3500.0f, 1U, 12000, NULL) == LAND_GATE_NONE);

    /* one low 1 Hz reading (held 1 s) then a good one: no trip */
    LandGate_Reset(&s);
    CHECK(run(&s, all, 14.2f, 3500.0f, 1U, 200, NULL) == LAND_GATE_NONE);
    CHECK(run(&s, all, 14.4f, 3500.0f, 1U, 200, NULL) == LAND_GATE_NONE);
    /* two low readings in a row: trips 1.5 s in */
    CHECK(run(&s, all, 14.2f, 3500.0f, 1U, 400, &at) == LAND_GATE_LOW_V);
    CHECK(NEAR_TICK(at, 299));
    CHECK(g_land_gate_trip == LAND_GATE_LOW_V);
    /* fires once per flight */
    CHECK(run(&s, all, 14.0f, 4000.0f, 1U, 400, NULL) == LAND_GATE_NONE);
    /* landed (not FLYING) keeps the reason; next takeoff clears it */
    CHECK(run(&s, all, 14.0f, 2000.0f, 0U, 10, NULL) == LAND_GATE_NONE);
    CHECK(g_land_gate_trip == LAND_GATE_LOW_V);
    CHECK(run(&s, all, 15.5f, 3500.0f, 1U, 1, NULL) == LAND_GATE_NONE);
    CHECK(g_land_gate_trip == LAND_GATE_NONE);

    /* exactly at land_v is not low */
    LandGate_Reset(&s);
    CHECK(run(&s, all, 14.3f, 3500.0f, 1U, 1000, NULL) == LAND_GATE_NONE);

    /* dead ADC (0 V before the first sample) never lands; NaN does */
    LandGate_Reset(&s);
    CHECK(run(&s, all, 0.0f, 3500.0f, 1U, 1000, NULL) == LAND_GATE_NONE);
    LandGate_Reset(&s);
    CHECK(run(&s, all, NAN, 3500.0f, 1U, 1000, NULL) == LAND_GATE_LOW_V);

    /* motor pinned: log only. 240 ms bursts (vp15 above 14.2 V) are not counted, 350 ms runs are, none lands */
    LandGate_Reset(&s);
    CHECK(run(&s, all, 15.0f, 4000.0f, 1U, 48, NULL) == LAND_GATE_NONE);
    CHECK(g_land_gate_sat_n == 0U);
    CHECK(g_land_gate_sat_max_s > 0.235f && g_land_gate_sat_max_s < 0.245f);
    CHECK(run(&s, all, 15.0f, 3800.0f, 1U, 1, NULL) == LAND_GATE_NONE);
    CHECK(run(&s, all, 15.0f, 3990.0f, 1U, 400, NULL) == LAND_GATE_NONE);   /* one 2 s run counts once */
    CHECK(g_land_gate_sat_n == 1U);
    CHECK(g_land_gate_sat_max_s > 1.99f && g_land_gate_sat_max_s < 2.01f);
    CHECK(g_land_gate_trip == LAND_GATE_NONE);
    CHECK(run(&s, all, 15.0f, 3800.0f, 1U, 1, NULL) == LAND_GATE_NONE);
    CHECK(run(&s, all, 15.0f, 4000.0f, 1U, 80, NULL) == LAND_GATE_NONE);    /* a second 400 ms run */
    CHECK(g_land_gate_sat_n == 2U);
    CHECK(g_land_gate_sat_max_s > 1.99f && g_land_gate_sat_max_s < 2.01f);  /* the max is kept */

    /* pinning still counted after the low-V gate fired; low-V still lands while pinned */
    CHECK(run(&s, all, 15.0f, 3800.0f, 1U, 1, NULL) == LAND_GATE_NONE);
    CHECK(run(&s, all, 13.0f, 4000.0f, 1U, 400, &at) == LAND_GATE_LOW_V);
    CHECK(g_land_gate_trip == LAND_GATE_LOW_V);
    CHECK(g_land_gate_sat_n == 3U);

    /* landing then a new takeoff clears the counters */
    CHECK(run(&s, all, 15.0f, 3500.0f, 0U, 10, NULL) == LAND_GATE_NONE);
    CHECK(g_land_gate_sat_n == 3U);                                            /* kept on the ground for the log */
    CHECK(run(&s, all, 15.0f, 3500.0f, 1U, 1, NULL) == LAND_GATE_NONE);
    CHECK(g_land_gate_sat_n == 0U && g_land_gate_sat_max_s == 0.0f && g_land_gate_trip == LAND_GATE_NONE);

    /* the off flag: 0 disables both, single bits disable one */
    LandGate_Reset(&s);
    CHECK(run(&s, 0U, 13.0f, 4000.0f, 1U, 2000, NULL) == LAND_GATE_NONE);
    CHECK(g_land_gate_sat_n == 0U);                                            /* EN_SAT off: not counted */
    LandGate_Reset(&s);
    CHECK(run(&s, LAND_GATE_EN_SAT, 13.0f, 3500.0f, 1U, 2000, NULL) == LAND_GATE_NONE);
    LandGate_Reset(&s);
    CHECK(run(&s, LAND_GATE_EN_LOW_V, 15.0f, 4000.0f, 1U, 2000, NULL) == LAND_GATE_NONE);
    CHECK(g_land_gate_sat_n == 0U);

    /* on the ground nothing accumulates */
    LandGate_Reset(&s);
    CHECK(run(&s, all, 13.0f, 4000.0f, 0U, 2000, NULL) == LAND_GATE_NONE);

    if (s_fail_count == 0) {
        printf("test_land_gate: all passed (%d checks)\n", s_check_count);
        return 0;
    }
    printf("test_land_gate: %d of %d failed\n", s_fail_count, s_check_count);
    return 1;
}
