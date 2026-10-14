#include "land_xy_gate.h"
#include <stdio.h>

#define ASSERT(cond) do { if (!(cond)) { printf("FAIL %s:%d\n", __FILE__, __LINE__); return 1; } } while(0)

static LandXyGateState s;

static LandXyGateIn base(void) {
    LandXyGateIn in = { 10.0f, 0.0f, 1.0f, 1U, 0U, 100U };
    return in;
}

static int test_off_cases(void) {
    LandXyGateIn in = base();
    in.err_cm = 50.0f;
    LandXyGate_Reset(&s);
    in.gate_cm = 0.0f;  ASSERT(LandXyGate_Step(&s, &in) == 0);   /* VAR 46 = 0 */
    in = base(); in.err_cm = 50.0f; in.fused = 0U;
    ASSERT(LandXyGate_Step(&s, &in) == 0);                         /* fusion 0/1 */
    in = base(); in.err_cm = 50.0f; in.z_m = 0.29f;
    ASSERT(LandXyGate_Step(&s, &in) == 0);                         /* below the floor */
    ASSERT(s.held == 0);
    return 0;
}

static int test_radius_hysteresis(void) {
    LandXyGateIn in = base();
    LandXyGate_Reset(&s);
    in.err_cm = 5.0f;   ASSERT(LandXyGate_Step(&s, &in) == 0);    /* inside: descend */
    in.err_cm = 12.0f;  ASSERT(LandXyGate_Step(&s, &in) == 1);    /* outside: pause */
    in.err_cm = 8.0f;   ASSERT(LandXyGate_Step(&s, &in) == 1);    /* between 6 and 10: still paused */
    in.err_cm = 5.9f;   ASSERT(LandXyGate_Step(&s, &in) == 0);    /* below gate * HYST: resume */
    in.err_cm = 8.0f;   ASSERT(LandXyGate_Step(&s, &in) == 0);    /* band again: keeps descending */
    ASSERT(s.held == 2);
    return 0;
}

static int test_freshness(void) {
    LandXyGateIn in = base();
    LandXyGate_Reset(&s);
    in.fix_age_ms = LAND_XY_GATE_FRESH_MS;  ASSERT(LandXyGate_Step(&s, &in) == 1);   /* old fix */
    in.fix_age_ms = 100U;                ASSERT(LandXyGate_Step(&s, &in) == 0);   /* fresh, on target */
    in.stale = 1U;                       ASSERT(LandXyGate_Step(&s, &in) == 1);   /* camera lost */
    return 0;
}

static int test_pause_cap(void) {
    LandXyGateIn in = base();
    unsigned i;
    LandXyGate_Reset(&s);
    in.err_cm = 50.0f;
    for (i = 0; i < LAND_XY_GATE_MAX_TICKS; i++)
        ASSERT(LandXyGate_Step(&s, &in) == 1);
    ASSERT(LandXyGate_Step(&s, &in) == 0);                         /* cap reached: commits */
    ASSERT(LandXyGate_Step(&s, &in) == 0);
    LandXyGate_Reset(&s);
    ASSERT(LandXyGate_Step(&s, &in) == 1);                         /* next landing pauses again */
    return 0;
}

int main(void) {
    if (test_off_cases() || test_radius_hysteresis() || test_freshness() || test_pause_cap())
        return 1;
    printf("land_xy_gate: 4 tests passed\n");
    return 0;
}
