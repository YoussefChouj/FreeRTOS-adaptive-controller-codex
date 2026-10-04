/* SIL step server (WP-31): the real API/pid.c, API/mrac.c, API/mrac_math.c and API/controller.c, stepped one
 * 200 Hz control tick per request by sim/sil/fw.py over binary stdin/stdout.
 *
 * TASK/StabilizerTask.c (Compute_Motor, Update_Des, the mixer) needs FreeRTOS, the HAL and the sensor tasks and
 * cannot be host-built, so its control path for one flight state is ported below, each block citing its lines:
 * flight_phase FLYING, armed, TWC.execute 1 (the scenario target), no RC stick active, OF hold on (OFHOLD_CH
 * high), ToF valid, land_contact 0, SysID idle except the optional rate dither. Sensor processing (OF rotation,
 * ToF tilt correction, attitude filter) belongs to the plant: a request carries the estimates the PIDs read as FB.
 *
 * Ops: one byte, then float32 fields; the reply is float32 fields.
 *   'S' step     SIL_NIN fields                  -> SIL_NOUT fields
 *   'C' command  (cmd_id, idx, val)              -> (applied 0/1)   CMD 0x01, 0x0F idx <= 12, 0x1D as send_data.c;
 *                                                                   0x1F idx 1 = g_ctrl_axis_mask (a probe write);
 *                                                                   0x7E idx axis = gamma scale, SIL only (no bound)
 *   'P' pid      (member, Des, FB)               -> (E, SumE, Up, Ui, Ud, U)   one ComputePID on a Ctrler member
 *   'K' gains    (member)                        -> (Kp, Ki, Kd)
 *   'G' wfb tick (now_ms, x_m, y_m, z_m, roll, pitch, vbat, yaw, armed, motors_idle, sbus_live, airborne,
 *                 rc_override)                   -> (setpoint_valid, x_sp, y_sp, z_sp, yaw_sp, takeoff_req, land_req,
 *                                                    motor_stop_req, prim_state, safety_trip, hb_age,
 *                                                    gs_flight_active, fence_push)   one API/wfb_glue.c tick
 *   'H' wfb cmd  (cmd, idx, val, now_ms)         -> (WFB_RESULT_*)  wfb_glue_on_cmd (0x1A prim, 0x1B traj)
 *   'Q' quit
 */
#include <stdio.h>
#include <math.h>
#include <io.h>
#include <fcntl.h>
#include <windows.h>

#include "pid.h"
#include "mrac.h"
#include "controller.h"
#include "imu_update.h"
#include "wfb_glue.h"

enum { I_PIT, I_ROL, I_YAW, I_GX, I_GY, I_GZ, I_X, I_Y, I_Z, I_VX, I_VY, I_VZ, I_TX, I_TY, I_TZ, I_SETYAW,
       I_FF, I_DP, I_DR, I_DY, I_TRIM_P, I_TRIM_R, SIL_NIN };
enum { O_M1, O_M2, O_M3, O_M4, O_THR,
       O_UNOM_P, O_UNOM_R, O_UNOM_Y, O_UNOM_Z, O_CORR_P, O_CORR_R, O_CORR_Y, O_CORR_Z,
       O_TH_P, O_TH_R, O_TH_Y, O_TH_Z, O_TRIPPED, O_INJ, O_FADE, O_LEARN,
       O_PIT_DES, O_ROL_DES, O_GY_DES, O_GX_DES, O_GZ_DES, O_ZP_DES, O_ZR_DES,
       O_VID_P, O_VID_R, O_VID_Y, O_HOST_US, SIL_NOUT };

/* Firmware globals the host-built files expect (API/SINS.c:9-10, TASK/StabilizerTask.c:78-79 and :88). */
float Sin_Yaw = 0.0f, Cos_Yaw = 1.0f;
static float Cos_Yaw_01 = 1.0f, Sin_Yaw_01 = 0.0f;
_imu_st imu_data;
float Throttle_out, u_gyrox, u_gyroy, u_gyroz;      /* read by controller.c mrac_mixer_deficit (V2) */
volatile float g_yaw_mix_dir = -1.0f;               /* StabilizerTask.c:88 */

