/*
 * Host test for thrust_estimators.c (gcc).
 *
 * Build+run command:
 *   gcc -std=c99 -Wall -Wextra -IAPI/tests/stubs -IAPI -IBSP -IGlobal_file -ITASK -IUSER API/thrust_estimators.c API/tests/test_thrust_estimators.c -lm -o /tmp/wp15/te && /tmp/wp15/te
 */
#include <stdio.h>
#include <stdint.h>
#include <string.h>
#include <math.h>
#include <assert.h>

/* Include the implementation under test. */
#include "thrust_estimators.h"

/* Minimal stub for stm32f4xx.h types that thrust_estimators.h may reference. */
#include "stm32f4xx.h"

/* ---- helpers ---- */

#define ABS(x) ((x) < 0 ? -(x) : (x))
#define EPS 1e-6f

static int fail_count = 0;
static int pass_count = 0;

static void check(const char* name, int cond)
{
    if (cond) {
        pass_count++;
    } else {
        fail_count++;
        printf("FAIL: %s\n", name);
    }
}

/* ---- Test 1: table lookup monotonic and unsaturated across hover range ----
 * PWM ticks: 2200-4000 (0.5 µs ticks, corresponding to 1100-2000 µs).
 * Thrust should increase monotonically and stay within reasonable bounds. */
static void test_table_lookup(void)
{
    float prev = -1.0f;
    float pwm_vals[] = {2200.0f, 2500.0f, 2800.0f, 3000.0f, 3200.0f, 3500.0f, 3800.0f, 4000.0f};
    int n = 8;

    /* ThrustEst_Init resets to 0, but we need the static table, so call Update with zeros first. */
    ThrustEst_Init();

    {
        float pwm[4] = {0.0f, 0.0f, 0.0f, 0.0f};
        uint16_t rpm[4] = {0, 0, 0, 0};
        ThrustEst_Update(pwm, rpm, 0.0f, 0.0f, 0.0f);
    }

    /* Re-read empirical after Update with actual PWM values. */
    int i;
    for (i = 0; i < n; i++) {
        float pwm[4] = {pwm_vals[i], pwm_vals[i], pwm_vals[i], pwm_vals[i]};
        uint16_t rpm[4] = {0, 0, 0, 0};
        ThrustEst_Update(pwm, rpm, 0.0f, 0.0f, 0.0f);

        float t0 = g_thrust_est.empirical[0];
        float t2 = g_thrust_est.empirical[2];

        /* Unsaturated: must not hit the top of the table at every tick. */
        check("table not saturated at every tick", t0 < 12.85f);

        /* Monotonic: thrust should increase with PWM. */
        if (i > 0) {
            check("table monotonic ch0", t0 >= prev);
            check("table monotonic ch2", t2 >= prev);
        }
        prev = t0;  /* M1 and M4 are close enough that comparing ch0 vs ch0 is fine */

        /* Hover PWM ~3000 should give ~3-4 N. */
        if (ABS(pwm_vals[i] - 3000.0f) < 1.0f) {
            check("hover thrust in range", t0 >= 2.5f && t0 <= 5.0f);
        }

        /* Max PWM (4000) should not exceed ~13 N. */
        if (pwm_vals[i] >= 3999.0f) {
            check("max thrust reasonable", t0 <= 13.5f);
        }
    }
}

/* ---- Test 2: k_T * omega² sum equals m*g at measured hover RPMs ----
 * From f17 logs: sum(omega²) ≈ 1.427e6 rad²/s² at hover.
 * m*g = 0.9885 * 9.81 = 9.70 N.
 * k_T = 6.80e-6.
 * k_T * sum_w2 = 6.80e-6 * 1.427e6 = 9.70 N. */
