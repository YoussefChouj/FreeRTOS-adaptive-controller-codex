/* Host test for API/wfb_glue.c, the Workflow B integration glue (task F4).
 *
 * Build and run from the repo root:
 *   gcc -std=c99 -Wall -Wextra -Werror -Wdeclaration-after-statement -Wvla -I API \
 *       tests/firmware_host/test_wfb_glue.c API/wfb_glue.c API/wfb_traj.c \
 *       API/wfb_safety.c API/wfb_prim.c -lm -o test_wfb_glue && ./test_wfb_glue
 *
 * The glue is a singleton, so every test starts with wfb_glue_init(). The vehicle is modelled
 * as ideal: after each tick it sits exactly on the setpoint it was given (follow()). */

#include <math.h>
#include <stdio.h>
#include <string.h>

#include "wfb_glue.h"
#include "wfb_prim.h"
#include "wfb_safety.h"
#include "wfb_traj.h"

#define TICK_MS         5u    /* the 200 Hz stabilizer loop */
#define HEARTBEAT_MS    500u  /* the GS heartbeat period */
#define HOVER_Z_DEFAULT 0.5f  /* WFB_PRIM_CFG_ROW hover_z_m */

/* ---- check harness ------------------------------------------------------------------ */

static int s_checks;
static int s_failures;
static const char *s_test;

static void check(int ok, const char *what, int line)
{
    s_checks++;
    if (!ok) {
        s_failures++;
        printf("FAIL %s (line %d): %s\n", s_test, line, what);
    }
}

#define CHECK(cond)           check((cond) != 0, #cond, __LINE__)
/* Compare floats that are not exact binary values through CHECK_NEAR: 32-bit x87 gcc evaluates a
 * constant like 0.8f in long double (FLT_EVAL_METHOD 2), so == against a stored float fails. */
#define CHECK_NEAR(a, b, tol) check(fabsf((a) - (b)) <= (tol), #a " ~= " #b, __LINE__)
#define RUN(test)             do { s_test = #test; test(); } while (0)

/* ---- helpers ------------------------------------------------------------------------ */

static int prim_state(void) { return (int)g_wfb_status.prim_state; }
static int traj_state(void) { return (int)g_wfb_status.traj_state; }
static int last_err(void)   { return (int)g_wfb_status.last_err; }

static uint8_t prim_cmd(uint8_t idx, float val, uint32_t now_ms)
{
    return wfb_glue_on_cmd(WFB_CMD_PRIM, idx, val, now_ms);
}

/* Upload and trajectory control ignore the command time. */
static uint8_t traj_cmd(uint8_t idx, float val)
{
    return wfb_glue_on_cmd(WFB_CMD_TRAJ, idx, val, 0u);
}

/* Armed on the ground, motors idling, RC link up: every TAKEOFF precondition met. */
static wfb_glue_in_t ground_ready(void)
{
    wfb_glue_in_t in;

    memset(&in, 0, sizeof(in));
    in.vbat_v = 16.0f;
    in.armed = 1u;
    in.motors_idle = 1u;
    in.sbus_live = 1u;
    return in;
}

/* The ideal vehicle: wherever the last setpoint put it; the fly-up path makes it airborne. */
static void follow(wfb_glue_in_t *in, const wfb_glue_out_t *out)
{
    if (out->takeoff_req) {
        in->airborne = 1u;
    }
    if (out->setpoint_valid) {
        in->x_m = out->x_sp_m;
        in->y_m = out->y_sp_m;
        in->z_m = out->z_sp_m;
    }
}

/* One 200 Hz tick. With heartbeat set, the GS heartbeat arrives on its 0.5 s schedule. */
static void step(wfb_glue_in_t *in, wfb_glue_out_t *out, int heartbeat)
{
    in->now_ms += TICK_MS;
    if (heartbeat && (in->now_ms % HEARTBEAT_MS) == 0u) {
        (void)prim_cmd(WFB_PRIM_CMD_HEARTBEAT, 0.0f, in->now_ms);
    }
    wfb_glue_tick(in, out);
    follow(in, out);
}

static void run_ticks(wfb_glue_in_t *in, wfb_glue_out_t *out, int ticks, int heartbeat)
{
    int i;

    for (i = 0; i < ticks; i++) {
        step(in, out, heartbeat);
    }
}

