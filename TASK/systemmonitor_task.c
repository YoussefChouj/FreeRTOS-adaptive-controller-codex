/**
 * @module     systemmonitor_task.c
 * @subsystem  health
 * @owner      SystemMonitor_Task (USER/main.c) calls SystemErrorDetect() once a second; FlightFSM_Event() calls
 *             PreArm_Refresh() on every ARM_REQUEST.
 * @purpose    Latch the per-task loop counters into *_fps, show the worst fault on the status LED, then run the
 *             WP-40 firmware health tick (watchdog, RTOS budget, pre-arm refresh; contract in docs/firmware-safety.md).
 * @inputs     system_monitor.*_cnt, Ctrler.Z_posPID.FB, linux_data.t265pos*, the task snapshot from USER/main.c,
 *             IMU/RC/battery/WFB state for the pre-arm checks.
 * @outputs    system_monitor.*_fps, status LED pins (PA11, PA12, PC8), g_rtos_budget, the pre-arm verdict, IWDG.
 */

#include "systemmonitor_task.h"
#include "prearm.h"
#include "fw_health.h"
#include "wfb_glue.h"

/* ------------------------------------------------------------------
 * Private constants
 * ------------------------------------------------------------------ */

#define SYSMON_LINK_MIN_FPS  10   /* USART4 (onboard computer) frames per second below which the link is faulty */

/* Copy one task's loop counter into its per-second rate and restart the count. */
#define SYSMON_LATCH(task)                                         \
    do {                                                           \
        system_monitor.task##_fps = system_monitor.task##_cnt;     \
        system_monitor.task##_cnt = 0;                             \
    } while (0)

/* ------------------------------------------------------------------
 * Public API
 * ------------------------------------------------------------------ */

/* Status LED, worst fault first (pin levels as written PA11 / PA12 / PC8):
     no height feedback (Z_posPID.FB == 0)   set   / set   / reset   "red"
     onboard-computer link below 10 fps      reset / reset / reset   "cyan"
     T265 position all zero                  set   / reset / set     "blue"
     everything healthy                      reset / set   / set     "green"  */
void SystemErrorDetect(void)
{
	SYSMON_LATCH(IMUSampleTask);
	SYSMON_LATCH(IMUUpdateTask);
	SYSMON_LATCH(stabilizerTask);
	SYSMON_LATCH(remoter_task);
	SYSMON_LATCH(USART1_task);
	SYSMON_LATCH(USART2_task);
	SYSMON_LATCH(USART4_task);
	SYSMON_LATCH(USART5_task);
	SYSMON_LATCH(AutoflyTask);

	if (Ctrler.Z_posPID.FB == 0)   /* no height feedback is the worst case */
	{
		GPIO_SetBits(GPIOA, GPIO_Pin_11);
		GPIO_SetBits(GPIOA, GPIO_Pin_12);
		GPIO_ResetBits(GPIOC, GPIO_Pin_8);
	}
	else if (system_monitor.USART4_task_fps < SYSMON_LINK_MIN_FPS)   /* onboard-computer link faulty */
	{
		GPIO_ResetBits(GPIOA, GPIO_Pin_11);
		GPIO_ResetBits(GPIOA, GPIO_Pin_12);
		GPIO_ResetBits(GPIOC, GPIO_Pin_8);
	}
	else if (linux_data.t265posy == 0 && linux_data.t265posx == 0)   /* T265 position missing */
	{
		GPIO_SetBits(GPIOA, GPIO_Pin_11);
		GPIO_ResetBits(GPIOA, GPIO_Pin_12);
		GPIO_SetBits(GPIOC, GPIO_Pin_8);
	}
	else   /* healthy */
	{
		GPIO_ResetBits(GPIOA, GPIO_Pin_11);
		GPIO_SetBits(GPIOA, GPIO_Pin_12);
		GPIO_SetBits(GPIOC, GPIO_Pin_8);
	}
	FwHealth_Tick();   /* WP-40: watchdog, RTOS budget, pre-arm refresh */
}

/* ---- WP-40 firmware health and pre-arm glue (ASCII; contract in docs/firmware-safety.md) ----
 * FwHealth_Tick() runs at 1 Hz at the end of SystemErrorDetect(). SystemMonitor_Task (USER/main.c) takes the task
 * snapshot after SystemErrorDetect(), so the stack and CPU numbers here describe the previous second. */

