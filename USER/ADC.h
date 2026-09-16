#ifndef __ADC_H__
#define __ADC_H__

#include "stm32f4xx.h"

void ADC1_Configuration(void);
uint16_t ADC_Read(void);
float Voltage_Calculation(uint16_t adc_value);

#endif
