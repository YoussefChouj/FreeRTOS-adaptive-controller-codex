/* WP-40 pre-arm checks: each check alone, mask bits, first failure, report-only default, NaN fails. */
#include <stdio.h>
#include <stdint.h>
#include <math.h>
#include "prearm.h"

static int s_check_count = 0;
static int s_fail_count = 0;

#define CHECK(cond) do { \
    s_check_count++; \
    if (!(cond)) { \
        s_fail_count++; \
        printf("FAIL %s:%d: %s\n", __FILE__, __LINE__, #cond); \
    } \
} while (0)

#define BIT(b) ((uint16_t)(1U << (b)))

static prearm_in_t nominal(void)
{
    prearm_in_t in;
    in.estimator_ready = 1U;
    in.vbat_v = 15.8f;
    in.rc_live = 1U;
    in.roll_deg = 1.0f;
    in.pitch_deg = -1.5f;
    in.safety_trip = 0U;
    in.stab_fps = 200U;
    return in;
}

static void test_nominal_passes(void)
{
    prearm_in_t in = nominal();
    g_prearm_enable_mask = 0x3FU;
    CHECK(PreArm_Evaluate(&in) == 0U);
    CHECK(g_prearm_fail_mask == 0U && g_prearm_block_mask == 0U);
    CHECK(g_prearm_first_fail == PREARM_FIRST_NONE);
    CHECK(PreArm_Allows() == 1U);
}

static void test_each_check_alone(void)
{
    prearm_limits_t lim;
    prearm_in_t in;
    PreArm_DefaultLimits(&lim);
    g_prearm_enable_mask = 0x3FU;

    in = nominal(); in.estimator_ready = 0U;
    CHECK(PreArm_Evaluate(&in) == BIT(PREARM_BIT_ESTIMATOR));
    in = nominal(); in.vbat_v = lim.vbat_min_v - 0.01f;
    CHECK(PreArm_Evaluate(&in) == BIT(PREARM_BIT_VBAT));
    in = nominal(); in.vbat_v = lim.vbat_min_v;                 /* at the limit passes */
    CHECK(PreArm_Evaluate(&in) == 0U);
    in = nominal(); in.rc_live = 0U;
    CHECK(PreArm_Evaluate(&in) == BIT(PREARM_BIT_RC));
    in = nominal(); in.roll_deg = -lim.tilt_max_deg;            /* at the limit fails: strict < */
    CHECK(PreArm_Evaluate(&in) == BIT(PREARM_BIT_LEVEL));
    in = nominal(); in.pitch_deg = lim.tilt_max_deg + 5.0f;
    CHECK(PreArm_Evaluate(&in) == BIT(PREARM_BIT_LEVEL));
    in = nominal(); in.safety_trip = 1U;
    CHECK(PreArm_Evaluate(&in) == BIT(PREARM_BIT_TRIP));
    in = nominal(); in.stab_fps = (uint16_t)(lim.stab_fps_min - 1.0f);
    CHECK(PreArm_Evaluate(&in) == BIT(PREARM_BIT_STAB_FPS));
    CHECK(PreArm_Allows() == 0U);
}

static void test_first_fail_is_lowest_bit(void)
{
    prearm_in_t in = nominal();
    g_prearm_enable_mask = 0x3FU;
    in.rc_live = 0U;
    in.stab_fps = 0U;
    CHECK(PreArm_Evaluate(&in) == (BIT(PREARM_BIT_RC) | BIT(PREARM_BIT_STAB_FPS)));
    CHECK(g_prearm_first_fail == PREARM_BIT_RC);
}

static void test_default_table_reports_only(void)
{
    prearm_in_t in = nominal();
    g_prearm_enable_mask = 0U;                                  /* the shipped PREARM_ENABLE_ROW */
    in.estimator_ready = 0U;
    in.vbat_v = 9.0f;
    in.safety_trip = 1U;
    CHECK(PreArm_Evaluate(&in) == (BIT(PREARM_BIT_ESTIMATOR) | BIT(PREARM_BIT_VBAT) | BIT(PREARM_BIT_TRIP)));
    CHECK(g_prearm_block_mask == 0U);
    CHECK(PreArm_Allows() == 1U);
}

static void test_enable_mask_selects_blockers(void)
{
    prearm_in_t in = nominal();
    g_prearm_enable_mask = BIT(PREARM_BIT_VBAT);
    in.rc_live = 0U;                                            /* fails but not enabled */
    CHECK(PreArm_Evaluate(&in) == BIT(PREARM_BIT_RC));
    CHECK(PreArm_Allows() == 1U);
    in.vbat_v = 12.0f;
    PreArm_Evaluate(&in);
    CHECK(g_prearm_block_mask == BIT(PREARM_BIT_VBAT));
    CHECK(PreArm_Allows() == 0U);
}

static void test_nan_and_null_fail(void)
{
    prearm_in_t in = nominal();
    g_prearm_enable_mask = 0x3FU;
    in.vbat_v = NAN;
    in.roll_deg = NAN;
    CHECK(PreArm_Evaluate(&in) == (BIT(PREARM_BIT_VBAT) | BIT(PREARM_BIT_LEVEL)));
    CHECK(PreArm_Evaluate(NULL) == 0x3FU);
    CHECK(g_prearm_first_fail == PREARM_BIT_ESTIMATOR);
    CHECK(PreArm_Allows() == 0U);
}

static void test_shipped_defaults(void)
{
    prearm_limits_t lim;
    PreArm_DefaultLimits(&lim);
    CHECK(lim.vbat_min_v == 14.0f && lim.tilt_max_deg == 10.0f && lim.stab_fps_min == 180.0f);
}

int main(void)
{
    uint16_t shipped = g_prearm_enable_mask;
    CHECK(shipped == 0U);                                       /* report only until the operator enables */
    test_nominal_passes();
    test_each_check_alone();
    test_first_fail_is_lowest_bit();
    test_default_table_reports_only();
    test_enable_mask_selects_blockers();
    test_nan_and_null_fail();
    test_shipped_defaults();
    printf("test_prearm: %d checks, %d failed\n", s_check_count, s_fail_count);
    return s_fail_count == 0 ? 0 : 1;
}
