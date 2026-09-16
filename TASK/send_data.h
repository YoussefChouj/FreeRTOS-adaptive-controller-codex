#ifndef __SEND_DATA_H
#define __SEND_DATA_H	 

#include "global_declare.h"
#include "usart1.h"
#include "GlobalUse_Basic_Function.h"
#include "usart2.h"
#include "imu_update.h"
#include "usart4.h"
#include "AutoflyTask.h"
#include "StabilizerTask.h"
#include "usart3.h"

void ANO_Report_UserData1(void);
void send_to_linux(void);
void Send_Groundstation_Telemetry_UART4(void);
void Process_GroundStation_Command(void);
extern _linux_flag stm32_to_linux_flag;
extern UCHAR8 str_USART[16];
void usart3_send(void);
#endif

