// ------------------------------------------------------------------------------
// MRAC Implementation File
// ------------------------------------------------------------------------------
// Contains the core adaptive control logic for the 4 axes of a free-flying
// quadcopter (Pitch, Roll, Yaw, Z-Axis).
// ------------------------------------------------------------------------------

#include "mrac.h"
#include "mrac_math.h"
#include <math.h>
#include "imu_update.h"

// Adaptive state lives in the CPU-only 64 KB CCM (0x10000000); USER/JX_FLY.sct places section
// MRAC_CCM there and __main still zeroes it. No DMA may read or write an object tagged MRAC_CCM.
// CYCCNT is already running: RPM_DwtInit enables it at boot. Host builds have neither.
#ifdef __CC_ARM
    #define MRAC_CCM __attribute__((section("MRAC_CCM"), zero_init))
    #define MRAC_CYC_NOW() (DWT->CYCCNT)
#else
    #define MRAC_CCM
    #define MRAC_CYC_NOW() 0U
#endif

// Global instance of the MRAC runtime states
MRAC_State_t mrac_state MRAC_CCM;
MRAC_FeatureFlags_t mrac_flags = {0};

// Global configurations for the 4 axes
MRAC_AxisConfig_t mrac_config_pitch MRAC_CCM;
MRAC_AxisConfig_t mrac_config_roll MRAC_CCM;
MRAC_AxisConfig_t mrac_config_yaw MRAC_CCM;
MRAC_AxisConfig_t mrac_config_z MRAC_CCM;

/* Simplex fallback. Defaults are inert: mode 0, variant 0, fade 1. */
MRAC_Simplex_t mrac_simplex = {0, 0, 0, 0, 0, 0, {0, 0, 0, 0}, 0, 200, 40,
                               3.14f, 3.14f, 1.0e6f, 1.0f};

MRAC_Inj_t mrac_inj = {0};
volatile uint8_t mrac_in_armed = 0;
volatile uint8_t mrac_in_phase = 0;
uint8_t mrac_var_id[AXES];
int8_t mrac_ref_type_eff[AXES];

static void MRAC_VariantSnap(MRAC_AxisState_t *st);

void MRAC_GateStep(void)
{
    uint8_t is_flying;
    uint8_t basic_gate;
    uint8_t disarm_edge;
    uint8_t inj_on;
    float p;
    
    is_flying = (mrac_in_phase == MRAC_PHASE_FLYING || mrac_in_phase == MRAC_PHASE_LANDING);

    // 1. fly_ticks
    if (mrac_in_armed && is_flying) {
        if (mrac_inj.fly_ticks < 65535) {
            mrac_inj.fly_ticks++;
        }
        mrac_inj.not_flying_ticks = 0;
    } else {
        if (mrac_inj.not_flying_ticks < MRAC_FLY_HYST_TICKS) {
            mrac_inj.not_flying_ticks++;
        } else {
            mrac_inj.fly_ticks = 0;
        }
    }
    
    // 2. basic learn_gate
    basic_gate = (mrac_in_armed && is_flying && 
                  mrac_inj.fly_ticks >= (uint16_t)(MRAC_LEARN_HOLD_S / MRAC_DT));
                          
    // 3. Disarm edge, or phase LANDED / GROUND_IDLE
    disarm_edge = (mrac_inj.prev_armed && !mrac_in_armed);
    if (disarm_edge || !is_flying) {
        mrac_inj.inj_alpha = 0.0f;
        mrac_inj.ramp_p = 0.0f;
        mrac_inj.learn_gate = 0;
        if (disarm_edge) {
            MRAC_ResetWeights();
            mrac_inj.freeze_shadow = 0; // reset freeze on disarm
        }
    }
    mrac_inj.prev_armed = mrac_in_armed;
    
    inj_on = mrac_flags.output_injection_on;
    
    // 4. Injection rising edge
    if (inj_on && !mrac_inj.prev_injection_on) {
        MRAC_ResetWeights();
        mrac_inj.ramp_p = 0.0f;
        mrac_inj.inj_alpha = 0.0f;
        mrac_inj.freeze_shadow = 0;
    }
    
    // 5 & 6. Ramp and freeze logic
    if (inj_on) {
        if (basic_gate) {
            mrac_inj.ramp_p += MRAC_DT / MRAC_INJ_T_UP;
            if (mrac_inj.ramp_p > 1.0f) mrac_inj.ramp_p = 1.0f;
            p = mrac_inj.ramp_p;
            mrac_inj.inj_alpha = 3.0f * p * p - 2.0f * p * p * p;
        } else {
            mrac_inj.ramp_p = 0.0f;
            mrac_inj.inj_alpha = 0.0f;
        }
        mrac_inj.learn_gate = basic_gate;
    } else {
        if (mrac_inj.inj_alpha > 0.0f) {
            mrac_inj.inj_alpha -= MRAC_DT / MRAC_INJ_T_DN;
            if (mrac_inj.inj_alpha <= 0.0f) {
                mrac_inj.inj_alpha = 0.0f;
                mrac_inj.freeze_shadow = 1;
            }
        } else {
            if (mrac_inj.prev_injection_on) {
                mrac_inj.freeze_shadow = 1;
            }
        }
        mrac_inj.ramp_p = 0.0f;
        mrac_inj.learn_gate = basic_gate && !mrac_inj.freeze_shadow;
    }
    
    if (disarm_edge || !is_flying) {
        mrac_inj.learn_gate = 0;
        mrac_inj.inj_alpha = 0.0f;
    }
    
    mrac_inj.prev_injection_on = inj_on;
}


// Supplied by SINS/baro-IMU fusion � wire this before flight test.

// ------------------------------------------------------------------------------
// Opt-in sigma-prior attractor (prior-D / ADR-0013 D5, D10)
// ------------------------------------------------------------------------------
// Guarded by MRAC_ENABLE_SIGMA_PRIOR. Default build is byte-identical to the
// pre-change build (sil_gate parity test enforces). When the flag is defined
// the gradient update in MRAC_UpdateAxis pulls Theta toward Theta_prior at
// rate sigma_prior. File-scope zero-init matches the existing convention.
//
// Axis-indexed storage: Theta_prior[MRAC_AXIS_*][:MAX_NUM_BASIS].
// ---------------------------------------------------------------------------
#ifdef MRAC_ENABLE_SIGMA_PRIOR
float Theta_prior[AXES][MAX_NUM_BASIS];
float sigma_prior = 0.0f;
#endif

// TODO [HW-PARAM]: identify from motor characterization
#define MOTOR_CT 0.0f




// ------------------------------------------------------------------------------
// Helper: Inverse Mixer
// ------------------------------------------------------------------------------
// Extracts the equivalent mechanical torque/thrust generated by the motors 
// purely from the current actual PWM values and Battery Voltage.
// Needed for actuator loss-of-effectiveness (LOE) estimation and 
// calculating saturation limits for hedging.
static void MRAC_InverseMixer(float current_vbatt, float pwm1, float pwm2, float pwm3, float pwm4,
                              float* u_pitch_actual, float* u_roll_actual, float* u_yaw_actual, float* u_z_actual)
{
    // TODO [HW-PARAM]: Tune c_T thrust coefficient and mixer geometry.
    // Assuming standard Quad-X configuration:
    // Thrust_i = MOTOR_CT * (pwm_i * current_vbatt)^2
    
    // float t1 = MOTOR_CT * (pwm1 * current_vbatt) * (pwm1 * current_vbatt);
    // float t2 = MOTOR_CT * (pwm2 * current_vbatt) * (pwm2 * current_vbatt);
    // float t3 = MOTOR_CT * (pwm3 * current_vbatt) * (pwm3 * current_vbatt);
    // float t4 = MOTOR_CT * (pwm4 * current_vbatt) * (pwm4 * current_vbatt);
    
    // Reverse mix for Quad-X
    // *u_pitch_actual = (t1 + t2 - t3 - t4);
    // *u_roll_actual  = (t1 - t2 + t3 - t4);
    // *u_yaw_actual   = (t1 - t2 - t3 + t4);
    // *u_z_actual     = (t1 + t2 + t3 + t4); // Total vertical thrust
    
    *u_pitch_actual = 0.0f; // Placeholder until wired
    *u_roll_actual  = 0.0f; // Placeholder until wired
    *u_yaw_actual   = 0.0f; // Placeholder until wired
    *u_z_actual     = 0.0f; // Placeholder until wired
}

// ------------------------------------------------------------------------------
// Helper: Regressor Generator (Structured Physics-Based)
// ------------------------------------------------------------------------------
// Populates the Phi vector mirroring 6DOF quadcopter physics.