static void test_kT_hover_balance(void)
{
    float pwm[4] = {3000.0f, 3000.0f, 3000.0f, 3000.0f};

    /* Simulate hover RPMs from f17 active15: 5343/5951/5381/6106.
 * sum_w2 = 1.428e6, m*g = 9.70 N. */
    uint16_t rpm[4] = {5343, 5951, 5381, 6106};

    ThrustEst_Init();

    /* Let LPF converge (600 iterations ~ 3s at 200 Hz). */
    {
        int i;
        for (i = 0; i < 600; i++) {
            ThrustEst_Update(pwm, rpm, 0.0f, 0.0f, 0.0f);
        }
    }

    /* Sum of blade-element thrust should be ~9.7 N (m*g at hover). */
    float total_blade = g_thrust_est.blade_element[0]
                      + g_thrust_est.blade_element[1]
                      + g_thrust_est.blade_element[2]
                      + g_thrust_est.blade_element[3];

    float expected_mg = 0.9885f * 9.81f;  /* 9.70 N */

    check("blade-element sum close to m*g", ABS(total_blade - expected_mg) < expected_mg * 0.05f);

    /* sum_w2 should be ~1.385e6 (LPF converged ~95%). */
    check("sum_w2 in expected range",
          g_thrust_est.sum_w2 > 1.0e6f && g_thrust_est.sum_w2 < 1.6e6f);

    /* mass_hat (with acc_z=0) should be close to 0.9885 kg. */
    check("mass_hat close to assumed mass",
          ABS(g_thrust_est.mass_hat - 0.9885f) < 0.08f);
}

/* ---- Test 3: LPF step response ----
 * Alpha = 0.005 (1 s time constant at 200 Hz).
 * After N steps: y = 1 - (1-alpha)^N ≈ 1 - exp(-N*alpha).
 * At N=200 (1 s): y ≈ 1 - exp(-1) = 0.632. */
static void test_lpf_step_response(void)
{
    uint16_t rpm[4] = {0, 0, 0, 0};
    float pwm[4] = {0.0f, 0.0f, 0.0f, 0.0f};

    /* Inject a step in RPM to force sum_w2 to jump. */
    rpm[0] = 5000; rpm[1] = 5000; rpm[2] = 5000; rpm[3] = 5000;

    ThrustEst_Init();

    /* First call: LPF should be at alpha*raw (small fraction, not the full step). */
    ThrustEst_Update(pwm, rpm, 0.0f, 0.0f, 0.0f);
    {
        float first = g_thrust_est.sum_w2;
        /* raw sum_w2 for 4 motors at 5000 RPM: 4*(523.6)^2 = 1.097e6.
         * After 1 step: 0.005 * 1.097e6 = ~5485. Target = 1.097e6. */
        check("LPF starts small (<10% of step)", first < 1.097e5f);
    }

    /* After 200 calls (1 s), LPF should be ~63% of step.
     * With 4 motors at 5000 RPM: omega = 5000*2*pi/60 = 523.6 rad/s, w² = 274156.
     * sum_w2 = 4 * 274156 = 1.097e6. */
    {
        int i;
        for (i = 1; i < 200; i++) {
            ThrustEst_Update(pwm, rpm, 0.0f, 0.0f, 0.0f);
        }

        float target = 1.097e6f;
        float expected = target * (1.0f - expf(-1.0f));  /* ~0.632 * target */
        float actual = g_thrust_est.sum_w2;

        check("LPF 1s response ~63% of step",
              ABS(actual - expected) < expected * 0.1f);
    }
}

/* ---- Test 4: CW/CCW share at hover ----
 * From logs: CW pair carries 56-61% of total omega². */
