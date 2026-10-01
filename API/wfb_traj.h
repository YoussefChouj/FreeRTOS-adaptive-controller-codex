#ifndef WFB_TRAJ_H
#define WFB_TRAJ_H

#include <stdint.h>
#include <stddef.h>
#include "wfb_types.h"

#define WFB_TRAJ_MAX_POINTS 600u

typedef enum {
    WFB_TRAJ_EMPTY = 0,
    WFB_TRAJ_LOADING,
    WFB_TRAJ_READY,
    WFB_TRAJ_EXECUTING,
    WFB_TRAJ_DONE
} wfb_traj_state_t;

typedef struct {
    float x_abs_m;
    float y_abs_m;
    float z_min_m;
    float z_max_m;
    float v_max_mps;
    float endpoint_tol_m;
} wfb_traj_limits_t;

typedef struct {
    wfb_traj_point_t *buf;
    uint16_t cap;
    uint16_t n;
    uint16_t seg;
    uint32_t rx;
    uint16_t crc_hi;
    uint8_t crc_hi_set;
    uint8_t state;
    uint32_t crc_calc;
} wfb_traj_t;

void      wfb_traj_init(wfb_traj_t *tr, wfb_traj_point_t *buf, uint16_t cap);
wfb_err_t wfb_traj_begin(wfb_traj_t *tr, float n_points);
wfb_err_t wfb_traj_append(wfb_traj_t *tr, float v);
wfb_err_t wfb_traj_crc_hi(wfb_traj_t *tr, float hi);
wfb_err_t wfb_traj_commit(wfb_traj_t *tr, float crc_lo, const wfb_traj_limits_t *lim, float hover_z_m);
wfb_err_t wfb_traj_start(wfb_traj_t *tr);
wfb_err_t wfb_traj_stop(wfb_traj_t *tr);
wfb_err_t wfb_traj_clear(wfb_traj_t *tr);
int       wfb_traj_sample(wfb_traj_t *tr, float t_s, wfb_traj_point_t *out); /* 1 = running, 0 = finished (out = last point, state DONE) */
uint32_t  wfb_crc32(const uint8_t *data, uint32_t len);
void      wfb_traj_default_limits(wfb_traj_limits_t *out);

#endif /* WFB_TRAJ_H */
