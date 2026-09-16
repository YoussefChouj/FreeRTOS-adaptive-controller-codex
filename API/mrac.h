// ------------------------------------------------------------------------------
// Model Reference Adaptive Control (MRAC) for 6DOF Quadcopter
// ------------------------------------------------------------------------------
// This header file defines the configuration, states, and function prototypes
// for the 4-layer MRAC augmentation (Pitch rate, Roll rate, Yaw rate, Z rate).
// ------------------------------------------------------------------------------

#ifndef MRAC_H
#define MRAC_H

#include <stdint.h>

// ------------------------------------------------------------------------------
// 1. Feature Configuration Flags
// ------------------------------------------------------------------------------
// Use these macros to enable/disable MRAC features at compile time.

// ------------------------------------------------------------------------------
// Payload Configuration Toggle
// ------------------------------------------------------------------------------
#define PAYLOAD_LIGHT 0 // Naked drone (Battery, optical flow, typical sensors, ~0.4kg to 0.6kg)
#define PAYLOAD_HEAVY 1 // Heavy drone (Mounted Jetson Orin, Realsense D435i, T265, ~1.5kg)

// ---> SET YOUR PAYLOAD HERE <---
#define ACTIVE_PAYLOAD PAYLOAD_LIGHT

// ------------------------------------------------------------------------------
// MRAC Output Scalers (Nm to Linear Mixer Units) - Defaults
// ------------------------------------------------------------------------------
#if ACTIVE_PAYLOAD == PAYLOAD_HEAVY
    // Derived algebraically assuming linear ESC mapping [2000 to 4000]:
    // Hover = 2800 (800 units above idle) for 1.5 kg, L = 0.19m.
    #define DEFAULT_MRAC_TO_MIXER_PR    286.0f   // Mixer units per Nm (Pitch/Roll)
    #define DEFAULT_MRAC_TO_MIXER_YAW   458.0f   // Mixer units per Nm (Yaw - 1.6x PR ratio based on inner PID Kp ratio 8/5)
    #define DEFAULT_MRAC_TO_MIXER_Z      54.0f   // Mixer units per N (Vertical Thrust)
#else
    // Derived for lighter payload w/o heavy sensors (~0.4kg to 0.6kg)
    #define DEFAULT_MRAC_TO_MIXER_PR    1170.0f  // Mixer units per Nm (Pitch/Roll)
    #define DEFAULT_MRAC_TO_MIXER_YAW   1872.0f  // Mixer units per Nm (Yaw - 1.6x PR ratio based on inner PID Kp ratio 8/5)
    #define DEFAULT_MRAC_TO_MIXER_Z      222.0f  // Mixer units per N (Vertical Thrust)
#endif



// Regressor type selection (choose ONE)
#define USE_STRUCTURED_UNCERTAINTY     1    // Physics-based: 6 features (bias, angle, rate, drag, un, v) [RECOMMENDED]
#define USE_UNSTRUCTURED_UNCERTAINTY   0    // RBF-based: 6 features (computational cost higher, enable for comparison)

// Core adaptive features
#define ENABLE_MRAC_COMPUTATION        1    // Master switch for adaptive law turn off mrac computations
#define ENABLE_MRAC_OUTPUT_INJECTION   1    // The "Shadow Mode" toggle. If 0, MRAC learns and computes u_ad, but we send 0.0f to the motor mixer.
#define ENABLE_PROJECTION_OPERATOR     1    // Bound adaptive weights to safe limits — MUST be on to prevent drift
#define ENABLE_SIGMA_MODIFICATION      1
#define ENABLE_WEIGHT_NORMALIZATION    1    // Normalize by (1 + theta'theta)
#define ENABLE_LOW_FREQ_LEARNING       1    // L1-style filtered weight updates
#define FIX_LEAKAGE_NORMALIZATION      1    // Remove denom from leakage terms
#define ENABLE_DYNAMIC_REF_MODEL       1    // (legacy compile gate; runtime type now set by DEFAULT_REF_MODEL_TYPE / CMD 0x13)

// Power-on reference-model type (runtime-switchable from dashboard via CMD 0x13):
//   0 = passthrough (xm = r)  [SAFEST — historically stable, matches e_sat/e_freeze calibration]
//   1 = first-order           2 = second-order
#define DEFAULT_REF_MODEL_TYPE         0

