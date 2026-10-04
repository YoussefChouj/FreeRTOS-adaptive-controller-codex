#include "StabilizerTask.h"
#include "ekf_of.h"         /* 8-state OF position+velocity-bias+accel-bias KF (Mode 2 bias estimation) */
#include "ekf.h"            /* g_ekf_gate: EKF body velocity for the x/y velocity loops */
#include "math.h"
#include "pid.h"
#include "ADC.h"
#include "mrac.h"
#include "controller.h"
#include "gyro_filter.h"
#include "sysid.h"
#include "rc_input.h"
#include "flight_fsm.h"
#include "calib.h"          /* ADR-0011 Phase 3 (acc-trim) + Phase 4 (gyro-hot) FSMs */
#include "RemoterTask.h"   /* OFHOLD_CH (ch6 OF-hold enable switch), sbus_channel[] */
#include "thrust_estimators.h"
#include "rpm.h"
#include "flight_telemetry.h"   /* g_tlm telemetry groups, filled by Tlm_Snapshot */
/* WFB BEGIN glue */
#include "wfb_glue.h"       /* Workflow B: GS takeoff/trajectory/land sequencer + safety net */
#include "wfb_traj.h"
#include "wfb_prim.h"
/* WFB END glue */

/**
 * @module     StabilizerTask.c
 * @subsystem  control
 * @owner      Stabilizer_Task (USER/main.c:418): stabilizer_Task() runs once per 5 ms tick (200 Hz).
 * @purpose    One control tick: sensors -> loop feedback, setpoints, PID/MRAC cascade, mixer, motor gating.
 * @inputs     imu_data, Gyro_*_Real, Lin_Acc_*_body, Gravity_Body_* (IMU), ano_of (optical flow + ToF),
 *             RCInput_* (RemoterTask), TWC targets (send_data.c, wfb glue), FlightFSM state, flight_phase.
 * @outputs    Ctrler.*.{Des,FB,U} (API/pid.c), Throttle_out, u_gyrox/y/z, mymotor -> Set_PWM_Motors(),
 *             OF bias + OF KF state (s_of_bias_*, s_ekf_of), g_cal_health, the landing detector.
 * @depends    pid.h, controller.h, mrac.h, ekf_of.h, ekf.h, calib.h, flight_fsm.h, rc_input.h, wfb_glue.h
 * @caution    mixer sign conventions and motor channel ordering are safety critical (Compute_Motor).
 *
 * Tick order, stabilizer_Task():
 *   warm-up origin pin, arm-state mirror, cold-boot sync
 *   Update_Data()    trig cache, OF bias + OF KF, OF position, calibrators, OF velocity, ToF height, attitude FB
 *   Wfb_Step()       Workflow B glue (API/wfb_glue.c)
 *   Compute_Motor()  landing contact, Z loops, ARM-edge rebase, position/velocity loops (every 2nd tick,
 *                    100 Hz), angle + rate loops, SysID, MRAC, controller layer, mixer, thrust estimators
 *   Update_Motor()   bench zero, motor-test/debug overrides, landing detector, ground idle, disarm
 *   Tlm_Snapshot()   read-only copy of this tick's key state into g_tlm (API/flight_telemetry.h)
 * Units: position cm (locx/locy loops, TWC.target_x/y), height m (Z loops), angles deg, rates deg/s,
 * motor commands in TIM CCR counts (2000 = stop, 4000 = full, BSP/pwm.h).
 */
unsigned char cnt_h,cnt_loc;
float Throttle_out,u_gyrox,u_gyroy,u_gyroz;
short Throttle_th = 2200;

/* ---- Constants --------------------------------------------------------------------------------- */
#define STAB_DT_S            0.005f   /* control tick, s (200 Hz) */
#define CM_PER_M             100.0f
#define M_PER_CM             0.01f
#define THR_IDLE_MAX         0.2f     /* RC throttle below this (0..1) = "motors idle" for every ground gate */
#define TAKEOFF_ALT_M        0.2f     /* GROUND_IDLE -> FLYING once Z FB is above this (with THR or TWC) */
#define POS_LOOP_DIV         2U       /* height and position loops run every 2nd tick (100 Hz) */
#define DES_FLOOR_M          0.01f    /* Z setpoint at or below this = the landing ramp reached the floor */
#define HOVER_THR_BENCH      3100     /* Throttle_th, CCR, on the 4DOF fixture (bench_mode_active) */
#define HOVER_THR_FREE       3050     /* Throttle_th, CCR, free flight; see the history at Compute_Motor */

/* LAND setpoint ramp: 0.0015 m/tick at 200 Hz = 0.30 m/s descent rate.
 * PID follows the setpoint to ground — no throttle ramp needed.
 * Touchdown detected by Z_ratePID.FB → 0 on frame contact. */
#define LAND_DES_STEP   0.0015f
/* Two-stage descent (2026-10-02): below LAND_SLOW_ALT the ramp halves to 0.15 m/s.
 * Logs: sink spikes to 0.65-1.05 m/s in the last 30 cm on a 0.30 m/s command, and the
 * operator saw less landing drift at slower descent. 0.15 m/s stays above the 0.08 m/s
 * touchdown-rate threshold. */
#define LAND_SLOW_ALT        0.40f
#define LAND_DES_STEP_SLOW   0.00075f
/* Safety net: 3000 ticks at 200 Hz = 15 s max landing time before forced disarm
 * (was 10 s; the slow final stage adds about 1.3 s, keep margin from 2.5 m). */
#define LAND_MAX_TICKS  3000U
/* Touchdown ground evidence (2026-10-02b): FB within LAND_REST_MARGIN of the pre-takeoff
 * rest height, or the sink bias saturated at LAND_SINK_BIAS_MAX (see Update_Motor). */
#define LAND_REST_MARGIN     0.03f
#define LAND_SINK_BIAS_MAX   0.40f
/* FIX 2026-10-03 (flight_test_drift_fix_1 F5): sitting at rest height cuts on its own.
 * F5 touched down at 0.05 m (rest 0.05) for 0.44 s, but the climb-rate reading swung
 * +-0.3 m/s on the ground, the 0.08 m/s stable-rate test never passed, and the rate
 * loop threw throttle at each impact: 5 bounces in 7 s. Now Des at the floor and FB
 * within LAND_REST_MARGIN of rest for LAND_REST_CUT_TICKS also lands (F2-F4 sat there
 * 0.26-0.40 s right before their normal cut, so they are unchanged). */
#define LAND_REST_CUT_TICKS  40U   /* 0.2 s at 200 Hz */
/* FIX 2026-10-03 (FLIGHT_TEST_DRIFT_FIX_1 F1, F3): floor for s_land_rest_z. Right after
 * power-up the ground reads 0.00 m, but after a landing it reads 0.05 m, so the rest gate
 * (0.03 m) never opened on the first flight per battery: props spun 1.5 s on the floor at
 * ~94% hover throttle while the xy estimate ran 15 cm and the hold tilted up to 15 deg (skid).
 * Gate is now 0.08 m minimum, still under the 0.11-0.14 m ground-effect float. */
#define LAND_REST_MIN        0.05f
/* WP-21 B: ground-contact stage (ArduPilot land_complete_maybe / soften_for_landing_xy idea).
 * In LANDING with the Z ramp at the floor, FB below LAND_CONTACT_ALT and |vz| below
 * LAND_CONTACT_VZ for LAND_CONTACT_TICKS, hold (not zero) the xy position and velocity
 * integrators. With feet touching, friction stops the motion the I terms are pushing for,
 * so they would wind up a tilt that skids or tips the drone at the cut. P and D stay
 * active: position hold is NOT turned off. It can also latch while floating on the
 * ground-effect cushion (0.11-0.14 m, vz ~0); holding I there is harmless. Does not fix
 * the OF position-estimate error itself. */
#define LAND_CONTACT_ALT     0.15f
#define LAND_CONTACT_VZ      0.10f
#define LAND_CONTACT_TICKS   60U   /* 0.3 s at 200 Hz */
/* Motor bench-test dead-man: stabilizer runs at 200 Hz, so 100 ticks = 500 ms.
 * If the dashboard stops sending CMD 0x16 heartbeats, motors are zeroed. */
#define MOTOR_TEST_DEADMAN_TICKS  100U
float Cos_Yaw_01= 0;
float Sin_Yaw_01= 0;
/* OF position-hold applied state (see case_Update_pitrol_Des); telemetered as status.of_hold. */
uint8_t g_of_hold_active = 0;
/* Keil debug manual motor override: set dbg_motor_manual=1 in the Watch window, then edit
 * dbg_motor_ccr[0..3] (M1..M4, 2000=stop, 2150=idle, max 4000). DISARMED-only, no dead-man:
 * set dbg_motor_manual=0 to stop. Props off. Never halt the CPU while a motor spins. */
volatile uint8_t  dbg_motor_manual = 0U;
volatile uint16_t dbg_motor_ccr[4] = {2000U, 2000U, 2000U, 2000U};
/* Yaw mixer sign: -1 = 09-10 signs (M3/M4 get +u_gyroz), +1 = flipped. Flight4 (+1) spun 3.5x faster. */
volatile float g_yaw_mix_dir = -1.0f;

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
 * Mode 2 (EKF): dedicated 8-state OF position+bias+accel bias KF (API/ekf_of.c).
 *   Jointly estimates [pos_x, vel_x, bof_x, pos_y, vel_y, bof_y, ba_x, ba_y].
 *   The measurement model (z = vel - bias) lets the KF separate real motion
 *   from bias in the Kalman sense — no pull-back by construction.
 *   Its position state feeds Ctrler.locx/yPID.FB directly, bypassing the
 *   raw OF integration. Runs in parallel with the 9-state IMU EKF (send_data.c).
 *   See API/ekf_of.h for the model and noise parameters.
 *
 * Optical-flow velocity-bias estimation mode selector.
 * 0 = FIXED: bias set once at boot (first locked OF sample), never changes.
 *           ARM edge also re-snaps to current sample.
 * 1 = EMA:   continuous EMA tracking (OF_BIAS_EMA_ALPHA), with optional
 *            freeze via g_of_bias_ema_freeze (CMD 0x1E idx=1).
 * 2 = EKF:   dedicated 8-state OF position+bias KF (API/ekf_of.c).
 *            Runs in parallel with the existing 9-state IMU EKF (send_data.c).
 *            Mode 2 FEEDS the control loop (Ctrler.locx/yPID.FB) from the
 *            KF's debiased position state — bypasses the raw OF integration.
 *
 * Switch modes via CMD 0x1E idx=0 (dashboard UI wires this to 3 buttons).
 * EMA freeze is CMD 0x1E idx=1 (0=run, 1=frozen). Freeze persists across ARM.
 * Mode and freeze state are live-pokeable via g_of_bias_mode / g_of_bias_ema_freeze. */
#ifndef OF_BIAS_MODE_DEFAULT
#define OF_BIAS_MODE_DEFAULT  2   /* EKF (WP-21 default; was 0 FIXED). Bias re-snapped at every ARM edge in all modes */
#endif
volatile uint8_t g_of_bias_mode = OF_BIAS_MODE_DEFAULT; /* 0=FIXED 1=EMA 2=EKF */
volatile uint8_t g_of_bias_ema_freeze = 0U;             /* 1=freeze EMA update */

/* EMA time constant — operator-configurable via CMD 0x1E idx=2.
 * Valid range: [1.0, 300.0] s. Default 20 s. The per-tick alpha is
 * recomputed each cycle as dt/tau so tau changes take effect immediately.
 * Clamped in the command handler; plain global so DWARF-subscribable. */
#define OF_BIAS_EMA_TAU_DEFAULT  20.0f
#define OF_BIAS_EMA_TAU_MIN       1.0f
#define OF_BIAS_EMA_TAU_MAX     300.0f
float g_of_bias_ema_tau_s = OF_BIAS_EMA_TAU_DEFAULT; /* EMA time constant (s) */

/* EKF (Mode 2) health and fallback flags.
 * g_ekf_of_health: 1=healthy, 0=diverged (innovation magnitude > threshold).
 * g_ekf_of_fallback: set to 1 once after any health-triggered mode fallback;
 *   sticky until the next ARM so the operator can see it on the dashboard.
 * Both are plain globals so they are DWARF-subscribable. */
