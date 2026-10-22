/* API/tests/test_mrac_xtlm.c - host test of the experiment telemetry block (mrac_xt, API/mrac.c MRAC_XtUpdate).
 *
 * Driven through MRAC_Control on the pitch axis, basis S10X, persistent rate error (FB 0, Des 20 deg/s), as
 * API/tests/test_mrac_l2_gate.c:
 * A. Layout: MRAC_XT_WORDS is 81 on the MULTI build, MRAC_Init clears the block and the group EMA.
 * B. Scales: e and u_ad are the axis state x 1000, rounded to nearest.
 * C. Groups: learning then frozen (adaptation off, constant input), the group words sum to u_ad (x 1000) within
 *    the rounding of the 8 words, and more than one group carries a share.
 * D. Saturation: a huge rate error reads +32767 / -32767, never wraps.
 * E. Trap bits: after the L2 noise run (mode 1), bit i%16 of word i/16 equals mrac_l2g_trapped for every
 *    feature on every axis, and ext 0 is set.
 * F. Pattern: MRAC_XtSetPattern round-trips, MRAC_Control does not touch it.
 *
 * Built by tools/host_tests.py, row mrac_xtlm (-DMRAC_VARIANT=2), on a copy of API/mrac*.[ch] so the stubs win:
 *   gcc -std=c99 -Wall -Wextra -DMRAC_VARIANT=2 -I<copy> -IAPI/tests/stubs API/tests/test_mrac_xtlm.c
 *       <copy>/mrac.c <copy>/mrac_math.c -lm
 */
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "mrac.h"
#include "mrac_math.h"

_imu_st imu_data;

#define EXT(k)  (MRAC_N_STRUCT + (k))
#define P       MRAC_AXIS_PITCH

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
static uint32_t lcg;

/* Flying, learn-gated, injected (as API/tests/test_mrac_l2_gate.c), pitch basis S10X, then the L2 mode. */
static void fly(float mode)
{
    MRAC_Init();
    memset(&mrac_state, 0, sizeof(mrac_state));
    memset(&mrac_flags, 0, sizeof(mrac_flags));
    mrac_flags.adaptation_on = 1;
    mrac_flags.axis_enable_pitch = 1;
    mrac_flags.axis_enable_roll = 1;
    mrac_flags.axis_enable_yaw = 1;
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
    mrac_simplex.reason = 0;
    mrac_simplex.would_trip_count = 0;
    mrac_simplex.roll_max = 3.14f;
    mrac_simplex.pitch_max = 3.14f;
    memset(&ctrl, 0, sizeof(ctrl));
    memset(&imu_data, 0, sizeof(imu_data));
    MRAC_VariantParamSet(P, MRAC_VF_BASIS, 6.0f);
    MRAC_VariantParamSet(P, MRAC_VF_L2_MODE, mode);
}

static float noise_deg(void)
{
    lcg = lcg * 1664525U + 1013904223U;
    return 20.0f * ((float)(lcg >> 8) / 16777216.0f - 0.5f);
}

/* pit_deg < -900: noise; else that constant angle. */
static void run(int ticks, float des, float pit_deg)
{
    int i;
    for (i = 0; i < ticks; i++) {
        ctrl.gyroyPID.FB = 0.0f;
        ctrl.gyroyPID.Des = des;
        imu_data.pit = (pit_deg < -900.0f) ? noise_deg() : pit_deg;
        imu_data.rol = 0.0f;
        MRAC_Control(&ctrl);
    }
}

static long q1000(float v)
{
    float q = v * 1000.0f;
    return (long)(q < 0.0f ? q - 0.5f : q + 0.5f);
}

static void test_layout_and_init(void)
{
    const int16_t *w = (const int16_t *)&mrac_xt;
    int i, zero = 1;
    ok("A MRAC_XT_WORDS == 81", MRAC_XT_WORDS == 81);
    ok("A struct has no padding", sizeof(MRAC_Xt_t) == 81U * sizeof(int16_t));
    fly(0.0f);
    run(400, 20.0f, 30.0f);
    MRAC_XtSetPattern(5U, 6U, 7U);
    fly(0.0f);
    for (i = 0; i < MRAC_XT_WORDS; i++)
        if (w[i] != 0) zero = 0;
    ok("A MRAC_Init clears the block", zero);
    run(1, 0.0f, 0.0f);
    zero = 1;
    for (i = 0; i < MRAC_N_GROUPS; i++)
        if (mrac_xt.grp[P][i] != 0) zero = 0;
    ok("A MRAC_Init clears the group EMA (zero input, zero groups)", zero);
}

