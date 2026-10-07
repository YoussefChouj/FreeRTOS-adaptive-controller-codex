#include <stdio.h>
#include <stdint.h>
#include <math.h>
#include <string.h>
#include "wfb_prog.h"

static int s_check_count = 0;
static int s_fail_count = 0;

#define CHECK(cond) do { \
    s_check_count++; \
    if (!(cond)) { \
        s_fail_count++; \
        printf("FAIL %s:%d: %s\n", __FILE__, __LINE__, #cond); \
    } \
} while (0)

#define CHECK_FLOAT_EQ(a, b, tol) do { \
    s_check_count++; \
    if (!(fabsf((a) - (b)) <= (tol))) { \
        s_fail_count++; \
        printf("FAIL %s:%d: fabsf(%s - %s) = %f > %f\n", \
               __FILE__, __LINE__, #a, #b, (double)fabsf((a) - (b)), (double)(tol)); \
    } \
} while (0)

#define HZ 1.0f
#define DT 0.005f

typedef struct { float f[WFB_PROG_F_COUNT]; } rec_t;

static wfb_prog_seg_t s_buf[WFB_PROG_MAX_SEGS];
static rec_t s_rec[WFB_PROG_MAX_SEGS];
static uint16_t s_n;
static int s_sent;  /* field commands sent by the last upload */
static wfb_traj_limits_t s_lim;
static wfb_prog_caps_t s_caps;
static wfb_prog_t s_pr;

static void add(float atom, float prof, float v, float a, float j, float vin, float vout, const float *p, int np)
{
    rec_t *r = &s_rec[s_n++];
    int i;

    memset(r, 0, sizeof(*r));
    r->f[WFB_PROG_F_ATOM] = atom;
    r->f[WFB_PROG_F_PROFILE] = prof;
    r->f[WFB_PROG_F_V] = v;
    r->f[WFB_PROG_F_A] = a;
    r->f[WFB_PROG_F_J] = j;
    r->f[WFB_PROG_F_V_IN] = vin;
    r->f[WFB_PROG_F_V_OUT] = vout;
    for (i = 0; i < np; i++) {
        r->f[WFB_PROG_F_P0 + i] = p[i];
    }
}

static void line(float prof, float v, float vin, float vout, float x, float y, float z)
{
    float p[4] = {x, y, z, 0.0f};
    add(WFB_ATOM_LINE, prof, v, 1.0f, 4.0f, vin, vout, p, 4);
}

static void hold(float s)
{
    add(WFB_ATOM_HOLD, 0.0f, 0.0f, 0.0f, 0.0f, 0.0f, 0.0f, &s, 1);
}

static wfb_err_t send(uint8_t idx, float val)
{
    return wfb_prog_on_cmd(&s_pr, idx, val, &s_lim, &s_caps, HZ);
}

/* Uploads s_rec like the ground station: only the fields that differ from the staged record. */
static wfb_err_t upload(uint32_t crc_xor)
{
    static const rec_t zero;
    const rec_t *prev = &zero;
    uint32_t crc;
    uint16_t k;
    int i;
    wfb_err_t err;

    s_sent = 0;
    err = send(WFB_PROG_IDX_BEGIN, (float)s_n);
    if (err != WFB_ERR_NONE) {
        return err;
    }
    for (k = 0u; k < s_n; k++) {
        for (i = 0; i < WFB_PROG_F_COUNT; i++) {
            if (s_rec[k].f[i] != prev->f[i]) {
                err = send((uint8_t)i, s_rec[k].f[i]);
                if (err != WFB_ERR_NONE) {
                    return err;
                }
                s_sent++;
            }
        }
        err = send(WFB_PROG_IDX_PUSH, (float)k);
        if (err != WFB_ERR_NONE) {
            return err;
        }
        prev = &s_rec[k];
    }
    crc = wfb_crc32((const uint8_t *)s_rec, (uint32_t)(s_n * sizeof(rec_t))) ^ crc_xor;
    err = send(WFB_PROG_IDX_CRC_HI, (float)(crc >> 16));
    if (err != WFB_ERR_NONE) {
        return err;
    }
    return send(WFB_PROG_IDX_COMMIT, (float)(crc & 0xFFFFu));
}

