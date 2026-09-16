// ------------------------------------------------------------------------------
// Gyro rate-feedback low-pass filter — implementation (see gyro_filter.h, ADR-0004)
// ------------------------------------------------------------------------------
// 2nd-order Butterworth (RBJ cookbook) biquad in Direct-Form-II-Transposed, per axis.
// ------------------------------------------------------------------------------

#include "gyro_filter.h"
#include <math.h>

#ifndef M_PI
#define M_PI 3.14159265358979323846f
#endif

#define GYRO_FILT_DEFAULT_FC   40.0f   // [Hz] sensible default cutoff once enabled (control BW ~8 Hz)

typedef struct {
    // Normalized biquad coefficients (a0 folded out)
    float b0, b1, b2, a1, a2;
    // Direct-Form-II-Transposed state
    float z1, z2;
    float fc;   // current cutoff [Hz]
} Biquad_t;

static Biquad_t s_filt[GYRO_FILT_AXES];
static float    s_fs = 200.0f;     // sample rate [Hz]
static uint8_t  s_enabled = 0U;    // global pass-through gate (default off)

// Compute RBJ low-pass coefficients for cutoff fc at sample rate s_fs (Q = 1/sqrt(2)).
static void biquad_design(Biquad_t* f, float fc)
{
    float w0, cw, sw, alpha, a0;
    float fc_max = 0.45f * s_fs; // keep below Nyquist with margin

    if (fc <= 0.0f) {
        // Disabled cutoff -> identity (pass-through), but keep state defined.
        f->b0 = 1.0f; f->b1 = 0.0f; f->b2 = 0.0f;
        f->a1 = 0.0f; f->a2 = 0.0f;
        f->fc = 0.0f;
        return;
    }
    if (fc > fc_max) fc = fc_max;

    w0 = 2.0f * M_PI * fc / s_fs;
    cw = cosf(w0);
    sw = sinf(w0);
    alpha = sw / 1.41421356f; // 2*Q with Q=1/sqrt(2)  -> sin/(2Q)=sin/sqrt(2)
    a0 = 1.0f + alpha;

    f->b0 = ((1.0f - cw) * 0.5f) / a0;
    f->b1 = (1.0f - cw) / a0;
    f->b2 = ((1.0f - cw) * 0.5f) / a0;
    f->a1 = (-2.0f * cw) / a0;
    f->a2 = (1.0f - alpha) / a0;
    f->fc = fc;
}

void GyroFilter_Init(float fs_hz)
{
    int i;
    s_fs = (fs_hz > 1.0f) ? fs_hz : 200.0f;
    s_enabled = 0U; // default: pass-through (no flight-behaviour change)
    for (i = 0; i < GYRO_FILT_AXES; i++) {
        s_filt[i].z1 = 0.0f;
        s_filt[i].z2 = 0.0f;
        biquad_design(&s_filt[i], GYRO_FILT_DEFAULT_FC);
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
        return x; // pass-through
    }
    f = &s_filt[axis];
    // Direct Form II Transposed
    y       = f->b0 * x + f->z1;
    f->z1   = f->b1 * x - f->a1 * y + f->z2;
    f->z2   = f->b2 * x - f->a2 * y;
    return y;
}
