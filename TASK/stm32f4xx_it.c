#include "stm32f4xx_it.h"
#include "rpm.h"

/* ====================================================================
 * DEBUG-telemetry-bisect: fault reporting over UART5 (polling, no DMA/RTOS).
 * Kept for future use — flip to `#if 1` to override the silent startup-file
 * HardFault_Handler so a fault ANNOUNCES itself on COM6 instead of looking like
 * a dead link. Format: "HARDFAULT PSP_PC=... PSP_LR=... MSP_PC=..." — look up
 * the PC address in the Keil .map / disassembly to find the faulting line.
 * ==================================================================== */
#if 0
static void dbg_it_uart5_putc(char c)
{
    while (USART_GetFlagStatus(UART5, USART_FLAG_TXE) == RESET) { }
    USART_SendData(UART5, (uint8_t)c);
}
static void dbg_it_uart5_puts(const char *s)
{
    while (*s) { dbg_it_uart5_putc(*s++); }
}
static void dbg_it_uart5_hex32(uint32_t v)
{
    const char *hx = "0123456789ABCDEF";
    int i;
    for (i = 28; i >= 0; i -= 4) { dbg_it_uart5_putc(hx[(v >> i) & 0xFU]); }
}

void HardFault_Handler(void)
{
    volatile uint32_t d;
    uint32_t psp = __get_PSP();
    uint32_t msp = __get_MSP();
    uint32_t *pf = (uint32_t *)psp;  /* stacked frame if fault was in task (PSP) context */
    uint32_t *mf = (uint32_t *)msp;  /* stacked frame if fault was in handler (MSP) context */
    for (;;)
    {
        dbg_it_uart5_puts("\r\nHARDFAULT PSP_PC=");
        dbg_it_uart5_hex32(pf[6]);
        dbg_it_uart5_puts(" PSP_LR=");
        dbg_it_uart5_hex32(pf[5]);
        dbg_it_uart5_puts(" MSP_PC=");
        dbg_it_uart5_hex32(mf[6]);
        dbg_it_uart5_puts("\r\n");
        for (d = 3000000U; d != 0U; d--) { }
    }
}
#endif /* DEBUG-telemetry-bisect */

USHORT16 Clear_IT = 0;
/*************************************************************************
中断处理函数名称：USART1_IRQHandler
*************************************************************************/

extern USART_RX_TypeDef USART1_Rcr;
extern UCHAR8 UA1RxDMAbuf[USART1_RXDMA_LEN];
void USART1_IRQHandler(void)
{
 	u8 com_data;

	if (USART_GetITStatus(USART1, USART_IT_RXNE))
	{
		USART_ClearITPendingBit(USART1, USART_IT_RXNE);
		//==
		com_data = USART1->DR;
		//
		DrvSbusGetOneByte(com_data);
		
		system_monitor.USART1_task_cnt++;
	}
}

///*************************************************************************
//中断处理函数名称：USART2_IRQHandler
//中断产生机制：USART2接收到一个空字节后触发中断
//*************************************************************************/
//extern USART_RX_TypeDef USART2_Rcr;
void USART2_IRQHandler(void)
{ 
    u8 com_data;

	if ( USART2->SR & USART_SR_ORE ) //ORE中断
        com_data = USART2->DR;
    //接收中断
    if ( USART_GetITStatus ( USART2, USART_IT_RXNE ) )
    {
        USART_ClearITPendingBit ( USART2, USART_IT_RXNE ); //清除中断标志

        com_data = USART2->DR;
				//====
				//匿名光流解析
					AnoOF_GetOneByte(com_data);		
       	system_monitor.USART2_task_cnt ++;			
    }
}

/***********************************************************************************
中断处理函数名称：DMA1_Stream6_IRQHandler
中断产生机制：串口2发送完成中断
函数功能：
************************************************************************************/
void DMA1_Stream6_IRQHandler(void)
{
   if(DMA_GetITStatus(DMA1_Stream6, DMA_IT_TCIF6))
   {
      DMA_ClearFlag(DMA1_Stream6, DMA_FLAG_TCIF6);//清除标志位
    	DMA_Cmd(DMA1_Stream6, DISABLE);             //关闭DMA传输 
   }
}

