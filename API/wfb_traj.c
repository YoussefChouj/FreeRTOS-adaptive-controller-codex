#include "wfb_traj.h"
#include <stdint.h>
#include <stddef.h>
#include <math.h>
#include <string.h>

/* Trajectory limits default configuration.
   x_abs_m        maximum |x| in world frame, m
   y_abs_m        maximum |y| in world frame, m
   z_min_m        minimum altitude, m
   z_max_m        maximum altitude (ceiling), m
   v_max_mps      maximum segment velocity, m/s
   endpoint_tol_m 3-D tolerance to hover point at endpoints, m
   x_abs/y_abs/z_max = the soft boundary, 0.3 m inside the wfb_safety fence and ceiling (2026-10-03 grill). */
#define WFB_TRAJ_LIMITS_ROW(x_abs_m, y_abs_m, z_min_m, z_max_m, v_max_mps, endpoint_tol_m) \
    { (x_abs_m), (y_abs_m), (z_min_m), (z_max_m), (v_max_mps), (endpoint_tol_m) }

static const wfb_traj_limits_t s_default_limits =
/*                    x_abs  y_abs  z_min  z_max  v_max  tol */
    WFB_TRAJ_LIMITS_ROW(1.3f,  1.7f,  0.3f,  1.4f,  1.0f,  0.10f); /* PROPOSED */

void wfb_traj_default_limits(wfb_traj_limits_t *out)
{
    if (out != NULL) {
        *out = s_default_limits;
    }
}

/* Stored yaw range, deg. Commit rejects anything outside it, which keeps every angle formed in
   wfb_traj_sample inside (-540, 540) deg. */
#define WFB_TRAJ_YAW_ABS_DEG 180.0f

/* Wraps an angle in (-540, 540) deg into [-180, 180] deg. No loop: a loop on an unbounded float
   never ends once 360 is below the float's resolution. */
static float wfb_traj_wrap180(float a)
{
    if (a > 180.0f) {
        a -= 360.0f;
    } else if (a < -180.0f) {
        a += 360.0f;
    }
    return a;
}

uint32_t wfb_crc32(const uint8_t *data, uint32_t len)
{
    uint32_t crc;
    uint32_t i;
    int j;

    crc = 0xFFFFFFFFu;
    if (data == NULL) {
        return 0u;
    }

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
    return crc ^ 0xFFFFFFFFu;
}

void wfb_traj_init(wfb_traj_t *tr, wfb_traj_point_t *buf, uint16_t cap)
{
    if (tr == NULL) {
        return;
    }
    tr->buf = buf;
    tr->cap = cap;
    tr->n = 0u;
    tr->seg = 0u;
    tr->rx = 0u;
    tr->crc_hi = 0u;
    tr->crc_hi_set = 0u;
    tr->state = (uint8_t)WFB_TRAJ_EMPTY;
    tr->crc_calc = 0u;
}

wfb_err_t wfb_traj_begin(wfb_traj_t *tr, float n_points)
{
    if (tr == NULL) {
        return WFB_ERR_STATE;
    }
    if (tr->state == (uint8_t)WFB_TRAJ_EXECUTING) {
        return WFB_ERR_STATE;
    }
    if (isnan(n_points) || isinf(n_points) || floorf(n_points) != n_points) {
        return WFB_ERR_RANGE;
    }
    if (n_points < 2.0f || n_points > (float)tr->cap || n_points > (float)WFB_TRAJ_MAX_POINTS) {
        return WFB_ERR_RANGE;
    }
    tr->n = (uint16_t)n_points;
    tr->seg = 0u;
    tr->rx = 0u;
    tr->crc_hi = 0u;
    tr->crc_hi_set = 0u;
    tr->crc_calc = 0u;
    tr->state = (uint8_t)WFB_TRAJ_LOADING;
    if (tr->buf != NULL && tr->cap > 0u) {
        memset(tr->buf, 0, (size_t)tr->cap * sizeof(wfb_traj_point_t));
    }
    return WFB_ERR_NONE;
}

wfb_err_t wfb_traj_append(wfb_traj_t *tr, float v)
{
    uint32_t point_idx;
    uint32_t field_idx;

    if (tr == NULL || tr->state != (uint8_t)WFB_TRAJ_LOADING) {
        return WFB_ERR_STATE;
    }
    if (isnan(v) || isinf(v)) {
        return WFB_ERR_RANGE;
    }
    if (tr->rx >= (uint32_t)(5u * tr->n)) {
        return WFB_ERR_COUNT;
    }

    point_idx = tr->rx / 5u;
    field_idx = tr->rx % 5u;

    if (tr->buf != NULL && point_idx < (uint32_t)tr->cap) {
        switch (field_idx) {
        case 0u:
            tr->buf[point_idx].x_m = v;
            break;
        case 1u:
            tr->buf[point_idx].y_m = v;
            break;
        case 2u:
            tr->buf[point_idx].z_m = v;
            break;
        case 3u:
            tr->buf[point_idx].yaw_deg = v;
            break;
        case 4u:
            tr->buf[point_idx].t_s = v;
            break;
        default:
            break;
        }
    }
    tr->rx++;
    return WFB_ERR_NONE;
}

