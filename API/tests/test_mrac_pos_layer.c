/* API/tests/test_mrac_pos_layer.c - host test of the D4 x/y MRAC layers (spec 2026-10-22 item 5, API/mrac.c
 * MRAC_PosStep / MRAC_XyLayersLatch, CMD 0x1D field 31 xy_layers).
 *
 * A. Boot: target and live = INNER; MRAC_PosStep returns 0 and its model tracks the plant.
 * B. CMD 0x1D field 31: accepted on axes 0 and 4 (any axis), 4 refused. A write changes the target only; live
 *    follows at the ground latch, which also clears the outer state.
 * C. Outer live, plant held 0.1 m past the demand: e > 0 and u goes negative (pushes back), returned = mrac_pos.u.
 * D. Limits: a 5 m error for 30 s keeps |W_k| <= w_max and |u| <= u_max, and u saturates at -u_max.
 * E. on = 0: returns 0, the model takes the plant state, W frozen. NaN input: returns 0, state cleared. i = 2: 0.
 * F. Inner gate through MRAC_Control: x u_ad learns with INNER live and stays exactly 0 without it.
 * G. The row values sit in the legend bounds.
 *
 * Built by tools/host_tests.py, row mrac_pos_layer (-DMRAC_VARIANT=2), on a copy of API/mrac*.[ch] so the stubs win:
 *   gcc -std=c99 -Wall -Wextra -DMRAC_VARIANT=2 -I<copy> -IAPI/tests/stubs API/tests/test_mrac_pos_layer.c
 *       <copy>/mrac.c <copy>/mrac_math.c -lm
 */
#include <math.h>
#include <stdio.h>
#include <string.h>

#include "mrac.h"

_imu_st imu_data;

static int checks, fails;
static void ok(const char *what, int cond)
{
    checks++;
    if (!cond) {
        fails++;
        printf("FAIL %s\n", what);
    }
}

static CtrlerTypeDef ctrl;

static void set_live(uint8_t bits)
{
    ok("field 31 write accepted", MRAC_VariantParamSet(MRAC_AXIS_X, MRAC_VF_XY_LAYERS, (float)bits) == 1U);
    MRAC_XyLayersLatch();
}

/* Flying, learn-gated, injected (as API/tests/test_mrac_l2_gate.c), x axis on; returns x u_ad after n ticks. */
static float fly_x(uint8_t bits, int n)
{
    int i;
    MRAC_Init();
    memset(&mrac_state, 0, sizeof(mrac_state));
    memset(&mrac_flags, 0, sizeof(mrac_flags));
    mrac_flags.adaptation_on = 1;
    mrac_flags.axis_enable_x = 1;
    mrac_flags.output_injection_on = 1;
    mrac_in_armed = 1;
    mrac_in_phase = MRAC_PHASE_FLYING;
    mrac_inj.fly_ticks = 65535;
    mrac_inj.prev_armed = 1;
    mrac_inj.prev_injection_on = 1;
    mrac_inj.ramp_p = 1.0f;
    mrac_inj.inj_alpha = 1.0f;
    mrac_inj.learn_gate = 1;
    mrac_simplex.mode = 0;
    mrac_simplex.tripped = 0;
    mrac_simplex.roll_max = 3.14f;
    mrac_simplex.pitch_max = 3.14f;
    memset(&ctrl, 0, sizeof(ctrl));
    memset(&imu_data, 0, sizeof(imu_data));
    set_live(bits);
    for (i = 0; i < n; i++) {
        ctrl.locxsPID.FB = 0.0f;
        ctrl.locxsPID.Des = 20.0f;
        MRAC_Control(&ctrl);
    }
    return mrac_state.x_vel.u_ad;
}