/* Firmware constants: Global_file/global_declare.c:34-37, StabilizerTask.c:1301 and :1370, BSP/pwm.h:13-15. */
#define GS_MAX_PITCH_DEG    15.0f
#define GS_MAX_ROLL_DEG     15.0f
#define GS_THROTTLE_MIN_PCT 0.0f
#define GS_THROTTLE_MAX_PCT 1.0f
#define THROTTLE_TH         3050
#define MOTOR_PWM_ZERO      2000
#define MOTOR_PWM_MAX       4000
#define VXY_DES_MAX         120.0f
#define YAWRATE_DES_MAX     60.0f
#define DEG2RAD             0.0174532925f
#define RAD2DEG             57.2957795f
#define GRAVITY_MSS         9.81f

static uint8_t cnt_h, cnt_loc;
static TrajFF_t s_traj_ff;

/* StabilizerTask.c:208-222 */
static void ComputePID_Hold(PIDTypeDef *pPID, uint8_t integrate, uint8_t hold)
{
	float sumE = pPID->SumE;
	float ui   = pPID->Ui;
	float u;
	ComputePID_Gated(pPID, integrate);
	if (hold && integrate && fabsf(sumE) < 1e12f && fabsf(ui) < 1e12f) {
		pPID->SumE = sumE;
		pPID->Ui   = ui;
		u = pPID->Up + ui + pPID->Ud;
		if (u >  pPID->UMax) u =  pPID->UMax;
		if (u < -pPID->UMax) u = -pPID->UMax;
		pPID->U = u;
	}
}

static float Constrain_Float(float amt, float low, float high)   /* StabilizerTask.c:1695-1698 */
{
	return ((amt) < (low) ? (low) : ((amt) > (high) ? (high) : (amt)));
}

/* StabilizerTask.c:1707-1723. fast_atan (a 257-entry table, linear interpolation) is replaced by atanf: the
 * interpolation error of a 1/256 grid is below 2e-6 rad. */
static void accel_to_lean_angles(float acc_tar_forward, float acc_tar_right, float *tar_pitch, float *tar_roll)
{
	float my_Cos_Roll = cosf(imu_data.rol * DEG2RAD);
	float my_Cos_Pitch = cosf(imu_data.pit * DEG2RAD);
	*tar_pitch = Constrain_Float(atanf(acc_tar_forward * my_Cos_Roll / (GRAVITY_MSS * 100)) * RAD2DEG,
	                             -GS_MAX_PITCH_DEG, GS_MAX_PITCH_DEG);
	*tar_roll = Constrain_Float(atanf(acc_tar_right * my_Cos_Pitch / (GRAVITY_MSS * 100)) * RAD2DEG,
	                            -GS_MAX_ROLL_DEG, GS_MAX_ROLL_DEG);
}

static short motor_clamp(float m)   /* (short) store into mymotor, then Set_PWM_Motors (BSP/pwm.c:269-282) */
{
	short s = (short)m;
	value_limit(s, MOTOR_PWM_ZERO, MOTOR_PWM_MAX);
	return s;
}

static float theta_norm(const MRAC_AxisState_t *st)
{
	float s = 0.0f;
	int i;
	for (i = 0; i < MRAC_N_FEATURES; i++) s += st->Theta[i] * st->Theta[i];
	return sqrtf(s);
}

