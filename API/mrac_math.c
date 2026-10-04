/**
 * @module     mrac_math.c
 * @subsystem  control
 * @owner      mrac.c (Stabilizer_Task context): MRAC_Simple_RBF builds the rate/angle RBF regressors,
 *             MRAC_VectorNormSquare gives ||Phi||^2 for the normalised update law. MRAC_Projection has no caller
 *             in the firmware today (git grep, WP-41); it is kept as the standard bounded-weight update.
 * @purpose    Pure math helpers for the MRAC adaptive law: no state, no globals.
 * @inputs     scalars and vectors from the caller.
 * @outputs    return values only.
 */
#include "mrac_math.h"
#include <math.h>

// ------------------------------------------------------------------------------
// Projection Operators
// ------------------------------------------------------------------------------

// Projection operator: keeps an adaptive weight theta inside [-w_max, w_max] by shaping its update y.
//   |theta| <= w_max - tol            -> y            (well inside)
//   update points inward               -> y            (always allowed)
//   |theta| >= w_max, pushing outward  -> 0            (hard stop)
//   in the tol band, pushing outward   -> y * (w_max - |theta|) / tol  (linear bleed-off)
float MRAC_Projection(float theta, float y, float w_max, float tol)
{
    float abs_theta = fabsf(theta);

    if (abs_theta <= (w_max - tol)) {
        return y;
    }

    if ((theta > 0.0f && y < 0.0f) || (theta < 0.0f && y > 0.0f)) {
        return y;
    }

    if (abs_theta >= w_max) {
        return 0.0f;
    }

    float scale = (w_max - abs_theta) / tol;
    return y * scale;
}

// ------------------------------------------------------------------------------
// Radial Basis Functions (RBF)
// ------------------------------------------------------------------------------

// Simple 1D Gaussian Radial Basis Function.
// Formula: exp(-width * (x - c)^2)
// Output in (0, 1]; mrac.c uses width 1.0 for both the rate and the angle regressors.
float MRAC_Simple_RBF(float x, float c, float width)
{
    float dist_sq = (x - c) * (x - c);

    return expf(-width * dist_sq);
}

// ------------------------------------------------------------------------------
// Vector Operations
// ------------------------------------------------------------------------------

// Sum of squares of the first length entries, for the normalisation theta_dot = Gamma * ... / (1 + ||Phi||^2)
float MRAC_VectorNormSquare(const float* vector_array, uint8_t length)
{
    float sum = 0.0f;

    for (uint8_t i = 0; i < length; i++) {
        sum += vector_array[i] * vector_array[i];
    }

    return sum;
}
