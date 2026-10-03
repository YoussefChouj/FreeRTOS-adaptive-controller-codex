#include <stdio.h>
#include <stdint.h>
#include <math.h>
#include <string.h>
#include "wfb_traj.h"

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
    if (fabsf((a) - (b)) > (tol)) { \
        s_fail_count++; \
        printf("FAIL %s:%d: fabsf(%s - %s) = %f > %f\n", \
               __FILE__, __LINE__, #a, #b, (double)fabsf((a) - (b)), (double)(tol)); \
    } \
} while (0)

static wfb_err_t load_and_commit(wfb_traj_t *tr, const wfb_traj_point_t *pts, uint16_t n,
                                 const wfb_traj_limits_t *lim, float hover_z_m)
{
    wfb_err_t err;
    uint32_t crc;
    uint16_t i;
    float hi;
    float lo;

    err = wfb_traj_begin(tr, (float)n);
    if (err != WFB_ERR_NONE) {
        return err;
    }

    for (i = 0u; i < n; i++) {
        err = wfb_traj_append(tr, pts[i].x_m);
        if (err != WFB_ERR_NONE) {
            return err;
        }
        err = wfb_traj_append(tr, pts[i].y_m);
        if (err != WFB_ERR_NONE) {
            return err;
        }
        err = wfb_traj_append(tr, pts[i].z_m);
        if (err != WFB_ERR_NONE) {
            return err;
        }
        err = wfb_traj_append(tr, pts[i].yaw_deg);
        if (err != WFB_ERR_NONE) {
            return err;
        }
        err = wfb_traj_append(tr, pts[i].t_s);
        if (err != WFB_ERR_NONE) {
            return err;
        }
    }

    crc = wfb_crc32((const uint8_t *)tr->buf, (uint32_t)n * (uint32_t)sizeof(wfb_traj_point_t));
    hi = (float)(crc >> 16);
    lo = (float)(crc & 0xFFFFu);

    err = wfb_traj_crc_hi(tr, hi);
    if (err != WFB_ERR_NONE) {
        return err;
    }

    return wfb_traj_commit(tr, lo, lim, hover_z_m);
}

/* 1. wfb_crc32 of the 9 ASCII bytes "123456789" equals 0xCBF43926. */
static void test_crc32(void)
{
    const uint8_t sample[9] = { '1', '2', '3', '4', '5', '6', '7', '8', '9' };
    uint32_t crc;

    crc = wfb_crc32(sample, 9u);
    CHECK(crc == 0xCBF43926u);
}

/* 2. begin: rejects N=1, N=cap+1, N=2.5 (WFB_ERR_RANGE); rejects while EXECUTING (WFB_ERR_STATE); accepts N=2. */
static void test_begin(void)
{
    wfb_traj_t tr;
    wfb_traj_point_t buf[10];

    wfb_traj_init(&tr, buf, 10u);
    CHECK(tr.state == (uint8_t)WFB_TRAJ_EMPTY);

    /* rejects N=1 */
    CHECK(wfb_traj_begin(&tr, 1.0f) == WFB_ERR_RANGE);
    CHECK(tr.state == (uint8_t)WFB_TRAJ_EMPTY);

    /* rejects N=cap+1 */
    CHECK(wfb_traj_begin(&tr, 11.0f) == WFB_ERR_RANGE);
    CHECK(tr.state == (uint8_t)WFB_TRAJ_EMPTY);

    /* rejects N=2.5 */
    CHECK(wfb_traj_begin(&tr, 2.5f) == WFB_ERR_RANGE);
    CHECK(tr.state == (uint8_t)WFB_TRAJ_EMPTY);

    /* accepts N=2 */
    CHECK(wfb_traj_begin(&tr, 2.0f) == WFB_ERR_NONE);
    CHECK(tr.state == (uint8_t)WFB_TRAJ_LOADING);
    CHECK(tr.n == 2u);

    /* rejects while EXECUTING */
    tr.state = (uint8_t)WFB_TRAJ_EXECUTING;
    CHECK(wfb_traj_begin(&tr, 2.0f) == WFB_ERR_STATE);
    CHECK(tr.state == (uint8_t)WFB_TRAJ_EXECUTING);
}

