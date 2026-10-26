/**
 * @module     traj_shape.c
 * @subsystem  guidance
 * @purpose    Circle / figure-8 shapes for the AutoflyTask presets plus yaw-follow heading and slew.
 *             Pure math, host-tested by tests/firmware_host/test_traj_shape.c.
 */
#include "traj_shape.h"
#include <math.h>
#include <stddef.h>

#define TRAJ_SHAPE_PI   3.14159265f
#define TRAJ_SHAPE_DTH  0.01f   /* central-difference step in path angle [rad] */

void TrajShape_Offset(const traj_shape_t *p, float th, float *dx, float *dy, float *dz)
{
    float s = sinf(th);
    float c = cosf(th);
    float y;

    if (p == NULL || dx == NULL || dy == NULL || dz == NULL) {
        return;
    }
    *dz = 0.0f;
    if (p->id == 3U) {
        /* circle through the start point, centre circ_r_cm toward -x, leaves toward +y */
        *dx = p->circ_r_cm * (c - 1.0f);
        *dy = p->circ_r_cm * s;
        return;
    }
    /* figure-8 from the start point, leaves toward +y */
    if (p->f8_type == 1U) {
        *dx = 0.5f * p->f8_a_cm * sinf(2.0f * th);
        y = p->f8_a_cm * s;
    } else {
        *dx = p->f8_a_cm * (c / (1.0f + s * s) - 1.0f);
        y = p->f8_a_cm * s * c / (1.0f + s * s);
    }
    *dy = y * p->tilt_c;
    *dz = y * p->tilt_s;
}

float TrajShape_TangentDeg(const traj_shape_t *p, float th, float om_sign)
{
    float x0 = 0.0f, y0 = 0.0f, x1 = 0.0f, y1 = 0.0f, z = 0.0f;
    float tx, ty;

    TrajShape_Offset(p, th - TRAJ_SHAPE_DTH, &x0, &y0, &z);
    TrajShape_Offset(p, th + TRAJ_SHAPE_DTH, &x1, &y1, &z);
    tx = x1 - x0;
    ty = y1 - y0;
    if (om_sign < 0.0f) {
        tx = -tx;
        ty = -ty;
    }
    return atan2f(ty, tx) * (180.0f / TRAJ_SHAPE_PI);
}

float TrajShape_WrapDeg(float deg)
{
    deg = fmodf(deg, 360.0f);   /* not a loop: an inf input gives NaN instead of hanging the task */
    if (deg >= 180.0f) {
        deg -= 360.0f;
    } else if (deg < -180.0f) {
        deg += 360.0f;
    }
    return deg;
}

float TrajShape_YawStep(float cmd, float target, float max_step)
{
    float step = TrajShape_WrapDeg(target - cmd);

    if (step > max_step) {
        step = max_step;
    } else if (step < -max_step) {
        step = -max_step;
    }
    return TrajShape_WrapDeg(cmd + step);
}


static float clamp01(float u)
{
    return (u <= 0.0f) ? 0.0f : ((u >= 1.0f) ? 1.0f : u);
}

float TrajShape_SlowClockStep(float s_prev, float err_cm, float e_lo_cm, float e_hi_cm,
                              float recover_rate_per_s, float dt_s, float *ds_dt)
{
    float s_raw;
    float s_new;
    float u;

    s_prev = clamp01(s_prev);
    if (e_hi_cm <= e_lo_cm || err_cm <= e_lo_cm) {
        s_raw = 1.0f;
    } else if (err_cm >= e_hi_cm) {
        s_raw = 0.0f;
    } else {
        u = (err_cm - e_lo_cm) / (e_hi_cm - e_lo_cm);
        /* C2 quintic smoothstep from 1 at u=0 to 0 at u=1 */
        s_raw = 1.0f - u * u * u * (10.0f + u * (-15.0f + 6.0f * u));
        s_raw = clamp01(s_raw);
    }
    if (s_raw <= s_prev) {
        s_new = s_raw;
    } else {
        float max_up = (recover_rate_per_s > 0.0f && dt_s > 0.0f) ? (recover_rate_per_s * dt_s) : 1.0f;
        s_new = s_prev + max_up;
        if (s_new > s_raw) {
            s_new = s_raw;
        }
    }
    s_new = clamp01(s_new);
    if (ds_dt != NULL) {
        *ds_dt = (dt_s > 0.0f) ? ((s_new - s_prev) / dt_s) : 0.0f;
    }
    return s_new;
}