/* Fresh glue, armed on the ground, TAKEOFF, ideal climb until the sequencer reports HOVER. */
static void fly_to_hover(wfb_glue_in_t *in, wfb_glue_out_t *out)
{
    int i;

    wfb_glue_init();
    *in = ground_ready();
    wfb_glue_tick(in, out); /* TAKEOFF needs one snapshot */
    CHECK(prim_cmd(WFB_PRIM_CMD_TAKEOFF, 0.0f, in->now_ms) == WFB_RESULT_APPLIED);
    for (i = 0; i < 2000 && prim_state() != WFB_PRIM_HOVER; i++) {
        step(in, out, 1);
    }
    CHECK(prim_state() == WFB_PRIM_HOVER);
}

/* Ticks until the sequencer asks the firmware to land (or max_ticks pass); 1 if it did. */
static int run_until_land_req(wfb_glue_in_t *in, wfb_glue_out_t *out, int max_ticks, int heartbeat)
{
    int i;

    for (i = 0; i < max_ticks; i++) {
        step(in, out, heartbeat);
        if (out->land_req) {
            return 1;
        }
    }
    return 0;
}

/* A 2 s out-and-back along x at the default hover height, yaw turning 0 -> 20 -> 40 deg.
 * Starts and ends on the hover point, as COMMIT requires. */
#define TRIP_N 3u
static const wfb_traj_point_t k_trip[TRIP_N] = {
    /* x_m   y_m   z_m   yaw_deg t_s */
    { 0.0f, 0.0f, 0.5f,  0.0f, 0.0f },
    { 0.3f, 0.0f, 0.5f, 20.0f, 1.0f },
    { 0.0f, 0.0f, 0.5f, 40.0f, 2.0f },
};

/* The GS CRCs the raw 20-byte point records; crc_flip != 0 corrupts the checksum it sends. */
static uint32_t trip_crc(uint32_t crc_flip)
{
    return wfb_crc32((const uint8_t *)k_trip, (uint32_t)sizeof(k_trip)) ^ crc_flip;
}

/* BEGIN, five APPENDs per point in record order, CRC_HI, COMMIT. Returns the COMMIT result. */
static uint8_t upload_trip(uint32_t crc_flip)
{
    uint32_t crc = trip_crc(crc_flip);
    uint16_t i;

    CHECK(traj_cmd(WFB_TRAJ_CMD_BEGIN, (float)TRIP_N) == WFB_RESULT_APPLIED);
    for (i = 0u; i < TRIP_N; i++) {
        CHECK(traj_cmd(WFB_TRAJ_CMD_APPEND, k_trip[i].x_m) == WFB_RESULT_APPLIED);
        CHECK(traj_cmd(WFB_TRAJ_CMD_APPEND, k_trip[i].y_m) == WFB_RESULT_APPLIED);
        CHECK(traj_cmd(WFB_TRAJ_CMD_APPEND, k_trip[i].z_m) == WFB_RESULT_APPLIED);
        CHECK(traj_cmd(WFB_TRAJ_CMD_APPEND, k_trip[i].yaw_deg) == WFB_RESULT_APPLIED);
        CHECK(traj_cmd(WFB_TRAJ_CMD_APPEND, k_trip[i].t_s) == WFB_RESULT_APPLIED);
    }
    CHECK(traj_cmd(WFB_TRAJ_CMD_CRC_HI, (float)(crc >> 16)) == WFB_RESULT_APPLIED);
    return traj_cmd(WFB_TRAJ_CMD_COMMIT, (float)(crc & 0xFFFFu));
}

