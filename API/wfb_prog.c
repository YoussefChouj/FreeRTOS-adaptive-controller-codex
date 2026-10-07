#include "wfb_prog.h"
#include <stdint.h>
#include <stddef.h>
#include <math.h>
#include <string.h>

/* Program caps (docs/workflow-c/onboard-preset-program.md). COMMIT rejects any segment above them.
   @a_max         m/s^2  [0.1, 5]    peak accel of LINE/ARC/LISSA (ramp plus centripetal)
   @j_max         m/s^3  [0.5, 50]   SCURVE jerk
   @yaw_rate_max  deg/s  [10, 360]   TURN cruise, LINE yaw rate, ARC yaw_mode 1
   @yaw_acc_max   deg/s^2 [10, 720]  TURN ramp accel
   @r_min         m      [0.02, 1]   ARC radius
   @t_max         s      [10, 600]   whole program (the 120 s airborne cap still applies on top)
   @v_jump_max    m/s    [0, 0.5]    velocity step allowed where two segments meet and at both program ends */
#define WFB_PROG_CAPS_ROW(a_max, j_max, yaw_rate_max, yaw_acc_max, r_min, t_max, v_jump_max) \
    { (a_max), (j_max), (yaw_rate_max), (yaw_acc_max), (r_min), (t_max), (v_jump_max) }

static const wfb_prog_caps_t s_default_caps =
/*                   a_max  j_max  yaw_rate  yaw_acc  r_min  t_max   v_jump */
    WFB_PROG_CAPS_ROW(1.5f,  5.0f,  90.0f,    180.0f,  0.05f, 120.0f, 0.05f); /* PROPOSED */

#define WFB_PROG_PI        3.14159265f
#define WFB_PROG_TWO_PI    6.28318531f
#define WFB_PROG_HALF_PI   1.57079633f
#define WFB_PROG_DEG2RAD   0.0174532925f
#define WFB_PROG_VAL_ABS   1.0e6f   /* any staged field */
#define WFB_PROG_LEN_EPS   0.001f   /* m (deg for TURN): shorter = zero-length segment */
#define WFB_PROG_SWEEP_MAX 3600.0f  /* deg, ARC */
#define WFB_PROG_CYC_MAX   100.0f   /* LISSA cycles */
#define WFB_PROG_N_MAX     10.0f    /* LISSA n_i */
#define WFB_PROG_PLAN_ITER 30

void wfb_prog_default_caps(wfb_prog_caps_t *out)
{
    if (out != NULL) {
        *out = s_default_caps;
    }
}

/* Wraps any finite angle into [-180, 180) deg. */
static float wfb_prog_wrap180(float a)
{
    a = fmodf(a + 180.0f, 360.0f);
    if (a < 0.0f) {
        a += 360.0f;
    }
    return a - 180.0f;
}

static int wfb_prog_is_int(float v)
{
    return !(isnan(v) || isinf(v)) && floorf(v) == v;
}

/* CRC-32 update without the init/final xor, so it runs one segment at a time (same polynomial as wfb_crc32). */
static uint32_t wfb_prog_crc_update(uint32_t crc, const uint8_t *data, uint32_t len)
{
    uint32_t i;
    int j;

    for (i = 0u; i < len; i++) {
        crc ^= (uint32_t)data[i];
        for (j = 0; j < 8; j++) {
            if (crc & 1u) {
                crc = (crc >> 1) ^ 0xEDB88320u;
            } else {
                crc >>= 1;
            }
        }
    }
    return crc;
}

/* ---------- velocity profiles ---------- */

/* Integral of the unit ramp f(tau) (0 -> 1 over tau 0 -> 1, f(tau) + f(1 - tau) = 1, so F(1) = 1/2).
   rho = SCURVE jerk fraction of the ramp (0 = constant accel). */