/* EKF_OF_INNOV_THRESH and EKF_OF_HEALTH_PERSIST are now in ekf_of.h (WP-14). */
#define EKF_OF_ACC_SIGN_X (+1.0f)
#define EKF_OF_ACC_SIGN_Y (+1.0f)
#define EKF_OF_MG_TO_MPS2 (9.80665e-3f)
/* WP-14 tilt input = SIGN * 1000*Gravity_Body_X/Y.  Both signs +1: ekf_of_replay fits k = d(OF vel)/(g*tilt*dt)
 * per axis on the 5 pinned logs and gets k_x 0.20..0.38, k_y 0.18..0.36 (all > 0) with these signs;
 * SIGN_Y = -1 gave k_y -0.18..-0.36 on every log. */
#define EKF_OF_UPDATE_ON_NEW_FRAME 1U /* 1 = feed each OF frame once (detected by ano_of.of_update_cnt changing), 0 = legacy every tick */
/* WP-21 A: full-tilt OF correction, default ON since 2026-10-03 (flown in flight_test_roaming_and_landing_2..4).  OF deltas are body-frame (sensor plane);
 * when tilted, climb/sink leaks into them (first order: v_level ~= v_body + SIGN*GB*v_up).
 * 1 = rotate each body delta to the level frame before the yaw rotation:
 *   n = (-SIGN_X*GBX, -SIGN_Y*GBY, n3) = up axis in OF axes, n3 = sqrt(1 - n1^2 - n2^2)
 *   dz = (dh_up - n1*dx - n2*dy) / n3                      (body z from the height change)
 *   level = minimal tilt rotation of (dx, dy, dz)          (yaw kept, so the yaw step is unchanged)
 * dh_up = ano_of.of2_h_f2_v (m/s, + up, tilt-corrected height rate).  Skipped above 60 deg
 * tilt (n3 < 0.5).  SIGN UNCONFIRMED: auto_landing_1 regression was inconclusive.  Before
 * flying with it, handheld test (CMD 0x1E idx=3): tilt ~10 deg on one axis, move straight
 * up/down ~50 cm; locx/locyPID.FB must stay flatter with the flag on than off. */
volatile uint8_t g_of_full_tilt = 1U;
/* OF distance scale (2026-10-03, x_y_calibration_hitting_wall_1): wall-to-wall passes in a
 * 400 cm room with a ~55 cm drone (true travel 345 cm) read 314.3 cm on body y and 316.4 cm on
 * body x at ~0.9 m, so OF under-reads by the same ~9% on both axes (345 / 315.35).  Applied
 * only where OF leaves for the loops (position deltas, velocity FB), so the KF tuning, bias
 * logic and rest thresholds stay in raw OF units.  Measured at one height.  1.0 = off. */
volatile float g_of_scale = 1.094f;
volatile uint8_t g_ekf_of_health   = 1U; /* 1=healthy 0=diverged  */
volatile uint8_t g_ekf_of_fallback = 0U; /* 1=fell back to FIXED  */
/* WP-14: shadow mode — KF runs every tick regardless of g_of_bias_mode.
 * Default 1 (shadow on). A bad innovation in shadow only clears health,
 * never changes g_of_bias_mode. Position feed stays on mode 2 only. */
volatile uint8_t g_ekf_of_shadow   = 1U;
/* WP-14: active velocity feedback. When 1 AND mode 2 AND healthy,
 * locxsPID/locysPID.FB take KF velocity instead of raw OF. Default 1 (WP-21; was 0). */
volatile uint8_t g_ekf_of_vel_fb   = 1U;
/* WP-14: innovation persistence counter for the health gate.
 * Trips only after EKF_OF_HEALTH_PERSIST consecutive bad ticks. */
static uint16_t s_ekf_of_innov_bad_cnt = 0U;
/* WP-14: 1 once the health gate has tripped; keeps g_ekf_of_health at 0 across the
 * clean re-init that follows, until the next ARM edge or until the KF stops ticking.
 * WHY: without the latch the re-init zeroes the innovation and health flips back
 * to 1 within 5 ms, so a shadow flight would never show the trip. */
static uint8_t s_ekf_of_tripped = 0U;
/* FIX 2026-10-03: a trip in flight drops mode 2 to FIXED for the rest of that
 * flight; this latch puts mode 2 back on the next ARM 0->1 edge. Before, the
 * ARM edge cleared health/fallback but left g_of_bias_mode at 0, so a trip
 * during a handheld carry (test_flight_ekf_1, 167.6 s) silently flew the next
 * flight on raw OF with health shown as 1. */
static uint8_t s_ekf_of_mode_restore = 0U;
/* Handheld test (CMD 0x1E idx=3): integrate OF position on the ground so the
 * drone can be carried by hand (modes 0/2). Freezes the ground bias EMA, zeroes
 * the origin on enable, and self-clears once the drone is flying. */
volatile uint8_t g_of_handheld_test = 0U;
/* Pre-arm OF warning (FIX 2026-10-02): 1 while the on-ground averaged OF
 * velocity at rest exceeds OF_REST_WARN_CMS on either axis (2026-10-01 ~0,
 * 2026-10-02 +3.3/+5.1). The ARM snap removes it, but a large rest reading
 * means the module or floor is off: check it before flying. */
#define OF_REST_WARN_CMS            3.0f
volatile uint8_t g_of_rest_warn = 0U;

/* OF velocity-bias EMA state. s_of_bias_seeded gates the seed-on-first-sample
 * logic (see comment above). g_of_bias_ema_freeze gates the continuous update. */
float s_of_bias_x = 0.0f, s_of_bias_y = 0.0f;
static u8 s_of_bias_seeded = 0;

/* Dedicated 8-state OF position+bias KF — used in Mode 2 (EKF).
 * Initialised once at boot, ticked every control cycle in Update_Data.
 * Its position state (x[0], x[3]) feeds Ctrler.locx/yPID.FB in EKF mode. */
EkfOf_t s_ekf_of;
static uint8_t s_ekf_of_inited = 0U;
/* Mode 2: last KF body-frame position (m) and a resync flag. earth_x/y are
 * accumulated from yaw-rotated KF position increments, not by rotating the
 * whole body-frame position with the current yaw (FIX 2026-09-26). */
static float s_ekf_prev_px = 0.0f;
static float s_ekf_prev_py = 0.0f;
static uint8_t s_ekf_pos_synced = 0U;

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
/* Handheld test: OF range floor. At 1-4 cm (resting on the ground) the module
 * still reports quality 255 but its velocity is not usable. */
#define OF_HANDHELD_MIN_ALT_CM      10U

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
#define CAL_HEALTH_BOOT_OK            0x01U
#define CAL_HEALTH_COLD_OK            0x02U
#define CAL_HEALTH_COLD_DEGRADED      0x04U
#define CAL_HEALTH_AIRBORNE_OK        0x08U
#define CAL_HEALTH_AIRBORNE_DEGRADED  0x10U
#define CAL_HEALTH_HOT_HOVER_OK       0x20U
#define CAL_HEALTH_HOT_REJECTED       0x40U
#define CAL_HEALTH_BOOT_TIMEOUT       0x100U
#define CAL_HEALTH_ESTIMATOR_READY    0x200U
#define IMU_SETTLE_E2                 0.0009f  /* g_imu_settle_metric at or above this = COLD_DEGRADED */
#define CAL_TRIM_WINDOW_TICKS         2000U    /* CalTrim_Init window: 10 s at 200 Hz */

/* ==== Optical-flow helpers ====================================================================== */
#define OF_TILT_N3SQ_MIN     0.25f    /* n3^2 below this = more than 60 deg tilt: leave the delta raw */

/* Rotate one tick's body OF delta (*dx, *dy) to the level frame in place; dh_up is the same
 * tick's height change in the same units (cm/tick here).  See g_of_full_tilt above. */
static void of_full_tilt_delta(float *dx, float *dy, float dh_up)
{
	float n1 = -EKF_OF_ACC_SIGN_X * Gravity_Body_X;
	float n2 = -EKF_OF_ACC_SIGN_Y * Gravity_Body_Y;
	float n3sq = 1.0f - n1 * n1 - n2 * n2;
	float n3, k, bx, by, bz;
	if (n3sq < OF_TILT_N3SQ_MIN) return;      /* > 60 deg tilt: leave the delta raw */
	n3 = sqrtf(n3sq);
	k  = 1.0f / (1.0f + n3);
	bx = *dx;
	by = *dy;
	bz = (dh_up - n1 * bx - n2 * by) / n3;
	*dx = (1.0f - n1 * n1 * k) * bx - n1 * n2 * k * by - n1 * bz;
	*dy = -n1 * n2 * k * bx + (1.0f - n2 * n2 * k) * by - n2 * bz;
}

/* FIX 2026-09-26: Mode 2 KF measures (OF - s_of_bias) = vel - bias. Any change
 * of s_of_bias by d counts shifts that measurement by -d*0.01 m/s, so shift the
 * KF bias state by the same amount; otherwise the step is read as velocity and
 * integrates into position. Every s_of_bias writer calls this with its delta. */
static void Of_RebaseKfBias(float dbx, float dby)
{
	/* WP-14: run in shadow too (was guarded on mode==2). Sign fix: measurement
	 * model z = v + bof, so a +d shift of the raw measurement = -d shift in
	 * the KF bias state (was +=, should be -=). */
	if (s_ekf_of_inited) {
		s_ekf_of.x[2] -= dbx * M_PER_CM;
		s_ekf_of.x[5] -= dby * M_PER_CM;
	}
}

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
	Of_RebaseKfBias(-s_of_bias_x, -s_of_bias_y);
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
	/* WP-14: reset KF position in all modes (shadow included).
	 * vel and bias states stay — only accumulated position resets to zero. */
	if (s_ekf_of_inited) {
		EkfOf_ResetPos(&s_ekf_of);
	}
	s_ekf_pos_synced = 0U;
}

/* WFB BEGIN glue */
/* Workflow B (docs/workflow-b/interfaces.md): once per 200 Hz tick, snapshot the vehicle into
 * API/wfb_glue.c and apply what it asks for through the existing firmware paths. This function
 * only converts units and routes requests; every decision lives in the glue (host-tested). */
static void Wfb_Step(void)
{
	static uint8_t s_was_armed = 0U;   /* disarm edge; the armed_pub mirror above is not one (s_sync writes ARM_Status too) */
	wfb_glue_in_t  in;
	wfb_glue_out_t out;

	in.now_ms      = (uint32_t)xTaskGetTickCount() * (uint32_t)portTICK_PERIOD_MS;
	in.x_m         = Ctrler.locxPID.FB * 0.01f;   /* TWC / loc loops are cm, the glue is m */
	in.y_m         = Ctrler.locyPID.FB * 0.01f;
	in.z_m         = Ctrler.Z_posPID.FB;           /* already m */
	in.roll_deg    = imu_data.rol;
	in.pitch_deg   = imu_data.pit;
	in.vbat_v      = real_voltage;
	in.yaw_deg     = Ctrler.yawPID.FB;             /* the TWC.set_yaw frame */
	in.armed       = (FlightFSM_GetState() == FLIGHT_STATE_ARMED) ? 1U : 0U;
	in.motors_idle = g_motor_idle_enabled ? 1U : 0U;
	in.sbus_live   = sbus_lost ? 0U : 1U;
	in.airborne    = (flight_phase == FLIGHT_PHASE_FLYING || flight_phase == FLIGHT_PHASE_LANDING) ? 1U : 0U;
	in.rc_override = (RCInput_IsActive(RC_AXIS_ROLL) || RCInput_IsActive(RC_AXIS_PITCH)) ? 1U : 0U;

	/* send_data.c and RemoterTask.c call into the glue from other tasks */
	taskENTER_CRITICAL();
	if (s_was_armed && !in.armed) {
		wfb_glue_disarmed();
	}
	wfb_glue_tick(&in, &out);
	taskEXIT_CRITICAL();
	s_was_armed = in.armed;

	/* PROTECTED BEGIN wfb_apply */
	if (out.motor_stop_req) {
		FlightFSM_Event(FLIGHT_EVENT_DANGEROUS_STOP);
	} else if (out.land_req) {
		if (flight_phase == FLIGHT_PHASE_FLYING) {   /* same two lines as the RC ch5 land */
			flight_phase = FLIGHT_PHASE_LANDING;
			TWC.execute  = 0U;
		}
	} else if (out.takeoff_req) {
		sbus_flyup_trigger = 1U;                      /* the ch7 fly-up path, consumed in Update_Data() */
	} else if (out.setpoint_valid) {
		TWC.target_x = out.x_sp_m * 100.0f;           /* m -> cm */
		TWC.target_y = out.y_sp_m * 100.0f;
		TWC.target_z = out.z_sp_m;
		TWC.set_yaw  = out.yaw_sp_deg;
		TWC.execute  = 1U;
	}
	/* PROTECTED END wfb_apply */
}
/* WFB END glue */

