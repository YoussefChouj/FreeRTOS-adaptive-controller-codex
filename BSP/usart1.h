#ifndef  __USART1_H__
#define  __USART1_H__

#include "stm32f4xx.h"
#include "data_types.h"
#include "robot_types.h"

/*串口1通信缓冲长度*/
#define USART1_RXDMA_LEN           50
#define USART1_RXMB_LEN            50
#define USART1_RX_STREAM           DMA2_Stream2

void USART1_Configuration(void);
void sbus_decode(unsigned char buffer[24]);
void DrvSbusGetOneByte(u8 data);
	
extern  unsigned short sbus_channel[16];



#endif