/* 3. append: WFB_ERR_STATE when not LOADING; WFB_ERR_RANGE for NaN and infinity; WFB_ERR_COUNT for float number 5N+1. */
static void test_append(void)
{
    wfb_traj_t tr;
    wfb_traj_point_t buf[10];
    uint32_t i;

    wfb_traj_init(&tr, buf, 10u);

    /* WFB_ERR_STATE when not LOADING */
    CHECK(wfb_traj_append(&tr, 0.0f) == WFB_ERR_STATE);

    CHECK(wfb_traj_begin(&tr, 2.0f) == WFB_ERR_NONE);
    CHECK(tr.state == (uint8_t)WFB_TRAJ_LOADING);

    /* WFB_ERR_RANGE for NaN and infinity */
    CHECK(wfb_traj_append(&tr, (float)NAN) == WFB_ERR_RANGE);
    CHECK(wfb_traj_append(&tr, (float)INFINITY) == WFB_ERR_RANGE);
    CHECK(wfb_traj_append(&tr, -(float)INFINITY) == WFB_ERR_RANGE);
    CHECK(tr.rx == 0u);

    /* Append 5N floats (10 floats for N=2) */
    for (i = 0u; i < 10u; i++) {
        CHECK(wfb_traj_append(&tr, (float)i) == WFB_ERR_NONE);
    }
    CHECK(tr.rx == 10u);

    /* float number 5N+1 */
    CHECK(wfb_traj_append(&tr, 42.0f) == WFB_ERR_COUNT);
    CHECK(tr.rx == 10u);
}

/* 4. commit while LOADING: WFB_ERR_STATE without crc_hi; WFB_ERR_COUNT with fewer than 5N floats;
      WFB_ERR_RANGE for crc_lo = 65536, -1, 0.5 or NaN; WFB_ERR_CRC on a wrong CRC. Every commit that fails while LOADING leaves state EMPTY.
      commit in any other state (EMPTY, READY, EXECUTING, DONE) returns WFB_ERR_STATE and changes nothing:
      a READY trajectory stays READY, an EXECUTING one keeps executing. */
