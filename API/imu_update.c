/**
 * @module     imu_update.c
 * @subsystem  sensors
 * @owner      IMU_DataDeal_Task (USER/main.c), 1 kHz: IMU_Update_Mahony(&imu_data, 1e-3f). IMU_EstimatorReady is
 *             read by flight_fsm.c (arm gate), prearm.c and systemmonitor_task.c.
 * @purpose    Mahony attitude filter: accelerometer-vs-estimated gravity error drives a PI correction of the gyro
 *             rates, the corrected rates propagate the quaternion, the quaternion gives roll/pitch/yaw. A boot window
 *             boosts the gains (fast convergence) and an innovation low-pass latches "estimator ready".
 * @inputs     Acc_*_Real (mg), Gyro_*_Real (rad/s, corrected in place), sensor.sensor_ok, dt (s, must match the
 *             task period: the PI integral and the quaternion step are both dt-scaled).
 * @outputs    imu->rol/pit/yaw (deg), g_estimator_ready, g_imu_settle_metric, Lin_Acc_*_body (mg),
 *             Gravity_Body_* (1 G = 1.0).
 */
#include "imu_update.h"

_imu_st imu_data =  {1,0,0,0,0,0,
					{0,0,0},
					{0,0,0},
					{0,0,0},
					{0,0,0},
					{0,0,0},
					{0,0,0},
					 0,0,0};

/* Nominal Mahony gains and the PI integral state; global so they stay watchable by name. */
float Kp = 0.5f;
float Ki = 0.001f;
float exInt = 0.0f;
float eyInt = 0.0f;
float ezInt = 0.0f;

/* ------------------------------------------------------------------
 * Private constants
 * ------------------------------------------------------------------ */

#define IMU_MG_PER_G  1000.0f   /* accelerometer units per 1 G */

/* Estimator tunables.
   @kp_boost         -    [0.5, 10]       boosted proportional gain at boot (t = 0)
   @ki_boost         -    [0.001, 0.1]    boosted integral gain at boot (t = 0)
   @fast_window      s    [1, 30]         boost decays linearly to the nominal Kp/Ki across this window
   @settle_e2        -    [1e-5, 0.01]    innovation |e|^2 below which the estimator counts as settled
   @settle_alpha     -    [0.0001, 0.05]  innovation low-pass coefficient per 1 kHz call
   @ready_timeout    s    [10, 120]       estimator reported ready after this regardless (arm-gate lockout guard)
   A1 boost: the attitude snaps onto gravity in ~10-15 s instead of the ~1-2 min the nominal Ki needs to walk out the
   cold->warm gyro-bias shift. A2 settle: |e|^2 (cross product of measured vs estimated gravity) stays large while a
   bias mismatch persists; settle_e2 0.0009 ~ 0.03 rad innovation, settle_alpha 0.002 ~ 0.5 s time constant at 1 kHz.
   "settled" latches once the low-pass drops below settle_e2 after the boost window. */
typedef struct {
    float kp_boost, ki_boost, fast_window, settle_e2, settle_alpha, ready_timeout;
} imu_tune_t;
#define IMU_TUNE_ROW(kp_boost, ki_boost, fast_window, settle_e2, settle_alpha, ready_timeout) \
    { (kp_boost), (ki_boost), (fast_window), (settle_e2), (settle_alpha), (ready_timeout) }
/*                                     kp_boost  ki_boost  fast_window  settle_e2  settle_alpha  ready_timeout */
static const imu_tune_t s_tune = IMU_TUNE_ROW( 4.0f,    0.02f,    10.0f,       0.0009f,   0.002f,       30.0f );

/* ------------------------------------------------------------------
 * Private state
 * ------------------------------------------------------------------ */

static float   s_boot_t    = 0.0f;
static float   s_innov_lpf = 1.0f;   /* start high => not settled at boot       */
static uint8_t s_settled   = 0U;

float   g_imu_settle_metric = 1.0f;  /* Keil-watchable LPF value for tuning     */
uint8_t g_estimator_ready   = 0U;    /* A3: published to telemetry / arm gate   */

static float q0 = 1.0f;
static float q1 = 0.0f;
static float q2 = 0.0f;
static float q3 = 0.0f;

/* Rotation-matrix entries of the last update: R11, R21 give yaw; (vecxZ, vecyZ, veczZ) = R^T * [0,0,1] is the
   estimated body-frame gravity direction, which the next update compares with the accelerometer. */
static float R11, R21;
static float vecxZ, vecyZ, veczZ;