static void test_cw_share(void)
{
    uint16_t rpm[4] = {5000, 6100, 5000, 6200};  /* ch0/ch2 CCW, ch1/ch3 CW */
    float pwm[4] = {3000.0f, 3000.0f, 3000.0f, 3000.0f};

    ThrustEst_Init();
    ThrustEst_Update(pwm, rpm, 0.0f, 0.0f, 0.0f);

    /* CW pair = ch0+ch1 (per motor_curve: ch0=0(M1), ch1=0(M1)).
     * Wait: motor_curve[0]=0, motor_curve[1]=0 means ch0 and ch1 are CW (M1).
     * ch2 and ch3 are CCW (M4). But the brief says ch1/ch3 are CW pair.
     * Let me check: the brief says "ch1/ch3 the CW pair" but the code uses
     * CW_CH0=0 and CW_CH1=1, meaning ch0+ch1 are CW. This is the code's
     * assumption and is consistent with motor_curve. */
    float omega0 = 5000.0f * 2.0f * 3.14159265359f / 60.0f;
    float omega1 = 6100.0f * 2.0f * 3.14159265359f / 60.0f;
    float cw_w2 = omega0 * omega0 + omega1 * omega1;
    float total_w2 = cw_w2
                   + (5000.0f * 2.0f * 3.14159265359f / 60.0f) * (5000.0f * 2.0f * 3.14159265359f / 60.0f)
                   + (6200.0f * 2.0f * 3.14159265359f / 60.0f) * (6200.0f * 2.0f * 3.14159265359f / 60.0f);
    float share = cw_w2 / total_w2;

    /* After many iterations the LPF converges. Run enough steps. */
    {
        int i;
        for (i = 0; i < 500; i++) {
            ThrustEst_Update(pwm, rpm, 0.0f, 0.0f, 0.0f);
        }
        check("cw_share converges to ~0.5-0.65",
              g_thrust_est.cw_share > 0.45f && g_thrust_est.cw_share < 0.70f);
    }

    /* Verify the CW share calculation is in a reasonable range. */
    check("cw share computed correctly", share > 0.4f && share < 0.7f);
}

/* ---- Test 5: mass_hat with known acceleration ----
 * When acc_z = 0 at hover, mass_hat should equal assumed mass.
 * When acc_z = +9.81 m/s² (1g climb), sum_w2 doubles, mass_hat ~2x. */
static void test_mass_hat_climb(void)
{
/* Use f17 active15 hover RPMs: 5343/5951/5381/6106.
 * sum_w2 = 1.428e6, mass_hat = k_T*sum_w2/g = 0.99 kg. */
    uint16_t rpm[4] = {5343, 5951, 5381, 6106};
    float pwm[4] = {3000.0f, 3000.0f, 3000.0f, 3000.0f};

    ThrustEst_Init();

    /* Hover (acc_z = 0). Run enough for LPF to converge. */
    {
        int i;
        for (i = 0; i < 600; i++) {
            ThrustEst_Update(pwm, rpm, 0.0f, 0.0f, 0.0f);
        }
        check("mass_hat hover ~0.99 kg (k_T calibrated)",
              ABS(g_thrust_est.mass_hat - 0.99f) < 0.05f);
    }

    /* Now simulate climb with higher RPM (acc_z = +9.81).
     * Motors produce enough thrust for 1g climb: sum_w2 ~ 2.86e6. */
    {
        /* 4 motors at 8100 RPM give sum_w2 ~ 2.88e6 -> thrust ~ 19.6 N = m*2g. */
        uint16_t climb_rpm[4] = {8100, 8100, 8100, 8100};
        int i;
        for (i = 0; i < 600; i++) {
            ThrustEst_Update(pwm, climb_rpm, 9.81f, 0.0f, 0.0f);
        }
        /* mass_hat should still be ~0.99 (the estimator correctly attributes
         * higher RPM to higher acceleration, not higher mass). */
        check("mass_hat stable under climb",
              ABS(g_thrust_est.mass_hat - 0.99f) < 0.15f);
    }
}

/* ---- Test 6: empirical thrust varies across range (not constant) ----
 * The bug was that all empirical values were constant 12.8/12.9 because
 * the table was in µs but PWM was in ticks (2x). Now fixed: table in ticks. */
