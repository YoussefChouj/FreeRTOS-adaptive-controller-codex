/* Host test of the Mahony filter's gyro handling (yaw overhaul 2026-10-16, API/imu_update.c).
 *
 * IMU_Update_Mahony must not write the shared samples Gyro_*_Real: the rate loops, MRAC and the 9-state EKF read
 * them, and before the overhaul they saw the PI-corrected rate or the raw one depending on which of the two 1 kHz
 * tasks ran last. The filter itself must still use the corrected rate: with a tilted accelerometer and zero gyro
 * the attitude has to converge toward the tilt.
 *
 *   gcc -std=c99 -Wall -Wextra -Itests/firmware_host/stubs_imu tests/firmware_host/test_imu_update.c
 *       API/imu_update.c -lm   (copy imu_update.c out of API/ first, or: python tools/host_tests.py imu_update)
 */
#include <stdio.h>
#include <math.h>
#include "imu_update.h"

_sensor_st sensor = { 1U };
float Gyro_X_Real, Gyro_Y_Real, Gyro_Z_Real;
float Acc_X_Real, Acc_Y_Real, Acc_Z_Real;

static int g_checks, g_fail;

static void check(int ok, const char *what, int step)
{
    g_checks++;
    if (!ok) {
        g_fail++;
        if (g_fail <= 10) printf("FAIL %s (step %d)\n", what, step);
    }
}

int main(void)
{
    const float gx = 0.01f, gy = -0.02f, gz = 0.03f;   /* rad/s, a small constant rate */
    int k;

    /* 1. The samples are read only: the filter corrects its own copy, every update, boost window included. */
    Acc_X_Real = 200.0f;   /* tilted against the estimate, so the PI correction is non-zero */
    Acc_Y_Real = -150.0f;
    Acc_Z_Real = 960.0f;
    for (k = 0; k < 3000; k++) {
        Gyro_X_Real = gx;
        Gyro_Y_Real = gy;
        Gyro_Z_Real = gz;
        IMU_Update_Mahony(&imu_data, 1e-3f);
        check(Gyro_X_Real == gx && Gyro_Y_Real == gy && Gyro_Z_Real == gz, "Gyro_*_Real unchanged", k);
    }

    /* 2. The correction still reaches the attitude: zero gyro, a still accelerometer tilted ~10 deg in roll. */
    Gyro_X_Real = Gyro_Y_Real = Gyro_Z_Real = 0.0f;
    Acc_X_Real = 0.0f;
    Acc_Y_Real = 1000.0f * sinf(10.0f / RAD2DEG);
    Acc_Z_Real = 1000.0f * cosf(10.0f / RAD2DEG);
    for (k = 0; k < 20000; k++) IMU_Update_Mahony(&imu_data, 1e-3f);
    check(fabsf(imu_data.rol - 10.0f) < 0.5f, "roll converges to the accelerometer tilt", k);
    check(fabsf(imu_data.pit) < 0.5f, "pitch converges to level", k);
    check(Gyro_X_Real == 0.0f && Gyro_Y_Real == 0.0f && Gyro_Z_Real == 0.0f, "zero samples stay zero", k);

    printf("test_imu_update: %d checks, %d failure(s) (roll %.3f pit %.3f)\n", g_checks, g_fail, imu_data.rol, imu_data.pit);
    return g_fail ? 1 : 0;
}
