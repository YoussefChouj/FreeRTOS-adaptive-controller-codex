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

#define REAL   float
#define TAN_MAP_SIZE    256
#define TAN_MAP_RES     0.003921569f     /* (smallest non-zero value in table) */

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
REAL fast_atan2(REAL y, REAL x);

typedef struct 
{
float target_x;//�趨Ŀ���x����
float target_y;//�趨Ŀ���y����
float target_z;//�趨Ŀ���z����
float world_x; //��ʵ��������
float world_y; //��ʵ��������
float world_z; //��ʵ��������
int execute;//��0�����޸�Ŀ�����꣬��1����������Ŀ��	
float set_yaw;//�趨ƫ����
float real_yaw;//ʵ��ƫ����
}TargetSet_WorldReal_Coordinate;
extern TargetSet_WorldReal_Coordinate TWC;

extern float real_voltage; //�������ʵ�ʵ�ѹ
extern float earth_x;
extern float earth_y;
extern float Cos_Yaw_01;
extern int track_fly_mode;

#endif

