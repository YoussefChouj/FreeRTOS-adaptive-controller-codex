/**
 * @module     send_prof.c
 * @subsystem  debug
 * @owner      Send_Task (USER/main.c): SendProf_Init once at task start, SendProf_TaskStart/TaskEnd around each loop
 *             iteration; TASK/send_data.c times its sections with SendProf_Record. Read by name from the GS
 *             (g_send_prof) and by mrac.c's note on the CYCCNT reset.
 * @purpose    Cycle-accurate Send_Task profiling on the DWT cycle counter: loop period last/min/max, work time
 *             last/max, and last/max per instrumented section.
 * @inputs     DWT->CYCCNT samples taken by the callers.
 * @outputs    g_send_prof [CPU cycles]; SendProf_Init starts CYCCNT from 0 and sets initialized = 1.
 */
#include "send_prof.h"

#define SEND_PROF_MIN_UNSET  0xFFFFFFFFU   /* period_cycles_min before the first measured period */

volatile SendProf_t g_send_prof = {
    0U, 0U, 0U, SEND_PROF_MIN_UNSET, 0U, 0U,
    {0U, 0U}, /* sec_send_to_linux */
    {0U, 0U}, /* sec_ekf */
    {0U, 0U}, /* sec_telem_build */
    {0U, 0U}, /* sec_subscribe_tick */
    {0U, 0U}, /* sec_uart5_request */
    {0U, 0U}, /* sec_process_cmd */
    {0U, 0U}  /* sec_usart3_send */
};

static uint32_t s_last_start = 0U;   /* previous SendProf_TaskStart stamp [cycles]; 0 = none yet */

void SendProf_Init(void)
{
    CoreDebug->DEMCR |= CoreDebug_DEMCR_TRCENA_Msk;
    DWT->CYCCNT = 0U;
    DWT->CTRL |= DWT_CTRL_CYCCNTENA_Msk;
    g_send_prof.initialized = 1U;
    g_send_prof.period_cycles_min = SEND_PROF_MIN_UNSET;
}

/* Loop period from consecutive start stamps; the first call (s_last_start still 0) only records the stamp. */
void SendProf_TaskStart(uint32_t now)
{
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
