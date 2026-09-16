#include "usart1.h"
#include "global_declare.h"
#include "FreeRTOS.h"
#include "task.h"

/*************************************************************************
函 数 名：USART1_Configuration(void)
函数功能：遥控器接收机DR16底层配置
备    注：PA10(USART1_RX)
*************************************************************************/

void USART1_Configuration(void)
{
  USART_InitTypeDef USART1_InitStructure;
	GPIO_InitTypeDef  GPIO_InitStructure;
  NVIC_InitTypeDef  NVIC_InitStructure;
  
	RCC_AHB1PeriphClockCmd(RCC_AHB1Periph_GPIOA,ENABLE	);
	RCC_APB2PeriphClockCmd(RCC_APB2Periph_USART1,ENABLE);

  NVIC_InitStructure.NVIC_IRQChannel                     = USART1_IRQn;
  NVIC_InitStructure.NVIC_IRQChannelPreemptionPriority   = 0;
  NVIC_InitStructure.NVIC_IRQChannelSubPriority          = 0;
  NVIC_InitStructure.NVIC_IRQChannelCmd                  = ENABLE;
  NVIC_Init(&NVIC_InitStructure);
	
	GPIO_PinAFConfig(GPIOA, GPIO_PinSource10 ,GPIO_AF_USART1);
	
	GPIO_InitStructure.GPIO_Pin      =     GPIO_Pin_10 ;
	GPIO_InitStructure.GPIO_Mode     =     GPIO_Mode_AF;
  GPIO_InitStructure.GPIO_OType    =     GPIO_OType_PP;
  GPIO_InitStructure.GPIO_Speed    =     GPIO_Speed_100MHz;
  GPIO_InitStructure.GPIO_PuPd     =     GPIO_PuPd_NOPULL;
	GPIO_Init(GPIOA,&GPIO_InitStructure);
      
  USART_DeInit(USART1);
	USART1_InitStructure.USART_BaudRate            =    100000;//SBUS 100K baudrate
	USART1_InitStructure.USART_WordLength          =    USART_WordLength_8b;
	USART1_InitStructure.USART_StopBits            =    USART_StopBits_2;
	USART1_InitStructure.USART_Parity              =    USART_Parity_Even;
	USART1_InitStructure.USART_Mode                =    USART_Mode_Rx;
  USART1_InitStructure.USART_HardwareFlowControl =    USART_HardwareFlowControl_None;
	USART_Init(USART1,&USART1_InitStructure);
    
	USART_ITConfig(USART1,USART_IT_RXNE,ENABLE);
  USART_Cmd(USART1,ENABLE);//使能串口
}


unsigned short sbus_channel[16];



void DrvSbusGetOneByte(u8 data)
{
	const u8 frame_end[4] = {0x04, 0x14, 0x24, 0x34};
	static u8 datatmp[25];
	static u8 cnt = 0;
	static u8 frame_cnt;

	datatmp[cnt++] = data;
	//
	if (cnt == 25)
	{
		cnt = 24;
		if ((datatmp[0] == 0x0F && (datatmp[24] == 0x00 || datatmp[24] == frame_end[frame_cnt])))
		{
			cnt = 0;
			sbus_channel[0] = (s16)(datatmp[2] & 0x07) << 8 | datatmp[1];
			sbus_channel[1] = (s16)(datatmp[3] & 0x3f) << 5 | (datatmp[2] >> 3);
			sbus_channel[2] = (s16)(datatmp[5] & 0x01) << 10 | ((s16)datatmp[4] << 2) | (datatmp[3] >> 6);
			sbus_channel[3] = (s16)(datatmp[6] & 0x0F) << 7 | (datatmp[5] >> 1);
			sbus_channel[4] = (s16)(datatmp[7] & 0x7F) << 4 | (datatmp[6] >> 4);
			sbus_channel[5] = (s16)(datatmp[9] & 0x03) << 9 | ((s16)datatmp[8] << 1) | (datatmp[7] >> 7);
			sbus_channel[6] = (s16)(datatmp[10] & 0x1F) << 6 | (datatmp[9] >> 2);
			sbus_channel[7] = (s16)datatmp[11] << 3 | (datatmp[10] >> 5);

			sbus_channel[8] = (s16)(datatmp[13] & 0x07) << 8 | datatmp[12];
			sbus_channel[9] = (s16)(datatmp[14] & 0x3f) << 5 | (datatmp[13] >> 3);
			sbus_channel[10] = (s16)(datatmp[16] & 0x01) << 10 | ((s16)datatmp[15] << 2) | (datatmp[14] >> 6);
			sbus_channel[11] = (s16)(datatmp[17] & 0x0F) << 7 | (datatmp[16] >> 1);
			sbus_channel[12] = (s16)(datatmp[18] & 0x7F) << 4 | (datatmp[17] >> 4);
			sbus_channel[13] = (s16)(datatmp[20] & 0x03) << 9 | ((s16)datatmp[19] << 1) | (datatmp[18] >> 7);
			sbus_channel[14] = (s16)(datatmp[21] & 0x1F) << 6 | (datatmp[20] >> 2);
			sbus_channel[15] = (s16)datatmp[22] << 3 | (datatmp[21] >> 5);

			sbus_last_valid_tick = xTaskGetTickCountFromISR();
			
		}
		else
		{
			for (u8 i = 0; i < 24; i++)
			{
				datatmp[i] = datatmp[i + 1];
			}
		}
	}
}