void TrajShape_ScaleFF(float s, float ds_dt,
                       float vx_path, float vy_path, float ax_path, float ay_path,
                       float *vff_x, float *vff_y, float *aff_x, float *aff_y)
{
    if (vff_x != NULL) {
        *vff_x = s * vx_path;
    }
    if (vff_y != NULL) {
        *vff_y = s * vy_path;
    }
    if (aff_x != NULL) {
        *aff_x = s * s * ax_path + ds_dt * vx_path;
    }
    if (aff_y != NULL) {
        *aff_y = s * s * ay_path + ds_dt * vy_path;
    }
}

void TrajShape_Quintic1D(float t_s, float T_s,
                         float p0, float v0, float a0,
                         float p1, float v1, float a1,
                         float *pos, float *vel, float *acc)
{
    float tau, tau2, tau3, tau4, tau5;
    float c0, c1, c2, c3, c4, c5;
    float dp, dv, da;

    if (T_s <= 1e-6f) {
        if (pos != NULL) *pos = p1;
        if (vel != NULL) *vel = v1;
        if (acc != NULL) *acc = a1;
        return;
    }
    tau = clamp01(t_s / T_s);
    tau2 = tau * tau;
    tau3 = tau2 * tau;
    tau4 = tau3 * tau;
    tau5 = tau4 * tau;

    c0 = p0;
    c1 = v0 * T_s;
    c2 = 0.5f * a0 * T_s * T_s;

    dp = p1 - (p0 + v0 * T_s + 0.5f * a0 * T_s * T_s);
    dv = (v1 - v0) * T_s - a0 * T_s * T_s;
    da = (a1 - a0) * T_s * T_s;

    c3 = 10.0f * dp - 4.0f * dv + 0.5f * da;
    c4 = -15.0f * dp + 7.0f * dv - da;
    c5 = 6.0f * dp - 3.0f * dv + 0.5f * da;

    if (pos != NULL) {
        *pos = c0 + c1 * tau + c2 * tau2 + c3 * tau3 + c4 * tau4 + c5 * tau5;
    }
    if (vel != NULL) {
        *vel = (c1 + 2.0f * c2 * tau + 3.0f * c3 * tau2 + 4.0f * c4 * tau3 + 5.0f * c5 * tau4) / T_s;
    }
    if (acc != NULL) {
        *acc = (2.0f * c2 + 6.0f * c3 * tau + 12.0f * c4 * tau2 + 20.0f * c5 * tau3) / (T_s * T_s);
    }
}

void TrajShape_QuinticSegment3D(float t_s, float T_s,
                                const float p0[3], const float v0[3], const float a0[3],
                                const float p1[3], const float v1[3], const float a1[3],
                                float pos[3], float vel[3], float acc[3])
{
    int k;
    for (k = 0; k < 3; k++) {
        TrajShape_Quintic1D(t_s, T_s, p0[k], v0[k], a0[k], p1[k], v1[k], a1[k],
                            pos ? &pos[k] : NULL, vel ? &vel[k] : NULL, acc ? &acc[k] : NULL);
    }
}

