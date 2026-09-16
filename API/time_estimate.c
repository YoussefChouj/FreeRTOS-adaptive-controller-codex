#include "time_estimate.h"

#define TIM5_Prescaler  84-1
#define TIM5_Period     4294967296-1   


void TIM5_Configuration(void)
{
    TIM_TimeBaseInitTypeDef TIM_TimeBaseStructure;
    
    RCC_APB1PeriphClockCmd(RCC_APB1Periph_TIM5, ENABLE);

    TIM_TimeBaseStructure.TIM_Prescaler     =	 TIM5_Prescaler;
	  TIM_TimeBaseStructure.TIM_Period        =  TIM5_Period;
	  TIM_TimeBaseStructure.TIM_CounterMode   =  TIM_CounterMode_Up;
	  TIM_TimeBaseStructure.TIM_ClockDivision =  TIM_CKD_DIV1;

    TIM_TimeBaseInit(TIM5, &TIM_TimeBaseStructure)	;

    TIM_Cmd(TIM5,ENABLE);
}

