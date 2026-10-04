/**
 * @module     stm32f4xx_it.c
 * @subsystem  bsp
 * @owner      the vector table in stm32_lib/startup_stm32f40_41xxx.s; every handler here runs in interrupt context.
 * @purpose    Interrupt handlers for the serial links (USART1 SBUS, USART2 optical flow, USART3 radio, UART4 onboard
 *             computer, UART5 ground station), the TX-complete DMA streams and the RPM edge inputs, plus
 *             USART_Receive, the DMA-ring-to-mailbox copier shared by the IDLE-line handlers.
 * @inputs     USART data/status registers, the RX DMA rings (USART3_Rcr, UART4_Rcr, UART5_Rcr), EXTI lines 0, 1, 5-9.
 * @outputs    SBUS and optical-flow byte parsers, the GS command handlers, RPM_EdgeISR, system_monitor.*_task_cnt,
 *             UA3RxFrameCnt / UA3RxLastLen, U4_RX_Data.
 */

#include "stm32f4xx_it.h"
#include "rpm.h"

/* ====================================================================
 * DEBUG-telemetry-bisect: fault reporting over UART5 (polling, no DMA/RTOS).
 * Kept for future use - flip to `#if 1` to override the silent startup-file
 * HardFault_Handler so a fault ANNOUNCES itself on COM6 instead of looking like
 * a dead link. Format: "HARDFAULT PSP_PC=... PSP_LR=... MSP_PC=..." - look up
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

/* ------------------------------------------------------------------
 * Private constants
 * ------------------------------------------------------------------ */

#define U4_T265_SYNC   0xAA         /* both of the first two bytes of a T265 frame on UART4    */
#define U4_WIDE_LIM    1000000.0f   /* accept band (-lim, lim) for fields 1-6 and 10-12        */
#define U4_NARROW_LIM  5.0f         /* accept band (-lim, lim) for fields 7-9                  */
#define U4_SCALE       100.0f       /* factor applied to fields 1-6                            */

/* Assemble the float that starts at mailbox byte k in data_to_float (byte order as received). */
#define U4_LOAD_FLOAT(k)                                   \
    do {                                                   \
        data_to_float.cdata[0] = UA4RxMailbox[(k)];        \
        data_to_float.cdata[1] = UA4RxMailbox[(k) + 1];    \
        data_to_float.cdata[2] = UA4RxMailbox[(k) + 2];    \
        data_to_float.cdata[3] = UA4RxMailbox[(k) + 3];    \
    } while (0)

/* The float just loaded lies strictly inside (-lim, lim). */
#define U4_IN_BAND(lim)  (data_to_float.data_float > -(lim) && data_to_float.data_float < (lim))

/* ------------------------------------------------------------------
 * External symbols (BSP serial drivers and the GS command layer)
 * ------------------------------------------------------------------ */

extern USART_RX_TypeDef USART3_Rcr;
extern USART_RX_TypeDef UART4_Rcr;
extern USART_RX_TypeDef UART5_Rcr;
extern volatile uint32_t UA3RxFrameCnt;
extern volatile uint16_t UA3RxLastLen;
extern void Handle_USART3_GroundStation_Command(const uint8_t*, USHORT16);
extern void Handle_UART4_GroundStation_Command(void);
extern void Handle_UART5_GroundStation_Command(void);
extern void Usart3_Tx_DmaIsr(void);

USHORT16 USART_Receive(USART_RX_TypeDef* USARTx);

/* ------------------------------------------------------------------
 * Public state  (watchable by symbol through the capability manifest)
 * ------------------------------------------------------------------ */

USHORT16 Clear_IT = 0;   /* sink for the SR-then-DR reads that clear an IDLE flag */

/* ------------------------------------------------------------------
 * Interrupt handlers: SBUS, optical flow, radio, TX DMA, RPM
 * ------------------------------------------------------------------ */

/* USART1: SBUS receiver, one byte per interrupt. */
void USART1_IRQHandler(void)
{
	u8 com_data;

	if (USART_GetITStatus(USART1, USART_IT_RXNE))
	{
		USART_ClearITPendingBit(USART1, USART_IT_RXNE);
		com_data = USART1->DR;
		DrvSbusGetOneByte(com_data);

		system_monitor.USART1_task_cnt++;
	}
}

/* USART2: optical-flow module, one byte per interrupt. */
void USART2_IRQHandler(void)
{
	u8 com_data;

	if (USART2->SR & USART_SR_ORE)   /* overrun: reading DR after SR clears it */
		com_data = USART2->DR;
	if (USART_GetITStatus(USART2, USART_IT_RXNE))
	{
		USART_ClearITPendingBit(USART2, USART_IT_RXNE);

		com_data = USART2->DR;
		AnoOF_GetOneByte(com_data);   /* optical-flow frame parser */
		system_monitor.USART2_task_cnt++;
	}
}

