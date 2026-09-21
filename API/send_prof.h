#ifndef __SEND_PROF_H__
#define __SEND_PROF_H__

#include <stdint.h>
#include "stm32f4xx.h"

/* Section profiling structure: holds last and maximum cycle count */
typedef struct {
    uint32_t last_cycles;
    uint32_t max_cycles;
} SendProfSection_t;

/* Global profiling data structure for Send_Task */
typedef struct {
    uint32_t initialized;            /* 0x00: 1 once DWT initialized */
    uint32_t period_cycles;          /* 0x04: Cycles between successive Send_Task starts (last) */
    uint32_t period_cycles_max;      /* 0x08: Max cycles between successive starts */
    uint32_t period_cycles_min;      /* 0x0C: Min cycles between successive starts */
    uint32_t total_work_cycles;      /* 0x10: Total execution cycles of Send_Task work body (last) */
    uint32_t total_work_cycles_max;  /* 0x14: Max total execution cycles of work body */

    /* Per-section profiling (8 bytes each: last_cycles, max_cycles) */
    SendProfSection_t sec_send_to_linux;  /* 0x18: send_to_linux() */
    SendProfSection_t sec_ekf;            /* 0x20: EKF step in Send_Groundstation_Telemetry_UART4 */
    SendProfSection_t sec_telem_build;    /* 0x28: Frame pack & UART5 DMA arming */
    SendProfSection_t sec_subscribe_tick; /* 0x30: Subscribe_StreamTick() */
    SendProfSection_t sec_uart5_request;  /* 0x38: Uart5_Subscribe_HandleRequest() */
    SendProfSection_t sec_process_cmd;    /* 0x40: Process_GroundStation_Command() */
    SendProfSection_t sec_usart3_send;    /* 0x48: usart3_send() */
} SendProf_t;

extern volatile SendProf_t g_send_prof;

void SendProf_Init(void);
void SendProf_TaskStart(uint32_t now);
void SendProf_TaskEnd(uint32_t start_time);
void SendProf_Record(volatile SendProfSection_t* sec, uint32_t start_time);

#endif /* __SEND_PROF_H__ */
