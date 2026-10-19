/* Drift NN task (PROPOSED, shadow by default).
 * 100 Hz sampling of the 23 model inputs, 50 ms block means, one model step every 5 samples (20 Hz),
 * the same binning the offline training uses (ground_station/analysis/nn_io.py, 50 ms block mean).
 * Priority 1: below every flight task, so it only uses idle time. It never writes a flight variable:
 * it publishes the clamped residual dvx/dvy, and StabilizerTask.c decides per tick whether to apply it
 * (g_drift_nn_live, Dnn_LiveGate). */

#include "FreeRTOS.h"
#include "task.h"
#include "stm32f4xx.h"
#include "bmi088_driver.h"
#include "imu_update.h"
#include "Ano_OF.h"
#include "ekf_of.h"
#include "drift_nn.h"
#include "drift_nn_task.h"

#ifdef __CC_ARM
    #define DNN_CYC_NOW() (DWT->CYCCNT)
#else
    #define DNN_CYC_NOW() 0U
#endif

#define DRIFT_NN_PRIO         1U
#define DRIFT_NN_STK_WORDS    384U    /* 256 left only 5% over the 972 B Keil worst case (expf chain) */
#define DRIFT_NN_HEAP_MARGIN  2048U   /* bytes left for later allocations */
#define DRIFT_NN_PERIOD_MS    10U
#define DRIFT_NN_BLOCK        5U      /* samples per step: 5 x 10 ms = 50 ms */
#define DRIFT_NN_ALT_MAX      1000.0f /* of_alt_cm at or above this is the 0xFFFFFFFF sentinel */
#define DRIFT_NN_IN           23

/* Defined in TASK/StabilizerTask.c without a header extern. */
extern EkfOf_t s_ekf_of;

volatile DriftNn_t g_drift_nn;
volatile uint8_t g_drift_nn_live = DRIFT_NN_LIVE_OFF;
volatile float   g_drift_nn_clamp_mps = 0.10f;

static float dnn_clamp(float x, float lim)
{
    if (x > lim) return lim;
    if (x < -lim) return -lim;
    return x;
}

static TaskHandle_t s_dnn_handle;

static void dnn_sample(float *f)
{
    f[0]  = Acc_X_Real;
    f[1]  = Acc_Y_Real;
    f[2]  = Acc_Z_Real;
    f[3]  = Gyro_X_Real;
    f[4]  = Gyro_Y_Real;
    f[5]  = Gyro_Z_Real;
    f[6]  = imu_data.rol;
    f[7]  = imu_data.pit;
    f[8]  = (float)ano_of.of2_dx_fix;
    f[9]  = (float)ano_of.of2_dy_fix;
    f[10] = (float)ano_of.of1_dx;
    f[11] = (float)ano_of.of1_dy;
    f[12] = s_ekf_of.x[1];
    f[13] = s_ekf_of.x[4];
    f[14] = (float)ano_of.of_alt_cm;
    f[15] = s_ekf_of.innov_x;
    f[16] = s_ekf_of.innov_y;
    f[17] = (float)ano_of.acc_data_x;
    f[18] = (float)ano_of.acc_data_y;
    f[19] = (float)ano_of.acc_data_z;
    f[20] = (float)ano_of.gyr_data_x;
    f[21] = (float)ano_of.gyr_data_y;
    f[22] = (float)ano_of.gyr_data_z;
}

static void drift_nn_task(void *arg)
{
    float sum[DRIFT_NN_IN], s[DRIFT_NN_IN], v[2], out[2], lim;
    int rc;
    float alt_last = drift_nn_mu(14);   /* training mean until the first valid altitude */
    uint32_t n = 0U, i, t0, dc;
    TickType_t wake = xTaskGetTickCount();
    (void)arg;

    drift_nn_reset();
    for (i = 0U; i < DRIFT_NN_IN; i++) sum[i] = 0.0f;
    for (;;) {
        vTaskDelayUntil(&wake, pdMS_TO_TICKS(DRIFT_NN_PERIOD_MS));
        if (g_drift_nn.reset_req) {
            /* steps first, the flag last: the live gate holds WARMUP while either says so */
            g_drift_nn.steps = 0U;
            drift_nn_reset();
            for (i = 0U; i < DRIFT_NN_IN; i++) sum[i] = 0.0f;
            n = 0U;
            g_drift_nn.reset_req = 0U;
        }
        dnn_sample(s);
        for (i = 0U; i < DRIFT_NN_IN; i++) sum[i] += s[i];
        if (++n < DRIFT_NN_BLOCK) continue;

        for (i = 0U; i < DRIFT_NN_IN; i++) {
            s[i] = sum[i] * (1.0f / (float)DRIFT_NN_BLOCK);
            sum[i] = 0.0f;
        }
        n = 0U;
        /* Same hold as nnio_fix.hold_alt: a block that caught the sentinel keeps the last valid value. */
        if (s[14] < DRIFT_NN_ALT_MAX) {
            alt_last = s[14];
        } else {
            s[14] = alt_last;
            g_drift_nn.alt_hold++;
        }
        v[0] = s[12];
        v[1] = s[13];
        t0 = DNN_CYC_NOW();
        rc = drift_nn_step(s, v, out);
        if (rc < 0) g_drift_nn.status = DRIFT_NN_ST_BADSUM;
        dc = DNN_CYC_NOW() - t0;
        g_drift_nn.cyc_last = dc;
        if (dc > g_drift_nn.cyc_max) g_drift_nn.cyc_max = dc;
        g_drift_nn.v_in[0] = v[0];
        g_drift_nn.v_in[1] = v[1];
        g_drift_nn.vx = out[0];
        g_drift_nn.vy = out[1];
        /* out is a residual (corrected = v + out); on a bad checksum it is a copy of v, never applied */
        lim = g_drift_nn_clamp_mps;
        if (!(lim > 0.0f)) lim = 0.0f;
        g_drift_nn.dvx = (rc == 0) ? dnn_clamp(out[0], lim) : 0.0f;
        g_drift_nn.dvy = (rc == 0) ? dnn_clamp(out[1], lim) : 0.0f;
        g_drift_nn.step_ms = (uint32_t)xTaskGetTickCount() * (uint32_t)portTICK_PERIOD_MS;
        g_drift_nn.steps++;
    }
}

void drift_nn_task_create(void)
{
    if (xTaskCreate(drift_nn_task, "drift_nn", DRIFT_NN_STK_WORDS, NULL, DRIFT_NN_PRIO, &s_dnn_handle) != pdPASS) {
        g_drift_nn.status = DRIFT_NN_ST_NOCREATE;
        return;
    }
    if (xPortGetFreeHeapSize() < DRIFT_NN_HEAP_MARGIN) {
        vTaskDelete(s_dnn_handle);   /* V9: frees its TCB and stack at once */
        s_dnn_handle = NULL;
        g_drift_nn.status = DRIFT_NN_ST_LOWHEAP;
        return;
    }
    if (g_drift_nn.status == DRIFT_NN_ST_IDLE) g_drift_nn.status = DRIFT_NN_ST_RUN;
}