/* The same trajectory loaded straight into the module: the oracle for the glue's setpoints. */
static void load_trip_oracle(wfb_traj_t *tr, wfb_traj_point_t *buf)
{
    wfb_traj_limits_t lim;
    uint32_t crc = trip_crc(0u);
    uint16_t i;

    wfb_traj_default_limits(&lim);
    wfb_traj_init(tr, buf, (uint16_t)TRIP_N);
    CHECK(wfb_traj_begin(tr, (float)TRIP_N) == WFB_ERR_NONE);
    for (i = 0u; i < TRIP_N; i++) {
        (void)wfb_traj_append(tr, k_trip[i].x_m);
        (void)wfb_traj_append(tr, k_trip[i].y_m);
        (void)wfb_traj_append(tr, k_trip[i].z_m);
        (void)wfb_traj_append(tr, k_trip[i].yaw_deg);
        (void)wfb_traj_append(tr, k_trip[i].t_s);
    }
    (void)wfb_traj_crc_hi(tr, (float)(crc >> 16));
    CHECK(wfb_traj_commit(tr, (float)(crc & 0xFFFFu), &lim, HOVER_Z_DEFAULT) == WFB_ERR_NONE);
    CHECK(wfb_traj_start(tr) == WFB_ERR_NONE);
}

static int same_out(const wfb_glue_out_t *a, const wfb_glue_out_t *b)
{
    return a->setpoint_valid == b->setpoint_valid && a->x_sp_m == b->x_sp_m && a->y_sp_m == b->y_sp_m &&
           a->z_sp_m == b->z_sp_m && a->yaw_sp_deg == b->yaw_sp_deg && a->takeoff_req == b->takeoff_req &&
           a->land_req == b->land_req && a->motor_stop_req == b->motor_stop_req;
}

/* ---- tests -------------------------------------------------------------------------- */

/* After init nothing is flying, and a ground tick asks the firmware for nothing. */
static void test_boot_state(void)
{
    wfb_glue_in_t in = ground_ready();
    wfb_glue_out_t out;

    wfb_glue_init();
    CHECK(prim_state() == WFB_PRIM_IDLE);
    CHECK(traj_state() == WFB_TRAJ_EMPTY);
    CHECK(g_wfb_status.gs_flight_active == 0.0f);
    CHECK(g_wfb_status.safety_trip == 0.0f);
    CHECK(g_wfb_status.hover_z == HOVER_Z_DEFAULT);

    wfb_glue_tick(&in, &out);
    CHECK(!out.setpoint_valid && !out.takeoff_req && !out.land_req && !out.motor_stop_req);
}

/* TAKEOFF needs a snapshot and an armed, idling, RC-linked vehicle on the ground. */
static void test_takeoff_preconditions(void)
{
    wfb_glue_in_t in;
    wfb_glue_out_t out;

    wfb_glue_init();
    CHECK(prim_cmd(WFB_PRIM_CMD_TAKEOFF, 0.0f, 0u) == WFB_RESULT_REJECTED); /* no tick yet */
    CHECK(last_err() == WFB_ERR_STATE);

    in = ground_ready(); in.armed = 0u;       wfb_glue_tick(&in, &out);
    CHECK(prim_cmd(WFB_PRIM_CMD_TAKEOFF, 0.0f, 0u) == WFB_RESULT_REJECTED);
    in = ground_ready(); in.motors_idle = 0u; wfb_glue_tick(&in, &out);
    CHECK(prim_cmd(WFB_PRIM_CMD_TAKEOFF, 0.0f, 0u) == WFB_RESULT_REJECTED);
    in = ground_ready(); in.sbus_live = 0u;   wfb_glue_tick(&in, &out);
    CHECK(prim_cmd(WFB_PRIM_CMD_TAKEOFF, 0.0f, 0u) == WFB_RESULT_REJECTED);
    in = ground_ready(); in.airborne = 1u;    wfb_glue_tick(&in, &out);
    CHECK(prim_cmd(WFB_PRIM_CMD_TAKEOFF, 0.0f, 0u) == WFB_RESULT_REJECTED);
    CHECK(last_err() == WFB_ERR_STATE);
    CHECK(g_wfb_status.gs_flight_active == 0.0f);

    in = ground_ready(); wfb_glue_tick(&in, &out);
    CHECK(prim_cmd(WFB_PRIM_CMD_TAKEOFF, 0.0f, 0u) == WFB_RESULT_APPLIED);
    CHECK(last_err() == WFB_ERR_NONE);
    CHECK(g_wfb_status.gs_flight_active == 1.0f);
    CHECK(prim_state() == WFB_PRIM_CLIMB);
}