// Advanced features
#define ENABLE_PERFORMANCE_RECOVERY    1    // 1st-order low-pass filter on u_ad (omega_u cutoff). NOTE: NOT a full L1 state predictor; lambda_perf/tau_v are unused.
#define INCLUDE_CONTROL_IN_REGRESSOR   1    // Add [un, v] to regressor (important for actuator modeling)

// Future features (disabled for now)
#define ENABLE_LYAPUNOV_BARRIER        0    // Barrier Lyapunov function (Layer 8)
#define ENABLE_DEADZONE                1
#define ENABLE_PSEUDO_CONTROL_HEDGING  1    // PCH to prevent windup    // Gradient deadzone near zero error


// ------------------------------------------------------------------------------
// Operation Mode Flags
// ------------------------------------------------------------------------------
// Define maximum basis size depending on the chosen uncertainty model.
// For structured 6DOF: [Bias, Rate, Quadratic Rate, Cross-Coupling, LOE, Perf Rec]

#define NUM_BASIS   4

#if USE_STRUCTURED_UNCERTAINTY == 1
    //  Structured: [bias, angle, rate, drag] + [un, v] = 4 or 6 features 
    #if INCLUDE_CONTROL_IN_REGRESSOR == 1
        #define MAX_NUM_BASIS   (NUM_BASIS + 2)
    #else
        #define MAX_NUM_BASIS   NUM_BASIS
    #endif
#else
    //  Unstructured (RBF): 
    #if INCLUDE_CONTROL_IN_REGRESSOR == 1
        #define MAX_NUM_BASIS   (2*NUM_BASIS + 2)
    #else
        #define MAX_NUM_BASIS   (2*NUM_BASIS)
    #endif
#endif

// ------------------------------------------------------------------------------
// 3. Definitions and Types
// ------------------------------------------------------------------------------

// Enum for the 4 MRAC axes
typedef enum {
    MRAC_AXIS_PITCH = 0,
    MRAC_AXIS_ROLL  = 1,
    MRAC_AXIS_YAW   = 2,
    MRAC_AXIS_Z     = 3
} MRAC_Axis_e;

// Number of MRAC axes. Equal to the number of distinct enum values above.
#define AXES 4

// Control limits (defaults applied in MRAC_Init)
// Refactored to mutable float fields in MRAC_AxisConfig_t

// Initial gamma/What_limit/What_tol values are set in MRAC_Init() (mrac.c).
// They are runtime-mutable via CMD 0x02 / 0x05 / 0x08 from the ground station.

#define MRAC_DT             0.005f      // [s] 5ms control period (200Hz)


