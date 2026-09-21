#include  "BSP.h"
#include "SINS.h"
#include "ADC.h"

void IRSensors_Init(void)
{
    GPIO_InitTypeDef gpio;
    RCC_AHB1PeriphClockCmd(RCC_AHB1Periph_GPIOC, ENABLE);

    /* Landing-pad sensors PC0, PC1: pull-down (idle LOW). These two pins are the
     * ONLY free port-C pins on this board. */
    gpio.GPIO_Mode  = GPIO_Mode_IN;
    gpio.GPIO_PuPd  = GPIO_PuPd_DOWN;
    gpio.GPIO_Speed = GPIO_Speed_25MHz;
    gpio.GPIO_Pin   = GPIO_Pin_0 | GPIO_Pin_1;
    GPIO_Init(GPIOC, &gpio);

    /* DO NOT configure PC2/PC3/PC4/PC5 here. They are the BMI088 IMU's SPI2 bus
     * (spi.h): PC2=MISO, PC3=MOSI, PC4=CSB1 accel-CS, PC5=CSB2 gyro-CS. Driving
     * them as GPIO inputs tears the IMU off the SPI bus, so the attitude estimate
     * runs on garbage and the drone flips on liftoff. The per-motor reflective
     * IR sensors need four genuinely-free pins re-homed elsewhere before they can
     * be wired up. */
}

void BSP_Init(void)
{
    NVIC_PriorityGroupConfig(NVIC_PriorityGroup_4);

    LED_Init();
    RPM_Init();   /* RPM EXTI on PA0/PA1/PC6/PC7 (does NOT conflict with IMU SPI PC2-PC5 or TIM3 motors PA6/PA7/PB0/PB1) */
    PWM_TIM3_Init();

    IRSensors_Init();  /* PC0–PC5 inputs, pull-down */

    ADC1_Configuration();
    delay_ms(2000);

    SPI_Configuration();
    if (!bmi088_init()) {
        sensor.sensor_ok = 0U;
    }

    USART1_Configuration();
    USART2_Configuration();
    USART3_Configuration();
    /* 2026-07-21: UART4 disabled — PA0/PA1 are now repurposed as RPM sensor
     * inputs (RPM_CH0, RPM_CH1). The UART4 peripheral stays compiled for
     * posterity but its GPIO AF config would clobber the EXTI input mode.
     * See BSP/rpm.h for the pin re-targeting rationale. */
    /* UART4_Configuration(); */
    UART5_Configuration();
    
    ThrustEst_Init();  /* Shadow thrust estimators (empirical/blade-element/IMU) */

    BEEP_Init();
    GPIO_ResetBits(GPIOA, GPIO_Pin_11);
    GPIO_SetBits(GPIOA,   GPIO_Pin_12);
    GPIO_SetBits(GPIOC,    GPIO_Pin_8);
    delay_ms(2000);
    PWM_TIM4_Init();

    linux_yolo_data.stree_angle = 90.0f;
}
