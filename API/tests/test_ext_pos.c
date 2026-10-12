/*
 * test_ext_pos.c - Host C test for the EXT_POS camera fix: wire decode, earth-path ring buffer, innovation gate,
 *                  stale flag, and the mode-2 closed loop (gain, slew clamp, slewed re-anchor, per-tick spread). Design: docs/video-truth/live_camera_fusion_design_2026-10-11.md.
 *
 * Build and run (from the repo root):
 *   gcc -std=c99 -Wall -Wextra -IAPI -IAPI/tests/stubs_wp14 API/tests/test_ext_pos.c API/ekf_of.c -lm \
 *       -o /tmp/ext_pos && /tmp/ext_pos
 *
 * The encoder mirrors ground_station/comm/ext_pos_encoder.py ext_pos_encode(); keep the two in step.
 */

#include <stdio.h>
#include <math.h>
#include <string.h>
#include "ekf_of.h"

/* globals normally defined in StabilizerTask.c */
volatile uint8_t g_ekf_of_shadow = 1U;
volatile uint8_t g_ekf_of_vel_fb = 0U;

static int s_fail;
#define CHECK(cond, msg) do { if (!(cond)) { printf("FAIL %s (line %d)\n", msg, __LINE__); s_fail++; } } while (0)

static float encode(float x_cm, float y_cm)
{
    long qx = lroundf(x_cm / EXT_POS_Q_CM), qy = lroundf(y_cm / EXT_POS_Q_CM);
    if (qx < -2048) qx = -2048;
    if (qx > 2047) qx = 2047;
    if (qy < -2048) qy = -2048;
    if (qy > 2047) qy = 2047;
    return (float)((qx + 2048) * 4096 + (qy + 2048));
}

static void test_decode(void)
{
    static const float xs[] = { 0.0f, 12.5f, -37.0f, 1023.5f, -1024.0f, 0.5f, -0.5f };
    float x, y;
    unsigned i, j;
    for (i = 0; i < sizeof xs / sizeof xs[0]; i++) {
        for (j = 0; j < sizeof xs / sizeof xs[0]; j++) {
            CHECK(ext_pos_decode(encode(xs[i], xs[j]), &x, &y) == 1U, "decode ok");
            CHECK(x == xs[i] && y == xs[j], "decode round trip");
        }
    }
    CHECK(ext_pos_decode(-1.0f, &x, &y) == 0U, "negative code");
    CHECK(ext_pos_decode(16777216.0f, &x, &y) == 0U, "code >= 2^24");
    CHECK(ext_pos_decode(1234.5f, &x, &y) == 0U, "non-integer code");
    CHECK(ext_pos_decode(NAN, &x, &y) == 0U, "NaN code");
    CHECK(ext_pos_decode(INFINITY, &x, &y) == 0U, "inf code");
}

static void test_ring(void)
{
    ext_pos_ring_t r;
    float x, y;
    uint32_t t;
    ext_pos_ring_reset(&r);
    CHECK(ext_pos_ring_at(&r, 0U, &x, &y) == 0U, "empty ring");
    /* 200 Hz ticks: only every 50 ms is stored; path x = t/10 cm, y = -t/20 cm */
    for (t = 1000U; t <= 1200U; t += 5U) {
        ext_pos_ring_tick(&r, t, (float)t / 10.0f, -(float)t / 20.0f);
    }
    CHECK(r.count == 5U, "tick spacing 50 ms");
    CHECK(ext_pos_ring_at(&r, 1125U, &x, &y) == 1U && fabsf(x - 112.5f) < 1e-3f && fabsf(y + 56.25f) < 1e-3f,
          "interpolate");
    CHECK(ext_pos_ring_at(&r, 1300U, &x, &y) == 1U && x == 120.0f, "newer than head -> head");
    CHECK(ext_pos_ring_at(&r, 1000U, &x, &y) == 1U && x == 100.0f, "oldest sample exact");
    CHECK(ext_pos_ring_at(&r, 999U, &x, &y) == 0U, "older than ring");
    /* wrap: 40 samples into 32 slots keeps the newest 32 (1.55 s) */
    ext_pos_ring_reset(&r);
    for (t = 0U; t < 40U * 50U; t += 50U) {
        ext_pos_ring_tick(&r, t, (float)t, 0.0f);
    }
    CHECK(r.count == EXT_POS_RING_N, "ring full");
    CHECK(ext_pos_ring_at(&r, 8U * 50U, &x, &y) == 1U && x == 400.0f, "oldest kept after wrap");
    CHECK(ext_pos_ring_at(&r, 7U * 50U + 25U, &x, &y) == 0U, "overwritten sample is gone");
    CHECK(ext_pos_ring_at(&r, 20U * 50U + 10U, &x, &y) == 1U && fabsf(x - 1010.0f) < 1e-3f, "interpolate after wrap");
    /* tick counter wrap-around (uint32 ms) */
    ext_pos_ring_reset(&r);
    ext_pos_ring_tick(&r, 0xFFFFFFF0U, 1.0f, 0.0f);
    ext_pos_ring_tick(&r, 0x00000022U, 3.0f, 0.0f);
    CHECK(r.count == 2U, "tick wrap stores");
    CHECK(ext_pos_ring_at(&r, 0x00000009U, &x, &y) == 1U && fabsf(x - 2.0f) < 1e-3f, "interpolate across wrap");
}

