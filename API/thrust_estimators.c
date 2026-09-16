#include "thrust_estimators.h"
#include <math.h>

/**
 * @module  thrust_estimators.c
 * @subsystem  API
 * @depends  thrust_estimators.h, math.h (cosf, fabsf)
 * @owns  Shadow-mode thrust estimation for motor dynamics validation.
 *        Three estimators: empirical (PWM→thrust bench LUT), blade-element
 *        (RPM→thrust k_T·ω²), IMU-derived (vertical accel sanity check).
 * @caution  Placeholder LUT knots and k_T coefficients — populate from real bench data.
 *           Motor pairing: M0→M1 curve, M1→M1 curve, M2→M4 curve, M3→M4 curve.
 */

ThrustEstimators_t g_thrust_est = {0};

static const float DRONE_MASS_KG = 0.9885f;  /* 988.5 g measured */
static const float GRAVITY_MS2 = 9.81f;
static const float DEG_TO_RAD = 0.017453292519943295f;

/* M1 bench curve knots (PWM µs, thrust N) — 10 knots from ADR-0009.
 * PLACEHOLDER: fit from ground_station/logs/bench/*.csv */
static const float m1_pwm[] = {1100.0f, 1200.0f, 1300.0f, 1400.0f, 1500.0f, 
                                1600.0f, 1700.0f, 1800.0f, 1900.0f, 2000.0f};
static const float m1_thrust[] = {0.0f, 0.5f, 1.2f, 2.1f, 3.3f, 
                                   4.8f, 6.5f, 8.4f, 10.5f, 12.8f};

/* M4 bench curve knots (PWM µs, thrust N) — 10 knots from ADR-0009.
 * PLACEHOLDER: fit from ground_station/logs/bench/*.csv */
static const float m4_pwm[] = {1100.0f, 1200.0f, 1300.0f, 1400.0f, 1500.0f, 
                                1600.0f, 1700.0f, 1800.0f, 1900.0f, 2000.0f};
static const float m4_thrust[] = {0.0f, 0.6f, 1.3f, 2.2f, 3.4f, 
                                   4.9f, 6.6f, 8.5f, 10.6f, 12.9f};

/* Blade-element coefficients k_T (N·s²/rad²) — fitted from bench RPM vs thrust.
 * PLACEHOLDER: actual values need bench RPM→thrust fit per motor.
 * Motor pairing: M0/M1 use 1.5e-5, M2/M3 use 1.6e-5 (matches M1/M4 motor types). */
static const float k_T_motors[4] = {1.5e-5f, 1.5e-5f, 1.6e-5f, 1.6e-5f};

void ThrustEst_Init(void)
{
    uint8_t i;
    for (i = 0; i < 4; i++) {
        g_thrust_est.empirical[i] = 0.0f;
        g_thrust_est.blade_element[i] = 0.0f;
    }
    g_thrust_est.imu_total = 0.0f;
}

/* Piecewise-linear interpolation (PWM → thrust) */
static float interp_pwm_thrust(float pwm, const float* pwm_knots, 
                                 const float* thrust_knots, uint8_t n)
{
    uint8_t i;
    float alpha;
    
    if (pwm <= pwm_knots[0]) return thrust_knots[0];
    if (pwm >= pwm_knots[n-1]) return thrust_knots[n-1];
    
    for (i = 0; i < n-1; i++) {
        if (pwm >= pwm_knots[i] && pwm < pwm_knots[i+1]) {
            alpha = (pwm - pwm_knots[i]) / (pwm_knots[i+1] - pwm_knots[i]);
            return thrust_knots[i] + alpha * (thrust_knots[i+1] - thrust_knots[i]);
        }
    }
    return 0.0f;
}

void ThrustEst_Update(const float pwm[4], const uint16_t rpm[4], 
                       float acc_z, float pitch_deg, float roll_deg)
{
    uint8_t i;
    float omega_rad_s;
    float cos_pitch, cos_roll, cos_tilt;
    float pitch_rad, roll_rad;
    
    /* 1. Empirical (PWM → thrust, bench LUT).
     * Motor pairing: M0/M1 use M1 curve, M2/M3 use M4 curve. */
    g_thrust_est.empirical[0] = interp_pwm_thrust(pwm[0], m1_pwm, m1_thrust, 10);
    g_thrust_est.empirical[1] = interp_pwm_thrust(pwm[1], m1_pwm, m1_thrust, 10);
    g_thrust_est.empirical[2] = interp_pwm_thrust(pwm[2], m4_pwm, m4_thrust, 10);
    g_thrust_est.empirical[3] = interp_pwm_thrust(pwm[3], m4_pwm, m4_thrust, 10);
    
    /* 2. Blade-element (RPM → thrust, T = k_T · ω²) */
    for (i = 0; i < 4; i++) {
        omega_rad_s = (float)rpm[i] * 2.0f * 3.14159265359f / 60.0f;  /* RPM → rad/s */
        g_thrust_est.blade_element[i] = k_T_motors[i] * omega_rad_s * omega_rad_s;
    }
    
    /* 3. IMU-derived (vertical acceleration → total thrust).
     * T_total = m · (a_z / cos(pitch)cos(roll) + g) */
    pitch_rad = pitch_deg * DEG_TO_RAD;
    roll_rad = roll_deg * DEG_TO_RAD;
    cos_pitch = cosf(pitch_rad);
    cos_roll = cosf(roll_rad);
    cos_tilt = cos_pitch * cos_roll;
    
    if (fabsf(cos_tilt) > 0.1f) {  /* Avoid divide-by-zero near 90° tilt */
        g_thrust_est.imu_total = DRONE_MASS_KG * (acc_z / cos_tilt + GRAVITY_MS2);
    } else {
        g_thrust_est.imu_total = 0.0f;  /* Invalid */
    }
}
