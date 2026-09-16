#ifndef USART5_H
#define USART5_H
#include <stdint.h>
#define USART5_SUBSCRIBE_RX_LEN 256
extern uint8_t  UA5RxSubscribeBuf[USART5_SUBSCRIBE_RX_LEN];
extern uint16_t UA5RxSubscribeLen;
extern volatile uint8_t UA5RxSubscribePending;
void Uart5_Subscribe_TxSend(const uint8_t* buf, uint16_t len);
void Uart5_Subscribe_HandleRequest(void);
#endif
