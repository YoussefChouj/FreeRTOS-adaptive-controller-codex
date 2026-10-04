#ifndef FW_HEALTH_H
#define FW_HEALTH_H

/* Firmware health (WP-40): reset cause, the independent watchdog and the RTOS budget. The helpers here are pure
 * (host-tested by tests/firmware_host/test_fw_health.c); the target glue FwHealth_Tick() lives in
 * TASK/systemmonitor_task.c and runs once a second at the end of SystemErrorDetect(). Contract:
 * docs/firmware-safety.md. */

#include <stdint.h>

/* RCC->CSR[31:25] captured by main() before RCC_ClearFlag(), shifted down to bits 0..6. */
#define FW_RESET_BOR   0x01U
#define FW_RESET_PIN   0x02U
#define FW_RESET_POR   0x04U
#define FW_RESET_SFT   0x08U
#define FW_RESET_IWDG  0x10U
#define FW_RESET_WWDG  0x20U
#define FW_RESET_LPWR  0x40U

#define FW_HEALTH_NONE 0xFFU        /* FwHealth_MinIndex() of an empty list */

typedef struct {
    uint8_t  reset_cause;           /* FW_RESET_* bits of the last reset */
    uint8_t  iwdg_on;               /* 1 once the IWDG is running (it cannot be stopped until the next reset) */
    uint8_t  alive;                 /* 1 when every critical task ran at least once in the last second */
    uint8_t  stack_min_task;        /* xTaskNumber of the task with the least stack left, FW_HEALTH_NONE if unknown */
    uint16_t stack_min_words;       /* that task's usStackHighWaterMark, words (4 bytes) */
    float    stab_cpu_pct;          /* Stabilizer_Task share of the CPU over the last snapshot interval, % */
    float    loop_max_us;           /* longest stabilizer loop period since the last g_loop_stats_reset, us */
} fw_rtos_budget_t;

extern volatile fw_rtos_budget_t g_rtos_budget;

uint8_t  FwHealth_IwdgEnabled(void);                              /* table value, 0 or 1 */
uint32_t FwHealth_IwdgTimeoutMs(void);                            /* table value, ms */
uint16_t FwHealth_IwdgReload(uint32_t timeout_ms);                /* reload for prescaler 64 at LSI 32 kHz, 1..4095 */
uint8_t  FwHealth_ResetCause(uint32_t rcc_csr);                   /* RCC->CSR -> FW_RESET_* bits */
uint8_t  FwHealth_Alive(const uint16_t *fps, uint8_t n);          /* 1 when n > 0 and every fps[i] > 0 */
uint8_t  FwHealth_MinIndex(const uint16_t *v, uint8_t n);         /* index of the smallest, first wins */
float    FwHealth_Pct(uint32_t part, uint32_t total);             /* 100 * part / total, 0 when total is 0 */
void     FwHealth_Tick(void);                                     /* target glue, 1 Hz */

#endif /* FW_HEALTH_H */