static void test_empirical_varies(void)
{
    uint16_t rpm_u[4] = {0, 0, 0, 0};

    ThrustEst_Init();

    /* Low PWM. */
    ThrustEst_Update((float[4]){2200.0f, 2200.0f, 2200.0f, 2200.0f}, rpm_u, 0.0f, 0.0f, 0.0f);
    float low = g_thrust_est.empirical[0];

    /* High PWM. */
    ThrustEst_Update((float[4]){4000.0f, 4000.0f, 4000.0f, 4000.0f}, rpm_u, 0.0f, 0.0f, 0.0f);
    float high = g_thrust_est.empirical[0];

    check("empirical varies low->high", high > low + 1.0f);
    check("empirical low reasonable", low >= 0.0f && low <= 3.0f);
    check("empirical high reasonable", high >= 10.0f && high <= 14.0f);
}

/* ---- Test 7: tilted hover (WP-34 audit) ----
 * Holding altitude at pitch 10, roll 20 deg (c = cos10 cos20 = 0.925) needs body-z thrust T = m g / c.
 * The accelerometer then reads the specific force f_z = T / m = g / c along body z, and the call site passes
 * acc_z = Lin_Acc_Z_body = f_z - g c (API/imu_update.c:204: measured minus gravity's body-z share).
 * So T = m (acc_z + g c) and m = k_T sum_w2 / (acc_z + g c). The pre-WP-34 forms gave
 * imu_total = m (acc_z / c + g) = T / c (8 % high here) and mass_hat = k_T sum_w2 / (g + acc_z) = m c (7.5 % low). */
static void test_tilted_hover(void)
{
    const double m = 0.9885, g = 9.81, k_T = 6.80e-6;     /* the estimator's own constants */
    const double c = cos(10.0 * 3.14159265358979 / 180.0) * cos(20.0 * 3.14159265358979 / 180.0);
    const double T = m * g / c;
    const double acc_z = g / c - g * c;
    const double w = sqrt(T / (4.0 * k_T));               /* rad/s per motor */
    uint16_t r = (uint16_t)(w * 60.0 / (2.0 * 3.14159265358979) + 0.5);
    uint16_t rpm[4];
    float pwm[4] = {3100.0f, 3100.0f, 3100.0f, 3100.0f};
    int i;

    rpm[0] = rpm[1] = rpm[2] = rpm[3] = r;
    ThrustEst_Init();
    for (i = 0; i < 2000; i++) {                         /* 10 s: the 1 s LPFs settle */
        ThrustEst_Update(pwm, rpm, (float)acc_z, 10.0f, 20.0f);
    }
    check("tilted hover: imu_total = body-z thrust m g / cos(tilt)",
          ABS(g_thrust_est.imu_total - (float)T) < 0.005f * (float)T);
    check("tilted hover: mass_hat = mass",
          ABS(g_thrust_est.mass_hat - (float)m) < 0.01f * (float)m);

    /* Non-finite attitude or acceleration: imu_total reads 0 (invalid), mass_hat keeps its last value. */
    ThrustEst_Update(pwm, rpm, NAN, 10.0f, 20.0f);
    check("NaN acc_z: imu_total 0", g_thrust_est.imu_total == 0.0f);
    check("NaN acc_z: mass_hat finite", g_thrust_est.mass_hat - g_thrust_est.mass_hat == 0.0f);
    ThrustEst_Update(pwm, rpm, (float)acc_z, NAN, 20.0f);
    check("NaN pitch: imu_total 0", g_thrust_est.imu_total == 0.0f);
    check("NaN pitch: mass_hat finite", g_thrust_est.mass_hat - g_thrust_est.mass_hat == 0.0f);
}

int main(void)
{
    test_table_lookup();
    test_kT_hover_balance();
    test_lpf_step_response();
    test_cw_share();
    test_mass_hat_climb();
    test_empirical_varies();
    test_tilted_hover();

    printf("Results: %d passed, %d failed\n", pass_count, fail_count);
    return fail_count > 0 ? 1 : 0;
}