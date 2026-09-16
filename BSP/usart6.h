#ifndef  __USART6_H__
#define  __USART6_H__

#include "stm32f4xx.h"
#include "stdio.h"	
#include "stm32f4xx_conf.h"
#include "sys.h" 

extern u8 Rx_Buf[];
extern u8 Tx6Buffer[256];
extern u8 Tx6Counter;
extern u8 count6 ;

void Usart6_Init(void);
void Usart6_IRQ(void);
void Usart6_Send(unsigned char *DataToSend ,u8 data_num);

#endif
