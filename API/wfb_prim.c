#include "wfb_prim.h"
#include <math.h>
#include <string.h>

/* One row per config, tunables only; runtime fields start at 0.
   hover_z_m        default hover height
   xy_rate_mps      return leg XY rate
   settle_radius_m  settle arrival distance
   settle_time_s    settle hold time
   return_timeout_s return phase timeout before forced descent */
#define WFB_PRIM_CFG_ROW(hover_z_m, xy_rate_mps, settle_radius_m, settle_time_s, return_timeout_s) \
    { hover_z_m, xy_rate_mps, settle_radius_m, settle_time_s, return_timeout_s }

static const wfb_prim_cfg_t wfb_prim_default_cfg_table =
/*                   hover_z_m xy_rate_mps settle_radius_m settle_time_s return_timeout_s */
    WFB_PRIM_CFG_ROW(0.5f,     0.3f,       0.15f,          1.0f,         10.0f); /* PROPOSED */

void wfb_prim_default_cfg(wfb_prim_cfg_t *out) {
    memcpy(out, &wfb_prim_default_cfg_table, sizeof(wfb_prim_cfg_t));
}

/* A non-finite position estimate must never become a setpoint: the caller gets the fallback instead. */
static float wfb_prim_finite_or(float v, float fallback) {
    return isfinite(v) ? v : fallback;
}

void wfb_prim_init(wfb_prim_t *p) {
    p->state = WFB_PRIM_IDLE;
    p->land_after_return = 0;
    p->x_sp_m = 0.0f;
    p->y_sp_m = 0.0f;
    p->settle_t = 0.0f;
    p->return_t = 0.0f;
    p->descend_frozen = 0;
}

wfb_err_t wfb_prim_takeoff(wfb_prim_t *p) {
    if (p->state == WFB_PRIM_IDLE) {
        p->state = WFB_PRIM_CLIMB;
        p->settle_t = 0.0f;
        return WFB_ERR_NONE;
    }
    return WFB_ERR_STATE;
}

wfb_err_t wfb_prim_traj_begin(wfb_prim_t *p) {
    if (p->state == WFB_PRIM_HOVER) {
        p->state = WFB_PRIM_TRAJ;
        return WFB_ERR_NONE;
    }
    return WFB_ERR_STATE;
}

wfb_err_t wfb_prim_traj_end(wfb_prim_t *p, const wfb_prim_in_t *in) {
    if (p->state != WFB_PRIM_TRAJ) {
        return WFB_ERR_STATE;
    }
    p->state = WFB_PRIM_RETURN;
    p->land_after_return = 0;
    p->x_sp_m = wfb_prim_finite_or(in->x_m, 0.0f);
    p->y_sp_m = wfb_prim_finite_or(in->y_m, 0.0f);
    p->return_t = 0.0f;
    return WFB_ERR_NONE;
}

wfb_err_t wfb_prim_land(wfb_prim_t *p, const wfb_prim_in_t *in) {
    if (p->state == WFB_PRIM_IDLE) {
        return WFB_ERR_STATE;
    }
    if (p->state == WFB_PRIM_DESCEND) {
        return WFB_ERR_NONE;
    }
    if (p->state == WFB_PRIM_RETURN || p->state == WFB_PRIM_SETTLE) {
        if (p->land_after_return == 0) {
            p->land_after_return = 1;
            p->return_t = 0.0f;
        }
        return WFB_ERR_NONE;
    }
    p->state = WFB_PRIM_RETURN;
    p->land_after_return = 1;
    p->x_sp_m = wfb_prim_finite_or(in->x_m, 0.0f);
    p->y_sp_m = wfb_prim_finite_or(in->y_m, 0.0f);
    p->return_t = 0.0f;
    return WFB_ERR_NONE;
}

wfb_err_t wfb_prim_land_in_place(wfb_prim_t *p) {
    if (p->state == WFB_PRIM_IDLE) {
        return WFB_ERR_STATE;
    }
    if (p->state == WFB_PRIM_DESCEND) {
        return WFB_ERR_NONE;
    }
    p->state = WFB_PRIM_DESCEND;
    p->descend_frozen = 0;
    return WFB_ERR_NONE;
}

void wfb_prim_disarmed(wfb_prim_t *p) {
    p->state = WFB_PRIM_IDLE;
    p->land_after_return = 0;
    p->x_sp_m = 0.0f;
    p->y_sp_m = 0.0f;
    p->settle_t = 0.0f;
    p->return_t = 0.0f;
    p->descend_frozen = 0;
}