static void sil_step(const float *in, float *out)
{
	const uint8_t airborne = 1U;
	float des_pitch, des_roll, u_nom[4], corr[4];
	short m[4];
	int a;

	/* Sensor -> FB, StabilizerTask.c:861-884 (GyroFilter_Apply is pass-through by default) */
	imu_data.pit = in[I_PIT];
	imu_data.rol = in[I_ROL];
	imu_data.yaw = in[I_YAW];
	Ctrler.Z_posPID.FB  = in[I_Z];
	Ctrler.Z_ratePID.FB = in[I_VZ];
	Ctrler.pitchPID.FB = -imu_data.pit;
	Ctrler.rollPID.FB  =  imu_data.rol;
	Ctrler.yawPID.FB   = -imu_data.yaw;
	Ctrler.gyroyPID.FB = -in[I_GY];
	Ctrler.gyroxPID.FB =  in[I_GX];
	Ctrler.gyrozPID.FB = -in[I_GZ];
	Ctrler.locxPID.FB  = in[I_X];
	Ctrler.locyPID.FB  = in[I_Y];
	Ctrler.locxsPID.FB = in[I_VX];
	Ctrler.locysPID.FB = in[I_VY];
	/* API/SINS.c:18-25 (Yaw = -imu_data.yaw) and StabilizerTask.c:503-504 */
	Sin_Yaw = sinf(-imu_data.yaw * DEG2RAD);
	Cos_Yaw = cosf(-imu_data.yaw * DEG2RAD);
	Cos_Yaw_01 = Cos_Yaw;
	Sin_Yaw_01 = Sin_Yaw;

	/* Height: Update_Des(height) TWC.execute rate limit (1539-1547), Z_pos every 2nd tick (1152-1165),
	 * Update_Des(v_h) (1575), Z_rate every tick (1166-1174) */
	{
		float z_err = in[I_TZ] - Ctrler.Z_posPID.Des;
		if      (z_err >  0.005f) Ctrler.Z_posPID.Des += 0.005f;
		else if (z_err < -0.005f) Ctrler.Z_posPID.Des -= 0.005f;
		else                      Ctrler.Z_posPID.Des  = in[I_TZ];
	}
	cnt_h++;
	if (cnt_h >= 2) {
		ComputePID(&Ctrler.Z_posPID);
		cnt_h = 0;
	}
	Ctrler.Z_ratePID.Des = Ctrler.Z_posPID.U;
	ComputePID(&Ctrler.Z_ratePID);

	/* Position and velocity loops every 2nd tick (1177-1313). SDK_Set_V_Loc acts only in legacy SDK states. */
	cnt_loc++;
	if (cnt_loc >= 2) {
		float vx, vy, ax, ay;
		cnt_loc = 0;
		Ctrler.locxPID.Des = in[I_TX];                                     /* Update_Des(loc), 1597 */
		Ctrler.locyPID.Des = in[I_TY];
		ComputePID_Hold(&Ctrler.locxPID, airborne, 0U);
		ComputePID_Hold(&Ctrler.locyPID, airborne, 0U);
		Ctrler.locysPID.Des = Constrain_Float(Ctrler.locyPID.U, -VXY_DES_MAX, VXY_DES_MAX);   /* 1604-1618 */
		Ctrler.locxsPID.Des = Constrain_Float(Ctrler.locxPID.U, -VXY_DES_MAX, VXY_DES_MAX);
		TrajFF_Step(&s_traj_ff, (uint8_t)(in[I_FF] > 0.5f), in[I_TX], in[I_TY], 0.01f, 0.2f, 100.0f,
		            &vx, &vy, &ax, &ay);                                   /* 1298-1308 */
		Ctrler.locxsPID.Des += vx;
		Ctrler.locysPID.Des += vy;
		ComputePID_Hold(&Ctrler.locxsPID, airborne, 0U);
		ComputePID_Hold(&Ctrler.locysPID, airborne, 0U);
		Ctrler.locxsPID.U += ax;
		Ctrler.locysPID.U += ay;
	}

	/* Attitude: Update_Des(pitrol) OF hold (1636-1659), angle PIDs (1319-1323) */
	des_pitch = -(Ctrler.locysPID.U) * Cos_Yaw_01 - (Ctrler.locxsPID.U) * Sin_Yaw_01;
	des_roll  = -(Ctrler.locxsPID.U) * Cos_Yaw_01 + (Ctrler.locysPID.U) * Sin_Yaw_01;
	accel_to_lean_angles(des_pitch, -des_roll, &Ctrler.pitchPID.Des, &Ctrler.rollPID.Des);
	Ctrler.pitchPID.Des = AttTrim_Apply(Ctrler.pitchPID.Des, in[I_TRIM_P], GS_MAX_PITCH_DEG, 1U);
	Ctrler.rollPID.Des  = AttTrim_Apply(Ctrler.rollPID.Des,  in[I_TRIM_R], GS_MAX_ROLL_DEG,  1U);
	ComputePID_Gated(&Ctrler.pitchPID, airborne);
	ComputePID_Gated(&Ctrler.rollPID, airborne);
	Ctrler.yawPID.Des = in[I_SETYAW];                                      /* Update_Des(yaw), 1669 */
	ComputeYawPID(&Ctrler.yawPID);

	/* Update_Des(gyro) (1672-1684), SysID dither site (1338-1340), rate PIDs (1352-1354) */
	Ctrler.gyroyPID.Des = Ctrler.pitchPID.U;
	Ctrler.gyroxPID.Des = Ctrler.rollPID.U;
	Ctrler.gyrozPID.Des = Constrain_Float(Ctrler.yawPID.U, -YAWRATE_DES_MAX, YAWRATE_DES_MAX);
	Ctrler.gyroyPID.Des += in[I_DP];
	Ctrler.gyroxPID.Des += in[I_DR];
	Ctrler.gyrozPID.Des += in[I_DY];
	ComputePID_Gated(&Ctrler.gyroxPID, airborne);
	ComputePID_Gated(&Ctrler.gyroyPID, airborne);
	ComputePID(&Ctrler.gyrozPID);

	/* MRAC after the PIDs (1358-1361) */
	Controller_CheckSwitch(1U);
	mrac_in_armed = 1U;
	mrac_in_phase = MRAC_PHASE_FLYING;
	MRAC_Control(&Ctrler);

	/* Controller layer and mixer (1370-1409), PWM clamp (BSP/pwm.c:269-282) */
	u_nom[CTRL_AXIS_PITCH] = Ctrler.gyroyPID.U;
	u_nom[CTRL_AXIS_ROLL]  = Ctrler.gyroxPID.U;
	u_nom[CTRL_AXIS_YAW]   = Ctrler.gyrozPID.U;
	u_nom[CTRL_AXIS_Z]     = Ctrler.Z_ratePID.U;
	for (a = 0; a < 4; a++) corr[a] = Controller_Update((uint8_t)a, u_nom[a]) - u_nom[a];
	Throttle_out = Controller_Update(CTRL_AXIS_Z, Ctrler.Z_ratePID.U) + (short)THROTTLE_TH;
	u_gyrox      = Controller_Update(CTRL_AXIS_ROLL, Ctrler.gyroxPID.U);
	u_gyroy      = -Controller_Update(CTRL_AXIS_PITCH, Ctrler.gyroyPID.U);
	u_gyroz      = Controller_Update(CTRL_AXIS_YAW, Ctrler.gyrozPID.U);
	Throttle_out = Constrain_Float(Throttle_out, 2000.0f + GS_THROTTLE_MIN_PCT * 2000.0f,
	                               2000.0f + GS_THROTTLE_MAX_PCT * 2000.0f);
	m[0] = motor_clamp(Throttle_out - u_gyroy - u_gyrox - g_yaw_mix_dir * u_gyroz);
	m[1] = motor_clamp(Throttle_out + u_gyroy + u_gyrox - g_yaw_mix_dir * u_gyroz);
	m[2] = motor_clamp(Throttle_out - u_gyroy + u_gyrox + g_yaw_mix_dir * u_gyroz);
	m[3] = motor_clamp(Throttle_out + u_gyroy - u_gyrox + g_yaw_mix_dir * u_gyroz);

	for (a = 0; a < 4; a++) {
		out[O_M1 + a] = (float)m[a];
		out[O_UNOM_P + a] = u_nom[a];
		out[O_CORR_P + a] = corr[a];
	}
	out[O_THR] = Throttle_out;
	out[O_TH_P] = theta_norm(&mrac_state.pitch);
	out[O_TH_R] = theta_norm(&mrac_state.roll);
	out[O_TH_Y] = theta_norm(&mrac_state.yaw);
	out[O_TH_Z] = theta_norm(&mrac_state.z_rate);
	out[O_TRIPPED] = (float)mrac_simplex.tripped;
	out[O_INJ] = mrac_inj.inj_alpha;
	out[O_FADE] = mrac_simplex.fade;
	out[O_LEARN] = (float)mrac_inj.learn_gate;
	out[O_PIT_DES] = Ctrler.pitchPID.Des;
	out[O_ROL_DES] = Ctrler.rollPID.Des;
	out[O_GY_DES] = Ctrler.gyroyPID.Des;
	out[O_GX_DES] = Ctrler.gyroxPID.Des;
	out[O_GZ_DES] = Ctrler.gyrozPID.Des;
	out[O_ZP_DES] = Ctrler.Z_posPID.Des;
	out[O_ZR_DES] = Ctrler.Z_ratePID.Des;
	out[O_VID_P] = (float)mrac_var_id[MRAC_AXIS_PITCH];
	out[O_VID_R] = (float)mrac_var_id[MRAC_AXIS_ROLL];
	out[O_VID_Y] = (float)mrac_var_id[MRAC_AXIS_YAW];
}