static void fresh(void)
{
    s_n = 0u;
    wfb_prog_init(&s_pr, s_buf, WFB_PROG_MAX_SEGS);
}

typedef struct {
    float vmax, amax, dvmax;
    float lo[3], hi[3];
    wfb_traj_point_t end;
} scan_t;

/* Samples the started program every DT until DONE: peak speed, peak accel, largest speed step, extents. */
static void scan(scan_t *sc)
{
    wfb_traj_point_t p;
    float pos[3], prev[3] = {0.0f, 0.0f, 0.0f}, v[3], vprev[3] = {0.0f, 0.0f, 0.0f}, d, acc;
    int i = 0, k;

    memset(sc, 0, sizeof(*sc));
    for (k = 0; k < 3; k++) {
        sc->lo[k] = 1.0e9f;
        sc->hi[k] = -1.0e9f;
    }
    while (wfb_prog_sample(&s_pr, (float)i * DT, &p)) {
        pos[0] = p.x_m;
        pos[1] = p.y_m;
        pos[2] = p.z_m;
        d = 0.0f;
        acc = 0.0f;
        for (k = 0; k < 3; k++) {
            sc->lo[k] = (pos[k] < sc->lo[k]) ? pos[k] : sc->lo[k];
            sc->hi[k] = (pos[k] > sc->hi[k]) ? pos[k] : sc->hi[k];
            v[k] = (pos[k] - prev[k]) / DT;
            d += v[k] * v[k];
            acc += (v[k] - vprev[k]) * (v[k] - vprev[k]);
        }
        if (i >= 1 && sqrtf(d) > sc->vmax) {
            sc->vmax = sqrtf(d);
        }
        if (i >= 2 && sqrtf(acc) / DT > sc->amax) {
            sc->amax = sqrtf(acc) / DT;
            sc->dvmax = sqrtf(acc);
        }
        memcpy(prev, pos, sizeof(pos));
        memcpy(vprev, v, sizeof(v));
        i++;
    }
    sc->end = p;
}

static void test_ramp_F(void)
{
    const float rho[WFB_PROF_COUNT] = {0.0f, 1.0f / 3.0f, 0.0f, 0.0f};
    const float taus[3] = {0.1f, 0.25f, 0.4f};
    float h = 1.0e-3f, r, left, right;
    int prof, i;

    for (prof = 0; prof < WFB_PROF_COUNT; prof++) {
        r = rho[prof];
        CHECK_FLOAT_EQ(wfb_prog_ramp_F((uint8_t)prof, r, 0.0f), 0.0f, 1.0e-7f);
        CHECK_FLOAT_EQ(wfb_prog_ramp_F((uint8_t)prof, r, 1.0f), 0.5f, 1.0e-7f);
        CHECK_FLOAT_EQ(wfb_prog_ramp_F((uint8_t)prof, r, -1.0f), 0.0f, 1.0e-7f);
        CHECK_FLOAT_EQ(wfb_prog_ramp_F((uint8_t)prof, r, 2.0f), 0.5f, 1.0e-7f);
        /* the unit ramp ends at 1: F(1 - h) = 1/2 - h */
        CHECK_FLOAT_EQ(wfb_prog_ramp_F((uint8_t)prof, r, 1.0f - h), 0.5f - h, 2.0e-6f);
        /* f(tau) + f(1 - tau) = 1  <=>  F(1 - tau) = 1/2 - tau + F(tau) */
        for (i = 0; i < 3; i++) {
            CHECK_FLOAT_EQ(wfb_prog_ramp_F((uint8_t)prof, r, 1.0f - taus[i]),
                           0.5f - taus[i] + wfb_prog_ramp_F((uint8_t)prof, r, taus[i]), 2.0e-6f);
        }
    }
    /* SCURVE: the slope f is continuous where the jerk phase ends */
    r = 1.0f / 3.0f;
    left = (wfb_prog_ramp_F(WFB_PROF_SCURVE, r, r) - wfb_prog_ramp_F(WFB_PROF_SCURVE, r, r - h)) / h;
    right = (wfb_prog_ramp_F(WFB_PROF_SCURVE, r, r + h) - wfb_prog_ramp_F(WFB_PROF_SCURVE, r, r)) / h;
    CHECK_FLOAT_EQ(left, right, 5.0e-3f);
    CHECK_FLOAT_EQ(left, 0.25f, 5.0e-3f);  /* f(rho) = rho / (2 (1 - rho)) */
}

