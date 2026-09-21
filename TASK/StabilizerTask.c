#include "StabilizerTask.h"
#include "ekf_of.h"         /* 6-state OF position+bias KF (Mode 2 bias estimation) */
#include "math.h"
#include "pid.h"
#include "ADC.h"
#include "mrac.h"
#include "gyro_filter.h"
#include "sysid.h"
#include "rc_input.h"
#include "flight_fsm.h"
#include "calib.h"          /* ADR-0011 Phase 3 (acc-trim) + Phase 4 (gyro-hot) FSMs */
#include "RemoterTask.h"   /* OFHOLD_CH (ch6 OF-hold enable switch), sbus_channel[] */
#include "thrust_estimators.h"
#include "rpm.h"

/**
 * @module  StabilizerTask.c
 * @subsystem  control
 * @depends  StabilizerTask.h, pid.h, ADC.h, mrac.h
 * @owns  state update, setpoint update, motor mixing, and arm-mode motor output gating
 * @caution  mixer sign conventions and motor channel ordering are safety critical
 */
unsigned char cnt_h,cnt_loc,cnt_locs,cnt_yaw;
float Throttle_out,u_gyrox,u_gyroy,u_gyroz;
short Throttle_th = 2200;

/* LAND setpoint ramp: 0.0015 m/tick at 200 Hz = 0.30 m/s descent rate.
 * PID follows the setpoint to ground — no throttle ramp needed.
 * Touchdown detected by Z_ratePID.FB → 0 on frame contact. */
#define LAND_DES_STEP   0.0015f
/* Safety net: 2000 ticks at 200 Hz = 10 s max landing time before forced disarm. */
#define LAND_MAX_TICKS  2000U
/* Motor bench-test dead-man: stabilizer runs at 200 Hz, so 100 ticks = 500 ms.
 * If the dashboard stops sending CMD 0x16 heartbeats, motors are zeroed. */
#define MOTOR_TEST_DEADMAN_TICKS  100U
float Cos_Yaw_01= 0;
float Sin_Yaw_01= 0;
/* OF position-hold applied state (see case_Update_pitrol_Des); telemetered as status.of_hold. */
uint8_t g_of_hold_active = 0;

float Sin_roll_01= 0;
float Cos_roll_01= 0;
float Sin_pitch_01= 0;
float Cos_pitch_01= 0;
//
TargetSet_WorldReal_Coordinate TWC;

/* Accumulated near-ground sink bias (m/s). Ramps up while drone is stuck against
 * ground effect; adapts to battery voltage without needing voltage measurement.
 * Reset on every landing exit so it starts fresh each attempt. */
static float s_land_sink_bias = 0.0f;

/* OF velocity-bias calibration (docs/tracking_baseline_and_drift.md, mitigation #1).
 * of2_dx_fix/of2_dy_fix carry a small constant offset (measured ~12-14 / ~2-3 raw
 * units on the bench) that integrates into unbounded locx/locyPID.FB drift.
 *
 * Three estimation modes (g_of_bias_mode, CMD 0x1E idx=0):
 *
 * Mode 0 (FIXED): bias captured at boot from the first locked OF sample and
 *   locked forever. No pull-back during motion (the root cause of the user's
 *   2026-09-14 complaint). Recaptures on every ARM edge.
 *   Tradeoff: bias is wrong if OF zero-point drifts during flight.
 *
 * Mode 1 (EMA): continuous EMA tracking with optional freeze (CMD 0x1E idx=1).
 *   tau=20s keeps typical bench-scale motion (seconds) from being absorbed while
 *   still correcting minutes-scale thermal drift.
 *   The EMA absorbs any *sustained* real translational flow into the bias
 *   estimate — this is the pull-back artifact the user observed: during
 *   physical translation the OF sees the real velocity, and over 3-5 tau the
 *   EMA converges to the translation, erasing the displacement from position.
 *   Use g_of_bias_ema_freeze=1 to lock the current estimate before maneuvers.
 *
 * Mode 2 (EKF): dedicated 6-state OF position+bias KF (API/ekf_of.c).
 *   Jointly estimates [pos_x, vel_x, bias_x, pos_y, vel_y, bias_y].
 *   The measurement model (z = vel - bias) lets the KF separate real motion
 *   from bias in the Kalman sense — no pull-back by construction.
 *   Its position state feeds Ctrler.locx/yPID.FB directly, bypassing the
 *   raw OF integration. Runs in parallel with the 9-state IMU EKF (send_data.c).
 *   See API/ekf_of.h for the model and noise parameters.
/* Optical-flow velocity-bias estimation mode selector.
 * 0 = FIXED: bias set once at boot (first locked OF sample), never changes.
 *           ARM edge also re-snaps to current sample.
 * 1 = EMA:   continuous EMA tracking (OF_BIAS_EMA_ALPHA), with optional
 *            freeze via g_of_bias_ema_freeze (CMD 0x1E idx=1).
 * 2 = EKF:   dedicated 6-state OF position+bias KF (API/ekf_of.c).
 *            Runs in parallel with the existing 9-state IMU EKF (send_data.c).
 *            Mode 2 FEEDS the control loop (Ctrler.locx/yPID.FB) from the
 *            KF's debiased position state — bypasses the raw OF integration.
 *
 * Switch modes via CMD 0x1E idx=0 (dashboard UI wires this to 3 buttons).
 * EMA freeze is CMD 0x1E idx=1 (0=run, 1=frozen). Freeze persists across ARM.
 * Mode and freeze state are live-pokeable via g_of_bias_mode / g_of_bias_ema_freeze. */
#ifndef OF_BIAS_MODE_DEFAULT
#define OF_BIAS_MODE_DEFAULT  0   /* FIXED: bias captured at boot, never changes */
#endif
volatile uint8_t g_of_bias_mode = OF_BIAS_MODE_DEFAULT; /* 0=FIXED 1=EMA 2=EKF */
volatile uint8_t g_of_bias_ema_freeze = 0U;             /* 1=freeze EMA update */

/* EMA time constant and gain — used only in Mode 1 (EMA). */
#define OF_BIAS_EMA_TAU_S  20.0f
#define OF_BIAS_EMA_ALPHA  (0.005f / OF_BIAS_EMA_TAU_S) /* dt=5ms @ 200 Hz */

/* OF velocity-bias EMA state. s_of_bias_seeded gates the seed-on-first-sample
 * logic (see comment above). g_of_bias_ema_freeze gates the continuous update. */
float s_of_bias_x = 0.0f, s_of_bias_y = 0.0f;
static u8 s_of_bias_seeded = 0;

/* Dedicated 6-state OF position+bias KF — used in Mode 2 (EKF).
 * Initialised once at boot, ticked every control cycle in Update_Data.
 * Its position state (x[0], x[3]) feeds Ctrler.locx/yPID.FB in EKF mode. */
EkfOf_t s_ekf_of;
static uint8_t s_ekf_of_inited = 0U;

/* Manual bias snap (CMD 0x17). g_of_bias_capture_req is set from the ground
 * station (Send_Task) when the pilot has placed the drone level and still; it
 * jumps s_of_bias_x/y straight to the current of2_dx_fix/dy_fix sample instead
 * of waiting out the EMA time constant. The background EMA (below) then keeps
 * tracking from there — s_of_bias_x/y is only ever written from this task. */
volatile uint8_t g_of_bias_capture_req = 0;
/* OF lock gate (rec 3). of_quality is 0..255 (higher = better); a real lock sits at
 * ~250-255 on the bench. Below this we treat the sensor as unlocked: freeze both the
 * bias calibration and the earth_x/y integration so we never integrate garbage flow. */
#define OF_MIN_QUALITY              50U

/* P0 Finding 2 (docs/p0-verdicts.md): per-tick ToF validity flag. 1 = this tick's
 * sample passed the band+jump gates in Update_Data. While 0 (out of range, e.g.
 * above the 5 m band ceiling or a 0xFFFF no-reading) the Z loops hold their last
 * valid FB and their integrators are frozen in Compute_Motor. */
static uint8_t s_alt_valid_tick = 0U;

/* ADR-0011 Phase 3 (CAL_AIRBORNE_HOVER_TRIM) + Phase 4 (CAL_HOT_HOVER).
 * Initialised once at boot by CalTrim_Init / CalHot_Init, ticked each control
 * cycle from Update_Data. CalHot_TickState_t reused below for the FSM transitions. */
/* ADR-0011 Phase 3 + 4 calibrator instances — non-static so send_data.c can read them
 * for the always-on telemetry surface (CMD 0x05 v14 frame, fields 7-12). */
CalTrim_t s_cal_trim;
CalHot_t  s_cal_hot;
uint16_t g_cal_health = 0U;   /* bitmask: 0x01 BOOT_OK | 0x02 COLD_OK | 0x04 COLD_DEGRADED
                               *         0x08 AIRBORNE_OK | 0x10 AIRBORNE_DEGRADED
                               *         0x20 HOT_HOVER_OK | 0x40 HOT_REJECTED
                               *         0x80 MANUAL_ORIGIN_RESET | 0x100 BOOT_TIMEOUT
                               *         0x200 ESTIMATOR_READY */

