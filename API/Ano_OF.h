#ifndef __DRV_ANO_OF_H
#define __DRV_ANO_OF_H

/* ANO optical-flow module: decoded fields. Written by Ano_OF.c (USART2 RX); read by StabilizerTask.c and
   send_data.c. */

#include "stm32f4xx.h"

typedef struct
{
	u8 of_update_cnt;  /* +1 per height-fused flow frame (mode 1)          */
	u8 alt_update_cnt; /* +1 per height frame                              */

	u8 link_sta;       /* 1 = frames arriving (set by AnoOF_Check_State)   */
	u8 work_sta;       /* 1 = flow and height both fresh                   */

	u8 of_quality;     /* flow quality from the latest flow frame          */

	u8 of0_sta;        /* mode 0: raw flow                                 */
	s8 of0_dx;
	s8 of0_dy;

	u8 of1_sta;        /* mode 1: height-fused flow                        */
	s16 of1_dx;
	s16 of1_dy;

	u8 of2_sta;        /* mode 2: inertial-fused flow                      */
	s16 of2_dx;
	s16 of2_dy;
	s16 of2_dx_fix;
	s16 of2_dy_fix;
	s16 intergral_x;
	s16 intergral_y;

	u32 of_alt_cm;     /* height [cm]                                      */
	float of2_raw_h;   /* height chain kept by StabilizerTask.c            */
	float of2_h;
	float of2_last_h;
	float of2_h_f2_v;
	float of2_h_v;
	float of2_last_h_v;

	float quaternion[4];

	s16 acc_data_x;    /* module IMU, raw LSB                              */
	s16 acc_data_y;
	s16 acc_data_z;
	s16 gyr_data_x;
	s16 gyr_data_y;
	s16 gyr_data_z;

	float DISTANCE_X;  /* integrated distance [cm], wraps -32768..+32767   */
	float DISTANCE_Y;  /* integrated distance [cm], wraps -32768..+32767   */
	float earth_x;
	float earth_y;
	float earth_x_ture;
	float earth_y_ture;

} _ano_of_st;

extern _ano_of_st ano_of;

void AnoOF_GetOneByte(uint8_t data);
void AnoOF_Check_State(float dT_s);
#endif