wfb_err_t wfb_traj_crc_hi(wfb_traj_t *tr, float hi)
{
    if (tr == NULL || tr->state != (uint8_t)WFB_TRAJ_LOADING) {
        return WFB_ERR_STATE;
    }
    if (isnan(hi) || isinf(hi) || floorf(hi) != hi || hi < 0.0f || hi > 65535.0f) {
        return WFB_ERR_RANGE;
    }
    tr->crc_hi = (uint16_t)hi;
    tr->crc_hi_set = 1u;
    return WFB_ERR_NONE;
}

wfb_err_t wfb_traj_commit(wfb_traj_t *tr, float crc_lo, const wfb_traj_limits_t *lim, float hover_z_m)
{
    uint32_t expected_crc;
    uint32_t buf_len;
    uint16_t i;
    float dx, dy, dz, dt, dist, speed;

    if (tr == NULL) {
        return WFB_ERR_STATE;
    }

    /* 1. Check state: only accepted when LOADING. In any other state, return WFB_ERR_STATE and change nothing. */
    if (tr->state != (uint8_t)WFB_TRAJ_LOADING) {
        return WFB_ERR_STATE;
    }

    /* From here on, state is LOADING. Every failure leaves state EMPTY. */

    /* 2. crc_hi present */
    if (!tr->crc_hi_set) {
        tr->state = (uint8_t)WFB_TRAJ_EMPTY;
        return WFB_ERR_STATE;
    }

    /* 3. Float count: exactly 5N floats received */
    if (tr->rx != (uint32_t)(5u * tr->n)) {
        tr->state = (uint8_t)WFB_TRAJ_EMPTY;
        return WFB_ERR_COUNT;
    }

    /* 4. crc_lo range: 0 <= crc_lo <= 65535, integral, finite */
    if (isnan(crc_lo) || isinf(crc_lo) || floorf(crc_lo) != crc_lo || crc_lo < 0.0f || crc_lo > 65535.0f) {
        tr->state = (uint8_t)WFB_TRAJ_EMPTY;
        return WFB_ERR_RANGE;
    }

    if (lim == NULL || tr->buf == NULL || tr->n < 2u) {
        tr->state = (uint8_t)WFB_TRAJ_EMPTY;
        return WFB_ERR_RANGE;
    }

    expected_crc = ((uint32_t)tr->crc_hi << 16) | (uint32_t)crc_lo;

    /* 5. COMMIT Check 1: CRC32 */
    buf_len = (uint32_t)tr->n * (uint32_t)sizeof(wfb_traj_point_t);
    tr->crc_calc = wfb_crc32((const uint8_t *)tr->buf, buf_len);
    if (tr->crc_calc != expected_crc) {
        tr->state = (uint8_t)WFB_TRAJ_EMPTY;
        return WFB_ERR_CRC;
    }

    /* 6. COMMIT Check 2: t[0] == 0 and t strictly increasing */
    if (tr->buf[0].t_s != 0.0f || isnan(tr->buf[0].t_s)) {
        tr->state = (uint8_t)WFB_TRAJ_EMPTY;
        return WFB_ERR_TIME;
    }
    for (i = 1u; i < tr->n; i++) {
        if (isnan(tr->buf[i].t_s) || tr->buf[i].t_s <= tr->buf[i - 1u].t_s) {
            tr->state = (uint8_t)WFB_TRAJ_EMPTY;
            return WFB_ERR_TIME;
        }
    }

    /* 7. COMMIT Check 3: Envelope bounds and yaw range. Negated form: a NaN value or limit fails. */
    for (i = 0u; i < tr->n; i++) {
        if (!(fabsf(tr->buf[i].x_m) <= lim->x_abs_m) ||
            !(fabsf(tr->buf[i].y_m) <= lim->y_abs_m) ||
            !(tr->buf[i].z_m >= lim->z_min_m) || !(tr->buf[i].z_m <= lim->z_max_m) ||
            !(fabsf(tr->buf[i].yaw_deg) <= WFB_TRAJ_YAW_ABS_DEG)) {
            tr->state = (uint8_t)WFB_TRAJ_EMPTY;
            return WFB_ERR_BOUNDS;
        }
    }

    /* 8. COMMIT Check 4: Endpoint check */
    /* Hover point: (0, 0, hover_z_m) */
    dx = tr->buf[0].x_m;
    dy = tr->buf[0].y_m;
    dz = tr->buf[0].z_m - hover_z_m;
    dist = sqrtf(dx * dx + dy * dy + dz * dz);
    if (!(dist <= lim->endpoint_tol_m)) {
        tr->state = (uint8_t)WFB_TRAJ_EMPTY;
        return WFB_ERR_ENDPOINT;
    }
    dx = tr->buf[tr->n - 1u].x_m;
    dy = tr->buf[tr->n - 1u].y_m;
    dz = tr->buf[tr->n - 1u].z_m - hover_z_m;
    dist = sqrtf(dx * dx + dy * dy + dz * dz);
    if (!(dist <= lim->endpoint_tol_m)) {
        tr->state = (uint8_t)WFB_TRAJ_EMPTY;
        return WFB_ERR_ENDPOINT;
    }

    /* 9. COMMIT Check 5: Segment speed <= v_max_mps */
    for (i = 0u; i < (uint16_t)(tr->n - 1u); i++) {
        dx = tr->buf[i + 1u].x_m - tr->buf[i].x_m;
        dy = tr->buf[i + 1u].y_m - tr->buf[i].y_m;
        dz = tr->buf[i + 1u].z_m - tr->buf[i].z_m;
        dt = tr->buf[i + 1u].t_s - tr->buf[i].t_s;
        dist = sqrtf(dx * dx + dy * dy + dz * dz);
        speed = dist / dt;
        if (!(speed <= lim->v_max_mps)) {
            tr->state = (uint8_t)WFB_TRAJ_EMPTY;
            return WFB_ERR_SPEED;
        }
    }

    tr->seg = 0u;
    tr->state = (uint8_t)WFB_TRAJ_READY;
    return WFB_ERR_NONE;
}

