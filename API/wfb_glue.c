#include "wfb_glue.h"
#include "wfb_traj.h"
#include "wfb_safety.h"
#include "wfb_prim.h"
#include <stdint.h>
#include <string.h>
#include <math.h>

/* Same section as API/mrac.c:16-21: the scatter file maps *(MRAC_CCM) to the 64 KB CCM
 * (USER/JX_FLY.sct). zero_init: no initialized data here, wfb_glue_init sets every value.
 * No DMA may touch these objects. Host builds place them in ordinary .bss. */
#ifndef MRAC_CCM
#ifdef __CC_ARM
    #define MRAC_CCM __attribute__((section("MRAC_CCM"), zero_init))
#else
    #define MRAC_CCM
#endif
#endif

/* Glue tunables (docs/firmware-table-pattern.md). Columns:
   hover_z_min_m   lowest SET_HOVER_Z accepted, m
   hover_z_max_m   highest SET_HOVER_Z accepted, m
   dt_max_s        longest tick interval fed to the timers (a stalled loop is not a hold), s */
typedef struct {
    float hover_z_min_m;
    float hover_z_max_m;
    float dt_max_s;
} wfb_glue_cfg_t;

#define WFB_GLUE_CFG_ROW(hover_z_min_m, hover_z_max_m, dt_max_s) \
    { (hover_z_min_m), (hover_z_max_m), (dt_max_s) }

static const wfb_glue_cfg_t s_glue_cfg =
    WFB_GLUE_CFG_ROW(0.3f,          1.2f,          0.05f); /* PROPOSED */

typedef struct {
    wfb_traj_point_t buf[WFB_TRAJ_MAX_POINTS];
    wfb_traj_t traj;
    wfb_traj_limits_t traj_lim;
    wfb_safety_t safety;
    wfb_safety_limits_t safety_lim;
    wfb_prim_t prim;
    wfb_prim_cfg_t prim_cfg;
    wfb_glue_in_t last_in;     /* previous tick's snapshot, used by the command checks */
    uint32_t last_ms;
    uint32_t hb_ref_ms;        /* TAKEOFF accept, then every HEARTBEAT */
    uint32_t traj_start_ms;
    uint8_t have_tick;
    uint8_t gs_flight_active;  /* TAKEOFF accepted until disarm */
    uint8_t takeoff_pending;   /* one-shot fly-up request for the next tick */
    uint8_t takeover;          /* pilot stick took a GS flight; latched until disarm */
    uint8_t land_now;          /* existing LANDING requested without the hover return (prim IDLE) */
    uint8_t applied_action;    /* highest safety action already applied */
    uint8_t last_err;
} wfb_glue_state_t;

static wfb_glue_state_t s_wfb MRAC_CCM;
wfb_status_t g_wfb_status MRAC_CCM;

/* Milliseconds from ref to now as seconds; 0 when now is before ref (the command task and the
 * 200 Hz loop read the tick count at slightly different moments). */
static float wfb_glue_age_s(uint32_t now_ms, uint32_t ref_ms)
{
    int32_t d = (int32_t)(now_ms - ref_ms);
    return (d > 0) ? (float)d * 0.001f : 0.0f;
}

static void wfb_glue_prim_in(wfb_prim_in_t *pin, const wfb_glue_in_t *in, float dt_s)
{
    pin->x_m = in->x_m;
    pin->y_m = in->y_m;
    pin->z_m = in->z_m;
    pin->dt_s = dt_s;
}

static void wfb_glue_stop_traj(void)
{
    if (s_wfb.traj.state == (uint8_t)WFB_TRAJ_EXECUTING) {
        (void)wfb_traj_stop(&s_wfb.traj);
    }
}

/* CMD 0x1A idx 1 and RC ch5: stop the trajectory, return to hover, settle, descend. Without a
 * GS primitive running (RC flight or pilot takeover) the existing LANDING is requested directly. */
static wfb_err_t wfb_glue_land(void)
{
    wfb_prim_in_t pin;

    if (!s_wfb.last_in.airborne) {
        return WFB_ERR_STATE;
    }
    wfb_glue_stop_traj();
    if (s_wfb.prim.state == (uint8_t)WFB_PRIM_IDLE) {
        s_wfb.land_now = 1u;
        return WFB_ERR_NONE;
    }
    wfb_glue_prim_in(&pin, &s_wfb.last_in, 0.0f);
    return wfb_prim_land(&s_wfb.prim, &pin);
}