static void test_fix_and_stale(void)
{
    ext_pos_ring_t r;
    ext_pos_stat_t s;
    uint32_t t;
    int i;
    memset(&s, 0, sizeof s);
    ext_pos_ring_reset(&r);
    for (t = 0U; t <= 2000U; t += 5U) {
        ext_pos_ring_tick(&r, t, 10.0f, 20.0f);
    }
    ext_pos_stale_tick(&s, 2000U);
    CHECK(s.stale == 1U, "stale before any fix");
    CHECK(ext_pos_fix(&s, &r, 1700U, 2000U, 13.0f, 16.0f) == EXT_POS_FIX_OK, "fix ok");
    CHECK(fabsf(s.e_x - 3.0f) < 1e-4f && fabsf(s.e_y + 4.0f) < 1e-4f && s.age_ms == 300U, "innovation and age");
    CHECK(s.n_ok == 1U && s.ok_run == 1U && s.stale == 1U && s.last_ok_ms == 2000U, "ok counters; stale until EXT_POS_TRUST_N in a row");
    for (i = 0; i < 3; i++) {
        CHECK(ext_pos_fix(&s, &r, 1900U, 2000U, 60.0f, 20.0f) == EXT_POS_FIX_GATED, "gated at 50 cm");
    }
    CHECK(s.n_rej == 3U && s.rej_run == 3U && s.ok_run == 0U && fabsf(s.e_x - 50.0f) < 1e-4f, "gated counters, e kept, ok run reset");
    CHECK(ext_pos_fix(&s, &r, 1900U, 2000U, 49.0f, 20.0f) == EXT_POS_FIX_OK && s.rej_run == 0U, "39 cm passes, run reset");
    for (i = 1; i < (int)EXT_POS_TRUST_N; i++) {
        CHECK(s.stale == 1U, "still stale inside the trust run");
        CHECK(ext_pos_fix(&s, &r, 1900U, 2000U, 12.0f, 20.0f) == EXT_POS_FIX_OK, "trust run fix ok");
    }
    CHECK(s.stale == 0U && s.ok_run == EXT_POS_TRUST_N, "EXT_POS_TRUST_N good fixes in a row: trusted");
    CHECK(ext_pos_fix(&s, &r, 100U, 2000U, 10.0f, 20.0f) == EXT_POS_FIX_OLD && s.n_old == 1U, "too old");
    CHECK(s.n_ok == 1U + EXT_POS_TRUST_N && s.n_rej == 3U, "old fix leaves ok/rej alone");
    ext_pos_stale_tick(&s, 5000U);
    CHECK(s.stale == 0U, "3.0 s after last ok: not stale yet");
    ext_pos_stale_tick(&s, 5001U);
    CHECK(s.stale == 1U, "3.001 s after last ok: stale");
    CHECK(ext_pos_fix(&s, &r, 2100U, 2000U, 10.0f, 20.0f) == EXT_POS_FIX_OK && s.age_ms == 0U, "future stamp clamps age");
}