float invSqrt(float x)
{
	float halfx = 0.5f * x;
	float y = x;
	long i = *(long*)&y;
	i = 0x5f3759df - (i>>1);
	y = *(float*)&i;
	y = y * (1.5f - (halfx * y * y));
	return y;
}
/* Gravity-removed body-frame linear acceleration (mg), computed from the fresh Mahony
 * gravity direction each update. Streamed in the 0x05 OF-calibration frame for the
 * IMU+OF fusion filter (prereq #1, docs/tracking_baseline_and_drift.md). 1 G = 1000 mg. */
float Lin_Acc_X_body = 0.0f, Lin_Acc_Y_body = 0.0f, Lin_Acc_Z_body = 0.0f;

/* Body-frame gravity unit vector (R^T * [0,0,1]). 1 G = 1.0 (NOT mg).
 * Exported so the CAL_AIRBORNE_HOVER_TRIM LSM (ADR-0011 Phase 3) can reconstruct
 * the world-frame gravity vector at hover from body-frame accel alone. */
float Gravity_Body_X = 0.0f, Gravity_Body_Y = 0.0f, Gravity_Body_Z = 0.0f;

/* ------------------------------------------------------------------
 * Update steps (called in this order by IMU_Update_Mahony)
 * ------------------------------------------------------------------ */

/* A1: advance the boot clock; the gain boost decays linearly from the boosted to the nominal gains. */
static void s_gain_schedule(float dt, float *kp_eff, float *ki_eff)
{
	float boost_frac;

	s_boot_t += dt;
	if (s_boot_t < s_tune.fast_window) {
		boost_frac = 1.0f - (s_boot_t / s_tune.fast_window);   /* 1 -> 0 across window */
	} else {
		boost_frac = 0.0f;
	}
	*kp_eff = Kp + (s_tune.kp_boost - Kp) * boost_frac;
	*ki_eff = Ki + (s_tune.ki_boost - Ki) * boost_frac;
}

/* PI correction of the gyro rates from the gravity error, and the A2 settle detector. Skipped when the
   accelerometer reads exactly zero (no sample yet). */
static void s_accel_correction(float kp_eff, float ki_eff, float dt)
{
	float normalise;
	float nor_acc[VEC_XYZ] = {0};
	float ex, ey, ez;

	if((Acc_X_Real != 0.0f) || (Acc_Y_Real != 0.0f) || (Acc_Z_Real != 0.0f))
	{
		nor_acc[X] = Acc_X_Real;
		nor_acc[Y] = Acc_Y_Real;
		nor_acc[Z] = Acc_Z_Real;

		/* unit measured gravity direction */
		normalise = invSqrt(nor_acc[X] * nor_acc[X] + nor_acc[Y] * nor_acc[Y] + nor_acc[Z] * nor_acc[Z]);
		nor_acc[X] *= normalise;
		nor_acc[Y] *= normalise;
		nor_acc[Z] *= normalise;

		/* error = measured x estimated gravity direction; |a x b| = sin(theta) ~ theta for unit vectors */
		ex = (nor_acc[Y] * veczZ - nor_acc[Z] * vecyZ);
		ey = (nor_acc[Z] * vecxZ - nor_acc[X] * veczZ);
		ez = (nor_acc[X] * vecyZ - nor_acc[Y] * vecxZ);

		/* integral term */
		exInt += ki_eff * ex * dt ;
		eyInt += ki_eff * ey * dt ;
		ezInt += ki_eff * ez * dt ;

		/* PI-corrected gyro rates */
 		Gyro_X_Real += kp_eff * ex + exInt;
 		Gyro_Y_Real += kp_eff * ey + eyInt;
 		Gyro_Z_Real += kp_eff * ez + ezInt;

		/* A2: track low-passed innovation energy and latch "settled" once it
		 * falls below threshold after the boost window has elapsed. */
		{
			float e2 = ex * ex + ey * ey + ez * ez;
			s_innov_lpf += s_tune.settle_alpha * (e2 - s_innov_lpf);
			g_imu_settle_metric = s_innov_lpf;
			if (!s_settled && (s_boot_t > s_tune.fast_window) && (s_innov_lpf < s_tune.settle_e2)) {
				s_settled = 1U;
			}
		}
	}
}

/* Quaternion step from Tk to Tk+1 with the corrected rates, then renormalise.
   Q(Tk+1) = ((1 - delta_theta_s) I + 0.5 * delta_theta) Q(Tk), delta_theta = rate * dt / 2. The exact second-order
   scalar term is (1 - delta_theta_s / 2); the two differ only at third order in |delta_theta|. */
