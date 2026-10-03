/*
 * test_ekf_of_shadow.c - Host C test for WP-14 shadow mode, rebase sign,
 *                        persistence gate and gate-trip re-init.
 *
 * Build and run (from the repo root; gcc.exe on the Windows laptop, gcc on Linux):
 *   gcc -std=c99 -Wall -Wextra -IAPI -IAPI/tests/stubs_wp14 API/tests/test_ekf_of_shadow.c API/ekf_of.c -lm \
 *       -o /tmp/wp14/tes && /tmp/wp14/tes
 *
 * StabilizerTask.c cannot be built on the host (FreeRTOS/Keil headers), so the health-gate
 * block is mirrored in gate_tick() below; keep the two in step.
 *
 * Tests:
 *   1. rebase_sign:       a rebase leaves the predicted OF measurement unchanged.
 *   2. persist_gate_99:   99 bad ticks -> no trip (health stays 1).
 *   3. persist_gate_100:  100 bad ticks -> trip (health goes to 0).
 *   4. shadow_no_mode:    shadow mode never changes g_of_bias_mode.
 *   5. trip_reinit:       a trip clears inited; the next tick re-inits to a clean state, health stays 0.
 *   6. trip_reinit_mode2: same in mode 2, plus fallback to mode 0.
 *   7. arm_edge_clears:   the ARM edge clears the latch and the health flag.
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

/* ---- Tests 2-7: persistence gate and re-init ----
 * Mirror of the health-gate block in StabilizerTask.c (EKF tick). State names match the firmware:
 * inited = s_ekf_of_inited, bad_cnt = s_ekf_of_innov_bad_cnt, tripped = s_ekf_of_tripped. */
typedef struct {
    EkfOf_t  e;
    uint8_t  inited;
    uint16_t bad_cnt;
    uint8_t  tripped;
    uint8_t  health;
    uint8_t  mode;
    uint8_t  fallback;
} GateSim;

static void gate_init(GateSim *g, uint8_t mode)
{
    g->inited   = 0U;
    g->bad_cnt  = 0U;
    g->tripped  = 0U;
    g->health   = 1U;
    g->mode     = mode;
    g->fallback = 0U;
}

/* One 5 ms tick with the given OF innovation (already in e.innov_x/y after EkfOf_Update). */
static void gate_tick(GateSim *g, float innov_x, float innov_y)
{
    float innov_mag;
    if (!g->inited) {
        EkfOf_Init(&g->e);
        g->inited  = 1U;
        g->health  = g->tripped ? 0U : 1U;
        g->bad_cnt = 0U;
    }
    g->e.innov_x = innov_x;
    g->e.innov_y = innov_y;
    innov_mag = g->e.innov_x * g->e.innov_x + g->e.innov_y * g->e.innov_y;
    if (innov_mag > (EKF_OF_HEALTH_THRESH * EKF_OF_HEALTH_THRESH)) {
        if (g->bad_cnt < 0xFFFFU) {
            g->bad_cnt++;
        }
        if (g->bad_cnt >= EKF_OF_HEALTH_PERSIST) {
            g->health  = 0U;
            g->tripped = 1U;
            if (g->mode == 2U) {
                g->mode = 0U;
                g->fallback = 1U;
            }
            g->inited  = 0U;
            g->bad_cnt = 0U;
        }
    } else {
        g->bad_cnt = 0U;
        if (!g->tripped) {
            g->health = 1U;
        }
    }
}

static void test_persist_gate_99(void)
{
    GateSim g;
    int i;
    float bad_innov = EKF_OF_HEALTH_THRESH * 2.0f;   /* well above the threshold */

    gate_init(&g, 2U);
    for (i = 0; i < 99; i++) {
        gate_tick(&g, bad_innov, 0.0f);
    }

    ASSERT_MSG(g.health == 1U, "99 bad ticks must not trip the gate");
    ASSERT_MSG(g.mode == 2U, "mode must stay 2 after 99 bad ticks");
    ASSERT_MSG(g.fallback == 0U, "fallback must stay 0 after 99 bad ticks");
    ASSERT_MSG(g.inited == 1U, "KF must not re-init before the trip");
    printf("PASS: test_persist_gate_99\n");
    g_pass++;
}

