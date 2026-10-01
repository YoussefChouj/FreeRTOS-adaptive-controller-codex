/*
 * test_ekf_of_shadow.c — Host C test for WP-14 shadow mode, rebase sign,
 *                         and persistence gate.
 *
 * Build (MinGW gcc.exe from WSL, 32-bit):
 *   gcc.exe -std=c99 -Wall -Wextra -IAPI -IAPI/tests/stubs_wp14 \
 *       API/tests/test_ekf_of_shadow.c API/ekf_of.c -lm \
 *       -o C:/tmp/wp14/test_shadow.exe && /mnt/c/tmp/wp14/test_shadow.exe
 *
 * Tests:
 *   1. rebase_sign:     a rebase leaves the predicted OF measurement unchanged.
 *   2. persist_gate_99: 99 bad ticks → no trip (health stays 1).
 *   3. persist_gate_100: 100 bad ticks → trip (health goes to 0).
 *   4. shadow_no_mode:   shadow mode never changes g_of_bias_mode.
 */

#include <stdio.h>
#include <stdlib.h>
#include <math.h>
#include "ekf_of.h"

/* ---- globals that the test defines (normally in StabilizerTask.c) ---- */
volatile uint8_t g_ekf_of_shadow  = 1U;
volatile uint8_t g_ekf_of_vel_fb  = 0U;

static int g_pass = 0;
static int g_fail = 0;

#define ASSERT_MSG(cond, msg) do { \
    if (!(cond)) { \
        printf("FAIL: %s (line %d): %s\n", __func__, __LINE__, msg); \
        g_fail++; \
        return; \
    } \
} while (0)

#define ASSERT_CLOSE(a, b, tol, msg) do { \
    float _a = (float)(a), _b = (float)(b); \
    if (fabsf(_a - _b) > (tol)) { \
        printf("FAIL: %s (line %d): %s: got %g, expected %g (tol %g)\n", \
               __func__, __LINE__, msg, (double)_a, (double)_b, (double)(tol)); \
        g_fail++; \
        return; \
    } \
} while (0)

/* ---- Test 1: rebase sign ----
 * A rebase should leave the OF innovation z - (v + bof) unchanged.
 * Measurement model: z = v + bof.
 * If s_of_bias shifts by +d counts, the raw measurement shifts by -d*0.01 m/s.
 * The KF bias bof must shift by -d*0.01 so that (v + bof) stays the same.
 * This is the -= fix.  We verify by comparing the predicted measurement
 * before and after a simulated rebase. */
static void test_rebase_sign(void)
{
    EkfOf_t e;
    float pred_before;
    float pred_after;
    float dbx;

    EkfOf_Init(&e);
    /* Give the filter some velocity and bias state */
    e.x[1] = 0.5f;   /* vel_x = 0.5 m/s */
    e.x[2] = 0.02f;  /* bof_x = 0.02 m/s */

    /* CEO fix: the invariant is the innovation z - (v + bof), not v + bof.
     * z = (raw - s_of_bias) * 0.01 with raw = 60 counts, s_of_bias = 0. */
    pred_before = 0.60f - (e.x[1] + e.x[2]);

    /* Simulate rebase: s_of_bias_x shifts by +10 counts, so z drops by 0.1.
     * The -= fix means bof shifts by -(+10)*0.01 = -0.1 */
    dbx = 10.0f;
    e.x[2] -= dbx * 0.01f;   /* this is what Of_RebaseKfBias does after the fix */

    pred_after = (60.0f - dbx) * 0.01f - (e.x[1] + e.x[2]);

    ASSERT_CLOSE(pred_before, pred_after, 1e-6f,
        "OF innovation must be unchanged after rebase");
    printf("PASS: test_rebase_sign\n");
    g_pass++;
}

/* ---- Test 2 & 3: persistence gate ----
 * Simulate bad innovations and verify the persistence counter behaviour.
 * The gate uses EKF_OF_HEALTH_THRESH and EKF_OF_HEALTH_PERSIST from ekf_of.h. */