static PIDTypeDef *member(int i)
{
	PIDTypeDef *all = &Ctrler.pitchPID;   /* CtrlerTypeDef is 14 PIDTypeDef in a row */
	return (i >= 0 && i < (int)(sizeof(CtrlerTypeDef) / sizeof(PIDTypeDef))) ? &all[i] : (PIDTypeDef *)0;
}

/* TASK/send_data.c: CMD 0x01 (1522-1541, gain lease off by default: API/pid.c:84), 0x0F idx <= 12 (1774-1790),
 * 0x1D (1820-1824, a ground-only write: the SIL applies presets before the first tick). */
static float sil_cmd(int id, int idx, float val)
{
	if (id == 0x01) {
		PIDTypeDef *pids[7] = { &Ctrler.pitchPID, &Ctrler.rollPID, &Ctrler.yawPID, &Ctrler.gyroxPID,
		                        &Ctrler.gyroyPID, &Ctrler.gyrozPID, &Ctrler.Z_ratePID };
		int axis = idx / 3, gain = idx % 3;
		if (idx < 0 || axis >= 7 || !(val >= 0.0f && val <= 200.0f)) return 0.0f;
		if (gain == 0) pids[axis]->Kp = val;
		else if (gain == 1) pids[axis]->Ki = val;
		else pids[axis]->Kd = val;
		return 1.0f;
	}
	if (id == 0x0F) {
		uint8_t on = ((uint8_t)(val + 0.5f)) != 0U ? 1U : 0U;
		switch (idx) {
		case 0:  mrac_flags.adaptation_on       = on; break;
		case 1:  mrac_flags.projection_on       = on; break;
		case 2:  mrac_flags.deadzone_on         = on; break;
		case 3:  mrac_flags.hard_freeze_on      = on; break;
		case 4:  mrac_flags.tanh_saturation_on  = on; break;
		case 5:  mrac_flags.e_modification_on   = on; break;
		case 6:  mrac_flags.l1_filtering_on     = on; break;
		case 7:  mrac_flags.axis_enable_pitch   = on; break;
		case 8:  mrac_flags.axis_enable_roll    = on; break;
		case 9:  mrac_flags.axis_enable_yaw     = on; break;
		case 10: mrac_flags.output_injection_on = on; break;
		case 11: mrac_flags.id_frame_on         = on; break;
		case 12: mrac_flags.of_frame_on         = on; break;
		default: return 0.0f;
		}
		return 1.0f;
	}
	if (id == 0x1D) {
		if (idx < 0 || idx > 255) return 0.0f;
		return (float)MRAC_VariantParamSet((uint8_t)((uint32_t)idx & 0x03U), (uint8_t)((uint32_t)idx >> 2U), val);
	}
	/* g_ctrl_axis_mask (API/controller.c:6): written by probe in flight; the SIL takes it as CMD 0x1F idx 1, the
	 * ground_station/platform/firmware_contract.py:525-531 map (send_data.c has no 0x1F handler). */
	if (id == 0x1F && idx == 1 && val >= 0.0f && val <= 15.0f) {
		g_ctrl_axis_mask = (uint8_t)(val + 0.5f);
		return 1.0f;
	}
	/* SIL only, not a flight command (sim/sil/limits.py): mrac_g_gamma[idx][*] = val without the 0x1D bound
	 * gamma_scale <= 2, so a gain sweep can reach the instability point. */
	if (id == 0x7E && idx >= 0 && idx < AXES && val >= 0.0f && val <= 1000.0f) {
		int k;
		for (k = 0; k < MRAC_N_GROUPS; k++) mrac_g_gamma[idx][k] = val;
		return 1.0f;
	}
	return 0.0f;
}