/* ==== Telemetry groups (API/flight_telemetry.h) =================================================== */
#ifdef __CC_ARM
    #define TLM_CCM __attribute__((section("MRAC_CCM"), zero_init))   /* CPU-only CCM, zeroed by __main */
#else
    #define TLM_CCM
#endif
FlightTelemetry_t g_tlm TLM_CCM;

/* Copy the key flight state of this tick into g_tlm (the groups of API/flight_telemetry.h). Runs last in
 * stabilizer_Task; reads the sources only, so the control path is unchanged. */
static void Tlm_Snapshot(void)
{
	g_tlm.att.roll_deg   =  Ctrler.rollPID.FB;
	g_tlm.att.pitch_deg  = -Ctrler.pitchPID.FB;
	g_tlm.att.yaw_deg    = -Ctrler.yawPID.FB;
	g_tlm.att.rate_x_dps =  Ctrler.gyroxPID.FB;
	g_tlm.att.rate_y_dps =  Ctrler.gyroyPID.FB;
	g_tlm.att.rate_z_dps =  Ctrler.gyrozPID.FB;

	g_tlm.sp.roll_deg    = Ctrler.rollPID.Des;
	g_tlm.sp.pitch_deg   = Ctrler.pitchPID.Des;
	g_tlm.sp.yaw_deg     = Ctrler.yawPID.Des;
	g_tlm.sp.rate_x_dps  = Ctrler.gyroxPID.Des;
	g_tlm.sp.rate_y_dps  = Ctrler.gyroyPID.Des;
	g_tlm.sp.rate_z_dps  = Ctrler.gyrozPID.Des;
	g_tlm.sp.x_cm        = Ctrler.locxPID.Des;
	g_tlm.sp.y_cm        = Ctrler.locyPID.Des;
	g_tlm.sp.vx_cms      = Ctrler.locxsPID.Des;
	g_tlm.sp.vy_cms      = Ctrler.locysPID.Des;
	g_tlm.sp.z_m         = Ctrler.Z_posPID.Des;
	g_tlm.sp.vz_mps      = Ctrler.Z_ratePID.Des;

	g_tlm.pos.x_cm       = Ctrler.locxPID.FB;
	g_tlm.pos.y_cm       = Ctrler.locyPID.FB;
	g_tlm.pos.vx_cms     = Ctrler.locxsPID.FB;
	g_tlm.pos.vy_cms     = Ctrler.locysPID.FB;
	g_tlm.pos.z_m        = Ctrler.Z_posPID.FB;
	g_tlm.pos.vz_mps     = Ctrler.Z_ratePID.FB;

	g_tlm.mot.throttle   = Throttle_out;
	g_tlm.mot.u_roll     = u_gyrox;
	g_tlm.mot.u_pitch    = u_gyroy;
	g_tlm.mot.u_yaw      = u_gyroz;
	g_tlm.mot.m1         = (float)mymotor.motor1;
	g_tlm.mot.m2         = (float)mymotor.motor2;
	g_tlm.mot.m3         = (float)mymotor.motor3;
	g_tlm.mot.m4         = (float)mymotor.motor4;

	g_tlm.mrac.e[0]      = mrac_state.pitch.e;
	g_tlm.mrac.e[1]      = mrac_state.roll.e;
	g_tlm.mrac.e[2]      = mrac_state.yaw.e;
	g_tlm.mrac.e[3]      = mrac_state.z_rate.e;
	g_tlm.mrac.u_nom[0]  = mrac_state.pitch.u_nom;
	g_tlm.mrac.u_nom[1]  = mrac_state.roll.u_nom;
	g_tlm.mrac.u_nom[2]  = mrac_state.yaw.u_nom;
	g_tlm.mrac.u_nom[3]  = mrac_state.z_rate.u_nom;
	g_tlm.mrac.u_ad[0]   = mrac_state.pitch.u_ad;
	g_tlm.mrac.u_ad[1]   = mrac_state.roll.u_ad;
	g_tlm.mrac.u_ad[2]   = mrac_state.yaw.u_ad;
	g_tlm.mrac.u_ad[3]   = mrac_state.z_rate.u_ad;
	g_tlm.mrac.fade      = mrac_simplex.fade;

	g_tlm.est.kf_x_m       = s_ekf_of.x[0];
	g_tlm.est.kf_y_m       = s_ekf_of.x[3];
	g_tlm.est.kf_vx_mps    = s_ekf_of.x[1];
	g_tlm.est.kf_vy_mps    = s_ekf_of.x[4];
	g_tlm.est.kf_bof_x_mps = s_ekf_of.x[2];
	g_tlm.est.kf_bof_y_mps = s_ekf_of.x[5];
	g_tlm.est.of_bias_x    = s_of_bias_x;
	g_tlm.est.of_bias_y    = s_of_bias_y;

	g_tlm.pwr.vbat_v         = real_voltage;
	g_tlm.pwr.thrust_imu_n   = g_thrust_est.imu_total;
	g_tlm.pwr.thrust_be_n[0] = g_thrust_est.blade_element[0];
	g_tlm.pwr.thrust_be_n[1] = g_thrust_est.blade_element[1];
	g_tlm.pwr.thrust_be_n[2] = g_thrust_est.blade_element[2];
	g_tlm.pwr.thrust_be_n[3] = g_thrust_est.blade_element[3];
	g_tlm.pwr.mass_hat_kg    = g_thrust_est.mass_hat;
}

/* ==== The control tick ========================================================================== */

/* Bug 2 (audit 2026-09-21 §1 row 2): published arm state must track the
 * motor gate unconditionally. s_sync() in flight_fsm.c writes ARM_Status
 * on every FSM transition, but the real flag that gates motor output in
 * Update_Motor() is FlightFSM_GetState()==FLIGHT_STATE_ARMED -- mirror it
 * into DroneStatus.ARM_Status every control cycle so the telemetry byte
 * (Frame A + slot-0 address subscription) can never diverge from it, on
 * both the RC and SDK arm paths. Observability only. */
static void Arm_PublishState(void)
{
	uint8_t armed_pub;
	armed_pub = (FlightFSM_GetState() == FLIGHT_STATE_ARMED) ? (uint8_t)Armed : (uint8_t)DisArmed;
	if (DroneStatus.ARM_Status != armed_pub) {
		DroneStatus.ARM_Status = armed_pub;
	}
}

/* ADR-0011: set BOOT_OK + COLD_OK on the rising edge of g_estimator_ready.
 * Cold-cal degraded/timeout detection is left to the dashboard via the
 * g_imu_settle_metric telemetry value (high value = didn't settle cleanly).
 * Lazy one-shot init of the calibrators on cold-cal completion. */
static void Cal_EstimatorReadyEdge(void)
{
	static uint8_t s_prev_ready = 0U;
	if (g_estimator_ready && !s_prev_ready) {
		g_cal_health |= CAL_HEALTH_BOOT_OK;             /* BOOT_OK rising edge */
		g_cal_health |= CAL_HEALTH_COLD_OK;             /* COLD_OK (Phase 2 done) */
		if (g_imu_settle_metric >= IMU_SETTLE_E2) {
			g_cal_health |= CAL_HEALTH_COLD_DEGRADED;
			g_cal_health |= CAL_HEALTH_BOOT_TIMEOUT;
		}
		CalTrim_Init(&s_cal_trim, CAL_TRIM_WINDOW_TICKS);
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
		 * flag is a function-static inside Des_Yaw and cannot
		 * be touched here. The direct Des assignment is sufficient. */
		Ctrler.yawPID.Des   = imu_data.yaw;
	}
	s_prev_ready = g_estimator_ready;
	if (g_estimator_ready) g_cal_health |= CAL_HEALTH_ESTIMATOR_READY;
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

	Check_Fly_Mode();

	Arm_PublishState();

	Cal_EstimatorReadyEdge();

	Update_Data();

	Wfb_Step();   /* WFB glue: after Update_Data() so the FB values are this tick's */

	Compute_Motor();

	Update_Motor();

	Tlm_Snapshot();   /* telemetry groups: read-only copy of this tick's state, last */
}

/* ==== Update_Data: sensors -> loop feedback ======================================================= */
#define MG_PER_G               1000.0f
#define OF_HRATE_TO_CM_TICK    0.5f     /* of2_h_f2_v (m/s) * 100 cm/m * 0.005 s/tick */
#define ALT_BAND_MIN_CM        5U       /* ToF band: below = dropout */
#define ALT_BAND_MAX_CM        500U     /* ToF band: above = no reading (65535) or out of range */
#define ALT_JUMP_BASE_M        0.05f    /* per-tick jump gate at zero commanded rate (10 m/s) */
#define ALT_JUMP_PER_VZ        0.15f    /* gate widening per m/s of |Z_ratePID.Des| */
#define ALT_JUMP_MAX_M         0.20f
#define ALT_RESYNC_REJECTS     20U      /* consecutive rejects (100 ms) that force a resync */
#define ALT_RATE_LPF_OLD       0.9f     /* of2_h_f2_v = 0.9 * old + 0.1 * new */
#define ALT_RATE_LPF_NEW       0.1f

/* Yaw/roll/pitch sin and cos used by the frame rotations below (the _01 globals). */
static void Att_UpdateTrig(void)
{
	Cos_Yaw_01=cos(-imu_data.yaw* DEG2RAD);
	Sin_Yaw_01=sin(-imu_data.yaw* DEG2RAD);

	Cos_roll_01=cos(imu_data.rol* DEG2RAD);
	Sin_roll_01 = sin(imu_data.rol* DEG2RAD);
	Cos_pitch_01=cos(imu_data.pit* DEG2RAD);
	Sin_pitch_01 = sin(imu_data.pit* DEG2RAD);
}

/* CMD 0x17 manual snap, then the mode 0 (FIXED) / mode 1 (EMA) bias update. */
static void Of_UpdateBias(u8 of_ok)
{
	/* CMD 0x17 manual snap: jump s_of_bias_x/y to current OF sample.
	 * Available in all modes. For Mode 2 (EKF), also re-seeds the KF's
	 * velocity/bias states from the debiased OF reading. */
	if (g_of_bias_capture_req) {
		g_of_bias_capture_req = 0;
		if (of_ok) {
			Of_RebaseKfBias((float)ano_of.of2_dx_fix - s_of_bias_x,
			                (float)ano_of.of2_dy_fix - s_of_bias_y);
			s_of_bias_x = (float)ano_of.of2_dx_fix;
			s_of_bias_y = (float)ano_of.of2_dy_fix;
		}
	}

	/* ---- Mode 0 (FIXED): seed once at boot, never adapt ---- */
	if (g_of_bias_mode == OF_BIAS_FIXED) {
		if (of_ok && !s_of_bias_seeded) {
			s_of_bias_seeded = 1;
			s_of_bias_x = (float)ano_of.of2_dx_fix;
			s_of_bias_y = (float)ano_of.of2_dy_fix;
		}
		/* bias stays fixed after seed */
	}
	/* ---- Mode 1 (EMA): continuous tracking with optional freeze ---- */
	else if (g_of_bias_mode == OF_BIAS_EMA) {
		float ema_alpha;
		if (of_ok && !s_of_bias_seeded) {
			s_of_bias_seeded = 1;
			s_of_bias_x = (float)ano_of.of2_dx_fix;
			s_of_bias_y = (float)ano_of.of2_dy_fix;
		}
		/* Per-tick alpha computed from configurable tau: alpha = dt/tau.
		 * tau is clamped [1,300] s in the command handler, safe to divide. */
		ema_alpha = STAB_DT_S / g_of_bias_ema_tau_s;
		/* EMA runs only when not frozen */
		if (of_ok && !g_of_bias_ema_freeze) {
			s_of_bias_x += ema_alpha
				* ((float)ano_of.of2_dx_fix - s_of_bias_x);
			s_of_bias_y += ema_alpha
				* ((float)ano_of.of2_dy_fix - s_of_bias_y);
		}
	}
}