/* DMA1 stream 6: USART2 TX complete, park the stream. */
void DMA1_Stream6_IRQHandler(void)
{
	if (DMA_GetITStatus(DMA1_Stream6, DMA_IT_TCIF6))
	{
		DMA_ClearFlag(DMA1_Stream6, DMA_FLAG_TCIF6);
		DMA_Cmd(DMA1_Stream6, DISABLE);
	}
}

/* USART3: radio link, IDLE line marks the end of a burst. */
void USART3_IRQHandler(void)
{
	if (USART_GetITStatus(USART3, USART_IT_IDLE) != RESET)
	{
		Clear_IT = USART3->SR;
		Clear_IT = USART3->DR;   /* reading SR then DR clears the IDLE flag */

		/* Drain the RX DMA ring into UA3RxMailbox (was: drained and counted,
		 * never parsed). USART3 is now a command ingress: dispatch the bytes
		 * through the same 0xCC 0xDD parser UART5 uses, so the radio link can
		 * carry every dashboard command (CMD 0x01..0x18). Mirrors the UART5
		 * path below. UA3RxFrameCnt / UA3RxLastLen stay as livewatch-visible
		 * proof the radio downlink is alive when driven in full duplex. */
		{
			uint16_t rx_len = USART_Receive(&USART3_Rcr);
			if (rx_len > 0)
			{
				UA3RxLastLen = rx_len;
				UA3RxFrameCnt++;
				Handle_USART3_GroundStation_Command(UA3RxMailbox, (USHORT16)USART3_Rcr.rxSize);
			}
		}
	}
}