void wfb_prim_step(wfb_prim_t *p, const wfb_prim_cfg_t *cfg, const wfb_prim_in_t *in, wfb_prim_out_t *out) {
    float dx, dy, dz, dist, move, rate;

    out->x_sp_m = 0.0f;
    out->y_sp_m = 0.0f;
    out->z_sp_m = cfg->hover_z_m;
    out->descend = 0;

    if (p->state == WFB_PRIM_IDLE) {
        out->z_sp_m = 0.0f;
        return;
    }

    if (p->state == WFB_PRIM_CLIMB) {
        out->x_sp_m = 0.0f;
        out->y_sp_m = 0.0f;

        dx = out->x_sp_m - in->x_m;
        dy = out->y_sp_m - in->y_m;
        dz = out->z_sp_m - in->z_m;
        dist = sqrtf(dx * dx + dy * dy + dz * dz);

        if (dist <= cfg->settle_radius_m) {
            p->settle_t += in->dt_s;
            if (p->settle_t >= cfg->settle_time_s) {
                p->state = WFB_PRIM_HOVER;
                p->settle_t = 0.0f;
            }
        } else {
            p->settle_t = 0.0f;
        }
    } else if (p->state == WFB_PRIM_HOVER || p->state == WFB_PRIM_TRAJ) {
        out->x_sp_m = 0.0f;
        out->y_sp_m = 0.0f;
    } else if (p->state == WFB_PRIM_RETURN) {
        p->return_t += in->dt_s;

        dx = 0.0f - p->x_sp_m;
        dy = 0.0f - p->y_sp_m;
        dist = sqrtf(dx * dx + dy * dy);

        if (dist > 0.0f) {
            move = cfg->xy_rate_mps * in->dt_s;
            if (move >= dist) {
                p->x_sp_m = 0.0f;
                p->y_sp_m = 0.0f;
            } else {
                rate = move / dist;
                p->x_sp_m += dx * rate;
                p->y_sp_m += dy * rate;
            }
        }

        out->x_sp_m = p->x_sp_m;
        out->y_sp_m = p->y_sp_m;

        if (p->x_sp_m == 0.0f && p->y_sp_m == 0.0f) {
            p->state = WFB_PRIM_SETTLE;
            p->settle_t = 0.0f;
        }

        if (p->land_after_return && p->return_t > cfg->return_timeout_s) {
            p->state = WFB_PRIM_DESCEND;
            p->x_sp_m = wfb_prim_finite_or(in->x_m, p->x_sp_m);
            p->y_sp_m = wfb_prim_finite_or(in->y_m, p->y_sp_m);
            p->descend_frozen = 1;
            out->x_sp_m = p->x_sp_m;
            out->y_sp_m = p->y_sp_m;
            out->descend = 1;
        }
    } else if (p->state == WFB_PRIM_SETTLE) {
        p->return_t += in->dt_s;
        out->x_sp_m = 0.0f;
        out->y_sp_m = 0.0f;

        dx = out->x_sp_m - in->x_m;
        dy = out->y_sp_m - in->y_m;
        dz = out->z_sp_m - in->z_m;
        dist = sqrtf(dx * dx + dy * dy + dz * dz);

        if (dist <= cfg->settle_radius_m) {
            p->settle_t += in->dt_s;
            if (p->settle_t >= cfg->settle_time_s) {
                if (p->land_after_return) {
                    p->state = WFB_PRIM_DESCEND;
                    p->x_sp_m = 0.0f;
                    p->y_sp_m = 0.0f;
                    p->descend_frozen = 1;
                    out->x_sp_m = p->x_sp_m;
                    out->y_sp_m = p->y_sp_m;
                    out->descend = 1;
                } else {
                    p->state = WFB_PRIM_HOVER;
                    p->settle_t = 0.0f;
                }
            }
        } else {
            p->settle_t = 0.0f;
        }

        if (p->state == WFB_PRIM_SETTLE && p->land_after_return && p->return_t > cfg->return_timeout_s) {
            p->state = WFB_PRIM_DESCEND;
            p->x_sp_m = wfb_prim_finite_or(in->x_m, p->x_sp_m);
            p->y_sp_m = wfb_prim_finite_or(in->y_m, p->y_sp_m);
            p->descend_frozen = 1;
            out->x_sp_m = p->x_sp_m;
            out->y_sp_m = p->y_sp_m;
            out->descend = 1;
        }
    }

    if (p->state == WFB_PRIM_DESCEND) {
        if (!p->descend_frozen) {
            p->x_sp_m = wfb_prim_finite_or(in->x_m, p->x_sp_m);
            p->y_sp_m = wfb_prim_finite_or(in->y_m, p->y_sp_m);
            p->descend_frozen = 1;
        }
        out->x_sp_m = p->x_sp_m;
        out->y_sp_m = p->y_sp_m;
        out->z_sp_m = 0.0f;
        out->descend = 1;
    }
}
