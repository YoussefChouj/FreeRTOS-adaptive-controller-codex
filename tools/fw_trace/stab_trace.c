/* Differential trace harness for TASK/StabilizerTask.c (WP-37). Built and compared by tools/fw_trace.py.
 *
 * The file under test is #included (STAB_SRC), so its file-scope statics are visible here. Everything it calls
 * outside API/pid.c is a stub below that mixes its id and arguments into one running FNV-1a hash and returns
 * scripted values. Each tick the harness randomizes the inputs (sensors, RC, FSM state, GS flags) from a
 * seeded generator, runs stabilizer_Task() (plus Get_Voltage() every 200 ticks, as SystemMonitor_Task does),
 * and mixes the state the file owns into the hash. Two builds that print the same hashes made the same external
 * calls with the same arguments in the same order and left the same state, tick by tick.
 * Float math is SSE (-mfpmath=sse), so x87 excess precision cannot hide or invent a difference.
 */
#include STAB_SRC
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/* ---- the trace hash ------------------------------------------------------------------------------ */
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
#define MIXV(x) mix(&(x), sizeof(x))

static void call(int id)
{
    g_calls++;
    mix(&id, sizeof id);
}
static void callf(int id, float a) { call(id); MIXV(a); }

/* ---- generators: g_in drives the scripted inputs, g_st the stub return values ------------------- */
static unsigned g_in = 1u, g_st = 7u;
static unsigned xs(unsigned *s) { *s ^= *s << 13; *s ^= *s >> 17; *s ^= *s << 5; return *s; }
static float fr(unsigned *s, float lo, float hi) { return lo + (hi - lo) * (float)(xs(s) >> 8) / 16777216.0f; }
static int pm(unsigned *s, unsigned per_mille) { return (xs(s) % 1000u) < per_mille; }

/* ---- firmware globals the file reads or writes, defined elsewhere in the firmware ---------------- */
#define DEF(x) __typeof__(x) x
DEF(ano_of); DEF(bench_mode_active); DEF(DroneStatus); DEF(flight_phase); DEF(g_ekf_gate);
DEF(g_estimator_ready); DEF(g_imu_settle_metric); DEF(g_motor_idle_enabled); DEF(g_wfb_status);
DEF(Gravity_Body_X); DEF(Gravity_Body_Y); DEF(Gravity_Body_Z); DEF(gs_max_horizontal_speed_mps);
DEF(gs_max_vertical_speed_mps); DEF(gs_max_pitch_deg); DEF(gs_max_roll_deg); DEF(gs_throttle_min_pct);
DEF(gs_throttle_max_pct); DEF(Gyro_X_Real); DEF(Gyro_Y_Real); DEF(Gyro_Z_Real); DEF(imu_data);
DEF(Lin_Acc_X_body); DEF(Lin_Acc_Y_body); DEF(Lin_Acc_Z_body); DEF(motor_test_active); DEF(motor_test_ccr);
DEF(motor_test_id); DEF(motor_test_watchdog); DEF(mrac_in_armed); DEF(mrac_in_phase); DEF(mymotor);
DEF(sbus_channel); DEF(sbus_flyup_trigger); DEF(sbus_lost); DEF(sbus_path_trigger); DEF(SDK_DelayWakeFlag);
DEF(TWC_arrived);
float Cos_Yaw = 1.0f, Sin_Yaw = 0.0f;   /* API/SINS.c, read by API/pid.c */

/* ---- scripted state behind the stubs -------------------------------------------------------------- */
static FlightState_t g_fsm = FLIGHT_STATE_DISARMED;
static float g_rc_val[4];
static int g_rc_act[4];
static uint8_t g_sysid_act[3];
static unsigned long g_tick;

/* ---- stubs ------------------------------------------------------------------------------------- */
uint16_t ADC_Read(void) { call(1); return (uint16_t)(xs(&g_st) & 0xFFFu); }
float Voltage_Calculation(uint16_t adc_value) { call(2); MIXV(adc_value); return adc_value * (3.3f / 4095); }
void SetBeep(int B) { call(3); MIXV(B); }
float abs_fl(float value) { callf(4, value); return fabsf(value); }

