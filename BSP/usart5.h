#ifndef  __USART5_H__
#define  __USART5_H__

#include "stm32f4xx.h"
#include "main.h"

/*����5ͨ�Ż��峤��*/
#define UART5_RX_STREAM         DMA1_Stream0
#define UART5_TX_STREAM         DMA1_Stream7
#define USART5_RXDMA_LEN           256
#define USART5_RXMB_LEN            256
#define USART5_SUBSCRIBE_RX_LEN    256

void UART5_Configuration(void);
void Handle_UART5_GroundStation_Command(void);
/* Transport-agnostic GS command-frame parser entry point. Implemented in
 * BSP/usart5.c; exposed so USART3_IRQHandler (TASK/stm32f4xx_it.c) can run
 * the same 0xCC 0xDD grammar over its own IDLE-coalesced mailbox bytes
 * (UA3RxMailbox) without duplicating the parser. 0xCC 0xDE subscribe requests
 * are not accepted on USART3 -- they have no reply DMA wired there -- so the
 * parser advances past any 0xCC 0xDE byte sequence silently and continues. */
void Handle_USART3_GroundStation_Command(const uint8_t* mailbox, uint16_t total);
/* UART5 extended-prefix subscription handler. Called from Send_Task in
 * TASK/send_data.c AFTER the live telemetry DMA completes. Reads from
 * UA5RxSubscribeBuf (already CRC-validated by the IRQ-side parser), builds a
 * 0x07 / 0x7F reply into a file-scope static in API/subscribe.c, and arms a
 * second DMA1_Stream7 turn via Uart5_Subscribe_TxSend. Implementation in
 * API/subscribe.c. */
#if SUBSCRIBE_UART5_ENABLED
void Uart5_Subscribe_HandleRequest(void);
#else
/* Stub when UART5 subscribe is disabled: the staging buffer is owned by the
 * USART3 parser (BSP/usart3.c) so no UART5-side handler is needed. */
void Uart5_Subscribe_HandleRequest(void);
#endif
/* Defined in API/subscribe.c; written by the UART5 parser (ParseGsCommandFrames)
 * so the USART3 parser (Handle_USART3_GroundStation_Command) can set it too. */
extern volatile uint8_t Subscribe_RxTransport;
/* DMA1_Stream7 hand-off for the 0x07 / 0x7F reply. Called from API/subscribe.c
 * via the reply-builder path. Same DMA channel as the live telemetry; the
 * caller is responsible for ensuring the live telemetry DMA has completed
 * (the existing `while (DMA_GetCurrDataCounter(DMA1_Stream7));` pattern). */
void Uart5_Subscribe_TxSend(const uint8_t* buf, uint16_t len);
extern UCHAR8 UA5RxDMAbuf[USART5_RXDMA_LEN] ;
extern UCHAR8 UA5RxMailbox[USART5_RXMB_LEN] ;
extern UCHAR8 UA5RxSubscribeBuf[USART5_SUBSCRIBE_RX_LEN];
extern uint16_t UA5RxSubscribeLen;
extern volatile uint8_t UA5RxSubscribePending;
extern USART_RX_TypeDef UART5_Rcr;
#endif
