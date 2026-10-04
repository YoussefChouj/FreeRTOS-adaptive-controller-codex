#include "fw_health.h"
#include <stddef.h>

/* Independent watchdog configuration.
   @iwdg_enable      -   [0, 1]       1 starts the IWDG at the first second every critical task is alive
   @iwdg_timeout_ms  ms  [500, 8000]  nominal timeout at LSI 32 kHz; LSI spans 17..47 kHz, so 2500 is 1.70..4.70 s
   2026-10-04 WP-40: PROPOSED, disabled. Once started the IWDG cannot be stopped: a stalled task in flight resets
   the board and the motors stop. Enable only after a bench soak shows alive == 1 for every second. */
#define FW_HEALTH_ROW(iwdg_enable, iwdg_timeout_ms)     { (iwdg_enable), (iwdg_timeout_ms) }

typedef struct {
    uint8_t  iwdg_enable;
    uint32_t iwdg_timeout_ms;
} fw_health_cfg_t;

static const fw_health_cfg_t s_cfg =
/*              enable timeout_ms */
    FW_HEALTH_ROW(0,     2500);  /* PROPOSED */

volatile fw_rtos_budget_t g_rtos_budget = { 0U, 0U, 0U, FW_HEALTH_NONE, 0U, 0.0f, 0.0f };

uint8_t FwHealth_IwdgEnabled(void)
{
    return s_cfg.iwdg_enable;
}

uint32_t FwHealth_IwdgTimeoutMs(void)
{
    return s_cfg.iwdg_timeout_ms;
}

/* Prescaler 64 at 32 kHz is 500 Hz, 2 ms per count. */
uint16_t FwHealth_IwdgReload(uint32_t timeout_ms)
{
    uint32_t reload = timeout_ms / 2U;

    if (reload < 1U)    { reload = 1U; }
    if (reload > 4095U) { reload = 4095U; }
    return (uint16_t)reload;
}

uint8_t FwHealth_ResetCause(uint32_t rcc_csr)
{
    return (uint8_t)((rcc_csr >> 25) & 0x7FU);
}

uint8_t FwHealth_Alive(const uint16_t *fps, uint8_t n)
{
    uint8_t i;

    if (fps == NULL || n == 0U) {
        return 0U;
    }
    for (i = 0U; i < n; i++) {
        if (fps[i] == 0U) {
            return 0U;
        }
    }
    return 1U;
}

uint8_t FwHealth_MinIndex(const uint16_t *v, uint8_t n)
{
    uint8_t i;
    uint8_t best = FW_HEALTH_NONE;

    if (v == NULL) {
        return FW_HEALTH_NONE;
    }
    for (i = 0U; i < n; i++) {
        if (best == FW_HEALTH_NONE || v[i] < v[best]) {
            best = i;
        }
    }
    return best;
}

float FwHealth_Pct(uint32_t part, uint32_t total)
{
    if (total == 0U) {
        return 0.0f;
    }
    return 100.0f * (float)part / (float)total;
}