/* Mode 2 as StabilizerTask runs it: 200 Hz ticks, earth = truth + drift (1, -0.5 cm/s), the raw path (earth - a)
 * buffered, earth ramped toward c by ext_pos_spread() every tick, a fix of the truth every 100 ms reaching the drone 300 ms after capture. Same case as
 * tests/test_video_truth.py test_ext_pos_replay_cancels_drift_through_the_delay, then a 100 cm truth jump. */
static float truth_x(uint32_t t) { return 30.0f * sinf(0.5e-3f * (float)t) + ((t >= 20000U) ? 100.0f : 0.0f); }
static float truth_y(uint32_t t) { return 20.0f * cosf(0.3e-3f * (float)t); }

static void test_closed_loop(void)
{
    ext_pos_ring_t r;
    ext_pos_stat_t s;
    uint32_t t, t_fix = 0U;
    float ex = truth_x(0U), ey = truth_y(0U), dx, dy, err, worst = 0.0f, step_max = 0.0f, tick_max = 0.0f;
    uint16_t rej_before_jump = 0U;
    uint8_t res;
    memset(&s, 0, sizeof s);
    ext_pos_ring_reset(&r);
    for (t = 5U; t <= 40000U; t += 5U) {
        /* the drift and the path; the jump is in the truth only (a re-anchor case) */
        ex += (30.0f * sinf(0.5e-3f * (float)t) - 30.0f * sinf(0.5e-3f * (float)(t - 5U))) + 1.0f * 0.005f;
        ey += (truth_y(t) - truth_y(t - 5U)) - 0.5f * 0.005f;
        ext_pos_ring_tick(&r, t, ex - s.a_x, ey - s.a_y);
        if (t >= 400U && t % 100U == 0U) {
            t_fix = t - 300U;
            res = ext_pos_fix(&s, &r, t_fix, t, truth_x(t_fix), truth_y(t_fix));
            if (ext_pos_step(&s, res, &dx, &dy) && sqrtf(dx * dx + dy * dy) > step_max) step_max = sqrtf(dx * dx + dy * dy);
        }
        ext_pos_spread(&s, &dx, &dy);
        ex += dx;
        ey += dy;
        if (sqrtf(dx * dx + dy * dy) > tick_max) tick_max = sqrtf(dx * dx + dy * dy);
        err = sqrtf((ex - truth_x(t)) * (ex - truth_x(t)) + (ey - truth_y(t)) * (ey - truth_y(t)));
        if (t > 10000U && t < 20000U && err > worst) worst = err;
        if (t == 19995U) rej_before_jump = s.n_rej;
    }
    CHECK(rej_before_jump == 0U && s.n_old == 0U, "no gated or old fix before the jump");
    CHECK(worst < 2.0f, "fused error < 2 cm from 10 s to the jump through a 300 ms delay");
    CHECK(step_max <= EXT_POS_SLEW_CM + 1e-4f, "every c step within the slew clamp");
    CHECK(tick_max <= EXT_POS_SLEW_CM / (float)EXT_POS_SPREAD_N + 1e-4f, "every earth step within the per-tick spread");
    CHECK(s.n_rej >= EXT_POS_REJ_N && s.n_anchor >= 1U, "jump gated, then re-anchored");
    CHECK(fabsf(ex - truth_x(40000U)) < 5.0f, "re-anchored onto the jumped truth by 40 s");
    (void)t_fix;
}

