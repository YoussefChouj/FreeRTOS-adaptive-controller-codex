#include "wfb_glue.h"
#include "wfb_traj.h"
#include "wfb_safety.h"
#include "wfb_prim.h"
#include "wfb_prog.h"
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
   @hover_z_min_m  m    [0.1, 2]     lowest SET_HOVER_Z accepted
   @hover_z_max_m  m    [0.3, 3]     highest SET_HOVER_Z accepted
   @dt_max_s       s    [0.005, 0.5] longest tick interval fed to the timers (a stalled loop is not a hold) */
typedef struct {
    float hover_z_min_m;
    float hover_z_max_m;
    float dt_max_s;
} wfb_glue_cfg_t;

#define WFB_GLUE_CFG_ROW(hover_z_min_m, hover_z_max_m, dt_max_s) \
    { (hover_z_min_m), (hover_z_max_m), (dt_max_s) }

static const wfb_glue_cfg_t s_glue_cfg =
    WFB_GLUE_CFG_ROW(0.3f,          1.4f,          0.05f); /* PROPOSED; max = ceiling 1.7 - soft margin 0.3 (2026-10-03 grill) */

/* One buffer for the 0x1B points or the 0x1C program segments (wfb_glue.h): the last BEGIN owns it. */
typedef union {
    wfb_traj_point_t pts[WFB_TRAJ_MAX_POINTS];
    wfb_prog_seg_t seg[WFB_PROG_MAX_SEGS];
} wfb_glue_buf_t;

/* Compile-time check: the program fits in the point buffer, so the union costs no RAM. */
typedef char wfb_glue_prog_fits_t[(sizeof(wfb_prog_seg_t) * WFB_PROG_MAX_SEGS
                                   <= sizeof(wfb_traj_point_t) * WFB_TRAJ_MAX_POINTS) ? 1 : -1];