/* Out 0.8 m along x and back at 0.5 m/s, a = 1, j = 4, with every profile. */
static void test_profiles(void)
{
    const float ta[WFB_PROF_COUNT] = {0.5f, 0.75f, 0.785398163f, 0.9375f};
    float dur;
    scan_t sc;
    int prof;

    for (prof = 0; prof < WFB_PROF_COUNT; prof++) {
        fresh();
        line((float)prof, 0.5f, 0.0f, 0.0f, 0.8f, 0.0f, HZ);
        line((float)prof, 0.5f, 0.0f, 0.0f, 0.0f, 0.0f, HZ);
        CHECK(upload(0u) == WFB_ERR_NONE);
        CHECK(s_pr.state == (uint8_t)WFB_TRAJ_READY);
        CHECK(s_pr.err_seg == 0xFFFFu);
        dur = 2.0f * ta[prof] + (0.8f - 0.5f * ta[prof]) / 0.5f;
        CHECK_FLOAT_EQ(s_buf[0].len, 0.8f, 1.0e-6f);
        CHECK_FLOAT_EQ(s_buf[0].vc, 0.5f, 1.0e-6f);
        CHECK_FLOAT_EQ(s_buf[0].dur, dur, 1.0e-4f);
        CHECK_FLOAT_EQ(s_buf[1].t0, dur, 1.0e-4f);
        CHECK_FLOAT_EQ(s_pr.t_total, 2.0f * dur, 2.0e-4f);
        if (prof == WFB_PROF_TRAP) {
            CHECK(s_sent == 7);  /* seg 0: atom v a j x z; seg 1: x only (sticky) */
        }
        if (prof == WFB_PROF_SCURVE) {
            CHECK_FLOAT_EQ(s_buf[0].rho0, 1.0f / 3.0f, 1.0e-5f);
        }
        CHECK(wfb_prog_start(&s_pr, 0.0f) == WFB_ERR_NONE);
        scan(&sc);
        CHECK(sc.vmax <= 0.505f && sc.vmax >= 0.495f);
        CHECK(sc.amax <= 1.05f && sc.amax >= 0.95f);
        CHECK_FLOAT_EQ(sc.hi[0], 0.8f, 1.0e-3f);
        CHECK_FLOAT_EQ(sc.lo[0], 0.0f, 1.0e-6f);
        CHECK_FLOAT_EQ(sc.hi[2] - sc.lo[2], 0.0f, 1.0e-6f);
        CHECK_FLOAT_EQ(sc.end.x_m, 0.0f, 1.0e-6f);
        CHECK_FLOAT_EQ(sc.end.z_m, HZ, 1.0e-6f);
        CHECK(s_pr.state == (uint8_t)WFB_TRAJ_DONE);
    }
}