static wfb_err_t wfb_glue_cmd_prim(uint8_t idx, float val, uint32_t now_ms)
{
    const wfb_glue_in_t *in = &s_wfb.last_in;
    wfb_err_t err;

    switch (idx) {
    case 0u: /* TAKEOFF */
        if (!s_wfb.have_tick || !in->armed || !in->motors_idle || !in->sbus_live || in->airborne ||
            s_wfb.takeover || s_wfb.safety.action != (uint8_t)WFB_ACT_NONE) {
            return WFB_ERR_STATE;
        }
        err = wfb_prim_takeoff(&s_wfb.prim);
        if (err == WFB_ERR_NONE) {
            s_wfb.gs_flight_active = 1u;
            s_wfb.takeoff_pending = 1u;
            s_wfb.hb_ref_ms = now_ms;
        }
        return err;
    case 1u: /* LAND */
        return wfb_glue_land();
    case 2u: /* HEARTBEAT */
        s_wfb.hb_ref_ms = now_ms;
        return WFB_ERR_NONE;
    case 3u: /* SET_HOVER_Z */
        if (s_wfb.prim.state != (uint8_t)WFB_PRIM_IDLE) {
            return WFB_ERR_STATE;
        }
        if (!(val >= s_glue_cfg.hover_z_min_m && val <= s_glue_cfg.hover_z_max_m)) {
            return WFB_ERR_RANGE; /* NaN fails the comparison */
        }
        s_wfb.prim_cfg.hover_z_m = val;
        return WFB_ERR_NONE;
    default:
        return WFB_ERR_RANGE;
    }
}

static wfb_err_t wfb_glue_cmd_traj(uint8_t idx, float val, uint32_t now_ms)
{
    wfb_prim_in_t pin;
    wfb_err_t err;

    if (idx <= 3u && !isfinite(val)) {
        return WFB_ERR_RANGE; /* BEGIN, APPEND, CRC_HI, COMMIT carry a payload */
    }
    switch (idx) {
    case 0u: /* BEGIN: clears the 12 kB buffer here, never in the tick */
        return wfb_traj_begin(&s_wfb.traj, val);
    case 1u: /* APPEND */
        return wfb_traj_append(&s_wfb.traj, val);
    case 2u: /* CRC_HI */
        return wfb_traj_crc_hi(&s_wfb.traj, val);
    case 3u: /* COMMIT */
        return wfb_traj_commit(&s_wfb.traj, val, &s_wfb.traj_lim, s_wfb.prim_cfg.hover_z_m);
    case 4u: /* START */
        if (s_wfb.prim.state != (uint8_t)WFB_PRIM_HOVER) {
            return WFB_ERR_STATE;
        }
        err = wfb_traj_start(&s_wfb.traj);
        if (err == WFB_ERR_NONE) {
            (void)wfb_prim_traj_begin(&s_wfb.prim);
            s_wfb.traj_start_ms = now_ms;
        }
        return err;
    case 5u: /* STOP */
        err = wfb_traj_stop(&s_wfb.traj);
        if (err == WFB_ERR_NONE && s_wfb.prim.state == (uint8_t)WFB_PRIM_TRAJ) {
            wfb_glue_prim_in(&pin, &s_wfb.last_in, 0.0f);
            (void)wfb_prim_traj_end(&s_wfb.prim, &pin);
        }
        return err;
    case 6u: /* CLEAR */
        return wfb_traj_clear(&s_wfb.traj);
    default:
        return WFB_ERR_RANGE;
    }
}

static void wfb_glue_mirror(float hb_age_s, float traj_t_s)
{
    g_wfb_status.prim_state = (float)s_wfb.prim.state;
    g_wfb_status.traj_state = (float)s_wfb.traj.state;
    g_wfb_status.traj_n = (float)s_wfb.traj.n;
    g_wfb_status.traj_rx = (float)s_wfb.traj.rx;
    g_wfb_status.traj_crc_hi = (float)s_wfb.traj.crc_hi;
    g_wfb_status.traj_crc_lo = (float)(s_wfb.traj.crc_calc & 0xFFFFu);
    g_wfb_status.traj_t = traj_t_s;
    g_wfb_status.last_err = (float)s_wfb.last_err;
    g_wfb_status.safety_trip = (float)s_wfb.safety.trip;
    g_wfb_status.hb_age = hb_age_s;
    g_wfb_status.gs_flight_active = (float)s_wfb.gs_flight_active;
    g_wfb_status.hover_z = s_wfb.prim_cfg.hover_z_m;
    g_wfb_status.airborne_t = s_wfb.safety.airborne_t;
}

