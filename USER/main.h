#ifndef __MAIN_H__
#define __MAIN_H__

#include "imu_update.h"
#include "FreeRTOS.h"
#include "task.h"
#include "stm32f4xx.h"
#include "time_estimate.h"
#include "systemmonitor_task.h"
#include "global_declare.h"
#include "robot_types.h"
#include "bmi088_driver.h"
#include "StabilizerTask.h"
#include "RemoterTask.h"
#include "AutoflyTask.h"
#include "usart5.h"
#include "Accel_Calibartion.h"
#include "send_data.h"


extern void SystemErrorDeteck(void);
extern void BSP_Init(void);


#endif



