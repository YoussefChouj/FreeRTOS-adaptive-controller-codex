#ifndef __STM32F4xx_IT_H
#define __STM32F4xx_IT_H

#ifdef __cplusplus
 extern "C" {
#endif 

#include "main.h"
#include "stm32f4xx.h"
#include "usart1.h"
#include "usart4.h"
#include "usart5.h"
#include "global_declare.h"
#include "tf_mini_plus.h"
#include "AutoflyTask.h"
#include "GPS.h"
void Decode_RX_Data(void);
void Decode_RX_Data_t265(void);
extern float x_pos;
extern float y_pos;
extern float z_pos;
extern float des_x ; 
extern float des_y ;
extern float des_z ;

extern float target_x_pos;
extern float target_y_pos;
extern float target_z_pos;
#ifdef __cplusplus
}
#endif

#endif 
