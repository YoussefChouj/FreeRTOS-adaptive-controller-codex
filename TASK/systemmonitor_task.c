#include "systemmonitor_task.h"

/*--------------------------------------------------------
功能：异常情况监测工具
----------------------------------------------------------*/

int beep_cnt = 0;
void SystemErrorDetect(void)
{
	system_monitor.IMUSampleTask_fps =system_monitor.IMUSampleTask_cnt; 
	system_monitor.IMUSampleTask_cnt = 0;
	system_monitor.IMUUpdateTask_fps =system_monitor.IMUUpdateTask_cnt; 
	system_monitor.IMUUpdateTask_cnt = 0;
	system_monitor.stabilizerTask_fps =system_monitor.stabilizerTask_cnt; 
	system_monitor.stabilizerTask_cnt = 0;
	system_monitor.remoter_task_fps =system_monitor.remoter_task_cnt; 
	system_monitor.remoter_task_cnt = 0;
	system_monitor.USART1_task_fps =system_monitor.USART1_task_cnt; 
	system_monitor.USART1_task_cnt = 0;
	system_monitor.USART2_task_fps =system_monitor.USART2_task_cnt; 
	system_monitor.USART2_task_cnt = 0;
	system_monitor.USART4_task_fps =system_monitor.USART4_task_cnt; 
	system_monitor.USART4_task_cnt = 0;
	system_monitor.USART5_task_fps =system_monitor.USART5_task_cnt; 
	system_monitor.USART5_task_cnt = 0;
	system_monitor.AutoflyTask_fps =system_monitor.AutoflyTask_cnt; 
	system_monitor.AutoflyTask_cnt = 0;
   //GPIO_ResetBits(GPIOB,GPIO_Pin_9);
	
	  if(Ctrler.Z_posPID.FB == 0)  //高度没反馈是最严重的后果，显示红灯  system_monitor.USART4_task_cnt++;
	{
	 	GPIO_SetBits(GPIOA,GPIO_Pin_11 ); //红色
	  GPIO_SetBits(GPIOA,GPIO_Pin_12 );
	  GPIO_ResetBits(GPIOC,GPIO_Pin_8 );
	}

		else if ( system_monitor.USART4_task_fps <10)  //linux电脑通讯不正常
	{
	  	GPIO_ResetBits(GPIOA,GPIO_Pin_11 ); //青色
	    GPIO_ResetBits(GPIOA,GPIO_Pin_12 );
	    GPIO_ResetBits(GPIOC,GPIO_Pin_8 );
	}
			else if (linux_data.t265posy == 0 && linux_data.t265posx == 0  )  //t265 fali
	{
	  	GPIO_SetBits(GPIOA,GPIO_Pin_11 ); //蓝色
	    GPIO_ResetBits(GPIOA,GPIO_Pin_12 );
	    GPIO_SetBits(GPIOC,GPIO_Pin_8 );
	}

	else //一切正常绿色
	{	  
   	GPIO_ResetBits(GPIOA,GPIO_Pin_11 ); //绿色
	  GPIO_SetBits(GPIOA,GPIO_Pin_12 );
	  GPIO_SetBits(GPIOC,GPIO_Pin_8 );
	}
}
