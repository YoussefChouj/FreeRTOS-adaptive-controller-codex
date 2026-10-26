/* Trajectory shapes: figure-8 tilt keeps the path length and puts z = y sin(tilt), path tangents point the way
 * of travel, yaw slew takes the short way round, and wrap never hangs.
 *   gcc -std=c99 -Wall -Werror -I../../API test_traj_shape.c ../../API/traj_shape.c -lm -o t && ./t */
#include <stdio.h>
#include <math.h>
#include "traj_shape.h"

static int s_check_count = 0;
static int s_fail_count = 0;

#define CHECK(cond) do { \
    s_check_count++; \
    if (!(cond)) { \
        s_fail_count++; \
        printf("FAIL %s:%d: %s\n", __FILE__, __LINE__, #cond); \
    } \
} while (0)

#define NEAR(a, b, tol) (fabsf((a) - (b)) <= (tol))
#define PI_F 3.14159265f

static traj_shape_t f8(uint8_t type, float a, float tilt_deg)
{
    traj_shape_t p;
    p.id = 4U;
    p.circ_r_cm = 0.0f;
    p.f8_type = type;
    p.f8_a_cm = a;
    p.tilt_s = sinf(tilt_deg * PI_F / 180.0f);
    p.tilt_c = cosf(tilt_deg * PI_F / 180.0f);
    return p;
}

/* Largest heading change per path radian over one lap, and the largest heading jump between samples. */
static void lap_rates(const traj_shape_t *p, float *max_dpsi, float *max_jump)
{
    int i, n = 2000;
    float prev = TrajShape_TangentDeg(p, 0.0f, 1.0f);
    *max_dpsi = 0.0f;
    *max_jump = 0.0f;
    for (i = 1; i <= n; i++) {
        float th = 2.0f * PI_F * (float)i / (float)n;
        float h = TrajShape_TangentDeg(p, th, 1.0f);
        float d = fabsf(TrajShape_WrapDeg(h - prev));
        if (d > *max_jump) { *max_jump = d; }
        if (d / (2.0f * PI_F / (float)n) > *max_dpsi) { *max_dpsi = d / (2.0f * PI_F / (float)n); }
        prev = h;
    }
}