static void s_propagate(float half_T)
{
	float normalise;
	float q0Last = q0;
	float q1Last = q1;
	float q2Last = q2;
	float q3Last = q3;
	float delta_theta[3];   /* half rotation increment, x y z (rad) */
	float delta_theta_s;    /* |delta_theta|^2 */

	delta_theta[0] = Gyro_X_Real*half_T;
	delta_theta[1] = Gyro_Y_Real*half_T;
	delta_theta[2] = Gyro_Z_Real*half_T;
	delta_theta_s = delta_theta[0]*delta_theta[0] + delta_theta[1]*delta_theta[1] + delta_theta[2]*delta_theta[2];

	q0 = q0Last*(1-delta_theta_s) - q1Last * delta_theta[0] - q2Last * delta_theta[1] - q3Last * delta_theta[2];
	q1 = q1Last*(1-delta_theta_s) + q0Last * delta_theta[0] + q2Last * delta_theta[2] - q3Last * delta_theta[1];
	q2 = q2Last*(1-delta_theta_s) + q0Last * delta_theta[1] - q1Last * delta_theta[2] + q3Last * delta_theta[0];
	q3 = q3Last*(1-delta_theta_s) + q0Last * delta_theta[2] + q1Last * delta_theta[1] - q2Last * delta_theta[0];

	normalise = invSqrt(q0 * q0 + q1 * q1 + q2 * q2 + q3 * q3);
	q0 *= normalise;
	q1 *= normalise;
	q2 *= normalise;
	q3 *= normalise;
}

/* Rotation-matrix entries from the quaternion, then the outputs: Euler angles, gravity-removed linear
   acceleration and the body-frame gravity direction. */
static void s_publish(_imu_st *imu)
{
	float q0s, q1s, q2s, q3s;   /* squared quaternion components */

	q0s = q0 * q0;
	q1s = q1 * q1;
	q2s = q2 * q2;
	q3s = q3 * q3;

	R11 = q0s + q1s - q2s - q3s;/* (1,1) */
	R21 = 2 * (q1 * q2 + q0 * q3);/* (2,1) */

	/* body-frame gravity direction: third row of R */
	vecxZ = 2 * (q1 * q3 - q0 * q2);/* (3,1) */
	vecyZ = 2 * (q0 * q1 + q2 * q3);/* (3,2) */
	veczZ = q0s - q1s - q2s + q3s;	/* (3,3) */

	if (vecxZ>1) vecxZ=1;
	if (vecxZ<-1) vecxZ=-1;

	/* roll pitch yaw (deg) */
	imu->pit = -asinf(vecxZ) *RAD2DEG;
	imu->rol = atan2f(vecyZ, veczZ) * RAD2DEG;
	imu->yaw = atan2f(R21, R11) * RAD2DEG;

	/* Remove gravity in body frame: linear accel = measured - G*gravity_direction (mg).
	 * (vecxZ,vecyZ,veczZ) is the body-frame gravity unit vector; static & level => lin ~ 0. */
	Lin_Acc_X_body = Acc_X_Real - IMU_MG_PER_G * vecxZ;
	Lin_Acc_Y_body = Acc_Y_Real - IMU_MG_PER_G * vecyZ;
	Lin_Acc_Z_body = Acc_Z_Real - IMU_MG_PER_G * veczZ;
	/* Exported for the calibrator (Phase 3 needs to reconstruct world-gravity in body frame
	 * without re-deriving the rotation matrix). 1 G = 1.0 here. */
	Gravity_Body_X = vecxZ;
	Gravity_Body_Y = vecyZ;
	Gravity_Body_Z = veczZ;
}

/* ------------------------------------------------------------------
 * Public API
 * ------------------------------------------------------------------ */

void IMU_Update_Mahony(_imu_st *imu,float dt)
{
	float kp_eff, ki_eff;

	s_gain_schedule(dt, &kp_eff, &ki_eff);
	s_accel_correction(kp_eff, ki_eff, dt);

	/* A3: publish readiness — settled, or the hard timeout as a lockout guard. */
	g_estimator_ready = (sensor.sensor_ok && (s_settled || (s_boot_t > s_tune.ready_timeout))) ? 1U : 0U;

	s_propagate(0.5f * dt);
	s_publish(imu);
}

/* Pre-arm gate: 1 once the attitude estimator has converged (or the timeout
 * fallback fired), 0 while still warming up. Used by the flight FSM to block
 * arming and by StabilizerTask to hold the OF world origin at zero. */
uint8_t IMU_EstimatorReady(void)
{
	if (!sensor.sensor_ok) {
		return 0U;
	}
	return g_estimator_ready;
}