static void MRAC_ProjectGradient(float grad[], const float Theta[], int num_basis,
                                 const float* limit, const float* tol, const float* lower_limit)
{
    int i;

    for (i = 0; i < num_basis; i++) {
        float upper;
        float lower;
        float band;
        float g;
        float w;
        float scale;

        upper = limit[i];
        lower = lower_limit[i];
        band = tol[i];
        g = grad[i];
        w = Theta[i];

        if (band <= 0.0f) {
            if ((w >= upper && g > 0.0f) || (w <= lower && g < 0.0f)) {
                grad[i] = 0.0f;
            }
            continue;
        }

        if (g > 0.0f) {
            if (w >= upper) {
                g = 0.0f;
            } else if (w > (upper - band)) {
                scale = (upper - w) / band;
                if (scale < 0.0f) {
                    scale = 0.0f;
                }
                g *= scale;
            }
        } else if (g < 0.0f) {
            if (w <= lower) {
                g = 0.0f;
            } else if (w < (lower + band)) {
                scale = (w - lower) / band;
                if (scale < 0.0f) {
                    scale = 0.0f;
                }
                g *= scale;
            }
        }

        grad[i] = g;
    }
}


const MRAC_FeatureDesc_t mrac_feature_desc[MRAC_N_FEATURES] = {
    {0, "bias",      MRAC_BLK_STRUCT, MRAC_GRP_BIAS},
    {1, "rate",      MRAC_BLK_STRUCT, MRAC_GRP_RATE},
    {2, "rate_tanh", MRAC_BLK_STRUCT, MRAC_GRP_AERO},
    {3, "cross",     MRAC_BLK_STRUCT, MRAC_GRP_COUPLING},
    {4, "u_nom",     MRAC_BLK_STRUCT, MRAC_GRP_CTRL},
    {5, "xm",        MRAC_BLK_STRUCT, MRAC_GRP_REF}
#if MRAC_VARIANT == MRAC_VARIANT_STRUCT6_RBF12
    /* rbf_<rate centre>_<angle centre>: 4 rate x 3 angle Gaussians, rate-major (MRAC_GenRBF) */
   ,{6,  "rbf_r0_a0", MRAC_BLK_RBF, MRAC_GRP_RBF},
    {7,  "rbf_r0_a1", MRAC_BLK_RBF, MRAC_GRP_RBF},
    {8,  "rbf_r0_a2", MRAC_BLK_RBF, MRAC_GRP_RBF},
    {9,  "rbf_r1_a0", MRAC_BLK_RBF, MRAC_GRP_RBF},
    {10, "rbf_r1_a1", MRAC_BLK_RBF, MRAC_GRP_RBF},
    {11, "rbf_r1_a2", MRAC_BLK_RBF, MRAC_GRP_RBF},
    {12, "rbf_r2_a0", MRAC_BLK_RBF, MRAC_GRP_RBF},
    {13, "rbf_r2_a1", MRAC_BLK_RBF, MRAC_GRP_RBF},
    {14, "rbf_r2_a2", MRAC_BLK_RBF, MRAC_GRP_RBF},
    {15, "rbf_r3_a0", MRAC_BLK_RBF, MRAC_GRP_RBF},
    {16, "rbf_r3_a1", MRAC_BLK_RBF, MRAC_GRP_RBF},
    {17, "rbf_r3_a2", MRAC_BLK_RBF, MRAC_GRP_RBF}
#endif
};
const uint8_t mrac_n_features = MRAC_N_FEATURES;

static void MRAC_GenStructured(MRAC_Axis_e axis, const MRAC_Bus_t *bus, float *phi)
{
    
    phi[0] = 1.0f;
    phi[1] = bus->x;
     
    phi[2] = bus->x * tanhf(bus->x);
#if INCLUDE_CONTROL_IN_REGRESSOR == 1
    if (axis == MRAC_AXIS_Z || axis == MRAC_AXIS_YAW) {
        phi[3] = 0.0f;
    } else {
        phi[3] = bus->cross;
    }
    phi[4] = bus->u_nom;
    phi[5] = bus->xm;
#else
    if (axis == MRAC_AXIS_Z || axis == MRAC_AXIS_YAW) {
        phi[3] = bus->u_nom;
    } else {
        phi[3] = bus->cross;
    }
#endif
}

MRAC_Bus_t mrac_bus[AXES] MRAC_CCM;

#if MRAC_VARIANT == MRAC_VARIANT_STRUCT6_RBF12
/* V3 grid centres in normalised units (rate/rbf_rate_scale, angle/rbf_ang_scale), width 1.
 * PROPOSED: evenly spread over +-1.5 (rate) and +-1 (angle = the 15 deg tilt limit at scale 0.26). */
static const float mrac_rbf_rate_c[4] = {-1.5f, -0.5f, 0.5f, 1.5f};
static const float mrac_rbf_ang_c[3]  = {-1.0f,  0.0f, 1.0f};

/* V3 block: phi[k] = g_rate[k/3] * g_ang[k%3], separable (4 + 3 expf). Pitch and roll only; yaw, z,
 * rbf_on 0 and a non-finite input give phi = 0, so the block adds nothing to Phi_sq or u_ad. */
static void MRAC_GenRBF(MRAC_Axis_e axis, const MRAC_Bus_t *bus, float *phi)
{
    const MRAC_AxisConfig_t *cfg = (axis == MRAC_AXIS_PITCH) ? &mrac_config_pitch : &mrac_config_roll;
    float gr[4];
    float ga[3];
    float xr;
    float xa;
    int i, j;

    xr = 0.0f;
    xa = 0.0f;
    if (axis == MRAC_AXIS_PITCH || axis == MRAC_AXIS_ROLL) {
        xr = bus->x / cfg->rbf_rate_scale;
        xa = ((axis == MRAC_AXIS_PITCH) ? imu_data.pit : imu_data.rol) / cfg->rbf_ang_scale;
    }
    if ((axis != MRAC_AXIS_PITCH && axis != MRAC_AXIS_ROLL) || cfg->rbf_on < 0.5f ||
        !(xr - xr == 0.0f) || !(xa - xa == 0.0f)) {
        for (i = 0; i < MRAC_N_RBF; i++) phi[i] = 0.0f;
        return;
    }
    for (i = 0; i < 4; i++) gr[i] = MRAC_Simple_RBF(xr, mrac_rbf_rate_c[i], 1.0f);
    for (j = 0; j < 3; j++) ga[j] = MRAC_Simple_RBF(xa, mrac_rbf_ang_c[j], 1.0f);
    for (i = 0; i < 4; i++) {
        for (j = 0; j < 3; j++) phi[i * 3 + j] = gr[i] * ga[j];
    }
}
#endif

const MRAC_BlockDesc_t mrac_block_table[] = {
    {MRAC_BLK_STRUCT, 0, MRAC_N_STRUCT, MRAC_GenStructured}
#if MRAC_VARIANT == MRAC_VARIANT_STRUCT6_RBF12
   ,{MRAC_BLK_RBF, MRAC_N_STRUCT, MRAC_N_RBF, MRAC_GenRBF}
#endif
};
#define MRAC_N_BLOCKS ((int)(sizeof(mrac_block_table) / sizeof(mrac_block_table[0])))

float mrac_g_gamma[AXES][MRAC_N_GROUPS] MRAC_CCM;
float mrac_g_sigma[AXES][MRAC_N_GROUPS] MRAC_CCM;
float mrac_g_phi[AXES][MRAC_N_GROUPS] MRAC_CCM;
float mrac_u_ff[AXES] MRAC_CCM;

MRAC_Cyc_t mrac_cyc;

static void MRAC_L2_Update(void)
{
}

static float MRAC_L3_Feedforward(MRAC_Axis_e axis, const MRAC_Bus_t *bus)
{
    (void)axis;
    (void)bus;
    return 0.0f; // consumed from stage S4
}

// Closes the cycle record of one MRAC_UpdateAxis call. t0 = entry, t1..t2 = bus fill,
// t2..t3 = block generators, t3..now = the rest. Unsigned deltas survive a CYCCNT wrap; a
// SendProf_Init CYCCNT reset inside a call gives one huge max, clear mrac_cyc.max.
static void MRAC_CycEnd(MRAC_Axis_e axis_id, uint32_t t0, uint32_t t1, uint32_t t2, uint32_t t3)
{
    uint32_t t4 = MRAC_CYC_NOW();
    MRAC_CycSet_t *last = &mrac_cyc.last[axis_id];
    MRAC_CycSet_t *max = &mrac_cyc.max[axis_id];

    last->bus    = t2 - t1;
    last->blocks = t3 - t2;
    last->law    = t4 - t3;
    last->total  = t4 - t0;
    if (last->bus    > max->bus)    max->bus    = last->bus;
    if (last->blocks > max->blocks) max->blocks = last->blocks;
    if (last->law    > max->law)    max->law    = last->law;
    if (last->total  > max->total)  max->total  = last->total;
}