static void test_commit(void)
{
    wfb_traj_t tr;
    wfb_traj_point_t buf[10];
    wfb_traj_limits_t lim;
    uint32_t i;
    wfb_traj_point_t pts[2] = {
        { 0.0f, 0.0f, 0.5f, 0.0f, 0.0f },
        { 0.0f, 0.0f, 0.5f, 0.0f, 1.0f }
    };
    uint32_t crc;
    float crc_lo_val;

    wfb_traj_default_limits(&lim);

    /* 1. commit while LOADING without crc_hi -> WFB_ERR_STATE, leaves state EMPTY */
    wfb_traj_init(&tr, buf, 10u);
    CHECK(wfb_traj_begin(&tr, 2.0f) == WFB_ERR_NONE);
    for (i = 0u; i < 10u; i++) {
        CHECK(wfb_traj_append(&tr, 0.0f) == WFB_ERR_NONE);
    }
    CHECK(wfb_traj_commit(&tr, 0.0f, &lim, 0.5f) == WFB_ERR_STATE);
    CHECK(tr.state == (uint8_t)WFB_TRAJ_EMPTY);

    /* 2. commit while LOADING with fewer than 5N floats -> WFB_ERR_COUNT, leaves state EMPTY */
    CHECK(wfb_traj_begin(&tr, 2.0f) == WFB_ERR_NONE);
    CHECK(wfb_traj_crc_hi(&tr, 0.0f) == WFB_ERR_NONE);
    for (i = 0u; i < 9u; i++) {
        CHECK(wfb_traj_append(&tr, 0.0f) == WFB_ERR_NONE);
    }
    CHECK(wfb_traj_commit(&tr, 0.0f, &lim, 0.5f) == WFB_ERR_COUNT);
    CHECK(tr.state == (uint8_t)WFB_TRAJ_EMPTY);

    /* 3. commit while LOADING: crc_lo = 65536 -> WFB_ERR_RANGE, leaves state EMPTY */
    CHECK(wfb_traj_begin(&tr, 2.0f) == WFB_ERR_NONE);
    CHECK(wfb_traj_crc_hi(&tr, 0.0f) == WFB_ERR_NONE);
    for (i = 0u; i < 10u; i++) {
        CHECK(wfb_traj_append(&tr, 0.0f) == WFB_ERR_NONE);
    }
    CHECK(wfb_traj_commit(&tr, 65536.0f, &lim, 0.5f) == WFB_ERR_RANGE);
    CHECK(tr.state == (uint8_t)WFB_TRAJ_EMPTY);

    /* crc_lo = -1 -> WFB_ERR_RANGE, leaves state EMPTY */
    CHECK(wfb_traj_begin(&tr, 2.0f) == WFB_ERR_NONE);
    CHECK(wfb_traj_crc_hi(&tr, 0.0f) == WFB_ERR_NONE);
    for (i = 0u; i < 10u; i++) {
        CHECK(wfb_traj_append(&tr, 0.0f) == WFB_ERR_NONE);
    }
    CHECK(wfb_traj_commit(&tr, -1.0f, &lim, 0.5f) == WFB_ERR_RANGE);
    CHECK(tr.state == (uint8_t)WFB_TRAJ_EMPTY);

    /* crc_lo = 0.5 -> WFB_ERR_RANGE, leaves state EMPTY */
    CHECK(wfb_traj_begin(&tr, 2.0f) == WFB_ERR_NONE);
    CHECK(wfb_traj_crc_hi(&tr, 0.0f) == WFB_ERR_NONE);
    for (i = 0u; i < 10u; i++) {
        CHECK(wfb_traj_append(&tr, 0.0f) == WFB_ERR_NONE);
    }
    CHECK(wfb_traj_commit(&tr, 0.5f, &lim, 0.5f) == WFB_ERR_RANGE);
    CHECK(tr.state == (uint8_t)WFB_TRAJ_EMPTY);

    /* crc_lo = NaN -> WFB_ERR_RANGE, leaves state EMPTY */
    CHECK(wfb_traj_begin(&tr, 2.0f) == WFB_ERR_NONE);
    CHECK(wfb_traj_crc_hi(&tr, 0.0f) == WFB_ERR_NONE);
    for (i = 0u; i < 10u; i++) {
        CHECK(wfb_traj_append(&tr, 0.0f) == WFB_ERR_NONE);
    }
    CHECK(wfb_traj_commit(&tr, (float)NAN, &lim, 0.5f) == WFB_ERR_RANGE);
    CHECK(tr.state == (uint8_t)WFB_TRAJ_EMPTY);

    /* 4. commit with wrong CRC -> WFB_ERR_CRC, leaves state EMPTY */
    CHECK(wfb_traj_begin(&tr, 2.0f) == WFB_ERR_NONE);
    CHECK(wfb_traj_crc_hi(&tr, 0.0f) == WFB_ERR_NONE);
    for (i = 0u; i < 10u; i++) {
        CHECK(wfb_traj_append(&tr, 0.0f) == WFB_ERR_NONE);
    }
    CHECK(wfb_traj_commit(&tr, 0.0f, &lim, 0.5f) == WFB_ERR_CRC);
    CHECK(tr.state == (uint8_t)WFB_TRAJ_EMPTY);

    /* 5. commit in any other state returns WFB_ERR_STATE and changes nothing */
    /* In EMPTY */
    tr.state = (uint8_t)WFB_TRAJ_EMPTY;
    CHECK(wfb_traj_commit(&tr, 0.0f, &lim, 0.5f) == WFB_ERR_STATE);
    CHECK(tr.state == (uint8_t)WFB_TRAJ_EMPTY);

    /* Load valid trajectory to reach READY */
    CHECK(load_and_commit(&tr, pts, 2u, &lim, 0.5f) == WFB_ERR_NONE);
    CHECK(tr.state == (uint8_t)WFB_TRAJ_READY);

    /* In READY -> stays READY */
    crc = tr.crc_calc;
    crc_lo_val = (float)(crc & 0xFFFFu);
    CHECK(wfb_traj_commit(&tr, crc_lo_val, &lim, 0.5f) == WFB_ERR_STATE);
    CHECK(tr.state == (uint8_t)WFB_TRAJ_READY);

    /* In EXECUTING -> keeps EXECUTING */
    CHECK(wfb_traj_start(&tr) == WFB_ERR_NONE);
    CHECK(tr.state == (uint8_t)WFB_TRAJ_EXECUTING);
    CHECK(wfb_traj_commit(&tr, crc_lo_val, &lim, 0.5f) == WFB_ERR_STATE);
    CHECK(tr.state == (uint8_t)WFB_TRAJ_EXECUTING);

    /* In DONE -> stays DONE */
    tr.state = (uint8_t)WFB_TRAJ_DONE;
    CHECK(wfb_traj_commit(&tr, crc_lo_val, &lim, 0.5f) == WFB_ERR_STATE);
    CHECK(tr.state == (uint8_t)WFB_TRAJ_DONE);
}

/* 5. commit check order, one test per check, each with a correct CRC:
      t[0] != 0 -> TIME; equal or decreasing t -> TIME; |x| over x_abs_m -> BOUNDS;
      z under z_min_m -> BOUNDS; first point off the hover point -> ENDPOINT;
      last point off -> ENDPOINT; a too-fast segment -> SPEED.
      A trajectory failing two checks reports the earlier one. */