float wfb_prog_ramp_F(uint8_t prof, float rho, float tau)
{
    float q, c;

    if (!(tau > 0.0f)) {
        return 0.0f;
    }
    if (tau >= 1.0f) {
        return 0.5f;
    }
    switch (prof) {
    case WFB_PROF_SINE:
        return 0.5f * tau - sinf(WFB_PROG_PI * tau) / WFB_PROG_TWO_PI;
    case WFB_PROF_QUINTIC:
        q = tau * tau;
        return q * q * (2.5f - 3.0f * tau + q);
    case WFB_PROF_SCURVE:
        if (rho > 0.0f && rho <= 0.5f) {
            if (tau > 1.0f - rho) {
                return tau - 0.5f + wfb_prog_ramp_F(prof, rho, 1.0f - tau);
            }
            if (tau <= rho) {
                return tau * tau * tau / (6.0f * rho * (1.0f - rho));
            }
            c = tau - 0.5f * rho;
            return rho * rho / (6.0f * (1.0f - rho)) + (c * c - 0.25f * rho * rho) / (2.0f * (1.0f - rho));
        }
        return 0.5f * tau * tau;
    default: /* TRAP */
        return 0.5f * tau * tau;
    }
}

/* Time to change speed by dv >= 0 with peak accel a (and jerk j for SCURVE). */
static float wfb_prog_ramp_time(uint8_t prof, float dv, float a, float j, float *rho)
{
    float ta;

    *rho = 0.0f;
    if (!(dv > 0.0f)) {
        return 0.0f;
    }
    switch (prof) {
    case WFB_PROF_SINE:
        return WFB_PROG_HALF_PI * dv / a;
    case WFB_PROF_QUINTIC:
        return 1.875f * dv / a;
    case WFB_PROF_SCURVE:
        if (dv >= a * a / j) {
            ta = dv / a + a / j;
            *rho = (a / j) / ta;
        } else {
            ta = 2.0f * sqrtf(dv / j);
            *rho = 0.5f;
        }
        return ta;
    default:
        return dv / a;
    }
}

/* Distance of both ramps at cruise speed vc. */
static float wfb_prog_ramp_dist(uint8_t prof, float v0, float vc, float v1, float a, float j)
{
    float r;
    return 0.5f * (v0 + vc) * wfb_prog_ramp_time(prof, vc - v0, a, j, &r) +
           0.5f * (vc + v1) * wfb_prog_ramp_time(prof, vc - v1, a, j, &r);
}

/* ramp(v_in -> vc), cruise, ramp(vc -> v_out) over len; lowers vc until the ramps fit. */
static wfb_err_t wfb_prog_plan(wfb_prog_seg_t *sg)
{
    uint8_t prof = (uint8_t)sg->f[WFB_PROG_F_PROFILE];
    float v = sg->f[WFB_PROG_F_V], a = sg->f[WFB_PROG_F_A], j = sg->f[WFB_PROG_F_J];
    float v0 = sg->f[WFB_PROG_F_V_IN], v1 = sg->f[WFB_PROG_F_V_OUT];
    float lo, hi, mid, lc;
    int it;

    lo = (v0 > v1) ? v0 : v1;
    hi = v;
    if (wfb_prog_ramp_dist(prof, v0, lo, v1, a, j) > sg->len + 1.0e-4f) {
        return WFB_ERR_RANGE;
    }
    if (wfb_prog_ramp_dist(prof, v0, hi, v1, a, j) <= sg->len) {
        lo = hi;
    } else {
        for (it = 0; it < WFB_PROG_PLAN_ITER; it++) {
            mid = 0.5f * (lo + hi);
            if (wfb_prog_ramp_dist(prof, v0, mid, v1, a, j) <= sg->len) {
                lo = mid;
            } else {
                hi = mid;
            }
        }
    }
    sg->vc = lo;
    sg->ta0 = wfb_prog_ramp_time(prof, lo - v0, a, j, &sg->rho0);
    sg->ta1 = wfb_prog_ramp_time(prof, lo - v1, a, j, &sg->rho1);
    sg->d0 = 0.5f * (v0 + lo) * sg->ta0;
    lc = sg->len - sg->d0 - 0.5f * (lo + v1) * sg->ta1;
    if (lc < 0.0f) {
        lc = 0.0f;
    }
    sg->tc = (lo > 0.0f) ? lc / lo : 0.0f;
    sg->dur = sg->ta0 + sg->tc + sg->ta1;
    return WFB_ERR_NONE;
}