static void test_scales_and_groups(void)
{
    long sum = 0;
    int g, shares = 0;
    fly(0.0f);
    run(1600, 20.0f, 30.0f);
    ok("B e word = round(e x 1000)", (long)mrac_xt.e[P] == q1000(mrac_state.pitch.e));
    ok("B u_ad word = round(u_ad x 1000)", (long)mrac_xt.u_ad[P] == q1000(mrac_state.pitch.u_ad));
    printf("info B: e %.4g -> %d, u_ad %.4g -> %d\n", (double)mrac_state.pitch.e, mrac_xt.e[P],
           (double)mrac_state.pitch.u_ad, mrac_xt.u_ad[P]);
    ok("B e word not zero (the block is filled)", mrac_xt.e[P] != 0);

    mrac_flags.adaptation_on = 0;                  /* Theta frozen, Phi constant: u_ad settles on raw u_ad */
    run(800, 20.0f, 30.0f);
    for (g = 0; g < MRAC_N_GROUPS; g++) {
        sum += mrac_xt.grp[P][g];
        if (mrac_xt.grp[P][g] != 0) shares++;
    }
    printf("info C: groups sum %ld, u_ad word %d, %d group(s) non-zero, u_max %.3g\n", sum, mrac_xt.u_ad[P],
           shares, (double)mrac_config_pitch.u_max);
    ok("C u_ad not clamped (the comparison is pre-clamp)", fabsf(mrac_state.pitch.u_ad) < mrac_config_pitch.u_max);
    ok("C groups sum to u_ad within 8 roundings", labs(sum - (long)mrac_xt.u_ad[P]) <= MRAC_N_GROUPS);
    ok("C more than one group carries a share", shares > 1);
}

static void test_saturation(void)
{
    fly(0.0f);
    run(1, 1.0e7f, 0.0f);
    printf("info D: e %.4g -> %d\n", (double)mrac_state.pitch.e, mrac_xt.e[P]);
    ok("D huge error saturates at a rail", mrac_xt.e[P] == 32767 || mrac_xt.e[P] == -32767);
    fly(0.0f);
    run(1, -1.0e7f, 0.0f);
    ok("D opposite huge error saturates at the other rail", mrac_xt.e[P] == 32767 || mrac_xt.e[P] == -32767);
    ok("D rails are +-32767 and opposite", (long)mrac_xt.e[P] * -1L ==
       (long)(mrac_state.pitch.e > 0.0f ? -32767 : 32767));
}

static void test_trap_bits(void)
{
    int a, i, same = 1;
    fly(1.0f);
    lcg = 12345U;
    run(1600, 20.0f, -999.0f);
    ok("E setup: ext 0 trapped", mrac_l2g_trapped[P][EXT(0)] == 1U);
    for (a = 0; a < AXES; a++)
        for (i = 0; i < MRAC_N_FEATURES; i++) {
            int bit = ((uint16_t)mrac_xt.trap[a][i / 16] >> (i % 16)) & 1;
            if (bit != (mrac_l2g_trapped[a][i] != 0U)) same = 0;
        }
    ok("E trap bits equal mrac_l2g_trapped on every axis", same);
    ok("E ext 0 bit set", (((uint16_t)mrac_xt.trap[P][EXT(0) / 16] >> (EXT(0) % 16)) & 1) == 1);
}

static void test_pattern(void)
{
    fly(0.0f);
    MRAC_XtSetPattern(0x1FFU, 12000U, 9U);
    run(10, 20.0f, 30.0f);
    ok("F pattern round-trips through MRAC_Control", mrac_xt.pat == 0x1FF && mrac_xt.phase == 12000
       && mrac_xt.period == 9);
}

int main(void)
{
#if MRAC_VARIANT == MRAC_VARIANT_MULTI
    test_layout_and_init();
    test_scales_and_groups();
    test_saturation();
    test_trap_bits();
    test_pattern();
#else
    printf("experiment telemetry test runs on the MULTI build (MRAC_VARIANT=2)\n");
#endif
    printf("%d checks, %d failure(s)\n", checks, fails);
    return fails ? 1 : 0;
}