/* Zero the world-frame optical-flow origin: the drone's current location becomes
 * the new (0,0) and position setpoints are synced so the next control tick sees
 * no jump. Shared by CMD 0x10 (manual reset) and the estimator-warmup auto-pin
 * (A4). Mirrors the former inline CMD 0x10 body in send_data.c.
 *
 * FIX 2026-09-13: also zero s_of_bias_x/y. Without this, CMD 0x10 zeroed
 * the accumulated position but the OF velocity bias kept integrating, so
 * position re-drifted within ~30 s. The operator's "Reset World Origin"
 * button had been broken in exactly this way for the entire v3 era; the
 * fix makes the reset sticky. The bias estimator (OF_BIAS_*) will start
 * accumulating again from the new (zero) state on the next tick. */
void Reset_World_Origin(void)
{
	ano_of.earth_x       = 0.0f;
	ano_of.earth_y       = 0.0f;
	ano_of.earth_x_ture  = 0.0f;
	ano_of.earth_y_ture  = 0.0f;
	ano_of.DISTANCE_X    = 0.0f;
	ano_of.DISTANCE_Y    = 0.0f;
	Ctrler.locxPID.FB    = 0.0f;
	Ctrler.locyPID.FB    = 0.0f;
	Ctrler.locxPID.Des   = 0.0f;
	Ctrler.locyPID.Des   = 0.0f;
	Ctrler.locxsPID.Des  = 0.0f;
	Ctrler.locysPID.Des  = 0.0f;
	s_of_bias_x          = 0.0f;
	s_of_bias_y          = 0.0f;
	/* Mode 2: also reset the OF KF position state.
	 * vel and bias states stay — only accumulated position resets to zero. */
	if (g_of_bias_mode == 2U && s_ekf_of_inited) {
		EkfOf_ResetPos(&s_ekf_of);
	}
}

void stabilizer_Task(void)
{
	 /* A4: while the attitude estimator is still converging, hold the OF world
	  * origin pinned at zero every tick. A tilted mid-warmup estimate would
	  * otherwise let optical flow integrate phantom drift; pinning removes the
	  * need to reset the origin by hand. On the tick the estimator goes ready
	  * this stops, so position accumulates from a clean, trustworthy origin. */
	 if (!g_estimator_ready) {
		 Reset_World_Origin();
	 }

	 Check_Fly_Mode(); //�ж����˻���״̬

	 /* ADR-0011: set BOOT_OK + COLD_OK on the rising edge of g_estimator_ready.
	  * Cold-cal degraded/timeout detection is left to the dashboard via the
	  * g_imu_settle_metric telemetry value (high value = didn't settle cleanly).
	  * Lazy one-shot init of the calibrators on cold-cal completion. */
	 {
		 static uint8_t s_prev_ready = 0U;
		 if (g_estimator_ready && !s_prev_ready) {
			 g_cal_health |= 0x01U;                          /* BOOT_OK rising edge */
			 g_cal_health |= 0x02U;                          /* COLD_OK (Phase 2 done) */
			 if (g_imu_settle_metric >= 0.0009f /* IMU_SETTLE_E2 */) {
				 g_cal_health |= 0x04U;                      /* COLD_DEGRADED */
				 g_cal_health |= 0x100U;                     /* BOOT_TIMEOUT */
			 }
			 CalTrim_Init(&s_cal_trim, 2000U);               /* 10 s @ 200 Hz */
			 CalHot_Init(&s_cal_hot);
			 /* FIX 2026-09-13: cold-boot Des sync. While warmup-pinned, Reset_World_Origin()
			  * keeps locx/yPID.Des=0 every tick. Once g_estimator_ready rises the position
			  * loop takes over, but case_Update_loc_Des only captures FB into Des after
			  * a stick-active→released transition. With sticks centered on the bench (or
			  * with the pilot never having touched the sticks), Des stays at 0 while FB
			  * holds whatever the OF has drifted to — opening the loop with a huge
			  * error that the integral saturates immediately. Sync once at the rising
			  * edge so the first uncontrolled tick sees zero error. Subsequent updates
			  * follow the existing case_Update_loc_Des rules (stick release or TWC).
			  * Same pattern for yaw — imu_data.yaw is the heading reference, sync
			  * yaw Des to it so the rate loop has zero command at handoff. */
			 Ctrler.locxPID.Des  = Ctrler.locxPID.FB;
			 Ctrler.locyPID.Des  = Ctrler.locyPID.FB;
			 Ctrler.locxsPID.Des = 0.0f;
			 Ctrler.locysPID.Des = 0.0f;
			 /* FIX 2026-09-13: yaw cold-boot sync. Same pattern as roll/pitch above —
			  * write Des directly here so the first tick sees zero error. The
			  * state-machine flag dance (is_last_yaw_valid) was attempted but the
			  * flag is a function-static inside case_Update_loc_Des and cannot
			  * be touched here. The direct Des assignment is sufficient. */
			 Ctrler.yawPID.Des   = imu_data.yaw;
		 }
		 s_prev_ready = g_estimator_ready;
		 if (g_estimator_ready) g_cal_health |= 0x200U;
	 }

	 Update_Data();

	 Compute_Motor();

	 Update_Motor();

//	 Get_Voltage();//�����ѹ

}