/* The fly-up request fires once; the CLIMB setpoint drives TWC from then on. */
static void test_takeoff_request_is_one_shot(void)
{
    wfb_glue_in_t in = ground_ready();
    wfb_glue_out_t out;

    wfb_glue_init();
    wfb_glue_tick(&in, &out);
    CHECK(prim_cmd(WFB_PRIM_CMD_TAKEOFF, 0.0f, in.now_ms) == WFB_RESULT_APPLIED);

    step(&in, &out, 1);
    CHECK(out.takeoff_req == 1u);
    step(&in, &out, 1);
    CHECK(out.takeoff_req == 0u);
    CHECK(out.setpoint_valid == 1u);
    CHECK_NEAR(out.z_sp_m, HOVER_Z_DEFAULT, 1e-6f);
}

/* SET_HOVER_Z accepts [0.3, 1.2] m, only while IDLE. */
static void test_set_hover_z(void)
{
    wfb_glue_in_t in = ground_ready();
    wfb_glue_out_t out;

    wfb_glue_init();
    CHECK(prim_cmd(WFB_PRIM_CMD_SET_HOVER_Z, 0.29f, 0u) == WFB_RESULT_REJECTED);
    CHECK(last_err() == WFB_ERR_RANGE);
    CHECK(prim_cmd(WFB_PRIM_CMD_SET_HOVER_Z, 1.41f, 0u) == WFB_RESULT_REJECTED);
    CHECK(prim_cmd(WFB_PRIM_CMD_SET_HOVER_Z, NAN, 0u) == WFB_RESULT_REJECTED);
    CHECK(last_err() == WFB_ERR_RANGE);
    CHECK(prim_cmd(WFB_PRIM_CMD_SET_HOVER_Z, 0.8f, 0u) == WFB_RESULT_APPLIED);
    CHECK_NEAR(g_wfb_status.hover_z, 0.8f, 1e-6f);

    wfb_glue_tick(&in, &out);
    CHECK(prim_cmd(WFB_PRIM_CMD_TAKEOFF, 0.0f, in.now_ms) == WFB_RESULT_APPLIED);
    CHECK(prim_cmd(WFB_PRIM_CMD_SET_HOVER_Z, 0.6f, in.now_ms) == WFB_RESULT_REJECTED);
    CHECK(last_err() == WFB_ERR_STATE);
    CHECK_NEAR(g_wfb_status.hover_z, 0.8f, 1e-6f);
}

/* START only from HOVER; while executing, the setpoints are the module's own samples;
 * at the end the sequencer leaves TRAJ. */
static void test_trajectory_executes_from_hover(void)
{
    wfb_glue_in_t in;
    wfb_glue_out_t out;
    wfb_traj_t oracle;
    wfb_traj_point_t oracle_buf[TRIP_N];
    wfb_traj_point_t want;
    int i;

    wfb_glue_init();
    CHECK(upload_trip(0u) == WFB_RESULT_APPLIED);
    CHECK(traj_state() == WFB_TRAJ_READY);
    CHECK(traj_cmd(WFB_TRAJ_CMD_START, 0.0f) == WFB_RESULT_REJECTED); /* still IDLE */
    CHECK(last_err() == WFB_ERR_STATE);

    fly_to_hover(&in, &out); /* re-inits the glue: upload again */
    CHECK(upload_trip(0u) == WFB_RESULT_APPLIED);
    CHECK(wfb_glue_on_cmd(WFB_CMD_TRAJ, WFB_TRAJ_CMD_START, 0.0f, in.now_ms) == WFB_RESULT_APPLIED);
    load_trip_oracle(&oracle, oracle_buf);

    for (i = 0; i < 399; i++) { /* 2 s of the 2 s trajectory, the last tick excluded */
        step(&in, &out, 1);
        CHECK(traj_state() == WFB_TRAJ_EXECUTING);
        (void)wfb_traj_sample(&oracle, g_wfb_status.traj_t, &want);
        CHECK(out.setpoint_valid == 1u);
        CHECK_NEAR(out.x_sp_m, want.x_m, 1e-4f);
        CHECK_NEAR(out.y_sp_m, want.y_m, 1e-4f);
        CHECK_NEAR(out.z_sp_m, want.z_m, 1e-4f);
        CHECK_NEAR(out.yaw_sp_deg, want.yaw_deg, 1e-4f);
    }
    run_ticks(&in, &out, 2, 1);
    CHECK(traj_state() == WFB_TRAJ_DONE);
    CHECK(prim_state() != WFB_PRIM_TRAJ);
}

