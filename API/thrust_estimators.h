#ifndef THRUST_ESTIMATORS_H
#define THRUST_ESTIMATORS_H

#include "stm32f4xx.h"

/**
 * @module  thrust_estimators.h
 * @subsystem  API
 * @depends  rpm.h (RPM_Get), imu_update.h (Lin_Acc_Z_body, Gravity_Body_Z), global_declare.h (Pitch, Roll)
 * @owns  Three thrust estimators: empirical (PWM->thrust bench LUT), blade-element
 *        (RPM->thrust via k_T·w²), IMU-derived (vertical accel projection).
 *        Plus derived telemetry: sum_w2 (total ω²), mass_hat (est. mass), cw_share (CW pair share).
 * @purpose  Shadow-mode logging for motor dynamics validation (ADR-0009 extension).
 *           Captures transient PWM→RPM lag, compares bench curves vs measured RPM.
 *           Telemetry-only — no control-loop feedback.
 */

typedef struct {
    float empirical[4];      /* N, per motor (PWM-based, bench LUT) */
    float blade_element[4];  /* N, per motor (RPM-based, k_T·w²) */
    float imu_total;         /* N, total thrust along body z = m (a_z + g cos_tilt); 0 on non-finite input */
    float sum_w2;            /* rad²/s², sum of all four motor ω², LPF 1 s */
    float mass_hat;          /* kg, = k_T * sum_w2 / (a_z + g cos_tilt), LPF 1 s */
    float cw_share;          /* ratio [0..1], CW pair (ch0+ch1) ω² share, LPF 1 s */
} ThrustEstimators_t;

void ThrustEst_Init(void);
void ThrustEst_Update(const float pwm[4], const uint16_t rpm[4], 
                        float acc_z, float pitch_deg, float roll_deg);

extern ThrustEstimators_t g_thrust_est;

#endif