/* ---- Mode 2 (EKF) or shadow: tick the 8-state KF, check health ----
 * WP-14: KF ticks whenever mode==2 or shadow. Predict, ZUPT, OF update
 * and health gate run every 5 ms tick. Shadow mode only records health;
 * mode 2 feeds the position path and falls back on persistent divergence. */
static void Of_TickKf(u8 of_ok)
{
	float ofx;
	float ofy;
	float innov_mag;
	float tilt_ax;
	float tilt_ay;
	uint8_t on_ground;
	static u8 s_last_of_update_cnt = 0;
	if (!s_ekf_of_inited) {
		EkfOf_Init(&s_ekf_of);
		s_ekf_of_inited = 1U;
		g_ekf_of_health = s_ekf_of_tripped ? 0U : 1U; /* a gate trip stays visible across its re-init */
		s_ekf_of_innov_bad_cnt = 0U;
	}
	on_ground = !g_of_handheld_test && !(DroneStatus.ARM_Status == Armed && (flight_phase == FLIGHT_PHASE_FLYING || flight_phase == FLIGHT_PHASE_LANDING));
	ofx = ((float)ano_of.of2_dx_fix - s_of_bias_x) * M_PER_CM; /* m/s */
	ofy = ((float)ano_of.of2_dy_fix - s_of_bias_y) * M_PER_CM;
	/* WP-14: tilt-only input — gravity-tilt term replaces Lin_Acc.
	 * Body accel explains ~0% of OF velocity change; the gravity-tilt
	 * alone explains 34-63% (drift investigation §ekf F2).
	 * Gravity_Body_X/Y = vecxZ/vecyZ (body-frame gravity unit vector,
	 * computed each tick in imu_update.c).  Multiply by 1000 to get mg. */
	tilt_ax = EKF_OF_ACC_SIGN_X * MG_PER_G * Gravity_Body_X * EKF_OF_TILT_GAIN * EKF_OF_MG_TO_MPS2;
	tilt_ay = EKF_OF_ACC_SIGN_Y * MG_PER_G * Gravity_Body_Y * EKF_OF_TILT_GAIN * EKF_OF_MG_TO_MPS2;
	EkfOf_Predict(&s_ekf_of, STAB_DT_S, tilt_ax, tilt_ay);
	if (on_ground) EkfOf_UpdateZeroVel(&s_ekf_of);
	/* FIX 2026-10-03 (flight_test_landing_x_drift_1): resting on the ground
	 * the module repeats one stale velocity (exactly 3,5 and 2,3 cm/s, std 0,
	 * quality 255). With ZUPT pinning v=0 these updates taught it to bof, and
	 * the hover flew that fake bias as a steady +x drift. Use OF only when
	 * not on the ground and above the range floor. */
	if (of_ok && !on_ground && ano_of.of_alt_cm >= OF_HANDHELD_MIN_ALT_CM) {
		if (EKF_OF_UPDATE_ON_NEW_FRAME) {
			if (ano_of.of_update_cnt != s_last_of_update_cnt) {
				EkfOf_Update(&s_ekf_of, ofx, ofy);
				s_last_of_update_cnt = ano_of.of_update_cnt;
			}
		} else {
			EkfOf_Update(&s_ekf_of, ofx, ofy);
		}
	}
	/* WP-14: innovation-based health gate with persistence.
	 * innov_x/y are the post-update residuals set in EkfOf_Update.
	 * Trip only after EKF_OF_HEALTH_PERSIST consecutive ticks (0.5 s)
	 * with innovation magnitude > EKF_OF_HEALTH_THRESH.
	 * In shadow mode: only set health=0, never change g_of_bias_mode. */
	innov_mag = s_ekf_of.innov_x * s_ekf_of.innov_x
	          + s_ekf_of.innov_y * s_ekf_of.innov_y;
	if (innov_mag > (EKF_OF_HEALTH_THRESH * EKF_OF_HEALTH_THRESH)) {
		if (s_ekf_of_innov_bad_cnt < 0xFFFFU) {
			s_ekf_of_innov_bad_cnt++;
		}
		if (s_ekf_of_innov_bad_cnt >= EKF_OF_HEALTH_PERSIST) {
			g_ekf_of_health = 0U;
			s_ekf_of_tripped = 1U;
			/* Fall back only when the KF drives the position loop (armed,
			 * FLYING/LANDING). On the ground or in handheld the re-init
			 * below is enough and mode 2 stays selected. */
			if (g_of_bias_mode == OF_BIAS_EKF && !on_ground && !g_of_handheld_test) {
				g_of_bias_mode  = OF_BIAS_FIXED; /* forced fallback to FIXED */
				g_ekf_of_fallback = 1U;
				s_ekf_of_mode_restore = 1U;
			}
			/* Re-init from a clean state on the next tick instead of
			 * running on the diverged one (shadow and mode 2 alike). */
			s_ekf_of_inited = 0U;
			s_ekf_of_innov_bad_cnt = 0U;
		}
	} else {
		s_ekf_of_innov_bad_cnt = 0U;
		if (!s_ekf_of_tripped) {
			g_ekf_of_health = 1U;
		}
	}
}

/* ---- Position integration: Modes 0/1 use raw OF, Mode 2 uses KF ----
 * FIX 2026-09-26: one ground-hold gate for all modes (mode 2 used to
 * integrate on the ground, so armed-idle drift became a takeoff step).
 * Handheld alt is band-limited: raw u32 glitches (2905, 0xFFFFFFFF).
 * Output: ano_of.earth_x/y (+ _ture) and Ctrler.locx/locyPID.FB, cm. */
static void Of_IntegratePosition(void)
{
	u8 pos_integrate;
	pos_integrate = (u8)(ano_of.of_quality >= OF_MIN_QUALITY &&
	    ((g_of_handheld_test && ano_of.of_alt_cm >= OF_HANDHELD_MIN_ALT_CM &&
	      ano_of.of_alt_cm <= ALT_BAND_MAX_CM) ||
	     (DroneStatus.ARM_Status == Armed &&
	      (flight_phase == FLIGHT_PHASE_FLYING || flight_phase == FLIGHT_PHASE_LANDING))));
	if (g_of_bias_mode == OF_BIAS_EKF) {
		/* Mode 2: EKF state x[0]=pos_x, x[3]=pos_y is already debiased by
		 * construction. Apply yaw rotation and feed directly to PID FB.
		 * EkfOf_ResetPos() was called from Reset_World_Origin() below.
		 * FIX 2026-09-26: KF states are metres; earth_x/y and locx/yPID
		 * are cm (modes 0/1), so scale by 100. */
		float ekf_dx = (s_ekf_of.x[0] - s_ekf_prev_px) * CM_PER_M;
		float ekf_dy = (s_ekf_of.x[3] - s_ekf_prev_py) * CM_PER_M;
		if (!s_ekf_pos_synced) {
			ekf_dx = 0.0f;
			ekf_dy = 0.0f;
			s_ekf_pos_synced = 1U;
		}
		s_ekf_prev_px = s_ekf_of.x[0];
		s_ekf_prev_py = s_ekf_of.x[3];
		if (pos_integrate) {
			ekf_dx *= g_of_scale;   /* to true cm before the tilt step mixes in height */
			ekf_dy *= g_of_scale;
			/* WP-21 A: m/s * 100 * 0.005 s = cm/tick, same units as ekf_dx/dy */
			if (g_of_full_tilt) of_full_tilt_delta(&ekf_dx, &ekf_dy, ano_of.of2_h_f2_v * OF_HRATE_TO_CM_TICK);
			ano_of.earth_x += ekf_dx * Cos_Yaw_01 + ekf_dy * Sin_Yaw_01;
			ano_of.earth_y += ekf_dy * Cos_Yaw_01 - ekf_dx * Sin_Yaw_01;
		}
		ano_of.earth_x_ture =  ano_of.earth_y;
		ano_of.earth_y_ture = -ano_of.earth_x;
		Ctrler.locxPID.FB = ano_of.earth_x_ture;
		Ctrler.locyPID.FB = ano_of.earth_y_ture;
	} else {
		s_ekf_pos_synced = 0U;
		/* WP-14: reached only when mode != 2. Clear the init flag only if the KF
		 * is not ticking either (shadow off), so a later start re-inits; in
		 * shadow the KF keeps running across mode changes and keeps its state. */
		if (!g_ekf_of_shadow) {
			s_ekf_of_inited = 0U;
			s_ekf_of_tripped = 0U;
		}
		/* Modes 0 and 1: debias raw OF and integrate as before.
		 * FIX 2026-09-26: hold position while on the ground (disarmed,
		 * GROUND_IDLE or LANDED): OF zero wanders 1-3 counts at rest,
		 * integrating to cm/s drift before takeoff. */
		if (pos_integrate)
		{
			float of_dx_deb = ano_of.of2_dx_fix - s_of_bias_x;
			float of_dy_deb = ano_of.of2_dy_fix - s_of_bias_y;

			ano_of.DISTANCE_X = ano_of.DISTANCE_X+of_dx_deb*STAB_DT_S;
			ano_of.DISTANCE_Y = ano_of.DISTANCE_Y+of_dy_deb*STAB_DT_S;

			float of_dx_t = of_dx_deb*STAB_DT_S*g_of_scale;   /* cm/tick, true cm */
			float of_dy_t = of_dy_deb*STAB_DT_S*g_of_scale;
			/* WP-21 A: DISTANCE_X/Y above stay raw body-frame */
			if (g_of_full_tilt) of_full_tilt_delta(&of_dx_t, &of_dy_t, ano_of.of2_h_f2_v * OF_HRATE_TO_CM_TICK);

			ano_of.earth_x = ano_of.earth_x + (of_dx_t*Cos_Yaw_01 + of_dy_t*Sin_Yaw_01 );
			ano_of.earth_y = ano_of.earth_y + (of_dy_t*Cos_Yaw_01 - of_dx_t*Sin_Yaw_01 );
		}
		ano_of.earth_x_ture  =  ano_of.earth_y;
		ano_of.earth_y_ture  =  -ano_of.earth_x;
		Ctrler.locxPID.FB= ano_of.earth_x_ture ;
		Ctrler.locyPID.FB= ano_of.earth_y_ture ;
	}
}

/* ADR-0011 Phase 3 + Phase 4 calibrators, one step each per tick. */
static void Cal_Step(void)
{
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
		float a_meas_x = Lin_Acc_X_body + MG_PER_G * Gravity_Body_X;
		float a_meas_y = Lin_Acc_Y_body + MG_PER_G * Gravity_Body_Y;
		float a_meas_z = Lin_Acc_Z_body + MG_PER_G * Gravity_Body_Z;
		CalTrim_Step(&s_cal_trim,
		             0.0f, 0.0f, MG_PER_G,
		             a_meas_x, a_meas_y, a_meas_z,
		             1U);
		if (s_cal_trim.state == CAL_TRIM_STATE_SETTLED) {
			g_cal_health |= CAL_HEALTH_AIRBORNE_OK;
		} else if (s_cal_trim.state == CAL_TRIM_STATE_DEGRADED) {
			g_cal_health |= CAL_HEALTH_AIRBORNE_DEGRADED;
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
			g_cal_health |= CAL_HEALTH_HOT_REJECTED;  /* sticky until next quiescent cycle */
		} else if (s_cal_hot.state == CAL_HOT_STATE_WAIT_STILL && !s_cal_hot.cleared) {
			/* One-shot commit fired: HOT_HOVER_OK. */
			g_cal_health |= CAL_HEALTH_HOT_HOVER_OK;
			s_cal_hot.cleared = 1U;
		}
	}
}

