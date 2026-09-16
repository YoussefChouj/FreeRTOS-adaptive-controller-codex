// ------------------------------------------------------------------------------
// MRAC Mathematical Utilities (Declarations)
// ------------------------------------------------------------------------------
// Helper module covering nonlinear bounds, adaptive projection operators,
// vector normalization, and basis function generation (like RBFs) required by MRAC.
// ------------------------------------------------------------------------------

#ifndef MRAC_MATH_H
#define MRAC_MATH_H

#include <stdint.h>

// ------------------------------------------------------------------------------
// Math Constraints and Bounds
// ------------------------------------------------------------------------------

// Defines boundaries for vector norms to avoid singularities
#define EPSILON_NORM 1e-6f

// ------------------------------------------------------------------------------
// Projection Operators
// ------------------------------------------------------------------------------

// Bounded lyapunov projection to prevent parameter drift
// Parameters:
//   theta : Current adaptive weight
//   y     : Unprojected update rate (gradient)
//   w_max : Safety boundary (limit)
//   tol   : Tolerance region around boundary for smooth fading
float MRAC_Projection(float theta, float y, float w_max, float tol);

// ------------------------------------------------------------------------------
// Radial Basis Functions (RBF)
// ------------------------------------------------------------------------------

// Generates a simple gaussian radial basis activation
// Parameters:
//   x     : Input state (e.g. angle or rate)
//   c     : Center of RBF
//   width : Spread parameter controlling neighbor overlap
float MRAC_Simple_RBF(float x, float c, float width);

// ------------------------------------------------------------------------------
// Vector Helpers
// ------------------------------------------------------------------------------

// Calculates the squared norm of a state vector (used for normalization)
// Parameters:
//   vector_array : Data buffer
//   length       : Array length
float MRAC_VectorNormSquare(const float* vector_array, uint8_t length);

#endif // MRAC_MATH_H