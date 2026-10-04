/* Differential trace harness for the ground-station command path of TASK/send_data.c (WP-37).
 *
 * Same method as stab_trace.c: the file under test is #included (SEND_SRC), API/pid.c is linked (Ctrler and the
 * WP-28 gain lease are real), every other function the command path calls is a stub that mixes its id and
 * arguments into one FNV-1a hash. Each tick the harness randomizes the vehicle context (fly mode, arm state,
 * flight phase, FSM state, throttle, height) and queues 0..3 random commands (ids, indexes and values chosen
 * around every bound in the handlers, transaction ids drawn from a small set so duplicates happen), runs
 * Process_GroundStation_Command(), and mixes every piece of state the handlers write. Functions the telemetry
 * half of send_data.c needs but this harness never runs are linked as empty dummies by tools/fw_trace.py.
 */
#include SEND_SRC
#include "controller.h"   /* g_ctrl_* (CMD 0x1F, WP-38); API/controller.c is linked */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>

static unsigned long long g_h = 1469598103934665603ULL;
static unsigned long g_calls;
static void mix(const void *p, size_t n)
{
    const unsigned char *b = (const unsigned char *)p;
    size_t i;
    for (i = 0; i < n; i++) {
        g_h ^= b[i];
        g_h *= 1099511628211ULL;
    }
}
#define MIXV(x) mix((const void *)&(x), sizeof(x))
static void call(int id) { g_calls++; mix(&id, sizeof id); }

static unsigned g_in = 1u, g_st = 7u;
static unsigned xs(unsigned *s) { *s ^= *s << 13; *s ^= *s >> 17; *s ^= *s << 5; return *s; }
static float fr(unsigned *s, float lo, float hi) { return lo + (hi - lo) * (float)(xs(s) >> 8) / 16777216.0f; }
static int pm(unsigned *s, unsigned per_mille) { return (xs(s) % 1000u) < per_mille; }

/* ---- data the command path reads or writes, defined elsewhere in the firmware ---- */
#define DEF(x) __typeof__(x) x
DEF(gs_cmd_queue); DEF(gs_cmd_head); DEF(gs_cmd_tail); DEF(TWC); DEF(sinusoid_path); DEF(circle_path);
DEF(figure8_path); DEF(GS_KeySDKflag); DEF(DroneStatus); DEF(flight_phase); DEF(mrac_config_pitch);
DEF(mrac_config_roll); DEF(mrac_config_yaw); DEF(mrac_config_z); DEF(mrac_flags); DEF(mrac_state);
DEF(mrac_simplex); DEF(bench_mode_active); DEF(gs_throttle_min_pct); DEF(gs_throttle_max_pct);
DEF(gs_max_horizontal_speed_mps); DEF(gs_max_vertical_speed_mps); DEF(gs_max_pitch_deg); DEF(gs_max_roll_deg);
DEF(waypoint_spacing); DEF(ano_of); DEF(g_of_bias_capture_req); DEF(g_of_bias_mode); DEF(g_of_handheld_test);
DEF(g_of_full_tilt); DEF(g_ekf_of_vel_fb); DEF(g_of_bias_ema_freeze); DEF(g_of_bias_ema_tau_s);
DEF(s_cal_trim); DEF(s_cal_hot); DEF(g_cal_health); DEF(g_estimator_ready); DEF(g_motor_idle_enabled);
DEF(motor_test_active); DEF(motor_test_watchdog); DEF(motor_test_id); DEF(motor_test_ccr); DEF(g_wfb_status);
float Cos_Yaw = 1.0f, Sin_Yaw = 0.0f;   /* API/SINS.c, read by API/pid.c */

static FlightState_t g_fsm = FLIGHT_STATE_DISARMED;
static float g_thr = -1.0f;
static unsigned long g_tick;