void TrajShape_WarpDerivs(uint8_t profile, float t_s, float move_s, float ramp_s,
                          float *w_s, float *dw_dt, float *ddw_dt2)
{
    float T = move_s;
    float R = ramp_s;
    float w = 0.0f, dw = 0.0f, ddw = 0.0f;

    if (profile == 0U || R <= 0.0f) {
        w = t_s;
        dw = 1.0f;
        ddw = 0.0f;
    } else if (t_s <= 0.0f) {
        w = 0.0f;
        dw = 0.0f;
        ddw = 0.0f;
    } else if (t_s >= T) {
        w = T - R;
        dw = 0.0f;
        ddw = 0.0f;
    } else if (profile == 2U) {
        /* Quintic speed ramp: dw(tau) = 10 tau^3 - 15 tau^4 + 6 tau^5, integral w = R * tau^4 (2.5 - 3 tau + tau^2) */
        if (t_s < R) {
            float tau = t_s / R;
            float tau2 = tau * tau;
            float tau3 = tau2 * tau;
            float tau4 = tau3 * tau;
            w = R * tau4 * (2.5f - 3.0f * tau + tau2);
            dw = tau3 * (10.0f - 15.0f * tau + 6.0f * tau2);
            ddw = 30.0f * tau2 * (1.0f - tau) * (1.0f - tau) / R;
        } else if (t_s > T - R) {
            float u = T - t_s;
            float tau = u / R;
            float tau2 = tau * tau;
            float tau3 = tau2 * tau;
            float tau4 = tau3 * tau;
            w = (T - R) - R * tau4 * (2.5f - 3.0f * tau + tau2);
            dw = tau3 * (10.0f - 15.0f * tau + 6.0f * tau2);
            ddw = -30.0f * tau2 * (1.0f - tau) * (1.0f - tau) / R;
        } else {
            w = 0.5f * R + (t_s - R);
            dw = 1.0f;
            ddw = 0.0f;
        }
    } else {
        /* Profile 1: cosine velocity ramp */
        if (t_s < R) {
            w = 0.5f * t_s - R / (2.0f * TRAJ_SHAPE_PI) * sinf(TRAJ_SHAPE_PI * t_s / R);
            dw = 0.5f * (1.0f - cosf(TRAJ_SHAPE_PI * t_s / R));
            ddw = 0.5f * (TRAJ_SHAPE_PI / R) * sinf(TRAJ_SHAPE_PI * t_s / R);
        } else if (t_s > T - R) {
            float u = T - t_s;
            w = (T - R) - (0.5f * u - R / (2.0f * TRAJ_SHAPE_PI) * sinf(TRAJ_SHAPE_PI * u / R));
            dw = 0.5f * (1.0f - cosf(TRAJ_SHAPE_PI * u / R));
            ddw = -0.5f * (TRAJ_SHAPE_PI / R) * sinf(TRAJ_SHAPE_PI * u / R);
        } else {
            w = 0.5f * R + (t_s - R);
            dw = 1.0f;
            ddw = 0.0f;
        }
    }
    if (w_s != NULL) *w_s = w;
    if (dw_dt != NULL) *dw_dt = dw;
    if (ddw_dt2 != NULL) *ddw_dt2 = ddw;
}

void TrajShape_Derivs(const traj_shape_t *p, float th, float dth_dt, float ddth_dt2,
                      float pos_cm[3], float vel_cms[3], float acc_cms2[3])
{
    float s = sinf(th);
    float c = cosf(th);
    float x = 0.0f, dx_dth = 0.0f, d2x_dth2 = 0.0f;
    float y = 0.0f, dy_dth = 0.0f, d2y_dth2 = 0.0f;
    float vx, vy_pl, ax, ay_pl;

    if (p == NULL) {
        return;
    }
    if (p->id == 3U) {
        x = p->circ_r_cm * (c - 1.0f);
        dx_dth = -p->circ_r_cm * s;
        d2x_dth2 = -p->circ_r_cm * c;
        y = p->circ_r_cm * s;
        dy_dth = p->circ_r_cm * c;
        d2y_dth2 = -p->circ_r_cm * s;
        if (pos_cm != NULL) { pos_cm[0] = x; pos_cm[1] = y; pos_cm[2] = 0.0f; }
        if (vel_cms != NULL) {
            vel_cms[0] = dx_dth * dth_dt;
            vel_cms[1] = dy_dth * dth_dt;
            vel_cms[2] = 0.0f;
        }
        if (acc_cms2 != NULL) {
            acc_cms2[0] = d2x_dth2 * dth_dt * dth_dt + dx_dth * ddth_dt2;
            acc_cms2[1] = d2y_dth2 * dth_dt * dth_dt + dy_dth * ddth_dt2;
            acc_cms2[2] = 0.0f;
        }
        return;
    }
    if (p->f8_type == 1U) {
        float s2 = sinf(2.0f * th);
        float c2 = cosf(2.0f * th);
        x = 0.5f * p->f8_a_cm * s2;
        dx_dth = p->f8_a_cm * c2;
        d2x_dth2 = -2.0f * p->f8_a_cm * s2;
        y = p->f8_a_cm * s;
        dy_dth = p->f8_a_cm * c;
        d2y_dth2 = -p->f8_a_cm * s;
    } else {
        float D = 1.0f + s * s;
        float Dp = 2.0f * s * c;
        float Dpp = 2.0f * (c * c - s * s);
        float D2 = D * D;
        float D3 = D2 * D;
        float nx = c, nxp = -s, nxpp = -c;
        float ny = s * c, nyp = c * c - s * s, nypp = -4.0f * s * c;
        x = p->f8_a_cm * (nx / D - 1.0f);
        dx_dth = p->f8_a_cm * (nxp * D - nx * Dp) / D2;
        d2x_dth2 = p->f8_a_cm * ((nxpp * D - nx * Dpp) * D - 2.0f * Dp * (nxp * D - nx * Dp)) / D3;
        y = p->f8_a_cm * (ny / D);
        dy_dth = p->f8_a_cm * (nyp * D - ny * Dp) / D2;
        d2y_dth2 = p->f8_a_cm * ((nypp * D - ny * Dpp) * D - 2.0f * Dp * (nyp * D - ny * Dp)) / D3;
    }
    vx = dx_dth * dth_dt;
    vy_pl = dy_dth * dth_dt;
    ax = d2x_dth2 * dth_dt * dth_dt + dx_dth * ddth_dt2;
    ay_pl = d2y_dth2 * dth_dt * dth_dt + dy_dth * ddth_dt2;
    if (pos_cm != NULL) {
        pos_cm[0] = x;
        pos_cm[1] = y * p->tilt_c;
        pos_cm[2] = y * p->tilt_s;
    }
    if (vel_cms != NULL) {
        vel_cms[0] = vx;
        vel_cms[1] = vy_pl * p->tilt_c;
        vel_cms[2] = vy_pl * p->tilt_s;
    }
    if (acc_cms2 != NULL) {
        acc_cms2[0] = ax;
        acc_cms2[1] = ay_pl * p->tilt_c;
        acc_cms2[2] = ay_pl * p->tilt_s;
    }
}

