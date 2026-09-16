#include "imu_update.h"

/**
 * @module  imu_update.c
 * @subsystem  sensors
 * @depends  imu_update.h
 * @owns  Mahony attitude update and quaternion-to-Euler conversion
 * @caution  gyroscope units and task-provided dt must remain consistent to avoid estimator drift
 */

_imu_st imu_data =  {1,0,0,0,0,0,
					{0,0,0},
					{0,0,0},
					{0,0,0},
					{0,0,0},
					{0,0,0},
					{0,0,0},
					 0,0,0};

float Kp = 0.5f;/**/
float Ki = 0.001f;/**/
/**/
float exInt = 0.0f;
float eyInt = 0.0f;
float ezInt = 0.0f;

/* --- A1: fast-converge boot window ---
 * For the first IMU_FAST_WINDOW seconds after boot the Mahony gains are boosted
 * so the attitude estimate snaps onto gravity in ~10-15 s instead of the ~1-2
 * min the nominal Ki=0.001 needs to walk out the cold->warm gyro-bias shift.
 * The boost decays linearly to the nominal Kp/Ki across the window. */
#define IMU_KP_BOOST     4.0f     /* boosted proportional gain at t=0          */
#define IMU_KI_BOOST     0.02f    /* boosted integral gain at t=0              */
#define IMU_FAST_WINDOW  10.0f    /* [s] boost-decay window                    */

/* --- A2: estimator-settled detector ---
 * Convergence proxy = low-passed innovation energy |e|^2 (cross product of the
 * measured vs estimated gravity direction). Large while a gyro-bias mismatch
 * persists, small once the integral has cancelled it. "settled" latches once
 * the LPF drops below the threshold after the boost window; a hard timeout
 * guarantees the arming gate can never lock the pilot out. */
#define IMU_SETTLE_E2     0.0009f  /* |e|^2 threshold (~0.03 rad innovation)    */
#define IMU_SETTLE_ALPHA  0.002f   /* LPF coeff at 1 kHz (~0.5 s time constant) */
#define IMU_READY_TIMEOUT 30.0f    /* [s] hard fallback: ready regardless       */

static float   s_boot_t    = 0.0f;
static float   s_innov_lpf = 1.0f;   /* start high => not settled at boot       */
static uint8_t s_settled   = 0U;

float   g_imu_settle_metric = 1.0f;  /* Keil-watchable LPF value for tuning     */
uint8_t g_estimator_ready   = 0U;    /* A3: published to telemetry / arm gate   */
/**/
static float q0 = 1.0f;	
static float q1 = 0.0f;
static float q2 = 0.0f;
static float q3 = 0.0f;


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