/* Defined in USER/main.c and Global_file/creat_task.h. */
extern volatile uint32_t    g_reset_csr;
extern TaskStatus_t         g_task_snapshot[];
extern volatile UBaseType_t g_task_snapshot_count;
extern volatile uint32_t    g_task_snapshot_total_time;
extern volatile uint32_t    g_loop_period_cyc_max;
extern TaskHandle_t         Stabilizer_Task_Handler;

#define FW_HEALTH_SNAPSHOT_MAX 16U      /* MAX_TASKS in USER/main.c */
#define FW_HEALTH_CYC_PER_US   168.0f   /* DWT CYCCNT at SYSCLK 168 MHz */

static uint32_t s_stab_run_prev  = 0U;
static uint32_t s_total_run_prev = 0U;

/* Called at 1 Hz from FwHealth_Tick() and on every ARM_REQUEST from FlightFSM_Event() (RC or ground station). */
void PreArm_Refresh(void)
{
    prearm_in_t in;

    in.estimator_ready = IMU_EstimatorReady();
    in.vbat_v          = real_voltage;
    in.rc_live         = (uint8_t)(sbus_lost == 0U);
    in.roll_deg        = imu_data.rol;
    in.pitch_deg       = imu_data.pit;
    in.safety_trip     = (uint8_t)(g_wfb_status.safety_trip != 0.0f);
    in.stab_fps        = system_monitor.stabilizerTask_fps;
    taskENTER_CRITICAL();
    (void)PreArm_Evaluate(&in);
    taskEXIT_CRITICAL();
}

void FwHealth_Tick(void)
{
    uint16_t fps[4];
    uint16_t hwm[FW_HEALTH_SNAPSHOT_MAX];
    uint32_t run;
    uint8_t  n;
    uint8_t  i;
    uint8_t  k;

    fps[0] = system_monitor.IMUSampleTask_fps;
    fps[1] = system_monitor.IMUUpdateTask_fps;
    fps[2] = system_monitor.stabilizerTask_fps;
    fps[3] = system_monitor.remoter_task_fps;
    g_rtos_budget.alive       = FwHealth_Alive(fps, 4U);
    g_rtos_budget.reset_cause = FwHealth_ResetCause(g_reset_csr);

    /* Start at the first alive second, then refresh only while every critical task keeps running. */
    if (FwHealth_IwdgEnabled() != 0U && g_rtos_budget.alive != 0U) {
        if (g_rtos_budget.iwdg_on == 0U) {
            DBGMCU_APB1PeriphConfig(DBGMCU_IWDG_STOP, ENABLE);   /* frozen while a debugger halts the core */
            IWDG_WriteAccessCmd(IWDG_WriteAccess_Enable);
            IWDG_SetPrescaler(IWDG_Prescaler_64);
            IWDG_SetReload(FwHealth_IwdgReload(FwHealth_IwdgTimeoutMs()));
            IWDG_ReloadCounter();
            IWDG_Enable();
            g_rtos_budget.iwdg_on = 1U;
        }
        IWDG_ReloadCounter();
    }

    n = (uint8_t)g_task_snapshot_count;
    if (n > FW_HEALTH_SNAPSHOT_MAX) {
        n = FW_HEALTH_SNAPSHOT_MAX;
    }
    for (i = 0U; i < n; i++) {
        hwm[i] = g_task_snapshot[i].usStackHighWaterMark;
    }
    k = FwHealth_MinIndex(hwm, n);
    if (k != FW_HEALTH_NONE) {
        g_rtos_budget.stack_min_task  = (uint8_t)g_task_snapshot[k].xTaskNumber;
        g_rtos_budget.stack_min_words = hwm[k];
    }
    for (i = 0U; i < n; i++) {
        if (g_task_snapshot[i].xHandle == Stabilizer_Task_Handler) {
            run = g_task_snapshot[i].ulRunTimeCounter;           /* CYCCNT based: unsigned deltas survive the wrap */
            g_rtos_budget.stab_cpu_pct = FwHealth_Pct(run - s_stab_run_prev, g_task_snapshot_total_time - s_total_run_prev);
            s_stab_run_prev  = run;
            s_total_run_prev = g_task_snapshot_total_time;
            break;
        }
    }
    g_rtos_budget.loop_max_us = (float)g_loop_period_cyc_max / FW_HEALTH_CYC_PER_US;

    PreArm_Refresh();
}