static void test_commit_check_order(void)
{
    wfb_traj_t tr;
    wfb_traj_point_t buf[10];
    wfb_traj_limits_t lim;
    wfb_traj_point_t pts[2];

    wfb_traj_default_limits(&lim);
    wfb_traj_init(&tr, buf, 10u);

    /* Check 2: t[0] != 0 -> TIME */
    pts[0].x_m = 0.0f; pts[0].y_m = 0.0f; pts[0].z_m = 0.5f; pts[0].yaw_deg = 0.0f; pts[0].t_s = 0.1f;
    pts[1].x_m = 0.0f; pts[1].y_m = 0.0f; pts[1].z_m = 0.5f; pts[1].yaw_deg = 0.0f; pts[1].t_s = 1.0f;
    CHECK(load_and_commit(&tr, pts, 2u, &lim, 0.5f) == WFB_ERR_TIME);
    CHECK(tr.state == (uint8_t)WFB_TRAJ_EMPTY);

    /* Check 2: equal t -> TIME */
    pts[0].t_s = 0.0f;
    pts[1].t_s = 0.0f;
    CHECK(load_and_commit(&tr, pts, 2u, &lim, 0.5f) == WFB_ERR_TIME);
    CHECK(tr.state == (uint8_t)WFB_TRAJ_EMPTY);

    /* Check 2: decreasing t -> TIME */
    pts[0].t_s = 0.0f;
    pts[1].t_s = -0.5f;
    CHECK(load_and_commit(&tr, pts, 2u, &lim, 0.5f) == WFB_ERR_TIME);
    CHECK(tr.state == (uint8_t)WFB_TRAJ_EMPTY);

    /* Check 3: |x| over x_abs_m -> BOUNDS */
    pts[0].x_m = 1.4f; pts[0].y_m = 0.0f; pts[0].z_m = 0.5f; pts[0].yaw_deg = 0.0f; pts[0].t_s = 0.0f;
    pts[1].x_m = 0.0f; pts[1].y_m = 0.0f; pts[1].z_m = 0.5f; pts[1].yaw_deg = 0.0f; pts[1].t_s = 1.0f;
    CHECK(load_and_commit(&tr, pts, 2u, &lim, 0.5f) == WFB_ERR_BOUNDS);
    CHECK(tr.state == (uint8_t)WFB_TRAJ_EMPTY);

    /* Check 3: z under z_min_m -> BOUNDS */
    pts[0].x_m = 0.0f; pts[0].y_m = 0.0f; pts[0].z_m = 0.2f; pts[0].yaw_deg = 0.0f; pts[0].t_s = 0.0f;
    pts[1].x_m = 0.0f; pts[1].y_m = 0.0f; pts[1].z_m = 0.5f; pts[1].yaw_deg = 0.0f; pts[1].t_s = 1.0f;
    CHECK(load_and_commit(&tr, pts, 2u, &lim, 0.5f) == WFB_ERR_BOUNDS);
    CHECK(tr.state == (uint8_t)WFB_TRAJ_EMPTY);

    /* Check 4: first point off the hover point -> ENDPOINT */
    pts[0].x_m = 0.08f; pts[0].y_m = 0.08f; pts[0].z_m = 0.5f; pts[0].yaw_deg = 0.0f; pts[0].t_s = 0.0f;
    pts[1].x_m = 0.0f;  pts[1].y_m = 0.0f;  pts[1].z_m = 0.5f; pts[1].yaw_deg = 0.0f; pts[1].t_s = 1.0f;
    CHECK(load_and_commit(&tr, pts, 2u, &lim, 0.5f) == WFB_ERR_ENDPOINT);
    CHECK(tr.state == (uint8_t)WFB_TRAJ_EMPTY);

    /* Check 4: last point off -> ENDPOINT */
    pts[0].x_m = 0.0f;  pts[0].y_m = 0.0f;  pts[0].z_m = 0.5f; pts[0].yaw_deg = 0.0f; pts[0].t_s = 0.0f;
    pts[1].x_m = 0.08f; pts[1].y_m = 0.08f; pts[1].z_m = 0.5f; pts[1].yaw_deg = 0.0f; pts[1].t_s = 1.0f;
    CHECK(load_and_commit(&tr, pts, 2u, &lim, 0.5f) == WFB_ERR_ENDPOINT);
    CHECK(tr.state == (uint8_t)WFB_TRAJ_EMPTY);

    /* Check 5: a too-fast segment -> SPEED */
    pts[0].x_m = 0.0f;  pts[0].y_m = 0.0f; pts[0].z_m = 0.5f; pts[0].yaw_deg = 0.0f; pts[0].t_s = 0.0f;
    pts[1].x_m = 0.05f; pts[1].y_m = 0.0f; pts[1].z_m = 0.5f; pts[1].yaw_deg = 0.0f; pts[1].t_s = 0.01f;
    CHECK(load_and_commit(&tr, pts, 2u, &lim, 0.5f) == WFB_ERR_SPEED);
    CHECK(tr.state == (uint8_t)WFB_TRAJ_EMPTY);

    /* Failing two checks reports the earlier one:
       Case A: TIME and BOUNDS -> reports TIME */
    pts[0].x_m = 1.4f; pts[0].y_m = 0.0f; pts[0].z_m = 0.5f; pts[0].yaw_deg = 0.0f; pts[0].t_s = 0.1f;
    pts[1].x_m = 0.0f; pts[1].y_m = 0.0f; pts[1].z_m = 0.5f; pts[1].yaw_deg = 0.0f; pts[1].t_s = 1.0f;
    CHECK(load_and_commit(&tr, pts, 2u, &lim, 0.5f) == WFB_ERR_TIME);
    CHECK(tr.state == (uint8_t)WFB_TRAJ_EMPTY);

    /* Case B: BOUNDS and ENDPOINT -> reports BOUNDS */
    pts[0].x_m = 1.4f; pts[0].y_m = 0.0f; pts[0].z_m = 0.5f; pts[0].yaw_deg = 0.0f; pts[0].t_s = 0.0f;
    pts[1].x_m = 0.0f; pts[1].y_m = 0.0f; pts[1].z_m = 0.5f; pts[1].yaw_deg = 0.0f; pts[1].t_s = 1.0f;
    CHECK(load_and_commit(&tr, pts, 2u, &lim, 0.5f) == WFB_ERR_BOUNDS);
    CHECK(tr.state == (uint8_t)WFB_TRAJ_EMPTY);

    /* Case C: ENDPOINT and SPEED -> reports ENDPOINT */
    pts[0].x_m = 0.0f; pts[0].y_m = 0.0f; pts[0].z_m = 0.5f; pts[0].yaw_deg = 0.0f; pts[0].t_s = 0.0f;
    pts[1].x_m = 0.2f; pts[1].y_m = 0.0f; pts[1].z_m = 0.5f; pts[1].yaw_deg = 0.0f; pts[1].t_s = 0.01f;
    CHECK(load_and_commit(&tr, pts, 2u, &lim, 0.5f) == WFB_ERR_ENDPOINT);
    CHECK(tr.state == (uint8_t)WFB_TRAJ_EMPTY);
}

