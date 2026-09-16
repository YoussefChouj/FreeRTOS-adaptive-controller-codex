#ifndef  __TIMER_H__
#define __TIMER_H__

#include "stm32f4xx.h"


//时间评估宏函数， 例如要计算delay_ma(1000)花费多长时间，写作"Time_EstimateFunction(delay_ms(1000));"
#define  Time_EstimateFunction(function)    do{\
       u32 cnt = TIM5->CNT; \
      function;  \
       printf("%s Runtime = %.3f  ms\r\n", #function, (TIM5->CNT - cnt)/1000.0);\
}while(0)

//注意宏函数的写法，思考#与##的区别是什么？
#define Time_EstimateCreaterVar(VarName) \
u32 TimeEstimate##VarName

#define Time_EstimateStart(VarName)   do{ \
       extern u32  TimeEstimate##VarName; \
      TimeEstimate##VarName = TIM5->CNT; \
}while(0)

//extern的作用于位置？
#define Time_EstimateEnd(VarName)   do{ \
       extern u32  TimeEstimate##VarName; \
      TimeEstimate##VarName = TIM5->CNT - TimeEstimate##VarName; \
      printf("%s Runtime = %.3f  ms\r\n", #VarName,TimeEstimate##VarName/1000.0);\
}while(0)

void TIM5_Configuration(void);


#endif