/* Path variable s at local time t (0..dur). */
static float wfb_prog_seg_s(const wfb_prog_seg_t *sg, float t)
{
    uint8_t prof = (uint8_t)sg->f[WFB_PROG_F_PROFILE];
    float v0 = sg->f[WFB_PROG_F_V_IN], v1 = sg->f[WFB_PROG_F_V_OUT];
    float s;

    if (!(t > 0.0f)) {
        return 0.0f;
    }
    if (t >= sg->dur) {
        return sg->len;
    }
    if (t < sg->ta0) {
        s = v0 * t + (sg->vc - v0) * sg->ta0 * wfb_prog_ramp_F(prof, sg->rho0, t / sg->ta0);
    } else if (t < sg->ta0 + sg->tc) {
        s = sg->d0 + sg->vc * (t - sg->ta0);
    } else {
        t -= sg->ta0 + sg->tc;
        s = sg->d0 + sg->vc * sg->tc + sg->vc * t +
            (v1 - sg->vc) * sg->ta1 * wfb_prog_ramp_F(prof, sg->rho1, t / sg->ta1);
    }
    return (s < sg->len) ? s : sg->len;
}

/* ---------- geometry atoms ---------- */

/* Pose (relative yaw, not wrapped) at path variable s. */
static void wfb_prog_seg_pose(const wfb_prog_seg_t *sg, float s, wfb_traj_point_t *p)
{
    const float *P = &sg->f[WFB_PROG_F_P0];
    float u, th, ph;
    int i;
    float *axis[3];

    p->x_m = sg->x0;
    p->y_m = sg->y0;
    p->z_m = sg->z0;
    p->yaw_deg = sg->yaw0;
    u = (sg->len > 0.0f) ? s / sg->len : 1.0f;
    switch ((uint8_t)sg->f[WFB_PROG_F_ATOM]) {
    case WFB_ATOM_LINE:
        p->x_m = sg->x0 + (P[0] - sg->x0) * u;
        p->y_m = sg->y0 + (P[1] - sg->y0) * u;
        p->z_m = sg->z0 + (P[2] - sg->z0) * u;
        p->yaw_deg = sg->yaw0 + sg->dyaw * u;
        break;
    case WFB_ATOM_TURN:
        p->yaw_deg = sg->yaw0 + sg->dyaw * u;
        break;
    case WFB_ATOM_ARC:
        th = sg->th0 + P[2] * WFB_PROG_DEG2RAD * u;
        p->x_m = P[0] + sg->r * cosf(th);
        p->y_m = P[1] + sg->r * sinf(th);
        p->z_m = sg->z0 + P[3] * u;
        if (P[4] != 0.0f) {
            p->yaw_deg = sg->yaw0 + P[2] * u;
        }
        break;
    case WFB_ATOM_LISSA:
        axis[0] = &p->x_m;
        axis[1] = &p->y_m;
        axis[2] = &p->z_m;
        for (i = 0; i < 3; i++) {
            ph = P[6 + i] * WFB_PROG_DEG2RAD;
            *axis[i] += P[i] * (sinf(WFB_PROG_TWO_PI * P[3 + i] * s + ph) - sinf(ph));
        }
        break;
    default: /* HOLD */
        break;
    }
}

/* Velocity vector at path variable s for path speed ds/dt = sd. */
static void wfb_prog_seg_vel(const wfb_prog_seg_t *sg, float s, float sd, float v[3])
{
    const float *P = &sg->f[WFB_PROG_F_P0];
    float th, k;
    int i;

    v[0] = 0.0f;
    v[1] = 0.0f;
    v[2] = 0.0f;
    if (!(sg->len > 0.0f)) {
        return;
    }
    switch ((uint8_t)sg->f[WFB_PROG_F_ATOM]) {
    case WFB_ATOM_LINE:
        v[0] = (P[0] - sg->x0) / sg->len * sd;
        v[1] = (P[1] - sg->y0) / sg->len * sd;
        v[2] = (P[2] - sg->z0) / sg->len * sd;
        break;
    case WFB_ATOM_ARC:
        th = sg->th0 + P[2] * WFB_PROG_DEG2RAD * s / sg->len;
        k = P[2] * WFB_PROG_DEG2RAD * sg->r / sg->len * sd; /* signed tangential speed */
        v[0] = -k * sinf(th);
        v[1] = k * cosf(th);
        v[2] = P[3] / sg->len * sd;
        break;
    case WFB_ATOM_LISSA:
        for (i = 0; i < 3; i++) {
            v[i] = WFB_PROG_TWO_PI * P[3 + i] * P[i] * sd *
                   cosf(WFB_PROG_TWO_PI * P[3 + i] * s + P[6 + i] * WFB_PROG_DEG2RAD);
        }
        break;
    default: /* HOLD, TURN */
        break;
    }
}