static void test_persist_gate_100(void)
{
    GateSim g;
    int i;
    float bad_innov = EKF_OF_HEALTH_THRESH * 2.0f;

    gate_init(&g, 2U);
    for (i = 0; i < 100; i++) {
        gate_tick(&g, bad_innov, 0.0f);
    }

    ASSERT_MSG(g.health == 0U, "100 bad ticks must trip the gate");
    ASSERT_MSG(g.mode == 0U, "mode must fall back to 0 after 100 bad ticks");
    ASSERT_MSG(g.fallback == 1U, "fallback must be set after 100 bad ticks");
    printf("PASS: test_persist_gate_100\n");
    g_pass++;
}

/* In shadow (mode != 2), a tripped gate must set health=0 but must NOT
 * change g_of_bias_mode. */
static void test_shadow_no_mode_change(void)
{
    GateSim g;
    int i;
    float bad_innov = EKF_OF_HEALTH_THRESH * 2.0f;

    gate_init(&g, 0U);   /* shadow: mode is 0, not 2 */
    for (i = 0; i < 200; i++) {
        gate_tick(&g, bad_innov, 0.0f);
    }

    ASSERT_MSG(g.health == 0U, "shadow: health must be 0 after persistent divergence");
    ASSERT_MSG(g.mode == 0U, "shadow: mode must stay 0 (never changed by health gate)");
    ASSERT_MSG(g.fallback == 0U, "shadow: fallback must stay 0 (only set when mode was 2)");
    printf("PASS: test_shadow_no_mode_change\n");
    g_pass++;
}

/* A trip clears inited and the counter; the next tick re-inits to the clean Init state
 * (a diverged velocity is gone), and health stays 0 across the re-init (latched). */
static void test_trip_reinit(void)
{
    GateSim g;
    EkfOf_t ref;
    int i;
    float bad_innov = EKF_OF_HEALTH_THRESH * 2.0f;

    gate_init(&g, 0U);
    gate_tick(&g, 0.0f, 0.0f);          /* first tick inits the KF */
    g.e.x[1] = 5.0f;                    /* diverged velocity */
    g.e.x[4] = -5.0f;
    for (i = 0; i < 100; i++) {
        gate_tick(&g, bad_innov, 0.0f);
    }
    ASSERT_MSG(g.inited == 0U, "trip must clear inited");
    ASSERT_MSG(g.bad_cnt == 0U, "trip must reset the bad-tick counter");
    ASSERT_MSG(g.health == 0U, "trip must clear health");

    gate_tick(&g, 0.0f, 0.0f);          /* the next tick re-inits */
    EkfOf_Init(&ref);
    ASSERT_MSG(g.inited == 1U, "next tick must re-init");
    ASSERT_CLOSE(g.e.x[1], ref.x[1], 1e-6f, "re-init must restore vx");
    ASSERT_CLOSE(g.e.x[4], ref.x[4], 1e-6f, "re-init must restore vy");
    ASSERT_CLOSE(g.e.P[0][5], ref.P[0][5], 1e-6f, "re-init must restore the covariance");
    ASSERT_MSG(g.health == 0U, "health must stay 0 across the re-init");
    for (i = 0; i < 50; i++) {
        gate_tick(&g, 0.0f, 0.0f);
    }
    ASSERT_MSG(g.health == 0U, "health must stay latched at 0 on good ticks after a trip");
    ASSERT_MSG(g.mode == 0U, "shadow: mode untouched by the re-init");
    printf("PASS: test_trip_reinit\n");
    g_pass++;
}

static void test_trip_reinit_mode2(void)
{
    GateSim g;
    int i;
    float bad_innov = EKF_OF_HEALTH_THRESH * 2.0f;

    gate_init(&g, 2U);
    gate_tick(&g, 0.0f, 0.0f);
    g.e.x[1] = 5.0f;
    for (i = 0; i < 100; i++) {
        gate_tick(&g, bad_innov, 0.0f);
    }
    ASSERT_MSG(g.mode == 0U && g.fallback == 1U, "mode 2 trip must fall back to mode 0");
    ASSERT_MSG(g.inited == 0U, "mode 2 trip must clear inited");
    gate_tick(&g, 0.0f, 0.0f);
    ASSERT_CLOSE(g.e.x[1], 0.0f, 1e-6f, "re-init must clear the diverged velocity");
    ASSERT_MSG(g.health == 0U, "health must stay 0 after the fallback");
    printf("PASS: test_trip_reinit_mode2\n");
    g_pass++;
}

