/* Host stub for API/imu_update.c (tests/firmware_host/test_imu_update.c): the real interface, with the few
 * firmware types it needs (Global_file/robot_types.h _imu_st and X/Y/Z, API/bmi088_driver.h sensor and samples)
 * instead of the STM32 header chain. tools/host_tests.py copies imu_update.c to {src} so this header wins. */
#ifndef STUBS_IMU_IMU_UPDATE_H
#define STUBS_IMU_IMU_UPDATE_H

#include <stdint.h>
#include <math.h>

#define PI      3.14159265358979f
#define RAD2DEG (180.0f / PI)

enum { X = 0, Y = 1, Z = 2, VEC_XYZ };

typedef struct {
    float q0, q1, q2, q3;
    float gkp, gki;
    float x_vec[VEC_XYZ], y_vec[VEC_XYZ], z_vec[VEC_XYZ];
    float a_acc[VEC_XYZ], gacc_deadzone[VEC_XYZ], gra_acc[VEC_XYZ];
    float rol, pit, yaw;
} _imu_st;

typedef struct { uint8_t sensor_ok; } _sensor_st;
extern _sensor_st sensor;

extern float Gyro_X_Real, Gyro_Y_Real, Gyro_Z_Real;   /* rad/s */
extern float Acc_X_Real, Acc_Y_Real, Acc_Z_Real;      /* mg */

extern _imu_st imu_data;
void IMU_Update_Mahony(_imu_st *imu, float dt);
uint8_t IMU_EstimatorReady(void);
extern float g_imu_settle_metric;
extern float Lin_Acc_X_body, Lin_Acc_Y_body, Lin_Acc_Z_body;
extern float Gravity_Body_X, Gravity_Body_Y, Gravity_Body_Z;

#endif /* STUBS_IMU_IMU_UPDATE_H */