static void test_planner_limits(void)
{
    scan_t sc;

    /* 0.1 m at 0.5 m/s, a = 1: no room to cruise, vc = sqrt(a L) */
    fresh();
    line(WFB_PROF_TRAP, 0.5f, 0.0f, 0.0f, 0.1f, 0.0f, HZ);
    line(WFB_PROF_TRAP, 0.5f, 0.0f, 0.0f, 0.0f, 0.0f, HZ);
    CHECK(upload(0u) == WFB_ERR_NONE);
    CHECK_FLOAT_EQ(s_buf[0].vc, 0.316228f, 1.0e-4f);
    CHECK(s_buf[0].tc < 1.0e-3f);

    /* SCURVE with dv < a^2 / j: the accel never reaches a, peak = j Ta / 2 */
    fresh();
    line(WFB_PROF_SCURVE, 0.2f, 0.0f, 0.0f, 0.8f, 0.0f, HZ);
    line(WFB_PROF_SCURVE, 0.2f, 0.0f, 0.0f, 0.0f, 0.0f, HZ);
    CHECK(upload(0u) == WFB_ERR_NONE);
    CHECK_FLOAT_EQ(s_buf[0].rho0, 0.5f, 1.0e-6f);
    CHECK_FLOAT_EQ(s_buf[0].ta0, 0.4472136f, 1.0e-5f);
    CHECK(wfb_prog_start(&s_pr, 0.0f) == WFB_ERR_NONE);
    scan(&sc);
    CHECK_FLOAT_EQ(sc.amax, 0.894427f, 0.03f);

    /* blended: 0.5 m to 0.3 m/s, 0.5 m on from 0.3 m/s, home; no speed step at the joint */
    fresh();
    line(WFB_PROF_SINE, 0.5f, 0.0f, 0.3f, 0.5f, 0.0f, HZ);
    line(WFB_PROF_SINE, 0.5f, 0.3f, 0.0f, 1.0f, 0.0f, HZ);
    line(WFB_PROF_SINE, 0.5f, 0.0f, 0.0f, 0.0f, 0.0f, HZ);
    CHECK(upload(0u) == WFB_ERR_NONE);
    CHECK(wfb_prog_start(&s_pr, 0.0f) == WFB_ERR_NONE);
    scan(&sc);
    CHECK(sc.vmax <= 0.505f);
    CHECK(sc.dvmax <= 1.05f * DT + 1.0e-4f);
    CHECK_FLOAT_EQ(sc.hi[0], 1.0f, 1.0e-3f);

    /* same without v_in on the second segment: a 0.3 m/s step at the joint */
    fresh();
    line(WFB_PROF_SINE, 0.5f, 0.0f, 0.3f, 0.5f, 0.0f, HZ);
    line(WFB_PROF_SINE, 0.5f, 0.0f, 0.0f, 1.0f, 0.0f, HZ);
    line(WFB_PROF_SINE, 0.5f, 0.0f, 0.0f, 0.0f, 0.0f, HZ);
    CHECK(upload(0u) == WFB_ERR_SPEED);
    CHECK(s_pr.err_seg == 1u);
    CHECK(s_pr.state == (uint8_t)WFB_TRAJ_EMPTY);

    /* v_out too high to stop on the next 0.05 m */
    fresh();
    line(WFB_PROF_TRAP, 0.5f, 0.0f, 0.0f, 0.5f, 0.0f, HZ);
    line(WFB_PROF_TRAP, 0.5f, 0.0f, 0.5f, 0.55f, 0.0f, HZ);
    line(WFB_PROF_TRAP, 0.5f, 0.0f, 0.0f, 0.0f, 0.0f, HZ);
    CHECK(upload(0u) == WFB_ERR_RANGE);
    CHECK(s_pr.err_seg == 1u);
}

