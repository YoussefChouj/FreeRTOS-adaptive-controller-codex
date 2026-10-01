#pragma once
#include "data_types.h"
#include <stdbool.h>
bool bmi088_init(void);
UCHAR8 BMI088_Read_Gyro_Data(UCHAR8 reg_id);
void BMI088_Write_Gyro_Data(UCHAR8 reg_id,UCHAR8 data);
UCHAR8 BMI088_Read_Acc_Data(UCHAR8 reg_id);
void BMI088_Write_Acc_Data(UCHAR8 reg_id,UCHAR8 data);
extern SSHORT16 Real_Temp;
extern void LpFilter(ST_LPF* lpf);
extern FP32 Gyro_X_Real;
extern FP32 Gyro_Y_Real;
extern FP32 Gyro_Z_Real;
extern volatile UCHAR8 g_gyro_z_bias_track;
extern volatile UINT32 g_gyro_z_bias_blocks;
extern FP32 Acc_X_Real;
extern FP32 Acc_Y_Real;
extern FP32 Acc_Z_Real;
extern FP32 Acc_X_Ori;
extern FP32 Acc_Y_Ori;
extern FP32 Acc_Z_Ori;
