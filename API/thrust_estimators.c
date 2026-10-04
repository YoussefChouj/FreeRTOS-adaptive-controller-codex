#include "thrust_estimators.h"
#include <math.h>

/**
 * @module  thrust_estimators.c
 * @subsystem  API
 * @owner   Stabilizer_Task, 200 Hz: ThrustEst_Update once per tick from TASK/StabilizerTask.c (ThrustEst_Step,
 *          after the mixer, before Set_PWM_Motors); ThrustEst_Init at boot.
 * @inputs  motor commands (CCR), RPM_Get per motor, body z accel (m/s^2), pitch/roll (deg).
 * @outputs g_thrust_est (telemetry only).
 * @depends  thrust_estimators.h, math.h (cosf)
 * @owns  Shadow-mode thrust estimation for motor dynamics validation.
 *        Three estimators: empirical (PWM->thrust bench LUT), blade-element
 *        (RPM->thrust k_T·w²), IMU-derived (vertical accel sanity check).
 *        Derived telemetry: sum_w2, mass_hat, cw_share (all LPF 1 s).
 * @caution  Telemetry only — no control-loop feedback.
 *
 * CCR (capture-compare register) unit proof from BSP/pwm.c:
 *
 *   BSP/pwm.c:60  TIM_Prescaler = 42-1          -- 84 MHz / 42 = 2 MHz
 *   BSP/pwm.c:59  TIM_Period    = 10000-1       -- 2 MHz / 10000 = 200 Hz PWM
 *
 * Each timer tick = 1 / 2e6 s = 0.5 us.
 * Motor CCR values are in 0.5 us ticks: 2000 = 1000 us = 1.0 ms pulse.
 * The bench LUT below is stored in ticks (table knots * 2 from us).
 *
 * k_T calibrated from 6 hover flights (f17 logs):
 *   k_T = m*g / sum(omega^2) = 0.9885*9.81 / 1.427e6 = 6.80e-6 N s^2 / rad^2
 * Mass 0.9885 kg is the onboard assumed mass; verify by weighing.
 */

ThrustEstimators_t g_thrust_est = {0};

/* Motor mass assumed for k_T calibration. Must be verified by weighing. */
static const float DRONE_MASS_KG = 0.9885f;  /* 988.5 g assumed, unverified */
static const float GRAVITY_MS2   = 9.81f;

/* Calibrated k_T (N·s²/rad²) from hover data at m = 0.9885 kg.
 * WHY: sum(omega²) across 6 hover flights = 1.427e6 rad²/s².
 *   k_T = m*g / sum_w2 = 0.9885*9.81 / 1.427e6 = 6.80e-6.
 * Per-motor: CW pair (M1/M2) and CCW pair (M3/M4) may differ;
 * single value is a first approximation. */
#define K_T_CALIBRATED  (6.80e-6f)

/* LPF alpha for 1 s time constant at 200 Hz call rate: dt/tau = 0.005/1.0 */
#define THRUST_LPF_ALPHA  (0.005f)

#define RPM_TO_RAD_S(rpm)   ((rpm) * 2.0f * 3.14159265359f / 60.0f)
#define DEG_TO_RAD          0.017453292519943295f
#define F_Z_MIN             1.0f      /* m/s^2 body-z specific force, mass_hat updates only above this */
#define SUM_W2_TURNING      100.0f    /* (rad/s)^2, cw_share updates only while the motors turn */

/* CW pair channels: ch0+ch1 are CW (M1/M2), ch2+ch3 are CCW (M3/M4).
 * Verified by yaw-trim regression across 4 flights (ch1 positive, ch0/ch2 negative).
 * Per-motor pairing (corner map) is unresolved — use channels directly. */
#define CW_CH0  (0U)
#define CW_CH1  (1U)
#define CCW_CH0 (2U)
#define CCW_CH1 (3U)

/* Bench thrust curves (ADR-0009), N at each PWM knot, one row per measured motor: M1 (CW props) and M4
 * (CCW props); motor_curve[] picks the row per channel. Knots in 0.5 µs ticks (original µs values * 2):
 * 1100 µs -> 2200 ticks, 2000 µs -> 4000 ticks. */
#define THRUST_KNOTS 10
static const float k_pwm_knots[THRUST_KNOTS] =
    {2200.0f, 2400.0f, 2600.0f, 2800.0f, 3000.0f, 3200.0f, 3400.0f, 3600.0f, 3800.0f, 4000.0f};
static const float k_thrust_curve[2][THRUST_KNOTS] = {
/*    2200   2400  2600  2800  3000  3200  3400  3600  3800   4000     CCR    curve */
    {0.0f, 0.5f, 1.2f, 2.1f, 3.3f, 4.8f, 6.5f, 8.4f, 10.5f, 12.8f},  /* 0: M1 (CW props)  */
    {0.0f, 0.6f, 1.3f, 2.2f, 3.4f, 4.9f, 6.6f, 8.5f, 10.6f, 12.9f}   /* 1: M4 (CCW props) */
};

