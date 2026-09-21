#include "send_prof.h"

volatile SendProf_t g_send_prof = {
    0U, 0U, 0U, 0xFFFFFFFFU, 0U, 0U,
    {0U, 0U}, /* sec_send_to_linux */
    {0U, 0U}, /* sec_ekf */
    {0U, 0U}, /* sec_telem_build */
    {0U, 0U}, /* sec_subscribe_tick */
    {0U, 0U}, /* sec_uart5_request */
    {0U, 0U}, /* sec_process_cmd */
    {0U, 0U}  /* sec_usart3_send */
};

void SendProf_Init(void)
{
    CoreDebug->DEMCR |= CoreDebug_DEMCR_TRCENA_Msk;
    DWT->CYCCNT = 0U;
    DWT->CTRL |= DWT_CTRL_CYCCNTENA_Msk;
    g_send_prof.initialized = 1U;
    g_send_prof.period_cycles_min = 0xFFFFFFFFU;
}

void SendProf_TaskStart(uint32_t now)
{
    static uint32_t s_last_start = 0U;
    uint32_t period;

    if (s_last_start != 0U) {
        period = now - s_last_start;
        g_send_prof.period_cycles = period;
        if (period > g_send_prof.period_cycles_max) {
            g_send_prof.period_cycles_max = period;
        }
        if (period < g_send_prof.period_cycles_min) {
            g_send_prof.period_cycles_min = period;
        }
    }
    s_last_start = now;
}

void SendProf_TaskEnd(uint32_t start_time)
{
    uint32_t total;

    total = DWT->CYCCNT - start_time;
    g_send_prof.total_work_cycles = total;
    if (total > g_send_prof.total_work_cycles_max) {
        g_send_prof.total_work_cycles_max = total;
    }
}

void SendProf_Record(volatile SendProfSection_t* sec, uint32_t start_time)
{
    uint32_t elapsed;

    elapsed = DWT->CYCCNT - start_time;
    sec->last_cycles = elapsed;
    if (elapsed > sec->max_cycles) {
        sec->max_cycles = elapsed;
    }
}
