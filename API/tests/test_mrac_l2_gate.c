/* API/tests/test_mrac_l2_gate.c - host test of the L2 feature gating (mrac_l2g_*, API/mrac.c MRAC_L2_Update).
 *
 * Score per feature: s_i = |EMA(signed weight step_i)|, tau l2_tau. A feature whose score stays below
 * l2_thr_off * max_j s_j for l2_t_trap is trapped (mode 1: its weight is held; mode 2: flagged only), and it
 * revives above l2_thr_on * max_j s_j. Driven through MRAC_Control on the pitch axis, basis S10X, with a
 * persistent rate error (FB 0, Des 20 deg/s); ext 0 = sin(pitch angle) is the planted feature:
 * A. Noise: pitch angle uniform +-10 deg (zero mean, independent of the error) -> ext 0 ends trapped, its
 *    Theta is held from then on, the top-scoring feature stays open. The same features end trapped with a
 *    second noise seed (repeatability).
 * B. Revival: then pitch angle 30 deg constant (ext 0 = 0.5, same sign as the error) -> ext 0 reopens and learns.
 * C. Selectivity: pitch angle 30 deg from the start -> ext 0 never traps.
 * D. Mode 2 observes: Theta and u_ad bit-exact vs mode 0, while ext 0 is flagged trapped.
 * E. Mode 0: scores and flags stay 0, other axes stay 0 in every mode.
 * F. CMD 0x1D fields: thr_on <= thr_off and thr_off >= thr_on are refused, a mode write clears the state.
 *
 * Built by tools/host_tests.py, row mrac_l2_gate (-DMRAC_VARIANT=2), on a copy of API/mrac*.[ch] so the stubs win:
 *   gcc -std=c99 -Wall -Wextra -DMRAC_VARIANT=2 -I<copy> -IAPI/tests/stubs API/tests/test_mrac_l2_gate.c
 *       <copy>/mrac.c <copy>/mrac_math.c -lm
 */
#include <math.h>
#include <stdio.h>
#include <string.h>

#include "mrac.h"
#include "mrac_math.h"

_imu_st imu_data;

#define N_TICKS 1600                      /* 8 s per phase, t_trap 2 s */
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

/* Flying, learn-gated, injected (as API/tests/test_mrac_z_feat.c), pitch basis S10X, then the L2 mode. */
static int fly(float mode)
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
    return MRAC_VariantParamSet(P, MRAC_VF_L2_MODE, mode);
}

static float noise_deg(void)
{
    lcg = lcg * 1664525U + 1013904223U;
    return 20.0f * ((float)(lcg >> 8) / 16777216.0f - 0.5f);
}

/* pit_deg < -900: noise; else that constant angle. Records the pitch u_ad per tick. */
static float uad[N_TICKS];
static void run(float pit_deg)
{
    int i;
    for (i = 0; i < N_TICKS; i++) {
        ctrl.gyroyPID.FB = 0.0f;
        ctrl.gyroyPID.Des = 20.0f;
        imu_data.pit = (pit_deg < -900.0f) ? noise_deg() : pit_deg;
        imu_data.rol = 0.0f;
        MRAC_Control(&ctrl);
        uad[i] = mrac_state.pitch.u_ad;
    }
}

static int argmax_score(void)
{
    int i, best = 0;
    for (i = 1; i < MRAC_N_FEATURES; i++)
        if (fabsf(mrac_l2g_ema[P][i]) > fabsf(mrac_l2g_ema[P][best])) best = i;
    return best;
}

static int other_axes_zero(void)
{
    int a, i;
    for (a = 0; a < AXES; a++) {
        if (a == P) continue;
        for (i = 0; i < MRAC_N_FEATURES; i++)
            if (mrac_l2g_ema[a][i] != 0.0f || mrac_l2g_trapped[a][i] != 0U) return 0;
    }
    return 1;
}

static uint8_t trapped_a[MRAC_N_FEATURES];

static void test_noise_traps_and_revives(void)
{
    float held;
    int top;
    ok("A mode 1 accepted", fly(1.0f) == 1U);
    lcg = 12345U;
    run(-999.0f);
    top = argmax_score();
    printf("info A: ext0 score %.3g, top feature %d score %.3g\n", (double)fabsf(mrac_l2g_ema[P][EXT(0)]),
           top, (double)fabsf(mrac_l2g_ema[P][top]));
    ok("A noise: ext 0 trapped", mrac_l2g_trapped[P][EXT(0)] == 1U);
    ok("A noise: top-scoring feature open", mrac_l2g_trapped[P][top] == 0U && top != EXT(0));
    ok("A other axes untouched", other_axes_zero());
    memcpy(trapped_a, mrac_l2g_trapped[P], sizeof(trapped_a));

    held = mrac_state.pitch.Theta[EXT(0)];
    run(-999.0f);
    ok("A mode 1: trapped ext 0 Theta held", memcmp(&held, (const void *)&mrac_state.pitch.Theta[EXT(0)],
                                                   sizeof(float)) == 0);

    run(30.0f);
    printf("info B: ext0 score %.3g of top %.3g\n", (double)fabsf(mrac_l2g_ema[P][EXT(0)]),
           (double)fabsf(mrac_l2g_ema[P][argmax_score()]));
    ok("B revival: ext 0 open again", mrac_l2g_trapped[P][EXT(0)] == 0U);
    ok("B revival: ext 0 Theta learns again", mrac_state.pitch.Theta[EXT(0)] != held);

    fly(1.0f);
    lcg = 987654321U;
    run(-999.0f);
    ok("A repeat, other seed: same trapped set", memcmp(trapped_a, mrac_l2g_trapped[P], sizeof(trapped_a)) == 0);
}