static int wfb_prog_in_box(const wfb_traj_limits_t *lim, float x, float y, float z)
{
    return (fabsf(x) <= lim->x_abs_m) && (fabsf(y) <= lim->y_abs_m) && (z >= lim->z_min_m) && (z <= lim->z_max_m);
}

/* Shared motion checks for the profiled atoms: v > 0, a > 0, j > 0 (SCURVE), 0 <= v_in, v_out <= v. */
static int wfb_prog_motion_ok(const wfb_prog_seg_t *sg)
{
    float v = sg->f[WFB_PROG_F_V];
    return (v > 0.0f) && (sg->f[WFB_PROG_F_A] > 0.0f) &&
           ((uint8_t)sg->f[WFB_PROG_F_PROFILE] != WFB_PROF_SCURVE || sg->f[WFB_PROG_F_J] > 0.0f) &&
           (sg->f[WFB_PROG_F_V_IN] >= 0.0f) && (sg->f[WFB_PROG_F_V_IN] <= v) &&
           (sg->f[WFB_PROG_F_V_OUT] >= 0.0f) && (sg->f[WFB_PROG_F_V_OUT] <= v);
}

/* Geometry and limits of one segment whose start pose is set; fills len/dyaw/r/th0, plans dur. */
static wfb_err_t wfb_prog_derive_seg(wfb_prog_seg_t *sg, const wfb_traj_limits_t *lim, const wfb_prog_caps_t *caps)
{
    const float *P = &sg->f[WFB_PROG_F_P0];
    float v = sg->f[WFB_PROG_F_V], a = sg->f[WFB_PROG_F_A], j = sg->f[WFB_PROG_F_J];
    uint8_t scurve = ((uint8_t)sg->f[WFB_PROG_F_PROFILE] == WFB_PROF_SCURVE);
    float dx, dy, dz, sw, d, ang, sv, sa, z1;
    int k;

    sg->len = 0.0f;
    sg->dyaw = 0.0f;
    sg->r = 0.0f;
    sg->th0 = 0.0f;
    sg->vc = 0.0f;
    sg->ta0 = sg->tc = sg->ta1 = 0.0f;
    sg->rho0 = sg->rho1 = 0.0f;
    sg->d0 = 0.0f;
    sg->dur = 0.0f;

    switch ((uint8_t)sg->f[WFB_PROG_F_ATOM]) {
    case WFB_ATOM_HOLD:
        if (!(P[0] >= 0.0f) || !(P[0] <= caps->t_max)) {
            return WFB_ERR_TIME;
        }
        sg->dur = P[0];
        return WFB_ERR_NONE;

    case WFB_ATOM_LINE:
        if (!(fabsf(P[3]) <= 180.0f)) {
            return WFB_ERR_RANGE;
        }
        if (!wfb_prog_in_box(lim, P[0], P[1], P[2])) {
            return WFB_ERR_BOUNDS;
        }
        dx = P[0] - sg->x0;
        dy = P[1] - sg->y0;
        dz = P[2] - sg->z0;
        sg->len = sqrtf(dx * dx + dy * dy + dz * dz);
        sg->dyaw = wfb_prog_wrap180(P[3] - sg->yaw0);
        if (sg->len < WFB_PROG_LEN_EPS) {
            /* zero-length: a snap, only for a negligible yaw change at rest */
            if (fabsf(sg->dyaw) > 0.5f || sg->f[WFB_PROG_F_V_IN] != 0.0f || sg->f[WFB_PROG_F_V_OUT] != 0.0f) {
                return WFB_ERR_RANGE;
            }
            sg->len = 0.0f;
            return WFB_ERR_NONE;
        }
        if (!wfb_prog_motion_ok(sg)) {
            return WFB_ERR_RANGE;
        }
        if (!(v <= lim->v_max_mps) || !(a <= caps->a_max) || (scurve && !(j <= caps->j_max))) {
            return WFB_ERR_SPEED;
        }
        if (!(fabsf(sg->dyaw) / sg->len * v <= caps->yaw_rate_max)) {
            return WFB_ERR_SPEED;
        }
        return wfb_prog_plan(sg);

    case WFB_ATOM_TURN:
        if (!(fabsf(P[0]) <= 180.0f)) {
            return WFB_ERR_RANGE;
        }
        sg->dyaw = wfb_prog_wrap180(P[0] - sg->yaw0);
        sg->len = fabsf(sg->dyaw);
        if (sg->len < WFB_PROG_LEN_EPS) {
            sg->len = 0.0f;
            sg->dyaw = 0.0f;
            return (sg->f[WFB_PROG_F_V_IN] == 0.0f && sg->f[WFB_PROG_F_V_OUT] == 0.0f) ? WFB_ERR_NONE : WFB_ERR_RANGE;
        }
        if (!wfb_prog_motion_ok(sg)) {
            return WFB_ERR_RANGE;
        }
        if (!(v <= caps->yaw_rate_max) || !(a <= caps->yaw_acc_max)) {
            return WFB_ERR_SPEED;
        }
        return wfb_prog_plan(sg);

    case WFB_ATOM_ARC:
        sw = P[2];
        if (!(fabsf(sw) > 0.0f) || !(fabsf(sw) <= WFB_PROG_SWEEP_MAX) || (P[4] != 0.0f && P[4] != 1.0f)) {
            return WFB_ERR_RANGE;
        }
        dx = sg->x0 - P[0];
        dy = sg->y0 - P[1];
        sg->r = sqrtf(dx * dx + dy * dy);
        if (!(sg->r >= caps->r_min)) {
            return WFB_ERR_RANGE;
        }
        sg->th0 = atan2f(dy, dx);
        d = fabsf(sw) * WFB_PROG_DEG2RAD * sg->r;
        sg->len = sqrtf(d * d + P[3] * P[3]);
        /* bounds: both ends plus every axis extreme the sweep passes */
        z1 = sg->z0 + P[3];
        if (!wfb_prog_in_box(lim, sg->x0, sg->y0, z1)) {
            return WFB_ERR_BOUNDS;
        }
        for (k = 0; k < 4; k++) {
            ang = (float)k * WFB_PROG_HALF_PI;
            d = (sw > 0.0f) ? (ang - sg->th0) : (sg->th0 - ang);
            d = fmodf(d, WFB_PROG_TWO_PI);
            if (d < 0.0f) {
                d += WFB_PROG_TWO_PI;
            }
            if (d <= fabsf(sw) * WFB_PROG_DEG2RAD &&
                !wfb_prog_in_box(lim, P[0] + sg->r * cosf(ang), P[1] + sg->r * sinf(ang), sg->z0)) {
                return WFB_ERR_BOUNDS;
            }
        }
        if (!wfb_prog_motion_ok(sg)) {
            return WFB_ERR_RANGE;
        }
        if (!(v <= lim->v_max_mps) || (scurve && !(j <= caps->j_max)) ||
            !(a * a + (v * v / sg->r) * (v * v / sg->r) <= caps->a_max * caps->a_max)) {
            return WFB_ERR_SPEED;
        }
        if (P[4] != 0.0f && !(fabsf(sw) / sg->len * v <= caps->yaw_rate_max)) {
            return WFB_ERR_SPEED;
        }
        return wfb_prog_plan(sg);

    case WFB_ATOM_LISSA:
        if (!(P[9] > 0.0f) || !(P[9] <= WFB_PROG_CYC_MAX)) {
            return WFB_ERR_RANGE;
        }
        sv = 0.0f;
        sa = 0.0f;
        for (k = 0; k < 3; k++) {
            if (!(P[3 + k] >= 0.0f) || !(P[3 + k] <= WFB_PROG_N_MAX) || !(fabsf(P[6 + k]) <= 360.0f)) {
                return WFB_ERR_RANGE;
            }
            sv += (P[k] * P[3 + k]) * (P[k] * P[3 + k]);
            sa += (P[k] * P[3 + k] * P[3 + k]) * (P[k] * P[3 + k] * P[3 + k]);
        }
        /* bounds: each axis swings centre +- abs(a) */
        dx = sg->x0 - P[0] * sinf(P[6] * WFB_PROG_DEG2RAD);
        dy = sg->y0 - P[1] * sinf(P[7] * WFB_PROG_DEG2RAD);
        dz = sg->z0 - P[2] * sinf(P[8] * WFB_PROG_DEG2RAD);
        if (!wfb_prog_in_box(lim, fabsf(dx) + fabsf(P[0]), fabsf(dy) + fabsf(P[1]), dz + fabsf(P[2])) ||
            !wfb_prog_in_box(lim, 0.0f, 0.0f, dz - fabsf(P[2]))) {
            return WFB_ERR_BOUNDS;
        }
        sg->len = P[9];
        if (!wfb_prog_motion_ok(sg)) {
            return WFB_ERR_RANGE;
        }
        sv = WFB_PROG_TWO_PI * sqrtf(sv);         /* m per cycle, peak */
        sa = WFB_PROG_TWO_PI * WFB_PROG_TWO_PI * sqrtf(sa);
        if (!(v * sv <= lim->v_max_mps) || !(v * v * sa + a * sv <= caps->a_max) ||
            (scurve && !(j * sv <= caps->j_max))) {
            return WFB_ERR_SPEED;
        }
        return wfb_prog_plan(sg);

    default:
        return WFB_ERR_RANGE;
    }
}

