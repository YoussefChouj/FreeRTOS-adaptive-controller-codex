#include "ADC.h"

void ADC1_Configuration(void)      //ADC����Դ��ѹ
{
	//GPIOA  ADCʱ��
	RCC_AHB1PeriphClockCmd(RCC_AHB1Periph_GPIOA, ENABLE);
	RCC_APB2PeriphClockCmd(RCC_APB2Periph_ADC1, ENABLE);
	
  //PA4ģ������
	GPIO_InitTypeDef GPIO_InitStruct;
	GPIO_InitStruct.GPIO_Pin = GPIO_Pin_4;
	GPIO_InitStruct.GPIO_Mode = GPIO_Mode_AN;
	GPIO_InitStruct.GPIO_PuPd = GPIO_PuPd_NOPULL;
	GPIO_Init(GPIOA, &GPIO_InitStruct);
	
	// ADC common (clock) config — REQUIRED. Without it ADC_CCR stays at reset (ADCPRE=PCLK2/2).
	// PCLK2=84MHz here, so /2 = 42MHz exceeds the STM32F407 36MHz ADC max -> conversions return 0.
	// Div4 -> 84/4 = 21MHz, within spec.
	ADC_CommonInitTypeDef ADC_CommonInitStruct;
	ADC_CommonStructInit(&ADC_CommonInitStruct);
	ADC_CommonInitStruct.ADC_Mode = ADC_Mode_Independent;
	ADC_CommonInitStruct.ADC_Prescaler = ADC_Prescaler_Div4;
	ADC_CommonInitStruct.ADC_DMAAccessMode = ADC_DMAAccessMode_Disabled;
	ADC_CommonInitStruct.ADC_TwoSamplingDelay = ADC_TwoSamplingDelay_5Cycles;
	ADC_CommonInit(&ADC_CommonInitStruct);

	// SET ADC
	ADC_InitTypeDef ADC_InitStruct;
	ADC_StructInit(&ADC_InitStruct);
	ADC_InitStruct.ADC_Resolution = ADC_Resolution_12b;  // 12λ�ֱ���
	ADC_InitStruct.ADC_ScanConvMode = DISABLE;           // ��ͨ��ת��
	ADC_InitStruct.ADC_ContinuousConvMode = DISABLE;     // ����ת��
	ADC_InitStruct.ADC_ExternalTrigConv = ADC_ExternalTrigConv_T1_CC1; // �ⲿ����
	ADC_Init(ADC1, &ADC_InitStruct);

  //ADC ͨ�� — 480-cycle sample time: battery divider is a slow DC source with non-trivial
  //          impedance; 3 cycles was far too short for the S/H cap to settle.
	ADC_RegularChannelConfig(ADC1, ADC_Channel_4, 1, ADC_SampleTime_480Cycles);

	//ʹ��
	ADC_Cmd(ADC1, ENABLE);
	
//	ADC_ResetCalibration(ADC1);
//	while(ADC_GetResetCalibrationStatus(ADC1));
//	ADC_StartCalibration(ADC1);
//	while(ADC_GetCalibrationStatus(ADC1));
}

uint16_t ADC_Read(void)
{
  //����ת��
	ADC_SoftwareStartConv(ADC1);
	//�ȴ�ת����� — bounded: a mis-clocked/stuck ADC must never hang the caller (SystemMonitor_Task).
	//If EOC never sets, bail out and report 0 (degraded) instead of spinning forever.
	{
		uint32_t eoc_timeout = 100000u;
		while(!ADC_GetFlagStatus(ADC1, ADC_FLAG_EOC))
		{
			if(--eoc_timeout == 0u) return 0;
		}
	}
  //��ȡADCֵ
	uint16_t value = ADC_GetConversionValue(ADC1);
  ADC_ClearFlag(ADC1, ADC_FLAG_EOC);
  return value;
}

float Voltage_Calculation(uint16_t adc_value) 
{
  float voltage = adc_value * (3.3f / 4095);  
  return voltage;
}
