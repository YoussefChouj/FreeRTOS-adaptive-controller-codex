#ifndef __REMOTERTASK_H
#define __REMOTERTASK_H	 

#include "global_declare.h"
#include "usart1.h"
#include "GlobalUse_Basic_Function.h"
#include "usart2.h"
#include "imu_update.h"
#include "usart4.h"
#include "AutoflyTask.h"
#include "StabilizerTask.h"
#include "usart3.h"

#define  PITCH_CH    sbus_channel[1]
#define  ROLL_CH     sbus_channel[0]
#define  YAW_CH      sbus_channel[3]
#define  THR_CH      sbus_channel[2]

/* Auxiliary SBUS channels (0-indexed, raw 0-2000 range) */
#define  MODE_CH      sbus_channel[4]  /* ch5: 2-state — mid(≈1000)=IDLE, high(≈1600)=LAND; low treated as IDLE */
#define  FLYUP_CH     sbus_channel[6]  /* ch7: momentary — rising edge >500 commands fly-up to Z=0.5 m */
#define  PATH_EXEC_CH sbus_channel[7]  /* ch8: momentary — rising edge >500 triggers preset path */
#define  OFHOLD_CH    sbus_channel[5]  /* ch6: 2-state - high(~1694)=OF position-hold ON, low(~306)/lost=angle mode (default) */

/////////////////////////////////////////////////////////////////////////////////////
//���ݲ�ֺ궨�壬�ڷ��ʹ���1�ֽڵ���������ʱ������int16��float�ȣ���Ҫ�����ݲ�ֳɵ����ֽڽ��з���
#define BYTE0(dwTemp)       ( *( (char *)(&dwTemp)      ) )
#define BYTE1(dwTemp)       ( *( (char *)(&dwTemp) + 1) )
#define BYTE2(dwTemp)       ( *( (char *)(&dwTemp) + 2) )
#define BYTE3(dwTemp)       ( *( (char *)(&dwTemp) + 3) )
	

void remoter_task(void);
void Check_Fly_Mode(void);
void ANO_Report_UserData1(void);
void send_to_linux(void);

#endif
