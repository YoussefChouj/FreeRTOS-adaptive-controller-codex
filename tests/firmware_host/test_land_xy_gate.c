#include "land_xy_gate.h"
#include <stdio.h>
#include <math.h>

#define ASSERT(cond) do { if (!(cond)) { printf("FAIL %s:%d\n", __FILE__, __LINE__); return 1; } } while(0)

static LandXyGateIn base(void) {
    LandXyGateIn in = { 10.0f, 0.0f, 1U, 0U, 100U };
    return in;
}

static int test_off_cases(void) {
    LandXyGateIn in = base();
    in.err_cm = 50.0f; in.gate_cm = 0.0f;
    ASSERT(LandXyGate_Check(&in) == LAND_XY_GATE_OFF);             /* VAR 46 = 0 */
    in.gate_cm = NAN;
    ASSERT(LandXyGate_Check(&in) == LAND_XY_GATE_OFF);
    in = base(); in.err_cm = 50.0f; in.fused = 0U;
    ASSERT(LandXyGate_Check(&in) == LAND_XY_GATE_OFF);             /* fusion 0/1 */
    in.stale = 1U;
    ASSERT(LandXyGate_Check(&in) == LAND_XY_GATE_OFF);             /* off wins over stale */
    return 0;
}

static int test_radius(void) {
    LandXyGateIn in = base();
    in.err_cm = 5.0f;   ASSERT(LandXyGate_Check(&in) == LAND_XY_GATE_OK);
    in.err_cm = 10.0f;  ASSERT(LandXyGate_Check(&in) == LAND_XY_GATE_OK);     /* on the edge */
    in.err_cm = 10.1f;  ASSERT(LandXyGate_Check(&in) == LAND_XY_GATE_WAIT);
    in.err_cm = NAN;    ASSERT(LandXyGate_Check(&in) == LAND_XY_GATE_WAIT);
    return 0;
}

static int test_freshness(void) {
    LandXyGateIn in = base();
    in.fix_age_ms = LAND_XY_GATE_FRESH_MS;  ASSERT(LandXyGate_Check(&in) == LAND_XY_GATE_WAIT);   /* old fix */
    in.fix_age_ms = LAND_XY_GATE_FRESH_MS - 1U;  ASSERT(LandXyGate_Check(&in) == LAND_XY_GATE_OK);
    in.stale = 1U;                          ASSERT(LandXyGate_Check(&in) == LAND_XY_GATE_WAIT);   /* camera lost */
    return 0;
}

int main(void) {
    if (test_off_cases() || test_radius() || test_freshness())
        return 1;
    printf("land_xy_gate: 3 tests passed\n");
    return 0;
}