void IMU_Update_Mahony(_imu_st *imu,float dt)
{
	float normalise;
	float nor_acc[VEC_XYZ] = {0};
	float ex, ey, ez;//
	float q0s, q1s, q2s, q3s;/*  */
	static float R11,R21;/* (1,1),(2,1) */
	static float vecxZ, vecyZ, veczZ;/* z(0,0,1)' */
	// CONSTRAINT: dt is supplied by the 1 kHz task in USER/main.c and must match actual loop period.
	// WHY: PI correction and quaternion propagation are both dt-scaled.
	float half_T = 0.5f * dt;

	float q0Last = q0;
	float q1Last = q1;
	float q2Last = q2;
	float q3Last = q3;
	float delta_theta[3];/* xyz */
	float delta_theta_s;/* xyz */
	float kp_eff, ki_eff, boost_frac;

	/* A1: advance the boot clock and derive the decaying gain boost. */
	s_boot_t += dt;
	if (s_boot_t < IMU_FAST_WINDOW) {
		boost_frac = 1.0f - (s_boot_t / IMU_FAST_WINDOW);   /* 1 -> 0 across window */
	} else {
		boost_frac = 0.0f;
	}
	kp_eff = Kp + (IMU_KP_BOOST - Kp) * boost_frac;
	ki_eff = Ki + (IMU_KI_BOOST - Ki) * boost_frac;

	/* 0 */
	if((Acc_X_Real != 0.0f) || (Acc_Y_Real != 0.0f) || (Acc_Z_Real != 0.0f))
	{
		nor_acc[X] = Acc_X_Real;
		nor_acc[Y] = Acc_Y_Real;
		nor_acc[Z] = Acc_Z_Real;
		
		/*  */
		normalise = invSqrt(nor_acc[X] * nor_acc[X] + nor_acc[Y] * nor_acc[Y] + nor_acc[Z] * nor_acc[Z]);
		nor_acc[X] *= normalise;
		nor_acc[Y] *= normalise;
		nor_acc[Z] *= normalise;

		/* , */
		/* |a x b| = |a|*|b|*sin(theta);|a|=|b|=1,thetasin(theta)theta, */
		ex = (nor_acc[Y] * veczZ - nor_acc[Z] * vecyZ);
		ey = (nor_acc[Z] * vecxZ - nor_acc[X] * veczZ);
		ez = (nor_acc[X] * vecyZ - nor_acc[Y] * vecxZ);
		
		/* , */
		exInt += ki_eff * ex * dt ;
		eyInt += ki_eff * ey * dt ;
		ezInt += ki_eff * ez * dt ;

		/* PI, */
 		Gyro_X_Real += kp_eff * ex + exInt;
 		Gyro_Y_Real += kp_eff * ey + eyInt;
 		Gyro_Z_Real += kp_eff * ez + ezInt;

		/* A2: track low-passed innovation energy and latch "settled" once it
		 * falls below threshold after the boost window has elapsed. */
		{
			float e2 = ex * ex + ey * ey + ez * ez;
			s_innov_lpf += IMU_SETTLE_ALPHA * (e2 - s_innov_lpf);
			g_imu_settle_metric = s_innov_lpf;
			if (!s_settled && (s_boot_t > IMU_FAST_WINDOW) && (s_innov_lpf < IMU_SETTLE_E2)) {
				s_settled = 1U;
			}
		}
	}

	/* A3: publish readiness — settled, or the hard timeout as a lockout guard. */
	g_estimator_ready = (s_settled || (s_boot_t > IMU_READY_TIMEOUT)) ? 1U : 0U;

	/* TkTk+1, */
	delta_theta[0] = Gyro_X_Real*half_T;
	delta_theta[1] = Gyro_Y_Real*half_T;
	delta_theta[2] = Gyro_Z_Real*half_T;
	delta_theta_s = delta_theta[0]*delta_theta[0] + delta_theta[1]*delta_theta[1] + delta_theta[2]*delta_theta[2];
	/* , */
	/* Q(Tk+1)=(I+0.5*delta_theta)Q(Tk) */
// 	q0 += -q1Last * delta_theta[0] - q2Last * delta_theta[1] - q3Last * delta_theta[2];
// 	q1 +=  q0Last * delta_theta[0] + q2Last * delta_theta[2] - q3Last * delta_theta[1];
// 	q2 +=  q0Last * delta_theta[1] - q1Last * delta_theta[2] + q3Last * delta_theta[0];
// 	q3 +=  q0Last * delta_theta[2] + q1Last * delta_theta[1] - q2Last * delta_theta[0];

	/*  */
	/* Q(Tk+1)=((1-0.125*delta_theta_s)I+0.5*delta_theta)Q(Tk) */	
	q0 = q0Last*(1-delta_theta_s) - q1Last * delta_theta[0] - q2Last * delta_theta[1] - q3Last * delta_theta[2];
	q1 = q1Last*(1-delta_theta_s) + q0Last * delta_theta[0] + q2Last * delta_theta[2] - q3Last * delta_theta[1];
	q2 = q2Last*(1-delta_theta_s) + q0Last * delta_theta[1] - q1Last * delta_theta[2] + q3Last * delta_theta[0];
	q3 = q3Last*(1-delta_theta_s) + q0Last * delta_theta[2] + q1Last * delta_theta[1] - q2Last * delta_theta[0];
	
	/*  */
	normalise = invSqrt(q0 * q0 + q1 * q1 + q2 * q2 + q3 * q3);
	q0 *= normalise;
	q1 *= normalise;
	q2 *= normalise;
	q3 *= normalise;
	/*  */
	q0s = q0 * q0;
	q1s = q1 * q1;
	q2s = q2 * q2;
	q3s = q3 * q3;
	
	R11 = q0s + q1s - q2s - q3s;/* (1,1) */
	R21 = 2 * (q1 * q2 + q0 * q3);/* (2,1) */

	/* z(0,0,1) */
	vecxZ = 2 * (q1 * q3 - q0 * q2);/* (3,1) */
	vecyZ = 2 * (q0 * q1 + q2 * q3);/* (3,2) */
	veczZ = q0s - q1s - q2s + q3s;	/* (3,3) */
	
	if (vecxZ>1) vecxZ=1;
	if (vecxZ<-1) vecxZ=-1;
	
	/* roll pitch yaw  */
	imu->pit = -asinf(vecxZ) *RAD2DEG;
	imu->rol = atan2f(vecyZ, veczZ) * RAD2DEG;
	imu->yaw = atan2f(R21, R11) * RAD2DEG;

	/* Remove gravity in body frame: linear accel = measured - G*gravity_direction (mg).
	 * (vecxZ,vecyZ,veczZ) is the body-frame gravity unit vector; static & level => lin ~ 0. */
	Lin_Acc_X_body = Acc_X_Real - 1000.0f * vecxZ;
	Lin_Acc_Y_body = Acc_Y_Real - 1000.0f * vecyZ;
	Lin_Acc_Z_body = Acc_Z_Real - 1000.0f * veczZ;
	/* Exported for the calibrator (Phase 3 needs to reconstruct world-gravity in body frame
	 * without re-deriving the rotation matrix). 1 G = 1.0 here. */
	Gravity_Body_X = vecxZ;
	Gravity_Body_Y = vecyZ;
	Gravity_Body_Z = veczZ;
}

/* Pre-arm gate: 1 once the attitude estimator has converged (or the timeout
 * fallback fired), 0 while still warming up. Used by the flight FSM to block
 * arming and by StabilizerTask to hold the OF world origin at zero. */
uint8_t IMU_EstimatorReady(void)
{
	return g_estimator_ready;
}

	 