wfb_err_t wfb_traj_start(wfb_traj_t *tr)
{
    if (tr == NULL || tr->state != (uint8_t)WFB_TRAJ_READY) {
        return WFB_ERR_STATE;
    }
    tr->seg = 0u;
    tr->state = (uint8_t)WFB_TRAJ_EXECUTING;
    return WFB_ERR_NONE;
}

wfb_err_t wfb_traj_stop(wfb_traj_t *tr)
{
    if (tr == NULL || tr->state != (uint8_t)WFB_TRAJ_EXECUTING) {
        return WFB_ERR_STATE;
    }
    tr->state = (uint8_t)WFB_TRAJ_READY;
    return WFB_ERR_NONE;
}

wfb_err_t wfb_traj_clear(wfb_traj_t *tr)
{
    if (tr == NULL || tr->state == (uint8_t)WFB_TRAJ_EXECUTING) {
        return WFB_ERR_STATE;
    }
    tr->n = 0u;
    tr->seg = 0u;
    tr->rx = 0u;
    tr->crc_hi = 0u;
    tr->crc_hi_set = 0u;
    tr->crc_calc = 0u;
    tr->state = (uint8_t)WFB_TRAJ_EMPTY;
    return WFB_ERR_NONE;
}

int wfb_traj_sample(wfb_traj_t *tr, float t_s, wfb_traj_point_t *out)
{
    uint16_t k;
    float t0, t1, dt, frac, diff, yaw;

    if (tr == NULL || out == NULL) {
        return 0;
    }

    if (tr->state == (uint8_t)WFB_TRAJ_DONE) {
        if (tr->buf != NULL && tr->n > 0u) {
            *out = tr->buf[tr->n - 1u];
        }
        return 0;
    }

    if (tr->state != (uint8_t)WFB_TRAJ_EXECUTING) {
        return 0;
    }

    if (tr->buf == NULL || tr->n == 0u) {
        return 0;
    }

    /* If t_s >= last point's time, transition to DONE, out = last point, return 0 */
    if (isnan(t_s) || t_s >= tr->buf[tr->n - 1u].t_s) {
        tr->state = (uint8_t)WFB_TRAJ_DONE;
        *out = tr->buf[tr->n - 1u];
        return 0;
    }

    /* If t_s <= first point's time, return first point, running (1) */
    if (t_s <= tr->buf[0].t_s) {
        tr->seg = 0u;
        *out = tr->buf[0];
        return 1;
    }

    /* Maintain/find segment k such that buf[k].t_s <= t_s < buf[k+1].t_s */
    while (tr->seg > 0u && t_s < tr->buf[tr->seg].t_s) {
        tr->seg--;
    }
    while ((uint16_t)(tr->seg + 1u) < (uint16_t)(tr->n - 1u) && t_s >= tr->buf[tr->seg + 1u].t_s) {
        tr->seg++;
    }

    k = tr->seg;
    t0 = tr->buf[k].t_s;
    t1 = tr->buf[k + 1u].t_s;
    dt = t1 - t0;
    if (dt <= 0.0f) {
        frac = 0.0f;
    } else {
        frac = (t_s - t0) / dt;
    }

    out->t_s = t_s;
    out->x_m = tr->buf[k].x_m + frac * (tr->buf[k + 1u].x_m - tr->buf[k].x_m);
    out->y_m = tr->buf[k].y_m + frac * (tr->buf[k + 1u].y_m - tr->buf[k].y_m);
    out->z_m = tr->buf[k].z_m + frac * (tr->buf[k + 1u].z_m - tr->buf[k].z_m);

    /* Yaw shortest-arc interpolation. Both yaws are within +/-180 deg (commit check 3) and frac is
       in [0, 1], so each sum is inside the range wfb_traj_wrap180 accepts. */
    diff = wfb_traj_wrap180(tr->buf[k + 1u].yaw_deg - tr->buf[k].yaw_deg);
    yaw = wfb_traj_wrap180(tr->buf[k].yaw_deg + frac * diff);
    out->yaw_deg = yaw;

    return 1;
}
