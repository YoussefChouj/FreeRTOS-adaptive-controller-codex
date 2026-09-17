#include "main.h"
#include "creat_task.h"
#include "mrac.h"
#include "gyro_filter.h"
#include "fault_capture.h"
#include "platform_registry.h"
/* Subscribe telemetry-mode knob (telemetry-throughput-2026-09-08). Used by
 * Send_Task to gate the legacy Frame A/B/C emission when the host has
 * requested SUBSCRIBE_ONLY mode. Pulls in Subscribe_TelemetryMode_e. */
#include "subscribe.h"

/* DEBUG-telemetry-bisect: instrumentation kept for future use. Flip to `#if 1`
 * to re-enable the UART5 polling checkpoints and the priority-5 counter
 * reporter. NOTE: while enabled these write to UART5 — the same port as
 * telemetry — so they corrupt the telemetry stream; only enable when
 * telemetry itself is not being used. The stack-overflow hook lives outside
 * this block now (see below); the polling UART5 path inside is no longer
 * used for stack diagnosis since livewatch can read g_stack_overflow_marker. */
#if 0
/* one-shot polling TX on UART5 (bypasses DMA + RTOS).
 * Used to checkpoint how far boot/scheduling gets when normal telemetry is
 * silent. Safe because DMA1_Stream7 is idle between transfers. */
static void dbg_uart5_puts(const char *s)
{
    while (*s) {
        while (USART_GetFlagStatus(UART5, USART_FLAG_TXE) == RESET) { }
        USART_SendData(UART5, (uint8_t)(*s++));
    }
    while (USART_GetFlagStatus(UART5, USART_FLAG_TC) == RESET) { }
}

/* DEBUG-telemetry-bisect: print an unsigned decimal over UART5 (polling). */
static void dbg_uart5_u32(uint32_t v)
{
    char b[11];
    int i = 10;
    b[10] = '\0';
    if (v == 0U) { dbg_uart5_puts("0"); return; }
    while (v != 0U && i > 0) { b[--i] = (char)('0' + (v % 10U)); v /= 10U; }
    dbg_uart5_puts(&b[i]);
}

/* DEBUG-telemetry-bisect: priority-5 reporter (above all app tasks at prio 4).
 * Dumps each task's execution counter every 250 ms. Whichever counter STOPS
 * incrementing between lines is the task whose work function hung (starving
 * Send_Task). If NO "CNT" line ever prints, the hang disables interrupts/tick
 * (e.g. an unmatched critical section) or lives in an ISR. Remove after use. */
void dbg_report_task(void *pvParameters)
{
    (void)pvParameters;
    for (;;) {
        dbg_uart5_puts("\r\nCNT imu=");  dbg_uart5_u32(system_monitor.IMUUpdateTask_cnt);
        dbg_uart5_puts(" samp=");        dbg_uart5_u32(system_monitor.IMUSampleTask_cnt);
        dbg_uart5_puts(" stab=");        dbg_uart5_u32(system_monitor.stabilizerTask_cnt);
        dbg_uart5_puts(" rem=");         dbg_uart5_u32(system_monitor.remoter_task_cnt);
        dbg_uart5_puts(" auto=");        dbg_uart5_u32(system_monitor.AutoflyTask_cnt);
        dbg_uart5_puts(" send=");        dbg_uart5_u32(system_monitor.USART2_task_cnt);
        dbg_uart5_puts("\r\n");
        vTaskDelay(pdMS_TO_TICKS(250));
    }
}
#endif /* DEBUG-telemetry-bisect */

/* Stack-overflow detection hooks (FreeRTOS calls these when a task blows its
 * fixed stack, or pvPortMalloc() returns NULL). Wired by configCHECK_FOR_STACK_
 * OVERFLOW=2 and (in Track 3) configUSE_MALLOC_FAILED_HOOK=1.
 *
 * The stack hook writes two volatile markers instead of polling UART5: the
 * livewatch link is the only path that works after a fault-induced reboot, so
 * reading g_stack_overflow_marker post-mortem is what actually pays off.
 * g_stack_overflow_task_crc is a small rolling hash of pcTaskName so the
 * operator can identify which task overflowed without reading the string
 * buffer (which may itself be on the corrupted stack). */