/* Chains the segments from the hover point, checks velocity continuity, the return home and the time cap. */
static wfb_err_t wfb_prog_derive(wfb_prog_t *pr, const wfb_traj_limits_t *lim, const wfb_prog_caps_t *caps,
                                 float hover_z_m)
{
    wfb_traj_point_t p;
    float t = 0.0f, vprev[3] = {0.0f, 0.0f, 0.0f}, vin[3], dv[3];
    uint16_t k;
    wfb_prog_seg_t *sg;
    wfb_err_t err;

    p.x_m = 0.0f;
    p.y_m = 0.0f;
    p.z_m = hover_z_m;
    p.yaw_deg = 0.0f;
    for (k = 0u; k < pr->n; k++) {
        sg = &pr->seg[k];
        pr->err_seg = k;
        sg->t0 = t;
        sg->x0 = p.x_m;
        sg->y0 = p.y_m;
        sg->z0 = p.z_m;
        sg->yaw0 = p.yaw_deg;
        err = wfb_prog_derive_seg(sg, lim, caps);
        if (err != WFB_ERR_NONE) {
            return err;
        }
        wfb_prog_seg_vel(sg, 0.0f, sg->f[WFB_PROG_F_V_IN], vin);
        dv[0] = vin[0] - vprev[0];
        dv[1] = vin[1] - vprev[1];
        dv[2] = vin[2] - vprev[2];
        if (!(sqrtf(dv[0] * dv[0] + dv[1] * dv[1] + dv[2] * dv[2]) <= caps->v_jump_max)) {
            return WFB_ERR_SPEED;
        }
        if ((uint8_t)sg->f[WFB_PROG_F_ATOM] == WFB_ATOM_HOLD) {
            vprev[0] = vprev[1] = vprev[2] = 0.0f;
        } else {
            wfb_prog_seg_vel(sg, sg->len, sg->f[WFB_PROG_F_V_OUT], vprev);
        }
        wfb_prog_seg_pose(sg, sg->len, &p);
        p.yaw_deg = wfb_prog_wrap180(p.yaw_deg);
        t += sg->dur;
    }
    pr->err_seg = pr->n;
    if (!(sqrtf(vprev[0] * vprev[0] + vprev[1] * vprev[1] + vprev[2] * vprev[2]) <= caps->v_jump_max)) {
        return WFB_ERR_SPEED;
    }
    dv[0] = p.x_m;
    dv[1] = p.y_m;
    dv[2] = p.z_m - hover_z_m;
    if (!(sqrtf(dv[0] * dv[0] + dv[1] * dv[1] + dv[2] * dv[2]) <= lim->endpoint_tol_m)) {
        return WFB_ERR_ENDPOINT;
    }
    if (!(t <= caps->t_max)) {
        return WFB_ERR_TIME;
    }
    pr->t_total = t;
    pr->err_seg = 0xFFFFu;
    return WFB_ERR_NONE;
}