static void test_turn_hold(void)
{
    float p[1];
    wfb_traj_point_t pt;

    /* TURN 90 at 45 deg/s, 90 deg/s^2, then back; yaw relative to the START heading */
    fresh();
    p[0] = 90.0f;
    add(WFB_ATOM_TURN, WFB_PROF_TRAP, 45.0f, 90.0f, 0.0f, 0.0f, 0.0f, p, 1);
    p[0] = 0.0f;
    add(WFB_ATOM_TURN, WFB_PROF_TRAP, 45.0f, 90.0f, 0.0f, 0.0f, 0.0f, p, 1);
    CHECK(upload(0u) == WFB_ERR_NONE);
    CHECK_FLOAT_EQ(s_buf[0].dur, 2.5f, 1.0e-4f);
    CHECK(wfb_prog_start(&s_pr, 170.0f) == WFB_ERR_NONE);
    CHECK(wfb_prog_sample(&s_pr, s_buf[1].t0, &pt) == 1);
    CHECK_FLOAT_EQ(pt.yaw_deg, -100.0f, 1.0e-3f);
    CHECK_FLOAT_EQ(pt.x_m, 0.0f, 1.0e-6f);
    CHECK_FLOAT_EQ(pt.z_m, HZ, 1.0e-6f);

    /* shortest way across +-180 */
    fresh();
    p[0] = 170.0f;
    add(WFB_ATOM_TURN, WFB_PROF_SINE, 60.0f, 90.0f, 0.0f, 0.0f, 0.0f, p, 1);
    p[0] = -170.0f;
    add(WFB_ATOM_TURN, WFB_PROF_SINE, 60.0f, 90.0f, 0.0f, 0.0f, 0.0f, p, 1);
    p[0] = 0.0f;
    add(WFB_ATOM_TURN, WFB_PROF_SINE, 60.0f, 90.0f, 0.0f, 0.0f, 0.0f, p, 1);
    CHECK(upload(0u) == WFB_ERR_NONE);
    CHECK_FLOAT_EQ(s_buf[1].len, 20.0f, 1.0e-3f);
    CHECK_FLOAT_EQ(s_buf[1].dyaw, 20.0f, 1.0e-3f);
    CHECK_FLOAT_EQ(s_buf[2].yaw0, -170.0f, 1.0e-3f);
    CHECK_FLOAT_EQ(s_buf[2].dyaw, 170.0f, 1.0e-3f);

    /* faster than yaw_rate_max */
    fresh();
    p[0] = 90.0f;
    add(WFB_ATOM_TURN, WFB_PROF_TRAP, 100.0f, 90.0f, 0.0f, 0.0f, 0.0f, p, 1);
    CHECK(upload(0u) == WFB_ERR_SPEED);

    /* HOLD keeps the point */
    fresh();
    line(WFB_PROF_QUINTIC, 0.5f, 0.0f, 0.0f, 0.5f, 0.0f, HZ);
    hold(2.0f);
    line(WFB_PROF_QUINTIC, 0.5f, 0.0f, 0.0f, 0.0f, 0.0f, HZ);
    CHECK(upload(0u) == WFB_ERR_NONE);
    CHECK_FLOAT_EQ(s_buf[1].dur, 2.0f, 1.0e-6f);
    CHECK_FLOAT_EQ(s_pr.t_total, 2.0f * s_buf[0].dur + 2.0f, 1.0e-4f);
    CHECK(wfb_prog_start(&s_pr, 0.0f) == WFB_ERR_NONE);
    CHECK(wfb_prog_sample(&s_pr, s_buf[1].t0 + 1.0f, &pt) == 1);
    CHECK_FLOAT_EQ(pt.x_m, 0.5f, 1.0e-6f);
}