/* Sentinel: 0xDEADBEEF = untouched. Hook flips it to 0xCAFEBABE on fire.
 * Distinct from any HardFault marker so the two failure modes don't collide. */
volatile uint32_t g_stack_overflow_marker   = 0xDEADBEEFU;
volatile uint32_t g_stack_overflow_task_crc = 0U;

void vApplicationStackOverflowHook(TaskHandle_t xTask, char *pcTaskName)
{
    /* Rolling-hash pcTaskName into a 32-bit CRC so post-mortem livewatch
     * reads can name the offending task without touching the (possibly
     * corrupted) task-name buffer. */
    const char *p = pcTaskName ? pcTaskName : "?";
    uint32_t h = 0U;
    while (*p != '\0') {
        h = (h << 5) | (h >> 27);
        h ^= (uint32_t)(uint8_t)*p;
        p++;
    }
    g_stack_overflow_task_crc = h;
    g_stack_overflow_marker   = 0xCAFEBABEU;

    (void)xTask;
    /* Halt here. Operator reads the markers via livewatch before reset. */
    __disable_irq();
    for (;;) { }
}

/* Malloc-failed hook stub. Track 3 (fault-logger spec) will wire this to the
 * persistent fault recorder in USER/fault_capture.c. Declared now so the symbol
 * exists when configUSE_MALLOC_FAILED_HOOK is flipped to 1 without a linker
 * error. Body intentionally empty — see track-3 spec for the real handler. */
void vApplicationMallocFailedHook(void)
{
    /* Records the fault context, persists to flash on next boot, then blinks SOS.
     * Requires configUSE_MALLOC_FAILED_HOOK=1 in FreeRTOSConfig.h. */
    FaultCapture_Record(4U);  /* 4 = malloc-failure fault type */
    for (;;) { }  /* halt — fault_captured is set before this */
}

/**
 * @module  main.c
 * @subsystem  scheduler
 * @depends  main.h, creat_task.h, mrac.h
 * @owns  firmware entrypoint and FreeRTOS task cadence
 * @caution  task periods must stay consistent with dt values used by control and estimation modules
 */

// main() is the entry point of the program; execution starts here after reset and startup code runs
int main(void)
{ 
    PlatformRegistry_Init();
    BSP_Init(); // Initialize Board Support Package (hardware peripherals: GPIO, timers, UART, etc.)

    /* Persistent fault logger: checks for a captured fault from a previous boot,
     * persists it to flash Sector 11, and blinks SOS on the red LED.
     * Called before the scheduler so the flash write is safe (no DMA in flight). */
    FaultRecord_Init();

  //������ʼ���� (chu��ng ji��n k��i sh�� r��n w��) - "Create start task"
  // xTaskCreate() is a FreeRTOS function that creates a new task
  xTaskCreate((TaskFunction_t )start_task,            //������ (r��n w�� h��n sh��) - "task function" - pointer to the function that will run as the task
        (const char*    )"start_task",          //�������� (r��n w�� m��ng ch��ng) - "task name" - human-readable string for debugging
        (uint16_t       )START_STK_SIZE,        //�����ջ��С (r��n w�� du�� zh��n d�� xi��o) - "task stack size" - amount of memory (in words) reserved for this task's stack
        (void*          )NULL,                  //���ݸ��������Ĳ��� (chu��n d�� g��i r��n w�� h��n sh�� de c��n sh��) - "parameter passed to task function" - NULL means no parameter
        (UBaseType_t    )START_TASK_PRIO,       //�������ȼ� (r��n w�� y��u xi��n j��) - "task priority" - higher number = higher priority
        (TaskHandle_t*  )&StartTask_Handler);   //������ (r��n w�� j�� b��ng) - "task handle" - a reference/ID to control this task later
  /* DEBUG-telemetry-bisect: dbg_uart5_puts("PRE-SCHED\r\n"); (checkpoint before scheduler start) */
  vTaskStartScheduler();          //����������� (k��i q�� r��n w�� di��o d��) - "start task scheduler" - begins FreeRTOS multitasking; this function never returns
}

