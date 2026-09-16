#ifndef __SYSTEMMONITOR_TASK_H__
#define __SYSTEMMONITOR_TASK_H__

#include "global_declare.h"
#include "stm32f4xx.h"
#include  "main.h"

#define Calc_System_Monitor_fps(xx) do{ \
	    system_monitor.xx##_fps = system_monitor.xx##_cnt; \
	    system_monitor.xx##_cnt = 0;\
}while(0)

#define Calc_All_fps()  do{ \
	 Calc_System_Monitor_fps(System);\
}while(0)

extern void SystemErrorDetect(void);

//extern ST_TAST G_ST_Task[];
extern u32 TotalTaskNum;
extern SYSTEM_MONITOR system_monitor;

#endif