static void test_arc(void)
{
    float p[5] = {0.0f, -1.0f, 360.0f, 0.0f, 1.0f};
    wfb_traj_point_t pt;
    scan_t sc;

    /* to (0, -0.5), one CCW lap about (0, -1) turning with the arc, home */
    fresh();
    line(WFB_PROF_SINE, 0.3f, 0.0f, 0.0f, 0.0f, -0.5f, HZ);
    add(WFB_ATOM_ARC, WFB_PROF_SINE, 0.3f, 1.0f, 4.0f, 0.0f, 0.0f, p, 5);
    line(WFB_PROF_SINE, 0.3f, 0.0f, 0.0f, 0.0f, 0.0f, HZ);
    CHECK(upload(0u) == WFB_ERR_NONE);
    CHECK_FLOAT_EQ(s_buf[1].r, 0.5f, 1.0e-5f);
    CHECK_FLOAT_EQ(s_buf[1].len, 3.14159265f, 1.0e-4f);
    CHECK_FLOAT_EQ(s_buf[2].x0, 0.0f, 1.0e-5f);
    CHECK_FLOAT_EQ(s_buf[2].y0, -0.5f, 1.0e-5f);
    CHECK_FLOAT_EQ(s_buf[2].yaw0, 0.0f, 1.0e-3f);
    CHECK(wfb_prog_start(&s_pr, 0.0f) == WFB_ERR_NONE);
    CHECK(wfb_prog_sample(&s_pr, s_buf[1].t0 + 0.5f * s_buf[1].dur, &pt) == 1);
    CHECK_FLOAT_EQ(pt.x_m, 0.0f, 1.0e-3f);
    CHECK_FLOAT_EQ(pt.y_m, -1.5f, 1.0e-3f);
    CHECK_FLOAT_EQ(fabsf(pt.yaw_deg), 180.0f, 0.1f);
    CHECK(wfb_prog_stop(&s_pr) == WFB_ERR_NONE);
    CHECK(wfb_prog_start(&s_pr, 0.0f) == WFB_ERR_NONE);
    scan(&sc);
    CHECK_FLOAT_EQ(sc.lo[1], -1.5f, 2.0e-3f);
    CHECK_FLOAT_EQ(sc.lo[0], -0.5f, 5.0e-3f);
    CHECK_FLOAT_EQ(sc.hi[0], 0.5f, 5.0e-3f);
    CHECK(sc.vmax <= 0.303f);

    /* 90 deg CCW from the pad about (0, -1) ends at (-1, -1): in the fence */
    fresh();
    p[2] = 90.0f;
    p[4] = 0.0f;
    add(WFB_ATOM_ARC, WFB_PROF_SINE, 0.3f, 1.0f, 4.0f, 0.0f, 0.0f, p, 5);
    line(WFB_PROF_SINE, 0.3f, 0.0f, 0.0f, 0.0f, 0.0f, HZ);
    CHECK(upload(0u) == WFB_ERR_NONE);
    CHECK_FLOAT_EQ(s_buf[1].x0, -1.0f, 1.0e-5f);
    CHECK_FLOAT_EQ(s_buf[1].y0, -1.0f, 1.0e-5f);

    /* 250 deg CCW about (0, -0.9): ends inside but passes the bottom (0, -1.8) */
    fresh();
    p[1] = -0.9f;
    p[2] = 250.0f;
    add(WFB_ATOM_ARC, WFB_PROF_SINE, 0.3f, 1.0f, 4.0f, 0.0f, 0.0f, p, 5);
    line(WFB_PROF_SINE, 0.3f, 0.0f, 0.0f, 0.0f, 0.0f, HZ);
    CHECK(upload(0u) == WFB_ERR_BOUNDS);
    CHECK(s_pr.err_seg == 0u);

    /* radius under r_min */
    fresh();
    p[1] = -0.03f;
    p[2] = 360.0f;
    add(WFB_ATOM_ARC, WFB_PROF_SINE, 0.3f, 1.0f, 4.0f, 0.0f, 0.0f, p, 5);
    CHECK(upload(0u) == WFB_ERR_RANGE);
}

static void test_lissa(void)
{
    /* figure-8: x n = 1 amp 0.3, y n = 2 amp 0.2, one cycle at 0.15 cycles/s */
    float p[10] = {0.3f, 0.2f, 0.0f, 1.0f, 2.0f, 0.0f, 0.0f, 0.0f, 0.0f, 1.0f};
    scan_t sc;

    fresh();
    add(WFB_ATOM_LISSA, WFB_PROF_SINE, 0.15f, 0.2f, 4.0f, 0.0f, 0.0f, p, 10);
    CHECK(upload(0u) == WFB_ERR_NONE);
    CHECK_FLOAT_EQ(s_buf[0].len, 1.0f, 1.0e-6f);
    CHECK(wfb_prog_start(&s_pr, 0.0f) == WFB_ERR_NONE);
    scan(&sc);
    CHECK_FLOAT_EQ(sc.hi[0], 0.3f, 1.0e-3f);
    CHECK_FLOAT_EQ(sc.lo[0], -0.3f, 1.0e-3f);
    CHECK_FLOAT_EQ(sc.hi[1], 0.2f, 1.0e-3f);
    CHECK_FLOAT_EQ(sc.lo[1], -0.2f, 1.0e-3f);
    CHECK(sc.vmax <= 0.475f);
    CHECK_FLOAT_EQ(sc.end.x_m, 0.0f, 1.0e-4f);
    CHECK_FLOAT_EQ(sc.end.y_m, 0.0f, 1.0e-4f);

    /* twice as fast: peak accel above a_max */
    fresh();
    add(WFB_ATOM_LISSA, WFB_PROF_SINE, 0.3f, 0.2f, 4.0f, 0.0f, 0.0f, p, 10);
    CHECK(upload(0u) == WFB_ERR_SPEED);

    /* x amplitude past the fence */
    fresh();
    p[0] = 1.5f;
    add(WFB_ATOM_LISSA, WFB_PROF_SINE, 0.05f, 0.2f, 4.0f, 0.0f, 0.0f, p, 10);
    CHECK(upload(0u) == WFB_ERR_BOUNDS);
}

