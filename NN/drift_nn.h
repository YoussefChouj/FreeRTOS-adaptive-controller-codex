/* drift_nn.h - streaming (one step per call) forward pass of the nnio_fix TCN drift model.
 * Host-tested against PyTorch (test_shadow.py). PROPOSED for firmware shadow mode, not flown.
 * All state is static: no heap (FreeRTOS heap is 20 KB). */
#ifndef DRIFT_NN_H
#define DRIFT_NN_H
#include <stdint.h>
#include "drift_nn_weights.h"

/* Reset the ring buffers (equivalent to the zero left-padding PyTorch sees at take-off). */
void drift_nn_reset(void);

/* One 20 Hz step. f: DNN_D raw features in the NN4_CH order (+ extra covariates), not normalised.
 * v: EKF velocity (x, y) the model rescales. out: body-velocity residual (x, y) in m/s, same frame as v:
 * corrected velocity = v + out (nn_io.corrected integrates it onto the earth track).
 * Returns 0, or -1 if the weight checksum does not match (out is then a copy of v, not a residual). */
int drift_nn_step(const float *f, const float v[2], float out[2]);

/* Training mean of feature i (0 <= i < DNN_D): the in-distribution value for a feature with no valid sample yet. */
float drift_nn_mu(int i);

/* FNV-1a over the weight bytes, compared with DNN_CHECKSUM at the first step. */
uint32_t drift_nn_checksum(void);
#endif
