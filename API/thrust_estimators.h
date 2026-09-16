#ifndef THRUST_ESTIMATORS_H
#define THRUST_ESTIMATORS_H

#include "stm32f4xx.h"

/**
 * @module  thrust_estimators.h
 * @subsystem  API
 * @depends  rpm.h (RPM_Get), imu_update.h (imu.acc.z), global_declare.h (Pitch, Roll)
 * @owns  Three thrust estimators: empirical (PWM→thrust bench LUT), blade-element
 *        (RPM→thrust via k_T·ω²), IMU-derived (vertical accel projection).
 * @purpose  Shadow-mode logging for motor dynamics validation (ADR-0009 extension).
 *           Captures transient PWM→RPM lag, compares bench curves vs measured RPM.
 *           Telemetry-only — no control-loop feedback.
 */

typedef struct {
    float empirical[4];      /* N, per motor (PWM-based, bench LUT) */
    float blade_element[4];  /* N, per motor (RPM-based, k_T·ω²) */
    float imu_total;         /* N, total vertical thrust (accel projection) */
} ThrustEstimators_t;

void ThrustEst_Init(void);
void ThrustEst_Update(const float pwm[4], const uint16_t rpm[4], 
                       float acc_z, float pitch_deg, float roll_deg);

extern ThrustEstimators_t g_thrust_est;

#endif