/* ---------- lifecycle ---------- */

void wfb_prog_init(wfb_prog_t *pr, wfb_prog_seg_t *buf, uint16_t cap)
{
    if (pr == NULL) {
        return;
    }
    memset(pr, 0, sizeof(*pr));
    pr->seg = buf;
    pr->cap = (buf != NULL) ? cap : 0u;
    pr->err_seg = 0xFFFFu;
    pr->state = (uint8_t)WFB_TRAJ_EMPTY;
}

static wfb_err_t wfb_prog_fail(wfb_prog_t *pr, wfb_err_t err)
{
    pr->state = (uint8_t)WFB_TRAJ_EMPTY;
    return err;
}

wfb_err_t wfb_prog_on_cmd(wfb_prog_t *pr, uint8_t idx, float val,
                          const wfb_traj_limits_t *lim, const wfb_prog_caps_t *caps, float hover_z_m)
{
    uint32_t want;

    if (pr == NULL || pr->seg == NULL) {
        return WFB_ERR_STATE;
    }
    if (idx < (uint8_t)WFB_PROG_F_COUNT) {
        if (pr->state != (uint8_t)WFB_TRAJ_LOADING) {
            return WFB_ERR_STATE;
        }
        if (isnan(val) || isinf(val) || !(fabsf(val) <= WFB_PROG_VAL_ABS)) {
            return WFB_ERR_RANGE;
        }
        pr->stage[idx] = val;
        return WFB_ERR_NONE;
    }

    switch (idx) {
    case WFB_PROG_IDX_BEGIN:
        if (pr->state == (uint8_t)WFB_TRAJ_EXECUTING) {
            return WFB_ERR_STATE;
        }
        if (!wfb_prog_is_int(val) || val < 1.0f || val > (float)pr->cap) {
            return WFB_ERR_COUNT;
        }
        pr->n = (uint16_t)val;
        pr->rx = 0u;
        pr->cur = 0u;
        pr->crc_hi = 0u;
        pr->crc_hi_set = 0u;
        pr->crc_calc = 0xFFFFFFFFu;
        pr->t_total = 0.0f;
        pr->err_seg = 0xFFFFu;
        memset(pr->stage, 0, sizeof(pr->stage));
        pr->state = (uint8_t)WFB_TRAJ_LOADING;
        return WFB_ERR_NONE;

    case WFB_PROG_IDX_PUSH:
        if (pr->state != (uint8_t)WFB_TRAJ_LOADING) {
            return WFB_ERR_STATE;
        }
        if (!wfb_prog_is_int(val) || val != (float)pr->rx || pr->rx >= pr->n) {
            return WFB_ERR_COUNT;
        }
        if (!wfb_prog_is_int(pr->stage[WFB_PROG_F_ATOM]) || pr->stage[WFB_PROG_F_ATOM] < 0.0f ||
            pr->stage[WFB_PROG_F_ATOM] >= (float)WFB_ATOM_COUNT ||
            !wfb_prog_is_int(pr->stage[WFB_PROG_F_PROFILE]) || pr->stage[WFB_PROG_F_PROFILE] < 0.0f ||
            pr->stage[WFB_PROG_F_PROFILE] >= (float)WFB_PROF_COUNT) {
            return WFB_ERR_RANGE;
        }
        memcpy(pr->seg[pr->rx].f, pr->stage, sizeof(pr->stage));
        pr->crc_calc = wfb_prog_crc_update(pr->crc_calc, (const uint8_t *)pr->stage, (uint32_t)sizeof(pr->stage));
        pr->rx++;
        return WFB_ERR_NONE;

    case WFB_PROG_IDX_CRC_HI:
        if (pr->state != (uint8_t)WFB_TRAJ_LOADING) {
            return WFB_ERR_STATE;
        }
        if (!wfb_prog_is_int(val) || val < 0.0f || val > 65535.0f) {
            return WFB_ERR_RANGE;
        }
        pr->crc_hi = (uint16_t)val;
        pr->crc_hi_set = 1u;
        return WFB_ERR_NONE;

    case WFB_PROG_IDX_COMMIT:
        if (pr->state != (uint8_t)WFB_TRAJ_LOADING) {
            return WFB_ERR_STATE;
        }
        if (!pr->crc_hi_set) {
            return wfb_prog_fail(pr, WFB_ERR_STATE);
        }
        if (pr->rx != pr->n) {
            return wfb_prog_fail(pr, WFB_ERR_COUNT);
        }
        if (!wfb_prog_is_int(val) || val < 0.0f || val > 65535.0f || lim == NULL || caps == NULL) {
            return wfb_prog_fail(pr, WFB_ERR_RANGE);
        }
        want = ((uint32_t)pr->crc_hi << 16) | (uint32_t)val;
        if ((pr->crc_calc ^ 0xFFFFFFFFu) != want) {
            return wfb_prog_fail(pr, WFB_ERR_CRC);
        }
        {
            wfb_err_t err = wfb_prog_derive(pr, lim, caps, hover_z_m);
            if (err != WFB_ERR_NONE) {
                return wfb_prog_fail(pr, err);
            }
        }
        pr->cur = 0u;
        pr->state = (uint8_t)WFB_TRAJ_READY;
        return WFB_ERR_NONE;

    case WFB_PROG_IDX_CLEAR:
        return wfb_prog_clear(pr);

    default:
        return WFB_ERR_RANGE;
    }
}

