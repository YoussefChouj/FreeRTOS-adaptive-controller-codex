/**
 * @module     time_estimate.c
 * @subsystem  debug
 * @owner      none: nothing calls TIM5_Configuration and no file uses the Time_Estimate* macros (git grep, WP-41).
 * @purpose    Opt-in profiling timebase: TIM5 as a free-running 1 MHz, 32-bit up-counter that the Time_Estimate*
 *             macros in time_estimate.h read to print how long a call took. Call TIM5_Configuration once from
 *             main.c before using the macros; without it TIM5->CNT stays 0.
 * @inputs     none.
 * @outputs    TIM5->CNT [us], wrapping every 2^32 us (~71.6 min).
 */
#include "time_estimate.h"

#define TIM5_PRESCALER  (84U - 1U)     /* 84 MHz APB1 timer clock / 84 = 1 MHz -> 1 count per us */
#define TIM5_PERIOD     0xFFFFFFFFU    /* full 32-bit range: free-running                        */

void TIM5_Configuration(void)
{
    TIM_TimeBaseInitTypeDef TIM_TimeBaseStructure;

    RCC_APB1PeriphClockCmd(RCC_APB1Periph_TIM5, ENABLE);

    TIM_TimeBaseStructure.TIM_Prescaler     = TIM5_PRESCALER;
    TIM_TimeBaseStructure.TIM_Period        = TIM5_PERIOD;
    TIM_TimeBaseStructure.TIM_CounterMode   = TIM_CounterMode_Up;
    TIM_TimeBaseStructure.TIM_ClockDivision = TIM_CKD_DIV1;

    TIM_TimeBaseInit(TIM5, &TIM_TimeBaseStructure);

    TIM_Cmd(TIM5, ENABLE);
}