/* 6. valid 3-point trajectory -> READY and crc_calc equals the sent CRC; start -> EXECUTING;
      sample(t=0) = first point; sample at a segment's mid time = the linear midpoint;
      yaw 170 -> -170 passes through +/-180 at the midpoint (shortest arc);
      sample(t >= last t) returns 0, out = last point, state DONE. */
static void test_trajectory_execution_and_sample(void)
{
    wfb_traj_t tr;
    wfb_traj_point_t buf[10];
    wfb_traj_limits_t lim;
    wfb_traj_point_t pts[3] = {
        { 0.0f, 0.0f, 0.5f,  170.0f, 0.0f },
        { 0.2f, 0.4f, 0.6f, -170.0f, 1.0f },
        { 0.0f, 0.0f, 0.5f,    0.0f, 2.0f }
    };
    wfb_traj_point_t out;
    uint32_t expected_crc;
    int ret;

    wfb_traj_default_limits(&lim);
    wfb_traj_init(&tr, buf, 10u);

    CHECK(load_and_commit(&tr, pts, 3u, &lim, 0.5f) == WFB_ERR_NONE);
    CHECK(tr.state == (uint8_t)WFB_TRAJ_READY);

    expected_crc = wfb_crc32((const uint8_t *)tr.buf, 3u * (uint32_t)sizeof(wfb_traj_point_t));
    CHECK(tr.crc_calc == expected_crc);

    /* start -> EXECUTING */
    CHECK(wfb_traj_start(&tr) == WFB_ERR_NONE);
    CHECK(tr.state == (uint8_t)WFB_TRAJ_EXECUTING);

    /* sample(t=0) = first point */
    memset(&out, 0, sizeof(out));
    ret = wfb_traj_sample(&tr, 0.0f, &out);
    CHECK(ret == 1);
    CHECK_FLOAT_EQ(out.x_m, 0.0f, 1e-4f);
    CHECK_FLOAT_EQ(out.y_m, 0.0f, 1e-4f);
    CHECK_FLOAT_EQ(out.z_m, 0.5f, 1e-4f);
    CHECK_FLOAT_EQ(out.yaw_deg, 170.0f, 1e-4f);
    CHECK_FLOAT_EQ(out.t_s, 0.0f, 1e-4f);

    /* sample at segment's mid time (t = 0.5) = linear midpoint */
    memset(&out, 0, sizeof(out));
    ret = wfb_traj_sample(&tr, 0.5f, &out);
    CHECK(ret == 1);
    CHECK_FLOAT_EQ(out.x_m, 0.1f, 1e-4f);
    CHECK_FLOAT_EQ(out.y_m, 0.2f, 1e-4f);
    CHECK_FLOAT_EQ(out.z_m, 0.55f, 1e-4f);
    CHECK_FLOAT_EQ(out.t_s, 0.5f, 1e-4f);

    /* yaw 170 -> -170 passes through +/-180 at the midpoint (shortest arc) */
    CHECK(fabsf(fabsf(out.yaw_deg) - 180.0f) <= 1e-4f);

    /* sample(t >= last t) returns 0, out = last point, state DONE */
    memset(&out, 0, sizeof(out));
    ret = wfb_traj_sample(&tr, 2.0f, &out);
    CHECK(ret == 0);
    CHECK_FLOAT_EQ(out.x_m, 0.0f, 1e-4f);
    CHECK_FLOAT_EQ(out.y_m, 0.0f, 1e-4f);
    CHECK_FLOAT_EQ(out.z_m, 0.5f, 1e-4f);
    CHECK_FLOAT_EQ(out.yaw_deg, 0.0f, 1e-4f);
    CHECK_FLOAT_EQ(out.t_s, 2.0f, 1e-4f);
    CHECK(tr.state == (uint8_t)WFB_TRAJ_DONE);

    /* Calling sample again when t > last t while in DONE */
    memset(&out, 0, sizeof(out));
    ret = wfb_traj_sample(&tr, 2.5f, &out);
    CHECK(ret == 0);
    CHECK_FLOAT_EQ(out.x_m, 0.0f, 1e-4f);
    CHECK_FLOAT_EQ(out.y_m, 0.0f, 1e-4f);
    CHECK_FLOAT_EQ(out.z_m, 0.5f, 1e-4f);
    CHECK_FLOAT_EQ(out.yaw_deg, 0.0f, 1e-4f);
    CHECK_FLOAT_EQ(out.t_s, 2.0f, 1e-4f);
    CHECK(tr.state == (uint8_t)WFB_TRAJ_DONE);
}

