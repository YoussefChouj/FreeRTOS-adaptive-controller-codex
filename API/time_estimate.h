#ifndef  __TIMER_H__
#define __TIMER_H__
#include "stm32f4xx.h"

/* Opt-in profiling on the TIM5 1 MHz counter (call TIM5_Configuration first; see time_estimate.c).
   Time one call, e.g. Time_EstimateFunction(delay_ms(1000)); prints "<call> Runtime = x ms". */
#define  Time_EstimateFunction(function)    do{\
       u32 cnt = TIM5->CNT; \
      function;  \
       printf("%s Runtime = %.3f  ms\r\n", #function, (TIM5->CNT - cnt)/1000.0);\
}while(0)

/* Time a span across statements: Time_EstimateCreaterVar(X) at file scope defines u32 TimeEstimateX
   (## pastes the name, # stringifies it), then Time_EstimateStart(X) ... Time_EstimateEnd(X) prints the span. */
#define Time_EstimateCreaterVar(VarName) \
u32 TimeEstimate##VarName
#define Time_EstimateStart(VarName)   do{ \
       extern u32  TimeEstimate##VarName; \
      TimeEstimate##VarName = TIM5->CNT; \
}while(0)
#define Time_EstimateEnd(VarName)   do{ \
       extern u32  TimeEstimate##VarName; \
      TimeEstimate##VarName = TIM5->CNT - TimeEstimate##VarName; \
      printf("%s Runtime = %.3f  ms\r\n", #VarName,TimeEstimate##VarName/1000.0);\
}while(0)

void TIM5_Configuration(void);
#endif
