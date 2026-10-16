/* Host test of the quad-X mixer table (WP-37, API/controller.c MIX_ROW).
 *
 * Mix_Motor and Mix_SatDeficit replaced written-out expressions in two places: the mixer in
 * TASK/StabilizerTask.c (Compute_Motor, wp/36) and the MRAC V2 saturation deficit in API/controller.c (wp/36).
 * The reference copies below are those expressions, verbatim. Every table cell is +-1, so the results must be
 * bit-identical for any input, including non-unit g_yaw_mix_dir, huge values and the sign of zero.
 * Mix_YawShare (yaw-first desaturation, 2026-10-16) is checked against its definition on random inputs.
 *
 *   gcc -std=c99 -Wall -Wextra -Itests/firmware_host/stubs -IAPI API/tests/test_mixer.c API/controller.c -lm
 *       (or: python tools/host_tests.py mixer)
 */
#include <stdio.h>
#include <string.h>
#include <math.h>
#include "controller.h"
#include "mrac.h"

/* controller.c's MRAC dependencies; only the mixer functions run here */
__typeof__(mrac_state) mrac_state;
__typeof__(mrac_flags) mrac_flags;
__typeof__(mrac_config_pitch) mrac_config_pitch, mrac_config_roll, mrac_config_yaw, mrac_config_z, mrac_config_x, mrac_config_y;
__typeof__(mrac_simplex) mrac_simplex;
__typeof__(mrac_inj) mrac_inj;
void MRAC_Reset(void) { }
void MRAC_Init(void) { }

static int g_checks, g_fail;

static unsigned bits(float f)
{
    unsigned u;
    memcpy(&u, &f, sizeof u);
    return u;
}

static void check_same(float a, float b, const char *what, long step)
{
    g_checks++;
    if (bits(a) != bits(b) && !(a != a && b != b)) {   /* NaN == NaN here: the sign of a NaN is not a value */
        g_fail++;
        if (g_fail <= 10) printf("FAIL %s (step %ld): %.9g vs %.9g\n", what, step, a, b);
    }
}

/* ---- reference: TASK/StabilizerTask.c at wp/36 (Compute_Motor mixer, verbatim, short -> float) ---- */
static void ref_mixer(float Throttle_out, float u_gyroy, float u_gyrox, float u_gyroz, float g_yaw_mix_dir, float m[4])
{
    m[0]= Throttle_out
                                    -u_gyroy//pitch
                                    -u_gyrox//
                                    -g_yaw_mix_dir*u_gyroz;//yaw  M1 CW prop

    m[1]= Throttle_out
                                    +u_gyroy//pitch
                                    +u_gyrox//roll
                                    -g_yaw_mix_dir*u_gyroz;//yaw  M2 CW prop

    m[2]= Throttle_out
                                    -u_gyroy//pitch
                                    +u_gyrox//roll
                                    +g_yaw_mix_dir*u_gyroz;//yaw  M3 CCW prop

    m[3]= Throttle_out
                                    +u_gyroy//pitch
                                    -u_gyrox//roll
                                    +g_yaw_mix_dir*u_gyroz;//yaw  M4 CCW prop
}

/* ---- reference: API/controller.c at wp/36 (mrac_mixer_deficit, verbatim, u before the u_def guard) ---- */
#define REF_PWM_MIN   2000.0f
#define REF_PWM_MAX   4000.0f
#define REF_PER_MOTOR 0.25f
static float ref_cut(float m)
{
    if (m > REF_PWM_MAX) return m - REF_PWM_MAX;
    if (m < REF_PWM_MIN) return m - REF_PWM_MIN;
    return 0.0f;
}
static void ref_deficit(float Throttle_out, float u_gyroy, float u_gyrox, float u_gyroz, float g_yaw_mix_dir,
                        const float k[4], float out[4])
{
    float yz = g_yaw_mix_dir * u_gyroz;
    float d1 = ref_cut(Throttle_out - u_gyroy - u_gyrox - yz);
    float d2 = ref_cut(Throttle_out + u_gyroy + u_gyrox - yz);
    float d3 = ref_cut(Throttle_out - u_gyroy + u_gyrox + yz);
    float d4 = ref_cut(Throttle_out + u_gyroy - u_gyrox + yz);
    out[CTRL_AXIS_ROLL]  = REF_PER_MOTOR * (-d1 + d2 + d3 - d4) / k[CTRL_AXIS_ROLL];
    out[CTRL_AXIS_PITCH] = -REF_PER_MOTOR * (-d1 + d2 - d3 + d4) / k[CTRL_AXIS_PITCH];   /* u_gyroy = -pitch */
    out[CTRL_AXIS_YAW]   = REF_PER_MOTOR * g_yaw_mix_dir * (-d1 - d2 + d3 + d4) / k[CTRL_AXIS_YAW];
    out[CTRL_AXIS_Z]     = REF_PER_MOTOR * (d1 + d2 + d3 + d4) / k[CTRL_AXIS_Z];
}

static unsigned g_lcg = 2024u;
static float rnd(float lo, float hi)
{
    g_lcg = g_lcg * 1664525u + 1013904223u;
    return lo + (hi - lo) * (float)(g_lcg >> 8) / 16777216.0f;
}

/* Out-of-range amount of one motor command, CCR (0 inside [2000, 4000]). */
static float over(float m)
{
    return m > 4000.0f ? m - 4000.0f : (m < 2000.0f ? 2000.0f - m : 0.0f);
}