/* Horizontal velocity FB (cm/s, world frame) for locxsPID/locysPID. */
static void Of_UpdateVelocityFB(void)
{
	/* EKF velocity only when selected (on ground) and still healthy; else debiased OF.
	 * FIX 2026-10-02: was raw of2_dx/dy, which no bias mode ever corrected. Same
	 * motion as of2_*_fix (gain 1.0-1.1, corr 0.92-0.97, no lag, hover logs
	 * 2026-10-01/02) but it read +8..+14 cm/s at rest on 2026-10-02, and the
	 * loop flew that phantom away as a ~12 cm -x drift. Now it uses the same
	 * debiased velocity the position loop integrates (modes 0/1/2). */
	float fb_dx = (float)ano_of.of2_dx_fix - s_of_bias_x;
	float fb_dy = (float)ano_of.of2_dy_fix - s_of_bias_y;
	if (g_ekf_gate.ctrl_enable && g_ekf_gate.healthy) {
		fb_dx = g_ekf_gate.vx_cms;
		fb_dy = g_ekf_gate.vy_cms;
	}
	/* WP-14: OF EKF velocity feedback. When enabled AND mode 2 AND
	 * healthy, KF body velocity (m/s, x[1]/x[4]) replaces raw OF.
	 * Same frame and units as fb_dx/fb_dy after *100 → cm/s. */
	if (g_ekf_of_vel_fb && g_of_bias_mode == OF_BIAS_EKF && g_ekf_of_health) {
		fb_dx = s_ekf_of.x[1] * CM_PER_M;   /* vel_x body, cm/s */
		fb_dy = s_ekf_of.x[4] * CM_PER_M;   /* vel_y body, cm/s */
	}
	fb_dx *= g_of_scale;   /* true cm/s, same scale as locx/locyPID.FB */
	fb_dy *= g_of_scale;
	Ctrler.locxsPID.FB= (fb_dy) *Cos_Yaw_01 +(-fb_dx)*Sin_Yaw_01;
	Ctrler.locysPID.FB=  (-fb_dx) * Cos_Yaw_01 - (fb_dy)*Sin_Yaw_01;
}

/* Altitude sanity gate (of_alt_cm is cm, u32; clamped to u16 below). Three layers (ADR-0011 Z-gate),
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
 *      so a genuine sustained level change is not locked out.
 * Then the Z FB: Z_posPID.FB = height (m), Z_ratePID.FB = low-passed climb rate (m/s), both held
 * on a rejected tick (s_alt_valid_tick = 0, P0 Finding 2). */