/* ---- stubs of everything the command path calls outside API/pid.c ---- */
TickType_t xTaskGetTickCount(void) { call(1); return (TickType_t)(g_tick * 5u); }
void vPortEnterCritical(void) { call(2); }
void vPortExitCritical(void) { call(3); }
uint8_t PlatformCommand_BuildResult(uint16_t transaction_id, uint8_t outcome, uint8_t command_id, uint8_t index,
                                    uint8_t reason, const char* detail, uint8_t* out, uint16_t out_cap,
                                    uint16_t* out_len)
{
    uint16_t n = (uint16_t)(8u + xs(&g_st) % 9u), i;
    call(4); MIXV(transaction_id); MIXV(outcome); MIXV(command_id); MIXV(index); MIXV(reason); MIXV(out_cap);
    mix(detail, strlen(detail));
    if (pm(&g_st, 50)) return 0U;
    for (i = 0; i < n; i++) out[i] = (uint8_t)xs(&g_st);
    *out_len = n;
    return 1U;
}
uint8_t Usart3_Stream_TxSend(const uint8_t* buf, uint16_t len) { call(5); MIXV(len); mix(buf, len); return 1U; }
void Uart5_Subscribe_TxSend(const uint8_t* buf, uint16_t len) { call(6); MIXV(len); mix(buf, len); }
void RCInput_SetAuthority(uint8_t has_authority) { call(7); MIXV(has_authority); }
void RCInput_SetVirtualStick(RC_Axis_t axis, float val) { call(8); MIXV(axis); MIXV(val); }
float RCInput_Get(RC_Axis_t axis) { call(9); MIXV(axis); return g_thr; }
void FlightFSM_Event(FlightEvent_t event) { call(10); MIXV(event); if (event == FLIGHT_EVENT_ARM_REQUEST && pm(&g_st, 700)) g_fsm = FLIGHT_STATE_ARMED; }
FlightState_t FlightFSM_GetState(void) { call(11); return g_fsm; }
uint8_t wfb_glue_on_cmd(uint8_t cmd, uint8_t idx, float val, uint32_t now_ms)
{
    call(12); MIXV(cmd); MIXV(idx); MIXV(val); MIXV(now_ms);
    if (pm(&g_st, 300)) { g_wfb_status.last_err = (float)(xs(&g_st) % 9u); return WFB_RESULT_REJECTED; }
    return pm(&g_st, 500) ? WFB_RESULT_ACK : WFB_RESULT_APPLIED;
}
void AutoflyTask_StartSinusoid(void) { call(13); sinusoid_path.active = 1U; }
void AutoflyTask_StartCircle(void) { call(14); circle_path.active = 1U; }
void AutoflyTask_StartFigure8(void) { call(15); figure8_path.active = 1U; }
void AutoflyTask_WaypointReset(void) { call(16); }
void SetTelemetryMode(uint8_t mode) { call(17); MIXV(mode); }
uint8_t MRAC_VariantParamSet(uint8_t axis, uint8_t field, float val) { call(18); MIXV(axis); MIXV(field); MIXV(val); return (uint8_t)(xs(&g_st) & 1u); }
uint8_t SysID_Start(SysID_Axis_e axis, SysID_Signal_e sig, float f0, float f1, float amp, float duration)
{
    call(19); MIXV(axis); MIXV(sig); MIXV(f0); MIXV(f1); MIXV(amp); MIXV(duration);
    return (uint8_t)(xs(&g_st) & 1u);
}
void SysID_Abort(void) { call(20); }
void SysID_SetGeofence(uint8_t enabled) { call(21); MIXV(enabled); }
void GyroFilter_SetEnabled(uint8_t on) { call(22); MIXV(on); }
void GyroFilter_SetCutoff(GyroFiltAxis_e axis, float fc_hz) { call(23); MIXV(axis); MIXV(fc_hz); }
void Reset_World_Origin(void) { call(24); }
void Ekf9_Init(Ekf9_t *e, uint8_t active) { call(25); MIXV(active); memset(e, 0, sizeof *e); }
void Ekf9_SetBiasFrozen(Ekf9_t *e, uint8_t frozen) { call(26); MIXV(frozen); (void)e; }

/* ---- scripted commands ---- */
static const uint8_t IDS[] = { 0x00, 0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07, 0x08, 0x09, 0x0A, 0x0B, 0x0C, 0x0D,
                               0x0E, 0x0F, 0x10, 0x11, 0x12, 0x13, 0x14, 0x15, 0x16, 0x17, 0x18, 0x19, 0x1A, 0x1B,
                               0x1C, 0x1D, 0x1E, 0x1F, 0x20, 0x23, 0x25, 0x28, 0x2B, 0x2C, 0x30, 0xFF };
static const float VALS[] = { -500.0f, -1.5f, -1.0f, -0.0f, 0.0f, 0.04f, 0.05f, 0.06f, 0.3f, 0.49f, 0.5f, 0.51f, 0.99f,
                              1.0f, 1.01f, 1.49f, 1.5f, 2.0f, 2.49f, 2.5f, 2.9f, 3.0f, 9.99f, 10.0f, 19.9f, 20.0f,
                              59.9f, 60.0f, 60.1f, 199.0f, 200.0f, 200.5f, 299.0f, 300.0f, 301.0f, 1999.0f, 2000.0f,
                              3000.4f, 4000.0f, 4001.0f, 65535.0f, 65536.0f };

static float pick_val(void)
{
    unsigned r = xs(&g_in) % 100u;
    if (r < 60u) return VALS[xs(&g_in) % (sizeof VALS / sizeof VALS[0])];
    if (r < 62u) return (r & 1u) ? INFINITY : -INFINITY;
    if (r < 63u) return NAN;
    return fr(&g_in, -100.0f, 500.0f);
}