void TrajWp_Init(traj_wp_tracker_t *trk, float r_cm, uint8_t n_wp,
                 const float (*wx_wy_wz_tplan)[4])
{
    uint8_t i;
    if (trk == NULL) {
        return;
    }
    if (n_wp > TRAJ_WP_MAX) {
        n_wp = TRAJ_WP_MAX;
    }
    trk->count = n_wp;
    trk->active_seg = 0U;
    trk->r_cm = (r_cm > 0.0f) ? r_cm : 15.0f;
    trk->elapsed_s = 0.0f;
    trk->int_s_accum = 0.0f;
    trk->time_lost_accum = 0.0f;
    trk->seg_peak_err_cm = 0.0f;
    for (i = 0U; i < TRAJ_WP_MAX; i++) {
        trk->entries[i].wp_idx = i;
        trk->entries[i].arrived = 0U;
        trk->entries[i].wx_cm = 0.0f;
        trk->entries[i].wy_cm = 0.0f;
        trk->entries[i].wz_cm = 0.0f;
        trk->entries[i].t_plan_s = 0.0f;
        trk->entries[i].t_actual_s = -1.0f;
        trk->entries[i].peak_err_cm = 0.0f;
        trk->entries[i].time_lost_s = 0.0f;
        trk->entries[i].int_s_s = 0.0f;
        if (i < n_wp && wx_wy_wz_tplan != NULL) {
            trk->entries[i].wx_cm = wx_wy_wz_tplan[i][0];
            trk->entries[i].wy_cm = wx_wy_wz_tplan[i][1];
            trk->entries[i].wz_cm = wx_wy_wz_tplan[i][2];
            trk->entries[i].t_plan_s = wx_wy_wz_tplan[i][3];
        }
    }
}

static float invert_warp_time(uint8_t profile, float w_target, float move_s, float ramp_s)
{
    float lo = 0.0f, hi = move_s, mid, w = 0.0f;
    int it;
    if (profile == 0U || ramp_s <= 0.0f) {
        return w_target;
    }
    for (it = 0; it < 20; it++) {
        mid = 0.5f * (lo + hi);
        TrajShape_WarpDerivs(profile, mid, move_s, ramp_s, &w, NULL, NULL);
        if (w < w_target) {
            lo = mid;
        } else {
            hi = mid;
        }
    }
    return 0.5f * (lo + hi);
}

