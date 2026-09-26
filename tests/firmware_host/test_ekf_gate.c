/* Host test for the EKF control-path gate (API/ekf.c Ekf9_GateStep & co).
 * gcc -std=c89 -pedantic -Wall -Werror -Istubs -I../../API test_ekf_gate.c ../../API/ekf.c -lm */
#include <stdio.h>
#include <stdint.h>
#include <assert.h>
#include "ekf.h"

volatile Ekf9Gate_t g_ekf_gate;

static void gate_defaults(volatile Ekf9Gate_t *g)
{
    g->bias_mode = EKF_BIAS_FIXED; g->bias_mode_req = EKF_BIAS_FIXED;
    g->ctrl_enable = 0U; g->ctrl_enable_req = 0U; g->reinit_req = 0U;
    g->healthy = 1U; g->bias_frozen = 0xFFU; g->fallback_count = 0U;
    g->vx_cms = 0.0f; g->vy_cms = 0.0f;
}

/* Same boot sequence as TASK/send_data.c: init, then freeze before the first step. */
static void boot(Ekf9_t *e, volatile Ekf9Gate_t *g, uint8_t active)
{
    Ekf9_Init(e, active);
    gate_defaults(g);
    Ekf9_SetBiasFrozen(e, 1U);
    g->bias_frozen = 1U;
}

/* One firmware tick: predict with a constant body accel, OF update, gate. */
static void tick(Ekf9_t *e, volatile Ekf9Gate_t *g, float ax, float ofx, uint8_t flying)
{
    Ekf9_Predict(e, ax, 0.0f, 9.81f, 0.0f, 0.0f, 0.0f, 0.01f);
    Ekf9_UpdateOf(e, ofx, 0.0f);
    Ekf9_GateStep(e, g, flying);
}

/* Legacy-vs-EKF source select, same expression as TASK/StabilizerTask.c. */
static float select_dx(volatile Ekf9Gate_t *g, float legacy)
{
    float fb_dx = legacy;
    if (g->ctrl_enable && g->healthy) fb_dx = g->vx_cms;
    return fb_dx;
}

static void test_fixed_freezes_bias(void)
{
    Ekf9_t e;
    int i;
    volatile Ekf9Gate_t *g = &g_ekf_gate;
    boot(&e, g, 1U);
    for (i = 0; i < 500; i++) tick(&e, g, 0.2f, 0.0f, 0U);   /* accel offset, OF says still */
    assert(g->bias_frozen == 1U);
    assert(e.x[3] == 0.0f && e.x[4] == 0.0f && e.x[6] == 0.0f);
    assert(e.K[3 * 2] == 0.0f && e.K[3 * 2 + 1] == 0.0f);   /* b_ax gain row */
    printf("PASS fixed mode: b_a stays 0 under a 0.2 m/s^2 accel offset\n");
}

static void test_online_learns_bias(void)
{
    Ekf9_t e;
    int i;
    volatile Ekf9Gate_t *g = &g_ekf_gate;
    boot(&e, g, 1U);
    g->bias_mode_req = EKF_BIAS_ONLINE;
    for (i = 0; i < 3000; i++) tick(&e, g, 0.2f, 0.0f, 0U);
    assert(g->bias_mode == EKF_BIAS_ONLINE && g->bias_frozen == 0U);
    assert(e.x[3] > 0.02f);                                   /* b_ax moved toward +0.2 */
    assert(e.x[6] == 0.0f);                                   /* b_g is uncoupled: never moves */
    printf("PASS online mode: b_ax -> %.4f, b_gx stays 0\n", (double)e.x[3]);
}

static void test_req_applied_on_ground_only(void)
{
    Ekf9_t e;
    volatile Ekf9Gate_t *g = &g_ekf_gate;
    boot(&e, g, 1U);
    tick(&e, g, 0.0f, 0.0f, 1U);
    g->bias_mode_req = EKF_BIAS_ONLINE; g->ctrl_enable_req = 1U; g->reinit_req = 1U;
    tick(&e, g, 0.0f, 0.0f, 1U);
    assert(g->bias_mode == EKF_BIAS_FIXED && g->ctrl_enable == 0U && g->reinit_req == 1U);
    tick(&e, g, 0.0f, 0.0f, 0U);
    assert(g->bias_mode == EKF_BIAS_ONLINE && g->ctrl_enable == 1U && g->reinit_req == 0U);
    g->bias_mode_req = 7U;                                    /* out of range: ignored */
    tick(&e, g, 0.0f, 0.0f, 0U);
    assert(g->bias_mode == EKF_BIAS_ONLINE);
    printf("PASS requests (mode, ctrl, reinit) apply on ground only; bad mode ignored\n");
}

static void test_gated_freezes_in_flight(void)
{
    Ekf9_t e;
    volatile Ekf9Gate_t *g = &g_ekf_gate;
    boot(&e, g, 1U);
    g->bias_mode_req = EKF_BIAS_GATED;
    tick(&e, g, 0.0f, 0.0f, 0U);
    assert(g->bias_frozen == 0U);
    tick(&e, g, 0.0f, 0.0f, 1U);
    assert(g->bias_frozen == 1U && e.Q_diag[3] == 0.0f);
    tick(&e, g, 0.0f, 0.0f, 0U);
    assert(g->bias_frozen == 0U && e.Q_diag[3] > 0.0f);
    printf("PASS gated mode: frozen while flying, released on ground\n");
}

static void test_nan_latches_fallback(void)
{
    Ekf9_t e;
    float zero = 0.0f;
    volatile Ekf9Gate_t *g = &g_ekf_gate;
    boot(&e, g, 1U);
    g->ctrl_enable_req = 1U;
    tick(&e, g, 0.0f, 0.3f, 0U);
    assert(g->healthy == 1U && select_dx(g, 99.0f) == g->vx_cms);
    e.x[0] = zero / zero;                                     /* NaN */
    Ekf9_GateStep(&e, g, 1U);
    assert(g->healthy == 0U && g->fallback_count == 1U);
    assert(select_dx(g, 99.0f) == 99.0f);                     /* controller back on legacy OF */
    Ekf9_GateStep(&e, g, 1U);
    assert(g->fallback_count == 1U);                          /* latched, counted once */
    e.x[0] = 6.0f;                                            /* > EKF_VEL_LIM_MPS */
    g->reinit_req = 1U;
    Ekf9_GateStep(&e, g, 1U);
    assert(g->healthy == 0U);                                 /* no reinit while flying */
    Ekf9_GateStep(&e, g, 0U);
    assert(g->healthy == 1U && e.x[0] == 0.0f && g->fallback_count == 1U);
    printf("PASS NaN -> fallback latched once, legacy source; reinit on ground only clears it\n");
}

static void test_inactive_filter_unhealthy(void)
{
    Ekf9_t e;
    volatile Ekf9Gate_t *g = &g_ekf_gate;
    boot(&e, g, 0U);
    Ekf9_GateStep(&e, g, 0U);
    assert(g->healthy == 0U);
    printf("PASS inactive filter reports unhealthy\n");
}

int main(void)
{
    test_fixed_freezes_bias();
    test_online_learns_bias();
    test_req_applied_on_ground_only();
    test_gated_freezes_in_flight();
    test_nan_latches_fallback();
    test_inactive_filter_unhealthy();
    printf("ALL PASS\n");
    return 0;
}
