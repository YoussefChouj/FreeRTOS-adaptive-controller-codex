#include "wfb_prim.h"
#include <stdio.h>
#include <math.h>

#define ASSERT(cond) do { if (!(cond)) { printf("FAIL %s:%d\n", __FILE__, __LINE__); return 1; } } while(0)

static wfb_prim_cfg_t cfg;
static wfb_prim_t p;
static wfb_prim_in_t in;
static wfb_prim_out_t out;

static int test_1_init_takeoff(void) {
    wfb_prim_init(&p);
    ASSERT(p.state == WFB_PRIM_IDLE);

    ASSERT(wfb_prim_takeoff(&p) == WFB_ERR_NONE);
    ASSERT(p.state == WFB_PRIM_CLIMB);

    ASSERT(wfb_prim_takeoff(&p) == WFB_ERR_STATE);

    p.state = WFB_PRIM_HOVER;
    ASSERT(wfb_prim_takeoff(&p) == WFB_ERR_STATE);
    return 0;
}

static int test_2_climb_settle(void) {
    wfb_prim_init(&p);
    wfb_prim_default_cfg(&cfg);
    wfb_prim_takeoff(&p);

    in.x_m = 1.0f; in.y_m = 1.0f; in.z_m = 0.0f; in.dt_s = 0.1f;
    wfb_prim_step(&p, &cfg, &in, &out);
    ASSERT(out.x_sp_m == 0.0f && out.y_sp_m == 0.0f && out.z_sp_m == 0.5f);
    ASSERT(out.descend == 0);
    ASSERT(p.state == WFB_PRIM_CLIMB);

    /* Move within radius */
    in.x_m = 0.0f; in.y_m = 0.0f; in.z_m = 0.4f; /* 0.1m dist < 0.15 */
    for (int i=0; i<9; i++) {
        wfb_prim_step(&p, &cfg, &in, &out);
        ASSERT(p.state == WFB_PRIM_CLIMB);
    }
    wfb_prim_step(&p, &cfg, &in, &out);
    ASSERT(p.state == WFB_PRIM_HOVER);

    /* Leaving radius restarts timer */
    wfb_prim_init(&p);
    wfb_prim_takeoff(&p);
    wfb_prim_step(&p, &cfg, &in, &out);
    in.z_m = 0.0f; /* Leave radius */
    wfb_prim_step(&p, &cfg, &in, &out);
    in.z_m = 0.4f; /* Enter again */
    for (int i=0; i<9; i++) {
        wfb_prim_step(&p, &cfg, &in, &out);
        ASSERT(p.state == WFB_PRIM_CLIMB);
    }
    wfb_prim_step(&p, &cfg, &in, &out);
    ASSERT(p.state == WFB_PRIM_HOVER);

    return 0;
}

static int test_3_traj_return(void) {
    wfb_prim_init(&p);
    wfb_prim_default_cfg(&cfg);

    ASSERT(wfb_prim_traj_begin(&p) == WFB_ERR_STATE);
    p.state = WFB_PRIM_HOVER;
    ASSERT(wfb_prim_traj_begin(&p) == WFB_ERR_NONE);
    ASSERT(p.state == WFB_PRIM_TRAJ);

    in.x_m = 0.0f; in.y_m = 0.0f; in.z_m = 0.5f; in.dt_s = 0.1f;
    wfb_prim_step(&p, &cfg, &in, &out);
    ASSERT(out.x_sp_m == 0.0f && out.y_sp_m == 0.0f && out.z_sp_m == 0.5f);
    ASSERT(out.descend == 0);

    in.x_m = 3.0f; in.y_m = 4.0f;
    ASSERT(wfb_prim_traj_end(&p, &in) == WFB_ERR_NONE);
    ASSERT(p.state == WFB_PRIM_RETURN);

    /* Starts at 3, 4 */
    wfb_prim_step(&p, &cfg, &in, &out);
    ASSERT(fabsf(out.x_sp_m - (3.0f - 0.3f*0.1f*(3.0f/5.0f))) <= 1e-4f);
    ASSERT(fabsf(out.y_sp_m - (4.0f - 0.3f*0.1f*(4.0f/5.0f))) <= 1e-4f);
    ASSERT(out.z_sp_m == 0.5f);

    /* Fast forward */
    in.dt_s = 20.0f; /* 6.0m move > 5.0m dist */
    wfb_prim_step(&p, &cfg, &in, &out);
    ASSERT(out.x_sp_m == 0.0f && out.y_sp_m == 0.0f);
    ASSERT(p.state == WFB_PRIM_SETTLE);

    in.dt_s = 1.1f;
    in.x_m = 0.0f; in.y_m = 0.0f; in.z_m = 0.5f; /* in radius */
    wfb_prim_step(&p, &cfg, &in, &out);
    ASSERT(p.state == WFB_PRIM_HOVER);

    return 0;
}