wfb_err_t wfb_prog_start(wfb_prog_t *pr, float yaw_ref_deg)
{
    if (pr == NULL || pr->state != (uint8_t)WFB_TRAJ_READY) {
        return WFB_ERR_STATE;
    }
    if (isnan(yaw_ref_deg) || isinf(yaw_ref_deg)) {
        return WFB_ERR_RANGE;
    }
    pr->yaw_ref = wfb_prog_wrap180(yaw_ref_deg);
    pr->cur = 0u;
    pr->state = (uint8_t)WFB_TRAJ_EXECUTING;
    return WFB_ERR_NONE;
}

wfb_err_t wfb_prog_stop(wfb_prog_t *pr)
{
    if (pr == NULL || pr->state != (uint8_t)WFB_TRAJ_EXECUTING) {
        return WFB_ERR_STATE;
    }
    pr->state = (uint8_t)WFB_TRAJ_READY;
    return WFB_ERR_NONE;
}

wfb_err_t wfb_prog_clear(wfb_prog_t *pr)
{
    if (pr == NULL || pr->state == (uint8_t)WFB_TRAJ_EXECUTING) {
        return WFB_ERR_STATE;
    }
    pr->n = 0u;
    pr->rx = 0u;
    pr->cur = 0u;
    pr->crc_hi = 0u;
    pr->crc_hi_set = 0u;
    pr->crc_calc = 0u;
    pr->t_total = 0.0f;
    pr->err_seg = 0xFFFFu;
    pr->state = (uint8_t)WFB_TRAJ_EMPTY;
    return WFB_ERR_NONE;
}