static void test_rejections(void)
{
    float p[4] = {0.5f, 0.0f, HZ, 0.0f};

    fresh();
    line(WFB_PROF_TRAP, 0.5f, 0.0f, 0.0f, 1.5f, 0.0f, HZ);
    CHECK(upload(0u) == WFB_ERR_BOUNDS);
    CHECK(s_pr.err_seg == 0u);

    fresh();
    line(WFB_PROF_TRAP, 1.2f, 0.0f, 0.0f, 0.5f, 0.0f, HZ);
    CHECK(upload(0u) == WFB_ERR_SPEED);

    fresh();
    add(WFB_ATOM_LINE, WFB_PROF_TRAP, 0.5f, 2.0f, 4.0f, 0.0f, 0.0f, p, 4);
    CHECK(upload(0u) == WFB_ERR_SPEED);

    fresh();
    add(WFB_ATOM_LINE, WFB_PROF_SCURVE, 0.5f, 1.0f, 0.0f, 0.0f, 0.0f, p, 4);
    CHECK(upload(0u) == WFB_ERR_RANGE);  /* SCURVE needs j > 0 */

    fresh();
    add(WFB_ATOM_LINE, WFB_PROF_TRAP, 0.5f, 1.0f, 4.0f, 0.6f, 0.0f, p, 4);
    CHECK(upload(0u) == WFB_ERR_RANGE);  /* v_in > v */

    fresh();
    line(WFB_PROF_TRAP, 0.5f, 0.0f, 0.0f, 0.5f, 0.0f, HZ);
    CHECK(upload(0u) == WFB_ERR_ENDPOINT);
    CHECK(s_pr.err_seg == 1u);

    fresh();
    hold(121.0f);
    CHECK(upload(0u) == WFB_ERR_TIME);
    fresh();
    hold(70.0f);
    hold(70.0f);
    CHECK(upload(0u) == WFB_ERR_TIME);
    CHECK(s_pr.err_seg == 2u);

    fresh();
    line(WFB_PROF_TRAP, 0.5f, 0.0f, 0.0f, 0.5f, 0.0f, HZ);
    line(WFB_PROF_TRAP, 0.5f, 0.0f, 0.0f, 0.0f, 0.0f, HZ);
    CHECK(upload(1u) == WFB_ERR_CRC);
    CHECK(s_pr.state == (uint8_t)WFB_TRAJ_EMPTY);

    /* framing */
    fresh();
    CHECK(send(WFB_PROG_F_V, 0.5f) == WFB_ERR_STATE);          /* field before BEGIN */
    CHECK(send(WFB_PROG_IDX_BEGIN, 0.0f) == WFB_ERR_COUNT);
    CHECK(send(WFB_PROG_IDX_BEGIN, 65.0f) == WFB_ERR_COUNT);
    CHECK(send(WFB_PROG_IDX_BEGIN, 1.5f) == WFB_ERR_COUNT);
    CHECK(send(40u, 0.0f) == WFB_ERR_RANGE);
    CHECK(send(WFB_PROG_IDX_BEGIN, 2.0f) == WFB_ERR_NONE);
    CHECK(send(WFB_PROG_F_V, NAN) == WFB_ERR_RANGE);
    CHECK(send(WFB_PROG_F_V, 2.0e6f) == WFB_ERR_RANGE);
    CHECK(send(WFB_PROG_IDX_PUSH, 1.0f) == WFB_ERR_COUNT);     /* out of sequence */
    CHECK(send(WFB_PROG_F_ATOM, 5.0f) == WFB_ERR_NONE);
    CHECK(send(WFB_PROG_IDX_PUSH, 0.0f) == WFB_ERR_RANGE);     /* no such atom */
    CHECK(send(WFB_PROG_F_ATOM, 1.5f) == WFB_ERR_NONE);
    CHECK(send(WFB_PROG_IDX_PUSH, 0.0f) == WFB_ERR_RANGE);
    CHECK(send(WFB_PROG_F_ATOM, 1.0f) == WFB_ERR_NONE);
    CHECK(send(WFB_PROG_F_PROFILE, 4.0f) == WFB_ERR_NONE);
    CHECK(send(WFB_PROG_IDX_PUSH, 0.0f) == WFB_ERR_RANGE);     /* no such profile */
    CHECK(send(WFB_PROG_IDX_COMMIT, 0.0f) == WFB_ERR_STATE);   /* no CRC_HI */
    CHECK(s_pr.state == (uint8_t)WFB_TRAJ_EMPTY);
    CHECK(send(WFB_PROG_IDX_CRC_HI, 0.0f) == WFB_ERR_STATE);
}