void wfb_glue_init(void)
{
    memset(&s_wfb, 0, sizeof(s_wfb));
    wfb_traj_init(&s_wfb.traj, s_wfb.buf, (uint16_t)WFB_TRAJ_MAX_POINTS);
    wfb_traj_default_limits(&s_wfb.traj_lim);
    wfb_safety_init(&s_wfb.safety);
    wfb_safety_default_limits(&s_wfb.safety_lim);
    wfb_prim_init(&s_wfb.prim);
    wfb_prim_default_cfg(&s_wfb.prim_cfg);
    wfb_glue_mirror(0.0f, 0.0f);
}

uint8_t wfb_glue_on_cmd(uint8_t cmd, uint8_t idx, float val, uint32_t now_ms)
{
    wfb_err_t err;

    if (cmd == WFB_CMD_PRIM) {
        err = wfb_glue_cmd_prim(idx, val, now_ms);
    } else if (cmd == WFB_CMD_TRAJ) {
        err = wfb_glue_cmd_traj(idx, val, now_ms);
    } else {
        err = WFB_ERR_RANGE;
    }
    s_wfb.last_err = (uint8_t)err;
    wfb_glue_mirror(g_wfb_status.hb_age, g_wfb_status.traj_t); /* ages are refreshed by the tick */
    return (err == WFB_ERR_NONE) ? (uint8_t)WFB_RESULT_APPLIED : (uint8_t)WFB_RESULT_REJECTED;
}

uint8_t wfb_glue_rc_land(uint32_t now_ms)
{
    wfb_err_t err;

    if (!s_wfb.gs_flight_active || s_wfb.takeover) {
        return 0u; /* RC flight or pilot takeover: the caller runs today's ch5 code */
    }
    err = wfb_glue_cmd_prim(1u, 0.0f, now_ms);
    s_wfb.last_err = (uint8_t)err;
    wfb_glue_mirror(g_wfb_status.hb_age, g_wfb_status.traj_t);
    return (err == WFB_ERR_NONE) ? 1u : 0u;
}

void wfb_glue_disarmed(void)
{
    wfb_glue_stop_traj();
    wfb_prim_disarmed(&s_wfb.prim);
    wfb_safety_init(&s_wfb.safety);
    s_wfb.gs_flight_active = 0u;
    s_wfb.takeoff_pending = 0u;
    s_wfb.takeover = 0u;
    s_wfb.land_now = 0u;
    s_wfb.applied_action = (uint8_t)WFB_ACT_NONE;
    wfb_glue_mirror(0.0f, 0.0f);
}

/* A newly raised safety level is applied once; the module keeps it latched until disarm. */
static void wfb_glue_apply_action(uint8_t action, const wfb_prim_in_t *pin)
{
    if (action <= s_wfb.applied_action) {
        return;
    }
    s_wfb.applied_action = action;
    wfb_glue_stop_traj();
    s_wfb.takeoff_pending = 0u;
    if (action == (uint8_t)WFB_ACT_KILL) {
        wfb_prim_disarmed(&s_wfb.prim); /* no setpoint may fight the motor stop */
    } else if (s_wfb.prim.state == (uint8_t)WFB_PRIM_IDLE) {
        s_wfb.land_now = 1u;            /* RC flight or pilot takeover: the existing LANDING */
    } else if (action == (uint8_t)WFB_ACT_LAND_IN_PLACE) {
        (void)wfb_prim_land_in_place(&s_wfb.prim);
    } else {
        (void)wfb_prim_land(&s_wfb.prim, pin);
    }
}

