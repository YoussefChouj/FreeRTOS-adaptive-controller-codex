/* drift_nn.c - see drift_nn.h. Layout of DNN_W (written by export_weights.py, all float32):
 *   mu[D] sd[D] | (DNN_RES) inp_w[H][D] inp_b[H] | per layer i: w[H][Cin][K] b[H] | out_w[4][H] out_b[4]
 * Layer i: Cin = H if (DNN_RES or i > 0) else D, dilation 2^i, causal (zero history at reset). */
#include <math.h>
#include <string.h>
#define DNN_WEIGHTS_IMPL   /* this file owns DNN_W (see drift_nn_weights.h) */
#include "drift_nn.h"

#define CIN0 (DNN_RES ? DNN_H : DNN_D)
#define CMAX (DNN_H > DNN_D ? DNN_H : DNN_D)

/* ring i holds the last (K-1)*2^i + 1 inputs of layer i; rows are time, CMAX columns */
#define RL(i) ((DNN_K - 1) * (1 << (i)) + 1)

/* The ~16.5 KB ring lives in the CPU-only CCM (section MRAC_CCM, placed by USER/JX_FLY.sct, zeroed by __main),
 * because IRAM1 is nearly full. No DMA touches it. Host builds use plain RAM. */
#ifdef __CC_ARM
    #define DNN_CCM __attribute__((section("MRAC_CCM"), zero_init))
#else
    #define DNN_CCM
#endif
static float ring[DNN_RING_ROWS][CMAX] DNN_CCM;
static int head[DNN_L];
static int ok = -2;   /* -2 unchecked, 0 good, -1 bad */

#if !DNN_ODD
/* erf, Abramowitz & Stegun 7.1.26 (|error| < 1.5e-7): no dependence on the C library providing erff */
static float erf_as(float x)
{
    const float a = fabsf(x), t = 1.0f / (1.0f + 0.3275911f * a);
    const float y = 1.0f - ((((1.061405429f * t - 1.453152027f) * t + 1.421413741f) * t - 0.284496736f) * t
                            + 0.254829592f) * t * expf(-a * a);
    return x < 0.0f ? -y : y;
}
#endif

static float act(float x)
{
#if DNN_ODD
    return tanhf(x);
#else
    return 0.5f * x * (1.0f + erf_as(x * 0.70710678f));   /* GELU, as torch's default (erf form) */
#endif
}

uint32_t drift_nn_checksum(void)
{
    const uint8_t *p = (const uint8_t *)DNN_W;
    uint32_t h = 2166136261u;
    for (uint32_t n = 0; n < sizeof(DNN_W); n++) { h ^= p[n]; h *= 16777619u; }
    return h;
}

void drift_nn_reset(void)
{
    memset(ring, 0, sizeof(ring));
    memset(head, 0, sizeof(head));
}

int drift_nn_step(const float *f, const float v[2], float out[2])
{
    if (ok == -2) ok = (drift_nn_checksum() == DNN_CHECKSUM) ? 0 : -1;
    if (ok) { out[0] = v[0]; out[1] = v[1]; return -1; }

    const float *w = DNN_W;
    const float *mu = w, *sd = w + DNN_D;
    w += 2 * DNN_D;
    float x[CMAX], h[CMAX], y[DNN_H];
    for (int c = 0; c < DNN_D; c++) x[c] = (f[c] - mu[c]) / sd[c];
#if DNN_RES
    for (int o = 0; o < DNN_H; o++) {
        float s = w[DNN_H * DNN_D + o];
        for (int c = 0; c < DNN_D; c++) s += w[o * DNN_D + c] * x[c];
        h[o] = s;
    }
    w += DNN_H * DNN_D + DNN_H;
#else
    memcpy(h, x, sizeof(float) * DNN_D);
#endif
    int base = 0;
    for (int i = 0; i < DNN_L; i++) {
        const int cin = i ? DNN_H : CIN0, d = 1 << i, rl = RL(i);
        float (*r)[CMAX] = ring + base;
        head[i] = (head[i] + 1) % rl;
        memcpy(r[head[i]], h, sizeof(float) * cin);
        const float *b = w + DNN_H * cin * DNN_K;
        for (int o = 0; o < DNN_H; o++) {
            float s = b[o];
            const float *wo = w + o * cin * DNN_K;
            for (int j = 0; j < DNN_K; j++) {   /* tap j sees time t - (K-1-j)*d */
                const float *hr = r[(head[i] - (DNN_K - 1 - j) * d + rl * DNN_K) % rl];
                for (int c = 0; c < cin; c++) s += wo[c * DNN_K + j] * hr[c];
            }
            y[o] = act(s);
        }
        for (int o = 0; o < DNN_H; o++) h[o] = DNN_RES ? h[o] + y[o] : y[o];
        w += DNN_H * cin * DNN_K + DNN_H;
        base += rl;
    }
    float o4[4];
    for (int k = 0; k < 4; k++) {
        float s = w[4 * DNN_H + k];
        for (int c = 0; c < DNN_H; c++) s += w[k * DNN_H + c] * h[c];
        o4[k] = s;
    }
    const float ar = 0.3f * o4[0], ai = 0.3f * o4[1];
    out[0] = ar * v[0] - ai * v[1] + 0.05f * o4[2];
    out[1] = ar * v[1] + ai * v[0] + 0.05f * o4[3];
    return 0;
}

float drift_nn_mu(int i)
{
    return (i >= 0 && i < DNN_D) ? DNN_W[i] : 0.0f;
}