int main(void)
{
    traj_shape_t g = f8(1U, 70.0f, 0.0f);
    traj_shape_t gt = f8(1U, 70.0f, 30.0f);
    traj_shape_t b = f8(0U, 70.0f, 0.0f);
    traj_shape_t c;
    float dx, dy, dz, r, jump, cmd;
    int i;

    c.id = 3U; c.circ_r_cm = 40.0f; c.f8_type = 0U; c.f8_a_cm = 0.0f; c.tilt_s = 0.0f; c.tilt_c = 1.0f;

    /* flat Gerono: starts and crosses at the start point, y lobe tip at a */
    TrajShape_Offset(&g, 0.0f, &dx, &dy, &dz);
    CHECK(NEAR(dx, 0.0f, 1e-3f) && NEAR(dy, 0.0f, 1e-3f) && dz == 0.0f);
    TrajShape_Offset(&g, 0.5f * PI_F, &dx, &dy, &dz);
    CHECK(NEAR(dx, 0.0f, 1e-3f) && NEAR(dy, 70.0f, 1e-3f) && dz == 0.0f);
    TrajShape_Offset(&g, PI_F, &dx, &dy, &dz);
    CHECK(NEAR(dx, 0.0f, 1e-3f) && NEAR(dy, 0.0f, 1e-3f));

    /* tilt 30: the +y lobe climbs 35 cm and shrinks to 60.6 cm on y, the -y lobe sinks; length kept */
    TrajShape_Offset(&gt, 0.5f * PI_F, &dx, &dy, &dz);
    CHECK(NEAR(dz, 35.0f, 1e-2f) && NEAR(dy, 70.0f * cosf(PI_F / 6.0f), 1e-2f));
    CHECK(NEAR(sqrtf(dy * dy + dz * dz), 70.0f, 1e-2f));
    TrajShape_Offset(&gt, 1.5f * PI_F, &dx, &dy, &dz);
    CHECK(NEAR(dz, -35.0f, 1e-2f));
    for (i = 0; i < 64; i++) {   /* z = y tan(tilt) everywhere: one plane */
        float th = 0.1f * (float)i;
        TrajShape_Offset(&gt, th, &dx, &dy, &dz);
        CHECK(NEAR(dz, dy * tanf(PI_F / 6.0f), 1e-2f));
    }

    /* tangents: Gerono leaves at 45 deg (x and y both grow), tilt flattens the y part, reverse flips it */
    CHECK(NEAR(TrajShape_TangentDeg(&g, 0.0f, 1.0f), 45.0f, 0.1f));
    CHECK(NEAR(TrajShape_TangentDeg(&gt, 0.0f, 1.0f), atan2f(cosf(PI_F / 6.0f), 1.0f) * 180.0f / PI_F, 0.1f));
    CHECK(NEAR(TrajShape_TangentDeg(&g, 0.0f, -1.0f), -135.0f, 0.1f));
    /* Gerono at the +y lobe tip runs toward -x (180) */
    CHECK(NEAR(fabsf(TrajShape_TangentDeg(&g, 0.5f * PI_F, 1.0f)), 180.0f, 0.1f));
    /* circle leaves toward +y, then heads -x at a quarter lap; Bernoulli leaves the +x tip toward +y */
    CHECK(NEAR(TrajShape_TangentDeg(&c, 0.0f, 1.0f), 90.0f, 0.1f));
    CHECK(NEAR(fabsf(TrajShape_TangentDeg(&c, 0.5f * PI_F, 1.0f)), 180.0f, 0.1f));
    CHECK(NEAR(TrajShape_TangentDeg(&b, 0.0f, 1.0f), 90.0f, 0.1f));
    TrajShape_Offset(&c, 0.5f * PI_F, &dx, &dy, &dz);
    CHECK(NEAR(dx, -40.0f, 1e-3f) && NEAR(dy, 40.0f, 1e-3f) && dz == 0.0f);

    /* the tangent turns smoothly round a lap: no flips between samples (pi/1000 apart) */
    lap_rates(&g, &r, &jump);
    CHECK(jump < 2.0f);
    printf("gerono a70 flat: max heading rate %.1f deg per path rad (laps 2 in 18 s: x0.698 rad/s = %.0f deg/s)\n",
           r, r * 0.698f);
    lap_rates(&gt, &r, &jump);
    CHECK(jump < 2.0f);
    printf("gerono a70 tilt 30: max heading rate %.1f deg per path rad (x0.698 = %.0f deg/s)\n", r, r * 0.698f);
    lap_rates(&b, &r, &jump);
    CHECK(jump < 2.0f);
    printf("bernoulli a70: max heading rate %.1f deg per path rad\n", r);

    /* wrap and slew: short way through +-180, clamped, settles exactly */
    CHECK(TrajShape_WrapDeg(540.0f) == -180.0f);
    CHECK(NEAR(TrajShape_WrapDeg(-190.0f), 170.0f, 1e-4f));
    CHECK(TrajShape_WrapDeg(INFINITY) != TrajShape_WrapDeg(INFINITY));   /* NaN, returned, not a hang */
    CHECK(NEAR(TrajShape_YawStep(170.0f, -170.0f, 5.0f), 175.0f, 1e-4f));
    cmd = 170.0f;
    for (i = 0; i < 4; i++) {
        cmd = TrajShape_YawStep(cmd, -170.0f, 5.0f);
    }
    CHECK(NEAR(cmd, -170.0f, 1e-3f));
    CHECK(NEAR(TrajShape_YawStep(0.0f, 90.0f, 0.45f), 0.45f, 1e-6f));
    CHECK(NEAR(TrajShape_YawStep(10.0f, 10.2f, 0.45f), 10.2f, 1e-6f));

    /* --- n26-B: 1. Slow clock continuity, rate limit, hold at s=0, and FF scaling --- */
    {
        float s = 1.0f, ds_dt = 0.0f, dt = 0.01f, t_ref = 5.0f;
        float vff_x = 0.0f, vff_y = 0.0f, aff_x = 0.0f, aff_y = 0.0f;
        int k;
        /* Below e_lo (15 cm): s = 1 */
        s = TrajShape_SlowClockStep(s, 10.0f, 15.0f, 45.0f, 1.0f, dt, &ds_dt);
        CHECK(NEAR(s, 1.0f, 1e-6f) && NEAR(ds_dt, 0.0f, 1e-5f));
        /* Midpoint (30 cm): smoothstep u=0.5 -> s = 0.5 */
        s = TrajShape_SlowClockStep(1.0f, 30.0f, 15.0f, 45.0f, 1.0f, dt, &ds_dt);
        CHECK(NEAR(s, 0.5f, 1e-5f));
        /* At/above e_hi (45 cm): s = 0 and reference holds */
        s = TrajShape_SlowClockStep(s, 50.0f, 15.0f, 45.0f, 1.0f, dt, &ds_dt);
        CHECK(s == 0.0f);
        for (k = 0; k < 20; k++) {
            s = TrajShape_SlowClockStep(s, 60.0f, 15.0f, 45.0f, 1.0f, dt, &ds_dt);
            t_ref += s * dt;
        }
        CHECK(s == 0.0f && t_ref == 5.0f);   /* reference holds where it is at s = 0 */
        TrajShape_ScaleFF(s, ds_dt, 40.0f, -25.0f, 120.0f, -80.0f, &vff_x, &vff_y, &aff_x, &aff_y);
        CHECK(vff_x == 0.0f && vff_y == 0.0f && aff_x == 0.0f && aff_y == 0.0f);
        /* Recovery when error drops to 0: rate-limited at 1.0 /s (0.01 per tick), continuous */
        for (k = 0; k < 50; k++) {
            float s_prev = s;
            s = TrajShape_SlowClockStep(s, 0.0f, 15.0f, 45.0f, 1.0f, dt, &ds_dt);
            CHECK(s >= s_prev && (s - s_prev) <= 1.0f * dt + 1e-6f);
            CHECK(NEAR(ds_dt, 1.0f, 1e-4f));
        }
        CHECK(NEAR(s, 0.5f, 1e-4f));
        TrajShape_ScaleFF(0.5f, 1.0f, 40.0f, -20.0f, 100.0f, -60.0f, &vff_x, &vff_y, &aff_x, &aff_y);
        CHECK(NEAR(vff_x, 20.0f, 1e-4f) && NEAR(vff_y, -10.0f, 1e-4f));
        CHECK(NEAR(aff_x, 0.25f * 100.0f + 1.0f * 40.0f, 1e-4f));
        CHECK(NEAR(aff_y, 0.25f * (-60.0f) + 1.0f * (-20.0f), 1e-4f));
    }

    /* --- n26-B: 2. Minimum-jerk quintic boundary conditions within 1e-4 --- */
    {
        float p0[3] = { -15.0f, 25.0f, 80.0f };
        float v0[3] = { 12.0f, -8.0f, 3.0f };
        float a0[3] = { -30.0f, 15.0f, -5.0f };
        float p1[3] = { 45.0f, -35.0f, 95.0f };
        float v1[3] = { -5.0f, 20.0f, 0.0f };
        float a1[3] = { 10.0f, -25.0f, 4.0f };
        float pos[3], vel[3], acc[3];
        int k;
        TrajShape_QuinticSegment3D(0.0f, 2.5f, p0, v0, a0, p1, v1, a1, pos, vel, acc);
        for (k = 0; k < 3; k++) {
            CHECK(NEAR(pos[k], p0[k], 1e-4f));
            CHECK(NEAR(vel[k], v0[k], 1e-4f));
            CHECK(NEAR(acc[k], a0[k], 1e-4f));
        }
        TrajShape_QuinticSegment3D(2.5f, 2.5f, p0, v0, a0, p1, v1, a1, pos, vel, acc);
        for (k = 0; k < 3; k++) {
            CHECK(NEAR(pos[k], p1[k], 1e-4f));
            CHECK(NEAR(vel[k], v1[k], 1e-4f));
            CHECK(NEAR(acc[k], a1[k], 1e-4f));
        }
        /* Analytic figure-8 derivatives match central differences */
        {
            float th = 0.7f, dth = 0.65f, ddth = -0.2f, eps = 1e-3f;
            float p_c[3], v_c[3], a_c[3], p_m[3], p_p[3], v_m[3], v_p[3];
            TrajShape_Derivs(&g, th, dth, ddth, p_c, v_c, a_c);
            TrajShape_Derivs(&g, th - eps * dth, dth - eps * ddth, ddth, p_m, v_m, NULL);
            TrajShape_Derivs(&g, th + eps * dth, dth + eps * ddth, ddth, p_p, v_p, NULL);
            CHECK(NEAR(v_c[0], (p_p[0] - p_m[0]) / (2.0f * eps), 5e-2f));
            CHECK(NEAR(v_c[1], (p_p[1] - p_m[1]) / (2.0f * eps), 5e-2f));
            CHECK(NEAR(a_c[0], (v_p[0] - v_m[0]) / (2.0f * eps), 1e-1f));
            CHECK(NEAR(a_c[1], (v_p[1] - v_m[1]) / (2.0f * eps), 1e-1f));
        }
    }

    /* --- n26-B: 3. Waypoint arrival events fire once per waypoint --- */
    {
        static const float sq_wps[4][4] = {
            { 40.0f,  0.0f, 0.0f, 2.0f },
            { 40.0f, 40.0f, 0.0f, 4.0f },
            {  0.0f, 40.0f, 0.0f, 6.0f },
            {  0.0f,  0.0f, 0.0f, 8.0f }
        };
        traj_wp_tracker_t trk;
        int fired_counts[4] = { 0, 0, 0, 0 };
        int step;
        TrajWp_Init(&trk, 10.0f, 4U, sq_wps);
        for (step = 1; step <= 900; step++) {
            float t = 0.01f * (float)step;
            /* Simulated drone lags planned square by 0.4 s */
            float t_lag = (t > 0.4f) ? (t - 0.4f) : 0.0f;
            float px = 0.0f, py = 0.0f;
            uint8_t idx = 99U;
            if (t_lag < 2.0f) { px = 20.0f * t_lag; py = 0.0f; }
            else if (t_lag < 4.0f) { px = 40.0f; py = 20.0f * (t_lag - 2.0f); }
            else if (t_lag < 6.0f) { px = 40.0f - 20.0f * (t_lag - 4.0f); py = 40.0f; }
            else if (t_lag < 8.0f) { px = 0.0f; py = 40.0f - 20.0f * (t_lag - 6.0f); }
            if (TrajWp_Tick(&trk, 0.01f, 0.9f, px, py, px + 5.0f, py, &idx) != 0U) {
                CHECK(idx < 4U);
                if (idx < 4U) fired_counts[idx]++;
            }
        }
        for (i = 0; i < 4; i++) {
            CHECK(fired_counts[i] == 1);
            CHECK(trk.entries[i].arrived == 1U);
            CHECK(trk.entries[i].t_actual_s > trk.entries[i].t_plan_s - 0.6f);
            CHECK(NEAR(trk.entries[i].peak_err_cm, 5.0f, 1e-3f));
            CHECK(trk.entries[i].time_lost_s > 0.0f);
        }
    }

    if (s_fail_count == 0) {
        printf("test_traj_shape: all passed (%d checks)\n", s_check_count);
        return 0;
    }
    printf("test_traj_shape: %d of %d failed\n", s_fail_count, s_check_count);
    return 1;
}