void wfb_glue_tick(const wfb_glue_in_t *in, wfb_glue_out_t *out)
{
    wfb_prim_in_t pin;
    wfb_prim_out_t pout;
    wfb_safety_in_t sin;
    wfb_traj_point_t pt;
    uint8_t action, st, sampled, gs_flying;
    float dt_s, hb_age_s, traj_t_s;

    memset(out, 0, sizeof(*out));
    memset(&pt, 0, sizeof(pt));
    sampled = 0u;
    traj_t_s = 0.0f;

    dt_s = s_wfb.have_tick ? wfb_glue_age_s(in->now_ms, s_wfb.last_ms) : 0.0f;
    if (dt_s > s_glue_cfg.dt_max_s) {
        dt_s = s_glue_cfg.dt_max_s;
    }
    s_wfb.last_ms = in->now_ms;
    s_wfb.have_tick = 1u;
    s_wfb.last_in = *in;
    wfb_glue_prim_in(&pin, in, dt_s);

    /* Pilot takeover of a GS flight: same stick condition that clears TWC.execute
     * (StabilizerTask.c Update_Des). The GS primitive stops for good; the safety net stays. */
    if (in->rc_override && s_wfb.gs_flight_active && !s_wfb.takeover) {
        wfb_glue_stop_traj();
        wfb_prim_disarmed(&s_wfb.prim);
        s_wfb.takeoff_pending = 0u;
        s_wfb.takeover = 1u;
    }

    /* After a takeover the pilot flies: only the RC-flight checks remain (no heartbeat, no cap). */
    gs_flying = (s_wfb.gs_flight_active && !s_wfb.takeover) ? 1u : 0u;
    hb_age_s = gs_flying ? wfb_glue_age_s(in->now_ms, s_wfb.hb_ref_ms) : 0.0f;
    sin.x_m = in->x_m;
    sin.y_m = in->y_m;
    sin.z_m = in->z_m;
    sin.roll_deg = in->roll_deg;
    sin.pitch_deg = in->pitch_deg;
    sin.vbat_v = in->vbat_v;
    sin.hb_age_s = hb_age_s;
    sin.dt_s = dt_s;
    sin.airborne = in->airborne;
    sin.gs_flight_active = gs_flying;
    action = (uint8_t)wfb_safety_step(&s_wfb.safety, &s_wfb.safety_lim, &sin);
    wfb_glue_apply_action(action, &pin);

    /* Keep the trajectory executor and the sequencer in step. */
    if (s_wfb.traj.state == (uint8_t)WFB_TRAJ_EXECUTING) {
        if (s_wfb.prim.state != (uint8_t)WFB_PRIM_TRAJ) {
            wfb_glue_stop_traj();
        } else {
            traj_t_s = wfb_glue_age_s(in->now_ms, s_wfb.traj_start_ms);
            if (wfb_traj_sample(&s_wfb.traj, traj_t_s, &pt)) {
                sampled = 1u;
            } else {
                (void)wfb_prim_traj_end(&s_wfb.prim, &pin); /* DONE: rate-limited return to hover */
            }
        }
    } else if (s_wfb.prim.state == (uint8_t)WFB_PRIM_TRAJ) {
        (void)wfb_prim_traj_end(&s_wfb.prim, &pin);
    }

    wfb_prim_step(&s_wfb.prim, &s_wfb.prim_cfg, &pin, &pout);
    st = s_wfb.prim.state;

    if (action == (uint8_t)WFB_ACT_KILL) {
        out->motor_stop_req = in->armed ? 1u : 0u;
    } else {
        out->land_req = (s_wfb.land_now || pout.descend) ? 1u : 0u;
        if (!s_wfb.takeover &&
            (st == (uint8_t)WFB_PRIM_CLIMB || st == (uint8_t)WFB_PRIM_HOVER || st == (uint8_t)WFB_PRIM_TRAJ ||
             st == (uint8_t)WFB_PRIM_RETURN || st == (uint8_t)WFB_PRIM_SETTLE)) {
            out->setpoint_valid = 1u;
            if (st == (uint8_t)WFB_PRIM_TRAJ && sampled) {
                out->x_sp_m = pt.x_m;
                out->y_sp_m = pt.y_m;
                out->z_sp_m = pt.z_m;
                out->yaw_sp_deg = pt.yaw_deg;
                out->yaw_valid = 1u;
            } else {
                out->x_sp_m = pout.x_sp_m;
                out->y_sp_m = pout.y_sp_m;
                out->z_sp_m = pout.z_sp_m;
            }
        }
        if (s_wfb.takeoff_pending && st == (uint8_t)WFB_PRIM_CLIMB) {
            out->takeoff_req = 1u;
        }
    }
    s_wfb.takeoff_pending = 0u;

    wfb_glue_mirror(hb_age_s, traj_t_s);
}