/* Per-channel motor curve selection (row of k_thrust_curve: 0 = M1, 1 = M4). */
static const uint8_t motor_curve[4] = {0U, 0U, 1U, 1U};
/* Per-channel k_T (CW/CCW may differ). Single calibrated value for now. */
static const float k_T_motors[4] = {K_T_CALIBRATED, K_T_CALIBRATED,
                                     K_T_CALIBRATED, K_T_CALIBRATED};

void ThrustEst_Init(void)
{
    uint8_t i;
    for (i = 0; i < 4; i++) {
        g_thrust_est.empirical[i] = 0.0f;
        g_thrust_est.blade_element[i] = 0.0f;
    }
    g_thrust_est.imu_total = 0.0f;
    g_thrust_est.sum_w2 = 0.0f;
    g_thrust_est.mass_hat = 0.0f;
    g_thrust_est.cw_share = 0.0f;
}

/* LPF: y[n] = alpha * x[n] + (1 - alpha) * y[n-1] */
static float lpf_step(float prev, float new_val, float alpha)
{
    return alpha * new_val + (1.0f - alpha) * prev;
}

/* Piecewise-linear interpolation (PWM ticks -> thrust N).
 * Table knots are in 0.5 µs ticks (same units as motor CCR). */
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
    float cos_pitch, cos_roll, cos_tilt, f_z;
    float pitch_rad, roll_rad;
    float sum_w2_raw;
    float cw_w2_raw;

    /* 1. Empirical (PWM -> thrust, bench LUT in ticks).
     * Motor pairing: ch0/ch1 use M1 curve (CW), ch2/ch3 use M4 curve (CCW). */
    for (i = 0; i < 4; i++) {
        g_thrust_est.empirical[i] = interp_pwm_thrust(pwm[i], k_pwm_knots, k_thrust_curve[motor_curve[i]],
                                                      THRUST_KNOTS);
    }

    /* 2. Blade-element (RPM -> thrust, T = k_T · w²) */
    sum_w2_raw = 0.0f;
    cw_w2_raw  = 0.0f;

    for (i = 0; i < 4; i++) {
        omega_rad_s = RPM_TO_RAD_S((float)rpm[i]);
        g_thrust_est.blade_element[i] = k_T_motors[i] * omega_rad_s * omega_rad_s;

        /* Accumulate for derived telemetry */
        omega_rad_s *= omega_rad_s;  /* ω² */
        sum_w2_raw += omega_rad_s;
        if (i == CW_CH0 || i == CW_CH1) {
            cw_w2_raw += omega_rad_s;
        }
    }

    /* 3. IMU-derived total thrust along body z.
     * acc_z is the body-z linear acceleration: specific force minus gravity's body-z share g cos(pitch)cos(roll)
     * (API/imu_update.c:204). The specific force is thrust / m, so T = m (acc_z + g cos_tilt). WP-34: the old
     * m (acc_z / cos_tilt + g) equals T / cos_tilt (+1.5 % at 10 deg tilt, +8 % at pitch 10 roll 20). */
    pitch_rad = pitch_deg * DEG_TO_RAD;
    roll_rad  = roll_deg  * 0.017453292519943295f;
    cos_pitch = cosf(pitch_rad);
    cos_roll  = cosf(roll_rad);
    cos_tilt  = cos_pitch * cos_roll;
    f_z       = acc_z + GRAVITY_MS2 * cos_tilt;     /* specific force along body z, m/s^2 */

    g_thrust_est.imu_total = DRONE_MASS_KG * f_z;
    if (!(f_z - f_z == 0.0f)) {
        g_thrust_est.imu_total = 0.0f;  /* Invalid: non-finite acc_z or attitude */
    }

    /* 4. Derived telemetry (LPF 1 s at 200 Hz -> alpha = 0.005). */

    /* sum_w2: total rotational kinetic indicator */
    g_thrust_est.sum_w2 = lpf_step(g_thrust_est.sum_w2, sum_w2_raw, THRUST_LPF_ALPHA);

    /* mass_hat: estimated mass from thrust balance along body z.
     * m_hat = k_T * sum_w2 / f_z, f_z = a_z + g cos_tilt.  When hovering level, m_hat ~ k_T*sum_w2/g.
     * Guard: only update if f_z is positive and non-negligible (false for NaN). WP-34: was / (g + a_z),
     * which reads m cos_tilt in a tilted hover. */
    if (f_z > F_Z_MIN) {
        float mass_raw = K_T_CALIBRATED * sum_w2_raw / f_z;
        g_thrust_est.mass_hat = lpf_step(g_thrust_est.mass_hat, mass_raw, THRUST_LPF_ALPHA);
    }

    /* cw_share: fraction of total omega² carried by the CW pair (ch0+ch1).
     * Deviation from 0.5 indicates thrust asymmetry (CG offset, prop mismatch, yaw trim). */
    if (sum_w2_raw > SUM_W2_TURNING) {  /* Only when motors are actually turning */
        float share_raw = cw_w2_raw / sum_w2_raw;
        g_thrust_est.cw_share = lpf_step(g_thrust_est.cw_share, share_raw, THRUST_LPF_ALPHA);
    }
}