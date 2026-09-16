#ifndef __BMI088_H__
#define __BMI088_H__

#include "main.h"
#include "spi.h"
#include "global_declare.h"
#include "time.h"
#include "algorithm.h"
#include "data_types.h"
#include "robot_types.h"
#include "math.h"
#include "delay.h"
#include "Filter.h"
#include "Accel_Calibartion.h"
bool bmi088_init(void);
UCHAR8 BMI088_Read_Gyro_Data(UCHAR8 reg_id);
void BMI088_Write_Gyro_Data(UCHAR8 reg_id,UCHAR8 data);
UCHAR8 BMI088_Read_Acc_Data(UCHAR8 reg_id);
void BMI088_Write_Acc_Data(UCHAR8 reg_id,UCHAR8 data);
void Sensor_Data_Prepare(void);
extern SSHORT16 Real_Temp;
extern void LpFilter(ST_LPF* lpf);

typedef struct 
{
	u8 acc_CALIBRATE;
	u8 gyr_CALIBRATE;
	u8 acc_z_auto_CALIBRATE;
	u8 temp_CALIBRATE;
	u8 sensor_ok;
	u8 motionless;
	s16 Acc_Original[VEC_XYZ];
	s16 Gyro_Original[VEC_XYZ];
	s16 Tempreature;
	float Tempreature_C;
	
	float Acc[VEC_XYZ];       //模拟量
	float Acc_cmss[VEC_XYZ];  //单位是cm/s2
	float Gyro[VEC_XYZ];      //模拟量
	float Gyro_deg[VEC_XYZ];  //单位是角度/s
	float Gyro_rad[VEC_XYZ];  //单位是弧度/s
	
	float acc_coe[VEC_XYZ];   //加速度计刻度因数
	float acc_offset[VEC_XYZ];//加速度计零偏
	float gyro_offset[VEC_XYZ];

	
}_sensor_st;


typedef struct 
{
	float ori_acc_x;
	float ori_acc_y;
	float ori_acc_z;
	
	float real_acc_x;
	float real_acc_y;
	float real_acc_z;
	float Gyro_Length;  //陀螺仪的模长
	
}_acc_data_st;


typedef struct
{
	float xv[3];
	float yv[3];
	float input;
	float output;
}Bw30HzLPFTypeDef;

typedef struct
{
	float xv[4];
	float yv[4];
	float input;
	float output;
}Bw50HzLPFTypeDef;

typedef struct
{
	Bw50HzLPFTypeDef GyroxLPF;
	Bw50HzLPFTypeDef GyroyLPF;
	Bw50HzLPFTypeDef GyrozLPF;
	Bw30HzLPFTypeDef AccxLPF;
	Bw30HzLPFTypeDef AccyLPF;
	Bw30HzLPFTypeDef AcczLPF;
	Bw30HzLPFTypeDef spl06LPF;
}Filter6axisTypeDef;

extern _sensor_st sensor;

extern FP32 Gyro_X_Real;											//单位为弧度每秒
extern FP32 Gyro_Y_Real;
extern FP32 Gyro_Z_Real;

extern FP32 Acc_X_Real;											//单位为mg
extern FP32 Acc_Y_Real;
extern FP32 Acc_Z_Real;

extern FP32 Acc_X_Ori;											//单位为mg
extern FP32 Acc_Y_Ori;
extern FP32 Acc_Z_Ori;
extern _acc_data_st acc_data;

#define Cali_Rol_Coe 1
#define Cali_Pit_Coe 	1
#define Cali_Yaw_Coe 	1

#define Cali_Rol_CoeA 1
#define Cali_Pit_CoeA 1
#define Cali_Yaw_CoeA 1

//Accelerater_Reg

#define	ACC_ID				0X00
#define	ACC_ERR				0X02
#define ACC_STATUS		0X03
#define	ACC_X_LSB			0X12
#define	ACC_X_MSB			0X13
#define	ACC_Y_LSB 		0X14
#define	ACC_Y_MSB 		0X15
#define	ACC_Z_LSB 		0X16
#define	ACC_Z_MSB 		0X17
#define ACC_RANGE			0X41
#define	TEMP_MSB			0X22
#define	TEMP_LSB 			0X23
#define ACC_CONF			0X40
#define	ACC_PWR_CTRL	0X7D
#define ACC_PWR_CONF	0X7C
#define ACC_SOFT_R		0X7E


//Gyro_Reg

#define	GYRO_ID				0X00
#define	GYRO_X_LSB		0X02
#define	GYRO_X_MSB		0X03
#define	GYRO_Y_LSB		0X04
#define	GYRO_Y_MSB		0X05
#define	GYRO_Z_LSB		0X06
#define	GYRO_Z_MSB		0X07
#define	GYRO_RANGE		0X0F
#define	GYRO_BANDW		0X10
#define GYRO_POWER		0X11
#define	GYRO_SOFT_R		0X14

#define CALI_NUM			5000

#define FAIL          0
#define SUCCESS       1

#define NOP			__asm{NOP;}

#define OFF_FREQ			1500

void Butterworth50HzLPF(Bw50HzLPFTypeDef* pLPF);
void Butterworth30HzLPF(Bw30HzLPFTypeDef* pLPF);

extern Filter6axisTypeDef Filters;

#endif



