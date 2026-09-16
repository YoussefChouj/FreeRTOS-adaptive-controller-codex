#include "FreeRTOS.h"
#include "task.h"

//任务优先级
#define START_TASK_PRIO		1
//任务堆栈大小	
#define START_STK_SIZE 		128  
//任务句柄
TaskHandle_t StartTask_Handler;
//任务函数
void start_task(void *pvParameters);

//////////////////////////////////函数的定义////////////////////////////////////////

//任务优先级
#define SYSTEMMONITOR_TASK_PRIO		1
//任务堆栈大小	
#define SYSTEMMONITOR_STK_SIZE 		500  
//任务句柄
TaskHandle_t SystemMonitorTask_Handler;
//任务函数								
void SystemMonitor_Task(void *pvParameters);								
								
//任务优先级
#define IMU_DataDeal_TASK_PRIO		4
//任务堆栈大小	
#define IMU_DataDeal_STK_SIZE 		500  
//任务句柄
TaskHandle_t IMU_DataDealTask_Handler;
//任务函数
void IMU_DataDeal_Task(void *pvParameters);

//任务优先级
#define IMUSAMPLE_TASK_PRIO		4
//任务堆栈大小	
#define IMUSAMPLE_STK_SIZE 		500
//任务句柄
TaskHandle_t IMUSampleTask_Handler;
//任务函数
void IMUSample_Task(void *pvParameters);

//任务优先级
#define STABILIZER_Task_TASK_PRIO		4
//任务堆栈大小	
#define STABILIZER_STK_SIZE 		500
//任务句柄
TaskHandle_t Stabilizer_Task_Handler;
//任务函数
void Stabilizer_Task(void *pvParameters);


//任务优先级
#define REMOTERTASK_TASK_PRIO		4
//任务堆栈大小	
#define REMOTERTASK_STK_SIZE 		500
//任务句柄
TaskHandle_t RemoterTaskTask_Handler;
//任务函数
void Remoter_Task(void *pvParameters);


//任务优先级
#define AUTOFLYTASK_TASK_PRIO		4
//任务堆栈大小	
#define AUTOFLYTASK_STK_SIZE 		500
//任务句柄
TaskHandle_t AutoflyTask_Handler;
//任务函数
void Autofly_Task(void *pvParameters);


//任务优先级
#define SENDTASK_PRIO		2
//任务堆栈大小	
#define SENDTASK_STK_SIZE 		500
//任务句柄
TaskHandle_t SendTask_Handler;
//任务函数
void Send_Task(void *pvParameters);