//��ʼ���������� (k��i sh�� r��n w�� r��n w�� h��n sh��) - "Start task function"
// This task's job is to create all other application tasks, then delete itself
void start_task(void *pvParameters)
{
  taskENTER_CRITICAL();           //�����ٽ��� (j��n r�� l��n ji�� q��) - "enter critical section" - disables interrupts to protect the following code from being interrupted
 
  // Create SystemMonitor_Task - runs at 1 Hz to check system health
  xTaskCreate((TaskFunction_t )SystemMonitor_Task,     
        (const char*    )"SystemMonitor_Task",   
        (uint16_t       )SYSTEMMONITOR_STK_SIZE, 
        (void*          )NULL,
        (UBaseType_t    )SYSTEMMONITOR_TASK_PRIO,
        (TaskHandle_t*  )&SystemMonitorTask_Handler);       

  // Create IMU_DataDeal_Task - processes IMU (Inertial Measurement Unit) sensor data at 1000 Hz
  xTaskCreate((TaskFunction_t )IMU_DataDeal_Task,     
        (const char*    )"IMU_DataDeal_Task",   
        (uint16_t       )IMU_DataDeal_STK_SIZE, 
        (void*          )NULL,
        (UBaseType_t    )IMU_DataDeal_TASK_PRIO,
        (TaskHandle_t*  )&IMU_DataDealTask_Handler);  
                
    // Create IMUSample_Task - samples raw IMU sensor readings at 1000 Hz
    xTaskCreate((TaskFunction_t )IMUSample_Task,     
        (const char*    )"IMUSample_Task",   
        (uint16_t       )IMUSAMPLE_STK_SIZE, 
        (void*          )NULL,
        (UBaseType_t    )IMUSAMPLE_TASK_PRIO,
        (TaskHandle_t*  )&IMUSampleTask_Handler); 


  // Create Stabilizer_Task - controls aircraft attitude (orientation) at 200 Hz
  xTaskCreate((TaskFunction_t )Stabilizer_Task,     
        (const char*    )"Stabilizer_Task",   
        (uint16_t       )STABILIZER_STK_SIZE, 
        (void*          )NULL,
        (UBaseType_t    )STABILIZER_Task_TASK_PRIO,
        (TaskHandle_t*  )&Stabilizer_Task_Handler); 

  // Create Remoter_Task - handles remote control input at 100 Hz
  xTaskCreate((TaskFunction_t )Remoter_Task,     
        (const char*    )"Remoter_Task",   
        (uint16_t       )REMOTERTASK_STK_SIZE, 
        (void*          )NULL,
        (UBaseType_t    )REMOTERTASK_TASK_PRIO,
        (TaskHandle_t*  )&RemoterTaskTask_Handler); 			

   // Create Autofly_Task - autonomous flight control at 200 Hz
   xTaskCreate((TaskFunction_t )Autofly_Task,     
        (const char*    )"Autofly_Task",   
        (uint16_t       )AUTOFLYTASK_STK_SIZE, 
        (void*          )NULL,
        (UBaseType_t    )AUTOFLYTASK_TASK_PRIO,
        (TaskHandle_t*  )&AutoflyTask_Handler); 		
  // Create Send_Task - wireless UART communication at 100 Hz
  xTaskCreate((TaskFunction_t )Send_Task,     
        (const char*    )"Send_Task",   
        (uint16_t       )SENDTASK_STK_SIZE, 
        (void*          )NULL,
        (UBaseType_t    )SENDTASK_PRIO,
        (TaskHandle_t*  )&SendTask_Handler);

  /* DEBUG-telemetry-bisect: priority-5 counter reporter (above app tasks @ prio 4).
   * Re-enable together with the `#if 0` helper block at the top of this file. */
#if 0
  xTaskCreate((TaskFunction_t )dbg_report_task,
        (const char*    )"dbg_report",
        (uint16_t       )256,
        (void*          )NULL,
        (UBaseType_t    )5,
        (TaskHandle_t*  )NULL);
#endif

  vTaskDelete(StartTask_Handler); //ɾ����ʼ���� (sh��n ch�� k��i sh�� r��n w��) - "delete start task" - this task deletes itself since its job (creating other tasks) is done
  taskEXIT_CRITICAL();            //�˳��ٽ��� (tu�� ch�� l��n ji�� q��) - "exit critical section" - re-enables interrupts (though this line never executes because task was deleted)
}