void CalTrim_Init(CalTrim_t *c, uint16_t max_ticks) { call(5); MIXV(max_ticks); memset(c, 0, sizeof *c); c->max_ticks = max_ticks; }
void CalTrim_Step(CalTrim_t *c, float gx, float gy, float gz, float mx, float my, float mz, uint8_t flying)
{
    call(6); MIXV(gx); MIXV(gy); MIXV(gz); MIXV(mx); MIXV(my); MIXV(mz); MIXV(flying);
    if (pm(&g_st, 20)) c->state = (CalTrimState_t)(xs(&g_st) % 4u);
}
void CalHot_Init(CalHot_t *c) { call(7); memset(c, 0, sizeof *c); }
void CalHot_Step(CalHot_t *c, float gx, float gy, float gz, float ax, float ay, uint8_t flying, uint8_t rc_q)
{
    call(8); MIXV(gx); MIXV(gy); MIXV(gz); MIXV(ax); MIXV(ay); MIXV(flying); MIXV(rc_q);
    if (pm(&g_st, 30)) c->state = (CalHotState_t)(xs(&g_st) % 3u);
    if (pm(&g_st, 10)) c->rejected = (uint8_t)(xs(&g_st) & 1u);
    if (pm(&g_st, 10)) c->cleared = 0U;
}

void Check_Fly_Mode(void) { call(9); }
void Controller_CheckSwitch(uint8_t armed) { call(10); MIXV(armed); }
float Controller_Update(uint8_t axis, float u_nom) { call(11); MIXV(axis); MIXV(u_nom); return u_nom + fr(&g_st, -5.0f, 5.0f); }

void EkfOf_Init(EkfOf_t *e)
{
    int i;
    call(12);
    memset(e, 0, sizeof *e);
    for (i = 0; i < 8; i++) e->x[i] = fr(&g_st, -0.5f, 0.5f);
}
void EkfOf_Predict(EkfOf_t *e, float dt, float ax, float ay)
{
    call(13); MIXV(dt); MIXV(ax); MIXV(ay);
    e->x[0] += e->x[1] * dt;
    e->x[3] += e->x[4] * dt;
    e->x[1] += ax * dt;
    e->x[4] += ay * dt;
}
void EkfOf_Update(EkfOf_t *e, float of_x, float of_y)
{
    float big = pm(&g_st, 300) ? 10.0f : 0.05f;   /* bursts of large innovations trip the health gate */
    call(14); MIXV(of_x); MIXV(of_y);
    e->innov_x = fr(&g_st, -big, big);
    e->innov_y = fr(&g_st, -big, big);
    e->x[1] += 0.1f * (of_x - e->x[1]);
    e->x[4] += 0.1f * (of_y - e->x[4]);
}
void EkfOf_UpdateZeroVel(EkfOf_t *e) { call(15); e->x[1] = 0.0f; e->x[4] = 0.0f; e->innov_x = 0.0f; e->innov_y = 0.0f; }
void EkfOf_ResetPos(EkfOf_t *e) { call(16); e->x[0] = 0.0f; e->x[3] = 0.0f; }
void EkfOf_ResetBias(EkfOf_t *e, float var) { call(17); MIXV(var); e->x[2] = 0.0f; e->x[5] = 0.0f; }

void FlightFSM_Event(FlightEvent_t event)
{
    call(18); MIXV(event);
    if (event == FLIGHT_EVENT_DISARM_REQUEST) g_fsm = FLIGHT_STATE_DISARMED;
    else if (event == FLIGHT_EVENT_DANGEROUS_STOP) g_fsm = FLIGHT_STATE_EMERGENCY;
}
FlightState_t FlightFSM_GetState(void) { call(19); return g_fsm; }
float GyroFilter_Apply(GyroFiltAxis_e axis, float x) { call(20); MIXV(axis); MIXV(x); return x; }
void MRAC_Control(const CtrlerTypeDef *current_state) { call(21); mix(current_state, sizeof *current_state); }