/*************************************************************************
�� �� ����void Update_Data(void);
�������ܣ����·���ֵ
��    ע��
*************************************************************************/
void Update_Data(void)
{
	
	Cos_Yaw_01=cos(-imu_data.yaw* DEG2RAD);
	Sin_Yaw_01=sin(-imu_data.yaw* DEG2RAD);
	
	Cos_roll_01=cos(imu_data.rol* DEG2RAD);
	Sin_roll_01 = sin(imu_data.rol* DEG2RAD);
	Cos_pitch_01=cos(imu_data.pit* DEG2RAD);
	Sin_pitch_01 = sin(imu_data.pit* DEG2RAD);
	
	//////////////////λ�û�����ֵ����/////////////////////////
		{
			u8 of_ok = (ano_of.of_quality >= OF_MIN_QUALITY);

			/* CMD 0x17 manual snap: jump s_of_bias_x/y to current OF sample.
			 * Available in all modes. For Mode 2 (EKF), also re-seeds the KF's
			 * velocity/bias states from the debiased OF reading. */
			if (g_of_bias_capture_req) {
				g_of_bias_capture_req = 0;
				if (of_ok) {
					s_of_bias_x = (float)ano_of.of2_dx_fix;
					s_of_bias_y = (float)ano_of.of2_dy_fix;
					/* Mode 2: inject debiased OF as KF velocity measurement */
					if (g_of_bias_mode == 2U && s_ekf_of_inited) {
						float ofx = ((float)ano_of.of2_dx_fix - s_of_bias_x) * 0.01f;
						float ofy = ((float)ano_of.of2_dy_fix - s_of_bias_y) * 0.01f;
						EkfOf_Update(&s_ekf_of, ofx, ofy);
					}
				}
			}

			/* ---- Mode 0 (FIXED): seed once at boot, never adapt ---- */
			if (g_of_bias_mode == 0U) {
				if (of_ok && !s_of_bias_seeded) {
					s_of_bias_seeded = 1;
					s_of_bias_x = (float)ano_of.of2_dx_fix;
					s_of_bias_y = (float)ano_of.of2_dy_fix;
				}
				/* bias stays fixed after seed */
			}
			/* ---- Mode 1 (EMA): continuous tracking with optional freeze ---- */
			else if (g_of_bias_mode == 1U) {
				if (of_ok && !s_of_bias_seeded) {
					s_of_bias_seeded = 1;
					s_of_bias_x = (float)ano_of.of2_dx_fix;
					s_of_bias_y = (float)ano_of.of2_dy_fix;
				}
				/* EMA runs only when not frozen */
				if (of_ok && !g_of_bias_ema_freeze) {
					s_of_bias_x += OF_BIAS_EMA_ALPHA
						* ((float)ano_of.of2_dx_fix - s_of_bias_x);
					s_of_bias_y += OF_BIAS_EMA_ALPHA
						* ((float)ano_of.of2_dy_fix - s_of_bias_y);
				}
			}
			/* ---- Mode 2 (EKF): tick the 6-state KF ---- */
			else if (g_of_bias_mode == 2U) {
				if (!s_ekf_of_inited) {
					EkfOf_Init(&s_ekf_of);
					s_ekf_of_inited = 1U;
				}
				{
					float ofx = ((float)ano_of.of2_dx_fix - s_of_bias_x) * 0.01f; /* m/s */
					float ofy = ((float)ano_of.of2_dy_fix - s_of_bias_y) * 0.01f;
					EkfOf_Predict(&s_ekf_of, 0.005f);
					if (of_ok) EkfOf_Update(&s_ekf_of, ofx, ofy);
				}
			}
		}
		/* ---- Position integration: Modes 0/1 use raw OF, Mode 2 uses KF ---- */
		if (g_of_bias_mode == 2U) {
			/* Mode 2: EKF state x[0]=pos_x, x[3]=pos_y is already debiased by
			 * construction. Apply yaw rotation and feed directly to PID FB.
			 * EkfOf_ResetPos() was called from Reset_World_Origin() below. */
			float ekf_px = s_ekf_of.x[0];
			float ekf_py = s_ekf_of.x[3];
			ano_of.earth_x = ekf_px * Cos_Yaw_01 + ekf_py * Sin_Yaw_01;
			ano_of.earth_y = ekf_py * Cos_Yaw_01 - ekf_px * Sin_Yaw_01;
			ano_of.earth_x_ture =  ano_of.earth_y;
			ano_of.earth_y_ture = -ano_of.earth_x;
			Ctrler.locxPID.FB = ano_of.earth_x_ture;
			Ctrler.locyPID.FB = ano_of.earth_y_ture;
		} else {
			/* Modes 0 and 1: debias raw OF and integrate as before */
			if (ano_of.of_quality >= OF_MIN_QUALITY)
			{
				float of_dx_deb = ano_of.of2_dx_fix - s_of_bias_x;
				float of_dy_deb = ano_of.of2_dy_fix - s_of_bias_y;

				ano_of.DISTANCE_X = ano_of.DISTANCE_X+of_dx_deb*0.005f;
				ano_of.DISTANCE_Y = ano_of.DISTANCE_Y+of_dy_deb*0.005f;

				ano_of.earth_x = ano_of.earth_x + (of_dx_deb*0.005f*Cos_Yaw_01 + of_dy_deb*0.005f*Sin_Yaw_01 );
				ano_of.earth_y = ano_of.earth_y + (of_dy_deb*0.005f*Cos_Yaw_01 - of_dx_deb*0.005f*Sin_Yaw_01 );
			}
			ano_of.earth_x_ture  =  ano_of.earth_y;
			ano_of.earth_y_ture  =  -ano_of.earth_x;
			Ctrler.locxPID.FB= ano_of.earth_x_ture ;
			Ctrler.locyPID.FB= ano_of.earth_y_ture ;
		}
		//                                                     |
		//                                                     | y(����)

		/* ADR-0011 Phase 3 (CAL_AIRBORNE_HOVER_TRIM) — closed-form accel-bias LS trim.
		 * Runs only while flying. The body-frame accel measurement in static hover is
		 *     a_meas_body = 1000 * Gravity_Body + b_a   (mg, Gravity_Body is unit vector)
		 * The estimator reconstructs a_meas = Lin_Acc + 1000*Gravity_Body (gravity-removed
		 * reading + gravity vector = raw body accel) and applies the LS step
		 *     b_a <- b_a + mu * (g_ref_world - a_meas_world)
		 * where g_ref_world = (0, 0, +1000) mg and a_meas_world is rotated to world frame.
		 * Approximated here as body-frame directly (small-angle assumption in stable hover;
		 * the rotation error is <5 % at 10 deg tilt and the slow mu keeps convergence well-
		 * behaved under that error). */
		if (flight_phase == FLIGHT_PHASE_FLYING) {
			float a_meas_x = Lin_Acc_X_body + 1000.0f * Gravity_Body_X;
			float a_meas_y = Lin_Acc_Y_body + 1000.0f * Gravity_Body_Y;
			float a_meas_z = Lin_Acc_Z_body + 1000.0f * Gravity_Body_Z;
			CalTrim_Step(&s_cal_trim,
			             0.0f, 0.0f, 1000.0f,
			             a_meas_x, a_meas_y, a_meas_z,
			             1U);
			if (s_cal_trim.state == CAL_TRIM_STATE_SETTLED) {
				g_cal_health |= 0x08U;
			} else if (s_cal_trim.state == CAL_TRIM_STATE_DEGRADED) {
				g_cal_health |= 0x10U;
			}
		}

		/* ADR-0011 Phase 4 (CAL_HOT_HOVER) — gyro hot-bias FSM. Always ticked so the
		 * quiescence gate can detect a still-window anywhere in the flight. */
		{
			uint8_t rc_q = !RCInput_IsActive(RC_AXIS_THR) &&
			               !RCInput_IsActive(RC_AXIS_PITCH) &&
			               !RCInput_IsActive(RC_AXIS_ROLL) &&
			               !RCInput_IsActive(RC_AXIS_YAW);
			CalHot_Step(&s_cal_hot,
			            Gyro_X_Real, Gyro_Y_Real, Gyro_Z_Real,
			            Lin_Acc_X_body, Lin_Acc_Y_body,
			            (uint8_t)(flight_phase == FLIGHT_PHASE_FLYING),
			            rc_q);
			if (s_cal_hot.rejected) {
				g_cal_health |= 0x40U;  /* HOT_REJECTED — sticky until next quiescent cycle */
			} else if (s_cal_hot.state == CAL_HOT_STATE_WAIT_STILL && !s_cal_hot.cleared) {
				/* One-shot commit fired: HOT_HOVER_OK. */
				g_cal_health |= 0x20U;
				s_cal_hot.cleared = 1U;
			}
		}
		//�����Դ�ʩ  //t265���װ�Ĵ���
//		if(linux_data.t265posy>-1000000.0f && linux_data.t265posy<1000000.0f)
//		{
//			Ctrler.locxPID.FB= linux_data.t265posy  ;  //��������ϵ��	
//		}
//		if(linux_data.t265posx>-1000000.0f && linux_data.t265posx<1000000.0f)
//		{
//			Ctrler.locyPID.FB= -linux_data.t265posx ;
//		}
		
	//�����ٶȷ���                                                    ^y(����)
//	  Ctrler.locxsPID.FB= ano_of.of2_dy;                           |
//    Ctrler.locysPID.FB= -ano_of.of2_dx;   //��������ϵ��          |-->x(����)          

		Ctrler.locxsPID.FB= (ano_of.of2_dy) *Cos_Yaw_01 +(-ano_of.of2_dx)*Sin_Yaw_01;
    Ctrler.locysPID.FB=  (-ano_of.of2_dx) * Cos_Yaw_01 - (ano_of.of2_dy)*Sin_Yaw_01; //��������ϵ��
	
	  /* Altitude sanity gate (of_alt_cm is cm, u16). Three layers (ADR-0011 Z-gate),
	   * mirroring PX4/DJI altitude filtering:
	   *   1. median-of-3 on the raw sample — a lone spike (in-band or the 0xFFFF
	   *      no-reading) is never the median of three, so it is dropped before the
	   *      band/jump gates ever see it.
	   *   2. band gate — 5..500 cm (the 500 upper bound rejects 65535 no-reading;
	   *      the 5 cm floor rejects sub-band 1 cm dropouts).
	   *   3. rate-aware per-tick jump gate — baseline 0.05 m/tick (=10 m/s, still
	   *      ~10x this drone's ~1 m/s climb), widened by the commanded vertical rate
	   *      so legit fast ascents are not clipped: gate = 0.05 + 0.15*|Z_ratePID.Des|,
	   *      capped at 0.20 m/tick. The 20-reject (100 ms) force-resync escape is kept
	   *      so a genuine sustained level change is not locked out. */
	  {
			static u16 s_alt_reject_cnt = 0U;
			static u16 s_alt_hist[3] = {0U, 0U, 0U};
			static uint8_t s_alt_hist_n = 0U;
			u16 a0, a1, a2, alt_med;

			/* Layer 1: median-of-3. */
			s_alt_hist[2] = s_alt_hist[1];
			s_alt_hist[1] = s_alt_hist[0];
			s_alt_hist[0] = ano_of.of_alt_cm;
			if (s_alt_hist_n < 3U) s_alt_hist_n++;
			if (s_alt_hist_n >= 3U)
			{
				a0 = s_alt_hist[0]; a1 = s_alt_hist[1]; a2 = s_alt_hist[2];
				alt_med = (a0 > a1) ? ((a1 > a2) ? a1 : ((a0 > a2) ? a2 : a0))
				                    : ((a0 > a2) ? a0 : ((a1 > a2) ? a2 : a1));
			}
			else
			{
				alt_med = ano_of.of_alt_cm;  /* warmup: not enough history yet */
			}

			/* Layer 2: band gate. */
			if( alt_med >= 5U && alt_med <= 500U )
			{
				float h_new = alt_med*0.01f*Cos_roll_01*Cos_pitch_01;
				/* Layer 3: rate-aware jump gate. */
				float gate = 0.05f + 0.15f * fabsf(Ctrler.Z_ratePID.Des);
				if (gate > 0.20f) gate = 0.20f;
				if( fabsf(h_new - ano_of.of2_raw_h) < gate || s_alt_reject_cnt >= 20U )
				{
					ano_of.of2_raw_h = h_new;
					s_alt_reject_cnt = 0U;
					s_alt_valid_tick = 1U;   /* P0 Finding 2: sample accepted this tick */
				}
				else
				{
					s_alt_reject_cnt++;
					s_alt_valid_tick = 0U;   /* P0 Finding 2: jump-rejected - hold */
				}
			}
			else
			{
				s_alt_valid_tick = 0U;   /* P0 Finding 2: out of band (>5 m / 0xFFFF) - hold */
			}
		}
	
	ano_of.of2_h =ano_of.of2_raw_h; 
	if (s_alt_valid_tick) { /* P0 Finding 2: valid sample - else hold last FB */
	                      /* and never publish the fake-zero d(frozen h)/dt.  */
	ano_of.of2_h_v = (ano_of.of2_h - ano_of.of2_last_h )/(0.005f);  //����ʱ��5ms
	ano_of.of2_last_h = ano_of.of2_h;
  ano_of.of2_h_f2_v  = ano_of.of2_h_f2_v  *0.9f +ano_of.of2_h_v *0.1f;
	 
	Ctrler.Z_posPID.FB =  ano_of.of2_h;
	Ctrler.Z_ratePID.FB = ano_of.of2_h_f2_v ;
	}
	
	////////////////////��̬���Ƕ�ֵ����//////////////////////////////////  
	
	{
		float imu_pit, imu_rol, imu_yaw;
		taskENTER_CRITICAL();
		imu_pit = imu_data.pit;
		imu_rol = imu_data.rol;
		imu_yaw = imu_data.yaw;
		taskEXIT_CRITICAL();

		Ctrler.pitchPID.FB = -imu_pit;
		Ctrler.rollPID.FB  =  imu_rol;
		Ctrler.yawPID.FB   = -imu_yaw;
	}

	// Phase-1 gyro low-pass (default pass-through; enable via CMD 0x15). Filters the rate FB that
	// the rate PID, MRAC, and the system-ID frame all consume — see API/gyro_filter.c / ADR-0004.
	Ctrler.gyroyPID.FB = GyroFilter_Apply(GYRO_FILT_PITCH, -Gyro_Y_Real*RAD2DEG);
	Ctrler.gyroxPID.FB = GyroFilter_Apply(GYRO_FILT_ROLL,   Gyro_X_Real*RAD2DEG);
	Ctrler.gyrozPID.FB = GyroFilter_Apply(GYRO_FILT_YAW,   -Gyro_Z_Real*RAD2DEG);
	
}
/*************************************************************************
�� �� ����void Update_Motor(void);
�������ܣ������ĸ������״̬
��    ע��
*************************************************************************/
void Update_Motor(void)
{
    static uint8_t  s_land_init    = 0U;
    static int      s_stable_ticks = 0;
    static uint16_t s_land_timeout = 0U;
    /* Bench-mode height zero: captures the fixture resting height when bench mode
     * activates (0→1 transition). The Z position PID then sees (of2_h - offset),
     * so the drone treats its current height as zero — prop wash displaces it by
     * ±Δ and the controller does not fight to return to a virtual ground.
     * Only captures on 0→1 transition; the offset holds until bench mode deactivates. */
    static float  s_bench_z_offset = 0.0f;
    static uint8_t s_bench_was_active = 0U;

    if (bench_mode_active && !s_bench_was_active) {
        s_bench_z_offset = Ctrler.Z_posPID.FB;   /* ~0.4 m on the 4DOF fixture */
    }
    s_bench_was_active = bench_mode_active;
    if (bench_mode_active) {
        Ctrler.Z_posPID.FB  -= s_bench_z_offset;  /* zero out the fixture resting height */
        Ctrler.Z_ratePID.FB  = 0.0f;              /* freeze Z rate so controller stays calm */
    }

    FlightState_t state = FlightFSM_GetState();

    /* Motor bench-test override (CMD 0x16): drive ONE chosen motor to a commanded CCR
     * for the thrust-stand experiment. Strictly DISARMED-only, with a dead-man — if the
     * dashboard stops sending heartbeats (watchdog exceeds the window) or anything leaves
     * DISARMED, motors are zeroed and test mode exits. RC/arming stays the final authority.
     * Set_PWM_Motors() applies the [2000,4000] clamp. See docs/bench_characterization.md. */
    if (motor_test_active)
    {
        if (state != FLIGHT_STATE_DISARMED ||
            ++motor_test_watchdog > MOTOR_TEST_DEADMAN_TICKS)
        {
            motor_test_active = 0U;
            Set_Zero_Motors();
            return;
        }
        mymotor.motor1 = (motor_test_id == 1U) ? (short)motor_test_ccr : Motor_PWM_ZERO;
        mymotor.motor2 = (motor_test_id == 2U) ? (short)motor_test_ccr : Motor_PWM_ZERO;
        mymotor.motor3 = (motor_test_id == 3U) ? (short)motor_test_ccr : Motor_PWM_ZERO;
        mymotor.motor4 = (motor_test_id == 4U) ? (short)motor_test_ccr : Motor_PWM_ZERO;
        Set_PWM_Motors();
        return;
    }

    if (state == FLIGHT_STATE_ARMED)
    {
        if (flight_phase == FLIGHT_PHASE_LANDING)
        {
            if (!s_land_init)
            {
                s_stable_ticks = 0;
                s_land_timeout = 0U;
                s_land_init    = 1U;
            }

            /* PID-controlled descent: Z_posPID.Des ramps down in case_Update_height_Des
             * at 0.30 m/s; rate cascade follows; Throttle_out drives motors to ground.
             * Integrator winds negative during descent so Throttle_out is already below
             * hover at touchdown — no motor spike when LANDED fires. */
            Set_PWM_Motors();

            /* Touchdown detection: rate stable for 0.25 s AND near ground.
             * Below 0.20 m a 0.15 m/s sink bias is active, so widen threshold to
             * 0.08 m/s — the drone decelerates through that on contact.
             * Above 0.20 m keep tight (0.02 m/s) to avoid false triggers mid-descent.
             * Safety net: force disarm after LAND_MAX_TICKS (10 s) regardless. */
            {
                float rate_thr = (Ctrler.Z_posPID.FB < 0.20f) ? 0.08f : 0.02f;
                if (fabsf(Ctrler.Z_ratePID.FB) < rate_thr)
                    s_stable_ticks++;
                else
                    s_stable_ticks = 0;
            }

            s_land_timeout++;

            if ((s_stable_ticks >= 10 && Ctrler.Z_posPID.FB < 0.15f) ||
                s_land_timeout >= LAND_MAX_TICKS)
            {
                s_stable_ticks    = 0;
                s_land_timeout    = 0U;
                s_land_init       = 0U;
                s_land_sink_bias  = 0.0f;
                flight_phase      = FLIGHT_PHASE_LANDED;
                FlightFSM_Event(FLIGHT_EVENT_DISARM_REQUEST);
                Set_Zero_Motors();
            }
        }
        else if (flight_phase == FLIGHT_PHASE_GROUND_IDLE)
        {
            /* Auto-detect takeoff: transition to FLYING once the OF sensor reads > 0.2 m above
             * the zeroed height. Bench mode (CMD 0x07) does NOT block this — the height
             * offset is captured on bench activation so of2_h is already relative to the fixture
             * resting height. The controller therefore does not fight prop wash at any altitude.
             * Throttle_cap_active (CMD 0x08) is also independent and does not affect this. */
            /* P0 Finding 3: height alone must not leave GROUND_IDLE — a slow hand-lift of an
             * armed aircraft ramps of2_h past every alt gate, so gate the transition itself
             * on THR >= 20% or an executing TWC policy, matching the IDLE interlock below. */
            if (Ctrler.Z_posPID.FB > 0.2f &&
                (TWC.execute || RCInput_Get(RC_AXIS_THR) >= 0.2f))
                flight_phase = FLIGHT_PHASE_FLYING;

            /* IDLE motors: hold until pilot or policy pushes THR above 20%. */
            if (!TWC.execute && RCInput_Get(RC_AXIS_THR) < 0.2f)
                Set_IDLE_Motors();
            else if (SDK_DelayWakeFlag == 1)
                Set_IDLE_Motors();
            else
                Set_PWM_Motors();
        }
        else if (flight_phase == FLIGHT_PHASE_FLYING)
        {
            /* Normal FLYING state: drive motors. Bench mode height zero is already applied
             * at the top of this function so the controller stays calm throughout. */
            Set_PWM_Motors();
        }
        /* FLIGHT_PHASE_LANDED: disarm was issued this tick; do nothing — motors
         * were already zeroed by the LANDING block that triggered this transition. */
    }
    else if (state == FLIGHT_STATE_EMERGENCY)
    {
        s_land_init      = 0U;
        s_stable_ticks   = 0;
        s_land_timeout   = 0U;
        s_land_sink_bias = 0.0f;
        /* FIX 2026-09-13 (autonomous-debug Tier 1.2): zero PID integrators every tick
         * in EMERGENCY. Without this, gyro-bias feedthrough and steady-state OF drift
         * wind roll/pitch/yaw SumE to the ±SumEMax clamp during long bench sits in
         * DangerousStop (sbus_lost or RC mode switch LOW). The transition path
         * EMERGENCY→DISARMED does call Clear_Structure on the first DISARMED tick so
         * arming after recovery is safe — but in-EMERGENCY windup is wasted CPU and
         * makes cold-boot symptoms harder to diagnose (SumE != 0 in EMERGENCY masks
         * the absence of a real fix in some metrics). Mirrors the DISARMED branch. */
        Clear_Structure();
        Set_Zero_Motors();
    }
    else   /* DISARMED */
    {
        s_land_init      = 0U;
        s_stable_ticks   = 0;
        s_land_timeout   = 0U;
        s_land_sink_bias = 0.0f;
        TWC.execute = 0U;
        sbus_flyup_trigger = 0U;
        SDK_StateMachine_Init();
        Clear_Structure();
        Set_Zero_Motors();
    }
}
//������У׼�������
//   if( DroneStatus.FlyMode == FlyMode_SDK   )//SDKģʽ	
//			{
//				 if(sbus_channel[5] >=1000) 
//				 {
//				   mymotor.motor1 = 4000;
//				   mymotor.motor2 = 4000;
//					 mymotor.motor3 = 4000;
//					 mymotor.motor4 = 4000;
//				 }
//				 else if(sbus_channel[5] <=500)
//				 {
//				   mymotor.motor1 = 2000;
//				   mymotor.motor2 = 2000;
//					 mymotor.motor3 = 2000;
//					 mymotor.motor4 = 2000;
//				 }
//				Set_PWM_Motors();
//			}
//		else 
//		{
//		  Clear_Structure();
//			Set_Zero_Motors();
//		}
//}
/*************************************************************************
�� �� ����void Compute_Motor(void);
�������ܣ�����PID����
��    ע��
*************************************************************************/
void Compute_Motor(void)
{
////////////////����߶�����//////////////////////////////////////////////////////////
				
	Update_Des(case_Update_height_Des);  
	cnt_h++;
	if(cnt_h>=2)
	{
		{   /* P0 Finding 2: ToF out of range - freeze the Z position integrator
		     (its FB is the frozen height, so letting SumE run would wind the
		     cascade that feeds Z_ratePID.Des). */
			float zp_sumE = Ctrler.Z_posPID.SumE;
			float zp_Ui   = Ctrler.Z_posPID.Ui;
			ComputePID(&Ctrler.Z_posPID);
			if (!s_alt_valid_tick) { Ctrler.Z_posPID.SumE = zp_sumE; Ctrler.Z_posPID.Ui = zp_Ui; }
		}
		cnt_h=0;
	}
  Update_Des(case_Update_v_h_Des);
	{   /* P0 Finding 2: ToF out of range - freeze the Z-rate integrator while
	     still running P/D on the held FB, so the manual height-rate loop cannot
	     wind throttle up without bound (verdict: unbounded climb at >5 m AGL). */
		float zr_sumE = Ctrler.Z_ratePID.SumE;
		float zr_Ui   = Ctrler.Z_ratePID.Ui;
		ComputePID(&Ctrler.Z_ratePID);
		if (!s_alt_valid_tick) { Ctrler.Z_ratePID.SumE = zr_sumE; Ctrler.Z_ratePID.Ui = zr_Ui; }
	}


	cnt_loc++;
	if(cnt_loc>=2)
	{
	cnt_loc=0;
	/* FIX 2026-09-13: ARM rising-edge world-origin rebase.
	 * The cold-boot sync (earlier in this function) syncs Des=FB at the
	 * rising edge of g_estimator_ready — but that fires at boot, before
	 * the OF has integrated any drift. After a long disarmed sit on the
	 * bench, FB can drift to ~+200/-188 cm while the drone is physically
	 * still. Arming then opens the position loop with a -195/+188 error,
	 * which immediately saturates the SumE and flies the drone into a
	 * wall. Fix it the same way the dashboard "Reset World Origin" button
	 * does (CMD 0x10): on the ARM 0→1 edge, rebase FB/Des/OF bias to zero
	 * so the position loop opens with zero error and the drone physically
	 * holds until the pilot commands throttle. The pilot can still hit
	 * the dashboard button mid-flight to re-anchor the path. */
	{
		static uint8_t s_prev_armed = 0U;
		uint8_t armed_now = (DroneStatus.ARM_Status == Armed) ? 1U : 0U;
		if (armed_now && !s_prev_armed) {
			Reset_World_Origin();
			/* Also clear any position-PID integral the previous session
			 * left behind: with Des=FB=0, the SumE the only thing that
			 * can drive a step jump on the first armed tick. */
			Ctrler.locxPID.SumE  = 0.0f;
			Ctrler.locyPID.SumE  = 0.0f;
			Ctrler.locxsPID.SumE = 0.0f;
			Ctrler.locysPID.SumE = 0.0f;
			/* FIX 2026-09-13 (Tier 2): bias auto-snap on ARM 0→1.
			 * Reset_World_Origin() zeroes s_of_bias_x/y to satisfy
			 * the "position re-drift within 30 s" fix. Re-snap from the
			 * current OF sample so the EMA starts from a clean value.
			 * In Mode 2 (EKF): re-initialize the KF velocity/bias from
			 * the current OF reading so the position estimate is warm
			 * at ARM rather than cold-starting from (0,0,0,0).
			 * Gated on OF quality. */
			if (ano_of.of_quality >= OF_MIN_QUALITY) {
				s_of_bias_x = (float)ano_of.of2_dx_fix;
				s_of_bias_y = (float)ano_of.of2_dy_fix;
				s_of_bias_seeded = 1U;
				/* Mode 2: inject one debiased OF update into the KF
				 * to warm-start its vel/bias states at ARM */
				if (g_of_bias_mode == 2U && s_ekf_of_inited) {
					float ofx = ((float)ano_of.of2_dx_fix - s_of_bias_x) * 0.01f;
					float ofy = ((float)ano_of.of2_dy_fix - s_of_bias_y) * 0.01f;
					/* One predict+update warms the KF state from current OF */
					EkfOf_Predict(&s_ekf_of, 0.005f);
					EkfOf_Update(&s_ekf_of, ofx, ofy);
				}
			}
		}
		s_prev_armed = armed_now;
	}
	/* Tier 1.0 (2026-09-13): only run position/velocity PIDs while armed.
	   While disarmed Clear_Structure zeros SumE every tick, but Compute_Motor
	   runs all PIDs first, so position SumE re-clamped to SumEMax=200 from OF
	   drift within one tick. Gating keeps locx/y/xs/ys.SumE at 0 in disarmed.
	   cnt_loc=0 sits outside the arm gate so the divider keeps running even
	   while disarmed (otherwise cnt_loc grows unbounded and the gate fires
	   every 256th tick on a uint8 wrap). */
	if (DroneStatus.ARM_Status != 0U) {
	Update_Des(case_Update_loc_Des);
	ComputePID(&Ctrler.locxPID);
	ComputePID(&Ctrler.locyPID);

  Update_Des(case_Update_v_loc_Des);
	SDK_Set_V_Loc();//������

	ComputePID(&Ctrler.locxsPID);
	ComputePID(&Ctrler.locysPID);
  }
	}
	
//////////////////////������̬����////////////////////////////////////////////////////////////
	
	Update_Des(case_Update_pitrol_Des);  //����pit��roll�Ƕ�ֵ
	ComputePID(&Ctrler.pitchPID);
	ComputePID(&Ctrler.rollPID);
	
	Update_Des(case_Update_yaw_Des);    //����yaw�ĽǶ�
	ComputeYawPID(&Ctrler.yawPID);
	
	
	Update_Des(case_Update_gyro_Des);   //���½��ٶ�
	
	SDK_Set_Gyroz();

	// SysID excitation (ADR-0004): tick the signal generator + safety FSM, then SUPERIMPOSE the
	// excitation onto the active axis's RATE setpoint with += (closed-loop SysID). The outer
	// angle/position cascade stays live, so position hold keeps the drone on station (it wiggles in
	// place instead of translating open-loop and walking out of the green zone). Pitch/roll/yaw here;
	// Z is injected at the Z_ratePID.Des site. No-op while the FSM is IDLE.
	// NOTE: the rate setpoint now = outer-loop output + dither, so the logged `r`/`u` are closed-loop
	// signals. Plant ID is done offline with the direct method (Phi_xu/Phi_uu) on u_nom+u_ad -> x.
	SysID_Update();
	if (SysID_IsAxisActive(SYSID_AXIS_PITCH)) Ctrler.gyroyPID.Des += SysID_GetRateSetpoint(SYSID_AXIS_PITCH);
	if (SysID_IsAxisActive(SYSID_AXIS_ROLL))  Ctrler.gyroxPID.Des += SysID_GetRateSetpoint(SYSID_AXIS_ROLL);
	if (SysID_IsAxisActive(SYSID_AXIS_YAW))   Ctrler.gyrozPID.Des += SysID_GetRateSetpoint(SYSID_AXIS_YAW);

	ComputePID(&Ctrler.gyroxPID);
	ComputePID(&Ctrler.gyroyPID);
	ComputePID(&Ctrler.gyrozPID);
	
	// Execute MRAC after all PID controllers have computed their nominal outputs (u_nom)
	// MRAC uses the current PID rates, references, and nominal outputs to learn and compute u_ad.
	MRAC_Control(&Ctrler);
	
 

	 //Throttle_th=2800+(16.70f-real_voltage)*105.5f;  //4s+d435i+t265+orin
	 // Fixture (extendable metal) adds load. 3200 covers ~+250 g fixture at 4S full;
	 // free-flight hover is 2950. PWM ceiling is 4000 - anything above risks ESC saturation.
	 Throttle_th = bench_mode_active ? (short)3200 : (short)2950;

#if ENABLE_MRAC_OUTPUT_INJECTION == 1
	// Runtime shadow-mode gate (mrac_flags.output_injection_on, CMD 0x0F idx 10).
	// When OFF the motors see pure PID even though MRAC keeps learning/logging u_ad.
	if (mrac_flags.output_injection_on) {
		// Inject MRAC adaptive signals.
		// u_total = u_nom + (u_ad * scaling_factor)
		// NaN/Inf guard: if u_ad is not finite (e.g. due to diverged adaptive weights),
		// fall back to zero correction so the PID baseline always reaches the motors.
		float mrac_z     = mrac_state.z_rate.u_ad * mrac_config_z.mrac_to_mixer;
		float mrac_roll  = mrac_state.roll.u_ad  * mrac_config_roll.mrac_to_mixer;
		float mrac_pitch = mrac_state.pitch.u_ad * mrac_config_pitch.mrac_to_mixer;
		float mrac_yaw   = mrac_state.yaw.u_ad   * mrac_config_yaw.mrac_to_mixer;
		if (!isfinite(mrac_z))     mrac_z     = 0.0f;
		if (!isfinite(mrac_roll))  mrac_roll  = 0.0f;
		if (!isfinite(mrac_pitch)) mrac_pitch = 0.0f;
		if (!isfinite(mrac_yaw))   mrac_yaw   = 0.0f;
		Throttle_out = Ctrler.Z_ratePID.U + Throttle_th + mrac_z;
		u_gyrox      = Ctrler.gyroxPID.U  + mrac_roll;
		u_gyroy      = -(Ctrler.gyroyPID.U + mrac_pitch); // Motor mixer needs gyroy reversed
		u_gyroz      = Ctrler.gyrozPID.U  + mrac_yaw;
	} else {
		// Runtime shadow mode: MRAC computes silently, motors see normal PID output.
		Throttle_out = Ctrler.Z_ratePID.U + Throttle_th;
		u_gyrox  = Ctrler.gyroxPID.U;
		u_gyroy  = -Ctrler.gyroyPID.U;
		u_gyroz  = Ctrler.gyrozPID.U;
	}
#else
	// Compile-time shadow mode: MRAC computes silently, but motors only see normal PID output.
    Throttle_out=Ctrler.Z_ratePID.U + Throttle_th;

	u_gyrox  = Ctrler.gyroxPID.U ;
	u_gyroy  = -Ctrler.gyroyPID.U;
	u_gyroz  = Ctrler.gyrozPID.U ;
#endif
	{
		float pwm_lo = 2000.0f + gs_throttle_min_pct * 2000.0f;
		float pwm_hi = 2000.0f + gs_throttle_max_pct * 2000.0f;
		if (pwm_hi < pwm_lo) {
			float t = pwm_hi;
			pwm_hi = pwm_lo;
			pwm_lo = t;
		}
		Throttle_out = Constrain_Float(Throttle_out, pwm_lo, pwm_hi);
	}
	
	// CONSTRAINT: Keep these mixer signs in sync with the physical motor map and pwm.h channel mapping.
	// WHY: Sign or channel drift here can invert closed-loop attitude response.
	mymotor.motor1= Throttle_out
									-u_gyroy//pitch
									-u_gyrox//
									-u_gyroz;//yaw  // FIX 2026-09-10: M1 is BR,CW -> -yaw (decrease for +u_gyroz)

	mymotor.motor2= Throttle_out
									+u_gyroy//pitch
									+u_gyrox//roll
									-u_gyroz;//yaw  // FIX 2026-09-10: M2 is FL,CW -> -yaw

	mymotor.motor3= Throttle_out
									-u_gyroy//pitch
									+u_gyrox//roll
									+u_gyroz;//yaw  // FIX 2026-09-10: M3 is BL,CCW -> +yaw

  mymotor.motor4= Throttle_out
									+u_gyroy//pitch
									-u_gyrox//roll
									+u_gyroz;//yaw  // FIX 2026-09-10: M4 is FR,CCW -> +yaw
	
	/* Shadow thrust estimators (200 Hz, after motor mixer, before Set_PWM_Motors).
	 * Three models: empirical (PWM→thrust bench LUT), blade-element (RPM→thrust),
	 * IMU-derived (accel projection). Telemetry-only, no control feedback. */
	{
		float motor_pwm[4];
		uint16_t motor_rpm[4];
		
		motor_pwm[0] = (float)mymotor.motor1;
		motor_pwm[1] = (float)mymotor.motor2;
		motor_pwm[2] = (float)mymotor.motor3;
		motor_pwm[3] = (float)mymotor.motor4;
		
		motor_rpm[0] = RPM_Get(0);
		motor_rpm[1] = RPM_Get(1);
		motor_rpm[2] = RPM_Get(2);
		motor_rpm[3] = RPM_Get(3);
		
		ThrustEst_Update(motor_pwm, motor_rpm, imu_data.a_acc[_Z], imu_data.pit, imu_data.rol);
	}
			
}
/*************************************************************************
�� �� ����void Update_Des(unsigned char which_level);
�������ܣ���������
��    ע��
*************************************************************************/
float des_pitch = 0;
float	des_roll = 0;
void Update_Des(unsigned char which_level)
{
//TWC.target_x = 0;
//TWC.target_y = 0;
//TWC.target_z = 0;
TWC.world_x = Ctrler.locxPID.FB;
TWC.world_y = Ctrler.locyPID.FB;
TWC.world_z = Ctrler.Z_posPID.FB;
//TWC.execute = 0;
//TWC.set_yaw = 0;
TWC.real_yaw = Ctrler.yawPID.FB; //�ṹ���Ա������ʼ��һ��Ҫд�ں����ڲ�������һ��ʼ�ͳ�ʼ��

	if (TWC.execute == 1) {
		float dx = (Ctrler.locxPID.FB - TWC.target_x) * 0.01f; /* cm → m */
		float dy = (Ctrler.locyPID.FB - TWC.target_y) * 0.01f; /* cm → m */
		float dz = Ctrler.Z_posPID.FB - TWC.target_z;           /* already m */
		float dist = sqrtf(dx * dx + dy * dy + dz * dz);
		TWC_arrived = (dist < 0.15f) ? 1U : 0U;
	} else {
		TWC_arrived = 0U;
	}

  /* Init to 1 (not 0) so the existing capture-on-stick-released state machine
   * fires on the very first iteration at boot when sticks are centered.
   * Before this (2026-09-13), is_last_*_valid was 0 at boot, so the condition
   * `is_last_*_valid && (!RCInput_IsActive(...))` was false on every tick
   * until the stick was actually moved, leaving Des stuck at whatever the
   * BSS RAM happened to contain — typically a previous flight's captured FB.
   * That stale Des was the root cause of the OF-hold cold-boot saturation
   * cascade / wall-crash. Keeping the 1 means: first tick captures Des=FB,
   * and the rest of the state machine behaves exactly as before. */
  static unsigned char is_last_thr_valid = 1U, is_last_yaw_valid = 1U, is_last_pitch_valid = 1U, is_last_roll_valid = 1U;
	switch(which_level)
	{
		
		//////////////////////�߶�����/////////////////////////////////////////////////////
		
		case case_Update_height_Des://���¸߶�����

			/* SBUS ch8 rising-edge preset-path trigger — handler to be added.
			 * sbus_path_trigger is set by RemoterTask; clear it here for now. */
			if (sbus_path_trigger)
			{
				/* TODO: launch preset path sequence */
				sbus_path_trigger = 0U;
			}

			/* SBUS ch7 fly-up: release authority so physical RC takeover detection
			 * works normally during flight. TWC.execute=1 gates the IDLE block. */
			if (sbus_flyup_trigger)
			{
				sbus_flyup_trigger = 0U;
				RCInput_SetAuthority(0U);   /* release IDLE throttle lock */
				TWC.target_x = TWC.world_x;
				TWC.target_y = TWC.world_y;
				TWC.target_z = 0.5f;
				TWC.execute  = 1U;
			}

			/* LANDING: ramp Z setpoint down at 0.30 m/s (LAND_DES_STEP at 200 Hz).
			 * Snap Des = min(Des, FB) so a setpoint above current altitude cannot
			 * pull the drone upward at landing entry (case A fix). */
			if (flight_phase == FLIGHT_PHASE_LANDING)
			{
				TWC.execute = 0U;
				if (Ctrler.Z_posPID.Des > Ctrler.Z_posPID.FB)
					Ctrler.Z_posPID.Des = Ctrler.Z_posPID.FB;
				Ctrler.Z_posPID.Des -= LAND_DES_STEP;
				if (Ctrler.Z_posPID.Des < 0.0f) Ctrler.Z_posPID.Des = 0.0f;
				break;
			}
			/* LANDED: hold setpoint at zero while disarm completes this tick. */
			if (flight_phase == FLIGHT_PHASE_LANDED)
			{
				TWC.execute = 0U;
				Ctrler.Z_posPID.Des = 0.0f;
				break;
			}

			/* GROUND_IDLE hold: pin Z setpoint until pilot pushes THR above 20%. */
			if (flight_phase == FLIGHT_PHASE_GROUND_IDLE && !TWC.execute && RCInput_Get(RC_AXIS_THR) < 0.2f)
			{
				Ctrler.Z_posPID.Des = Ctrler.Z_posPID.FB;
				break;
			}

			/* FLY mode (normal) */
			if (is_last_thr_valid && (!RCInput_IsActive(RC_AXIS_THR)))
				Ctrler.Z_posPID.Des = Ctrler.Z_posPID.FB;

			is_last_thr_valid = RCInput_IsActive(RC_AXIS_THR);

			if (TWC.execute == 1)
			{
				/* Rate-limit Z setpoint to 0.5 m/s (0.005 m/cycle at ~100 Hz)
				 * to prevent overshoot when target is far above current altitude. */
				float z_err = TWC.target_z - Ctrler.Z_posPID.Des;
				if      (z_err >  0.005f) Ctrler.Z_posPID.Des += 0.005f;
				else if (z_err < -0.005f) Ctrler.Z_posPID.Des -= 0.005f;
				else                      Ctrler.Z_posPID.Des  = TWC.target_z;
			}

       break;
			
		case case_Update_v_h_Des://������ֱ�ٶ�����
			/* Landing: THR stick must not override PID cascade — rate setpoint
			 * comes exclusively from Z_posPID.U throughout the descent. */
			if (flight_phase == FLIGHT_PHASE_LANDING || flight_phase == FLIGHT_PHASE_LANDED)
			{
				Ctrler.Z_ratePID.Des = Ctrler.Z_posPID.U;
				/* Progressive sink bias: once setpoint has reached the floor, ramp up
				 * commanded sink rate while the drone is slow (stuck in ground effect).
				 * 0.001 m/s per tick at 200 Hz → reaches 0.15 m/s in 0.75 s, max 0.40 m/s.
				 * Adapts to any battery voltage — no altitude threshold needed. */
				if (Ctrler.Z_posPID.Des <= 0.01f)
				{
					if (fabsf(Ctrler.Z_ratePID.FB) < 0.10f)
						s_land_sink_bias += 0.001f;
					if (s_land_sink_bias > 0.40f) s_land_sink_bias = 0.40f;
					Ctrler.Z_ratePID.Des -= s_land_sink_bias;
				}
			}
			else if (flight_phase == FLIGHT_PHASE_GROUND_IDLE && !TWC.execute && RCInput_Get(RC_AXIS_THR) < 0.2f)
				Ctrler.Z_ratePID.Des = 0.0f;
			else if(RCInput_IsActive(RC_AXIS_THR))
 				Ctrler.Z_ratePID.Des = RCInput_Get(RC_AXIS_THR) * gs_max_vertical_speed_mps ;
			else
				Ctrler.Z_ratePID.Des = Ctrler.Z_posPID.U;
       break;
			
		////////////////////ˮƽλ������////////////////////////////////////////////////////	
			
		case case_Update_loc_Des://����λ������
			
			if( is_last_roll_valid && (!RCInput_IsActive(RC_AXIS_ROLL)) )
			{
				Ctrler.locxPID.Des = Ctrler.locxPID.FB;
			}
			if( is_last_pitch_valid && (!RCInput_IsActive(RC_AXIS_PITCH)) )
			{
				Ctrler.locyPID.Des = Ctrler.locyPID.FB;
			}

			is_last_pitch_valid = RCInput_IsActive(RC_AXIS_PITCH);
			is_last_roll_valid = RCInput_IsActive(RC_AXIS_ROLL);
			/* Manual pitch/roll input cancels TWC XY target so the drone
			 * does not snap back to the fly-up launch point after ch7. */
			if (RCInput_IsActive(RC_AXIS_ROLL) || RCInput_IsActive(RC_AXIS_PITCH))
				TWC.execute = 0U;
			if(TWC.execute == 1){Ctrler.locxPID.Des = TWC.target_x;Ctrler.locyPID.Des = TWC.target_y;}//��Ŀ���ת��
			break;
			
    case case_Update_v_loc_Des://����ˮƽ�ٶ�����

				if(RCInput_IsActive(RC_AXIS_PITCH))
					   Ctrler.locysPID.Des = -RCInput_Get(RC_AXIS_PITCH) * (gs_max_horizontal_speed_mps * 100.0f);
				else if (Ctrler.locyPID.U>120.0f)
						Ctrler.locysPID.Des = 120.0f;
				else if (Ctrler.locyPID.U< -120.0f)
						Ctrler.locysPID.Des = -120.0f;
				else
					Ctrler.locysPID.Des = 	Ctrler.locyPID.U;//����˾Ͷ���
					
				if(RCInput_IsActive(RC_AXIS_ROLL))
					Ctrler.locxsPID.Des = -RCInput_Get(RC_AXIS_ROLL) * (gs_max_horizontal_speed_mps * 100.0f);
				else if(Ctrler.locxPID.U>120.0f)
					Ctrler.locxsPID.Des = 120.0f;
				else if(Ctrler.locxPID.U< -120.0f)
					Ctrler.locxsPID.Des = -120.0f;
				else
					Ctrler.locxsPID.Des = 	Ctrler.locxPID.U;//����˾Ͷ���

       break;
		////////////////////////��̬����////////////////////////////////////////////////////	
		
		case case_Update_pitrol_Des://���� pitch roll����
		{
			/* OF position-hold enable switch on ch6 (OFHOLD_CH = sbus_channel[5]).
			 * HIGH (>1000, ~1694) = OF hold ON; LOW (~306) or signal-lost = ANGLE MODE.
			 * Angle mode bypasses ALL optical-flow loops (position AND velocity): the
			 * sticks command a body-frame lean angle directly and centered sticks = level
			 * (held by the IMU angle loop), so a bad OF velocity/position estimate can no
			 * longer drive tilt - this is the loop that ran the drone away on takeoff.
			 * Default at boot / on failsafe is angle mode: the drone never lifts off into
			 * OF hold unless the pilot deliberately flips ch6 high while already stable. */
			u8 of_hold_on = (sbus_lost == 0U) && (OFHOLD_CH > 1000);
			g_of_hold_active = of_hold_on;   /* publish for Frame 0x01 status.of_hold */
			if (of_hold_on)
			{
				/* BUGFIX 2026-07-20: sign flip on velocity PID outputs.
				 * locxsPID.U > 0 = moving forward (positive X). Desired: decelerate
				 * by pitching nose DOWN (negative des_pitch). Current code produced the
				 * opposite sign -> positive feedback -> runaway on arm.
				 * Fix: negate both velocity PID outputs before the world->body lean
				 * rotation so that positive velocity error produces correcting lean. */
				des_pitch = -(Ctrler.locysPID.U)*Cos_Yaw_01 - (Ctrler.locxsPID.U)*Sin_Yaw_01;
				des_roll  = -(Ctrler.locxsPID.U)*Cos_Yaw_01 + (Ctrler.locysPID.U)*Sin_Yaw_01;
			}
			else
			{
				/* Stick -> accel that maps full deflection to exactly the configured lean
				 * limit (gs_max_pitch/roll_deg), which accel_to_lean_angles then re-clamps.
				 * Sign matches the OF manual path (minus stick). No yaw rotation: body frame. */
				des_pitch = -RCInput_Get(RC_AXIS_PITCH) * tanf(gs_max_pitch_deg*DEG2RAD) * (GRAVITY_MSS*100.0f);
				des_roll  = -RCInput_Get(RC_AXIS_ROLL)  * tanf(gs_max_roll_deg *DEG2RAD) * (GRAVITY_MSS*100.0f);
			}

			accel_to_lean_angles( des_pitch,-des_roll,
			  &Ctrler.pitchPID.Des,&Ctrler.rollPID.Des);
		}
    break;
			
		case case_Update_yaw_Des://����yaw����
			
			if(is_last_yaw_valid && (!RCInput_IsActive(RC_AXIS_YAW)) )
			Ctrler.yawPID.Des = Ctrler.yawPID.FB;
			is_last_yaw_valid = RCInput_IsActive(RC_AXIS_YAW);
		
			if(TWC.execute == 1){Ctrler.yawPID.Des = TWC.set_yaw; }//��Ŀ���ת��
    break;
			
		case case_Update_gyro_Des://���½��ٶ�����

			Ctrler.gyroyPID.Des = Ctrler.pitchPID.U ;
			Ctrler.gyroxPID.Des = Ctrler.rollPID.U ;
			if(RCInput_IsActive(RC_AXIS_YAW))
				Ctrler.gyrozPID.Des = RCInput_Get(RC_AXIS_YAW) * Stick_to_MAX_GyroZ ;
			else
				if(Ctrler.yawPID.U>60.0f)
					Ctrler.gyrozPID.Des  = 60.0f;
				else if(Ctrler.yawPID.U< -60.0f)
					Ctrler.gyrozPID.Des  = -60.0f;
				else
					Ctrler.gyrozPID.Des = Ctrler.yawPID.U ;		
       break;
			
    default: 
       break;
	}
}