/*--------------------------------------------------------
�������ܣ� ϵͳ������ (h��n sh�� g��ng n��ng: x�� t��ng ji��n sh�� q��) - "Function: System monitor"
֡��    ��  1 (zh��n l��: 1) - "Frame rate: 1 Hz" (runs once per second)
----------------------------------------------------------*/
void SystemMonitor_Task(void *pvParameters)
{
    TickType_t PreviousWakeTime; // Stores the last time this task was woken up
    const TickType_t TimeIncrement = pdMS_TO_TICKS(1000); // Convert 1000 milliseconds to RTOS ticks
    PreviousWakeTime = xTaskGetTickCount(); // Initialize with current tick count
    while(1) // Infinite loop - task runs forever
  {
      SystemErrorDetect(); // Check for system errors or faults
      Get_Voltage();       // 1 Hz ADC battery read: drives the low-battery beep (SetBeep<15V),
                           // the dashboard battery widget (status.vbat / id.vbat), and the SysID
                           // operating-point log. Previously only called from the unused
                           // ANO_Report_UserData1(), so real_voltage stayed 0 everywhere.

      vTaskDelayUntil(&PreviousWakeTime, TimeIncrement ); // Sleep until exactly 1000ms has passed since last wake (maintains precise 1 Hz timing)
  }
}