static int test_4_land(void) {
    wfb_prim_init(&p);
    wfb_prim_default_cfg(&cfg);
    ASSERT(wfb_prim_land(&p, &in) == WFB_ERR_STATE);

    p.state = WFB_PRIM_CLIMB;
    in.x_m = 0.6f; in.y_m = 0.8f; in.dt_s = 0.1f;
    ASSERT(wfb_prim_land(&p, &in) == WFB_ERR_NONE);
    ASSERT(p.state == WFB_PRIM_RETURN);

    /* Reaches 0,0 (dist 1.0m takes 3.33s at 0.3m/s) */
    in.dt_s = 4.0f;
    wfb_prim_step(&p, &cfg, &in, &out);
    ASSERT(p.state == WFB_PRIM_SETTLE);

    in.dt_s = 1.1f;
    in.x_m = 0.0f; in.y_m = 0.0f; in.z_m = 0.5f;
    wfb_prim_step(&p, &cfg, &in, &out);
    ASSERT(p.state == WFB_PRIM_DESCEND);
    ASSERT(out.x_sp_m == 0.0f && out.y_sp_m == 0.0f);
    ASSERT(out.descend == 1);

    return 0;
}

static int test_5_land_every_step(void) {
    wfb_prim_init(&p);
    wfb_prim_default_cfg(&cfg);
    p.state = WFB_PRIM_RETURN;
    p.land_after_return = 0;
    p.x_sp_m = 3.0f; p.y_sp_m = 4.0f; p.return_t = 5.0f;

    in.x_m = 10.0f; in.y_m = 10.0f; in.dt_s = 0.1f;
    ASSERT(wfb_prim_land(&p, &in) == WFB_ERR_NONE);
    ASSERT(p.state == WFB_PRIM_RETURN);
    ASSERT(p.land_after_return == 1);
    ASSERT(p.return_t == 0.0f);
    ASSERT(p.x_sp_m == 3.0f && p.y_sp_m == 4.0f);

    p.return_t = 5.0f;
    ASSERT(wfb_prim_land(&p, &in) == WFB_ERR_NONE);
    ASSERT(p.return_t == 5.0f); /* Does not reset */

    p.state = WFB_PRIM_DESCEND;
    ASSERT(wfb_prim_land(&p, &in) == WFB_ERR_NONE);
    ASSERT(p.state == WFB_PRIM_DESCEND);

    return 0;
}

static int test_6_timeout(void) {
    wfb_prim_init(&p);
    wfb_prim_default_cfg(&cfg);
    p.state = WFB_PRIM_RETURN;
    p.land_after_return = 1;
    p.x_sp_m = 3.0f; p.y_sp_m = 4.0f; p.return_t = 9.9f;

    in.x_m = 10.0f; in.y_m = 10.0f; in.z_m = 0.0f; in.dt_s = 0.2f; /* Will push over 10.0 */
    wfb_prim_step(&p, &cfg, &in, &out);
    ASSERT(p.state == WFB_PRIM_DESCEND);
    ASSERT(out.x_sp_m == 10.0f && out.y_sp_m == 10.0f); /* Frozen at vehicle pos */
    ASSERT(out.descend == 1);

    wfb_prim_init(&p);
    p.state = WFB_PRIM_RETURN;
    p.land_after_return = 0;
    p.x_sp_m = 3.0f; p.y_sp_m = 4.0f; p.return_t = 9.9f;
    wfb_prim_step(&p, &cfg, &in, &out);
    ASSERT(p.state == WFB_PRIM_RETURN); /* without landing stays in RETURN/SETTLE */

    in.dt_s = 20.0f;
    wfb_prim_step(&p, &cfg, &in, &out);
    ASSERT(p.state == WFB_PRIM_SETTLE);
    wfb_prim_step(&p, &cfg, &in, &out);
    ASSERT(p.state == WFB_PRIM_SETTLE); /* Stays in SETTLE since dist not met */

    return 0;
}