// ------------------------------------------------------------------------------
// Helper: Axis Update Core

// ------------------------------------------------------------------------------
// Runs the core MRAC algorithm for a single axis.
static void MRAC_UpdateAxis(MRAC_Axis_e axis_id, MRAC_AxisState_t* state, const MRAC_AxisConfig_t* config, float cross_coupling, float r)
{
    float P = 1.0f;
    float P_e = 0.0f;
    float P_edot = 0.0f;
    float s;
    float raw_xdot;
    float Phi_sq;
    float denom;
    static float grad[MAX_NUM_BASIS] MRAC_CCM;
    float y;
    float PBe;
    float sigma_e;
    float sigma_eff;
    float sigma_lf_active;
    float raw_u_ad;
    int do_adaptation;
    int i;
    int n;              // features in use: MRAC_N_STRUCT while V3 rbf_on is 0, else MRAC_N_FEATURES
    int ref_type;       // reference-model type this axis runs (global flag unless V1 ref_type >= 0)
    float r_m;          // command into the reference model (r, or r delayed by V1 ref_delay_s)
    uint8_t vid;
    uint32_t t0, t1, t2, t3;

    t0 = MRAC_CYC_NOW();
    ref_type = mrac_flags.ref_model_type;
    vid = 0U;
    n = MRAC_N_FEATURES;
#if MRAC_VARIANT == MRAC_VARIANT_STRUCT6_RBF12
    if (config->rbf_on < 0.5f) {
        n = MRAC_N_STRUCT;
    } else {
        vid |= MRAC_VID_RBF;
    }
#endif
#if MRAC_ENABLE_REFMODEL_V2 == 1
    // V1: per-axis type and command delay. The ring is written every tick so a delay switched on
    // later starts from real history; delay 0 reads the slot just written, i.e. r itself.
    {
        int d = (int)(config->ref_delay_s / MRAC_DT + 0.5f);
        if (d < 0) d = 0;
        if (d > MRAC_REF_BUF - 1) d = MRAC_REF_BUF - 1;
        state->r_buf[state->r_idx] = r;
        r_m = state->r_buf[(state->r_idx + MRAC_REF_BUF - d) % MRAC_REF_BUF];
        state->r_idx = (uint8_t)((state->r_idx + 1U) % MRAC_REF_BUF);
        if (d > 0) vid |= MRAC_VID_DELAY;
    }
    if (config->ref_type > -0.5f) {
        ref_type = (int)(config->ref_type + 0.5f);
        vid |= MRAC_VID_REF_TYPE;
    }
#else
    r_m = r;
#endif
    mrac_ref_type_eff[axis_id] = (int8_t)ref_type;

    // 1. Update reference model dynamics (runtime-selectable via mrac_flags.ref_model_type)
    //    Adaptive-law gain: 1st-order/passthrough use the scalar heuristic P (ADR-0003);
    //    the 2nd-order case uses the FULL matrix-P state-space drive (ADR-0007, supersedes
    //    ADR-0003 for type 2) — see the s = e*Pe + e_dot*Pedot selection in step 5.
    state->r = r; // latch command for telemetry / system-ID frame
    switch (ref_type) {
        case 2: {
            // 2nd-order: xm_ddot = wn^2 (r - xm) - 2*zeta*wn*xm_dot  (semi-implicit Euler, stable for DT*wn < 2)
            float wn  = config->ref_model_bw;
            float acc = wn * wn * (r_m - state->xm) - 2.0f * config->ref_model_zeta * wn * state->xm_dot;
            state->xm_dot += MRAC_DT * acc;
            state->xm     += MRAC_DT * state->xm_dot;
            // P unused for the drive here; matrix-P (Pe,Pedot) is formed in step 5.
            break;
        }
        case 1: {
            // 1st-order: xm_dot = bw*(r - xm), unity DC gain. P solves 2*Am*P = 1.
            float bw = config->ref_model_bw;
            float dx;
            if (bw < 0.1f) bw = 0.1f;   // P = 1/(2*bw): bw 0 divides by zero
            dx = bw * (r_m - state->xm);
            state->xm    += MRAC_DT * dx;
            state->xm_dot = dx;
            P = 1.0f / (2.0f * bw);
            break;
        }
        case 0:
        default:
            // Passthrough: instantaneous command, infinite-bandwidth reference. e = -(PID error).
            state->xm = r_m;
            state->xm_dot = 0.0f;
            P = 1.0f;
            break;
    }
#if MRAC_ENABLE_PERF_RECOVERY == 1
    // PR, closed-loop reference model (WP-29 [E5]): pull xm toward the plant, xm' += crm_ell*(x - xm).
    // Types 1/2 only; passthrough has no model state. crm_ell*DT <= 0.25 (MRAC_VariantParamSet).
    if (config->crm_ell > 0.0f && ref_type != 0) {
        state->xm += MRAC_DT * config->crm_ell * (state->x - state->xm);
        vid |= MRAC_VID_CRM;
    }
#endif

    // 2. Compute tracking error (e = x - xm)
    state->e = state->x - state->xm;

    // 2b. Filtered finite-difference rate derivative -> tracking-error derivative.
    //     Drives the 2nd-order matrix-P law (e_dot); kept warm every tick (also for
    //     telemetry) even in deadzone/freeze. ADR-0007.
    raw_xdot = (state->x - state->x_prev) / MRAC_DT;
    state->xdot_f += MRAC_DT * config->wc_edot * (raw_xdot - state->xdot_f);
    state->x_prev = state->x;
    state->e_dot  = state->xdot_f - state->xm_dot;

#if MRAC_ENABLE_3L == 1
    // 3L layer 1: leaky integral of e = attitude error against the model (1 s leak so a standing
    // command offset cannot wind it up), bounded by e_sat. Kept warm every tick; used only if lam_ang > 0.
    state->e_int += MRAC_DT * (state->e - state->e_int);
    if (config->e_sat > 0.0f) {
        if (state->e_int >  config->e_sat) state->e_int =  config->e_sat;
        if (state->e_int < -config->e_sat) state->e_int = -config->e_sat;
    }
    if (!(state->e_int - state->e_int == 0.0f)) state->e_int = 0.0f;
#endif

    // 3. Compute nominal control (done externally)
    
    // 4. Generate Basis/Regressor vector (Phi)
    t1 = MRAC_CYC_NOW();
    mrac_bus[axis_id].x = state->x;
    mrac_bus[axis_id].xm = state->xm;
    mrac_bus[axis_id].xm_dot = state->xm_dot;
    mrac_bus[axis_id].e = state->e;
    mrac_bus[axis_id].e_dot = state->e_dot;
    mrac_bus[axis_id].u_nom = state->u_nom;
    mrac_bus[axis_id].cross = cross_coupling;
    mrac_bus[axis_id].r = r;
    t2 = MRAC_CYC_NOW();

    for (i = 0; i < MRAC_N_BLOCKS; i++) {
        mrac_block_table[i].generator(axis_id, &mrac_bus[axis_id], state->Phi + mrac_block_table[i].first);
    }
    t3 = MRAC_CYC_NOW();
    mrac_u_ff[axis_id] = MRAC_L3_Feedforward(axis_id, &mrac_bus[axis_id]);
    
    // 5. Update adaptive weights using Lyapunov gradient descent
    Phi_sq = MRAC_VectorNormSquare(state->Phi, (uint8_t)n);
    denom = 1.0f + Phi_sq;

    // Proceed with adaptation only if error is outside deadzone
    do_adaptation = (!mrac_flags.deadzone_on) || (fabsf(state->e) >= config->e_deadzone);

    // Hard freeze: zero adaptive output and skip updates during large error spikes
    if (mrac_flags.hard_freeze_on && config->e_freeze > 0.0f && fabsf(state->e) > config->e_freeze) {
        // Freeze pauses adaptation/output only; Theta is intentionally preserved.
        state->u_ad = 0.0f;
        MRAC_CycEnd(axis_id, t0, t1, t2, t3);
        return;
    }

    /* Simplex: freeze Theta/Whatf (never reset) while tripped or in the PID-only
     * variant; u_ad is still computed and faded at the injection point. */
    if (mrac_simplex.tripped || mrac_simplex.variant == 1) {
        do_adaptation = 0;
    }

    PBe = state->e;

    // Tanh saturation: bound effective error to +/-e_sat regardless of spike magnitude
    if (mrac_flags.tanh_saturation_on && config->e_sat > 0.0f) {
        PBe = config->e_sat * tanhf(PBe / config->e_sat);
    }

    // Lyapunov drive signal s (= e_v^T P B). Scalar heuristic for passthrough/1st-order
    // (ADR-0003); full state-space [e, e_dot]^T P [0;1] = e*Pe + e_dot*Pedot for the
    // 2nd-order matrix-P law (ADR-0007). e_dot is LPF-bounded and not separately
    // tanh-saturated in Phase 1 (e is, via PBe).
    if (ref_type == 2) {
        float wn = config->ref_model_bw;
        float zeta = config->ref_model_zeta;
        float a0;
        float a1;
        if (wn < 0.1f) wn = 0.1f;       // a0 and a1 are denominators below
        if (zeta < 0.1f) zeta = 0.1f;
        a0 = wn * wn;
        a1 = 2.0f * zeta * wn;
        P_e    = config->ref_Q1 / (2.0f * a0);
        P_edot = (config->ref_Q1 / a0 + config->ref_Q2) / (2.0f * a1);
        s = PBe * P_e + state->e_dot * P_edot;
    } else {
        s = PBe * P;
    }
#if MRAC_ENABLE_REFMODEL_V2 == 1
    // V1 normalized drive (roadmap Q1 finding 4): P = 1/(2bw) and P_e = 1/(2wn^2) shrink the drive
    // 88-3900x and the gamma-scaled leak keeps theta = grad/sigma put, so drop P: s = PBe (+ lam_edot*e_dot).
    if (config->drive_norm > 0.5f) {
        s = (ref_type == 2) ? (PBe + config->lam_edot * state->e_dot) : PBe;
        vid |= MRAC_VID_DRIVE_NORM;
    }
#endif
#if MRAC_ENABLE_3L == 1
    // 3L layer 1 drive (sim ctrl_mrac3l.py:133): s = rate error + lam_ang * angle error.
    if (config->lam_ang > 0.0f) {
        s += config->lam_ang * state->e_int;
        vid |= MRAC_VID_3L;
    }
#endif
#if MRAC_ENABLE_PERF_RECOVERY == 1
    if (config->kappa_pr > 0.0f) vid |= MRAC_VID_KAPPA_PR;
#endif
#if MRAC_ENABLE_SATAWARE == 1
    if (config->mu_sat > 0.0f) vid |= MRAC_VID_SATAWARE;
#endif
    mrac_var_id[axis_id] = vid;

    if (mrac_flags.adaptation_on && do_adaptation && mrac_inj.learn_gate) {
        float theta_scale = mrac_flags.output_injection_on ? mrac_inj.inj_alpha : 1.0f;
        sigma_e = 0.0f;
        if (mrac_flags.e_modification_on) {
            sigma_e = config->k_e * fabsf(state->e);
        }
        sigma_eff = config->sigma + sigma_e;
        sigma_lf_active = mrac_flags.l1_filtering_on ? config->sigma_lf : 0.0f;

        for (i = 0; i < n; i++) {
            grad[i] = (-s * state->Phi[i]) / denom;
        }

#ifdef MRAC_ENABLE_SIGMA_PRIOR
        /* Opt-in prior attractor folded into the gradient BEFORE projection.
         * It is the gradient of (1/2)*sigma_prior*||Theta-Theta_prior||^2, so
         * projection must bound the combined gradient — otherwise a large
         * sigma_prior pushes Theta past What_limit (prior-D-fix). */
        if (sigma_prior != 0.0f) {
            for (i = 0; i < n; i++) {
                grad[i] -= sigma_prior * (state->Theta[i] - Theta_prior[axis_id][i]);
            }
        }
#endif

        if (mrac_flags.projection_on) {
            MRAC_ProjectGradient(grad, state->Theta, n,
                                 config->What_limit, config->What_tol, config->What_lower_limit);
        }

        for (i = 0; i < n; i++) {
#if FIX_LEAKAGE_NORMALIZATION == 1
            y = config->gamma[i] * mrac_g_gamma[axis_id][mrac_feature_desc[i].group] * (grad[i]
                - sigma_lf_active * (state->Theta[i] - state->Whatf[i])
                - sigma_eff * mrac_g_sigma[axis_id][mrac_feature_desc[i].group] * state->Theta[i]
                );
#else
            y = config->gamma[i] * mrac_g_gamma[axis_id][mrac_feature_desc[i].group] * (grad[i]
                - sigma_lf_active * (state->Theta[i] - state->Whatf[i]) / denom
                - sigma_eff * mrac_g_sigma[axis_id][mrac_feature_desc[i].group] * state->Theta[i] / denom
                );
#endif
#if MRAC_ENABLE_SATAWARE == 1
            // V2 saturation-aware leakage (sim ctrl_mrac_b.py:120), inside the gamma bracket like sigma:
            // pulls Theta toward 0 while the mixer clips, so u_ad cannot wind up against a dead actuator.
            if (config->mu_sat > 0.0f) {
                y -= config->gamma[i] * mrac_g_gamma[axis_id][mrac_feature_desc[i].group]
                     * config->mu_sat * fabsf(state->u_def) * state->Theta[i];
            }
#endif

            state->Theta[i] += MRAC_DT * y * theta_scale;

#ifdef MRAC_ENABLE_SIGMA_PRIOR
            /* Discrete-time safeguard: the band-scaling projection cannot
             * bound a single large sigma_prior-driven step (the gradient
             * magnitude can overshoot the band in one tick), so hard-bounds
             * Theta to the projection set after the update (prior-D-fix).
             * No-op when sigma_prior=0, keeping the baseline update exact. */
            if (sigma_prior != 0.0f) {
                if (state->Theta[i] > config->What_limit[i]) {
                    state->Theta[i] = config->What_limit[i];
                } else if (state->Theta[i] < config->What_lower_limit[i]) {
                    state->Theta[i] = config->What_lower_limit[i];
                }
            }
#endif

            // L1-style low-frequency leakage: pull fast weights toward filtered copy.
            // PR (kappa_pr > 0) needs the filtered copy too, without the sigma_lf pull.
#if MRAC_ENABLE_PERF_RECOVERY == 1
            if (mrac_flags.l1_filtering_on || config->kappa_pr > 0.0f) {
#else
            if (mrac_flags.l1_filtering_on) {
#endif
                state->Whatf[i] += MRAC_DT * config->gam_f * (state->Theta[i] - state->Whatf[i]);
            }
        }
    }

    // 6. Compute adaptive control component (u_ad = Theta^T * Phi)
    raw_u_ad = 0.0f;
    for (i = 0; i < n; i++) {
        raw_u_ad += state->Theta[i] * (state->Phi[i] * mrac_g_phi[axis_id][mrac_feature_desc[i].group]);
    }
#if MRAC_ENABLE_PERF_RECOVERY == 1
    // PR, Yucelen-Calise form in Theta = -W sign (roadmap Q2): u_ad += kappa_pr*(Theta - Whatf)'Phi,
    // the high-frequency part of the weights. Bounded by the u_max clamp below like the rest of u_ad.
    if (config->kappa_pr > 0.0f) {
        float u_pr = 0.0f;
        for (i = 0; i < n; i++) {
            u_pr += (state->Theta[i] - state->Whatf[i]) * (state->Phi[i] * mrac_g_phi[axis_id][mrac_feature_desc[i].group]);
        }
        raw_u_ad += config->kappa_pr * u_pr;
    }
#endif
    // Variant guard: with any variant on, a non-finite drive or output drops u_ad and the weights of
    // this axis back to 0 instead of reaching the mixer. Inert when vid == 0 (today's law).
    if (vid != 0U && (!(raw_u_ad - raw_u_ad == 0.0f) || !(state->u_ad - state->u_ad == 0.0f))) {
        for (i = 0; i < MRAC_N_FEATURES; i++) {
            state->Theta[i] = 0.0f;
            state->Whatf[i] = 0.0f;
        }
        state->e_int = 0.0f;
        raw_u_ad = 0.0f;
        state->u_ad = 0.0f;
    }

#if ENABLE_PERFORMANCE_RECOVERY == 1
    // Apply L1-style low-pass filter to the adaptive signal
    // u_ad_dot = omega_u * (raw_u_ad - u_ad)
    state->u_ad += MRAC_DT * config->omega_u * (raw_u_ad - state->u_ad);
#else
    state->u_ad = raw_u_ad;
#endif

    if (config->u_max > 0.0f) {
        if (state->u_ad > config->u_max) {
            state->u_ad = config->u_max;
        } else if (state->u_ad < -config->u_max) {
            state->u_ad = -config->u_max;
        }
    }
    MRAC_CycEnd(axis_id, t0, t1, t2, t3);
}

void MRAC_SimplexStep(void)
{
    static uint8_t prev_trigger = 0;
    const MRAC_AxisConfig_t *configs[4];
    MRAC_AxisState_t *axes[4];
    float   sum_w2;
    float   target;
    uint8_t trigger;
    uint8_t r;
    uint8_t i;
    uint8_t a;

    configs[0] = &mrac_config_pitch; axes[0] = &mrac_state.pitch;
    configs[1] = &mrac_config_roll;  axes[1] = &mrac_state.roll;
    configs[2] = &mrac_config_yaw;   axes[2] = &mrac_state.yaw;
    configs[3] = &mrac_config_z;     axes[3] = &mrac_state.z_rate;

    r = 0;
    for (a = 0; a < 4; a++) {
        if (fabsf(axes[a]->u_ad) >= configs[a]->u_max * 0.999f) {
            if (mrac_simplex.sat_ticks[a] < 0xFFFFU) mrac_simplex.sat_ticks[a]++;
        } else {
            mrac_simplex.sat_ticks[a] = 0;
        }
        if (r == 0 && mrac_simplex.sat_ticks[a] > mrac_simplex.sat_ticks_max) r = 4;
        sum_w2 = 0.0f;
        for (i = 0; i < MRAC_N_FEATURES; i++) sum_w2 += axes[a]->Theta[i] * axes[a]->Theta[i];
        if (sqrtf(sum_w2) > mrac_simplex.w_norm_max) r = 3;
    }
    if (fabsf(imu_data.pit) > mrac_simplex.pitch_max) r = 2;
    if (fabsf(imu_data.rol) > mrac_simplex.roll_max)   r = 1;
    trigger = (uint8_t)(r != 0);

    if (mrac_simplex.mode == 0) {
        mrac_simplex.tripped     = 0;
        mrac_simplex.clear_ticks = 0;
        prev_trigger = 0;
    } else if (trigger) {
        mrac_simplex.clear_ticks = 0;
        if (mrac_simplex.mode == 1 && mrac_simplex.tripped == 0) {
            mrac_simplex.tripped = 1;
            mrac_simplex.trip_count++;
            mrac_simplex.reason = r;
        } else if (mrac_simplex.mode == 2) {
            if (prev_trigger == 0) mrac_simplex.would_trip_count++;
            mrac_simplex.reason = r;
        }
        prev_trigger = 1;
    } else {
        if (mrac_simplex.tripped) {
            mrac_simplex.clear_ticks++;
            if (mrac_simplex.clear_ticks >= mrac_simplex.hold_ticks) mrac_simplex.tripped = 0;
        }
        prev_trigger = 0;
    }

    /* Fade u_ad over ~100 ms toward 0 (tripped or PID-only) or back to 1. */
    target = (mrac_simplex.tripped || mrac_simplex.variant == 1) ? 0.0f : 1.0f;
    if (mrac_simplex.fade > target) {
        mrac_simplex.fade -= MRAC_DT / 0.1f;
        if (mrac_simplex.fade < target) mrac_simplex.fade = target;
    } else if (mrac_simplex.fade < target) {
        mrac_simplex.fade += MRAC_DT / 0.1f;
        if (mrac_simplex.fade > target) mrac_simplex.fade = target;
    }
}

// ------------------------------------------------------------------------------
// Public API Operations
// ------------------------------------------------------------------------------

#define MRAC_SET(f, p, r, y, z) \
    mrac_config_pitch.f = (p); \
    mrac_config_roll.f = (r); \
    mrac_config_yaw.f = (y); \
    mrac_config_z.f = (z)

#define MRAC_BASIS(ax, i, g, lim, t, low) \
    mrac_config_##ax.gamma[i] = (g); \
    mrac_config_##ax.What_limit[i] = (lim); \
    mrac_config_##ax.What_tol[i] = (t); \
    mrac_config_##ax.What_lower_limit[i] = (low)

void MRAC_Init(void)
{
    int i, j;

    /* Per-axis scalars: one row per float field of MRAC_AxisConfig_t, in struct order.
     * Zeros are listed on purpose: these fields were never set before the tables existed. */
    /*       field            pitch                      roll                       yaw                        z */
    MRAC_SET(sigma_lf,        0.8f,                      0.8f,                      1.0f,                      0.0f);
    MRAC_SET(sigma,           0.01f,                     0.01f,                     0.01f,                     0.01f);
    MRAC_SET(gam_f,           16.0f,                     16.0f,                     16.0f,                     0.0f);
    MRAC_SET(omega_u,         4.0f,                      5.0f,                      4.0f,                      20.0f);
    MRAC_SET(lambda_perf,     0.0f,                      0.0f,                      0.0f,                      0.0f);
    MRAC_SET(tau_v,           0.0f,                      0.0f,                      0.0f,                      0.0f);
    MRAC_SET(u_max,           6.73863f,                  6.73863f,                  2.027f,                    13.47726f);
    MRAC_SET(mrac_to_mixer,   DEFAULT_MRAC_TO_MIXER_PR,  DEFAULT_MRAC_TO_MIXER_PR,  DEFAULT_MRAC_TO_MIXER_YAW, DEFAULT_MRAC_TO_MIXER_Z);
    MRAC_SET(J,               0.0023f,                   0.0023f,                   0.0015f,                   1.5f);
    MRAC_SET(e_deadzone,      0.05f,                     0.05f,                     0.05f,                     0.05f);
    MRAC_SET(e_freeze,        1.2f,                      1.2f,                      1.0f,                      1.2f);
    MRAC_SET(e_sat,           0.5f,                      0.5f,                      0.7f,                      0.4f);
    MRAC_SET(k_e,             0.05f,                     0.05f,                     0.05f,                     0.0f);
    MRAC_SET(ref_model_bw,    44.0f,                     44.0f,                     30.0f,                     20.0f);
    MRAC_SET(ref_model_zeta,  0.8f,                      0.8f,                      0.8f,                      0.8f);
    MRAC_SET(P_lyap,          0.0f,                      0.0f,                      0.0f,                      0.0f);
    MRAC_SET(ref_Q1,          1.0f,                      1.0f,                      1.0f,                      1.0f);
    MRAC_SET(ref_Q2,          1.0f,                      1.0f,                      1.0f,                      1.0f);
    MRAC_SET(wc_edot,         30.0f,                     30.0f,                     30.0f,                     30.0f);
    /* WP-27 variant rows: every value here is OFF (law == the rows above). Flight values: CMD 0x1D. */
    MRAC_SET(ref_type,        -1.0f,                     -1.0f,                     -1.0f,                     -1.0f);
    MRAC_SET(ref_delay_s,     0.0f,                      0.0f,                      0.0f,                      0.0f);
    MRAC_SET(drive_norm,      0.0f,                      0.0f,                      0.0f,                      0.0f);
    MRAC_SET(lam_edot,        0.0f,                      0.0f,                      0.0f,                      0.0f);
    MRAC_SET(kappa_pr,        0.0f,                      0.0f,                      0.0f,                      0.0f);
    MRAC_SET(crm_ell,         0.0f,                      0.0f,                      0.0f,                      0.0f);
    MRAC_SET(mu_sat,          0.0f,                      0.0f,                      0.0f,                      0.0f);
    MRAC_SET(lam_ang,         0.0f,                      0.0f,                      0.0f,                      0.0f);
    MRAC_SET(rbf_on,          0.0f,                      0.0f,                      0.0f,                      0.0f);
    MRAC_SET(rbf_rate_scale,  3.0f,                      3.0f,                      3.0f,                      3.0f);
    MRAC_SET(rbf_ang_scale,   0.26f,                     0.26f,                     0.26f,                     0.26f);

    /* Basis weights: gamma = learning rate, limit/lower = weight bounds (projection),
     * tol = projection boundary layer. Yaw limit/tol = pitch/roll value * 0.6f. */
    /*         axis   i  gamma  limit        tol          lower           feature */
    MRAC_BASIS(pitch, 0, 1.50f, 0.15f,       0.03f,       -0.15f);     /* bias */
    MRAC_BASIS(pitch, 1, 0.20f, 0.05f,       0.01f,       0.0f);       /* rate */
    MRAC_BASIS(pitch, 2, 0.05f, 0.02f,       0.005f,      0.0f);       /* rate_tanh */
    MRAC_BASIS(pitch, 3, 0.05f, 0.05f,       0.01f,       0.0f);       /* cross */
    MRAC_BASIS(pitch, 4, 0.10f, 0.20f,       0.04f,       0.0f);       /* u_nom */
    MRAC_BASIS(pitch, 5, 0.10f, 0.15f,       0.03f,       0.0f);       /* xm */
    MRAC_BASIS(roll,  0, 1.50f, 0.15f,       0.03f,       -0.15f);     /* bias */
    MRAC_BASIS(roll,  1, 0.20f, 0.05f,       0.01f,       0.0f);       /* rate */
    MRAC_BASIS(roll,  2, 0.05f, 0.02f,       0.005f,      0.0f);       /* rate_tanh */
    MRAC_BASIS(roll,  3, 0.05f, 0.05f,       0.01f,       0.0f);       /* cross */
    MRAC_BASIS(roll,  4, 0.10f, 0.20f,       0.04f,       0.0f);       /* u_nom */
    MRAC_BASIS(roll,  5, 0.10f, 0.15f,       0.03f,       0.0f);       /* xm */
    MRAC_BASIS(yaw,   0, 1.00f, 0.15f*0.6f,  0.03f*0.6f,  -0.15f*0.6f); /* bias */
    MRAC_BASIS(yaw,   1, 0.10f, 0.05f*0.6f,  0.01f*0.6f,  0.0f);       /* rate */
    MRAC_BASIS(yaw,   2, 0.05f, 0.02f*0.6f,  0.005f*0.6f, 0.0f);       /* rate_tanh */
    MRAC_BASIS(yaw,   3, 0.05f, 0.05f*0.6f,  0.01f*0.6f,  0.0f);       /* cross */
    MRAC_BASIS(yaw,   4, 0.10f, 0.20f*0.6f,  0.04f*0.6f,  0.0f);       /* u_nom */
    MRAC_BASIS(yaw,   5, 0.10f, 0.15f*0.6f,  0.03f*0.6f,  0.0f);       /* xm */
    MRAC_BASIS(z,     0, 2.00f, 1.00f,       0.20f,       0.0f);       /* bias */
    MRAC_BASIS(z,     1, 0.50f, 0.10f,       0.02f,       0.0f);       /* rate */
    MRAC_BASIS(z,     2, 0.10f, 0.05f,       0.01f,       0.0f);       /* rate_tanh */
    MRAC_BASIS(z,     3, 0.10f, 0.05f,       0.01f,       0.0f);       /* cross */
    MRAC_BASIS(z,     4, 0.20f, 0.20f,       0.04f,       0.0f);       /* u_nom */
    MRAC_BASIS(z,     5, 0.20f, 0.20f,       0.04f,       0.0f);       /* xm */
#if MRAC_VARIANT == MRAC_VARIANT_STRUCT6_RBF12
    /* V3 RBF grid, pitch/roll only (MRAC_GenRBF gives phi = 0 on yaw/z). Symmetric bounds: the sign of
     * a payload torque is unknown. PROPOSED: gamma = the rate row's 0.20 split over 12 features. */
    MRAC_BASIS(pitch, 6,  0.10f, 0.05f,      0.01f,       -0.05f);     /* rbf_r0_a0 */
    MRAC_BASIS(pitch, 7,  0.10f, 0.05f,      0.01f,       -0.05f);     /* rbf_r0_a1 */
    MRAC_BASIS(pitch, 8,  0.10f, 0.05f,      0.01f,       -0.05f);     /* rbf_r0_a2 */
    MRAC_BASIS(pitch, 9,  0.10f, 0.05f,      0.01f,       -0.05f);     /* rbf_r1_a0 */
    MRAC_BASIS(pitch, 10, 0.10f, 0.05f,      0.01f,       -0.05f);     /* rbf_r1_a1 */
    MRAC_BASIS(pitch, 11, 0.10f, 0.05f,      0.01f,       -0.05f);     /* rbf_r1_a2 */
    MRAC_BASIS(pitch, 12, 0.10f, 0.05f,      0.01f,       -0.05f);     /* rbf_r2_a0 */
    MRAC_BASIS(pitch, 13, 0.10f, 0.05f,      0.01f,       -0.05f);     /* rbf_r2_a1 */
    MRAC_BASIS(pitch, 14, 0.10f, 0.05f,      0.01f,       -0.05f);     /* rbf_r2_a2 */
    MRAC_BASIS(pitch, 15, 0.10f, 0.05f,      0.01f,       -0.05f);     /* rbf_r3_a0 */
    MRAC_BASIS(pitch, 16, 0.10f, 0.05f,      0.01f,       -0.05f);     /* rbf_r3_a1 */
    MRAC_BASIS(pitch, 17, 0.10f, 0.05f,      0.01f,       -0.05f);     /* rbf_r3_a2 */
    MRAC_BASIS(roll,  6,  0.10f, 0.05f,      0.01f,       -0.05f);     /* rbf_r0_a0 */
    MRAC_BASIS(roll,  7,  0.10f, 0.05f,      0.01f,       -0.05f);     /* rbf_r0_a1 */
    MRAC_BASIS(roll,  8,  0.10f, 0.05f,      0.01f,       -0.05f);     /* rbf_r0_a2 */
    MRAC_BASIS(roll,  9,  0.10f, 0.05f,      0.01f,       -0.05f);     /* rbf_r1_a0 */
    MRAC_BASIS(roll,  10, 0.10f, 0.05f,      0.01f,       -0.05f);     /* rbf_r1_a1 */
    MRAC_BASIS(roll,  11, 0.10f, 0.05f,      0.01f,       -0.05f);     /* rbf_r1_a2 */
    MRAC_BASIS(roll,  12, 0.10f, 0.05f,      0.01f,       -0.05f);     /* rbf_r2_a0 */
    MRAC_BASIS(roll,  13, 0.10f, 0.05f,      0.01f,       -0.05f);     /* rbf_r2_a1 */
    MRAC_BASIS(roll,  14, 0.10f, 0.05f,      0.01f,       -0.05f);     /* rbf_r2_a2 */
    MRAC_BASIS(roll,  15, 0.10f, 0.05f,      0.01f,       -0.05f);     /* rbf_r3_a0 */
    MRAC_BASIS(roll,  16, 0.10f, 0.05f,      0.01f,       -0.05f);     /* rbf_r3_a1 */
    MRAC_BASIS(roll,  17, 0.10f, 0.05f,      0.01f,       -0.05f);     /* rbf_r3_a2 */
    for (i = MRAC_N_STRUCT; i < MRAC_N_FEATURES; i++) {   /* yaw / z: no RBF learning */
        MRAC_BASIS(yaw, i, 0.0f, 0.0f, 0.0f, 0.0f);
        MRAC_BASIS(z,   i, 0.0f, 0.0f, 0.0f, 0.0f);
    }
#endif

    /* History and provenance (newest first)
     * 2026-10-04 WP-27  Variant rows (ref_type .. rbf_ang_scale), all OFF; ref_type -1 = follow the global
     *                   CMD 0x13 type. Flight values are PROPOSED in docs/workflow-b/mrac-variants.md and
     *                   are written by CMD 0x1D, not here. rbf scales 3.0 rad/s and 0.26 rad (15 deg tilt
     *                   limit) from the roadmap V3; they matter only when rbf_on = 1.
     * 2026-09-29 S1b    Local arrays and per-axis assignments -> MRAC_SET / MRAC_BASIS tables, bit-exact
     *                   (API/tests/run_mrac_equiv.py EQUIV OK). Yaw cells stay the products x*0.6f so they
     *                   round exactly as the old PR_Wlim[i]*0.6f did.
     * lower             Bias weight (i=0) unlocked below zero on p/r/y, symmetric with its limit, so the law
     *                   can cancel a standing torque bias (e.g. yaw reactive-torque imbalance). Every other
     *                   weight, and the z bias, keeps lower bound 0 (floored).
     * ref_Q1/Q2 wc_edot 2nd-order matrix-P law (ADR-0007), used only when ref_model_type==2 (p/r), harmless
     *                   on yaw/z. Q = I; Q1=wn would recover the old scalar gain 1/(2*wn). wc_edot = LPF
     *                   cutoff of the finite-difference rate derivative.
     * ref_model_bw      SysID 2026-06-18. Pitch: closed-loop -3dB ~44 rad/s (K~185, lag pole ~2.6Hz, delay
     *                   ~12ms, rel-degree 2), 2nd-order ref. Roll: ~44.1 rad/s, repeatable <0.3% (multisine
     *                   x7 + chirp; K~165, pole ~3.2Hz, delay ~15ms). Yaw 30: PROVISIONAL, yaw is a pure
     *                   integrator G~37/s (rel-degree 1) -> should get a first-order ref (ADR-0005); 30 is
     *                   unvalidated, needs a yaw closed-loop BW measurement before injection. Z 20: slower
     *                   translation bandwidth.
     * omega_u           Perf-recovery LPF on u_ad = p/4 of the identified lag pole (docs/sysid_results.md).
     *                   Pitch p = 16-18 rad/s -> 4.0. Roll p = 19.8 rad/s (7 runs, <0.3% spread) -> 5.0,
     *                   phase lag -18.5 deg (-82 deg at the old 30). The old 30 sat above the plant corner,
     *                   so it was inert. Yaw 4.0: no pole below the 2.2 Hz coherent band, but the same
     *                   motors/ESCs/gyro filter as roll and K~37 is ~5x weaker, so no higher than roll
     *                   (was 20, copied from roll's old number).
     * e_freeze          2026-06-16 data-grounded, passthrough max|e|: pitch 0.77, roll 0.82 -> 1.2 (was 5.0,
     *                   never fired). Yaw 1.0 kept (max|e|=1.28, still catches true outliers). Z 1.2
     *                   (max|e|=1.0; was 0 = disabled, closes the spike-protection gap).
     * e_sat             2026-06-16 ~p99 of |e|: p/r 0.5 (was 3.5, tanh stayed linear), yaw 0.7 (was 2.0),
     *                   z 0.4 ~p95-p99 (was 0 = tanh disabled on the most over-driven axis).
     * J                 kg*m^2 on p/r/y; z holds the mass in kg.
     * u_max             U_MAX_PITCH / U_MAX_ROLL / U_MAX_YAW / U_MAX_Z.
     */

    for (i = 0; i < (int)AXES; i++) {
        mrac_u_ff[i] = 0.0f;
        for (j = 0; j < MRAC_N_GROUPS; j++) {
            mrac_g_gamma[i][j] = 1.0f;
            mrac_g_sigma[i][j] = 1.0f;
            mrac_g_phi[i][j] = 1.0f;
        }
    }

    mrac_flags.adaptation_on      = ENABLE_MRAC_COMPUTATION;
    mrac_flags.projection_on      = ENABLE_PROJECTION_OPERATOR;
    mrac_flags.deadzone_on        = ENABLE_DEADZONE;
    mrac_flags.hard_freeze_on     = 1;
    mrac_flags.tanh_saturation_on = 1;
    mrac_flags.e_modification_on  = 1;
    mrac_flags.l1_filtering_on    = 0;
    mrac_flags.axis_enable_pitch  = 1;
    mrac_flags.axis_enable_roll   = 1;
    mrac_flags.axis_enable_yaw    = 1;
    // Shadow mode by default: MRAC learns and computes u_ad but motors see pure PID
    // until the operator explicitly enables injection from the dashboard (CMD 0x0F idx 10).
    mrac_flags.output_injection_on = 0;
    mrac_flags.id_frame_on        = 0;                       // high-rate system-ID frame off by default (CMD 0x0F idx 11)
    mrac_flags.of_frame_on        = 0;                       // OF calibration frame off by default (CMD 0x0F idx 12)
    mrac_flags.ref_model_type     = DEFAULT_REF_MODEL_TYPE;  // reference model type (CMD 0x13); 0 = passthrough

    MRAC_Reset();
}

void MRAC_ResetWeights(void)
{
    int i;
    // Force reference models to snap to current plant states, reset weights.
    // Useful during mid-flight mode switches or disarms safely.
    for (i = 0; i < MAX_NUM_BASIS; i++) {
        mrac_state.pitch.Theta[i] = 0.0f;
        mrac_state.roll.Theta[i]  = 0.0f;
        mrac_state.yaw.Theta[i]   = 0.0f;
        mrac_state.z_rate.Theta[i]= 0.0f;
        mrac_state.pitch.Whatf[i] = 0.0f;
        mrac_state.roll.Whatf[i]  = 0.0f;
        mrac_state.yaw.Whatf[i]   = 0.0f;
        mrac_state.z_rate.Whatf[i]= 0.0f;
    }
    
    // Snap references to plant state (bumpless) and zero 2nd-order velocity states
    mrac_state.pitch.xm = mrac_state.pitch.x;   mrac_state.pitch.xm_dot  = 0.0f;
    mrac_state.roll.xm  = mrac_state.roll.x;    mrac_state.roll.xm_dot   = 0.0f;
    mrac_state.yaw.xm   = mrac_state.yaw.x;     mrac_state.yaw.xm_dot    = 0.0f;
    mrac_state.z_rate.xm= mrac_state.z_rate.x;  mrac_state.z_rate.xm_dot = 0.0f;

    // Zero the rate-derivative estimator (ADR-0007) so e_dot starts clean post-reset.
    mrac_state.pitch.x_prev = mrac_state.pitch.x;   mrac_state.pitch.xdot_f  = 0.0f; mrac_state.pitch.e_dot  = 0.0f;
    mrac_state.roll.x_prev  = mrac_state.roll.x;    mrac_state.roll.xdot_f   = 0.0f; mrac_state.roll.e_dot   = 0.0f;
    mrac_state.yaw.x_prev   = mrac_state.yaw.x;     mrac_state.yaw.xdot_f    = 0.0f; mrac_state.yaw.e_dot    = 0.0f;
    mrac_state.z_rate.x_prev= mrac_state.z_rate.x;  mrac_state.z_rate.xdot_f = 0.0f; mrac_state.z_rate.e_dot = 0.0f;

    MRAC_VariantSnap(&mrac_state.pitch);
    MRAC_VariantSnap(&mrac_state.roll);
    MRAC_VariantSnap(&mrac_state.yaw);
    MRAC_VariantSnap(&mrac_state.z_rate);
}

// V1 delay ring holds the plant state (bumpless) and the 3L integral restarts at 0.
static void MRAC_VariantSnap(MRAC_AxisState_t *st)
{
    int k;
    for (k = 0; k < MRAC_REF_BUF; k++) st->r_buf[k] = st->x;
    st->e_int = 0.0f;
}

/* CMD 0x1D field table: one row per MRAC_VariantField_e, in enum order. Writes outside [lo, hi] or
 * non-finite are refused. snap = 1: the write changes the reference model, so xm snaps to the plant.
 * Bounds keep an enabled variant inside the existing u_max clamp and numerically stable:
 *   ref_delay_s  <= (MRAC_REF_BUF-1)*DT;  crm_ell*DT <= 0.25;  rbf scales > 0 (they divide). */
#define MRAC_VAR_FIELD(lo, hi, snap) { lo, hi, snap }
static const struct { float lo; float hi; uint8_t snap; } mrac_var_field[MRAC_VF_COUNT] = {
/*                  lo      hi      snap     field */
    MRAC_VAR_FIELD(-1.0f,  2.0f,   1),   /* ref_type        -1 global, 0 pass, 1 first, 2 second */
    MRAC_VAR_FIELD( 0.0f,  0.035f, 1),   /* ref_delay_s     s */
    MRAC_VAR_FIELD( 0.0f,  1.0f,   0),   /* drive_norm      0/1 */
    MRAC_VAR_FIELD( 0.0f,  0.1f,   0),   /* lam_edot        s */
    MRAC_VAR_FIELD( 0.0f,  2.0f,   0),   /* kappa_pr        - */
    MRAC_VAR_FIELD( 0.0f,  50.0f,  1),   /* crm_ell         1/s */
    MRAC_VAR_FIELD( 0.0f,  10.0f,  0),   /* mu_sat          1/(N m s) */
    MRAC_VAR_FIELD( 0.0f,  20.0f,  0),   /* lam_ang         1/s */
    MRAC_VAR_FIELD( 0.0f,  1.0f,   0),   /* rbf_on          0/1 */
    MRAC_VAR_FIELD( 0.1f,  20.0f,  0),   /* rbf_rate_scale  rad/s */
    MRAC_VAR_FIELD( 0.05f, 1.0f,   0),   /* rbf_ang_scale   rad */
    MRAC_VAR_FIELD( 0.0f,  2.0f,   0),   /* gamma_scale     mrac_g_gamma[axis][*] */
    MRAC_VAR_FIELD( 0.5f,  100.0f, 1)    /* ref_model_bw    rad/s, wn on type 2 (DT*wn < 2) */
};

uint8_t MRAC_VariantParamSet(uint8_t axis, uint8_t field, float val)
{
    MRAC_AxisConfig_t *cfg[AXES];
    MRAC_AxisState_t *st[AXES];
    MRAC_AxisConfig_t *c;
    int k;

    cfg[0] = &mrac_config_pitch; st[0] = &mrac_state.pitch;
    cfg[1] = &mrac_config_roll;  st[1] = &mrac_state.roll;
    cfg[2] = &mrac_config_yaw;   st[2] = &mrac_state.yaw;
    cfg[3] = &mrac_config_z;     st[3] = &mrac_state.z_rate;

    if (axis >= (uint8_t)AXES || field >= (uint8_t)MRAC_VF_COUNT) return 0U;
    if (!(val - val == 0.0f) || val < mrac_var_field[field].lo || val > mrac_var_field[field].hi) return 0U;
    c = cfg[axis];
    switch (field) {
        case MRAC_VF_REF_TYPE:       c->ref_type = (float)(int)(val + ((val < 0.0f) ? -0.5f : 0.5f)); break;
        case MRAC_VF_REF_DELAY_S:    c->ref_delay_s = val;    break;
        case MRAC_VF_DRIVE_NORM:     c->drive_norm = (val >= 0.5f) ? 1.0f : 0.0f; break;
        case MRAC_VF_LAM_EDOT:       c->lam_edot = val;       break;
        case MRAC_VF_KAPPA_PR:       c->kappa_pr = val;       break;
        case MRAC_VF_CRM_ELL:        c->crm_ell = val;        break;
        case MRAC_VF_MU_SAT:         c->mu_sat = val;         break;
        case MRAC_VF_LAM_ANG:        c->lam_ang = val;        break;
        case MRAC_VF_RBF_ON:
            c->rbf_on = (val >= 0.5f) ? 1.0f : 0.0f;
            // fresh RBF weights on every switch, so stale ones never come back
            for (k = MRAC_N_STRUCT; k < MRAC_N_FEATURES; k++) {
                st[axis]->Theta[k] = 0.0f;
                st[axis]->Whatf[k] = 0.0f;
            }
            break;
        case MRAC_VF_RBF_RATE_SCALE: c->rbf_rate_scale = val; break;
        case MRAC_VF_RBF_ANG_SCALE:  c->rbf_ang_scale = val;  break;
        case MRAC_VF_GAMMA_SCALE:
            for (k = 0; k < MRAC_N_GROUPS; k++) mrac_g_gamma[axis][k] = val;
            break;
        case MRAC_VF_REF_MODEL_BW:   c->ref_model_bw = val;   break;
        default: return 0U;
    }
    if (mrac_var_field[field].snap) {
        st[axis]->xm = st[axis]->x;
        st[axis]->xm_dot = 0.0f;
        MRAC_VariantSnap(st[axis]);
    }
    return 1U;
}

void MRAC_Reset(void)
{
    MRAC_ResetWeights();
}

void MRAC_Control(const CtrlerTypeDef* current_state)
{
    float p_rate, q_rate, r_rate;
    float cross_pitch, cross_roll;
    float r_pitch, r_roll, r_yaw, r_z;
    uint32_t t_l2;
    

    MRAC_GateStep();
    /* Simplex fallback step — evaluate triggers and manage fade. */
    MRAC_SimplexStep();
    t_l2 = MRAC_CYC_NOW();
    MRAC_L2_Update();
    t_l2 = MRAC_CYC_NOW() - t_l2;
    mrac_cyc.l2_last = t_l2;
    if (t_l2 > mrac_cyc.l2_max) mrac_cyc.l2_max = t_l2;
    // Main execution cycle (Hooked into FreeRTOS / Tasks)
    
    // 1. Acquire current Gyro Rates (p, q, r) and Z-velocity from inner-loop cascade
    // Convert PID FB (degrees/s) to rad/s for regressor bounds alignment
    p_rate = current_state->gyroyPID.FB * 0.0174533f; // Y-axis gyro represents Pitch
    q_rate = current_state->gyroxPID.FB * 0.0174533f; // X-axis gyro represents Roll
    r_rate = current_state->gyrozPID.FB * 0.0174533f; // Z-axis gyro represents Yaw
    
    // Assign measured states
    mrac_state.pitch.x = p_rate;
    mrac_state.roll.x  = q_rate;
    mrac_state.yaw.x   = r_rate;
    mrac_state.z_rate.x = current_state->Z_ratePID.FB; // Z-axis velocity measurement from cascade
    
    // Compute cross-coupling terms
    cross_pitch = q_rate * r_rate;
    cross_roll  = p_rate * r_rate;
    
    // 2. Read current commanded targets streaming from outer loops
    // Degrees/s to Radians/s (for gyro references)
    r_pitch = current_state->gyroyPID.Des * 0.0174533f;
    r_roll  = current_state->gyroxPID.Des * 0.0174533f;
    r_yaw   = current_state->gyrozPID.Des * 0.0174533f;
    r_z     = current_state->Z_ratePID.Des; // Pull desired vertical rate directly
    
    // 3. Assign Nominal Control (U) computed by PID controllers
    // Convert from PWM/Mixer units down to physical torque/thrust SI Units 
    // to prevent regressor norm scaling explosion (1 + ||Phi||^2)
    mrac_state.pitch.u_nom  = current_state->gyroyPID.U / mrac_config_pitch.mrac_to_mixer;
    mrac_state.roll.u_nom   = current_state->gyroxPID.U / mrac_config_roll.mrac_to_mixer;
    mrac_state.yaw.u_nom    = current_state->gyrozPID.U / mrac_config_yaw.mrac_to_mixer;
    mrac_state.z_rate.u_nom = current_state->Z_ratePID.U / mrac_config_z.mrac_to_mixer;
    
    // 4. Perform inverse mixing to measure actual actuation capabilities
    // (Omitted in early dev, rely on basic limits)
    
    // 5-8. Update core MRAC algorithms for all axes
    if (mrac_flags.axis_enable_pitch) {
        MRAC_UpdateAxis(MRAC_AXIS_PITCH, &mrac_state.pitch, &mrac_config_pitch, cross_pitch, r_pitch);
    } else {
        mrac_state.pitch.u_ad = 0.0f;
    }
    if (mrac_flags.axis_enable_roll) {
        MRAC_UpdateAxis(MRAC_AXIS_ROLL,  &mrac_state.roll,  &mrac_config_roll,  cross_roll,  r_roll);
    } else {
        mrac_state.roll.u_ad = 0.0f;
    }
    if (mrac_flags.axis_enable_yaw) {
        MRAC_UpdateAxis(MRAC_AXIS_YAW,   &mrac_state.yaw,   &mrac_config_yaw,   0.0f,        r_yaw);
    } else {
        mrac_state.yaw.u_ad = 0.0f;
    }
    MRAC_UpdateAxis(MRAC_AXIS_Z,     &mrac_state.z_rate,&mrac_config_z,     0.0f,        r_z);


}

// ------------------------------------------------------------------------------
// Opt-in sigma-prior accessors (prior-D / ADR-0013 D5, D10)
// ------------------------------------------------------------------------------
// Only emitted when MRAC_ENABLE_SIGMA_PRIOR is defined. Critical-section
// protected against the 200 Hz task-context MRAC_UpdateAxis read of
// Theta_prior[axis_id][:]. The ground-station command dispatch is the
// intended writer; MRAC_Control/UpdateAxis is the reader. A single-writer /
// single-reader pair is race-free under a PRIMASK critical section.
// ---------------------------------------------------------------------------
#ifdef MRAC_ENABLE_SIGMA_PRIOR

void MRAC_SetPrior(uint8_t axis, const float *arr)
{
    uint32_t pri;
    int i;

    if (axis >= (uint8_t)AXES) {
        return;
    }
    if (arr == (const float *)0) {
        return;
    }
    pri = __get_PRIMASK();
    __disable_irq();
    for (i = 0; i < MAX_NUM_BASIS; i++) {
        Theta_prior[axis][i] = arr[i];
    }
    __set_PRIMASK(pri);
}

void MRAC_GetPrior(uint8_t axis, float *out_arr)
{
    uint32_t pri;
    int i;

    if (axis >= (uint8_t)AXES) {
        return;
    }
    if (out_arr == (float *)0) {
        return;
    }
    pri = __get_PRIMASK();
    __disable_irq();
    for (i = 0; i < MAX_NUM_BASIS; i++) {
        out_arr[i] = Theta_prior[axis][i];
    }
    __set_PRIMASK(pri);
}

#endif /* MRAC_ENABLE_SIGMA_PRIOR */
