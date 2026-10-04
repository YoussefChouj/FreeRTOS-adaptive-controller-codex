/**
 * @module     gyro_filter.c
 * @subsystem  sensors
 * @owner      Stabilizer_Task (TASK/StabilizerTask.c): GyroFilter_Apply on the pitch/roll/yaw rate-loop feedback.
 *             USER/main.c calls GyroFilter_Init(200.0f) once before the scheduler; Send_Task (send_data.c,
 *             Cmd_GyroLpf: idx 0 enable, idx 1 cutoff) calls GyroFilter_SetEnabled/SetCutoff.
 * @purpose    Optional 2nd-order Butterworth low-pass on the gyro rate feedback (ADR-0004): RBJ-cookbook biquad in
 *             Direct-Form-II-Transposed, one per axis. Off (pass-through) at boot, so it changes nothing until enabled.
 * @inputs     rate per axis [deg/s], sample rate fs [Hz] (Init), cutoff fc [Hz] per axis, enable flag.
 * @outputs    filtered rate [deg/s]; the input unchanged while disabled or for an out-of-range axis.
 */

#include "gyro_filter.h"
#include <math.h>
#include "FreeRTOS.h"
#include "task.h"

#ifndef M_PI
#define M_PI 3.14159265358979323846f
#endif

/* ------------------------------------------------------------------
 * Private constants
 * ------------------------------------------------------------------ */

#define GYRO_FS_FALLBACK_HZ  200.0f        /* sample rate used before Init, and when Init gets fs <= 1 Hz */
#define GYRO_BIQUAD_TWO_Q    1.41421356f   /* 2Q for the Butterworth Q = 1/sqrt(2); alpha = sin(w0) / (2Q) */

/* Gyro filter tunables.
   @default_fc    Hz  [5, 90]       cutoff every axis is designed for at Init (takes effect once enabled)
   @nyquist_frac  -   [0.1, 0.49]   cutoff clamp as a fraction of the sample rate (Nyquist is 0.5)
   default_fc 40 Hz sits well above the ~8 Hz control bandwidth (ADR-0004); 0.45 keeps the clamp below Nyquist. */
typedef struct {
    float default_fc, nyquist_frac;
} gyro_tune_t;
#define GYRO_TUNE_ROW(default_fc, nyquist_frac) \
    { (default_fc), (nyquist_frac) }

static const gyro_tune_t s_tune =
/*             default_fc nyquist_frac */
    GYRO_TUNE_ROW(40.0f,     0.45f);

/* ------------------------------------------------------------------
 * Module state  (all private to this translation unit)
 * ------------------------------------------------------------------ */

typedef struct {
    float b0, b1, b2, a1, a2;   /* normalised biquad coefficients (a0 folded out) */
    float z1, z2;               /* Direct-Form-II-Transposed state                */
    float fc;                   /* current cutoff [Hz]; 0 = identity              */
} Biquad_t;

static Biquad_t s_filt[GYRO_FILT_AXES];
static float    s_fs = GYRO_FS_FALLBACK_HZ;   /* sample rate [Hz]                       */
static uint8_t  s_enabled = 0U;               /* global pass-through gate (default off) */

/* RBJ low-pass coefficients for cutoff fc at sample rate s_fs. fc <= 0 designs the identity filter; fc above
   nyquist_frac * s_fs is clamped. The coefficients are computed outside and committed inside one critical section,
   so Stabilizer_Task never runs a half-written set. */
static void biquad_design(Biquad_t* f, float fc)
{
    float w0, cw, sw, alpha, a0;
    float fc_max = s_tune.nyquist_frac * s_fs;
    float b0, b1, b2, a1, a2, new_fc;

    if (fc <= 0.0f) {
        b0 = 1.0f;
        b1 = 0.0f;
        b2 = 0.0f;
        a1 = 0.0f;
        a2 = 0.0f;
        new_fc = 0.0f;
    } else {
        if (fc > fc_max) fc = fc_max;

        w0 = 2.0f * M_PI * fc / s_fs;
        cw = cosf(w0);
        sw = sinf(w0);
        alpha = sw / GYRO_BIQUAD_TWO_Q;
        a0 = 1.0f + alpha;

        b0 = ((1.0f - cw) * 0.5f) / a0;
        b1 = (1.0f - cw) / a0;
        b2 = ((1.0f - cw) * 0.5f) / a0;
        a1 = (-2.0f * cw) / a0;
        a2 = (1.0f - alpha) / a0;
        new_fc = fc;
    }

    taskENTER_CRITICAL();
    f->b0 = b0;
    f->b1 = b1;
    f->b2 = b2;
    f->a1 = a1;
    f->a2 = a2;
    f->fc = new_fc;
    taskEXIT_CRITICAL();
}

/* ------------------------------------------------------------------
 * Public API
 * ------------------------------------------------------------------ */

void GyroFilter_Init(float fs_hz)
{
    int i;
    s_fs = (fs_hz > 1.0f) ? fs_hz : GYRO_FS_FALLBACK_HZ;
    s_enabled = 0U;   /* pass-through until a GS command enables it: no flight-behaviour change at boot */
    for (i = 0; i < GYRO_FILT_AXES; i++) {
        s_filt[i].z1 = 0.0f;
        s_filt[i].z2 = 0.0f;
        biquad_design(&s_filt[i], s_tune.default_fc);
    }
}

void GyroFilter_SetCutoff(GyroFiltAxis_e axis, float fc_hz)
{
    if (axis < 0 || axis >= GYRO_FILT_AXES) return;
    biquad_design(&s_filt[axis], fc_hz);
}

void GyroFilter_SetEnabled(uint8_t on)
{
    s_enabled = on ? 1U : 0U;
}

float GyroFilter_Apply(GyroFiltAxis_e axis, float x)
{
    Biquad_t* f;
    float y;

    if (!s_enabled || axis < 0 || axis >= GYRO_FILT_AXES) {
        return x;
    }
    f = &s_filt[axis];
    y       = f->b0 * x + f->z1;
    f->z1   = f->b1 * x - f->a1 * y + f->z2;
    f->z2   = f->b2 * x - f->a2 * y;
    return y;
}