/* Mix_YawShare (yaw overhaul 2026-10-16): k in [0, 1]; with k*u_yaw no motor is further out of range than without
 * yaw; k is the largest such share (some motor sits on its limit when k < 1); 1 when the full yaw fits. */
static void check_yaw_share(float thr, float up, float ur, float uw, long step)
{
    float k = Mix_YawShare(thr, up, ur, uw);
    float tight = 1e30f, m0, m1, m;
    int i, fits = 1;
    g_checks++;
    if (!(k >= 0.0f && k <= 1.0f)) {
        g_fail++;
        if (g_fail <= 10) printf("FAIL Mix_YawShare out of [0, 1]: %g (step %ld)\n", k, step);
        return;
    }
    for (i = 0; i < 4; i++) {
        m0 = Mix_Motor((uint8_t)i, thr, up, ur, 0.0f);
        m1 = Mix_Motor((uint8_t)i, thr, up, ur, uw);
        m  = Mix_Motor((uint8_t)i, thr, up, ur, k * uw);
        if (over(m1) > over(m0)) fits = 0;
        g_checks++;
        if (over(m) > over(m0) + 1e-3f * (1.0f + fabsf(m0))) {
            g_fail++;
            if (g_fail <= 10) printf("FAIL yaw pushes motor %d out: %g vs %g (step %ld)\n", i, m, m0, step);
        }
        if (g_mix[i][MIX_YAW] * uw > 0.0f) tight = fminf(tight, 4000.0f - m);
        if (g_mix[i][MIX_YAW] * uw < 0.0f) tight = fminf(tight, m - 2000.0f);
    }
    g_checks++;
    if (fits ? (k != 1.0f) : (k < 1.0f && fabsf(tight) > 1e-3f * (1.0f + fabsf(thr)) && k > 0.0f)) {
        g_fail++;
        if (g_fail <= 10) printf("FAIL Mix_YawShare not the largest share: k %g fits %d slack %g (step %ld)\n", k, fits, tight, step);
    }
}

int main(void)
{
    static const float dirs[] = { -1.0f, 1.0f, 0.37f, -2.5f };
    static const float special[] = { 0.0f, -0.0f, 1e30f, -1e30f, 3000.0f, 2000.0f, 4000.0f };
    long k;
    for (k = 0; k < 400000; k++) {
        float thr = rnd(1500.0f, 4500.0f), uy = rnd(-900.0f, 900.0f), ux = rnd(-900.0f, 900.0f);
        float uz = rnd(-900.0f, 900.0f), dir = dirs[k % 4];
        float kk[4] = { rnd(50.0f, 500.0f), rnd(50.0f, 500.0f), rnd(50.0f, 500.0f), rnd(50.0f, 500.0f) };
        float ref[4], def[4], refd[4];
        int i;
        if (k % 13 == 0) uy = special[k % 7];
        if (k % 17 == 0) ux = special[(k / 17) % 7];
        if (k % 19 == 0) uz = special[(k / 19) % 7];
        if (k % 23 == 0) thr = special[(k / 23) % 7];
        ref_mixer(thr, uy, ux, uz, dir, ref);
        for (i = 0; i < 4; i++) {
            float m = Mix_Motor((uint8_t)i, thr, uy, ux, dir * uz);
            check_same(m, ref[i], "Mix_Motor == wp/36 StabilizerTask.c mixer", k);
            g_checks++;
            if ((short)m != (short)ref[i] && m == m && fabsf(m) < 30000.0f) {
                g_fail++;
                printf("FAIL short conversion (step %ld)\n", k);
            }
        }
        ref_deficit(thr, uy, ux, uz, dir, kk, refd);
        Mix_SatDeficit(thr, uy, ux, dir * uz, dir, def);
        for (i = 0; i < 4; i++) {
            check_same(def[i] / kk[i], refd[i], "Mix_SatDeficit / k == wp/36 mrac_mixer_deficit", k);
        }
    }
    for (k = 0; k < 400000; k++) {
        check_yaw_share(rnd(1800.0f, 4200.0f), rnd(-900.0f, 900.0f), rnd(-900.0f, 900.0f), rnd(-1500.0f, 1500.0f), k);
    }
    check_yaw_share(3050.0f, 0.0f, 0.0f, 0.0f, -1);
    g_checks++;
    if (Mix_YawShare(3050.0f, 0.0f, 0.0f, 500.0f) != 1.0f) { g_fail++; printf("FAIL hover yaw 500 must fit\n"); }
    g_checks++;   /* hover 3050 + roll 300 + pitch 300: the worst motor has 4000 - 3650 = 350 left for yaw 650 */
    if (fabsf(Mix_YawShare(3050.0f, 300.0f, 300.0f, -650.0f) - 350.0f / 650.0f) > 1e-6f) { g_fail++; printf("FAIL hover share\n"); }
    g_checks++;
    if (Mix_YawShare(3050.0f, 0.0f, 0.0f, NAN) != 0.0f || Mix_YawShare(NAN, 0.0f, 0.0f, 10.0f) != 0.0f) { g_fail++; printf("FAIL NaN share\n"); }
    printf("%d checks, %d failure(s)\n", g_checks, g_fail);
    return g_fail ? 1 : 0;
}