static int test_7_land_in_place(void) {
    wfb_prim_init(&p);
    wfb_prim_default_cfg(&cfg);
    ASSERT(wfb_prim_land_in_place(&p) == WFB_ERR_STATE);

    p.state = WFB_PRIM_CLIMB;
    ASSERT(wfb_prim_land_in_place(&p) == WFB_ERR_NONE);
    ASSERT(p.state == WFB_PRIM_DESCEND);

    in.x_m = 5.0f; in.y_m = 6.0f; in.dt_s = 0.1f;
    wfb_prim_step(&p, &cfg, &in, &out);
    ASSERT(out.x_sp_m == 5.0f && out.y_sp_m == 6.0f);

    in.x_m = 7.0f; in.y_m = 8.0f;
    ASSERT(wfb_prim_land_in_place(&p) == WFB_ERR_NONE);
    wfb_prim_step(&p, &cfg, &in, &out);
    ASSERT(out.x_sp_m == 5.0f && out.y_sp_m == 6.0f); /* Keeps already frozen */

    return 0;
}

static int test_8_disarmed(void) {
    wfb_prim_init(&p);
    wfb_prim_default_cfg(&cfg);
    p.state = WFB_PRIM_TRAJ;
    p.return_t = 5.0f;
    p.settle_t = 5.0f;
    wfb_prim_disarmed(&p);
    ASSERT(p.state == WFB_PRIM_IDLE);
    ASSERT(p.return_t == 0.0f && p.settle_t == 0.0f);
    return 0;
}

static int test_9_default_cfg(void) {
    wfb_prim_default_cfg(&cfg);
    ASSERT(fabsf(cfg.hover_z_m - 0.5f) < 1e-4f);
    ASSERT(fabsf(cfg.xy_rate_mps - 0.3f) < 1e-4f);
    ASSERT(fabsf(cfg.settle_radius_m - 0.15f) < 1e-4f);
    ASSERT(fabsf(cfg.settle_time_s - 1.0f) < 1e-4f);
    ASSERT(fabsf(cfg.return_timeout_s - 10.0f) < 1e-4f);
    return 0;
}

static int test_10_non_finite_position(void) {
    float nan_v = nanf("");
    int i;

    /* land with a NaN estimate: the return leg starts at the hover point, never at NaN */
    wfb_prim_init(&p);
    wfb_prim_default_cfg(&cfg);
    p.state = WFB_PRIM_HOVER;
    in.x_m = nan_v; in.y_m = nan_v; in.z_m = 0.5f; in.dt_s = 0.1f;
    ASSERT(wfb_prim_land(&p, &in) == WFB_ERR_NONE);
    ASSERT(p.x_sp_m == 0.0f && p.y_sp_m == 0.0f);
    for (i = 0; i < 150; i++) {
        wfb_prim_step(&p, &cfg, &in, &out);
        ASSERT(isfinite(out.x_sp_m) && isfinite(out.y_sp_m) && isfinite(out.z_sp_m));
    }
    /* never settles on a NaN estimate, so the return timeout forces the descent */
    ASSERT(p.state == WFB_PRIM_DESCEND && out.descend == 1);
    ASSERT(out.x_sp_m == 0.0f && out.y_sp_m == 0.0f);

    /* traj_end with a NaN estimate */
    wfb_prim_init(&p);
    p.state = WFB_PRIM_TRAJ;
    ASSERT(wfb_prim_traj_end(&p, &in) == WFB_ERR_NONE);
    ASSERT(p.x_sp_m == 0.0f && p.y_sp_m == 0.0f);

    /* land_in_place with a NaN estimate keeps the last finite setpoint */
    wfb_prim_init(&p);
    p.state = WFB_PRIM_RETURN;
    p.x_sp_m = 0.4f; p.y_sp_m = -0.2f;
    ASSERT(wfb_prim_land_in_place(&p) == WFB_ERR_NONE);
    wfb_prim_step(&p, &cfg, &in, &out);
    ASSERT(out.descend == 1);
    ASSERT(fabsf(out.x_sp_m - 0.4f) < 1e-6f && fabsf(out.y_sp_m + 0.2f) < 1e-6f);
    return 0;
}

int main(void) {
    int passed = 0;
    if (test_1_init_takeoff() == 0) passed++; else return 1;
    if (test_2_climb_settle() == 0) passed++; else return 1;
    if (test_3_traj_return() == 0) passed++; else return 1;
    if (test_4_land() == 0) passed++; else return 1;
    if (test_5_land_every_step() == 0) passed++; else return 1;
    if (test_6_timeout() == 0) passed++; else return 1;
    if (test_7_land_in_place() == 0) passed++; else return 1;
    if (test_8_disarmed() == 0) passed++; else return 1;
    if (test_9_default_cfg() == 0) passed++; else return 1;
    if (test_10_non_finite_position() == 0) passed++; else return 1;

    printf("PASS %d\n", passed);
    return 0;
}