float RCInput_Get(RC_Axis_t axis) { call(22); MIXV(axis); return g_rc_val[axis]; }
int RCInput_IsActive(RC_Axis_t axis) { call(23); MIXV(axis); return g_rc_act[axis]; }
void RCInput_SetAuthority(uint8_t has_authority) { call(24); MIXV(has_authority); }
uint16_t RPM_Get(uint8_t ch) { call(25); MIXV(ch); return (uint16_t)(xs(&g_st) % 9000u); }

void SDK_Set_Gyroz(void) { call(26); MIXV(Ctrler); }
void SDK_Set_V_Loc(void) { call(27); MIXV(Ctrler); }
void SDK_StateMachine_Init(void) { call(28); }
void Set_IDLE_Motors(void) { call(29); MIXV(mymotor); }
void Set_PWM_Motors(void) { call(30); MIXV(mymotor); }
void Set_Zero_Motors(void) { call(31); MIXV(mymotor); }

void SysID_Update(void) { call(32); }
uint8_t SysID_IsAxisActive(SysID_Axis_e axis) { call(33); MIXV(axis); return axis < 3 ? g_sysid_act[axis] : 0U; }
float SysID_GetRateSetpoint(SysID_Axis_e axis) { call(34); MIXV(axis); return fr(&g_st, -30.0f, 30.0f); }

void ThrustEst_Update(const float pwm[4], const uint16_t rpm[4], float acc_z, float pitch_deg, float roll_deg)
{
    call(35); mix(pwm, 4 * sizeof pwm[0]); mix(rpm, 4 * sizeof rpm[0]); MIXV(acc_z); MIXV(pitch_deg); MIXV(roll_deg);
}

void wfb_glue_disarmed(void) { call(36); }
void wfb_glue_tick(const wfb_glue_in_t *in, wfb_glue_out_t *out)
{
    call(37);   /* field by field: `in` is a stack local, its padding bytes are undefined */
    MIXV(in->now_ms); MIXV(in->x_m); MIXV(in->y_m); MIXV(in->z_m); MIXV(in->roll_deg); MIXV(in->pitch_deg);
    MIXV(in->vbat_v); MIXV(in->yaw_deg); MIXV(in->armed); MIXV(in->motors_idle); MIXV(in->sbus_live);
    MIXV(in->airborne); MIXV(in->rc_override);
    memset(out, 0, sizeof *out);
    if (pm(&g_st, 5)) out->motor_stop_req = 1U;
    else if (pm(&g_st, 10)) out->land_req = 1U;
    else if (pm(&g_st, 10)) out->takeoff_req = 1U;
    else if (pm(&g_st, 100)) {
        out->setpoint_valid = 1U;
        out->x_sp_m = fr(&g_st, -2.0f, 2.0f);
        out->y_sp_m = fr(&g_st, -2.0f, 2.0f);
        out->z_sp_m = fr(&g_st, 0.0f, 2.0f);
        out->yaw_sp_deg = fr(&g_st, -180.0f, 180.0f);
    }
}

TickType_t xTaskGetTickCount(void) { call(38); return (TickType_t)g_tick; }
void vPortEnterCritical(void) { call(39); }
void vPortExitCritical(void) { call(40); }

/* ---- per-tick inputs ------------------------------------------------------------------------------ */
static void toggle_u8(volatile uint8_t *v, unsigned per_mille) { if (pm(&g_in, per_mille)) *v = (uint8_t)!*v; }