/*********�޷�����*******/
float Constrain_Float(float amt, float low, float high)
{
  return ((amt)<(low)?(low):((amt)>(high)?(high):(amt)));
}

float fast_atan(float v)
{
    float v2 = v*v;
    return (v*(1.6867629106f+v2*0.4378497304f)/(1.6867633134f+v2));
}


void accel_to_lean_angles(float acc_tar_forward,float acc_tar_right,float *tar_pitch,float *tar_roll)//cm/s^2
{
  float lim_p = gs_max_pitch_deg;
  float lim_r = gs_max_roll_deg;
	
	float my_Cos_Roll;
	float my_Cos_Pitch;
	my_Cos_Roll = cos(imu_data.rol*DEG2RAD);//*Cos_Roll
	my_Cos_Pitch = cos(imu_data.pit*DEG2RAD);
	
  *tar_pitch=Constrain_Float(
									fast_atan(    acc_tar_forward    *my_Cos_Roll   /(GRAVITY_MSS*100)    )*RAD2DEG,
														-lim_p,lim_p);//pitch
  *tar_roll = Constrain_Float(
									fast_atan(acc_tar_right * my_Cos_Pitch /(GRAVITY_MSS*100))*RAD2DEG,
														-lim_r,lim_r);//roll
}

const float fast_atan_table[257] = 
{
	0.000000e+00, 3.921549e-03, 7.842976e-03, 1.176416e-02,
	1.568499e-02, 1.960533e-02, 2.352507e-02, 2.744409e-02,
	3.136226e-02, 3.527947e-02, 3.919560e-02, 4.311053e-02,
	4.702413e-02, 5.093629e-02, 5.484690e-02, 5.875582e-02,
	6.266295e-02, 6.656816e-02, 7.047134e-02, 7.437238e-02,
	7.827114e-02, 8.216752e-02, 8.606141e-02, 8.995267e-02,
	9.384121e-02, 9.772691e-02, 1.016096e-01, 1.054893e-01,
	1.093658e-01, 1.132390e-01, 1.171087e-01, 1.209750e-01,
	1.248376e-01, 1.286965e-01, 1.325515e-01, 1.364026e-01,
	1.402496e-01, 1.440924e-01, 1.479310e-01, 1.517652e-01,
	1.555948e-01, 1.594199e-01, 1.632403e-01, 1.670559e-01,
	1.708665e-01, 1.746722e-01, 1.784728e-01, 1.822681e-01,
	1.860582e-01, 1.898428e-01, 1.936220e-01, 1.973956e-01,
	2.011634e-01, 2.049255e-01, 2.086818e-01, 2.124320e-01,
	2.161762e-01, 2.199143e-01, 2.236461e-01, 2.273716e-01,
	2.310907e-01, 2.348033e-01, 2.385093e-01, 2.422086e-01,
	2.459012e-01, 2.495869e-01, 2.532658e-01, 2.569376e-01,
	2.606024e-01, 2.642600e-01, 2.679104e-01, 2.715535e-01,
	2.751892e-01, 2.788175e-01, 2.824383e-01, 2.860514e-01,
	2.896569e-01, 2.932547e-01, 2.968447e-01, 3.004268e-01,
	3.040009e-01, 3.075671e-01, 3.111252e-01, 3.146752e-01,
	3.182170e-01, 3.217506e-01, 3.252758e-01, 3.287927e-01,
	3.323012e-01, 3.358012e-01, 3.392926e-01, 3.427755e-01,
	3.462497e-01, 3.497153e-01, 3.531721e-01, 3.566201e-01,
	3.600593e-01, 3.634896e-01, 3.669110e-01, 3.703234e-01,
	3.737268e-01, 3.771211e-01, 3.805064e-01, 3.838825e-01,
	3.872494e-01, 3.906070e-01, 3.939555e-01, 3.972946e-01,
	4.006244e-01, 4.039448e-01, 4.072558e-01, 4.105574e-01,
	4.138496e-01, 4.171322e-01, 4.204054e-01, 4.236689e-01,
	4.269229e-01, 4.301673e-01, 4.334021e-01, 4.366272e-01,
	4.398426e-01, 4.430483e-01, 4.462443e-01, 4.494306e-01,
	4.526070e-01, 4.557738e-01, 4.589307e-01, 4.620778e-01,
	4.652150e-01, 4.683424e-01, 4.714600e-01, 4.745676e-01,
	4.776654e-01, 4.807532e-01, 4.838312e-01, 4.868992e-01,
	4.899573e-01, 4.930055e-01, 4.960437e-01, 4.990719e-01,
	5.020902e-01, 5.050985e-01, 5.080968e-01, 5.110852e-01,
	5.140636e-01, 5.170320e-01, 5.199904e-01, 5.229388e-01,
	5.258772e-01, 5.288056e-01, 5.317241e-01, 5.346325e-01,
	5.375310e-01, 5.404195e-01, 5.432980e-01, 5.461666e-01,
	5.490251e-01, 5.518738e-01, 5.547124e-01, 5.575411e-01,
	5.603599e-01, 5.631687e-01, 5.659676e-01, 5.687566e-01,
	5.715357e-01, 5.743048e-01, 5.770641e-01, 5.798135e-01,
	5.825531e-01, 5.852828e-01, 5.880026e-01, 5.907126e-01,
	5.934128e-01, 5.961032e-01, 5.987839e-01, 6.014547e-01,
	6.041158e-01, 6.067672e-01, 6.094088e-01, 6.120407e-01,
	6.146630e-01, 6.172755e-01, 6.198784e-01, 6.224717e-01,
	6.250554e-01, 6.276294e-01, 6.301939e-01, 6.327488e-01,
	6.352942e-01, 6.378301e-01, 6.403565e-01, 6.428734e-01,
	6.453808e-01, 6.478788e-01, 6.503674e-01, 6.528466e-01,
	6.553165e-01, 6.577770e-01, 6.602282e-01, 6.626701e-01,
	6.651027e-01, 6.675261e-01, 6.699402e-01, 6.723452e-01,
	6.747409e-01, 6.771276e-01, 6.795051e-01, 6.818735e-01,
	6.842328e-01, 6.865831e-01, 6.889244e-01, 6.912567e-01,
	6.935800e-01, 6.958943e-01, 6.981998e-01, 7.004964e-01,
	7.027841e-01, 7.050630e-01, 7.073330e-01, 7.095943e-01,
	7.118469e-01, 7.140907e-01, 7.163258e-01, 7.185523e-01,
	7.207701e-01, 7.229794e-01, 7.251800e-01, 7.273721e-01,
	7.295557e-01, 7.317307e-01, 7.338974e-01, 7.360555e-01,
	7.382053e-01, 7.403467e-01, 7.424797e-01, 7.446045e-01,
	7.467209e-01, 7.488291e-01, 7.509291e-01, 7.530208e-01,
	7.551044e-01, 7.571798e-01, 7.592472e-01, 7.613064e-01,
	7.633576e-01, 7.654008e-01, 7.674360e-01, 7.694633e-01,
	7.714826e-01, 7.734940e-01, 7.754975e-01, 7.774932e-01,
	7.794811e-01, 7.814612e-01, 7.834335e-01, 7.853983e-01,
	7.853983e-01
};