int main(void)
{
    const MRAC_PosConfig_t *c = &mrac_pos_cfg;
    float u, prev, wmax_seen, umax_seen;
    int i, k, track;

    /* A. boot */
    MRAC_Init();
    ok("A boot target INNER", mrac_xy_layers == MRAC_XY_LAYER_INNER);
    ok("A boot live INNER", mrac_xy_layers_live == MRAC_XY_LAYER_INNER);
    track = 1;
    for (i = 0; i < 200; i++) {
        u = MRAC_PosStep(0U, 1U, 0.0f, 0.3f, 0.2f, 0.1f);
        if (u != 0.0f || mrac_pos[0].p_m != 0.3f || mrac_pos[0].v_m != 0.2f || mrac_pos[0].W[0] != 0.0f) track = 0;
    }
    ok("A outer off at boot: u 0, model = plant, W 0", track);

    /* B. field 31 */
    ok("B field 31 on axis 0", MRAC_VariantParamSet(0U, MRAC_VF_XY_LAYERS, 3.0f) == 1U);
    ok("B field 31 on axis 4", MRAC_VariantParamSet(MRAC_AXIS_X, MRAC_VF_XY_LAYERS, 2.0f) == 1U);
    ok("B value 4 refused", MRAC_VariantParamSet(MRAC_AXIS_X, MRAC_VF_XY_LAYERS, 4.0f) == 0U);
    ok("B NaN refused", MRAC_VariantParamSet(MRAC_AXIS_X, MRAC_VF_XY_LAYERS, NAN) == 0U);
    ok("B target 2", mrac_xy_layers == 2U);
    ok("B live unchanged before the latch", mrac_xy_layers_live == MRAC_XY_LAYER_INNER);
    mrac_pos[1].W[2] = 1.5f;
    mrac_pos[1].u = 0.2f;
    MRAC_XyLayersLatch();
    ok("B latch copies the target", mrac_xy_layers_live == MRAC_XY_LAYER_OUTER);
    ok("B latch clears the outer state", mrac_pos[1].W[2] == 0.0f && mrac_pos[1].u == 0.0f);

    /* C. sign: plant 0.1 m past a 0 demand, model starts at the plant and decays to the demand */
    MRAC_Init();
    set_live(MRAC_XY_LAYER_INNER | MRAC_XY_LAYER_OUTER);
    MRAC_PosStep(0U, 0U, 0.0f, 0.1f, 0.0f, 0.0f);
    prev = 0.0f;
    track = 1;
    for (i = 0; i < 300; i++) {
        u = MRAC_PosStep(0U, 1U, 0.0f, 0.1f, 0.0f, 0.0f);
        if (u != mrac_pos[0].u) track = 0;
        if (i > 0 && u > prev) track = 0;
        prev = u;
    }
    ok("C e > 0", mrac_pos[0].e > 0.0f);
    ok("C u < 0 after 3 s", u < 0.0f);
    ok("C u non-increasing, returned = mrac_pos.u", track);
    ok("C y untouched", mrac_pos[1].u == 0.0f && mrac_pos[1].W[0] == 0.0f);

    /* D. limits */
    wmax_seen = 0.0f;
    umax_seen = 0.0f;
    for (i = 0; i < 3000; i++) {
        u = MRAC_PosStep(0U, 1U, 0.0f, 5.0f, 0.0f, 3.0f);
        for (k = 0; k < 3; k++)
            if (fabsf(mrac_pos[0].W[k]) > wmax_seen) wmax_seen = fabsf(mrac_pos[0].W[k]);
        if (fabsf(u) > umax_seen) umax_seen = fabsf(u);
    }
    ok("D |W| <= w_max", wmax_seen <= c->w_max);
    ok("D |u| <= u_max", umax_seen <= c->u_max);
    ok("D u saturates at -u_max", u == -c->u_max);

    /* E. off / NaN / bad axis */
    {
        float w0 = mrac_pos[0].W[0];
        u = MRAC_PosStep(0U, 0U, 0.0f, 1.25f, -0.5f, 0.0f);
        ok("E off returns 0", u == 0.0f && mrac_pos[0].u == 0.0f);
        ok("E off: model = plant", mrac_pos[0].p_m == 1.25f && mrac_pos[0].v_m == -0.5f && mrac_pos[0].e == 0.0f);
        ok("E off: W frozen", mrac_pos[0].W[0] == w0 && w0 != 0.0f);
    }
    u = MRAC_PosStep(0U, 1U, 0.0f, NAN, 0.0f, 0.0f);
    ok("E NaN returns 0, state cleared", u == 0.0f && mrac_pos[0].W[0] == 0.0f && mrac_pos[0].W[1] == 0.0f &&
                                         mrac_pos[0].W[2] == 0.0f && mrac_pos[0].p_m == 0.0f);
    ok("E axis 2 returns 0", MRAC_PosStep(2U, 1U, 0.0f, 1.0f, 0.0f, 0.0f) == 0.0f);
    set_live(MRAC_XY_LAYER_INNER);
    for (i = 0; i < 10; i++) u = MRAC_PosStep(0U, 1U, 0.0f, 5.0f, 0.0f, 3.0f);
    ok("E INNER only: outer stays 0", u == 0.0f && mrac_pos[0].W[0] == 0.0f);

    /* F. inner gate */
    u = fly_x(MRAC_XY_LAYER_INNER, 400);
    ok("F INNER live: x u_ad learns", u != 0.0f && u - u == 0.0f);
    ok("F OUTER only: x u_ad exactly 0", fly_x(MRAC_XY_LAYER_OUTER, 400) == 0.0f);
    ok("F none: x u_ad exactly 0", fly_x(0U, 400) == 0.0f);

    /* G. row bounds (legend above MRAC_POS_ROW in API/mrac.c) */
    MRAC_Init();
    ok("G gamma [0,20]", c->gamma >= 0.0f && c->gamma <= 20.0f);
    ok("G sigma [0,1]", c->sigma >= 0.0f && c->sigma <= 1.0f);
    ok("G w_max [0,10]", c->w_max >= 0.0f && c->w_max <= 10.0f);
    ok("G u_max [0,1]", c->u_max >= 0.0f && c->u_max <= 1.0f);
    ok("G bw [0.1,10]", c->bw >= 0.1f && c->bw <= 10.0f);
    ok("G zeta [0.1,2]", c->zeta >= 0.1f && c->zeta <= 2.0f);

    printf("%s: %d/%d checks\n", fails ? "FAIL" : "PASS", checks - fails, checks);
    return fails ? 1 : 0;
}