/* The heading setpoint never jumps: TAKEOFF captures the current heading, a trajectory steers
 * it, and afterwards the last trajectory yaw is held (TWC.execute = 1 also drives the yaw loop). */
static void test_yaw_is_held_between_phases(void)
{
    wfb_glue_in_t in = ground_ready();
    wfb_glue_out_t out;
    int i;

    wfb_glue_init();
    in.yaw_deg = 30.0f;
    wfb_glue_tick(&in, &out);
    CHECK(prim_cmd(WFB_PRIM_CMD_TAKEOFF, 0.0f, in.now_ms) == WFB_RESULT_APPLIED);
    in.yaw_deg = 35.0f; /* drift after TAKEOFF does not move the setpoint */
    for (i = 0; i < 2000 && prim_state() != WFB_PRIM_HOVER; i++) {
        step(&in, &out, 1);
        if (out.setpoint_valid) {
            CHECK(out.yaw_sp_deg == 30.0f);
        }
    }
    CHECK(prim_state() == WFB_PRIM_HOVER);

    CHECK(upload_trip(0u) == WFB_RESULT_APPLIED);
    CHECK(wfb_glue_on_cmd(WFB_CMD_TRAJ, WFB_TRAJ_CMD_START, 0.0f, in.now_ms) == WFB_RESULT_APPLIED);
    run_ticks(&in, &out, 200, 1); /* t = 1 s: the middle point */
    CHECK_NEAR(out.yaw_sp_deg, 20.0f, 0.1f);

    run_ticks(&in, &out, 260, 1); /* past the end, back in the sequencer */
    CHECK(prim_state() != WFB_PRIM_TRAJ);
    CHECK(out.setpoint_valid == 1u);
    CHECK(out.yaw_sp_deg == 40.0f);
}

/* A corrupted checksum is refused at COMMIT; a clean re-upload then commits. */
static void test_bad_crc_rejected_then_reupload(void)
{
    wfb_glue_init();
    CHECK(upload_trip(0x1u) == WFB_RESULT_REJECTED);
    CHECK(last_err() == WFB_ERR_CRC);
    CHECK(traj_state() != WFB_TRAJ_READY);

    CHECK(upload_trip(0u) == WFB_RESULT_APPLIED);
    CHECK(traj_state() == WFB_TRAJ_READY);
}

/* STOP ends the trajectory and returns to the hover point; CLEAR is refused mid-execution. */
static void test_stop_and_clear_while_executing(void)
{
    wfb_glue_in_t in;
    wfb_glue_out_t out;

    fly_to_hover(&in, &out);
    CHECK(upload_trip(0u) == WFB_RESULT_APPLIED);
    CHECK(wfb_glue_on_cmd(WFB_CMD_TRAJ, WFB_TRAJ_CMD_START, 0.0f, in.now_ms) == WFB_RESULT_APPLIED);
    run_ticks(&in, &out, 100, 1);

    CHECK(traj_cmd(WFB_TRAJ_CMD_CLEAR, 0.0f) == WFB_RESULT_REJECTED);
    CHECK(last_err() == WFB_ERR_STATE);
    CHECK(traj_cmd(WFB_TRAJ_CMD_STOP, 0.0f) == WFB_RESULT_APPLIED);
    CHECK(traj_state() == WFB_TRAJ_READY);
    CHECK(prim_state() == WFB_PRIM_RETURN);
}

/* RC ch5 during a GS flight is the GS LAND: same outputs tick for tick, ending in land_req
 * with no setpoint while descending. */
#define LAND_TICKS 400
static void test_rc_land_matches_gs_land(void)
{
    static wfb_glue_out_t by_gs[LAND_TICKS];
    wfb_glue_in_t in;
    wfb_glue_out_t out;
    int i, identical = 1;

    fly_to_hover(&in, &out);
    CHECK(prim_cmd(WFB_PRIM_CMD_LAND, 0.0f, in.now_ms) == WFB_RESULT_APPLIED);
    for (i = 0; i < LAND_TICKS; i++) {
        step(&in, &out, 1);
        by_gs[i] = out;
    }
    CHECK(out.land_req == 1u);
    CHECK(out.setpoint_valid == 0u);

    fly_to_hover(&in, &out);
    CHECK(wfb_glue_rc_land(in.now_ms) == 1u);
    for (i = 0; i < LAND_TICKS; i++) {
        step(&in, &out, 1);
        identical = identical && same_out(&out, &by_gs[i]);
    }
    CHECK(identical);
}