static int read_f(float *buf, int n) { return (int)fread(buf, sizeof(float), (size_t)n, stdin) == n; }
static void write_f(const float *buf, int n) { fwrite(buf, sizeof(float), (size_t)n, stdout); fflush(stdout); }

int main(void)
{
	float in[SIL_NIN], out[SIL_NOUT];
	LARGE_INTEGER f, t0, t1;
	_setmode(_fileno(stdin), _O_BINARY);
	_setmode(_fileno(stdout), _O_BINARY);
	QueryPerformanceFrequency(&f);
	Controller_Init();
	wfb_glue_init();
	for (;;) {
		int op = getchar();
		if (op == EOF || op == 'Q') return 0;
		if (op == 'S') {
			if (!read_f(in, SIL_NIN)) return 1;
			QueryPerformanceCounter(&t0);
			sil_step(in, out);
			QueryPerformanceCounter(&t1);
			out[O_HOST_US] = (float)((double)(t1.QuadPart - t0.QuadPart) * 1e6 / (double)f.QuadPart);
			write_f(out, SIL_NOUT);
		} else if (op == 'C') {
			float c[3], r;
			if (!read_f(c, 3)) return 1;
			r = sil_cmd((int)c[0], (int)c[1], c[2]);
			write_f(&r, 1);
		} else if (op == 'P') {
			float c[3], r[6] = {0};
			PIDTypeDef *p;
			if (!read_f(c, 3)) return 1;
			p = member((int)c[0]);
			if (p) {
				p->Des = c[1];
				p->FB = c[2];
				ComputePID(p);
				r[0] = p->E; r[1] = p->SumE; r[2] = p->Up; r[3] = p->Ui; r[4] = p->Ud; r[5] = p->U;
			}
			write_f(r, 6);
		} else if (op == 'K') {
			float c, r[3] = {0};
			PIDTypeDef *p;
			if (!read_f(&c, 1)) return 1;
			p = member((int)c);
			if (p) { r[0] = p->Kp; r[1] = p->Ki; r[2] = p->Kd; }
			write_f(r, 3);
		} else if (op == 'G') {
			float c[13], r[13];
			wfb_glue_in_t gi;
			wfb_glue_out_t go;
			if (!read_f(c, 13)) return 1;
			gi.now_ms = (uint32_t)c[0];
			gi.x_m = c[1]; gi.y_m = c[2]; gi.z_m = c[3];
			gi.roll_deg = c[4]; gi.pitch_deg = c[5]; gi.vbat_v = c[6]; gi.yaw_deg = c[7];
			gi.armed = (uint8_t)c[8]; gi.motors_idle = (uint8_t)c[9]; gi.sbus_live = (uint8_t)c[10];
			gi.airborne = (uint8_t)c[11]; gi.rc_override = (uint8_t)c[12];
			wfb_glue_tick(&gi, &go);
			r[0] = go.setpoint_valid; r[1] = go.x_sp_m; r[2] = go.y_sp_m; r[3] = go.z_sp_m; r[4] = go.yaw_sp_deg;
			r[5] = go.takeoff_req; r[6] = go.land_req; r[7] = go.motor_stop_req;
			r[8] = g_wfb_status.prim_state; r[9] = g_wfb_status.safety_trip; r[10] = g_wfb_status.hb_age;
			r[11] = g_wfb_status.gs_flight_active; r[12] = g_wfb_status.fence_push;
			write_f(r, 13);
		} else if (op == 'H') {
			float c[4], r;
			if (!read_f(c, 4)) return 1;
			r = (float)wfb_glue_on_cmd((uint8_t)c[0], (uint8_t)c[1], c[2], (uint32_t)c[3]);
			write_f(&r, 1);
		} else {
			return 2;
		}
	}
}