static void script_inputs(void)
{
    int a;
    /* regime changes, with persistence so multi-tick logic (landing, edges, gates) gets exercised */
    if (pm(&g_in, 5)) { unsigned r = xs(&g_in) % 10u; g_fsm = r < 6u ? FLIGHT_STATE_ARMED : (r < 9u ? FLIGHT_STATE_DISARMED : FLIGHT_STATE_EMERGENCY); }
    if (pm(&g_in, 3)) flight_phase = (FlightPhase_t)(xs(&g_in) % 4u);
    for (a = 0; a < 4; a++) {
        if (pm(&g_in, 15)) g_rc_act[a] = !g_rc_act[a];
        g_rc_val[a] += fr(&g_in, -0.05f, 0.05f);
        if (g_rc_val[a] > 1.0f) g_rc_val[a] = 1.0f;
        if (g_rc_val[a] < (a == 0 ? 0.0f : -1.0f)) g_rc_val[a] = a == 0 ? 0.0f : -1.0f;
    }
    for (a = 0; a < 3; a++) if (pm(&g_in, 3)) g_sysid_act[a] = (uint8_t)!g_sysid_act[a];
    if (g_tick == 300) g_estimator_ready = 1U;
    if (pm(&g_in, 1)) g_estimator_ready = (uint8_t)!g_estimator_ready;
    toggle_u8(&g_motor_idle_enabled, 10);
    if (pm(&g_in, 10)) {
        int near = pm(&g_in, 500);   /* half the targets sit within a few cm of the drone (TWC_arrived) */
        TWC.execute = !TWC.execute;
        TWC.target_x = near ? Ctrler.locxPID.FB + fr(&g_in, -10.0f, 10.0f) : fr(&g_in, -300.0f, 300.0f);
        TWC.target_y = near ? Ctrler.locyPID.FB + fr(&g_in, -10.0f, 10.0f) : fr(&g_in, -300.0f, 300.0f);
        TWC.target_z = near ? Ctrler.Z_posPID.FB + fr(&g_in, -0.1f, 0.1f) : fr(&g_in, 0.0f, 2.0f);
        TWC.set_yaw  = fr(&g_in, -180.0f, 180.0f);
    }
    if (pm(&g_in, 3)) sbus_flyup_trigger = 1U;
    if (pm(&g_in, 3)) sbus_path_trigger = 1U;
    toggle_u8(&sbus_lost, 5);
    if (pm(&g_in, 10)) sbus_channel[5] = (unsigned short)(pm(&g_in, 500) ? 1694 : 306);
    toggle_u8(&bench_mode_active, 2);
    if (pm(&g_in, 2)) { motor_test_active = 1U; motor_test_id = (uint8_t)(xs(&g_in) % 5u); motor_test_ccr = (uint16_t)(2000u + xs(&g_in) % 2000u); }
    if (pm(&g_in, 30)) motor_test_watchdog = 0U;
    toggle_u8(&dbg_motor_manual, 2);
    for (a = 0; a < 4; a++) if (pm(&g_in, 5)) dbg_motor_ccr[a] = (uint16_t)(2000u + xs(&g_in) % 2000u);
    if (pm(&g_in, 3)) g_of_bias_mode = (uint8_t)(xs(&g_in) % 3u);
    toggle_u8(&g_of_bias_ema_freeze, 3);
    if (pm(&g_in, 1)) g_of_bias_ema_tau_s = fr(&g_in, 1.0f, 300.0f);
    if (pm(&g_in, 4)) g_of_bias_capture_req = 1U;
    if (pm(&g_in, 2)) g_of_handheld_test = 1U;
    toggle_u8(&g_ekf_of_shadow, 2);
    toggle_u8(&g_ekf_of_vel_fb, 2);
    toggle_u8(&g_of_full_tilt, 2);
    toggle_u8(&g_traj_ff_on, 2);
    toggle_u8(&SDK_DelayWakeFlag, 5);
    if (pm(&g_in, 5)) g_ekf_gate.ctrl_enable = (uint8_t)!g_ekf_gate.ctrl_enable;
    if (pm(&g_in, 5)) g_ekf_gate.healthy = (uint8_t)!g_ekf_gate.healthy;
    g_ekf_gate.vx_cms = fr(&g_in, -30.0f, 30.0f);
    g_ekf_gate.vy_cms = fr(&g_in, -30.0f, 30.0f);
    if (pm(&g_in, 10)) g_wfb_status.traj_state = pm(&g_in, 600) ? (float)WFB_TRAJ_EXECUTING : 0.0f;
    if (pm(&g_in, 10)) g_wfb_status.prim_state = pm(&g_in, 600) ? (float)WFB_PRIM_TRAJ : 0.0f;
    if (pm(&g_in, 2)) { gs_throttle_min_pct = fr(&g_in, 0.0f, 0.6f); gs_throttle_max_pct = fr(&g_in, 0.3f, 1.0f); }
    if (pm(&g_in, 2)) { gs_max_pitch_deg = fr(&g_in, 5.0f, 30.0f); gs_max_roll_deg = fr(&g_in, 5.0f, 30.0f); }
    if (pm(&g_in, 2)) { gs_max_horizontal_speed_mps = fr(&g_in, 0.2f, 2.0f); gs_max_vertical_speed_mps = fr(&g_in, 0.2f, 2.0f); }
    if (pm(&g_in, 2)) g_yaw_mix_dir = pm(&g_in, 500) ? 1.0f : -1.0f;
    if (pm(&g_in, 2)) { g_att_trim_roll_deg = fr(&g_in, -3.0f, 3.0f); g_att_trim_pitch_deg = fr(&g_in, -3.0f, 3.0f); }
    g_imu_settle_metric = fr(&g_in, 0.0f, 0.002f);

    /* sensors, every tick */
    imu_data.rol += fr(&g_in, -1.0f, 1.0f);
    imu_data.pit += fr(&g_in, -1.0f, 1.0f);
    imu_data.yaw += fr(&g_in, -3.0f, 3.0f);
    if (imu_data.rol > 40.0f || imu_data.rol < -40.0f) imu_data.rol *= 0.5f;
    if (imu_data.pit > 40.0f || imu_data.pit < -40.0f) imu_data.pit *= 0.5f;
    if (imu_data.yaw > 180.0f) imu_data.yaw -= 360.0f;
    if (imu_data.yaw < -180.0f) imu_data.yaw += 360.0f;
    Gyro_X_Real = fr(&g_in, -2.0f, 2.0f);
    Gyro_Y_Real = fr(&g_in, -2.0f, 2.0f);
    Gyro_Z_Real = fr(&g_in, -2.0f, 2.0f);
    Lin_Acc_X_body = fr(&g_in, -300.0f, 300.0f);
    Lin_Acc_Y_body = fr(&g_in, -300.0f, 300.0f);
    Lin_Acc_Z_body = fr(&g_in, -300.0f, 300.0f);
    {
        float t = pm(&g_in, 20) ? 0.95f : 0.3f;   /* now and then past the 60 deg OF tilt cut */
        Gravity_Body_X = fr(&g_in, -t, t);
        Gravity_Body_Y = fr(&g_in, -t, t);
        Gravity_Body_Z = sqrtf(fabsf(1.0f - Gravity_Body_X * Gravity_Body_X - Gravity_Body_Y * Gravity_Body_Y));
    }
    if (pm(&g_in, 20)) ano_of.of_quality = (u8)(pm(&g_in, 800) ? 255 : xs(&g_in) % 60u);
    ano_of.of2_dx_fix = (s16)((int)(xs(&g_in) % 41u) - 20);
    ano_of.of2_dy_fix = (s16)((int)(xs(&g_in) % 41u) - 20);
    if (pm(&g_in, 250)) ano_of.of_update_cnt++;
    {
        /* height regimes: 0 rest (constant, near the ground), 1 slow walk, 2 noisy walk; plus glitches */
        static long alt = 50;
        static unsigned regime = 1u;
        unsigned r = xs(&g_in) % 1000u;
        if (pm(&g_in, 5)) {
            regime = xs(&g_in) % 3u;
            if (regime == 0u) alt = (long)(xs(&g_in) % 13u);
        }
        if (regime == 1u) alt += (long)(xs(&g_in) % 3u) - 1;
        if (regime == 2u) alt += (long)(xs(&g_in) % 21u) - 10;
        if (alt < 0) alt = 0;
        if (alt > 700) alt = 700;
        ano_of.of_alt_cm = r < 3u ? 0xFFFFu : (r < 4u ? 0xFFFFFFFFu : (r < 5u ? 2905u : (r < 15u ? (u32)(xs(&g_in) % 600u) : (u32)alt)));
    }
}