/* A GS flight that stops heartbeating trips the safety net and lands; disarm clears it. */
static void test_heartbeat_loss_lands(void)
{
    wfb_glue_in_t in;
    wfb_glue_out_t out;

    fly_to_hover(&in, &out);
    CHECK(run_until_land_req(&in, &out, 2000, 0));
    CHECK(g_wfb_status.safety_trip != 0.0f);
    CHECK(g_wfb_status.hb_age > 1.0f);

    wfb_glue_disarmed();
    CHECK(g_wfb_status.safety_trip == 0.0f);
    CHECK(g_wfb_status.gs_flight_active == 0.0f);
    CHECK(prim_state() == WFB_PRIM_IDLE);
}

/* A heartbeat every 0.5 s keeps a long hover untripped. */
static void test_regular_heartbeat_never_trips(void)
{
    wfb_glue_in_t in;
    wfb_glue_out_t out;

    fly_to_hover(&in, &out);
    run_ticks(&in, &out, 1000, 1);
    CHECK(g_wfb_status.safety_trip == 0.0f);
    CHECK(prim_state() == WFB_PRIM_HOVER);
    CHECK(out.land_req == 0u);
}

/* The safety net is workflow B only: a plain RC flight past the fence, the ceiling, the tilt
 * limit and the low-V limit gets no landing and no motor stop, and ch5 stays with today's code. */
static void test_rc_flight_has_no_safety_net(void)
{
    wfb_glue_in_t in = ground_ready();
    wfb_glue_out_t out;

    wfb_glue_init();
    in.airborne = 1u;
    in.x_m = 1.7f;       /* fence_x_m = 1.6 */
    in.z_m = 2.0f;       /* ceiling_m = 1.7 */
    in.roll_deg = 70.0f; /* tilt_deg = 60 for tilt_hold_s = 0.2 */
    in.vbat_v = 13.0f;   /* low_v = 14 for low_v_hold_s = 3 */
    run_ticks(&in, &out, 800, 0);
    CHECK(out.land_req == 0u);
    CHECK(out.motor_stop_req == 0u);
    CHECK(out.setpoint_valid == 0u);
    CHECK(g_wfb_status.safety_trip == 0.0f);
    CHECK(wfb_glue_rc_land(in.now_ms) == 0u);
}

/* A roll/pitch stick during a GS flight hands it to the pilot for good: no more setpoints,
 * no heartbeat check, ch5 back to today's code; the fence still lands. */
static void test_pilot_takeover(void)
{
    wfb_glue_in_t in;
    wfb_glue_out_t out;

    fly_to_hover(&in, &out);
    in.rc_override = 1u;
    step(&in, &out, 1);
    CHECK(out.setpoint_valid == 0u);
    in.rc_override = 0u; /* stick centred again: still the pilot's */
    run_ticks(&in, &out, 600, 0);
    CHECK(out.setpoint_valid == 0u);
    CHECK(out.land_req == 0u);
    CHECK(g_wfb_status.safety_trip == 0.0f);
    CHECK(wfb_glue_rc_land(in.now_ms) == 0u);

    in.airborne = 0u; /* the pilot lands but does not disarm: the GS cannot launch it again */
    step(&in, &out, 0);
    CHECK(prim_cmd(WFB_PRIM_CMD_TAKEOFF, 0.0f, in.now_ms) == WFB_RESULT_REJECTED);
    CHECK(last_err() == WFB_ERR_STATE);

    in.airborne = 1u;
    in.x_m = 1.7f; /* no GS setpoint to push with: lands at once */
    step(&in, &out, 0);
    CHECK(out.land_req == 1u);
}

/* Just outside the fence on a GS flight: the setpoint is pushed to the soft boundary, the
 * trajectory clock stands still, and back inside the push clears with no landing. */
