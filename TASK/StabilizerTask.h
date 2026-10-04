#ifndef _STABILIZERTASK__H_
#define _STABILIZERTASK__H_

#include "algorithm.h"
#include "robot_types.h"
#include "pid.h"
#include "pwm.h"
#include "global_declare.h"
#include "imu_update.h"
#include "bmi088_driver.h"
#include "Ano_OF.h"
#include "tf_mini_plus.h"
#include "AutoflyTask.h"
#include "stm32f4xx_it.h"
#include "SINS.h"
#include "usart4.h"
#include "GPS.h"

#define case_Update_loc_Des          1
#define case_Update_v_loc_Des        2
#define case_Update_height_Des       3
#define case_Update_v_h_Des          4
#define case_Update_pitrol_Des       5
#define case_Update_yaw_Des          6
#define case_Update_gyro_Des         7

/* g_of_bias_mode values (StabilizerTask.c, set by CMD 0x1E idx 0 in send_data.c) */
#define OF_BIAS_FIXED                0U   /* bias seeded once, never adapted */
#define OF_BIAS_EMA                  1U   /* continuous EMA, tau g_of_bias_ema_tau_s */
#define OF_BIAS_EKF                  2U   /* 8-state OF KF (API/ekf_of.c) feeds position and velocity */

void stabilizer_Task(void);
void Reset_World_Origin(void);
void Update_Motor(void);
void Compute_Motor(void);
void Update_Des(unsigned char which_level);
void Update_Data(void);
void accel_to_lean_angles(float acc_tar_forward,float acc_tar_right,float *tar_pitch,float *tar_roll);

void Get_Voltage(void);

float Constrain_Float(float amt, float low, float high);
float fast_atan(float v);

/* Target / world coordinates shared by the setpoint logic, the GS commands and the WFB glue.
 * x/y in cm (the locx/locy loop frame), z in m, yaw in deg. */
typedef struct
{
float target_x;   /* target x, cm */
float target_y;   /* target y, cm */
float target_z;   /* target height, m */
float world_x;    /* current x (locxPID.FB), cm; refreshed by every Update_Des call */
float world_y;    /* current y (locyPID.FB), cm */
float world_z;    /* current height (Z_posPID.FB), m */
int execute;      /* 1 = fly to target_*, set_yaw; 0 = sticks / hold */
float set_yaw;    /* target heading, deg */
float real_yaw;   /* current heading (yawPID.FB), deg */
}TargetSet_WorldReal_Coordinate;
extern TargetSet_WorldReal_Coordinate TWC;

extern float real_voltage;   /* pack voltage, V (Get_Voltage) */
extern float Cos_Yaw_01;

#endif