REAL fast_atan2(REAL y, REAL x) 
{
	REAL x_abs, y_abs, z;
	REAL alpha, angle, base_angle;
	int index;

	/* don't divide by zero! */
	if ((y == 0.0f) && (x == 0.0f))
		angle = 0.0f;
	else 
	{
		/* normalize to +/- 45 degree range */
		y_abs = abs_fl(y);
		x_abs = abs_fl(x);
		//z = (y_abs < x_abs ? y_abs / x_abs : x_abs / y_abs);
		if (y_abs < x_abs)
			z = y_abs / x_abs;
		else
			z = x_abs / y_abs;
		/* when ratio approaches the table resolution, the angle is */
		/*      best approximated with the argument itself...       */
		if (z < TAN_MAP_RES)
			base_angle = z;
		else 
		{
			/* find index and interpolation value */
			alpha = z * (REAL) TAN_MAP_SIZE - .5f;
			index = (int) alpha;
			alpha -= (REAL) index;
			/* determine base angle based on quadrant and */
			/* add or subtract table value from base angle based on quadrant */
			base_angle = fast_atan_table[index];
			base_angle += (fast_atan_table[index + 1] - fast_atan_table[index]) * alpha;
		}

		if (x_abs > y_abs) 
		{        /* -45 -> 45 or 135 -> 225 */
			if (x >= 0.0f) 
			{           /* -45 -> 45 */
				if (y >= 0.0f)
					angle = base_angle;   /* 0 -> 45, angle OK */
				else
					angle = -base_angle;  /* -45 -> 0, angle = -angle */
				  return angle;
			} 
			else
			{                  /* 135 -> 180 or 180 -> -135 */
				angle = 3.14159265358979323846;

				if (y >= 0.0f)
					angle -= base_angle;  /* 135 -> 180, angle = 180 - angle */
				else
					angle = base_angle - angle;   /* 180 -> -135, angle = angle - 180 */

			}
		} 
		else 
		{                    /* 45 -> 135 or -135 -> -45 */
			if (y >= 0.0f) 
			{           /* 45 -> 135 */
				angle = 1.57079632679489661923;

				if (x >= 0.0f)
					angle -= base_angle;  /* 45 -> 90, angle = 90 - angle */
				else
					angle += base_angle;  /* 90 -> 135, angle = 90 + angle */
			} 
			else
			{                  /* -135 -> -45 */
				angle = -1.57079632679489661923;

				if (x >= 0.0f)
					angle += base_angle;  /* -90 -> -45, angle = -90 + angle */
				else
					angle -= base_angle;  /* -135 -> -90, angle = -90 - angle */
			}
		}
	}
	return angle;
}

float voltage;    //������ѹֵ(2.67-2.99)    
float real_voltage;    //ʵ�ʵ�ѹֵ
uint16_t adc_value ;
void Get_Voltage(void)     //ͨ��ADC����ص�ѹ
{
	adc_value = ADC_Read();
	voltage = Voltage_Calculation(adc_value);
	real_voltage = 7.0663f*voltage + 0.8930f;   //2-pt cal 2026-06-24: (2.21289V,16.53) (1.98081V,14.89)
	if(real_voltage<15.0f)
	{
		SetBeep(1);//�����ź�
		
	}
}