static void Alt_UpdateFB(void)
{
	{
		static u16 s_alt_reject_cnt = 0U;
		static u16 s_alt_hist[3] = {0U, 0U, 0U};
		static uint8_t s_alt_hist_n = 0U;
		u16 a0, a1, a2, alt_med;

		/* Layer 1: median-of-3. */
		s_alt_hist[2] = s_alt_hist[1];
		s_alt_hist[1] = s_alt_hist[0];
		/* of_alt_cm is u32: clamp, a bare cast aliases 65536+x to x cm */
		s_alt_hist[0] = (ano_of.of_alt_cm > 0xFFFFU) ? 0xFFFFU : (u16)ano_of.of_alt_cm;
		if (s_alt_hist_n < 3U) s_alt_hist_n++;
		if (s_alt_hist_n >= 3U)
		{
			a0 = s_alt_hist[0]; a1 = s_alt_hist[1]; a2 = s_alt_hist[2];
			alt_med = (a0 > a1) ? ((a1 > a2) ? a1 : ((a0 > a2) ? a2 : a0))
			                    : ((a0 > a2) ? a0 : ((a1 > a2) ? a2 : a1));
		}
		else
		{
			alt_med = s_alt_hist[0];  /* warmup: not enough history yet */
		}

		/* Layer 2: band gate. */
		if( alt_med >= ALT_BAND_MIN_CM && alt_med <= ALT_BAND_MAX_CM )
		{
			float h_new = alt_med*M_PER_CM*Cos_roll_01*Cos_pitch_01;
			/* Layer 3: rate-aware jump gate. */
			float gate = ALT_JUMP_BASE_M + ALT_JUMP_PER_VZ * fabsf(Ctrler.Z_ratePID.Des);
			if (gate > ALT_JUMP_MAX_M) gate = ALT_JUMP_MAX_M;
			if( fabsf(h_new - ano_of.of2_raw_h) < gate || s_alt_reject_cnt >= ALT_RESYNC_REJECTS )
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
		ano_of.of2_h_v = (ano_of.of2_h - ano_of.of2_last_h )/(STAB_DT_S);  /* one tick, 5 ms */
		ano_of.of2_last_h = ano_of.of2_h;
		ano_of.of2_h_f2_v  = ano_of.of2_h_f2_v  *ALT_RATE_LPF_OLD +ano_of.of2_h_v *ALT_RATE_LPF_NEW;

		Ctrler.Z_posPID.FB =  ano_of.of2_h;
		Ctrler.Z_ratePID.FB = ano_of.of2_h_f2_v ;
	}
}

/* Angle FB from imu_data (one critical section, so the three angles are one sample) and rate FB. */
static void Att_UpdateFB(void)
{
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

/* Sensors -> loop feedback, in this order: trig cache, OF bias + OF KF, OF position, calibrators,
 * OF velocity, ToF height, attitude. */
void Update_Data(void)
{
	Att_UpdateTrig();

	{
		u8 of_ok = (ano_of.of_quality >= OF_MIN_QUALITY);
		Of_UpdateBias(of_ok);
		if (g_of_bias_mode == OF_BIAS_EKF || g_ekf_of_shadow) {
			Of_TickKf(of_ok);
		}
	}
	Of_IntegratePosition();

	Cal_Step();

	Of_UpdateVelocityFB();

	Alt_UpdateFB();

	Att_UpdateFB();
}

/* ==== Update_Motor: motor gating by arm state and flight phase ===================================== */
#define LAND_RATE_SLOW_ALT_M   0.20f    /* below this height the touchdown rate threshold widens */
#define LAND_RATE_THR_LOW      0.08f    /* m/s, |Z rate| that counts as still below LAND_RATE_SLOW_ALT_M */
#define LAND_RATE_THR_HIGH     0.02f    /* m/s, |Z rate| that counts as still above it */
#define LAND_STABLE_TICKS      10       /* still ticks the rate test needs (s_stable_ticks is an int) */
#define LAND_CUT_ALT_M         0.15f    /* the rate test only cuts below this height */
#define GROUND_REST_MAX_M      0.15f    /* rest height is sampled only below this (hand-lift guard) */

/* Landing detector state. Reset on every way out of LANDING: the cut itself (Land_Step),
 * EMERGENCY and DISARMED (Land_ResetDetector). */
static uint8_t  s_land_init    = 0U;
static int      s_stable_ticks = 0;
static uint16_t s_land_rest_ticks = 0U;
static uint16_t s_land_timeout = 0U;
static float    s_land_rest_z  = LAND_REST_MIN;   /* ground z sampled in GROUND_IDLE */

/* Bench-mode height zero: captures the fixture resting height when bench mode
 * activates (0→1 transition). The Z position PID then sees (of2_h - offset),
 * so the drone treats its current height as zero — prop wash displaces it by
 * ±Δ and the controller does not fight to return to a virtual ground.
 * Only captures on 0→1 transition; the offset holds until bench mode deactivates. */
static void Bench_ZeroHeight(void)
{
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
}

/* Motor bench-test override (CMD 0x16): drive ONE chosen motor to a commanded CCR
 * for the thrust-stand experiment. Strictly DISARMED-only, with a dead-man — if the
 * dashboard stops sending heartbeats (watchdog exceeds the window) or anything leaves
 * DISARMED, motors are zeroed and test mode exits. RC/arming stays the final authority.
 * Set_PWM_Motors() applies the [2000,4000] clamp. See docs/bench_characterization.md.
 * Returns 1 when it owned the motors this tick. */
static uint8_t Motor_TestOverride(FlightState_t state)
{
	if (motor_test_active)
	{
		if (state != FLIGHT_STATE_DISARMED ||
		    ++motor_test_watchdog > MOTOR_TEST_DEADMAN_TICKS)
		{
			motor_test_active = 0U;
			Set_Zero_Motors();
			return 1U;
		}
		mymotor.motor1 = (motor_test_id == 1U) ? (short)motor_test_ccr : Motor_PWM_ZERO;
		mymotor.motor2 = (motor_test_id == 2U) ? (short)motor_test_ccr : Motor_PWM_ZERO;
		mymotor.motor3 = (motor_test_id == 3U) ? (short)motor_test_ccr : Motor_PWM_ZERO;
		mymotor.motor4 = (motor_test_id == 4U) ? (short)motor_test_ccr : Motor_PWM_ZERO;
		Set_PWM_Motors();
		return 1U;
	}
	return 0U;
}

/* Keil debug manual motor override (dbg_motor_manual / dbg_motor_ccr, see their definition).
 * DISARMED-only. Returns 1 when it owned the motors this tick. */
static uint8_t Motor_DebugOverride(FlightState_t state)
{
	if (dbg_motor_manual)
	{
		if (state != FLIGHT_STATE_DISARMED)
		{
			dbg_motor_manual = 0U;
			Set_Zero_Motors();
			return 1U;
		}
		mymotor.motor1 = (short)dbg_motor_ccr[0];
		mymotor.motor2 = (short)dbg_motor_ccr[1];
		mymotor.motor3 = (short)dbg_motor_ccr[2];
		mymotor.motor4 = (short)dbg_motor_ccr[3];
		Set_PWM_Motors();
		return 1U;
	}
	return 0U;
}

/* ARMED + LANDING: drive the motors, then the touchdown detector; on touchdown LANDED + disarm. */
static void Land_Step(void)
{
	if (!s_land_init)
	{
		s_stable_ticks = 0;
		s_land_rest_ticks = 0U;
		s_land_timeout = 0U;
		s_land_init    = 1U;
	}

	/* PID-controlled descent: Z_posPID.Des ramps down in Des_Height
	 * at 0.30 m/s; rate cascade follows; Throttle_out drives motors to ground.
	 * Integrator winds negative during descent so Throttle_out is already below
	 * hover at touchdown — no motor spike when LANDED fires. */
	Set_PWM_Motors();

	/* Touchdown detection: rate stable for 0.25 s AND near ground.
	 * Below 0.20 m a 0.15 m/s sink bias is active, so widen threshold to
	 * 0.08 m/s — the drone decelerates through that on contact.
	 * Above 0.20 m keep tight (0.02 m/s) to avoid false triggers mid-descent.
	 * FIX 2026-10-02: also require the Z setpoint ramp to have reached the floor.
	 * hover_7 and unique_position1 disarmed at 0.13-0.14 m while floating on the
	 * ground-effect cushion (vz ~0), then fell 4-8 cm with motors off. Gated, the
	 * existing sink bias pushes through the cushion and motors cut on the ground.
	 * FIX 2026-10-02b: Des <= 0.01 did not stop it (auto_landing_1 F2-F5 still cut at
	 * 0.11-0.14 m: Des leads FB, so Des hits the floor while still airborne).
	 * Now also require ground evidence (PX4 land detector style), either:
	 *   (a) FB within 0.03 m of the rest height read before takeoff
	 *       (measured rest 0.00-0.07 m, so mid-air cuts at 0.11-0.14 m are blocked), or
	 *   (b) sink bias saturated: 0.40 m/s commanded descent not achieved for ~2 s.
	 * (b) covers a rest reading that shifts after touchdown (F3: 0.06 pre, 0.10 post).
	 * Safety net: force disarm after LAND_MAX_TICKS (15 s) regardless. */
	{
		float rate_thr = (Ctrler.Z_posPID.FB < LAND_RATE_SLOW_ALT_M) ? LAND_RATE_THR_LOW : LAND_RATE_THR_HIGH;
		if (fabsf(Ctrler.Z_ratePID.FB) < rate_thr)
			s_stable_ticks++;
		else
			s_stable_ticks = 0;
	}

	if (Ctrler.Z_posPID.Des <= DES_FLOOR_M &&
	    Ctrler.Z_posPID.FB <= s_land_rest_z + LAND_REST_MARGIN)
		s_land_rest_ticks++;
	else
		s_land_rest_ticks = 0U;

	s_land_timeout++;

	if (s_land_rest_ticks >= LAND_REST_CUT_TICKS ||
	    (s_stable_ticks >= LAND_STABLE_TICKS && Ctrler.Z_posPID.FB < LAND_CUT_ALT_M &&
	     Ctrler.Z_posPID.Des <= DES_FLOOR_M &&
	     (Ctrler.Z_posPID.FB <= s_land_rest_z + LAND_REST_MARGIN ||
	      s_land_sink_bias >= LAND_SINK_BIAS_MAX)) ||
	    s_land_timeout >= LAND_MAX_TICKS)
	{
		s_stable_ticks    = 0;
		s_land_rest_ticks = 0U;
		s_land_timeout    = 0U;
		s_land_init       = 0U;
		s_land_sink_bias  = 0.0f;
		flight_phase      = FLIGHT_PHASE_LANDED;
		FlightFSM_Event(FLIGHT_EVENT_DISARM_REQUEST);
		Set_Zero_Motors();
	}
}

/* ARMED + GROUND_IDLE: zero until the idle gesture, then rest-height sampling, takeoff detection
 * and idle/PWM output. */
static void GroundIdle_Step(void)
{
	if (!g_motor_idle_enabled)
	{
		/* ARMED but idle not yet enabled: motors at zero, integrators
		 * cleared so they do not wind up while waiting for the idle
		 * gesture.  Takeoff detection is impossible in this state. */
		Clear_Structure();
		Set_Zero_Motors();
	}
	else
	{
		/* Rest height for the touchdown gate: sample while motors idle on the ground.
		 * FB < 0.15 keeps a hand-lifted reading out, so the gate is never looser
		 * than the FB < 0.15 term in Land_Step. */
		if (!TWC.execute && RCInput_Get(RC_AXIS_THR) < THR_IDLE_MAX &&
		    Ctrler.Z_posPID.FB < GROUND_REST_MAX_M)
			s_land_rest_z = (Ctrler.Z_posPID.FB > LAND_REST_MIN) ?
			                Ctrler.Z_posPID.FB : LAND_REST_MIN;

		/* Auto-detect takeoff: transition to FLYING once the OF sensor reads > 0.2 m above
		 * the zeroed height. Bench mode (CMD 0x07) does NOT block this — the height
		 * offset is captured on bench activation so of2_h is already relative to the fixture
		 * resting height. The controller therefore does not fight prop wash at any altitude.
		 * Throttle_cap_active (CMD 0x08) is also independent and does not affect this. */
		/* P0 Finding 3: height alone must not leave GROUND_IDLE — a slow hand-lift of an
		 * armed aircraft ramps of2_h past every alt gate, so gate the transition itself
		 * on THR >= 20% or an executing TWC policy, matching the IDLE interlock below. */
		if (Ctrler.Z_posPID.FB > TAKEOFF_ALT_M &&
		    (TWC.execute || RCInput_Get(RC_AXIS_THR) >= THR_IDLE_MAX))
			flight_phase = FLIGHT_PHASE_FLYING;

		/* IDLE motors: hold until pilot or policy pushes THR above 20%. */
		if (!TWC.execute && RCInput_Get(RC_AXIS_THR) < THR_IDLE_MAX)
			Set_IDLE_Motors();
		else if (SDK_DelayWakeFlag == 1)
			Set_IDLE_Motors();
		else
			Set_PWM_Motors();
	}
}

/* EMERGENCY and DISARMED: forget any landing in progress. */
static void Land_ResetDetector(void)
{
	s_land_init      = 0U;
	s_stable_ticks   = 0;
	s_land_timeout   = 0U;
	s_land_sink_bias = 0.0f;
}

void Update_Motor(void)
{
	FlightState_t state;

	Bench_ZeroHeight();

	state = FlightFSM_GetState();

	if (Motor_TestOverride(state)) return;

	if (Motor_DebugOverride(state)) return;

	if (state == FLIGHT_STATE_ARMED)
	{
		if (flight_phase == FLIGHT_PHASE_LANDING)
		{
			Land_Step();
		}
		else if (flight_phase == FLIGHT_PHASE_GROUND_IDLE)
		{
			GroundIdle_Step();
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
		Land_ResetDetector();
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
		Land_ResetDetector();
		TWC.execute = 0U;
		sbus_flyup_trigger = 0U;
		SDK_StateMachine_Init();
		Clear_Structure();
		Set_Zero_Motors();
	}
}

/* ==== Compute_Motor: setpoints, the PID/MRAC cascade, the mixer ==================================== */
/* WP-9 hover means in the controller frame (pitchPID frame = -imu_pit), operator zeroes them if the
 * rotated-takeoff flight shows the lean is the room. */
volatile float g_att_trim_roll_deg = -1.24f;
volatile float g_att_trim_pitch_deg = -0.90f;
volatile uint8_t g_traj_ff_on = 1U;
static TrajFF_t s_traj_ff;
/* Trajectory feed-forward (TrajFF_Step) on the xy velocity loops. Vmax for the FF clamp: the v_max_mps
 * of wfb_traj_default_limits (API/wfb_traj.c) x 100 = 100.0f cm/s. */
#define TRAJ_FF_DT_S           0.01f    /* the xy loops run every 2nd tick */
#define TRAJ_FF_TAU_S          0.2f
#define TRAJ_FF_VMAX_CMS       100.0f
#define OF_PRE_EMA_ALPHA       0.005f   /* 1 s EMA of the resting OF velocity at 200 Hz */
#define PWM_PCT_BASE           2000.0f  /* CCR at 0 % (gs_throttle_min/max_pct are 0..1) */
#define PWM_PCT_SPAN           2000.0f  /* CCR per 100 % */
#define GRAVITY_STD_MPS2       9.80665f

/* WP-21 B ground contact: in LANDING with the Z ramp at the floor, low and slow for
 * LAND_CONTACT_TICKS. Returns 1 while in contact (the xy integrators are then held). */
static uint8_t Land_ContactStep(void)
{
	static uint8_t s_land_contact_cnt = 0U;   /* WP-21 B, see LAND_CONTACT_* */
	if (flight_phase == FLIGHT_PHASE_LANDING && Ctrler.Z_posPID.Des <= DES_FLOOR_M &&
	    Ctrler.Z_posPID.FB < LAND_CONTACT_ALT && fabsf(Ctrler.Z_ratePID.FB) < LAND_CONTACT_VZ) {
		if (s_land_contact_cnt < LAND_CONTACT_TICKS) s_land_contact_cnt++;
	} else {
		s_land_contact_cnt = 0U;
	}
	return (uint8_t)(s_land_contact_cnt >= LAND_CONTACT_TICKS);
}

/* Height (every 2nd tick) and climb-rate loops. P0 Finding 2: while the ToF sample is invalid
 * (s_alt_valid_tick == 0) each integrator is restored after ComputePID, so P/D run on the held FB. */
static void Z_Compute(void)
{
	Update_Des(case_Update_height_Des);
	cnt_h++;
	if(cnt_h>=POS_LOOP_DIV)
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
}

/* FIX 2026-09-13: ARM rising-edge world-origin rebase.
 * The cold-boot sync (Cal_EstimatorReadyEdge) syncs Des=FB at the
 * rising edge of g_estimator_ready — but that fires at boot, before
 * the OF has integrated any drift. After a long disarmed sit on the
 * bench, FB can drift to ~+200/-188 cm while the drone is physically
 * still. Arming then opens the position loop with a -195/+188 error,
 * which immediately saturates the SumE and flies the drone into a
 * wall. Fix it the same way the dashboard "Reset World Origin" button
 * does (CMD 0x10): on the ARM 0→1 edge, rebase FB/Des/OF bias to zero
 * so the position loop opens with zero error and the drone physically
 * holds until the pilot commands throttle. The pilot can still hit
 * the dashboard button mid-flight to re-anchor the path.
 * Runs every 2nd tick, armed or not. Also: the resting-OF average (g_of_rest_warn) and the
 * handheld-test enable edge. */
static void Pos_ArmEdge(void)
{
	static uint8_t s_prev_armed = 0U;
	/* FIX 2026-09-26: OF is integer counts, so a single-sample bias
	 * snap leaves up to 1 count residual that integrates to ~1.4 cm/s
	 * drift. While disarmed, average OF with a 1 s EMA and snap to that. */
	static float s_of_pre_x = 0.0f, s_of_pre_y = 0.0f;
	static uint8_t s_of_pre_ok = 0U;
	static uint8_t s_prev_handheld = 0U;
	uint8_t armed_now = (DroneStatus.ARM_Status == Armed) ? 1U : 0U;
	/* FIX 2026-09-26: keep averaging while armed on the ground too and
	 * track the Mode 0 bias with it, so the bias locks at takeoff. */
	uint8_t on_ground = (!armed_now || (flight_phase != FLIGHT_PHASE_FLYING &&
	                     flight_phase != FLIGHT_PHASE_LANDING)) ? 1U : 0U;
	if (!on_ground) g_of_handheld_test = 0U; /* never carried into flight */
	/* FIX 2026-09-27: GROUND_IDLE lasts until of2_h > 0.2 m, so the drone
	 * is already airborne and moving while still "on_ground". Averaging then
	 * locked the lift-off velocity into the Mode 0 bias, and the position
	 * loop held that velocity as "zero" for the whole flight (slow one-way
	 * drift). Average only while motors are off or at idle (THR < 20%). */
	/* FIX 2026-10-02 (hover_5): props spinning at idle on the ground made the
	 * OF read -1 cm/s y for 2.4 s with the drone still, and this average
	 * locked -0.66 as the bias, so the hold flew that fake velocity all
	 * flight. Average only while the motors are stopped. */
	if (armed_now && (g_motor_idle_enabled || TWC.execute ||
	                  RCInput_Get(RC_AXIS_THR) >= THR_IDLE_MAX))
		on_ground = 0U;
	if (on_ground && !g_of_handheld_test && ano_of.of_quality >= OF_MIN_QUALITY) {
		if (!s_of_pre_ok) {
			s_of_pre_x = (float)ano_of.of2_dx_fix;
			s_of_pre_y = (float)ano_of.of2_dy_fix;
			s_of_pre_ok = 1U;
		} else {
			s_of_pre_x += OF_PRE_EMA_ALPHA * ((float)ano_of.of2_dx_fix - s_of_pre_x);
			s_of_pre_y += OF_PRE_EMA_ALPHA * ((float)ano_of.of2_dy_fix - s_of_pre_y);
		}
		g_of_rest_warn = (fabsf(s_of_pre_x) > OF_REST_WARN_CMS
		               || fabsf(s_of_pre_y) > OF_REST_WARN_CMS) ? 1U : 0U;
	}
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
		/* FIX 2026-10-03: no ground snap. The resting OF value is a stale
		 * repeat, not a bias (good flights read 0 at rest and ~0-1 cm/s in
		 * hover; the drift flights read 3,5 and 2,3 at rest). Start every
		 * flight from zero total bias: s_of_bias = 0 (Reset_World_Origin) and
		 * KF bof = 0 with a tight variance, so the hold flies true zero flow. */
		s_of_bias_seeded = 1U;
		if (s_ekf_of_inited) {
			EkfOf_ResetBias(&s_ekf_of, EKF_OF_BOF_ARM_VAR);
			s_ekf_of.x[1] = 0.0f;
			s_ekf_of.x[4] = 0.0f;
		}
		/* Clear EKF fallback sticky flag on ARM 0→1 edge so the
		 * dashboard sees a clean state for each new flight. */
		g_ekf_of_fallback = 0U;
		s_ekf_of_innov_bad_cnt = 0U;
		s_ekf_of_tripped = 0U;
		g_ekf_of_health = 1U;
		if (s_ekf_of_mode_restore) {
			s_ekf_of_mode_restore = 0U;
			g_of_bias_mode = OF_BIAS_EKF;   /* mode-2 tick re-inits the KF if shadow was off */
		}
	}
	/* Handheld test enabled: zero the origin and the bias (same rule as ARM;
	 * the resting OF value is stale, not a bias). Hold still when enabling. */
	if (g_of_handheld_test && !s_prev_handheld) {
		Reset_World_Origin();
		if (s_ekf_of_inited) {
			EkfOf_ResetBias(&s_ekf_of, EKF_OF_BOF_ARM_VAR);
			s_ekf_of.x[1] = 0.0f;
			s_ekf_of.x[4] = 0.0f;
		}
	}
	s_prev_handheld = g_of_handheld_test;
	s_prev_armed = armed_now;
}

/* xy position loops, then the velocity loops with the trajectory feed-forward. Armed only. */
static void Pos_Compute(uint8_t airborne, uint8_t land_contact)
{
	Update_Des(case_Update_loc_Des);
	ComputePID_GatedHold(&Ctrler.locxPID, airborne, land_contact);
	ComputePID_GatedHold(&Ctrler.locyPID, airborne, land_contact);

	Update_Des(case_Update_v_loc_Des);
	SDK_Set_V_Loc();   /* SDK velocity override (AutoflyTask) */

	{
		uint8_t active = g_traj_ff_on && airborne && TWC.execute && g_wfb_status.traj_state == (float)WFB_TRAJ_EXECUTING && g_wfb_status.prim_state == (float)WFB_PRIM_TRAJ;
		float vx, vy, ax, ay;
		TrajFF_Step(&s_traj_ff, active, TWC.target_x, TWC.target_y, TRAJ_FF_DT_S, TRAJ_FF_TAU_S, TRAJ_FF_VMAX_CMS, &vx, &vy, &ax, &ay);
		Ctrler.locxsPID.Des += vx;
		Ctrler.locysPID.Des += vy;
		ComputePID_GatedHold(&Ctrler.locxsPID, airborne, land_contact);
		ComputePID_GatedHold(&Ctrler.locysPID, airborne, land_contact);
		Ctrler.locxsPID.U += ax;
		Ctrler.locysPID.U += ay;
	}
}

/* Angle loops (pitch/roll gated on airborne, yaw), rate setpoints + SysID dither, rate loops. */
static void Att_Compute(uint8_t airborne)
{
	Update_Des(case_Update_pitrol_Des);   /* pitch/roll angle setpoints */
	ComputePID_Gated(&Ctrler.pitchPID, airborne);
	ComputePID_Gated(&Ctrler.rollPID, airborne);

	Update_Des(case_Update_yaw_Des);      /* yaw angle setpoint */
	ComputeYawPID(&Ctrler.yawPID);

	Update_Des(case_Update_gyro_Des);     /* rate setpoints */

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

	/* FIX 2026-09-27: flight8 wound gyrozPID up to U=650 on the ground at idle
	 * (heading-hold Des with the airframe unable to turn). Hold the yaw integrals
	 * at 0 until lift-off throttle; same idle test as the OF bias averaging. */
	if (flight_phase != FLIGHT_PHASE_FLYING && flight_phase != FLIGHT_PHASE_LANDING &&
	    !TWC.execute && RCInput_Get(RC_AXIS_THR) < THR_IDLE_MAX)
	{
		Ctrler.gyrozPID.SumE = 0.0f;
		Ctrler.yawPID.SumE   = 0.0f;
	}

	ComputePID_Gated(&Ctrler.gyroxPID, airborne);
	ComputePID_Gated(&Ctrler.gyroyPID, airborne);
	ComputePID(&Ctrler.gyrozPID);
}

/* Controller layer, throttle window, quad-X mixer into mymotor (CCR; Set_PWM_Motors clamps later). */
static void Mix_Compute(void)
{
	 //Throttle_th=2800+(16.70f-real_voltage)*105.5f;  //4s+d435i+t265+orin
	 // Fixture (extendable metal) adds load. 3200 covers ~+250 g fixture at 4S full;
	 // free-flight hover 2950->3150: flight_test_pid_2/_4 (matched props, 2026-09-28) hovered at
	 // motor avg 3135-3145 with Z_rate P carrying the missing ~190 -> 0.4-0.5 m altitude sag.
	 // PWM ceiling is 4000 - anything above risks ESC saturation.
	 Throttle_th = bench_mode_active ? (short)HOVER_THR_BENCH : (short)HOVER_THR_FREE;

	// Controller layer (API/controller.c): u = u_nom + correction of the selected controller.
	// CTRL_MRAC (default) == PID + MRAC u_ad gated by output_injection_on and simplex fade; CTRL_PID == pure PID.
	Throttle_out = Controller_Update(CTRL_AXIS_Z, Ctrler.Z_ratePID.U) + Throttle_th;
	u_gyrox      = Controller_Update(CTRL_AXIS_ROLL, Ctrler.gyroxPID.U);
	u_gyroy      = -Controller_Update(CTRL_AXIS_PITCH, Ctrler.gyroyPID.U); // Motor mixer needs gyroy reversed
	u_gyroz      = Controller_Update(CTRL_AXIS_YAW, Ctrler.gyrozPID.U);
	{
		float pwm_lo = PWM_PCT_BASE + gs_throttle_min_pct * PWM_PCT_SPAN;
		float pwm_hi = PWM_PCT_BASE + gs_throttle_max_pct * PWM_PCT_SPAN;
		if (pwm_hi < pwm_lo) {
			float t = pwm_hi;
			pwm_hi = pwm_lo;
			pwm_lo = t;
		}
		Throttle_out = Constrain_Float(Throttle_out, pwm_lo, pwm_hi);
	}

	// CONSTRAINT: the motor signs live in one place, the MIX_ROW table in API/controller.c (also used by the
	// MRAC V2 saturation deficit). Keep it in sync with the physical motor map and pwm.h channel mapping.
	// WHY: Sign or channel drift here can invert closed-loop attitude response.
	// g_yaw_mix_dir (+-1) is read once per motor, as before.
	mymotor.motor1 = Mix_Motor(0U, Throttle_out, u_gyroy, u_gyrox, g_yaw_mix_dir*u_gyroz);   /* M1 CW  */
	mymotor.motor2 = Mix_Motor(1U, Throttle_out, u_gyroy, u_gyrox, g_yaw_mix_dir*u_gyroz);   /* M2 CW  */
	mymotor.motor3 = Mix_Motor(2U, Throttle_out, u_gyroy, u_gyrox, g_yaw_mix_dir*u_gyroz);   /* M3 CCW */
	mymotor.motor4 = Mix_Motor(3U, Throttle_out, u_gyroy, u_gyrox, g_yaw_mix_dir*u_gyroz);   /* M4 CCW */
}

/* Shadow thrust estimators (200 Hz, after motor mixer, before Set_PWM_Motors).
 * Three models: empirical (PWM→thrust bench LUT), blade-element (RPM→thrust),
 * IMU-derived (accel projection). Telemetry-only, no control feedback. */
static void ThrustEst_Step(void)
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

	ThrustEst_Update(motor_pwm, motor_rpm, Lin_Acc_Z_body * GRAVITY_STD_MPS2 / MG_PER_G, imu_data.pit, imu_data.rol);
}

void Compute_Motor(void)
{
	uint8_t airborne = (flight_phase == FLIGHT_PHASE_FLYING || flight_phase == FLIGHT_PHASE_LANDING);
	uint8_t land_contact = Land_ContactStep();

	Z_Compute();

	cnt_loc++;
	if(cnt_loc>=POS_LOOP_DIV)
	{
		cnt_loc=0;
		Pos_ArmEdge();
		/* Tier 1.0 (2026-09-13): only run position/velocity PIDs while armed.
		   While disarmed Clear_Structure zeros SumE every tick, but Compute_Motor
		   runs all PIDs first, so position SumE re-clamped to SumEMax=200 from OF
		   drift within one tick. Gating keeps locx/y/xs/ys.SumE at 0 in disarmed.
		   cnt_loc=0 sits outside the arm gate so the divider keeps running even
		   while disarmed (otherwise cnt_loc grows unbounded and the gate fires
		   every 256th tick on a uint8 wrap). */
		if (DroneStatus.ARM_Status != 0U) {
			Pos_Compute(airborne, land_contact);
		}
		else
		{
			TrajFF_Reset(&s_traj_ff);
		}
	}

	Att_Compute(airborne);

	// Execute MRAC after all PID controllers have computed their nominal outputs (u_nom)
	// MRAC uses the current PID rates, references, and nominal outputs to learn and compute u_ad.
	Controller_CheckSwitch(DroneStatus.ARM_Status == Armed);
	mrac_in_armed = (DroneStatus.ARM_Status == Armed) ? 1U : 0U;
	mrac_in_phase = (uint8_t)flight_phase;
	MRAC_Control(&Ctrler);

	Mix_Compute();

	ThrustEst_Step();
}

/* ==== Update_Des: setpoints, one case per loop level ============================================== */
#define TWC_ARRIVE_M           0.15f    /* TWC_arrived: 3-D distance to the TWC target below this */
#define FLYUP_TARGET_Z_M       0.5f     /* SBUS ch7 fly-up height */
#define Z_DES_SLEW_M           0.005f   /* max Z setpoint step per call toward TWC.target_z */
#define LAND_SINK_BIAS_STEP    0.001f   /* m/s added per tick while stuck in ground effect */
#define LAND_SINK_SLOW_VZ      0.10f    /* m/s, |Z rate| below this counts as stuck */
#define VXY_DES_MAX            120.0f   /* cm/s, clamp on the position-loop velocity setpoints */
#define YAWRATE_DES_MAX        60.0f    /* deg/s, clamp on the heading-hold rate setpoint */
#define OFHOLD_ON_MIN          1000     /* ch6 (OFHOLD_CH) above this = OF position hold on */

/* Body-frame lean commands (cm/s^2) of the last pitch/roll setpoint update; telemetry only. */
float des_pitch = 0;
float	des_roll = 0;

/* Z position setpoint: ch8/ch7 triggers, LANDING ramp, LANDED/GROUND_IDLE pins, stick capture,
 * TWC slew. */
static void Des_Height(void)
{
	/* Init to 1 (not 0) so the existing capture-on-stick-released state machine
	 * fires on the very first iteration at boot when sticks are centered.
	 * Before this (2026-09-13), is_last_*_valid was 0 at boot, so the condition
	 * `is_last_*_valid && (!RCInput_IsActive(...))` was false on every tick
	 * until the stick was actually moved, leaving Des stuck at whatever the
	 * BSS RAM happened to contain — typically a previous flight's captured FB.
	 * That stale Des was the root cause of the OF-hold cold-boot saturation
	 * cascade / wall-crash. Keeping the 1 means: first tick captures Des=FB,
	 * and the rest of the state machine behaves exactly as before.
	 * (Same rule for the yaw, pitch and roll flags in Des_Loc and Des_Yaw.) */
	static unsigned char is_last_thr_valid = 1U;

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
		TWC.target_z = FLYUP_TARGET_Z_M;
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
		Ctrler.Z_posPID.Des -= (Ctrler.Z_posPID.FB < LAND_SLOW_ALT) ?
		                       LAND_DES_STEP_SLOW : LAND_DES_STEP;
		if (Ctrler.Z_posPID.Des < 0.0f) Ctrler.Z_posPID.Des = 0.0f;
		return;
	}
	/* LANDED: hold setpoint at zero while disarm completes this tick. */
	if (flight_phase == FLIGHT_PHASE_LANDED)
	{
		TWC.execute = 0U;
		Ctrler.Z_posPID.Des = 0.0f;
		return;
	}

	/* GROUND_IDLE hold: pin Z setpoint until pilot pushes THR above 20%.
	 * Also pin when idle not yet enabled (motors at zero, no takeoff possible). */
	if (flight_phase == FLIGHT_PHASE_GROUND_IDLE &&
	    (!g_motor_idle_enabled || (!TWC.execute && RCInput_Get(RC_AXIS_THR) < THR_IDLE_MAX)))
	{
		Ctrler.Z_posPID.Des = Ctrler.Z_posPID.FB;
		return;
	}

	/* FLY mode (normal) */
	if (is_last_thr_valid && (!RCInput_IsActive(RC_AXIS_THR)))
		Ctrler.Z_posPID.Des = Ctrler.Z_posPID.FB;

	is_last_thr_valid = RCInput_IsActive(RC_AXIS_THR);

	if (TWC.execute == 1)
	{
		/* Rate-limit the Z setpoint so it cannot jump to a far target: Z_DES_SLEW_M per call.
		 * This runs every tick (Z_Compute), so 0.005 m x 200 Hz = 1.0 m/s; the original
		 * comment said 0.5 m/s at ~100 Hz. */
		float z_err = TWC.target_z - Ctrler.Z_posPID.Des;
		if      (z_err >  Z_DES_SLEW_M) Ctrler.Z_posPID.Des += Z_DES_SLEW_M;
		else if (z_err < -Z_DES_SLEW_M) Ctrler.Z_posPID.Des -= Z_DES_SLEW_M;
		else                            Ctrler.Z_posPID.Des  = TWC.target_z;
	}
}