void TrajWp_InitPreset(traj_wp_tracker_t *trk, const traj_shape_t *shape,
                       uint8_t profile, float move_s, float ramp_s, float laps,
                       float step_cm, uint8_t axis, float r_cm)
{
    float wps[TRAJ_WP_MAX][4];
    uint8_t n = 0U;

    if (trk == NULL || shape == NULL) {
        return;
    }
    if (shape->id == 1U) {
        wps[0][0] = (axis == 0U) ? step_cm : 0.0f;
        wps[0][1] = (axis == 1U) ? step_cm : 0.0f;
        wps[0][2] = (axis == 2U) ? step_cm : 0.0f;
        wps[0][3] = (profile == 0U) ? 0.0f : ramp_s;
        wps[1][0] = 0.0f;
        wps[1][1] = 0.0f;
        wps[1][2] = 0.0f;
        wps[1][3] = (profile == 0U) ? (0.5f * move_s) : (0.5f * move_s + ramp_s);
        n = 2U;
    } else if (shape->id == 3U || shape->id == 4U) {
        float path_s = move_s - ((profile != 0U) ? ramp_s : 0.0f);
        int total_q = (int)(laps * 4.0f + 0.5f);
        int q;
        if (total_q < 1) total_q = 4;
        if (total_q > (int)TRAJ_WP_MAX) total_q = (int)TRAJ_WP_MAX;
        for (q = 1; q <= total_q; q++) {
            float frac = (float)q / (float)total_q;
            float th = 2.0f * TRAJ_SHAPE_PI * laps * frac;
            float w_t = path_s * frac;
            float dx = 0.0f, dy = 0.0f, dz = 0.0f;
            TrajShape_Offset(shape, th, &dx, &dy, &dz);
            wps[n][0] = dx;
            wps[n][1] = dy;
            wps[n][2] = dz;
            wps[n][3] = invert_warp_time(profile, w_t, move_s, ramp_s);
            n++;
        }
    }
    TrajWp_Init(trk, r_cm, n, (const float (*)[4])wps);
}

uint8_t TrajWp_Tick(traj_wp_tracker_t *trk, float dt_s, float s,
                    float pos_x_cm, float pos_y_cm, float ref_x_cm, float ref_y_cm,
                    uint8_t *arrived_idx)
{
    float ex, ey, err_ref, dx, dy, dist_wp;
    uint8_t k;

    if (trk == NULL || trk->count == 0U) {
        return 0U;
    }
    trk->elapsed_s += dt_s;
    trk->int_s_accum += s * dt_s;
    trk->time_lost_accum += (1.0f - s) * dt_s;

    ex = pos_x_cm - ref_x_cm;
    ey = pos_y_cm - ref_y_cm;
    err_ref = sqrtf(ex * ex + ey * ey);
    if (err_ref > trk->seg_peak_err_cm) {
        trk->seg_peak_err_cm = err_ref;
    }

    k = trk->active_seg;
    if (k < trk->count && trk->entries[k].arrived == 0U) {
        dx = pos_x_cm - trk->entries[k].wx_cm;
        dy = pos_y_cm - trk->entries[k].wy_cm;
        dist_wp = sqrtf(dx * dx + dy * dy);
        if (dist_wp <= trk->r_cm) {
            float prev_plan = (k > 0U) ? trk->entries[k - 1U].t_plan_s : 0.0f;
            float min_t = prev_plan + 0.25f * (trk->entries[k].t_plan_s - prev_plan);
            if (trk->int_s_accum >= min_t || k == 0U ||
                fabsf(trk->entries[k].wx_cm - trk->entries[k - 1U].wx_cm) +
                fabsf(trk->entries[k].wy_cm - trk->entries[k - 1U].wy_cm) > 2.0f * trk->r_cm) {
                trk->entries[k].arrived = 1U;
                trk->entries[k].t_actual_s = trk->elapsed_s;
                trk->entries[k].peak_err_cm = trk->seg_peak_err_cm;
                trk->entries[k].time_lost_s = trk->time_lost_accum;
                trk->entries[k].int_s_s = trk->int_s_accum;
                trk->seg_peak_err_cm = 0.0f;
                trk->active_seg = (uint8_t)(k + 1U);
                if (arrived_idx != NULL) {
                    *arrived_idx = k;
                }
                return 1U;
            }
        }
    }
    return 0U;
}