static void queue_one(void)
{
    volatile GS_Cmd_t *q = &gs_cmd_queue[gs_cmd_head];
    uint8_t id = IDS[xs(&g_in) % (sizeof IDS / sizeof IDS[0])];
    unsigned r = xs(&g_in) % 100u;
    q->id = id;
    q->index = (uint8_t)(r < 60u ? xs(&g_in) % 10u : (r < 70u ? 99u + xs(&g_in) % 5u : (r < 80u ? xs(&g_in) % 24u : xs(&g_in) & 0xFFu)));
    q->value = pick_val();
    q->transaction_id = (uint16_t)(pm(&g_in, 500) ? 0u : 1u + xs(&g_in) % 24u);
    q->transaction_flags = (uint8_t)xs(&g_in);
    q->transaction_transport = (uint8_t)(xs(&g_in) & 1u);
    gs_cmd_head = (uint8_t)((gs_cmd_head + 1u) % 16u);
}

static void script_context(void)
{
    if (pm(&g_in, 50)) DroneStatus.FlyMode = (uint8_t)(pm(&g_in, 600) ? FlyMode_SDK : 0);
    if (pm(&g_in, 50)) DroneStatus.ARM_Status = (uint8_t)(pm(&g_in, 500) ? Armed : DisArmed);
    if (pm(&g_in, 50)) flight_phase = (FlightPhase_t)(xs(&g_in) % 4u);
    if (pm(&g_in, 50)) g_fsm = (FlightState_t)(xs(&g_in) % 3u);
    if (pm(&g_in, 50)) g_motor_idle_enabled = (uint8_t)(xs(&g_in) & 1u);
    if (pm(&g_in, 50)) g_thr = pm(&g_in, 500) ? -1.0f : fr(&g_in, -1.0f, 1.0f);
    if (pm(&g_in, 50)) s_ekf_inited = (uint8_t)(xs(&g_in) & 1u);
    Ctrler.Z_posPID.FB = fr(&g_in, 0.0f, 1.0f);
}

static void mix_state(void)
{
    MIXV(Ctrler); MIXV(mrac_config_pitch); MIXV(mrac_config_roll); MIXV(mrac_config_yaw); MIXV(mrac_config_z);
    MIXV(mrac_flags); MIXV(mrac_state); MIXV(mrac_simplex); MIXV(TWC); MIXV(sinusoid_path); MIXV(circle_path);
    MIXV(figure8_path); MIXV(gs_throttle_min_pct); MIXV(gs_throttle_max_pct); MIXV(gs_max_horizontal_speed_mps);
    MIXV(gs_max_vertical_speed_mps); MIXV(gs_max_pitch_deg); MIXV(gs_max_roll_deg); MIXV(bench_mode_active);
    MIXV(motor_test_active); MIXV(motor_test_watchdog); MIXV(motor_test_id); MIXV(motor_test_ccr);
    MIXV(g_of_bias_capture_req); MIXV(g_of_bias_mode); MIXV(g_of_handheld_test); MIXV(g_of_full_tilt);
    MIXV(g_ekf_of_vel_fb); MIXV(g_of_bias_ema_freeze); MIXV(g_of_bias_ema_tau_s); MIXV(s_cal_trim); MIXV(s_cal_hot);
    MIXV(g_cal_health); MIXV(g_estimator_ready); MIXV(g_ekf_gate); MIXV(s_ekf); MIXV(s_transaction_history);
    MIXV(g_ctrl_select_req); MIXV(g_ctrl_axis_mask);
    MIXV(s_transaction_history_head); MIXV(s_transaction_result_buf); MIXV(GS_KeySDKflag); MIXV(flight_phase);
    MIXV(g_motor_idle_enabled); MIXV(waypoint_spacing); MIXV(ano_of); MIXV(gs_cmd_tail); MIXV(g_fsm);
}

int main(int argc, char **argv)
{
    unsigned long ticks = argc > 1 ? strtoul(argv[1], 0, 10) : 100000ul;
    unsigned seed = argc > 2 ? (unsigned)strtoul(argv[2], 0, 10) : 1u;
    unsigned long every = argc > 3 ? strtoul(argv[3], 0, 10) : 1000ul;
    int a, e;
    MRAC_AxisConfig_t *cfg[4] = { &mrac_config_pitch, &mrac_config_roll, &mrac_config_yaw, &mrac_config_z };
    g_in = 2463534242u ^ (seed * 2654435761u);
    g_st = 88675123u ^ (seed * 40503u);
    for (a = 0; a < 4; a++) {
        for (e = 0; e < MRAC_N_FEATURES; e++) {
            cfg[a]->What_limit[e] = 2.0f;
            cfg[a]->What_tol[e] = 0.5f;
        }
    }
    for (g_tick = 1; g_tick <= ticks; g_tick++) {
        int n = (int)(xs(&g_in) % 4u);
        script_context();
        while (n-- > 0) queue_one();
        Process_GroundStation_Command();
        mix_state();
        if (g_tick % every == 0u || g_tick == ticks) printf("tick %lu hash %016llx calls %lu\n", g_tick, g_h, g_calls);
    }
    return 0;
}
