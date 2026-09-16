#ifndef _PWM__H_
#define _PWM__H_

#include "stm32f4xx.h"
#include "global_declare.h"
#include "GlobalUse_Basic_Function.h"

#define M1  TIM3->CCR1  //���
#define M4  TIM3->CCR2
#define M2  TIM3->CCR3
#define M3  TIM3->CCR4

#define Motor_PWM_ZERO   2000
#define Motor_PWM_IDLE   2150
#define Motor_PWM_MAX   4000

typedef struct
{
	short motor1;  //���
	short motor2;
	short motor3;
	short motor4;
}MOTORTypeDef;

extern MOTORTypeDef mymotor;


void PWM_TIM3_Init(void);
void PWM_TIM2_Init(void);
void PWM_TIM4_Init (void);
void SetPitchAngle(float angle);
void SetRollAngle(float angle);

void Set_PWM_Motors(void);  //���õ�� 
void Set_Zero_Motors(void);
void Set_IDLE_Motors(void);

void SetBeep(int B);
#endif
