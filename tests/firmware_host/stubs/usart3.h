#ifndef USART3_H
#define USART3_H
#include <stdint.h>
#define USART3_BAUD 921600   /* keep in sync with BSP/usart3.h */
uint8_t Usart3_Stream_Busy(void);
uint8_t Usart3_Stream_TxSend(const uint8_t* buf, uint16_t len);
#endif