static void test_step_rules(void)
{
    ext_pos_stat_t s;
    float dx, dy;
    memset(&s, 0, sizeof s);
    s.e_x = 4.0f; s.e_y = -2.0f;
    CHECK(ext_pos_step(&s, EXT_POS_FIX_OK, &dx, &dy) == 1U && fabsf(dx - EXT_POS_GAIN * 4.0f) < 1e-5f && fabsf(dy + EXT_POS_GAIN * 2.0f) < 1e-5f, "gain step");
    CHECK(fabsf(s.c_x - dx) < 1e-6f && fabsf(s.c_y - dy) < 1e-6f && s.a_x == 0.0f, "c moves by the step, a does not");
    CHECK(ext_pos_step(&s, EXT_POS_FIX_OLD, &dx, &dy) == 0U && dx == 0.0f && dy == 0.0f, "old fix: no step");
    s.stale = 1U;
    CHECK(ext_pos_step(&s, EXT_POS_FIX_OK, &dx, &dy) == 0U, "accepted fix while stale: no step");
    s.stale = 0U;
    s.rej_run = EXT_POS_REJ_N - 1U; s.e_x = 300.0f; s.e_y = 400.0f;
    CHECK(ext_pos_step(&s, EXT_POS_FIX_GATED, &dx, &dy) == 0U && s.n_anchor == 0U, "gated inside the run: no step");
    s.rej_run = EXT_POS_REJ_N;
    s.o_n = EXT_POS_HIST_N; s.o_head = 0U;
    s.o_x[0] = 300.0f; s.o_y[0] = 400.0f; s.o_x[1] = 300.0f; s.o_y[1] = 380.0f; s.o_x[2] = 300.0f; s.o_y[2] = 400.0f;
    CHECK(ext_pos_step(&s, EXT_POS_FIX_GATED, &dx, &dy) == 0U && s.n_anchor == 0U && s.n_disagree == 1U, "gated run spread over 20 cm: held back");
    s.o_y[1] = 404.0f;
    CHECK(ext_pos_step(&s, EXT_POS_FIX_GATED, &dx, &dy) == 0U && s.n_anchor == 0U && s.n_disagree == 2U, "agreeing run 500 cm away (r6_f3_mode3): bounded, held back");
    s.e_x = 60.0f; s.e_y = 80.0f;
    CHECK(ext_pos_step(&s, EXT_POS_FIX_GATED, &dx, &dy) == 1U && s.n_anchor == 1U, "gated run within EXT_POS_AGREE_CM: re-anchor step");
    CHECK(fabsf(dx - 0.6f * EXT_POS_SLEW_CM) < 1e-4f && fabsf(dy - 0.8f * EXT_POS_SLEW_CM) < 1e-4f, "re-anchor step clamped to the slew along e");
}

static void test_spread(void)
{
    ext_pos_stat_t s;
    float dx, dy, sx = 0.0f, worst = 0.0f;
    unsigned i;
    memset(&s, 0, sizeof s);
    s.c_x = EXT_POS_SLEW_CM;
    for (i = 0U; i < EXT_POS_SPREAD_N; i++) {
        ext_pos_spread(&s, &dx, &dy);
        sx += dx;
        if (sqrtf(dx * dx + dy * dy) > worst) worst = sqrtf(dx * dx + dy * dy);
    }
    CHECK(worst <= EXT_POS_SLEW_CM / (float)EXT_POS_SPREAD_N + 1e-5f, "spread step within slew / SPREAD_N");
    CHECK(fabsf(s.a_x - s.c_x) < 1e-4f && fabsf(sx - s.c_x) < 1e-4f && s.a_y == 0.0f, "a reaches c in SPREAD_N ticks");
    ext_pos_spread(&s, &dx, &dy);
    CHECK(fabsf(dx) < 1e-4f && dy == 0.0f, "no step once a == c");
}

static void test_shadow_with_held_c(void)
{
    /* mode 2 -> 1 keeps c; the shadow innovation must still be z - earth, earth = raw + c */
    ext_pos_ring_t r;
    ext_pos_stat_t s;
    uint32_t t;
    memset(&s, 0, sizeof s);
    s.c_x = 7.0f; s.c_y = -3.0f;
    s.a_x = s.c_x; s.a_y = s.c_y;   /* spread done */
    ext_pos_ring_reset(&r);
    for (t = 0U; t <= 2000U; t += 5U) {
        ext_pos_ring_tick(&r, t, 10.0f - s.a_x, 20.0f - s.a_y);   /* earth = (10, 20) */
    }
    CHECK(ext_pos_fix(&s, &r, 1700U, 2000U, 13.0f, 16.0f) == EXT_POS_FIX_OK, "fix ok with c held");
    CHECK(fabsf(s.e_x - 3.0f) < 1e-4f && fabsf(s.e_y + 4.0f) < 1e-4f, "innovation against earth, not raw");
}

int main(void)
{
    test_decode();
    test_ring();
    test_fix_and_stale();
    test_step_rules();
    test_spread();
    test_shadow_with_held_c();
    test_closed_loop();
    if (s_fail) {
        printf("test_ext_pos: %d FAILED\n", s_fail);
        return 1;
    }
    printf("test_ext_pos: all passed\n");
    return 0;
}