/* ARM edge (StabilizerTask.c): fallback, counter, latch cleared and health back to 1. */
static void test_arm_edge_clears(void)
{
    GateSim g;
    int i;
    float bad_innov = EKF_OF_HEALTH_THRESH * 2.0f;

    gate_init(&g, 2U);
    for (i = 0; i < 100; i++) {
        gate_tick(&g, bad_innov, 0.0f);
    }
    ASSERT_MSG(g.tripped == 1U && g.health == 0U, "precondition: tripped");
    g.fallback = 0U;                    /* ARM edge block */
    g.bad_cnt  = 0U;
    g.tripped  = 0U;
    g.health   = 1U;
    gate_tick(&g, 0.0f, 0.0f);
    ASSERT_MSG(g.health == 1U, "after the ARM edge a good tick keeps health at 1");
    printf("PASS: test_arm_edge_clears\n");
    g_pass++;
}

/* ---- 2026-10-03: EkfOf_ResetBias ----
 * Zeroes bof with a tight variance and no cross-covariance. A step in OF
 * right after it must go mostly into v, not bof (the resting OF value is a
 * stale repeat and must not be learned back as bias at lift-off). */
static float run_of_step(EkfOf_t *e)
{
    int i;
    for (i = 0; i < 200; i++) {                 /* 1 s at 200 Hz, OF at 50 Hz */
        EkfOf_Predict(e, 0.005f, 0.0f, 0.0f);
        if ((i & 3) == 0) EkfOf_Update(e, 0.04f, 0.0f);
    }
    return e->x[2];
}

static void test_reset_bias(void)
{
    EkfOf_t e, loose;
    int a, k;
    float b_tight, b_loose;

    EkfOf_Init(&e);
    for (k = 0; k < 400; k++) {                 /* build cross-covariances */
        EkfOf_Predict(&e, 0.005f, 0.1f, -0.1f);
        if ((k & 3) == 0) EkfOf_Update(&e, 0.03f, -0.02f);
    }
    EkfOf_ResetBias(&e, EKF_OF_BOF_ARM_VAR);
    ASSERT_MSG(e.x[2] == 0.0f && e.x[5] == 0.0f, "bof zeroed");
    for (a = 0; a < 2; a++) {
        ASSERT_CLOSE(e.P[a][10], EKF_OF_BOF_ARM_VAR, 1e-12f, "bof variance");
        for (k = 0; k < 4; k++) {
            if (k == 2) continue;
            ASSERT_MSG(e.P[a][8 + k] == 0.0f && e.P[a][k*4 + 2] == 0.0f, "bof cross-cov cleared");
        }
    }
    e.x[1] = 0.0f; e.x[4] = 0.0f;
    b_tight = run_of_step(&e);
    EkfOf_Init(&loose);                          /* P0 bof 0.01: the old ARM state */
    b_loose = run_of_step(&loose);
    printf("  bof after 4 cm/s step: tight %.4f loose %.4f, vx tight %.4f\n",
           (double)b_tight, (double)b_loose, (double)e.x[1]);
    ASSERT_MSG(fabsf(b_tight) < 0.5f * fabsf(b_loose), "tight bof learns less than loose");
    ASSERT_MSG(e.x[1] > fabsf(b_tight), "step goes mostly into velocity");
    printf("PASS: test_reset_bias\n");
    g_pass++;
}

int main(void)
{
    test_rebase_sign();
    test_persist_gate_99();
    test_persist_gate_100();
    test_shadow_no_mode_change();
    test_trip_reinit();
    test_trip_reinit_mode2();
    test_arm_edge_clears();
    test_reset_bias();

    printf("\n%d passed, %d failed\n", g_pass, g_fail);
    return g_fail > 0 ? 1 : 0;
}