static void test_lifecycle(void)
{
    wfb_traj_point_t pt;
    rec_t zero;

    fresh();
    line(WFB_PROF_TRAP, 0.5f, 0.0f, 0.0f, 0.5f, 0.0f, HZ);
    line(WFB_PROF_TRAP, 0.5f, 0.0f, 0.0f, 0.0f, 0.0f, HZ);
    CHECK(wfb_prog_start(&s_pr, 0.0f) == WFB_ERR_STATE);
    CHECK(upload(0u) == WFB_ERR_NONE);
    CHECK(wfb_prog_sample(&s_pr, 0.0f, &pt) == 0);              /* READY: not running */
    CHECK(wfb_prog_stop(&s_pr) == WFB_ERR_STATE);
    CHECK(wfb_prog_start(&s_pr, NAN) == WFB_ERR_RANGE);
    CHECK(wfb_prog_start(&s_pr, 0.0f) == WFB_ERR_NONE);
    CHECK(send(WFB_PROG_IDX_BEGIN, 1.0f) == WFB_ERR_STATE);
    CHECK(wfb_prog_clear(&s_pr) == WFB_ERR_STATE);
    CHECK(wfb_prog_sample(&s_pr, 1.0f, &pt) == 1);
    CHECK(wfb_prog_sample(&s_pr, 0.2f, &pt) == 1);              /* backwards in time */
    CHECK(pt.x_m > 0.0f && pt.x_m < 0.5f);
    CHECK(wfb_prog_sample(&s_pr, s_pr.t_total + 1.0f, &pt) == 0);
    CHECK(s_pr.state == (uint8_t)WFB_TRAJ_DONE);
    CHECK_FLOAT_EQ(pt.x_m, 0.0f, 1.0e-6f);
    CHECK(wfb_prog_sample(&s_pr, 0.0f, &pt) == 0);
    CHECK_FLOAT_EQ(pt.z_m, HZ, 1.0e-6f);
    CHECK(wfb_prog_clear(&s_pr) == WFB_ERR_NONE);
    CHECK(s_pr.state == (uint8_t)WFB_TRAJ_EMPTY);
    CHECK(wfb_prog_start(&s_pr, 0.0f) == WFB_ERR_STATE);

    /* BEGIN zeroes the staging record: a bare PUSH is HOLD 0 */
    memset(&zero, 0, sizeof(zero));
    CHECK(send(WFB_PROG_IDX_BEGIN, 1.0f) == WFB_ERR_NONE);
    CHECK(send(WFB_PROG_IDX_PUSH, 0.0f) == WFB_ERR_NONE);
    CHECK(s_buf[0].f[WFB_PROG_F_ATOM] == 0.0f && s_buf[0].f[WFB_PROG_F_P0] == 0.0f);
    {
        uint32_t crc = wfb_crc32((const uint8_t *)&zero, (uint32_t)sizeof(zero));
        CHECK(send(WFB_PROG_IDX_CRC_HI, (float)(crc >> 16)) == WFB_ERR_NONE);
        CHECK(send(WFB_PROG_IDX_COMMIT, (float)(crc & 0xFFFFu)) == WFB_ERR_NONE);
    }
    CHECK_FLOAT_EQ(s_pr.t_total, 0.0f, 1.0e-9f);
}

int main(void)
{
    wfb_traj_default_limits(&s_lim);
    wfb_prog_default_caps(&s_caps);
    test_ramp_F();
    test_profiles();
    test_planner_limits();
    test_turn_hold();
    test_arc();
    test_lissa();
    test_rejections();
    test_lifecycle();
    printf("%s: %d checks, %d failures\n", (s_fail_count == 0) ? "PASS" : "FAIL", s_check_count, s_fail_count);
    return (s_fail_count == 0) ? 0 : 1;
}