/*--------------------------------------------------------
�������ܣ� ���ߴ������� (h��n sh�� g��ng n��ng: w�� xi��n chu��n k��u r��n w��) - "Function: Wireless serial port task"
// Frame rate: 100 Hz normal flight; 200 Hz during a SysID run (id_frame_on) for clean high-rate ID.
----------------------------------------------------------*/
void Send_Task(void *pvParameters)
{
    TickType_t PreviousWakeTime;
    PreviousWakeTime = xTaskGetTickCount();
    /* DEBUG-telemetry-bisect: dbg_uart5_puts("SEND-RAN\r\n"); (fires once when Send_Task first runs) */
  while(1)
  {
        // SysID high-rate mode (id_frame_on): stream the single-axis ID frame at a STABLE 200 Hz
        // (1:1 with the 200 Hz Stabilizer loop -> no aliasing; clean periodic capture for FRF /
        // 2nd-order+ plant ID, which a multisine needs to avoid spectral leakage).
        //
        // MUST be vTaskDelay (relative), NOT vTaskDelayUntil: the GS frame TX is a background DMA,
        // but Send_Groundstation_Telemetry_UART4() busy-waits at its START for the PREVIOUS DMA to
        // finish (send_data.c). One 43 B frame takes ~3.73 ms on the 115200 link. The 1000 Hz IMU
        // and 200 Hz control tasks preempt this priority-2 task, so the body can't reliably finish
        // inside a 5 ms deadline -> vTaskDelayUntil falls behind and RUNS AWAY (returns immediately
        // each pass), pacing the loop at the ~3.73 ms busy-wait = ~270 Hz, which 100%-saturates the
        // UART and drops bursts of frames. vTaskDelay always sleeps a full 5 ms of wall-clock; the
        // DMA completes during that sleep, so the next busy-wait is ~0, the link sits at ~75 %
        // (8.6 KB/s of 11.52 KB/s) with margin, and the rate cannot run away. (For >200 Hz you must
        // raise the UART5 baud or shrink the frame; 115200 + 43 B caps clean capture near 200 Hz.)
        if (mrac_flags.id_frame_on || mrac_flags.of_frame_on) {
#if SUBSCRIBE_BUILD_DEFAULT_SUBSCRIBE
            /* SUBSCRIBE_ONLY skips the entire legacy UART5 DMA path so
             * Send_Task can run at its nominal 200 Hz instead of being
             * paced by the ~3.7 ms DMA busy-wait. ID/OF frame users
             * (SysID) stay on MIXED so the capture keeps flowing. */
            if (g_telemetry_mode != SUBSCRIBE_TELEMETRY_SUBSCRIBE_ONLY) {
                Send_Groundstation_Telemetry_UART4(); // single-axis ID frame (0x03) or OF-cal frame (0x05) -> UART5 (DMA1_Stream7)
            } else {
                Subscribe_StreamTick();                // 0x0A stream frames on USART3 (WiFi) for active slots
            }
#else
            Send_Groundstation_Telemetry_UART4(); // single-axis ID frame (0x03) or OF-cal frame (0x05) -> UART5 (DMA1_Stream7)
#endif
            Process_GroundStation_Command();      // still service START/ABORT/flag commands
            vTaskDelay(pdMS_TO_TICKS(5));          // hard 5 ms floor = stable ~200 Hz, no runaway
            PreviousWakeTime = xTaskGetTickCount(); // re-base so the 100 Hz path resumes cleanly after a run
        } else {
            send_to_linux(); // Send data to Linux companion computer (if present)
#if SUBSCRIBE_BUILD_DEFAULT_SUBSCRIBE
            /* Same gate as the SysID branch above: in SUBSCRIBE_ONLY the
             * 3.7 ms legacy DMA busy-wait goes away and Send_Task runs at
             * the measured 200 Hz post-fix. MIXED (default) preserves the
             * legacy Frame A/B/C stream the dashboard depends on. */
            if (g_telemetry_mode != SUBSCRIBE_TELEMETRY_SUBSCRIBE_ONLY) {
                Send_Groundstation_Telemetry_UART4(); // Send telemetry to Ground Station
            } else {
                Subscribe_StreamTick();                // 0x0A stream frames on USART3 (WiFi) for active slots
            }
#else
            Send_Groundstation_Telemetry_UART4(); // Send telemetry to Ground Station
#endif
            Process_GroundStation_Command(); // Process received commands
            usart3_send();//�������������� - "UART3 send task" - transmit data via UART3 peripheral
            vTaskDelayUntil(&PreviousWakeTime, pdMS_TO_TICKS(10)); // 10ms = 100 Hz
        }
        system_monitor.USART2_task_cnt++; // Increment task execution counter (for monitoring task health)
  }
}

/*--------------------------------------------------------
�������ܣ� �����Ǹ��� (h��n sh�� g��ng n��ng: tu�� lu�� y�� g��ng x��n) - "Function: Gyroscope update" (actually full IMU sensor fusion)
֡��    �� 1000 (zh��n l��: 1000) - "Frame rate: 1000 Hz"
----------------------------------------------------------*/
extern _imu_st imu_data; // 'extern' means this variable is defined in another file; we're just declaring we'll use it here

void IMU_DataDeal_Task(void *pvParameters)
{
    TickType_t PreviousWakeTime;
    const TickType_t TimeIncrement = pdMS_TO_TICKS(1); // 1ms period = 1000 Hz
    PreviousWakeTime = xTaskGetTickCount();	
  while(1)
  {
        // CONSTRAINT: This 1 ms task period must match the dt argument passed below.
        // WHY: Mahony integration and bias integral terms scale directly with dt.
        IMU_Update_Mahony(&imu_data,1e-3f); // Run Mahony filter algorithm to fuse gyro/accel into attitude estimate; 1e-3f = 0.001 = 1ms sample time
        system_monitor.IMUUpdateTask_cnt++; // Count executions for health monitoring
        vTaskDelayUntil(&PreviousWakeTime, TimeIncrement );
  }
}