/* 7. stop from EXECUTING -> READY; stop otherwise -> WFB_ERR_STATE;
      clear while EXECUTING -> WFB_ERR_STATE; start when not READY -> WFB_ERR_STATE. */
static void test_state_transitions(void)
{
    wfb_traj_t tr;
    wfb_traj_point_t buf[10];
    wfb_traj_limits_t lim;
    wfb_traj_point_t out;
    wfb_traj_point_t pts[2] = {
        { 0.0f, 0.0f, 0.5f, 0.0f, 0.0f },
        { 0.0f, 0.0f, 0.5f, 0.0f, 1.0f }
    };

    wfb_traj_default_limits(&lim);
    wfb_traj_init(&tr, buf, 10u);

    /* start when not READY -> WFB_ERR_STATE (in EMPTY) */
    CHECK(wfb_traj_start(&tr) == WFB_ERR_STATE);

    /* in LOADING */
    CHECK(wfb_traj_begin(&tr, 2.0f) == WFB_ERR_NONE);
    CHECK(wfb_traj_start(&tr) == WFB_ERR_STATE);

    /* clear while not EXECUTING -> WFB_ERR_NONE */
    CHECK(wfb_traj_clear(&tr) == WFB_ERR_NONE);
    CHECK(tr.state == (uint8_t)WFB_TRAJ_EMPTY);

    /* stop otherwise -> WFB_ERR_STATE (in EMPTY) */
    CHECK(wfb_traj_stop(&tr) == WFB_ERR_STATE);

    /* move to READY */
    CHECK(load_and_commit(&tr, pts, 2u, &lim, 0.5f) == WFB_ERR_NONE);
    CHECK(tr.state == (uint8_t)WFB_TRAJ_READY);

    /* stop while READY -> WFB_ERR_STATE */
    CHECK(wfb_traj_stop(&tr) == WFB_ERR_STATE);

    /* start from READY -> EXECUTING */
    CHECK(wfb_traj_start(&tr) == WFB_ERR_NONE);
    CHECK(tr.state == (uint8_t)WFB_TRAJ_EXECUTING);

    /* start when already EXECUTING -> WFB_ERR_STATE */
    CHECK(wfb_traj_start(&tr) == WFB_ERR_STATE);

    /* clear while EXECUTING -> WFB_ERR_STATE */
    CHECK(wfb_traj_clear(&tr) == WFB_ERR_STATE);
    CHECK(tr.state == (uint8_t)WFB_TRAJ_EXECUTING);

    /* stop from EXECUTING -> READY */
    CHECK(wfb_traj_stop(&tr) == WFB_ERR_NONE);
    CHECK(tr.state == (uint8_t)WFB_TRAJ_READY);

    /* transition to DONE via start then sample */
    CHECK(wfb_traj_start(&tr) == WFB_ERR_NONE);
    CHECK(wfb_traj_sample(&tr, 1.0f, &out) == 0);
    CHECK(tr.state == (uint8_t)WFB_TRAJ_DONE);

    /* start when DONE -> WFB_ERR_STATE */
    CHECK(wfb_traj_start(&tr) == WFB_ERR_STATE);

    /* stop when DONE -> WFB_ERR_STATE */
    CHECK(wfb_traj_stop(&tr) == WFB_ERR_STATE);

    /* clear when DONE -> WFB_ERR_NONE */
    CHECK(wfb_traj_clear(&tr) == WFB_ERR_NONE);
    CHECK(tr.state == (uint8_t)WFB_TRAJ_EMPTY);
}