/* Climb-rate setpoint: Z position loop output, the LANDING sink bias, or the throttle stick. */
static void Des_VHeight(void)
{
	/* Landing: THR stick must not override PID cascade — rate setpoint
	 * comes exclusively from Z_posPID.U throughout the descent. */
	if (flight_phase == FLIGHT_PHASE_LANDING || flight_phase == FLIGHT_PHASE_LANDED)
	{
		Ctrler.Z_ratePID.Des = Ctrler.Z_posPID.U;
		/* Progressive sink bias: once setpoint has reached the floor, ramp up
		 * commanded sink rate while the drone is slow (stuck in ground effect).
		 * 0.001 m/s per tick at 200 Hz → reaches 0.15 m/s in 0.75 s, max 0.40 m/s.
		 * Adapts to any battery voltage — no altitude threshold needed. */
		if (Ctrler.Z_posPID.Des <= DES_FLOOR_M)
		{
			if (fabsf(Ctrler.Z_ratePID.FB) < LAND_SINK_SLOW_VZ)
				s_land_sink_bias += LAND_SINK_BIAS_STEP;
			if (s_land_sink_bias > LAND_SINK_BIAS_MAX) s_land_sink_bias = LAND_SINK_BIAS_MAX;
			Ctrler.Z_ratePID.Des -= s_land_sink_bias;
		}
	}
	else if (flight_phase == FLIGHT_PHASE_GROUND_IDLE &&
	         (!g_motor_idle_enabled || (!TWC.execute && RCInput_Get(RC_AXIS_THR) < THR_IDLE_MAX)))
		Ctrler.Z_ratePID.Des = 0.0f;
	else if(RCInput_IsActive(RC_AXIS_THR))
		Ctrler.Z_ratePID.Des = RCInput_Get(RC_AXIS_THR) * gs_max_vertical_speed_mps ;
	else
		Ctrler.Z_ratePID.Des = Ctrler.Z_posPID.U;
}

