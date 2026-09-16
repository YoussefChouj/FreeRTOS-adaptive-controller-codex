// ------------------------------------------------------------------------------
// MRAC Mathematical Utilities
// ------------------------------------------------------------------------------
// Essential math functions for executing MRAC with Lyapunov stability. All
// routines are floating-point optimized for Cortex-M FPU.
// ------------------------------------------------------------------------------

#include "mrac_math.h"
#include <math.h>

// ------------------------------------------------------------------------------
// Projection Operators
// ------------------------------------------------------------------------------

// The projection operator ensures that the adaptive weights (Theta) do not 
// exceed predetermined physical safety bounds (w_max). It modifies the gradient 
// learning law (y) to gracefully bleed off learning as it approaches the boundary.
float MRAC_Projection(float theta, float y, float w_max, float tol)
{
    float abs_theta = fabsf(theta);
    
    // 1. If theta is well inside bounds (-w_max+tol < theta < w_max-tol), return y
    if (abs_theta <= (w_max - tol)) {
        return y;
    }
    
    // 4. If pushing inward, allow full learning (return y)
    if ((theta > 0.0f && y < 0.0f) || (theta < 0.0f && y > 0.0f)) {
        return y;
    }
    
    // 2 & 3. If theta is outside boundary and pushing outward, hard stop
    if (abs_theta >= w_max) {
        return 0.0f;
    }
    
    // Smoothly scale down y in the tolerance region
    float scale = (w_max - abs_theta) / tol;
    return y * scale;
}

// ------------------------------------------------------------------------------
// Radial Basis Functions (RBF)
// ------------------------------------------------------------------------------

// Simple 1D Gaussian Radial Basis Function.
// Formula: exp(-width * (x - c)^2)
// Used when unstructured nonlinear mapping is configured instead of physics models.
float MRAC_Simple_RBF(float x, float c, float width)
{
    // Calculate squared distance from center
    float dist_sq = (x - c) * (x - c);
    
    // Return exponentiated value bounding between 0.0 and 1.0
    return expf(-width * dist_sq);
}

// ------------------------------------------------------------------------------
// Vector Operations
// ------------------------------------------------------------------------------

// Computes the dot product of a vector with itself.
// Crucial for the normalization step: theta_dot = Gamma * ... / (1 + ||Phi||^2)
float MRAC_VectorNormSquare(const float* vector_array, uint8_t length)
{
    float sum = 0.0f;
    
    // Accumulate sum of squares in a loop
    for (uint8_t i = 0; i < length; i++) {
        sum += vector_array[i] * vector_array[i];
    }
    
    return sum;
}