/* State the file owns or writes, in both the old and the new version. */
static void mix_state(void)
{
    MIXV(Ctrler); MIXV(ano_of); MIXV(mymotor); MIXV(DroneStatus); MIXV(TWC); MIXV(TWC_arrived); MIXV(flight_phase);
    MIXV(Throttle_out); MIXV(u_gyrox); MIXV(u_gyroy); MIXV(u_gyroz); MIXV(Throttle_th); MIXV(cnt_h); MIXV(cnt_loc);
    MIXV(Cos_Yaw_01); MIXV(Sin_Yaw_01); MIXV(Cos_roll_01); MIXV(Sin_roll_01); MIXV(Cos_pitch_01); MIXV(Sin_pitch_01);
    MIXV(g_of_hold_active); MIXV(s_land_sink_bias); MIXV(g_of_bias_mode); MIXV(g_of_bias_ema_freeze);
    MIXV(g_of_bias_ema_tau_s); MIXV(g_of_full_tilt); MIXV(g_of_scale); MIXV(g_ekf_of_health); MIXV(g_ekf_of_fallback);
    MIXV(g_ekf_of_shadow); MIXV(g_ekf_of_vel_fb); MIXV(s_ekf_of_innov_bad_cnt); MIXV(s_ekf_of_tripped);
    MIXV(s_ekf_of_mode_restore); MIXV(g_of_handheld_test); MIXV(g_of_rest_warn); MIXV(s_of_bias_x); MIXV(s_of_bias_y);
    MIXV(s_of_bias_seeded); MIXV(s_ekf_of); MIXV(s_ekf_of_inited); MIXV(s_ekf_prev_px); MIXV(s_ekf_prev_py);
    MIXV(s_ekf_pos_synced); MIXV(g_of_bias_capture_req); MIXV(s_alt_valid_tick); MIXV(s_cal_trim); MIXV(s_cal_hot);
    MIXV(g_cal_health); MIXV(des_pitch); MIXV(des_roll); MIXV(voltage); MIXV(real_voltage); MIXV(adc_value);
    MIXV(motor_test_active); MIXV(motor_test_watchdog); MIXV(dbg_motor_manual); MIXV(sbus_flyup_trigger);
    MIXV(sbus_path_trigger); MIXV(mrac_in_armed); MIXV(mrac_in_phase); MIXV(g_fsm);
}

int main(int argc, char **argv)
{
    unsigned long ticks = argc > 1 ? strtoul(argv[1], 0, 10) : 100000ul;
    unsigned seed = argc > 2 ? (unsigned)strtoul(argv[2], 0, 10) : 1u;
    unsigned long every = argc > 3 ? strtoul(argv[3], 0, 10) : 1000ul;
    g_in = 2463534242u ^ (seed * 2654435761u);
    g_st = 88675123u ^ (seed * 40503u);
    gs_max_pitch_deg = 15.0f; gs_max_roll_deg = 15.0f; gs_throttle_min_pct = 0.0f; gs_throttle_max_pct = 1.0f;
    gs_max_horizontal_speed_mps = 0.5f; gs_max_vertical_speed_mps = 0.5f;
    for (g_tick = 1; g_tick <= ticks; g_tick++) {
        script_inputs();
        stabilizer_Task();
        if (g_tick % 200u == 0u) Get_Voltage();
        mix_state();
        if (g_tick % every == 0u || g_tick == ticks) printf("tick %lu hash %016llx calls %lu\n", g_tick, g_h, g_calls);
    }
    return 0;
}