static void test_selectivity(void)
{
    int i, ever = 0;
    fly(1.0f);
    for (i = 0; i < 4; i++) {
        run(30.0f);
        ever |= mrac_l2g_trapped[P][EXT(0)];
    }
    ok("C excited ext 0 never trapped", ever == 0);
}

static float uad0[N_TICKS];
static float theta0[MRAC_N_FEATURES];

static void test_observe_and_off(void)
{
    int i, zero = 1;
    ok("E mode 0 accepted", fly(0.0f) == 1U);
    lcg = 12345U;
    run(-999.0f);
    memcpy(uad0, uad, sizeof(uad));
    memcpy(theta0, (const void *)mrac_state.pitch.Theta, sizeof(theta0));
    for (i = 0; i < MRAC_N_FEATURES; i++)
        if (mrac_l2g_ema[P][i] != 0.0f || mrac_l2g_trapped[P][i] != 0U || mrac_l2g_trap_ticks[P][i] != 0U) zero = 0;
    ok("E mode 0: scores, counters and flags stay 0", zero);
    ok("E mode 0: other axes untouched", other_axes_zero());

    ok("D mode 2 accepted", fly(2.0f) == 1U);
    lcg = 12345U;
    run(-999.0f);
    ok("D mode 2: u_ad bit-exact vs mode 0", memcmp(uad0, uad, sizeof(uad)) == 0);
    ok("D mode 2: Theta bit-exact vs mode 0", memcmp(theta0, (const void *)mrac_state.pitch.Theta,
                                                     sizeof(theta0)) == 0);
    ok("D mode 2: ext 0 flagged trapped", mrac_l2g_trapped[P][EXT(0)] == 1U);
    ok("D mode 2: other axes untouched", other_axes_zero());

    fly(1.0f);
    lcg = 12345U;
    run(-999.0f);
    ok("A mode 1: Theta differs from mode 0 (the gate acted)",
       memcmp(theta0, (const void *)mrac_state.pitch.Theta, sizeof(theta0)) != 0);
}

static void test_fields(void)
{
    fly(1.0f);
    lcg = 12345U;
    run(-999.0f);
    ok("F setup: ext 0 trapped", mrac_l2g_trapped[P][EXT(0)] == 1U);
    ok("F thr_on == thr_off refused", MRAC_VariantParamSet(P, MRAC_VF_L2_THR_ON, mrac_l2g_cfg[P].thr_off) == 0U);
    ok("F thr_off == thr_on refused", MRAC_VariantParamSet(P, MRAC_VF_L2_THR_OFF, mrac_l2g_cfg[P].thr_on) == 0U);
    ok("F thr_off 0.1 < thr_on accepted", MRAC_VariantParamSet(P, MRAC_VF_L2_THR_OFF, 0.1f) == 1U
       && mrac_l2g_cfg[P].thr_off == 0.1f);
    ok("F tau 0.5 accepted", MRAC_VariantParamSet(P, MRAC_VF_L2_TAU, 0.5f) == 1U && mrac_l2g_cfg[P].tau_s == 0.5f);
    ok("F mode 3 refused", MRAC_VariantParamSet(P, MRAC_VF_L2_MODE, 3.0f) == 0U);
    ok("F mode write accepted", MRAC_VariantParamSet(P, MRAC_VF_L2_MODE, 1.0f) == 1U);
    ok("F mode write clears flags and scores", mrac_l2g_trapped[P][EXT(0)] == 0U
       && mrac_l2g_ema[P][EXT(0)] == 0.0f && mrac_l2g_trap_ticks[P][EXT(0)] == 0U);
}

int main(void)
{
#if MRAC_VARIANT == MRAC_VARIANT_MULTI
    test_noise_traps_and_revives();
    test_selectivity();
    test_observe_and_off();
    test_fields();
#else
    printf("L2 gating test runs on the MULTI build (MRAC_VARIANT=2)\n");
#endif
    printf("%d checks, %d failure(s)\n", checks, fails);
    return fails ? 1 : 0;
}