/* xy position setpoints (cm): capture FB on stick release, TWC target while executing. */
static void Des_Loc(void)
{
	static unsigned char is_last_pitch_valid = 1U, is_last_roll_valid = 1U;   /* see Des_Height */

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
	if(TWC.execute == 1){Ctrler.locxPID.Des = TWC.target_x;Ctrler.locyPID.Des = TWC.target_y;}   /* go to the TWC target */
}

/* xy velocity setpoints (cm/s): stick, else the position loop output clamped to VXY_DES_MAX. */
static void Des_VLoc(void)
{
	if(RCInput_IsActive(RC_AXIS_PITCH))
		   Ctrler.locysPID.Des = -RCInput_Get(RC_AXIS_PITCH) * (gs_max_horizontal_speed_mps * CM_PER_M);
	else if (Ctrler.locyPID.U>VXY_DES_MAX)
			Ctrler.locysPID.Des = VXY_DES_MAX;
	else if (Ctrler.locyPID.U< -VXY_DES_MAX)
			Ctrler.locysPID.Des = -VXY_DES_MAX;
	else
		Ctrler.locysPID.Des = 	Ctrler.locyPID.U;   /* cascade: position loop output */

	if(RCInput_IsActive(RC_AXIS_ROLL))
		Ctrler.locxsPID.Des = -RCInput_Get(RC_AXIS_ROLL) * (gs_max_horizontal_speed_mps * CM_PER_M);
	else if(Ctrler.locxPID.U>VXY_DES_MAX)
		Ctrler.locxsPID.Des = VXY_DES_MAX;
	else if(Ctrler.locxPID.U< -VXY_DES_MAX)
		Ctrler.locxsPID.Des = -VXY_DES_MAX;
	else
		Ctrler.locxsPID.Des = 	Ctrler.locxPID.U;   /* cascade: position loop output */
}

/* Pitch/roll angle setpoints (deg): OF hold (velocity loop outputs rotated to body) or angle mode
 * (sticks), then accel_to_lean_angles and the attitude trim. */
static void Des_Att(void)
{
	uint8_t flying = (flight_phase == FLIGHT_PHASE_FLYING);
	/* OF position-hold enable switch on ch6 (OFHOLD_CH = sbus_channel[5]).
	 * HIGH (>1000, ~1694) = OF hold ON; LOW (~306) or signal-lost = ANGLE MODE.
	 * Angle mode bypasses ALL optical-flow loops (position AND velocity): the
	 * sticks command a body-frame lean angle directly and centered sticks = level
	 * (held by the IMU angle loop), so a bad OF velocity/position estimate can no
	 * longer drive tilt - this is the loop that ran the drone away on takeoff.
	 * Default at boot / on failsafe is angle mode: the drone never lifts off into
	 * OF hold unless the pilot deliberately flips ch6 high while already stable. */
	u8 of_hold_on = (sbus_lost == 0U) && (OFHOLD_CH > OFHOLD_ON_MIN);
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
		des_pitch = -RCInput_Get(RC_AXIS_PITCH) * tanf(gs_max_pitch_deg*DEG2RAD) * (GRAVITY_MSS*CM_PER_M);
		des_roll  = -RCInput_Get(RC_AXIS_ROLL)  * tanf(gs_max_roll_deg *DEG2RAD) * (GRAVITY_MSS*CM_PER_M);
	}

	accel_to_lean_angles( des_pitch,-des_roll,
	  &Ctrler.pitchPID.Des,&Ctrler.rollPID.Des);
	Ctrler.pitchPID.Des = AttTrim_Apply(Ctrler.pitchPID.Des, g_att_trim_pitch_deg, gs_max_pitch_deg, flying);
	Ctrler.rollPID.Des  = AttTrim_Apply(Ctrler.rollPID.Des,  g_att_trim_roll_deg,  gs_max_roll_deg,  flying);
}

/* Heading setpoint (deg): capture FB on yaw-stick release, TWC.set_yaw while executing. */
static void Des_Yaw(void)
{
	static unsigned char is_last_yaw_valid = 1U;   /* see Des_Height */

	if(is_last_yaw_valid && (!RCInput_IsActive(RC_AXIS_YAW)) )
	Ctrler.yawPID.Des = Ctrler.yawPID.FB;
	is_last_yaw_valid = RCInput_IsActive(RC_AXIS_YAW);

	if(TWC.execute == 1){Ctrler.yawPID.Des = TWC.set_yaw; }   /* go to the TWC heading */
}

/* Rate setpoints (deg/s): angle loop outputs; yaw from the stick or the clamped heading loop. */
static void Des_Gyro(void)
{
	Ctrler.gyroyPID.Des = Ctrler.pitchPID.U ;
	Ctrler.gyroxPID.Des = Ctrler.rollPID.U ;
	if(RCInput_IsActive(RC_AXIS_YAW))
		Ctrler.gyrozPID.Des = RCInput_Get(RC_AXIS_YAW) * Stick_to_MAX_GyroZ ;
	else
		if(Ctrler.yawPID.U>YAWRATE_DES_MAX)
			Ctrler.gyrozPID.Des  = YAWRATE_DES_MAX;
		else if(Ctrler.yawPID.U< -YAWRATE_DES_MAX)
			Ctrler.gyrozPID.Des  = -YAWRATE_DES_MAX;
		else
			Ctrler.gyrozPID.Des = Ctrler.yawPID.U ;
}

/* Update one level's setpoints (which_level = case_Update_*_Des, StabilizerTask.h). Every call first
 * refreshes TWC.world_* / real_yaw and TWC_arrived. */
void Update_Des(unsigned char which_level)
{
	TWC.world_x = Ctrler.locxPID.FB;
	TWC.world_y = Ctrler.locyPID.FB;
	TWC.world_z = Ctrler.Z_posPID.FB;
	TWC.real_yaw = Ctrler.yawPID.FB;

	if (TWC.execute == 1) {
		float dx = (Ctrler.locxPID.FB - TWC.target_x) * M_PER_CM; /* cm → m */
		float dy = (Ctrler.locyPID.FB - TWC.target_y) * M_PER_CM; /* cm → m */
		float dz = Ctrler.Z_posPID.FB - TWC.target_z;           /* already m */
		float dist = sqrtf(dx * dx + dy * dy + dz * dz);
		TWC_arrived = (dist < TWC_ARRIVE_M) ? 1U : 0U;
	} else {
		TWC_arrived = 0U;
	}

	switch(which_level)
	{
		case case_Update_height_Des: Des_Height();  break;
		case case_Update_v_h_Des:    Des_VHeight(); break;
		case case_Update_loc_Des:    Des_Loc();     break;
		case case_Update_v_loc_Des:  Des_VLoc();    break;
		case case_Update_pitrol_Des: Des_Att();     break;
		case case_Update_yaw_Des:    Des_Yaw();     break;
		case case_Update_gyro_Des:   Des_Gyro();    break;
		default:                                    break;
	}
}

/* ==== Helpers ===================================================================================== */
/* Clamp amt to [low, high]. */
float Constrain_Float(float amt, float low, float high)
{
  return ((amt)<(low)?(low):((amt)>(high)?(high):(amt)));
}

/* Rational approximation of atan(v), rad. */
float fast_atan(float v)
{
    float v2 = v*v;
    return (v*(1.6867629106f+v2*0.4378497304f)/(1.6867633134f+v2));
}

/* Horizontal accel commands (cm/s^2, body frame) -> pitch/roll angle setpoints (deg), clamped to
 * gs_max_pitch/roll_deg. */
void accel_to_lean_angles(float acc_tar_forward,float acc_tar_right,float *tar_pitch,float *tar_roll)
{
  float lim_p = gs_max_pitch_deg;
  float lim_r = gs_max_roll_deg;

	float my_Cos_Roll;
	float my_Cos_Pitch;
	my_Cos_Roll = cos(imu_data.rol*DEG2RAD);
	my_Cos_Pitch = cos(imu_data.pit*DEG2RAD);

  *tar_pitch=Constrain_Float(
									fast_atan(    acc_tar_forward    *my_Cos_Roll   /(GRAVITY_MSS*100)    )*RAD2DEG,
														-lim_p,lim_p);//pitch
  *tar_roll = Constrain_Float(
									fast_atan(acc_tar_right * my_Cos_Pitch /(GRAVITY_MSS*100))*RAD2DEG,
														-lim_r,lim_r);//roll
}

/* ==== Battery voltage (called at 1 Hz from SystemMonitor_Task, USER/main.c:252) ==================== */
#define VBAT_CAL_GAIN          7.0663f  /* 2-pt cal 2026-06-24: (2.21289 V, 16.53) (1.98081 V, 14.89) */
#define VBAT_CAL_OFFSET        0.8930f
#define VBAT_LOW_BEEP_V        15.0f    /* beep below this pack voltage */

float voltage;         /* ADC pin voltage, V (2.67-2.99 on a full pack) */
float real_voltage;    /* pack voltage, V */
uint16_t adc_value ;
void Get_Voltage(void)
{
	adc_value = ADC_Read();
	voltage = Voltage_Calculation(adc_value);
	real_voltage = VBAT_CAL_GAIN*voltage + VBAT_CAL_OFFSET;
	if(real_voltage<VBAT_LOW_BEEP_V)
	{
		SetBeep(1);   /* low-battery beep */

	}
}