/***********************************************************************************
中断处理函数名称：USART3_IRQHandler
中断产生机制：串口3接收完成中断
函数功能：
************************************************************************************/
void USART3_IRQHandler(void)
{
  	if(USART_GetITStatus(USART3, USART_IT_IDLE)!= RESET)
	{
		Clear_IT = USART3->SR;
		Clear_IT = USART3->DR;//先读SR后读DR清楚中断标志位

		/* Drain the RX DMA ring into UA3RxMailbox (was: drained and counted,
		 * never parsed). USART3 is now a command ingress: dispatch the bytes
		 * through the same 0xCC 0xDD parser UART5 uses, so the radio link can
		 * carry every dashboard command (CMD 0x01..0x18). Mirrors the UART5
		 * path below. UA3RxFrameCnt / UA3RxLastLen stay as livewatch-visible
		 * proof the radio downlink is alive when driven in full duplex. */
		extern USHORT16 USART_Receive(USART_RX_TypeDef* USARTx);
		extern USART_RX_TypeDef USART3_Rcr;
		extern volatile uint32_t UA3RxFrameCnt;
		extern volatile uint16_t UA3RxLastLen;
		extern void Handle_USART3_GroundStation_Command(const uint8_t*, USHORT16);
		{
			uint16_t rx_len = USART_Receive(&USART3_Rcr);
			if(rx_len > 0)
			{
				UA3RxLastLen = rx_len;
				UA3RxFrameCnt++;
				Handle_USART3_GroundStation_Command(UA3RxMailbox, (USHORT16)USART3_Rcr.rxSize);
			}
		}
	}
}
/***********************************************************************************
中断处理函数名称：USART3_IRQHandler1
中断产生机制：串口3发送完成中断
函数功能：
************************************************************************************/
//void USART3_IRQHandler1(void)
//{
//    // 检查是否是发送完成中断
//    if (USART_GetITStatus(USART3, USART_IT_TC) != RESET)
//    {
//        // 清除发送完成中断标志位
//        USART_ClearITPendingBit(USART3, USART_IT_TC);

//        // 在这里可以添加发送完成后的处理逻辑
//        // 例如：启动下一次发送、通知主程序发送完成等
//    }
//}

void DMA1_Stream3_IRQHandler(void)//串口3发送完成中断，这个一定不能删除，否则系统会卡死
{
   /* Body moved into BSP/usart3.c so the ring's tail pointer stays private to
    * the driver. It no longer just parks the stream: it advances the ring and
    * immediately arms the next chunk, which is what keeps USART3 transmitting
    * back to back instead of one frame per Send_Task tick. Calls no FreeRTOS
    * API, so running at preemption priority 0 is safe. */
   extern void Usart3_Tx_DmaIsr(void);
   Usart3_Tx_DmaIsr();
}
/* ADR-0010: RPM acquisition - EXTI handlers for PA0/PA1/PC6/PC7.
 * Re-targeted 2026-07-21 from PA5/PB3/PB10/PB11; the new pins are
 * UART4 TX/RX and UART6 TX/RX (physically unused on this custom FC). */
void EXTI0_IRQHandler(void)
{
	if (EXTI_GetITStatus(RPM_CH0_EXTI_LINE) != RESET)
	{
		RPM_EdgeISR(0);
		EXTI_ClearITPendingBit(RPM_CH0_EXTI_LINE);
	}
}

void EXTI1_IRQHandler(void)
{
	if (EXTI_GetITStatus(RPM_CH1_EXTI_LINE) != RESET)
	{
		RPM_EdgeISR(1);
		EXTI_ClearITPendingBit(RPM_CH1_EXTI_LINE);
	}
}