/* 8. wfb_traj_default_limits returns the interfaces.md values (0.8, 1.3, 0.3, 1.5, 1.0, 0.10). */
static void test_default_limits(void)
{
    wfb_traj_limits_t lim;

    memset(&lim, 0, sizeof(lim));
    wfb_traj_default_limits(&lim);

    CHECK_FLOAT_EQ(lim.x_abs_m, 1.3f, 1e-6f);
    CHECK_FLOAT_EQ(lim.y_abs_m, 1.7f, 1e-6f);
    CHECK_FLOAT_EQ(lim.z_min_m, 0.3f, 1e-6f);
    CHECK_FLOAT_EQ(lim.z_max_m, 1.4f, 1e-6f);
    CHECK_FLOAT_EQ(lim.v_max_mps, 1.0f, 1e-6f);
    CHECK_FLOAT_EQ(lim.endpoint_tol_m, 0.10f, 1e-6f);
}

/* 9. crc_hi: WFB_ERR_STATE when not LOADING; WFB_ERR_RANGE for 65536, -1, 1.5 and NaN; 0 and 65535 are accepted. */
static void test_crc_hi(void)
{
    wfb_traj_t tr;
    wfb_traj_point_t buf[10];

    wfb_traj_init(&tr, buf, 10u);

    /* WFB_ERR_STATE when not LOADING */
    CHECK(wfb_traj_crc_hi(&tr, 0.0f) == WFB_ERR_STATE);

    CHECK(wfb_traj_begin(&tr, 2.0f) == WFB_ERR_NONE);
    CHECK(tr.state == (uint8_t)WFB_TRAJ_LOADING);

    /* WFB_ERR_RANGE for 65536, -1, 1.5 and NaN */
    CHECK(wfb_traj_crc_hi(&tr, 65536.0f) == WFB_ERR_RANGE);
    CHECK(wfb_traj_crc_hi(&tr, -1.0f) == WFB_ERR_RANGE);
    CHECK(wfb_traj_crc_hi(&tr, 1.5f) == WFB_ERR_RANGE);
    CHECK(wfb_traj_crc_hi(&tr, (float)NAN) == WFB_ERR_RANGE);

    /* 0 and 65535 are accepted */
    CHECK(wfb_traj_crc_hi(&tr, 0.0f) == WFB_ERR_NONE);
    CHECK(tr.crc_hi == 0u);
    CHECK(tr.crc_hi_set == 1u);

    CHECK(wfb_traj_crc_hi(&tr, 65535.0f) == WFB_ERR_NONE);
    CHECK(tr.crc_hi == 65535u);
    CHECK(tr.crc_hi_set == 1u);
}

/* 10. sample in state EMPTY, LOADING or READY returns 0 and does not write *out. */
static void test_sample_inactive_states(void)
{
    wfb_traj_t tr;
    wfb_traj_point_t buf[10];
    wfb_traj_limits_t lim;
    wfb_traj_point_t out;
    uint8_t poison[sizeof(wfb_traj_point_t)];
    int ret;
    wfb_traj_point_t pts[2] = {
        { 0.0f, 0.0f, 0.5f, 0.0f, 0.0f },
        { 0.0f, 0.0f, 0.5f, 0.0f, 1.0f }
    };

    memset(poison, 0xAA, sizeof(poison));
    wfb_traj_default_limits(&lim);
    wfb_traj_init(&tr, buf, 10u);

    /* In state EMPTY */
    CHECK(tr.state == (uint8_t)WFB_TRAJ_EMPTY);
    memcpy(&out, poison, sizeof(out));
    ret = wfb_traj_sample(&tr, 0.0f, &out);
    CHECK(ret == 0);
    CHECK(memcmp(&out, poison, sizeof(out)) == 0);

    /* In state LOADING */
    CHECK(wfb_traj_begin(&tr, 2.0f) == WFB_ERR_NONE);
    CHECK(tr.state == (uint8_t)WFB_TRAJ_LOADING);
    memcpy(&out, poison, sizeof(out));
    ret = wfb_traj_sample(&tr, 0.0f, &out);
    CHECK(ret == 0);
    CHECK(memcmp(&out, poison, sizeof(out)) == 0);

    /* In state READY */
    CHECK(load_and_commit(&tr, pts, 2u, &lim, 0.5f) == WFB_ERR_NONE);
    CHECK(tr.state == (uint8_t)WFB_TRAJ_READY);
    memcpy(&out, poison, sizeof(out));
    ret = wfb_traj_sample(&tr, 0.0f, &out);
    CHECK(ret == 0);
    CHECK(memcmp(&out, poison, sizeof(out)) == 0);
}