/*--------------------------------------------------------
�������ܣ� �����ǲ��� (h��n sh�� g��ng n��ng: tu�� lu�� y�� c��i y��ng) - "Function: Gyroscope sampling" (raw sensor reading)
֡��    �� 1000 (zh��n l��: 1000) - "Frame rate: 1000 Hz"
----------------------------------------------------------*/
void IMUSample_Task(void *pvParameters)
{
    TickType_t PreviousWakeTime;
    const TickType_t TimeIncrement = pdMS_TO_TICKS(1); // 1ms = 1000 Hz
    PreviousWakeTime = xTaskGetTickCount();	
  while(1)
  {
        Sensor_Data_Prepare(); // Read raw sensor values from IMU hardware (SPI/I2C communication)
        system_monitor.IMUSampleTask_cnt++;
        vTaskDelayUntil(&PreviousWakeTime, TimeIncrement );
  }
  
}
/*--------------------------------------------------------
�������ܣ� ������̬���� (h��n sh�� g��ng n��ng: k��ng zh�� z�� t��i r��n w��) - "Function: Control attitude task" (stabilization/flight control)
֡��    �� 200 (zh��n l��: 200) - "Frame rate: 200 Hz"
----------------------------------------------------------*/
void Stabilizer_Task(void *pvParameters)
{
    TickType_t PreviousWakeTime;
    const TickType_t TimeIncrement = pdMS_TO_TICKS(5); // 5ms = 200 Hz
    PreviousWakeTime = xTaskGetTickCount();
    MRAC_Init(); // Must run once before the control loop: populates mrac_config_* gains,
                 // mrac_to_mixer scalers, and axis_enable flags. Without this call all
                 // configs stay zero-initialized, causing division-by-zero (u_nom = +/-inf)
                 // and NaN in u_ad that corrupts the motor throttle channel.
    GyroFilter_Init(200.0f); // Phase-1 gyro LPF; starts DISABLED (pass-through) — see ADR-0004.
  while(1)
    {
                        // PERF: Keep fixed-period scheduling for control-loop determinism.
        stabilizer_Task(); // Run PID controllers and compute motor outputs to stabilize aircraft

        system_monitor.stabilizerTask_cnt++;
        vTaskDelayUntil(&PreviousWakeTime, TimeIncrement );
  }
}

/*--------------------------------------------------------
�������ܣ� ң�������� (h��n sh�� g��ng n��ng: y��o k��ng q�� r��n w��) - "Function: Remote controller task"
֡��    �� 100 (zh��n l��: 100) - "Frame rate: 100 Hz"
----------------------------------------------------------*/
void Remoter_Task(void *pvParameters)
{
    TickType_t PreviousWakeTime;
    const TickType_t TimeIncrement = pdMS_TO_TICKS(10); // 10ms = 100 Hz
    PreviousWakeTime = xTaskGetTickCount();	
  while(1)
  {
        remoter_task(); // Process remote control receiver input (RC radio commands)
        system_monitor.remoter_task_cnt++;
        vTaskDelayUntil(&PreviousWakeTime, TimeIncrement );
  }
}

/*--------------------------------------------------------
�������ܣ� ������������ (h��n sh�� g��ng n��ng: z�� zh�� f��i x��ng r��n w��) - "Function: Autonomous flight task"
֡��    �� 200 (zh��n l��: 200) - "Frame rate: 200 Hz"
----------------------------------------------------------*/
void Autofly_Task(void *pvParameters)
{
    TickType_t PreviousWakeTime;
    const TickType_t TimeIncrement = pdMS_TO_TICKS(5); // 5ms = 200 Hz
    PreviousWakeTime = xTaskGetTickCount();	
  while(1)
  {
        AutoflyTask(); // Execute autonomous navigation algorithms (waypoint following, etc.)
        system_monitor.AutoflyTask_cnt++;
        vTaskDelayUntil(&PreviousWakeTime, TimeIncrement );
  }
}