typedef struct {
    wfb_glue_buf_t buf;
    wfb_traj_t traj;
    wfb_traj_limits_t traj_lim;
    wfb_prog_t prog;
    wfb_prog_caps_t prog_caps;
    wfb_safety_t safety;
    wfb_safety_limits_t safety_lim;
    wfb_prim_t prim;
    wfb_prim_cfg_t prim_cfg;
    wfb_glue_in_t last_in;     /* previous tick's snapshot, used by the command checks */
    uint32_t last_ms;
    uint32_t hb_ref_ms;        /* TAKEOFF accept, then every HEARTBEAT */
    uint32_t traj_start_ms;
    float yaw_hold_deg;        /* heading the setpoints carry: TWC.execute = 1 also drives the yaw loop */
    uint8_t have_tick;
    uint8_t gs_flight_active;  /* TAKEOFF accepted until disarm */
    uint8_t takeoff_pending;   /* one-shot fly-up request for the next tick */
    uint8_t takeover;          /* pilot stick took a GS flight; latched until disarm */
    uint8_t land_now;          /* existing LANDING requested without the hover return (prim IDLE) */
    uint8_t applied_action;    /* highest safety action already applied */
    uint8_t last_err;
    uint8_t prog_mode;         /* 1: the last BEGIN was 0x1C, START/STOP/CLEAR and the tick run the program */
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

/* 1 while the trajectory or the program is being flown. */
static uint8_t wfb_glue_path_running(void)
{
    return (uint8_t)(s_wfb.traj.state == (uint8_t)WFB_TRAJ_EXECUTING
                     || s_wfb.prog.state == (uint8_t)WFB_TRAJ_EXECUTING);
}

static void wfb_glue_stop_traj(void)
{
    if (s_wfb.traj.state == (uint8_t)WFB_TRAJ_EXECUTING) {
        (void)wfb_traj_stop(&s_wfb.traj);
    }
    if (s_wfb.prog.state == (uint8_t)WFB_TRAJ_EXECUTING) {
        (void)wfb_prog_stop(&s_wfb.prog);
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
    case WFB_PRIM_CMD_TAKEOFF:
        if (!s_wfb.have_tick || !in->armed || !in->motors_idle || !in->sbus_live || in->airborne ||
            s_wfb.takeover || s_wfb.safety.action != (uint8_t)WFB_ACT_NONE) {
            return WFB_ERR_STATE;
        }
        err = wfb_prim_takeoff(&s_wfb.prim);
        if (err == WFB_ERR_NONE) {
            s_wfb.gs_flight_active = 1u;
            s_wfb.takeoff_pending = 1u;
            s_wfb.hb_ref_ms = now_ms;
            s_wfb.yaw_hold_deg = in->yaw_deg;
        }
        return err;
    case WFB_PRIM_CMD_LAND:
        return wfb_glue_land();
    case WFB_PRIM_CMD_HEARTBEAT:
        s_wfb.hb_ref_ms = now_ms;
        return WFB_ERR_NONE;
    case WFB_PRIM_CMD_SET_HOVER_Z:
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

    if (idx <= WFB_TRAJ_CMD_COMMIT && !isfinite(val)) {
        return WFB_ERR_RANGE; /* only the payload-carrying commands look at val */
    }
    switch (idx) {
    case WFB_TRAJ_CMD_BEGIN: /* clears the 12 kB buffer here, never in the tick */
        if (s_wfb.prog.state == (uint8_t)WFB_TRAJ_EXECUTING) {
            return WFB_ERR_STATE; /* the points would overwrite the segments being flown */
        }
        err = wfb_traj_begin(&s_wfb.traj, val);
        if (err == WFB_ERR_NONE) {
            (void)wfb_prog_clear(&s_wfb.prog);
            s_wfb.prog_mode = 0u;
        }
        return err;
    case WFB_TRAJ_CMD_APPEND:
        return wfb_traj_append(&s_wfb.traj, val);
    case WFB_TRAJ_CMD_CRC_HI:
        return wfb_traj_crc_hi(&s_wfb.traj, val);
    case WFB_TRAJ_CMD_COMMIT:
        return wfb_traj_commit(&s_wfb.traj, val, &s_wfb.traj_lim, s_wfb.prim_cfg.hover_z_m);
    case WFB_TRAJ_CMD_START:
        if (s_wfb.prim.state != (uint8_t)WFB_PRIM_HOVER) {
            return WFB_ERR_STATE;
        }
        if (s_wfb.prog_mode != 0u) {
            err = wfb_prog_start(&s_wfb.prog, s_wfb.yaw_hold_deg); /* program yaw is relative to this heading */
        } else {
            err = wfb_traj_start(&s_wfb.traj);
        }
        if (err == WFB_ERR_NONE) {
            (void)wfb_prim_traj_begin(&s_wfb.prim);
            s_wfb.traj_start_ms = now_ms;
        }
        return err;
    case WFB_TRAJ_CMD_STOP:
        err = (s_wfb.prog_mode != 0u) ? wfb_prog_stop(&s_wfb.prog) : wfb_traj_stop(&s_wfb.traj);
        if (err == WFB_ERR_NONE && s_wfb.prim.state == (uint8_t)WFB_PRIM_TRAJ) {
            wfb_glue_prim_in(&pin, &s_wfb.last_in, 0.0f);
            (void)wfb_prim_traj_end(&s_wfb.prim, &pin);
        }
        return err;
    case WFB_TRAJ_CMD_CLEAR:
        return (s_wfb.prog_mode != 0u) ? wfb_prog_clear(&s_wfb.prog) : wfb_traj_clear(&s_wfb.traj);
    default:
        return WFB_ERR_RANGE;
    }
}

/* CMD 0x1C: program upload (staging fields, BEGIN, PUSH, CRC_HI, COMMIT, CLEAR). START and STOP stay on 0x1B. */
static wfb_err_t wfb_glue_cmd_prog(uint8_t idx, float val)
{
    wfb_err_t err;

    if (idx == (uint8_t)WFB_PROG_IDX_BEGIN && s_wfb.traj.state == (uint8_t)WFB_TRAJ_EXECUTING) {
        return WFB_ERR_STATE; /* the segments would overwrite the points being flown */
    }
    err = wfb_prog_on_cmd(&s_wfb.prog, idx, val, &s_wfb.traj_lim, &s_wfb.prog_caps, s_wfb.prim_cfg.hover_z_m);
    if (idx == (uint8_t)WFB_PROG_IDX_BEGIN && err == WFB_ERR_NONE) {
        (void)wfb_traj_clear(&s_wfb.traj);
        s_wfb.prog_mode = 1u;
    }
    return err;
}

static void wfb_glue_mirror(float hb_age_s, float traj_t_s)
{
    g_wfb_status.prim_state = (float)s_wfb.prim.state;
    if (s_wfb.prog_mode != 0u) {
        g_wfb_status.traj_state = (float)s_wfb.prog.state;
        g_wfb_status.traj_n = (float)s_wfb.prog.n;
        g_wfb_status.traj_rx = (float)s_wfb.prog.rx;
        g_wfb_status.traj_crc_hi = (float)s_wfb.prog.crc_hi;
        g_wfb_status.traj_crc_lo = (float)(s_wfb.prog.crc_calc & 0xFFFFu);
    } else {
        g_wfb_status.traj_state = (float)s_wfb.traj.state;
        g_wfb_status.traj_n = (float)s_wfb.traj.n;
        g_wfb_status.traj_rx = (float)s_wfb.traj.rx;
        g_wfb_status.traj_crc_hi = (float)s_wfb.traj.crc_hi;
        g_wfb_status.traj_crc_lo = (float)(s_wfb.traj.crc_calc & 0xFFFFu);
    }
    g_wfb_status.prog_mode = (float)s_wfb.prog_mode;
    g_wfb_status.prog_err_seg = (s_wfb.prog.err_seg == 0xFFFFu) ? -1.0f : (float)s_wfb.prog.err_seg;
    g_wfb_status.traj_t = traj_t_s;
    g_wfb_status.last_err = (float)s_wfb.last_err;
    g_wfb_status.safety_trip = (float)s_wfb.safety.trip;
    g_wfb_status.hb_age = hb_age_s;
    g_wfb_status.gs_flight_active = (float)s_wfb.gs_flight_active;
    g_wfb_status.hover_z = s_wfb.prim_cfg.hover_z_m;
    g_wfb_status.airborne_t = s_wfb.safety.airborne_t;
    g_wfb_status.fence_push = (float)s_wfb.safety.push;
}

void wfb_glue_init(void)
{
    memset(&s_wfb, 0, sizeof(s_wfb));
    wfb_traj_init(&s_wfb.traj, s_wfb.buf.pts, (uint16_t)WFB_TRAJ_MAX_POINTS);
    wfb_traj_default_limits(&s_wfb.traj_lim);
    wfb_prog_init(&s_wfb.prog, s_wfb.buf.seg, (uint16_t)WFB_PROG_MAX_SEGS);
    wfb_prog_default_caps(&s_wfb.prog_caps);
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
    } else if (cmd == WFB_CMD_PROG) {
        err = wfb_glue_cmd_prog(idx, val);
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
    err = wfb_glue_cmd_prim(WFB_PRIM_CMD_LAND, 0.0f, now_ms);
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
    uint32_t tick_ms;

    memset(out, 0, sizeof(*out));
    memset(&pt, 0, sizeof(pt));
    sampled = 0u;
    traj_t_s = 0.0f;

    tick_ms = s_wfb.have_tick ? (uint32_t)(in->now_ms - s_wfb.last_ms) : 0u;
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

    /* The safety net belongs to workflow B: it arms with an accepted GS TAKEOFF and stays latched until
     * disarm. A plain RC flight (bench and tuning experiments) has no fence, ceiling, tilt or low-V action:
     * the safety module sees it as on the ground. After a takeover the pilot flies a GS flight: the fence,
     * ceiling, tilt and low-V checks stay, the heartbeat and airborne cap go. */
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
    sin.airborne = s_wfb.gs_flight_active ? in->airborne : 0u;
    sin.gs_flight_active = gs_flying;
    action = (uint8_t)wfb_safety_step(&s_wfb.safety, &s_wfb.safety_lim, &sin);
    wfb_glue_apply_action(action, &pin);

    /* Fence push-back (safety.push, GS flights only): the trajectory clock stops so the path resumes where it
       left off once the drone is back inside. */
    if (s_wfb.safety.push != 0u && wfb_glue_path_running()) {
        s_wfb.traj_start_ms += tick_ms;
    }

    /* Keep the trajectory executor (points or program) and the sequencer in step. */
    if (wfb_glue_path_running()) {
        if (s_wfb.prim.state != (uint8_t)WFB_PRIM_TRAJ) {
            wfb_glue_stop_traj();
        } else {
            traj_t_s = wfb_glue_age_s(in->now_ms, s_wfb.traj_start_ms);
            if ((s_wfb.prog_mode != 0u) ? wfb_prog_sample(&s_wfb.prog, traj_t_s, &pt)
                                        : wfb_traj_sample(&s_wfb.traj, traj_t_s, &pt)) {
                sampled = 1u;
            } else {
                s_wfb.yaw_hold_deg = pt.yaw_deg; /* DONE hands back the end point: hold its heading */
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
                s_wfb.yaw_hold_deg = pt.yaw_deg;
            } else {
                out->x_sp_m = pout.x_sp_m;
                out->y_sp_m = pout.y_sp_m;
                out->z_sp_m = pout.z_sp_m;
            }
            out->yaw_sp_deg = s_wfb.yaw_hold_deg;
            if (s_wfb.safety.push != 0u) {
                wfb_safety_push_sp(&s_wfb.safety, &s_wfb.safety_lim, &sin,
                                   &out->x_sp_m, &out->y_sp_m, &out->z_sp_m);
            }
        }
        if (s_wfb.takeoff_pending && st == (uint8_t)WFB_PRIM_CLIMB) {
            out->takeoff_req = 1u;
        }
    }
    s_wfb.takeoff_pending = 0u;

    wfb_glue_mirror(hb_age_s, traj_t_s);
}