/* Supervisor test 11: yaw range at commit, NaN limits, and the loop-free shortest-arc wrap. */
static void test_yaw_range_and_nan_limits(void)
{
    wfb_traj_t tr;
    wfb_traj_point_t buf[10];
    wfb_traj_limits_t lim;
    wfb_traj_limits_t bad;
    wfb_traj_point_t pts[2];
    wfb_traj_point_t out;
    int ret;

    wfb_traj_default_limits(&lim);
    wfb_traj_init(&tr, buf, 10u);

    pts[0].x_m = 0.0f; pts[0].y_m = 0.0f; pts[0].z_m = 0.5f; pts[0].yaw_deg = 0.0f; pts[0].t_s = 0.0f;
    pts[1].x_m = 0.0f; pts[1].y_m = 0.0f; pts[1].z_m = 0.5f; pts[1].yaw_deg = 0.0f; pts[1].t_s = 1.0f;

    /* A huge finite yaw is accepted by append and must be rejected by commit. */
    pts[1].yaw_deg = 1.0e30f;
    CHECK(load_and_commit(&tr, pts, 2u, &lim, 0.5f) == WFB_ERR_BOUNDS);
    CHECK(tr.state == (uint8_t)WFB_TRAJ_EMPTY);

    /* Just outside +/-180 deg, either sign, either point. */
    pts[1].yaw_deg = 180.5f;
    CHECK(load_and_commit(&tr, pts, 2u, &lim, 0.5f) == WFB_ERR_BOUNDS);
    pts[1].yaw_deg = 0.0f;
    pts[0].yaw_deg = -180.5f;
    CHECK(load_and_commit(&tr, pts, 2u, &lim, 0.5f) == WFB_ERR_BOUNDS);
    CHECK(tr.state == (uint8_t)WFB_TRAJ_EMPTY);

    /* A NaN limit fails the check instead of disabling it. */
    pts[0].yaw_deg = 0.0f;
    bad = lim;
    bad.x_abs_m = NAN;
    CHECK(load_and_commit(&tr, pts, 2u, &bad, 0.5f) == WFB_ERR_BOUNDS);
    bad = lim;
    bad.z_max_m = NAN;
    CHECK(load_and_commit(&tr, pts, 2u, &bad, 0.5f) == WFB_ERR_BOUNDS);
    bad = lim;
    bad.endpoint_tol_m = NAN;
    CHECK(load_and_commit(&tr, pts, 2u, &bad, 0.5f) == WFB_ERR_ENDPOINT);
    bad = lim;
    bad.v_max_mps = NAN;
    CHECK(load_and_commit(&tr, pts, 2u, &bad, 0.5f) == WFB_ERR_SPEED);
    CHECK(tr.state == (uint8_t)WFB_TRAJ_EMPTY);

    /* Exactly +/-180 deg is accepted; 180 -> -180 is the same heading, so yaw stays on the seam. */
    pts[0].yaw_deg = 180.0f;
    pts[1].yaw_deg = -180.0f;
    CHECK(load_and_commit(&tr, pts, 2u, &lim, 0.5f) == WFB_ERR_NONE);
    CHECK(tr.state == (uint8_t)WFB_TRAJ_READY);
    CHECK(wfb_traj_start(&tr) == WFB_ERR_NONE);
    ret = wfb_traj_sample(&tr, 0.5f, &out);
    CHECK(ret == 1);
    CHECK_FLOAT_EQ(fabsf(out.yaw_deg), 180.0f, 1.0e-4f);
    CHECK(wfb_traj_stop(&tr) == WFB_ERR_NONE);

    /* -170 -> 170 crosses the seam the other way: midpoint at +/-180, quarter point at -175. */
    pts[0].yaw_deg = -170.0f;
    pts[1].yaw_deg = 170.0f;
    CHECK(load_and_commit(&tr, pts, 2u, &lim, 0.5f) == WFB_ERR_NONE);
    CHECK(wfb_traj_start(&tr) == WFB_ERR_NONE);
    ret = wfb_traj_sample(&tr, 0.25f, &out);
    CHECK(ret == 1);
    CHECK_FLOAT_EQ(out.yaw_deg, -175.0f, 1.0e-3f);
    ret = wfb_traj_sample(&tr, 0.5f, &out);
    CHECK(ret == 1);
    CHECK_FLOAT_EQ(fabsf(out.yaw_deg), 180.0f, 1.0e-3f);
    ret = wfb_traj_sample(&tr, 0.75f, &out);
    CHECK(ret == 1);
    CHECK_FLOAT_EQ(out.yaw_deg, 175.0f, 1.0e-3f);
}

int main(void)
{
    test_crc32();
    test_begin();
    test_append();
    test_commit();
    test_commit_check_order();
    test_trajectory_execution_and_sample();
    test_state_transitions();
    test_default_limits();
    test_crc_hi();
    test_sample_inactive_states();
    test_yaw_range_and_nan_limits();

    if (s_fail_count == 0) {
        printf("PASS %d\n", s_check_count);
        return 0;
    } else {
        printf("FAIL: %d checks failed out of %d\n", s_fail_count, s_check_count);
        return 1;
    }
}