/* DMA1 stream 3: USART3 TX complete. Must stay installed: without it the system hangs. */
void DMA1_Stream3_IRQHandler(void)
{
	/* Body moved into BSP/usart3.c so the ring's tail pointer stays private to
	 * the driver. It no longer just parks the stream: it advances the ring and
	 * immediately arms the next chunk, which is what keeps USART3 transmitting
	 * back to back instead of one frame per Send_Task tick. Calls no FreeRTOS
	 * API, so running at preemption priority 0 is safe. */
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

/* ------------------------------------------------------------------
 * Public API: DMA ring to mailbox
 * ------------------------------------------------------------------ */

/* Copy the bytes the RX DMA wrote since the last call into the mailbox and return how many there were (0 when
   nothing new arrived). The copy is skipped, but the count still returned, when the burst exceeds MbLen. */
USHORT16 USART_Receive(USART_RX_TypeDef* USARTx)
{
	USARTx->rxConter = USARTx->DMALen - DMA_GetCurrDataCounter(USARTx->DMAy_Streamx);   /* where the DMA is now */

	USARTx->rxBufferPtr += USARTx->rxSize;   /* where the previous burst ended */

	if (USARTx->rxBufferPtr >= USARTx->DMALen)   /* the ring wrapped */
	{
		USARTx->rxBufferPtr %= USARTx->DMALen;
	}

	if (USARTx->rxBufferPtr == USARTx->rxConter)
	{
		USARTx->rxSize = 0;
		return 0U;
	}

	if (USARTx->rxBufferPtr < USARTx->rxConter)
	{
		USARTx->rxSize = USARTx->rxConter - USARTx->rxBufferPtr;
		if (USARTx->rxSize <= USARTx->MbLen)
		{
			for (u16 i = 0; i < USARTx->rxSize; i++)  *(USARTx->pMailbox + i) = *(USARTx->pDMAbuf + USARTx->rxBufferPtr + i);
		}
	}
	else   /* the burst straddles the end of the ring */
	{
		USARTx->rxSize = USARTx->rxConter + USARTx->DMALen - USARTx->rxBufferPtr;
		if (USARTx->rxSize <= USARTx->MbLen)   /* only copy what fits the mailbox */
		{
			for (u16 i = 0; i < USARTx->rxSize - USARTx->rxConter; i++) *(USARTx->pMailbox + i) = *(USARTx->pDMAbuf + USARTx->rxBufferPtr + i);
			for (u16 i = 0; i < USARTx->rxConter; i++) *(USARTx->pMailbox + USARTx->rxSize - USARTx->rxConter + i) = *(USARTx->pDMAbuf + i);
		}
	}
	return USARTx->rxSize;
}

/* ------------------------------------------------------------------
 * Interrupt handlers: onboard computer (UART4)
 * ------------------------------------------------------------------ */

/* UART4: onboard computer, IDLE line marks the end of a frame. */
void UART4_IRQHandler(void)
{
	if (USART_GetITStatus(UART4, USART_IT_IDLE) != RESET)
	{
		Clear_IT = UART4->SR;
		Clear_IT = UART4->DR;   /* reading SR then DR clears the IDLE flag */

		uint16_t rx_len = USART_Receive(&UART4_Rcr);
		if (rx_len > 0)
		{
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
} data_to_float;

float U4_RX_Data = 0;

/* Legacy T265 frame: two U4_T265_SYNC bytes, then 12 floats. Every field lands in U4_RX_Data, so after a frame it
   holds the last field that passed its accept band; the per-field stores were removed earlier. */
void Decode_RX_Data_t265(void)
{
	if (UA4RxMailbox[0] == U4_T265_SYNC && UA4RxMailbox[1] == U4_T265_SYNC)
	{
		U4_LOAD_FLOAT(2);   if (U4_IN_BAND(U4_WIDE_LIM))   U4_RX_Data = data_to_float.data_float * U4_SCALE;
		U4_LOAD_FLOAT(6);   if (U4_IN_BAND(U4_WIDE_LIM))   U4_RX_Data = data_to_float.data_float * U4_SCALE;
		U4_LOAD_FLOAT(10);  if (U4_IN_BAND(U4_WIDE_LIM))   U4_RX_Data = data_to_float.data_float * U4_SCALE;
		U4_LOAD_FLOAT(14);  if (U4_IN_BAND(U4_WIDE_LIM))   U4_RX_Data = data_to_float.data_float * U4_SCALE;
		U4_LOAD_FLOAT(18);  if (U4_IN_BAND(U4_WIDE_LIM))   U4_RX_Data = data_to_float.data_float * U4_SCALE;
		U4_LOAD_FLOAT(22);  if (U4_IN_BAND(U4_WIDE_LIM))   U4_RX_Data = data_to_float.data_float * U4_SCALE;
		U4_LOAD_FLOAT(26);  if (U4_IN_BAND(U4_NARROW_LIM)) U4_RX_Data = data_to_float.data_float;
		U4_LOAD_FLOAT(30);  if (U4_IN_BAND(U4_NARROW_LIM)) U4_RX_Data = data_to_float.data_float;
		U4_LOAD_FLOAT(34);  if (U4_IN_BAND(U4_NARROW_LIM)) U4_RX_Data = data_to_float.data_float;
		U4_LOAD_FLOAT(38);  if (U4_IN_BAND(U4_WIDE_LIM))   U4_RX_Data = data_to_float.data_float;
		U4_LOAD_FLOAT(42);  if (U4_IN_BAND(U4_WIDE_LIM))   U4_RX_Data = data_to_float.data_float;
		U4_LOAD_FLOAT(46);  if (U4_IN_BAND(U4_WIDE_LIM))   U4_RX_Data = data_to_float.data_float;
	}
}

/* ------------------------------------------------------------------
 * Interrupt handlers: ground station (UART5), TX DMA, unused USART6
 * ------------------------------------------------------------------ */

/* Legacy motion-capture fields. Nothing in the firmware writes them now; they stay because the capability manifest
   lists them. Axes as the old capture setup defined them: */
float x_pos = 0;   /* nose toward the PC: fore-aft, decreasing forward */
float y_pos = 0;   /* height, increasing upward */
float z_pos = 0;   /* lateral, increasing to the left */
float des_x = 0;
float des_y = 0;
float des_z = 0;

/* UART5: ground-station / debug link, IDLE line marks the end of a frame. */
void UART5_IRQHandler(void)
{
	if (USART_GetITStatus(UART5, USART_IT_IDLE) != RESET)
	{
		Clear_IT = UART5->SR;
		Clear_IT = UART5->DR;   /* reading SR then DR clears the IDLE flag */

		{
			uint16_t rx_len = USART_Receive(&UART5_Rcr);
			if (rx_len > 0)
			{
				Handle_UART5_GroundStation_Command();
				system_monitor.USART5_task_cnt++;
			}
		}
	}
}

/* DMA1 stream 7: UART5 TX complete, park the stream. */
void DMA1_Stream7_IRQHandler(void)
{
	if (DMA_GetITStatus(DMA1_Stream7, DMA_IT_TCIF7))
	{
		DMA_ClearFlag(DMA1_Stream7, DMA_FLAG_TCIF7);
		DMA_Cmd(DMA1_Stream7, DISABLE);
	}
}

/* USART6 is not used (PC6/PC7 are RPM inputs). The empty handler replaces the startup file's spin-forever default,
   so a stray USART6 interrupt returns instead of hanging. */
void USART6_IRQHandler(void)
{

}
/************************ (C) COPYRIGHT STMicroelectronics *****END OF FILE****/