void EXTI9_5_IRQHandler(void)
{
	if (EXTI_GetITStatus(RPM_CH2_EXTI_LINE) != RESET)
	{
		RPM_EdgeISR(2);
		EXTI_ClearITPendingBit(RPM_CH2_EXTI_LINE);
	}
	if (EXTI_GetITStatus(RPM_CH3_EXTI_LINE) != RESET)
	{
		RPM_EdgeISR(3);
		EXTI_ClearITPendingBit(RPM_CH3_EXTI_LINE);
	}
}
//void DMA1_Stream1_IRQHandler(void)//串口3发送完成中断
//{
//   if(DMA_GetITStatus(DMA1_Stream1, DMA_IT_TCIF3))
//   {
//      DMA_ClearFlag(DMA1_Stream1, DMA_FLAG_TCIF1);//清除标志位
//    	DMA_Cmd(DMA1_Stream1, DISABLE);             //关闭DMA传输 
//   }
//}
USHORT16 USART_Receive(USART_RX_TypeDef* USARTx)
{ 
	USARTx->rxConter = USARTx->DMALen - DMA_GetCurrDataCounter(USARTx->DMAy_Streamx);  //本次DMA缓冲区填充到的位置

	USARTx->rxBufferPtr += USARTx->rxSize;  //上次DMA缓冲区填充到的位置

	if(USARTx->rxBufferPtr >= USARTx->DMALen)//说明DMA缓冲区已经满了一次
	{
		USARTx->rxBufferPtr %= USARTx->DMALen;
	}

	if(USARTx->rxBufferPtr < USARTx->rxConter)
	{
		USARTx->rxSize = USARTx->rxConter - USARTx->rxBufferPtr; //计算本次接收数据的长度
		if(USARTx->rxSize <= USARTx->MbLen) 
		{
			for(u16 i=0;i<USARTx->rxSize;i++)  *(USARTx->pMailbox + i) = *(USARTx->pDMAbuf + USARTx->rxBufferPtr + i);
		}
	}
	else
	{
		USARTx->rxSize = USARTx->rxConter + USARTx->DMALen - USARTx->rxBufferPtr;//计算本次接收数据的长度
		if(USARTx->rxSize <= USARTx->MbLen) //接收的数据长度不超过期望数据长度，把数据写进邮箱，防止数组越界
		
		{
			for(u16 i=0;i<USARTx->rxSize-USARTx->rxConter;i++) *(USARTx->pMailbox + i) = *(USARTx->pDMAbuf + USARTx->rxBufferPtr + i);
			for(u16 i=0;i<USARTx->rxConter;i++) *(USARTx->pMailbox + USARTx->rxSize-USARTx->rxConter + i) = *(USARTx->pDMAbuf + i);
		}
	}
	return USARTx->rxSize;  //返回本次空闲中断一共接收多少字节
}


/***********************************************************************************
中断处理函数名称：UART4_IRQHandler
中断产生机制：视觉通讯
函数功能：
************************************************************************************/
extern USART_RX_TypeDef UART4_Rcr;
void UART4_IRQHandler(void)
{
	if(USART_GetITStatus(UART4, USART_IT_IDLE)!= RESET)
	{
		Clear_IT = UART4->SR;
		Clear_IT = UART4->DR;//先读SR后读DR清楚中断标志位
		
		uint16_t rx_len = USART_Receive(&UART4_Rcr);
		if(rx_len > 0)
		{
             extern void Handle_UART4_GroundStation_Command(void);
             Handle_UART4_GroundStation_Command();
             
             if (rx_len == UART4_RXMB_LEN) {
			     Decode_RX_Data_t265();	
             }
			system_monitor.USART4_task_cnt++;		
		}		
	}
	  
}

union 
{
   float data_float;
	 char  cdata[4];
}data_to_float;

float U4_RX_Data = 0;