// Configuration structure for an MRAC axis (constants and gains)
typedef struct {
    // Reference Model Parameters (wn, zeta for 2nd order models)
    /// empty for now since we are using a 1st order model for simplicity, but can be added back if needed
    
    // Adaptive law parameters
    //  Per-component learning rates: gamma[i] compensates for regressor magnitude imbalance.
        // Rule of thumb: gamma[i] = gamma_base / (typical |theta[i]|^2)
        // For theta=[bias=1, angle~0.15, rate~0.5, drag~0.25]:
        //   gamma[0]=0.50 (bias, theta^2=1.0)
        //   gamma[1]=3.30 (angle, theta^2=0.023, needs 44x more gain)
        //   gamma[2]=1.00 (rate, theta^2=0.25)
        //   gamma[3]=2.00 (drag, theta^2=0.063) 
    float gamma[MAX_NUM_BASIS]; // [array] Per-weight learning rates (diagonal Gamma matrix)
    float sigma_lf;             // [scalar] Low-frequency leakage (L1-style)
    float sigma;                // [scalar] Sigma-modification leakage
    float gam_f;                // [scalar] Low-pass filter gain for Whatf
    float omega_u;              // [rad/s] L1-style Low-pass filter cutoff parameter for adaptation signal
    
    // Projection operator bounds — per-component arrays (see WLIM/WTOL macros in mrac.h)
    // What_limit[i]: maximum |W_i| for basis i, derived from disturbance budget / theta_max_i
    // What_tol[i]:   soft-zone width (projection starts scaling at limit-tol, reaches 0 at limit)
    float What_limit[MAX_NUM_BASIS]; // per-component max weight magnitude
    float What_tol[MAX_NUM_BASIS];   // per-component soft boundary tolerance (20% of limit)
    // What_lower_limit[i]: per-component asymmetric lower bound on weight magnitude
    // prevents W[un] from going negative and cancelling nominal control
    float What_lower_limit[MAX_NUM_BASIS];
    
    // Performance recovery parameters
    float lambda_perf;          // [rad/s] State predictor bandwidth
    float tau_v;                // [s] Low-pass filter time constant for v
    
    // Physical limits
    float u_max;                // [Nm or N] Max control torque or force
    float mrac_to_mixer;        // Mixer units per torque/thrust
    float J;                    // Rotational: moment of inertia [kg·m²] | Z-axis: vehicle mass [kg]
    
    float e_deadzone;           // [norm] Error deadzone threshold
    float e_freeze;             // [rad/s] Hard-freeze threshold - ua zeroed and weights frozen above this error
    float e_sat;                // [rad/s] Tanh saturation scale for gradient PBe signal
    float k_e;                  // [-] e-modification: extra leakage proportional to |e|

    // Reference model parameters (runtime-selectable type via CMD 0x13)
    float ref_model_bw;         // [rad/s] Reference model bandwidth (Am for 1st-order, wn for 2nd-order)
    float ref_model_zeta;       // [-] Damping ratio for 2nd-order reference model (~0.8 = mild overshoot)
    float P_lyap;               // [legacy] scalar Lyapunov gain field — UNUSED (ADR-0007)

    // 2nd-order matrix-P state-space adaptive law (ADR-0007; active only when
    // ref_model_type==2). Lyapunov drive  s = e*Pe + e_dot*Pedot  where Pe,Pedot are
    // the 2nd column of P (B=[0;1]), computed live from (ref_model_bw, ref_model_zeta, Q):
    //   Pe = Q1/(2*wn^2),  Pedot = (Q1/wn^2 + Q2)/(4*zeta*wn).  Q1=wn -> Pe=1/(2*wn).
    float ref_Q1;               // [-] Lyapunov Q diagonal: rate-error weight
    float ref_Q2;               // [-] Lyapunov Q diagonal: rate-derivative weight
    float wc_edot;              // [rad/s] LPF cutoff for the finite-difference rate derivative

} MRAC_AxisConfig_t;

// State structure for an MRAC axis (mutable runtime data)
typedef struct {
    // Reference model states (xm, omega_m)
    float xm;           // Reference state (e.g. desired rate)
    float xm_dot;       // Reference state velocity (2nd-order model); 0 for passthrough/1st-order

    // Plant states (x, omega)
    float x;            // Actual plant state (e.g. measured gyro rate)
    float r;            // Latched rate command into this axis (rad/s) — for telemetry / system-ID frame
    
    // Tracking errors (e)
    float e;            // Tracking error (x - xm)
    
    // Regressor vector (Phi array of size MAX_BASIS_FEATURES)
    float Phi[MAX_NUM_BASIS];
    
    // Adaptive weights (Theta array of size MAX_BASIS_FEATURES)
    float Theta[MAX_NUM_BASIS];
    // Low-frequency filtered weight copy for L1-style leakage (tracks Theta with lag)
    float Whatf[MAX_NUM_BASIS];
    
    // Computed nominal control output (u_nom)
    float u_nom;
    
    // Computed adaptive control output (u_ad)
    float u_ad;
    
    // Saturation deficit used for Pseudo Control Hedging
    float u_def;        // u_cmd - u_actual_after_mixer

    // Rate-derivative estimator for the 2nd-order state-space law (ADR-0007)
    float x_prev;       // previous-tick plant rate (finite-difference derivative)
    float xdot_f;       // LPF'd plant-rate derivative (angular-accel estimate)
    float e_dot;        // tracking-error derivative (xdot_f - xm_dot)
} MRAC_AxisState_t;

// Main MRAC structure holding all 4 axes
typedef struct {
    MRAC_AxisState_t pitch;
    MRAC_AxisState_t roll;
    MRAC_AxisState_t yaw;
    MRAC_AxisState_t z_rate;
} MRAC_State_t;

