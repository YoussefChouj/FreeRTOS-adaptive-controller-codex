#ifndef __IMU_UPDATE__
#define __IMU_UPDATE__

#include "global_declare.h"
#include "bmi088_driver.h"


extern	_imu_st imu_data	; 

void IMU_Update_Mahony(_imu_st *imu,float dt);

/* Estimator warmup / convergence (A1-A2). */
uint8_t IMU_EstimatorReady(void);      /* 1 = converged or timeout, ready to arm */
extern float g_imu_settle_metric;      /* Keil-watchable innovation-LPF for tuning */

/* Gravity-removed body-frame linear acceleration (mg) — computed each update from
 * the fresh Mahony gravity direction. ADR-0011 Phase 3 reads these to drive the
 * in-flight accel-bias trim. */
extern float Lin_Acc_X_body;
extern float Lin_Acc_Y_body;
extern float Lin_Acc_Z_body;

/* Body-frame gravity unit vector (R^T * [0,0,1]). Magnitude is 1.0 at 1 G, so use
 * directly (do NOT multiply by 1000). ADR-0011 Phase 3 reads these to reconstruct
 * the world-frame gravity vector at hover without re-deriving the rotation matrix. */
extern float Gravity_Body_X;
extern float Gravity_Body_Y;
extern float Gravity_Body_Z;

#endif