void Decode_RX_Data_t265(void) //改成自己接收的数据
{
    if (UA4RxMailbox[0] == 0xAA && UA4RxMailbox[1] == 0xAA)
   {
    data_to_float.cdata[0]  =  UA4RxMailbox[2];
		data_to_float.cdata[1]  =  UA4RxMailbox[3];
    data_to_float.cdata[2]  =  UA4RxMailbox[4];
    data_to_float.cdata[3]  =  UA4RxMailbox[5];	

		if(data_to_float.data_float>-1000000.0f && data_to_float.data_float<1000000.0f)  //范围限幅
		{
		  U4_RX_Data = data_to_float.data_float*100.0f  ;
		}
		data_to_float.cdata[0]  =  UA4RxMailbox[6];
		data_to_float.cdata[1]  =  UA4RxMailbox[7];
    data_to_float.cdata[2]  =  UA4RxMailbox[8];
    data_to_float.cdata[3]  =  UA4RxMailbox[9];
		
    if(data_to_float.data_float>-1000000.0f && data_to_float.data_float< 1000000.0f)
	  {		 
		 U4_RX_Data  = data_to_float.data_float*100.0f ;
	  }
		 
		data_to_float.cdata[0]  =  UA4RxMailbox[10];
		data_to_float.cdata[1]  =  UA4RxMailbox[11];
    data_to_float.cdata[2]  =  UA4RxMailbox[12];
    data_to_float.cdata[3]  =  UA4RxMailbox[13];	
		
	  if(data_to_float.data_float>-1000000.0f && data_to_float.data_float<1000000.0f)
	  {		 
			 U4_RX_Data  = data_to_float.data_float*100.0f ;
	  }

		 
		data_to_float.cdata[0]  =  UA4RxMailbox[14];
		data_to_float.cdata[1]  =  UA4RxMailbox[15];
    data_to_float.cdata[2]  =  UA4RxMailbox[16];
    data_to_float.cdata[3]  =  UA4RxMailbox[17];	
			
		if(data_to_float.data_float>-1000000.0f && data_to_float.data_float< 1000000.0f)
	  {		 
			U4_RX_Data  = data_to_float.data_float*100.0f ;
	  }
	
		data_to_float.cdata[0]  =  UA4RxMailbox[18];
		data_to_float.cdata[1]  =  UA4RxMailbox[19];
    data_to_float.cdata[2]  =  UA4RxMailbox[20];
    data_to_float.cdata[3]  =  UA4RxMailbox[21];	
		
		if(data_to_float.data_float>-1000000.0f && data_to_float.data_float< 1000000.0f)
	  {
			U4_RX_Data = data_to_float.data_float *100.0f;
		}
	  data_to_float.cdata[0]  =  UA4RxMailbox[22];
		data_to_float.cdata[1]  =  UA4RxMailbox[23];
    data_to_float.cdata[2]  =  UA4RxMailbox[24];
    data_to_float.cdata[3]  =  UA4RxMailbox[25];	
		if(data_to_float.data_float>-1000000.0f && data_to_float.data_float< 1000000.0f)
	  {
			U4_RX_Data = data_to_float.data_float *100.0f;
		}
		
		
		data_to_float.cdata[0]  =  UA4RxMailbox[26];
		data_to_float.cdata[1]  =  UA4RxMailbox[27];
    data_to_float.cdata[2]  =  UA4RxMailbox[28];
    data_to_float.cdata[3]  =  UA4RxMailbox[29];	
		if(data_to_float.data_float>-5.0f && data_to_float.data_float< 5.0f)
	  {
			U4_RX_Data = data_to_float.data_float ;
		}

		
		data_to_float.cdata[0]  =  UA4RxMailbox[30];
		data_to_float.cdata[1]  =  UA4RxMailbox[31];
    data_to_float.cdata[2]  =  UA4RxMailbox[32];
    data_to_float.cdata[3]  =  UA4RxMailbox[33];	
		if(data_to_float.data_float>-5.0f && data_to_float.data_float< 5.0f)
	  {
			U4_RX_Data = data_to_float.data_float ;
		}
		
/////////////////////////////////////////////////////////////////////////////////////////		
		data_to_float.cdata[0]  =  UA4RxMailbox[34];
		data_to_float.cdata[1]  =  UA4RxMailbox[35];
    data_to_float.cdata[2]  =  UA4RxMailbox[36];
    data_to_float.cdata[3]  =  UA4RxMailbox[37];	
		if(data_to_float.data_float>-5.0f && data_to_float.data_float< 5.0f)
	  {
			U4_RX_Data = data_to_float.data_float ;
		}
		
		data_to_float.cdata[0]  =  UA4RxMailbox[38];
		data_to_float.cdata[1]  =  UA4RxMailbox[39];
    data_to_float.cdata[2]  =  UA4RxMailbox[40];
    data_to_float.cdata[3]  =  UA4RxMailbox[41];	
		if(data_to_float.data_float>-1000000.0f && data_to_float.data_float< 1000000.0f)
	  {
		  U4_RX_Data = data_to_float.data_float ;
		}
		
		data_to_float.cdata[0]  =  UA4RxMailbox[42];
		data_to_float.cdata[1]  =  UA4RxMailbox[43];
    data_to_float.cdata[2]  =  UA4RxMailbox[44];
    data_to_float.cdata[3]  =  UA4RxMailbox[45];	
		if(data_to_float.data_float>-1000000.0f && data_to_float.data_float< 1000000.0f)
	  {
			U4_RX_Data = data_to_float.data_float ;
		}
		
		data_to_float.cdata[0]  =  UA4RxMailbox[46];
		data_to_float.cdata[1]  =  UA4RxMailbox[47];
    data_to_float.cdata[2]  =  UA4RxMailbox[48];
    data_to_float.cdata[3]  =  UA4RxMailbox[49];	
		if(data_to_float.data_float>-1000000.0f && data_to_float.data_float< 1000000.0f)
	  {
			U4_RX_Data = data_to_float.data_float ;
		}
   }
}
/***********************************************************************************
中断处理函数名称：UART5_IRQHandler
中断产生机制：洞捕摄像头通讯
函数功能：
************************************************************************************/