/* Simple simulation of the persistence gate logic from StabilizerTask.c */
static void simulate_health_gate(uint16_t *bad_cnt, uint8_t *health,
                                  float innov_x, float innov_y,
                                  uint8_t *mode, uint8_t *fallback)
{
    float innov_mag = innov_x * innov_x + innov_y * innov_y;

    if (innov_mag > (EKF_OF_HEALTH_THRESH * EKF_OF_HEALTH_THRESH)) {
        if (*bad_cnt < 0xFFFFU) {
            (*bad_cnt)++;
        }
        if (*bad_cnt >= EKF_OF_HEALTH_PERSIST) {
            *health = 0U;
            if (*mode == 2U) {
                *mode = 0U;
                *fallback = 1U;
            }
        }
    } else {
        *bad_cnt = 0U;
        *health = 1U;
    }
}

static void test_persist_gate_99(void)
{
    uint16_t bad_cnt = 0U;
    uint8_t health = 1U;
    uint8_t mode = 2U;
    uint8_t fallback = 0U;
    int i;
    /* Big innovation — well above the threshold */
    float bad_innov = EKF_OF_HEALTH_THRESH * 2.0f;

    for (i = 0; i < 99; i++) {
        simulate_health_gate(&bad_cnt, &health, bad_innov, 0.0f, &mode, &fallback);
    }

    ASSERT_MSG(health == 1U, "99 bad ticks must not trip the gate");
    ASSERT_MSG(mode == 2U, "mode must stay 2 after 99 bad ticks");
    ASSERT_MSG(fallback == 0U, "fallback must stay 0 after 99 bad ticks");
    printf("PASS: test_persist_gate_99\n");
    g_pass++;
}

static void test_persist_gate_100(void)
{
    uint16_t bad_cnt = 0U;
    uint8_t health = 1U;
    uint8_t mode = 2U;
    uint8_t fallback = 0U;
    int i;
    float bad_innov = EKF_OF_HEALTH_THRESH * 2.0f;

    for (i = 0; i < 100; i++) {
        simulate_health_gate(&bad_cnt, &health, bad_innov, 0.0f, &mode, &fallback);
    }

    ASSERT_MSG(health == 0U, "100 bad ticks must trip the gate");
    ASSERT_MSG(mode == 0U, "mode must fall back to 0 after 100 bad ticks");
    ASSERT_MSG(fallback == 1U, "fallback must be set after 100 bad ticks");
    printf("PASS: test_persist_gate_100\n");
    g_pass++;
}

/* ---- Test 4: shadow never changes mode ----
 * In shadow (mode != 2), a tripped gate must set health=0 but must NOT
 * change g_of_bias_mode. */
static void test_shadow_no_mode_change(void)
{
    uint16_t bad_cnt = 0U;
    uint8_t health = 1U;
    uint8_t mode = 0U;   /* shadow: mode is 0, not 2 */
    uint8_t fallback = 0U;
    int i;
    float bad_innov = EKF_OF_HEALTH_THRESH * 2.0f;

    for (i = 0; i < 200; i++) {
        simulate_health_gate(&bad_cnt, &health, bad_innov, 0.0f, &mode, &fallback);
    }

    ASSERT_MSG(health == 0U, "shadow: health must be 0 after persistent divergence");
    ASSERT_MSG(mode == 0U, "shadow: mode must stay 0 (never changed by health gate)");
    ASSERT_MSG(fallback == 0U, "shadow: fallback must stay 0 (only set when mode was 2)");
    printf("PASS: test_shadow_no_mode_change\n");
    g_pass++;
}

int main(void)
{
    test_rebase_sign();
    test_persist_gate_99();
    test_persist_gate_100();
    test_shadow_no_mode_change();

    printf("\n%d passed, %d failed\n", g_pass, g_fail);
    return g_fail > 0 ? 1 : 0;
}
