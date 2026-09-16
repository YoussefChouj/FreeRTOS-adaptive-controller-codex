#ifndef  __USART4_H__
#define  __USART4_H__

#include "stm32f4xx.h"
#include "main.h"

/*����4ͨ�Ż��峤��*/
#define UART4_RX_STREAM         DMA1_Stream2
#define UART4_TX_STREAM         DMA1_Stream4
#define UART4_RXDMA_LEN          128
#define UART4_RXMB_LEN           128

void UART4_Configuration(void);
void Handle_UART4_GroundStation_Command(void);
void Process_GroundStation_Command_Task(void);

// Command parser globals
extern volatile uint8_t gstation_cmd_ready;
extern volatile uint8_t gstation_cmd_id;
extern volatile uint8_t gstation_cmd_index;
extern volatile float gstation_cmd_value;


extern UCHAR8 UA4RxDMAbuf[UART4_RXDMA_LEN] ;
extern UCHAR8 UA4RxMailbox[UART4_RXMB_LEN] ;
extern USART_RX_TypeDef UART4_Rcr;

extern yolo_data linux_yolo_data;
extern _linux_data_st linux_data;
#endif