typedef struct {
    uint8_t adaptation_on;          // Master adaptive switch
    uint8_t projection_on;          // Weight projection
    uint8_t deadzone_on;            // Gradient deadzone
    uint8_t hard_freeze_on;         // Hard freeze on transient error spike
    uint8_t tanh_saturation_on;     // Tanh soft saturation on PBe
    uint8_t e_modification_on;      // e-modification term
    uint8_t l1_filtering_on;        // L1 adaptive low-pass filter on u_ad
    uint8_t axis_enable_pitch;
    uint8_t axis_enable_roll;
    uint8_t axis_enable_yaw;
    uint8_t output_injection_on;    // Runtime shadow-mode gate: 0 = MRAC learns but motors see pure PID; 1 = u_ad injected
    uint8_t id_frame_on;            // High-rate system-ID telemetry frame (0x03 @ 100Hz, replaces A/B while set)
    uint8_t of_frame_on;            // OF calibration/fusion raw telemetry frame (0x05 @ 200Hz, replaces A/B while set)
    uint8_t ref_model_type;         // Reference model: 0 = passthrough (xm=r), 1 = first-order, 2 = second-order
} MRAC_FeatureFlags_t;

// ------------------------------------------------------------------------------
// External Globals
// ------------------------------------------------------------------------------
extern MRAC_State_t mrac_state;
extern MRAC_FeatureFlags_t mrac_flags;
extern MRAC_AxisConfig_t mrac_config_pitch;
extern MRAC_AxisConfig_t mrac_config_roll;
extern MRAC_AxisConfig_t mrac_config_yaw;
extern MRAC_AxisConfig_t mrac_config_z;

// ------------------------------------------------------------------------------
// 5. Opt-in sigma-prior attractor (prior-D / ADR-0013 D5, D10)
// ------------------------------------------------------------------------------
// The default firmware build does NOT change: the terms below are guarded by
// MRAC_ENABLE_SIGMA_PRIOR. When the flag is undefined, none of the symbols are
// emitted and `mrac.c`'s object code is bit-identical to the pre-change build
// (sil_gate parity test enforces this). The flag is OFF in the production
// JX_FLY.uvprojx.
//
// When the flag IS defined, the gradient update in `MRAC_UpdateAxis` gains a
// sibling σ-mod term that pulls the adaptive weights toward a scenario-
// conditioned `Theta_prior` at rate `sigma_prior`:
//
//     y = γ · (grad − σ_lf·(Θ − Whatf) − σ_eff·Θ − σ_prior·(Θ − Θ_prior))
//
// Equilibrium shifts from Θ=0 to Θ=Θ_prior; the σ-mod UUB Lyapunov argument
// carries over directly (gradient-style term, bounded by projection).
//
// `Theta_prior` is zero-initialised via file-scope zero-init. `sigma_prior`
// defaults to 0.0 (no effect). `MRAC_SetPrior` / `MRAC_GetPrior` provide a
// critical-section-protected read/write path from the ground-station command
// dispatch (NOT wired in this slice — see journal "Operator decision").
#ifdef MRAC_ENABLE_SIGMA_PRIOR

/* Scenario-conditioned prior per axis. Default zero-init. */
extern float Theta_prior[AXES][MAX_NUM_BASIS];

/* Common scalar attractor rate. Default 0.0. */
extern float sigma_prior;

/* Write `arr[:MAX_NUM_BASIS]` into `Theta_prior[axis][:]`. Critical-section
 * protected so a ground-station command path can write safely against the
 * 200 Hz task-context update. `axis` is `MRAC_Axis_e`. */
void MRAC_SetPrior(uint8_t axis, const float *arr);

/* Read `Theta_prior[axis][:]` into `out_arr[:MAX_NUM_BASIS]`. */
void MRAC_GetPrior(uint8_t axis, float *out_arr);

#endif /* MRAC_ENABLE_SIGMA_PRIOR */

#include "robot_types.h"

// ------------------------------------------------------------------------------
// 4. Public Function Prototypes
// ------------------------------------------------------------------------------

// Initialize MRAC states, base configurations, and zero the adaptive weights
void MRAC_Init(void);

// Reset all adaptive weights and align reference models with plant state
void MRAC_Reset(void);

// Main periodic MRAC controller computation (Called deeply from StabilizerTask)
void MRAC_Control(const CtrlerTypeDef* current_state);

#endif // MRAC_H