static void wfb_prog_out(const wfb_prog_t *pr, const wfb_prog_seg_t *sg, float s, float t, wfb_traj_point_t *out)
{
    wfb_prog_seg_pose(sg, s, out);
    out->yaw_deg = wfb_prog_wrap180(pr->yaw_ref + out->yaw_deg);
    out->t_s = t;
}

int wfb_prog_sample(wfb_prog_t *pr, float t_s, wfb_traj_point_t *out)
{
    const wfb_prog_seg_t *last;
    const wfb_prog_seg_t *sg;

    if (pr == NULL || out == NULL || pr->seg == NULL || pr->n == 0u) {
        return 0;
    }
    last = &pr->seg[pr->n - 1u];
    if (pr->state == (uint8_t)WFB_TRAJ_DONE) {
        wfb_prog_out(pr, last, last->len, pr->t_total, out);
        return 0;
    }
    if (pr->state != (uint8_t)WFB_TRAJ_EXECUTING) {
        return 0;
    }
    if (isnan(t_s) || t_s >= pr->t_total) {
        pr->state = (uint8_t)WFB_TRAJ_DONE;
        wfb_prog_out(pr, last, last->len, pr->t_total, out);
        return 0;
    }
    if (t_s < 0.0f) {
        t_s = 0.0f;
    }
    if (t_s < pr->seg[pr->cur].t0) {
        pr->cur = 0u;
    }
    while ((uint16_t)(pr->cur + 1u) < pr->n && t_s >= pr->seg[pr->cur + 1u].t0) {
        pr->cur++;
    }
    sg = &pr->seg[pr->cur];
    wfb_prog_out(pr, sg, wfb_prog_seg_s(sg, t_s - sg->t0), t_s, out);
    return 1;
}