float x_pos = 0; // 机头像电脑，x为前后，向前减小，向后增加
float y_pos = 0;  // y为高度，向上增加  ，初始高度164--170
float z_pos = 0;  //横向，向左增加
float des_x = 0; 
float des_y = 0;
float des_z = 0;


/***********************************************************************************
中断处理函数名称：UART5_IRQHandler
中断产生机制：
函数功能：
************************************************************************************/
void UART5_IRQHandler(void)
{
  	if(USART_GetITStatus(UART5, USART_IT_IDLE)!= RESET)
	{
		Clear_IT = UART5->SR;
		Clear_IT = UART5->DR;//先读SR后读DR清楚中断标志位

		extern USART_RX_TypeDef UART5_Rcr;
		{
			uint16_t rx_len = USART_Receive(&UART5_Rcr);
			if(rx_len > 0)
			{
				extern void Handle_UART5_GroundStation_Command(void);
				Handle_UART5_GroundStation_Command();
				system_monitor.USART5_task_cnt++;
			}
		}
	}
}

void DMA1_Stream7_IRQHandler(void)  //串口5 流7
{
   if(DMA_GetITStatus(DMA1_Stream7, DMA_IT_TCIF7))
   {
      DMA_ClearFlag(DMA1_Stream7, DMA_FLAG_TCIF7);//清除标志位
    	DMA_Cmd(DMA1_Stream7, DISABLE);             //关闭DMA传输 
   }
}
////////////////////////////////////////////////////////
void Decode_RX_Data(void)
{

}
/***********************************************************************************
中断处理函数名称：UART6_IRQHandler
中断产生机制：视觉通讯
函数功能：
************************************************************************************/

void USART6_IRQHandler(void)
{
   
}
/************************ (C) COPYRIGHT STMicroelectronics *****END OF FILE****/

