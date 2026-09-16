#include "usart2.h"


void USART2_Configuration(void)  //光流
{
    USART_InitTypeDef usart2;
    GPIO_InitTypeDef  gpio;
    NVIC_InitTypeDef  nvic;
	

    RCC_AHB1PeriphClockCmd(RCC_AHB1Periph_GPIOA | RCC_AHB1Periph_DMA1,ENABLE);//使能PA端口时钟
    RCC_APB1PeriphClockCmd(RCC_APB1Periph_USART2,ENABLE);//使能USART2时钟
	

    GPIO_PinAFConfig(GPIOA,GPIO_PinSource2,GPIO_AF_USART2);
    GPIO_PinAFConfig(GPIOA,GPIO_PinSource3,GPIO_AF_USART2); 

	  gpio.GPIO_Pin = GPIO_Pin_2 | GPIO_Pin_3;
    gpio.GPIO_Mode = GPIO_Mode_AF;
    gpio.GPIO_Speed = GPIO_Speed_50MHz;
    gpio.GPIO_OType = GPIO_OType_PP;
    gpio.GPIO_PuPd = GPIO_PuPd_UP ;
  	GPIO_Init(GPIOA,&gpio);//根据设定参数初始化GPIOA

	/*USART2接收空闲中断*/
    nvic.NVIC_IRQChannel = USART2_IRQn;
    nvic.NVIC_IRQChannelPreemptionPriority = 0;//抢占优先级
    nvic.NVIC_IRQChannelSubPriority = 0;//子优先级
    nvic.NVIC_IRQChannelCmd = ENABLE;//IRQ通道使能 
    NVIC_Init(&nvic);//根据指定的参数初始化VIC寄存器
	
    usart2.USART_BaudRate = 500000;//波特率
    usart2.USART_WordLength = USART_WordLength_8b;//字长为8位数据格式
    usart2.USART_StopBits = USART_StopBits_1;//一个停止位
    usart2.USART_Parity = USART_Parity_No;//无奇偶校验位
    usart2.USART_Mode = USART_Mode_Tx|USART_Mode_Rx;//收发模式
    usart2.USART_HardwareFlowControl = USART_HardwareFlowControl_None;//无硬件数据流控制
    USART_Init(USART2,&usart2);//初始化串口
    USART_ITConfig ( USART2, USART_IT_RXNE, ENABLE );
	
    USART_Cmd(USART2,ENABLE);//使能串口


		
	
}