static void test_fence_push_back(void)
{
    wfb_glue_in_t in;
    wfb_glue_out_t out;

    fly_to_hover(&in, &out);
    in.x_m = 1.7f;
    step(&in, &out, 1);
    CHECK(out.land_req == 0u);
    CHECK(out.setpoint_valid == 1u);
    CHECK_NEAR(out.x_sp_m, 1.3f, 1e-6f);
    CHECK(g_wfb_status.fence_push == 1.0f);
    in.x_m = -1.7f;
    in.z_m = 1.8f;
    step(&in, &out, 1);
    CHECK_NEAR(out.x_sp_m, -1.3f, 1e-6f);
    CHECK_NEAR(out.z_sp_m, 1.4f, 1e-6f);
    CHECK(g_wfb_status.fence_push == 5.0f);
    in.x_m = 0.0f;
    in.z_m = 0.8f;
    step(&in, &out, 1);
    CHECK(g_wfb_status.fence_push == 0.0f);
    CHECK(out.land_req == 0u);
    CHECK(g_wfb_status.safety_trip == 0.0f);
}

/* A heartbeat stamped after the tick time (task race) reads as age 0, not a wrapped age. */
static void test_future_heartbeat_clamps_to_zero(void)
{
    wfb_glue_in_t in;
    wfb_glue_out_t out;

    fly_to_hover(&in, &out);
    CHECK(prim_cmd(WFB_PRIM_CMD_HEARTBEAT, 0.0f, in.now_ms + 1000u) == WFB_RESULT_APPLIED);
    step(&in, &out, 0);
    CHECK(g_wfb_status.hb_age == 0.0f);
    CHECK(g_wfb_status.safety_trip == 0.0f);
}

/* Unknown commands and indices, and NaN payloads, are rejected without side effects. */
static void test_malformed_commands_rejected(void)
{
    wfb_glue_init();
    CHECK(prim_cmd(9u, 0.0f, 0u) == WFB_RESULT_REJECTED);
    CHECK(last_err() == WFB_ERR_RANGE);
    CHECK(traj_cmd(9u, 0.0f) == WFB_RESULT_REJECTED);
    CHECK(wfb_glue_on_cmd(0x1Cu, 0u, 0.0f, 0u) == WFB_RESULT_REJECTED);

    CHECK(traj_cmd(WFB_TRAJ_CMD_BEGIN, 2.0f) == WFB_RESULT_APPLIED);
    CHECK(traj_cmd(WFB_TRAJ_CMD_APPEND, NAN) == WFB_RESULT_REJECTED);
    CHECK(last_err() == WFB_ERR_RANGE);
    CHECK(g_wfb_status.traj_rx == 0.0f);
}

/* A sustained 70 deg roll kills the motors, and no setpoint competes with the stop. */
static void test_tilt_kills_motors(void)
{
    wfb_glue_in_t in;
    wfb_glue_out_t out;

    fly_to_hover(&in, &out);
    in.roll_deg = 70.0f; /* tilt_deg = 60 for tilt_hold_s = 0.2 */
    run_ticks(&in, &out, 60, 1);
    CHECK(out.motor_stop_req == 1u);
    CHECK(out.setpoint_valid == 0u);
    CHECK(out.land_req == 0u);
}

int main(void)
{
    RUN(test_boot_state);
    RUN(test_takeoff_preconditions);
    RUN(test_takeoff_request_is_one_shot);
    RUN(test_set_hover_z);
    RUN(test_trajectory_executes_from_hover);
    RUN(test_yaw_is_held_between_phases);
    RUN(test_bad_crc_rejected_then_reupload);
    RUN(test_stop_and_clear_while_executing);
    RUN(test_rc_land_matches_gs_land);
    RUN(test_heartbeat_loss_lands);
    RUN(test_regular_heartbeat_never_trips);
    RUN(test_rc_flight_has_no_safety_net);
    RUN(test_pilot_takeover);
    RUN(test_fence_push_back);
    RUN(test_future_heartbeat_clamps_to_zero);
    RUN(test_malformed_commands_rejected);
    RUN(test_tilt_kills_motors);

    if (s_failures == 0) {
        printf("PASS %d\n", s_checks);
        return 0;
    }
    printf("FAIL: %d of %d checks\n", s_failures, s_checks);
    return 1;
}
