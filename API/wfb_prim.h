#ifndef WFB_PRIM_H
#define WFB_PRIM_H

#include <stdint.h>
#include "wfb_types.h"

typedef enum {
    WFB_PRIM_IDLE = 0,
    WFB_PRIM_CLIMB,
    WFB_PRIM_HOVER,
    WFB_PRIM_TRAJ,
    WFB_PRIM_RETURN,
    WFB_PRIM_SETTLE,
    WFB_PRIM_DESCEND
} wfb_prim_state_t;

typedef struct {
    float hover_z_m;
    float xy_rate_mps;
    float settle_radius_m;
    float settle_time_s;
    float return_timeout_s;
} wfb_prim_cfg_t;

typedef struct {
    float x_m;
    float y_m;
    float z_m;
    float dt_s;
} wfb_prim_in_t;

typedef struct {
    float x_sp_m;
    float y_sp_m;
    float z_sp_m;
    uint8_t descend;
} wfb_prim_out_t;

typedef struct {
    uint8_t state;
    uint8_t land_after_return;
    float x_sp_m;
    float y_sp_m;
    float settle_t;
    float return_t;
    uint8_t descend_frozen;
} wfb_prim_t;

void wfb_prim_init(wfb_prim_t *p);
wfb_err_t wfb_prim_takeoff(wfb_prim_t *p);
wfb_err_t wfb_prim_traj_begin(wfb_prim_t *p);
wfb_err_t wfb_prim_traj_end(wfb_prim_t *p, const wfb_prim_in_t *in);
wfb_err_t wfb_prim_land(wfb_prim_t *p, const wfb_prim_in_t *in);
wfb_err_t wfb_prim_land_in_place(wfb_prim_t *p);
void wfb_prim_disarmed(wfb_prim_t *p);
void wfb_prim_step(wfb_prim_t *p, const wfb_prim_cfg_t *cfg, const wfb_prim_in_t *in, wfb_prim_out_t *out);

void wfb_prim_default_cfg(wfb_prim_cfg_t *out);

#endif /* WFB_PRIM_H */
